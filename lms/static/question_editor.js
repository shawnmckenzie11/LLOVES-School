/**
 * Shared Add New / Edit question dialog for Run Live Class and Question banks.
 *
 * Live Add New posts through the page's submit hook. Question banks lock
 * Save to bank and either POST a new bank row or PATCH with from_editor.
 */
import { api, escapeHtml, renderLiveQuestionMath } from "/static/common.js";
import { questionPointsValue } from "/static/question_points.js";

const EQ_RENDER_ERROR = "Couldn't render this equation — check the TeX.";

/** @type {{classId: () => number, submitAdd: ((body: Record<string, unknown>) => Promise<void>) | null, submitBankAdd: ((body: Record<string, unknown>) => Promise<void>) | null, onSaved: (() => void) | null, onRemoved: (() => void) | null}} */
let hooks = {
  classId: () => 0,
  submitAdd: null,
  submitBankAdd: null,
  onSaved: null,
  onRemoved: null,
};

/** @type {"add" | "edit"} */
let mode = "add";
let lockSave = false;
let scopeKnown = true;
let defaultsDirty = false;
let scopeDirty = false;
let initialScope = "M1";
/** @type {string} */
let keptImageUrl = "";
/** @type {Record<string, unknown> | null} */
let currentQuestion = null;
/** @type {Record<string, unknown>[]} */
let navItems = [];
let navIndex = 0;
/** MCK-172: Answer order as rank row numbers (0–5), right order first. */
let rankKeyRows = [];

const $ = (id) => document.getElementById(id);

/**
 * Register the page that hosts the dialog.
 *
 * The live page supplies ``submitAdd``. The bank page supplies
 * ``submitBankAdd`` for a locked Save to bank.
 *
 * @param {Partial<typeof hooks>} next
 */
export function configureQuestionEditor(next) {
  hooks = { ...hooks, ...next };
}

/**
 * Strip wrapping dollar signs so stored TeX matches the KaTeX span helper.
 * @param {unknown} raw
 * @returns {string}
 */
function stripEquationLatex(raw) {
  return String(raw || "")
    .trim()
    .replace(/^\$+|\$+$/g, "")
    .trim();
}

/**
 * Render optional LaTeX in the equation preview.
 * @param {string} latex
 * @returns {string}
 */
function equationPreviewHtml(latex) {
  const raw = stripEquationLatex(latex);
  if (!raw) return "";
  return `<p class="live-question-equation"><span class="math-latex" data-latex="${escapeHtml(raw)}"></span></p>`;
}

/**
 * Show MC, numeric, or rank fields for the selected type.
 */
function syncTypeFields() {
  const selected = document.querySelector('input[name="live-add-q-type"]:checked');
  const kind = String(selected?.value || "mc").toLowerCase();
  const mc = $("live-add-q-mc-fields");
  const numeric = $("live-add-q-numeric-fields");
  const rank = $("live-add-q-rank-fields");
  if (mc instanceof HTMLElement) mc.hidden = kind !== "mc";
  if (numeric instanceof HTMLElement) numeric.hidden = kind !== "numeric";
  if (rank instanceof HTMLElement) rank.hidden = kind !== "rank";
  syncRankOptionRows();
}

/**
 * Show the first three rank rows and keep remove disabled at the minimum.
 */
function syncRankOptionRows() {
  const rows = [...document.querySelectorAll("#live-add-q-rank-list .live-add-rank-row")];
  const visible = rows.filter((row) => row instanceof HTMLElement && !row.hidden);
  rows.forEach((row) => {
    const button = row.querySelector("[data-rank-remove]");
    if (button instanceof HTMLButtonElement) button.disabled = visible.length <= 3;
  });
  const add = $("live-add-q-rank-add");
  if (add instanceof HTMLButtonElement) add.disabled = visible.length >= 6;
  syncRankKeyList();
}

/**
 * Rank rows that are shown and filled, in authored order.
 * @returns {number[]}
 */
function filledRankRows() {
  return [...document.querySelectorAll("#live-add-q-rank-list .live-add-rank-row")]
    .map((row, index) => ({ row, index }))
    .filter(
      ({ row }) =>
        row instanceof HTMLElement &&
        !row.hidden &&
        String(row.querySelector("input")?.value || "").trim()
    )
    .map(({ index }) => index);
}

/**
 * Keep the Answer order list in step with the options.
 */
function syncRankKeyList() {
  const on = $("live-add-q-rank-key-on");
  const box = $("live-add-q-rank-key");
  const list = $("live-add-q-rank-key-list");
  const enabled = on instanceof HTMLInputElement && on.checked;
  if (box instanceof HTMLElement) box.hidden = !enabled;
  const filled = filledRankRows();
  rankKeyRows = [
    ...rankKeyRows.filter((row) => filled.includes(row)),
    ...filled.filter((row) => !rankKeyRows.includes(row)),
  ];
  if (!(list instanceof HTMLElement) || !enabled) return;
  list.innerHTML = rankKeyRows
    .map((row) => {
      const label = String($(`live-add-q-rank-${row}`)?.value || "").trim();
      return `<li draggable="true" tabindex="0" data-rank-key-row="${row}" aria-keyshortcuts="Alt+ArrowUp Alt+ArrowDown">${escapeHtml(label)}</li>`;
    })
    .join("");
}

/**
 * Move one Answer order row and repaint.
 * @param {number} row Rank row number being moved.
 * @param {number} to Target position in the list.
 */
function moveRankKeyRow(row, to) {
  const from = rankKeyRows.indexOf(row);
  if (from < 0) return;
  const next = rankKeyRows.filter((item) => item !== row);
  next.splice(Math.max(0, Math.min(next.length, to)), 0, row);
  rankKeyRows = next;
  syncRankKeyList();
  const moved = document.querySelector(`#live-add-q-rank-key-list [data-rank-key-row="${row}"]`);
  if (moved instanceof HTMLElement) moved.focus();
}

/**
 * Answer order as option indices, or null when unset.
 * @param {string[]} options
 * @returns {number[] | null}
 */
function rankKeyForSubmit(options) {
  const on = $("live-add-q-rank-key-on");
  if (!(on instanceof HTMLInputElement) || !on.checked) return null;
  const filled = filledRankRows();
  if (filled.length !== options.length) return null;
  return rankKeyRows.map((row) => filled.indexOf(row));
}

/**
 * Insert a LaTeX snippet into the equation field.
 * @param {string} snippet
 */
function insertLatex(snippet) {
  const field = $("live-add-q-equation");
  if (!(field instanceof HTMLTextAreaElement)) return;
  const start = field.selectionStart ?? field.value.length;
  const end = field.selectionEnd ?? field.value.length;
  const token = String(snippet || "");
  field.value = `${field.value.slice(0, start)}${token}${field.value.slice(end)}`;
  const cursor = start + token.length;
  field.focus();
  field.setSelectionRange(cursor, cursor);
  paintEquationPreview();
}

/**
 * Typeset the equation preview with the same KaTeX helper as live cards.
 * @returns {Promise<void>}
 */
async function paintEquationPreview() {
  const field = $("live-add-q-equation");
  const preview = $("live-add-q-equation-preview");
  if (!(preview instanceof HTMLElement)) return;
  const latex = stripEquationLatex(field instanceof HTMLTextAreaElement ? field.value : "");
  if (!latex) {
    preview.innerHTML = `<p class="hint compact">No equation yet.</p>`;
    return;
  }
  preview.innerHTML = equationPreviewHtml(latex);
  await renderLiveQuestionMath(preview);
  if (preview.querySelector(".katex-error")) {
    preview.innerHTML = `<p class="error compact">${EQ_RENDER_ERROR}</p>`;
  }
}

/**
 * Show Bank scope while Save to bank is on, or whenever the bank page locks it.
 */
function syncBankFields() {
  const save = $("live-add-q-save-bank");
  const scope = $("live-add-q-bank-scope");
  const select = $("live-add-q-bank-scope-select");
  const hint = $("live-add-q-bank-scope-hint");
  const enabled = lockSave || mode === "edit" || (save instanceof HTMLInputElement && save.checked);
  if (scope instanceof HTMLElement) scope.hidden = !enabled;
  if (select instanceof HTMLSelectElement) select.disabled = !enabled;
  if (hint instanceof HTMLElement) hint.hidden = !enabled || scopeKnown || mode === "edit";
}

/**
 * Apply add or edit chrome. Prev/Next stay hidden while adding.
 */
function paintChrome() {
  const title = $("live-add-question-title");
  const note = $("live-add-question-note");
  const submit = $("live-add-q-submit");
  const remove = $("live-add-q-remove");
  const hint = $("live-add-q-save-hint");
  const save = $("live-add-q-save-bank");
  const editing = mode === "edit";
  if (title) {
    const label = editing ? "Edit question" : lockSave ? "Add question" : "Add New question";
    title.childNodes[0].textContent = `${label} `;
  }
  if (note instanceof HTMLElement) note.hidden = !editing;
  if (submit instanceof HTMLButtonElement) {
    submit.textContent = editing || lockSave ? "Save to bank" : "Add to this page";
  }
  if (remove instanceof HTMLElement) remove.hidden = !editing;
  if (hint instanceof HTMLElement) hint.hidden = !(editing || lockSave);
  document.querySelectorAll("[data-question-editor-prev], [data-question-editor-next]").forEach((button) => {
    if (button instanceof HTMLElement) button.hidden = !editing;
  });
  if (save instanceof HTMLInputElement) {
    if (editing || lockSave) {
      save.checked = true;
      save.disabled = true;
    } else {
      save.disabled = false;
    }
  }
  syncBankFields();
}

/**
 * Fill the form from a bank question. Programmatic values do not mark defaults dirty.
 * @param {Record<string, unknown>} question
 */
function fillQuestion(question) {
  currentQuestion = question;
  const kind = String(question.type || question.question_type || "mc").toLowerCase();
  const typeValue = ["mc", "numeric", "poll", "rank"].includes(kind) ? kind : "mc";
  const typeInput = document.querySelector(`input[name="live-add-q-type"][value="${typeValue}"]`);
  if (typeInput instanceof HTMLInputElement) typeInput.checked = true;
  const text = $("live-add-q-text");
  if (text instanceof HTMLTextAreaElement) {
    text.value = String(question.text || question.prompt || question.stem_plain || "");
  }
  const equation = $("live-add-q-equation");
  if (equation instanceof HTMLTextAreaElement) {
    equation.value = stripEquationLatex(question.equation_latex || question.equation || "");
  }
  const bankKind = $("live-add-q-bank-kind");
  if (bankKind instanceof HTMLSelectElement) {
    const stored = String(question.bank_kind || question.kind || "");
    bankKind.value = ["standard", "contest", "warmup"].includes(stored) ? stored : "";
  }
  const options = Array.isArray(question.options) ? question.options.map((opt) => String(opt ?? "")) : [];
  for (let index = 0; index < 4; index += 1) {
    const field = $(`live-add-q-opt-${index}`);
    if (field instanceof HTMLInputElement) field.value = typeValue === "mc" ? options[index] || "" : "";
  }
  const keyLetter = String(question.key || question.correct_answer || "A").trim().toUpperCase();
  const keyIndex = Math.max(0, Math.min(3, keyLetter.charCodeAt(0) - 65));
  const keyRadio = document.querySelector(`input[name="live-add-q-key"][value="${keyIndex}"]`);
  if (keyRadio instanceof HTMLInputElement) keyRadio.checked = true;
  const numeric = $("live-add-q-numeric-answer");
  if (numeric instanceof HTMLInputElement) {
    numeric.value = typeValue === "numeric" ? String(question.correct_answer || question.key || "") : "";
  }
  const tolerance = $("live-add-q-tolerance");
  if (tolerance instanceof HTMLInputElement) {
    tolerance.value = question.tolerance == null ? "0" : String(question.tolerance);
  }
  const toleranceKind = $("live-add-q-tolerance-kind");
  if (toleranceKind instanceof HTMLSelectElement) {
    toleranceKind.value = String(question.tolerance_kind || "absolute") === "percent" ? "percent" : "absolute";
  }
  fillRank(question, typeValue === "rank" ? options : []);
  keptImageUrl = String(question.image_url || "");
  const image = $("live-add-q-image");
  if (image instanceof HTMLInputElement) image.value = "";
  const response = String(question.live_response_mode || "individual");
  const responseInput = document.querySelector(
    `input[name="live-add-q-response"][value="${response === "group" ? "group" : "individual"}"]`
  );
  if (responseInput instanceof HTMLInputElement) responseInput.checked = true;
  const show = $("live-add-q-show-results");
  if (show instanceof HTMLInputElement) {
    show.checked = question.live_show_results == null ? true : Boolean(question.live_show_results);
  }
  const points = $("live-add-q-points");
  if (points instanceof HTMLInputElement) {
    points.value = questionPointsValue(question);
  }
  const select = $("live-add-q-bank-scope-select");
  const storedScope = String(question.bank_scope || "");
  if (select instanceof HTMLSelectElement) {
    select.value = /^M[0-8]$/.test(storedScope) || storedScope === "course" ? storedScope : initialScope;
    initialScope = select.value;
  }
  defaultsDirty = false;
  scopeDirty = false;
  syncTypeFields();
  paintEquationPreview();
}

/**
 * Load rank rows, keeping existing option ids on the inputs.
 * @param {Record<string, unknown>} question
 * @param {string[]} options
 */
function fillRank(question, options) {
  const rows = [...document.querySelectorAll("#live-add-q-rank-list .live-add-rank-row")];
  const rankOptions = Array.isArray(question.rank_options) ? question.rank_options : [];
  rows.forEach((row, index) => {
    if (!(row instanceof HTMLElement)) return;
    const input = row.querySelector("input");
    const source = rankOptions[index] || options[index] || "";
    const label = typeof source === "object" && source ? String(source.label || source.text || "") : String(source || "");
    const id = typeof source === "object" && source ? String(source.id || "") : "";
    if (input instanceof HTMLInputElement) {
      input.value = label;
      input.dataset.rankId = id;
    }
    row.hidden = index >= Math.max(3, options.length || rankOptions.length);
  });
  const on = $("live-add-q-rank-key-on");
  const key = Array.isArray(question.rank_key) ? question.rank_key.map((id) => String(id)) : [];
  if (on instanceof HTMLInputElement) on.checked = key.length > 0;
  rankKeyRows = [];
  if (key.length) {
    const idToRow = new Map();
    rows.forEach((row, index) => {
      const input = row.querySelector("input");
      const id = input instanceof HTMLInputElement ? String(input.dataset.rankId || "") : "";
      if (id) idToRow.set(id, index);
    });
    rankKeyRows = key.map((id) => idToRow.get(id)).filter((index) => index != null);
  }
  syncRankOptionRows();
}

/**
 * Reset to a blank Add New question.
 * @param {string} scope
 */
function fillBlank(scope) {
  currentQuestion = null;
  keptImageUrl = "";
  const form = $("live-add-question-form");
  if (form instanceof HTMLFormElement) form.reset();
  rankKeyRows = [];
  const mc = document.querySelector('input[name="live-add-q-type"][value="mc"]');
  if (mc instanceof HTMLInputElement) mc.checked = true;
  const select = $("live-add-q-bank-scope-select");
  const token = /^M[0-8]$/.test(scope) || scope === "course" ? scope : "M1";
  if (select instanceof HTMLSelectElement) {
    select.value = token;
    initialScope = token;
  }
  defaultsDirty = false;
  scopeDirty = false;
  syncTypeFields();
  paintChrome();
  paintEquationPreview();
}

/**
 * Open the shared editor.
 *
 * Add mode hides Prev/Next so Run Live Class still closes from the header.
 * Edit mode locks Save to bank and walks ``questions`` with the arrows and J/K.
 *
 * @param {{mode?: "add" | "edit", question?: Record<string, unknown> | null, questions?: Record<string, unknown>[], index?: number, scope?: string, scopeKnown?: boolean, lockSave?: boolean}} opts
 */
export function openQuestionEditor(opts = {}) {
  mode = opts.mode === "edit" ? "edit" : "add";
  lockSave = Boolean(opts.lockSave) || mode === "edit";
  scopeKnown = opts.scopeKnown !== false;
  navItems = Array.isArray(opts.questions) ? opts.questions : [];
  navIndex = Math.max(0, Number(opts.index) || 0);
  const err = $("live-add-q-error");
  if (err instanceof HTMLElement) {
    err.hidden = true;
    err.textContent = "";
  }
  const scope = String(opts.scope || "M1");
  if (mode === "edit" && opts.question) {
    initialScope = /^M[0-8]$/.test(scope) || scope === "course" ? scope : "M1";
    fillQuestion(opts.question);
    paintChrome();
  } else {
    fillBlank(scope);
  }
  const dialog = $("live-add-question-dialog");
  if (dialog instanceof HTMLDialogElement && !dialog.open) dialog.showModal();
}

/**
 * Close the editor without clearing the last fill.
 */
export function closeQuestionEditor() {
  const dialog = $("live-add-question-dialog");
  if (dialog instanceof HTMLDialogElement && dialog.open) dialog.close();
}

/**
 * Step to another question while the editor stays open.
 * @param {number} delta
 */
function stepQuestion(delta) {
  if (mode !== "edit" || navItems.length < 2) return;
  navIndex = Math.max(0, Math.min(navItems.length - 1, navIndex + delta));
  fillQuestion(navItems[navIndex] || {});
  paintChrome();
}

/**
 * Build the write body. Untouched live defaults and an unchanged scope are omitted.
 * @returns {Promise<Record<string, unknown>>}
 */
async function collectBody() {
  const selected = document.querySelector('input[name="live-add-q-type"]:checked');
  const kind = String(selected?.value || "mc").toLowerCase();
  const text = String($("live-add-q-text")?.value || "").trim();
  if (!text) throw new Error("Question text required.");
  const equation = stripEquationLatex($("live-add-q-equation")?.value || "");
  const imageInput = $("live-add-q-image");
  const imageFile = imageInput instanceof HTMLInputElement && imageInput.files?.[0] ? imageInput.files[0] : null;
  const imageUrl = imageFile ? await uploadImage(imageFile) : keptImageUrl;
  const saveBank = $("live-add-q-save-bank");
  const scope = $("live-add-q-bank-scope-select");
  const bankKind = $("live-add-q-bank-kind");
  /** @type {Record<string, unknown>} */
  const body = {
    type: kind,
    text,
    stem_text: text,
    equation,
    image_url: imageUrl,
    bank_kind: bankKind instanceof HTMLSelectElement ? String(bankKind.value || "") : "",
    save_to_bank: Boolean(lockSave || mode === "edit" || (saveBank instanceof HTMLInputElement && saveBank.checked)),
  };
  if (mode === "add" || scopeDirty) {
    body.bank_scope = String(scope instanceof HTMLSelectElement && scope.value ? scope.value : initialScope || "M1");
  }
  if (defaultsDirty) {
    const response = document.querySelector('input[name="live-add-q-response"]:checked');
    body.live_response_mode = String(response?.value || "individual");
    const show = $("live-add-q-show-results");
    body.live_show_results = Boolean(show instanceof HTMLInputElement && show.checked);
    body.live_points = String($("live-add-q-points")?.value || "").trim();
  }
  if (kind === "mc") {
    body.options = [0, 1, 2, 3].map((index) => String($(`live-add-q-opt-${index}`)?.value || "").trim());
    const key = document.querySelector('input[name="live-add-q-key"]:checked');
    body.correct_index = Number(key?.value || 0);
    body.correct_answer = String.fromCharCode(65 + Number(body.correct_index));
  }
  if (kind === "numeric") {
    body.correct_answer = String($("live-add-q-numeric-answer")?.value || "").trim();
    body.tolerance = String($("live-add-q-tolerance")?.value || "0").trim();
    body.tolerance_kind = String($("live-add-q-tolerance-kind")?.value || "absolute");
  }
  if (kind === "rank") {
    const rows = [...document.querySelectorAll("#live-add-q-rank-list .live-add-rank-row")].filter(
      (row) => row instanceof HTMLElement && !row.hidden
    );
    body.options = rows
      .map((row) => String(row.querySelector("input")?.value || "").trim())
      .filter(Boolean);
    body.rank_options = rows
      .map((row) => {
        const input = row.querySelector("input");
        const label = String(input?.value || "").trim();
        const id = input instanceof HTMLInputElement ? String(input.dataset.rankId || "") : "";
        return label ? { id, label } : null;
      })
      .filter(Boolean);
    const rankKey = rankKeyForSubmit(body.options);
    body.rank_key = rankKey;
  }
  return body;
}

/**
 * Upload an optional question image and return its public URL.
 * @param {File} file
 * @returns {Promise<string>}
 */
async function uploadImage(file) {
  const classId = Number(hooks.classId?.() || 0);
  if (!classId || !(file instanceof File) || !file.size) return keptImageUrl;
  const form = new FormData();
  form.append("image", file);
  const response = await fetch(`/api/staff/class/${classId}/live-question-image`, {
    method: "POST",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    body: form,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return String(payload?.image_url || "").trim();
}

/**
 * Save the open question through the host page.
 * @returns {Promise<void>}
 */
async function submitEditor() {
  const body = await collectBody();
  if (mode === "edit") {
    const bankId = Number(currentQuestion?.bank_id || currentQuestion?.source_bank_id || 0);
    const questionId = Number(currentQuestion?.question_id || currentQuestion?.id || 0);
    if (!bankId || !questionId) throw new Error("Question is not in a bank.");
    await api(`/api/staff/class/${Number(hooks.classId?.() || 0)}/question-bank/${bankId}/questions/${questionId}`, {
      method: "PATCH",
      body: JSON.stringify({ ...body, from_editor: true }),
    });
    hooks.onSaved?.();
    return;
  }
  if (lockSave) {
    if (!hooks.submitBankAdd) throw new Error("Save to bank is not available.");
    await hooks.submitBankAdd(body);
    hooks.onSaved?.();
    return;
  }
  if (!hooks.submitAdd) throw new Error("Add question is not available.");
  await hooks.submitAdd(body);
}

/**
 * Ask with the bank remove dialog, then delete the open question.
 * @returns {Promise<void>}
 */
async function removeCurrent() {
  const editor = $("live-add-question-dialog");
  const dialog = $("bank-remove-dialog");
  if (!(dialog instanceof HTMLDialogElement)) return;
  if (editor instanceof HTMLDialogElement && editor.open) editor.close();
  const ok = await new Promise((resolve) => {
    const onClose = () => {
      dialog.removeEventListener("close", onClose);
      resolve(dialog.returnValue === "ok");
    };
    dialog.addEventListener("close", onClose);
    dialog.showModal();
  });
  if (!ok) {
    if (editor instanceof HTMLDialogElement) editor.showModal();
    return;
  }
  const bankId = Number(currentQuestion?.bank_id || currentQuestion?.source_bank_id || 0);
  const questionId = Number(currentQuestion?.question_id || currentQuestion?.id || 0);
  await api(
    `/api/staff/class/${Number(hooks.classId?.() || 0)}/question-bank/${bankId}/questions/${questionId}`,
    { method: "DELETE" }
  );
  hooks.onRemoved?.();
}

/**
 * Bind the dialog once. Both host pages import this module.
 */
function bindEditor() {
  const dialog = $("live-add-question-dialog");
  if (!(dialog instanceof HTMLDialogElement) || dialog.dataset.editorBound === "1") return;
  dialog.dataset.editorBound = "1";
  document.querySelectorAll('input[name="live-add-q-type"]').forEach((input) => {
    input.addEventListener("change", () => syncTypeFields());
  });
  $("live-add-q-rank-add")?.addEventListener("click", () => {
    const hidden = document.querySelector("#live-add-q-rank-list .live-add-rank-row[hidden]");
    if (hidden instanceof HTMLElement) hidden.hidden = false;
    syncRankOptionRows();
  });
  document.querySelectorAll("#live-add-q-rank-list [data-rank-remove]").forEach((button) => {
    button.addEventListener("click", () => {
      const rows = [...document.querySelectorAll("#live-add-q-rank-list .live-add-rank-row")].filter(
        (row) => row instanceof HTMLElement && !row.hidden
      );
      if (rows.length <= 3) return;
      const row = button.closest(".live-add-rank-row");
      if (!(row instanceof HTMLElement)) return;
      const input = row.querySelector("input");
      if (input instanceof HTMLInputElement) input.value = "";
      row.hidden = true;
      syncRankOptionRows();
    });
  });
  document.querySelectorAll("#live-add-q-rank-list input").forEach((input) => {
    input.addEventListener("input", () => syncRankKeyList());
  });
  $("live-add-q-rank-key-on")?.addEventListener("change", () => syncRankKeyList());
  const keyList = $("live-add-q-rank-key-list");
  let dragRow = -1;
  const rowOf = (node) =>
    Number(
      (node instanceof Element ? node.closest("[data-rank-key-row]") : null)?.getAttribute("data-rank-key-row") ?? -1
    );
  keyList?.addEventListener("dragstart", (event) => {
    dragRow = rowOf(event.target);
    if (event.target instanceof HTMLElement) event.target.classList.add("is-dragging");
    if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
  });
  keyList?.addEventListener("dragover", (event) => {
    if (dragRow >= 0) event.preventDefault();
  });
  keyList?.addEventListener("drop", (event) => {
    event.preventDefault();
    const target = rowOf(event.target);
    if (dragRow < 0) return;
    const to = target >= 0 ? rankKeyRows.indexOf(target) : rankKeyRows.length - 1;
    moveRankKeyRow(dragRow, to);
    dragRow = -1;
  });
  keyList?.addEventListener("dragend", () => {
    dragRow = -1;
    syncRankKeyList();
  });
  keyList?.addEventListener("keydown", (event) => {
    if (!event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return;
    const row = rowOf(event.target);
    if (row < 0) return;
    event.preventDefault();
    const at = rankKeyRows.indexOf(row);
    moveRankKeyRow(row, event.key === "ArrowUp" ? at - 1 : at + 1);
  });
  document.querySelectorAll("[data-eq-insert]").forEach((button) => {
    button.addEventListener("click", () => {
      insertLatex(String(button.getAttribute("data-eq-insert") || ""));
    });
  });
  $("live-add-q-equation")?.addEventListener("input", () => {
    paintEquationPreview();
  });
  $("live-add-q-save-bank")?.addEventListener("change", () => syncBankFields());
  $("live-add-q-bank-scope-select")?.addEventListener("change", () => {
    const select = $("live-add-q-bank-scope-select");
    scopeDirty = select instanceof HTMLSelectElement && select.value !== initialScope;
  });
  document.querySelectorAll('input[name="live-add-q-response"], #live-add-q-show-results, #live-add-q-points').forEach((input) => {
    input.addEventListener("input", () => {
      defaultsDirty = true;
    });
    input.addEventListener("change", () => {
      defaultsDirty = true;
    });
  });
  $("live-add-question-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const err = $("live-add-q-error");
    if (err instanceof HTMLElement) {
      err.hidden = true;
      err.textContent = "";
    }
    const btn = $("live-add-q-submit");
    if (btn instanceof HTMLButtonElement) btn.disabled = true;
    try {
      await submitEditor();
      closeQuestionEditor();
    } catch (error) {
      if (err instanceof HTMLElement) {
        err.hidden = false;
        err.textContent = error instanceof Error ? error.message : String(error);
      }
    } finally {
      if (btn instanceof HTMLButtonElement) btn.disabled = false;
    }
  });
  dialog.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.closest("[data-live-add-question-close]")) closeQuestionEditor();
    if (target.closest("[data-question-editor-prev]")) stepQuestion(-1);
    if (target.closest("[data-question-editor-next]")) stepQuestion(1);
  });
  dialog.addEventListener("cancel", () => closeQuestionEditor());
  $("live-add-q-remove")?.addEventListener("click", () => {
    removeCurrent().catch((error) => {
      const err = $("live-add-q-error");
      if (err instanceof HTMLElement) {
        err.hidden = false;
        err.textContent = error instanceof Error ? error.message : String(error);
      }
      if (dialog instanceof HTMLDialogElement && !dialog.open) dialog.showModal();
    });
  });
  document.addEventListener("keydown", (event) => {
    if (!dialog.open || mode !== "edit") return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const key = event.key.toLowerCase();
    if (key !== "j" && key !== "k") return;
    const tag = document.activeElement?.tagName || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    event.preventDefault();
    stepQuestion(key === "j" ? 1 : -1);
  });
}

bindEditor();
