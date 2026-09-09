# Astra → Cursor handoff

How GPT Astra should pass an **implementation plan** to the parent Cursor agent for `content-builder/`.

## Roles

| Role | Does |
|---|---|
| **Astra** | Instructional design + plan authoring. Emits JSON matching `catalogue/contracts/implementation-plan.schema.json`. Does not edit the repo. |
| **Parent Cursor agent** | Production manager. Validates plan, assigns specialists, integrates, runs scripts, presents review. |
| **Specialists** | Write only their contract outputs under `lessons/{CODE}/{lesson_id}/`. |
| **Shawn** | Approves pedagogical raises, locks, merges. |

## Plan lifecycle

1. Astra drafts `implementation-plan` (schema below / sibling JSON Schema).
2. Parent validates: schema, onboarding identity exists, expectations are Ontario-seeded, licence rules, no LMS/Fly scope creep.
3. Parent runs sequence in `.cursor/rules/content-builder-parent.mdc`.
4. Scripts: `build_lesson.py` → `check_feedback.py` → `verify_lesson.py`.
5. Shawn reviews `:8790`; parent records durable corrections into rules / writer-reference.

## Hard rules Astra must include in every plan

- Never invent Ministry expectation wording; cite `lms/seeds/` or onboarding curriculum sources.
- Never paste Nelson / commercial bank stems into student-facing fields.
- Student HTML is static: no runtime AI, credentials, or required remote compute.
- Module packs / IMSCC / Fly `/data` are out of scope unless Shawn explicitly expands.
- Completing the square (A2.8) is not M4-L1.

## Minimal plan fields

See `implementation-plan.schema.json`. Required:

- `course_code`, `lesson_id`, `title`
- `expectations[]` with codes + `why_this_lesson`
- `not_this_lesson[]` with reasons
- `benchmark` (optional): depth checklist IDs from Nelson-style bar
- `specialist_jobs[]`: role, inputs, output paths, acceptance
- `verify`: scripts to run + pedagogical checklist
- `locks` / `preserve`
- `licence`: excluded sources

## Example: M4-L1 raise to Nelson bar

See `implementation-plan.m4-l1-nelson-raise.example.json`.

## Parent reject reasons

- Identity not in onboarding / wrong Drive match
- Expectation text rewritten vs seed
- Student fields contain ranks, GeoGebra disclaimers, or Nelson stems
- Plan asks to commit or deploy without Shawn
- Shared `components/` edit without isolated worktree note
