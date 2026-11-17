/**
 * MCK-473: poll-question boards survive live-sync paints.
 *
 * Group and individual boards, on the teacher card and the student phone,
 * used to be built again every time the question list was replaced.
 * Several paints with the same ink must keep the same canvases and must
 * not draw them again. An opened teacher board stays open.
 */

class HTMLElement {}
class HTMLCanvasElement extends HTMLElement {}
class HTMLButtonElement extends HTMLElement {}
class HTMLTextAreaElement extends HTMLElement {}
class HTMLSelectElement extends HTMLElement {}
class HTMLOptionElement extends HTMLElement {}
class HTMLInputElement extends HTMLElement {}

globalThis.HTMLElement = HTMLElement;
globalThis.HTMLCanvasElement = HTMLCanvasElement;
globalThis.HTMLButtonElement = HTMLButtonElement;
globalThis.HTMLTextAreaElement = HTMLTextAreaElement;
globalThis.HTMLSelectElement = HTMLSelectElement;
globalThis.HTMLOptionElement = HTMLOptionElement;
globalThis.HTMLInputElement = HTMLInputElement;
globalThis.Element = class Element {};
globalThis.Node = class Node {};

const PROTOTYPES = {
  canvas: HTMLCanvasElement,
  button: HTMLButtonElement,
  textarea: HTMLTextAreaElement,
  select: HTMLSelectElement,
  option: HTMLOptionElement,
  input: HTMLInputElement,
};

let draws = 0;
let bitmapWrites = 0;
let armWrites = false;
let canvasCount = 0;

/**
 * 2d context that counts a full paint and ignores the rest.
 * @returns {CanvasRenderingContext2D}
 */
function makeContext() {
  return new Proxy(
    {},
    {
      get(_target, prop) {
        if (prop === "clearRect" || prop === "fillRect" || prop === "stroke") {
          return () => {
            draws += 1;
          };
        }
        if (prop === "measureText") return () => ({ width: 8 });
        return () => {};
      },
      set() {
        return true;
      },
    }
  );
}

/**
 * @param {string} prop Dataset key.
 * @returns {string}
 */
function dataAttr(prop) {
  return `data-${String(prop).replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}`;
}

/**
 * One element or text node in the tiny document.
 * @param {string} tag
 * @param {Function} proto
 */
function createNode(tag, proto) {
  const listeners = {};
  const attrs = {};
  const node = {
    nodeType: 1,
    tagName: String(tag || "div").toUpperCase(),
    id: "",
    className: "",
    hidden: false,
    disabled: false,
    readOnly: false,
    value: "",
    type: "",
    rows: 0,
    maxLength: 0,
    wrap: "",
    style: {},
    children: [],
    parentElement: null,
    _width: tag === "canvas" ? 300 : 0,
    _height: tag === "canvas" ? 150 : 0,
    get width() {
      return this._width;
    },
    set width(value) {
      this._width = Number(value) || 0;
      if (armWrites) bitmapWrites += 1;
    },
    get height() {
      return this._height;
    },
    set height(value) {
      this._height = Number(value) || 0;
      if (armWrites) bitmapWrites += 1;
    },
    get clientWidth() {
      return 960;
    },
    get clientHeight() {
      return 480;
    },
    get offsetLeft() {
      return 0;
    },
    get offsetTop() {
      return 0;
    },
    get childElementCount() {
      return this.children.filter((child) => child.nodeType !== 3).length;
    },
    get firstChild() {
      return this.children[0] || null;
    },
    get nextSibling() {
      if (!this.parentElement) return null;
      const kids = this.parentElement.children;
      const index = kids.indexOf(this);
      return index >= 0 ? kids[index + 1] || null : null;
    },
    get isConnected() {
      let current = this;
      while (current) {
        if (current === document.body || current === document.documentElement) return true;
        current = current.parentElement;
      }
      return false;
    },
    get textContent() {
      if (this.nodeType === 3) return this.nodeValue || "";
      return this.children.map((child) => child.textContent || "").join("");
    },
    set textContent(value) {
      for (const child of [...this.children]) this.removeChild(child);
      const text = String(value ?? "");
      if (text) this.appendChild(createText(text, this));
    },
    classList: null,
    dataset: null,
    setAttribute(name, value) {
      const key = String(name);
      attrs[key] = String(value);
      if (key === "class") this.className = String(value);
      if (key === "id") this.id = String(value);
      if (key === "hidden") this.hidden = true;
      if (key === "width") this._width = Number(value) || 0;
      if (key === "height") this._height = Number(value) || 0;
      if (key === "type") this.type = String(value);
      if (key === "disabled") this.disabled = true;
    },
    getAttribute(name) {
      const key = String(name);
      if (key === "class") return this.className;
      if (key === "id") return this.id;
      return Object.prototype.hasOwnProperty.call(attrs, key) ? attrs[key] : null;
    },
    removeAttribute(name) {
      delete attrs[String(name)];
    },
    addEventListener(type, fn) {
      (listeners[type] ||= []).push(fn);
    },
    removeEventListener() {},
    dispatchEvent(event) {
      for (const fn of listeners[event.type] || []) fn(event);
      return true;
    },
    click() {
      this.dispatchEvent({
        type: "click",
        target: this,
        preventDefault() {},
        stopPropagation() {},
      });
    },
    append(...kids) {
      for (const kid of kids) this.appendChild(kid);
    },
    appendChild(child) {
      if (child.parentElement) child.parentElement.removeChild(child);
      child.parentElement = this;
      this.children.push(child);
      return child;
    },
    removeChild(child) {
      const index = this.children.indexOf(child);
      if (index >= 0) this.children.splice(index, 1);
      if (child.parentElement === this) child.parentElement = null;
      return child;
    },
    insertBefore(child, ref) {
      if (child.parentElement) child.parentElement.removeChild(child);
      const index = ref ? this.children.indexOf(ref) : -1;
      if (index < 0) this.children.push(child);
      else this.children.splice(index, 0, child);
      child.parentElement = this;
      return child;
    },
    insertAdjacentElement(where, child) {
      if (where === "afterend" && this.parentElement) {
        this.parentElement.insertBefore(child, this.nextSibling);
      } else {
        this.appendChild(child);
      }
      return child;
    },
    replaceChildren(...kids) {
      const next = kids.flat();
      for (const child of [...this.children]) {
        if (!next.includes(child) && child.parentElement === this) child.parentElement = null;
      }
      this.children = [];
      for (const child of next) this.appendChild(child);
    },
    remove() {
      if (this.parentElement) this.parentElement.removeChild(this);
    },
    contains(other) {
      let current = other;
      while (current) {
        if (current === this) return true;
        current = current.parentElement;
      }
      return false;
    },
    closest(selector) {
      let current = this;
      while (current) {
        if (current.nodeType !== 3 && matches(current, selector)) return current;
        current = current.parentElement;
      }
      return null;
    },
    querySelector(selector) {
      return this.querySelectorAll(selector)[0] || null;
    },
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
    getBoundingClientRect() {
      if (this.tagName === "CANVAS") {
        return { width: 720, height: 360, left: 0, top: 0, right: 720, bottom: 360 };
      }
      return { width: 960, height: 480, left: 0, top: 80, right: 960, bottom: 560 };
    },
    getContext() {
      return makeContext();
    },
    focus() {},
    blur() {},
    select() {},
    setPointerCapture() {},
  };
  node.classList = {
    add(...names) {
      const set = new Set(node.className.split(/\s+/).filter(Boolean));
      for (const name of names) set.add(name);
      node.className = [...set].join(" ");
    },
    remove(...names) {
      const set = new Set(node.className.split(/\s+/).filter(Boolean));
      for (const name of names) set.delete(name);
      node.className = [...set].join(" ");
    },
    toggle(name, force) {
      const set = new Set(node.className.split(/\s+/).filter(Boolean));
      const on = force === undefined ? !set.has(name) : Boolean(force);
      if (on) set.add(name);
      else set.delete(name);
      node.className = [...set].join(" ");
      return on;
    },
    contains(name) {
      return node.className.split(/\s+/).includes(name);
    },
  };
  node.dataset = new Proxy(
    {},
    {
      get(_target, prop) {
        if (typeof prop !== "string") return undefined;
        const value = node.getAttribute(dataAttr(prop));
        return value == null ? undefined : value;
      },
      set(_target, prop, value) {
        node.setAttribute(dataAttr(prop), value == null ? "" : String(value));
        return true;
      },
    }
  );
  Object.defineProperty(node, "innerHTML", {
    get() {
      return "";
    },
    set(html) {
      for (const child of [...node.children]) node.removeChild(child);
      parseHTML(node, String(html || ""));
    },
  });
  if (proto) Object.setPrototypeOf(node, proto.prototype);
  if (tag === "canvas") canvasCount += 1;
  return node;
}

/**
 * @param {string} value
 * @param {ReturnType<typeof createNode>} parent
 */
function createText(value, parent) {
  return {
    nodeType: 3,
    nodeValue: value,
    textContent: value,
    parentElement: parent,
    children: [],
  };
}

/**
 * @param {ReturnType<typeof createNode>} el
 * @param {string} compound
 * @returns {boolean}
 */
function matchCompound(el, compound) {
  const re = /\[([^\]]+)\]|([#.])([A-Za-z0-9_-]+)|([A-Za-z0-9_-]+)/g;
  let saw = false;
  let match;
  while ((match = re.exec(compound))) {
    saw = true;
    if (match[1]) {
      const raw = match[1];
      const eq = raw.indexOf("=");
      if (eq < 0) {
        if (el.getAttribute(raw.trim()) == null) return false;
      } else {
        const name = raw.slice(0, eq).trim();
        let val = raw.slice(eq + 1).trim();
        if (
          (val.startsWith('"') && val.endsWith('"')) ||
          (val.startsWith("'") && val.endsWith("'"))
        ) {
          val = val.slice(1, -1);
        }
        if (el.getAttribute(name) !== val) return false;
      }
    } else if (match[2] === "#") {
      if (el.id !== match[3]) return false;
    } else if (match[2] === ".") {
      if (!el.classList.contains(match[3])) return false;
    } else if (String(el.tagName).toLowerCase() !== String(match[4]).toLowerCase()) {
      return false;
    }
  }
  return saw;
}

/**
 * @param {ReturnType<typeof createNode>} el
 * @param {string} selector
 * @returns {boolean}
 */
function matches(el, selector) {
  return String(selector)
    .split(",")
    .some((alternative) => {
      const parts = alternative.trim().split(/\s+/).filter(Boolean);
      if (!parts.length || !matchCompound(el, parts[parts.length - 1])) return false;
      let node = el.parentElement;
      for (let index = parts.length - 2; index >= 0; index -= 1) {
        while (node && !matchCompound(node, parts[index])) node = node.parentElement;
        if (!node) return false;
        node = node.parentElement;
      }
      return true;
    });
}

/**
 * Fill one parent from an HTML snippet. Enough for the gallery markup.
 * @param {ReturnType<typeof createNode>} parent
 * @param {string} html
 */
function parseHTML(parent, html) {
  const stack = [parent];
  const re = /<!--[\s\S]*?-->|<\/([A-Za-z0-9-]+)>|<([A-Za-z0-9-]+)([^>]*)>|([^<]+)/g;
  let match;
  while ((match = re.exec(html))) {
    if (match[0].startsWith("<!--")) continue;
    if (match[1]) {
      const tag = match[1].toLowerCase();
      if (stack.length > 1 && stack[stack.length - 1].tagName.toLowerCase() === tag) stack.pop();
      continue;
    }
    if (match[2]) {
      let raw = match[3] || "";
      let self = false;
      if (raw.trim().endsWith("/")) {
        self = true;
        raw = raw.trim().slice(0, -1);
      }
      const el = document.createElement(match[2].toLowerCase());
      const attr = /([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+)))?/g;
      let found;
      while ((found = attr.exec(raw))) {
        el.setAttribute(found[1], found[2] ?? found[3] ?? found[4] ?? "");
      }
      stack[stack.length - 1].appendChild(el);
      if (!self) stack.push(el);
      continue;
    }
    const text = match[4].replace(/\s+/g, " ");
    if (text.trim()) stack[stack.length - 1].appendChild(createText(text, stack[stack.length - 1]));
  }
}

const body = createNode("body", HTMLElement);
const documentElement = createNode("html", HTMLElement);
documentElement.appendChild(body);

globalThis.document = {
  body,
  documentElement,
  createElement(tag) {
    const name = String(tag || "div").toLowerCase();
    return createNode(name, PROTOTYPES[name] || HTMLElement);
  },
  createTextNode(text) {
    return createText(String(text), null);
  },
  getElementById(id) {
    return body.querySelector(`#${id}`);
  },
  querySelector(selector) {
    return body.querySelector(selector);
  },
  querySelectorAll(selector) {
    return body.querySelectorAll(selector);
  },
  addEventListener() {},
  removeEventListener() {},
  activeElement: null,
};

globalThis.window = globalThis;
globalThis.CustomEvent = class CustomEvent {
  /**
   * @param {string} type
   * @param {{detail?: any}} [init]
   */
  constructor(type, init) {
    this.type = type;
    this.detail = init && init.detail;
  }
};
window.addEventListener = () => {};
window.removeEventListener = () => {};
window.dispatchEvent = () => true;
window.devicePixelRatio = 1;
window.innerHeight = 800;
window.requestAnimationFrame = (fn) => setTimeout(() => fn(Date.now()), 0);
window.cancelAnimationFrame = (id) => clearTimeout(id);
window.getComputedStyle = () => ({ columnGap: "10px" });
globalThis.getComputedStyle = window.getComputedStyle;
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  disconnect() {}
};
globalThis.MutationObserver = class MutationObserver {
  observe() {}
  disconnect() {}
};

const realSetTimeout = setTimeout;
const realSetInterval = setInterval;
window.setTimeout = (fn, ms, ...args) => {
  const timer = realSetTimeout(fn, ms, ...args);
  timer.unref?.();
  return timer;
};
window.clearTimeout = clearTimeout;
window.setInterval = (fn, ms, ...args) => {
  const timer = realSetInterval(fn, ms, ...args);
  timer.unref?.();
  return timer;
};
window.clearInterval = clearInterval;

const INK = {
  strokes: [{ id: "s1", points: [[0.2, 0.3], [0.5, 0.6]], color: "#2563eb" }],
  texts: [],
};

/**
 * @param {string} boardKey
 * @param {number} seq
 */
function tile(boardKey, seq) {
  return {
    board_key: boardKey,
    name: "",
    away: false,
    status: "Drawing",
    drawing: true,
    board_seq: seq,
    snapshot: true,
    ops: [],
    canvas_view: INK,
    text: "",
    feedback: "",
  };
}

/**
 * @param {number} status
 * @param {any} body
 */
function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

globalThis.fetch = async (url) => {
  const path = String(url);
  if (path.includes("/questions/1/boards?") || path.endsWith("/questions/1/boards")) {
    return jsonResponse(200, {
      ok: true,
      visible: true,
      ended: false,
      run_key: "q-run",
      answer_with: "board",
      context: "",
      summary: "",
      projected_board: "",
      filters: [{ id: "all", label: "All 1" }],
      tiles: [tile("solo:1", 4)],
    });
  }
  if (path.includes("/questions/2/boards?") || path.endsWith("/questions/2/boards")) {
    return jsonResponse(200, {
      ok: true,
      visible: true,
      ended: false,
      run_key: "q-run",
      answer_with: "board",
      context: "",
      summary: "",
      projected_board: "",
      filters: [{ id: "all", label: "All 1" }],
      tiles: [tile("team:0", 4)],
    });
  }
  if (path.includes("/questions/1/boards/")) {
    return jsonResponse(200, {
      ok: true,
      board_key: "solo:1",
      name: "",
      run_key: "q-run",
      board_seq: 4,
      snapshot: true,
      ops: [],
      canvas_view: INK,
      legend: [{ id: "1", color: "#2563eb", slot: 1 }],
    });
  }
  if (path.includes("/questions/2/boards/")) {
    return jsonResponse(200, {
      ok: true,
      board_key: "team:0",
      name: "",
      run_key: "q-run",
      board_seq: 4,
      snapshot: true,
      ops: [],
      canvas_view: INK,
      legend: [{ id: "1", color: "#2563eb", slot: 1 }],
    });
  }
  if (path.includes("/api/student/question-board/")) {
    const team = path.includes("/question-board/2");
    return jsonResponse(200, {
      ok: true,
      run_key: "q-run",
      board_key: team ? "team:0" : "solo:1",
      board_seq: 4,
      snapshot: true,
      canvas_view: INK,
      read_only: false,
      editing_locked: false,
      group: team,
      viewer_id: "1",
      text: "",
      status_line: "",
      feedback: "",
      submit_ready: true,
      formative: { members: [{ id: "1", color: "#2563eb", slot: 1 }] },
    });
  }
  return jsonResponse(404, {});
};

/**
 * Let the zero-delay board reads finish. Later polls stay on their timer.
 */
async function flush() {
  for (let i = 0; i < 30; i += 1) {
    await new Promise((resolve) => realSetTimeout(resolve, 0));
  }
}

/**
 * Replace the question hosts the way a live-sync paint does.
 * @param {ReturnType<typeof createNode>} teacherHost
 * @param {ReturnType<typeof createNode>} studentHost
 */
function repaint(teacherHost, studentHost) {
  teacherHost.innerHTML = "";
  studentHost.innerHTML = "";
  for (const [id] of [
    ["1", "0"],
    ["2", "1"],
  ]) {
    const dash = document.createElement("div");
    dash.className = "qboard-dash";
    dash.setAttribute("data-qboard-dash", id);
    teacherHost.appendChild(dash);
    const slot = document.createElement("div");
    slot.className = "qboard-slot";
    slot.setAttribute("data-qboard-prompt", id);
    slot.setAttribute("data-qboard-mode", "board");
    slot.setAttribute("data-qboard-team", id === "2" ? "1" : "0");
    studentHost.appendChild(slot);
  }
}

/**
 * @param {ReturnType<typeof createNode>} host
 * @param {string} key
 * @returns {HTMLCanvasElement | null}
 */
function tileCanvas(host, key) {
  for (const button of host.querySelectorAll(".wb-live-tile")) {
    if (button.dataset.boardKey === key) return button.querySelector("canvas");
  }
  return null;
}

const { mountTeacherQuestionBoards, mountStudentQuestionBoards } = await import(
  "./question_board_ui.js"
);

const teacherHost = document.createElement("div");
const studentHost = document.createElement("div");
body.appendChild(teacherHost);
body.appendChild(studentHost);

try {
  repaint(teacherHost, studentHost);
  mountTeacherQuestionBoards("9");
  mountStudentQuestionBoards();
  await flush();

  const soloTile = tileCanvas(teacherHost, "solo:1");
  const teamTile = tileCanvas(teacherHost, "team:0");
  const soloPanel = teacherHost.querySelector('[data-qboard-dash="1"] [data-wb-part="panel-canvas"]');
  const teamPanel = teacherHost.querySelector('[data-qboard-dash="2"] [data-wb-part="panel-canvas"]');
  const soloPhone = studentHost.querySelector('[data-qboard-prompt="1"] canvas.student-canvas');
  const teamPhone = studentHost.querySelector('[data-qboard-prompt="2"] canvas.student-canvas');
  for (const canvas of [soloTile, teamTile, soloPanel, teamPanel, soloPhone, teamPhone]) {
    if (!(canvas instanceof HTMLCanvasElement)) throw new Error("missing canvas");
  }
  soloTile.closest(".wb-live-tile").click();
  teamTile.closest(".wb-live-tile").click();
  await flush();

  const drawsAtRest = draws;
  const writesAtRest = bitmapWrites;
  const canvasesAtRest = canvasCount;
  armWrites = true;

  for (let poll = 0; poll < 3; poll += 1) {
    repaint(teacherHost, studentHost);
    mountTeacherQuestionBoards("9");
    mountStudentQuestionBoards();
    await flush();
  }

  const soloPanelAfter = teacherHost.querySelector('[data-qboard-dash="1"] [data-wb-part="panel"]');
  const teamPanelAfter = teacherHost.querySelector('[data-qboard-dash="2"] [data-wb-part="panel"]');
  const report = {
    sameSoloTile: tileCanvas(teacherHost, "solo:1") === soloTile,
    sameTeamTile: tileCanvas(teacherHost, "team:0") === teamTile,
    sameSoloPanel: teacherHost.querySelector('[data-qboard-dash="1"] [data-wb-part="panel-canvas"]') === soloPanel,
    sameTeamPanel: teacherHost.querySelector('[data-qboard-dash="2"] [data-wb-part="panel-canvas"]') === teamPanel,
    sameSoloPhone: studentHost.querySelector('[data-qboard-prompt="1"] canvas.student-canvas') === soloPhone,
    sameTeamPhone: studentHost.querySelector('[data-qboard-prompt="2"] canvas.student-canvas') === teamPhone,
    soloOpen: Boolean(soloPanelAfter) && soloPanelAfter.hidden === false,
    teamOpen: Boolean(teamPanelAfter) && teamPanelAfter.hidden === false,
    drawsHeld: draws === drawsAtRest,
    writesHeld: bitmapWrites === writesAtRest,
    canvasesHeld: canvasCount === canvasesAtRest,
    draws: drawsAtRest,
    drawsAfter: draws,
    canvases: canvasesAtRest,
    canvasesAfter: canvasCount,
  };
  console.log(JSON.stringify(report));
  const failed = Object.entries(report).filter(([, ok]) => ok === false);
  process.exit(failed.length ? 1 : 0);
} catch (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
}
