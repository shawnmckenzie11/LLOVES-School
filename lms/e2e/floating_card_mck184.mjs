// MCK-184 browser check: a lifted (floating) question card keeps repainting.
// Driven by test_floating_card_mck184.py; prints one JSON result line.
import fs from "fs";
import { chromium } from "playwright-core";

const S = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const BASE = `http://127.0.0.1:${S.port}`;
const TABLET = { width: 820, height: 1180 };
const PHONE = { width: 390, height: 844 };
const out = { steps: [], errors: [] };
const browser = await chromium.launch({ executablePath: S.chrome, headless: true });

async function open(name, viewport) {
  const ctx = await browser.newContext({ viewport, isMobile: viewport.width < 720, hasTouch: true });
  await ctx.addCookies(Object.entries(S.students[name].cookies).map(([k, v]) => ({ name: k, value: v, url: BASE })));
  const page = await ctx.newPage();
  page.on("pageerror", (e) => out.errors.push(`${name}: ${String(e).slice(0, 300)}`));
  await page.goto(`${BASE}/student/home?v=${encodeURIComponent(S.students[name].v || "")}`);
  await page.waitForSelector(`[data-live-card-id="${S.turns_id}"] [data-rank-turns]`, { timeout: 30000 });
  return page;
}

const sel = (id) => `[data-live-card-id="${id}"]`;

function turnState(page, id = S.turns_id) {
  return page.evaluate((s) => {
    const card = document.querySelector(s);
    const block = card && card.querySelector("[data-rank-turns]");
    if (!block) return null;
    return {
      floating: card.classList.contains("is-floating"),
      rev: Number(block.dataset.turnRev || 0),
      cue: (block.querySelector(".rank-turn-cue") || {}).innerText || "",
      enabled: block.querySelectorAll("[data-rank-turn]:not([disabled])").length,
      undo: block.querySelectorAll("[data-rank-turn-undo]").length,
      spots: [...block.querySelectorAll(".rank-turn-spot.is-filled")].map((li) => li.innerText.replace(/\s+/g, " ").trim()),
    };
  }, sel(id));
}

async function waitFor(page, label, pred, getter = () => turnState(page), timeout = 30000) {
  const end = Date.now() + timeout;
  let last = null;
  while (Date.now() < end) {
    last = await getter();
    if (last && pred(last)) {
      out.steps.push({ step: label, ok: true, state: last });
      return last;
    }
    await page.waitForTimeout(250);
  }
  out.steps.push({ step: label, ok: false, state: last });
  return null;
}

async function lift(page, id) {
  // Title-bar drag past the ~4px threshold (tablet / desktop only).
  const bar = page.locator(`${sel(id)} [data-pane-drag]`);
  const box = await bar.boundingBox();
  await page.mouse.move(box.x + 30, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 60, box.y + box.height / 2 + 40, { steps: 6 });
  await page.mouse.up();
  return page.evaluate((s) => document.querySelector(s).classList.contains("is-floating"), sel(id));
}

async function place(page) {
  const ready = await waitFor(page, "can place", (t) => t.enabled > 0, () => turnState(page), 15000);
  if (!ready) throw new Error("no enabled option");
  out.steps.pop();
  // A repaint landing between press and release swallows a click (a person
  // just taps again), so re-tap once if this phone's own card did not move.
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const btn = page.locator(`${sel(S.turns_id)} [data-rank-turn]:not([disabled])`).first();
    const label = (await btn.innerText()).trim();
    await btn.click();
    const end = Date.now() + 8000;
    while (Date.now() < end) {
      const t = await turnState(page);
      if (t && t.rev > ready.rev) return label;
      await page.waitForTimeout(200);
    }
    out.retaps = (out.retaps || 0) + 1;
  }
  throw new Error("tap never landed");
}

try {
  const iggy = await open("Iggy", TABLET);
  const eva = await open("Eva", TABLET);
  // A: lift, then pick. The picker's own lifted card must show the pick.
  out.liftIggy = await lift(iggy, S.turns_id);
  const first = await place(iggy);
  await waitFor(iggy, "A picker's lifted card shows its own pick", (t) =>
    t.floating && t.rev >= 1 && t.spots.length === 1 && t.spots[0].includes(first) && t.spots[0].includes("You") && t.undo === 1 && t.enabled === 0);
  await waitFor(eva, "A teammate sees it and may place", (t) => t.rev >= 1 && t.enabled > 0 && t.undo === 0);
  const second = await place(eva);
  await waitFor(iggy, "A picker's lifted card gets turn 2", (t) =>
    t.rev >= 2 && t.spots.length === 2 && t.spots[1].includes(second) && t.enabled > 0 && t.undo === 0);
  // B: place, lift, then the teammate places. Eva's lifted card must update.
  out.liftEva = await lift(eva, S.turns_id);
  const third = await place(iggy);
  await waitFor(eva, "B lifted card shows the teammate's pick and my turn", (t) =>
    t.floating && t.rev >= 3 && t.spots.length === 3 && t.spots[2].includes(third) && t.enabled > 0 && t.undo === 0);
  await waitFor(iggy, "B only the last placer has Undo", (t) => t.undo === 1 && t.enabled === 0);
  // Another question type: a lifted multiple-choice card repaints after Submit.
  out.liftMc = await lift(iggy, S.mc_id);
  await iggy.locator(`${sel(S.mc_id)} [data-live-choice]`).first().click();
  const before = await iggy.locator(sel(S.mc_id)).innerText();
  await iggy.locator(`${sel(S.mc_id)} [data-live-submit]`).first().click();
  await waitFor(iggy, "lifted MC card repaints after Submit", (m) => m.floating && m.text !== before && m.submitButtons === 0,
    () => iggy.evaluate((s) => {
      const card = document.querySelector(s);
      return card && {
        floating: card.classList.contains("is-floating"),
        text: card.innerText,
        submitButtons: card.querySelectorAll("[data-live-submit]:not([disabled])").length,
      };
    }, sel(S.mc_id)));
  // Phone: no resize pill and a touch on the card never lifts it.
  const ava = await open("Ava", PHONE);
  const pill = await ava.evaluate((s) => {
    const el = document.querySelector(`${s} [data-pane-resize]`);
    return el ? getComputedStyle(el).display : "missing";
  }, sel(S.turns_id));
  await ava.locator(`${sel(S.turns_id)} [data-pane-drag]`).tap();
  await ava.locator(`${sel(S.turns_id)} .student-live-card-head`).tap();
  out.phone = {
    pill,
    floating: await ava.evaluate((s) => document.querySelector(s).classList.contains("is-floating"), sel(S.turns_id)),
  };
} catch (err) {
  out.errors.push(String(err && err.stack ? err.stack : err).slice(0, 800));
} finally {
  await browser.close();
  console.log(JSON.stringify(out));
}
