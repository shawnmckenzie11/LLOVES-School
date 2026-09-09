# Onboarding contract (`onboarding-store.v1`)

Logical records for verified course inputs. JSON under `catalogue/onboarding/{COURSE}/`. Not LMS storage. Not student-facing.

| Entity | Required information | Store file |
|---|---|---|
| Import batch | Package version/hash, import time, snapshot date, change summary, outcome | `batches/*.json` |
| Course/module | Course code, module order, instructional title, Drive folder title and ID | `course.json` |
| Lesson identity | Internal id, stable `MCF3M-MnLi` key, Drive id, parent id, source title, optional display title | `identities.json` |
| Curriculum source | Course, edition, type/code, exact text, sample problem, locator, text hash | `curriculum-sources.json` |
| Curriculum mapping | Lesson/section, expectation ref, rationale, partial-coverage note, origin, review status | `mappings.json` |
| Instructional rule | Stable id, scope, requirement, authority, version, enforcement owner, supersession | `rules.json` |
| Lesson revision | Content revision, source/rule deps, teacher overrides, locks, review state | `lesson-revisions.json` |
| Conflict | Record/field, existing, incoming, evidence, resolution status | `conflicts.json` |

Exact curriculum wording is immutable within a source revision. Corrections add a new revision. Student-friendly goals are authored separately.

A verified source is not an approved mapping. Passing `verify_lesson.py` does not approve a lesson.

`student-content.json`, `teacher-notes.json`, and `provenance.json` stay distinct. The student renderer allowlist is `scripts/student_fields.py`. Onboarding must not rewrite existing student copy.

## Change classes

| Change | Effect |
|---|---|
| Folder-title update | Identity/display metadata unless the title is embedded in lesson content |
| Mapping change | Brief and coverage review |
| Student-language rule | Generated prose and its review |
| Feedback rule | Interaction specs and checks |
| Live-slide rule | Live decks only; asynchronous sections stay valid |

Rebuild only when the existing workflow requests it. Locked text stays; surface the conflict.
