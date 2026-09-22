import { api, escapeHtml, formatQuestionHtml, renderLiveQuestionMath } from "/static/common.js";

const root = document.getElementById("catalog-root");
const classId = Number(root?.dataset.classId || 0);
const EMPTY_BANKS = root?.dataset.emptyBanks || "No banks imported yet.";
const EDIT_BANK = root?.dataset.editBank || "Edit bank";
const DONE_LABEL = root?.dataset.done || "Done";
const CANCEL_LABEL = root?.dataset.cancel || "Cancel";
const CHIP_COPY = root?.dataset.editedInLms || "Edited in LMS";
const TOAST_COPY = root?.dataset.saveToast || "Saved to this course’s copy.";
const EQ_ERROR = "Couldn't render this equation — check the TeX.";
const list = document.getElementById("catalog-list");
const detail = document.getElementById("bank-detail");
const toast = document.getElementById("bank-toast");
const removeDialog = document.getElementById("bank-remove-dialog");
const scopeEl = document.getElementById("live-bank-scope");
const kindEl = document.getElementById("live-bank-kind");
const searchEl = document.getElementById("live-bank-search");

/** @type {Record<string, unknown>[]} */
let items = [];
/** @type {Record<string, unknown> | null} */
let selected = null;
/** @type {Record<string, unknown> | null} */
let editorQuestion = null;
let editMode = false;
/** @type {number | "new" | null} */
let openEditorId = null;
/** @type {number | null} */
let pendingRemoveId = null;
let toastTimer = 0;
let searchTimer = 0;
let loadToken = 0;

/**
 * Escape text for HTML interpolation.
 * @param {unknown} value
 */
function escapeText(value) {
  return escapeHtml(value);
}

/**
 * True when Import would treat the row as a warmup.
 * @param {Record<string, unknown> | null | undefined} item
 */
function isWarmup(item) {
  const kind = String(item?.kind || item?.payload?.kind || "").toLowerCase();
  if (kind === "warmup") return true;
  const tags = item?.payload?.tags;
  return Array.isArray(tags) && tags.some((tag) => String(tag).toLowerCase() === "warmup");
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
 * Current bank scope token (``M1``–``M8`` or ``course``).
 */
function currentScope() {
  const value = scopeEl instanceof HTMLSelectElement ? scopeEl.value : "M1";
  return String(value || "M1");
}

/**
 * Kind query shared with Import. Empty is Process (warmup hidden).
 */
function currentKind() {
  return kindEl instanceof HTMLSelectElement ? String(kindEl.value || "") : "";
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
      correct: !isWarmup(question) && rows.length === 0,
      letter: String.fromCharCode(65 + rows.length),
    });
  }
  if (!isWarmup(question) && !rows.some((row) => row.correct)) rows[0].correct = true;
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
  const correctIndex = checked instanceof HTMLInputElement ? Number(checked.value) : -1;
  const pointsInput = card.querySelector("[data-bank-points]");
  const pointsRaw =
    pointsInput instanceof HTMLInputElement ? pointsInput.value.trim() : "";
  const typeEl = card.querySelector("[data-bank-new-type]");
  const type = typeEl instanceof HTMLSelectElement ? typeEl.value : "mc";
  const numericEl = card.querySelector("[data-bank-numeric]");
  const numeric =
    numericEl instanceof HTMLInputElement ? numericEl.value.trim() : "";
  return {
    stem_text: stem,
    options,
    correct_answer:
      type === "numeric"
        ? numeric
        : correctIndex >= 0
          ? String.fromCharCode(65 + correctIndex)
          : "",
    points: pointsRaw === "" ? null : Number(pointsRaw),
    type,
  };
}

/**
 * Render one read-only peek for the selected live-bank row.
 * @param {Record<string, unknown>} item
 */
function peekHtml(item) {
  const options = Array.isArray(item.options) ? item.options : [];
  const choiceBits = options
    .map((choice) => {
      const text = typeof choice === "string" ? choice : String(choice?.text || "");
      return `<li>${escapeText(text)}</li>`;
    })
    .join("");
  const key = String(item.correct_answer || "").trim();
  const warmup = isWarmup(item);
  return `<div class="bank-q-peek">
    <div class="bank-q-stem live-question-html">${formatQuestionHtml(String(item.text || item.question_title || ""))}</div>
    ${choiceBits ? `<ul class="bank-q-choices">${choiceBits}</ul>` : ""}
    ${
      warmup
        ? `<p class="hint compact">Warmup — no answer key</p>`
        : key
          ? `<p class="hint compact">Answer ${escapeText(key)}</p>`
          : ""
    }
  </div>`;
}

/**
 * Render the in-place QuestionEditor for one stored question.
 * @param {Record<string, unknown>} question
 * @param {boolean} isNew
 */
function editorHtml(question, isNew) {
  const warmup = isWarmup(question);
  const newType = String(question.type || "mc");
  const mc =
    isNew
      ? newType === "mc"
      : String(question.item_type || "").includes("multiple_choice") ||
        String(question.item_type || "") === "true_false_question" ||
        (Array.isArray(question.choices) && question.choices.length > 0 && !warmup) ||
        (warmup && Array.isArray(question.choices) && question.choices.length > 0);
  const options = mc ? editorOptions(question) : [];
  const optionFields = mc
    ? `<fieldset class="bank-q-options">
        <legend>${warmup ? "Options — warmup has no key" : "Options"}</legend>
        ${options
          .map((row, index) => {
            const keyControl = warmup
              ? `<span>${row.letter}</span>`
              : `<input type="radio" name="bank-correct-${escapeText(question.id ?? "new")}" data-bank-correct value="${index}"${
                  row.correct ? " checked" : ""
                }><span>${row.letter}</span>`;
            return `<label class="bank-q-option">${keyControl}
              <input type="text" data-bank-option maxlength="240" value="${escapeText(row.text)}">
            </label>`;
          })
          .join("")}
      </fieldset>`
    : "";
  const numericFields =
    isNew && newType === "numeric"
      ? `<label>Correct answer
          <input data-bank-numeric type="text" inputmode="decimal" required>
        </label>`
      : "";
  const typeFields = isNew
    ? `<label>Type
        <select data-bank-new-type aria-label="Type">
          <option value="mc"${newType === "mc" ? " selected" : ""}>mc</option>
          <option value="numeric"${newType === "numeric" ? " selected" : ""}>numeric</option>
          <option value="poll"${newType === "poll" ? " selected" : ""}>poll</option>
        </select>
      </label>`
    : "";
  return `<form class="bank-q-editor" data-bank-editor="${escapeText(question.id ?? "new")}">
    ${typeFields}
    <label>Stem
      <textarea data-bank-stem rows="4" required>${escapeText(question.stem_plain || question.text || "")}</textarea>
    </label>
    <div class="bank-q-eq-preview live-question-html" data-bank-eq-preview></div>
    <p class="error compact" hidden data-bank-eq-error>${escapeText(EQ_ERROR)}</p>
    ${optionFields}
    ${numericFields}
    <label>Points
      <input data-bank-points type="number" min="0" step="0.5" value="${escapeText(question.points ?? 1)}">
    </label>
    <p class="row-actions">
      <button type="submit">Save</button>
      ${
        !isNew
          ? `<button type="button" class="danger" data-bank-remove="${Number(question.id)}">Remove</button>`
          : `<button type="button" class="secondary" data-bank-cancel-new>${escapeText(CANCEL_LABEL)}</button>`
      }
    </p>
  </form>`;
}

/**
 * Paint Browse or Edit for the selected live-bank row.
 */
function paintDetail() {
  if (!(detail instanceof HTMLElement)) return;
  const modeControls = editMode
    ? `<button type="button" class="secondary" data-bank-add>Add question</button>`
    : "";
  if (editMode && openEditorId === "new") {
    detail.innerHTML = `<header class="bank-detail-head">
      <div><h2>Add question</h2><p class="hint compact">${escapeText(EDIT_BANK)}</p></div>
      <div class="bank-mode-controls">${modeControls}</div>
    </header>${editorHtml({ id: "new", type: "mc", choices: [], points: 1 }, true)}`;
    void renderLiveQuestionMath(detail);
    return;
  }
  if (!selected) {
    detail.innerHTML = `<p class="hint">${escapeText(EMPTY_BANKS)}</p>`;
    return;
  }
  const title = String(selected.question_title || selected.text || "Question");
  const chip = selected.edited_in_lms
    ? `<span class="bank-q-chip">${escapeText(CHIP_COPY)}</span>`
    : "";
  const body =
    editMode && editorQuestion
      ? editorHtml(editorQuestion, false)
      : editMode
        ? `<p class="hint">Loading editor…</p>`
        : peekHtml(selected);
  detail.innerHTML = `<header class="bank-detail-head">
    <div>
      <h2>${escapeText(title)}</h2>
      <p class="hint compact">${escapeText(String(selected.bank_title || "Live bank"))} ${chip}</p>
    </div>
    <div class="bank-mode-controls">${modeControls}</div>
  </header>${body}`;
  void renderLiveQuestionMath(detail);
  detail.querySelectorAll("[data-bank-editor]").forEach((form) => {
    if (form instanceof HTMLElement) void paintEditorMathPreview(form);
  });
}

/**
 * Render the browse list for the current scope, kind, and search.
 */
function paintList() {
  if (!list) return;
  if (!items.length) {
    list.innerHTML = `<p class="hint">${escapeText(EMPTY_BANKS)}</p>`;
    return;
  }
  let html = '<ul class="nav-list bank-nav-list">';
  for (const item of items) {
    const qid = Number(item.question_id || 0);
    const on = Number(selected?.question_id) === qid ? " is-on" : "";
    const previewHtml = String(item.stem_preview_html || "").trim();
    const preview = String(item.text || item.question_title || "Untitled");
    const kind = isWarmup(item) ? "Warmup" : String(item.type || "mc");
    const previewMarkup = previewHtml
      ? `<span class="bank-q-preview live-question-html">${previewHtml}</span>`
      : `<span class="bank-q-preview">${escapeText(preview)}</span>`;
    html += `<li>
      <button type="button" class="bank-nav-btn${on}" data-live-bank-id="${qid}">
        ${previewMarkup}
        <span class="hint">${escapeText(kind)}</span>
      </button>
    </li>`;
  }
  html += "</ul>";
  list.innerHTML = html;
}

/**
 * Sync the Browse | Edit pressed state.
 */
function paintMode() {
  const browse = document.getElementById("live-bank-browse");
  const edit = document.getElementById("live-bank-edit");
  if (browse instanceof HTMLButtonElement) {
    browse.setAttribute("aria-pressed", editMode ? "false" : "true");
    browse.title = DONE_LABEL;
  }
  if (edit instanceof HTMLButtonElement) {
    edit.setAttribute("aria-pressed", editMode ? "true" : "false");
    edit.setAttribute("aria-label", EDIT_BANK);
  }
}

/**
 * Return True when ``$...$`` delimiters are unbalanced.
 * @param {string} text
 */
function hasUnmatchedDollar(text) {
  let count = 0;
  const raw = String(text || "");
  for (let index = 0; index < raw.length; index += 1) {
    if (raw[index] === "$" && raw[index - 1] !== "\\") count += 1;
  }
  return count % 2 === 1;
}

/**
 * Typeset the in-editor stem with the same KaTeX path as browse/student.
 * @param {HTMLElement} form
 */
async function paintEditorMathPreview(form) {
  const field = form.querySelector("[data-bank-stem]");
  const preview = form.querySelector("[data-bank-eq-preview]");
  const error = form.querySelector("[data-bank-eq-error]");
  if (!(preview instanceof HTMLElement)) return;
  const stem = field instanceof HTMLTextAreaElement ? field.value : "";
  const options = [...form.querySelectorAll("[data-bank-option]")]
    .map((input) => (input instanceof HTMLInputElement ? input.value.trim() : ""))
    .filter(Boolean);
  const optionHtml = options
    .map((text, index) => {
      const letter = String.fromCharCode(65 + index);
      return `<div class="bank-q-eq-choice">${letter}. ${formatQuestionHtml(text)}</div>`;
    })
    .join("");
  preview.innerHTML = `${formatQuestionHtml(stem)}${optionHtml}`;
  await renderLiveQuestionMath(preview);
  if (error instanceof HTMLElement) {
    const unmatched = [stem, ...options].some(hasUnmatchedDollar);
    const leftover = /(?<!\\)\$/.test(preview.textContent || "");
    const failed = unmatched || leftover || Boolean(preview.querySelector(".katex-error"));
    error.hidden = !failed;
    if (!error.hidden) error.textContent = EQ_ERROR;
  }
}

/**
 * Load the staff editor row for the selected live-bank question.
 */
async function loadEditorQuestion() {
  editorQuestion = null;
  const bankId = Number(selected?.bank_id || 0);
  const questionId = Number(selected?.question_id || 0);
  if (!bankId || !questionId) {
    paintDetail();
    return;
  }
  const data = await api(`/api/staff/class/${classId}/question-bank/${bankId}`);
  const questions = Array.isArray(data.questions) ? data.questions : [];
  editorQuestion =
    questions.find((row) => Number(row.id) === questionId) || null;
  if (editorQuestion && isWarmup(selected)) {
    editorQuestion.kind = "warmup";
  }
  paintDetail();
}

/**
 * Load the live bank through the same mc-search Import uses.
 */
async function loadLiveBank() {
  const token = ++loadToken;
  const scope = currentScope();
  const kind = currentKind();
  const query = searchEl instanceof HTMLInputElement ? searchEl.value.trim() : "";
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (kind) params.set("kind", kind);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  let data;
  try {
    data = await api(
      `/api/staff/class/${classId}/module-banks/${encodeURIComponent(scope)}/mc-search${suffix}`
    );
  } catch (err) {
    if (token !== loadToken) return;
    items = [];
    selected = null;
    if (list) list.textContent = err instanceof Error ? err.message : String(err);
    if (detail instanceof HTMLElement) {
      detail.innerHTML = `<p class="hint">${escapeText(EMPTY_BANKS)}</p>`;
    }
    return;
  }
  if (token !== loadToken) return;
  items = Array.isArray(data.items) ? data.items : [];
  const keep = Number(selected?.question_id || 0);
  selected = items.find((row) => Number(row.question_id) === keep) || items[0] || null;
  openEditorId = null;
  paintList();
  paintMode();
  if (editMode && selected) {
    await loadEditorQuestion();
  } else {
    editorQuestion = null;
    paintDetail();
  }
}

/**
 * Persist the open QuestionEditor (PATCH or POST).
 * @param {HTMLFormElement} form
 */
async function saveEditor(form) {
  const body = readEditor(form);
  if (openEditorId === "new") {
    await api(`/api/staff/class/${classId}/live-bank/questions`, {
      method: "POST",
      body: JSON.stringify({
        ...body,
        bank_scope: currentScope(),
      }),
    });
    openEditorId = null;
    editMode = false;
    paintMode();
    await loadLiveBank();
    showToast(TOAST_COPY);
    return;
  }
  const bankId = Number(selected?.bank_id || editorQuestion?.bank_id || 0);
  const questionId = Number(openEditorId || selected?.question_id || 0);
  await api(
    `/api/staff/class/${classId}/question-bank/${bankId}/questions/${questionId}`,
    { method: "PATCH", body: JSON.stringify(body) }
  );
  openEditorId = null;
  await loadLiveBank();
  showToast(TOAST_COPY);
}

/**
 * Ask for delete confirmation with the in-page modal, not a browser confirm.
 * @param {number} questionId
 */
function confirmRemove(questionId) {
  pendingRemoveId = questionId;
  if (removeDialog instanceof HTMLDialogElement && typeof removeDialog.showModal === "function") {
    removeDialog.showModal();
  }
}

/**
 * Delete the pending question after the modal accepts.
 */
async function removePending() {
  const questionId = Number(pendingRemoveId);
  const bankId = Number(selected?.bank_id || editorQuestion?.bank_id || 0);
  pendingRemoveId = null;
  if (!questionId || !bankId) return;
  await api(
    `/api/staff/class/${classId}/question-bank/${bankId}/questions/${questionId}`,
    { method: "DELETE" }
  );
  if (openEditorId === questionId) openEditorId = null;
  selected = null;
  await loadLiveBank();
}

list?.addEventListener("click", (event) => {
  const btn =
    event.target instanceof Element ? event.target.closest("[data-live-bank-id]") : null;
  if (!(btn instanceof HTMLElement)) return;
  const qid = Number(btn.dataset.liveBankId || 0);
  selected = items.find((row) => Number(row.question_id) === qid) || null;
  openEditorId = qid || null;
  paintList();
  if (editMode) {
    loadEditorQuestion().catch((err) => {
      if (detail instanceof HTMLElement) {
        detail.textContent = err instanceof Error ? err.message : String(err);
      }
    });
  } else {
    paintDetail();
  }
});

document.getElementById("live-bank-browse")?.addEventListener("click", () => {
  editMode = false;
  openEditorId = null;
  editorQuestion = null;
  paintMode();
  paintDetail();
});

document.getElementById("live-bank-edit")?.addEventListener("click", () => {
  editMode = true;
  openEditorId = Number(selected?.question_id || 0) || null;
  paintMode();
  loadEditorQuestion().catch((err) => {
    if (detail instanceof HTMLElement) {
      detail.textContent = err instanceof Error ? err.message : String(err);
    }
  });
});

scopeEl?.addEventListener("change", () => {
  selected = null;
  loadLiveBank().catch((err) => {
    if (list) list.textContent = err instanceof Error ? err.message : String(err);
  });
});

kindEl?.addEventListener("change", () => {
  selected = null;
  loadLiveBank().catch((err) => {
    if (list) list.textContent = err instanceof Error ? err.message : String(err);
  });
});

searchEl?.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    loadLiveBank().catch((err) => {
      if (list) list.textContent = err instanceof Error ? err.message : String(err);
    });
  }, 200);
});

detail?.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  if (target.closest("[data-bank-add]")) {
    openEditorId = "new";
    paintDetail();
    detail.querySelector("[data-bank-stem]")?.focus();
    return;
  }
  if (target.closest("[data-bank-cancel-new]") || target.closest("[data-bank-done]")) {
    openEditorId = null;
    editMode = false;
    paintMode();
    paintDetail();
    return;
  }
  const removeBtn = target.closest("[data-bank-remove]");
  if (removeBtn instanceof HTMLElement) {
    event.preventDefault();
    confirmRemove(Number(removeBtn.getAttribute("data-bank-remove") || 0));
  }
});

detail?.addEventListener("change", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!(target instanceof HTMLSelectElement) || !target.matches("[data-bank-new-type]")) return;
  const form = target.closest("[data-bank-editor]");
  if (!(form instanceof HTMLElement)) return;
  const draft = readEditor(form);
  detail.innerHTML = `<header class="bank-detail-head"><div><h2>Add question</h2></div></header>${editorHtml(
    { id: "new", type: draft.type, stem_plain: draft.stem_text, points: draft.points ?? 1, choices: [] },
    true
  )}`;
});

detail?.addEventListener("input", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target || !target.matches("[data-bank-stem], [data-bank-option]")) return;
  const form = target.closest("[data-bank-editor]");
  if (form instanceof HTMLElement) void paintEditorMathPreview(form);
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

paintMode();
loadLiveBank().catch((err) => {
  if (list) list.textContent = err instanceof Error ? err.message : String(err);
});
