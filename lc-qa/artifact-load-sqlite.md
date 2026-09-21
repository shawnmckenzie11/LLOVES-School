# Artifact sqlite lock (PASS)

Re-run:

```bash
python3 -m unittest discover -s lms -p 'test_artifact_load.py' -v
```

Writes this file and `lc-qa/artifact-load-sqlite.log`.

## Repro (alc incident note)

Source: `lc-qa/incident-alc-artifact-overload-2026-09-21.md`. Session **11**, class 8, v148 / `1849339`, N=12 still in, Artifact prompts 16–18.

Fly stack, both routes, same write:

1. `GET /api/student/state` → `student_state` → `_require_active_live_attendee` → `touch_live_session_heartbeat` → `_resume_live_attendee` (`UPDATE live_session_attendees`)
2. `POST /api/student/heartbeat` → `touch_live_session_heartbeat` → `_resume_live_attendee`

Artifact on screen: `mcf3m-m1-c2-transformations` at `/static/live-media/m1c2-transforms.html`. Incident payloads had **`group_q: false`** (individual Match challenges). `live_group_members` / `live_group_responses` were 0. This run mints the same way.

The 20s heartbeat skip is forced to 0 so every poll takes that UPDATE. Production only writes when the last beat is older than 20s; the class still reached this line.

Pass bar: resume writes cover every state and heartbeat 200, 0 `database is locked`, 0 HTTP 500, 0 “Internal Server Error” / “server overloaded”, 0 open transactions.

## Why not Postgres

Postgres would be a second source of truth and a service this app does not run. The file is already the live-class store, mounted on one machine. The durable fix is to stop the two connections from holding a reserved lock across the resume UPDATE.

## Cause

`_resume_live_attendee` writes `last_heartbeat_at` on the school connection. Student `/state` and heartbeat both call it while other work uses the game-show connection on the same file. Legacy isolation kept that UPDATE’s reserved lock until `commit()`, so the other connection raised `sqlite3.OperationalError: database is locked`. Flask rendered Internal Server Error / “the server is overloaded”. Not OOM. #115 and #116 did not cover this write.

## Fix

- Both connections use autocommit, so a statement releases the write lock when it returns.
- Both connections share one process lock, so the two gunicorn threads do not interleave one connection.
- WAL + `busy_timeout` stay. `synchronous=NORMAL` is the WAL companion so a heartbeat fsync does not sit on the lock.
- If a lock still escapes, heartbeat returns JSON 503 `retry: true` and student `/state` returns the reconnect stub. The student page keeps the last Artifact frame and shows Reconnecting… / Retry. This run expects those branches not to fire.

## This run

| | |
|---|---|
| Verdict | **PASS** |
| Class | 12 students |
| Waves | 4 |
| In-flight cap | 2 |
| Wall | 555 ms |
| sqlite lock logs | 0 |
| `_resume_live_attendee` writes | 96 |
| school in_transaction | False |
| game in_transaction | False |
| HTTP | heartbeat:200=48, media:200=48, staff-state:200=4, state:200=48 |

| Path | n | med ms | p95 ms | max ms |
|---|---:|---:|---:|---:|
| heartbeat | 48 | 2 | 3 | 5 |
| media | 48 | 1 | 1 | 3 |
| staff-state | 4 | 12 | 13 | 13 |
| state | 48 | 17 | 19 | 22 |

### Errors

- none

### Lock logs

- none

## Residual

- Fly machine size is unchanged (shared-cpu-1x, 1 GB, 2 threads). Not smoked on Fly.
- A second process on the same file can still wait on `busy_timeout` (30s). Production runs one gunicorn worker.
- The 0.5s team-membership cache can lag a team edit by one poll.
