/**
 * MCK-499: student image buttons, badge copy, and compact stack layout.
 */
import {
  contentFromPushedQuestionCard,
  liveAnswerControlsHtml,
  liveQuestionHeadHtml,
  mountQuestionLightbox,
  newestOpenQuestionId,
  questionCardLayout,
  questionImageHtmlStudent,
  syncQuestionLightbox,
  zoomBadgeLabel,
} from "./live_question_card.js";

function fail(message) {
  console.error(message);
  process.exit(1);
}

if (zoomBadgeLabel(false) !== "Tap to enlarge") fail("touch badge");
if (zoomBadgeLabel(true) !== "Click to enlarge") fail("desktop badge");

const html = questionImageHtmlStudent({
  image_urls: ["/static/graph-a.png", "/static/graph-b.png"],
  image_alts: ["First graph"],
});
if (!html.includes('class="live-q-image-btn"')) fail("image is not a button");
if (!html.includes('aria-label="Enlarge image 1 of 2"')) fail("enlarge label");
if (!html.includes('alt="First graph"')) fail("bank alt was dropped");
if (!html.includes('alt="Question image 2"')) fail("missing alt fallback");
if (!html.includes("Tap to enlarge")) fail("badge copy");
if (!html.includes('width="4"') || !html.includes('height="3"')) fail("size attributes");
if (html.includes('loading="lazy"')) fail("live question images must load eagerly");
if (!html.includes('loading="eager"') || !html.includes('decoding="async"')) {
  fail("eager async image attributes");
}
if (html.includes("live-question-card-options")) fail("image html picked up options");

const open = { id: 2, status: "active" };
const answered = { id: 1, status: "active", my_response: { response: { value: 4 } } };
const closed = { id: 3, status: "closed" };
if (questionCardLayout(open, 2) !== "is-open-focus") fail("open card is not the focus");
if (questionCardLayout(answered, 2) !== "is-open-extra") {
  fail("answered card must stay open after submit (MCK-509)");
}
if (questionCardLayout(closed, 2) !== "is-compact") fail("closed card did not collapse");
if (questionCardLayout({ id: 4, status: "active" }, 2) !== "is-open-extra") {
  fail("older open card took the focus");
}
if (newestOpenQuestionId([answered, open, closed]) !== 2) fail("newest open id");

/* MCK-529: a tiny document so the lightbox can be opened from painted cards. */

class Element {}
class HTMLElement extends Element {}
class HTMLImageElement extends HTMLElement {}
class HTMLButtonElement extends HTMLElement {}
class HTMLDialogElement extends HTMLElement {}
class HTMLSpanElement extends HTMLElement {}

globalThis.Element = Element;
globalThis.HTMLElement = HTMLElement;
globalThis.HTMLImageElement = HTMLImageElement;
globalThis.HTMLButtonElement = HTMLButtonElement;
globalThis.HTMLDialogElement = HTMLDialogElement;
globalThis.HTMLSpanElement = HTMLSpanElement;

const PROTOTYPES = {
  img: HTMLImageElement,
  button: HTMLButtonElement,
  dialog: HTMLDialogElement,
  span: HTMLSpanElement,
};

/**
 * @param {string} prop Dataset key.
 * @returns {string}
 */
function dataAttr(prop) {
  return `data-${String(prop).replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}`;
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
      const tag = match[2].toLowerCase();
      if (raw.trim().endsWith("/") || ["img", "br", "input", "meta", "link"].includes(tag)) {
        self = true;
        if (raw.trim().endsWith("/")) raw = raw.trim().slice(0, -1);
      }
      const el = document.createElement(tag);
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

/**
 * @param {string} value
 * @param {ReturnType<typeof createNode> | null} parent
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
 * @param {string} tag
 * @param {Function} proto
 */
function createNode(tag, proto) {
  const listeners = { capture: {}, bubble: {} };
  const attrs = {};
  const node = {
    nodeType: 1,
    tagName: String(tag || "div").toUpperCase(),
    id: "",
    className: "",
    open: false,
    parentElement: null,
    get parentNode() {
      return this.parentElement;
    },
    children: [],
    ownerDocument: null,
    get src() {
      return attrs.src || "";
    },
    set src(value) {
      attrs.src = String(value ?? "");
    },
    get alt() {
      return attrs.alt || "";
    },
    set alt(value) {
      attrs.alt = String(value ?? "");
    },
    setAttribute(name, value) {
      const key = String(name);
      attrs[key] = String(value);
      if (key === "class") this.className = String(value);
      if (key === "id") this.id = String(value);
      if (key === "src") this.src = String(value);
      if (key === "alt") this.alt = String(value);
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
    addEventListener(type, fn, options) {
      const capture = options === true || (options && options.capture);
      const bucket = capture ? listeners.capture : listeners.bubble;
      (bucket[type] ||= []).push(fn);
    },
    removeEventListener() {},
    _fire(event, capture) {
      event.currentTarget = this;
      const bucket = capture ? listeners.capture : listeners.bubble;
      for (const fn of bucket[event.type] || []) {
        fn(event);
        if (event._immediate) break;
      }
    },
    dispatchEvent(event) {
      event.target = event.target || this;
      const path = [];
      let current = this;
      while (current) {
        path.push(current);
        current = current.parentElement;
      }
      if (document && !path.includes(document)) path.push(document);
      for (let index = path.length - 1; index >= 0; index -= 1) {
        if (event._stopped) break;
        path[index]._fire(event, true);
      }
      for (let index = 0; index < path.length; index += 1) {
        if (event._stopped) break;
        path[index]._fire(event, false);
      }
      if (event.type === "close" && this.tagName === "DIALOG") {
        this.open = false;
      }
      return !event.defaultPrevented;
    },
    click() {
      this.dispatchEvent(makeEvent("click", this));
    },
    showModal() {
      this.open = true;
    },
    close() {
      this.open = false;
      this.dispatchEvent(makeEvent("close", this, { bubbles: false }));
    },
    focus() {},
    appendChild(child) {
      if (child.parentElement) child.parentElement.removeChild(child);
      child.parentElement = this;
      if (child.ownerDocument == null) child.ownerDocument = this.ownerDocument || globalThis.document;
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
      if (child.ownerDocument == null) child.ownerDocument = this.ownerDocument || globalThis.document;
      return child;
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
      while (current && current.nodeType === 1) {
        if (matches(current, selector)) return current;
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
    get textContent() {
      if (this.nodeType === 3) return this.nodeValue || "";
      return this.children.map((child) => child.textContent || "").join("");
    },
    set textContent(value) {
      this.children = [];
      const text = String(value ?? "");
      if (text) this.appendChild(createText(text, this));
    },
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
  return node;
}

/**
 * @param {string} type
 * @param {ReturnType<typeof createNode>} target
 * @param {{bubbles?: boolean}} [opts]
 */
function makeEvent(type, target, opts = {}) {
  return {
    type,
    target,
    currentTarget: null,
    bubbles: opts.bubbles !== false,
    defaultPrevented: false,
    _stopped: false,
    _immediate: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
    stopPropagation() {
      this._stopped = true;
    },
    stopImmediatePropagation() {
      this._stopped = true;
      this._immediate = true;
    },
  };
}

const body = createNode("body", HTMLElement);
const documentElement = createNode("html", HTMLElement);
documentElement.appendChild(body);

const document = {
  body,
  documentElement,
  createElement(tag) {
    const name = String(tag || "div").toLowerCase();
    const el = createNode(name, PROTOTYPES[name] || HTMLElement);
    el.ownerDocument = document;
    return el;
  },
  getElementById(id) {
    return documentElement.querySelector(`#${id}`);
  },
  querySelector(selector) {
    return documentElement.querySelector(selector);
  },
  querySelectorAll(selector) {
    return documentElement.querySelectorAll(selector);
  },
  addEventListener(type, fn, options) {
    body.addEventListener(type, fn, options);
    documentElement.addEventListener(type, fn, options);
    const capture = options === true || (options && options.capture);
    const key = capture ? "_cap" : "_bub";
    (document[key] ||= {});
    (document[key][type] ||= []).push(fn);
  },
  removeEventListener() {},
  _fire(event, capture) {
    const key = capture ? "_cap" : "_bub";
    event.currentTarget = document;
    for (const fn of (document[key] || {})[event.type] || []) {
      fn(event);
      if (event._immediate) break;
    }
  },
  parentElement: null,
  nodeType: 9,
  children: [documentElement],
};
body.ownerDocument = document;
documentElement.ownerDocument = document;
body.parentElement = documentElement;
documentElement.parentElement = null;
globalThis.document = document;

const format = (text) => String(text || "");
const postcard = contentFromPushedQuestionCard({
  kind: "mc",
  text: "Which graph?",
  text_html: `<p>Which graph?</p><img src="/static/stem.png" alt="Stem graph">`,
  options: ["A", "B"],
  options_html: [
    `<img src="/static/opt-a.png" alt="Option A">`,
    `<img src="/static/opt-b.png" alt="Option B">`,
  ],
  options_image_urls: ["/static/opt-a.png", "/static/opt-b.png"],
  image_url: "/static/field.png",
  image_urls: ["/static/field.png"],
});
if (!postcard) fail("postcard content was dropped");

const card = document.createElement("article");
card.className = "student-live-card is-open-focus";
card.dataset.liveCardId = "12";
card.dataset.liveCardStatus = "active";
card.innerHTML = `${liveQuestionHeadHtml(postcard, "mc", format)}${liveAnswerControlsHtml(
  "mc",
  postcard,
  "item:12",
  "—",
  "Submit Answer"
)}`;
body.appendChild(card);

mountQuestionLightbox(document);
syncQuestionLightbox(card);

const dialog = document.getElementById("live-q-lightbox");
if (!(dialog instanceof HTMLDialogElement)) fail("lightbox was not mounted");
const close = dialog.querySelector("[data-live-q-close]");
if (!close || close.getAttribute("aria-label") !== "Close" || close.textContent !== "Close") {
  fail("lightbox close button must be labelled Close");
}

const stemWrap = card.querySelector(".live-q-inline-figure");
const stemImg = stemWrap && stemWrap.querySelector("img");
if (!stemWrap || !(stemImg instanceof HTMLImageElement)) fail("stem image was not given a tap hint");
if (!stemWrap.querySelector(".live-q-zoom-badge") || !stemWrap.textContent.includes("Tap to enlarge")) {
  fail("stem image is missing the Tap to enlarge hint");
}

const choice = card.querySelector(".prompt-choice");
const magnifier = card.querySelector(".live-q-enlarge-option");
if (!(choice instanceof HTMLButtonElement) || !magnifier) fail("option magnifier was not painted");
if (magnifier.getAttribute("aria-label") !== "Enlarge image") fail("magnifier aria-label");
if (magnifier.getAttribute("title") !== "Enlarge image") fail("magnifier tooltip");
if (choice.contains(magnifier) === false) fail("magnifier should sit on the answer image");

syncQuestionLightbox(card);
if (card.querySelectorAll(".live-q-enlarge-option").length !== 2) {
  fail("re-render must not add a second magnifier");
}
if (card.querySelectorAll(".live-q-inline-figure").length !== 1) {
  fail("re-render must not wrap the stem image twice");
}

choice.addEventListener("click", () => {
  choice.classList.add("is-selected");
});

stemImg.click();
if (!dialog.open) fail("tapping an inline stem image should open the lightbox");
const stageImg = dialog.querySelector("img");
if (!(stageImg instanceof HTMLImageElement) || !String(stageImg.src).includes("stem.png")) {
  fail("lightbox should show the stem image");
}
if (choice.classList.contains("is-selected")) fail("stem enlarge selected an answer");
dialog.close();
if (dialog.open) fail("close did not shut the lightbox");

magnifier.click();
if (!dialog.open) fail("the option magnifier should open the same lightbox");
if (!String(stageImg.src).includes("opt-a.png")) fail("lightbox should show the answer image");
if (choice.classList.contains("is-selected")) {
  fail("the magnifier must not select the answer");
}
dialog.close();

const optionImg = choice.querySelector("img");
if (!(optionImg instanceof HTMLImageElement)) fail("option image missing");
optionImg.click();
if (dialog.open) fail("tapping the answer image itself should not open the lightbox");
if (!choice.classList.contains("is-selected")) fail("tapping the answer image should still select it");

console.log("ok");
