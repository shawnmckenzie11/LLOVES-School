#!/usr/bin/env python3
"""Prepare one closed hand-scored group rank for MCK-219 staff screenshots.

Writes through the local LMS sqlite (same as the dev server). Prints the staff URL
on the first line and a JSON metadata object on the second line.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LMS_DIR = ROOT / "lms"
sys.path.insert(0, str(LMS_DIR))

from app import create_app  # noqa: E402

from shoot import shawn_class_id, staff_shawn_email, db_path  # noqa: E402

OPTION_LABELS = ["Two points", "A graph", "An equation", "A table"]
PAGE_NUMBER = 4


def main() -> int:
    """Publish a group rank, submit one team order, close; print URL + metadata."""
    os.environ.setdefault("LOCAL_DEV_LOGIN", "1")
    path = db_path()
    class_id = shawn_class_id(path)
    email = staff_shawn_email(path)
    evidence = f"MCK-219 evidence {uuid.uuid4().hex[:8]}"
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
        "page_number": PAGE_NUMBER,
        "stage": "round",
        "type": "rank",
        "text": evidence,
        "options": OPTION_LABELS,
        "rank_key": ["o3", "o1", "o4", "o2"],
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
    student = school.conn.execute(
        "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id LIMIT 1",
        (class_id,),
    ).fetchone()
    code = str(live.get("session_code") or "")
    aliases = school.student_rank_aliases(live_item)
    student_client = app.test_client()
    rv = student_client.post(
        "/auth/student-code",
        data={"code": code, "name": str(student["codename"])},
    )
    if rv.status_code not in (302, 200):
        raise SystemExit(rv.get_data(as_text=True))
    student_order = [aliases.out(opt) for opt in order_a] if aliases is not None else order_a
    for opt in student_order:
        student_client.post(f"/api/student/live-items/{item_id}/rank-tap", json={"option_id": opt})
    rv = student_client.post(
        f"/api/student/live-items/{item_id}/group-submit",
        json={"order": student_order},
    )
    if rv.status_code != 200:
        raise SystemExit(rv.get_data(as_text=True))
    rv = client.post(f"/api/live-sessions/{session_id}/items/{item_id}/close", json={})
    if rv.status_code != 200:
        raise SystemExit(rv.get_data(as_text=True))
    closed = school.get_live_session_item(session_id, item_id)
    if str(closed.get("status") or "") != "closed":
        raise SystemExit(f"Expected closed item, got {closed.get('status')}")
    origin = os.environ.get("SHOOT_BASE", "http://127.0.0.1:8787").rstrip("/")
    url = f"{origin}/staff/class/{class_id}?tab=live&live_session_id={session_id}"
    meta = {
        "url": url,
        "live_item_id": item_id,
        "page_number": PAGE_NUMBER,
        "evidence_text": evidence,
        "session_id": session_id,
    }
    print(url)
    print(json.dumps(meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
