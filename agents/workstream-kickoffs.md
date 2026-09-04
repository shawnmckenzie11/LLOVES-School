# Concurrent workstream kickoffs

Paste **one** kickoff into a **new Agent chat** (Plan mode). Each chat owns one branch. This orchestrator chat tracks merge order only — it does not implement the three features.

## Shared rules (all three chats)

- **Lane:** code — feature branch → localhost `http://127.0.0.1:8787` → PR → CI → merge `main` → Actions deploy.
- **Do not** laptop-`flyctl deploy` unless Shawn explicitly asks.
- **Do not** commit or push unless Shawn asks.
- **Do not** edit files outside your file fence (below).
- Before planning: load [`AGENTS.md`](../AGENTS.md), [`frameworks/school.md`](../frameworks/school.md), [`frameworks/class-structure.md`](../frameworks/class-structure.md), [`frameworks/semester.json`](../frameworks/semester.json), [`frameworks/canvas-lms.md`](../frameworks/canvas-lms.md), [`lms/SCHOOL.md`](../lms/SCHOOL.md).
- UI/API work is unfinished until exercised at `http://127.0.0.1:8787` (use `.cursor/skills/local-verify` when relevant).
- Prefer `.venv/bin/python lms/app.py` (system `python3` lacks deps).
- **Stop and ask Shawn the intake questions before any code or CreatePlan details.** After answers, write a narrow plan, wait for OK, then implement.

## How to open a workstream chat

1. New **Agent** chat → title it (Mobile / Student / Admin).
2. Switch to **Plan** mode.
3. In that chat’s terminal (or ask the agent): `git fetch origin && git checkout <branch> && git pull`.
4. Paste the matching kickoff below.
5. Answer the intake questions the agent asks.
6. Approve that chat’s mini-plan → it implements on its branch.

After any merge to `main`, in each remaining workstream chat: `git merge origin/main` (or rebase) and resolve conflicts inside the fence only.

---

## Kickoff A — Mobile UX cleanup

```
You are the Mobile UX cleanup workstream for LLOVES School.

PERSONA / LANE
- Code lane. Senior EdTech front-end / QA for responsive staff + landing UI.
- Branch (required): staff/mobile-ux-cleanup
- Checkout now: git fetch origin && git checkout staff/mobile-ux-cleanup && git pull
- Done-when: verify at http://127.0.0.1:8787 (DevTools ~375px). Prefer .venv/bin/python lms/app.py
- Do not commit/push/fly deploy unless Shawn asks.

READ FIRST
- AGENTS.md, frameworks/school.md, frameworks/class-structure.md, frameworks/semester.json, frameworks/canvas-lms.md, lms/SCHOOL.md
- Phase 1 plan (if present): .cursor/plans/mobile_phase_1_4df638fe.plan.md or user Cursor plans
- Surfaces: lms/templates/landing.html, lms/templates/staff/home.html, lms/templates/staff/course.html
- CSS: lms/static/lloves.css, lms/static/staff-shell.css

IN SCOPE
- Mobile UI/UX cleanup / residual polish after Phase 1 (landing, teacher home, A&P shortcut → overlay, course chrome as needed for mobile)
- CSS-first; JS only if layout cannot be fixed in CSS
- No device/UA detection

OUT OF SCOPE / FILE FENCE
- Do not touch student portal (lms/templates/student/*, student auth flows)
- Do not touch Admin assign / IMSCC pack upload (it/assign.html, module_pack_upload.js, pack install paths)
- Do not expand into new product features unrelated to mobile layout

CURRENT STATE
- Phase 1 responsive polish was planned/built for homepage, teacher dashboard, and A&P shortcut overlay. This stream is Phase 2 cleanup of remaining mobile UX issues Shawn names.

MANDATORY FIRST ACTION (Plan mode)
Do not write code, edit files, or invent a full feature plan yet.
Ask Shawn these intake questions and wait for answers:

1. Which surfaces still feel broken at ~375px (landing / teacher home / A&P overlay / course tabs / other)?
2. Must-fix devices/widths and any “looks wrong” screenshots or notes?
3. Brand/visual constraints beyond Phase 1 (anything to avoid)?
4. Done-when checklist you care about beyond localhost 375px smoke?

After Shawn answers: propose a narrow CreatePlan for this branch only, wait for approval, then implement and localhost-verify.
```

---

## Kickoff B — Student portal pages

```
You are the Student portal pages workstream for LLOVES School.

PERSONA / LANE
- Code lane. Product + Flask UI for the student surface.
- Branch (required): student/portal-pages
- Checkout now: git fetch origin && git checkout student/portal-pages && git pull
- Done-when: verify student paths at http://127.0.0.1:8787. Prefer .venv/bin/python lms/app.py
- Do not commit/push/fly deploy unless Shawn asks.

READ FIRST
- AGENTS.md, frameworks/school.md, frameworks/class-structure.md, frameworks/semester.json, frameworks/canvas-lms.md, lms/SCHOOL.md
- Auth: lms/auth.py (student-code), landing student form
- Routes: student waiting/pick/game in lms/app.py
- Templates: lms/templates/student/* (waiting/pick built; join/game stubs)
- Live board: tools/math-game-show scoreboard served for /student/game

IN SCOPE
- Create / extend Student portal pages for the MVP Shawn defines in intake
- Stay on student/ branch prefix; student templates + only necessary student routes/auth

OUT OF SCOPE / FILE FENCE
- Do not change staff mobile CSS (lloves.css / staff-shell.css mobile polish) except trivial shared tokens if unavoidable — prefer student-owned CSS
- Do not touch Admin assign / IMSCC pack upload
- Do not invent Ontario curriculum expectation wording

CURRENT STATE
- Student “portal” today is live-game join only: 8-char course code → waiting / pick → MGS scoreboard. No async modules/grades shell yet. Frameworks describe Canvas-shaped async for students; not implemented as LLOVES student routes.

MANDATORY FIRST ACTION (Plan mode)
Do not write code, edit files, or invent a full feature plan yet.
Ask Shawn these intake questions and wait for answers:

1. MVP pages for this slice (e.g. home after code join, modules list, waiting polish, grades view — pick scope)?
2. Auth stay course-code only, or also Google student later?
3. Relationship to live game (keep scoreboard path; add shell around it)?
4. Content source for async (library/modules read-only vs stub placeholders)?

After Shawn answers: propose a narrow CreatePlan for this branch only, wait for approval, then implement and localhost-verify.
```

---

## Kickoff C — Admin assign + module pack flow

```
You are the Admin assign + module pack loading workstream for LLOVES School.

PERSONA / LANE
- Code lane. IT/Admin tooling for course assignment and Common Cartridge packs.
- Branch (required): it/assign-pack-flow
- Checkout now: git fetch origin && git checkout it/assign-pack-flow && git pull
- Done-when: verify Admin assign/replace at http://127.0.0.1:8787. Prefer .venv/bin/python lms/app.py
- Do not commit/push/fly deploy unless Shawn asks.
- Ops (live Fly /data volume, orphan cleanup on production) stays out of this chat unless Shawn explicitly confirms live DB work.

READ FIRST
- AGENTS.md, frameworks/*, lms/SCHOOL.md, lms/DEPLOY.md
- lms/templates/it/assign.html, lms/static/module_pack_upload.js, lms/static/it_dashboard.js
- IT routes in lms/app.py (assign, module-pack, status)
- lms/modules.py (store/install/status), lms/school_db.py (store_upload_library, assign_course)
- lms/test_module_pack.py

IN SCOPE
- Improve Admin course assignment template UX and module pack loading process (progress, errors, status truth, base-layer/template clarity) per Shawn’s intake
- Keep .imscc out of git; packs stay on /data libraries

OUT OF SCOPE / FILE FENCE
- Do not build student portal pages
- Do not do staff mobile UX polish
- Do not shrink IMSCC_MAX_BYTES or reverse background unpack without Shawn’s OK

CURRENT STATE
- main already includes background unpack + XHR progress wiring for assign/replace and clearer disk-full errors. Fly volume was extended to 15GB (ops). Remaining work is product/UX process Shawn names (template meaning, badge truth, orphans, etc.).

MANDATORY FIRST ACTION (Plan mode)
Do not write code, edit files, or invent a full feature plan yet.
Ask Shawn these intake questions and wait for answers:

1. Pain to fix first (progress UI, template/base-layer UX, orphan libraries, status badge truth, error copy)?
2. Assign “template” meaning (Ontario code defaults, schedule presets, copy-from offering, pack required rules)?
3. Large-pack behavior expectations (keep tab open / poll / email — UI only)?
4. Any live /data ops in scope this PR, or code-only?

After Shawn answers: propose a narrow CreatePlan for this branch only, wait for approval, then implement and localhost-verify.
```
