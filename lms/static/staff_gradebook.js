import { api, escapeHtml, hideError, showError } from "/static/common.js";
import { moodGlyph } from "/static/mood_faces.js";

const root = document.getElementById("gradebook-root");
const classId = Number(root?.dataset.classId || 0);
const reflectionEndpoint = `/api/classes/${classId}/module-reflections`;
const schemeEndpoint = `/api/classes/${classId}/grade-scheme`;

/** @type {Record<string, any>} */
let moduleRules = {};
let selectedModule = 1;

/**
 * Roster label is the Codename only.
 * @param {{codename?: string, first_name?: string}} student
 * @returns {string}
 */
function displayName(student) {
  return String(student?.codename || student?.first_name || "").trim();
}

/**
 * Format a percent (or em dash when empty).
 * @param {unknown} value
 * @returns {string}
 */
function scoreLabel(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return String(Math.round(n * 10) / 10);
}

/**
 * Compact month+day from an ISO date.
 * @param {string|null|undefined} iso
 * @returns {string}
 */
function shortIso(iso) {
  if (!iso) return "";
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${months[d.getMonth()]} ${d.getDate()}`;
}

/**
 * Met/unmet chip HTML.
 * @param {boolean} met
 * @param {string} label
 * @returns {string}
 */
function chip(met, label) {
  const cls = met ? "gb-chip is-met" : "gb-chip is-unmet";
  const mark = met ? "✓" : "·";
  return `<span class="${cls}"><span aria-hidden="true">${mark}</span> ${escapeHtml(label)}</span>`;
}

/**
 * Numeric value of a scheme input.
 * @param {string} id
 * @returns {number}
 */
function inputNumber(id) {
  return Number(document.getElementById(id)?.value || 0);
}

/**
 * Update the live weight-sum hint under the scheme form.
 */
function paintWeightSum() {
  const el = document.getElementById("scheme-weight-sum");
  if (!el) return;
  const total =
    inputNumber("scheme-weight-term") +
    inputNumber("scheme-weight-exam") +
    inputNumber("scheme-weight-participation");
  const ok = Math.abs(total - 100) <= 0.05;
  el.textContent = `Total ${Number.isFinite(total) ? total : "—"}%${ok ? "" : " — must equal 100%"}`;
  el.classList.toggle("gb-scheme-sum-bad", !ok);
}

/**
 * Read the Term Mark portfolio fields for the selected module.
 * @returns {{min_sessions: number, min_r1: number, min_r3: number, require_reflections: boolean}}
 */
function readTermRulesFromForm() {
  return {
    min_sessions: inputNumber("scheme-term-sessions"),
    min_r1: inputNumber("scheme-term-r1"),
    min_r3: inputNumber("scheme-term-r3"),
    require_reflections: Boolean(document.getElementById("scheme-term-reflections")?.checked),
  };
}

/**
 * Keep unsaved Term Mark edits when switching the module dropdown.
 */
function stashCurrentModule() {
  const prev = moduleRules[String(selectedModule)] || {};
  moduleRules[String(selectedModule)] = { ...prev, ...readTermRulesFromForm() };
}

/**
 * Fill Term Mark portfolio inputs from one module's stored rules.
 * @param {any} row
 */
function applyTermRulesToForm(row) {
  const rules = row || {};
  const window = rules.window || {};
  const sessions = document.getElementById("scheme-term-sessions");
  const r1 = document.getElementById("scheme-term-r1");
  const r3 = document.getElementById("scheme-term-r3");
  const refl = document.getElementById("scheme-term-reflections");
  if (sessions) sessions.value = String(rules.min_sessions ?? 3);
  if (r1) r1.value = String(rules.min_r1 ?? 10);
  if (r3) r3.value = String(rules.min_r3 ?? 10);
  if (refl) refl.checked = rules.require_reflections !== false;
  const winEl = document.getElementById("scheme-term-window");
  if (winEl) {
    const start = shortIso(window.start || rules.start);
    const end = shortIso(window.end || rules.end);
    const n = Number(rules.number || selectedModule);
    winEl.textContent =
      start && end
        ? `Module ${n} window ${start} – ${end} (even two-week spread; not edited here).`
        : `Module ${n} uses the even two-week content window.`;
  }
}

/**
 * Index scheme modules from gradebook JSON.
 * @param {any} data
 * @returns {Record<string, any>}
 */
function indexModules(data) {
  /** @type {Record<string, any>} */
  const out = {};
  const rows = data.scheme?.modules;
  if (Array.isArray(rows)) {
    for (const row of rows) {
      const n = Number(row?.number);
      if (!Number.isFinite(n) || n < 1) continue;
      out[String(n)] = { ...row };
    }
  }
  if (!out["1"]) {
    const fallback = data.scheme?.module_1 || data.module_1?.rules || {};
    out["1"] = {
      number: 1,
      ...fallback,
      window: data.module_1?.window || fallback.window || {},
    };
  }
  return out;
}

/**
 * Fill the grading-scheme form from the latest book.
 * @param {any} data
 */
function paintScheme(data) {
  const weights = data.scheme?.weights || data.weights || {};
  const term = document.getElementById("scheme-weight-term");
  const exam = document.getElementById("scheme-weight-exam");
  const ap = document.getElementById("scheme-weight-participation");
  if (term) term.value = String(weights.term ?? 65);
  if (exam) exam.value = String(weights.exam ?? 20);
  if (ap) ap.value = String(weights.participation ?? 15);
  moduleRules = indexModules(data);
  const select = document.getElementById("scheme-term-module");
  if (select instanceof HTMLSelectElement) {
    if (!moduleRules[String(selectedModule)]) selectedModule = 1;
    select.value = String(selectedModule);
  }
  applyTermRulesToForm(moduleRules[String(selectedModule)]);
  paintWeightSum();
}

/**
 * Persist the scheme form, then redraw the book.
 * @param {SubmitEvent} event
 */
async function saveScheme(event) {
  event.preventDefault();
  hideError("#gradebook-error");
  stashCurrentModule();
  /** @type {Record<string, any>} */
  const modules = {};
  for (const [num, row] of Object.entries(moduleRules)) {
    modules[num] = {
      min_sessions: row.min_sessions,
      min_r1: row.min_r1,
      min_r3: row.min_r3,
      require_reflections: row.require_reflections !== false,
    };
  }
  const status = document.getElementById("gradebook-scheme-status");
  const payload = {
    weights: {
      term: inputNumber("scheme-weight-term"),
      exam: inputNumber("scheme-weight-exam"),
      participation: inputNumber("scheme-weight-participation"),
    },
    module_number: selectedModule,
    module: modules[String(selectedModule)],
    modules,
  };
  const data = await api(schemeEndpoint, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  paint(data.gradebook || data);
  if (status) {
    status.hidden = false;
    status.textContent = `Scheme saved. Module ${selectedModule} portfolio rules are in effect.`;
  }
}

/**
 * Load and paint the weighted gradebook.
 */
async function refresh() {
  hideError("#gradebook-error");
  const data = await api(`/api/classes/${classId}/gradebook?sort=az`);
  paint(data);
}

/**
 * Persist the reflections checkbox, then redraw from the returned book.
 * @param {number} studentId
 * @param {boolean} complete
 */
async function saveReflection(studentId, complete) {
  hideError("#gradebook-error");
  const data = await api(reflectionEndpoint, {
    method: "POST",
    body: JSON.stringify({
      student_id: studentId,
      module_number: 1,
      complete,
    }),
  });
  paint(data.gradebook || data);
}

/**
 * Render the student sheet and scheme from gradebook JSON.
 * @param {any} data
 */
function paint(data) {
  const table = document.getElementById("gradebook-sheet");
  if (!table) return;
  paintScheme(data);

  const categories = data.categories || [];
  const weights = data.weights || {};
  const m1 = data.module_1 || {};
  const byCat = Object.fromEntries(categories.map((c) => [c.id, c]));
  const termW = weights.term ?? byCat.term?.weight_pct ?? 65;
  const apW = weights.participation ?? byCat.participation?.weight_pct ?? 15;
  const examW = weights.exam ?? byCat.exam?.weight_pct ?? 20;
  const head = `<tr>
    <th class="name">Student</th>
    <th>Live / Office</th>
    <th>Open Q</th>
    <th>Formative</th>
    <th>Reflections</th>
    <th>M1 Portfolio</th>
    <th>M1 Test</th>
    <th>Term <span class="weight-pct">${escapeHtml(String(termW))}%</span></th>
    <th>A&amp;P <span class="weight-pct">${escapeHtml(String(apW))}%</span></th>
    <th>Exam <span class="weight-pct">${escapeHtml(String(examW))}%</span></th>
  </tr>`;

  let body = "";
  for (const student of data.students || []) {
    const sid = String(student.id);
    const detail = (m1.students && m1.students[sid]) || {};
    const earned = Boolean(detail.earned_100);
    const sessionsLabel = `${detail.sessions ?? 0}/${detail.sessions_needed ?? 3}`;
    const r1Label = `${scoreLabel(detail.points_r1)}/${detail.r1_needed ?? 10}`;
    const r3Label = `${scoreLabel(detail.points_r3)}/${detail.r3_needed ?? 10}`;
    const reflected = Boolean(detail.reflections_complete);
    const portfolio = earned ? "100" : "—";
    const portfolioCls = earned ? "cell gb-earned" : "cell gb-pending";
    const reflectDisabled = detail.require_reflections === false;
    body += `<tr>
      <td class="name">${moodGlyph(student.mood) ? `${moodGlyph(student.mood)} ` : ""}${escapeHtml(displayName(student))}</td>
      <td class="cell">${chip(Boolean(detail.sessions_met), sessionsLabel)}</td>
      <td class="cell">${chip(Boolean(detail.r1_met), r1Label)}</td>
      <td class="cell">${chip(Boolean(detail.r3_met), r3Label)}</td>
      <td class="cell">
        <label class="gb-reflect">
          <input type="checkbox" data-student-id="${escapeHtml(sid)}" ${reflected ? "checked" : ""}${reflectDisabled ? " disabled" : ""}>
          <span>${reflectDisabled ? "Off" : reflected ? "Done" : "Log"}</span>
        </label>
      </td>
      <td class="${portfolioCls}">${escapeHtml(portfolio)}</td>
      <td class="cell gb-placeholder">—</td>
      <td class="cell">${escapeHtml(scoreLabel(byCat.term?.scores?.[sid]))}</td>
      <td class="cell gb-placeholder" title="${escapeHtml(String(byCat.participation?.points?.[sid] ?? 0))} live-class points">—</td>
      <td class="cell gb-placeholder">—</td>
    </tr>`;
  }
  table.innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
  table.querySelectorAll("input[type=checkbox][data-student-id]").forEach((el) => {
    el.addEventListener("change", () => {
      const id = Number(el.dataset.studentId || 0);
      saveReflection(id, el.checked).catch((err) => {
        el.checked = !el.checked;
        showError("#gradebook-error", err);
      });
    });
  });
}

document.getElementById("gradebook-scheme-form")?.addEventListener("submit", (event) => {
  saveScheme(event).catch((err) => showError("#gradebook-error", err));
});
document.getElementById("scheme-term-module")?.addEventListener("change", (event) => {
  const select = event.currentTarget;
  if (!(select instanceof HTMLSelectElement)) return;
  stashCurrentModule();
  selectedModule = Number(select.value) || 1;
  applyTermRulesToForm(moduleRules[String(selectedModule)]);
});
["scheme-weight-term", "scheme-weight-exam", "scheme-weight-participation"].forEach((id) => {
  document.getElementById(id)?.addEventListener("input", paintWeightSum);
});

refresh().catch((err) => showError("#gradebook-error", err));
