#!/usr/bin/env python3
"""Repeatable checks on a compiled static lesson (not the LMS)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / "Library/Caches/ms-playwright"))


def verify(html_path: Path) -> int:
    """Launch Chromium against a file URL and assert tabs + board + check."""
    from playwright.sync_api import sync_playwright

    url = html_path.resolve().as_uri()
    failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        title = page.title()
        if "Vertex Form" not in title and "vertex" not in title.lower():
            failures.append(f"unexpected title: {title}")
        for name in ("The water", "Minds on", "Action", "Consolidation"):
            if page.get_by_role("tab", name=name).count() != 1:
                failures.append(f"missing tab {name}")
        body = page.content()
        for banned in ("api.desmos.com", "gizmos.explorelearning", "YOUR_API_KEY", "GeoGebra"):
            if banned in body:
                failures.append(f"banned string {banned}")
        if "jsxgraphcore.js" not in body:
            failures.append("JSXGraph script not referenced")
        if "student.css" not in body:
            failures.append("student.css not referenced")
        page.get_by_role("tab", name="Action").click()
        page.wait_for_timeout(500)
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
        page.wait_for_timeout(300)
        if page.locator("[data-spec-id=fresh-case]").count() != 1:
            failures.append("fresh-case not on consolidation")
        if page.locator("[data-spec-id=fresh-case]").count() and page.get_by_role(
            "tab", name="Action"
        ):
            page.get_by_role("tab", name="Action").click()
            if page.locator("#panel-action [data-spec-id=fresh-case]").count():
                failures.append("fresh-case leaked onto Action")
        browser.close()
    if failures:
        print("FAIL")
        for item in failures:
            print("-", item)
        return 1
    print("PASS", url)
    return 0


def main() -> int:
    """CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--html",
        type=Path,
        default=Path("content-builder/build/MCF3M/M4-L1-vertex-form/index.html"),
    )
    args = parser.parse_args()
    if not args.html.is_file():
        print(f"missing {args.html}", file=sys.stderr)
        return 1
    return verify(args.html)


if __name__ == "__main__":
    raise SystemExit(main())
