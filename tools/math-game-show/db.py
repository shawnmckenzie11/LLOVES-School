#!/usr/bin/env python3
"""SQLite schema and queries for the Math Game Show local app."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from csv_import import parse_canvas_grades_csv
from schedule import (
    TIME_OPTIONS,
    format_header_label,
    format_time_label,
    next_meeting_datetime,
    parse_time_label,
    store_days,
    unique_header_label,
)

from teams import (
    assign_balanced,
    assign_manual,
    assign_random,
    color_for_team,
    default_team_name,
    validate_team_count,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY,
    year TEXT NOT NULL,
    semester TEXT NOT NULL,
    course_code TEXT NOT NULL,
    days TEXT NOT NULL,
    time TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    canvas_id TEXT NOT NULL,
    last_display TEXT NOT NULL,
    first_name TEXT NOT NULL,
    UNIQUE(class_id, canvas_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    starts_at TEXT NOT NULL,
    header_label TEXT NOT NULL,
    status TEXT NOT NULL,
    log_path TEXT,
    source TEXT NOT NULL DEFAULT 'game'
);

CREATE TABLE IF NOT EXISTS session_scores (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    present INTEGER NOT NULL DEFAULT 0,
    points REAL NOT NULL DEFAULT 0,
    points_r1 REAL NOT NULL DEFAULT 0,
    points_r2 REAL NOT NULL DEFAULT 0,
    points_r3 REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, student_id)
);

CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    event_seq INTEGER NOT NULL DEFAULT 0,
    last_event_json TEXT,
    owns_session INTEGER NOT NULL DEFAULT 0,
    current_round INTEGER NOT NULL DEFAULT 1,
    round_started_at TEXT,
    round_duration_sec INTEGER,
    rounds_json TEXT
);

CREATE TABLE IF NOT EXISTS point_events (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    from_kind TEXT NOT NULL,
    from_id INTEGER,
    to_kind TEXT NOT NULL,
    to_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    team_rule TEXT,
    round INTEGER NOT NULL DEFAULT 1,
    UNIQUE(session_id, seq)
);

CREATE TABLE IF NOT EXISTS game_teams (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    sort_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS game_memberships (
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    team_id INTEGER NOT NULL REFERENCES game_teams(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    PRIMARY KEY (game_id, student_id)
);

CREATE TABLE IF NOT EXISTS team_buckets (
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    team_id INTEGER NOT NULL REFERENCES game_teams(id) ON DELETE CASCADE,
    points REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (game_id, team_id)
);

CREATE TABLE IF NOT EXISTS subtotals (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    through_session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    through_starts_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subtotal_scores (
    subtotal_id INTEGER NOT NULL REFERENCES subtotals(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    points REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (subtotal_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_students_class ON students(class_id);
CREATE INDEX IF NOT EXISTS idx_sessions_class ON sessions(class_id);
CREATE INDEX IF NOT EXISTS idx_games_class ON games(class_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON point_events(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_subtotals_class ON subtotals(class_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_game
    ON games(class_id) WHERE status != 'ended';

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

SCOREBOARD_GAME_KEY = "scoreboard_game_id"
CURRENT_CLASS_KEY = "current_class_id"
STAT_WINDOW_KEY_PREFIX = "stat_window:"
STAT_WINDOWS = ("last_class", "last_week", "year")
DEFAULT_STAT_WINDOW = "last_class"
STAT_WINDOW_LABELS = {
    "last_class": "last class",
    "last_week": "last week",
    "year": "this year",
}

TEAM_RULES = ("each_member", "split_members", "team_only")

ROUND_TITLES = {
    1: "Open Question Round",
    2: "Team Challenge Question",
    3: "Formative Round",
}

ROUND_KIND_META = {
    "open": {"title": "Open Question", "bucket": 1, "default_min": 20},
    "challenge": {"title": "Team Challenge", "bucket": 2, "default_min": 10},
    "formative": {"title": "Formative", "bucket": 3, "default_min": 10},
}

DEFAULT_ROUNDS_CONFIG = [
    {"kind": "open", "duration_sec": 20 * 60},
    {"kind": "challenge", "duration_sec": 10 * 60},
    {"kind": "formative", "duration_sec": 10 * 60},
]

# Public scoreboard ticker: Leaders + Most Improved per teaching slice.
LEADER_SLICES = (
    ("points_r1", "Open Question Leaders", "Open Question Most Improved"),
    ("points_r2", "Team Challenge Leaders", "Team Challenge Most Improved"),
    ("points_r3", "Formative Leaders", "Formative Most Improved"),
)

ROUND_DURATIONS_SEC = {
    1: 20 * 60,
    2: 10 * 60,
    3: 10 * 60,
}


def normalize_rounds_config(raw: Any) -> list[dict[str, Any]]:
    """Validate and normalize a teacher rounds plan (1–3 unique kinds).

    Args:
        raw: List of ``{kind, duration_sec|minutes}`` or None for defaults.

    Returns:
        Normalized list with ``kind``, ``title``, ``bucket``, ``duration_sec``.

    Raises:
        ValueError: When count, kinds, or durations are invalid.
    """
    if raw is None:
        items = list(DEFAULT_ROUNDS_CONFIG)
    else:
        if not isinstance(raw, list):
            raise ValueError("rounds must be a list")
        items = list(raw)
    if not 1 <= len(items) <= 3:
        raise ValueError("Choose between 1 and 3 rounds")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each round must be an object")
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in ROUND_KIND_META:
            raise ValueError(
                "Round type must be Open Question, Team Challenge, or Formative"
            )
        if kind in seen:
            raise ValueError("Each round type can be used at most once")
        seen.add(kind)
        meta = ROUND_KIND_META[kind]
        if item.get("duration_sec") is not None:
            duration = int(item.get("duration_sec") or 0)
        elif item.get("minutes") is not None:
            duration = int(float(item.get("minutes")) * 60)
        else:
            duration = int(meta["default_min"] * 60)
        if duration < 60 or duration > 3 * 60 * 60:
            raise ValueError("Round length must be between 1 and 180 minutes")
        out.append(
            {
                "kind": kind,
                "title": meta["title"],
                "bucket": int(meta["bucket"]),
                "duration_sec": duration,
            }
        )
    return out


def rounds_from_game_row(game: dict[str, Any] | sqlite3.Row) -> list[dict[str, Any]]:
    """Parse ``rounds_json`` from a games row, falling back to defaults.

    Args:
        game: Games row mapping.
    """
    raw = None
    try:
        raw = game["rounds_json"]
    except (KeyError, IndexError, TypeError):
        raw = None
    if raw:
        try:
            parsed = json.loads(str(raw))
            return normalize_rounds_config(parsed)
        except (ValueError, TypeError, json.JSONDecodeError):
            pass
    return normalize_rounds_config(None)


def split_amount(amount: float, n_members: int) -> list[float]:
    """Give each member the same share, rounded to one decimal place.

    The team board still shows the full awarded amount; any rounding
    leftover is stored on the team-only bucket (see ``award_points``).

    Args:
        amount: Points to split (must be positive).
        n_members: Present team size.

    Returns:
        Per-member credits, each ``round(amount / n, 1)``.
    """
    if n_members < 1:
        raise ValueError("Cannot split team points across an empty team")
    share = round(float(amount) / float(n_members), 1)
    return [share] * int(n_members)


def as_points(value: Any) -> float:
    """Round a credit to one decimal place.

    Args:
        value: Raw numeric (int, float, or SQLite number).

    Returns:
        Value rounded to one tenth.
    """
    return round(float(value or 0), 1)


def points_label(value: Any) -> str:
    """Format a credit the way the scoreboard ticker prints it.

    Args:
        value: Raw numeric (int, float, or SQLite number).
    """
    n = as_points(value)
    return str(int(n)) if n == int(n) else f"{n:.1f}"


def normalize_stat_window(value: Any) -> str:
    """Return a known stats-period key, or raise.

    Args:
        value: Teacher-selected window (``last_class``, ``last_week``, ``year``).
    """
    raw = str(value or "").strip()
    if raw not in STAT_WINDOWS:
        raise ValueError("window must be last_class, last_week, or year")
    return raw


def stat_window_label(window: str) -> str:
    """Student-facing period phrase for the ticker kicker.

    Args:
        window: A value in ``STAT_WINDOWS``.
    """
    return STAT_WINDOW_LABELS.get(window, STAT_WINDOW_LABELS[DEFAULT_STAT_WINDOW])


def leader_periods(
    scored_ids: list[int], window: str
) -> tuple[list[int], list[int] | None]:
    """Current and prior session-id lists for Leaders / Most Improved.

    Last week is the last two scored columns. Most Improved needs the
    previous two (four scored columns). One scored column falls back to
    last-class Leaders with no prior. This year uses every scored column
    and has no prior period.

    Args:
        scored_ids: Session ids with at least one credited present score,
            oldest first.
        window: ``last_class``, ``last_week``, or ``year``.
    """
    if not scored_ids:
        return [], None
    chosen = window if window in STAT_WINDOWS else DEFAULT_STAT_WINDOW
    if chosen == "year":
        return list(scored_ids), None
    if chosen == "last_week":
        if len(scored_ids) < 2:
            return [scored_ids[-1]], None
        current = scored_ids[-2:]
        prior = scored_ids[-4:-2] if len(scored_ids) >= 4 else None
        return current, prior
    current = [scored_ids[-1]]
    prior = [scored_ids[-2]] if len(scored_ids) > 1 else None
    return current, prior


def member_round_points(score: dict[str, Any] | None) -> dict[str, float]:
    """Individual credited points for a teacher-game member.

    ``session_points`` is the running lesson total (R1+R2+R3). Round
    buckets stay 0 for team-only ESPN awards, which never hit a student.

    Args:
        score: A ``session_scores`` slice, or ``None`` if the student
            has no row yet.
    """
    row = score or {}
    return {
        "session_points": as_points(row.get("points")),
        "points_r1": as_points(row.get("points_r1")),
        "points_r2": as_points(row.get("points_r2")),
        "points_r3": as_points(row.get("points_r3")),
    }


def round_title(round_n: int) -> str:
    """Return the teacher-facing title for rounds 1–3.

    Args:
        round_n: Round index.

    Returns:
        Display name such as ``Open Question Round``.
    """
    return ROUND_TITLES.get(int(round_n), ROUND_TITLES[1])


def round_remaining_sec(
    started_at: str | None,
    duration_sec: int | None,
    *,
    now: datetime | None = None,
) -> int:
    """Seconds left on a round clock, never below 0.

    A full second must elapse before the count drops, so a just-started
    20:00 round still reads 1200.

    Args:
        started_at: ISO timestamp when the round began, or None.
        duration_sec: Planned length in seconds.
        now: Optional clock (tests inject this).

    Returns:
        Remaining whole seconds, clamped at 0.
    """
    if not started_at or not duration_sec:
        return 0
    try:
        started = datetime.fromisoformat(str(started_at))
    except ValueError:
        return 0
    clock = now or datetime.now()
    elapsed = (clock - started).total_seconds()
    return max(0, int(duration_sec) - int(max(0, elapsed)))


def round_ends_at(started_at: str | None, duration_sec: int | None) -> str | None:
    """ISO end time for the round clock, or None if it has not started.

    Args:
        started_at: ISO timestamp when the round began.
        duration_sec: Planned length in seconds.
    """
    if not started_at or not duration_sec:
        return None
    try:
        started = datetime.fromisoformat(str(started_at))
    except ValueError:
        return None
    return (started + timedelta(seconds=int(duration_sec))).isoformat()


def round_ends_at_ms(started_at: str | None, duration_sec: int | None) -> int | None:
    """UTC epoch milliseconds when the round clock hits 0:00.

    Clients count down from this deadline so polls do not reset the display.

    Args:
        started_at: ISO timestamp when the round began.
        duration_sec: Planned length in seconds.
    """
    if not started_at or not duration_sec:
        return None
    try:
        started = datetime.fromisoformat(str(started_at))
    except ValueError:
        return None
    end = started + timedelta(seconds=int(duration_sec))
    return int(end.timestamp() * 1000)

# Future TODO (Teacher Game Dashboard): allow students to award points to one
# another. Scoring is teacher-only until then; keep the log `from` field.


def normalize_codename(raw: str) -> str:
    """Validate a LLOVES roster Codename.

    Args:
        raw: Teacher-entered display name.

    Returns:
        Stripped Codename.

    Raises:
        ValueError: If empty, too short/long, or contains a comma.
    """
    name = (raw or "").strip()
    if not name:
        raise ValueError("Codename is required")
    if "," in name:
        raise ValueError("Codenames cannot contain commas")
    if len(name) < 2 or len(name) > 32:
        raise ValueError("Codenames must be 2–32 characters")
    return name


def roster_from_codenames(codenames: list[str]) -> list[dict[str, str]]:
    """Build student insert rows from teacher-typed Codenames.

    Args:
        codenames: One Codename per student (order preserved).

    Returns:
        Rows with ``canvas_id``, ``first_name``, ``last_display``, ``codename``.

    Raises:
        ValueError: If the list is empty or a Codename is invalid/duplicated.
    """
    seen: set[str] = set()
    roster: list[dict[str, str]] = []
    for raw in codenames:
        name = normalize_codename(raw)
        key = name.lower()
        if key in seen:
            raise ValueError(f"Duplicate Codename: {name}")
        seen.add(key)
        roster.append(
            {
                "canvas_id": f"codename:{key}",
                "last_display": "",
                "first_name": name,
                "codename": name,
            }
        )
    if not roster:
        raise ValueError("Add at least one Codename")
    return roster


class GameShowDB:
    """Thread-safe SQLite access for classes, sessions, and live games."""

    def __init__(self, db_path: Path, data_dir: Path) -> None:
        """Open (or create) the app database.

        Args:
            db_path: Path to ``app.sqlite``.
            data_dir: Root for uploads and JSONL logs.
        """
        self.db_path = db_path
        self.data_dir = data_dir
        self.logs_dir = data_dir / "logs"
        self.uploads_dir = data_dir / "uploads"
        self._lock = threading.RLock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the first schema.

        Existing local DBs keep their data; ``owns_session`` defaults to 0.
        Legacy ``session_scores.points`` become Round 1 totals.
        LLOVES adds ``offering_id`` / ``teacher_user_id`` on classes and
        ``codename`` on students (Canvas CSV columns stay for the local app).
        """
        game_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(games)").fetchall()
        }
        if "owns_session" not in game_cols:
            self.conn.execute(
                "ALTER TABLE games ADD COLUMN owns_session INTEGER NOT NULL DEFAULT 0"
            )
        self._ensure_real_points("session_scores")
        self._ensure_real_points("team_buckets")
        sess_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "source" not in sess_cols:
            self.conn.execute(
                "ALTER TABLE sessions ADD COLUMN source TEXT NOT NULL DEFAULT 'game'"
            )
        self._migrate_rounds()
        self._migrate_lloves_roster()
        self._migrate_class_live_access_code()

    def _migrate_rounds(self) -> None:
        """Add round/timer columns and copy legacy points into Round 1.

        Live games that predate the timer get Round 1 started at ``created_at``.
        """
        game_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(games)").fetchall()
        }
        if "current_round" not in game_cols:
            self.conn.execute(
                "ALTER TABLE games ADD COLUMN current_round INTEGER NOT NULL DEFAULT 1"
            )
        if "round_started_at" not in game_cols:
            self.conn.execute("ALTER TABLE games ADD COLUMN round_started_at TEXT")
        if "round_duration_sec" not in game_cols:
            self.conn.execute("ALTER TABLE games ADD COLUMN round_duration_sec INTEGER")
        if "rounds_json" not in game_cols:
            self.conn.execute("ALTER TABLE games ADD COLUMN rounds_json TEXT")
        self.conn.execute(
            """
            UPDATE games
            SET round_started_at = created_at,
                round_duration_sec = ?
            WHERE status = 'live' AND round_started_at IS NULL
            """,
            (ROUND_DURATIONS_SEC[1],),
        )
        score_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(session_scores)").fetchall()
        }
        added_r1 = False
        if "points_r1" not in score_cols:
            self.conn.execute(
                "ALTER TABLE session_scores ADD COLUMN points_r1 REAL NOT NULL DEFAULT 0"
            )
            added_r1 = True
        if "points_r2" not in score_cols:
            self.conn.execute(
                "ALTER TABLE session_scores ADD COLUMN points_r2 REAL NOT NULL DEFAULT 0"
            )
        if "points_r3" not in score_cols:
            self.conn.execute(
                "ALTER TABLE session_scores ADD COLUMN points_r3 REAL NOT NULL DEFAULT 0"
            )
        if added_r1:
            self.conn.execute("UPDATE session_scores SET points_r1 = points")
        event_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(point_events)").fetchall()
        }
        if "round" not in event_cols:
            self.conn.execute(
                'ALTER TABLE point_events ADD COLUMN "round" INTEGER NOT NULL DEFAULT 1'
            )

    def _migrate_lloves_roster(self) -> None:
        """Add offering/teacher links and a Codename roster column.

        The standalone Math Game Show still uses Canvas CSV fields. LLOVES
        stores the teacher-chosen Codename and leaves last/first blank.
        """
        class_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(classes)").fetchall()
        }
        if "offering_id" not in class_cols:
            self.conn.execute("ALTER TABLE classes ADD COLUMN offering_id INTEGER")
        if "teacher_user_id" not in class_cols:
            self.conn.execute("ALTER TABLE classes ADD COLUMN teacher_user_id INTEGER")
        student_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(students)").fetchall()
        }
        if "codename" not in student_cols:
            self.conn.execute("ALTER TABLE students ADD COLUMN codename TEXT")
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_students_class_codename
            ON students(class_id, codename)
            WHERE codename IS NOT NULL AND TRIM(codename) != ''
            """
        )

    def _migrate_class_live_access_code(self) -> None:
        """Add a unique 8-character student join code per class section."""
        class_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(classes)").fetchall()
        }
        if "live_access_code" not in class_cols:
            self.conn.execute("ALTER TABLE classes ADD COLUMN live_access_code TEXT")
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_classes_live_access_code
            ON classes(live_access_code)
            WHERE live_access_code IS NOT NULL AND TRIM(live_access_code) != ''
            """
        )

    def _ensure_real_points(self, table: str) -> None:
        """Rebuild a scores table if ``points`` still has INTEGER affinity.

        SQLite INTEGER columns coerce 3.3 to 3, which breaks 1-decimal splits.

        Args:
            table: ``session_scores`` or ``team_buckets``.
        """
        cols = list(self.conn.execute(f"PRAGMA table_info({table})").fetchall())
        if not cols:
            return
        points = next((row for row in cols if row["name"] == "points"), None)
        if points is None or str(points["type"]).upper() == "REAL":
            return
        self.conn.execute("PRAGMA foreign_keys = OFF")
        if table == "session_scores":
            col_names = {row["name"] for row in cols}
            if "points_r1" in col_names:
                self.conn.executescript(
                    """
                    CREATE TABLE session_scores_v2 (
                        session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                        present INTEGER NOT NULL DEFAULT 0,
                        points REAL NOT NULL DEFAULT 0,
                        points_r1 REAL NOT NULL DEFAULT 0,
                        points_r2 REAL NOT NULL DEFAULT 0,
                        points_r3 REAL NOT NULL DEFAULT 0,
                        PRIMARY KEY (session_id, student_id)
                    );
                    INSERT INTO session_scores_v2
                        SELECT session_id, student_id, present, points,
                               points_r1, points_r2, points_r3
                        FROM session_scores;
                    DROP TABLE session_scores;
                    ALTER TABLE session_scores_v2 RENAME TO session_scores;
                    """
                )
            else:
                self.conn.executescript(
                    """
                    CREATE TABLE session_scores_v2 (
                        session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                        present INTEGER NOT NULL DEFAULT 0,
                        points REAL NOT NULL DEFAULT 0,
                        PRIMARY KEY (session_id, student_id)
                    );
                    INSERT INTO session_scores_v2
                        SELECT session_id, student_id, present, points FROM session_scores;
                    DROP TABLE session_scores;
                    ALTER TABLE session_scores_v2 RENAME TO session_scores;
                    """
                )
        else:
            self.conn.executescript(
                """
                CREATE TABLE team_buckets_v2 (
                    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                    team_id INTEGER NOT NULL REFERENCES game_teams(id) ON DELETE CASCADE,
                    points REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (game_id, team_id)
                );
                INSERT INTO team_buckets_v2 SELECT game_id, team_id, points FROM team_buckets;
                DROP TABLE team_buckets;
                ALTER TABLE team_buckets_v2 RENAME TO team_buckets;
                """
            )
        self.conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        """Close the SQLite connection."""
        with self._lock:
            self.conn.close()

    def _now(self) -> str:
        """Return an ISO-8601 local timestamp."""
        return datetime.now().replace(microsecond=0).isoformat()

    def _insert_student_row(
        self,
        class_id: int,
        *,
        canvas_id: str,
        last_display: str,
        first_name: str,
        codename: str | None = None,
    ) -> int:
        """Insert one students row, including ``codename`` when migrated.

        Args:
            class_id: Parent class.
            canvas_id: Unique-per-class key (Canvas SIS id or ``codename:…``).
            last_display: Family name, or empty on the LLOVES path.
            first_name: Given name, or the Codename on the LLOVES path.
            codename: LLOVES roster name, or None for Canvas CSV imports.

        Returns:
            New student primary key.
        """
        self.conn.execute(
            """
            INSERT INTO students
                (class_id, canvas_id, last_display, first_name, codename)
            VALUES (?, ?, ?, ?, ?)
            """,
            (class_id, canvas_id, last_display, first_name, codename),
        )
        return int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def create_class(
        self,
        *,
        year: str,
        semester: str,
        course_code: str,
        days_preset: str,
        time_label: str,
        csv_text: str | None = None,
        codenames: list[str] | None = None,
        offering_id: int | None = None,
        teacher_user_id: int | None = None,
        today: date | None = None,
    ) -> dict[str, Any]:
        """Insert a class, roster, and the first template session column.

        Pass either ``csv_text`` (standalone Math Game Show) or ``codenames``
        (LLOVES Populate Class). Do not pass both.

        Args:
            year: Display year such as ``2026/27``.
            semester: ``Semester 1`` or ``Semester 2``.
            course_code: e.g. ``MCF3M``.
            days_preset: ``M/W/F`` or ``T/Th/F``.
            time_label: One of the wizard times.
            csv_text: Canvas gradebook CSV contents (legacy path).
            codenames: Teacher-typed roster (LLOVES path).
            offering_id: School ``course_offerings.id`` when created from LLOVES.
            teacher_user_id: School ``users.id`` of the class teacher.
            today: Optional reference date for the first meeting.

        Returns:
            Class dict including ``id`` and imported student count.
        """
        if not year.strip() or not semester.strip() or not course_code.strip():
            raise ValueError("Year, semester, and course code are required")
        if time_label not in TIME_OPTIONS:
            raise ValueError(f"Time must be one of: {', '.join(TIME_OPTIONS)}")
        if codenames is not None and csv_text:
            raise ValueError("Provide a Canvas CSV or a Codename roster, not both")
        if codenames is not None:
            roster = roster_from_codenames(codenames)
        elif csv_text:
            roster = parse_canvas_grades_csv(csv_text)
        else:
            raise ValueError("Provide a Canvas CSV or a Codename roster")
        days = store_days(days_preset)
        meeting = next_meeting_datetime(days, time_label, today=today)
        header = format_header_label(meeting, time_label)
        created = self._now()
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT INTO classes (
                    year, semester, course_code, days, time, created_at,
                    offering_id, teacher_user_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    year.strip(),
                    semester.strip(),
                    course_code.strip(),
                    days,
                    time_label,
                    created,
                    offering_id,
                    teacher_user_id,
                ),
            )
            class_id = int(cur.lastrowid)
            for student in roster:
                self._insert_student_row(
                    class_id,
                    canvas_id=str(student["canvas_id"]),
                    last_display=str(student["last_display"]),
                    first_name=str(student["first_name"]),
                    codename=student.get("codename"),
                )
            self.conn.execute(
                """
                INSERT INTO sessions (class_id, starts_at, header_label, status, log_path)
                VALUES (?, ?, ?, 'template', NULL)
                """,
                (class_id, meeting.isoformat(), header),
            )
            session_id = int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            student_ids = [
                int(row["id"])
                for row in self.conn.execute(
                    "SELECT id FROM students WHERE class_id = ?", (class_id,)
                )
            ]
            for sid in student_ids:
                self.conn.execute(
                    """
                    INSERT INTO session_scores (session_id, student_id, present, points)
                    VALUES (?, ?, 0, 0)
                    """,
                    (session_id, sid),
                )
            if csv_text:
                upload_path = self.uploads_dir / f"class-{class_id}.csv"
                upload_path.write_text(csv_text, encoding="utf-8")
            self._set_current_class(class_id)
            self.conn.commit()
        return self.get_class(class_id)

    def list_classes(self, year: str, semester: str) -> list[dict[str, Any]]:
        """Return classes whose year/semester match the picker filter.

        Args:
            year: Display year such as ``2026/27``.
            semester: ``Semester 1`` or ``Semester 2``.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM classes
                WHERE year = ? AND semester = ?
                ORDER BY created_at DESC, id DESC
                """,
                (year, semester),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_classes_for_teacher(
        self,
        teacher_user_id: int,
        *,
        offering_id: int | None = None,
        year: str | None = None,
        semester: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return classes owned by a LLOVES staff user.

        Args:
            teacher_user_id: School ``users.id``.
            offering_id: Optional ``course_offerings.id`` filter.
            year: Optional display year such as ``2026/27``.
            semester: Optional ``Semester 1`` / ``Semester 2``.

        Returns:
            Class dicts newest first.
        """
        clauses = ["teacher_user_id = ?"]
        args: list[Any] = [teacher_user_id]
        if offering_id is not None:
            clauses.append("offering_id = ?")
            args.append(offering_id)
        if year:
            clauses.append("year = ?")
            args.append(year)
        if semester:
            clauses.append("semester = ?")
            args.append(semester)
        sql = (
            "SELECT * FROM classes WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, id DESC"
        )
        with self._lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def live_games_for_offering(self, offering_id: int) -> list[dict[str, Any]]:
        """Return non-ended games for every class on a course offering.

        Args:
            offering_id: School ``course_offerings.id``.

        Returns:
            Game rows plus class days/time.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT g.*, c.days AS class_days, c.time AS class_time,
                       c.course_code AS course_code
                FROM games g
                JOIN classes c ON c.id = g.class_id
                WHERE c.offering_id = ? AND g.status != 'ended'
                ORDER BY g.id DESC
                """,
                (offering_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_class(self, class_id: int) -> dict[str, Any]:
        """Return one class or raise KeyError.

        Args:
            class_id: Classes primary key.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM classes WHERE id = ?", (class_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"class {class_id}")
        payload = dict(row)
        payload["student_count"] = self._student_count(class_id)
        return payload

    def get_class_by_live_code(self, live_access_code: str) -> dict[str, Any] | None:
        """Return the class that owns this unique student join code.

        Args:
            live_access_code: Normalized 8-character code.

        Returns:
            Class dict, or ``None`` when no class has that code.
        """
        code = (live_access_code or "").strip().upper()
        if not code:
            return None
        with self._lock:
            row = self.conn.execute(
                """
                SELECT * FROM classes
                WHERE live_access_code = ?
                LIMIT 1
                """,
                (code,),
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["student_count"] = self._student_count(int(payload["id"]))
        return payload

    def set_class_live_access_code(self, class_id: int, code: str) -> dict[str, Any]:
        """Persist a unique student join code on one class.

        Args:
            class_id: Classes primary key.
            code: Uppercase 8-character live-access code.

        Returns:
            Updated class dict.

        Raises:
            KeyError: If the class does not exist.
        """
        self.get_class(class_id)
        with self._lock:
            self.conn.execute(
                "UPDATE classes SET live_access_code = ? WHERE id = ?",
                ((code or "").strip().upper(), int(class_id)),
            )
            self.conn.commit()
        return self.get_class(class_id)

    def live_game_for_class(self, class_id: int) -> dict[str, Any] | None:
        """Return the open live game for one class, if any.

        Args:
            class_id: Classes primary key.

        Returns:
            Game row joined with class schedule, or ``None``.
        """
        with self._lock:
            row = self.conn.execute(
                """
                SELECT g.id AS game_id, g.class_id, g.status, g.session_id,
                       cl.days, cl.time, cl.course_code, cl.offering_id
                FROM games g
                JOIN classes cl ON cl.id = g.class_id
                WHERE g.class_id = ? AND g.status = 'live'
                ORDER BY g.id DESC
                LIMIT 1
                """,
                (int(class_id),),
            ).fetchone()
        return dict(row) if row else None

    def _student_count(self, class_id: int) -> int:
        """Count roster rows for a class.

        Args:
            class_id: Classes primary key.
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM students WHERE class_id = ?", (class_id,)
        ).fetchone()
        return int(row["n"])

    def career_totals(self, class_id: int) -> dict[int, float]:
        """Sum credited individual scores per student (all columns).

        Dashboard TOTAL is the sum of each session's credited score, which
        comes from individual events plus any team-rule credits. Pure
        team-only awards never enter this total. Frozen SUBTOTAL columns
        are snapshots and are not added again here.

        Args:
            class_id: Classes primary key.

        Returns:
            Map of student_id → summed points.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT ss.student_id AS student_id, COALESCE(SUM(ss.points), 0) AS total
                FROM students s
                LEFT JOIN session_scores ss ON ss.student_id = s.id
                WHERE s.class_id = ?
                GROUP BY s.id
                """,
                (class_id,),
            ).fetchall()
        return {int(row["student_id"]): as_points(row["total"]) for row in rows}

    def dashboard(self, class_id: int, sort: str = "last") -> dict[str, Any]:
        """Spreadsheet payload: students, mixed columns, cells, totals.

        ``columns`` interleaves live-class sessions with frozen SUBTOTAL
        snapshots. ``live_subtotals`` sums only sessions after the latest
        freeze. ``totals`` still sums every live-class column.

        Args:
            class_id: Classes primary key.
            sort: ``first`` / ``last`` (Canvas names) or ``az`` / ``za`` (Codename).
        """
        cls = self.get_class(class_id)
        self.set_current_class(class_id)
        with self._lock:
            students = [
                dict(row)
                for row in self.conn.execute(
                    "SELECT * FROM students WHERE class_id = ?", (class_id,)
                )
            ]
            sessions = [
                dict(row)
                for row in self.conn.execute(
                    """
                    SELECT * FROM sessions
                    WHERE class_id = ?
                    ORDER BY starts_at ASC, id ASC
                    """,
                    (class_id,),
                )
            ]
            score_rows = self.conn.execute(
                """
                SELECT ss.session_id, ss.student_id, ss.present, ss.points,
                       ss.points_r1, ss.points_r2, ss.points_r3
                FROM session_scores ss
                JOIN sessions se ON se.id = ss.session_id
                WHERE se.class_id = ?
                """,
                (class_id,),
            ).fetchall()
            subtotals = [
                dict(row)
                for row in self.conn.execute(
                    """
                    SELECT * FROM subtotals
                    WHERE class_id = ?
                    ORDER BY created_at ASC, id ASC
                    """,
                    (class_id,),
                )
            ]
            sub_score_rows = self.conn.execute(
                """
                SELECT ss.subtotal_id, ss.student_id, ss.points
                FROM subtotal_scores ss
                JOIN subtotals st ON st.id = ss.subtotal_id
                WHERE st.class_id = ?
                """,
                (class_id,),
            ).fetchall()
            open_game = self.conn.execute(
                """
                SELECT id, session_id, status FROM games
                WHERE class_id = ? AND status != 'ended'
                """,
                (class_id,),
            ).fetchone()
        cells: dict[str, dict[str, Any]] = {}
        totals: dict[int, float] = {int(s["id"]): 0.0 for s in students}
        per_session: dict[int, dict[int, float]] = {}
        for row in score_rows:
            sid = int(row["student_id"])
            sess_id = int(row["session_id"])
            pts = as_points(row["points"])
            totals[sid] = as_points(totals.get(sid, 0) + pts)
            per_session.setdefault(sess_id, {})[sid] = pts
            key = f"{sess_id}:{sid}"
            cells[key] = {
                "present": bool(row["present"]),
                "points": pts,
                "points_r1": as_points(row["points_r1"]),
                "points_r2": as_points(row["points_r2"]),
                "points_r3": as_points(row["points_r3"]),
            }
        for row in sub_score_rows:
            cells[f"sub:{row['subtotal_id']}:{row['student_id']}"] = {
                "present": False,
                "points": as_points(row["points"]),
            }
        live_ids = self._live_session_ids(sessions, subtotals)
        live_subtotals: dict[int, float] = {int(s["id"]): 0.0 for s in students}
        for sess_id in live_ids:
            for sid, pts in per_session.get(sess_id, {}).items():
                live_subtotals[sid] = as_points(live_subtotals.get(sid, 0) + pts)
        sort_key = (sort or "last").strip().lower()
        if sort_key in {"az", "codename"}:
            students.sort(
                key=lambda s: (
                    str(s.get("codename") or s.get("first_name") or "").lower(),
                    int(s["id"]),
                )
            )
            sort_out = "az"
        elif sort_key in {"za", "codename_desc"}:
            students.sort(
                key=lambda s: (
                    str(s.get("codename") or s.get("first_name") or "").lower(),
                    int(s["id"]),
                ),
                reverse=True,
            )
            sort_out = "za"
        elif sort_key == "first":
            students.sort(
                key=lambda s: (
                    str(s.get("codename") or s["first_name"]).lower(),
                    str(s["last_display"]).lower(),
                )
            )
            sort_out = "first"
        else:
            students.sort(
                key=lambda s: (
                    str(s.get("codename") or s["last_display"] or s["first_name"]).lower(),
                    str(s["first_name"]).lower(),
                )
            )
            sort_out = "last"
        columns = self._sheet_columns(sessions, subtotals)
        return {
            "class": cls,
            "sort": sort_out,
            "students": students,
            "sessions": sessions,
            "columns": columns,
            "cells": cells,
            "live_subtotals": {str(k): v for k, v in live_subtotals.items()},
            "totals": {str(k): v for k, v in totals.items()},
            "open_game": dict(open_game) if open_game else None,
            "time_options": list(TIME_OPTIONS),
            "stat_window": self._get_stat_window(class_id),
        }

    def _live_session_ids(
        self,
        sessions: list[dict[str, Any]],
        subtotals: list[dict[str, Any]],
    ) -> set[int]:
        """Session ids that count toward the live SUBTOTAL column.

        After a freeze, only sessions created afterward (higher ids than
        ``through_session_id``) are live. Frozen snapshots stay unchanged.

        Args:
            sessions: Class sessions in display order.
            subtotals: Frozen snapshots oldest-first.
        """
        if not subtotals:
            return {int(s["id"]) for s in sessions}
        last = subtotals[-1]
        through_id = last.get("through_session_id")
        if through_id is not None:
            cutoff = int(through_id)
            return {int(s["id"]) for s in sessions if int(s["id"]) > cutoff}
        through_at = str(last["through_starts_at"])
        return {
            int(s["id"])
            for s in sessions
            if str(s["starts_at"]) > through_at
        }

    def _sheet_columns(
        self,
        sessions: list[dict[str, Any]],
        subtotals: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Place frozen SUBTOTAL columns, then any newer game columns to the right.

        A freeze includes every session that existed at snapshot time (ids up
        to ``through_session_id``). Later games — even with an earlier calendar
        date — stay to the right and count toward live SUBTOTAL and TOTAL,
        not the frozen snapshot.

        Args:
            sessions: Sessions in ``starts_at``, id order.
            subtotals: Frozen rows oldest-first.
        """
        remaining = list(sessions)
        columns: list[dict[str, Any]] = []
        cutoff = 0
        for sub in subtotals:
            through_id = sub.get("through_session_id")
            if through_id is not None:
                cutoff = max(cutoff, int(through_id))
            chunk = [s for s in remaining if int(s["id"]) <= cutoff]
            remaining = [s for s in remaining if int(s["id"]) > cutoff]
            for session in chunk:
                columns.append(self._session_column(session))
            columns.append(self._subtotal_column(sub))
        for session in remaining:
            columns.append(self._session_column(session))
        return columns

    def _session_column(self, session: dict[str, Any]) -> dict[str, Any]:
        """JSON column descriptor for a live-class session.

        Args:
            session: Sessions row.
        """
        return {
            "kind": "session",
            "id": int(session["id"]),
            "header_label": session["header_label"],
            "status": session["status"],
            "log_path": session.get("log_path"),
            "source": session.get("source") or "game",
        }

    def _subtotal_column(self, sub: dict[str, Any]) -> dict[str, Any]:
        """JSON column descriptor for a frozen SUBTOTAL.

        Args:
            sub: Subtotals row.
        """
        return {
            "kind": "subtotal",
            "id": int(sub["id"]),
            "header_label": sub["name"],
            "name": sub["name"],
        }

    def _ensure_session_scores(self, session_id: int, class_id: int) -> None:
        """Insert missing zero/absent cells for every student in a session.

        Args:
            session_id: Sessions primary key.
            class_id: Classes primary key.
        """
        self.conn.execute(
            """
            INSERT OR IGNORE INTO session_scores (session_id, student_id, present, points)
            SELECT ?, id, 0, 0 FROM students WHERE class_id = ?
            """,
            (session_id, class_id),
        )

    def _ensure_subtotal_scores(self, subtotal_id: int, class_id: int) -> None:
        """Insert missing zero cells for every student on a freeze.

        Args:
            subtotal_id: Subtotals primary key.
            class_id: Classes primary key.
        """
        self.conn.execute(
            """
            INSERT OR IGNORE INTO subtotal_scores (subtotal_id, student_id, points)
            SELECT ?, id, 0 FROM students WHERE class_id = ?
            """,
            (subtotal_id, class_id),
        )

    def add_student(
        self,
        class_id: int,
        first_name: str = "",
        last_display: str = "",
        sort: str = "last",
        codename: str | None = None,
    ) -> dict[str, Any]:
        """Append a roster row and zero cells on every existing column.

        Args:
            class_id: Classes primary key.
            first_name: Given name (Canvas/manual path).
            last_display: Family name as shown when sorting Last, First.
            sort: Dashboard name order to return.
            codename: LLOVES roster name. When set, last/first are not used.

        Returns:
            Updated dashboard payload.
        """
        if codename is not None and str(codename).strip():
            name = normalize_codename(codename)
            first = name
            last = ""
            canvas_id = f"codename:{name.lower()}"
            stored_codename = name
        else:
            first = first_name.strip()
            last = last_display.strip()
            if not first or not last:
                raise ValueError("First name and last name are required")
            canvas_id = f"manual-{class_id}-{int(time.time() * 1000)}"
            stored_codename = None
        self.get_class(class_id)
        try:
            with self._lock:
                student_id = self._insert_student_row(
                    class_id,
                    canvas_id=canvas_id,
                    last_display=last,
                    first_name=first,
                    codename=stored_codename,
                )
                session_ids = [
                    int(r["id"])
                    for r in self.conn.execute(
                        "SELECT id FROM sessions WHERE class_id = ?", (class_id,)
                    )
                ]
                for session_id in session_ids:
                    self.conn.execute(
                        """
                        INSERT OR IGNORE INTO session_scores
                            (session_id, student_id, present, points)
                        VALUES (?, ?, 0, 0)
                        """,
                        (session_id, student_id),
                    )
                sub_ids = [
                    int(r["id"])
                    for r in self.conn.execute(
                        "SELECT id FROM subtotals WHERE class_id = ?", (class_id,)
                    )
                ]
                for sub_id in sub_ids:
                    self._ensure_subtotal_scores(sub_id, class_id)
                self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("That Codename is already on this roster") from exc
        return self.dashboard(class_id, sort)

    def replace_codename_roster(
        self, class_id: int, codenames: list[str], sort: str = "az"
    ) -> dict[str, Any]:
        """Sync this class roster to ``codenames`` without recreating the class.

        Students whose Codename remains keep their id and scores. New names
        are added; omitted names are deleted (blocked if they are on a live
        game team).

        Args:
            class_id: Classes primary key.
            codenames: Desired roster order (unique, 2–32 characters).
            sort: Dashboard sort to return.

        Returns:
            Updated dashboard payload.
        """
        wanted: list[str] = []
        seen: set[str] = set()
        for raw in codenames:
            name = normalize_codename(raw)
            key = name.lower()
            if key in seen:
                raise ValueError(f"Duplicate Codename: {name}")
            seen.add(key)
            wanted.append(name)
        if not wanted:
            raise ValueError("Add at least one Codename")
        self.get_class(class_id)
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT id, codename, first_name FROM students WHERE class_id = ?
                """,
                (class_id,),
            ).fetchall()
        current: dict[str, int] = {}
        for row in rows:
            label = str(row["codename"] or row["first_name"] or "").strip()
            if label:
                current[label.lower()] = int(row["id"])
        wanted_keys = {name.lower() for name in wanted}
        for key, student_id in list(current.items()):
            if key not in wanted_keys:
                self.delete_student(class_id, student_id, sort=sort)
                current.pop(key, None)
        for name in wanted:
            if name.lower() not in current:
                self.add_student(class_id, codename=name, sort=sort)
        return self.dashboard(class_id, sort)

    def delete_student(self, class_id: int, student_id: int, sort: str = "last") -> dict[str, Any]:
        """Remove a roster row unless they are on a live game team.

        Args:
            class_id: Classes primary key.
            student_id: Students primary key.
            sort: Dashboard name order to return.

        Returns:
            Updated dashboard payload.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT id FROM students WHERE id = ? AND class_id = ?",
                (student_id, class_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"student {student_id}")
            live = self.conn.execute(
                """
                SELECT m.student_id
                FROM game_memberships m
                JOIN games g ON g.id = m.game_id
                WHERE g.class_id = ? AND g.status = 'live' AND m.student_id = ?
                """,
                (class_id, student_id),
            ).fetchone()
            if live:
                raise ValueError("Cannot remove a student who is on a live game team")
            self.conn.execute("DELETE FROM students WHERE id = ?", (student_id,))
            self.conn.commit()
        return self.dashboard(class_id, sort)

    def add_session_column(
        self,
        class_id: int,
        meeting_date: date,
        time_label: str | None = None,
        sort: str = "last",
    ) -> dict[str, Any]:
        """Add an empty live-class column at a chosen date and time.

        Args:
            class_id: Classes primary key.
            meeting_date: Calendar day for the column.
            time_label: One of the wizard times; defaults to the class time.
            sort: Dashboard name order to return.

        Returns:
            Updated dashboard payload.
        """
        cls = self.get_class(class_id)
        label = (time_label or cls["time"]).strip()
        if label not in TIME_OPTIONS:
            raise ValueError(f"Time must be one of: {', '.join(TIME_OPTIONS)}")
        meeting = datetime.combine(meeting_date, parse_time_label(label))
        with self._lock:
            header = self._label_for_meeting(class_id, cls, meeting, time_label=label)
            self._insert_session(
                class_id, meeting, header, status="template", source="manual"
            )
            self.conn.commit()
        return self.dashboard(class_id, sort)

    def delete_session_column(
        self,
        class_id: int,
        session_id: int,
        sort: str = "last",
    ) -> dict[str, Any]:
        """Delete a live-class column that is not used by an open game.

        Args:
            class_id: Classes primary key.
            session_id: Sessions primary key.
            sort: Dashboard name order to return.

        Returns:
            Updated dashboard payload.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT id FROM sessions WHERE id = ? AND class_id = ?",
                (session_id, class_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"session {session_id}")
            open_game = self.conn.execute(
                """
                SELECT * FROM games
                WHERE class_id = ? AND session_id = ? AND status != 'ended'
                """,
                (class_id, session_id),
            ).fetchone()
            if open_game:
                if open_game["status"] == "live":
                    raise ValueError("Cannot delete the column used by the open game")
                self._discard_setup_unlocked(open_game)
                leftover = self.conn.execute(
                    "SELECT id FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
                if leftover is None:
                    self.conn.commit()
                    return self.dashboard(class_id, sort)
            self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self.conn.commit()
        return self.dashboard(class_id, sort)

    def freeze_subtotal(
        self,
        class_id: int,
        name: str | None = None,
        sort: str = "last",
    ) -> dict[str, Any]:
        """Snapshot current TOTAL into a frozen column after the last class.

        The live SUBTOTAL column then only sums newer live-class columns.
        TOTAL SCORE still sums every live-class column since the course start.

        Args:
            class_id: Classes primary key.
            name: Optional header; defaults to SUBTOTAL, SUBTOTAL 2, …
            sort: Dashboard name order to return.

        Returns:
            Updated dashboard payload.
        """
        self.get_class(class_id)
        with self._lock:
            last = self.conn.execute(
                """
                SELECT * FROM sessions
                WHERE class_id = ?
                ORDER BY starts_at DESC, id DESC
                LIMIT 1
                """,
                (class_id,),
            ).fetchone()
            if last is None:
                raise ValueError("Add a live-class column before logging a subtotal")
            label = self._unique_subtotal_name(class_id, name)
            self.conn.execute(
                """
                INSERT INTO subtotals (
                    class_id, name, through_session_id, through_starts_at, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    class_id,
                    label,
                    int(last["id"]),
                    str(last["starts_at"]),
                    self._now(),
                ),
            )
            sub_id = int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            totals = {
                int(r["student_id"]): as_points(r["total"])
                for r in self.conn.execute(
                    """
                    SELECT s.id AS student_id, COALESCE(SUM(ss.points), 0) AS total
                    FROM students s
                    LEFT JOIN session_scores ss ON ss.student_id = s.id
                    WHERE s.class_id = ?
                    GROUP BY s.id
                    """,
                    (class_id,),
                )
            }
            for sid, pts in totals.items():
                self.conn.execute(
                    """
                    INSERT INTO subtotal_scores (subtotal_id, student_id, points)
                    VALUES (?, ?, ?)
                    """,
                    (sub_id, sid, pts),
                )
            self.conn.commit()
        return self.dashboard(class_id, sort)

    def rename_subtotal(
        self,
        class_id: int,
        subtotal_id: int,
        name: str,
        sort: str = "last",
    ) -> dict[str, Any]:
        """Rename a frozen SUBTOTAL column header.

        Args:
            class_id: Classes primary key.
            subtotal_id: Subtotals primary key.
            name: New header text.
            sort: Dashboard name order to return.

        Returns:
            Updated dashboard payload.
        """
        label = name.strip()
        if not label:
            raise ValueError("Subtotal name cannot be empty")
        with self._lock:
            cur = self.conn.execute(
                "UPDATE subtotals SET name = ? WHERE id = ? AND class_id = ?",
                (label, subtotal_id, class_id),
            )
            if cur.rowcount != 1:
                raise KeyError(f"subtotal {subtotal_id}")
            self.conn.commit()
        return self.dashboard(class_id, sort)

    def delete_subtotal(
        self,
        class_id: int,
        subtotal_id: int,
        sort: str = "last",
    ) -> dict[str, Any]:
        """Remove a frozen SUBTOTAL column from the class sheet.

        Session scores stay put. Live SUBTOTAL then follows the latest
        remaining freeze, or every class column if none are left.

        Args:
            class_id: Classes primary key.
            subtotal_id: Subtotals primary key.
            sort: Dashboard name order to return.

        Returns:
            Updated dashboard payload.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT id FROM subtotals WHERE id = ? AND class_id = ?",
                (subtotal_id, class_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"subtotal {subtotal_id}")
            self.conn.execute("DELETE FROM subtotals WHERE id = ?", (subtotal_id,))
            self.conn.commit()
        return self.dashboard(class_id, sort)

    def _unique_subtotal_name(self, class_id: int, name: str | None) -> str:
        """Pick a non-empty freeze label that is unique in this class.

        Args:
            class_id: Classes primary key.
            name: Teacher-supplied name, or None for SUBTOTAL.
        """
        raw = (name or "").strip() or "SUBTOTAL"
        existing = {
            str(r["name"])
            for r in self.conn.execute(
                "SELECT name FROM subtotals WHERE class_id = ?", (class_id,)
            )
        }
        if raw not in existing:
            return raw
        n = 2
        while f"{raw} {n}" in existing:
            n += 1
        return f"{raw} {n}"

    def _header_set(self, class_id: int, exclude_id: int | None = None) -> set[str]:
        """Headers already used by this class.

        Args:
            class_id: Classes primary key.
            exclude_id: Session id to ignore (when retargeting).
        """
        rows = self.conn.execute(
            "SELECT id, header_label FROM sessions WHERE class_id = ?",
            (class_id,),
        ).fetchall()
        return {
            str(row["header_label"])
            for row in rows
            if exclude_id is None or int(row["id"]) != exclude_id
        }

    def _meeting_datetime(
        self,
        cls: dict[str, Any],
        *,
        today: date | None,
        meeting_date: date | None,
    ) -> datetime:
        """Resolve the session start: override date, else next calendar slot.

        Does not skip forward just because a column already exists for that
        slot. Manual dates may fall on any weekday.

        Args:
            cls: Class row dict.
            today: Reference date for the default next meeting.
            meeting_date: Teacher-chosen calendar date, if any.
        """
        clock = parse_time_label(cls["time"])
        if meeting_date is not None:
            return datetime.combine(meeting_date, clock)
        return next_meeting_datetime(cls["days"], cls["time"], today=today)

    def _label_for_meeting(
        self,
        class_id: int,
        cls: dict[str, Any],
        meeting: datetime,
        exclude_id: int | None = None,
        time_label: str | None = None,
    ) -> str:
        """Build a unique header for ``meeting`` (``_2``, ``_3``, … if needed).

        Args:
            class_id: Classes primary key.
            cls: Class row dict.
            meeting: Session start.
            exclude_id: Session being retargeted, if any.
            time_label: Wizard time string; defaults to the class start time.
        """
        base = format_header_label(meeting, time_label or cls["time"])
        return unique_header_label(base, self._header_set(class_id, exclude_id))

    def _unused_template_session(self, class_id: int) -> dict[str, Any] | None:
        """Template column that has never been scored — safe to retarget.

        Args:
            class_id: Classes primary key.
        """
        row = self.conn.execute(
            """
            SELECT * FROM sessions
            WHERE class_id = ? AND status = 'template'
              AND COALESCE(source, 'game') = 'game'
            ORDER BY id ASC
            LIMIT 1
            """,
            (class_id,),
        ).fetchone()
        if row is None:
            return None
        used = self.conn.execute(
            """
            SELECT COUNT(*) AS n FROM session_scores
            WHERE session_id = ? AND (present = 1 OR points != 0)
            """,
            (row["id"],),
        ).fetchone()
        if int(used["n"]) > 0:
            return None
        return dict(row)

    def _insert_session(
        self,
        class_id: int,
        meeting: datetime,
        header: str,
        status: str = "template",
        source: str = "game",
    ) -> dict[str, Any]:
        """Insert a session column and zero cells.

        Args:
            class_id: Classes primary key.
            meeting: Session start.
            header: Unique header label.
            status: ``template`` or ``active``.
            source: ``game`` (Begin a New Game) or ``manual`` (added column).
        """
        self.conn.execute(
            """
            INSERT INTO sessions (
                class_id, starts_at, header_label, status, log_path, source
            )
            VALUES (?, ?, ?, ?, NULL, ?)
            """,
            (class_id, meeting.isoformat(), header, status, source),
        )
        session_id = int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        self._ensure_session_scores(session_id, class_id)
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row)

    def begin_game(
        self,
        class_id: int,
        today: date | None = None,
        meeting_date: date | None = None,
    ) -> dict[str, Any]:
        """Start a new game, or resume only if one is already live.

        An unfinished Begin a New Game (attendance / teams / names) is
        discarded so the teacher always lands on Mark Attendance. A live
        game still resumes. Default slot is the next calendar class time
        from ``today``, even if a column already exists for that meeting.
        Extra plays use ``_2``, ``_3``, … rather than bumping the day.

        Args:
            class_id: Classes primary key.
            today: Optional reference date.
            meeting_date: Optional teacher override (any calendar day).

        Returns:
            Full game-state payload.
        """
        cls = self.get_class(class_id)
        with self._lock:
            existing = self.conn.execute(
                """
                SELECT * FROM games
                WHERE class_id = ? AND status != 'ended'
                """,
                (class_id,),
            ).fetchone()
            if existing:
                if existing["status"] == "live":
                    self._set_scoreboard_game(int(existing["id"]))
                    self._set_current_class(class_id)
                    self.conn.commit()
                    return self.game_state(class_id, game_id=int(existing["id"]))
                self._discard_setup_unlocked(existing)
            self._set_scoreboard_game(None)
            self._set_current_class(class_id)

            meeting = self._meeting_datetime(cls, today=today, meeting_date=meeting_date)
            unused = self._unused_template_session(class_id)
            if unused:
                frozen_through = self.conn.execute(
                    "SELECT 1 FROM subtotals WHERE through_session_id = ?",
                    (int(unused["id"]),),
                ).fetchone()
                if frozen_through:
                    unused = None
            if unused:
                header = self._label_for_meeting(
                    class_id, cls, meeting, exclude_id=int(unused["id"])
                )
                self.conn.execute(
                    """
                    UPDATE sessions
                    SET starts_at = ?, header_label = ?, status = 'active'
                    WHERE id = ?
                    """,
                    (meeting.isoformat(), header, unused["id"]),
                )
                session_id = int(unused["id"])
                owns_session = 0
                self._ensure_session_scores(session_id, class_id)
            else:
                header = self._label_for_meeting(class_id, cls, meeting)
                session = self._insert_session(class_id, meeting, header, status="active")
                session_id = int(session["id"])
                owns_session = 1
            cur = self.conn.execute(
                """
                INSERT INTO games (
                    class_id, session_id, status, created_at, owns_session
                )
                VALUES (?, ?, 'attendance', ?, ?)
                """,
                (class_id, session_id, self._now(), owns_session),
            )
            game_id = int(cur.lastrowid)
            self.conn.commit()
        return self.game_state(class_id, game_id=game_id)

    def set_meeting_date(
        self,
        class_id: int,
        meeting_date: date,
        time_label: str | None = None,
        today: date | None = None,
    ) -> dict[str, Any]:
        """Move the open setup session to a teacher-chosen date and time.

        Allowed until Create Teams. Suffixes the header if that slot already
        has a column. Does not change the class's usual schedule.

        Args:
            class_id: Classes primary key.
            meeting_date: Chosen calendar date (today or later).
            time_label: One of the wizard times; defaults to the class time.
            today: Reference date for the past-date check (tests inject this).

        Returns:
            Updated game state.
        """
        cls = self.get_class(class_id)
        # Attendance logs may target any day in the semester window (not only today+).
        try:
            from schedule import load_semester_json
        except ImportError:
            load_semester_json = None  # type: ignore[assignment]
        first_day = None
        if load_semester_json:
            payload = load_semester_json() or {}
            raw = (payload.get("instructional") or {}).get("first_day_of_school")
            if raw:
                try:
                    first_day = date.fromisoformat(str(raw)[:10])
                except ValueError:
                    first_day = None
        if first_day and meeting_date < first_day:
            raise ValueError("Choose a date on or after the first instructional day")
        if not first_day and meeting_date < (today or date.today()):
            raise ValueError("Choose today or a future date")
        label = (time_label or cls["time"]).strip()
        if label not in TIME_OPTIONS:
            raise ValueError(f"Time must be one of: {', '.join(TIME_OPTIONS)}")
        with self._lock:
            game = self._game_row(class_id)
            if game["status"] not in {"attendance", "teams", "names"}:
                raise ValueError("Date can only be changed during setup")
            meeting = datetime.combine(meeting_date, parse_time_label(label))
            header = self._label_for_meeting(
                class_id,
                cls,
                meeting,
                exclude_id=int(game["session_id"]),
                time_label=label,
            )
            self.conn.execute(
                """
                UPDATE sessions
                SET starts_at = ?, header_label = ?
                WHERE id = ?
                """,
                (meeting.isoformat(), header, game["session_id"]),
            )
            self.conn.commit()
            # #region agent log
            try:
                import json as _json, time as _time
                with open(
                    "/Users/shawnscomputer/Documents/LLOVES-School/.cursor/debug-436036.log",
                    "a",
                    encoding="utf-8",
                ) as _dbg:
                    _dbg.write(
                        _json.dumps(
                            {
                                "sessionId": "436036",
                                "hypothesisId": "D",
                                "location": "db.py:set_meeting_date",
                                "message": "set_meeting_date applied",
                                "data": {
                                    "class_id": class_id,
                                    "requested": meeting_date.isoformat(),
                                    "session_id": int(game["session_id"]),
                                    "starts_at": meeting.isoformat(),
                                    "header": header,
                                    "game_status": str(game["status"]),
                                },
                                "timestamp": int(_time.time() * 1000),
                            }
                        )
                        + "\n"
                    )
            except Exception:
                pass
            # #endregion
        return self.game_state(class_id)

    def cancel_setup(self, class_id: int) -> dict[str, Any]:
        """Abort setup or Quit a live game without keeping scores.

        Drops the in-progress game. A session this begin created is removed;
        a retargeted empty template column is restored. Live scores are not
        written as an ended class column.

        Args:
            class_id: Classes primary key.

        Returns:
            ``{ok, class_id}``.
        """
        with self._lock:
            game = self._game_row(class_id)
            if game["status"] not in {"attendance", "teams", "names", "rounds", "live"}:
                raise ValueError("Quit is only available during an open game")
            self._discard_setup_unlocked(game)
            self.conn.commit()
        return {"ok": True, "class_id": class_id}

    def _discard_setup_unlocked(self, game: sqlite3.Row) -> None:
        """Drop an unfinished setup game. Caller holds the lock and commits.

        Args:
            game: Open games row whose status is attendance, teams, or names.
        """
        game_id = int(game["id"])
        session_id = int(game["session_id"])
        owns = int(game["owns_session"] or 0)
        self.conn.execute("DELETE FROM point_events WHERE game_id = ?", (game_id,))
        self.conn.execute("DELETE FROM game_memberships WHERE game_id = ?", (game_id,))
        self.conn.execute("DELETE FROM team_buckets WHERE game_id = ?", (game_id,))
        self.conn.execute("DELETE FROM game_teams WHERE game_id = ?", (game_id,))
        if self._scoreboard_game_id() == game_id:
            self._set_scoreboard_game(None)
        self.conn.execute("DELETE FROM games WHERE id = ?", (game_id,))
        if owns:
            self.conn.execute(
                "DELETE FROM session_scores WHERE session_id = ?", (session_id,)
            )
            self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        else:
            self.conn.execute(
                """
                UPDATE session_scores
                SET present = 0, points = 0, points_r1 = 0, points_r2 = 0, points_r3 = 0
                WHERE session_id = ?
                """,
                (session_id,),
            )
            self.conn.execute(
                """
                UPDATE sessions
                SET status = 'template', log_path = NULL
                WHERE id = ?
                """,
                (session_id,),
            )

    def _game_row(self, class_id: int, game_id: int | None = None) -> sqlite3.Row:
        """Load the open game, or a specific game id.

        Args:
            class_id: Classes primary key.
            game_id: Optional games primary key.

        Raises:
            KeyError: If no matching game exists.
        """
        if game_id is not None:
            row = self.conn.execute(
                "SELECT * FROM games WHERE id = ? AND class_id = ?",
                (game_id, class_id),
            ).fetchone()
        else:
            row = self.conn.execute(
                """
                SELECT * FROM games
                WHERE class_id = ? AND status != 'ended'
                """,
                (class_id,),
            ).fetchone()
        if row is None:
            raise KeyError("no open game")
        return row

    def previous_present_ids(self, class_id: int, session_id: int) -> list[int]:
        """Present student ids from the previous session column.

        First session defaults to all absent (empty list).

        Args:
            class_id: Classes primary key.
            session_id: Current session id.
        """
        with self._lock:
            current = self.conn.execute(
                "SELECT starts_at FROM sessions WHERE id = ? AND class_id = ?",
                (session_id, class_id),
            ).fetchone()
            if current is None:
                return []
            prev = self.conn.execute(
                """
                SELECT id FROM sessions
                WHERE class_id = ? AND id != ?
                  AND starts_at <= ?
                ORDER BY starts_at DESC, id DESC
                LIMIT 1
                """,
                (class_id, session_id, current["starts_at"]),
            ).fetchone()
            if prev is None:
                return []
            rows = self.conn.execute(
                """
                SELECT student_id FROM session_scores
                WHERE session_id = ? AND present = 1
                """,
                (prev["id"],),
            ).fetchall()
        return [int(r["student_id"]) for r in rows]

    def save_attendance(self, class_id: int, present_ids: list[int]) -> dict[str, Any]:
        """Mark present/absent on the open game's session and advance to teams.

        Args:
            class_id: Classes primary key.
            present_ids: Student ids who are present.

        Returns:
            Updated game state.
        """
        present_set = {int(x) for x in present_ids}
        with self._lock:
            game = self._game_row(class_id)
            if game["status"] not in {"attendance", "teams", "names"}:
                raise ValueError("Attendance can only be edited during setup")
            if not present_set:
                raise ValueError("Mark at least one student present")
            self._write_attendance_unlocked(game, present_set)
            # Drop any teams from a previous pass through this setup.
            self.conn.execute(
                "DELETE FROM game_memberships WHERE game_id = ?", (game["id"],)
            )
            self.conn.execute(
                "DELETE FROM team_buckets WHERE game_id = ?", (game["id"],)
            )
            self.conn.execute(
                "DELETE FROM game_teams WHERE game_id = ?", (game["id"],)
            )
            self.conn.execute(
                "UPDATE games SET status = 'teams' WHERE id = ?", (game["id"],)
            )
            self.conn.commit()
        return self.game_state(class_id)

    def _write_attendance_unlocked(
        self, game: sqlite3.Row, present_set: set[int]
    ) -> None:
        """Persist present/absent flags for every roster student.

        Caller holds the lock. Does not change ``games.status``.

        Args:
            game: Open games row.
            present_set: Student ids marked present.
        """
        session_id = int(game["session_id"])
        class_id = int(game["class_id"])
        students = self.conn.execute(
            "SELECT id FROM students WHERE class_id = ?", (class_id,)
        ).fetchall()
        for row in students:
            sid = int(row["id"])
            is_present = 1 if sid in present_set else 0
            self.conn.execute(
                """
                UPDATE session_scores
                SET present = ?,
                    points = CASE WHEN ? = 0 THEN 0 ELSE points END,
                    points_r1 = CASE WHEN ? = 0 THEN 0 ELSE points_r1 END,
                    points_r2 = CASE WHEN ? = 0 THEN 0 ELSE points_r2 END,
                    points_r3 = CASE WHEN ? = 0 THEN 0 ELSE points_r3 END
                WHERE session_id = ? AND student_id = ?
                """,
                (
                    is_present,
                    is_present,
                    is_present,
                    is_present,
                    is_present,
                    session_id,
                    sid,
                ),
            )

    def finalize_attendance_only(
        self, class_id: int, present_ids: list[int]
    ) -> dict[str, Any]:
        """Save today's attendance and close without teams or live scoring.

        Writes present/absent into the week grid column, ends the session, and
        clears the open game so Take Attendance can start fresh next time.

        Args:
            class_id: Classes primary key.
            present_ids: Student ids who are present.

        Returns:
            ``{ok, class_id, session_id}``.
        """
        present_set = {int(x) for x in present_ids}
        with self._lock:
            game = self._game_row(class_id)
            if game["status"] not in {"attendance", "teams", "names"}:
                raise ValueError("Attendance-only finish is only available during setup")
            if not present_set:
                raise ValueError("Mark at least one student present")
            self._write_attendance_unlocked(game, present_set)
            # #region agent log
            try:
                import json as _json, time as _time
                sess = self.conn.execute(
                    "SELECT id, starts_at, status, header_label FROM sessions WHERE id = ?",
                    (int(game["session_id"]),),
                ).fetchone()
                with open(
                    "/Users/shawnscomputer/Documents/LLOVES-School/.cursor/debug-436036.log",
                    "a",
                    encoding="utf-8",
                ) as _dbg:
                    _dbg.write(
                        _json.dumps(
                            {
                                "sessionId": "436036",
                                "hypothesisId": "E",
                                "location": "db.py:finalize_attendance_only",
                                "message": "finalize writing attendance",
                                "data": {
                                    "class_id": class_id,
                                    "session_id": int(game["session_id"]),
                                    "starts_at": str(sess["starts_at"]) if sess else None,
                                    "header": str(sess["header_label"]) if sess else None,
                                    "present_count": len(present_set),
                                    "game_status": str(game["status"]),
                                },
                                "timestamp": int(_time.time() * 1000),
                            }
                        )
                        + "\n"
                    )
            except Exception:
                pass
            # #endregion
            # Drop any partial team setup; attendance column stays.
            self.conn.execute(
                "DELETE FROM point_events WHERE game_id = ?", (game["id"],)
            )
            self.conn.execute(
                "DELETE FROM game_memberships WHERE game_id = ?", (game["id"],)
            )
            self.conn.execute(
                "DELETE FROM team_buckets WHERE game_id = ?", (game["id"],)
            )
            self.conn.execute(
                "DELETE FROM game_teams WHERE game_id = ?", (game["id"],)
            )
            session_id = int(game["session_id"])
            game_id = int(game["id"])
            self.conn.execute(
                """
                UPDATE sessions
                SET status = 'ended', log_path = NULL
                WHERE id = ?
                """,
                (session_id,),
            )
            self.conn.execute(
                "UPDATE games SET status = 'ended' WHERE id = ?", (game_id,)
            )
            if self._scoreboard_game_id() == game_id:
                self._set_scoreboard_game(None)
            self.conn.commit()
        return {"ok": True, "class_id": class_id, "session_id": session_id}

    def attendance_score_rows(self, class_id: int) -> dict[str, list[dict[str, Any]]]:
        """Session meeting rows and present flags for the week grid.

        Args:
            class_id: Classes primary key.

        Returns:
            ``{sessions, scores}`` lists of plain dicts.
        """
        with self._lock:
            sessions = [
                dict(row)
                for row in self.conn.execute(
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
                for row in self.conn.execute(
                    """
                    SELECT ss.session_id, ss.student_id, ss.present
                    FROM session_scores ss
                    JOIN sessions se ON se.id = ss.session_id
                    WHERE se.class_id = ?
                    """,
                    (int(class_id),),
                )
            ]
        return {"sessions": sessions, "scores": scores}

    def set_setup_step(self, class_id: int, status: str) -> dict[str, Any]:
        """Move the open game between attendance / teams / names.

        Args:
            class_id: Classes primary key.
            status: ``attendance``, ``teams``, or ``names``.

        Returns:
            Updated game state.
        """
        if status not in {"attendance", "teams", "names", "rounds"}:
            raise ValueError("Setup step must be attendance, teams, names, or rounds")
        with self._lock:
            game = self._game_row(class_id)
            if game["status"] not in {"attendance", "teams", "names", "rounds", "live"}:
                raise ValueError("Cannot change setup step after End Game")
            if game["status"] == "live":
                raise ValueError("Cannot return to setup during a live game")
            if status == "names":
                n_teams = self.conn.execute(
                    "SELECT COUNT(*) AS n FROM game_teams WHERE game_id = ?",
                    (game["id"],),
                ).fetchone()
                if int(n_teams["n"]) < 1:
                    raise ValueError("Assign teams before renaming them")
            if status == "rounds":
                n_teams = self.conn.execute(
                    "SELECT COUNT(*) AS n FROM game_teams WHERE game_id = ?",
                    (game["id"],),
                ).fetchone()
                if int(n_teams["n"]) < 1:
                    raise ValueError("Create teams before setting up rounds")
            self.conn.execute(
                "UPDATE games SET status = ? WHERE id = ?", (status, game["id"])
            )
            self.conn.commit()
        return self.game_state(class_id)

    def assign_teams(
        self,
        class_id: int,
        n_teams: int,
        mode: str,
        rng: Any | None = None,
        assignments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Build teams from present students (random, balanced, or manual).

        Args:
            class_id: Classes primary key.
            n_teams: Number of teams.
            mode: ``random``, ``balanced``, or ``manual``.
            rng: Optional random.Random for tests.
            assignments: Required for ``manual``: ``{student_id, team_index}``.

        Returns:
            Updated game state including team names for the rename step.
        """
        with self._lock:
            game = self._game_row(class_id)
            if game["status"] not in {"teams", "names"}:
                raise ValueError("Assign teams during the setup wizard")
            session_id = int(game["session_id"])
            present_rows = self.conn.execute(
                """
                SELECT s.id AS id, s.last_display AS last_display, s.first_name AS first_name
                FROM session_scores ss
                JOIN students s ON s.id = ss.student_id
                WHERE ss.session_id = ? AND ss.present = 1
                """,
                (session_id,),
            ).fetchall()
            present_ids = [int(r["id"]) for r in present_rows]
            validate_team_count(int(n_teams), len(present_ids))
            totals_map = {}
            for sid in present_ids:
                row = self.conn.execute(
                    """
                    SELECT COALESCE(SUM(points), 0) AS total
                    FROM session_scores WHERE student_id = ?
                    """,
                    (sid,),
                ).fetchone()
                totals_map[sid] = as_points(row["total"])
            if mode == "balanced":
                buckets = assign_balanced(
                    present_ids, int(n_teams), [totals_map[sid] for sid in present_ids]
                )
            elif mode == "random":
                buckets = assign_random(present_ids, int(n_teams), rng=rng)
            elif mode == "manual":
                buckets = assign_manual(present_ids, int(n_teams), assignments or [])
            else:
                raise ValueError("mode must be random, balanced, or manual")
            game_id = int(game["id"])
            self.conn.execute("DELETE FROM game_memberships WHERE game_id = ?", (game_id,))
            self.conn.execute("DELETE FROM team_buckets WHERE game_id = ?", (game_id,))
            self.conn.execute("DELETE FROM game_teams WHERE game_id = ?", (game_id,))
            for order, members in enumerate(buckets):
                cur = self.conn.execute(
                    """
                    INSERT INTO game_teams (game_id, name, color, sort_order)
                    VALUES (?, ?, ?, ?)
                    """,
                    (game_id, default_team_name(order), color_for_team(order), order),
                )
                team_id = int(cur.lastrowid)
                self.conn.execute(
                    """
                    INSERT INTO team_buckets (game_id, team_id, points)
                    VALUES (?, ?, 0)
                    """,
                    (game_id, team_id),
                )
                for sid in members:
                    self.conn.execute(
                        """
                        INSERT INTO game_memberships (game_id, team_id, student_id)
                        VALUES (?, ?, ?)
                        """,
                        (game_id, team_id, sid),
                    )
            self.conn.execute(
                "UPDATE games SET status = 'names' WHERE id = ?", (game_id,)
            )
            self.conn.commit()
        return self.game_state(class_id)

    def rename_teams(
        self,
        class_id: int,
        names: list[dict[str, Any]],
        *,
        go_live: bool = True,
        rounds: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Apply teacher team names, optionally moving the game to live scoring.

        Args:
            class_id: Classes primary key.
            names: List of ``{id, name}`` for each team.
            go_live: When False, stay on the names/rounds setup step.
            rounds: Optional rounds plan used when going live.

        Returns:
            Updated game state.
        """
        plan = normalize_rounds_config(rounds) if go_live else None
        with self._lock:
            game = self._game_row(class_id)
            if game["status"] not in {"names", "live", "rounds"}:
                raise ValueError("Rename teams after they have been assigned")
            game_id = int(game["id"])
            for item in names:
                team_id = int(item["id"])
                name = str(item.get("name") or "").strip()
                if not name:
                    raise ValueError("Team name cannot be empty")
                cur = self.conn.execute(
                    """
                    UPDATE game_teams SET name = ?
                    WHERE id = ? AND game_id = ?
                    """,
                    (name, team_id, game_id),
                )
                if cur.rowcount != 1:
                    raise KeyError(f"team {team_id}")
            if not go_live:
                self.conn.execute(
                    "UPDATE games SET status = 'rounds' WHERE id = ?",
                    (game_id,),
                )
                self.conn.commit()
                return self.game_state(class_id)
            assert plan is not None
            going_live = game["status"] != "live"
            if going_live:
                first = plan[0]
                self.conn.execute(
                    """
                    UPDATE games
                    SET status = 'live',
                        current_round = 1,
                        round_started_at = ?,
                        round_duration_sec = ?,
                        rounds_json = ?
                    WHERE id = ?
                    """,
                    (
                        self._now(),
                        int(first["duration_sec"]),
                        json.dumps(plan),
                        game_id,
                    ),
                )
            else:
                self.conn.execute(
                    "UPDATE games SET status = 'live' WHERE id = ?", (game_id,)
                )
            self._set_scoreboard_game(game_id)
            self.conn.commit()
        return self.game_state(class_id)

    def start_live_with_rounds(
        self, class_id: int, rounds: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Start live scoring from the rounds setup step.

        Args:
            class_id: Classes primary key.
            rounds: Teacher rounds plan (1–3 unique types with lengths).

        Returns:
            Live game state.
        """
        plan = normalize_rounds_config(rounds)
        with self._lock:
            game = self._game_row(class_id)
            if game["status"] not in {"names", "rounds"}:
                raise ValueError("Set up rounds after teams are created")
            n_teams = self.conn.execute(
                "SELECT COUNT(*) AS n FROM game_teams WHERE game_id = ?",
                (game["id"],),
            ).fetchone()
            if int(n_teams["n"]) < 1:
                raise ValueError("Create teams before starting rounds")
            first = plan[0]
            self.conn.execute(
                """
                UPDATE games
                SET status = 'live',
                    current_round = 1,
                    round_started_at = ?,
                    round_duration_sec = ?,
                    rounds_json = ?
                WHERE id = ?
                """,
                (
                    self._now(),
                    int(first["duration_sec"]),
                    json.dumps(plan),
                    int(game["id"]),
                ),
            )
            self._set_scoreboard_game(int(game["id"]))
            self.conn.commit()
        return self.game_state(class_id)

    def start_ungamified_live(self, class_id: int) -> dict[str, Any]:
        """Go live with one implicit team so individual +/- scoring works.

        Used when the teacher declines Gamify. Present students (or the
        whole roster if none are marked) sit on a single team named Class.
        The scoreboard is not opened.

        Args:
            class_id: Classes primary key.

        Returns:
            Game state with status ``live``.
        """
        with self._lock:
            game = self._game_row(class_id)
            if game["status"] == "live":
                return self.game_state(class_id, game_id=int(game["id"]))
            if game["status"] not in {"attendance", "teams", "names"}:
                raise ValueError("Start scoring after attendance is marked")
            session_id = int(game["session_id"])
            present_rows = self.conn.execute(
                """
                SELECT student_id FROM session_scores
                WHERE session_id = ? AND present = 1
                """,
                (session_id,),
            ).fetchall()
            present_ids = [int(r["student_id"]) for r in present_rows]
            if not present_ids:
                present_ids = [
                    int(r["id"])
                    for r in self.conn.execute(
                        "SELECT id FROM students WHERE class_id = ? ORDER BY id",
                        (class_id,),
                    ).fetchall()
                ]
                for sid in present_ids:
                    self.conn.execute(
                        """
                        UPDATE session_scores SET present = 1
                        WHERE session_id = ? AND student_id = ?
                        """,
                        (session_id, sid),
                    )
            if not present_ids:
                raise ValueError("Mark at least one student present")
            game_id = int(game["id"])
            self.conn.execute("DELETE FROM game_memberships WHERE game_id = ?", (game_id,))
            self.conn.execute("DELETE FROM team_buckets WHERE game_id = ?", (game_id,))
            self.conn.execute("DELETE FROM game_teams WHERE game_id = ?", (game_id,))
            cur = self.conn.execute(
                """
                INSERT INTO game_teams (game_id, name, color, sort_order)
                VALUES (?, ?, ?, ?)
                """,
                (game_id, "Class", color_for_team(0), 0),
            )
            team_id = int(cur.lastrowid)
            self.conn.execute(
                "INSERT INTO team_buckets (game_id, team_id, points) VALUES (?, ?, 0)",
                (game_id, team_id),
            )
            for sid in present_ids:
                self.conn.execute(
                    """
                    INSERT INTO game_memberships (game_id, team_id, student_id)
                    VALUES (?, ?, ?)
                    """,
                    (game_id, team_id, sid),
                )
            self.conn.execute(
                """
                UPDATE games
                SET status = 'live',
                    current_round = 1,
                    round_started_at = ?,
                    round_duration_sec = ?,
                    rounds_json = ?
                WHERE id = ?
                """,
                (
                    self._now(),
                    ROUND_DURATIONS_SEC[1],
                    json.dumps(normalize_rounds_config(None)),
                    game_id,
                ),
            )
            self._set_scoreboard_game(None)
            self.conn.commit()
        return self.game_state(class_id)

    def add_late_student(
        self,
        class_id: int,
        student_id: int,
        team_id: int,
    ) -> dict[str, Any]:
        """Put an absent roster student on a live team at zero points.

        Marks them present for this lesson and inserts a membership.
        Does not change the ESPN team-only bucket or other students.

        Args:
            class_id: Classes primary key.
            student_id: Students primary key on this roster.
            team_id: Existing team in the live game.

        Returns:
            Updated game state.
        """
        with self._lock:
            game = self._game_row(class_id)
            if game["status"] != "live":
                raise ValueError("Add a late student during a live game")
            student = self.conn.execute(
                "SELECT id FROM students WHERE id = ? AND class_id = ?",
                (student_id, class_id),
            ).fetchone()
            if student is None:
                raise KeyError(f"student {student_id}")
            team = self.conn.execute(
                "SELECT id FROM game_teams WHERE id = ? AND game_id = ?",
                (team_id, int(game["id"])),
            ).fetchone()
            if team is None:
                raise KeyError(f"team {team_id}")
            already = self.conn.execute(
                """
                SELECT student_id FROM game_memberships
                WHERE game_id = ? AND student_id = ?
                """,
                (int(game["id"]), student_id),
            ).fetchone()
            if already:
                raise ValueError("That student is already on a team")
            session_id = int(game["session_id"])
            score = self.conn.execute(
                """
                SELECT present FROM session_scores
                WHERE session_id = ? AND student_id = ?
                """,
                (session_id, student_id),
            ).fetchone()
            if score is not None and int(score["present"] or 0):
                raise ValueError("That student is already marked present")
            if score is None:
                self.conn.execute(
                    """
                    INSERT INTO session_scores (
                        session_id, student_id, present,
                        points, points_r1, points_r2, points_r3
                    )
                    VALUES (?, ?, 1, 0, 0, 0, 0)
                    """,
                    (session_id, student_id),
                )
            else:
                self.conn.execute(
                    """
                    UPDATE session_scores
                    SET present = 1, points = 0,
                        points_r1 = 0, points_r2 = 0, points_r3 = 0
                    WHERE session_id = ? AND student_id = ?
                    """,
                    (session_id, student_id),
                )
            self.conn.execute(
                """
                INSERT INTO game_memberships (game_id, team_id, student_id)
                VALUES (?, ?, ?)
                """,
                (int(game["id"]), team_id, student_id),
            )
            self.conn.commit()
        return self.game_state(class_id)

    def start_round(self, class_id: int, round_number: int) -> dict[str, Any]:
        """Advance the live game to the next configured round.

        Only the next round in the teacher plan can be started. The timer
        hitting 0:00 does not advance the round.

        Args:
            class_id: Classes primary key.
            round_number: 1-based index into the configured rounds list.

        Returns:
            Updated game state with a fresh countdown.
        """
        target = int(round_number)
        with self._lock:
            game = self._game_row(class_id)
            if game["status"] != "live":
                raise ValueError("Rounds start after Create Teams")
            plan = rounds_from_game_row(game)
            if target < 2 or target > len(plan):
                raise ValueError(f"round must be between 2 and {len(plan)}")
            current = int(game["current_round"] or 1)
            if target != current + 1:
                raise ValueError(f"Cannot skip to round {target}")
            nxt = plan[target - 1]
            self.conn.execute(
                """
                UPDATE games
                SET current_round = ?,
                    round_started_at = ?,
                    round_duration_sec = ?
                WHERE id = ?
                """,
                (target, self._now(), int(nxt["duration_sec"]), int(game["id"])),
            )
            self.conn.commit()
        return self.game_state(class_id)

    def _append_log(self, session_id: int, record: dict[str, Any]) -> Path:
        """Append one JSONL scoring action under ``data/logs/``.

        Args:
            session_id: Sessions primary key.
            record: JSON-serializable log object.

        Returns:
            Path to the log file.
        """
        path = self.logs_dir / f"session-{session_id}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    def _team_member_ids(self, game_id: int, team_id: int) -> list[int]:
        """Present members of a team, stable order for split remainder.

        Args:
            game_id: Games primary key.
            team_id: Game teams primary key.
        """
        rows = self.conn.execute(
            """
            SELECT m.student_id AS student_id
            FROM game_memberships m
            JOIN students s ON s.id = m.student_id
            WHERE m.game_id = ? AND m.team_id = ?
            ORDER BY s.last_display, s.first_name, s.id
            """,
            (game_id, team_id),
        ).fetchall()
        return [int(r["student_id"]) for r in rows]

    def _credit_student(
        self, session_id: int, student_id: int, amount: float, round_n: int = 1
    ) -> None:
        """Add ``amount`` to a present student's credited session score.

        Writes both the matching ``points_rN`` bucket and ``points`` (the sum).
        Team-only buckets never call this method.

        Args:
            session_id: Sessions primary key.
            student_id: Students primary key.
            amount: Signed points (stored to one decimal place).
            round_n: Current round 1–3; credits ``points_r1`` / ``points_r2`` /
                ``points_r3``.
        """
        credit = as_points(amount)
        n = int(round_n)
        if n not in (1, 2, 3):
            raise ValueError("round must be 1, 2, or 3")
        col = f"points_r{n}"
        cur = self.conn.execute(
            f"""
            UPDATE session_scores
            SET points = ROUND(points + ?, 1),
                {col} = ROUND({col} + ?, 1)
            WHERE session_id = ? AND student_id = ? AND present = 1
            """,
            (credit, credit, session_id, student_id),
        )
        if cur.rowcount != 1:
            raise KeyError(f"student {student_id} is not present this session")

    def _bump_team_bucket(self, game_id: int, team_id: int, amount: float) -> None:
        """Add ``amount`` to the persisted team-only bucket.

        Args:
            game_id: Games primary key.
            team_id: Game teams primary key.
            amount: Points (team awards are positive; stored to one decimal).
        """
        self.conn.execute(
            """
            UPDATE team_buckets
            SET points = ROUND(points + ?, 1)
            WHERE game_id = ? AND team_id = ?
            """,
            (as_points(amount), game_id, team_id),
        )

    def _insert_event(
        self,
        *,
        session_id: int,
        game_id: int,
        seq: int,
        to_kind: str,
        to_id: int,
        amount: int,
        team_rule: str | None,
        round_n: int = 1,
    ) -> dict[str, Any]:
        """Append an immutable point event and a JSONL line.

        Args:
            session_id: Sessions primary key.
            game_id: Games primary key.
            seq: Monotonic per-game sequence.
            to_kind: ``student`` or ``team``.
            to_id: Student or team id.
            amount: Signed amount.
            team_rule: Team credit rule, or None for individual events.
            round_n: Round the award belongs to (1–3).

        Returns:
            The log record written to JSONL.
        """
        ts = self._now()
        n = int(round_n) if int(round_n) in (1, 2, 3) else 1
        self.conn.execute(
            """
            INSERT INTO point_events (
                session_id, game_id, seq, ts, from_kind, from_id,
                to_kind, to_id, amount, team_rule, "round"
            )
            VALUES (?, ?, ?, ?, 'teacher', NULL, ?, ?, ?, ?, ?)
            """,
            (session_id, game_id, seq, ts, to_kind, to_id, amount, team_rule, n),
        )
        record = {
            "ts": ts,
            "seq": seq,
            "from": "teacher",
            "to_kind": to_kind,
            "to": to_id,
            "amount": amount,
            "team_rule": team_rule,
            "round": n,
        }
        log_path = self._append_log(session_id, record)
        self.conn.execute(
            "UPDATE sessions SET log_path = ? WHERE id = ?",
            (str(log_path), session_id),
        )
        return record

    def award_points(
        self,
        class_id: int,
        *,
        kind: str,
        target_id: int,
        amount: int,
        team_rule: str | None = None,
    ) -> dict[str, Any]:
        """Apply a teacher award as an immutable event plus live caches.

        Individual awards change that student's credited score and the team's
        ESPN total. Team awards always add ``amount`` to the team total, then
        credit members according to ``team_rule``:

        * ``each_member`` — every member's credited score += amount
        * ``split_members`` — each member gets ``round(amount / n, 1)``;
          leftover tenths go on the team bucket so the ESPN total stays
          the full award
        * ``team_only`` — team bucket only; individuals unchanged
          (also used for a −5 team penalty)

        Positive team awards need a rule. A negative team award must use
        ``team_only``. Individual awards may be negative.
        Future TODO: students awarding points to one another.

        Args:
            class_id: Classes primary key.
            kind: ``student`` or ``team``.
            target_id: Student id or team id.
            amount: Signed integer (negative team awards require ``team_only``).
            team_rule: Required for team awards.

        Returns:
            Updated game state (includes last_event for the scoreboard).
        """
        if amount == 0:
            raise ValueError("Amount cannot be 0")
        if kind not in {"student", "team"}:
            raise ValueError("kind must be student or team")
        if kind == "team":
            rule = (team_rule or "").strip()
            if rule not in TEAM_RULES:
                raise ValueError(
                    "Team awards need a rule: each_member, split_members, or team_only"
                )
            if amount < 0 and rule != "team_only":
                raise ValueError("Team penalties must use the team-only bucket")
        else:
            rule = None
        with self._lock:
            game = self._game_row(class_id)
            if game["status"] != "live":
                raise ValueError("Scoring starts after teams are created")
            game_id = int(game["id"])
            session_id = int(game["session_id"])
            seq = int(game["event_seq"] or 0) + 1
            round_n = int(game["current_round"] or 1)
            plan = rounds_from_game_row(game)
            if 1 <= round_n <= len(plan):
                score_bucket = int(plan[round_n - 1]["bucket"])
            else:
                score_bucket = 1 if round_n not in (1, 2, 3) else round_n
            last_event: dict[str, Any]
            if kind == "student":
                member = self.conn.execute(
                    """
                    SELECT team_id FROM game_memberships
                    WHERE game_id = ? AND student_id = ?
                    """,
                    (game_id, target_id),
                ).fetchone()
                if member is None:
                    raise KeyError(f"student {target_id} is not on a live team")
                self._credit_student(session_id, target_id, amount, score_bucket)
                team = dict(
                    self.conn.execute(
                        "SELECT * FROM game_teams WHERE id = ?",
                        (member["team_id"],),
                    ).fetchone()
                )
                student = self.conn.execute(
                    "SELECT first_name FROM students WHERE id = ?",
                    (target_id,),
                ).fetchone()
                first_name = str(student["first_name"] if student else "").strip()
                signed = f"+{amount}" if amount > 0 else str(amount)
                last_event = {
                    "kind": "student",
                    "student_id": target_id,
                    "first_name": first_name,
                    "team_id": int(team["id"]),
                    "team_name": team["name"],
                    "amount": amount,
                    "team_rule": None,
                    "celebrate": amount > 0,
                    "label": f"{first_name} {signed}".strip() or f"{team['name']} {signed}",
                }
                self._insert_event(
                    session_id=session_id,
                    game_id=game_id,
                    seq=seq,
                    to_kind="student",
                    to_id=target_id,
                    amount=amount,
                    team_rule=None,
                    round_n=round_n,
                )
            else:
                team_row = self.conn.execute(
                    "SELECT * FROM game_teams WHERE id = ? AND game_id = ?",
                    (target_id, game_id),
                ).fetchone()
                if team_row is None:
                    raise KeyError(f"team {target_id}")
                member_ids = self._team_member_ids(game_id, target_id)
                if rule == "each_member":
                    for sid in member_ids:
                        self._credit_student(session_id, sid, amount, score_bucket)
                elif rule == "split_members":
                    shares = split_amount(amount, len(member_ids))
                    for sid, share in zip(member_ids, shares, strict=True):
                        if share:
                            self._credit_student(session_id, sid, share, score_bucket)
                    remainder = as_points(amount - sum(shares))
                    if remainder:
                        self._bump_team_bucket(game_id, target_id, remainder)
                else:
                    self._bump_team_bucket(game_id, target_id, amount)
                signed = f"+{amount}" if amount > 0 else str(amount)
                if rule == "team_only" and amount > 0:
                    caption = f"Small Team Bonus {signed}"
                else:
                    caption = f"{team_row['name']} {signed}"
                last_event = {
                    "kind": "team",
                    "team_id": target_id,
                    "team_name": team_row["name"],
                    "amount": amount,
                    "team_rule": rule,
                    "celebrate": amount > 0,
                    "caption": caption,
                    "label": caption,
                }
                self._insert_event(
                    session_id=session_id,
                    game_id=game_id,
                    seq=seq,
                    to_kind="team",
                    to_id=target_id,
                    amount=amount,
                    team_rule=rule,
                    round_n=round_n,
                )
            self.conn.execute(
                """
                UPDATE games
                SET event_seq = ?, last_event_json = ?
                WHERE id = ?
                """,
                (seq, json.dumps(last_event), game_id),
            )
            self.conn.commit()
        return self.game_state(class_id)

    def end_game(self, class_id: int) -> dict[str, Any]:
        """Close the live game; keep events, team scores, and credited cells.

        Individual credited scores are already on ``session_scores``. Team
        buckets and memberships stay so the session remains inspectable.
        Dashboard cells show credited scores only (not a blended formula).

        Args:
            class_id: Classes primary key.

        Returns:
            ``{ok, class_id, session_id, log_path}``.
        """
        with self._lock:
            game = self._game_row(class_id)
            if game["status"] != "live":
                raise ValueError("End Game is only available during a live game")
            session_id = int(game["session_id"])
            game_id = int(game["id"])
            log_path = self.logs_dir / f"session-{session_id}.jsonl"
            log_path.touch(exist_ok=True)
            self.conn.execute(
                """
                UPDATE sessions
                SET status = 'ended', log_path = ?
                WHERE id = ?
                """,
                (str(log_path), session_id),
            )
            self.conn.execute(
                "UPDATE games SET status = 'ended' WHERE id = ?", (game_id,)
            )
            self._set_scoreboard_game(game_id)
            self.conn.commit()
        return {
            "ok": True,
            "class_id": class_id,
            "session_id": session_id,
            "log_path": str(log_path),
        }

    def game_state(self, class_id: int, game_id: int | None = None) -> dict[str, Any]:
        """Full teacher-game payload (setup or live).

        Team members include ``session_points`` (the lesson sum) plus
        ``points_r1`` / ``points_r2`` / ``points_r3`` for the teacher Now
        triple. Team-only ESPN buckets are not in those fields.

        Args:
            class_id: Classes primary key.
            game_id: Optional specific game (including just-ended).
        """
        cls = self.get_class(class_id)
        with self._lock:
            game = dict(self._game_row(class_id, game_id=game_id))
            session = dict(
                self.conn.execute(
                    "SELECT * FROM sessions WHERE id = ?", (game["session_id"],)
                ).fetchone()
            )
            session["meeting_date"] = str(session["starts_at"])[:10]
            try:
                started = datetime.fromisoformat(str(session["starts_at"]))
                session["time"] = format_time_label(started.time())
            except ValueError:
                session["time"] = cls["time"]
            students = [
                dict(r)
                for r in self.conn.execute(
                    "SELECT * FROM students WHERE class_id = ? ORDER BY last_display, first_name",
                    (class_id,),
                )
            ]
            scores = {
                int(r["student_id"]): {
                    "present": bool(r["present"]),
                    "points": as_points(r["points"]),
                    "points_r1": as_points(r["points_r1"]),
                    "points_r2": as_points(r["points_r2"]),
                    "points_r3": as_points(r["points_r3"]),
                }
                for r in self.conn.execute(
                    """
                    SELECT student_id, present, points,
                           points_r1, points_r2, points_r3
                    FROM session_scores WHERE session_id = ?
                    """,
                    (session["id"],),
                )
            }
            teams = [
                dict(r)
                for r in self.conn.execute(
                    """
                    SELECT t.*, COALESCE(b.points, 0) AS bucket
                    FROM game_teams t
                    LEFT JOIN team_buckets b
                      ON b.team_id = t.id AND b.game_id = t.game_id
                    WHERE t.game_id = ?
                    ORDER BY t.sort_order
                    """,
                    (game["id"],),
                )
            ]
            memberships = [
                dict(r)
                for r in self.conn.execute(
                    """
                    SELECT student_id, team_id FROM game_memberships
                    WHERE game_id = ?
                    """,
                    (game["id"],),
                )
            ]
        totals = self.career_totals(class_id)
        team_of = {int(m["student_id"]): int(m["team_id"]) for m in memberships}
        present_ids = [int(s["id"]) for s in students if scores.get(int(s["id"]), {}).get("present")]
        default_present = self.previous_present_ids(class_id, int(session["id"]))
        last_event = None
        if game.get("last_event_json"):
            try:
                last_event = json.loads(str(game["last_event_json"]))
            except json.JSONDecodeError:
                last_event = None
        round_info = self._round_fields(game)
        teams_out: list[dict[str, Any]] = []
        for team in teams:
            members = []
            individual_sum = 0.0
            for student in students:
                sid = int(student["id"])
                if team_of.get(sid) != int(team["id"]):
                    continue
                credited = member_round_points(scores.get(sid))
                individual_sum = as_points(individual_sum + credited["session_points"])
                members.append(
                    {
                        **student,
                        **credited,
                        "career_total": as_points(totals.get(sid, 0)),
                    }
                )
            bucket = as_points(team["bucket"])
            teams_out.append(
                {
                    "id": int(team["id"]),
                    "name": team["name"],
                    "color": team["color"],
                    "sort_order": int(team["sort_order"]),
                    "bucket": bucket,
                    "individual_sum": individual_sum,
                    "score": as_points(individual_sum + bucket),
                    "members": members,
                }
            )
        return {
            "class": cls,
            "game": {
                "id": int(game["id"]),
                "status": game["status"],
                "session_id": int(game["session_id"]),
                "event_seq": int(game["event_seq"] or 0),
                "last_event": last_event,
                **round_info,
            },
            "session": session,
            "students": [
                {
                    **s,
                    "present": scores.get(int(s["id"]), {}).get("present", False),
                    **member_round_points(scores.get(int(s["id"]))),
                    "career_total": totals.get(int(s["id"]), 0),
                }
                for s in students
            ],
            "present_ids": present_ids,
            "default_present_ids": default_present,
            "teams": teams_out,
            "time_options": list(TIME_OPTIONS),
        }

    def set_current_class(self, class_id: int) -> None:
        """Remember which class the teacher is viewing.

        The one scoreboard follows this class, not a leftover live game on
        another class.

        Args:
            class_id: Classes primary key.
        """
        with self._lock:
            self._set_current_class(class_id)
            self.conn.commit()

    def set_stat_window(
        self, class_id: int, window: str, sort: str = "last"
    ) -> dict[str, Any]:
        """Persist the scoreboard stats period for this roster.

        Args:
            class_id: Classes primary key.
            window: ``last_class``, ``last_week``, or ``year``.
            sort: Dashboard sort passed through on the returned payload.
        """
        self.get_class(class_id)
        chosen = normalize_stat_window(window)
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO app_state (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (self._stat_window_key(class_id), chosen),
            )
            self.conn.commit()
        return self.dashboard(class_id, sort)

    def scoreboard(self, class_id: int | None = None) -> dict[str, Any]:
        """The one public scoreboard for the class the teacher is viewing.

        Live scores if that class has a live game. Final Score only for the
        game pinned by End Game on that same class. Begin / Quit / opening a
        different class dashboard leaves leftover boards hidden.

        Args:
            class_id: When set (LLOVES student join), pin the board to that
                class instead of the teacher's current-class pointer.
        """
        current_id = class_id if class_id is not None else self._current_class_id()
        window = (
            self._get_stat_window(current_id)
            if current_id is not None
            else DEFAULT_STAT_WINDOW
        )
        if current_id is not None:
            live_id = self._live_game_id_for_class(current_id)
            if live_id is not None:
                return self._scoreboard_payload(
                    self.game_state(current_id, game_id=live_id), live=True
                )
            pinned_id = self._scoreboard_game_id()
            if pinned_id is not None:
                pinned = self._game_row_by_id(pinned_id)
                if (
                    pinned is not None
                    and pinned["status"] == "ended"
                    and int(pinned["class_id"]) == current_id
                ):
                    return self._scoreboard_payload(
                        self.game_state(current_id, game_id=pinned_id),
                        live=False,
                    )
        idle_leaders = (
            self._scoreboard_leaders(current_id, window) if current_id is not None else []
        )
        return {
            "ok": True,
            "live": False,
            "final": False,
            "teams": [],
            "last_event": None,
            "event_seq": 0,
            "header": None,
            "status": None,
            "game_id": None,
            "class_id": current_id,
            "round": None,
            "round_title": None,
            "round_ends_at": None,
            "round_ends_at_ms": None,
            "round_remaining_sec": None,
            "leaders": idle_leaders,
            "stat_window": window,
            "stat_window_label": stat_window_label(window),
        }

    def _live_game_id_for_class(self, class_id: int) -> int | None:
        """Return the live game id for a class, if any.

        Args:
            class_id: Classes primary key.
        """
        with self._lock:
            row = self.conn.execute(
                """
                SELECT id FROM games
                WHERE class_id = ? AND status = 'live'
                ORDER BY id DESC
                LIMIT 1
                """,
                (class_id,),
            ).fetchone()
        return int(row["id"]) if row else None

    def _game_row_by_id(self, game_id: int) -> sqlite3.Row | None:
        """Load a game by primary key.

        Args:
            game_id: Games primary key.
        """
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM games WHERE id = ?", (game_id,)
            ).fetchone()

    def _scoreboard_game_id(self) -> int | None:
        """Return the pinned scoreboard game id, if any."""
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM app_state WHERE key = ?",
                (SCOREBOARD_GAME_KEY,),
            ).fetchone()
        if row is None:
            return None
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return None

    def _current_class_id(self) -> int | None:
        """Return the class the teacher last opened, if any."""
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM app_state WHERE key = ?",
                (CURRENT_CLASS_KEY,),
            ).fetchone()
        if row is None:
            return None
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return None

    def _set_current_class(self, class_id: int) -> None:
        """Write the current-class pointer. Caller holds the lock and commits.

        Args:
            class_id: Classes primary key.
        """
        self.conn.execute(
            """
            INSERT INTO app_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (CURRENT_CLASS_KEY, str(int(class_id))),
        )

    def _stat_window_key(self, class_id: int) -> str:
        """Return the ``app_state`` key for a class's stats period.

        Args:
            class_id: Classes primary key.
        """
        return f"{STAT_WINDOW_KEY_PREFIX}{int(class_id)}"

    def _get_stat_window(self, class_id: int) -> str:
        """Return the saved stats window for a class (default last class).

        Args:
            class_id: Classes primary key.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM app_state WHERE key = ?",
                (self._stat_window_key(class_id),),
            ).fetchone()
        if row is None:
            return DEFAULT_STAT_WINDOW
        raw = str(row["value"] or "").strip()
        if raw not in STAT_WINDOWS:
            return DEFAULT_STAT_WINDOW
        return raw

    def _set_scoreboard_game(self, game_id: int | None) -> None:
        """Pin the public scoreboard to a game, or clear it.

        Caller holds the lock and commits.

        Args:
            game_id: Games primary key, or ``None`` to show idle.
        """
        if game_id is None:
            self.conn.execute(
                "DELETE FROM app_state WHERE key = ?", (SCOREBOARD_GAME_KEY,)
            )
            return
        self.conn.execute(
            """
            INSERT INTO app_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (SCOREBOARD_GAME_KEY, str(int(game_id))),
        )

    def _scoreboard_players(self, team: dict[str, Any]) -> list[dict[str, str]]:
        """First names on a team for the public scoreboard.

        Args:
            team: A ``game_state`` team dict (may include full member rows).

        Returns:
            ``[{first_name}]`` sorted by first name. No last names or IDs.
        """
        names: list[dict[str, str]] = []
        for member in team.get("members") or []:
            first = str(member.get("first_name") or "").strip()
            if first:
                names.append({"first_name": first})
        names.sort(key=lambda row: row["first_name"].lower())
        return names

    def _ticker_first_name(self, student: dict[str, Any], roster: list[dict[str, Any]]) -> str:
        """First name for the public ticker; last name only when first names clash.

        Args:
            student: A students row.
            roster: Students in the same class.
        """
        first = str(student.get("first_name") or "").strip() or "?"
        key = first.lower()
        clashes = [
            row
            for row in roster
            if str(row.get("first_name") or "").strip().lower() == key
        ]
        if len(clashes) > 1:
            last = str(student.get("last_display") or "").strip()
            return f"{first} {last}".strip()
        return first

    def _leader_phrase(
        self,
        students: list[dict[str, Any]],
        roster: list[dict[str, Any]],
        score: float,
        *,
        plus: bool = False,
    ) -> str:
        """Compact 'Maya 12' / 'Maya & Jordan +4' for the ticker.

        Args:
            students: Named leaders in roster order.
            roster: Full class roster for first-name clashes.
            score: Points or improvement amount.
            plus: Prefix the number with + (Most Improved).
        """
        names = [self._ticker_first_name(row, roster) for row in students]
        pts = points_label(score)
        if plus and as_points(score) > 0:
            pts = f"+{pts}"
        if len(names) == 1:
            return f"{names[0]} {pts}"
        if len(names) == 2:
            return f"{names[0]} & {names[1]} {pts}"
        if len(names) == 3:
            return f"{names[0]}, {names[1]} & {names[2]} {pts}"
        return f"{names[0]} +{len(names) - 1} {pts}"

    def _session_has_credits(self, cells: dict[int, dict[str, Any]]) -> bool:
        """True when a lesson column has at least one present credited score.

        Args:
            cells: ``student_id`` to present/round-points for one session.
        """
        for cell in cells.values():
            if not cell.get("present"):
                continue
            if (
                as_points(cell.get("points_r1")) > 0
                or as_points(cell.get("points_r2")) > 0
                or as_points(cell.get("points_r3")) > 0
            ):
                return True
        return False

    def _present_slice_average(
        self,
        student_id: int,
        session_ids: list[int],
        by_session: dict[int, dict[int, dict[str, Any]]],
        key: str,
    ) -> float | None:
        """Mean of one slice across present lessons in the window.

        Absent lessons are skipped so missing a class does not count as
        zero. Returns ``None`` when the student was not present in any
        lesson in ``session_ids``. Team-only ESPN buckets never appear
        in these cells.

        Args:
            student_id: Students primary key.
            session_ids: Window sessions, oldest first.
            by_session: session_id → student_id → present/round points.
            key: ``points_r1``, ``points_r2``, or ``points_r3``.
        """
        values: list[float] = []
        for sid in session_ids:
            cell = by_session.get(sid, {}).get(student_id)
            if not cell or not cell.get("present"):
                continue
            values.append(as_points(cell.get(key)))
        if not values:
            return None
        return as_points(sum(values) / len(values))

    def _scoreboard_leaders(
        self, class_id: int, window: str | None = None
    ) -> list[str]:
        """Open Question / Team Challenge / Formative leaders and most improved.

        Uses scored class columns only (present + any round points > 0),
        skipping an empty in-progress game. The saved stats window picks
        last class, last two scored classes (a week), or every scored
        column this year. A student counts if present in at least one
        lesson in that window. Most Improved needs a prior period of the
        same shape (previous class, or previous two classes). This year
        has no prior window. First names only (last name on a clash).

        Args:
            class_id: Classes primary key.
            window: Optional ``last_class`` / ``last_week`` / ``year``.
                Defaults to the class's saved window.
        """
        chosen = (
            window
            if window in STAT_WINDOWS
            else self._get_stat_window(class_id)
        )
        with self._lock:
            sessions = [
                dict(row)
                for row in self.conn.execute(
                    """
                    SELECT id FROM sessions
                    WHERE class_id = ?
                    ORDER BY starts_at ASC, id ASC
                    """,
                    (class_id,),
                )
            ]
            students = [
                dict(row)
                for row in self.conn.execute(
                    """
                    SELECT id, first_name, last_display
                    FROM students WHERE class_id = ?
                    ORDER BY last_display, first_name
                    """,
                    (class_id,),
                )
            ]
            if not sessions:
                return []
            session_ids = [int(row["id"]) for row in sessions]
            placeholders = ",".join("?" * len(session_ids))
            score_rows = self.conn.execute(
                f"""
                SELECT session_id, student_id, present, points_r1, points_r2, points_r3
                FROM session_scores
                WHERE session_id IN ({placeholders})
                """,
                session_ids,
            ).fetchall()
        by_session: dict[int, dict[int, dict[str, Any]]] = {}
        for row in score_rows:
            by_session.setdefault(int(row["session_id"]), {})[int(row["student_id"])] = {
                "present": bool(row["present"]),
                "points_r1": as_points(row["points_r1"]),
                "points_r2": as_points(row["points_r2"]),
                "points_r3": as_points(row["points_r3"]),
            }
        scored_ids = [
            sid for sid in session_ids if self._session_has_credits(by_session.get(sid, {}))
        ]
        if not scored_ids:
            return []
        current_ids, prior_ids = leader_periods(scored_ids, chosen)
        if not current_ids:
            return []
        items: list[str] = []
        for key, leader_label, improved_label in LEADER_SLICES:
            scored: list[tuple[dict[str, Any], float]] = []
            for student in students:
                avg = self._present_slice_average(
                    int(student["id"]), current_ids, by_session, key
                )
                if avg is None:
                    continue
                scored.append((student, avg))
            if scored:
                top = max(pts for _s, pts in scored)
                if top > 0:
                    leaders = [s for s, pts in scored if pts == top]
                    items.append(
                        f"{leader_label} · {self._leader_phrase(leaders, students, top)}"
                    )
            if not prior_ids:
                continue
            climbs: list[tuple[dict[str, Any], float]] = []
            for student in students:
                sid = int(student["id"])
                cur_avg = self._present_slice_average(
                    sid, current_ids, by_session, key
                )
                prev_avg = self._present_slice_average(
                    sid, prior_ids, by_session, key
                )
                if cur_avg is None or prev_avg is None:
                    continue
                diff = as_points(cur_avg - prev_avg)
                climbs.append((student, diff))
            if not climbs:
                continue
            best = max(diff for _s, diff in climbs)
            if best <= 0:
                continue
            leaders = [s for s, diff in climbs if diff == best]
            items.append(
                f"{improved_label} · {self._leader_phrase(leaders, students, best, plus=True)}"
            )
        return items

    def _round_fields(self, game: dict[str, Any]) -> dict[str, Any]:
        """Round index, title, timer end, and remaining seconds.

        Timer expiry does not change ``current_round``. Remaining is never
        negative.

        Args:
            game: A games row dict (status, current_round, round_started_at, …).
        """
        plan = rounds_from_game_row(game)
        current = int(game.get("current_round") or 1)
        if current < 1 or current > len(plan):
            current = 1
        active = plan[current - 1]
        live = str(game.get("status") or "") == "live"
        started = game.get("round_started_at") if live else None
        raw_dur = game.get("round_duration_sec") if live else None
        duration_i = int(raw_dur) if raw_dur not in (None, "") else int(active["duration_sec"])
        started_s = str(started) if started else None
        return {
            "round": current,
            "round_title": str(active["title"]),
            "round_kind": str(active["kind"]),
            "round_count": len(plan),
            "rounds": plan,
            "round_started_at": started_s,
            "round_duration_sec": duration_i or None,
            "round_ends_at": round_ends_at(started_s, duration_i),
            "round_ends_at_ms": round_ends_at_ms(started_s, duration_i),
            "round_remaining_sec": round_remaining_sec(started_s, duration_i),
        }

    def _scoreboard_payload(self, state: dict[str, Any], live: bool) -> dict[str, Any]:
        """Build the public scoreboard JSON from a game-state payload.

        Args:
            state: Output of :meth:`game_state`.
            live: True when this game is in progress.
        """
        game = state["game"]
        class_id = int(state["class"]["id"])
        window = self._get_stat_window(class_id)
        payload = {
            "ok": True,
            "live": live,
            "final": not live,
            "game_id": game["id"],
            "class_id": class_id,
            "teams": [
                {
                    "id": t["id"],
                    "name": t["name"],
                    "color": t["color"],
                    "score": t["score"],
                    "players": self._scoreboard_players(t),
                }
                for t in state["teams"]
            ],
            "last_event": game["last_event"],
            "event_seq": game["event_seq"],
            "header": state["session"]["header_label"],
            "status": "live" if live else "ended",
            "round": game.get("round"),
            "round_title": game.get("round_title"),
            "round_ends_at": game.get("round_ends_at"),
            "round_ends_at_ms": game.get("round_ends_at_ms"),
            "round_remaining_sec": game.get("round_remaining_sec"),
            "leaders": self._scoreboard_leaders(class_id, window),
            "stat_window": window,
            "stat_window_label": stat_window_label(window),
        }
        return payload

    def session_log_path(self, session_id: int) -> Path:
        """Return the JSONL log path for a session if it exists.

        Args:
            session_id: Sessions primary key.

        Raises:
            KeyError: If the session is unknown.
            FileNotFoundError: If no log has been written yet.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT log_path FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"session {session_id}")
        fallback = self.logs_dir / f"session-{session_id}.jsonl"
        path = Path(row["log_path"]) if row["log_path"] else fallback
        if not path.is_file():
            raise FileNotFoundError(str(path))
        return path
