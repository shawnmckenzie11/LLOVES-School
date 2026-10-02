"""Staff and public celebration helpers for ``alc.mckenzian.com#celebrations``.

Ranking and the teacher-picked Shoutout setting live here. The public page
asks ``public_celebration_board`` for filled cards only.

Cards:

* **Shoutout** — teacher-picked Codename (school setting)
* **Most Engaged** — one card per class in ``ENGAGED_COURSES``: most
  distinct class days present, then participation points; ties list every
  tied student

MCK-118 freeze: while ``celebrations_frozen()`` is True (env
``CELEBRATIONS_FROZEN``, default on) the public Most Engaged cards freeze
*who* won, not their names. The snapshot (school setting
``SETTING_PUBLIC_SNAPSHOT``) stores each winner's ``class_id``,
``student_id`` and a fingerprint of the roster row. Names are looked up
live on every read, so a Codename change or a roster delete still reaches
the public page within the 60 s memo. The ranking code is unchanged.

The fingerprint is an HMAC keyed by the app secret (``FLASK_SECRET_KEY``),
so a copy of the database alone cannot be used to guess a deleted
student's Codename. Refs that no longer resolve to a live student are
pruned from the stored snapshot; a card whose winners are all gone stays
as an empty placeholder so it is not refilled with a new winner.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any

try:
    from gradebook import session_meeting_date
except ImportError:  # ``lms`` package import
    from lms.gradebook import session_meeting_date

SETTING_FEATURED_AWARD = "celebration_featured_award"
# MCK-116: identity of the current Shoutout, ``{class_id, student_id, fp,
# featured_at}``. Its own setting, so a rollback build that rewrites
# SETTING_FEATURED_AWARD without these keys cannot drop them (the same
# Shoutout keeps one reward key), and ``fp`` stops a reused ``students.id``
# from inheriting the Shoutout.
SETTING_FEATURED_AWARD_ID = "celebration_featured_award_id"
# Section codes (``section_code``) that each get their own Most Engaged card.
ENGAGED_COURSES: tuple[str, ...] = ("MCR3U", "MCR3U-2", "MCF3M")
PUBLIC_BOARD_TTL_SECONDS = 60.0

# MCK-118: the one switch for the public board is the env var
# ``CELEBRATIONS_FROZEN``. Unset (the default) or any value other than
# 0/false/no/off keeps Most Engaged frozen. ``CELEBRATIONS_FROZEN=0`` serves
# the live ranking, as before the freeze. Turning it off marks the stored
# snapshot stale, so turning it back on takes a fresh one.
CELEBRATIONS_FROZEN_ENV = "CELEBRATIONS_FROZEN"
CELEBRATIONS_FROZEN_DEFAULT = True
# JSON ``{"version", "semester_id", "epoch", "kid", "taken_at", "cards"}``.
# Each card keeps every tied winner as ``{class_id, student_id, fp}``. No
# names. ``fp`` is an HMAC under the app secret; ``kid`` identifies that key
# (an HMAC of a constant, not the key). A snapshot is only used for the
# semester, freeze epoch, and key it was taken with.
SETTING_PUBLIC_SNAPSHOT = "celebration_public_snapshot"
# Freeze epoch. Changes each time the board is seen unfrozen.
SETTING_FREEZE_EPOCH = "celebration_freeze_epoch"
# v3: keyed HMAC fingerprints plus ``kid``. v2 rows (unsalted sha256) and
# older are retaken like any other old format.
SNAPSHOT_VERSION = 3
# Same env var and dev default as ``create_app``'s ``app.secret_key``. Used
# only when no app bound its secret (scripts that open SchoolDB directly).
SECRET_KEY_ENV = "FLASK_SECRET_KEY"
SECRET_KEY_DEV_DEFAULT = "lloves-dev-secret-change-me"
# Domain separation: the fingerprint key is derived, never the raw secret.
_FP_KEY_CONTEXT = b"lloves:celebration-fp:v1"
_KID_MESSAGE = b"lloves:celebration-fp:key-id"

# Every user-visible celebrations string. Wonder replaces these placeholders.
WONDER_COPY: dict[str, str] = {
    "page_title": "Celebrations",  # Wonder copy slot
    # MCK-78 Wonder copy for the three keys #178 kept. Copy pending Shawn.
    "empty_board": "No shout-outs yet. The first one's up for grabs.",  # Wonder copy slot
    "coming_soon_line": "Shout-outs are on their way.",  # Wonder copy slot
    "coming_soon_sub": "Good work deserves a spotlight. We're still setting up the lights.",  # Wonder copy slot
    "award_title": "Shoutout",  # Wonder copy slot
    "engaged_title": "Most Engaged",  # Wonder copy slot
}

_public_board_lock = Lock()
_public_board_memo: dict[str, Any] = {
    "school_id": None,
    "at": 0.0,
    "payload": None,
}


def student_public_name(row: dict[str, Any]) -> str:
    """Codename first; never a legal last name on the public board.

    Args:
        row: Game-show ``students`` row.

    Returns:
        Display string for the celebration cards.
    """
    for key in ("codename", "first_name"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return "Student"


def public_student_label(row: dict[str, Any], course: str) -> str:
    """Public card name: Codename, else first name, else a course placeholder.

    Matches ``student_public_name`` when a Codename or first name is set.
    A blank Codename and a blank first name become ``A student in <course>``.
    Last names are never read.

    Args:
        row: Game-show ``students`` row.
        course: Section code used only for the blank-name placeholder.

    Returns:
        Name safe to show on the public board.
    """
    if str(row.get("codename") or "").strip() or str(row.get("first_name") or "").strip():
        return student_public_name(row)
    course_code = str(course or "").strip()
    if course_code:
        return f"A student in {course_code}"
    return "A student"


# Letters NFKD does not split into base + accent.
_FOLD_EXTRA = str.maketrans(
    {"đ": "d", "ð": "d", "ł": "l", "ø": "o", "æ": "ae", "œ": "oe", "ħ": "h", "ı": "i", "þ": "th"}
)
# Apostrophe look-alikes typed by phones and word processors. Folded to
# ``'`` before NFKD (which would turn ``´`` into a space plus an accent).
_FOLD_APOSTROPHES = str.maketrans(
    {
        "\u2019": "'",  # ’ right single quotation mark (iOS/macOS smart quote)
        "\u2018": "'",  # ‘ left single quotation mark
        "\u201b": "'",  # ‛ single high-reversed-9 quotation mark
        "\u02bc": "'",  # ʼ modifier letter apostrophe
        "\u02bb": "'",  # ʻ modifier letter turned comma (ʻokina)
        "\u2032": "'",  # ′ prime
        "\u00b4": "'",  # ´ acute accent used as an apostrophe
        "\u0060": "'",  # ` grave accent used as an apostrophe
        "\uff07": "'",  # ＇ fullwidth apostrophe
    }
)


def name_sort_key(name: str) -> tuple[str, str]:
    """Sort key that files accented names with their base letter.

    "Élodie" sorts among the Es and "Łukasz" among the Ls: casefold, fold
    apostrophe look-alikes (``’``, ``ʼ``, ``´``...) to ``'`` so "O’Brien"
    sorts with "O'Brien", NFKD, drop combining marks, then map the few
    letters NFKD leaves alone.

    Args:
        name: Display name.

    Returns:
        ``(folded, casefolded)``; the second part breaks folded ties.
    """
    text = str(name or "").casefold()
    folded = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", text.translate(_FOLD_APOSTROPHES))
        if not unicodedata.combining(ch)
    ).translate(_FOLD_EXTRA)
    return folded, text


def _empty_card(key: str, title: str, kicker: str, waiting: str) -> dict[str, Any]:
    """Return a card payload with no winner yet."""
    return {
        "key": key,
        "title": title,
        "kicker": kicker,
        "waiting": waiting,
        "name": "",
        "detail": "",
        "course": "",
    }


def _active_class_rows(school: Any) -> list[dict[str, Any]]:
    """Populated class ids in the active semester."""
    semester = school.get_active_semester()
    if not semester:
        return []
    offerings = school.list_offerings(
        semester_id=int(semester["id"]),
        include_archived=False,
    )
    rows: list[dict[str, Any]] = []
    for offering in offerings:
        course = str(
            offering.get("section_code") or offering.get("ontario_code") or ""
        ).strip()
        for cls in offering.get("classes") or []:
            rows.append({"class_id": int(cls["id"]), "course": course})
    return rows


# MCK-125: only these game ``sessions.status`` values feed Most Engaged.
# ``ended`` is written only when attendance is saved (finalize, End live
# scoring). ``active`` is a class still running: its ``present`` flags are
# provisional joins that End-without-save resets (status back to
# ``template``). Templates are empty grid columns.
COUNTED_SESSION_STATUSES = frozenset({"ended"})


def _gather_stats(school: Any) -> list[dict[str, Any]]:
    """Per-student attendance and points, scoped to one class at a time.

    ``session_scores`` holds one row per (session, student), but a class can
    log several sessions on the same meeting day (a second attendance pass,
    an extra game ``_2``/``_3``). Attendance therefore counts distinct
    meeting days with ``present`` set, matching the staff Attendance grid
    (``gradebook.build_attendance_week_grid``): template columns are
    skipped. Points sum the scored rows of those same sessions.

    Only sessions with saved attendance count (``COUNTED_SESSION_STATUSES``,
    MCK-125). A class still running (``active``) never reaches the board,
    so a mid-class read cannot freeze provisional joins.

    Students are ``students`` rows owned by one ``class_id``, so a learner
    rostered in two sections gets two separate entries, never a merged one.

    Args:
        school: ``SchoolDB`` instance.

    Returns:
        One stats row per (class, student) in the active semester.
    """
    stats: list[dict[str, Any]] = []
    game = school.game
    seen_classes: set[int] = set()
    for cls in _active_class_rows(school):
        class_id = int(cls["class_id"])
        if class_id in seen_classes:
            continue
        seen_classes.add(class_id)
        with game._lock:
            students = [
                dict(row)
                for row in game.conn.execute(
                    "SELECT * FROM students WHERE class_id = ?",
                    (class_id,),
                )
            ]
            sessions = [
                dict(row)
                for row in game.conn.execute(
                    """
                    SELECT id, starts_at, status FROM sessions
                    WHERE class_id = ?
                    ORDER BY starts_at ASC, id ASC
                    """,
                    (class_id,),
                )
            ]
            score_rows = [
                dict(row)
                for row in game.conn.execute(
                    """
                    SELECT ss.session_id, ss.student_id, ss.present, ss.points,
                           se.status AS session_status
                    FROM session_scores ss
                    JOIN sessions se ON se.id = ss.session_id
                    JOIN students st ON st.id = ss.student_id
                    WHERE se.class_id = ? AND st.class_id = se.class_id
                    """,
                    (class_id,),
                )
            ]
        if not students:
            continue
        # Meeting day per non-template session in this class.
        session_days: dict[int, str] = {}
        for session in sessions:
            if str(session.get("status") or "") not in COUNTED_SESSION_STATUSES:
                continue
            meeting = session_meeting_date(session.get("starts_at"))
            if meeting is None:
                continue
            session_days[int(session["id"])] = meeting.isoformat()
        present_days: dict[int, set[str]] = {}
        points_by_student: dict[int, float] = {}
        for row in score_rows:
            if str(row.get("session_status") or "") not in COUNTED_SESSION_STATUSES:
                continue
            stid = int(row["student_id"])
            points_by_student[stid] = points_by_student.get(stid, 0.0) + float(
                row.get("points") or 0
            )
            day = session_days.get(int(row["session_id"]))
            if day is not None and int(row.get("present") or 0) == 1:
                present_days.setdefault(stid, set()).add(day)
        held_days = set(session_days.values())
        for student in students:
            stid = int(student["id"])
            stats.append(
                {
                    "class_id": class_id,
                    "student_id": stid,
                    "name": student_public_name(student),
                    "course": cls["course"],
                    "present": len(present_days.get(stid, set())),
                    "session_count": len(held_days),
                    "points": round(points_by_student.get(stid, 0.0), 1),
                }
            )
    return stats


def _top_tied(stats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every student tied for most presents, then most points.

    Args:
        stats: ``_gather_stats`` rows for one class.

    Returns:
        Tied leaders sorted by name, or an empty list when nobody has
        attended or scored yet.
    """
    pool = [s for s in stats if s["present"] > 0 or s["points"] > 0]
    if not pool:
        return []
    best = max((s["present"], s["points"]) for s in pool)
    leaders = [s for s in pool if (s["present"], s["points"]) == best]
    return sorted(
        leaders, key=lambda s: (name_sort_key(s["name"]), int(s["student_id"]))
    )


def _engaged_card(course: str, leaders: list[dict[str, Any]]) -> dict[str, Any]:
    """Most Engaged card for one class, listing every tied leader.

    Args:
        course: Section code shown on the card (``MCR3U-2``).
        leaders: ``_top_tied`` rows; empty leaves the card waiting.

    Returns:
        Card payload with ``students`` ids for the public name lookup.
    """
    card = _empty_card(
        "engaged",
        WONDER_COPY["engaged_title"],
        "",
        "Waiting on the first attendance.",
    )
    card["course"] = course
    if not leaders:
        return card
    top = leaders[0]
    detail = f"{top['present']} class{'es' if top['present'] != 1 else ''} present"
    if top["points"]:
        detail += f" · {top['points']:g} pts"
    card["name"] = ", ".join(s["name"] for s in leaders)
    card["names"] = [s["name"] for s in leaders]
    card["detail"] = detail
    card["students"] = [
        {"class_id": int(s["class_id"]), "student_id": int(s["student_id"])}
        for s in leaders
    ]
    return card


def _featured_card(school: Any) -> dict[str, Any]:
    """Resolve the teacher-picked Shoutout against the live roster."""
    card = _empty_card(
        "award",
        WONDER_COPY["award_title"],
        "",
        "A teacher will feature someone here.",
    )
    award = _award_payload(school)
    if award is None:
        return card
    class_id = award["class_id"]
    student_id = award["student_id"]
    blurb = str(award.get("blurb") or "").strip()
    # MCK-116 MED-2: a stale pointer (id reused by a new student) shows nobody.
    student = _award_owner_row(school, award)
    if not student:
        return card
    course = ""
    try:
        cls = school.game.get_class(class_id)
        offering_id = cls.get("offering_id")
        if offering_id:
            offering = school.get_offering(int(offering_id))
            course = str(
                offering.get("section_code") or offering.get("ontario_code") or ""
            )
    except (KeyError, TypeError):
        course = ""
    card["name"] = student_public_name(student)
    card["course"] = course
    card["detail"] = blurb or "Featured by their teacher."
    card["class_id"] = class_id
    card["student_id"] = student_id
    return card


def build_celebration_board(school: Any) -> dict[str, Any]:
    """Assemble the Shoutout card and one Most Engaged card per class.

    Args:
        school: ``SchoolDB`` instance.

    Returns:
        ``{cards: [...]}`` in display order.
    """
    stats = _gather_stats(school)
    cards = [_featured_card(school)]
    for course in ENGAGED_COURSES:
        in_course = [
            s for s in stats if str(s.get("course") or "").upper() == course
        ]
        cards.append(_engaged_card(course, _top_tied(in_course)))
    return {"cards": cards}


def celebrations_frozen() -> bool:
    """True while the public Most Engaged cards are frozen.

    Reads env ``CELEBRATIONS_FROZEN`` on every call. Unset means
    ``CELEBRATIONS_FROZEN_DEFAULT`` (frozen).
    """
    raw = os.environ.get(CELEBRATIONS_FROZEN_ENV)
    if raw is None or not raw.strip():
        return CELEBRATIONS_FROZEN_DEFAULT
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def bind_celebration_secret(school: Any, secret: Any) -> None:
    """Key this school's snapshot fingerprints to the app secret.

    ``create_app`` calls this with ``app.secret_key``. A changed secret
    changes the key id, so the next read retakes the snapshot instead of
    failing every fingerprint.

    Args:
        school: ``SchoolDB`` instance.
        secret: App secret (str or bytes). Empty falls back to the env.
    """
    school._celebration_fp_secret = secret


def _fp_key(school: Any) -> bytes:
    """Derived HMAC key for snapshot fingerprints.

    The app's bound secret, else ``FLASK_SECRET_KEY`` with the same dev
    default ``create_app`` uses. Never the raw secret: an HMAC of a fixed
    context string under it.
    """
    secret = getattr(school, "_celebration_fp_secret", None)
    if secret in (None, "", b""):
        secret = os.environ.get(SECRET_KEY_ENV) or SECRET_KEY_DEV_DEFAULT
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(bytes(secret), _FP_KEY_CONTEXT, hashlib.sha256).digest()


def _key_id(school: Any) -> str:
    """Short id of the current fingerprint key, stored as ``kid``."""
    return hmac.new(_fp_key(school), _KID_MESSAGE, hashlib.sha256).hexdigest()[:16]


def _student_fp(school: Any, class_id: int, student_id: int, canvas_id: Any) -> str:
    """Keyed fingerprint of one roster row: class, id, and its never-changing key.

    ``students.id`` can be reused after a delete. ``canvas_id`` is set once
    at insert and never updated (renames keep it), so a reused id on a
    different student gives a different fingerprint. HMAC-SHA256 under the
    app secret, so the snapshot holds no Canvas id or Codename and a DB
    copy without the secret cannot test name guesses against it.
    """
    raw = f"{int(class_id)}:{int(student_id)}:{canvas_id or ''}"
    return hmac.new(_fp_key(school), raw.encode("utf-8"), hashlib.sha256).hexdigest()[:20]


def _roster_row(school: Any, class_id: Any, student_id: Any) -> dict[str, Any] | None:
    """Live ``students`` row, or None when it is gone or the ids are bad."""
    try:
        return school.game.get_student(int(class_id), int(student_id))
    except (KeyError, TypeError, ValueError):
        return None


def _resolve_ref(school: Any, ref: Any) -> dict[str, Any] | None:
    """Live roster row for a frozen winner, or None.

    None when the student was deleted, moved off the class, or the id now
    belongs to someone else (fingerprint mismatch).
    """
    if not isinstance(ref, dict):
        return None
    row = _roster_row(school, ref.get("class_id"), ref.get("student_id"))
    if row is None:
        return None
    want = str(ref.get("fp") or "")
    have = _student_fp(
        school, int(ref["class_id"]), int(ref["student_id"]), row.get("canvas_id")
    )
    return row if want and hmac.compare_digest(want, have) else None


def _sorted_labels(labels: list[str]) -> list[str]:
    """Public names in tie order (accent-folded, see ``name_sort_key``)."""
    return sorted(labels, key=name_sort_key)


def _public_labels(school: Any, card: dict[str, Any]) -> list[tuple[dict[str, int], str]]:
    """Public name for every student on a live card, in card order.

    Args:
        school: ``SchoolDB`` instance.
        card: Internal board card with ``students`` ids, or a Shoutout card
            with ``class_id`` and ``student_id``.

    Returns:
        ``(ids, label)`` pairs. Students missing from the roster are skipped.
    """
    course = str(card.get("course") or "").strip()
    refs = list(card.get("students") or [])
    if not refs and card.get("class_id") is not None and card.get("student_id") is not None:
        refs = [{"class_id": card["class_id"], "student_id": card["student_id"]}]
    out: list[tuple[dict[str, int], str]] = []
    for ids in refs:
        row = _roster_row(school, ids.get("class_id"), ids.get("student_id"))
        if row is not None:
            out.append(
                (
                    {"class_id": int(ids["class_id"]), "student_id": int(ids["student_id"])},
                    public_student_label(row, course),
                )
            )
    return out


def _public_shape(card: dict[str, Any], names: list[str]) -> dict[str, Any] | None:
    """Public JSON card: no roster ids, every tied name kept.

    Args:
        card: Internal or snapshot card (``key``, ``course``, ``detail``...).
        names: Public names in display order.

    Returns:
        ``key``, ``name`` (names joined with ", "), ``names``, ``course``,
        ``detail``, ``title``, and ``kicker``, or None when no name is left.
    """
    names = [str(n).strip() for n in names if str(n or "").strip()]
    if not names:
        return None
    return {
        "key": str(card.get("key") or ""),
        "name": ", ".join(names),
        "names": names,
        "course": str(card.get("course") or "").strip(),
        "detail": str(card.get("detail") or ""),
        "title": str(card.get("title") or ""),
        "kicker": str(card.get("kicker") or ""),
    }


def _public_card(school: Any, card: dict[str, Any]) -> dict[str, Any] | None:
    """One public card from the live board, or None when it has no winner.

    Args:
        school: ``SchoolDB`` instance.
        card: Internal board card, including roster ids.

    Returns:
        ``_public_shape`` output, or None when ``name`` is empty.
    """
    if not str(card.get("name") or "").strip():
        return None
    labels = [label for _ids, label in _public_labels(school, card)]
    return _public_shape(card, _sorted_labels(labels))


def _frozen_public_card(school: Any, card: dict[str, Any]) -> dict[str, Any] | None:
    """One public card from the snapshot, names looked up now.

    Winners no longer on the roster are left out. A card with no winner
    left is dropped.
    """
    course = str(card.get("course") or "").strip()
    labels = []
    for ref in card.get("students") or []:
        row = _resolve_ref(school, ref)
        if row is not None:
            labels.append(public_student_label(row, course))
    return _public_shape(card, _sorted_labels(labels))


def _engaged_snapshot_cards(
    school: Any, courses: tuple[str, ...] | list[str] | None = None
) -> list[dict[str, Any]]:
    """Most Engaged cards with a winner, in the snapshot's id-only form.

    Args:
        school: ``SchoolDB`` instance.
        courses: Only these section codes; None means every course.

    Returns:
        One dict per course with ``students: [{class_id, student_id, fp}]``
        for every tied leader. No names.
    """
    out: list[dict[str, Any]] = []
    for card in build_celebration_board(school).get("cards") or []:
        if card.get("key") != "engaged" or not str(card.get("name") or "").strip():
            continue
        if courses is not None and card.get("course") not in courses:
            continue
        refs = []
        for ids in card.get("students") or []:
            row = _roster_row(school, ids.get("class_id"), ids.get("student_id"))
            if row is None:
                continue
            class_id, student_id = int(ids["class_id"]), int(ids["student_id"])
            refs.append(
                {
                    "class_id": class_id,
                    "student_id": student_id,
                    "fp": _student_fp(school, class_id, student_id, row.get("canvas_id")),
                }
            )
        if not refs:
            continue
        out.append(
            {
                "key": "engaged",
                "title": str(card.get("title") or ""),
                "kicker": str(card.get("kicker") or ""),
                "course": str(card.get("course") or ""),
                "detail": str(card.get("detail") or ""),
                "students": refs,
            }
        )
    return out


def _parse_snapshot(raw: str | None) -> dict[str, Any] | None:
    """Snapshot dict, or None when blank, corrupt, or an older format."""
    if not str(raw or "").strip():
        return None
    try:
        payload = json.loads(str(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("cards"), list):
        return None
    if payload.get("version") != SNAPSHOT_VERSION:
        return None
    return payload


def _live_class_running(school: Any) -> bool:
    """True while any live class session is active (MCK-125).

    A failed check counts as running, so an error never stores a snapshot.
    """
    check = getattr(school, "has_active_live_sessions", None)
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:  # noqa: BLE001 - fail closed: do not persist
        return True


def _freeze_epoch(school: Any) -> str:
    """Current freeze epoch marker ("" until the board is first unfrozen)."""
    return str(school.get_school_setting(SETTING_FREEZE_EPOCH, "") or "")


def _active_semester_id(school: Any) -> int | None:
    """Active semester id, or None."""
    semester = school.get_active_semester()
    if not semester:
        return None
    try:
        return int(semester["id"])
    except (KeyError, TypeError, ValueError):
        return None


def _new_snapshot(school: Any, semester_id: int, epoch: str) -> dict[str, Any]:
    """Build a snapshot payload from the live ranking right now."""
    return {
        "version": SNAPSHOT_VERSION,
        "semester_id": int(semester_id),
        "epoch": epoch,
        "kid": _key_id(school),
        "taken_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "cards": _engaged_snapshot_cards(school),
    }


def _store_snapshot(school: Any, old_raw: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    """Write ``payload`` only if the row still holds ``old_raw``; return the winner.

    Workers racing on the same read both build a payload. The insert (or
    compare-and-set) lets exactly one land; everyone then serves that row.
    """
    text = json.dumps(payload)
    if old_raw is None:
        school.add_school_setting_if_missing(SETTING_PUBLIC_SNAPSHOT, text)
    else:
        school.compare_and_set_school_setting(SETTING_PUBLIC_SNAPSHOT, old_raw, text)
    stored = _parse_snapshot(school.get_school_setting(SETTING_PUBLIC_SNAPSHOT, None))
    if (
        stored is not None
        and stored.get("semester_id") == payload["semester_id"]
        and stored.get("epoch") == payload["epoch"]
        and stored.get("kid") == payload.get("kid")
    ):
        return stored
    return payload


def _prune_cards(school: Any, cards: list[Any]) -> tuple[list[dict[str, Any]], bool]:
    """Drop frozen refs that no longer resolve to a live student.

    A deleted student, a student moved off the class, or an id now held by
    someone else (fingerprint mismatch) is removed from the stored row, so
    the snapshot stops carrying their fingerprint. The card itself is kept,
    even with no students left: an empty card is not shown, and keeping it
    stops the missing-course path from refilling it with a new winner.

    Args:
        school: ``SchoolDB`` instance.
        cards: Stored snapshot cards.

    Returns:
        ``(cards, changed)``. ``changed`` is True when any ref was removed.
    """
    out: list[dict[str, Any]] = []
    changed = False
    for card in cards:
        if not isinstance(card, dict):
            changed = True
            continue
        refs = list(card.get("students") or [])
        kept = [ref for ref in refs if _resolve_ref(school, ref) is not None]
        if len(kept) != len(refs):
            changed = True
            card = {**card, "students": kept}
        out.append(card)
    return out, changed


def frozen_celebration_snapshot(school: Any) -> dict[str, Any]:
    """The Most Engaged snapshot for this semester and freeze epoch.

    * No active semester, or nobody has attended yet: nothing is stored and
      the next read tries again.
    * A stored snapshot from another semester or epoch, taken under another
      app secret (``kid``), a blank or corrupt value, or an older format
      (v1 names, v2 unsalted fingerprints) is replaced (compare-and-set).
    * Refs that no longer resolve to a live student are pruned from the
      stored row. A card left with nobody stays as an empty placeholder,
      so it is hidden and not refilled.
    * A course with no card yet is added the first time it has a winner,
      then stays frozen.
    * While any live class is running no snapshot is taken and no course
      is added; only pruning may write (MCK-125). Rankings only count sessions with saved attendance.

    Args:
        school: ``SchoolDB`` instance.

    Returns:
        Snapshot dict with ``cards`` (possibly empty, then not stored).
    """
    semester_id = _active_semester_id(school)
    if semester_id is None:
        return {"cards": []}
    epoch = _freeze_epoch(school)
    raw = school.get_school_setting(SETTING_PUBLIC_SNAPSHOT, None)
    saved = _parse_snapshot(raw)
    # MCK-125: never take or extend the snapshot while any live class is
    # running. Serve the stored one (pruning gone students still applies);
    # with nothing usable stored, serve the ended-sessions view unstored.
    live = _live_class_running(school)
    if (
        saved is not None
        and saved.get("semester_id") == semester_id
        and saved.get("epoch") == epoch
        and saved.get("kid") == _key_id(school)
        and saved["cards"]
    ):
        cards, changed = _prune_cards(school, saved["cards"])
        have = {str(card.get("course") or "") for card in cards}
        missing = [] if live else [c for c in ENGAGED_COURSES if c not in have]
        extra = _engaged_snapshot_cards(school, missing) if missing else []
        if not changed and not extra:
            return saved
        merged = dict(saved)
        merged["cards"] = sorted(
            cards + extra,
            key=lambda c: (
                ENGAGED_COURSES.index(c["course"]) if c.get("course") in ENGAGED_COURSES else 99
            ),
        )
        return _store_snapshot(school, raw, merged)
    fresh = _new_snapshot(school, semester_id, epoch)
    if not fresh["cards"] or live:
        return fresh
    return _store_snapshot(school, raw, fresh)


def note_celebrations_unfrozen(school: Any) -> None:
    """Retire the stored snapshot while the board is unfrozen.

    Bumps ``SETTING_FREEZE_EPOCH`` once per off period, so turning the flag
    back on takes a fresh snapshot instead of reviving the old one. Called
    at app start and on every unfrozen public read. No-op while frozen.
    """
    if celebrations_frozen():
        return
    saved = _parse_snapshot(school.get_school_setting(SETTING_PUBLIC_SNAPSHOT, None))
    if saved is not None and saved.get("epoch") == _freeze_epoch(school):
        school.set_school_setting(SETTING_FREEZE_EPOCH, uuid.uuid4().hex)


def refresh_public_celebration_snapshot(school: Any) -> dict[str, Any]:
    """Replace the frozen snapshot with the live ranking right now.

    Not wired to any route. Clears only this process's memo; other workers
    pick it up within ``PUBLIC_BOARD_TTL_SECONDS``.

    Args:
        school: ``SchoolDB`` instance.

    Returns:
        The new snapshot payload (not stored when it has no cards).
    """
    semester_id = _active_semester_id(school)
    if semester_id is None:
        return {"cards": []}
    fresh = _new_snapshot(school, semester_id, _freeze_epoch(school))
    if fresh["cards"]:
        school.set_school_setting(SETTING_PUBLIC_SNAPSHOT, json.dumps(fresh))
    clear_public_celebration_memo()
    return fresh


def _assemble_public_board(school: Any) -> dict[str, Any]:
    """Build the public card list.

    The Shoutout card always follows the teacher's current pick. Most
    Engaged comes from the snapshot while frozen, else from the live
    ranking. Names are always looked up now.

    Args:
        school: ``SchoolDB`` instance.

    Returns:
        ``{cards: [...]}`` with ids removed and empty cards dropped.
    """
    cards = []
    if celebrations_frozen():
        award = _public_card(school, _featured_card(school))
        if award is not None:
            cards.append(award)
        for card in frozen_celebration_snapshot(school).get("cards") or []:
            if not isinstance(card, dict):
                continue
            public = _frozen_public_card(school, card)
            if public is not None:
                cards.append(public)
        return {"cards": cards}
    note_celebrations_unfrozen(school)
    for card in build_celebration_board(school).get("cards") or []:
        public = _public_card(school, card)
        if public is not None:
            cards.append(public)
    return {"cards": cards}


def celebrated_students(school: Any) -> list[dict[str, Any]]:
    """Students the public board is celebrating right now, with roster ids.

    For the reward pop-up (pick an avatar on next login). One row per
    (card, student): a tie gives one row per tied student, and a student on
    two cards appears twice. Ids are game-show ``students`` rows, which are
    per class, so the same learner in two sections has two different ids.
    While frozen, a winner whose id now belongs to someone else (deleted,
    id reused) is skipped by the fingerprint check.

    Args:
        school: ``SchoolDB`` instance.

    Returns:
        ``{card, course, class_id, student_id, name}`` rows in board order.
    """
    rows: list[dict[str, Any]] = []
    award = _featured_card(school)
    if award.get("name"):
        for ids, label in _public_labels(school, award):
            rows.append(
                {"card": "award", "course": str(award.get("course") or ""), **ids, "name": label}
            )
    if celebrations_frozen():
        for card in frozen_celebration_snapshot(school).get("cards") or []:
            course = str(card.get("course") or "")
            found = []
            for ref in card.get("students") or []:
                row = _resolve_ref(school, ref)
                if row is not None:
                    found.append(
                        {
                            "card": "engaged",
                            "course": course,
                            "class_id": int(ref["class_id"]),
                            "student_id": int(ref["student_id"]),
                            "name": public_student_label(row, course),
                        }
                    )
            rows.extend(sorted(found, key=lambda r: name_sort_key(r["name"])))
        return rows
    for card in build_celebration_board(school).get("cards") or []:
        if card.get("key") != "engaged" or not card.get("name"):
            continue
        for ids, label in _public_labels(school, card):
            rows.append(
                {"card": "engaged", "course": str(card.get("course") or ""), **ids, "name": label}
            )
    return rows


def clear_public_celebration_memo() -> None:
    """Drop the in-process public board memo.

    Tests call this when a new ``SchoolDB`` replaces the previous one so a
    reused ``id()`` cannot serve another school's cards.
    """
    with _public_board_lock:
        _public_board_memo["school_id"] = None
        _public_board_memo["at"] = 0.0
        _public_board_memo["payload"] = None


def _copy_public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a detached copy so callers cannot mutate the memo.

    Args:
        payload: Cached ``{cards: [...]}``.

    Returns:
        Shallow copy of the payload and each card.
    """
    cards = []
    for card in payload.get("cards") or []:
        copied = dict(card)
        if isinstance(copied.get("names"), list):
            copied["names"] = list(copied["names"])
        cards.append(copied)
    return {"cards": cards}


def public_celebration_board(
    school: Any,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Public celebration cards, memoized for about 60 seconds.

    Only cards with a winner are returned. ``class_id`` and ``student_id``
    are stripped. Names follow ``public_student_label``: Codename, else
    first name, else ``A student in <course>``. Each card lists every tied
    student in ``names``; ``name`` is the same list joined with ", ".
    While ``celebrations_frozen()``, Most Engaged winners come from the
    saved snapshot (see ``frozen_celebration_snapshot``); names are looked
    up on every rebuild.

    Args:
        school: ``SchoolDB`` instance.
        now: Monotonic timestamp for tests. Production omits it.

    Returns:
        ``{cards: [...]}`` safe to send to the public landing page.
    """
    stamp = time.monotonic() if now is None else float(now)
    school_id = id(school)
    with _public_board_lock:
        cached = _public_board_memo
        payload = cached.get("payload")
        if (
            cached.get("school_id") == school_id
            and isinstance(payload, dict)
            and stamp - float(cached.get("at") or 0.0) < PUBLIC_BOARD_TTL_SECONDS
        ):
            return _copy_public_payload(payload)
    fresh = _assemble_public_board(school)
    with _public_board_lock:
        _public_board_memo["school_id"] = school_id
        _public_board_memo["at"] = stamp
        _public_board_memo["payload"] = fresh
    return _copy_public_payload(fresh)


def celebration_candidates(school: Any, teacher_user_id: int) -> list[dict[str, Any]]:
    """Codenames this teacher can feature on the Shoutout card.

    Args:
        school: ``SchoolDB`` instance.
        teacher_user_id: Staff user id.

    Returns:
        Sorted ``{class_id, student_id, name, course}`` rows.
    """
    out: list[dict[str, Any]] = []
    for cls in school.list_staff_classes(int(teacher_user_id)):
        class_id = int(cls["id"])
        course = str(cls.get("section_code") or cls.get("course_code") or "")
        with school.game._lock:
            students = [
                dict(row)
                for row in school.game.conn.execute(
                    "SELECT * FROM students WHERE class_id = ? ORDER BY first_name",
                    (class_id,),
                )
            ]
        for student in students:
            out.append(
                {
                    "class_id": class_id,
                    "student_id": int(student["id"]),
                    "name": student_public_name(student),
                    "course": course,
                }
            )
    out.sort(key=lambda row: (row["course"], row["name"].lower()))
    return out


def set_featured_award(
    school: Any,
    *,
    teacher_user_id: int,
    class_id: int | None,
    student_id: int | None,
    blurb: str = "",
) -> dict[str, Any]:
    """Save or clear the teacher-picked Shoutout student.

    Args:
        school: ``SchoolDB`` instance.
        teacher_user_id: Acting staff user.
        class_id: Class of the featured student, or None to clear.
        student_id: Featured student, or None to clear.
        blurb: Optional one-line celebration.

    Returns:
        Updated featured card.

    Raises:
        ValueError: Unknown student or class the teacher does not own.
    """
    if class_id is None or student_id is None:
        school.set_school_setting(SETTING_FEATURED_AWARD, "")
        school.set_school_setting(SETTING_FEATURED_AWARD_ID, "")
        return _featured_card(school)
    if not school.teacher_owns_class(int(teacher_user_id), int(class_id)):
        raise ValueError("That class is not yours to celebrate.")
    with school.game._lock:
        row = school.game.conn.execute(
            "SELECT id, canvas_id FROM students WHERE id = ? AND class_id = ?",
            (int(student_id), int(class_id)),
        ).fetchone()
    if row is None:
        raise ValueError("That Codename is not on this roster.")
    payload = {
        "class_id": int(class_id),
        "student_id": int(student_id),
        "blurb": str(blurb or "").strip()[:240],
        "fp": _student_fp(school, int(class_id), int(student_id), row[1]),
    }
    # MCK-116: ``featured_at`` marks one featured appearance for the avatar
    # reward. Re-saving the same student (a new blurb) keeps it, so it never
    # re-grants; featuring someone new (or a new student on a reused id)
    # starts a new appearance.
    previous = _award_payload(school)
    if previous and _same_award_student(previous, payload):
        if previous.get("featured_at"):
            payload["featured_at"] = previous["featured_at"]
    else:
        payload["featured_at"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    school.set_school_setting(SETTING_FEATURED_AWARD, json.dumps(payload))
    _set_award_identity(school, payload)
    return _featured_card(school)


def _setting_ids(school: Any, key: str) -> dict[str, Any] | None:
    """One JSON setting with int ``class_id`` / ``student_id``, or None."""
    raw = school.get_school_setting(key, "") or ""
    if not str(raw).strip():
        return None
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return None
        return {
            **payload,
            "class_id": int(payload.get("class_id")),
            "student_id": int(payload.get("student_id")),
        }
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _same_award_student(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Same roster row: same ids and, when both carry one, the same ``fp``."""
    if (a["class_id"], a["student_id"]) != (b["class_id"], b["student_id"]):
        return False
    fa, fb = str(a.get("fp") or ""), str(b.get("fp") or "")
    return not (fa and fb) or hmac.compare_digest(fa, fb)


def _set_award_identity(school: Any, award: dict[str, Any]) -> None:
    """Store the Shoutout identity (MCK-116) beside the Shoutout setting."""
    ident = {
        "class_id": int(award["class_id"]),
        "student_id": int(award["student_id"]),
        "fp": str(award.get("fp") or ""),
    }
    if award.get("featured_at"):
        ident["featured_at"] = str(award["featured_at"])
    school.set_school_setting(SETTING_FEATURED_AWARD_ID, json.dumps(ident))


def _award_payload(school: Any) -> dict[str, Any] | None:
    """The stored Shoutout with int ids, or None when unset or bad.

    MCK-116: ``fp`` and ``featured_at`` come from the identity setting when
    the Shoutout setting lost them (rewritten by a rollback build) and the
    identity names the same student, so one Shoutout keeps one reward key.
    """
    award = _setting_ids(school, SETTING_FEATURED_AWARD)
    if award is None:
        return None
    ident = _setting_ids(school, SETTING_FEATURED_AWARD_ID)
    if ident is not None and _same_award_student(ident, award):
        for key in ("fp", "featured_at"):
            if not award.get(key) and ident.get(key):
                award[key] = ident[key]
    return award


def _award_owner_row(school: Any, award: dict[str, Any]) -> dict[str, Any] | None:
    """Live roster row the Shoutout points at, or None when it is stale.

    None when the student was deleted or moved, or (MCK-116 MED-2) the id
    now belongs to someone else: ``fp`` (``_student_fp``, the #212 frozen
    board check) no longer matches the row's never-changing ``canvas_id``.
    """
    row = _roster_row(school, award.get("class_id"), award.get("student_id"))
    if row is None or int(row.get("class_id") or 0) != int(award["class_id"]):
        return None
    want = str(award.get("fp") or "")
    if want:
        have = _student_fp(
            school, int(award["class_id"]), int(award["student_id"]), row.get("canvas_id")
        )
        if not hmac.compare_digest(want, have):
            return None
    return row


def forget_featured_students(school: Any, class_id: int, student_ids: Any) -> bool:
    """Clear the Shoutout when its roster row is deleted or replaced (MED-2).

    ``students.id`` can be reused, so a pointer left behind would hand the
    Shoutout (and its avatar reward) to the next new student on that id.

    Args:
        school: ``SchoolDB`` instance.
        class_id: Class the rows were removed from.
        student_ids: Removed ``students.id`` values.

    Returns:
        True when the Shoutout pointed at one of them and was cleared.
    """
    ids = {int(x) for x in student_ids or []}
    award = _setting_ids(school, SETTING_FEATURED_AWARD)
    ident = _setting_ids(school, SETTING_FEATURED_AWARD_ID)
    hit = False
    for key, row in ((SETTING_FEATURED_AWARD, award), (SETTING_FEATURED_AWARD_ID, ident)):
        if row is not None and row["class_id"] == int(class_id) and row["student_id"] in ids:
            school.set_school_setting(key, "")
            hit = True
    if hit:
        clear_public_celebration_memo()
    return hit


def reward_appearances(school: Any) -> list[dict[str, Any]]:
    """Featured appearances that earn a Celebrations avatar reward (MCK-116 S1).

    Built on ``celebrated_students``, so it is exactly who the public board
    features right now: the Shoutout, plus every tied Most Engaged winner per
    course (frozen snapshot while frozen). Each row names its appearance:

    * Most Engaged: ``engaged:<semester>:<freeze epoch>:<course>``. A login,
      a reload or a re-taken snapshot in the same epoch is the same
      appearance; a new epoch (board unfrozen and frozen again) is a new one.
    * Shoutout: ``shoutout:<featured_at>`` (set when a teacher features a new
      student; a blurb edit keeps it, and so does a rollback build's rewrite,
      via the identity setting). An older setting without ``featured_at``
      uses ``shoutout:c<class>:s<student>``; ``sync_avatar_reward_grants``
      never lets both keys grant the same Shoutout (LOW-4). The Shoutout
      only counts while its ``fp`` still matches the roster row (MED-2).

    Args:
        school: ``SchoolDB`` instance.

    Returns:
        ``{class_id, student_id, source, source_ref, award_title}`` rows.
    """
    rows = celebrated_students(school)
    if not rows:
        return []
    semester_id = _active_semester_id(school)
    epoch = _freeze_epoch(school) or "-"
    award = _award_payload(school)
    if award is not None and not award.get("fp"):
        # A pre-#221 Shoutout has no fingerprint: pin the row it names now so
        # a later id reuse cannot inherit it.
        owner = _award_owner_row(school, award)
        if owner is not None:
            award["fp"] = _student_fp(
                school, award["class_id"], award["student_id"], owner.get("canvas_id")
            )
            _set_award_identity(school, award)
    out: list[dict[str, Any]] = []
    for row in rows:
        ids = {"class_id": int(row["class_id"]), "student_id": int(row["student_id"])}
        if row.get("card") == "award":
            if award is None or (award["class_id"], award["student_id"]) != (
                ids["class_id"], ids["student_id"]
            ):
                continue
            if _award_owner_row(school, award) is None:
                continue
            featured_at = str(award.get("featured_at") or "")
            if featured_at:
                ref = f"shoutout:{featured_at}"
            else:
                ref = shoutout_fallback_ref(ids["class_id"], ids["student_id"])
            out.append(
                {**ids, "source": "shoutout", "source_ref": ref,
                 "featured_at": featured_at or None,
                 "award_title": WONDER_COPY["award_title"]}
            )
        elif row.get("card") == "engaged":
            course = str(row.get("course") or "")
            if not course or semester_id is None:
                continue
            out.append(
                {**ids, "source": "engaged",
                 "source_ref": f"engaged:{semester_id}:{epoch}:{course}",
                 "award_title": WONDER_COPY["engaged_title"]}
            )
    return out


def shoutout_fallback_ref(class_id: int, student_id: int) -> str:
    """Reward key of a Shoutout saved without ``featured_at`` (pre-#221)."""
    return f"shoutout:c{int(class_id)}:s{int(student_id)}"


def reward_title(source: str) -> str:
    """Board card title for a grant source, as the board shows it (Wonder copy)."""
    return WONDER_COPY["award_title"] if source == "shoutout" else WONDER_COPY["engaged_title"]
