/**
 * MCK-218 headless acceptance (dr eggbot). Args: baseUrl, role, outDir.
 */
import { chromium } from "playwright-core";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const base = process.argv[2] || "http://127.0.0.1:8787";
const role = process.argv[3] || "student";
const outDir = process.argv[4] || "/opt/cursor/artifacts/mck218";
const width = role === "teacher" ? 1280 : 390;

await mkdir(outDir, { recursive: true });

const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || "/usr/bin/google-chrome",
  headless: true,
});
const page = await browser.newPage({ viewport: { width, height: 800 } });
const url = `${base.replace(/\/$/, "")}/dev/whiteboard-formative/${role}`;
await page.goto(url, { waitUntil: "networkidle" });

await page.evaluate(() => {
  window.__wbFormativeSeed([
    { owner: "maya", count: 5, offsetX: 0, offsetY: 0 },
    { owner: "liam", count: 5, offsetX: 30, offsetY: 40 },
    { owner: "ava", count: 5, offsetX: 60, offsetY: 80 },
  ]);
});

const results = {};

if (role === "student") {
  await page.waitForTimeout(3100);
  results.chips_after_3s = await page.locator(".name-chip").count();
  await page.screenshot({ path: path.join(outDir, "student-390.png") });
} else {
  const canvas = page.locator("#wb-canvas");
  const box = await canvas.boundingBox();
  if (box) {
    await page.mouse.move(box.x + 50, box.y + 70);
    await page.waitForTimeout(100);
    results.chips_on_hover = await page.locator(".name-chip").count();
  }
  await page.screenshot({ path: path.join(outDir, "teacher-1280.png") });
}

const chipCount = await page.locator(".name-chip").count();
const itemEstimate = await page.evaluate(() => {
  const ink = window.__wbFormativeBoard?.getInk?.() || { strokes: [], texts: [] };
  return (ink.strokes || []).length;
});
results.chips_lte_items = chipCount <= itemEstimate;

await writeFile(path.join(outDir, `acceptance-${role}.json`), JSON.stringify(results, null, 2));
await browser.close();

if (role === "student" && results.chips_after_3s !== 0) {
  process.exit(2);
}
if (role === "teacher" && results.chips_on_hover !== 1) {
  process.exit(3);
}
if (!results.chips_lte_items) {
  process.exit(4);
}
