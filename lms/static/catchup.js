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
} from "/static/live_question_card.js";

/**
 * One catch-up question as a live question card.
 * Rank and Artifact need the live session, so they take a typed answer.
 * @param {any} question ``{question_key, content, prompt_kind, answered}``
 * @param {string} tag "Answered after class" copy.
 * @returns {string}
 */
export function catchupCardHtml(question, tag) {
  const content = question && typeof question.content === "object" ? question.content : {};
  const promptKind = String(question?.prompt_kind || "");
  const liveKind = lifecycleAnswerKind({ content, prompt: { kind: promptKind } });
  const kind = liveKind === "rank" || liveKind === "artifact" ? "text" : liveKind;
  const body = question?.answered
    ? `<p class="student-live-own-answer">${escapeText(tag)}</p>`
    : liveAnswerControlsHtml(kind, content, "individual", "—", "Submit Answer");
  return `<article class="student-live-card is-${question?.answered ? "closed" : "active"}" data-question-key="${escapeText(
    question?.question_key || ""
  )}">
        ${liveQuestionHeadHtml(content, promptKind, formatQuestionHtml)}
        ${body}
      </article>`;
}

/**
 * The answer a card holds right now, or "" when nothing is chosen/typed.
 * @param {Element} card
 * @returns {string}
 */
function cardAnswer(card) {
  const picked = card.querySelector("[data-live-choice].is-selected");
  if (picked) return picked.getAttribute("data-live-choice") || "";
  const numeric = card.querySelector("[data-live-value]");
  if (numeric instanceof HTMLInputElement) return numeric.value.trim();
  const text = card.querySelector("[data-live-text]");
  if (text instanceof HTMLTextAreaElement) return text.value.trim();
  return "";
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
  root.innerHTML = questions.map((q) => catchupCardHtml(q, String(data.tag || ""))).join("");
  void renderLiveQuestionMath(root);
  root.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const card = target?.closest(".student-live-card");
    if (!card) return;
    const choice = target.closest("[data-live-choice]");
    if (choice) {
      card.querySelectorAll("[data-live-choice]").forEach((btn) => {
        btn.classList.toggle("is-selected", btn === choice);
      });
      return;
    }
    if (!target.closest("[data-live-submit]")) return;
    const answer = cardAnswer(card);
    if (!answer) return;
    form.elements.namedItem("question_key").value = card.getAttribute("data-question-key") || "";
    form.elements.namedItem("answer").value = answer;
    form.submit();
  });
}

mountCatchup();
