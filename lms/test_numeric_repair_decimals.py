"""MCK-81: repairing a numeric live prompt keeps decimal-enabled items decimal.

``_repair_numeric_live_prompt`` rewrites a compatibility prompt that lost its
numeric kind. It used to set ``integer_only = ... or True``, so every repaired
prompt became whole-number only and undid MCK-30 for decimal questions.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from school_db import SchoolDB  # noqa: E402


class NumericRepairDecimalTests(unittest.TestCase):
    """The repair path honours the item's ``integer_only`` flag."""

    @classmethod
    def setUpClass(cls) -> None:
        """Open one throwaway SchoolDB for all cases."""
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.db = SchoolDB(root / "lloves.sqlite", root, live_database_url="")

    @classmethod
    def tearDownClass(cls) -> None:
        """Close the DB and remove the temp dir."""
        cls.db.close()
        cls._tmp.cleanup()

    def _repair(self, question: dict[str, Any], old_payload: dict[str, Any]) -> dict[str, Any]:
        """Run the repair on an MC-shaped prompt and return the written payload.

        Args:
            question: Catalogue item on the lifecycle row.
            old_payload: Payload of the stale compatibility prompt.
        """
        written: dict[str, Any] = {}

        def capture(session_id: int, **kwargs: Any) -> dict[str, Any]:
            """Record the rewrite instead of touching live_session_prompts."""
            written.update(kwargs["payload"])
            return {"id": 1, "kind": kwargs["kind"], "payload": kwargs["payload"]}

        original = self.db.set_live_session_prompt
        self.db.set_live_session_prompt = capture  # type: ignore[method-assign]
        try:
            item = {"id": 7, "live_session_id": 3, "item": {"type": "numeric", **question}}
            prompt = {"id": 1, "kind": "mc", "slide_index": 5, "payload": dict(old_payload)}
            self.db._repair_numeric_live_prompt(item, prompt)
        finally:
            self.db.set_live_session_prompt = original  # type: ignore[method-assign]
        self.assertTrue(written, "repair did not rewrite the prompt")
        self.assertEqual(written["kind"], "numeric")
        return written

    def test_decimal_item_stays_decimal(self) -> None:
        """``integer_only: False`` on the item survives the repair."""
        out = self._repair({"integer_only": False, "correct_answer": "2.5"}, {"kind": "mc"})
        self.assertIs(out["integer_only"], False)

    def test_item_flag_beats_a_stale_integer_prompt(self) -> None:
        """An old prompt saying integer does not override a decimal item."""
        out = self._repair({"integer_only": False}, {"kind": "mc", "integer_only": True})
        self.assertIs(out["integer_only"], False)

    def test_integer_item_stays_integer(self) -> None:
        """``integer_only: True`` on the item is kept."""
        out = self._repair({"integer_only": True}, {"kind": "mc"})
        self.assertIs(out["integer_only"], True)

    def test_prompt_flag_used_when_item_has_none(self) -> None:
        """No item flag: the old prompt's decimal flag is kept."""
        out = self._repair({}, {"kind": "mc", "integer_only": False})
        self.assertIs(out["integer_only"], False)

    def test_no_flag_anywhere_keeps_the_publish_default(self) -> None:
        """No flag at all matches the publish path default (integer)."""
        out = self._repair({}, {"kind": "mc"})
        self.assertIs(out["integer_only"], True)


if __name__ == "__main__":
    unittest.main()
