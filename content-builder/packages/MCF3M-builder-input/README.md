# MCF3M builder input package

Prepared 9 September 2026 for adding to Cursor. This package contains 39 existing lesson folder identities across eight modules, 53 specific curriculum expectations from Drive, nine overall expectations from the Ministry PDF, your instructional rules, source snapshots and a proposed lesson-to-expectation map.

## Your steps

1. Unzip this package and place `MCF3M-builder-input/` inside your Cursor project, preferably under its existing content-input directory. It does not overwrite root rules or contain a new application.
2. Open `CURSOR-HANDOFF.md` and give Cursor its contents, with this folder attached as context.
3. Have Cursor import and validate the data, then review its mapping/conflict report before generating a lesson batch.

## Files

| File | Purpose |
| --- | --- |
| `CURSOR-HANDOFF.md` | Ordered instructions for importing into the existing builder. |
| `LESSON-INVENTORY.md` | Human-readable table of all 39 folder IDs and linked Drive titles. |
| `data/course.json` | Course, eight module topic names, actual module IDs and hierarchy. |
| `data/lessons.json` | All 39 current lesson folder IDs, titles, URLs, parent IDs and proposed curriculum references. |
| `data/curriculum.json` | 53 specific expectations, source sample problems and nine overall expectations. |
| `data/curriculum-processes.json` | Seven mathematical process expectations from the official course page. |
| `rules/instructional-rules.md` | Consolidated teaching, student-language, practice, media, solution and live-slide rules. |
| `data/reconciliation.json` | Title differences and superseded assumptions. |
| `data/existing-teaching-files.json` | Observed Drive metadata for existing MCF3M teaching files. |
| `data/resources.json` | Empty approved-resource registry, explicitly marked unresearched. |
| `data/sources.json` | Source IDs, URLs, provenance and attribution. |
| `sources/` | Preserved Drive extracts and prior rule/lesson-list artifacts. |
| `validate_package.py` | Offline integrity and cross-reference checks; no external dependencies. |
| `VALIDATION.json` | Results from validating this package. |
| `SHA256SUMS.json` | Checksums of package contents, excluding this checksum file. |

## What changed since the earlier plan

All seven Module 1 lesson folders now exist. The current total is 39, superseding the earlier 32-folder count. The module still uses the Drive name `Module 1 - temp`, and its lessons sit inside `Async Lessons`. The original JSON supplies its topic title, `Introduction to the Quadratic Function`.

Three current folder titles differ from the original lesson list: M2L1, M2L2 and the missing space in the original M4L2 label. Both versions are retained, with current Drive titles selected for import. Module 7 has unnumbered folder titles, so its sequence follows the original JSON. No folders were created or changed.

## Curriculum evidence and limits

The specific expectation wording is copied from [the existing MCF3M Drive extract](https://docs.google.com/document/d/197toOKG8z2CqdaDtuYTD0Z1JaMXhKG8JVmrMo30gsvM/edit). Its superscript conventions, examples and sample problems are preserved. The package checks equality against that transcription; it does not claim a new full glyph-by-glyph audit of it against the Ministry PDF.

The overall expectations were absent from that extract. They come from [The Ontario Curriculum, Grades 11 and 12: Mathematics, 2007](https://www.edu.gov.on.ca/eng/curriculum/secondary/math1112currb.pdf), printed pages 59, 62 and 65, corresponding to PDF pages 61, 64 and 67. The seven process expectations come from printed page 58, PDF page 60. Words and punctuation are retained while extraction line wraps are removed. Credit: Ontario Ministry of Education, © Queen’s Printer for Ontario, 2007. Crown copyright acknowledged; reproduced for personal/non-commercial educational preparation under [Ontario's reproduction terms](https://www.ontario.ca/page/copyright-information).

Lesson mappings are proposed instructional judgments, not mappings found in Drive or Ministry claims. Specific attention is needed for the partial B1.6 comparison in M1L2, TVM work under B3.4, and actual data-collection/application tasks under B2 and C3. Code inclusion is not evidence of complete instruction or assessment.

This is an input package, not 39 finished lessons. Resource research and generated lesson approval are separate. The full historical plans are included for context, but their outdated counts, example course typo, model-selection proposals and dependency statuses must not become current operational facts.

## Validate locally

From this directory:

```sh
python3 validate_package.py
```

The validator checks all 39 identities, module counts, title reconciliation, exact copied text, curriculum codes and proposed references. It uses only the Python standard library and makes no Drive changes or network calls.
