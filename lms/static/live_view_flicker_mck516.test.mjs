/**
 * MCK-516: MC bars ease from the width already painted, and an unchanged
 * whiteboard thumbnail is not redrawn or replaced.
 */
import { inkContentStamp, syncWhiteboardGrid } from "./formative_live_board.js";
import { mcResultsGraphHtml, syncMcResultsBars } from "./mc_results_graph.js";

const result = {};

/**
 * @param {string} name
 * @param {boolean} ok
 */
function check(name, ok) {
  result[name] = Boolean(ok);
}

class HTMLElement {}
class HTMLButtonElement extends HTMLElement {}
class HTMLCanvasElement extends HTMLElement {}

globalThis.HTMLElement = HTMLElement;
globalThis.HTMLButtonElement = HTMLButtonElement;
globalThis.HTMLCanvasElement = HTMLCanvasElement;

/** @type {WeakMap<object, string[]>} */
const widthWrites = new WeakMap();
let recordWidths = false;

/**
 * One element. Width writes are recorded only after the first paint.
 * @param {string} tag
 * @returns {HTMLElement}
 */
function createNode(tag) {
  const attrs = {};
  const node = {
    nodeType: 1,
    tagName: String(tag || "div").toUpperCase(),
    className: "",
    hidden: false,
    type: "",
    children: [],
    parentElement: null,
    _width: 0,
    _height: 0,
    /**
     * @returns {number}
     */
    get width() {
      return this._width;
    },
    /**
     * @param {number} value
     */
    set width(value) {
      this._width = Number(value) || 0;
    },
    /**
     * @returns {number}
     */
    get height() {
      return this._height;
    },
    /**
     * @param {number} value
     */
    set height(value) {
      this._height = Number(value) || 0;
    },
    /**
     * @returns {Node|null}
     */
    get firstChild() {
      return this.children[0] || null;
    },
    /**
     * @returns {Node|null}
     */
    get nextSibling() {
      if (!this.parentElement) return null;
      const kids = this.parentElement.children;
      const index = kids.indexOf(this);
      return index >= 0 ? kids[index + 1] || null : null;
    },
    /**
     * @returns {string}
     */
    get textContent() {
      if (this.nodeType === 3) return this.nodeValue || "";
      return this.children.map((child) => child.textContent || "").join("");
    },
    /**
     * @param {string} value
     */
    set textContent(value) {
      for (const child of [...this.children]) this.removeChild(child);
      const text = String(value ?? "");
      if (text) this.appendChild(createText(text));
    },
    classList: null,
    dataset: null,
    style: null,
    /**
     * @param {string} name
     * @param {string} value
     */
    setAttribute(name, value) {
      const key = String(name);
      attrs[key] = String(value);
      if (key === "class") this.className = String(value);
      if (key === "style") {
        const match = /width:\s*([^;]+)/i.exec(String(value));
        if (match) this.style.width = match[1].trim();
      }
    },
    /**
     * @param {string} name
     * @returns {string|null}
     */
    getAttribute(name) {
      const key = String(name);
      if (key === "class") return this.className;
      return Object.prototype.hasOwnProperty.call(attrs, key) ? attrs[key] : null;
    },
    /**
     * @param {...Node} kids
     */
    append(...kids) {
      for (const kid of kids) this.appendChild(kid);
    },
    /**
     * @param {Node} child
     * @returns {Node}
     */
    appendChild(child) {
      if (child.parentElement) child.parentElement.removeChild(child);
      child.parentElement = this;
      this.children.push(child);
      return child;
    },
    /**
     * @param {Node} child
     * @returns {Node}
     */
    removeChild(child) {
      const index = this.children.indexOf(child);
      if (index >= 0) this.children.splice(index, 1);
      if (child.parentElement === this) child.parentElement = null;
      return child;
    },
    /**
     * @param {Node} child
     * @param {Node|null} ref
     * @returns {Node}
     */
    insertBefore(child, ref) {
      if (child.parentElement) child.parentElement.removeChild(child);
      const index = ref ? this.children.indexOf(ref) : -1;
      if (index < 0) this.children.push(child);
      else this.children.splice(index, 0, child);
      child.parentElement = this;
      return child;
    },
    remove() {
      if (this.parentElement) this.parentElement.removeChild(this);
    },
    /**
     * @param {string} selector
     * @returns {HTMLElement|null}
     */
    querySelector(selector) {
      return this.querySelectorAll(selector)[0] || null;
    },
    /**
     * @param {string} selector
     * @returns {HTMLElement[]}
     */
    querySelectorAll(selector) {
      const found = [];
      const walk = (host) => {
        for (const child of host.children || []) {
          if (child.nodeType === 3) continue;
          if (matches(child, selector)) found.push(child);
          walk(child);
        }
      };
      walk(this);
      return found;
    },
    addEventListener() {},
  };
  const writes = [];
  widthWrites.set(node, writes);
  let width = "";
  node.style = {};
  Object.defineProperty(node.style, "width", {
    /**
     * @returns {string}
     */
    get() {
      return width;
    },
    /**
     * @param {string} value
     */
    set(value) {
      const next = String(value);
      if (recordWidths) writes.push(next);
      width = next;
    },
  });
  node.classList = {
    /**
     * @param {string} name
     * @param {boolean} [force]
     * @returns {boolean}
     */
    toggle(name, force) {
      const set = new Set(node.className.split(/\s+/).filter(Boolean));
      const on = force === undefined ? !set.has(name) : Boolean(force);
      if (on) set.add(name);
      else set.delete(name);
      node.className = [...set].join(" ");
      return on;
    },
    /**
     * @param {string} name
     * @returns {boolean}
     */
    contains(name) {
      return node.className.split(/\s+/).includes(name);
    },
  };
  node.dataset = new Proxy(
    {},
    {
      /**
       * @param {object} _target
       * @param {string|symbol} prop
       * @returns {string|undefined}
       */
      get(_target, prop) {
        if (typeof prop !== "string") return undefined;
        const value = node.getAttribute(dataAttr(prop));
        return value == null ? undefined : value;
      },
      /**
       * @param {object} _target
       * @param {string|symbol} prop
       * @param {string} value
       * @returns {boolean}
       */
      set(_target, prop, value) {
        if (typeof prop !== "string") return false;
        node.setAttribute(dataAttr(prop), value == null ? "" : String(value));
        return true;
      },
    }
  );
  const proto =
    tag === "button" ? HTMLButtonElement : tag === "canvas" ? HTMLCanvasElement : HTMLElement;
  Object.setPrototypeOf(node, proto.prototype);
  return node;
}

/**
 * @param {string} prop
 * @returns {string}
 */
function dataAttr(prop) {
  return `data-${String(prop).replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}`;
}

/**
 * @param {string} value
 * @returns {object}
 */
function createText(value) {
  return {
    nodeType: 3,
    nodeValue: value,
    textContent: value,
    parentElement: null,
    children: [],
  };
}

/**
 * @param {ReturnType<typeof createNode>} el
 * @param {string} selector
 * @returns {boolean}
 */
function matches(el, selector) {
  const raw = String(selector);
  if (!raw.includes(".")) return String(el.tagName).toLowerCase() === raw.toLowerCase();
  const classes = raw
    .split(".")
    .map((part) => part.trim())
    .filter(Boolean);
  if (!classes.length) return false;
  const own = el.className.split(/\s+/);
  return classes.every((name) => own.includes(name));
}

/**
 * Build a graph from the real markup.
 * @param {HTMLElement} parent
 * @param {string} html
 */
function parseHTML(parent, html) {
  const stack = [parent];
  const re = /<\/([A-Za-z0-9-]+)>|<([A-Za-z0-9-]+)([^>]*?)(\/?)>|([^<]+)/g;
  let match;
  while ((match = re.exec(html))) {
    if (match[1]) {
      const tag = match[1].toLowerCase();
      if (stack.length > 1 && stack[stack.length - 1].tagName.toLowerCase() === tag) stack.pop();
      continue;
    }
    if (match[2]) {
      const el = createNode(match[2].toLowerCase());
      const raw = match[3] || "";
      const attr = /([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+)))?/g;
      let found;
      while ((found = attr.exec(raw))) {
        el.setAttribute(found[1], found[2] ?? found[3] ?? found[4] ?? "");
      }
      stack[stack.length - 1].appendChild(el);
      if (!match[4]) stack.push(el);
      continue;
    }
    const text = match[5].replace(/\s+/g, " ");
    if (text.trim()) stack[stack.length - 1].appendChild(createText(text));
  }
}

globalThis.document = {
  /**
   * @param {string} tag
   * @returns {HTMLElement}
   */
  createElement(tag) {
    return createNode(String(tag || "div").toLowerCase());
  },
  getElementById() {
    return null;
  },
  body: createNode("body"),
};

const tally = {
  kind: "mc",
  choices: [
    { id: "A", label: "north", count: 0, correct: false },
    { id: "B", label: "east", count: 2, correct: true },
    { id: "C", label: "south", count: 2, correct: false },
  ],
};
const opts = { order: "default", showKey: false, markKey: true, controls: true, itemKey: "item:9" };
const host = createNode("div");
parseHTML(host, mcResultsGraphHtml(tally, opts));
const graph = host.querySelector(".mc-results-graph");
const rows = [...graph.querySelectorAll(".mc-results-row")];
const fills = rows.map((row) => row.querySelector(".mc-results-fill"));

/**
 * Widths written on one fill since recording started.
 * @param {HTMLElement} fill
 * @returns {string[]}
 */
function writes(fill) {
  return widthWrites.get(fill) || [];
}

recordWidths = true;
const same = syncMcResultsBars(graph, tally, opts);
check("samePollKeepsGraph", graph === host.querySelector(".mc-results-graph"));
check(
  "samePollKeepsRows",
  rows.every((row, index) => graph.querySelectorAll(".mc-results-row")[index] === row)
);
check("samePollMatches", same.matched === true && same.updated.length === 0);
check(
  "samePollWritesNothing",
  fills.every((fill) => writes(fill).length === 0)
);

const changed = {
  kind: "mc",
  choices: [
    { id: "A", label: "north", count: 0, correct: false },
    { id: "B", label: "east", count: 2, correct: true },
    { id: "C", label: "south", count: 6, correct: true },
  ],
};
const moved = syncMcResultsBars(graph, changed, opts);
check(
  "changedPollKeepsRows",
  rows.every((row, index) => graph.querySelectorAll(".mc-results-row")[index] === row)
);
check("changedPollKeepsFills", fills.every((fill, index) => rows[index].querySelector(".mc-results-fill") === fill));
check("unchangedZeroBarNotWritten", writes(fills[0]).length === 0 && moved.skipped.includes("A"));
check("changedBarWritesNewWidth", writes(fills[2]).join(",") === "75%");
check("percentShiftWritesNewWidth", writes(fills[1]).join(",") === "25%");
check(
  "widthsNeverRestartAtZero",
  fills.every((fill) => writes(fill).every((value) => value !== "0%" && value !== "0"))
);
check("tableNotRebuilt", moved.matched === true && moved.updated.includes("C") && !moved.updated.includes("A"));

const again = syncMcResultsBars(graph, changed, opts);
const writesBeforeAgain = fills.map((fill) => writes(fill).length);
check("repeatPollWritesNothing", again.updated.length === 0);
check(
  "repeatPollStillQuiet",
  fills.every((fill, index) => writes(fill).length === writesBeforeAgain[index])
);

const strokesA = { s: { id: "s", color: "#2563eb", points: [[0.1, 0.2]] } };
const strokesB = { s: { id: "s", color: "#111111", points: [[0.2, 0.2], [0.3, 0.4]] } };
const strokesBSame = {
  s: { id: "s", color: "#111111", points: [[0.2, 0.2], [0.3, 0.4]] },
};
check("sameInkSameStamp", inkContentStamp(strokesB, {}) === inkContentStamp(strokesBSame, {}));
check("changedInkDifferentStamp", inkContentStamp(strokesA, {}) !== inkContentStamp(strokesB, {}));

/**
 * @param {Record<string, any>} strokes
 * @param {string} key
 * @param {string} name
 */
function tile(strokes, key, name) {
  return {
    key,
    name,
    away: false,
    status: "Drawing",
    text: "",
    open: false,
    stamp: inkContentStamp(strokes, {}),
    strokes,
    texts: {},
  };
}

const grid = createNode("div");
/** @type {HTMLCanvasElement[]} */
const painted = [];
syncWhiteboardGrid(grid, [tile(strokesA, "solo:1", "One"), tile(strokesB, "solo:2", "Two")], {
  /**
   * @param {HTMLCanvasElement} canvas
   */
  paint(canvas) {
    painted.push(canvas);
  },
});

/**
 * @param {string} key
 * @returns {HTMLCanvasElement|null}
 */
function canvasFor(key) {
  for (const button of grid.querySelectorAll(".wb-live-tile")) {
    if (button.dataset.boardKey === key) return button.querySelector("canvas");
  }
  return null;
}

const firstA = canvasFor("solo:1");
const firstB = canvasFor("solo:2");
const buttonA = grid.querySelectorAll(".wb-live-tile").find((button) => button.dataset.boardKey === "solo:1");
const buttonB = grid.querySelectorAll(".wb-live-tile").find((button) => button.dataset.boardKey === "solo:2");
painted.length = 0;
syncWhiteboardGrid(
  grid,
  [tile(strokesA, "solo:1", "One"), tile(strokesBSame, "solo:2", "Two")],
  {
    /**
     * @param {HTMLCanvasElement} canvas
     */
    paint(canvas) {
      painted.push(canvas);
    },
  }
);
check("unchangedThumbnailsNotRepainted", painted.length === 0);
check("unchangedThumbnailANotReplaced", canvasFor("solo:1") === firstA && canvasFor("solo:2") === firstB);
check(
  "unchangedButtonsNotReplaced",
  grid.querySelectorAll(".wb-live-tile").find((button) => button.dataset.boardKey === "solo:1") === buttonA &&
    grid.querySelectorAll(".wb-live-tile").find((button) => button.dataset.boardKey === "solo:2") === buttonB
);

const strokesBNext = {
  s: { id: "s", color: "#111111", points: [[0.2, 0.2], [0.3, 0.4], [0.6, 0.7]] },
};
syncWhiteboardGrid(grid, [tile(strokesA, "solo:1", "One"), tile(strokesBNext, "solo:2", "Two")], {
  /**
   * @param {HTMLCanvasElement} canvas
   */
  paint(canvas) {
    painted.push(canvas);
  },
});
check("onlyChangedBoardRepainted", painted.length === 1 && painted[0] === firstB);
check("unchangedBoardNotRepainted", !painted.includes(firstA));
check("changedCanvasNotReplaced", canvasFor("solo:2") === firstB && canvasFor("solo:1") === firstA);

const failed = Object.entries(result).filter(([, ok]) => !ok).map(([name]) => name);
console.log(JSON.stringify({ ok: failed.length === 0, failed, result }));
if (failed.length) process.exit(1);
