# LLOVES LMS

Learning Live Online Virtually & Explicitly School. This app is **not** a rename of ELC; it mounts existing MCF3M tools (IMSCC modules, click-to-place syllabus, Math Game Show grades).

## Run locally

```bash
python3 -m pip install -r lms/requirements.txt
python3 lms/app.py
```

Landing page: **http://127.0.0.1:8787/**

IT can assign **Mathematics**, **Grade 11–12 Science**, and **Health and Physical Education** (plus MCF3M expectations). Other subjects are not in the catalog yet. Science/HPE PDFs: `python lms/fetch_ontario_curriculum_pdfs.py`.

Staff/IT: real Google OAuth (see [GOOGLE.md](GOOGLE.md)). First LLOVES login emails a 6-digit code (see [DEPLOY.md](DEPLOY.md) for Resend/SMTP). The verify page shows the code only in local/dev when `ALLOW_DEV_VERIFICATION_CODE=1` and email is not configured — never when `FLASK_ENV=production`. Mock Google is tests-only.

## Local test server (:8788)

The Mac LC Teacher and Student View bots sign in here. This is not the Google sign-in path, and an unsigned Google credential is still rejected.

```bash
scripts/run_local_test_server.sh
```

The script listens on `http://127.0.0.1:8788`, sets `LLOVES_TEST_LOGIN=1`, and sets `HOST=127.0.0.1`. It passes that same value to `--host`. The process refuses to boot if the flag is combined with `FLY_APP_NAME`, `FLASK_ENV=production`, or a `HOST` that is not explicitly `localhost` or `127.0.0.1`. An unset `HOST` does not count: `gunicorn --bind` and `flask run --host` do not read it. A request whose host is not one of those two gets 404. Leave the flag unset for the normal `:8787` dev server and on Fly.

Never put a tunnel or proxy (ngrok, ssh -R, cloudflared) in front of :8788 while the flag is on.

Bots open a staff session with POST (no Google credential). GET returns 405. Omit Origin and Referer, or send the same loopback host:

```bash
curl -c /tmp/lloves-test.cj -b /tmp/lloves-test.cj -L \
  -X POST http://127.0.0.1:8788/auth/test-staff
```

That signs in the fixed staff account `lloves-test-staff@localhost`. The account is created the first time the route runs. It does not take over classes that already belong to another teacher. `?next=/staff/...` lands on a staff page after sign-in.

Bootstrap IT account: `solutions@mckenzian.com`

## Google OAuth (production)

Set in `lms/.env`:

- `FLASK_SECRET_KEY`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI` — `http://127.0.0.1:8787/auth/google/callback` locally, plus the Fly.io HTTPS callback
- `IT_EMAIL` / `IT_EMAILS` (optional extras)

Authorized JavaScript origins and redirect URIs must include both localhost and the hosted hostname.

## Fly.io

Deploy from the **repository root** (`fly.toml` there) so Docker copies `courses/` and `frameworks/`. See `lms/FLY.md`. Needs a persistent volume at `/data` for sqlite + unpacked IMSCC.

## Tests

```bash
python3 -m unittest lms.test_auth lms.test_it lms.test_roster lms.test_module_pack
python3 tools/math-game-show/test_app.py
```
