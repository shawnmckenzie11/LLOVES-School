# Artifact sqlite lock (PASS)

Re-run:

```bash
python3 -m unittest discover -s lms -p 'test_artifact_load.py' -v
```

Writes this file and `lc-qa/artifact-load-sqlite.log`.

## Repro (alc incident shape)

1. Tip under test was v148 / `1849339` (includes #115 and #116). Fly app `lloves-lms` is still SQLite: `LLOVES_DB=/data/lloves.sqlite` on volume `lloves_data`. One machine, gunicorn 1 worker / 2 threads, shared-cpu-1x / 1 GB. There is no Postgres service in `fly.toml`.
2. Live session, Artifact `m1c2-transforms` open (`/static/live-media/m1c2-transforms.html`).
3. N=12 students (incident was N≈12, SID 11). Each wave, two at a time: `GET /api/student/state`, `POST /api/student/heartbeat`, and the media file. 4 waves. One staff `GET /state` per wave.
4. Pass bar: 0 `database is locked`, 0 HTTP 500, 0 “Internal Server Error” / “server overloaded”, 0 open transactions on either connection.

The incident note `lc-qa/incident-alc-artifact-overload-2026-09-21.md` is not in this repo. The shape above is the Ops log: sqlite locked on those two routes while the Artifact was open. Not OOM.

## Why not Postgres

Postgres would be a second source of truth and a service this app does not run. The file is already the live-class store, mounted on one machine. The durable fix is to stop the two connections from holding a reserved lock across polls.

## Cause

School tables and Math Game Show tables are two connections on one file. Python's legacy isolation begins a transaction on UPDATE and keeps the reserved lock until `commit()`. A heartbeat or student `/state` write on the school connection then makes the game connection (or the other thread) raise `sqlite3.OperationalError: database is locked`. Flask renders that as Internal Server Error / “the server is overloaded”. #115 and #116 did not cover this pair.

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
| Wall | 556 ms |
| sqlite lock logs | 0 |
| school in_transaction | False |
| game in_transaction | False |
| HTTP | heartbeat:200=48, media:200=48, staff-state:200=4, state:200=48 |

| Path | n | med ms | p95 ms | max ms |
|---|---:|---:|---:|---:|
| heartbeat | 48 | 1 | 2 | 2 |
| media | 48 | 1 | 2 | 5 |
| staff-state | 4 | 12 | 13 | 22 |
| state | 48 | 18 | 20 | 22 |

### Errors

- none

### Lock logs

- none

## Residual

- Fly machine size is unchanged (shared-cpu-1x, 1 GB, 2 threads). Not smoked on Fly.
- A second process on the same file can still wait on `busy_timeout` (30s). Production runs one gunicorn worker.
- The 0.5s team-membership cache can lag a team edit by one poll.
