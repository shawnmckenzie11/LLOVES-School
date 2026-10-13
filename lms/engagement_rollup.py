"""MCK-58 P1.1: roll ``engagement_events`` into per-student and per-group counts.

At End Live Class the server aggregates one run's events into append-only
``engagement_rollup`` rows. Counts are keyed by *signal* (evidence type).
Offered denominators count ``item.offered`` only while the student was
present; when a signal's item type never appeared, ``offered`` is NULL (N/A).

Group-linked signals also store each member's share of the team total and
their ratio versus an even split across present teammates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/Toronto")

# Teacher-facing evidence buckets (see mobbin-sites/group-engagement-tracking-ia-v0.md).
SIGNALS: tuple[str, ...] = (
    "individual_answer",
    "group_vote",
    "group_draft",
    "group_submit",
    "board",
    "artifact",
    "checkin",
    "teacher_mark",
    "points",
)

ITEM_SIGNALS: frozenset[str] = frozenset(
    {"individual_answer", "group_vote", "group_draft", "group_submit"}
)

GROUP_SHARE_SIGNALS: frozenset[str] = frozenset(
    {"group_vote", "group_draft", "group_submit", "board", "artifact", "points"}
)

_KIND_TO_SIGNAL: dict[str, str] = {
    "answer.submit": "individual_answer",
    "answer.change": "individual_answer",
    "group.vote": "group_vote",
    "group.draft_edit": "group_draft",
    "group.submit": "group_submit",
    "group.finalize": "group_submit",
    "board.ink_batch": "board",
    "board.text": "board",
    "artifact.move": "artifact",
    "artifact.answer": "artifact",
    "checkin.mood": "checkin",
    "checkin.avatar": "checkin",
    "lookfor": "teacher_mark",
    "note": "teacher_mark",
    "points.student": "points",
    "points.team": "points",
}

# ``item.offered`` payload ``mode`` (canonical publish_mode) → signals offered.
_MODE_OFFERS: dict[str, tuple[str, ...]] = {
    "individual": ("individual_answer",),
    "individual_in_group": ("individual_answer",),
    "group": ("group_vote", "group_draft", "group_submit"),
    "group_consensus": ("group_vote", "group_draft", "group_submit"),
    "group_submit": ("group_draft", "group_submit"),
    "group_shared": ("group_draft", "group_submit"),
}

_ROLLUP_COLUMNS = (
    "class_id, live_session, run_key, rolled_at, scope, student_id, team_id, "
    "signal, count, offered, share_of_group, share_vs_even, payload"
)

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS engagement_rollup (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL,
    live_session INTEGER,
    run_key TEXT NOT NULL,
    rolled_at TEXT NOT NULL,
    scope TEXT NOT NULL,
    student_id INTEGER,
    team_id INTEGER,
    signal TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    offered INTEGER,
    share_of_group REAL,
    share_vs_even REAL,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS engagement_rollup_class_run
    ON engagement_rollup (class_id, run_key);
CREATE INDEX IF NOT EXISTS engagement_rollup_run_scope
    ON engagement_rollup (run_key, scope, signal);
"""

_POSTGRES_DDL = _SQLITE_DDL.replace("id INTEGER PRIMARY KEY", "id BIGSERIAL PRIMARY KEY")


def rollup_ddl_sqlite() -> str:
    """DDL for sqlite engagement rollup tables."""
    return _SQLITE_DDL


def rollup_ddl_postgres_parts() -> list[str]:
    """DDL statements for Postgres (split for advisory-lock installer)."""
    return [p.strip() for p in _POSTGRES_DDL.split(";") if p.strip()]


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(str(value))


def _open_intervals(events: Sequence[Mapping[str, Any]], student_id: int) -> list[tuple[datetime, datetime | None]]:
    """Presence windows for one student from join/resume through leave/stale."""
    start: datetime | None = None
    out: list[tuple[datetime, datetime | None]] = []
    for row in sorted(events, key=lambda r: (str(r.get("ts") or ""), int(r.get("id") or 0))):
        if int(row.get("student_id") or 0) != int(student_id):
            continue
        kind = str(row.get("kind") or "")
        ts = _parse_ts(str(row["ts"]))
        if kind in ("present.join", "present.resume"):
            start = ts
        elif kind in ("present.leave", "present.stale") and start is not None:
            out.append((start, ts))
            start = None
    if start is not None:
        out.append((start, None))
    return out


def _present_at(intervals: list[tuple[datetime, datetime | None]], moment: datetime) -> bool:
    for start, end in intervals:
        if moment < start:
            continue
        if end is None or moment <= end:
            return True
    return False


def _canonical_mode(raw: Any) -> str:
    token = str(raw or "individual").strip().lower()
    token = token.replace("-", " ").replace("_", " ")
    token = " ".join(token.split())
    aliases = {
        "group submit": "group_submit",
        "group submission": "group_submit",
        "group consensus": "group_consensus",
        "group shared": "group_shared",
        "individual in group": "individual_in_group",
    }
    return aliases.get(token, token.replace(" ", "_"))


def _count_event(signal: str, row: Mapping[str, Any], tallies: dict[tuple[int | None, str], set[Any]]) -> None:
    """Accumulate one event into per-student distinct-item tallies."""
    kind = str(row.get("kind") or "")
    mapped = _KIND_TO_SIGNAL.get(kind)
    if mapped != signal:
        return
    stu = row.get("student_id")
    if stu in (None, ""):
        return
    sid = int(stu)
    item = row.get("item_ref")
    key = (sid, signal)
    if signal in {"group_draft"}:
        tallies.setdefault(key, set()).add((kind, item, row.get("ts")))
        return
    if signal == "board":
        payload = row.get("payload") or {}
        try:
            payload = json.loads(payload) if isinstance(payload, str) else payload
        except (TypeError, ValueError):
            payload = {}
        ops = int(payload.get("ops") or 1) if isinstance(payload, dict) else 1
        tallies.setdefault(key, set()).add(("board", ops))
        return
    tallies.setdefault(key, set()).add(str(item))


def _tally_count(signal: str, items: set[Any]) -> int:
    if signal == "board":
        return sum(int(x[1]) for x in items if isinstance(x, tuple) and x[0] == "board")
    if signal == "group_draft":
        return len(items)
    return len(items)


@dataclass(frozen=True)
class RollupRow:
    """One persisted rollup cell."""

    scope: str
    signal: str
    count: int
    offered: int | None
    student_id: int | None = None
    team_id: int | None = None
    share_of_group: float | None = None
    share_vs_even: float | None = None
    payload: dict[str, Any] | None = None


def compute_rollup(
    events: Sequence[Mapping[str, Any]],
    *,
    class_id: int,
    live_session: int | None,
    run_key: str,
    team_for_student: Mapping[int, int | None],
    student_ids: Iterable[int],
) -> list[RollupRow]:
    """Build student and group rollup rows for one run.

    Args:
        events: Engagement log rows for a single ``run_key``.
        class_id: Game-show class id (unused today, reserved for validation).
        live_session: Session id for the run.
        run_key: Live run uuid.
        team_for_student: Roster student id → team id (or None).
        student_ids: Every student to emit a student-scope row for.

    Returns:
        Flat list of ``RollupRow`` for students and teams.
    """
    students = sorted({int(s) for s in student_ids})
    offers_by_signal: dict[str, list[datetime]] = {s: [] for s in SIGNALS}
    for row in events:
        if str(row.get("kind") or "") != "item.offered":
            continue
        mode = _canonical_mode((row.get("payload") or {}).get("mode"))
        for sig in _MODE_OFFERS.get(mode, ()):
            offers_by_signal.setdefault(sig, []).append(_parse_ts(str(row["ts"])))

    run_offered_any: dict[str, bool] = {sig: bool(offers_by_signal.get(sig)) for sig in ITEM_SIGNALS}

    tallies: dict[tuple[int | None, str], set[Any]] = {}
    for row in events:
        kind = str(row.get("kind") or "")
        signal = _KIND_TO_SIGNAL.get(kind)
        if not signal:
            continue
        stu = row.get("student_id")
        if stu not in (None, ""):
            _count_event(signal, row, tallies)

    presence: dict[int, list[tuple[datetime, datetime | None]]] = {
        sid: _open_intervals(events, sid) for sid in students
    }

    def offered_for_student(sig: str, sid: int) -> int | None:
        if sig not in ITEM_SIGNALS:
            return None
        if not run_offered_any.get(sig):
            return None
        intervals = presence.get(sid) or []
        if not intervals:
            return 0
        return sum(
            1
            for moment in offers_by_signal.get(sig, [])
            if _present_at(intervals, moment)
        )

    rows: list[RollupRow] = []
    team_members: dict[int, list[int]] = {}
    for sid in students:
        tid = team_for_student.get(sid)
        if tid is not None:
            team_members.setdefault(int(tid), []).append(sid)

    for sid in students:
        tid = team_for_student.get(sid)
        tid_int = int(tid) if tid not in (None, "") else None
        for sig in SIGNALS:
            count = _tally_count(sig, tallies.get((sid, sig), set()))
            offered = offered_for_student(sig, sid)
            share_of: float | None = None
            share_even: float | None = None
            if sig in GROUP_SHARE_SIGNALS and tid_int is not None:
                members = team_members.get(tid_int, [])
                present_members = [m for m in members if presence.get(m)]
                group_count = sum(
                    _tally_count(sig, tallies.get((m, sig), set())) for m in members
                )
                if group_count > 0 and count >= 0:
                    share_of = count / group_count
                n = len(present_members) or len(members)
                if group_count > 0 and n > 0:
                    even = group_count / n
                    share_even = count / even if even > 0 else None
            rows.append(
                RollupRow(
                    scope="student",
                    signal=sig,
                    count=count,
                    offered=offered,
                    student_id=sid,
                    team_id=tid_int,
                    share_of_group=share_of,
                    share_vs_even=share_even,
                )
            )

    for tid, members in sorted(team_members.items()):
        for sig in SIGNALS:
            count = sum(_tally_count(sig, tallies.get((m, sig), set())) for m in members)
            offered: int | None
            if sig not in ITEM_SIGNALS:
                offered = None
            elif not run_offered_any.get(sig):
                offered = None
            else:
                offered = len(offers_by_signal.get(sig, []))
            rows.append(
                RollupRow(
                    scope="group",
                    signal=sig,
                    count=count,
                    offered=offered,
                    team_id=tid,
                )
            )
    return rows


def rollup_row_to_store(
    row: RollupRow,
    *,
    class_id: int,
    live_session: int | None,
    run_key: str,
    rolled_at: str | None = None,
) -> dict[str, Any]:
    """Map a ``RollupRow`` into an insert dict for the engagement store."""
    payload = row.payload or {}
    return {
        "class_id": int(class_id),
        "live_session": live_session,
        "run_key": str(run_key),
        "rolled_at": rolled_at
        or datetime.now(_TZ).isoformat(timespec="seconds"),
        "scope": row.scope,
        "student_id": row.student_id,
        "team_id": row.team_id,
        "signal": row.signal,
        "count": int(row.count),
        "offered": row.offered,
        "share_of_group": row.share_of_group,
        "share_vs_even": row.share_vs_even,
        "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
    }
