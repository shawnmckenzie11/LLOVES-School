// MCK-514: a mark at one spot on the background image lands on that spot
// on the projected board, the teacher thumbnail, and the phone.
import { fittedImageRect } from "./board_background.js";
import { SHOWN_BOARD_SIZE, place } from "./poll_board_show.js";

function fail(message) {
  console.error(message);
  process.exit(1);
}

const close = (a, b) => Math.abs(a - b) < 1e-6;

// A tall image and a wide image, both letterboxed on a 2:1 board.
for (const [iw, ih] of [[400, 400], [1600, 500], [300, 900]]) {
  // The student draws on the phone at 40% across and 70% down the image.
  const phone = { w: 320, h: 160 };
  const phoneRect = fittedImageRect(phone.w, phone.h, iw, ih);
  if (!phoneRect) fail("no phone rect");
  const phoneX = phoneRect.x + 0.4 * phoneRect.w;
  const phoneY = phoneRect.y + 0.7 * phoneRect.h;
  // Stored ink is 0–1 of the board.
  const stored = { x: phoneX / phone.w, y: phoneY / phone.h };
  for (const board of [SHOWN_BOARD_SIZE, { w: 1440, h: 720 }, { w: 180, h: 90 }]) {
    const rect = fittedImageRect(board.w, board.h, iw, ih);
    const want = { x: rect.x + 0.4 * rect.w, y: rect.y + 0.7 * rect.h };
    const got =
      board === SHOWN_BOARD_SIZE
        ? place(stored)
        : { x: stored.x * board.w, y: stored.y * board.h };
    if (!close(got.x, want.x) || !close(got.y, want.y)) {
      fail(`mark moved off the image on ${board.w}x${board.h} for ${iw}x${ih}: ${JSON.stringify({ got, want })}`);
    }
  }
}

// Fit rule: centred and fully inside the board.
const r = fittedImageRect(720, 360, 1000, 1000);
if (!close(r.w, 360) || !close(r.h, 360) || !close(r.x, 180) || !close(r.y, 0)) {
  fail(`fit rule changed: ${JSON.stringify(r)}`);
}
if (fittedImageRect(720, 360, 0, 10) !== null) fail("zero-size image must not fit");
console.log("ok");
