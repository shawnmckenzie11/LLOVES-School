"""School sqlite for LLOVES users, semesters, catalog, and course offerings."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    from codes import generate_live_access_code
    from paths import GAME_SHOW, SEMESTER_JSON
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.codes import generate_live_access_code
    from lms.paths import GAME_SHOW, SEMESTER_JSON

IT_EMAIL_DEFAULT = "solutions@mckenzian.com"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    google_sub TEXT UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    verified_at TEXT,
    verification_code TEXT,
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
"""


def _now() -> str:
    """Return a local ISO timestamp without microseconds."""
    return datetime.now().replace(microsecond=0).isoformat()


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
        self._ensure_offering_columns()
        self._ensure_offering_sections()
        self._ensure_offering_archived_column()
        self._ensure_offering_schedule_columns()
        self._ensure_library_schema()
        self._ensure_archived_column()
        self._ensure_gradebook_schema()
        self._seed()
        self.conn.commit()

    def close(self) -> None:
        """Close the sqlite connection."""
        with self._lock:
            self.conn.close()

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
                    UNIQUE(semester_id, ontario_code, teacher_user_id, section_index)
                );
                INSERT INTO course_offerings_sections (
                    id, semester_id, ontario_code, teacher_user_id,
                    live_access_code, imscc_path, expectations_status,
                    student_options_json, created_at, copied_from_offering_id,
                    instance_relpath, library_id, section_index
                )
                SELECT id, semester_id, ontario_code, teacher_user_id,
                       live_access_code, imscc_path, expectations_status,
                       student_options_json, created_at, copied_from_offering_id,
                       instance_relpath, library_id, COALESCE(section_index, 1)
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

    def _ensure_gradebook_schema(self) -> None:
        """Create per-class grade category weight storage (editable later).

        Seeds are applied lazily in ``grade_weights_for_class`` so empty classes
        still get Participation 15% / Term 60% / Exam 25% defaults.
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

    def _seed(self) -> None:
        """Insert IT user, curriculum catalog, MCF3M expectations, default semester."""
        with self._lock:
            row = self.conn.execute(
                "SELECT id FROM users WHERE email = ?", (self.it_email,)
            ).fetchone()
            if row is None:
                self.conn.execute(
                    """
                    INSERT INTO users (email, display_name, role, created_at)
                    VALUES (?, ?, 'it', ?)
                    """,
                    (self.it_email, "Shawn", _now()),
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

    def register_staff(self, email: str, display_name: str = "") -> dict[str, Any]:
        """Add a staff Google email to the allowlist.

        Args:
            email: Personal Google account.
            display_name: Optional label.

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
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT INTO users (email, display_name, role, created_at)
                VALUES (?, ?, 'staff', ?)
                """,
                (key, (display_name or key.split("@")[0]).strip(), _now()),
            )
            self.conn.commit()
            user_id = int(cur.lastrowid)
        return self.get_user(user_id) or {}

    def list_staff(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        """Return IT and staff users ordered by role then email.

        Args:
            include_archived: When False (default), users whose ``archived_at``
                is non-NULL are excluded.

        Returns:
            List of user dicts. Each dict contains all ``users`` columns.
        """
        with self._lock:
            if include_archived:
                rows = self.conn.execute(
                    "SELECT * FROM users ORDER BY role, email"
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM users WHERE archived_at IS NULL ORDER BY role, email"
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
        """Store a first-login email code."""
        with self._lock:
            self.conn.execute(
                "UPDATE users SET verification_code = ? WHERE id = ?",
                (code, user_id),
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

        Uses ``MAX(section_index) + 1`` rather than a row count so deleting a
        section never re-labels the ones that remain.

        Args:
            semester_id: Semester the offering belongs to.
            ontario_code: Catalog course code.
            teacher_user_id: Staff or IT user id.
        """
        with self._lock:
            row = self.conn.execute(
                """
                SELECT MAX(COALESCE(section_index, 1)) AS top
                FROM course_offerings
                WHERE semester_id = ? AND ontario_code = ? AND teacher_user_id = ?
                """,
                (semester_id, (ontario_code or "").strip().upper(), teacher_user_id),
            ).fetchone()
        top = int(row["top"] or 0) if row else 0
        return max(top, 0) + 1

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
                    semester_id, ontario_code, teacher_user_id, live_access_code,
                    imscc_path, expectations_status, created_at,
                    copied_from_offering_id, instance_relpath, section_index
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    semester_id,
                    code,
                    teacher_user_id,
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
        data["section_code"] = section_code(str(data.get("ontario_code") or ""), index)
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
        """Return this teacher's first section of a course/semester, if present.

        Later sections (``MCF3M-2``) exist as their own rows; callers that need
        every section use ``list_offerings``.
        """
        with self._lock:
            row = self.conn.execute(
                """
                SELECT id FROM course_offerings
                WHERE semester_id = ? AND ontario_code = ? AND teacher_user_id = ?
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
    ) -> list[dict[str, Any]]:
        """List offerings, optionally filtered.

        Args:
            semester_id: Restrict to one semester.
            teacher_user_id: Restrict to one teacher.
            include_archived: When False, rows with a non-NULL ``archived_at``
                are excluded (used by the staff dashboard).
        """
        clauses: list[str] = []
        args: list[Any] = []
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
        """Count Student Code POSTs from an IP in the last ``seconds``."""
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
        """Log a Student Code attempt and return the recent-window count."""
        with self._lock:
            self.conn.execute(
                "INSERT INTO student_code_attempts (ip, ts) VALUES (?, ?)",
                (ip, _now()),
            )
            self.conn.commit()
        return self.count_recent_code_attempts(ip, seconds=600)

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

    def list_staff(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        """Staff list with pending/active/archived status and assigned codes.

        Args:
            include_archived: When False (default), archived users are excluded.

        Returns:
            List of enriched user dicts each with ``status``, ``assigned_codes``,
            and ``last_login_at`` keys populated.
        """
        active_sem = self.get_active_semester()
        people = super().list_staff(include_archived=include_archived)
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
        return self.get_offering(int(offering_id))

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
        return updated

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
        import uuid

        try:
            from instances import library_root
            from modules import store_uploaded_module_pack
        except ImportError:
            from lms.instances import library_root
            from lms.modules import store_uploaded_module_pack

        incoming = Path(self.data_dir) / "libraries" / "_incoming" / uuid.uuid4().hex
        try:
            stored = store_uploaded_module_pack(file_storage, incoming)
        except Exception:
            shutil.rmtree(incoming, ignore_errors=True)
            raise
        library = self.create_library(ontario_code, origin="upload")
        dest = library_root(self.data_dir, int(library["id"]))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(incoming), str(dest))
        stored = dest / stored.name
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
        """Log an attempt and return the count in the last 10 minutes."""
        super().record_code_attempt(ip)
        return self.count_recent_code_attempts(ip, seconds=600) + 0

    def list_offerings(
        self,
        *,
        teacher_user_id: int | None = None,
        semester_id: int | None = None,
        include_archived: bool = True,
    ) -> list[dict[str, Any]]:
        """Offerings with class sections and roster sizes.

        Args:
            teacher_user_id: Restrict to one teacher.
            semester_id: Restrict to one semester.
            include_archived: Pass False to hide archived offerings (used by
                the staff dashboard so archived courses disappear for the teacher).
        """
        rows = super().list_offerings(
            semester_id=semester_id,
            teacher_user_id=teacher_user_id,
            include_archived=include_archived,
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
                SELECT cl.*, o.live_access_code, o.ontario_code AS offering_code,
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
            out.append(item)
        return out

    def teacher_owns_class(self, teacher_user_id: int, class_id: int) -> bool:
        """True when the class belongs to this teacher (IT may open any class)."""
        user = self.get_user(teacher_user_id)
        if user and user["role"] == "it":
            try:
                self.game.get_class(class_id)
            except KeyError:
                return False
            return True
        with self.game._lock:
            row = self.game.conn.execute(
                "SELECT teacher_user_id FROM classes WHERE id = ?",
                (int(class_id),),
            ).fetchone()
        return bool(row) and int(row["teacher_user_id"] or 0) == int(teacher_user_id)

    def live_games_for_access_code(self, live_access_code: str) -> list[dict[str, Any]]:
        """Live games for every section sharing this course key."""
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
        """All class sections that share this student key."""
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
        class_payload["live_access_code"] = offering["live_access_code"]
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
            from gradebook import GRADE_CATEGORIES, default_grade_weights, normalize_grade_weights
        except ImportError:
            from lms.gradebook import (
                GRADE_CATEGORIES,
                default_grade_weights,
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
            "first_day": span[0].isoformat() if span else None,
            "last_day": span[-1].isoformat() if span else None,
            "default_date": default_date,
            "suggested_date": self._suggested_log_date(
                class_id, picker_isos, picker_set=set(picker_isos)
            ),
        }

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

    def gradebook_for_class(self, class_id: int, sort: str = "az") -> dict[str, Any]:
        """Weighted gradebook scaffold: Participation / Term / Exam.

        Participation rolls up credited live-class session points. Term and Exam
        are placeholders (empty items) until assignments/exams are wired.

        Args:
            class_id: Class primary key.
            sort: Roster sort key.

        Returns:
            Gradebook JSON for the staff Grades tab.
        """
        weights = self.grade_weights_for_class(class_id)
        dash = self.game.dashboard(class_id, sort=sort)
        students = list(dash.get("students") or [])
        career = self.game.career_totals(class_id)
        participation_scores: dict[str, float] = {}
        for student in students:
            sid = int(student["id"])
            participation_scores[str(sid)] = float(career.get(sid) or 0)
        return {
            "ok": True,
            "class": self.enrich_class(dash["class"]),
            "students": students,
            "weights": weights,
            "categories": [
                {
                    "id": "participation",
                    "label": "Participation",
                    "weight_pct": weights["participation"],
                    "scores": participation_scores,
                    "editable_weights": True,
                },
                {
                    "id": "term",
                    "label": "Term",
                    "weight_pct": weights["term"],
                    "scores": {str(int(s["id"])): None for s in students},
                    "items": [],
                    "editable_weights": True,
                },
                {
                    "id": "exam",
                    "label": "Exam",
                    "weight_pct": weights["exam"],
                    "scores": {str(int(s["id"])): None for s in students},
                    "items": [],
                    "placeholder": True,
                    "editable_weights": True,
                },
            ],
            # Explicit hook for a later weight-edit UI (POST /grade-weights).
            "weight_edit_endpoint": f"/api/classes/{int(class_id)}/grade-weights",
        }
