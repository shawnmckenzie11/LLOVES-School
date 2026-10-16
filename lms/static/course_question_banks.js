/**
 * Question banks tab: module cards, shared editor, and a short bulk bar.
 */
import { api, escapeHtml, formatQuestionHtml, renderLiveQuestionMath } from "/static/common.js";
import { mountTypeChips } from "/static/bank_type_chips.js";
import { configureQuestionEditor, openQuestionEditor } from "/static/question_editor.js";

const root = document.getElementById("catalog-root");
const classId = Number(root?.dataset.classId || 0);
const EMPTY_BANKS = root?.dataset.emptyBanks || "No banks imported yet.";
const CHIP_COPY = root?.dataset.editedInLms || "Edited in LMS";
const TOAST_COPY = root?.dataset.saveToast || "Saved to this course’s copy.";
const list = document.getElementById("catalog-list");
const toast = document.getElementById("bank-toast");
const removeDialog = document.getElementById("bank-remove-dialog");
const scopeEl = document.getElementById("live-bank-scope");
const kindEl = document.getElementById("live-bank-kind");
const searchEl = document.getElementById("live-bank-search");
const sortEl = document.getElementById("live-bank-sort");
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

const TABS = ["all", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "course"];
/** @type {Record<string, number>} */
let tabCounts = {};
/** @type {Record<string, unknown>[]} */
let items = [];
/** @type {Set<string>} */
let selectedIds = new Set();
let activeTab = "M1";
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
 * Kind label on a card.
 * @param {Record<string, unknown>} item
 * @returns {string}
 */
function kindLabel(item) {
  const kind = String(item.kind || item.bank_kind || "").toLowerCase();
  if (kind === "standard") return "Custom";
  if (kind === "contest") return "Contest";
  if (kind === "warmup") return "Warmup";
  return "Core";
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
 * Rank key line. Order is set when the row carries an answer key.
 * @param {Record<string, unknown>} item
 * @returns {string}
 */
function rankPeekHtml(item) {
  return item?.rank_key ? "order set" : "—";
}

/**
 * One-line key for the card footer.
 * @param {Record<string, unknown>} item
 * @returns {string}
 */
function keyLine(item) {
  const type = String(item.type || item.question_type || "").toLowerCase();
  if (type === "rank") return rankPeekHtml(item);
  if (type === "poll" || type === "open") return "—";
  const key = String(item.key || item.correct_answer || "").trim();
  return key || "—";
}

/**
 * Points chip: a stored live default, else the question's points, else 1.
 * @param {Record<string, unknown>} item
 * @returns {string}
 */
function pointsLabel(item) {
  const live = item.live_points;
  const points = live != null && live !== "" ? Number(live) : Number(item.points ?? 1);
  if (!Number.isFinite(points)) return "1 pt";
  return `${points} pt`;
}

/**
 * Sort the current page of cards without another search.
 * @param {Record<string, unknown>[]} rows
 * @returns {Record<string, unknown>[]}
 */
function sortItems(rows) {
  const mode = sortEl instanceof HTMLSelectElement ? sortEl.value : "bank";
  const copy = [...rows];
  if (mode === "edited") {
    copy.sort((a, b) => Number(Boolean(b.edited_in_lms)) - Number(Boolean(a.edited_in_lms)));
    return copy;
  }
  if (mode === "type") {
    copy.sort((a, b) => String(a.question_type || a.type || "").localeCompare(String(b.question_type || b.type || "")));
    return copy;
  }
  copy.sort((a, b) => Number(a.question_id || a.id || 0) - Number(b.question_id || b.id || 0));
  return copy;
}

/**
 * Paint the card grid.
 */
function renderGrid() {
  if (!(list instanceof HTMLElement)) return;
  const rows = sortItems(items);
  const query = String(searchEl instanceof HTMLInputElement ? searchEl.value : "").trim();
  const qtype = typeChips ? typeChips.value() : "";
  const kindSet = kindEl instanceof HTMLSelectElement && Boolean(kindEl.value);
  const tabTotal = Number(tabCounts[activeTab] ?? 0);
  const courseHasQuestions = Object.values(tabCounts).some((count) => Number(count) > 0);
  const filtered = Boolean(query || qtype || kindSet || (tabTotal > 0 && !rows.length));
  if (!rows.length) {
    const emptyCard = courseHasQuestions
      ? `<article class="qbank-empty-card"><p>No questions in this module yet.</p><button type="button" id="qbank-empty-add">+ Add question</button></article>`
      : `<article class="qbank-empty-card"><p>${escapeHtml(EMPTY_BANKS)}</p><button type="button" id="qbank-empty-add">+ Add question</button></article>`;
    list.innerHTML = filtered
      ? `<p class="qbank-empty">No questions match these filters. <button type="button" id="qbank-clear-filters">Clear filters</button></p>`
      : emptyCard;
    document.getElementById("qbank-clear-filters")?.addEventListener("click", clearFilters);
    document.getElementById("qbank-empty-add")?.addEventListener("click", () => openAdd());
    paintBulk();
    return;
  }
  list.innerHTML = rows
    .map((item) => {
      const chip = typeChip(item);
      const key = itemKey(item);
      const stemSource = String(item.text || item.prompt || item.stem_preview_html || item.question_title || "");
      const edited = item.edited_in_lms
        ? `<span class="chip qbank-edited">${escapeHtml(CHIP_COPY)}</span>`
        : "";
      return `<article class="qbank-card" data-bank-kind="${escapeHtml(String(item.kind || ""))}" data-qbank-key="${escapeHtml(key)}">
        <div class="qbank-card-head">
          <input type="checkbox" data-qbank-check="${escapeHtml(key)}" aria-label="Select question"${selectedIds.has(key) ? " checked" : ""}>
          <span class="chip qbank-type qbank-type-${escapeHtml(chip.token)}">${escapeHtml(chip.label)}</span>
          <span class="qbank-kind">${escapeHtml(kindLabel(item))}</span>
          <span class="qbank-points">${escapeHtml(pointsLabel(item))}</span>
          ${edited}
        </div>
        <div class="qbank-stem">${formatQuestionHtml(stemSource)}</div>
        <p class="qbank-key">Key: <b>${escapeHtml(keyLine(item))}</b></p>
      </article>`;
    })
    .join("");
  renderLiveQuestionMath(list);
  paintBulk();
}

/**
 * Show the bulk bar only while at least one card is checked.
 */
function paintBulk() {
  if (!(bulkEl instanceof HTMLElement)) return;
  const count = selectedIds.size;
  bulkEl.hidden = count === 0;
  if (bulkCountEl) bulkCountEl.textContent = `${count} selected`;
}

/**
 * Keep the hidden scope select aligned with the active tab.
 */
function syncScopeSelect() {
  if (!(scopeEl instanceof HTMLSelectElement)) return;
  if (activeTab === "all") return;
  scopeEl.value = activeTab === "course" ? "course" : activeTab;
}

/**
 * Mark the active module tab.
 */
function paintTabs() {
  document.querySelectorAll("[data-bank-tab]").forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) return;
    const tab = button.dataset.bankTab || "";
    const on = tab === activeTab;
    button.setAttribute("aria-selected", on ? "true" : "false");
    button.classList.toggle("is-on", on);
    const count = tabCounts[tab];
    const label = button.dataset.tabLabel || "";
    button.textContent = count == null ? label : `${label} · ${count}`;
  });
  syncScopeSelect();
}

/**
 * Scope token sent to mc-search.
 * @returns {string}
 */
function searchScope() {
  if (activeTab === "all" || activeTab === "course") return "course";
  return activeTab;
}

/**
 * Load one tab through the same mc-search Import uses, plus staff numeric and poll.
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
  if (query) params.set("q", query);
  if (kind) params.set("kind", kind);
  if (qtype) params.set("type", qtype);
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
    selectedIds = new Set([...selectedIds].filter((key) => items.some((item) => itemKey(item) === key)));
    if (typeChips) typeChips.update(payload.type_counts || []);
    if (summaryEl) {
      const edited = items.filter((item) => item.edited_in_lms).length;
      summaryEl.textContent = `${Number(payload.total || items.length)} questions · ${edited} edited in LMS`;
    }
    renderGrid();
  } catch (error) {
    if (token !== loadToken) return;
    list.innerHTML = `<p class="error compact">${escapeHtml(error instanceof Error ? error.message : String(error))}</p>`;
  } finally {
    if (token === loadToken) list.removeAttribute("aria-busy");
  }
}

/**
 * Tab counts once per visit and after a write, not on each keystroke.
 * @returns {Promise<void>}
 */
async function loadTabCounts() {
  const jobs = TABS.map(async (tab) => {
    const scope = tab === "all" || tab === "course" ? "course" : tab;
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
  paintTabs();
}

/**
 * Reload counts and the open tab after a save, move, or remove.
 */
function refreshAfterWrite() {
  selectedIds = new Set();
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
 * Scope the Add dialog should offer for the open tab.
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
 * Open Edit question for one card, with Prev/Next across the visible grid.
 * @param {string} key
 */
function openEdit(key) {
  const rows = sortItems(items);
  const index = rows.findIndex((item) => itemKey(item) === key);
  if (index < 0) return;
  openQuestionEditor({
    mode: "edit",
    question: rows[index],
    questions: rows,
    index,
    scope: addScope(),
    scopeKnown: true,
    lockSave: true,
  });
}

/**
 * Questions currently checked, as bulk-route rows.
 * @returns {{bank_id: number, question_id: number}[]}
 */
function selectedQuestions() {
  return items
    .filter((item) => selectedIds.has(itemKey(item)))
    .map((item) => ({
      bank_id: Number(item.bank_id || item.source_bank_id || 0),
      question_id: Number(item.question_id || item.id || 0),
    }))
    .filter((row) => row.bank_id && row.question_id);
}

/**
 * POST one bulk action and refresh.
 * @param {Record<string, unknown>} body
 * @returns {Promise<void>}
 */
async function postBulk(body) {
  await api(`/api/staff/class/${classId}/question-bank/bulk`, {
    method: "POST",
    body: JSON.stringify({ ...body, questions: selectedQuestions() }),
  });
  showToast(TOAST_COPY);
  refreshAfterWrite();
}

/**
 * Confirm removal, then delete each selected question.
 * @returns {Promise<void>}
 */
async function removeSelected() {
  if (!(removeDialog instanceof HTMLDialogElement) || !selectedIds.size) return;
  const ok = await new Promise((resolve) => {
    const onClose = () => {
      removeDialog.removeEventListener("close", onClose);
      resolve(removeDialog.returnValue === "ok");
    };
    removeDialog.addEventListener("close", onClose);
    if (typeof removeDialog.showModal === "function") removeDialog.showModal();
    else resolve(false);
  });
  if (!ok) return;
  const rows = selectedQuestions();
  for (const row of rows) {
    await api(`/api/staff/class/${classId}/question-bank/${row.bank_id}/questions/${row.question_id}`, {
      method: "DELETE",
    });
  }
  showToast("Removed from bank.");
  refreshAfterWrite();
}

document.querySelectorAll("[data-bank-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    activeTab = button.getAttribute("data-bank-tab") || "M1";
    selectedIds = new Set();
    paintTabs();
    loadLiveBank().catch(() => {});
  });
});
kindEl?.addEventListener("change", () => {
  loadLiveBank().catch(() => {});
});
sortEl?.addEventListener("change", () => renderGrid());
searchEl?.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    loadLiveBank().catch(() => {});
  }, 200);
});
document.getElementById("qbank-add")?.addEventListener("click", () => openAdd());
list?.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const check = target.closest("[data-qbank-check]");
  if (check instanceof HTMLInputElement) {
    const key = check.dataset.qbankCheck || "";
    if (check.checked) selectedIds.add(key);
    else selectedIds.delete(key);
    paintBulk();
    return;
  }
  const card = target.closest("[data-qbank-key]");
  if (card instanceof HTMLElement) openEdit(card.dataset.qbankKey || "");
});
list?.addEventListener("change", (event) => {
  const check = event.target;
  if (!(check instanceof HTMLInputElement) || !check.dataset.qbankCheck) return;
  if (check.checked) selectedIds.add(check.dataset.qbankCheck);
  else selectedIds.delete(check.dataset.qbankCheck);
  paintBulk();
});
document.getElementById("qbank-bulk-move")?.addEventListener("click", () => {
  const dialog = document.getElementById("qbank-move-dialog");
  if (dialog instanceof HTMLDialogElement) dialog.showModal();
});
document.getElementById("qbank-bulk-kind")?.addEventListener("click", () => {
  const dialog = document.getElementById("qbank-kind-dialog");
  if (dialog instanceof HTMLDialogElement) dialog.showModal();
});
document.getElementById("qbank-bulk-remove")?.addEventListener("click", () => {
  removeSelected().catch((error) => showToast(error instanceof Error ? error.message : String(error)));
});
document.getElementById("qbank-move-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const select = document.getElementById("qbank-move-scope");
  const scope = select instanceof HTMLSelectElement ? select.value : "M1";
  postBulk({ action: "move", bank_scope: scope }).catch((error) => {
    showToast(error instanceof Error ? error.message : String(error));
  });
  document.getElementById("qbank-move-dialog")?.close();
});
document.getElementById("qbank-kind-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const select = document.getElementById("qbank-kind-value");
  const kind = select instanceof HTMLSelectElement ? select.value : "";
  postBulk({ action: "kind", bank_kind: kind }).catch((error) => {
    showToast(error instanceof Error ? error.message : String(error));
  });
  document.getElementById("qbank-kind-dialog")?.close();
});

paintTabs();
loadTabCounts()
  .catch(() => {})
  .finally(() => {
    loadLiveBank().catch(() => {});
  });
