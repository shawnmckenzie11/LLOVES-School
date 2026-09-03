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
tools/math-game-show/   Live Math Game Show (db/schedule/teams; no overlay)
scripts/        syllabus_calendar + canvas unpack/inventory + reingest
agents/         School-facing agent prompts (semester, syllabus calendar)
.cursor/rules/  Always-on school rules
.cursor/skills/ semester-context, syllabus-calendar
```

## Non-negotiables

- Online ELC delivery (Canvas-shaped async + Zoom sync); not in-person defaults
- Ontario curriculum adherence; use `lms/seeds/` and Ministry PDFs under `lms/sources/ontario-curriculum/` — never invent expectation wording
- Semester-aware pacing from `frameworks/semester.json` (20-week shape, 2 intro days, review week, school-day due dates)
- **No `.imscc` in git** — Admin uploads create `content_libraries` on the Fly volume `/data`
- Include docstrings on any new functions/methods
- Do not commit unless Shawn asks

## Production

- App: `lloves-lms` (Fly.io, region `yyz`)
- Public URL: https://alc.mckenzian.com
- Volume: `lloves_data` → `/data` (sqlite + libraries)
- Deploy: `flyctl deploy --remote-only` from repo root (or GitHub Actions with `FLY_API_TOKEN`)

## Agent entry points

| Agent | Path | Use when |
|-------|------|----------|
| Semester context | [`agents/semester-context.md`](agents/semester-context.md) | Pacing, calendars, “what week” |
| Syllabus calendar | [`agents/syllabus-calendar.md`](agents/syllabus-calendar.md) | School-day syllabus dates (prefer `--edit`) |
