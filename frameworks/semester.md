# Semester state (human-readable)

**Source of truth for machines:** [`semester.json`](semester.json)  
**Calendar PDF:** [`sources/tldsb-2026-27-school-year-calendar.pdf`](sources/tldsb-2026-27-school-year-calendar.pdf)  
**Calendar URL:** https://www.tldsb.ca/wp-content/uploads/2025/12/Proposed-2026-27-TLDSB-School-Year-Calendar-2.pdf  

> Calendar PDF title: *Proposed* TLDSB 2026–27 School Year Calendar. Dates below verified 2026-07-30 from letter marks (P/B/H/E) and purple **First Day of School** shading. Re-verify if the board publishes a final version.

## Current focus

| Field | Value |
|-------|--------|
| Semester | 2026–2027 S1 |
| Span | September 2026 – January 2027 |
| Phase (as of 2026-07-30) | `preparing` |
| Prep context | Summer 2026 prep for Fall/Winter S1 |

## Key S1 dates (verified from calendar PDF)

| Event | Date(s) |
|-------|---------|
| PD days (start of year) | 2026-09-02, 2026-09-03 |
| Board holiday | 2026-09-04 |
| Labour Day (stat) | 2026-09-07 |
| First day of school (students) | **2026-09-08** (purple “First Day of School” shading) |
| Thanksgiving | 2026-10-12 |
| PD day | 2026-11-20 |
| Winter break (board/stat) | 2026-12-21 → 2027-01-01 |
| Secondary exam days (S1) | 2027-01-26 → 2027-01-29; also 2027-02-01 |
| Jan 29 | Marked **E\|P** (exam + secondary PD) |
| PD after exams | 2027-02-02 |

## Semester shape (all ELC courses)

Board semesters are **20 weeks**: first instructional day through the exam window. Instructional days are fewer than 20 × 5 because of weekends, holidays, PD, and winter break — always count from `semester.json`, never assume 100 days.

| Rule | Detail |
|------|--------|
| Intro | First **2 instructional days** — course overview only (no module work) |
| Review | Last instructional week before the exam window — no new module. A leftover day after that week (if any) stays review/flex. |
| Due dates | School days only (never weekends, holidays, or PD) |

**2026–27 S1:** instructional **2026-09-08 → 2027-01-25**; exam window **2027-01-26 → 2027-02-01**. Intro = Tue Sep 8 and Wed Sep 9. Review week = Jan 18–22; Mon Jan 25 = review/flex.

Full constants: [`class-structure.md`](class-structure.md).

## How agents should use this

1. Read `frameworks/semester.json` before pacing, module calendars, or “what week is it?” planning.
2. Treat `current_phase` as authoritative: `preparing` | `in_session` | `exam` | `break`.
3. Derive school days from `semester.json` (weekdays minus `holidays` and `pd_days`). Apply the 20-week / 2-intro-days / review-week / school-day due-date rules.
4. When the semester changes, update **both** `semester.json` and this file; see `agents/semester-context.md`.

## Updating semester state

1. Confirm board calendar (PDF or final published calendar).
2. Update dates and `current_phase` in `semester.json`.
3. Mirror the narrative summary here.
4. Optionally archive prior semester JSON under `frameworks/archive/`.
