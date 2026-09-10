"""School sqlite for LLOVES users, semesters, catalog, and course offerings."""

from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from codes import generate_live_access_code
    from live_media import (
        apply_active_media_update,
        challenge_clears_active_media,
        get_c1_cons_item,
        is_c1_cons_payload,
        is_c1_real_slice,
        public_active_media_payload,
        staff_cons_prompt_payload,
    )
    from live_prompt_feedback import (
        public_feedback_fragment,
        strip_teacher_prompt_fields,
    )
    from minds_on import (
        MINDS_ON_KIND,
        MINDS_ON_SLIDE_INDEX,
        is_minds_on_payload,
        minds_on_prompt_payload,
    )
    from paths import GAME_SHOW, SEMESTER_JSON
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.codes import generate_live_access_code
    from lms.live_media import (
        apply_active_media_update,
        challenge_clears_active_media,
        get_c1_cons_item,
        is_c1_cons_payload,
        is_c1_real_slice,
        public_active_media_payload,
        staff_cons_prompt_payload,
    )
    from lms.live_prompt_feedback import (
        public_feedback_fragment,
        strip_teacher_prompt_fields,
    )
    from lms.minds_on import (
        MINDS_ON_KIND,
        MINDS_ON_SLIDE_INDEX,
        is_minds_on_payload,
        minds_on_prompt_payload,
    )
    from lms.paths import GAME_SHOW, SEMESTER_JSON

IT_EMAIL_DEFAULT = "solutions@mckenzian.com"

SETTING_STAFF_2FA_MODE = "staff_admin_2fa_mode"
STAFF_2FA_FIRST_LOGIN = "first_login"
STAFF_2FA_DAILY = "daily"
STAFF_2FA_EVERY_SIGN_IN = "every_sign_in"
STAFF_2FA_MODES: tuple[str, ...] = (
    STAFF_2FA_FIRST_LOGIN,
    STAFF_2FA_DAILY,
    STAFF_2FA_EVERY_SIGN_IN,
)
STAFF_2FA_MODE_LABELS: dict[str, str] = {
    STAFF_2FA_FIRST_LOGIN: "Only on first log in",
    STAFF_2FA_DAILY: "Only on first log in each day",
    STAFF_2FA_EVERY_SIGN_IN: "Every sign in",
}


def normalize_staff_2fa_mode(raw: str | None) -> str:
    """Return a valid staff/admin 2FA mode, defaulting to first login.

    Args:
        raw: Stored or posted value.

    Returns:
        One of ``STAFF_2FA_MODES``.
    """
    value = (raw or "").strip()
    if value in STAFF_2FA_MODES:
        return value
    return STAFF_2FA_FIRST_LOGIN


DEFAULT_TENANT_SLUG = "elc"
DEFAULT_TENANT_NAME = "ELC (single school)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    google_sub TEXT UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    tenant_id INTEGER NOT NULL DEFAULT 1,
    verified_at TEXT,
    verification_code TEXT,
    verification_sent_at TEXT,
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS semesters (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    year_display TEXT NOT NULL,
    term TEXT NOT NULL,
    source_pdf TEXT,
    is_active INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS curriculum_documents (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    jurisdiction TEXT NOT NULL,
    grades TEXT,
    subject TEXT,
    source_url TEXT,
    local_path TEXT
);

CREATE TABLE IF NOT EXISTS ontario_courses (
    code TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    grade INTEGER,
    pathway TEXT,
    document_id INTEGER REFERENCES curriculum_documents(id),
    content_root TEXT,
    verification_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expectations (
    id INTEGER PRIMARY KEY,
    course_code TEXT NOT NULL,
    kind TEXT NOT NULL,
    code TEXT NOT NULL,
    parent_code TEXT,
    strand TEXT,
    statement TEXT NOT NULL,
    verification_status TEXT,
    UNIQUE(course_code, code)
);

CREATE TABLE IF NOT EXISTS course_offerings (
    id INTEGER PRIMARY KEY,
    semester_id INTEGER NOT NULL REFERENCES semesters(id),
    ontario_code TEXT NOT NULL REFERENCES ontario_courses(code),
    teacher_user_id INTEGER NOT NULL REFERENCES users(id),
    tenant_id INTEGER NOT NULL DEFAULT 1,
    live_access_code TEXT NOT NULL,
    imscc_path TEXT,
    expectations_status TEXT NOT NULL DEFAULT 'unverified',
    student_options_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    copied_from_offering_id INTEGER,
    instance_relpath TEXT,
    section_index INTEGER NOT NULL DEFAULT 1,
    archived_at TEXT,
    live_days TEXT,
    live_time TEXT,
    ap_round_profiles_json TEXT,
    slides_template_id TEXT,
    style_guide_file_id TEXT,
    UNIQUE(semester_id, ontario_code, teacher_user_id, section_index)
);

CREATE INDEX IF NOT EXISTS idx_live_access_code
    ON course_offerings(live_access_code);

CREATE TABLE IF NOT EXISTS content_libraries (
    id INTEGER PRIMARY KEY,
    ontario_code TEXT NOT NULL,
    origin TEXT NOT NULL,
    source_path TEXT,
    source_sha256 TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_content_libraries_code
    ON content_libraries(ontario_code);

CREATE TABLE IF NOT EXISTS student_code_attempts (
    id INTEGER PRIMARY KEY,
    ip TEXT NOT NULL,
    ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS live_class_sessions (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL,
    offering_id INTEGER NOT NULL,
    teacher_user_id INTEGER NOT NULL,
    session_code TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    mgs_session_id INTEGER,
    allow_unmatched_guests INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_live_session_code_active
    ON live_class_sessions(session_code)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_live_sessions_class_status
    ON live_class_sessions(class_id, status);

CREATE TABLE IF NOT EXISTS live_session_attendees (
    id INTEGER PRIMARY KEY,
    live_session_id INTEGER NOT NULL
        REFERENCES live_class_sessions(id) ON DELETE CASCADE,
    student_id INTEGER,
    participant_uuid TEXT NOT NULL UNIQUE,
    visit_token TEXT UNIQUE,
    codename TEXT NOT NULL DEFAULT '',
    unmatched INTEGER NOT NULL DEFAULT 0,
    joined_at TEXT NOT NULL,
    left_at TEXT,
    last_heartbeat_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_live_session_attendees_session
    ON live_session_attendees(live_session_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_live_session_attendees_roster
    ON live_session_attendees(live_session_id, student_id)
    WHERE student_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS live_session_prompts (
    id INTEGER PRIMARY KEY,
    live_session_id INTEGER NOT NULL
        REFERENCES live_class_sessions(id) ON DELETE CASCADE,
    slide_index INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'idle',
    payload TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_live_session_prompts_session
    ON live_session_prompts(live_session_id, active);

CREATE UNIQUE INDEX IF NOT EXISTS idx_live_session_prompts_slide
    ON live_session_prompts(live_session_id, slide_index);

CREATE TABLE IF NOT EXISTS live_session_responses (
    id INTEGER PRIMARY KEY,
    prompt_id INTEGER NOT NULL
        REFERENCES live_session_prompts(id) ON DELETE CASCADE,
    student_id INTEGER,
    participant_uuid TEXT,
    response_json TEXT NOT NULL DEFAULT '{}',
    awarded_points REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(prompt_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_live_session_responses_prompt
    ON live_session_responses(prompt_id);

CREATE TABLE IF NOT EXISTS google_api_tokens (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    refresh_token TEXT NOT NULL,
    access_token TEXT,
    access_token_expires_at TEXT,
    scopes TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lesson_slide_decks (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL,
    module_number INTEGER NOT NULL,
    live_index INTEGER NOT NULL,
    presentation_id TEXT,
    presentation_url TEXT,
    preview_json TEXT,
    fill_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(class_id, module_number, live_index)
);

CREATE TABLE IF NOT EXISTS live_problems (
    id INTEGER PRIMARY KEY,
    ontario_code TEXT NOT NULL,
    module_hint TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    stem_html TEXT NOT NULL DEFAULT '',
    task_html TEXT NOT NULL DEFAULT '',
    diagram_note TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    license TEXT NOT NULL DEFAULT '',
    expectation_codes_json TEXT NOT NULL DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS live_problem_processes (
    problem_id INTEGER NOT NULL
        REFERENCES live_problems(id) ON DELETE CASCADE,
    process_key TEXT NOT NULL,
    PRIMARY KEY (problem_id, process_key)
);

CREATE TABLE IF NOT EXISTS math_processes (
    process_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    statement TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quick_phrases (
    id INTEGER PRIMARY KEY,
    process_key TEXT NOT NULL,
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    live_session_id INTEGER NOT NULL
        REFERENCES live_class_sessions(id) ON DELETE CASCADE,
    class_id INTEGER NOT NULL,
    observer_user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    scope TEXT NOT NULL,
    team_id INTEGER,
    note TEXT NOT NULL DEFAULT '',
    evidence_strength TEXT,
    follow_up_required INTEGER NOT NULL DEFAULT 0,
    visibility TEXT NOT NULL DEFAULT 'staff',
    source TEXT NOT NULL DEFAULT 'manual',
    lookfor_key TEXT,
    quick_phrase_id INTEGER REFERENCES quick_phrases(id)
);

CREATE INDEX IF NOT EXISTS idx_observations_session
    ON observations(live_session_id, created_at);

CREATE TABLE IF NOT EXISTS observation_subjects (
    observation_id INTEGER NOT NULL
        REFERENCES observations(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL,
    PRIMARY KEY (observation_id, student_id)
);

CREATE TABLE IF NOT EXISTS observation_processes (
    observation_id INTEGER NOT NULL
        REFERENCES observations(id) ON DELETE CASCADE,
    process_key TEXT NOT NULL,
    PRIMARY KEY (observation_id, process_key)
);

CREATE TABLE IF NOT EXISTS portfolio_mark_suggestions (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL,
    ontario_code TEXT NOT NULL,
    module_number INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    suggested_json TEXT NOT NULL DEFAULT '{}',
    override_json TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(class_id, module_number, student_id)
);

CREATE TABLE IF NOT EXISTS access_audit_log (
    id INTEGER PRIMARY KEY,
    tenant_id INTEGER NOT NULL,
    actor_user_id INTEGER,
    actor_role TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    student_id INTEGER,
    ip TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_tenant_created
    ON access_audit_log(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS access_requests (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    organization TEXT NOT NULL DEFAULT '',
    context TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    reviewed_by_user_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_access_requests_email
    ON access_requests(email);
CREATE INDEX IF NOT EXISTS idx_access_requests_status
    ON access_requests(status);
"""


def _now() -> str:
    """Return a local ISO timestamp without microseconds."""
    return datetime.now().replace(microsecond=0).isoformat()


# Missed heartbeats after this window mark an attendee left (tab close / drop).
# ~10s client beat; 90s survives Chrome background timer throttling.
LIVE_HEARTBEAT_INTERVAL_SECONDS = 10
LIVE_HEARTBEAT_STALE_SECONDS = 90

# Never serialize these attendee columns to overlay / staff state APIs.
LIVE_ATTENDEE_SECRET_KEYS = ("visit_token",)


def first_name_only(raw: str) -> str:
    """Keep the first token of a typed name; never persist a last name.

    Args:
        raw: Student-entered name or Codename.

    Returns:
        Trimmed first token, or ``""``.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    return text.split()[0]


def _parse_iso_datetime(raw: Any) -> datetime | None:
    """Parse an ISO timestamp stored by ``_now()``.

    Args:
        raw: ISO datetime string or blank.

    Returns:
        Naive datetime, or ``None`` when unparseable.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def public_live_attendee(row: dict[str, Any]) -> dict[str, Any]:
    """Copy an attendee row without rejoin secrets.

    Args:
        row: ``live_session_attendees`` mapping (may include mood).

    Returns:
        JSON-safe attendee dict for overlay / staff state.
    """
    item = dict(row)
    for key in LIVE_ATTENDEE_SECRET_KEYS:
        item.pop(key, None)
    sid = item.get("student_id")
    item["student_id"] = int(sid) if sid not in (None, "") else None
    item["unmatched"] = bool(int(item.get("unmatched") or 0))
    item["participant_uuid"] = str(item.get("participant_uuid") or "")
    return item


def format_human_datetime(raw: str | None) -> str:
    """Turn an ISO timestamp into a short, human-readable local string.

    Args:
        raw: ISO datetime (``2026-09-03T17:40:13``) or blank.

    Returns:
        ``Never``, ``Today · 5:40 PM``, ``Yesterday · 5:40 PM``,
        ``Sep 3 · 5:40 PM``, or ``Sep 3, 2025 · 5:40 PM``.
    """
    text = str(raw or "").strip()
    if not text:
        return "Never"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return text
    now = datetime.now().replace(microsecond=0)
    hour = dt.hour % 12 or 12
    meridiem = "AM" if dt.hour < 12 else "PM"
    clock = f"{hour}:{dt.minute:02d} {meridiem}"
    day = dt.date()
    today = now.date()
    yesterday = today.fromordinal(today.toordinal() - 1)
    if day == today:
        return f"Today · {clock}"
    if day == yesterday:
        return f"Yesterday · {clock}"
    months = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )
    mon = months[dt.month - 1]
    if dt.year == now.year:
        return f"{mon} {dt.day} · {clock}"
    return f"{mon} {dt.day}, {dt.year} · {clock}"


def parse_semester_label(semester: str) -> tuple[str, str]:
    """Turn ``2026-2027 S1`` into ``(2026/27, Semester 1)``.

    Args:
        semester: Value of ``semester.json``'s ``semester`` field.

    Returns:
        ``(year_display, term_name)``.
    """
    text = (semester or "").strip()
    parts = text.split()
    year_raw = parts[0] if parts else ""
    term = parts[1] if len(parts) > 1 else ""
    years = year_raw.split("-")
    if len(years) == 2 and years[0].isdigit() and years[1].isdigit():
        year_display = f"{years[0]}/{years[1][-2:]}"
    else:
        year_display = year_raw or "2026/27"
    if "S2" in term.upper():
        return year_display, "Semester 2"
    return year_display, "Semester 1"


def section_code(ontario_code: str, section_index: Any = 1) -> str:
    """Return the teacher-facing code for one section of a course.

    The first section a teacher holds of a code keeps the plain Ontario code
    so existing courses read unchanged; later sections get ``-2``, ``-3``, ….

    Args:
        ontario_code: Catalog course code (``MCF3M``).
        section_index: 1-based occurrence for this (teacher, code, term).

    Returns:
        ``MCF3M`` for section 1, ``MCF3M-2`` for section 2, and so on.
    """
    code = (ontario_code or "").strip().upper()
    try:
        index = int(section_index or 1)
    except (TypeError, ValueError):
        index = 1
    if index <= 1:
        return code
    return f"{code}-{index}"


class LovesDB:
    """School-level sqlite (users, semesters, offerings) on the shared LMS file."""

    def __init__(self, db_path: Path, *, it_email: str = IT_EMAIL_DEFAULT) -> None:
        """Open the school database and seed catalog + IT user.

        Args:
            db_path: Shared sqlite path (same file as Math Game Show).
            it_email: Bootstrap IT Google email.
        """
        self.db_path = db_path
        self.it_email = it_email.lower().strip()
        self._lock = threading.RLock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._ensure_tenant_and_audit_schema()
        self._ensure_offering_columns()
        self._ensure_offering_sections()
        # Section rebuild may recreate course_offerings; re-apply additive columns.
        self._ensure_offering_columns()
        self._ensure_tenant_and_audit_schema()
        self._ensure_offering_archived_column()
        self._ensure_offering_schedule_columns()
        self._ensure_library_schema()
        self._ensure_archived_column()
        self._ensure_gradebook_schema()
        self._ensure_live_session_schema()
        self._ensure_live_session_identity_schema()
        self._ensure_live_class_feature_schema()
        self._ensure_access_request_schema()
        self._seed()
        self._seed_live_class_features()
        self.conn.commit()

    def close(self) -> None:
        """Close the sqlite connection."""
        with self._lock:
            self.conn.close()

    def _ensure_tenant_and_audit_schema(self) -> None:
        """Add tenant + audit tables/columns on DBs created before this schema.

        The product is still one school (ELC). ``tenants`` is the seam for a
        second homeschool later — not a claim that the live app is multi-tenant.
        """
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                id INTEGER PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS access_audit_log (
                id INTEGER PRIMARY KEY,
                tenant_id INTEGER NOT NULL,
                actor_user_id INTEGER,
                actor_role TEXT NOT NULL,
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT,
                student_id INTEGER,
                ip TEXT,
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_audit_tenant_created
            ON access_audit_log(tenant_id, created_at)
            """
        )
        existing = self.conn.execute(
            "SELECT id FROM tenants WHERE slug = ?",
            (DEFAULT_TENANT_SLUG,),
        ).fetchone()
        if existing is None:
            self.conn.execute(
                "INSERT INTO tenants (slug, name, created_at) VALUES (?, ?, ?)",
                (DEFAULT_TENANT_SLUG, DEFAULT_TENANT_NAME, _now()),
            )
        default_id = int(
            self.conn.execute(
                "SELECT id FROM tenants WHERE slug = ?",
                (DEFAULT_TENANT_SLUG,),
            ).fetchone()["id"]
        )
        user_cols = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(users)")
        }
        if "tenant_id" not in user_cols:
            self.conn.execute(
                "ALTER TABLE users ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1"
            )
        offering_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(course_offerings)")
        }
        if "tenant_id" not in offering_cols:
            self.conn.execute(
                "ALTER TABLE course_offerings "
                "ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1"
            )
        self.conn.execute(
            "UPDATE users SET tenant_id = ? WHERE tenant_id IS NULL OR tenant_id = 0",
            (default_id,),
        )
        self.conn.execute(
            """
            UPDATE course_offerings
            SET tenant_id = ?
            WHERE tenant_id IS NULL OR tenant_id = 0
            """,
            (default_id,),
        )

    def default_tenant_id(self) -> int:
        """Return the ELC tenant id (always present after schema ensure)."""
        with self._lock:
            row = self.conn.execute(
                "SELECT id FROM tenants WHERE slug = ? LIMIT 1",
                (DEFAULT_TENANT_SLUG,),
            ).fetchone()
            if row is None:
                row = self.conn.execute(
                    "SELECT id FROM tenants ORDER BY id LIMIT 1"
                ).fetchone()
        if row is None:
            raise RuntimeError("School database has no tenants row")
        return int(row["id"])

    def tenant_id_of(self, row: dict[str, Any] | None) -> int:
        """Return ``tenant_id`` from a user/offering dict, or the ELC default."""
        if row and row.get("tenant_id") not in (None, ""):
            return int(row["tenant_id"])
        return self.default_tenant_id()

    def same_tenant(
        self, left: dict[str, Any] | None, right: dict[str, Any] | None
    ) -> bool:
        """True when two rows belong to the same tenant."""
        return self.tenant_id_of(left) == self.tenant_id_of(right)

    def create_tenant(self, slug: str, name: str) -> dict[str, Any]:
        """Insert a tenant seam row (tests / future second school).

        Args:
            slug: Stable key (``homeschool-a``).
            name: Human label.

        Returns:
            The new ``tenants`` dict.
        """
        key = (slug or "").strip().lower()
        label = (name or "").strip()
        if not key or not label:
            raise ValueError("Tenant slug and name are required")
        with self._lock:
            self.conn.execute(
                "INSERT INTO tenants (slug, name, created_at) VALUES (?, ?, ?)",
                (key, label, _now()),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT * FROM tenants WHERE slug = ?", (key,)
            ).fetchone()
        return dict(row) if row else {}

    def record_access_event(
        self,
        *,
        action: str,
        resource_type: str,
        actor_user_id: int | None = None,
        actor_role: str = "",
        tenant_id: int | None = None,
        resource_id: str | int | None = None,
        student_id: int | None = None,
        ip: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Append one access/admin event for Ministry-style production.

        Args:
            action: Verb such as ``login.success`` or ``student.record.view``.
            resource_type: ``session``, ``class``, ``staff``, ``offering``.
            actor_user_id: Staff/IT ``users.id``, or None for student joins.
            actor_role: ``it``, ``staff``, or ``student``.
            tenant_id: Defaults to the ELC tenant.
            resource_id: Offering/class/user id as text.
            student_id: Optional roster student id.
            ip: Client IP when known.
            detail: Small JSON payload (no secrets).
        """
        tid = int(tenant_id or self.default_tenant_id())
        blob = json.dumps(detail or {}, default=str)
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO access_audit_log (
                    tenant_id, actor_user_id, actor_role, action,
                    resource_type, resource_id, student_id, ip,
                    detail_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tid,
                    int(actor_user_id) if actor_user_id is not None else None,
                    (actor_role or "unknown")[:32],
                    (action or "unknown")[:80],
                    (resource_type or "unknown")[:80],
                    None if resource_id is None else str(resource_id)[:80],
                    int(student_id) if student_id is not None else None,
                    (ip or "")[:120],
                    blob,
                    _now(),
                ),
            )
            self.conn.commit()

    def list_access_events(
        self,
        *,
        tenant_id: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return recent audit rows for one tenant (newest first).

        Args:
            tenant_id: Defaults to the ELC tenant.
            limit: Max rows (capped at 2000).
        """
        tid = int(tenant_id or self.default_tenant_id())
        cap = max(1, min(int(limit), 2000))
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT a.*, u.email AS actor_email
                FROM access_audit_log a
                LEFT JOIN users u ON u.id = a.actor_user_id
                WHERE a.tenant_id = ?
                ORDER BY a.id DESC
                LIMIT ?
                """,
                (tid, cap),
            ).fetchall()
        return [dict(row) for row in rows]

    def _ensure_offering_columns(self) -> None:
        """Add instance columns on live DBs created before this schema.

        ``CREATE TABLE IF NOT EXISTS`` will not ALTER existing Fly sqlite files.
        """
        cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(course_offerings)")
        }
        if "copied_from_offering_id" not in cols:
            self.conn.execute(
                "ALTER TABLE course_offerings ADD COLUMN copied_from_offering_id INTEGER"
            )
        if "instance_relpath" not in cols:
            self.conn.execute(
                "ALTER TABLE course_offerings ADD COLUMN instance_relpath TEXT"
            )
        if "library_id" not in cols:
            self.conn.execute(
                "ALTER TABLE course_offerings ADD COLUMN library_id INTEGER"
            )
        if "section_index" not in cols:
            self.conn.execute(
                "ALTER TABLE course_offerings "
                "ADD COLUMN section_index INTEGER NOT NULL DEFAULT 1"
            )
        if "ap_round_profiles_json" not in cols:
            self.conn.execute(
                "ALTER TABLE course_offerings ADD COLUMN ap_round_profiles_json TEXT"
            )
        if "slides_template_id" not in cols:
            self.conn.execute(
                "ALTER TABLE course_offerings ADD COLUMN slides_template_id TEXT"
            )
        if "style_guide_file_id" not in cols:
            self.conn.execute(
                "ALTER TABLE course_offerings ADD COLUMN style_guide_file_id TEXT"
            )

    def _legacy_offering_unique_index(self) -> str | None:
        """Return the pre-section UNIQUE index name on ``course_offerings``.

        Databases created before section support carry a table-level
        ``UNIQUE(semester_id, ontario_code, teacher_user_id)``, which blocks a
        second section for the same teacher. SQLite cannot drop a table
        constraint in place, so the caller rebuilds the table.

        Returns:
            The auto-index name, or None when the schema already allows sections.
        """
        wanted = {"semester_id", "ontario_code", "teacher_user_id"}
        for index in self.conn.execute("PRAGMA index_list(course_offerings)"):
            if not int(index["unique"]) or str(index["origin"]) != "u":
                continue
            name = str(index["name"]).replace('"', '""')
            cols = {
                str(row["name"])
                for row in self.conn.execute(f'PRAGMA index_info("{name}")')
            }
            if cols == wanted:
                return str(index["name"])
        return None

    def _ensure_offering_sections(self) -> None:
        """Backfill ``section_index`` and widen the offering uniqueness key.

        Every pre-existing row becomes section 1 (the old constraint made more
        than one impossible), so live Fly databases migrate without changing a
        single displayed course label.
        """
        self.conn.execute(
            "UPDATE course_offerings SET section_index = 1 WHERE section_index IS NULL"
        )
        if self._legacy_offering_unique_index() is None:
            return
        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys = OFF")
        try:
            self.conn.executescript(
                """
                BEGIN;
                CREATE TABLE course_offerings_sections (
                    id INTEGER PRIMARY KEY,
                    semester_id INTEGER NOT NULL REFERENCES semesters(id),
                    ontario_code TEXT NOT NULL REFERENCES ontario_courses(code),
                    teacher_user_id INTEGER NOT NULL REFERENCES users(id),
                    live_access_code TEXT NOT NULL,
                    imscc_path TEXT,
                    expectations_status TEXT NOT NULL DEFAULT 'unverified',
                    student_options_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    copied_from_offering_id INTEGER,
                    instance_relpath TEXT,
                    library_id INTEGER,
                    section_index INTEGER NOT NULL DEFAULT 1,
                    ap_round_profiles_json TEXT,
                    UNIQUE(semester_id, ontario_code, teacher_user_id, section_index)
                );
                INSERT INTO course_offerings_sections (
                    id, semester_id, ontario_code, teacher_user_id,
                    live_access_code, imscc_path, expectations_status,
                    student_options_json, created_at, copied_from_offering_id,
                    instance_relpath, library_id, section_index,
                    ap_round_profiles_json
                )
                SELECT id, semester_id, ontario_code, teacher_user_id,
                       live_access_code, imscc_path, expectations_status,
                       student_options_json, created_at, copied_from_offering_id,
                       instance_relpath, library_id, COALESCE(section_index, 1),
                       ap_round_profiles_json
                FROM course_offerings;
                DROP TABLE course_offerings;
                ALTER TABLE course_offerings_sections RENAME TO course_offerings;
                CREATE INDEX IF NOT EXISTS idx_live_access_code
                    ON course_offerings(live_access_code);
                COMMIT;
                """
            )
        finally:
            self.conn.execute("PRAGMA foreign_keys = ON")

    def _ensure_library_schema(self) -> None:
        """Create shared-library and normalized component tables."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS content_libraries (
                id INTEGER PRIMARY KEY,
                ontario_code TEXT NOT NULL,
                origin TEXT NOT NULL,
                source_path TEXT,
                source_sha256 TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_content_libraries_code
                ON content_libraries(ontario_code);

            CREATE TABLE IF NOT EXISTS blobs (
                sha256 TEXT PRIMARY KEY,
                bytes INTEGER NOT NULL,
                mime TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS library_files (
                id INTEGER PRIMARY KEY,
                library_id INTEGER NOT NULL
                    REFERENCES content_libraries(id) ON DELETE CASCADE,
                relpath TEXT NOT NULL,
                blob_sha TEXT NOT NULL REFERENCES blobs(sha256),
                created_at TEXT NOT NULL,
                UNIQUE(library_id, relpath)
            );
            CREATE INDEX IF NOT EXISTS idx_library_files_library
                ON library_files(library_id);

            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY,
                library_id INTEGER NOT NULL
                    REFERENCES content_libraries(id) ON DELETE CASCADE,
                import_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                html_text TEXT,
                blob_sha TEXT REFERENCES blobs(sha256),
                url TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(library_id, import_key)
            );

            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY,
                library_id INTEGER NOT NULL
                    REFERENCES content_libraries(id) ON DELETE CASCADE,
                import_key TEXT NOT NULL,
                title TEXT NOT NULL,
                body_html TEXT,
                blob_sha TEXT REFERENCES blobs(sha256),
                points REAL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(library_id, import_key)
            );

            CREATE TABLE IF NOT EXISTS quizzes (
                id INTEGER PRIMARY KEY,
                library_id INTEGER NOT NULL
                    REFERENCES content_libraries(id) ON DELETE CASCADE,
                import_key TEXT NOT NULL,
                title TEXT NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                qti_blob_sha TEXT REFERENCES blobs(sha256),
                created_at TEXT NOT NULL,
                UNIQUE(library_id, import_key)
            );

            CREATE TABLE IF NOT EXISTS question_banks (
                id INTEGER PRIMARY KEY,
                library_id INTEGER NOT NULL
                    REFERENCES content_libraries(id) ON DELETE CASCADE,
                import_key TEXT NOT NULL,
                title TEXT NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(library_id, import_key)
            );

            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY,
                bank_id INTEGER NOT NULL
                    REFERENCES question_banks(id) ON DELETE CASCADE,
                import_key TEXT NOT NULL,
                item_type TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(bank_id, import_key)
            );

            CREATE TABLE IF NOT EXISTS module_outlines (
                id INTEGER PRIMARY KEY,
                library_id INTEGER NOT NULL
                    REFERENCES content_libraries(id) ON DELETE CASCADE,
                import_key TEXT NOT NULL,
                title TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(library_id, import_key)
            );

            CREATE TABLE IF NOT EXISTS module_items (
                id INTEGER PRIMARY KEY,
                outline_id INTEGER NOT NULL
                    REFERENCES module_outlines(id) ON DELETE CASCADE,
                import_key TEXT NOT NULL,
                title TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                component_type TEXT NOT NULL,
                component_id INTEGER,
                source_type TEXT NOT NULL DEFAULT '',
                source_href TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(outline_id, import_key)
            );

            CREATE INDEX IF NOT EXISTS idx_pages_library
                ON pages(library_id);
            CREATE INDEX IF NOT EXISTS idx_assignments_library
                ON assignments(library_id);
            CREATE INDEX IF NOT EXISTS idx_quizzes_library
                ON quizzes(library_id);
            CREATE INDEX IF NOT EXISTS idx_question_banks_library
                ON question_banks(library_id);
            CREATE INDEX IF NOT EXISTS idx_module_outlines_library
                ON module_outlines(library_id, position);
            CREATE INDEX IF NOT EXISTS idx_module_items_outline
                ON module_items(outline_id, position);
            """
        )

    def _ensure_archived_column(self) -> None:
        """Add users.archived_at if the column does not yet exist (live migration).

        Uses the same pattern as ``_ensure_offering_columns`` so that existing
        Fly sqlite files are altered without recreating the table.
        """
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(users)")}
        if "archived_at" not in cols:
            self.conn.execute("ALTER TABLE users ADD COLUMN archived_at TEXT")
        if "verification_sent_at" not in cols:
            self.conn.execute("ALTER TABLE users ADD COLUMN verification_sent_at TEXT")

    def _ensure_gradebook_schema(self) -> None:
        """Create per-class grade category weight storage (editable later).

        Seeds are applied lazily in ``grade_weights_for_class`` so empty classes
        still get Att & Participation 10% / Term 65% / Exam 25% defaults.
        """
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS grade_category_weights (
                class_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                weight_pct REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (class_id, category)
            );
            CREATE TABLE IF NOT EXISTS school_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS module_reflection_flags (
                class_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                module_number INTEGER NOT NULL,
                complete INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (class_id, student_id, module_number)
            );
            CREATE TABLE IF NOT EXISTS grade_module_rules (
                class_id INTEGER NOT NULL,
                module_number INTEGER NOT NULL,
                min_sessions INTEGER NOT NULL,
                min_r1 REAL NOT NULL,
                min_r3 REAL NOT NULL,
                require_reflections INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (class_id, module_number)
            );
            """
        )

    def _ensure_offering_archived_column(self) -> None:
        """Add course_offerings.archived_at if absent (live migration)."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(course_offerings)")}
        if "archived_at" not in cols:
            self.conn.execute(
                "ALTER TABLE course_offerings ADD COLUMN archived_at TEXT"
            )

    def _ensure_offering_schedule_columns(self) -> None:
        """Add Admin-assigned live class days/time columns if absent.

        ``live_days`` stores the wizard preset (``M/W/F`` / ``T/Th/F``).
        ``live_time`` stores a ``TIME_OPTIONS`` label such as ``2:00pm``.
        """
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(course_offerings)")}
        if "live_days" not in cols:
            self.conn.execute(
                "ALTER TABLE course_offerings ADD COLUMN live_days TEXT"
            )
        if "live_time" not in cols:
            self.conn.execute(
                "ALTER TABLE course_offerings ADD COLUMN live_time TEXT"
            )

    def _ensure_live_session_schema(self) -> None:
        """Create live-class session + attendee + prompt tables on existing DBs.

        ``CREATE TABLE IF NOT EXISTS`` in ``SCHEMA`` covers new files; this
        helper re-runs the same DDL so older Fly sqlite volumes pick up the
        session tracking tables without a recreate. Identity columns (uuid,
        heartbeat, nullable roster link, guest flag) are applied by
        ``_ensure_live_session_identity_schema``.
        """
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_class_sessions (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                offering_id INTEGER NOT NULL,
                teacher_user_id INTEGER NOT NULL,
                session_code TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                mgs_session_id INTEGER,
                allow_unmatched_guests INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_live_session_code_active
                ON live_class_sessions(session_code)
                WHERE status = 'active';
            CREATE INDEX IF NOT EXISTS idx_live_sessions_class_status
                ON live_class_sessions(class_id, status);
            CREATE TABLE IF NOT EXISTS live_session_attendees (
                id INTEGER PRIMARY KEY,
                live_session_id INTEGER NOT NULL
                    REFERENCES live_class_sessions(id) ON DELETE CASCADE,
                student_id INTEGER,
                participant_uuid TEXT NOT NULL UNIQUE,
                visit_token TEXT UNIQUE,
                codename TEXT NOT NULL DEFAULT '',
                unmatched INTEGER NOT NULL DEFAULT 0,
                joined_at TEXT NOT NULL,
                left_at TEXT,
                last_heartbeat_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_live_session_attendees_session
                ON live_session_attendees(live_session_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_live_session_attendees_roster
                ON live_session_attendees(live_session_id, student_id)
                WHERE student_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS live_session_prompts (
                id INTEGER PRIMARY KEY,
                live_session_id INTEGER NOT NULL
                    REFERENCES live_class_sessions(id) ON DELETE CASCADE,
                slide_index INTEGER NOT NULL DEFAULT 0,
                kind TEXT NOT NULL DEFAULT 'idle',
                payload TEXT NOT NULL DEFAULT '{}',
                active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_live_session_prompts_session
                ON live_session_prompts(live_session_id, active);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_live_session_prompts_slide
                ON live_session_prompts(live_session_id, slide_index);
            CREATE TABLE IF NOT EXISTS live_session_responses (
                id INTEGER PRIMARY KEY,
                prompt_id INTEGER NOT NULL
                    REFERENCES live_session_prompts(id) ON DELETE CASCADE,
                student_id INTEGER,
                participant_uuid TEXT,
                response_json TEXT NOT NULL DEFAULT '{}',
                awarded_points REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(prompt_id, student_id)
            );
            CREATE INDEX IF NOT EXISTS idx_live_session_responses_prompt
                ON live_session_responses(prompt_id);
            """
        )

    def _ensure_live_session_identity_schema(self) -> None:
        """Add participant uuid, stable token, heartbeat, and guest flag.

        Rebuilds ``live_session_attendees`` when ``student_id`` is still
        NOT NULL so unmatched guests can join without a roster row.
        """
        sess_cols = {
            str(row[1])
            for row in self.conn.execute(
                "PRAGMA table_info(live_class_sessions)"
            ).fetchall()
        }
        if "allow_unmatched_guests" not in sess_cols:
            self.conn.execute(
                """
                ALTER TABLE live_class_sessions
                ADD COLUMN allow_unmatched_guests INTEGER NOT NULL DEFAULT 0
                """
            )

        att_info = list(
            self.conn.execute("PRAGMA table_info(live_session_attendees)").fetchall()
        )
        att_cols = {str(row[1]): row for row in att_info}
        student_notnull = bool(
            att_cols.get("student_id") is not None and int(att_cols["student_id"][3])
        )
        needs_rebuild = student_notnull or "participant_uuid" not in att_cols
        if needs_rebuild and att_cols:
            self._rebuild_live_session_attendees_identity(att_cols)
        elif "participant_uuid" in att_cols:
            self.conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_live_session_attendees_roster
                ON live_session_attendees(live_session_id, student_id)
                WHERE student_id IS NOT NULL
                """
            )
            self.conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                idx_live_session_attendees_visit_token
                ON live_session_attendees(visit_token)
                WHERE visit_token IS NOT NULL
                """
            )

        resp_info = list(
            self.conn.execute("PRAGMA table_info(live_session_responses)").fetchall()
        )
        resp_cols = {str(row[1]): row for row in resp_info}
        resp_student_notnull = bool(
            resp_cols.get("student_id") is not None and int(resp_cols["student_id"][3])
        )
        if "participant_uuid" not in resp_cols:
            self.conn.execute(
                "ALTER TABLE live_session_responses ADD COLUMN participant_uuid TEXT"
            )
            resp_cols["participant_uuid"] = True
        if resp_student_notnull:
            self._rebuild_live_session_responses_identity()
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_live_responses_prompt_uuid
            ON live_session_responses(prompt_id, participant_uuid)
            WHERE participant_uuid IS NOT NULL AND TRIM(participant_uuid) != ''
            """
        )
        self.conn.commit()

    def _rebuild_live_session_responses_identity(self) -> None:
        """Recreate prompt responses with a nullable roster ``student_id``.

        Existing rows keep their ids and JSON; ``participant_uuid`` is copied
        when present so live-session identity survives the rebuild.
        """
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.conn.executescript(
            """
            CREATE TABLE live_session_responses_identity (
                id INTEGER PRIMARY KEY,
                prompt_id INTEGER NOT NULL
                    REFERENCES live_session_prompts(id) ON DELETE CASCADE,
                student_id INTEGER,
                participant_uuid TEXT,
                response_json TEXT NOT NULL DEFAULT '{}',
                awarded_points REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        self.conn.execute(
            """
            INSERT INTO live_session_responses_identity (
                id, prompt_id, student_id, participant_uuid, response_json,
                awarded_points, created_at, updated_at
            )
            SELECT id, prompt_id, student_id, participant_uuid, response_json,
                   awarded_points, created_at, updated_at
            FROM live_session_responses
            """
        )
        self.conn.execute("DROP TABLE live_session_responses")
        self.conn.execute(
            "ALTER TABLE live_session_responses_identity RENAME TO live_session_responses"
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_live_session_responses_prompt
            ON live_session_responses(prompt_id)
            """
        )
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_live_responses_prompt_student
            ON live_session_responses(prompt_id, student_id)
            WHERE student_id IS NOT NULL
            """
        )
        self.conn.execute("PRAGMA foreign_keys = ON")

    def _rebuild_live_session_attendees_identity(
        self, att_cols: dict[str, Any]
    ) -> None:
        """Recreate attendees with uuid, heartbeat, and a nullable roster link.

        Args:
            att_cols: ``PRAGMA table_info`` rows keyed by column name.
        """
        has_uuid = "participant_uuid" in att_cols
        has_unmatched = "unmatched" in att_cols
        has_heartbeat = "last_heartbeat_at" in att_cols
        has_token = "visit_token" in att_cols
        select_uuid = (
            "participant_uuid" if has_uuid else "NULL"
        )
        select_unmatched = "unmatched" if has_unmatched else "0"
        select_heartbeat = (
            "last_heartbeat_at" if has_heartbeat else "joined_at"
        )
        select_token = "visit_token" if has_token else "NULL"
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.conn.executescript(
            """
            CREATE TABLE live_session_attendees_identity (
                id INTEGER PRIMARY KEY,
                live_session_id INTEGER NOT NULL
                    REFERENCES live_class_sessions(id) ON DELETE CASCADE,
                student_id INTEGER,
                participant_uuid TEXT NOT NULL UNIQUE,
                visit_token TEXT UNIQUE,
                codename TEXT NOT NULL DEFAULT '',
                unmatched INTEGER NOT NULL DEFAULT 0,
                joined_at TEXT NOT NULL,
                left_at TEXT,
                last_heartbeat_at TEXT
            );
            """
        )
        rows = self.conn.execute(
            f"""
            SELECT id, live_session_id, student_id, {select_uuid} AS participant_uuid,
                   {select_token} AS visit_token, codename, {select_unmatched} AS unmatched,
                   joined_at, left_at, {select_heartbeat} AS last_heartbeat_at
            FROM live_session_attendees
            """
        ).fetchall()
        for row in rows:
            item = dict(row)
            pid = str(item.get("participant_uuid") or "").strip() or str(uuid.uuid4())
            token = str(item.get("visit_token") or "").strip() or secrets.token_urlsafe(24)
            sid = item.get("student_id")
            self.conn.execute(
                """
                INSERT INTO live_session_attendees_identity (
                    id, live_session_id, student_id, participant_uuid, visit_token,
                    codename, unmatched, joined_at, left_at, last_heartbeat_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(item["id"]),
                    int(item["live_session_id"]),
                    int(sid) if sid not in (None, "") else None,
                    pid,
                    token,
                    str(item.get("codename") or ""),
                    int(item.get("unmatched") or 0),
                    str(item.get("joined_at") or _now()),
                    item.get("left_at"),
                    item.get("last_heartbeat_at") or item.get("joined_at"),
                ),
            )
        self.conn.execute("DROP TABLE live_session_attendees")
        self.conn.execute(
            "ALTER TABLE live_session_attendees_identity RENAME TO live_session_attendees"
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_live_session_attendees_session
            ON live_session_attendees(live_session_id)
            """
        )
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_live_session_attendees_roster
            ON live_session_attendees(live_session_id, student_id)
            WHERE student_id IS NOT NULL
            """
        )
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_live_session_attendees_visit_token
            ON live_session_attendees(visit_token)
            WHERE visit_token IS NOT NULL
            """
        )
        self.conn.execute("PRAGMA foreign_keys = ON")

    def _ensure_live_class_feature_schema(self) -> None:
        """Add slides columns, Google API tokens, problem bank, and evidence tables.

        Live-migrates existing Fly sqlite files. Also adds ``meeting_date`` and
        presentation fields on ``live_class_sessions``.
        """
        self.conn.executescript(SCHEMA)
        cols = {
            str(row[1])
            for row in self.conn.execute("PRAGMA table_info(live_class_sessions)")
        }
        if "meeting_date" not in cols:
            self.conn.execute(
                "ALTER TABLE live_class_sessions ADD COLUMN meeting_date TEXT"
            )
        if "presentation_id" not in cols:
            self.conn.execute(
                "ALTER TABLE live_class_sessions ADD COLUMN presentation_id TEXT"
            )
        if "presentation_url" not in cols:
            self.conn.execute(
                "ALTER TABLE live_class_sessions ADD COLUMN presentation_url TEXT"
            )
        if "slides_json" not in cols:
            self.conn.execute(
                "ALTER TABLE live_class_sessions ADD COLUMN slides_json TEXT"
            )
        if "active_media_json" not in cols:
            self.conn.execute(
                "ALTER TABLE live_class_sessions ADD COLUMN active_media_json TEXT"
            )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_live_sessions_class_date
            ON live_class_sessions(class_id, meeting_date)
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lesson_slide_decks (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                module_number INTEGER NOT NULL,
                live_index INTEGER NOT NULL,
                presentation_id TEXT,
                presentation_url TEXT,
                preview_json TEXT,
                fill_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(class_id, module_number, live_index)
            )
            """
        )
        obs_cols = {
            str(row[1])
            for row in self.conn.execute("PRAGMA table_info(observations)").fetchall()
        }
        if "lookfor_key" not in obs_cols:
            self.conn.execute("ALTER TABLE observations ADD COLUMN lookfor_key TEXT")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_mark_suggestions (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                ontario_code TEXT NOT NULL,
                module_number INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                suggested_json TEXT NOT NULL DEFAULT '{}',
                override_json TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(class_id, module_number, student_id)
            )
            """
        )
        self.conn.commit()

    def _seed_live_class_features(self) -> None:
        """Seed Ontario processes, default phrases, live bank, and Drive IDs."""
        self.seed_math_processes()
        self.seed_quick_phrases()
        self.seed_live_problems()
        self._seed_slides_drive_defaults()

    def _seed_slides_drive_defaults(self) -> None:
        """Store ALC folder and Lesson Theme Template #1 when unset.

        Does not overwrite IDs IT already saved.
        """
        try:
            from live_class_constants import (
                ALC_DRIVE_FOLDER_ID,
                DEFAULT_SLIDES_TEMPLATE_ID,
                SETTING_ALC_DRIVE_FOLDER_ID,
                SETTING_DEFAULT_SLIDES_TEMPLATE_ID,
            )
        except ImportError:
            from lms.live_class_constants import (
                ALC_DRIVE_FOLDER_ID,
                DEFAULT_SLIDES_TEMPLATE_ID,
                SETTING_ALC_DRIVE_FOLDER_ID,
                SETTING_DEFAULT_SLIDES_TEMPLATE_ID,
            )
        defaults = (
            (SETTING_ALC_DRIVE_FOLDER_ID, ALC_DRIVE_FOLDER_ID),
            (SETTING_DEFAULT_SLIDES_TEMPLATE_ID, DEFAULT_SLIDES_TEMPLATE_ID),
        )
        with self._lock:
            for key, value in defaults:
                row = self.conn.execute(
                    "SELECT value FROM school_settings WHERE key = ?",
                    (key,),
                ).fetchone()
                if row is not None and str(row["value"] or "").strip():
                    continue
                self.conn.execute(
                    """
                    INSERT INTO school_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (key, value, _now()),
                )
            self.conn.commit()

    def seed_math_processes(self) -> None:
        """Upsert the seven Ontario mathematical processes from the MCF3M seed."""
        try:
            from live_class_constants import process_key_from_name
            from paths import MCF3M_EXPECTATIONS
        except ImportError:
            from lms.live_class_constants import process_key_from_name
            from lms.paths import MCF3M_EXPECTATIONS
        if not MCF3M_EXPECTATIONS.is_file():
            return
        payload = json.loads(MCF3M_EXPECTATIONS.read_text(encoding="utf-8"))
        processes = (payload.get("mathematical_processes") or {}).get("processes") or []
        with self._lock:
            for index, proc in enumerate(processes, start=1):
                key = process_key_from_name(str(proc.get("name") or ""))
                if not key:
                    continue
                self.conn.execute(
                    """
                    INSERT INTO math_processes (process_key, name, statement, sort_order)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(process_key) DO UPDATE SET
                        name = excluded.name,
                        statement = excluded.statement,
                        sort_order = excluded.sort_order
                    """,
                    (
                        key,
                        str(proc.get("name") or key),
                        str(proc.get("statement") or ""),
                        index,
                    ),
                )
            self.conn.commit()

    def seed_quick_phrases(self) -> None:
        """Insert default evidence phrases when the table is empty."""
        try:
            from live_problem_seed import default_quick_phrases
        except ImportError:
            from lms.live_problem_seed import default_quick_phrases
        with self._lock:
            count = self.conn.execute(
                "SELECT COUNT(*) AS n FROM quick_phrases"
            ).fetchone()["n"]
            if int(count) > 0:
                return
            for row in default_quick_phrases():
                self.conn.execute(
                    """
                    INSERT INTO quick_phrases (
                        process_key, label, description, category, active, sort_order
                    ) VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (
                        row["process_key"],
                        row["label"],
                        row["description"],
                        row["category"],
                        int(row["sort_order"]),
                    ),
                )
            self.conn.commit()

    def seed_live_problems(self) -> None:
        """Insert or refresh the starter MCF3M live-problem bank.

        Upserts by (``ontario_code``, ``kind``, ``title``) so existing databases
        pick up new lesson-keyed rows (e.g. M1C1) without wiping teacher-added
        items that use other titles.
        """
        try:
            from live_problem_seed import default_live_problems
        except ImportError:
            from lms.live_problem_seed import default_live_problems
        for row in default_live_problems():
            payload = dict(row)
            payload.pop("staff_note", None)
            existing_id = self._live_problem_id_by_natural_key(
                str(payload.get("ontario_code") or "MCF3M"),
                str(payload.get("kind") or "standard"),
                str(payload.get("title") or ""),
            )
            if existing_id is not None:
                payload["id"] = existing_id
            self.upsert_live_problem(payload)

    def _live_problem_id_by_natural_key(
        self, ontario_code: str, kind: str, title: str
    ) -> int | None:
        """Return the id of a live problem matching course, kind, and title.

        Args:
            ontario_code: Course code.
            kind: ``warmup``, ``contest``, or ``standard``.
            title: Exact title string.

        Returns:
            Primary key, or ``None`` if no row matches.
        """
        code = str(ontario_code or "MCF3M").strip().upper()
        kind_key = str(kind or "standard").strip().lower()
        title_key = str(title or "").strip()
        if not title_key:
            return None
        with self._lock:
            row = self.conn.execute(
                """
                SELECT id FROM live_problems
                WHERE ontario_code = ? AND kind = ? AND title = ?
                ORDER BY id LIMIT 1
                """,
                (code, kind_key, title_key),
            ).fetchone()
        return int(row["id"]) if row else None

    def _ensure_access_request_schema(self) -> None:
        """Create the public signup-request table on existing sqlite files.

        Requests are a queue for IT. Submitting one never creates a user,
        tenant, or allowlist row.
        """
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS access_requests (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                role TEXT NOT NULL,
                organization TEXT NOT NULL DEFAULT '',
                context TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by_user_id INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_access_requests_email
                ON access_requests(email);
            CREATE INDEX IF NOT EXISTS idx_access_requests_status
                ON access_requests(status);
            """
        )

    def _seed(self) -> None:
        """Insert IT user, curriculum catalog, MCF3M expectations, default semester."""
        with self._lock:
            row = self.conn.execute(
                "SELECT id FROM users WHERE email = ?", (self.it_email,)
            ).fetchone()
            if row is None:
                tenant_id = self.default_tenant_id()
                self.conn.execute(
                    """
                    INSERT INTO users (
                        email, display_name, role, tenant_id, created_at
                    )
                    VALUES (?, ?, 'it', ?, ?)
                    """,
                    (self.it_email, "Shawn", tenant_id, _now()),
                )
            if SEMESTER_JSON.is_file() and self.conn.execute(
                "SELECT COUNT(*) AS n FROM semesters"
            ).fetchone()["n"] == 0:
                self.activate_from_semester_json(SEMESTER_JSON, make_active=True)

    def activate_from_semester_json(
        self, path: Path | None = None, *, make_active: bool = True
    ) -> dict[str, Any]:
        """Insert or refresh a semester row from ``frameworks/semester.json``.

        Args:
            path: Path to semester JSON (default ``frameworks/semester.json``).
            make_active: If True, this semester becomes the inherited active one.
        """
        target = path or SEMESTER_JSON
        payload = json.loads(target.read_text(encoding="utf-8"))
        label = str(payload.get("semester") or "2026-2027 S1")
        year_display, term = parse_semester_label(label)
        source_pdf = str((payload.get("calendar") or {}).get("local_pdf") or "")
        blob = json.dumps(payload)
        with self._lock:
            existing = self.conn.execute(
                "SELECT id FROM semesters WHERE label = ?", (label,)
            ).fetchone()
            if existing:
                self.conn.execute(
                    """
                    UPDATE semesters
                    SET year_display = ?, term = ?, source_pdf = ?, payload_json = ?
                    WHERE id = ?
                    """,
                    (year_display, term, source_pdf, blob, int(existing["id"])),
                )
                semester_id = int(existing["id"])
            else:
                cur = self.conn.execute(
                    """
                    INSERT INTO semesters
                        (label, year_display, term, source_pdf, is_active,
                         payload_json, created_at)
                    VALUES (?, ?, ?, ?, 0, ?, ?)
                    """,
                    (label, year_display, term, source_pdf, blob, _now()),
                )
                semester_id = int(cur.lastrowid)
            if make_active:
                self.conn.execute("UPDATE semesters SET is_active = 0")
                self.conn.execute(
                    "UPDATE semesters SET is_active = 1 WHERE id = ?", (semester_id,)
                )
            self.conn.commit()
        return self.get_semester(semester_id)

    def set_active_semester(self, semester_id: int) -> dict[str, Any]:
        """Mark one semester as the inherited active term.

        Args:
            semester_id: Semesters primary key.
        """
        self.get_semester(semester_id)
        with self._lock:
            self.conn.execute("UPDATE semesters SET is_active = 0")
            self.conn.execute(
                "UPDATE semesters SET is_active = 1 WHERE id = ?", (semester_id,)
            )
            self.conn.commit()
        return self.get_semester(semester_id)

    def list_semesters(self) -> list[dict[str, Any]]:
        """Return all semesters, active first."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM semesters ORDER BY is_active DESC, id DESC"
            ).fetchall()
        return [self._semester_dict(row) for row in rows]

    def active_semester(self) -> dict[str, Any] | None:
        """Return the inherited active semester, if any."""
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM semesters WHERE is_active = 1 LIMIT 1"
            ).fetchone()
        return self._semester_dict(row) if row else None

    def get_semester(self, semester_id: int) -> dict[str, Any]:
        """Return one semester or raise KeyError."""
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM semesters WHERE id = ?", (semester_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"semester {semester_id}")
        return self._semester_dict(row)

    def _semester_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """Attach parsed payload and IT-dashboard fields to a semester row."""
        payload = json.loads(row["payload_json"])
        data = dict(row)
        instructional = payload.get("instructional") or {}
        exam = payload.get("exam_window") or {}
        data["payload"] = payload
        data["raw_json"] = payload
        data["instructional_first"] = instructional.get("first_day_of_school")
        data["instructional_last"] = instructional.get("last_instructional_day_before_exams")
        data["exam_window_json"] = json.dumps(exam.get("secondary_exam_days") or [])
        data["pd_days_json"] = json.dumps(payload.get("pd_days") or [])
        data["holidays_json"] = json.dumps(payload.get("holidays") or [])
        return data

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        """Return a user by id."""
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        """Alias used by auth after Google / 2SV."""
        return self.get_user(user_id)

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        """Return a user by email (case-insensitive)."""
        key = (email or "").strip().lower()
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM users WHERE lower(email) = ?", (key,)
            ).fetchone()
        return dict(row) if row else None

    def get_user_by_google_sub(self, sub: str) -> dict[str, Any] | None:
        """Return a user by Google subject identifier."""
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM users WHERE google_sub = ?", (sub,)
            ).fetchone()
        return dict(row) if row else None

    def register_staff(
        self,
        email: str,
        display_name: str = "",
        *,
        tenant_id: int | None = None,
    ) -> dict[str, Any]:
        """Add a staff Google email to the allowlist.

        Args:
            email: Personal Google account.
            display_name: Optional label.
            tenant_id: School seam id; defaults to the ELC tenant.

        Returns:
            User dict.

        Raises:
            ValueError: If the email is empty or already IT-only conflict.
        """
        key = (email or "").strip().lower()
        if "@" not in key:
            raise ValueError("Enter a Google email address")
        existing = self.get_user_by_email(key)
        if existing:
            return existing
        tid = int(tenant_id or self.default_tenant_id())
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT INTO users (email, display_name, role, tenant_id, created_at)
                VALUES (?, ?, 'staff', ?, ?)
                """,
                (key, (display_name or key.split("@")[0]).strip(), tid, _now()),
            )
            self.conn.commit()
            user_id = int(cur.lastrowid)
        return self.get_user(user_id) or {}

    ACCESS_REQUEST_ROLES = frozenset(
        {"parent", "teacher", "school_admin", "other"}
    )

    def create_access_request(
        self,
        *,
        name: str,
        email: str,
        role: str,
        organization: str = "",
        context: str = "",
    ) -> dict[str, Any]:
        """Store a public signup request without creating a user.

        A second submit from the same email while still pending updates the
        existing row so IT is not flooded with duplicates.

        Args:
            name: Contact name.
            email: Contact email (not auto-allowlisted).
            role: One of ``ACCESS_REQUEST_ROLES``.
            organization: School or family name.
            context: Why they want access.

        Returns:
            The access_requests row.

        Raises:
            ValueError: If required fields are missing or the role is unknown.
        """
        label = (name or "").strip()
        key = (email or "").strip().lower()
        kind = (role or "").strip().lower()
        org = (organization or "").strip()
        note = (context or "").strip()
        if not label:
            raise ValueError("Enter your name")
        if "@" not in key:
            raise ValueError("Enter a contact email")
        if kind not in self.ACCESS_REQUEST_ROLES:
            raise ValueError("Choose how you would use ALC")
        if not note:
            raise ValueError("Tell us a little about your school or family")
        now = _now()
        with self._lock:
            pending = self.conn.execute(
                """
                SELECT * FROM access_requests
                WHERE lower(email) = ? AND status = 'pending'
                ORDER BY id DESC LIMIT 1
                """,
                (key,),
            ).fetchone()
            if pending:
                self.conn.execute(
                    """
                    UPDATE access_requests
                    SET name = ?, role = ?, organization = ?, context = ?,
                        created_at = ?
                    WHERE id = ?
                    """,
                    (label, kind, org, note, now, int(pending["id"])),
                )
                self.conn.commit()
                row = self.conn.execute(
                    "SELECT * FROM access_requests WHERE id = ?",
                    (int(pending["id"]),),
                ).fetchone()
            else:
                cur = self.conn.execute(
                    """
                    INSERT INTO access_requests (
                        name, email, role, organization, context, status,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (label, key, kind, org, note, now),
                )
                self.conn.commit()
                row = self.conn.execute(
                    "SELECT * FROM access_requests WHERE id = ?",
                    (int(cur.lastrowid),),
                ).fetchone()
        return dict(row) if row else {}

    def list_access_requests(
        self, *, status: str | None = "pending"
    ) -> list[dict[str, Any]]:
        """Return access requests, newest first.

        Args:
            status: Filter by status, or ``None`` for every row.
        """
        with self._lock:
            if status:
                rows = self.conn.execute(
                    """
                    SELECT * FROM access_requests
                    WHERE status = ?
                    ORDER BY id DESC
                    """,
                    (status,),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM access_requests ORDER BY id DESC"
                ).fetchall()
        return [dict(row) for row in rows]

    def mark_access_request_reviewed(
        self, request_id: int, by_user_id: int
    ) -> dict[str, Any] | None:
        """Mark a request seen. Does not allowlist or provision anyone.

        Args:
            request_id: ``access_requests.id``.
            by_user_id: IT user who reviewed it.
        """
        with self._lock:
            self.conn.execute(
                """
                UPDATE access_requests
                SET status = 'reviewed',
                    reviewed_at = ?,
                    reviewed_by_user_id = ?
                WHERE id = ?
                """,
                (_now(), int(by_user_id), int(request_id)),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT * FROM access_requests WHERE id = ?",
                (int(request_id),),
            ).fetchone()
        return dict(row) if row else None

    def list_staff(
        self,
        *,
        include_archived: bool = False,
        tenant_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return IT and staff users ordered by role then email.

        Args:
            include_archived: When False (default), users whose ``archived_at``
                is non-NULL are excluded.
            tenant_id: Restrict to one school seam. ``None`` means the ELC
                default tenant (not every tenant).

        Returns:
            List of user dicts. Each dict contains all ``users`` columns.
        """
        tid = int(tenant_id or self.default_tenant_id())
        with self._lock:
            if include_archived:
                rows = self.conn.execute(
                    """
                    SELECT * FROM users WHERE tenant_id = ?
                    ORDER BY role, email
                    """,
                    (tid,),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    """
                    SELECT * FROM users
                    WHERE tenant_id = ? AND archived_at IS NULL
                    ORDER BY role, email
                    """,
                    (tid,),
                ).fetchall()
        return [dict(row) for row in rows]

    def deactivate_staff(self, user_id: int, by_user_id: int) -> dict[str, Any]:
        """Soft-deactivate a staff user by setting archived_at.

        Args:
            user_id: Primary key of the user to deactivate.
            by_user_id: Primary key of the requesting user (cannot equal user_id).

        Returns:
            Updated user dict.

        Raises:
            ValueError: If user_id == by_user_id (cannot self-deactivate), if the
                target user has the IT role (bootstrap IT is untouchable), or if
                the user does not exist.
        """
        if user_id == by_user_id:
            raise ValueError("You cannot deactivate your own account.")
        target = self.get_user(user_id)
        if target is None:
            raise ValueError(f"User {user_id} not found.")
        if target.get("role") == "it":
            raise ValueError("IT accounts cannot be deactivated.")
        with self._lock:
            self.conn.execute(
                "UPDATE users SET archived_at = ? WHERE id = ?",
                (_now(), user_id),
            )
            self.conn.commit()
        return self.get_user(user_id) or {}

    def reactivate_staff(self, user_id: int) -> dict[str, Any]:
        """Clear archived_at, restoring login access for a deactivated user.

        Args:
            user_id: Primary key of the user to reactivate.

        Returns:
            Updated user dict.

        Raises:
            ValueError: If the user does not exist.
        """
        if self.get_user(user_id) is None:
            raise ValueError(f"User {user_id} not found.")
        with self._lock:
            self.conn.execute(
                "UPDATE users SET archived_at = NULL WHERE id = ?",
                (user_id,),
            )
            self.conn.commit()
        return self.get_user(user_id) or {}

    def delete_staff_permanently(self, user_id: int, by_user_id: int) -> dict[str, Any]:
        """Hard-delete a staff user and their offerings / live-class data.

        Frees the email for re-registration. Shared content libraries are kept.
        Offering instance directories under the data volume are removed when present.

        Args:
            user_id: Primary key of the staff user to delete.
            by_user_id: Primary key of the requesting IT user (cannot equal user_id).

        Returns:
            Snapshot of the deleted user row (before delete).

        Raises:
            ValueError: Self-delete, IT target, missing user, or non-staff role.
        """
        if user_id == by_user_id:
            raise ValueError("You cannot permanently delete your own account.")
        target = self.get_user(user_id)
        if target is None:
            raise ValueError(f"User {user_id} not found.")
        if target.get("role") == "it":
            raise ValueError("IT accounts cannot be permanently deleted.")
        if target.get("role") != "staff":
            raise ValueError("Only staff accounts can be permanently deleted.")

        snapshot = dict(target)
        uid = int(user_id)
        data_dir = Path(getattr(self, "data_dir", self.db_path.parent))

        with self._lock:
            offering_rows = self.conn.execute(
                "SELECT id, instance_relpath FROM course_offerings WHERE teacher_user_id = ?",
                (uid,),
            ).fetchall()
            offering_ids = [int(row["id"]) for row in offering_rows]
            instance_paths = [
                str(row["instance_relpath"] or "").strip()
                for row in offering_rows
                if str(row["instance_relpath"] or "").strip()
            ]

            class_ids: list[int] = []
            has_classes = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='classes'"
            ).fetchone()
            if has_classes:
                if offering_ids:
                    placeholders = ",".join("?" * len(offering_ids))
                    class_rows = self.conn.execute(
                        f"""
                        SELECT id FROM classes
                        WHERE teacher_user_id = ?
                           OR offering_id IN ({placeholders})
                        """,
                        (uid, *offering_ids),
                    ).fetchall()
                else:
                    class_rows = self.conn.execute(
                        "SELECT id FROM classes WHERE teacher_user_id = ?",
                        (uid,),
                    ).fetchall()
                class_ids = [int(row["id"]) for row in class_rows]

            if offering_ids:
                placeholders = ",".join("?" * len(offering_ids))
                self.conn.execute(
                    f"""
                    DELETE FROM live_class_sessions
                    WHERE teacher_user_id = ?
                       OR offering_id IN ({placeholders})
                    """,
                    (uid, *offering_ids),
                )
            else:
                self.conn.execute(
                    "DELETE FROM live_class_sessions WHERE teacher_user_id = ?",
                    (uid,),
                )

            if class_ids:
                placeholders = ",".join("?" * len(class_ids))
                has_weights = self.conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='grade_category_weights'"
                ).fetchone()
                if has_weights:
                    self.conn.execute(
                        f"DELETE FROM grade_category_weights WHERE class_id IN ({placeholders})",
                        class_ids,
                    )
                self.conn.execute(
                    f"DELETE FROM classes WHERE id IN ({placeholders})",
                    class_ids,
                )
            elif has_classes:
                self.conn.execute(
                    "DELETE FROM classes WHERE teacher_user_id = ?",
                    (uid,),
                )

            if offering_ids:
                placeholders = ",".join("?" * len(offering_ids))
                self.conn.execute(
                    f"""
                    UPDATE course_offerings
                    SET copied_from_offering_id = NULL
                    WHERE copied_from_offering_id IN ({placeholders})
                    """,
                    offering_ids,
                )
                self.conn.execute(
                    f"DELETE FROM course_offerings WHERE id IN ({placeholders})",
                    offering_ids,
                )

            self.conn.execute("DELETE FROM users WHERE id = ?", (uid,))
            self.conn.commit()

        for rel in instance_paths:
            path = (data_dir / rel).resolve()
            try:
                path.relative_to(data_dir.resolve())
            except ValueError:
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)

        return snapshot

    def rename_staff(self, user_id: int, display_name: str) -> dict[str, Any]:
        """Update display_name for any non-IT user.

        Args:
            user_id: Primary key of the user to rename.
            display_name: New display name; must be non-blank after stripping.

        Returns:
            Updated user dict.

        Raises:
            ValueError: If display_name is blank after strip, or user not found.
        """
        name = (display_name or "").strip()
        if not name:
            raise ValueError("Display name cannot be blank.")
        if self.get_user(user_id) is None:
            raise ValueError(f"User {user_id} not found.")
        with self._lock:
            self.conn.execute(
                "UPDATE users SET display_name = ? WHERE id = ?",
                (name, user_id),
            )
            self.conn.commit()
        return self.get_user(user_id) or {}

    def link_google(
        self, user_id: int, google_sub: str, display_name: str | None = None
    ) -> dict[str, Any]:
        """Attach a Google subject id after OAuth.

        Args:
            user_id: Users primary key.
            google_sub: Google ``sub`` claim.
            display_name: Optional profile name.
        """
        with self._lock:
            if display_name:
                self.conn.execute(
                    "UPDATE users SET google_sub = ?, display_name = ? WHERE id = ?",
                    (google_sub, display_name, user_id),
                )
            else:
                self.conn.execute(
                    "UPDATE users SET google_sub = ? WHERE id = ?",
                    (google_sub, user_id),
                )
            self.conn.commit()
        return self.get_user(user_id) or {}

    def set_verification_code(self, user_id: int, code: str) -> None:
        """Store a first-login email code and when it was issued."""
        with self._lock:
            self.conn.execute(
                """
                UPDATE users
                SET verification_code = ?, verification_sent_at = ?
                WHERE id = ?
                """,
                (code, _now(), user_id),
            )
            self.conn.commit()

    def mark_verified(self, user_id: int) -> dict[str, Any]:
        """Clear the email code and stamp verified_at."""
        with self._lock:
            self.conn.execute(
                """
                UPDATE users
                SET verified_at = ?, verification_code = NULL, last_login_at = ?
                WHERE id = ?
                """,
                (_now(), _now(), user_id),
            )
            self.conn.commit()
        return self.get_user(user_id) or {}

    def record_login(self, user_id: int) -> None:
        """Stamp last_login_at."""
        with self._lock:
            self.conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (_now(), user_id),
            )
            self.conn.commit()

    def list_ontario_courses(self, q: str = "") -> list[dict[str, Any]]:
        """Search the course catalog by code or title."""
        needle = f"%{(q or '').strip().upper()}%"
        with self._lock:
            if (q or "").strip():
                rows = self.conn.execute(
                    """
                    SELECT * FROM ontario_courses
                    WHERE upper(code) LIKE ? OR upper(title) LIKE ?
                    ORDER BY grade, code
                    """,
                    (needle, needle),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM ontario_courses ORDER BY grade, code"
                ).fetchall()
        return [dict(row) for row in rows]

    def get_course(self, code: str) -> dict[str, Any] | None:
        """Return one catalog course."""
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM ontario_courses WHERE code = ?",
                ((code or "").strip().upper(),),
            ).fetchone()
        return dict(row) if row else None

    def expectations_for(self, code: str) -> list[dict[str, Any]]:
        """Return inherited overall/specific expectations for a course code."""
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM expectations
                WHERE course_code = ?
                ORDER BY kind DESC, code
                """,
                ((code or "").strip().upper(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _code_for_semester_course(self, semester_id: int, ontario_code: str) -> str | None:
        """Return the shared live-access code for this course this semester."""
        with self._lock:
            row = self.conn.execute(
                """
                SELECT live_access_code FROM course_offerings
                WHERE semester_id = ? AND ontario_code = ?
                LIMIT 1
                """,
                (semester_id, ontario_code),
            ).fetchone()
        return str(row["live_access_code"]) if row else None

    def next_section_index(
        self, semester_id: int, ontario_code: str, teacher_user_id: int
    ) -> int:
        """Return the section number a new offering for this teacher should take.

        Only non-archived offerings in this semester count toward the preferred
        next index. When an archived row still occupies that UNIQUE slot, the
        lowest unused positive index is chosen instead.

        Args:
            semester_id: Semester the offering belongs to.
            ontario_code: Catalog course code.
            teacher_user_id: Staff or IT user id.
        """
        code = (ontario_code or "").strip().upper()
        with self._lock:
            active = self.conn.execute(
                """
                SELECT MAX(COALESCE(section_index, 1)) AS top
                FROM course_offerings
                WHERE semester_id = ? AND ontario_code = ? AND teacher_user_id = ?
                  AND archived_at IS NULL
                """,
                (semester_id, code, teacher_user_id),
            ).fetchone()
            used_rows = self.conn.execute(
                """
                SELECT COALESCE(section_index, 1) AS section_index
                FROM course_offerings
                WHERE semester_id = ? AND ontario_code = ? AND teacher_user_id = ?
                """,
                (semester_id, code, teacher_user_id),
            ).fetchall()
        used = {int(row["section_index"]) for row in used_rows}
        top = int(active["top"] or 0) if active else 0
        candidate = max(top, 0) + 1
        if candidate not in used:
            return candidate
        index = 1
        while index in used:
            index += 1
        return index

    def assign_course(
        self,
        *,
        semester_id: int,
        ontario_code: str,
        teacher_user_id: int,
        new_section: bool = False,
    ) -> dict[str, Any]:
        """Assign an Ontario course to a teacher for a semester.

        Mints one 8-character live-access code per (semester, course), reused
        if another teacher is assigned the same course.

        Args:
            semester_id: Active or chosen semester.
            ontario_code: Catalog course code.
            teacher_user_id: Staff or IT user id.
            new_section: When True and the teacher already holds this code,
                create an additional section (``MCF3M-2``) instead of
                returning the existing offering.

        Returns:
            Offering dict.

        Raises:
            KeyError: If semester, course, or user is missing.
            ValueError: If the teacher is not registered.
        """
        code = (ontario_code or "").strip().upper()
        if self.get_course(code) is None:
            raise KeyError(f"course {code}")
        self.get_semester(semester_id)
        teacher = self.get_user(teacher_user_id)
        if teacher is None:
            raise KeyError(f"user {teacher_user_id}")
        existing = self.get_offering_for(semester_id, code, teacher_user_id)
        if existing and not new_section:
            return existing
        index = self.next_section_index(semester_id, code, teacher_user_id)
        shared = self._code_for_semester_course(semester_id, code)
        live_code = shared or generate_live_access_code()
        if shared is None:
            while self.get_offering_by_code(live_code):
                live_code = generate_live_access_code()
        expects = self.expectations_for(code)
        status = "verified" if expects else "unverified"
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT INTO course_offerings (
                    semester_id, ontario_code, teacher_user_id, tenant_id,
                    live_access_code, imscc_path, expectations_status, created_at,
                    copied_from_offering_id, instance_relpath, section_index
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    semester_id,
                    code,
                    teacher_user_id,
                    self.tenant_id_of(teacher),
                    live_code,
                    None,
                    status,
                    _now(),
                    None,
                    None,
                    int(index),
                ),
            )
            self.conn.commit()
            offering_id = int(cur.lastrowid)
        return self.get_offering(offering_id)

    def get_offering(self, offering_id: int) -> dict[str, Any]:
        """Return one offering or raise KeyError."""
        with self._lock:
            row = self.conn.execute(
                """
                SELECT o.*, u.email AS teacher_email, u.display_name AS teacher_name,
                       s.label AS semester_label, s.year_display AS year_display,
                       s.term AS term, c.title AS course_title
                FROM course_offerings o
                JOIN users u ON u.id = o.teacher_user_id
                JOIN semesters s ON s.id = o.semester_id
                JOIN ontario_courses c ON c.code = o.ontario_code
                WHERE o.id = ?
                """,
                (offering_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"offering {offering_id}")
        return self._offering_dict(row)

    def _active_section_rank(self, data: dict[str, Any]) -> int:
        """1-based rank among non-archived offerings for this teacher/code/term.

        Archived rows and other semesters are ignored so a lone active section
        keeps the plain Ontario code even if older archived rows used higher
        ``section_index`` values.

        Args:
            data: Offering row dict with id, semester, code, teacher, archive.

        Returns:
            Display rank starting at 1.
        """
        if data.get("archived_at"):
            try:
                return max(int(data.get("section_index") or 1), 1)
            except (TypeError, ValueError):
                return 1
        oid = int(data["id"])
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT id FROM course_offerings
                WHERE semester_id = ?
                  AND ontario_code = ?
                  AND teacher_user_id = ?
                  AND archived_at IS NULL
                ORDER BY COALESCE(section_index, 1), id
                """,
                (
                    int(data["semester_id"]),
                    str(data.get("ontario_code") or "").strip().upper(),
                    int(data["teacher_user_id"]),
                ),
            ).fetchall()
        for index, row in enumerate(rows, start=1):
            if int(row["id"]) == oid:
                return index
        return 1

    def _offering_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """Attach the section number and its display code to an offering row.

        Args:
            row: Joined ``course_offerings`` row.

        Returns:
            Offering dict with ``section_index`` and ``section_code``.
        """
        data = dict(row)
        index = int(data.get("section_index") or 1)
        data["section_index"] = index
        rank = self._active_section_rank(data)
        data["section_code"] = section_code(str(data.get("ontario_code") or ""), rank)
        return data

    def archive_offering(self, offering_id: int) -> dict[str, Any]:
        """Soft-archive a course offering (sets archived_at).

        Archived offerings are hidden from the teacher's staff dashboard.
        The offering row and all associated student data remain intact.

        Args:
            offering_id: Primary key of the offering to archive.

        Returns:
            Updated offering dict.

        Raises:
            KeyError: If offering_id does not exist.
        """
        self.get_offering(offering_id)  # raises KeyError if missing
        with self._lock:
            self.conn.execute(
                "UPDATE course_offerings SET archived_at = ? WHERE id = ?",
                (_now(), offering_id),
            )
            self.conn.commit()
        return self.get_offering(offering_id)

    def set_offering_schedule(
        self,
        offering_id: int,
        *,
        live_days: str,
        live_time: str,
    ) -> dict[str, Any]:
        """Persist Admin-chosen live class days/time on an offering.

        Validates against the staff wizard presets. When the offering already
        has game-show classes, their ``days``/``time`` columns are updated to
        match so teachers do not need to re-pick the schedule.

        Args:
            offering_id: ``course_offerings.id``.
            live_days: ``M/W/F`` or ``T/Th/F`` (or already-stored Mon/Wed form).
            live_time: One of the wizard ``TIME_OPTIONS`` labels.

        Returns:
            Updated offering dict.

        Raises:
            KeyError: Unknown offering.
            ValueError: Invalid days or time.
        """
        import sys

        try:
            from paths import MGS_DIR
        except ImportError:
            from lms.paths import MGS_DIR

        if str(MGS_DIR) not in sys.path:
            sys.path.insert(0, str(MGS_DIR))
        from schedule import DAY_PRESETS, TIME_OPTIONS, store_days

        self.get_offering(offering_id)
        days_key = (live_days or "").strip()
        time_key = (live_time or "").strip()
        if days_key in {"Mon/Wed/Fri", "Tue/Thu/Fri"}:
            reverse = {stored: preset for preset, stored in DAY_PRESETS.items()}
            days_key = reverse[days_key]
        if days_key not in DAY_PRESETS:
            raise ValueError("Live-class days must be M/W/F or T/Th/F")
        if time_key not in TIME_OPTIONS:
            raise ValueError(f"Start time must be one of: {', '.join(TIME_OPTIONS)}")
        stored_days = store_days(days_key)
        with self._lock:
            self.conn.execute(
                """
                UPDATE course_offerings
                SET live_days = ?, live_time = ?
                WHERE id = ?
                """,
                (days_key, time_key, int(offering_id)),
            )
            self.conn.commit()
        updater = getattr(self, "sync_offering_class_schedule", None)
        if callable(updater):
            updater(int(offering_id), stored_days=stored_days, live_time=time_key)
        return self.get_offering(offering_id)

    def unarchive_offering(self, offering_id: int) -> dict[str, Any]:
        """Clear archived_at on a course offering, restoring it to the teacher dashboard.

        Args:
            offering_id: Primary key of the offering to restore.

        Returns:
            Updated offering dict.

        Raises:
            KeyError: If offering_id does not exist.
        """
        self.get_offering(offering_id)  # raises KeyError if missing
        with self._lock:
            self.conn.execute(
                "UPDATE course_offerings SET archived_at = NULL WHERE id = ?",
                (offering_id,),
            )
            self.conn.commit()
        return self.get_offering(offering_id)

    def get_offering_for(
        self, semester_id: int, ontario_code: str, teacher_user_id: int
    ) -> dict[str, Any] | None:
        """Return this teacher's first active section of a course/semester.

        Archived offerings are ignored. Later sections (``MCF3M-2``) exist as
        their own rows; callers that need every section use ``list_offerings``.
        """
        with self._lock:
            row = self.conn.execute(
                """
                SELECT id FROM course_offerings
                WHERE semester_id = ? AND ontario_code = ? AND teacher_user_id = ?
                  AND archived_at IS NULL
                ORDER BY COALESCE(section_index, 1), id
                LIMIT 1
                """,
                (semester_id, (ontario_code or "").strip().upper(), teacher_user_id),
            ).fetchone()
        if row is None:
            return None
        return self.get_offering(int(row["id"]))

    def offerings_for_live_code(self, live_access_code: str) -> list[dict[str, Any]]:
        """Return every offering that shares this course live-access code."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT id FROM course_offerings WHERE live_access_code = ?",
                (live_access_code,),
            ).fetchall()
        return [self.get_offering(int(row["id"])) for row in rows]

    def get_offering_by_code(self, live_access_code: str) -> dict[str, Any] | None:
        """Look up a course offering by the shared student code."""
        found = self.offerings_for_live_code(live_access_code)
        return found[0] if found else None

    def list_offerings(
        self,
        *,
        semester_id: int | None = None,
        teacher_user_id: int | None = None,
        include_archived: bool = True,
        tenant_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """List offerings, optionally filtered.

        Args:
            semester_id: Restrict to one semester.
            teacher_user_id: Restrict to one teacher.
            include_archived: When False, rows with a non-NULL ``archived_at``
                are excluded (used by the staff dashboard).
            tenant_id: Restrict to one school seam. ``None`` means ELC default.
        """
        clauses: list[str] = ["o.tenant_id = ?"]
        args: list[Any] = [int(tenant_id or self.default_tenant_id())]
        if semester_id is not None:
            clauses.append("o.semester_id = ?")
            args.append(semester_id)
        if teacher_user_id is not None:
            clauses.append("o.teacher_user_id = ?")
            args.append(teacher_user_id)
        if not include_archived:
            clauses.append("o.archived_at IS NULL")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT o.*, u.email AS teacher_email, u.display_name AS teacher_name,
                   s.label AS semester_label, s.year_display AS year_display,
                   s.term AS term, c.title AS course_title
            FROM course_offerings o
            JOIN users u ON u.id = o.teacher_user_id
            JOIN semesters s ON s.id = o.semester_id
            JOIN ontario_courses c ON c.code = o.ontario_code
            {where}
            ORDER BY o.ontario_code, u.email, COALESCE(o.section_index, 1), o.id
        """
        with self._lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [self._offering_dict(row) for row in rows]

    def list_curriculum_documents(self) -> list[dict[str, Any]]:
        """Return registered Ontario curriculum source documents."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM curriculum_documents ORDER BY subject, grades"
            ).fetchall()
        return [dict(row) for row in rows]

    def count_recent_code_attempts(self, ip: str, seconds: int = 600) -> int:
        """Count failed Student Code joins from an IP in the last ``seconds``."""
        cutoff = datetime.now().timestamp() - seconds
        with self._lock:
            rows = self.conn.execute(
                "SELECT ts FROM student_code_attempts WHERE ip = ?", (ip,)
            ).fetchall()
        n = 0
        for row in rows:
            try:
                ts = datetime.fromisoformat(str(row["ts"])).timestamp()
            except ValueError:
                continue
            if ts >= cutoff:
                n += 1
        return n

    def record_code_attempt(self, ip: str) -> int:
        """Log a failed Student Code join and return the recent-window count.

        Only failed joins should call this — successful joins must not consume
        the failure budget.
        """
        with self._lock:
            self.conn.execute(
                "INSERT INTO student_code_attempts (ip, ts) VALUES (?, ?)",
                (ip, _now()),
            )
            self.conn.commit()
        return self.count_recent_code_attempts(ip, seconds=600)

    def clear_recent_code_attempts(self, ip: str) -> None:
        """Wipe failed Student Code join records for an IP after a success."""
        with self._lock:
            self.conn.execute(
                "DELETE FROM student_code_attempts WHERE ip = ?",
                (ip,),
            )
            self.conn.commit()

    def get_library(self, library_id: int) -> dict[str, Any] | None:
        """Return one ``content_libraries`` row, or None.

        Args:
            library_id: ``content_libraries.id``.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM content_libraries WHERE id = ?",
                (int(library_id),),
            ).fetchone()
        return dict(row) if row else None

    def get_template_library(self, ontario_code: str) -> dict[str, Any] | None:
        """Return the shared template library for a course code, if created.

        Args:
            ontario_code: Catalog course code.
        """
        code = (ontario_code or "").strip().upper()
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM content_libraries
                WHERE ontario_code = ? AND origin = 'template'
                ORDER BY id
                LIMIT 1
                """,
                (code,),
            ).fetchone()
        return dict(row) if row else None

    def latest_library_for_code(self, ontario_code: str) -> dict[str, Any] | None:
        """Return the newest library row for a course code (any origin).

        Used when a code has no git template so a later teacher shares an
        IT upload. Args:
            ontario_code: Catalog course code.
        """
        code = (ontario_code or "").strip().upper()
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM content_libraries
                WHERE ontario_code = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (code,),
            ).fetchone()
        return dict(row) if row else None

    def create_library(
        self,
        ontario_code: str,
        *,
        origin: str,
        source_path: str | None = None,
        source_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Insert a shared content-library pointer.

        Args:
            ontario_code: Catalog course code this pack belongs to.
            origin: ``template``, ``upload``, or ``legacy``.
            source_path: IMSCC path (git template or ``libraries/<id>/``).
            source_sha256: Optional content hash (filled on upload).

        Returns:
            The new library row.
        """
        code = (ontario_code or "").strip().upper()
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT INTO content_libraries (
                    ontario_code, origin, source_path, source_sha256, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (code, origin, source_path, source_sha256, _now()),
            )
            self.conn.commit()
            library_id = int(cur.lastrowid)
        row = self.get_library(library_id)
        assert row is not None
        return row

    def set_library_source(
        self,
        library_id: int,
        source_path: str,
        *,
        source_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Update the IMSCC pointer on a library after the file is stored.

        Args:
            library_id: ``content_libraries.id``.
            source_path: Absolute path to the cartridge.
            source_sha256: Optional content hash.
        """
        with self._lock:
            self.conn.execute(
                """
                UPDATE content_libraries
                SET source_path = ?, source_sha256 = COALESCE(?, source_sha256)
                WHERE id = ?
                """,
                (source_path, source_sha256, int(library_id)),
            )
            self.conn.commit()
        row = self.get_library(int(library_id))
        assert row is not None
        return row

    def register_blob(
        self, sha256: str, byte_count: int, mime: str
    ) -> dict[str, Any]:
        """Record content-addressed blob metadata idempotently.

        Args:
            sha256: Lowercase SHA-256 digest.
            byte_count: Stored payload size in bytes.
            mime: Detected or supplied media type.

        Returns:
            The stable ``blobs`` row.
        """
        digest = str(sha256).strip().lower()
        if len(digest) != 64:
            raise ValueError("sha256 must be a 64-character digest")
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO blobs (sha256, bytes, mime, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(sha256) DO UPDATE SET
                    bytes = excluded.bytes,
                    mime = CASE
                        WHEN blobs.mime = 'application/octet-stream'
                        THEN excluded.mime
                        ELSE blobs.mime
                    END
                """,
                (digest, int(byte_count), str(mime), _now()),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT * FROM blobs WHERE sha256 = ?", (digest,)
            ).fetchone()
        assert row is not None
        return dict(row)

    def base_layer_available(self, ontario_code: str) -> bool:
        """Return True if a module pack library exists for this course code.

        Packs come from Admin IMSCC uploads into ``content_libraries`` on the
        data volume. Git template ``content_root`` is not used in this school
        repo (always NULL).

        Args:
            ontario_code: Catalog course code to check.

        Returns:
            True when a ``content_libraries`` row exists for the code.
        """
        code = (ontario_code or "").strip().upper()
        with self._lock:
            lib_row = self.conn.execute(
                "SELECT id FROM content_libraries WHERE ontario_code = ? LIMIT 1",
                (code,),
            ).fetchone()
        return lib_row is not None

    def get_blob(self, sha256: str) -> dict[str, Any] | None:
        """Return blob metadata without touching the stored file."""
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM blobs WHERE sha256 = ?",
                (str(sha256).strip().lower(),),
            ).fetchone()
        return dict(row) if row else None

    def list_module_outlines(self, library_id: int) -> list[dict[str, Any]]:
        """Return module outlines for a content library in position order.

        Args:
            library_id: ``content_libraries.id``.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM module_outlines
                WHERE library_id = ?
                ORDER BY position, id
                """,
                (int(library_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_google_api_token(self, user_id: int) -> dict[str, Any] | None:
        """Return stored Slides/Drive tokens for a user, if any.

        Args:
            user_id: ``users.id``.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM google_api_tokens WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
        return dict(row) if row else None

    def get_lesson_slide_deck(
        self,
        class_id: int,
        module_number: int,
        live_index: int,
    ) -> dict[str, Any] | None:
        """Return the Lesson Slides deck for one live class in a module.

        Args:
            class_id: MGS ``classes.id``.
            module_number: 1-based module outline position.
            live_index: 1-based live class within the module.
        """
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM lesson_slide_decks
                WHERE class_id = ? AND module_number = ? AND live_index = ?
                """,
                (int(class_id), int(module_number), int(live_index)),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        for key in ("preview_json", "fill_json"):
            raw = data.get(key)
            if isinstance(raw, str) and raw.strip():
                try:
                    data[key] = json.loads(raw)
                except json.JSONDecodeError:
                    pass
        return data

    def upsert_lesson_slide_deck(
        self,
        class_id: int,
        module_number: int,
        live_index: int,
        *,
        presentation_id: str,
        presentation_url: str,
        preview_json: Any = None,
        fill_json: Any = None,
    ) -> dict[str, Any]:
        """Insert or replace the Drive file keyed by class + module + live index.

        Args:
            class_id: MGS class id.
            module_number: Module outline position.
            live_index: Live class in the module.
            presentation_id: Google or mock id.
            presentation_url: Open URL.
            preview_json: Connected Lessons snapshot.
            fill_json: Image mode and fill metadata.

        Returns:
            Stored row.
        """
        preview_blob = preview_json
        if preview_blob is not None and not isinstance(preview_blob, str):
            preview_blob = json.dumps(preview_blob)
        fill_blob = fill_json
        if fill_blob is not None and not isinstance(fill_blob, str):
            fill_blob = json.dumps(fill_blob)
        now = _now()
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO lesson_slide_decks (
                    class_id, module_number, live_index, presentation_id,
                    presentation_url, preview_json, fill_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(class_id, module_number, live_index) DO UPDATE SET
                    presentation_id = excluded.presentation_id,
                    presentation_url = excluded.presentation_url,
                    preview_json = excluded.preview_json,
                    fill_json = excluded.fill_json,
                    updated_at = excluded.updated_at
                """,
                (
                    int(class_id),
                    int(module_number),
                    int(live_index),
                    presentation_id,
                    presentation_url,
                    preview_blob,
                    fill_blob,
                    now,
                    now,
                ),
            )
            self.conn.commit()
        row = self.get_lesson_slide_deck(class_id, module_number, live_index)
        assert row is not None
        return row

    def store_google_api_token(
        self,
        user_id: int,
        *,
        refresh_token: str,
        scopes: str,
        access_token: str | None = None,
        expires_in: int | None = None,
    ) -> dict[str, Any]:
        """Insert or update Google API tokens for deck creation.

        Args:
            user_id: Operator ``users.id``.
            refresh_token: Offline refresh token (or mock marker).
            scopes: Space-separated OAuth scopes.
            access_token: Optional short-lived token.
            expires_in: Seconds until access token expiry.

        Returns:
            Stored token row.
        """
        expires_at = None
        if expires_in:
            expires_at = (
                datetime.now().replace(microsecond=0)
                + timedelta(seconds=int(expires_in))
            ).isoformat()
        now = _now()
        with self._lock:
            existing = self.conn.execute(
                "SELECT refresh_token FROM google_api_tokens WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
            keep_refresh = refresh_token or (
                str(existing["refresh_token"]) if existing else ""
            )
            self.conn.execute(
                """
                INSERT INTO google_api_tokens (
                    user_id, refresh_token, access_token,
                    access_token_expires_at, scopes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    refresh_token = excluded.refresh_token,
                    access_token = excluded.access_token,
                    access_token_expires_at = excluded.access_token_expires_at,
                    scopes = excluded.scopes,
                    updated_at = excluded.updated_at
                """,
                (
                    int(user_id),
                    keep_refresh,
                    access_token,
                    expires_at,
                    scopes,
                    now,
                ),
            )
            self.conn.commit()
        row = self.get_google_api_token(user_id)
        assert row is not None
        return row

    def delete_google_api_token(self, user_id: int) -> None:
        """Drop stored Slides/Drive tokens for a user.

        Args:
            user_id: ``users.id``.
        """
        with self._lock:
            self.conn.execute(
                "DELETE FROM google_api_tokens WHERE user_id = ?",
                (int(user_id),),
            )
            self.conn.commit()

    def _live_problem_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        """Attach process keys and parsed expectation codes to a problem row.

        Args:
            row: ``live_problems`` sqlite row.
        """
        item = dict(row)
        item["expectation_codes"] = json.loads(
            item.get("expectation_codes_json") or "[]"
        )
        with self._lock:
            procs = self.conn.execute(
                """
                SELECT process_key FROM live_problem_processes
                WHERE problem_id = ? ORDER BY process_key
                """,
                (int(item["id"]),),
            ).fetchall()
        item["processes"] = [str(p["process_key"]) for p in procs]
        return item

    def list_live_problems(
        self,
        *,
        ontario_code: str | None = None,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        """List curated live problems, optionally filtered by course.

        Args:
            ontario_code: Course code filter.
            active_only: When True, hide deactivated items.
        """
        sql = "SELECT * FROM live_problems WHERE 1=1"
        args: list[Any] = []
        if ontario_code:
            sql += " AND ontario_code = ?"
            args.append(str(ontario_code).strip().upper())
        if active_only:
            sql += " AND active = 1"
        sql += " ORDER BY sort_order, id"
        with self._lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [self._live_problem_from_row(row) for row in rows]

    def get_live_problem(self, problem_id: int) -> dict[str, Any] | None:
        """Return one live problem with process tags.

        Args:
            problem_id: ``live_problems.id``.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM live_problems WHERE id = ?",
                (int(problem_id),),
            ).fetchone()
        return self._live_problem_from_row(row) if row else None

    def upsert_live_problem(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create or update a live problem and replace its process tags.

        Args:
            payload: Problem fields including optional ``id`` and ``processes``.

        Returns:
            Stored problem dict.
        """
        now = _now()
        codes = payload.get("expectation_codes") or []
        if isinstance(codes, str):
            codes = [part.strip() for part in codes.split(",") if part.strip()]
        processes = payload.get("processes") or []
        problem_id = payload.get("id")
        fields = (
            str(payload.get("ontario_code") or "MCF3M").strip().upper(),
            str(payload.get("module_hint") or "").strip(),
            str(payload.get("kind") or "standard").strip().lower(),
            str(payload.get("title") or "").strip() or "Untitled",
            str(payload.get("stem_html") or ""),
            str(payload.get("task_html") or ""),
            str(payload.get("diagram_note") or ""),
            str(payload.get("source") or ""),
            str(payload.get("license") or ""),
            json.dumps(list(codes)),
            1 if payload.get("active", True) else 0,
            int(payload.get("sort_order") or 0),
        )
        with self._lock:
            if problem_id:
                self.conn.execute(
                    """
                    UPDATE live_problems SET
                        ontario_code = ?, module_hint = ?, kind = ?, title = ?,
                        stem_html = ?, task_html = ?, diagram_note = ?,
                        source = ?, license = ?, expectation_codes_json = ?,
                        active = ?, sort_order = ?
                    WHERE id = ?
                    """,
                    (*fields, int(problem_id)),
                )
                pid = int(problem_id)
            else:
                cur = self.conn.execute(
                    """
                    INSERT INTO live_problems (
                        ontario_code, module_hint, kind, title, stem_html,
                        task_html, diagram_note, source, license,
                        expectation_codes_json, active, sort_order, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*fields, now),
                )
                pid = int(cur.lastrowid)
            self.conn.execute(
                "DELETE FROM live_problem_processes WHERE problem_id = ?",
                (pid,),
            )
            for key in processes:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO live_problem_processes
                        (problem_id, process_key) VALUES (?, ?)
                    """,
                    (pid, str(key)),
                )
            self.conn.commit()
        row = self.get_live_problem(pid)
        assert row is not None
        return row

    def set_live_problem_active(self, problem_id: int, active: bool) -> dict[str, Any]:
        """Activate or deactivate a live problem.

        Args:
            problem_id: ``live_problems.id``.
            active: When False, hide from the generator.

        Returns:
            Updated problem.

        Raises:
            KeyError: Unknown id.
        """
        if self.get_live_problem(problem_id) is None:
            raise KeyError(f"live problem {problem_id}")
        with self._lock:
            self.conn.execute(
                "UPDATE live_problems SET active = ? WHERE id = ?",
                (1 if active else 0, int(problem_id)),
            )
            self.conn.commit()
        row = self.get_live_problem(problem_id)
        assert row is not None
        return row

    def list_math_processes(self) -> list[dict[str, Any]]:
        """Return seeded Ontario mathematical processes."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM math_processes ORDER BY sort_order, process_key"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_quick_phrases(
        self,
        *,
        category: str | None = None,
        active_only: bool = True,
        process_keys: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List evidence phrases, optionally filtered.

        Args:
            category: ``team_problem``, ``consolidation``, or ``general``.
            active_only: Hide deactivated phrases.
            process_keys: Restrict to these process keys.
        """
        sql = "SELECT * FROM quick_phrases WHERE 1=1"
        args: list[Any] = []
        if active_only:
            sql += " AND active = 1"
        if category:
            sql += " AND category = ?"
            args.append(str(category))
        if process_keys:
            placeholders = ",".join("?" for _ in process_keys)
            sql += f" AND process_key IN ({placeholders})"
            args.extend(process_keys)
        sql += " ORDER BY sort_order, id"
        with self._lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def upsert_quick_phrase(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create or update a quick-evidence phrase.

        Args:
            payload: Phrase fields; ``id`` updates an existing row.

        Returns:
            Stored phrase dict.
        """
        phrase_id = payload.get("id")
        fields = (
            str(payload.get("process_key") or "").strip(),
            str(payload.get("label") or "").strip() or "Untitled",
            str(payload.get("description") or ""),
            str(payload.get("category") or "general").strip(),
            1 if payload.get("active", True) else 0,
            int(payload.get("sort_order") or 0),
        )
        with self._lock:
            if phrase_id:
                self.conn.execute(
                    """
                    UPDATE quick_phrases SET
                        process_key = ?, label = ?, description = ?,
                        category = ?, active = ?, sort_order = ?
                    WHERE id = ?
                    """,
                    (*fields, int(phrase_id)),
                )
                pid = int(phrase_id)
            else:
                cur = self.conn.execute(
                    """
                    INSERT INTO quick_phrases (
                        process_key, label, description, category, active, sort_order
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    fields,
                )
                pid = int(cur.lastrowid)
            self.conn.commit()
            row = self.conn.execute(
                "SELECT * FROM quick_phrases WHERE id = ?", (pid,)
            ).fetchone()
        if row is None:
            raise KeyError(f"quick phrase {pid}")
        return dict(row)

    def preselect_quick_phrases_for_processes(
        self, process_keys: list[str]
    ) -> list[int]:
        """Return phrase ids whose process matches contest/consolidation tags.

        Args:
            process_keys: Process keys from the generated deck.
        """
        if not process_keys:
            return []
        rows = self.list_quick_phrases(active_only=True, process_keys=process_keys)
        return [int(row["id"]) for row in rows]

    def get_live_session_for_class_date(
        self, class_id: int, meeting_date: str
    ) -> dict[str, Any] | None:
        """Return the newest live session for a class on a given date.

        Args:
            class_id: MGS class id.
            meeting_date: ISO date.
        """
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM live_class_sessions
                WHERE class_id = ? AND meeting_date = ?
                ORDER BY id DESC LIMIT 1
                """,
                (int(class_id), str(meeting_date)[:10]),
            ).fetchone()
        if row:
            return dict(row)
        active = self.get_active_live_session_for_class(class_id)
        if active and (
            not active.get("meeting_date")
            or str(active.get("meeting_date")) == str(meeting_date)[:10]
        ):
            return dict(active)
        return None

    def set_live_session_slides(
        self,
        session_id: int,
        *,
        meeting_date: str,
        presentation_id: str,
        presentation_url: str,
        slides_json: Any = None,
    ) -> dict[str, Any]:
        """Persist deck ids and timeline snapshot on a live session.

        Args:
            session_id: ``live_class_sessions.id``.
            meeting_date: ISO class date.
            presentation_id: Google id or mock id.
            presentation_url: View URL or local preview path.
            slides_json: Selected problem ids and process keys.

        Returns:
            Updated session row.

        Raises:
            KeyError: Unknown session.
        """
        blob = slides_json
        if blob is not None and not isinstance(blob, str):
            blob = json.dumps(blob)
        with self._lock:
            cur = self.conn.execute(
                """
                UPDATE live_class_sessions
                SET meeting_date = ?, presentation_id = ?,
                    presentation_url = ?, slides_json = ?
                WHERE id = ?
                """,
                (
                    str(meeting_date)[:10],
                    presentation_id,
                    presentation_url,
                    blob,
                    int(session_id),
                ),
            )
            self.conn.commit()
            if cur.rowcount == 0:
                raise KeyError(f"live session {session_id}")
        row = self.get_live_session(session_id)
        assert row is not None
        return row

    def _observation_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """Attach subject ids and process keys to an observation.

        Args:
            row: ``observations`` sqlite row.
        """
        item = dict(row)
        oid = int(item["id"])
        with self._lock:
            subjects = self.conn.execute(
                """
                SELECT student_id FROM observation_subjects
                WHERE observation_id = ? ORDER BY student_id
                """,
                (oid,),
            ).fetchall()
            processes = self.conn.execute(
                """
                SELECT process_key FROM observation_processes
                WHERE observation_id = ? ORDER BY process_key
                """,
                (oid,),
            ).fetchall()
        item["student_ids"] = [int(s["student_id"]) for s in subjects]
        item["process_keys"] = [str(p["process_key"]) for p in processes]
        item["follow_up_required"] = bool(item.get("follow_up_required"))
        return item

    def list_observations(self, live_session_id: int) -> list[dict[str, Any]]:
        """List observations for a live session, newest last.

        Args:
            live_session_id: ``live_class_sessions.id``.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM observations
                WHERE live_session_id = ?
                ORDER BY created_at, id
                """,
                (int(live_session_id),),
            ).fetchall()
        return [self._observation_dict(row) for row in rows]

    def team_student_ids(self, team_id: int) -> list[int]:
        """Return current team members from ``game_memberships``.

        Args:
            team_id: MGS ``game_teams.id``.
        """
        with self.game._lock:
            rows = self.game.conn.execute(
                """
                SELECT student_id FROM game_memberships
                WHERE team_id = ?
                ORDER BY student_id
                """,
                (int(team_id),),
            ).fetchall()
        return [int(row["student_id"]) for row in rows]

    def get_latest_live_session_for_class(self, class_id: int) -> dict[str, Any] | None:
        """Most recent live session for a class, including ended sessions.

        Args:
            class_id: Game-show ``classes.id``.
        """
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM live_class_sessions
                WHERE class_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(class_id),),
            ).fetchone()
        return dict(row) if row else None

    def get_observation(self, observation_id: int) -> dict[str, Any] | None:
        """Return one observation with subjects and processes.

        Args:
            observation_id: ``observations.id``.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM observations WHERE id = ?",
                (int(observation_id),),
            ).fetchone()
        return self._observation_dict(row) if row else None

    def create_observation(
        self,
        *,
        live_session_id: int,
        class_id: int,
        observer_user_id: int,
        scope: str,
        note: str,
        student_ids: list[int] | None = None,
        team_id: int | None = None,
        process_keys: list[str] | None = None,
        evidence_strength: str | None = None,
        follow_up_required: bool = False,
        source: str = "manual",
        lookfor_key: str | None = None,
        quick_phrase_id: int | None = None,
        visibility: str = "staff",
    ) -> dict[str, Any]:
        """Insert an evidence observation (not a point event).

        Args:
            live_session_id: Owning live session.
            class_id: MGS class id.
            observer_user_id: Staff user recording evidence.
            scope: ``student``, ``students``, ``team``, or ``class``.
            note: Observation text (often from a phrase).
            student_ids: MGS student ids when scope is student(s).
            team_id: MGS ``game_teams.id`` when scope is team.
            process_keys: Ontario process keys.
            evidence_strength: ``emerging``, ``developing``, ``clear``, or None.
            follow_up_required: Teacher flag.
            source: ``quick_phrase``, ``manual``, or ``lookfor``.
            lookfor_key: Team Challenge look-for id when ``source=lookfor``.
            quick_phrase_id: Phrase used, if any.
            visibility: Default ``staff``.

        Returns:
            Stored observation.

        Raises:
            ValueError: Invalid scope or empty note.
        """
        scope_key = (scope or "").strip().lower()
        if scope_key not in {"student", "students", "team", "class"}:
            raise ValueError("scope must be student, students, team, or class")
        text = (note or "").strip()
        if not text:
            raise ValueError("note is required")
        ids = [int(sid) for sid in (student_ids or [])]
        if scope_key == "student" and len(ids) != 1:
            raise ValueError("student scope requires exactly one student_id")
        if scope_key == "students" and len(ids) < 2:
            raise ValueError("students scope requires at least two student_ids")
        if scope_key == "team" and team_id is None:
            raise ValueError("team scope requires team_id")
        strength = (evidence_strength or "").strip().lower() or None
        if strength and strength not in {"emerging", "developing", "clear"}:
            raise ValueError("invalid evidence_strength")
        keys = [str(k) for k in (process_keys or [])]
        now = _now()
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT INTO observations (
                    live_session_id, class_id, observer_user_id, created_at,
                    scope, team_id, note, evidence_strength, follow_up_required,
                    visibility, source, lookfor_key, quick_phrase_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(live_session_id),
                    int(class_id),
                    int(observer_user_id),
                    now,
                    scope_key,
                    int(team_id) if team_id is not None else None,
                    text,
                    strength,
                    1 if follow_up_required else 0,
                    visibility or "staff",
                    source or "manual",
                    (lookfor_key or "").strip() or None,
                    int(quick_phrase_id) if quick_phrase_id else None,
                ),
            )
            oid = int(cur.lastrowid)
            for sid in ids:
                self.conn.execute(
                    """
                    INSERT INTO observation_subjects (observation_id, student_id)
                    VALUES (?, ?)
                    """,
                    (oid, sid),
                )
            for key in keys:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO observation_processes
                        (observation_id, process_key) VALUES (?, ?)
                    """,
                    (oid, key),
                )
            self.conn.commit()
        row = self.get_observation(oid)
        assert row is not None
        return row

    def update_observation(
        self, observation_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Edit note, strength, follow-up, subjects, or processes.

        Args:
            observation_id: ``observations.id``.
            payload: Partial fields to update.

        Returns:
            Updated observation.

        Raises:
            KeyError: Unknown id.
        """
        existing = self.get_observation(observation_id)
        if existing is None:
            raise KeyError(f"observation {observation_id}")
        note = payload.get("note", existing["note"])
        strength = payload.get("evidence_strength", existing.get("evidence_strength"))
        follow = payload.get("follow_up_required", existing.get("follow_up_required"))
        if strength == "":
            strength = None
        with self._lock:
            self.conn.execute(
                """
                UPDATE observations
                SET note = ?, evidence_strength = ?, follow_up_required = ?
                WHERE id = ?
                """,
                (
                    str(note),
                    strength,
                    1 if follow else 0,
                    int(observation_id),
                ),
            )
            if "student_ids" in payload:
                self.conn.execute(
                    "DELETE FROM observation_subjects WHERE observation_id = ?",
                    (int(observation_id),),
                )
                for sid in payload.get("student_ids") or []:
                    self.conn.execute(
                        """
                        INSERT INTO observation_subjects
                            (observation_id, student_id) VALUES (?, ?)
                        """,
                        (int(observation_id), int(sid)),
                    )
            if "process_keys" in payload:
                self.conn.execute(
                    "DELETE FROM observation_processes WHERE observation_id = ?",
                    (int(observation_id),),
                )
                for key in payload.get("process_keys") or []:
                    self.conn.execute(
                        """
                        INSERT OR IGNORE INTO observation_processes
                            (observation_id, process_key) VALUES (?, ?)
                        """,
                        (int(observation_id), str(key)),
                    )
            self.conn.commit()
        row = self.get_observation(observation_id)
        assert row is not None
        return row

    def delete_observation(self, observation_id: int) -> None:
        """Delete an observation and its subject/process rows.

        Args:
            observation_id: ``observations.id``.

        Raises:
            KeyError: Unknown id.
        """
        if self.get_observation(observation_id) is None:
            raise KeyError(f"observation {observation_id}")
        with self._lock:
            self.conn.execute(
                "DELETE FROM observations WHERE id = ?",
                (int(observation_id),),
            )
            self.conn.commit()

    def observation_coverage(self, live_session_id: int) -> dict[str, Any]:
        """Count observations per student × process for one live session.

        Args:
            live_session_id: ``live_class_sessions.id``.

        Returns:
            ``{student_id: {process_key: count}}`` plus class-level counts.
        """
        observations = self.list_observations(live_session_id)
        by_student: dict[str, dict[str, int]] = {}
        class_counts: dict[str, int] = {}
        for obs in observations:
            keys = obs.get("process_keys") or []
            if obs.get("scope") == "class":
                for key in keys:
                    class_counts[key] = class_counts.get(key, 0) + 1
            for sid in obs.get("student_ids") or []:
                bucket = by_student.setdefault(str(sid), {})
                for key in keys:
                    bucket[key] = bucket.get(key, 0) + 1
        return {
            "live_session_id": int(live_session_id),
            "by_student": by_student,
            "class_counts": class_counts,
            "observation_count": len(observations),
        }

    def lookfor_tallies_for_module(
        self,
        class_id: int,
        module_number: int,
        *,
        n_modules: int = 8,
    ) -> dict[str, dict[str, int]]:
        """Count ``source=lookfor`` observations per student in a module window.

        Args:
            class_id: MGS class id.
            module_number: 1-based module index.
            n_modules: Course module count for even windows.

        Returns:
            ``{student_id: {lookfor_id: count}}``.
        """
        try:
            from gradebook import even_module_windows
            from portfolio.lookfors import LOOKFOR_IDS, empty_tally
        except ImportError:
            from lms.gradebook import even_module_windows
            from lms.portfolio.lookfors import LOOKFOR_IDS, empty_tally

        windows = even_module_windows(n_modules=n_modules)
        window = next(
            (row for row in windows if int(row.get("number") or 0) == int(module_number)),
            None,
        )
        this_days = set(window.get("days") or []) if window else set()
        all_window_days = {
            str(day)
            for row in windows
            for day in (row.get("days") or [])
        }
        with self._lock:
            obs_rows = self.conn.execute(
                """
                SELECT * FROM observations
                WHERE class_id = ? AND source = 'lookfor'
                ORDER BY id
                """,
                (int(class_id),),
            ).fetchall()
            sessions = {
                int(row["id"]): dict(row)
                for row in self.conn.execute(
                    """
                    SELECT id, meeting_date, started_at FROM live_class_sessions
                    WHERE class_id = ?
                    """,
                    (int(class_id),),
                ).fetchall()
            }
        tallies: dict[str, dict[str, int]] = {}
        for raw in obs_rows:
            obs = self._observation_dict(raw)
            key = str(obs.get("lookfor_key") or "").strip()
            if key not in LOOKFOR_IDS:
                continue
            sess = sessions.get(int(obs.get("live_session_id") or 0))
            meeting = ""
            if sess is not None:
                meeting = str(sess.get("meeting_date") or sess.get("started_at") or "")[:10]
            if this_days:
                if meeting in this_days:
                    pass
                elif meeting in all_window_days:
                    continue
                elif int(module_number) != 1:
                    continue
            ids = [int(sid) for sid in (obs.get("student_ids") or [])]
            if not ids and obs.get("team_id") is not None:
                ids = self.team_student_ids(int(obs["team_id"]))
            for student_id in ids:
                bucket = tallies.setdefault(str(int(student_id)), empty_tally())
                bucket[key] = int(bucket.get(key) or 0) + 1
        return tallies

    def list_portfolio_mark_rows(
        self, class_id: int, module_number: int
    ) -> list[dict[str, Any]]:
        """Load persisted mark suggestions/overrides for a class module.

        Args:
            class_id: MGS class id.
            module_number: 1-based module index.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM portfolio_mark_suggestions
                WHERE class_id = ? AND module_number = ?
                """,
                (int(class_id), int(module_number)),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for field in ("suggested_json", "override_json"):
                raw = item.get(field)
                if isinstance(raw, str) and raw.strip():
                    try:
                        item[field.replace("_json", "")] = json.loads(raw)
                    except json.JSONDecodeError:
                        item[field.replace("_json", "")] = {}
                else:
                    item[field.replace("_json", "")] = {} if field == "suggested_json" else None
            out.append(item)
        return out

    def upsert_portfolio_mark(
        self,
        *,
        class_id: int,
        ontario_code: str,
        module_number: int,
        student_id: int,
        suggested: dict[str, Any] | None = None,
        override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert or update local portfolio mark suggestions (not the 100% gate).

        Args:
            class_id: MGS class id.
            ontario_code: Course code.
            module_number: Module index.
            student_id: Roster student id.
            suggested: Classifier output.
            override: Staff L1–L4 overrides.

        Returns:
            Stored row.
        """
        now = _now()
        suggested_json = json.dumps(suggested or {}, ensure_ascii=False)
        override_json = json.dumps(override, ensure_ascii=False) if override is not None else None
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO portfolio_mark_suggestions (
                    class_id, ontario_code, module_number, student_id,
                    suggested_json, override_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(class_id, module_number, student_id) DO UPDATE SET
                    ontario_code = excluded.ontario_code,
                    suggested_json = CASE
                        WHEN excluded.suggested_json = '{}' THEN
                            portfolio_mark_suggestions.suggested_json
                        ELSE excluded.suggested_json
                    END,
                    override_json = COALESCE(
                        excluded.override_json,
                        portfolio_mark_suggestions.override_json
                    ),
                    updated_at = excluded.updated_at
                """,
                (
                    int(class_id),
                    str(ontario_code).upper(),
                    int(module_number),
                    int(student_id),
                    suggested_json,
                    override_json,
                    now,
                ),
            )
            self.conn.commit()
            row = self.conn.execute(
                """
                SELECT * FROM portfolio_mark_suggestions
                WHERE class_id = ? AND module_number = ? AND student_id = ?
                """,
                (int(class_id), int(module_number), int(student_id)),
            ).fetchone()
        return dict(row) if row else {}


class SchoolDB(LovesDB):
    """LLOVES facade: school tables plus a GameShowDB on the same sqlite file."""

    def __init__(
        self,
        db_path: Path | None = None,
        data_dir: Path | None = None,
        *,
        it_email: str = IT_EMAIL_DEFAULT,
    ) -> None:
        """Open school + game-show tables.

        Args:
            db_path: Shared sqlite file.
            data_dir: Uploads/logs for Game Show.
            it_email: Bootstrap IT account.
        """
        import sys

        try:
            from paths import DEFAULT_DB_PATH, MGS_DIR
        except ImportError:
            from lms.paths import DEFAULT_DB_PATH, MGS_DIR

        path = Path(db_path or DEFAULT_DB_PATH)
        store = Path(data_dir or path.parent)
        super().__init__(path, it_email=it_email)
        if str(MGS_DIR) not in sys.path:
            sys.path.append(str(MGS_DIR))
        import importlib.util

        spec = importlib.util.spec_from_file_location("mgs_db", MGS_DIR / "db.py")
        if spec is None or spec.loader is None:
            raise ImportError("Math Game Show db.py is missing")
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("mgs_db", mod)
        spec.loader.exec_module(mod)
        self.game = mod.GameShowDB(path, store)
        self.data_dir = store

    def close(self) -> None:
        """Close both sqlite connections."""
        try:
            self.game.close()
        except Exception:
            pass
        super().close()

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        """Alias for ``get_user``."""
        return self.get_user(user_id)

    def get_active_semester(self) -> dict[str, Any] | None:
        """Alias for ``active_semester``."""
        return self.active_semester()

    def search_ontario_courses(self, query: str = "", limit: int = 40) -> list[dict[str, Any]]:
        """Autocomplete wrapper with a row cap."""
        return self.list_ontario_courses(query)[: int(limit)]

    def get_ontario_course(self, code: str) -> dict[str, Any] | None:
        """Alias for ``get_course``."""
        return self.get_course(code)

    def list_expectations(self, course_code: str) -> list[dict[str, Any]]:
        """Alias for ``expectations_for``."""
        return self.expectations_for(course_code)

    def list_staff(
        self,
        *,
        include_archived: bool = False,
        tenant_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Staff list with pending/active/archived status and assigned codes.

        Args:
            include_archived: When False (default), archived users are excluded.
            tenant_id: Restrict to one school seam.

        Returns:
            List of enriched user dicts each with ``status``, ``assigned_codes``,
            and ``last_login_at`` keys populated.
        """
        active_sem = self.get_active_semester()
        people = super().list_staff(
            include_archived=include_archived, tenant_id=tenant_id
        )
        out = []
        for person in people:
            item = dict(person)
            if item.get("archived_at"):
                item["status"] = "archived"
            elif item.get("verified_at"):
                item["status"] = "active"
            else:
                item["status"] = "pending"
            if active_sem:
                offs = self.list_offerings(
                    teacher_user_id=int(item["id"]),
                    semester_id=int(active_sem["id"]),
                    include_archived=False,
                )
            else:
                offs = self.list_offerings(
                    teacher_user_id=int(item["id"]),
                    include_archived=False,
                )
            item["assigned_codes"] = (
                ", ".join(
                    o.get("section_code")
                    or section_code(str(o["ontario_code"]), o.get("section_index"))
                    for o in offs
                )
                or None
            )
            item["last_login_at"] = item.get("last_login_at")
            item["last_login_display"] = format_human_datetime(item.get("last_login_at"))
            out.append(item)
        return out

    def assign_course(
        self,
        *,
        teacher_user_id: int,
        ontario_code: str,
        semester_id: int | None = None,
        imscc_path: str | None = None,
        copied_from_offering_id: int | None = None,
        library_id: int | None = None,
        new_section: bool = False,
        slides_template_id: str | None = None,
        style_guide_file_id: str | None = None,
    ) -> dict[str, Any]:
        """Assign a course using the active semester when ``semester_id`` is omitted.

        Creates a thin instance (manifest + syllabus). Teachers of the same
        code share one ``library_id`` unless IT attaches a new upload — and so
        do extra sections held by one teacher. Live-access codes stay per
        (semester, course).

        Args:
            teacher_user_id: Staff or IT user id.
            ontario_code: Catalog course code.
            semester_id: Defaults to the active semester.
            imscc_path: Optional override after attach (tests/uploads).
            copied_from_offering_id: Prior offering to copy syllabus from
                and share that offering's library.
            library_id: Explicit library to attach (IT upload). None =
                shared template, or the newest library for this code.
            new_section: When True and this teacher already holds the code,
                add another section (``MCF3M-2``) instead of returning the
                offering they already have.
            slides_template_id: Optional Google presentation id. Empty uses
                the base offering or school default (Lesson Theme Template #1).
            style_guide_file_id: Optional Drive file id for the style guide.
        """
        try:
            from instances import materialize_instance
        except ImportError:
            from lms.instances import materialize_instance

        semester = (
            self.get_semester(semester_id)
            if semester_id is not None
            else self.get_active_semester()
        )
        if not semester:
            raise ValueError("Activate a semester before assigning courses")
        if self.get_course(ontario_code) is None:
            raise ValueError(
                f"Unknown Ontario course code {ontario_code}. "
                "Ask IT to add a curriculum source."
            )
        existing = self.get_offering_for(
            int(semester["id"]), ontario_code, teacher_user_id
        )
        if existing and not new_section:
            return self.ensure_offering_instance(existing)
        offering = super().assign_course(
            semester_id=int(semester["id"]),
            ontario_code=ontario_code,
            teacher_user_id=teacher_user_id,
            new_section=new_section,
        )
        base = None
        if copied_from_offering_id:
            base = self.get_offering(int(copied_from_offering_id))
            if str(base["ontario_code"]).upper() != str(offering["ontario_code"]).upper():
                raise ValueError("Base instance must be the same Ontario course")
            base = self.ensure_offering_instance(base)
        course = self.get_course(str(offering["ontario_code"]))
        teacher = self.get_user(int(offering["teacher_user_id"])) or {}
        calendar = semester.get("raw_json") or semester.get("payload")
        attached = self._library_for_assign(
            str(offering["ontario_code"]),
            content_root=(course or {}).get("content_root"),
            base_offering=base,
            library_id=library_id,
        )
        result = materialize_instance(
            self.data_dir,
            offering,
            semester_label=str(semester["label"]),
            teacher_name=str(teacher.get("display_name") or teacher.get("email") or ""),
            content_root=(course or {}).get("content_root"),
            base_offering=base,
            calendar=calendar,
            library_id=int(attached["id"]) if attached else None,
        )
        stored_imscc = imscc_path
        if stored_imscc is None and attached:
            stored_imscc = attached.get("source_path")
        self._save_offering_instance(
            int(offering["id"]),
            instance_relpath=str(result["instance_relpath"]),
            copied_from_offering_id=(
                int(copied_from_offering_id) if copied_from_offering_id else None
            ),
            imscc_path=stored_imscc,
            library_id=int(attached["id"]) if attached else None,
        )
        self.set_offering_slides_theme(
            int(offering["id"]),
            slides_template_id=slides_template_id,
            style_guide_file_id=style_guide_file_id,
            base_offering=base,
        )
        return self.get_offering(int(offering["id"]))

    def _save_offering_instance(
        self,
        offering_id: int,
        *,
        instance_relpath: str,
        copied_from_offering_id: int | None = None,
        imscc_path: str | None = None,
        library_id: int | None = None,
    ) -> None:
        """Persist instance path / library pointer on ``course_offerings``.

        Args:
            offering_id: ``course_offerings.id``.
            instance_relpath: Volume-relative instance folder.
            copied_from_offering_id: Base offering, or None for the shared library.
            imscc_path: Shared cartridge pointer (template or ``libraries/<id>/``).
            library_id: Shared ``content_libraries.id``.
        """
        with self._lock:
            self.conn.execute(
                """
                UPDATE course_offerings
                SET instance_relpath = ?, copied_from_offering_id = ?,
                    imscc_path = ?, library_id = ?
                WHERE id = ?
                """,
                (
                    instance_relpath,
                    copied_from_offering_id,
                    imscc_path,
                    library_id,
                    int(offering_id),
                ),
            )
            self.conn.commit()

    def set_offering_slides_theme(
        self,
        offering_id: int,
        *,
        slides_template_id: str | None = None,
        style_guide_file_id: str | None = None,
        base_offering: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist Google theme ids on an offering (assign-time snapshot).

        Empty form values fall back to the base layer, then school defaults.

        Args:
            offering_id: ``course_offerings.id``.
            slides_template_id: Pasted presentation id, or None for default.
            style_guide_file_id: Pasted style-guide id, or None.
            base_offering: Copied-from offering, if any.

        Returns:
            Updated offering dict.
        """
        try:
            from live_class_constants import (
                DEFAULT_SLIDES_TEMPLATE_ID,
                SETTING_DEFAULT_SLIDES_TEMPLATE_ID,
                SETTING_DEFAULT_STYLE_GUIDE_FILE_ID,
            )
        except ImportError:
            from lms.live_class_constants import (
                DEFAULT_SLIDES_TEMPLATE_ID,
                SETTING_DEFAULT_SLIDES_TEMPLATE_ID,
                SETTING_DEFAULT_STYLE_GUIDE_FILE_ID,
            )
        template = (slides_template_id or "").strip()
        style = (style_guide_file_id or "").strip()
        if not template and base_offering:
            template = str(base_offering.get("slides_template_id") or "").strip()
        if not style and base_offering:
            style = str(base_offering.get("style_guide_file_id") or "").strip()
        if not template:
            template = self.get_school_setting(
                SETTING_DEFAULT_SLIDES_TEMPLATE_ID, DEFAULT_SLIDES_TEMPLATE_ID
            )
        if not style:
            style = self.get_school_setting(SETTING_DEFAULT_STYLE_GUIDE_FILE_ID, "")
        with self._lock:
            self.conn.execute(
                """
                UPDATE course_offerings
                SET slides_template_id = ?, style_guide_file_id = ?
                WHERE id = ?
                """,
                (template or None, style or None, int(offering_id)),
            )
            self.conn.commit()
        return self.get_offering(int(offering_id))

    def ensure_template_library(
        self, ontario_code: str, content_root: str | None = None
    ) -> dict[str, Any] | None:
        """Create the shared template library once for a code that has an IMSCC.

        Args:
            ontario_code: Catalog course code.
            content_root: Catalog ``content_root`` (repo-relative).

        Returns:
            Existing or new ``origin=template`` row, or None if no git pack.
        """
        try:
            from instances import template_pack_paths
        except ImportError:
            from lms.instances import template_pack_paths

        existing = self.get_template_library(ontario_code)
        if existing:
            return existing
        tmpl = template_pack_paths(ontario_code, content_root)
        if tmpl.imscc is None or not tmpl.imscc.is_file():
            return None
        return self.create_library(
            ontario_code,
            origin="template",
            source_path=str(tmpl.imscc.resolve()),
        )

    def _library_for_assign(
        self,
        ontario_code: str,
        *,
        content_root: str | None,
        base_offering: dict[str, Any] | None,
        library_id: int | None,
    ) -> dict[str, Any] | None:
        """Pick the shared library a new offering should point at.

        Explicit ``library_id`` (IT upload) wins. Else share the base
        offering's library. Else the git template for this code. Else the
        newest library already stored for this code (typically an upload).

        Args:
            ontario_code: Catalog course code.
            content_root: Catalog template pointer.
            base_offering: Prior offering when IT chose a base layer.
            library_id: Caller-supplied library (new upload).
        """
        if library_id:
            return self.get_library(int(library_id))
        if base_offering and base_offering.get("library_id"):
            return self.get_library(int(base_offering["library_id"]))
        tmpl = self.ensure_template_library(ontario_code, content_root)
        if tmpl:
            return tmpl
        return self.latest_library_for_code(ontario_code)

    def attach_library(
        self, offering_id: int, library_id: int | None
    ) -> dict[str, Any]:
        """Point an offering at a shared library (or clear the pointer).

        Args:
            offering_id: ``course_offerings.id``.
            library_id: ``content_libraries.id``, or None.
        """
        source = None
        if library_id:
            lib = self.get_library(int(library_id))
            if lib:
                source = lib.get("source_path")
        with self._lock:
            self.conn.execute(
                "UPDATE course_offerings SET library_id = ?, imscc_path = ? WHERE id = ?",
                (library_id, source, int(offering_id)),
            )
            self.conn.commit()
        offering = self.get_offering(int(offering_id))
        # New pack attach: re-seed profiles from pack when offering has none yet.
        if not (offering.get("ap_round_profiles_json") or "").strip():
            self.ensure_offering_ap_round_profiles(int(offering_id))
        return self.get_offering(int(offering_id))

    def ensure_offering_ap_round_profiles(self, offering_id: int) -> dict[str, Any]:
        """Return Open Question profiles for an offering, seeding when empty.

        Prefers existing offering JSON, else pack ``ap_round_profiles.json``,
        else the builtin Default profile.

        Args:
            offering_id: ``course_offerings.id``.

        Returns:
            Normalized profiles document.
        """
        try:
            from ap_round_profiles import dump_profiles_json, seed_document_for_offering
        except ImportError:
            from lms.ap_round_profiles import (
                dump_profiles_json,
                seed_document_for_offering,
            )

        offering = self.get_offering(int(offering_id))
        existing = offering.get("ap_round_profiles_json")
        library_id = offering.get("library_id")
        data_dir = Path(getattr(self, "data_dir", self.db_path.parent))
        doc = seed_document_for_offering(
            data_dir,
            int(library_id) if library_id else None,
            existing if isinstance(existing, str) else None,
        )
        serialized = dump_profiles_json(doc)
        if (existing or "").strip() != serialized:
            with self._lock:
                self.conn.execute(
                    """
                    UPDATE course_offerings
                    SET ap_round_profiles_json = ?
                    WHERE id = ?
                    """,
                    (serialized, int(offering_id)),
                )
                self.conn.commit()
        return doc

    def get_offering_ap_round_profiles(self, offering_id: int) -> dict[str, Any]:
        """Load (and seed if needed) AP round action profiles for an offering."""
        return self.ensure_offering_ap_round_profiles(int(offering_id))

    def set_offering_ap_round_profiles(
        self, offering_id: int, document: Any
    ) -> dict[str, Any]:
        """Validate and persist AP round action profiles on an offering.

        Args:
            offering_id: ``course_offerings.id``.
            document: Profiles JSON object (must include ``open`` profiles).

        Returns:
            Normalized saved document.

        Raises:
            ValueError: When validation fails.
            KeyError: Unknown offering.
        """
        try:
            from ap_round_profiles import dump_profiles_json, normalize_profiles_document
        except ImportError:
            from lms.ap_round_profiles import (
                dump_profiles_json,
                normalize_profiles_document,
            )

        self.get_offering(int(offering_id))
        normalized = normalize_profiles_document(document)
        serialized = dump_profiles_json(normalized)
        with self._lock:
            self.conn.execute(
                """
                UPDATE course_offerings
                SET ap_round_profiles_json = ?
                WHERE id = ?
                """,
                (serialized, int(offering_id)),
            )
            self.conn.commit()
        return normalized

    def _attach_leftover_or_template(
        self, offering: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Build a library pointer from a leftover pack or the git template.

        Does not delete leftover ``pack/`` or ``module_packs/<id>/`` copies.

        Args:
            offering: Offering that may already have a leftover IMSCC.
        """
        try:
            from instances import leftover_pack_imscc
        except ImportError:
            from lms.instances import leftover_pack_imscc

        if offering.get("library_id"):
            return self.get_library(int(offering["library_id"]))
        leftover = leftover_pack_imscc(self.data_dir, offering)
        if leftover is not None:
            return self.create_library(
                str(offering["ontario_code"]),
                origin="legacy",
                source_path=str(leftover.resolve()),
            )
        course = self.get_course(str(offering["ontario_code"]))
        tmpl = self.ensure_template_library(
            str(offering["ontario_code"]),
            (course or {}).get("content_root"),
        )
        if tmpl:
            return tmpl
        return self.latest_library_for_code(str(offering["ontario_code"]))

    def ensure_offering_instance(self, offering: dict[str, Any]) -> dict[str, Any]:
        """Create a thin instance and attach a shared library if missing.

        Leftover ``pack/`` / ``module_packs/<id>/`` trees are left on disk
        and become a ``legacy`` library pointer. New assigns do not fork them.

        Args:
            offering: Offering row.

        Returns:
            Offering dict with ``instance_relpath`` and ``library_id``.
        """
        try:
            from instances import migrate_legacy_pack, migrate_legacy_syllabus
        except ImportError:
            from lms.instances import migrate_legacy_pack, migrate_legacy_syllabus

        offering_id = int(offering["id"])
        semester = self.get_semester(int(offering["semester_id"]))
        rel = offering.get("instance_relpath")
        if rel:
            library = self._attach_leftover_or_template(offering)
            if library and not offering.get("library_id"):
                offering = self.attach_library(offering_id, int(library["id"]))
            peers_after = [
                row
                for row in super().list_offerings(semester_id=int(offering["semester_id"]))
                if str(row["ontario_code"]) == str(offering["ontario_code"])
            ]
            migrate_legacy_syllabus(
                self.data_dir,
                offering,
                semester_label=str(semester["label"]),
                peer_count=len(peers_after),
                all_peers_have_instance=all(p.get("instance_relpath") for p in peers_after),
            )
            self.ensure_offering_ap_round_profiles(offering_id)
            return self.get_offering(offering_id)
        course = self.get_course(str(offering["ontario_code"]))
        teacher = self.get_user(int(offering["teacher_user_id"])) or {}
        calendar = semester.get("raw_json") or semester.get("payload")
        library = self._attach_leftover_or_template(offering)
        result = migrate_legacy_pack(
            self.data_dir,
            offering,
            semester_label=str(semester["label"]),
            teacher_name=str(teacher.get("display_name") or teacher.get("email") or ""),
            content_root=(course or {}).get("content_root"),
            calendar=calendar,
            library_id=int(library["id"]) if library else None,
        )
        peers = [
            row
            for row in super().list_offerings(semester_id=int(offering["semester_id"]))
            if str(row["ontario_code"]) == str(offering["ontario_code"])
        ]
        stored_imscc = (library or {}).get("source_path") or result.get("imscc_path")
        self._save_offering_instance(
            offering_id,
            instance_relpath=str(result["instance_relpath"]),
            copied_from_offering_id=(
                int(offering["copied_from_offering_id"])
                if offering.get("copied_from_offering_id")
                else None
            ),
            imscc_path=stored_imscc,
            library_id=int(library["id"]) if library else None,
        )
        updated = self.get_offering(offering_id)
        peers_after = [
            row
            for row in super().list_offerings(semester_id=int(offering["semester_id"]))
            if str(row["ontario_code"]) == str(offering["ontario_code"])
        ]
        migrate_legacy_syllabus(
            self.data_dir,
            updated,
            semester_label=str(semester["label"]),
            peer_count=len(peers),
            all_peers_have_instance=all(p.get("instance_relpath") for p in peers_after),
        )
        self.ensure_offering_ap_round_profiles(offering_id)
        return self.get_offering(offering_id)

    def list_prior_instances(self, ontario_code: str) -> list[dict[str, Any]]:
        """Offerings of this code (any semester, any teacher) for the IT picker.

        Args:
            ontario_code: Catalog course code.
        """
        try:
            from instances import offering_has_pack, year_term_from_label
        except ImportError:
            from lms.instances import offering_has_pack, year_term_from_label

        code = (ontario_code or "").strip().upper()
        out: list[dict[str, Any]] = []
        for item in super().list_offerings():
            if str(item["ontario_code"]).upper() != code:
                continue
            year, term = year_term_from_label(str(item.get("semester_label") or ""))
            out.append(
                {
                    "offering_id": int(item["id"]),
                    "ontario_code": code,
                    "section_index": int(item.get("section_index") or 1),
                    "section_code": item.get("section_code")
                    or section_code(code, item.get("section_index")),
                    "year": year,
                    "term": term,
                    "semester_label": item.get("semester_label"),
                    "teacher_email": item.get("teacher_email"),
                    "teacher_name": item.get("teacher_name"),
                    "teacher_user_id": item.get("teacher_user_id"),
                    "has_pack": bool(item.get("library_id"))
                    or offering_has_pack(self.data_dir, item),
                    "library_id": item.get("library_id"),
                    "instance_relpath": item.get("instance_relpath"),
                }
            )
        return out

    def set_offering_imscc(self, offering_id: int, imscc_path: str) -> dict[str, Any]:
        """Store a cartridge pointer on the offering (legacy column).

        Prefer ``attach_library``. Kept so leftover upload jobs still write
        ``imscc_path`` after unpack.

        Args:
            offering_id: ``course_offerings.id``.
            imscc_path: Absolute or volume-relative path to the ``.imscc``.

        Returns:
            Updated offering dict.
        """
        offering = self.get_offering(offering_id)
        with self._lock:
            self.conn.execute(
                "UPDATE course_offerings SET imscc_path = ? WHERE id = ?",
                (imscc_path, int(offering_id)),
            )
            self.conn.commit()
        return self.get_offering(int(offering["id"]))

    def store_upload_library(
        self,
        ontario_code: str,
        file_storage: Any,
        *,
        offering_id: int | None = None,
    ) -> dict[str, Any]:
        """Validate an IT IMSCC upload into a new shared library folder.

        Stores the zip once under ``libraries/<id>/``. Does not copy into
        any teacher instance. Callers unpack into the same folder.

        Args:
            ontario_code: Catalog course code the pack belongs to.
            file_storage: Werkzeug ``FileStorage``.
            offering_id: If set, attach the new library to that offering.

        Returns:
            Dict with ``library``, ``stored`` (Path), ``dest_root``.
        """
        import shutil

        try:
            from instances import library_root
            from modules import store_uploaded_module_pack
        except ImportError:
            from lms.instances import library_root
            from lms.modules import store_uploaded_module_pack

        # Write straight into the permanent library folder so large cartridges
        # are not saved under ``_incoming`` and then moved again in-request.
        library = self.create_library(ontario_code, origin="upload")
        dest = library_root(self.data_dir, int(library["id"]))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        try:
            stored = store_uploaded_module_pack(file_storage, dest)
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        library = self.set_library_source(int(library["id"]), str(stored))
        if offering_id is not None:
            self.attach_library(int(offering_id), int(library["id"]))
        return {"library": library, "stored": stored, "dest_root": dest}

    def rotate_live_access_code(self, offering_id: int) -> dict[str, Any]:
        """Mint a new shared key for this (semester, course) pair."""
        offering = self.get_offering(offering_id)
        new_code = generate_live_access_code()
        while self.get_offering_by_code(new_code):
            new_code = generate_live_access_code()
        with self._lock:
            self.conn.execute(
                """
                UPDATE course_offerings SET live_access_code = ?
                WHERE semester_id = ? AND ontario_code = ?
                """,
                (new_code, offering["semester_id"], offering["ontario_code"]),
            )
            self.conn.commit()
        return self.get_offering(offering_id)

    def record_code_attempt(self, ip: str) -> int:
        """Log a failed join and return the failure count in the last 10 minutes."""
        return super().record_code_attempt(ip)

    def list_offerings(
        self,
        *,
        teacher_user_id: int | None = None,
        semester_id: int | None = None,
        include_archived: bool = True,
        tenant_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Offerings with class sections and roster sizes.

        Args:
            teacher_user_id: Restrict to one teacher.
            semester_id: Restrict to one semester.
            include_archived: Pass False to hide archived offerings (used by
                the staff dashboard so archived courses disappear for the teacher).
            tenant_id: Restrict to one school seam.
        """
        rows = super().list_offerings(
            semester_id=semester_id,
            teacher_user_id=teacher_user_id,
            include_archived=include_archived,
            tenant_id=tenant_id,
        )
        out = []
        for item in rows:
            item = dict(item)
            item["classes"] = self.classes_for_offering(int(item["id"]))
            item["roster_size"] = sum(
                int(c.get("student_count") or 0) for c in item["classes"]
            )
            out.append(item)
        return out

    def classes_for_offering(self, offering_id: int) -> list[dict[str, Any]]:
        """Game-show sections linked to an offering."""
        with self.game._lock:
            rows = self.game.conn.execute(
                "SELECT * FROM classes WHERE offering_id = ? ORDER BY days, time, id",
                (int(offering_id),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["student_count"] = self.game._student_count(int(item["id"]))
            class_code = str(item.get("live_access_code") or "").strip()
            item["live_access_code"] = class_code or None
            result.append(item)
        return result

    def sync_offering_class_schedule(
        self,
        offering_id: int,
        *,
        stored_days: str,
        live_time: str,
    ) -> None:
        """Update linked game-show classes when Admin changes offering schedule.

        Args:
            offering_id: ``course_offerings.id``.
            stored_days: Stored weekday label (``Mon/Wed/Fri`` / ``Tue/Thu/Fri``).
            live_time: Wizard time label such as ``2:00pm``.
        """
        with self.game._lock:
            self.game.conn.execute(
                """
                UPDATE classes
                SET days = ?, time = ?
                WHERE offering_id = ?
                """,
                (stored_days, live_time, int(offering_id)),
            )
            self.game.conn.commit()

    def list_staff_classes(
        self, teacher_user_id: int, semester_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Classes this teacher populated in the (active) semester."""
        semester = (
            self.get_semester(semester_id)
            if semester_id is not None
            else self.get_active_semester()
        )
        if not semester:
            return []
        with self.game._lock:
            rows = self.game.conn.execute(
                """
                SELECT cl.*, o.live_access_code AS offering_live_access_code,
                       o.ontario_code AS offering_code,
                       o.section_index AS section_index,
                       s.label AS semester_label
                FROM classes cl
                JOIN course_offerings o ON o.id = cl.offering_id
                JOIN semesters s ON s.id = o.semester_id
                WHERE cl.teacher_user_id = ? AND o.semester_id = ?
                ORDER BY cl.course_code, o.section_index, cl.days, cl.time
                """,
                (int(teacher_user_id), int(semester["id"])),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["section_index"] = int(item.get("section_index") or 1)
            item["section_code"] = section_code(
                str(item.get("offering_code") or item.get("course_code") or ""),
                item["section_index"],
            )
            item["student_count"] = self.game._student_count(int(item["id"]))
            class_code = str(item.get("live_access_code") or "").strip()
            item["live_access_code"] = class_code or None
            out.append(item)
        return out

    def teacher_owns_class(self, teacher_user_id: int, class_id: int) -> bool:
        """True when the class belongs to this teacher in the same tenant.

        IT may open any class **in their tenant**, not every class on the
        sqlite file. That is the second-school isolation seam.
        """
        user = self.get_user(teacher_user_id)
        if not user:
            return False
        try:
            cls = self.game.get_class(int(class_id))
        except KeyError:
            return False
        offering_id = cls.get("offering_id")
        if offering_id:
            try:
                offering = self.get_offering(int(offering_id))
            except KeyError:
                return False
            if not self.same_tenant(user, offering):
                return False
            if user.get("role") == "it":
                return True
            return int(offering["teacher_user_id"]) == int(teacher_user_id)
        if user.get("role") == "it":
            return True
        return int(cls.get("teacher_user_id") or 0) == int(teacher_user_id)

    def live_games_for_access_code(self, live_access_code: str) -> list[dict[str, Any]]:
        """Live games for a class join code or shared offering course key."""
        cls = self.game.get_class_by_live_code(live_access_code)
        if cls:
            live = self.game.live_game_for_class(int(cls["id"]))
            return [live] if live else []
        offering = self.get_offering_by_code(live_access_code)
        if not offering:
            return []
        with self.game._lock:
            rows = self.game.conn.execute(
                """
                SELECT g.id AS game_id, g.class_id, g.status, g.session_id,
                       cl.days, cl.time, cl.course_code, cl.offering_id
                FROM games g
                JOIN classes cl ON cl.id = g.class_id
                JOIN course_offerings o ON o.id = cl.offering_id
                WHERE o.semester_id = ? AND o.ontario_code = ? AND g.status = 'live'
                ORDER BY cl.days, cl.time, cl.id
                """,
                (offering["semester_id"], offering["ontario_code"]),
            ).fetchall()
        return [dict(r) for r in rows]

    def classes_for_access_code(self, live_access_code: str) -> list[dict[str, Any]]:
        """Resolve sections by per-class join code, else by offering course key."""
        cls = self.game.get_class_by_live_code(live_access_code)
        if cls:
            return [cls]
        offering = self.get_offering_by_code(live_access_code)
        if not offering:
            return []
        with self.game._lock:
            rows = self.game.conn.execute(
                """
                SELECT cl.* FROM classes cl
                JOIN course_offerings o ON o.id = cl.offering_id
                WHERE o.semester_id = ? AND o.ontario_code = ?
                ORDER BY cl.days, cl.time, cl.id
                """,
                (offering["semester_id"], offering["ontario_code"]),
            ).fetchall()
        return [dict(r) for r in rows]

    def find_class_by_codename(
        self, live_access_code: str, codename: str
    ) -> dict[str, Any] | None:
        """Disambiguate overlapping live sections by roster Codename."""
        name = (codename or "").strip()
        if not name:
            return None
        classes = self.classes_for_access_code(live_access_code)
        matches = []
        with self.game._lock:
            for cls in classes:
                row = self.game.conn.execute(
                    """
                    SELECT id FROM students
                    WHERE class_id = ? AND lower(codename) = ?
                    """,
                    (int(cls["id"]), name.lower()),
                ).fetchone()
                if row:
                    matches.append(cls)
        if len(matches) == 1:
            return matches[0]
        return None

    def find_roster_matches(
        self, live_access_code: str, name: str
    ) -> list[dict[str, Any]]:
        """Return ``{class, student}`` pairs whose Codename matches ``name``.

        Matching is case-insensitive on ``lower(trim(codename))`` across every
        section that shares the student course code.

        Args:
            live_access_code: Shared 8-character offering key.
            name: Student-entered roster name.
        """
        needle = (name or "").strip().lower()
        if not needle:
            return []
        classes = self.classes_for_access_code(live_access_code)
        matches: list[dict[str, Any]] = []
        with self.game._lock:
            for cls in classes:
                row = self.game.conn.execute(
                    """
                    SELECT * FROM students
                    WHERE class_id = ? AND lower(trim(codename)) = ?
                    """,
                    (int(cls["id"]), needle),
                ).fetchone()
                if row:
                    matches.append({"class": cls, "student": dict(row)})
        return matches

    def enrich_class(self, class_payload: dict[str, Any]) -> dict[str, Any]:
        """Attach offering semester label and live access code."""
        offering_id = class_payload.get("offering_id")
        if not offering_id:
            return class_payload
        try:
            offering = self.get_offering(int(offering_id))
        except KeyError:
            return class_payload
        try:
            semester = self.get_semester(int(offering["semester_id"]))
        except KeyError:
            semester = {}
        class_payload["offering_live_access_code"] = offering["live_access_code"]
        class_code = str(class_payload.get("live_access_code") or "").strip()
        class_payload["live_access_code"] = class_code or None
        class_payload["semester_label"] = (semester or {}).get("label")
        class_payload["ontario_code"] = offering["ontario_code"]
        class_payload["section_index"] = int(offering.get("section_index") or 1)
        class_payload["section_code"] = offering.get("section_code") or section_code(
            str(offering["ontario_code"]), offering.get("section_index")
        )
        class_payload["expectations_status"] = offering.get("expectations_status")
        class_payload["imscc_path"] = offering.get("imscc_path")
        class_payload["instance_relpath"] = offering.get("instance_relpath")
        class_payload["copied_from_offering_id"] = offering.get("copied_from_offering_id")
        return class_payload

    def class_live_code_taken(self, live_access_code: str) -> bool:
        """True when a class or offering already uses this 8-character code.

        Args:
            live_access_code: Candidate join code.
        """
        code = (live_access_code or "").strip().upper()
        if not code:
            return True
        if self.game.get_class_by_live_code(code):
            return True
        return bool(self.offerings_for_live_code(code))

    def ensure_class_live_access_code(self, class_id: int) -> dict[str, Any]:
        """Mint a unique durable per-class join code, or return the existing one.

        Prefer ``start_live_class_session`` for Run Live Class: meeting join
        codes are ephemeral session rows. This helper remains for legacy class
        codes that may still exist on older rows.

        Args:
            class_id: Game-show ``classes.id``.

        Returns:
            Enriched class dict including ``live_access_code``.

        Raises:
            KeyError: If the class does not exist.
        """
        cls = self.game.get_class(class_id)
        existing = str(cls.get("live_access_code") or "").strip().upper()
        if existing:
            return self.enrich_class(cls)
        code = generate_live_access_code()
        while self.class_live_code_taken(code):
            code = generate_live_access_code()
        updated = self.game.set_class_live_access_code(class_id, code)
        return self.enrich_class(updated)

    def get_class_by_live_code(self, live_access_code: str) -> dict[str, Any] | None:
        """Resolve a student-typed join code to one class.

        Args:
            live_access_code: Raw or normalized 8-character code.

        Returns:
            Enriched class dict, or ``None`` if the code is not a class key.
        """
        try:
            from codes import normalize_live_access_code
        except ImportError:
            from lms.codes import normalize_live_access_code
        try:
            code = normalize_live_access_code(live_access_code)
        except ValueError:
            return None
        cls = self.game.get_class_by_live_code(code)
        if cls is None:
            return None
        return self.enrich_class(cls)

    def active_session_code_taken(self, session_code: str) -> bool:
        """True when another active live session already uses this code.

        Args:
            session_code: Candidate 8-character session join code.
        """
        code = (session_code or "").strip().upper()
        if not code:
            return True
        with self._lock:
            row = self.conn.execute(
                """
                SELECT id FROM live_class_sessions
                WHERE session_code = ? AND status = 'active'
                LIMIT 1
                """,
                (code,),
            ).fetchone()
        return row is not None

    def mint_unique_active_session_code(self) -> str:
        """Generate an 8-character code unused by any active live session.

        Also avoids colliding with durable offering or class join codes so
        later join resolution stays unambiguous.

        Returns:
            Uppercase 8-character code from ``codes.generate_live_access_code``.
        """
        code = generate_live_access_code()
        while self.active_session_code_taken(code) or self.class_live_code_taken(code):
            code = generate_live_access_code()
        return code

    def get_live_session(self, session_id: int) -> dict[str, Any] | None:
        """Return one live-class session row by id.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM live_class_sessions WHERE id = ?",
                (int(session_id),),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        raw = item.get("slides_json")
        if isinstance(raw, str) and raw:
            try:
                item["slides_json"] = json.loads(raw)
            except json.JSONDecodeError:
                pass
        media_raw = item.get("active_media_json")
        if isinstance(media_raw, str) and media_raw.strip():
            try:
                parsed_media = json.loads(media_raw)
            except json.JSONDecodeError:
                parsed_media = None
            item["active_media"] = public_active_media_payload(
                parsed_media if isinstance(parsed_media, dict) else None
            )
        else:
            item["active_media"] = None
        item.pop("active_media_json", None)
        return item

    def get_active_live_session_for_class(self, class_id: int) -> dict[str, Any] | None:
        """Return the active live session for a class, if any.

        Args:
            class_id: Game-show ``classes.id``.
        """
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM live_class_sessions
                WHERE class_id = ? AND status = 'active'
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(class_id),),
            ).fetchone()
        return dict(row) if row else None

    def get_active_live_session_by_code(
        self, session_code: str
    ) -> dict[str, Any] | None:
        """Resolve an active session by its ephemeral join code.

        Args:
            session_code: Raw or normalized 8-character code.
        """
        try:
            from codes import normalize_live_access_code
        except ImportError:
            from lms.codes import normalize_live_access_code
        try:
            code = normalize_live_access_code(session_code)
        except ValueError:
            return None
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM live_class_sessions
                WHERE session_code = ? AND status = 'active'
                LIMIT 1
                """,
                (code,),
            ).fetchone()
        return dict(row) if row else None

    def list_live_session_attendees(
        self, session_id: int, *, present_only: bool = False
    ) -> list[dict[str, Any]]:
        """Return attendee rows for a live session.

        Args:
            session_id: ``live_class_sessions.id``.
            present_only: When True, omit attendees with ``left_at`` set.
        """
        sql = """
            SELECT * FROM live_session_attendees
            WHERE live_session_id = ?
        """
        if present_only:
            sql += " AND left_at IS NULL"
        sql += " ORDER BY joined_at ASC, id ASC"
        with self._lock:
            rows = self.conn.execute(sql, (int(session_id),)).fetchall()
        return [dict(row) for row in rows]

    def sweep_stale_live_attendees(
        self, session_id: int, *, except_token: str = ""
    ) -> int:
        """Set ``left_at`` when heartbeats have been missing too long.

        Args:
            session_id: ``live_class_sessions.id``.
            except_token: Optional rejoin token to leave present (the caller).

        Returns:
            Number of attendees marked left.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None or session_row.get("status") != "active":
            return 0
        now = datetime.now().replace(microsecond=0)
        skip = (except_token or "").strip()
        marked = 0
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT id, visit_token, left_at, last_heartbeat_at, joined_at
                FROM live_session_attendees
                WHERE live_session_id = ? AND left_at IS NULL
                """,
                (int(session_id),),
            ).fetchall()
            stamp = _now()
            for row in rows:
                token = str(row["visit_token"] or "")
                if skip and token == skip:
                    continue
                last = _parse_iso_datetime(
                    row["last_heartbeat_at"] or row["joined_at"]
                )
                if last is None:
                    continue
                age = (now - last).total_seconds()
                if age < LIVE_HEARTBEAT_STALE_SECONDS:
                    continue
                self.conn.execute(
                    """
                    UPDATE live_session_attendees
                    SET left_at = ?
                    WHERE id = ? AND left_at IS NULL
                    """,
                    (stamp, int(row["id"])),
                )
                marked += 1
            if marked:
                self.conn.commit()
        return marked

    def set_live_session_allow_unmatched_guests(
        self, session_id: int, allowed: bool
    ) -> dict[str, Any]:
        """Persist the mid-session Allow guests checkbox.

        Args:
            session_id: ``live_class_sessions.id``.
            allowed: True when unmatched names may join.

        Returns:
            Updated session row.

        Raises:
            KeyError: If the session is missing.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            raise KeyError(f"live session {session_id}")
        flag = 1 if allowed else 0
        with self._lock:
            self.conn.execute(
                """
                UPDATE live_class_sessions
                SET allow_unmatched_guests = ?
                WHERE id = ?
                """,
                (flag, int(session_id)),
            )
            self.conn.commit()
        updated = self.get_live_session(session_id)
        assert updated is not None
        return updated

    def live_session_allows_unmatched_guests(self, session_id: int) -> bool:
        """True when this live session accepts names that are not on the roster.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            return False
        return bool(int(session_row.get("allow_unmatched_guests") or 0))

    def record_live_session_attendee(
        self,
        session_id: int,
        student_id: int | None = None,
        *,
        codename: str = "",
        visit_token: str = "",
        unmatched: bool = False,
    ) -> dict[str, Any]:
        """Upsert a logical person into the live session (join / resume).

        Mints ``participant_uuid`` and a stable rejoin token once per
        (session × person). Refresh and rejoin keep the same token while the
        session is active. A second device using the same name while the first
        is still present is rejected.

        Args:
            session_id: ``live_class_sessions.id``.
            student_id: Optional roster ``students.id``.
            codename: Display first name / Codename (last names are stripped).
            visit_token: Rejoin cookie or request token when resuming.
            unmatched: True for a guest whose name is not on the roster.

        Returns:
            The attendee row after upsert.

        Raises:
            KeyError: If the session does not exist or is not active.
            ValueError: If this name is already signed in with a different token.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            raise KeyError(f"live session {session_id}")
        if session_row.get("status") != "active":
            raise KeyError(f"live session {session_id} is not active")
        name = first_name_only(codename)
        token_in = (visit_token or "").strip()
        sid = int(student_id) if student_id not in (None, "") else None
        self.sweep_stale_live_attendees(session_id, except_token=token_in)

        if token_in:
            by_token = self._attendee_by_visit_token(token_in)
            if (
                by_token is not None
                and int(by_token["live_session_id"]) == int(session_id)
            ):
                token_name = first_name_only(str(by_token.get("codename") or ""))
                same_person = (not name) or (
                    token_name.lower() == name.lower()
                ) or (
                    sid is not None
                    and by_token.get("student_id") not in (None, "")
                    and int(by_token["student_id"]) == sid
                )
                if same_person:
                    return self._resume_live_attendee(
                        by_token, name=name or token_name
                    )

        existing = None
        if sid is not None:
            existing = self.get_live_session_attendee(session_id, sid)
        elif unmatched and name:
            existing = self._guest_attendee_by_codename(session_id, name)

        if existing is not None:
            existing_token = str(existing.get("visit_token") or "")
            present = not existing.get("left_at")
            if present and token_in and existing_token and token_in != existing_token:
                raise ValueError(
                    "That name’s already in class. If it’s you, reopen the "
                    "tab that’s still open — or wait a beat and try again."
                )
            if present and not token_in:
                raise ValueError(
                    "That name’s already in class. If it’s you, reopen the "
                    "tab that’s still open — or wait a beat and try again."
                )
            return self._resume_live_attendee(existing, name=name or str(existing.get("codename") or ""))

        now = _now()
        participant_uuid = str(uuid.uuid4())
        new_token = secrets.token_urlsafe(24)
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO live_session_attendees (
                    live_session_id, student_id, participant_uuid, visit_token,
                    codename, unmatched, joined_at, left_at, last_heartbeat_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    int(session_id),
                    sid,
                    participant_uuid,
                    new_token,
                    name,
                    1 if unmatched else 0,
                    now,
                    now,
                ),
            )
            self.conn.commit()
            row = self.conn.execute(
                """
                SELECT * FROM live_session_attendees
                WHERE visit_token = ?
                """,
                (new_token,),
            ).fetchone()
        return dict(row) if row else {}

    def _attendee_by_visit_token(self, token: str) -> dict[str, Any] | None:
        """Return an attendee row for a rejoin token, ignoring left/ended.

        Args:
            token: ``live_session_attendees.visit_token``.
        """
        cleaned = (token or "").strip()
        if not cleaned:
            return None
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM live_session_attendees
                WHERE visit_token = ?
                LIMIT 1
                """,
                (cleaned,),
            ).fetchone()
        return dict(row) if row else None

    def _guest_attendee_by_codename(
        self, session_id: int, name: str
    ) -> dict[str, Any] | None:
        """Return the unmatched guest row for this display name, if any.

        Args:
            session_id: ``live_class_sessions.id``.
            name: First-name / Codename already passed through ``first_name_only``.
        """
        needle = first_name_only(name).lower()
        if not needle:
            return None
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM live_session_attendees
                WHERE live_session_id = ?
                  AND unmatched = 1
                  AND lower(codename) = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (int(session_id), needle),
            ).fetchone()
        return dict(row) if row else None

    def _resume_live_attendee(
        self, existing: dict[str, Any], *, name: str = ""
    ) -> dict[str, Any]:
        """Clear ``left_at``, keep uuid + token, refresh heartbeat.

        Args:
            existing: Current attendee row.
            name: Optional display-name refresh (first token only).
        """
        display = first_name_only(name) or str(existing.get("codename") or "")
        now = _now()
        token = str(existing.get("visit_token") or "").strip()
        if not token:
            token = secrets.token_urlsafe(24)
        with self._lock:
            self.conn.execute(
                """
                UPDATE live_session_attendees
                SET left_at = NULL,
                    last_heartbeat_at = ?,
                    visit_token = ?,
                    codename = CASE
                        WHEN ? != '' THEN ? ELSE codename
                    END
                WHERE id = ?
                """,
                (now, token, display, display, int(existing["id"])),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT * FROM live_session_attendees WHERE id = ?",
                (int(existing["id"]),),
            ).fetchone()
        return dict(row) if row else dict(existing)

    def get_live_session_attendee(
        self, session_id: int, student_id: int
    ) -> dict[str, Any] | None:
        """Return one roster-linked attendee row, or ``None`` if not joined.

        Args:
            session_id: ``live_class_sessions.id``.
            student_id: Game-show ``students.id``.
        """
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM live_session_attendees
                WHERE live_session_id = ? AND student_id = ?
                """,
                (int(session_id), int(student_id)),
            ).fetchone()
        return dict(row) if row else None

    def student_is_active_live_attendee(
        self,
        session_id: int,
        student_id: int | None = None,
        *,
        visit_token: str = "",
    ) -> bool:
        """True when the live session is active and this person has not left.

        Args:
            session_id: ``live_class_sessions.id``.
            student_id: Optional roster ``students.id``.
            visit_token: Optional stable rejoin token.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None or session_row.get("status") != "active":
            return False
        attendee = None
        token = (visit_token or "").strip()
        if token:
            attendee = self._attendee_by_visit_token(token)
            if attendee is not None and int(attendee["live_session_id"]) != int(
                session_id
            ):
                attendee = None
        elif student_id not in (None, ""):
            attendee = self.get_live_session_attendee(session_id, int(student_id))
        if attendee is None or attendee.get("left_at"):
            return False
        return True

    def touch_live_session_heartbeat(self, token: str) -> dict[str, Any] | None:
        """Refresh heartbeat and re-admit a still-active session attendee.

        Args:
            token: ``live_session_attendees.visit_token``.

        Returns:
            Updated attendee row, or ``None`` when the token/session is invalid.
        """
        attendee = self._attendee_by_visit_token(token)
        if attendee is None:
            return None
        session_row = self.get_live_session(int(attendee["live_session_id"]))
        if session_row is None or session_row.get("status") != "active":
            return None
        self.sweep_stale_live_attendees(
            int(attendee["live_session_id"]), except_token=str(token)
        )
        return self._resume_live_attendee(attendee)

    def resolve_student_visit_token(
        self, token: str, *, allow_left: bool = False
    ) -> dict[str, Any] | None:
        """Resolve an opaque rejoin token to session + attendee context.

        Args:
            token: ``live_session_attendees.visit_token``.
            allow_left: When True, include attendees with ``left_at`` set so
                cookie auto-resume can re-admit them while the session is live.

        Returns:
            Dict with ``attendee``, ``session``, ``class_id``, ``student_id``
            when the token is valid and the session is still active;
            otherwise ``None``.
        """
        attendee = self._attendee_by_visit_token(token)
        if attendee is None:
            return None
        if attendee.get("left_at") and not allow_left:
            return None
        session_row = self.get_live_session(int(attendee["live_session_id"]))
        if session_row is None or session_row.get("status") != "active":
            return None
        sid = attendee.get("student_id")
        return {
            "attendee": attendee,
            "session": session_row,
            "class_id": int(session_row["class_id"]),
            "student_id": int(sid) if sid not in (None, "") else None,
            "live_session_id": int(attendee["live_session_id"]),
            "participant_uuid": str(attendee.get("participant_uuid") or ""),
            "unmatched": bool(int(attendee.get("unmatched") or 0)),
        }

    def _prompt_row_to_dict(self, row: Any) -> dict[str, Any]:
        """Normalize a ``live_session_prompts`` sqlite row for JSON APIs.

        Args:
            row: sqlite3.Row or mapping.
        """
        payload = dict(row)
        raw = payload.get("payload") or "{}"
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except json.JSONDecodeError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        payload["payload"] = parsed
        payload["active"] = bool(payload.get("active"))
        payload["slide_index"] = int(payload.get("slide_index") or 0)
        return payload

    def get_active_live_prompt(
        self, session_id: int
    ) -> dict[str, Any] | None:
        """Return the active slide prompt for a live session, if any.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM live_session_prompts
                WHERE live_session_id = ? AND active = 1
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (int(session_id),),
            ).fetchone()
        return self._prompt_row_to_dict(row) if row else None

    def set_live_session_prompt(
        self,
        session_id: int,
        *,
        slide_index: int,
        kind: str = "idle",
        payload: dict[str, Any] | None = None,
        activate: bool = True,
    ) -> dict[str, Any]:
        """Upsert a slide-index prompt and optionally make it the active one.

        Args:
            session_id: ``live_class_sessions.id``.
            slide_index: Zero-based slide index from the future slides plugin.
            kind: ``mc``, ``numeric``, ``share``, ``draw``, or ``idle``.
            payload: Kind-specific JSON (choices, prompt text, etc.).
            activate: When True, deactivate other prompts for this session.

        Returns:
            The upserted prompt row.

        Raises:
            KeyError: If the live session is missing.
            ValueError: If ``kind`` is unsupported.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            raise KeyError(f"live session {session_id}")
        kind_norm = (kind or "idle").strip().lower()
        if kind_norm not in {"mc", "numeric", "share", "draw", "idle"}:
            raise ValueError(f"unsupported prompt kind: {kind}")
        if is_c1_cons_payload(payload):
            media = self.live_session_active_media_payload(session_id)
            if not media or not media.get("frozen"):
                raise ValueError("Consolidation is available only after freeze.")
            if not is_c1_real_slice(media):
                raise ValueError(
                    "C1 consolidation is only for the Real-slice channel."
                )
        body = json.dumps(payload or {})
        now = _now()
        with self._lock:
            if activate:
                self.conn.execute(
                    """
                    UPDATE live_session_prompts
                    SET active = 0, updated_at = ?
                    WHERE live_session_id = ? AND active = 1
                    """,
                    (now, int(session_id)),
                )
            self.conn.execute(
                """
                INSERT INTO live_session_prompts (
                    live_session_id, slide_index, kind, payload, active,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(live_session_id, slide_index) DO UPDATE SET
                    kind = excluded.kind,
                    payload = excluded.payload,
                    active = excluded.active,
                    updated_at = excluded.updated_at
                """,
                (
                    int(session_id),
                    int(slide_index),
                    kind_norm,
                    body,
                    1 if activate else 0,
                    now,
                    now,
                ),
            )
            self.conn.commit()
            row = self.conn.execute(
                """
                SELECT * FROM live_session_prompts
                WHERE live_session_id = ? AND slide_index = ?
                """,
                (int(session_id), int(slide_index)),
            ).fetchone()
        return self._prompt_row_to_dict(row) if row else {}

    def clear_active_live_prompt(self, session_id: int) -> None:
        """Deactivate every prompt for a live session (idle shell).

        Args:
            session_id: ``live_class_sessions.id``.
        """
        now = _now()
        with self._lock:
            self.conn.execute(
                """
                UPDATE live_session_prompts
                SET active = 0, updated_at = ?
                WHERE live_session_id = ? AND active = 1
                """,
                (now, int(session_id)),
            )
            self.conn.commit()

    def _class_game_is_live(self, class_id: int) -> bool:
        """True when this class has a live scoring game (not setup/ended).

        Args:
            class_id: Game-show ``classes.id``.
        """
        with self.game._lock:
            row = self.game.conn.execute(
                """
                SELECT status FROM games
                WHERE class_id = ? AND status != 'ended'
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(class_id),),
            ).fetchone()
        return row is not None and str(row["status"] or "") == "live"

    def _session_left_waiting_room(self, session_id: int) -> bool:
        """True when scoring is live, challenge media mounted, or Minds-On ended.

        The Minds-On question is deactivated (or an inactive sentinel is
        written) when Team Challenge media / C2–C3 mounts so lazy-seed cannot
        bring it back.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            return True
        if self._class_game_is_live(int(session_row["class_id"])):
            return True
        media = self.live_session_active_media_payload(session_id)
        if media and media.get("url"):
            return True
        if self._minds_on_row_exists(session_id):
            active = self.get_active_live_prompt(session_id)
            if not (active and is_minds_on_payload(active.get("payload"))):
                return True
        return False

    def _minds_on_row_exists(self, session_id: int) -> bool:
        """True when this session already has a Minds-On prompt row.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        with self._lock:
            row = self.conn.execute(
                """
                SELECT id FROM live_session_prompts
                WHERE live_session_id = ? AND slide_index = ?
                LIMIT 1
                """,
                (int(session_id), int(MINDS_ON_SLIDE_INDEX)),
            ).fetchone()
        return row is not None

    def ensure_waiting_room_minds_on(self, session_id: int) -> dict[str, Any] | None:
        """Seed the Minds-On question if this session is still waiting-room.

        Lazy-seeds existing smoke sessions that started before this prompt
        existed. Refreshes the active waiting-room payload when the
        authoritative stem/choices/key change. Does not recreate the row
        after Team Challenge / scoring cleared it.

        Args:
            session_id: ``live_class_sessions.id``.

        Returns:
            The seeded prompt row, or ``None`` when skipped.
        """
        if self._session_left_waiting_room(session_id):
            return None
        desired = minds_on_prompt_payload()
        active = self.get_active_live_prompt(session_id)
        if active and is_minds_on_payload(active.get("payload")):
            current = active.get("payload") or {}
            if (
                current.get("prompt") == desired["prompt"]
                and current.get("choices") == desired["choices"]
                and current.get("key") == desired.get("key")
            ):
                return None
            return self.set_live_session_prompt(
                session_id,
                slide_index=MINDS_ON_SLIDE_INDEX,
                kind=MINDS_ON_KIND,
                payload=desired,
                activate=True,
            )
        if self._minds_on_row_exists(session_id):
            return None
        return self.set_live_session_prompt(
            session_id,
            slide_index=MINDS_ON_SLIDE_INDEX,
            kind=MINDS_ON_KIND,
            payload=desired,
            activate=True,
        )

    def clear_waiting_room_minds_on(self, session_id: int) -> None:
        """Deactivate the Minds-On question when the waiting-room ends.

        Writes an inactive sentinel row when Minds-On was never seeded so a
        later student poll cannot lazy-seed it after Team Challenge starts.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        active = self.get_active_live_prompt(session_id)
        if active and is_minds_on_payload(active.get("payload")):
            self.clear_active_live_prompt(session_id)
            return
        if self._minds_on_row_exists(session_id):
            return
        if self.get_live_session(session_id) is None:
            return
        self.set_live_session_prompt(
            session_id,
            slide_index=MINDS_ON_SLIDE_INDEX,
            kind=MINDS_ON_KIND,
            payload=minds_on_prompt_payload(),
            activate=False,
        )

    def clear_waiting_room_minds_on_for_class(self, class_id: int) -> None:
        """Deactivate Minds-On on the class's active live session, if any.

        Args:
            class_id: Game-show ``classes.id``.
        """
        live = self.get_active_live_session_for_class(int(class_id))
        if live is None:
            return
        self.clear_waiting_room_minds_on(int(live["id"]))

    def get_live_prompt_response(
        self,
        prompt_id: int,
        student_id: int | None = None,
        *,
        participant_uuid: str = "",
    ) -> dict[str, Any] | None:
        """Return one student's response to a prompt, if any.

        Args:
            prompt_id: ``live_session_prompts.id``.
            student_id: Optional game-show ``students.id``.
            participant_uuid: Preferred live-session person key.
        """
        pid = (participant_uuid or "").strip()
        with self._lock:
            row = None
            if pid:
                row = self.conn.execute(
                    """
                    SELECT * FROM live_session_responses
                    WHERE prompt_id = ? AND participant_uuid = ?
                    """,
                    (int(prompt_id), pid),
                ).fetchone()
            if row is None and student_id not in (None, ""):
                row = self.conn.execute(
                    """
                    SELECT * FROM live_session_responses
                    WHERE prompt_id = ? AND student_id = ?
                    """,
                    (int(prompt_id), int(student_id)),
                ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        raw = payload.get("response_json") or "{}"
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except json.JSONDecodeError:
            parsed = {}
        payload["response"] = parsed if isinstance(parsed, dict) else {}
        return payload

    def submit_live_prompt_response(
        self,
        prompt_id: int,
        student_id: int | None = None,
        response: dict[str, Any] | None = None,
        *,
        participant_uuid: str = "",
    ) -> dict[str, Any]:
        """Upsert a student response for an active-style prompt.

        Keys off ``participant_uuid`` when provided so guests (no roster id)
        can still answer. Does not award points yet.

        Args:
            prompt_id: ``live_session_prompts.id``.
            student_id: Optional game-show ``students.id``.
            response: Student answer JSON (choice, number, share text, …).
            participant_uuid: Live-session person key.

        Returns:
            The response row (with parsed ``response``).

        Raises:
            KeyError: If the prompt does not exist.
            ValueError: If neither uuid nor student_id is provided.
        """
        pid = (participant_uuid or "").strip()
        sid = int(student_id) if student_id not in (None, "") else None
        if not pid and sid is None:
            raise ValueError("participant_uuid or student_id is required")
        with self._lock:
            prompt = self.conn.execute(
                "SELECT id FROM live_session_prompts WHERE id = ?",
                (int(prompt_id),),
            ).fetchone()
        if prompt is None:
            raise KeyError(f"prompt {prompt_id}")
        now = _now()
        body = json.dumps(response or {})
        with self._lock:
            existing = None
            if pid:
                existing = self.conn.execute(
                    """
                    SELECT id FROM live_session_responses
                    WHERE prompt_id = ? AND participant_uuid = ?
                    """,
                    (int(prompt_id), pid),
                ).fetchone()
            if existing is None and sid is not None:
                existing = self.conn.execute(
                    """
                    SELECT id FROM live_session_responses
                    WHERE prompt_id = ? AND student_id = ?
                    """,
                    (int(prompt_id), sid),
                ).fetchone()
            if existing is not None:
                self.conn.execute(
                    """
                    UPDATE live_session_responses
                    SET response_json = ?, updated_at = ?,
                        participant_uuid = COALESCE(?, participant_uuid),
                        student_id = COALESCE(?, student_id)
                    WHERE id = ?
                    """,
                    (body, now, pid or None, sid, int(existing["id"])),
                )
            else:
                self.conn.execute(
                    """
                    INSERT INTO live_session_responses (
                        prompt_id, student_id, participant_uuid, response_json,
                        awarded_points, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (int(prompt_id), sid, pid or None, body, now, now),
                )
            self.conn.commit()
        result = self.get_live_prompt_response(
            prompt_id, sid, participant_uuid=pid
        )
        return result or {}

    def apply_prompt_score_to_participation(
        self,
        class_id: int,
        student_id: int,
        points: float,
        *,
        prompt_id: int | None = None,
        label: str | None = None,
    ) -> None:
        """Stub: future slides plugin writes prompt scores into participation.

        Intentionally a no-op in this pass so gradebook auto-insert stays
        behind a clear hook for the Google Slides plugin branch.

        Args:
            class_id: Game-show ``classes.id``.
            student_id: Game-show ``students.id``.
            points: Points to award once grading is wired.
            prompt_id: Optional ``live_session_prompts.id`` for audit.
            label: Optional human label for the gradebook event.
        """
        # TODO(slides-plugin): insert into participation / session score path.
        _ = (class_id, student_id, points, prompt_id, label)
        return None

    def student_live_prompt_payload(
        self,
        session_id: int,
        student_id: int | None = None,
        *,
        participant_uuid: str = "",
    ) -> dict[str, Any]:
        """Build the student-facing active prompt + prior response fragment.

        Args:
            session_id: ``live_class_sessions.id``.
            student_id: Optional game-show ``students.id``.
            participant_uuid: Live-session person key.

        Returns:
            Dict with ``prompt`` (or ``None``) and optional ``my_response``.
        """
        self.ensure_waiting_room_minds_on(session_id)
        waiting_room = not self._session_left_waiting_room(session_id)
        prompt = self.get_active_live_prompt(session_id)
        empty = {"prompt": None, "my_response": None, "waiting_room": waiting_room}
        if prompt is None or prompt.get("kind") == "idle":
            return empty
        raw_payload = dict(prompt.get("payload") or {})
        if is_minds_on_payload(raw_payload) and not waiting_room:
            return empty
        if is_c1_cons_payload(raw_payload):
            media = self.live_session_active_media_payload(session_id)
            if not media or not media.get("frozen") or not is_c1_real_slice(media):
                return empty
        prior = self.get_live_prompt_response(
            int(prompt["id"]), student_id, participant_uuid=participant_uuid
        )
        my_response = None
        if prior is not None:
            my_response = {
                "response": prior.get("response") or {},
                "awarded_points": prior.get("awarded_points"),
                "updated_at": prior.get("updated_at"),
            }
            fragment = public_feedback_fragment(
                raw_payload, my_response["response"]
            )
            if fragment:
                my_response["feedback"] = fragment
        return {
            "prompt": {
                "id": int(prompt["id"]),
                "slide_index": int(prompt["slide_index"]),
                "kind": str(prompt["kind"]),
                "payload": strip_teacher_prompt_fields(raw_payload),
            },
            "my_response": my_response,
            "waiting_room": waiting_room,
        }

    def live_session_active_media_payload(
        self, session_id: int
    ) -> dict[str, Any] | None:
        """Return the current active-media object for student/staff polls.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            return None
        stored = session_row.get("active_media")
        if isinstance(stored, dict):
            return public_active_media_payload(stored)
        return None

    def set_live_session_active_media(
        self,
        session_id: int,
        *,
        clear: bool = False,
        url: Any = None,
        title: Any = None,
        caption: Any = None,
        stem: Any = None,
        entry_chip: Any = None,
        student_controls_unlocked: Any = None,
        reveal_axes: Any = None,
        reveal_lateral: Any = None,
        allow_3d_limited: Any = None,
        frozen: Any = None,
        unlock_flags: Any = None,
        answers: Any = None,
        params: Any = None,
        challenge: Any = None,
        cons_item: Any = None,
        toast: Any = None,
        toast_key: Any = None,
        allow_url_swap: bool = True,
        merge: bool = False,
    ) -> dict[str, Any] | None:
        """Set, swap, patch control-state, or clear session active media.

        ``merge=True`` (no url / control-state update) keeps the current page
        and overlays unlock flags, quadratic params, or stem/caption.

        Args:
            session_id: ``live_class_sessions.id``.
            clear: Drop media so the student iframe hides.
            url: Same-origin ``/static/...`` path; omitted when ``merge``.
            title: Optional title.
            caption: Optional caption slot (Wonder delight pass).
            stem: Optional student stem.
            entry_chip: Optional entry chip overlay.
            student_controls_unlocked: Teacher unlock for student sliders.
            reveal_axes: Teacher peel for student axes/grid.
            reveal_lateral: In-pane lateral slice reveal (L4).
            allow_3d_limited: Small student yaw after lateral, not free orbit.
            frozen: Argue done; optional click-out encore may appear.
            unlock_flags: Partial L0–L4 flags (delight pass).
            answers: Optional engagement choices for the current reveal.
            params: Optional ``{a,b,c}`` for y = ax^2 + bx + c.
            challenge: ``C1`` / ``C2`` / ``C3``. C2/C3 clear the blob (do not seed).
            cons_item: Post-freeze CONS-1…5 id, or empty to clear.
            toast: Optional Wonder toast overlay.
            toast_key: Optional toast identity.
            allow_url_swap: When False, production seed-locks the Real-slice URL.
            merge: When True, treat omitted url as a patch of current media.

        Returns:
            Public payload, or ``None`` when cleared.

        Raises:
            KeyError: If the live session is missing.
            ValueError: Invalid URL/params, CONS before freeze, or merge with
                no current media.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            raise KeyError(f"live session {session_id}")
        current = self.live_session_active_media_payload(session_id)
        kwargs: dict[str, Any] = {
            "clear": clear,
            "updated_at": _now(),
            "allow_url_swap": allow_url_swap,
        }
        if not merge:
            kwargs["url"] = url
        if title is not None:
            kwargs["title"] = title
        if caption is not None:
            kwargs["caption"] = caption
        if stem is not None:
            kwargs["stem"] = stem
        if entry_chip is not None:
            kwargs["entry_chip"] = entry_chip
        if student_controls_unlocked is not None:
            kwargs["student_controls_unlocked"] = student_controls_unlocked
        if reveal_axes is not None:
            kwargs["reveal_axes"] = reveal_axes
        if reveal_lateral is not None:
            kwargs["reveal_lateral"] = reveal_lateral
        if allow_3d_limited is not None:
            kwargs["allow_3d_limited"] = allow_3d_limited
        if frozen is not None:
            kwargs["frozen"] = frozen
        if unlock_flags is not None:
            kwargs["unlock_flags"] = unlock_flags
        if answers is not None:
            kwargs["answers"] = answers
        if params is not None:
            kwargs["params"] = params
        if challenge is not None:
            kwargs["challenge"] = challenge
        if cons_item is not None:
            kwargs["cons_item"] = cons_item
        if toast is not None:
            kwargs["toast"] = toast
        if toast_key is not None:
            kwargs["toast_key"] = toast_key
        payload = apply_active_media_update(current, **kwargs)
        encoded = json.dumps(payload) if payload else None
        with self._lock:
            self.conn.execute(
                """
                UPDATE live_class_sessions
                SET active_media_json = ?
                WHERE id = ?
                """,
                (encoded, int(session_id)),
            )
            self.conn.commit()
        if payload is not None or challenge_clears_active_media(challenge):
            self.clear_waiting_room_minds_on(session_id)
        self._sync_c1_cons_prompt(session_id, payload)
        return payload

    def _sync_c1_cons_prompt(
        self, session_id: int, media: dict[str, Any] | None
    ) -> None:
        """Push or clear the live-prompt row to match ``cons_item`` after freeze.

        Args:
            session_id: ``live_class_sessions.id``.
            media: Public active-media payload, or ``None`` when cleared.
        """
        wanted = ""
        if media and media.get("frozen") and is_c1_real_slice(media):
            wanted = str(media.get("cons_item") or "").strip()
        active = self.get_active_live_prompt(session_id)
        if not wanted:
            if active and is_c1_cons_payload(active.get("payload")):
                self.clear_active_live_prompt(session_id)
            return
        item = get_c1_cons_item(wanted)
        if item is None:
            if active and is_c1_cons_payload(active.get("payload")):
                self.clear_active_live_prompt(session_id)
            return
        current_id = ""
        if active and is_c1_cons_payload(active.get("payload")):
            current_id = str((active.get("payload") or {}).get("item_id") or "")
        if current_id == item["id"] and active and active.get("kind") == item["kind"]:
            return
        self.set_live_session_prompt(
            session_id,
            slide_index=int(item["slide_index"]),
            kind=str(item["kind"]),
            payload=staff_cons_prompt_payload(item),
            activate=True,
        )

    def mark_live_session_attendee_left(
        self,
        session_id: int,
        student_id: int | None = None,
        *,
        attendee_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Set ``left_at`` for one attendee without ending the session.

        Args:
            session_id: ``live_class_sessions.id``.
            student_id: Optional game-show ``students.id``.
            attendee_id: Optional ``live_session_attendees.id``.
        """
        now = _now()
        with self._lock:
            if attendee_id is not None:
                self.conn.execute(
                    """
                    UPDATE live_session_attendees
                    SET left_at = ?
                    WHERE id = ? AND live_session_id = ? AND left_at IS NULL
                    """,
                    (now, int(attendee_id), int(session_id)),
                )
                self.conn.commit()
                row = self.conn.execute(
                    "SELECT * FROM live_session_attendees WHERE id = ?",
                    (int(attendee_id),),
                ).fetchone()
            elif student_id not in (None, ""):
                self.conn.execute(
                    """
                    UPDATE live_session_attendees
                    SET left_at = ?
                    WHERE live_session_id = ? AND student_id = ? AND left_at IS NULL
                    """,
                    (now, int(session_id), int(student_id)),
                )
                self.conn.commit()
                row = self.conn.execute(
                    """
                    SELECT * FROM live_session_attendees
                    WHERE live_session_id = ? AND student_id = ?
                    """,
                    (int(session_id), int(student_id)),
                ).fetchone()
            else:
                return None
        return dict(row) if row else None

    def clear_attendee_moods_and_characters(self, session_id: int) -> int:
        """Wipe mood check-ins and character picks for session attendees.

        Args:
            session_id: ``live_class_sessions.id``.

        Returns:
            Number of distinct students cleared.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            return 0
        class_id = int(session_row["class_id"])
        attendees = self.list_live_session_attendees(session_id)
        student_ids = [
            int(row["student_id"])
            for row in attendees
            if row.get("student_id") not in (None, "")
        ]
        if not student_ids:
            return 0
        self.game.clear_students_live_presence(class_id, student_ids)
        return len(student_ids)

    def invalidate_live_session_code(self, session_id: int) -> dict[str, Any] | None:
        """Mark a session ended so its code is no longer joinable.

        Idempotent: already-ended sessions are returned unchanged.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            return None
        if session_row.get("status") == "ended":
            return session_row
        now = _now()
        with self._lock:
            self.conn.execute(
                """
                UPDATE live_class_sessions
                SET status = 'ended', ended_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (now, int(session_id)),
            )
            self.conn.execute(
                """
                UPDATE live_session_attendees
                SET left_at = COALESCE(left_at, ?)
                WHERE live_session_id = ? AND left_at IS NULL
                """,
                (now, int(session_id)),
            )
            self.conn.commit()
        return self.get_live_session(session_id)

    def end_live_class_session(
        self, session_id: int, *, clear_moods: bool = True
    ) -> dict[str, Any] | None:
        """End a live session, invalidate its code, and optionally wipe moods.

        Args:
            session_id: ``live_class_sessions.id``.
            clear_moods: When True, clear mood/character for attendees.

        Returns:
            The ended session row, or ``None`` if missing.
        """
        if clear_moods:
            self.clear_attendee_moods_and_characters(session_id)
        return self.invalidate_live_session_code(session_id)

    def end_active_live_sessions_for_class(
        self, class_id: int, *, clear_moods: bool = True
    ) -> list[dict[str, Any]]:
        """End every active live session for a class.

        Args:
            class_id: Game-show ``classes.id``.
            clear_moods: Forwarded to ``end_live_class_session``.

        Returns:
            List of ended session rows (may be empty).
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT id FROM live_class_sessions
                WHERE class_id = ? AND status = 'active'
                ORDER BY id ASC
                """,
                (int(class_id),),
            ).fetchall()
        ended: list[dict[str, Any]] = []
        for row in rows:
            result = self.end_live_class_session(
                int(row["id"]), clear_moods=clear_moods
            )
            if result:
                ended.append(result)
        return ended

    def get_active_live_session_for_teacher(
        self, teacher_user_id: int
    ) -> dict[str, Any] | None:
        """Return the teacher's active live session, if any.

        Args:
            teacher_user_id: Staff ``users.id``.
        """
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM live_class_sessions
                WHERE teacher_user_id = ? AND status = 'active'
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(teacher_user_id),),
            ).fetchone()
        return dict(row) if row else None

    def end_all_active_live_sessions(
        self, *, clear_moods: bool = True
    ) -> list[dict[str, Any]]:
        """End every active live session (ops / stuck cleanup).

        Args:
            clear_moods: Forwarded to ``end_live_class_session``.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT id FROM live_class_sessions
                WHERE status = 'active'
                ORDER BY id ASC
                """
            ).fetchall()
        ended: list[dict[str, Any]] = []
        for row in rows:
            result = self.end_live_class_session(
                int(row["id"]), clear_moods=clear_moods
            )
            if result:
                ended.append(result)
        return ended

    def start_live_class_session(
        self, class_id: int, teacher_user_id: int
    ) -> dict[str, Any]:
        """Mint a new active live session for this teacher.

        A teacher may have only one active session at a time (any course).
        End Class / End Game / Quit must finish the current session before
        Run Live Class can start another.

        Args:
            class_id: Game-show ``classes.id``.
            teacher_user_id: Staff user starting the meeting.

        Returns:
            The newly created active session row.

        Raises:
            KeyError: If the class is missing or has no offering link.
            ValueError: If this teacher already has an active live session.
        """
        cls = self.game.get_class(class_id)
        offering_id = cls.get("offering_id")
        if offering_id is None:
            raise KeyError(f"class {class_id} has no offering_id")
        existing = self.get_active_live_session_for_teacher(int(teacher_user_id))
        if existing is not None:
            if int(existing["class_id"]) == int(class_id):
                return existing
            raise ValueError(
                "You already have a live class running. "
                "Use End Class, End Game, or Quit to finish it before starting another."
            )
        # Drop prior-run mood/character so Mark Attendance starts clean.
        self.game.clear_class_moods_and_characters(int(class_id))
        code = self.mint_unique_active_session_code()
        now = _now()
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT INTO live_class_sessions (
                    class_id, offering_id, teacher_user_id, session_code,
                    status, started_at, ended_at, mgs_session_id
                ) VALUES (?, ?, ?, ?, 'active', ?, NULL, NULL)
                """,
                (
                    int(class_id),
                    int(offering_id),
                    int(teacher_user_id),
                    code,
                    now,
                ),
            )
            self.conn.commit()
            session_id = int(cur.lastrowid)
        session_row = self.get_live_session(session_id)
        assert session_row is not None
        self.ensure_waiting_room_minds_on(session_id)
        return session_row


    def list_active_live_sessions(self) -> list[dict[str, Any]]:
        """Return enriched active sessions for IT / overlay polling.

        Each row includes ``attendee_count``, teacher display name, and course
        section labels when available.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT s.*,
                       u.display_name AS teacher_display_name,
                       u.email AS teacher_email,
                       o.ontario_code AS ontario_code,
                       o.section_index AS section_index,
                       (
                           SELECT COUNT(*)
                           FROM live_session_attendees a
                           WHERE a.live_session_id = s.id AND a.left_at IS NULL
                       ) AS attendee_count
                FROM live_class_sessions s
                LEFT JOIN users u ON u.id = s.teacher_user_id
                LEFT JOIN course_offerings o ON o.id = s.offering_id
                WHERE s.status = 'active'
                ORDER BY s.started_at DESC, s.id DESC
                """
            ).fetchall()
        payload: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["section_code"] = section_code(
                str(item.get("ontario_code") or ""),
                item.get("section_index"),
            )
            item["attendee_count"] = int(item.get("attendee_count") or 0)
            payload.append(item)
        return payload

    def get_live_session_state(self, session_id: int) -> dict[str, Any]:
        """Return overlay/IT state for one live session.

        Args:
            session_id: ``live_class_sessions.id``.

        Returns:
            Dict with ``session``, ``code``, ``count``, ``attendees``, ``phase``.

        Raises:
            KeyError: If the session id is unknown.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            raise KeyError(f"live session {session_id}")
        self.sweep_stale_live_attendees(session_id)
        attendees = self.list_live_session_attendees(session_id)
        moods = self.game.today_moods(int(session_row["class_id"]))
        public_rows: list[dict[str, Any]] = []
        for row in attendees:
            sid = row.get("student_id")
            item = public_live_attendee(row)
            if sid not in (None, ""):
                item["mood"] = moods.get(int(sid))
            else:
                item["mood"] = None
            public_rows.append(item)
        present = [row for row in public_rows if not row.get("left_at")]
        phase = "ended" if session_row.get("status") == "ended" else "live"
        session_public = dict(session_row)
        session_public["allow_unmatched_guests"] = bool(
            int(session_public.get("allow_unmatched_guests") or 0)
        )
        return {
            "session": session_public,
            "code": session_row.get("session_code"),
            "count": len(present),
            "attendees": public_rows,
            "phase": phase,
            "active_media": self.live_session_active_media_payload(session_id),
            "allow_unmatched_guests": session_public["allow_unmatched_guests"],
        }

    def has_active_live_sessions(self) -> bool:
        """True when at least one live class session is currently joinable."""
        with self._lock:
            row = self.conn.execute(
                """
                SELECT 1 FROM live_class_sessions
                WHERE status = 'active'
                LIMIT 1
                """
            ).fetchone()
        return row is not None

    def find_roster_student_for_live_session(
        self, session_id: int, name: str
    ) -> dict[str, Any] | None:
        """Match a Codename against the class roster for one live session.

        Args:
            session_id: ``live_class_sessions.id``.
            name: Student-entered roster username / Codename.

        Returns:
            Student row dict, or ``None`` when the session is missing/ended,
            the name is not on that class roster, or more than one row matches.
        """
        matches = self.list_roster_matches_for_live_session(session_id, name)
        if len(matches) != 1:
            return None
        return matches[0]

    def list_roster_matches_for_live_session(
        self, session_id: int, name: str
    ) -> list[dict[str, Any]]:
        """Roster rows whose Codename or first name matches ``name``.

        Matching is case-insensitive and uses the first token only so last
        names are never required or stored.

        Args:
            session_id: ``live_class_sessions.id``.
            name: Student-entered name.

        Returns:
            Matching student dicts (possibly empty).
        """
        session_row = self.get_live_session(session_id)
        if session_row is None or session_row.get("status") != "active":
            return []
        needle = first_name_only(name).lower()
        if not needle:
            return []
        class_id = int(session_row["class_id"])
        with self.game._lock:
            rows = self.game.conn.execute(
                """
                SELECT * FROM students
                WHERE class_id = ?
                  AND (
                      lower(trim(codename)) = ?
                      OR lower(trim(first_name)) = ?
                  )
                ORDER BY id ASC
                """,
                (class_id, needle, needle),
            ).fetchall()
        return [dict(row) for row in rows]

    def disambiguated_roster_labels(
        self, matches: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Build picker labels for colliding first names without last names.

        Args:
            matches: Roster student rows.

        Returns:
            Dicts with ``student_id``, ``label``, and ``codename``.
        """
        from collections import Counter

        firsts = Counter(
            first_name_only(
                str(row.get("first_name") or row.get("codename") or "")
            ).lower()
            for row in matches
        )
        payload: list[dict[str, Any]] = []
        for row in matches:
            first = first_name_only(str(row.get("first_name") or ""))
            code = first_name_only(str(row.get("codename") or first))
            if firsts[first.lower()] > 1 and code.lower() != first.lower():
                label = f"{first} ({code})"
            elif firsts[first.lower()] > 1:
                label = code or first
            else:
                label = code or first
            payload.append(
                {
                    "student_id": int(row["id"]),
                    "label": label,
                    "codename": code,
                }
            )
        seen: dict[str, int] = {}
        for item in payload:
            key = item["label"].lower()
            seen[key] = seen.get(key, 0) + 1
        if any(count > 1 for count in seen.values()):
            counters: dict[str, int] = {}
            for item in payload:
                key = item["label"].lower()
                if seen[key] > 1:
                    counters[key] = counters.get(key, 0) + 1
                    item["label"] = f"{item['label']} ({counters[key]})"
        return payload

    def guest_student_live_payload(
        self, *, codename: str, class_id: int
    ) -> dict[str, Any]:
        """Minimal student-home payload for an unmatched live-class guest.

        Args:
            codename: Display first name.
            class_id: Class primary key for scoreboard context.

        Returns:
            Same shape as ``student_live_payload`` without roster scoring.
        """
        display = first_name_only(codename)
        try:
            scoreboard = self.game.scoreboard(class_id)
        except Exception:  # noqa: BLE001 - guest home must still render
            scoreboard = {"teams": [], "final": False}
        return {
            "ok": True,
            "status": "waiting",
            "scoring": False,
            "show_rank": False,
            "class_id": int(class_id),
            "round_label": "",
            "round_kind": "",
            "unmatched": True,
            "me": {
                "id": None,
                "codename": display,
                "character": None,
                "mood": None,
                "points": 0,
                "team_name": None,
                "team_points": 0,
            },
            "scoreboard": scoreboard,
        }

    def join_live_class_session(
        self,
        session_id: int,
        student_id: int | None = None,
        *,
        codename: str = "",
        visit_token: str = "",
        unmatched: bool = False,
    ) -> dict[str, Any]:
        """Record a student or guest join and best-effort auto-mark attendance.

        Upserts ``live_session_attendees`` (feeds the live overlay) and, when
        a roster ``student_id`` is present and an open Mark Attendance / live
        game exists, sets that student present on the meeting column.

        Args:
            session_id: ``live_class_sessions.id``.
            student_id: Optional game-show ``students.id``.
            codename: Display first name / Codename.
            visit_token: Stable rejoin token when resuming from cookie.
            unmatched: True for names not on the roster.

        Returns:
            Dict with ``attendee``, ``session``, and ``attendance_marked``.

        Raises:
            KeyError: If the session does not exist or is not active.
            ValueError: If this name is already signed in to the session.
        """
        attendee = self.record_live_session_attendee(
            session_id,
            student_id,
            codename=codename,
            visit_token=visit_token,
            unmatched=unmatched,
        )
        session_row = self.get_live_session(session_id)
        if session_row is None:
            raise KeyError(f"live session {session_id}")
        marked = False
        sid = attendee.get("student_id")
        if sid not in (None, "") and not unmatched:
            marked = self.game.mark_student_present_on_open_session(
                int(session_row["class_id"]), int(sid)
            )
        return {
            "attendee": attendee,
            "session": session_row,
            "attendance_marked": marked,
        }

    def disconnect_live_session_student(
        self,
        session_id: int,
        student_id: int,
        *,
        clear_mood: bool = True,
    ) -> dict[str, Any] | None:
        """Mark one attendee left and optionally wipe mood/character.

        Used on student tab close / logout while the teacher session continues.
        End Game still wipes all attendees via ``end_live_class_session``.

        Args:
            session_id: ``live_class_sessions.id``.
            student_id: Game-show ``students.id``.
            clear_mood: When True, clear this student's mood and character.

        Returns:
            Updated attendee row, or ``None`` if unknown.
        """
        session_row = self.get_live_session(session_id)
        attendee = self.mark_live_session_attendee_left(session_id, student_id)
        if clear_mood and session_row is not None:
            self.game.clear_students_live_presence(
                int(session_row["class_id"]), [int(student_id)]
            )
        return attendee

    def disconnect_live_session_by_visit_token(
        self,
        token: str,
        *,
        clear_mood: bool = True,
    ) -> dict[str, Any] | None:
        """Mark one attendee left by opaque visit token.

        Used when a student tab closes with ``X-Student-Visit-Token`` so other
        tabs in the same browser (shared cookie) are not disconnected.

        Args:
            token: ``live_session_attendees.visit_token``.
            clear_mood: When True, clear this student's mood and character.

        Returns:
            Updated attendee row, or ``None`` when the token is unknown.
        """
        cleaned = (token or "").strip()
        if not cleaned:
            return None
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM live_session_attendees
                WHERE visit_token = ?
                LIMIT 1
                """,
                (cleaned,),
            ).fetchone()
        if row is None:
            return None
        attendee = dict(row)
        if attendee.get("left_at"):
            return attendee
        sid = attendee.get("student_id")
        session_row = self.get_live_session(int(attendee["live_session_id"]))
        updated = self.mark_live_session_attendee_left(
            int(attendee["live_session_id"]),
            int(sid) if sid not in (None, "") else None,
            attendee_id=int(attendee["id"]),
        )
        if clear_mood and session_row is not None and sid not in (None, ""):
            self.game.clear_students_live_presence(
                int(session_row["class_id"]), [int(sid)]
            )
        return updated

    def upsert_document(self, **fields: Any) -> int:
        """Insert or update a curriculum PDF registry row."""
        title = str(fields.get("title") or "").strip()
        with self._lock:
            existing = self.conn.execute(
                "SELECT id FROM curriculum_documents WHERE title = ?", (title,)
            ).fetchone()
            values = (
                title,
                str(fields.get("jurisdiction") or "Ontario"),
                fields.get("grades"),
                fields.get("subject"),
                fields.get("source_url"),
                fields.get("local_path"),
            )
            if existing:
                self.conn.execute(
                    """
                    UPDATE curriculum_documents
                    SET title = ?, jurisdiction = ?, grades = ?, subject = ?,
                        source_url = ?, local_path = ?
                    WHERE id = ?
                    """,
                    (*values, int(existing["id"])),
                )
                doc_id = int(existing["id"])
            else:
                cur = self.conn.execute(
                    """
                    INSERT INTO curriculum_documents
                        (title, jurisdiction, grades, subject, source_url, local_path)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                doc_id = int(cur.lastrowid)
            self.conn.commit()
        return doc_id

    def upsert_ontario_course(
        self,
        code: str,
        title: str,
        *,
        grade: int | None = None,
        pathway: str | None = None,
        document_id: int | None = None,
        content_root: str | None = None,
        expectations_status: str | None = None,
    ) -> None:
        """Insert or update one Ontario course-code catalog row."""
        key = (code or "").strip().upper()
        status = expectations_status or "unverified"
        with self._lock:
            cols = {
                row["name"]
                for row in self.conn.execute("PRAGMA table_info(ontario_courses)")
            }
            status_col = (
                "expectations_status" if "expectations_status" in cols else "verification_status"
            )
            self.conn.execute(
                f"""
                INSERT INTO ontario_courses (
                    code, title, grade, pathway, document_id, content_root, {status_col}
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    title = excluded.title,
                    grade = COALESCE(excluded.grade, ontario_courses.grade),
                    pathway = COALESCE(excluded.pathway, ontario_courses.pathway),
                    document_id = COALESCE(excluded.document_id, ontario_courses.document_id),
                    content_root = COALESCE(excluded.content_root, ontario_courses.content_root)
                """,
                (key, title.strip(), grade, pathway, document_id, content_root, status),
            )
            self.conn.commit()

    def replace_course_expectations(
        self, course_code: str, rows: list[dict[str, Any]], *, status: str
    ) -> int:
        """Replace overall/specific expectation rows for one course."""
        key = course_code.strip().upper()
        count = 0
        with self._lock:
            self.conn.execute("DELETE FROM expectations WHERE course_code = ?", (key,))
            for item in rows:
                statement = str(item.get("statement") or "").strip()
                code = str(item.get("code") or "").strip()
                kind = str(item.get("kind") or "").strip()
                if not statement or not code or kind not in {"overall", "specific"}:
                    continue
                self.conn.execute(
                    """
                    INSERT INTO expectations (
                        course_code, kind, code, parent_code, strand,
                        statement, verification_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        kind,
                        code,
                        item.get("parent_code"),
                        item.get("strand"),
                        statement,
                        str(item.get("verification_status") or status),
                    ),
                )
                count += 1
            cols = {
                row["name"]
                for row in self.conn.execute("PRAGMA table_info(ontario_courses)")
            }
            if "expectations_status" in cols:
                self.conn.execute(
                    "UPDATE ontario_courses SET expectations_status = ? WHERE code = ?",
                    (status, key),
                )
            elif "verification_status" in cols:
                self.conn.execute(
                    "UPDATE ontario_courses SET verification_status = ? WHERE code = ?",
                    (status, key),
                )
            self.conn.commit()
        return count

    def grade_weights_for_class(self, class_id: int) -> dict[str, float]:
        """Return persisted category weights, seeding defaults when missing.

        Args:
            class_id: Game-show / offering class primary key.

        Returns:
            Map of category → weight percent (participation / term / exam).
        """
        try:
            from gradebook import (
                GRADE_CATEGORIES,
                default_grade_weights,
                is_legacy_grade_weights,
                normalize_grade_weights,
            )
        except ImportError:
            from lms.gradebook import (
                GRADE_CATEGORIES,
                default_grade_weights,
                is_legacy_grade_weights,
                normalize_grade_weights,
            )

        defaults = default_grade_weights()
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT category, weight_pct FROM grade_category_weights
                WHERE class_id = ?
                """,
                (int(class_id),),
            ).fetchall()
            if not rows:
                now = _now()
                for category, weight in defaults.items():
                    self.conn.execute(
                        """
                        INSERT INTO grade_category_weights (
                            class_id, category, weight_pct, updated_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (int(class_id), category, float(weight), now),
                    )
                self.conn.commit()
                return defaults
            raw = {str(r["category"]): float(r["weight_pct"]) for r in rows}
            if is_legacy_grade_weights(raw):
                now = _now()
                for category, weight in defaults.items():
                    self.conn.execute(
                        """
                        INSERT INTO grade_category_weights (
                            class_id, category, weight_pct, updated_at
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(class_id, category) DO UPDATE SET
                            weight_pct = excluded.weight_pct,
                            updated_at = excluded.updated_at
                        """,
                        (int(class_id), category, float(weight), now),
                    )
                    raw[category] = float(weight)
                self.conn.commit()
            # Backfill any category added in a later schema revision.
            missing = [c for c in GRADE_CATEGORIES if c not in raw]
            if missing:
                now = _now()
                for category in missing:
                    weight = defaults[category]
                    self.conn.execute(
                        """
                        INSERT OR IGNORE INTO grade_category_weights (
                            class_id, category, weight_pct, updated_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (int(class_id), category, float(weight), now),
                    )
                    raw[category] = weight
                self.conn.commit()
            return normalize_grade_weights(raw)

    def set_grade_weights(
        self, class_id: int, weights: dict[str, Any]
    ) -> dict[str, float]:
        """Persist category weights for a class (extension point for edit UI).

        Args:
            class_id: Class primary key.
            weights: Partial or full category → percent map.

        Returns:
            Normalized stored weights.
        """
        try:
            from gradebook import GRADE_CATEGORIES, normalize_grade_weights
        except ImportError:
            from lms.gradebook import GRADE_CATEGORIES, normalize_grade_weights

        normalized = normalize_grade_weights(weights)
        now = _now()
        with self._lock:
            for category in GRADE_CATEGORIES:
                self.conn.execute(
                    """
                    INSERT INTO grade_category_weights (
                        class_id, category, weight_pct, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(class_id, category) DO UPDATE SET
                        weight_pct = excluded.weight_pct,
                        updated_at = excluded.updated_at
                    """,
                    (int(class_id), category, float(normalized[category]), now),
                )
            self.conn.commit()
        return normalized

    def module_portfolio_rules_for_class(
        self, class_id: int, module_number: int = 1
    ) -> dict[str, Any]:
        """Return stored portfolio thresholds for one module, seeding defaults.

        Args:
            class_id: Class primary key.
            module_number: 1-based module index.

        Returns:
            ``min_sessions``, ``min_r1``, ``min_r3``, ``require_reflections``.
        """
        try:
            from gradebook import (
                default_module_portfolio_rules,
                normalize_module_portfolio_rules,
            )
        except ImportError:
            from lms.gradebook import (
                default_module_portfolio_rules,
                normalize_module_portfolio_rules,
            )

        defaults = default_module_portfolio_rules()
        with self._lock:
            row = self.conn.execute(
                """
                SELECT min_sessions, min_r1, min_r3, require_reflections
                FROM grade_module_rules
                WHERE class_id = ? AND module_number = ?
                """,
                (int(class_id), int(module_number)),
            ).fetchone()
            if row is None:
                now = _now()
                self.conn.execute(
                    """
                    INSERT INTO grade_module_rules (
                        class_id, module_number, min_sessions, min_r1, min_r3,
                        require_reflections, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(class_id),
                        int(module_number),
                        int(defaults["min_sessions"]),
                        float(defaults["min_r1"]),
                        float(defaults["min_r3"]),
                        1 if defaults["require_reflections"] else 0,
                        now,
                    ),
                )
                self.conn.commit()
                return defaults
        return normalize_module_portfolio_rules(
            {
                "min_sessions": row["min_sessions"],
                "min_r1": row["min_r1"],
                "min_r3": row["min_r3"],
                "require_reflections": bool(int(row["require_reflections"] or 0)),
            }
        )

    def set_module_portfolio_rules(
        self, class_id: int, module_number: int, rules: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist portfolio thresholds for one module.

        Args:
            class_id: Class primary key.
            module_number: 1-based module index.
            rules: Partial or full threshold map.

        Returns:
            Normalized stored rules.
        """
        try:
            from gradebook import normalize_module_portfolio_rules
        except ImportError:
            from lms.gradebook import normalize_module_portfolio_rules

        normalized = normalize_module_portfolio_rules(rules)
        now = _now()
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO grade_module_rules (
                    class_id, module_number, min_sessions, min_r1, min_r3,
                    require_reflections, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(class_id, module_number) DO UPDATE SET
                    min_sessions = excluded.min_sessions,
                    min_r1 = excluded.min_r1,
                    min_r3 = excluded.min_r3,
                    require_reflections = excluded.require_reflections,
                    updated_at = excluded.updated_at
                """,
                (
                    int(class_id),
                    int(module_number),
                    int(normalized["min_sessions"]),
                    float(normalized["min_r1"]),
                    float(normalized["min_r3"]),
                    1 if normalized["require_reflections"] else 0,
                    now,
                ),
            )
            self.conn.commit()
        return normalized

    def _assert_module_number(self, module_number: int) -> None:
        """Raise when a scheme module index is outside 1–8.

        Args:
            module_number: 1-based module index.

        Raises:
            ValueError: When the index is not a math-course module.
        """
        try:
            from gradebook import MATH_MODULE_COUNT
        except ImportError:
            from lms.gradebook import MATH_MODULE_COUNT

        if int(module_number) < 1 or int(module_number) > int(MATH_MODULE_COUNT):
            raise ValueError(f"module_number must be 1–{MATH_MODULE_COUNT}")

    def set_grade_scheme(
        self, class_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Save course weights and per-module Term Mark portfolio rules.

        Args:
            class_id: Class primary key.
            payload: ``weights`` plus optional ``modules`` map, or
                ``module_number`` with ``module`` / ``module_1``.

        Returns:
            ``{weights, modules, module_1}`` after persist.

        Raises:
            ValueError: When category percents do not add to 100, or a
                module number is out of range.
        """
        try:
            from gradebook import assert_weights_sum_100, normalize_grade_weights
        except ImportError:
            from lms.gradebook import assert_weights_sum_100, normalize_grade_weights

        raw_weights = payload.get("weights")
        if not isinstance(raw_weights, dict):
            raw_weights = payload
        weights = normalize_grade_weights(raw_weights)
        assert_weights_sum_100(weights)
        self.set_grade_weights(class_id, weights)
        written: dict[int, dict[str, Any]] = {}
        raw_modules = payload.get("modules")
        if isinstance(raw_modules, dict):
            for key, rules in raw_modules.items():
                try:
                    number = int(key)
                except (TypeError, ValueError) as exc:
                    raise ValueError("modules keys must be module numbers") from exc
                self._assert_module_number(number)
                if isinstance(rules, dict):
                    written[number] = self.set_module_portfolio_rules(
                        class_id, number, rules
                    )
        module_raw = payload.get("module")
        if isinstance(module_raw, dict):
            try:
                number = int(
                    payload.get("module_number")
                    or module_raw.get("number")
                    or module_raw.get("module_number")
                    or 1
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("module_number must be an integer") from exc
            self._assert_module_number(number)
            written[number] = self.set_module_portfolio_rules(
                class_id, number, module_raw
            )
        elif isinstance(payload.get("module_1"), dict) and 1 not in written:
            written[1] = self.set_module_portfolio_rules(
                class_id, 1, payload["module_1"]
            )
        return {
            "weights": weights,
            "modules": {str(key): written[key] for key in sorted(written)},
            "module_1": written.get(1)
            or self.module_portfolio_rules_for_class(class_id, 1),
        }

    def get_school_setting(self, key: str, default: str = "") -> str:
        """Return one school-wide setting value.

        Args:
            key: Settings primary key.
            default: Fallback when the row is missing.

        Returns:
            Stored string, or ``default``.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM school_settings WHERE key = ?",
                (str(key),),
            ).fetchone()
        if row is None:
            return default
        return str(row["value"])

    def set_school_setting(self, key: str, value: str) -> dict[str, str]:
        """Upsert one school-wide setting.

        Args:
            key: Settings primary key.
            value: Stored string.

        Returns:
            ``{key, value}``.
        """
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO school_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (str(key), str(value), _now()),
            )
            self.conn.commit()
        return {"key": str(key), "value": str(value)}

    def only_live_class_days(self) -> bool:
        """True when Admin requires live-class-day log validation."""
        try:
            from gradebook import SETTING_ONLY_LIVE_CLASS_DAYS
        except ImportError:
            from lms.gradebook import SETTING_ONLY_LIVE_CLASS_DAYS
        raw = self.get_school_setting(SETTING_ONLY_LIVE_CLASS_DAYS, "0")
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def set_only_live_class_days(self, enabled: bool) -> bool:
        """Persist the live-class-day attendance gate.

        Args:
            enabled: Whether logging is limited to M/W/F or T/Th/F school days.

        Returns:
            The stored flag.
        """
        try:
            from gradebook import SETTING_ONLY_LIVE_CLASS_DAYS
        except ImportError:
            from lms.gradebook import SETTING_ONLY_LIVE_CLASS_DAYS
        self.set_school_setting(SETTING_ONLY_LIVE_CLASS_DAYS, "1" if enabled else "0")
        return enabled

    def staff_2fa_mode(self) -> str:
        """How often staff/IT must complete Resend email 2SV.

        Defaults to first login when the setting has never been saved.
        """
        return normalize_staff_2fa_mode(
            self.get_school_setting(SETTING_STAFF_2FA_MODE, STAFF_2FA_FIRST_LOGIN)
        )

    def set_staff_2fa_mode(self, mode: str) -> str:
        """Persist the staff/admin 2FA cadence.

        Args:
            mode: ``first_login``, ``daily``, or ``every_sign_in``.

        Returns:
            The stored mode.

        Raises:
            ValueError: Unknown mode string.
        """
        value = str(mode or "").strip()
        if value not in STAFF_2FA_MODES:
            raise ValueError(
                "staff_2fa_mode must be first_login, daily, or every_sign_in"
            )
        self.set_school_setting(SETTING_STAFF_2FA_MODE, value)
        return value

    def log_context_for_class(self, class_id: int) -> dict[str, Any]:
        """Calendar + schedule payload for attendance/participation overlays.

        Args:
            class_id: Class primary key.

        Returns:
            Live-day flags, valid picker dates, and Admin gate.
        """
        try:
            from gradebook import (
                is_live_class_date,
                load_instructional_weekdays,
                live_weekday_set,
                short_day_label,
                teacher_weekday_span,
            )
        except ImportError:
            from lms.gradebook import (
                is_live_class_date,
                load_instructional_weekdays,
                live_weekday_set,
                short_day_label,
                teacher_weekday_span,
            )

        cls = self.enrich_class(self.game.get_class(class_id))
        days_label = str(cls.get("days") or "")
        instructional = load_instructional_weekdays()
        school_set = set(instructional)
        today = date.today()
        valid_live = [
            {
                "iso": d.isoformat(),
                "label": f"{short_day_label(d)} · {d.isoformat()}",
            }
            for d in instructional
            if is_live_class_date(
                d, days_label=days_label, instructional=school_set
            )
        ]
        valid_school = [
            {
                "iso": d.isoformat(),
                "label": f"{short_day_label(d)} · {d.isoformat()}",
            }
            for d in instructional
        ]
        gated = self.only_live_class_days()
        span = teacher_weekday_span()
        picker_rows = valid_live if gated else valid_school
        picker_isos = [row["iso"] for row in picker_rows]
        today_iso = today.isoformat()
        if today_iso in picker_isos:
            default_date = today_iso
        else:
            default_date = next(
                (iso for iso in picker_isos if iso >= today_iso),
                picker_isos[0] if picker_isos else today_iso,
            )
        return {
            "ok": True,
            "class": cls,
            "days": days_label,
            "live_weekdays": sorted(live_weekday_set(days_label)),
            "only_live_class_days": gated,
            "today": today.isoformat(),
            "today_is_live": is_live_class_date(
                today, days_label=days_label, instructional=school_set
            ),
            "today_is_school": today in school_set,
            "valid_dates": valid_live if gated else valid_school,
            "valid_live_dates": valid_live,
            "valid_school_dates": valid_school,
            "logged_dates": self._logged_meeting_dates(class_id),
            "first_day": span[0].isoformat() if span else None,
            "last_day": span[-1].isoformat() if span else None,
            "default_date": default_date,
            "suggested_date": self._suggested_log_date(
                class_id, picker_isos, picker_set=set(picker_isos)
            ),
        }

    def _logged_meeting_dates(self, class_id: int) -> list[str]:
        """ISO school days that already have ended attendance/participation.

        Args:
            class_id: Class primary key.

        Returns:
            Sorted unique ``YYYY-MM-DD`` strings for finalized session columns.
        """
        try:
            from gradebook import session_meeting_date
        except ImportError:
            from lms.gradebook import session_meeting_date

        marks = self.game.attendance_score_rows(class_id)
        logged: set[str] = set()
        for sess in marks.get("sessions") or []:
            if str(sess.get("status") or "") != "ended":
                continue
            meeting = session_meeting_date(sess.get("starts_at"))
            if meeting is not None:
                logged.add(meeting.isoformat())
        return sorted(logged)

    def _suggested_log_date(
        self,
        class_id: int,
        picker_isos: list[str],
        *,
        picker_set: set[str] | None = None,
    ) -> str:
        """Next picker-valid school day without finalized attendance.

        In-progress setup sessions are ignored so the next day stays available.

        Args:
            class_id: Class primary key.
            picker_isos: Ordered allowed ISO dates from log context.
            picker_set: Optional prebuilt set of ``picker_isos``.

        Returns:
            ISO date string for the next open attendance slot.
        """
        try:
            from gradebook import session_meeting_date
        except ImportError:
            from lms.gradebook import session_meeting_date

        allowed = picker_set if picker_set is not None else set(picker_isos)
        if not picker_isos:
            return date.today().isoformat()
        marks = self.game.attendance_score_rows(class_id)
        logged_dates: set[str] = set()
        for sess in marks.get("sessions") or []:
            if str(sess.get("status") or "") != "ended":
                continue
            meeting = session_meeting_date(sess.get("starts_at"))
            if meeting is not None:
                logged_dates.add(meeting.isoformat())
        today_iso = date.today().isoformat()
        if logged_dates:
            last_logged = max(logged_dates)
            search = [iso for iso in picker_isos if iso > last_logged]
        else:
            search = [iso for iso in picker_isos if iso >= today_iso]
        if not search:
            search = list(picker_isos)
        for iso in search:
            if iso in allowed and iso not in logged_dates:
                return iso
        for iso in picker_isos:
            if iso in allowed and iso not in logged_dates:
                return iso
        return picker_isos[-1]

    def assert_log_date_allowed(
        self, class_id: int, meeting: date, *, require_live: bool | None = None
    ) -> None:
        """Raise if this meeting date cannot be logged.

        Always rejects non-school days. When the Admin gate is on (or
        ``require_live`` is True), also requires a live-class weekday.

        Args:
            class_id: Class primary key.
            meeting: Chosen session date.
            require_live: Override the Admin setting when not None.

        Raises:
            ValueError: When the date is not allowed.
        """
        try:
            from gradebook import is_live_class_date, load_instructional_weekdays
        except ImportError:
            from lms.gradebook import is_live_class_date, load_instructional_weekdays

        cls = self.enrich_class(self.game.get_class(class_id))
        instructional = set(load_instructional_weekdays())
        if meeting not in instructional:
            raise ValueError(
                f"{meeting.isoformat()} is not a secondary school day "
                "(holiday, PD, weekend, or outside the semester)."
            )
        gated = self.only_live_class_days() if require_live is None else require_live
        if gated and not is_live_class_date(
            meeting, days_label=str(cls.get("days") or ""), instructional=instructional
        ):
            schedule = cls.get("days") or "the course live-class days"
            raise ValueError(
                f"{meeting.isoformat()} is not a live class day for this course "
                f"({schedule}). Pick a valid date."
            )

    def attendance_week_grid(self, class_id: int, sort: str = "az") -> dict[str, Any]:
        """Slim weekday attendance grid for the staff Attendance sub-tab.

        Args:
            class_id: Class primary key.
            sort: Roster sort (``az`` / ``za`` Codename).

        Returns:
            Week-grid payload plus enriched class metadata.
        """
        try:
            from gradebook import build_attendance_week_grid
        except ImportError:
            from lms.gradebook import build_attendance_week_grid

        dash = self.game.dashboard(class_id, sort=sort)
        marks = self.game.attendance_score_rows(class_id)
        grid = build_attendance_week_grid(
            students=list(dash.get("students") or []),
            sessions=marks["sessions"],
            score_rows=marks["scores"],
        )
        grid["class"] = self.enrich_class(dash["class"])
        grid["ok"] = True
        return grid

    def mood_week_grid(self, class_id: int, sort: str = "az") -> dict[str, Any]:
        """Slim weekday mood grid for the staff Mood sub-tab.

        Same calendar shape as attendance; cell values come from student
        check-ins rather than present/absent marks.

        Args:
            class_id: Class primary key.
            sort: Roster sort (``az`` / ``za`` Codename).
        """
        try:
            from gradebook import build_attendance_week_grid
        except ImportError:
            from lms.gradebook import build_attendance_week_grid

        dash = self.game.dashboard(class_id, sort=sort)
        marks = self.game.attendance_score_rows(class_id)
        grid = build_attendance_week_grid(
            students=list(dash.get("students") or []),
            sessions=marks["sessions"],
            score_rows=marks["scores"],
        )
        mood = self.game.mood_cells(class_id)
        grid["cells"] = mood.get("cells") or {}
        grid["totals"] = mood.get("totals") or {}
        grid["day_totals"] = mood.get("day_totals") or {}
        grid["class"] = self.enrich_class(dash["class"])
        grid["ok"] = True
        return grid

    def attendance_for_date(self, class_id: int, meeting: date) -> dict[str, Any]:
        """Present/absent ids logged on a school day (from finalized sessions).

        Args:
            class_id: Class primary key.
            meeting: Calendar date to inspect.

        Returns:
            ``present_ids``, ``absent_ids``, ``logged``, optional ``session_id``.
        """
        try:
            from gradebook import session_meeting_date
        except ImportError:
            from lms.gradebook import session_meeting_date

        marks = self.game.attendance_score_rows(class_id)
        session_dates: dict[int, date] = {}
        session_ids: list[int] = []
        for sess in marks["sessions"]:
            if str(sess.get("status") or "") == "template":
                continue
            md = session_meeting_date(sess.get("starts_at"))
            if md == meeting:
                sid = int(sess["id"])
                session_dates[sid] = md
                session_ids.append(sid)
        if not session_ids:
            return {
                "ok": True,
                "date": meeting.isoformat(),
                "logged": False,
                "present_ids": [],
                "absent_ids": [],
                "session_id": None,
            }
        present: set[int] = set()
        absent: set[int] = set()
        for row in marks["scores"]:
            sess_id = int(row["session_id"])
            if sess_id not in session_dates:
                continue
            sid = int(row["student_id"])
            if int(row.get("present") or 0) == 1:
                present.add(sid)
            else:
                absent.add(sid)
        return {
            "ok": True,
            "date": meeting.isoformat(),
            "logged": True,
            "present_ids": sorted(present),
            "absent_ids": sorted(absent),
            "session_id": session_ids[0] if len(session_ids) == 1 else None,
        }

    def clear_attendance_day(
        self, class_id: int, meeting: date, sort: str = "az"
    ) -> dict[str, Any]:
        """Remove attendance/participation logged on one school day.

        Deletes every non-template session column whose meeting date matches.

        Args:
            class_id: Class primary key.
            meeting: School day to clear.
            sort: Roster sort for the returned grid.

        Returns:
            Updated attendance week grid payload.
        """
        try:
            from gradebook import session_meeting_date
        except ImportError:
            from lms.gradebook import session_meeting_date

        marks = self.game.attendance_score_rows(class_id)
        to_delete: list[int] = []
        for sess in marks["sessions"]:
            if str(sess.get("status") or "") == "template":
                continue
            md = session_meeting_date(sess.get("starts_at"))
            if md == meeting:
                to_delete.append(int(sess["id"]))
        for sess_id in to_delete:
            self.game.delete_session_column(class_id, sess_id, sort=sort)
        return self.attendance_week_grid(class_id, sort=sort)

    def participation_week_grid(self, class_id: int, sort: str = "az") -> dict[str, Any]:
        """Semester calendar participation grid (same columns as attendance).

        Args:
            class_id: Class primary key.
            sort: Roster sort (``az`` / ``za`` Codename).

        Returns:
            Week grid with point cells plus live/total rollups from dashboard.
        """
        try:
            from gradebook import build_participation_week_grid
        except ImportError:
            from lms.gradebook import build_participation_week_grid

        dash = self.game.dashboard(class_id, sort=sort)
        marks = self.game.attendance_score_rows(class_id)
        score_rows: list[dict[str, Any]] = []
        cells = dash.get("cells") or {}
        for sess in marks["sessions"]:
            sess_id = int(sess["id"])
            for student in dash.get("students") or []:
                stid = int(student["id"])
                cell = cells.get(f"{sess_id}:{stid}")
                if not cell:
                    continue
                score_rows.append(
                    {
                        "session_id": sess_id,
                        "student_id": stid,
                        "points": cell.get("points", 0),
                        "points_r1": cell.get("points_r1", 0),
                        "points_r2": cell.get("points_r2", 0),
                        "points_r3": cell.get("points_r3", 0),
                    }
                )
        grid = build_participation_week_grid(
            students=list(dash.get("students") or []),
            sessions=list(marks.get("sessions") or []),
            score_rows=score_rows,
        )
        grid["class"] = self.enrich_class(dash["class"])
        grid["live_subtotals"] = dash.get("live_subtotals") or {}
        grid["totals"] = dash.get("totals") or {}
        grid["ok"] = True
        return grid

    def _class_tracker_rows(
        self, class_id: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Sessions plus present/late/round-point rows for portfolio criteria.

        Args:
            class_id: Class primary key.

        Returns:
            ``(sessions, score_rows)`` from the live-class tracker DB.
        """
        with self.game._lock:
            sessions = [
                dict(row)
                for row in self.game.conn.execute(
                    """
                    SELECT id, starts_at, status FROM sessions
                    WHERE class_id = ?
                    ORDER BY starts_at ASC, id ASC
                    """,
                    (int(class_id),),
                )
            ]
            scores = [
                dict(row)
                for row in self.game.conn.execute(
                    """
                    SELECT ss.session_id, ss.student_id, ss.present, ss.late,
                           ss.points, ss.points_r1, ss.points_r2, ss.points_r3
                    FROM session_scores ss
                    JOIN sessions se ON se.id = ss.session_id
                    WHERE se.class_id = ?
                    """,
                    (int(class_id),),
                )
            ]
        return sessions, scores

    def module_reflections_for_class(
        self, class_id: int, module_number: int
    ) -> dict[int, bool]:
        """Return per-student reflection-complete flags for one module.

        Args:
            class_id: Class primary key.
            module_number: 1-based module index.

        Returns:
            Map of student_id → complete.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT student_id, complete FROM module_reflection_flags
                WHERE class_id = ? AND module_number = ?
                """,
                (int(class_id), int(module_number)),
            ).fetchall()
        return {int(r["student_id"]): bool(int(r["complete"] or 0)) for r in rows}

    def set_module_reflection(
        self,
        class_id: int,
        student_id: int,
        module_number: int,
        complete: bool,
    ) -> dict[str, Any]:
        """Persist the post-activity reflections flag for one student/module.

        Args:
            class_id: Class primary key.
            student_id: Game-show student id.
            module_number: 1-based module index.
            complete: Whether all reflection questions are answered.

        Returns:
            Stored flag row.
        """
        now = _now()
        flag = 1 if complete else 0
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO module_reflection_flags (
                    class_id, student_id, module_number, complete, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(class_id, student_id, module_number) DO UPDATE SET
                    complete = excluded.complete,
                    updated_at = excluded.updated_at
                """,
                (int(class_id), int(student_id), int(module_number), flag, now),
            )
            self.conn.commit()
        return {
            "class_id": int(class_id),
            "student_id": int(student_id),
            "module_number": int(module_number),
            "complete": bool(flag),
        }

    def gradebook_for_class(self, class_id: int, sort: str = "az") -> dict[str, Any]:
        """Ontario-weighted gradebook with Module 1 portfolio auto-score.

        Term 65% includes the Module 1 portfolio (100% when live-class
        criteria are met) and a placeholder Module 1 test. Att &
        Participation 10% and Exam 25% stay placeholders until those
        marks are entered. Live-class point totals remain as diagnostics.

        Args:
            class_id: Class primary key.
            sort: Roster sort key.

        Returns:
            Gradebook JSON for the staff Grades tab.
        """
        try:
            from gradebook import (
                GRADE_CATEGORY_LABELS,
                evaluate_module_portfolio,
                even_module_windows,
                gradebook_overview_text,
                load_instructional_weekdays,
                module_window_for,
            )
        except ImportError:
            from lms.gradebook import (
                GRADE_CATEGORY_LABELS,
                evaluate_module_portfolio,
                even_module_windows,
                gradebook_overview_text,
                load_instructional_weekdays,
                module_window_for,
            )

        weights = self.grade_weights_for_class(class_id)
        m1_rules = self.module_portfolio_rules_for_class(class_id, 1)
        dash = self.game.dashboard(class_id, sort=sort)
        students = list(dash.get("students") or [])
        cls = self.enrich_class(dash["class"])
        days_label = str(cls.get("days") or "")
        instructional = set(load_instructional_weekdays())
        sessions, score_rows = self._class_tracker_rows(class_id)
        windows = even_module_windows()
        m1 = module_window_for(1) or (windows[0] if windows else None)
        reflections = self.module_reflections_for_class(class_id, 1)
        career = self.game.career_totals(class_id)

        portfolio_by_student: dict[str, dict[str, Any]] = {}
        portfolio_scores: dict[str, float | None] = {}
        term_scores: dict[str, float | None] = {}
        for student in students:
            sid = int(student["id"])
            key = str(sid)
            if m1 is None:
                detail = {
                    "module": 1,
                    "earned_100": False,
                    "score": None,
                    "pending_reason": "Module window is not available",
                }
            else:
                detail = evaluate_module_portfolio(
                    student_id=sid,
                    window=m1,
                    days_label=days_label,
                    instructional=instructional,
                    sessions=sessions,
                    score_rows=score_rows,
                    reflections_complete=bool(reflections.get(sid)),
                    rules=m1_rules,
                )
            portfolio_by_student[key] = detail
            portfolio_scores[key] = detail.get("score")
            term_scores[key] = detail.get("score")

        test_scores = {str(int(s["id"])): None for s in students}
        participation_points = {
            str(int(s["id"])): float(career.get(int(s["id"])) or 0) for s in students
        }
        participation_pct = {str(int(s["id"])): None for s in students}
        exam_scores = {str(int(s["id"])): None for s in students}
        overview = gradebook_overview_text(
            weights=weights, window=m1, rules=m1_rules
        )
        scheme_modules = []
        for win in windows:
            number = int(win.get("number") or 0)
            if number < 1:
                continue
            rules = (
                m1_rules
                if number == 1
                else self.module_portfolio_rules_for_class(class_id, number)
            )
            scheme_modules.append(
                {
                    "number": number,
                    "start": win.get("start"),
                    "end": win.get("end"),
                    "window": win,
                    **rules,
                    "editable": True,
                }
            )

        return {
            "ok": True,
            "class": cls,
            "students": students,
            "weights": weights,
            "overview": overview,
            "scheme": {
                "weights": weights,
                "module_1": {
                    **m1_rules,
                    "window": m1,
                    "editable": True,
                },
                "modules": scheme_modules,
            },
            "policy": {
                "source": "Ontario Ministry of Education",
                "term_pct": weights["term"],
                "exam_pct": weights["exam"],
                "participation_pct": weights["participation"],
                "term_includes": "Tests and assignments (module portfolios)",
            },
            "modules": windows,
            "module_1": {
                "window": m1,
                "rules": m1_rules,
                "criteria": [
                    {
                        "id": "sessions",
                        "label": (
                            f"{int(m1_rules['min_sessions'])}+ live classes or "
                            "Friday open offices"
                        ),
                        "threshold": int(m1_rules["min_sessions"]),
                    },
                    {
                        "id": "reflections",
                        "label": "All reflection questions after group activities",
                    },
                    {
                        "id": "r1",
                        "label": (
                            f"{m1_rules['min_r1']:g}+ Open Question (Round 1) points"
                        ),
                        "threshold": m1_rules["min_r1"],
                    },
                    {
                        "id": "r3",
                        "label": (
                            f"{m1_rules['min_r3']:g}+ Formative (Round 3) points"
                        ),
                        "threshold": m1_rules["min_r3"],
                    },
                ],
                "students": portfolio_by_student,
            },
            "term_items": [
                {
                    "id": "m1_portfolio",
                    "label": "M1 Portfolio",
                    "module": 1,
                    "kind": "portfolio",
                    "auto": True,
                    "scores": portfolio_scores,
                },
                {
                    "id": "m1_test",
                    "label": "M1 Test",
                    "module": 1,
                    "kind": "test",
                    "placeholder": True,
                    "scores": test_scores,
                },
            ],
            "categories": [
                {
                    "id": "participation",
                    "label": GRADE_CATEGORY_LABELS["participation"],
                    "weight_pct": weights["participation"],
                    "scores": participation_pct,
                    "points": participation_points,
                    "placeholder": True,
                    "editable_weights": True,
                },
                {
                    "id": "term",
                    "label": GRADE_CATEGORY_LABELS["term"],
                    "weight_pct": weights["term"],
                    "scores": term_scores,
                    "incomplete": True,
                    "editable_weights": True,
                },
                {
                    "id": "exam",
                    "label": GRADE_CATEGORY_LABELS["exam"],
                    "weight_pct": weights["exam"],
                    "scores": exam_scores,
                    "items": [],
                    "placeholder": True,
                    "editable_weights": True,
                },
            ],
            "weight_edit_endpoint": f"/api/classes/{int(class_id)}/grade-weights",
            "scheme_edit_endpoint": f"/api/classes/{int(class_id)}/grade-scheme",
            "reflection_edit_endpoint": (
                f"/api/classes/{int(class_id)}/module-reflections"
            ),
        }
