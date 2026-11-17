/**
 * Staff question-bank tools on the bank tab and during a live class.
 *
 * Fetches only when a teacher opens or clicks a control. It is not part of
 * the student poll.
 */
import { api, escapeHtml } from "/static/common.js";

/**
 * Wire Generate ranking and Recently removed on the question-bank tab.
 * @param {{classId: number, getScope: () => string, getType: () => string, getKind: () => string, getSelection: () => {any: boolean, ids: number[]}, onChanged: () => void, showMessage: (text: string) => void}} opts
 */
export function bindQuestionBankBot(opts) {
  const run = () => {
    generateRanking(opts.classId, selectionBody(opts), opts.showMessage, () => {
      opts.onChanged?.();
    }).catch((error) => opts.showMessage(errorText(error)));
  };
  document.getElementById("qbank-generate")?.addEventListener("click", run);
  document.getElementById("qbank-bulk-generate")?.addEventListener("click", run);
  document.getElementById("qbank-removed")?.addEventListener("click", () => {
    const host = document.getElementById("qbank-bot-removed");
    const panel = document.getElementById("qbank-bot");
    if (panel instanceof HTMLElement) panel.hidden = false;
    const title = document.getElementById("qbank-bot-title");
    if (title) title.textContent = "Recently removed";
    loadRemoved(opts.classId, host, opts.showMessage, () => opts.onChanged?.()).catch((error) =>
      opts.showMessage(errorText(error))
    );
  });
}

/**
 * @param {{getScope: () => string, getType: () => string, getKind: () => string, getSelection: () => {any: boolean, ids: number[]}, showMessage: (text: string) => void}} opts
 * @returns {Record<string, unknown>|null}
 */
function selectionBody(opts) {
  const picked = opts.getSelection();
  if (picked.any && !picked.ids.length) {
    opts.showMessage("Select multiple-choice questions.");
    return null;
  }
  if (picked.ids.length) {
    return { question_ids: picked.ids, bank_scope: opts.getScope() };
  }
  const type = String(opts.getType() || "");
  if (type && type !== "mc") {
    opts.showMessage("Choose Multiple choice, or select multiple-choice questions.");
    return null;
  }
  return {
    bank_scope: opts.getScope() || "M1",
    question_type: "mc",
    kind: opts.getKind() || "",
  };
}

/**
 * Ask for drafts, then paint them. A missing key is explained and not retried.
 * @param {number} classId
 * @param {Record<string, unknown>|null} body
 * @param {(text: string) => void} showMessage
 * @param {() => void} onChanged
 * @returns {Promise<void>}
 */
async function generateRanking(classId, body, showMessage, onChanged) {
  if (!body) return;
  document.querySelectorAll("#qbank-bot-removed, #live-bank-bot-removed-list").forEach((node) => {
    node.innerHTML = "";
  });
  const panel = openPanel();
  const payload = await api(`/api/staff/class/${classId}/bank-bot/generate`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  const note = payload.truncated
    ? "Drafts use the first multiple-choice questions in this filter. They stay out of the bank until you add them."
    : "These drafts are not in the bank until you add them.";
  showMessage(note);
  const status = panel?.querySelector("[data-bank-bot-status]");
  if (status) status.textContent = note;
  paintDrafts(panel?.querySelector("[data-bank-bot-drafts]"), payload.drafts || [], classId, showMessage, onChanged);
}

/**
 * Show the question-bank panel, or the live dialog's draft area.
 * @returns {HTMLElement|null}
 */
function openPanel() {
  const bank = document.getElementById("qbank-bot");
  if (bank instanceof HTMLElement && document.getElementById("qbank-generate")) {
    bank.hidden = false;
    const title = document.getElementById("qbank-bot-title");
    if (title) title.textContent = "Ranking drafts";
    return bank;
  }
  const live = document.getElementById("live-bank-bot-dialog");
  return live instanceof HTMLElement ? live : null;
}

/**
 * @param {number} classId
 * @param {HTMLElement|null} host
 * @param {(text: string) => void} showMessage
 * @param {() => void} onChanged
 * @returns {Promise<void>}
 */
async function loadRemoved(classId, host, showMessage, onChanged) {
  if (!(host instanceof HTMLElement)) return;
  const payload = await api(`/api/staff/class/${classId}/bank-bot/removed`);
  const rows = Array.isArray(payload.questions) ? payload.questions : [];
  if (!rows.length) {
    host.innerHTML = `<p class="hint">No recently removed questions.</p>`;
    return;
  }
  host.innerHTML = `<ul class="bank-bot-list">${rows
    .map(
      (row) => `<li class="bank-bot-row">
        <span>${escapeHtml(row.stem || "Question")}</span>
        <button type="button" data-bank-bot-undo="${Number(row.question_id)}">Undo</button>
      </li>`
    )
    .join("")}</ul>`;
  host.querySelectorAll("[data-bank-bot-undo]").forEach((button) => {
    button.addEventListener("click", () => {
      const questionId = Number(button.getAttribute("data-bank-bot-undo"));
      restoreQuestions(classId, [questionId], showMessage, onChanged)
        .then(() => loadRemoved(classId, host, showMessage, onChanged))
        .catch((error) => showMessage(errorText(error)));
    });
  });
}

/**
 * @param {number} classId
 * @param {number[]} questionIds
 * @param {(text: string) => void} showMessage
 * @param {() => void} onChanged
 * @returns {Promise<void>}
 */
async function restoreQuestions(classId, questionIds, showMessage, onChanged) {
  await api(`/api/staff/class/${classId}/bank-bot/restore`, {
    method: "POST",
    body: JSON.stringify({ question_ids: questionIds }),
  });
  showMessage("Restored to the bank.");
  onChanged?.();
}

/**
 * @param {Element|null|undefined} host
 * @param {Array<Record<string, unknown>>} drafts
 * @param {number} classId
 * @param {(text: string) => void} showMessage
 * @param {() => void} onChanged
 */
function paintDrafts(host, drafts, classId, showMessage, onChanged) {
  if (!(host instanceof HTMLElement)) return;
  if (!drafts.length) {
    host.innerHTML = "";
    return;
  }
  host.innerHTML = drafts.map((draft) => draftCard(draft)).join("");
  host.querySelectorAll("[data-bank-bot-draft]").forEach((card) => {
    if (!(card instanceof HTMLElement)) return;
    const draftId = Number(card.dataset.bankBotDraft);
    card.querySelector("[data-bank-bot-approve]")?.addEventListener("click", () => {
      approveDraft(classId, draftId, readDraft(card), showMessage, onChanged)
        .then(() => {
          card.remove();
        })
        .catch((error) => showMessage(errorText(error)));
    });
    card.querySelector("[data-bank-bot-save]")?.addEventListener("click", () => {
      api(`/api/staff/class/${classId}/bank-bot/drafts/${draftId}`, {
        method: "PATCH",
        body: JSON.stringify(readDraft(card)),
      })
        .then(() => showMessage("Draft saved. It is not in the bank yet."))
        .catch((error) => showMessage(errorText(error)));
    });
    card.querySelector("[data-bank-bot-discard]")?.addEventListener("click", () => {
      api(`/api/staff/class/${classId}/bank-bot/drafts/${draftId}/discard`, { method: "POST" })
        .then(() => card.remove())
        .catch((error) => showMessage(errorText(error)));
    });
  });
}

/**
 * @param {Record<string, unknown>} draft
 * @returns {string}
 */
function draftCard(draft) {
  const options = Array.isArray(draft.options) ? draft.options : [];
  const order = Array.isArray(draft.rank_key) ? draft.rank_key.map((value) => Number(value)) : [];
  const optionFields = options
    .map(
      (option, index) =>
        `<label>Option ${index + 1}<input data-draft-option value="${escapeHtml(option)}"></label>`
    )
    .join("");
  const orderFields = options
    .map((_, index) => {
      const selected = order[index];
      const choices = options
        .map((option, optionIndex) => {
          const on = optionIndex === selected ? " selected" : "";
          return `<option value="${optionIndex}"${on}>${escapeHtml(option)}</option>`;
        })
        .join("");
      return `<label>${index + 1}<select data-draft-order>${choices}</select></label>`;
    })
    .join("");
  return `<article class="bank-bot-draft card" data-bank-bot-draft="${Number(draft.id)}">
    <p class="hint">Draft. Not in the bank until you add it.</p>
    <label>Stem<textarea data-draft-stem rows="3">${escapeHtml(draft.stem || "")}</textarea></label>
    ${optionFields}
    <p class="hint">Answer order, first to last.</p>
    <div class="bank-bot-row">${orderFields}</div>
    <div class="row-actions">
      <button type="button" data-bank-bot-save class="secondary">Save draft</button>
      <button type="button" data-bank-bot-approve>Add to bank</button>
      <button type="button" data-bank-bot-discard class="secondary">Discard</button>
    </div>
  </article>`;
}

/**
 * @param {HTMLElement} card
 * @returns {{stem: string, options: string[], rank_key: number[]}}
 */
function readDraft(card) {
  const stem = card.querySelector("[data-draft-stem]");
  const options = [...card.querySelectorAll("[data-draft-option]")].map((input) =>
    input instanceof HTMLInputElement ? input.value.trim() : ""
  );
  const rankKey = [...card.querySelectorAll("[data-draft-order]")].map((select) =>
    select instanceof HTMLSelectElement ? Number(select.value) : 0
  );
  return {
    stem: stem instanceof HTMLTextAreaElement ? stem.value.trim() : "",
    options,
    rank_key: rankKey,
  };
}

/**
 * @param {number} classId
 * @param {number} draftId
 * @param {{stem: string, options: string[], rank_key: number[]}} body
 * @param {(text: string) => void} showMessage
 * @param {() => void} onChanged
 * @returns {Promise<void>}
 */
async function approveDraft(classId, draftId, body, showMessage, onChanged) {
  await api(`/api/staff/class/${classId}/bank-bot/drafts/${draftId}/approve`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  showMessage("Added to the bank.");
  onChanged?.();
}

/**
 * @param {unknown} error
 * @returns {string}
 */
function errorText(error) {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Live-class panel. Opens on click and loads the module's multiple-choice rows once.
 */
function bootLiveBankBot() {
  const openButton = document.getElementById("live-bank-bot-btn");
  const dialog = document.getElementById("live-bank-bot-dialog");
  const root = document.getElementById("ap-root");
  if (!(openButton instanceof HTMLButtonElement) || !(dialog instanceof HTMLDialogElement)) return;
  const classId = Number(root?.dataset.classId || 0);
  const showMessage = (text) => {
    const status = document.getElementById("live-bank-bot-status");
    if (status) status.textContent = text;
  };
  openButton.addEventListener("click", () => {
    if (!dialog.open) dialog.showModal();
    loadLiveMcList(classId, showMessage).catch((error) => showMessage(errorText(error)));
  });
  document.getElementById("live-bank-bot-close")?.addEventListener("click", () => dialog.close());
  document.getElementById("live-bank-bot-generate")?.addEventListener("click", () => {
    const ids = checkedLiveIds();
    const body = ids.length
      ? { question_ids: ids, bank_scope: liveScope() }
      : { bank_scope: liveScope(), question_type: "mc" };
    generateRanking(classId, body, showMessage, () => {
      loadLiveMcList(classId, showMessage).catch((error) => showMessage(errorText(error)));
    }).catch((error) => showMessage(errorText(error)));
  });
  document.getElementById("live-bank-bot-remove")?.addEventListener("click", () => {
    confirmLiveRemove()
      .then((ok) => {
        if (!ok) return null;
        const questions = checkedLiveQuestions();
        if (!questions.length) {
          showMessage("Select questions to remove.");
          return null;
        }
        return api(`/api/staff/class/${classId}/question-bank/bulk`, {
          method: "POST",
          body: JSON.stringify({ action: "remove", questions }),
        });
      })
      .then((payload) => {
        if (!payload) return;
        showMessage("Removed from bank. Undo is under Recently removed.");
        return loadLiveMcList(classId, showMessage);
      })
      .catch((error) => showMessage(errorText(error)));
  });
  document.getElementById("live-bank-bot-removed")?.addEventListener("click", () => {
    loadRemoved(
      classId,
      document.getElementById("live-bank-bot-removed-list"),
      showMessage,
      () => {
        loadLiveMcList(classId, showMessage).catch((error) => showMessage(errorText(error)));
      }
    ).catch((error) => showMessage(errorText(error)));
  });
}

/**
 * @returns {Promise<boolean>}
 */
function confirmLiveRemove() {
  const confirmDialog = document.getElementById("live-bank-bot-confirm");
  if (!(confirmDialog instanceof HTMLDialogElement)) return Promise.resolve(false);
  return new Promise((resolve) => {
    const finish = (accepted) => {
      confirmDialog.removeEventListener("close", onClose);
      resolve(accepted);
    };
    const onClose = () => finish(confirmDialog.returnValue === "ok");
    confirmDialog.addEventListener("close", onClose);
    confirmDialog.returnValue = "";
    confirmDialog.showModal();
  });
}

/**
 * @returns {string}
 */
function liveScope() {
  const select = document.getElementById("live-module-select");
  const value = select instanceof HTMLSelectElement ? select.value : "M1";
  return /^M[0-8]$/.test(value) ? value : "M1";
}

/**
 * @param {number} classId
 * @param {(text: string) => void} showMessage
 * @returns {Promise<void>}
 */
async function loadLiveMcList(classId, showMessage) {
  const host = document.getElementById("live-bank-bot-list");
  if (!(host instanceof HTMLElement) || !classId) return;
  const scope = liveScope();
  const payload = await api(
    `/api/staff/class/${classId}/module-banks/${encodeURIComponent(scope)}/mc-search?staff_types=1&type=mc`
  );
  const items = Array.isArray(payload.items) ? payload.items : [];
  if (!items.length) {
    host.innerHTML = `<p class="hint">No multiple-choice questions in ${escapeHtml(scope)}.</p>`;
    return;
  }
  host.innerHTML = `<ul class="bank-bot-list">${items
    .map((item) => {
      const bankId = Number(item.bank_id || item.source_bank_id || 0);
      const questionId = Number(item.question_id || item.id || 0);
      const stem = String(item.text || item.stem || item.title || "Question");
      return `<li class="bank-bot-row">
        <input type="checkbox" data-live-bank-check data-bank-id="${bankId}" data-question-id="${questionId}" aria-label="Select question">
        <span>${escapeHtml(stem)}</span>
      </li>`;
    })
    .join("")}</ul>`;
  showMessage(`${items.length} multiple-choice questions in ${scope}.`);
}

/**
 * @returns {number[]}
 */
function checkedLiveIds() {
  return checkedLiveQuestions().map((row) => row.question_id);
}

/**
 * @returns {{bank_id: number, question_id: number}[]}
 */
function checkedLiveQuestions() {
  return [...document.querySelectorAll("[data-live-bank-check]")].flatMap((node) => {
    if (!(node instanceof HTMLInputElement) || !node.checked) return [];
    const bankId = Number(node.dataset.bankId || 0);
    const questionId = Number(node.dataset.questionId || 0);
    if (!bankId || !questionId) return [];
    return [{ bank_id: bankId, question_id: questionId }];
  });
}

bootLiveBankBot();
