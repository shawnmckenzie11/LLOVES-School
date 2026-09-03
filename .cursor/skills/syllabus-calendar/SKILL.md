---
name: syllabus-calendar
description: >-
  Build a day-by-day ELC syllabus calendar (CSV + highlighted HTML) from the
  board calendar, live-class times, and legacy IMSCC lessons. Prefer --edit for
  click-to-place layout; --yes for the sequential packer. Use when planning
  syllabus dates, due dates, live vs async days, tests, or conferences.
---

# Syllabus calendar

## Instructions

1. Read `frameworks/semester.json` (school days, holidays, PD, exam window; sourced from `frameworks/sources/tldsb-2026-27-school-year-calendar.pdf`).
2. For `--yes`, also read `courses/<CODE>/schedule.json` and `courses/<CODE>/canvas/inventory.json`. For `--edit`, the user uploads a `.imscc` in the browser.
3. Follow `agents/syllabus-calendar.md`.
4. Prefer `--edit` for click-to-place layout. Use `--yes` only for the sequential packer.

## Commands

```bash
python3 scripts/syllabus_calendar.py --course MCF3M --edit
python3 scripts/syllabus_calendar.py --course MCF3M --yes
python3 scripts/syllabus_calendar.py --course MCF3M --answers courses/MCF3M/syllabus-calendar/2026-2027-S1.answers.json
```

`--edit` serves a local browser: upload a Canvas `.imscc`, then place items on a blank board calendar (PD / holidays / exam window locked). Toolbar **Live class** stamps as many live days as needed (current module colour). **Remaining items** lists every module item (delete / move up/down). Module dropdown does not auto-advance. Save writes the static CSV/HTML. `--yes` still runs the automatic sequential packer.

## Key files

- `scripts/syllabus_calendar.py`
- `agents/syllabus-calendar.md`
- `courses/MCF3M/schedule.json`
- `courses/MCF3M/syllabus-calendar/`
- `courses/MCF3M/canvas/inventory.json`
- `frameworks/semester.json`

## Shape

First 2 instructional days = intro. Last 3 instructional days before the first exam-window day = exam-prep Review (no exam title on the calendar). Content pool is after intro and before those 3 days. Due dates only on school days. Live Mon/Wed 14:00–15:15 for MCF3M.

Hard packing rules (every course): lesson include/exclude prompts only; every module auto-gets a Test and a Conference on separate days (no portfolios, no exam); consecutive lessons including live days; next school day is **Review**; next day live/Friday → conference then test, async Tue/Thu → test then conference; leftover empty days split at module ends (`leftover // n_modules`, extra to the first `leftover % n_modules` modules); test titles from IMSCC else `Module N Test`; conferences `Module N Conference`; no lessons on test days.

HTML: stacked Mon–Fri month grids on top, day list below. Each week is three aligned table rows (day number; lesson or Review; assessments). Live class is a second line in the middle cell. Month grid is Canvas-RCE-safe (all table styles inlined; do not rely on `<style>` for the grid).
