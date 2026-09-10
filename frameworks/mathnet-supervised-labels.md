# MathNet TEAM_CHALLENGE supervised labels

Shawn’s accepted and rejected examples. **SL-MathNet** (`sl-mit-mathnet`)
loads this file before ranking any new `topics_flat` path. Do not contradict
a stored label.

Corpus: [`ShadenA/MathNet`](https://huggingface.co/datasets/ShadenA/MathNet).
Selection principles: [`portfolio-anchor-selection.md`](portfolio-anchor-selection.md).

## Positive labels (quadratic, Module 1)

| id | Title | Raw stem | Live CONTEXT / QUESTION | Label |
|---|---|---|---|---|
| `02zc` | Two-Stripe Flag | Already a **best** TEAM_CHALLENGE candidate before rewording. | CONTEXT: A rectangular flag contains two equal-width crossing stripes of the same colour. QUESTION: Their width must be chosen so the striped and unstriped areas are equal. | After that reframe: **absolutely perfect** for live class. Prefer Flag-class items that are strong *raw*, then compress to one picture + one ask. |
| `022j` | Furious Teacher | Original wording **very good** (do not trash the contest stem). | CONTEXT: A teacher reduces each student’s test mark by the same percentage as the original mark to generate their final grade. QUESTION: What mark should you score on the test to finish atop the class? | Teacher-class: keep the human rule; **one** live ask (vertex / top of the class), not the three-part olympiad (highest / lowest / ties). |
| `029j` | Triangle of Cans | Growing pattern; leftover cans. | CONTEXT: A child is arranging 480 cans into a triangle, with one can in the first row and one extra can in each new row. When the last complete row is finished, 15 cans are left unused. QUESTION: How many complete rows does the triangle have? | Module 1 gold for growing-pattern representation. Live CONTEXT is now Flag/Teacher-quality spoken sentences (was a fragment with a trailing leftover tack-on and no 480 / 15). |

## Pool (quadratic analogues, ranks 4–6)

Keep in the MCF3M Strand A TEAM_CHALLENGE pool unless a later label replaces them. Do not replace these ids.

Earlier pool CONTEXT were **fragments** (colon lists, missing setup numbers, meta “you are given,” three-part olympiad asks). They are now Flag/Teacher-quality complete sentences. Copy this spoken standard on the next topic search: one or two full sentences a Grade 11 student can hear once and picture; put listener-needed facts in CONTEXT when the picture is incomplete without them; QUESTION is one live ask.

### Pool live copy

| Rank | id | Title | Live CONTEXT / QUESTION | Class |
|---:|---|---|---|---|
| 4 | `0e6g` | Chicken-and-Goose Fence | CONTEXT: Chickens and geese share a rectangular yard of 1632 square metres, split down the middle of the width so the two groups stay separate. The total fencing, including the divider, is 198 metres. QUESTION: What are the length and width of the yard? | Flag-class drawable farm (two lengths, three widths). Two positive pairs both work — that surprise stays in `why`, not in the question. |
| 5 | `02be` | Wire Cut into Two Squares | CONTEXT: A 10-metre wire is cut into two pieces. Each piece is bent into a square. QUESTION: Where should the cut be so the two squares together enclose as little area as possible? | Teacher-class: keep the cut-and-bend situation; one vertex-minimum ask. Drop the ten-piece olympiad extra. |
| 6 | `0e2j` | Goalkeeper’s Kick | CONTEXT: A goalkeeper kicks a soccer ball from the ground. The ball rises and then comes down again farther along the field. QUESTION: How high does the ball get? | Teacher-class: the kick is the situation (not “you are given a rule”); one surprising live ask (maximum height), analogous to “finish atop the class.” Formula stays in `stem` for after they sketch. Weaker on situation-first because the contest prints \(h(x)\). |

## Negative labels

| id | Why it was tempting | Label |
|---|---|---|
| `0hy4` | Named human, prison, crossbow | **Not** “most non-math sounding.” Kinematics. Never promote. |
| `0ija` | Ant and spider | Lexical story_score. Olympiad motion. Never promote. |
| `0j2v` | Short English quadratic in *p* | Needs probability. Candidate, not a gold. |

## Reframe templates to copy

**Flag-class** (raw already a winner): one visual situation; one equality/width ask; no extra contest parts.

**Teacher-class** (raw already very good): keep the human rule in CONTEXT; QUESTION is a single surprising target (“finish atop the class”), not a–b–c.

**Spoken-sentence standard:** CONTEXT is one or two complete sentences a Grade 11 student can hear once and picture. No fragments, colon lists, trailing tack-ons (“with some cans left unused”), or meta language (“you are given a rule”). Put listener-needed facts in CONTEXT when the picture is incomplete without them (480 and 15 leftover; 1632 m² and 198 m; 10 m wire). Flag omitted 40×20 because the picture was already complete.

When Shawn accepts or rejects a later topic’s pick, append a row here.

## Polynomials (MCT4C Strand B)

Parent path only: `Algebra > Algebraic Expressions > Polynomials` (**171** rows). This pass is **not** quadratic-gold reuse (`02zc`, `022j`, `029j` stay MCF3M Strand A) and did **not** widen to `Polynomial operations`. Honest Flag-class visual-overlap is scarce; Gold #1 is Teacher-class.

**Gold #1 live** (`0532` Soothsayer’s Book):

CONTEXT: A reference book claims that if you add one to a number and raise the result to a positive even power, the answer is always at least as large as the original number times that same power of two.

QUESTION: Is the book telling the truth?

### This-pass six (pending Shawn’s accept/reject)

| Rank | id | Title | Class |
|---:|---|---|---|
| 1 | `0532` | Soothsayer’s Book | Teacher-class (raw already a short English yes/no) |
| 2 | `0kn8` | Six Checkpoints | Flag-class number line (factored degree-six product) |
| 3 | `08t6` | Closest to Ten Thousand | Growing-pattern list n(n+4) |
| 4 | `0e6k` | Plus-Minus Degree Five | Table: evaluate ±1 coefficients at 1 |
| 5 | `08fh` | Three Integers Add to 2022 | Teacher-class summing rule on outputs |
| 6 | `0kma` | Two Consecutive Outputs | Remainder / integer-root family |

Skipped in-tag: `07c2` (operations computer; destem to “can it store a square?”), `0665` (tetrahedron tips; contest prints the cubic), `0iub` (rectangle on a cubic graph), Eisenstein/irreducibility/Gauss items, IMO magician `06w1`, named game `07fn`.
