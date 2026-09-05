# Live session prompts (slides plugin contract)

Thin stub for interactive student responses keyed by **slide index**. The Google
Slides plugin branch will drive these APIs; this document is the contract.

## Tables

- `live_session_prompts` — one row per `(live_session_id, slide_index)`
  - `kind`: `mc` | `numeric` | `share` | `idle`
  - `payload`: JSON object (choices, question text, etc.)
  - `active`: at most one active prompt per live session
- `live_session_responses` — one row per `(prompt_id, student_id)`
  - `response_json`: student answer
  - `awarded_points`: nullable (gradebook write still stubbed)

## Staff driver

`POST /api/live-sessions/<session_id>/prompts`

```json
{
  "slide_index": 3,
  "kind": "mc",
  "payload": {
    "prompt": "Which graph is linear?",
    "choices": ["A", "B", "C", "D"]
  },
  "active": true
}
```

- `kind=idle` or `"active": false` clears the student Live response shell.
- `GET /api/live-sessions/<session_id>/prompts/active` returns the current prompt.

## Student

- Cookie session remains source of truth (no `student_id` in URLs).
- Optional bookmark: `/student/s/<visit_token>` → binds cookie → `/student/home`.
- Poll: `GET /api/student/state` includes `prompt` + `my_response`, or
  `GET /api/student/live-prompt`.
- Submit: `POST /api/student/live-prompt/response`

```json
{ "prompt_id": 12, "response": { "choice": "B" } }
```

Kinds:

| kind | `payload` (staff) | `response` (student) |
|------|-------------------|----------------------|
| `mc` | `{ prompt, choices[] }` | `{ choice }` |
| `numeric` | `{ prompt }` | `{ value: number\|null }` |
| `share` | `{ prompt }` | `{ text }` |
| `idle` | `{}` | n/a |

## Gradebook hook (stub)

`SchoolDB.apply_prompt_score_to_participation(class_id, student_id, points, …)`
is a no-op TODO for the slides-plugin branch. Do not auto-insert participation
scores from this stub.

## Security

Student home / mood / state require:

1. `student_live_session_id` in the cookie
2. live session `status = 'active'`
3. attendee `left_at IS NULL`

Otherwise student keys are cleared and the client is sent to landing.
