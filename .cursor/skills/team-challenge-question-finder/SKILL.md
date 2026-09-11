---
name: team-challenge-question-finder
description: >-
  Finds MathNet contest stems for ELC live-class TEAM_CHALLENGE (CONTEXT +
  QUESTION) using the portfolio-anchor selection rule. Use when Shawn asks for
  team challenge questions, strand contest pools, MathNet anchors, Two-Stripe
  Flag / Furious Teacher style items, or top-N candidates per Ontario math
  strand. Query via mit-mathnet; rank and reframe via sl-mit-mathnet using
  labeled feedback. Do not invent Ministry wording.
disable-model-invocation: true
---

# Team challenge question finder

Find live-class **TEAM_CHALLENGE** contest items from MIT MathNet for ELC
online Ontario courses. Delegate corpus search to **mit-mathnet**
(`.cursor/agents/mit-mathnet.md`). Delegate ranking and CONTEXT/QUESTION
reframe to **sl-mit-mathnet** (`.cursor/agents/sl-mit-mathnet.md`) after
loading `frameworks/mathnet-supervised-labels.md`. Dataset:
[`ShadenA/MathNet`](https://huggingface.co/datasets/ShadenA/MathNet).

## The rule (verbatim)

From `frameworks/portfolio-anchor-selection.md`:

1. A student should understand the situation before seeing an equation.
2. Drawing, building a table, guessing, or testing small cases should all be legitimate starts.
3. The quadratic should explain something surprising in the situation.
4. The three anchors should vary in representation: area, input/output symmetry, and growing pattern.
5. Contest difficulty is secondary to productive entry points for traditionally weaker students.

For strands that are **not** quadratic, keep 1, 2, and 5 unchanged. Rewrite 3–4 as:

3. The **strand’s idea** should explain something surprising in the situation.
4. The six candidates should vary in representation (picture / human rule / growing pattern / table), not six clones of one contest type.

Gold archetypes (quadratic, Module 1): **The Two-Stripe Flag** (`02zc`, visual overlap), **The Furious Teacher’s Grading Rule** (`022j`, human investigation), **The Triangle of Cans** (`029j`, growing pattern). Do not treat `0hy4` (prison / crossbow kinematics) as “non-math sounding.”

## When invoked

1. Load `frameworks/portfolio-anchor-selection.md` and the course strands from `.local-data/curriculum/_team_challenge_strand_catalog.json` (or `{CODE}-specific-expectations.json`). Course order: **MCF3M, then MCR3U**, then `MAP4C`, `MBF3C`, `MCT4C`, `MCV4U`, `MDM4U`, `MEL3E`, `MEL4E`, `MHF4U`.
2. Map each strand to MathNet `topics_flat` paths (exact `list_contains`, not Viewer `/search`). Local DuckDB: `.local-data/mathnet/all_text.duckdb`, table `mathnet`.
3. Rank with the rule above. Reject lexical “story_score” that rewards named characters, ants/spiders, or olympiad games without a drawable/try-numbers entry.
4. Return **six** candidates per strand: MathNet `id`, student-facing title, why it belongs, `final_answer` if present, rewrite note, viewer/attribution. Prefer English or a one-line gloss.
5. Write compact JSON under `.local-data/curriculum/_team_challenge_top6/{CODE}_{letter}.json`. Do not commit banks, `.imscc`, or `.local-data/`. Do not upload to live Drive from this workspace; if Shawn needs `ALC / Curriculum / {CODE}`, stop and say open that workspace.

## TEAM_CHALLENGE shape

Staff live class uses CONTEXT (slide 4) then QUESTION (slide 5). Each pick must compress to that: a situation students get before algebra, then one team question. Mixed-strength Zoom; two 75-minute lives per week.

## Output

For each strand: six ranked rows. If MathNet has no honest twin of the golds, say so and list analogues. Never invent Ontario expectation statements; quote codes from seeds/extracts if mapping.
