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
