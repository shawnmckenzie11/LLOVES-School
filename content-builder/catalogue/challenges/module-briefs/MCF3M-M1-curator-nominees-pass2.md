# MCF3M M1 — Curator nominees pass 2 (replace C2/C3)

Updated: 2026-09-10T03:20:21Z

## Coverage pass 1 result
| Slot | Prior nominee | Call |
|---|---|---|
| C1 | `cemc-fermat-2007-Q20` | **OK** (conditional) — **kept provisional** |
| C2 | `cemc-fermat-2008-Q22` | **PARK M3/M4** — dropped |
| C3 | `cemc-fermat-2011-Q12` | **PARK M3 / MCR3U M2** — dropped |

## Corpus gap (exact PSG)
After full `problems_full.jsonl` scan (all contests): **no** native stem whose ask is a,h,k transforms of \(y=x^2\), and **no** native stem whose ask is domain/range of a quadratic. I will not force a parked contest item into those slots.

## Pass 2 trio (for Coverage re-check → Shawn)

### C1 (exact, provisional) — `cemc-fermat-2007-Q20`
- Stem status: **exact** CEMC Fermat 2007 #20
- Strip MCQ; graph → what must be true about \(a,b,c\)
- Coverage already OK (conditional)

### C2 (adapted — NOT CEMC) — `adapted-MCF3M-M1-C2-yx2-transforms-v1`
- Stem status: **adapted** (do not label as contest question)
- Through-point constraint on transforms of \(y=x^2\); equations \(y=a(x-h)^2+k\)
- Revisit: roles of \(a,h,k\) (A2.5/A2.6); leave completing-the-square for M4
- **Coverage ask:** ok-for-M1 as adapted, or reject and widen sourced pool?

### C3 (adapted — NOT CEMC) — `adapted-MCF3M-M1-C3-domain-range-v1`
- Stem status: **adapted** (do not label as contest question)
- Fountain path model; graph/table-first domain & range with courtyard walls
- Revisit: domain/range language (A2.3/A2.4); forbid formula-first
- Textbook-feel risk unless graph-first rule is enforced
- **Coverage ask:** ok-for-M1 as adapted, or reject and widen sourced pool?

## If Shawn wants exact contest sources only
Next probes (not indexed yet): Euclid/COMC PDFs, OlympiadHQ, AMC 12 quadratic-transform items. I can open that hunt after Coverage/Shawn choose **adapted OK** vs **sourced-only**.

## Gate
1. @Coverage Mapper ok/park on adapted C2 + C3 (C1 already OK).
2. Hard pause — surface the three for Shawn.
3. Verifier + Live-Class still hold.
