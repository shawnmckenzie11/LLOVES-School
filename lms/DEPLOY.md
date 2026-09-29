# Deploy LLOVES to alc.mckenzian.com (Fly.io + Cloudflare)

Same pattern as Happy Hour (`hh.mckenzian.com`) and IAW (`iaw.mckenzian.com`): Fly runs the app; Cloudflare DNS points the subdomain; GoDaddy stays the registrar only.

| Piece | Where |
|-------|--------|
| LMS | Fly app `lloves-lms`, region `yyz`, port 8080 |
| SQLite + unpacked IMSCC | Volume `lloves_data` at `/data` |
| DNS | Cloudflare — CNAME `alc` → `lloves-lms.fly.dev` |
| TLS | Fly-managed certificate for `alc.mckenzian.com` |
| Public URL | **https://alc.mckenzian.com** |

Marketing at `mckenzian.com` is unchanged.

## Cloudflare (you must add this record)

In **Cloudflare → mckenzian.com → DNS**, add:

| Type | Name | Target | Proxy |
|------|------|--------|-------|
| CNAME | `alc` | `lloves-lms.fly.dev` | **DNS only** (grey cloud) |

Grey cloud avoids double-proxy TLS between Cloudflare and Fly (same as `hh` and `iaw`).

## GoDaddy

**Nothing**, if `mckenzian.com` nameservers already point at Cloudflare (they do for `hh` / `iaw` / `paperscraper`). Do not add a second A/CNAME for `alc` in GoDaddy — that fights Cloudflare.

Only touch GoDaddy if nameservers are still GoDaddy’s. Then either switch nameservers to Cloudflare (preferred, matches the other apps) or add the CNAME there instead.

## Google Cloud OAuth (you must add these URIs)

APIs & Services → Credentials → the **Web application** client used by LLOVES.

**Authorized JavaScript origins**

- `http://127.0.0.1:8787` (keep)
- `https://alc.mckenzian.com`

**Authorized redirect URIs**

- `http://127.0.0.1:8787/auth/google/callback` (keep)
- `https://alc.mckenzian.com/auth/google/callback`

Consent screen → **Test users** (while the app is in Testing): add `rspercival10@gmail.com` (and any other staff Gmail). Otherwise Google blocks sign-in before LLOVES sees them.

Do not turn the consent screen **Internal**.

## After DNS + OAuth

Production sqlite starts empty. Log in as IT (`solutions@mckenzian.com`) at https://alc.mckenzian.com → activate 2026–2027 S1 → register `rspercival10@gmail.com` → assign MCF3M.

First Google login emails a 6-digit code. **Production requires that code on every staff/IT sign-in** (`FLASK_ENV=production`). Production never shows the code on the verify page. Set Resend or SMTP **secrets** before anyone needs login:

```bash
# Resend (preferred) — paste the key at the prompt; do not echo it into shell history if you can avoid it.
fly secrets set RESEND_API_KEY='re_…' EMAIL_FROM='LLOVES <noreply@mckenzian.com>' --app lloves-lms

# Or SMTP instead of / in addition to Resend
fly secrets set SMTP_SERVER='smtp.example.com' SMTP_PORT='587' \
  SMTP_USERNAME='…' SMTP_PASSWORD='…' --app lloves-lms
```

`ALLOW_DEV_VERIFICATION_CODE` is not set on Fly. Local `.env` may keep it for when email is not configured.

## Deploy commands (from repo root)

```bash
fly apps create lloves-lms --org personal   # once
fly volumes create lloves_data --region yyz --size 15 --app lloves-lms   # once
fly secrets set FLASK_SECRET_KEY="$(openssl rand -hex 32)" \
  GOOGLE_CLIENT_ID='...' GOOGLE_CLIENT_SECRET='...' --app lloves-lms
fly deploy --app lloves-lms
fly certs add alc.mckenzian.com --app lloves-lms
```

Verify:

```bash
curl -s https://alc.mckenzian.com/health
fly certs check alc.mckenzian.com --app lloves-lms
```

## Live presence Postgres (heartbeat / Artifact polls)

Student `/api/student/state` and `/api/student/heartbeat` write attendee presence on every poll. That write used to take a lock on `/data/lloves.sqlite` and returned `database is locked` (HTML “server overloaded”) once a class was in the mid-20s with an Artifact open.

Those writes use **Postgres** when either secret is a postgres URL:

| Secret | When |
|--------|------|
| `LIVE_DATABASE_URL` | Preferred. Only the live poll store reads it. |
| `DATABASE_URL` | Used when it starts with `postgres://` or `postgresql://`. This is what `fly mpg attach` sets. |

Sqlite on `lloves_data` stays the catalogue (users, rosters, packs, prompts, grades). `/health` reports `live_presence`: `postgres`, `postgres-down`, or `sqlite`.

**alc:** `fly.toml` does not set either secret. Until one is on the machine, the running image keeps the sqlite hot path (WAL + autocommit).

Attach with Actions → **Attach live Postgres** (`.github/workflows/attach-live-postgres.yml`). Merge does not run that job. The job creates `lloves-live` (Basic, `yyz`) when missing, `fly mpg attach` sets `DATABASE_URL`, then a restart-only secret deploy if `/health` is still not `postgres`. Basic is paid. If the token cannot authorize the charge, the job prints this and stops:

```bash
fly mpg create --name lloves-live --org <org> --region yyz --plan basic --pg-major-version 16 --volume-size 10
fly mpg attach <cluster-id> --app lloves-lms
curl -s https://alc.mckenzian.com/health
# "live_presence": "postgres"
```

Startup copies **active** sqlite sessions into Postgres once. Heartbeats after that do not `UPDATE live_session_attendees`. Details: `lms/LIVE_PRESENCE.md`.

## Large module-pack uploads (any size the volume can hold)

LLOVES does **not** enforce an app-level byte ceiling on Admin/IT `.imscc` uploads.
Fly Proxy streams request bodies and does **not** advertise a hard body-size limit
that requires a paid upgrade to raise. Config already set (no machine upsell):

| Layer | Setting | Notes |
|-------|---------|--------|
| Flask / Werkzeug | `MAX_CONTENT_LENGTH` / `IMSCC_MAX_BYTES` | **`None`** (unlimited) in `lms/modules.py` |
| gunicorn | **4 workers, 8 threads, `--timeout 120`**, keepalive 1s, `worker_connections` 32 per worker, **backlog 64** | Same line in `lms/Dockerfile` and `fly.toml` `[processes]`. A silent worker is killed after 120s. Poll slices stop at 8s. Boot `/state` sheds for 8s. |
| Fly `http_service.http_options.idle_timeout` | **600s** | Free config; quiet periods while the body is received / unpack runs |
| Fly Proxy body size | streaming | No documented hard cap; >10 MB skips replay buffering (latency quirk only, not a reject) |
| Fly volume `lloves_data` | currently **15 GB** | **Real hard limit** for `.imscc` + unpacked tree. Extending volume **costs money** — only if disk-full errors appear |

**Zero-cost vs paid:** Raising Flask/gunicorn/idle_timeout is free. Extending the
volume or buying a larger VM is **not** free — do not extend unless `/data` is full.
Stay on **one** shared-cpu-1x / 1 GB machine. Do not add a second Fly machine.
Inside that machine the image command is 4 gunicorn workers × 8 threads and
timeout 120, with keepalive 1s, `worker_connections` 32 per worker, and
listen backlog 64. Live `/state` sends `Connection: close`. Fly proxy
`idle_timeout` stays 600 for a quiet upload body. The gunicorn worker
timeout is 120.

## When `/health` wedges under live polls

On 2026-09-28 the live machine command was already
`gunicorn … --workers 4 --threads 8 --timeout 120`. A plain
`fly machines restart` wedged `/health` again (curl exit 28, about 90s).
Re-applying that same command with `fly machines update` (fresh launch)
restored `/health` 200.

Restart binds `:8080` before workers finish `create_app()`. Poll clients
re-attach into that listen queue and the workers then spend their threads
on `/state`. A fresh launch or an image deploy cuts traffic over after
the process is serving.

Recovery under live poll load:

- Image deploy (a merge to `main` runs tests, then the Fly deploy waits for Shawn's approval on the `production` environment), or `fly machines update` on the one machine.
- Do not use `fly machines restart` while student and staff polls are attached.

The image also refuses connections past backlog 64 (gunicorn's default
queue is 2048) and, for 8 seconds after each worker loads, answers
`/state` with retry JSON. `/health` is served during that window. That
does not make a soft restart the recovery path. The listen socket is
still open while `create_app()` runs.

**Cloudflare:** keep the `alc` CNAME **DNS only** (grey cloud). Orange-cloud proxying
often rejects or truncates very large request bodies.

If something still returns HTTP 413, it is not an LLOVES size cap — check Cloudflare
proxy mode, idle timeout on a stalled upload, or volume free space.

## Sentry

Org `mckenzian`. Projects: `lloves-lms` (Flask) and `lloves-live` (browser). DSNs stay in the environment. The repo has no DSN strings.

| Variable | Role |
|----------|------|
| `SENTRY_DSN` | Flask / gunicorn. Leave unset and the process boots with no Sentry client. |
| `SENTRY_DSN_LIVE` | Public browser key for Run Live Class and the student portal. The server injects it as a meta tag. Leave unset and those pages do not load the browser SDK. |
| `GH_SHA` | Release tag. `.github/workflows/deploy.yml` passes the commit as Docker `--build-arg GH_SHA`. |
| `FLASK_ENV` | Environment tag. Fly sets `production`. Unset local runs tag `development`. |
| `SENTRY_ENVIRONMENT` | Optional override. Use it for a tip smoke so you do not have to set `FLASK_ENV=production` (that flag refuses `LOCAL_DEV_LOGIN`). |
| `SENTRY_RELEASE` | Optional override when `GH_SHA` is empty. |

Fly secrets, after Shawn's GO (this tip does not deploy):

```bash
fly secrets set SENTRY_DSN='…' SENTRY_DSN_LIVE='…' --app lloves-lms
```

Paste each DSN at the prompt. The next approved deploy from `main` ships the SDK (tests run, then the Fly deploy waits for Shawn's approval on the `production` environment). Events appear once those secrets exist on the machine.

### Tip smoke on :8787

```bash
read -r SENTRY_DSN
read -r SENTRY_DSN_LIVE
export SENTRY_DSN SENTRY_DSN_LIVE
export SENTRY_ENVIRONMENT=tip
export GH_SHA="$(git rev-parse HEAD)"
export LOCAL_DEV_LOGIN=1
python3 lms/app.py
```

Flask event, same environment, second shell:

```bash
python3 -c "
import sys
sys.path.insert(0, 'lms')
from sentry_wire import init_flask_sentry
import sentry_sdk
assert init_flask_sentry(), 'SENTRY_DSN missing'
try:
    raise RuntimeError('lloves-lms tip smoke MCK-8')
except RuntimeError:
    sentry_sdk.capture_exception()
sentry_sdk.flush(timeout=10)
print('flask event flushed')
"
```

Browser: sign in with the offline picker, open Run Live Class or the student home, then in the console:

```js
throw new Error('lloves-live tip smoke MCK-8')
```

Confirm `lloves-lms` and `lloves-live` show those messages, environment `tip`, and release equal to `GH_SHA` when it is set.

Busy, 503, reconnect, and heartbeat failures stay on the soft strip. They are not fatal Sentry events. A script exception while the page is up still reports. Traces sample at 5%. `/health`, `/api/student/state`, `/api/student/heartbeat`, and `/api/live-sessions/<id>/state` are not sampled. Request bodies and local variables are not sent.
