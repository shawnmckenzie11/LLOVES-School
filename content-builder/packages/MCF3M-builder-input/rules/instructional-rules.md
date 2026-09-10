# MCF3M instructional rules

This is a builder input contract, not student-facing lesson text. Sources and status are recorded below. Source snapshots are retained in `sources/`.

## Authority and scope

1. Current explicit teacher instructions take priority. Preserve teacher-locked material.
2. Current Drive IDs and titles in `data/lessons.json` govern identity and reuse. The user-provided lesson list supplies module topic names and Module 7 order.
3. `data/curriculum.json` supplies source wording. Keep curriculum text separate from student-friendly goals and proposed mappings. Never invent an expectation, problem stem, answer, resource URL or licence.
4. Later language, flow and practice corrections supersede earlier generic lesson defaults where they conflict. Historical plan inventories are superseded by the fresh 39-folder inventory.
5. Source plans include implementation proposals. No particular writing model, framework, resource, performance score or generated lesson is approved merely because it appears in a plan.
6. This package imports content inputs. It does not authorize changes to Drive organization, deployment, or a bulk lesson rebuild.

Sources: user project instructions; original lesson JSON; current Drive inventory; prior build plan; latest revision plan.

## Teaching purpose

Individual learning and practice build students' foundations. Live classes use collaborative problem solving to strengthen reasoning, justification and connections. Team-game rounds belong to the live-class format. Do not import live attendance or participation machinery into asynchronous lessons.

Build a coherent instructional progression. State the intended understanding, a central question, prerequisite assumptions and the purpose of each section before drafting. Start with an accessible action that does not require students to already like mathematics. Return to the opening context during consolidation.

Action should connect at least two useful representations. Explain what stays the same across them and what each makes easier to see. A tool must change what students can see, test or explain. Its instructional relationship comes before its implementation.

Sources: explicit teacher teaching philosophy; prior build plan; latest revision plan.

## Three-part HTML contract

Use consistent styling and exactly three main tabs, in this order:

| Tab | Instructional requirement |
| --- | --- |
| Minds On | Give an accessible first move, such as a prediction, sort or visual puzzle. Create a reason to learn. The earlier 5–10-minute target is a planning default, not a required student-facing timer. |
| Action | Explore, name the pattern, connect representations, demonstrate reasoning, then provide progressively less supported practice. Use short, connected sections. |
| Consolidation | Revisit the opening idea. Reveal understanding through explanation, error analysis, comparison or transfer. Include an answer check and a useful next step. |

One primary interactive and one supporting media item is the earlier default. Additional items need distinct instructional purposes. Static, portable core instruction must remain usable without accounts, API keys, a database, a build server, required network calls or JavaScript. Optional online resources need complete local alternatives.

Use button-based tabs, keyboard navigation, visible focus, ARIA relationships and URL hashes. Print all sections. With JavaScript disabled, show all content in reading order. Maintain useful alternative text, equivalent video summaries/transcripts, readable equations, 200% zoom and a layout that works at 320 CSS pixels.

Source: accepted prior build constraints, with current revision refinements.

## Student language and flow

- Address the student directly. Say what to do and what to notice. Explain why when it helps.
- Introduce objects, variables, units and notation before using them. Resolve unclear references such as “this graph.”
- Connect each new task to something already seen. Write one continuous lesson in one voice.
- Use short, varied sentences, active voice, concrete verbs and precise mathematical language. Read it aloud for naturalness without making it childish.
- Avoid artificial enthusiasm, repetitive encouragement, canned transitions, unnecessary narration and dramatic framing.
- Do not use these teacher-banned words: delve, testament, tapestry, beacon, crucial, navigate, realm, symphony, foster.
- Do not use em dashes to break up sentences. Use periods or commas.
- Do not use the formula “This is a [X], not a [Y].” Avoid cliché openers and closers, including “In today's fast-paced world,” “In conclusion,” “Ultimately” and “At the end of the day.”
- Never expose integration notes, provider configuration, retrieval rankings, agent instructions, raw provenance records or developer terminology in lesson directions. Necessary attribution uses tidy source labels or credits.
- Enforce this with separate `student_content`, `teacher_notes` and `provenance` fields and a student-renderer allowlist. Do not send an entire builder record to the student view.

The original blacklist is not sufficient by itself. Review context, continuity and mathematical precision. The source revision plan proposes comparing two available writing models with the same material; the teacher's preference should determine the configured copywriter model. No model has been selected by this package.

Sources: explicit student-language rules; latest revision plan.

## Practice, reinforcement and feedback

For each newly taught process, normally provide:

1. A fully explained example.
2. A purposeful variation.
3. A partially completed example.
4. Independent practice with feedback.
5. Later retrieval or transfer.

Fade support as students gain independence. This is a configurable default, not a demand for five separate activities for every small idea. Keep the required workload realistic; provide accessible further practice.

Review every supplied candidate. Classify it as worked example, guided practice, core practice, additional practice, extension, or excluded with a reason. The revision plan's “all 12 candidates” refers to the existing pilot's candidate set, not a universal requirement to fabricate twelve items per lesson. Retain sufficiently relevant, clear candidates in suitable practice pools.

Feedback should describe observable evidence and a useful next action. Support submit, check, specific feedback, hints, retry, another task and reset where appropriate. Define valid alternatives, tolerances and reset behaviour before implementation. Use deterministic mathematical checking for supported answers. Do not infer a student's mental misconception from one slider setting. Use self-check criteria or teacher review for unrestricted explanations unless an appropriate evaluator exists.

Reinforcement means practice, feedback and revisiting ideas. Accepted teacher corrections update versioned rules, examples, components or checks. They do not require student-runtime model training.

Source: latest revision plan.

## Every generated word-problem solution: model-STAR plus diagram

| Step | Teacher requirement |
| --- | --- |
| S: Search the word problem | One sentence identifying the clues, key numbers and what must be found. Highlight the relevant information. |
| T: Translate the words into an equation | One sentence explaining how the words lead to the mathematical statement, with quantities and operations identified. Do not present an unexplained initial equation. |
| A: Answer the problem | Carry out the calculation with the chosen strategy or tool and show the reasoning. |
| R: Review the solution | Check the result in minimal steps against the original conditions. |

Include a supporting diagram. Keep labels, units and the equation consistent. If supplied information is insufficient, identify what is missing. Any added numerical scenario must be clearly marked as a constructed example, never presented as the original sourced problem.

Source: explicit model-STAR instruction. The incomplete earlier “problems with no specifics” instruction is not reconstructed.

## Questions, hooks and resources

Prefer accessible, interesting contexts and low-entry problems, including for traditionally weaker students. Record why a resource fits the lesson's intended understanding. A hook must lead into the mathematics and return in consolidation; unfamiliar cultural references must not be required for entry.

The user's question-sourcing history favours real contest questions and subsequently MathNet as a candidate source, with verified original stems. Do not invent contest URLs or label generated questions as sourced. MCR3U-specific searches are not approved MCF3M banks. Desmos, GeoGebra, Khan Academy, Mathigon, YouTube, MathNet and other named providers remain candidates until checked.

For each proposed resource record purpose, module/lesson fit, canonical URL, permitted embed URL, source/repository, licence/terms, maintenance signal, bundle size, network calls, accessibility, mobile behaviour, iframe policy, offline alternative, verification date and adopt/link/adapt/reject decision. Do not assume public pages allow embedding or rehosting. Dataset entries go through review, not automatic insertion.

The four resource gates are access without sign-in, feasible delivery, permitted use and a complete fallback. Preserve provenance outside student instructions. If a full problem image is requested, use a verified source and permitted reproduction, keeping the full question legible.

Sources: explicit sourcing corrections; prior build plan; latest revision plan.

## Live Slides and portfolio boundary

The seven-slide contract in `sources/live-class-slides.metadata.json` applies to live classes only. Use the existing template and preserve slide order. Semantic names identify roles; populate by actual slide index, not nonexistent placeholders. Template ID: `1yn2JQQNyd1AfBrrRK0_BFx4ibkdIgXTgR7bu0QzesRU`.

| Index | Role | Requirement |
| --- | --- | --- |
| 1 | TITLE_CLASS | `MCF3M - M{n}C{i}`. Agenda below left; exact relevant overall and specific expectations right. |
| 2 | TEAM_INTRO | Only “Meet the Teams”. |
| 3 | OPEN_QUESTIONS_ROUND | Blank. |
| 4 | TEAM_CHALLENGE_ROUND_CONTEXT | Large “Team Challenge Round”; problem title left and context right. |
| 5 | TEAM_CHALLENGE_ROUND_QUESTION | Complete sourced team-challenge question. |
| 6 | TEAM_CHALLENGE_REFLECTION | Wait for supplied solutions from the designated solution dependency. Do not invent or pre-empt them. |
| 7 | CONSOLIDATION_ROUND | Exactly three typical math questions central to slide 1's specific expectations. |

Slide 7 prompt, exactly: “Choose question 1, 2, or 3 to solve on today’s Formative page and get feedback. You may solve more than one.”

Three live classes per module is the planning assumption. Do not confuse this with the number of asynchronous lessons. Do not include timed Do Now labels or a separate course-code subtitle. Historical dependency statuses and the MCR3M example in the source metadata are not current MCF3M state.

Reuse an existing LMS-linked deck for the same class/module/live index. ChatGPT owns teaching Slides, module Portfolio Docs and curriculum content in Drive. Cursor owns the product, LMS and repository implementation. Keep class-time clickers and attendance in the LMS. Do not modify Flask/LMS code, staff/student/IT UI, grades, participation systems, session codes, sqlite, Fly data or deployment as part of this package import.

Source: user project roles; explicit #slides contract. Existing file locations may differ from intended naming rules; no relocation is implied.

## Review and production

One copywriter owns final wording; the lesson director owns instructional coherence; engineers implement the agreed contract. The latest revision plan describes specialist roles and integration order for Cursor to adapt to its current setup. It does not require starting agents during a data import.

Teacher review should support goals/prerequisites, candidate comparisons with reasons, three-tab preview, editing and section locks, targeted regeneration and HTML export. Preserve locked content and surface conflicts.

Verify mathematics, source fidelity, purpose, natural flow, accessible practice, feedback, keyboard use, mobile layout and failed-media behaviour. Review one pilot, then a substantially different lesson before course-wide regeneration. Generate only approved lesson content and approved resources. Importing this package does not mark proposed mappings or empty resources as approved.

Sources: prior builder plan and latest revision plan.
