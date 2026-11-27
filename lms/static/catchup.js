/**
 * MCK-198 catch-up page: paint each question with the live class card.
 *
 * Questions come from ``#catchup-data`` (student-safe payloads, same as the
 * live card). Submitting posts the hidden ``#catchup-answer`` form, so the
 * server keeps owning which questions this student may still answer.
 */
import { formatQuestionHtml, renderLiveQuestionMath } from "/static/common.js";
import {
  escapeText,
  lifecycleAnswerKind,
  liveAnswerControlsHtml,
  liveQuestionHeadHtml,
  mountQuestionLightbox,
  syncQuestionLightbox,
} from "/static/live_question_card.js";
import { bindWhiteboard } from "/static/live_whiteboard.js";
import { canvasToPngDataUrl, catchupDrawControlsHtml } from "/static/team_draw.js";
import { featureOn } from "/static/teacher_features.js";
import { mountCatchupQuestionBoards } from "./question_board_ui.js";

/**
 * True when this catch-up's teacher may show a draw board.
 * Team draw and group draw are the catch-up drawing features.
 * @returns {boolean}
 */
function catchupDrawOn() {
  return featureOn("team_draw") || featureOn("group_draw");
}

/**
 * Option rows for a solo group-rank catch-up. Ids are the ones the server sent.
 * @param {any} content
 * @returns {{id: string, label: string}[]}
 */
function groupRankOptions(content) {
  const body = content && typeof content === "object" ? content : {};
  const raw = Array.isArray(body.rank_options)
    ? body.rank_options
    : Array.isArray(body.options)
      ? body.options
      : Array.isArray(body.choices)
        ? body.choices
        : [];
  return raw
    .map((row, index) => {
      if (row && typeof row === "object") {
        const label = String(row.label || row.text || "").trim();
        const id = String(row.id || label || index);
        return { id, label };
      }
      const label = String(row || "").trim();
      return { id: label, label };
    })
    .filter((row) => row.label);
}

/**
 * Solo group rank: tap options into an order and send that order.
 * Any complete order counts. The page does not show an answer order.
 * @param {any} content
 * @returns {string}
 */
function groupRankCatchupHtml(content) {
  const options = groupRankOptions(content);
  const buttons = options
    .map(
      (row) =>
        `<button type="button" class="prompt-choice" data-catchup-rank="${escapeText(row.id)}">${escapeText(
          row.label
        )}</button>`
    )
    .join("");
  return `<div class="student-live-answer-controls" data-catchup-rank-n="${options.length}">
    <ol class="catchup-rank-order" data-catchup-rank-order></ol>
    <div class="catchup-rank-options">${buttons}</div>
    <button type="button" class="prompt-submit" data-live-submit="catchup-rank" disabled>Submit Answer</button>
  </div>`;
}

/**
 * Teacher note under a catch-up question card.
 * @param {string} text
 * @returns {string}
 */
function boardFeedbackBlock(text) {
  const note = String(text || "").trim();
  if (!note) return "";
  return `<div class="prompt-feedback qboard-feedback" role="note">
    <p class="prompt-feedback-lead">Feedback</p>
    <p class="prompt-feedback-why">${escapeText(note)}</p>
  </div>`;
}

/**
 * One catch-up question as a live question card.
 * Rank and Artifact need the live session, so they take a typed answer.
 * A group rank is ordered here and sent as a complete list.
 * An individual draw uses the drawing board. Submit sends the PNG.
 * @param {any} question ``{question_key, content, prompt_kind, answered}``
 * @param {string} tag "Answered after class" copy.
 * @returns {string}
 */
export function catchupCardHtml(question, tag) {
  const content = question && typeof question.content === "object" ? question.content : {};
  const promptKind = String(question?.prompt_kind || "");
  const liveKind = lifecycleAnswerKind({ content, prompt: { kind: promptKind } });
  const kind = liveKind === "rank" || liveKind === "artifact" ? "text" : liveKind;
  const boardMode = String(content.answer_with || (question?.board_answer ? "board" : ""));
  const board = boardMode === "board" || boardMode === "both" || Boolean(question?.board_answer);
  const boardImage =
    content?.image_as_board_background !== false && content?.image_url
      ? String(content.image_url)
      : "";
  const groupKey = question?.group_item && !question?.item_id ? String(question?.question_key || "") : "";
  const body = board
    ? `<div class="qboard-slot" data-qboard-catchup="${escapeText(String(question?.item_id || ""))}" data-qboard-group-key="${escapeText(groupKey)}" data-qboard-mode="${escapeText(boardMode)}" data-qboard-readonly="${question?.read_only ? "1" : "0"}" data-qboard-image="${escapeText(boardImage)}"></div>`
    : question?.answered
    ? `<p class="student-live-own-answer">${escapeText(tag)}</p>`
    : question?.group_rank
      ? groupRankCatchupHtml(content)
      : liveKind === "team_draw"
        ? catchupDrawOn()
          ? catchupDrawControlsHtml()
          : ""
        : liveAnswerControlsHtml(kind, content, "individual", "—", "Submit Answer");
  const category = question?.group_item || question?.group_rank ? "group" : String(question?.category || "individual");
  return `<article class="student-live-card is-${question?.answered ? "closed" : "active"}" data-question-key="${escapeText(
    question?.question_key || ""
  )}" data-catchup-category="${escapeText(category)}"${question?.group_rank ? ' data-group-rank="1"' : ""}${question?.group_item ? ' data-group-item="1"' : ""}>
        ${liveQuestionHeadHtml(content, promptKind, formatQuestionHtml)}
        ${body}
        ${boardFeedbackBlock(question?.board_feedback)}
      </article>`;
}

/**
 * The answer a card holds right now, or "" when nothing is chosen/typed.
 * A draw card stays empty until the board has a stroke.
 * @param {Element} card
 * @returns {string}
 */
function cardAnswer(card) {
  const draw = card.querySelector("canvas.catchup-draw-canvas");
  if (draw instanceof HTMLCanvasElement) {
    if (draw.dataset.hasStroke !== "1") return "";
    return canvasToPngDataUrl(draw);
  }
  const rank = card.querySelector("[data-catchup-rank-order]");
  if (rank) {
    const ids = [...rank.querySelectorAll("[data-catchup-rank-id]")].map((node) =>
      node.getAttribute("data-catchup-rank-id") || ""
    );
    const need = Number(card.querySelector("[data-catchup-rank-n]")?.getAttribute("data-catchup-rank-n")) || 0;
    if (!ids.length || ids.length !== need) return "";
    return JSON.stringify(ids);
  }
  const picked = card.querySelector("[data-live-choice].is-selected");
  if (picked) return picked.getAttribute("data-live-choice") || "";
  const numeric = card.querySelector("[data-live-value]");
  if (numeric instanceof HTMLInputElement) return numeric.value.trim();
  const text = card.querySelector("[data-live-text]");
  if (text instanceof HTMLTextAreaElement) return text.value.trim();
  return "";
}

/**
 * Tap a group-rank option into the next spot, or tap it again to take it out.
 * @param {Element} card
 * @param {Element} button
 */
function toggleCatchupRank(card, button) {
  const list = card.querySelector("[data-catchup-rank-order]");
  if (!list) return;
  const id = button.getAttribute("data-catchup-rank") || "";
  const existing = list.querySelector(`[data-catchup-rank-id="${CSS.escape(id)}"]`);
  if (existing) {
    existing.remove();
    button.classList.remove("is-selected");
  } else {
    const spot = list.querySelectorAll("[data-catchup-rank-id]").length + 1;
    const row = document.createElement("li");
    row.setAttribute("data-catchup-rank-id", id);
    row.textContent = `${spot}. ${button.textContent || ""}`;
    list.appendChild(row);
    button.classList.add("is-selected");
  }
  list.querySelectorAll("[data-catchup-rank-id]").forEach((node, index) => {
    const label = node.textContent?.replace(/^\d+\.\s*/, "") || "";
    node.textContent = `${index + 1}. ${label}`;
  });
  const need = Number(card.querySelector("[data-catchup-rank-n]")?.getAttribute("data-catchup-rank-n")) || 0;
  const ready = list.querySelectorAll("[data-catchup-rank-id]").length === need && need > 0;
  const submit = card.querySelector("[data-live-submit]");
  if (submit instanceof HTMLButtonElement) submit.disabled = !ready;
}

/** Paint the cards, typeset math, and wire choice + submit buttons. */
function mountCatchup() {
  const root = document.getElementById("catchup-questions");
  const dataEl = document.getElementById("catchup-data");
  const form = document.getElementById("catchup-answer");
  if (!root || !dataEl || !(form instanceof HTMLFormElement)) return;
  let data = {};
  try {
    data = JSON.parse(dataEl.textContent || "{}");
  } catch {
    data = {};
  }
  const questions = Array.isArray(data.questions) ? data.questions : [];
  const tabs = [
    ["individual", "Individual"],
    ["group", "Group"],
    ["formative", "Formative"],
  ];
  const tabBar = `<div class="catchup-tabs" role="tablist" aria-label="Catch-up categories">${tabs
    .map(
      ([id, label], index) =>
        `<button type="button" class="catchup-tab${index === 0 ? " is-on" : ""}" role="tab" aria-selected="${
          index === 0 ? "true" : "false"
        }" data-catchup-tab="${id}">${label}</button>`
    )
    .join("")}</div>`;
  root.innerHTML = tabBar + questions.map((q) => catchupCardHtml(q, String(data.tag || ""))).join("");
  const showCategory = (id) => {
    root.querySelectorAll("[data-catchup-tab]").forEach((btn) => {
      const on = btn.getAttribute("data-catchup-tab") === id;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    root.querySelectorAll("[data-catchup-category]").forEach((card) => {
      card.hidden = card.getAttribute("data-catchup-category") !== id;
    });
  };
  showCategory("individual");
  root.addEventListener("click", (event) => {
    const tab = event.target instanceof Element ? event.target.closest("[data-catchup-tab]") : null;
    if (!tab) return;
    showCategory(tab.getAttribute("data-catchup-tab") || "individual");
  });
  mountQuestionLightbox(document);
  syncQuestionLightbox(root);
  const parts = window.location.pathname.split("/").filter(Boolean);
  const token = parts.length ? parts[parts.length - 1] : "";
  mountCatchupQuestionBoards(token);
  if (catchupDrawOn()) root.querySelectorAll("canvas.catchup-draw-canvas").forEach((canvas) => {
    if (!(canvas instanceof HTMLCanvasElement)) return;
    const submit = canvas.closest(".student-live-card")?.querySelector("[data-live-submit]");
    bindWhiteboard(canvas, {
      onInkChange(hasStroke) {
        canvas.dataset.hasStroke = hasStroke ? "1" : "";
        if (submit instanceof HTMLButtonElement) submit.disabled = !hasStroke;
      },
    });
  });
  void renderLiveQuestionMath(root);
  root.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const card = target?.closest(".student-live-card");
    if (!card) return;
    const rankPick = target.closest("[data-catchup-rank]");
    if (rankPick && card) {
      toggleCatchupRank(card, rankPick);
      return;
    }
    const choice = target.closest("[data-live-choice]");
    if (choice) {
      card.querySelectorAll("[data-live-choice]").forEach((btn) => {
        btn.classList.toggle("is-selected", btn === choice);
      });
      return;
    }
    const submit = target.closest("[data-live-submit]");
    if (!submit) return;
    if (submit instanceof HTMLButtonElement && submit.disabled) return;
    const answer = cardAnswer(card);
    if (!answer) return;
    form.elements.namedItem("question_key").value = card.getAttribute("data-question-key") || "";
    form.elements.namedItem("answer").value = answer;
    form.submit();
  });
}

mountCatchup();
