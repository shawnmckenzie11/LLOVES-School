# MCF3M onboarding integration map

Baseline on `content-builder-git-onboarding-test`, 9 September 2026. Storage stays under `content-builder/` JSON (not LMS sqlite). Smallest extension: `catalogue/onboarding/` plus import/review/context scripts. No second builder.

| Concern | Present / partial / absent | Where it lives now | Onboarding adapter |
|---|---|---|---|
| Lesson identity | Partial | Pilot folder `lessons/MCF3M/M4-L1-vertex-form/` (`lesson_id` `M4-L1-vertex-form`, module 4, lesson 1). No Drive IDs. No 39-lesson manifest. | Match Drive folder ID, else unique `MCF3M` + module + lesson number. Attach `MCF3M-M4L1` + Drive `1HIEARbCbXeZFsSUWoA612KYgR8vRrLk4`. Create records for the other 38. Do not guess from titles. |
| Curriculum | Partial | `lms/seeds/mcf3m_expectations.json` and catalogue `ontario-seed-mcf3m-A2.7.json` (verbatim A2.7). Not the full 53 + 9 + 7 as versioned source records. | Import package texts as immutable source revisions. Compare seed wording; flag disagreement. Keep sample problems separate. Process expectations are course-wide, not a coverage checklist. |
| Rules | Partial | `.cursor/rules/content-builder-*.mdc`, agent files, `writer-reference/`. Package `rules/instructional-rules.md` is not in the store. | Versioned rule records with scope (`async_tabs` vs `live_slides`). Current explicit repo rules win over older plans. |
| Review | Partial | `review/index.html` loads one lesson; saves `instruction.json` / `locks.json`. No import or course-readiness view. | Compact onboarding panel on the same review server (`:8790`). Do not reset locks, instruction, or student copy. |
| Generation | Partial | `build_lesson.py` reads only `student-content.json` (whitelist `student_fields.py`). Specialists read per-lesson JSON. No bound context revision. | `resolve_lesson_context.py` binds identity, exact expectations, mappings, rules, locks, resources, concerns. Student renderer still cannot read that file. |
| Resources | Partial | `catalogue/resources/*.json` (JSXGraph board, OpenStax, MathNet, Ontario seed, GeoGebra ref). Package `resources.json` is empty on purpose. | Keep existing records. Empty package list is not a wipe. |

## Pilot snapshot (preserve)

| Field | Value |
|---|---|
| Internal id | `M4-L1-vertex-form` |
| Content | `student-content.json`, compiled `build/MCF3M/M4-L1-vertex-form/` |
| Fixture | `fixtures/M4-L1-vertex-form-v1/` |
| Brief expectations | A2.7, A2.10 (teacher-present, not an approved mapping status) |
| Package proposal for M4L1 | A2.5, A2.6, A2.7, A2.10 — difference stays unresolved |
| Locks | none on disk |
| Selected resources | fountain JPEG; bundled JSXGraph; catalogue entries unchanged |
| Concurrent uncommitted work | LMS / Game Show / `.local-data` — out of scope; do not touch |

## Identity resolution order

1. Exact Drive folder ID
2. Unique existing course / module / lesson number (attach Drive metadata)
3. Create a builder identity record
4. Disagreement or multiple matches → conflict; continue; never title-similarity

## Status fields (independent)

`source_verification` · `mapping_review` · `content_approval` · `validation_result`

A validator pass cannot approve a lesson. Proposed mappings are not coverage.
