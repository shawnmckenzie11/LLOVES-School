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
export function questionCardLayout(item, focusId) {
  const status = String(item?.status || "active");
  if (status === "closed" || item?.my_response) return "is-compact";
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
      )}" alt="${escapeText(figure.alt)}" width="${width}" height="${height}" loading="lazy"><span class="live-q-zoom-badge">${zoomBadgeLabel(
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
 * @param {any} content
 * @returns {string[]}
 */
export function liveChoiceLabels(content) {
  const body = content && typeof content === "object" ? content : {};
  const raw =
    Array.isArray(body.choices) && body.choices.length
      ? body.choices
      : Array.isArray(body.options)
        ? body.options
        : [];
  return raw
    .map((choice) => {
      if (choice && typeof choice === "object") {
        return String(choice.label || choice.text || choice.choice || "").trim();
      }
      return String(choice || "").trim();
    })
    .filter(Boolean);
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
  return `<div class="student-live-card-head">
          <span class="student-live-card-kicker">${escapeText(
            String(body.type || fallbackKind || "Question").toUpperCase()
          )}</span>
        </div>
        ${questionImageHtmlStudent(body)}
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
    return `<div class="student-live-answer-controls" data-live-action="${action}">
      ${choices
        .map(
          (choice, index) => {
            const optionBody = String(optionsHtml[index] || "").trim()
              ? `<span class="live-question-html">${optionsHtml[index]}</span>`
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
      <button type="button" class="live-q-lightbox-close" data-live-q-close>Close</button>
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

  root.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const button = target.closest(".live-q-image-btn");
    if (!(button instanceof HTMLButtonElement)) return;
    const card = button.closest(".student-live-card, .live-question-card");
    const buttons = [
      ...(card ? card.querySelectorAll(".live-q-image-btn") : [button]),
    ];
    figures = buttons
      .map((node) => {
        const img = node.querySelector("img");
        return {
          src: img instanceof HTMLImageElement ? img.src : "",
          alt: img instanceof HTMLImageElement ? img.alt : "Question image 1",
        };
      })
      .filter((figure) => figure.src);
    if (!figures.length || !(image instanceof HTMLImageElement)) return;
    index = Math.max(0, buttons.indexOf(button));
    opener = button;
    dialog.classList.remove("is-2x");
    dialog.dataset.cardId = card instanceof HTMLElement ? card.dataset.liveCardId || "" : "";
    paint();
    if (typeof dialog.showModal === "function") dialog.showModal();
    root.documentElement.classList.add("live-q-lightbox-open");
    if (close instanceof HTMLButtonElement) close.focus();
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
  applyZoomBadges(root, finePointerNow());
  const dialog = doc.getElementById(LIGHTBOX_ID);
  if (!(dialog instanceof HTMLDialogElement) || !dialog.open) return;
  const id = dialog.dataset.cardId || "";
  const card = id ? root.querySelector(`[data-live-card-id="${CSS.escape(id)}"]`) : null;
  if (!(card instanceof HTMLElement) || card.dataset.liveCardStatus === "closed") {
    dialog.close();
  }
}
