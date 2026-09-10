# MCF3M M1C1 — consolidation-round pack (Live-Class Designer)

**Scale:** post-argue consolidation only — **not** mid-entry formative  
**Stem:** B′ · `MCF3M-M1-C1-stem-and-slide.md`  
**Beat:** `MCF3M-M1-C1-consolidation-beat-packet.md`  
**Coverage frame:** `MCF3M-M1-C1-consolidation-coverage-frame.md`  
**Dual-serve:** MCF3M M1 + MCR3U M1 · cement chips **A2.1 / A2.2** only

---

## When the pack fires

Teacher has already (C1 **active_media_json** control state — not a parallel FlagStrip):
1. Media live (stem picture visible)
2. Progressive peels inside the same blob: `reveal_axes` → `unlock_flags` L0–L4 → optional `reveal_lateral` / `allow_3d_limited`
3. Hit **`frozen: true`** (argue closed; encore click-out OK)

Then open **consolidation** (ResponseShell / Nearpod-style). Do **not** fire these at entry.

| `active_media_json` cue | Consolidation behaviour |
|---|---|
| `frozen: true` | Unlock pack — items 1→5 in order (or teacher peels one-at-a-time) |
| `reveal_lateral` already true | Item 5 may reference “what stayed free” — still same picture |
| pre-`frozen` / mid-argue | **no items** from this pack |

Timing guess: **8–12 min** after `frozen` (5 hitters + individual return).

### Argue peel map (teacher timing → fields)
| Live beat | Field |
|---|---|
| Axes still hidden at start | `reveal_axes: false` |
| Peel axes on | `reveal_axes: true` |
| Progressive unlocks | `unlock_flags` L0→L4 |
| Optional lateral chip | `reveal_lateral: true` (+ limited yaw only if `allow_3d_limited`) |
| Close argue | `frozen: true` → CONS-1…5 |

---

## Item sequence

### C1-CONS-1 · `mc` · opens → \(a\)
**Fires:** first after `frozen: true`  
**Prompt:** For *this* picture, what must be true about \(a\) in \(y=ax^2+bx+c\)?  
**Choices:**  
A) \(a < 0\)  
B) \(a > 0\)  
C) \(a = 0\)  
D) Can’t tell from this picture  

**Key:** B · (exact value \(a=1\) optional teacher note, not required)  
**Cement:** opens upward → \(a > 0\)  
**Teacher peel:** If many pick D, re-show axes-on freeze-frame of opening; don’t open C2 stretch talk.  
**Coverage intent:** A2.1/A2.2 · evidence

---

### C1-CONS-2 · `mc` · y-intercept → \(c\)
**Fires:** after CONS-1  
**Prompt:** This parabola meets the y-axis at the origin. What does that force about \(c\)?  
**Choices:**  
A) \(c > 0\)  
B) \(c < 0\)  
C) \(c = 0\)  
D) \(c\) could be anything  

**Key:** C  
**Cement:** y-intercept at origin → \(c = 0\)  
**Teacher peel:** Point at intercept on picture; reject “vertex formula” detours.  
**Coverage intent:** A2.1/A2.2 · evidence

---

### C1-CONS-3 · `mc` · no sideways lean → \(b\)
**Fires:** after CONS-2 (best after axes-on peel already happened in argue)  
**Prompt:** The vertex sits on the origin and the graph is symmetric about the y-axis. What must be true about \(b\) for *this* picture?  
**Choices:**  
A) \(b > 0\)  
B) \(b < 0\)  
C) \(b = 0\)  
D) \(b\) is free — we’d need more info  

**Key:** C  
**Cement:** no linear term / \(b = 0\) for \(y=x^2\)  
**Teacher peel:** If debate “symmetry ≠ \(b=0\)”, stay on *this* picture; park full family talk (Verifier overclaim killers).  
**Coverage intent:** A2.1/A2.2 · evidence  
**Risk note:** light dual-log if talk becomes “how \(b\) shifts vertex” catalogue → don’t stamp C2

---

### C1-CONS-4 · `draw` or `share` · feature → coefficient
**Fires:** after CONS-1–3  
**Prompt (draw preferred):** Mark **one** feature on the picture and name the coefficient claim it forces.  
**Alt share:** “From this picture I know ___ about \(a\) / \(b\) / \(c\) because ___.”  

**Cement:** graph feature → coefficient language (structure of the argue)  
**Teacher peel:** Spotlight 2–3 responses; reject formula-only “because.”  
**Coverage intent:** A2.1/A2.2 · evidence (strongest individual signal)

---

### C1-CONS-5 · `share` · still unknown
**Fires:** last consolidation item before return task  
**Prompt:** What about \(a\), \(b\), or \(c\) (or the quadratic family) do you **still not know** without changing this picture?  

**Cement:** “still free / unknown” without opening C2 lesson  
**Teacher peel:** Collect unknowns; sticky-park any stretch-flip-slide catalogue or domain/range into C2/C3.  
**Coverage intent:** A2.1/A2.2 · evidence of what stays open  
**Park if response is:** DoS / mean±d / zeros-as-goal / CTS — name park, don’t score as C1 cement

---

## Individual return (required; not a live poll)
Same as MD packet — after CONS-5 or instead of if time dies:  
1. “From this picture I know ___ about \(a\)/\(b\)/\(c\) because ___.”  
2. “I still don’t know ___ without changing the picture.”  

---

## Explicitly not in this pack
- Mid-entry pulse checks  
- Transform catalogue items (C2)  
- Domain/range / fountain walls (C3)  
- Exact Fermat MCQ residue  
- Zip jigsaw / mean±d / DoS

## Handoffs
- @Coverage Mapper — row map: item id · type · codes · evidence|dual-log|park  
- @Wonder — delight toasts on consolidation unlock / after CONS-4 spotlight only  
- @ELC v1.0 — wire prompts into live-session prompt/response store; gate on `active_media_json.frozen`  
- @Module Director — beat packet unchanged unless Shawn retargets B′ ask again
