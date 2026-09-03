# Semester context agent

## Purpose

Keep all planning **semester-aware**: phase, instructional dates, PD days, holidays, and exam windows for the semester Shawn is preparing for or teaching.

## Always do first

1. Read [`frameworks/semester.json`](../frameworks/semester.json)
2. Skim [`frameworks/semester.md`](../frameworks/semester.md) if human context helps
3. State the active semester, `current_phase`, and any date conflicts with the user’s request

## Phase meanings

| Phase | Meaning |
|-------|---------|
| `preparing` | Pre-term / summer build; no live classes yet |
| `in_session` | Instructional days in progress |
| `exam` | Secondary exam window |
| `break` | Board/stat multi-day break (e.g. winter break) |

## Pacing rules

School-wide (all courses, all semesters) — also in [`frameworks/class-structure.md`](../frameworks/class-structure.md):

- Semester is **20 weeks** (first instructional day through the exam window). Count real school days from `semester.json`; do **not** assume 20 × 5 = 100 days.
- **First 2 instructional days** = intro / course overview only (no module work).
- **Last instructional week before exams** = review (no new module). Any leftover instructional day after that week stays review/flex.
- **Due dates only on school days** — never weekends, holidays, or PD days listed in `semester.json`.

Also:

- In `preparing`, prioritize course structure, modules, and Canvas readiness over “this week’s homework”
- Live-class count must fit remaining instructional weeks (2×75 min/week)

**2026–27 S1:** instructional **2026-09-08 → 2027-01-25**; exam window **2027-01-26 → 2027-02-01**. Intro = Sep 8–9. Review week = Jan 18–22; Jan 25 = review/flex.

## Updating semester state

When Shawn changes semester or the board publishes a final calendar:

1. Download/update PDF under `frameworks/sources/`
2. Parse key dates (or update manually from the PDF grid)
3. Update `frameworks/semester.json` (`as_of`, `current_phase`, date arrays)
4. Sync narrative in `frameworks/semester.md`
5. Optionally move the previous JSON to `frameworks/archive/`

## Related skill

`.cursor/skills/semester-context/SKILL.md`
