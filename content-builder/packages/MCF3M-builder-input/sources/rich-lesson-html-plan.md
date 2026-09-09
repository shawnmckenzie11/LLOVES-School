# MCF3M Rich Lesson HTML Build Plan

## Outcome

Build one portable HTML lesson for every confirmed MCF3M lesson folder. Each page will share one design system, use three accessible tabs named **Minds On**, **Action**, and **Consolidation**, and run as a static file without a user account, API key, database, or build server.

The first production target is the 32 lesson folders confirmed in Drive across Modules 2 to 8. Module 1's **Async Lessons** folder is currently empty, and Module 0 has no topic folders. Those two modules need a source lesson list before generation.

## Source-of-truth rules

- Drive supplies the course hierarchy, module names, lesson titles, curriculum extracts, examples, and resource-bank material.
- Existing lesson folders are reused. No duplicate module, lesson, `examples/`, or `banks/` folders are created.
- Ministry expectations are copied only from the existing MCF3M expectation files. They are never reconstructed from memory.
- The repository stores the HTML system, manifests, validation scripts, and generated pages. Cursor owns repository implementation. ChatGPT owns pedagogy, resource research, content drafting, and Drive-facing teaching artifacts.
- Each lesson must still teach well when every third-party embed is unavailable.

## Confirmed lesson inventory

| Module | Lessons |
| --- | --- |
| 2 | Expanding, Adding and Subtracting Polynomials; Expanding Polynomials; Common Factoring; Factoring Simple Trinomials; Factoring Harder Trinomials; Special Cases |
| 3 | Relating Standard and Factored Form; Solving Quadratic Equations by Graphing; Solving Quadratic Functions by Factoring; Solving Problems Involving Quadratics; Creating a Quadratic Model from Data |
| 4 | Vertex Form; Standard and Vertex Forms by Completing the Square; Quadratic Formula; Discriminant; Quadratic Models |
| 5 | Trigonometric Ratios; Applying Primary Trig Ratios; Right-Triangle Models; Cosine and Sine Law; Acute-Triangle Models |
| 6 | Periodic Functions; Sinusoidal Functions; Transformations of the Sine Function |
| 7 | Laws of Exponents and Integer Exponents; Rational Exponents; Exponential Functions, Growth and Decay |
| 8 | Simple and Compound Interest; Compound Interest Future Value; Compound Interest Present Value; Annuities Future Value; Regular/Simple Annuities Present Value |

## Architecture Cursor should build

Use a content-first static architecture:

```text
course/
  lesson.schema.json
  resources/
    approved-resources.json
  lessons/
    m2l1.json
    ...
  src/
    lesson-template.html
    lesson.css
    lesson.js
  scripts/
    build-lessons.*
    validate-lessons.*
  dist/
    mcf3m-m2l1/index.html
    ...
```

The lesson JSON should hold content, curriculum references, prompts, worked examples, media candidates, fallbacks, and attribution. A small generator should inject that data into a shared template. Prefer compiled, self-contained pages with local CSS and JavaScript. Avoid React and a runtime server unless the existing repository already requires them.

### Required lesson data

- Course, module, lesson number, title, and short student-facing goal
- Exact overall and specific expectation references
- Prerequisite check and likely misconceptions
- Minds On prompt with an accessible first move
- Action sequence with exploration, explanation, practice, and checks for understanding
- Consolidation prompt, exit check, and model-STAR solution where a word problem is solved
- Mathematical representations and a diagram when they clarify the reasoning
- Resource entries with provider, canonical URL, embed URL if allowed, licence or terms note, accessibility note, fallback type, and last verification date
- Teacher notes kept separate from student-facing text

## Three-part lesson contract

### Minds On

Aim for 5 to 10 minutes. Activate prior knowledge with one low-floor prompt, prediction, sorting task, visual notice-and-wonder, or short interactive. Give students something useful to do before any explanation.

### Action

Use short sections rather than one long scroll. A strong default sequence is explore, name the pattern, connect representations, try a guided example, then try independently. Embed at most one primary interactive and one supporting media item unless both have distinct instructional jobs.

### Consolidation

Return to the opening idea. Ask students to explain a connection, correct an error, compare methods, or apply the idea in a small new setting. Include a brief answer check and a next-step cue.

Tabs must use real buttons, keyboard navigation, visible focus states, ARIA relationships, URL hashes, and print styles that reveal all three sections. With JavaScript disabled, all content should remain visible in reading order.

## Resource integration policy

Every external resource passes four gates:

1. **Access:** opens in a private browser window without sign-in.
2. **Delivery:** works through a stable public iframe, link, downloadable file, or locally hosted open-source package.
3. **Rights:** terms and licence permit the planned use. Do not scrape or rehost closed material.
4. **Fallback:** the lesson includes a local image, table, graph, transcript, or equivalent task.

Likely resource roles:

| Source | Best use | Offline or no-sign-in strategy |
| --- | --- | --- |
| Desmos | Graphs, sliders, transformations, regression | Use public graph links only after private-window testing. Add a local screenshot, equations, and static task. |
| GeoGebra | Geometry, trig, dynamic graphs | Prefer public materials and permitted embeds. Export or recreate a static diagram as fallback. |
| Mathigon | Manipulatives and visual explanations | Link or embed only public components whose terms allow it. Pair with a local equivalent prompt. |
| Khan Academy | Short explanation or practice | Treat as an optional public link or video where available. Core instruction stays in the page. |
| ExploreLearning Gizmos | Enrichment when school access exists | Never make it required under the no-sign-in rule. Provide a complete local alternative. |
| YouTube or public video | Brief explanation or context | Add a transcript summary and still-image or text alternative. Privacy-enhanced embeds are preferred. |
| Open-source repositories | Local interactives, diagrams, question banks, renderers | Pin versions, record licences, vendor only needed files, and remove telemetry or network assumptions. |

Do not assume that a platform's public page is embeddable. `X-Frame-Options`, content security policy, cookies, regional restrictions, and changing terms can break an iframe.

## Repository research track

Agents should investigate bounded categories and return evidence in a common format. Useful targets include:

- MathLive or CortexJS for accessible math input and static expression rendering
- KaTeX for fast local equation rendering
- JSXGraph and function-plot for lightweight local graphs
- GeoGebra and Desmos public embedding rules and fallback export options
- Mathigon open-source packages and Polypad reuse constraints
- H5P content types that can be exported as standalone HTML without an LMS runtime
- PhET simulations relevant to functions, trigonometry, and finance, with licence checks
- MathNet and other Hugging Face or GitHub datasets for candidate problems, with dataset licence, provenance, solution quality, and grade-fit checks
- OpenStax or other openly licensed explanations and diagrams where Ontario course fit is strong

For each candidate, record: instructional purpose, relevant modules, public URL, repository URL, licence, last release or maintenance signal, bundle size, external network calls, accessibility, mobile behaviour, iframe policy, offline potential, and a recommendation of adopt, link, adapt, or reject.

Dataset content is never inserted automatically. It enters a review queue because topic labels, difficulty, wording, rights, and answer reliability need human judgment.

## Agent workflow with supervised gates

### Phase 1: Inventory and standards

**ChatGPT here:** read the Drive hierarchy and exact expectation files, reconcile lesson titles, and draft the course-level design and content rules.

**Cursor:** inspect the repository, locate its existing static-site conventions, and propose the smallest compatible folder structure.

**Your gate:** approve the final inventory, visual tone, reading level, and one representative lesson.

### Phase 2: Parallel research

Assign narrowly scoped subagents:

- Resource scout by module family: algebra/quadratics, trigonometry/periodic functions, exponents/finance
- Open-source and licence reviewer
- Accessibility and no-sign-in verifier
- Pedagogy mapper who matches each resource to Minds On, Action, or Consolidation

Each agent writes structured findings to the resource manifest. Agents do not edit production lessons directly.

### Phase 3: Build the vertical slice

Cursor builds the shared template, generator, schema, and validator. ChatGPT drafts one complete representative lesson, ideally one that exercises equations, a diagram, an interactive, practice, and a fallback. M3 graphing or M6 sinusoidal transformations would expose most technical needs.

**Your gate:** open the built file locally, test its flow as a student, and approve content density and interaction style before batch work.

### Phase 4: Generate in module batches

For each lesson:

1. ChatGPT produces a lesson brief grounded in the Drive title and exact expectations.
2. Research agents propose resources using the approved manifest.
3. A pedagogy pass chooses only resources that perform a clear job.
4. Cursor's generator builds the HTML.
5. A review agent checks mathematics, links, accessibility, licensing fields, and fallbacks.
6. You review a module sample, then approve the batch.

Work one module at a time. Modules 2 and 3 make a good first batch because they establish algebraic notation, diagrams, graphing, and problem-solving patterns reused later.

### Phase 5: Validation and release

Cursor runs automated checks. ChatGPT performs a content and student-language review. You complete a short classroom-readiness check.

## Automated acceptance tests

- Opens from the built `index.html` on desktop and mobile
- No API keys, authentication, database, service worker dependency, or required network call for core content
- No console errors when third-party requests are blocked
- All tabs work by pointer and keyboard; all content is readable without JavaScript
- Every image has useful alternative text; every video has a transcript or equivalent summary
- Every iframe has a title, lazy loading, explicit dimensions, sandbox review, and a visible fallback link
- Equations remain readable at 200% zoom and in print
- No horizontal overflow at 320 CSS pixels
- External links are checked and stored with last-verified dates
- Mathematical answers and examples pass independent verification
- Student-facing prose follows your direct-language rules
- Word-problem solutions use model-STAR plus a supporting diagram
- Lighthouse accessibility and performance targets are set after the vertical slice, then enforced consistently

## Human review checklist

- Can a student tell what to do within ten seconds?
- Does the Minds On create a reason to learn the idea?
- Does the Action connect at least two useful representations?
- Does each embedded item change what the student can see, test, or explain?
- Can the entire lesson still be completed if embeds fail?
- Is the amount of practice realistic for one lesson?
- Does Consolidation reveal understanding rather than simple completion?
- Are answers, notation, diagrams, and expectation references correct?

## Cursor handoff prompts

### Repository inspection prompt

> Inspect this repository without changing it. Identify the current frontend stack, build commands, static asset conventions, test tools, deployment assumptions, and any existing lesson or course schemas. Recommend the smallest architecture for generated, portable MCF3M lesson HTML files. Flag conflicts with the requirement that core lessons work without APIs, accounts, or a runtime server. Return proposed paths and commands only after citing the relevant existing files.

### Vertical-slice implementation prompt

> Implement the approved MCF3M lesson schema, shared three-tab template, CSS, progressive-enhancement JavaScript, generator, and validation script. Build one representative lesson from the supplied JSON. Preserve repository conventions. Do not add a framework unless the repository already uses it and it reduces total complexity. Core content must survive blocked network requests and disabled JavaScript. Add tests for keyboard tabs, print output, missing embeds, broken resource metadata, and mobile overflow.

### Batch-generation prompt

> Generate only the named module batch from approved lesson JSON and the approved resource manifest. Do not invent curriculum wording, lesson titles, URLs, answers, or licences. Fail validation when required metadata or fallbacks are absent. Produce a build report listing generated files, rejected resources, warnings, and tests.

## Recommended decision sequence

1. Fill the missing Module 1 lesson inventory.
2. Choose M3 graphing or M6 sinusoidal transformations as the vertical slice.
3. Let Cursor inspect the repo and return its compatibility report.
4. Approve one visual and interaction pattern.
5. Research and approve a small resource registry.
6. Build and test the vertical slice.
7. Batch Modules 2 and 3, then 4 to 8.
8. Revisit Module 0 and Module 1 only after their source topics are confirmed.

## Definition of done

The course has one validated HTML package per confirmed lesson folder. Pages look and behave consistently, carry exact curriculum references, contain purposeful media with local fallbacks, remain usable without accounts or APIs, and can be regenerated from reviewed lesson data without hand-editing 32 separate files.
