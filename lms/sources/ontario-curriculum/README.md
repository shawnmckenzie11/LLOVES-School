# Ontario curriculum PDF sources

Register Ministry documents in the LLOVES `curriculum_documents` table. `seed_curriculum` extracts **course code + official title** from local PDFs; it never invents expectation wording.

This catalog round is **Mathematics + Grade 11–12 Science + Health and Physical Education** only.

- Mathematics 11–12: `ontario-math-curriculum-gr-11-12.pdf` (seeded under this folder).
- Science 11–12: [2009science11_12.pdf](https://www.edu.gov.on.ca/eng/curriculum/secondary/2009science11_12.pdf) → `science-11-12.pdf`.
- HPE assignable codes (PPL1O–PPL4O, PPZ3C, PSK4U, PLF4M, …) come from the [Grades 9–12 HPE PDF](https://www.edu.gov.on.ca/eng/curriculum/secondary/health9to12.pdf) → `health-pe-9-12.pdf`. The [elementary HPE DCP page](https://www.dcp.edu.gov.on.ca/en/curriculum/elementary-health-and-physical-education) is registered as a source only — it has no Ontario secondary course codes, so nothing is invented from it.

If a PDF is missing, run `python lms/fetch_ontario_curriculum_pdfs.py`. Overall/specific expectation wording is copied from verified seeds (`lms/seeds/mcf3m_expectations.json`). Other courses can still be assigned (`expectations_status=unverified`).

Module packs (`.imscc`) are **not** stored in git. Admin uploads them into `content_libraries` on the Fly `/data` volume.
