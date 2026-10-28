// MCK-334 browser check: Forward keeps the slide header and the student pane.
// Driven by test_mck334_slide_follow.py. Prints one JSON result line.
import fs from "fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright-core");

const S = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const BASE = `http://127.0.0.1:${S.port}`;
const out = { steps: [], errors: [], slides: [], overlap: [] };

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function readText(locator) {
  return ((await locator.innerText().catch(() => "")) || "").replace(/\s+/g, " ").trim();
}

async function waitText(locator, wanted, timeout) {
  const end = Date.now() + timeout;
  let last = "";
  while (Date.now() < end) {
    last = await readText(locator);
    if (last === wanted || last.includes(wanted)) return last;
    await sleep(80);
  }
  throw new Error(`wanted ${wanted}, saw ${last}`);
}

function boxesOverlap(a, b) {
  if (!a || !b) return true;
  return a.x + a.width > b.x + 0.5 && a.x < b.x + b.width - 0.5 && a.y + a.height > b.y + 0.5 && a.y < b.y + b.height - 0.5;
}

const browser = await chromium.launch({
  executablePath: S.chrome,
  headless: true,
});

function cookies(jar) {
  return Object.entries(jar).map(([name, value]) => ({
    name,
    value,
    url: BASE,
  }));
}

const staffContext = await browser.newContext({ viewport: { width: 1280, height: 800 } });
await staffContext.addCookies(cookies(S.staff_cookies));
const staff = await staffContext.newPage();
staff.on("pageerror", (err) => out.errors.push(String(err).slice(0, 300)));
await staff.goto(`${BASE}/staff/class/${S.class_id}?tab=live`, { waitUntil: "domcontentloaded" });
await waitText(staff.locator("#live-page-counter"), "1 / 13", 20000);
const skipTour = staff.locator("[data-tour-skip]").first();
if (await skipTour.isVisible().catch(() => false)) {
  await skipTour.click();
  await sleep(200);
}

for (const width of [1280, 1024]) {
  await staff.setViewportSize({ width, height: 800 });
  await sleep(120);
  const name = await staff.locator("#live-page-name").boundingBox();
  const eyes = await staff.locator("#live-eyes-up").boundingBox();
  const view = await staff.locator("#live-student-view").boundingBox();
  const title = (await staff.locator("#live-page-name").getAttribute("title")) || "";
  const hitEyes = boxesOverlap(name, eyes);
  const hitView = boxesOverlap(name, view);
  const capped = Boolean(name && name.width <= 18 * 16 + 2);
  out.overlap.push({
    width,
    titleLen: title.length,
    nameWidth: name ? name.width : 0,
    eyes,
    view,
  });
  out.steps.push({
    step: `title ${width}`,
    ok: title.length === 120 && capped && !hitEyes && !hitView,
  });
}
await staff.setViewportSize({ width: 1280, height: 800 });

const studentContext = await browser.newContext({ viewport: { width: 1100, height: 800 } });
await studentContext.addCookies(cookies(S.student_cookies));
const student = await studentContext.newPage();
student.on("pageerror", (err) => out.errors.push(String(err).slice(0, 300)));
await student.goto(`${BASE}/student/home?v=${encodeURIComponent(S.visit || "")}`, {
  waitUntil: "domcontentloaded",
});

async function studentSlide() {
  const frame = student.locator("#slides-frame");
  const card = student.locator("#slides-card");
  const empty = student.locator("#slides-empty");
  const frameHidden = (await frame.getAttribute("hidden")) !== null;
  const frameBox = await frame.boundingBox();
  const cardHidden = (await card.getAttribute("hidden")) !== null;
  const src = (await card.getAttribute("src")) || "";
  const alt = (await card.getAttribute("alt")) || "";
  const emptyHidden = (await empty.getAttribute("hidden")) !== null;
  const emptyText = await readText(empty);
  return {
    frameHidden,
    frameBox: frameBox ? frameBox.width * frameBox.height : 0,
    cardHidden,
    src,
    alt,
    emptyHidden,
    emptyText,
  };
}

const waitPane = Date.now() + 20000;
let waiting = await studentSlide();
while (
  Date.now() < waitPane &&
  !(waiting.frameHidden && waiting.emptyText === "Teacher hasn't shared this slide yet")
) {
  await sleep(100);
  waiting = await studentSlide();
}
out.steps.push({
  step: "unpublished pane",
  ok:
    waiting.frameHidden &&
    waiting.frameBox === 0 &&
    waiting.cardHidden &&
    waiting.emptyText === "Teacher hasn't shared this slide yet",
  state: {
    frameHidden: waiting.frameHidden,
    frameBox: waiting.frameBox,
    cardHidden: waiting.cardHidden,
    emptyHidden: waiting.emptyHidden,
    emptyText: waiting.emptyText,
  },
});

await staff.locator('[data-surface-publish="slides"]').click();
await waitText(staff.locator('[data-surface-status="slides"]'), "ACTIVE", 8000);
const forwardReady = Date.now() + 2000;
while (Date.now() < forwardReady && (await staff.locator("#live-stage-next").isDisabled())) {
  await sleep(40);
}

let previous = "";
for (let step = 1; step <= 7; step += 1) {
  if (step > 1) {
    const ready = Date.now() + 3000;
    while (Date.now() < ready && (await staff.locator("#live-stage-next").isDisabled())) {
      await sleep(40);
    }
    await staff.locator("#live-stage-next").click();
  }
  const header = await waitText(staff.locator("#live-page-counter"), `${step} / 13`, 3000);
  const badge = await readText(staff.locator('[data-surface-status="slides"]'));
  const forwardDisabled = await staff.locator("#live-stage-next").isDisabled();
  const until = Date.now() + 15000;
  let seen = await studentSlide();
  while (Date.now() < until && !(seen.src && seen.src !== previous && !seen.cardHidden && seen.frameHidden)) {
    await sleep(80);
    seen = await studentSlide();
  }
  let imageStatus = 0;
  if (seen.src) {
    const url = seen.src.startsWith("http") ? seen.src : `${BASE}${seen.src}`;
    const response = await student.request.get(url);
    imageStatus = response.status();
  }
  previous = seen.src;
  out.slides.push(seen.alt || "");
  out.steps.push({
    step: `slide ${step}`,
    ok:
      badge.toUpperCase() === "ACTIVE" &&
      header === `${step} / 13` &&
      !forwardDisabled &&
      seen.frameHidden &&
      seen.frameBox === 0 &&
      !seen.cardHidden &&
      imageStatus === 200 &&
      seen.alt === `Slide ${step}`,
    imageStatus,
    forwardDisabled,
    frameHidden: seen.frameHidden,
    cardHidden: seen.cardHidden,
  });
}

await browser.close();
process.stdout.write(`${JSON.stringify(out)}\n`);
