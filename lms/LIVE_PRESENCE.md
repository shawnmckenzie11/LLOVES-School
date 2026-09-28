# Live presence (Postgres)

Student `/api/student/state` and `/api/student/heartbeat` both call `touch_live_session_heartbeat`. With an Artifact open, a class of 24–28 students hits that write on every poll. On alc that write was `UPDATE live_session_attendees` on `/data/lloves.sqlite`, shared with the Math Game Show connection, and Postgres is the store that can take that concurrency.

## What moves

| | Postgres (when a URL is set) | Sqlite (always) |
|--|--|--|
| Heartbeat `last_heartbeat_at`, `left_at` clear on resume | source of truth | not written on the poll |
| Stale sweep during polls and staff `/state` | source of truth | skipped |
| Session `active` / `ended` for the poll gate | mirrored | still the catalogue row |
| Join insert (once per person) | mirrored after the insert | still inserts the row |
| Users, rosters, prompts, grades, packs | no | yes |

`/health` adds `live_presence`: `postgres`, `postgres-down`, or `sqlite`. `school` stays `LLOVES`.

## URL

`LovesDB` calls `resolve_live_database_url`:

1. Explicit argument (`""` forces sqlite, used by tests).
2. `LIVE_DATABASE_URL`.
3. `DATABASE_URL` if it starts with `postgres://` or `postgresql://`.

Connections use autocommit and `prepare_threshold=None` so Fly's PgBouncer transaction pool (the URL `fly mpg attach` injects) does not break on prepared statements.

The app pool is LIFO inside each gunicorn worker. Production is 4 workers × 8 threads on one 1 GB machine (`lms/serve_capacity.py`). The pool stays at 4 connections per worker: extra threads wait 2 seconds and the poll returns retry JSON instead of opening more Postgres sockets. A connection that has not run a query for 15 seconds is closed and replaced. That is shorter than PgBouncer `client_idle_timeout`, which otherwise answers the next poll with `ProtocolViolation: client_idle_timeout` and `LivePresenceUnavailable`. If a dead socket still gets checked out, that one statement is retried on a new connection. `tcp_user_timeout` is 8 seconds so a silent socket cannot hold a thread until the 120s worker timeout.

Student `/api/student/state` checks the visit token before the view. That gate, landing auto-resume, and staff `/state` turn a presence blip into a JSON retry or the last sqlite roster. They do not render Flask's HTML 500. `/health` reads the cached presence label and does not ping. The idle-timeout recycle stays. A later hang was accepted sockets filling a 1×2 worker (`worker_connections` default 1000). The image command is 4 workers × 8 threads, timeout 120, keepalive 1s, 32 connections per worker, and listen backlog 64. For 8 seconds after a gunicorn worker loads, `/state` is retry JSON so a restart backlog cannot pin the threads. `/health` is not part of that shed. A soft `fly machines restart` under live polls still re-attaches before workers serve; recovery is a fresh launch (`fly machines update`) or an image deploy. See `lms/DEPLOY.md`.

## Boot

On process start the store copies **active** `live_class_sessions` and their attendees from sqlite. A class already running survives the cutover. Heartbeats after that do not `UPDATE live_session_attendees`.

## alc attach

`fly.toml` does not set `LIVE_DATABASE_URL` or `DATABASE_URL`. The postgres-capable image can be on the machine while `/health` still says `sqlite`.

Attach is manual: Actions → **Attach live Postgres** (`.github/workflows/attach-live-postgres.yml`) → Run workflow. That job lists Managed Postgres, creates `lloves-live` in `yyz` on Basic when that name is missing, runs `fly mpg attach` so `DATABASE_URL` is a postgres URL, and rolls `lloves-lms` only when `/health` is not yet `postgres`. It fails unless `live_presence` is `postgres`.

Merging the workflow file does not attach. Basic is a paid plan (2 shared vCPUs, 1 GB). Current `fly mpg create` does not prompt when `--name`, `--org`, `--region`, and `--plan` are set. If the token cannot authorize that charge, the job stops and prints:

```bash
fly mpg create --name lloves-live --org <org> --region yyz --plan basic --pg-major-version 16 --volume-size 10
fly mpg attach <cluster-id> --app lloves-lms
curl -s https://alc.mckenzian.com/health
```

Expect `"live_presence": "postgres"`. Until that curl says so, alc is still the sqlite hot path (WAL, autocommit, shared process lock).

## Local / CI

```bash
python3 -m unittest lms.test_live_presence_load -v
```

`LIVE_PRESENCE_TEST_URL` defaults to `postgresql://lloves:lloves@127.0.0.1:5432/lloves_live`. Feature-branch CI (`.github/workflows/ci.yml`) and the test job in Deploy (`.github/workflows/deploy.yml`) both start Postgres 16, wait until `pg_isready` succeeds, and set that URL. The suite does not export `LIVE_DATABASE_URL` or `DATABASE_URL`: those would attach Postgres to every `create_app()` and pull the sqlite heartbeat bars off sqlite. The run writes `lc-qa/artifact-load-postgres.md` and `.log`.
