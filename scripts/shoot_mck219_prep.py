#!/usr/bin/env python3
"""Prepare one closed hand-scored group rank for MCK-219 staff screenshots.

Writes through the local LMS sqlite (same as the dev server). Prints the staff URL.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LMS_DIR = ROOT / "lms"
sys.path.insert(0, str(LMS_DIR))

from app import create_app  # noqa: E402

from shoot import shawn_class_id, staff_shawn_email, db_path  # noqa: E402

OPTIONS = [
    {"id": "o1", "label": "Two points"},
    {"id": "o2", "label": "A graph"},
    {"id": "o3", "label": "An equation"},
    {"id": "o4", "label": "A table"},
]
KEY = ["o3", "o1", "o4", "o2"]


def main() -> int:
    """Close one group rank on an active live session; print staff URL."""
    os.environ.setdefault("LOCAL_DEV_LOGIN", "1")
    path = db_path()
    class_id = shawn_class_id(path)
    email = staff_shawn_email(path)
    app = create_app()
    client = app.test_client()
    school = app.config["SCHOOL_DB"]
    client.get(f"/auth/google/callback?portal=staff&email={email}&name=Shawn")
    while True:
        active = school.get_active_live_session_for_class(class_id)
        if not active:
            break
        school.end_live_class_session(int(active["id"]))
    school.game.begin_game(class_id)
    rv = client.post(f"/api/classes/{class_id}/live-session/start", json={})
    if rv.status_code != 200:
        raise SystemExit(rv.get_data(as_text=True))
    live = rv.get_json()["live_session"]
    session_id = int(live["id"])
    client.post(
        f"/api/live-sessions/{session_id}/teacher-state",
        json={"live_module": "M1", "live_slot": "C2", "stage": "round", "page_id": "round_1"},
    )
    students_all = school.conn.execute(
        "SELECT id FROM students WHERE class_id = ? ORDER BY id", (class_id,)
    ).fetchall()
    present = [int(r["id"]) for r in students_all[:6]]
    rv = client.post(
        f"/api/classes/{class_id}/game/assign",
        json={
            "n_teams": 2,
            "mode": "manual",
            "present_ids": present,
            "assignments": [{"student_id": sid, "team_index": i % 2} for i, sid in enumerate(present)],
        },
    )
    assign_err = rv.get_data(as_text=True)
    if rv.status_code != 200 and "already set up" not in assign_err.lower():
        raise SystemExit(assign_err)
    body = {
        "page_number": 4,
        "stage": "round",
        "type": "rank",
        "text": "MCK-219 group rank",
        "options": OPTIONS,
        "rank_key": KEY,
    }
    rv = client.post(f"/api/staff/class/{class_id}/live-lessons/M1/C2/add-question", json=body)
    if rv.status_code != 200:
        raise SystemExit(rv.get_data(as_text=True))
    placement_id = rv.get_json()["placement"]["item"]["id"]
    items = school.list_live_session_items(session_id)
    row = next(r for r in items if r.get("item_id") == placement_id)
    item_id = int(row["id"])
    rv = client.post(
        f"/api/live-sessions/{session_id}/items/{item_id}/publish",
        json={"publish_mode": "group_submit"},
    )
    if rv.status_code != 200:
        raise SystemExit(rv.get_data(as_text=True))
    live_item = school.get_live_session_item(session_id, item_id)
    question = live_item.get("item") if isinstance(live_item.get("item"), dict) else {}
    order_a = [str(opt_id) for opt_id in (question.get("rank_key") or school._rank_race_key(live_item))]
    order_b = list(order_a)
    if len(order_b) >= 2:
        order_b[0], order_b[1] = order_b[1], order_b[0]
    students = school.conn.execute(
        "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id LIMIT 2",
        (class_id,),
    ).fetchall()
    code = str(live.get("session_code") or "")
    aliases = school.student_rank_aliases(live_item)
    for index, student in enumerate(students):
        student_client = app.test_client()
        rv = student_client.post("/auth/student-code", data={"code": code, "name": str(student["codename"])})
        if rv.status_code not in (302, 200):
            raise SystemExit(rv.get_data(as_text=True))
        order = order_a if index == 0 else order_b
        student_order = [aliases.out(opt) for opt in order] if aliases is not None else order
        for opt in student_order:
            student_client.post(f"/api/student/live-items/{item_id}/rank-tap", json={"option_id": opt})
        rv = student_client.post(f"/api/student/live-items/{item_id}/group-submit", json={"order": student_order})
        if rv.status_code != 200:
            raise SystemExit(rv.get_data(as_text=True))
    rv = client.post(f"/api/live-sessions/{session_id}/items/{item_id}/close", json={})
    if rv.status_code != 200:
        raise SystemExit(rv.get_data(as_text=True))
    origin = os.environ.get("SHOOT_BASE", "http://127.0.0.1:8787").rstrip("/")
    print(f"{origin}/staff/class/{class_id}?tab=live&live_session_id={session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
