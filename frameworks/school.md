# ELC — school identity

**School:** ELC (online high school)  
**Teacher:** Shawn  
**Jurisdiction:** Ontario, Canada  
**LMS:** Canvas  

## Delivery model

ELC follows the same semester schedule as traditional in-person Ontario schools (TLDSB calendar), but delivers courses online:

| Mode | What it is |
|------|------------|
| **Asynchronous** | Canvas course module lesson pages students complete on their own schedule |
| **Synchronous** | Two scheduled 75-minute interactive live Zoom classes per week |
| **Office hours** | Flexible 75-minute open office Zoom session on Fridays |

## Curriculum focus

- Ontario Math Curriculum, Grades 11–12
- **Active course build:** MCF3M (Functions and Applications, Grade 11, University/College Preparation)
- Additional courses may be added under `courses/` later; they inherit these school frameworks

## Agent constraints

- Design for **online** learning (Canvas + Zoom), not in-person classroom assumptions
- Align all lessons/assessments to Ontario curriculum expectations
- Prefer pacing that fits the current semester in `frameworks/semester.json`
- Reference shared frameworks from course-level work; do not duplicate conflicting school rules inside a single course

## Related frameworks

| File | Topic |
|------|--------|
| [`class-structure.md`](class-structure.md) | 2×75 live + Friday office hours + async |
| [`semester.json`](semester.json) / [`semester.md`](semester.md) | Phase & calendar |
| [`canvas-lms.md`](canvas-lms.md) | LMS constraints |
| [`textbook-question-bank.md`](textbook-question-bank.md) | Textbook PDF → curated reference bank → promote into live Canvas items |
