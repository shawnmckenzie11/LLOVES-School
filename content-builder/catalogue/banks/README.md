# Module question banks

Licensed, tagged item corpora that feed the **practice-designer**.

One bank per course module (`catalogue/banks/<COURSE>/<MODULE>/`). Items are stable JSON records validated by `bank-item.schema.json`; the module manifest (`module.json`) lists process slots and item ids.

## Role in the pipeline

```
bank fill → practice-designer → human gate → batch gen
```

1. **Bank fill** — importers and curators add licensed stems (OpenStax, Ontario seed, MathNet, …) plus optional parametric families. Nelson Functions 11 is a **density oracle only**: it informs how deep and how many items a process needs; it does **not** contribute stems to student HTML.
2. **practice-designer** — selects and sequences from approved bank items into `practice-sequence.json` / practice sets.
3. **Human gate** — review status must reach `approved` (or `locked`) before student-facing batch generation.
4. **Batch gen** — specialists emit lesson artifacts only from permitted, student-HTML-allowed items.

## Nelson policy

- `source: nelson_functions_11`
- `permitted_use_status: excluded`
- `student_html_allowed: false`
- `stem_student: null` (never paste commercial stems)
- Use `density_notes` for pedagogy density / coverage hints only

## Parametric families

Welcome. Use `source: parametric` with a stable family id in `source_id` and a `checker_ref` when answers are deterministic. Instances may be expanded later by generators; the bank stores the family contract and seed exemplars.

## Layout

```
catalogue/banks/
  README.md
  bank-item.schema.json
  module-bank.schema.json
  MCF3M/
    M4/
      module.json
      items/*.json
```

Validate with `content-builder/scripts/validate_banks.py`.


## Practice bridge

Deterministic scripts map approved (or dry-run selected) bank items into lesson
candidate files without flipping `review_status` (Shawn’s human gate owns
`selected` → `approved` / `locked`):

| Script | Role |
|---|---|
| `scripts/banks_to_practice_candidates.py` | Emit `lessons/{COURSE}/{lesson_id}/bank-sourced-candidates.json` from the module bank. Defaults: `--course MCF3M --module 4 --lesson-id M4-L1-vertex-form`. Include `review_status` in `{approved, locked}`; add `--include-selected` for pre-gate dry-runs. Always omits Nelson / `permitted_use_status: excluded` / `student_html_allowed: false`. Does **not** overwrite hand-authored `question-candidates.json`. |
| `scripts/check_bank_coverage.py` | Report each `process_slots[].min_approved_items` vs counts of student-allowed approved/locked (and selected). Exit non-zero when approved coverage is below mins **and** the selected pool cannot cover; selected-only shortfalls warn and exit 0 unless `--strict-selected`. |

practice-designer prefers `bank-sourced-candidates.json` when present, then falls back to `question-candidates.json`.
