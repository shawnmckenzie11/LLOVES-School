"""Test helpers to opt into an authored seed or an explicit engine-ride add."""

from __future__ import annotations

import json
import uuid
from typing import Any

from live_class_metadata import default_math_pages, load_live_class_metadata
from minds_on import MINDS_ON_LABEL, minds_on_items
from school_db import _now
from teams_spark import TEAMS_SPARK_PROMPT


def _ensure_blank_owned_deck(
    school: Any, class_id: int, module: str, slot: str
) -> None:
    """Write a blank seed row so schema-v2 owns empty stages (no auto-seed).

    Does not write the three Blank hide-rows, so a later explicit add of
    Minds-On or Teams Spark can still land on the deck.
    """

    module_key, slot_key = str(module).upper(), str(slot).upper()
    stamp = _now()
    with school._lock:
        existing = school.conn.execute(
            """
            SELECT mode FROM class_live_deck_seeds
            WHERE class_id = ? AND module = ? AND slot = ?
            """,
            (int(class_id), module_key, slot_key),
        ).fetchone()
        if existing is not None:
            return
        school.conn.execute(
            """
            INSERT INTO class_live_deck_seeds (
                class_id, module, slot, mode, source_module, source_slot,
                suppress_authored, media_json, team_challenge_json,
                created_at, updated_at, is_test, source_live_session_id
            ) VALUES (?, ?, ?, 'blank', NULL, NULL, 1, NULL, NULL, ?, ?, 0, NULL)
            """,
            (int(class_id), module_key, slot_key, stamp, stamp),
        )
        school.conn.commit()
    school.invalidate_live_metadata_cache(
        class_id=int(class_id), module=module_key, slot=slot_key
    )


def _upsert_ride_placement(
    school: Any,
    class_id: int,
    module: str,
    slot: str,
    *,
    item_id: str,
    payload: dict[str, Any],
    stage: str,
    page_number: int,
) -> None:
    """Insert or keep one engine-ride placement on the class deck."""

    module_key, slot_key = str(module).upper(), str(slot).upper()
    token = str(item_id)
    with school._lock:
        found = school.conn.execute(
            """
            SELECT 1 FROM class_live_playlist_placements
            WHERE class_id = ? AND module = ? AND slot = ? AND item_id = ?
            """,
            (int(class_id), module_key, slot_key, token),
        ).fetchone()
        if found is not None:
            return
        placement_key = f"class:{int(class_id)}:{token}:{uuid.uuid4().hex[:8]}"
        body = dict(payload)
        body["id"] = token
        body["item_id"] = token
        body["stage"] = stage
        body["page_number"] = page_number
        body["order"] = 1
        stamp = _now()
        school.conn.execute(
            """
            INSERT INTO class_live_playlist_placements (
                class_id, module, slot, page_number, stage, sort_order,
                placement_key, item_id, item_json, source_question_id,
                created_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, NULL, ?)
            """,
            (
                int(class_id),
                module_key,
                slot_key,
                page_number,
                stage,
                placement_key,
                token,
                json.dumps(body),
                stamp,
            ),
        )
        school.conn.commit()
    school.invalidate_live_metadata_cache(
        class_id=int(class_id), module=module_key, slot=slot_key
    )


def add_waiting_room_minds_on(
    school: Any,
    class_id: int,
    *,
    module: str = "M1",
    slot: str = "C1",
    session_id: int | None = None,
) -> None:
    """Place the official waiting-room Minds-On on page 1, as a teacher add.

    Does not publish or activate the prompt. Callers that need students
    to see it should publish afterwards.
    """

    _ensure_blank_owned_deck(school, class_id, module, slot)
    item = minds_on_items(slot, module)[0]
    question = {
        "id": "minds_on",
        "item_id": "minds_on",
        "kind": "mc",
        "type": "mc",
        "item_type": "question",
        "text": item["prompt"],
        "prompt": item["prompt"],
        "options": list(item["choices"]),
        "choices": list(item["choices"]),
        "key": item["key"],
        "correct_answer": item["key"],
        "label": MINDS_ON_LABEL,
        "pack": "minds_on",
        "stage": "join",
        "page_number": 1,
        "order": 1,
    }
    _upsert_ride_placement(
        school,
        class_id,
        module,
        slot,
        item_id="minds_on",
        payload=question,
        stage="join",
        page_number=1,
    )
    if session_id is not None:
        school.ensure_live_session_items(int(session_id))


def add_teams_spark(
    school: Any,
    class_id: int,
    *,
    module: str = "M1",
    slot: str = "C1",
    session_id: int | None = None,
) -> None:
    """Place Teams Spark on the Welcome page, as a teacher add."""

    _ensure_blank_owned_deck(school, class_id, module, slot)
    question = {
        "id": "teams-spark",
        "item_id": "teams-spark",
        "kind": "numeric",
        "type": "numeric",
        "item_type": "question",
        "text": TEAMS_SPARK_PROMPT,
        "prompt": TEAMS_SPARK_PROMPT,
        "integer_only": True,
        "pack": "teams-spark",
        "stage": "teams",
        "page_number": 2,
        "order": 1,
    }
    _upsert_ride_placement(
        school,
        class_id,
        module,
        slot,
        item_id="teams-spark",
        payload=question,
        stage="teams",
        page_number=2,
    )
    if session_id is not None:
        school.ensure_live_session_items(int(session_id))


def install_authored_seed(
    school: Any,
    class_id: int,
    module: str,
    slot: str,
    *,
    include_rides: bool = False,
    session_id: int | None = None,
) -> dict[str, Any]:
    """Write this slot's course seed as a saved working copy.

    Engine rides are omitted by default, matching Course deck copy.
    """

    course = school._course_code_for_class(int(class_id))
    meta = load_live_class_metadata(course, module, slot)
    questions = [
        dict(row) for row in meta.get("questions") or [] if isinstance(row, dict)
    ]
    if not include_rides:
        questions = school._without_engine_rides(questions)
    pages = [row for row in (meta.get("pages") or []) if isinstance(row, dict)]
    if not pages:
        pages = default_math_pages()
    media = meta.get("media") if isinstance(meta.get("media"), dict) else None
    challenge = (
        meta.get("team_challenge")
        if isinstance(meta.get("team_challenge"), dict)
        else None
    )
    school._replace_class_deck_working_copy(
        int(class_id),
        module,
        slot,
        mode="course",
        source_module=module,
        source_slot=slot,
        questions=school._serializable_deck_questions(questions),
        pages=pages,
        media=media,
        team_challenge=challenge,
        replace=True,
    )
    if session_id is not None:
        school.ensure_live_session_items(int(session_id))
    return meta
