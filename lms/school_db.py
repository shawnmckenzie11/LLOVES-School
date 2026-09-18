"""School sqlite for LLOVES users, semesters, catalog, and course offerings."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from codes import generate_live_access_code
    from live_media import (
        apply_active_media_update,
        challenge_clears_active_media,
        cons_unlock_toast,
        TOAST_FREEZE,
        cons_catalog,
        get_cons_item,
        is_c1_real_slice,
        is_cons_payload,
        public_active_media_payload,
        staff_cons_prompt_payload,
    )
    from live_canvas import (
        apply_canvas_presence,
        canvas_view_for,
        cursor_color_for,
        public_canvas_sync,
    )
    from live_mc import build_live_tally, build_mc_tally
    from live_class_metadata import (
        SCHEMA_V2,
        _placement_sort_key,
        load_live_class_metadata,
        questions_for_stage,
    )
    from live_teacher_state import (
        CUE_CONS_UNLOCK,
        CUE_FREEZE,
        apply_teacher_state_update,
        bind_meet_student_projection,
        default_student_view,
        default_text_ride,
        mc_poll_closed,
        normalize_live_module,
        normalize_live_slot,
        public_teacher_state,
        public_text_ride,
        student_mc_summary_visible,
        unlocks_from_student_view,
    )
    from live_prompt_feedback import (
        choice_letter,
        strip_teacher_prompt_fields,
    )
    from meet_team import (
        CUE_MEET_CLEAR,
        CUE_MEET_OPEN,
        MEET_STEP_SLIDE,
        MEET_TEAM_KIND,
        MEET_TEAM_SLIDE_INDEX,
        advance_meet_chain,
        current_meet_step,
        is_meet_team_payload,
        meet_chip_for,
        meet_participant_key,
        meet_payload_for_state,
        meet_prompt_ref_for,
        meet_team_prompt_payload,
        new_meet_chain_state,
        public_meet_chain,
        record_meet_pick,
        skip_meet_c,
    )
    from minds_on import (
        MINDS_ON_KIND,
        MINDS_ON_SLIDE_INDEX,
        is_minds_on_payload,
        minds_on_prompt_payload,
    )
    from teams_spark import (
        CUE_TEAMS_SPARK,
        TEAMS_SPARK_KIND,
        TEAMS_SPARK_SLIDE_INDEX,
        is_teams_spark_payload,
        staff_teams_spark_card,
        teams_spark_prompt_payload,
    )
    from game_show_welcome import game_show_welcome_payload
    from team_challenge import (
        TEAM_CHALLENGE_KIND,
        TEAM_CHALLENGE_SLIDE_INDEX,
        is_team_challenge_payload,
        resolve_team_challenge,
        staff_team_challenge_prompt_payload,
        live_class_seed_media,
        uses_c1_real_slice,
    )
    from paths import GAME_SHOW, SEMESTER_JSON
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.codes import generate_live_access_code
    from lms.live_media import (
        apply_active_media_update,
        challenge_clears_active_media,
        cons_unlock_toast,
        TOAST_FREEZE,
        cons_catalog,
        get_cons_item,
        is_c1_real_slice,
        is_cons_payload,
        public_active_media_payload,
        staff_cons_prompt_payload,
    )
    from lms.live_canvas import (
        apply_canvas_presence,
        canvas_view_for,
        cursor_color_for,
        public_canvas_sync,
    )
    from lms.live_mc import build_live_tally, build_mc_tally
    from lms.live_class_metadata import (
        SCHEMA_V2,
        _placement_sort_key,
        load_live_class_metadata,
        questions_for_stage,
    )
    from lms.live_teacher_state import (
        CUE_CONS_UNLOCK,
        CUE_FREEZE,
        apply_teacher_state_update,
        bind_meet_student_projection,
        default_student_view,
        default_text_ride,
        mc_poll_closed,
        normalize_live_module,
        normalize_live_slot,
        public_teacher_state,
        public_text_ride,
        student_mc_summary_visible,
        unlocks_from_student_view,
    )
    from lms.live_prompt_feedback import (
        choice_letter,
        strip_teacher_prompt_fields,
    )
    from lms.meet_team import (
        CUE_MEET_CLEAR,
        CUE_MEET_OPEN,
        MEET_STEP_SLIDE,
        MEET_TEAM_KIND,
        MEET_TEAM_SLIDE_INDEX,
        advance_meet_chain,
        current_meet_step,
        is_meet_team_payload,
        meet_chip_for,
        meet_participant_key,
        meet_payload_for_state,
        meet_prompt_ref_for,
        meet_team_prompt_payload,
        new_meet_chain_state,
        public_meet_chain,
        record_meet_pick,
        skip_meet_c,
    )
    from lms.minds_on import (
        MINDS_ON_KIND,
        MINDS_ON_SLIDE_INDEX,
        is_minds_on_payload,
        minds_on_prompt_payload,
    )
    from lms.teams_spark import (
        CUE_TEAMS_SPARK,
        TEAMS_SPARK_KIND,
        TEAMS_SPARK_SLIDE_INDEX,
        is_teams_spark_payload,
        staff_teams_spark_card,
        teams_spark_prompt_payload,
    )
    from lms.game_show_welcome import game_show_welcome_payload
    from lms.team_challenge import (
        TEAM_CHALLENGE_KIND,
        TEAM_CHALLENGE_SLIDE_INDEX,
        is_team_challenge_payload,
        resolve_team_challenge,
        staff_team_challenge_prompt_payload,
        live_class_seed_media,
        uses_c1_real_slice,
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

CREATE TABLE IF NOT EXISTS live_session_items (
    id INTEGER PRIMARY KEY,
    live_session_id INTEGER NOT NULL
        REFERENCES live_class_sessions(id) ON DELETE CASCADE,
    placement_key TEXT NOT NULL,
    item_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    page_number INTEGER,
    sort_order INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'poll',
    item_json TEXT NOT NULL DEFAULT '{}',
    prompt_id INTEGER
        REFERENCES live_session_prompts(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'inactive',
    publish_mode TEXT NOT NULL DEFAULT 'individual',
    response_mode TEXT NOT NULL DEFAULT 'individual',
    show_live_results INTEGER NOT NULL DEFAULT 1,
    published_at TEXT,
    closed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(live_session_id, placement_key)
);

CREATE INDEX IF NOT EXISTS idx_live_session_items_status
    ON live_session_items(live_session_id, status, sort_order, id);

CREATE TABLE IF NOT EXISTS live_group_members (
    live_item_id INTEGER NOT NULL
        REFERENCES live_session_items(id) ON DELETE CASCADE,
    team_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    joined_at TEXT NOT NULL,
    PRIMARY KEY(live_item_id, team_id, student_id)
);

CREATE TABLE IF NOT EXISTS live_group_votes (
    id INTEGER PRIMARY KEY,
    live_item_id INTEGER NOT NULL
        REFERENCES live_session_items(id) ON DELETE CASCADE,
    team_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    response_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(live_item_id, team_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_live_group_votes_team
    ON live_group_votes(live_item_id, team_id);

CREATE TABLE IF NOT EXISTS live_group_responses (
    id INTEGER PRIMARY KEY,
    live_item_id INTEGER NOT NULL
        REFERENCES live_session_items(id) ON DELETE CASCADE,
    team_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'collecting_votes',
    vote_summary_json TEXT NOT NULL DEFAULT '[]',
    proposed_answer_json TEXT,
    final_answer_json TEXT,
    finalizer_student_id INTEGER,
    awarded_points REAL,
    voting_ended_at TEXT,
    finalized_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(live_item_id, team_id)
);

CREATE INDEX IF NOT EXISTS idx_live_group_responses_item
    ON live_group_responses(live_item_id, status, team_id);

CREATE TABLE IF NOT EXISTS live_class_feedback (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL,
    offering_id INTEGER,
    student_id INTEGER,
    participant_uuid TEXT,
    codename TEXT NOT NULL DEFAULT '',
    meeting_date TEXT,
    token TEXT NOT NULL UNIQUE,
    mood TEXT,
    before_mood TEXT,
    live_module TEXT,
    live_slot TEXT,
    comment TEXT,
    submitted_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_live_class_feedback_class
    ON live_class_feedback(class_id, submitted_at);

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
# Skip a write when the last beat is newer than this (class-size sqlite load).
LIVE_HEARTBEAT_WRITE_SECONDS = 20
LIVE_SWEEP_MIN_INTERVAL_SECONDS = 20
SQLITE_BUSY_TIMEOUT_MS = 30_000


def configure_sqlite_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Wait on writers instead of failing immediately under class load.

    Args:
        conn: Open sqlite connection.

    Returns:
        The same connection after busy-timeout and WAL are set.
    """
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def retry_if_db_locked(fn, *, attempts: int = 4, delay_s: float = 0.4):
    """Retry a sqlite write when the class-size poll storm holds the lock.

    Args:
        fn: Zero-arg callable.
        attempts: Including the first try.
        delay_s: Base sleep, multiplied by the attempt index.

    Returns:
        ``fn()`` result.

    Raises:
        sqlite3.OperationalError: Re-raised after the last attempt.
    """
    last_exc: sqlite3.OperationalError | None = None
    for index in range(max(1, int(attempts))):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if "locked" not in str(exc).lower() or index == attempts - 1:
                raise
            time.sleep(delay_s * (index + 1))
    assert last_exc is not None
    raise last_exc

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
        self._sweep_at: dict[int, float] = {}
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            str(db_path), check_same_thread=False, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000
        )
        configure_sqlite_connection(self.conn)
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
        self._ensure_module_bank_schema()
        self._ensure_archived_column()
        self._ensure_gradebook_schema()
        self._ensure_live_session_schema()
        self._ensure_live_session_identity_schema()
        self._ensure_live_item_schema()
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

    def _ensure_module_bank_schema(self) -> None:
        """Create module-bank links, class playlist overlays, and question overlays."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS course_module_bank_links (
                library_id INTEGER NOT NULL
                    REFERENCES content_libraries(id) ON DELETE CASCADE,
                module_number INTEGER NOT NULL,
                bank_id INTEGER NOT NULL
                    REFERENCES question_banks(id) ON DELETE CASCADE,
                confirmed_at TEXT NOT NULL,
                PRIMARY KEY (library_id, module_number, bank_id)
            );
            CREATE INDEX IF NOT EXISTS idx_module_bank_links_library_module
                ON course_module_bank_links(library_id, module_number);

            CREATE TABLE IF NOT EXISTS class_live_playlist_placements (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                module TEXT NOT NULL,
                slot TEXT NOT NULL,
                page_number INTEGER NOT NULL DEFAULT 1,
                stage TEXT NOT NULL DEFAULT 'round',
                sort_order INTEGER NOT NULL DEFAULT 0,
                placement_key TEXT NOT NULL,
                item_id TEXT NOT NULL,
                item_json TEXT NOT NULL DEFAULT '{}',
                source_question_id INTEGER,
                created_at TEXT NOT NULL,
                UNIQUE(class_id, placement_key)
            );
            CREATE INDEX IF NOT EXISTS idx_class_playlist_class_module_slot
                ON class_live_playlist_placements(class_id, module, slot);

            CREATE TABLE IF NOT EXISTS class_live_playlist_item_overrides (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                module TEXT NOT NULL,
                slot TEXT NOT NULL,
                item_id TEXT NOT NULL,
                removed INTEGER NOT NULL DEFAULT 0,
                page_number INTEGER,
                stage TEXT,
                sort_order INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(class_id, module, slot, item_id)
            );
            CREATE INDEX IF NOT EXISTS idx_class_playlist_overrides_class_module_slot
                ON class_live_playlist_item_overrides(class_id, module, slot);

            CREATE TABLE IF NOT EXISTS class_live_playlist_pages (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                module TEXT NOT NULL,
                slot TEXT NOT NULL,
                page_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                stage TEXT NOT NULL DEFAULT 'play',
                page_number INTEGER,
                insert_after_page_id TEXT,
                removed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(class_id, module, slot, page_id)
            );
            CREATE INDEX IF NOT EXISTS idx_class_playlist_pages_class_module_slot
                ON class_live_playlist_pages(class_id, module, slot);

            CREATE TABLE IF NOT EXISTS class_live_media_overlays (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                module TEXT NOT NULL,
                slot TEXT NOT NULL,
                stem TEXT NOT NULL DEFAULT '',
                caption TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(class_id, module, slot)
            );
            CREATE INDEX IF NOT EXISTS idx_class_media_overlays_class_module_slot
                ON class_live_media_overlays(class_id, module, slot);

            CREATE TABLE IF NOT EXISTS library_question_overlays (
                library_id INTEGER NOT NULL
                    REFERENCES content_libraries(id) ON DELETE CASCADE,
                question_id INTEGER NOT NULL
                    REFERENCES questions(id) ON DELETE CASCADE,
                stem_text TEXT NOT NULL DEFAULT '',
                options_json TEXT NOT NULL DEFAULT '[]',
                correct_answer TEXT NOT NULL DEFAULT '',
                points REAL,
                PRIMARY KEY (library_id, question_id)
            );
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

    def _ensure_live_item_schema(self) -> None:
        """Create idempotent publish and group-consensus session tables."""

        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_session_items (
                id INTEGER PRIMARY KEY,
                live_session_id INTEGER NOT NULL
                    REFERENCES live_class_sessions(id) ON DELETE CASCADE,
                placement_key TEXT NOT NULL,
                item_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                page_number INTEGER,
                sort_order INTEGER NOT NULL DEFAULT 0,
                kind TEXT NOT NULL DEFAULT 'poll',
                item_json TEXT NOT NULL DEFAULT '{}',
                prompt_id INTEGER
                    REFERENCES live_session_prompts(id) ON DELETE SET NULL,
                status TEXT NOT NULL DEFAULT 'inactive',
                publish_mode TEXT NOT NULL DEFAULT 'individual',
                response_mode TEXT NOT NULL DEFAULT 'individual',
                show_live_results INTEGER NOT NULL DEFAULT 1,
                published_at TEXT,
                closed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(live_session_id, placement_key)
            );
            CREATE INDEX IF NOT EXISTS idx_live_session_items_status
                ON live_session_items(
                    live_session_id, status, sort_order, id
                );
            CREATE TABLE IF NOT EXISTS live_group_members (
                live_item_id INTEGER NOT NULL
                    REFERENCES live_session_items(id) ON DELETE CASCADE,
                team_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                joined_at TEXT NOT NULL,
                PRIMARY KEY(live_item_id, team_id, student_id)
            );
            CREATE TABLE IF NOT EXISTS live_group_votes (
                id INTEGER PRIMARY KEY,
                live_item_id INTEGER NOT NULL
                    REFERENCES live_session_items(id) ON DELETE CASCADE,
                team_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                response_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(live_item_id, team_id, student_id)
            );
            CREATE INDEX IF NOT EXISTS idx_live_group_votes_team
                ON live_group_votes(live_item_id, team_id);
            CREATE TABLE IF NOT EXISTS live_group_responses (
                id INTEGER PRIMARY KEY,
                live_item_id INTEGER NOT NULL
                    REFERENCES live_session_items(id) ON DELETE CASCADE,
                team_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'collecting_votes',
                vote_summary_json TEXT NOT NULL DEFAULT '[]',
                proposed_answer_json TEXT,
                final_answer_json TEXT,
                finalizer_student_id INTEGER,
                awarded_points REAL,
                voting_ended_at TEXT,
                finalized_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(live_item_id, team_id)
            );
            CREATE INDEX IF NOT EXISTS idx_live_group_responses_item
                ON live_group_responses(live_item_id, status, team_id);
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
        if "teacher_state_json" not in cols:
            self.conn.execute(
                "ALTER TABLE live_class_sessions ADD COLUMN teacher_state_json TEXT"
            )
        if "canvas_sync_json" not in cols:
            self.conn.execute(
                "ALTER TABLE live_class_sessions ADD COLUMN canvas_sync_json TEXT"
            )
        feedback_cols = {
            str(row[1])
            for row in self.conn.execute("PRAGMA table_info(live_class_feedback)")
        }
        if "before_mood" not in feedback_cols:
            self.conn.execute(
                "ALTER TABLE live_class_feedback ADD COLUMN before_mood TEXT"
            )
        if "live_module" not in feedback_cols:
            self.conn.execute(
                "ALTER TABLE live_class_feedback ADD COLUMN live_module TEXT"
            )
        if "live_slot" not in feedback_cols:
            self.conn.execute(
                "ALTER TABLE live_class_feedback ADD COLUMN live_slot TEXT"
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
        """Count wrong-session-code joins from an IP in the last ``seconds``."""
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

        Only a submitted code that matched no active session should call
        this. Name typos, the roster picker, and idle-class submits must
        not consume the shared-IP budget.
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
        self._live_metadata_cache: dict[
            tuple[int, str, str, str], dict[str, Any]
        ] = {}

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

    def list_module_bank_links(
        self, library_id: int, module_number: int
    ) -> list[dict[str, Any]]:
        """Return confirmed module→bank links for one library and module.

        Args:
            library_id: ``content_libraries.id``.
            module_number: One-based module index.

        Returns:
            Rows with ``bank_id``, ``confirmed_at``, and bank title/import_key.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT l.bank_id, l.confirmed_at, b.title, b.import_key
                FROM course_module_bank_links l
                JOIN question_banks b ON b.id = l.bank_id
                WHERE l.library_id = ? AND l.module_number = ?
                ORDER BY b.title, b.id
                """,
                (int(library_id), int(module_number)),
            ).fetchall()
        return [dict(row) for row in rows]

    def recommended_module_test_banks(
        self, library_id: int, module_number: int
    ) -> list[dict[str, Any]]:
        """Return primary module test MC banks (for example ``Module 1 Test``).

        Args:
            library_id: ``content_libraries.id``.
            module_number: One-based module index.

        Returns:
            Banks with at least one imported multiple-choice question.
        """
        try:
            from bank_mc_normalize import (
                _normalize_bank_title,
                bank_matches_module,
                is_primary_module_test_bank,
            )
        except ImportError:
            from lms.bank_mc_normalize import (
                _normalize_bank_title,
                bank_matches_module,
                is_primary_module_test_bank,
            )

        with self._lock:
            banks = self.conn.execute(
                """
                SELECT id, title, import_key
                FROM question_banks
                WHERE library_id = ?
                ORDER BY title, id
                """,
                (int(library_id),),
            ).fetchall()
        recommended: list[dict[str, Any]] = []
        for row in banks:
            title = str(row["title"] or "")
            import_key = str(row["import_key"] or "")
            if not bank_matches_module(
                title=title,
                import_key=import_key,
                module_number=int(module_number),
            ):
                continue
            if not is_primary_module_test_bank(
                title=title, module_number=int(module_number)
            ):
                continue
            bank_id = int(row["id"])
            mc_count = int(
                self.conn.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM questions
                    WHERE bank_id = ?
                      AND item_type = 'multiple_choice_question'
                    """,
                    (bank_id,),
                ).fetchone()["n"]
            )
            if mc_count <= 0:
                continue
            recommended.append(
                {
                    "bank_id": bank_id,
                    "title": title,
                    "import_key": import_key,
                    "mc_count": mc_count,
                }
            )
        best_by_title: dict[str, dict[str, Any]] = {}
        for row in recommended:
            key = _normalize_bank_title(str(row.get("title") or ""))
            existing = best_by_title.get(key)
            if existing is None or int(row["mc_count"]) > int(existing["mc_count"]):
                best_by_title[key] = row
        return sorted(
            best_by_title.values(),
            key=lambda row: str(row.get("title") or "").lower(),
        )

    def suggest_module_banks(
        self, library_id: int, module_number: int
    ) -> dict[str, Any]:
        """List heuristic module bank matches and whether teacher confirmation is needed.

        Args:
            library_id: ``content_libraries.id``.
            module_number: One-based module index.

        Returns:
            ``suggested``, ``recommended``, ``confirmed``, and ``needs_confirmation`` keys.
        """
        try:
            from bank_mc_normalize import bank_matches_module
        except ImportError:
            from lms.bank_mc_normalize import bank_matches_module

        with self._lock:
            banks = self.conn.execute(
                """
                SELECT id, title, import_key
                FROM question_banks
                WHERE library_id = ?
                ORDER BY title, id
                """,
                (int(library_id),),
            ).fetchall()
        suggested = [
            {
                "bank_id": int(row["id"]),
                "title": str(row["title"] or ""),
                "import_key": str(row["import_key"] or ""),
            }
            for row in banks
            if bank_matches_module(
                title=str(row["title"] or ""),
                import_key=str(row["import_key"] or ""),
                module_number=int(module_number),
            )
        ]
        recommended = self.recommended_module_test_banks(
            int(library_id), int(module_number)
        )
        confirmed = self.list_module_bank_links(int(library_id), int(module_number))
        confirmed_ids = {int(row["bank_id"]) for row in confirmed}
        recommended_ids = {int(row["bank_id"]) for row in recommended}
        if recommended_ids:
            needs_confirmation = not recommended_ids.issubset(confirmed_ids)
        else:
            needs_confirmation = len(confirmed_ids) == 0
        return {
            "suggested": suggested,
            "recommended": recommended,
            "confirmed": confirmed,
            "needs_confirmation": needs_confirmation,
        }

    def confirm_module_bank_links(
        self,
        library_id: int,
        module_number: int,
        bank_ids: list[int],
    ) -> list[dict[str, Any]]:
        """Persist teacher-confirmed banks for one module in a library.

        Replaces any prior confirmations for the same library/module pair.

        Args:
            library_id: ``content_libraries.id``.
            module_number: One-based module index.
            bank_ids: Selected ``question_banks.id`` values (must belong to library).

        Returns:
            Updated confirmed link rows.
        """
        clean_ids: list[int] = []
        seen: set[int] = set()
        for raw in bank_ids:
            try:
                bank_id = int(raw)
            except (TypeError, ValueError):
                continue
            if bank_id in seen:
                continue
            seen.add(bank_id)
            clean_ids.append(bank_id)
        with self._lock:
            if clean_ids:
                placeholders = ",".join("?" for _ in clean_ids)
                valid = {
                    int(row["id"])
                    for row in self.conn.execute(
                        f"""
                        SELECT id FROM question_banks
                        WHERE library_id = ? AND id IN ({placeholders})
                        """,
                        (int(library_id), *clean_ids),
                    ).fetchall()
                }
                clean_ids = [bank_id for bank_id in clean_ids if bank_id in valid]
            self.conn.execute(
                """
                DELETE FROM course_module_bank_links
                WHERE library_id = ? AND module_number = ?
                """,
                (int(library_id), int(module_number)),
            )
            stamp = _now()
            for bank_id in clean_ids:
                self.conn.execute(
                    """
                    INSERT INTO course_module_bank_links (
                        library_id, module_number, bank_id, confirmed_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (int(library_id), int(module_number), int(bank_id), stamp),
                )
            self.conn.commit()
        return self.list_module_bank_links(int(library_id), int(module_number))

    @staticmethod
    def _module_bank_mc_search_haystack(item: dict[str, Any]) -> str:
        """Lowercase stem + option text used for keyword filtering."""
        parts = [
            str(item.get("text") or ""),
            str(item.get("question_title") or ""),
        ]
        options = item.get("options")
        if isinstance(options, list):
            for opt in options:
                if isinstance(opt, dict):
                    parts.append(str(opt.get("text") or opt.get("html") or ""))
                else:
                    parts.append(str(opt))
        return " ".join(parts).lower()

    def search_module_bank_mcs(
        self,
        library_id: int,
        module_number: int,
        query: str = "",
        *,
        limit: int = 200,
        class_id: int | None = None,
    ) -> dict[str, Any]:
        """Search normalized MCs scoped to confirmed module banks only.

        Args:
            library_id: ``content_libraries.id``.
            module_number: One-based module index.
            query: Optional stem/options substring filter.
            limit: Maximum rows to return (capped at 500).

        Returns:
            Dict with ``items``, ``total`` (importable MC count), and
            ``filtered`` (count after keyword filter).
        """
        try:
            from bank_mc_normalize import normalize_bank_mc
        except ImportError:
            from lms.bank_mc_normalize import normalize_bank_mc

        confirmed = self.list_module_bank_links(int(library_id), int(module_number))
        bank_ids = [int(row["bank_id"]) for row in confirmed]
        if not bank_ids:
            return {"items": [], "total": 0, "filtered": 0}
        needle = str(query or "").strip().lower()
        cap = max(1, min(int(limit), 500))
        placeholders = ",".join("?" for _ in bank_ids)
        params: list[Any] = [int(library_id), *bank_ids]
        sql = f"""
            SELECT q.id, q.bank_id, q.item_type, q.title, q.payload_json,
                   b.title AS bank_title,
                   o.stem_text, o.options_json, o.correct_answer, o.points
            FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            LEFT JOIN library_question_overlays o
                ON o.library_id = b.library_id AND o.question_id = q.id
            WHERE b.library_id = ?
              AND q.bank_id IN ({placeholders})
              AND q.item_type = 'multiple_choice_question'
            ORDER BY b.title, q.id
        """
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        all_items: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            overlay = None
            if row["stem_text"] is not None:
                overlay = {
                    "stem_text": row["stem_text"],
                    "options_json": row["options_json"],
                    "correct_answer": row["correct_answer"],
                    "points": row["points"],
                }
            normalized, skip_reason = normalize_bank_mc(
                question_id=int(row["id"]),
                bank_id=int(row["bank_id"]),
                item_type=str(row["item_type"] or ""),
                payload=payload,
                overlay=overlay,
                class_id=class_id,
                school=self,
                library_id=int(library_id),
            )
            if normalized is None:
                continue
            normalized["question_id"] = int(row["id"])
            normalized["bank_id"] = int(row["bank_id"])
            normalized["bank_title"] = str(row["bank_title"] or "")
            normalized["question_title"] = str(row["title"] or "")
            if skip_reason:
                normalized["skip_reason"] = skip_reason
            all_items.append(normalized)
        total = len(all_items)
        if needle:
            filtered_items = [
                item
                for item in all_items
                if needle in self._module_bank_mc_search_haystack(item)
            ]
        else:
            filtered_items = all_items
        return {
            "items": filtered_items[:cap],
            "total": total,
            "filtered": len(filtered_items),
        }

    @staticmethod
    def _playlist_row_item_id(row: dict[str, Any]) -> str:
        """Return the canonical metadata item id for one playlist row."""
        return str(row.get("id") or row.get("item_id") or "").strip()

    def list_class_playlist_item_overrides(
        self, class_id: int, module: str, slot: str
    ) -> list[dict[str, Any]]:
        """Return per-class seed-question hide/move overrides for one lesson file.

        Args:
            class_id: Game-show ``classes.id``.
            module: Module token such as ``M1``.
            slot: Live slot such as ``C2``.

        Returns:
            Override rows keyed by metadata ``item_id``.
        """
        module_key = str(module or "").upper()
        slot_key = str(slot or "").upper()
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT *
                FROM class_live_playlist_item_overrides
                WHERE class_id = ? AND module = ? AND slot = ?
                ORDER BY id ASC
                """,
                (int(class_id), module_key, slot_key),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_class_live_media_copy(
        self, class_id: int, module: str, slot: str
    ) -> dict[str, str]:
        """Return the class overlay stem/caption for one lesson slot.

        Args:
            class_id: Game-show ``classes.id``.
            module: Module token such as ``M1``.
            slot: Live slot such as ``C3``.

        Returns:
            ``{stem, caption}``; empty strings when no overlay exists.
        """
        module_key = str(module or "").upper()
        slot_key = str(slot or "").upper()
        with self._lock:
            row = self.conn.execute(
                """
                SELECT stem, caption
                FROM class_live_media_overlays
                WHERE class_id = ? AND module = ? AND slot = ?
                """,
                (int(class_id), module_key, slot_key),
            ).fetchone()
        if row is None:
            return {"stem": "", "caption": ""}
        return {
            "stem": str(row["stem"] or ""),
            "caption": str(row["caption"] or ""),
        }

    def upsert_class_live_media_copy(
        self,
        class_id: int,
        module: str,
        slot: str,
        *,
        stem: str | None = None,
        caption: str | None = None,
    ) -> dict[str, str]:
        """Persist student-facing media text as a class overlay template.

        Same lifetime as playlist overlays: the next open of this
        class/module/slot restores the saved stem and caption.

        Args:
            class_id: Game-show ``classes.id``.
            module: Module token such as ``M1``.
            slot: Live slot such as ``C3``.
            stem: Replacement title/stem, or ``None`` to keep the stored value.
            caption: Replacement caption, or ``None`` to keep the stored value.

        Returns:
            Stored ``{stem, caption}``.
        """
        module_key = str(module or "").upper()
        slot_key = str(slot or "").upper()
        current = self.get_class_live_media_copy(int(class_id), module_key, slot_key)
        next_stem = current["stem"] if stem is None else str(stem)
        next_caption = current["caption"] if caption is None else str(caption)
        stamp = _now()
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO class_live_media_overlays (
                    class_id, module, slot, stem, caption, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(class_id, module, slot) DO UPDATE SET
                    stem = excluded.stem,
                    caption = excluded.caption,
                    updated_at = excluded.updated_at
                """,
                (
                    int(class_id),
                    module_key,
                    slot_key,
                    next_stem,
                    next_caption,
                    stamp,
                    stamp,
                ),
            )
            self.conn.commit()
        self.invalidate_live_metadata_cache(
            class_id=int(class_id), module=module_key, slot=slot_key
        )
        return {"stem": next_stem, "caption": next_caption}

    @staticmethod
    def _apply_class_live_media_overlay(
        metadata: dict[str, Any], overlay: dict[str, str] | None
    ) -> dict[str, Any]:
        """Copy class media stem/caption onto merged live-lesson metadata.

        Args:
            metadata: Seed metadata after playlist overlays.
            overlay: ``get_class_live_media_copy`` row.

        Returns:
            Metadata whose ``media`` slot includes overlay text when present.
        """
        if not overlay:
            return metadata
        stem = str(overlay.get("stem") or "")
        caption = str(overlay.get("caption") or "")
        if not stem and not caption:
            return metadata
        merged = deepcopy(metadata)
        media = (
            dict(merged["media"])
            if isinstance(merged.get("media"), dict)
            else {}
        )
        if stem:
            media["stem"] = stem
            if not str(media.get("title") or "").strip():
                media["title"] = stem
        if caption:
            media["caption"] = caption
        if media:
            merged["media"] = media
        return merged

    @staticmethod
    def _apply_class_playlist_item_overrides(
        metadata: dict[str, Any], overrides: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Apply teacher hide/move overrides to merged live metadata.

        Args:
            metadata: Seed metadata merged with bank imports.
            overrides: Rows from ``class_live_playlist_item_overrides``.

        Returns:
            Metadata with removed questions dropped and moved rows re-placed.
        """
        if not overrides:
            return metadata
        by_item = {
            str(row.get("item_id") or "").strip(): row
            for row in overrides
            if str(row.get("item_id") or "").strip()
        }
        if not by_item:
            return metadata

        def override_for_item(item_id: str) -> dict[str, Any] | None:
            """Return the hide/move override that matches one playlist id.

            Engine-ride aliases (``teams_spark`` / ``teams-spark``) share a row.
            """
            token = str(item_id or "").strip()
            if not token:
                return None
            direct = by_item.get(token)
            if direct is not None:
                return direct
            for key, row in by_item.items():
                if SchoolDB._same_live_item_id(key, token):
                    return row
            return None

        def transform(row: dict[str, Any]) -> dict[str, Any] | None:
            item_id = SchoolDB._playlist_row_item_id(row)
            override = override_for_item(item_id)
            if override is None:
                return row
            if int(override.get("removed") or 0):
                return None
            patched = dict(row)
            stage = str(override.get("stage") or "").strip().lower()
            if stage:
                patched["stage"] = stage
            if override.get("page_number") is not None:
                patched["page_number"] = int(override["page_number"])
            if override.get("sort_order") is not None:
                patched["order"] = int(override["sort_order"])
            return patched

        merged = deepcopy(metadata)
        for key in ("items", "questions"):
            rows = [
                dict(row)
                for row in merged.get(key) or []
                if isinstance(row, dict)
            ]
            updated: list[dict[str, Any]] = []
            for row in rows:
                item_type = str(row.get("item_type") or row.get("kind") or "").strip().lower()
                is_question = item_type == "question" or key == "questions"
                if not is_question:
                    updated.append(row)
                    continue
                transformed = transform(row)
                if transformed is not None:
                    updated.append(transformed)
            merged[key] = updated
        return merged

    @staticmethod
    def _playlist_page_target(
        metadata: dict[str, Any], page_index: int
    ) -> tuple[str, int]:
        """Resolve one rail page index into stage and page_number.

        ``metadata`` must be the same merged deck the staff rail uses
        (``live_class_metadata_for_class_lesson``), including overlay pages.

        Args:
            metadata: Merged live-lesson metadata with ``pages``.
            page_index: One-based index into ``metadata.pages``.

        Returns:
            ``(stage, page_number)`` tuple.

        Raises:
            ValueError: When the page index is out of range.
        """
        pages = [
            row
            for row in metadata.get("pages") or []
            if isinstance(row, dict) and row.get("stage")
        ]
        if not pages:
            raise ValueError("lesson has no pages")
        try:
            idx = int(page_index)
        except (TypeError, ValueError):
            raise ValueError("target_page_index required") from None
        if idx < 1 or idx > len(pages):
            raise ValueError("target_page_index out of range")
        page = pages[idx - 1]
        stage = str(page.get("stage") or "round").strip().lower()
        try:
            stored = int(page["page_number"]) if page.get("page_number") not in (None, "") else idx
        except (TypeError, ValueError):
            stored = idx
        return stage, stored

    def _next_question_sort_order(
        self,
        metadata: dict[str, Any],
        *,
        stage: str,
        page_number: int,
        exclude_item_id: str | None = None,
    ) -> int:
        """Return the next ``order`` value for one stage/page bucket."""
        stage_key = str(stage or "").strip().lower()
        exclude = str(exclude_item_id or "").strip()
        orders: list[int] = []
        for row in metadata.get("questions") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("stage") or "").strip().lower() != stage_key:
                continue
            try:
                page = int(row.get("page_number") or 0)
            except (TypeError, ValueError):
                page = 0
            if page != int(page_number):
                continue
            if exclude and self._same_live_item_id(
                self._playlist_row_item_id(row), exclude
            ):
                continue
            try:
                orders.append(int(row.get("order") or 0))
            except (TypeError, ValueError):
                orders.append(0)
        return max(orders, default=0) + 1

    def _resolve_playlist_metadata_item_id(
        self, metadata: dict[str, Any], item_id: str
    ) -> str:
        """Return the merged-deck question id that matches ``item_id``.

        Engine-ride aliases (``teams_spark`` / ``teams-spark``) resolve to the
        catalogue id already on the deck so overrides apply to that row.

        Args:
            metadata: Merged live-lesson metadata.
            item_id: Staff-supplied playlist or engine-ride id.

        Returns:
            Canonical metadata ``id``, or the original token when unmatched.
        """
        token = str(item_id or "").strip()
        if not token:
            return token
        for key in ("questions", "items"):
            for row in metadata.get(key) or []:
                if not isinstance(row, dict):
                    continue
                candidate = self._playlist_row_item_id(row)
                if self._same_live_item_id(candidate, token):
                    return candidate
        return token

    def _prepare_playlist_item_edit(self, class_id: int, item_id: str) -> None:
        """Close an active lifecycle row so playlist edits can apply immediately."""
        active = self.get_active_live_session_for_class(int(class_id))
        if active is None:
            return
        token = str(item_id or "").strip()
        session_id = int(active["id"])
        for row in self.list_live_session_items(session_id):
            if not self._same_live_item_id(row.get("item_id"), token):
                continue
            if str(row.get("status") or "").strip().lower() != "active":
                continue
            self.close_live_session_item(session_id, int(row["id"]))

    def _sync_playlist_change(
        self, class_id: int, module: str, slot: str
    ) -> None:
        """Refresh cached metadata and lifecycle rows after playlist edits."""
        module_key = str(module or "").upper()
        slot_key = str(slot or "").upper()
        self.invalidate_live_metadata_cache(
            class_id=int(class_id), module=module_key, slot=slot_key
        )
        active = self.get_active_live_session_for_class(int(class_id))
        if active is None:
            return
        teacher = self.live_session_teacher_state_payload(int(active["id"]))
        if str(teacher.get("live_module") or "M1").upper() != module_key:
            return
        if str(teacher.get("live_slot") or "C1").upper() != slot_key:
            return
        session_id = int(active["id"])
        self.ensure_live_session_items(session_id)
        expected = self._expected_live_item_placement_keys(session_id)
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT id, placement_key, status
                FROM live_session_items
                WHERE live_session_id = ?
                """,
                (session_id,),
            ).fetchall()
            for row in rows:
                if str(row["placement_key"] or "") in expected:
                    continue
                if str(row["status"] or "").strip().lower() != "inactive":
                    continue
                self.conn.execute(
                    "DELETE FROM live_session_items WHERE id = ?",
                    (int(row["id"]),),
                )
            self.conn.commit()

    def _upsert_playlist_item_override(
        self,
        class_id: int,
        module: str,
        slot: str,
        item_id: str,
        *,
        removed: bool = False,
        stage: str | None = None,
        page_number: int | None = None,
        sort_order: int | None = None,
    ) -> dict[str, Any]:
        """Insert or update one seed-question playlist override row."""
        module_key = str(module or "").upper()
        slot_key = str(slot or "").upper()
        token = str(item_id or "").strip()
        if not token:
            raise ValueError("item_id required")
        stamp = _now()
        with self._lock:
            existing = self.conn.execute(
                """
                SELECT id FROM class_live_playlist_item_overrides
                WHERE class_id = ? AND module = ? AND slot = ? AND item_id = ?
                """,
                (int(class_id), module_key, slot_key, token),
            ).fetchone()
            if existing is None:
                self.conn.execute(
                    """
                    INSERT INTO class_live_playlist_item_overrides (
                        class_id, module, slot, item_id, removed,
                        page_number, stage, sort_order, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(class_id),
                        module_key,
                        slot_key,
                        token,
                        1 if removed else 0,
                        page_number,
                        stage,
                        sort_order,
                        stamp,
                        stamp,
                    ),
                )
            else:
                self.conn.execute(
                    """
                    UPDATE class_live_playlist_item_overrides
                    SET removed = ?, page_number = ?, stage = ?, sort_order = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        1 if removed else 0,
                        page_number,
                        stage,
                        sort_order,
                        stamp,
                        int(existing["id"]),
                    ),
                )
            self.conn.commit()
            saved = self.conn.execute(
                """
                SELECT * FROM class_live_playlist_item_overrides
                WHERE class_id = ? AND module = ? AND slot = ? AND item_id = ?
                """,
                (int(class_id), module_key, slot_key, token),
            ).fetchone()
        return dict(saved) if saved else {}

    def remove_class_playlist_item(
        self, class_id: int, module: str, slot: str, item_id: str
    ) -> dict[str, Any]:
        """Remove one question from a class live-lesson overlay.

        Bank imports delete their placement row. Seed questions persist a
        hide override so git seeds stay untouched.

        Args:
            class_id: Game-show ``classes.id``.
            module: Module token such as ``M1``.
            slot: Live slot such as ``C2``.
            item_id: Resolved metadata question id.

        Returns:
            Summary dict with ``action`` and ``item_id``.
        """
        module_key = str(module or "").upper()
        slot_key = str(slot or "").upper()
        token = str(item_id or "").strip()
        if not token:
            raise ValueError("item_id required")
        self._prepare_playlist_item_edit(int(class_id), token)
        if token.startswith("bank-import-") or token.startswith("staff-q-"):
            with self._lock:
                cur = self.conn.execute(
                    """
                    DELETE FROM class_live_playlist_placements
                    WHERE class_id = ? AND module = ? AND slot = ? AND item_id = ?
                    """,
                    (int(class_id), module_key, slot_key, token),
                )
                self.conn.commit()
                if int(cur.rowcount or 0) == 0:
                    raise KeyError(f"playlist item {token}")
        else:
            self._upsert_playlist_item_override(
                int(class_id),
                module_key,
                slot_key,
                token,
                removed=True,
            )
        self._sync_playlist_change(int(class_id), module_key, slot_key)
        if self._engine_ride_item_id(token):
            live = self.get_active_live_session_for_class(int(class_id))
            if live is not None:
                self._dismiss_engine_ride_prompt(int(live["id"]), token)
        return {"action": "removed", "item_id": token}

    def move_class_playlist_item(
        self,
        class_id: int,
        module: str,
        slot: str,
        item_id: str,
        *,
        target_page_index: int,
    ) -> dict[str, Any]:
        """Move one question onto another page in the same lesson deck.

        Bank imports update their placement row. Seed and engine-ride
        questions (``teams_spark``, ``minds_on``, ``meet_team``,
        ``team_challenge``) persist a class overlay. Destination pages
        come from the same merged deck as the staff rail, including
        overlay Welcome/Winner/blank pages.

        Args:
            class_id: Game-show ``classes.id``.
            module: Module token such as ``M1``.
            slot: Live slot such as ``C2``.
            item_id: Playlist, bank-import, or engine-ride id.
            target_page_index: One-based rail index into merged ``pages``.

        Returns:
            Summary dict with destination stage/page and ``item_id``.
        """
        module_key = str(module or "").upper()
        slot_key = str(slot or "").upper()
        token = str(item_id or "").strip()
        if not token:
            raise ValueError("item_id required")
        self._prepare_playlist_item_edit(int(class_id), token)
        metadata = self.live_class_metadata_for_class_lesson(
            int(class_id), module_key, slot_key
        )
        canonical = self._resolve_playlist_metadata_item_id(metadata, token)
        stage, page_number = self._playlist_page_target(
            metadata, int(target_page_index)
        )
        sort_order = self._next_question_sort_order(
            metadata,
            stage=stage,
            page_number=page_number,
            exclude_item_id=canonical,
        )
        if canonical.startswith("bank-import-") or canonical.startswith("staff-q-"):
            with self._lock:
                cur = self.conn.execute(
                    """
                    UPDATE class_live_playlist_placements
                    SET stage = ?, page_number = ?, sort_order = ?
                    WHERE class_id = ? AND module = ? AND slot = ? AND item_id = ?
                    """,
                    (
                        stage,
                        int(page_number),
                        int(sort_order),
                        int(class_id),
                        module_key,
                        slot_key,
                        canonical,
                    ),
                )
                self.conn.commit()
                if int(cur.rowcount or 0) == 0:
                    raise KeyError(f"playlist item {canonical}")
                row = self.conn.execute(
                    """
                    SELECT item_json FROM class_live_playlist_placements
                    WHERE class_id = ? AND module = ? AND slot = ? AND item_id = ?
                    """,
                    (int(class_id), module_key, slot_key, canonical),
                ).fetchone()
                if row is not None:
                    try:
                        payload = json.loads(row["item_json"] or "{}")
                    except json.JSONDecodeError:
                        payload = {}
                    if isinstance(payload, dict):
                        payload["stage"] = stage
                        payload["page_number"] = int(page_number)
                        payload["order"] = int(sort_order)
                        self.conn.execute(
                            """
                            UPDATE class_live_playlist_placements
                            SET item_json = ?
                            WHERE class_id = ? AND module = ? AND slot = ? AND item_id = ?
                            """,
                            (
                                json.dumps(payload),
                                int(class_id),
                                module_key,
                                slot_key,
                                canonical,
                            ),
                        )
                        self.conn.commit()
        else:
            self._upsert_playlist_item_override(
                int(class_id),
                module_key,
                slot_key,
                canonical,
                removed=False,
                stage=stage,
                page_number=int(page_number),
                sort_order=int(sort_order),
            )
        self._sync_playlist_change(int(class_id), module_key, slot_key)
        self._apply_moved_playlist_item_to_session(
            int(class_id),
            canonical,
            stage=stage,
            page_number=int(page_number),
            sort_order=int(sort_order),
        )
        self._reset_moved_playlist_item_session_state(
            int(class_id), token, module=module_key, slot=slot_key
        )
        return {
            "action": "moved",
            "item_id": token,
            "stage": stage,
            "page_number": int(page_number),
            "sort_order": int(sort_order),
            "target_page_index": int(target_page_index),
        }

    def playlist_staff_snapshot(
        self, class_id: int, module: str, slot: str
    ) -> dict[str, Any]:
        """Return merged deck metadata plus live cards for staff playlist APIs.

        Args:
            class_id: Game-show ``classes.id``.
            module: Module token such as ``M1``.
            slot: Live slot such as ``C2``.

        Returns:
            Dict with ``live_metadata`` and, when this lesson is the active
            session, ``live_items`` plus ``question_cards``.
        """
        module_key = str(module or "").strip().upper()
        slot_key = str(slot or "").strip().upper()
        snapshot: dict[str, Any] = {
            "live_metadata": self.live_class_metadata_for_class_lesson(
                int(class_id), module_key, slot_key
            )
        }
        active = self.get_active_live_session_for_class(int(class_id))
        if active is None:
            return snapshot
        teacher = self.live_session_teacher_state_payload(int(active["id"]))
        live_module = str(teacher.get("live_module") or "M1").upper()
        live_slot = str(teacher.get("live_slot") or "C1").upper()
        if live_module != module_key or live_slot != slot_key:
            return snapshot
        session_id = int(active["id"])
        self.ensure_live_session_items_if_stale(session_id)
        snapshot["live_items"] = self.list_live_session_items(session_id)
        snapshot["question_cards"] = self.live_session_question_cards(session_id)
        return snapshot

    def _reset_moved_playlist_item_session_state(
        self,
        class_id: int,
        item_id: str,
        *,
        module: str,
        slot: str,
    ) -> None:
        """Return a relocated question to unpublished and clear its answers.

        Resets matching active-session lifecycle rows to ``inactive``,
        deactivates the linked prompt, and deletes that question's
        responses, votes, and group-consensus rows. Does not change
        ``session_points``, career scores, or teacher awards.

        Args:
            class_id: Game-show ``classes.id``.
            item_id: Playlist item id that was moved.
            module: Module token such as ``M1``.
            slot: Live slot such as ``C2``.
        """
        live = self.get_active_live_session_for_class(int(class_id))
        if live is None:
            return
        session_id = int(live["id"])
        token = str(item_id or "").strip()
        now = _now()
        for item in self.list_live_session_items(session_id):
            if not self._same_live_item_id(item.get("item_id"), token):
                continue
            prompt_id = item.get("prompt_id")
            with self._lock:
                if prompt_id not in (None, ""):
                    self.conn.execute(
                        "DELETE FROM live_session_responses WHERE prompt_id = ?",
                        (int(prompt_id),),
                    )
                    self.conn.execute(
                        """
                        UPDATE live_session_prompts
                        SET active = 0, updated_at = ?
                        WHERE id = ?
                        """,
                        (now, int(prompt_id)),
                    )
                self.conn.execute(
                    "DELETE FROM live_group_votes WHERE live_item_id = ?",
                    (int(item["id"]),),
                )
                self.conn.execute(
                    "DELETE FROM live_group_members WHERE live_item_id = ?",
                    (int(item["id"]),),
                )
                self.conn.execute(
                    "DELETE FROM live_group_responses WHERE live_item_id = ?",
                    (int(item["id"]),),
                )
                self.conn.execute(
                    """
                    UPDATE live_session_items
                    SET status = 'inactive', published_at = NULL,
                        closed_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, int(item["id"])),
                )
                self.conn.commit()
        self._sync_playlist_change(int(class_id), str(module or ""), str(slot or ""))

    def _apply_moved_playlist_item_to_session(
        self,
        class_id: int,
        item_id: str,
        *,
        stage: str,
        page_number: int,
        sort_order: int,
    ) -> None:
        """Write dest stage/page onto every matching active-session lifecycle row.

        Bank and staff-authored items keep a stable ``placement_key``, so
        ``ensure_live_session_items_if_stale`` would otherwise skip the write
        and leave the old page on the session blob. Seed items may have a
        stage:page key; those rows are patched here and stale keys are
        dropped by the following ``_sync_playlist_change``.

        Args:
            class_id: Game-show ``classes.id``.
            item_id: Canonical playlist id after the move.
            stage: Destination stage token.
            page_number: Stored dest ``page_number``.
            sort_order: Destination sort/order value.
        """
        live = self.get_active_live_session_for_class(int(class_id))
        if live is None:
            return
        token = str(item_id or "").strip()
        if not token:
            return
        now = _now()
        stage_key = str(stage or "round").strip().lower() or "round"
        dest_page = int(page_number)
        dest_order = int(sort_order)
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT id, item_id, item_json
                FROM live_session_items
                WHERE live_session_id = ?
                """,
                (int(live["id"]),),
            ).fetchall()
            for row in rows:
                if not self._same_live_item_id(row["item_id"], token):
                    continue
                try:
                    payload = json.loads(row["item_json"] or "{}")
                except json.JSONDecodeError:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                payload["stage"] = stage_key
                payload["page_number"] = dest_page
                payload["order"] = dest_order
                self.conn.execute(
                    """
                    UPDATE live_session_items
                    SET stage = ?, page_number = ?, sort_order = ?,
                        item_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        stage_key,
                        dest_page,
                        dest_order,
                        json.dumps(payload),
                        now,
                        int(row["id"]),
                    ),
                )
            self.conn.commit()

    CUSTOM_LIVE_PAGE_STAGE = "play"

    def list_class_playlist_pages(
        self, class_id: int, module: str, slot: str
    ) -> list[dict[str, Any]]:
        """Return per-class add/hide page overlay rows for one live lesson.

        Args:
            class_id: Game-show ``classes.id``.
            module: Module token such as ``M1``.
            slot: Live slot such as ``C2``.

        Returns:
            Overlay rows ordered by id (creation order).
        """
        module_key = str(module or "").upper()
        slot_key = str(slot or "").upper()
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT *
                FROM class_live_playlist_pages
                WHERE class_id = ? AND module = ? AND slot = ?
                ORDER BY id ASC
                """,
                (int(class_id), module_key, slot_key),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _normalize_deck_pages(metadata: dict[str, Any]) -> list[dict[str, Any]]:
        """Return seed ``pages`` with stable ids and page_number values.

        Args:
            metadata: Loaded live-lesson metadata.

        Returns:
            Page dicts ``{id, name, stage, page_number}``.
        """
        pages: list[dict[str, Any]] = []
        for index, row in enumerate(metadata.get("pages") or [], start=1):
            if not isinstance(row, dict):
                continue
            stage = str(row.get("stage") or "").strip().lower()
            if not stage:
                continue
            page_id = str(row.get("id") or stage).strip() or stage
            name = str(row.get("name") or stage).strip() or stage
            try:
                page_number = (
                    int(row["page_number"])
                    if row.get("page_number") not in (None, "")
                    else index
                )
            except (TypeError, ValueError):
                page_number = index
            pages.append(
                {
                    "id": page_id,
                    "name": name,
                    "stage": stage,
                    "page_number": page_number,
                }
            )
        return pages

    @staticmethod
    def _apply_class_playlist_page_overlays(
        metadata: dict[str, Any], overlays: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Apply teacher add/hide page overlays without rewriting seed JSON.

        Added pages keep a unique ``page_number`` (max seed number + 1, …)
        so question bindings on authored pages stay put. Hidden seed pages
        are dropped from ``metadata.pages`` only.

        Args:
            metadata: Seed metadata, possibly with bank-import questions.
            overlays: Rows from ``class_live_playlist_pages``.

        Returns:
            Metadata whose ``pages`` list includes overlay inserts/hides.
        """
        merged = deepcopy(metadata)
        pages = SchoolDB._normalize_deck_pages(merged)
        seed_ids = {str(page.get("id") or "") for page in pages}
        removed = {
            str(row.get("page_id") or "").strip()
            for row in overlays
            if int(row.get("removed") or 0) and str(row.get("page_id") or "").strip()
        }
        pages = [page for page in pages if page["id"] not in removed]
        added = [
            row
            for row in overlays
            if not int(row.get("removed") or 0)
            and str(row.get("page_id") or "").strip()
            and str(row.get("page_id") or "").strip() not in seed_ids
        ]
        added.sort(key=lambda row: int(row.get("id") or 0))
        used_numbers = {
            int(page["page_number"])
            for page in pages
            if isinstance(page.get("page_number"), int)
        }
        for row in added:
            page_id = str(row.get("page_id") or "").strip()
            if not page_id or any(page["id"] == page_id for page in pages):
                continue
            try:
                page_number = (
                    int(row["page_number"])
                    if row.get("page_number") not in (None, "")
                    else 0
                )
            except (TypeError, ValueError):
                page_number = 0
            if page_number < 1 or page_number in used_numbers:
                page_number = max(used_numbers, default=0) + 1
            used_numbers.add(page_number)
            new_page = {
                "id": page_id,
                "name": str(row.get("name") or "Page").strip() or "Page",
                "stage": str(
                    row.get("stage") or SchoolDB.CUSTOM_LIVE_PAGE_STAGE
                ).strip().lower()
                or SchoolDB.CUSTOM_LIVE_PAGE_STAGE,
                "page_number": page_number,
                "source": "overlay",
            }
            after = str(row.get("insert_after_page_id") or "").strip()
            insert_at = len(pages)
            if after:
                for index, page in enumerate(pages):
                    if page["id"] == after:
                        insert_at = index + 1
                        break
            pages.insert(insert_at, new_page)
        merged["pages"] = pages
        return merged

    def _next_custom_page_number(
        self, metadata: dict[str, Any], overlays: list[dict[str, Any]]
    ) -> int:
        """Return a page_number that does not collide with seed or overlay pages."""
        used: list[int] = []
        for page in self._normalize_deck_pages(metadata):
            try:
                used.append(int(page["page_number"]))
            except (TypeError, ValueError, KeyError):
                continue
        for row in overlays:
            if row.get("page_number") in (None, ""):
                continue
            try:
                used.append(int(row["page_number"]))
            except (TypeError, ValueError):
                continue
        return max(used, default=0) + 1

    def _land_live_session_on_page(
        self,
        class_id: int,
        module: str,
        slot: str,
        page: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Point the active session at one deck page when it matches this slot.

        Args:
            class_id: Game-show ``classes.id``.
            module: Module token such as ``M1``.
            slot: Live slot such as ``C3``.
            page: Target ``{id, stage}`` page dict.

        Returns:
            Updated public teacher state, or ``None`` when no matching session.
        """
        active = self.get_active_live_session_for_class(int(class_id))
        if active is None:
            return None
        teacher = self.live_session_teacher_state_payload(int(active["id"]))
        if str(teacher.get("live_module") or "M1").upper() != str(module).upper():
            return None
        if str(teacher.get("live_slot") or "C1").upper() != str(slot).upper():
            return None
        return self.set_live_session_teacher_state(
            int(active["id"]),
            stage=str(page.get("stage") or "play"),
            page_id=str(page.get("id") or ""),
        )

    @staticmethod
    def _normalize_playlist_page_kind(kind: str) -> str:
        """Return ``blank``, ``welcome``, or ``winner``.

        Args:
            kind: Raw add-page kind from the staff dialog or API.

        Raises:
            ValueError: When ``kind`` is not one of the supported tokens.
        """
        token = str(kind or "blank").strip().lower() or "blank"
        if token not in {"blank", "welcome", "winner"}:
            raise ValueError(f"invalid page kind: {token}")
        return token

    def add_class_playlist_page(
        self,
        class_id: int,
        module: str,
        slot: str,
        *,
        name: str,
        after_page_id: str,
        kind: str = "blank",
    ) -> dict[str, Any]:
        """Insert an overlay page after ``after_page_id`` for this class deck.

        Authored seed JSON is not rewritten. Blank pages have no questions
        and reuse the pack's existing media via the shared slot metadata.
        Welcome and winner pages land on the existing student TEAMS / Summary
        surfaces.

        Args:
            class_id: Game-show ``classes.id``.
            module: Module token such as ``M1``.
            slot: Live slot such as ``C3``.
            name: Teacher-supplied page title. Required for ``blank``.
            after_page_id: Page identity to insert after.
            kind: ``blank``, ``welcome``, or ``winner``.

        Returns:
            Summary with ``page``, ``land_page``, and merged ``live_metadata``.

        Raises:
            KeyError: Unknown class.
            ValueError: Empty blank name, unknown ``after_page_id``, or kind.
        """
        module_key = str(module or "").upper()
        slot_key = str(slot or "").upper()
        page_kind = self._normalize_playlist_page_kind(kind)
        title = str(name or "").strip()
        if page_kind == "welcome":
            stage = "teams"
            if not title:
                title = "Welcome"
        elif page_kind == "winner":
            stage = "summary"
            if not title:
                title = "Winner"
        else:
            stage = self.CUSTOM_LIVE_PAGE_STAGE
            if not title:
                raise ValueError("page name required")
        if len(title) > 80:
            title = title[:80]
        after = str(after_page_id or "").strip()
        class_row = self.game.get_class(int(class_id))
        if class_row is None:
            raise KeyError(f"class {class_id}")
        course = str(class_row.get("course_code") or "").upper()
        loaded = load_live_class_metadata(course, module_key, slot_key)
        placements = self.list_class_playlist_placements(
            int(class_id), module_key, slot_key
        )
        item_overrides = self.list_class_playlist_item_overrides(
            int(class_id), module_key, slot_key
        )
        page_overlays = self.list_class_playlist_pages(
            int(class_id), module_key, slot_key
        )
        current = self._apply_class_playlist_page_overlays(
            self._apply_class_playlist_item_overrides(
                self._merge_playlist_placements_into_metadata(loaded, placements),
                item_overrides,
            ),
            page_overlays,
        )
        pages = [
            row
            for row in current.get("pages") or []
            if isinstance(row, dict) and row.get("id")
        ]
        if after and not any(str(row.get("id") or "") == after for row in pages):
            raise ValueError("after_page_id is not in this deck")
        if not after and pages:
            after = str(pages[0].get("id") or "")
        existing_ids = {str(row.get("id") or "") for row in pages}
        existing_ids.update(
            str(row.get("page_id") or "") for row in page_overlays
        )
        page_id = f"custom-{secrets.token_hex(4)}"
        while page_id in existing_ids:
            page_id = f"custom-{secrets.token_hex(4)}"
        page_number = self._next_custom_page_number(current, page_overlays)
        stamp = _now()
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO class_live_playlist_pages (
                    class_id, module, slot, page_id, name, stage, page_number,
                    insert_after_page_id, removed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    int(class_id),
                    module_key,
                    slot_key,
                    page_id,
                    title,
                    stage,
                    int(page_number),
                    after or None,
                    stamp,
                    stamp,
                ),
            )
            self.conn.commit()
        self._sync_playlist_change(int(class_id), module_key, slot_key)
        live_metadata = self.live_class_metadata_for_class_lesson(
            int(class_id), module_key, slot_key
        )
        page = next(
            (
                row
                for row in live_metadata.get("pages") or []
                if isinstance(row, dict) and str(row.get("id") or "") == page_id
            ),
            {
                "id": page_id,
                "name": title,
                "stage": stage,
                "page_number": int(page_number),
                "source": "overlay",
            },
        )
        teacher_state = self._land_live_session_on_page(
            int(class_id), module_key, slot_key, page
        )
        return {
            "action": "added",
            "page": page,
            "land_page": page,
            "live_metadata": live_metadata,
            "teacher_state": teacher_state,
        }

    def delete_class_playlist_page(
        self, class_id: int, module: str, slot: str, page_id: str
    ) -> dict[str, Any]:
        """Remove one deck page for this class, keeping at least one page.

        Last-page rule: refuse with ``ValueError`` when the merged deck
        has only one page. Land on the next page after the deleted one,
        or the previous page when the deleted page was last.

        Seed JSON is not rewritten. Authored pages are hidden via an
        overlay row; overlay-only pages delete their row.

        Args:
            class_id: Game-show ``classes.id``.
            module: Module token such as ``M1``.
            slot: Live slot such as ``C3``.
            page_id: Page identity to remove.

        Returns:
            Summary with ``land_page`` and merged ``live_metadata``.

        Raises:
            KeyError: Unknown class or page.
            ValueError: Last remaining page, or empty ``page_id``.
        """
        module_key = str(module or "").upper()
        slot_key = str(slot or "").upper()
        token = str(page_id or "").strip()
        if not token:
            raise ValueError("page_id required")
        class_row = self.game.get_class(int(class_id))
        if class_row is None:
            raise KeyError(f"class {class_id}")
        course = str(class_row.get("course_code") or "").upper()
        loaded = load_live_class_metadata(course, module_key, slot_key)
        placements = self.list_class_playlist_placements(
            int(class_id), module_key, slot_key
        )
        item_overrides = self.list_class_playlist_item_overrides(
            int(class_id), module_key, slot_key
        )
        page_overlays = self.list_class_playlist_pages(
            int(class_id), module_key, slot_key
        )
        current = self._apply_class_playlist_page_overlays(
            self._apply_class_playlist_item_overrides(
                self._merge_playlist_placements_into_metadata(loaded, placements),
                item_overrides,
            ),
            page_overlays,
        )
        pages = [
            row
            for row in current.get("pages") or []
            if isinstance(row, dict) and row.get("id")
        ]
        index = next(
            (
                idx
                for idx, row in enumerate(pages)
                if str(row.get("id") or "") == token
            ),
            -1,
        )
        if index < 0:
            raise KeyError(f"playlist page {token}")
        if len(pages) <= 1:
            raise ValueError("cannot delete the last remaining page")
        if index + 1 < len(pages):
            land = pages[index + 1]
        else:
            land = pages[index - 1]
        predecessor = str(pages[index - 1].get("id") or "") if index > 0 else ""
        stamp = _now()
        overlay = next(
            (
                row
                for row in page_overlays
                if str(row.get("page_id") or "") == token
            ),
            None,
        )
        seed_ids = {
            str(row.get("id") or "")
            for row in self._normalize_deck_pages(loaded)
        }
        with self._lock:
            if overlay is not None and not int(overlay.get("removed") or 0):
                self.conn.execute(
                    """
                    DELETE FROM class_live_playlist_pages
                    WHERE class_id = ? AND module = ? AND slot = ? AND page_id = ?
                    """,
                    (int(class_id), module_key, slot_key, token),
                )
            elif token in seed_ids:
                existing = self.conn.execute(
                    """
                    SELECT id FROM class_live_playlist_pages
                    WHERE class_id = ? AND module = ? AND slot = ? AND page_id = ?
                    """,
                    (int(class_id), module_key, slot_key, token),
                ).fetchone()
                if existing is None:
                    self.conn.execute(
                        """
                        INSERT INTO class_live_playlist_pages (
                            class_id, module, slot, page_id, name, stage,
                            page_number, insert_after_page_id, removed,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            int(class_id),
                            module_key,
                            slot_key,
                            token,
                            str(pages[index].get("name") or token),
                            str(pages[index].get("stage") or "play"),
                            pages[index].get("page_number"),
                            predecessor or None,
                            stamp,
                            stamp,
                        ),
                    )
                else:
                    self.conn.execute(
                        """
                        UPDATE class_live_playlist_pages
                        SET removed = 1, updated_at = ?
                        WHERE id = ?
                        """,
                        (stamp, int(existing["id"])),
                    )
            else:
                raise KeyError(f"playlist page {token}")
            self.conn.execute(
                """
                UPDATE class_live_playlist_pages
                SET insert_after_page_id = ?, updated_at = ?
                WHERE class_id = ? AND module = ? AND slot = ?
                  AND insert_after_page_id = ?
                """,
                (
                    predecessor or None,
                    stamp,
                    int(class_id),
                    module_key,
                    slot_key,
                    token,
                ),
            )
            self.conn.commit()
        self._sync_playlist_change(int(class_id), module_key, slot_key)
        live_metadata = self.live_class_metadata_for_class_lesson(
            int(class_id), module_key, slot_key
        )
        land_page = next(
            (
                row
                for row in live_metadata.get("pages") or []
                if isinstance(row, dict) and str(row.get("id") or "") == str(land.get("id") or "")
            ),
            land,
        )
        teacher_state = self._land_live_session_on_page(
            int(class_id), module_key, slot_key, land_page
        )
        return {
            "action": "deleted",
            "page_id": token,
            "land_page": land_page,
            "live_metadata": live_metadata,
            "teacher_state": teacher_state,
        }

    def invalidate_live_metadata_cache(
        self,
        *,
        class_id: int | None = None,
        course: str | None = None,
        module: str | None = None,
        slot: str | None = None,
    ) -> None:
        """Drop cached live metadata entries matching optional scope filters.

        Args:
            class_id: When set, only evict rows for this class.
            course: When set with other keys, narrow to one course code.
            module: When set, narrow to one module token (e.g. ``M1``).
            slot: When set, narrow to one live slot (e.g. ``C2``).
        """
        if not self._live_metadata_cache:
            return
        course_key = str(course or "").upper() if course else None
        module_key = str(module or "").upper() if module else None
        slot_key = str(slot or "").upper() if slot else None
        drop: list[tuple[int, str, str, str]] = []
        for key in self._live_metadata_cache:
            if class_id is not None and key[0] != int(class_id):
                continue
            if course_key and key[1] != course_key:
                continue
            if module_key and key[2] != module_key:
                continue
            if slot_key and key[3] != slot_key:
                continue
            drop.append(key)
        for key in drop:
            self._live_metadata_cache.pop(key, None)


    def _class_library_id(self, class_id: int) -> int | None:
        """Return the content library attached to one class offering."""
        with self._lock:
            row = self.conn.execute(
                """
                SELECT o.library_id
                FROM classes c
                JOIN course_offerings o ON o.id = c.offering_id
                WHERE c.id = ?
                """,
                (int(class_id),),
            ).fetchone()
        if row is None or row["library_id"] is None:
            return None
        return int(row["library_id"])

    def _bank_item_needs_rehydrate(self, payload: dict[str, Any]) -> bool:
        """Return True when a stored bank import should be re-normalized."""
        if str(payload.get("import_source") or "") != "module_bank":
            return False
        if not payload.get("source_question_id"):
            return False
        blob = json.dumps(payload, ensure_ascii=False)
        if "instructure.com" in blob or "equation_images" in blob:
            return True
        if not str(payload.get("text_html") or "").strip():
            return True
        return False

    def rehydrate_module_bank_item(
        self,
        class_id: int,
        library_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Re-normalize one stored bank import with fresh image mirroring."""
        if not self._bank_item_needs_rehydrate(payload):
            return payload
        try:
            from bank_mc_normalize import normalize_bank_mc
        except ImportError:
            from lms.bank_mc_normalize import normalize_bank_mc

        question_id = int(payload.get("source_question_id") or 0)
        if not question_id:
            return payload
        with self._lock:
            row = self.conn.execute(
                """
                SELECT q.id, q.bank_id, q.item_type, q.payload_json,
                       o.stem_text, o.options_json, o.correct_answer, o.points
                FROM questions q
                JOIN question_banks b ON b.id = q.bank_id
                LEFT JOIN library_question_overlays o
                    ON o.library_id = b.library_id AND o.question_id = q.id
                WHERE q.id = ? AND b.library_id = ?
                """,
                (question_id, int(library_id)),
            ).fetchone()
        if row is None:
            return payload
        try:
            raw_payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            return payload
        overlay = None
        if row["stem_text"] is not None:
            overlay = {
                "stem_text": row["stem_text"],
                "options_json": row["options_json"],
                "correct_answer": row["correct_answer"],
                "points": row["points"],
            }
        normalized, _ = normalize_bank_mc(
            question_id=int(row["id"]),
            bank_id=int(row["bank_id"]),
            item_type=str(row["item_type"] or ""),
            payload=raw_payload,
            overlay=overlay,
            class_id=int(class_id),
            school=self,
            library_id=int(library_id),
        )
        if not normalized:
            return payload
        merged = {**payload, **normalized}
        merged["id"] = str(payload.get("id") or merged.get("id") or "")
        merged["import_source"] = "module_bank"
        return merged


    def list_class_playlist_placements(
        self, class_id: int, module: str, slot: str
    ) -> list[dict[str, Any]]:
        """Return per-class imported MC placements for one live lesson file.

        Args:
            class_id: Game-show ``classes.id``.
            module: Module token such as ``M1``.
            slot: Live slot such as ``C2``.

        Returns:
            Placement rows ordered by sort_order then id.
        """
        module_key = str(module or "").upper()
        slot_key = str(slot or "").upper()
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT *
                FROM class_live_playlist_placements
                WHERE class_id = ? AND module = ? AND slot = ?
                ORDER BY sort_order ASC, id ASC
                """,
                (int(class_id), module_key, slot_key),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                parsed = json.loads(item.get("item_json") or "{}")
            except json.JSONDecodeError:
                parsed = {}
            item["item"] = parsed if isinstance(parsed, dict) else {}
            item.pop("item_json", None)
            out.append(item)
        library_id = self._class_library_id(int(class_id))
        if library_id is not None:
            for item in out:
                payload = item.get("item")
                if isinstance(payload, dict):
                    item["item"] = self.rehydrate_module_bank_item(
                        int(class_id), int(library_id), payload
                    )
        return out

    @staticmethod
    def _merge_playlist_placements_into_metadata(
        metadata: dict[str, Any], placements: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Append imported MC placements to seed metadata items and questions.

        Args:
            metadata: Loaded live-class metadata document.
            placements: Rows from ``class_live_playlist_placements``.

        Returns:
            The same metadata dict with overlay rows merged and sorted.
        """
        if not placements:
            return metadata
        merged = deepcopy(metadata)
        items = [
            dict(row)
            for row in merged.get("items") or []
            if isinstance(row, dict)
        ]
        questions = [
            dict(row)
            for row in merged.get("questions") or []
            if isinstance(row, dict)
        ]
        for placement in placements:
            payload = (
                placement.get("item")
                if isinstance(placement.get("item"), dict)
                else {}
            )
            if not payload:
                continue
            row = {
                **payload,
                "id": str(
                    payload.get("id")
                    or placement.get("item_id")
                    or ""
                ).strip(),
                "item_id": str(placement.get("item_id") or "").strip(),
                "placement_key": str(placement.get("placement_key") or "").strip(),
                "item_type": "question",
                "type": str(payload.get("type") or "mc").strip().lower(),
                "stage": str(
                    payload.get("stage") or placement.get("stage") or "round"
                ).strip().lower(),
                "page_number": placement.get("page_number"),
                "order": placement.get("sort_order") or payload.get("order"),
                "source_question_id": placement.get("source_question_id"),
                "import_source": "module_bank",
            }
            if not row["id"]:
                continue
            items.append(row)
            questions.append(deepcopy(row))
        items.sort(key=_placement_sort_key)
        questions.sort(key=_placement_sort_key)
        merged["items"] = items
        merged["questions"] = questions
        return merged

    def upsert_library_question_overlay(
        self,
        library_id: int,
        question_id: int,
        *,
        stem_text: str,
        options: list[str],
        correct_answer: str,
        points: float | None = None,
    ) -> dict[str, Any]:
        """Persist staff edits for one ingest MC without mutating ``questions``.

        Args:
            library_id: ``content_libraries.id``.
            question_id: ``questions.id`` belonging to the library.
            stem_text: Plain-text stem override.
            options: Ordered option strings.
            correct_answer: Letter key ``A``–``H``.
            points: Optional point value override.

        Returns:
            The saved overlay row as a dict.
        """
        clean_options = [str(opt).strip() for opt in options if str(opt).strip()]
        correct = str(correct_answer or "").strip().upper()
        with self._lock:
            bank_row = self.conn.execute(
                """
                SELECT q.id
                FROM questions q
                JOIN question_banks b ON b.id = q.bank_id
                WHERE q.id = ? AND b.library_id = ?
                """,
                (int(question_id), int(library_id)),
            ).fetchone()
            if bank_row is None:
                raise KeyError(f"question {question_id}")
            self.conn.execute(
                """
                INSERT INTO library_question_overlays (
                    library_id, question_id, stem_text, options_json,
                    correct_answer, points
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(library_id, question_id) DO UPDATE SET
                    stem_text = excluded.stem_text,
                    options_json = excluded.options_json,
                    correct_answer = excluded.correct_answer,
                    points = excluded.points
                """,
                (
                    int(library_id),
                    int(question_id),
                    str(stem_text or "").strip(),
                    json.dumps(clean_options),
                    correct,
                    float(points) if points is not None else None,
                ),
            )
            self.conn.commit()
            row = self.conn.execute(
                """
                SELECT * FROM library_question_overlays
                WHERE library_id = ? AND question_id = ?
                """,
                (int(library_id), int(question_id)),
            ).fetchone()
        return dict(row) if row else {}

    def import_mc_to_class_playlist(
        self,
        class_id: int,
        module: str,
        slot: str,
        question_id: int,
        *,
        library_id: int,
        page_number: int,
        stage: str | None = None,
        order: int | None = None,
    ) -> dict[str, Any]:
        """Import one module-bank MC onto a class live-lesson overlay.

        Args:
            class_id: Game-show ``classes.id``.
            module: Module token such as ``M1``.
            slot: Live slot such as ``C2``.
            question_id: ``questions.id`` to import.
            library_id: Attached pack library id (authorization scope).
            page_number: Teacher page index for the placement.
            stage: Optional lifecycle stage; defaults to ``round``.
            order: Optional sort order on the page; defaults to next slot.

        Returns:
            Inserted placement row including parsed ``item`` payload.

        Raises:
            KeyError: When the question is missing or not module-scoped.
            ValueError: When the question cannot be normalized to one MC key.
        """
        try:
            from bank_mc_normalize import normalize_bank_mc, parse_module_token
        except ImportError:
            from lms.bank_mc_normalize import normalize_bank_mc, parse_module_token

        module_number = parse_module_token(str(module or "").strip().upper())
        if module_number is None:
            raise ValueError("module required (e.g. M1)")
        module_key = f"M{module_number}"
        slot_key = str(slot or "").upper()
        stage_key = str(stage or "round").strip().lower()
        try:
            page = max(1, int(page_number))
        except (TypeError, ValueError):
            page = 1
        with self._lock:
            row = self.conn.execute(
                """
                SELECT q.id, q.bank_id, q.item_type, q.title, q.payload_json,
                       b.title AS bank_title, b.library_id,
                       o.stem_text, o.options_json, o.correct_answer, o.points
                FROM questions q
                JOIN question_banks b ON b.id = q.bank_id
                LEFT JOIN library_question_overlays o
                    ON o.library_id = b.library_id AND o.question_id = q.id
                WHERE q.id = ? AND b.library_id = ?
                """,
                (int(question_id), int(library_id)),
            ).fetchone()
            if row is None:
                raise KeyError(f"question {question_id}")
            confirmed = self.list_module_bank_links(int(library_id), int(module_number))
            allowed_banks = {int(link["bank_id"]) for link in confirmed}
            if int(row["bank_id"]) not in allowed_banks:
                raise KeyError(
                    f"question {question_id} is not in confirmed banks for {module_key}"
                )
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            overlay = None
            if row["stem_text"] is not None:
                overlay = {
                    "stem_text": row["stem_text"],
                    "options_json": row["options_json"],
                    "correct_answer": row["correct_answer"],
                    "points": row["points"],
                }
            normalized, skip_reason = normalize_bank_mc(
                question_id=int(row["id"]),
                bank_id=int(row["bank_id"]),
                item_type=str(row["item_type"] or ""),
                payload=payload,
                overlay=overlay,
                class_id=int(class_id),
                school=self,
                library_id=int(library_id),
            )
            if normalized is None:
                raise ValueError(skip_reason or "invalid_mc")
            if order is None:
                existing = self.conn.execute(
                    """
                    SELECT MAX(sort_order) AS max_order
                    FROM class_live_playlist_placements
                    WHERE class_id = ? AND module = ? AND slot = ?
                      AND stage = ? AND page_number = ?
                    """,
                    (int(class_id), module_key, slot_key, stage_key, page),
                ).fetchone()
                base = int(existing["max_order"] or 0) if existing else 0
                sort_order = base + 1
            else:
                sort_order = max(1, int(order))
            item_id = f"bank-import-{int(question_id)}"
            placement_key = f"class:{int(class_id)}:import:{uuid.uuid4().hex}"
            item_payload = {
                **normalized,
                "id": item_id,
                "item_type": "question",
                "stage": stage_key,
                "page_number": page,
                "order": sort_order,
                "placement_key": placement_key,
                "publish_modes": ["individual"],
                "response_mode": "individual",
                "bank_title": str(row["bank_title"] or ""),
                "question_title": str(row["title"] or ""),
                "import_source": "module_bank",
            }
            stamp = _now()
            cur = self.conn.execute(
                """
                INSERT INTO class_live_playlist_placements (
                    class_id, module, slot, page_number, stage, sort_order,
                    placement_key, item_id, item_json, source_question_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(class_id),
                    module_key,
                    slot_key,
                    page,
                    stage_key,
                    sort_order,
                    placement_key,
                    item_id,
                    json.dumps(item_payload),
                    int(question_id),
                    stamp,
                ),
            )
            self.conn.commit()
            placement_id = int(cur.lastrowid)
            saved = self.conn.execute(
                """
                SELECT * FROM class_live_playlist_placements WHERE id = ?
                """,
                (placement_id,),
            ).fetchone()
        self.invalidate_live_metadata_cache(
            class_id=int(class_id),
            module=module_key,
            slot=slot_key,
        )
        active = self.get_active_live_session_for_class(int(class_id))
        if active is not None:
            teacher = self.live_session_teacher_state_payload(int(active["id"]))
            live_module = str(teacher.get("live_module") or "M1").upper()
            live_slot = str(teacher.get("live_slot") or "C1").upper()
            if live_module == module_key and live_slot == slot_key:
                self.ensure_live_session_items(int(active["id"]))
        placement = dict(saved) if saved else {}
        try:
            parsed_item = json.loads(placement.get("item_json") or "{}")
        except json.JSONDecodeError:
            parsed_item = {}
        placement["item"] = parsed_item if isinstance(parsed_item, dict) else {}
        placement.pop("item_json", None)
        return placement

    def _ensure_module_bank_link(
        self, library_id: int, module_number: int, bank_id: int
    ) -> None:
        """Confirm one bank for a module without replacing existing links.

        Args:
            library_id: ``content_libraries.id``.
            module_number: One-based module index.
            bank_id: ``question_banks.id``.
        """
        with self._lock:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO course_module_bank_links (
                    library_id, module_number, bank_id, confirmed_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    int(library_id),
                    int(module_number),
                    int(bank_id),
                    _now(),
                ),
            )
            self.conn.commit()

    def _ensure_staff_authored_bank(self, library_id: int) -> int:
        """Return the library's staff-authored bank, creating it when missing.

        Args:
            library_id: ``content_libraries.id``.

        Returns:
            ``question_banks.id``.
        """
        import_key = "staff-authored"
        with self._lock:
            row = self.conn.execute(
                """
                SELECT id FROM question_banks
                WHERE library_id = ? AND import_key = ?
                """,
                (int(library_id), import_key),
            ).fetchone()
            if row is not None:
                return int(row["id"])
            cur = self.conn.execute(
                """
                INSERT INTO question_banks (
                    library_id, import_key, title, settings_json, created_at
                ) VALUES (?, ?, ?, '{}', ?)
                """,
                (int(library_id), import_key, "Staff authored", _now()),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def _insert_staff_bank_question(
        self,
        *,
        library_id: int,
        bank_id: int,
        question_type: str,
        text: str,
        options: list[str],
        correct_answer: str,
        payload: dict[str, Any],
    ) -> int:
        """Insert one staff-authored question into the existing bank schema.

        Args:
            library_id: ``content_libraries.id``.
            bank_id: ``question_banks.id``.
            question_type: ``mc``, ``numeric``, or ``poll``.
            text: Stem text.
            options: MC option strings.
            correct_answer: Letter key or numeric string; empty for polls.
            payload: Full live-item payload stored on ``questions.payload_json``.

        Returns:
            New ``questions.id``.
        """
        item_type = {
            "mc": "multiple_choice_question",
            "numeric": "numerical_question",
            "poll": "essay_question",
        }.get(question_type, "essay_question")
        import_key = f"staff-q-{uuid.uuid4().hex}"
        title = str(text or "").strip()[:80] or "Staff question"
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT INTO questions (
                    bank_id, import_key, item_type, title, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(bank_id),
                    import_key,
                    item_type,
                    title,
                    json.dumps(payload),
                    _now(),
                ),
            )
            self.conn.commit()
            question_id = int(cur.lastrowid)
        if question_type == "mc":
            self.upsert_library_question_overlay(
                int(library_id),
                question_id,
                stem_text=text,
                options=options,
                correct_answer=correct_answer,
            )
        return question_id

    @staticmethod
    def _bank_scope_module_numbers(
        bank_scope: str, current_module_number: int
    ) -> list[int]:
        """Resolve Save-to-bank radios into module numbers 1–8.

        Accepts ``course`` / ``all`` (every module), ``module`` (current),
        ``M2`` / ``m2`` / ``2``. Unknown tokens fall back to the current module.

        Args:
            bank_scope: Staff radio value.
            current_module_number: Module number of the open live lesson.

        Returns:
            Distinct module numbers to link.
        """
        try:
            from bank_mc_normalize import parse_module_token
        except ImportError:
            from lms.bank_mc_normalize import parse_module_token

        token = str(bank_scope or "module").strip().lower()
        if token in {"course", "course-wide", "coursewide", "all"}:
            return list(range(1, 9))
        if token in {"module", "this", "current", ""}:
            return [int(current_module_number)]
        digits = token[1:] if token.startswith("m") else token
        if digits.isdigit():
            number = int(digits)
            if 1 <= number <= 8:
                return [number]
        parsed = parse_module_token(str(bank_scope or "").strip().upper())
        if parsed is not None and 1 <= int(parsed) <= 8:
            return [int(parsed)]
        return [int(current_module_number)]

    def live_question_image_path(
        self, class_id: int, filename: str
    ) -> Path | None:
        """Return the on-disk path for one staff-authored live question image.

        Args:
            class_id: Game-show ``classes.id``.
            filename: Safe stored filename (digest + extension).

        Returns:
            Existing file path, or ``None``.
        """
        safe = Path(str(filename or "")).name
        if not safe or safe != str(filename or "").strip():
            return None
        path = Path(self.data_dir) / "live-question-images" / str(int(class_id)) / safe
        return path if path.is_file() else None

    def store_live_question_image(
        self, class_id: int, uploaded: Any, *, filename: str = ""
    ) -> dict[str, Any]:
        """Store one staff-authored question image on the data volume.

        Args:
            class_id: Game-show ``classes.id``.
            uploaded: Flask file storage or raw bytes.
            filename: Original filename, used only for the extension.

        Returns:
            Dict with ``image_url`` and ``filename``.

        Raises:
            ValueError: When the upload is missing or not a supported image.
        """
        raw = b""
        name = str(filename or "").strip()
        mime = ""
        if hasattr(uploaded, "read"):
            raw = uploaded.read() or b""
            name = name or str(getattr(uploaded, "filename", "") or "")
            mime = str(getattr(uploaded, "mimetype", "") or "")
        elif isinstance(uploaded, (bytes, bytearray)):
            raw = bytes(uploaded)
        if not raw:
            raise ValueError("image required")
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("image is too large (4 MB max)")
        ext = Path(name).suffix.lower()
        allowed = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
        if ext not in allowed:
            raise ValueError("image must be png, jpg, gif, or webp")
        if mime and not str(mime).startswith("image/"):
            raise ValueError("image must be png, jpg, gif, or webp")
        digest = hashlib.sha256(raw).hexdigest()
        stored_name = f"{digest}{ext}"
        folder = Path(self.data_dir) / "live-question-images" / str(int(class_id))
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / stored_name
        if not target.exists():
            target.write_bytes(raw)
        return {
            "filename": stored_name,
            "image_url": (
                f"/api/classes/{int(class_id)}/live-question-images/{stored_name}"
            ),
        }

    def add_staff_question_to_class_playlist(
        self,
        class_id: int,
        module: str,
        slot: str,
        *,
        question_type: str,
        text: str,
        page_number: int,
        stage: str | None = None,
        options: list[str] | None = None,
        correct_index: int | None = None,
        correct_answer: str | None = None,
        tolerance: Any = None,
        tolerance_kind: str | None = None,
        equation: str | None = None,
        image_url: str | None = None,
        save_to_bank: bool = False,
        bank_scope: str = "module",
        library_id: int | None = None,
    ) -> dict[str, Any]:
        """Add one staff-authored question to the current class overlay page.

        Always writes a ``class_live_playlist_placements`` row. Optional bank
        save uses the existing ``questions`` / overlay / module-link tables so
        Import from Bank can pick the item up later. Does not write seed JSON.

        Args:
            class_id: Game-show ``classes.id``.
            module: Module token such as ``M1``.
            slot: Live slot such as ``C2``.
            question_type: ``mc``, ``numeric``, or ``poll``.
            text: Required stem (equation is stored separately).
            page_number: Current overlay page number.
            stage: Lifecycle stage; defaults to ``round``.
            options: Four MC option strings.
            correct_index: Zero-based correct MC option.
            correct_answer: Numeric key when ``question_type`` is ``numeric``.
            tolerance: Absolute or percent window for numeric scoring.
            tolerance_kind: ``absolute`` or ``percent``.
            equation: Optional LaTeX shown below the stem.
            image_url: Optional stored image URL.
            save_to_bank: Persist into the module-bank tables.
            bank_scope: ``course``, ``module``, ``M2`` / ``2``, or similar.
            library_id: Attached pack library, required when saving to bank.

        Returns:
            Inserted placement row including parsed ``item`` payload.
        """
        try:
            from bank_mc_normalize import parse_module_token
        except ImportError:
            from lms.bank_mc_normalize import parse_module_token

        kind = str(question_type or "").strip().lower()
        if kind not in {"mc", "numeric", "poll"}:
            raise ValueError("type must be mc, numeric, or poll")
        stem = str(text or "").strip()
        if not stem:
            raise ValueError("question text required")
        module_number = parse_module_token(str(module or "").strip().upper())
        if module_number is None:
            raise ValueError("module required (e.g. M1)")
        module_key = f"M{module_number}"
        slot_key = str(slot or "").upper()
        stage_key = str(stage or "round").strip().lower() or "round"
        try:
            page = max(1, int(page_number))
        except (TypeError, ValueError):
            page = 1
        latex = str(equation or "").strip()
        image = str(image_url or "").strip()
        option_list = [str(opt or "").strip() for opt in (options or [])]
        item_payload: dict[str, Any] = {
            "item_type": "question",
            "type": kind,
            "text": stem,
            "prompt": stem,
            "stage": stage_key,
            "page_number": page,
            "publish_modes": ["individual"],
            "response_mode": "individual",
            "import_source": "staff_authored",
        }
        if latex:
            item_payload["equation"] = latex
            item_payload["equation_latex"] = latex
        if image:
            item_payload["image_url"] = image
        if kind == "mc":
            clean_options = [opt for opt in option_list if opt]
            if len(clean_options) != 4:
                raise ValueError("multiple choice needs four options")
            try:
                key_index = int(correct_index)
            except (TypeError, ValueError):
                raise ValueError("mark the correct option") from None
            if key_index < 0 or key_index > 3:
                raise ValueError("mark the correct option")
            letter = chr(ord("A") + key_index)
            item_payload["options"] = clean_options
            item_payload["choices"] = clean_options
            item_payload["key"] = letter
            item_payload["correct_answer"] = letter
        elif kind == "numeric":
            key = str(correct_answer or "").strip()
            if not key:
                raise ValueError("correct answer required")
            try:
                float(key)
            except (TypeError, ValueError) as exc:
                raise ValueError("correct answer must be a number") from exc
            kind_token = str(tolerance_kind or "absolute").strip().lower()
            if kind_token not in {"absolute", "percent"}:
                raise ValueError("tolerance_kind must be absolute or percent")
            try:
                window = float(0 if tolerance in (None, "") else tolerance)
            except (TypeError, ValueError) as exc:
                raise ValueError("tolerance must be a number") from exc
            if window < 0:
                raise ValueError("tolerance cannot be negative")
            item_payload["correct_answer"] = key
            item_payload["key"] = key
            item_payload["tolerance"] = window
            item_payload["tolerance_kind"] = kind_token
            item_payload["integer_only"] = False
            item_payload["options"] = []
            item_payload["choices"] = []
        else:
            item_payload["options"] = []
            item_payload["choices"] = []

        source_question_id = None
        if save_to_bank:
            if library_id in (None, ""):
                raise ValueError("a module pack is required to save to the bank")
            bank_id = self._ensure_staff_authored_bank(int(library_id))
            source_question_id = self._insert_staff_bank_question(
                library_id=int(library_id),
                bank_id=bank_id,
                question_type=kind,
                text=stem,
                options=list(item_payload.get("options") or []),
                correct_answer=str(item_payload.get("correct_answer") or ""),
                payload=item_payload,
            )
            for number in self._bank_scope_module_numbers(
                bank_scope, int(module_number)
            ):
                self._ensure_module_bank_link(int(library_id), number, bank_id)
            item_payload["source_question_id"] = source_question_id
            item_payload["import_source"] = "module_bank"

        with self._lock:
            existing = self.conn.execute(
                """
                SELECT MAX(sort_order) AS max_order
                FROM class_live_playlist_placements
                WHERE class_id = ? AND module = ? AND slot = ?
                  AND stage = ? AND page_number = ?
                """,
                (int(class_id), module_key, slot_key, stage_key, page),
            ).fetchone()
            sort_order = int(existing["max_order"] or 0) + 1 if existing else 1
            item_id = (
                f"bank-import-{int(source_question_id)}"
                if source_question_id is not None
                else f"staff-q-{uuid.uuid4().hex}"
            )
            placement_key = f"class:{int(class_id)}:staff:{uuid.uuid4().hex}"
            item_payload["id"] = item_id
            item_payload["item_id"] = item_id
            item_payload["order"] = sort_order
            item_payload["placement_key"] = placement_key
            stamp = _now()
            cur = self.conn.execute(
                """
                INSERT INTO class_live_playlist_placements (
                    class_id, module, slot, page_number, stage, sort_order,
                    placement_key, item_id, item_json, source_question_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(class_id),
                    module_key,
                    slot_key,
                    page,
                    stage_key,
                    sort_order,
                    placement_key,
                    item_id,
                    json.dumps(item_payload),
                    source_question_id,
                    stamp,
                ),
            )
            self.conn.commit()
            placement_id = int(cur.lastrowid)
            saved = self.conn.execute(
                """
                SELECT * FROM class_live_playlist_placements WHERE id = ?
                """,
                (placement_id,),
            ).fetchone()
        self.invalidate_live_metadata_cache(
            class_id=int(class_id),
            module=module_key,
            slot=slot_key,
        )
        active = self.get_active_live_session_for_class(int(class_id))
        if active is not None:
            teacher = self.live_session_teacher_state_payload(int(active["id"]))
            live_module = str(teacher.get("live_module") or "M1").upper()
            live_slot = str(teacher.get("live_slot") or "C1").upper()
            if live_module == module_key and live_slot == slot_key:
                self.ensure_live_session_items(int(active["id"]))
        placement = dict(saved) if saved else {}
        try:
            parsed_item = json.loads(placement.get("item_json") or "{}")
        except json.JSONDecodeError:
            parsed_item = {}
        placement["item"] = parsed_item if isinstance(parsed_item, dict) else {}
        placement.pop("item_json", None)
        return placement

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
        """Log a wrong session code and return the recent-window count."""
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
        state_raw = item.get("teacher_state_json")
        parsed_state = None
        if isinstance(state_raw, str) and state_raw.strip():
            try:
                parsed_state = json.loads(state_raw)
            except json.JSONDecodeError:
                parsed_state = None
        item["teacher_state"] = public_teacher_state(
            parsed_state if isinstance(parsed_state, dict) else None
        )
        item.pop("teacher_state_json", None)
        canvas_raw = item.get("canvas_sync_json")
        parsed_canvas = None
        if isinstance(canvas_raw, str) and canvas_raw.strip():
            try:
                parsed_canvas = json.loads(canvas_raw)
            except json.JSONDecodeError:
                parsed_canvas = None
        item["canvas_sync"] = public_canvas_sync(
            parsed_canvas if isinstance(parsed_canvas, dict) else None
        )
        item.pop("canvas_sync_json", None)
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
        last_sweep = self._sweep_at.get(int(session_id), 0.0)
        if time.monotonic() - last_sweep < LIVE_SWEEP_MIN_INTERVAL_SECONDS:
            return 0
        self._sweep_at[int(session_id)] = time.monotonic()
        now = datetime.now().replace(microsecond=0)
        skip = (except_token or "").strip()
        marked = 0
        try:
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
        except sqlite3.OperationalError:
            return 0
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
        if not self._session_open_for_student(session_row):
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
        if attendee is None:
            return False
        if attendee.get("left_at") and not self.session_is_celebrating(session_id):
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
        if not self._session_open_for_student(session_row):
            return None
        if session_row.get("status") == "active":
            last = _parse_iso_datetime(attendee.get("last_heartbeat_at"))
            fresh = (
                last is not None
                and not attendee.get("left_at")
                and (datetime.now() - last).total_seconds()
                < LIVE_HEARTBEAT_WRITE_SECONDS
            )
            if not fresh:
                self.sweep_stale_live_attendees(
                    int(attendee["live_session_id"]), except_token=str(token)
                )
                return self._resume_live_attendee(attendee)
        return dict(attendee)

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
            if not self.session_is_celebrating(int(attendee["live_session_id"])):
                return None
        session_row = self.get_live_session(int(attendee["live_session_id"]))
        if not self._session_open_for_student(session_row):
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

    def _require_active_live_session(self, session_id: int) -> dict[str, Any]:
        """Return an active session row or raise a lifecycle-safe error.

        Args:
            session_id: ``live_class_sessions.id``.

        Raises:
            KeyError: If the session does not exist.
            ValueError: If the session has ended.
        """
        row = self.get_live_session(session_id)
        if row is None:
            raise KeyError(f"live session {session_id}")
        if str(row.get("status") or "") != "active":
            raise ValueError("Session is not active.")
        return row

    @staticmethod
    def _live_item_row_to_dict(row: Any) -> dict[str, Any]:
        """Normalize a publish-lifecycle sqlite row for JSON APIs."""

        item = dict(row)
        raw = item.get("item_json") or "{}"
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except json.JSONDecodeError:
            parsed = {}
        item["item"] = parsed if isinstance(parsed, dict) else {}
        item.pop("item_json", None)
        item["show_live_results"] = bool(item.get("show_live_results"))
        item["sort_order"] = int(item.get("sort_order") or 0)
        item["page_number"] = (
            int(item["page_number"])
            if item.get("page_number") not in (None, "")
            else None
        )
        item["prompt_id"] = (
            int(item["prompt_id"])
            if item.get("prompt_id") not in (None, "")
            else None
        )
        return item

    @staticmethod
    def _question_placement_key(question: dict[str, Any], index: int) -> str:
        """Return a stable placement key without metadata-worker coupling.

        Args:
            question: Resolved metadata question/placement.
            index: Stable zero-based fallback position.
        """
        explicit = str(
            question.get("placement_key")
            or question.get("placement_id")
            or ""
        ).strip()
        if explicit:
            return explicit
        item_id = str(
            question.get("id")
            or question.get("item_id")
            or question.get("ref")
            or f"question-{index + 1}"
        ).strip()
        stage = str(question.get("stage") or "round").strip().lower()
        page = question.get("page_number")
        order = question.get("order") or index + 1
        return f"{stage}:{page if page not in (None, '') else 0}:{order}:{item_id}"

    @staticmethod
    def _question_publish_modes(question: dict[str, Any]) -> list[str]:
        """Return normalized supported publish modes for one question."""

        raw = question.get("publish_modes")
        modes = [
            str(value).strip().lower()
            for value in (raw if isinstance(raw, list) else [])
            if str(value).strip()
        ]
        if not modes:
            response_mode = str(
                question.get("response_mode") or "individual"
            ).strip().lower()
            modes = ["individual"]
            if response_mode == "group_consensus":
                modes.append("group_consensus")
        if "group_consensus" not in modes:
            item_id = str(
                question.get("id") or question.get("item_id") or ""
            ).strip().lower().replace("_", "-")
            is_meet = item_id in {"meet-team", "meet-a", "meet-b", "meet-c"}
            qtype = str(question.get("type") or "").strip().lower()
            open_ended = qtype in {"numeric", "text", "open", "share"} or bool(
                question.get("integer_only")
            )
            if open_ended and not is_meet:
                modes.append("group_consensus")
        return list(dict.fromkeys(modes))

    @staticmethod
    def _question_response_mode(question: dict[str, Any]) -> str:
        """Return ``individual`` or ``group_consensus`` metadata mode."""

        token = str(question.get("response_mode") or "individual").strip().lower()
        return "group_consensus" if token == "group_consensus" else "individual"

    @staticmethod
    def _question_answer_kind(item: dict[str, Any]) -> str:
        """Return ``numeric`` or ``mc`` from catalogue type, not session kind.

        Lifecycle rows store ``kind`` as the item type (``question``). Using
        that token would publish a numeric stem as an empty MC.

        Args:
            item: Lifecycle row, optionally with a nested catalogue ``item``.
        """
        question = item.get("item") if isinstance(item.get("item"), dict) else {}
        token = str(
            question.get("type")
            or question.get("kind")
            or item.get("type")
            or ""
        ).strip().lower()
        if token == "question":
            token = ""
        if token == "numeric" or question.get("integer_only") or item.get(
            "integer_only"
        ):
            return "numeric"
        if token == "poll":
            return "poll"
        return "mc"

    @staticmethod
    def _singular_question_key(question: Any) -> str:
        """Return one authored answer key, or empty when unkeyed or ambiguous.

        Numeric and MC items may store the key as ``correct_answer`` or
        ``key``. A list counts as singular only when it has exactly one
        non-empty value. Polls and multi-key items stay unkeyed.

        Args:
            question: Catalogue item or prompt payload.
        """
        if not isinstance(question, dict):
            return ""
        raw = question.get("correct_answer")
        if raw in (None, ""):
            raw = question.get("key")
        if isinstance(raw, list):
            values = [str(item).strip() for item in raw if str(item).strip()]
            return values[0] if len(values) == 1 else ""
        return str(raw or "").strip()

    def _attach_singular_answer_key(
        self, payload: dict[str, Any], question: dict[str, Any]
    ) -> dict[str, Any]:
        """Copy a singular authored key onto a live prompt payload.

        Args:
            payload: Mutable prompt payload.
            question: Catalogue item that may hold ``correct_answer``.

        Returns:
            The same payload dict, with ``key`` / ``correct_answer`` set when
            exactly one authored key exists.
        """
        key = self._singular_question_key(question) or self._singular_question_key(
            payload
        )
        if key:
            payload["key"] = key
            payload["correct_answer"] = key
        return payload

    def _repair_numeric_live_prompt(
        self, item: dict[str, Any], prompt: dict[str, Any]
    ) -> dict[str, Any]:
        """Rewrite a compatibility prompt that lost its numeric kind.

        Also attaches a singular authored key when the catalogue item has
        exactly one ``correct_answer``. Unkeyed numeric items stay unkeyed.

        Args:
            item: Lifecycle row that owns the prompt.
            prompt: Existing linked prompt row.

        Returns:
            The original prompt, or the repaired numeric prompt.
        """
        if self._question_answer_kind(item) != "numeric":
            return prompt
        payload = (
            dict(prompt.get("payload") or {})
            if isinstance(prompt.get("payload"), dict)
            else {}
        )
        question = item.get("item") if isinstance(item.get("item"), dict) else {}
        before_key = str(
            payload.get("key") or payload.get("correct_answer") or ""
        ).strip()
        self._attach_singular_answer_key(payload, question)
        after_key = str(payload.get("key") or "").strip()
        already_numeric = (
            str(prompt.get("kind") or "").strip().lower() == "numeric"
            and str(payload.get("kind") or "").strip().lower() == "numeric"
        )
        if already_numeric and before_key == after_key:
            return prompt
        payload["kind"] = "numeric"
        payload["type"] = "numeric"
        payload["integer_only"] = bool(
            question.get("integer_only") or payload.get("integer_only") or True
        )
        payload["options"] = []
        payload["choices"] = []
        placeholder = str(
            question.get("placeholder") or payload.get("placeholder") or ""
        ).strip()
        if placeholder:
            payload["placeholder"] = placeholder
        try:
            slide_index = int(prompt.get("slide_index"))
        except (TypeError, ValueError):
            slide_index = 20000 + int(item["id"])
        return self.set_live_session_prompt(
            int(item["live_session_id"]),
            slide_index=slide_index,
            kind="numeric",
            payload=payload,
            activate=False,
        )

    def ensure_live_session_items(self, session_id: int) -> list[dict[str, Any]]:
        """Seed inactive lifecycle rows for every resolved question placement.

        The method consumes only the resolver's existing ``questions`` list and
        optional fields on each row, so schema-v1 metadata and the metadata
        worker's additive schema-v2 fields are both supported.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        self._require_active_live_session(session_id)
        metadata = self.live_class_metadata_for_session(session_id)
        questions = [
            dict(row)
            for row in metadata.get("questions") or []
            if isinstance(row, dict)
        ]
        question_by_ref = {
            str(
                row.get("ref")
                or row.get("item_ref")
                or row.get("id")
                or ""
            ): row
            for row in questions
        }
        placements = [
            dict(row)
            for row in metadata.get("items") or []
            if isinstance(row, dict)
        ]
        if not placements:
            placements = questions
        have_types = {
            str(row.get("item_type") or row.get("kind") or "").strip().lower()
            for row in placements
        }
        if "whiteboard" not in have_types:
            placements.append(
                {
                    "ref": "universal/whiteboard/live-workspace",
                    "item_ref": "universal/whiteboard/live-workspace",
                    "item_type": "whiteboard",
                    "id": "whiteboard",
                    "stage": "meet",
                    "order": 90,
                    "type": "whiteboard",
                }
            )
        prompts = self._list_live_session_prompts(session_id)
        prompt_by_item: dict[str, dict[str, Any]] = {}
        for prompt in prompts:
            payload = (
                prompt.get("payload")
                if isinstance(prompt.get("payload"), dict)
                else {}
            )
            item_id = str(
                payload.get("item_id")
                or payload.get("pack")
                or ""
            ).strip()
            if item_id and item_id not in prompt_by_item:
                prompt_by_item[item_id] = prompt
        now = _now()
        stage_order = {
            "join": 0,
            "teams": 1,
            "meet": 2,
            "round": 3,
            "play": 4,
        }
        with self._lock:
            for index, placement in enumerate(placements):
                lookup = str(
                    placement.get("ref")
                    or placement.get("item_ref")
                    or placement.get("id")
                    or ""
                )
                question = {
                    **placement,
                    **(
                        question_by_ref.get(lookup) or {}
                        if placement.get("item_type") == "question"
                        else {}
                    ),
                }
                placement_key = self._question_placement_key(question, index)
                item_id = str(
                    question.get("id")
                    or question.get("item_id")
                    or question.get("ref")
                    or placement_key
                ).strip()
                stage = str(question.get("stage") or "round").strip().lower()
                page_raw = question.get("page_number")
                try:
                    page_number = (
                        int(page_raw) if page_raw not in (None, "") else None
                    )
                except (TypeError, ValueError):
                    page_number = None
                try:
                    order = max(1, int(question.get("order") or index + 1))
                except (TypeError, ValueError):
                    order = index + 1
                sort_order = (
                    stage_order.get(stage, 9) * 100000
                    + (page_number or 0) * 1000
                    + order
                )
                kind = str(
                    question.get("item_type")
                    or question.get("type")
                    or question.get("kind")
                    or "question"
                ).strip().lower()
                response_mode = self._question_response_mode(question)
                default_publish = (
                    "group_consensus"
                    if response_mode == "group_consensus"
                    and "group_consensus"
                    in self._question_publish_modes(question)
                    else "individual"
                )
                prompt = prompt_by_item.get(item_id)
                prompt_id = int(prompt["id"]) if prompt else None
                self.conn.execute(
                    """
                    INSERT INTO live_session_items (
                        live_session_id, placement_key, item_id, stage,
                        page_number, sort_order, kind, item_json, prompt_id,
                        status, publish_mode, response_mode, show_live_results,
                        published_at, closed_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'inactive', ?, ?, 1,
                              NULL, NULL, ?, ?)
                    ON CONFLICT(live_session_id, placement_key) DO UPDATE SET
                        item_id = excluded.item_id,
                        stage = excluded.stage,
                        page_number = excluded.page_number,
                        sort_order = excluded.sort_order,
                        kind = excluded.kind,
                        item_json = excluded.item_json,
                        prompt_id = COALESCE(
                            live_session_items.prompt_id, excluded.prompt_id
                        ),
                        response_mode = CASE
                            WHEN live_session_items.status = 'inactive'
                            THEN excluded.response_mode
                            ELSE live_session_items.response_mode
                        END,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(session_id),
                        placement_key,
                        item_id,
                        stage,
                        page_number,
                        sort_order,
                        kind,
                        json.dumps(question),
                        prompt_id,
                        default_publish,
                        response_mode,
                        now,
                        now,
                    ),
                )
            self.conn.commit()
        return self.list_live_session_items(session_id)

    def list_live_session_items(self, session_id: int) -> list[dict[str, Any]]:
        """Return existing lifecycle rows without seeding new placements.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM live_session_items
                WHERE live_session_id = ?
                ORDER BY sort_order ASC, id ASC
                """,
                (int(session_id),),
            ).fetchall()
        return [self._live_item_row_to_dict(row) for row in rows]

    def _expected_live_item_placement_keys(self, session_id: int) -> set[str]:
        """Return placement keys ``ensure_live_session_items`` would persist.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        metadata = self.live_class_metadata_for_session(session_id)
        questions = [
            dict(row)
            for row in metadata.get("questions") or []
            if isinstance(row, dict)
        ]
        question_by_ref = {
            str(row.get("ref") or row.get("item_ref") or row.get("id") or ""): row
            for row in questions
        }
        placements = [
            dict(row)
            for row in metadata.get("items") or []
            if isinstance(row, dict)
        ]
        if not placements:
            placements = questions
        have_types = {
            str(row.get("item_type") or row.get("kind") or "").strip().lower()
            for row in placements
        }
        if "whiteboard" not in have_types:
            placements.append(
                {
                    "ref": "universal/whiteboard/live-workspace",
                    "item_ref": "universal/whiteboard/live-workspace",
                    "item_type": "whiteboard",
                    "id": "whiteboard",
                    "stage": "meet",
                    "order": 90,
                    "type": "whiteboard",
                }
            )
        keys: set[str] = set()
        for index, placement in enumerate(placements):
            lookup = str(
                placement.get("ref")
                or placement.get("item_ref")
                or placement.get("id")
                or ""
            )
            question = {
                **placement,
                **(
                    question_by_ref.get(lookup) or {}
                    if placement.get("item_type") == "question"
                    else {}
                ),
            }
            keys.add(self._question_placement_key(question, index))
        return keys

    def ensure_live_session_items_if_stale(
        self, session_id: int
    ) -> list[dict[str, Any]]:
        """Seed missing placements once; skip the write when keys already match.

        Staff publish cards need lifecycle row ids. Polls must not rewrite
        every placement on every full snapshot.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        existing = self.list_live_session_items(session_id)
        have = {str(row.get("placement_key") or "") for row in existing}
        expected = self._expected_live_item_placement_keys(session_id)
        if expected and expected <= have:
            if not self._live_session_items_placement_stale(session_id, existing):
                return existing
        return self.ensure_live_session_items(session_id)

    def _live_session_items_placement_stale(
        self, session_id: int, existing: list[dict[str, Any]]
    ) -> bool:
        """True when a lifecycle row still has a pre-move stage or page.

        Args:
            session_id: ``live_class_sessions.id``.
            existing: Current ``live_session_items`` rows.

        Returns:
            True when metadata dest stage/page differs from a session row.
        """
        metadata = self.live_class_metadata_for_session(session_id)
        wanted: dict[str, tuple[str, int | None]] = {}
        for row in metadata.get("questions") or []:
            if not isinstance(row, dict):
                continue
            token = str(row.get("id") or row.get("item_id") or "").strip()
            if not token:
                continue
            stage = str(row.get("stage") or "").strip().lower()
            try:
                page = (
                    int(row["page_number"])
                    if row.get("page_number") not in (None, "")
                    else None
                )
            except (TypeError, ValueError):
                page = None
            wanted[token] = (stage, page)
        for item in existing:
            token = str(item.get("item_id") or "").strip()
            dest = wanted.get(token)
            if dest is None:
                for key, value in wanted.items():
                    if self._same_live_item_id(key, token):
                        dest = value
                        break
            if dest is None:
                continue
            stage, page = dest
            item_stage = str(item.get("stage") or "").strip().lower()
            try:
                item_page = (
                    int(item["page_number"])
                    if item.get("page_number") not in (None, "")
                    else None
                )
            except (TypeError, ValueError):
                item_page = None
            if stage and item_stage and stage != item_stage:
                return True
            if page is not None and item_page is not None and page != item_page:
                return True
        return False

    def get_live_session_item(
        self, session_id: int, placement_or_item: str | int
    ) -> dict[str, Any]:
        """Resolve one lifecycle row by id, placement key, or unique item id.

        Args:
            session_id: ``live_class_sessions.id``.
            placement_or_item: Row id, placement key, or metadata item id.
        """
        self.ensure_live_session_items(session_id)
        token = str(placement_or_item or "").strip()
        if not token:
            raise ValueError("placement_key is required")
        with self._lock:
            row = None
            if token.isdigit():
                row = self.conn.execute(
                    """
                    SELECT * FROM live_session_items
                    WHERE live_session_id = ? AND id = ?
                    """,
                    (int(session_id), int(token)),
                ).fetchone()
            if row is None:
                row = self.conn.execute(
                    """
                    SELECT * FROM live_session_items
                    WHERE live_session_id = ? AND placement_key = ?
                    """,
                    (int(session_id), token),
                ).fetchone()
            if row is None:
                matches = self.conn.execute(
                    """
                    SELECT * FROM live_session_items
                    WHERE live_session_id = ? AND item_id = ?
                    ORDER BY sort_order ASC, id ASC
                    """,
                    (int(session_id), token),
                ).fetchall()
                if len(matches) > 1:
                    raise ValueError(
                        "item_id has multiple placements; use placement_key"
                    )
                row = matches[0] if matches else None
        if row is None:
            raise KeyError(f"live item {token}")
        return self._live_item_row_to_dict(row)

    def _prompt_for_live_item(
        self, item: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Return the linked prompt row for a lifecycle item."""

        prompt_id = item.get("prompt_id")
        if prompt_id in (None, ""):
            return None
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM live_session_prompts WHERE id = ?",
                (int(prompt_id),),
            ).fetchone()
        return self._prompt_row_to_dict(row) if row else None

    def _ensure_prompt_for_live_item(
        self, item: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Create and link a compatibility prompt for a question item."""

        prompt = self._prompt_for_live_item(item)
        if prompt is not None:
            return self._repair_numeric_live_prompt(item, prompt)
        item_id_norm = str(item.get("item_id") or "").strip().lower().replace("_", "-")
        session_id = int(item["live_session_id"])
        if item_id_norm in {"minds-on", "minds_on"}:
            existing = self._prompt_at_slide(session_id, int(MINDS_ON_SLIDE_INDEX))
            if existing is None:
                self.ensure_waiting_room_minds_on(session_id)
                existing = self._prompt_at_slide(session_id, int(MINDS_ON_SLIDE_INDEX))
            if existing is not None:
                with self._lock:
                    self.conn.execute(
                        """
                        UPDATE live_session_items
                        SET prompt_id = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (int(existing["id"]), _now(), int(item["id"])),
                    )
                    self.conn.commit()
                return existing
        if item_id_norm in {"meet-team", "meet-a"}:
            for existing in self._list_live_session_prompts(session_id):
                if is_meet_team_payload(existing.get("payload")):
                    with self._lock:
                        self.conn.execute(
                            """
                            UPDATE live_session_items
                            SET prompt_id = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (int(existing["id"]), _now(), int(item["id"])),
                        )
                        self.conn.commit()
                    return existing
            payload = meet_team_prompt_payload()
            prompt = self.set_live_session_prompt(
                session_id,
                slide_index=int(MEET_TEAM_SLIDE_INDEX),
                kind=MEET_TEAM_KIND,
                payload=payload,
                activate=False,
            )
            with self._lock:
                self.conn.execute(
                    """
                    UPDATE live_session_items
                    SET prompt_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (int(prompt["id"]), _now(), int(item["id"])),
                )
                self.conn.commit()
            return prompt
        question = item.get("item") if isinstance(item.get("item"), dict) else {}
        item_type = str(
            question.get("item_type")
            or ("question" if item.get("kind") in {"poll", "mc", "numeric"} else "")
        )
        if item_type != "question":
            return None
        item_id = str(item.get("item_id") or item.get("placement_key") or "")
        prompt_kind = self._question_answer_kind(item)
        payload = {
            **question,
            "item_id": item_id,
            "prompt": str(
                question.get("text")
                or question.get("prompt")
                or question.get("question")
                or ""
            ).strip(),
            "choices": list(
                question.get("options") or question.get("choices") or []
            ),
            "kind": prompt_kind,
            "type": question.get("type") or prompt_kind,
            "page_number": item.get("page_number"),
        }
        if prompt_kind == "poll":
            payload.pop("key", None)
            payload.pop("correct_answer", None)
            payload["options"] = []
            payload["choices"] = []
        else:
            self._attach_singular_answer_key(payload, question)
        if prompt_kind == "numeric":
            payload["integer_only"] = bool(question.get("integer_only", True))
            payload["options"] = []
            payload["choices"] = []
            placeholder = str(question.get("placeholder") or "").strip()
            if placeholder:
                payload["placeholder"] = placeholder
            if question.get("tolerance") not in (None, ""):
                try:
                    payload["tolerance"] = float(question.get("tolerance"))
                except (TypeError, ValueError):
                    payload["tolerance"] = 0.0
            kind = str(question.get("tolerance_kind") or "").strip().lower()
            if kind in {"absolute", "percent"}:
                payload["tolerance_kind"] = kind
        equation = str(
            question.get("equation_latex") or question.get("equation") or ""
        ).strip()
        if equation:
            payload["equation"] = equation
            payload["equation_latex"] = equation
        image_url = str(question.get("image_url") or "").strip()
        if image_url:
            payload["image_url"] = image_url
        session_kind = "share" if prompt_kind == "poll" else prompt_kind
        prompt = self.set_live_session_prompt(
            int(item["live_session_id"]),
            slide_index=20000 + int(item["id"]),
            kind=session_kind,
            payload=payload,
            activate=False,
        )
        with self._lock:
            self.conn.execute(
                """
                UPDATE live_session_items
                SET prompt_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(prompt["id"]), _now(), int(item["id"])),
            )
            self.conn.commit()
        return prompt

    def publish_live_session_item(
        self,
        session_id: int,
        placement_or_item: str | int,
        *,
        publish_mode: str = "individual",
    ) -> dict[str, Any]:
        """Publish one inactive placement without closing other items.

        Args:
            session_id: ``live_class_sessions.id``.
            placement_or_item: Placement key, unique item id, or lifecycle id.
            publish_mode: ``individual`` or an explicitly supported group mode.
        """
        self._require_active_live_session(session_id)
        item = self.get_live_session_item(session_id, placement_or_item)
        if item["status"] == "closed":
            raise ValueError("Closed items cannot be republished.")
        mode = str(publish_mode or "individual").strip().lower()
        if mode in {"group", "individual_in_group"}:
            mode = "group_consensus"
        supported = self._question_publish_modes(item.get("item") or {})
        if mode not in supported:
            raise ValueError(f"publish mode is not supported: {mode}")
        response_mode = (
            "group_consensus" if mode == "group_consensus" else "individual"
        )
        if response_mode == "group_consensus":
            teacher = self.live_session_teacher_state_payload(session_id)
            if not (
                teacher.get("groups_configured")
                and teacher.get("run_as_group")
            ):
                raise ValueError(
                    "Set up groups and enable Run as Group before publishing."
                )
        prompt = self._ensure_prompt_for_live_item(item)
        now = _now()
        with self._lock:
            self.conn.execute(
                """
                UPDATE live_session_items
                SET prompt_id = COALESCE(?, prompt_id),
                    status = 'active', publish_mode = ?,
                    response_mode = ?, published_at = COALESCE(published_at, ?),
                    closed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    int(prompt["id"]) if prompt is not None else None,
                    mode,
                    response_mode,
                    now,
                    now,
                    int(item["id"]),
                ),
            )
            self.conn.commit()
        published = self.get_live_session_item(session_id, int(item["id"]))
        published_id = str(published.get("item_id") or "").strip().lower().replace(
            "-", "_"
        )
        if published_id in {"minds_on"}:
            self.activate_join_minds_on(session_id)
        if (
            str(published.get("stage") or "").strip().lower() == "join"
            and published_id not in {"minds_on"}
        ):
            self._inactivate_join_minds_on_items(session_id)
            legacy = self.get_active_live_prompt(session_id)
            if legacy and is_minds_on_payload(legacy.get("payload")):
                self.clear_active_live_prompt(session_id)
        if response_mode == "group_consensus":
            self._initialize_group_consensus(published)
        return self.get_live_session_item(session_id, int(item["id"]))

    def close_live_session_item(
        self, session_id: int, placement_or_item: str | int
    ) -> dict[str, Any]:
        """Close one active item, locking submissions and freezing results."""

        self._require_active_live_session(session_id)
        item = self.get_live_session_item(session_id, placement_or_item)
        if item["status"] != "active":
            raise ValueError("Only an active item can be closed.")
        if item.get("response_mode") == "group_consensus":
            self.end_group_consensus_voting(session_id, int(item["id"]))
        now = _now()
        with self._lock:
            self.conn.execute(
                """
                UPDATE live_session_items
                SET status = 'closed', closed_at = ?, updated_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (now, now, int(item["id"])),
            )
            self.conn.commit()
        return self.get_live_session_item(session_id, int(item["id"]))

    def update_live_session_item_settings(
        self,
        session_id: int,
        placement_or_item: str | int,
        *,
        show_live_results: Any,
    ) -> dict[str, Any]:
        """Update governed result visibility for one session item."""

        self._require_active_live_session(session_id)
        item = self.get_live_session_item(session_id, placement_or_item)
        if isinstance(show_live_results, str):
            token = show_live_results.strip().lower()
            if token not in {"1", "0", "true", "false", "yes", "no", "on", "off"}:
                raise ValueError("show_live_results must be a boolean")
            visible = token in {"1", "true", "yes", "on"}
        elif isinstance(show_live_results, (bool, int)):
            visible = bool(show_live_results)
        else:
            raise ValueError("show_live_results must be a boolean")
        with self._lock:
            self.conn.execute(
                """
                UPDATE live_session_items
                SET show_live_results = ?, updated_at = ?
                WHERE id = ?
                """,
                (1 if visible else 0, _now(), int(item["id"])),
            )
            self.conn.commit()
        return self.get_live_session_item(session_id, int(item["id"]))

    def list_active_live_questions(self, session_id: int) -> list[dict[str, Any]]:
        """Return independently active question prompts in placement order."""

        with self._lock:
            rows = self.conn.execute(
                """
                SELECT p.*
                FROM live_session_items i
                JOIN live_session_prompts p ON p.id = i.prompt_id
                WHERE i.live_session_id = ? AND i.status = 'active'
                ORDER BY i.sort_order ASC, i.id ASC
                """,
                (int(session_id),),
            ).fetchall()
        return [self._prompt_row_to_dict(row) for row in rows]

    def _named_teams_for_live_session(
        self, session_id: int
    ) -> list[dict[str, Any]]:
        """Return current non-Class teams for a live session."""

        session_row = self.get_live_session(session_id)
        if session_row is None:
            return []
        try:
            state = self.game.game_state(int(session_row["class_id"]))
        except Exception:  # noqa: BLE001 - group setup may not exist yet
            return []
        teams = [
            dict(team)
            for team in state.get("teams") or []
            if str(team.get("name") or "") != "Class"
        ]
        teams.sort(
            key=lambda team: (
                int(team.get("sort_order") or 0),
                int(team.get("id") or 0),
            )
        )
        return teams

    def _active_team_member_ids(
        self, session_id: int, team_id: int
    ) -> list[int]:
        """Return currently joined roster ids assigned to one team."""

        active = {
            int(row["student_id"])
            for row in self.list_live_session_attendees(
                session_id, present_only=True
            )
            if row.get("student_id") not in (None, "")
        }
        members: list[int] = []
        for team in self._named_teams_for_live_session(session_id):
            if int(team.get("id") or 0) != int(team_id):
                continue
            for member in team.get("members") or []:
                try:
                    student_id = int(member["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                if student_id in active:
                    members.append(student_id)
        return sorted(set(members))

    def _initialize_group_consensus(self, item: dict[str, Any]) -> None:
        """Snapshot active team members when group voting is published."""

        live_item_id = int(item["id"])
        session_id = int(item["live_session_id"])
        now = _now()
        with self._lock:
            for team in self._named_teams_for_live_session(session_id):
                team_id = int(team["id"])
                self.conn.execute(
                    """
                    INSERT INTO live_group_responses (
                        live_item_id, team_id, status, vote_summary_json,
                        proposed_answer_json, final_answer_json,
                        finalizer_student_id, awarded_points, voting_ended_at,
                        finalized_at, created_at, updated_at
                    ) VALUES (?, ?, 'collecting_votes', '[]', NULL, NULL,
                              NULL, NULL, NULL, NULL, ?, ?)
                    ON CONFLICT(live_item_id, team_id) DO NOTHING
                    """,
                    (live_item_id, team_id, now, now),
                )
                for student_id in self._active_team_member_ids(
                    session_id, team_id
                ):
                    self.conn.execute(
                        """
                        INSERT OR IGNORE INTO live_group_members (
                            live_item_id, team_id, student_id, joined_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (live_item_id, team_id, student_id, now),
                    )
            self.conn.commit()

    @staticmethod
    def _normalize_group_answer(response: Any) -> dict[str, Any]:
        """Normalize a private vote or final team answer.

        Args:
            response: JSON answer object containing choice, value, or text.

        Raises:
            ValueError: If no supported non-empty answer is present.
        """
        body = response if isinstance(response, dict) else {}
        if body.get("choice") not in (None, ""):
            return {
                "kind": "choice",
                "value": str(body["choice"]).strip()[:500],
            }
        if body.get("value") not in (None, ""):
            raw = body["value"]
            try:
                number = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("value must be numeric") from exc
            return {
                "kind": "value",
                "value": int(number) if number.is_integer() else number,
            }
        text = str(body.get("text") or body.get("share") or "").strip()
        if text:
            return {"kind": "text", "value": text[:2000]}
        raise ValueError("An answer is required.")

    def _group_response_row(
        self, live_item_id: int, team_id: int
    ) -> dict[str, Any] | None:
        """Return one normalized canonical team-response row."""

        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM live_group_responses
                WHERE live_item_id = ? AND team_id = ?
                """,
                (int(live_item_id), int(team_id)),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for source, target, fallback in (
            ("vote_summary_json", "vote_summary", []),
            ("proposed_answer_json", "proposed_answer", None),
            ("final_answer_json", "final_answer", None),
        ):
            raw = result.pop(source, None)
            try:
                parsed = json.loads(raw) if raw else fallback
            except (TypeError, json.JSONDecodeError):
                parsed = fallback
            result[target] = parsed
        return result

    def _group_vote_rows(
        self, live_item_id: int, team_id: int
    ) -> list[dict[str, Any]]:
        """Return normalized private vote rows for internal/teacher use."""

        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM live_group_votes
                WHERE live_item_id = ? AND team_id = ?
                ORDER BY id ASC
                """,
                (int(live_item_id), int(team_id)),
            ).fetchall()
        votes: list[dict[str, Any]] = []
        for row in rows:
            vote = dict(row)
            try:
                answer = json.loads(vote.pop("response_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                answer = {}
            vote["answer"] = answer if isinstance(answer, dict) else {}
            votes.append(vote)
        return votes

    @staticmethod
    def _group_member_answer_values(votes: list[dict[str, Any]]) -> list[Any]:
        """Return anonymous answer values from each vote in submit order.

        Args:
            votes: Normalized vote rows from ``_group_vote_rows``.

        Returns:
            Each vote's ``answer.value`` without student identifiers.
        """
        values: list[Any] = []
        for vote in votes:
            answer = vote.get("answer") or {}
            if isinstance(answer, dict) and "value" in answer:
                values.append(answer["value"])
        return values

    def _transition_group_team_to_discussion(
        self, live_item_id: int, team_id: int
    ) -> dict[str, Any]:
        """Freeze private votes and reveal a team-only distribution."""

        votes = self._group_vote_rows(live_item_id, team_id)
        counts: dict[str, dict[str, Any]] = {}
        for vote in votes:
            answer = vote.get("answer") or {}
            key = json.dumps(answer, sort_keys=True, separators=(",", ":"))
            if key not in counts:
                counts[key] = {"answer": answer, "count": 0}
            counts[key]["count"] += 1
        summary = sorted(
            counts.values(),
            key=lambda row: (
                -int(row["count"]),
                json.dumps(row["answer"], sort_keys=True),
            ),
        )
        proposal = None
        if summary and (
            len(summary) == 1
            or int(summary[0]["count"]) > int(summary[1]["count"])
        ):
            proposal = summary[0]["answer"]
        status = "awaiting_team_answer" if proposal is not None else "discussion"
        now = _now()
        with self._lock:
            self.conn.execute(
                """
                UPDATE live_group_responses
                SET status = ?, vote_summary_json = ?,
                    proposed_answer_json = ?, voting_ended_at = ?,
                    updated_at = ?
                WHERE live_item_id = ? AND team_id = ?
                  AND status = 'collecting_votes'
                """,
                (
                    status,
                    json.dumps(summary),
                    json.dumps(proposal) if proposal is not None else None,
                    now,
                    now,
                    int(live_item_id),
                    int(team_id),
                ),
            )
            self.conn.commit()
        result = self._group_response_row(live_item_id, team_id)
        if result is None:
            raise KeyError(f"group response {live_item_id}:{team_id}")
        return result

    def _advance_group_team_if_ready(
        self, item: dict[str, Any], team_id: int
    ) -> dict[str, Any]:
        """Advance automatically when every currently active member voted."""

        state = self._group_response_row(int(item["id"]), int(team_id))
        if state is None:
            raise KeyError(f"group response {item['id']}:{team_id}")
        if state["status"] != "collecting_votes":
            return state
        active_ids = self._active_team_member_ids(
            int(item["live_session_id"]), int(team_id)
        )
        if not active_ids:
            return state
        voted = {
            int(row["student_id"])
            for row in self._group_vote_rows(int(item["id"]), int(team_id))
        }
        if set(active_ids).issubset(voted):
            return self._transition_group_team_to_discussion(
                int(item["id"]), int(team_id)
            )
        return state

    def submit_group_consensus_vote(
        self,
        session_id: int,
        live_item_id: int,
        student_id: int,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        """Store one private member vote and auto-advance a complete team."""

        self._require_active_live_session(session_id)
        item = self.get_live_session_item(session_id, live_item_id)
        if (
            item["status"] != "active"
            or item["response_mode"] != "group_consensus"
        ):
            raise ValueError("Group voting is not active for this item.")
        if not self.student_is_active_live_attendee(session_id, student_id):
            raise ValueError("Student is not active in this session.")
        session_row = self.get_live_session(session_id)
        assert session_row is not None
        team_id = self.student_team_id_for_class(
            int(session_row["class_id"]), int(student_id)
        )
        if team_id is None:
            raise ValueError("Student is not assigned to a group.")
        state = self._group_response_row(int(item["id"]), team_id)
        if state is None or state["status"] != "collecting_votes":
            raise ValueError("Voting has ended for this group.")
        answer = self._normalize_group_answer(response)
        now = _now()
        with self._lock:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO live_group_members (
                    live_item_id, team_id, student_id, joined_at
                ) VALUES (?, ?, ?, ?)
                """,
                (int(item["id"]), team_id, int(student_id), now),
            )
            self.conn.execute(
                """
                INSERT INTO live_group_votes (
                    live_item_id, team_id, student_id, response_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(live_item_id, team_id, student_id) DO UPDATE SET
                    response_json = excluded.response_json,
                    updated_at = excluded.updated_at
                """,
                (
                    int(item["id"]),
                    team_id,
                    int(student_id),
                    json.dumps(answer),
                    now,
                    now,
                ),
            )
            self.conn.commit()
        team_state = self._advance_group_team_if_ready(item, team_id)
        return {
            "live_item_id": int(item["id"]),
            "team_id": team_id,
            "my_vote": answer,
            "team": self._public_student_group_state(
                item, team_id, int(student_id), team_state=team_state
            ),
        }

    def end_group_consensus_voting(
        self, session_id: int, placement_or_item: str | int
    ) -> dict[str, Any]:
        """Teacher fallback: advance every unfinished team to discussion."""

        self._require_active_live_session(session_id)
        item = self.get_live_session_item(session_id, placement_or_item)
        if (
            item["status"] != "active"
            or item["response_mode"] != "group_consensus"
        ):
            raise ValueError("Group voting is not active for this item.")
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT team_id FROM live_group_responses
                WHERE live_item_id = ? AND status = 'collecting_votes'
                ORDER BY team_id ASC
                """,
                (int(item["id"]),),
            ).fetchall()
        for row in rows:
            self._transition_group_team_to_discussion(
                int(item["id"]), int(row["team_id"])
            )
        return self.teacher_group_consensus_summary(
            session_id, int(item["id"])
        )

    def finalize_group_consensus_answer(
        self,
        session_id: int,
        live_item_id: int,
        student_id: int,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically record the one canonical answer for a student's team."""

        self._require_active_live_session(session_id)
        item = self.get_live_session_item(session_id, live_item_id)
        if (
            item["status"] != "active"
            or item["response_mode"] != "group_consensus"
        ):
            raise ValueError("Group consensus is not active for this item.")
        session_row = self.get_live_session(session_id)
        assert session_row is not None
        team_id = self.student_team_id_for_class(
            int(session_row["class_id"]), int(student_id)
        )
        if team_id is None:
            raise ValueError("Student is not assigned to a group.")
        with self._lock:
            member = self.conn.execute(
                """
                SELECT 1 FROM live_group_members
                WHERE live_item_id = ? AND team_id = ? AND student_id = ?
                """,
                (int(item["id"]), int(team_id), int(student_id)),
            ).fetchone()
        if member is None:
            raise ValueError("Student is not a member of this group.")
        answer = self._normalize_group_answer(response)
        now = _now()
        with self._lock:
            cursor = self.conn.execute(
                """
                UPDATE live_group_responses
                SET status = 'finalized', final_answer_json = ?,
                    finalizer_student_id = ?, finalized_at = ?, updated_at = ?
                WHERE live_item_id = ? AND team_id = ?
                  AND status IN ('discussion', 'awaiting_team_answer')
                  AND final_answer_json IS NULL
                """,
                (
                    json.dumps(answer),
                    int(student_id),
                    now,
                    now,
                    int(item["id"]),
                    int(team_id),
                ),
            )
            self.conn.commit()
        if cursor.rowcount != 1:
            raise ValueError("Team answer is already finalized or voting is open.")
        return self._public_student_group_state(item, team_id, int(student_id))

    def _public_student_group_state(
        self,
        item: dict[str, Any],
        team_id: int,
        student_id: int,
        *,
        team_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return only this student's team consensus state."""

        state = team_state or self._group_response_row(int(item["id"]), team_id)
        if state is None:
            return {}
        votes = self._group_vote_rows(int(item["id"]), team_id)
        my_vote = next(
            (
                row.get("answer")
                for row in votes
                if int(row["student_id"]) == int(student_id)
            ),
            None,
        )
        with self._lock:
            eligible = self.conn.execute(
                """
                SELECT COUNT(*) AS n FROM live_group_members
                WHERE live_item_id = ? AND team_id = ?
                """,
                (int(item["id"]), int(team_id)),
            ).fetchone()
            member = self.conn.execute(
                """
                SELECT 1 FROM live_group_members
                WHERE live_item_id = ? AND team_id = ? AND student_id = ?
                """,
                (int(item["id"]), int(team_id), int(student_id)),
            ).fetchone()
        is_member = member is not None
        public = {
            "team_id": int(team_id),
            "status": str(state["status"]),
            "has_voted": my_vote is not None,
            "my_vote": my_vote,
            "vote_count": len(votes),
            "eligible_count": int((eligible or {"n": 0})["n"] or 0),
            "eligible": is_member,
            "can_vote": is_member and state["status"] == "collecting_votes",
            "can_finalize": is_member
            and state["status"]
            in {"discussion", "awaiting_team_answer"},
        }
        if state["status"] != "collecting_votes":
            public["vote_summary"] = state.get("vote_summary") or []
            public["proposed_answer"] = state.get("proposed_answer")
            public["member_answers"] = self._group_member_answer_values(votes)
        if state["status"] == "finalized":
            public["final_answer"] = state.get("final_answer")
            public["finalizer_student_id"] = state.get("finalizer_student_id")
            public["finalized_at"] = state.get("finalized_at")
        return public

    def student_group_consensus_state(
        self, item: dict[str, Any], student_id: int | None
    ) -> dict[str, Any] | None:
        """Return team-only consensus data for one roster student."""

        if student_id in (None, ""):
            return None
        session_row = self.get_live_session(int(item["live_session_id"]))
        if session_row is None:
            return None
        team_id = self.student_team_id_for_class(
            int(session_row["class_id"]), int(student_id)
        )
        if team_id is None:
            return None
        state = self._group_response_row(int(item["id"]), int(team_id))
        if state is None:
            return None
        if state.get("status") == "collecting_votes":
            state = self._advance_group_team_if_ready(item, int(team_id))
        return self._public_student_group_state(
            item, int(team_id), int(student_id), team_state=state
        )

    def teacher_group_consensus_summary(
        self, session_id: int, placement_or_item: str | int
    ) -> dict[str, Any]:
        """Return compact teacher summaries for every team on one item."""

        self._require_active_live_session(session_id)
        item = self.get_live_session_item(session_id, placement_or_item)
        teams = {
            int(team["id"]): team
            for team in self._named_teams_for_live_session(session_id)
        }
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT team_id FROM live_group_responses
                WHERE live_item_id = ?
                ORDER BY team_id ASC
                """,
                (int(item["id"]),),
            ).fetchall()
        summaries = []
        for row in rows:
            team_id = int(row["team_id"])
            state = self._group_response_row(int(item["id"]), team_id) or {}
            if state.get("status") == "collecting_votes":
                state = self._advance_group_team_if_ready(item, team_id)
            votes = self._group_vote_rows(int(item["id"]), team_id)
            vote_summary = state.get("vote_summary") or []
            if state.get("status") == "collecting_votes":
                counts: dict[str, dict[str, Any]] = {}
                for vote in votes:
                    answer = vote.get("answer") or {}
                    key = json.dumps(
                        answer, sort_keys=True, separators=(",", ":")
                    )
                    if key not in counts:
                        counts[key] = {"answer": answer, "count": 0}
                    counts[key]["count"] += 1
                vote_summary = sorted(
                    counts.values(),
                    key=lambda value: (
                        -int(value["count"]),
                        json.dumps(value["answer"], sort_keys=True),
                    ),
                )
            finalizer_id = state.get("finalizer_student_id")
            finalizer_name = None
            if finalizer_id not in (None, ""):
                try:
                    finalizer = self.game.get_student(
                        int(self.get_live_session(session_id)["class_id"]),
                        int(finalizer_id),
                    )
                    finalizer_name = str(
                        finalizer.get("codename")
                        or finalizer.get("first_name")
                        or ""
                    )
                except Exception:  # noqa: BLE001 - summary remains useful
                    finalizer_name = None
            eligible_count = len(
                self._active_team_member_ids(session_id, team_id)
            ) or len(votes)
            summaries.append(
                {
                    "team_id": team_id,
                    "team_name": str(
                        (teams.get(team_id) or {}).get("name")
                        or f"Team {team_id}"
                    ),
                    "status": state.get("status"),
                    "vote_count": len(votes),
                    "eligible_count": eligible_count,
                    "member_answers": self._group_member_answer_values(votes),
                    "vote_summary": vote_summary,
                    "proposed_answer": state.get("proposed_answer"),
                    "final_answer": state.get("final_answer"),
                    "changed_from_proposal": bool(
                        state.get("final_answer") is not None
                        and state.get("proposed_answer") is not None
                        and state.get("final_answer")
                        != state.get("proposed_answer")
                    ),
                    "finalizer_student_id": finalizer_id,
                    "finalizer_name": finalizer_name,
                    "finalized_at": state.get("finalized_at"),
                    "awarded_points": state.get("awarded_points"),
                }
            )
        return {"item": item, "teams": summaries}

    def award_group_consensus_points(
        self,
        session_id: int,
        placement_or_item: str | int,
        *,
        team_id: int,
        amount: int = 1,
    ) -> dict[str, Any]:
        """Award equal points to all current members of a finalized team."""

        self._require_active_live_session(session_id)
        item = self.get_live_session_item(session_id, placement_or_item)
        state = self._group_response_row(int(item["id"]), int(team_id))
        if state is None or state["status"] != "finalized":
            raise ValueError("Finalize the team answer before awarding points.")
        points = int(amount)
        if points == 0:
            raise ValueError("amount cannot be 0")
        session_row = self.get_live_session(session_id)
        assert session_row is not None
        student_ids = self._teammate_ids_for_class(
            int(session_row["class_id"]), int(team_id)
        )
        game = None
        for student_id in student_ids:
            game = self.game.award_points(
                int(session_row["class_id"]),
                kind="student",
                target_id=int(student_id),
                amount=points,
                label="Group consensus",
            )
        with self._lock:
            self.conn.execute(
                """
                UPDATE live_group_responses
                SET awarded_points = COALESCE(awarded_points, 0) + ?,
                    updated_at = ?
                WHERE live_item_id = ? AND team_id = ?
                """,
                (points, _now(), int(item["id"]), int(team_id)),
            )
            self.conn.commit()
        return {
            "awarded_student_ids": sorted(student_ids),
            "team_id": int(team_id),
            "amount": points,
            "game": game,
        }


    def lifecycle_response_counts(self, session_id: int) -> dict[int, int]:
        """Map lifecycle item ids to response counts for the current stage.

        Light staff polls use this so ``Publish`` cards show live answer counts
        without a full session snapshot on every student submit.

        Args:
            session_id: ``live_class_sessions.id``.

        Returns:
            ``{live_session_items.id: response_count}`` for active/closed
            question rows on the teacher's current stage.
        """
        try:
            teacher = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            return {}
        stage = str(teacher.get("stage") or "").strip().lower()
        counts: dict[int, int] = {}
        for item in self.list_live_session_items(session_id):
            if str(item.get("status") or "") not in {"active", "closed"}:
                continue
            item_stage = str(item.get("stage") or "").strip().lower()
            if item_stage and stage and item_stage != stage:
                continue
            kind = str(item.get("kind") or "").strip().lower()
            item_type = str((item.get("item") or {}).get("item_type") or "").strip().lower()
            if kind in {"media", "whiteboard", "slides"} or item_type in {
                "media",
                "whiteboard",
                "slides",
            }:
                continue
            prompt = self._prompt_for_live_item(item)
            if prompt is None or prompt.get("id") in (None, ""):
                continue
            counts[int(item["id"])] = len(
                self.list_live_prompt_responses(int(prompt["id"]))
            )
        return counts


    def live_session_item_results(
        self, session_id: int, placement_or_item: str | int
    ) -> dict[str, Any]:
        """Return teacher results for one active-session lifecycle item."""

        self._require_active_live_session(session_id)
        item = self.get_live_session_item(session_id, placement_or_item)
        if item["response_mode"] == "group_consensus":
            summary = self.teacher_group_consensus_summary(
                session_id, int(item["id"])
            )
            return {
                "item": item,
                "response_mode": "group_consensus",
                "teams": summary["teams"],
            }
        prompt = self._prompt_for_live_item(item)
        responses = (
            self.list_live_prompt_responses(int(prompt["id"])) if prompt else []
        )
        teacher = self.live_session_teacher_state_payload(session_id)
        tally = self._tally_for_prompt(session_id, prompt, teacher)
        eligible = len(
            self.list_live_session_attendees(session_id, present_only=True)
        )
        return {
            "item": item,
            "response_mode": "individual",
            "response_count": len(responses),
            "eligible_count": eligible,
            "tally": tally,
        }

    def student_live_items_payload(
        self,
        session_id: int,
        student_id: int | None,
        *,
        participant_uuid: str = "",
    ) -> dict[str, Any]:
        """Return ordered active questions and closed final-result cards.

        Args:
            session_id: ``live_class_sessions.id``.
            student_id: Optional roster id.
            participant_uuid: Stable live-session participant key.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None or session_row.get("status") != "active":
            return {
                "active_questions": [],
                "closed_results": [],
                "live_items": [],
            }
        teacher = self.live_session_teacher_state_payload(session_id)
        stage = str(teacher.get("stage") or "").strip().lower()
        listed = list(self.list_live_session_items(session_id))
        has_published_join = any(
            str(row.get("status") or "") == "active"
            and str(row.get("stage") or "").strip().lower() == "join"
            and str(row.get("item_id") or "").strip().lower().replace("-", "_")
            not in {"minds_on"}
            and str(row.get("kind") or "").strip().lower()
            not in {"media", "whiteboard", "slides"}
            for row in listed
        )
        items = []
        for row in listed:
            if row.get("status") not in {"active", "closed"}:
                continue
            item_id = str(row.get("item_id") or "").strip().lower().replace("-", "_")
            item_stage = str(row.get("stage") or "").strip().lower()
            if item_id in {"teams_spark"} or item_stage == "teams" and item_id.endswith("spark"):
                if stage != "teams":
                    continue
            if item_id in {"minds_on"} and (
                stage != "join" or has_published_join
            ):
                continue
            if item_id in {"meet_team", "meet_a", "meet_b", "meet_c"} and stage != "meet":
                continue
            items.append(row)
        public_items: list[dict[str, Any]] = []
        for item in items:
            prompt = self._prompt_for_live_item(item)
            raw_payload = (
                prompt.get("payload")
                if prompt is not None
                and isinstance(prompt.get("payload"), dict)
                else item.get("item")
                if isinstance(item.get("item"), dict)
                else {}
            )
            prior = None
            my_response = None
            group_state = None
            if item["response_mode"] == "group_consensus":
                group_state = self.student_group_consensus_state(
                    item, student_id
                )
            elif prompt is not None:
                prior = self.get_live_prompt_response(
                    int(prompt["id"]),
                    student_id,
                    participant_uuid=participant_uuid,
                )
                if prior is not None:
                    my_response = {
                        "response": prior.get("response") or {},
                        "awarded_points": prior.get("awarded_points"),
                        "updated_at": prior.get("updated_at"),
                    }
                elif is_meet_team_payload(raw_payload):
                    meet_state = public_meet_chain(teacher.get("meet_chain"))
                    if meet_state is not None:
                        token = meet_participant_key(
                            participant_uuid=participant_uuid,
                            student_id=student_id,
                        )
                        step = str(raw_payload.get("step") or "A")
                        bag_key = {
                            "A": "a_picks",
                            "C": "c_reacts",
                            "B": "b_picks",
                        }.get(step, "a_picks")
                        choice = str(
                            (meet_state.get(bag_key) or {}).get(token) or ""
                        ).strip()
                        if choice:
                            my_response = {
                                "response": {"choice": choice},
                                "awarded_points": None,
                                "updated_at": None,
                                "ephemeral": True,
                            }
            status = str(item["status"])
            include_results = status == "closed" or (
                bool(item["show_live_results"])
                and (
                    my_response is not None
                    or bool(group_state and group_state.get("has_voted"))
                )
            )
            results = None
            if (
                include_results
                and item["response_mode"] == "individual"
                and prompt is not None
            ):
                results = self._tally_for_prompt(session_id, prompt, teacher)
            elif include_results and group_state is not None:
                team_summaries = self.teacher_group_consensus_summary(
                    session_id, int(item["id"])
                )["teams"]
                own_team_id = group_state.get("team_id")
                class_counts: dict[str, dict[str, Any]] = {}
                team_answers: list[dict[str, Any]] = []
                for team in team_summaries:
                    is_own = (
                        own_team_id not in (None, "")
                        and int(team["team_id"]) == int(own_team_id)
                    )
                    if status != "closed" and not is_own:
                        continue
                    final_answer = team.get("final_answer")
                    team_answers.append(
                        {
                            "team_id": int(team["team_id"]),
                            "team_name": str(team.get("team_name") or ""),
                            "status": str(team.get("status") or ""),
                            "final_answer": final_answer,
                        }
                    )
                    if status != "closed" or not isinstance(final_answer, dict):
                        continue
                    key = json.dumps(
                        final_answer, sort_keys=True, separators=(",", ":")
                    )
                    if key not in class_counts:
                        class_counts[key] = {
                            "answer": final_answer,
                            "count": 0,
                        }
                    class_counts[key]["count"] += 1
                results = {
                    "team_id": group_state.get("team_id"),
                    "vote_summary": group_state.get("vote_summary") or [],
                    "final_answer": group_state.get("final_answer"),
                    "member_answers": group_state.get("member_answers") or [],
                    "team_answers": team_answers,
                    "class_distribution": (
                        sorted(
                            class_counts.values(),
                            key=lambda row: (
                                -int(row["count"]),
                                json.dumps(row["answer"], sort_keys=True),
                            ),
                        )
                        if status == "closed"
                        else []
                    ),
                    "phase": "final" if status == "closed" else "live",
                }
            public_items.append(
                {
                    "id": int(item["id"]),
                    "placement_key": item["placement_key"],
                    "item_id": item["item_id"],
                    "stage": item["stage"],
                    "page_number": item["page_number"],
                    "order": item["sort_order"],
                    "status": status,
                    "publish_mode": item["publish_mode"],
                    "response_mode": item["response_mode"],
                    "show_live_results": bool(item["show_live_results"]),
                    "published_at": item.get("published_at"),
                    "closed_at": item.get("closed_at"),
                    "content": strip_teacher_prompt_fields(raw_payload),
                    "prompt": (
                        {
                            "id": int(prompt["id"]),
                            "slide_index": int(prompt["slide_index"]),
                            "kind": str(prompt["kind"]),
                            "payload": strip_teacher_prompt_fields(raw_payload),
                        }
                        if prompt is not None
                        else None
                    ),
                    "my_response": my_response,
                    "group_consensus": group_state,
                    "can_submit": prompt is not None
                    and status == "active"
                    and item["response_mode"] == "individual",
                    "results": results,
                    "results_phase": (
                        "final" if status == "closed" else "live"
                    )
                    if results is not None
                    else None,
                }
            )
        return {
            "active_questions": [
                row
                for row in public_items
                if row["status"] == "active" and row.get("prompt") is not None
            ],
            "closed_results": [
                row for row in public_items if row["status"] == "closed"
            ],
            "live_items": public_items,
        }

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
        if is_cons_payload(payload):
            slot = self.session_live_slot(session_id)
            flags = (
                self.live_session_teacher_state_payload(session_id).get(
                    "round_flags"
                )
                or {}
            )
            allow_round_set = bool(flags.get("consolidation"))
            if slot == "C1":
                media = self.live_session_active_media_payload(session_id)
                if not allow_round_set:
                    if not media or not media.get("frozen"):
                        raise ValueError(
                            "Consolidation is available only after freeze."
                        )
                    if not is_c1_real_slice(media):
                        raise ValueError(
                            "C1 consolidation is only for the Real-slice channel."
                        )
            else:
                ride = self.session_text_ride(session_id)
                if not allow_round_set and not ride.get("frozen"):
                    raise ValueError("Consolidation is available only after freeze.")
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

    def _class_overlay_is_meet_teams(self, class_id: int) -> bool:
        """True when the live overlay is in the Meet the Teams phase.

        Args:
            class_id: Game-show ``classes.id``.
        """
        with self.game._lock:
            row = self.game.conn.execute(
                """
                SELECT overlay_phase FROM games
                WHERE class_id = ? AND status != 'ended'
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(class_id),),
            ).fetchone()
        return row is not None and str(row["overlay_phase"] or "") == "meet_teams"

    def _session_challenge_started(self, session_id: int) -> bool:
        """True when scoring is live or challenge media is mounted.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            return True
        if self._class_game_is_live(int(session_row["class_id"])):
            return True
        media = self.live_session_active_media_payload(session_id)
        return bool(media and media.get("url"))

    def _session_left_waiting_room(self, session_id: int) -> bool:
        """True when scoring, MEET/ROUND/PLAY, or Minds-On has ended.

        JOIN keeps waiting-room Minds-On even when the teacher preview
        has a local Real-slice blob. TEAMS keeps the teacher spark on
        the staff Question frame; students get the VLC welcome card
        instead of Minds-On. Meet / later stages leave the waiting room.
        Team Challenge /
        scoring still write a Minds-On sentinel so lazy-seed cannot
        bring it back.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            return True
        class_id = int(session_row["class_id"])
        if self._class_game_is_live(class_id):
            return True
        if self._class_overlay_is_meet_teams(class_id):
            return True
        stored = session_row.get("teacher_state")
        teacher = public_teacher_state(stored if isinstance(stored, dict) else None)
        if str(teacher.get("stage") or "") in {"meet", "round", "play"}:
            return True
        frames = teacher.get("student_frames") or {}
        if frames.get("media") or frames.get("canvas"):
            return True
        if self._meet_team_row_exists(session_id):
            return True
        if self._minds_on_row_exists(session_id):
            active = self.get_active_live_prompt(session_id)
            if not (active and is_minds_on_payload(active.get("payload"))):
                return True
        return False

    def session_live_slot(self, session_id: int) -> str:
        """Return the session live slot ``C1`` / ``C2`` / ``C3``.

        Defaults to C1 when the teacher channel has no slot yet.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        try:
            teacher = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            return "C1"
        return normalize_live_slot(teacher.get("live_slot"))

    def session_live_module(self, session_id: int) -> str:
        """Return the session catalogue module ``M1`` / ``M2`` / …

        Args:
            session_id: ``live_class_sessions.id``.
        """
        try:
            teacher = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            return "M1"
        return normalize_live_module(teacher.get("live_module"))

    def session_ontario_code(self, session_id: int) -> str:
        """Return the offering course code for one live session.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            return ""
        offering_id = session_row.get("offering_id")
        if offering_id in (None, ""):
            return ""
        offering = self.get_offering(int(offering_id))
        return str((offering or {}).get("ontario_code") or "").strip().upper()

    def ensure_live_class_media(self, session_id: int) -> dict[str, Any] | None:
        """Seed course-specific Join/Play or playlist media when authored.

        MCF3M M1C1 keeps the parabola Real-slice. MCR3U M1C1 mounts the
        nested square-root graph. C2/C3 playlists with a ``media.file``
        (for example MCR3U M1 C3 parent transformations) seed the same way.
        Wrong-course leftovers are replaced. A leftover stem or caption
        from another slot is replaced with this slot's overlay or seed
        title; remounts do not persist that copy onto the overlay.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        seed = live_class_seed_media(
            self.session_ontario_code(session_id),
            self.session_live_module(session_id),
            self.session_live_slot(session_id),
        )
        if seed is None:
            return self.live_session_active_media_payload(session_id)
        current = self.live_session_active_media_payload(session_id)
        current_url = str((current or {}).get("url") or "")
        session_row = self.get_live_session(session_id)
        overlay = (
            self.get_class_live_media_copy(
                int(session_row["class_id"]),
                self.session_live_module(session_id),
                self.session_live_slot(session_id),
            )
            if session_row is not None
            else {"stem": "", "caption": ""}
        )
        overlay_stem = str(overlay.get("stem") or "").strip()
        overlay_caption = str(overlay.get("caption") or "").strip()
        wanted_stem = overlay_stem or str(seed.get("stem") or seed.get("title") or "")
        wanted_caption = overlay_caption
        wanted_title = str(seed.get("title") or wanted_stem or "")
        if current_url == seed["url"]:
            current_stem = str((current or {}).get("stem") or "")
            current_caption = str((current or {}).get("caption") or "").strip()
            if current_stem != wanted_stem or current_caption != wanted_caption:
                return self.set_live_session_active_media(
                    session_id,
                    merge=True,
                    title=wanted_title,
                    stem=wanted_stem,
                    caption=wanted_caption,
                    persist_media_copy=False,
                )
            return current
        kwargs: dict[str, Any] = {
            "url": seed["url"],
            "title": wanted_title,
            "stem": wanted_stem,
            "caption": wanted_caption,
            "persist_media_copy": False,
        }
        if uses_c1_real_slice(
            self.session_ontario_code(session_id),
            self.session_live_module(session_id),
            self.session_live_slot(session_id),
        ):
            kwargs["challenge"] = "C1"
        return self.set_live_session_active_media(session_id, **kwargs)

    def session_text_ride(self, session_id: int) -> dict[str, Any]:
        """Return the C2/C3 text-only freeze + CONS ride.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        try:
            teacher = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            return default_text_ride()
        return public_text_ride(teacher.get("text_ride"))

    _ENGINE_RIDE_IDS = frozenset(
        {
            "minds_on",
            "minds-on",
            "teams-spark",
            "teams_spark",
            "meet-team",
            "meet_team",
            "team-challenge",
            "team_challenge",
        }
    )

    def _engine_ride_item_id(self, item_id: Any, payload: Any = None) -> bool:
        """True when this id/payload is an engine-seeded live-class ride.

        Args:
            item_id: Prompt or catalogue id such as ``minds_on``.
            payload: Optional live-prompt payload.
        """
        token = str(item_id or "").strip().lower().replace("-", "_")
        if token in self._ENGINE_RIDE_IDS:
            return True
        if not isinstance(payload, dict):
            return False
        return (
            is_minds_on_payload(payload)
            or is_teams_spark_payload(payload)
            or is_meet_team_payload(payload)
            or is_team_challenge_payload(payload)
        )

    def _session_playlist_item_removed(self, session_id: int, item_id: str) -> bool:
        """True when this class/slot overlay hides the given item id.

        Args:
            session_id: ``live_class_sessions.id``.
            item_id: Playlist or engine-ride id.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            return False
        try:
            teacher = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            return False
        token = str(item_id or "").strip()
        aliases = {token, token.replace("_", "-"), token.replace("-", "_")}
        for row in self.list_class_playlist_item_overrides(
            int(session_row["class_id"]),
            teacher.get("live_module") or "M1",
            teacher.get("live_slot") or "C1",
        ):
            if str(row.get("item_id") or "").strip() in aliases and int(
                row.get("removed") or 0
            ):
                return True
        return False

    def _current_deck_page(
        self, metadata: Any, teacher: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Resolve the teacher's current named page from merged metadata.

        Prefers ``page_id`` so overlay pages that reuse a pedagogical stage
        (``play``) stay distinct from authored pages.

        Args:
            metadata: Merged live-class metadata.
            teacher: Public teacher state.

        Returns:
            The matching page dict, or ``None`` when the deck has no pages.
        """
        pages = [
            row
            for row in (metadata.get("pages") if isinstance(metadata, dict) else [])
            or []
            if isinstance(row, dict) and row.get("stage")
        ]
        page_id = str(teacher.get("page_id") or "").strip()
        if page_id:
            for row in pages:
                if str(row.get("id") or "").strip() == page_id:
                    return row
        stage = str(teacher.get("stage") or "").strip().lower()
        for row in pages:
            if str(row.get("stage") or "").strip().lower() == stage:
                return row
        return pages[0] if pages else None

    def _page_number_for_deck_page(self, page: dict[str, Any] | None) -> int | None:
        """Return the stored question-binding page_number for one page."""
        if not isinstance(page, dict):
            return None
        try:
            raw = page.get("page_number")
            if raw not in (None, ""):
                return int(raw)
        except (TypeError, ValueError):
            return None
        return None

    def _page_number_for_stage(self, metadata: Any, stage: Any) -> int | None:
        """Return the 1-based lesson page that owns ``stage``, if any.

        Args:
            metadata: Normalized live-class metadata.
            stage: Stage token such as ``join``.
        """
        wanted = str(stage or "").strip().lower()
        pages = metadata.get("pages") if isinstance(metadata, dict) else None
        if not isinstance(pages, list):
            return 1 if wanted == "join" else None
        for index, page in enumerate(pages, start=1):
            if not isinstance(page, dict):
                continue
            if str(page.get("stage") or "").strip().lower() == wanted:
                raw = page.get("page_number")
                try:
                    return int(raw) if raw not in (None, "") else index
                except (TypeError, ValueError):
                    return index
        return 1 if wanted == "join" else None

    def _engine_ride_label(self, item_id: str, payload: Any = None) -> str:
        """Return a short teacher-card label for an engine-seeded ride.

        Args:
            item_id: Prompt id.
            payload: Optional live-prompt payload.
        """
        token = str(item_id or "").strip().lower().replace("-", "_")
        if token in {"minds_on"} or is_minds_on_payload(payload):
            return "Waiting room"
        if token in {"team_challenge"} or is_team_challenge_payload(payload):
            return "Team challenge"
        if token in {"teams_spark"} or is_teams_spark_payload(payload):
            return "Shared spark"
        if token in {"meet_team"} or is_meet_team_payload(payload):
            return "Meet"
        return "Live class"

    def _dismiss_engine_ride_prompt(self, session_id: int, item_id: str) -> None:
        """Deactivate one engine-seeded ride after the teacher removes it.

        Args:
            session_id: ``live_class_sessions.id``.
            item_id: Ride id such as ``minds_on``.
        """
        token = str(item_id or "").strip().lower().replace("-", "_")
        if token in {"minds_on"}:
            self.clear_waiting_room_minds_on(session_id)
            return
        active = self.get_active_live_prompt(session_id)
        payload = (active or {}).get("payload") or {}
        if self._engine_ride_item_id(item_id, payload):
            self.clear_active_live_prompt(session_id)

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
        if self._session_playlist_item_removed(session_id, "minds_on"):
            return None
        desired = minds_on_prompt_payload(
            self.session_live_slot(session_id),
            self.session_live_module(session_id),
        )
        active = self.get_active_live_prompt(session_id)
        existing = self._prompt_at_slide(session_id, int(MINDS_ON_SLIDE_INDEX))
        stale = existing if existing and is_minds_on_payload(existing.get("payload")) else (
            active if active and is_minds_on_payload(active.get("payload")) else None
        )
        if stale is not None:
            current = stale.get("payload") or {}
            if str(current.get("live_slot") or "").upper() != str(
                desired.get("live_slot") or ""
            ).upper():
                return self.set_live_session_prompt(
                    session_id,
                    slide_index=MINDS_ON_SLIDE_INDEX,
                    kind=MINDS_ON_KIND,
                    payload=desired,
                    activate=bool(stale.get("active")),
                )
        if self._published_stage_catalogue_prompt(session_id, "join") is not None:
            return None
        if self._meet_team_row_exists(session_id):
            return None
        if active and is_minds_on_payload(active.get("payload")):
            current = active.get("payload") or {}
            if (
                current.get("prompt") == desired["prompt"]
                and current.get("choices") == desired["choices"]
                and current.get("key") == desired.get("key")
                and current.get("items") == desired.get("items")
                and current.get("live_slot") == desired.get("live_slot")
                and current.get("live_module") == desired.get("live_module")
            ):
                return None
            return self.set_live_session_prompt(
                session_id,
                slide_index=MINDS_ON_SLIDE_INDEX,
                kind=MINDS_ON_KIND,
                payload=desired,
                activate=True,
            )
        # Do not steal activation from a different active prompt
        if active is not None:
            # Seed the row but keep it inactive
            if not self._minds_on_row_exists(session_id):
                return self.set_live_session_prompt(
                    session_id,
                    slide_index=MINDS_ON_SLIDE_INDEX,
                    kind=MINDS_ON_KIND,
                    payload=desired,
                    activate=False,
                )
            return None
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
            payload=minds_on_prompt_payload(
                self.session_live_slot(session_id),
                self.session_live_module(session_id),
            ),
            activate=False,
        )

    def activate_join_minds_on(self, session_id: int) -> dict[str, Any] | None:
        """Remount the Join C1 prompt when the teacher is on JOIN.

        Used when navigating back to Join so the Question tab and student
        face keep the Minds-On row instead of a leftover Welcome/Meet
        prompt.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        if self._session_playlist_item_removed(session_id, "minds_on"):
            return None
        if self._published_stage_catalogue_prompt(session_id, "join") is not None:
            return None
        desired = minds_on_prompt_payload(
            self.session_live_slot(session_id),
            self.session_live_module(session_id),
        )
        existing = self._prompt_at_slide(session_id, int(MINDS_ON_SLIDE_INDEX))
        return self.set_live_session_prompt(
            session_id,
            slide_index=MINDS_ON_SLIDE_INDEX,
            kind=MINDS_ON_KIND,
            payload=desired,
            activate=True,
        )

    def activate_welcome_c2(
        self, session_id: int, *, activate: bool = False
    ) -> dict[str, Any] | None:
        """Seed the integer poll after a student submits the Join minds-on.

        Runs for any slot/module/course, not only C1. Leaves the waiting-room
        MC as the class-active prompt unless ``activate`` is true so
        classmates still on minds-on can answer. Welcome (TEAMS) activates
        C2 for everyone via ``ensure_teams_spark``.

        Args:
            session_id: ``live_class_sessions.id``.
            activate: When True, make C2 the session-active prompt.
        """
        return self._write_welcome_c2(session_id, activate=activate)

    def _welcome_c2_is_current(self, payload: Any) -> bool:
        """True when stored C2 matches the locked integer-poll copy.

        Args:
            payload: Stored live-prompt payload.
        """
        desired = teams_spark_prompt_payload()
        current = payload if isinstance(payload, dict) else {}
        return (
            current.get("prompt") == desired["prompt"]
            and current.get("kind") == desired.get("kind")
            and bool(current.get("integer_only")) == bool(desired.get("integer_only"))
        )

    def _write_welcome_c2(
        self, session_id: int, *, activate: bool
    ) -> dict[str, Any] | None:
        """Insert or refresh the Join/Welcome C2 integer poll.

        Args:
            session_id: ``live_class_sessions.id``.
            activate: When True, make C2 the session-active prompt.
        """
        desired = teams_spark_prompt_payload()
        existing = self._prompt_at_slide(session_id, int(TEAMS_SPARK_SLIDE_INDEX))
        if existing and self._welcome_c2_is_current(existing.get("payload")):
            if bool(existing.get("active")) == bool(activate):
                return existing
        return self.set_live_session_prompt(
            session_id,
            slide_index=TEAMS_SPARK_SLIDE_INDEX,
            kind=TEAMS_SPARK_KIND,
            payload=desired,
            activate=activate,
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

    def ensure_teams_spark(self, session_id: int) -> dict[str, Any] | None:
        """Seed the TEAMS shared spark when the teacher is on TEAMS.

        Lazy-seeds sessions that entered TEAMS before this prompt existed.
        Refreshes the active payload when the locked copy changes. Does
        not recreate the row after TEAMS→MEET cleared it.

        Args:
            session_id: ``live_class_sessions.id``.

        Returns:
            The seeded prompt row, or ``None`` when skipped.
        """
        try:
            teacher = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            return None
        if str(teacher.get("stage") or "") != "teams":
            return None
        active = self.get_active_live_prompt(session_id)
        if active and self._welcome_c2_is_current(active.get("payload")):
            if self._lifecycle_item_is_published(session_id, "teams_spark"):
                return None
        return self._write_welcome_c2(
            session_id,
            activate=self._lifecycle_item_is_published(session_id, "teams_spark"),
        )

    def clear_teams_spark(self, session_id: int) -> None:
        """Deactivate the TEAMS shared spark when leaving TEAMS.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        active = self.get_active_live_prompt(session_id)
        if active and is_teams_spark_payload(active.get("payload")):
            self.clear_active_live_prompt(session_id)

    def staff_teams_spark_payload(self, session_id: int) -> dict[str, Any] | None:
        """Teacher-only integer-poll card for Join and TEAMS Question frames.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        try:
            teacher = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            return None
        if str(teacher.get("stage") or "") not in {"join", "teams"}:
            return None
        ui = teacher.get("mc_ui") if isinstance(teacher.get("mc_ui"), dict) else {}
        revealed = bool(ui.get("reveal") and ui.get("reveal_to_students"))
        return staff_teams_spark_card(teams_spark_prompt_payload(), reveal=revealed)

    def _prompt_at_slide(
        self, session_id: int, slide_index: int
    ) -> dict[str, Any] | None:
        """Return the prompt row for one slide index, active or not.

        Args:
            session_id: ``live_class_sessions.id``.
            slide_index: Prompt slide index.
        """
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM live_session_prompts
                WHERE live_session_id = ? AND slide_index = ?
                LIMIT 1
                """,
                (int(session_id), int(slide_index)),
            ).fetchone()
        return self._prompt_row_to_dict(row) if row else None

    def _meet_team_row_exists(self, session_id: int) -> bool:
        """True when this session already has a Meet chain prompt row.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        return any(
            self._prompt_at_slide(session_id, slide) is not None
            for slide in MEET_STEP_SLIDE.values()
        )

    def _deactivate_meet_chain_prompts(self, session_id: int) -> None:
        """Deactivate every Meet A/C/B slide for one session.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        now = _now()
        with self._lock:
            self.conn.execute(
                f"""
                UPDATE live_session_prompts
                SET active = 0, updated_at = ?
                WHERE live_session_id = ?
                  AND slide_index IN ({",".join("?" * len(MEET_STEP_SLIDE))})
                """,
                (now, int(session_id), *MEET_STEP_SLIDE.values()),
            )
            self.conn.commit()

    def _ensure_student_meet_prompt(
        self,
        session_id: int,
        chain_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Activate the visible Meet step on the student prompt channel.

        Remounts only when the active row is missing, not a Meet ride, or
        the step no longer matches ``meet_chain``. Teacher preview media
        must not hide this prompt.

        Args:
            session_id: ``live_class_sessions.id``.
            chain_state: Public MeetChainState.

        Returns:
            The active Meet prompt row, or None when the session is missing.
        """
        step = current_meet_step(chain_state)
        active = self.get_active_live_prompt(session_id)
        payload = (active or {}).get("payload") or {}
        if (
            active
            and is_meet_team_payload(payload)
            and str(payload.get("step") or "") == str(step or "")
        ):
            return active
        return self.seed_meet_team_warmup(session_id, chain_state=chain_state)

    def activate_meet_team_question(self, session_id: int) -> dict[str, Any] | None:
        """Publish the Meet teammate poll as an individual live question.

        Reuses the seeded Meet prompt when present so staff tally and
        student cards share one row. Group and chain modes are never applied.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        meet_prompt = None
        for prompt in self._list_live_session_prompts(session_id):
            if not is_meet_team_payload(prompt.get("payload")):
                continue
            meet_prompt = prompt
            if prompt.get("active"):
                break
        published = None
        for item in self.ensure_live_session_items(session_id):
            item_id = str(item.get("item_id") or "").strip().lower().replace("_", "-")
            stage = str(item.get("stage") or "").strip().lower()
            status = str(item.get("status") or "").strip().lower()
            if stage != "meet" or item_id not in {"meet-team", "meet-a"}:
                continue
            if status == "closed":
                continue
            if meet_prompt is not None:
                now = _now()
                with self._lock:
                    self.conn.execute(
                        """
                        UPDATE live_session_items
                        SET prompt_id = ?, status = 'active',
                            publish_mode = 'individual',
                            response_mode = 'individual',
                            published_at = COALESCE(published_at, ?),
                            closed_at = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (int(meet_prompt["id"]), now, now, int(item["id"])),
                    )
                    self.conn.commit()
                published = self.get_live_session_item(session_id, int(item["id"]))
                continue
            if status != "active":
                try:
                    published = self.publish_live_session_item(
                        session_id,
                        int(item["id"]),
                        publish_mode="individual",
                    )
                except ValueError:
                    published = item
            else:
                published = item
        return published

    def seed_meet_team_warmup(
        self,
        session_id: int,
        *,
        chain_state: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Activate the visible Meet chain step on the live-prompt channel.

        Args:
            session_id: ``live_class_sessions.id``.
            chain_state: Optional MeetChainState; when omitted, mounts A
                of a fresh A → C → B chain.

        Returns:
            The upserted prompt row, or ``None`` when the session is missing.
        """
        if self.get_live_session(session_id) is None:
            return None
        state = public_meet_chain(chain_state) or new_meet_chain_state()
        payload = meet_payload_for_state(state) or meet_team_prompt_payload()
        step = str(payload.get("step") or "A")
        slide = int(MEET_STEP_SLIDE.get(step) or MEET_TEAM_SLIDE_INDEX)
        self._deactivate_meet_chain_prompts(session_id)
        return self.set_live_session_prompt(
            session_id,
            slide_index=slide,
            kind=MEET_TEAM_KIND,
            payload=payload,
            activate=True,
        )

    def activate_meet_team_warmup_for_class(
        self, class_id: int
    ) -> dict[str, Any] | None:
        """Enter MEET: clear Minds-On, mount A, fire ``cue.meet_open``.

        Staff Start Meet / Meet Teams path. No-op when the class has
        no active live session, and no-op when MEET is already mounted
        so a timer start does not bump ``state_seq`` or remount chrome.

        Args:
            class_id: Game-show ``classes.id``.

        Returns:
            The updated teacher state, or ``None`` when skipped.
        """
        live = self.get_active_live_session_for_class(int(class_id))
        if live is None:
            return None
        session_id = int(live["id"])
        try:
            current = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            current = None
        if (
            current
            and str(current.get("stage") or "") == "meet"
            and public_meet_chain(current.get("meet_chain"))
        ):
            return current
        return self.set_live_session_teacher_state(session_id, stage="meet")

    def _inactivate_join_minds_on_items(self, session_id: int) -> None:
        """Mark leftover waiting-room minds-on rows inactive after a join MC.

        JOIN catalogue questions share the join stage with the waiting-room
        minds-on card. Once staff publish ``function-notation`` (or another
        join-page item), an active minds-on card intercepts student Submit.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        now = _now()
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT id, item_id, status FROM live_session_items
                WHERE live_session_id = ?
                """,
                (int(session_id),),
            ).fetchall()
            for row in rows:
                item_id = str(row["item_id"] or "").strip().lower().replace("-", "_")
                if item_id not in {"minds_on"}:
                    continue
                if str(row["status"] or "") != "active":
                    continue
                self.conn.execute(
                    """
                    UPDATE live_session_items
                    SET status = 'inactive', closed_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, int(row["id"])),
                )
            self.conn.commit()

    def _inactivate_meet_team_items(self, session_id: int) -> None:
        """Mark Meet lifecycle rows inactive so later pages can accept answers.

        Leaving MEET deactivates the prompt but used to leave the published
        Meet card ``active``. That stale card then appeared on Round pages
        and intercepted student submits.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        now = _now()
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT id, item_id, status FROM live_session_items
                WHERE live_session_id = ?
                """,
                (int(session_id),),
            ).fetchall()
            for row in rows:
                item_id = str(row["item_id"] or "").strip().lower().replace("_", "-")
                if item_id not in {"meet-team", "meet-a", "meet-b", "meet-c"}:
                    continue
                if str(row["status"] or "") != "active":
                    continue
                self.conn.execute(
                    """
                    UPDATE live_session_items
                    SET status = 'inactive', closed_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, int(row["id"])),
                )
            self.conn.commit()

    def clear_meet_team_warmup(self, session_id: int) -> None:
        """Wipe the Meet chain when MEET ends or Team Challenge starts.

        Writes an inactive A sentinel when the chain was never seeded so a
        later poll cannot treat the session as still in Meet.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        active = self.get_active_live_prompt(session_id)
        if active and is_meet_team_payload(active.get("payload")):
            self.clear_active_live_prompt(session_id)
        self._deactivate_meet_chain_prompts(session_id)
        self._inactivate_meet_team_items(session_id)
        if self._meet_team_row_exists(session_id):
            return
        if self.get_live_session(session_id) is None:
            return
        self.set_live_session_prompt(
            session_id,
            slide_index=MEET_TEAM_SLIDE_INDEX,
            kind=MEET_TEAM_KIND,
            payload=meet_team_prompt_payload(),
            activate=False,
        )

    def clear_meet_team_warmup_for_class(self, class_id: int) -> None:
        """Deactivate Meet Your Team on the class's active live session.

        Args:
            class_id: Game-show ``classes.id``.
        """
        live = self.get_active_live_session_for_class(int(class_id))
        if live is None:
            return
        self.clear_meet_team_warmup(int(live["id"]))

    def clear_session_warmups(self, session_id: int) -> None:
        """Drop Minds-On and the Meet chain when Team Challenge starts.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        self.clear_waiting_room_minds_on(session_id)
        try:
            current = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            self.clear_meet_team_warmup(session_id)
            return
        if current.get("meet_chain") or current.get("stage") == "meet":
            if current.get("stage") == "meet":
                current["stage"] = "round"
            self._wipe_meet_chain(session_id, current, fire_clear=True)
            self._write_teacher_state(session_id, current)
            return
        self.clear_meet_team_warmup(session_id)

    def clear_session_warmups_for_class(self, class_id: int) -> None:
        """Drop ephemeral warm-ups on the class's active live session.

        Args:
            class_id: Game-show ``classes.id``.
        """
        live = self.get_active_live_session_for_class(int(class_id))
        if live is None:
            return
        self.clear_session_warmups(int(live["id"]))

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
                "SELECT * FROM live_session_prompts WHERE id = ?",
                (int(prompt_id),),
            ).fetchone()
        if prompt is None:
            raise KeyError(f"prompt {prompt_id}")
        prompt_row = self._prompt_row_to_dict(prompt)
        with self._lock:
            lifecycle = self.conn.execute(
                """
                SELECT status, response_mode
                FROM live_session_items
                WHERE prompt_id = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (int(prompt_id),),
            ).fetchone()
        if lifecycle is not None:
            status = str(lifecycle["status"] or "")
            if status == "closed":
                raise ValueError("This item is not accepting responses.")
            if str(lifecycle["response_mode"] or "") == "group_consensus":
                raise ValueError("Use the private group-vote endpoint.")
        payload = prompt_row.get("payload") or {}
        if (
            str(prompt_row.get("kind") or "").strip().lower() == "numeric"
            or is_teams_spark_payload(payload)
            or payload.get("integer_only")
        ):
            raw_value = (response or {}).get("value")
            if raw_value is None:
                raw_value = (response or {}).get("choice")
            try:
                number = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError("Enter a number.") from exc
            integer_only = bool(
                payload.get("integer_only") or is_teams_spark_payload(payload)
            )
            if integer_only and not number.is_integer():
                raise ValueError("Enter an integer.")
            response = dict(response or {})
            response["value"] = int(number) if integer_only else number
        session_id = int(prompt_row["live_session_id"])
        questions_mode = self._questions_view_mode(session_id)
        response = dict(response or {})
        response["question_view"] = questions_mode
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
        if sid is not None:
            if is_minds_on_payload(payload):
                self.activate_welcome_c2(session_id)
            self._copy_group_question_response(
                session_id,
                prompt_id=int(prompt_id),
                student_id=sid,
                response=response or {},
            )
        return result or {}

    def list_live_prompt_responses(self, prompt_id: int) -> list[dict[str, Any]]:
        """Return every response row for one live prompt.

        Args:
            prompt_id: ``live_session_prompts.id``.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM live_session_responses
                WHERE prompt_id = ?
                ORDER BY id ASC
                """,
                (int(prompt_id),),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            raw = payload.get("response_json") or "{}"
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except json.JSONDecodeError:
                parsed = {}
            payload["response"] = parsed if isinstance(parsed, dict) else {}
            out.append(payload)
        return out

    def _list_live_session_prompts(self, session_id: int) -> list[dict[str, Any]]:
        """Return every prompt row for one live session in slide order."""

        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM live_session_prompts
                WHERE live_session_id = ?
                ORDER BY slide_index ASC, id ASC
                """,
                (int(session_id),),
            ).fetchall()
        return [self._prompt_row_to_dict(row) for row in rows]

    @staticmethod
    def _live_question_type(kind: Any, payload: Any) -> str:
        """Classify a runtime prompt as poll, keyed MC, or numeric."""

        body = payload if isinstance(payload, dict) else {}
        token = str(kind or body.get("kind") or "").strip().lower()
        if token == "numeric" or body.get("integer_only"):
            return "numeric"
        key = str(
            body.get("key")
            or body.get("correct_answer")
            or ((body.get("correct_ids") or [""])[0])
        ).strip()
        return "mc" if key else "poll"

    def _merged_live_class_metadata(
        self,
        class_id: int,
        course: str,
        module: str,
        slot: str,
    ) -> dict[str, Any]:
        """Load seed metadata merged with per-class bank, page, and item overlays."""
        key = (
            int(class_id),
            str(course or "").upper(),
            str(module or "M1").upper(),
            str(slot or "C1").upper(),
        )
        cached = self._live_metadata_cache.get(key)
        if cached is not None:
            return deepcopy(cached)
        loaded = load_live_class_metadata(key[1], key[2], key[3])
        placements = self.list_class_playlist_placements(
            int(class_id), key[2], key[3]
        )
        overrides = self.list_class_playlist_item_overrides(
            int(class_id), key[2], key[3]
        )
        page_overlays = self.list_class_playlist_pages(
            int(class_id), key[2], key[3]
        )
        merged = self._merge_playlist_placements_into_metadata(
            loaded, placements
        )
        merged = self._apply_class_playlist_item_overrides(merged, overrides)
        merged = self._apply_class_playlist_page_overlays(merged, page_overlays)
        merged = self._apply_class_live_media_overlay(
            merged,
            self.get_class_live_media_copy(int(class_id), key[2], key[3]),
        )
        self._live_metadata_cache[key] = merged
        return deepcopy(merged)

    def live_class_metadata_for_class_lesson(
        self, class_id: int, module: str, slot: str
    ) -> dict[str, Any]:
        """Return merged live-lesson metadata for one class deck."""
        class_row = self.game.get_class(int(class_id))
        if class_row is None:
            raise KeyError(f"class {class_id}")
        course = str(class_row.get("course_code") or "").upper()
        return self._merged_live_class_metadata(
            int(class_id), course, module, slot
        )

    def live_class_metadata_for_session(self, session_id: int) -> dict[str, Any]:
        """Return file-backed metadata selected by this live session.

        Playlist JSON is cached in-process by class/course/module/slot so
        polls do not re-parse the same file on every request. Per-class
        bank imports are merged after the seed load.
        """

        session_row = self.get_live_session(session_id)
        if session_row is None:
            raise KeyError(f"live session {session_id}")
        class_id = int(session_row["class_id"])
        class_row = self.game.get_class(class_id)
        teacher = self.live_session_teacher_state_payload(session_id)
        return self._merged_live_class_metadata(
            class_id,
            str(class_row.get("course_code") or "").upper(),
            str(teacher.get("live_module") or "M1").upper(),
            str(teacher.get("live_slot") or "C1").upper(),
        )

    def schema_v2_owns_live_stage_questions(
        self, session_id: int, stage: str | None = None
    ) -> bool:
        """Return whether schema-v2 lifecycle metadata owns stage questions.

        Schema-v1 and ad-hoc sessions continue to use the singleton prompt
        compatibility path. A schema-v2 stage with no question placements also
        falls back so partially authored playlists remain usable.

        Args:
            session_id: ``live_class_sessions.id``.
            stage: Optional stage override; defaults to the teacher's stage.
        """

        metadata = self.live_class_metadata_for_session(session_id)
        if metadata.get("schema_version") != SCHEMA_V2:
            return False
        current_stage = str(stage or "").strip().lower()
        if not current_stage:
            teacher = self.live_session_teacher_state_payload(session_id)
            current_stage = str(teacher.get("stage") or "").strip().lower()
        return bool(questions_for_stage(metadata, current_stage))

    def student_live_class_metadata_for_session(
        self, session_id: int
    ) -> dict[str, Any]:
        """Return live metadata with teacher-only answer keys removed."""

        metadata = self.live_class_metadata_for_session(session_id)
        public = dict(metadata)
        for key in ("questions", "items"):
            cleaned_rows = []
            for row in metadata.get(key) or []:
                if not isinstance(row, dict):
                    continue
                cleaned = strip_teacher_prompt_fields(row)
                cleaned.pop("correct_answer", None)
                for nested in cleaned.get("items") or []:
                    if isinstance(nested, dict):
                        nested.pop("correct_answer", None)
                cleaned_rows.append(cleaned)
            public[key] = cleaned_rows
        return public

    @staticmethod
    def _lifecycle_row_for_metadata_question(
        candidates: list[dict[str, Any]],
        question: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Pick the session row whose stage/page matches metadata dest first.

        Args:
            candidates: Lifecycle rows sharing the question id.
            question: Merged metadata question (post-move dest).

        Returns:
            Best matching row, or the first candidate.
        """
        if not candidates:
            return None
        dest_stage = str(question.get("stage") or "").strip().lower()
        try:
            dest_page = (
                int(question["page_number"])
                if question.get("page_number") not in (None, "")
                else None
            )
        except (TypeError, ValueError):
            dest_page = None
        for row in candidates:
            row_stage = str(row.get("stage") or "").strip().lower()
            try:
                row_page = (
                    int(row["page_number"])
                    if row.get("page_number") not in (None, "")
                    else None
                )
            except (TypeError, ValueError):
                row_page = None
            if dest_stage and row_stage and dest_stage != row_stage:
                continue
            if dest_page is not None and row_page is not None and dest_page != row_page:
                continue
            return row
        return candidates[0]

    def live_session_question_cards(self, session_id: int) -> list[dict[str, Any]]:
        """Return ordered teacher cards for questions associated with the stage."""

        teacher = self.live_session_teacher_state_payload(session_id)
        stage = str(teacher.get("stage") or "join")
        metadata = self.live_class_metadata_for_session(session_id)
        current_page = self._current_deck_page(metadata, teacher)
        wanted_page = self._page_number_for_deck_page(current_page)
        lifecycle_rows = self.list_live_session_items(session_id)
        lifecycle_by_item: dict[str, list[dict[str, Any]]] = {}
        for lifecycle in lifecycle_rows:
            lifecycle_by_item.setdefault(
                str(lifecycle.get("item_id") or ""), []
            ).append(lifecycle)
        prompt_rows = self._list_live_session_prompts(session_id)
        by_item: dict[str, dict[str, Any]] = {}
        for prompt in prompt_rows:
            payload = prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
            item_id = str(
                payload.get("item_id")
                or payload.get("pack")
                or f"prompt-{prompt.get('id')}"
            ).strip()
            if item_id:
                by_item[item_id] = prompt
        question_views = (
            teacher.get("question_views")
            if isinstance(teacher.get("question_views"), dict)
            else {}
        )
        cards: list[dict[str, Any]] = []
        seen: set[str] = set()
        metadata_question_ids = {
            str(row.get("id") or "").strip()
            for row in metadata.get("questions") or []
            if isinstance(row, dict) and str(row.get("id") or "").strip()
        }
        for question in questions_for_stage(metadata, stage):
            question_id = str(question.get("id") or "").strip()
            if not question_id or self._session_playlist_item_removed(
                session_id, question_id
            ):
                continue
            try:
                question_page = (
                    int(question["page_number"])
                    if question.get("page_number") not in (None, "")
                    else None
                )
            except (TypeError, ValueError):
                question_page = None
            if (
                wanted_page is not None
                and question_page is not None
                and question_page != wanted_page
            ):
                continue
            lifecycle = self._lifecycle_row_for_metadata_question(
                lifecycle_by_item.get(question_id) or [],
                question,
            )
            prompt = by_item.get(question_id)
            if prompt is None and lifecycle is not None:
                prompt = self._prompt_for_live_item(lifecycle)
            payload = prompt.get("payload") if isinstance(prompt, dict) else {}
            payload = payload if isinstance(payload, dict) else {}
            payload_slot = str(payload.get("live_slot") or "").upper()
            current_slot = str(self.session_live_slot(session_id) or "").upper()
            if (
                payload_slot
                and current_slot
                and payload_slot != current_slot
                and self._engine_ride_item_id(question_id, {**question, **payload})
            ):
                payload = {}
            options = payload.get("choices") or question.get("options") or []
            key = str(
                payload.get("key")
                or payload.get("correct_answer")
                or question.get("correct_answer")
                or ""
            ).strip()
            kind = self._live_question_type(
                (prompt or {}).get("kind") or question.get("type"),
                {**question, **payload, "key": key},
            )
            visibility = question_views.get(question_id)
            if visibility not in {"none", "student"}:
                visibility = (
                    "student" if question.get("default_visibility") else "none"
                )
            prompt_id = (prompt or {}).get("id")
            responses = (
                self.list_live_prompt_responses(int(prompt_id))
                if prompt_id not in (None, "")
                else []
            )
            cards.append(
                {
                    **question,
                    "id": question_id,
                    "type": kind,
                    "text": str(
                        payload.get("prompt")
                        or payload.get("question")
                        or question.get("text")
                        or ""
                    ).strip(),
                    "options": [str(item) for item in options],
                    "correct_answer": key or None,
                    "prompt_id": int(prompt_id)
                    if prompt_id not in (None, "")
                    else None,
                    "live_item_id": int(lifecycle["id"])
                    if lifecycle is not None
                    else None,
                    "placement_key": lifecycle.get("placement_key")
                    if lifecycle is not None
                    else None,
                    "status": lifecycle.get("status")
                    if lifecycle is not None
                    else ("active" if (prompt or {}).get("active") else "inactive"),
                    "publish_mode": lifecycle.get("publish_mode")
                    if lifecycle is not None
                    else "individual",
                    "response_mode": lifecycle.get("response_mode")
                    if lifecycle is not None
                    else "individual",
                    "show_live_results": bool(
                        lifecycle.get("show_live_results")
                    )
                    if lifecycle is not None
                    else True,
                    "student_view": visibility,
                    "active": (
                        lifecycle.get("status") == "active"
                        if lifecycle is not None
                        else bool((prompt or {}).get("active"))
                    ),
                    "response_count": len(responses),
                    "engine_ride": self._engine_ride_item_id(
                        question_id, {**question, **payload}
                    ),
                    "ride_label": self._engine_ride_label(
                        question_id, {**question, **payload}
                    )
                    if self._engine_ride_item_id(
                        question_id, {**question, **payload}
                    )
                    else "",
                }
            )
            seen.add(question_id)
        # Build a map from prompt_id to lifecycle stage for filtering
        prompt_to_lifecycle_stage: dict[int, str] = {}
        for lifecycle in lifecycle_rows:
            prompt_id = lifecycle.get("prompt_id")
            if prompt_id not in (None, ""):
                prompt_to_lifecycle_stage[int(prompt_id)] = str(
                    lifecycle.get("stage") or ""
                )
        for prompt in prompt_rows:
            payload = prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
            payload = payload if isinstance(payload, dict) else {}
            # Check lifecycle stage first - if prompt has a lifecycle item, use its stage
            lifecycle_stage = prompt_to_lifecycle_stage.get(int(prompt["id"]))
            if lifecycle_stage:
                if lifecycle_stage != stage:
                    continue
            else:
                # Fallback: infer stage from payload type
                prompt_stage = "meet" if is_meet_team_payload(payload) else (
                    "join" if is_minds_on_payload(payload) else (
                    "teams" if is_teams_spark_payload(payload) else None))
                if stage == "join" and not is_minds_on_payload(payload):
                    continue
                if stage == "teams" and not is_teams_spark_payload(payload):
                    continue
                if stage == "meet" and prompt_stage != "meet":
                    continue
            if stage in {"round", "play"} and not prompt.get("active"):
                continue
            question_id = str(
                payload.get("item_id")
                or payload.get("pack")
                or f"prompt-{prompt.get('id')}"
            ).strip()
            if not question_id or question_id in seen:
                continue
            if self._session_playlist_item_removed(session_id, question_id):
                continue
            if (
                metadata_question_ids
                and question_id not in metadata_question_ids
                and not self._engine_ride_item_id(question_id, payload)
            ):
                continue
            choices = payload.get("choices") or []
            key = str(
                payload.get("key")
                or payload.get("correct_answer")
                or ((payload.get("correct_ids") or [""])[0])
            ).strip()
            mode = question_views.get(question_id)
            if mode not in {"none", "student"}:
                mode = (
                    "student"
                    if prompt.get("active")
                    and (teacher.get("student_view") or {}).get("questions")
                    == "student"
                    else "none"
                )
            responses = self.list_live_prompt_responses(int(prompt["id"]))
            page_number = payload.get("page_number")
            if page_number in (None, ""):
                page_number = self._page_number_for_stage(metadata, stage)
            try:
                prompt_page = (
                    int(page_number) if page_number not in (None, "") else None
                )
            except (TypeError, ValueError):
                prompt_page = None
            engine_ride = self._engine_ride_item_id(question_id, payload)
            if (
                wanted_page is not None
                and prompt_page is not None
                and prompt_page != wanted_page
                and not engine_ride
            ):
                continue
            cards.append(
                {
                    "id": question_id,
                    "stage": stage,
                    "type": self._live_question_type(
                        prompt.get("kind"), {**payload, "key": key}
                    ),
                    "text": str(
                        payload.get("prompt") or payload.get("question") or ""
                    ).strip(),
                    "options": [str(item) for item in choices],
                    "correct_answer": key or None,
                    "page_number": page_number,
                    "engine_ride": engine_ride,
                    "ride_label": self._engine_ride_label(question_id, payload)
                    if engine_ride
                    else "",
                    "order": len(cards) + 1,
                    "default_visibility": False,
                    "prompt_id": int(prompt["id"]),
                    "live_item_id": None,
                    "placement_key": None,
                    "status": "active" if prompt.get("active") else "inactive",
                    "publish_mode": "individual",
                    "response_mode": "individual",
                    "show_live_results": True,
                    "student_view": mode,
                    "active": bool(prompt.get("active")),
                    "response_count": len(responses),
                }
            )
            seen.add(question_id)
        cards.sort(key=lambda row: (int(row.get("order") or 0), str(row["id"])))
        return cards

    def set_live_question_visibility(
        self,
        session_id: int,
        question_id: str,
        mode: str,
    ) -> dict[str, Any]:
        """Set one question teacher-only or immediately activate it for students."""

        wanted = "student" if str(mode or "").strip() == "student" else "none"
        key = str(question_id or "").strip()
        if not key:
            raise ValueError("question_id is required")
        cards = self.live_session_question_cards(session_id)
        card = next((row for row in cards if row.get("id") == key), None)
        if card is None:
            raise KeyError(f"live question {key}")
        prompt_id = card.get("prompt_id")
        if wanted == "student":
            if prompt_id not in (None, ""):
                prompt = next(
                    row
                    for row in self._list_live_session_prompts(session_id)
                    if int(row["id"]) == int(prompt_id)
                )
                self.set_live_session_prompt(
                    session_id,
                    slide_index=int(prompt["slide_index"]),
                    kind=str(prompt.get("kind") or "mc"),
                    payload=prompt.get("payload") or {},
                    activate=True,
                )
            else:
                payload = {
                    "item_id": key,
                    "pack": "live-metadata",
                    "prompt": card.get("text"),
                    "kind": "numeric"
                    if card.get("type") == "numeric"
                    else "mc",
                    "choices": card.get("options") or [],
                    "integer_only": card.get("type") == "numeric",
                    "key": card.get("correct_answer"),
                    "page_number": card.get("page_number"),
                    "ephemeral": True,
                }
                stage_index = ("join", "teams", "meet", "round", "play").index(
                    str(card.get("stage") or "round")
                )
                prompt = self.set_live_session_prompt(
                    session_id,
                    slide_index=9000
                    + stage_index * 100
                    + int(card.get("order") or 1),
                    kind=str(payload["kind"]),
                    payload=payload,
                    activate=True,
                )
                prompt_id = prompt.get("id")
        current = self.live_session_teacher_state_payload(session_id)
        views = {
            str(row.get("id")): "none"
            for row in cards
            if str(row.get("id") or "").strip()
        }
        views[key] = wanted
        student_view = dict(current.get("student_view") or {})
        student_view["questions"] = wanted
        next_state = apply_teacher_state_update(
            current,
            question_views=views,
            student_view=student_view,
            prompt_ref=key,
        )
        written = self._write_teacher_state(session_id, next_state)
        return {
            "teacher_state": written,
            "question_cards": self.live_session_question_cards(session_id),
            "prompt_id": int(prompt_id) if prompt_id not in (None, "") else None,
        }

    @staticmethod
    def _numeric_within_tolerance(
        actual: float,
        expected: float,
        tolerance: Any,
        tolerance_kind: Any,
    ) -> bool:
        """Return True when ``actual`` is within the authored numeric window.

        Args:
            actual: Student numeric response.
            expected: Authored correct value.
            tolerance: Absolute delta or percent, depending on ``tolerance_kind``.
            tolerance_kind: ``absolute`` or ``percent``.
        """
        try:
            window = float(tolerance)
        except (TypeError, ValueError):
            window = 0.0
        kind = str(tolerance_kind or "absolute").strip().lower()
        if kind in {"percent", "percentage", "pct"}:
            if expected == 0:
                return abs(actual) <= window
            return abs(actual - expected) <= abs(expected) * (window / 100.0)
        return abs(actual - expected) <= window

    def _response_matches_key(
        self,
        *,
        letter: str | None,
        value: Any,
        key: str,
        raw_key: str,
        payload: dict[str, Any] | None = None,
    ) -> bool | None:
        """Return whether one answer matches the teacher key.

        Args:
            letter: Normalized MC letter, if any.
            value: Numeric or free-text answer.
            key: Uppercased letter key.
            raw_key: Original key string for numeric compare.
            payload: Prompt payload that may hold numeric tolerance.
        """
        body = payload if isinstance(payload, dict) else {}
        if raw_key and value not in (None, ""):
            try:
                return self._numeric_within_tolerance(
                    float(str(value).strip()),
                    float(str(raw_key).strip()),
                    body.get("tolerance"),
                    body.get("tolerance_kind"),
                )
            except (TypeError, ValueError):
                pass
        if key and letter:
            return letter == key
        if raw_key and value not in (None, ""):
            return str(value).strip() == raw_key
        return None

    def live_prompt_response_roster(
        self, session_id: int, prompt_id: int
    ) -> list[dict[str, Any]]:
        """Return ephemeral named responses for the active staff session."""

        prompt = next(
            (
                row
                for row in self._list_live_session_prompts(session_id)
                if int(row["id"]) == int(prompt_id)
            ),
            None,
        )
        if prompt is None:
            raise KeyError(f"prompt {prompt_id}")
        session_row = self.get_live_session(session_id)
        if session_row is None:
            raise KeyError(f"live session {session_id}")
        class_id = int(session_row["class_id"])
        with self.game._lock:
            students = [
                dict(row)
                for row in self.game.conn.execute(
                    "SELECT * FROM students WHERE class_id = ?",
                    (class_id,),
                )
            ]
        student_map = {int(row["id"]): row for row in students}
        attendees = self.list_live_session_attendees(session_id)
        guest_map = {
            str(row.get("participant_uuid") or ""): row
            for row in attendees
            if row.get("participant_uuid")
        }
        payload = prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
        choices = [str(item) for item in payload.get("choices") or []]
        raw_key = str(
            payload.get("key")
            or payload.get("correct_answer")
            or ((payload.get("correct_ids") or [""])[0])
        ).strip()
        key = raw_key.upper()
        rows: list[dict[str, Any]] = []
        for response_row in self.list_live_prompt_responses(prompt_id):
            sid = response_row.get("student_id")
            student = student_map.get(int(sid)) if sid not in (None, "") else None
            guest = guest_map.get(str(response_row.get("participant_uuid") or ""))
            answer = response_row.get("response") or {}
            letter = choice_letter(answer, choices)
            value = answer.get("value")
            if value is None:
                value = answer.get("text")
            if value is None:
                value = answer.get("share")
            if value is None:
                value = answer.get("choice")
            label = (
                choices[ord(letter) - ord("A")]
                if letter and ord(letter) - ord("A") < len(choices)
                else value
            )
            rows.append(
                {
                    "student_id": int(sid) if sid not in (None, "") else None,
                    "name": str(
                        (student or {}).get("codename")
                        or (student or {}).get("first_name")
                        or (guest or {}).get("display_name")
                        or "Guest"
                    ).strip(),
                    "character": (student or {}).get("character_key"),
                    "answer": str(label if label is not None else "").strip(),
                    "choice": letter or None,
                    "correct": self._response_matches_key(
                        letter=letter,
                        value=value,
                        key=key,
                        raw_key=raw_key,
                        payload=payload,
                    ),
                    "awarded_points": response_row.get("awarded_points"),
                }
            )
        rows.sort(key=lambda row: str(row["name"]).casefold())
        return rows

    def award_live_prompt_points(
        self,
        session_id: int,
        prompt_id: int,
        *,
        mode: str,
        student_ids: list[Any] | None = None,
        amount: int = 1,
        replace: bool = True,
    ) -> dict[str, Any]:
        """Award prompt points to a selected set, replacing a prior assignment.

        Args:
            session_id: ``live_class_sessions.id``.
            prompt_id: ``live_session_prompts.id``.
            mode: ``answered``, ``correct``, or ``manual``.
            student_ids: Roster ids when ``mode`` is ``manual``.
            amount: Points each selected student should hold after commit.
            replace: When true, unchecked respondents lose any prior award.
        """

        token = str(mode or "").strip().lower()
        if token not in {"answered", "correct", "manual"}:
            raise ValueError("mode must be answered, correct, or manual")
        points = int(amount)
        if points == 0:
            raise ValueError("amount cannot be 0")
        rows = self.live_prompt_response_roster(session_id, prompt_id)
        if token == "answered":
            targets = [row["student_id"] for row in rows]
        elif token == "correct":
            if not any(row.get("correct") is not None for row in rows):
                raise ValueError("This question has no correct answer.")
            targets = [
                row["student_id"] for row in rows if row.get("correct") is True
            ]
        else:
            wanted = {
                int(value)
                for value in (student_ids or [])
                if str(value or "").strip().isdigit()
            }
            targets = [
                row["student_id"] for row in rows if row.get("student_id") in wanted
            ]
        selected = {int(value) for value in targets if value not in (None, "")}
        session_row = self.get_live_session(session_id)
        if session_row is None:
            raise KeyError(f"live session {session_id}")
        plan: list[tuple[int, int, int]] = []
        for row in rows:
            sid = row.get("student_id")
            if sid in (None, ""):
                continue
            student_id = int(sid)
            current = int(row.get("awarded_points") or 0)
            if replace:
                desired = points if student_id in selected else 0
            else:
                desired = current + (points if student_id in selected else 0)
            plan.append((student_id, current, int(desired)))
        game = None
        for student_id, current, desired in plan:
            delta = desired - current
            if delta:
                game = self.game.award_points(
                    int(session_row["class_id"]),
                    kind="student",
                    target_id=student_id,
                    amount=delta,
                    label="Live question",
                )
        with self._lock:
            for student_id, current, desired in plan:
                if desired != current:
                    self.conn.execute(
                        """
                        UPDATE live_session_responses
                        SET awarded_points = ?
                        WHERE prompt_id = ? AND student_id = ?
                        """,
                        (desired, int(prompt_id), student_id),
                    )
            self.conn.commit()
        return {
            "awarded_student_ids": sorted(selected),
            "game": game,
            "responses": self.live_prompt_response_roster(session_id, prompt_id),
        }

    def live_session_mc_tally(self, session_id: int) -> dict[str, Any] | None:
        """Staff-only MC distribution for the current active prompt.

        Source-agnostic: JOIN Minds-On, MEET soft MC, CONS, and any other
        ``kind=mc`` ride share this shape. Meet tallies ephemeral chain
        picks (no gradebook).         Seeds waiting-room Minds-On so JOIN staff
        polls see the same prompt students get. TEAMS keeps that
        Minds-On tally for students (teacher spark is staff-only).

        Args:
            session_id: ``live_class_sessions.id``.
        """
        if self._published_stage_catalogue_prompt(session_id, "join") is None:
            self.ensure_waiting_room_minds_on(session_id)
        try:
            teacher = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            teacher = None
        stage = str((teacher or {}).get("stage") or "")
        prompt = self.get_active_live_prompt(session_id)
        if stage == "join":
            published = self._published_stage_catalogue_prompt(session_id, "join")
            if published is not None:
                prompt = published
            else:
                join_row = self._prompt_at_slide(session_id, int(MINDS_ON_SLIDE_INDEX))
                if join_row is not None:
                    prompt = join_row
        elif stage == "teams":
            spark = self._prompt_at_slide(session_id, int(TEAMS_SPARK_SLIDE_INDEX))
            if spark is not None:
                prompt = spark
        elif stage == "meet":
            meet_row = self._prompt_at_slide(session_id, int(MEET_TEAM_SLIDE_INDEX))
            if meet_row is None or not is_meet_team_payload(meet_row.get("payload")):
                meet_row = None
                for row in self._list_live_session_prompts(session_id):
                    if is_meet_team_payload(row.get("payload")):
                        meet_row = row
                        if row.get("active"):
                            break
            if meet_row is not None:
                prompt = meet_row
        if prompt is None:
            for item in self.list_live_session_items(session_id):
                if str(item.get("status") or "") != "active":
                    continue
                item_stage = str(item.get("stage") or "").strip().lower()
                if item_stage and stage and item_stage != stage:
                    continue
                linked = self._prompt_for_live_item(item)
                if linked is not None:
                    prompt = linked
                    break
        if prompt is None:
            return None
        if is_minds_on_payload(prompt.get("payload")) and stage not in {
            "join",
            "teams",
        }:
            return None
        attendees = self.list_live_session_attendees(session_id)
        present = sum(1 for row in attendees if not row.get("left_at"))
        prompt_id = prompt.get("id")
        responses = (
            self.list_live_prompt_responses(int(prompt_id))
            if prompt_id not in (None, "")
            else []
        )
        return build_live_tally(
            prompt,
            responses=responses,
            meet_chain=(teacher or {}).get("meet_chain"),
            present=present,
            teacher_state=teacher,
        )

    def live_session_mc_poll_closed(self, session_id: int) -> bool:
        """True when JOIN Reveal (or an explicit flag) has closed the MC poll.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        try:
            teacher = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            return False
        return mc_poll_closed(teacher)

    def student_visible_mc_tally(self, session_id: int) -> dict[str, Any] | None:
        """Return the class MC summary only when the teacher has shared Reveal.

        JOIN Reveal commits ``reveal_to_students``. Other stages stay
        teacher-only unless that flag is set. Same tally shape as staff.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        try:
            teacher = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            return None
        if not student_mc_summary_visible(teacher):
            return None
        return self.live_session_mc_tally(session_id)

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

    def student_game_show_welcome(self, session_id: int) -> dict[str, Any]:
        """Build the TEAMS welcome card for student phones.

        Args:
            session_id: ``live_class_sessions.id``.

        Returns:
            Title, class/lesson codes, round blurbs, and present avatars.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            raise KeyError(f"live session {session_id}")
        class_id = int(session_row["class_id"])
        cls = self.enrich_class(self.game.get_class(class_id))
        class_code = str(
            cls.get("section_code")
            or cls.get("ontario_code")
            or cls.get("course_code")
            or ""
        ).strip()
        teacher = self.live_session_teacher_state_payload(session_id)
        module = normalize_live_module(teacher.get("live_module"))
        slot = normalize_live_slot(teacher.get("live_slot"))
        characters = self.game.character_keys(class_id)
        participants: list[dict[str, Any]] = []
        for row in self.list_live_session_attendees(session_id, present_only=True):
            sid = row.get("student_id")
            name = first_name_only(str(row.get("codename") or "")) or (
                "Guest" if row.get("unmatched") else "Student"
            )
            character = None
            if sid not in (None, ""):
                character = characters.get(int(sid))
            participants.append(
                {
                    "student_id": int(sid) if sid not in (None, "") else None,
                    "codename": name,
                    "character": character,
                }
            )
        participants.sort(key=lambda row: str(row.get("codename") or "").lower())
        return game_show_welcome_payload(
            class_code=class_code,
            lesson_code=f"{module}-{slot}",
            participants=participants,
        )

    def _student_answered_prompt(
        self,
        prompt: dict[str, Any] | None,
        student_id: int | None,
        participant_uuid: str,
    ) -> bool:
        """True when this student already has a response on ``prompt``.

        Args:
            prompt: Live-prompt row.
            student_id: Roster id, if any.
            participant_uuid: Live-session person key.
        """
        if not prompt or prompt.get("id") in (None, ""):
            return False
        return (
            self.get_live_prompt_response(
                int(prompt["id"]), student_id, participant_uuid=participant_uuid
            )
            is not None
        )

    @staticmethod
    def _same_live_item_id(left: Any, right: Any) -> bool:
        """True when two playlist/engine-ride ids name the same item."""
        first = str(left or "").strip().lower().replace("-", "_")
        second = str(right or "").strip().lower().replace("-", "_")
        return bool(first) and first == second

    def _lifecycle_item_for_prompt(
        self, session_id: int, prompt: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Return the live_session_items row linked to one prompt, if any.

        Args:
            session_id: ``live_class_sessions.id``.
            prompt: Prompt row to match by ``prompt_id`` or payload item id.
        """
        if not prompt:
            return None
        prompt_id = prompt.get("id")
        payload = (
            prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
        )
        item_id = str(payload.get("item_id") or payload.get("pack") or "").strip()
        for item in self.list_live_session_items(session_id):
            linked = item.get("prompt_id")
            if (
                prompt_id not in (None, "")
                and linked not in (None, "")
                and int(linked) == int(prompt_id)
            ):
                return item
            if item_id and self._same_live_item_id(item.get("item_id"), item_id):
                return item
        return None

    @staticmethod
    def _live_item_is_student_visible(item: dict[str, Any] | None) -> bool:
        """True when a lifecycle row is published to students.

        Args:
            item: ``live_session_items`` row, or None.

        Returns:
            True when ``status`` is ``active`` or ``closed``.
        """
        if not item:
            return False
        return str(item.get("status") or "") in {"active", "closed"}

    def _lifecycle_item_is_published(self, session_id: int, item_id: str) -> bool:
        """True when a lifecycle row for ``item_id`` is active or closed.

        Args:
            session_id: ``live_class_sessions.id``.
            item_id: Playlist or engine-ride id.
        """
        for item in self.list_live_session_items(session_id):
            if not self._same_live_item_id(item.get("item_id"), item_id):
                continue
            if self._live_item_is_student_visible(item):
                return True
        return False

    def _student_visible_prompt(
        self, session_id: int, prompt: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Return ``prompt`` only when students may see it.

        When a matching ``live_session_items`` row exists, it must be
        student-visible (``active`` or ``closed``). Prompts with no
        lifecycle row keep the legacy staff-set ``/prompts`` path.

        Args:
            session_id: ``live_class_sessions.id``.
            prompt: Candidate prompt row.
        """
        if not prompt:
            return None
        item = self._lifecycle_item_for_prompt(session_id, prompt)
        if item is not None:
            return prompt if self._live_item_is_student_visible(item) else None
        payload = (
            prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
        )
        ride_id = str(payload.get("item_id") or payload.get("pack") or "").strip()
        if self._engine_ride_item_id(ride_id, payload):
            if self._lifecycle_item_is_published(session_id, ride_id):
                return prompt
            try:
                teacher = self.live_session_teacher_state_payload(session_id)
                stage = str(teacher.get("stage") or "")
            except KeyError:
                stage = ""
            if self.schema_v2_owns_live_stage_questions(session_id, stage or None):
                return None
        return prompt

    def _student_may_see_tally(
        self,
        session_id: int,
        prompt: dict[str, Any] | None,
        *,
        my_response: Any = None,
        teacher: dict[str, Any] | None = None,
    ) -> bool:
        """True when a student payload may include live MC totals.

        Closed items stay visible. Active items need Show live results
        plus a submit or Reveal. Prompts with no lifecycle row keep the
        legacy ``my_response`` / Reveal rule.

        Args:
            session_id: ``live_class_sessions.id``.
            prompt: Facing prompt row.
            my_response: Prior or just-saved student response, if any.
            teacher: Public teacher state for Reveal.
        """
        lifecycle = self._lifecycle_item_for_prompt(session_id, prompt)
        if lifecycle is None:
            return my_response is not None or student_mc_summary_visible(teacher)
        status = str(lifecycle.get("status") or "")
        return status == "closed" or (
            bool(lifecycle.get("show_live_results"))
            and (
                my_response is not None
                or student_mc_summary_visible(teacher)
            )
        )

    def _published_stage_catalogue_prompt(
        self, session_id: int, stage: str
    ) -> dict[str, Any] | None:
        """Return the first active catalogue prompt for a teacher stage.

        Leftover waiting-room minds-on stays in the prompt table after staff
        publish a join-page MC. Prefer that published item so student Submit
        records the facing question, not the leftover card.

        Args:
            session_id: ``live_class_sessions.id``.
            stage: Current teacher stage.
        """
        wanted = str(stage or "").strip().lower()
        leftover = {"minds_on", "minds-on"} if wanted == "join" else set()
        for item in self.list_live_session_items(session_id):
            if str(item.get("status") or "") != "active":
                continue
            item_stage = str(item.get("stage") or "").strip().lower()
            if item_stage and wanted and item_stage != wanted:
                continue
            item_id = str(item.get("item_id") or "").strip().lower().replace("_", "-")
            if item_id in leftover or item_id.replace("-", "_") in {
                token.replace("-", "_") for token in leftover
            }:
                continue
            kind = str(item.get("kind") or "").strip().lower()
            if kind in {"media", "whiteboard", "slides"}:
                continue
            prompt = self._prompt_for_live_item(item)
            if prompt is not None:
                return prompt
        return None

    def _student_stage_prompt(
        self,
        session_id: int,
        *,
        teacher: dict[str, Any] | None,
        student_id: int | None,
        participant_uuid: str,
    ) -> dict[str, Any] | None:
        """Pick the student-facing prompt for the current teacher stage.

        Join shows the waiting-room minds-on until staff publish a join-page
        catalogue question, then that item. Welcome shows C2. Meet keeps the
        visible chain step.

        Args:
            session_id: ``live_class_sessions.id``.
            teacher: Public teacher state.
            student_id: Roster id, if any.
            participant_uuid: Live-session person key.
        """
        stage = str((teacher or {}).get("stage") or "")
        question_views = (teacher or {}).get("question_views")
        if isinstance(question_views, dict):
            wanted = next(
                (
                    str(key)
                    for key, value in question_views.items()
                    if value == "student"
                ),
                "",
            )
            if wanted:
                for row in self._list_live_session_prompts(session_id):
                    payload = (
                        row.get("payload")
                        if isinstance(row.get("payload"), dict)
                        else {}
                    )
                    item_id = str(
                        payload.get("item_id")
                        or payload.get("pack")
                        or f"prompt-{row.get('id')}"
                    ).strip()
                    if item_id == wanted:
                        return self._student_visible_prompt(session_id, row)
        if stage == "meet":
            return self._student_visible_prompt(
                session_id, self.get_active_live_prompt(session_id)
            )
        minds = self._prompt_at_slide(session_id, int(MINDS_ON_SLIDE_INDEX))
        spark = self._prompt_at_slide(session_id, int(TEAMS_SPARK_SLIDE_INDEX))
        answered_c1 = self._student_answered_prompt(
            minds, student_id, participant_uuid
        )
        active = self.get_active_live_prompt(session_id)
        if active and is_cons_payload(active.get("payload")):
            return self._student_visible_prompt(session_id, active)
        if stage == "join":
            published = self._published_stage_catalogue_prompt(session_id, "join")
            if published is not None:
                return published
            if (
                mc_poll_closed(teacher)
                and spark is not None
                and self.schema_v2_owns_live_stage_questions(session_id, "join")
            ):
                return self._student_visible_prompt(session_id, spark)
            if self._session_playlist_item_removed(session_id, "minds_on"):
                return (
                    None
                    if is_minds_on_payload((active or {}).get("payload"))
                    else self._student_visible_prompt(session_id, active)
                )
            return self._student_visible_prompt(session_id, minds or active)
        if stage == "teams":
            self.ensure_teams_spark(session_id)
            published = self._published_stage_catalogue_prompt(session_id, "teams")
            if published is not None:
                return published
            return self._student_visible_prompt(
                session_id,
                self._prompt_at_slide(session_id, int(TEAMS_SPARK_SLIDE_INDEX))
                or spark,
            )
        published = self._published_stage_catalogue_prompt(session_id, stage)
        if published is not None:
            return published
        return self._student_visible_prompt(session_id, active)

    def _tally_for_prompt(
        self,
        session_id: int,
        prompt: dict[str, Any] | None,
        teacher: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Tally one prompt row for staff/student graphs.

        Args:
            session_id: ``live_class_sessions.id``.
            prompt: Prompt row to tally.
            teacher: Public teacher state.
        """
        if not prompt or prompt.get("id") in (None, ""):
            return None
        attendees = self.list_live_session_attendees(session_id)
        present = sum(1 for row in attendees if not row.get("left_at"))
        return build_live_tally(
            prompt,
            responses=self.list_live_prompt_responses(int(prompt["id"])),
            meet_chain=(teacher or {}).get("meet_chain"),
            present=present,
            teacher_state=teacher,
        )

    def _questions_view_mode(self, session_id: int) -> str:
        """Return the Questions student-view mode for one live session.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        try:
            teacher = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            return "student"
        view = teacher.get("student_view")
        token = ""
        if isinstance(view, dict):
            token = str(view.get("questions") or "").strip().lower()
        return token if token in {"none", "student", "team"} else "student"

    def _teammate_ids_for_class(self, class_id: int, team_id: int) -> list[int]:
        """Roster ids on one team, excluding the Class bucket.

        Args:
            class_id: Game-show ``classes.id``.
            team_id: Assigned team id.
        """
        try:
            state = self.game.game_state(int(class_id))
        except Exception:  # noqa: BLE001
            return []
        out: list[int] = []
        for team in state.get("teams") or []:
            try:
                if int(team.get("id") or 0) != int(team_id):
                    continue
            except (TypeError, ValueError):
                continue
            if str(team.get("name") or "") == "Class":
                continue
            for member in team.get("members") or []:
                try:
                    out.append(int(member.get("id")))
                except (TypeError, ValueError):
                    continue
        return out

    def _copy_group_question_response(
        self,
        session_id: int,
        *,
        prompt_id: int,
        student_id: int,
        response: dict[str, Any],
    ) -> None:
        """Mirror a Shared-within-Group answer onto every teammate.

        Team-shared copies are stamped ``question_view=team`` so they
        never auto-score participation game points.

        Args:
            session_id: ``live_class_sessions.id``.
            prompt_id: ``live_session_prompts.id``.
            student_id: Roster id of the student who submitted.
            response: Submitted answer JSON.
        """
        if self._questions_view_mode(session_id) != "team":
            return
        session_row = self.get_live_session(session_id)
        if session_row is None:
            return
        team_id = self.student_team_id_for_class(
            int(session_row["class_id"]), int(student_id)
        )
        if team_id is None:
            return
        stamped = dict(response or {})
        stamped["question_view"] = "team"
        now = _now()
        body = json.dumps(stamped)
        for tid in self._teammate_ids_for_class(
            int(session_row["class_id"]), team_id
        ):
            if tid == int(student_id):
                continue
            with self._lock:
                existing = self.conn.execute(
                    """
                    SELECT id FROM live_session_responses
                    WHERE prompt_id = ? AND student_id = ?
                    """,
                    (int(prompt_id), tid),
                ).fetchone()
                if existing is not None:
                    self.conn.execute(
                        """
                        UPDATE live_session_responses
                        SET response_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (body, now, int(existing["id"])),
                    )
                else:
                    self.conn.execute(
                        """
                        INSERT INTO live_session_responses (
                            prompt_id, student_id, participant_uuid,
                            response_json, awarded_points, created_at, updated_at
                        ) VALUES (?, ?, NULL, ?, NULL, ?, ?)
                        """,
                        (int(prompt_id), tid, body, now, now),
                    )
                self.conn.commit()

    def group_question_draft(
        self,
        session_id: int,
        *,
        prompt_id: int,
        student_id: int | None,
    ) -> dict[str, Any] | None:
        """Return the shared group draft for this student's team, if any.

        Args:
            session_id: ``live_class_sessions.id``.
            prompt_id: ``live_session_prompts.id``.
            student_id: Roster id.
        """
        if student_id in (None, ""):
            return None
        try:
            teacher = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            return None
        drafts = teacher.get("group_drafts")
        if not isinstance(drafts, dict):
            return None
        session_row = self.get_live_session(session_id)
        if session_row is None:
            return None
        team_id = self.student_team_id_for_class(
            int(session_row["class_id"]), int(student_id)
        )
        if team_id is None:
            return None
        row = drafts.get(f"{int(prompt_id)}:{int(team_id)}")
        return row if isinstance(row, dict) else None

    def set_group_question_draft(
        self,
        session_id: int,
        *,
        prompt_id: int,
        student_id: int | None,
        choice: str,
    ) -> dict[str, Any] | None:
        """Store a one-response-per-group draft selection.

        Args:
            session_id: ``live_class_sessions.id``.
            prompt_id: ``live_session_prompts.id``.
            student_id: Roster id of the student who clicked.
            choice: Selected option text.
        """
        if student_id in (None, ""):
            return None
        session_row = self.get_live_session(session_id)
        if session_row is None:
            return None
        team_id = self.student_team_id_for_class(
            int(session_row["class_id"]), int(student_id)
        )
        if team_id is None:
            return None
        current = self.live_session_teacher_state_payload(session_id)
        drafts = dict(current.get("group_drafts") or {})
        drafts[f"{int(prompt_id)}:{int(team_id)}"] = {
            "choice": str(choice or "").strip(),
            "by": int(student_id),
        }
        current["group_drafts"] = drafts
        self._write_teacher_state(session_id, current)
        return drafts[f"{int(prompt_id)}:{int(team_id)}"]

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
            Dict with ``prompt`` (or ``None``), optional ``my_response``,
            and JOIN Reveal fields ``poll_closed`` / ``mc_tally``.
        """
        teacher = None
        try:
            teacher = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            teacher = None
        stage = str((teacher or {}).get("stage") or "")
        meet_state = public_meet_chain((teacher or {}).get("meet_chain"))
        meet_live = stage == "meet" and meet_state is not None
        if meet_live:
            if (
                self._lifecycle_item_is_published(session_id, "meet_team")
                or self._published_stage_catalogue_prompt(session_id, "meet")
                is not None
            ):
                self._ensure_student_meet_prompt(session_id, meet_state)
            prompt = self._student_stage_prompt(
                session_id,
                teacher=teacher,
                student_id=student_id,
                participant_uuid=participant_uuid,
            )
        else:
            if self._lifecycle_item_is_published(session_id, "minds_on"):
                self.ensure_waiting_room_minds_on(session_id)
            elif not self.schema_v2_owns_live_stage_questions(session_id, "join"):
                self.ensure_waiting_room_minds_on(session_id)
            prompt = self._student_stage_prompt(
                session_id,
                teacher=teacher,
                student_id=student_id,
                participant_uuid=participant_uuid,
            )
        waiting_room = False if meet_live else not self._session_left_waiting_room(
            session_id
        )
        poll_closed = False
        view = (teacher or {}).get("student_view") or {}
        questions_mode = str(view.get("questions") or "none")
        empty = {
            "prompt": None,
            "my_response": None,
            "waiting_room": waiting_room,
            "poll_closed": poll_closed,
            "question_view": questions_mode,
        }
        empty.update(
            self.student_live_items_payload(
                session_id,
                student_id,
                participant_uuid=participant_uuid,
            )
        )
        if stage == "teams":
            empty["game_show_welcome"] = self.student_game_show_welcome(session_id)
        if questions_mode == "none" and not meet_live:
            return empty
        if prompt is None or prompt.get("kind") == "idle":
            return empty
        raw_payload = dict(prompt.get("payload") or {})
        poll_closed = mc_poll_closed(teacher) and is_minds_on_payload(raw_payload)
        if is_minds_on_payload(raw_payload) and stage != "join":
            return empty
        if is_teams_spark_payload(raw_payload) and stage != "teams":
            if not (
                stage == "join"
                and mc_poll_closed(teacher)
                and self.schema_v2_owns_live_stage_questions(session_id, "join")
            ):
                return empty
        if is_meet_team_payload(raw_payload) and not meet_live and stage != "meet":
            return empty
        if is_cons_payload(raw_payload):
            slot = self.session_live_slot(session_id)
            if slot == "C1":
                media = self.live_session_active_media_payload(session_id)
                if not media or not media.get("frozen") or not is_c1_real_slice(media):
                    return empty
            else:
                ride = self.session_text_ride(session_id)
                item_id = str(raw_payload.get("item_id") or "").strip()
                if not ride.get("frozen") or item_id != str(ride.get("cons_item") or ""):
                    return empty
        prior = None
        my_response = None
        prior = self.get_live_prompt_response(
            int(prompt["id"]), student_id, participant_uuid=participant_uuid
        )
        if (
            prior is None
            and is_meet_team_payload(raw_payload)
            and meet_state is not None
        ):
            token = meet_participant_key(
                participant_uuid=participant_uuid, student_id=student_id
            )
            step = str(raw_payload.get("step") or "A")
            bag_key = {"A": "a_picks", "C": "c_reacts", "B": "b_picks"}.get(
                step, "a_picks"
            )
            choice = str((meet_state.get(bag_key) or {}).get(token) or "").strip()
            if choice:
                my_response = {
                    "response": {"choice": choice},
                    "awarded_points": None,
                    "updated_at": None,
                    "ephemeral": True,
                }
        if prior is not None:
            my_response = {
                "response": prior.get("response") or {},
                "awarded_points": prior.get("awarded_points"),
                "updated_at": prior.get("updated_at"),
            }
        cleaned = strip_teacher_prompt_fields(raw_payload)
        if is_teams_spark_payload(raw_payload):
            ui = teacher.get("mc_ui") if isinstance((teacher or {}).get("mc_ui"), dict) else {}
            revealed = bool(ui.get("reveal") and ui.get("reveal_to_students"))
            if not revealed:
                cleaned.pop("student_feedback_after_reveal", None)
        out = {
            "prompt": {
                "id": int(prompt["id"]),
                "slide_index": int(prompt["slide_index"]),
                "kind": str(prompt["kind"]),
                "payload": cleaned,
            },
            "my_response": my_response,
            "waiting_room": waiting_room,
            "poll_closed": poll_closed,
            "question_view": questions_mode,
            "meet_chip": meet_chip_for(
                meet_state,
                meet_participant_key(
                    participant_uuid=participant_uuid, student_id=student_id
                ),
            )
            if meet_state is not None
            else None,
        }
        if stage == "teams":
            out["game_show_welcome"] = self.student_game_show_welcome(session_id)
        shared_tally = self._tally_for_prompt(session_id, prompt, teacher)
        if shared_tally is not None and self._student_may_see_tally(
            session_id,
            prompt,
            my_response=my_response,
            teacher=teacher,
        ):
            out["mc_tally"] = shared_tally
        draft = self.group_question_draft(
            session_id,
            prompt_id=int(prompt["id"]),
            student_id=student_id,
        )
        if draft:
            out["group_draft"] = draft
        out.update(
            self.student_live_items_payload(
                session_id,
                student_id,
                participant_uuid=participant_uuid,
            )
        )
        return out

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
        if not isinstance(stored, dict):
            return None
        public = public_active_media_payload(stored)
        if public and is_c1_real_slice(public):
            cls = self.enrich_class(self.game.get_class(int(session_row["class_id"])))
            ontario = str(cls.get("ontario_code") or cls.get("course_code") or "")
            if not uses_c1_real_slice(
                ontario,
                self.session_live_module(session_id),
                self.session_live_slot(session_id),
            ):
                return None
        return public

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
        param_push: Any = None,
        param_frozen: Any = None,
        reveal_axes: Any = None,
        reveal_lateral: Any = None,
        allow_3d_limited: Any = None,
        show_z_axis: Any = None,
        student_zoom: Any = None,
        freeze_zoom: Any = None,
        surface_transparency: Any = None,
        freeze_surface: Any = None,
        student_yaw_range: Any = None,
        freeze_yaw: Any = None,
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
        persist_media_copy: bool = True,
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
            param_push: Optional ``{a,b,c}`` push-to-student-view flags.
            param_frozen: Optional ``{a,b,c}`` freeze flags.
            reveal_axes: Teacher peel for student axes/grid.
            reveal_lateral: In-pane lateral slice reveal (L4).
            allow_3d_limited: Small student yaw after lateral, not free orbit.
            show_z_axis: Show the z-axis on student and teacher faces.
            student_zoom: 0 = paper distance, 10 = full zoom-in.
            freeze_zoom: Lock the student zoom slider.
            surface_transparency: 0 = faint saddle, 10 = solid.
            freeze_surface: Lock the 3D surface transparency slider.
            student_yaw_range: 0 = fixed, 360 = full student yaw (degrees).
            freeze_yaw: Lock the student yaw-range slider.
            frozen: Argue done; optional click-out encore may appear.
            unlock_flags: Partial L0–L4 flags (delight pass).
            answers: Optional engagement choices for the current reveal.
            params: Optional ``{a,b,c}`` for y = ax^2 + bx + c.
            challenge: ``C1`` / ``C2`` / ``C3``. Challenge C2/C3 without a
                URL still clear the Real-slice blob. An explicit playlist
                URL on C2/C3 is stored so authored media can mount.
            cons_item: Post-freeze CONS-1…5 id, or empty to clear.
            toast: Optional Wonder toast overlay.
            toast_key: Optional toast identity.
            allow_url_swap: When False, production seed-locks the Real-slice URL.
            merge: When True, treat omitted url as a patch of current media.
            persist_media_copy: When False, skip writing ``class_live_media_overlays``
                (automatic slot remounts must not overwrite a saved overlay).

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
        if param_push is not None:
            kwargs["param_push"] = param_push
        if param_frozen is not None:
            kwargs["param_frozen"] = param_frozen
        if reveal_axes is not None:
            kwargs["reveal_axes"] = reveal_axes
        if reveal_lateral is not None:
            kwargs["reveal_lateral"] = reveal_lateral
        if allow_3d_limited is not None:
            kwargs["allow_3d_limited"] = allow_3d_limited
        if show_z_axis is not None:
            kwargs["show_z_axis"] = show_z_axis
        if student_zoom is not None:
            kwargs["student_zoom"] = student_zoom
        if freeze_zoom is not None:
            kwargs["freeze_zoom"] = freeze_zoom
        if surface_transparency is not None:
            kwargs["surface_transparency"] = surface_transparency
        if freeze_surface is not None:
            kwargs["freeze_surface"] = freeze_surface
        if student_yaw_range is not None:
            kwargs["student_yaw_range"] = student_yaw_range
        if freeze_yaw is not None:
            kwargs["freeze_yaw"] = freeze_yaw
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
        slot = (
            normalize_live_slot(challenge)
            if challenge is not None
            else self.session_live_slot(session_id)
        )
        explicit_url = url is not None and str(url).strip()
        switching_text_ride = (
            slot in {"C2", "C3"}
            and not explicit_url
            and (challenge is not None or current is None)
        )
        if switching_text_ride:
            payload = None
            if challenge is not None or current is not None:
                payload = apply_active_media_update(
                    current, challenge=slot, updated_at=_now()
                )
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
            self._persist_live_slot(session_id, slot)
            waiting_room = not self._session_left_waiting_room(session_id)
            if waiting_room and not self.schema_v2_owns_live_stage_questions(session_id):
                self.ensure_waiting_room_minds_on(session_id)
            elif challenge is not None:
                teacher = self.live_session_teacher_state_payload(session_id)
                stage = str(teacher.get("stage") or "")
                frames = teacher.get("student_frames") or {}
                if stage in {"round", "play"} or bool(frames.get("media")):
                    self.clear_session_warmups(session_id)
            if (
                frozen is not None
                or cons_item is not None
                or toast is not None
                or toast_key is not None
            ):
                self._apply_text_ride_update(
                    session_id,
                    frozen=frozen,
                    cons_item=cons_item,
                    toast=toast,
                    toast_key=toast_key,
                )
            self._sync_cons_prompt(session_id, None)
            return None
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
        if challenge is not None:
            self._persist_live_slot(session_id, slot)
            if not self._session_left_waiting_room(session_id) and not self.schema_v2_owns_live_stage_questions(session_id):
                self.ensure_waiting_room_minds_on(session_id)
        if payload is not None:
            teacher = self.live_session_teacher_state_payload(session_id)
            stage = str(teacher.get("stage") or "")
            frames = teacher.get("student_frames") or {}
            if stage in {"round", "play"} or bool(frames.get("media")):
                self.clear_session_warmups(session_id)
            if persist_media_copy and (stem is not None or caption is not None):
                self.upsert_class_live_media_copy(
                    int(session_row["class_id"]),
                    teacher.get("live_module") or "M1",
                    teacher.get("live_slot") or slot,
                    stem=None if stem is None else str(payload.get("stem") or ""),
                    caption=None
                    if caption is None
                    else str(payload.get("caption") or ""),
                )
        self._sync_cons_prompt(session_id, payload)
        return payload

    def _persist_live_slot(self, session_id: int, live_slot: str) -> dict[str, Any]:
        """Write ``live_slot`` on the teacher channel and reset C1 text_ride.

        Args:
            session_id: ``live_class_sessions.id``.
            live_slot: ``C1`` / ``C2`` / ``C3``.
        """
        current = self.live_session_teacher_state_payload(session_id)
        slot = normalize_live_slot(live_slot)
        if current.get("live_slot") == slot:
            return current
        payload = apply_teacher_state_update(
            current,
            live_slot=slot,
            text_ride=default_text_ride(),
        )
        return self._write_teacher_state(session_id, payload)

    def _apply_text_ride_update(
        self,
        session_id: int,
        *,
        frozen: Any = None,
        cons_item: Any = None,
        toast: Any = None,
        toast_key: Any = None,
    ) -> dict[str, Any]:
        """Patch the C2/C3 text-only freeze + CONS ride.

        Args:
            session_id: ``live_class_sessions.id``.
            frozen: Session freeze (CONS unlocks only after True).
            cons_item: Post-freeze CONS id, or empty to clear.
            toast: Optional Wonder toast overlay.
            toast_key: Optional toast identity.
        """
        slot = self.session_live_slot(session_id)
        if slot not in {"C2", "C3"}:
            raise ValueError("Text-only consolidation is only for C2 and C3.")
        current = self.live_session_teacher_state_payload(session_id)
        ride = public_text_ride(current.get("text_ride"))
        prev_frozen = bool(ride.get("frozen"))
        prev_cons = str(ride.get("cons_item") or "")
        if frozen is not None:
            if isinstance(frozen, str):
                ride["frozen"] = frozen.strip().lower() in {"1", "true", "yes"}
            else:
                ride["frozen"] = bool(frozen)
        wanted = None
        if cons_item is not None:
            item = get_cons_item(cons_item, live_slot=slot)
            wanted = str(item["id"]) if item else ""
            if wanted and not ride.get("frozen"):
                raise ValueError("Consolidation is available only after freeze.")
            ride["cons_item"] = wanted
        elif not ride.get("frozen"):
            ride["cons_item"] = ""
        toast_given = toast is not None
        if toast is not None:
            ride["toast"] = str(toast or "").strip()
        if toast_key is not None:
            ride["toast_key"] = str(toast_key or "").strip()
        freeze_on = bool(ride.get("frozen")) and not prev_frozen
        new_cons = str(ride.get("cons_item") or "")
        cons_unlock_on = bool(new_cons) and new_cons != prev_cons
        cue_id = None
        if freeze_on:
            if not toast_given:
                ride["toast"] = TOAST_FREEZE
                ride["toast_key"] = "freeze"
            cue_id = CUE_FREEZE
        elif cons_unlock_on:
            if not toast_given:
                ride["toast"] = cons_unlock_toast(slot)
                ride["toast_key"] = "cons_unlock"
            cue_id = CUE_CONS_UNLOCK
        payload = apply_teacher_state_update(
            current,
            live_slot=slot,
            text_ride=ride,
            cue_id=cue_id if cue_id else current.get("cue_id"),
            prompt_ref=new_cons or current.get("prompt_ref"),
        )
        return self._write_teacher_state(session_id, payload)

    def live_session_display_time(self, class_id: int) -> dict[str, Any]:
        """Student-facing SessionTimer snapshot (beat 20).

        Mirrors the teacher SessionTimer: running countdown, paused
        remaining, or idle. Does not invent a second clock source.

        Args:
            class_id: Game-show ``classes.id``.

        Returns:
            ``{running, paused, ends_at_ms, remaining_sec, label}``.
        """
        idle = {
            "running": False,
            "paused": False,
            "ends_at_ms": None,
            "remaining_sec": 0,
            "label": "—",
        }
        try:
            state = self.game.game_state(int(class_id))
        except Exception:  # noqa: BLE001 — no open game is idle
            return idle
        game = state.get("game") or {}
        ends_at_ms = game.get("round_ends_at_ms")
        paused = bool(game.get("timer_paused"))
        remaining = game.get("round_remaining_sec")
        try:
            remaining_i = max(0, int(remaining)) if remaining is not None else 0
        except (TypeError, ValueError):
            remaining_i = 0
        try:
            ends_i = int(ends_at_ms) if ends_at_ms is not None else None
        except (TypeError, ValueError):
            ends_i = None
        running = bool(ends_i) and not paused

        def _label(seconds: int) -> str:
            n = max(0, int(seconds))
            return f"{n // 60}:{n % 60:02d}"

        if running:
            return {
                "running": True,
                "paused": False,
                "ends_at_ms": ends_i,
                "remaining_sec": remaining_i,
                "label": _label(remaining_i),
            }
        if paused:
            return {
                "running": False,
                "paused": True,
                "ends_at_ms": None,
                "remaining_sec": remaining_i,
                "label": _label(remaining_i),
            }
        return idle

    SESSION_TIMER_STAGE_PRESETS: dict[str, int] = {}

    def apply_session_timer_on_stage_advance(
        self, class_id: int, new_stage: str
    ) -> dict[str, Any] | None:
        """Stop the running SessionTimer when the teacher changes stage.

        Every stage now exposes the same opt-in Timer controls. No stage starts
        a countdown until the teacher checks Timer and clicks Start.

        Args:
            class_id: Game-show ``classes.id``.
            new_stage: Destination pedagogical stage.

        Returns:
            Updated game state, or ``None`` when no game exists and
            the destination has no preset to start.
        """
        try:
            self.game.stop_session_timer(int(class_id))
        except Exception:  # noqa: BLE001 — missing game is idle
            pass
        minutes = self.SESSION_TIMER_STAGE_PRESETS.get(
            str(new_stage or "").strip().lower()
        )
        if minutes:
            return self.game.start_session_timer(int(class_id), minutes)
        try:
            return self.game.game_state(int(class_id))
        except Exception:  # noqa: BLE001
            return None

    def live_session_teacher_state_payload(
        self, session_id: int
    ) -> dict[str, Any]:
        """Return the thin LiveTeacherState for staff/student polls.

        Args:
            session_id: ``live_class_sessions.id``.

        Returns:
            Public teacher state. Defaults when the column is empty.

        Raises:
            KeyError: If the live session is missing.
        """
        with self._lock:
            row = self.conn.execute(
                """
                SELECT teacher_state_json
                FROM live_class_sessions
                WHERE id = ?
                """,
                (int(session_id),),
            ).fetchone()
        if row is None:
            raise KeyError(f"live session {session_id}")
        stored: dict[str, Any] = {}
        raw = row["teacher_state_json"]
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                stored = parsed
        state = public_teacher_state(stored)
        teams_exist = bool(self._named_teams_for_live_session(session_id))
        if teams_exist and (
            "groups_configured" not in stored
            or bool(stored.get("groups_configured"))
        ):
            state["groups_configured"] = True
            if "run_as_group" not in stored:
                state["run_as_group"] = True
            if "scoreboard_visible" not in stored:
                state["scoreboard_visible"] = True
        if not state.get("groups_configured"):
            state["run_as_group"] = False
            state["scoreboard_visible"] = False
        state["teams_mode"] = (
            "teams" if state.get("run_as_group") else "individual"
        )
        return state

    def live_class_roster_projection(
        self, session_id: int
    ) -> list[dict[str, Any]]:
        """Return the full roster or the session's Hide Absent subset."""

        session_row = self.get_live_session(session_id)
        if session_row is None:
            raise KeyError(f"live session {session_id}")
        class_id = int(session_row["class_id"])
        teacher = self.live_session_teacher_state_payload(session_id)
        with self.game._lock:
            students = [
                dict(row)
                for row in self.game.conn.execute(
                    """
                    SELECT * FROM students
                    WHERE class_id = ?
                    ORDER BY lower(codename), id
                    """,
                    (class_id,),
                ).fetchall()
            ]
        attendees = {
            int(row["student_id"]): row
            for row in self.list_live_session_attendees(session_id)
            if row.get("student_id") not in (None, "")
        }
        team_by_student: dict[int, dict[str, Any]] = {}
        if teacher.get("run_as_group"):
            for team in self._named_teams_for_live_session(session_id):
                team_public = {
                    "id": int(team["id"]),
                    "name": str(team.get("name") or ""),
                    "color": str(team.get("color") or ""),
                    "sort_order": int(team.get("sort_order") or 0),
                }
                for member in team.get("members") or []:
                    try:
                        team_by_student[int(member["id"])] = team_public
                    except (KeyError, TypeError, ValueError):
                        continue
        rows: list[dict[str, Any]] = []
        for student in students:
            student_id = int(student["id"])
            attendee = attendees.get(student_id)
            present = bool(attendee and not attendee.get("left_at"))
            if teacher.get("hide_absent") and not present:
                continue
            rows.append(
                {
                    "student_id": student_id,
                    "codename": str(
                        student.get("codename")
                        or student.get("first_name")
                        or ""
                    ),
                    "present": present,
                    "joined": attendee is not None,
                    "left_at": attendee.get("left_at") if attendee else None,
                    "mood": student.get("mood"),
                    "character": student.get("character_key"),
                    "team": team_by_student.get(student_id),
                }
            )
        if teacher.get("run_as_group"):
            rows.sort(
                key=lambda item: (
                    int((item.get("team") or {}).get("sort_order") or 99999),
                    str(item.get("codename") or "").casefold(),
                    int(item["student_id"]),
                )
            )
        return rows

    def live_group_projection(self, session_id: int) -> list[dict[str, Any]]:
        """Return named groups only while Run as Group is enabled."""

        teacher = self.live_session_teacher_state_payload(session_id)
        if not teacher.get("run_as_group"):
            return []
        return self._named_teams_for_live_session(session_id)

    def live_scoreboard_projection(
        self, session_id: int
    ) -> dict[str, Any] | None:
        """Return the scoreboard only when both global group flags permit it."""

        session_row = self.get_live_session(session_id)
        if session_row is None:
            raise KeyError(f"live session {session_id}")
        teacher = self.live_session_teacher_state_payload(session_id)
        if not (
            teacher.get("run_as_group")
            and teacher.get("scoreboard_visible")
        ):
            return None
        try:
            return self.game.scoreboard(int(session_row["class_id"]))
        except Exception:  # noqa: BLE001 - setup may not have a live board yet
            return None

    def apply_student_live_group_projection(
        self, payload: dict[str, Any], session_id: int
    ) -> dict[str, Any]:
        """Gate teammate and scoreboard payloads by session-global flags."""

        teacher = self.live_session_teacher_state_payload(session_id)
        run = bool(teacher.get("run_as_group"))
        board_on = bool(teacher.get("scoreboard_visible"))
        groups = self._named_teams_for_live_session(session_id) if run else []
        payload["groups"] = groups
        if not run:
            payload["my_team"] = None
            me = payload.get("me")
            if isinstance(me, dict):
                me["team_name"] = None
                me["team_points"] = 0
        if not (run and board_on):
            payload["scoreboard"] = None
        else:
            current = payload.get("scoreboard")
            named = []
            if isinstance(current, dict):
                named = [
                    team
                    for team in (current.get("teams") or [])
                    if str(team.get("name") or "") != "Class"
                ]
            if not named:
                projected = self.live_scoreboard_projection(session_id)
                if isinstance(projected, dict) and projected.get("teams"):
                    payload["scoreboard"] = projected
                elif groups:
                    payload["scoreboard"] = {
                        "teams": [
                            {
                                "id": team.get("id"),
                                "name": team.get("name"),
                                "color": team.get("color"),
                                "score": team.get("score") or 0,
                            }
                            for team in groups
                        ]
                    }
        payload["group_controls"] = {
            "groups_configured": bool(teacher.get("groups_configured")),
            "run_as_group": run,
            "scoreboard_visible": board_on,
        }
        return payload

    def _write_teacher_state(
        self, session_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist a public LiveTeacherState blob.

        Args:
            session_id: ``live_class_sessions.id``.
            payload: Public teacher-state dict.
        """
        encoded = json.dumps(payload)
        with self._lock:
            self.conn.execute(
                """
                UPDATE live_class_sessions
                SET teacher_state_json = ?
                WHERE id = ?
                """,
                (encoded, int(session_id)),
            )
            self.conn.commit()
        return public_teacher_state(payload)

    def live_session_canvas_sync(self, session_id: int) -> dict[str, Any]:
        """Return the ephemeral canvas-sync blob for one live session.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            raise KeyError(f"live session {session_id}")
        stored = session_row.get("canvas_sync")
        return public_canvas_sync(stored if isinstance(stored, dict) else None)

    def _write_canvas_sync(
        self, session_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist an ephemeral canvas-sync blob.

        Args:
            session_id: ``live_class_sessions.id``.
            payload: Public canvas-sync dict.
        """
        cleaned = public_canvas_sync(payload)
        with self._lock:
            self.conn.execute(
                """
                UPDATE live_class_sessions
                SET canvas_sync_json = ?
                WHERE id = ?
                """,
                (json.dumps(cleaned), int(session_id)),
            )
            self.conn.commit()
        return cleaned

    def student_team_id_for_class(
        self, class_id: int, student_id: int
    ) -> int | None:
        """Assigned team id for a roster student, if teams exist.

        Args:
            class_id: Game-show ``classes.id``.
            student_id: Roster id.
        """
        try:
            state = self.game.game_state(int(class_id))
        except Exception:  # noqa: BLE001
            return None
        for team in state.get("teams") or []:
            if str(team.get("name") or "") == "Class":
                continue
            for member in team.get("members") or []:
                try:
                    if int(member.get("id")) == int(student_id):
                        return int(team.get("id") or 0) or None
                except (TypeError, ValueError):
                    continue
        return None

    def apply_live_canvas_presence(
        self,
        session_id: int,
        *,
        owner: str,
        name: str,
        team_id: int | None = None,
        x: Any = None,
        y: Any = None,
        stroke_id: str | None = None,
        point: Any = None,
        ended: bool = False,
        as_teacher: bool = False,
    ) -> dict[str, Any]:
        """Merge one ephemeral cursor / stroke tick.

        Unique-per-student alignment stores cursors only. Frozen-to-teacher
        publishes teacher strokes. Shared-within-group publishes team
        strokes plus named cursors.

        Args:
            session_id: ``live_class_sessions.id``.
            owner: ``teacher`` or roster id string.
            name: Cursor label.
            team_id: Team bucket when known.
            x: Cursor x in 0–1.
            y: Cursor y in 0–1.
            stroke_id: Stable stroke id while the pointer is down.
            point: Optional ``[x, y]``.
            ended: True when the pointer lifts.
            as_teacher: True for the staff stub.
        """
        teacher = self.live_session_teacher_state_payload(session_id)
        align = str(teacher.get("canvas_align") or "student")
        publish = False
        bucket = "teacher"
        if align == "teacher" and as_teacher:
            publish = True
            bucket = "teacher"
        elif align == "team" and not as_teacher and team_id is not None:
            publish = True
            bucket = "team"
        color = cursor_color_for("teacher" if as_teacher else owner)
        blob = apply_canvas_presence(
            self.live_session_canvas_sync(session_id),
            owner="teacher" if as_teacher else str(owner),
            name=name,
            color=color,
            team_id=team_id,
            x=x,
            y=y,
            stroke_id=stroke_id,
            point=point,
            ended=ended,
            publish_stroke=publish and not ended,
            stroke_bucket=bucket,
        )
        return self._write_canvas_sync(session_id, blob)

    def live_session_canvas_view(
        self,
        session_id: int,
        *,
        student_id: int | None = None,
        as_teacher: bool = False,
    ) -> dict[str, Any]:
        """Filtered strokes/cursors for staff or one student.

        Args:
            session_id: ``live_class_sessions.id``.
            student_id: Roster id when the viewer is a student.
            as_teacher: True for the staff preview (all team buckets).
        """
        teacher = self.live_session_teacher_state_payload(session_id)
        align = str(teacher.get("canvas_align") or "student")
        team_id = None
        if student_id not in (None, "") and not as_teacher:
            session_row = self.get_live_session(session_id)
            if session_row is not None:
                team_id = self.student_team_id_for_class(
                    int(session_row["class_id"]), int(student_id)
                )
        return canvas_view_for(
            self.live_session_canvas_sync(session_id),
            align=align,
            team_id=team_id,
            include_all_teams=bool(as_teacher and align == "team"),
        )

    def _mount_meet_chain(
        self,
        session_id: int,
        payload: dict[str, Any],
        *,
        chain_state: dict[str, Any] | None = None,
        fire_open: bool = False,
    ) -> dict[str, Any]:
        """Mount the visible Meet step on Questions and optionally open-cue.

        Args:
            session_id: ``live_class_sessions.id``.
            payload: In-progress teacher state (mutated).
            chain_state: MeetChainState to persist and activate.
            fire_open: True to set ``cue.meet_open`` (MEET enter only).
        """
        state = public_meet_chain(chain_state) or new_meet_chain_state()
        self.clear_waiting_room_minds_on(session_id)
        self.clear_teams_spark(session_id)
        self.seed_meet_team_warmup(session_id, chain_state=state)
        self.activate_meet_team_question(session_id)
        payload["meet_chain"] = state
        payload["prompt_ref"] = meet_prompt_ref_for(state)
        bind_meet_student_projection(payload)
        if fire_open:
            payload["cue_id"] = CUE_MEET_OPEN
        return payload

    def _wipe_meet_chain(
        self,
        session_id: int,
        payload: dict[str, Any],
        *,
        fire_clear: bool = False,
    ) -> dict[str, Any]:
        """Drop ephemeral Meet picks / prompt and optionally clear-cue.

        Args:
            session_id: ``live_class_sessions.id``.
            payload: In-progress teacher state (mutated).
            fire_clear: True to set ``cue.meet_clear`` (MEET exit only).
        """
        self.clear_meet_team_warmup(session_id)
        payload["meet_chain"] = None
        payload["prompt_ref"] = None
        if fire_clear:
            payload["cue_id"] = CUE_MEET_CLEAR
        return payload

    def set_live_session_teacher_state(
        self,
        session_id: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Patch the thin teacher shell channel for one live session.

        References ``active_media`` / prompts by id. Does not copy those
        payloads. ``canvas_ephemeral`` stays true. Rising edge into
        ``meet`` mounts the A→C→B chain and fires ``cue.meet_open``;
        leaving MEET wipes ephemeral picks and fires ``cue.meet_clear``.
        ``meet_action`` is ``next`` / ``skip_c`` / ``clear``. Optional
        ``assign`` on TEAMS→MEET commits roster teams before the same
        ``state_seq`` write; count ``< 2`` skips assign. JOIN Reveal
        commits ``reveal_to_students`` and closes the waiting-room poll
        so students see the class summary (Wonder stays silent).
        JOIN→TEAMS closes Minds-On, binds the shared spark, and fires
        ``cue.teams_spark`` once. TEAMS→MEET clears the spark.

        Args:
            session_id: ``live_class_sessions.id``.
            **kwargs: Fields accepted by ``apply_teacher_state_update``
                plus optional ``meet_action`` and ``assign``.

        Returns:
            Updated public teacher state.

        Raises:
            KeyError: If the live session is missing.
            ValueError: Invalid stage, tab, preset, frames, meet_action,
                or assign payload.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            raise KeyError(f"live session {session_id}")
        if session_row.get("status") != "active":
            raise ValueError("Session is not active.")
        posted_slot = kwargs.get("live_slot")
        posted_module = kwargs.get("live_module")
        assign = kwargs.pop("assign", None)
        meet_action = kwargs.pop("meet_action", None)
        if meet_action is not None:
            token = str(meet_action).strip().lower()
            if token not in {"next", "skip_c", "clear", "reset_a"}:
                raise ValueError("meet_action must be next, skip_c, clear, or reset_a")
            meet_action = token
        current = self.live_session_teacher_state_payload(session_id)
        prev_stage = str(current.get("stage") or "")
        payload = apply_teacher_state_update(current, **kwargs)
        new_stage = str(payload.get("stage") or "")
        entering = new_stage == "meet" and prev_stage != "meet"
        entering_teams = new_stage == "teams" and prev_stage != "teams"
        leaving_teams = prev_stage == "teams" and new_stage != "teams"
        if entering_teams:
            if "cue_id" not in kwargs:
                payload["cue_id"] = CUE_TEAMS_SPARK
        if new_stage == "join" and prev_stage != "join":
            self.activate_join_minds_on(session_id)
        if leaving_teams and new_stage != "join":
            self.clear_teams_spark(session_id)
        if leaving_teams and new_stage not in {"join", "teams"}:
            self.clear_waiting_room_minds_on(session_id)
        if assign is not None:
            assigned = self._assign_teams_for_meet_advance(session_id, assign)
            if assigned is not None:
                payload["groups_configured"] = True
                payload["run_as_group"] = True
                payload["scoreboard_visible"] = True
                payload["teams_mode"] = "teams"
        if new_stage == "summary" and prev_stage != "summary":
            payload["winner"] = self.snapshot_live_winner(int(session_row["class_id"]))
            payload["scoreboard_visible"] = True
        leaving = prev_stage == "meet" and new_stage != "meet"
        if meet_action == "clear":
            payload["stage"] = "round"
            new_stage = "round"
            leaving = True
            entering = False
        if entering:
            self._mount_meet_chain(
                session_id,
                payload,
                chain_state=new_meet_chain_state(),
                fire_open="cue_id" not in kwargs,
            )
            try:
                self.game.start_meet_teams(int(session_row["class_id"]), 3)
            except (KeyError, ValueError):
                pass
        elif leaving:
            self._wipe_meet_chain(
                session_id,
                payload,
                fire_clear="cue_id" not in kwargs,
            )
        elif new_stage == "meet" and meet_action == "reset_a":
            self._mount_meet_chain(
                session_id,
                payload,
                chain_state=new_meet_chain_state(),
                fire_open=False,
            )
        elif new_stage == "meet" and meet_action in {"next", "skip_c"}:
            state = public_meet_chain(payload.get("meet_chain"))
            if state is None:
                state = new_meet_chain_state()
            if meet_action == "skip_c":
                state = skip_meet_c(state)
            else:
                state = advance_meet_chain(state)
            self._mount_meet_chain(
                session_id, payload, chain_state=state, fire_open=False
            )
        written = self._write_teacher_state(session_id, payload)
        flags = written.get("round_flags") or {}
        posted_round = "round_flags" in kwargs or "round" in kwargs
        if written.get("stage") == "play" and (
            new_stage == "play" or posted_round
        ):
            if flags.get("consolidation"):
                self.mount_consolidation_pack(session_id)
            elif flags.get("action"):
                self.mount_action_pack(session_id)
        advance = str(kwargs.get("advance") or "").strip().lower()
        if advance == "next" and new_stage and new_stage != prev_stage:
            self.apply_session_timer_on_stage_advance(
                int(session_row["class_id"]), new_stage
            )
        if posted_slot is not None or posted_module is not None:
            prev_slot = normalize_live_slot(current.get("live_slot"))
            prev_module = normalize_live_module(current.get("live_module"))
            new_slot = normalize_live_slot(
                posted_slot if posted_slot is not None else payload.get("live_slot")
            )
            new_module = normalize_live_module(
                posted_module
                if posted_module is not None
                else payload.get("live_module")
            )
            if new_slot != prev_slot or new_module != prev_module:
                self.ensure_live_class_media(session_id)
            if not self._session_left_waiting_room(session_id) and not self.schema_v2_owns_live_stage_questions(session_id):
                self.ensure_waiting_room_minds_on(session_id)
        return written

    def _assign_teams_for_meet_advance(
        self, session_id: int, assign: Any
    ) -> dict[str, Any] | None:
        """Commit a one-time Balanced/Random/Manual group assignment.

        Count ``< 2`` is the individual path. Assignment is independent
        from stage navigation and the session timer; callers may set up
        groups from any stage without implicitly starting MEET.

        Args:
            session_id: ``live_class_sessions.id``.
            assign: ``{n_teams, mode, present_ids?, assignments?}``.

        Returns:
            Game state after assign, or ``None`` when count is below 2.

        Raises:
            KeyError: If the live session is missing.
            ValueError: Missing present students or an invalid payload.
        """
        if not isinstance(assign, dict):
            raise ValueError("assign must be an object")
        session_row = self.get_live_session(session_id)
        if session_row is None:
            raise KeyError(f"live session {session_id}")
        class_id = int(session_row["class_id"])
        try:
            n_teams = int(assign.get("n_teams") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("n_teams must be an integer") from exc
        if n_teams < 2:
            return None
        raw_present = assign.get("present_ids") or []
        if not isinstance(raw_present, list):
            raise ValueError("present_ids must be a list")
        present_ids: list[int] = []
        for item in raw_present:
            try:
                present_ids.append(int(item))
            except (TypeError, ValueError) as exc:
                raise ValueError("present_ids must be integers") from exc
        if not present_ids:
            raise ValueError(
                "No students have joined yet. Share the live session code, "
                "then continue when someone is present."
            )
        raw_assignments = assign.get("assignments")
        if raw_assignments is not None and not isinstance(raw_assignments, list):
            raise ValueError("assignments must be a list")
        setup = self.setup_live_session_groups(
            session_id,
            n_teams=n_teams,
            mode=str(assign.get("mode") or "balanced"),
            present_ids=present_ids,
            assignments=raw_assignments,
        )
        return setup["game"]

    def setup_live_session_groups(
        self,
        session_id: int,
        *,
        n_teams: int,
        mode: str,
        present_ids: list[int],
        assignments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create fixed memberships once and enable group projections.

        Args:
            session_id: Active ``live_class_sessions.id``.
            n_teams: Number of named teams.
            mode: ``balanced``, ``random``, or ``manual``.
            present_ids: Roster ids included in initial setup.
            assignments: Manual assignment rows when ``mode=manual``.
        """
        session_row = self._require_active_live_session(session_id)
        current = self.live_session_teacher_state_payload(session_id)
        if current.get("groups_configured") or self._named_teams_for_live_session(
            session_id
        ):
            raise ValueError(
                "Groups are already set up for this session; memberships are fixed."
            )
        ids = sorted({int(value) for value in present_ids})
        if not ids:
            raise ValueError("Mark at least one student present.")
        self.game.save_attendance(int(session_row["class_id"]), ids)
        game = self.game.assign_teams(
            int(session_row["class_id"]),
            int(n_teams),
            str(mode or "balanced"),
            assignments=assignments,
        )
        teacher = apply_teacher_state_update(
            current,
            groups_configured=True,
            run_as_group=True,
            scoreboard_visible=True,
        )
        teacher = self._write_teacher_state(session_id, teacher)
        return {"game": game, "teacher_state": teacher}

    def record_meet_chain_pick(
        self,
        session_id: int,
        *,
        participant_uuid: str = "",
        student_id: int | None = None,
        choice: str,
    ) -> dict[str, Any] | None:
        """Store one ephemeral Meet pick. No gradebook / response row.

        Args:
            session_id: ``live_class_sessions.id``.
            participant_uuid: Live-session person key.
            student_id: Optional roster id fallback.
            choice: Student-facing option text.

        Returns:
            Updated MeetChainState, or ``None`` when Meet is not mounted.
        """
        current = self.live_session_teacher_state_payload(session_id)
        if str(current.get("stage") or "") != "meet":
            return None
        key = meet_participant_key(
            participant_uuid=participant_uuid, student_id=student_id
        )
        updated = record_meet_pick(
            current.get("meet_chain"), participant_key=key, choice=choice
        )
        if updated is None:
            return None
        current["meet_chain"] = updated
        self._write_teacher_state(session_id, current)
        return updated

    def student_meet_chip(
        self,
        session_id: int,
        *,
        participant_uuid: str = "",
        student_id: int | None = None,
    ) -> str | None:
        """Own-avatar A chip for this Meet window, or None.

        Args:
            session_id: ``live_class_sessions.id``.
            participant_uuid: Live-session person key.
            student_id: Optional roster id fallback.
        """
        current = self.live_session_teacher_state_payload(session_id)
        return meet_chip_for(
            current.get("meet_chain"),
            meet_participant_key(
                participant_uuid=participant_uuid, student_id=student_id
            ),
        )

    def _sync_c1_cons_prompt(
        self, session_id: int, media: dict[str, Any] | None
    ) -> None:
        """Compatibility wrapper — CONS sync is slot-aware."""
        self._sync_cons_prompt(session_id, media)

    def _sync_cons_prompt(
        self, session_id: int, media: dict[str, Any] | None
    ) -> None:
        """Push or clear the live-prompt row to match CONS after freeze or SET.

        C1 reads ``cons_item`` from the Real-slice blob. C2/C3 read the
        text-only ``text_ride`` on teacher state (no ``active_media_json``).
        ROUND SET with Consolidation keeps CONS-1 mounted without freeze.

        Args:
            session_id: ``live_class_sessions.id``.
            media: Public active-media payload, or ``None`` when cleared.
        """
        slot = self.session_live_slot(session_id)
        wanted = ""
        if slot == "C1":
            if media and media.get("frozen") and is_c1_real_slice(media):
                wanted = str(media.get("cons_item") or "").strip()
        else:
            ride = self.session_text_ride(session_id)
            if ride.get("frozen"):
                wanted = str(ride.get("cons_item") or "").strip()
        active = self.get_active_live_prompt(session_id)
        if not wanted:
            flags = (
                self.live_session_teacher_state_payload(session_id).get(
                    "round_flags"
                )
                or {}
            )
            if flags.get("consolidation"):
                catalog = cons_catalog(slot)
                if catalog:
                    wanted = str(catalog[0]["id"])
        if not wanted:
            if active and is_cons_payload(active.get("payload")):
                self.clear_active_live_prompt(session_id)
            return
        item = get_cons_item(wanted, live_slot=slot)
        if item is None:
            if active and is_cons_payload(active.get("payload")):
                self.clear_active_live_prompt(session_id)
            return
        current_id = ""
        if active and is_cons_payload(active.get("payload")):
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

    def mount_action_pack(self, session_id: int) -> dict[str, Any] | None:
        """Mount the course/slot team-challenge on the live prompt channel.

        Play + Action uses the MCF3M M1C1 Real-slice stem, or the Lesson
        Slides / live-problem contest for every other course and class.

        Args:
            session_id: ``live_class_sessions.id``.

        Returns:
            The mounted prompt row, or ``None`` when no stem is resolved.
        """
        session_row = self.get_live_session(session_id)
        if session_row is None:
            return None
        class_id = int(session_row["class_id"])
        cls = self.enrich_class(self.game.get_class(class_id))
        ontario = str(cls.get("ontario_code") or cls.get("course_code") or "MCF3M")
        row = resolve_team_challenge(
            self,
            class_id=class_id,
            ontario_code=ontario,
            live_module=self.session_live_module(session_id),
            live_slot=self.session_live_slot(session_id),
        )
        prompt_body = staff_team_challenge_prompt_payload(row)
        if not str(prompt_body.get("prompt") or "").strip():
            return None
        media_url = str(row.get("media_url") or "").strip()
        if media_url:
            media = self.live_session_active_media_payload(session_id)
            if not media or str(media.get("url") or "") != media_url:
                kwargs = {
                    "url": media_url,
                    "title": str(row.get("title") or row.get("question") or ""),
                    "stem": str(row.get("question") or ""),
                }
                if row.get("use_real_slice"):
                    kwargs["challenge"] = "C1"
                self.set_live_session_active_media(session_id, **kwargs)
            self._project_action_media(session_id)
        else:
            media = self.live_session_active_media_payload(session_id)
            if media and is_c1_real_slice(media):
                self.set_live_session_active_media(session_id, clear=True)
        active = self.get_active_live_prompt(session_id)
        if (
            active
            and is_team_challenge_payload(active.get("payload"))
            and (active.get("payload") or {}).get("prompt") == prompt_body["prompt"]
            and (active.get("payload") or {}).get("label") == prompt_body.get("label")
        ):
            return active
        return self.set_live_session_prompt(
            session_id,
            slide_index=TEAM_CHALLENGE_SLIDE_INDEX,
            kind=TEAM_CHALLENGE_KIND,
            payload=prompt_body,
            activate=True,
        )

    def _project_action_media(self, session_id: int) -> None:
        """Show the team-challenge graph on Play without remounting Action.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        current = self.live_session_teacher_state_payload(session_id)
        view = dict(current.get("student_view") or default_student_view("play"))
        view["media"] = "student"
        view["questions"] = "student"
        current["student_view"] = view
        current["unlocks"] = unlocks_from_student_view(view)
        student_frames = dict(current.get("student_frames") or {})
        student_frames["questions"] = True
        student_frames["media"] = True
        current["student_frames"] = student_frames
        current["layout_preset"] = "media_questions"
        current["frames"] = {"A": "media", "B": "questions"}
        current["active_tab"] = "media"
        current["state_seq"] = int(current.get("state_seq") or 0) + 1
        self._write_teacher_state(session_id, current)

    def mount_consolidation_pack(self, session_id: int) -> dict[str, Any] | None:
        """Mount CONS-1 for the session live slot without requiring freeze.

        ROUND SET with Consolidation pops the first CONS item on the live
        prompt channel so teacher and student Question frames show it.

        Args:
            session_id: ``live_class_sessions.id``.

        Returns:
            The mounted prompt row, or ``None`` when the CONS catalog is empty.
        """
        slot = self.session_live_slot(session_id)
        catalog = cons_catalog(slot)
        if not catalog:
            return None
        item = catalog[0]
        return self.set_live_session_prompt(
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

    def cleanup_live_session_response_data(self, session_id: int) -> None:
        """Delete ephemeral individual votes and group response artifacts."""

        with self._lock:
            item_rows = self.conn.execute(
                """
                SELECT id FROM live_session_items
                WHERE live_session_id = ?
                """,
                (int(session_id),),
            ).fetchall()
            item_ids = [int(row["id"]) for row in item_rows]
            if item_ids:
                placeholders = ",".join("?" for _ in item_ids)
                self.conn.execute(
                    f"""
                    DELETE FROM live_group_votes
                    WHERE live_item_id IN ({placeholders})
                    """,
                    item_ids,
                )
                self.conn.execute(
                    f"""
                    DELETE FROM live_group_members
                    WHERE live_item_id IN ({placeholders})
                    """,
                    item_ids,
                )
                self.conn.execute(
                    f"""
                    DELETE FROM live_group_responses
                    WHERE live_item_id IN ({placeholders})
                    """,
                    item_ids,
                )
            self.conn.execute(
                """
                DELETE FROM live_session_responses
                WHERE prompt_id IN (
                    SELECT id FROM live_session_prompts
                    WHERE live_session_id = ?
                )
                """,
                (int(session_id),),
            )
            self.conn.commit()

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
            self.cleanup_live_session_response_data(session_id)
            return self.get_live_session(session_id)
        self.cleanup_live_session_response_data(session_id)
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

    def list_live_sessions_for_class(self, class_id: int) -> list[dict[str, Any]]:
        """Return every live session row for a class (active and ended).

        Args:
            class_id: Game-show ``classes.id``.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM live_class_sessions
                WHERE class_id = ?
                ORDER BY id ASC
                """,
                (int(class_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def wipe_live_sessions_for_class(
        self, class_id: int, session_ids: list[int] | None = None
    ) -> dict[str, Any]:
        """Delete live sessions and their data for a class.

        Teacher Quit / new-run recovery: hung sessions plus leftover
        attendees, prompts, responses, observations, slides, and active
        media are removed so Run Live Class can start clean. Roster,
        gradebook, and lesson-slide decks stay intact.

        Args:
            class_id: Game-show ``classes.id``.
            session_ids: Optional subset. Default is every session.

        Returns:
            ``{ok, class_id, wiped_session_ids, wiped_count}``.
        """
        sessions = self.list_live_sessions_for_class(class_id)
        if session_ids is None:
            session_ids = [int(row["id"]) for row in sessions]
        else:
            wanted = {int(sid) for sid in session_ids}
            session_ids = [
                int(row["id"]) for row in sessions if int(row["id"]) in wanted
            ]
        for sid in session_ids:
            self.clear_attendee_moods_and_characters(sid)
        try:
            self.game.clear_class_moods_and_characters(int(class_id))
        except Exception:  # noqa: BLE001 — roster moods are best-effort
            pass
        with self._lock:
            if session_ids:
                placeholders = ",".join("?" * len(session_ids))
                prompt_rows = self.conn.execute(
                    f"""
                    SELECT id FROM live_session_prompts
                    WHERE live_session_id IN ({placeholders})
                    """,
                    session_ids,
                ).fetchall()
                prompt_ids = [int(row["id"]) for row in prompt_rows]
                if prompt_ids:
                    prompt_ph = ",".join("?" * len(prompt_ids))
                    self.conn.execute(
                        f"""
                        DELETE FROM live_session_responses
                        WHERE prompt_id IN ({prompt_ph})
                        """,
                        prompt_ids,
                    )
                self.conn.execute(
                    f"""
                    DELETE FROM live_session_prompts
                    WHERE live_session_id IN ({placeholders})
                    """,
                    session_ids,
                )
                obs_rows = self.conn.execute(
                    f"""
                    SELECT id FROM observations
                    WHERE live_session_id IN ({placeholders})
                    """,
                    session_ids,
                ).fetchall()
                obs_ids = [int(row["id"]) for row in obs_rows]
                if obs_ids:
                    obs_ph = ",".join("?" * len(obs_ids))
                    self.conn.execute(
                        f"""
                        DELETE FROM observation_subjects
                        WHERE observation_id IN ({obs_ph})
                        """,
                        obs_ids,
                    )
                    self.conn.execute(
                        f"""
                        DELETE FROM observation_processes
                        WHERE observation_id IN ({obs_ph})
                        """,
                        obs_ids,
                    )
                self.conn.execute(
                    f"""
                    DELETE FROM observations
                    WHERE live_session_id IN ({placeholders})
                    """,
                    session_ids,
                )
                self.conn.execute(
                    f"""
                    DELETE FROM live_session_attendees
                    WHERE live_session_id IN ({placeholders})
                    """,
                    session_ids,
                )
            self.conn.execute(
                "DELETE FROM live_class_sessions WHERE class_id = ?",
                (int(class_id),),
            )
            self.conn.execute(
                """
                DELETE FROM live_class_feedback
                WHERE class_id = ? AND submitted_at IS NULL
                """,
                (int(class_id),),
            )
            self.conn.commit()
        return {
            "ok": True,
            "class_id": int(class_id),
            "wiped_session_ids": session_ids,
            "wiped_count": len(session_ids),
        }

    def present_student_ids_for_live_class(self, class_id: int) -> list[int]:
        """Roster ids who joined any live session for this class.

        Args:
            class_id: Game-show ``classes.id``.

        Returns:
            Distinct student ids (may be empty).
        """
        sessions = self.list_live_sessions_for_class(class_id)
        session_ids = [int(row["id"]) for row in sessions]
        found: list[int] = []
        if session_ids:
            placeholders = ",".join("?" * len(session_ids))
            with self._lock:
                rows = self.conn.execute(
                    f"""
                    SELECT DISTINCT student_id
                    FROM live_session_attendees
                    WHERE live_session_id IN ({placeholders})
                      AND student_id IS NOT NULL
                    """,
                    session_ids,
                ).fetchall()
            found = [int(row["student_id"]) for row in rows]
        if found:
            return found
        try:
            state = self.game.game_state(int(class_id))
        except Exception:  # noqa: BLE001
            return []
        return [
            int(row["id"])
            for row in (state.get("students") or [])
            if row.get("present")
        ]

    def _qh_credit_key(self, payload: dict[str, Any], kind: str) -> str | None:
        """Participation key for one answered question. Meet social is None.

        Pure Meet A/B/C taps are excluded. Real QH (Minds-On, teams spark,
        CONS items, other non-Meet MC) each keep their own ``item_id``.
        Team-shared questions are filtered later — this key is only for
        Student: Individual answers.

        Args:
            payload: Prompt payload JSON.
            kind: ``live_session_prompts.kind``.

        Returns:
            Question key, or ``None`` to exclude the prompt.
        """
        if is_meet_team_payload(payload):
            return None
        item = str(payload.get("item_id") or "").strip()
        lowered = item.lower()
        if lowered in {"meet-team", "meet-a", "meet-b", "meet-c"} or (
            lowered.startswith("meet-") and not is_teams_spark_payload(payload)
        ):
            if not is_minds_on_payload(payload) and not is_teams_spark_payload(
                payload
            ):
                return None
        if is_teams_spark_payload(payload):
            return item or "teams-spark"
        if item:
            return item
        token = str(kind or "").strip().lower()
        if token and token not in {"idle", "meet", MEET_TEAM_KIND}:
            return token
        return None

    def _mc_round_key(self, payload: dict[str, Any], kind: str) -> str | None:
        """Backward-compatible alias for ``_qh_credit_key``."""
        return self._qh_credit_key(payload, kind)

    def participation_question_credits_for_class(
        self, class_id: int
    ) -> dict[int, int]:
        """Map student id → distinct individual QH answers this live class.

        Meet taps are excluded. Shared-within-Group answers do not
        auto-score participation game points — only Student: Individual.

        Args:
            class_id: Game-show ``classes.id``.

        Returns:
            One count per student who answered a student-individual QH.
        """
        sessions = self.list_live_sessions_for_class(class_id)
        session_ids = [int(row["id"]) for row in sessions]
        if not session_ids:
            return {}
        placeholders = ",".join("?" * len(session_ids))
        with self._lock:
            rows = self.conn.execute(
                f"""
                SELECT r.student_id, r.prompt_id, r.response_json,
                       p.kind, p.payload, p.live_session_id
                FROM live_session_responses r
                JOIN live_session_prompts p ON p.id = r.prompt_id
                WHERE p.live_session_id IN ({placeholders})
                  AND r.student_id IS NOT NULL
                """,
                session_ids,
            ).fetchall()
        session_modes: dict[int, str] = {}
        keys: dict[int, set[str]] = {}
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            try:
                answer = json.loads(row["response_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                answer = {}
            if not isinstance(answer, dict):
                answer = {}
            view = str(answer.get("question_view") or "").strip().lower()
            if view not in {"none", "student", "team"}:
                live_id = int(row["live_session_id"])
                if live_id not in session_modes:
                    session_modes[live_id] = self._questions_view_mode(live_id)
                view = session_modes[live_id]
            if view != "student":
                continue
            key = self._qh_credit_key(payload, str(row["kind"] or ""))
            if key is None:
                continue
            sid = int(row["student_id"])
            keys.setdefault(sid, set()).add(f"{int(row['prompt_id'])}:{key}")
        return {sid: len(seen) for sid, seen in keys.items()}

    def participation_round_credits_for_class(
        self, class_id: int
    ) -> dict[int, int]:
        """Map student id → QH counts (alias used by Save and End Class)."""
        return self.participation_question_credits_for_class(class_id)

    def live_awarded_session_points(self, class_id: int) -> dict[int, int]:
        """Map student id → live game points from teacher awards only.

        Args:
            class_id: Game-show ``classes.id``.
        """
        try:
            state = self.game.game_state(int(class_id))
        except Exception:  # noqa: BLE001 — no open game yet
            return {}
        out: dict[int, int] = {}
        for row in state.get("students") or []:
            sid = row.get("id")
            if sid in (None, ""):
                continue
            raw = row.get("session_points")
            if raw in (None, ""):
                raw = row.get("points")
            try:
                points = int(raw)
            except (TypeError, ValueError):
                continue
            if points:
                out[int(sid)] = points
        return out

    def sync_live_participation_scores(self, class_id: int) -> dict[str, Any] | None:
        """Push live QH counts onto the open game so the scoreboard updates.

        Args:
            class_id: Game-show ``classes.id``.
        """
        credits = self.participation_question_credits_for_class(int(class_id))
        try:
            return self.game.write_live_participation(int(class_id), credits)
        except Exception:  # noqa: BLE001 — live paint must not fail the answer
            return None

    EXIT_FEEDBACK_MOODS = ("good", "ok", "low")

    def open_exit_feedback_for_class(self, class_id: int) -> int:
        """Mint pending How-was-class rows for current live attendees.

        Must run before the SID wipe so student identity still exists.
        Tokens stay for End Live Class celebration; Quit deletes
        unsubmitted rows with the SID.

        Args:
            class_id: Game-show ``classes.id``.

        Returns:
            Number of new pending rows inserted.
        """
        live = self.get_active_live_session_for_class(int(class_id))
        if live is None:
            return 0
        session_id = int(live["id"])
        offering_id = live.get("offering_id")
        meeting_date = str(live.get("meeting_date") or "")[:10] or None
        attendees = self.list_live_session_attendees(session_id)
        now = _now()
        live_module = "M1"
        live_slot = "C1"
        try:
            teacher = self.live_session_teacher_state_payload(session_id)
            live_module = str(teacher.get("live_module") or "M1").upper()
            live_slot = str(teacher.get("live_slot") or "C1").upper()
        except KeyError:
            pass
        inserted = 0
        with self._lock:
            for row in attendees:
                student_id = row.get("student_id")
                participant_uuid = str(row.get("participant_uuid") or "").strip()
                existing = self.conn.execute(
                    """
                    SELECT id FROM live_class_feedback
                    WHERE class_id = ?
                      AND COALESCE(student_id, 0) = COALESCE(?, 0)
                      AND COALESCE(participant_uuid, '') = COALESCE(?, '')
                      AND submitted_at IS NULL
                    LIMIT 1
                    """,
                    (int(class_id), student_id, participant_uuid),
                ).fetchone()
                if existing is not None:
                    continue
                before_mood = None
                if student_id not in (None, ""):
                    try:
                        before_mood = self.game.get_mood(
                            int(class_id), int(student_id), meeting_date
                        )
                    except Exception:  # noqa: BLE001 — missing mood is fine
                        before_mood = None
                token = secrets.token_urlsafe(18)
                self.conn.execute(
                    """
                    INSERT INTO live_class_feedback (
                        class_id, offering_id, student_id, participant_uuid,
                        codename, meeting_date, token, mood, before_mood,
                        live_module, live_slot, comment,
                        submitted_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, ?)
                    """,
                    (
                        int(class_id),
                        int(offering_id) if offering_id not in (None, "") else None,
                        int(student_id) if student_id not in (None, "") else None,
                        participant_uuid or None,
                        str(row.get("codename") or ""),
                        meeting_date,
                        token,
                        before_mood,
                        live_module,
                        live_slot,
                        now,
                    ),
                )
                inserted += 1
            if inserted:
                self.conn.commit()
        return inserted

    def pending_exit_feedback(
        self,
        *,
        class_id: int | None = None,
        student_id: int | None = None,
        participant_uuid: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the latest unsubmitted exit-feedback row for this student.

        Args:
            class_id: Optional game-show ``classes.id``.
            student_id: Roster id when the student is matched.
            participant_uuid: Live-session person key for guests.
        """
        clauses = ["submitted_at IS NULL"]
        args: list[Any] = []
        if class_id not in (None, ""):
            clauses.append("class_id = ?")
            args.append(int(class_id))
        if student_id not in (None, ""):
            clauses.append("student_id = ?")
            args.append(int(student_id))
        elif str(participant_uuid or "").strip():
            clauses.append("participant_uuid = ?")
            args.append(str(participant_uuid).strip())
        else:
            return None
        sql = f"""
            SELECT * FROM live_class_feedback
            WHERE {' AND '.join(clauses)}
            ORDER BY id DESC
            LIMIT 1
        """
        with self._lock:
            row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def get_exit_feedback_by_token(self, token: str) -> dict[str, Any] | None:
        """Return one exit-feedback row by opaque token.

        Args:
            token: ``live_class_feedback.token``.
        """
        cleaned = str(token or "").strip()
        if not cleaned:
            return None
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM live_class_feedback WHERE token = ? LIMIT 1",
                (cleaned,),
            ).fetchone()
        return dict(row) if row else None

    def submit_exit_feedback(
        self,
        token: str,
        *,
        mood: str | None = None,
        comment: str = "",
        skip: bool = False,
    ) -> dict[str, Any]:
        """Persist How-was-class mood and optional comment.

        Skip records ``submitted_at`` without a mood so the student is
        not prompted again. Quit and Save and End Class both keep these
        rows (they live outside the wiped live SID).

        Args:
            token: Opaque row token from the student session.
            mood: ``good`` / ``ok`` / ``low`` when not skipping.
            comment: Optional free text.
            skip: True when the student declines to rate.

        Returns:
            The updated row.

        Raises:
            KeyError: Unknown token.
            ValueError: Mood missing or not a check-in face.
        """
        row = self.get_exit_feedback_by_token(token)
        if row is None:
            raise KeyError("unknown exit feedback token")
        if row.get("submitted_at"):
            return row
        cleaned_mood = None
        if not skip:
            cleaned_mood = str(mood or "").strip().lower()
            if cleaned_mood not in self.EXIT_FEEDBACK_MOODS:
                raise ValueError("Choose a face or skip.")
        note = str(comment or "").strip()[:2000]
        now = _now()
        with self._lock:
            self.conn.execute(
                """
                UPDATE live_class_feedback
                SET mood = ?, comment = ?, submitted_at = ?
                WHERE token = ?
                """,
                (cleaned_mood, note, now, str(token).strip()),
            )
            self.conn.commit()
        updated = self.get_exit_feedback_by_token(token)
        assert updated is not None
        return updated

    def list_exit_feedback_for_class(self, class_id: int) -> list[dict[str, Any]]:
        """Submitted How-was-class rows for the staff Feedback tab.

        Args:
            class_id: Game-show ``classes.id``.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM live_class_feedback
                WHERE class_id = ? AND submitted_at IS NOT NULL
                ORDER BY submitted_at DESC, id DESC
                """,
                (int(class_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    MOOD_RANK = {"low": 0, "ok": 1, "good": 2}

    def mood_jump_score(self, before: str | None, after: str | None) -> int | None:
        """+1 per mood step up, −1 per step down. None when either face is missing."""
        left = self.MOOD_RANK.get(str(before or "").strip().lower())
        right = self.MOOD_RANK.get(str(after or "").strip().lower())
        if left is None or right is None:
            return None
        return int(right) - int(left)

    def feedback_grid_for_class(self, class_id: int) -> dict[str, Any]:
        """Roster table grouped by live class (M1C1) with before/after mood.

        Rows include every rostered student. Comment text stays off the
        grid; cells only flag a pop-out when a note exists.

        Args:
            class_id: Game-show ``classes.id``.
        """
        try:
            roster = list(self.game.dashboard(int(class_id), sort="az").get("students") or [])
        except Exception:  # noqa: BLE001 — empty roster still shows submitted guests
            roster = []
        submitted = self.list_exit_feedback_for_class(int(class_id))
        columns: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in submitted:
            module = str(row.get("live_module") or "M1").upper()
            slot = str(row.get("live_slot") or "C1").upper()
            key = f"{module}{slot}"
            if key in seen:
                continue
            seen.add(key)
            columns.append({"key": key, "module": module, "slot": slot})
        columns.sort(key=lambda col: (col["module"], col["slot"]))
        by_student: dict[str, dict[str, Any]] = {}
        extras: list[dict[str, Any]] = []
        for row in submitted:
            module = str(row.get("live_module") or "M1").upper()
            slot = str(row.get("live_slot") or "C1").upper()
            key = f"{module}{slot}"
            sid = row.get("student_id")
            identity = f"s:{sid}" if sid not in (None, "") else f"g:{row.get('participant_uuid')}"
            bucket = by_student.setdefault(
                identity,
                {
                    "student_id": sid,
                    "codename": str(row.get("codename") or "Student"),
                    "cells": {},
                    "guest": sid in (None, ""),
                },
            )
            if sid in (None, "") and identity not in {f"s:{r.get('id')}" for r in roster}:
                extras.append(bucket)
            score = self.mood_jump_score(row.get("before_mood"), row.get("mood"))
            bucket["cells"][key] = {
                "before_mood": row.get("before_mood"),
                "after_mood": row.get("mood"),
                "score": score,
                "has_comment": bool(str(row.get("comment") or "").strip()),
                "comment": str(row.get("comment") or ""),
            }
        students: list[dict[str, Any]] = []
        for person in roster:
            sid = person.get("id")
            identity = f"s:{sid}"
            bucket = by_student.get(identity) or {
                "student_id": sid,
                "codename": str(person.get("codename") or person.get("name") or "Student"),
                "cells": {},
                "guest": False,
            }
            total = 0
            has_score = False
            for cell in bucket["cells"].values():
                if cell.get("score") is not None:
                    total += int(cell["score"])
                    has_score = True
            bucket["total"] = total if has_score else 0
            students.append(bucket)
        for bucket in extras:
            if bucket in students:
                continue
            total = 0
            for cell in bucket["cells"].values():
                if cell.get("score") is not None:
                    total += int(cell["score"])
            bucket["total"] = total
            students.append(bucket)
        totals = {col["key"]: 0 for col in columns}
        for person in students:
            for key, cell in (person.get("cells") or {}).items():
                if key in totals and cell.get("score") is not None:
                    totals[key] += int(cell["score"])
        return {
            "columns": columns,
            "students": students,
            "totals": totals,
            "grand_total": sum(totals.values()),
        }

    def parse_live_class_feedback_key(self, raw: Any) -> tuple[str, str]:
        """Split a Feedback column key such as ``M1C1`` into module and slot.

        Args:
            raw: ``M1C1`` / ``m12c3`` style key.

        Returns:
            Uppercased ``(module, slot)``.

        Raises:
            ValueError: Missing or malformed key.
        """
        key = str(raw or "").strip().upper()
        match = re.fullmatch(r"(M\d+)(C\d+)", key)
        if match is None:
            raise ValueError("live class is required (e.g. M1C1)")
        return match.group(1), match.group(2)

    def clear_live_class_feedback(self, class_id: int, live_key: str) -> dict[str, Any]:
        """Delete How-was-class rows for one live class column.

        Removes submitted and pending rows for that module + slot so the
        Feedback sheet no longer shows the column.

        Args:
            class_id: Game-show ``classes.id``.
            live_key: Column key such as ``M1C1``.

        Returns:
            Updated ``feedback_grid_for_class`` payload plus ``deleted``.
        """
        module, slot = self.parse_live_class_feedback_key(live_key)
        with self._lock:
            cursor = self.conn.execute(
                """
                DELETE FROM live_class_feedback
                WHERE class_id = ?
                  AND UPPER(COALESCE(NULLIF(TRIM(live_module), ''), 'M1')) = ?
                  AND UPPER(COALESCE(NULLIF(TRIM(live_slot), ''), 'C1')) = ?
                """,
                (int(class_id), module, slot),
            )
            deleted = int(cursor.rowcount or 0)
            self.conn.commit()
        grid = self.feedback_grid_for_class(int(class_id))
        grid["deleted"] = deleted
        grid["cleared_key"] = f"{module}{slot}"
        return grid

    def session_is_celebrating(self, session_id: int) -> bool:
        """True when End Live Class left the SID up for student celebration."""
        try:
            teacher = self.live_session_teacher_state_payload(int(session_id))
        except KeyError:
            return False
        return bool(teacher.get("celebrate"))

    def _session_open_for_student(self, session_row: dict[str, Any] | None) -> bool:
        """True when students may still poll this live session.

        Active classes stay open. Ended celebrating sessions stay readable
        so the scoreboard and How-was-class overlay remain on student home.
        """
        if session_row is None:
            return False
        status = str(session_row.get("status") or "")
        if status == "active":
            return True
        if status == "ended":
            return self.session_is_celebrating(int(session_row["id"]))
        return False


    def snapshot_live_winner(self, class_id: int) -> dict[str, Any]:
        """Winning team at End Live, or Class when no named teams exist.

        Args:
            class_id: Game-show ``classes.id``.
        """
        teams: list[Any] = []
        try:
            teams = list((self.game.scoreboard(int(class_id)) or {}).get("teams") or [])
        except Exception:  # noqa: BLE001 — ended games still resolve below
            teams = []
        if not teams:
            try:
                with self.game._lock:
                    row = self.game.conn.execute(
                        """
                        SELECT id FROM games
                        WHERE class_id = ?
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (int(class_id),),
                    ).fetchone()
                if row is not None:
                    state = self.game.game_state(int(class_id), game_id=int(row["id"]))
                    teams = list(state.get("teams") or [])
            except Exception:  # noqa: BLE001 — Class fallback
                teams = []
        named = [row for row in teams if str(row.get("name") or "") != "Class"]
        pool = named or teams
        if pool:
            winner = max(pool, key=lambda row: float(row.get("score") or 0))
            name = str(winner.get("name") or "").strip() or "Class"
            players = []
            for row in winner.get("players") or winner.get("members") or []:
                if not isinstance(row, dict):
                    continue
                label = str(row.get("codename") or "").strip()
                if not label:
                    first = str(row.get("first_name") or "").strip()
                    last = str(row.get("last_name") or "").strip()
                    label = f"{first} {last}".strip()
                if not label:
                    label = str(row.get("name") or "").strip()
                if not label:
                    continue
                players.append(
                    {
                        "name": label[:80],
                        "codename": str(row.get("codename") or "").strip()[:80],
                        "first_name": str(row.get("first_name") or label).strip()[:80],
                    }
                )
            return {"name": name, "score": winner.get("score"), "players": players}
        return {"name": "Class", "score": None, "players": []}

    def close_live_class_for_celebration(
        self,
        class_id: int,
        winner: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """End the live SID for staff but keep student celebration + feedback.

        Marks the session ended so new joins fail, writes ``celebrate`` on
        teacher state, and leaves attendees in place. Quit still wipes.

        Args:
            class_id: Game-show ``classes.id``.
            winner: Optional pre-persist winning team snapshot.
        """
        live = self.get_active_live_session_for_class(int(class_id))
        if live is None:
            return {"ok": True, "class_id": int(class_id), "celebrating": False}
        session_id = int(live["id"])
        self.open_exit_feedback_for_class(int(class_id))
        try:
            payload = self.live_session_teacher_state_payload(session_id)
        except KeyError:
            payload = {"stage": "play"}
        payload["celebrate"] = True
        snap = winner if isinstance(winner, dict) else self.snapshot_live_winner(int(class_id))
        name = str((snap or {}).get("name") or "").strip() or "Class"
        payload["winner"] = {
            "name": name[:80],
            "score": (snap or {}).get("score"),
            "players": list((snap or {}).get("players") or []),
        }
        self._write_teacher_state(session_id, payload)
        self.cleanup_live_session_response_data(session_id)
        now = _now()
        with self._lock:
            self.conn.execute(
                """
                UPDATE live_class_sessions
                SET status = 'ended', ended_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (now, session_id),
            )
            self.conn.commit()
        return {
            "ok": True,
            "class_id": int(class_id),
            "celebrating": True,
            "session_id": session_id,
        }

    def student_end_class_overlay(
        self,
        session_id: int,
        class_id: int,
        student_id: int | None = None,
        participant_uuid: str = "",
    ) -> dict[str, Any]:
        """Scoreboard celebration + pending How-was-class for student home.

        Args:
            session_id: ``live_class_sessions.id``.
            class_id: Game-show ``classes.id``.
            student_id: Optional roster id.
            participant_uuid: Live-session person key.
        """
        if not self.session_is_celebrating(int(session_id)):
            return {"celebrate": False}
        winner = None
        try:
            teacher = self.live_session_teacher_state_payload(int(session_id))
            stored = teacher.get("winner")
            if isinstance(stored, dict) and str(stored.get("name") or "").strip():
                winner = stored
        except Exception:  # noqa: BLE001 — fall through to scoreboard
            winner = None
        if winner is None:
            winner = self.snapshot_live_winner(int(class_id))
        pending = self.pending_exit_feedback(
            class_id=int(class_id),
            student_id=student_id,
            participant_uuid=participant_uuid,
        )
        return {
            "celebrate": True,
            "winner": {
                "name": str((winner or {}).get("name") or "Class"),
                "score": (winner or {}).get("score"),
                "players": list((winner or {}).get("players") or []),
            },
            "exit_feedback": {
                "pending": bool(pending),
                "token": pending.get("token") if pending else None,
            },
        }

    def apply_student_summary_winner(
        self,
        payload: dict[str, Any],
        session_id: int,
        class_id: int,
    ) -> dict[str, Any]:
        """Attach the winning team graphic while the Summary page is live.

        Args:
            payload: Student home / ``/api/student/state`` dict.
            session_id: ``live_class_sessions.id``.
            class_id: Game-show ``classes.id``.
        """
        teacher = payload.get("teacher_state")
        if not isinstance(teacher, dict):
            try:
                teacher = self.live_session_teacher_state_payload(int(session_id))
            except KeyError:
                teacher = {}
        if str((teacher or {}).get("stage") or "") != "summary":
            return payload
        stored = (teacher or {}).get("winner")
        snap = stored if isinstance(stored, dict) and stored.get("name") else None
        if snap is None:
            snap = self.snapshot_live_winner(int(class_id))
        payload["summary_winner"] = True
        payload["winner"] = {
            "name": str((snap or {}).get("name") or "Class")[:80],
            "score": (snap or {}).get("score"),
            "players": list((snap or {}).get("players") or []),
        }
        return payload

    def apply_student_end_overlay(
        self,
        payload: dict[str, Any],
        session_id: int,
        class_id: int,
        student_id: int | None,
        participant_uuid: str = "",
    ) -> dict[str, Any]:
        """Merge celebration, winner, and ended-game points onto student state.

        Args:
            payload: Student home / ``/api/student/state`` dict.
            session_id: ``live_class_sessions.id``.
            class_id: Game-show ``classes.id``.
            student_id: Optional roster id.
            participant_uuid: Live-session person key.
        """
        overlay = self.student_end_class_overlay(
            int(session_id),
            int(class_id),
            int(student_id) if student_id not in (None, "") else None,
            participant_uuid,
        )
        payload.update(overlay)
        if not overlay.get("celebrate"):
            self.apply_student_summary_winner(payload, session_id, class_id)
        if overlay.get("celebrate") and student_id not in (None, ""):
            ended = self.game.student_live_payload(
                int(class_id),
                int(student_id),
                include_ended=True,
            )
            if ended.get("me"):
                payload["me"] = ended["me"]
            if ended.get("scoreboard"):
                payload["scoreboard"] = ended["scoreboard"]
        return payload

    def finish_live_class(
        self,
        class_id: int,
        *,
        persist: bool | None = None,
        save_attendance: bool = True,
        save_participation: bool = True,
        celebrate: bool | None = None,
    ) -> dict[str, Any]:
        """End Live Class (keep student celebration) or Quit (wipe).

        The End Live Class dialog can save attendance, participation,
        both, or neither. Students keep the scoreboard, winner graphic,
        and How-was-class until Quit wipes the SID.

        Args:
            class_id: Game-show ``classes.id``.
            persist: Legacy Quit/Save flag. When set, both attendance
                and participation follow this value (Quit is ``False``).
            save_attendance: Write the attendance column.
            save_participation: Write +1/question participation.
            celebrate: True for End Live Class; False for Quit. Default
                follows ``persist`` (Quit is ``persist=False``).

        Returns:
            Celebration payload or wipe payload.
        """
        if persist is not None:
            save_attendance = bool(persist)
            save_participation = bool(persist)
        if celebrate is None:
            celebrate = persist is not False
        present_ids = self.present_student_ids_for_live_class(class_id)
        winner = self.snapshot_live_winner(int(class_id)) if celebrate else None
        credits = (
            self.participation_round_credits_for_class(class_id)
            if save_participation
            else {}
        )
        wrote = None
        if save_attendance or save_participation:
            try:
                wrote = self.game.persist_end_class_column(
                    int(class_id),
                    present_ids,
                    credits,
                    include_attendance=bool(save_attendance),
                    include_participation=bool(save_participation),
                )
            except Exception:  # noqa: BLE001 — close still happens
                wrote = None
        if wrote is None and not save_attendance and not save_participation:
            try:
                self.game.cancel_setup(int(class_id))
            except Exception:  # noqa: BLE001 — no open game is fine
                pass
        elif wrote is None and not save_participation:
            try:
                self.game.cancel_setup(int(class_id))
            except Exception:  # noqa: BLE001 — no open game is fine
                pass
        if celebrate:
            return retry_if_db_locked(
                lambda: self.close_live_class_for_celebration(
                    int(class_id), winner=winner
                )
            )
        return retry_if_db_locked(
            lambda: self.wipe_live_sessions_for_class(int(class_id))
        )

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
        self,
        class_id: int,
        teacher_user_id: int,
        *,
        live_module: Any = None,
        live_slot: Any = None,
    ) -> dict[str, Any]:
        """Mint a new active live session for this teacher.

        A teacher may have only one active session at a time (any course).
        End Class / End Game / Quit must finish the current session before
        Run Live Class can start another.

        Args:
            class_id: Game-show ``classes.id``.
            teacher_user_id: Staff user starting the meeting.
            live_module: Optional catalogue module (``M1``).
            live_slot: Optional live class (``C1`` / ``C2`` / ``C3``).

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
                if live_module is not None or live_slot is not None:
                    self.set_live_session_teacher_state(
                        int(existing["id"]),
                        live_module=live_module,
                        live_slot=live_slot,
                    )
                    refreshed = self.get_live_session(int(existing["id"]))
                    return refreshed or existing
                return existing
            raise ValueError(
                "You already have a live class running. "
                "Use End Live Class to finish it before starting another."
            )
        # Drop a leftover End-Live celebration SID; keep historical ended rows.
        celebrating = [
            int(row["id"])
            for row in self.list_live_sessions_for_class(int(class_id))
            if self.session_is_celebrating(int(row["id"]))
        ]
        if celebrating:
            self.wipe_live_sessions_for_class(int(class_id), session_ids=celebrating)
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
        self._write_teacher_state(session_id, public_teacher_state(None))
        if live_module is not None or live_slot is not None:
            self.set_live_session_teacher_state(
                session_id,
                live_module=live_module,
                live_slot=live_slot,
            )
        else:
            if not self.schema_v2_owns_live_stage_questions(session_id):
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

    def live_student_poll_stamp(self, session_id: int, class_id: int) -> str:
        """Return a cheap revision token for student /state short-circuit.

        Args:
            session_id: ``live_class_sessions.id``.
            class_id: Game-show class id (accepted for call-site clarity).
        """
        del class_id
        teacher = self.live_session_teacher_state_payload(session_id)
        seq = int(teacher.get("state_seq") or 0)
        with self._lock:
            row = self.conn.execute(
                """
                SELECT
                  COALESCE((
                    SELECT MAX(id) FROM live_session_prompts
                    WHERE live_session_id = ?
                  ), 0) AS prompt_max,
                  COALESCE((
                    SELECT COUNT(*) FROM live_session_items
                    WHERE live_session_id = ? AND status = 'active'
                  ), 0) AS active_n,
                  COALESCE((
                    SELECT MAX(id) FROM live_session_items
                    WHERE live_session_id = ?
                  ), 0) AS item_max,
                  COALESCE((
                    SELECT MAX(r.id) FROM live_session_responses r
                    INNER JOIN live_session_prompts p ON p.id = r.prompt_id
                    WHERE p.live_session_id = ?
                  ), 0) AS response_max
                """,
                (int(session_id), int(session_id), int(session_id), int(session_id)),
            ).fetchone()
        event_max = 0
        try:
            with self.game._lock:
                ev = self.game.conn.execute(
                    "SELECT MAX(id) AS n FROM point_events"
                ).fetchone()
            if ev is not None and ev["n"] is not None:
                event_max = int(ev["n"])
        except (TypeError, ValueError, sqlite3.Error):
            event_max = 0
        prompt_max = int(row["prompt_max"] if row is not None else 0)
        active_n = int(row["active_n"] if row is not None else 0)
        item_max = int(row["item_max"] if row is not None else 0)
        response_max = int(row["response_max"] if row is not None else 0)
        return f"{seq}:{prompt_max}:{active_n}:{item_max}:{response_max}:{event_max}"

    def student_live_poll_unchanged(
        self, session_id: int, class_id: int, seq: Any, stamp: Any
    ) -> dict[str, Any] | None:
        """Return a tiny payload when the student's seq and stamp still match.

        Args:
            session_id: ``live_class_sessions.id``.
            class_id: Game-show class id.
            seq: Client ``state_seq`` from the last full payload.
            stamp: Client ``live_student_poll_stamp`` from the last full payload.
        """
        try:
            requested = int(seq)
        except (TypeError, ValueError):
            return None
        if stamp in (None, ""):
            return None
        teacher = self.live_session_teacher_state_payload(session_id)
        current_seq = int(teacher.get("state_seq") or 0)
        current_stamp = self.live_student_poll_stamp(session_id, class_id)
        if requested != current_seq or str(stamp) != current_stamp:
            return None
        return {
            "ok": True,
            "unchanged": True,
            "state_seq": current_seq,
            "stamp": current_stamp,
        }

    def get_live_session_state(
        self, session_id: int, *, light: bool = False
    ) -> dict[str, Any]:
        """Return overlay/IT state for one live session.

        Light polls return attendees and teacher chrome only. Full snapshots
        seed missing placements once, then list lifecycle rows.

        Args:
            session_id: ``live_class_sessions.id``.
            light: When True, skip cards, metadata, scoreboard, and points.

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
        characters = self.game.character_keys(int(session_row["class_id"]))
        public_rows: list[dict[str, Any]] = []
        for row in attendees:
            sid = row.get("student_id")
            item = public_live_attendee(row)
            if sid not in (None, ""):
                item["mood"] = moods.get(int(sid))
                item["character"] = characters.get(int(sid))
            else:
                item["mood"] = None
                item["character"] = None
            public_rows.append(item)
        present = [row for row in public_rows if not row.get("left_at")]
        phase = "ended" if session_row.get("status") == "ended" else "live"
        session_public = dict(session_row)
        session_public["allow_unmatched_guests"] = bool(
            int(session_public.get("allow_unmatched_guests") or 0)
        )
        teacher_state = self.live_session_teacher_state_payload(session_id)
        payload = {
            "session": session_public,
            "code": session_row.get("session_code"),
            "count": len(present),
            "attendees": public_rows,
            "phase": phase,
            "teacher_state": teacher_state,
            "allow_unmatched_guests": session_public["allow_unmatched_guests"],
            "mc_tally": self.live_session_mc_tally(session_id),
            "lifecycle_response_counts": self.lifecycle_response_counts(session_id),
            "state_seq": int(teacher_state.get("state_seq") or 0),
            "light": bool(light),
        }
        if light:
            return payload
        is_active = session_row.get("status") == "active"
        if is_active:
            self.ensure_live_session_items_if_stale(session_id)
        payload.update(
            {
                "active_media": self.live_session_active_media_payload(session_id),
                "teams_spark": self.staff_teams_spark_payload(session_id),
                "join_prompt": (
                    None
                    if self._session_playlist_item_removed(session_id, "minds_on")
                    else self._prompt_at_slide(
                        session_id, int(MINDS_ON_SLIDE_INDEX)
                    )
                ),
                "active_prompt": self.get_active_live_prompt(session_id),
                "active_questions": (
                    self.list_active_live_questions(session_id) if is_active else []
                ),
                "live_items": (
                    self.list_live_session_items(session_id) if is_active else []
                ),
                "live_metadata": self.live_class_metadata_for_session(session_id),
                "question_cards": (
                    self.live_session_question_cards(session_id) if is_active else []
                ),
                "class_list": self.live_class_roster_projection(session_id),
                "groups": self.live_group_projection(session_id),
                "scoreboard": self.live_scoreboard_projection(session_id),
                "canvas_sync": self.live_session_canvas_view(
                    session_id, as_teacher=True
                ),
                "game_points": {
                    str(sid): int(n)
                    for sid, n in self.live_awarded_session_points(
                        int(session_row["class_id"])
                    ).items()
                },
                "career_totals": {
                    str(sid): float(n)
                    for sid, n in (
                        self.game.career_totals(int(session_row["class_id"])) or {}
                    ).items()
                },
            }
        )
        return payload

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
            "my_team": None,
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
            self._include_late_joiner_in_group_votes(
                session_id, int(sid)
            )
        return {
            "attendee": attendee,
            "session": session_row,
            "attendance_marked": marked,
        }

    def _include_late_joiner_in_group_votes(
        self, session_id: int, student_id: int
    ) -> None:
        """Add a late joiner only to teams still collecting private votes."""

        session_row = self.get_live_session(session_id)
        if session_row is None:
            return
        team_id = self.student_team_id_for_class(
            int(session_row["class_id"]), int(student_id)
        )
        if team_id is None:
            return
        now = _now()
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT r.live_item_id
                FROM live_group_responses r
                JOIN live_session_items i ON i.id = r.live_item_id
                WHERE i.live_session_id = ?
                  AND i.status = 'active'
                  AND r.team_id = ?
                  AND r.status = 'collecting_votes'
                """,
                (int(session_id), int(team_id)),
            ).fetchall()
            for row in rows:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO live_group_members (
                        live_item_id, team_id, student_id, joined_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        int(row["live_item_id"]),
                        int(team_id),
                        int(student_id),
                        now,
                    ),
                )
            if rows:
                self.conn.commit()

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
