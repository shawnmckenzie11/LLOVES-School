---
name: release-gate
description: >-
  Ship via PR → CI → merge main → approve production → watch Deploy and
  /health. Use when Shawn is ready to release code; never laptop fly
  deploy for routine release.
---

# Release gate

## When

Shawn says work is ready to ship, or asks to open/merge a PR that should go live.

## Path (routine)

1. Feature branch is green locally (see `.cursor/skills/local-verify`).
2. Open PR → wait for [`.github/workflows/ci.yml`](.github/workflows/ci.yml) (tests only).
3. Merge to **`main`** (only when Shawn asks to merge/ship).
4. A merge runs tests, then the Fly deploy waits in GitHub Actions for Shawn's approval on the `production` environment ([`.github/workflows/deploy.yml`](.github/workflows/deploy.yml)).
5. Confirm **https://alc.mckenzian.com/health**.

## Do not

- Run `flyctl deploy` / `fly deploy` from a laptop for routine release (Actions owns deploy on `main`).
- Push straight to `main` without a PR unless Shawn explicitly directs that.
- Commit secrets, `.imscc`, or `lms/data/`.
- Treat CI green on a feature branch as “live” — a merge to `main` runs tests, then the Fly deploy waits in GitHub Actions for Shawn's approval on the `production` environment.

## Secrets / volume

Production data lives on Fly volume `/data`. Ops lane changes there are not git commits; see `.cursor/rules/local-first-workflow.mdc`.
