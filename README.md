# LLOVES School

**Learning Live Online Virtually & Explicitly School** — Admin / Staff / Student LMS for ELC (Ontario online).

- **Live:** https://alc.mckenzian.com (`lloves-lms` on Fly.io)
- **Local:** `python3 -m pip install -r lms/requirements.txt` then `python3 lms/app.py` → http://127.0.0.1:8787

## Quick start for agents / humans

1. Read [`AGENTS.md`](AGENTS.md)
2. Check [`frameworks/semester.json`](frameworks/semester.json) for phase and key dates
3. Curriculum seeds: `lms/seeds/`; Ministry PDFs: `lms/sources/ontario-curriculum/`
4. Module packs: Admin upload only (nothing under `courses/`, no IMSCC in git)

## Layout

| Path | Purpose |
|------|---------|
| `frameworks/` | Shared ELC school, class, semester guidance |
| `lms/` | Flask LMS + seeds + Ontario curriculum PDFs |
| `tools/math-game-show/` | Live class game show |
| `agents/` | School-facing agent workflows |
| `scripts/` | Syllabus calendar + IMSCC unpack/inventory helpers |

## Tests

```bash
python3 -m pip install -r lms/requirements.txt
ALLOW_DEV_VERIFICATION_CODE=1 python3 -m unittest discover -s lms -p 'test_*.py' -v
```
