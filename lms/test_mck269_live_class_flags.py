#!/usr/bin/env python3
"""MCK-269: strict is_test parsing."""

from __future__ import annotations

import unittest

from live_class_flags import parse_is_test_form, parse_is_test_json


class ParseIsTestValueTests(unittest.TestCase):
    """Only explicit true values count as a test run."""

    def test_json_true_only(self) -> None:
        self.assertTrue(parse_is_test_json(True))
        self.assertFalse(parse_is_test_json("true"))
        self.assertFalse(parse_is_test_json(1))
        self.assertFalse(parse_is_test_json("1"))
        self.assertFalse(parse_is_test_json(None))

    def test_form_strings(self) -> None:
        self.assertTrue(parse_is_test_form("true"))
        self.assertTrue(parse_is_test_form("1"))
        self.assertTrue(parse_is_test_form(" TRUE "))
        self.assertFalse(parse_is_test_form("yes"))
        self.assertFalse(parse_is_test_form(""))
        self.assertFalse(parse_is_test_form(None))
