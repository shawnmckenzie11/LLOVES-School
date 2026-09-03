# Class structure constants

These constants apply to ELC math courses unless a course overrides them explicitly.

## Weekly rhythm

| Session | Length | Purpose |
|---------|--------|---------|
| Live class 1 | 75 min | Interactive Zoom instruction / practice |
| Live class 2 | 75 min | Interactive Zoom instruction / practice |
| Friday office hours | 75 min | Open Zoom help; flexible attendance |
| Async modules | ongoing | Canvas lesson pages between live sessions |

## Semester shape

These apply to **all ELC courses and all semesters**. Derive real school days from [`semester.json`](semester.json) (weekdays minus `holidays` and `pd_days`). Do **not** assume 20 × 5 = 100 instructional days.

| Constant | Rule |
|----------|------|
| Length | **20 weeks** — first instructional day through the exam window on the board calendar |
| Intro | **First 2 instructional days** = course overview / housekeeping only (no module work) |
| Review | **Last instructional week before exams** = review (no new module). Any leftover instructional day after that week stays review/flex. |
| Due dates | **School days only** — never weekends, holidays, or PD days listed in `semester.json` |

**2026–27 S1:** instructional **2026-09-08 → 2027-01-25**; exam window **2027-01-26 → 2027-02-01**. Intro days are Tue Sep 8 and Wed Sep 9. Review week is Jan 18–22; Mon Jan 25 is review/flex.

## Planning implications

- Live time is scarce (~150 min/week instructional + optional Friday help). Async Canvas carries the bulk of content exposure and practice.
- Live sessions should prioritize high-interaction work: misconceptions, modeling, worked problems, discussion — not reading pages aloud.
- Module pages must stand alone for students who miss a live class.
- Office hours are support, not a third mandatory class; do not require new mandatory content only available Fridays.
- Content modules fill the instructional span **after** the two intro days and **before** the review week.

## Assessment / LMS notes

- Primary delivery and submission surface: **Canvas**
- Honorlock or similar may be used for secured assessments when configured in Canvas
- Keep student-facing instructions LMS-native (pages, assignments, modules) rather than external-only docs
