/**
 * Click × on every open question card and expect a right-panel chip.
 * mode "reload" minimizes one card, reloads, and checks it stays docked.
 * The config path is local and is not printed.
 */
import { createRequire } from "node:module";
import { readFileSync, writeFileSync } from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright-core");

const cfg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const launchArgs = ["--no-sandbox", "--disable-dev-shm-usage"];
let browser;
let context;
if (cfg.userDataDir) {
  context = await chromium.launchPersistentContext(cfg.userDataDir, {
    executablePath: cfg.chrome,
    headless: true,
    viewport: { width: 1100, height: 800 },
    args: launchArgs,
  });
  browser = context.browser();
} else {
  browser = await chromium.launch({
    executablePath: cfg.chrome,
    headless: true,
    args: launchArgs,
  });
  context = await browser.newContext({ viewport: { width: 1100, height: 800 } });
}
if (cfg.userDataDir) await context.clearCookies();
const cookies = Object.entries(cfg.cookies).map(([name, value]) => ({
  name,
  value,
  url: cfg.base,
}));
await context.addCookies(cookies);
const page = await context.newPage();
const errors = [];
page.on("pageerror", (err) => errors.push(String(err).slice(0, 180)));
/**
 * Close the catch-up PIN dialog so it cannot cover the question card.
 * @param {import("playwright-core").Page} target
 */

/**
 * Count dock entries without returning the key, which can hold a visit token.
 * @param {import("playwright-core").Page} target
 * @returns {Promise<{stored: number, scoped: boolean}>}
 */
async function dockState(target) {
  return target.evaluate(() => {
    const keys = [];
    try {
      for (let i = 0; i < sessionStorage.length; i += 1) {
        const key = sessionStorage.key(i);
        if (key && key.startsWith("lloves.dockedQuestions")) keys.push(key);
      }
    } catch (_err) {
      return { stored: -1, scoped: false };
    }
    const scoped = keys.some((key) => {
      const rest = key.slice("lloves.dockedQuestions:".length);
      const parts = rest.split(":");
      return parts.length >= 2 && parts[0].length > 1 && parts[1].length > 0;
    });
    return { stored: keys.length, scoped };
  });
}

async function closeDialogs(target) {
  await target.evaluate(() => {
    document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  });
}

await page.goto(`${cfg.base}${cfg.path}`, { waitUntil: "domcontentloaded" });
await page.locator(".student-live-card").first().waitFor({ timeout: 15000 });
await closeDialogs(page);

if (cfg.mode === "reload") {
  const card = page.locator(".student-live-card").first();
  await card.locator('[aria-label="Minimize question"]').evaluate((button) => button.click());
  await page.locator(".student-question-dock-chip.is-question").first().waitFor({ timeout: 5000 });
  await page.reload({ waitUntil: "domcontentloaded" });
  await closeDialogs(page);
  await page.locator(".student-question-dock-chip.is-question").first().waitFor({ timeout: 15000 });
  const openAfterReload = await page.locator(".student-live-card").count();
  const chipsAfterReload = await page.locator(".student-question-dock-chip.is-question").count();
  await page.locator(".student-question-dock-chip.is-question [data-undock-live-card]").first().evaluate((button) => button.click());
  await page.locator(".student-live-card").first().waitFor({ timeout: 5000 });
  const text = (await page.locator(".student-live-card").first().innerText()).replace(/\s+/g, " ");
  await browser.close();
  console.log(JSON.stringify({
    errors,
    openAfterReload,
    chipsAfterReload,
    showsAnswer: text.includes("Red"),
    showsScore: text.includes("100%"),
  }));
  process.exit(0);
}

if (cfg.mode === "second") {
  await page.locator('[aria-label="Minimize question"]').first().evaluate((button) => button.click());
  await page.locator(".student-question-dock-chip.is-question").first().waitFor({ timeout: 5000 });
  const first = await dockState(page);
  await context.clearCookies();
  const nextCookies = Object.entries(cfg.cookiesB).map(([name, value]) => ({
    name,
    value,
    url: cfg.base,
  }));
  await context.addCookies(nextCookies);
  await page.goto(`${cfg.base}${cfg.pathB}`, { waitUntil: "domcontentloaded" });
  await page.locator(".student-live-card").first().waitFor({ timeout: 15000 });
  await closeDialogs(page);
  const openForSecond = await page.locator(".student-live-card").count();
  const chipsForSecond = await page.locator(".student-question-dock-chip.is-question").count();
  const kept = await dockState(page);
  await page.goto(`${cfg.base}/logout`, { waitUntil: "domcontentloaded" });
  const afterLogout = await dockState(page);
  await browser.close();
  console.log(JSON.stringify({
    errors,
    firstStored: first.stored,
    firstScoped: first.scoped,
    openForSecond,
    chipsForSecond,
    keptStored: kept.stored,
    afterLogout: afterLogout.stored,
  }));
  process.exit(0);
}

if (cfg.mode === "quit-rerun") {
  await page.locator('[aria-label="Minimize question"]').first().evaluate((button) => button.click());
  await page.locator(".student-question-dock-chip.is-question").first().waitFor({ timeout: 5000 });
  const parked = await dockState(page);
  await page.goto(`${cfg.base}/static/lloves.css`, { waitUntil: "domcontentloaded" });
  writeFileSync(cfg.handoff, JSON.stringify({ phase: "parked" }));
  let nextPath = "";
  for (let i = 0; i < 80; i += 1) {
    const note = JSON.parse(readFileSync(cfg.handoff, "utf8"));
    if (note.phase === "ready" && note.path) {
      nextPath = note.path;
      break;
    }
    await page.waitForTimeout(250);
  }
  if (!nextPath) throw new Error("new run was not ready");
  await page.goto(`${cfg.base}${nextPath}`, { waitUntil: "domcontentloaded" });
  await page.locator(".student-live-card").first().waitFor({ timeout: 15000 });
  await closeDialogs(page);
  const after = await dockState(page);
  const openCards = await page.locator(".student-live-card").count();
  const chips = await page.locator(".student-question-dock-chip.is-question").count();
  await browser.close();
  console.log(JSON.stringify({
    errors,
    parkedStored: parked.stored,
    parkedScoped: parked.scoped,
    stored: after.stored,
    openCards,
    chips,
  }));
  process.exit(0);
}

const saveControl = await page.getByText("Save to card", { exact: true }).count();
const opened = await page.locator(".student-live-card").count();
const stems = [];
for (let i = 0; i < opened; i += 1) {
  const card = page.locator(".student-live-card").first();
  const stem = (await card.locator("h2").innerText()).replace(/\s+/g, " ").trim();
  stems.push(stem.slice(0, 24));
  await card.locator('[aria-label="Minimize question"]').evaluate((button) => button.click());
  await page.locator(".student-question-dock-chip.is-question").nth(i).waitFor({ timeout: 5000 });
}
const chips = await page.locator(".student-question-dock-chip.is-question").count();
const openAfter = await page.locator(".student-live-card").count();
await page.locator(".student-question-dock-chip.is-question [data-undock-live-card]").first().evaluate((button) => button.click());
await page.locator(".student-live-card").first().waitFor({ timeout: 5000 });
const restored = page.locator(".student-live-card").first();
const badge = (await restored.locator(".student-live-card-status").innerText()).trim();
const keptStem = (await restored.locator("h2").innerText()).replace(/\s+/g, " ").trim();
await browser.close();
const missing = (cfg.stems || []).filter(
  (stem) => !stems.some((seen) => seen.startsWith(stem))
);
console.log(JSON.stringify({
  errors,
  saveControl,
  opened,
  chips,
  openAfter,
  badge,
  keptStem: keptStem.slice(0, 24),
  stems,
  missing,
}));
