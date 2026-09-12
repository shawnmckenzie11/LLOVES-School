# MCF3M M1 C3 — Verifier notes

Mathematical Verifier · 2026-09-10T07:11:09Z  
Id: `adapted-MCF3M-M1-C3-domain-range-v1` · **adapted / not CEMC**  
Status: **verified** (wall/nozzle flags cleared by MD stem lock). Live set (2026-09-12): Coverage Mapper countersigned C3. Minds-On + light CONS cleared. **Text-only Team Challenge still locked.**

## Stem (curator)
Fountain path \(y=-0.2(x-3)^2+4.5\). Graph/table first: (1) what heights are possible? (2) which \(x\) make sense if far wall at \(8\) m and water cannot go underground? Defend domain/range from the graph.

## Independent working

Vertex / max: \((3, 4.5)\). Opens down (\(a=-0.2\)).

Ground hits (\(y=0\)):
\[
(x-3)^2 = \frac{4.5}{0.2} = 22.5, \quad x = 3 \pm \sqrt{22.5} \approx -1.743,\ 7.743.
\]

Check walls:
- \(x=0\): \(y=2.7\)
- \(x=8\): \(y=-0.5\) (underground — outside physical path)

### Recommended physical answers (courtyard \(0\le x\le 8\), \(y\ge 0\))
- **Domain:** \([0,\ 3+\sqrt{22.5}] \approx [0,\ 7.74]\) (cut before far wall where model goes negative)
- **Range (heights possible on that path):** \([0,\ 4.5]\)

### Pure math (no courtyard) — do **not** prefer as the live “answer”
- Unrestricted range: \((-\infty,\ 4.5]\) — conflicts with “cannot go underground” + graph-first defence.

## Approaches (≥2, real)
1. **Graph / sketch** — plot vertex and zeros; shade \(y\ge 0\) and \(0\le x\le 8\).
2. **Table** — evaluate at \(x=0,1,2,3,4,5,6,7,8\); spot sign change between 7 and 8.
3. **Solve \(y=0\) then intersect** with courtyard — algebraic check after graph (not first).

## Overclaims / traps
| move | issue | teacher push |
|---|---|---|
| formula-first domain from “all reals” | ignores context | “Show it on the graph.” |
| domain = \([0,8]\) | includes underground at \(x=8\) | “Is \(y\) nonnegative there?” |
| range = \((-\infty,4.5]\) | ignores ground | “Water below ground?” |
| nozzle must be at \(x=0\) | stem says not necessarily | interpret; peak at \(x=3\) is the natural nozzle reading |

## Gate scores
| gate | call |
|---|---|
| accessible entry | **pass** if graph/table first is enforced |
| source integrity | **pass** if Adapted badge stays |
| revisit value | **honest** — domain/range language (A2.3/A2.4); later tools sharpen defence |
| multiple approaches | **real** (graph / table / solve-then-intersect) |
| interesting decision | **pass** (physical cut vs full parabola) |
| classroom feasibility | **pass** (~25–35) |
| textbook feel | **risk** — mitigated only if graph-first + defend is hard-ruled |

## Flags for @Module Director / @Challenge Curator before live
1. Tighten “wall / nozzle / \(x=0\)” so teams share one courtyard reading (recommend: courtyard \([0,8]\), peak at \(x=3\) is the jet’s high point).
2. Keep **graph/table first** on the student stem; algebra is a check, not the entry.
3. Teacher key should prefer physical domain/range above, not unrestricted.

## Dual-serve
MCF3M M1 C3 OK. MCR3U M1 OK if it stays function/domain language (not zeros-as-algebra goal).


---

## Countersign (2026-09-10T09:26:08Z)

Checked `MCF3M-M1-C3-stem-and-slide.md` + `MCF3M-M1-C3-student-wording.md`:

| flag from prior verify | status |
|---|---|
| Courtyard [0, 8] + y ≥ 0 locked | **cleared** |
| Peak (3, 4.5) ≠ nozzle at x=0 | **cleared** |
| Graph/table first, algebra after | **cleared** |
| Physical key ≈ [0, 7.74] × [0, 4.5] | **unchanged** (teacher-only) |

Math-bearing student wording matches. **Countersigned.**
