# MCF3M M1C1 — live teaching notes (Shawn pick: **B**)

Live-Class Designer · 2026-09-10  
Dual-serve: **MCF3M M1** + **MCR3U M1** (graph/function language)  
Coverage pathways: `MCF3M-M1-live-pathways-coverage.md` (rematch likely — @Coverage Mapper)  
Status: **B locked** — immersive \(y=x^2\) is the **stem media**, not a side exploration. Exact/CEMC badge **drops** (@Challenge Curator).

---

## Class identity
- **Slot:** C1 Models — parent parabola / graph language first
- **Stem media:** fixed “looking at \(y=x^2\)” view of the 3D model (Run Live Class main pane)
- **Live ask (teacher framing):** What does *this* view force us to notice about a quadratic? What stays free until we move/stretch/flip?
- **Source label:** **Adapted / not Exact Fermat** — immersive parent view ≠ Fermat 2007 #20 diagram
- **Central throughline (keep visible):** How do we recognize a quadratic — and how do tables, graphs, and \(y=x^2\)-family equations tell the same story?

## Readiness check (2 min)
- Teams can orient axes on the fixed slice (up, symmetry, vertex at origin in the parent).
- No A–E. No “what must be positive about a,b,c on a *different* contest graph” unless MD deliberately merges — default under B is **parent-first**.

## Default round sequence (~25–35 min)
1. **Reveal media** (flag): fixed view of \(y=x^2\) in 3D
2. **Entry:** “Consider the parabola represented by \(y = ax^2 + bx + c\). What do you know about \(a\), \(b\), and \(c\)?”
3. **Roles:** modeller (claims from the view) · checker (what would break the claim if we moved the surface?) · explainer (one sentence to class)
4. **Optional probe (still B):** teacher unlocks limited 3D move — same question family, not a new contest stem
5. **Freeze:** one forced claim + one unknown before any algebra dump
6. **Debrief + individual:** claim + unknown in own words

## Media control (`active_media_json` · C1 only — not FlagStrip)
| Field / cue | Teacher use under B′ |
|---|---|
| media URL / show Real-slice | **start** — this *is* the picture |
| `reveal_axes` | peel axes when ready |
| `unlock_flags` L0–L4 | progressive unlocks |
| `reveal_lateral` / `allow_3d_limited` | optional lateral + limited yaw |
| `frozen: true` | close argue → consolidation CONS-1…5 |

---

## Pathway α — Parent view → forced vs free *(intended under B)*

**Teacher move:** Stay on noticing (symmetry, vertex, opening, “same shape family”). Push “forced vs free” before naming full \(a,h,k\) catalogue (that’s C2).

**Teachable moments**

| observed_move | possible_interpretations | diagnostic_prompt | teacher_move | representation | return_prompt |
|---|---|---|---|---|---|
| “It’s symmetric” | solid entry; or vague | “Symmetric *about what*? What would break that?” | have checker propose a move | freeze-frame + axis | “So what must the equation respect?” |
| “Vertex at origin so no +k” | reading parent well; or overclaim for all quadratics | “Is that true of *every* quadratic, or of *this* view?” | contrast with a verbal “shifted” tease | parent vs imagined shift | “What’s forced *here* vs free in the family?” |
| wants a,b,c signs on a non-shown graph | A muscle memory / Exact Fermat residue | “Do we have that graph today?” | redirect to *this* media | immersive only | “What does *this* picture force?” |

**Leave open:** formal second differences; full transform catalogue (C2); Fermat coefficient hunt if not on slide.  
**Coverage note:** heavier A2.1/A2.2; light A2.5/A2.6 *language* — @Coverage Mapper rematch chips for B.

---

## Pathway β — Moves turn into full a,h,k lesson (C2 bleed)

**Teacher move:** If the room starts systematic stretch/flip/translate catalogue, **log dual evidence** — don’t pretend C2 is finished unless you intentionally merge slots. Freeze: “one move → one sentence about the equation.”

**Teachable moments**

| observed_move | possible_interpretations | diagnostic_prompt | teacher_move | representation | return_prompt |
|---|---|---|---|---|---|
| lists a,h,k like a textbook | C2 early; strong prep | “Which of those did *today’s view* force?” | sticky park “full transforms → C2” | parameter cards | “Forced by the parent view only.” |
| algebra for a without looking | escaping the media | “Can you show it on the model?” | lock algebra; unlock one move | 3D | “Point to the change.” |

**Park if:** completing-the-square appears → M4.

---

## Pathway γ — Symmetry → mean±d / difference-of-squares (zip drift)

**Teacher move:** Name it (“different throughline”), park to M2 / other module story, return to graph noticing.

**Teachable moments**

| observed_move | possible_interpretations | diagnostic_prompt | teacher_move | representation | return_prompt |
|---|---|---|---|---|---|
| “mean plus d / mean minus d” | zip jigsaw residue | “Are we reading the surface or doing an identity?” | park sticky | immersive | “What must the picture show?” |
| zeros/factoring as the goal | M2/M3 bleed | “Is today’s ask about zeros?” | redirect | parent view | “Forced claim from the view.” |

---

## Slide handoff (indices — @Module Director fills)
- TEAM_CHALLENGE **context:** fixed view of the 3D model / looking at \(y=x^2\)
- TEAM_CHALLENGE **question:** MD writes student stem for B (parent noticing / forced vs free) — **not** Exact Fermat MCQ
- Badge: **Adapted** (or “classroom media”) — never Exact CEMC under B
- OPEN_QUESTIONS blank; CONSOLIDATION optional 5-hitters after freeze

## Individual evidence
- One claim the parent view forces  
- One thing still unknown about the quadratic family (feeds lessons / C2)

## Hand-offs / asks
- @Challenge Curator: drop Exact Fermat badge for this C1 packaging; record as adapted/media stem
- @Coverage Mapper: rematch chips for B (A2.1/A2.2 + possible A2.5/A2.6 language; park rules for γ)
- @Module Director: finalize B stem + slide question text
- @Mathematical Verifier: reopen verify on the **B ask** (not Fermat #20 answer key)
- @dr eggbot / @homeslice: immersive as stem reveal, not optional-only
- Shawn: confirm C2/C3 still in first live set (scripts wait on that)
