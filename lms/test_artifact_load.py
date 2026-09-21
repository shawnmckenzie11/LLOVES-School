"""Class-size Artifact open: Group Q must not 500 or rebuild the game.

Production serves live class on one gunicorn worker with two threads
(``lms/Dockerfile``) and a shared-cpu-1x Fly machine. Opening an Artifact
for group work used to call ``game_state`` on every teacher-state read.
Sixteen students plus the teacher then rebuilt the full roster hundreds of
times in one wave. That pegged the machine (Fly "server overloaded") and
any escaped error became an HTML 500.

This test is the ops re-run. It writes ``lc-qa/artifact-load-latest.md``
and ``lc-qa/artifact-load-latest.log``.
"""

from __future__ import annotations

import os
import statistics
import sys
import tempfile
import threading
import time
import unittest
from collections import Counter
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from artifact import C2_TRANSFORM_MEDIA_URL, TRANSFORMATIONS_ARTIFACT_ID  # noqa: E402

# N=16 matches the #116 idle bar. The teacher is the 17th party.
CLASS_SIZE = 16
# gunicorn ``--threads 2``. Extra requests wait, the way Fly queues them.
GUNICORN_THREADS = 2
# One student poll may still build the live game once for points and once
# for the group list. The old path was ~25 ``game_state`` calls.
STUDENT_GAME_STATE_BUDGET = 4
# Whole open wave. The pre-fix fanout was ~600.
WAVE_GAME_STATE_BUDGET = 80
REPORT_MD = REPO_ROOT / "lc-qa" / "artifact-load-latest.md"
REPORT_LOG = REPO_ROOT / "lc-qa" / "artifact-load-latest.log"
CODENAMES = [f"S{index:02d}" for index in range(CLASS_SIZE)]


def _write_report(markdown: str, log_text: str) -> None:
    """Write the ops log next to the repo so a re-run leaves a file.

    Args:
        markdown: Human report.
        log_text: Line-oriented timing log.
    """
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(markdown, encoding="utf-8")
    REPORT_LOG.write_text(log_text, encoding="utf-8")


class ArtifactGroupLoadTests(unittest.TestCase):
    """Artifact mint, media, and Group Q previews under a full class."""

    def setUp(self) -> None:
        """Sixteen joined students, four teams, Run as Group on."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        self.school = self.app.config["SCHOOL_DB"]
        self.game_state_calls = 0
        original = self.school.game.game_state

        def _counted(*args: Any, **kwargs: Any) -> Any:
            """Count full game rebuilds. The load bar fails if this spikes."""
            self.game_state_calls += 1
            return original(*args, **kwargs)

        self.school.game.game_state = _counted  # type: ignore[method-assign]
        self.staff = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.staff.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=CODENAMES,
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        self.session_code = str(live["session_code"])
        self.school.game.begin_game(self.class_id)
        with self.school.game._lock:
            rows = self.school.game.conn.execute(
                """
                SELECT id FROM students
                WHERE class_id = ?
                ORDER BY id ASC
                """,
                (self.class_id,),
            ).fetchall()
        self.student_ids = [int(row["id"]) for row in rows]
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=4,
            mode="manual",
            present_ids=self.student_ids,
            assignments=[
                {"student_id": sid, "team_index": index % 4}
                for index, sid in enumerate(self.student_ids)
            ],
        )
        self.clients: list[tuple[str, Any]] = []
        for name in CODENAMES:
            client = self.app.test_client()
            joined = client.post(
                "/auth/student-code",
                data={"code": self.session_code, "name": name},
                follow_redirects=False,
            )
            self.assertEqual(joined.status_code, 302, joined.get_data(as_text=True)[:300])
            client.post("/student/mood", data={"mood": "good"})
            client.post("/student/character", data={"character": "fox"})
            self.clients.append((name, client))

    def tearDown(self) -> None:
        """Close sqlite and drop the temp database."""
        self.school.close()
        self.tmp.cleanup()

    def _mint(self, *, snapshot: dict[str, float], mode: str = "graph") -> Any:
        """POST one Group Q Artifact mint.

        Args:
            snapshot: Teacher slider values.
            mode: ``graph`` or ``equation``.
        """
        return self.staff.post(
            f"/api/live-sessions/{self.session_id}/artifacts",
            json={
                "artifact_id": TRANSFORMATIONS_ARTIFACT_ID,
                "snapshot": snapshot,
                "target_mode": mode,
                "group_q": True,
                "hot_cold_visible": True,
            },
        )

    def test_student_poll_does_not_rebuild_game_per_read(self) -> None:
        """One student snapshot after Group Q mint stays within the budget."""
        minted = self._mint(snapshot={"a": 2, "h": 1, "k": -1})
        self.assertEqual(minted.status_code, 200, minted.get_data(as_text=True)[:500])
        body = minted.get_json()
        self.assertTrue(body["prompt"]["payload"]["group_q"])
        self.assertEqual(body["active_media"]["url"], C2_TRANSFORM_MEDIA_URL)
        self.game_state_calls = 0
        student = self.clients[0][1].get("/api/student/state")
        self.assertEqual(student.status_code, 200, student.get_data(as_text=True)[:500])
        payload = student.get_json()
        self.assertNotEqual(payload.get("error"), "state unavailable")
        self.assertIn("group_q_ready", payload)
        self.assertEqual(
            (payload.get("active_media") or {}).get("url"),
            C2_TRANSFORM_MEDIA_URL,
        )
        self.assertLessEqual(
            self.game_state_calls,
            STUDENT_GAME_STATE_BUDGET,
            f"student /state rebuilt game_state {self.game_state_calls} times",
        )

    def test_mint_failure_is_json_retry_not_html_500(self) -> None:
        """A mint crash is a JSON 503, not Internal Server Error HTML."""

        def boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("artifact store locked")

        self.school.mint_live_artifact = boom  # type: ignore[method-assign]
        rv = self._mint(snapshot={"a": 1, "h": 0, "k": 0})
        text = rv.get_data(as_text=True)
        self.assertEqual(rv.status_code, 503, text[:400])
        self.assertNotIn("Internal Server Error", text)
        body = rv.get_json()
        self.assertFalse(body["ok"])
        self.assertTrue(body["retry"])

    def test_class_open_wave_stays_healthy(self) -> None:
        """Teacher mint plus N=16 media, state, and previews: 0×500.

        Requests are gated to two at a time, matching production gunicorn.
        """
        seeded = self._mint(snapshot={"a": 2, "h": 1, "k": -1})
        self.assertEqual(seeded.status_code, 200, seeded.get_data(as_text=True)[:400])
        prompt_id = int(seeded.get_json()["prompt"]["id"])
        self.game_state_calls = 0
        gate = threading.Semaphore(GUNICORN_THREADS)
        lock = threading.Lock()
        errors: list[str] = []
        rows: list[str] = []
        codes: Counter[str] = Counter()
        latencies: dict[str, list[float]] = {}

        def record(label: str, rv: Any, started: float) -> None:
            """Store one timed response. 500s and reconnect stubs are failures."""
            elapsed_ms = (time.perf_counter() - started) * 1000
            text = rv.get_data(as_text=True)
            body = rv.get_json(silent=True) or {}
            line = f"{label}\t{rv.status_code}\t{elapsed_ms:.1f}ms"
            with lock:
                codes[f"{label}:{rv.status_code}"] += 1
                latencies.setdefault(label, []).append(elapsed_ms)
                rows.append(line)
                if rv.status_code >= 500 or "Internal Server Error" in text:
                    errors.append(f"{line} {text[:240]}")
                elif body.get("error") == "state unavailable":
                    errors.append(f"{line} reconnect stub")
                elif rv.status_code != 200 or (
                    label != "media" and body.get("ok") is False
                ):
                    errors.append(f"{line} {text[:240]}")

        def run(label: str, fn: Any) -> None:
            """Hold a worker slot, then call the route."""
            gate.acquire()
            started = time.perf_counter()
            try:
                record(label, fn(), started)
            except Exception as exc:  # noqa: BLE001 — the log must show the crash
                with lock:
                    errors.append(f"{label} EXC {exc!r}")
            finally:
                gate.release()

        threads = [
            threading.Thread(
                target=run,
                args=(
                    "mint",
                    lambda: self._mint(
                        snapshot={"a": 3, "h": -1, "k": 2}, mode="equation"
                    ),
                ),
            ),
            threading.Thread(
                target=run,
                args=(
                    "staff-state",
                    lambda: self.staff.get(
                        f"/api/live-sessions/{self.session_id}/state"
                    ),
                ),
            ),
            threading.Thread(
                target=run,
                args=(
                    "staff-media",
                    lambda: self.staff.get(
                        f"/api/live-sessions/{self.session_id}/active-media"
                    ),
                ),
            ),
        ]
        for name, client in self.clients:
            threads.append(
                threading.Thread(
                    target=run,
                    args=("state", lambda client=client: client.get("/api/student/state")),
                )
            )
            threads.append(
                threading.Thread(
                    target=run,
                    args=(
                        "media",
                        lambda client=client: client.get(C2_TRANSFORM_MEDIA_URL),
                    ),
                )
            )
            threads.append(
                threading.Thread(
                    target=run,
                    args=(
                        "preview",
                        lambda client=client: client.post(
                            "/api/student/live-prompt/artifact-preview",
                            json={
                                "prompt_id": prompt_id,
                                "params": {"a": 2.0, "h": 1.0, "k": -1.0},
                            },
                        ),
                    ),
                )
            )
        wall_started = time.perf_counter()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        wall_ms = (time.perf_counter() - wall_started) * 1000
        summary_lines = [
            f"wall_ms={wall_ms:.1f}",
            f"game_state_calls={self.game_state_calls}",
            f"gunicorn_threads={GUNICORN_THREADS}",
            f"class_size={CLASS_SIZE}",
            f"errors={len(errors)}",
        ]
        for label, values in sorted(latencies.items()):
            ordered = sorted(values)
            p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
            summary_lines.append(
                f"{label} n={len(values)} med={statistics.median(values):.1f}ms "
                f"p95={p95:.1f}ms max={max(values):.1f}ms"
            )
        summary_lines.append("codes " + " ".join(
            f"{key}={count}" for key, count in sorted(codes.items())
        ))
        log_text = "\n".join(summary_lines + ["---"] + rows) + "\n"
        passed = not errors and self.game_state_calls <= WAVE_GAME_STATE_BUDGET
        markdown = _report_markdown(
            wall_ms=wall_ms,
            game_state_calls=self.game_state_calls,
            codes=codes,
            latencies=latencies,
            errors=errors,
            passed=passed,
        )
        _write_report(markdown, log_text)
        self.assertEqual(errors, [], errors[:4])
        self.assertLessEqual(self.game_state_calls, WAVE_GAME_STATE_BUDGET)
        self.assertLess(wall_ms, 3000)
        self.assertEqual(codes["state:200"], CLASS_SIZE)
        self.assertEqual(codes["media:200"], CLASS_SIZE)
        self.assertEqual(codes["preview:200"], CLASS_SIZE)
        self.assertEqual(codes["mint:200"], 1)
        self.assertEqual(codes["staff-state:200"], 1)
        self.assertEqual(codes["staff-media:200"], 1)
        self.assertNotIn("state:500", codes)
        self.assertNotIn("mint:500", codes)


def _report_markdown(
    *,
    wall_ms: float,
    game_state_calls: int,
    codes: Counter[str],
    latencies: dict[str, list[float]],
    errors: list[str],
    passed: bool,
) -> str:
    """Build the ops report for one Artifact open wave.

    Args:
        wall_ms: Wall time for the gated wave.
        game_state_calls: Full ``game_state`` rebuilds during the wave.
        codes: ``label:status`` counts.
        latencies: Milliseconds by label.
        errors: Failure lines. Empty when the wave stayed healthy.
        passed: True when the bar was met.

    Returns:
        Markdown report.
    """
    latency_rows = []
    for label, values in sorted(latencies.items()):
        ordered = sorted(values)
        p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
        latency_rows.append(
            f"| {label} | {len(values)} | {statistics.median(values):.0f} | "
            f"{p95:.0f} | {max(values):.0f} |"
        )
    code_line = ", ".join(f"{key}={count}" for key, count in sorted(codes.items()))
    error_block = "\n".join(f"- {line}" for line in errors[:12]) or "- none"
    verdict = "PASS" if passed else "FAIL"
    return f"""# Artifact group-work load ({verdict})

Re-run:

```bash
python3 -m unittest discover -s lms -p 'test_artifact_load.py' -v
```

Writes this file and `lc-qa/artifact-load-latest.log`.

## What failed in class

On alc, opening an **Artifact for group work** returned Internal Server Error / server overloaded for the teacher and students. #115 and #116 kept idle `/state` at 200. They did not cover Artifact mint, active media, or Group Q preview fanout.

Production is gunicorn **1 worker, 2 threads** on a **shared-cpu-1x / 1gb** Fly machine (`fly.toml`, `lms/Dockerfile`). No Fly deploy in this change.

## Cause

`live_session_teacher_state_payload` called `_named_teams_for_live_session` → full `game_state` (roster, scores, career totals, moods) on **every** teacher-state read once groups existed.

One student `/api/student/state` after a Group Q mint did that about **25 times**. A class of 16, plus previews and the teacher mint, did it about **600 times** in one wave. That pegs the shared CPU on the 2-thread worker. An exception then renders Flask's default HTML 500, whose text is: "Internal Server Error … Either the server is overloaded or there is an error in the application." That is the page teacher and students saw. It is not the #115/#116 idle `/state` path.

Group Q team checks and the session timer used the same full rebuild.

## Fix

- Teacher-state only asks whether a named team exists, via one locked membership read (cached 0.5s for the poll wave).
- Group Q teammate checks use that index. They do not rebuild the game.
- The student SessionTimer reads the open game's clock columns.
- The student prompt builds the question list once per poll.
- Mint, active-media, and artifact-preview failures return JSON `503` `retry: true` instead of an HTML 500.

## This run

| | |
|---|---|
| Verdict | **{verdict}** |
| Class | {CLASS_SIZE} students + teacher |
| In-flight cap | {GUNICORN_THREADS} (gunicorn threads) |
| Wall | {wall_ms:.0f} ms |
| `game_state` calls | {game_state_calls} (budget {WAVE_GAME_STATE_BUDGET}) |
| HTTP | {code_line} |

| Path | n | med ms | p95 ms | max ms |
|---|---:|---:|---:|---:|
{chr(10).join(latency_rows)}

### Errors

{error_block}

## Residual

- Fly machine size is unchanged: shared-cpu-1x, 1 GB, 2 threads. A different heavy path can still saturate that VM. This wave no longer rebuilds the game per teacher-state read.
- Staff heavy `/state` is still the #115 path (field isolation, 200). This test rides one heavy staff poll in the same wave and expects 200.
- The 0.5s membership cache can lag a team edit by one student poll. Artifact open does not edit teams.
- Not smoked on Fly. Re-run this test on tip `:8787` only if you want the same protocol against the dev server; the in-process bar above is the regression lock.
"""


if __name__ == "__main__":
    unittest.main()
