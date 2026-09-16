/**
 * Node assertion: closeLiveSessionOverlay closes the stored opener Window.
 */
const closes = [];

/**
 * Minimal popup stub for reserve/open/close.
 * @param {string} label
 * @returns {{closed: boolean, location: {href: string}, focus: Function, close: Function}}
 */
function fakePopup(label) {
  return {
    closed: false,
    location: { href: "about:blank" },
    focus() {},
    close() {
      this.closed = true;
      closes.push(label);
    },
  };
}

const stored = fakePopup("stored");
let namedOpens = 0;
globalThis.screen = { availWidth: 1280, availHeight: 900 };
globalThis.window = {
  open(url) {
    if (url === "" || url == null) {
      namedOpens += 1;
      return fakePopup("named");
    }
    return stored;
  },
};

const { reserveLiveSessionOverlay, openLiveSessionOverlay, closeLiveSessionOverlay } =
  await import("./static/common.js");

const reserved = reserveLiveSessionOverlay();
if (reserved !== stored) {
  console.error("reserve did not store the opener Window");
  process.exit(1);
}
const opened = openLiveSessionOverlay(7, reserved, { classId: 3 });
if (opened !== stored) {
  console.error("open did not keep the stored Window");
  process.exit(1);
}
closeLiveSessionOverlay();
if (!stored.closed || closes[0] !== "stored") {
  console.error("close did not close the stored Window first", closes);
  process.exit(1);
}
if (namedOpens !== 0) {
  console.error("close used window.open('', name) despite a stored handle");
  process.exit(1);
}
console.log("ok");
