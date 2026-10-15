#!/usr/bin/env python3
"""Headless screenshots of the local LMS.

Logs in once as the seeded teacher Shawn, saves Playwright storageState,
and reuses that file for every URL. Widths default to a phone (390) and a
desktop (1280). Any host that is not loopback is refused, so this never
opens the live alc site.

Examples:
    LOCAL_DEV_LOGIN=1 python3 scripts/shoot.py --seed \\
        --url http://127.0.0.1:8787/staff/class/1?tab=lesson-slides \\
        --widths 390,1280 --out /opt/cursor/artifacts

    python3 scripts/shoot.py --scenario catchup \\
        --base http://127.0.0.1:8787 --out /opt/cursor/artifacts
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LMS_DIR = ROOT / "lms"
DEFAULT_DB = LMS_DIR / "data" / "lloves.sqlite"
DEFAULT_BASE = "http://127.0.0.1:8787"
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
SHAWN_EMAIL = "shawnmckenzie11.sm@gmail.com"
CHROME = os.environ.get("CHROME_PATH", "/usr/local/bin/google-chrome")


def assert_loopback(url: str) -> str:
    """Return ``url`` when its host is loopback.

    Args:
        url: Absolute http(s) URL.

    Raises:
        SystemExit: The host is missing or not loopback.
    """
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host not in LOOPBACK:
        raise SystemExit(f"Refusing non-local URL: {url}")
    return url


def db_path() -> Path:
    """Sqlite file the local LMS is using."""
    return Path(os.environ.get("LLOVES_DB") or DEFAULT_DB)


def seed_demo() -> None:
    """Run the idempotent local-dev seed against the local sqlite.

    Requires ``LOCAL_DEV_LOGIN``. Does not talk to Fly or alc.
    """
    env = os.environ.copy()
    env["LOCAL_DEV_LOGIN"] = "1"
    script = LMS_DIR / "local_dev_seed.py"
    subprocess.run([sys.executable, str(script)], cwd=str(ROOT), env=env, check=True)


def staff_shawn_email(path: Path) -> str:
    """Email of the staff user whose display name is Shawn.

    The IT picker account is also named Shawn. This returns the staff row.

    Args:
        path: LMS sqlite path.

    Raises:
        SystemExit: Shawn the teacher is not in the database.
    """
    con = sqlite3.connect(path)
    try:
        row = con.execute(
            """
            SELECT email FROM users
            WHERE role = 'staff' AND display_name = 'Shawn'
            ORDER BY CASE email WHEN ? THEN 0 ELSE 1 END, id
            LIMIT 1
            """,
            (SHAWN_EMAIL,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise SystemExit("No staff user named Shawn. Run with --seed first.")
    return str(row[0])


def shawn_class_id(path: Path) -> int:
    """Class id owned by staff Shawn.

    Args:
        path: LMS sqlite path.

    Raises:
        SystemExit: Shawn has no class.
    """
    con = sqlite3.connect(path)
    try:
        row = con.execute(
            """
            SELECT c.id FROM classes c
            JOIN users u ON u.id = c.teacher_user_id
            WHERE u.email = ?
            ORDER BY c.id
            LIMIT 1
            """,
            (staff_shawn_email(path),),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise SystemExit("Shawn has no class. Run with --seed first.")
    return int(row[0])


def _ensure_demo_deck(con: sqlite3.Connection, class_id: int) -> None:
    """Insert one Module 1 Class C1 deck so the card sits under a deck list."""
    found = con.execute(
        """
        SELECT 1 FROM lesson_slide_decks
        WHERE class_id = ? AND module_number = 1 AND live_index = 1
        """,
        (class_id,),
    ).fetchone()
    if found is not None:
        return
    stamp = "2026-10-01T12:00:00"
    con.execute(
        """
        INSERT INTO lesson_slide_decks (
            class_id, module_number, live_index, presentation_id,
            presentation_url, created_at, updated_at
        ) VALUES (?, 1, 1, 'local-demo-m1c1', ?, ?, ?)
        """,
        (class_id, "/staff/class/%s?tab=lesson-slides" % class_id, stamp, stamp),
    )


def prepare_catchup(path: Path, mode: str) -> int:
    """Reset Shawn's catch-up links for an empty or mixed screenshot.

    Args:
        path: LMS sqlite path.
        mode: ``empty`` (grey C1–C4) or ``mixed`` (open, closed, earlier runs).

    Returns:
        Class id that was updated.

    Raises:
        SystemExit: ``mode`` is not empty or mixed.
    """
    if mode not in {"empty", "mixed"}:
        raise SystemExit(f"Unknown catch-up prepare mode: {mode}")
    class_id = shawn_class_id(path)
    con = sqlite3.connect(path)
    try:
        _ensure_demo_deck(con, class_id)
        con.execute("DELETE FROM catchup_links WHERE class_id = ?", (class_id,))
        con.execute("DELETE FROM lesson_slides_latest WHERE class_id = ?", (class_id,))
        if mode == "mixed":
            rows = [
                ("cu-new", "session:3:2026-10-13T09:00:00", "M1", "C1", 1, "2026-10-13T09:00:00", "AB12"),
                ("cu-c2", "session:4:2026-10-02T09:00:00", "M1", "C2", 0, "2026-10-02T09:00:00", "CD34"),
            ]
            sample_questions = json.dumps(
                [
                    {
                        "question_key": "item:q0",
                        "page": 1,
                        "stem": "Which expression is equivalent to $(x+2)^2$?",
                        "type": "mc",
                        "type_label": "Multiple choice",
                        "mode": "individual",
                        "weight": 1,
                        "in_catchup": True,
                    },
                    {
                        "question_key": "item:q1",
                        "page": 2,
                        "stem": "Order the team steps.",
                        "type": "rank",
                        "type_label": "Rank",
                        "mode": "group",
                        "weight": 0,
                        "in_catchup": False,
                    },
                ]
            )
            for token, run_key, module, slot, is_open, created, join_code in rows:
                con.execute(
                    """
                    INSERT INTO catchup_links (
                        token, class_id, run_key, live_module, live_slot, is_open,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (token, class_id, run_key, module, slot, is_open, created, created),
                )
                con.execute(
                    """
                    INSERT OR REPLACE INTO participation_runs (
                        class_id, run_key, is_real, auto_is_real, discarded
                    ) VALUES (?, ?, 1, 1, 0)
                    """,
                    (class_id, run_key),
                )
                con.execute(
                    """
                    INSERT OR REPLACE INTO lesson_slides_latest (
                        class_id, live_module, live_slot, run_key, session_code, ended_at,
                        presentation_url, questions_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        class_id,
                        module,
                        slot,
                        run_key,
                        join_code,
                        created,
                        f"/staff/class/{class_id}?tab=lesson-slides",
                        sample_questions,
                        created,
                    ),
                )
        con.commit()
    finally:
        con.close()
    return class_id


def fresh_state_path() -> Path:
    """Per-run Playwright storageState file (not a fixed shared /tmp path)."""
    fd, name = tempfile.mkstemp(prefix="lloves-shawn-state-", suffix=".json")
    os.close(fd)
    return Path(name)


def slug_for(url: str) -> str:
    """Filesystem slug for a screenshot URL."""
    parsed = urllib.parse.urlparse(url)
    raw = (parsed.path or "/").strip("/").replace("/", "-") or "home"
    if parsed.query:
        raw = f"{raw}-{parsed.query}"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw)
    return safe[:80]


def login_state(base: str, state_path: Path, *, teacher_email: str) -> None:
    """Sign in once as Shawn and write Playwright storageState.

    Args:
        base: Loopback origin, no path.
        state_path: Where to write the storageState JSON.
        teacher_email: Staff Shawn's email.
    """
    from playwright.sync_api import sync_playwright

    origin = assert_loopback(base if "://" in base else f"http://{base}")
    callback = assert_loopback(
        origin.rstrip("/")
        + "/auth/google/callback?"
        + urllib.parse.urlencode(
            {"portal": "staff", "email": teacher_email, "name": "Shawn"}
        )
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.goto(callback, wait_until="domcontentloaded")
        page.wait_for_url("**/staff**", timeout=15000)
        context.storage_state(path=str(state_path))
        browser.close()


def _launch(playwright: object):
    """Start headless Chrome already installed on the machine.

    Args:
        playwright: ``sync_playwright()`` handle.
    """
    chromium = playwright.chromium  # type: ignore[attr-defined]
    launch_args = {
        "headless": True,
        "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    }
    chrome = Path(CHROME)
    if chrome.is_file():
        launch_args["executable_path"] = str(chrome)
    return chromium.launch(**launch_args)


def shoot_mck219(
    origin: str,
    state_path: Path,
    out_dir: Path,
    widths: list[int],
) -> list[Path]:
    """MCK-219: closed group rank on the teacher card with Score teams (no reveal).

    Runs ``shoot_mck219_prep.py``, lands on the lesson page, and screenshots the
    closed card (no projector reveal or podium).
    """
    import json
    import subprocess

    from playwright.sync_api import sync_playwright

    env = os.environ.copy()
    env["LOCAL_DEV_LOGIN"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(LMS_DIR)])
    env["SHOOT_BASE"] = origin if "://" in origin else f"http://{origin}"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "shoot_mck219_prep.py")],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stderr or proc.stdout or "shoot_mck219_prep failed")
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        raise SystemExit(f"shoot_mck219_prep output missing metadata: {proc.stdout!r}")
    meta = json.loads(lines[1])
    item_id = int(meta["live_item_id"])
    url = str(meta.get("url") or lines[0])
    evidence = str(meta.get("evidence_text") or "")
    card_sel = f'.live-question-card.is-closed[data-live-item-id="{item_id}"]'
    score_sel = f'{card_sel} [data-rank-score-open="{item_id}"]'
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        for width in widths:
            context = browser.new_context(
                storage_state=str(state_path),
                viewport={"width": int(width), "height": 900},
            )
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=60000)
            for _ in range(12):
                if page.locator(score_sel).count() > 0:
                    break
                if evidence and page.get_by_text(evidence).count() > 0:
                    break
                next_btn = page.locator("#live-stage-next")
                if next_btn.count() == 0 or next_btn.is_disabled():
                    break
                next_btn.click()
                page.wait_for_timeout(400)
            page.locator(score_sel).first.wait_for(state="visible", timeout=45000)
            if page.locator(f"{card_sel} .race-results").count() > 0:
                raise SystemExit("Teacher reveal UI still visible on closed rank card")
            card = page.locator(card_sel).first
            card.scroll_into_view_if_needed()
            dest = out_dir / f"mck219-teacher-score-teams-{width}.png"
            card.screenshot(path=str(dest))
            written.append(dest)
            context.close()
        browser.close()
    return written


def shoot_urls(
    urls: list[str],
    widths: list[int],
    *,
    state_path: Path,
    out_dir: Path,
    click: str = "",
    ready: str = "",
) -> list[Path]:
    """Screenshot each URL at each width, reusing ``state_path``.

    Args:
        urls: Absolute loopback URLs.
        widths: Viewport widths in pixels.
        state_path: storageState from :func:`login_state`.
        out_dir: Directory for PNG files.
        click: Optional CSS selector to click before the shot.
        ready: Optional CSS selector that must be visible first.

    Returns:
        PNG paths written.
    """
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        for url in urls:
            assert_loopback(url)
            for width in widths:
                context = browser.new_context(
                    storage_state=str(state_path),
                    viewport={"width": int(width), "height": 900},
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded")
                if ready:
                    page.locator(ready).first.wait_for(state="visible", timeout=30000)
                if click:
                    page.locator(click).first.click()
                name = f"{slug_for(url)}-{width}.png"
                dest = out_dir / name
                page.screenshot(path=str(dest), full_page=True)
                written.append(dest)
                context.close()
        browser.close()
    return written




def shoot_mck230(
    base: str,
    state_path: Path,
    out_dir: Path,
    db: Path,
    widths: list[int],
) -> list[Path]:
    """MCK-230: Participation tab with merged Individual/Group filters and fraction view."""
    from playwright.sync_api import sync_playwright

    class_id = shawn_class_id(db)
    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    url = f"{origin}/staff/class/{class_id}?tab=ap&view=participation"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        for label, setup in (
            ("mck-230-participation-points", "points"),
            ("mck-230-participation-fraction", "fraction"),
        ):
            for width in widths:
                context = browser.new_context(
                    storage_state=str(state_path),
                    viewport={"width": int(width), "height": 900},
                )
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=60000)
                page.locator("#sheet").wait_for(state="visible", timeout=60000)
                if setup == "fraction":
                    page.select_option("#ap-display-mode-select", "fraction")
                page.wait_for_function(
                    "() => document.querySelector('th.live-sub')",
                    timeout=60000,
                )
                page.wait_for_selector(".ap-finish-module", timeout=60000)
                dest = out_dir / f"{label}-{width}.png"
                page.screenshot(path=str(dest), full_page=True)
                written.append(dest)
                context.close()
        browser.close()
    return written


def shoot_mck220(
    base: str,
    state_path: Path,
    out_dir: Path,
    db: Path,
    widths: list[int],
) -> list[Path]:
    """MCK-220: Participation tab group grid + individual module columns."""
    from playwright.sync_api import sync_playwright

    class_id = shawn_class_id(db)
    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    url = f"{origin}/staff/class/{class_id}?tab=ap&view=participation"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        for label, setup in (
            ("mck-220-participation-group", None),
            ("mck-220-participation-individual-level", "individual"),
        ):
            for width in widths:
                context = browser.new_context(
                    storage_state=str(state_path),
                    viewport={"width": int(width), "height": 900},
                )
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=60000)
                page.locator("#sheet").wait_for(state="visible", timeout=60000)
                if setup == "individual":
                    page.select_option("#ap-data-mode-select", "individual")
                    page.wait_for_function(
                        "() => document.querySelector('table.ap-module-grid th.ap-module-col')"
                        " && document.querySelector('.ap-module-mark')",
                        timeout=60000,
                    )
                    page.wait_for_function(
                        "() => document.body.innerText.includes('M3 (2)')",
                        timeout=60000,
                    )
                else:
                    page.wait_for_function(
                        "() => document.querySelector('.attendance-grid th.live-sub')",
                        timeout=60000,
                    )
                dest = out_dir / f"{label}-{width}.png"
                page.screenshot(path=str(dest), full_page=True)
                written.append(dest)
                context.close()
        browser.close()
    return written


def prepare_mck207_pin_modal(path: Path) -> tuple[int, str, int]:
    """Ensure one open catch-up link and a student with default PIN (popup)."""
    class_id = shawn_class_id(path)
    con = sqlite3.connect(path)
    try:
        row = con.execute(
            "SELECT token FROM catchup_links WHERE class_id = ? ORDER BY id DESC LIMIT 1",
            (class_id,),
        ).fetchone()
        if row is None:
            token = "mck207demo"
            con.execute(
                """
                INSERT INTO catchup_links (
                    token, class_id, run_key, live_module, live_slot, is_open,
                    created_at, updated_at
                ) VALUES (?, ?, 'session:mck207:2026-10-13T09:00:00', 'M2', 'C1', 1,
                    '2026-10-13T09:00:00', '2026-10-13T09:00:00')
                """,
                (token, class_id),
            )
        else:
            token = str(row[0])
        sid = con.execute(
            "SELECT id FROM students WHERE class_id = ? ORDER BY id LIMIT 1",
            (class_id,),
        ).fetchone()[0]
        sys.path.insert(0, str(LMS_DIR))
        from participation import hash_pin

        con.execute(
            """
            UPDATE students SET pin_hash = ?, pin_set_by = 'teacher', default_pin_ciphertext = NULL
            WHERE id = ?
            """,
            (hash_pin("5678"), int(sid)),
        )
        con.execute(
            "UPDATE classes SET require_catchup_pin_change = 0 WHERE id = ?",
            (class_id,),
        )
        con.commit()
    finally:
        con.close()
    return class_id, token, int(sid)


def prepare_mck207_run_toggle(path: Path) -> tuple[int, str, int]:
    """Seed one ended live session with a classified real run for the toggle shot."""
    import json

    class_id = shawn_class_id(path)
    con = sqlite3.connect(path)
    try:
        row = con.execute(
            "SELECT offering_id FROM classes WHERE id = ?", (class_id,)
        ).fetchone()
        offering_id = int(row[0]) if row and row[0] is not None else 0
        teacher_id = con.execute(
            "SELECT id FROM users WHERE lower(email) = lower(?) LIMIT 1",
            (SHAWN_EMAIL,),
        ).fetchone()[0]
        now = "2026-10-14T15:10:00"
        run_key = f"session:shoot-mck207:{now}"
        old_keys = [
            str(r[0])
            for r in con.execute(
                "SELECT run_key FROM live_class_sessions WHERE run_key LIKE 'session:shoot-mck207:%'"
            ).fetchall()
        ]
        for key in old_keys:
            con.execute(
                "DELETE FROM participation_items WHERE class_id = ? AND run_key = ?",
                (class_id, key),
            )
            con.execute(
                "DELETE FROM participation_runs WHERE class_id = ? AND run_key = ?",
                (class_id, key),
            )
        con.execute(
            "DELETE FROM live_class_sessions WHERE run_key LIKE 'session:shoot-mck207:%'"
        )
        cur = con.execute(
            """
            INSERT INTO live_class_sessions (
                class_id, offering_id, teacher_user_id, session_code, status, started_at, ended_at,
                run_key, teacher_state_json
            ) VALUES (?, ?, ?, 'SHOOT', 'ended', ?, ?, ?, ?)
            """,
            (
                class_id,
                offering_id,
                int(teacher_id),
                now,
                now,
                run_key,
                json.dumps({"live_module": "M1", "live_slot": "C1"}),
            ),
        )
        sid = int(cur.lastrowid)
        con.execute(
            """
            INSERT INTO participation_items (
                class_id, run_key, live_module, live_slot, question_key, item_json,
                prompt_kind, offered_at
            ) VALUES (?, ?, 'M1', 'C1', 'item:q0', '{}', 'poll', ?)
            ON CONFLICT(class_id, run_key, question_key) DO NOTHING
            """,
            (class_id, run_key, now),
        )
        con.execute(
            """
            INSERT INTO participation_runs (
                class_id, run_key, is_real, auto_is_real, discarded
            ) VALUES (?, ?, 1, 1, 0)
            ON CONFLICT(class_id, run_key) DO UPDATE SET
                is_real = 1, auto_is_real = 1, discarded = 0
            """,
            (class_id, run_key),
        )
        con.commit()
        con.execute("PRAGMA wal_checkpoint(FULL)")
    finally:
        con.close()
    return class_id, run_key, sid


def shoot_mck207_run_toggle(
    base: str, state_path: Path, out_dir: Path, path: Path, widths: list[int]
) -> list[Path]:
    """MCK-207: Real class / Test toggle on the ended live teacher header."""
    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    class_id, _run_key, session_id = prepare_mck207_run_toggle(path)
    url = f"{origin}/staff/class/{class_id}?tab=live&live_session_id={session_id}"
    shots: list[Path] = []
    for width in widths:
        dest = out_dir / f"mck207-run-class-toggle-{width}.png"
        shots.extend(
            shoot_urls(
                [url],
                [width],
                state_path=state_path,
                out_dir=out_dir,
                ready="#run-class-kind:not([hidden])",
            )
        )
        src = out_dir / f"{slug_for(url)}-{width}.png"
        if src.exists() and src != dest:
            src.rename(dest)
            shots.append(dest)
    return shots


def shoot_mck233_end_live_dialog(
    base: str, state_path: Path, out_dir: Path, path: Path, widths: list[int]
) -> list[Path]:
    """MCK-233: End Live Class dialog Test/Live toggle."""
    from playwright.sync_api import sync_playwright

    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    school = _mck208_school(path)
    class_id = shawn_class_id(path)
    teacher = school.get_user_by_email(SHAWN_EMAIL)
    if teacher is None:
        raise SystemExit("Shawn staff user missing. Run with --seed first.")
    _mck208_end_active_live(school, class_id)
    school.start_live_class_session(class_id, int(teacher["id"]))
    url = f"{origin}/staff/class/{class_id}?tab=live"
    out_dir.mkdir(parents=True, exist_ok=True)
    shots: list[Path] = []
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        for width in widths:
            context = browser.new_context(
                storage_state=str(state_path), viewport={"width": int(width), "height": 900}
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.locator("[data-open-end-live]").first.click()
            dialog = page.locator("#end-live-dialog")
            dialog.wait_for(state="visible", timeout=30000)
            dest = out_dir / f"mck233-end-live-default-{width}.png"
            page.screenshot(path=str(dest), full_page=True)
            shots.append(dest)
            dest2 = out_dir / f"mck233-end-live-test-selected-{width}.png"
            page.locator('input[name="run_mode"][value="test"]').check()
            page.screenshot(path=str(dest2), full_page=True)
            shots.append(dest2)
            dest3 = out_dir / f"mck233-end-live-live-selected-{width}.png"
            page.locator('input[name="run_mode"][value="live"]').check()
            page.screenshot(path=str(dest3), full_page=True)
            shots.append(dest3)
            context.close()
        browser.close()
    return shots

def shoot_mck207(base: str, state_path: Path, out_dir: Path, path: Path, widths: list[int]) -> list[Path]:
    """MCK-207: roster tab, catch-up sign-in, header, PIN pop-up."""
    from playwright.sync_api import sync_playwright

    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    class_id, token, _sid = prepare_mck207_pin_modal(path)
    school = _mck208_school(path)
    link_row = next(
        row for row in school.list_catchup_links(class_id) if str(row["token"]) == token
    )
    canonical = origin + school.catchup_canonical_path_for_link(
        {**link_row, "class_id": class_id, "token": token}
    )
    canonical_play = f"{canonical}?play=1"
    legacy_signin = f"{origin}/catchup/{token}"
    roster = f"{origin}/staff/class/{class_id}?tab=ap&view=roster"
    portal_code, student_name = prepare_mck222_portal_join(path)
    catchup_pin = "5678"
    shots: list[Path] = []
    shots.extend(
        shoot_urls([roster], widths, state_path=state_path, out_dir=out_dir, ready="#roster-root")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        for width in widths:
            context = browser.new_context(viewport={"width": int(width), "height": 900})
            page = context.new_page()
            page.goto(f"{origin}/", wait_until="domcontentloaded")
            page.locator("#code").fill(portal_code)
            page.locator("#student-name").fill(student_name)
            page.locator("form#student-code-panel button[type='submit']").click()
            page.wait_for_url("**/student/catchup**", timeout=30000)
            page.goto(legacy_signin, wait_until="domcontentloaded")
            page.locator("h1", has_text="Enter your PIN").wait_for(state="visible", timeout=30000)
            dest_signin = out_dir / f"mck207-catchup-signin-{width}.png"
            page.screenshot(path=str(dest_signin), full_page=True)
            shots.append(dest_signin)

            _catchup_landing_signin_via_portal(
                page, origin, portal_code, student_name, pin=catchup_pin
            )
            page.goto(canonical_play, wait_until="domcontentloaded")
            page.locator("#catchup-data, .kicker").first.wait_for(state="visible", timeout=30000)
            dest_header = out_dir / f"mck207-catchup-header-{width}.png"
            page.screenshot(path=str(dest_header), full_page=True)
            shots.append(dest_header)

            con = sqlite3.connect(path)
            try:
                con.execute(
                    "UPDATE classes SET require_catchup_pin_change = 1 WHERE id = ?",
                    (class_id,),
                )
                con.commit()
            finally:
                con.close()
            page.goto(canonical, wait_until="domcontentloaded")
            page.locator("#catchup-pin-dialog").wait_for(state="visible", timeout=30000)
            dest_popup = out_dir / f"mck207-catchup-pin-popup-{width}.png"
            page.screenshot(path=str(dest_popup), full_page=True)
            shots.append(dest_popup)
            context.close()
        browser.close()
    for w in widths:
        src = out_dir / f"{slug_for(roster)}-{w}.png"
        dest = out_dir / f"mck207-roster-{w}.png"
        if src.exists():
            src.rename(dest)
            shots.append(dest)
    return shots


def mck208_live_home_url(origin: str, path: Path) -> str:
    """Start one live class for Maple and return ``/student/s/<visit_token>``."""
    sys.path.insert(0, str(LMS_DIR))
    os.environ["LLOVES_DB"] = str(path)
    os.environ["LOCAL_DEV_LOGIN"] = "1"
    from app import create_app

    app = create_app(db_path=path, testing=True)
    school = app.config["SCHOOL_DB"]
    class_id = shawn_class_id(path)
    teacher = school.get_user_by_email(SHAWN_EMAIL)
    live = school.start_live_class_session(class_id, int(teacher["id"]))
    cls = school.game.get_class(class_id)
    code = str(live.get("session_code") or cls.get("live_access_code") or "")
    client = app.test_client()
    client.post("/auth/student-code", data={"code": code, "name": "Maple"}, follow_redirects=True)
    client.post("/student/mood", data={"mood": "good"}, follow_redirects=True)
    client.post("/student/character", data={"character": "fox"}, follow_redirects=True)
    attendees = school.list_live_session_attendees(int(live["id"]))
    visit = str(next(row["visit_token"] for row in attendees if row.get("codename") == "Maple"))
    return f"{origin.rstrip('/')}/student/s/{visit}"


def _mck208_school(path: Path):
    """SchoolDB for the same sqlite the running dev server uses."""
    sys.path.insert(0, str(LMS_DIR))
    os.environ["LLOVES_DB"] = str(path)
    os.environ["LOCAL_DEV_LOGIN"] = "1"
    from app import create_app

    return create_app(db_path=path, testing=True).config["SCHOOL_DB"]


def _mck208_signin_url(origin: str, school, class_id: int, token: str) -> str:
    """Canonical catch-up sign-in URL for one link token."""
    link_row = next(
        row for row in school.list_catchup_links(class_id) if str(row["token"]) == token
    )
    return origin + school.catchup_canonical_path_for_link(
        {**link_row, "class_id": class_id, "token": token}
    )


def _catchup_landing_signin_via_portal(
    page,
    origin: str,
    portal_code: str,
    student_name: str,
    *,
    pin: str = "5678",
) -> None:
    """Portal join + landing PIN (MCK-234; catch-up links no longer use a name picker)."""
    page.goto(f"{origin.rstrip('/')}/", wait_until="domcontentloaded")
    page.locator("#code").fill(portal_code)
    page.locator("#student-name").fill(student_name)
    page.locator("form#student-code-panel button[type='submit']").click()
    page.wait_for_url("**/student/catchup**", timeout=30000)
    landing_pin = page.locator("#landing_pin")
    if landing_pin.count():
        landing_pin.fill(pin)
        page.locator(".catchup-landing button.cl-btn-primary").click()
        page.wait_for_url("**/student/catchup**", timeout=30000)


def _mck208_pin_signin(
    page, signin_url: str, path: Path, *, pin: str = "5678"
) -> None:
    """Portal landing PIN, then catch-up doorway PIN when needed."""
    origin = urllib.parse.urljoin(signin_url, "/").rstrip("/")
    portal_code, student_name = prepare_mck222_portal_join(path)
    _catchup_landing_signin_via_portal(
        page, origin, portal_code, student_name, pin=pin
    )
    page.goto(signin_url, wait_until="domcontentloaded")
    pin_field = page.locator("#personal_pin")
    if pin_field.count():
        pin_field.fill(pin)
        with page.expect_navigation(timeout=30000):
            page.locator('button[type="submit"]').first.click()
    if "/student/dashboard" not in page.url:
        page.wait_for_url("**/student/dashboard**", timeout=30000)


def _mck208_ensure_finish_open(school, class_id: int, student_id: int) -> None:
    """Ensure the dashboard shows at least one Finish these row (seed may be caught up)."""
    if school.student_dashboard_payload(int(class_id), int(student_id))["finish_these"]:
        return
    cid, sid = int(class_id), int(student_id)
    for link in school.list_catchup_links(cid):
        if not int(link.get("is_open") or 0):
            continue
        run_key = str(link["run_key"])
        with school._lock:
            school.conn.execute(
                """
                DELETE FROM participation_answers
                WHERE class_id = ? AND student_id = ? AND run_key = ?
                """,
                (cid, sid, run_key),
            )
            school.conn.commit()
        if school.student_dashboard_payload(cid, sid)["finish_these"]:
            return
    raise SystemExit("No open catch-up rows for Maple; cannot shoot finish-open dashboard.")


def _mck208_answer_all_catchup(school, class_id: int, student_id: int) -> None:
    """Clear Finish these by answering every open catch-up question once."""
    for link in school.list_catchup_links(class_id):
        if not int(link.get("is_open") or 0):
            continue
        tok = str(link["token"])
        for question in school.catchup_questions(tok, int(student_id)):
            if question.get("answered"):
                continue
            school.submit_catchup_answer(
                tok, int(student_id), str(question["question_key"]), "A"
            )




def _mck208_ensure_past_modules(school, class_id: int, student_id: int) -> None:
    """Ensure Past modules section is visible for screenshots."""
    cid, sid = int(class_id), int(student_id)
    if school.student_dashboard_payload(cid, sid)["past_modules"]:
        return
    school.set_module_complete(cid, "M1", True)
    with school._lock:
        has_m2 = school.conn.execute(
            "SELECT 1 FROM participation_items WHERE class_id = ? AND live_module = 'M2' LIMIT 1",
            (cid,),
        ).fetchone()
        if not has_m2:
            sample = school.conn.execute(
                """
                SELECT run_key, live_slot, question_key
                FROM participation_items
                WHERE class_id = ? AND live_module = 'M1'
                LIMIT 1
                """,
                (cid,),
            ).fetchone()
            if sample is None:
                raise SystemExit("No M1 participation rows; cannot fabricate M2 for past-modules shot.")
            school.conn.execute(
                """
                INSERT OR IGNORE INTO participation_items (
                    class_id, run_key, live_module, live_slot, question_key,
                    item_json, prompt_kind, offered_at
                ) VALUES (?, ?, 'M2', ?, ?, '{}', 'prompt', datetime('now'))
                """,
                (
                    cid,
                    f"{sample['run_key']}:mck208-past",
                    str(sample["live_slot"] or "C1"),
                    str(sample["question_key"]),
                ),
            )
            school.conn.commit()
    if not school.student_dashboard_payload(cid, sid)["past_modules"]:
        raise SystemExit("No past modules for Maple; cannot shoot past-modules dashboard.")

def _mck208_end_active_live(school, class_id: int) -> None:
    """End any active live session for this class (screenshot prep)."""
    live = school.get_active_live_session_for_class(int(class_id))
    if live is not None:
        school.end_live_class_session(int(live["id"]))


def mck215_maple_visit_url(
    school: object,
    class_id: int,
    teacher_id: int,
    *,
    group_question: bool,
) -> str:
    """Start a fresh live class for Maple and return the visit token (MCK-215)."""
    _mck208_end_active_live(school, class_id)
    live = school.start_live_class_session(int(class_id), int(teacher_id))
    session_id = int(live["id"])
    maple = school.game.find_student_by_codename(class_id, "Maple")
    if maple is None:
        raise SystemExit("No Maple on the demo class. Run with --seed first.")
    maple_id = int(maple["id"])
    school.join_live_class_session(session_id, maple_id, codename="Maple")
    if group_question:
        school.record_engagement_event(
            "item.offered",
            session_id=session_id,
            item_ref="shoot-mck215-group",
            payload={"mode": "group"},
        )
    rows = school.list_live_session_attendees(session_id)
    visit = str(next(row["visit_token"] for row in rows if row.get("codename") == "Maple"))
    return visit


def _mck215_dismiss_catchup_pin(page: object) -> None:
    """Close the first-login catch-up PIN modal via Later (MCK-215 shots)."""
    dialog = page.locator(".catchup-pin-dialog")  # type: ignore[attr-defined]
    try:
        dialog.wait_for(state="visible", timeout=20000)
        later = page.locator("#live-catchup-pin-later")  # type: ignore[attr-defined]
        later.click(timeout=5000)
        dialog.wait_for(state="hidden", timeout=15000)
    except Exception:
        pass


def _mck215_reach_live_home(page: object) -> None:
    """Join flow through mood/character until ``/student/home`` (MCK-215)."""
    page.wait_for_url("**/student/**", timeout=30000)  # type: ignore[attr-defined]
    if "/student/mood" in page.url:  # type: ignore[attr-defined]
        page.get_by_role("button", name="Skip").click()  # type: ignore[attr-defined]
        page.wait_for_url("**/student/character**", timeout=30000)  # type: ignore[attr-defined]
    if "/student/character" in page.url:  # type: ignore[attr-defined]
        page.locator('label:has(input[value="fox"])').click()  # type: ignore[attr-defined]
        page.locator('button[type="submit"]').click()  # type: ignore[attr-defined]
    page.wait_for_url("**/student/home**", timeout=30000)  # type: ignore[attr-defined]
    page.locator(".student-home, .student-portal.student-home").first.wait_for(  # type: ignore[attr-defined]
        state="visible", timeout=30000
    )


def shoot_mck215(base: str, out_dir: Path, path: Path, widths: list[int]) -> list[Path]:
    """MCK-215: student live engagement card before/after a group question (PIN dismissed)."""
    from playwright.sync_api import sync_playwright

    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    prepare_mck207_pin_modal(path)
    school = _mck208_school(path)
    class_id = shawn_class_id(path)
    teacher = school.get_user_by_email(SHAWN_EMAIL)
    if teacher is None:
        raise SystemExit("Shawn staff user missing. Run with --seed first.")
    teacher_id = int(teacher["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    shots: tuple[tuple[bool, str], ...] = (
        (False, "mck215-engagement-no-group"),
        (True, "mck215-engagement-with-group"),
    )
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        for group_question, prefix in shots:
            visit = mck215_maple_visit_url(
                school, class_id, teacher_id, group_question=group_question
            )
            url = f"{origin}/student/s/{visit}"
            for width in widths:
                context = browser.new_context(viewport={"width": int(width), "height": 900})
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded")
                _mck215_reach_live_home(page)
                _mck215_dismiss_catchup_pin(page)
                page.locator("#me-engagement").wait_for(state="visible", timeout=30000)
                if group_question:
                    page.wait_for_function(
                        "() => { const el = document.getElementById('me-team-stat'); "
                        "return el && !el.hidden; }",
                        timeout=30000,
                    )
                else:
                    page.wait_for_function(
                        "() => { const el = document.getElementById('me-team-stat'); "
                        "return el && el.hidden; }",
                        timeout=30000,
                    )
                dest = out_dir / f"{prefix}-{width}.png"
                page.screenshot(path=str(dest), full_page=True)
                written.append(dest)
                context.close()
        browser.close()
    return written


def shoot_mck208(base: str, out_dir: Path, path: Path, widths: list[int], *, only: str | None = None) -> list[Path]:
    """MCK-208: student dashboard states at 390px (seeded Maple / PIN portal)."""
    from playwright.sync_api import sync_playwright

    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    class_id, token, student_id = prepare_mck207_pin_modal(path)
    school = _mck208_school(path)
    _mck208_end_active_live(school, class_id)
    _mck208_ensure_finish_open(school, class_id, student_id)
    signin_url = _mck208_signin_url(origin, school, class_id, token)
    teacher = school.get_user_by_email(SHAWN_EMAIL)
    dashboard_url = f"{origin}/student/dashboard"
    shots: list[Path] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    use_widths = [w for w in widths if int(w) == 390] or [390]
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        for width in use_widths:
            context = browser.new_context(viewport={"width": int(width), "height": 900})
            page = context.new_page()

            _mck208_pin_signin(page, signin_url, path)
            page.locator(".sd-header").wait_for(state="visible", timeout=30000)
            page.locator(".sd-finish-list").wait_for(state="visible", timeout=30000)
            finish_open = out_dir / f"mck208-dashboard-finish-open-{width}.png"
            page.screenshot(path=str(finish_open), full_page=True)
            shots.append(finish_open)
            if only == "finish-open":
                context.close()
                continue

            live = school.start_live_class_session(class_id, int(teacher["id"]))
            page.goto(dashboard_url, wait_until="domcontentloaded")
            page.locator("#student-live-rejoin-strip", has_text="Rejoin class").wait_for(
                state="visible", timeout=30000
            )
            live_strip = out_dir / f"mck208-dashboard-live-strip-{width}.png"
            page.screenshot(path=str(live_strip), full_page=True)
            shots.append(live_strip)

            page.goto(f"{origin}/student/rejoin-live", wait_until="domcontentloaded")
            if "/student/mood" in page.url:
                page.locator('button[name="skip"]').click()
                page.wait_for_url("**/student/character**", timeout=30000)
            if "/student/character" in page.url:
                page.locator('label:has(input[value="fox"])').click()
                page.locator('button[type="submit"]').click()
                page.wait_for_url("**/student/home**", timeout=30000)
            page.locator(".student-home, .student-portal.student-home").first.wait_for(
                state="visible", timeout=30000
            )
            takeover = out_dir / f"mck208-live-takeover-{width}.png"
            page.screenshot(path=str(takeover), full_page=True)
            shots.append(takeover)

            school.end_live_class_session(int(live["id"]))
            page.goto(dashboard_url, wait_until="domcontentloaded")
            page.locator(".sd-header").wait_for(state="visible", timeout=30000)
            page.locator("#student-live-rejoin-strip").wait_for(state="hidden", timeout=30000)
            page.locator(".sd-finish-list").wait_for(state="visible", timeout=30000)
            after_end = out_dir / f"mck208-dashboard-after-live-end-{width}.png"
            page.screenshot(path=str(after_end), full_page=True)
            shots.append(after_end)

            _mck208_answer_all_catchup(school, class_id, student_id)
            page.goto(dashboard_url, wait_until="domcontentloaded")
            page.locator(".sd-header").wait_for(state="visible", timeout=30000)
            page.get_by_text("All caught up.").wait_for(state="visible", timeout=30000)
            caught_up = out_dir / f"mck208-dashboard-all-caught-up-{width}.png"
            page.screenshot(path=str(caught_up), full_page=True)
            shots.append(caught_up)

            _mck208_ensure_past_modules(school, class_id, student_id)
            page.goto(dashboard_url, wait_until="domcontentloaded")
            page.locator("#sd-past").wait_for(state="visible", timeout=30000)
            past_mod = out_dir / f"mck208-dashboard-past-modules-{width}.png"
            page.screenshot(path=str(past_mod), full_page=True)
            shots.append(past_mod)

            context.close()
        browser.close()
    return shots


def prepare_mck222_portal_join(path: Path) -> tuple[str, str]:
    """Portal join code and a roster first name for the catch-up landing picker.

    Args:
        path: LMS sqlite path.

    Returns:
        ``(portal_join_code, student_first_name)``.

    Raises:
        SystemExit: Seed data is missing a portal code or student.
    """
    class_id = shawn_class_id(path)
    con = sqlite3.connect(path)
    try:
        row = con.execute(
            """
            SELECT cl.portal_join_code, s.first_name
            FROM catchup_links cl
            JOIN students s ON s.class_id = cl.class_id
            WHERE cl.class_id = ?
              AND cl.is_open = 1
              AND cl.portal_join_code IS NOT NULL
              AND TRIM(cl.portal_join_code) <> ''
            ORDER BY cl.id DESC, s.id ASC
            LIMIT 1
            """,
            (class_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise SystemExit("No open catch-up link with portal_join_code. Run with --seed.")
    code, name = str(row[0]).strip(), str(row[1] or "").strip()
    if not code or not name:
        raise SystemExit("MCK-222 shoot needs portal_join_code and a student first name.")
    return code, name


def shoot_mck222(base: str, out_dir: Path, path: Path, widths: list[int]) -> list[Path]:
    """Student landing join → landing PIN → catch-up picker with tier cells (MCK-222).

    Args:
        base: Loopback origin.
        out_dir: Artifact directory.
        path: LMS sqlite path.
        widths: Viewport widths in pixels.

    Returns:
        PNG paths written.
    """
    from playwright.sync_api import sync_playwright

    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    prepare_mck207_pin_modal(path)
    portal_code, first_name = prepare_mck222_portal_join(path)
    catchup_pin = "5678"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        for width in widths:
            context = browser.new_context(viewport={"width": int(width), "height": 900})
            page = context.new_page()
            page.goto(f"{origin}/", wait_until="domcontentloaded")
            page.locator("#code").fill(portal_code)
            page.locator("#student-name").fill(first_name)
            page.locator("form#student-code-panel button[type='submit']").click()
            page.wait_for_url("**/student/catchup**", timeout=30000)
            page.locator(".catchup-landing").first.wait_for(state="visible", timeout=30000)
            landing_pin = page.locator("#landing_pin")
            if landing_pin.count():
                landing_pin.fill(catchup_pin)
                page.locator(".catchup-landing button.cl-btn-primary").click()
            page.locator(".cl-tier-key", has_text="T1 Individual").first.wait_for(
                state="visible", timeout=30000
            )
            dest = out_dir / f"mck222-catchup-landing-{width}.png"
            page.screenshot(path=str(dest), full_page=True)
            written.append(dest)
            context.close()
        browser.close()
    return written


def shoot_catchup(base: str, state_path: Path, out_dir: Path, path: Path) -> list[Path]:
    """Save empty, mixed, and expanded catch-up cards at desktop width.

    Args:
        base: Loopback origin.
        state_path: Shawn's storageState.
        out_dir: Artifact directory.
        path: LMS sqlite path.

    Returns:
        PNG paths.
    """
    from playwright.sync_api import sync_playwright

    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    class_id = prepare_catchup(path, "empty")
    url = assert_loopback(f"{origin}/staff/class/{class_id}?tab=lesson-slides")
    out_dir.mkdir(parents=True, exist_ok=True)
    shots = {
        "empty": out_dir / "catchup-empty.png",
        "mixed": out_dir / "catchup-mixed.png",
        "earlier": out_dir / "catchup-earlier.png",
    }
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(
            storage_state=str(state_path),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        table = page.locator("#ls-run-table")
        table.wait_for(state="visible", timeout=15000)
        page.locator("#ls-run-table-body tr").first.wait_for(state="visible", timeout=15000)
        page.locator(".lesson-slides-table-card").screenshot(path=str(shots["empty"]))

        prepare_catchup(path, "mixed")
        page.reload(wait_until="domcontentloaded")
        table.wait_for(state="visible", timeout=15000)
        page.locator("#ls-run-table-body tr").first.wait_for(state="visible", timeout=15000)
        page.locator(".lesson-slides-table-card").screenshot(path=str(shots["mixed"]))

        page.locator("#ls-run-table details summary").first.click()
        page.locator("table.ls-questions-sheet").first.wait_for(state="visible", timeout=15000)
        page.locator(".lesson-slides-table-card").screenshot(path=str(shots["earlier"]))
        context.close()
        browser.close()
    return list(shots.values())


def _pin_mck62_teacher_stage(school, session_id: int) -> None:
    """Put the harness session on the M1 C1 page that shows ``q-one``.

    Args:
        school: ``SchoolDb`` from the live harness.
        session_id: Active live session id.
    """
    school.set_live_session_teacher_state(
        session_id,
        stage="round",
        class_set=True,
        page_id="round:1",
        live_module="M1",
        live_slot="C1",
        scoreboard_visible=False,
    )
    school.set_live_session_active_media(session_id, clear=True)


def _wait_quiet_member_dots(
    page, budget: float = 30.0, *, school=None, session_id: int | None = None
) -> bool:
    """Find the published group card and wait for grey quiet-member dots.

    Args:
        page: Playwright page on Run Live Class (Questions tab).
        budget: Seconds to keep trying page/stage navigation.

    Returns:
        True when at least one ``.quiet-member-dot`` is visible.
    """
    end = time.time() + budget
    prev_clicks = 0
    while time.time() < end:
        if school is not None and session_id:
            school.set_live_session_active_media(int(session_id), clear=True)
        questions = page.locator('.live-content-tab[data-tab="questions"]')
        if questions.count():
            questions.first.click(timeout=2000)
        if page.locator(".quiet-member-dot").count():
            return True
        prev = page.locator("#live-stage-prev")
        if prev.count() and prev_clicks < 12 and prev.is_enabled():
            prev.click(timeout=2000)
            prev_clicks += 1
        page.wait_for_timeout(600)
    return False


def _metadata_with_round_page() -> dict:
    """Deck metadata whose ``round:1`` rail page matches ``q-one`` (page_number 1)."""
    from test_live_class_backend_state import metadata_fixture  # noqa: E402

    meta = metadata_fixture()
    meta["pages"] = [
        {
            "id": "round:1",
            "name": "Round 1",
            "stage": "round",
            "page_number": 1,
        }
    ]
    return meta


def shoot_mck217_student_view(out_dir: Path) -> list[Path]:
    """MCK-217: full teacher shell Questions tab before/after Student view.

    Spins a throwaway live session (group MC with both teams sent), logs in as
    the harness teacher, and saves two 1280×900 viewport PNGs at the same scroll.

    Args:
        out_dir: Artifact directory.

    Returns:
        Paths written.
    """
    sys.path.insert(0, str(LMS_DIR))
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("LOCAL_DEV_LOGIN", "1")
    os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
    from test_live_class_backend_state import LiveBackendStateTests  # noqa: E402

    harness = LiveBackendStateTests()
    harness.setUp()
    try:
        harness.school.live_class_metadata_for_session = lambda _sid: _metadata_with_round_page()
        harness._begin_and_join(4)
        harness._setup_groups()
        harness.school.set_live_session_teacher_state(
            harness.session_id,
            stage="round",
            class_set=True,
            page_id="round:1",
            live_module="M1",
            live_slot="C1",
            run_as_group=True,
            scoreboard_visible=False,
        )
        items = harness.school.ensure_live_session_items(harness.session_id)
        group_item = next(row for row in items if row["item_id"] == "q-one")
        published = harness.school.publish_live_session_item(
            harness.session_id,
            int(group_item["id"]),
            publish_mode="group_submit",
        )
        harness.school.end_group_mc_pick_step(harness.session_id, int(published["id"]))
        for index, choice in ((0, "A"), (1, "B")):
            harness.school.submit_group_mc_answer(
                harness.session_id,
                int(published["id"]),
                harness.student_ids[index],
                choice=choice,
                why="because the graph rises",
            )
        port = 8766
        origin = f"http://127.0.0.1:{port}"
        assert_loopback(origin)
        url = (
            f"{origin}/staff/class/{harness.class_id}"
            f"?tab=live&live_session_id={harness.session_id}"
        )

        def run_server() -> None:
            harness.app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)

        threading.Thread(target=run_server, daemon=True).start()
        time.sleep(1.5)
        state = fresh_state_path()
        login_state(origin, state, teacher_email=str(harness.teacher["email"]))
        out_dir.mkdir(parents=True, exist_ok=True)
        shots = {
            "show-strength": out_dir / "mck217-questions-show-strength-1280.png",
            "student-view": out_dir / "mck217-questions-student-view-1280.png",
        }
        from playwright.sync_api import sync_playwright  # noqa: E402

        with sync_playwright() as playwright:
            browser = _launch(playwright)
            context = browser.new_context(
                storage_state=str(state),
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            harness.school.set_live_session_active_media(int(harness.session_id), clear=True)
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.locator("body.staff-shell").wait_for(state="attached", timeout=30000)
            page.locator('.live-content-tab[data-tab="questions"]').click(timeout=5000)
            card = page.locator("#live-question-list .live-question-card").first
            card.wait_for(state="visible", timeout=45000)
            page.locator("#live-question-list .group-status-board").first.wait_for(
                state="visible", timeout=30000
            )
            if page.locator("#live-question-list .group-status-board").count() != 1:
                raise SystemExit("expected exactly one group status board on the Questions card")
            page.locator("text=last: Aspen").first.wait_for(state="visible", timeout=15000)
            card.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            scroll_y = page.evaluate("() => window.scrollY")
            page.screenshot(path=str(shots["show-strength"]), full_page=False)
            page.locator("#live-student-view").click(timeout=10000)
            page.locator("body.is-student-view").wait_for(state="attached", timeout=10000)
            page.locator("#live-question-list .group-status-board").first.wait_for(
                state="visible", timeout=15000
            )
            page.evaluate("(y) => window.scrollTo(0, y)", scroll_y)
            page.wait_for_timeout(400)
            if page.locator("text=Aspen").count():
                raise SystemExit("Student view still shows Aspen on Questions card")
            page.screenshot(path=str(shots["student-view"]), full_page=False)
            context.close()
            browser.close()
        state.unlink(missing_ok=True)
        return list(shots.values())
    finally:
        harness.tearDown()


def shoot_mck62_quiet_dots(out_dir: Path) -> list[Path]:
    """MCK-62: first load, reload, and Student view exit (isolated harness).

    Spins a throwaway live session with a published group MC, logs in as the
    harness teacher, and saves three desktop PNGs under ``out_dir``.

    Args:
        out_dir: Artifact directory.

    Returns:
        Paths written.
    """
    sys.path.insert(0, str(LMS_DIR))
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("LOCAL_DEV_LOGIN", "1")
    os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
    from test_live_class_backend_state import (  # noqa: E402
        LiveBackendStateTests,
        metadata_fixture,
    )

    harness = LiveBackendStateTests()
    harness.setUp()
    try:
        harness.school.live_class_metadata_for_session = lambda _sid: metadata_fixture()
        harness._begin_and_join(4)
        harness._setup_groups()
        harness.school.set_live_session_teacher_state(
            harness.session_id,
            stage="round",
            class_set=True,
            page_id="round:1",
            live_module="M1",
            live_slot="C1",
        )
        items = harness.school.ensure_live_session_items(harness.session_id)
        group_item = next(row for row in items if row["item_id"] == "q-one")
        harness.school.publish_live_session_item(
            harness.session_id,
            int(group_item["id"]),
            publish_mode="group_submit",
        )
        port = 8765
        origin = f"http://127.0.0.1:{port}"
        assert_loopback(origin)
        url = (
            f"{origin}/staff/class/{harness.class_id}"
            f"?tab=live&live_session_id={harness.session_id}"
        )

        def run_server() -> None:
            harness.app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)

        threading.Thread(target=run_server, daemon=True).start()
        time.sleep(1.5)
        state = fresh_state_path()
        login_state(origin, state, teacher_email=str(harness.teacher["email"]))
        out_dir.mkdir(parents=True, exist_ok=True)
        shots = {
            "first-load": out_dir / "mck62-quiet-dots-first-load-1280.png",
            "reload": out_dir / "mck62-quiet-dots-reload-1280.png",
            "student-view-exit": out_dir / "mck62-quiet-dots-student-view-exit-1280.png",
        }
        from playwright.sync_api import sync_playwright  # noqa: E402

        with sync_playwright() as playwright:
            browser = _launch(playwright)
            context = browser.new_context(
                storage_state=str(state),
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()

            def ready_for_dots() -> None:
                _pin_mck62_teacher_stage(harness.school, harness.session_id)

            ready_for_dots()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if not _wait_quiet_member_dots(
                page, school=harness.school, session_id=harness.session_id
            ):
                raise SystemExit("quiet dots missing on first load")
            page.screenshot(path=str(shots["first-load"]), full_page=True)
            ready_for_dots()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_response(
                    lambda r: "/items/" in r.url and "/results" in r.url and r.status == 200,
                    timeout=45000,
                )
            except Exception:
                pass
            if not _wait_quiet_member_dots(
                page, school=harness.school, session_id=harness.session_id
            ):
                raise SystemExit("quiet dots missing after reload")
            page.screenshot(path=str(shots["reload"]), full_page=True)
            ready_for_dots()
            page.locator("#live-student-view").click(timeout=30000)
            page.locator("body.is-student-view").wait_for(state="attached", timeout=30000)
            page.locator("#student-view-exit").click(timeout=30000)
            page.locator("body.is-student-view").wait_for(state="detached", timeout=30000)
            ready_for_dots()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_response(
                    lambda r: "/items/" in r.url and "/results" in r.url and r.status == 200,
                    timeout=30000,
                )
            except Exception:
                pass
            if not _wait_quiet_member_dots(
                page, school=harness.school, session_id=harness.session_id
            ):
                raise SystemExit("quiet dots missing after Student view exit")
            page.screenshot(path=str(shots["student-view-exit"]), full_page=True)
            context.close()
            browser.close()
        state.unlink(missing_ok=True)
        return list(shots.values())
    finally:
        harness.tearDown()


def shoot_engagement_overrides(
    base: str, state_path: Path, out_dir: Path, *, class_id: int = 1
) -> list[Path]:
    """Save engagement override success for Maple (ungrouped) and Cedar (grouped).

    Args:
        base: Loopback origin.
        state_path: Shawn's storageState.
        out_dir: Artifact directory.
        class_id: Demo class id.

    Returns:
        PNG paths.
    """
    from playwright.sync_api import sync_playwright

    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    url = assert_loopback(f"{origin}/staff/class/{class_id}?tab=ap&view=engagement")
    out_dir.mkdir(parents=True, exist_ok=True)
    shots = {
        "maple": out_dir / "engagement-override-maple-1280.png",
        "cedar": out_dir / "engagement-override-cedar-1280.png",
    }
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(
            storage_state=str(state_path),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        def save_override(codename: str, level: str, dest: Path) -> None:
            """Open engagement summary, save one override, screenshot the row."""
            page.goto(url, wait_until="domcontentloaded")
            page.locator("#engagement-summary-card").wait_for(state="visible", timeout=15000)
            row = page.locator(".ap-engagement-student").filter(
                has=page.locator(".ap-engagement-name", has_text=codename)
            ).first
            row.wait_for(state="visible", timeout=15000)
            row.locator('select[name="level"]').select_option(level)
            row.locator('button[type="submit"]').click()
            row.locator(".ap-engagement-level-badge", has_text="Set by teacher").wait_for(
                state="visible", timeout=15000
            )
            row.screenshot(path=str(dest))

        save_override("Maple", "3", shots["maple"])
        save_override("Cedar", "2", shots["cedar"])
        context.close()
        browser.close()
    return list(shots.values())


def _wait_avatar_reward_modal_settled(page) -> None:
    """Wait until the reward dialog fade-in finishes (``rw-fade`` on ``.rw-modal``).

    Args:
        page: Playwright page on ``/student/character`` with the pop-up open.
    """
    modal = page.locator("#avatar-reward:not([hidden]) .rw-modal")
    modal.wait_for(state="visible", timeout=30000)
    page.evaluate(
        """() => new Promise((resolve) => {
          const modal = document.querySelector('#avatar-reward:not([hidden]) .rw-modal');
          if (!modal) {
            resolve();
            return;
          }
          const style = getComputedStyle(modal);
          const animating = style.animationName && style.animationName !== 'none';
          if (!animating) {
            resolve();
            return;
          }
          const finish = () => resolve();
          modal.addEventListener('animationend', finish, { once: true });
          setTimeout(finish, 400);
        })"""
    )
    page.wait_for_timeout(50)


def shoot_avatar_reward_popup(origin: str, out_dir: Path, widths: list[int], path: Path) -> list[Path]:
    """Open the Celebrations avatar reward pop-up on the character picker (JOIN).

    Seeds one Shoutout grant for Maple, starts a live class, joins as Maple,
    and screenshots the auto-opened ``You made Celebrations!`` dialog.

    Args:
        origin: Loopback LMS origin (no path).
        out_dir: Directory for PNG files.
        widths: Viewport widths in pixels.
        path: LMS sqlite path (same file the dev server uses).

    Returns:
        PNG paths written.
    """
    sys.path.insert(0, str(LMS_DIR))
    os.environ["LLOVES_DB"] = str(path)
    os.environ["LOCAL_DEV_LOGIN"] = "1"
    from app import create_app
    from celebration import set_featured_award
    from playwright.sync_api import sync_playwright

    app = create_app(db_path=path, testing=True)
    school = app.config["SCHOOL_DB"]
    class_id = shawn_class_id(path)
    teacher = school.get_user_by_email(SHAWN_EMAIL)
    if teacher is None:
        raise SystemExit("Shawn staff user missing. Run with --seed first.")
    active = school.get_active_live_session_for_class(class_id)
    if active:
        school.end_live_class_session(int(active["id"]))
    maple = school.game.find_student_by_codename(class_id, "Maple")
    if maple is None:
        raise SystemExit("No Maple on the demo class. Run with --seed first.")
    set_featured_award(
        school,
        teacher_user_id=int(teacher["id"]),
        class_id=class_id,
        student_id=int(maple["id"]),
        blurb="",
    )
    live = school.start_live_class_session(class_id, int(teacher["id"]))
    cls = school.game.get_class(class_id)
    code = str(live.get("session_code") or cls.get("live_access_code") or "")
    out_dir.mkdir(parents=True, exist_ok=True)
    base = assert_loopback(origin if "://" in origin else f"http://{origin}")
    written: list[Path] = []
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        for width in widths:
            context = browser.new_context(viewport={"width": int(width), "height": 900})
            page = context.new_page()
            page.goto(base, wait_until="domcontentloaded")
            page.fill("#code", code)
            page.fill("#student-name", "Maple")
            page.locator('#student-code-panel button[type="submit"]').click()
            page.wait_for_url("**/student/mood**", timeout=30000)
            page.get_by_role("button", name="Skip").click()
            page.wait_for_url("**/student/character**", timeout=30000)
            page.locator("#avatar-reward:not([hidden]) #rw-title").wait_for(
                state="visible", timeout=30000
            )
            _wait_avatar_reward_modal_settled(page)
            dest = out_dir / f"student-avatar-reward-{width}.png"
            page.screenshot(path=str(dest), full_page=True)
            written.append(dest)
            context.close()
        browser.close()
    return written


MCK216_OPTIONS = ("Two points", "A graph", "An equation", "A table")
MCK216_RANK_KEY = (2, 0, 3, 1)


def _mck216_key_order(school, session_id: int, live_item_id: int) -> list[str]:
    """Answer-order option ids for the seeded rank key (same labels as MCK-171)."""
    item = school.get_live_session_item(session_id, live_item_id)
    by_label = {row["label"]: row["id"] for row in school._rank_option_rows(item)}
    ordered = [by_label[label] for label in MCK216_OPTIONS]
    return [ordered[index] for index in MCK216_RANK_KEY]


def prepare_mck216_student_live_url(origin: str, path: Path) -> str:
    """Seed one active Team challenge rank (full order, not locked) and return Maple's live URL.

    Ends any active live on Shawn's class, starts a new session with a published
    group rank + ``group_rank_race``, and pre-fills the shared order without locking.

    Args:
        origin: Loopback LMS origin (scheme + host + port).
        path: Sqlite path the dev server uses.

    Returns:
        Absolute ``/student/s/<visit_token>`` URL for Maple.
    """
    sys.path.insert(0, str(LMS_DIR))
    os.environ["LLOVES_DB"] = str(path)
    os.environ["LOCAL_DEV_LOGIN"] = "1"
    from app import create_app

    app = create_app(db_path=path, testing=True)
    school = app.config["SCHOOL_DB"]
    class_id = shawn_class_id(path)
    teacher = school.get_user_by_email(SHAWN_EMAIL)
    if teacher is None:
        raise SystemExit("Shawn staff user missing. Run with --seed first.")
    maple = school.game.find_student_by_codename(class_id, "Maple")
    if maple is None:
        raise SystemExit("No Maple on the demo class. Run with --seed first.")
    _mck208_end_active_live(school, class_id)
    live = school.start_live_class_session(class_id, int(teacher["id"]))
    session_id = int(live["id"])
    school.set_live_session_teacher_state(
        session_id,
        stage="round",
        live_module="M1",
        live_slot="C1",
        page_id="round_1",
        class_set=True,
    )
    school.game.begin_game(class_id)
    present = [int(maple["id"])]
    assignments = [{"student_id": int(maple["id"]), "team_index": 0}]
    cedar = school.game.find_student_by_codename(class_id, "Cedar")
    if cedar is not None:
        present.append(int(cedar["id"]))
        assignments.append({"student_id": int(cedar["id"]), "team_index": 0})
    school.setup_live_session_groups(
        session_id,
        n_teams=1,
        mode="manual",
        present_ids=present,
        assignments=assignments,
    )
    school.ensure_live_session_items(session_id)
    placement = school.add_staff_question_to_class_playlist(
        class_id,
        "M1",
        "C1",
        question_type="rank",
        text="Rank these representations.",
        page_number=4,
        stage="round",
        options=list(MCK216_OPTIONS),
        rank_key=list(MCK216_RANK_KEY),
    )
    items = school.ensure_live_session_items(session_id)
    row = next(r for r in items if r["item_id"] == placement["item_id"])
    live_item_id = int(row["id"])
    school.update_live_session_item_settings(session_id, live_item_id, group_rank_race=True)
    school.publish_live_session_item(session_id, live_item_id, publish_mode="group_submit")
    key_order = _mck216_key_order(school, session_id, live_item_id)
    school.save_group_mc_draft(
        session_id,
        live_item_id,
        int(maple["id"]),
        order=key_order,
        choice=None,
        why=None,
    )
    code = str(
        live.get("session_code")
        or school.game.get_class(class_id).get("live_access_code")
        or ""
    )
    client = app.test_client()
    client.post("/auth/student-code", data={"code": code, "name": "Maple"}, follow_redirects=True)
    client.post("/student/mood", data={"mood": "good"}, follow_redirects=True)
    client.post("/student/character", data={"character": "fox"}, follow_redirects=True)
    attendees = school.list_live_session_attendees(session_id)
    visit = str(next(r["visit_token"] for r in attendees if r.get("codename") == "Maple"))
    base = assert_loopback(origin if "://" in origin else f"http://{origin}").rstrip("/")
    return f"{base}/student/s/{visit}"


def _mck216_wait_ready_to_lock(page) -> None:
    """Wait until the student card shows Undo + enabled Lock in (no strip / agree UI)."""
    lock = page.locator('[data-race-lock="1"]')
    lock.wait_for(state="visible", timeout=30000)
    if lock.is_disabled():
        raise SystemExit("Lock in our order is disabled; expected a complete unlocked order.")
    page.locator('[data-race-undo="1"]').wait_for(state="visible", timeout=15000)
    page.get_by_role("button", name="Lock in our order").wait_for(state="visible", timeout=15000)
    if page.locator(".race-phone-strip").count():
        raise SystemExit("race-phone-strip visible; MCK-216 shots expect no team strip.")
    if page.locator(".race-phone-pips").count():
        raise SystemExit("race-phone-pips visible; MCK-216 shots expect no agree pips.")


def shoot_mck216(base: str, out_dir: Path, path: Path, widths: list[int]) -> list[Path]:
    """MCK-216: student Team challenge group rank before and after lock-in (phone).

    Args:
        base: Loopback LMS origin.
        out_dir: Artifact directory.
        path: LMS sqlite path.
        widths: Viewport widths (typically ``390`` only).

    Returns:
        PNG paths: ``mck216-ready-<w>.png`` and ``mck216-locked-<w>.png`` per width.
    """
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        for width in widths:
            url = prepare_mck216_student_live_url(base, path)
            context = browser.new_context(viewport={"width": int(width), "height": 900})
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            _mck216_wait_ready_to_lock(page)
            ready = out_dir / f"mck216-ready-{width}.png"
            page.screenshot(path=str(ready), full_page=True)
            written.append(ready)
            page.locator('[data-race-lock="1"]').click(timeout=15000)
            page.locator('[data-race-phone-state="locked"]').wait_for(state="visible", timeout=30000)
            page.get_by_text("Waiting for the reveal.").wait_for(
                state="visible", timeout=15000
            )
            locked = out_dir / f"mck216-locked-{width}.png"
            page.screenshot(path=str(locked), full_page=True)
            written.append(locked)
            context.close()
        browser.close()
    return written


def main(argv: list[str] | None = None) -> int:
    """CLI entry: seed, log in once, then shoot URLs or the catch-up scenario."""
    parser = argparse.ArgumentParser(description="Headless local LMS screenshots.")
    parser.add_argument("--base", default=DEFAULT_BASE, help="Loopback origin.")
    parser.add_argument("--url", action="append", default=[], help="URL to shoot. Repeatable.")
    parser.add_argument("--widths", default="390,1280", help="Comma-separated viewport widths.")
    parser.add_argument("--out", default="/opt/cursor/artifacts", help="PNG directory.")
    parser.add_argument("--state", default="", help="storageState JSON path.")
    parser.add_argument("--mck208-only", default=None, choices=("finish-open",), help="With --scenario mck208, capture only this shot.")
    parser.add_argument("--seed", action="store_true", help="Run the local-dev seed first.")
    parser.add_argument("--click", default="", help="CSS selector to click before each shot.")
    parser.add_argument("--ready", default="", help="CSS selector that must be visible.")
    parser.add_argument(
        "--scenario",
        choices=(
            "catchup",
            "mck62-quiet-dots",
            "engagement-override",
            "mck207",
            "mck207-run-toggle",
            "mck208",
            "avatar-reward",
            "mck216",
            "mck220",
            "mck230",
            "mck215",
            "mck222",
            "mck219",
            "mck233-end-live",
            "mck217-student-view",
        ),
        default="",
        help=(
            "Built-in catch-up, MCK-62 quiet-dot, engagement override, "
            "MCK-207 roster/PIN, run-class toggle, MCK-208 dashboard, "
            "avatar-reward pop-up, MCK-216 Team challenge rank lock-in, "
            "MCK-220/230/215/222/219 scenarios, or MCK-217 Student view Questions."
        ),
    )
    args = parser.parse_args(argv)
    assert_loopback(args.base if "://" in args.base else f"http://{args.base}")
    if args.seed:
        seed_demo()
    path = db_path()
    owns_state = not args.state
    state = Path(args.state) if args.state else fresh_state_path()
    try:
        student_only = args.scenario in {"mck222"}
        needs_login = (
            not student_only
            and (args.seed or not state.exists() or state.stat().st_size == 0)
        )
        if needs_login:
            login_state(args.base, state, teacher_email=staff_shawn_email(path))
        out = Path(args.out)
        written: list[Path] = []
        if args.scenario == "catchup":
            written.extend(shoot_catchup(args.base, state, out, path))
        if args.scenario == "mck62-quiet-dots":
            written.extend(shoot_mck62_quiet_dots(out))
        if args.scenario == "engagement-override":
            written.extend(shoot_engagement_overrides(args.base, state, out))
        if args.scenario == "mck207":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck207(args.base, state, out, path, widths))
        if args.scenario == "mck207-run-toggle":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck207_run_toggle(args.base, state, out, path, widths))
        if args.scenario == "mck208":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck208(args.base, out, path, widths, only=args.mck208_only))
        if args.scenario == "avatar-reward":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_avatar_reward_popup(args.base, out, widths, path))
        if args.scenario == "mck216":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck216(args.base, out, path, widths))
        if args.scenario == "mck220":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck220(args.base, state, out, path, widths))
        if args.scenario == "mck230":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck230(args.base, state, out, path, widths))
        if args.scenario == "mck215":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck215(args.base, out, path, widths))
        if args.scenario == "mck222":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck222(args.base, out, path, widths))
        if args.scenario == "mck233-end-live":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck233_end_live_dialog(args.base, state, out, path, widths))
        if args.scenario == "mck219":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck219(args.base, state, out, widths))
        if args.scenario == "mck217-student-view":
            written.extend(shoot_mck217_student_view(out))
        if args.url:
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(
                shoot_urls(
                    args.url,
                    widths,
                    state_path=state,
                    out_dir=out,
                    click=args.click,
                    ready=args.ready,
                )
            )
        if not written:
            raise SystemExit(
                "Pass --url and/or --scenario "
                "catchup|mck62-quiet-dots|engagement-override|mck207|mck207-run-toggle|"
                "mck208|avatar-reward|mck216|mck220|mck230|mck215|mck222|mck219|mck217-student-view."
            )
        for item in written:
            print(item)
        return 0
    finally:
        if owns_state:
            state.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
