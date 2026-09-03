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
