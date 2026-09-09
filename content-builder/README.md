# Math course content builder

Isolated lesson factory for ELC / LLOVES. **Not the school LMS.** Students receive static HTML with no AI calls, credentials, or required backend.

The **parent Cursor agent** (this chat’s main agent) is the production manager. It assigns specialists against shared contracts, resolves conflicts, maintains the catalogue, and is the only agent that integrates shared files. Copywriter owns wording; director owns coherence; engineer implements specs.

## Layers

| Layer | What it holds | Where |
|---|---|---|
| Source collection | Downloaded banks, curriculum extracts, applet references | `cache/` (gitignored); selected records in `catalogue/` |
| Instructional authoring | Briefs, questions, interactions, teaching copy, locks | `lessons/{CODE}/{lesson_id}/` in Git |
| Lesson production | Shared components, three-tab HTML, validation | `components/`, `build/`, scripts |

## Paths

```
content-builder/
  packages/           # verified course input snapshots (MCF3M-builder-input)
  catalogue/          # committed resource records + onboarding store + schema
    onboarding/       # identities, curriculum sources, mappings, rules, conflicts
  lessons/            # per-lesson JSON/Markdown (specialist outputs)
  components/        # reusable static assets (JSXGraph, CSS)
  build/              # compiled three-tab HTML
  cache/              # gitignored source clones and dataset caches
  index/              # rebuildable SQLite search index (not LMS sqlite)
  scripts/            # deterministic import / index / build / verify
```

Do not write builder artefacts into `lms/`, `tools/math-game-show/`, Fly `/data`, or `.imscc` packs. Do not use the LMS database as the catalogue index.

## Pipeline (parent)

1. Audit (when revising) and contracts in `catalogue/contracts/`
2. `lesson-director` → `lesson-brief.json` (process cognitive targets)
3. When `catalogue/banks/` exists: `bank-curator` (licence, Bloom, dispositions)
4. **Parallel:** `practice-designer` (Bloom + FAME), `hook-curator`, `visual-experience-designer`, writing-model comparison
5. `interaction-designer` and `formative-feedback-designer` (mistakes + explanation prompts)
6. `student-copywriter` → `student-content.json` (renderer whitelist)
7. `lesson-engineer` (isolated worktree if it touches shared components)
8. `lesson-verifier` (independent; language, flow, Bloom/FAME, visual, feedback cases); module-level flow verify before course-wide regen
9. Parent integrates; preserve locked/approved sections

Pedagogy: `docs/pedagogy-specialist-enhancement.md`.

Revision plan: `content-builder/docs/math-content-builder-revision-plan.md`. v1 fixture: `content-builder/fixtures/M4-L1-vertex-form-v1/`. MCF3M onboarding (39 identities, curriculum, rules) lives in `packages/MCF3M-builder-input/` and `catalogue/onboarding/MCF3M/`. Finish onboarding before further revision-plan prose rewrites.

## Sources (start small)

Authoritative Ontario wording: `lms/seeds/` and Ministry PDFs — never invent statements. OpenStax, WeBWorK, MathNet, GeoGebra, Desmos public embeds, and locally bundled JSXGraph are described in `.cursor/agents/` and the content-builder rules. Defer WeBWorK conversion until the pipeline works. Exclude Gizmos from required activities.

## Local toolchain

Use the isolated venv. Do not install these into the LMS `.venv`.

```bash
python3 -m venv content-builder/.venv
content-builder/.venv/bin/python -m pip install -r content-builder/requirements.txt
content-builder/.venv/bin/playwright install chromium
cd content-builder && npm install
PLAYWRIGHT_BROWSERS_PATH="$HOME/Library/Caches/ms-playwright" \
  content-builder/.venv/bin/python content-builder/scripts/check_setup.py
```

Playwright MCP for this repo is `.cursor/mcp.json` (`npx @playwright/mcp`). After that file changes, enable **playwright** in Cursor Settings → MCP (or Customize) and restart Cursor if the tools do not appear. Individual MCP calls still need approval.

## First lesson (MCF3M M4-L1)

```bash
# Import / index (from repo root; isolated venv)
content-builder/.venv/bin/python content-builder/scripts/import_ontario_seed.py
content-builder/.venv/bin/python content-builder/scripts/import_openstax.py
content-builder/.venv/bin/python content-builder/scripts/import_mathnet.py
content-builder/.venv/bin/python content-builder/scripts/catalogue.py

# MCF3M onboarding (verified Drive identities + curriculum + rules)
content-builder/.venv/bin/python content-builder/packages/MCF3M-builder-input/validate_package.py
content-builder/.venv/bin/python content-builder/scripts/import_mcf3m_package.py --preview
content-builder/.venv/bin/python content-builder/scripts/import_mcf3m_package.py --apply
content-builder/.venv/bin/python content-builder/scripts/check_onboarding.py
content-builder/.venv/bin/python content-builder/scripts/resolve_lesson_context.py --role lesson-director --write

# Compile + check the static three-tab page (not the LMS on :8787)
content-builder/.venv/bin/python content-builder/scripts/build_lesson.py
content-builder/.venv/bin/python content-builder/scripts/check_feedback.py
PLAYWRIGHT_BROWSERS_PATH="$HOME/Library/Caches/ms-playwright" \
  content-builder/.venv/bin/python content-builder/scripts/verify_lesson.py

# Teacher review (document root = content-builder/)
content-builder/.venv/bin/python content-builder/scripts/serve_review.py
# http://127.0.0.1:8790/review/
# compiled export: http://127.0.0.1:8790/build/MCF3M/M4-L1-vertex-form/index.html
```

Search: `content-builder/.venv/bin/python content-builder/scripts/search_resources.py vertex`.

