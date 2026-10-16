/**
 * Question banks tab: one module menu, a light table, and a short bulk bar.
 */
import { api, escapeHtml, formatQuestionHtml, renderLiveQuestionMath } from "/static/common.js";
import { mountTypeChips } from "/static/bank_type_chips.js";
import { configureQuestionEditor, openQuestionEditor } from "/static/question_editor.js";
import { questionPointsValue } from "/static/question_points.js";

const root = document.getElementById("catalog-root");
const classId = Number(root?.dataset.classId || 0);
const EMPTY_BANKS = root?.dataset.emptyBanks || "No banks imported yet.";
const CHIP_COPY = root?.dataset.editedInLms || "Edited in LMS";
const TOAST_COPY = root?.dataset.saveToast || "Saved to this course’s copy.";
const list = document.getElementById("catalog-list");
const toast = document.getElementById("bank-toast");
const removeDialog = document.getElementById("bank-remove-dialog");
const moduleEl = document.getElementById("qbank-module");
const kindEl = document.getElementById("live-bank-kind");
const searchEl = document.getElementById("live-bank-search");
const typeHost = document.getElementById("live-bank-types");
const summaryEl = document.getElementById("qbank-summary");
const bulkEl = document.getElementById("qbank-bulk");
const bulkCountEl = document.getElementById("qbank-bulk-count");

const typeChips =
  typeHost instanceof HTMLElement
    ? mountTypeChips(typeHost, {
        view: "question-banks",
        onChange: () => {
          loadLiveBank().catch(() => {});
        },
      })
    : null;

const TABS = ["all", "M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "course"];
/** @type {Record<string, number>} */
let tabCounts = {};
/** @type {Record<string, unknown>[]} */
let items = [];
/** @type {Map<string, {bank_id: number, question_id: number}>} */
let selectedRows = new Map();
let activeTab = moduleEl instanceof HTMLSelectElement ? moduleEl.value || "M1" : "M1";
/** @type {"bank"|"edited"|"type"} */
let activeSort = "bank";
/** @type {{bank_id: number, question_id: number}[]|null} */
let actionQuestions = null;
let toastTimer = 0;
let searchTimer = 0;
let loadToken = 0;

configureQuestionEditor({
  classId: () => classId,
  submitBankAdd: async (body) => {
    await api(`/api/staff/class/${classId}/live-bank/questions`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  onSaved: () => {
    showToast(TOAST_COPY);
    refreshAfterWrite();
  },
  onRemoved: () => {
    showToast("Removed from bank.");
    refreshAfterWrite();
  },
});

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
 * Stable id for selection and bulk writes.
 * @param {Record<string, unknown>} item
 * @returns {string}
 */
function itemKey(item) {
  return `${Number(item.bank_id || item.source_bank_id || 0)}:${Number(item.question_id || item.id || 0)}`;
}

/**
 * Kind label on a row.
 * @param {Record<string, unknown>} item
 * @returns {string}
 */
function kindLabel(item) {
  const kind = String(item.kind || item.bank_kind || "").toLowerCase();
  if (kind === "standard") return "Custom";
  if (kind === "contest") return "Contest";
  if (kind === "warmup") return "Warmup";
  return root?.dataset.courseSimple === "1" ? "Course" : "Core Math";
}

/**
 * Short type chip.
 * @param {Record<string, unknown>} item
 * @returns {{token: string, label: string}}
 */
function typeChip(item) {
  const token = String(item.question_type || item.type || "mc").toLowerCase();
  if (token === "numeric" || token === "numerical") return { token: "num", label: "Numeric" };
  if (token === "rank") return { token: "rank", label: "Rank" };
  if (token === "poll" || token === "open") return { token: "poll", label: "Poll" };
  if (token === "true_false") return { token: "mc", label: "T/F" };
  return { token: "mc", label: "MC" };
}

/**
 * Module cell. A blank scope has no module pin.
 * @param {Record<string, unknown>} item
 * @returns {string}
 */
function moduleLabel(item) {
  const scope = String(item.bank_scope || "").trim();
  if (!scope) return "—";
  if (scope.toLowerCase() === "course") return "Course Wide";
  const token = scope.toUpperCase();
  if (/^M[0-8]$/.test(token)) return token;
  if (/^[0-8]$/.test(scope)) return `M${scope}`;
  return scope;
}

/**
 * Live response mode. A blank stored value is Individual.
 * @param {Record<string, unknown>} item
 * @returns {string}
 */
function modeLabel(item) {
  return String(item.live_response_mode || "").toLowerCase() === "group" ? "Group" : "Individual";
}

/**
 * Show-results cell. Null matches the editor and means Yes.
 * @param {Record<string, unknown>} item
 * @returns {string}
 */
function showResultsLabel(item) {
  const value = item.live_show_results;
  if (value === false || value === 0 || value === "0") return "No";
  return "Yes";
}

/**
 * Points text from the same figure the editor uses.
 * @param {Record<string, unknown>} item
 * @returns {string}
 */
function pointsText(item) {
  return String(questionPointsValue(item));
}

/**
 * Small equation and image tags beside the stem.
 * @param {Record<string, unknown>} item
 * @returns {string}
 */
function mediaTagHtml(item) {
  const tags = [];
  if (String(item.equation || item.equation_latex || "").trim()) {
    tags.push('<span class="qbank-tag">∑ equation</span>');
  }
  if (String(item.image_url || "").trim()) {
    tags.push('<span class="qbank-tag">▣ image</span>');
  }
  return tags.join("");
}

/**
 * The three server sorts, as buttons in the Question header.
 * @returns {string}
 */
function sortButtons() {
  const sorts = [
    ["bank", "Question"],
    ["type", "Type"],
    ["edited", "Edited"],
  ];
  return `<span class="qbank-sorts">${sorts
    .map(([value, label]) => {
      const on = activeSort === value;
      const icon = on ? '<span class="qbank-sort-ico" aria-hidden="true">▲</span>' : "";
      return `<button type="button" class="qbank-sort${on ? " is-on" : ""}" data-qbank-sort="${value}" aria-pressed="${on ? "true" : "false"}">${label}${icon}</button>`;
    })
    .join("")}</span>`;
}

/**
 * Remember one checked row so a module change does not drop it.
 * @param {string} key
 * @param {boolean} checked
 */
function setChecked(key, checked) {
  if (!checked) {
    selectedRows.delete(key);
    paintBulk();
    paintChecks();
    return;
  }
  const item = items.find((row) => itemKey(row) === key);
  if (!item) return;
  const bankId = Number(item.bank_id || item.source_bank_id || 0);
  const questionId = Number(item.question_id || item.id || 0);
  if (!bankId || !questionId) return;
  selectedRows.set(key, { bank_id: bankId, question_id: questionId });
  paintBulk();
  paintChecks();
}

/**
 * Paint checked rows and the header checkbox without rebuilding the table.
 */
function paintChecks() {
  if (!(list instanceof HTMLElement)) return;
  list.querySelectorAll("[data-qbank-key]").forEach((row) => {
    if (!(row instanceof HTMLElement)) return;
    const key = row.dataset.qbankKey || "";
    const on = selectedRows.has(key);
    row.classList.toggle("is-checked", on);
    const box = row.querySelector("[data-qbank-check]");
    if (box instanceof HTMLInputElement) box.checked = on;
  });
  const all = document.getElementById("qbank-check-all");
  if (!(all instanceof HTMLInputElement)) return;
  const keys = items.map((item) => itemKey(item));
  const picked = keys.filter((key) => selectedRows.has(key)).length;
  all.checked = keys.length > 0 && picked === keys.length;
  all.indeterminate = picked > 0 && picked < keys.length;
}

/**
 * Check every row on the open module without touching other modules.
 */
function selectAllOnTab() {
  for (const item of items) {
    const key = itemKey(item);
    const bankId = Number(item.bank_id || item.source_bank_id || 0);
    const questionId = Number(item.question_id || item.id || 0);
    if (bankId && questionId) selectedRows.set(key, { bank_id: bankId, question_id: questionId });
  }
  renderList();
}

/**
 * Drop every checked row, including ones on other modules.
 */
function clearSelection() {
  selectedRows.clear();
  renderList();
}

/**
 * Uncheck the rows on the open module and leave other modules selected.
 */
function clearModuleSelection() {
  for (const item of items) selectedRows.delete(itemKey(item));
  renderList();
}

/**
 * Paint the question table in the order the server returned.
 */
function renderList() {
  if (!(list instanceof HTMLElement)) return;
  closeRowMenu();
  const rows = items;
  const query = String(searchEl instanceof HTMLInputElement ? searchEl.value : "").trim();
  const qtype = typeChips ? typeChips.value() : "";
  const kindSet = kindEl instanceof HTMLSelectElement && Boolean(kindEl.value);
  const tabTotal = Number(tabCounts[activeTab] ?? 0);
  const courseHasQuestions = Object.values(tabCounts).some((count) => Number(count) > 0);
  const filtered = Boolean(query || qtype || kindSet || (tabTotal > 0 && !rows.length));
  const body = rows.length
    ? rows.map((item) => rowHtml(item)).join("")
    : emptyRowHtml(filtered, courseHasQuestions);
  list.innerHTML = tableHtml(body);
  const all = document.getElementById("qbank-check-all");
  if (all instanceof HTMLInputElement) {
    const keys = items.map((item) => itemKey(item));
    const picked = keys.filter((key) => selectedRows.has(key)).length;
    all.indeterminate = picked > 0 && picked < keys.length;
  }
  document.getElementById("qbank-clear-filters")?.addEventListener("click", clearFilters);
  document.getElementById("qbank-empty-add")?.addEventListener("click", () => openAdd());
  if (rows.length) renderLiveQuestionMath(list);
  paintBulk();
}

/**
 * One question row.
 * @param {Record<string, unknown>} item
 * @returns {string}
 */
function rowHtml(item) {
  const chip = typeChip(item);
  const key = itemKey(item);
  const stemSource = String(item.text || item.prompt || item.stem_preview_html || item.question_title || "");
  const edited = item.edited_in_lms
    ? `<span class="chip qbank-edited">${escapeHtml(CHIP_COPY)}</span>`
    : "";
  const checked = selectedRows.has(key);
  return `<tr class="qbank-tr${checked ? " is-checked" : ""}" data-qbank-key="${escapeHtml(key)}">
    <td class="qcell qcell-check"><input type="checkbox" data-qbank-check="${escapeHtml(key)}" aria-label="Select question"${checked ? " checked" : ""}></td>
    <td class="qcell qcell-question"><div class="qbank-line"><span class="chip qbank-type qbank-type-${escapeHtml(chip.token)}">${escapeHtml(chip.label)}</span><div class="qbank-stem">${formatQuestionHtml(stemSource)}</div>${mediaTagHtml(item)}${edited}</div></td>
    <td class="qcell">${escapeHtml(kindLabel(item))}</td>
    <td class="qcell">${escapeHtml(moduleLabel(item))}</td>
    <td class="qcell">${escapeHtml(modeLabel(item))}</td>
    <td class="qcell">${escapeHtml(showResultsLabel(item))}</td>
    <td class="qcell qcell-points">${escapeHtml(pointsText(item))}</td>
    <td class="qcell qcell-more"><button type="button" class="qbank-more" data-qbank-more="${escapeHtml(key)}" aria-label="Question actions">⋯</button></td>
  </tr>`;
}

/**
 * Empty table body for a module with no rows, or a filter that matches none.
 * @param {boolean} filtered
 * @param {boolean} courseHasQuestions
 * @returns {string}
 */
function emptyRowHtml(filtered, courseHasQuestions) {
  const message = filtered
    ? 'No questions match these filters. <button type="button" id="qbank-clear-filters">Clear filters</button>'
    : courseHasQuestions
      ? '<p>No questions in this module yet.</p><button type="button" id="qbank-empty-add">+ Add question</button>'
      : `<p>${escapeHtml(EMPTY_BANKS)}</p><button type="button" id="qbank-empty-add">+ Add question</button>`;
  return `<tr class="qbank-tr"><td class="qcell qbank-empty" colspan="8">${message}</td></tr>`;
}

/**
 * Sticky header plus the current rows.
 * @param {string} body
 * @returns {string}
 */
function tableHtml(body) {
  const keys = items.map((item) => itemKey(item));
  const picked = keys.filter((key) => selectedRows.has(key)).length;
  const allOn = keys.length > 0 && picked === keys.length;
  return `<table class="qbank-table">
    <colgroup>
      <col class="qcol-check">
      <col>
      <col class="qcol-kind">
      <col class="qcol-module">
      <col class="qcol-mode">
      <col class="qcol-results">
      <col class="qcol-points">
      <col class="qcol-more">
    </colgroup>
    <thead>
      <tr>
        <th class="qcell qcell-check" scope="col"><input type="checkbox" id="qbank-check-all" data-qbank-check-all aria-label="Select all in this module"${allOn ? " checked" : ""}${keys.length ? "" : " disabled"}></th>
        <th class="qcell qcell-question" scope="col">${sortButtons()}</th>
        <th class="qcell" scope="col">Kind</th>
        <th class="qcell" scope="col">Module</th>
        <th class="qcell" scope="col">Mode</th>
        <th class="qcell" scope="col">Show results</th>
        <th class="qcell" scope="col">Points</th>
        <th class="qcell qcell-more" scope="col"><span aria-hidden="true">⋯</span></th>
      </tr>
    </thead>
    <tbody>${body}</tbody>
  </table>`;
}

/**
 * Show the bulk bar only while at least one row is checked.
 */
function paintBulk() {
  if (!(bulkEl instanceof HTMLElement)) return;
  const count = selectedRows.size;
  bulkEl.hidden = count === 0;
  if (bulkCountEl) bulkCountEl.textContent = `${count} selected`;
}

/**
 * Write each module's count beside its menu label.
 */
function paintModule() {
  if (!(moduleEl instanceof HTMLSelectElement)) return;
  for (const option of moduleEl.options) {
    const tab = option.value;
    const label = option.dataset.tabLabel || option.textContent || "";
    const count = tabCounts[tab];
    option.textContent = count == null ? label : `${label} · ${count}`;
  }
}

/**
 * Scope token sent to mc-search.
 * @returns {string}
 */
function searchScope() {
  if (activeTab === "all") return "all";
  if (activeTab === "course") return "course";
  return activeTab;
}

/**
 * Load one module through the same mc-search Import uses, plus staff numeric and poll.
 * @returns {Promise<void>}
 */
async function loadLiveBank() {
  if (!(list instanceof HTMLElement) || !classId) return;
  const token = ++loadToken;
  const params = new URLSearchParams();
  params.set("staff_types", "1");
  const query = String(searchEl instanceof HTMLInputElement ? searchEl.value : "").trim();
  const kind = kindEl instanceof HTMLSelectElement ? String(kindEl.value || "") : "";
  const qtype = typeChips ? typeChips.value() : "";
  const sort = activeSort;
  if (query) params.set("q", query);
  if (kind) params.set("kind", kind);
  if (qtype) params.set("type", qtype);
  if (sort) params.set("sort", sort);
  if (activeTab === "course") params.set("placement", "course");
  else if (activeTab !== "all") params.set("placement", "module");
  const suffix = params.toString() ? `?${params.toString()}` : "";
  list.setAttribute("aria-busy", "true");
  try {
    const payload = await api(
      `/api/staff/class/${classId}/module-banks/${encodeURIComponent(searchScope())}/mc-search${suffix}`
    );
    if (token !== loadToken) return;
    items = Array.isArray(payload.items) ? payload.items : [];
    if (typeChips) typeChips.update(payload.type_counts || []);
    if (summaryEl) {
      const shown = items.length;
      const matched = Number(payload.filtered ?? shown);
      const edited = items.filter((item) => item.edited_in_lms).length;
      summaryEl.textContent =
        shown < matched
          ? `Showing ${shown} of ${matched}`
          : `${Number(payload.total || shown)} questions · ${edited} edited in LMS`;
    }
    renderList();
  } catch (error) {
    if (token !== loadToken) return;
    list.innerHTML = `<p class="error compact">${escapeHtml(error instanceof Error ? error.message : String(error))}</p>`;
  } finally {
    if (token === loadToken) list.removeAttribute("aria-busy");
  }
}

/**
 * Module counts once per visit and after a write, not on each keystroke.
 * @returns {Promise<void>}
 */
async function loadTabCounts() {
  const jobs = TABS.map(async (tab) => {
    const scope = tab === "all" ? "all" : tab === "course" ? "course" : tab;
    const params = new URLSearchParams();
    params.set("staff_types", "1");
    params.set("kind", "any");
    if (tab === "course") params.set("placement", "course");
    else if (tab !== "all") params.set("placement", "module");
    const payload = await api(
      `/api/staff/class/${classId}/module-banks/${encodeURIComponent(scope)}/mc-search?${params.toString()}`
    );
    tabCounts[tab] = Number(payload.total || 0);
  });
  await Promise.all(jobs);
  paintModule();
}

/**
 * Reload counts and the open module after a save, move, or remove.
 */
function refreshAfterWrite() {
  selectedRows = new Map();
  paintBulk();
  loadTabCounts()
    .catch(() => {})
    .finally(() => {
      loadLiveBank().catch(() => {});
    });
}

/**
 * Reset search, kind, and type, then reload.
 */
function clearFilters() {
  if (searchEl instanceof HTMLInputElement) searchEl.value = "";
  if (kindEl instanceof HTMLSelectElement) kindEl.value = "";
  const all = typeHost?.querySelector('[data-bank-type=""]');
  if (all instanceof HTMLElement && typeChips && typeChips.value()) {
    all.click();
    return;
  }
  loadLiveBank().catch(() => {});
}

/**
 * Scope the Add dialog should offer for the open module.
 * @returns {string}
 */
function addScope() {
  if (activeTab === "course") return "course";
  if (/^M[0-8]$/.test(activeTab)) return activeTab;
  return "M1";
}

/**
 * Open Add question with Save to bank locked.
 */
function openAdd() {
  openQuestionEditor({
    mode: "add",
    lockSave: true,
    scope: addScope(),
    scopeKnown: true,
  });
}

/**
 * Open Edit question for one row, with Prev/Next across the visible list.
 * @param {string} key
 */
function openEdit(key) {
  const index = items.findIndex((item) => itemKey(item) === key);
  if (index < 0) return;
  openQuestionEditor({
    mode: "edit",
    question: items[index],
    questions: items,
    index,
    scope: addScope(),
    scopeKnown: true,
    lockSave: true,
  });
}

/**
 * Questions currently checked, including rows on other modules.
 * @returns {{bank_id: number, question_id: number}[]}
 */
function selectedQuestions() {
  return [...selectedRows.values()];
}

/**
 * POST one bulk action and refresh.
 * @param {Record<string, unknown>} body
 * @param {string} [toastMessage]
 * @returns {Promise<void>}
 */
async function postBulk(body, toastMessage, questions) {
  const rows = questions && questions.length ? questions : selectedQuestions();
  await api(`/api/staff/class/${classId}/question-bank/bulk`, {
    method: "POST",
    body: JSON.stringify({ ...body, questions: rows }),
  });
  showToast(toastMessage || TOAST_COPY);
  refreshAfterWrite();
}

/**
 * Questions for the dialog that is opening.
 *
 * The row menu acts on one question. The bulk bar acts on the checkboxes.
 * @returns {{bank_id: number, question_id: number}[]}
 */
function questionsForAction() {
  const picked = actionQuestions;
  actionQuestions = null;
  return picked && picked.length ? picked : selectedQuestions();
}

/**
 * One checked row, for the ⋯ menu.
 * @param {string} key
 * @returns {{bank_id: number, question_id: number}|null}
 */
function questionByKey(key) {
  const item = items.find((row) => itemKey(row) === key);
  if (!item) return null;
  const bankId = Number(item.bank_id || item.source_bank_id || 0);
  const questionId = Number(item.question_id || item.id || 0);
  if (!bankId || !questionId) return null;
  return { bank_id: bankId, question_id: questionId };
}

/**
 * Confirm heading for a bulk remove. One row stays singular.
 * @param {number} count
 * @returns {string}
 */
function removeConfirmTitle(count) {
  const n = Number(count) || 0;
  return n === 1 ? "Remove 1 question?" : `Remove ${n} questions?`;
}

/**
 * Resting heading for the shared remove dialog.
 *
 * The question editor opens the same dialog and does not set the heading.
 * Bulk remove must put this back when the dialog closes.
 * @returns {string}
 */
function defaultRemoveTitle() {
  return root?.dataset.removeFromBank || "Remove from bank?";
}

/**
 * Confirm removal, then delete the checked questions in one request.
 * @returns {Promise<void>}
 */
async function removeSelected() {
  await confirmRemove(selectedQuestions());
}

/**
 * Confirm, then delete these questions in one request.
 * @param {{bank_id: number, question_id: number}[]} questions
 * @returns {Promise<void>}
 */
async function confirmRemove(questions) {
  const count = questions.length;
  if (!(removeDialog instanceof HTMLDialogElement) || !count) return;
  const title = document.getElementById("bank-remove-title");
  if (title) title.textContent = removeConfirmTitle(count);
  const ok = await new Promise((resolve) => {
    const finish = (accepted) => {
      removeDialog.removeEventListener("close", onClose);
      if (title) title.textContent = defaultRemoveTitle();
      resolve(accepted);
    };
    const onClose = () => finish(removeDialog.returnValue === "ok");
    removeDialog.addEventListener("close", onClose);
    if (typeof removeDialog.showModal === "function") removeDialog.showModal();
    else finish(false);
  });
  if (!ok) return;
  await postBulk({ action: "remove" }, "Removed from bank.", questions);
}

/**
 * Place the row menu under its ⋯ button.
 * @param {HTMLButtonElement} button
 */
function openRowMenu(button) {
  const menu = document.getElementById("qbank-row-menu");
  if (!(menu instanceof HTMLElement)) return;
  const key = button.dataset.qbankMore || "";
  if (menu.dataset.key === key && !menu.hidden) {
    closeRowMenu();
    return;
  }
  menu.dataset.key = key;
  menu.hidden = false;
  const rect = button.getBoundingClientRect();
  const width = menu.offsetWidth;
  const height = menu.offsetHeight;
  let top = rect.bottom + 4;
  if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 4);
  menu.style.top = `${top}px`;
  menu.style.left = `${Math.max(8, rect.right - width)}px`;
}

/**
 * Hide the row menu.
 */
function closeRowMenu() {
  const menu = document.getElementById("qbank-row-menu");
  if (!(menu instanceof HTMLElement)) return;
  menu.hidden = true;
  delete menu.dataset.key;
}

/**
 * Apply one of the three server sorts and reload.
 * @param {string} value
 */
function setBankSort(value) {
  const next = value === "type" || value === "edited" ? value : "bank";
  if (next === activeSort) return;
  activeSort = next;
  loadLiveBank().catch(() => {});
}


moduleEl?.addEventListener("change", () => {
  activeTab = moduleEl instanceof HTMLSelectElement ? moduleEl.value || "M1" : "M1";
  loadLiveBank().catch(() => {});
});
kindEl?.addEventListener("change", () => {
  loadLiveBank().catch(() => {});
});
searchEl?.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    loadLiveBank().catch(() => {});
  }, 200);
});
document.getElementById("qbank-add")?.addEventListener("click", () => openAdd());
document.getElementById("qbank-clear-selection")?.addEventListener("click", () => clearSelection());
list?.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const sort = target.closest("[data-qbank-sort]");
  if (sort instanceof HTMLButtonElement) {
    setBankSort(sort.dataset.qbankSort || "bank");
    return;
  }
  const more = target.closest("[data-qbank-more]");
  if (more instanceof HTMLButtonElement) {
    openRowMenu(more);
  }
});
list?.addEventListener("change", (event) => {
  const check = event.target;
  if (!(check instanceof HTMLInputElement)) return;
  if (check.hasAttribute("data-qbank-check-all")) {
    if (check.checked) selectAllOnTab();
    else clearModuleSelection();
    return;
  }
  if (!check.dataset.qbankCheck) return;
  setChecked(check.dataset.qbankCheck, check.checked);
});
list?.addEventListener("scroll", () => closeRowMenu());
document.getElementById("qbank-row-menu")?.addEventListener("click", (event) => {
  const target = event.target;
  const button = target instanceof HTMLElement ? target.closest("[data-qbank-menu]") : null;
  if (!(button instanceof HTMLButtonElement)) return;
  const menu = document.getElementById("qbank-row-menu");
  const key = menu instanceof HTMLElement ? menu.dataset.key || "" : "";
  const action = button.dataset.qbankMenu || "";
  closeRowMenu();
  if (action === "edit") {
    openEdit(key);
    return;
  }
  const row = questionByKey(key);
  if (!row) return;
  if (action === "move") {
    actionQuestions = [row];
    const dialog = document.getElementById("qbank-move-dialog");
    if (dialog instanceof HTMLDialogElement) dialog.showModal();
  } else if (action === "kind") {
    actionQuestions = [row];
    const dialog = document.getElementById("qbank-kind-dialog");
    if (dialog instanceof HTMLDialogElement) dialog.showModal();
  } else if (action === "remove") {
    confirmRemove([row]).catch((error) => showToast(error instanceof Error ? error.message : String(error)));
  }
});
document.addEventListener("click", (event) => {
  const menu = document.getElementById("qbank-row-menu");
  if (!(menu instanceof HTMLElement) || menu.hidden) return;
  const target = event.target;
  if (!(target instanceof Node) || menu.contains(target)) return;
  if (target instanceof Element && target.closest("[data-qbank-more]")) return;
  closeRowMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeRowMenu();
});
document.getElementById("qbank-bulk-move")?.addEventListener("click", () => {
  actionQuestions = null;
  const dialog = document.getElementById("qbank-move-dialog");
  if (dialog instanceof HTMLDialogElement) dialog.showModal();
});
document.getElementById("qbank-bulk-kind")?.addEventListener("click", () => {
  actionQuestions = null;
  const dialog = document.getElementById("qbank-kind-dialog");
  if (dialog instanceof HTMLDialogElement) dialog.showModal();
});
document.getElementById("qbank-bulk-remove")?.addEventListener("click", () => {
  removeSelected().catch((error) => showToast(error instanceof Error ? error.message : String(error)));
});
document.getElementById("qbank-move-dialog")?.addEventListener("close", () => {
  actionQuestions = null;
});
document.getElementById("qbank-kind-dialog")?.addEventListener("close", () => {
  actionQuestions = null;
});
document.getElementById("qbank-move-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const select = document.getElementById("qbank-move-scope");
  const scope = select instanceof HTMLSelectElement ? select.value : "M1";
  const rows = questionsForAction();
  postBulk({ action: "move", bank_scope: scope }, undefined, rows).catch((error) => {
    showToast(error instanceof Error ? error.message : String(error));
  });
  document.getElementById("qbank-move-dialog")?.close();
});
document.getElementById("qbank-kind-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const select = document.getElementById("qbank-kind-value");
  const kind = select instanceof HTMLSelectElement ? select.value : "";
  const rows = questionsForAction();
  postBulk({ action: "kind", bank_kind: kind }, undefined, rows).catch((error) => {
    showToast(error instanceof Error ? error.message : String(error));
  });
  document.getElementById("qbank-kind-dialog")?.close();
});

paintModule();
loadTabCounts()
  .catch(() => {})
  .finally(() => {
    loadLiveBank().catch(() => {});
  });
