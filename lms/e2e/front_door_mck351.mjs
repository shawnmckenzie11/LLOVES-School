// MCK-351: landing, unknown-account page, and request confirmation.
// Driven by test_front_door_mck351.py. Prints one JSON result line.
import fs from "fs";
import path from "path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright-core");

const S = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const BASE = `http://127.0.0.1:${S.port}`;
const outDir = S.outDir;

const VIEWPORTS = [
  { label: "desktop", width: 1280, height: 800, mobile: false },
  {
    label: "mobile",
    width: 375,
    height: 667,
    mobile: true,
  },
];

/**
 * Drop addresses from a failure string before it is printed.
 *
 * @param {unknown} err
 * @returns {string}
 */
function safe(err) {
  return String(err && err.stack ? err.stack : err).replace(/[\w.+-]+@[\w.-]+/g, "[redacted]");
}

/**
 * True when the element's box sits fully inside the viewport.
 *
 * @param {import("playwright-core").Page} page
 * @param {string} selector
 * @returns {Promise<boolean>}
 */
async function inViewport(page, selector) {
  const box = await page.locator(selector).boundingBox();
  const viewport = page.viewportSize();
  if (!box || !viewport) return false;
  return box.y < viewport.height && box.x < viewport.width && box.y + box.height > 0 && box.x + box.width > 0;
}

/**
 * True when the document is no wider than the window.
 *
 * @param {import("playwright-core").Page} page
 * @returns {Promise<boolean>}
 */
async function noHorizontalScroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
}

const browser = await chromium.launch({
  executablePath: S.chrome,
  headless: true,
});

const result = { errors: [], steps: [], shots: [] };

try {
  fs.mkdirSync(outDir, { recursive: true });
  for (const viewport of VIEWPORTS) {
    const contextOptions = {
      viewport: { width: viewport.width, height: viewport.height },
      isMobile: viewport.mobile,
      hasTouch: viewport.mobile,
    };
    if (viewport.mobile) {
      contextOptions.userAgent =
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1";
    }
    const context = await browser.newContext(contextOptions);
    const page = await context.newPage();
    const label = viewport.label;
    try {
      await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
      const topInView = await inViewport(page, "#landing-request-access");
      const inlineInView = await inViewport(page, "#landing-request-access-inline");
      if (!topInView && !inlineInView) {
        throw new Error(`${label} landing CTA is below the fold`);
      }
      const landingShot = path.join(outDir, `p0-6-landing-${label}.png`);
      await page.screenshot({ path: landingShot });
      result.shots.push(landingShot);
      const landingFits = await noHorizontalScroll(page);
      if (viewport.mobile && !landingFits) {
        throw new Error(`${label} landing scrolls sideways`);
      }
      const clickId = topInView ? "#landing-request-access" : "#landing-request-access-inline";
      await page.click(clickId);
      await page.waitForURL("**/request-access");
      const landingH1 = (await page.locator("h1").innerText()).trim();
      if (landingH1 !== "Request access") {
        throw new Error(`${label} landing click landed on ${landingH1}`);
      }
      result.steps.push({ name: `${label}-landing`, ok: true, clicked: clickId });

      const denied = await page.goto(`${BASE}${S.forbiddenPath}`, { waitUntil: "domcontentloaded" });
      if (!denied || denied.status() !== 403) {
        throw new Error(`${label} unknown-account status ${denied ? denied.status() : "none"}`);
      }
      await page.locator("#forbidden-request-access").waitFor({ state: "visible" });
      const deniedShot = path.join(outDir, `p0-6-403-${label}.png`);
      await page.screenshot({ path: deniedShot });
      result.shots.push(deniedShot);
      if (viewport.mobile && !(await noHorizontalScroll(page))) {
        throw new Error(`${label} unknown-account page scrolls sideways`);
      }
      await page.click("#forbidden-request-access");
      await page.waitForURL("**/request-access");
      const deniedH1 = (await page.locator("h1").innerText()).trim();
      if (deniedH1 !== "Request access") {
        throw new Error(`${label} unknown-account click landed on ${deniedH1}`);
      }
      result.steps.push({ name: `${label}-403`, ok: true });

      await page.goto(`${BASE}/request-access`, { waitUntil: "domcontentloaded" });
      await page.fill("#name", S.applicant.name);
      await page.fill("#email", S.applicant.email);
      await page.selectOption("#role", "teacher");
      await page.fill("#context", S.applicant.context);
      await page.click('button[type="submit"]');
      await page.locator("#request-access-confirmed").waitFor({ state: "visible" });
      const confirmShot = path.join(outDir, `p0-6-confirm-${label}.png`);
      await page.screenshot({ path: confirmShot });
      result.shots.push(confirmShot);
      if (viewport.mobile && !(await noHorizontalScroll(page))) {
        throw new Error(`${label} confirmation scrolls sideways`);
      }
      result.steps.push({ name: `${label}-confirm`, ok: true });
    } catch (err) {
      result.errors.push(safe(err));
      result.steps.push({ name: label, ok: false });
    } finally {
      await context.close();
    }
  }
} catch (err) {
  result.errors.push(safe(err));
} finally {
  await browser.close();
}

console.log(JSON.stringify(result));
process.exit(result.errors.length ? 1 : 0);
