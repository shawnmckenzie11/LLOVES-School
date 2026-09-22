# Artifact Postgres presence (PASS)

Re-run:

```bash
python3 -m unittest lms.test_live_presence_load -v
```

Writes this file and `lc-qa/artifact-load-postgres.log`.

## Repro

Thursday bar: **N=28** (MCR3U / MCR3U-2 / MCF3M headcount, not the N=12 alc sample). Artifact `mcf3m-m1-c2-transformations` at `/static/live-media/m1c2-transforms.html` with **`group_q: true`**. In-flight cap 2 (gunicorn threads). Heartbeat write window forced to 0 so every state and heartbeat poll takes the presence UPDATE. A second connection hammers `classes` on the sqlite file for the whole storm.

Fly stack this replaces:

1. `GET /api/student/state` → `touch_live_session_heartbeat` → `UPDATE live_session_attendees`
2. `POST /api/student/heartbeat` → same UPDATE

Pass bar: those updates are Postgres, sqlite attendee writes stay 0, 0 `database is locked`, 0 HTTP 500, 0 “Internal Server Error” / “server overloaded”, 0 open transactions.

## This run

| | |
|---|---|
| Verdict | **PASS** |
| Store | postgres |
| Class | 28 students |
| Waves | 4 |
| In-flight cap | 2 |
| Wall | 3195 ms |
| Postgres heartbeat writes | 336 (state + heartbeat + preview gates) |
| Sqlite attendee INSERT/UPDATE | 0 |
| `hot_sqlite_writes` | 0 |
| Game-show hammer writes | 224970 |
| School in_transaction | False |
| Game in_transaction | False |
| HTTP | heartbeat:200=112, media:200=112, preview:200=112, staff-state:200=4, state:200=112 |

| Path | n | med ms | p95 ms | max ms |
|---|---:|---:|---:|---:|
| heartbeat | 112 | 3 | 4 | 10 |
| media | 112 | 1 | 1 | 2 |
| preview | 112 | 10 | 14 | 20 |
| staff-state | 4 | 35 | 36 | 77 |
| state | 112 | 39 | 48 | 50 |

### Errors

- none

### Lock logs

- none

## alc gap

`fly.toml` does not set `LIVE_DATABASE_URL` or `DATABASE_URL`. `/health` on this process reports `live_presence=postgres` only because the test passed a URL. Production reports `sqlite` until Shawn runs `fly mpg attach` (that command restarts the app — not done here). See `lms/LIVE_PRESENCE.md`.

## Residual

- Thursday still runs on sqlite if this branch is not merged **and** the secret is not attached. Both are required. Attach alone on the current image does nothing; the image alone without the secret stays on sqlite.
- Staff `/state` still reads prompts, scores, and roster from sqlite. Those are reads. A sqlite writer outside this process (an SSH `sqlite3` with no busy timeout) can still stall the catalogue.
- Fly machine size is unchanged (shared-cpu-1x, 1 GB, 2 threads). Postgres moves the lock. It does not add CPU.
- Basic Managed Postgres is a paid cluster. WAL remains the local/dev belt when no URL is set.
