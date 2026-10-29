// MCK-348: which live-class tabs a basic teacher and a full teacher see.
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
 * Open one live-class page and count the content controls.
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
  page.on("pageerror", (err) => errors.push(String(err).slice(0, 240)));
  await page.goto(`${base}${shot.path}`, { waitUntil: "domcontentloaded" });
  const skip = page.locator("[data-tour-skip]").first();
  if (await skip.isVisible().catch(() => false)) {
    await skip.click();
  }
  await page.waitForSelector("#live-content-tabs", { timeout: 15000, state: "attached" });
  const counts = await page.evaluate(() => {
    const tab = (name) =>
      document.querySelectorAll(`#live-content-tabs [data-tab="${name}"]`).length;
    return {
      media: tab("media"),
      questions: tab("questions"),
      whiteboard: tab("canvas"),
      slides: tab("slides"),
      importForm: document.querySelectorAll("#set-class-slides-form").length,
      googleStage: document.querySelectorAll("#live-google-slides-stage").length,
      refresh: document.querySelectorAll("#wb-live-refresh").length,
      livePill: document.querySelectorAll(".wb-live-pill").length,
    };
  });
  if (shot.shot) {
    await page.screenshot({ path: shot.shot, fullPage: true });
  }
  await context.close();
  return { errors, counts, width: 1280 };
}

const basic = await readPage(spec.basic);
const full = await readPage(spec.full);
await browser.close();
process.stdout.write(JSON.stringify({ basic, full }) + "\n");
