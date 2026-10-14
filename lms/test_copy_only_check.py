#!/usr/bin/env python3
"""Unit tests for scripts/copy_only_check.py (copy-only fast lane guard)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import copy_only_check as coc  # noqa: E402


class CopyOnlyNormalizerTests(unittest.TestCase):
    """Pass/fail examples on synthetic before/after pairs."""

    def test_markup_text_node_passes(self) -> None:
        """Visible text between tags may change."""
        old = "<p>Hello team</p>"
        new = "<p>Hello class</p>"
        ok, _ = coc.file_is_copy_only(old, new, "markup")
        self.assertTrue(ok)

    def test_markup_aria_label_passes(self) -> None:
        """Allowed accessibility attributes may change."""
        old = '<input aria-label="Search students">'
        new = '<input aria-label="Find students">'
        ok, _ = coc.file_is_copy_only(old, new, "markup")
        self.assertTrue(ok)

    def test_markup_class_change_fails(self) -> None:
        """Structural attributes are rejected."""
        old = '<p class="muted">Hi</p>'
        new = '<p class="loud">Hi</p>'
        ok, reason = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)
        self.assertIn("copy", reason.lower())

    def test_markup_jinja_expression_fails(self) -> None:
        """Template expressions must not change."""
        old = "<span>{{ user.name }}</span>"
        new = "<span>{{ user.email }}</span>"
        ok, _ = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)

    def test_json_string_value_passes(self) -> None:
        """JSON string values may change."""
        old = '{"greeting": "Hello", "count": 3}'
        new = '{"greeting": "Welcome", "count": 3}'
        ok, _ = coc.file_is_copy_only(old, new, "json_copy")
        self.assertTrue(ok)

    def test_json_key_change_fails(self) -> None:
        """JSON keys are structural."""
        old = '{"greeting": "Hello"}'
        new = '{"welcome": "Hello"}'
        ok, _ = coc.file_is_copy_only(old, new, "json_copy")
        self.assertFalse(ok)

    def test_markdown_prose_passes(self) -> None:
        """Docs prose may change outside fences."""
        old = "# Title\n\nOld paragraph.\n"
        new = "# Title\n\nNew paragraph.\n"
        ok, _ = coc.file_is_copy_only(old, new, "markdown")
        self.assertTrue(ok)

    def test_forbidden_path_kind(self) -> None:
        """Python paths are never copy-only."""
        ok, reason = coc.file_is_copy_only("x = 1\n", "x = 2\n", "forbidden")
        self.assertFalse(ok)
        self.assertIn("allowlist", reason)


class CopyOnlyGitTests(unittest.TestCase):
    """End-to-end git range check in a temporary repository."""

    def setUp(self) -> None:
        """Create an isolated git repo with one template file."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.template = self.root / "lms" / "templates" / "staff" / "sample.html"
        self.template.parent.mkdir(parents=True)
        self.template.write_text("<p>Alpha</p>\n", encoding="utf-8")
        self._git("init")
        self._git("config", "user.email", "copy@test.local")
        self._git("config", "user.name", "Copy Test")
        self._git("add", ".")
        self._git("commit", "-m", "base")
        self.base = self._git("rev-parse", "HEAD").strip()

    def tearDown(self) -> None:
        """Remove temp directory."""
        self.tmp.cleanup()

    def _git(self, *args: str) -> str:
        """Run git inside the temp repo."""
        env = {"GIT_DIR": None}
        result = subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
            env=None,
        )
        return result.stdout

    def test_git_range_passes_copy_edit(self) -> None:
        """A text-only template edit exits 0."""
        self.template.write_text("<p>Beta</p>\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "copy")
        head = self._git("rev-parse", "HEAD").strip()
        errors = self._check(self.base, head)
        self.assertEqual(errors, [])

    def test_git_range_fails_class_edit(self) -> None:
        """A class attribute edit is reported."""
        self.template.write_text('<p class="on">Beta</p>\n', encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "structure")
        head = self._git("rev-parse", "HEAD").strip()
        errors = self._check(self.base, head)
        self.assertTrue(errors)
        self.assertIn("sample.html", errors[0])

    def _check(self, base: str, head: str) -> list[str]:
        """Run check_range against the temp repo via subprocess."""
        script = SCRIPTS / "copy_only_check.py"
        env = {**os.environ, "COPY_ONLY_GIT_DIR": str(self.root)}
        result = subprocess.run(
            [sys.executable, str(script), base, head],
            cwd=self.root,
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode == 0:
            return []
        return [line for line in result.stderr.splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
