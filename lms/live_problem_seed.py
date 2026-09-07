"""Seed MCF3M live problems from Ministry examples plus original contest-style items.

Does not copy CEMC/Waterloo contest wording. Ministry stems come from
``lms/seeds/mcf3m_expectations.json`` ``examples`` arrays.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from live_class_constants import PROCESS_KEYS
from paths import MCF3M_EXPECTATIONS

# Original multi-step items (not routine plug-in). Genre is contest-like; wording is ours.
ORIGINAL_CONTEST: list[dict[str, Any]] = [
    {
        "ontario_code": "MCF3M",
        "module_hint": "A",
        "kind": "contest",
        "title": "Fence that maximizes a rectangular garden",
        "stem_html": (
            "<p>A gardener has 40 m of fencing for three sides of a rectangular plot "
            "against a long shed (the shed is the fourth side). Let <em>x</em> be the "
            "side perpendicular to the shed.</p>"
        ),
        "task_html": (
            "<p>Write the enclosed area as a quadratic in <em>x</em>. Without calculus, "
            "find the dimensions that maximize area and the maximum area. Then explain "
            "why the other root of the area equation is not a usable garden.</p>"
        ),
        "diagram_note": "Sketch the shed as one long side; two widths and one length of fence.",
        "source": "original",
        "license": "original",
        "expectation_codes": ["A3.1", "A2.8"],
        "processes": ["problem_solving", "representing", "reasoning_proving"],
        "sort_order": 100,
    },
    {
        "ontario_code": "MCF3M",
        "module_hint": "A",
        "kind": "contest",
        "title": "Two numbers, product 91",
        "stem_html": (
            "<p>Two numbers differ by 6. Their product is 91.</p>"
        ),
        "task_html": (
            "<p>Set up a quadratic, solve it, and interpret both roots in context. "
            "Which representation (factored form vs graph) makes the unused root obvious?</p>"
        ),
        "diagram_note": "",
        "source": "original",
        "license": "original",
        "expectation_codes": ["A1.4", "A1.5"],
        "processes": ["problem_solving", "reasoning_proving"],
        "sort_order": 101,
    },
    {
        "ontario_code": "MCF3M",
        "module_hint": "B",
        "kind": "contest",
        "title": "Views: exponential vs linear",
        "stem_html": (
            "<p>A clip starts at 800 views. Model A: views grow 15% per week. "
            "Model B: views increase by 120 each week.</p>"
        ),
        "task_html": (
            "<p>Write both models. Find the first whole week when Model A exceeds 2000 views. "
            "Compare with Model B at that week. Which model is more realistic after 12 weeks, and why?</p>"
        ),
        "diagram_note": "",
        "source": "original",
        "license": "original",
        "expectation_codes": ["B1.6", "B2.3"],
        "processes": ["problem_solving", "connecting", "representing"],
        "sort_order": 200,
    },
    {
        "ontario_code": "MCF3M",
        "module_hint": "B",
        "kind": "contest",
        "title": "Compound vs simple over 18 months",
        "stem_html": (
            "<p>$2500 is invested for 18 months. Option 1: 4.8% per annum compounded monthly. "
            "Option 2: the same annual rate as simple interest.</p>"
        ),
        "task_html": (
            "<p>Compute both future values. How large is the difference? "
            "What would change if compounding were quarterly instead?</p>"
        ),
        "diagram_note": "",
        "source": "original",
        "license": "original",
        "expectation_codes": ["B3.1", "B3.2"],
        "processes": ["problem_solving", "tools_strategies", "communicating"],
        "sort_order": 201,
    },
    {
        "ontario_code": "MCF3M",
        "module_hint": "C",
        "kind": "contest",
        "title": "Ferris wheel first at 20 m",
        "stem_html": (
            "<p>A Ferris wheel has radius 12 m. Its centre is 14 m above the ground. "
            "One revolution takes 20 s. A rider starts at the lowest point.</p>"
        ),
        "task_html": (
            "<p>Write a sine model for height as a function of time. "
            "Find the first time the rider is 20 m above the ground. "
            "State amplitude, period, and a reasonable domain for one ride.</p>"
        ),
        "diagram_note": "Side view: ground, centre, lowest and 20 m marks.",
        "source": "original",
        "license": "original",
        "expectation_codes": ["C3.1", "C3.3"],
        "processes": ["problem_solving", "representing", "connecting"],
        "sort_order": 300,
    },
    {
        "ontario_code": "MCF3M",
        "module_hint": "C",
        "kind": "contest",
        "title": "Tide height 3.0 m twice",
        "stem_html": (
            "<p>A simplified tide model is <em>h(t) = 1.5 sin(30°·t) + 2.4</em>, "
            "where <em>h</em> is height in metres and <em>t</em> is hours after midnight.</p>"
        ),
        "task_html": (
            "<p>Find two times in [0, 12] when the height is 3.0 m. "
            "Explain how the sine equation produces two solutions in that window.</p>"
        ),
        "diagram_note": "",
        "source": "original",
        "license": "original",
        "expectation_codes": ["C3.2", "C3.3"],
        "processes": ["problem_solving", "reasoning_proving"],
        "sort_order": 301,
    },
]


# Lesson-keyed bank rows. ``staff_note`` is seed-only (not stored / not on student slides).
ORIGINAL_LESSON_ITEMS: list[dict[str, Any]] = [
    {
        "ontario_code": "MCF3M",
        "module_hint": "A/M1C1",
        "kind": "warmup",
        "title": "Fair equal groups — notice and wonder",
        "stem_html": (
            "<p>Mr. M wants groups that feel fair: same size, nobody left over. "
            "Attendance of 12 and 18 both “give options.” Attendance of 16 is his favourite.</p>"
        ),
        "task_html": (
            "<p>What might “options” mean for 12 versus 18? What do you notice? "
            "What do you wonder? (Do not yet invent a quick-check rule.)</p>"
        ),
        "diagram_note": "",
        "source": "original",
        "license": "original",
        "expectation_codes": ["A2.1"],
        "processes": ["connecting", "communicating"],
        "sort_order": 40,
    },
    {
        "ontario_code": "MCF3M",
        "module_hint": "A/M1C1",
        "kind": "contest",
        "title": "How can Mr. M quickly check if his class is jigsaw-able?",
        "stem_html": (
            "<p>Mr. M likes attendance that splits into equal groups. "
            "12 and 18 give options; 16 is his favourite because he can run 4 equal groups. "
            "(The seating-grid visual lives on the previous slide.)</p>"
        ),
        "task_html": (
            "<p>With <em>n</em> = today’s attendance, how can he <strong>quickly</strong> tell "
            "if the class is jigsaw-able? Check <em>n</em> = 12, 16, 18. "
            "Use function language if it helps (e.g. <em>f(n)</em>).</p>"
        ),
        "diagram_note": (
            "Shawn’s 4×4 initials grid is on the previous slide; do not duplicate in this deck."
        ),
        "source": "original",
        "license": "original",
        "expectation_codes": ["A2.1", "A2.2", "A1.4"],
        "processes": ["problem_solving", "connecting", "representing"],
        "sort_order": 41,
    },
    {
        "ontario_code": "MCF3M",
        "module_hint": "A/M1C1",
        "kind": "standard",
        "title": "Equal-group factorizations of 12 and 18",
        "stem_html": (
            "<p>List the ways 12 students can sit in equal groups of the same size "
            "(with none left over). Do the same for 18.</p>"
        ),
        "task_html": (
            "<p>Which sizes appear for both 12 and 18? Which appear for only one of them?</p>"
        ),
        "diagram_note": "",
        "source": "original",
        "license": "original",
        "expectation_codes": ["A2.1"],
        "processes": ["representing", "connecting"],
        "sort_order": 42,
    },
    {
        "ontario_code": "MCF3M",
        "module_hint": "A/M1C1",
        "kind": "standard",
        "title": "Why 16 supports four equal groups",
        "stem_html": (
            "<p>Sixteen students can run four equal groups. Write 16 as a product that "
            "makes those four groups obvious, and also as a square.</p>"
        ),
        "task_html": (
            "<p>How does the product picture match “four groups of four”? "
            "How does the square picture match a 4-by-4 seating grid?</p>"
        ),
        "diagram_note": "",
        "source": "original",
        "license": "original",
        "expectation_codes": ["A1.4", "A2.2"],
        "processes": ["representing", "connecting"],
        "sort_order": 43,
    },
    {
        "ontario_code": "MCF3M",
        "module_hint": "A/M1C1",
        "kind": "standard",
        "title": "f(n) for number of groups of 4",
        "stem_html": (
            "<p>Let <em>n</em> be today’s attendance. Let <em>f(n)</em> be the number of "
            "groups of 4 you can make if every group has exactly 4 students and nobody "
            "is left over.</p>"
        ),
        "task_html": (
            "<p>When is <em>f(n)</em> a whole number? Evaluate <em>f(12)</em>, <em>f(16)</em>, "
            "and <em>f(18)</em> — or explain why a value is not defined.</p>"
        ),
        "diagram_note": "",
        "source": "original",
        "license": "original",
        "expectation_codes": ["A2.1", "A2.2"],
        "processes": ["representing", "problem_solving"],
        "sort_order": 44,
    },
    {
        "ontario_code": "MCF3M",
        "module_hint": "A/M1C1",
        "kind": "standard",
        "title": "Both roots of x² = 16 in a seating story",
        "stem_html": (
            "<p>The equation <em>x</em><sup>2</sup> = 16 has two real roots. "
            "One can describe the side length of a square seating grid. "
            "The other is the opposite number.</p>"
        ),
        "task_html": (
            "<p>Which root is a usable class-list / grid size? What does the unused root "
            "mean (or fail to mean) in this story?</p>"
        ),
        "diagram_note": "",
        "source": "original",
        "license": "original",
        "expectation_codes": ["A1.4", "A1.5"],
        "processes": ["representing", "reasoning_proving"],
        "sort_order": 45,
    },
    {
        "ontario_code": "MCF3M",
        "module_hint": "A/M1C1",
        "kind": "standard",
        "title": "15 versus 16: equal groups without a shortcut identity",
        "stem_html": (
            "<p>Can 16 students sit in equal groups of the same size with none left over? "
            "Can 15? Check by listing group sizes (pairs, threes, …), not by rewriting "
            "one number as a difference of squares.</p>"
        ),
        "task_html": (
            "<p>What equal-group sizes work for 16? What works for 15? "
            "Stay with factors and leftovers — do not use 16 − 1 as an algebraic trick.</p>"
        ),
        "diagram_note": "",
        "source": "original",
        "license": "original",
        "expectation_codes": ["A2.1"],
        "processes": ["problem_solving", "representing"],
        "sort_order": 46,
    },
    {
        "ontario_code": "MCF3M",
        "module_hint": "A/M1C2",
        "kind": "contest",
        "title": "Groupable after one student leaves — why?",
        "stem_html": (
            "<p>Some class sizes that are perfectly group-able stay groupable in a "
            "different way when one student is absent.</p>"
        ),
        "task_html": (
            "<p>Why? Try a few sizes (include 16 → 15). This is next week’s team follow-up, "
            "not today’s contest.</p>"
        ),
        "diagram_note": "",
        "staff_note": (
            "Teacher only: non-real answers; Po-Shen Loh / 3Blue1Brown difference of "
            "squares in week 2. Keep off student slides."
        ),
        "source": "original",
        "license": "original",
        "expectation_codes": ["A1.4", "A1.5"],
        "processes": ["problem_solving", "reasoning_proving", "connecting"],
        "sort_order": 70,
    },
]


def _strand_processes(strand: str) -> list[str]:
    """Default supporting processes for a Ministry example in this strand.

    Args:
        strand: ``A``, ``B``, or ``C``.
    """
    if strand == "A":
        return ["representing", "tools_strategies"]
    if strand == "B":
        return ["connecting", "tools_strategies"]
    return ["representing", "connecting"]


def ministry_example_problems(
    seed_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Build warmup/standard live problems from MCF3M ``examples`` arrays.

    Args:
        seed_path: Expectations JSON (default catalog path).

    Returns:
        Problem dicts ready for ``SchoolDB.upsert_live_problem``.
    """
    path = seed_path or MCF3M_EXPECTATIONS
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    warmup_used: set[str] = set()
    sort_base = {"A": 10, "B": 20, "C": 30}
    for strand_block in payload.get("strands") or []:
        strand = str(strand_block.get("code") or "").strip().upper()[:1]
        if strand not in {"A", "B", "C"}:
            continue
        idx = 0
        for spec in strand_block.get("specific") or []:
            examples = spec.get("examples") or []
            if not examples:
                continue
            code = str(spec.get("code") or "")
            for example in examples:
                text = str(example or "").strip()
                if not text:
                    continue
                idx += 1
                is_warmup = strand not in warmup_used
                if is_warmup:
                    warmup_used.add(strand)
                kind = "warmup" if is_warmup else "standard"
                processes = ["problem_solving"] if kind == "contest" else _strand_processes(strand)
                if kind == "warmup":
                    processes = ["connecting", "communicating"]
                out.append(
                    {
                        "ontario_code": "MCF3M",
                        "module_hint": strand,
                        "kind": kind,
                        "title": f"{code} ministry example" if code else "Ministry example",
                        "stem_html": f"<p>{_escape_html(text)}</p>",
                        "task_html": "",
                        "diagram_note": "",
                        "source": "ontario_curriculum_example",
                        "license": "ontario_curriculum",
                        "expectation_codes": [code] if code else [],
                        "processes": processes,
                        "sort_order": sort_base.get(strand, 90) * 10 + idx,
                    }
                )
    return out


def _escape_html(text: str) -> str:
    """Escape Ministry example text for stem HTML.

    Args:
        text: Plain example stem.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def default_live_problems(seed_path: Path | None = None) -> list[dict[str, Any]]:
    """Return the starter bank: Ministry examples, lesson-keyed items, contest items.

    Args:
        seed_path: Optional expectations JSON override.
    """
    return ministry_example_problems(seed_path) + ORIGINAL_LESSON_ITEMS + ORIGINAL_CONTEST


def default_quick_phrases() -> list[dict[str, Any]]:
    """Return starter clickable evidence phrases for Team Challenge.

    Returns:
        Phrase dicts with ``process_key``, ``label``, ``description``, ``category``.
    """
    rows: list[dict[str, Any]] = [
        {
            "process_key": "problem_solving",
            "label": "Proposed a strategy",
            "description": "Offered a plan or approach for the task.",
            "category": "team_problem",
            "sort_order": 10,
        },
        {
            "process_key": "problem_solving",
            "label": "Tried a different approach",
            "description": "Adapted after getting stuck.",
            "category": "team_problem",
            "sort_order": 11,
        },
        {
            "process_key": "reasoning_proving",
            "label": "Made a conjecture",
            "description": "Stated a claim before verifying it.",
            "category": "team_problem",
            "sort_order": 20,
        },
        {
            "process_key": "reasoning_proving",
            "label": "Justified a claim",
            "description": "Gave reasons, a proof sketch, or a counter-example.",
            "category": "team_problem",
            "sort_order": 21,
        },
        {
            "process_key": "reflecting",
            "label": "Checked reasonableness",
            "description": "Judged whether a result made sense.",
            "category": "team_problem",
            "sort_order": 30,
        },
        {
            "process_key": "reflecting",
            "label": "Monitored their thinking",
            "description": "Noticed an error and adjusted.",
            "category": "team_problem",
            "sort_order": 31,
        },
        {
            "process_key": "tools_strategies",
            "label": "Chose a useful tool",
            "description": "Selected technology, a diagram, or an algorithm.",
            "category": "team_problem",
            "sort_order": 40,
        },
        {
            "process_key": "connecting",
            "label": "Connected to prior work",
            "description": "Linked to an earlier concept or a real context.",
            "category": "team_problem",
            "sort_order": 50,
        },
        {
            "process_key": "representing",
            "label": "Used a representation",
            "description": "Used a graph, table, equation, or diagram.",
            "category": "team_problem",
            "sort_order": 60,
        },
        {
            "process_key": "communicating",
            "label": "Explained clearly",
            "description": "Used precise mathematical language.",
            "category": "team_problem",
            "sort_order": 70,
        },
        {
            "process_key": "communicating",
            "label": "Shared with the team",
            "description": "Communicated the idea so teammates could use it.",
            "category": "team_problem",
            "sort_order": 71,
        },
        {
            "process_key": "problem_solving",
            "label": "Compared strategies",
            "description": "Class compared two approaches.",
            "category": "consolidation",
            "sort_order": 110,
        },
        {
            "process_key": "reasoning_proving",
            "label": "Defended a solution",
            "description": "Justified the team's answer to the class.",
            "category": "consolidation",
            "sort_order": 120,
        },
        {
            "process_key": "reflecting",
            "label": "Named what they learned",
            "description": "Reflected on the process or a misconception.",
            "category": "consolidation",
            "sort_order": 130,
        },
        {
            "process_key": "connecting",
            "label": "Linked to the lesson goal",
            "description": "Connected the task to today's learning goal.",
            "category": "consolidation",
            "sort_order": 140,
        },
        {
            "process_key": "communicating",
            "label": "Summarized the method",
            "description": "Restated the solution path for others.",
            "category": "consolidation",
            "sort_order": 150,
        },
        {
            "process_key": "problem_solving",
            "label": "Stuck productively",
            "description": "Stayed with a hard part of the task.",
            "category": "general",
            "sort_order": 200,
        },
    ]
    known = set(PROCESS_KEYS)
    for row in rows:
        if row["process_key"] not in known:
            raise ValueError(f"unknown process_key {row['process_key']}")
    return rows
