// MCK-519: Show All draws each image under its marks, and rejects unsafe URLs.
import { fittedImageRect } from "./board_background.js";
import { safeBoardImageUrl } from "./board_background.js";
import {
  paintShownBoardCanvas,
  place,
  shownBoardCaption,
  syncShownBoards,
  POLL_BOARD_GRID_ID,
} from "./poll_board_show.js";

function fail(message) {
  console.error(message);
  process.exit(1);
}

const close = (a, b) => Math.abs(a - b) < 1e-6;

for (const bad of [
  "javascript:alert(1)",
  "JAVASCRIPT:alert(1)",
  "data:image/png;base64,AAAA",
  "http://example.test/board.png",
  "HTTP://example.test/board.png",
  "//cdn.example/board.png",
]) {
  if (safeBoardImageUrl(bad) !== "") fail(`kept unsafe url ${bad}`);
}
if (safeBoardImageUrl("/static/bank-graphs/parabola-grid.svg") !== "/static/bank-graphs/parabola-grid.svg") {
  fail("site path was dropped");
}
if (safeBoardImageUrl("https://alc.example/img.png") !== "https://alc.example/img.png") {
  fail("https link was dropped");
}

const calls = [];
const ctx = {
  setTransform() {},
  clearRect() {
    calls.push("clear");
  },
  fillRect() {
    calls.push("fill");
  },
  drawImage() {
    calls.push("image");
  },
  beginPath() {},
  moveTo(x, y) {
    calls.push(["move", x, y]);
  },
  lineTo(x, y) {
    calls.push(["line", x, y]);
  },
  stroke() {
    calls.push("stroke");
  },
  fillText() {},
};
const canvas = {
  width: 0,
  height: 0,
  getContext() {
    return ctx;
  },
};
const stored = { x: 0.25, y: 0.5 };
paintShownBoardCanvas(
  canvas,
  { strokes: [{ points: [[stored.x, stored.y], [0.75, 0.5]], color: "#111" }] },
  { naturalWidth: 400, naturalHeight: 400 }
);
const imageAt = calls.indexOf("image");
const strokeAt = calls.indexOf("stroke");
if (imageAt < 0 || strokeAt < 0 || imageAt > strokeAt) {
  fail(`image was not under the marks: ${JSON.stringify(calls)}`);
}
const moved = calls.find((row) => Array.isArray(row) && row[0] === "move");
const expected = place(stored);
if (!moved || !close(moved[1], expected.x) || !close(moved[2], expected.y)) {
  fail(`mark left the board space: ${JSON.stringify({ moved, expected })}`);
}
const fitted = fittedImageRect(720, 360, 400, 400);
if (!fitted || !close(fitted.w, 360)) fail("grid board stopped using the fit rule");

if (shownBoardCaption({ name: "Row A", member_names: [] }) !== "Row A") {
  fail("solo caption dropped the name");
}
if (shownBoardCaption({ name: "Team", member_names: ["Row A", "Row B"] }) !== "Row A, Row B") {
  fail("team caption dropped the student names");
}

const nodes = {};
function makeEl() {
  const node = {
    className: "",
    hidden: true,
    textContent: "",
    dataset: {},
    children: [],
    attributes: {},
    width: 0,
    height: 0,
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    replaceChildren() {
      this.children = [];
    },
    querySelector(sel) {
      const walk = (current) => {
        if (sel.startsWith(".") && current.className === sel.slice(1)) return current;
        for (const child of current.children || []) {
          const found = walk(child);
          if (found) return found;
        }
        return null;
      };
      return walk(this);
    },
    getContext() {
      return ctx;
    },
  };
  let id = "";
  Object.defineProperty(node, "id", {
    get() {
      return id;
    },
    set(value) {
      if (id && nodes[id] === node) delete nodes[id];
      id = String(value || "");
      if (id) nodes[id] = node;
    },
  });
  return node;
}

globalThis.Image = class {
  set src(_value) {}
};

const doc = {
  head: makeEl(),
  body: makeEl(),
  getElementById(id) {
    return nodes[id] || null;
  },
  createElement() {
    return makeEl();
  },
};

const boards = [
  {
    board_key: "solo:1",
    name: "Row A",
    image_url: "javascript:alert(1)",
    canvas_view: { strokes: [{ points: [[0.2, 0.2], [0.3, 0.3]], color: "#111" }], texts: [] },
  },
  {
    board_key: "solo:2",
    name: "Row B",
    member_names: ["Row B"],
    image_url: "/static/bank-graphs/parabola-grid.svg",
    canvas_view: { strokes: [], texts: [] },
  },
];
if (!syncShownBoards(doc, { class_boards: boards })) fail("grid did not show");
const overlay = doc.getElementById(POLL_BOARD_GRID_ID);
if (!overlay || overlay.hidden) fail("grid overlay stayed hidden");
const captions = [];
const urls = [];
const walk = (node) => {
  if (node.className === "poll-board-name") captions.push(node.textContent);
  if (node.dataset && Object.prototype.hasOwnProperty.call(node.dataset, "imageUrl")) {
    urls.push(node.dataset.imageUrl);
  }
  for (const child of node.children || []) walk(child);
};
walk(overlay);
if (captions.join("|") !== "Row A|Row B") fail(`names missing on the grid: ${captions.join("|")}`);
if (urls.includes("javascript:alert(1)")) fail("javascript url was painted");
if (!urls.includes("") || !urls.includes("/static/bank-graphs/parabola-grid.svg")) {
  fail(`image refs were not filtered: ${urls.join("|")}`);
}
const stripped = boards.map((board) => ({
  board_key: board.board_key,
  poll: true,
  read_only: true,
  image_url: board.image_url,
  canvas_view: board.canvas_view,
}));
if (!syncShownBoards(doc, { class_boards: stripped })) fail("stripped grid did not show");
const blank = [];
const walkBlank = (node) => {
  if (node.className === "poll-board-name") blank.push(node.textContent);
  for (const child of node.children || []) walkBlank(child);
};
walkBlank(doc.getElementById(POLL_BOARD_GRID_ID));
if (blank.length !== 2 || blank.some((label) => label)) {
  fail(`stripped captions were not blank: ${JSON.stringify(blank)}`);
}
if (syncShownBoards(doc, { class_boards: null })) fail("null grid stayed up");
if (!doc.getElementById(POLL_BOARD_GRID_ID).hidden) fail("grid did not dismiss");
console.log("ok");
