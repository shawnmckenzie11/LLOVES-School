# Course Wide warmup import — teacher click path

Ops check after restart/seed (`LOCAL_DEV_LOGIN=1`, http://127.0.0.1:8787).

The eleven locked icebreakers are stored two ways:

- `live_problems` (`kind=warmup`, `module_hint` `COURSE/…`) for MCF3M and MCR3U. That table is not what Import searches.
- Question bank `course-wide-warmups` on the class library. Course Wide Import reads this bank. Opening Course Wide search seeds it when the locked titles are missing.

## Click path

1. Sign in as staff (Teacher card: `shawnmckenzie11.sm@gmail.com`). One click, no 2SV.
2. Open the MCF3M class (roster Maple) → **Run Live Class**.
3. On the live page, click **Import from bank**.
4. Bank scope → **Course Wide**.
5. Kind → **Warmup**.
6. The count line reads **11 questions**. The row titles are:

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

7. Switch Bank scope to a module (M1–M8). Those eleven titles stay off the math list, including when Kind is Warmup.

Same eleven titles for an MCR3U class whose offering has a content library. Kind left on Process hides warmup-tagged rows in a module search. Course Wide with Kind Warmup is the list above, not A2/A3 items.

A database seeded before the title rename can hold about twenty `COURSE/` warmups per course (old title plus locked title). Restart/seed deletes the nine retired titles and leaves these eleven. See `lc-qa/ops-smoke-pr124-warmup-import-FAIL-2026-09-22.md`.
