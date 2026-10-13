import { api, escapeHtml, hideError, showError } from "/static/common.js";

const root = document.getElementById("engagement-summary-root");
const classId = Number(root?.dataset.classId || 0);
const runSelect = document.getElementById("engagement-run-select");
const card = document.getElementById("engagement-summary-card");
const empty = document.getElementById("engagement-summary-empty");
const groupsEl = document.getElementById("engagement-summary-groups");
const ungroupedEl = document.getElementById("engagement-summary-ungrouped");
const rulesEl = document.getElementById("engagement-rubric-rules");
const blindEl = document.getElementById("engagement-blind-spots");
const titleEl = card?.querySelector("h2.track-step-title");

let currentRunKey = "";

/**
 * Format an ISO timestamp as HH:MM ET for the run picker.
 * @param {string|undefined} iso
 * @returns {string}
 */
function formatEtTime(iso) {
  const text = String(iso || "").trim();
  if (!text) {
    return "";
  }
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  const clock = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Toronto",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
  return `${clock} ET`;
}

/**
 * Disambiguate run picker labels when several runs share the same M1C1 key.
 * @param {any} row
 * @param {any[]} runs
 * @returns {string}
 */
function runOptionLabel(row, runs) {
  const base = String(row.live_label || row.run_key || "Run");
  const matches = runs.filter((r) => String(r.live_label || r.run_key) === base);
  if (matches.length <= 1) {
    return base;
  }
  const time = formatEtTime(row.started_at || row.rolled_at);
  if (time) {
    return `${base} · ${time}`;
  }
  const key = String(row.run_key || "").slice(0, 8);
  return key ? `${base} · ${key}` : base;
}

/**
 * Format one evidence chip for the summary row.
 * @param {any} row
 * @returns {string}
 */
function evidenceChip(row) {
  const signal = escapeHtml(String(row?.signal || "").replace(/_/g, " "));
  const count = Number(row?.count || 0);
  const offered = row?.offered;
  if (offered != null && offered !== "") {
    return `${signal} ${count}/${offered}`;
  }
  return `${signal} ${count}`;
}

/**
 * Render override controls for one student line.
 * @param {any} student
 * @param {string} runKey
 * @returns {string}
 */
function overrideBlock(student, runKey) {
  const sid = Number(student.student_id);
  const override = student.override || null;
  const level = override?.level ?? student.suggested_level ?? "";
  const note = override?.note ?? "";
  const attr = override?.teacher_name
    ? `Saved by ${escapeHtml(override.teacher_name)}`
    : "";
  return `
    <form class="ap-engagement-override" data-student-id="${sid}">
      <label class="field compact">
        <span>Your engagement level</span>
        <select name="level" aria-label="Override engagement level for ${escapeHtml(student.codename || "student")}">
          <option value="">—</option>
          ${[1, 2, 3, 4].map((n) => `<option value="${n}"${String(level) === String(n) ? " selected" : ""}>L${n}</option>`).join("")}
        </select>
      </label>
      <label class="field compact">
        <span>Note</span>
        <input type="text" name="note" maxlength="500" value="${escapeHtml(note)}" placeholder="Optional — not sent to gradebook">
      </label>
      <button type="submit" class="secondary">Save override</button>
      <span class="hint compact ap-engagement-override-meta">${attr}</span>
    </form>`;
}

/**
 * One student row inside a group block.
 * @param {any} student
 * @param {string} runKey
 * @returns {string}
 */
function studentRow(student, runKey) {
  const label = escapeHtml(student.effective_label || student.suggested_label || "—");
  const reason = escapeHtml(student.reason || "");
  const evidence = (student.evidence || [])
    .map((row) => `<span class="ap-engagement-evidence-chip">${escapeHtml(evidenceChip(row))}</span>`)
    .join("");
  return `
    <li class="ap-engagement-student">
      <div class="ap-engagement-student-head">
        <strong class="ap-engagement-name">${escapeHtml(student.codename || "Student")}</strong>
        <span class="ap-engagement-level-badge">${label}</span>
      </div>
      <p class="hint compact ap-engagement-reason">${reason}</p>
      <div class="ap-engagement-evidence">${evidence || "<span class='hint compact'>No counts</span>"}</div>
      ${overrideBlock(student, runKey)}
    </li>`;
}

/**
 * Paint summary payload into the card.
 * @param {any} summary
 */
function renderSummary(summary) {
  if (!summary) {
    card.hidden = true;
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  card.hidden = false;
  if (titleEl) {
    titleEl.textContent = "End-of-class engagement";
  }
  const rules = (summary.rubric?.rules || []).join(" · ");
  rulesEl.textContent = rules;
  blindEl.innerHTML = (summary.blind_spots || [])
    .map((line) => `<li>${escapeHtml(line)}</li>`)
    .join("");
  const runKey = String(summary.run_key || currentRunKey);
  groupsEl.innerHTML = (summary.groups || [])
    .map(
      (group) => `
      <section class="card ap-engagement-group">
        <h3>${escapeHtml(group.team_name || "Group")}</h3>
        <ul class="ap-engagement-students">
          ${(group.students || []).map((s) => studentRow(s, runKey)).join("")}
        </ul>
      </section>`
    )
    .join("");
  if ((summary.ungrouped || []).length) {
    ungroupedEl.innerHTML = `
      <section class="card ap-engagement-group">
        <h3>No team</h3>
        <ul class="ap-engagement-students">
          ${summary.ungrouped.map((s) => studentRow(s, runKey)).join("")}
        </ul>
      </section>`;
  } else {
    ungroupedEl.innerHTML = "";
  }
  bindEngagementOverrideForms();
}

/**
 * Wire Save override on every form under the summary card (grouped and ungrouped).
 */
function bindEngagementOverrideForms() {
  const scope = document.getElementById("engagement-summary-card");
  if (!scope) return;
  scope.querySelectorAll("form.ap-engagement-override").forEach((form) => {
    form.addEventListener("submit", onOverrideSubmit);
  });
}

/**
 * Load runs into the picker and fetch the selected summary.
 * @param {string} [runKey]
 */
async function loadSummary(runKey) {
  hideError("engagement-summary-error");
  const qs = runKey ? `?run_key=${encodeURIComponent(runKey)}` : "";
  const data = await api(`/api/classes/${classId}/engagement-summary${qs}`);
  const runs = data.runs || [];
  if (runSelect && runs.length) {
    runSelect.innerHTML = runs
      .map(
        (row) =>
          `<option value="${escapeHtml(row.run_key)}">${escapeHtml(runOptionLabel(row, runs))}</option>`
      )
      .join("");
    currentRunKey = runKey || runs[0].run_key;
    runSelect.value = currentRunKey;
  }
  if (!data.summary) {
    renderSummary(null);
    return;
  }
  currentRunKey = data.summary.run_key;
  if (runSelect) runSelect.value = currentRunKey;
  renderSummary(data.summary);
}

/**
 * Persist teacher override (MCK-61).
 * @param {Event} event
 */
async function onOverrideSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!(form instanceof HTMLFormElement)) return;
  const studentId = Number(form.dataset.studentId || 0);
  const level = Number(form.level?.value || 0);
  if (!level) {
    showError("engagement-summary-error", "Pick a level (L1–L4) to save an override.");
    return;
  }
  const note = String(form.note?.value || "");
  hideError("engagement-summary-error");
  try {
    const data = await api(
      `/api/classes/${classId}/engagement-summary/${encodeURIComponent(currentRunKey)}/students/${studentId}`,
      { method: "PUT", body: JSON.stringify({ level, note }) }
    );
    renderSummary(data.summary);
  } catch (err) {
    showError("engagement-summary-error", err?.message || "Could not save override.");
  }
}

if (root && classId) {
  runSelect?.addEventListener("change", () => {
    currentRunKey = String(runSelect.value || "");
    loadSummary(currentRunKey).catch((err) => {
      showError("engagement-summary-error", err?.message || "Could not load summary.");
    });
  });
  loadSummary().catch((err) => {
    showError("engagement-summary-error", err?.message || "Could not load summary.");
  });
}
