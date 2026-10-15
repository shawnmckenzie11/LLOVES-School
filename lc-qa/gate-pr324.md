# Gate report — PR #324 (MCK-230 Participation data)

## Ops verdict: NO-GO (initial)

Participation tab fractions and module rollups did not match production rules for question types, catch-up credit, test-run exclusion, and column headers.

## Findings and resolution

| Priority | Finding | Fix |
|----------|---------|-----|
| HIGH | Denominator counted MC only; rank (and other types) omitted — prod classes 9/10 M2 showed wrong `/N`. | `participation_weight.py` + `staff_grades.js` count MC, numeric, text, rank (and aligned prompt kinds). Denominator = in-scope deck items with ≥1 live answer from a real student on a real run. Removed MC-only hint in `staff/course.html`. Prod-like tests: class 9 M2 `/5`, class 10 M2 `/6`. |
| HIGH | Catch-up did not credit session cell numerator. | `participation_individual_session_cells` counts live + catch-up answers on counted deck items; catch-up never adds to denominator. Tests: catch-up credits numerator; catch-up-only excluded from denominator. |
| MED | Module rollup could exceed 100% before UI clamp. | Module answered uses `participation_student_module_answered` over `slot×question_key` pairs in the denominator set (no `Math.min` clamp on score). |
| MED | Test runs must not appear on the grid. | `participation_exclusion` + session meta SQL; grid API omits non-real runs. |
| LOW | Column headers need slot + Toronto date; bad timestamps must not default to today. | `format_participation_session_header` on every session column; unparseable date → `—`. |

## Verification

- `python3 -m unittest` participation suites (`test_mck230_participation`, `test_participation_weight*`, `test_participation_exclusion_mck230`)
- CI on branch `cursor/mck-230-participation-data-35a0` (see PR for head SHA)
