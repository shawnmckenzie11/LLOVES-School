"""MCK-183 I1: Admin email invites for new teachers.

One row per invite in ``staff_invites``. The link token is random
(``secrets.token_urlsafe(32)``) and only its sha256 is stored, so a link
can't be shown twice: Resend and Copy link mint a new token and the old one
stops working. Links expire after 7 days and work once.

``users.is_test`` marks a Test user: tagged "Test" in Admin and left out of
staff reports; otherwise the account is a normal teacher.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any

INVITE_TTL = timedelta(days=7)

#: Sends per Admin in a rolling hour (form + Resend + Copy link).
SENDS_PER_ADMIN_PER_HOUR = 20

#: Minimum gap between two sends to the same invite (double-click guard).
MIN_RESEND_GAP = timedelta(seconds=30)

INVITE_KINDS = frozenset({"teacher", "test"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS staff_invites (
    id INTEGER PRIMARY KEY,
    tenant_id INTEGER NOT NULL DEFAULT 1,
    user_id INTEGER NOT NULL REFERENCES users(id),
    email TEXT NOT NULL,
    first_name TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'teacher',
    preset_code TEXT,
    preset_days TEXT,
    preset_time TEXT,
    token_sha256 TEXT NOT NULL UNIQUE,
    created_by_user_id INTEGER,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    send_count INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL,
    accepted_at TEXT,
    revoked_at TEXT,
    owns_account INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_staff_invites_email ON staff_invites(tenant_id, email);
CREATE TABLE IF NOT EXISTS staff_invite_sends (
    id INTEGER PRIMARY KEY,
    invite_id INTEGER NOT NULL REFERENCES staff_invites(id),
    by_user_id INTEGER,
    sent_at TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_staff_invite_sends_by ON staff_invite_sends(by_user_id, sent_at);
"""


class InviteRateLimited(ValueError):
    """Too many invite emails from one Admin, or a double click."""


def _now() -> datetime:
    """Local time without microseconds (matches ``school_db._now``)."""
    return datetime.now().replace(microsecond=0)


def _iso(value: datetime) -> str:
    """ISO text for sqlite."""
    return value.isoformat()


def token_hash(token: str) -> str:
    """sha256 hex of a raw link token.

    Args:
        token: Raw token from the link.

    Returns:
        64-char hex digest.
    """
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def new_token() -> str:
    """A fresh random link token (43 url-safe chars)."""
    return secrets.token_urlsafe(32)


def ensure_schema(conn: Any) -> None:
    """Create the invite tables and ``users.is_test`` (live migration).

    Args:
        conn: sqlite connection (caller holds the boot lock).
    """
    conn.executescript(SCHEMA)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "is_test" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0")
    invite_cols = {row[1] for row in conn.execute("PRAGMA table_info(staff_invites)")}
    if "owns_account" not in invite_cols:
        # 1 when the invite created (or reactivated) the account, so Revoke
        # may undo it. An invite to an existing active teacher never owns it.
        conn.execute(
            "ALTER TABLE staff_invites ADD COLUMN owns_account INTEGER NOT NULL DEFAULT 0"
        )


def invite_status(row: dict[str, Any], now: datetime | None = None) -> str:
    """``joined``, ``revoked``, ``expired`` or ``pending``.

    Args:
        row: ``staff_invites`` row.
        now: Clock override for tests.

    Returns:
        Derived status.
    """
    if row.get("accepted_at"):
        return "joined"
    if row.get("revoked_at"):
        return "revoked"
    current = now or _now()
    try:
        expires = datetime.fromisoformat(str(row.get("expires_at")))
    except ValueError:
        return "expired"
    return "expired" if current >= expires else "pending"


def get_invite(school: Any, invite_id: int) -> dict[str, Any] | None:
    """One invite row by id."""
    with school._lock:
        row = school.conn.execute(
            "SELECT * FROM staff_invites WHERE id = ?", (int(invite_id),)
        ).fetchone()
    return dict(row) if row else None


def open_invite_for(school: Any, tenant_id: int, email: str) -> dict[str, Any] | None:
    """Newest invite for this email that is neither joined nor revoked."""
    with school._lock:
        row = school.conn.execute(
            """
            SELECT * FROM staff_invites
            WHERE tenant_id = ? AND email = ? AND accepted_at IS NULL AND revoked_at IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            (int(tenant_id), email),
        ).fetchone()
    return dict(row) if row else None


def check_send_rate(school: Any, by_user_id: int | None, invite: dict[str, Any] | None) -> None:
    """Raise :class:`InviteRateLimited` when this send is over the limits.

    Args:
        school: ``SchoolDB``.
        by_user_id: Admin sending.
        invite: Existing invite being re-sent, if any.
    """
    now = _now()
    if invite and invite.get("sent_at"):
        try:
            last = datetime.fromisoformat(str(invite["sent_at"]))
        except ValueError:
            last = None
        if last is not None and now - last < MIN_RESEND_GAP:
            raise InviteRateLimited("That invite was just sent. Wait a moment before sending it again.")
    if by_user_id is None:
        return
    since = _iso(now - timedelta(hours=1))
    with school._lock:
        count = school.conn.execute(
            "SELECT COUNT(*) FROM staff_invite_sends WHERE by_user_id = ? AND sent_at >= ?",
            (int(by_user_id), since),
        ).fetchone()[0]
    if int(count) >= SENDS_PER_ADMIN_PER_HOUR:
        raise InviteRateLimited("Too many invites in the last hour. Try again later.")


def create_or_refresh_invite(
    school: Any,
    *,
    email: str,
    first_name: str,
    kind: str = "teacher",
    preset: tuple[str, str, str] | None = None,
    created_by_user_id: int | None = None,
    tenant_id: int | None = None,
) -> tuple[dict[str, Any], str]:
    """Allowlist the teacher and create (or refresh) her invite.

    Idempotent per email: an open invite gets a new token, a new 7-day
    expiry and the new details; otherwise a new invite row is made. An
    archived (revoked) account is reactivated, since inviting again is the
    Admin's explicit choice.

    Args:
        school: ``SchoolDB``.
        email: Google email.
        first_name: Teacher first name.
        kind: ``teacher`` or ``test``.
        preset: Optional validated ``(code, days, time)``.
        created_by_user_id: Admin user id.
        tenant_id: School seam.

    Returns:
        ``(invite_row, raw_token)``. The raw token is never stored.

    Raises:
        ValueError: Bad email or kind, or an Admin (IT) address.
    """
    kind_s = str(kind or "teacher").strip().lower()
    if kind_s not in INVITE_KINDS:
        raise ValueError("Pick Teacher or Test user.")
    first = str(first_name or "").strip()[:80]
    # #269 gate: an invite to a teacher who already has an active account must
    # not change that account (Test flag) and Revoke must never archive it.
    before = school.get_user_by_email(email) if str(email or "").strip() else None
    user = school.register_staff(email, first or None, tenant_id=tenant_id)
    if not user or not user.get("id"):
        raise ValueError("Enter a Google email address")
    if str(user.get("role") or "") == "it":
        raise ValueError("That address is an Admin account and can already sign in.")
    reactivated = bool(user.get("archived_at"))
    if reactivated:
        user = school.reactivate_staff(int(user["id"]))
    tid = int(user.get("tenant_id") or tenant_id or school.default_tenant_id())
    key = str(user["email"]).strip().lower()
    existing = open_invite_for(school, tid, key)
    owns = (
        before is None
        or reactivated
        or bool(existing and int(existing.get("owns_account") or 0))
    )
    token = new_token()
    now = _now()
    code, days, time = preset if preset else (None, None, None)
    with school._lock:
        if owns:
            school.conn.execute(
                "UPDATE users SET is_test = ? WHERE id = ?",
                (1 if kind_s == "test" else 0, int(user["id"])),
            )
        if first and not str(user.get("display_name") or "").strip():
            school.conn.execute(
                "UPDATE users SET display_name = ? WHERE id = ?", (first, int(user["id"]))
            )
        school.conn.commit()
    with school._lock:
        if existing:
            school.conn.execute(
                """
                UPDATE staff_invites
                SET first_name = ?, kind = ?, preset_code = ?, preset_days = ?,
                    preset_time = ?, token_sha256 = ?, expires_at = ?, owns_account = ?
                WHERE id = ?
                """,
                (first, kind_s, code, days, time, token_hash(token),
                 _iso(now + INVITE_TTL), 1 if owns else 0, int(existing["id"])),
            )
            invite_id = int(existing["id"])
        else:
            cur = school.conn.execute(
                """
                INSERT INTO staff_invites (
                    tenant_id, user_id, email, first_name, kind, preset_code,
                    preset_days, preset_time, token_sha256, created_by_user_id,
                    created_at, expires_at, owns_account
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tid, int(user["id"]), key, first, kind_s, code, days, time,
                 token_hash(token), created_by_user_id, _iso(now), _iso(now + INVITE_TTL),
                 1 if owns else 0),
            )
            invite_id = int(cur.lastrowid)
        school.conn.commit()
    return get_invite(school, invite_id) or {}, token


def rotate_token(school: Any, invite_id: int) -> tuple[dict[str, Any], str]:
    """New token and a fresh 7-day expiry. Older links stop working.

    Args:
        school: ``SchoolDB``.
        invite_id: Invite to rotate.

    Returns:
        ``(invite_row, raw_token)``.

    Raises:
        KeyError: Unknown invite.
        ValueError: The invite was already used or revoked.
    """
    row = get_invite(school, invite_id)
    if row is None:
        raise KeyError(f"invite {invite_id}")
    if row.get("accepted_at") or row.get("revoked_at"):
        raise ValueError("This invite can't be sent again.")
    token = new_token()
    with school._lock:
        school.conn.execute(
            "UPDATE staff_invites SET token_sha256 = ?, expires_at = ? WHERE id = ?",
            (token_hash(token), _iso(_now() + INVITE_TTL), int(invite_id)),
        )
        school.conn.commit()
    return get_invite(school, invite_id) or {}, token


def record_send(school: Any, invite_id: int, by_user_id: int | None, delivered: bool) -> None:
    """Log one send (for the rate limit) and bump the invite counters."""
    stamp = _iso(_now())
    with school._lock:
        school.conn.execute(
            "INSERT INTO staff_invite_sends (invite_id, by_user_id, sent_at, delivered) VALUES (?, ?, ?, ?)",
            (int(invite_id), by_user_id, stamp, 1 if delivered else 0),
        )
        school.conn.execute(
            "UPDATE staff_invites SET sent_at = ?, send_count = send_count + 1 WHERE id = ?",
            (stamp, int(invite_id)),
        )
        school.conn.commit()


def resolve_invite(school: Any, token: str) -> tuple[str, dict[str, Any] | None]:
    """Look up a link token.

    Args:
        school: ``SchoolDB``.
        token: Raw token from ``/invite/<token>``.

    Returns:
        ``(state, row)`` where state is ``valid``, ``expired``, ``used``,
        ``revoked`` or ``unknown`` (row ``None``).
    """
    if not token or len(str(token)) > 200:
        return "unknown", None
    with school._lock:
        row = school.conn.execute(
            "SELECT * FROM staff_invites WHERE token_sha256 = ?", (token_hash(token),)
        ).fetchone()
    if row is None:
        return "unknown", None
    item = dict(row)
    status = invite_status(item)
    return {"pending": "valid", "joined": "used"}.get(status, status), item


def mark_accepted(school: Any, invite_id: int) -> None:
    """Single use: stamp ``accepted_at`` once."""
    with school._lock:
        school.conn.execute(
            "UPDATE staff_invites SET accepted_at = ? WHERE id = ? AND accepted_at IS NULL",
            (_iso(_now()), int(invite_id)),
        )
        school.conn.commit()


def revoke_invite(school: Any, invite_id: int, by_user_id: int) -> dict[str, Any]:
    """Revoke a pending invite; archive the account only if the invite made it.

    Args:
        school: ``SchoolDB``.
        invite_id: Invite id.
        by_user_id: Admin revoking.

    Returns:
        The updated invite row.

    Raises:
        KeyError: Unknown invite.
        ValueError: Already joined.
    """
    row = get_invite(school, invite_id)
    if row is None:
        raise KeyError(f"invite {invite_id}")
    if row.get("accepted_at"):
        raise ValueError("This teacher already joined. Deactivate them under Staff instead.")
    with school._lock:
        school.conn.execute(
            "UPDATE staff_invites SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?",
            (_iso(_now()), int(invite_id)),
        )
        school.conn.commit()
    # Only undo what the invite did: an account it created or reactivated is
    # archived again; a teacher who already had an account keeps it as is.
    if not int(row.get("owns_account") or 0):
        return get_invite(school, invite_id) or {}
    user = school.get_user(int(row["user_id"]))
    if user and not user.get("archived_at") and str(user.get("role")) == "staff":
        school.deactivate_staff(int(row["user_id"]), int(by_user_id))
    return get_invite(school, invite_id) or {}


def list_invites(school: Any, tenant_id: int | None = None) -> list[dict[str, Any]]:
    """Every non-revoked invite for Admin, newest first, with ``status``.

    Args:
        school: ``SchoolDB``.
        tenant_id: School seam.

    Returns:
        Rows with ``status`` and ``days_left``.
    """
    tid = int(tenant_id or school.default_tenant_id())
    with school._lock:
        rows = school.conn.execute(
            "SELECT * FROM staff_invites WHERE tenant_id = ? AND revoked_at IS NULL ORDER BY id DESC",
            (tid,),
        ).fetchall()
    now = _now()
    out = []
    for raw in rows:
        item = dict(raw)
        item["status"] = invite_status(item, now)
        try:
            left = datetime.fromisoformat(str(item["expires_at"])) - now
            item["seconds_left"] = max(0, int(left.total_seconds()))
        except ValueError:
            item["seconds_left"] = 0
        out.append(item)
    return out


def days_left_label(seconds_left: int) -> str:
    """Wonder v1.4 ``inv.list.left``."""
    if int(seconds_left) < 86400:
        return "Expires today"
    days = max(1, round(int(seconds_left) / 86400))
    if days == 1:
        return "1 day left"
    return f"{days} days left"


def invite_tabs(school: Any, tenant_id: int | None = None) -> dict[str, list[dict[str, Any]]]:
    """Admin Invites card: ``pending`` / ``joined`` / ``expired`` row lists.

    Each row gains ``left_label`` (Wonder's ``inv.list.left``),
    ``preset_label`` (``SBI4U · MWF · 2:00pm`` or ``""``) and ``sent_label``.

    Args:
        school: ``SchoolDB``.
        tenant_id: School seam.

    Returns:
        Dict of the three tabs, newest first.
    """
    from onboarding_presets import DAY_LABELS

    tabs: dict[str, list[dict[str, Any]]] = {"pending": [], "joined": [], "expired": []}
    for item in list_invites(school, tenant_id):
        parts = [
            str(item.get("preset_code") or ""),
            DAY_LABELS.get(str(item.get("preset_days") or ""), str(item.get("preset_days") or "")),
            str(item.get("preset_time") or ""),
        ]
        item["preset_label"] = " · ".join(p for p in parts if p) if parts[0] else ""
        item["left_label"] = days_left_label(int(item.get("seconds_left") or 0))
        sent = str(item.get("sent_at") or item.get("created_at") or "")
        try:
            item["sent_label"] = datetime.fromisoformat(sent).strftime("%b %-d")
        except ValueError:
            item["sent_label"] = ""
        joined = str(item.get("accepted_at") or "")
        try:
            item["joined_label"] = datetime.fromisoformat(joined).strftime("%b %-d") if joined else ""
        except ValueError:
            item["joined_label"] = ""
        if item["status"] in tabs:
            tabs[item["status"]].append(item)
    return tabs


def accepted_preset_for(school: Any, user_id: int) -> tuple[str, str, str] | None:
    """The class preset from this teacher's accepted invite, for screen 2.

    Args:
        school: ``SchoolDB``.
        user_id: Teacher ``users.id``.

    Returns:
        ``(code, days, time)`` or ``None`` when the invite had no preset.
    """
    with school._lock:
        row = school.conn.execute(
            """
            SELECT preset_code, preset_days, preset_time FROM staff_invites
            WHERE user_id = ? AND accepted_at IS NOT NULL AND preset_code IS NOT NULL
              AND preset_code != ''
            ORDER BY accepted_at DESC, id DESC LIMIT 1
            """,
            (int(user_id),),
        ).fetchone()
    if row is None:
        return None
    return (
        str(row["preset_code"] or ""),
        str(row["preset_days"] or ""),
        str(row["preset_time"] or ""),
    )

