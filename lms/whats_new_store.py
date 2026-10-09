"""Durable What's new history (MCK-182 slice 2).

Slice 1 writes ``static/whats-new/releases.json`` at deploy time from the
committed file plus the last few Deploy runs. Entries that were only ever
generated (never committed) fall out of that window after 30 runs or 100
PRs. This module keeps every entry once it has shipped: each worker's boot
copies the shipped file into the ``whats_new_releases`` table of the school
database (``/data/lloves.sqlite`` on the Fly volume in production, a local
SQLite file in dev), and the staff Dashboard reads the table.

Rules for a release in the shipped file:

* ``"source": "deploy"`` (written by ``.github/scripts/whats_new.py``):
  insert once, never overwrite. A later edit to a merged PR's body can't
  rewrite history that teachers already saw.
* Anything else (hand-written and committed in git): git is the source of
  truth, so the row is inserted or replaced. Committing an entry with the
  same id fixes a generated line. Committing it with ``"items": []`` takes
  it down.
* A release with no usable items is housekeeping and is never stored.

The teacher payload (:func:`teacher_payload`) never carries ``refs``,
``sha`` or any ticket id, PR number or SHA in the text.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RELEASES_FILE = Path(__file__).resolve().parent / "static" / "whats-new" / "releases.json"
# Newest releases sent to the Dashboard. The UI shows 3 day groups plus 30
# days of "Earlier"; this only bounds the payload.
PAYLOAD_LIMIT = 120
DEPLOY_SOURCE = "deploy"
ITEM_TEXT_KEYS = ("text", "title", "line")

SCHEMA = """
CREATE TABLE IF NOT EXISTS whats_new_releases (
    id TEXT PRIMARY KEY,
    sha TEXT NOT NULL DEFAULT '',
    deployed_at TEXT NOT NULL DEFAULT '',
    day TEXT NOT NULL,
    sort_at TEXT NOT NULL,
    source TEXT NOT NULL,
    release_json TEXT NOT NULL,
    stored_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_whats_new_releases_sort ON whats_new_releases(sort_at DESC);
"""

#: A ticket id: MCK-12, LMS-4, or any multi-part key like LLOVES-LMS-9.
#: Single-word caps ("COVID-19") stay: they read as plain words.
_TICKET = r"\b(?:(?i:mck)|LMS|(?:[A-Z][A-Z0-9]+-)+[A-Z][A-Z0-9]+)-\d+\b"
#: A PR ref "#12" that is not a phrase like "Question #3" / "Step #2".
_PR = r"(?<![\w&])(?<!Question )(?<!question )(?<!Step )(?<!step )(?<!No\. )(?<!Number )(?<!number )#\d+\b"
_REF_PATTERNS = (
    # "(#202 · MCK-46)"-style groups first, then bare tokens.
    re.compile(r"\(\s*[^()]*?(?:" + _PR + "|" + _TICKET + r")[^()]*\)"),
    # Branch names go whole: "feat/mck-182-whats-new-history".
    re.compile(r"\b(?:feat|fix|chore|hotfix|refactor)/[\w./-]+"),
    re.compile(_TICKET),
    re.compile(_PR),
    re.compile(r"\b(?=[0-9a-fA-F]*\d)(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{7,40}\b"),
)


def scrub_refs(text: Any) -> str:
    """Teacher text with ticket ids, PR numbers and SHAs taken out.

    Notes are already checked when they are written; this is the last guard
    before anything reaches a teacher's screen.

    Args:
        text: Any value; ``None`` becomes ``""``.

    Returns:
        The text with refs removed and spacing tidied.
    """
    out = str(text if text is not None else "")
    group, *tokens = _REF_PATTERNS
    prev = None
    while prev != out:  # nested "(#202 (+#200) · MCK-46)"
        prev, out = out, group.sub("", out)
    for pattern in tokens:
        out = pattern.sub("", out)
    out = re.sub(r"\([\s·,+]*\)", "", out)
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip(" ·")


def clean_items(release: dict[str, Any]) -> list[dict[str, Any]]:
    """Object items with some text, title or line (others drop out)."""
    items = release.get("items") if isinstance(release, dict) else None
    if not isinstance(items, list):
        return []
    return [
        item
        for item in items
        if isinstance(item, dict)
        and any(str(item.get(k) if item.get(k) is not None else "").strip() for k in ITEM_TEXT_KEYS)
    ]


def release_day(release: dict[str, Any]) -> str:
    """Toronto ``YYYY-MM-DD`` for a release (``day``, legacy ``date`` or id)."""
    for key in ("day", "date", "id"):
        value = str(release.get(key) or "")[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return value
    stamp = str(release.get("deployed_at") or "")
    return stamp[:10] if re.match(r"\d{4}-\d{2}-\d{2}", stamp) else ""


def sort_at(release: dict[str, Any]) -> str:
    """UTC ISO sort key: ``deployed_at``, or a legacy day read as 23:59 UTC-4.

    Same rule as ``.github/scripts/whats_new.py:sort_key`` and the UI.
    """
    stamp = release.get("deployed_at")
    if stamp:
        try:
            return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    day = release_day(release)
    if not day:
        return ""
    return datetime.fromisoformat(f"{day}T23:59:00-04:00").astimezone(timezone.utc).isoformat()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the history table (idempotent)."""
    conn.executescript(SCHEMA)


def read_file(path: Path | str | None = None) -> list[dict[str, Any]]:
    """Releases in a ``releases.json`` file; ``[]`` if missing or unreadable."""
    target = Path(path) if path else RELEASES_FILE
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("whats-new: cannot read %s (%s)", target, type(exc).__name__)
        return []
    releases = data.get("releases") if isinstance(data, dict) else None
    return [r for r in releases or [] if isinstance(r, dict)]


def sync_from_file(conn: sqlite3.Connection, path: Path | str | None = None) -> dict[str, int]:
    """Copy the shipped ``releases.json`` into the history table.

    Runs in one savepoint, so a bad row leaves the table as it was.

    Args:
        conn: School database connection.
        path: File to read; defaults to the shipped ``releases.json``.

    Returns:
        Counts ``{inserted, replaced, removed, kept, skipped}``.
    """
    ensure_schema(conn)
    counts = {"inserted": 0, "replaced": 0, "removed": 0, "kept": 0, "skipped": 0}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    conn.execute("SAVEPOINT whats_new_sync")
    try:
        for release in read_file(path):
            rid = str(release.get("id") or "").strip()
            day = release_day(release)
            key = sort_at(release)
            if not rid or not day or not key:
                counts["skipped"] += 1
                continue
            items = clean_items(release)
            is_deploy = str(release.get("source") or "") == DEPLOY_SOURCE
            if not items:
                if not is_deploy and "items" in release:
                    gone = conn.execute(
                        "DELETE FROM whats_new_releases WHERE id = ?", (rid,)
                    ).rowcount
                    counts["removed"] += max(gone, 0)
                else:
                    counts["skipped"] += 1
                continue
            stored = {**release, "items": items}
            row = (
                rid,
                str(release.get("sha") or ""),
                str(release.get("deployed_at") or ""),
                day,
                key,
                DEPLOY_SOURCE if is_deploy else "committed",
                json.dumps(stored, ensure_ascii=False, sort_keys=True),
                now,
                now,
            )
            if is_deploy:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO whats_new_releases
                       (id, sha, deployed_at, day, sort_at, source, release_json, stored_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    row,
                )
                counts["inserted" if cur.rowcount > 0 else "kept"] += 1
                continue
            existing = conn.execute(
                "SELECT release_json FROM whats_new_releases WHERE id = ?", (rid,)
            ).fetchone()
            if existing is not None and existing[0] == row[6]:
                counts["kept"] += 1
                continue
            conn.execute(
                """INSERT INTO whats_new_releases
                   (id, sha, deployed_at, day, sort_at, source, release_json, stored_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     sha = excluded.sha, deployed_at = excluded.deployed_at, day = excluded.day,
                     sort_at = excluded.sort_at, source = excluded.source,
                     release_json = excluded.release_json, updated_at = excluded.updated_at""",
                row,
            )
            counts["replaced" if existing is not None else "inserted"] += 1
        conn.execute("RELEASE SAVEPOINT whats_new_sync")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT whats_new_sync")
        conn.execute("RELEASE SAVEPOINT whats_new_sync")
        raise
    return counts


def stored_releases(conn: sqlite3.Connection, limit: int = PAYLOAD_LIMIT) -> list[dict[str, Any]]:
    """Stored releases, newest first (raw, refs included: server use only)."""
    rows = conn.execute(
        "SELECT release_json FROM whats_new_releases ORDER BY sort_at DESC, id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for (raw,) in rows:
        try:
            release = json.loads(raw)
        except ValueError:
            continue
        if isinstance(release, dict):
            out.append(release)
    return out


def _teacher_item(item: dict[str, Any]) -> dict[str, Any] | None:
    audience = str(item.get("audience") or "Teacher")
    out: dict[str, Any] = {"audience": "Both" if audience.startswith("Both") else "Teacher"}
    for key in ITEM_TEXT_KEYS:
        value = scrub_refs(item.get(key))
        if value:
            out[key] = value
    return out if any(k in out for k in ITEM_TEXT_KEYS) else None


def teacher_payload(releases: list[dict[str, Any]], limit: int = PAYLOAD_LIMIT) -> dict[str, Any]:
    """What the Dashboard gets: no refs, no sha, no ids in the text.

    Args:
        releases: Raw releases (any order).
        limit: Newest releases to keep.

    Returns:
        ``{"schema": 2, "releases": [{id, deployed_at, day, items}]}``,
        newest first, housekeeping releases left out.
    """
    shaped: list[dict[str, Any]] = []
    seen: set[str] = set()
    public_ids: set[str] = set()
    for release in sorted(releases, key=sort_at, reverse=True):
        rid = str(release.get("id") or "").strip()
        day = release_day(release)
        key = sort_at(release)
        if not rid or rid in seen or not day or not key:
            continue
        items = [t for t in (_teacher_item(i) for i in clean_items(release)) if t]
        if not items:
            continue
        seen.add(rid)
        # MCK-182 gate LOW: the id teachers get is the deploy time, never the
        # deploy SHA. A repeat time gets "~2", "~3"...
        public = key
        n = 1
        while public in public_ids:
            n += 1
            public = f"{key}~{n}"
        public_ids.add(public)
        shaped.append({"id": public, "deployed_at": key, "day": day, "items": items})
        if len(shaped) >= limit:
            break
    return {"schema": 2, "releases": shaped}


_LEGACY_ID = re.compile(r"[0-9a-fA-F]{7,40}")


def legacy_seen_at(releases: list[dict[str, Any]], value: Any) -> str | None:
    """Map a seen value saved by an older Dashboard (a release SHA id) to time.

    Teachers who last saw What's new before ids became deploy times have
    the SHA in their browser. The page asks once; the answer is that
    release's ``deployed_at``, which it stores instead.

    Args:
        releases: Raw releases.
        value: The stored value from the browser.

    Returns:
        The release's sort time, or ``None`` when nothing matches.
    """
    text = str(value or "").strip()
    if not _LEGACY_ID.fullmatch(text):
        return None
    low = text.lower()
    for release in releases:
        rid = str(release.get("id") or "").strip().lower()
        sha = str(release.get("sha") or "").strip().lower()
        if rid and (rid == low or (sha and sha.startswith(low))):
            return sort_at(release) or None
    return None
