# Enrichment ledger — MCF3M M4-L1

COO verification of R1–R9. Pedagogy was not rewritten. Nothing was committed, pushed, merged, deployed, or published.

| Field | Value |
|---|---|
| Job | `enrichment-full-revision` |
| COO job | [`111f135d.json`](../jobs/111f135d.json) · `state=verifying` · `max_concurrent_specialists=3` |
| Branch | `content-builder-gpt-onboarding-implementation-plan` |
| Inspected | 2026-09-09T14:36:06+00:00 |
| Plan pin | `16e6db249e4917e07181ebf2fd589eb7d5cf5ebb` |
| HEAD at inspect | `7ef7b7b` |
| Before fixture | `content-builder/fixtures/M4-L1-vertex-form-before-enrichment/` |
| Live review | http://127.0.0.1:8790/review/ |
| `scale-proof.json` | **Not evidence.** `ok: true` is ignored this pass. |

**Authoring vs student render.** Canonical student copy is `student-content.json`. The compiled M4 page matches that JSON (three tabs, hook inside Minds On, “The photograph cannot be marked”). `student-feedback.json` is still dumped into the page `messages` object, so hint/check copy can disagree with the stems. The review HTML on disk edits `student-content.json`; the long-running `:8790` Python process still does not.

Tests this pass: **18 passed** (`test_authoring`, `test_language`, `test_nelson_internal`, `test_pool`, `test_coo`). `test_generic_lessons.py` failed here because Playwright Chromium is missing in this environment — tabs were checked from compiled HTML and the live page instead.

---

## R1 — Canonical authoring — **partial**

- **Owner:** integration + lesson-engineer
- **Acceptance:** A teacher edit in the review UI writes `student-content.json`, rebuilds the same compiler as export, and the preview/export HTML contains the saved string. POST `/api/instruction` is refused.
- **Affected:** `review/index.html`, `scripts/serve_review.py`, `scripts/student_authoring.py`, `scripts/test_authoring.py`, M4 `student-content.json`, M4 `build/.../index.html`

**Evidence**

- Disk UI: button **Save, rebuild, preview**. Helper: “Saves a patch against student-content.json, rebuilds, and refreshes this preview. instruction.json is not an authoring target.”
- No control labelled **Save instruction.json**.
- Source `serve_review.py` POST `/api/instruction` → 410, “instruction.json is retired. Edit student-content.json via POST /api/student-content.”
- `test_authoring.py` passed, including `test_instruction_retired_and_http_save_reaches_preview` and `test_save_rebuild_preview_shows_change` (marker `SAVE_PREVIEW_MARKER_9f3c`).
- Live process (started 2026-09-09T05:00:22Z, not restarted): GET `/api/student-content` **404**; POST `/api/student-content` `{ok: false, error: "unknown endpoint"}`; POST `/api/instruction` **400** `{ok: false, error: "'course'"}` — not 410.

**Remaining**

- Restart `serve_review.py` from current source before a teacher can complete save → rebuild → preview.
- This pass did not click Save on the live UI because block textareas never loaded.

---

## R2 — model-STAR and three tabs — **pass**

- **Owner:** lesson-director + student-copywriter
- **Acceptance:** Rule text is Search / Translate / Answer / Review + diagram. Compiled student page has only Minds On, Action, Consolidation. Hook lives inside Minds On.

**Evidence**

- `.cursor/rules/content-builder-student-language.mdc`: “Do **not** use Situation / Task / Action / Result.” Table is **S**earch / **T**ranslate / **A**nswer / **R**eview.
- Live JSON parts: `minds-on` / Minds On, `action` / Action, `consolidation` / Consolidation.
- Cache-busted compiled page `http://127.0.0.1:8790/build/MCF3M/M4-L1-vertex-form/index.html?t=enrichment-ledger` tablist: **Minds On**, **Action**, **Consolidation**. No **The water**.
- Hook is the Minds On card **Look at the streams**. Body copy “Look at the water leaving the fountain…” is not a fourth tab.

**Remaining**

- M4 worked examples do not print STAR labels. Fine for these non-word-problem examples; use STAR when a word-problem solution is written.

---

## R3 — Nelson internal, no student leak — **pass**

- **Owner:** practice-designer + parent ingest
- **Acceptance:** Internal records present; RIGHTS.md; no bungee / cigarettes / libfile in student-content or export HTML; no crops directory.

**Evidence**

- `catalogue/sources/internal/nelson-mcf3m-4-1/`: `manifest.json`, `source-map.json`, `question-baseline.json`, `RIGHTS.md`. `rights_status`: `restricted_pending_review`.
- RIGHTS.md: “Do not export page crops, publisher illustrations, calculator screenshots, or source wording.”
- No `crops/` directory. `test_nelson_internal.py` passed.
- Build tree has no `bungee`, `cigarettes`, `libfile`, or `nelson-mcf3m`.
- Pool item `vf-excluded-nelson-bank` is `excluded`.

**Remaining:** none.

---

## R4 — Question pool 24–36 — **pass**

- **Owner:** practice-designer
- **Acceptance:** Pool validates; 24–36 accepted; distinct families counted separately; ≥40% of newly accepted families Analyze / Evaluate / Create or recorded exception; independently checked sample solutions; assembled lesson need not use every item.

**Evidence**

- `coverage-brief.json`: `accepted` 27, 13 `distinct_families`, `higher_order_new_family_pct` 1.0, target [24, 36].
- `test_pool.py` passed. All accepted items `answer_kind: authored`, `verified: true`.
- COO sample: `vf-read-plus-inside` → vertex (−3, 1), axis \(x = -3\); `vf-read-open-down` → (−2, 4) maximum; `vf-claim-axis-is-y` → refute, axis is \(x = 4\) not \(y = 4\).

**Remaining**

- Live `/api/pool` 404, so the candidate drawer did not render on this process (R7). Pool JSON itself is in range.

---

## R5 — Terminology lint — **pass**

- **Owner:** student-copywriter
- **Acceptance:** Registry preferred terms vertex form, standard form, factored form. No “the writing” as a form substitute. No “Some books swap.” Zeros vs x-intercepts. `language_lint` on M4 is empty, including `student-feedback.json`.

**Evidence**

- Registry and Action render: “In this course, f(x) = a(x - h)² + k is vertex form. The expanded equation f(x) = ax² + bx + c is standard form.”
- `test_language.py` passed after lint was extended to feedback messages.
- Rebuilt export has no “under the graph” / “The water”.

**Remaining:** none on student-visible M4 copy. `instruction.json` is retired and still contains old language internally.

---

## R6 — Cards and artifacts — **pass**

- **Owner:** visual + interaction + copywriter
- **Acceptance:** Explore / Worked example / Try / Discuss / Check / Optional remark with text labels and aria-hidden icons. Objects have id, capabilities, fallback. Copy does not ask to mark a static photo or refer to an equation under the graph.

**Evidence**

- Minds On: `section.card-block.is-explore`, `svg.card-icon` `aria-hidden="true"`, heading **Look at the streams**.
- Task: “Say where it is on the photograph. **The photograph cannot be marked.**”
- Objects: `fountain-photo` (`look`); `vertex-form-graph` (`set-a-h-k`, `show-vertex-form-equation`, …).
- Action stem: “Submit when the live equation labelled Vertex form matches that equation.” Live **Vertex form:** line sits above the board.
- Card kinds used on M4: explore, try, check. Schema/CSS also define worked-example, discuss, optional-remark.

**Remaining**

- No discuss / optional-remark cards on M4; worked examples still use `type: example` plus a “Worked example” cue rather than `kind: worked-example` cards. That is a card-kind mix, not the original artifact failures.

---

## R7 — Builder workflow — **partial**

- **Owner:** review UI + `serve_review`
- **Acceptance:** Persistent course/module/lesson picker. Block editors bound to student-content ids. Candidate drawer. Diff vs approved. Approve version ≠ publish. Raw JSON is internal.

**Evidence**

- Live chrome: **Lesson review**. “Student preview uses the same compiler as export. Approve is not publish.”
- Buttons: **Load**, **Approve version**, **Save, rebuild, preview**, **Save locks.json**. No **Save instruction.json**.
- Headings: Coverage, Candidate drawer, Student preview (same compiler as export), Edit student blocks, Diff versus approved version, Lock blocks.
- Course textbox `MCF3M` + Lesson combobox. No dedicated Module control.
- Source APIs exist (`/api/lessons`, `/api/student-content`, `/api/coverage`, `/api/pool`, `/api/diff`, POST `/api/approve`). `test_list_lessons_and_approve` passed against `handler_for()`.
- Live GETs 404. Onboarding: `Unexpected token '<', "<!DOCTYPE "... is not valid JSON`. Status: `/api/lessons?course=MCF3M 404`.

**Remaining**

- Restart `serve_review.py`.
- No first-class course → module → lesson picker; no autosave; no natural-language revision actions.

---

## R8 — Runtime generic — **partial**

- **Owner:** lesson-engineer
- **Acceptance:** No unconditional vertex-board bundling. Verifier does not require Vertex Form, The water, or four exact cycles. Fractions/powers readable without a CDN. Schema recursive; student-safe projection.

**Evidence**

- `build_lesson.lesson_uses_vertex_board` gates JSXGraph. M5 and M7 compiled HTML have the three tabs and do **not** include `jsxgraphcore.js`.
- `verify_lesson.py`: `GENERIC_TABS = ("Minds On", "Action", "Consolidation")`. A **The water** tab is a failure if present, not a requirement.
- Schema: three parts, recursive `card`/`example`, objects require `id`, `capabilities`, `fallback`. `student_fields.assert_student_content` whitelist.
- No CDN / `katex.min.js` on compiled M4. Fractions render as `g(x) = (1)/(2)(x + 2)² - 3`.

**Remaining**

- `VERTEX_TASKS` still hard-codes `match-plus-inside`, `predict-h`, `expand-same-graph`, `fresh-case` when a vertex board is present.
- `coo.py` imports `verify_lesson.generic_html_gates` — **that function does not exist**.
- `math_text` is still regex substitution, not a local accessible math renderer.

---

## R9 — COO scale — **partial**

- **Owner:** parent integrator
- **Acceptance:** Job states, budget, cancel/resume. M4, a trigonometry lesson, and an exponential lesson each validate, compile, and keep three tabs. A save on M4 appears in preview and export. Incomplete items stay marked incomplete.

**Evidence**

- `test_coo.py` passed. Default budget `max_concurrent_specialists=3`. Cancel → `blocked`; resume → `planned`. M4 first in rebuild order.
- This pass created [`111f135d`](../jobs/111f135d.json): `kind=enrichment-full-revision`, `state=verifying`.
- M5 `Lesson 1: Trigonometric Ratios` and M7 `Exploring Exponential Functions, Growth & Decay` JSON + compiled HTML each have Minds On / Action / Consolidation.
- Isolated save → preview is proven by `test_authoring.py`, not by live `:8790`.
- `catalogue/coo/reports/scale-proof.json` `ok: true` is **not** treated as a gate.

**Remaining**

- Live M4 save → preview → export not shown on `:8790` (R1).
- Do not run or trust `coo --proof` until `generic_html_gates` exists.
- 39-lesson rollout stays blocked.

---

## Notes for other specialists

1. **Do not revert STAR** to Situation / Task / Action / Result.
2. **Do not copy Nelson wording or crops** into student export. Rights stay `restricted_pending_review`.
3. **Restart** `content-builder/scripts/serve_review.py` so live `/api/student-content` matches source. Until then the teacher loop is broken even though tests pass.
4. **Formative-feedback-designer + copywriter:** retarget `student-feedback.json` (under the graph / this writing / expanded writing). Lint only covers `student-content.json`.
5. **Lesson-engineer:** add `generic_html_gates` or stop importing it from `coo.py`; keep vertex cycles out of the generic verifier; improve local fraction rendering (`(1)/(2)` is readable but not the plan’s accessible renderer).
6. **Review UI:** after restart, a module picker is still missing; Approve version must stay distinct from publish.
7. **Parent:** do not treat `scale-proof.json` as green; do not start the remaining 39-lesson rollout.
