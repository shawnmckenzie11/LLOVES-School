# Copy-only fast lane

Use this lane when a pull request changes **only** user-visible wording in
**existing** files: template text, accessibility labels, copy JSON, or docs
prose. No Python, JavaScript, CSS, routing, structural HTML, or lane
infra changes.

## How to use it

1. Open a pull request with your copy edits (modify existing paths only).
2. Add the GitHub label **`copy-only`**.
3. Wait for [`.github/workflows/copy-only.yml`](../.github/workflows/copy-only.yml)
   **and** the normal [`ci.yml`](../.github/workflows/ci.yml) LMS suite before merge.

The copy-only job **always runs** on pull requests (it is never skipped). Without
the `copy-only` label it exits successfully and prints that the lane was not
used. **Do not add this job to required branch protection checks** — unlabeled
PRs would still pass it, but copy-only PRs must also pass the full LMS `ci.yml`
job. With the label it runs:

| Step | Script | What it proves |
|------|--------|----------------|
| Diff guard | [`scripts/copy_only_check.py`](../scripts/copy_only_check.py) from the **base branch** | Every change is limited to visible copy; PRs cannot weaken the checker |
| Template smoke | [`scripts/copy_only_template_smoke.py`](../scripts/copy_only_template_smoke.py) (PR code) | Changed staff/student templates still render (HTTP 200 via Flask test client) |

Run the guard locally (use the checker on `main`, not your branch):

```bash
git show origin/main:scripts/copy_only_check.py > /tmp/copy_only_check.py
COPY_ONLY_GIT_DIR=$PWD python3 /tmp/copy_only_check.py origin/main HEAD
python3 scripts/copy_only_template_smoke.py origin/main HEAD
```

## Allowed changes

- **Jinja / HTML** (`lms/templates/**`, other `.html`): plain text between
  tags; exact `alt`, `title`, `placeholder`, and `aria-label` values (no Jinja
  inside those values). Not inside `<script>` / `<style>` bodies.
- **Copy / i18n JSON** (existing files under `copy/` or `i18n/`, or
  `copy.json` / `strings.json` / `locale.json`): string **values** only.
- **Docs** (`docs/**/*.md`, existing files): markdown prose outside fenced code
  blocks (no raw HTML or `<script>` in prose).

## Rejected changes

The diff guard fails (with a path and reason) when a change touches:

- New, deleted, or renamed paths; chmod / mode changes; symlinks; binary blobs
- `scripts/copy_only_check.py`, `scripts/copy_only_template_smoke.py`, or
  `.github/workflows/copy-only.yml`
- Python, JavaScript, CSS, or other non-allowlisted files
- Jinja `{% %}` / `{{ }}` / `{# #}` (including inside copy attributes)
- `<script>` / `<style>` bodies, structural HTML (`class`, `id`, `href`, …),
  or non-exact attributes (`data-title`, `subtitle`, …)
- JSON keys or non-string values
- Bidi / zero-width unicode tricks; HTML tags in docs prose

If you need any of the above, drop the `copy-only` label and use the normal
**code** lane (full CI on the pull request).

## Branch protection

Keep **`ci.yml` / LMS test suite** required for all merges. **Do not** mark the
copy-only job as a required check: it is optional fast feedback when the label is
present. Label detection uses `PR_LABELS_JSON` in the environment (not shell
interpolation of label names).

## Linear / release notes

Copy-only PRs still need a Linear id (`MCK-###`) and an optional
`What's new:` line in the body — same as other staff-facing changes.
