# M4-L1 v1 failure audit

Captured **2026-09-09** from the compiled student page, authoring JSON, `build_lesson.py`, `lesson.css`, `review/index.html`, and `.cursor/rules/content-builder-*.mdc`. Do not treat the later revised lesson as this baseline.

Fixture: `content-builder/fixtures/M4-L1-vertex-form-v1/`  
Screenshots: `screenshots/desktop-minds-on.png`, `desktop-action.png`, `desktop-consolidation.png`, plus phone captures.

Fix each failure at the **origin** listed, not by polishing only this HTML.

## 1. Developer-facing language in student instructions

| Example (student view) | Origin | Fix at |
|---|---|---|
| “The graph tool is the local board (parabola, vertex marker labelled (h, k)… It is not a required GeoGebra page.” | `instruction.json` `action.interactive_bridge` plus instruction-author mixing engine notes into student prose | Language standard + `student_content` vs `teacher_notes`; renderer whitelist |
| Meta line `MCF3M · Module 4 · M4-L1-vertex-form` | `build_lesson.py` dumps `course_code` + `lesson_id` | Renderer: student title only; ids stay in provenance |
| Footer “Graph tool: JSXGraph (MIT)… Queen’s Printer excerpt via the verified seed. No student login or remote computation.” | `build_lesson.py` hard-coded `attr` paragraph | Provenance credits field; student sees tidy source labels, not runtime policy |
| “Worked reading (OpenStax College Algebra 2e, §5.1, Figure 5 paragraph, CC BY-NC-SA 4.0).” inside the example | instruction-author stuffed licence into the worked reading | `provenance` block; student copywriter; credits component |
| Practice 1 parenthetical: “Adapted from OpenStax… Their wording asks about writing a quadratic in ‘standard form’…” | Same mixing | Provenance + a short Ontario naming sentence in student copy, not a retrieval note |
| Review page dumps `rank`, `reason`, GeoGebra analogue into the iframe-adjacent cards (ok for teacher; leaked into student via instruction) | `review/index.html` vs student renderer sharing one blob | Distinct views; student renderer never reads `reason` / `rank` |

## 2. Missing explanations or abrupt transitions

| Example | Origin | Fix at |
|---|---|---|
| Minds On: “You already know… This lesson is about reading…” then a task with no why-this-equation-now | instruction-author; no hook; brief has sequence but no student question | lesson-director central question + hook-curator; copywriter |
| Action opens with a three-sentence itinerary (“First you will follow… Then you will use the graph tool… After that, two practice questions”) | instruction-author itinerary voice | Language standard: do the next task; do not narrate the timetable |
| Labels “Prediction.” / “Action.” / “Interpretation.” as headings inside a wall of prose | instruction-author restating the spec’s field names | Interaction copy is task language; designer owns sequence, copywriter owns wording |
| Consolidation: “A fresh case.” then “What to remember” as a bare paragraph, not a heading | schema has one `student` string; renderer `_para` has no heading types | student_content blocks (`p`, `h2`, `task`, `example`) |
| Action “Check” visually stranded on its own line after the GeoGebra disclaimer | `_para` + long paragraphs; no component for task steps | Copy + layout components |

## 3. Skills introduced without enough demonstration and practice

| Skill | What v1 actually does | Origin | Fix at |
|---|---|---|---|
| Read vertex / axis / max-min from `a(x−h)²+k` | One OpenStax reading, then sliders, then two practice items (verbal + expand) | question-curator offers 12; author used 4; schema has no worked→guided→core→additional fade | practice-designer disposition of **all 12** + practice schema default |
| Plus-inside `(x+3)` | Diagnostic prompt only; no second varied worked example; no partial example to finish | instruction schema `guided_example` is a single string | Deliberate-repetition default in schema |
| Expand vertex form and compare graphs (A2.7) | Ministry sample as Practice 2, unworked; live expansion on the board can skip the algebra | Single practice field; no staged hints | practice-designer + formative-feedback-designer |
| Slider investigation | Predict/act/interpret in prose; no submit, no “try another” | interaction-spec has no check cycle | feedback contract + reusable cycle component |

## 4. Interactives without a clear task or useful feedback

| Example | Origin | Fix at |
|---|---|---|
| Three sliders + Match / Starting / Fresh with no Submit and no check | engineer implemented a sandbox; spec never required evidence | interaction-designer + formative-feedback-designer contract |
| Fresh case button can show Consolidation’s graph during Action | engineer toolbar convenience | Director sequence + hide until consolidation / “try another” pool |
| No distinction of “h right, k wrong” vs “opens the wrong way” | No checker | Feedback rules + vertex-form checker, independent of JSXGraph wrapper |
| Paper fallback duplicated in Action prose and `<details>` | spec `fallback` + instruction restating it | One student fallback block; teacher fallback stays in teacher_notes |

## 5. Weak visual hierarchy

| Example | Origin | Fix at |
|---|---|---|
| Grey tab strip, untitled walls of 18px body text, no example/practice chrome | `lesson.css` is a thin sheet; no design owner | visual-experience-designer design system |
| Equations as ordinary `<p>`, mixed `^2` and `²` | renderer escape-as-paragraphs; no equation component | Equation block + KaTeX or well-styled MathML/plain |
| Board, sliders, table stacked with equal weight | engineer dump | Component layout: task, graph, evidence, actions |
| Teacher review: stacked JSON/pre, unlabeled ranks, iframe below a long candidate list | `review/index.html` inline styles | Separate teacher view in the same design system |
| Phone (390px): long lines, range inputs, live equations wrap; no inspected states for hints/errors (none exist) | no responsive spec | Visual designer + browser checks in verifier |

## 6. Openers with little reason to care

| Example | Origin | Fix at |
|---|---|---|
| “You already know how stretching, flipping, and sliding change the graph of y = x^2.” | instruction-author activating prerequisite as the hook | hook-curator: three ranked proposals; director selects one context |
| No return to the opener in Consolidation | no hook field in schema | Hook must name `returns_in`; copywriter + verifier |

## Candidate disposition gap (all 12)

Existing `question-candidates.json` already has 12 items. v1 **student HTML used only**: diagnostic rank 2, guided rank 1, practice ranks 1–2 (Ministry), spec fresh case. Unused: Nelson diagnostics/practice/transfer (licence), A2.6 sample, OpenStax expand-and-table, OpenStax range, OpenStax family graphs, MathNet 0gz2. Practice-designer must assign each; exclusion needs a reason.

## Rule and schema origins (why the next lesson would repeat this)

- `content-builder-student-language.mdc` is a **word-problem STAR note**, not a language standard. No field split.
- `instruction.json` schema **mixes** student, teacher, and source in the same strings.
- `build_lesson.py` concatenates those strings into HTML; it cannot refuse internal fields.
- Pipeline is mapper → curator ∥ designer → author → engineer → verifier: **no** practice fade, **no** feedback specialist, **no** visual owner, **no** hook.
- Interaction rule requires predict/act/interpret **prompts**, not check/feedback/retry.

## Acceptance for the revised system (from the revision plan)

Pilot is complete when internal notes cannot leak; the lesson has a recognizable question; each new process has example → variation → partial → independent → retrieval; interactive feedback is mathematically correct; styling is consistent including mobile/error; the hook returns at the end; approved voice lives in writer exemplars. Then run one substantially different lesson (e.g. trigonometry) before regenerating the course.
