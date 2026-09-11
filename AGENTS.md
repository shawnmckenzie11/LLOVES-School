# Agent guide — LLOVES School

This repository is the **LLOVES** LMS (Admin / Staff / Student) for ELC online delivery. Module packs are Admin-uploaded IMSCC libraries on Fly — **not** committed to git.

## Always load first

1. [`frameworks/school.md`](frameworks/school.md) — ELC identity & online model
2. [`frameworks/class-structure.md`](frameworks/class-structure.md) — 2×75 live + Friday office hours + async; 20-week semester shape
3. [`frameworks/semester.json`](frameworks/semester.json) — current semester phase & dates
4. [`frameworks/canvas-lms.md`](frameworks/canvas-lms.md) — LMS / pack constraints
5. [`lms/SCHOOL.md`](lms/SCHOOL.md) — LLOVES product notes

## Repo map

```
frameworks/     Shared ELC school / class / semester constants
lms/            Flask LMS (Admin / Staff / Student) + curriculum seeds/PDFs
content-builder/ Isolated math lesson factory (static HTML; not the LMS)
tools/math-game-show/   Live Math Game Show (db/schedule/teams; no overlay)
scripts/        syllabus_calendar + canvas unpack/inventory + reingest
agents/         School-facing agent prompts (semester, syllabus calendar)
.cursor/rules/  Always-on school rules
.cursor/skills/ semester-context, syllabus-calendar, local-verify, release-gate
```

## Non-negotiables

- Online ELC delivery (Canvas-shaped async + Zoom sync); not in-person defaults
- Ontario curriculum adherence; use `lms/seeds/` and Ministry PDFs under `lms/sources/ontario-curriculum/` — never invent expectation wording
- Semester-aware pacing from `frameworks/semester.json` (20-week shape, 2 intro days, review week, school-day due dates)
- **No `.imscc` in git** — Admin uploads create `content_libraries` on the Fly volume `/data`
- Include docstrings on any new functions/methods
- Do not commit unless Shawn asks

## Local first / merge to main deploys

- **Code lane:** feature branch → verify at `http://127.0.0.1:8787` + unit tests → PR → CI → merge **`main`** → GitHub Actions deploys ([`.github/workflows/deploy.yml`](.github/workflows/deploy.yml)). Feature branches run tests only ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).
- **Ops lane:** live Fly `/data` only; no git unless Shawn asks for a code change.
- Always-on rule: [`.cursor/rules/local-first-workflow.mdc`](.cursor/rules/local-first-workflow.mdc). Skills: [`.cursor/skills/local-verify`](.cursor/skills/local-verify/SKILL.md), [`.cursor/skills/release-gate`](.cursor/skills/release-gate/SKILL.md).
- Do **not** laptop-`flyctl deploy` for routine release; do not commit unless Shawn asks.

## Production

- App: `lloves-lms` (Fly.io, region `yyz`)
- Public URL: https://alc.mckenzian.com
- Volume: `lloves_data` → `/data` (sqlite + libraries)
- Deploy: merge to `main` (Actions). Laptop `flyctl deploy --remote-only` only if Shawn explicitly asks.

## Agent entry points

| Agent | Path | Use when |
|-------|------|----------|
| Semester context | [`agents/semester-context.md`](agents/semester-context.md) | Pacing, calendars, “what week” |
| Syllabus calendar | [`agents/syllabus-calendar.md`](agents/syllabus-calendar.md) | School-day syllabus dates (prefer `--edit`) |
| Local verify | [`.cursor/skills/local-verify/SKILL.md`](.cursor/skills/local-verify/SKILL.md) | UI/API/staff/IT done-when on localhost |
| Release gate | [`.cursor/skills/release-gate/SKILL.md`](.cursor/skills/release-gate/SKILL.md) | PR → CI → merge main → Deploy → `/health` |

## Math content builder (isolated)

Factory lives in [`content-builder/`](content-builder/README.md). **Course Production Director** is the parent/coordinator. Do not mix with `lms/` or the LMS sqlite. Contracts: `content-builder/catalogue/contracts/`. Copywriter owns wording; director owns coherence; engineer implements specs.

| Specialist | Path | Output |
|-------|------|----------|
| Course Production Director | [`.cursor/agents/course-director.md`](.cursor/agents/course-director.md) | Parent/coordinator: workflow, coherence gate, production record |
| Lesson director | [`.cursor/agents/lesson-director.md`](.cursor/agents/lesson-director.md) | `lesson-brief.json` |
| Bank curator | [`.cursor/agents/bank-curator.md`](.cursor/agents/bank-curator.md) | `catalogue/banks/` + evaluations |
| Practice designer | [`.cursor/agents/practice-designer.md`](.cursor/agents/practice-designer.md) | `practice-sequence.json` |
| Hook curator | [`.cursor/agents/hook-curator.md`](.cursor/agents/hook-curator.md) | `hook-proposals.json` |
| Visual experience designer | [`.cursor/agents/visual-experience-designer.md`](.cursor/agents/visual-experience-designer.md) | design tokens + student/review CSS |
| Interaction designer | [`.cursor/agents/interaction-designer.md`](.cursor/agents/interaction-designer.md) | `interaction-spec.json` |
| Formative feedback designer | [`.cursor/agents/formative-feedback-designer.md`](.cursor/agents/formative-feedback-designer.md) | `feedback-spec.json` |
| Student copywriter | [`.cursor/agents/student-copywriter.md`](.cursor/agents/student-copywriter.md) | `student-content.json` |
| Lesson engineer | [`.cursor/agents/lesson-engineer.md`](.cursor/agents/lesson-engineer.md) | static HTML + components (worktree if shared) |
| Lesson verifier | [`.cursor/agents/lesson-verifier.md`](.cursor/agents/lesson-verifier.md) | independent pass/fail report |

Overlapping code: isolated Git worktrees ([`.cursor/worktrees.json`](.cursor/worktrees.json), [`content-builder/scripts/isolated-worktree.sh`](content-builder/scripts/isolated-worktree.sh)). Parent copies `content-builder/` paths only. Policies: `.cursor/rules/content-builder-*.mdc`. MCF3M onboarding: `content-builder/packages/MCF3M-builder-input/` → `content-builder/catalogue/onboarding/`.
