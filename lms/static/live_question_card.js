/**
 * The student live question card (head + individual answer controls).
 *
 * One renderer for the live class (``student-portal.js``) and the after-class
 * catch-up page (``catchup.js``), so a catch-up question looks exactly like
 * the card the student saw in class: same option text, math, images, classes.
 */

/** @typedef {(value: unknown) => string} QuestionFormatter ``formatQuestionHtml`` from ``/static/common.js``. */

import { stripDuplicateOptionTable } from "./mc_results_graph.js";

/**
 * Escape text for HTML.
 * @param {unknown} value
 * @returns {string}
 */
export function escapeText(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/**
 * Badge under a question image.
 *
 * Phones and other touch screens say tap. A fine pointer says click.
 * @param {boolean} finePointer True for a desktop hover pointer.
 * @returns {string}
 */
export function zoomBadgeLabel(finePointer) {
  return finePointer ? "Click to enlarge" : "Tap to enlarge";
}

/**
 * Layout class for one stacked question.
 *
 * The newest unanswered question stays open so Submit fits. An answered
 * or closed question collapses. Any other open question shares the stack.
 * @param {any} item Lifecycle row.
 * @param {number} focusId Newest unanswered live item id, or 0.
 * @returns {"is-compact"|"is-open-focus"|"is-open-extra"}
 */
/** Student board note prefix above the canvas (MCK-504). */
export const STUDENT_BOARD_FEEDBACK_LEAD = "Teacher feedback:";

export function questionCardLayout(item, focusId) {
  const status = String(item?.status || "active");
  if (status === "closed") return "is-compact";
  if (Number(item?.id) === Number(focusId)) return "is-open-focus";
  return "is-open-extra";
}

/**
 * Newest unanswered question in push order.
 *
 * Items are already sorted oldest to newest. Closed and answered rows
 * are skipped, so the last remaining id is the one that keeps Submit.
 * @param {any[]} items Visible stacked questions.
 * @returns {number}
 */
export function newestOpenQuestionId(items) {
  let id = 0;
  for (const item of items || []) {
    const status = String(item?.status || "active");
    if (status === "closed" || item?.my_response) continue;
    const next = Number(item?.id) || 0;
    if (next) id = next;
  }
  return id;
}

/**
 * Decide whether a newly published question may move the panel scroll.
 *
 * A student who is typing or drawing keeps their place and gets a pill.
 * A repaint of the same questions also keeps the place. The first paint
 * does not invent a pill.
 *
 * @param {{busy?: boolean, scrollTop?: number, previousIds?: string[], nextIds?: string[]}} state
 * @returns {{keepScroll: boolean, showPill: boolean, targetId: string, scrollTop: number}}
 */
export function newQuestionScrollPlan(state) {
  const prev = (state?.previousIds || []).map((id) => String(id || "")).filter(Boolean);
  const next = (state?.nextIds || []).map((id) => String(id || "")).filter(Boolean);
  const held = Number(state?.scrollTop) || 0;
  if (!prev.length) {
    return { keepScroll: false, showPill: false, targetId: "", scrollTop: 0 };
  }
  const seen = new Set(prev);
  const added = next.filter((id) => !seen.has(id));
  if (!added.length) {
    return { keepScroll: true, showPill: false, targetId: "", scrollTop: held };
  }
  const targetId = added[added.length - 1];
  if (state?.busy) {
    return { keepScroll: true, showPill: true, targetId, scrollTop: held };
  }
  return { keepScroll: false, showPill: false, targetId, scrollTop: held };
}

/**
 * One student figure: url, alt, and optional pixel size.
 * @param {any} body Question payload.
 * @returns {{url: string, alt: string, width: number, height: number}[]}
 */
function studentFigures(body) {
  const source = body && typeof body === "object" ? body : {};
  const listed = Array.isArray(source.image_urls) ? source.image_urls : [];
  const raw = listed.length
    ? listed
    : String(source.image_url || "").trim()
      ? [source.image_url]
      : [];
  const alts = Array.isArray(source.image_alts) ? source.image_alts : [];
  const singleAlt = String(source.image_alt || source.alt || "").trim();
  return raw
    .map((item, index) => {
      const record = item && typeof item === "object" ? item : { url: item };
      const url = String(record.url || record.src || "").trim();
      const fallback = `Question image ${index + 1}`;
      const alt = String(
        record.alt || alts[index] || (raw.length === 1 ? singleAlt : "") || fallback
      ).trim();
      return {
        url,
        alt: alt || fallback,
        width: Number(record.width) || 0,
        height: Number(record.height) || 0,
      };
    })
    .filter((figure) => figure.url);
}

/**
 * Student question images, capped in CSS and opened in one lightbox.
 *
 * Each image is a button. The badge says tap or click. Width and height
 * hold the box so Submit does not jump when the file arrives.
 * @param {any} imageUrl URL, URL list, or figure records.
 * @returns {string}
 */
export function questionImageHtmlStudent(imageUrl) {
  const figures = studentFigures(
    Array.isArray(imageUrl) || typeof imageUrl === "string"
      ? { image_urls: Array.isArray(imageUrl) ? imageUrl : [imageUrl] }
      : imageUrl
  );
  if (!figures.length) return "";
  const total = figures.length;
  const buttons = figures
    .map((figure, index) => {
      const width = figure.width > 0 ? figure.width : 4;
      const height = figure.height > 0 ? figure.height : 3;
      return `<button type="button" class="live-q-image-btn" data-live-q-image="${index}" aria-label="Enlarge image ${
        index + 1
      } of ${total}"><img class="live-question-image is-full" src="${escapeText(
        figure.url
      )}" alt="${escapeText(figure.alt)}" width="${width}" height="${height}" loading="eager" decoding="async"><span class="live-q-zoom-badge">${zoomBadgeLabel(
        false
      )}</span></button>`;
    })
    .join("");
  return `<div class="live-q-figures">${buttons}</div>`;
}

/**
 * Prefer server-rendered question HTML when available. Plain text with a
 * blank line (a Contest Question's stem / task break, MCK-161 INFO-2)
 * keeps its paragraphs via ``.is-paragraphs`` (``white-space: pre-line``).
 * @param {any} content
 * @param {QuestionFormatter} formatQuestionHtml
 * @returns {string}
 */
export function lifecyclePromptHtml(content, formatQuestionHtml) {
  const rendered = String(content?.text_html || "").trim();
  if (rendered) {
    const stem = stripDuplicateOptionTable(rendered, liveChoiceLabels(content));
    return `<span class="live-question-html">${stem}</span>`;
  }
  const raw = String(
    content?.text || content?.prompt || content?.question || "Live question"
  ).trim();
  const paragraphs = /\n\s*\n/.test(raw) ? " is-paragraphs" : "";
  return `<span class="live-question-html${paragraphs}">${formatQuestionHtml(raw)}</span>`;
}

/**
 * Render optional LaTeX under the student stem, never concatenated into text.
 * @param {any} content
 * @returns {string}
 */
export function lifecycleEquationHtml(content) {
  const latex = String(content?.equation_latex || content?.equation || "").trim();
  if (!latex) return "";
  const raw = latex.replace(/^\$+|\$+$/g, "").trim();
  return `<p class="student-live-equation"><span class="math-latex" data-latex="${escapeText(
    raw
  )}"></span></p>`;
}

/**
 * Flatten catalogue options or prompt choices into student-facing labels.
 * Image-only slots keep their id or the letter A, B, C so the submit
 * value matches server scoring and group pick.
 * @param {any} content
 * @returns {string[]}
 */
export function liveChoiceLabels(content) {
  const body = content && typeof content === "object" ? content : {};
  const options = Array.isArray(body.options) ? body.options : [];
  const listed = Array.isArray(body.choices) ? body.choices : [];
  const optionsHtml = Array.isArray(body.options_html) ? body.options_html : [];
  const optionImages = Array.isArray(body.options_image_urls)
    ? body.options_image_urls
    : [];
  const hasText = (raw) =>
    raw.some((choice) => {
      if (choice && typeof choice === "object") {
        return Boolean(String(choice.label || choice.text || choice.choice || "").trim());
      }
      return Boolean(String(choice || "").trim());
    });
  const hasFigures =
    optionsHtml.some((row) => String(row || "").trim()) ||
    optionImages.some((row) => String(row || "").trim());
  const raw =
    options.length && (hasText(options) || hasFigures || !listed.length)
      ? options
      : listed;
  const count = Math.max(raw.length, optionsHtml.length, optionImages.length);
  const labels = [];
  for (let index = 0; index < count; index += 1) {
    const choice = raw[index];
    let text = "";
    let ident = "";
    if (choice && typeof choice === "object") {
      text = String(choice.label || choice.text || choice.choice || "").trim();
      ident = String(choice.id || "").trim();
    } else {
      text = String(choice || "").trim();
    }
    const html = String(optionsHtml[index] || "").trim();
    const image = String(optionImages[index] || "").trim();
    if (text) labels.push(text);
    else if (html || image || ident) labels.push(ident || String.fromCharCode(65 + index));
  }
  return labels;
}

/**
 * Build the student card body from a publish postcard, before ``/state``.
 * Copies stem HTML and option image fields. Answer keys are not read.
 * ``applyPushedQuestionCard`` paints from this object.
 * @param {any} card
 * @returns {any | null}
 */
export function contentFromPushedQuestionCard(card) {
  if (!card || typeof card !== "object") return null;
  const kind = String(card.kind || "mc");
  const text = String(card.text || "");
  const options = Array.isArray(card.options) ? card.options : [];
  const textHtml = String(card.text_html || "").trim();
  const optionsHtml = Array.isArray(card.options_html) ? card.options_html : [];
  const optionImages = Array.isArray(card.options_image_urls)
    ? card.options_image_urls
    : [];
  const listed = Array.isArray(card.image_urls) ? card.image_urls : [];
  const hasImages = Boolean(String(card.image_url || "").trim()) || listed.length > 0;
  const hasOptions =
    options.length > 0 ||
    optionsHtml.some((row) => String(row || "").trim()) ||
    optionImages.some((row) => String(row || "").trim());
  if (!text.trim() && !textHtml && !hasOptions && !hasImages) return null;
  /** @type {any} */
  const content = { type: kind, text, options };
  if (textHtml) content.text_html = textHtml;
  if (optionsHtml.length) content.options_html = optionsHtml;
  if (optionImages.length) content.options_image_urls = optionImages;
  if (card.image_url) content.image_url = String(card.image_url);
  if (listed.length) content.image_urls = listed;
  if (Array.isArray(card.image_alts) && card.image_alts.length) {
    content.image_alts = card.image_alts;
  }
  if (card.image_alt) content.image_alt = String(card.image_alt);
  if (card.image_as_board_background != null) {
    content.image_as_board_background = Boolean(card.image_as_board_background);
  }
  const answerWith = String(card.answer_with || "");
  if (answerWith === "board" || answerWith === "both") {
    content.answer_with = answerWith;
  }
  if (kind === "rank") content.rank_options = options;
  return content;
}

/**
 * Return the student answer kind for one lifecycle card.
 * Catalogue ``type`` / ``integer_only`` win over a stale MC compatibility prompt.
 * @param {any} item
 * @returns {string}
 */
export function lifecycleAnswerKind(item) {
  const prompt = item?.prompt || {};
  const content = item?.content || prompt.payload || {};
  if (
    content.integer_only ||
    String(content.type || "").toLowerCase() === "numeric" ||
    String(prompt.kind || "").toLowerCase() === "numeric"
  ) {
    return "numeric";
  }
  if (
    String(content.kind || "").toLowerCase() === "artifact" ||
    String(content.type || "").toLowerCase() === "artifact" ||
    String(prompt.kind || "").toLowerCase() === "artifact" ||
    String(content.artifact_id || "").trim()
  ) {
    return "artifact";
  }
  const type = String(content.type || prompt.kind || "mc").toLowerCase();
  if (type === "poll" && !liveChoiceLabels(content).length) {
    return "text";
  }
  return type;
}

/**
 * Kicker, graph image, question, optional stem and equation of one card.
 * @param {any} content Student-safe question payload.
 * @param {string} fallbackKind Prompt kind when the payload has no ``type``.
 * @param {QuestionFormatter} formatQuestionHtml
 * @returns {string}
 */
export function liveQuestionHeadHtml(content, fallbackKind, formatQuestionHtml) {
  const body = content && typeof content === "object" ? content : {};
  const title = String(body.title || "").trim();
  const stem = String(body.stem || body.prompt || "").trim();
  const mode = String(body.answer_with || "").toLowerCase();
  const boardBg =
  (mode === "board" || mode === "both") &&
    Boolean(String(body.image_url || "").trim()) &&
    body.image_as_board_background !== false;
  const imageBody = boardBg ? { ...body, image_url: "", image_urls: [] } : body;
  return `<div class="student-live-card-head">
          <span class="student-live-card-kicker">${escapeText(
            String(body.type || fallbackKind || "Question").toUpperCase()
          )}</span>
        </div>
        ${questionImageHtmlStudent(imageBody)}
        <h2>${lifecyclePromptHtml({
          ...body,
          text: body.title || body.text,
          prompt: body.title || body.text || body.prompt,
        }, formatQuestionHtml)}</h2>
        ${
          title && stem && stem !== title
            ? `<p class="student-live-stem">${formatQuestionHtml(stem)}</p>`
            : ""
        }
        ${lifecycleEquationHtml(body)}`;
}

/**
 * Multiple choice, numeric, or free-text answer controls for one card.
 * Rank and Artifact controls stay in ``student-portal.js`` (live state).
 * @param {string} kind From ``lifecycleAnswerKind``.
 * @param {any} content Student-safe question payload.
 * @param {string} action ``data-live-action`` value.
 * @param {string} current Already-chosen label, or ``"—"`` for none.
 * @param {string} submitLabel Submit button text.
 * @returns {string}
 */
export function liveAnswerControlsHtml(kind, content, action, current, submitLabel) {
  const body = content && typeof content === "object" ? content : {};
  if (kind === "mc" || kind === "poll") {
    const choices = liveChoiceLabels(body);
    const optionsHtml = Array.isArray(body.options_html) ? body.options_html : [];
    const optionImages = Array.isArray(body.options_image_urls)
      ? body.options_image_urls
      : [];
    return `<div class="student-live-answer-controls" data-live-action="${action}">
      ${choices
        .map(
          (choice, index) => {
            const html = String(optionsHtml[index] || "").trim();
            const image = String(optionImages[index] || "").trim();
            const optionBody = html
              ? `<span class="live-question-html">${optionsHtml[index]}</span>`
              : image
                ? `<span class="live-question-html"><img src="${escapeText(
                    image
                  )}" alt=""></span>`
                : escapeText(choice);
            return `<button type="button" class="prompt-choice${
              String(choice) === current ? " is-selected" : ""
            }" data-live-choice="${escapeText(choice)}">${optionBody}</button>`;
          }
        )
        .join("")}
      <button type="button" class="prompt-submit" data-live-submit="${action}">${submitLabel}</button>
    </div>`;
  }
  if (kind === "numeric") {
    const value = current === "—" ? "" : current;
    const integerOnly = Boolean(body.integer_only);
    return `<div class="student-live-answer-controls" data-live-action="${action}">
      <label class="prompt-numeric"><span>${integerOnly ? "Enter an integer…" : "Your answer"}</span>
        <input type="number" inputmode="${integerOnly ? "numeric" : "decimal"}" step="${
          integerOnly ? "1" : "any"
        }" data-live-value placeholder="${escapeText(
          body.placeholder || (integerOnly ? "Enter an integer…" : "Enter a number")
        )}" value="${escapeText(value)}">
      </label>
      <button type="button" class="prompt-submit" data-live-submit="${action}">${submitLabel}</button>
    </div>`;
  }
  const value = current === "—" ? "" : current;
  return `<div class="student-live-answer-controls" data-live-action="${action}">
    <label class="prompt-share"><span>Your answer</span>
      <textarea rows="3" maxlength="2000" data-live-text>${escapeText(value)}</textarea>
    </label>
    <button type="button" class="prompt-submit" data-live-submit="${action}">${submitLabel}</button>
  </div>`;
}

const LIGHTBOX_ID = "live-q-lightbox";

/**
 * Rewrite every badge for the pointer this screen actually has.
 * @param {ParentNode} root
 * @param {boolean} finePointer
 */
export function applyZoomBadges(root, finePointer) {
  const label = zoomBadgeLabel(finePointer);
  root.querySelectorAll(".live-q-zoom-badge").forEach((node) => {
    node.textContent = label;
  });
}

const ENLARGE_IMAGE_LABEL = "Enlarge image";

const MAGNIFIER_ICON = `<svg viewBox="0 0 24 24" width="16" height="16" focusable="false" aria-hidden="true"><circle cx="10" cy="10" r="6.25" fill="none" stroke="currentColor" stroke-width="2"/><path d="M15 15l6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;

/**
 * Owner document for a painted node.
 * @param {Element} node
 * @returns {Document | null}
 */
function ownerDoc(node) {
  return node.ownerDocument || (typeof document !== "undefined" ? document : null);
}

/**
 * Wrap one image in a span, keeping its place in the stem or option.
 * @param {HTMLImageElement} img
 * @param {string} className
 * @returns {HTMLElement | null}
 */
function wrapImage(img, className) {
  const parent = img.parentNode || img.parentElement;
  const doc = ownerDoc(img);
  if (!parent || !doc) return null;
  const wrap = doc.createElement("span");
  wrap.className = className;
  parent.insertBefore(wrap, img);
  wrap.appendChild(img);
  return wrap;
}

/**
 * Put a visible enlarge hint on one inline stem image.
 * @param {HTMLImageElement} img
 */
function decorateStemImage(img) {
  const wrap = wrapImage(img, "live-q-inline-figure");
  if (!wrap) return;
  wrap.setAttribute("role", "button");
  wrap.setAttribute("tabindex", "0");
  wrap.setAttribute("aria-label", ENLARGE_IMAGE_LABEL);
  const badge = (ownerDoc(img) || document).createElement("span");
  badge.className = "live-q-zoom-badge";
  badge.textContent = zoomBadgeLabel(false);
  wrap.appendChild(badge);
}

/**
 * Put the corner magnifier on one answer image, without nesting a button.
 * @param {HTMLImageElement} img
 */
function decorateOptionImage(img) {
  const wrap = wrapImage(img, "live-q-option-figure");
  if (!wrap) return;
  const doc = ownerDoc(img);
  if (!doc) return;
  const control = doc.createElement("span");
  control.className = "live-q-enlarge-option";
  control.setAttribute("role", "button");
  control.setAttribute("tabindex", "0");
  control.setAttribute("title", ENLARGE_IMAGE_LABEL);
  control.setAttribute("aria-label", ENLARGE_IMAGE_LABEL);
  const icon = doc.createElement("span");
  icon.className = "live-q-enlarge-option-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.innerHTML = MAGNIFIER_ICON;
  control.appendChild(icon);
  wrap.appendChild(control);
}

/**
 * Add enlarge hints and option magnifiers after a card paint.
 *
 * Safe to run again: already-wrapped images are left alone. Event
 * handlers stay on the document, so nothing is re-bound.
 * @param {ParentNode} root
 */
export function enhanceQuestionImages(root) {
  if (!root || typeof root.querySelectorAll !== "function") return;
  root.querySelectorAll(".live-question-html img, .prompt-choice img").forEach((node) => {
    if (!(node instanceof HTMLImageElement)) return;
    if (node.closest(".live-q-image-btn, .live-q-inline-figure, .live-q-option-figure")) {
      return;
    }
    if (node.closest(".prompt-choice")) decorateOptionImage(node);
    else decorateStemImage(node);
  });
}

/**
 * True when this screen has a mouse or trackpad, not a touch-only phone.
 * @returns {boolean}
 */
function finePointerNow() {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(hover: hover) and (pointer: fine)").matches;
}

/**
 * Build the one page-level image dialog, if it is not already there.
 *
 * The dialog sits on document.body, outside the question stack, so a
 * stacked card cannot clip it. Close is a real button.
 * @param {Document} doc
 * @returns {HTMLDialogElement | null}
 */
function ensureQuestionLightbox(doc) {
  const found = doc.getElementById(LIGHTBOX_ID);
  if (found instanceof HTMLDialogElement) return found;
  if (!doc.body) return null;
  const dialog = doc.createElement("dialog");
  dialog.id = LIGHTBOX_ID;
  dialog.className = "live-q-lightbox";
  dialog.setAttribute("aria-label", "Question image, enlarged");
  dialog.innerHTML = `<div class="live-q-lightbox-bar">
      <button type="button" class="live-q-lightbox-close" data-live-q-close aria-label="Close">Close</button>
    </div>
    <div class="live-q-lightbox-stage"><img alt=""></div>`;
  doc.body.appendChild(dialog);
  return dialog;
}

/**
 * Open, zoom, and page one question's images in the shared dialog.
 *
 * A desktop double-click or phone double-tap flips fit vs 2x.
 * Pinch-zoom stays with the browser.
 * The dialog closes when that question leaves the open stack.
 * @param {Document} doc
 */
export function mountQuestionLightbox(doc) {
  const root = doc || (typeof document !== "undefined" ? document : null);
  if (!root || root.documentElement?.dataset.liveQLightbox === "1") return;
  const dialog = ensureQuestionLightbox(root);
  if (!dialog) return;
  root.documentElement.dataset.liveQLightbox = "1";
  const image = dialog.querySelector("img");
  const stage = dialog.querySelector(".live-q-lightbox-stage");
  const close = dialog.querySelector("[data-live-q-close]");
  /** @type {{src: string, alt: string}[]} */
  let figures = [];
  let index = 0;
  /** @type {HTMLElement | null} */
  let opener = null;
  let lastTap = 0;

  /**
   * Paint the current figure in the lightbox.
   */
  function paint() {
    const figure = figures[index];
    if (!(image instanceof HTMLImageElement) || !figure) return;
    image.src = figure.src;
    image.alt = figure.alt;
  }

  /**
   * Flip between fit and twice the fitted size.
   */
  function toggleZoom() {
    dialog.classList.toggle("is-2x");
    if (stage instanceof HTMLElement) stage.scrollTop = 0;
    paint();
  }

  dialog.addEventListener("close", () => {
    root.documentElement.classList.remove("live-q-lightbox-open");
    dialog.classList.remove("is-2x");
    if (opener) opener.focus();
    opener = null;
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  close?.addEventListener("click", () => dialog.close());
  image?.addEventListener("dblclick", (event) => {
    event.preventDefault();
    toggleZoom();
  });
  stage?.addEventListener("pointerup", (event) => {
    if (!(event instanceof PointerEvent) || event.pointerType === "mouse") return;
    const now = Date.now();
    if (now - lastTap < 350) toggleZoom();
    lastTap = now;
  });

  /**
   * Images in one card: gallery buttons, then inline stem images, then
   * answer images, so paging stays in document order.
   * @param {Element | null} card
   * @returns {HTMLImageElement[]}
   */
  function cardImages(card) {
    /** @type {HTMLImageElement[]} */
    const images = [];
    const seen = new Set();
    const add = (node) => {
      if (!(node instanceof HTMLImageElement) || !String(node.src || "").trim()) return;
      if (seen.has(node)) return;
      seen.add(node);
      images.push(node);
    };
    if (card instanceof Element) {
      card.querySelectorAll(".live-q-image-btn img").forEach(add);
      card.querySelectorAll(".live-question-html img").forEach((node) => {
        if (!node.closest(".prompt-choice")) add(node);
      });
      card.querySelectorAll(".prompt-choice img").forEach(add);
    }
    return images;
  }

  /**
   * The image a control should open.
   * @param {Element} openerNode
   * @returns {HTMLImageElement | null}
   */
  function imageFromOpener(openerNode) {
    if (openerNode instanceof HTMLImageElement) return openerNode;
    const host = openerNode.closest(
      ".live-q-image-btn, .live-q-inline-figure, .live-q-option-figure"
    ) || openerNode;
    const found = host instanceof HTMLImageElement ? host : host.querySelector("img");
    return found instanceof HTMLImageElement ? found : null;
  }

  /**
   * Open the shared lightbox on one card image.
   * @param {Element} openerNode
   */
  function openFrom(openerNode) {
    const card = openerNode.closest(".student-live-card, .live-question-card");
    const images = cardImages(card instanceof Element ? card : null);
    const clicked = imageFromOpener(openerNode);
    if (clicked && !images.includes(clicked)) images.push(clicked);
    figures = images
      .map((node) => ({
        src: node.src,
        alt: node.alt || "Question image 1",
      }))
      .filter((figure) => figure.src);
    if (!figures.length || !(image instanceof HTMLImageElement)) return;
    index = clicked ? Math.max(0, images.indexOf(clicked)) : 0;
    if (index >= figures.length) index = 0;
    opener = openerNode instanceof HTMLElement ? openerNode : clicked;
    dialog.classList.remove("is-2x");
    dialog.dataset.cardId = card instanceof HTMLElement ? card.dataset.liveCardId || "" : "";
    paint();
    if (typeof dialog.showModal === "function") dialog.showModal();
    root.documentElement.classList.add("live-q-lightbox-open");
    if (close instanceof HTMLButtonElement) close.focus();
  }

  /**
   * True when this event should open the lightbox and, for a magnifier,
   * must not also select the answer.
   * @param {Event} event
   * @returns {Element | null}
   */
  function enlargeOpener(event) {
    const target = event.target;
    if (!(target instanceof Element)) return null;
    if (target.closest(".live-q-lightbox")) return null;
    const magnifier = target.closest(".live-q-enlarge-option");
    if (magnifier) return magnifier;
    const button = target.closest(".live-q-image-btn");
    if (button instanceof HTMLButtonElement) return button;
    const inline = target.closest(".live-q-inline-figure");
    if (inline && !inline.closest(".prompt-choice")) return inline;
    const stemImg = target.closest(".live-question-html img");
    if (stemImg instanceof HTMLImageElement && !stemImg.closest(".prompt-choice")) {
      return stemImg;
    }
    return null;
  }

  root.addEventListener(
    "click",
    (event) => {
      const openerNode = enlargeOpener(event);
      if (!openerNode) return;
      if (openerNode.classList?.contains("live-q-enlarge-option") || openerNode.closest?.(".live-q-enlarge-option")) {
        event.preventDefault();
        event.stopPropagation();
      }
      openFrom(openerNode);
    },
    true
  );
  root.addEventListener(
    "pointerdown",
    (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (!target.closest(".live-q-enlarge-option")) return;
      event.preventDefault();
      event.stopPropagation();
    },
    true
  );
  root.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    const openerNode = target.closest(".live-q-enlarge-option, .live-q-inline-figure");
    if (!openerNode || openerNode.closest(".live-q-lightbox")) return;
    event.preventDefault();
    event.stopPropagation();
    openFrom(openerNode);
  });
}

/**
 * Close the lightbox when its question is closed, minimized, or gone.
 *
 * An answered card may still enlarge a thumbnail. A closed card, a card
 * the teacher minimized off the stack, or the end of class closes it.
 * @param {ParentNode} root Stack that was just painted.
 */
export function syncQuestionLightbox(root) {
  const doc = root.ownerDocument || (typeof document !== "undefined" ? document : null);
  if (!doc) return;
  enhanceQuestionImages(root);
  applyZoomBadges(root, finePointerNow());
  const dialog = doc.getElementById(LIGHTBOX_ID);
  if (!(dialog instanceof HTMLDialogElement) || !dialog.open) return;
  const id = dialog.dataset.cardId || "";
  const card = id ? root.querySelector(`[data-live-card-id="${CSS.escape(id)}"]`) : null;
  if (!(card instanceof HTMLElement) || card.dataset.liveCardStatus === "closed") {
    dialog.close();
  }
}
