# Cursor handoff

Read this file, `README.md`, `rules/instructional-rules.md`, and `data/reconciliation.json` first.

## Task

Import this MCF3M input package into the existing course content builder, preserving the repository's conventions and current user changes. Inspect the repository's applicable AGENTS.md and current content schema before editing. Do not replace existing project instructions with this file.

1. Run `python3 validate_package.py` from this package directory. Read the report and provenance.
2. Find the builder's existing lesson manifest, expectation registry, rules, renderer and review interface. Explain the smallest adapter needed. If the builder already represents a lesson, match by Drive folder ID, then by stable `MCF3M-MnLi` ID. Never duplicate it based on a changed title.
3. Import all 39 verified folder identities and current Drive titles. Preserve module topic titles separately from Drive module folder names. Module 1 lives under `Module 1 - temp / Async Lessons`; do not flatten that hierarchy. Module 7 order comes from the original list.
4. Import the 53 specific expectations, nine overall expectations and seven mathematical process expectations as read-only source text. Keep sample problems separate. Integrate the processes across lessons. Preserve the raw sources for auditing. Do not rewrite Ministry wording or substitute MCR3U expectations.
5. Import the proposed curriculum links with `proposed_review_required` status. Show the partial-coverage notes to the teacher. Keep teacher-confirmed mappings if already present and report differences. Do not claim all expectations are covered merely because all codes appear.
6. Apply the language, flow, practice, model-STAR and media rules to the shared content contract. Keep `student_content`, `teacher_notes` and `provenance` distinct. Preserve locks. Avoid rewriting existing lessons merely as a side effect of the import.
7. `data/resources.json` is deliberately empty. It approves no third-party embed. Existing approved resources may be retained with their evidence; candidates still require review.
8. Report imported counts, identity matches, title differences, conflicts and next content-review tasks. Stop before a bulk generation or deployment. The useful next step is one reviewed lesson brief and a pilot built through the existing workflow.

## Boundaries

No Drive folder creation, deletion, rename or relocation. No public publishing. No changes to attendance, grades, participation, session codes, sqlite or deployment. Reuse existing teaching decks and keep asynchronous lesson IDs separate from live class IDs.

The historical source files are evidence. Current structured data and the consolidated rules explain which statements are superseded. Never restore the earlier 32-lesson inventory or treat historical in-progress statuses as fresh state.
