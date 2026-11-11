/**
 * MCK-473: the teacher gallery stays still while polls arrive, and an
 * opened board stays open at full width.
 *
 * Args: outDir. Serves this repo and a fixture page, then drives Chrome.
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
  <script>
    window.__writes = 0;
    window.__ro = 0;
    const OrigRO = window.ResizeObserver;
    window.ResizeObserver = class extends OrigRO {
      constructor(cb) {
        super((entries, obs) => {
          window.__ro += 1;
          cb(entries, obs);
        });
      }
    };
    window.fetch = async (url) => {
      const path = String(url);
      const n = (window.__n = (window.__n || 0) + 1);
      const tile = (key, name) => ({
        board_key: key,
        name,
        away: false,
        drawing: true,
        last_stroke_at: "2026-11-11T18:00:00Z",
        board_seq: n,
        snapshot: true,
        ops: [],
        canvas_view: {
          strokes: [{
            id: key + "-s",
            owner: "s1",
            color: "#2563eb",
            points: [[0.12, 0.2], [0.42, 0.28 + (n % 4) * 0.04], [0.7, 0.55]],
          }],
          texts: [],
        },
        team_sort_order: null,
        team_label: "",
        team_board: "",
      });
      let body;
      if (path.includes("/whiteboard-gallery/a")) {
        body = {
          ok: true,
          board_key: "a",
          name: "Board A",
          run_key: "run-1",
          board_seq: n,
          snapshot: true,
          ops: [],
          canvas_view: {
            strokes: [{
              id: "a-s",
              owner: "s1",
              color: "#2563eb",
              points: [[0.15, 0.25], [0.55, 0.45 + (n % 5) * 0.02]],
            }],
            texts: [],
          },
          legend: [{ id: "s1", name: "A", color: "#2563eb", slot: 1 }],
        };
      } else {
        body = {
          ok: true,
          ended: false,
          visible: true,
          run_key: "run-1",
          context: "Live",
          summary: "8 boards",
          filters: [{ id: "all", label: "All" }],
          projected_board: "",
          tiles: ["a", "b", "c", "d", "e", "f", "g", "h"].map((key, index) =>
            tile(key, "Board " + String.fromCharCode(65 + index))
          ),
        };
      }
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };
  </script>
</head>
<body class="staff-shell course-live">
  <script type="application/json" id="lloves-features">{"whiteboard": true}</script>
  <div id="ap-root" data-live-session-id="7"></div>
  <div class="wrap course-shell">
    <div class="track-live-root live-shell-root live-shell-ia-v2">
      <div class="live-header" style="height:52px"></div>
      <div class="live-shell-body">
        <aside class="live-shell-left"></aside>
        <main class="live-shell-right">
          <section class="live-active-content" id="live-active-content">
            <div class="live-frames" id="live-frames" data-slots="A">
              <section id="canvas-zone" class="live-content-slot" data-content-id="canvas" data-slot="A">
                <div class="live-content-pane-head"><h2 class="live-pane-title">Whiteboard</h2></div>
                <section id="formative-live-board" class="wb-live" hidden data-refresh-ms="2000">
                  <header class="wb-live-head">
                    <div>
                      <h2 class="wb-live-title">Live whiteboard</h2>
                      <p id="wb-live-context" class="wb-live-context"></p>
                    </div>
                    <div class="wb-live-controls">
                      <label>Tiles/row
                        <select id="wb-live-cols" aria-label="Tiles per row">
                          <option value="4" selected>4</option>
                        </select>
                      </label>
                      <span class="wb-live-pill">Live</span>
                    </div>
                  </header>
                  <div id="wb-live-filters" class="wb-live-filters" role="tablist"></div>
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
                            <button type="button" id="wb-live-text" hidden>Text</button>
                            <button type="button" id="wb-live-erase" hidden>Erase</button>
                            <button type="button" id="wb-live-redo" hidden>Redo</button>
                            <button type="button" id="wb-live-show" class="wb-live-show">Show on board</button>
                          </div>
                          <div id="wb-live-legend" hidden></div>
                        </div>
                      </div>
                    </aside>
                  </div>
                </section>
              </section>
            </div>
          </section>
        </main>
      </div>
    </div>
  </div>
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
    if (url.pathname === "/mck473.html") {
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

await mkdir(outDir, { recursive: true });
const server = await startServer();
const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || "/usr/bin/google-chrome",
  headless: true,
  args: ["--disable-dev-shm-usage"],
});
const context = await browser.newContext({
  viewport: { width: 1366, height: 768 },
  deviceScaleFactor: 1.25,
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (err) => errors.push(err && err.stack ? err.stack : String(err)));

const checks = {};
try {
  await page.goto(`http://127.0.0.1:${server.port}/mck473.html`, { waitUntil: "networkidle" });
  await page.waitForSelector("#wb-live-grid .wb-live-tile");
  await page.evaluate(() => {
    document.querySelectorAll(".wb-live-tile").forEach((el, index) => {
      el.dataset.pin = String(index);
    });
    const grid = document.getElementById("wb-live-grid");
    window.__writes = 0;
    const watch = new MutationObserver(() => {
      window.__writes += 1;
    });
    watch.observe(grid, { attributes: true, attributeFilter: ["style"] });
  });
  await page.screenshot({ path: path.join(outDir, "mck473-grid.png") });
  const beforeOpen = await page.evaluate(() => ({
    pins: document.querySelectorAll(".wb-live-tile[data-pin]").length,
    writes: window.__writes,
  }));
  await page.locator(".wb-live-tile").first().click();
  await page.waitForTimeout(400);
  await page.evaluate(() => {
    window.__ro = 0;
  });
  const opened = await page.evaluate(() => {
    const canvas = document.getElementById("wb-live-panel-canvas");
    const stage = canvas.parentElement;
    const box = canvas.getBoundingClientRect();
    const stageBox = stage.getBoundingClientRect();
    return {
      hidden: document.getElementById("wb-live-panel").hidden,
      inlineWidth: canvas.style.width,
      w: box.width,
      h: box.height,
      stageW: stageBox.width,
      stageClient: stage.clientWidth,
    };
  });
  for (let frame = 0; frame < 4; frame += 1) {
    await page.waitForTimeout(1200);
    await page.screenshot({ path: path.join(outDir, `mck473-open-${frame}.png`) });
  }
  const after = await page.evaluate(() => {
    const canvas = document.getElementById("wb-live-panel-canvas");
    const box = canvas.getBoundingClientRect();
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const sample = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    let blue = 0;
    for (let i = 0; i < sample.length; i += 16) {
      if (sample[i + 2] > 80 && sample[i + 2] > sample[i] + 30) blue += 1;
    }
    const pins = [...document.querySelectorAll(".wb-live-tile")].map((el) => el.dataset.pin || "");
    return {
      hidden: document.getElementById("wb-live-panel").hidden,
      name: document.getElementById("wb-live-panel-name").textContent,
      w: box.width,
      h: box.height,
      blue,
      pins,
      writes: window.__writes,
      ro: window.__ro,
      tiles: document.querySelectorAll(".wb-live-tile").length,
    };
  });
  await page.screenshot({ path: path.join(outDir, "mck473-open.png") });
  await page.locator("#wb-live-close").click();
  const closed = await page.evaluate(() => ({
    hidden: document.getElementById("wb-live-panel").hidden,
    pins: [...document.querySelectorAll(".wb-live-tile")].map((el) => el.dataset.pin || "").join(","),
    shown: getComputedStyle(document.getElementById("wb-live-grid")).display,
  }));
  await page.screenshot({ path: path.join(outDir, "mck473-closed.png") });
  checks.tilesKept =
    beforeOpen.pins === 8 &&
    after.tiles === 8 &&
    after.pins.join(",") === "0,1,2,3,4,5,6,7";
  checks.panelOpen = opened.hidden === false && after.hidden === false && after.name === "Board A";
  checks.aspect =
    opened.h > 80 &&
    Math.abs(opened.w / opened.h - 2) < 0.05 &&
    Math.abs(after.w - opened.w) < 2 &&
    Math.abs(after.h - opened.h) < 2;
  checks.fullWidth =
    opened.inlineWidth === "" &&
    Math.abs(opened.w - opened.stageClient) < 3;
  checks.ink = after.blue > 8;
  checks.resizeBounded = after.ro < 12;
  checks.columnWritesBounded = after.writes === 0;
  checks.noPageError = errors.length === 0;
  checks.closedRestoresGrid =
    closed.hidden === true && closed.pins === "0,1,2,3,4,5,6,7" && closed.shown !== "none";
  checks.details = { errors, beforeOpen, opened, after, closed };
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
