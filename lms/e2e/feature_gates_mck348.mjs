// MCK-348: live-class surfaces follow the owning teacher's feature flags.
import fs from "fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright-core");

const spec = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const browser = await chromium.launch({
  executablePath: spec.chrome,
  headless: true,
});

/**
 * Open one page, count feature surfaces, and record gated requests.
 * @param {object} shot
 * @returns {Promise<object>}
 */
async function readPage(shot) {
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
  });
  const base = `http://127.0.0.1:${spec.port}`;
  if (shot.cookies) {
    await context.addCookies(
      Object.entries(shot.cookies).map(([name, value]) => ({
        name,
        value,
        url: base,
      })),
    );
  }
  const page = await context.newPage();
  const errors = [];
  const requests = [];
  page.on("pageerror", (err) => errors.push(String(err).slice(0, 240)));
  page.on("request", (req) => {
    const path = req.url().replace(base, "").split("?")[0];
    if (
      path.includes("/active-media") ||
      path.includes("whiteboard-gallery") ||
      path.includes("/board/") ||
      path.includes("team-draw") ||
      path.includes("/canvas-presence")
    ) {
      requests.push(`${req.method()} ${path}`);
    }
  });
  await page.goto(`${base}${shot.path}`, { waitUntil: "domcontentloaded" });
  const skips = page.locator("[data-tour-skip]");
  const skipCount = await skips.count();
  for (let i = 0; i < skipCount; i += 1) {
    const skip = skips.nth(i);
    if (await skip.isVisible().catch(() => false)) {
      await skip.click();
    }
  }
  const ready = shot.ready || "#live-content-tabs";
  await page.waitForSelector(ready, { timeout: 15000, state: "attached" });
  await page.waitForTimeout(shot.waitMs || 1000);
  const counts = await page.evaluate(() => {
    const tab = (name) =>
      document.querySelectorAll(`#live-content-tabs [data-tab="${name}"]`).length;
    const id = (name) => document.querySelectorAll(`#${name}`).length;
    let flags = {};
    try {
      flags = JSON.parse(document.getElementById("lloves-features")?.textContent || "{}");
    } catch (_err) {
      flags = { parseError: true };
    }
    return {
      media: tab("media"),
      questions: tab("questions"),
      whiteboard: tab("canvas"),
      slides: tab("slides"),
      importForm: id("set-class-slides-form"),
      googleStage: id("live-google-slides-stage"),
      refresh: id("wb-live-refresh"),
      livePill: document.querySelectorAll(".wb-live-pill").length,
      canvasZone: id("canvas-zone"),
      mediaZone: id("media-artifact-zone"),
      mediaPane: id("media-pane"),
      canvasPane: id("canvas-pane"),
      cursors: id("canvas-cursors"),
      slidesCard: id("slides-card"),
      slidesFrame: id("slides-frame"),
      teamDrawPhone: id("team-draw-phone"),
      teamDrawMystery: id("team-draw-mystery"),
      portfolioDoc: id("portfolio-drive"),
      flags,
    };
  });
  const later = page.locator("#live-catchup-pin-later");
  if (await later.isVisible().catch(() => false)) {
    await later.click();
    await page.waitForTimeout(200);
  }
  if (shot.shot) {
    await page.screenshot({ path: shot.shot, fullPage: true });
  }
  await context.close();
  return { errors, counts, requests, width: 1280 };
}

const out = {};
for (const shot of spec.pages) {
  out[shot.name] = await readPage(shot);
}
await browser.close();
process.stdout.write(JSON.stringify(out) + "\n");
