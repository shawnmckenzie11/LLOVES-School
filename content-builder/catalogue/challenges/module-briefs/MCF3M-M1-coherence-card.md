# Coherence card — MCF3M Module 1

**Verdict:** `pass` for **C1–C3** (C1 B′ immersive; C2/C3 adapted, **no team-challenge media**). Monday review set.  
**Dual-serve:** MCF3M M1 + MCR3U M1 (graph/function language)  
**Countersign:** Coverage C1 rematch B = ok · C2 re-passed 2026-09-12 · C3 countersigned 2026-09-12 · Curator C1–C3 = adapted

## Throughline claim
Students learn to recognize a quadratic from graphs/tables and to say what a picture forces vs what stays free — then connect that to equations in the \(y=x^2\) family.  
**Expectations (codes):** overall A2; C1 chips A2.1 / A2.2 (Coverage). C2 chips A2.5 / A2.6; C3 chips A2.3 / A2.4.

## Prerequisite graph
- Before C1: read axes; open-up/open-down; vertex as a point (informal).  
- Before C2: C1 freeze claims (forced vs free).  
- Before C3: C2 parameter language (or park if C3 runs first — not recommended).  
- Not required for M1 live: factoring (M2), completing-the-square (M4).

## Challenge rows

| Slot | Intent | Revisit upgrade | Stays unresolved | Lessons that feed | Must not steal | Source |
|---|---|---|---|---|---|---|
| **C1** | Models — immersive \(y=x^2\); what do you know about \(a,b,c\)? | Graph → coefficients for *this* picture | Stretch/flip/slide algebra | M1L1–L2 (characteristics / rates) | M1L4–L6 full a,h,k investigation | **Adapted** immersive · `MCF3M-M1-C1-stem-and-slide.md` (B′ re-lock) |
| **C2** | Connect — a,h,k on \(y=x^2\) | Parameters as controlled changes | Completing-the-square | M1L4–L6 | M4L2 CTS | **Adapted** · `MCF3M-M1-C2-stem-and-slide.md` (no media) |
| **C3** | Transfer — domain/range | Context restrictions from graph/table | Formula solving / zeros-as-goal | M1L7 | M3 zeros / MCR3U M2 if zeros-primary | **Adapted** · `MCF3M-M1-C3-stem-and-slide.md` (no media; courtyard locked) |

## Transition rationale
- **C1→C2:** Persists “argue from the picture.” Changes: students *control* parameters that were “still free.” Bridge: freeze list of free claims from C1 becomes C2 agenda.  
- **C2→C3:** Persists graph-first. Changes: restrictions on x/y from context, not only parameter toys. Bridge: after transforms, ask what heights/x make sense.  
- **Missing if C2/C3 absent:** async lessons must still teach A2.5–A2.6 / A2.3–A2.4 with honest non-live tasks (Coverage gap log).

## Park rules
| If the ask is… | Park |
|---|---|
| Factoring / expand polynomials | MCF3M M2 |
| Zeros / solve by factoring | MCF3M M3 · MCR3U M2 |
| Completing-the-square / standard↔vertex | MCF3M M4 (esp. M4L2) |
| Jigsaw / difference-of-squares zip story | Do not import as M1 C1 |
| Exact Fermat 2007 #20 diagram as live Exact | Curator: bank only under B |

## Dual-serve tags
- Graph/function / parent noticing / transforms language → **MCR3U M1**  
- Zeros / max-min algebra apps → **MCR3U M2** (do not force into M1 C1)

## Lesson packets
Issue only after this card’s relevant row is `pass`. C1 packet may issue now. C2/C3 Minds-On + light CONS may issue; full text-only Team Challenge still locked.

## coherence_gate YAML
```yaml
coherence_gate:
  module: MCF3M-M1
  scale: module
  artefact: catalogue/challenges/module-briefs/MCF3M-M1-coherence-card.md
  verdict: pass  # C1–C3 stems locked
  throughline_claim: "Recognize a quadratic from a picture; say what is forced vs free; connect to the y=x^2 family."
  expectations_codes: [A2, A2.1, A2.2, A2.3, A2.4, A2.5, A2.6]
  revisit_upgrades:
    C1: "forced vs free vs every-quadratic from immersive y=x^2"
    C2: "a,h,k forced relation vs free through (2,5)"
    C3: "domain/range from graph with courtyard cuts"
  park_rules:
    - "CTS / form conversion → M4"
    - "factoring → M2"
    - "zeros-primary → M3 / MCR3U M2"
  dual_serve: [MCF3M-M1, MCR3U-M1]
  fixes_required: []  # C2/C3 Minds-On + light CONS cleared 2026-09-12; text-only TC still locked
  countersign:
    coverage: ok  # C1 B
    curator_provenance: ok  # adapted
  next_owners: [student-copywriter, Live-Class Designer, Mathematical Verifier]
```
