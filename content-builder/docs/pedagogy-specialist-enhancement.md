# Pedagogy enhancement for Content Builder specialists

Short design note: how Bloom’s Taxonomy (cognitive process dimension) and EEF FAME worked-example techniques strengthen **question bank curation/evaluation** and **module/lesson flow verification**. Parent remains the sole integrator. No LMS / Fly / IMSCC work.

## Sources (cite; do not invent beyond these)

| Framework | Source | URL |
|---|---|---|
| Bloom’s Taxonomy — cognitive process dimension | University of Waterloo, Centre for Teaching Excellence tip sheets | [Bloom’s Taxonomy](https://uwaterloo.ca/centre-for-teaching-excellence/catalogs/tip-sheets/blooms-taxonomy); [Learning activities and assessments](https://uwaterloo.ca/centre-for-teaching-excellence/catalogs/tip-sheets/blooms-taxonomy-learning-activities-and-assessments) |
| FAME worked examples | Bob Pritchard / Education Endowment Foundation blog | [Working with worked examples (FAME)](https://educationendowmentfoundation.org.uk/news/eef-blog-working-with-worked-examples-simple-techniques-to-enhance-their-effectiveness); companion [Mistakes and explanations](https://educationendowmentfoundation.org.uk/news/eef-blog-mistakes-and-explanations) |

Claims in agent prompts and contracts stay within what these tip sheets state.

## Bloom’s cognitive process dimension

Six levels (Waterloo CTE / revised Bloom): **Remember → Understand → Apply → Analyze → Evaluate → Create**.

Use for tagging and evaluating bank items and practice sequences. In math lesson practice, prefer that each **newly taught process** moves through lower→higher as support fades. Not every lesson needs **Create**.

### Rough map onto existing `support_fade` placements

| Placement (practice-sequence / bank hint) | Typical Bloom target | Notes |
|---|---|---|
| Worked / variation (with explanation) | Understand / Apply | Demonstrate reasoning; student explains how/why steps |
| Guided / partial | Apply (supported) | Aligns with FAME fading (later steps removed first) |
| Independent / core | Apply | Independence after fade |
| Retrieval / transfer | Analyze / Evaluate | Create only when the task is authentically generative |
| Extension | Analyze / Evaluate / Create | Only when brief warrants |

**Flag** banks or sequences that only quiz **Remember** when the lesson brief targets **Apply+**.

## FAME → factory roles

EEF FAME (Pritchard): **F**ading, **A**lternating, **M**istakes, **E**xplanation.

| FAME | Tip-sheet gist | Where it lives |
|---|---|---|
| **Fading** | After complete worked examples, remove solution steps in **reverse order** toward independence | Aligns with existing `support_fade` (`worked` → `variation` → `partial` → `independent` → `retrieval`). `practice-designer` sequences; `lesson-verifier` checks fade complete per process |
| **Alternating** | **I do / you do**: alternate worked with similar student-complete items before a new variation | `practice-designer` candidate order / sets; verifier `alternating_present` |
| **Mistakes** | Clearly **signposted** incorrect worked examples **only after competence**; explain why wrong | `formative-feedback-designer` feedback contract + `practice-designer` placement; never early in the fade |
| **Explanation** | Teacher think-aloud / student **self-explanation** prompts on worked examples (how/why each step) | Worked slots in practice sequence; feedback message ids; light note in `content-builder-instruction.mdc` |

## Role map

| Role | Pedagogy duty |
|---|---|
| `bank-curator` | Fill/review `catalogue/banks/` items: licence, Bloom tag, process tags; evaluate via `bank-item-evaluation` contract; hand dispositions to `practice-designer`. Legacy `question-curator` redirects here (or to practice-designer for sequence-only work). |
| `practice-designer` | Bank-aware curation when banks exist; Bloom tags on dispositions; FAME fading + alternating + mistakes timing + explanation prompts; writes `practice-sequence.json` |
| `lesson-director` | Purpose map notes **process cognitive targets** (Bloom level per newly taught process) |
| `formative-feedback-designer` | Mistakes examples + explanation prompts in the feedback contract |
| `lesson-verifier` | Bloom / FAME / flow checks; structured output per `lesson-flow-verify.schema.json` |
| **Parent** | Invokes `bank-curator` when `catalogue/banks/` exists for the module; module-level flow verify before course-wide regen; sole integrator |

## Contracts

- `catalogue/contracts/bank-item-evaluation.schema.json` — evaluation record: `bloom_level`, `fame_checks`, `student_html_allowed`, `disposition`, …
- `catalogue/contracts/lesson-flow-verify.schema.json` — verifier flow output: `central_question`, `hook_returns`, `fade_complete_per_process`, `alternating_present`, `bloom_progression_ok`, `fame_fading_ok`, …

## Out of scope

- LMS, Fly, IMSCC
- Pasting Nelson (or other restricted) stems into student HTML
- Inventing pedagogical claims beyond the Waterloo and EEF tip sheets linked above
