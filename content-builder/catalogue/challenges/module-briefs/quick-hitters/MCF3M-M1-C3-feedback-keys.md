# MCF3M M1C3 — instant text feedback (soft keys)

Teacher-authored juice for the live student prompt/response shell.  
Runtime table: `lms/live_prompt_feedback.py` (Fly image may omit this folder).  
Local teacher DB only — no cloud sync of student responses.

**Never attach feedback to the Team Challenge stem.**  
Minds-On stays ephemeral / juice-only (`durable_store: false`).

Wonder clarity bar: **one short line**, no pile.  
Text-only Team Challenge — no Real-slice peels / no `active_media_json`.

---

## Minds-On · free-vs-forced MC · soft key **B**

Waiting-room check-in. Item ids: `minds_on` / `C3-minds_on`.

**Prompt:** A graph of y = x^2 has been moved so it still passes through a marked point. Which claim is safest?

| Choice | Line |
|---|---|
| A | Not that one — one point doesn’t freeze a, h, and k all at once. |
| **B** (key) | Good work. The point links the parameters — some stay free. |
| C | Domain and range aren’t always all real numbers. Context can cut them. |
| D | Hint: does one marked point lock every parameter? |

---

## C3-CONS-1 · wall / ground · soft key **B**

Light CONS after freeze (3 items, not a C1 5-pack). Courtyard lock.  
**Prompt:** At x = 8, is the model above ground?

| Choice | Line |
|---|---|
| A | Not above ground there — the path is already underground at x = 8. |
| **B** (key) | Good work. At the wall the model is not above ground. |
| C | Hint: check a table at x = 8. Is y still at least 0? |

---

## C3-CONS-2 · share · range on the path

**Prompt:** State the range of heights on the physical path.

**on_submit:** Heights on this path run from the ground up to the peak.

---

## C3-CONS-3 · draw/share · x-values that make sense

**Prompt:** Shade the x-values that make sense; one defence sentence.

**on_submit:** Shade the x-values that stay above ground — then defend from the picture.

---

## Out of scope

- Team Challenge stem / Real-slice media ask
- Formula-first zeros as the goal (park M3/M4)
- C1 peels / `active_media_json`
- Gradebook writeback
