# Instruction — M4-L1-vertex-form

- **lesson_id:** `M4-L1-vertex-form`
- **brief_path:** `content-builder/lessons/MCF3M/M4-L1-vertex-form/lesson-brief.json`
- **skipped_locked_keys:** none (`locks.json` is absent)

## Approved question roles

| Role | Status | Pick |
|---|---|---|
| Diagnostic | approved | question-candidates diagnostic **rank 2**: predict vertex of \(f(x)=(x+3)^2+1\), then check sliders on the **local JSXGraph** board (not a required GeoGebra embed; catalogue analogue `geogebra-cxk4xt4u`) |
| Guided example | approved | guided_example **rank 1**: OpenStax \(y=-3(x+2)^2+4\), including their worked reading \(h=-2\). `openstax-ca2e-m51274-vertex-form-definition`, CC BY-NC-SA 4.0 |
| Practice | approved | OpenStax verbal advantage item, rewritten so “standard form” is not Ontario’s \(ax^2+bx+c\). Plus Ministry A2.7 sample **verbatim** as a second practice (`lms/seeds/mcf3m_expectations.json`). Do not invent expanded coefficients. |
| Transfer | approved | Spec `fresh_case_equation` \(f(x)=-0.5(x-2)^2+4\). Not MathNet `0gz2`. |

No Nelson commercial stems in student-facing copy.

---

## Minds On

### Student

You already know how stretching, flipping, and sliding change the graph of \(y=x^2\). This lesson is about reading a quadratic written in vertex form:

\[
f(x)=a(x-h)^2+k
\]

That writing is useful because the vertex, the axis of symmetry, and whether the graph has a maximum or a minimum can be read from the equation — if you know how to look.

**Before you move anything**, look at this equation:

\[
f(x)=(x+3)^2+1
\]

Name the vertex of this parabola. Write it as a point.

You will check on the graph tool in Action. Do not move the sliders yet.

### Teacher notes

Activates the intended understanding: vertex form makes the vertex readable. Diagnostic is question-candidates diagnostic rank 2 (brief h-sign example). Listen for \((3,1)\) or a habit of negating every visible number; the check is the local JSXGraph board, not GeoGebra. Do not confirm the vertex before they set sliders in Action. Ontario codes stay here: A2.10 vertex-form inspection (A2.5/A2.6 are prerequisite, not a new target). No Nelson stems.

---

## Action

### Student

First you will follow a worked reading of one vertex-form equation. Then you will use the graph tool: check the vertex you named, predict what a single slider will do, move that slider, and explain what changed. After that, two practice questions — one about what this writing is for, and one where you expand vertex form and compare graphs.

In this course, \(f(x)=a(x-h)^2+k\) is **vertex form**. The expanded writing \(f(x)=ax^2+bx+c\) is **standard form**. Some other books swap those names; we will not.

### Guided example

The equation is

\[
y=-3(x+2)^2+4
\]

Name the vertex, say whether the graph has a maximum or a minimum, and say how you knew.

Worked reading (OpenStax *College Algebra 2e*, §5.1, Figure 5 paragraph, CC BY-NC-SA 4.0). Some books call this writing “standard form.” In this course it is vertex form.

Since \(x-h=x+2\) in this example, \(h=-2\). In this form, \(a=-3\), \(h=-2\), and \(k=4\). Because \(a\) is negative, the parabola opens downward. The vertex is at \((-2,4)\).

That vertex is a maximum.

Source: https://openstax.org/books/college-algebra-2e/pages/5-1-quadratic-functions

### Interactive bridge

The graph tool is the local board (parabola, vertex marker labelled \((h,k)\), dashed axis of symmetry, live vertex-form equation, the same function expanded as \(ax^2+bx+c\), and a short table of shared points). It is not a required GeoGebra page.

**Check your Minds On prediction.** Set the sliders so the live equation is \(f(x)=(x+3)^2+1\). Does the vertex marker match the point you named?

**Return to the starting equation** \(f(x)=2(x+3)^2+1\).

**Prediction.** Before you move a slider, look at the equation. Where is the vertex? What is the axis of symmetry? Is this a maximum or a minimum? Then predict: if you change only \(h\), what will happen to the graph and to the equation?

**Action.** Move exactly one slider — start with \(h\). Watch the live equation, the vertex marker, and the dashed axis. You may later repeat with only \(a\), or only \(k\).

**Interpretation.** After you moved one slider, compare the graph and the equation. What changed? What stayed the same? How can you read the new vertex from the equation? Does the expanded form show a different parabola, or the same one?

**If the graph tool is not available:** On paper, use \(f(x)=2(x+3)^2+1\). Name the vertex, the axis, and whether this is a maximum or a minimum, then sketch. Change only \(h\) to 2, rewrite the equation, and sketch again. Expand the starting equation and check that a few table points still sit on the same sketch.

### Practice

**Practice 1.** In this course, \(f(x)=a(x-h)^2+k\) is called vertex form. What can you read from it that is harder to see when the same function is written as \(f(x)=ax^2+bx+c\)?

(Adapted from OpenStax *College Algebra 2e* §5.1, CC BY-NC-SA 4.0, https://openstax.org/books/college-algebra-2e/pages/5-1-quadratic-functions. Their wording asks about writing a quadratic in “standard form”; they mean \(a(x-h)^2+k\). That is not what this course means by standard form.)

**Practice 2.** Ontario sample:

> Given the vertex form \(f(x)=3(x-1)^2+4\), express the equation in standard form. Use technology to compare the graphs of these two forms of the equation.

In this course, standard form means \(f(x)=ax^2+bx+c\). Expand the vertex form yourself first. Then compare the graphs — you may use the graph tool. Do not skip the expansion by only reading a line off the board.

### Teacher notes

Guided example is OpenStax rank 1; keep their \(h=-2\) reading. This is inspection, not a new transformations lesson. Interactive starting equation and sliders are from `interaction-spec.json` (\(a=2\), \(h=-3\), \(k=1\)); do not add applet parameters. Prefer moving \(h\) first (h-sign). Then \(a\) (stretch/reflection/max vs min) and \(k\) (vertical shift) one at a time so a-vs-k does not collapse. Live expansion + table target expand-changes-graph; axis should stay \(x=h\), not \(y=h\) or \(y=k\).

Practice 1 listen-for (OpenStax solution element, not a student key to print): the vertex is easy to identify in this writing. Students may also name axis and maximum or minimum.

Practice 2 is the Ministry A2.7 sample verbatim from `lms/seeds/mcf3m_expectations.json` (examples[0] on code A2.7). The seed has no key. Do not invent or print expanded coefficients in student copy. Completing the square is Lesson 2 (A2.8).

Fallback is the spec’s paper task. JSXGraph licence: MIT, bundled. GeoGebra `cxk4xt4u` is optional teacher analogue only (non-commercial terms if opened).

---

## Consolidation

### Student

**A fresh case.** Here is a new equation. Do not move the sliders yet.

\[
f(x)=-0.5(x-2)^2+4
\]

Name the vertex, the axis of symmetry, and whether this is a maximum or a minimum. Then move one parameter to check.

If you are on paper: sketch from the equation, then change one of \(a\), \(h\), or \(k\) and sketch again.

**What to remember**

Vertex form is \(f(x)=a(x-h)^2+k\). You can read the vertex \((h,k)\), the axis of symmetry \(x=h\), and whether the graph has a maximum or a minimum from the sign of \(a\): negative \(a\) means a maximum; positive \(a\) means a minimum.

Match the brackets to \((x-h)\). A plus inside the brackets does not mean the vertex is on the positive side of the \(x\)-axis.

\(a\) stretches or flips the graph. \(k\) moves the vertex up or down. Those two jobs are not the same.

Expanding vertex form into \(f(x)=ax^2+bx+c\) gives the same parabola, not a new one. The graphs should land on top of each other.

Vertex form does not make the \(x\)-intercepts as obvious as factored form. Do not treat the vertex as an intercept unless it actually sits on an axis.

You do not need to complete the square in this lesson.

### Teacher notes

Transfer uses the spec `fresh_case_equation` only. Do not paste MathNet `0gz2` (or its contest numbers) as student copy. Do not solve the fresh case in student-facing text: no vertex coordinates, no axis equation keyed, no max/min answer. Family of idea only: minus-inside; maximum versus minimum from the sign of \(a\), not from \(k\); axis is a vertical line \(x=h\).

Remember list covers h-sign, a-vs-k, expand-changes-graph, axis-equation, max-min-from-a, and a light intercepts contrast (A2.10 three-form share; factored form is not a production target here). A3.3 contextual max/min is not attached. A2.8 completing the square is the next lesson.
