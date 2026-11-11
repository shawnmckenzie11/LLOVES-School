/**
 * MCK-476: feedback sits beside the opened board, and Prev/Next keeps it open.
 *
 * Args: outDir. Serves a fixture page and drives Chrome at 1280×800, then
 * a window narrow enough that the column would squeeze the board.
 */
import { createRequire } from "node:module";
import http from "node:http";
import { readFile } from "node:fs/promises";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright-core");

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const outDir = process.argv[2] || "/opt/cursor/artifacts";

const FIXTURE = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="/lms/static/lloves.css">
  <link rel="stylesheet" href="/lms/static/staff-shell.css">
  <link rel="stylesheet" href="/lms/static/whiteboard_formative.css">
  <style>
    html, body { margin: 0; height: 100%; overflow: hidden; background: #0b1220; }
    #formative-live-board { height: 100%; box-sizing: border-box; margin: 0; }
  </style>
  <script>
    window.fetch = async (url) => {
      const path = String(url);
      const detail = path.includes("/whiteboard-gallery/");
      const key = path.includes("/whiteboard-gallery/b") ? "b" : "a";
      const tile = (id, name, extra) => ({
        board_key: id,
        name,
        away: false,
        drawing: true,
        last_stroke_at: "2026-11-11T18:00:00Z",
        board_seq: 3,
        snapshot: true,
        ops: [],
        canvas_view: {
          strokes: [
            {
              id: id + "-s",
              owner: "s1",
              color: "#2563eb",
              points: [[0.08, 0.3], [0.4, 0.45], [0.72, 0.28]],
            },
            {
              id: id + "-t",
              owner: "teacher",
              color: "#16a34a",
              points: [[0.15, 0.7], [0.55, 0.62], [0.8, 0.75]],
            },
          ],
          texts: [],
        },
        team_sort_order: null,
        team_label: "",
        team_board: "",
        ...extra,
      });
      const tiles = [
        tile("a", "Board A", {
          status: "Drawing",
          text: "The line crosses at 2. The next step keeps that intercept and checks the rise. The typed work stays with the board so the teacher can read it beside the drawing without covering the strokes.",
        }),
        tile("b", "Team 1", {
          status: "Submitted",
          team_sort_order: 1,
          member_names: ["Seat A", "Seat B"],
          text: "",
        }),
      ];
      const body = detail
        ? {
            ok: true,
            board_key: key,
            name: key === "b" ? "Team 1" : "Board A",
            run_key: "run-1",
            board_seq: 3,
            snapshot: true,
            ops: [],
            canvas_view: tiles.find((row) => row.board_key === key).canvas_view,
            legend: [{ id: "s1", name: "Seat A", color: "#2563eb", slot: 1 }],
          }
        : {
            ok: true,
            ended: false,
            visible: true,
            run_key: "run-1",
            answer_with: "both",
            context: "Typing and whiteboard",
            summary: "2 boards",
            filters: [{ id: "all", label: "All" }],
            projected_board: "",
            tiles,
          };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };
  </script>
</head>
<body>
  <script type="application/json" id="lloves-features">{"whiteboard": true}</script>
  <div id="ap-root" data-live-session-id="7"></div>
  <section id="formative-live-board" class="wb-live" hidden data-refresh-ms="2000">
    <header class="wb-live-head">
      <div>
        <h2 class="wb-live-title">Live whiteboard</h2>
        <p id="wb-live-context" class="wb-live-context"></p>
      </div>
    </header>
    <div id="wb-live-filters" class="wb-live-filters"></div>
    <p id="wb-live-summary" class="wb-live-summary"></p>
    <p id="wb-live-projected" class="wb-live-projected"></p>
    <div class="wb-live-body">
      <div id="wb-live-grid" class="wb-live-grid"></div>
      <aside id="wb-live-panel" class="wb-live-panel" hidden>
        <div class="wb-live-open">
          <div class="wb-live-board">
            <div class="live-canvas-stage wb-live-panel-stage">
              <canvas id="wb-live-panel-canvas" class="wb-live-panel-canvas" width="720" height="360" aria-label="Whiteboard"></canvas>
            </div>
          </div>
          <div id="wb-live-feedback" class="wb-live-feedback">
            <div class="wb-live-panel-bar">
              <div class="wb-live-panel-who">
                <strong id="wb-live-panel-name">Board</strong>
                <span id="wb-live-panel-team" class="wb-live-team" hidden></span>
                <span id="wb-live-panel-status" class="wb-live-panel-status"></span>
              </div>
              <div class="wb-live-panel-nav">
                <button type="button" id="wb-live-prev" aria-label="Previous board">←</button>
                <button type="button" id="wb-live-next" aria-label="Next board">→</button>
                <button type="button" id="wb-live-collapse" class="wb-live-collapse" aria-expanded="true">›</button>
                <button type="button" id="wb-live-close" aria-label="Close board">×</button>
              </div>
            </div>
            <div class="wb-live-panel-tools">
              <button type="button" id="wb-live-pen" class="is-on wb-live-pen">Pen</button>
              <button type="button" id="wb-live-undo">Undo</button>
              <button type="button" id="wb-live-clear">Clear my ink</button>
              <button type="button" id="wb-live-erase" hidden>Erase</button>
            </div>
            <p id="wb-live-answer" class="wb-live-answer" hidden></p>
            <button type="button" id="wb-live-answer-more" class="wb-live-more" hidden>more</button>
            <label class="qboard-note-label">Feedback
              <textarea id="wb-live-note" data-wb-part="note" rows="2" maxlength="500" aria-label="Feedback for this board"></textarea>
            </label>
            <button type="button" id="wb-live-show" class="wb-live-show">Show on board</button>
          </div>
        </div>
      </aside>
    </div>
  </section>
  <script type="module" src="/lms/static/formative_live_board.js"></script>
</body>
</html>`;

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
  ".png": "image/png",
};

/**
 * Serve the fixture and the repo. Paths that leave the repo are refused.
 * @returns {Promise<{port: number, close: () => void}>}
 */
function startServer() {
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url || "/", "http://127.0.0.1");
    if (url.pathname === "/mck476.html") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(FIXTURE);
      return;
    }
    const rel = decodeURIComponent(url.pathname).replace(/^\/+/, "");
    if (!rel || rel.includes("\0") || rel.split("/").includes("..")) {
      res.writeHead(403);
      res.end();
      return;
    }
    const file = path.resolve(repo, rel);
    if (!file.startsWith(repo + path.sep)) {
      res.writeHead(403);
      res.end();
      return;
    }
    try {
      const body = await readFile(file);
      const type = TYPES[path.extname(file)] || "application/octet-stream";
      res.writeHead(200, { "Content-Type": type });
      res.end(body);
    } catch (_err) {
      res.writeHead(404);
      res.end();
    }
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      resolve({
        port: address.port,
        close() {
          server.close();
        },
      });
    });
  });
}

/**
 * Read the opened board and the feedback column.
 * @param {import('playwright-core').Page} page
 */
function measure(page) {
  return page.evaluate(() => {
    const canvas = document.getElementById("wb-live-panel-canvas");
    const feedback = document.getElementById("wb-live-feedback");
    const panel = document.getElementById("wb-live-panel");
    const root = document.getElementById("formative-live-board");
    const board = canvas.getBoundingClientRect();
    const side = feedback.getBoundingClientRect();
    return {
      hidden: panel.hidden,
      name: document.getElementById("wb-live-panel-name").textContent,
      status: document.getElementById("wb-live-panel-status").textContent,
      members: document.getElementById("wb-live-panel-team").textContent,
      show: document.getElementById("wb-live-show").textContent,
      clear: document.getElementById("wb-live-clear").textContent,
      eraseHidden: getComputedStyle(document.getElementById("wb-live-erase")).display === "none",
      inlineWidth: canvas.style.width,
      boardW: board.width,
      boardH: board.height,
      panelW: side.width,
      panelH: side.height,
      gap: side.left - board.right,
      overlap: side.left < board.right - 1,
      overlay: root.classList.contains("is-overlay"),
      collapsed: root.classList.contains("is-feedback-collapsed"),
      scrollX: document.documentElement.scrollWidth - window.innerWidth,
      scrollY: document.documentElement.scrollHeight - window.innerHeight,
    };
  });
}

await mkdir(outDir, { recursive: true });
const server = await startServer();
const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || "/usr/bin/google-chrome",
  headless: true,
  args: ["--disable-dev-shm-usage"],
});
const context = await browser.newContext({
  viewport: { width: 1280, height: 800 },
  deviceScaleFactor: 1,
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (err) => errors.push(err && err.stack ? err.stack : String(err)));

const checks = {};
try {
  await page.goto(`http://127.0.0.1:${server.port}/mck476.html`, { waitUntil: "networkidle" });
  await page.waitForSelector("#wb-live-grid .wb-live-tile");
  await page.locator(".wb-live-tile").first().click();
  await page.waitForTimeout(400);
  const beside = await measure(page);
  await page.screenshot({ path: path.join(outDir, "mck476-beside-1280.png") });
  await page.locator("#wb-live-next").click();
  await page.waitForTimeout(2500);
  const afterNext = await measure(page);
  await page.keyboard.press("ArrowLeft");
  await page.waitForTimeout(200);
  const afterPrev = await measure(page);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(150);
  const closed = await measure(page);

  await page.setViewportSize({ width: 880, height: 640 });
  await page.locator(".wb-live-tile").first().click();
  await page.waitForTimeout(400);
  const overlay = await measure(page);
  await page.screenshot({ path: path.join(outDir, "mck476-overlay-expanded.png") });
  await page.locator("#wb-live-collapse").click();
  await page.waitForTimeout(200);
  const collapsed = await measure(page);
  await page.screenshot({ path: path.join(outDir, "mck476-overlay-collapsed.png") });

  checks.beside =
    beside.hidden === false &&
    beside.overlay === false &&
    beside.overlap === false &&
    beside.boardW >= 640 &&
    beside.boardW <= 980 &&
    Math.abs(beside.boardW / beside.boardH - 2) < 0.05 &&
    Math.abs(beside.panelW - 320) < 4 &&
    Math.abs(beside.gap - 16) < 6 &&
    beside.panelH <= beside.boardH + 2 &&
    beside.inlineWidth === "" &&
    beside.scrollX <= 1 &&
    beside.scrollY <= 1 &&
    beside.name === "Board A" &&
    beside.status === "Drawing" &&
    beside.show === "Show on board" &&
    beside.clear === "Clear my ink" &&
    beside.eraseHidden === true;
  checks.nextKeepsOpen =
    afterNext.hidden === false &&
    afterNext.name === "Team 1" &&
    afterNext.members === "Seat A, Seat B" &&
    afterNext.status === "Submitted";
  checks.prevKeepsOpen =
    afterPrev.hidden === false && afterPrev.name === "Board A" && afterPrev.status === "Drawing";
  checks.escapeCloses = closed.hidden === true;
  checks.overlay =
    overlay.hidden === false &&
    overlay.overlay === true &&
    overlay.overlap === true &&
    overlay.boardW >= 640 &&
    Math.abs(overlay.boardW / overlay.boardH - 2) < 0.05 &&
    overlay.scrollX <= 1 &&
    overlay.scrollY <= 1;
  checks.collapsed =
    collapsed.hidden === false &&
    collapsed.collapsed === true &&
    collapsed.boardW >= 640 &&
    Math.abs(collapsed.panelW - 40) < 4 &&
    collapsed.boardW + 1 >= overlay.boardW;
  checks.noPageError = errors.length === 0;
  checks.details = { errors, beside, afterNext, afterPrev, closed, overlay, collapsed };
} finally {
  await context.close();
  await browser.close();
  server.close();
}

const failed = Object.entries(checks).filter(([key, ok]) => key !== "details" && !ok);
if (failed.length) {
  console.log(JSON.stringify(checks));
  process.exit(1);
}
console.log(JSON.stringify(checks));
