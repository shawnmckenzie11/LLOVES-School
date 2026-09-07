# ALC / LLOVES — security & privacy note (not a certification)

This is an **operational** description of what the Flask LMS implements today, plus a **gap list for legal counsel**. It does **not** claim PIPEDA, FIPPA, or Ministry of Education certification.

Product holds **Ontario secondary student records** (roster names/codenames, attendance, participation, grades) for ELC online delivery, with a homeschool-facing ALC front door. **No payments** in this round (no Stripe / PCI).

## Tenancy (what is true)

The live app is **one school**: ELC, default `tenants` row `slug=elc`. Staff see only classes they own; IT sees only users/offerings in their tenant. A second `tenants` row is the **isolation seam** for another school/family later — it is not a marketed multi-tenant product yet. Do not claim “multi-tenant SaaS” in customer copy.

Ontario curriculum libraries (IMSCC templates) are still **shared by course code** across the sqlite file. That is lesson content, not a student’s personal record. Student PII lives on offerings/classes/roster/grade/live-session rows keyed by `tenant_id` + teacher ownership.

## Implemented

| Control | What exists |
| --- | --- |
| Roles | IT / staff / student; unknown Google accounts are not auto-created |
| Staff isolation | Teacher A cannot open teacher B’s class, gradebook, or live session |
| Tenant seam | `tenants` + `users.tenant_id` + `course_offerings.tenant_id`; IT/staff lists and class access are filtered |
| MFA (privileged) | Email 6-digit 2SV on **every** staff/IT production login (`FLASK_ENV=production`) |
| Student auth | Ephemeral live-session code + roster name; rate-limited failed joins; visit tokens scope tabs |
| In transit | Fly `force_https`, `alc.mckenzian.com` TLS, `SESSION_COOKIE_SECURE` in production |
| At rest | Fly volume `lloves_data` is encrypted by default (sqlite is not SQLCipher) |
| Secrets | OAuth/email keys are env/Fly secrets; not referenced from `lms/static/` |
| Headers | `X-Content-Type-Options`, `X-Frame-Options`, CSP, HSTS in production |
| Audit | `access_audit_log` for login, student-record views, staff admin, offering assign, student join; IT **Audit** tab + `/it/audit.csv` |

## Student-portal MFA policy (minors)

Students **do not** use Google OAuth. The join threat model is “know the live code and match the roster,” not “prove a Google account.” Requiring phone authenticators or Google MFA for minors would expand identity collection (phone numbers, Google accounts) without a parent/guardian consent flow.

Policy:

- **Do not** require MFA on the student portal in this round.
- Keep live codes **session-scoped** (idle offering codes do not admit students).
- Prefer **codenames** over legal names on the roster where teaching practice allows.
- Homeschool/external families: access is still teacher-issued code + name, not a family Google login.

Legal should still review whether course-code join is an acceptable authentication level for the personal information stored (mood, attendance, grades).

## OAuth / consent (staff vs families)

| Flow | Scopes | Minors |
| --- | --- | --- |
| Staff / IT | `openid email profile` only | Adult staff Google accounts; allowlisted by IT |
| Student | None (no Google) | No OAuth consent screen; no Workspace `hd=` |

Gaps for legal: parental/guardian consent for storing a minor’s name, attendance, and grades when the customer is an **external homeschool**, not the original enrolled ELC student; Google consent-screen naming (LLOVES vs ALC) is a 3.1 branding item.

## Encryption caveats (legal)

- Volume encryption is **Fly host-disk encryption**, not application-level field encryption.
- Backups/snapshots of `lloves_data` inherit Fly’s snapshot controls; retention of snapshots is an ops/legal question.
- Confirm production volume `encrypted: true` with `fly volumes list` after any volume recreation. Never pass `--no-encryption`.

## PIPEDA / FIPPA / Ministry — gap list for legal

Counsel should treat the following as **open**, not implemented:

1. **Accountability** — named privacy officer, written PIPEDA/FIPPA policies, vendor (Fly, Google, Resend) agreements and cross-border processing (US Google OAuth / possible US email).
2. **Lawful basis & consent** — especially external minors; school vs service-provider role under FIPPA if a board/school is the customer.
3. **Retention & deletion** — no automated purge of student records, audit logs, or IMSCC trees; no documented destruction schedule.
4. **Access/correction** — no parent/student self-serve “download my data” or correction UI; IT CSV audit is staff-side only.
5. **Breach process** — no in-app incident workflow or statutory notification timers.
6. **Purpose limitation / minimization** — roster still stores display names; mood/character live-session fields; confirm each field is necessary.
7. **Audit completeness** — logs cover login, class page views, and selected admin writes; not every JSON poll or grade-weight tweak. No WORM/tamper-evident storage.
8. **Subprocessors** — Fly.io (yyz), Google Identity, Resend/SMTP; not listed in a public privacy notice yet (3.1 marketing).
9. **Multi-school operations** — second tenant is a schema seam only; no tenant admin UI, no data-residency choice, shared curriculum libraries.
10. **Ministry audit pack** — CSV + sqlite can be produced; there is no formal information-management manual mapping fields to Ontario student-record requirements.

## Payments

Out of scope. Do not add card collection.

## How to produce a student-record access extract

1. IT signs in (MFA) → Audit tab → Download CSV, filter by class/`student_id`/date.
2. For the underlying rows: staff course Attendance & Participation + Grades for that class (same tenant/teacher ownership).
3. Legal/ops may also snapshot `/data/lloves.sqlite` from the Fly volume under existing backup practice.
