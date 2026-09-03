# Google OAuth for LLOVES

Staff and IT sign in with a **personal Google account**, the same way the Cannabis Paper Scraper does: Google Cloud **OAuth 2.0 Web client** + Flask code exchange at `/auth/google/callback`. LLOVES does **not** auto-create accounts. IT allowlists staff emails first.

This is **Google Cloud Console** work (APIs & Services), not Google Workspace Admin “SSO / SAML”. Workspace can own the Cloud project; teachers still use `@gmail.com` (or any Google login) unless you switch the consent screen to Internal.

## 1. Open the same Cloud project as the scraper

1. Sign in at [Google Cloud Console](https://console.cloud.google.com/) with the McKenzian Google account that already has the Cannabis Paper Scraper OAuth client.
2. Select that **project** (or create `lloves-lms` if you want a separate client).
3. APIs & Services → **OAuth consent screen** (Branding / Audience in the new UI).

## 2. OAuth consent screen

| Setting | Value |
| --- | --- |
| User type | **External** (Internal would only allow your Workspace domain; LLOVES staff use personal Gmail) |
| App name | Learning Live Online Virtually & Explicitly School |
| User support email | `solutions@mckenzian.com` |
| App logo | optional |
| App domain / home page | `http://127.0.0.1:8787` for local; `https://alc.mckenzian.com` in production |
| Developer contact | `solutions@mckenzian.com` |
| Scopes | `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile` (non-sensitive) |
| Publishing status | **Testing** until you are ready to publish |

While **Testing**:

- Add **Test users**: `solutions@mckenzian.com`, `rspercival10@gmail.com`, and every personal Gmail that should be able to click Sign in with Google.
- Anyone not on that list sees Google’s “app hasn’t been verified” / access denied screen. That is Google, not LLOVES.

Do **not** turn on “Google Workspace domain only” or pass `hd=` — that would block personal Gmail.

## 3. Create the OAuth client (Web application)

APIs & Services → Credentials → **Create credentials** → **OAuth client ID**.

- Application type: **Web application** (same as the scraper; not Desktop, not iOS).
- Name: `LLOVES LMS local` (or add URIs to the existing scraper Web client).

**Authorized JavaScript origins** (no path):

- `http://127.0.0.1:8787`
- `http://localhost:8787` (only if you will open that host)
- `https://alc.mckenzian.com`

**Authorized redirect URIs** (exact match, including `/auth/google/callback`):

- `http://127.0.0.1:8787/auth/google/callback`
- `http://localhost:8787/auth/google/callback` (if you added that origin)
- `https://alc.mckenzian.com/auth/google/callback`

Google treats `localhost` and `127.0.0.1` as different. This repo’s default for local is **http://127.0.0.1:8787**. Production is **https://alc.mckenzian.com** (not a `.fly.dev` placeholder).

If you **reuse** the Cannabis Paper Scraper Web client, add the LLOVES origins and redirect URIs to that client. Do not change the scraper’s existing URIs.

## 4. Put the secrets on this machine

```bash
cp lms/.env.example lms/.env
```

Paste the client ID and secret (the secret is shown once at creation; you can reset it). Set:

```
FLASK_SECRET_KEY=<long random string>
GOOGLE_CLIENT_ID=<....apps.googleusercontent.com>
GOOGLE_CLIENT_SECRET=<from the Cloud Console>
GOOGLE_REDIRECT_URI=http://127.0.0.1:8787/auth/google/callback
IT_EMAILS=solutions@mckenzian.com
```

`lms/.env` is gitignored. On Fly, set the same names as secrets (`fly secrets set GOOGLE_CLIENT_ID=...`). Production `GOOGLE_REDIRECT_URI` is already `https://alc.mckenzian.com/auth/google/callback` in [`fly.toml`](../fly.toml).

Restart the LMS after editing `.env`:

```bash
# stop the old process on 8787, then:
lms/.venv/bin/python lms/app.py
```

Open **http://127.0.0.1:8787** → IT Login. You should go to `accounts.google.com`, not “Simulated Google Sign-In”.

## 5. First login on LLOVES (after Google)

1. Google account must match an allowlisted user (`solutions@mckenzian.com` is seeded as IT).
2. First LLOVES login emails a 6-digit code to that Google account (later Google logins skip that code). Production never displays the code on the verify page. Configure `RESEND_API_KEY` + `EMAIL_FROM`, or SMTP, in `.env` locally and as Fly secrets in production. `ALLOW_DEV_VERIFICATION_CODE=1` may show the code locally only when email is not configured.
3. Unknown Google accounts get **403** and are not created.

## 6. Common Google errors

| Google message | Fix |
| --- | --- |
| `redirect_uri_mismatch` | URI in Console must equal `GOOGLE_REDIRECT_URI` and the URL in the address bar (127.0.0.1 vs localhost). |
| `origin_mismatch` / One Tap silent fail | Add the exact origin (`http://127.0.0.1:8787`) under JavaScript origins. |
| Access blocked: app in testing | Add the Google account under Consent screen → Test users. |
| 403 “not registered” on LLOVES after Google | IT must register that Gmail on `/it` first (except the bootstrap IT email). |
| Consent screen Internal | Switch to External or you cannot use personal Gmail. |

The mock email form is **only** used when Flask `TESTING=1` (unit tests). If you still see “Simulated Google Sign-In” in the browser, `.env` is missing, empty, or the server was started before you saved it.
