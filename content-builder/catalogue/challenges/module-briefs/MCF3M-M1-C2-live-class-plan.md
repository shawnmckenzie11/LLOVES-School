# MCF3M M1C2 — live-class plan (Live-Class Designer)

**Status:** draft for Monday review · Dual-serve MCF3M M1 + MCR3U M1  
**Stem:** `MCF3M-M1-C2-stem-and-slide.md` · **Context:** `MCF3M-M1-C2-context-packet.md` · **Verify:** `MCF3M-M1-C2-verify.md`  
**Media:** **none** — empty ArtifactViewer / text-only chrome (paper, board, optional Desmos teacher-side)  
**Chips:** A2.5 / A2.6 · Adapted / not CEMC  
**Media control:** none — do **not** seed `active_media_json`. C1 peels (`reveal_axes` / `reveal_lateral` / `allow_3d_limited` / `frozen` / L-flags) are C1-only. C2: unlock ask → session freeze → optional short consolidation. Timing guesses only.

---

## Class identity
- **Slot:** C2 Connect — transforms of \(y=x^2\) through a point  
- **Ask:** three different \(y=a(x-h)^2+k\) through \((2,5)\); forced vs free parameters  
- **After C1:** students can argue coefficient claims from a fixed picture  

## Readiness check (~2 min)
- Teams can sketch \(y=x^2\) and name “stretch / flip / slide” in plain language  
- No immersive required; if Desmos is used, it’s optional teacher choice — not an `active_media_json` peel pack  

## Round sequence (~25–40 min)
1. **Unlock ask** — stem on slide / text chrome  
2. **Entry (shared, ~3 min):** sketch \(y=x^2\); try one stretch and one translate; what changed?  
3. **Roles:** modeller (builds graphs) · checker (verifies \((2,5)\) on each) · explainer (forced vs free sentence)  
4. **Work (~15–20 min):** produce three distinct equations; reject three writings of one graph  
5. **Freeze:** post three equations + one forced/free claim before answer reveal  
6. **Debrief (~5–8 min):** compare families; kill overclaims (“\(h\) must be 2”, “only one graph”)  
7. **Individual evidence:** three equations (or photo) + one sentence on forced vs free  

## Freeze prompts (OPEN_QUESTIONS)
- Our three equations: …  
- Forced by \((2,5)\): … · Still free: …  

## Teacher key (collapse — Verifier)
- Forced relation: \(k = 5 - a(2-h)^2\), \(a\neq 0\)  
- No single parameter forced in isolation  
- Sample triple in `MCF3M-M1-C2-verify.md`  

---

## Pathway α — Hands move graph, then name parameters *(intended)*

| observed_move | possible_interpretations | diagnostic_prompt | teacher_move | representation | return_prompt |
|---|---|---|---|---|---|
| three graphs, all \(h=2,k=5\) | valid special case; or thinks vertex must sit on point | “Must the vertex be at (2,5)?” | demand one example with \(h\neq 2\) | board sketches | “Which parameters stayed free?” |
| checks (2,5) by plugging | solid checker habit | “Does plugging prove they’re *different* graphs?” | compare shapes / \(a\) signs | equations side-by-side | “Point to what makes them different.” |
| says “\(a\) is forced” | overclaim | “Show another \(a\) that still hits (2,5).” | Verifier counterexample | \(y=0.5x^2+3\) | “So what *is* forced?” |

**Leave open:** completing-the-square / standard↔vertex (M4).  
**Coverage:** A2.5/A2.6 live evidence.

---

## Pathway β — Algebra-first “solve for \(a\)”

| observed_move | possible_interpretations | diagnostic_prompt | teacher_move | representation | return_prompt |
|---|---|---|---|---|---|
| only manipulates \(5=a(2-h)^2+k\) with no sketches | thin transform investigation | “Can you show me the three graphs?” | require sketches before more algebra | paper graphs | “Name a stretch and a slide you used.” |

**Coverage:** still A2.5 only if parameter *roles* stay the talk; else flag thin live evidence.

---

## Pathway γ — Completing the square / form conversion

| observed_move | possible_interpretations | diagnostic_prompt | teacher_move | representation | return_prompt |
|---|---|---|---|---|---|
| converts to standard form via CTS | M4 drift | “Do we need that to hit (2,5) today?” | sticky park → M4 | vertex form only | “Stay in \(a(x-h)^2+k\).” |

**Park → M4.** Don’t stamp M1 C2 complete via CTS.

---

## Lightweight consolidation (optional, after freeze · not a C1-style 5-pack)
| id | type | prompt | intent |
|---|---|---|---|
| C2-CONS-1 | mc/share | Is \(h\) forced to be 2? | kill overclaim |
| C2-CONS-2 | share | In one sentence: what does (2,5) force? | relation language |
| C2-CONS-3 | numeric/share | Give one more equation through (2,5) not on your freeze list | family awareness |

No team-challenge media peels. Gate only on teacher “consolidation open” if wired later.

## Slide handoff
MD fills already in stem file. Empty media pane OK. OPEN_QUESTIONS blank until freeze.

## Unknowns
- Whether Shawn expands live set beyond C1-first (Verifier marked parked until then)  
- Optional Desmos = teacher choice, not architecture peel  
