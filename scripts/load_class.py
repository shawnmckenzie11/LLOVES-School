#!/usr/bin/env python3
"""Reproducible live-class load test (MCK-231).

Seeds a private data dir with one MCF3M class of N students in a live run,
starts gunicorn with the production flags (4 workers x 8 threads, timeout
120, backlog 64; only the bind changes), then drives it for ``--minutes``:

* every student holds the LiveNewsWire SSE stream (25 s holds, reconnect),
  polls ``/api/student/state`` every 4 s, ``/api/student/board/mine``
  every 20 s and reloads ``/student/home`` every 60 s;
* the teacher polls ``/api/live-sessions/<sid>/state`` every 2 s and
  reloads ``/staff/class/<id>`` every 30 s;
* every ``--round-minutes`` the teacher moves the page and publishes the
  next rank question (take turns), and each team places its options;
* about once a minute the teacher adds a question to the deck (a mid-class
  edit, which changes ``class_deck_revision`` for every worker);
* a sampler records RSS per worker every 15 s and notices worker restarts.

Outputs (in ``--out``): ``samples.csv`` (time, pid, rss_mb), ``requests.csv``
(time, kind, status, ms), ``summary.json`` and ``summary.md``.

The 1 GB limit is checked, not enforced: the box has no cgroup scope, so
the summary flags any sample where the sum of worker + master RSS > 1024 MB.

Usage::

    python scripts/load_class.py --minutes 60 --students 30 --out lc-qa/load-class/run1

The deploy gate runs a shorter smoke on the CI runner, never against
production. Two classes of 15 students is 30 seats on one gunicorn::

    python scripts/load_class.py --minutes 1 --classes 2 --students 15 \\
        --items 2 --gate --max-p95-ms 250 --min-classes 2 \\
        --out /tmp/load-smoke

Pass ``--tracemalloc`` to dump the top allocation growth per worker every
5 minutes (slows the server; profiling only). Needs Postgres for live
presence at ``--presence-url`` (default local lloves_live), like prod.
The presence host must be loopback. ``--gate`` exits non-zero unless
there are 0 errors, HTTP p95 is under the limit, and 0 workers restarted.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import shlex
import signal
import statistics
import string
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
LMS = REPO / "lms"

SECRET = "load-class-secret-not-for-prod-0123456789abcdef"
DEFAULT_PRESENCE = "postgresql://lloves:lloves@127.0.0.1:5432/lloves_live"
RANK_ITEMS = 16
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def class_codenames(index: int, students: int) -> list[str]:
    """Codenames for one class, disjoint from every other class index.

    Args:
        index: Zero-based class number, 0 through 25.
        students: How many names to build.

    Returns:
        Names such as ``A00`` and ``A01``.
    """
    if index < 0 or index > 25:
        raise ValueError("class index must be 0..25")
    if students < 1:
        raise ValueError("students must be at least 1")
    letter = string.ascii_uppercase[index]
    return [f"{letter}{n:02d}" for n in range(students)]


def presence_is_local(url: str) -> bool:
    """True when a Postgres URL is on this machine, not production.

    Args:
        url: ``--presence-url`` value.

    Returns:
        Whether the host is loopback.
    """
    host = (urlparse(url).hostname or "").lower().strip("[]")
    if "mckenzian.com" in host or host.endswith(".fly.dev"):
        return False
    return host in _LOCAL_HOSTS


def smoke_gate_errors(
    summary: dict[str, Any],
    *,
    max_p95_ms: float,
    min_workers: int,
    min_classes: int,
) -> list[str]:
    """Return deploy-gate failures. An empty list means the smoke passed.

    Ops limits: 0 HTTP/SSE errors, HTTP p95 strictly under ``max_p95_ms``,
    and 0 worker restarts. The sampler must also have seen every gunicorn
    worker, and the run must have opened ``min_classes`` classes.

    Args:
        summary: ``summarize`` output.
        max_p95_ms: HTTP p95 must be strictly below this.
        min_workers: How many gunicorn workers the sampler must have seen.
        min_classes: How many concurrent classes the run must have driven.

    Returns:
        Human-readable failure lines.
    """
    errors: list[str] = []
    kinds = summary.get("kinds") or {}
    http_n = sum(int(v["n"]) for k, v in kinds.items() if not str(k).startswith("sse"))
    if int(summary.get("requests") or 0) < 1 or http_n < 1:
        errors.append("no HTTP requests recorded")
    errors_5xx = int(summary.get("errors_5xx") or 0)
    if errors_5xx != 0:
        errors.append(f"errors_5xx={errors_5xx}, want 0")
    p95 = float(summary.get("http_p95_ms") or 0)
    if p95 >= float(max_p95_ms):
        errors.append(f"http_p95_ms={p95}, want < {max_p95_ms}")
    restarts = int(summary.get("worker_restarts") or 0)
    if restarts != 0:
        errors.append(f"worker_restarts={restarts}, want 0")
    for key in ("log_worker_timeouts", "log_autorestarts"):
        count = int(summary.get(key) or 0)
        if count != 0:
            errors.append(f"{key}={count}, want 0")
    seen = int(summary.get("max_workers") or 0)
    if seen < int(min_workers):
        errors.append(f"max_workers={seen}, want at least {min_workers}")
    classes = int(summary.get("classes") or 0)
    if classes < int(min_classes):
        errors.append(f"classes={classes}, want at least {min_classes}")
    return errors


# --------------------------------------------------------------------------- setup
def seed(
    data: Path,
    students: int,
    presence_url: str | None,
    *,
    classes: int = 1,
    items: int = RANK_ITEMS,
    include_mc: bool = False,
    retain: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], Any]:
    """Build concurrent live classes in one sqlite file.

    Each class has its own teacher, because one staff user can run only one
    live class at a time. The load client drives them together on one
    gunicorn, which is how the one Fly machine serves a school. Each class
    gets its own roster, live session, and rank items.

    Args:
        data: Private data directory (sqlite and uploads).
        students: Roster size of each class.
        presence_url: Postgres URL for live presence. Loopback only.
            ``None`` keeps presence on sqlite.
        classes: How many classes to open at once. At least 1.
        items: Rank questions prepared on each class.
        include_mc: Also prepare one group multiple-choice item, published first.
        retain: Keep the app and test clients open and return ``(fixtures, app)``.

    Returns:
        One fixture dict per class, or ``(fixtures, app)`` when ``retain`` is set.
    """
    if classes < 1:
        raise ValueError("classes must be at least 1")
    if items < 1:
        raise ValueError("items must be at least 1")
    sys.path.insert(0, str(LMS))
    sys.path.insert(0, str(REPO))
    os.environ["ALLOW_DEV_VERIFICATION_CODE"] = "1"
    os.environ["FLASK_SECRET_KEY"] = SECRET
    from app import create_app  # noqa: PLC0415

    app = create_app(
        db_path=data / "lloves.sqlite",
        data_dir=data,
        testing=True,
        live_database_url=presence_url,
    )
    school = app.config["SCHOOL_DB"]
    school.activate_from_semester_json()
    fixtures: list[dict[str, Any]] = []
    for index in range(classes):
        # One live class per teacher. Two concurrent classes are two staff
        # sessions on the same machine, which is what the product allows.
        email = f"teacher{index}@gmail.com"
        teacher = school.register_staff(email)
        offering = school.assign_course(teacher_user_id=int(teacher["id"]), ontario_code="MCF3M")
        staff = app.test_client()
        staff.get("/auth/google?portal=staff")
        staff.get(f"/auth/google/callback?email={email}&name=T{index}")
        code = school.get_user_by_email(email)["verification_code"]
        staff.post("/verify-email", data={"code": code})
        staff_cookie = staff.get_cookie("session").value
        names = class_codenames(index, students)
        class_id = int(
            school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCF3M",
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=names,
                offering_id=int(offering["id"]),
                teacher_user_id=int(teacher["id"]),
            )["id"]
        )
        live = school.start_live_class_session(class_id, int(teacher["id"]))
        sid = int(live["id"])
        staff.post(
            f"/api/live-sessions/{sid}/teacher-state",
            json={"live_module": "M1", "live_slot": "C2", "stage": "round", "page_id": "round_1"},
        )
        school.game.begin_game(class_id)
        rows = school.game.conn.execute(
            "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id", (class_id,)
        ).fetchall()
        ids = {str(r["codename"]): int(r["id"]) for r in rows}
        cookies: dict[str, str] = {}
        rejoins: dict[str, str] = {}
        phones: dict[str, Any] = {}
        for name in names:
            client = app.test_client()
            rv = client.post(
                "/auth/student-code",
                data={"code": str(live["session_code"]), "name": name},
            )
            assert rv.status_code == 302, rv.get_data(as_text=True)[:300]
            client.post("/student/mood", data={"mood": "good"})
            cookies[name] = client.get_cookie("session").value
            rejoin = client.get_cookie("lloves_live_rejoin")
            rejoins[name] = rejoin.value if rejoin is not None else ""
            if retain:
                phones[name] = client
        n_teams = max(2, students // 4)
        order = [ids[n] for n in names]
        school.setup_live_session_groups(
            sid,
            n_teams=n_teams,
            mode="manual",
            present_ids=order,
            assignments=[{"student_id": s, "team_index": i % n_teams} for i, s in enumerate(order)],
        )
        school.ensure_live_session_items(sid)
        prepared: list[dict[str, Any]] = []
        for k in range(items):
            rv = staff.post(
                f"/api/staff/class/{class_id}/live-lessons/M1/C2/add-question",
                json={
                    "page_number": 5,
                    "stage": "round",
                    "type": "rank",
                    "text": f"Rank these representations ({index}-{k}).",
                    "options": ["Two points", "A graph", "An equation", "A table"],
                },
            )
            assert rv.status_code == 200, rv.get_data(as_text=True)[:300]
            item_id = rv.get_json()["placement"]["item"]["id"]
            row = next(r for r in school.list_live_session_items(sid) if r["item_id"] == item_id)
            staff.patch(
                f"/api/live-sessions/{sid}/items/{row['id']}/settings",
                json={"group_rank_mode": "turns"},
            )
            live_item = school.get_live_session_item(sid, int(row["id"]))
            raw = [o["id"] for o in school._rank_option_rows(live_item)]
            aliases = school.student_rank_aliases(live_item)
            opts = [aliases.out(o) for o in raw] if aliases is not None else raw
            prepared.append(
                {"live_item_id": int(row["id"]), "options": opts, "kind": "rank"}
            )
        if include_mc:
            rv = staff.post(
                f"/api/staff/class/{class_id}/live-lessons/M1/C2/add-question",
                json={
                    "page_number": 5,
                    "stage": "round",
                    "type": "mc",
                    "text": f"Which representation ({index})?",
                    "options": ["Graph", "Table", "Equation", "Points"],
                    "correct_index": 0,
                },
            )
            assert rv.status_code == 200, rv.get_data(as_text=True)[:300]
            item_id = rv.get_json()["placement"]["item"]["id"]
            row = next(r for r in school.list_live_session_items(sid) if r["item_id"] == item_id)
            live_item = school.get_live_session_item(sid, int(row["id"]))
            labels = school._mc_option_labels(live_item)
            prepared.insert(
                0,
                {
                    "live_item_id": int(row["id"]),
                    "options": [],
                    "kind": "mc",
                    "choice": labels[0] if labels else "Graph",
                },
            )
        fixtures.append(
            {
                "session_id": sid,
                "class_id": class_id,
                "staff_cookie": staff_cookie,
                "students": [
                    {
                        "name": n,
                        "id": ids[n],
                        "cookie": cookies[n],
                        "rejoin": rejoins.get(n, ""),
                        "team": i % n_teams,
                        **({"client": phones[n]} if retain else {}),
                    }
                    for i, n in enumerate(names)
                ],
                "items": prepared,
                **({"staff_client": staff} if retain else {}),
            }
        )
    if retain:
        return fixtures, app
    school.close()
    return fixtures


# --------------------------------------------------------------------------- server
GUNICORN_HOOKS = '''
import os, threading, time, tracemalloc
OUT = os.environ["LOAD_OUT"]
def post_fork(server, worker):
    if os.environ.get("LOAD_TRACEMALLOC") != "1":
        return
    tracemalloc.start(15)
    def dump():
        base = None
        n = 0
        while True:
            time.sleep(300)
            snap = tracemalloc.take_snapshot()
            path = os.path.join(OUT, f"tracemalloc-{os.getpid()}.txt")
            with open(path, "a") as fh:
                fh.write(f"\\n===== t+{(n+1)*5} min pid {os.getpid()} =====\\n")
                stats = snap.compare_to(base, "traceback") if base else snap.statistics("traceback")
                for st in stats[:15]:
                    fh.write(f"{st}\\n")
                    for line in st.traceback.format()[-8:]:
                        fh.write(f"    {line}\\n")
            if base is None:
                base = snap
            n += 1
    threading.Thread(target=dump, daemon=True).start()
'''


def gunicorn_argv(port: int, hooks: Path, strip_max_requests: bool = False) -> list[str]:
    sys.path.insert(0, str(LMS))
    import serve_capacity  # noqa: PLC0415

    cmd = serve_capacity.image_gunicorn_command().split()
    if strip_max_requests:
        for flag in ("--max-requests", "--max-requests-jitter"):
            while flag in cmd:
                i = cmd.index(flag)
                del cmd[i : i + 2]
    cmd = [c.replace("0.0.0.0:8080", f"127.0.0.1:{port}") for c in cmd]
    # Extra hooks for the sampler only; keep any -c the image command carries.
    return [sys.executable, "-m", *cmd[:1], "-c", str(hooks), *cmd[1:]] if "-c" not in cmd and "--config" not in cmd else [
        sys.executable, "-m", *cmd
    ]


def cpu_max_spec(percent: float) -> str:
    """cgroup v2 ``cpu.max`` for a share of one core.

    6.25 is the shared-CPU baseline: 5 ms every 80 ms, written ``5000 80000``.

    Args:
        percent: Percent of one core, such as 6.25 or 25.

    Returns:
        The ``quota period`` pair, in microseconds.
    """
    period = 80000
    quota = int(round(period * float(percent) / 100.0))
    return f"{max(1000, quota)} {period}"


def wrap_cpu_quota_out(
    argv: list[str], percent: float, cwd: Path, out: Path
) -> tuple[list[str], str]:
    """Run argv inside a cgroup v2 cpu.max, pinned to one core.

    ``systemd-run --user`` has no session bus here, so this writes
    ``cpu.max``, moves the shell into that cgroup, then ``setpriv`` drops
    back to the invoking user before exec. Workers inherit the cgroup.
    The gunicorn pid is recorded at ``out/server.pid``.

    Args:
        argv: gunicorn command.
        percent: Percent of one core.
        cwd: Directory the server should start in.
        out: Run directory that receives ``server.pid``.
    """
    spec = cpu_max_spec(percent)
    path = "/sys/fs/cgroup/mck283load"
    inner = " ".join(shlex.quote(part) for part in argv)
    pid_file = out / "server.pid"
    uid = os.getuid()
    gid = os.getgid()
    # setpriv keeps this pid, so server.pid is the gunicorn master and the
    # sampler can read it as the invoking user. The cgroup stays attached.
    script = (
        f"mkdir -p {path} && echo {shlex.quote(spec)} > {path}/cpu.max && "
        f"cd {shlex.quote(str(cwd))} && echo $$ > {shlex.quote(str(pid_file))} && "
        f"echo $$ > {path}/cgroup.procs && "
        f"exec setpriv --reuid {uid} --regid {gid} --init-groups --inh-caps=-all "
        f"taskset -c 0 {inner}"
    )
    return ["sudo", "-E", "sh", "-c", script], f"cgroup v2 cpu.max {spec} + taskset -c 0"


def pin_cpu_quota(master_pid: int, percent: float) -> str:
    """Move a running gunicorn into ``cpu.max`` and pin it to core 0.

    Boot stays uncapped. Four workers cannot finish ``create_app`` inside a
    hard 6.25% share, and this cgroup has no CPU burst. The cap is on
    before any load client starts.

    Args:
        master_pid: gunicorn master pid.
        percent: Percent of one core, such as 6.25.

    Returns:
        A short label stored on the run summary.
    """
    import psutil  # noqa: PLC0415

    spec = cpu_max_spec(percent)
    path = "/sys/fs/cgroup/mck283load"
    master = psutil.Process(int(master_pid))
    pids = [int(master_pid), *[int(child.pid) for child in master.children()]]
    moves = " && ".join(
        f"echo {pid} > {path}/cgroup.procs && taskset -cp 0 {pid} >/dev/null"
        for pid in pids
    )
    script = f"mkdir -p {path} && echo {shlex.quote(spec)} > {path}/cpu.max && {moves}"
    subprocess.check_call(["sudo", "sh", "-c", script])
    return f"cgroup v2 cpu.max {spec} after boot + taskset -c 0"


# --------------------------------------------------------------------------- clients
class Recorder:
    def __init__(self, path: Path) -> None:
        self.lock = threading.Lock()
        self.fh = path.open("w", newline="")
        self.w = csv.writer(self.fh)
        self.w.writerow(["t", "kind", "status", "ms"])
        self.rows: list[tuple[float, str, int, float]] = []

    def add(self, kind: str, status: int, ms: float) -> None:
        t = time.time()
        with self.lock:
            self.rows.append((t, kind, status, ms))
            self.w.writerow([f"{t:.3f}", kind, status, f"{ms:.1f}"])


_PHONE_LIGHT = frozenset(
    {
        "state_seq",
        "stage",
        "slide",
        "active_media",
        "flags",
        "prompt",
        "response_landed",
        "flag_work",
    }
)


def builds_per_tap(builds: int, taps: int) -> float | None:
    """Full ``/state`` builds divided by successful rank-turn taps.

    Args:
        builds: Responses that were not ``unchanged`` and not shed.
        taps: HTTP 200 rank-turn posts.

    Returns:
        The ratio, or ``None`` when there were no successful taps.
    """
    if int(taps) < 1:
        return None
    return int(builds) / int(taps)


def state_rebuild_budget(team_size: int) -> int:
    """Most full ``/state`` builds one tap may cause.

    The tapping team may each rebuild, and the teacher poll always counts
    as one build. Any other phone is over the cap.

    Args:
        team_size: Students on the tapping team.

    Returns:
        Hard cap, team size plus the teacher.
    """
    return int(team_size) + 1


def tap_over_budget(builds: int, team_size: int) -> bool:
    """True when a tap rebuilt more states than the team plus the teacher.

    Args:
        builds: Full ``/state`` responses counted for that tap.
        team_size: Students on the tapping team.
    """
    return int(builds) > state_rebuild_budget(team_size)


def _phone_can_place(payload: dict[str, Any] | None, item_id: int) -> bool:
    """True when this student's last full payload says it is their turn.

    Args:
        payload: Last full ``/api/student/state`` body.
        item_id: ``live_session_items.id`` currently open.
    """
    if not isinstance(payload, dict):
        return False
    for pool in ("active_questions", "live_items"):
        for row in payload.get(pool) or []:
            if not isinstance(row, dict):
                continue
            if int(row.get("id") or 0) != int(item_id):
                continue
            turns = (row.get("group_submit") or {}).get("turns") or {}
            return bool(turns.get("can_place"))
    return False


def start_phone_threads(
    base: str,
    fx: dict[str, Any],
    stop_at: float,
    rec: Recorder,
    round_minutes: float,
    call: Any,
    mk: Any,
) -> list[threading.Thread]:
    """Drive one class the way a phone does during a group question.

    Each student holds the SSE stream and fetches ``/state`` on a postcard
    (40 ms debounce, one in flight plus one queued), with a 20 s fallback
    while the stream is open and a 4 s poll while it is not. A heartbeat
    goes out every 10 s unless the previous one is still in flight. Each
    team places one rank item every 3–5 s. The tapping phone clears its
    stamp and fetches ``/state`` again 1.2 s later.

    Args:
        base: Loopback origin.
        fx: One seeded class.
        stop_at: ``time.monotonic`` deadline.
        rec: Shared request log.
        round_minutes: Gap between teacher publishes.
        call: ``run_load`` request helper.
        mk: Session factory.

    Returns:
        Threads the caller starts and joins.
    """
    sid = fx["session_id"]
    state: dict[str, Any] = {"item": -1}

    def student_session(st: dict[str, Any]) -> Any:
        """Session cookie plus the rejoin cookie the heartbeat route requires."""
        sess = mk(st["cookie"])
        rejoin = str(st.get("rejoin") or "")
        if rejoin:
            sess.headers["Cookie"] = f"session={st['cookie']}; lloves_live_rejoin={rejoin}"
        return sess

    teams: dict[int, list[dict[str, Any]]] = {}
    for student in fx["students"]:
        teams.setdefault(int(student.get("team") or 0), []).append(student)

    def fetch_state(sess: Any, st: dict[str, Any]) -> None:
        """One ``/state``. Full builds, unchanged, and shed are separate kinds."""
        params: dict[str, Any] = {}
        if st.get("seq") is not None:
            params["seq"] = st["seq"]
        if st.get("stamp"):
            params["stamp"] = st["stamp"]
        t0 = time.perf_counter()
        try:
            rv = sess.get(base + "/api/student/state", params=params, timeout=60)
        except Exception:  # noqa: BLE001
            rec.add("student_state_error", 599, (time.perf_counter() - t0) * 1000)
            return
        ms = (time.perf_counter() - t0) * 1000
        if rv.status_code >= 500:
            rec.add("student_state_error", rv.status_code, ms)
            return
        try:
            body = rv.json()
        except Exception:  # noqa: BLE001
            rec.add("student_state_error", rv.status_code, ms)
            return
        if not isinstance(body, dict):
            rec.add("student_state_error", rv.status_code, ms)
            return
        if body.get("unchanged"):
            rec.add("student_state_unchanged", rv.status_code, ms)
            return
        if body.get("retry") or body.get("error") == "state unavailable":
            rec.add("student_state_shed", rv.status_code, ms)
            return
        rec.add("student_state_build", rv.status_code, ms)
        if body.get("state_seq") is not None:
            st["seq"] = body.get("state_seq")
        if body.get("stamp"):
            st["stamp"] = str(body["stamp"])
        st["payload"] = body

    def phone_stream(st: dict[str, Any]) -> None:
        """Hold SSE and arm a debounced ``/state`` on each light postcard."""
        sess = student_session(st)
        last = None
        while time.monotonic() < stop_at:
            st["stream_up"] = False
            t0 = time.perf_counter()
            headers = {"Last-Event-ID": last} if last else {}
            try:
                with sess.get(
                    base + f"/api/live/session/{sid}/events",
                    stream=True,
                    timeout=(10, 40),
                    headers=headers,
                ) as rv:
                    rec.add(
                        "sse_first_byte",
                        rv.status_code,
                        (time.perf_counter() - t0) * 1000,
                    )
                    if rv.status_code != 200:
                        time.sleep(1)
                        continue
                    st["stream_up"] = True
                    etype = ""
                    data = ""
                    for line in rv.iter_lines(decode_unicode=True):
                        if time.monotonic() >= stop_at:
                            break
                        if line is None:
                            continue
                        if line == "":
                            if etype in _PHONE_LIGHT and not st.get("want"):
                                st["want"] = True
                                st["want_at"] = time.monotonic() + 0.04
                            etype = ""
                            data = ""
                            continue
                        if line.startswith("id:"):
                            last = line[3:].strip()
                        elif line.startswith("event:"):
                            etype = line[6:].strip()
                        elif line.startswith("data:"):
                            data += line[5:].strip()
            except Exception:  # noqa: BLE001
                rec.add("sse_first_byte", 599, (time.perf_counter() - t0) * 1000)
                time.sleep(1)
            st["stream_up"] = False
            time.sleep(1)

    def phone_state(st: dict[str, Any]) -> None:
        """Debounced ``/state``, the safety poll, the post-tap resync, heartbeats."""
        sess = student_session(st)
        next_fallback = time.monotonic() + random.random()
        next_hb = time.monotonic() + random.random() * 2
        hb_out = False
        fetch_state(sess, st)
        while time.monotonic() < stop_at:
            now = time.monotonic()
            do = False
            if st.get("resync_at") and now >= float(st["resync_at"]):
                st["resync_at"] = 0
                st["stamp"] = ""
                do = True
            elif st.get("want") and now >= float(st.get("want_at") or 0):
                st["want"] = False
                do = True
            elif now >= next_fallback:
                next_fallback = now + (20 if st.get("stream_up") else 4)
                do = True
            if do:
                fetch_state(sess, st)
                if st.get("want"):
                    st["want"] = False
                    fetch_state(sess, st)
            if now >= next_hb and not hb_out:
                hb_out = True
                next_hb = now + 10

                def beat(flag: dict[str, bool] = {"on": True}) -> None:
                    """One heartbeat. A slow beat blocks the next one."""
                    del flag
                    try:
                        call(sess, "heartbeat", "POST", "/api/student/heartbeat", json={})
                    finally:
                        nonlocal hb_out
                        hb_out = False

                threading.Thread(target=beat, daemon=True).start()
            time.sleep(0.02)

    def team_tap(members: list[dict[str, Any]], team_index: int) -> None:
        """One tap every 3–5 s for this team, on the open rank or MC item."""
        seen_mc: set[int] = set()
        cursor = 0
        while time.monotonic() < stop_at:
            k = int(state["item"])
            if k < 0 or k >= len(fx["items"]):
                time.sleep(0.2)
                continue
            item = fx["items"][k]
            if item.get("kind") == "mc":
                if team_index in seen_mc:
                    time.sleep(0.4)
                    continue
                member = members[0]
                sess = student_session(member)
                rv = call(
                    sess,
                    "group_pick",
                    "POST",
                    f"/api/student/live-items/{item['live_item_id']}/group-pick",
                    json={"choice": item.get("choice") or "Graph"},
                )
                if rv is not None and rv.status_code == 200:
                    seen_mc.add(team_index)
                    call(
                        sess,
                        "group_submit",
                        "POST",
                        f"/api/student/live-items/{item['live_item_id']}/group-submit",
                        json={
                            "choice": item.get("choice") or "Graph",
                            "why": "It matches the graph.",
                        },
                    )
                else:
                    time.sleep(1)
                continue
            time.sleep(random.uniform(3, 5))
            if time.monotonic() >= stop_at:
                break
            chosen = None
            for member in members:
                if _phone_can_place(member.get("payload"), int(item["live_item_id"])):
                    chosen = member
                    break
            if chosen is None:
                chosen = members[cursor % len(members)]
                cursor += 1
            opt = random.choice(item["options"])
            rv = call(
                student_session(chosen),
                "rank_turn",
                "POST",
                f"/api/student/live-items/{item['live_item_id']}/rank-turn",
                json={"option_id": opt},
            )
            if rv is not None and rv.status_code == 200:
                chosen["resync_at"] = time.monotonic() + 1.2

    def teacher() -> None:
        """Publish the next item, close the previous one, and time the course page."""
        sess = mk(fx["staff_cookie"])
        next_round = time.monotonic()
        page = 1
        while time.monotonic() < stop_at:
            call(sess, "staff_state", "GET", f"/api/live-sessions/{sid}/state")
            ticks = state.setdefault("ticks", 0)
            state["ticks"] = ticks + 1
            if ticks % 15 == 0:
                call(sess, "staff_class_page", "GET", f"/staff/class/{fx['class_id']}")
            if time.monotonic() >= next_round and state["item"] + 1 < len(fx["items"]):
                page += 1
                current = int(state["item"])
                if current >= 0:
                    call(
                        sess,
                        "close_item",
                        "POST",
                        f"/api/live-sessions/{sid}/items/{fx['items'][current]['live_item_id']}/close",
                    )
                call(
                    sess,
                    "teacher_state",
                    "POST",
                    f"/api/live-sessions/{sid}/teacher-state",
                    json={
                        "live_module": "M1",
                        "live_slot": "C2",
                        "stage": "round",
                        "page_id": f"round_{page}",
                    },
                )
                k = current + 1
                call(
                    sess,
                    "publish",
                    "POST",
                    f"/api/live-sessions/{sid}/items/{fx['items'][k]['live_item_id']}/publish",
                    json={"publish_mode": "group_submit"},
                )
                call(sess, "static_js", "GET", "/static/student-portal.js")
                call(sess, "static_svg", "GET", "/static/mood/good.svg")
                state["item"] = k
                next_round = time.monotonic() + round_minutes * 60
            time.sleep(2)

    def static_sampler() -> None:
        """Keep a static-file sample in flight through the round, not only at publish."""
        sess = mk(fx["staff_cookie"])
        while time.monotonic() < stop_at:
            call(sess, "static_js", "GET", "/static/student-portal.js")
            call(sess, "static_svg", "GET", "/static/mood/good.svg")
            time.sleep(5)

    threads = [
        threading.Thread(target=teacher, daemon=True),
        threading.Thread(target=static_sampler, daemon=True),
    ]
    for student in fx["students"]:
        threads.append(threading.Thread(target=phone_stream, args=(student,), daemon=True))
        threads.append(threading.Thread(target=phone_state, args=(student,), daemon=True))
    for index, members in teams.items():
        threads.append(
            threading.Thread(target=team_tap, args=(members, index), daemon=True)
        )
    return threads


def run_load(
    base: str,
    fixtures: list[dict[str, Any]],
    minutes: float,
    round_minutes: float,
    rec: Recorder,
    *,
    phone: bool = False,
) -> None:
    """Drive every class at once against one local gunicorn.

    Args:
        base: Loopback origin, always ``http://127.0.0.1:<port>``.
        fixtures: One seed dict per concurrent class.
        minutes: How long the clients run.
        round_minutes: Gap between teacher publishes.
        rec: Shared request log.
        phone: When true, students follow postcard ``/state`` fetches and
            one tap per team instead of the fixed 4 s poll. The default
            path is the deploy smoke.
    """
    import requests  # noqa: PLC0415

    stop = time.monotonic() + minutes * 60

    def call(sess: Any, kind: str, method: str, url: str, **kw: Any) -> Any:
        t0 = time.perf_counter()
        try:
            rv = sess.request(method, base + url, timeout=60, **kw)
            rec.add(kind, rv.status_code, (time.perf_counter() - t0) * 1000)
            return rv
        except Exception:  # noqa: BLE001
            rec.add(kind, 599, (time.perf_counter() - t0) * 1000)
            time.sleep(1)
            return None

    def mk(cookie: str) -> Any:
        s = requests.Session()
        s.headers["Cookie"] = f"session={cookie}"
        return s

    def drive(fx: dict[str, Any]) -> list[threading.Thread]:
        """Start one class's teacher and students. ``fx`` is bound per call."""
        if phone:
            return start_phone_threads(
                base, fx, stop, rec, round_minutes, call, mk
            )
        sid = fx["session_id"]
        state: dict[str, Any] = {"item": -1}

        def student_poll(st: dict[str, Any]) -> None:
            s = mk(st["cookie"])
            time.sleep(random.random() * 4)
            n = 0
            while time.monotonic() < stop:
                call(s, "student_state", "GET", "/api/student/state")
                if n % 5 == 0:
                    call(s, "board_mine", "GET", "/api/student/board/mine")
                if n % 15 == 0:
                    call(s, "student_home", "GET", "/student/home")
                n += 1
                time.sleep(4)

        def student_sse(st: dict[str, Any]) -> None:
            s = mk(st["cookie"])
            last = None
            while time.monotonic() < stop:
                t0 = time.perf_counter()
                hdr = {"Last-Event-ID": last} if last else {}
                try:
                    with s.get(
                        base + f"/api/live/session/{sid}/events",
                        stream=True,
                        timeout=60,
                        headers=hdr,
                    ) as rv:
                        first = None
                        busy = False
                        for line in rv.iter_lines(decode_unicode=True):
                            if first is None:
                                first = (time.perf_counter() - t0) * 1000
                            if line.startswith("id:"):
                                last = line[3:].strip()
                            if '"busy"' in line:
                                busy = True
                            if time.monotonic() >= stop:
                                break
                        rec.add("sse_busy" if busy else "sse_first_byte", rv.status_code, first or 0.0)
                        if busy:
                            time.sleep(20)
                except Exception:  # noqa: BLE001
                    rec.add("sse_first_byte", 599, (time.perf_counter() - t0) * 1000)
                    time.sleep(1)
                time.sleep(1)

        def student_rank(st: dict[str, Any]) -> None:
            s = mk(st["cookie"])
            seen = -1
            while time.monotonic() < stop:
                k = state["item"]
                if k >= 0 and k != seen:
                    item = fx["items"][k]
                    for _ in range(12):
                        if time.monotonic() >= stop:
                            break
                        opt = random.choice(item["options"])
                        rv = call(
                            s,
                            "rank_turn",
                            "POST",
                            f"/api/student/live-items/{item['live_item_id']}/rank-turn",
                            json={"option_id": opt},
                        )
                        time.sleep(random.uniform(2, 6))
                        if rv is not None and rv.status_code == 200:
                            body = rv.json().get("group_submit") or {}
                            if (body.get("turns") or {}).get("done"):
                                break
                    seen = k
                time.sleep(1)

        def teacher() -> None:
            s = mk(fx["staff_cookie"])
            next_round = time.monotonic()
            page = 1
            while time.monotonic() < stop:
                call(s, "staff_state", "GET", f"/api/live-sessions/{sid}/state")
                ticks = state.setdefault("ticks", 0)
                state["ticks"] = ticks + 1
                if ticks % 15 == 0:
                    call(s, "staff_class_page", "GET", f"/staff/class/{fx['class_id']}")
                if time.monotonic() >= next_round and state["item"] + 1 < len(fx["items"]):
                    page += 1
                    call(
                        s,
                        "teacher_state",
                        "POST",
                        f"/api/live-sessions/{sid}/teacher-state",
                        json={
                            "live_module": "M1",
                            "live_slot": "C2",
                            "stage": "round",
                            "page_id": f"round_{page}",
                        },
                    )
                    k = state["item"] + 1
                    call(
                        s,
                        "publish",
                        "POST",
                        f"/api/live-sessions/{sid}/items/{fx['items'][k]['live_item_id']}/publish",
                        json={"publish_mode": "group_submit"},
                    )
                    state["item"] = k
                    next_round = time.monotonic() + round_minutes * 60
                if ticks % 30 == 15:
                    # Mid-class deck edit (teacher adds a question), about once a
                    # minute. Bumps class_deck_revision, which every worker sees.
                    edits = state.setdefault("edits", 0)
                    state["edits"] = edits + 1
                    call(
                        s,
                        "add_question",
                        "POST",
                        f"/api/staff/class/{fx['class_id']}/live-lessons/M1/C2/add-question",
                        json={
                            "page_number": 6,
                            "stage": "round",
                            "type": "rank",
                            "text": f"Extra rank {edits}",
                            "options": ["w", "x", "y", "z"],
                        },
                    )
                time.sleep(2)

        threads = [threading.Thread(target=teacher, daemon=True)]
        for st in fx["students"]:
            for fn in (student_poll, student_sse, student_rank):
                threads.append(threading.Thread(target=fn, args=(st,), daemon=True))
        return threads

    threads: list[threading.Thread] = []
    for fx in fixtures:
        threads.extend(drive(fx))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=max(1.0, stop - time.monotonic() + 90))


# --------------------------------------------------------------------------- sampler
def sampler(
    master_pid: int,
    out: Path,
    stop_evt: threading.Event,
    info: dict[str, Any],
    expected_workers: int,
) -> None:
    """Sample RSS and count a worker pid that appears after the full set is up.

    A worker that forks during boot is not a restart. Once ``expected_workers``
    children have been seen together, a new pid is a replacement.

    Args:
        master_pid: gunicorn master.
        out: Directory for ``samples.csv``.
        stop_evt: Set when the load clients finish.
        info: Mutated with ``restarts``, ``max_total_mb``, and ``max_workers``.
        expected_workers: gunicorn ``--workers`` count.
    """
    import psutil  # noqa: PLC0415

    master = psutil.Process(master_pid)
    seen: set[int] = set()
    armed = False
    with (out / "samples.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "pid", "rss_mb", "total_mb"])
        while not stop_evt.is_set():
            try:
                kids = master.children()
                procs = [master, *kids]
                rss = {p.pid: p.memory_info().rss / 1e6 for p in procs}
            except psutil.Error:
                break
            total = sum(rss.values())
            now = time.time()
            for pid, mb in rss.items():
                w.writerow([f"{now:.0f}", pid, f"{mb:.1f}", f"{total:.1f}"])
            fh.flush()
            pids = {p.pid for p in kids}
            info["max_workers"] = max(int(info.get("max_workers", 0)), len(pids))
            if not armed:
                if len(pids) >= expected_workers:
                    seen = set(pids)
                    armed = True
            else:
                info["restarts"] += len(pids - seen)
                seen |= pids
            info["max_total_mb"] = max(info["max_total_mb"], total)
            stop_evt.wait(15)


# --------------------------------------------------------------------------- summary
def pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1))))]


def summarize(
    out: Path,
    rec: Recorder,
    info: dict[str, Any],
    minutes: float,
    server_log: Path,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    by: dict[str, list[tuple[int, float]]] = {}
    for _, kind, status, ms in rec.rows:
        by.setdefault(kind, []).append((status, ms))
    kinds = {}
    for kind, vals in sorted(by.items()):
        ms = [m for s, m in vals if s < 500]
        kinds[kind] = {
            "n": len(vals),
            "p50_ms": round(pct(ms, 50), 1),
            "p95_ms": round(pct(ms, 95), 1),
            "max_ms": round(max(ms) if ms else 0, 1),
            "errors_5xx": sum(1 for s, _ in vals if s >= 500),
            "status": {str(s): sum(1 for x, _ in vals if x == s) for s in sorted({x for x, _ in vals})},
        }
    http = [m for _, k, s, m in rec.rows if not k.startswith("sse") and s < 500]
    # RSS: per-pid first/last 10 min medians.
    rows = list(csv.DictReader((out / "samples.csv").open()))
    per: dict[str, list[tuple[float, float]]] = {}
    for r in rows:
        per.setdefault(r["pid"], []).append((float(r["t"]), float(r["rss_mb"])))
    growth = {}
    for pid, pts in per.items():
        if len(pts) < 8:
            continue
        t0 = pts[0][0]
        early = [m for t, m in pts if 300 <= t - t0 <= 900] or [pts[0][1]]
        late = [m for t, m in pts if t - t0 >= (pts[-1][0] - t0) - 600]
        growth[pid] = {
            "start_mb": round(pts[0][1], 1),
            "early_median_mb": round(statistics.median(early), 1),
            "late_median_mb": round(statistics.median(late), 1),
            "max_mb": round(max(m for _, m in pts), 1),
            "growth_mb_per_hour": round(
                (statistics.median(late) - statistics.median(early))
                / max(1e-6, ((pts[-1][0] - t0) - 600) / 3600 - 0.1)
                if len(pts) > 40 else 0.0,
                1,
            ),
        }
    log = server_log.read_text(errors="replace") if server_log.exists() else ""
    summary = {
        "minutes": minutes,
        "requests": len(rec.rows),
        "http_p50_ms": round(pct(http, 50), 1),
        "http_p95_ms": round(pct(http, 95), 1),
        "errors_5xx": sum(1 for _, _, s, _ in rec.rows if s >= 500),
        "worker_restarts": info["restarts"],
        "log_worker_timeouts": log.count("WORKER TIMEOUT"),
        "log_autorestarts": log.count("Autorestarting worker"),
        "log_oom_or_sigkill": log.count("SIGKILL") + log.count("Out of memory"),
        "max_total_rss_mb": round(info["max_total_mb"], 1),
        "over_1gb": info["max_total_mb"] > 1024,
        "max_workers": int(info.get("max_workers", 0)),
        "kinds": kinds,
        "rss": growth,
    }
    if "student_state_build" in kinds or "static_js" in kinds:
        builds = int(kinds.get("student_state_build", {}).get("n") or 0)
        taps = int((kinds.get("rank_turn", {}).get("status") or {}).get("200") or 0)
        ratio = builds_per_tap(builds, taps)
        static_ms = [
            m
            for _, kind, status, m in rec.rows
            if kind in {"static_js", "static_svg"} and status < 500
        ]
        summary["state_builds"] = builds
        summary["rank_taps_200"] = taps
        summary["builds_per_tap"] = None if ratio is None else round(ratio, 2)
        summary["state_unchanged"] = int(kinds.get("student_state_unchanged", {}).get("n") or 0)
        summary["state_shed"] = int(kinds.get("student_state_shed", {}).get("n") or 0)
        summary["static_p95_ms"] = round(pct(static_ms, 95), 1)
        summary["staff_class_p95_ms"] = (kinds.get("staff_class_page") or {}).get("p95_ms", 0)
        summary["group_pick_200"] = int((kinds.get("group_pick", {}).get("status") or {}).get("200") or 0)
    if meta:
        summary.update(meta)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    md = [f"# load_class.py — {minutes:g} min", "", "| metric | value |", "|---|---|"]
    for key in (
        "classes",
        "students_per_class",
        "students_total",
        "requests",
        "http_p50_ms",
        "http_p95_ms",
        "errors_5xx",
        "worker_restarts",
        "log_worker_timeouts",
        "log_autorestarts",
        "max_workers",
        "max_total_rss_mb",
        "over_1gb",
        "state_builds",
        "rank_taps_200",
        "builds_per_tap",
        "state_unchanged",
        "state_shed",
        "static_p95_ms",
        "staff_class_p95_ms",
        "group_pick_200",
        "cpu_quota",
        "code_root",
    ):
        if key in summary:
            md.append(f"| {key} | {summary[key]} |")
    md += ["", "| kind | n | p50 ms | p95 ms | max ms | 5xx |", "|---|---|---|---|---|---|"]
    for k, v in kinds.items():
        md.append(f"| {k} | {v['n']} | {v['p50_ms']} | {v['p95_ms']} | {v['max_ms']} | {v['errors_5xx']} |")
    md += ["", "| pid | start MB | 5–15 min MB | last 10 min MB | max MB | MB/h |", "|---|---|---|---|---|---|"]
    for pid, g in growth.items():
        md.append(f"| {pid} | {g['start_mb']} | {g['early_median_mb']} | {g['late_median_mb']} | {g['max_mb']} | {g['growth_mb_per_hour']} |")
    (out / "summary.md").write_text("\n".join(md) + "\n")
    return summary


def _budget_warmup(fx: dict[str, Any]) -> None:
    """Store each phone's seq and stamp after a full ``/state``.

    Args:
        fx: One retained class fixture.
    """
    for student in fx["students"]:
        rv = student["client"].get("/api/student/state")
        body = rv.get_json(silent=True) or {}
        student["seq"] = body.get("state_seq")
        student["stamp"] = body.get("stamp")
        student["payload"] = body


def _budget_rebuilds(fx: dict[str, Any]) -> int:
    """Count student polls that rebuilt, plus the teacher poll.

    Args:
        fx: One retained class fixture with seq and stamp stored.
    """
    builds = 0
    for student in fx["students"]:
        rv = student["client"].get(
            "/api/student/state",
            query_string={"seq": student.get("seq"), "stamp": student.get("stamp")},
        )
        body = rv.get_json(silent=True) or {}
        if not body.get("unchanged"):
            builds += 1
    fx["staff_client"].get(f"/api/live-sessions/{fx['session_id']}/state")
    return builds + 1


def _budget_show_prepared_page(school: Any, fx: dict[str, Any]) -> None:
    """Open the teacher on the page the prepared questions already use.

    Students build a take-turns card only for the page on screen. The
    seed adds questions on a later page than the opening rail page.
    Rewriting those rows would make the next tap stamp every placement
    and rebuild every phone, so the rail moves instead.

    Args:
        school: Open school database.
        fx: One retained class fixture.
    """
    session_id = int(fx["session_id"])
    wanted: int | None = None
    for item in fx["items"]:
        row = school.get_live_session_item(
            session_id, int(item["live_item_id"]), sync="skip"
        )
        raw = row.get("page_number")
        if raw in (None, ""):
            continue
        try:
            wanted = int(raw)
        except (TypeError, ValueError):
            continue
        break
    if wanted is None or school._current_student_page_number(session_id) == wanted:
        return
    meta = school.live_class_metadata_for_session(session_id)
    teacher = school.live_session_teacher_state_payload(session_id)
    match: dict[str, Any] | None = None
    for page in meta.get("pages") or []:
        if not isinstance(page, dict) or not str(page.get("id") or "").strip():
            continue
        raw = page.get("page_number")
        try:
            number = int(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            number = None
        if number == wanted:
            match = page
            break
    if match is None:
        return
    fx["staff_client"].post(
        f"/api/live-sessions/{session_id}/teacher-state",
        json={
            "live_module": teacher.get("live_module"),
            "live_slot": teacher.get("live_slot"),
            "stage": str(match.get("stage") or teacher.get("stage") or ""),
            "page_id": str(match["id"]),
        },
    )


def _budget_publish(fx: dict[str, Any], item: dict[str, Any]) -> int:
    """Publish one prepared item to the group. Returns the HTTP status.

    Args:
        fx: One retained class fixture.
        item: Prepared item from ``seed``.
    """
    rv = fx["staff_client"].post(
        f"/api/live-sessions/{fx['session_id']}/items/{item['live_item_id']}/publish",
        json={"publish_mode": "group_submit"},
    )
    return int(rv.status_code)


def _budget_close(fx: dict[str, Any], item: dict[str, Any]) -> None:
    """Close one item so the next tap is the only open question.

    Args:
        fx: One retained class fixture.
        item: Prepared item from ``seed``.
    """
    fx["staff_client"].post(
        f"/api/live-sessions/{fx['session_id']}/items/{item['live_item_id']}/close"
    )


def run_sqlite_budget(students: int, out: Path) -> int:
    """Group multiple-choice pick and one rank tap on sqlite.

    No Postgres and no gunicorn. After a warmup, each tap may rebuild at
    most that team plus the teacher. A tap that does not land also fails.

    Args:
        students: Roster size.
        out: Directory for the one-line summary.

    Returns:
        0 when both taps stay inside the cap, 1 otherwise.
    """
    data = Path(tempfile.mkdtemp(prefix="load-budget-"))
    out.mkdir(parents=True, exist_ok=True)
    fixtures, app = seed(
        data,
        students,
        None,
        classes=1,
        items=1,
        include_mc=True,
        retain=True,
    )
    errors: list[str] = []
    summary: dict[str, Any] = {"students": int(students)}
    try:
        fx = fixtures[0]
        _budget_show_prepared_page(app.config["SCHOOL_DB"], fx)
        mc = next(item for item in fx["items"] if item.get("kind") == "mc")
        rank = next(item for item in fx["items"] if item.get("kind") == "rank")
        if _budget_publish(fx, mc) != 200:
            errors.append("group question did not publish")
        else:
            _budget_warmup(fx)
            team = int(fx["students"][0]["team"])
            members = [row for row in fx["students"] if int(row["team"]) == team]
            rv = members[0]["client"].post(
                f"/api/student/live-items/{mc['live_item_id']}/group-pick",
                json={"choice": mc.get("choice") or "Graph"},
            )
            if rv.status_code != 200:
                errors.append(f"group pick status {rv.status_code}")
            else:
                builds = _budget_rebuilds(fx)
                summary["group_pick_builds"] = builds
                summary["group_pick_budget"] = state_rebuild_budget(len(members))
                if tap_over_budget(builds, len(members)):
                    errors.append(
                        f"group pick rebuilt {builds}, budget {summary['group_pick_budget']}"
                    )
        _budget_close(fx, mc)
        if _budget_publish(fx, rank) != 200:
            errors.append("rank question did not publish")
        else:
            _budget_warmup(fx)
            actor = None
            for row in fx["students"]:
                if _phone_can_place(row.get("payload"), int(rank["live_item_id"])):
                    actor = row
                    break
            if actor is None:
                errors.append("rank tap found no turn")
            else:
                team = int(actor["team"])
                members = [row for row in fx["students"] if int(row["team"]) == team]
                rv = actor["client"].post(
                    f"/api/student/live-items/{rank['live_item_id']}/rank-turn",
                    json={"option_id": rank["options"][0]},
                )
                if rv.status_code != 200:
                    errors.append(f"rank tap status {rv.status_code}")
                else:
                    builds = _budget_rebuilds(fx)
                    summary["rank_builds"] = builds
                    summary["rank_budget"] = state_rebuild_budget(len(members))
                    if tap_over_budget(builds, len(members)):
                        errors.append(
                            f"rank tap rebuilt {builds}, budget {summary['rank_budget']}"
                        )
        summary["ok"] = not errors
        summary["errors"] = errors
        (out / "summary.json").write_text(json.dumps(summary, indent=2))
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        print(
            f"sqlite budget ok pick={summary.get('group_pick_builds')}"
            f"/{summary.get('group_pick_budget')}"
            f" rank={summary.get('rank_builds')}/{summary.get('rank_budget')}"
        )
        return 0
    finally:
        app.config["SCHOOL_DB"].close()


def main() -> int:
    """Seed local classes, run gunicorn, and optionally fail the deploy gate.

    The HTTP target is always ``127.0.0.1``. A non-loopback presence URL is
    refused so this cannot be pointed at production.

    Returns:
        0 when the run finished and, with ``--gate``, the limits passed.
        1 when ``--gate`` limits failed. 2 when setup or boot failed.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--students", type=int, default=30)
    ap.add_argument("--classes", type=int, default=1, help="Concurrent classes on one gunicorn.")
    ap.add_argument("--items", type=int, default=RANK_ITEMS, help="Rank questions seeded per class.")
    ap.add_argument("--round-minutes", type=float, default=4)
    ap.add_argument("--port", type=int, default=18791)
    ap.add_argument("--out", type=Path, default=REPO / "lc-qa" / "load-class" / time.strftime("%Y%m%d-%H%M"))
    ap.add_argument("--presence-url", default=os.environ.get("LIVE_PRESENCE_TEST_URL", DEFAULT_PRESENCE))
    ap.add_argument("--tracemalloc", action="store_true")
    ap.add_argument("--strip-max-requests", action="store_true",
                    help="Drop --max-requests from the image command (baseline leak runs).")
    ap.add_argument("--gate", action="store_true",
                    help="Exit 1 unless errors, p95, and worker restarts are within limits.")
    ap.add_argument("--max-p95-ms", type=float, default=250.0)
    ap.add_argument("--min-classes", type=int, default=1)
    ap.add_argument(
        "--phone",
        action="store_true",
        help="Postcard /state fetches and one team tap every few seconds.",
    )
    ap.add_argument(
        "--sqlite-budget",
        action="store_true",
        help="In-process sqlite group pick and rank tap. Fail above team size plus the teacher.",
    )
    ap.add_argument(
        "--cpu-quota",
        type=float,
        default=0,
        help="Percent of one core for gunicorn (cgroup cpu.max). 0 is uncapped.",
    )
    ap.add_argument(
        "--code-root",
        type=Path,
        default=None,
        help="Checkout gunicorn imports. Default is this script's repo.",
    )
    args = ap.parse_args()
    if args.sqlite_budget:
        return run_sqlite_budget(args.students, args.out.resolve())
    if not presence_is_local(args.presence_url):
        print("refusing non-local presence URL; this smoke never targets production", file=sys.stderr)
        return 2
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    # Fresh presence schema per run so attendees from old runs are not counted.
    # Loopback check first, then CREATE DATABASE; the db is dropped on exit.
    dbname = f"load_{int(time.time())}_{os.getpid()}"
    presence = args.presence_url.rsplit("/", 1)[0] + "/" + dbname
    if not presence_is_local(presence):
        print("refusing non-local presence database", file=sys.stderr)
        return 2
    import psycopg  # noqa: PLC0415
    with psycopg.connect(args.presence_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    try:
        return _run(args, out, presence)
    finally:
        drop_presence_db(args.presence_url, dbname)


def drop_presence_db(admin_url: str, dbname: str) -> None:
    """Drop the per-run presence database. Never raises."""
    if not dbname.startswith("load_") or not presence_is_local(admin_url):
        return
    try:
        import psycopg  # noqa: PLC0415
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    except Exception as exc:  # noqa: BLE001
        print(f"could not drop presence db {dbname}: {exc}", file=sys.stderr)


def _run(args: argparse.Namespace, out: Path, presence: str) -> int:
    """Seed, boot gunicorn, drive load, and apply the gate. See ``main``."""
    data = Path(tempfile.mkdtemp(prefix="load-class-"))
    code_root = Path(args.code_root).resolve() if args.code_root else REPO
    if not (code_root / "lms" / "app.py").is_file():
        print("code root has no lms/app.py:", code_root, file=sys.stderr)
        return 2
    phone = bool(args.phone)
    fixtures = seed(
        data,
        args.students,
        presence,
        classes=args.classes,
        items=args.items,
        include_mc=phone,
    )
    public = [
        {k: v for k, v in fx.items() if k not in ("students", "staff_cookie")}
        for fx in fixtures
    ]
    (out / "fixture.json").write_text(json.dumps(public, indent=2))
    hooks = out / "gunicorn_hooks.py"
    hooks.write_text(GUNICORN_HOOKS)
    env = {
        **os.environ,
        "FLASK_ENV": "production",
        "FLASK_SECRET_KEY": SECRET,
        "LLOVES_DB": str(data / "lloves.sqlite"),
        "LLOVES_DATA_DIR": str(data),
        "LIVE_DATABASE_URL": presence,
        "LOAD_OUT": str(out),
        "LOAD_TRACEMALLOC": "1" if args.tracemalloc else "0",
        "SENTRY_DSN": "",
        "PYTHONPATH": str(code_root),
        "PYTHONUNBUFFERED": "1",
    }
    env.pop("LOCAL_DEV_LOGIN", None)
    server_log = out / "server.log"
    argv = gunicorn_argv(args.port, hooks, args.strip_max_requests)
    quota = float(args.cpu_quota or 0)
    quota_label = ""
    (out / "command.txt").write_text(" ".join(argv) + "\n")
    proc = subprocess.Popen(
        argv, cwd=code_root, env=env, stdout=server_log.open("w"), stderr=subprocess.STDOUT
    )
    import requests  # noqa: PLC0415
    import serve_capacity  # noqa: PLC0415

    base = f"http://127.0.0.1:{args.port}"
    if not base.startswith("http://127.0.0.1:"):
        print("refusing non-local HTTP target", file=sys.stderr)
        proc.terminate()
        return 2
    ready = False
    for _ in range(120):
        if proc.poll() is not None:
            break
        try:
            if requests.get(base + "/health", timeout=2).status_code == 200:
                ready = True
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    if not ready:
        if proc.poll() is None:
            proc.terminate()
        print("server did not come up; see", server_log)
        return 2
    time.sleep(10)  # let every worker finish create_app()
    master_pid = proc.pid
    if quota > 0:
        try:
            quota_label = pin_cpu_quota(master_pid, quota)
        except (OSError, subprocess.CalledProcessError) as exc:
            print("could not apply cpu quota:", exc, file=sys.stderr)
            proc.terminate()
            return 2
        (out / "command.txt").write_text(
            (out / "command.txt").read_text() + quota_label + "\n"
        )
    info = {"restarts": 0, "max_total_mb": 0.0, "max_workers": 0}
    stop_evt = threading.Event()
    smp = threading.Thread(
        target=sampler,
        args=(master_pid, out, stop_evt, info, serve_capacity.WORKERS),
        daemon=True,
    )
    smp.start()
    rec = Recorder(out / "requests.csv")
    try:
        run_load(
            base,
            fixtures,
            args.minutes,
            args.round_minutes,
            rec,
            phone=phone,
        )
    finally:
        stop_evt.set()
        smp.join(timeout=20)
        rec.fh.close()
        try:
            os.kill(master_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=40)
        except subprocess.TimeoutExpired:
            proc.kill()
    summary = summarize(
        out,
        rec,
        info,
        args.minutes,
        server_log,
        meta={
            "classes": args.classes,
            "students_per_class": args.students,
            "students_total": args.classes * args.students,
            "target": base,
            "phone": phone,
            "cpu_quota": quota_label or "uncapped",
            "code_root": code_root.name,
        },
    )
    print(json.dumps({k: v for k, v in summary.items() if k not in ("kinds", "rss")}, indent=2))
    print("results:", out)
    if not args.gate:
        return 0
    failures = smoke_gate_errors(
        summary,
        max_p95_ms=args.max_p95_ms,
        min_workers=serve_capacity.WORKERS,
        min_classes=args.min_classes,
    )
    if failures:
        for line in failures:
            print("gate:", line, file=sys.stderr)
        return 1
    print("gate: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
