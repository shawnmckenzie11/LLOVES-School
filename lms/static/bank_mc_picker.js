/**
 * Shared module-bank MC picker for live-class Import from bank.
 */
import { api, escapeHtml } from "/static/common.js";

/**
 * Open a searchable picker of confirmed module-bank multiple-choice items.
 *
 * @param {{
 *   classId: number,
 *   moduleNumber?: string,
 *   mode?: string,
 *   onSelect: (item: Record<string, unknown>) => (void|Promise<void>),
 * }} opts
 * @returns {Promise<void>}
 */
export async function openBankMcPicker(opts) {
  const classId = Number(opts?.classId || 0);
  const module = String(opts?.moduleNumber || "M1").toUpperCase();
  const onSelect = opts?.onSelect;
  if (!classId || typeof onSelect !== "function") {
    throw new Error("Bank picker needs a class and an onSelect handler.");
  }

  closeBankMcPicker();
  const modal = document.createElement("div");
  modal.id = "bank-mc-picker-modal";
  modal.className = "bank-mc-picker-modal";
  modal.innerHTML = `
    <div class="bank-mc-picker-backdrop" data-bank-mc-close></div>
    <div class="bank-mc-picker-dialog card" role="dialog" aria-modal="true" aria-labelledby="bank-mc-picker-title">
      <div class="bank-mc-picker-titlebar">
        <h3 id="bank-mc-picker-title">Import from bank · ${escapeHtml(module)}</h3>
        <button type="button" class="secondary live-q-btn" data-bank-mc-close>Close</button>
      </div>
      <div class="bank-mc-picker">
        <div class="bank-mc-picker-head">
          <label class="bank-mc-picker-search" for="bank-mc-picker-q">
            <span>Search</span>
            <input id="bank-mc-picker-q" type="search" autocomplete="off" placeholder="Stem or option text">
          </label>
        </div>
        <p class="hint compact bank-mc-picker-count" id="bank-mc-picker-count">Loading…</p>
        <p class="error compact" id="bank-mc-picker-error" hidden></p>
        <ul class="bank-mc-picker-list" id="bank-mc-picker-list"></ul>
      </div>
    </div>
  `;
  document.body.append(modal);

  const close = () => closeBankMcPicker();
  modal.querySelectorAll("[data-bank-mc-close]").forEach((el) => {
    el.addEventListener("click", close);
  });
  const onKey = (event) => {
    if (event.key === "Escape") close();
  };
  document.addEventListener("keydown", onKey);
  modal._bankMcOnKey = onKey;

  const search = modal.querySelector("#bank-mc-picker-q");
  const list = modal.querySelector("#bank-mc-picker-list");
  const count = modal.querySelector("#bank-mc-picker-count");
  const errorEl = modal.querySelector("#bank-mc-picker-error");

  /**
   * Fetch and paint MCs for the current search box.
   * @returns {Promise<void>}
   */
  async function refresh() {
    if (errorEl instanceof HTMLElement) {
      errorEl.hidden = true;
      errorEl.textContent = "";
    }
    if (count) count.textContent = "Loading…";
    if (list) list.innerHTML = "";
    const query = search instanceof HTMLInputElement ? search.value.trim() : "";
    try {
      const payload = await api(
        `/api/staff/class/${classId}/module-banks/${encodeURIComponent(module)}/mc-search?q=${encodeURIComponent(query)}`
      );
      const items = Array.isArray(payload?.items) ? payload.items : [];
      const importable = items.filter((row) => !row?.skip_reason);
      if (count) {
        const total = Number(payload?.total || importable.length);
        count.textContent = importable.length
          ? `${importable.length} of ${total} questions`
          : payload?.empty
            ? "Ask Admin to attach a module pack."
            : "No importable questions in the confirmed banks for this module.";
      }
      if (!list) return;
      list.innerHTML = importable
        .map((item) => {
          const qid = Number(item.question_id || item.source_question_id || 0);
          const stem = String(item.text || item.question_title || "").trim() || "Untitled";
          const bank = String(item.bank_title || "").trim();
          return `<li class="bank-mc-picker-row">
            <div class="bank-mc-picker-row-main">
              <p class="bank-mc-picker-text">${escapeHtml(stem)}</p>
              <p class="hint compact">${escapeHtml(bank || "Bank")}</p>
            </div>
            <button type="button" class="live-q-btn" data-bank-mc-id="${qid}">Import</button>
          </li>`;
        })
        .join("");
      list.querySelectorAll("[data-bank-mc-id]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const questionId = Number(btn.getAttribute("data-bank-mc-id") || 0);
          const item = importable.find(
            (row) => Number(row.question_id || row.source_question_id || 0) === questionId
          );
          if (!item) return;
          btn.disabled = true;
          try {
            await onSelect(item);
            close();
          } catch (err) {
            if (errorEl instanceof HTMLElement) {
              errorEl.hidden = false;
              errorEl.textContent = err instanceof Error ? err.message : String(err);
            }
            btn.disabled = false;
          }
        });
      });
    } catch (err) {
      if (count) count.textContent = "";
      if (errorEl instanceof HTMLElement) {
        errorEl.hidden = false;
        errorEl.textContent = err instanceof Error ? err.message : String(err);
      }
    }
  }

  let debounce = 0;
  search?.addEventListener("input", () => {
    window.clearTimeout(debounce);
    debounce = window.setTimeout(() => {
      refresh().catch(() => {});
    }, 200);
  });
  await refresh();
  if (search instanceof HTMLInputElement) search.focus();
}

/**
 * Remove the bank picker overlay if it is open.
 */
function closeBankMcPicker() {
  const modal = document.getElementById("bank-mc-picker-modal");
  if (!(modal instanceof HTMLElement)) return;
  if (typeof modal._bankMcOnKey === "function") {
    document.removeEventListener("keydown", modal._bankMcOnKey);
  }
  modal.remove();
}
