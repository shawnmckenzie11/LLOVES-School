# Incident: alc Artifact / overload — 2026-09-21

**Status:** log triage only · **no Fly redeploy**  
**Reporter ask:** LC BT Ops CoS (P0) — Shawn live class on alc hit Artifact/group-work → internal server error / server overloaded (post #115+#116)  
**App:** `lloves-lms` → https://alc.mckenzian.com  
**Window requested:** 13:45–15:30 America/Toronto (UTC 17:45–19:30) 2026-09-21  
**Note author:** LC Development & Engineering · 15:30 ET approx

---

## Deployed tip during / after window

| Fly release | Created (UTC) | Approx ET | Image GH_SHA | Notes |
|-------------|---------------|-----------|--------------|-------|
| v146 | 16:56:37Z | 12:56 | `01392ce` (#115) | Earlier Shawn GO tip |
| v147 | 17:46:56Z | 13:46 | (pre-#116 merge image) | Start of requested window |
| **v148** | **18:00:51Z** | **14:00** | **`1849339`** (#116 merge) | Machine relaunch 14:00:58 ET · **still live** |

- `#116` merged 17:49:19Z → tip `18493392c06be77dcd78b903dd6668854bb3e221`.
- Live confirm at triage time: health 200; image label `GH_SHA=1849339…`; release **v148**.

---

## Log coverage (important gap)

| Source | Result |
|--------|--------|
| `flyctl logs -a lloves-lms --no-tail -j` | Short ring buffer only. Captured **~19:24:21–19:24:26Z (15:24 ET)** — **end** of window, not full 13:45–15:30 ET archive. Raw: `lc-qa/raw/fly-buffer-lloves-lms-2026-09-21T1924Z.json` |
| Fly Prometheus (`api.fly.io/prometheus/...`) | Failed: “something went wrong resolving organization” (slug + org id) |
| `/data/logs/session-*.jsonl` | **Score / celebration events**, not HTTP access logs |
| Historical HTTP archive | **Not available** from CLI in this pass |

So: strong smoking-gun samples at 15:24 ET; **not** a complete 500 count for the whole class.

---

## Hard errors captured (Fly app logs)

At **2026-09-21T19:24:21Z** and **19:24:26Z** (15:24 ET):

```
sqlite3.OperationalError: database is locked
```

**Routes (exceptions):**

1. `GET /api/student/state` → `student_state` → `_require_active_live_attendee` → `touch_live_session_heartbeat` → `_resume_live_attendee` (`school_db.py` ~9111 `self.conn.execute(...)`)
2. `POST /api/student/heartbeat` → same heartbeat / resume path (`auth.py` ~1046)

**Count in buffer:** ≥4 `database is locked` lines; ≥2 `/api/student/state` ERROR; ≥1 `/api/student/heartbeat` ERROR (buffer truncated mid-trace earlier).

**Not seen in buffer:** OOM, `MemoryError`, `WorkerTimeout`, gunicorn worker kill, explicit `/artifact` route 500 lines, `module-files` 500 lines.

**Memory at triage SSH (after class pressure):** MemTotal ~985 MB · MemAvailable ~673 MB — **not OOM now** (1 shared CPU / 1024 MB VM).

**Collateral:** an earlier SSH `sqlite3` query **without** `busy_timeout` hung >35s against `/data/lloves.sqlite` while the class session was open — consistent with write-lock contention under live load.

---

## Live session / Artifact evidence (prod sqlite)

Open afternoon session (timestamps stored as UTC-looking ISO):

| Session | class_id | started_at | ended_at | attendees | still_in |
|---------|----------|------------|----------|-----------|----------|
| **11** | **8** | **2026-09-21T18:01:56** (~14:02 ET, right after v148) | **open** | **12** | **12** |
| 10 | 10 | 14:37:18Z | 15:55:06Z | 16 | 9 |
| 9 | 9 | 13:30:54Z | 14:32:43Z | 24 | 21 |

**Approx N (session 11):** **12** concurrent attendees still in at triage.

**Artifact / media prompts on session 11** (`live_session_prompts.kind = artifact`), published ~18:56–18:58Z (**14:56–14:58 ET**):

| id | artifact_id | media_url | notes |
|----|-------------|-----------|-------|
| 16–18 | `mcf3m-m1-c2-transformations` | `/static/live-media/m1c2-transforms.html` | Match challenges 1–3; active id 18 at triage |
| payload | `group_q: false` | individual publish | Ops “group-work” may be classroom language, not `live_group_*` tables |

Also bank media on earlier prompts, e.g.:

- `/staff/class/8/module-files/web_resources/bank-mirror/*.jpg|*.svg`

`live_group_members` / `live_group_responses` counts were **0** — no DB group-response rows for this failure mode.

Score log `session-29.jsonl` (UTC 18:11–19:00) shows ~10+ distinct student ids receiving points during the same afternoon class — aligns with N≈12.

---

## Timeline (thin)

1. **14:00 ET** — Fly **v148** / `#116` tip `1849339` live; machine start.  
2. **~14:02 ET** — live_class_session **11** (class 8) starts; fills to **12** attendees.  
3. **~14:56–14:58 ET** — teacher publishes **Artifact** Match challenges (`m1c2-transforms.html` / `mcf3m-m1-c2-transformations`).  
4. **15:24 ET** — Fly buffer shows **sqlite locked** 500s on student **state** + **heartbeat** (poll path every client hits).  
5. User/Ops report: UI “internal server error / server overloaded” at Artifact/group-work moment.

---

## Hypothesis (evidence-tied; not a fix)

Under ~**12** concurrent students, frequent `/api/student/state` + `/api/student/heartbeat` **writes** (`_resume_live_attendee`) collide with other sqlite writers on a **single 1 GB / 1 CPU** Fly VM. SQLite returns **`database is locked`** → Flask 500. Clients / proxy may surface that as “server overloaded.”

Artifact publish itself (`/static/live-media/...`) is static and was **not** seen throwing in the short log buffer; the **timing** lines up with Artifact being on screen while poll traffic continues. Bank `module-files` URLs appear in prompt payloads; no module-files 500 lines in the captured buffer.

**Not claimed:** OOM; exact total 500 count for 13:45–15:30; Artifact HTML handler crash; that `#116` alone caused the lock (class was on v148, but lock is a concurrency/sqlite pattern).

---

## Gaps / next if Ops wants Eng

- Full-window HTTP metrics need Fly Metrics UI or working Prometheus org token (CLI failed).  
- Optional: add durable access/error logging off the short Fly ring buffer.  
- Product follow-ups (needs Ops/Shawn clear — **not started**): shorten heartbeat write path / WAL busy handling / read-only state poll; scale VM or move off single-writer sqlite for live poll; no redeploy from this note.

---

## Actions taken

- Pulled Fly log buffer + release/image SHA; queried prod sqlite (session 11 Artifact payloads, N=12).  
- Wrote this note + raw buffer under `lc-qa/raw/`.  
- **No Fly redeploy / restart.**
