#!/usr/bin/env python3
"""Unit tests for bank question math formatting and graph images."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))

from bank_mc_normalize import normalize_bank_mc  # noqa: E402
from question_math import (  # noqa: E402
    enrich_live_mc_display,
    format_math_html,
    format_mc_html_fragment,
    graph_image_for_builder_item,
    html_to_plain,
    normalize_house_tex,
    resolve_bank_image_url,
)


class QuestionMathTests(unittest.TestCase):
    """Math formatting helpers."""

    def test_html_to_plain_preserves_caret_from_sup(self) -> None:
        """``<sup>`` tags become caret notation before display formatting."""
        plain = html_to_plain("<p>f(x) = a(x − h)<sup>2</sup> + k</p>")
        self.assertIn("^2", plain)
        self.assertNotIn("<sup>", plain)

    def test_format_math_html_renders_exponents(self) -> None:
        """Caret exponents render as superscript HTML."""
        rendered = format_math_html("Compare y = x^2 with f(x) = a(x − h)^2 + k")
        self.assertIn("x<sup>2</sup>", rendered)
        self.assertIn("(x − h)<sup>2</sup>", rendered)
        self.assertNotIn("^2", rendered)

    def test_format_math_html_wraps_dollar_and_tex(self) -> None:
        """Dollar / LaTeX delimiters become KaTeX-ready spans."""
        rendered = format_math_html(r"Solve $x^2$ and \(\frac{1}{2}\) then \sqrt{96}")
        self.assertIn('class="math-latex"', rendered)
        self.assertIn("data-latex=", rendered)
        self.assertIn("x^2", rendered)
        self.assertIn(r"\frac{1}{2}", rendered)
        self.assertIn(r"\sqrt{96}", rendered)
        self.assertNotIn("$x^2$", rendered)

    def test_format_math_html_wraps_dollar_numeric_option(self) -> None:
        """A bare ``$8$`` choice is still TeX, not leftover dollar signs."""
        rendered = format_math_html("$8$")
        self.assertIn('data-latex="8"', rendered)
        self.assertNotIn("$8$", rendered)

    def test_format_math_html_collapses_double_backslash_frac(self) -> None:
        """One extra TeX backslash is stripped before wrapping."""
        rendered = format_math_html(r"Find \\frac{1}{2}")
        self.assertIn(r"\frac{1}{2}", rendered)
        self.assertNotIn(r"\\frac", rendered)

    def test_format_math_html_prefers_tex_fraction_inside_dollars(self) -> None:
        """ASCII ``1/2`` inside math becomes ``\\frac``."""
        rendered = format_math_html(r"Compute $1/2$")
        self.assertIn(r"\frac{1}{2}", rendered)

    def test_normalize_house_tex_converts_parens_to_dollars(self) -> None:
        """``\\(...\\)`` / ``\\[...\\]`` become house ``$`` / ``$$``."""
        self.assertEqual(normalize_house_tex(r"Find \(\frac{1}{2}\)"), r"Find $\frac{1}{2}$")
        self.assertIn("$$", normalize_house_tex(r"\[x^2\]"))

    def test_format_math_html_houses_paren_delimiters(self) -> None:
        """Display wrapping runs after house-style conversion."""
        rendered = format_math_html(r"Find \(\frac{1}{2}\)")
        self.assertIn('data-latex="\\frac{1}{2}"', rendered)
        self.assertNotIn(r"\(", rendered)

    def test_format_math_html_unescapes_entities(self) -> None:
        """Double-escaped less-than does not stay as visible &lt;."""
        rendered = format_math_html("Is x &lt; 5?")
        self.assertIn("x &lt; 5", rendered)
        self.assertNotIn("&amp;lt;", rendered)

    def test_html_to_plain_keeps_math_span_latex(self) -> None:
        """KaTeX spans round-trip to dollar TeX for editors and fingerprints."""
        plain = html_to_plain(
            '<p>Find <span class="math-latex" data-latex="x^2">x^2</span></p>'
        )
        self.assertEqual(plain, "Find $x^2$")

    def test_graph_image_for_sketch_item(self) -> None:
        """Sketch-related builder items receive a static graph asset."""
        url = graph_image_for_builder_item(
            "mcf3m-m1-owned-sketch-steps-mc",
            "Starting from y = x^2",
            process_tags=["sketch-by-transformations"],
        )
        self.assertTrue(url.startswith("/static/bank-graphs/"))


class BankMcDisplayTests(unittest.TestCase):
    """Normalized bank MC display enrichment."""

    def test_normalize_attaches_display_html_and_image(self) -> None:
        """Ingest MC rows expose formatted HTML and image metadata."""
        payload = {
            "stem_html": "From the graph of y = ax^2 + bx + c, what is the vertex?",
            "points_possible": 1.0,
            "choices": [
                {"id": "a", "html": "(0, c)", "correct": True},
                {"id": "b", "html": "(h, k)", "correct": False},
            ],
            "correct_ids": ["a"],
            "image_url": "/static/bank-graphs/parabola-grid.svg",
        }
        live, err = normalize_bank_mc(
            question_id=1,
            bank_id=2,
            item_type="multiple_choice_question",
            payload=payload,
        )
        assert live is not None and err is None
        self.assertIn("<sup>", live["text_html"])
        self.assertEqual(live["image_url"], "/static/bank-graphs/parabola-grid.svg")
        self.assertEqual(len(live["options_html"]), 2)

    def test_enrich_live_mc_display_from_plain_seed(self) -> None:
        """Seed live items can be enriched for display without re-ingest."""
        live = {"text": "Evaluate r(s) = −10s^2 + 1500s", "options": ["A", "B"]}
        enrich_live_mc_display(live)
        self.assertIn("s<sup>2</sup>", live["text_html"])




    def test_equation_image_becomes_math_latex(self) -> None:
        """Canvas equation images become KaTeX-ready spans."""
        raw = (
            '<img class="equation_image" title="\\sqrt{96}" '
            'src="https://virtuallearning.instructure.com/equation_images/x" '
            'alt="\\sqrt{96}">'
        )
        rendered = format_mc_html_fragment(raw, class_id=5)
        self.assertIn('class="math-latex"', rendered)
        self.assertIn("data-latex=", rendered)
        self.assertNotIn("instructure.com", rendered)

    def test_format_mc_html_fragment_dollar_tex_and_escaped_html(self) -> None:
        """Ingest HTML with $TeX$ and escaped tags becomes readable math."""
        raw = "&lt;p&gt;Find $x^2$ and \\(y=x\\)&lt;/p&gt;"
        rendered = format_mc_html_fragment(raw)
        self.assertIn('class="math-latex"', rendered)
        self.assertIn("data-latex=", rendered)
        self.assertIn("Find", rendered)
        self.assertNotIn("&lt;p&gt;", rendered)

    def test_format_mc_html_does_not_double_wrap_math_span(self) -> None:
        """Existing KaTeX spans are left as a single span."""
        raw = '<p>Find <span class="math-latex" data-latex="x^2">x^2</span></p>'
        rendered = format_mc_html_fragment(raw)
        self.assertEqual(rendered.count("math-latex"), 1)

    def test_format_mc_html_fragment_preserves_table(self) -> None:
        """Table HTML survives sanitization for live MC display."""
        raw = (
            "<p>Hours studied</p><table><tr><th>x</th><th>y</th></tr>"
            "<tr><td>1</td><td>2</td></tr></table>"
        )
        rendered = format_mc_html_fragment(raw)
        self.assertIn("<table>", rendered)
        self.assertIn("<td>", rendered)
        self.assertIn("Hours studied", rendered)

    def test_resolve_filebase_with_class_id(self) -> None:
        """IMSCC filebase tokens resolve against a class module-files root."""
        url = resolve_bank_image_url(
            "$IMS-CC-FILEBASE$/Uploaded%20Media/graph.png",
            class_id=5,
        )
        self.assertIn("/staff/class/5/module-files/web_resources/", url)

    def test_normalize_image_only_choices(self) -> None:
        """Image-only MC choices import with letter labels and option HTML."""
        payload = {
            "stem_html": "<p>Which graph matches?</p>",
            "points_possible": 1.0,
            "choices": [
                {"id": "a", "html": '<img src="https://example.com/a.png">', "correct": False},
                {"id": "b", "html": '<img src="https://example.com/b.png">', "correct": True},
                {"id": "c", "html": '<img src="https://example.com/c.png">', "correct": False},
                {"id": "d", "html": '<img src="https://example.com/d.png">', "correct": False},
            ],
            "correct_ids": ["b"],
        }
        live, err = normalize_bank_mc(
            question_id=99,
            bank_id=1,
            item_type="multiple_choice_question",
            payload=payload,
        )
        assert live is not None and err is None
        self.assertEqual(live["options"], ["A", "B", "C", "D"])
        self.assertEqual(live["correct_answer"], "B")
        self.assertIn("<img", live["options_html"][1])
        self.assertEqual(live["options_image_urls"][1], "https://example.com/b.png")

    def test_normalize_table_stem_renders_html(self) -> None:
        """Table stems keep HTML structure in text_html."""
        payload = {
            "stem_html": (
                "<p>This table shows values.</p><table><tr><th>Hours</th><th>Grade</th></tr>"
                "<tr><td>1</td><td>72</td></tr></table><p>Which domain is shown?</p>"
            ),
            "points_possible": 1.0,
            "choices": [
                {"id": "a", "html": "{1, 2, 3}", "correct": True},
                {"id": "b", "html": "{4, 5, 6}", "correct": False},
            ],
            "correct_ids": ["a"],
        }
        live, err = normalize_bank_mc(
            question_id=100,
            bank_id=1,
            item_type="multiple_choice_question",
            payload=payload,
        )
        assert live is not None and err is None
        self.assertIn("<table>", live["text_html"])
        self.assertIn("Hours", live["text_html"])


if __name__ == "__main__":
    unittest.main()
