# M4-L1 vs Nelson §4.1 — gap analysis

**Benchmark:** Nelson Functions 11, Chapter 4.1 *The Vertex Form of a Quadratic Function* (PDF, 2026-09-09).  
**Licence:** Commercial — use as pedagogical bar only. Never paste Nelson wording into student HTML.  
**Pilot:** `content-builder/lessons/MCF3M/M4-L1-vertex-form/` on branch `content-builder`.  
**Ontario targets for this lesson:** A2.7, A2.10 (A2.8 completing the square → M4-L2).

## Beat map

| Nelson beat | Intent | M4-L1 today | Gap | Owner to close |
|---|---|---|---|---|
| GOAL + YOU WILL NEED | Clear outcome + tools | Brief has central question; student chrome lighter | Add student-visible goal line; tools = local board / paper | director + copywriter + VED |
| LEARN ABOUT garden decision | Two models, which to use? | Fountain-arc hook: mark the peak | Missing **compare two equivalent writings / decide** | hook-curator + director |
| Ex1 dual paths (Kirsten algebra / Marc table+graph) | Same claim, two strategies | Expand + JSXGraph verify | Need explicit **dual-path worked example** on one decision problem | practice-designer + copywriter + interaction-designer |
| Ex2 max area ↔ vertex (h,k) | Read max from vertex form | Inspect vertex / max-min from sign of a | Present; weaker **context continuity** from opener | copywriter |
| Reflecting A–D | Meta questions before Apply | Consolidation retrieval only | Add short **Reflect** block after first connect | director + copywriter |
| Ex3 features + sketch + domain/range | Inspect + hand sketch | Features via board + readings | Sketch/domain/range thin or teacher-only | practice-designer + VED |
| Ex4 zeros three methods | Expand/factor, inverse ops, graph | Mostly deferred / thin | **Defer with rationale** unless A2.10 share justifies light treatment; do not steal M4-L2 | director (document) |
| Ex5 equation from graph | Vertex + point → a | Additional / not core | Optional additional practice; not core for A2.7 pilot | practice-designer |
| In Summary Key Idea + Need to Know | Durable takeaways | Consolidation “what to remember” | Add **Key Idea / Need to Know** chrome | VED + copywriter |
| Check → Practising → Extending | Graduated volume | practice-sequence support_fade + additional licence sets | Ensure fade **renders** in student HTML; Extending stays additional | engineer + practice-designer |
| Student thinking voice | Named solution voices | OpenStax / Ministry samples | Licence-safe dual-voice texture without Nelson names | copywriter |
| Tech Support calculator notes | Calculator zeros | Local JSXGraph only | Keep local; no required TI path | interaction-designer |

## What already meets the bar

- Central question and expectation scoping (A2.7 / A2.10; not A2.8).
- Hook with `returns_in` consolidation (fountain).
- Formative Check cycles + mathematically patched verifier findings.
- Provenance split; Nelson bank stems excluded from student HTML.
- Practice process map: inspect → predict params → expand-and-verify → three-form contrast.

## Acceptance for “raised to Nelson depth”

1. Student page shows a **decision/compare-two-models** beat with **two solution strategies** (algebra + graph/table), original wording.
2. Short **Reflect** (4 questions) appears before heavy Apply.
3. Consolidation has **Key Idea** + **Need to Know** blocks.
4. Support fade for core processes is visible in compiled HTML (not only JSON).
5. `check_feedback.py` and `verify_lesson.py` still PASS.
6. No Nelson / developer / licence prose in student copy.

## Remaining after a first raise pass

- Full Nelson practice volume (Extending cigarette data, etc.) — skip or replace with licensed analogues.
- Zeros-three-ways and equation-from-graph as core — defer to later M4 unless director expands scope.
- Writer exemplars for dual-voice solutions in `writer-reference/`.
