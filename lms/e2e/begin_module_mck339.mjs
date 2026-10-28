// MCK-339: Begin new module dialog at a 1280px viewport.
import fs from "fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright-core");

const spec = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const base = `http://127.0.0.1:${spec.port}`;
const browser = await chromium.launch({
  executablePath: spec.chrome,
  headless: true,
});
const context = await browser.newContext({
  viewport: { width: 1280, height: 900 },
});
if (spec.cookies) {
  await context.addCookies(
    Object.entries(spec.cookies).map(([name, value]) => ({
      name,
      value,
      url: base,
    })),
  );
}
const page = await context.newPage();
const errors = [];
page.on("pageerror", (err) => errors.push(String(err).slice(0, 300)));
const out = { errors, steps: [], width: 0 };

function step(name, ok, extra) {
  out.steps.push({ step: name, ok, ...(extra || {}) });
}

await page.goto(`${base}${spec.path}`, { waitUntil: "domcontentloaded" });
const skip = page.locator("[data-tour-skip]").first();
if (await skip.isVisible().catch(() => false)) {
  await skip.click();
}
await page.waitForSelector("#begin-new-module", { timeout: 15000 });
const width = page.viewportSize().width;
out.width = width;
const row = await page.evaluate(() => {
  const title = document.querySelector(".begin-mod-head h2");
  const button = document.getElementById("begin-new-module");
  const titleBox = title.getBoundingClientRect();
  const buttonBox = button.getBoundingClientRect();
  return {
    title: title.textContent.trim(),
    button: button.textContent.trim(),
    disabled: button.disabled,
    sameRow: Math.abs(titleBox.top - buttonBox.top) < 12,
    current: document.getElementById("begin-mod-current").textContent.trim(),
  };
});
step("header", row.title === "Attendance & Participation" && row.button === "Begin new module" && row.sameRow && !row.disabled, row);

await page.click("#begin-new-module");
await page.waitForSelector("#begin-mod-dialog", { state: "visible" });
const dialog = await page.evaluate(() => {
  const node = document.getElementById("begin-mod-dialog");
  const confirm = document.getElementById("begin-mod-confirm");
  const style = getComputedStyle(confirm);
  return {
    open: node.open,
    title: document.getElementById("begin-mod-title").textContent.trim(),
    body: node.innerText,
    cancel: document.getElementById("begin-mod-cancel").textContent.trim(),
    confirm: confirm.textContent.trim(),
    focused: document.activeElement && document.activeElement.id,
    danger: confirm.className.toLowerCase().includes("danger"),
    blue: style.backgroundColor,
  };
});
const frozen = dialog.body.toLowerCase().includes("frozen");
step("dialog", dialog.open && dialog.title === spec.title && dialog.cancel === "Cancel" && dialog.confirm === spec.confirm && dialog.focused === "begin-mod-cancel" && !dialog.danger && !frozen && spec.lines.every((line) => dialog.body.includes(line)), {
  title: dialog.title,
  focused: dialog.focused,
  blue: dialog.blue,
  frozen,
});

await page.keyboard.press("Escape");
const closed = await page.evaluate(() => !document.getElementById("begin-mod-dialog").open);
step("escape", closed);

await page.click("#begin-new-module");
await page.waitForSelector("#begin-mod-dialog", { state: "visible" });
if (spec.dialogShot) {
  await page.screenshot({ path: spec.dialogShot, fullPage: false });
}
let disabledInFlight = false;
await page.route("**/participation/modules/begin", async (route) => {
  disabledInFlight = await page.locator("#begin-new-module").isDisabled();
  await route.continue();
});
await page.click("#begin-mod-confirm");
await page.waitForSelector("#begin-mod-toast", { timeout: 15000 });
const after = await page.evaluate(() => ({
  current: document.getElementById("begin-mod-current").textContent.trim(),
  toast: document.getElementById("begin-mod-toast").textContent.trim(),
  past: document.getElementById("begin-mod-past-select")
    ? document.getElementById("begin-mod-past-select").selectedOptions[0].textContent.trim()
    : "",
  wrapped: (document.querySelector(".begin-mod-past-panel:not([hidden]) .begin-mod-wrapped") || {}).textContent || "",
  zeros: document.querySelectorAll("#begin-mod-current-table .begin-mod-attended").length
    ? [...document.querySelectorAll("#begin-mod-current-table .begin-mod-attended")].every((cell) => cell.textContent.trim() === "0 / 0")
    : false,
  percents: [...document.querySelectorAll("#begin-mod-current-table .begin-mod-percent")].map((cell) => cell.textContent.trim()),
  reason: (document.getElementById("begin-mod-reason") || {}).textContent || "",
  buttonDisabled: document.getElementById("begin-new-module").disabled,
}));
step("in-flight", disabledInFlight);
step("after", after.current === spec.afterCurrent && after.toast === spec.toast && after.past === spec.pastLabel && after.wrapped.trim().startsWith("Wrapped up ") && after.zeros && after.percents.every((text) => text === "—") && after.buttonDisabled && after.reason.includes(spec.emptyReason), after);

await page.screenshot({ path: spec.shot, fullPage: false });
await browser.close();
process.stdout.write(`${JSON.stringify(out)}\n`);
