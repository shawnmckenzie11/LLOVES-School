/**
 * Phone-first student live-class home: Live response shell + chrome boards.
 */
const waitEl = document.getElementById("student-wait");
const meEl = document.getElementById("me-board");
const boardEl = document.getElementById("class-board");
const roundBannerEl = document.getElementById("student-round-banner");
const promptShell = document.getElementById("prompt-shell");
const promptAck = document.getElementById("prompt-ack");
const mediaPane = document.getElementById("media-pane");
const mediaFrame = document.getElementById("media-frame");
const mediaStem = document.getElementById("media-stem");
const body = document.body;

/** @type {number | null} */
let lastPromptId = null;
/** @type {string} */
let lastMediaUrl = "";
/** @type {string} */
let lastMediaSig = "";

/** Open Question waiting copy shown on the Phone during that round. */
const OPEN_QUESTION_WAIT_HTML = `
  <div class="student-wait-copy">
    <p>Use this time to ask any questions you have to make sure you're clear on the key ideas and skills from the module. Your questions could be about:</p>
    <ul>
      <li>module lessons you've completed independently</li>
      <li>team challenge questions from past classes</li>
      <li>formatives or my feedback on past questions you've attempted</li>
    </ul>
    <p>Honest, relevant questions about the math or the problem solving process earn points for you — and your team — by showing that you've made an effort to understand. Bonus points for answering peers' questions or asking ones when you don't normally speak up!</p>
  </div>
`;

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
      ? `<p class="me-stat me-rank"><span class="me-stat-label">Rank</span><strong>${me.rank}</strong>${me.rank_of ? ` <span class="me-stat-of">/ ${me.rank_of}</span>` : ""}</p>`
      : "";
  meEl.innerHTML = `
    <p class="me-name">${escapeText(me.codename || "Student")}</p>
    <div class="me-stats">
      <p class="me-stat"><span class="me-stat-label">My points</span><strong>${escapeText(pts(me.points))}</strong></p>
      <p class="me-stat"><span class="me-stat-label">${escapeText(me.team_name || "Team")}</span><strong>${escapeText(pts(me.team_points))}</strong></p>
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
    boardEl.innerHTML = "";
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
 * Show the active round label while scoring and clear it otherwise.
 * @param {any} payload
 */
function paintRoundBanner(payload) {
  if (!roundBannerEl) return;
  const label = payload.scoring ? String(payload.round_label || "").trim() : "";
  roundBannerEl.textContent = label;
  roundBannerEl.hidden = !label;
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
 * Waiting / guidance copy for the live response shell.
 * @param {any} payload
 * @returns {{html?: string, text?: string}}
 */
function waitCopyFor(payload) {
  if (!payload.scoring) {
    return { text: "Waiting for your teacher to start scoring." };
  }
  const kind = String(payload.round_kind || "").toLowerCase();
  if (kind === "break") {
    return { text: "Scoring paused" };
  }
  if (kind === "open") {
    return { html: OPEN_QUESTION_WAIT_HTML };
  }
  return { text: "Waiting for the next question…" };
}

/**
 * Apply live vs waiting layout for the response shell chrome.
 * @param {any} payload
 */
function applyLayout(payload) {
  const live = Boolean(payload.scoring);
  const hasMedia = Boolean(payload.active_media && payload.active_media.url);
  body.classList.toggle("is-live", live);
  body.classList.toggle("has-media", hasMedia);
  const hasPrompt = Boolean(payload.prompt && payload.prompt.kind && payload.prompt.kind !== "idle");
  if (waitEl) {
    if (hasPrompt || hasMedia) {
      waitEl.hidden = true;
      waitEl.textContent = "";
      waitEl.innerHTML = "";
    } else {
      waitEl.hidden = false;
      const copy = waitCopyFor(payload);
      waitEl.classList.remove("is-paused", "is-plain");
      if (copy.html) {
        waitEl.innerHTML = copy.html;
      } else {
        waitEl.textContent = copy.text || "";
        const kind = String(payload.round_kind || "").toLowerCase();
        if (kind === "break") waitEl.classList.add("is-paused");
        else if (payload.scoring) waitEl.classList.add("is-plain");
      }
    }
  }
}

/**
 * Same-origin /static/ path the iframe is allowed to load.
 * @param {unknown} raw
 * @returns {string}
 */
function safeMediaUrl(raw) {
  const text = String(raw || "").trim();
  if (!text.startsWith("/static/")) return "";
  if (text.includes("..")) return "";
  return text;
}

/**
 * Push teacher control-state into the Real-slice iframe without a reload.
 * @param {any} media
 */
function postMediaState(media) {
  if (!mediaFrame || !mediaFrame.contentWindow || !media) return;
  try {
    mediaFrame.contentWindow.postMessage(
      {
        source: "lloves-student-home",
        type: "live-media-state",
        student_controls_unlocked: Boolean(media.student_controls_unlocked),
        params: media.params || { a: 1, b: 0, c: 0 },
        stem: media.stem || "",
        caption: media.caption || "",
      },
      window.location.origin
    );
  } catch (_err) {
    /* keep last frame */
  }
}

/**
 * Show or hide the student iframe pane from /api/student/state.
 * @param {any} payload
 */
function paintMedia(payload) {
  const media = payload.active_media;
  const url = media ? safeMediaUrl(media.url) : "";
  if (mediaStem) {
    const stem = String((media && (media.stem || media.caption)) || "").trim();
    mediaStem.textContent = stem;
    mediaStem.hidden = !stem;
  }
  if (!mediaPane || !mediaFrame) return;
  if (!url) {
    mediaPane.hidden = true;
    mediaFrame.removeAttribute("src");
    lastMediaUrl = "";
    lastMediaSig = "";
    return;
  }
  mediaPane.hidden = false;
  const sig = JSON.stringify({
    url,
    unlocked: Boolean(media.student_controls_unlocked),
    params: media.params || {},
  });
  if (url !== lastMediaUrl) {
    lastMediaUrl = url;
    lastMediaSig = sig;
    mediaFrame.onload = () => postMediaState(media);
    mediaFrame.src = url;
    return;
  }
  if (sig !== lastMediaSig) {
    lastMediaSig = sig;
    postMediaState(media);
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
    paintRoundBanner(data);
    paintMedia(data);
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
