# Content contracts

Shared contracts for the math lesson factory. Specialists submit against these files. The parent is the only integrator. The student renderer reads **only** `student-content.json` and never `teacher_notes`, `provenance` internals, ranks, or agent reasons.

| File | Owner | Student renderer |
|---|---|---|
| `lesson-brief.json` | lesson-director | no |
| `hook-proposals.json` | hook-curator | no (selected hook copy lives in student-content) |
| `assembly.json` | parent | no (pool assemble vs drawer; fade cells) |
| `practice-sequence.json` | practice-designer | no (practice-set ids only) |
| `interaction-spec.json` | interaction-designer | ids only |
| `feedback-spec.json` | formative-feedback-designer | no (engine uses it) |
| `student-content.json` | student-copywriter | **yes** |
| `teacher-notes.json` | director / copywriter teacher fields | no |
| `provenance.json` | copywriter + parent | credits blocks only |
| `locks.json` | parent after Shawn | skip locked keys |
| `identity.json` / `resolved-context.*.json` | onboarding import | no |

Onboarding contract: [`onboarding.md`](onboarding.md). Import does not rewrite `student-content.json`.
