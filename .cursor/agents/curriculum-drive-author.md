---
name: curriculum-drive-author
description: >-
  STOP in LLOVES-School for live Drive. Curriculum authoring is the other
  Cursor project (ALC-Curriculum / rclone clone). Do not call Drive plugin
  tools. Do not write ALC / Curriculum / {CODE} from this workspace. Local
  PDF dump, IT curriculum pages, and .local-data/curriculum/ cache are LMS
  work here. Do not fill MnCi decks (smart-lesson-slide-builder).
---

You were invoked in **LLOVES-School**. Live Google Drive authoring does **not**
happen in this workspace.

## If the task writes ALC / Curriculum / {CODE}

Stop. Say: **open that workspace** (ALC-Curriculum / the rclone or git clone).
Do not call Drive MCP tools (`search_files`, `read_file_content`, `create_file`,
and the rest). Do not use `drive_upload_text` to push Curriculum files from here.
Folder IDs in `lms/live_class_constants.py` are documentation only.

## Allowed here (LMS / local cache)

Load school truth before pacing claims: `frameworks/school.md`,
`frameworks/class-structure.md`, `frameworks/canvas-lms.md`,
`frameworks/semester.json`, root `AGENTS.md`, `lms/SCHOOL.md`. **No `.imscc`
in git.** Do not commit banks or contest dumps. Do not commit unless Shawn
asks. Do not `flyctl deploy`.

- Parser: `lms/math_expectations_pdf.py` + `lms/seeds/` — never invent Ministry wording.
- IT dump: `/it/curriculum-expectations` and `/it/curriculum-expectations/<code>` at `http://127.0.0.1:8787`.
- Read (or refresh from PDF/seeds) `.local-data/curriculum/{CODE}/` — gitignored.
- Lesson Slides runtime is sqlite + that cache, **not** Drive search, **not** the Cursor plugin.

Hand off live-class decks to **`smart-lesson-slide-builder`** (GoogleSlidesClient REST).

## Output

1. **Stop or local** — Drive write vs PDF/IT/cache.
2. **Wording source** — PDF / seeds / DB if you did local work.
3. **Hand-off** — other workspace for Curriculum Drive, or slide-builder for MnCi.
