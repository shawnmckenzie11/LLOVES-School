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


def shoot_mck249(
    base: str,
    state_path: Path,
    out_dir: Path,
    db: Path,
    widths: list[int],
) -> list[Path]:
    """MCK-249 dashboard, primary tabs, More menu, and masked Roster & PINs.

    Refuses to write a shot when the page HTML contains a PIN slip or a
    roster PIN cell shows digits. The refusal does not print the page.

    Args:
        base: Loopback origin.
        state_path: Shawn storageState.
        out_dir: Directory for PNG files.
        db: LMS sqlite path, used to find Shawn's class.
        widths: Viewport widths. The spec shots are 1280.

    Returns:
        PNG paths written.
    """
    from playwright.sync_api import sync_playwright

    class_id = shawn_class_id(db)
    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    course = f"{origin}/staff/class/{class_id}"
    shots = (
        ("mck-249-dashboard", f"{origin}/staff", ""),
        ("mck-249-course-landing", course, ""),
        ("mck-249-tabs", f"{course}?tab=gradebook", ""),
        ("mck-249-more", f"{course}?tab=gradebook", "summary.course-more-summary"),
        ("mck-249-more-syllabus", f"{course}?tab=syllabus", ""),
        ("mck-249-library", f"{course}?tab=library", ""),
        ("mck-249-library-pages", f"{course}?tab=pages", ""),
        ("mck-249-attendance", f"{course}?tab=ap&view=attendance", ""),
        ("mck-249-roster", f"{course}?tab=ap&view=roster", ""),
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        for label, url, click in shots:
            assert_loopback(url)
            for width in widths:
                context = browser.new_context(
                    storage_state=str(state_path),
                    viewport={"width": int(width), "height": 900},
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                if label == "mck-249-course-landing":
                    page.locator("#course-tabs").wait_for(state="visible", timeout=15000)
                    page.locator(".staff-top-menu").wait_for(state="visible", timeout=15000)
                    page.locator(".topbar").wait_for(state="visible", timeout=15000)
                if "view=roster" in url:
                    page.locator(".ap-roster-table").wait_for(state="visible", timeout=30000)
                if click:
                    page.locator(click).first.click()
                    page.locator(".course-more-menu").wait_for(state="visible", timeout=5000)
                html = page.content()
                if "Your catch-up PIN:" in html:
                    context.close()
                    browser.close()
                    raise SystemExit("Refusing screenshot: page HTML contains a PIN slip")
                if page.locator(".ap-roster-pin").count():
                    for cell in page.locator(".ap-roster-pin").all_inner_texts():
                        if any(ch.isdigit() for ch in cell):
                            context.close()
                            browser.close()
                            raise SystemExit("Refusing screenshot: a PIN cell is unmasked")
                dest = out_dir / f"{label}-{width}.png"
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
                page.locator("#ap-expand-all").wait_for(state="visible", timeout=60000)
                if setup == "fraction":
                    page.locator("[data-display='fraction']").click()
                page.wait_for_function(
                    "() => document.querySelector('.ap-mod-table tbody tr')",
                    timeout=60000,
                )
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


def _ensure_real_participation_run_row(
    con: sqlite3.Connection, class_id: int, run_key: str, ended_at: str
) -> None:
    """Insert a real, undiscarded ``participation_runs`` row when the shoot seed lacks one."""
    exists = con.execute(
        "SELECT 1 FROM participation_runs WHERE class_id = ? AND run_key = ? LIMIT 1",
        (int(class_id), str(run_key)),
    ).fetchone()
    if exists is not None:
        return
    con.execute(
        """
        INSERT INTO participation_runs (
            class_id, run_key, is_real, auto_is_real, discarded, ended_at
        ) VALUES (?, ?, 1, 1, 0, ?)
        """,
        (int(class_id), str(run_key), ended_at),
    )


def prepare_mck207_pin_modal(path: Path) -> tuple[int, str, int]:
    """Ensure one open catch-up link and a student with default PIN (popup)."""
    class_id = shawn_class_id(path)
    demo_run_key = "session:mck207:2026-10-13T09:00:00"
    demo_ended = "2026-10-13T09:00:00"
    con = sqlite3.connect(path)
    try:
        row = con.execute(
            "SELECT token, run_key FROM catchup_links WHERE class_id = ? ORDER BY id DESC LIMIT 1",
            (class_id,),
        ).fetchone()
        if row is None:
            token = "mck207demo"
            con.execute(
                """
                INSERT INTO catchup_links (
                    token, class_id, run_key, live_module, live_slot, is_open,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'M2', 'C1', 1, ?, ?)
                """,
                (token, class_id, demo_run_key, demo_ended, demo_ended),
            )
            _ensure_real_participation_run_row(con, class_id, demo_run_key, demo_ended)
        else:
            token = str(row[0])
            run_key = str(row[1] or demo_run_key)
            if token == "mck207demo" or run_key == demo_run_key:
                _ensure_real_participation_run_row(
                    con,
                    class_id,
                    run_key,
                    demo_ended,
                )
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


def shoot_mck275_end_quit(
    base: str, state_path: Path, out_dir: Path, path: Path
) -> list[Path]:
    """MCK-275: End Class controls and the Quit confirm dialog at 1280x800.

    The join billboard text is blanked before the shot so a class code
    is not stored in the image.
    """
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
        context = browser.new_context(
            storage_state=str(state_path), viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.locator("#live-end-class").wait_for(state="visible", timeout=30000)
        page.evaluate(
            """() => {
              const code = document.querySelector("#ap-join-billboard-code");
              if (code) code.textContent = "····";
            }"""
        )
        controls = out_dir / "mck275-end-class-controls-1280.png"
        page.screenshot(path=str(controls), full_page=False)
        shots.append(controls)
        page.locator("#live-quit-class").click()
        dialog = page.locator("#quit-live-dialog")
        dialog.wait_for(state="visible", timeout=30000)
        page.locator("#quit-live-title", has_text="Quit without saving?").wait_for(
            state="visible", timeout=30000
        )
        confirm = out_dir / "mck275-quit-confirm-1280.png"
        page.screenshot(path=str(confirm), full_page=False)
        shots.append(confirm)
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


_MCK242_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def prepare_mck242_live(path: Path, origin: str) -> dict[str, int | str]:
    """Publish Team Draw on Shawn's class and return teacher/student live URLs.

    Ends any active live session, starts a fresh one with two teams (Maple + Cedar),
    publishes one built-in Team Draw prompt, and pre-submits Cedar's team so the
    teacher grid shows mixed ``drawing…`` / ``✓ submitted`` rows.

    Args:
        path: Sqlite path the dev server uses.
        origin: Loopback LMS origin.

    Returns:
        Dict with ``class_id``, ``session_id``, ``live_item_id``, ``teacher_url``,
        and ``student_url`` (Maple's visit link).
    """
    school = _mck208_school(path)
    from team_draw import BUILTIN_PROMPTS
    class_id = shawn_class_id(path)
    teacher = school.get_user_by_email(SHAWN_EMAIL)
    if teacher is None:
        raise SystemExit("Shawn staff user missing. Run with --seed first.")
    maple = school.game.find_student_by_codename(class_id, "Maple")
    if maple is None:
        raise SystemExit("No Maple on the demo class. Run with --seed first.")
    cedar = school.game.find_student_by_codename(class_id, "Cedar")
    if cedar is None:
        school.game.add_student(class_id, codename="Cedar")
        cedar = school.game.find_student_by_codename(class_id, "Cedar")
    if cedar is None:
        raise SystemExit("Could not add Cedar for Team Draw demo.")
    _mck208_end_active_live(school, class_id)
    live = school.start_live_class_session(class_id, int(teacher["id"]))
    session_id = int(live["id"])
    school.set_live_session_teacher_state(
        session_id,
        stage="round",
        live_module="M1",
        live_slot="C1",
        page_id="round:1",
        class_set=True,
        run_as_group=True,
        groups_configured=True,
    )
    school.game.begin_game(class_id)
    maple_id = int(maple["id"])
    cedar_id = int(cedar["id"])
    for student_id, codename in ((maple_id, "Maple"), (cedar_id, "Cedar")):
        school.join_live_class_session(session_id, student_id, codename=codename)
    school.setup_live_session_groups(
        session_id,
        n_teams=2,
        mode="manual",
        present_ids=[maple_id, cedar_id],
        assignments=[
            {"student_id": maple_id, "team_index": 0},
            {"student_id": cedar_id, "team_index": 1},
        ],
    )
    school.ensure_live_session_items(session_id)
    mystery = BUILTIN_PROMPTS[1]
    placement = school.add_staff_question_to_class_playlist(
        class_id,
        "M1",
        "C1",
        question_type="team_draw",
        text=mystery["text"],
        page_number=4,
        stage="round",
        extra_item={"team_draw_prompt_id": mystery["id"]},
    )
    items = school.ensure_live_session_items(session_id)
    row = next(r for r in items if r["item_id"] == placement["item_id"])
    live_item_id = int(row["id"])
    school.publish_live_session_item(session_id, live_item_id, publish_mode="group_submit")
    school.submit_team_draw(
        session_id,
        live_item_id,
        cedar_id,
        title="Forest Friends",
        image_b64=_MCK242_TINY_PNG_B64,
    )
    rows = school.list_live_session_attendees(session_id)
    visit = str(next(r["visit_token"] for r in rows if r.get("codename") == "Maple"))
    base = assert_loopback(origin if "://" in origin else f"http://{origin}").rstrip("/")
    teacher_url = f"{base}/staff/class/{class_id}?tab=live&live_session_id={session_id}"
    student_url = f"{base}/student/s/{visit}"
    return {
        "class_id": class_id,
        "session_id": session_id,
        "live_item_id": live_item_id,
        "teacher_url": teacher_url,
        "student_url": student_url,
    }


def shoot_mck242(
    base: str,
    state_path: Path,
    out_dir: Path,
    path: Path,
    widths: list[int],
) -> list[Path]:
    """MCK-242: Team Draw teacher status grid (desktop) and student board (phone).

    Args:
        base: Loopback LMS origin.
        state_path: Shawn's Playwright storageState.
        out_dir: Artifact directory.
        path: LMS sqlite path.
        widths: Viewport widths (typically ``1280`` and ``390``).

    Returns:
        PNG paths ``mck242-teacher-<w>.png`` and ``mck242-student-<w>.png``.
    """
    from playwright.sync_api import sync_playwright

    meta = prepare_mck242_live(path, base)
    teacher_url = str(meta["teacher_url"])
    student_url = str(meta["student_url"])
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        for width in widths:
            if int(width) >= 768:
                context = browser.new_context(
                    storage_state=str(state_path),
                    viewport={"width": int(width), "height": 900},
                )
                page = context.new_page()
                page.goto(teacher_url, wait_until="domcontentloaded", timeout=60000)
                page.locator("body.staff-shell").wait_for(state="attached", timeout=30000)
                page.locator('.live-content-tab[data-tab="questions"]').click(timeout=10000)
                page.locator(".team-draw-status-board").first.wait_for(state="visible", timeout=60000)
                page.get_by_role("button", name="Show gallery").wait_for(state="visible", timeout=30000)
                dest = out_dir / f"mck242-teacher-{width}.png"
                page.screenshot(path=str(dest), full_page=True)
                written.append(dest)
                context.close()
            else:
                context = browser.new_context(viewport={"width": int(width), "height": 900})
                page = context.new_page()
                page.goto(student_url, wait_until="domcontentloaded", timeout=60000)
                _mck215_reach_live_home(page)
                _mck215_dismiss_catchup_pin(page)
                page.locator(".live-canvas-stage").first.wait_for(state="visible", timeout=60000)
                page.locator("#student-canvas").wait_for(state="visible", timeout=30000)
                dest = out_dir / f"mck242-student-{width}.png"
                page.screenshot(path=str(dest), full_page=True)
                written.append(dest)
                context.close()
        browser.close()
    return written


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


def prepare_mck218_group_whiteboard(path: Path) -> tuple[int, int, str]:
    """Start a live class with a published group whiteboard and demo ink.

    Args:
        path: LMS sqlite path used by the running dev server.

    Returns:
        ``(class_id, session_id, visit_token)`` for staff/student URLs.
    """
    sys.path.insert(0, str(LMS_DIR))
    os.environ["LLOVES_DB"] = str(path)
    os.environ.setdefault("LOCAL_DEV_LOGIN", "1")
    from app import create_app

    app = create_app(db_path=path, testing=True)
    school = app.config["SCHOOL_DB"]
    class_id = shawn_class_id(path)
    teacher = school.get_user_by_email(staff_shawn_email(path))
    if teacher is None:
        raise SystemExit("Shawn staff user missing. Run with --seed first.")
    _mck208_end_active_live(school, class_id)
    try:
        school.game.end_game(class_id)
    except (KeyError, ValueError):
        pass
    live = school.start_live_class_session(class_id, int(teacher["id"]))
    session_id = int(live["id"])
    maple = school.game.find_student_by_codename(class_id, "Maple")
    if maple is None:
        raise SystemExit("No Maple on the demo class. Run with --seed first.")
    cedar = school.game.find_student_by_codename(class_id, "Cedar")
    if cedar is None:
        school.game.add_student(class_id, codename="Cedar")
        cedar = school.game.find_student_by_codename(class_id, "Cedar")
    pine = school.game.find_student_by_codename(class_id, "Pine")
    if pine is None:
        school.game.add_student(class_id, codename="Pine")
        pine = school.game.find_student_by_codename(class_id, "Pine")
    assert cedar is not None and pine is not None
    school.game.begin_game(class_id)
    school.game.clear_team_lock(class_id)
    for row in (maple, cedar, pine):
        school.join_live_class_session(
            session_id,
            int(row["id"]),
            codename=str(row["codename"]),
        )
    school.setup_live_session_groups(
        session_id,
        n_teams=2,
        mode="manual",
        present_ids=[int(maple["id"]), int(cedar["id"]), int(pine["id"])],
        assignments=[
            {"student_id": int(maple["id"]), "team_index": 0},
            {"student_id": int(cedar["id"]), "team_index": 0},
            {"student_id": int(pine["id"]), "team_index": 1},
        ],
    )
    school.ensure_live_session_items(session_id)
    wb = next(
        row
        for row in school.list_live_session_items(session_id)
        if str(row.get("kind") or "") == "whiteboard"
    )
    school.publish_live_session_item(
        session_id, int(wb["id"]), publish_mode=mode
    )
    grouped = mode == "group_shared"
    school.set_live_session_teacher_state(
        session_id,
        class_set=True,
        run_as_group=grouped,
        groups_configured=True,
        canvas_align="team" if grouped else "student",
        active_tab="canvas",
    )
    team_id = school.student_team_id_for_class(class_id, int(maple["id"]))
    for owner, name, x in (
        (str(int(maple["id"])), "Maple", 0.22),
        (str(int(cedar["id"])), "Cedar", 0.62),
    ):
        school.apply_live_canvas_presence(
            session_id,
            owner=owner,
            name=name,
            team_id=team_id,
            x=x,
            y=0.35,
            stroke_id=f"mck218-{owner}",
            point=[x, 0.35],
            as_teacher=False,
        )
    char_keys = list(school.game.character_keys(class_id).keys())
    if char_keys:
        school.game.set_character(class_id, int(maple["id"]), char_keys[0])
    maple_row = school.conn.execute(
        """
        SELECT visit_token FROM live_session_attendees
        WHERE live_session_id = ? AND student_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (session_id, int(maple["id"])),
    ).fetchone()
    token = str(maple_row["visit_token"] if maple_row else "")
    return class_id, session_id, token


def shoot_mck218(base: str, out_dir: Path, path: Path, state_path: Path) -> list[Path]:
    """MCK-218 formative whiteboard on real staff/student shells."""
    from playwright.sync_api import sync_playwright

    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    class_id, _session_id, visit_token = prepare_mck218_group_whiteboard(path)
    staff_url = f"{origin}/staff/class/{class_id}?tab=live"
    out_dir.mkdir(parents=True, exist_ok=True)
    shots: list[Path] = []
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        staff_ctx = browser.new_context(
            viewport={"width": 1280, "height": 900},
            storage_state=str(state_path) if state_path.exists() else None,
        )
        staff = staff_ctx.new_page()
        staff.goto(staff_url, wait_until="domcontentloaded")
        staff.locator('.live-content-tab[data-tab="canvas"]').click(timeout=15000)
        staff.locator("#formative-live-board .wb-live-name").first.wait_for(
            state="visible", timeout=60000
        )
        staff.locator("#formative-live-board .wb-live-tile").first.click()
        staff.locator("#wb-live-panel:not([hidden])").wait_for(state="visible", timeout=15000)
        staff.locator("#wb-live-undo").wait_for(state="visible", timeout=15000)
        teacher_path = out_dir / "mck218-teacher-1280.png"
        staff.screenshot(path=str(teacher_path), full_page=True)
        shots.append(teacher_path)
        staff_ctx.close()

        student_ctx = browser.new_context(viewport={"width": 390, "height": 900})
        student = student_ctx.new_page()
        student.goto(f"{origin}/student/s/{visit_token}", wait_until="domcontentloaded")
        for _ in range(12):
            _mck215_dismiss_catchup_pin(student)
            if student.locator("#canvas-pane:not([hidden])").count():
                break
            if "/student/mood" in student.url:
                student.locator('button[name="skip"]').click()
            elif "/student/character" in student.url:
                pick = student.locator('label:has(input[type="radio"])').first
                if pick.count():
                    pick.click()
                student.locator('button[type="submit"]').click()
            student.wait_for_timeout(1000)
        _mck215_dismiss_catchup_pin(student)
        student.locator("#canvas-pane:not([hidden])").wait_for(state="visible", timeout=90000)
        student.locator("#student-canvas").wait_for(state="visible", timeout=30000)
        student.evaluate(
            """() => {
              const canvas = document.getElementById('student-canvas');
              const stage = canvas && canvas.closest('.live-canvas-stage');
              if (canvas && stage) {
                window.dispatchEvent(new Event('resize'));
              }
            }"""
        )
        student.wait_for_timeout(1500)
        student_path = out_dir / "mck218-student-390.png"
        student.screenshot(path=str(student_path), full_page=True)
        shots.append(student_path)
        student_ctx.close()
        browser.close()
    return shots


def _mck272_open_student(page: object, origin: str, visit_token: str) -> None:
    """Land a student on the live home. Does not print the visit token."""
    page.goto(f"{origin}/student/s/{visit_token}", wait_until="domcontentloaded")  # type: ignore[attr-defined]
    for _ in range(12):
        _mck215_dismiss_catchup_pin(page)
        if "/student/home" in page.url and page.locator("#live-response").count():  # type: ignore[attr-defined]
            break
        if "/student/mood" in page.url:  # type: ignore[attr-defined]
            page.locator('button[name="skip"]').click()  # type: ignore[attr-defined]
        elif "/student/character" in page.url:  # type: ignore[attr-defined]
            pick = page.locator('label:has(input[type="radio"])').first  # type: ignore[attr-defined]
            if pick.count():
                pick.click()
            page.locator('button[type="submit"]').click()  # type: ignore[attr-defined]
        page.wait_for_timeout(1000)  # type: ignore[attr-defined]
    _mck215_dismiss_catchup_pin(page)
    page.locator("#live-response").wait_for(state="visible", timeout=90000)  # type: ignore[attr-defined]


def _mck272_box_in_view(page: object, selector: str) -> dict[str, float]:
    """Return a control's box and whether it sits inside the viewport."""
    box = page.locator(selector).bounding_box()  # type: ignore[attr-defined]
    if not box:
        raise SystemExit(f"Missing box for {selector}")
    size = page.viewport_size or {"width": 0, "height": 0}  # type: ignore[attr-defined]
    bottom = float(box["y"] + box["height"])
    right = float(box["x"] + box["width"])
    metrics = {
        "x": float(box["x"]),
        "y": float(box["y"]),
        "width": float(box["width"]),
        "height": float(box["height"]),
        "bottom": bottom,
        "right": right,
        "viewport_width": float(size["width"]),
        "viewport_height": float(size["height"]),
    }
    if box["y"] < -1 or box["x"] < -1 or bottom > size["height"] + 1 or right > size["width"] + 1:
        raise SystemExit(f"{selector} is outside the viewport: {metrics}")
    return metrics


def _mck272_assert_open_panel(page: object, label: str) -> dict[str, float]:
    """Require a tall canvas, Undo in view, and no page scroll.

    The canvas fills the space under the toolbar down to the window.
    Its height stays at least 320px.

    Args:
        page: Playwright page with the teacher panel open.
        label: Viewport label used when a check fails.

    Returns:
        Measured canvas and scroll figures.
    """
    metrics = page.evaluate(  # type: ignore[attr-defined]
        """() => {
          const canvas = document.querySelector('#wb-live-panel-canvas');
          const undo = document.querySelector('#wb-live-undo');
          const slot = document.querySelector('#canvas-zone');
          const c = canvas.getBoundingClientRect();
          const u = undo.getBoundingClientRect();
          const root = document.scrollingElement;
          return {
            canvasH: c.height,
            canvasBottom: c.bottom,
            innerH: window.innerHeight,
            canvasCss: getComputedStyle(canvas).height,
            panelCss: getComputedStyle(canvas.parentElement).height,
            panelPos: getComputedStyle(canvas.parentElement).position,
            undoTop: u.top,
            undoBottom: u.bottom,
            undoH: u.height,
            scrollTop: root ? root.scrollTop : 0,
            docExtra: root ? root.scrollHeight - window.innerHeight : 0,
            slotExtra: slot ? slot.scrollHeight - slot.clientHeight : 0,
          };
        }"""
    )
    print("mck272 panel", label, metrics)
    if float(metrics["canvasH"]) < 320:
        raise SystemExit(f"{label} canvas is shorter than 320px: {metrics}")
    if (
        float(metrics["undoH"]) < 1
        or float(metrics["undoTop"]) < -1
        or float(metrics["undoBottom"]) > float(metrics["innerH"]) + 1
    ):
        raise SystemExit(f"{label} Undo is outside the window: {metrics}")
    if (
        float(metrics["scrollTop"]) > 1
        or float(metrics["docExtra"]) > 2
        or float(metrics["slotExtra"]) > 2
    ):
        raise SystemExit(f"{label} page scrolls: {metrics}")
    gap = float(metrics["innerH"]) - float(metrics["canvasBottom"])
    if gap > 40 or gap < -12:
        raise SystemExit(f"{label} canvas does not meet the window bottom: {metrics}")
    return metrics


def _mck272_draw_and_measure(page: object) -> dict[str, object]:
    """Drag a stroke and report whether live ink sits on the pointer.

    Args:
        page: Playwright page with ``#student-canvas`` visible.

    Returns:
        Rect delta plus the live-layer sample around the pointer.
    """
    canvas = page.locator("#student-canvas")  # type: ignore[attr-defined]
    canvas.wait_for(state="visible", timeout=30000)
    box = canvas.bounding_box()
    if not box or box["height"] < 40 or box["width"] < 40:
        raise SystemExit(f"Student board has no drawable box: {box}")
    x = box["x"] + box["width"] * 0.35
    y = box["y"] + box["height"] * 0.42
    page.mouse.move(x, y)  # type: ignore[attr-defined]
    page.mouse.down()  # type: ignore[attr-defined]
    page.mouse.move(x + min(120, box["width"] * 0.25), y, steps=12)  # type: ignore[attr-defined]
    page.wait_for_timeout(180)  # type: ignore[attr-defined]
    metrics = page.evaluate(  # type: ignore[attr-defined]
        """(pointer) => {
          const canvas = document.getElementById('student-canvas');
          const stage = canvas && canvas.closest('.live-canvas-stage');
          const ink = stage && stage.querySelector('canvas.live-canvas-ink');
          const hit = document.elementFromPoint(pointer.x, pointer.y);
          if (!canvas || !ink) return { error: 'missing ink layer' };
          const crect = canvas.getBoundingClientRect();
          const irect = ink.getBoundingClientRect();
          const countNear = (layer, box) => {
            const ctx = layer.getContext('2d');
            if (!ctx || layer.width < 2 || layer.height < 2) return { n: 0, near: 0 };
            const data = ctx.getImageData(0, 0, layer.width, layer.height).data;
            let n = 0;
            let near = 0;
            const step = 4;
            for (let i = 3; i < data.length; i += 4 * step) {
              if (data[i] < 24) continue;
              n += 1;
              const pixel = (i / 4) | 0;
              const px = pixel % layer.width;
              const py = Math.floor(pixel / layer.width);
              const cssX = box.left + (px / layer.width) * box.width;
              const cssY = box.top + (py / layer.height) * box.height;
              if (Math.abs(cssY - pointer.y) <= 14 && Math.abs(cssX - pointer.x) <= 140) near += 1;
              if (n > 800) break;
            }
            return { n, near };
          };
          const live = countNear(ink, irect);
          const committed = countNear(canvas, crect);
          return {
            deltaX: irect.left - crect.left,
            deltaY: irect.top - crect.top,
            deltaW: irect.width - crect.width,
            deltaH: irect.height - crect.height,
            canvasH: crect.height,
            inkH: irect.height,
            samples: live.n + committed.n,
            near: live.near + committed.near,
            liveNear: live.near,
            pointerY: pointer.y,
            hit: hit ? (hit.id || hit.className || hit.tagName) : '',
          };
        }""",
        {"x": x + min(120, box["width"] * 0.25), "y": y},
    )
    page.mouse.up()  # type: ignore[attr-defined]
    page.wait_for_timeout(200)  # type: ignore[attr-defined]
    if not isinstance(metrics, dict) or metrics.get("error"):
        raise SystemExit(f"Ink probe failed: {metrics}")
    if abs(float(metrics["deltaY"])) > 2 or abs(float(metrics["deltaH"])) > 2:
        raise SystemExit(f"Live ink layer is not pinned to the board: {metrics}")
    if int(metrics["near"]) < 1:
        raise SystemExit(f"Live ink is not under the pointer: {metrics}")
    return metrics


def _mck272_opaque_count(page: object) -> int:
    """Count painted pixels on the student board (committed or live)."""
    count = page.evaluate(  # type: ignore[attr-defined]
        """() => {
          const canvas = document.getElementById('student-canvas');
          const stage = canvas && canvas.closest('.live-canvas-stage');
          const layers = [];
          if (canvas) layers.push(canvas);
          const ink = stage && stage.querySelector('canvas.live-canvas-ink');
          if (ink) layers.push(ink);
          let n = 0;
          for (const layer of layers) {
            const ctx = layer.getContext('2d');
            if (!ctx || layer.width < 2) continue;
            const data = ctx.getImageData(0, 0, layer.width, layer.height).data;
            for (let i = 3; i < data.length; i += 16) {
              if (data[i] > 24) n += 1;
            }
          }
          return n;
        }"""
    )
    return int(count or 0)


def prepare_mck272(path: Path) -> dict[str, object]:
    """Start a live class with two teammates and no whiteboard published yet.

    Visit tokens stay in the returned dict for navigation. Callers must not
    print them.

    Args:
        path: LMS sqlite path used by the running dev server.

    Returns:
        Class id, session id, and visit tokens keyed by roster name.
    """
    sys.path.insert(0, str(LMS_DIR))
    os.environ["LLOVES_DB"] = str(path)
    os.environ.setdefault("LOCAL_DEV_LOGIN", "1")
    from app import create_app

    app = create_app(db_path=path, testing=True)
    school = app.config["SCHOOL_DB"]
    class_id = shawn_class_id(path)
    teacher = school.get_user_by_email(staff_shawn_email(path))
    if teacher is None:
        raise SystemExit("Shawn staff user missing. Run with --seed first.")
    _mck208_end_active_live(school, class_id)
    try:
        school.game.end_game(class_id)
    except (KeyError, ValueError):
        pass
    live = school.start_live_class_session(class_id, int(teacher["id"]))
    session_id = int(live["id"])
    rows = {}
    for name in ("Maple", "Cedar", "Pine"):
        row = school.game.find_student_by_codename(class_id, name)
        if row is None:
            school.game.add_student(class_id, codename=name)
            row = school.game.find_student_by_codename(class_id, name)
        if row is None:
            raise SystemExit(f"Demo roster is missing {name}.")
        rows[name] = row
    school.game.begin_game(class_id)
    school.game.clear_team_lock(class_id)
    for row in rows.values():
        school.join_live_class_session(
            session_id,
            int(row["id"]),
            codename=str(row["codename"]),
        )
    school.setup_live_session_groups(
        session_id,
        n_teams=2,
        mode="manual",
        present_ids=[int(rows[name]["id"]) for name in ("Maple", "Cedar", "Pine")],
        assignments=[
            {"student_id": int(rows["Maple"]["id"]), "team_index": 0},
            {"student_id": int(rows["Cedar"]["id"]), "team_index": 0},
            {"student_id": int(rows["Pine"]["id"]), "team_index": 1},
        ],
    )
    school.ensure_live_session_items(session_id)
    school.set_live_session_teacher_state(
        session_id,
        class_set=True,
        run_as_group=True,
        groups_configured=True,
        active_tab="canvas",
    )
    tokens: dict[str, str] = {}
    for name, row in rows.items():
        found = school.conn.execute(
            """
            SELECT visit_token FROM live_session_attendees
            WHERE live_session_id = ? AND student_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (session_id, int(row["id"])),
        ).fetchone()
        tokens[name] = str(found["visit_token"] if found else "")
    return {
        "class_id": class_id,
        "session_id": session_id,
        "tokens": tokens,
    }


def publish_mck272_whiteboard(
    path: Path, session_id: int, *, mode: str = "group_shared"
) -> None:
    """Publish the session whiteboard to the group or to each student.

    Args:
        path: LMS sqlite path used by the running dev server.
        session_id: Active live class session.
        mode: ``group_shared`` or ``individual``.
    """
    sys.path.insert(0, str(LMS_DIR))
    os.environ["LLOVES_DB"] = str(path)
    os.environ.setdefault("LOCAL_DEV_LOGIN", "1")
    from app import create_app

    app = create_app(db_path=path, testing=True)
    school = app.config["SCHOOL_DB"]
    wb = next(
        row
        for row in school.list_live_session_items(session_id)
        if str(row.get("kind") or "") == "whiteboard"
    )
    school.publish_live_session_item(
        session_id, int(wb["id"]), publish_mode="group_shared"
    )
    school.set_live_session_teacher_state(
        session_id,
        class_set=True,
        run_as_group=True,
        groups_configured=True,
        canvas_align="team",
        active_tab="canvas",
    )


def mark_second_teammate_drew_and_left(path: Path, session_id: int) -> None:
    """Second student on the first team draws, then leaves.

    The shown legend should still list them. This does not print names
    or visit tokens.

    Args:
        path: LMS sqlite path used by the running dev server.
        session_id: Active live class session.
    """
    sys.path.insert(0, str(LMS_DIR))
    os.environ["LLOVES_DB"] = str(path)
    os.environ.setdefault("LOCAL_DEV_LOGIN", "1")
    from app import create_app

    app = create_app(db_path=path, testing=True)
    school = app.config["SCHOOL_DB"]
    rows = school.list_live_session_team_members(int(session_id), 0)
    ids = [
        int(row["student_id"])
        for row in rows
        if row.get("student_id") not in (None, "")
    ]
    if len(ids) < 2:
        raise SystemExit("The first team needs two students before a leave.")
    leaver = ids[1]
    school.append_live_board_ops(
        int(session_id),
        "team:0",
        owner=str(leaver),
        ops=[
            {
                "type": "text_upsert",
                "id": "kept-ink",
                "text": "kept",
                "x": 0.55,
                "y": 0.62,
            }
        ],
    )
    school.mark_live_session_attendee_left(int(session_id), student_id=leaver)


def shoot_mck272(base: str, out_dir: Path, path: Path, state_path: Path) -> list[Path]:
    """MCK-272: one board, hidden until publish, fitted, named, in sync.

    Args:
        base: Loopback origin.
        out_dir: Directory for PNG files.
        path: LMS sqlite path.
        state_path: Staff storageState.

    Returns:
        Written screenshot paths.
    """
    from playwright.sync_api import sync_playwright

    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    prepared = prepare_mck272(path)
    class_id = int(prepared["class_id"])
    session_id = int(prepared["session_id"])
    tokens = prepared["tokens"]
    if not isinstance(tokens, dict):
        raise SystemExit("Demo visit tokens were not prepared.")
    staff_url = f"{origin}/staff/class/{class_id}?tab=live"
    out_dir.mkdir(parents=True, exist_ok=True)
    shots: list[Path] = []
    gone = (
        "#live-canvas-stub",
        "#live-whiteboard-always-names",
        "#live-whiteboard-projector-names",
        "#wb-live-always-names",
    )
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        staff_ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            storage_state=str(state_path) if state_path.exists() else None,
        )
        staff = staff_ctx.new_page()
        staff.goto(staff_url, wait_until="domcontentloaded")
        staff.locator('.live-content-tab[data-tab="canvas"]').click(timeout=15000)
        staff.locator('[data-surface-publish="canvas"]').wait_for(state="visible", timeout=30000)
        staff.wait_for_timeout(2500)
        if staff.locator("#formative-live-board:not([hidden])").count():
            raise SystemExit("Whiteboard is visible before Publish.")
        for selector in gone:
            if staff.locator(selector).count():
                raise SystemExit(f"Removed control is still in the page: {selector}")
        before = out_dir / "mck272-before-publish-1280x800.png"
        staff.screenshot(path=str(before))
        shots.append(before)

        maple_ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        cedar_ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        maple = maple_ctx.new_page()
        cedar = cedar_ctx.new_page()
        _mck272_open_student(maple, origin, str(tokens["Maple"]))
        _mck272_open_student(cedar, origin, str(tokens["Cedar"]))
        maple.wait_for_timeout(1500)
        if maple.locator("#canvas-pane:not([hidden])").count():
            raise SystemExit("Student board is visible before Publish.")
        hidden_student = out_dir / "mck272-student-before-publish-1280x800.png"
        maple.screenshot(path=str(hidden_student))
        shots.append(hidden_student)

        publish_mck272_whiteboard(path, session_id)
        staff.locator("#formative-live-board:not([hidden]) .wb-live-name").first.wait_for(
            state="visible", timeout=30000
        )
        names = staff.locator("#formative-live-board .wb-live-name").all_inner_texts()
        labels = [part.strip() for part in names if part.strip()]
        if labels != ["Team 1", "Team 2"] and sorted(labels) != ["Team 1", "Team 2"]:
            raise SystemExit(f"Gallery labels are not the team names: {labels}")
        for label in labels:
            if label.startswith("Student "):
                raise SystemExit(f"Gallery used a placeholder name: {label}")
        gallery = out_dir / "mck272-gallery-1280x800.png"
        staff.screenshot(path=str(gallery))
        shots.append(gallery)

        opened = False
        for _ in range(4):
            tile = staff.locator("#formative-live-board .wb-live-tile").first
            tile.click(timeout=5000)
            try:
                staff.locator("#wb-live-panel:not([hidden]) #wb-live-undo").wait_for(
                    state="visible", timeout=4000
                )
                opened = True
                break
            except Exception:
                continue
        if not opened:
            raise SystemExit("Opening a gallery card did not show the board tools.")
        for selector in ("#wb-live-pen", "#wb-live-text", "#wb-live-erase", "#wb-live-undo"):
            _mck272_box_in_view(staff, selector)
        _mck272_assert_open_panel(staff, "team-1280x800")
        panel = out_dir / "mck272-panel-1280x800.png"
        staff.screenshot(path=str(panel))
        shots.append(panel)

        staff.set_viewport_size({"width": 1366, "height": 768})
        staff.wait_for_timeout(400)
        for selector in ("#wb-live-pen", "#wb-live-text", "#wb-live-erase", "#wb-live-undo"):
            _mck272_box_in_view(staff, selector)
        _mck272_assert_open_panel(staff, "team-1366x768")
        short = out_dir / "mck272-panel-1366x768.png"
        staff.screenshot(path=str(short))
        shots.append(short)

        maple.locator("#canvas-pane:not([hidden]) #student-canvas").wait_for(
            state="visible", timeout=30000
        )
        cedar.locator("#canvas-pane:not([hidden]) #student-canvas").wait_for(
            state="visible", timeout=30000
        )
        for student, width, height, tag in (
            (maple, 1280, 800, "1280x800"),
            (maple, 1366, 768, "1366x768"),
        ):
            student.set_viewport_size({"width": width, "height": height})
            student.wait_for_timeout(300)
            for selector in (
                "#student-canvas-pen",
                "#student-canvas-text",
                "#student-canvas-erase",
                "#student-canvas-undo",
            ):
                _mck272_box_in_view(student, selector)
            if tag == "1366x768":
                fitted = out_dir / f"mck272-student-toolbar-{tag}.png"
                student.screenshot(path=str(fitted))
                shots.append(fitted)
        maple.set_viewport_size({"width": 1280, "height": 800})
        cedar.set_viewport_size({"width": 1280, "height": 800})
        ink = _mck272_draw_and_measure(maple)
        print("mck272 ink", {key: ink[key] for key in ("deltaX", "deltaY", "deltaW", "deltaH", "near", "samples")})
        drawing = out_dir / "mck272-student-ink-1280x800.png"
        maple.screenshot(path=str(drawing))
        shots.append(drawing)
        synced = False
        for _ in range(12):
            if _mck272_opaque_count(cedar) > 0:
                synced = True
                break
            cedar.wait_for_timeout(1000)
        if not synced:
            raise SystemExit("Teammate board did not receive the shared stroke.")
        team = out_dir / "mck272-team-sync-1280x800.png"
        cedar.screenshot(path=str(team))
        shots.append(team)

        _mck272_republish(staff, origin, path, session_id, "individual")
        staff.set_viewport_size({"width": 1280, "height": 800})
        staff.reload(wait_until="domcontentloaded")
        staff.locator('.live-content-tab[data-tab="canvas"]').click(timeout=15000)
        staff.locator("#formative-live-board .wb-live-name").first.wait_for(
            state="visible", timeout=30000
        )
        individual_names = [
            part.strip()
            for part in staff.locator("#formative-live-board .wb-live-name").all_inner_texts()
            if part.strip()
        ]
        print("mck272 individual names", individual_names)
        maple_tile = staff.locator("#formative-live-board .wb-live-tile", has_text="Maple").first
        maple_tile.wait_for(state="visible", timeout=15000)
        maple_tile.click()
        staff.locator("#wb-live-panel:not([hidden]) #wb-live-undo").wait_for(
            state="visible", timeout=15000
        )
        for width, height, tag in ((1280, 800, "1280x800"), (1366, 768, "1366x768")):
            staff.set_viewport_size({"width": width, "height": height})
            staff.wait_for_timeout(400)
            for selector in ("#wb-live-pen", "#wb-live-text", "#wb-live-erase", "#wb-live-undo"):
                _mck272_box_in_view(staff, selector)
            _mck272_assert_open_panel(staff, f"individual-{tag}")
            panel_shot = out_dir / f"mck272-individual-panel-{tag}.png"
            staff.screenshot(path=str(panel_shot))
            shots.append(panel_shot)

        maple.set_viewport_size({"width": 1280, "height": 800})
        _mck272_open_student(maple, origin, str(tokens["Maple"]))
        maple.locator("#canvas-pane:not([hidden]) #student-canvas").wait_for(
            state="visible", timeout=30000
        )
        solo_ink = _mck272_draw_and_measure(maple)
        print(
            "mck272 solo ink",
            {key: solo_ink[key] for key in ("deltaX", "deltaY", "deltaW", "deltaH", "near", "samples")},
        )
        solo_shot = out_dir / "mck272-individual-ink-1280x800.png"
        maple.screenshot(path=str(solo_shot))
        shots.append(solo_shot)
        solo_keys: list[str] = []
        import sqlite3

        for _ in range(12):
            with sqlite3.connect(path) as conn:
                rows = conn.execute(
                    "SELECT DISTINCT board_key FROM board_ops"
                ).fetchall()
            keys = [str(row[0]) for row in rows]
            solo_keys = [key for key in keys if key.startswith("solo:")]
            banned = [key for key in keys if key.startswith("student:") or key.startswith("fb:")]
            if banned:
                raise SystemExit(f"Individual ink used a removed board key: {banned}")
            if solo_keys:
                break
            maple.wait_for_timeout(1000)
        if not solo_keys:
            raise SystemExit("Individual stroke did not save a solo: board key.")
        print("mck272 solo keys", solo_keys)
        staff.set_viewport_size({"width": 1280, "height": 800})
        staff.reload(wait_until="domcontentloaded")
        staff.locator('.live-content-tab[data-tab="canvas"]').click(timeout=15000)
        staff.locator("#formative-live-board .wb-live-tile", has_text="Maple").first.wait_for(
            state="visible", timeout=30000
        )
        staff.wait_for_timeout(1500)
        gallery_shot = out_dir / "mck272-individual-gallery-1280x800.png"
        staff.screenshot(path=str(gallery_shot))
        shots.append(gallery_shot)
        maple_ctx.close()
        cedar_ctx.close()
        staff_ctx.close()
        browser.close()
    return shots


def _mck272_republish(page: object, origin: str, path: Path, session_id: int, mode: str) -> None:
    """Ask the running server to publish the whiteboard again.

    A second process writing sqlite does not update the server's copy of
    the live item. This posts to the staff publish route instead.

    Args:
        page: Logged-in staff page. Its request context carries the cookie.
        origin: Loopback origin.
        path: LMS sqlite path.
        session_id: Active live class session.
        mode: ``individual`` or ``group_shared``.
    """
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            """
            SELECT id FROM live_session_items
            WHERE live_session_id = ? AND kind = 'whiteboard'
            ORDER BY id DESC LIMIT 1
            """,
            (int(session_id),),
        ).fetchone()
    if row is None:
        raise SystemExit("Whiteboard item is missing.")
    response = page.request.post(  # type: ignore[attr-defined]
        f"{origin}/api/live-sessions/{int(session_id)}/items/{int(row[0])}/publish",
        data=json.dumps({"publish_mode": mode}),
        headers={"Content-Type": "application/json"},
    )
    if response.status != 200:
        raise SystemExit(f"Republish {mode} failed with status {response.status}.")


def _mck273_publish_one_answer(school, session_id: int, module: str, slot: str, student_id: int) -> None:
    """Publish one individual item and one roster answer. Does not log codes."""
    now = "2026-10-14T13:20:00"
    conn = school.conn
    conn.execute(
        """
        UPDATE live_class_sessions
        SET teacher_state_json = ?
        WHERE id = ?
        """,
        (json.dumps({"live_module": module, "live_slot": slot}), int(session_id)),
    )
    prompt_id = conn.execute(
        """
        INSERT INTO live_session_prompts (
            live_session_id, slide_index, kind, payload, created_at, updated_at
        ) VALUES (?, 1, 'poll', '{}', ?, ?)
        """,
        (int(session_id), now, now),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO live_session_items (
            live_session_id, placement_key, item_id, stage, kind, item_json,
            prompt_id, status, publish_mode, response_mode, published_at,
            created_at, updated_at
        ) VALUES (?, 'q0', 'q0', 'round', 'poll', ?, ?, 'closed',
                  'individual', 'individual', ?, ?, ?)
        """,
        (
            int(session_id),
            json.dumps({"text": "q0", "type": "mc", "options": [{"text": "A"}]}),
            prompt_id,
            now,
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO live_session_responses (
            prompt_id, student_id, response_json, created_at, updated_at
        ) VALUES (?, ?, '{}', ?, ?)
        """,
        (int(prompt_id), int(student_id), now, now),
    )
    conn.commit()


def _mck273_end_with_answer(
    school, class_id: int, teacher_id: int, student_id: int, module: str, slot: str, *, is_test: bool
) -> str:
    """Start, publish one answer, and End. Return the live join code."""
    active = school.get_active_live_session_for_class(int(class_id))
    if active is not None:
        school.end_live_class_session(int(active["id"]))
    live = school.start_live_class_session(int(class_id), int(teacher_id), is_test=is_test)
    _mck273_publish_one_answer(school, int(live["id"]), module, slot, student_id)
    join_code = str(live.get("session_code") or "")
    school.finish_live_class(
        int(class_id),
        celebrate=True,
        live_run=True,
    )
    return join_code


def _mck273_stored_code(school, class_id: int, module: str, slot: str, *, is_test: bool) -> str:
    """Catch-up code stored for one slot. Empty when the row is missing."""
    row = school.conn.execute(
        """
        SELECT portal_join_code FROM catchup_links
        WHERE class_id = ? AND live_module = ? AND live_slot = ?
          AND COALESCE(is_test, 0) = ?
        """,
        (int(class_id), module, slot, 1 if is_test else 0),
    ).fetchone()
    if row is None:
        return ""
    return str(row["portal_join_code"] or "")


def _mck273_session_shaped_code() -> str:
    """A structured catch-up code that is also a legal live join code."""
    sys.path.insert(0, str(LMS_DIR))
    from catchup_portal_code import decode_portal_join_code, mint_portal_join_code
    from codes import SESSION_CODE_ALPHABET, is_offensive_code

    for module in ("M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9"):
        for slot in ("C2", "C3", "C4"):
            for _ in range(12):
                code = mint_portal_join_code("MCF3M", module, slot)
                if (
                    decode_portal_join_code(code) is not None
                    and all(ch in SESSION_CODE_ALPHABET for ch in code)
                    and not is_offensive_code(code)
                ):
                    return code
    raise SystemExit("could not mint a session-shaped catch-up code")


def _mck273_course_pin(school, class_id: int, student_id: int, blocked: str) -> str:
    """Set a 4-digit course PIN that is not the join code. Do not log it."""
    import secrets

    sys.path.insert(0, str(LMS_DIR))
    from codes import SESSION_CODE_ALPHABET

    digits = "".join(ch for ch in SESSION_CODE_ALPHABET if ch.isdigit())
    pin = blocked
    while pin == blocked or len(pin) != 4:
        pin = "".join(secrets.choice(digits) for _ in range(4))
    school.set_student_pin(int(class_id), int(student_id), pin, pin_set_by="teacher")
    school.conn.execute(
        "UPDATE classes SET require_catchup_pin_change = 0 WHERE id = ?",
        (int(class_id),),
    )
    school.conn.commit()
    return pin


def shoot_mck273(base: str, out_dir: Path, path: Path, widths: list[int]) -> list[Path]:
    """Start and end a class, then open catch-up with that same join code.

    Also checks rerun, collision, live-before-catch-up, test-class delete,
    and sign-out. Join codes and PINs are not written to the log or filenames.

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
    school = _mck208_school(path)
    class_id = shawn_class_id(path)
    teacher = school.get_user_by_email(SHAWN_EMAIL)
    student = school.game.find_student_by_codename(class_id, "Maple")
    if teacher is None or student is None:
        raise SystemExit("MCK-273 shoot needs Shawn's class and Maple. Run with --seed.")
    teacher_id = int(teacher["id"])
    student_id = int(student["id"])
    notes: list[str] = []

    join_code = _mck273_end_with_answer(
        school, class_id, teacher_id, student_id, "M8", "C2", is_test=False
    )
    stored = _mck273_stored_code(school, class_id, "M8", "C2", is_test=False)
    if not join_code or join_code != stored:
        raise SystemExit("catch-up code did not match the live join code")
    notes.append("end stores the live join code")

    pin = _mck273_course_pin(school, class_id, student_id, join_code)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    width = int(widths[0]) if widths else 1280
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()

        def _blocked(dialog) -> None:
            """Fail if the join form alerts instead of sending the code."""
            dialog.dismiss()
            raise SystemExit("join form blocked the code before the server saw it")

        page.on("dialog", _blocked)
        page.goto(f"{origin}/", wait_until="domcontentloaded")
        page.locator("#code").fill(join_code)
        page.locator("#student-name").fill("Maple")
        page.locator("form#student-code-panel button[type='submit']").click()
        page.wait_for_url("**/student/catchup**", timeout=30000)
        landing_pin = page.locator("#landing_pin")
        if landing_pin.count():
            landing_pin.fill(pin)
            page.locator(".catchup-landing button.cl-btn-primary").click()
        page.get_by_text("Pick a class to catch up on").wait_for(state="visible", timeout=30000)
        dashboard = out_dir / f"mck273-catchup-dashboard-{width}.png"
        page.screenshot(path=str(dashboard), full_page=True)
        written.append(dashboard)
        notes.append("same code opens the catch-up dashboard")

        page.goto(f"{origin}/student/catchup/sign-out", wait_until="domcontentloaded")
        page.locator("#code").wait_for(state="visible", timeout=30000)
        if "student_code=" in page.url or "code=" in page.url:
            raise SystemExit("sign-out URL still carries a join code")
        if page.locator("#code").input_value().strip():
            raise SystemExit("sign-out left a code in the join field")
        signed_out = out_dir / f"mck273-sign-out-landing-{width}.png"
        page.screenshot(path=str(signed_out), full_page=True)
        written.append(signed_out)
        notes.append("sign-out URL has no join code")
        context.close()

        replacement = _mck273_end_with_answer(
            school, class_id, teacher_id, student_id, "M8", "C2", is_test=False
        )
        stored_again = _mck273_stored_code(school, class_id, "M8", "C2", is_test=False)
        if replacement == join_code or replacement != stored_again:
            raise SystemExit("rerun did not replace the catch-up code")
        if school.resolve_catchup_link_by_portal_code(join_code) is not None:
            raise SystemExit("the replaced catch-up code still resolves")
        notes.append("rerun replaces the catch-up code")

        sys.path.insert(0, str(LMS_DIR))
        from codes import SESSION_CODE_ALPHABET, is_offensive_code, pick_session_code
        import secrets

        calls = {"n": 0}

        def generate(length: int) -> str:
            """Offer the open catch-up code once, then a different code."""
            calls["n"] += 1
            if calls["n"] == 1:
                return stored_again
            while True:
                nxt = "".join(secrets.choice(SESSION_CODE_ALPHABET) for _ in range(int(length)))
                if nxt != stored_again and not is_offensive_code(nxt):
                    return nxt

        minted = pick_session_code(
            lambda candidate: school.catchup_join_code_reserved(candidate)
            or school.active_session_code_taken(candidate)
            or school.class_live_code_taken(candidate),
            school.count_active_live_sessions(),
            generate=generate,
        )
        if minted == stored_again or calls["n"] < 2:
            raise SystemExit("join code collided with an open catch-up code")
        notes.append("open catch-up code is not reissued")

        live = school.start_live_class_session(class_id, teacher_id, is_test=False)
        shaped = _mck273_session_shaped_code()
        school.conn.execute(
            "UPDATE live_class_sessions SET session_code = ? WHERE id = ?",
            (shaped, int(live["id"])),
        )
        school.conn.commit()
        live_context = browser.new_context(viewport={"width": width, "height": 900})
        live_page = live_context.new_page()
        live_page.goto(f"{origin}/", wait_until="domcontentloaded")
        live_page.locator("#code").fill(shaped)
        live_page.locator("#student-name").fill("Maple")
        live_page.locator("form#student-code-panel button[type='submit']").click()
        live_page.wait_for_url("**/student/mood**", timeout=30000)
        if "/student/catchup" in live_page.url:
            raise SystemExit("live join code was treated as catch-up")
        live_page.locator("#mood-form").wait_for(state="visible", timeout=30000)
        mood = out_dir / f"mck273-live-code-wins-{width}.png"
        live_page.screenshot(path=str(mood), full_page=True)
        written.append(mood)
        notes.append("live join code wins at sign-in")
        live_context.close()
        school.end_live_class_session(int(live["id"]))

        test_code = _mck273_end_with_answer(
            school, class_id, teacher_id, student_id, "M8", "C3", is_test=True
        )
        test_stored = _mck273_stored_code(school, class_id, "M8", "C3", is_test=True)
        if not test_code or test_code != test_stored:
            raise SystemExit("test-class catch-up code did not match")
        if school.resolve_catchup_link_by_portal_code(test_code) is None:
            raise SystemExit("test-class code did not resolve")
        session_row = school.conn.execute(
            """
            SELECT id FROM live_class_sessions
            WHERE class_id = ? AND COALESCE(is_test, 0) = 1
            ORDER BY id DESC LIMIT 1
            """,
            (class_id,),
        ).fetchone()
        school.delete_test_live_class(class_id, int(session_row["id"]))
        if school.resolve_catchup_link_by_portal_code(test_code) is not None:
            raise SystemExit("deleting the test class left the code working")
        notes.append("deleting the test class revokes the code")
        browser.close()

    log_path = out_dir / "mck273-checks.log"
    log_path.write_text("\n".join(notes) + "\n", encoding="utf-8")
    written.append(log_path)
    return written


def _mck281_blank_identity(page) -> None:
    """Hide join codes and roster names, and stop the live poll before a shot.

    CSS hides identity text. Timers are cleared so a poll cannot replace the
    card while Playwright is capturing it. The function does not write into
    nodes the poll owns.
    """
    page.evaluate(
        """() => {
          const style = document.createElement("style");
          style.setAttribute("data-mck281-shot", "1");
          style.textContent = [
            "#ap-join-billboard-code, #ap-join-billboard { font-size: 0 !important; color: transparent !important; }",
            ".live-group-setup-teams, #me-name, #me-team-list, .student-group-strip,",
            ".me-team-list, .rank-turn-by, .group-submit-last { display: none !important; }",
            "*, *::before, *::after { animation: none !important; transition: none !important; }",
          ].join("");
          document.head.appendChild(style);
          document.title = "Class";
          const newest = window.setTimeout(() => {}, 0);
          for (let id = 0; id <= newest; id += 1) {
            window.clearTimeout(id);
            window.clearInterval(id);
          }
          window.setTimeout = () => 0;
          window.setInterval = () => 0;
        }"""
    )


def _mck281_shot(locator, path: Path) -> None:
    """Screenshot one locator after the live poll has been stopped.

    Args:
        locator: Playwright locator for the card.
        path: PNG path.
    """
    last = None
    page = locator.page
    for _ in range(4):
        try:
            locator.wait_for(state="visible", timeout=15000)
            locator.evaluate(
                "el => el.scrollIntoView({block: 'center', inline: 'nearest'})"
            )
            page.wait_for_timeout(200)
            box = locator.bounding_box()
            vp = page.viewport_size or {"width": 1280, "height": 900}
            if not box or box["width"] < 8 or box["height"] < 8:
                raise RuntimeError("rank card has no box")
            x = max(0, box["x"])
            y = max(0, box["y"])
            right = min(float(vp["width"]), box["x"] + box["width"])
            bottom = min(float(vp["height"]), box["y"] + box["height"])
            if right - x < 8 or bottom - y < 8:
                raise RuntimeError("rank card is off screen")
            page.screenshot(
                path=str(path),
                clip={"x": x, "y": y, "width": right - x, "height": bottom - y},
            )
            return
        except Exception as exc:
            last = exc
            page.wait_for_timeout(400)
    raise SystemExit("could not capture the rank card") from last


def prepare_mck281_teacher(path: Path, origin: str) -> dict[str, int | str]:
    """Save one unpublished group rank as together and return the teacher URL.

    The stored ``group_rank_mode`` stays ``together``. The questions tab
    should show Take turns with no Rank together control.

    Args:
        path: Sqlite path the dev server uses.
        origin: Loopback LMS origin.

    Returns:
        ``class_id``, ``session_id``, ``live_item_id``, and ``teacher_url``.
    """
    school = _mck208_school(path)
    class_id = shawn_class_id(path)
    teacher = school.get_user_by_email(SHAWN_EMAIL)
    if teacher is None:
        raise SystemExit("Shawn staff user missing. Run with --seed first.")
    maple = school.game.find_student_by_codename(class_id, "Maple")
    cedar = school.game.find_student_by_codename(class_id, "Cedar")
    if maple is None or cedar is None:
        raise SystemExit("Demo roster missing. Run with --seed first.")
    _mck208_end_active_live(school, class_id)
    try:
        school.game.end_game(class_id)
    except (KeyError, ValueError):
        pass
    school.game.clear_team_lock(class_id)
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
    maple_id = int(maple["id"])
    cedar_id = int(cedar["id"])
    school.setup_live_session_groups(
        session_id,
        n_teams=2,
        mode="manual",
        present_ids=[maple_id, cedar_id],
        assignments=[
            {"student_id": maple_id, "team_index": 0},
            {"student_id": cedar_id, "team_index": 1},
        ],
    )
    membership = school.game.team_membership_index(class_id).get("by_student") or {}
    if maple_id not in membership or cedar_id not in membership:
        raise SystemExit("demo roster is not on a team")
    school.ensure_live_session_items(session_id)
    placement = school.add_staff_question_to_class_playlist(
        class_id,
        "M1",
        "C1",
        question_type="rank",
        text="Rank these representations.",
        page_number=4,
        stage="round",
        options=["A graph", "A table", "An equation", "Two points"],
    )
    items = school.ensure_live_session_items(session_id)
    row = next(item for item in items if item["item_id"] == placement["item_id"])
    live_item_id = int(row["id"])
    school.update_live_session_item_settings(
        session_id,
        live_item_id,
        group_rank_mode="together",
        publish_mode="group_submit",
    )
    saved = school.get_live_session_item(session_id, live_item_id)
    if saved["item"].get("group_rank_mode") != "together":
        raise SystemExit("saved rank mode was rewritten before open")
    base = assert_loopback(origin if "://" in origin else f"http://{origin}").rstrip("/")
    return {
        "class_id": class_id,
        "session_id": session_id,
        "live_item_id": live_item_id,
        "teacher_url": f"{base}/staff/class/{class_id}?tab=live&live_session_id={session_id}",
    }


def _mck281_seat_on_team(school, class_id: int) -> None:
    """Seat the demo student on a team after the local-dev seed.

    That seed removes one roster row from ``game_memberships`` on every
    app boot. A group rank card only runs Take turns for a student who
    is on a team.

    Args:
        school: SchoolDB bound to the dev sqlite.
        class_id: Demo class id.
    """
    primary = school.game.find_student_by_codename(class_id, "Maple")
    partner = school.game.find_student_by_codename(class_id, "Cedar")
    if primary is None or partner is None:
        raise SystemExit("Demo roster missing. Run with --seed first.")
    primary_id = int(primary["id"])
    if school.student_team_id_for_class(class_id, primary_id) is not None:
        return
    partner_id = int(partner["id"])
    team_id = school.student_team_id_for_class(class_id, partner_id)
    if team_id is None:
        raise SystemExit("demo roster is not on a team")
    game = school.game
    with game._lock:
        game_row = game.conn.execute(
            "SELECT id FROM games WHERE class_id = ? AND status != 'ended'",
            (int(class_id),),
        ).fetchone()
        if game_row is None:
            raise SystemExit("demo roster is not on a team")
        game.conn.execute(
            """
            INSERT OR IGNORE INTO game_memberships (game_id, team_id, student_id)
            VALUES (?, ?, ?)
            """,
            (int(game_row["id"]), int(team_id), primary_id),
        )
        game.conn.commit()
    cache = getattr(game, "_team_index_cache", None)
    if isinstance(cache, dict):
        cache.pop(int(class_id), None)


def _mck281_student_url(path: Path, origin: str, session_id: int, live_item_id: int) -> str:
    """Publish the saved rank and return one student's live URL.

    Publish leaves the stored together token in place.

    Args:
        path: Sqlite path the dev server uses.
        origin: Loopback LMS origin.
        session_id: Active live session.
        live_item_id: Unpublished rank lifecycle id.
    """
    sys.path.insert(0, str(LMS_DIR))
    os.environ["LLOVES_DB"] = str(path)
    os.environ["LOCAL_DEV_LOGIN"] = "1"
    from app import create_app

    app = create_app(db_path=path, testing=True)
    school = app.config["SCHOOL_DB"]
    class_id = int(school.get_live_session(session_id)["class_id"])
    _mck281_seat_on_team(school, class_id)
    school.publish_live_session_item(session_id, live_item_id, publish_mode="group_submit")
    saved = school.get_live_session_item(session_id, live_item_id)
    if saved["item"].get("group_rank_mode") != "together":
        raise SystemExit("publish rewrote the saved rank mode")
    if school._group_rank_mode(saved) != "turns":
        raise SystemExit("saved rank did not open as take turns")
    class_id = int(school.get_live_session(session_id)["class_id"])
    live = school.get_live_session(session_id)
    student = school.game.find_student_by_codename(class_id, "Maple")
    if student is None or live is None:
        raise SystemExit("Demo roster missing. Run with --seed first.")
    code = str(live.get("session_code") or "")
    client = app.test_client()
    client.post("/auth/student-code", data={"code": code, "name": "Maple"}, follow_redirects=True)
    client.post("/student/mood", data={"mood": "good"}, follow_redirects=True)
    client.post("/student/character", data={"character": "fox"}, follow_redirects=True)
    attendees = school.list_live_session_attendees(session_id)
    visit = str(next(row["visit_token"] for row in attendees if row.get("codename") == "Maple"))
    base = assert_loopback(origin if "://" in origin else f"http://{origin}").rstrip("/")
    return f"{base}/student/s/{visit}"


def shoot_mck281(base: str, state_path: Path, out_dir: Path, path: Path) -> list[Path]:
    """MCK-281: teacher group setup and the student Take turns card.

    A question saved as Rank together is opened as Take turns. Shots hide
    roster names and the join code.

    Args:
        base: Loopback LMS origin.
        state_path: Shawn's Playwright storageState.
        out_dir: Artifact directory.
        path: LMS sqlite path.

    Returns:
        PNG paths and a short checks log.
    """
    from playwright.sync_api import sync_playwright

    meta = prepare_mck281_teacher(path, base)
    teacher_url = str(meta["teacher_url"])
    out_dir.mkdir(parents=True, exist_ok=True)
    notes = [
        "teacher card shows take-turns style line",
        "teacher card has no Rank together control",
        "student card instructs Take turns",
        "student card has no Rank together line",
    ]
    written: list[Path] = []
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(
            storage_state=str(state_path), viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        page.goto(teacher_url, wait_until="domcontentloaded", timeout=60000)
        page.locator("body.staff-shell").wait_for(state="attached", timeout=30000)
        page.locator('.live-content-tab[data-tab="questions"]').click(timeout=10000)
        card = page.locator(
            f'.live-question-card[data-live-item-id="{int(meta["live_item_id"])}"]'
        )
        card.wait_for(state="visible", timeout=30000)
        card.locator(".live-group-setup-style").wait_for(state="visible", timeout=15000)
        setup_text = card.inner_text()
        if "Each person places one item, then passes." not in setup_text:
            raise SystemExit("teacher card did not show the take-turns line")
        if "Rank together" in setup_text:
            raise SystemExit("teacher card still shows Rank together")
        _mck281_blank_identity(page)
        page.wait_for_timeout(600)
        teacher_shot = out_dir / "mck281-teacher-setup-1280.png"
        setup = card.locator(".live-q-strip")
        _mck281_shot(setup if setup.count() else card, teacher_shot)
        written.append(teacher_shot)
        context.close()

        student_url = _mck281_student_url(
            path, base, int(meta["session_id"]), int(meta["live_item_id"])
        )
        student = browser.new_context(viewport={"width": 390, "height": 900})
        phone = student.new_page()
        try:
            phone.goto(student_url, wait_until="domcontentloaded", timeout=60000)
            phone.locator('[data-group-instruction="rank_turns"]').wait_for(
                state="visible", timeout=30000
            )
            phone.locator("[data-rank-turns-card]").wait_for(state="visible", timeout=15000)
        except Exception:
            raise SystemExit("student card did not show Take turns") from None
        phone_text = phone.locator("#live-response").inner_text()
        if "Take turns. Each person places one item, then passes." not in phone_text:
            raise SystemExit("student instruction is not Take turns")
        if "Rank together" in phone_text:
            raise SystemExit("student card still shows Rank together")
        _mck281_blank_identity(phone)
        phone.wait_for_timeout(600)
        student_shot = out_dir / "mck281-student-turns-390.png"
        _mck281_shot(phone.locator("#live-response"), student_shot)
        written.append(student_shot)
        student.close()
        browser.close()
    log_path = out_dir / "mck281-checks.log"
    log_path.write_text("\n".join(notes) + "\n", encoding="utf-8")
    written.append(log_path)
    return written


def prepare_mck293(path: Path, origin: str) -> dict[str, int | str]:
    """Save one keyed group rank and return the teacher engagement URL.

    The answer order stays on the server. This does not print join codes
    or roster names.

    Args:
        path: Sqlite path the dev server uses.
        origin: Loopback LMS origin.

    Returns:
        ``class_id``, ``session_id``, ``live_item_id``, and ``teacher_url``.
    """
    school = _mck208_school(path)
    class_id = shawn_class_id(path)
    teacher = school.get_user_by_email(SHAWN_EMAIL)
    if teacher is None:
        raise SystemExit("Shawn staff user missing. Run with --seed first.")
    primary = school.game.find_student_by_codename(class_id, "Maple")
    partner = school.game.find_student_by_codename(class_id, "Cedar")
    if primary is None or partner is None:
        raise SystemExit("Demo roster missing. Run with --seed first.")
    _mck208_end_active_live(school, class_id)
    try:
        school.game.end_game(class_id)
    except (KeyError, ValueError):
        pass
    school.game.clear_team_lock(class_id)
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
    primary_id = int(primary["id"])
    partner_id = int(partner["id"])
    school.setup_live_session_groups(
        session_id,
        n_teams=2,
        mode="manual",
        present_ids=[primary_id, partner_id],
        assignments=[
            {"student_id": primary_id, "team_index": 0},
            {"student_id": partner_id, "team_index": 1},
        ],
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
        options=["A graph", "A table", "An equation", "Two points"],
        rank_key=[0, 1, 2, 3],
    )
    items = school.ensure_live_session_items(session_id)
    row = next(item for item in items if item["item_id"] == placement["item_id"])
    live_item_id = int(row["id"])
    school.update_live_session_item_settings(
        session_id,
        live_item_id,
        group_rank_mode="together",
        publish_mode="group_submit",
    )
    base = assert_loopback(origin if "://" in origin else f"http://{origin}").rstrip("/")
    return {
        "class_id": class_id,
        "session_id": session_id,
        "live_item_id": live_item_id,
        "student_id": primary_id,
        "teacher_url": f"{base}/staff/class/{class_id}?tab=ap&view=engagement",
    }


def _mck293_arm(path: Path, origin: str, meta: dict[str, int | str]) -> dict[str, int | str]:
    """Publish the rank, place every spot but the last, and return a visit URL.

    The last spot is left open so Check our ranking scores an unsent draft.
    The visit URL is for the browser only.

    Args:
        path: Sqlite path the dev server uses.
        origin: Loopback LMS origin.
        meta: Ids from :func:`prepare_mck293`.
    """
    sys.path.insert(0, str(LMS_DIR))
    os.environ["LLOVES_DB"] = str(path)
    os.environ["LOCAL_DEV_LOGIN"] = "1"
    from app import create_app

    app = create_app(db_path=path, testing=True)
    school = app.config["SCHOOL_DB"]
    session_id = int(meta["session_id"])
    live_item_id = int(meta["live_item_id"])
    class_id = int(meta["class_id"])
    try:
        _mck281_seat_on_team(school, class_id)
        school.publish_live_session_item(session_id, live_item_id, publish_mode="group_submit")
        item = school.get_live_session_item(session_id, live_item_id)
        order = [str(opt) for opt in (school._rank_race_key(item) or [])]
        if len(order) < 2:
            raise SystemExit("rank question has no answer order")
        student_id = int(meta["student_id"])
        for option_id in order[:-1]:
            school.rank_turn_place(
                session_id, live_item_id, student_id, option_id=option_id
            )
        live = school.get_live_session(session_id)
        student = school.game.find_student_by_codename(class_id, "Maple")
        if student is None or live is None:
            raise SystemExit("Demo roster missing. Run with --seed first.")
        client = app.test_client()
        client.post(
            "/auth/student-code",
            data={"code": str(live.get("session_code") or ""), "name": "Maple"},
            follow_redirects=True,
        )
        client.post("/student/mood", data={"mood": "good"}, follow_redirects=True)
        client.post("/student/character", data={"character": "fox"}, follow_redirects=True)
        attendees = school.list_live_session_attendees(session_id)
        visit = str(next(row["visit_token"] for row in attendees if row.get("codename") == "Maple"))
    finally:
        school.close()
    base = assert_loopback(origin if "://" in origin else f"http://{origin}").rstrip("/")
    return {
        "visit": f"{base}/student/s/{visit}",
        "last_option_id": order[-1],
    }


def _mck293_finish(path: Path, meta: dict[str, int | str], last_option_id: str) -> None:
    """Send the checked rank, send a second rank with no check, then End.

    The first send used the check, so it stores 1. The second matches the
    answer order with no check, so it stores 2. Catch-up is not offered
    the check.

    Args:
        path: Sqlite path the dev server uses.
        meta: Ids from :func:`prepare_mck293`.
        last_option_id: The unsent last spot of the checked rank.
    """
    sys.path.insert(0, str(LMS_DIR))
    os.environ["LLOVES_DB"] = str(path)
    # A fresh app with local-dev login reseeds and takes the student off
    # the team. This helper only writes the send, so leave that seed off.
    os.environ["LOCAL_DEV_LOGIN"] = "0"
    from app import create_app

    app = create_app(db_path=path, testing=True)
    school = app.config["SCHOOL_DB"]
    session_id = int(meta["session_id"])
    class_id = int(meta["class_id"])
    student_id = int(meta["student_id"])
    try:
        _mck281_seat_on_team(school, class_id)
        school.rank_turn_place(
            session_id,
            int(meta["live_item_id"]),
            student_id,
            option_id=last_option_id,
        )
        placement = school.add_staff_question_to_class_playlist(
            class_id,
            "M1",
            "C1",
            question_type="rank",
            text="Rank these representations again.",
            page_number=5,
            stage="round",
            options=["A graph", "A table", "An equation", "Two points"],
            rank_key=[0, 1, 2, 3],
        )
        items = school.ensure_live_session_items(session_id)
        row = next(item for item in items if item["item_id"] == placement["item_id"])
        second_id = int(row["id"])
        school.update_live_session_item_settings(
            session_id,
            second_id,
            group_rank_mode="together",
            publish_mode="group_submit",
        )
        school.publish_live_session_item(session_id, second_id, publish_mode="group_submit")
        second = school.get_live_session_item(session_id, second_id)
        order = [str(opt) for opt in (school._rank_race_key(second) or [])]
        if len(order) < 2:
            raise SystemExit("second rank has no answer order")
        for option_id in order:
            school.rank_turn_place(session_id, second_id, student_id, option_id=option_id)
        dash = school.student_dashboard_payload(class_id, student_id)
        module = str(dash.get("current_module") or "M1")
        school.set_live_session_teacher_state(
            session_id, live_module=module, live_slot="C1", stage="round"
        )
        school.close_live_class_for_celebration(class_id)
    finally:
        school.close()


def _mck293_hide_people(page) -> None:
    """Hide roster names and the document title before a points screenshot."""
    page.evaluate(
        """() => {
          const style = document.createElement("style");
          style.textContent = [
            ".sd-codename, .ap-engagement-name, #me-name { font-size: 0 !important; color: transparent !important; }",
            ".ap-engagement-override { display: none !important; }",
          ].join("");
          document.head.appendChild(style);
          document.title = "Class";
        }"""
    )


def _mck293_open_dashboard(page, origin: str, path: Path, class_id: int, student_id: int) -> None:
    """PIN sign-in, then the student dashboard for the student who just sent.

    The live visit cookie is not the dashboard session. The PIN is set for
    this shot and is not written to the log.

    Args:
        page: Playwright page that may still be on the live card.
        origin: Loopback LMS origin.
        path: LMS sqlite path.
        class_id: Demo class id.
        student_id: Roster id that should show the points.
    """
    sys.path.insert(0, str(LMS_DIR))
    os.environ["LLOVES_DB"] = str(path)
    os.environ["LOCAL_DEV_LOGIN"] = "0"
    from app import create_app

    app = create_app(db_path=path, testing=True)
    school = app.config["SCHOOL_DB"]
    pin = "2468"
    try:
        with school.game._lock:
            student = school.game.conn.execute(
                "SELECT first_name, codename FROM students WHERE id = ? AND class_id = ?",
                (int(student_id), int(class_id)),
            ).fetchone()
        if student is None:
            raise SystemExit("demo student missing for the dashboard")
        name = str(student["first_name"] or student["codename"] or "").strip()
        school.set_student_pin(int(class_id), int(student_id), pin, pin_set_by="teacher")
        link = school.conn.execute(
            """
            SELECT portal_join_code FROM catchup_links
            WHERE class_id = ? AND COALESCE(is_open, 0) = 1
              AND TRIM(COALESCE(portal_join_code, '')) <> ''
            ORDER BY id DESC LIMIT 1
            """,
            (int(class_id),),
        ).fetchone()
        if link is None or not name:
            raise SystemExit("dashboard sign-in is missing a class code")
        code = str(link["portal_join_code"])
    finally:
        school.close()
    page.context.clear_cookies()
    _catchup_landing_signin_via_portal(page, origin, code, name, pin=pin)
    page.goto(f"{origin}/student/dashboard", wait_until="domcontentloaded", timeout=60000)


def shoot_mck293(base: str, state_path: Path, out_dir: Path, path: Path) -> list[Path]:
    """MCK-293: check count, student group points, teacher group points.

    Shots hide roster names. The check line is a count only.

    Args:
        base: Loopback LMS origin.
        state_path: Shawn's Playwright storageState.
        out_dir: Artifact directory.
        path: LMS sqlite path.

    Returns:
        PNG paths.
    """
    from playwright.sync_api import sync_playwright

    meta = prepare_mck293(path, base)
    armed = _mck293_arm(path, base, meta)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        student = browser.new_context(viewport={"width": 390, "height": 900})
        phone = student.new_page()
        try:
            phone.goto(str(armed["visit"]), wait_until="domcontentloaded", timeout=60000)
            phone.locator("[data-rank-check]").wait_for(state="visible", timeout=30000)
            phone.locator("[data-rank-check]").click()
            phone.locator(".rank-check-result").wait_for(state="visible", timeout=15000)
        except Exception:
            raise SystemExit("check feedback did not appear") from None
        feedback = phone.locator(".rank-check-result").inner_text()
        if "in the right spot" not in feedback:
            raise SystemExit("check feedback was not a count")
        if phone.locator("[data-rank-check]").count():
            raise SystemExit("check stayed available after one use")
        _mck281_blank_identity(phone)
        phone.wait_for_timeout(400)
        check_shot = out_dir / "mck293-check-feedback-390.png"
        _mck281_shot(phone.locator("#live-response"), check_shot)
        written.append(check_shot)

        _mck293_finish(path, meta, str(armed["last_option_id"]))
        try:
            _mck293_open_dashboard(
                phone, origin, path, int(meta["class_id"]), int(meta["student_id"])
            )
            phone.locator("#sd-group-engagement").wait_for(state="visible", timeout=30000)
        except Exception:
            raise SystemExit("student dashboard did not show group points") from None
        points = phone.locator("#sd-group-engagement").inner_text().strip()
        if points in {"", "0/0"}:
            raise SystemExit("student dashboard group points were empty")
        _mck293_hide_people(phone)
        phone.wait_for_timeout(300)
        dash_shot = out_dir / "mck293-student-dashboard-390.png"
        phone.screenshot(path=str(dash_shot), full_page=True)
        written.append(dash_shot)
        student.close()

        context = browser.new_context(
            storage_state=str(state_path), viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        try:
            page.goto(str(meta["teacher_url"]), wait_until="domcontentloaded", timeout=60000)
            page.locator("body.staff-shell").wait_for(state="attached", timeout=30000)
            page.get_by_text("Group engagement").first.wait_for(state="visible", timeout=30000)
        except Exception:
            raise SystemExit("teacher engagement view did not show group points") from None
        _mck293_hide_people(page)
        page.wait_for_timeout(300)
        teacher_shot = out_dir / "mck293-teacher-engagement-1280.png"
        page.screenshot(path=str(teacher_shot), full_page=True)
        written.append(teacher_shot)
        context.close()
        browser.close()
    return written


def prepare_mck289(path: Path) -> int:
    """Show one open catch-up, one closed, and unrun rows as none.

    Updates rows by lesson slot. Does not read or print join codes.

    Args:
        path: LMS sqlite path.

    Returns:
        Shawn's class id.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    class_id = shawn_class_id(path)
    future = (
        datetime.now(tz=ZoneInfo("America/Toronto")).replace(microsecond=0)
        + timedelta(days=7)
    ).isoformat(timespec="seconds")
    con = sqlite3.connect(path)
    try:
        con.execute(
            """
            UPDATE catchup_links
            SET is_open = 1, open_until = ?
            WHERE class_id = ? AND live_module = 'M1' AND live_slot = 'C1'
              AND COALESCE(is_test, 0) = 0
            """,
            (future, class_id),
        )
        con.execute(
            """
            UPDATE catchup_links
            SET is_open = 0
            WHERE class_id = ? AND live_module = 'M1' AND live_slot = 'C2'
              AND COALESCE(is_test, 0) = 0
            """,
            (class_id,),
        )
        con.commit()
    finally:
        con.close()
    return class_id


def shoot_mck289(base: str, state_path: Path, out_dir: Path, path: Path, widths: list[int]) -> list[Path]:
    """Screenshot the Lesson Slides catch-up column. Join codes are masked first.

    Args:
        base: Loopback origin.
        state_path: Shawn's storageState.
        out_dir: Artifact directory.
        path: LMS sqlite path.
        widths: Viewport widths.

    Returns:
        PNG paths.
    """
    from playwright.sync_api import sync_playwright

    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    class_id = prepare_mck289(path)
    url = assert_loopback(f"{origin}/staff/class/{class_id}?tab=lesson-slides")
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
            page.goto(url, wait_until="domcontentloaded")
            page.locator("#ls-catchup-days").wait_for(state="visible", timeout=30000)
            page.locator(".ls-catchup-label").first.wait_for(state="visible", timeout=30000)
            labels = page.locator(".ls-catchup-label").all_inner_texts()
            if not any(text.startswith("Open until ") for text in labels):
                raise SystemExit("Catch-up column is missing an open row")
            if "Closed" not in labels:
                raise SystemExit("Catch-up column is missing a closed row")
            if "none" not in labels:
                raise SystemExit("Catch-up column is missing a none row")
            card = page.locator(".lesson-slides-table-card")
            if int(width) < 800:
                card.locator(".table-wrap").evaluate(
                    "el => { el.scrollLeft = el.scrollWidth; }"
                )
            # Accessible names are "Close catch-up for …", so match the label text.
            card.locator(".ls-catchup-toggle", has_text="Close").first.wait_for(
                state="visible", timeout=15000
            )
            card.locator(".ls-catchup-toggle", has_text="Reopen").first.wait_for(
                state="visible", timeout=15000
            )
            page.locator(".ls-join-code").evaluate_all(
                "nodes => nodes.forEach((node) => { node.textContent = '\\u2014'; })"
            )
            dest = out_dir / f"mck289-lesson-slides-catchup-{width}.png"
            page.locator(".lesson-slides-table-card").screenshot(path=str(dest))
            written.append(dest)
            context.close()
        browser.close()
    return written


def prepare_mck290(path: Path) -> int:
    """Show one ended template class and one deletable test entry.

    The live session has a real participation run and no stored Lesson
    Slides row. The test entry is a stored test row with its own session.
    Placeholder session codes are masked before the screenshot. This does
    not print codes or student names.

    Args:
        path: LMS sqlite path.

    Returns:
        Shawn's class id.
    """
    class_id = shawn_class_id(path)
    con = sqlite3.connect(path)
    try:
        cols = {str(row[1]) for row in con.execute("PRAGMA table_info(live_class_sessions)")}
        owner = con.execute(
            "SELECT offering_id, teacher_user_id FROM classes WHERE id = ?",
            (class_id,),
        ).fetchone()
        if owner is None or owner[0] in (None, ""):
            raise SystemExit("Demo class has no offering.")
        con.execute(
            """
            DELETE FROM lesson_slides_latest
            WHERE class_id = ? AND live_module = 'M1' AND live_slot = 'C4'
              AND COALESCE(is_test, 0) = 0
            """,
            (class_id,),
        )
        if "teacher_state_json" in cols:
            con.execute(
                """
                DELETE FROM live_class_sessions
                WHERE class_id = ? AND status = 'ended'
                  AND json_extract(teacher_state_json, '$.live_module') = 'M1'
                  AND json_extract(teacher_state_json, '$.live_slot') = 'C4'
                """,
                (class_id,),
            )
        fields = [
            "class_id",
            "offering_id",
            "teacher_user_id",
            "session_code",
            "status",
            "started_at",
            "ended_at",
            "is_test",
        ]
        values: list[object] = [
            class_id,
            int(owner[0]),
            int(owner[1]),
            "ended-run",
            "ended",
            "2026-10-19T18:00:00",
            "2026-10-19T19:15:00",
            0,
        ]
        if "teacher_state_json" in cols:
            fields.append("teacher_state_json")
            values.append(json.dumps({"live_module": "M1", "live_slot": "C4"}))
        if "run_key" in cols:
            fields.append("run_key")
            values.append("mck290-ended")
        con.execute(
            f"INSERT INTO live_class_sessions ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
            values,
        )
        run_cols = {str(row[1]) for row in con.execute("PRAGMA table_info(participation_runs)")}
        if "run_key" in run_cols and "is_real" in run_cols:
            con.execute(
                "DELETE FROM participation_runs WHERE class_id = ? AND run_key = ?",
                (class_id, "mck290-ended"),
            )
            run_fields = ["class_id", "run_key", "is_real"]
            run_values: list[object] = [class_id, "mck290-ended", 1]
            if "discarded" in run_cols:
                run_fields.append("discarded")
                run_values.append(0)
            if "auto_is_real" in run_cols:
                run_fields.append("auto_is_real")
                run_values.append(1)
            if "ended_at" in run_cols:
                run_fields.append("ended_at")
                run_values.append("2026-10-19T19:15:00")
            con.execute(
                f"INSERT INTO participation_runs ({', '.join(run_fields)}) "
                f"VALUES ({', '.join('?' for _ in run_fields)})",
                run_values,
            )
        if "run_key" in cols:
            con.execute(
                "DELETE FROM live_class_sessions WHERE class_id = ? AND run_key = ?",
                (class_id, "mck290-test"),
            )
        test_fields = [
            "class_id",
            "offering_id",
            "teacher_user_id",
            "session_code",
            "status",
            "started_at",
            "ended_at",
            "is_test",
        ]
        test_values: list[object] = [
            class_id,
            int(owner[0]),
            int(owner[1]),
            "test-entry",
            "ended",
            "2026-10-19T19:16:00",
            "2026-10-19T19:20:00",
            1,
        ]
        if "teacher_state_json" in cols:
            test_fields.append("teacher_state_json")
            test_values.append(json.dumps({"live_module": "M1", "live_slot": "C1"}))
        if "run_key" in cols:
            test_fields.append("run_key")
            test_values.append("mck290-test")
        inserted = con.execute(
            f"INSERT INTO live_class_sessions ({', '.join(test_fields)}) "
            f"VALUES ({', '.join('?' for _ in test_fields)})",
            test_values,
        )
        test_sid = int(inserted.lastrowid)
        slide_cols = {str(row[1]) for row in con.execute("PRAGMA table_info(lesson_slides_latest)")}
        if "is_test" in slide_cols:
            con.execute(
                """
                DELETE FROM lesson_slides_latest
                WHERE class_id = ? AND live_module = 'M1' AND live_slot = 'C1'
                  AND COALESCE(is_test, 0) = 1
                """,
                (class_id,),
            )
        slide_fields = [
            "class_id",
            "live_module",
            "live_slot",
            "run_key",
            "ended_at",
            "questions_json",
            "updated_at",
        ]
        slide_values: list[object] = [
            class_id,
            "M1",
            "C1",
            "mck290-test",
            "2026-10-19T19:20:00",
            "[]",
            "2026-10-19T19:20:00",
        ]
        optional = {
            "session_code": "test-entry",
            "presentation_url": "",
            "is_test": 1,
            "live_session_id": test_sid,
            "ended_at_inferred": 0,
            "inferred_ended_at": "",
        }
        for name, value in optional.items():
            if name in slide_cols:
                slide_fields.append(name)
                slide_values.append(value)
        con.execute(
            f"INSERT INTO lesson_slides_latest ({', '.join(slide_fields)}) "
            f"VALUES ({', '.join('?' for _ in slide_fields)})",
            slide_values,
        )
        con.commit()
    finally:
        con.close()
    return class_id


def shoot_mck290(base: str, state_path: Path, out_dir: Path, path: Path, widths: list[int]) -> list[Path]:
    """Screenshot Lesson Slides with Delete on a live row and a test row.

    Join codes are masked before each shot. The menu shots open Delete
    and do not confirm it. One shot shows the confirm dialog, then cancels.

    Args:
        base: Loopback origin.
        state_path: Shawn's storageState.
        out_dir: Artifact directory.
        path: LMS sqlite path.
        widths: Viewport widths.

    Returns:
        PNG paths.
    """
    from playwright.sync_api import sync_playwright

    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    class_id = prepare_mck290(path)
    url = assert_loopback(f"{origin}/staff/class/{class_id}?tab=lesson-slides")
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
            page.goto(url, wait_until="domcontentloaded")
            page.locator("#ls-run-table").wait_for(state="visible", timeout=30000)
            row = page.locator(".ls-run-row").filter(
                has=page.locator(".ls-row-name", has_text="M1C4")
            )
            row.first.wait_for(state="visible", timeout=30000)
            ran = row.first.locator("td").nth(1).inner_text()
            if "Not run yet" in ran or "Oct 19" not in ran:
                raise SystemExit("Ended class is missing from Lesson Slides")
            catchup = row.first.locator(".ls-catchup-label").inner_text()
            if catchup.strip() != "none":
                raise SystemExit("Ended class without answers should show catch-up none")
            page.locator(".ls-join-code").evaluate_all(
                "nodes => nodes.forEach((node) => { node.textContent = '\\u2014'; })"
            )
            dest = out_dir / f"mck290-lesson-slides-ended-{width}.png"
            page.locator(".lesson-slides-table-card").screenshot(path=str(dest))
            written.append(dest)
            if row.first.locator(".ls-row-menu-btn").count() != 1:
                raise SystemExit("Live Lesson Slides row is missing Delete")
            row.first.evaluate("node => node.scrollIntoView({block: 'center'})")
            row.first.locator(".ls-row-menu-btn").click()
            live_delete = row.first.locator(".ls-row-menu-delete")
            live_delete.wait_for(state="visible", timeout=10000)
            if live_delete.inner_text().strip() != "Delete":
                raise SystemExit("Live Lesson Slides delete label is missing")
            page.locator(".ls-join-code").evaluate_all(
                "nodes => nodes.forEach((node) => { node.textContent = '\u2014'; })"
            )
            live_dest = out_dir / f"mck290-lesson-slides-live-delete-{width}.png"
            card_box = page.locator(".lesson-slides-table-card").bounding_box()
            menu_box = live_delete.bounding_box()
            if card_box is None or menu_box is None:
                raise SystemExit("Live Delete menu is outside the Lesson Slides card")
            left = min(card_box["x"], menu_box["x"])
            top = min(card_box["y"], menu_box["y"])
            right = max(card_box["x"] + card_box["width"], menu_box["x"] + menu_box["width"])
            bottom = max(card_box["y"] + card_box["height"], menu_box["y"] + menu_box["height"])
            page.screenshot(
                path=str(live_dest),
                clip={
                    "x": max(0, left),
                    "y": max(0, top),
                    "width": right - max(0, left),
                    "height": bottom - max(0, top) + 8,
                },
            )
            written.append(live_dest)
            live_delete.click()
            dialog = page.locator("#ls-delete-entry-dialog")
            dialog.wait_for(state="visible", timeout=10000)
            if "Lesson Slides" not in dialog.locator("h2").inner_text():
                raise SystemExit("Delete confirm dialog is missing")
            confirm_dest = out_dir / f"mck290-lesson-slides-delete-confirm-{width}.png"
            dialog.screenshot(path=str(confirm_dest))
            written.append(confirm_dest)
            dialog.locator("button[value='cancel']").click()
            test_row = page.locator(".ls-run-row.is-test-run").first
            test_row.wait_for(state="visible", timeout=30000)
            badge = test_row.locator(".badge-test-run")
            if badge.inner_text().strip() != "Test":
                raise SystemExit("Test tag is missing")
            test_row.evaluate("node => node.scrollIntoView({block: 'center'})")
            test_row.locator(".ls-row-menu-btn").click()
            delete_item = test_row.locator(".ls-row-menu-delete")
            delete_item.wait_for(state="visible", timeout=10000)
            if delete_item.inner_text().strip() != "Delete":
                raise SystemExit("Test Lesson Slides delete label is missing")
            page.locator(".ls-join-code").evaluate_all(
                "nodes => nodes.forEach((node) => { node.textContent = '\u2014'; })"
            )
            menu_dest = out_dir / f"mck290-lesson-slides-test-delete-{width}.png"
            card_box = page.locator(".lesson-slides-table-card").bounding_box()
            menu_box = delete_item.bounding_box()
            if card_box is None or menu_box is None:
                raise SystemExit("Test Delete menu is outside the Lesson Slides card")
            left = min(card_box["x"], menu_box["x"])
            top = min(card_box["y"], menu_box["y"])
            right = max(card_box["x"] + card_box["width"], menu_box["x"] + menu_box["width"])
            bottom = max(card_box["y"] + card_box["height"], menu_box["y"] + menu_box["height"])
            page.screenshot(
                path=str(menu_dest),
                clip={
                    "x": max(0, left),
                    "y": max(0, top),
                    "width": right - max(0, left),
                    "height": bottom - max(0, top) + 8,
                },
            )
            written.append(menu_dest)
            context.close()
        browser.close()
    return written


def shoot_mck280(base: str, out_dir: Path, path: Path, state_path: Path) -> list[Path]:
    """MCK-280: legend colours, no on-board names, show to class, no send.

    Visit tokens are used only to open student pages. This does not print
    them, or student names.

    Args:
        base: Loopback origin.
        out_dir: Directory for PNG files.
        path: LMS sqlite path.
        state_path: Staff storageState.

    Returns:
        Written screenshot paths.
    """
    from playwright.sync_api import sync_playwright

    origin = assert_loopback(base if "://" in base else f"http://{base}").rstrip("/")
    prepared = prepare_mck272(path)
    class_id = int(prepared["class_id"])
    session_id = int(prepared["session_id"])
    tokens = prepared["tokens"]
    if not isinstance(tokens, dict):
        raise SystemExit("Demo visit tokens were not prepared.")
    staff_url = f"{origin}/staff/class/{class_id}?tab=live"
    out_dir.mkdir(parents=True, exist_ok=True)
    shots: list[Path] = []
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        staff_ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            storage_state=str(state_path) if state_path.exists() else None,
        )
        staff = staff_ctx.new_page()
        staff.goto(staff_url, wait_until="domcontentloaded")
        staff.locator('.live-content-tab[data-tab="canvas"]').click(timeout=15000)
        staff.locator('[data-surface-publish="canvas"]').wait_for(state="visible", timeout=30000)
        if staff.locator("#wb-live-send").count():
            raise SystemExit("Send feedback is still on the board.")
        publish_mck272_whiteboard(path, session_id)
        mark_second_teammate_drew_and_left(path, session_id)
        staff.locator("#formative-live-board:not([hidden]) .wb-live-tile").first.wait_for(
            state="visible", timeout=30000
        )
        posted = staff.evaluate(
            """async () => {
              const id = document.getElementById('ap-root')?.dataset.liveSessionId || '';
              if (!id) return 'no-session';
              const res = await fetch(`/api/live-sessions/${id}/whiteboard-gallery/team:0/feedback`, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                  ops: [{type: 'text_upsert', id: 'm280', text: 'marked', x: 0.3, y: 0.4, color: '#dc2626'}],
                }),
              });
              return res.ok ? 'ok' : 'fail';
            }"""
        )
        if posted != "ok":
            raise SystemExit("Teacher ink was not saved.")
        staff.locator("#formative-live-board .wb-live-status").filter(
            has_text="Teacher wrote"
        ).first.wait_for(state="visible", timeout=15000)
        if not staff.locator("#wb-live-filters").get_by_text("Teacher wrote", exact=True).count():
            raise SystemExit("Teacher wrote filter is missing.")
        gallery = out_dir / "mck280-gallery-1280x800.png"
        staff.screenshot(path=str(gallery))
        shots.append(gallery)

        staff.locator("#formative-live-board .wb-live-tile").first.click()
        staff.locator("#wb-live-panel:not([hidden]) #wb-live-show").wait_for(
            state="visible", timeout=15000
        )
        staff.locator("#wb-live-legend .wb-legend-chip").nth(1).wait_for(
            state="visible", timeout=15000
        )
        for selector in ("#wb-live-pen", "#wb-live-undo", "#wb-live-show", "#wb-live-prev", "#wb-live-next"):
            staff.locator(selector).scroll_into_view_if_needed()
            if not staff.locator(selector).is_visible():
                raise SystemExit(f"Panel control is not visible: {selector}")
        panel = out_dir / "mck280-panel-1280x800.png"
        staff.locator("#wb-live-panel").screenshot(path=str(panel))
        shots.append(panel)

        maple_ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        pine_ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        maple = maple_ctx.new_page()
        pine = pine_ctx.new_page()
        _mck272_open_student(maple, origin, str(tokens["Maple"]))
        maple.locator("#canvas-pane:not([hidden]) #class-board-legend .wb-legend-chip").first.wait_for(
            state="visible", timeout=30000
        )
        if maple.locator(".name-chip").count():
            raise SystemExit("A name label is still drawn on the student board.")
        own = out_dir / "mck280-student-legend-1280x800.png"
        maple.locator("#canvas-pane").screenshot(path=str(own))
        shots.append(own)

        staff.locator("#wb-live-show").click()
        _mck272_open_student(pine, origin, str(tokens["Pine"]))
        pine.locator("#canvas-pane:not([hidden])").wait_for(state="visible", timeout=30000)
        pine.wait_for_function(
            """async () => {
              const res = await fetch('/api/student/board/mine?since=0&teacher_since=0', {credentials: 'same-origin'});
              if (!res.ok) return false;
              const data = await res.json();
              const board = data && data.class_board;
              if (!board || board.board_key !== 'team:0') return false;
              const legend = Array.isArray(board.legend) ? board.legend : [];
              if (legend.length < 2) return false;
              return JSON.stringify(board).includes('marked');
            }""",
            timeout=30000,
        )
        pine.locator("#class-board-legend .wb-legend-chip").nth(1).wait_for(
            state="visible", timeout=15000
        )
        if pine.locator(".name-chip").count():
            raise SystemExit("A name label is still drawn on the projected board.")
        shown = out_dir / "mck280-class-board-1280x800.png"
        pine.locator("#canvas-pane").screenshot(path=str(shown))
        shots.append(shown)
        browser.close()
    return shots


def _mck291_mask_secrets(page: object) -> None:
    """Hide join codes and roster labels before a Set Class screenshot.

    The billboard text is replaced in place and the board is hidden. Roster
    name nodes are cleared by selector. Nothing from the page is printed.

    Args:
        page: Playwright page on Run Live Class setup.
    """
    page.evaluate(  # type: ignore[attr-defined]
        """() => {
          const board = document.querySelector('#ap-join-billboard');
          const codeEl = document.querySelector('#ap-join-billboard-code');
          const raw = (codeEl && codeEl.textContent ? codeEl.textContent : '').trim();
          if (codeEl) codeEl.textContent = '\\u00b7\\u00b7\\u00b7\\u00b7';
          if (board) board.hidden = true;
          const root = document.querySelector('#ap-root');
          if (root) root.removeAttribute('data-live-code');
          const placeholder = '\\u00b7\\u00b7\\u00b7\\u00b7';
          if (raw && raw !== placeholder && raw !== '\\u2014' && raw !== '----') {
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            const nodes = [];
            while (walker.nextNode()) nodes.push(walker.currentNode);
            for (const node of nodes) {
              if (node.textContent && node.textContent.includes(raw)) {
                node.textContent = node.textContent.split(raw).join(placeholder);
              }
            }
          }
          document.querySelectorAll('.ap-att-name, #ap-att-list, .roster-names, .ap-roster-pin').forEach((el) => {
            el.textContent = '';
          });
          document.querySelectorAll('dialog[open]').forEach((dialog) => {
            if (typeof dialog.close === 'function') dialog.close();
          });
          document.querySelectorAll('#onb-tour, #onb-check, #whats-new-dialog').forEach((el) => {
            el.hidden = true;
          });
        }"""
    )


def _mck291_refuse_visible_secrets(page: object) -> None:
    """Stop the shot when a PIN, join code, or catch-up code is still on screen.

    The check reads visible text and does not include that text in the error.

    Args:
        page: Playwright page after masking.

    Raises:
        SystemExit: A secret pattern is still visible.
    """
    leaked = page.evaluate(  # type: ignore[attr-defined]
        """() => {
          const text = document.body ? document.body.innerText : '';
          if (/\\bPIN\\b[^\\n]{0,16}\\d{4}/i.test(text)) return 'pin';
          if (/catch-up code/i.test(text)) return 'catchup';
          const board = document.querySelector('#ap-join-billboard');
          if (board && !board.hidden) {
            const code = (document.querySelector('#ap-join-billboard-code') || {}).textContent || '';
            if (/[A-Za-z0-9]{4,}/.test(code) && !/^\\u00b7+$/.test(code.trim())) return 'join';
          }
          return '';
        }"""
    )
    if leaked:
        raise SystemExit("Refusing screenshot: a code or PIN is still visible")


def _mck291_scroll_calendar(page: object) -> dict[str, object]:
    """Scroll the Set Class day grid into the viewport.

    Args:
        page: Playwright page with the setup calendar rendered.

    Returns:
        Geometry after the scroll. ``ok`` is true when the grid and a day
        cell sit fully inside the viewport.
    """
    return page.evaluate(  # type: ignore[attr-defined]
        """() => {
          const grid = document.querySelector('#ap-day-grid');
          const cell = grid && grid.querySelector('.ap-day-cell');
          const bodyStyle = getComputedStyle(document.body);
          const shell = document.querySelector('.track-live-root');
          const shellStyle = shell ? getComputedStyle(shell) : null;
          if (!grid || !cell) return {ok: false, reason: 'missing'};
          const scroller = document.scrollingElement || document.documentElement;
          const beforeTop = grid.getBoundingClientRect().top;
          const beforeScroll = scroller.scrollTop;
          const needed = beforeTop < -2 || grid.getBoundingClientRect().bottom > window.innerHeight + 2;
          grid.scrollIntoView({block: 'center', inline: 'nearest'});
          const rect = grid.getBoundingClientRect();
          const cellRect = cell.getBoundingClientRect();
          const viewH = window.innerHeight;
          const viewW = window.innerWidth;
          const gridInView = rect.height > 40
            && rect.top >= -1 && rect.bottom <= viewH + 1
            && rect.left >= -1 && rect.right <= viewW + 1;
          const cellInView = cellRect.height > 8
            && cellRect.top >= -1 && cellRect.bottom <= viewH + 1
            && cellRect.left >= -1 && cellRect.right <= viewW + 1;
          const scrolled = Math.abs(scroller.scrollTop - beforeScroll) > 1;
          return {
            ok: gridInView && cellInView && (!needed || scrolled || gridInView),
            needed,
            scrolled,
            scrollTop: scroller.scrollTop,
            scrollHeight: scroller.scrollHeight,
            clientHeight: scroller.clientHeight,
            top: Math.round(rect.top),
            bottom: Math.round(rect.bottom),
            height: Math.round(rect.height),
            viewH,
            viewW,
            bodyOverflowY: bodyStyle.overflowY,
            shellOverflowY: shellStyle ? shellStyle.overflowY : '',
          };
        }"""
    )


def shoot_mck291(base: str, state_path: Path, out_dir: Path, path: Path) -> list[Path]:
    """Screenshot Set Class after scrolling the day calendar into view.

    Viewports are a phone (390x844), a tablet (768x1024), and a short desktop
    (1280x800). Join codes and roster labels are masked before the shot.

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
    class_id = shawn_class_id(path)
    home = assert_loopback(f"{origin}/staff")
    setup = assert_loopback(f"{origin}/staff/class/{class_id}?tab=live&run=1")
    viewports = ((390, 844), (768, 1024), (1280, 800))
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        for width, height in viewports:
            context = browser.new_context(
                storage_state=str(state_path),
                viewport={"width": width, "height": height},
            )
            page = context.new_page()
            page.goto(home, wait_until="domcontentloaded", timeout=60000)
            link = page.locator("a.course-action-live")
            if link.count():
                link.first.click()
                page.wait_for_url("**tab=live**", timeout=30000)
            else:
                page.goto(setup, wait_until="domcontentloaded", timeout=60000)
            page.locator("#ap-root.is-set-class").wait_for(state="visible", timeout=30000)
            page.locator("#set-class-day-step").wait_for(state="visible", timeout=30000)
            page.locator("#ap-day-grid .ap-day-cell").first.wait_for(state="visible", timeout=30000)
            page.locator("#set-class-kind-live").wait_for(state="visible", timeout=15000)
            page.locator("#set-class-start").wait_for(state="visible", timeout=15000)
            skip = page.locator("[data-tour-skip]")
            if skip.count() and skip.first.is_visible():
                skip.first.click()
            _mck291_mask_secrets(page)
            _mck291_refuse_visible_secrets(page)
            metrics = _mck291_scroll_calendar(page)
            print(
                "mck291",
                f"{width}x{height}",
                {key: metrics.get(key) for key in (
                    "ok", "needed", "scrolled", "scrollTop", "scrollHeight",
                    "clientHeight", "top", "bottom", "height", "viewH",
                    "bodyOverflowY", "shellOverflowY",
                )},
            )
            if not metrics.get("ok"):
                context.close()
                browser.close()
                raise SystemExit(f"Calendar is not in view at {width}x{height}")
            if metrics.get("bodyOverflowY") not in {"auto", "scroll"}:
                context.close()
                browser.close()
                raise SystemExit(f"Set Class page does not scroll at {width}x{height}")
            _mck291_refuse_visible_secrets(page)
            dest = out_dir / f"mck291-set-class-calendar-{width}x{height}.png"
            page.screenshot(path=str(dest), full_page=False)
            written.append(dest)
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
            "mck218",
            "avatar-reward",
            "mck216",
            "mck220",
            "mck230",
            "mck215",
            "mck222",
            "mck219",
            "mck217-student-view",
            "mck242",
            "mck272",
            "mck273",
            "mck275",
            "mck249",
            "mck281",
            "mck289",
            "mck290",
            "mck280",
            "mck291",
            "mck293",
        ),
        default="",
        help=(
            "Built-in catch-up, MCK-62 quiet-dot, engagement override, "
            "MCK-207 roster/PIN, run-class toggle, MCK-208 dashboard, "
            "avatar-reward pop-up, MCK-216 Team challenge rank lock-in, "
            "MCK-220/230/215/222/219 scenarios, MCK-217 Student view Questions, "
            "MCK-242 Team Draw, MCK-272 whiteboard, MCK-273 one join code, "
            "MCK-275 End Class and Quit, MCK-249 teacher course tabs, "
            "MCK-281 Take turns rank, MCK-289 catch-up column, "
            "MCK-290 ended class on Lesson Slides, "
            "MCK-280 whiteboard fixes, MCK-291 Set Class calendar, "
            "or MCK-293 group rank check and group engagement points."
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
        if args.scenario == "mck218":
            written.extend(shoot_mck218(args.base, out, path, state))
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
        if args.scenario == "mck219":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck219(args.base, state, out, widths))
        if args.scenario == "mck217-student-view":
            written.extend(shoot_mck217_student_view(out))
        if args.scenario == "mck242":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck242(args.base, state, out, path, widths))
        if args.scenario == "mck272":
            written.extend(shoot_mck272(args.base, out, path, state))
        if args.scenario == "mck273":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck273(args.base, out, path, widths))
        if args.scenario == "mck275":
            written.extend(shoot_mck275_end_quit(args.base, state, out, path))
        if args.scenario == "mck249":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck249(args.base, state, out, path, widths))
        if args.scenario == "mck281":
            written.extend(shoot_mck281(args.base, state, out, path))
        if args.scenario == "mck289":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck289(args.base, state, out, path, widths))
        if args.scenario == "mck290":
            widths = [int(part) for part in args.widths.split(",") if part.strip()]
            written.extend(shoot_mck290(args.base, state, out, path, widths))
        if args.scenario == "mck280":
            written.extend(shoot_mck280(args.base, out, path, state))
        if args.scenario == "mck291":
            written.extend(shoot_mck291(args.base, state, out, path))
        if args.scenario == "mck293":
            written.extend(shoot_mck293(args.base, state, out, path))
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
                "mck208|mck218|avatar-reward|mck216|mck220|mck230|mck215|mck222|mck219|"
                "mck217-student-view|mck242|mck272|mck273|mck275|mck249|mck281|mck289|mck290|mck280|mck291|mck293."
            )
        for item in written:
            print(item)
        return 0
    finally:
        if owns_state:
            state.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
