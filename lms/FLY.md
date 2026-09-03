# Fly.io (LLOVES)

Canonical config is the repository-root [`fly.toml`](../fly.toml). Deploy from the **repo root** so Docker copies `courses/`, `frameworks/`, `tools/`, and `scripts/`. Public URL is **https://alc.mckenzian.com**. Full Cloudflare / GoDaddy / OAuth / empty-DB IT steps: [`DEPLOY.md`](DEPLOY.md).

```bash
cd /path/to/vlc-math-modernization
fly apps create lloves-lms --org personal   # once
fly volumes create lloves_data --region yyz --size 3 --app lloves-lms   # once
fly deploy --app lloves-lms
fly certs add alc.mckenzian.com --app lloves-lms
```

Google Cloud OAuth client (keep local URIs; add production):

- Authorized JavaScript origins: `http://127.0.0.1:8787` and `https://alc.mckenzian.com`
- Authorized redirect URIs: `http://127.0.0.1:8787/auth/google/callback` and `https://alc.mckenzian.com/auth/google/callback`

Volume `/data` is `LLOVES_DB=/data/lloves.sqlite` plus unpacked IMSCC. Do not deploy from `lms/` — the image would miss curriculum PDFs and semester.json. The Fly hostname `https://lloves-lms.fly.dev` is for health checks and DNS CNAME targets only; do not register it as the production Google redirect.
