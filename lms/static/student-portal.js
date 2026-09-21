/**
 * Phone-first student live-class home: Live response shell + chrome boards.
 */
import { formatQuestionHtml, renderLiveQuestionMath } from "/static/common.js";
import { bindWhiteboard } from "/static/live_whiteboard.js";
import { avatarGlyph, nameWithAvatar } from "/static/student_avatars.js";

/**
 * Format a minted title or stem the same way catalogue question HTML is formatted.
 * @param {unknown} value
 * @returns {string}
 */
function formatPromptHtml(value) {
  return formatQuestionHtml(value);
}

const waitEl = document.getElementById("student-wait");
const gameShowWelcomeEl = document.getElementById("game-show-welcome");
const promptPollTotals = document.getElementById("prompt-poll-totals");
const displayTimeEl = document.getElementById("me-display-time");
const meEl = document.getElementById("me-board");
const boardEl = document.getElementById("class-board");
const roundBannerEl = document.getElementById("student-round-banner");
const promptShell = document.getElementById("prompt-shell");
const questionFrame = document.getElementById("question-frame");
const liveQuestionStack = document.getElementById("live-question-stack");
const liveQuestionStackBody = document.getElementById("live-question-stack-body");
const studentQuestionDock = document.getElementById("student-question-dock");
const promptFeedback = document.getElementById("prompt-feedback");
const promptFeedbackLead = document.getElementById("prompt-feedback-lead");
const promptFeedbackWhy = document.getElementById("prompt-feedback-why");
const promptFeedbackHelper = document.getElementById("prompt-feedback-helper");
const promptFeedbackClose = document.getElementById("prompt-feedback-close");
const promptDismiss = document.getElementById("prompt-dismiss");
const promptAck = document.getElementById("prompt-ack");
const mediaPane = document.getElementById("media-pane");
const mediaFrame = document.getElementById("media-frame");
const mediaLock = document.getElementById("media-lock");
const canvasPane = document.getElementById("canvas-pane");
const canvasLock = document.getElementById("canvas-lock");
const studentCanvas = document.getElementById("student-canvas");
const canvasCursors = document.getElementById("canvas-cursors");
const slidesPane = document.getElementById("slides-pane");
const slidesFrame = document.getElementById("slides-frame");
const slidesEmpty = document.getElementById("slides-empty");
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
/** Prompt ids explicitly dismissed after submission. */
const dismissedPromptIds = new Set();
/** Lifecycle Active/Closed cards explicitly dismissed in this tab. */
const dockedLiveCardKeys = new Set();
/** Question cards closed with × — removed from the student view, not docked. */
const dismissedLiveCardKeys = new Set();
/** Floating panes currently following a held pointer. Do not remount or hide. */
const activePaneDrags = new Set();
/** Original parent/sibling so Reset can put a reparented card back. */
const paneHomes = new WeakMap();
/** Unsaved per-card answers preserved across state polls. */
const liveCardDrafts = new Map();
/** In-flight lifecycle submit keys (`itemId:action`) to block double posts. */
const liveSubmitInFlight = new Set();
/** @type {number} */
let lastStateSeq = -1;
let lastPollStamp = "";
/** @type {any} */
let lastStudentPayload = null;
/** @type {string} */
let lastSummarySig = "";
/** @type {number} */
let toastHideTimer = 0;
/** @type {Record<string, number|string>} */
let lastArtifactSliders = { a: 1, h: 0, k: 0 };
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
 * Named teams for the student scoreboard, from the board or group snapshot.
 * @param {any} payload
 * @returns {Array<{name:string,color?:string,score?:number}>}
 */
function namedScoreboardTeams(payload) {
  const fromBoard = ((payload?.scoreboard || {}).teams || []).filter(
    (team) => team && team.name !== "Class"
  );
  if (fromBoard.length) return fromBoard;
  return ((payload?.groups || []).filter((team) => team && team.name !== "Class"));
}

/**
 * Paint the public team scoreboard strip.
 * @param {any} payload
 */
function paintBoard(payload) {
  if (!boardEl) return;
  const visible = Boolean((payload?.teacher_state || {}).scoreboard_visible);
  const teams = visible ? namedScoreboardTeams(payload) : [];
  if (!teams.length) {
    boardEl.innerHTML = "";
    boardEl.hidden = true;
    return;
  }
  boardEl.hidden = false;
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

/** Render a graph image at full card width for lifecycle questions. */
function questionImageHtmlStudent(imageUrl) {
  const url = String(imageUrl || "").trim();
  if (!url) return "";
  return `<img class="live-question-image is-full" src="${escapeText(url)}" alt="Question graph" loading="lazy">`;
}

/** Prefer server-rendered question HTML when available. */
function lifecyclePromptHtml(content) {
  const rendered = String(content?.text_html || "").trim();
  if (rendered) return `<span class="live-question-html">${rendered}</span>`;
  const raw = content?.text || content?.prompt || content?.question || "Live question";
  return `<span class="live-question-html">${formatQuestionHtml(raw)}</span>`;
}

/**
 * Render optional LaTeX under the student stem, never concatenated into text.
 * @param {any} content
 * @returns {string}
 */
function lifecycleEquationHtml(content) {
  const latex = String(content?.equation_latex || content?.equation || "").trim();
  if (!latex) return "";
  const raw = latex.replace(/^\$+|\$+$/g, "").trim();
  return `<p class="student-live-equation"><span class="math-latex" data-latex="${escapeText(
    raw
  )}"></span></p>`;
}

/** Wonder waiting-room line (pre–Generate teams / pre–Team Challenge). */
const WAITING_ROOM_WAIT_LINE = "Waiting room — class is about to begin.";

/**
 * Return one active published non-question lifecycle item.
 * @param {any} payload
 * @param {string} itemType
 * @returns {any|null}
 */
/**
 * Return the public item type for one student live-item row.
 * @param {any} row
 * @returns {string}
 */
function publishedItemType(row) {
  return String(
    row?.content?.item_type || row?.kind || row?.item?.item_type || ""
  ).toLowerCase();
}

function activePublishedItem(payload, itemType) {
  const wanted = String(itemType || "").toLowerCase();
  return (
    (payload?.live_items || []).find(
      (row) => row?.status === "active" && publishedItemType(row) === wanted
    ) || null
  );
}

/**
 * Student frame projection from LiveTeacherState.
 * @param {any} payload
 * @returns {{stage: string, seq: number, questions: boolean, media: boolean, canvas: boolean, slides: boolean, unlockMedia: boolean, unlockCanvas: boolean}}
 */
function studentProjection(payload) {
  const ts = (payload && payload.teacher_state) || {};
  const view = ts.student_view || {};
  const frames = ts.student_frames || {};
  const unlocks = ts.unlocks || {};
  const stage = String(ts.stage || "join");
  const seq = Number(ts.state_seq);
  const hasLifecycleQuestions = [
    ...(payload?.active_questions || []),
    ...(payload?.closed_results || []),
  ].some((row) => {
    const kind = String(
      row?.content?.item_type || row?.item_type || row?.kind || row?.prompt?.kind || "question"
    ).toLowerCase();
    return !["media", "whiteboard", "slides"].includes(kind);
  });
  const questionsMode = String(
    hasLifecycleQuestions
      ? "student"
      : view.questions || payload.question_view || (frames.questions === false ? "none" : "student")
  );
  const mediaItem = activePublishedItem(payload, "media");
  const whiteboardItem = activePublishedItem(payload, "whiteboard");
  const slidesItem = activePublishedItem(payload, "slides");
  const mediaMode = mediaItem
    ? mediaItem.publish_mode === "group_shared" ? "team" : "student"
    : String(view.media || (unlocks.media ? "student" : "none"));
  const canvasMode = whiteboardItem
    ? whiteboardItem.publish_mode === "group_shared" ? "team" : "student"
    : String(view.canvas || (unlocks.canvas ? "student" : "none"));
  const slidesMode = slidesItem
    ? "student"
    : String(view.slides || (unlocks.slides ? "student" : "none"));
  const canvasAlign =
    canvasMode === "team" ? "team" : canvasMode === "student" ? "student" : "teacher";
  return {
    stage,
    seq: Number.isFinite(seq) ? seq : 0,
    questions: questionsMode !== "none",
    media: mediaMode !== "none",
    canvas: canvasMode !== "none",
    slides: slidesMode !== "none",
    unlockMedia: mediaMode !== "none",
    unlockCanvas: canvasMode !== "none",
    canvasAlign,
    questionsMode,
    mediaMode,
    canvasMode,
    slidesMode,
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

/**
 * Paint the metadata-connected slide deck without exposing teacher fields.
 * @param {any} payload
 */
function paintStudentSlides(payload) {
  const published = activePublishedItem(payload, "slides");
  const deckRef = String(
    published?.content?.deck_ref || payload?.live_metadata?.slides?.deck_ref || ""
  ).trim();
  const safe = /^https?:\/\//i.test(deckRef);
  if (slidesEmpty) slidesEmpty.hidden = safe;
  if (!(slidesFrame instanceof HTMLIFrameElement)) return;
  if (!safe) {
    slidesFrame.hidden = true;
    slidesFrame.removeAttribute("src");
    return;
  }
  if (slidesFrame.src !== deckRef) slidesFrame.src = deckRef;
  slidesFrame.hidden = false;
}

/**
 * True while a pane is pinned to a held pointer.
 * @param {HTMLElement | null} pane
 * @returns {boolean}
 */
function paneIsBeingGrabbed(pane) {
  return pane instanceof HTMLElement && activePaneDrags.has(pane);
}

/**
 * Remember where a card lived before it was lifted onto the workspace.
 * @param {HTMLElement} pane
 */
function rememberPaneHome(pane) {
  if (paneHomes.has(pane)) return;
  paneHomes.set(pane, { parent: pane.parentElement, next: pane.nextSibling });
}

/**
 * Put a Reset pane back in the question stack (or wherever it started).
 * @param {HTMLElement} pane
 */
function restorePaneHome(pane) {
  const home = paneHomes.get(pane);
  paneHomes.delete(pane);
  if (!home?.parent || home.parent === pane.parentElement) return;
  if (home.next && home.next.parentNode === home.parent) {
    home.parent.insertBefore(pane, home.next);
  } else {
    home.parent.appendChild(pane);
  }
}

/**
 * Clamp a floated box so it stays inside the student workspace.
 * @param {DOMRect} hostRect
 * @param {number} left
 * @param {number} top
 * @param {number} width
 * @param {number} height
 * @returns {{left: number, top: number}}
 */
function clampPaneToHost(hostRect, left, top, width, height) {
  const maxLeft = Math.max(0, hostRect.width - Math.min(width, hostRect.width));
  const maxTop = Math.max(0, hostRect.height - Math.min(height, hostRect.height));
  return {
    left: Math.max(0, Math.min(maxLeft, left)),
    top: Math.max(0, Math.min(maxTop, top)),
  };
}

/**
 * Float a projected pane at its current position inside the student workspace.
 *
 * Question cards live in a narrow absolute stack. Reparent onto `#live-response`
 * so left/top match the host and the card cannot jump off-workspace.
 *
 * @param {HTMLElement} pane
 * @param {HTMLElement} host
 * @returns {{hostRect: DOMRect, paneRect: DOMRect}}
 */
function floatPaneAtCurrentPosition(pane, host) {
  const hostRect = host.getBoundingClientRect();
  const paneRect = pane.getBoundingClientRect();
  const width = Math.max(1, paneRect.width);
  const height = Math.max(1, paneRect.height);
  if (pane.parentElement !== host) {
    rememberPaneHome(pane);
    host.appendChild(pane);
  }
  pane.hidden = false;
  pane.classList.add("is-floating");
  const placed = clampPaneToHost(
    hostRect,
    paneRect.left - hostRect.left,
    paneRect.top - hostRect.top,
    width,
    height
  );
  pane.style.left = `${placed.left}px`;
  pane.style.top = `${placed.top}px`;
  pane.style.width = `${width}px`;
  pane.style.height = `${height}px`;
  return { hostRect, paneRect };
}

/**
 * Make one projected pane draggable and visibly resizable inside the workspace.
 * @param {HTMLElement | null} pane
 */
function bindFloatingPane(pane) {
  if (!(pane instanceof HTMLElement)) return;
  if (pane.dataset.paneBound === "1") return;
  const host = document.getElementById("live-response");
  const handle = pane.querySelector("[data-pane-drag]");
  const resizeHandle = pane.querySelector("[data-pane-resize]");
  const reset = pane.querySelector("[data-pane-reset]");
  if (!(host instanceof HTMLElement) || !(handle instanceof HTMLElement)) return;
  pane.dataset.paneBound = "1";
  let drag = null;
  let resizeDrag = null;
  const resetPane = () => {
    activePaneDrags.delete(pane);
    pane.classList.remove("is-floating", "is-pane-dragging");
    for (const prop of ["left", "top", "width", "height"]) {
      pane.style.removeProperty(prop);
    }
    restorePaneHome(pane);
  };
  reset?.addEventListener("click", (event) => {
    event.stopPropagation();
    resetPane();
  });
  handle.addEventListener("pointerdown", (event) => {
    if (window.innerWidth < 720 || event.target.closest("button")) return;
    event.preventDefault();
    event.stopPropagation();
    const paneRect = pane.getBoundingClientRect();
    drag = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      grabX: event.clientX - paneRect.left,
      grabY: event.clientY - paneRect.top,
      pending: true,
    };
    handle.setPointerCapture(event.pointerId);
  });
  /**
   * Follow the pointer once the grab clears the ~4px click threshold.
   * @param {PointerEvent} event
   */
  const onPanePointerMove = (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (drag.pending) {
      if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 4) return;
      floatPaneAtCurrentPosition(pane, host);
      drag.pending = false;
      pane.classList.add("is-pane-dragging");
      activePaneDrags.add(pane);
    }
    const hostRect = host.getBoundingClientRect();
    const width = Math.max(1, pane.offsetWidth || pane.getBoundingClientRect().width);
    const height = Math.max(1, pane.offsetHeight || pane.getBoundingClientRect().height);
    const placed = clampPaneToHost(
      hostRect,
      event.clientX - hostRect.left - drag.grabX,
      event.clientY - hostRect.top - drag.grabY,
      width,
      height
    );
    pane.hidden = false;
    pane.style.left = `${placed.left}px`;
    pane.style.top = `${placed.top}px`;
  };
  handle.addEventListener("pointermove", onPanePointerMove);
  /**
   * Drop the grab. A click (still pending) must not undock, hide, or float.
   * pointercancel also keeps the pane visible.
   * @param {PointerEvent} event
   */
  const endDrag = (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const wasPending = drag.pending;
    drag = null;
    pane.classList.remove("is-pane-dragging");
    activePaneDrags.delete(pane);
    if (handle.hasPointerCapture(event.pointerId)) {
      handle.releasePointerCapture(event.pointerId);
    }
    if (wasPending) return;
    pane.hidden = false;
  };
  handle.addEventListener("pointerup", endDrag);
  handle.addEventListener("pointercancel", endDrag);
  resizeHandle?.addEventListener("pointerdown", (event) => {
    if (window.innerWidth < 720) return;
    event.preventDefault();
    event.stopPropagation();
    const { hostRect, paneRect } = floatPaneAtCurrentPosition(pane, host);
    resizeDrag = {
      x: event.clientX,
      y: event.clientY,
      width: paneRect.width,
      height: paneRect.height,
      left: paneRect.left - hostRect.left,
      top: paneRect.top - hostRect.top,
    };
    resizeHandle.setPointerCapture(event.pointerId);
  });
  resizeHandle?.addEventListener("pointermove", (event) => {
    if (!resizeDrag) return;
    const hostRect = host.getBoundingClientRect();
    const maxWidth = Math.max(1, hostRect.width - resizeDrag.left);
    const maxHeight = Math.max(1, hostRect.height - resizeDrag.top);
    const minWidth = Math.min(288, maxWidth);
    const minHeight = Math.min(192, maxHeight);
    const width = Math.max(
      minWidth,
      Math.min(maxWidth, resizeDrag.width + event.clientX - resizeDrag.x)
    );
    const height = Math.max(
      minHeight,
      Math.min(maxHeight, resizeDrag.height + event.clientY - resizeDrag.y)
    );
    pane.style.width = `${width}px`;
    pane.style.height = `${height}px`;
  });
  /** End a pointer resize and release its capture. */
  const endResize = (event) => {
    if (!resizeDrag) return;
    resizeDrag = null;
    if (resizeHandle?.hasPointerCapture(event.pointerId)) {
      resizeHandle.releasePointerCapture(event.pointerId);
    }
  };
  resizeHandle?.addEventListener("pointerup", endResize);
  resizeHandle?.addEventListener("pointercancel", endResize);
  window.addEventListener("resize", () => {
    if (window.innerWidth < 720) resetPane();
  });
}

function applyTeacherProjection(payload) {
  const proj = studentProjection(payload);
  const welcomeOn = hasGameShowWelcome(payload);
  if (proj.seq !== lastStateSeq) {
    lastStateSeq = proj.seq;
  }
  if (questionFrame) {
    const promptId = Number(payload?.prompt?.id) || 0;
    const dismissed =
      Boolean(payload?.my_response) && dismissedPromptIds.has(promptId);
    questionFrame.hidden = !proj.questions || dismissed;
  }
  if (canvasPane) {
    const docked = dockedLiveCardKeys.has("surface:canvas");
    if (!paneIsBeingGrabbed(canvasPane)) {
      canvasPane.hidden = !proj.canvas || docked;
    }
    canvasPane.classList.toggle("is-readonly", proj.canvas && proj.canvasAlign === "teacher");
    if (canvasLock) canvasLock.hidden = true;
  }
  if (mediaPane) {
    mediaPane.classList.remove("is-locked");
    if (mediaLock) mediaLock.hidden = true;
    if (
      !paneIsBeingGrabbed(mediaPane) &&
      (!proj.media || dockedLiveCardKeys.has("surface:media"))
    ) {
      mediaPane.hidden = true;
      unmountStudentMedia();
    }
  }
  if (slidesPane) {
    if (!paneIsBeingGrabbed(slidesPane)) {
      slidesPane.hidden = !proj.slides || dockedLiveCardKeys.has("surface:slides");
    }
    if (proj.slides && !dockedLiveCardKeys.has("surface:slides")) paintStudentSlides(payload);
  }
  return proj;
}

function isWaitingRoom(payload) {
  if (typeof payload.waiting_room === "boolean") {
    return payload.waiting_room;
  }
  const proj = studentProjection(payload);
  const publishedMedia = activePublishedItem(payload, "media");
  const hasMedia =
    proj.media &&
    Boolean(
      payload.active_media?.url ||
      publishedMedia?.content?.file ||
      publishedMedia?.content?.url
    );
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
  const publishedMedia = activePublishedItem(payload, "media");
  const hasMedia =
    proj.media &&
    Boolean(
      payload.active_media?.url ||
      publishedMedia?.content?.file ||
      publishedMedia?.content?.url
    );
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
        artifact: media.artifact || null,
        student_play: Boolean(media.artifact),
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
  const proj = applyTeacherProjection(payload);
  if (payload.game_show_welcome) {
    if (mediaChip) mediaChip.hidden = true;
    if (mediaStem) mediaStem.hidden = true;
    if (mediaCaption) mediaCaption.hidden = true;
    if (mediaAnswers) mediaAnswers.hidden = true;
    if (mediaEncore) mediaEncore.hidden = true;
    if (!proj.media) return;
  }
  if (!proj.media) {
    if (mediaChip) mediaChip.hidden = true;
    if (mediaStem) mediaStem.hidden = true;
    if (mediaCaption) mediaCaption.hidden = true;
    if (mediaAnswers) mediaAnswers.hidden = true;
    if (mediaEncore) mediaEncore.hidden = true;
    return;
  }
  const published = activePublishedItem(payload, "media");
  const media =
    payload.active_media ||
    (published
      ? {
          ...published.content,
          url: published.content?.file || published.content?.url || "",
        }
      : null);
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
  if (dockedLiveCardKeys.has("surface:media") && !paneIsBeingGrabbed(mediaPane)) {
    mediaPane.hidden = true;
    return;
  }
  if (!url) {
    mediaFrame.removeAttribute("src");
    lastMediaUrl = "";
    lastMediaSig = "";
    lastToastKey = "";
    if (mediaToast) {
      mediaToast.hidden = true;
      mediaToast.textContent = "";
    }
    mediaPane.hidden = false;
    mediaPane.classList.add("is-empty");
    return;
  }
  mediaPane.classList.remove("is-empty");
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
    artifact: media.artifact || null,
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
  return Boolean(payload && payload.poll_closed);
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
 * Show class poll totals immediately after a student submits.
 * @param {any} payload
 */
function paintPollIfQuestionsVisible(payload) {
  const item =
    (payload?.active_questions || []).find(
      (row) => Number(row?.prompt?.id) === Number(payload?.prompt?.id)
    ) || null;
  if (item && item.show_live_results === false) {
    hidePollTotals();
    return;
  }
  paintPollTotalsBelowFeedback(payload);
}

/**
 * Hide class totals for keyed MC feedback.
 */
function hidePollTotals() {
  if (!promptPollTotals) return;
  promptPollTotals.hidden = true;
  promptPollTotals.innerHTML = "";
}

/**
 * Local dismissal key for one lifecycle card phase.
 * @param {any} item
 * @returns {string}
 */
function liveCardKey(item) {
  return `${Number(item?.id) || 0}:${String(item?.status || "active")}`;
}

/**
 * Stable per-question dock key so two cards never share one chip.
 * @param {any} item
 * @returns {string}
 */
function liveCardDockKey(item) {
  const liveId = Number(item?.id) || 0;
  const promptId = Number(item?.prompt?.id) || 0;
  const itemId = String(item?.item_id || item?.placement_key || item?.content?.item_id || "");
  return `card:${liveId}:${promptId}:${itemId}:${liveCardKey(item)}`;
}

/**
 * Display a normalized individual/group answer.
 * @param {any} answer
 * @returns {string}
 */
function liveAnswerLabel(answer) {
  if (typeof answer === "string" && answer.trim()) return answer.trim();
  if (!answer || typeof answer !== "object") return "—";
  return String(answer.value ?? answer.choice ?? answer.text ?? "—");
}

/**
 * Flatten catalogue options or prompt choices into student-facing labels.
 * @param {any} content
 * @returns {string[]}
 */
function liveChoiceLabels(content) {
  const body = content && typeof content === "object" ? content : {};
  const raw =
    Array.isArray(body.choices) && body.choices.length
      ? body.choices
      : Array.isArray(body.options)
        ? body.options
        : [];
  return raw
    .map((choice) => {
      if (choice && typeof choice === "object") {
        return String(choice.label || choice.text || choice.choice || "").trim();
      }
      return String(choice || "").trim();
    })
    .filter(Boolean);
}

/**
 * Render governed answer distribution bars.
 * @param {any} results
 * @returns {string}
 */
function lifecycleResultsHtml(results) {
  const choices = Array.isArray(results?.choices) ? results.choices : [];
  if (!choices.length) return "";
  return `<section class="student-live-results" aria-label="Class results">
    <p class="student-live-card-kicker">${
      results.kind === "numeric" ? "Class histogram" : "Class results"
    }</p>
    ${mcRevealBarsHtml({ choices })}
  </section>`;
}

/**
 * Render canonical team-answer distribution without exposing private votes.
 * @param {any} results
 * @returns {string}
 */
function lifecycleClassConsensusHtml(results) {
  const rows = Array.isArray(results?.class_distribution)
    ? results.class_distribution
    : [];
  if (!rows.length) return "";
  const total = rows.reduce((sum, row) => sum + Number(row.count || 0), 0);
  return `<section class="student-live-results" aria-label="Class team answers">
    <p class="student-live-card-kicker">Class team answers</p>
    ${mcRevealBarsHtml({
      choices: rows.map((row, index) => ({
        id: String(index + 1),
        label: liveAnswerLabel(row.answer),
        count: Number(row.count) || 0,
        pct: total ? Math.round((100 * Number(row.count || 0)) / total) : 0,
      })),
    })}
  </section>`;
}

/**
 * Return the student answer kind for one lifecycle card.
 * Catalogue ``type`` / ``integer_only`` win over a stale MC compatibility prompt.
 * @param {any} item
 * @returns {string}
 */
function lifecycleAnswerKind(item) {
  const prompt = item?.prompt || {};
  const content = item?.content || prompt.payload || {};
  if (
    content.integer_only ||
    String(content.type || "").toLowerCase() === "numeric" ||
    String(prompt.kind || "").toLowerCase() === "numeric"
  ) {
    return "numeric";
  }
  if (
    String(content.kind || "").toLowerCase() === "artifact" ||
    String(content.type || "").toLowerCase() === "artifact" ||
    String(prompt.kind || "").toLowerCase() === "artifact" ||
    String(content.artifact_id || "").trim()
  ) {
    return "artifact";
  }
  const type = String(content.type || prompt.kind || "mc").toLowerCase();
  if (type === "poll" && !liveChoiceLabels(content).length) {
    return "text";
  }
  return type;
}

/**
 * Compact parent-function radios for the MCR3U C3 Artifact card.
 * @param {any} content
 * @param {string} groupId
 * @returns {string}
 */
function parentChoiceRadiosHtml(content, groupId) {
  const choices = Array.isArray(content?.parent_choices) ? content.parent_choices : [];
  if (!choices.length) return "";
  const picked = String(
    lastArtifactSliders.parent || content.parent?.kind || choices[0]?.kind || ""
  );
  const name = `artifact-parent-${escapeText(groupId)}`;
  const labelId = `${name}-label`;
  return `<div class="artifact-parents-block">
    <p class="artifact-parents-label" id="${labelId}">Parent function</p>
    <div class="artifact-parents" role="radiogroup" aria-labelledby="${labelId}">${choices
    .map((row) => {
      const kind = String(row?.kind || "");
      const label = String(row?.label || kind);
      const on = kind === picked;
      return `<label class="artifact-parent${on ? " is-on" : ""}">
        <input type="radio" name="${name}" data-artifact-parent="${escapeText(kind)}"${
          on ? " checked" : ""
        }>
        <span>${escapeText(label)}</span>
      </label>`;
    })
    .join("")}</div>
  </div>`;
}

/**
 * Return answer controls for a lifecycle question.
 * @param {any} item
 * @param {"individual"|"vote"|"team"} action
 * @param {any} [initial]
 * @returns {string}
 */
function lifecycleAnswerControls(item, action, initial = null) {
  const prompt = item?.prompt || {};
  const content = item?.content || prompt.payload || {};
  const kind = lifecycleAnswerKind(item);
  const current = liveAnswerLabel(initial);
  const prefix = action === "team" ? "Submit Group Answer" : "Submit Answer";
  if (kind === "mc" || kind === "poll") {
    const choices = liveChoiceLabels(content);
    const optionsHtml = Array.isArray(content.options_html) ? content.options_html : [];
    return `<div class="student-live-answer-controls" data-live-action="${action}">
      ${choices
        .map(
          (choice, index) => {
            const optionBody = String(optionsHtml[index] || "").trim()
              ? `<span class="live-question-html">${optionsHtml[index]}</span>`
              : escapeText(choice);
            return `<button type="button" class="prompt-choice${
              String(choice) === current ? " is-selected" : ""
            }" data-live-choice="${escapeText(choice)}">${optionBody}</button>`;
          }
        )
        .join("")}
      <button type="button" class="prompt-submit" data-live-submit="${action}">${prefix}</button>
    </div>`;
  }
  if (kind === "numeric") {
    const value = current === "—" ? "" : current;
    const integerOnly = Boolean(content.integer_only);
    return `<div class="student-live-answer-controls" data-live-action="${action}">
      <label class="prompt-numeric"><span>${integerOnly ? "Enter an integer…" : "Your answer"}</span>
        <input type="number" inputmode="${integerOnly ? "numeric" : "decimal"}" step="${
          integerOnly ? "1" : "any"
        }" data-live-value placeholder="${escapeText(
          content.placeholder || (integerOnly ? "Enter an integer…" : "Enter a number")
        )}" value="${escapeText(value)}">
      </label>
      <button type="button" class="prompt-submit" data-live-submit="${action}">${prefix}</button>
    </div>`;
  }
  if (kind === "artifact") {
    const mode = String(content.target_mode || "graph");
    const equation = String(content.equation || "").trim();
    const keys = Array.isArray(content.slider_keys) ? content.slider_keys : ["a", "h", "k"];
    const snapshot = content.snapshot && typeof content.snapshot === "object" ? content.snapshot : {};
    const showMeters = Boolean(content.hot_cold_visible);
    const accuracyMargin = artifactAccuracyMargin(content);
    const meters = showMeters
      ? keys
          .map((key) => {
            const target = Number(snapshot[key] ?? 0);
            return `<div class="hot-cold-row" data-meter="${escapeText(key)}" data-target="${escapeText(target)}" data-accuracy-margin="${accuracyMargin}">
          <span class="hot-cold-key">${escapeText(key)}</span>
          <span class="hot-cold-track"><span class="hot-cold-fill"></span></span>
        </div>`;
          })
          .join("")
      : "";
    const eqBlock =
      mode === "equation" && equation
        ? `<p class="artifact-equation">${escapeText(equation)}</p>`
        : "";
    const locked = artifactGroupQLocked(content, lastStudentPayload, item);
    return `<div class="student-live-answer-controls" data-live-action="${action}" data-artifact-kind="1" data-group-q="1">
      ${eqBlock}
      ${parentChoiceRadiosHtml(content, `${Number(item.id)}:${action}`)}
      ${showMeters ? `<div class="hot-cold-meters">${meters}</div>` : ""}
      <button type="button" class="prompt-submit" data-live-submit="${action}"${
        locked ? " disabled" : ""
      }>${prefix}</button>
    </div>`;
  }
  const value = current === "—" ? "" : current;
  return `<div class="student-live-answer-controls" data-live-action="${action}">
    <label class="prompt-share"><span>Your answer</span>
      <textarea rows="3" maxlength="2000" data-live-text>${escapeText(value)}</textarea>
    </label>
    <button type="button" class="prompt-submit" data-live-submit="${action}">${prefix}</button>
  </div>`;
}

/**
 * Expand anonymous teammate answers for the discussion list.
 * @param {any} group
 * @returns {string[]}
 */
function lifecycleMemberAnswerValues(group) {
  if (Array.isArray(group?.member_answers) && group.member_answers.length) {
    return group.member_answers.map((value) => String(value));
  }
  const expanded = [];
  for (const row of group?.vote_summary || []) {
    const label = liveAnswerLabel(row.answer);
    const count = Number(row.count) || 0;
    for (let index = 0; index < count; index += 1) {
      expanded.push(label);
    }
  }
  return expanded;
}

/**
 * Render one student's private-response and group-answer consensus state.
 * @param {any} item
 * @returns {string}
 */
function lifecycleConsensusHtml(item) {
  const group = item?.group_consensus || {};
  if (!group.eligible) {
    return `<p class="student-live-note">You joined after voting closed for this question.</p>`;
  }
  const responded = Number(group.vote_count) || 0;
  const eligible = Number(group.eligible_count) || 0;
  const progress = `<p class="student-live-progress">${responded} / ${eligible} teammates have responded</p>`;
  if (group.status === "collecting_votes") {
    return `${progress}${
      group.can_vote
        ? lifecycleAnswerControls(
            item,
            "vote",
            liveCardDrafts.get(`${Number(item.id)}:vote`) || group.my_vote
          )
        : ""
    }`;
  }
  const answers = lifecycleMemberAnswerValues(group);
  const responseList = answers
    .map((value) => `<li>${escapeText(value)}</li>`)
    .join("");
  const discussion = `<section class="student-team-distribution">
    <p class="student-live-card-kicker">TEAM RESPONSES</p>
    <ul>${responseList || "<li>No responses recorded</li>"}</ul>
    <p class="student-live-proposal">Discuss: What should your team's answer be?</p>
  </section>`;
  if (group.status === "finalized") {
    return `${progress}${discussion}<p class="student-live-final">GROUP ANSWER SENT<br><strong>${escapeText(
      liveAnswerLabel(group.final_answer)
    )}</strong></p>`;
  }
  return `${progress}${discussion}${
    group.can_finalize
      ? lifecycleAnswerControls(
          item,
          "team",
          liveCardDrafts.get(`${Number(item.id)}:team`)
        )
      : `<p class="student-live-note">Waiting for a teammate to submit the group answer.</p>`
  }`;
}

/**
 * Render dock chips for media / whiteboard / slides only.
 * Dismissed questions are removed from the student view, not docked.
 * @param {any} _items
 * @param {any} payload
 */
function paintQuestionDock(_items, payload) {
  if (!studentQuestionDock) return;
  const proj = studentProjection(payload);
  const surfaces = [];
  if (proj.media && dockedLiveCardKeys.has("surface:media")) {
    surfaces.push({ key: "surface:media", kind: "media", label: "Media" });
  }
  if (proj.canvas && dockedLiveCardKeys.has("surface:canvas")) {
    surfaces.push({ key: "surface:canvas", kind: "whiteboard", label: "Whiteboard" });
  }
  if (proj.slides && dockedLiveCardKeys.has("surface:slides")) {
    surfaces.push({ key: "surface:slides", kind: "slides", label: "Slides" });
  }
  const chips = surfaces.map(
    (row) => `<div class="student-question-dock-chip is-${row.kind}" data-dock-key="${escapeText(row.key)}">
        <span class="student-question-dock-kind">${escapeText(row.label)}</span>
        <button type="button" class="student-question-dock-pop" data-undock-live-card="${escapeText(row.key)}" aria-label="Pop ${escapeText(row.label)} back out">↗</button>
      </div>`
  );
  studentQuestionDock.hidden = chips.length === 0;
  studentQuestionDock.innerHTML = chips.join("");
}

/**
 * Wrap the legacy published prompt as a lifecycle card when needed.
 * @param {any} payload
 * @returns {any|null}
 */
function promptAsLifecycleItem(payload) {
  const prompt = payload?.prompt;
  if (!prompt || !prompt.kind || prompt.kind === "idle") return null;
  const body = prompt.payload || {};
  const published = (payload?.active_questions || []).find(
    (row) => Number(row?.prompt?.id) === Number(prompt.id)
  );
  const itemId = String(body.item_id || body.pack || "").toLowerCase();
  const inferredStage = itemId.includes("teams-spark")
    ? "teams"
    : itemId.includes("minds_on") || itemId.includes("minds-on")
      ? "join"
      : itemId.includes("meet")
        ? "meet"
        : String(payload?.teacher_state?.stage || "");
  return {
    id: Number(prompt.id) || 0,
    item_id: body.item_id || body.pack || "",
    stage: inferredStage,
    status: "active",
    content: {
      type: prompt.kind,
      text: body.text || body.prompt || body.question || prompt.stem || "",
      ...body,
    },
    prompt,
    can_submit: !payload.my_response,
    my_response: payload.my_response || null,
    response_mode: "individual",
    show_live_results: Boolean(published?.show_live_results),
    results: published?.results || null,
  };
}

/**
 * True when leftover waiting-room minds-on must not intercept a join MC.
 * @param {any} item
 * @param {string} stage
 * @param {boolean} hasPublishedJoinCatalogue
 * @returns {boolean}
 */

/**
 * True when a published join-stage catalogue MC should supersede waiting-room minds-on.
 * @param {any} payload
 * @returns {boolean}
 */
function publishedJoinCatalogueActive(payload) {
  const stage = String(payload?.teacher_state?.stage || "").toLowerCase();
  if (stage !== "join") return false;
  return [...(payload?.active_questions || []), ...(payload?.closed_results || [])].some((item) => {
    const itemId = String(item?.item_id || item?.content?.item_id || "")
      .toLowerCase()
      .replace(/_/g, "-");
    const itemStage = String(item?.stage || item?.content?.stage || "").toLowerCase();
    return itemId && itemId !== "minds-on" && (!itemStage || itemStage === "join");
  });
}

function isLeftoverJoinMindsOnCard(item, stage, hasPublishedJoinCatalogue) {
  const itemId = String(item?.item_id || item?.content?.item_id || "")
    .toLowerCase()
    .replace(/_/g, "-");
  if (itemId !== "minds-on") return false;
  return stage !== "join" || hasPublishedJoinCatalogue;
}

/**
 * True when a lifecycle row is a minted Artifact match challenge.
 * Those cards stay visible even if the stored stage lags the rail page.
 * @param {any} item
 * @returns {boolean}
 */
function isArtifactLifecycleItem(item) {
  const itemId = String(item?.item_id || item?.content?.item_id || "")
    .toLowerCase()
    .replace(/_/g, "-");
  if (itemId.startsWith("artifact-match-")) return true;
  return lifecycleAnswerKind(item) === "artifact";
}

function paintLifecycleQuestionStack(payload) {
  if (!liveQuestionStack) return;
  const grabbedCard = [...activePaneDrags].find((el) =>
    el instanceof HTMLElement && el.classList.contains("student-live-card")
  );
  if (grabbedCard) {
    liveQuestionStack.hidden = false;
    return;
  }
  let active = Array.isArray(payload?.active_questions)
    ? payload.active_questions
    : [];
  const closed = Array.isArray(payload?.closed_results)
    ? payload.closed_results
    : [];
  const legacy = promptAsLifecycleItem(payload);
  const welcomeOn = hasGameShowWelcome(payload);
  if (
    legacy &&
    (welcomeOn || !active.length) &&
    !active.some((item) => Number(item?.prompt?.id || item?.id) === Number(legacy.prompt.id))
  ) {
    active = [legacy, ...active];
  }
  const stage = String(payload?.teacher_state?.stage || "").toLowerCase();
  const hasPublishedJoinCatalogue = publishedJoinCatalogueActive(payload);
  const all = [...active, ...closed].filter((item) => {
    const kind = String(
      item?.content?.item_type || item?.item_type || item?.kind || ""
    ).toLowerCase();
    if (["media", "whiteboard", "slides"].includes(kind)) return false;
    const status = String(item?.status || "active").toLowerCase();
    const itemStage = String(item?.stage || item?.content?.stage || "").toLowerCase();
    const itemId = String(item?.item_id || item?.content?.item_id || "")
      .toLowerCase()
      .replace(/_/g, "-");
    if (isLeftoverJoinMindsOnCard(item, stage, hasPublishedJoinCatalogue)) {
      return false;
    }
    if (
      ["meet-team", "meet-a", "meet-b", "meet-c"].includes(itemId) &&
      stage !== "meet"
    ) {
      return false;
    }
    if (isArtifactLifecycleItem(item)) {
      return status === "active" || status === "closed";
    }
    if (status === "active") {
      return !itemStage || !stage || itemStage === stage;
    }
    return !itemStage || !stage || itemStage === stage;
  });
  const visible = all.filter((item) => {
    const key = liveCardDockKey(item);
    return !dismissedLiveCardKeys.has(key) && !dockedLiveCardKeys.has(key);
  });
  paintQuestionDock(all, payload);
  const floatingCards = new Map();
  document.querySelectorAll(".student-live-card.is-floating").forEach((card) => {
    const key = card instanceof HTMLElement ? card.dataset.liveCardKey || "" : "";
    if (key) floatingCards.set(key, card);
  });
  const visibleKeys = new Set(visible.map((item) => liveCardDockKey(item)));
  floatingCards.forEach((card, key) => {
    if (!visibleKeys.has(key) && !paneIsBeingGrabbed(card)) {
      card.remove();
      floatingCards.delete(key);
    }
  });
  const stacked = visible.filter((item) => !floatingCards.has(liveCardDockKey(item)));
  liveQuestionStackBody.innerHTML = stacked
    .map((item) => {
      const content = item.content || item.prompt?.payload || {};
      const status = String(item.status || "active");
      const groupMode = item.response_mode === "group_consensus";
      const individualControls =
        !groupMode && !item.my_response && item.can_submit
          ? lifecycleAnswerControls(
              item,
              "individual",
              liveCardDrafts.get(`${Number(item.id)}:individual`)
            )
          : "";
      const ownLabel = !groupMode
        ? item.my_response
          ? liveAnswerLabel(item.my_response.response)
          : String(payload?.meet_chip || "").trim()
        : "";
      const showResults =
        status === "closed" ||
        (Boolean(item.show_live_results) &&
          Boolean(
            item.my_response ||
              (ownLabel && ownLabel !== "—") ||
              item.group_consensus?.has_voted
          ));
      const results =
        !showResults
          ? ""
          : groupMode
            ? lifecycleClassConsensusHtml(item.results)
            : lifecycleResultsHtml(item.results);
      const dockKey = liveCardDockKey(item);
      return `<article class="student-live-card student-floating-pane is-${escapeText(status)}" data-live-card-id="${Number(
        item.id
      )}" data-live-prompt-id="${Number(item.prompt?.id) || 0}" data-live-card-status="${escapeText(status)}" data-live-card-key="${escapeText(dockKey)}">
        <div class="student-pane-bar" data-pane-drag="${escapeText(dockKey)}">
          <span>Question</span>
          <span class="student-live-card-status">${status === "closed" ? "Results" : "Active"}</span>
          <button type="button" data-pane-reset="${escapeText(dockKey)}">Reset</button>
          <button type="button" class="student-live-dismiss" data-dismiss-live-card="${escapeText(dockKey)}" aria-label="Dismiss question">×</button>
        </div>
        <button type="button" class="student-pane-resize" data-pane-resize="${escapeText(dockKey)}" aria-label="Resize question"></button>
        <div class="student-live-card-head">
          <span class="student-live-card-kicker">${escapeText(
            String(content.type || item.prompt?.kind || "Question").toUpperCase()
          )}</span>
        </div>
        ${questionImageHtmlStudent(content.image_url)}
        <h2>${lifecyclePromptHtml({
          ...content,
          text: content.title || content.text,
          prompt: content.title || content.text || content.prompt,
        })}</h2>
        ${
          String(content.title || "").trim() &&
          String(content.stem || content.prompt || "").trim() &&
          String(content.stem || content.prompt || "").trim() !==
            String(content.title || "").trim()
            ? `<p class="student-live-stem">${formatPromptHtml(
                content.stem || content.prompt
              )}</p>`
            : ""
        }
        ${lifecycleEquationHtml(content)}
        ${
          groupMode
            ? lifecycleConsensusHtml(item)
            : individualControls
        }
        ${results}
      </article>`;
    })
    .join("");
  void renderLiveQuestionMath(liveQuestionStackBody);
  visible.forEach((item, index) => {
    const key = liveCardDockKey(item);
    const card =
      floatingCards.get(key) ||
      liveQuestionStackBody.querySelector(
        `[data-live-card-key="${CSS.escape(key)}"]`
      );
    if (card instanceof HTMLElement) {
      card.style.setProperty("--live-card-offset", String(index));
      bindFloatingPane(card);
    }
  });
  liveQuestionStack.hidden = visible.length === 0;
  liveQuestionStack.dataset.hasLifecycle = all.length ? "1" : "0";
  const legacyPromptId = Number(payload?.prompt?.id) || 0;
  const lifecycleOwnsLegacy = all.some(
    (item) => Number(item?.prompt?.id) === legacyPromptId
  );
  if (questionFrame && (lifecycleOwnsLegacy || (welcomeOn && visible.length))) {
    questionFrame.hidden = true;
  }
  syncArtifactGroupQLock();
}

/**
 * Read a selected/input answer from one lifecycle card.
 * @param {HTMLElement} card
 * @returns {Record<string, unknown>|null}
 */
function lifecycleAnswerFromCard(card) {
  if (card.querySelector("[data-artifact-kind]")) {
    return { params: { ...lastArtifactSliders } };
  }
  const selected = card.querySelector("[data-live-choice].is-selected");
  if (selected) return { choice: selected.getAttribute("data-live-choice") || "" };
  const numeric = card.querySelector("[data-live-value]");
  if (numeric instanceof HTMLInputElement && numeric.value.trim() !== "") {
    const value = Number(numeric.value);
    if (Number.isFinite(value)) return { value };
  }
  const text = card.querySelector("[data-live-text]");
  if (text instanceof HTMLTextAreaElement && text.value.trim()) {
    return { text: text.value.trim() };
  }
  return null;
}

/**
 * Apply a successful lifecycle submit onto the cached student payload.
 * @param {any} payload
 * @param {any} item
 * @param {any} data
 * @returns {any}
 */
function mergeLifecycleSubmitResponse(payload, item, data) {
  if (!payload || !item || !data) return payload;
  const promptId = Number(item.prompt?.id) || 0;
  const itemId = Number(item.id) || 0;
  const myResponse = data.my_response || null;
  const allowTally =
    String(item.status || "") === "closed" || item.show_live_results !== false;
  const tally = allowTally ? data.mc_tally : null;
  const active = Array.isArray(payload.active_questions)
    ? payload.active_questions.slice()
    : [];
  let touched = false;
  const activeQuestions = active.map((row) => {
    const rowPromptId = Number(row?.prompt?.id) || 0;
    const rowItemId = Number(row.id) || 0;
    if (rowPromptId !== promptId && rowItemId !== itemId) return row;
    touched = true;
    const results =
      tally && Array.isArray(tally.choices) && tally.choices.length
        ? { kind: tally.kind || "mc", choices: tally.choices }
        : row.results;
    return {
      ...row,
      my_response: myResponse || row.my_response,
      can_submit: false,
      results: results || row.results,
    };
  });
  if (!touched) return payload;
  return {
    ...payload,
    my_response: myResponse || payload.my_response,
    mc_tally: tally || payload.mc_tally,
    active_questions: activeQuestions,
  };
}


/**
 * Submit an individual response, private vote, or canonical Team Answer.
 * @param {HTMLElement} card
 * @param {"individual"|"vote"|"team"} action
 */
async function submitLifecycleAnswer(card, action) {
  const itemId = Number(card.dataset.liveCardId) || 0;
  const flightKey = `${itemId}:${action}`;
  if (liveSubmitInFlight.has(flightKey)) return;
  liveSubmitInFlight.add(flightKey);
  const promptIdFromCard = Number(card.dataset.livePromptId) || 0;
  const item =
    (lastStudentPayload?.active_questions || []).find(
      (row) => Number(row.id) === itemId
    ) ||
    (lastStudentPayload?.active_questions || []).find(
      (row) => Number(row?.prompt?.id) === promptIdFromCard
    ) ||
    (Number(lastStudentPayload?.prompt?.id) === itemId
      ? promptAsLifecycleItem(lastStudentPayload)
      : null);
  const response = lifecycleAnswerFromCard(card);
  try {
    if (!response) return;
    if (!item) {
      const promptId = promptIdFromCard || Number(lastStudentPayload?.prompt?.id) || itemId;
      if (!promptId) return;
      await submitResponse(promptId, response);
      return;
    }
    let url = "/api/student/live-prompt/response";
    let body = { prompt_id: Number(item.prompt?.id) || promptIdFromCard || 0, response };
    if (action === "vote") {
      url = `/api/student/live-items/${itemId}/vote`;
      body = { response };
    } else if (action === "team") {
      url = `/api/student/live-items/${itemId}/team-answer`;
      body = { response };
    }
    const res = await fetch(
      url,
      visitFetchInit({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(body),
      })
    );
    const data = await res.json();
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || "Could not submit that answer.");
    }
    liveCardDrafts.delete(`${itemId}:${action}`);
    if (lastStudentPayload) {
      lastStudentPayload = mergeLifecycleSubmitResponse(lastStudentPayload, item, data);
      paintLifecycleQuestionStack(lastStudentPayload);
    }
    await tick();
  } finally {
    liveSubmitInFlight.delete(flightKey);
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
    if (promptDismiss) promptDismiss.hidden = true;
    return;
  }
  const prompt = payload.prompt;
  const data = (prompt && prompt.payload) || {};
  const isMeet = String(data.ride || "") === "meet_team" || String(data.pack || "") === "meet-team";
  const isSpark = isTeamsSparkPrompt(payload);
  const answered = Boolean(payload.my_response);
  const promptId = Number(prompt?.id) || 0;
  if (answered && promptId && dismissedPromptIds.has(promptId)) {
    hideQuestionBody();
    hideFeedbackPanel();
    if (promptPollTotals) promptPollTotals.hidden = true;
    if (promptDismiss) promptDismiss.hidden = true;
    if (questionFrame) questionFrame.hidden = true;
    lastPromptId = promptId;
    return;
  }
  if (holdJoinFeedback && !isJoinMindsOnPrompt(payload)) {
    return;
  }
  if (isJoinMindsOnPrompt(payload) && publishedJoinCatalogueActive(payload)) {
    if (questionFrame) questionFrame.hidden = true;
    if (promptShell) {
      promptShell.hidden = true;
      promptShell.innerHTML = "";
    }
    lastPromptId = null;
    return;
  }
  const summary = studentMcSummary(payload);
  const closedFacing = studentPollClosed(payload);
  if (
    closedFacing &&
    summary &&
    prompt &&
    prompt.kind &&
    prompt.kind !== "idle" &&
    !answered
  ) {
    hideFeedbackPanel();
    if (promptAck) {
      promptAck.hidden = true;
      promptAck.classList.remove("is-feedback");
    }
    renderPromptBody(prompt, data, payload, true);
    lastFeedbackKey = "";
    lastSummarySig = studentSummarySig(payload);
    if (promptDismiss) promptDismiss.hidden = true;
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
    if (promptDismiss) promptDismiss.hidden = true;
    return;
  }
  if (questionFrame) {
    questionFrame.hidden = !studentProjection(payload).questions;
  }
  if (answered) {
    const fb = !isMeet && !isSpark ? feedbackObject(payload.my_response) : null;
    const key = `${prompt.id}:${(fb && fb.lead) || ""}:${(fb && fb.text) || ""}`;
    hideQuestionBody();
    hidePromptAck();
    lastPromptId = Number(prompt.id);
    lastMeetSig = `${prompt.id}:${data.step || ""}:${data.chain_index || ""}`;
    lastSummarySig = studentSummarySig(payload);
    if (promptDismiss) {
      promptDismiss.hidden = String(prompt.kind || "") !== "mc";
    }
    if (fb && !feedbackDismissed) {
      if (key !== lastFeedbackKey || (promptFeedback && promptFeedback.hidden)) {
        showFeedbackPanel(fb);
        lastFeedbackKey = key;
      }
      hidePollTotals();
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
  if (promptDismiss) promptDismiss.hidden = true;
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
  const mintedTitle = String(data.title || "").trim();
  const title = mintedTitle
    ? formatPromptHtml(mintedTitle)
    : lifecyclePromptHtml(data);
  const mintedStem =
    mintedTitle &&
    String(data.stem || data.prompt || "").trim() &&
    String(data.stem || data.prompt || "").trim() !== mintedTitle
      ? `<p class="student-live-stem">${formatPromptHtml(
          data.stem || data.prompt
        )}</p>`
      : "";
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
          const optionHtml = Array.isArray(data.options_html) && data.options_html[index]
            ? `<span class="live-question-html">${String(data.options_html[index])}</span>`
            : formatQuestionHtml(label);
          return `<button type="button" class="prompt-choice${on}" data-choice="${escapeText(choice)}"${
            picked && lockChoices ? " disabled" : ""
          }>${optionHtml}</button>`;
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
    const integerOnly = true;
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
  } else if (kind === "artifact") {
    const mode = String(data.target_mode || "graph");
    const equation = String(data.equation || "").trim();
    const keys = Array.isArray(data.slider_keys) ? data.slider_keys : ["a", "h", "k"];
    const snapshot = data.snapshot && typeof data.snapshot === "object" ? data.snapshot : {};
    const showMeters = Boolean(data.hot_cold_visible);
    const accuracyMargin = artifactAccuracyMargin(data);
    const meters = showMeters
      ? keys
          .map((key) => {
            const target = Number(snapshot[key] ?? 0);
            return `<div class="hot-cold-row" data-meter="${escapeText(key)}" data-target="${escapeText(target)}" data-accuracy-margin="${accuracyMargin}">
          <span class="hot-cold-key">${escapeText(key)}</span>
          <span class="hot-cold-track"><span class="hot-cold-fill"></span></span>
        </div>`;
          })
          .join("")
      : "";
    const eqBlock =
      mode === "equation" && equation
        ? `<p class="artifact-equation">${escapeText(equation)}</p>`
        : "";
    const artifactItem = (payload.active_questions || []).find(
      (row) => Number(row?.prompt?.id) === Number(prompt.id)
    );
    const locked = lockChoices || artifactGroupQLocked(data, payload, artifactItem);
    controls = `
      <div data-artifact-kind="1" data-group-q="1">
      ${eqBlock}
      ${parentChoiceRadiosHtml(data, "prompt")}
      ${showMeters ? `<div class="hot-cold-meters" id="artifact-meters">${meters}</div>` : ""}
      <button type="button" class="prompt-submit" id="prompt-artifact-submit"${
        locked ? " disabled" : ""
      }>Submit Answer</button>
      </div>
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
  promptShell.hidden = false;
  promptShell.innerHTML = `
    <p class="prompt-kind">${escapeText(kindLine)}</p>
    <h2 class="prompt-title">${title}</h2>
    ${mintedStem}
    <div class="prompt-controls" data-prompt-id="${escapeText(prompt.id)}">${controls}</div>
  `;
  lastPromptId = Number(prompt.id);
  lastMeetSig = `${prompt.id}:${data.step || ""}:${data.chain_index || ""}`;
  lastSummarySig = studentSummarySig(payload);
  if (!lockChoices && !studentPollClosed(payload)) {
    wirePromptControls(prompt);
  }
  void renderLiveQuestionMath(promptShell);
  if (kind === "artifact") {
    applyArtifactPreview(lastArtifactSliders);
    syncArtifactGroupQLock();
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
  const artifactSubmit = root.querySelector("#prompt-artifact-submit");
  if (artifactSubmit) {
    artifactSubmit.addEventListener("click", () => {
      if (artifactSubmit.disabled) return;
      submitResponse(prompt.id, { params: { ...lastArtifactSliders } });
    });
  }
  root.querySelectorAll("[data-artifact-parent]").forEach((radio) => {
    radio.addEventListener("change", () => {
      if (!(radio instanceof HTMLInputElement) || !radio.checked) return;
      lastArtifactSliders = {
        ...lastArtifactSliders,
        parent: radio.getAttribute("data-artifact-parent") || "",
      };
      radio
        .closest(".artifact-parents")
        ?.querySelectorAll(".artifact-parent")
        .forEach((label) => {
          label.classList.toggle("is-on", label.contains(radio));
        });
    });
  });
}

/**
 * Artifact match band from a minted payload. Only 10% or 20%.
 * @param {any} content
 * @returns {number}
 */
function artifactAccuracyMargin(content) {
  const raw = content && typeof content === "object" ? content.accuracy_margin : 0.1;
  const number = Number(raw);
  return Math.abs(number - 0.2) < 0.001 ? 0.2 : 0.1;
}

/**
 * Live hot/cold chrome for one Artifact slider. Silent — no chatter.
 * @param {string} key
 * @param {number} student
 * @param {number} target
 * @param {number} [margin]
 */
function paintHotColdMeter(key, student, target, margin) {
  const host = document.getElementById("live-response");
  const roots = host ? [host] : [promptShell, liveQuestionStack].filter(Boolean);
  for (const root of roots) {
    root.querySelectorAll(`[data-meter="${key}"]`).forEach((row) => {
      const fill = row.querySelector(".hot-cold-fill");
      if (!(fill instanceof HTMLElement)) return;
      const rowMargin = Number(row.getAttribute("data-accuracy-margin") || margin || 0.1);
      const allowed =
        (Math.abs(rowMargin - 0.2) < 0.001 ? 0.2 : 0.1) * Math.max(Math.abs(target), 1);
      const err = Math.abs(Number(student) - Number(target));
      row.classList.remove(
        "is-cold",
        "is-hot",
        "is-hc-cold",
        "is-hc-near",
        "is-hc-very",
        "is-hc-hot"
      );
      let band = "is-hc-cold";
      if (err <= allowed) band = "is-hc-hot";
      else if (err <= allowed * 2.5) band = "is-hc-very";
      else if (err <= allowed * 5) band = "is-hc-near";
      row.classList.add(band);
      const heat = Math.max(0, Math.min(1, 1 - err / (allowed * 6)));
      fill.style.width = `${Math.round(heat * 100)}%`;
    });
  }
}

/**
 * True when the live class is running questions as groups.
 * @param {any} payload
 * @returns {boolean}
 */
function artifactGroupsRunning(payload) {
  const bag = payload && typeof payload === "object" ? payload : {};
  if (String(bag.question_view || "") === "team") return true;
  const teacher = bag.teacher_state;
  return Boolean(teacher && teacher.run_as_group);
}

/**
 * True when Group Q submit must wait for every teammate to match.
 * @param {any} content
 * @param {any} payload
 * @param {any} [item]
 * @returns {boolean}
 */
function artifactGroupQLocked(content, payload, item) {
  const body = content && typeof content === "object" ? content : {};
  if (!body.group_q) return false;
  if (!artifactGroupsRunning(payload)) return false;
  if (item && typeof item.group_q_ready === "boolean") {
    return !item.group_q_ready;
  }
  const bag = payload && typeof payload === "object" ? payload : {};
  return !bag.group_q_ready;
}

/**
 * Disable Artifact Submit Answer until every teammate matches.
 */
function syncArtifactGroupQLock() {
  const payload = lastStudentPayload || {};
  const questions = Array.isArray(payload.active_questions)
    ? payload.active_questions
    : [];
  const shellBtn = document.querySelector("#prompt-artifact-submit");
  if (shellBtn instanceof HTMLButtonElement) {
    const content =
      payload.prompt && typeof payload.prompt.payload === "object"
        ? payload.prompt.payload
        : {};
    const item = questions.find(
      (row) => Number(row?.prompt?.id) === Number(payload.prompt?.id)
    );
    shellBtn.disabled = artifactGroupQLocked(content, payload, item);
  }
  document.querySelectorAll("[data-artifact-kind][data-group-q]").forEach((root) => {
    const card = root.closest("[data-live-card-id]");
    const cardId = Number(card instanceof HTMLElement ? card.dataset.liveCardId : 0);
    const promptId = Number(
      card instanceof HTMLElement ? card.dataset.livePromptId : 0
    );
    const item =
      questions.find((row) => Number(row?.id) === cardId) ||
      questions.find((row) => Number(row?.prompt?.id) === promptId);
    const content =
      item?.content ||
      item?.prompt?.payload ||
      (payload.prompt && payload.prompt.payload) ||
      {};
    const btn = root.querySelector("#prompt-artifact-submit, [data-live-submit], .prompt-submit");
    if (btn instanceof HTMLButtonElement) {
      btn.disabled = artifactGroupQLocked(content, payload, item);
    }
  });
}

/**
 * POST live Artifact sliders so Group Q can see this student's match.
 * @param {Record<string, unknown>} sliders
 */
function postArtifactSliderPreview(sliders) {
  const questions = lastStudentPayload?.active_questions || [];
  const artifactCard = questions.find(
    (row) =>
      String(row?.content?.kind || row?.prompt?.kind || row?.prompt?.payload?.kind || "") ===
      "artifact"
  );
  const promptId =
    Number(artifactCard?.prompt?.id) || Number(lastStudentPayload?.prompt?.id) || 0;
  if (!promptId) return;
  fetch(
    "/api/student/live-prompt/artifact-preview",
    visitFetchInit({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ prompt_id: promptId, params: sliders }),
    })
  )
    .then((res) => res.json())
    .then((data) => {
      if (data && data.ok && lastStudentPayload) {
        lastStudentPayload.group_q_ready = Boolean(data.group_q_ready);
        (lastStudentPayload.active_questions || []).forEach((row) => {
          if (Number(row?.prompt?.id) === promptId) {
            row.group_q_ready = Boolean(data.group_q_ready);
          }
        });
      }
      syncArtifactGroupQLock();
    })
    .catch(() => {});
}

/**
 * Apply a student slider preview to the meters under the question.
 * @param {Record<string, unknown>} sliders
 */
function applyArtifactPreview(sliders) {
  if (!sliders || typeof sliders !== "object") return;
  const next = { ...lastArtifactSliders };
  Object.entries(sliders).forEach(([key, value]) => {
    if (key === "parent") {
      next.parent = String(value || "");
      return;
    }
    const number = Number(value);
    if (Number.isFinite(number)) next[key] = number;
  });
  lastArtifactSliders = next;
  postArtifactSliderPreview(lastArtifactSliders);
  const host = document.getElementById("live-response");
  const roots = host ? [host] : [promptShell, liveQuestionStack].filter(Boolean);
  if (!roots.length) return;
  roots.forEach((root) => {
    root.querySelectorAll("[data-meter]").forEach((row) => {
      const key = row.getAttribute("data-meter") || "";
      const target = Number(row.getAttribute("data-target") || 0);
      if (!key) return;
      paintHotColdMeter(key, Number(lastArtifactSliders[key] ?? 0), target);
    });
  });
}

/**
 * Student-safe feedback object from submit JSON or my_response.
 * Leftover keyed miss/hit payloads stay hidden; cards never paint lead/why.
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
  return null;
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
  const item =
    (payload?.active_questions || []).find(
      (row) => Number(row?.prompt?.id) === Number(payload?.prompt?.id)
    ) || null;
  if (item && item.show_live_results === false) {
    promptPollTotals.hidden = true;
    promptPollTotals.innerHTML = "";
    return;
  }
  const meet = studentMeetPollsHtml(payload);
  const tally = studentMcSummary(payload);
  if (!meet && !tally) {
    promptPollTotals.hidden = true;
    promptPollTotals.innerHTML = "";
    return;
  }
  promptPollTotals.hidden = false;
  promptPollTotals.innerHTML = meet || `<section class="prompt-poll-card"><p class="meet-poll-kicker">Class results</p>${mcRevealBarsHtml(tally)}</section>`;
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
  const item =
    (payload?.active_questions || []).find((row) => {
      const id = String(row?.item_id || row?.content?.id || "").replace(/_/g, "-");
      return id === "meet-team" || Number(row?.prompt?.id) === Number(payload?.prompt?.id);
    }) || null;
  if (item && item.show_live_results === false) return "";
  const ts = (payload && payload.teacher_state) || {};
  if (String(ts.stage || "") !== "meet") return "";
  const tally = (item && item.results) || payload?.mc_tally;
  if (!tally) return "";
  const labels = [];
  const classCounts = {};
  (tally.choices || []).forEach((row) => {
    const label = typeof row === "string" ? row : String(row.label || row.text || "");
    if (!label) return;
    labels.push(label);
    classCounts[label] = Number(row.count || row.n || 0);
  });
  if (!labels.length) return "";
  return `<section class="prompt-poll-card prompt-poll-class"><p class="meet-poll-kicker">Class results</p>${countsToTallyHtml(
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
 * Close a submitted MC/poll, including its compact feedback or result card.
 */
function dismissFeedbackPanel() {
  hideFeedbackPanel();
  holdJoinFeedback = false;
  feedbackDismissed = true;
  const promptId = Number(lastStudentPayload?.prompt?.id || lastPromptId) || 0;
  if (promptId) dismissedPromptIds.add(promptId);
  if (promptPollTotals) {
    promptPollTotals.hidden = true;
    promptPollTotals.innerHTML = "";
  }
  if (promptDismiss) promptDismiss.hidden = true;
  if (questionFrame) questionFrame.hidden = true;
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
        hidePollTotals();
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
  const summaryOn = String((payload.teacher_state || {}).stage || "") === "summary";
  const showWinner = Boolean(payload.celebrate) || summaryOn || Boolean(payload.summary_winner);
  const winnerPlayersEl = document.getElementById("student-winner-players");
  if (!showWinner) {
    host.hidden = true;
    celebratePainted = false;
    if (graffiti) graffiti.innerHTML = "";
    if (form) form.hidden = true;
    if (winnerBanner) winnerBanner.hidden = true;
    if (winnerNameEl) winnerNameEl.textContent = "";
    if (winnerPlayersEl) winnerPlayersEl.textContent = "";
    document.body.classList.remove("is-celebrating", "is-summary-winner");
    return;
  }
  host.hidden = false;
  document.body.classList.toggle("is-celebrating", Boolean(payload.celebrate));
  document.body.classList.toggle("is-summary-winner", showWinner && !payload.celebrate);
  const teamName = String((payload.winner && payload.winner.name) || "").trim();
  if (winnerNameEl) winnerNameEl.textContent = teamName || "Winner";
  if (winnerPlayersEl) {
    const players = Array.isArray(payload.winner && payload.winner.players)
      ? payload.winner.players
      : [];
    const names = players
      .map((row) => String((row && (row.codename || row.first_name || row.name)) || "").trim())
      .filter(Boolean);
    winnerPlayersEl.textContent = names.join(" · ");
  }
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
    const params = new URLSearchParams();
    if (lastStateSeq >= 0) params.set("seq", String(lastStateSeq));
    if (lastPollStamp) params.set("stamp", lastPollStamp);
    const qs = params.toString() ? `?${params}` : "";
    const res = await fetch(`/api/student/state${qs}`, visitFetchInit());
    const data = await res.json();
    if (data.unchanged) {
      if (data.stamp) lastPollStamp = String(data.stamp);
      if (data.state_seq != null) lastStateSeq = Number(data.state_seq);
      return;
    }
    if (data.stamp) lastPollStamp = String(data.stamp);
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
    if (![...activePaneDrags].some((el) => el.classList.contains("student-live-card"))) {
      paintLifecycleQuestionStack(data);
    }
  } catch (_err) {
    /* keep last paint */
  }
}

document.getElementById("live-response")?.addEventListener("click", (event) => {
  const dismissSurface = event.target.closest("[data-dismiss-surface]");
  if (dismissSurface instanceof HTMLButtonElement) {
    event.preventDefault();
    dockedLiveCardKeys.add(`surface:${dismissSurface.dataset.dismissSurface || ""}`);
    if (lastStudentPayload) {
      applyTeacherProjection(lastStudentPayload);
      paintMedia(lastStudentPayload);
      paintLifecycleQuestionStack(lastStudentPayload);
    }
    return;
  }
  if (
    event.target.closest("[data-pane-drag]") &&
    !event.target.closest("button")
  ) {
    event.preventDefault();
    return;
  }
  const choice = event.target.closest("[data-live-choice]");
  if (choice instanceof HTMLButtonElement) {
    const controls = choice.closest(".student-live-answer-controls");
    controls?.querySelectorAll("[data-live-choice]").forEach((button) => {
      button.classList.toggle("is-selected", button === choice);
    });
    const card = choice.closest("[data-live-card-id]");
    const action = controls?.getAttribute("data-live-action") || "individual";
    if (card instanceof HTMLElement) {
      liveCardDrafts.set(`${Number(card.dataset.liveCardId)}:${action}`, {
        choice: choice.getAttribute("data-live-choice") || "",
      });
    }
    return;
  }
  const dismissCard = event.target.closest("[data-dismiss-live-card]");
  if (dismissCard instanceof HTMLButtonElement) {
    event.stopPropagation();
    dismissedLiveCardKeys.add(dismissCard.dataset.dismissLiveCard || "");
    const floating = dismissCard.closest(".student-live-card");
    if (floating instanceof HTMLElement && floating.classList.contains("is-floating")) {
      floating.remove();
    }
    paintLifecycleQuestionStack(lastStudentPayload || {});
    return;
  }
  const parentPick = event.target.closest("[data-artifact-parent]");
  if (parentPick instanceof HTMLInputElement) {
    lastArtifactSliders = {
      ...lastArtifactSliders,
      parent: parentPick.getAttribute("data-artifact-parent") || "",
    };
    parentPick
      .closest(".artifact-parents")
      ?.querySelectorAll(".artifact-parent")
      .forEach((label) => {
        label.classList.toggle("is-on", label.contains(parentPick));
      });
    return;
  }
  const submit = event.target.closest("[data-live-submit]");
  const card = submit?.closest("[data-live-card-id]");
  if (!(submit instanceof HTMLButtonElement) || !(card instanceof HTMLElement)) return;
  if (submit.disabled) return;
  submit.disabled = true;
  submitLifecycleAnswer(card, submit.dataset.liveSubmit || "individual")
    .catch((err) => {
      let note = card.querySelector(".student-live-submit-error");
      if (!note) {
        note = document.createElement("p");
        note.className = "student-live-submit-error";
        card.appendChild(note);
      }
      note.textContent = err instanceof Error ? err.message : "Could not submit.";
      submit.disabled = false;
    });
});
document.getElementById("live-response")?.addEventListener("input", (event) => {
  const field = event.target;
  const card = field.closest?.("[data-live-card-id]");
  const controls = field.closest?.("[data-live-action]");
  if (!(card instanceof HTMLElement) || !(controls instanceof HTMLElement)) return;
  const key = `${Number(card.dataset.liveCardId)}:${
    controls.dataset.liveAction || "individual"
  }`;
  if (field instanceof HTMLInputElement) {
    liveCardDrafts.set(key, { value: field.value });
  } else if (field instanceof HTMLTextAreaElement) {
    liveCardDrafts.set(key, { text: field.value });
  }
});

if (studentQuestionDock) {
  studentQuestionDock.addEventListener("click", (event) => {
    const undock = event.target.closest("[data-undock-live-card]");
    if (!(undock instanceof HTMLButtonElement)) return;
    dockedLiveCardKeys.delete(undock.dataset.undockLiveCard || "");
    if (lastStudentPayload) {
      applyTeacherProjection(lastStudentPayload);
      paintMedia(lastStudentPayload);
      paintLifecycleQuestionStack(lastStudentPayload);
    }
  });
}

if (promptFeedbackClose) {
  promptFeedbackClose.addEventListener("click", dismissFeedbackPanel);
}
if (promptDismiss) {
  promptDismiss.addEventListener("click", dismissFeedbackPanel);
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

window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin) return;
  const data = event.data;
  const artifactSources = new Set([
    "lloves-m1c2-transforms",
    "lloves-mcr3u-m1c3-parents",
  ]);
  if (!data || !artifactSources.has(data.source)) return;
  if (data.type === "artifact-preview") {
    applyArtifactPreview(data.sliders || {});
  }
});

bindStudentCanvas();
bindFloatingPane(mediaPane);
bindFloatingPane(canvasPane);
bindFloatingPane(slidesPane);
tick();
setInterval(tick, 4000);
setInterval(tickDisplayTime, 250);

const bootCodename = body && body.dataset ? body.dataset.codename : "";
if (bootCodename) {
  setTabTitle(bootCodename);
}
