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

First Google login emails a 6-digit LLOVES code to that Google account (later logins skip 2SV). Production never shows the code on the verify page (`FLASK_ENV=production`). Set Resend or SMTP **secrets** before anyone needs first login:

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
fly volumes create lloves_data --region yyz --size 3 --app lloves-lms   # once
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

## Large module-pack uploads (~650MB+)

App caps are raised so Admin can attach large Common Cartridges:

| Layer | Setting | Notes |
|-------|---------|--------|
| Flask / Werkzeug | `MAX_CONTENT_LENGTH` / `IMSCC_MAX_BYTES` | **800 MB** in `lms/modules.py` |
| gunicorn | `--timeout 600` | Unpack after upload can exceed 5 minutes |
| Fly `http_service.http_options.idle_timeout` | **600s** | Quiet periods while the body is received / unpack runs |
| Fly volume `lloves_data` | currently **3 GB** | A 639 MB `.imscc` plus unpacked tree may need a larger volume (`fly volumes extend`) |

**Cloudflare:** keep the `alc` CNAME **DNS only** (grey cloud). Orange-cloud proxying often rejects or truncates very large request bodies; if uploads still fail under ~800 MB with a proxy error, confirm the record is grey-clouded.

If Flask returns HTTP 413, the message names the app max and points here for edge/volume caps.
