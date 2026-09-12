# MCF3M M1C2 — instant text feedback (soft keys)

Teacher-authored juice for the live student prompt/response shell.  
Runtime table: `lms/live_prompt_feedback.py` (Fly image may omit this folder).  
Local teacher DB only — no cloud sync of student responses.

**Never attach feedback to the Team Challenge stem.**  
Minds-On stays ephemeral / juice-only (`durable_store: false`).

Wonder clarity bar: **one short line**, no pile.  
Text-only Team Challenge — no Real-slice peels / no `active_media_json`.

---

## Minds-On · opening MC · soft key **A**

Waiting-room check-in. Item ids: `minds_on` / `C2-minds_on`.

**Prompt:** Looking at y = x^2, which claim is **forced** by the picture?

| Choice | Line |
|---|---|
| **A** (key) | Good work. The U opens up — so a > 0. |
| B | Not that one — look which way the arms open. |
| C | Curves wait. This picture is still a parabola. |
| D | Hint: which way do the arms open? |

---

## C2-CONS-1 · `h` overclaim · soft key **B**

Light CONS after freeze (3 items, not a C1 5-pack).  
**Prompt:** Is h forced to be 2?

| Choice | Line |
|---|---|
| A | One special case isn’t a law. Must the vertex sit on (2,5)? |
| **B** (key) | Good work. The point ties parameters — it doesn’t freeze h alone. |
| C | Hint: can you hit (2,5) with a vertex somewhere else? |

---

## C2-CONS-2 · share · what (2,5) forces

**Prompt:** In one sentence: what does (2,5) force?

**on_submit:** The point ties a, h, and k — it doesn’t freeze one parameter.

---

## C2-CONS-3 · share · one more equation

**Prompt:** Give one more equation through (2,5) not on your freeze list.

**on_submit:** Another writing through (2,5) shows the family — not a single graph.

---

## Out of scope

- Team Challenge stem / Real-slice media ask
- C1 peels (`reveal_axes` / L-flags / `allow_3d_limited`)
- Completing-the-square / standard↔vertex (park M4)
- Gradebook writeback
