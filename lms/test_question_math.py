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
    rewrite_student_prompt_images,
    student_visible_bank_image_url,
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
        rendered_stem = " ".join(
            str(live.get(key) or "")
            for key in ("text_html", "image_url", "image_src", "stem_html")
        )
        for src in ("a.png", "b.png", "c.png", "d.png"):
            self.assertNotIn(src, rendered_stem)

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

    def test_glued_answer_formula_and_snapshot_leave_the_stem(self) -> None:
        """Answer snapshots and a matching formula appendix are not part of the stem."""
        from question_math import clean_question_stem_fields

        question = {
            "text": "Which equation matches the graph? y=2^{x}+3",
            "text_html": (
                "<p>Which equation matches the graph?</p>"
                '<img src="https://i.gyazo.com/snap.png" alt="snapshot">'
                '<span class="math-latex" data-latex="y=2^{x}+3">y=2^{x}+3</span>'
                "<table><tr><td>x</td><td>y</td></tr></table>"
                '<img src="/static/bank-graphs/parabola-grid.svg" alt="graph">'
            ),
            "options": ["y=2^{x}+3", "y=2^{x}", "y=x^{2}", "y=|x|"],
            "correct_answer": "A",
            "equation_latex": "y=2^{x}+3",
        }
        clean_question_stem_fields(question)
        self.assertIn("Which equation matches the graph?", question["text_html"])
        self.assertNotIn("gyazo.com", question["text_html"])
        self.assertNotIn("y=2^{x}+3", question["text_html"])
        self.assertIn("<table>", question["text_html"])
        self.assertIn("parabola-grid.svg", question["text_html"])
        self.assertNotIn("y=2^{x}+3", question["text"])
        self.assertEqual(question["equation_latex"], "y=2^{x}+3")

    def test_house_inline_tex_is_not_stripped_as_an_answer_relic(self) -> None:
        """Ingest house TeX stays when it is the question, not a pasted answer."""
        from question_math import clean_question_stem_fields

        question = {
            "text": r"Find $\frac{1}{2}$",
            "text_html": (
                '<p>Find <span class="math-latex" data-latex="\\frac{1}{2}">'
                r"$\frac{1}{2}$</span></p>"
            ),
            "options": [r"$\frac{1}{2}$", "$2$"],
            "correct_answer": "A",
        }
        clean_question_stem_fields(question)
        self.assertEqual(question["text"], r"Find $\frac{1}{2}$")
        self.assertIn(r"\frac{1}{2}", question["text_html"])

    def test_choice_image_is_not_the_stem_hero(self) -> None:
        """An MC choice image stays on the choice, including when it is correct.

        Shawn's M4 shape: the stem is an exponent product and the correct
        choice is an equation image of the simplified power. That image must
        not be copied onto ``image_url`` / ``image_src`` or into ``text_html``.
        """
        answer = "https://cdn.example/answer-5-3.png"
        distractor = "https://cdn.example/5-11.png"
        payload = {
            "stem_html": "<p>Simplify (5<sup>-4</sup>)(5<sup>7</sup>).</p>",
            "points_possible": 1.0,
            "choices": [
                {
                    "id": "a",
                    "html": f'<img class="equation_image" src="{answer}" alt="five cubed">',
                    "correct": True,
                },
                {
                    "id": "b",
                    "html": f'<img class="equation_image" src="{distractor}" alt="five to the eleven">',
                    "correct": False,
                },
            ],
            "correct_ids": ["a"],
        }
        live, err = normalize_bank_mc(
            question_id=320,
            bank_id=4,
            item_type="multiple_choice_question",
            payload=payload,
        )
        assert live is not None and err is None
        stem_fields = " ".join(
            str(live.get(key) or "")
            for key in ("text_html", "image_url", "image_src", "stem_html")
        )
        self.assertNotIn(answer, stem_fields)
        self.assertNotIn(distractor, stem_fields)
        self.assertNotIn("image_url", live)
        self.assertIn("Simplify", live["text_html"])
        self.assertIn(answer, live["options_html"][0])
        self.assertEqual(live["options_image_urls"][0], answer)
        self.assertEqual(live["correct_answer"], "A")

    def test_authored_stem_image_repeated_on_choices_stays(self) -> None:
        """A stem image Canvas also put on every choice stays in the stem."""
        from question_math import clean_question_stem_fields

        triangle = "https://cdn.example/files/10/preview"
        question = {
            "text": "Given the triangle above, if a = 9.",
            "text_html": (
                f'<img src="{triangle}" alt="triangle">'
                "<p>Given the triangle above, if a = 9.</p>"
            ),
            "stem_html": (
                f'<img src="{triangle}" alt="triangle">'
                "<p>Given the triangle above, if a = 9.</p>"
            ),
            "image_url": triangle,
            "options_html": [
                f'<img src="{triangle}" alt="triangle">',
                f'<img src="{triangle}" alt="triangle">',
            ],
            "options": ["A", "B"],
            "correct_answer": "A",
        }
        clean_question_stem_fields(question)
        self.assertIn(triangle, question["text_html"])
        self.assertIn(triangle, question["stem_html"])
        self.assertEqual(question["image_url"], triangle)
        self.assertIn("triangle above", question["text_html"])

    def test_promoted_choice_hero_is_removed(self) -> None:
        """A hero copied from a choice, with no image in the stem, is dropped."""
        from question_math import clean_question_stem_fields

        answer = "https://cdn.example/answer-5-3.png"
        question = {
            "text": "Simplify (5^-4)(5^7).",
            "text_html": "<p>Simplify (5<sup>-4</sup>)(5<sup>7</sup>).</p>",
            "image_url": answer,
            "image_src": answer,
            "options_html": [
                f'<img src="{answer}" alt="five cubed">',
                '<img src="https://cdn.example/5-11.png" alt="five to the eleven">',
            ],
            "options": ["A", "B"],
            "correct_answer": "A",
        }
        clean_question_stem_fields(question)
        self.assertNotIn("image_url", question)
        self.assertNotIn("image_src", question)
        self.assertIn("Simplify", question["text_html"])
        self.assertNotIn(answer, question["text_html"])
        self.assertIn(answer, question["options_html"][0])

    def test_image_identity_uses_the_full_path(self) -> None:
        """Folder, file id, and data URLs are not compared by file name alone."""
        from question_math import bank_image_identity

        self.assertNotEqual(
            bank_image_identity("https://canvas.example/files/10/preview"),
            bank_image_identity("https://canvas.example/files/11/preview"),
        )
        self.assertNotEqual(
            bank_image_identity("web_resources/U1/image.png"),
            bank_image_identity("web_resources/U2/image.png"),
        )
        self.assertNotEqual(
            bank_image_identity("data:image/png;base64,AAAA"),
            bank_image_identity("data:image/png;base64,BBBB"),
        )
        self.assertNotEqual(
            bank_image_identity("https://canvas.example/equation?mmlgz=one"),
            bank_image_identity("https://canvas.example/equation?mmlgz=two"),
        )
        self.assertEqual(
            bank_image_identity(
                "/staff/class/3/module-files/web_resources/U1/graph.png"
            ),
            bank_image_identity("web_resources/U1/graph.png"),
        )

    def test_stem_graph_that_is_not_a_choice_stays(self) -> None:
        """A question graph that no choice uses remains on the stem."""
        payload = {
            "stem_html": (
                "<p>From the graph, what is the vertex?</p>"
                '<img src="/static/bank-graphs/parabola-grid.svg" alt="parabola">'
            ),
            "points_possible": 1.0,
            "choices": [
                {"id": "a", "html": "(0, c)", "correct": True},
                {"id": "b", "html": "(h, k)", "correct": False},
            ],
            "correct_ids": ["a"],
        }
        live, err = normalize_bank_mc(
            question_id=321,
            bank_id=4,
            item_type="multiple_choice_question",
            payload=payload,
        )
        assert live is not None and err is None
        self.assertIn("parabola-grid.svg", live["text_html"])
        self.assertIn("vertex", live["text_html"])

    def test_qti_import_keeps_choice_images_off_the_stem(self) -> None:
        """Canvas QTI stores a choice image on the choice, not the stem."""
        import xml.etree.ElementTree as ET

        from ingest import parse_qti_item

        answer = "https://cdn.example/answer-5-3.png"
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <questestinterop xmlns="http://www.imsglobal.org/xsd/ims_qtiasiv1p2">
          <item ident="m4-simplify" title="Simplify">
            <itemmetadata><qtimetadata><qtimetadatafield>
              <fieldlabel>question_type</fieldlabel>
              <fieldentry>multiple_choice_question</fieldentry>
            </qtimetadatafield></qtimetadata></itemmetadata>
            <presentation>
              <material><mattext texttype="text/html">&lt;p&gt;Simplify (5&lt;sup&gt;-4&lt;/sup&gt;)(5&lt;sup&gt;7&lt;/sup&gt;).&lt;/p&gt;</mattext></material>
              <response_lid ident="response1" rcardinality="Single">
                <render_choice>
                  <response_label ident="a1">
                    <material><mattext texttype="text/html">&lt;img src="{answer}" alt="five cubed"&gt;</mattext></material>
                  </response_label>
                  <response_label ident="a2">
                    <material><mattext texttype="text/html">&lt;img src="https://cdn.example/5-11.png" alt="five to the eleven"&gt;</mattext></material>
                  </response_label>
                </render_choice>
              </response_lid>
            </presentation>
            <resprocessing>
              <outcomes><decvar maxvalue="100" minvalue="0" varname="SCORE" vartype="Decimal"/></outcomes>
              <respcondition continue="No">
                <conditionvar><varequal respident="response1">a1</varequal></conditionvar>
                <setvar action="Set" varname="SCORE">100</setvar>
              </respcondition>
            </resprocessing>
          </item>
        </questestinterop>
        """
        root = ET.fromstring(xml)
        item = next(node for node in root.iter() if node.tag == "item" or node.tag.endswith("}item"))
        parsed = parse_qti_item(item)
        payload = parsed["payload"]
        self.assertNotIn(answer, payload["stem_html"])
        self.assertIn("Simplify", payload["stem_html"])
        self.assertIn(answer, payload["choices"][0]["html"])
        self.assertTrue(payload["choices"][0]["correct"])
        live, err = normalize_bank_mc(
            question_id=322,
            bank_id=4,
            item_type=parsed["item_type"],
            payload=payload,
        )
        assert live is not None and err is None
        stem_fields = " ".join(
            str(live.get(key) or "")
            for key in ("text_html", "image_url", "image_src")
        )
        self.assertNotIn(answer, stem_fields)
        self.assertIn(answer, live["options_html"][0])

    def test_staff_bank_view_keeps_an_authored_stem_image(self) -> None:
        """The teacher bank card keeps a stem image that choices also use."""
        from bank_edit import serialize_staff_question

        triangle = "https://cdn.example/files/10/preview"
        stored_stem = (
            f'<img src="{triangle}" alt="triangle">'
            "<p>Given the triangle above, if a = 9.</p>"
        )
        question = {
            "id": 1270,
            "bank_id": 4,
            "import_key": "triangle",
            "item_type": "multiple_choice_question",
            "title": "Triangle",
            "payload": {
                "stem_html": stored_stem,
                "choices": [
                    {"id": "a", "html": f'<img src="{triangle}" alt="triangle">', "correct": True},
                    {"id": "b", "html": f'<img src="{triangle}" alt="triangle">', "correct": False},
                ],
            },
        }
        view = serialize_staff_question(question, None)
        self.assertIn(triangle, view["stem_html"])
        self.assertIn("triangle above", view["stem_html"])
        self.assertIn(triangle, view["choices"][0]["html"])
        self.assertEqual(question["payload"]["stem_html"], stored_stem)

    def test_student_visible_bank_image_url_rewrites_staff_path(self) -> None:
        """Staff module-file URLs become the student-accessible twin."""
        staff = "/staff/class/9/module-files/web_resources/diagram.png"
        self.assertEqual(
            student_visible_bank_image_url(staff),
            "/api/classes/9/module-files/web_resources/diagram.png",
        )
        self.assertEqual(
            student_visible_bank_image_url("/static/bank-graphs/parabola-grid.svg"),
            "/static/bank-graphs/parabola-grid.svg",
        )

    def test_rewrite_student_prompt_images_covers_stem_and_options(self) -> None:
        """Hero, stem HTML, and option images all leave the staff path."""
        staff = "/staff/class/4/module-files/web_resources/g.png"
        rewritten = rewrite_student_prompt_images(
            {
                "image_url": staff,
                "text_html": f'<p>Refer to the graph.</p><img src="{staff}">',
                "options_html": [f'<img src="{staff}">', "B"],
                "options_image_urls": [staff, ""],
                "key": "A",
            }
        )
        public = "/api/classes/4/module-files/web_resources/g.png"
        self.assertEqual(rewritten["image_url"], public)
        self.assertIn(public, rewritten["text_html"])
        self.assertNotIn("/staff/class/", rewritten["text_html"])
        self.assertEqual(rewritten["options_html"][0], f'<img src="{public}">')
        self.assertEqual(rewritten["options_image_urls"][0], public)
        self.assertEqual(rewritten["key"], "A")


if __name__ == "__main__":
    unittest.main()
