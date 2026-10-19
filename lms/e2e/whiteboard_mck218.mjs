/**
 * MCK-218 headless acceptance (dr eggbot). Args: baseUrl, outDir.
 */
import { chromium } from "playwright-core";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const base = process.argv[2] || "http://127.0.0.1:8787";
const outDir = process.argv[3] || "/opt/cursor/artifacts/mck218";

await mkdir(outDir, { recursive: true });

const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || "/usr/bin/google-chrome",
  headless: true,
});

const results = { checks: {}, errors: [] };

async function seedBoard(page) {
  await page.evaluate(() => {
    const board = window.__wbFormativeBoard;
    const lw = 720;
    const lh = 560;
    const strokes = [];
    for (const row of [
      { owner: "maya", count: 5, offsetX: 0, offsetY: 0 },
      { owner: "liam", count: 5, offsetX: 30, offsetY: 40 },
      { owner: "ava", count: 5, offsetX: 60, offsetY: 80 },
    ]) {
      for (let i = 0; i < row.count; i += 1) {
        strokes.push({
          id: `${row.owner}-${i}`,
          owner: row.owner,
          points: [
            [(40 + i * 18 + row.offsetX) / lw, (60 + row.offsetY) / lh],
            [(90 + i * 18 + row.offsetX) / lw, (120 + row.offsetY) / lh],
          ],
          mine: false,
        });
      }
    }
    board?.importRemote({ strokes, texts: [], cursors: [] }, { collab: true });
    board?.refreshFormative?.();
  });
}

async function openStudentPage() {
  const page = await browser.newPage({ viewport: { width: 390, height: 800 } });
  const url = `${base.replace(/\/$/, "")}/dev/whiteboard-formative/student`;
  await page.goto(url, { waitUntil: "networkidle" });
  await seedBoard(page);
  return page;
}

async function openTeacherPage() {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const url = `${base.replace(/\/$/, "")}/dev/whiteboard-formative/teacher`;
  await page.goto(url, { waitUntil: "networkidle" });
  await seedBoard(page);
  return page;
}

try {
  const student = await openStudentPage();
  await student.waitForTimeout(3100);
  results.checks.student_chips_hide_after_3s = (await student.locator(".name-chip").count()) === 0;

  const teacher = await openTeacherPage();
  const canvas = teacher.locator("#wb-canvas");
  const box = await canvas.boundingBox();
  await teacher.evaluate(() => {
    const canvas = document.getElementById("wb-canvas");
    const ink = window.__wbFormativeBoard?.getInk?.() || { strokes: [] };
    const stroke = (ink.strokes || [])[0];
    const pt = stroke?.points?.[0];
    if (!canvas || !pt) return;
    const lw = Number(canvas.dataset.logicalWidth) || canvas.width;
    const lh = Number(canvas.dataset.logicalHeight) || canvas.height;
    const rect = canvas.getBoundingClientRect();
    const x = rect.left + (pt.x / lw) * rect.width;
    const y = rect.top + (pt.y / lh) * rect.height;
    canvas.dispatchEvent(new MouseEvent("mousemove", { clientX: x, clientY: y, bubbles: true }));
  });
  await teacher.waitForTimeout(400);
  const hoverChips = await teacher.locator(".name-chip").count();
  results.checks.teacher_hover_no_name_chip = hoverChips === 0;

  const chipCount = hoverChips;
  const itemEstimate = await teacher.evaluate(() => {
    const ink = window.__wbFormativeBoard?.getInk?.() || { strokes: [], texts: [] };
    return (ink.strokes || []).length;
  });
  results.checks.chips_lte_items = chipCount <= itemEstimate;

  const textPage = await openStudentPage();
  await textPage.evaluate(() => window.__wbFormativeBoard?.openTextAt?.(0.2, 0.22));
  const textCanvas = textPage.locator("#wb-canvas");
  const editor = textPage.locator(".canvas-text-editor.wb-formative-text");
  await textPage.waitForFunction(() => { const el = document.querySelector(".canvas-text-editor.wb-formative-text"); return el && !el.hidden; });

  const widths = [];
  const longToken = "abcdefghijklmnopqrstuvwxyz".repeat(3);
  for (const ch of longToken) {
    await editor.type(ch, { delay: 2 });
    const w = await editor.evaluate((el) => el.getBoundingClientRect().width);
    widths.push(w);
  }
  const widthSpread = Math.max(...widths) - Math.min(...widths);
  results.checks.text_width_stable = widthSpread < 2;

  const tall = await editor.evaluate((el) => el.scrollHeight > 22);
  results.checks.text_wraps = tall;

  const rectBefore = await editor.boundingBox();
  await editor.type("xyz");
  const rectAfter = await editor.boundingBox();
  results.checks.typing_rect_stable =
    rectBefore &&
    rectAfter &&
    Math.abs(rectBefore.x - rectAfter.x) < 1 &&
    Math.abs(rectBefore.y - rectAfter.y) < 1;

  const peer = await browser.newPage({ viewport: { width: 390, height: 800 } });
  await peer.goto(`${base.replace(/\/$/, "")}/dev/whiteboard-formative/student`, {
    waitUntil: "networkidle",
  });
  await seedBoard(peer);
  await peer.evaluate(() => window.__wbFormativeBoard?.openTextAt?.(0.18, 0.25));
  const peerEditor = peer.locator(".canvas-text-editor.wb-formative-text");
  await peer.waitForFunction(() => { const el = document.querySelector(".canvas-text-editor.wb-formative-text"); return el && !el.hidden; });
  await peerEditor.type("draft-not-committed", { delay: 8 });
  const preCommit = await peer.evaluate(() => {
    const ink = window.__wbFormativeBoard?.getInk?.() || { texts: [] };
    return (ink.texts || []).map((row) => row.text).join("|");
  });
  results.checks.peer_pre_commit_hidden = !preCommit.includes("draft-not-committed");

  await peerEditor.press("Enter");
  await peer.waitForTimeout(200);
  await peer.waitForTimeout(300);
  const committed = await peer.evaluate(() => {
    const ink = window.__wbFormativeBoard?.getInk?.() || { texts: [] };
    return (ink.texts || []).some((row) => String(row.text || "").includes("draft-not-committed"));
  });
  results.checks.enter_commits = committed;

  await textPage.evaluate(() => window.__wbFormativeBoard?.openTextAt?.(0.35, 0.4));
  await textPage.waitForFunction(() => { const el = document.querySelector(".canvas-text-editor.wb-formative-text"); return el && !el.hidden; });
  await editor.fill("esc-cancel");
  await textPage.keyboard.press("Escape");
  await textPage.waitForTimeout(100);
  const escVisible = await editor.isVisible();
  const escInk = await textPage.evaluate(() => {
    const ink = window.__wbFormativeBoard?.getInk?.() || { texts: [] };
    return (ink.texts || []).some((row) => row.text === "esc-cancel");
  });
  results.checks.esc_cancels = !escVisible && !escInk;

  await textPage.evaluate(() => window.__wbFormativeBoard?.openTextAt?.(0.4, 0.45));
  await textPage.waitForFunction(() => { const el = document.querySelector(".canvas-text-editor.wb-formative-text"); return el && !el.hidden; });
  await editor.fill("blur-commit");
  await editor.evaluate((el) => el.blur());
  await textPage.waitForTimeout(150);
  const blurInk = await textPage.evaluate(() => {
    const ink = window.__wbFormativeBoard?.getInk?.() || { texts: [] };
    return (ink.texts || []).some((row) => row.text === "blur-commit");
  });
  results.checks.blur_commits = blurInk;

  await student.screenshot({ path: path.join(outDir, "acceptance-student-390.png") });
  await teacher.screenshot({ path: path.join(outDir, "acceptance-teacher-1280.png") });
} catch (err) {
  results.errors.push(String(err && err.message ? err.message : err));
}

await writeFile(path.join(outDir, "acceptance-mck218.json"), JSON.stringify(results, null, 2));
await browser.close();

const failed = Object.entries(results.checks).filter(([, ok]) => !ok);
if (results.errors.length || failed.length) {
  console.error(JSON.stringify({ failed, errors: results.errors }));
  process.exit(1);
}
console.log(JSON.stringify(results));
