# Ops smoke — Course Wide warmup Import

## Fail (tip `1f8a7e8`, port 8788)

Import → Course Wide → Kind=Warmup showed **0**.

`live_problems` had 11 `COURSE/` warmups for MCF3M and 11 for MCR3U.

Root: Import `mc-search` reads catalogue question banks. `#124` / `#126` seeded `live_problems` only, so Kind=Warmup had nothing to list.

## Tip drift (`610899b`)

Teacher Import already returned **11 short titles** from the `#125` Course Wide warmup bank (`Overrated food`, `Useless skill`, `Fraction who never did it`, and the titles that already matched).

`live_problems` was about **20 per course**. The title rename upserts on `(ontario_code, kind, title)`, so each renamed icebreaker left the old row beside the locked title. Nine retired titles × two courses on top of the locked eleven is twenty.

## Fix

Course Wide Import stays the teacher path (option B): Kind=Warmup lists the question bank `course-wide-warmups`, seeded with the locked eleven titles. Module math Import does not list that bank.

`seed_live_problems` deletes the nine retired `COURSE/` titles so MCF3M and MCR3U each keep eleven course warmups. A lesson-keyed row that happens to use an old title is left in place.

Locked titles:

- Aisle or window
- Text or call
- Beach or cabin
- Most overrated food
- A rule that should differ
- Unimportant confident opinion
- Weirdly useless skill
- Group mascot
- Laughed too hard
- Fraction who never did X
- Rank three annoyances

Click path: `lc-qa/repo-paths-course-wide-warmup-import-2026-09-22.md`.
