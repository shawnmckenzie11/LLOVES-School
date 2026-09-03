---
name: release-gate
description: >-
  Ship via PR → CI → merge main → watch Deploy and /health. Use when Shawn
  is ready to release code; never laptop fly deploy for routine release.
---

# Release gate

## When

Shawn says work is ready to ship, or asks to open/merge a PR that should go live.

## Path (routine)

1. Feature branch is green locally (see `.cursor/skills/local-verify`).
2. Open PR → wait for [`.github/workflows/ci.yml`](.github/workflows/ci.yml) (tests only).
3. Merge to **`main`** (only when Shawn asks to merge/ship).
4. Watch [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) Deploy job.
5. Confirm **https://alc.mckenzian.com/health**.

## Do not

- Run `flyctl deploy` / `fly deploy` from a laptop for routine release (Actions owns deploy on `main`).
- Push straight to `main` without a PR unless Shawn explicitly directs that.
- Commit secrets, `.imscc`, or `lms/data/`.
- Treat CI green on a feature branch as “live” — only merge to `main` deploys.

## Secrets / volume

Production data lives on Fly volume `/data`. Ops lane changes there are not git commits; see `.cursor/rules/local-first-workflow.mdc`.
