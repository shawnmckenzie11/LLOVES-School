# Syllabus calendar agent

## Purpose

Build a **day-by-day syllabus calendar** (CSV + highlighted HTML + reusable answers JSON) from the board calendar, course live-class times, and the **legacy Canvas IMSCC** lesson/assessment list.

This is a **scheduling** tool. It does not write dates back into Canvas, and it does not replace the rebuild 5-theme IA in `course-plan.md`.

## Always do first

1. Read [`frameworks/semester.json`](../frameworks/semester.json)
2. Prefer the LLOVES Staff syllabus editor (school-day calendar on the volume)
3. For offline rebuilds, pass explicit `--imscc` / inventory paths — packs are not in git
4. For MCF3M S1 2026–27, use the **legacy 8 content modules** in the IMSCC, not the rebuild M1–M5 in `course-plan.md`

## School-day bands

- Instructional days = weekdays from `first_day_of_school` through `last_instructional_day_before_exams`, minus `holidays` and `pd_days`
- **Intro:** first **2 instructional days** (course overview; no module work)
- **Exam prep:** last **3 instructional days** before the first exam-window day, labeled **Review**. Not a content module. Do not attach a final-exam assessment
- **Content pool:** instructional days after intro and before those 3 exam-prep days
- Exam-window days may appear as **empty exam-period cells** (no exam title)
- Due dates only on school days (never weekends, holidays, or PD)
- Live classes: remaining Mon/Wed school days at the times in `schedule.json` (holiday Mondays skip automatically)
- Friday office hours stay off the calendar unless Shawn asks to add them

## Hard packing rules (all courses)

These apply when the tool **auto-packs** (`--yes` or the CLI without `--edit`). Lesson include/exclude prompts stay; **do not** prompt for test, conference, quiz, assignment, or exam dates.

1. **Lesson prompts only.** Per-module include/exclude of detected lessons. No test/conference/quiz/assignment/exam date prompts. `--yes` / `--answers` remember **lesson** flags only.
2. **Every module auto-gets a Test and a Conference on separate days.** No portfolios. No extra quizzes. **No exam** on the calendar.
3. **Intro** is the first 2 instructional days.
4. **Exam prep** is the last 3 instructional days before the first exam-window day, labeled Review. Exam-window days may appear empty (no exam title).
5. **Content pool** is after intro and before those 3 exam-prep days.
6. **Tight length** of module *i* is `n_lessons + 1` (Review) `+ 2` (conference and test). `leftover = len(content_pool) - sum(tight)`. `shares[i] = leftover // n_modules`, plus 1 extra to the first `leftover % n_modules` modules. Trailing empty days sit **after** conference and test, still tagged as that module (live time may show; no lesson).
7. **Walk** the content pool in order: M1 lesson 1 = first content day; next `n_lessons` consecutive school days = lessons (including live days); next school day = **Review**; next school day after Review: **live (Mon/Wed) or Friday** → conference that day, test the following school day; **async (Tue/Thu)** → test that day, conference the following school day; then that module’s leftover empty days; next module starts the next school day. Repeat through the last content module.
8. **Titles:** tests from IMSCC when present, else `Module N Test`. Conferences always `Module N Conference`.
9. **Test days never have lessons.** Conference and test stay on **separate** days.

`--edit` does **not** run this walk. It opens a blank board calendar (PD / holidays / exam window locked from `frameworks/semester.json`, which follows the TLDSB school-year PDF). Upload a Canvas `.imscc` at start. **Remaining items** lists every titled item in the selected module; delete or reorder there. The top toolbar **Live class** tool stamps as many live-class days as you click (current module colour; can share a day with a lesson). Click days to place the next remaining item, Review, test, or conference. The module dropdown does **not** auto-advance. Save still writes the same CSV/HTML (no exam, no portfolios, test days have no lesson).

Also: **Lesson and Assessment are separate columns.** Never merge lesson titles with tests/conferences in one cell. **Date is the day-of-month number only** (e.g. `2` for 2 September). **Module lessons land on live-class days** as well as other school days (live class + lesson in the same cell).

HTML layout: **stacked Mon–Fri month grids** with the **day list table below**. Each week is **three aligned table rows** (day number; module lesson or **Review**; assessments). Live class is a second line in the middle cell (`Live class<br>Lesson …`). The month grid is **Canvas-RCE-safe**: every table/th/td style is inlined so paste into Canvas keeps the layout after the RCE strips `<style>`. Local `<style>` is for the day-list table only.

## Commands

Prefer the click-to-place editor for layout. Use `--yes` only when you want the sequential packer.

```bash
python3 scripts/syllabus_calendar.py --course MCF3M --edit
python3 scripts/syllabus_calendar.py --course MCF3M --yes
python3 scripts/syllabus_calendar.py --course MCF3M --answers courses/MCF3M/syllabus-calendar/2026-2027-S1.answers.json
```

`--edit` opens a local browser. Upload a Canvas `.imscc`. The grid is the blank board calendar (PD / holidays / exam window locked). Use the toolbar **Live class** tool for as many live days as you need (module colour). Place other module items from Remaining items. Reorder or delete items in the sidebar. Change module only from the dropdown. **Save** writes CSV + HTML (Canvas-safe, no editor chrome). Closing without Save discards clicks.

`--yes` accepts lesson-flag defaults after loading `--answers` (or the existing answers file in the output folder, or built-in include/exclude defaults) and runs the sequential packer.

## Outputs

Under `courses/MCF3M/syllabus-calendar/`:

- `2026-2027-S1.csv`
- `2026-2027-S1.html` (open in a browser; print-friendly)
- `2026-2027-S1.answers.json` (lesson include/exclude only)

## Wizard / editor

`--edit` uses an uploaded `.imscc` (all titled module items). The sidebar is **Remaining items**: delete or move items up/down. **Live class** is a toolbar tool (place on many days). The selected module stays until you change the dropdown.

Without `--edit`, the CLI still lists detected lessons per module so you can include/exclude extras (e.g. “Putting it all Together”). `--yes` places tests and conferences automatically. Do not prompt for assignments, quizzes, conferences, tests, or the exam.

## Related skill

`.cursor/skills/syllabus-calendar/SKILL.md`
