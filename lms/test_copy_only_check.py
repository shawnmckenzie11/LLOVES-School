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

    def test_markup_jinja_in_title_attribute_fails(self) -> None:
        """Jinja inside a copy attribute is rejected."""
        old = '<span title="Hello"></span>'
        new = '<span title="{{ config.SECRET_KEY }}"></span>'
        ok, _ = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)

    def test_markup_jinja_include_in_title_fails(self) -> None:
        """Jinja block tags inside title are rejected."""
        old = '<span title="Hi"></span>'
        new = """<span title="{% include 'x' %}"></span>"""
        ok, reason = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)

    def test_markup_script_body_change_fails(self) -> None:
        """Existing script bodies are not copy."""
        old = "<script>const x = 1;</script>"
        new = "<script>fetch('https://evil.example/')</script>"
        ok, _ = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)

    def test_markup_style_body_change_fails(self) -> None:
        """Existing style bodies are not copy."""
        old = "<style>.a{color:red}</style>"
        new = "<style>.a{color:blue}</style>"
        ok, _ = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)

    def test_markup_data_title_not_copy(self) -> None:
        """Only exact alt/title/placeholder/aria-label count as copy attrs."""
        old = '<span data-title="1"></span>'
        new = '<span data-title="2"></span>'
        ok, _ = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)

    def test_markup_subtitle_not_copy(self) -> None:
        """subtitle= is not an allowed copy attribute."""
        old = '<span subtitle="a"></span>'
        new = '<span subtitle="b"></span>'
        ok, _ = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)

    def test_markup_bidi_in_text_fails(self) -> None:
        """Bidi override characters in text are rejected."""
        old = "<p>Hello</p>"
        new = "<p>Hel\u202elo</p>"
        ok, reason = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)
        self.assertIn("unicode", reason.lower())

    def test_markup_zero_width_in_text_fails(self) -> None:
        """Zero-width characters in text are rejected."""
        old = "<p>Hello</p>"
        new = "<p>Hel\u200blo</p>"
        ok, reason = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)

    def test_new_file_not_allowed(self) -> None:
        """New files are rejected at the file gate."""
        ok, reason = coc.file_is_copy_only(None, "<p>x</p>", "markup")
        self.assertFalse(ok)
        self.assertIn("new", reason.lower())

    def test_deleted_file_not_allowed(self) -> None:
        """Deleted files are rejected at the file gate."""
        ok, reason = coc.file_is_copy_only("<p>x</p>", None, "markup")
        self.assertFalse(ok)
        self.assertIn("deleted", reason.lower())

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

    def test_markdown_html_in_prose_fails(self) -> None:
        """Raw HTML in docs prose is rejected."""
        old = "# Title\n\nSafe text.\n"
        new = "# Title\n\n<script>alert(1)</script>\n"
        ok, reason = coc.file_is_copy_only(old, new, "markdown")
        self.assertFalse(ok)
        self.assertIn("html", reason.lower())

    def test_markdown_tag_in_prose_fails(self) -> None:
        """HTML tags in docs prose are rejected."""
        old = "Hello\n"
        new = "<p>Hello</p>\n"
        ok, reason = coc.file_is_copy_only(old, new, "markdown")
        self.assertFalse(ok)

    def test_forbidden_path_kind(self) -> None:
        """Python paths are never copy-only."""
        ok, reason = coc.file_is_copy_only("x = 1\n", "x = 2\n", "forbidden")
        self.assertFalse(ok)
        self.assertIn("allowlist", reason)

    def test_trusted_lane_path_constant(self) -> None:
        """Lane infra paths are listed as trusted."""
        self.assertIn("scripts/copy_only_check.py", coc.TRUSTED_LANE_PATHS)

    def test_markup_unclosed_tag_fails(self) -> None:
        """Appending an unclosed tag changes angle brackets and fails."""
        old = "</details>\n<span>Help</span>\n"
        new = '</details>\n<img src=x onerror=alert(document.cookie) \n<span>Help</span>\n'
        ok, reason = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)
        self.assertIn("angle bracket", reason.lower())

    def test_markup_gt_in_title_allows_title_but_not_onclick(self) -> None:
        """A ``>`` inside a quoted title must not let onclick edits pass."""
        old = '<a title="a > b" onclick="f()">link</a>'
        new = '<a title="a > b" onclick="evil()">link</a>'
        ok, _ = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)

    def test_markup_gt_in_jinja_attr_blocks_class_edit(self) -> None:
        """``>`` inside ``{{ }}`` in an attribute must not hide class edits."""
        old = '<span data-n="{{ 1 if n > 0 else 2 }}" class="hero"></span>'
        new = '<span data-n="{{ 1 if n > 0 else 2 }}" class="loud"></span>'
        ok, _ = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)

    def test_markup_html_entity_in_copy_fails(self) -> None:
        """HTML entities in visible copy are rejected."""
        old = "<p>Hello</p>"
        new = "<p>&#60;script&#62;</p>"
        ok, reason = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)
        self.assertIn("entities", reason.lower())

    def test_markup_javascript_scheme_in_copy_fails(self) -> None:
        """javascript: text in copy nodes is rejected."""
        old = "<p>Go</p>"
        new = "<p>javascript:alert(1)</p>"
        ok, reason = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)
        self.assertIn("javascript", reason.lower())

    def test_markup_jinja_line_for_in_copy_fails(self) -> None:
        """Jinja-like ``# for`` line statements in copy are rejected."""
        old = "<p>Total</p>"
        new = "<p># for x in y</p>"
        ok, reason = coc.file_is_copy_only(old, new, "markup")
        self.assertFalse(ok)
        self.assertIn("line", reason.lower())

    def test_sensitive_landing_flagged(self) -> None:
        """Join-code templates are treated as sensitive auth wording."""
        path = "lms/templates/landing.html"
        old = '<label for="code">Join code</label>'
        new = '<label for="code">Class code</label>'
        self.assertTrue(coc.is_sensitive_auth_template(path, old, new))
        ok, _ = coc.file_is_copy_only(old, new, "markup")
        self.assertTrue(ok)


class CopyOnlyWorkflowTests(unittest.TestCase):
    """CI layout: base checker path + pr git directory."""

    def test_workflow_checker_command_fixture(self) -> None:
        """Mirror the workflow: python base/checker.py with COPY_ONLY_GIT_DIR=pr."""
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        pr = root / "pr"
        base = root / "base" / "scripts"
        base.mkdir(parents=True)
        checker_src = SCRIPTS / "copy_only_check.py"
        checker_dst = base / "copy_only_check.py"
        checker_dst.write_text(checker_src.read_text(encoding="utf-8"), encoding="utf-8")
        template = pr / "lms" / "templates" / "staff" / "sample.html"
        template.parent.mkdir(parents=True)
        template.write_text("<p>Alpha</p>\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=pr, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "wf@test.local"],
            cwd=pr,
            check=True,
        )
        subprocess.run(["git", "config", "user.name", "WF"], cwd=pr, check=True)
        subprocess.run(["git", "add", "."], cwd=pr, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=pr, check=True)
        sha = (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=pr,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        env = {
            **os.environ,
            "COPY_ONLY_GIT_DIR": str(pr),
            "COPY_ONLY_CHECKER": str(checker_dst),
        }
        selftest = subprocess.run(
            [sys.executable, str(SCRIPTS / "copy_only_workflow_selftest.py")],
            cwd=pr,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(selftest.returncode, 0, selftest.stderr)
        tmp.cleanup()


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
        result = subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def test_git_empty_diff_fails(self) -> None:
        """An empty revision range is rejected."""
        errors = self._check(self.base, self.base)
        self.assertTrue(any("empty diff" in e.lower() for e in errors))

    def test_git_sensitive_join_code_requires_human_review(self) -> None:
        """Copy-only join-code edits still require human review."""
        landing = self.root / "lms" / "templates" / "landing.html"
        landing.parent.mkdir(parents=True, exist_ok=True)
        landing.write_text('<label>Join code</label>\n', encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "landing")
        base2 = self._git("rev-parse", "HEAD").strip()
        landing.write_text('<label>Class code</label>\n', encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "join copy")
        head = self._git("rev-parse", "HEAD").strip()
        errors = self._check(base2, head)
        self.assertTrue(any("human review" in e.lower() for e in errors))

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

    def test_git_range_rejects_new_file(self) -> None:
        """Adding a docs file is rejected."""
        doc = self.root / "docs" / "new.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("# New\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "add doc")
        head = self._git("rev-parse", "HEAD").strip()
        errors = self._check(self.base, head)
        self.assertTrue(any("new files" in e.lower() for e in errors))

    def test_git_range_rejects_delete(self) -> None:
        """Deleting a template is rejected."""
        self.template.unlink()
        self._git("add", ".")
        self._git("commit", "-m", "delete")
        head = self._git("rev-parse", "HEAD").strip()
        errors = self._check(self.base, head)
        self.assertTrue(any("deleted" in e.lower() for e in errors))

    def test_git_range_rejects_rename(self) -> None:
        """Renames are surfaced as add+delete and rejected."""
        target = self.root / "docs" / "sample.md"
        target.parent.mkdir(parents=True)
        self.template.rename(target)
        self._git("add", ".")
        self._git("commit", "-m", "rename")
        head = self._git("rev-parse", "HEAD").strip()
        errors = self._check(self.base, head)
        self.assertGreaterEqual(len(errors), 2)

    def test_git_range_rejects_mode_change(self) -> None:
        """chmod on a template is rejected."""
        self.template.chmod(0o755)
        self._git("add", ".")
        self._git("commit", "-m", "mode")
        head = self._git("rev-parse", "HEAD").strip()
        errors = self._check(self.base, head)
        self.assertTrue(any("mode" in e.lower() for e in errors))

    def test_git_range_rejects_symlink(self) -> None:
        """Symlinked docs paths are rejected."""
        doc = self.root / "docs" / "existing.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("# Hi\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "doc base")
        base2 = self._git("rev-parse", "HEAD").strip()
        doc.unlink()
        doc.symlink_to("../lms/templates/staff/sample.html")
        self._git("add", ".")
        self._git("commit", "-m", "symlink")
        head = self._git("rev-parse", "HEAD").strip()
        errors = self._check(base2, head)
        self.assertTrue(
            any(
                "symlink" in e.lower() or "type" in e.lower() or "mode" in e.lower()
                for e in errors
            )
        )

    def test_git_range_rejects_binary_docs(self) -> None:
        """Binary blobs posing as markdown are rejected."""
        blob = self.root / "docs" / "existing.md"
        blob.parent.mkdir(parents=True)
        blob.write_text("# Hi\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "doc base")
        base2 = self._git("rev-parse", "HEAD").strip()
        blob.write_bytes(b"hello\x00world")
        self._git("add", ".")
        self._git("commit", "-m", "binary")
        head = self._git("rev-parse", "HEAD").strip()
        errors = self._check(base2, head)
        self.assertTrue(any("binary" in e.lower() for e in errors))

    def test_git_range_rejects_checker_edit(self) -> None:
        """PRs cannot modify the trusted checker."""
        checker = self.root / "scripts" / "copy_only_check.py"
        checker.parent.mkdir(parents=True, exist_ok=True)
        checker.write_text("print('v1')\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "add checker")
        base2 = self._git("rev-parse", "HEAD").strip()
        checker.write_text("print('v2')\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "tamper")
        head = self._git("rev-parse", "HEAD").strip()
        errors = self._check(base2, head)
        self.assertTrue(any("trusted" in e.lower() for e in errors))

    def test_git_script_body_in_template_fails(self) -> None:
        """JS edits inside an existing script tag fail."""
        self.template.write_text(
            "<script>const a=1;</script>\n", encoding="utf-8"
        )
        self._git("add", ".")
        self._git("commit", "-m", "add script")
        base2 = self._git("rev-parse", "HEAD").strip()
        self.template.write_text(
            "<script>fetch('evil')</script>\n", encoding="utf-8"
        )
        self._git("add", ".")
        self._git("commit", "-m", "evil js")
        head = self._git("rev-parse", "HEAD").strip()
        errors = self._check(base2, head)
        self.assertTrue(errors)

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
