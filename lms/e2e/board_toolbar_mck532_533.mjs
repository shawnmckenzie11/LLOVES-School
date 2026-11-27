/**
 * MCK-532 / MCK-533 headless checks. Config path is local and is not printed.
 * Screenshots are cropped to the board stage only.
 */
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright-core");

const cfg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const browser = await chromium.launch({
  executablePath: cfg.chrome,
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const errors = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * @param {import("playwright-core").Page} page
 */
async function closeDialogs(page) {
  await page.evaluate(() => {
    document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  });
}

/**
 * @param {import("playwright-core").Browser} browser
 * @param {number} width
 */
async function openStudent(width) {
  const context = await browser.newContext({
    viewport: { width, height: 844 },
    deviceScaleFactor: 2,
    hasTouch: false,
  });
  await context.addCookies(
    Object.entries(cfg.cookies).map(([name, value]) => ({
      name,
      value,
      url: cfg.base,
    })),
  );
  const page = await context.newPage();
  page.on("pageerror", (err) => errors.push(String(err).slice(0, 180)));
  await page.goto(`${cfg.base}${cfg.path}`, { waitUntil: "domcontentloaded" });
  await page.locator(".qboard-slot canvas.student-canvas").first().waitFor({ timeout: 20000 });
  await closeDialogs(page);
  await sleep(800);
  return { context, page };
}

/**
 * @param {import("playwright-core").Page} page
 */
function toolHits(page) {
  return page.evaluate(() => {
    const stage =
      document.querySelector(".qboard-phone .live-canvas-stage") ||
      document.querySelector(".qboard-slot .live-canvas-stage");
    const tools = ["pen", "line", "text", "equation", "undo", "clear"];
    return tools.map((tool) => {
      const button = stage?.querySelector(`[data-tool="${tool}"]`);
      if (!(button instanceof HTMLElement)) {
        return { tool, present: false, w: 0, h: 0, hit: false };
      }
      const box = button.getBoundingClientRect();
      const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
      const onButton = Boolean(
        hit && (hit === button || button.contains(hit) || hit.closest?.(`[data-tool="${tool}"]`) === button),
      );
      return {
        tool,
        present: true,
        w: Math.round(box.width * 100) / 100,
        h: Math.round(box.height * 100) / 100,
        hit: onButton,
      };
    });
  });
}

/**
 * @param {import("playwright-core").Page} page
 */
function boardGeo(page) {
  return page.evaluate(() => {
    const canvas = document.querySelector(".qboard-slot canvas.student-canvas");
    const box = canvas?.getBoundingClientRect();
    const body = document.getElementById("live-question-stack-body");
    return {
      top: box ? Math.round(box.top * 100) / 100 : null,
      x: box ? box.x : null,
      y: box ? box.y : null,
      w: box ? box.width : null,
      h: box ? box.height : null,
      cards: document.querySelectorAll("#live-question-stack-body .student-live-card").length,
      minimizedBars: document.querySelectorAll(".student-minimized-bar").length,
      scrollTop: body ? body.scrollTop : null,
      strokeDown: Boolean(document.querySelector("canvas.student-canvas.is-stroke-down")),
    };
  });
}

/**
 * @param {import("playwright-core").Page} page
 * @param {string} selector
 */
function inkCount(page, selector) {
  return page.evaluate((sel) => {
    const canvas = document.querySelector(sel);
    if (!(canvas instanceof HTMLCanvasElement)) return 0;
    const data = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    let n = 0;
    for (let i = 3; i < data.length; i += 4) {
      if (data[i] > 40) n += 1;
    }
    return n;
  }, selector);
}

const { context, page } = await openStudent(360);
const toolbar = {};
for (const width of [360, 390]) {
  await page.setViewportSize({ width, height: 844 });
  await page.locator(".qboard-slot canvas.student-canvas").first().evaluate((el) => {
    el.scrollIntoView({ block: "center" });
  });
  await sleep(400);
  toolbar[String(width)] = await toolHits(page);
  const stage = page.locator(".qboard-phone .live-canvas-stage").first();
  await stage.screenshot({ path: `${cfg.out}/toolbar-${width}.png` });
}

await page.setViewportSize({ width: 390, height: 844 });
await page.locator(".qboard-slot canvas.student-canvas").first().evaluate((el) => {
  el.scrollIntoView({ block: "center" });
});
await sleep(400);
const before = await boardGeo(page);
const fx = before.x + before.w * 0.3;
const fy = before.y + before.h * 0.5;
await page.mouse.move(fx, fy);
await page.mouse.down();
for (let i = 1; i <= 5; i += 1) {
  await page.mouse.move(fx + i * 8, fy);
  await sleep(30);
}
const staffCookies = Object.entries(cfg.staffCookies || {})
  .map(([name, value]) => `${name}=${value}`)
  .join("; ");
const actStatus = await fetch(`${cfg.base}${cfg.minimizePath}`, {
  method: "PATCH",
  headers: { "Content-Type": "application/json", Cookie: staffCookies },
  body: JSON.stringify({ students_minimized: true }),
}).then((r) => r.status);
// Give the minimize postcard time to arrive; the stack must not rebuild yet.
await sleep(2000);
const mid = await boardGeo(page);
const midInk = await inkCount(page, ".qboard-slot canvas.live-canvas-ink");
for (let i = 6; i <= 12; i += 1) {
  await page.mouse.move(fx + i * 8, fy);
  await sleep(30);
}
await page.mouse.up();
let after = await boardGeo(page);
const t1 = Date.now();
while (
  Date.now() - t1 < 25000 &&
  after.minimizedBars === before.minimizedBars &&
  after.cards === before.cards
) {
  await sleep(100);
  after = await boardGeo(page);
}
await sleep(200);
after = await boardGeo(page);
const afterInk = await inkCount(page, ".qboard-slot canvas.student-canvas");
const topDelta = after.top == null || before.top == null ? null : Math.abs(after.top - before.top);
const midDelta = mid.top == null || before.top == null ? null : Math.abs(mid.top - before.top);

console.log(JSON.stringify({
  toolbar,
  actStatus,
  midStrokeDown: mid.strokeDown,
  midCards: mid.cards,
  midMinimized: mid.minimizedBars,
  midDelta,
  topDelta,
  midInk,
  afterInk,
  cardsBefore: before.cards,
  cardsAfter: after.cards,
  minimizedAfter: after.minimizedBars,
  minimizedBefore: before.minimizedBars,
  errors,
}));
await context.close();
await browser.close();
if (errors.length) process.exit(1);
