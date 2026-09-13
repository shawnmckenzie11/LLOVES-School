/**
 * Phone-first student live-class home: Live response shell + chrome boards.
 */
const waitEl = document.getElementById("student-wait");
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
/** @type {number} */
let lastStateSeq = -1;
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
  "cue.freeze": "Park the wonderings. Leave the blank honest.",
  "cue.cons_unlock": "Argue’s parked. Time to name what this picture forced.",
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
 * Updates identity + stats nodes only — never remounts Save Work.
 * @param {any} payload
 */
function paintMe(payload) {
  if (!meEl) return;
  const me = payload.me || {};
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
  const showRank = Boolean(payload.show_rank && me.rank);
  if (meRankEl) meRankEl.hidden = !showRank;
  if (meRankValueEl) meRankValueEl.textContent = showRank ? String(me.rank) : "";
  if (meRankOfEl) {
    const of = showRank && me.rank_of ? `/ ${me.rank_of}` : "";
    meRankOfEl.textContent = of;
    meRankOfEl.hidden = !of;
  }
  setTabTitle(String(me.codename || ""));
}

/**
 * Show a short Save Work status under the name row.
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
  if (!(canvas instanceof HTMLCanvasElement) || !canvas.width || !canvas.height) {
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
 * Save the visible student canvas + media as one stacked PNG.
 * Hidden / unchecked frames are omitted. No gradebook write.
 */
async function saveStudentWork() {
  /** @type {{dataUrl: string, width: number, height: number}[]} */
  const layers = [];
  if (paneIsMounted(canvasPane)) {
    const dataUrl = canvasPngDataUrl(studentCanvas);
    if (dataUrl && studentCanvas) {
      layers.push({
        dataUrl,
        width: studentCanvas.width,
        height: studentCanvas.height,
      });
    }
  }
  if (paneIsMounted(mediaPane)) {
    const media = captureMediaFrame(mediaFrame);
    if (media) layers.push(media);
  }
  if (!layers.length) {
    showSaveWorkToast(SAVE_WORK_EMPTY);
    return;
  }
  try {
    const images = await Promise.all(layers.map((layer) => loadPngImage(layer.dataUrl)));
    const width = Math.max(...images.map((img) => img.naturalWidth || img.width));
    const height = images.reduce(
      (sum, img) => sum + (img.naturalHeight || img.height),
      0
    );
    const out = document.createElement("canvas");
    out.width = Math.max(1, width);
    out.height = Math.max(1, height);
    const ctx = out.getContext("2d");
    if (!ctx) {
      showSaveWorkToast(SAVE_WORK_EMPTY);
      return;
    }
    ctx.fillStyle = "#0b1020";
    ctx.fillRect(0, 0, out.width, out.height);
    let y = 0;
    for (const img of images) {
      const h = img.naturalHeight || img.height;
      ctx.drawImage(img, 0, y);
      y += h;
    }
    downloadPng(out.toDataURL("image/png"), "live-class-work.png");
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
  const frames = ts.student_frames || {};
  const unlocks = ts.unlocks || {};
  const stage = String(ts.stage || "join");
  const seq = Number(ts.state_seq);
  return {
    stage,
    seq: Number.isFinite(seq) ? seq : 0,
    questions: frames.questions !== false,
    media: Boolean(unlocks.media),
    canvas: Boolean(unlocks.canvas),
    unlockMedia: Boolean(unlocks.media),
    unlockCanvas: Boolean(unlocks.canvas),
    canvasAlign: String(ts.canvas_align || "student"),
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
  const ctx = studentCanvas.getContext("2d");
  if (!ctx) return;
  let drawing = false;
  let strokeId = "";
  let lastAlign = "student";
  const point = (event) => {
    const rect = studentCanvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * studentCanvas.width,
      y: ((event.clientY - rect.top) / rect.height) * studentCanvas.height,
    };
  };
  const postPresence = (p, ended) => {
    if (lastAlign === "student" || lastAlign === "teacher") return;
    const norm = { x: p.x / studentCanvas.width, y: p.y / studentCanvas.height };
    fetch(
      "/api/student/canvas-presence",
      visitFetchInit({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          x: norm.x,
          y: norm.y,
          point: [norm.x, norm.y],
          stroke_id: strokeId || undefined,
          ended: Boolean(ended),
        }),
      })
    ).catch(() => {});
  };
  studentCanvas.addEventListener("pointerdown", (event) => {
    if (lastAlign === "teacher") return;
    if (canvasLock && !canvasLock.hidden) return;
    drawing = true;
    strokeId = `s-${Date.now()}`;
    const p = point(event);
    if (lastAlign === "student" || lastAlign === "team") {
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
    }
    studentCanvas.setPointerCapture(event.pointerId);
    postPresence(p, false);
  });
  studentCanvas.addEventListener("pointermove", (event) => {
    if (!drawing || lastAlign === "teacher") return;
    const p = point(event);
    if (lastAlign === "student" || lastAlign === "team") {
      ctx.lineTo(p.x, p.y);
      ctx.strokeStyle = "#12202e";
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    postPresence(p, false);
  });
  studentCanvas.addEventListener("pointerup", (event) => {
    if (drawing) postPresence(point(event), true);
    drawing = false;
    strokeId = "";
  });
  bindStudentCanvas.setAlign = (align) => {
    lastAlign = String(align || "student");
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
  if (proj.seq !== lastStateSeq) {
    lastStateSeq = proj.seq;
  }
  if (questionFrame) {
    questionFrame.hidden = !proj.questions;
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
  const live = Boolean(payload.scoring);
  const proj = studentProjection(payload);
  const hasMedia = proj.media && Boolean(payload.active_media && payload.active_media.url);
  const waitingRoom = isWaitingRoom(payload);
  const ts = (payload && payload.teacher_state) || {};
  const meetOn = proj.stage === "meet" && Boolean(ts.meet_chain);
  body.classList.toggle("is-live", live);
  body.classList.toggle("has-media", hasMedia);
  body.classList.toggle("is-waiting-room", waitingRoom);
  const hasPrompt = Boolean(payload.prompt && payload.prompt.kind && payload.prompt.kind !== "idle");
  if (waitEl) {
    // Waiting-room keeps Wonder's line even when the Minds-On question is showing.
    // MEET hides leftover scoring-wait chrome — the Question frame holds the chain.
    if ((!waitingRoom && (hasPrompt || hasMedia)) || meetOn) {
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
  // C1 Real-slice already chips the ask inside the frame; do not double it.
  const iframeOwnsAsk = url.includes("m1c1-c1-real-slice.html");
  if (mediaChip) {
    const chip = String(
      (media && (media.chip || media.entry_chip)) || ""
    ).trim();
    mediaChip.textContent = chip;
    mediaChip.hidden = !chip || iframeOwnsAsk;
  }
  if (mediaStem) {
    const stem = String((media && media.stem) || "").trim();
    mediaStem.textContent = stem;
    mediaStem.hidden = !stem || iframeOwnsAsk;
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
  if (!MEET_CUES.has(cue) && !TEXT_RIDE_CUES.has(cue) && !TEAMS_SPARK_CUES.has(cue)) return;
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
  return Boolean(ui.poll_closed) || Boolean(studentMcSummary(payload));
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
      return `<div class="mc-reveal-row"><span class="mc-reveal-letter">${id}</span><p class="mc-reveal-label">${label}</p><span class="mc-reveal-meta">${escapeText(
        String(row.count ?? 0)
      )} · ${pct}%</span><span class="mc-reveal-track"><span class="mc-reveal-fill" style="width:${pct}%"></span></span></div>`;
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
function paintPrompt(payload) {
  if (!promptShell) return;
  const ts = (payload && payload.teacher_state) || {};
  if (String(ts.stage || "") === "teams" && isJoinMindsOnPrompt(payload)) {
    payload = { ...payload, prompt: null, my_response: null };
  }
  const prompt = payload.prompt;
  const data = (prompt && prompt.payload) || {};
  const isMeet = String(data.ride || "") === "meet_team" || String(data.pack || "") === "meet-team";
  const isSpark = isTeamsSparkPrompt(payload);
  const answered = Boolean(payload.my_response);
  const summary = studentMcSummary(payload);
  if (summary && prompt && prompt.kind && prompt.kind !== "idle") {
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
    hideFeedbackPanel();
    if (promptAck) {
      promptAck.hidden = true;
      promptAck.classList.remove("is-feedback");
    }
    return;
  }
  if (answered && !isMeet && !isSpark) {
    const fb = feedbackObject(payload.my_response);
    const key = `${prompt.id}:${(fb && fb.lead) || ""}:${(fb && fb.text) || ""}`;
    if (key === lastFeedbackKey && (feedbackDismissed || (promptFeedback && !promptFeedback.hidden))) {
      lastPromptId = Number(prompt.id);
      return;
    }
    if (fb) {
      renderPromptBody(prompt, data, payload, true);
      if (key !== lastFeedbackKey || !feedbackDismissed) {
        showFeedbackPanel(fb);
        lastFeedbackKey = key;
      }
      lastPromptId = Number(prompt.id);
      lastMeetSig = `${prompt.id}:${data.step || ""}:${data.chain_index || ""}`;
      return;
    }
    hideFeedbackPanel();
    promptShell.hidden = true;
    promptShell.innerHTML = "";
    showPromptAck("");
    lastPromptId = Number(prompt.id);
    lastFeedbackKey = key;
    return;
  }
  hideFeedbackPanel();
  lastFeedbackKey = "";
  feedbackDismissed = false;
  if (promptAck) {
    promptAck.hidden = true;
    promptAck.classList.remove("is-feedback");
  }
  const picked = String(
    (payload.my_response && payload.my_response.response && payload.my_response.response.choice) ||
      ""
  ).trim();
  renderPromptBody(prompt, data, payload, Boolean(picked));
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
      ""
  ).trim();
  let controls = "";
  if (kind === "mc") {
    const summary = studentMcSummary(payload);
    const closed = studentPollClosed(payload) || lockChoices;
    if (summary) {
      controls = mcRevealBarsHtml(summary);
    } else {
      const choices = Array.isArray(data.choices) ? data.choices : ["A", "B", "C", "D"];
      controls = choices
        .map((choice, index) => {
          const label = typeof choice === "string" ? choice : `Option ${index + 1}`;
          const on = picked && label === picked ? " is-selected" : "";
          return `<button type="button" class="prompt-choice${on}" data-choice="${escapeText(choice)}"${
            picked || closed ? " disabled" : ""
          }>${escapeText(label)}</button>`;
        })
        .join("");
    }
    const sparkLine = String(data.student_feedback_after_reveal || "").trim();
    if (sparkLine && isTeamsSparkPrompt({ prompt })) {
      controls += `<p class="prompt-spark-feedback">${escapeText(sparkLine)}</p>`;
    }
  } else if (kind === "numeric") {
    controls = `
      <label class="prompt-numeric">
        <span>Your answer</span>
        <input type="number" inputmode="decimal" id="prompt-numeric-input" ${
          lockChoices ? "disabled" : ""
        } />
      </label>
      <button type="button" class="prompt-submit" id="prompt-numeric-submit"${
        lockChoices ? " disabled" : ""
      }>Submit</button>
    `;
  } else if (kind === "share" || kind === "draw") {
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
      }>Share</button>
    `;
  } else {
    controls = `<p class="prompt-idle">Unsupported prompt kind.</p>`;
  }
  const itemId = String(data.item_id || "").trim();
  const label = String(data.label || "").trim();
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
  if (!picked && !lockChoices && !studentMcSummary(payload) && !studentPollClosed(payload)) {
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
function showFeedbackPanel(fragment) {
  if (!promptFeedback || !fragment) return;
  const lead = String(fragment.lead || "").trim();
  const why = String(fragment.text || "").trim();
  if (!lead && !why) return;
  hidePromptAck();
  if (promptFeedbackLead) promptFeedbackLead.textContent = lead || "Good work.";
  if (promptFeedbackWhy) promptFeedbackWhy.textContent = why;
  if (promptFeedbackHelper) {
    const dense = why.length > 180;
    promptFeedbackHelper.hidden = dense;
  }
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
 * Dismiss the feedback overlay; keep the Question frame mounted.
 */
function dismissFeedbackPanel() {
  hideFeedbackPanel();
  feedbackDismissed = true;
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
      const meetRide = promptShell && promptShell.querySelector(".meet-progress-dots");
      if (meetRide) {
        const choice = String((response && response.choice) || "").trim();
        promptShell.querySelectorAll(".prompt-choice").forEach((btn) => {
          const on = btn.getAttribute("data-choice") === choice;
          btn.classList.toggle("is-selected", on);
          btn.disabled = true;
        });
        return;
      }
      const fb = feedbackObject(data);
      if (fb && promptShell) {
        promptShell.querySelectorAll(".prompt-choice, .prompt-submit").forEach((btn) => {
          btn.disabled = true;
        });
        promptShell.querySelectorAll("input, textarea").forEach((field) => {
          field.disabled = true;
        });
        if (response && response.choice) {
          promptShell.querySelectorAll(".prompt-choice").forEach((btn) => {
            btn.classList.toggle(
              "is-selected",
              btn.getAttribute("data-choice") === String(response.choice)
            );
          });
        }
        const key = `${promptId}:${fb.lead}:${fb.text}`;
        lastFeedbackKey = key;
        showFeedbackPanel(fb);
        return;
      }
      hideFeedbackPanel();
      if (promptShell) {
        promptShell.hidden = true;
        promptShell.innerHTML = "";
      }
      showPromptAck(feedbackLine(data));
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
    const prevSeq = lastStateSeq;
    applyTeacherProjection(data);
    applyLayout(data);
    paintStudentCanvas(data);
    paintDisplayTime(data);
    paintMe(data);
    paintBoard(data);
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
    if (
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
