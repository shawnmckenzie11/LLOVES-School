/**
 * Phone-first student live-class home: Live response shell + chrome boards.
 */
import { bindWhiteboard } from "/static/live_whiteboard.js";
import { avatarGlyph, nameWithAvatar } from "/static/student_avatars.js";

const waitEl = document.getElementById("student-wait");
const gameShowWelcomeEl = document.getElementById("game-show-welcome");
const promptPollTotals = document.getElementById("prompt-poll-totals");
const displayTimeEl = document.getElementById("me-display-time");
const meEl = document.getElementById("me-board");
const boardEl = document.getElementById("class-board");
const roundBannerEl = document.getElementById("student-round-banner");
const promptShell = document.getElementById("prompt-shell");
const questionFrame = document.getElementById("question-frame");
const promptFeedback = document.getElementById("prompt-feedback");
const promptFeedbackLead = document.getElementById("prompt-feedback-lead");
const promptFeedbackWhy = document.getElementById("prompt-feedback-why");
const promptFeedbackHelper = document.getElementById("prompt-feedback-helper");
const promptFeedbackClose = document.getElementById("prompt-feedback-close");
const promptAck = document.getElementById("prompt-ack");
const mediaPane = document.getElementById("media-pane");
const mediaFrame = document.getElementById("media-frame");
const mediaLock = document.getElementById("media-lock");
const canvasPane = document.getElementById("canvas-pane");
const canvasLock = document.getElementById("canvas-lock");
const studentCanvas = document.getElementById("student-canvas");
const canvasCursors = document.getElementById("canvas-cursors");
const mediaStem = document.getElementById("media-stem");
const mediaChip = document.getElementById("media-chip");
const mediaCaption = document.getElementById("media-caption");
const mediaToast = document.getElementById("media-toast");
const mediaAnswers = document.getElementById("media-answers");
const mediaEncore = document.getElementById("media-encore");
const mediaEncoreLink = document.getElementById("media-encore-link");
const meAvatarEl = document.getElementById("me-avatar");
const meNameEl = document.getElementById("me-name");
const meChipEl = document.getElementById("me-meet-chip");
const mePointsEl = document.getElementById("me-points");
const meTeamLabelEl = document.getElementById("me-team-label");
const meTeamPointsEl = document.getElementById("me-team-points");
const meRankEl = document.getElementById("me-rank");
const meRankValueEl = document.getElementById("me-rank-value");
const meRankOfEl = document.getElementById("me-rank-of");
const saveWorkBtn = document.getElementById("save-work");
const saveWorkToast = document.getElementById("save-work-toast");
const body = document.body;
const SAVE_WORK_OK = "Saved to your downloads.";
/** @type {string} */
let lastWelcomeKey = "";
const SAVE_WORK_EMPTY = "Nothing to save yet.";
/** @type {number} */
let saveWorkToastTimer = 0;

/** @type {number | null} */
let lastPromptId = null;
/** @type {string} */
let lastMediaUrl = "";
/** @type {string} */
let lastMediaSig = "";
/** @type {string} */
let lastToastKey = "";
/** @type {string} */
let lastCueId = "";
/** @type {string} */
let lastMeetSig = "";
/** @type {string} */
let lastFeedbackKey = "";
/** @type {boolean} */
let feedbackDismissed = false;
/** True after a keyed waiting-room submit until the student hits Close. */
let holdJoinFeedback = false;
/** @type {number} */
let lastStateSeq = -1;
/** @type {any} */
let lastStudentPayload = null;
/** @type {string} */
let lastSummarySig = "";
/** @type {number} */
let toastHideTimer = 0;
const MEET_CUES = new Set(["cue.meet_open", "cue.meet_clear"]);
const MEET_CUE_COPY = {
  "cue.meet_open": "Meet your team",
  "cue.meet_clear": "Meet cleared",
};
const TEAMS_SPARK_CUES = new Set(["cue.teams_spark"]);
const TEAMS_SPARK_CUE_COPY = {
  "cue.teams_spark": "Shared spark",
};
const TEXT_RIDE_CUES = new Set(["cue.freeze", "cue.cons_unlock"]);
const TEXT_RIDE_CUE_COPY = {
  "cue.freeze": "Pause exploring. Answer from what you already see.",
  "cue.cons_unlock": "Now say what this graph shows must be true.",
};

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
 * Format seconds as m:ss for the reserved display-time slot.
 * @param {unknown} value
 * @returns {string}
 */
function formatDisplayClock(value) {
  const n = Math.max(0, Math.floor(Number(value) || 0));
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;
}

/** Teacher SessionTimer deadline (epoch ms), or 0 when idle/paused. */
let displayEndsAtMs = 0;

/**
 * Paint the reserved display-time slot above the student name.
 * Text only — never remounts chrome or toggles hidden.
 * @param {any} payload
 */
function paintDisplayTime(payload) {
  if (!displayTimeEl) return;
  const dt = payload && payload.display_time ? payload.display_time : {};
  if (dt.running && dt.ends_at_ms) {
    displayEndsAtMs = Number(dt.ends_at_ms) || 0;
    const rem = displayEndsAtMs
      ? Math.max(0, Math.ceil((displayEndsAtMs - Date.now()) / 1000))
      : Number(dt.remaining_sec) || 0;
    displayTimeEl.textContent = formatDisplayClock(rem);
    displayTimeEl.dataset.state = "running";
    return;
  }
  displayEndsAtMs = 0;
  if (dt.paused) {
    displayTimeEl.textContent = formatDisplayClock(dt.remaining_sec);
    displayTimeEl.dataset.state = "paused";
    return;
  }
  displayTimeEl.textContent = dt.label || "—";
  displayTimeEl.dataset.state = "idle";
}

/**
 * Tick the reserved display-time slot while the teacher timer is running.
 */
function tickDisplayTime() {
  if (!displayTimeEl || displayTimeEl.dataset.state !== "running") return;
  if (!displayEndsAtMs) return;
  displayTimeEl.textContent = formatDisplayClock(
    Math.max(0, Math.ceil((displayEndsAtMs - Date.now()) / 1000))
  );
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
 * Updates identity + stats nodes only — never remounts Save View.
 * @param {any} payload
 */
function paintMe(payload) {
  if (!meEl) return;
  const me = payload.me || {};
  if (meAvatarEl) {
    const face = avatarGlyph(me.character);
    meAvatarEl.textContent = face;
    meAvatarEl.hidden = !face;
  }
  if (meNameEl) meNameEl.textContent = String(me.codename || "Student");
  const chip = String(payload.meet_chip || "").trim();
  if (meChipEl) {
    meChipEl.textContent = chip;
    meChipEl.hidden = !chip;
    if (chip) meChipEl.title = "Meet window only";
    else meChipEl.removeAttribute("title");
  }
  if (mePointsEl) mePointsEl.textContent = pts(me.points);
  if (meTeamLabelEl) meTeamLabelEl.textContent = String(me.team_name || "Team");
  if (meTeamPointsEl) meTeamPointsEl.textContent = pts(me.team_points);
  if (meRankEl) meRankEl.hidden = true;
  setTabTitle(String(me.codename || ""));
}

/**
 * Paint this student's team under the name/points/Save View card.
 * Names and avatars only — never scores or moods.
 * @param {any} payload
 */
function paintMyTeam(payload) {
  const list = document.getElementById("me-team-list");
  if (!list) return;
  const team = payload && payload.my_team;
  const members = team && Array.isArray(team.members) ? team.members : [];
  if (!members.length) {
    list.innerHTML = "";
    list.hidden = true;
    return;
  }
  const title = escapeText(team.name || "Your team");
  list.hidden = false;
  list.innerHTML = `<p class="me-team-kicker">${title}</p><ul class="me-team-people">${members
    .map((row) => {
      const name = row.codename || row.first_name || "Student";
      return `<li>${nameWithAvatar(name, row.character)}</li>`;
    })
    .join("")}</ul>`;
}

/**
 * Show a short Save View status under the name row.
 * @param {string} text
 */
function showSaveWorkToast(text) {
  if (!saveWorkToast) return;
  saveWorkToast.textContent = text;
  saveWorkToast.hidden = false;
  if (saveWorkToastTimer) window.clearTimeout(saveWorkToastTimer);
  saveWorkToastTimer = window.setTimeout(() => {
    saveWorkToast.hidden = true;
  }, 2400);
}

/**
 * True when a live pane is mounted (not hidden / unchecked).
 * @param {HTMLElement | null} pane
 * @returns {boolean}
 */
function paneIsMounted(pane) {
  return Boolean(pane && !pane.hidden);
}

/**
 * Snapshot a canvas as a PNG data URL, or null.
 * @param {HTMLCanvasElement | null} canvas
 * @returns {string | null}
 */
function canvasPngDataUrl(canvas) {
  // Iframe canvases fail `instanceof HTMLCanvasElement` in the parent window.
  if (!canvas || canvas.nodeName !== "CANVAS" || !canvas.width || !canvas.height) {
    return null;
  }
  try {
    return canvas.toDataURL("image/png");
  } catch (_err) {
    return null;
  }
}

/**
 * Capture the first drawable canvas inside a same-origin media iframe.
 * @param {HTMLIFrameElement | null} iframe
 * @returns {{dataUrl: string, width: number, height: number} | null}
 */
function captureMediaFrame(iframe) {
  if (!(iframe instanceof HTMLIFrameElement) || !iframe.src) return null;
  try {
    const doc = iframe.contentDocument;
    if (!doc) return null;
    const canvases = Array.from(doc.querySelectorAll("canvas"));
    for (const canvas of canvases) {
      const dataUrl = canvasPngDataUrl(canvas);
      if (dataUrl) {
        return { dataUrl, width: canvas.width, height: canvas.height };
      }
    }
  } catch (_err) {
    return null;
  }
  return null;
}

/**
 * Load a data URL into an Image.
 * @param {string} dataUrl
 * @returns {Promise<HTMLImageElement>}
 */
function loadPngImage(dataUrl) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("png"));
    img.src = dataUrl;
  });
}

/**
 * Download a PNG data URL to the student device.
 * @param {string} dataUrl
 * @param {string} filename
 */
function downloadPng(dataUrl, filename) {
  const link = document.createElement("a");
  link.href = dataUrl;
  link.download = filename;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

/**
 * Collect same-origin stylesheet text for an SVG foreignObject snapshot.
 * @returns {string}
 */
function pageCssText() {
  let css = "";
  for (const sheet of document.styleSheets) {
    try {
      for (const rule of sheet.cssRules) css += `${rule.cssText}\n`;
    } catch (_err) {
      /* cross-origin sheets stay out */
    }
  }
  return css;
}

/**
 * Screenshot the student view and blit same-origin media / whiteboard canvases.
 * @returns {Promise<string>}
 */
async function captureEntireStudentView() {
  const root = document.querySelector(".student-home") || document.body;
  const width = Math.max(root.scrollWidth, window.innerWidth, 1);
  const height = Math.max(root.scrollHeight, window.innerHeight, 1);
  const layers = collectViewCanvases(root);
  const clone = root.cloneNode(true);
  if (clone instanceof HTMLElement) {
    clone.querySelectorAll("script, iframe").forEach((el) => el.remove());
    clone.style.width = `${width}px`;
    clone.style.minHeight = `${height}px`;
  }
  const wrap = document.createElement("div");
  wrap.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
  const style = document.createElement("style");
  style.textContent = pageCssText();
  wrap.appendChild(style);
  wrap.appendChild(clone);
  const serialized = new XMLSerializer().serializeToString(wrap);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><foreignObject width="100%" height="100%">${serialized}</foreignObject></svg>`;
  const url = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  const img = await loadPngImage(url);
  const out = document.createElement("canvas");
  out.width = width;
  out.height = height;
  const ctx = out.getContext("2d");
  if (!ctx) throw new Error("png");
  ctx.fillStyle = "#0b1020";
  ctx.fillRect(0, 0, width, height);
  ctx.drawImage(img, 0, 0, width, height);
  for (const layer of layers) {
    try {
      const shot = await loadPngImage(layer.dataUrl);
      ctx.drawImage(shot, layer.x, layer.y, layer.w, layer.h);
    } catch (_err) {
      /* skip a tainted layer */
    }
  }
  return out.toDataURL("image/png");
}

/**
 * Media iframe + student whiteboard canvases, positioned in page pixels.
 * @param {Element} root
 * @returns {Array<{dataUrl: string, x: number, y: number, w: number, h: number}>}
 */
function collectViewCanvases(root) {
  const rootRect = root.getBoundingClientRect();
  const layers = [];
  const push = (dataUrl, el, fallbackW, fallbackH) => {
    if (!dataUrl || !(el instanceof Element)) return;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return;
    layers.push({
      dataUrl,
      x: rect.left - rootRect.left + root.scrollLeft,
      y: rect.top - rootRect.top + root.scrollTop,
      w: rect.width,
      h: rect.height || fallbackH || rect.width,
    });
  };
  if (paneIsMounted(mediaPane)) {
    const shot = captureMediaFrame(mediaFrame);
    if (shot) push(shot.dataUrl, mediaFrame, shot.width, shot.height);
  }
  if (paneIsMounted(canvasPane) && studentCanvas instanceof HTMLCanvasElement) {
    push(canvasPngDataUrl(studentCanvas), studentCanvas, studentCanvas.width, studentCanvas.height);
  }
  return layers;
}

/**
 * Save a PNG of the student view, including live media. No gradebook write.
 */
async function saveStudentWork() {
  try {
    const dataUrl = await captureEntireStudentView();
    downloadPng(dataUrl, "live-class-view.png");
    showSaveWorkToast(SAVE_WORK_OK);
  } catch (_err) {
    showSaveWorkToast(SAVE_WORK_EMPTY);
  }
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
 * Escape prompt copy and render copywriter ``**bold**`` markers.
 * @param {unknown} value
 * @returns {string}
 */
function formatPromptHtml(value) {
  return escapeText(value).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

/** Wonder waiting-room line (pre–Generate teams / pre–Team Challenge). */
const WAITING_ROOM_WAIT_LINE = "Waiting room — class is about to begin.";

/**
 * True when the session is still waiting-room (no scoring, no challenge media).
 * @param {any} payload
 * @returns {boolean}
 */
/**
 * Student frame projection from LiveTeacherState.
 * @param {any} payload
 * @returns {{stage: string, seq: number, questions: boolean, media: boolean, canvas: boolean, unlockMedia: boolean, unlockCanvas: boolean}}
 */
function studentProjection(payload) {
  const ts = (payload && payload.teacher_state) || {};
  const view = ts.student_view || {};
  const frames = ts.student_frames || {};
  const unlocks = ts.unlocks || {};
  const stage = String(ts.stage || "join");
  const seq = Number(ts.state_seq);
  const questionsMode = String(
    view.questions || payload.question_view || (frames.questions === false ? "none" : "student")
  );
  const mediaMode = String(view.media || (unlocks.media ? "student" : "none"));
  const canvasMode = String(view.canvas || (unlocks.canvas ? "student" : "none"));
  const canvasAlign =
    canvasMode === "team" ? "team" : canvasMode === "student" ? "student" : "teacher";
  return {
    stage,
    seq: Number.isFinite(seq) ? seq : 0,
    questions: questionsMode !== "none",
    media: mediaMode !== "none" && stage !== "round",
    canvas: canvasMode !== "none" && stage !== "round",
    unlockMedia: mediaMode !== "none",
    unlockCanvas: canvasMode !== "none",
    canvasAlign,
    questionsMode,
    mediaMode,
    canvasMode,
  };
}

/**
 * Unmount the student media iframe (no opacity flicker).
 */
function unmountStudentMedia() {
  if (!mediaFrame) return;
  mediaFrame.removeAttribute("src");
  lastMediaUrl = "";
  lastMediaSig = "";
}

/**
 * Apply student_frames / unlocks when state_seq changes or on first paint.
 * @param {any} payload
 * @returns {{media: boolean, canvas: boolean, unlockMedia: boolean}}
 */
/**
 * Draw remote strokes from the thin canvas-sync view.
 * @param {any} view
 */
function paintRemoteCanvas(view) {
  if (!(studentCanvas instanceof HTMLCanvasElement)) return;
  const ctx = studentCanvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, studentCanvas.width, studentCanvas.height);
  const strokes = Array.isArray(view?.strokes) ? view.strokes : [];
  strokes.forEach((stroke) => {
    const points = Array.isArray(stroke.points) ? stroke.points : [];
    if (!points.length) return;
    ctx.beginPath();
    points.forEach((pt, i) => {
      const x = Number(pt[0]) * studentCanvas.width;
      const y = Number(pt[1]) * studentCanvas.height;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = String(stroke.color || "#12202e");
    ctx.lineWidth = 2;
    ctx.stroke();
  });
  if (!(canvasCursors instanceof HTMLElement)) return;
  const cursors = Array.isArray(view?.cursors) ? view.cursors : [];
  canvasCursors.innerHTML = cursors
    .map((row) => {
      const name = String(row.name || row.owner || "").replace(/[<>&]/g, "");
      const color = String(row.color || "#0b3d91");
      const left = Math.round(Number(row.x) * 1000) / 10;
      const top = Math.round(Number(row.y) * 1000) / 10;
      return `<span class="canvas-cursor-chip" style="left:${left}%;top:${top}%;color:${color}">${name}</span>`;
    })
    .join("");
}

/**
 * Bind unique-per-student local drawing and team/teacher presence posts.
 */
function bindStudentCanvas() {
  if (!(studentCanvas instanceof HTMLCanvasElement)) return;
  let lastAlign = "student";
  const board = bindWhiteboard(studentCanvas, {
    undoBtn: document.getElementById("student-canvas-undo"),
    redoBtn: document.getElementById("student-canvas-redo"),
    eraseBtn: document.getElementById("student-canvas-erase"),
    canDraw: () => lastAlign !== "teacher" && !(canvasLock && !canvasLock.hidden),
    onPoint: (p, ended, strokeId) => {
      if (lastAlign === "student" || lastAlign === "teacher") return;
      fetch(
        "/api/student/canvas-presence",
        visitFetchInit({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({
            x: p.x / studentCanvas.width,
            y: p.y / studentCanvas.height,
            point: [p.x / studentCanvas.width, p.y / studentCanvas.height],
            stroke_id: strokeId || undefined,
            ended: Boolean(ended),
          }),
        })
      ).catch(() => {});
    },
  });
  bindStudentCanvas.setAlign = (align) => {
    lastAlign = String(align || "student");
    board.setEnabled(lastAlign !== "teacher");
  };
}

/**
 * Paint student canvas from teacher unlocks + canvas_sync view.
 * @param {any} payload
 */
function paintStudentCanvas(payload) {
  const proj = studentProjection(payload);
  if (typeof bindStudentCanvas.setAlign === "function") {
    bindStudentCanvas.setAlign(proj.canvasAlign);
  }
  if (!proj.canvas || !proj.unlockCanvas) return;
  if (proj.canvasAlign === "student") return;
  paintRemoteCanvas(payload.canvas_sync || payload.canvas_view || {});
}

function applyTeacherProjection(payload) {
  const proj = studentProjection(payload);
  const welcomeOn = hasGameShowWelcome(payload);
  if (proj.seq !== lastStateSeq) {
    lastStateSeq = proj.seq;
  }
  if (questionFrame) {
    questionFrame.hidden = !proj.questions;
  }
  if (welcomeOn) {
    if (mediaPane) {
      mediaPane.hidden = true;
      unmountStudentMedia();
    }
    if (canvasPane) canvasPane.hidden = true;
  }
  if (canvasPane) {
    canvasPane.hidden = !proj.canvas;
    canvasPane.classList.toggle("is-readonly", proj.canvas && proj.canvasAlign === "teacher");
    if (canvasLock) canvasLock.hidden = true;
  }
  if (mediaPane) {
    mediaPane.classList.remove("is-locked");
    if (mediaLock) mediaLock.hidden = true;
    if (!proj.media) {
      mediaPane.hidden = true;
      unmountStudentMedia();
    }
  }
  return proj;
}

function isWaitingRoom(payload) {
  if (typeof payload.waiting_room === "boolean") {
    return payload.waiting_room;
  }
  const proj = studentProjection(payload);
  const hasMedia = proj.media && Boolean(payload.active_media && payload.active_media.url);
  return !payload.scoring && !hasMedia;
}

/**
 * Waiting / guidance copy for the live response shell.
 * @param {any} payload
 * @returns {{html?: string, text?: string}}
 */
function waitCopyFor(payload) {
  if (isWaitingRoom(payload)) {
    return { text: WAITING_ROOM_WAIT_LINE };
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
  const live = Boolean(payload.scoring) && !payload.celebrate;
  const proj = studentProjection(payload);
  const hasMedia = proj.media && Boolean(payload.active_media && payload.active_media.url);
  const waitingRoom = isWaitingRoom(payload);
  const welcomeOn = Boolean(payload.game_show_welcome);
  const celebrating = Boolean(payload.celebrate);
  const ts = (payload && payload.teacher_state) || {};
  const meetOn = proj.stage === "meet" && Boolean(ts.meet_chain);
  body.classList.toggle("is-live", live);
  body.classList.toggle("has-media", hasMedia);
  body.classList.toggle("is-waiting-room", waitingRoom && !welcomeOn);
  body.classList.toggle("is-game-show-welcome", welcomeOn);
  const hasPrompt = Boolean(payload.prompt && payload.prompt.kind && payload.prompt.kind !== "idle");
  if (waitEl) {
    // Waiting-room keeps Wonder's line even when the Minds-On question is showing.
    // MEET hides leftover scoring-wait chrome — the Question frame holds the chain.
    // TEAMS swaps the whole live-response face for the VLC welcome card.
    if (celebrating || welcomeOn || (!waitingRoom && (hasPrompt || hasMedia)) || meetOn) {
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
        param_push: media.param_push || { a: false, b: false, c: false },
        param_frozen: media.param_frozen || { a: true, b: true, c: true },
        reveal_axes: Boolean(media.reveal_axes),
        reveal_lateral: Boolean(media.reveal_lateral),
        allow_3d_limited: Boolean(media.allow_3d_limited),
        show_z_axis: Boolean(media.show_z_axis),
        student_zoom: Number(media.student_zoom ?? 0),
        freeze_zoom: Boolean(media.freeze_zoom),
        surface_transparency: Number(media.surface_transparency ?? 0.75),
        freeze_surface: Boolean(media.freeze_surface),
        student_yaw_range: Number(media.student_yaw_range ?? 0),
        freeze_yaw: Boolean(media.freeze_yaw),
        frozen: Boolean(media.frozen),
        unlock_flags: media.unlock_flags || {},
        params: media.params || { a: 1, b: 0, c: 0 },
        stem: media.stem || "",
        caption: media.caption || "",
        entry_chip: media.chip || media.entry_chip || "",
        answers: media.answers || [],
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
  if (payload.game_show_welcome) {
    applyTeacherProjection(payload);
    if (mediaChip) mediaChip.hidden = true;
    if (mediaStem) mediaStem.hidden = true;
    if (mediaCaption) mediaCaption.hidden = true;
    if (mediaAnswers) mediaAnswers.hidden = true;
    if (mediaEncore) mediaEncore.hidden = true;
    return;
  }
  const proj = applyTeacherProjection(payload);
  if (!proj.media) {
    if (mediaChip) mediaChip.hidden = true;
    if (mediaStem) mediaStem.hidden = true;
    if (mediaCaption) mediaCaption.hidden = true;
    if (mediaAnswers) mediaAnswers.hidden = true;
    if (mediaEncore) mediaEncore.hidden = true;
    return;
  }
  const media = payload.active_media;
  const url = media ? safeMediaUrl(media.url) : "";
  if (mediaChip) {
    const chip = String(
      (media && (media.chip || media.entry_chip)) || ""
    ).trim();
    mediaChip.textContent = chip;
    mediaChip.hidden = !chip;
  }
  if (mediaStem) {
    const stem = String((media && media.stem) || "").trim();
    mediaStem.textContent = stem;
    mediaStem.hidden = !stem;
  }
  if (mediaCaption) {
    const caption = String((media && media.caption) || "").trim();
    mediaCaption.textContent = caption;
    mediaCaption.hidden = !caption;
  }
  paintMediaToast(media);
  if (mediaAnswers) {
    const answers = Array.isArray(media && media.answers) ? media.answers : [];
    mediaAnswers.innerHTML = "";
    if (!answers.length) {
      mediaAnswers.hidden = true;
    } else {
      mediaAnswers.hidden = false;
      answers.forEach((item) => {
        const li = document.createElement("li");
        li.textContent = String(item);
        mediaAnswers.appendChild(li);
      });
    }
  }
  if (mediaEncore && mediaEncoreLink) {
    const frozen = Boolean(media && media.frozen);
    const encoreUrl = String((media && media.encore_url) || "").trim();
    const encoreLabel = String((media && media.encore_label) || "").trim();
    const youtubeOk =
      encoreUrl.startsWith("https://www.youtube.com/watch?") ||
      encoreUrl.startsWith("https://youtu.be/");
    if (frozen && youtubeOk && encoreLabel) {
      mediaEncoreLink.href = encoreUrl;
      mediaEncoreLink.textContent = encoreLabel;
      mediaEncore.hidden = false;
    } else {
      mediaEncore.hidden = true;
      mediaEncoreLink.removeAttribute("href");
      mediaEncoreLink.textContent = "";
    }
  }
  if (!mediaPane || !mediaFrame) return;
  if (!url) {
    mediaPane.hidden = true;
    mediaFrame.removeAttribute("src");
    lastMediaUrl = "";
    lastMediaSig = "";
    lastToastKey = "";
    if (mediaToast) {
      mediaToast.hidden = true;
      mediaToast.textContent = "";
    }
    return;
  }
  mediaPane.hidden = false;
  const sig = JSON.stringify({
    url,
    param_push: media.param_push || { a: false, b: false, c: false },
    param_frozen: media.param_frozen || { a: true, b: true, c: true },
    unlocked: Boolean(media.student_controls_unlocked),
    reveal_axes: Boolean(media.reveal_axes),
    reveal_lateral: Boolean(media.reveal_lateral),
    allow_3d_limited: Boolean(media.allow_3d_limited),
    show_z_axis: Boolean(media.show_z_axis),
    student_zoom: Number(media.student_zoom ?? 0),
    freeze_zoom: Boolean(media.freeze_zoom),
    surface_transparency: Number(media.surface_transparency ?? 0.75),
    freeze_surface: Boolean(media.freeze_surface),
    student_yaw_range: Number(media.student_yaw_range ?? 0),
    freeze_yaw: Boolean(media.freeze_yaw),
    frozen: Boolean(media.frozen),
    unlock_flags: media.unlock_flags || {},
    answers: media.answers || [],
    params: media.params || {},
    entry_chip: media.entry_chip || "",
    chip: media.chip || "",
    caption: media.caption || "",
    toast_key: media.toast_key || "",
    cons_item: media.cons_item || "",
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
 * Show an ephemeral Wonder toast when peel identity changes.
 * @param {any} media
 */
function paintMediaToast(media) {
  if (!mediaToast) return;
  const key = String((media && media.toast_key) || "");
  const line = String((media && media.toast) || "").trim();
  if (!key || key === lastToastKey) {
    return;
  }
  lastToastKey = key;
  window.clearTimeout(toastHideTimer);
  if (!line) {
    mediaToast.hidden = true;
    mediaToast.textContent = "";
    return;
  }
  mediaToast.textContent = line;
  mediaToast.hidden = false;
  toastHideTimer = window.setTimeout(() => {
    mediaToast.hidden = true;
  }, 4200);
}

/**
 * One-beat Wonder cue on MEET enter/exit only. No meet_a / meet_c / meet_b.
 * @param {any} payload
 */
function paintMeetCue(payload) {
  if (!mediaToast) return;
  const ts = (payload && payload.teacher_state) || {};
  const ride = ts.text_ride || {};
  const rideKey = String(ride.toast_key || "").trim();
  const rideLine = String(ride.toast || "").trim();
  if (rideKey && rideKey !== lastToastKey && rideLine) {
    lastToastKey = rideKey;
    window.clearTimeout(toastHideTimer);
    mediaToast.textContent = rideLine;
    mediaToast.hidden = false;
    toastHideTimer = window.setTimeout(() => {
      mediaToast.hidden = true;
    }, 4200);
  }
  const cue = String(ts.cue_id || "").trim();
  if (!cue || cue === lastCueId) return;
  lastCueId = cue;
  const line = MEET_CUE_COPY[cue] || TEXT_RIDE_CUE_COPY[cue] || TEAMS_SPARK_CUE_COPY[cue] || rideLine;
  if (!MEET_CUES.has(cue) && !TEXT_RIDE_CUES.has(cue)) return;
  window.clearTimeout(toastHideTimer);
  if (!line) {
    mediaToast.hidden = true;
    mediaToast.textContent = "";
    return;
  }
  mediaToast.textContent = line;
  mediaToast.hidden = false;
  toastHideTimer = window.setTimeout(() => {
    mediaToast.hidden = true;
  }, MEET_CUES.has(cue) ? 2200 : 4200);
}

/**
 * Shared class MC tally when the teacher has Revealed to students.
 * @param {any} payload
 * @returns {any | null}
 */
function studentMcSummary(payload) {
  const tally = payload && payload.mc_tally;
  const ui = ((payload && payload.teacher_state) || {}).mc_ui || {};
  if (!tally || !Array.isArray(tally.choices) || !tally.choices.length) return null;
  if (payload.my_response) return tally;
  if (!ui.reveal || !ui.reveal_to_students) return null;
  return tally;
}

/**
 * True when JOIN Reveal (or an explicit flag) has closed the poll.
 * @param {any} payload
 * @returns {boolean}
 */
function studentPollClosed(payload) {
  if (Boolean(payload && payload.poll_closed)) return true;
  const ui = ((payload && payload.teacher_state) || {}).mc_ui || {};
  return Boolean(ui.poll_closed);
}

/**
 * Bind key so JOIN Reveal re-paints bars without remounting chrome.
 * @param {any} payload
 * @returns {string}
 */
function studentSummarySig(payload) {
  const tally = studentMcSummary(payload);
  if (!tally) return studentPollClosed(payload) ? "closed" : "";
  return [
    tally.prompt_ref || "",
    tally.response_seq ?? "",
    tally.response_count ?? "",
    "reveal",
  ].join("|");
}

/**
 * Bars / % / labels — same optimal MC distribution as the teacher Reveal slot.
 * @param {any} tally
 * @returns {string}
 */
function mcRevealBarsHtml(tally) {
  const rows = (tally && tally.choices) || [];
  return `<div class="mc-reveal-bars" id="student-mc-reveal-bars">${rows
    .map((row) => {
      const pct = Math.max(0, Math.min(100, Number(row.pct) || 0));
      const label = escapeText(row.label || "");
      const id = escapeText(row.id || "");
      const correct = row.correct ? " is-correct" : "";
      const mark = row.correct ? '<span class="mc-reveal-correct">Correct</span>' : "";
      return `<div class="mc-reveal-row${correct}"><span class="mc-reveal-letter">${id}</span><p class="mc-reveal-label">${label}</p><span class="mc-reveal-meta">${escapeText(
        String(row.count ?? 0)
      )} · ${pct}%${mark}</span><span class="mc-reveal-track"><span class="mc-reveal-fill" style="width:${pct}%"></span></span></div>`;
    })
    .join("")}</div>`;
}

/**
 * True when the student Question face is still the JOIN Minds-On MC.
 * @param {any} payload
 * @returns {boolean}
 */
function isJoinMindsOnPrompt(payload) {
  const data = (payload && payload.prompt && payload.prompt.payload) || {};
  return String(data.ride || "") === "minds_on" || String(data.item_id || "") === "minds_on";
}

/**
 * True when the student Question face is the TEAMS shared spark.
 * @param {any} payload
 * @returns {boolean}
 */
function isTeamsSparkPrompt(payload) {
  const data =
    (payload && payload.prompt && payload.prompt.payload) ||
    (payload && payload.payload) ||
    {};
  return (
    String(data.source || "") === "teams_spark" ||
    String(data.item_id || "") === "teams-spark" ||
    String(data.pack || "") === "teams-spark"
  );
}

/**
 * Render placeholder widgets for mc / numeric / share prompts.
 * Waiting-room Minds-On paints the single MC on payload.prompt / choices.
 * JOIN→TEAMS unbinds Minds-On and binds the shared spark instead.
 * @param {any} payload
 */
/**
 * Hide the submitted question stem and controls.
 */
function hideQuestionBody() {
  if (!promptShell) return;
  promptShell.hidden = true;
  promptShell.innerHTML = "";
}

/**
 * Show class poll totals only when the teacher Questions dropdown is on.
 * @param {any} payload
 */
function paintPollIfQuestionsVisible(payload) {
  if (studentProjection(payload).questions) {
    paintPollTotalsBelowFeedback(payload);
    return;
  }
  if (promptPollTotals) {
    promptPollTotals.hidden = true;
    promptPollTotals.innerHTML = "";
  }
}

function paintPrompt(payload) {
  if (!promptShell) return;
  const welcomeOnly =
    Boolean(payload.game_show_welcome) &&
    !(payload.prompt && payload.prompt.kind && payload.prompt.kind !== "idle");
  if (welcomeOnly) {
    promptShell.hidden = true;
    promptShell.innerHTML = "";
    lastPromptId = null;
    lastMeetSig = "";
    lastFeedbackKey = "";
    lastSummarySig = "";
    feedbackDismissed = false;
    holdJoinFeedback = false;
    hideFeedbackPanel();
    if (promptPollTotals) {
      promptPollTotals.hidden = true;
      promptPollTotals.innerHTML = "";
    }
    hidePromptAck();
    return;
  }
  const prompt = payload.prompt;
  const data = (prompt && prompt.payload) || {};
  const isMeet = String(data.ride || "") === "meet_team" || String(data.pack || "") === "meet-team";
  const isSpark = isTeamsSparkPrompt(payload);
  const answered = Boolean(payload.my_response);
  if (holdJoinFeedback && !isJoinMindsOnPrompt(payload)) {
    return;
  }
  const summary = studentMcSummary(payload);
  if (summary && prompt && prompt.kind && prompt.kind !== "idle" && !answered) {
    hideFeedbackPanel();
    if (promptAck) {
      promptAck.hidden = true;
      promptAck.classList.remove("is-feedback");
    }
    renderPromptBody(prompt, data, payload, true);
    lastFeedbackKey = "";
    lastSummarySig = studentSummarySig(payload);
    return;
  }
  if (!prompt || !prompt.kind || prompt.kind === "idle") {
    promptShell.hidden = true;
    promptShell.innerHTML = "";
    lastPromptId = null;
    lastMeetSig = "";
    lastFeedbackKey = "";
    lastSummarySig = "";
    feedbackDismissed = false;
    holdJoinFeedback = false;
    hideFeedbackPanel();
    if (promptPollTotals) {
      promptPollTotals.hidden = true;
      promptPollTotals.innerHTML = "";
    }
    if (promptAck) {
      promptAck.hidden = true;
      promptAck.classList.remove("is-feedback");
    }
    return;
  }
  if (answered) {
    const fb = !isMeet && !isSpark ? feedbackObject(payload.my_response) : null;
    const key = `${prompt.id}:${(fb && fb.lead) || ""}:${(fb && fb.text) || ""}`;
    hideQuestionBody();
    hidePromptAck();
    lastPromptId = Number(prompt.id);
    lastMeetSig = `${prompt.id}:${data.step || ""}:${data.chain_index || ""}`;
    lastSummarySig = studentSummarySig(payload);
    if (fb && !feedbackDismissed) {
      if (key !== lastFeedbackKey || (promptFeedback && promptFeedback.hidden)) {
        showFeedbackPanel(fb);
        lastFeedbackKey = key;
      }
      paintPollIfQuestionsVisible(payload);
      return;
    }
    hideFeedbackPanel();
    lastFeedbackKey = key;
    paintPollIfQuestionsVisible(payload);
    return;
  }
  hideFeedbackPanel();
  lastFeedbackKey = "";
  feedbackDismissed = false;
  holdJoinFeedback = false;
  hidePromptAck();
  renderPromptBody(prompt, data, payload, false);
  if (promptPollTotals) {
    promptPollTotals.hidden = true;
    promptPollTotals.innerHTML = "";
  }
}

/**
 * Paint the Question-frame stem + controls without remounting chrome.
 * @param {any} prompt
 * @param {any} data
 * @param {any} payload
 * @param {boolean} lockChoices
 */
function renderPromptBody(prompt, data, payload, lockChoices) {
  const kind = String(prompt.kind);
  const title = formatPromptHtml(data.prompt || data.question || "Live response");
  const picked = String(
    (payload.my_response && payload.my_response.response && payload.my_response.response.choice) ||
      (payload.group_draft && payload.group_draft.choice) ||
      ""
  ).trim();
  let controls = "";
  const isMeet = String(data.ride || "") === "meet_team" || String(data.pack || "") === "meet-team";
  const groupQuestion = String(payload.question_view || "") === "team";
  const groupNote = groupQuestion
    ? `<p class="prompt-group-note">Note: This is a one-response-per-group question. Make sure you discuss before clicking Submit Answer.</p>`
    : "";
  if (kind === "mc") {
    const summary = studentMcSummary(payload);
    const closed = studentPollClosed(payload) || lockChoices;
    if (summary && !picked && lockChoices) {
      controls = mcRevealBarsHtml(summary);
    } else {
      const choices = Array.isArray(data.choices) ? data.choices : ["A", "B", "C", "D"];
      controls = choices
        .map((choice, index) => {
          const label = typeof choice === "string" ? choice : `Option ${index + 1}`;
          const on = picked && label === picked ? " is-selected" : "";
          return `<button type="button" class="prompt-choice${on}" data-choice="${escapeText(choice)}"${
            picked && lockChoices ? " disabled" : ""
          }>${escapeText(label)}</button>`;
        })
        .join("");
      if (!lockChoices && !closed) {
        controls += `<button type="button" class="prompt-submit" id="prompt-mc-submit">Submit Answer</button>`;
      }
    }
    const sparkLine = String(data.student_feedback_after_reveal || "").trim();
    if (sparkLine && isTeamsSparkPrompt({ prompt })) {
      controls += `<p class="prompt-spark-feedback">${escapeText(sparkLine)}</p>`;
    }
  } else if (kind === "numeric") {
    const integerOnly = Boolean(data.integer_only) || isTeamsSparkPrompt({ prompt });
    const prior = payload.my_response && payload.my_response.response;
    const priorValue =
      prior && prior.value != null
        ? String(prior.value)
        : prior && prior.choice != null
          ? String(prior.choice)
          : "";
    const numericPlaceholder = escapeText(
      data.placeholder || (integerOnly ? "Enter an integer…" : "Enter a number")
    );
    controls = `
      <label class="prompt-numeric">
        <span>${integerOnly ? "Enter an integer…" : "Your answer"}</span>
        <input type="number" inputmode="${integerOnly ? "numeric" : "decimal"}" step="${
          integerOnly ? "1" : "any"
        }" id="prompt-numeric-input" placeholder="${numericPlaceholder}" ${lockChoices ? "disabled" : ""} value="${escapeText(priorValue)}" />
      </label>
      <button type="button" class="prompt-submit" id="prompt-numeric-submit"${
        lockChoices ? " disabled" : ""
      }>Submit Answer</button>
    `;
  } else if (kind === "share" || kind === "draw") {
    const isTeamChallenge =
      data.ride === "action" || data.item_id === "team-challenge" || data.pack === "team-challenge";
    if (isTeamChallenge) {
      controls = "";
    } else {
    const placeholder = escapeText(data.placeholder || "Type a short note…");
    const shared = escapeText(
      (payload.my_response && payload.my_response.response && payload.my_response.response.text) ||
        ""
    );
    controls = `
      <label class="prompt-share">
        <span>${kind === "draw" ? "Mark and name" : "Share your work"}</span>
        <textarea id="prompt-share-input" rows="3" maxlength="2000" placeholder="${placeholder}"${
          lockChoices ? " disabled" : ""
        }>${shared}</textarea>
      </label>
      <button type="button" class="prompt-submit" id="prompt-share-submit"${
        lockChoices ? " disabled" : ""
      }>Submit Answer</button>
    `;
    }
  } else {
    controls = `<p class="prompt-idle">Unsupported prompt kind.</p>`;
  }
  controls = groupNote + controls;
  const itemId = String(data.item_id || "").trim();
  const label = String(data.label || data.title || "").trim();
  const kindLine = label
    ? label
    : itemId
    ? itemId
    : `${kind.toUpperCase()} · slide ${escapeText(prompt.slide_index)}`;
  const chain = Array.isArray(data.chain) ? data.chain : [];
  const step = String(data.step || "");
  const dots = chain.length
    ? `<p class="meet-progress-dots" aria-label="Meet progress">${chain
        .map((letter) => {
          const cls = letter === step ? "is-current" : "";
          return `<span class="meet-dot ${cls}">${escapeText(letter)}</span>`;
        })
        .join("")}</p>`
    : "";
  promptShell.hidden = false;
  promptShell.innerHTML = `
    ${dots}
    <p class="prompt-kind">${escapeText(kindLine)}</p>
    <h2 class="prompt-title">${title}</h2>
    <div class="prompt-controls" data-prompt-id="${escapeText(prompt.id)}">${controls}</div>
  `;
  lastPromptId = Number(prompt.id);
  lastMeetSig = `${prompt.id}:${data.step || ""}:${data.chain_index || ""}`;
  lastSummarySig = studentSummarySig(payload);
  if (!lockChoices && !studentPollClosed(payload)) {
    wirePromptControls(prompt);
  }
}

/**
 * Bind placeholder submit handlers for the active prompt widgets.
 * @param {any} prompt
 */
function wirePromptControls(prompt) {
  const root = promptShell && promptShell.querySelector(".prompt-controls");
  if (!root) return;
  const payload = lastStudentPayload || {};
  const groupQuestion = String(payload.question_view || "") === "team";
  const data = (prompt && prompt.payload) || {};
  const integerOnly = Boolean(data.integer_only) || isTeamsSparkPrompt({ prompt });
  root.querySelectorAll(".prompt-choice").forEach((btn) => {
    btn.addEventListener("click", () => {
      root.querySelectorAll(".prompt-choice").forEach((other) => {
        other.classList.toggle("is-selected", other === btn);
      });
      const choice = btn.getAttribute("data-choice") || "";
      if (groupQuestion && choice) {
        submitResponse(prompt.id, { choice }, { draft: true });
      }
    });
  });
  const mcSubmit = root.querySelector("#prompt-mc-submit");
  if (mcSubmit) {
    mcSubmit.addEventListener("click", () => {
      const selected = root.querySelector(".prompt-choice.is-selected");
      const choice = selected && selected.getAttribute("data-choice");
      if (!choice) return;
      submitResponse(prompt.id, { choice });
    });
  }
  const numSubmit = root.querySelector("#prompt-numeric-submit");
  if (numSubmit) {
    numSubmit.addEventListener("click", () => {
      const input = root.querySelector("#prompt-numeric-input");
      const raw = input && "value" in input ? String(input.value).trim() : "";
      if (raw === "") return;
      const number = Number(raw);
      if (!Number.isFinite(number)) return;
      if (integerOnly && !Number.isInteger(number)) return;
      submitResponse(prompt.id, { value: integerOnly ? Math.trunc(number) : number });
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
 * Student-safe feedback object from submit JSON or my_response.
 * @param {any} data
 * @returns {{text: string, lead: string, source: string, match?: boolean} | null}
 */
function feedbackObject(data) {
  const top = data && data.feedback;
  const mine = data && data.my_response && data.my_response.feedback;
  const fb = top && top.text ? top : mine && mine.text ? mine : top || mine;
  if (!fb || typeof fb !== "object") return null;
  const text = String(fb.text || "").trim();
  const lead = String(fb.lead || "").trim();
  if (!text && !lead) return null;
  return {
    text,
    lead,
    source: String(fb.source || "").trim(),
    match: Boolean(fb.match),
  };
}

/**
 * One short why line from submit JSON or my_response.
 * @param {any} data
 * @returns {string}
 */
function feedbackLine(data) {
  const fb = feedbackObject(data);
  return fb ? fb.text : "";
}

/**
 * Show the post-submit beat: one calm line, or the generic ack.
 * @param {string} line
 */
function showPromptAck(line) {
  if (!promptAck) return;
  const text = String(line || "").trim();
  promptAck.hidden = false;
  promptAck.textContent = text || "Response received.";
  promptAck.classList.toggle("is-feedback", Boolean(text));
}

/**
 * Mount one lead + why + Close overlay inside the Question frame.
 * @param {{text?: string, lead?: string}} fragment
 */
/**
 * Show live class (and Meet team) totals under personal feedback.
 * @param {any} payload
 */
function paintPollTotalsBelowFeedback(payload) {
  if (!promptPollTotals) return;
  const meet = studentMeetPollsHtml(payload);
  const tally = studentMcSummary(payload);
  if (!meet && !tally) {
    promptPollTotals.hidden = true;
    promptPollTotals.innerHTML = "";
    return;
  }
  promptPollTotals.hidden = false;
  promptPollTotals.innerHTML = meet || `<section class="prompt-poll-card"><p class="meet-poll-kicker">Class-wide</p>${mcRevealBarsHtml(tally)}</section>`;
}

/**
 * Turn a choice-count map into the shared poll-bar markup.
 * @param {Record<string, number>} counts
 * @param {string[]} [labels]
 * @returns {string}
 */
function countsToTallyHtml(counts, labels) {
  const keys = Array.isArray(labels) && labels.length ? labels : Object.keys(counts || {});
  const total = keys.reduce((sum, key) => sum + Number((counts || {})[key] || 0), 0);
  const letters = "ABCDEFGH";
  return mcRevealBarsHtml({
    choices: keys.map((label, index) => {
      const count = Number((counts || {})[label] || 0);
      return {
        id: letters[index] || String(index + 1),
        label,
        count,
        pct: total ? Math.round((100 * count) / total) : 0,
      };
    }),
  });
}

/**
 * Team-specific poll and class-wide poll as two separate result cards.
 * @param {any} payload
 * @returns {string}
 */
function studentMeetPollsHtml(payload) {
  const ts = (payload && payload.teacher_state) || {};
  if (String(ts.stage || "") !== "meet") return "";
  const chain = ts.meet_chain || {};
  const letters = Array.isArray(chain.chain) ? chain.chain : [];
  const step = String(letters[Number(chain.index) || 0] || "");
  if (!step || step === "C") return "";
  const bag = step === "B" ? chain.b_picks || {} : chain.a_picks || {};
  const promptChoices =
    (payload.prompt && payload.prompt.payload && payload.prompt.payload.choices) || [];
  const labels = Array.isArray(promptChoices)
    ? promptChoices.map((row) => (typeof row === "string" ? row : String(row.label || row.text || "")))
    : [];
  const classCounts = {};
  labels.forEach((label) => {
    classCounts[label] = 0;
  });
  Object.values(bag).forEach((choice) => {
    const key = String(choice || "").trim();
    if (key) classCounts[key] = (classCounts[key] || 0) + 1;
  });
  const meId = Number((payload.me && payload.me.id) || 0);
  const meTeam = String((payload.me && payload.me.team_name) || "").trim();
  const teams = (payload.scoreboard && payload.scoreboard.teams) || [];
  const mine =
    teams.find((team) =>
      (team.members || team.players || []).some((row) => Number(row.id) === meId)
    ) || teams.find((team) => String(team.name || "").trim() === meTeam);
  const teamCounts = {};
  labels.forEach((label) => {
    teamCounts[label] = 0;
  });
  if (mine) {
    const memberIds = new Set(
      (mine.members || mine.players || []).map((row) => Number(row.id))
    );
    Object.entries(bag).forEach(([key, choice]) => {
      const sid = Number(String(key).replace(/^student:/, ""));
      if (!memberIds.has(sid)) return;
      const label = String(choice || "").trim();
      if (label) teamCounts[label] = (teamCounts[label] || 0) + 1;
    });
  }
  const teamName = escapeText((mine && mine.name) || "Your team");
  return `<section class="prompt-poll-card prompt-poll-team"><p class="meet-poll-kicker">Team · ${teamName}</p>${countsToTallyHtml(
    teamCounts,
    labels
  )}</section><section class="prompt-poll-card prompt-poll-class"><p class="meet-poll-kicker">Class-wide</p>${countsToTallyHtml(
    classCounts,
    labels
  )}</section>`;
}

function showFeedbackPanel(fragment) {
  if (!promptFeedback || !fragment) return;
  const lead = String(fragment.lead || "").trim();
  const why = String(fragment.text || "").trim();
  if (!lead && !why) return;
  hidePromptAck();
  if (promptFeedbackLead) promptFeedbackLead.textContent = lead || "Good work.";
  if (promptFeedbackWhy) promptFeedbackWhy.textContent = why;
  promptFeedback.hidden = false;
  if (questionFrame) questionFrame.classList.add("is-feedback");
  feedbackDismissed = false;
  if (promptFeedbackClose) {
    promptFeedbackClose.focus();
  } else {
    promptFeedback.focus();
  }
}

/**
 * Hide the generic one-line ack.
 */
function hidePromptAck() {
  if (!promptAck) return;
  promptAck.hidden = true;
  promptAck.classList.remove("is-feedback");
}

/**
 * Close keyed waiting-room feedback, then paint the next student prompt.
 */
function dismissFeedbackPanel() {
  hideFeedbackPanel();
  holdJoinFeedback = false;
  feedbackDismissed = true;
  tick();
}

/**
 * Hide the overlay without treating it as an explicit Close.
 */
function hideFeedbackPanel() {
  if (promptFeedback) {
    promptFeedback.hidden = true;
    if (promptFeedbackLead) promptFeedbackLead.textContent = "";
    if (promptFeedbackWhy) promptFeedbackWhy.textContent = "";
  }
  if (questionFrame) questionFrame.classList.remove("is-feedback");
}

/**
 * POST a student response for the active prompt.
 * @param {number} promptId
 * @param {Record<string, unknown>} response
 */
async function submitResponse(promptId, response, { draft = false } = {}) {
  try {
    const res = await fetch(
      "/api/student/live-prompt/response",
      visitFetchInit({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ prompt_id: promptId, response, draft }),
      })
    );
    const data = await res.json();
    if (data.redirect) {
      location.href = data.redirect;
      return;
    }
    if (data.ok && data.draft) {
      return;
    }
    if (data.ok && data.ack) {
      hideQuestionBody();
      hidePromptAck();
      const fb = feedbackObject(data);
      if (fb) {
        const key = `${promptId}:${fb.lead}:${fb.text}`;
        lastFeedbackKey = key;
        if (isJoinMindsOnPrompt(lastStudentPayload)) {
          holdJoinFeedback = true;
        }
        showFeedbackPanel(fb);
        paintPollIfQuestionsVisible({
          ...data,
          my_response: data.my_response,
          mc_tally: data.mc_tally,
        });
        tick();
        return;
      }
      hideFeedbackPanel();
      await tick();
    }
  } catch (_err) {
    /* keep UI; next poll retries */
  }
}

let lastScoreSig = "";
let celebratePainted = false;

/**
 * Bounce a name off the student scoreboard for five seconds.
 * @param {string} name
 */
function spawnScorePop(name) {
  const layer = document.getElementById("score-pop-layer");
  if (!(layer instanceof HTMLElement)) return;
  const chip = document.createElement("p");
  chip.className = "score-pop-chip";
  chip.textContent = `+1 ${String(name || "").trim() || "Point"}`;
  layer.appendChild(chip);
  window.setTimeout(() => chip.remove(), 5000);
}

/**
 * When a team or personal score rises, pop that name from the board.
 * @param {any} payload
 */
function maybeScorePops(payload) {
  const teams = ((payload.scoreboard && payload.scoreboard.teams) || []).filter(
    (team) => String(team.name || "") !== "Class"
  );
  const mePts = Number((payload.me && (payload.me.session_points ?? payload.me.points)) || 0);
  const sig = `${teams.map((team) => `${team.id}:${team.score}`).join("|")}|me:${mePts}`;
  if (lastScoreSig && sig !== lastScoreSig) {
    const prev = Object.fromEntries(
      lastScoreSig
        .split("|")
        .filter((part) => part && !part.startsWith("me:"))
        .map((part) => {
          const idx = part.lastIndexOf(":");
          return [part.slice(0, idx), Number(part.slice(idx + 1))];
        })
    );
    teams.forEach((team) => {
      const before = Number(prev[String(team.id)] || 0);
      const after = Number(team.score || 0);
      if (after > before) spawnScorePop(team.name);
    });
    const prevMe = Number(String(lastScoreSig.split("|").find((part) => part.startsWith("me:")) || "me:0").slice(3));
    if (mePts > prevMe) {
      const meName = String((payload.me && (payload.me.codename || payload.me.name)) || "You");
      spawnScorePop(meName);
    }
  }
  lastScoreSig = sig;
}

/**
 * Keep the scoreboard up with winner graffiti + How-was-class after End Live.
 * Quit wipes the SID and this overlay disappears with the redirect.
 * @param {any} payload
 */
function paintCelebrate(payload) {
  const host = document.getElementById("student-celebrate");
  const graffiti = document.getElementById("student-graffiti");
  const form = document.getElementById("how-was-class");
  if (!(host instanceof HTMLElement)) return;
  const winnerBanner = document.getElementById("student-winner-banner");
  const winnerNameEl = document.getElementById("student-winner-name");
  if (saveWorkBtn) saveWorkBtn.hidden = Boolean(payload.celebrate);
  if (!payload.celebrate) {
    host.hidden = true;
    celebratePainted = false;
    if (graffiti) graffiti.innerHTML = "";
    if (form) form.hidden = true;
    if (winnerBanner) winnerBanner.hidden = true;
    if (winnerNameEl) winnerNameEl.textContent = "";
    document.body.classList.remove("is-celebrating");
    return;
  }
  host.hidden = false;
  document.body.classList.add("is-celebrating");
  const teamName = String((payload.winner && payload.winner.name) || "").trim();
  if (winnerNameEl) winnerNameEl.textContent = teamName || "Winner";
  if (winnerBanner) winnerBanner.hidden = false;
  if (form) {
    form.hidden = !Boolean(payload.exit_feedback && payload.exit_feedback.pending);
    form.dataset.token = (payload.exit_feedback && payload.exit_feedback.token) || "";
  }
  if (celebratePainted || !(graffiti instanceof HTMLElement)) return;
  celebratePainted = true;
  graffiti.innerHTML = "";
  const colors = ["#f5c518", "#ef4444", "#3d7eff", "#9dffb0"];
  for (let i = 0; i < 8; i += 1) {
    const drip = document.createElement("span");
    drip.className = "live-overlay-graffiti-drip";
    drip.style.setProperty("--c", colors[i % colors.length]);
    drip.style.setProperty("--delay", `${(0.2 + Math.random() * 0.8).toFixed(2)}s`);
    drip.style.setProperty("--h", `${24 + Math.random() * 56}px`);
    drip.style.left = `${18 + Math.random() * 64}%`;
    drip.style.top = `${42 + Math.random() * 18}%`;
    graffiti.appendChild(drip);
  }
}

/**
 * POST How-was-class from the celebrating overlay.
 * @param {{skip?: boolean}} [opts]
 */
async function submitExitFeedback(opts = {}) {
  const form = document.getElementById("how-was-class");
  if (!(form instanceof HTMLElement)) return;
  const picked = form.querySelector('input[name="exit-mood"]:checked');
  const comment = document.getElementById("exit-comment");
  const body = {
    token: form.dataset.token || "",
    mood: picked && "value" in picked ? picked.value : "",
    comment: comment && "value" in comment ? comment.value : "",
    skip: opts.skip ? "1" : "",
  };
  try {
    const res = await fetch(
      "/api/student/exit-feedback",
      visitFetchInit({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(body),
      })
    );
    const data = await res.json();
    if (data && data.ok) {
      form.hidden = true;
    }
  } catch (_err) {
    /* keep the form */
  }
}

document.getElementById("exit-feedback-send")?.addEventListener("click", () => {
  submitExitFeedback();
});
document.getElementById("exit-feedback-skip")?.addEventListener("click", () => {
  submitExitFeedback({ skip: true });
});

/**
 * True when the student should see the TEAMS VLC welcome card.
 * @param {any} payload
 * @returns {boolean}
 */
function hasGameShowWelcome(payload) {
  return Boolean(payload && payload.game_show_welcome);
}

/**
 * Paint the TEAMS VLC Math Game Show welcome (title, codes, rounds, avatars).
 * @param {any} payload
 */
function paintGameShowWelcome(payload) {
  if (!gameShowWelcomeEl) return;
  const welcome = payload && payload.game_show_welcome;
  if (!welcome) {
    lastWelcomeKey = "";
    gameShowWelcomeEl.hidden = true;
    gameShowWelcomeEl.innerHTML = "";
    return;
  }
  const people = Array.isArray(welcome.participants) ? welcome.participants : [];
  const key = [
    welcome.class_code || "",
    welcome.lesson_code || "",
    people.map((row) => `${row.codename || ""}:${row.character || ""}`).join("|"),
    (welcome.rounds || [])
      .map((row) => `${row.title || ""}:${row.blurb || ""}`)
      .join("|"),
  ].join("::");
  if (key === lastWelcomeKey && !gameShowWelcomeEl.hidden) return;
  lastWelcomeKey = key;
  const title = escapeText(welcome.title || "VLC Math Game Show");
  const classCode = escapeText(welcome.class_code || "—");
  const lessonCode = escapeText(welcome.lesson_code || "—");
  const rounds = Array.isArray(welcome.rounds) ? welcome.rounds : [];
  const roundHtml = rounds
    .map((row, index) => {
      const n = String(index + 1).padStart(2, "0");
      return `<li class="gs-round gs-round-${escapeText(row.kind || "open")}">
        <span class="gs-round-n" aria-hidden="true">${n}</span>
        <div class="gs-round-copy">
          <p class="gs-round-title">${escapeText(row.title || "")}</p>
          <p class="gs-round-blurb">${escapeText(row.blurb || "")}</p>
        </div>
      </li>`;
    })
    .join("");
  const orbit = people.length > 0 && people.length <= 12;
  const castHtml = people
    .map((row, index) => {
      const face = nameWithAvatar(row.codename || "Student", row.character);
      return `<li class="gs-cast-chip" style="--i:${index};--n:${people.length}">${face}</li>`;
    })
    .join("");
  gameShowWelcomeEl.innerHTML = `
    <div class="gs-banner" role="heading" aria-level="1">
      <p class="gs-kicker">Welcome to the</p>
      <h2 class="gs-title">${title}</h2>
    </div>
    <p class="gs-subtitle">
      <span class="gs-code"><span class="gs-code-label">Class</span> ${classCode}</span>
      <span class="gs-code"><span class="gs-code-label">Lesson</span> ${lessonCode}</span>
    </p>
    <ol class="gs-rounds">${roundHtml}</ol>
    <div class="gs-cast ${orbit ? "is-orbit" : "is-marquee"}" aria-label="Who is here">
      <p class="gs-cast-kicker">${people.length ? "In the room" : "Waiting for classmates…"}</p>
      <ul class="gs-cast-list">${castHtml || `<li class="gs-cast-empty">Avatars appear as students join.</li>`}</ul>
    </div>`;
  gameShowWelcomeEl.hidden = false;
}

/**
 * Fetch and paint /api/student/state.
 */
async function tick() {
  try {
    const res = await fetch("/api/student/state", visitFetchInit());
    const data = await res.json();
    if (
      (data.status === "ended" || data.status === "waiting")
      && !data.celebrate
    ) {
      paintCelebrate({ celebrate: false });
    }
    if (data.redirect && data.redirect !== "/student/home" && !data.celebrate) {
      location.href = data.redirect;
      return;
    }
    const prevSeq = lastStateSeq;
    lastStudentPayload = data;
    applyTeacherProjection(data);
    applyLayout(data);
    paintGameShowWelcome(data);
    paintStudentCanvas(data);
    paintDisplayTime(data);
    paintMe(data);
    paintMyTeam(data);
    paintBoard(data);
    maybeScorePops(data);
    paintCelebrate(data);
    paintRoundBanner(data);
    paintMedia(data);
    paintMeetCue(data);
    const promptId = data.prompt && data.prompt.id != null ? Number(data.prompt.id) : null;
    const meetSig = data.prompt
      ? `${promptId}:${(data.prompt.payload && data.prompt.payload.step) || ""}:${
          (data.prompt.payload && data.prompt.payload.chain_index) || ""
        }`
      : "";
    const seqChanged = lastStateSeq !== prevSeq;
    const summarySig = studentSummarySig(data);
    if (data.celebrate) {
      if (promptShell) {
        promptShell.hidden = true;
        promptShell.innerHTML = "";
      }
      hideFeedbackPanel();
    } else if (
      seqChanged ||
      promptId !== lastPromptId ||
      meetSig !== lastMeetSig ||
      summarySig !== lastSummarySig ||
      (data.my_response && promptShell && !promptShell.hidden)
    ) {
      paintPrompt(data);
    } else if (!data.prompt) {
      paintPrompt(data);
    }
  } catch (_err) {
    /* keep last paint */
  }
}

if (promptFeedbackClose) {
  promptFeedbackClose.addEventListener("click", dismissFeedbackPanel);
}
if (promptFeedback) {
  promptFeedback.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      dismissFeedbackPanel();
    }
  });
}

if (saveWorkBtn) {
  saveWorkBtn.addEventListener("click", () => {
    saveStudentWork();
  });
}

bindStudentCanvas();
tick();
setInterval(tick, 4000);
setInterval(tickDisplayTime, 250);

const bootCodename = body && body.dataset ? body.dataset.codename : "";
if (bootCodename) {
  setTabTitle(bootCodename);
}
