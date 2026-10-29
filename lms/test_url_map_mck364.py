#!/usr/bin/env python3
"""MCK-364: the public URL map stays the same when routes move.

The snapshot is the rule, the methods Werkzeug will accept, and the
endpoint name ``url_for`` uses. It was taken from ``main`` before the
blueprint split. A later refactor matches it exactly.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

SNAPSHOT_PATH = LMS_DIR / "fixtures" / "url_map_mck364.json"


def url_map_rows(app) -> list[list[object]]:
    """Return each URL rule as ``[rule, methods, endpoint]``.

    Methods include the ones Flask adds (``HEAD``, ``OPTIONS``) so a
    blueprint that dropped them would fail. Rows are sorted so registration
    order alone cannot change the snapshot.

    Args:
        app: Flask application from ``create_app``.

    Returns:
        Sorted rows of rule, sorted methods, and endpoint.
    """
    rows: list[list[object]] = []
    for rule in app.url_map.iter_rules():
        methods = sorted(rule.methods or ())
        rows.append([rule.rule, methods, rule.endpoint])
    rows.sort(key=lambda row: (str(row[0]), str(row[2]), json.dumps(row[1])))
    return rows


class UrlMapSnapshotTests(unittest.TestCase):
    """The reachable URL map matches the pre-split snapshot."""

    def test_url_map_matches_snapshot(self) -> None:
        """Rule, methods, and endpoint are unchanged from the committed map."""
        from app import create_app

        raw = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = create_app(
            db_path=Path(tmp.name) / "lloves.sqlite",
            data_dir=Path(tmp.name),
            testing=True,
            live_database_url="",
        )
        self.assertEqual(url_map_rows(app), raw["rows"])


if __name__ == "__main__":
    unittest.main()
