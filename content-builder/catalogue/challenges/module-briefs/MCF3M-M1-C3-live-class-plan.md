# MCF3M M1C3 — live-class plan (Live-Class Designer)

**Status:** draft for Monday review · Dual-serve MCF3M M1 + MCR3U M1  
**CPD 2026-09-12:** Coverage Mapper countersigned C3. Wire fully clear. **No hold** on waiting-room Minds-On + light CONS. **Text-only Team Challenge still locked.**  
**Stem:** `MCF3M-M1-C3-stem-and-slide.md` · **Context:** `MCF3M-M1-C3-context-packet.md` · **Verify:** `MCF3M-M1-C3-verify.md`  
**Media:** **none** — empty ArtifactViewer / text-only chrome (paper sketch + table)  
**Chips:** A2.3 / A2.4 · Adapted / not CEMC  
**Courtyard lock:** \(0\le x\le 8\), \(y\ge 0\), model \(y=-0.2(x-3)^2+4.5\) · peak \((3,4.5)\) is jet high point (not “nozzle at \(x=0\)”)  
**Media control:** none — do **not** seed `active_media_json`. Unlock ask → session freeze → optional short consolidation. Timing guesses only.

---

## Class identity
- **Slot:** C3 Transfer — domain/range with context, graph/table-first  
- **Ask:** heights possible; which \(x\) make sense; defend from graph/table  
- **After C1–C2:** coefficient talk + controlled \(a,h,k\)  

## Readiness check (~2 min)
- Teams can plot/table a simple parabola and shade a region  
- Enforce **graph/table first** — no formula sheet as entry  

## Round sequence (~25–35 min)
1. **Unlock ask** — courtyard reading on slide  
2. **Entry (~3 min):** sketch or table the model; mark ground \(y=0\) and walls \(x=0,8\)  
3. **Roles:** modeller (graph/table) · checker (“is \(y\ge 0\) here?”) · explainer (domain/range defence sentence)  
4. **Work (~12–18 min):** heights possible; sensible \(x\); shade physical path  
5. **Freeze:** stated domain + range + one defence sentence from the picture/table  
6. **Debrief (~5–8 min):** kill domain=\([0,8]\) (underground at \(x=8\)); kill range \((-\infty,4.5]\)  
7. **Individual evidence:** domain + range + one graph/table defence sentence  

## Freeze prompts (OPEN_QUESTIONS)
- Heights possible (range): …  
- \(x\) that make sense (domain): …  
- Because (from graph/table): …  

## Teacher key (collapse — Verifier)
- Prefer physical domain \(\approx[0,\ 3+\sqrt{22.5}]\approx[0,\ 7.74]\)  
- Range on that path: \([0,\ 4.5]\)  
- Algebra check after graph OK; not the entry  

---

## Pathway α — Graph/table invent bounds *(intended)*

| observed_move | possible_interpretations | diagnostic_prompt | teacher_move | representation | return_prompt |
|---|---|---|---|---|---|
| shades \(y\ge 0\) and cuts before \(x=8\) | solid physical reading | “Where does the model go underground?” | table \(x=7,8\) | sketch + table | “So what’s your domain sentence?” |
| range = \([0,4.5]\) from vertex + ground | strong | “Could water be above 4.5 on this path?” | point at vertex | graph | “What heights are possible?” |
| ignores walls, uses all reals | pure-math drift | “What’s the courtyard?” | re-draw walls | context sketch | “Which \(x\) make sense *here*?” |

**Coverage:** A2.3/A2.4 live evidence.

---

## Pathway β — Max height / zeros become the goal

| observed_move | possible_interpretations | diagnostic_prompt | teacher_move | representation | return_prompt |
|---|---|---|---|---|---|
| only solves \(y=0\) / “find the zeros” | M3 bleed | “Is today’s ask zeros, or what \(x,y\) make sense?” | return to shade + walls | graph | “Defend domain/range from the picture.” |
| formula-first before any sketch | textbook escape | “Show me one row of a table first.” | lock formula | table | “Now what heights look possible?” |

**Park zeros-as-goal → M3/M4** (or MCR3U M2). Return to sense-making.

---

## Pathway γ — Context ignored; pure algebra

| observed_move | possible_interpretations | diagnostic_prompt | teacher_move | representation | return_prompt |
|---|---|---|---|---|---|
| domain \(\mathbb{R}\), range \((-\infty,4.5]\) | ignores ground/walls | “Can water go underground in this courtyard?” | force \(y\ge 0\) shade | graph | “Physical path only.” |

**Coverage:** weak A2.4 if live never touches restrictions — note gap for a later lesson opportunity.

---

## Lightweight consolidation (optional, after freeze)
| id | type | prompt | intent |
|---|---|---|---|
| C3-CONS-1 | mc | At \(x=8\), is the model above ground? | kill domain=[0,8] |
| C3-CONS-2 | share | State range of heights on the physical path | [0,4.5] language |
| C3-CONS-3 | draw/share | Shade the \(x\)-values that make sense; one defence sentence | graph-first habit |

No media peels. Gate on teacher consolidation open if wired later.

## Slide handoff
MD fills in stem file. Empty media pane. OPEN_QUESTIONS blank until freeze.

## Unknowns
- Live-set expansion beyond C1 still Shawn’s call  
- Nozzle-at-wall misconception: stem already says peak ≠ nozzle at \(x=0\) — reinforce in debrief if it appears  
