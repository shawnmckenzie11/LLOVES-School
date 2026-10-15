#!/usr/bin/env python3
"""MCK-230: shared participation exclusion helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))

from participation_exclusion import (  # noqa: E402
    participation_run_counts_in_tab,
    sql_exclude_non_counted_participation_run,
)


class ParticipationExclusionTests(unittest.TestCase):
    """Test-run SQL fragment and tab eligibility."""

    def test_sql_fragment_mentions_discarded_and_is_real(self) -> None:
        frag = sql_exclude_non_counted_participation_run("p.class_id", "p.run_key")
        self.assertIn("discarded = 1", frag)
        self.assertIn("is_real = 0", frag)

    def test_counts_in_tab_requires_real_undiscarded(self) -> None:
        self.assertTrue(participation_run_counts_in_tab({"is_real": True, "discarded": False}))
        self.assertFalse(participation_run_counts_in_tab({"is_real": False, "discarded": True}))
        self.assertTrue(participation_run_counts_in_tab(None))


if __name__ == "__main__":
    unittest.main()
