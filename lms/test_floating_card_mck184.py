"""MCK-184: a lifted (floating) question card keeps repainting (real browser).

A floating card used to keep its old DOM forever: after a Take turns pick
greyed the buttons, the picker's card never showed the pick or any later
turn. This drives two tablets and a phone in headless Chrome against the
real app on a seeded #236 bank rank (Take turns) plus a multiple-choice
card.

Needs ``node``, Chrome, and ``playwright-core`` on ``NODE_PATH`` (set
``LLOVES_E2E_NODE_PATH``); otherwise it is skipped.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
SCRIPT = LMS_DIR / "e2e" / "floating_card_mck184.mjs"
NAMES = ["Eva", "Iggy", "Ava"]
TEAMS = [["Eva", "Iggy"], ["Ava"]]
SEED = json.loads((LMS_DIR / "seeds" / "rank_items" / "MCR3U.json").read_text(encoding="utf-8"))
SEED_ITEM = next(row for row in SEED["items"] if row["id"] == "MCR3U-M1-inverse-interchange")


def _chrome() -> str:
    for path in (os.environ.get("LLOVES_E2E_CHROME", ""), "/usr/bin/google-chrome", "/usr/bin/chromium"):
        if path and Path(path).exists():
            return path
    return ""


def _node_path() -> str:
    return os.environ.get("LLOVES_E2E_NODE_PATH", "").strip()


def _ready() -> bool:
    if not (shutil.which("node") and _chrome() and _node_path()):
        return False
    env = {**os.environ, "NODE_PATH": _node_path()}
    probe = subprocess.run(
        ["node", "-e", "require.resolve('playwright-core')"], env=env, capture_output=True, check=False
    )
    return probe.returncode == 0


@unittest.skipUnless(_ready(), "node + Chrome + playwright-core (LLOVES_E2E_NODE_PATH) not available")
class FloatingCardBrowserTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.WARNING)
        os.environ["ALLOW_DEV_VERIFICATION_CODE"] = "1"
        from app import create_app

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        s = self.school
        self.client = self.app.test_client()
        s.activate_from_semester_json()
        teacher = s.register_staff("teacher@gmail.com")
        offering = s.assign_course(teacher_user_id=int(teacher["id"]), ontario_code="MCR3U")
        self.class_id = int(
            s.game.create_class(
                year="2026/27", semester="Semester 1", course_code="MCR3U", days_preset="M/W/F",
                time_label="2:00pm", codenames=NAMES, offering_id=int(offering["id"]),
                teacher_user_id=int(teacher["id"]),
            )["id"]
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post("/verify-email", data={"code": s.get_user_by_email("teacher@gmail.com")["verification_code"]})
        live = s.start_live_class_session(self.class_id, int(teacher["id"]))
        self.session_id = int(live["id"])
        self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C2", "stage": "round", "page_id": "round_1"},
        )
        s.game.begin_game(self.class_id)
        with s.game._lock:
            rows = s.game.conn.execute(
                "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id", (self.class_id,)
            ).fetchall()
        ids = {str(r["codename"]): int(r["id"]) for r in rows}
        self.students: dict[str, Any] = {}
        self.visit: dict[str, str] = {}
        for name in NAMES:
            c = self.app.test_client()
            rv = c.post("/auth/student-code", data={"code": str(live["session_code"]), "name": name})
            m = re.search(r"[?&]v=([^&]+)", rv.headers.get("Location", ""))
            v = m.group(1) if m else ""
            c.post("/student/mood", data={"visit_token": v, "skip": "1"})
            page = c.get(f"/student/character?v={v}").get_data(as_text=True)
            pick = re.search(r'name="character" value="([^"]+)"', page)
            if pick:
                c.post("/student/character", data={"visit_token": v, "character": pick.group(1)})
            self.students[name], self.visit[name] = c, v
        s.setup_live_session_groups(
            self.session_id, n_teams=2, mode="manual", present_ids=[ids[n] for n in NAMES],
            assignments=[{"student_id": ids[n], "team_index": t} for t, team in enumerate(TEAMS) for n in team],
        )
        s.ensure_live_session_items(self.session_id)

    def tearDown(self) -> None:
        if getattr(self, "server", None) is not None:
            self.server.shutdown()
        logging.disable(logging.NOTSET)
        self.tmp.cleanup()

    def _publish(self, live: dict[str, Any], mode: str) -> dict[str, Any]:
        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{live['id']}/publish", json={"publish_mode": mode}
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return rv.get_json()["item"]

    def _seeded_turns_item(self) -> dict[str, Any]:
        row = self.school.conn.execute("SELECT offering_id FROM classes WHERE id = ?", (self.class_id,)).fetchone()
        library_id = int(self.school.create_library("MCR3U", origin="upload")["id"])
        self.school.attach_library(int(row["offering_id"]), library_id)
        rv = self.client.get(f"/api/staff/class/{self.class_id}/module-banks/M1/mc-search", query_string={"type": "rank"})
        hit = next(r for r in rv.get_json()["items"] if r.get("prompt") == SEED_ITEM["payload"]["text"])
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={"question_id": int(hit["question_id"]), "page_number": 4, "stage": "round"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        placed = rv.get_json()["placement"]["item"]
        self.assertEqual(placed["group_rank_mode"], "turns")
        self.school.ensure_live_session_items(self.session_id)
        live = next(r for r in self.school.list_live_session_items(self.session_id) if r["placement_key"] == placed["placement_key"])
        return self._publish(live, "group_submit")

    def _mc_item(self) -> dict[str, Any]:
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json={"page_number": 4, "stage": "round", "type": "mc", "text": "Slope of y = 2x + 1?",
                  "options": ["2", "1", "-2", "0"], "correct_index": 0},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item_id = rv.get_json()["placement"]["item"]["id"]
        live = next(r for r in self.school.list_live_session_items(self.session_id) if r["item_id"] == item_id)
        return self._publish(live, "individual")

    def test_lifted_cards_repaint(self) -> None:
        from werkzeug.serving import make_server

        turns = self._seeded_turns_item()
        mc = self._mc_item()
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

        def jar(c: Any) -> dict[str, str]:
            return {ck.key: ck.value for ck in c._cookies.values()}

        info = {
            "port": self.server.server_port,
            "chrome": _chrome(),
            "turns_id": int(turns["id"]),
            "mc_id": int(mc["id"]),
            "students": {n: {"cookies": jar(self.students[n]), "v": self.visit[n]} for n in NAMES},
        }
        cfg = Path(self.tmp.name) / "e2e.json"
        cfg.write_text(json.dumps(info), encoding="utf-8")
        proc = subprocess.run(
            ["node", str(SCRIPT), str(cfg)],
            env={**os.environ, "NODE_PATH": _node_path()},
            capture_output=True, text=True, timeout=240, check=False,
        )
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
        self.assertTrue(lines, proc.stderr[-2000:] + proc.stdout[-2000:])
        out = json.loads(lines[-1])
        self.assertEqual(out["errors"], [], out)
        self.assertTrue(out["liftIggy"] and out["liftEva"] and out["liftMc"], out)
        failed = [s for s in out["steps"] if not s["ok"]]
        self.assertEqual(failed, [], json.dumps(out["steps"], ensure_ascii=False, indent=1))
        self.assertEqual(len(out["steps"]), 6, out["steps"])
        self.assertEqual(out["phone"], {"pill": "none", "floating": False})


if __name__ == "__main__":
    unittest.main()
