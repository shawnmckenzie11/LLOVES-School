/**
 * Staff Profiles tab: Open Question and Team Challenge action profiles.
 */
import { api, escapeHtml, hideError, showError } from "/static/common.js";

const root = document.getElementById("ap-profiles-root");
const classId = Number(root?.dataset.classId || 0);

/** @type {{open: object, challenge?: object} | null} */
let documentState = null;
let selectedId = "";
let kind = "open";

/**
 * @param {string} id
 * @returns {HTMLElement | null}
 */
function $(id) {
  return document.getElementById(id);
}

/**
 * @returns {object}
 */
function section() {
  if (!documentState) return { active_id: "", profiles: [] };
  if (kind === "challenge") {
    if (!documentState.challenge) {
      documentState.challenge = { active_id: "", profiles: [] };
    }
    return documentState.challenge;
  }
  return documentState.open || { active_id: "", profiles: [] };
}

/**
 * @returns {object[]}
 */
function profiles() {
  return section().profiles || [];
}

/**
 * @returns {object | null}
 */
function selectedProfile() {
  return profiles().find((p) => p.id === selectedId) || profiles()[0] || null;
}

/**
 * Load profiles from the API.
 */
async function load() {
  hideError("#ap-profiles-error");
  const data = await api(`/api/classes/${classId}/ap-round-profiles`);
  documentState = data.document || { open: { active_id: "default", profiles: [] } };
  kind = "open";
  const kindSelect = $("ap-profiles-kind");
  if (kindSelect instanceof HTMLSelectElement) kindSelect.value = kind;
  selectedId = documentState.open?.active_id || documentState.open?.profiles?.[0]?.id || "";
  paint();
}

/**
 * Paint profile select + editor fields.
 */
function paint() {
  const select = $("ap-profiles-select");
  const nameInput = $("ap-profiles-name");
  const activeCheck = $("ap-profiles-active");
  const tbody = $("ap-profiles-actions")?.querySelector("tbody");
  if (!select || !nameInput || !activeCheck || !tbody || !documentState) return;
  const list = profiles();
  if (!list.length) return;
  if (!list.some((p) => p.id === selectedId)) {
    selectedId = list[0].id;
  }
  select.innerHTML = list
    .map(
      (p) =>
        `<option value="${escapeHtml(p.id)}"${p.id === selectedId ? " selected" : ""}>${escapeHtml(p.name)}</option>`
    )
    .join("");
  const profile = selectedProfile();
  if (!profile) return;
  nameInput.value = profile.name || "";
  activeCheck.checked = section().active_id === profile.id;
  tbody.innerHTML = (profile.actions || [])
    .map(
      (action, index) => `<tr data-action-index="${index}">
        <td><input type="text" data-field="id" value="${escapeHtml(action.id || "")}" maxlength="40"></td>
        <td><input type="text" data-field="label" value="${escapeHtml(action.label || "")}" maxlength="120"></td>
        <td><input type="number" data-field="amount" value="${escapeHtml(String(action.amount ?? 1))}" step="0.1"></td>
        <td><button type="button" class="secondary" data-remove-action="${index}">Remove</button></td>
      </tr>`
    )
    .join("");
}

/**
 * Sync editor fields into documentState for the selected profile.
 */
function syncFromDom() {
  if (!documentState) return;
  const profile = selectedProfile();
  if (!profile) return;
  const nameInput = $("ap-profiles-name");
  const activeCheck = $("ap-profiles-active");
  const tbody = $("ap-profiles-actions")?.querySelector("tbody");
  if (nameInput) profile.name = nameInput.value.trim() || profile.name;
  if (activeCheck?.checked) {
    section().active_id = profile.id;
  }
  if (!tbody) return;
  profile.actions = [...tbody.querySelectorAll("tr")].map((tr, index) => {
    const id = tr.querySelector('[data-field="id"]');
    const label = tr.querySelector('[data-field="label"]');
    const amount = tr.querySelector('[data-field="amount"]');
    const row = {
      id: id instanceof HTMLInputElement ? id.value.trim() : `action_${index + 1}`,
      label: label instanceof HTMLInputElement ? label.value.trim() : "",
      amount: amount instanceof HTMLInputElement ? Number(amount.value) : 1,
    };
    if (kind === "challenge") {
      row.lookfor_key = row.id;
    }
    return row;
  });
}

/**
 * Persist documentState via PUT.
 */
async function save() {
  hideError("#ap-profiles-error");
  const status = $("ap-profiles-status");
  syncFromDom();
  try {
    const data = await api(`/api/classes/${classId}/ap-round-profiles`, {
      method: "PUT",
      body: JSON.stringify({ document: documentState }),
    });
    documentState = data.document;
    selectedId = section().active_id;
    paint();
    if (status) {
      status.hidden = false;
      status.textContent = "Saved.";
      setTimeout(() => {
        status.hidden = true;
      }, 2000);
    }
  } catch (err) {
    showError("#ap-profiles-error", err);
  }
}

/**
 * @param {string} base
 * @returns {string}
 */
function uniqueId(base) {
  const used = new Set(profiles().map((p) => p.id));
  let id = base;
  let n = 2;
  while (used.has(id)) {
    id = `${base}_${n}`;
    n += 1;
  }
  return id;
}

$("ap-profiles-kind")?.addEventListener("change", (event) => {
  const select = event.target;
  if (!(select instanceof HTMLSelectElement)) return;
  syncFromDom();
  kind = select.value === "challenge" ? "challenge" : "open";
  selectedId = section().active_id || profiles()[0]?.id || "";
  paint();
});

$("ap-profiles-select")?.addEventListener("change", (event) => {
  const select = event.target;
  if (!(select instanceof HTMLSelectElement)) return;
  syncFromDom();
  selectedId = select.value;
  paint();
});

$("ap-profiles-new")?.addEventListener("click", () => {
  syncFromDom();
  if (!documentState) return;
  const id = uniqueId("profile");
  section().profiles.push({
    id,
    name: "New profile",
    actions: [{ id: "action_1", label: "New action", amount: 1 }],
  });
  selectedId = id;
  paint();
});

$("ap-profiles-duplicate")?.addEventListener("click", () => {
  syncFromDom();
  const profile = selectedProfile();
  if (!documentState || !profile) return;
  const id = uniqueId(`${profile.id}_copy`);
  section().profiles.push({
    id,
    name: `${profile.name} copy`,
    actions: (profile.actions || []).map((a) => ({ ...a })),
  });
  selectedId = id;
  paint();
});

$("ap-profiles-delete")?.addEventListener("click", () => {
  syncFromDom();
  if (!documentState) return;
  if (profiles().length <= 1) {
    showError(
      "#ap-profiles-error",
      new Error("Keep at least one profile for this round kind.")
    );
    return;
  }
  section().profiles = profiles().filter((p) => p.id !== selectedId);
  if (section().active_id === selectedId) {
    section().active_id = section().profiles[0].id;
  }
  selectedId = section().profiles[0].id;
  paint();
});

$("ap-profiles-add-action")?.addEventListener("click", () => {
  syncFromDom();
  const profile = selectedProfile();
  if (!profile) return;
  const n = (profile.actions || []).length + 1;
  profile.actions = profile.actions || [];
  profile.actions.push({ id: `action_${n}`, label: "New action", amount: 1 });
  paint();
});

$("ap-profiles-actions")?.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-remove-action]");
  if (!btn) return;
  syncFromDom();
  const profile = selectedProfile();
  if (!profile) return;
  const index = Number(btn.getAttribute("data-remove-action"));
  profile.actions = (profile.actions || []).filter((_, i) => i !== index);
  paint();
});

$("ap-profiles-save")?.addEventListener("click", () => {
  save();
});

if (root && classId) {
  load().catch((err) => showError("#ap-profiles-error", err));
}
