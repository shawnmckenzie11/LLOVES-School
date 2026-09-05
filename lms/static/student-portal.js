/**
 * Phone-first student live-class home: Live response shell + chrome boards.
 */
const waitEl = document.getElementById("student-wait");
const meEl = document.getElementById("me-board");
const boardEl = document.getElementById("class-board");
const promptShell = document.getElementById("prompt-shell");
const promptAck = document.getElementById("prompt-ack");
const body = document.body;

/** @type {number | null} */
let lastPromptId = null;

/**
 * Fetch init with per-tab visit token header when available.
 * @param {RequestInit} [init]
 * @returns {RequestInit}
 */
function visitFetchInit(init) {
  if (typeof window.studentVisitFetchInit === "function") {
    return window.studentVisitFetchInit(init);
  }
  return init || {};
}

/**
 * Set a distinctive tab title for multi-tab testing.
 * @param {string} codename
 */
function setTabTitle(codename) {
  const name = (codename || "").trim();
  if (!name) return;
  document.title = `${name} · Class`;
}

/**
 * Format a points value for the student boards.
 * @param {unknown} value
 * @returns {string}
 */
function pts(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0";
  return String(Math.round(n * 10) / 10);
}

/**
 * Paint private stats (points, team, optional rank).
 * @param {any} payload
 */
function paintMe(payload) {
  if (!meEl) return;
  const me = payload.me || {};
  const rankLine =
    payload.show_rank && me.rank
      ? `<p class="me-stat me-rank">Rank <strong>${me.rank}</strong>${me.rank_of ? ` / ${me.rank_of}` : ""}</p>`
      : "";
  meEl.innerHTML = `
    <p class="me-kicker">You</p>
    <p class="me-name">${escapeText(me.codename || "You")}</p>
    <div class="me-stats">
      <p class="me-stat">My points <strong>${escapeText(pts(me.points))}</strong></p>
      <p class="me-stat">Team ${escapeText(me.team_name || "—")} <strong>${escapeText(pts(me.team_points))}</strong></p>
      ${rankLine}
    </div>
  `;
  setTabTitle(String(me.codename || ""));
}

/**
 * Paint the public team scoreboard strip.
 * @param {any} payload
 */
function paintBoard(payload) {
  if (!boardEl) return;
  const sb = payload.scoreboard || {};
  const teams = sb.teams || [];
  if (!teams.length) {
    boardEl.innerHTML = `<p class="sb-idle">${sb.live ? "Scores coming…" : "Scoreboard idle"}</p>`;
    return;
  }
  boardEl.innerHTML = `
    <p class="sb-kicker">Scores</p>
    <div class="sb-espn-board">
      ${teams
        .map(
          (team) =>
            `<div class="sb-espn" style="--team:${escapeText(team.color || "#0f766e")}">
              <span class="sb-espn-swatch" aria-hidden="true"></span>
              <div class="sb-espn-meta">
                <span class="sb-espn-name">${escapeText(team.name)}</span>
                <span class="sb-espn-score">${escapeText(pts(team.score))}</span>
              </div>
            </div>`
        )
        .join("")}
    </div>`;
}

/**
 * Escape text for HTML.
 * @param {unknown} value
 * @returns {string}
 */
function escapeText(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/**
 * Apply live vs waiting layout for the response shell chrome.
 * @param {any} payload
 */
function applyLayout(payload) {
  const live = Boolean(payload.scoring);
  body.classList.toggle("is-live", live);
  const hasPrompt = Boolean(payload.prompt && payload.prompt.kind && payload.prompt.kind !== "idle");
  if (waitEl) {
    if (hasPrompt) {
      waitEl.hidden = true;
      waitEl.textContent = "";
    } else {
      waitEl.hidden = false;
      waitEl.textContent = live
        ? "Waiting for the next question…"
        : "Waiting for your teacher to start scoring.";
    }
  }
}

/**
 * Render placeholder widgets for mc / numeric / share prompts.
 * @param {any} payload
 */
function paintPrompt(payload) {
  if (!promptShell) return;
  const prompt = payload.prompt;
  const answered = Boolean(payload.my_response);
  if (!prompt || !prompt.kind || prompt.kind === "idle") {
    promptShell.hidden = true;
    promptShell.innerHTML = "";
    lastPromptId = null;
    if (promptAck) promptAck.hidden = true;
    return;
  }
  if (answered) {
    promptShell.hidden = true;
    promptShell.innerHTML = "";
    if (promptAck) {
      promptAck.hidden = false;
      promptAck.textContent = "Response received.";
    }
    lastPromptId = Number(prompt.id);
    return;
  }
  if (promptAck) promptAck.hidden = true;
  const kind = String(prompt.kind);
  const data = prompt.payload || {};
  const title = escapeText(data.prompt || data.question || "Live response");
  let controls = "";
  if (kind === "mc") {
    const choices = Array.isArray(data.choices) ? data.choices : ["A", "B", "C", "D"];
    controls = choices
      .map(
        (choice, index) =>
          `<button type="button" class="prompt-choice" data-choice="${escapeText(choice)}">${escapeText(
            typeof choice === "string" ? choice : `Option ${index + 1}`
          )}</button>`
      )
      .join("");
  } else if (kind === "numeric") {
    controls = `
      <label class="prompt-numeric">
        <span>Your answer</span>
        <input type="number" inputmode="decimal" id="prompt-numeric-input" />
      </label>
      <button type="button" class="prompt-submit" id="prompt-numeric-submit">Submit</button>
    `;
  } else if (kind === "share") {
    controls = `
      <label class="prompt-share">
        <span>Share your work</span>
        <textarea id="prompt-share-input" rows="3" maxlength="2000" placeholder="Type a short note…"></textarea>
      </label>
      <button type="button" class="prompt-submit" id="prompt-share-submit">Share</button>
    `;
  } else {
    controls = `<p class="prompt-idle">Unsupported prompt kind.</p>`;
  }
  promptShell.hidden = false;
  promptShell.innerHTML = `
    <p class="prompt-kind">${escapeText(kind.toUpperCase())} · slide ${escapeText(prompt.slide_index)}</p>
    <h2 class="prompt-title">${title}</h2>
    <div class="prompt-controls" data-prompt-id="${escapeText(prompt.id)}">${controls}</div>
  `;
  lastPromptId = Number(prompt.id);
  wirePromptControls(prompt);
}

/**
 * Bind placeholder submit handlers for the active prompt widgets.
 * @param {any} prompt
 */
function wirePromptControls(prompt) {
  const root = promptShell && promptShell.querySelector(".prompt-controls");
  if (!root) return;
  root.querySelectorAll(".prompt-choice").forEach((btn) => {
    btn.addEventListener("click", () => {
      submitResponse(prompt.id, { choice: btn.getAttribute("data-choice") });
    });
  });
  const numSubmit = root.querySelector("#prompt-numeric-submit");
  if (numSubmit) {
    numSubmit.addEventListener("click", () => {
      const input = root.querySelector("#prompt-numeric-input");
      const raw = input && "value" in input ? String(input.value) : "";
      submitResponse(prompt.id, { value: raw === "" ? null : Number(raw) });
    });
  }
  const shareSubmit = root.querySelector("#prompt-share-submit");
  if (shareSubmit) {
    shareSubmit.addEventListener("click", () => {
      const input = root.querySelector("#prompt-share-input");
      const text = input && "value" in input ? String(input.value) : "";
      submitResponse(prompt.id, { text });
    });
  }
}

/**
 * POST a student response for the active prompt.
 * @param {number} promptId
 * @param {Record<string, unknown>} response
 */
async function submitResponse(promptId, response) {
  try {
    const res = await fetch(
      "/api/student/live-prompt/response",
      visitFetchInit({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ prompt_id: promptId, response }),
      })
    );
    const data = await res.json();
    if (data.redirect) {
      location.href = data.redirect;
      return;
    }
    if (data.ok && data.ack) {
      if (promptShell) {
        promptShell.hidden = true;
        promptShell.innerHTML = "";
      }
      if (promptAck) {
        promptAck.hidden = false;
        promptAck.textContent = "Response received.";
      }
    }
  } catch (_err) {
    /* keep UI; next poll retries */
  }
}

/**
 * Fetch and paint /api/student/state.
 */
async function tick() {
  try {
    const res = await fetch("/api/student/state", visitFetchInit());
    const data = await res.json();
    if (data.redirect && data.redirect !== "/student/home" && !data.me) {
      location.href = data.redirect;
      return;
    }
    applyLayout(data);
    paintMe(data);
    paintBoard(data);
    const promptId = data.prompt && data.prompt.id != null ? Number(data.prompt.id) : null;
    if (promptId !== lastPromptId || (data.my_response && promptShell && !promptShell.hidden)) {
      paintPrompt(data);
    } else if (!data.prompt) {
      paintPrompt(data);
    }
  } catch (_err) {
    /* keep last paint */
  }
}

tick();
setInterval(tick, 4000);

const bootCodename = body && body.dataset ? body.dataset.codename : "";
if (bootCodename) {
  setTabTitle(bootCodename);
}
