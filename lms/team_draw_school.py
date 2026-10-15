"""MCK-242: SchoolDB methods for Team Draw (registered on ``SchoolDB``)."""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from team_draw import (
    TEAM_DRAW_RESPONSE_MODE,
    decode_png_payload,
    gallery_row_title,
    is_team_draw_item,
    mystery_steps_for_item,
    resolved_prompt_text,
    should_snapshot_team_draw_run,
    status_label,
    strip_image_from_snapshot_answer,
    team_draw_answer_json,
)

if TYPE_CHECKING:
    from school_db import SchoolDB


def register_team_draw_methods(cls: type) -> None:
    """Attach Team Draw helpers to ``SchoolDB``."""

    cls._ensure_team_draw_schema = _ensure_team_draw_schema  # type: ignore[attr-defined]
    cls._initialize_team_draw = _initialize_team_draw  # type: ignore[attr-defined]
    cls._publish_team_draw_whiteboard = _publish_team_draw_whiteboard  # type: ignore[attr-defined]
    cls.team_draw_teacher_view = team_draw_teacher_view  # type: ignore[attr-defined]
    cls.student_team_draw_state = student_team_draw_state  # type: ignore[attr-defined]
    cls.submit_team_draw = submit_team_draw  # type: ignore[attr-defined]
    cls.team_draw_gallery = team_draw_gallery  # type: ignore[attr-defined]
    cls.team_draw_end_and_clear = team_draw_end_and_clear  # type: ignore[attr-defined]
    cls._purge_team_draw_images = _purge_team_draw_images  # type: ignore[attr-defined]
    cls._purge_team_board_key = _purge_team_board_key  # type: ignore[attr-defined]
    cls._store_team_draw_image = _store_team_draw_image  # type: ignore[attr-defined]


def _ensure_team_draw_schema(self: SchoolDB) -> None:
    """Session-only PNG storage for gallery (never snapshotted)."""
    self.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS live_team_draw_images (
            live_session_id INTEGER NOT NULL,
            live_item_id INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            image_png BLOB NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (live_session_id, live_item_id, team_id)
        )
        """
    )


def _initialize_team_draw(self: SchoolDB, item: dict[str, Any]) -> None:
    """Same team rows as group submit, without MC fields."""
    self._initialize_group_submit(item)


def _publish_team_draw_whiteboard(self: SchoolDB, session_id: int) -> None:
    """Publish the deck whiteboard to the group when Team Draw goes live."""
    self.ensure_live_session_items(session_id)
    for row in self.list_live_session_items(session_id):
        if str(row.get("kind") or "") != "whiteboard":
            continue
        if str(row.get("status") or "") == "active":
            if str(row.get("publish_mode") or "") != "group_shared":
                self.update_live_session_item_settings(
                    session_id,
                    int(row["id"]),
                    publish_mode="group_shared",
                )
            return
        self.publish_live_session_item(
            session_id, int(row["id"]), publish_mode="group_shared"
        )
        return


def _purge_team_draw_images(
    self: SchoolDB, session_id: int, live_item_id: int | None = None
) -> None:
    """Delete ephemeral gallery blobs for one item or the whole session."""
    with self._lock:
        if live_item_id is None:
            self.conn.execute(
                "DELETE FROM live_team_draw_images WHERE live_session_id = ?",
                (int(session_id),),
            )
        else:
            self.conn.execute(
                """
                DELETE FROM live_team_draw_images
                WHERE live_session_id = ? AND live_item_id = ?
                """,
                (int(session_id), int(live_item_id)),
            )
        self.conn.commit()


def _purge_team_board_key(self: SchoolDB, session_id: int, board_key: str) -> None:
    """Remove ink for one team board without ending the run."""
    boards = getattr(self, "boards", None)
    if boards is None:
        return
    try:
        run_key, _gen = self._live_board_base_run_key(session_id)
    except KeyError:
        return
    boards.purge_board_key(run_key, str(board_key))


def _store_team_draw_image(
    self: SchoolDB,
    session_id: int,
    live_item_id: int,
    team_id: int,
    png: bytes,
    *,
    now: str,
) -> None:
    """Upsert one team's gallery PNG."""
    with self._lock:
        self.conn.execute(
            """
            INSERT INTO live_team_draw_images (
                live_session_id, live_item_id, team_id, image_png, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(live_session_id, live_item_id, team_id) DO UPDATE SET
                image_png = excluded.image_png,
                updated_at = excluded.updated_at
            """,
            (int(session_id), int(live_item_id), int(team_id), png, now),
        )
        self.conn.commit()


def team_draw_teacher_view(
    self: SchoolDB, session_id: int, item: dict[str, Any]
) -> dict[str, Any]:
    """Staff grid: drawing / submitted plus title; gallery metadata."""
    from participation_exclusion import exclude_participation_answer_student_ids

    session_row = self.get_live_session(session_id)
    class_id = int(session_row["class_id"]) if session_row else 0
    excluded = exclude_participation_answer_student_ids(class_id, self)
    status_board: list[dict[str, Any]] = []
    for team_id, team_name, state in self._iter_group_submit_teams(session_id, item):
        count = int(state.get("submit_count") or 0)
        submitted = count > 0
        title = gallery_row_title(state) if submitted else ""
        status_board.append(
            {
                "team_id": team_id,
                "team_name": team_name,
                "submitted": submitted,
                "status_label": status_label(submitted),
                "title": title,
            }
        )
    prompt = resolved_prompt_text(item)
    view: dict[str, Any] = {
        "item": item,
        "response_mode": TEAM_DRAW_RESPONSE_MODE,
        "prompt": prompt,
        "mystery_steps": mystery_steps_for_item(item),
        "status_board": status_board,
        "response_count": sum(1 for row in status_board if row["submitted"]),
        "eligible_count": len(status_board),
        "scoring": False,
        "excluded_student_ids": sorted(excluded),
    }
    seq = self._merge_teacher_quiet_dots_from_light(
        session_id, int(item["id"]), status_board=status_board
    )
    if seq:
        view["group_response_seq"] = seq
    return view


def student_team_draw_state(
    self: SchoolDB, item: dict[str, Any], student_id: int | None
) -> dict[str, Any] | None:
    """Student card: prompt, mystery steps, submit eligibility."""
    if student_id in (None, "") or str(item.get("response_mode") or "") != TEAM_DRAW_RESPONSE_MODE:
        return None
    session_id = int(item["live_session_id"])
    try:
        team_id = self._group_submit_read_team(item, student_id)
    except ValueError:
        return None
    row = self._group_response_row(int(item["id"]), int(team_id)) or {}
    submitted = int(row.get("submit_count") or 0) > 0
    final = row.get("final_answer") if isinstance(row.get("final_answer"), dict) else {}
    teams = {
        int(team["id"]): team
        for team in self._named_teams_for_live_session(session_id)
    }
    active = str(item.get("status") or "") == "active"
    question = item.get("item") if isinstance(item.get("item"), dict) else {}
    prompt_id = str(question.get("team_draw_prompt_id") or "").strip()
    return {
        "team_id": int(team_id),
        "team_name": str((teams.get(int(team_id)) or {}).get("name") or ""),
        "prompt": resolved_prompt_text(item),
        "mystery_steps": mystery_steps_for_item(item) if prompt_id == "mystery_rule" else [],
        "submitted": submitted,
        "title": str(final.get("title") or "").strip(),
        "can_submit": active and not submitted,
        "phase": "done" if submitted else "draw",
    }


def submit_team_draw(
    self: SchoolDB,
    session_id: int,
    live_item_id: int,
    student_id: int,
    *,
    title: str,
    image_b64: str,
) -> dict[str, Any]:
    """Store title as the group answer and PNG for today's gallery only."""
    from school_db import GroupAnswerLocked, _now

    item = self.get_live_session_item(session_id, live_item_id)
    if str(item.get("response_mode") or "") != TEAM_DRAW_RESPONSE_MODE:
        raise ValueError("Not a Team Draw question.")
    if str(item.get("status") or "") != "active":
        card = self.student_team_draw_state(item, student_id)
        raise GroupAnswerLocked("This question is closed.", card=card)
    team_id = self._require_group_submit_member(item, student_id)
    row = self._group_response_row(int(item["id"]), int(team_id)) or {}
    if int(row.get("submit_count") or 0) > 0:
        card = self.student_team_draw_state(item, student_id)
        raise GroupAnswerLocked("Your team already submitted.", card=card)
    clean_title = str(title or "").strip()
    if not clean_title:
        raise ValueError("Name your masterpiece before submitting.")
    png = decode_png_payload(image_b64)
    answer = team_draw_answer_json(clean_title)
    now = _now()
    with self._lock:
        self.conn.execute(
            """
            UPDATE live_group_responses
            SET status = 'submitted',
                final_answer_json = ?,
                proposed_answer_json = ?,
                finalizer_student_id = ?,
                submit_count = 1,
                submitter_ids_json = ?,
                why_text = '',
                updated_at = ?,
                finalized_at = ?
            WHERE live_item_id = ? AND team_id = ?
            """,
            (
                json.dumps(answer),
                json.dumps(answer),
                int(student_id),
                json.dumps([int(student_id)]),
                now,
                now,
                int(item["id"]),
                int(team_id),
            ),
        )
        self.conn.commit()
    self._store_team_draw_image(session_id, int(item["id"]), int(team_id), png, now=now)
    self._purge_team_board_key(session_id, f"team:{int(team_id)}")
    card = self.student_team_draw_state(item, student_id) or {}
    return card


def team_draw_gallery(
    self: SchoolDB, session_id: int, live_item_id: int
) -> list[dict[str, Any]]:
    """Teacher gallery rows with data URLs (session only)."""
    import base64

    item = self.get_live_session_item(session_id, live_item_id)
    if not is_team_draw_item(item):
        raise ValueError("Not a Team Draw question.")
    rows: list[dict[str, Any]] = []
    for team_id, team_name, state in self._iter_group_submit_teams(session_id, item):
        blob_row = self.conn.execute(
            """
            SELECT image_png FROM live_team_draw_images
            WHERE live_session_id = ? AND live_item_id = ? AND team_id = ?
            """,
            (int(session_id), int(live_item_id), int(team_id)),
        ).fetchone()
        image_url = ""
        if blob_row and blob_row["image_png"]:
            encoded = base64.b64encode(bytes(blob_row["image_png"])).decode("ascii")
            image_url = f"data:image/png;base64,{encoded}"
        rows.append(
            {
                "team_id": team_id,
                "team_name": team_name,
                "title": gallery_row_title(state),
                "submitted": int(state.get("submit_count") or 0) > 0,
                "image_url": image_url,
            }
        )
    return rows


def team_draw_end_and_clear(self: SchoolDB, session_id: int, live_item_id: int) -> dict[str, Any]:
    """Drop all team boards and gallery images for this Team Draw item."""
    item = self.get_live_session_item(session_id, live_item_id)
    if not is_team_draw_item(item):
        raise ValueError("Not a Team Draw question.")
    session_row = self.get_live_session(session_id)
    if session_row is None:
        raise KeyError(f"live session {session_id}")
    for team in self._named_teams_for_live_session(session_id):
        self._purge_team_board_key(session_id, f"team:{int(team['id'])}")
    self._purge_team_draw_images(session_id, int(live_item_id))
    return self.team_draw_teacher_view(session_id, item)
