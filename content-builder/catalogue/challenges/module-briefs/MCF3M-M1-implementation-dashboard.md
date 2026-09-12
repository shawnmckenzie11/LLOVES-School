# MCF3M M1 — Implementation dashboard (skeleton)

Temporary check-in board for the challenge-led M1 pipeline. Supervisor: dr eggbot. Project lead: CEMC Contest Question Generator.

Last updated: 2026-09-12 · Course first: **MCF3M** · Module first: **M1 Introduction to the Quadratic Function**

## Current lock (CPD 2026-09-12)
- **C1** immersive Real-slice + CONS-1…5 remains live.
- **C2 + C3 waiting-room Minds-On + light CONS (3 items, after freeze): no hold.** Coverage Mapper countersigned C3 and re-passed C2. Ship both in the same LMS PR. No Fly.
- **Text-only Team Challenge still locked** for C2/C3 — empty ArtifactViewer; do not seed `active_media_json`; no Real-slice / peels.
- Exact Fermat 2007 #20 is **not** the live C1 stem (bank inspiration only). C2/C3 stay Adapted / not CEMC.

## Prior lock (Verifier, 2026-09-10 — superseded for rides)
- Was **C1 only** for the first live insert. Live id: `adapted-MCF3M-M1-C1-immersive-yx2-v1` · **Math: verified (adapted)**.
- C2/C3 verify notes remain valid; the ride hold is lifted for Minds-On + light CONS only.

## Working order (locked)

| Step | Who | Status | Notes |
|---|---|---|---|
| 1. Module frame | Module Director | **done (draft)** | `MCF3M-M1-frame.md` |
| 2. Coverage ride-along | Coverage Mapper | **gates done** | `MCF3M-M1-coverage-gates.md` |
| 3. Shortlist 3 for slots | Challenge Curator | **pass 2 up** | C1 exact + C2/C3 adapted gap-fills — `MCF3M-M1-curator-nominees-pass2.md` |
| 4. Coverage ok/park each | Coverage Mapper | **pass 1 done; pass 2 needed** | Pass1: C1 OK, C2/C3 PARK. Pass2 adapted C2/C3 awaiting call |
| 5. **Hard pause — Shawn check** | Shawn | **cleared for rides** | Adapted C2/C3 accepted; CPD 2026-09-12: no hold on Minds-On + light CONS |
| 6. Math verify | Mathematical Verifier | **C1 verified; C2 verified; C3 verified w/ flags** | See `MCF3M-M1-C2-verify.md`, `MCF3M-M1-C3-verify.md` |
| 7. Live-class plans | Live-Class Designer | **plans exist** | `MCF3M-M1-C2-live-class-plan.md`, `MCF3M-M1-C3-live-class-plan.md` |
| 8. Lesson wiring | ELC / LMS | **Minds-On + light CONS wired** | Same PR for C2 and C3. Full text-only TC still locked. |

## Coverage call (pass 1)

| Slot | Nominee | Call |
|---|---|---|
| C1 | `cemc-fermat-2007-Q20` | **OK** (conditional: strip MCQ; graph→coeff; not A2.10) |
| C2 | `cemc-fermat-2008-Q22` | **PARK** → M3/M4 (intersection counting ≠ A2.5/A2.6) |
| C3 | `cemc-fermat-2011-Q12` | **PARK** → M3 / MCR3U M2 (zeros-primary ≠ A2.3/A2.4) |

## Out of scope for M1 live trio

- Old GO trio (`pascal-2024-Q13`, `pascal-2024-Q21` family, `cayley-2024-Q25`) — wrong math family
- M2 factoring / M4 completing-the-square as live upgrades
- Student copy, HTML, LMS, slides until later stages

## MCR3U dual-tags (ride-along only)

- Function / domain / transform → tag **MCR3U M1**
- Zeros / max-min / algebra apps → usually **MCR3U M2** (park for later, don't force into this trio)

## Artefact index

| Artefact | Path |
|---|---|
| Module frame | `catalogue/challenges/module-briefs/MCF3M-M1-frame.md` |
| Coverage gates | `catalogue/challenges/module-briefs/MCF3M-M1-coverage-gates.md` |
| Coverage ok/park | `catalogue/challenges/module-briefs/MCF3M-M1-coverage-ok-park.md` |
| Curator nominees | `catalogue/challenges/module-briefs/MCF3M-M1-curator-nominees.md` (+ `.json`) |
| This dashboard | `catalogue/challenges/module-briefs/MCF3M-M1-implementation-dashboard.md` |

## Next action

**ELC / LMS** — merge the C2+C3 Minds-On + light CONS PR when CI is green. Do **not** Fly-deploy unless Shawn asks. Do **not** unlock C2/C3 Team Challenge media.

## Coverage Mapper update (pass 2)
- C1 exact: OK (conditional)
- C2 adapted transforms: **OK for M1** (labeled adapted)
- C3 adapted domain/range: **OK for M1** (labeled adapted)
- Detail: `MCF3M-M1-coverage-ok-park-pass2.md`
- Next (2026-09-12): **no hold** on Minds-On + light CONS. Text-only TC still locked.

## Visual / public Module Engineer (in flight)

| Layer | Path | Status |
|---|---|---|
| UI/UX plan (Mobbin) | `docs/module-engineer/public-ui-ux-plan.md` | ready |
| Content contract (MD) | `MCF3M-M1-visual-content-contract.md` | ready |
| Coverage chips | `MCF3M-M1-visual-coverage-layer.md` | ready |
| Curator cards | `MCF3M-M1-visual-curator-layer.md` | ready |
| Live-class (teacher) | `MCF3M-M1-visual-liveclass-layer.md` | ready |
| Verifier | `MCF3M-M1-visual-verifier-layer.md` | C1 verified; C2 verified; C3 verified w/ flags. Rides: C1 immersive + C2/C3 Minds-On/CONS |
| Host | calc.mckenzian.com | build PR https://github.com/shawnmckenzie11/LLOVES-School/pull/29 |

Hard pause on the **adapted trio** is lifted for Minds-On + light CONS. C2/C3 remain Adapted / not CEMC. Text-only TC still locked.



## Verifier pre-clear (C2/C3) — 2026-09-10T07:11:09Z
- **C2** transforms through (2,5): **verified**. Forced relation \(k=5-a(2-h)^2\); no lone parameter forced. Notes: `MCF3M-M1-C2-verify.md`.
- **C3** fountain domain/range: **verified with flags**. Physical domain ≈ [0, 7.74], range [0, 4.5]. Tighten wall/nozzle wording before live. Notes: `MCF3M-M1-C3-verify.md`.
- Live set (2026-09-12): C1 immersive + C2/C3 Minds-On + light CONS. Text-only TC still locked.
