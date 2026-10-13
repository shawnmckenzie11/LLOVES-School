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
import os
import sqlite3
import subprocess
import sys
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
        if mode == "mixed":
            rows = [
                ("cu-old", "session:1:2026-09-01T09:00:00", "M1", "C1", 1, "2026-09-01T09:00:00"),
                ("cu-mid", "session:2:2026-09-15T09:00:00", "M1", "C1", 0, "2026-09-15T09:00:00"),
                ("cu-new", "session:3:2026-10-13T09:00:00", "M1", "C1", 1, "2026-10-13T09:00:00"),
                ("cu-c2", "session:4:2026-10-02T09:00:00", "M1", "C2", 0, "2026-10-02T09:00:00"),
            ]
            con.executemany(
                """
                INSERT INTO catchup_links (
                    token, class_id, run_key, live_module, live_slot, is_open,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (token, class_id, run_key, module, slot, is_open, created, created)
                    for token, run_key, module, slot, is_open, created in rows
                ],
            )
        con.commit()
    finally:
        con.close()
    return class_id


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
    return chromium.launch(
        headless=True,
        executable_path=CHROME,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    )


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
                    page.locator(ready).first.wait_for(state="visible", timeout=15000)
                if click:
                    page.locator(click).first.click()
                name = f"{slug_for(url)}-{width}.png"
                dest = out_dir / name
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
        card = page.locator(".catchup-links-card").first
        card.wait_for(state="visible", timeout=15000)
        page.locator(".catchup-pending").first.wait_for(state="visible", timeout=15000)
        if page.locator(".catchup-links-card iframe, .catchup-links-card embed").count():
            raise SystemExit("Catch-up card rendered a frame or embed.")
        page.locator(".ls-module-block").first.screenshot(path=str(shots["empty"]))

        prepare_catchup(path, "mixed")
        page.reload(wait_until="domcontentloaded")
        card.wait_for(state="visible", timeout=15000)
        page.locator(".catchup-slot > .catchup-row .catchup-badge.is-open").first.wait_for(
            state="visible", timeout=15000
        )
        page.locator(".catchup-slot > .catchup-row .catchup-badge.is-muted").first.wait_for(
            state="visible", timeout=15000
        )
        page.locator(".ls-module-block").first.screenshot(path=str(shots["mixed"]))

        page.locator("details.catchup-earlier summary").first.click()
        page.locator(".catchup-row.is-earlier").first.wait_for(state="visible", timeout=15000)
        page.locator(".ls-module-block").first.screenshot(path=str(shots["earlier"]))
        context.close()
        browser.close()
    return list(shots.values())


def main(argv: list[str] | None = None) -> int:
    """CLI entry: seed, log in once, then shoot URLs or the catch-up scenario."""
    parser = argparse.ArgumentParser(description="Headless local LMS screenshots.")
    parser.add_argument("--base", default=DEFAULT_BASE, help="Loopback origin.")
    parser.add_argument("--url", action="append", default=[], help="URL to shoot. Repeatable.")
    parser.add_argument("--widths", default="390,1280", help="Comma-separated viewport widths.")
    parser.add_argument("--out", default="/opt/cursor/artifacts", help="PNG directory.")
    parser.add_argument("--state", default="", help="storageState JSON path.")
    parser.add_argument("--seed", action="store_true", help="Run the local-dev seed first.")
    parser.add_argument("--click", default="", help="CSS selector to click before each shot.")
    parser.add_argument("--ready", default="", help="CSS selector that must be visible.")
    parser.add_argument(
        "--scenario",
        choices=("catchup",),
        default="",
        help="Built-in Lesson Slides catch-up card shots.",
    )
    args = parser.parse_args(argv)
    assert_loopback(args.base if "://" in args.base else f"http://{args.base}")
    if args.seed:
        seed_demo()
    path = db_path()
    state = Path(args.state) if args.state else Path("/tmp/lloves-shawn-state.json")
    if not state.exists() or args.seed:
        login_state(args.base, state, teacher_email=staff_shawn_email(path))
    out = Path(args.out)
    written: list[Path] = []
    if args.scenario == "catchup":
        written.extend(shoot_catchup(args.base, state, out, path))
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
        raise SystemExit("Pass --url and/or --scenario catchup.")
    for item in written:
        print(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
