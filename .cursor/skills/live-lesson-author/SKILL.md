---
name: live-lesson-author
description: >-
  Author or revise one live-class playlist and its namespaced catalogue
  items (MCF3M or MCR3U, M1–M8, C1–C4). Use when Shawn says "update MCR3U
  M1 C2", add Join/Round questions, change stems/keys/inactive defaults, or
  copy a lesson template into another course slot. Do not use for
  live-session publishing, student portal, media/whiteboard engine bugs, or
  keyed-answer feedback.
---

# Live lesson author

Load this skill before editing any live-class playlist or `live_items.json`
keys. One run = one `{CODE} M# C#`.

## Silent intake

| Field | How to decide |
|----|----|
| Course / module / slot | From the prompt (`MCR3U M1 C2`). Refuse to guess a second slot. |
| Pages | Join=1, Welcome=2, Meet=3, Round 1=4, Round 2=5, Round 3=6, Summary=7 |
| Copy vs new | Reuse `universal/…` and `course/…` refs. New stems get a new `live-class/{CODE}/M#/C#/…` key. |
| Defaults | `default_status: inactive`, `response_mode: individual` unless Shawn says otherwise |
| Done-when | Schema-valid JSON + `lms/test_live_class_metadata.py` for that slot. Not a live-session walk. |

## Write only

- `lms/seeds/live_classes/{CODE}/M{n}/C{n}.json`
- New or edited keys in `lms/seeds/live_items.json` whose ref starts with `live-class/{CODE}/M{n}/C{n}/`
- Focused assertions in `lms/test_live_class_metadata.py` for that slot

## Read, do not rewrite

- `lms/seeds/live_class_playlist.schema.json`
- `lms/seeds/live_items.schema.json`
- `lms/live_class_metadata.py`
- A sibling playlist only as a page-frame template (usually same-course `C1`, or the other course’s same slot)

## Never touch

- `lms/school_db.py`, `lms/app.py`, `lms/live_teacher_state.py`, `lms/live_prompt_feedback.py`
- `lms/static/staff_ap.js`, `lms/static/student-portal.js`
- Universal keys: `universal/question/teams-spark`, `universal/question/meet-team`, `universal/whiteboard/live-workspace`
- Course-shared media: `course/MCF3M/media/…`, `course/MCR3U/media/…`
- Any `live-class/{OTHER_COURSE}/…` key
- Drive plugin / `ALC / Curriculum`

If the request needs an engine fix (numeric box missing after copy, no resize, click-to-float hiding a card, Meet poll 1/N with empty bars, student “Your answer” missing, media/whiteboard dead on Join/Welcome/Meet, “The keyed answer is B: …” toast), **stop**. Tell Shawn to open a `live-class-engineer` chat. Those contracts live in `.cursor/agents/live-class-engineer.md`. Do not paper over them with cloned universals or per-page media copies.

## Authoring rules

- Prefer `ref` to universals. Do not clone `teams-spark` into a lesson-local item.
- Copying a question into another course means a **new** `live-class/{CODE}/…` key with the same stem/type/key. Do not retarget the other course’s ref.
- Numeric items must keep `type: "numeric"`, `integer_only`, and `placeholder`. Do not collapse a copy to `mc` / `poll`.
- Do not add `feedback_id`, `student_feedback_*`, or keyed-answer copy. Results already show the answer.
- Keep the seven-page math frame (`default_math_pages`) unless Shawn changes the page list.
- Do not commit unless Shawn asks. Do not `flyctl deploy`.

## Concurrent courses

MCF3M and MCR3U chats may run at the same time.

- One conversation owns one `{CODE} M# C#`.
- In `live_items.json`, add or edit only that slot’s keys.
- Leave universal and `course/…` keys read-only.
- Do not patch the live-class runner in an author chat.

## Prompt shape

```text
Live lesson author. Course {CODE}, module M{n}, slot C{n}.
Pages: …
Use {template} as the page template only.
Add: [stems, type, correct letter/value, default inactive].
Do not change universal items, course media, or LMS runtime.
```
