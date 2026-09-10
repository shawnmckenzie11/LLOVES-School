#!/usr/bin/env python3
"""Repeatable checks on a compiled static lesson (not the LMS)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / "Library/Caches/ms-playwright"))

GENERIC_TABS = ("Minds On", "Action", "Consolidation")
BANNED = ("api.desmos.com", "gizmos.explorelearning", "YOUR_API_KEY", "GeoGebra")
VERTEX_TASKS = ("match-plus-inside", "predict-h", "expand-same-graph", "fresh-case")


def _ensure_playwright_browsers() -> None:
    """Prefer the user Playwright cache over a sandbox-empty path."""
    home = Path.home() / "Library/Caches/ms-playwright"
    if home.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(home)


_ensure_playwright_browsers()


def generic_html_gates(html: str, *, lesson_id: str | None = None) -> dict:
    """Static HTML gates that do not require a browser."""
    failures: list[str] = []
    for name in GENERIC_TABS:
        needle = f">{name}</button>"
        if needle not in html:
            failures.append(f"missing tab {name}")
    if 'id="tab-hook"' in html or ">The water</button>" in html:
        failures.append("retired hook tab The water is still present")
    for banned in BANNED:
        if banned in html:
            failures.append(f"banned string {banned}")
    if "student.css" not in html:
        failures.append("student.css not referenced")
    if "under the graph" in html.lower():
        failures.append("student HTML still says under the graph")
    vertex_tasks = any(f'data-spec-id="{sid}"' in html for sid in VERTEX_TASKS)
    if vertex_tasks:
        if "jsxgraphcore.js" not in html:
            failures.append("JSXGraph script not referenced on a vertex-board lesson")
        if 'data-spec-id="match-plus-inside"' not in html:
            failures.append("missing match-plus-inside cycle")
        if 'data-spec-id="fresh-case"' not in html:
            failures.append("fresh-case not on consolidation")
        action = html.split('id="panel-action"')
        if len(action) > 1 and 'data-spec-id="fresh-case"' in action[1].split('id="panel-consolidation"')[0]:
            failures.append("fresh-case leaked onto Action")
    else:
        if "jsxgraphcore.js" in html:
            failures.append("JSXGraph bundled on a lesson with no vertex-board task")
        if lesson_id and "vertex-form" not in lesson_id and "The Vertex Form" in html:
            failures.append("vertex-form title leaked onto a different lesson")
    return {
        "ok": not failures,
        "failures": failures,
        "vertex_tasks": vertex_tasks,
        "lesson_id": lesson_id,
    }


def verify_html_file(html_path: Path, *, lesson_id: str | None = None) -> dict:
    """Launch Chromium against a file URL and return a structured report."""
    from playwright.sync_api import sync_playwright

    url = html_path.resolve().as_uri()
    failures: list[str] = []
    vertex_tasks = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        for name in GENERIC_TABS:
            if page.get_by_role("tab", name=name).count() != 1:
                failures.append(f"missing tab {name}")
        if page.get_by_role("tab", name="The water").count():
            failures.append("retired hook tab The water is still present")
        body = page.content()
        for banned in BANNED:
            if banned in body:
                failures.append(f"banned string {banned}")
        if "student.css" not in body:
            failures.append("student.css not referenced")
        vertex_tasks = any(f'data-spec-id="{sid}"' in body for sid in VERTEX_TASKS)
        if vertex_tasks:
            if "jsxgraphcore.js" not in body:
                failures.append("JSXGraph script not referenced on a vertex-board lesson")
            page.get_by_role("tab", name="Action").click()
            page.wait_for_timeout(400)
            if page.locator("[data-spec-id=match-plus-inside]").count() != 1:
                failures.append("missing match-plus-inside cycle")
            if page.get_by_role("button", name="Check").count() < 1:
                failures.append("missing Check")
            page.locator("[data-spec-id=match-plus-inside] [data-action=submit]").click()
            page.wait_for_timeout(200)
            fb = page.locator("[data-spec-id=match-plus-inside] .feedback")
            if fb.count() != 1 or not fb.inner_text().strip():
                failures.append("Check produced no feedback")
            page.get_by_role("tab", name="Consolidation").click()
            page.wait_for_timeout(200)
            if page.locator("[data-spec-id=fresh-case]").count() != 1:
                failures.append("fresh-case not on consolidation")
            page.get_by_role("tab", name="Action").click()
            if page.locator("#panel-action [data-spec-id=fresh-case]").count():
                failures.append("fresh-case leaked onto Action")
        else:
            if "jsxgraphcore.js" in body:
                failures.append("JSXGraph bundled on a lesson with no vertex-board task")
            if lesson_id and "vertex-form" not in lesson_id and "The Vertex Form" in page.title():
                failures.append("vertex-form title leaked onto a different lesson")
        browser.close()
    return {
        "ok": not failures,
        "failures": failures,
        "vertex_tasks": vertex_tasks,
        "url": url,
        "lesson_id": lesson_id,
    }


def verify(html_path: Path) -> int:
    """CLI-shaped wrapper used by older scripts."""
    report = verify_html_file(html_path)
    if not report["ok"]:
        print("FAIL")
        for item in report["failures"]:
            print("-", item)
        return 1
    print("PASS", report["url"])
    return 0


def main() -> int:
    """CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--html",
        type=Path,
        default=Path("content-builder/build/MCF3M/M4-L1-vertex-form/index.html"),
    )
    parser.add_argument("--lesson-id", default="")
    args = parser.parse_args()
    if not args.html.is_file():
        print(f"missing {args.html}", file=sys.stderr)
        return 1
    report = verify_html_file(args.html, lesson_id=args.lesson_id or None)
    if not report["ok"]:
        print("FAIL")
        for item in report["failures"]:
            print("-", item)
        return 1
    print("PASS", report["url"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
