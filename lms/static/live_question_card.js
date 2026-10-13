/**
 * The student live question card (head + individual answer controls).
 *
 * One renderer for the live class (``student-portal.js``) and the after-class
 * catch-up page (``catchup.js``), so a catch-up question looks exactly like
 * the card the student saw in class: same option text, math, images, classes.
 */

/** @typedef {(value: unknown) => string} QuestionFormatter ``formatQuestionHtml`` from ``/static/common.js``. */

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

/** Render a graph image at full card width for lifecycle questions. */
export function questionImageHtmlStudent(imageUrl) {
  const url = String(imageUrl || "").trim();
  if (!url) return "";
  return `<img class="live-question-image is-full" src="${escapeText(url)}" alt="Question graph" loading="lazy">`;
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
  if (rendered) return `<span class="live-question-html">${rendered}</span>`;
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
        ${questionImageHtmlStudent(body.image_url)}
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
