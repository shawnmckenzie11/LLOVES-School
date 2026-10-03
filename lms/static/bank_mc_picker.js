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
  contentGroupHeading,
  contentImportDoneText,
  contentModuleView,
  contentPickSummary,
  contentPicksPayload,
  contentRowsView,
  contentSectionHeading,
  contentStalePicks,
  contentStaleText,
  contestEmptyText,
  contestKindView,
  deckLiveProblemIds,
  searchResultsScroll,
} from "/static/content_questions_help.js";
import { mountTypeChips } from "/static/bank_type_chips.js";

/** @type {HTMLElement | null} */
let modalRoot = null;
/**
 * MCK-170 LOW-7: the open picker's Type chips. Destroyed on close or the
 * next mount so its document listener and ResizeObserver don't pile up.
 * @type {{destroy: () => void} | null}
 */
let activeTypeChips = null;

/** Tear down the current picker's Type chips, if any. */
function destroyTypeChips() {
  activeTypeChips?.destroy();
  activeTypeChips = null;
}

/**
 * Remove the picker modal from the document.
 */
function closePickerModal() {
  destroyTypeChips();
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
 * @param {string} [qtype] MCK-170 question type (``rank``, ``mc``, ...). Empty is All.
 * @returns {Promise<ModuleMcSearchResult>}
 */
async function searchModuleMcs(classId, moduleToken, query, kind, qtype) {
  const scope = normalizeBankScope(moduleToken);
  const q = encodeURIComponent(String(query || "").trim());
  const kindParam = encodeURIComponent(String(kind || "").trim().toLowerCase());
  const typeParam = qtype ? `&type=${encodeURIComponent(qtype)}` : "";
  const payload = await api(
    `/api/staff/class/${classId}/module-banks/${scope}/mc-search?q=${q}&kind=${kindParam}${typeParam}`
  );
  return {
    items: Array.isArray(payload?.items) ? payload.items : [],
    total: Number(payload?.total) || 0,
    filtered: Number(payload?.filtered) || 0,
    typeCounts: Array.isArray(payload?.type_counts) ? payload.type_counts : [],
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
 *   load: (module: string) => Promise<{modules?: Array<Record<string, unknown>>,
 *     group?: Record<string, unknown> | null, per_module?: number}>,
 *   currentModule: string,
 *   onDeckIds?: () => Array<Record<string, unknown>>,
 *   onImport: (picks: Array<{question_id: number, module: string}>) => Promise<number>,
 *   onError?: () => Promise<void> | void,
 * }} [opts.contentQuestions] MCK-79: Contest Questions (contest live problems,
 *   top 6 per module or in the course-wide Contest group), import mode only.
 *   MCK-175: shown only while Kind is "Contest Questions", in place of the
 *   type chips and bank list. Loads the current module when shown and each
 *   other group only when the teacher expands it.
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
  shell.className = contentOpts ? "bank-mc-picker has-content-questions" : "bank-mc-picker";
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
                <option value="contest">Contest Questions</option>
                <option value="warmup">Warmup</option>
              </select>
            </label>`
          : ""
      }
      <label class="bank-mc-picker-search">Search
        <input type="search" data-bank-mc-query placeholder="Stem or option text" autocomplete="off">
      </label>
      <div data-bank-type-chips></div>
    </div>
    ${
      contentOpts
        ? `<section class="bank-mc-content" data-bank-mc-content aria-label="Contest Questions" hidden></section>`
        : ""
    }
    <p class="hint compact bank-mc-picker-count" data-bank-mc-count></p>
    <p class="hint compact" data-bank-mc-status hidden></p>
    <ul class="bank-mc-picker-list" data-bank-mc-list></ul>
  `;

  const mountTarget = opts.mount instanceof HTMLElement ? opts.mount : null;
  if (mountTarget) {
    destroyTypeChips();
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
  const typeHost = shell.querySelector("[data-bank-type-chips]");
  /** MCK-170: Type chips; ANDed with scope, Kind and search on the server. */
  const typeChips =
    typeHost instanceof HTMLElement
      ? mountTypeChips(typeHost, {
          view: mode,
          onChange: () => {
            refreshSearch().catch(() => {});
          },
        })
      : null;
  activeTypeChips = typeChips;
  const contentSection = shell.querySelector("[data-bank-mc-content]");
  /** MCK-175: Kind is "Contest Questions" and the block is showing. */
  let contestShown = false;

  /**
   * MCK-175: show the Contest block only while Kind is "Contest Questions",
   * in place of the type chips and the bank list. Each show remounts it
   * fresh (ticks from an earlier visit are cleared); leaving it re-runs the
   * bank search so the last results come back.
   * @param {string} kindValue
   * @returns {boolean} Whether the Contest view is on.
   */
  function applyKindView(kindValue) {
    const view = contestKindView(kindValue, Boolean(contentOpts && contentSection));
    const was = contestShown;
    contestShown = view.contest;
    shell.classList.toggle("is-contest-view", view.contest);
    if (typeHost instanceof HTMLElement) typeHost.hidden = !view.chips;
    [countEl, listEl].forEach((node) => {
      if (node instanceof HTMLElement) node.hidden = !view.list;
    });
    if (view.contest && statusEl instanceof HTMLElement) {
      statusEl.hidden = true;
      statusEl.textContent = "";
    }
    if (!(contentSection instanceof HTMLElement) || !contentOpts) return view.contest;
    contentSection.hidden = !view.contest;
    if (view.contest && !was) {
      contentSection.innerHTML = `
        <div class="bank-mc-content-head">
          <h4>${escapeHtml(contentSectionHeading(CONTENT_PER_MODULE))}</h4>
          <button type="button" class="compact" data-bank-mc-content-import disabled>Import selected</button>
        </div>
        <p class="hint compact" data-bank-mc-content-status>Loading Contest Questions…</p>
        <div data-bank-mc-content-groups></div>`;
      void mountContentQuestions(shell, contentOpts, () => {
        if (!mountTarget) closePickerModal();
      });
    } else if (!view.contest) {
      contentSection.replaceChildren();
    }
    return view.contest;
  }

  /**
   * Paint search hits into the list pane.
   * @param {Record<string, unknown>[]} items
   */
  /**
   * Update the question count line under the search box.
   * @param {number} total
   * @param {number} filtered
   * @param {string} query
   * @param {string} [typeLabel] MCK-170: selected Type chip label, if any.
   */
  function paintCount(total, filtered, query, typeLabel) {
    if (!(countEl instanceof HTMLElement)) return;
    const q = String(query || "").trim();
    const scopeLabel = moduleNumber === "course" ? "Course Wide" : "this module bank";
    if (!total) {
      countEl.textContent = `0 questions in ${scopeLabel}`;
      return;
    }
    if (typeLabel) {
      countEl.textContent = `${filtered} of ${total} question${total === 1 ? "" : "s"} · ${typeLabel}${
        q ? ` · “${q}”` : ""
      }`;
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
        const rank = String(item.type || "") === "rank";
        const rankKeyed = Array.isArray(item.rank_key) && item.rank_key.length > 0;
        const meta = escapeHtml(
          rank
            ? `Rank · ${optionList.length} items · ${rankKeyed ? "answer order set" : "no answer order"}`
            : warmup
            ? `Warmup · ${
                optionList.length ? optionList.join(" / ") : "Open response"
              }`
            : openPrompt && !String(item.correct_answer || "").trim()
              ? "Open prompt"
              : `Answer ${String(item.correct_answer || "?")} · ${Number(item.points || 1)} pt`
        );
        // MCK-169: rank rows are read-only (no Edit control); Import still works.
        const action =
          mode === "import"
            ? `<button type="button" data-bank-mc-select="${qid}">Import</button>`
            : rank
              ? `<span class="hint compact">Read-only</span>`
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
    // MCK-175: the Contest view searches nothing (the block is not filtered).
    if (contestShown) return;
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
      const typeValue = typeChips ? typeChips.value() : "";
      const search = await searchModuleMcs(
        classId,
        moduleNumber,
        queryValue,
        kindValue,
        typeValue
      );
      typeChips?.update(search.typeCounts);
      const typeLabel = typeValue
        ? String(typeHost?.querySelector('[aria-pressed="true"] .bank-type-chip-label')?.textContent || "")
        : "";
      paintCount(search.total, search.filtered, queryValue, typeLabel);
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

  /**
   * MCK-161 LOW-6: with Contest Questions above the bank list, typed
   * search results can land below the fold. The search row is sticky
   * (CSS); after a typed search, bring the results line up under it.
   */
  const revealSearchResults = () => {
    // MCK-175: the block only shows in place of the list, so this is a
    // no-op unless both are ever on screen again.
    if (!contentOpts || contestShown || !(countEl instanceof HTMLElement)) return;
    if (!(contentSection instanceof HTMLElement) || contentSection.hidden) return;
    const head = shell.querySelector(".bank-mc-picker-head");
    if (!(head instanceof HTMLElement)) return;
    const delta = searchResultsScroll({
      resultsTop: countEl.getBoundingClientRect().top,
      headBottom: head.getBoundingClientRect().bottom,
      viewBottom: shell.getBoundingClientRect().bottom,
    });
    if (delta) shell.scrollBy({ top: delta, behavior: "smooth" });
  };

  let debounceTimer = 0;
  queryEl?.addEventListener("input", () => {
    window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(() => {
      refreshSearch()
        .then(() => {
          if (queryEl instanceof HTMLInputElement && queryEl.value.trim()) {
            revealSearchResults();
          }
        })
        .catch(() => {});
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
      if (applyKindView(kindFilter)) return;
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
        kindEl instanceof HTMLSelectElement ? kindEl.value : kindFilter,
        typeChips ? typeChips.value() : ""
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

  // MCK-175: the first search runs the module's usual bank auto-confirm.
  // Contest Questions wait until Kind is "Contest Questions" (they never
  // link banks themselves).
  await refreshSearch();
  queryEl?.focus();
}

/** Default cap shown in the heading until the API answers. */
const CONTENT_PER_MODULE = 6;

/**
 * MCK-79: list Contest Questions groups (modules with contest live
 * problems, this class's module, and the course-wide Contest group) as
 * collapsed groups; load a group's top 6 only when it opens (the class's
 * module opens first, or Contest when that is empty). Ticked rows import
 * through ``onImport`` as one batch.
 * @param {HTMLElement} shell
 * @param {{load: Function, currentModule: string, onImport: Function,
 *   onDeckIds?: Function, onError?: Function}} contentOpts
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
  const onDeck = () =>
    deckLiveProblemIds(
      typeof contentOpts.onDeckIds === "function" ? contentOpts.onDeckIds() : []
    );
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
  const current = String(contentOpts.currentModule || "").toUpperCase();
  let first;
  try {
    first = await contentOpts.load(current);
  } catch (err) {
    setStatus(friendlyApiError(err));
    return;
  }
  const perModule = Number(first?.per_module) || CONTENT_PER_MODULE;
  const view = contentModuleView(first?.modules, current);
  /** MCK-175: one bank.contest.empty line, never per-module empty rows. */
  const showAllEmpty = () => {
    groupsEl.replaceChildren();
    if (importBtn instanceof HTMLElement) importBtn.hidden = true;
    setStatus(contestEmptyText(current));
  };
  if (!view.length) {
    showAllEmpty();
    return;
  }
  setStatus("");
  groupsEl.innerHTML = view
    .map(
      (group) => `<details class="bank-mc-content-group" data-content-group="${escapeHtml(
        group.module
      )}" data-content-label="${escapeHtml(group.label)}" data-content-count="${group.count}"${group.current ? ' data-content-current="1"' : ""}${group.open ? " open" : ""}>
        <summary>${escapeHtml(group.heading)}</summary>
        <div data-content-body></div>
      </details>`
    )
    .join("");
  /** @type {Map<string, Record<string, unknown> | null>} */
  const loaded = new Map();
  /** @type {Map<string, Promise<void>>} */
  const loading = new Map();

  /**
   * Paint one loaded group's rows (or its empty line) into its body.
   * @param {HTMLDetailsElement} details
   * @param {Record<string, unknown> | null} group
   */
  const paintGroup = (details, group) => {
    const body = details.querySelector("[data-content-body]");
    const summary = details.querySelector("summary");
    if (!(body instanceof HTMLElement)) return;
    const label = details.getAttribute("data-content-label") || "This module";
    const rowsView = contentRowsView(group, perModule, onDeck());
    // MCK-175: a group that comes back empty (it changed since the list
    // loaded) drops out; when none are left, one empty line remains.
    details.hidden = !rowsView.rows.length;
    if (!groupsEl.querySelector("details[data-content-group]:not([hidden])")) {
      showAllEmpty();
      return;
    }
    if (summary) {
      summary.textContent = contentGroupHeading(
        label,
        details.hasAttribute("data-content-current"),
        rowsView.rows.length
      );
    }
    if (!rowsView.rows.length) {
      body.innerHTML = `<p class="hint compact">${escapeHtml(rowsView.empty)}</p>`;
      return;
    }
    const ticked = new Set(
      [...body.querySelectorAll("input[data-content-pick]:checked")].map((node) =>
        node.getAttribute("data-content-pick")
      )
    );
    body.innerHTML = `<ul class="bank-mc-content-list">${rowsView.rows
      .map((row) => {
        const stem =
          questionFieldHtml(row.item, "text") || formatQuestionHtml(itemPreview(row.item));
        const checked = ticked.has(String(row.id)) ? " checked" : "";
        return `<li class="bank-mc-picker-row bank-mc-content-row${
          row.onDeck ? " is-on-deck" : ""
        }">
          <label>
            <input type="checkbox" data-content-pick="${row.id}" data-content-module="${escapeHtml(
              row.module
            )}" aria-label="${escapeHtml(`${label} question ${row.rank}`)}"${checked}>
            <span class="bank-mc-picker-row-main">
              ${row.title ? `<span class="bank-mc-picker-title">${escapeHtml(row.title)}</span>` : ""}
              <span class="bank-mc-picker-text live-question-html">${stem}</span>
              <span class="hint compact">${escapeHtml(`${label} · #${row.rank} · ${row.meta}`)}</span>
            </span>
          </label>
        </li>`;
      })
      .join("")}</ul>`;
    void renderLiveQuestionMath(body);
  };

  /**
   * Fetch one module's top questions once, then paint them.
   * @param {HTMLDetailsElement} details
   * @param {Record<string, unknown> | null} [preloaded]
   * @returns {Promise<void>}
   */
  const ensureGroup = (details, preloaded) => {
    const module = details.getAttribute("data-content-group") || "";
    if (loaded.has(module)) return Promise.resolve();
    if (loading.has(module)) return loading.get(module);
    const body = details.querySelector("[data-content-body]");
    if (!Number(details.getAttribute("data-content-count") || 0)) {
      loaded.set(module, null);
      paintGroup(details, { module, label: details.getAttribute("data-content-label"), items: [] });
      return Promise.resolve();
    }
    if (preloaded !== undefined) {
      loaded.set(module, preloaded);
      paintGroup(details, preloaded);
      return Promise.resolve();
    }
    if (body instanceof HTMLElement) body.innerHTML = `<p class="hint compact">Loading…</p>`;
    const job = Promise.resolve(contentOpts.load(module))
      .then((payload) => {
        const group = payload?.group || null;
        loaded.set(module, group);
        paintGroup(details, group);
      })
      .catch((err) => {
        if (body instanceof HTMLElement) {
          body.innerHTML = `<p class="hint compact">${escapeHtml(friendlyApiError(err))}</p>`;
        }
      })
      .finally(() => {
        loading.delete(module);
      });
    loading.set(module, job);
    return job;
  };

  /** Repaint loaded groups (keeps ticks) so "on deck" marks are current. */
  const repaintLoaded = () => {
    groupsEl.querySelectorAll("details[data-content-group]").forEach((details) => {
      const module = details.getAttribute("data-content-group") || "";
      if (loaded.has(module) && details instanceof HTMLDetailsElement) {
        paintGroup(details, loaded.get(module) || null);
      }
    });
  };

  /**
   * MCK-161 LOW-8: re-fetch every loaded group and repaint it (ticks on
   * rows that are still listed are kept).
   * @returns {Promise<Map<string, number[]>>} Ids now listed, per group.
   */
  const reloadLoadedGroups = async () => {
    /** @type {Map<string, number[]>} */
    const present = new Map();
    const jobs = [];
    groupsEl.querySelectorAll("details[data-content-group]").forEach((details) => {
      const module = details.getAttribute("data-content-group") || "";
      if (!loaded.has(module) || !(details instanceof HTMLDetailsElement)) return;
      jobs.push(
        Promise.resolve(contentOpts.load(module)).then((payload) => {
          const group = payload?.group || null;
          loaded.set(module, group);
          paintGroup(details, group);
          present.set(
            module,
            (Array.isArray(group?.items) ? group.items : [])
              .slice(0, perModule)
              .map((item) => Number(item?.question_id || 0))
          );
        })
      );
    });
    await Promise.all(jobs);
    paintButton();
    return present;
  };

  groupsEl.querySelectorAll("details[data-content-group]").forEach((details) => {
    if (!(details instanceof HTMLDetailsElement)) return;
    const module = details.getAttribute("data-content-group") || "";
    if (module === current && first?.group) {
      void ensureGroup(details, first.group);
    } else if (details.open) {
      void ensureGroup(details);
    }
    details.addEventListener("toggle", () => {
      if (details.open) void ensureGroup(details);
    });
  });
  groupsEl.addEventListener("change", paintButton);
  paintButton();
  importBtn?.addEventListener("click", async () => {
    const picks = contentPicksPayload(checkedPicks());
    if (!picks.length || !(importBtn instanceof HTMLButtonElement)) return;
    importBtn.disabled = true;
    setStatus("Importing…");
    const deckBefore = onDeck();
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
      // The batch is all-or-nothing: the server rolled it back. Reload the
      // deck so cards and "on deck" marks match. Ticks stay for a retry
      // unless a pick is now on the deck (then clear those so a retry
      // can't double it).
      if (typeof contentOpts.onError === "function") {
        try {
          await contentOpts.onError();
        } catch {
          /* status below still explains */
        }
      }
      const deckNow = onDeck();
      const landed = picks.filter(
        (pick) => deckNow.has(pick.question_id) && !deckBefore.has(pick.question_id)
      );
      landed.forEach((pick) => {
        groupsEl
          .querySelectorAll(`input[data-content-pick="${pick.question_id}"]`)
          .forEach((node) => {
            if (node instanceof HTMLInputElement) node.checked = false;
          });
      });
      // MCK-161 LOW-8: a pick may have left its group's top 6 since the
      // list loaded. Reload the loaded groups; picks that vanished lose
      // their tick (the repaint drops their rows) and the status says so.
      const stale = await reloadLoadedGroups()
        .then((present) => contentStalePicks(picks, present))
        .catch(() => []);
      if (!stale.length) repaintLoaded();
      setStatus(
        stale.length
          ? contentStaleText(stale.length)
          : landed.length
            ? `${friendlyApiError(err)} ${landed.length} landed and stay on the deck; their ticks were cleared.`
            : friendlyApiError(err)
      );
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
