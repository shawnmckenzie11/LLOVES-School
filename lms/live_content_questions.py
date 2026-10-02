"""MCK-79: each module's top Contest Questions for Run Live Class Import.

**Source: ``live_problems``.** Contest Questions are the active curated
live problems with kind ``contest`` for the class's course (Ontario code),
the contest-style items Team Challenge and the slide builder already use.
They are not question-bank rows: a read-only prod count (Ops, 2026-10-02
16:27 ET) found 0 of 31,981 bank rows with a contest kind or tag, while
``live_problems`` holds 9 active contest items (8 MCF3M, 1 MCR3U). Kind is
matched case-insensitively and ``cemc`` counts as contest. ``live_problems``
has one ``kind`` column, so a row is never both warmup and contest.

**Grouping.** ``module_hint`` is split on ``/`` like ``live_class_slides``
does. A part like ``M1C1`` maps the problem to Module 1 when that module is
in the class's Run Live Class list. Strand-only hints (``A``, ``B``, ``C``),
empty hints, or modules outside the list go to one course-wide
**Contest** group (token ``COURSE``). Strand letters are not guessed onto
modules.

**Top 6** is the existing ``live_problems`` order (``sort_order``, then
id), the order ``list_live_problems`` and Team Challenge use. There is no
rank field. A group with fewer than six shows what it has.

**Import** reuses the deck's Add New path
(``add_staff_question_to_class_playlist``, type ``poll``, Kind Contest): the
card text is the Team Challenge prompt (stem, blank line, task). That is
the same text the Action-round team challenge shows for a contest problem.
Each card carries ``live_problem_id`` (for the "on deck" mark) and a batch
token. A batch is all-or-nothing: if any placement fails, every row that
carries the batch token is deleted again, including a row committed just
before a later step raised.

Python, JS, CSS and data-attribute identifiers keep the earlier
``content`` spelling. Only labels and endpoints say Contest.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

try:
    from bank_mc_normalize import parse_module_token
    from live_class_packs import live_class_registry
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.bank_mc_normalize import parse_module_token
    from lms.live_class_packs import live_class_registry

LOG = logging.getLogger(__name__)

CONTENT_QUESTIONS_PER_MODULE = 6
# ``live_problems.kind`` tokens that count as Contest (lower-cased, trimmed).
CONTEST_KINDS = ("contest", "cemc")
# Group token and label for contest problems with no module mapping.
COURSE_GROUP = "COURSE"
COURSE_GROUP_LABEL = "Contest"
# Every module's six plus the course group's six.
MAX_CONTENT_PICKS = 54


class ContentImportFailed(RuntimeError):
    """A batch placement failed and the batch was rolled back (or not).

    Attributes:
        landed: Live problem ids still on the deck from this batch.
        rolled_back: Placement rows removed again.
        status: HTTP status for the route (409 undone, 500 undo failed).
    """

    def __init__(
        self,
        message: str,
        *,
        landed: list[int],
        rolled_back: int,
        status: int = 409,
    ) -> None:
        """Keep the failure text, what is still on the deck and the status."""
        super().__init__(message)
        self.landed = landed
        self.rolled_back = rolled_back
        self.status = status


def selectable_live_modules(course: Any) -> list[str]:
    """Return the Run Live Class module tokens that have a bank scope.

    Args:
        course: Ontario code from the class offering.

    Returns:
        ``M1``–``M8`` tokens in the order the Module select lists them.
    """
    modules = live_class_registry(course).get("modules") or []
    out: list[str] = []
    for token in modules:
        number = parse_module_token(token)
        if number is None:
            continue
        key = f"M{number}"
        if key not in out:
            out.append(key)
    return out


def group_key(raw: Any, modules: list[str]) -> str:
    """Normalize a group token: ``M1``–``M8`` in ``modules``, ``COURSE`` or "".

    Args:
        raw: Posted or query token (``m2``, ``2``, ``course``).
        modules: Selectable module tokens.
    """
    token = str(raw or "").strip().upper()
    if token == COURSE_GROUP:
        return COURSE_GROUP
    number = parse_module_token(token)
    key = f"M{number}" if number is not None else ""
    return key if key in modules else ""


def problem_group(module_hint: Any, modules: list[str]) -> str:
    """Map one ``module_hint`` onto a selectable module or the Contest group.

    Args:
        module_hint: ``live_problems.module_hint`` (``A/M1C1``, ``B``, …).
        modules: Selectable module tokens.

    Returns:
        ``M<n>`` when a ``M<n>C<k>`` part names a selectable module, else
        ``COURSE``.
    """
    hint = str(module_hint or "").replace(" ", "").upper()
    for part in (p for p in hint.split("/") if p):
        if part.startswith("M") and "C" in part:
            head = part[1:].split("C", 1)[0]
            if head.isdigit() and f"M{int(head)}" in modules:
                return f"M{int(head)}"
    return COURSE_GROUP


def contest_problems(school: Any, ontario_code: str) -> list[dict[str, Any]]:
    """Active contest ``live_problems`` for one course, in bank order.

    One indexed-size SQL read (the table holds tens of rows).

    Args:
        school: ``SchoolDB``.
        ontario_code: Course code from the class offering.
    """
    placeholders = ",".join("?" for _ in CONTEST_KINDS)
    with school._lock:
        rows = school.conn.execute(
            f"""
            SELECT id, module_hint, kind, title, stem_html, task_html, sort_order
            FROM live_problems
            WHERE active = 1
              AND UPPER(TRIM(ontario_code)) = ?
              AND LOWER(TRIM(kind)) IN ({placeholders})
            ORDER BY sort_order, id
            """,
            (str(ontario_code or "").strip().upper(), *CONTEST_KINDS),
        ).fetchall()
    return [dict(row) for row in rows]


def _prompt_text(row: dict[str, Any]) -> str:
    """Team Challenge prompt text for one problem (stem, blank line, task)."""
    try:
        from live_class_slides import _plain_from_html
        from team_challenge import compose_team_challenge_prompt
    except ImportError:
        from lms.live_class_slides import _plain_from_html
        from lms.team_challenge import compose_team_challenge_prompt

    stem = _plain_from_html(str(row.get("stem_html") or ""))
    task = _plain_from_html(str(row.get("task_html") or ""))
    return compose_team_challenge_prompt(context=stem, question=task) or str(
        row.get("title") or ""
    ).strip()


def _group_label(key: str) -> str:
    """``Module N`` or ``Contest``."""
    return COURSE_GROUP_LABEL if key == COURSE_GROUP else f"Module {key[1:]}"


def _grouped(
    school: Any, ontario_code: str, modules: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Contest problems keyed by group, de-duplicated by id, bank order kept."""
    out: dict[str, list[dict[str, Any]]] = {}
    seen: set[int] = set()
    for row in contest_problems(school, ontario_code):
        pid = int(row["id"])
        if pid in seen:
            continue
        seen.add(pid)
        out.setdefault(problem_group(row.get("module_hint"), modules), []).append(row)
    return out


def module_summaries(
    school: Any, ontario_code: str, modules: list[str], current: str = ""
) -> list[dict[str, Any]]:
    """Groups to show: modules with contest problems, the class's module, Contest.

    Args:
        school: ``SchoolDB``.
        ontario_code: Course code.
        modules: Selectable module tokens.
        current: The class's current module, always listed.

    Returns:
        ``{module, label, count}`` in Module-select order, Contest last.
    """
    grouped = _grouped(school, ontario_code, modules)
    current_key = group_key(current, modules)
    out: list[dict[str, Any]] = []
    for key in [*modules, COURSE_GROUP]:
        count = min(len(grouped.get(key, [])), CONTENT_QUESTIONS_PER_MODULE)
        if count or key == current_key:
            out.append({"module": key, "label": _group_label(key), "count": count})
    return out


def module_content_questions(
    school: Any,
    ontario_code: str,
    module: str,
    modules: list[str],
    *,
    limit: int = CONTENT_QUESTIONS_PER_MODULE,
) -> dict[str, Any]:
    """Return one group's top Contest Questions in bank order.

    Args:
        school: ``SchoolDB``.
        ontario_code: Course code.
        module: Group token (``M1`` or ``COURSE``), already normalized.
        modules: Selectable module tokens.
        limit: How many to keep (6).

    Returns:
        ``module``, ``label``, ``count`` and ``items``. Each item has
        ``question_id`` (= ``live_problem_id``), ``content_rank``,
        ``question_title``, ``text`` (the card prompt), ``type`` ``poll``.
    """
    rows = _grouped(school, ontario_code, modules).get(module, [])
    items: list[dict[str, Any]] = []
    for rank, row in enumerate(rows[: max(0, int(limit))], start=1):
        items.append(
            {
                "question_id": int(row["id"]),
                "live_problem_id": int(row["id"]),
                "module": module,
                "content_rank": rank,
                "question_title": str(row.get("title") or "").strip(),
                "text": _prompt_text(row),
                "type": "poll",
                "curriculum_open": True,
                "kind": "contest",
            }
        )
    return {
        "module": module,
        "label": _group_label(module),
        "count": len(items),
        "items": items,
    }


def clean_content_picks(raw: Any, modules: list[str]) -> list[tuple[int, str]]:
    """Validate posted picks into ``(live_problem_id, group)`` pairs.

    The same id twice in one batch keeps only the first pick.

    Args:
        raw: ``picks`` list of ``{question_id, module}``.
        modules: Selectable module tokens (``COURSE`` is always allowed).

    Returns:
        De-duplicated picks in posted order.

    Raises:
        ValueError: Empty, too many, or a malformed pick.
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("Pick at least one Contest Question.")
    if len(raw) > MAX_CONTENT_PICKS:
        raise ValueError(f"Pick at most {MAX_CONTENT_PICKS} Contest Questions.")
    picks: list[tuple[int, str]] = []
    seen: set[int] = set()
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError("Each pick needs question_id and module.")
        try:
            problem_id = int(row.get("question_id") or row.get("questionId") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Each pick needs question_id and module.") from exc
        key = group_key(row.get("module"), modules)
        if problem_id <= 0 or not key:
            raise ValueError("Each pick needs question_id and module.")
        if problem_id in seen:
            continue
        seen.add(problem_id)
        picks.append((problem_id, key))
    return picks


def _batch_rows(
    school: Any, class_id: int, module: str, slot: str, token: str
) -> list[dict[str, Any]]:
    """Placement rows on this deck that carry one batch token."""
    with school._lock:
        rows = school.conn.execute(
            """
            SELECT id, item_json FROM class_live_playlist_placements
            WHERE class_id = ? AND module = ? AND slot = ? AND item_json LIKE ?
            """,
            (int(class_id), module, slot, f"%{token}%"),
        ).fetchall()
    return [dict(row) for row in rows]


def _landed_ids(rows: list[dict[str, Any]]) -> list[int]:
    """``live_problem_id`` of each placement row (0 when unreadable)."""
    out: list[int] = []
    for row in rows:
        try:
            item = json.loads(row.get("item_json") or "{}")
        except (TypeError, ValueError):
            item = {}
        out.append(int((item or {}).get("live_problem_id") or 0))
    return out


def _roll_back_batch(
    school: Any, class_id: int, module: str, slot: str, token: str
) -> tuple[int, list[int], bool]:
    """Delete every placement carrying ``token`` and resync, best effort.

    Finds rows by the batch token, so a row committed inside the placement
    call before it raised is removed too. Earlier copies of the same
    problem (other tokens) are left alone.

    Returns:
        ``(rows deleted, live problem ids still on the deck, undo_ok)``.
    """
    deleted = 0
    undo_ok = True
    try:
        with school._lock:
            cur = school.conn.execute(
                """
                DELETE FROM class_live_playlist_placements
                WHERE class_id = ? AND module = ? AND slot = ? AND item_json LIKE ?
                """,
                (int(class_id), module, slot, f"%{token}%"),
            )
            school.conn.commit()
            deleted = int(cur.rowcount or 0)
    except Exception:  # noqa: BLE001 - report, never mask the import error
        LOG.exception("MCK-79 contest import rollback delete failed (class %s)", class_id)
        undo_ok = False
        try:
            school.conn.rollback()
        except Exception:  # noqa: BLE001
            LOG.exception("MCK-79 rollback() after failed delete also failed")
    try:
        school._sync_playlist_change(int(class_id), module, slot)
    except Exception:  # noqa: BLE001 - rows are gone; session items resync later
        LOG.exception("MCK-79 contest import rollback resync failed (class %s)", class_id)
        try:
            school.invalidate_live_metadata_cache(
                class_id=int(class_id), module=module, slot=slot
            )
        except Exception:  # noqa: BLE001
            LOG.exception("MCK-79 metadata cache invalidate failed")
    try:
        landed = _landed_ids(_batch_rows(school, class_id, module, slot, token))
    except Exception:  # noqa: BLE001
        LOG.exception("MCK-79 could not re-read the batch after rollback")
        landed = []
        undo_ok = False
    return deleted, landed, undo_ok and not landed


def import_content_questions(
    school: Any,
    class_id: int,
    module: str,
    slot: str,
    picks: list[tuple[int, str]],
    *,
    ontario_code: str,
    modules: list[str],
    page_number: int,
    stage: str,
) -> list[dict[str, Any]]:
    """Import picked Contest Questions onto one class live-lesson page.

    All-or-nothing. Every pick is checked against its group's current top
    six before anything is written. Then each pick goes through the deck's
    Add New path (``add_staff_question_to_class_playlist``, type ``poll``,
    Kind Contest). That function commits per call and resyncs the live
    session, so one SQL transaction is not possible without forking it;
    instead each card carries a batch token, and on any failure every row
    with that token is deleted again and the deck resynced.

    Args:
        school: ``SchoolDB``.
        class_id: Game-show ``classes.id``.
        module: Target live module (``M1``).
        slot: Target live slot (``C2``).
        picks: Output of ``clean_content_picks``.
        ontario_code: Course code for the ``live_problems`` read.
        modules: Selectable module tokens.
        page_number: Teacher page for the placement.
        stage: Lifecycle stage for the placement.

    Returns:
        Placement rows in pick order.

    Raises:
        KeyError: A pick is not in its group's top Contest Questions
            (nothing written).
        ContentImportFailed: A placement failed. ``status`` 409 when the
            batch was undone, 500 when the undo itself failed.
    """
    groups: dict[str, dict[int, dict[str, Any]]] = {}
    for problem_id, key in picks:
        if key not in groups:
            group = module_content_questions(school, ontario_code, key, modules)
            groups[key] = {int(item["question_id"]): item for item in group["items"]}
        if problem_id not in groups[key]:
            raise KeyError(f"problem {problem_id} is not a {_group_label(key)} Contest Question")
    module_key = str(module or "").strip().upper()
    slot_key = str(slot or "").strip().upper()
    token = f"mck79-{uuid.uuid4().hex}"
    placements: list[dict[str, Any]] = []
    try:
        for problem_id, key in picks:
            item = groups[key][problem_id]
            placements.append(
                school.add_staff_question_to_class_playlist(
                    int(class_id),
                    module_key,
                    slot_key,
                    question_type="poll",
                    text=str(item["text"]),
                    page_number=int(page_number),
                    stage=stage,
                    bank_kind="contest",
                    extra_item={
                        "live_problem_id": int(problem_id),
                        "question_title": str(item.get("question_title") or ""),
                        "contest_batch": token,
                    },
                )
            )
    except Exception as exc:  # noqa: BLE001 - any failure rolls the batch back
        deleted, landed, undo_ok = _roll_back_batch(
            school, int(class_id), module_key, slot_key, token
        )
        reason = str(exc).strip("'\"")
        if undo_ok:
            raise ContentImportFailed(
                f"Import stopped and was undone; nothing was imported. {reason}",
                landed=[],
                rolled_back=deleted,
                status=409,
            ) from exc
        raise ContentImportFailed(
            "Import failed and could not be fully undone. Reload the deck and "
            f"remove any extra cards. {reason}",
            landed=landed,
            rolled_back=deleted,
            status=500,
        ) from exc
    return placements
