# Copy-only fast lane

Use this lane when a pull request changes **only** user-visible wording: template
text, accessibility labels, copy JSON, or docs prose. No Python, JavaScript,
CSS, routing, or structural HTML.

## How to use it

1. Open a pull request with your copy edits.
2. Add the GitHub label **`copy-only`**.
3. CI runs [`.github/workflows/copy-only.yml`](../.github/workflows/copy-only.yml)
   instead of waiting on the full LMS suite for copy review.

The workflow runs two checks:

| Step | Script | What it proves |
|------|--------|----------------|
| Diff guard | [`scripts/copy_only_check.py`](../scripts/copy_only_check.py) | Every changed hunk is limited to visible copy |
| Template smoke | [`scripts/copy_only_template_smoke.py`](../scripts/copy_only_template_smoke.py) | Changed staff/student templates still render (HTTP 200 via Flask test client) |

Run the guard locally:

```bash
python scripts/copy_only_check.py origin/main HEAD
python scripts/copy_only_template_smoke.py origin/main HEAD
```

## Allowed changes

- **Jinja / HTML** (`lms/templates/**`): text between tags; `alt`, `title`,
  `placeholder`, and `aria-label` attribute values.
- **Copy / i18n JSON** (paths containing `copy/` or `i18n/`, or
  `copy.json` / `strings.json` / `locale.json`): string **values** only.
- **Docs** (`docs/**/*.md`): markdown prose outside fenced code blocks.

## Rejected changes

The diff guard fails (with a path and reason) when a hunk touches:

- Python, JavaScript, CSS, or other code files
- Jinja `{% %}` / `{{ }}` logic or expressions
- Structural HTML (`class`, `id`, `href`, new tags, and other attributes)
- JSON keys or non-string values
- Fenced code bodies in docs

If you need any of the above, drop the `copy-only` label and use the normal
**code** lane (full CI on the pull request).

## Linear / release notes

Copy-only PRs still need a Linear id (`MCK-###`) and an optional
`What's new:` line in the body — same as other staff-facing changes.
