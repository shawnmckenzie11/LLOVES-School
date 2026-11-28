# Teacher Settings and Admin Console — UI spec

Tickets: **MCK-539** (teacher Settings), **MCK-540** (admin console redesign).
Desktop only, 1280px minimum. This document and the 1280×800 mocks are the deliverable. Do not treat this as a build brief to merge or deploy.

Copy: Wonder owns final wording. Strings already set by Wonder are marked **(Wonder)**. Every other user-facing string is a **placeholder for Wonder**.

---

## 1. Design decisions

- Settings and the Teacher plans console use the **staff-shell** light language, not the dark IT dashboard theme in `lloves.css`. The rest of `/it` (Staff, Offerings, and so on) stays as it is; MCK-540 restyles only `/it/teacher-plans`.
- Settings is a **read-only account surface**. Teachers cannot change plan, features, name, or email here. There is no billing product yet, so Billing is a clearly marked future placeholder that points at MCK-355 / MCK-356 — not a live form.
- The Settings left nav is **Profile**, **Plan**, **Connected apps**, **Billing**. Plan uses a **Basic vs Pro checkmark table** with **Pro** badges on plan-gated rows, not a single-plan checklist.
- The admin console has **three tabs**: **Teachers**, **Plans**, **Audit**. Teachers is the working table (search, plan filter, bulk Set plan, row plan dropdown, Default / On / Off chips, last changed, row click opens the **Change history** drawer, save toast with Undo). Plans is a read-only registry of what each plan includes. Audit is a tenant-wide log with filters and **no export**.
- Admin writes reuse the existing MCK-538 / PR #526 setters and the append-only `teacher_feature_audit` table. **No new tables or audit mutations** (no update, no delete). Undo is a **second normal write** that appends its own audit row. Bulk Set plan is **one transaction**, one audit row per teacher, all-or-nothing, with the same tenant and archived checks as today.
- Plan-change copy says changes apply at the **next class Start**. Today the server still applies on the teacher’s next request; mid-class deferral is a later behaviour ticket, not part of MCK-539 or MCK-540.
- The locked-with-**Pro**-badge teacher view on live boards is a follow-up for the course shell, not these two pages. Wonder’s locked-panel line is listed below so builders do not invent a substitute.

---

## 2. Tokens and components

Reuse `lms/static/staff-shell.css`. Do not introduce a second palette.

| Token | Value | Source |
| --- | --- | --- |
| Page background | `#e8eef4` | `body.staff-shell` |
| Ink | `#12202e` | `body.staff-shell` |
| Mid text | `#334155` | `.staff-top-link`, course meta |
| Muted text | `#64748b` | placeholder meta |
| Pane / table header | `#f7f9fb` | `--live-pane-bg` |
| Border | `#d5dde6` / `#c5d0dc` | `--live-border`, top menu |
| Accent / Pro / On | `#0f766e`, hover `#0b5f59` | `.staff-top-btn.on`, primary buttons |
| Surface | `#fff` | cards, menus, drawers |
| Font | `"Segoe UI", system-ui, sans-serif` | staff-shell |
| Card radius | `10px` | `.staff-top-menu` |
| Control radius | `8px` | `.staff-top-btn` |
| Chip radius | `999px` | `.course-live-chip` |
| Shell gap | `12px` | `--live-shell-gap` |
| Table text | `14px`, left-aligned, regular weight | MCK-540 requirement |
| Menu shadow | `0 10px 28px rgba(15, 23, 42, 0.18)` | `.staff-help-menu` |

**Components to add (staff-shell, not a new design system):**

- **Account menu** — same `details` pattern as `staff/_help_menu.html` (white 10px menu, 12.5rem min, `.staff-help-item` rows). Items: Settings, Log out.
- **Settings shell** — left sub-nav (one current item) + one main panel.
- **Read-only field** — label above value, no inputs.
- **Plan card** — plan name, short status line, “Managed by your admin”.
- **Plan comparison table** — feature label, Basic column, Pro column, check / em dash, **Pro** badge on gated rows.
- **Future panel** — dashed border, muted, not a form (Billing).
- **Admin top tabs** — Teachers / Plans / Audit, visually related to `.staff-top-btn` / `.on`.
- **Admin table** — 14px, leftover whitespace, row hover, checkbox column, sticky header.
- **Plan dropdown** — compact select in the row; confirm before write.
- **Override chips** — **Default**, **On**, **Off** **(Wonder)**. Default shows the inherited value in muted text (for example “Default · Off”).
- **Change history drawer** — right side panel, title **Change history** **(Wonder)**.
- **Save toast** — short confirmation plus **Undo**.
- **Confirm dialog** and **batch error** banner (all-or-nothing bulk failure).
- **Empty / loading / error** states for the Teachers table and Audit log.

---

## 3. A. Teacher Settings — MCK-539

### Entry and chrome

There is no Settings page today. Staff home (`lms/templates/staff/home.html`) and the course / bots top bar put **Log out** in a flat corner cluster (`aria-label="Account"`) next to Help. Introduce an **Account** menu in both `staff-home-corner` and `staff-top-menu`:

- Account → **Settings** (new page), **Log out** (`GET /logout`, existing).
- Leave Help, What’s new, and IT-only Admin / Bots where they are.

New page: `GET /staff/settings` (name is a placeholder), `staff-shell` chrome, 1280px min, no mobile layout in this ticket. Left sub-nav: **Profile**, **Plan**, **Connected apps**, **Billing**.

### Profile

| Field | Source | Existing or new | Editability |
| --- | --- | --- | --- |
| Name | `users.display_name` | Existing | **Read-only** for the teacher. There is no self-edit route. |
| Google sign-in email | `users.email` | Existing | **Read-only**. Set at Google sign-in. |

**Name edit route (do not put it on Settings):** `POST /it/staff/<staff_id>/rename` is IT-only (`it_staff_rename` → `school.rename_staff`). It already lives on the IT dashboard Staff tab. Teachers do not get a name field they can submit.

Do **not** add a time-zone control. `users` has no timezone column, and there is no edit route. Do not persist America/Toronto as a preference. Sign out stays in the Account menu, not duplicated as a form action on Profile unless Wonder wants a second control later.

### Plan

Read-only. Data already on the signed-in user and in `FEATURE_REGISTRY` / `teacher_features`:

| Field | Source | Existing or new |
| --- | --- | --- |
| Current plan (Basic / Pro) | `users.plan` (`basic` / `pro`) | Existing |
| Effective features | `has_feature` / `teacher_features` (plan default + `user_features` overrides) | Existing |

**Layout:** a plan card at the top (plan name + “Managed by your admin” — placeholder for Wonder) and a **checkmark table** below. One row per registry key. Columns: feature, Basic, Pro. A check means the plan includes it with no override. Pro-only rows carry the **Pro** badge **(Wonder)**.

Teacher-facing feature labels (placeholders for Wonder except the six already set):

| Key | Label | Basic | Pro |
| --- | --- | --- | --- |
| `mc_questions` | Multiple-choice questions *(placeholder)* | Yes | Yes |
| `rank_questions` | Ranking questions *(placeholder)* | Yes | Yes |
| `numeric_questions` | Numeric answers *(placeholder)* | Yes | Yes |
| `essay_questions` | Essay answers *(placeholder)* | Yes | Yes |
| `live_slides` | Live lesson slides *(placeholder)* | Yes | Yes |
| `catchup_links` | Catch-up, most recent class *(placeholder)* | Yes | Yes |
| `catchup_full` | Catch-up, all past classes *(placeholder)* | No | Yes |
| `celebrations` | Celebrations *(placeholder)* | Yes | Yes |
| `gradebook_tracking` | Gradebook, attendance, and participation *(placeholder)* | Yes | Yes |
| `game_show` | Rank game show *(placeholder)* | Yes | Yes |
| `slides_import` | Google Slides / PowerPoint import *(placeholder)* | No | Yes |
| `whiteboard_submit` | Draw and submit *(placeholder)* | Yes | Yes |
| `whiteboard` | Whiteboards **(Wonder)** | No | Yes |
| `media` | Media **(Wonder)** | No | Yes |
| `team_draw` | Team draw **(Wonder)** | No | Yes |
| `group_draw` | Group draw **(Wonder)** | No | Yes |
| `portfolio_gdoc` | Portfolio to Google Doc **(Wonder)** | No | Yes |
| `news_wire_extras` | News wire extras **(Wonder)** | No | Yes |

If the teacher has a per-feature override, show that only as a short note under the card (for example “Whiteboards is on for your account”) — do not turn this table into an editor. Overrides stay an admin action.

There is **no upgrade checkout**. The path off Basic is “ask your admin” (placeholder for Wonder; related Wonder line for locked course controls: “[Feature] is part of the Pro plan. Ask your admin to turn it on.”).

### Connected apps

| Field | Source | Existing or new | Rules |
| --- | --- | --- | --- |
| Google Slides | `GET /api/auth/slides-status` returns `{ connected: bool, … }` | Existing | Show only **Connected** or **Not connected**. **Never expose tokens**, refresh tokens, raw `connect_url` internals, or `google_sub`. |

The status route is already wrapped in `@feature_required("slides_import")` and also returns `allowed` (slides-operator allowlist). On Settings:

- If the teacher does not have `slides_import`, **do not call** the status API (it 404s). Hide the Google Slides row, or show a non-interactive “Not on this plan” line from `teacher_features` only.
- If they have the feature but `allowed` is false, hide the row.
- If they have the feature and are allowed, show **Connected** or **Not connected** from `connected`. No token fields, no “connect” flow designed as a Settings action in this ticket (connect already lives on the slides path).

No other connected apps have a safe boolean status to show. Do not invent them.

### Billing

**No billing or subscription data exists** — no Stripe, no table, no invoices, no payment method. This section is a **future placeholder only**, not a live design.

- Heading and one or two lines of placeholder copy for Wonder, pointing at a future Billing & subscription project (**MCK-355 / MCK-356**).
- Dashed, muted panel. No form controls, no fake prices, no fake invoice list, no “upgrade” button that posts.
- Teachers still see their plan on the Plan section (`users.plan`), which is the only plan truth today.

---

## 4. B. Admin console — MCK-540

**Page:** existing `GET /it/teacher-plans` (`it_teacher_plans` in `lms/routes_teacher.py`, template `lms/templates/it/teacher_plans.html`). Today this is a dark, 1990-style stack of per-teacher cards, raw feature keys, and full-page POST forms. There is no search, no history UI, and no last-changed column.

**Visual + approved writes only.** Reuse PR #526 / MCK-538 routes and `teacher_feature_audit`. No new schema. Change history and Audit stay **scoped to the signed-in admin’s tenant**. Do not show emails on this page (today’s helper `teacher_display_handle` already uses `display_name` or “User {id}”).

Existing write routes (already accept form or JSON, already tenant- and archived-scoped):

- `POST /it/api/teacher-plans/<teacher_id>/plan` — `admin_set_teacher_plan`
- `POST /it/api/teacher-plans/<teacher_id>/features/<feature_key>` — `admin_set_teacher_override` (`on` / `off` / `plan`)
- `POST /it/api/teacher-plans/<teacher_id>/reset-overrides` — `admin_reset_teacher_overrides`

Existing list payload from `list_teachers_for_plan_admin`: `id`, `handle`, `plan`, `features[{ key, source, effective }]`. No email, no last changed, no history.

### Tab: Teachers

Header: search (client-side over **handle** and id — the list has no email), **plan filter** (All / Basic / Pro), and a bulk **Set plan** control that is disabled until at least one row is selected.

**Table, one row per teacher:**

| Column | Data | Notes |
| --- | --- | --- |
| Checkbox | — | Bulk selection |
| Teacher | `handle` | Display name, or “User {id}” |
| School | `SCHOOL_NAME` already passed to the template | Same school for the tenant; still useful for orientation |
| Plan | `users.plan` | **Dropdown** (Basic / Pro). Confirm, then POST the existing per-teacher plan route. |
| Overrides | chips | **Default**, **On**, **Off** **(Wonder)**. Default shows the inherited plan value muted (“Default · On” / “Default · Off”). On / Off are explicit `user_features` rows. Clicking a chip confirms, then POSTs the existing override route (`plan` / `on` / `off`). |
| Last changed | `max(teacher_feature_audit.created_at)` for that teacher | **Not in the list payload today.** Add a **read-only** enrichment on the existing GET (or a tenant-scoped read helper). No new column on `users`. Format in America/Toronto. |

**Override chips at 1280px:** the row shows the eight plan-differentiated features (`catchup_full`, `slides_import`, `whiteboard`, `media`, `team_draw`, `group_draw`, `portfolio_gdoc`, `news_wire_extras`). A Basic-on feature appears in the row only when it is overridden. The drawer can list the full registry for that teacher.

**Row click** (not on the dropdown or chips) opens the right-hand **Change history** drawer **(Wonder)**. Append-only list: when, who, what, before → after. “Who” is the admin’s **handle** resolved at read time from `admin_user_id` — never an email, never a raw token. There is **no list-history API today**; add a **tenant-scoped GET** (or include the rows on the page payload) that reads `teacher_feature_audit` and rejects teachers outside the admin’s tenant. No new writes.

**Save toast with Undo.** After a successful plan or override write, show a toast (placeholder copy for Wonder, for example “Plan updated to Pro”) with **Undo**. Undo calls the **same existing setter** with the previous value. That is a **second normal write** and appends **its own audit row**. There is no undo table and no deleting or editing the first audit row. If the second write fails (503 / 404), keep the toast in an error state; do not pretend the undo landed.

**Bulk Set plan.** This cannot stay a client-side loop of the per-teacher POST if the rule is one transaction and all-or-nothing. Add **one new POST** (for example `/it/api/teacher-plans/bulk-plan`) that:

- Accepts a list of teacher ids and a plan (`basic` / `pro`).
- Runs in **one transaction**.
- Applies the same tenant and archived / staff-or-IT checks as `admin_set_teacher_plan`.
- Writes **one audit row per teacher** whose plan actually changes (same shape as today’s `key = "plan"` rows). Teachers already on the target plan are no-ops and get no audit row.
- **All-or-nothing:** any failed check rolls the batch back. The UI shows an **error state** for a failed batch (no partial checkmarks, no implied success).
- No new tables.

`POST .../reset-overrides` can stay a per-row or drawer action; it is already transactional for one teacher.

**Empty / loading / error:** empty school (“No staff or IT accounts in this school” — placeholder), skeleton or “Loading teachers” on first paint if the page is data-fetched, and an inline error if the list or a write fails (including 503 from a locked school connection).

### Tab: Plans

Read-only registry. Render `FEATURE_REGISTRY` as the same Basic vs Pro checkmark table used on teacher Settings → Plan (admin-facing labels can match). No writes, no per-teacher state, no overrides. This is documentation of plan defaults, not an editor.

### Tab: Audit

Tenant-wide log of `teacher_feature_audit` for teachers in the admin’s tenant. **No export** (do not add a CSV or download control; do not reuse `it_audit_csv`, which is a different IT audit).

Filters: **teacher**, **feature** (registry key or `plan`), **date** (from / to, America/Toronto). Columns: when, teacher handle, who (admin handle), what, before → after.

Needs a **tenant-scoped GET** (new read, no schema). Never list another tenant’s rows. Never show emails.

### Mid-class behaviour (copy vs server)

UI copy (placeholder for Wonder): plan and feature changes apply at the **next class Start**, and are not pulled off students mid-class. Current server behaviour still applies on the next request. Shipping deferred apply is **out of scope** for MCK-540.

---

## 5. Existing vs new data (backend gaps)

**Already in the product**

- `users`: `id`, `email`, `display_name`, `role`, `tenant_id`, `plan`, `archived_at`, `created_at`, `last_login_at`, `google_sub` (do not display `google_sub` or verification columns).
- `user_features`: per-teacher overrides (`on` / `off`); missing row = follow plan.
- `teacher_feature_audit`: append-only; triggers block UPDATE and DELETE; stores **account ids and setting values only**.
- `FEATURE_REGISTRY` in `lms/features.py` (Basic vs Pro defaults).
- School label: `SCHOOL_NAME` (app constant), already on the Teacher plans template.
- Google Slides boolean: `GET /api/auth/slides-status` (`connected` only — do not pass tokens through Settings).
- Auth: `GET /logout`. IT rename: `POST /it/staff/<staff_id>/rename`.

**Not in the product (do not design as live)**

- Stripe, subscriptions, invoices, payment methods, billing emails.
- `users.timezone` or any per-teacher time-zone preference.
- A teacher self-edit route for name or email.
- A history or audit **list** API (table exists; UI cannot read it yet).
- `last_changed` on the teacher list.
- A bulk plan endpoint (today only one teacher per POST).
- Mid-class “apply at next Start” deferral storage.

**Allowed additions for these tickets (no new schema)**

- `GET /staff/settings` page and Account menu links.
- Read helpers / GETs: last-changed enrichment, per-teacher history, tenant Audit log — all tenant-scoped reads of `teacher_feature_audit`.
- `POST` bulk Set plan: one transaction, one audit row per changed teacher, all-or-nothing.
- Undo: client calls the existing per-teacher setter; no new store.

---

## 6. Slice order (smallest shippable first)

Each slice should be usable on its own. Files are the ones that slice actually touches.

### MCK-539 — Settings

1. **Account menu + Settings shell + Profile.** Files: `lms/templates/staff/home.html`, `lms/templates/staff/course.html`, `lms/templates/staff/bots.html`, new `lms/templates/staff/_account_menu.html`, new `lms/templates/staff/settings.html`, `lms/static/staff-shell.css`, `lms/routes_teacher.py` (new GET). Data: `display_name`, `email` only.
2. **Plan card + Basic vs Pro checkmark table** (read-only). Files: `settings.html`, `staff-shell.css`, the Settings GET (pass `users.plan` + registry / `teacher_features`).
3. **Connected apps.** Files: `settings.html`, a small `lms/static/staff_settings.js` if the status call stays client-side, `staff-shell.css`. Calls `GET /api/auth/slides-status` only when `slides_import` is on; renders Connected / Not connected.
4. **Billing placeholder** pointing at MCK-355 / MCK-356. Files: `settings.html`, `staff-shell.css`. No API.

### MCK-540 — Admin console

1. **Teachers tab visual:** search, plan filter, row plan dropdown with confirm, Default / On / Off chips, empty / loading / error, light staff-shell styling. Writes go to the **existing** per-teacher POST routes. Files: `lms/templates/it/teacher_plans.html`, new `lms/static/it_teacher_plans.js`, `lms/static/staff-shell.css` (or a small `it-teacher-plans.css` that only uses staff-shell tokens). Last changed may be blank in this slice.
2. **Last changed + Change history drawer.** Files: `lms/feature_admin.py` (read-only helpers), `lms/routes_teacher.py` (tenant-scoped GET or enrich the page GET), template + JS. No schema.
3. **Save toast + Undo.** Files: JS + CSS. Undo = second call to the existing plan or override POST; new audit row follows automatically.
4. **Bulk Set plan** (one transaction, all-or-nothing) + failed-batch error state. Files: `lms/feature_admin.py`, `lms/routes_teacher.py` (new POST), tests beside `lms/test_mck538_teacher_plans.py`, template + JS.
5. **Plans tab** (read-only registry table). Files: template + CSS; data from `FEATURE_REGISTRY` already imported in the page process.
6. **Audit tab** (tenant-wide log, filters for teacher / feature / date, **no export**). Files: `feature_admin.py` (read helper), `routes_teacher.py` (GET), template + JS.

QA should gate each slice. Production deploy still needs Shawn’s OK.

---

## 7. Wonder copy already set (do not rewrite)

- Badge: **Pro**
- Locked course panel (follow-up, not Settings/Admin): **“[Feature] is part of the Pro plan. Ask your admin to turn it on.”** Feature names: Whiteboards, Media, Team draw, Group draw, Portfolio to Google Doc, News wire extras.
- Chips: **Default**, **On**, **Off**
- Drawer title: **Change history**

All other labels, toast lines, confirmations, empty states, Billing placeholder copy, and “Managed by your admin” / “ask your admin” are placeholders for Wonder.

---

## 8. Mocks

All example data is obviously fake: “Teacher A”, “Example School”, `a@example.com`. No real names, emails, class codes, or join codes.

| File | Frame |
| --- | --- |
| `/workspace/settings-admin/settings-1280.png` | Settings, **Plan** open, 1280×800 |
| `/workspace/settings-admin/admin-1280.png` | Admin, **Teachers** table, 1280×800 |
| `/workspace/settings-admin/admin-history-1280.png` | Teachers table, **Change history** drawer open, 1280×800 |
| `/workspace/settings-admin/admin-audit-1280.png` | Admin, **Audit** tab, 1280×800 |

Source HTML lives beside the PNGs in `/workspace/settings-admin/`.
