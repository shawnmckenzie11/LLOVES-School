# Content Builder — pipeline readiness

Status as of 2026-09-09 on branch `content-builder`. Companion to `content-builder/README.md`.

## Ready

| Piece | Evidence |
|---|---|
| Isolated layout | `content-builder/` separate from `lms/`; no IMSCC in git |
| Contracts | `catalogue/contracts/*.schema.json` + onboarding.md |
| Specialist prompts | `.cursor/agents/*` + `content-builder-*.mdc` rules |
| MCF3M onboarding | `packages/MCF3M-builder-input/` imported into `catalogue/onboarding/MCF3M/` |
| Pilot lesson artifacts | `lessons/MCF3M/M4-L1-vertex-form/` (brief through verify-report) |
| Compile + checks | `scripts/build_lesson.py`, `check_feedback.py`, `verify_lesson.py` — PASS after parent patches |
| Teacher review | `scripts/serve_review.py` → `/review/` and compiled HTML under `/build/` |
| v1 failure fixture | `fixtures/M4-L1-vertex-form-v1/` + CHECKLIST.md |

## Not ready / out of scope

| Piece | Notes |
|---|---|
| Cursor Cloud Agents on this repo | Cursor’s connected GitHub account cannot access `LLOVES-School` yet |
| GitHub MCP connector in Cursor | Fails with malformed Authorization header; use `gh` CLI instead |
| Bulk course generation | Onboarding handoff says stop before bulk gen; one reviewed brief + pilot first |
| WeBWorK conversion | Deferred until pipeline is stable |
| LMS / Fly / IMSCC packaging | Admin upload path; builder does not write packs into git |
| Nelson bank stems in student HTML | Licence-excluded; pedagogy benchmark only |

## Question banks

Module question banks live at `catalogue/banks/`. They are the licensed, tagged corpus that feeds practice-designer (`bank fill → practice-designer → human gate → batch gen`). Nelson is density-oracle only (no stems in `stem_student`). Schemas: `bank-item.schema.json`, `module-bank.schema.json`. Seed: `catalogue/banks/MCF3M/M4/`. Validate with `scripts/validate_banks.py`.

### Banks → practice bridge

- `scripts/banks_to_practice_candidates.py` writes `lessons/{COURSE}/{lesson_id}/bank-sourced-candidates.json` from student-HTML-allowed bank items (`approved`/`locked` by default; `--include-selected` for dry-run). Hand-authored `question-candidates.json` is left alone.
- `scripts/check_bank_coverage.py` checks `process_slots` mins vs approved/locked (selected warns, exit 0 unless `--strict-selected`).
- **Human gate:** do not flip `review_status` to `approved`/`locked` in automation — Shawn owns selected→approved. M4 pilot currently has student-allowed items at `selected`; bridge dry-runs use `--include-selected` until that gate lands.
- practice-designer prefers `bank-sourced-candidates.json` when present.

## Astra → Cursor production path

1. Astra emits an implementation plan matching `docs/astra-cursor-handoff.md` / `implementation-plan.schema.json`.
2. Parent Cursor agent validates the plan against contracts and onboarding.
3. Parent runs the specialist sequence in `content-builder-parent.mdc`.
4. Deterministic scripts build and verify.
5. Shawn reviews at `:8790` before any merge to `main` (LMS deploy is separate).

## First raise target

MCF3M **M4-L1** against Nelson Functions 11 §4.1 depth/style bar — see `docs/m4-l1-nelson-gap-analysis.md`.
