/**
 * Click × on every open question card and expect a right-panel chip.
 * mode "reload" minimizes one card, reloads, and checks it stays docked.
 * The config path is local and is not printed.
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
const context = await browser.newContext({ viewport: { width: 1100, height: 800 } });
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
