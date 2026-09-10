# MCF3M M1 C2 — Verifier notes

Mathematical Verifier · 2026-09-10T07:11:09Z  
Id: `adapted-MCF3M-M1-C2-yx2-transforms-v1` · **adapted / not CEMC**  
Status: **verified** (math + gates). Live set: parked until Shawn expands beyond C1-only.

## Stem (curator)
Start with \(y=x^2\). Apply vertical stretch/compress (\(a\neq 0\)), reflect in the x-axis, translate by \(h\) and \(k\). Produce **three** different graphs through \((2,5)\). Give each as \(y=a(x-h)^2+k\). Which parameters are forced by \((2,5)\), and which stay free?

## Core constraint
Point on the graph \(\iff\) \(5 = a(2-h)^2 + k\) with \(a\neq 0\).

One equation, three parameters \(\Rightarrow\) a 2-parameter family. **No single parameter is forced** in isolation.

## Sample triple (teacher key — not student slide)

| # | \(a\) | \(h\) | \(k\) | equation | check at \(x=2\) |
|---|---|---|---|---|---|
| 1 | 1 | 2 | 5 | \(y=(x-2)^2+5\) | vertex at point |
| 2 | 1 | 0 | 1 | \(y=x^2+1\) | \(4+1=5\) |
| 3 | -2 | 2 | 5 | \(y=-2(x-2)^2+5\) | opens down, vertex at point |
| 4 | 0.5 | 0 | 3 | \(y=0.5x^2+3\) | \(2+3=5\) |
| 5 | 2 | 1 | 3 | \(y=2(x-1)^2+3\) | \(2(1)+3=5\) |

Any three distinct from this family work.

## Forced vs free (the real answer)
- **Forced:** the relation \(k = 5 - a(2-h)^2\) (with \(a\neq 0\)).
- **Free:** choose any two of \(\{a,h,k\}\) (respecting \(a\neq 0\)); the third follows when determined.
- Special case \(h=2\): then \(k=5\) and \(a\neq 0\) stays free (vertex sits on the point).

## Overclaims to kill
| overclaim | counterexample |
|---|---|
| "\(h\) must be 2" | \(y=x^2+1\) (\(h=0\)) |
| "\(k\) must be 5" | \(y=x^2+1\) (\(k=1\)) |
| "\(a\) must be 1" | \(y=0.5x^2+3\) |
| "only one graph through (2,5)" | the family above |

## Approaches (≥2, real)
1. **Pick vertex then \(a\)** — put vertex at (2,5) or elsewhere, solve.
2. **Pick \(a,h\); solve \(k\)** — algebraic use of the constraint.
3. **Graphical moves on parent** — stretch/flip/slide until the curve hits (2,5); then read \(a,h,k\).

## Gate scores
| gate | call |
|---|---|
| accessible entry | **pass** (move the parent / try a vertex) |
| source integrity | **pass** if badge stays Adapted |
| revisit value | **honest** — roles of \(a,h,k\); leave completing-the-square for M4 |
| multiple approaches | **real** |
| interesting decision | **pass** (forced relation vs free choices) |
| classroom feasibility | **pass** (~25–40 with three graphs + debrief) |
| textbook feel | mild — mitigated by forced/free + three distinct graphs |

## Hard constraints for live packaging
- Forbid completing-the-square / standard↔vertex conversion (M4).
- Require **three distinct** equations, not three writings of one graph.
- Keep Exact/CEMC badge **off**.

## Dual-serve
MCF3M M1 C2 + MCR3U M1 (transform language) OK if Coverage agrees.


---

## Countersign (2026-09-10T09:26:08Z)

Checked `MCF3M-M1-C2-stem-and-slide.md` + `MCF3M-M1-C2-student-wording.md`: three distinct graphs through (2,5), forced vs free, a ≠ 0, no completing-the-square. Matches prior verify. **Countersigned.**
