# Artifact group-work load (PASS)

Re-run:

```bash
python3 -m unittest discover -s lms -p 'test_artifact_load.py' -v
```

Writes this file and `lc-qa/artifact-load-latest.log`.

## What failed in class

On alc, opening an **Artifact for group work** returned Internal Server Error / server overloaded for the teacher and students. #115 and #116 kept idle `/state` at 200. They did not cover Artifact mint, active media, or Group Q preview fanout.

Production is gunicorn **1 worker, 2 threads** on a **shared-cpu-1x / 1gb** Fly machine (`fly.toml`, `lms/Dockerfile`). No Fly deploy in this change.

## Cause

`live_session_teacher_state_payload` called `_named_teams_for_live_session` → full `game_state` (roster, scores, career totals, moods) on **every** teacher-state read once groups existed.

One student `/api/student/state` after a Group Q mint did that about **25 times**. A class of 16, plus previews and the teacher mint, did it about **600 times** in one wave. That pegs the shared CPU on the 2-thread worker. An exception then renders Flask's default HTML 500, whose text is: "Internal Server Error … Either the server is overloaded or there is an error in the application." That is the page teacher and students saw. It is not the #115/#116 idle `/state` path.

Group Q team checks and the session timer used the same full rebuild.

## Fix

- Teacher-state only asks whether a named team exists, via one locked membership read (cached 0.5s for the poll wave).
- Group Q teammate checks use that index. They do not rebuild the game.
- The student SessionTimer reads the open game's clock columns.
- The student prompt builds the question list once per poll.
- Mint, active-media, and artifact-preview failures return JSON `503` `retry: true` instead of an HTML 500.

## This run

| | |
|---|---|
| Verdict | **PASS** |
| Class | 16 students + teacher |
| In-flight cap | 2 (gunicorn threads) |
| Wall | 229 ms |
| `game_state` calls | 35 (budget 80) |
| HTTP | media:200=16, mint:200=1, preview:200=16, staff-media:200=1, staff-state:200=1, state:200=16 |

| Path | n | med ms | p95 ms | max ms |
|---|---:|---:|---:|---:|
| media | 16 | 1 | 1 | 2 |
| mint | 1 | 43 | 43 | 43 |
| preview | 16 | 4 | 6 | 7 |
| staff-media | 1 | 3 | 3 | 3 |
| staff-state | 1 | 37 | 37 | 37 |
| state | 16 | 17 | 18 | 19 |

### Errors

- none

## Residual

- Fly machine size is unchanged: shared-cpu-1x, 1 GB, 2 threads. A different heavy path can still saturate that VM. This wave no longer rebuilds the game per teacher-state read.
- Staff heavy `/state` is still the #115 path (field isolation, 200). This test rides one heavy staff poll in the same wave and expects 200.
- The 0.5s membership cache can lag a team edit by one student poll. Artifact open does not edit teams.
- Not smoked on Fly. Re-run this test on tip `:8787` only if you want the same protocol against the dev server; the in-process bar above is the regression lock.
- The sqlite lock bar is `lc-qa/artifact-load-sqlite.md` (N=12 state + heartbeat).
