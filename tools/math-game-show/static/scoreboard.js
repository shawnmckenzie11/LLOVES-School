import { formatCountdown, formatPoints, lockRoundDeadline, remainingUntilMs } from "./common.js";

if (new URLSearchParams(location.search).get("overlay") === "1") {
  document.body.classList.add("overlay");
}
let lastSeq = 0;
let lastGameId = null;
let phase = "idle";
let tickBusy = false;
let roundEndsAtMs = 0;

const stage = document.querySelector(".board-stage");
const bar = document.getElementById("bar");
const idle = document.getElementById("idle");
const finalBoard = document.getElementById("final");
const finalList = document.getElementById("final-list");
const boardTicker = document.getElementById("board-ticker");
const boardTickerKicker = document.getElementById("board-ticker-kicker");
const boardTickerTrack = document.getElementById("board-ticker-track");
const roundBanner = document.getElementById("round-banner");
let lastTickerStamp = "";
const roundTitleEl = document.getElementById("round-title");
const roundClockEl = document.getElementById("round-clock");

/**
 * True when the ESPN bar's team nodes no longer match the payload.
 * @param {Array<{id: number}>} teams
 */
function barNeedsRebuild(teams) {
  const nodes = [...bar.querySelectorAll(":scope > .espn-team")];
  if (nodes.length !== teams.length) return true;
  return nodes.some((node, i) => node.dataset.teamId !== String(teams[i].id));
}

/**
 * Paint the ESPN-style bottom bar. No roster, no IDs.
 * Updates score text in place so pulse animations are not wiped.
 * @param {any} data
 */
function paintBar(data) {
  const teams = data.teams || [];
  if (barNeedsRebuild(teams)) {
    bar.innerHTML = teams
      .map(
        (team) => `<div class="espn-team" data-team-id="${team.id}">
        <div class="espn-swatch" style="background:${team.color}"></div>
        <div class="espn-meta">
          <div class="espn-name">${escapeText(team.name)}</div>
          <div class="espn-score" data-score="${escapeText(team.score)}">${escapeText(formatPoints(team.score))}</div>
        </div>
      </div>`
      )
      .join("");
    return;
  }
  for (const team of teams) {
    const node = bar.querySelector(`:scope > [data-team-id="${team.id}"]`);
    if (!node) continue;
    const nameEl = node.querySelector(".espn-name");
    const scoreEl = node.querySelector(".espn-score");
    if (nameEl && nameEl.textContent !== team.name) nameEl.textContent = team.name;
    const scoreKey = String(team.score);
    if (scoreEl && scoreEl.dataset.score !== scoreKey) {
      scoreEl.dataset.score = scoreKey;
      scoreEl.textContent = formatPoints(team.score);
    }
    const swatch = node.querySelector(".espn-swatch");
    if (swatch) swatch.style.background = team.color;
  }
}

/**
 * Teams with the highest score (ties included).
 * @param {Array<{score?: number}>} teams
 * @returns {Array<any>}
 */
function winningTeams(teams) {
  if (!teams.length) return [];
  const top = Math.max(...teams.map((team) => Number(team.score) || 0));
  return teams.filter((team) => (Number(team.score) || 0) === top);
}

/**
 * Ranked Final Score plus a looping winners ticker at the bottom.
 * @param {any} data
 */
function showFinal(data) {
  idle.hidden = true;
  finalBoard.hidden = false;
  finalBoard.removeAttribute("hidden");
  finalBoard.classList.remove("hidden");
  document.body.classList.add("is-final");
  if (stage) stage.classList.add("is-final");
  const ranked = [...(data.teams || [])].sort((a, b) => {
    const diff = Number(b.score) - Number(a.score);
    if (diff !== 0) return diff;
    return Number(a.id) - Number(b.id);
  });
  finalList.innerHTML = ranked
    .map((team) => {
      const place = 1 + ranked.filter((other) => Number(other.score) > Number(team.score)).length;
      const win = (Number(team.score) || 0) === (Number(ranked[0]?.score) || 0);
      return `<li class="${win ? "winner" : "runner"}" style="--team:${escapeText(team.color)}">
        <span class="final-place">${place}</span>
        <span class="final-name">${escapeText(team.name)}</span>
        ${win ? `<span class="final-crown">Winner</span>` : ""}
        <span class="final-score">${escapeText(formatPoints(team.score))}</span>
      </li>`;
    })
    .join("");
  const winners = winningTeams(ranked);
  const names = winners.flatMap((team) =>
    (team.players || [])
      .map((player) => String(player.first_name || "").trim())
      .filter(Boolean)
      .map((first) => ({ first, team: team.name, color: team.color }))
  );
  startWinnerTicker(names, winners);
}

/**
 * Crawl items across the bottom ticker (leaders while live, winners at Final).
 * @param {string} kicker
 * @param {string} itemHtml
 * @param {string} stamp
 */
function startBoardTicker(kicker, itemHtml, stamp) {
  if (!boardTicker || !boardTickerTrack) return;
  if (stamp === lastTickerStamp && boardTickerTrack.classList.contains("is-moving")) {
    boardTicker.hidden = false;
    boardTicker.removeAttribute("hidden");
    boardTicker.classList.remove("hidden");
    return;
  }
  lastTickerStamp = stamp;
  if (boardTickerKicker) boardTickerKicker.textContent = kicker;
  boardTickerTrack.classList.remove("is-moving");
  boardTickerTrack.style.removeProperty("--ticker-s");
  const unit = `<div class="ticker-unit">${itemHtml}</div>`;
  boardTickerTrack.innerHTML = `${unit}${unit}`;
  boardTicker.hidden = false;
  boardTicker.removeAttribute("hidden");
  boardTicker.classList.remove("hidden");
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      const scroller = boardTickerTrack.parentElement;
      const units = boardTickerTrack.querySelectorAll(".ticker-unit");
      const first = units[0];
      const second = units[1];
      if (!scroller || !first) return;
      const gap = `<span class="final-ticker-dot" aria-hidden="true">●</span>`;
      let guard = 0;
      while (first.scrollWidth < scroller.clientWidth && guard < 8) {
        first.insertAdjacentHTML("beforeend", `${gap}${itemHtml}`);
        guard += 1;
      }
      if (second) second.innerHTML = first.innerHTML;
      const seconds = Math.max(18, Math.round(first.scrollWidth / 70));
      boardTickerTrack.style.setProperty("--ticker-s", `${seconds}s`);
      boardTickerTrack.classList.add("is-moving");
    });
  });
}

/**
 * Loop winning first names across the bottom like a news ticker.
 * @param {Array<{first: string, team: string, color: string}>} names
 * @param {Array<{name: string}>} winners
 */
function startWinnerTicker(names, winners) {
  const kicker =
    winners.length === 1 ? String(winners[0].name || "Winners") : "Winners";
  const itemHtml = names.length
    ? names
        .map(
          (row) => `<span class="final-ticker-item" style="--team:${escapeText(row.color)}">
            ${escapeText(row.first)}${
              winners.length > 1 ? ` <span class="final-ticker-team">${escapeText(row.team)}</span>` : ""
            }
          </span>`
        )
        .join('<span class="final-ticker-dot" aria-hidden="true">●</span>')
    : `<span class="final-ticker-item">No players listed</span>`;
  startBoardTicker(kicker, itemHtml, `winners:${kicker}:${names.map((n) => n.first).join(",")}`);
}

/**
 * Ticker kicker for live Leaders, including the selected stats period.
 * @param {any} data
 * @returns {string}
 */
function leadersKicker(data) {
  const fromServer = String(data?.stat_window_label || "").trim();
  if (fromServer) return `Leaders · ${fromServer}`;
  const labels = { last_class: "last class", last_week: "last week", year: "this year" };
  const window = String(data?.stat_window || "last_class");
  return `Leaders · ${labels[window] || "last class"}`;
}

/**
 * Loop Open Question / Team Challenge / Formative leaders until Final Score.
 * @param {any} data
 */
function paintLeadersTicker(data) {
  const items = Array.isArray(data.leaders)
    ? data.leaders.map((row) => String(row || "").trim()).filter(Boolean)
    : [];
  const shown = items.length ? items : ["Leaders appear when students score"];
  const kicker = leadersKicker(data);
  const itemHtml = shown
    .map((text) => `<span class="final-ticker-item">${escapeText(text)}</span>`)
    .join('<span class="final-ticker-dot" aria-hidden="true">●</span>');
  startBoardTicker(kicker, itemHtml, `leaders:${kicker}:${shown.join("|")}`);
}

/**
 * Hide the bottom ticker (idle board, or empty leaders).
 */
function hideTicker() {
  lastTickerStamp = "";
  if (boardTickerTrack) {
    boardTickerTrack.classList.remove("is-moving");
    boardTickerTrack.innerHTML = "";
  }
  if (boardTicker) {
    boardTicker.hidden = true;
    boardTicker.classList.add("hidden");
  }
}

/**
 * Hide the Final Score overlay for a new live game or idle board.
 */
function hideFinal() {
  finalBoard.hidden = true;
  finalBoard.classList.add("hidden");
  document.body.classList.remove("is-final");
  if (stage) stage.classList.remove("is-final");
  finalList.innerHTML = "";
  hideTicker();
}

/**
 * Route idle / live / final from a scoreboard payload.
 * @param {any} data
 */
function render(data) {
  const teams = data.teams || [];
  const isLive = Boolean(data.live) && teams.length > 0;
  const isFinal =
    !isLive &&
    teams.length > 0 &&
    (Boolean(data.final) || data.status === "ended");

  if (isLive) {
    idle.hidden = true;
    if (phase !== "live") {
      hideFinal();
      phase = "live";
      document.body.classList.remove("is-idle");
      document.body.classList.add("is-live");
      if (stage) {
        stage.classList.remove("is-idle");
        stage.classList.add("is-live");
      }
    }
    paintBar(data);
    paintRoundBanner(data);
    paintLeadersTicker(data);
    return;
  }
  if (isFinal) {
    idle.hidden = true;
    if (phase !== "final") {
      document.body.classList.remove("is-live", "is-idle");
      if (stage) stage.classList.remove("is-live", "is-idle");
      hideRoundBanner();
      paintBar(data);
      phase = "final";
      showFinal(data);
    }
    return;
  }
  if (phase !== "idle") {
    hideFinal();
    phase = "idle";
    document.body.classList.remove("is-live");
    if (stage) stage.classList.remove("is-live");
    hideRoundBanner();
    bar.innerHTML = "";
  }
  idle.hidden = false;
  document.body.classList.add("is-idle");
  if (stage) stage.classList.add("is-idle");
  paintLeadersTicker(data);
}

/**
 * Seconds left until the locked round deadline.
 * @returns {number}
 */
function displayedRemaining() {
  return remainingUntilMs(roundEndsAtMs);
}

/**
 * Show Round N · title and countdown above the ESPN bar.
 * @param {any} data
 */
function paintRoundBanner(data) {
  if (!roundBanner) return;
  const n = Number(data.round) || 1;
  const title = String(data.round_title || "");
  if (roundTitleEl) roundTitleEl.textContent = `Round ${n} · ${title}`;
  roundEndsAtMs = lockRoundDeadline(roundEndsAtMs, data.round_ends_at_ms);
  paintRoundClock();
  roundBanner.hidden = false;
  roundBanner.classList.remove("hidden");
}

/**
 * Write the interpolated countdown on the scoreboard banner.
 */
function paintRoundClock() {
  if (roundClockEl) roundClockEl.textContent = formatCountdown(displayedRemaining());
}

/**
 * Hide the live round banner (idle or Final Score).
 */
function hideRoundBanner() {
  if (!roundBanner) return;
  roundEndsAtMs = 0;
  roundBanner.hidden = true;
  roundBanner.classList.add("hidden");
}

/**
 * Minimal text escape for scoreboard labels.
 * @param {unknown} value
 */
function escapeText(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/"/g, "&quot;");
}

/**
 * Bounce a caption inside a team's scoreboard rectangle until it is removed.
 * @param {HTMLElement} floater
 * @param {HTMLElement} host
 */
function bounceFloater(floater, host) {
  const pad = 4;
  let x = pad;
  let y = pad;
  let vx = 90;
  let vy = 55;
  let last = 0;

  /**
   * Clamp space the caption can occupy inside the team section.
   * @returns {{maxX: number, maxY: number}}
   */
  function bounds() {
    const maxX = Math.max(pad, host.clientWidth - floater.offsetWidth - pad);
    const maxY = Math.max(pad, host.clientHeight - floater.offsetHeight - pad);
    return { maxX, maxY };
  }

  /**
   * Reverse one axis and keep speed in a playful range.
   * @param {number} vel
   * @returns {number}
   */
  function bounce(vel) {
    const speed = Math.max(46, Math.abs(vel) * (0.94 + Math.random() * 0.18));
    return -Math.sign(vel || 1) * speed;
  }

  const box = bounds();
  x = pad + Math.random() * Math.max(0, box.maxX - pad);
  y = pad + Math.random() * Math.max(0, box.maxY - pad);
  const speed = 70 + Math.random() * 80;
  const angle = Math.random() * Math.PI * 2;
  vx = Math.cos(angle) * speed;
  vy = Math.sin(angle) * speed;
  if (Math.abs(vx) < 28) vx = 28 * Math.sign(vx || 1);
  if (Math.abs(vy) < 22) vy = 22 * Math.sign(vy || 1);
  floater.style.transform = `translate(${x}px, ${y}px)`;

  /**
   * Advance one bounce frame while the caption is still on the board.
   * @param {number} now
   */
  function frame(now) {
    if (!floater.isConnected) return;
    const dt = last ? Math.min(0.05, (now - last) / 1000) : 0;
    last = now;
    const limit = bounds();
    x += vx * dt;
    y += vy * dt;
    if (x <= pad) {
      x = pad;
      vx = bounce(-Math.abs(vx));
    } else if (x >= limit.maxX) {
      x = limit.maxX;
      vx = bounce(Math.abs(vx));
    }
    if (y <= pad) {
      y = pad;
      vy = bounce(-Math.abs(vy));
    } else if (y >= limit.maxY) {
      y = limit.maxY;
      vy = bounce(Math.abs(vy));
    }
    floater.style.transform = `translate(${x}px, ${y}px)`;
    requestAnimationFrame(frame);
  }

  requestAnimationFrame(frame);
}

/**
 * Pulse a team and bounce a caption inside that team's scoreboard section.
 * Negative individual awards skip the animation. Font stays put for 2s,
 * then fades over 3s. Small Team Bonus and player first names use the
 * event caption.
 * @param {any} event
 */
function celebrate(event) {
  if (!event) return;
  const amount = Number(event.amount);
  if (!amount) return;
  const isStudent = event.kind === "student";
  if (amount < 0 && isStudent) return;
  const node = bar.querySelector(`:scope > [data-team-id="${event.team_id}"]`);
  if (!node) return;
  const first = String(event.first_name || "").trim();
  let text = String(event.caption || event.label || "").trim();
  if (!text) {
    if (event.team_rule === "team_only" && amount > 0) {
      text = `Small Team Bonus +${amount}`;
    } else if (first) {
      text = amount > 0 ? `${first} +${amount}` : `${first} ${amount}`;
    } else {
      text = amount > 0 ? `+${amount}` : String(amount);
    }
  }
  if (amount > 0) {
    node.classList.remove("pulse");
    void node.offsetWidth;
    node.classList.add("pulse");
    window.setTimeout(() => node.classList.remove("pulse"), 1300);
  }
  let layer = node.querySelector(":scope > .espn-floaters");
  if (!layer) {
    layer = document.createElement("div");
    layer.className = "espn-floaters";
    node.appendChild(layer);
  }
  const floater = document.createElement("div");
  floater.className = amount > 0 ? "float-plus" : "float-plus float-minus";
  floater.textContent = text;
  layer.appendChild(floater);
  bounceFloater(floater, layer);
  floater.addEventListener("animationend", () => floater.remove(), { once: true });
  setTimeout(() => floater.remove(), 5200);
}

/**
 * Poll live scores about every 400ms.
 */
async function tick() {
  if (tickBusy) return;
  tickBusy = true;
  try {
    const response = await fetch("/api/scoreboard");
    if (!response.ok) return;
    const data = await response.json();
    const gameId = data.game_id ?? null;
    if (gameId !== lastGameId) {
      lastGameId = gameId;
      lastSeq = Number(data.event_seq || 0);
      if (data.live) phase = "idle";
    }
    render(data);
    const seq = Number(data.event_seq || 0);
    if (data.live && seq > lastSeq) {
      lastSeq = seq;
      celebrate(data.last_event);
    } else if (seq > lastSeq) {
      lastSeq = seq;
    }
  } finally {
    tickBusy = false;
  }
}

tick().catch(() => {});
setInterval(() => {
  tick().catch(() => {});
}, 400);
setInterval(paintRoundClock, 200);
