**Revise the production workflow, shared components, and acceptance criteria together.** Otherwise, Cursor will polish this lesson while continuing to generate the same weaknesses in subsequent lessons.

Give Cursor the following implementation plan.

**1. Capture the current failures before changing anything**

Inspect the lesson’s rendered student view, authoring records, shared template, and active rules. Save the current lesson as a comparison fixture.

Create a revision checklist tied to actual examples:

- Developer-facing language appearing in student instructions.
- Missing explanations or abrupt transitions.
- Skills introduced without enough demonstration and practice.
- Interactives without a clear task or useful feedback.
- Weak visual hierarchy.
- Openers with little reason for students to care.

Trace each failure to its origin: source content, agent instructions, schema, component, or rendering. Fix it at that level.

**2. Reorganize the agents around these responsibilities**

Keep the parent Cursor agent as coordinator. Enhance the existing team as follows:

| Agent | Responsibility | Deliverable |
|---|---|---|
| `lesson-director` | Establish the lesson’s central question, intended understanding, prerequisite assumptions, and sequence | A short instructional brief and section-by-section purpose map |
| `student-copywriter` | Write all questions, instructions, explanations, transitions, and feedback in one consistent voice | Complete student-facing copy |
| `practice-designer` | Organize examples and questions into progressively less supported practice | Practice sequence and disposition of every candidate |
| `interaction-designer` | Specify what students manipulate, observe, and explain | Interaction specification |
| `formative-feedback-designer` | Work with the interaction designer to define checking, diagnosis, hints, retries, and progression | Feedback rules and scenario tests |
| `visual-experience-designer` | Own typography, layout, responsive behaviour, component styling, and interaction states | Shared design system and implemented components |
| `hook-curator` | Find accessible contexts and media that connect meaningfully to the lesson | Three ranked hook proposals with verified sources |
| `lesson-verifier` | Independently assess mathematics, language, flow, visual quality, and runtime behaviour | Evidence-backed acceptance report |

Do not have every agent rewrite the lesson. **The copywriter owns final wording; the director owns instructional coherence; the engineer implements approved specifications.**

Use separate contexts for independent judgment and worktrees for overlapping code changes. Cursor supports custom agent models and isolated agent work. [Cursor documentation](https://cursor.com/docs/subagents)

**3. Replace the word blacklist with a complete language standard**

The revised language rules must require:

- Address the student directly.
- State what to do, what to attend to, and why it matters when that reason is useful.
- Introduce objects and notation before referring to them.
- Connect each new task to something the student has already seen.
- Use concrete verbs and precise mathematical language.
- Avoid artificial enthusiasm, repetitive encouragement, canned transitions, and unnecessary narration.
- Never expose provider configuration, integration notes, retrieval rankings, or agent instructions.
- Preserve necessary attribution through tidy source labels or credits.
- Read naturally aloud without sounding childish.

For example:

> “Manipulate the JSXGraph parameters to explore transformation behaviour.”

becomes:

> “Move the horizontal slider until the vertex is at \(x=3\). What changed in the equation?”

Enforce this structurally: separate `student_content`, `teacher_notes`, and `provenance` fields. The student renderer accepts only explicitly permitted fields. Do not rely on an agent remembering to hide internal material.

**Select the writing model through a small comparison.** Have two available models rewrite the same opening, worked example, and feedback sequence using identical context. Compare anonymous versions for naturalness, clarity, mathematical precision, and continuity. Your preference determines the copywriter’s configured model. Do not assume the strongest coding model produces the best student prose.

Save your approved examples and rejected examples with explanations as the writer’s reference set.

**4. Build deliberate repetition into the lesson schema**

Every newly taught process should normally receive:

1. A fully explained example.
2. A second example with a purposeful variation.
3. A partially completed example for the student to finish.
4. Independent practice with feedback.
5. A later retrieval or transfer question.

Treat this as a configurable default. Worked-example support should fade as students become more independent. [EEF guidance on worked examples](https://educationendowmentfoundation.org.uk/news/eef-blog-working-with-worked-examples-simple-techniques-to-enhance-their-effectiveness)

Require the practice designer to review **all 12 candidates**, assigning each to:

| Placement | Purpose |
|---|---|
| Worked example | Demonstrate reasoning and process |
| Guided practice | Practise with partial support |
| Core practice | Establish independent success |
| Additional practice | Provide further repetitions when needed |
| Extension | Introduce a meaningful new demand |
| Excluded, with reason | Record duplication, poor fit, ambiguity, or excessive difficulty |

Include all sufficiently relevant, clear candidates in the appropriate practice pool. Avoid making every student complete every item in one uninterrupted sequence.

Add reusable components for worked examples, staged hints, partial solutions, independent questions, and “Try another.” Show a manageable number at once while making further practice easy to reach.

For this builder, implement reinforcement as **practice, feedback, and revisiting ideas**. Teacher corrections improve the versioned rules and exemplars; the student runtime does not require model training.

**5. Make formative feedback a reusable interaction layer**

The feedback specialist first reads the lesson brief, task, mathematical model, and expected misconceptions. It then defines a common interaction contract:

```text
initial state
student controls
target and valid alternatives
submission evidence
checking method and tolerances
feedback conditions
hint sequence
retry behaviour
next-task generation
reset behaviour
```

Build the reusable cycle:

**Start → manipulate → submit → check → receive specific feedback → retry or try another.**

For a vertex-form task, feedback could distinguish:

- Correct horizontal position, incorrect vertical position.
- Correct vertex, incorrect opening direction.
- Correct position and direction, incorrect width.
- A correct graph, followed by a separate explanation prompt.

Feedback should describe observable evidence:

> “Your vertex is at \((3,1)\). The target vertex is at \((3,4)\). Keep the horizontal position and adjust the height.”

Do not infer a student’s mental misconception from a slider value alone. Ask a follow-up when the cause is uncertain.

Use deterministic mathematical checks for supported responses. Do not claim to automatically grade unrestricted explanations without a suitable evaluator. Those can use a self-check rubric or teacher review.

Keep the wrapper independent of JSXGraph so it can also support draggable diagrams, tables, matching tasks, number lines, and other tools. Each tool supplies its own checker.

**6. Give the visual designer ownership of both interfaces**

Design the **student lesson** and **teacher review page** as distinct views sharing a visual system.

| Student lesson | Teacher review page |
|---|---|
| Clear three-part navigation | Lesson outline and review status |
| Comfortable reading width | Student preview beside editable content |
| Consistent examples and practice layouts | Resource comparison and selection reasons |
| Legible equations and diagrams | Visible source and integration metadata |
| Obvious controls, hints, and feedback | Section locking and revision comparison |
| Responsive media and graph containers | Targeted regeneration controls |

Define shared typography, spacing, colour, surfaces, focus states, buttons, and feedback patterns before styling individual pages.

Require browser inspection at phone and desktop sizes, including long equations, expanded solutions, incorrect-answer feedback, and unavailable media. Screenshot comparison and keyboard checks must accompany the design review.

**7. Make the hook part of the instructional sequence**

The hook curator proposes three possibilities:

- An everyday situation or visual puzzle.
- A short clip, image, or interactive.
- A surprising claim, playful question, or appropriate joke.

Each proposal must explain:

- Why a student could find it interesting without already liking mathematics.
- What the student will predict, notice, or decide.
- How it leads to the intended understanding.
- Where the lesson returns to it.

Select one coherent context. For vertex form, a fountain image could support a prediction about the highest point, a graph-positioning task, and a final explanation of how the model changes.

Verify media access and reuse conditions. Include meaningful text alternatives. Avoid requiring students to understand an unfamiliar cultural reference to enter the mathematics.

**8. Implement in this dependency order**

1. **Parent agent:** audit failures and establish shared content contracts.
2. **Lesson director:** produce the revised instructional brief.
3. **Parallel work:** practice design, hook research, visual-system design, and writing-model comparison.
4. **Interaction and feedback designers:** jointly specify the tasks and checking behaviour.
5. **Student copywriter:** synthesize the selected material into one continuous lesson.
6. **Engineers:** implement shared components and rebuild the pilot.
7. **Independent verifier:** inspect the rendered result and test mathematical feedback cases.
8. **Parent agent:** resolve findings and present the before/after lesson for your review.

Shared schemas and components have one integration owner. Agents submit changes against those contracts rather than independently inventing incompatible formats.

**9. Turn your feedback into durable improvements**

Every accepted correction must update the relevant shared rule, exemplar, component, or test. Preserve teacher-locked content and show conflicts when a shared change affects it.

The pilot is complete when:

- Internal notes cannot leak into the student view.
- The lesson has a recognizable question and coherent progression.
- Each new process has sufficient examples and accessible further practice.
- Interactive feedback is mathematically correct and actionable.
- Styling is consistent across normal, expanded, error, and mobile states.
- The hook connects to the lesson’s conclusion.
- Your approved voice is represented in reusable writing examples.

Then test the revised system on **one substantially different lesson**, such as trigonometry, before regenerating the course. That checks whether the improvements belong to the builder or only work for the original quadratic lesson.

