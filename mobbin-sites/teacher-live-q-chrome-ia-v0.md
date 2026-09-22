# Teacher live Q chrome pack — IA v0

**Owner:** Mobbin Sites  
**Shawn GO via Wonder:** tip-only · calm density  
**Surface:** RLC teacher **per-question** chrome (`live-question-card` / `.live-question-card-actions`)  
**Repo:** LLOVES-School (grounding only)  
**Date:** 2026-09-22  
**Runtime:** IA draft · Eng after GO · **No Fly** · **No LMS code** · **No Ministry content**

Wonder: silent / one-line cues only — no delight on toggles or Publish.

**Related IAs**
- Add New / bank: `teacher-add-new-bank-ia-v0.md` · type chips: `teacher-add-new-type-selector-ia-v0.md`
- Group poll: `group-submission-poll-ia-v0.md`
- MC reveal family: `teacher-live-shell-ia-v2.1-mc-reveal.md`
- Parent shell: `teacher-live-shell-ia-v2.md`

Acronyms: Information Architecture (IA); Run Live Class (RLC); Multiple Choice (MC); Learning Management System (LMS).

---

## 0. Job / lock

### Job

One **global teacher per-question chrome pack**:

1. **Save to card** — pin this Q onto the student **right panel** as a persistent card  
2. **Show Live Results** — arm visibility **before** Publish (not only post-publish)  
3. **Menu reorg** — grouped, denser, tablet-ok checkbox/button strip (no scatter)

### Out

Fly · LMS patches from Mobbin · pedagogy / Ministry stems · inventing new reveal UX beyond existing MC reveal family.

### As-built (grounding — labels only)

From `staff_ap.js` question card actions today:

| When | Controls (scattered) |
|------|----------------------|
| Head | `(Re)move` |
| Inactive + on-stage | `Publish` · optional mode select (`Individual` / `Individual in Group`) |
| Active | `Show Live Results` checkbox · `Reveal answers` *or* `Responses & points` · `Close` |
| Closed | `Responses & points` · “Final results” copy |

**Gap:** `Show Live Results` mounts only in **active** actions → teachers cannot arm it pre-publish. **Missing:** per-Q **Save to card**.

Student side (grounding): lifecycle cards render as `.student-live-card` in the question stack / right area; × dismiss removes from view; dock chips are for media/canvas/slides — **not** Qs. Save-to-card = teacher-armed **persist** so the Q card **stays** on the student right panel (does not evaporate with ephemeral close/advance).

---

## 1. Strip wire (teacher per-Q)

```
┌ live-question-card …………………………………………………………………………………┐
│ meta · status · [(Re)move]                                           │
│ stem · eq · options · (teacher soft tally if any)                    │
│                                                                      │
│ ┌ QChromeStrip (actions) — denser row / wrap 2 max ───────────────┐ │
│ │ [A Persist]  ☑ Save to card                                       │ │
│ │ [B Visibility] ☑ Show Live Results                                │ │
│ │ [C Lifecycle]  Publish ▾mode  |  Reveal… / Responses & points  | Close │ │
│ └───────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

**Tablet:** one card-width strip; groups stack vertically if &lt;720 CSS px; controls stay **left-aligned in group order A→B→C** — never scattered into card head or stem.

---

## 2. Components + groups (menu reorg)

| Group | Order | Controls | Role |
|-------|-------|----------|------|
| **A · Persist** | 1 | `Save to card` checkbox | Pin to student right-panel card rail |
| **B · Visibility** | 2 | `Show Live Results` checkbox | Arm student-facing live tally / reveal-family visibility |
| **C · Lifecycle** | 3 | `Publish` (+ mode select if group-capable) · post-live: `Reveal answers` *or* `Responses & points` · `Close` | Go live / end / staff responses |

| Always (secondary) | Placement |
|--------------------|-----------|
| `(Re)move` | Card **head** only (playlist relocate) — **not** in A/B/C strip |

### Density rules

- Checkboxes: compact `label` + short span (existing `.live-result-toggle` family)  
- Primary CTA: **Publish** only while inactive  
- Secondary buttons: ghost/secondary `live-q-btn` — one row when space allows  
- No duplicate toggles; no mystery third results card (MC reveal lock holds)

---

## 3. Save to card

**Label:** `Save to card`  
**Placement:** Group **A** on teacher per-Q chrome (same strip as Add New / Publish family — **not** inside Add New bank sheet; that sheet keeps **Save to bank**)

| State | Teacher | Student right panel |
|-------|---------|---------------------|
| **Off** (default) | Ephemeral — current lifecycle behavior | Q appears while facing / active; may clear on Close, dismiss, or stage leave (as today) |
| **On** | Pin armed for this live item | Saved Q **appears as a card** on student **right panel** and **does not disappear** when ephemeral would clear — persists as a card until teacher unpins **or** session ends / explicit remove |

### Empty / disabled

| Rule | Spec |
|------|------|
| No `live_item_id` yet | Checkbox **disabled** + calm hint “Add / stage this question first” (or hide until item exists) |
| Off-stage playlist ghost | Enabled only when item is on current stage **or** Eng already allows off-stage pin — default: **on-stage only** (match Publish gate) |
| Uncheck while pinned | Student card may leave rail on next project tick (calm; no Wonder fanfare) |
| Collision | **Not** the same as student **Save Work** download (`student-live-shell-ia-v0.2-save-work.md`) · **Not** **Save to bank** |

Wire tip for Eng (state shape only — no schema invent beyond flag): per live item `save_to_card: boolean` (default `false`). Student payload includes pinned cards for right-panel paint.

---

## 4. Show Live Results (pre-publish)

**Label:** keep as-built **`Show Live Results`**  
**Placement:** Group **B** — **visible in inactive (pre-publish) chrome**, not only after Publish

| State | Meaning |
|-------|---------|
| **Off** | Students do **not** see class tally / live results while item active (align existing `show_live_results === false` student gates) |
| **On (armed)** | After student **submit** and/or teacher **Reveal** family — students may see live results / MC distribution per existing rules in `teacher-live-shell-ia-v2.1-mc-reveal.md` + current student portal (`show_live_results` + submit/reveal) |

### Placement / lifecycle

| Phase | Strip shows |
|-------|-------------|
| **Inactive** (pre-publish) | A · B · **Publish** (+ mode) |
| **Active** | A · B (editable) · Reveal / Responses & points · **Close** |
| **Closed** | A (read or still editable — Eng pick; prefer still toggleable) · B read/togglable · Responses & points · “Final results” |

**Default:** keep product default (as-built DB default on = true). Pre-publish arming must **persist through Publish** (no reset on go-live).

**Hard rule:** Teacher soft bars in Questions card ≠ student-visible results. Student mirror still follows armed `Show Live Results` + submit/reveal family — do not invent new pedagogy.

---

## 5. Acceptance

- [ ] Per-Q strip groups **A Persist → B Visibility → C Lifecycle**; `(Re)move` stays in head  
- [ ] Tablet: no scatter; ≤2 wrap rows; readable hit targets  
- [ ] **Save to card** off = ephemeral; on = student right-panel card **persists** (does not disappear)  
- [ ] Save to card disabled/empty rules clear when no live item / off-stage  
- [ ] **Show Live Results** visible **before** Publish; armed value survives Publish  
- [ ] Armed + submit/reveal → student results align with existing MC reveal / `show_live_results` family  
- [ ] Off → no student live tally while active (closed may still show final per as-built)  
- [ ] No Fly · no mystery second results card · Wonder silent on toggles  

---

## 6. Awareness only (Eng primary — flag if layout fights)

Do **not** redesign in this pack; note if chrome density collides:

| Flag | Note |
|------|------|
| Student Q text **−25%** + resize | Student card typography / resize handle — Eng |
| **Relic Qs** | Leftover / ghost questions after publish or stage change |
| **Submit dead** | Student Submit inert on facing card |
| **Stem equation / image relics** | Stale eq or image after item swap |

If strip reorg worsens any of the above, stop density squeeze and file Eng — IA does not own those fixes.

---

## 7. Handoff — eggbot / Wonder / Shawn

| | |
|--|--|
| **Stamp path** | `/workspace/mobbin-sites/teacher-live-q-chrome-ia-v0.md` |
| Eggbot | Implement A/B/C strip · `Save to card` · pre-publish `Show Live Results` · student right-panel persist |
| Wonder | Calm density only; no delight on checkboxes / Publish |
| Shawn | GO this tip pack; Eng bugs above = awareness, not this ship |
| Mobbin | IA only — **no LMS code** |

**Path:** `/workspace/mobbin-sites/teacher-live-q-chrome-ia-v0.md`
