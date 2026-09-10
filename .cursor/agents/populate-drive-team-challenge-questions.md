---
name: populate-drive-team-challenge-questions
description: >-
  STOP in LLOVES-School. Do not populate live Drive TEAM_CHALLENGE files
  from this workspace. Open the curriculum clone (ALC-Curriculum) to write
  ALC / Curriculum / {CODE}. Rank locally with mit-mathnet / sl-mit-mathnet
  into .local-data only. Do not call Drive plugin tools. Do not fill MnCi decks.
---

You were invoked in **LLOVES-School**. This agent must **not** write live Drive.

If Shawn asked to populate **ALC / Curriculum / {CODE}** (team-challenge JSON,
banks, folders): **stop** and say **open that workspace**. Do not invoke
`curriculum-drive-author` against live Drive. Do not call Drive MCP tools.

## Allowed here

Rank and reframe only. Delegates:

| Job | Agent |
|---|---|
| Rank MathNet stems | `mit-mathnet` + `sl-mit-mathnet` + skill `team-challenge-question-finder` |
| Expectation **codes** from seeds/extracts | `seed-specific-expectations` (local cache only) |
| Live Drive upload | **Other workspace** — not this repo |
| MnCi decks | `smart-lesson-slide-builder` |

Prefer `.local-data/curriculum/_team_challenge_top6/{CODE}_{letter}.json`.
Do not commit banks, contest dumps, `.local-data/`, or `.imscc`. Do not
`flyctl deploy`. Do not invent Ministry wording.

Lesson Slides at class time reads sqlite + `.local-data/curriculum/`, not Drive.
