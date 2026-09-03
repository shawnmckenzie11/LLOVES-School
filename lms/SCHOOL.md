# Learning Live Online Virtually & Explicitly School (LLOVES)

This folder is a **new school product**, not a rename of ELC. ELC identity stays in `frameworks/school.md`.

Shawn is IT at `solutions@mckenzian.com`.

## Run locally

```bash
python3 -m pip install -r lms/requirements.txt
python3 lms/app.py
# http://127.0.0.1:8787
```

Staff/IT use **real Google OAuth**. Mock email login is tests-only. Follow [GOOGLE.md](GOOGLE.md) (same Web client + External consent screen as the Cannabis Paper Scraper).

## Environment

Copy `.env.example` to `.env` and paste the Cloud Console client id/secret. Redirect URIs:

- `http://127.0.0.1:8787/auth/google/callback`
- `https://<fly-app>.fly.dev/auth/google/callback`

Consent screen: External, personal Google accounts (no Workspace domain restriction). App-side allowlist: IT registers staff emails first.

Deploy Fly from the **repository root** (`fly.toml`) so the image includes `frameworks/`, `lms/`, and `tools/`. Volume `/data` holds sqlite and Admin-uploaded IMSCC libraries (no git template packs).

## Tests

```bash
python3 -m unittest lms.test_auth lms.test_it lms.test_roster
python3 tools/math-game-show/test_app.py
```
