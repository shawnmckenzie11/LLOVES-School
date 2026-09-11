# MCF3M M1C1 — consolidation beat context packet (Module Director)

**Scale:** live consolidation only (after argue / progressive reveals) — **not** mid-entry formative  
**Stem lock:** B′ · `MCF3M-M1-C1-stem-and-slide.md`  
**Ask:** Consider the parabola represented by \(y=ax^2+bx+c\). What do you know about \(a\), \(b\), and \(c\)?  
**Media:** fixed immersive \(y=x^2\) (paper-like); teacher peel: axes-off → axes-on → unlocks → optional lateral  
**Dual-serve:** MCF3M M1 + MCR3U M1 · chips for this live: A2.1 / A2.2 (Coverage)

---

## When this packet fires
Only **after** teams have argued from the picture and teacher has run the planned reveals. Consolidation cements; it does not reopen entry.

## What students already encountered (live argue)
- Immersive view of \(y=x^2\) written as \(y=ax^2+bx+c\)
- Team claims about \(a\), \(b\), \(c\) from *this* picture
- Progressive truth changes as teacher peels (axes / unlocks / optional lateral) — same stem, sharper evidence
- Freeze: at least one defended claim + one unknown (from live plan)

## What this consolidation beat develops
Cement the **structure**: for *this* graph, which coefficient claims are forced by the picture vs still free / unknown. Name the link “graph feature → coefficient” without turning into a transform lesson.

**Target cement (examples — Live-Class chooses check-in forms):**
1. Opens upward → \(a > 0\) (here \(a = 1\) if they go exact)
2. Vertex on origin / no sideways shift → \(b = 0\) (with care: “no linear term” language OK)
3. Passes through origin as y-intercept → \(c = 0\)
4. Symmetry about the y-axis as *evidence*, not a new topic

## What stays unresolved (do not cement here)
| Leave open | Why / where it returns |
|---|---|
| Full roles of \(a,h,k\) / stretch-flip-slide catalogue | **C2** (A2.5/A2.6) |
| Domain/range / context walls | **C3** (A2.3/A2.4) |
| Completing-the-square / form conversion | **M4** |
| Factoring / zeros-as-goal | **M2/M3** · MCR3U M2 |
| Zip jigsaw / mean±d / DoS story | **Parked** — never consolidation content |

## Transition rationale (argue → consolidate)
- **Persists:** same picture, same \(a,b,c\) ask  
- **Changes:** from open team argument → short individual/team check-ins that lock the forced claims  
- **Why next:** reveals already changed what students can *see*; consolidation makes the coefficient language stick before async lessons

## Return task / individual evidence hook
Each student (post-consolidation):  
1. One sentence: “From this picture I know ___ about \(a\) / \(b\) / \(c\) because ___.”  
2. One sentence: “I still don’t know ___ without changing the picture.”  

Feeds async M1L1–L2 (characteristics / rates) — not a grade dump of Ministry wording.

## Handoff to @Live-Class Designer
Sequence Nearpod/PearDeck-style **consolidation** items only:
- Fire **after** argue + reveals (tie to FlagStrip post-freeze / post-peel, not entry)
- Prefer: short mc/numeric/share that re-asks coefficient claims; one share for “because from the graph…”
- Optional draw: mark the feature that forces \(a\) or \(c\)
- Do **not** insert mid-entry pulse checks in this pack

## Handoff to @Coverage Mapper
Map consolidation items to **A2.1 / A2.2** evidence only. Flag any item that drifts to A2.5/A2.6 as dual-log / not C1-done.

## coherence_gate (lesson-live)
```yaml
coherence_gate:
  module: MCF3M-M1
  scale: lesson
  artefact: catalogue/challenges/module-briefs/MCF3M-M1-C1-consolidation-beat-packet.md
  verdict: pass
  throughline_claim: "Graph features force claims about a,b,c for this y=x^2 picture."
  expectations_codes: [A2.1, A2.2]
  revisit_upgrades: {C1: "cement coefficient claims after progressive reveals"}
  park_rules: ["no C2 transform catalogue", "no C3 domain", "no zip DoS"]
  dual_serve: [MCF3M-M1, MCR3U-M1]
  fixes_required: []
  next_owners: [Live-Class Designer, Coverage Mapper, Wonder, ELC v1.0]
```
