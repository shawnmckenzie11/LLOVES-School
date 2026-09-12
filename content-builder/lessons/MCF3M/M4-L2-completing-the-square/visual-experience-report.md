# M4-L2 visual-experience report

Role: visual-experience-designer  
Lesson: `MCF3M` / `M4-L2-completing-the-square`  
Branch: `cursor/m4-l2-ved-8559` (isolated worktree; do not merge from this specialist)

## Diagram decision (for CPD)

**diagram-ex960 = static fallback this pass.**

Do **not** build `jsxgraph-cts-area-diagram` as an interactive tile board.

Ship `media/cts-area-diagram-ex960.svg`: area picture of \(f(x)=x^2+6x+5\) with six \(x\)-rectangles split 3+3 around \(x^2\), a dashed empty 3×3 completing-square corner, and five unit tiles labeled `+5`. Students name **side = 3** and **leftover = −4**; those values are not printed on the figure.

Chrome the engineer should attach: `figure.media-slot.cts-area-diagram` + `.diagram-inputs` (side, leftover) + existing `.cycle` predict → submit → check. Paper tiles stay the no-JS fallback (`.fallback` / `.media.is-unavailable`).

**Wonder advance chrome = thin.** Style `.btn-advance` / `.cycle-actions .advance` as the existing lean Advance / Try another control. No Wonder widget, no packet picker, no explore-all, no Fresh-case on Action.

## Files written

| Path | What |
|---|---|
| `content-builder/components/design-tokens.css` | Three lean-chrome tokens: `--rail-size`, `--rail-gap`, `--state-pad-y`. Same colour family as M4-L1. |
| `content-builder/components/student.css` | Packet hooks: `.progress-rail`, `.btn-advance`, `.cycle.is-ok` (Advance becomes the one primary), `.board-evidence` surface, `.task-board` phone/desktop layout, `.media-slot` / `.cts-area-diagram` / static path when `.is-unavailable`, `.feedback-strip` (≤3), named one-line states. |
| `content-builder/review/review.css` | Same board / feedback tokens. Advance stays secondary. No delight / motion. |
| `content-builder/lessons/MCF3M/M4-L2-completing-the-square/media/cts-area-diagram-ex960.svg` | Static area diagram. |
| `…/visual-experience-fixture.html` | Hook demo for lesson-engineer (not compiled student HTML). |
| `…/visual-experience-review-fixture.html` | Review-chrome hook demo. |
| `…/visual-experience-evidence/` | Viewport screenshots. |
| `…/visual-experience-report.md` | This file. |

Not edited: `student-content.json`, `student-feedback.json`, `lms/`, interaction-spec (decision recorded here for CPD).

## Token summary

Reused M4-L1: `--ink`, `--muted`, `--accent`, `--paper`, `--surface-muted`, `--line`, `--ok-*`, `--retry-*`, `--hint-*`, `--btn-primary-*`, `--btn-ghost-fg`, `--figure-bg`, `--tap`, `--motion`.

Added only: `--rail-size`, `--rail-gap`, `--state-pad-y`.

## Engineer hooks (attach; do not invent a second system)

- `.progress-rail` + `.now` / `[aria-current="step"]` + `.next` — thin where-am-I / what’s-next. Not a TOC.
- `.btn-advance` or `.cycle-actions .advance` / `[data-action="advance"]`. After `.cycle.is-ok`, Check is secondary and Advance is the one primary.
- `.task-board` on `.board-wrap`: phone (~390) stacks task → graph → evidence → actions; desktop (~1280, `min-width: 1100px`) is task + graph side-by-side.
- `.board-evidence` + `.diagram-inputs` for side / leftover.
- `.media-slot.cts-area-diagram` / `.board-figure.is-static` — do **not** put the SVG in a 380px `.jxgbox` crop.
- `.media.is-unavailable` / `.board-wrap.is-unavailable` still shows the static figure, inputs, and Check.
- `.feedback-strip` — at most three of ok / try-again / hint.
- One-line states: `.media-slot.is-loading` / `.embed.is-loading`, `.practice.is-empty`, `.end-of-cycle`. Default chrome strings only if the node is empty.

## Viewport evidence

Captured from the VED fixture over a local static server (compiled M4-L2 HTML does not exist yet).

| File | What it shows |
|---|---|
| `visual-experience-evidence/cts-area-diagram-ex960.png` | Static area picture (six \(x\)-tiles, dashed 3×3, +5 units). |
| `visual-experience-evidence/phone-390-task-board.png` | ~390 stack: rail → task → diagram → side/leftover → Check (primary) / Hint (ghost) / Advance (quiet). |
| `visual-experience-evidence/phone-390-full.png` | Same fixture, taller; includes unavailable path and named states. |
| `visual-experience-evidence/desktop-1280-task-board.png` | ~1280: task + diagram side-by-side; evidence under task; actions full width; after-pass Advance primary. |
| `visual-experience-evidence/desktop-1280-review.png` | Review chrome: same evidence/feedback tokens; Advance not delighted. |

## What I did not test

- Compiled M4-L2 student HTML (engineer has not wired packets yet). Auto-advance on ok→next is engineer.
- Keyboard focus on a live CTS checker (no L2 board script). Focus styles are the existing L1 rings.
- JSXGraph resize on a CTS board (out of scope).
- Interactive tile board (explicitly not this pass).
- Mobbin reference screens (store required a paid plan).
- Live Fly / LMS / student-copywriter wording.
- Wonder canvas widget (locked off).
- M4-L1 compiled page re-checked in a browser after these shared CSS additions. Selectors for `.task-board` / `.progress-rail` / `.btn-advance` are new; L1 boards without those classes should keep the old stack. The 900px figure\|evidence grid is now `:not(.task-board)` so L1 is unchanged unless someone adds `.task-board`.
