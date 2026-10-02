/**
 * Shared module-bank MC picker for Run Live Class import and Question Banks browse.
 */

/**
 * Shorten HTML error pages from failed API calls.
 * @param {unknown} err
 * @returns {string}
 */
function friendlyApiError(err) {
  const raw = err instanceof Error ? err.message : String(err || "Request failed");
  if (raw.includes("<!doctype html>") || raw.includes("<html")) {
    return "Server route not found. Restart the LMS dev server and hard-refresh.";
  }
  return raw;
}

import { api, escapeHtml, formatQuestionHtml, questionFieldHtml, questionImageHtml, renderLiveQuestionMath } from "/static/common.js";
import {
  contentGroupView,
  contentImportDoneText,
  contentPickSummary,
  contentPicksPayload,
} from "/static/content_questions_help.js";

/** @type {HTMLElement | null} */
let modalRoot = null;

/**
 * Remove the picker modal from the document.
 */
function closePickerModal() {
  modalRoot?.remove();
  modalRoot = null;
}

/**
 * Derive a one-line preview label for one normalized MC row.
 * @param {Record<string, unknown>} item
 * @returns {string}
 */
function itemPreview(item) {
  return String(item.text || item.question_title || "").trim() || "Untitled MC";
}

/**
 * @typedef {{ items: Record<string, unknown>[], total: number, filtered: number }} ModuleMcSearchResult
 */

/**
 * Normalize a Bank scope token to ``M1``–``M8`` or ``course``.
 * @param {unknown} raw
 * @returns {string}
 */
function normalizeBankScope(raw) {
  const token = String(raw || "").trim();
  if (token.toLowerCase() === "course") return "course";
  const upper = token.toUpperCase();
  return /^M[1-8]$/.test(upper) ? upper : "M1";
}

/**
 * Option list for the shared Bank scope control.
 * @param {string} selected
 * @returns {string}
 */
function bankScopeOptionsHtml(selected) {
  const current = normalizeBankScope(selected);
  const modules = ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"]
    .map((token, index) => {
      const picked = token === current ? " selected" : "";
      return `<option value="${token}"${picked}>Module ${index + 1}</option>`;
    })
    .join("");
  const coursePicked = current === "course" ? " selected" : "";
  return `${modules}<option value="course"${coursePicked}>Course Wide</option>`;
}

/**
 * Fetch module-scoped MC search results with counts.
 * @param {number} classId
 * @param {string} moduleToken
 * @param {string} query
 * @param {string} [kind] ``standard``, ``contest``, or ``warmup``. Empty excludes warmup.
 * @returns {Promise<ModuleMcSearchResult>}
 */
async function searchModuleMcs(classId, moduleToken, query, kind) {
  const scope = normalizeBankScope(moduleToken);
  const q = encodeURIComponent(String(query || "").trim());
  const kindParam = encodeURIComponent(String(kind || "").trim().toLowerCase());
  const payload = await api(
    `/api/staff/class/${classId}/module-banks/${scope}/mc-search?q=${q}&kind=${kindParam}`
  );
  return {
    items: Array.isArray(payload?.items) ? payload.items : [],
    total: Number(payload?.total) || 0,
    filtered: Number(payload?.filtered) || 0,
  };
}

/**
 * Load confirmed/suggested banks so the module selector can warn when unconfirmed.
 * @param {number} classId
 * @param {string} moduleToken
 */

/**
 * Persist teacher confirmation for suggested module banks.
 * @param {number} classId
 * @param {string} moduleToken
 * @param {number[]} bankIds
 */

/**
 * Build bank ids to confirm: recommended test pools plus any already linked banks.
 * @param {object} bankStatus
 * @returns {number[]}
 */
function moduleBankConfirmIds(bankStatus) {
  const confirmed = Array.isArray(bankStatus?.confirmed) ? bankStatus.confirmed : [];
  const recommended = Array.isArray(bankStatus?.recommended)
    ? bankStatus.recommended
    : [];
  const suggested = Array.isArray(bankStatus?.suggested) ? bankStatus.suggested : [];
  const source = recommended.length ? recommended : suggested;
  const ids = [
    ...confirmed.map((row) => Number(row.bank_id)),
    ...source.map((row) => Number(row.bank_id)),
  ].filter((id) => id > 0);
  return [...new Set(ids)];
}

async function confirmModuleBanks(classId, moduleToken, bankIds) {
  const module = String(moduleToken || "M1").toUpperCase();
  return api(`/api/staff/class/${classId}/module-banks/confirm`, {
    method: "POST",
    body: JSON.stringify({ module, bank_ids: bankIds }),
  });
}

async function loadModuleBankStatus(classId, moduleToken) {
  const module = String(moduleToken || "M1").toUpperCase();
  return api(`/api/staff/class/${classId}/module-banks?module=${module}`);
}

/**
 * Mount the searchable MC picker into a container or open a modal.
 * @param {object} opts
 * @param {number} opts.classId
 * @param {string} opts.moduleNumber Module token such as ``M1``.
 * @param {"import"|"browse"} [opts.mode]
 * @param {(item: Record<string, unknown>) => void | Promise<void>} opts.onSelect
 * @param {HTMLElement} [opts.mount] When set, render inline instead of modal.
 * @param {boolean} [opts.showModuleSelector] Allow changing module (browse tab).
 * @param {{
 *   load: () => Promise<{groups?: Array<Record<string, unknown>>, per_module?: number}>,
 *   currentModule: string,
 *   onImport: (picks: Array<{question_id: number, module: string}>) => Promise<number>,
 * }} [opts.contentQuestions] MCK-79: Content Questions (top 6 per module),
 *   import mode only.
 */
export async function mountBankMcPicker(opts) {
  const classId = Number(opts.classId || 0);
  const mode = opts.mode === "browse" ? "browse" : "import";
  const onSelect = typeof opts.onSelect === "function" ? opts.onSelect : () => {};
  const showModuleSelector = Boolean(opts.showModuleSelector);
  const showScope = mode === "import" || showModuleSelector;
  const showKind = mode === "import";
  let moduleNumber = normalizeBankScope(opts.moduleNumber || "M1");
  let kindFilter = "";
  const contentOpts =
    mode === "import" &&
    opts.contentQuestions &&
    typeof opts.contentQuestions.load === "function" &&
    typeof opts.contentQuestions.onImport === "function"
      ? opts.contentQuestions
      : null;

  const shell = document.createElement("div");
  shell.className = "bank-mc-picker";
  shell.innerHTML = `
    <div class="bank-mc-picker-head">
      ${
        showScope
          ? `<label class="bank-mc-picker-module">Bank scope
              <select data-bank-mc-module aria-label="Bank scope">
                ${bankScopeOptionsHtml(moduleNumber)}
              </select>
            </label>`
          : `<p class="hint compact">Module ${escapeHtml(moduleNumber)} banks only</p>`
      }
      ${
        showKind
          ? `<label class="bank-mc-picker-kind">Kind
              <select data-bank-mc-kind aria-label="Kind">
                <option value="" selected>Core Math</option>
                <option value="standard">Custom</option>
                <option value="contest">Contest</option>
                <option value="warmup">Warmup</option>
              </select>
            </label>`
          : ""
      }
      <label class="bank-mc-picker-search">Search
        <input type="search" data-bank-mc-query placeholder="Stem or option text" autocomplete="off">
      </label>
    </div>
    ${
      contentOpts
        ? `<section class="bank-mc-content" data-bank-mc-content aria-label="Content Questions">
            <div class="bank-mc-content-head">
              <h4>Content Questions · top ${CONTENT_PER_MODULE} per module</h4>
              <button type="button" class="compact" data-bank-mc-content-import disabled>Import selected</button>
            </div>
            <p class="hint compact" data-bank-mc-content-status>Loading Content Questions…</p>
            <div data-bank-mc-content-groups></div>
          </section>`
        : ""
    }
    <p class="hint compact bank-mc-picker-count" data-bank-mc-count></p>
    <p class="hint compact" data-bank-mc-status hidden></p>
    <ul class="bank-mc-picker-list" data-bank-mc-list></ul>
  `;

  const mountTarget = opts.mount instanceof HTMLElement ? opts.mount : null;
  if (mountTarget) {
    mountTarget.replaceChildren(shell);
  } else {
    closePickerModal();
    modalRoot = document.createElement("div");
    modalRoot.className = "bank-mc-picker-modal";
    modalRoot.innerHTML = `
      <div class="bank-mc-picker-backdrop" data-bank-mc-close></div>
      <div class="bank-mc-picker-dialog card" role="dialog" aria-label="${mode === "import" ? "Import from bank" : "Module bank MCs"}">
        <header class="bank-mc-picker-titlebar">
          <h3>${mode === "import" ? "Import from bank" : "Module bank MCs"}</h3>
          <button type="button" class="secondary compact" data-bank-mc-close aria-label="Close">Close</button>
        </header>
      </div>
    `;
    modalRoot.querySelector(".bank-mc-picker-dialog")?.appendChild(shell);
    document.body.appendChild(modalRoot);
    modalRoot.querySelectorAll("[data-bank-mc-close]").forEach((node) => {
      node.addEventListener("click", closePickerModal);
    });
  }

  const listEl = shell.querySelector("[data-bank-mc-list]");
  const countEl = shell.querySelector("[data-bank-mc-count]");
  const statusEl = shell.querySelector("[data-bank-mc-status]");
  const queryEl = shell.querySelector("[data-bank-mc-query]");
  const moduleEl = shell.querySelector("[data-bank-mc-module]");
  const kindEl = shell.querySelector("[data-bank-mc-kind]");

  /**
   * Paint search hits into the list pane.
   * @param {Record<string, unknown>[]} items
   */
  /**
   * Update the question count line under the search box.
   * @param {number} total
   * @param {number} filtered
   * @param {string} query
   */
  function paintCount(total, filtered, query) {
    if (!(countEl instanceof HTMLElement)) return;
    const q = String(query || "").trim();
    const scopeLabel = moduleNumber === "course" ? "Course Wide" : "this module bank";
    if (!total) {
      countEl.textContent = `0 questions in ${scopeLabel}`;
      return;
    }
    if (q) {
      countEl.textContent =
        filtered === total
          ? `${filtered} question${filtered === 1 ? "" : "s"} match “${q}”`
          : `${filtered} of ${total} question${total === 1 ? "" : "s"} match “${q}”`;
      return;
    }
    countEl.textContent = `${total} question${total === 1 ? "" : "s"}`;
  }

  function paintList(items) {
    if (!(listEl instanceof HTMLElement)) return;
    if (!items.length) {
      listEl.innerHTML =
        moduleNumber === "course"
          ? `<li class="hint">No Course Wide questions match.</li>`
          : `<li class="hint">No MCs found. Confirm module banks first.</li>`;
      return;
    }
    listEl.innerHTML = items
      .map((item) => {
        const qid = Number(item.question_id || 0);
        const title = String(item.question_title || "").trim();
        const stemLabel =
          questionFieldHtml(item, "text") || formatQuestionHtml(itemPreview(item));
        const warmup = String(item.kind || "") === "warmup";
        const openPrompt = Boolean(item.curriculum_open) || String(item.type || "") === "poll";
        const label =
          warmup && title
            ? `<span class="bank-mc-picker-title">${escapeHtml(title)}</span>${stemLabel}`
            : stemLabel;
        const optionList = Array.isArray(item.options) ? item.options : [];
        const meta = escapeHtml(
          warmup
            ? `Warmup · ${
                optionList.length ? optionList.join(" / ") : "Open response"
              }`
            : openPrompt && !String(item.correct_answer || "").trim()
              ? "Open prompt"
              : `Answer ${String(item.correct_answer || "?")} · ${Number(item.points || 1)} pt`
        );
        const action =
          mode === "import"
            ? `<button type="button" data-bank-mc-select="${qid}">Import</button>`
            : `<button type="button" data-bank-mc-select="${qid}">Edit</button>`;
        const thumb = questionImageHtml(item.image_url, { variant: "thumb" });
        return `<li class="bank-mc-picker-row" data-bank-mc-title="${escapeHtml(title)}">
          <div class="bank-mc-picker-row-main">
            ${thumb}
            <div class="bank-mc-picker-text live-question-html">${label}</div>
            <p class="hint compact">${meta}</p>
          </div>
          ${action}
        </li>`;
      })
      .join("");
    void renderLiveQuestionMath(listEl);
  }

  /**
   * Run one debounced search against the module-scoped API.
   */
  async function refreshSearch() {
    if (statusEl instanceof HTMLElement) {
      statusEl.hidden = false;
      statusEl.textContent = "Searching…";
    }
    try {
      const bankStatus =
        moduleNumber === "course"
          ? null
          : await loadModuleBankStatus(classId, moduleNumber);
      if (mode === "import" && bankStatus?.needs_confirmation) {
        const ids = moduleBankConfirmIds(bankStatus);
        if (ids.length) {
          await confirmModuleBanks(classId, moduleNumber, ids);
        }
      }
      const linkedStatus =
        mode === "import" && bankStatus?.needs_confirmation
          ? await loadModuleBankStatus(classId, moduleNumber)
          : bankStatus;
      if (linkedStatus?.needs_confirmation && statusEl instanceof HTMLElement) {
        const pickRows = Array.isArray(linkedStatus.recommended) && linkedStatus.recommended.length
          ? linkedStatus.recommended
          : Array.isArray(linkedStatus.suggested)
            ? linkedStatus.suggested
            : [];
        const labels = pickRows
          .map((row) => String(row.title || row.import_key || row.bank_id || ""))
          .filter(Boolean)
          .join(", ");
        statusEl.innerHTML = labels
          ? `Link these banks to ${escapeHtml(moduleNumber)}? ${escapeHtml(labels)} `
          : `No banks matched ${escapeHtml(moduleNumber)}. Adjust titles or confirm manually. `;
        if (pickRows.length) {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "secondary compact";
          btn.textContent = "Confirm banks";
          btn.addEventListener("click", () => {
            const ids = moduleBankConfirmIds(linkedStatus);
            confirmModuleBanks(classId, moduleNumber, ids)
              .then(() => refreshSearch())
              .catch((err) => {
                statusEl.textContent = friendlyApiError(err);
              });
          });
          statusEl.appendChild(btn);
        }
      } else if (statusEl instanceof HTMLElement) {
        statusEl.hidden = true;
        statusEl.textContent = "";
      }
      const queryValue =
        queryEl instanceof HTMLInputElement ? queryEl.value : "";
      const kindValue =
        kindEl instanceof HTMLSelectElement ? kindEl.value : kindFilter;
      const search = await searchModuleMcs(
        classId,
        moduleNumber,
        queryValue,
        kindValue
      );
      paintCount(search.total, search.filtered, queryValue);
      paintList(search.items);
    } catch (err) {
      if (statusEl instanceof HTMLElement) {
        statusEl.hidden = false;
        statusEl.textContent =
          friendlyApiError(err);
      }
      if (listEl instanceof HTMLElement) listEl.innerHTML = "";
    }
  }

  let debounceTimer = 0;
  queryEl?.addEventListener("input", () => {
    window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(() => {
      refreshSearch().catch(() => {});
    }, 220);
  });
  moduleEl?.addEventListener("change", () => {
    if (moduleEl instanceof HTMLSelectElement) {
      moduleNumber = normalizeBankScope(moduleEl.value);
      refreshSearch().catch(() => {});
    }
  });
  kindEl?.addEventListener("change", () => {
    if (kindEl instanceof HTMLSelectElement) {
      kindFilter = String(kindEl.value || "");
      refreshSearch().catch(() => {});
    }
  });
  listEl?.addEventListener("click", async (event) => {
    const btn =
      event.target instanceof Element
        ? event.target.closest("[data-bank-mc-select]")
        : null;
    if (!(btn instanceof HTMLElement)) return;
    const qid = Number(btn.getAttribute("data-bank-mc-select") || 0);
    if (!qid) return;
    if (statusEl instanceof HTMLElement) {
      statusEl.hidden = false;
      statusEl.textContent = "Importing…";
    }
    btn.setAttribute("disabled", "disabled");
    try {
      const search = await searchModuleMcs(
        classId,
        moduleNumber,
        queryEl instanceof HTMLInputElement ? queryEl.value : "",
        kindEl instanceof HTMLSelectElement ? kindEl.value : kindFilter
      );
      const picked = search.items.find((row) => Number(row.question_id) === qid);
      if (!picked) {
        throw new Error("That question is no longer in the module bank search results.");
      }
      await onSelect({ ...picked, module: moduleNumber });
      if (!mountTarget) closePickerModal();
    } catch (err) {
      if (statusEl instanceof HTMLElement) {
        statusEl.hidden = false;
        statusEl.textContent = friendlyApiError(err);
      }
    } finally {
      btn.removeAttribute("disabled");
    }
  });

  if (contentOpts) {
    void mountContentQuestions(shell, contentOpts, () => {
      if (!mountTarget) closePickerModal();
    });
  }
  await refreshSearch();
  queryEl?.focus();
}

/** Default cap shown in the heading until the API answers. */
const CONTENT_PER_MODULE = 6;

/**
 * MCK-79: paint each module's top Content Questions with checkboxes and
 * an Import selected button. Import goes through ``onImport``.
 * @param {HTMLElement} shell
 * @param {{load: Function, currentModule: string, onImport: Function}} contentOpts
 * @param {() => void} onDone
 */
async function mountContentQuestions(shell, contentOpts, onDone) {
  const section = shell.querySelector("[data-bank-mc-content]");
  const groupsEl = shell.querySelector("[data-bank-mc-content-groups]");
  const statusEl = shell.querySelector("[data-bank-mc-content-status]");
  const importBtn = shell.querySelector("[data-bank-mc-content-import]");
  if (!(section instanceof HTMLElement) || !(groupsEl instanceof HTMLElement)) return;
  const setStatus = (text) => {
    if (!(statusEl instanceof HTMLElement)) return;
    statusEl.hidden = !text;
    statusEl.textContent = text || "";
  };
  const checkedPicks = () =>
    [...groupsEl.querySelectorAll("input[data-content-pick]:checked")].map((node) => ({
      id: node.getAttribute("data-content-pick"),
      module: node.getAttribute("data-content-module"),
    }));
  const paintButton = () => {
    if (!(importBtn instanceof HTMLButtonElement)) return;
    const summary = contentPickSummary(contentPicksPayload(checkedPicks()));
    importBtn.textContent = summary.label;
    importBtn.disabled = summary.disabled;
  };
  let payload;
  try {
    payload = await contentOpts.load();
  } catch (err) {
    setStatus(friendlyApiError(err));
    return;
  }
  const view = contentGroupView(
    payload?.groups,
    contentOpts.currentModule,
    Number(payload?.per_module) || CONTENT_PER_MODULE
  );
  if (!view.length) {
    setStatus("No modules to show yet.");
    return;
  }
  setStatus("");
  groupsEl.innerHTML = view
    .map((group) => {
      const rows = group.rows
        .map((row) => {
          const stem =
            questionFieldHtml(row.item, "text") || formatQuestionHtml(itemPreview(row.item));
          return `<li class="bank-mc-picker-row bank-mc-content-row">
            <label>
              <input type="checkbox" data-content-pick="${row.id}" data-content-module="${escapeHtml(
                row.module
              )}" aria-label="${escapeHtml(`${group.label} question ${row.rank}`)}">
              <span class="bank-mc-picker-row-main">
                <span class="bank-mc-picker-text live-question-html">${stem}</span>
                <span class="hint compact">${escapeHtml(`${group.label} · #${row.rank} · ${row.meta}`)}</span>
              </span>
            </label>
          </li>`;
        })
        .join("");
      const body = group.rows.length
        ? `<ul class="bank-mc-content-list">${rows}</ul>`
        : `<p class="hint compact">${escapeHtml(group.empty)}</p>`;
      return `<details class="bank-mc-content-group" data-content-group="${escapeHtml(
        group.module
      )}"${group.open ? " open" : ""}>
        <summary>${escapeHtml(group.heading)}</summary>
        ${body}
      </details>`;
    })
    .join("");
  void renderLiveQuestionMath(groupsEl);
  groupsEl.addEventListener("change", paintButton);
  paintButton();
  importBtn?.addEventListener("click", async () => {
    const picks = contentPicksPayload(checkedPicks());
    if (!picks.length || !(importBtn instanceof HTMLButtonElement)) return;
    importBtn.disabled = true;
    setStatus("Importing…");
    try {
      const count = await contentOpts.onImport(picks);
      setStatus(contentImportDoneText(Number(count) || picks.length));
      groupsEl
        .querySelectorAll("input[data-content-pick]:checked")
        .forEach((node) => {
          if (node instanceof HTMLInputElement) node.checked = false;
        });
      onDone();
    } catch (err) {
      setStatus(friendlyApiError(err));
    } finally {
      paintButton();
    }
  });
}

/**
 * Open the picker in a modal dialog.
 * @param {object} opts
 * @param {number} opts.classId
 * @param {string} opts.moduleNumber
 * @param {"import"|"browse"} [opts.mode]
 * @param {(item: Record<string, unknown>) => void | Promise<void>} opts.onSelect
 */
export async function openBankMcPicker(opts) {
  return mountBankMcPicker({ ...opts, mount: null });
}
