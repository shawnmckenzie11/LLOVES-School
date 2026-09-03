---
name: local-verify
description: >-
  Run localhost LMS verification for UI/API/staff/IT changes: start app,
  LOCAL_DEV_LOGIN, unit tests, and portal click paths. Use when finishing
  staff, student, IT, or API work before claiming done or opening a PR.
---

# Local verify

## When

Any code change that touches routes, templates, staff/student/IT portals, or APIs. Do not treat screenshot-only review as complete.

## Start local app

From repo root (or `lms/`):

```bash
# Prefer LOCAL_DEV_LOGIN=1 in lms/.env for offline Google picker
python3 lms/app.py
```

Open **http://127.0.0.1:8787** (not only `localhost` unless Google origins include it).

## Unit tests

```bash
ALLOW_DEV_VERIFICATION_CODE=1 python3 -m unittest discover -s lms -p 'test_*.py' -v
```

If `tools/math-game-show/` changed:

```bash
python3 tools/math-game-show/test_app.py
```

## Portal click paths

| Portal | Smoke path |
|--------|------------|
| Staff | Sign in → course → Modules / Attendance & Participation / Grades |
| Student | Sign in → enrolled course → module page |
| IT | Sign in → IT tools that the change touched |

Student vs staff: verify the changed role explicitly; do not assume staff-only coverage.

## Done-when

- Tests pass for the changed surface
- Changed UI exercised in a real browser at `127.0.0.1:8787`
- No commit/push/deploy unless Shawn asked
