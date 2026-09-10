# MCF3M M1 C1 — Verifier reopen (B)

Mathematical Verifier · 2026-09-10  
Object: live B ask (immersive fixed view of \(y=x^2\)), **not** Fermat 2007 #20.  
Stem status: **adapted** · solution_status: **verified** (final MD stem+slide checked).

## What B is asking
From the fixed “looking at \(y=x^2\)” view: what does *this* picture force, and what stays free until we stretch / flip / translate?

## Forced by *this* view (true claims)
- Opens upward
- Vertex at the origin
- Symmetric about the \(y\)-axis
- Nonnegative outputs on the visible slice (\(y \ge 0\) for the parent)
- Same-shape family as the parent parabola (not a V, not a line)

## Free / not forced for the *family* (must stay unresolved in C1)
- Stretch/compress factor \(a\) (including \(a<0\))
- Horizontal/vertical shifts \(h,k\)
- Whether the live equation is written \(y=x^2\) or \(y=a(x-h)^2+k\)

## Overclaims to kill (counterexamples)
| overclaim from the parent view | counterexample |
|---|---|
| “Every quadratic has vertex at origin” | \(y=(x-2)^2\) |
| “Every quadratic opens up” | \(y=-x^2\) |
| “Every quadratic is symmetric about the \(y\)-axis” | \(y=(x-1)^2\) |
| “\(a,b,c\) signs from Fermat #20” | **wrong media** — Exact path is closed under B |

## Approaches (≥2, real)
1. **Visual noticing** — symmetry line, vertex, opening, “U”
2. **Table / points** — read \((0,0),\(\pm1,1),\(\pm2,4)\) from the view; pattern in \(y\)
3. **Equation recognition** — name \(y=x^2\), then separate *this graph* vs *what the family can become* (bridge to C2, don’t finish C2)

(1) and (2) are genuinely different entries; (3) is a third if teams already know the parent equation.

## Gate scores (B framing)
| gate | call |
|---|---|
| accessible entry | **pass** |
| source integrity | **pass** if badge stays Adapted / not Exact |
| revisit value | **honest** → C2 owns \(a,h,k\); leave completing-the-square for M4 |
| multiple approaches | **real** (visual / table / equation) |
| interesting decision | **pass** iff stem forces *forced vs free*, not “list everything about parabolas” |
| classroom feasibility | **pass** with Live-Class α/β/γ park rules |

## Hard wording constraint for @Module Director
Student stem must not ask “what must be true of *a quadratic*” without anchoring to **this view**. Prefer: “From **this** picture, what must be true? What can’t you know yet about the quadratic family?” Otherwise overclaims become the default “answer.”

## Not verifying
- Fermat 2007 #20 answer key (Exact path closed under B)
- Final student copy (student-copywriter later)
- 3D applet implementation correctness (homeslice / eggbot)

## Status
`Math: verified` on B framing + final MD stem (2026-09-10T06:33:42Z).


---

## Final stem check (2026-09-10T06:33:42Z)

Source: `MCF3M-M1-C1-stem-and-slide.md` (MD locked).

| check | result |
|---|---|
| Anchored to **this picture** | pass |
| Forced vs free | pass (asks 1–2) |
| Every-quadratic stress test | pass (ask 3) |
| Exact/CEMC creep | none |
| C2 steal | blocked (“don’t steal C2 yet”) |
| Overclaim counterexamples available | yes (teacher notes + Verifier file) |

**solution_status: verified** (B live ask + final wording).  
Public badge may move from `Math: pending` → `Math: verified (adapted)`.


---

## Teacher sample claims (2026-09-10T07:09:21Z) — not for student slide

### Forced by *this* picture (\(y=x^2\))
- Opens upward
- Vertex at the origin
- Symmetric about the \(y\)-axis
- Outputs are nonnegative (\(y \ge 0\))
- Same “U” / parabolic shape as the parent

### Still free (need a different picture / later language)
- Stretch or compress (\(a\))
- Open downward (\(a<0\))
- Horizontal / vertical shift (\(h,k\))
- Full \(y=a(x-h)^2+k\) catalogue (C2)

### Ask-3 stress (every quadratic?)
- Shape family / “can look like a parabola” — can survive as soft family talk
- Vertex at origin, y-axis symmetry, opens up, \(y\ge0\) — **break** under move/flip/stretch (use \(y=(x-2)^2\), \(y=-x^2\))

### Student-copywriter check
`MCF3M-M1-C1-student-wording.md` matches locked stem — **no math conflict**.
