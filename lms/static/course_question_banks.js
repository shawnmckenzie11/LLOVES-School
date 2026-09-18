import { api, escapeHtml } from "/static/common.js";

const EMPTY_BANKS = "No banks imported yet.";
const TOAST_COPY = "Saved.";
const CHIP_COPY = "Edited in LMS";

const root = document.getElementById("catalog-root");
const classId = Number(root?.dataset.classId || 0);
const list = document.getElementById("catalog-list");
const detail = document.getElementById("bank-detail");
const toast = document.getElementById("bank-toast");
const removeDialog = document.getElementById("bank-remove-dialog");

/** @type {Record<string, unknown>[]} */
let banks = [];
/** @type {Record<string, unknown>[]} */
let questions = [];
/** @type {number | null} */
let selectedBankId = null;
let editMode = false;
/** @type {number | "new" | null} */
let openEditorId = null;
/** @type {number | "new" | null} */
let pendingRemoveId = null;
let toastTimer = 0;

const ITEM_TYPE_LABELS = {
  multiple_choice_question: "Multiple choice",
  true_false_question: "True / false",
  essay_question: "Essay",
  short_answer_question: "Short answer",
  numerical_question: "Numerical",
};

/**
 * Escape text for HTML interpolation.
 * @param {unknown} value
 */
function escapeText(value) {
  return escapeHtml(value);
}

/**
 * Human label for a stored Canvas item type.
 * @param {unknown} itemType
 */
function itemTypeLabel(itemType) {
  const key = String(itemType || "");
  return ITEM_TYPE_LABELS[key] || key.replace(/_/g, " ") || "Question";
}

/**
 * Show one calm save toast, then hide it.
 * @param {string} [message]
 */
function showToast(message) {
  if (!(toast instanceof HTMLElement)) return;
  toast.textContent = message || TOAST_COPY;
  toast.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toast.hidden = true;
  }, 2400);
}

/**
 * Return option rows for the QuestionEditor.
 * @param {Record<string, unknown>} question
 */
function editorOptions(question) {
  const choices = Array.isArray(question.choices) ? question.choices : [];
  const rows = choices.map((choice, index) => ({
    text: String(choice?.text || "").trim(),
    correct: Boolean(choice?.correct),
    letter: String.fromCharCode(65 + index),
  }));
  while (rows.length < 2) {
    rows.push({
      text: "",
      correct: rows.length === 0,
      letter: String.fromCharCode(65 + rows.length),
    });
  }
  if (!rows.some((row) => row.correct)) rows[0].correct = true;
  return rows;
}

/**
 * Collect QuestionEditor field values from one card.
 * @param {HTMLElement} card
 */
function readEditor(card) {
  const stem = String(
    card.querySelector("[data-bank-stem]") instanceof HTMLTextAreaElement
      ? card.querySelector("[data-bank-stem]").value
      : ""
  ).trim();
  const optionInputs = [...card.querySelectorAll("[data-bank-option]")];
  const options = optionInputs
    .map((input) => (input instanceof HTMLInputElement ? input.value.trim() : ""))
    .filter(Boolean);
  const checked = card.querySelector("[data-bank-correct]:checked");
  const correctIndex = checked instanceof HTMLInputElement ? Number(checked.value) : 0;
  const pointsInput = card.querySelector("[data-bank-points]");
  const pointsRaw =
    pointsInput instanceof HTMLInputElement ? pointsInput.value.trim() : "";
  return {
    stem_text: stem,
    options,
    correct_answer: String.fromCharCode(65 + (Number.isFinite(correctIndex) ? correctIndex : 0)),
    points: pointsRaw === "" ? null : Number(pointsRaw),
  };
}

/**
 * Render the left-hand bank list.
 */
function paintBanks() {
  if (!list) return;
  if (!banks.length) {
    list.innerHTML = `<p class="hint">${escapeText(EMPTY_BANKS)}</p>`;
    return;
  }
  let html = '<ul class="nav-list bank-nav-list">';
  for (const bank of banks) {
    const n = Number(bank.question_count || 0);
    const on = Number(bank.id) === Number(selectedBankId) ? " is-on" : "";
    html += `<li>
      <button type="button" class="bank-nav-btn${on}" data-bank-id="${Number(bank.id)}">
        ${escapeText(bank.title || "Untitled bank")}
        <span class="hint">(${n} question${n === 1 ? "" : "s"})</span>
      </button>
    </li>`;
  }
  html += "</ul>";
  list.innerHTML = html;
}

/**
 * Render one read-only peek for an expanded question.
 * @param {Record<string, unknown>} question
 */
function peekHtml(question) {
  const choices = Array.isArray(question.choices) ? question.choices : [];
  const choiceBits = choices
    .map((choice) => {
      const mark = choice.correct ? "✓" : "○";
      return `<li class="${choice.correct ? "is-correct" : ""}">${mark} ${
        choice.html || escapeText(choice.text || "")
      }</li>`;
    })
    .join("");
  const answers = Array.isArray(question.correct_answers)
    ? question.correct_answers.filter(Boolean)
    : [];
  return `<div class="bank-q-peek">
    <div class="bank-q-stem live-question-html">${question.stem_html || escapeText(question.stem_plain || "")}</div>
    ${choiceBits ? `<ul class="bank-q-choices">${choiceBits}</ul>` : ""}
    ${
      !choiceBits && answers.length
        ? `<p class="hint compact">Accepted: ${escapeText(answers.join(", "))}</p>`
        : ""
    }
  </div>`;
}

/**
 * Render the in-place QuestionEditor for one question.
 * @param {Record<string, unknown>} question
 * @param {boolean} isNew
 */
function editorHtml(question, isNew) {
  const options = editorOptions(question);
  const mc = String(question.item_type || "").includes("multiple_choice")
    || String(question.item_type || "") === "true_false_question"
    || isNew;
  const optionFields = mc
    ? `<fieldset class="bank-q-options">
        <legend>Options</legend>
        ${options
          .map(
            (row, index) => `<label class="bank-q-option">
              <input type="radio" name="bank-correct-${escapeText(question.id ?? "new")}" data-bank-correct value="${index}"${
                row.correct ? " checked" : ""
              }>
              <span>${row.letter}</span>
              <input type="text" data-bank-option maxlength="240" value="${escapeText(row.text)}">
            </label>`
          )
          .join("")}
      </fieldset>`
    : "";
  return `<form class="bank-q-editor" data-bank-editor="${escapeText(question.id ?? "new")}">
    <label>Stem
      <textarea data-bank-stem rows="4" required>${escapeText(question.stem_plain || "")}</textarea>
    </label>
    ${optionFields}
    <label>Points
      <input data-bank-points type="number" min="0" step="0.5" value="${escapeText(question.points ?? 1)}">
    </label>
    <p class="row-actions">
      <button type="submit">Save</button>
      ${
        !isNew
          ? `<button type="button" class="danger" data-bank-remove="${Number(question.id)}">Remove</button>`
          : `<button type="button" class="secondary" data-bank-cancel-new>Cancel</button>`
      }
    </p>
  </form>`;
}

/**
 * Render the selected bank's questions (browse or edit).
 */
function paintDetail() {
  if (!(detail instanceof HTMLElement)) return;
  if (!selectedBankId) {
    detail.innerHTML = `<p class="hint">Select a bank to browse questions.</p>`;
    return;
  }
  const bank = banks.find((row) => Number(row.id) === Number(selectedBankId));
  const title = String(bank?.title || "Question bank");
  const modeControls = editMode
    ? `<button type="button" data-bank-done>Done</button>
       <button type="button" class="secondary" data-bank-cancel>Cancel</button>`
    : `<button type="button" data-bank-edit>Edit bank</button>`;
  const addBtn = editMode
    ? `<button type="button" class="secondary" data-bank-add>Add question</button>`
    : "";
  let body = "";
  if (!questions.length && openEditorId !== "new") {
    body = `<p class="hint">This bank has no questions yet.</p>`;
  } else {
    body = '<ul class="bank-q-list">';
    for (const question of questions) {
      const qid = Number(question.id);
      const expanded = detail.querySelector(`[data-bank-q="${qid}"]`)?.classList.contains("is-open");
      const editing = editMode && openEditorId === qid;
      const chip = question.edited_in_lms
        ? `<span class="bank-q-chip">${escapeText(CHIP_COPY)}</span>`
        : "";
      body += `<li class="bank-q-row${expanded && !editing ? " is-open" : ""}" data-bank-q="${qid}">
        <button type="button" class="bank-q-stem-btn" data-bank-expand="${qid}">
          <span class="bank-q-preview">${escapeText(question.stem_preview || question.stem_plain || "Untitled")}</span>
          <span class="hint">${escapeText(itemTypeLabel(question.item_type))}</span>
          ${chip}
        </button>
        ${
          editMode
            ? `<button type="button" class="secondary compact" data-bank-open-editor="${qid}">Edit</button>`
            : ""
        }
        ${editing ? editorHtml(question, false) : `<div class="bank-q-peek-wrap">${peekHtml(question)}</div>`}
      </li>`;
    }
    if (editMode && openEditorId === "new") {
      body += `<li class="bank-q-row is-editing" data-bank-q="new">
        ${editorHtml({ id: "new", item_type: "multiple_choice_question", choices: [
          { text: "", correct: true },
          { text: "", correct: false },
          { text: "", correct: false },
          { text: "", correct: false },
        ], points: 1 }, true)}
      </li>`;
    }
    body += "</ul>";
  }
  detail.innerHTML = `<header class="bank-detail-head">
    <div>
      <h2>${escapeText(title)}</h2>
      <p class="hint compact">${questions.length} question${questions.length === 1 ? "" : "s"}</p>
    </div>
    <div class="bank-mode-controls">${modeControls}${addBtn}</div>
  </header>${body}`;
}

/**
 * Load the selected bank's questions from the staff API.
 */
async function loadQuestions() {
  if (!selectedBankId) {
    questions = [];
    paintDetail();
    return;
  }
  const data = await api(`/api/staff/class/${classId}/question-bank/${selectedBankId}`);
  questions = Array.isArray(data.questions) ? data.questions : [];
  paintDetail();
}

/**
 * Load imported banks for this class.
 */
async function loadBanks() {
  const data = await api(`/api/staff/class/${classId}/components/question-banks`);
  banks = Array.isArray(data.items) ? data.items : [];
  if (!banks.length) {
    selectedBankId = null;
    questions = [];
    paintBanks();
    if (detail instanceof HTMLElement) {
      detail.innerHTML = `<p class="hint">${escapeText(data.message || EMPTY_BANKS)}</p>`;
    }
    return;
  }
  if (!banks.some((row) => Number(row.id) === Number(selectedBankId))) {
    selectedBankId = Number(banks[0].id);
  }
  paintBanks();
  await loadQuestions();
}

/**
 * Persist the open QuestionEditor (PATCH or POST).
 * @param {HTMLFormElement} form
 */
async function saveEditor(form) {
  const body = readEditor(form);
  if (openEditorId === "new") {
    const saved = await api(
      `/api/staff/class/${classId}/question-bank/${selectedBankId}/questions`,
      { method: "POST", body: JSON.stringify(body) }
    );
    openEditorId = null;
    await loadBanks();
    showToast(TOAST_COPY);
    return saved;
  }
  const saved = await api(
    `/api/staff/class/${classId}/question-bank/${selectedBankId}/questions/${Number(openEditorId)}`,
    { method: "PATCH", body: JSON.stringify(body) }
  );
  openEditorId = null;
  await loadBanks();
  showToast(TOAST_COPY);
  return saved;
}

/**
 * Ask for delete confirmation with the in-page modal, not window.confirm.
 * @param {number} questionId
 */
function confirmRemove(questionId) {
  pendingRemoveId = questionId;
  if (removeDialog instanceof HTMLDialogElement) {
    if (typeof removeDialog.showModal === "function") {
      removeDialog.showModal();
      return;
    }
  }
}

/**
 * Delete the pending question after the modal accepts.
 */
async function removePending() {
  const questionId = Number(pendingRemoveId);
  pendingRemoveId = null;
  if (!questionId || !selectedBankId) return;
  await api(
    `/api/staff/class/${classId}/question-bank/${selectedBankId}/questions/${questionId}`,
    { method: "DELETE" }
  );
  if (openEditorId === questionId) openEditorId = null;
  await loadBanks();
}

list?.addEventListener("click", (event) => {
  const btn =
    event.target instanceof Element ? event.target.closest("[data-bank-id]") : null;
  if (!(btn instanceof HTMLElement)) return;
  selectedBankId = Number(btn.dataset.bankId || 0) || null;
  editMode = false;
  openEditorId = null;
  paintBanks();
  loadQuestions().catch((err) => {
    if (detail instanceof HTMLElement) {
      detail.textContent = err instanceof Error ? err.message : String(err);
    }
  });
});

detail?.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  if (target.closest("[data-bank-edit]")) {
    editMode = true;
    openEditorId = null;
    paintDetail();
    return;
  }
  if (target.closest("[data-bank-done]") || target.closest("[data-bank-cancel]")) {
    editMode = false;
    openEditorId = null;
    paintDetail();
    return;
  }
  if (target.closest("[data-bank-add]")) {
    openEditorId = "new";
    paintDetail();
    detail.querySelector("[data-bank-stem]")?.focus();
    return;
  }
  if (target.closest("[data-bank-cancel-new]")) {
    openEditorId = null;
    paintDetail();
    return;
  }
  const editorBtn = target.closest("[data-bank-open-editor]");
  if (editorBtn instanceof HTMLElement) {
    openEditorId = Number(editorBtn.getAttribute("data-bank-open-editor") || 0) || null;
    paintDetail();
    return;
  }
  const removeBtn = target.closest("[data-bank-remove]");
  if (removeBtn instanceof HTMLElement) {
    event.preventDefault();
    confirmRemove(Number(removeBtn.getAttribute("data-bank-remove") || 0));
    return;
  }
  const expandBtn = target.closest("[data-bank-expand]");
  if (expandBtn instanceof HTMLElement) {
    const qid = Number(expandBtn.getAttribute("data-bank-expand") || 0);
    if (editMode && openEditorId === qid) return;
    const row = expandBtn.closest("[data-bank-q]");
    row?.classList.toggle("is-open");
  }
});

detail?.addEventListener("submit", (event) => {
  const form =
    event.target instanceof HTMLFormElement && event.target.matches("[data-bank-editor]")
      ? event.target
      : null;
  if (!form) return;
  event.preventDefault();
  saveEditor(form).catch((err) => {
    const note = document.createElement("p");
    note.className = "error compact";
    note.textContent = err instanceof Error ? err.message : String(err);
    form.appendChild(note);
  });
});

removeDialog?.addEventListener("close", () => {
  if (!(removeDialog instanceof HTMLDialogElement)) return;
  if (removeDialog.returnValue === "ok") {
    removePending().catch((err) => {
      if (detail instanceof HTMLElement) {
        const note = document.createElement("p");
        note.className = "error compact";
        note.textContent = err instanceof Error ? err.message : String(err);
        detail.prepend(note);
      }
    });
  } else {
    pendingRemoveId = null;
  }
});

loadBanks().catch((err) => {
  if (list) list.textContent = err instanceof Error ? err.message : String(err);
});
