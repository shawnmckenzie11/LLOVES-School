/**
 * Team Challenge process-evidence panel (separate from Open Question point chips).
 */
import { api, escapeHtml } from "/static/common.js";

const root = document.getElementById("ap-root");
const panel = document.getElementById("ap-evidence-panel");
const coverageRoot = document.getElementById("ap-evidence-coverage");

let phrases = [];
let processes = [];
let observations = [];
let liveSessionId = 0;
let classId = Number(root?.dataset.classId || 0);
let overlayState = null;
let selectedProcess = "";
let selectedPhraseId = null;
let selectedStudentIds = [];
let scope = "team";

/**
 * Load phrases and processes once.
 * @returns {Promise<void>}
 */
async function loadPhraseCatalog() {
  const data = await api("/api/quick-phrases?category=team_problem");
  phrases = data.phrases || [];
  processes = data.processes || [];
  const extra = await api("/api/quick-phrases?category=consolidation");
  const seen = new Set(phrases.map((p) => p.id));
  for (const row of extra.phrases || []) {
    if (!seen.has(row.id)) phrases.push(row);
  }
}

/**
 * Current team tab id from scoring UI.
 * @returns {number|null}
 */
function activeTeamId() {
  const tabs = document.getElementById("ap-score-team-tabs");
  const raw = tabs?.dataset.activeTeam;
  return raw ? Number(raw) : overlayState?.teams?.[0]?.id || null;
}

/**
 * Members of the active team.
 * @returns {Array<{id:number,codename?:string,display_name?:string}>}
 */
function activeMembers() {
  const tid = activeTeamId();
  const team = (overlayState?.teams || []).find((t) => Number(t.id) === Number(tid));
  return team?.members || overlayState?.students || [];
}

/**
 * Paint scope buttons: selected students / team / class.
 */
function renderScope() {
  const wrap = document.getElementById("ap-evidence-scope");
  if (!wrap) return;
  const members = activeMembers();
  const studentBtns = members
    .map((s) => {
      const on = selectedStudentIds.includes(Number(s.id));
      const name = escapeHtml(s.codename || s.display_name || `Student ${s.id}`);
      return `<button type="button" class="ap-evidence-chip${on ? " is-on" : ""}" data-scope-student="${s.id}">${name}</button>`;
    })
    .join("");
  wrap.innerHTML = `
    <p class="hint compact">Scope</p>
    <div class="ap-evidence-chip-row">
      ${studentBtns}
      <button type="button" class="ap-evidence-chip${scope === "team" ? " is-on" : ""}" data-scope="team">Team</button>
      <button type="button" class="ap-evidence-chip${scope === "class" ? " is-on" : ""}" data-scope="class">Class</button>
    </div>`;
}

/**
 * Paint process chips then phrase buttons.
 */
function renderProcessesAndPhrases() {
  const procWrap = document.getElementById("ap-evidence-processes");
  const phraseWrap = document.getElementById("ap-evidence-phrases");
  if (!procWrap || !phraseWrap) return;
  procWrap.innerHTML = (processes || [])
    .map((p) => {
      const on = selectedProcess === p.process_key;
      return `<button type="button" class="ap-evidence-chip${on ? " is-on" : ""}" data-process="${escapeHtml(p.process_key)}">${escapeHtml(p.name)}</button>`;
    })
    .join("");
  const filtered = selectedProcess
    ? phrases.filter((ph) => ph.process_key === selectedProcess)
    : phrases;
  phraseWrap.innerHTML = filtered
    .map((ph) => {
      const on = Number(selectedPhraseId) === Number(ph.id);
      return `<button type="button" class="ap-evidence-chip${on ? " is-on" : ""}" data-phrase="${ph.id}">${escapeHtml(ph.label)}</button>`;
    })
    .join("");
}

/**
 * Paint saved observations for undo/edit/delete.
 */
function renderLog() {
  const list = document.getElementById("ap-evidence-log");
  if (!list) return;
  list.innerHTML = observations
    .slice()
    .reverse()
    .map((obs) => {
      const label = escapeHtml((obs.note || "").slice(0, 80));
      return `<li data-obs="${obs.id}">
        <span>${escapeHtml(obs.scope)} · ${label}</span>
        <button type="button" class="linkish" data-edit-obs="${obs.id}">Edit</button>
        <button type="button" class="linkish" data-del-obs="${obs.id}">Delete</button>
      </li>`;
    })
    .join("");
}

/**
 * Refresh observations from the API.
 * @returns {Promise<void>}
 */
async function refreshObservations() {
  if (!liveSessionId) return;
  const data = await api(`/api/live-sessions/${liveSessionId}/observations`);
  observations = data.observations || [];
  renderLog();
}

/**
 * Show the evidence panel on Team Challenge (and formative).
 * @param {string} roundKind
 */
function setPanelVisible(roundKind) {
  if (!panel) return;
  const show = roundKind === "challenge" || roundKind === "formative";
  panel.hidden = !show;
}

/**
 * Save the current note as an observation.
 * @returns {Promise<void>}
 */
async function saveObservation() {
  if (!liveSessionId) return;
  const noteEl = document.getElementById("ap-evidence-note");
  const note = String(noteEl?.value || "").trim();
  if (!note) return;
  const phrase = phrases.find((p) => Number(p.id) === Number(selectedPhraseId));
  const processKeys = selectedProcess
    ? [selectedProcess]
    : phrase
      ? [phrase.process_key]
      : [];
  let scopeKey = scope;
  if (selectedStudentIds.length === 1) scopeKey = "student";
  else if (selectedStudentIds.length > 1) scopeKey = "students";
  const strength = document.getElementById("ap-evidence-strength")?.value || null;
  const follow = Boolean(document.getElementById("ap-evidence-followup")?.checked);
  await api(`/api/live-sessions/${liveSessionId}/observations`, {
    method: "POST",
    body: JSON.stringify({
      scope: scopeKey,
      note,
      student_ids: selectedStudentIds,
      team_id: scopeKey === "team" ? activeTeamId() : null,
      process_keys: processKeys,
      evidence_strength: strength || null,
      follow_up_required: follow,
      source: phrase ? "quick_phrase" : "manual",
      quick_phrase_id: phrase ? phrase.id : null,
    }),
  });
  if (noteEl) noteEl.value = "";
  selectedPhraseId = null;
  renderProcessesAndPhrases();
  await refreshObservations();
}

/**
 * Bind clicks inside the evidence panel.
 */
function bindPanel() {
  panel?.addEventListener("click", (event) => {
    const btn = event.target.closest("button");
    if (!btn) return;
    if (btn.dataset.scopeStudent) {
      const id = Number(btn.dataset.scopeStudent);
      if (selectedStudentIds.includes(id)) {
        selectedStudentIds = selectedStudentIds.filter((x) => x !== id);
      } else {
        selectedStudentIds = [...selectedStudentIds, id];
      }
      scope = selectedStudentIds.length ? "student" : "team";
      renderScope();
      return;
    }
    if (btn.dataset.scope) {
      scope = btn.dataset.scope;
      if (scope !== "student") selectedStudentIds = [];
      renderScope();
      return;
    }
    if (btn.dataset.process) {
      selectedProcess = btn.dataset.process;
      selectedPhraseId = null;
      renderProcessesAndPhrases();
      return;
    }
    if (btn.dataset.phrase) {
      selectedPhraseId = Number(btn.dataset.phrase);
      const phrase = phrases.find((p) => Number(p.id) === selectedPhraseId);
      const noteEl = document.getElementById("ap-evidence-note");
      if (phrase && noteEl && !noteEl.value) noteEl.value = phrase.label;
      if (phrase) selectedProcess = phrase.process_key;
      renderProcessesAndPhrases();
      return;
    }
    if (btn.id === "ap-evidence-save") {
      saveObservation().catch((err) => window.alert(err.message || String(err)));
      return;
    }
    if (btn.id === "ap-evidence-undo") {
      const last = observations[observations.length - 1];
      if (!last) return;
      api(`/api/live-sessions/${liveSessionId}/observations/${last.id}`, {
        method: "DELETE",
      })
        .then(refreshObservations)
        .catch((err) => window.alert(err.message || String(err)));
      return;
    }
    if (btn.dataset.delObs) {
      api(`/api/live-sessions/${liveSessionId}/observations/${btn.dataset.delObs}`, {
        method: "DELETE",
      })
        .then(refreshObservations)
        .catch((err) => window.alert(err.message || String(err)));
      return;
    }
    if (btn.dataset.editObs) {
      const obs = observations.find((o) => String(o.id) === String(btn.dataset.editObs));
      const noteEl = document.getElementById("ap-evidence-note");
      if (obs && noteEl) noteEl.value = obs.note;
      selectedPhraseId = obs?.quick_phrase_id || null;
    }
  });
  document.getElementById("ap-evidence-note")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      saveObservation().catch((err) => window.alert(err.message || String(err)));
    }
  });
}

/**
 * After-class coverage matrix on the Participation tab.
 * @returns {Promise<void>}
 */
async function loadCoverage() {
  if (!coverageRoot || root?.dataset.apView !== "participation") return;
  const sid = Number(root.dataset.liveSessionId || 0);
  let sessionId = sid;
  if (!sessionId) {
    const dash = await api(`/api/classes/${classId}/game`).catch(() => null);
    sessionId = Number(dash?.live_session_id || 0);
  }
  if (!sessionId) {
    const active = await api("/api/live-sessions/active").catch(() => null);
    const mine = (active?.sessions || []).find((s) => Number(s.class_id) === classId);
    sessionId = mine ? Number(mine.id) : 0;
  }
  if (!sessionId) {
    document.getElementById("ap-coverage-matrix").textContent =
      "No live session on this page. Open Run Live Class, then return here after End Class.";
    return;
  }
  const cov = await api(`/api/live-sessions/${sessionId}/observations/coverage`);
  const obs = await api(`/api/live-sessions/${sessionId}/observations`);
  const procKeys = (cov.class_counts && Object.keys(cov.class_counts)) || [];
  const students = Object.keys(cov.by_student || {});
  const keys = [...new Set([...procKeys, ...students.flatMap((id) => Object.keys(cov.by_student[id] || {}))])];
  const head = keys.map((k) => `<th>${escapeHtml(k)}</th>`).join("");
  const body = students
    .map((id) => {
      const cells = keys
        .map((k) => `<td>${Number(cov.by_student[id]?.[k] || 0)}</td>`)
        .join("");
      return `<tr><td>Student ${escapeHtml(id)}</td>${cells}</tr>`;
    })
    .join("");
  document.getElementById("ap-coverage-matrix").innerHTML = keys.length
    ? `<table class="data"><thead><tr><th>Student</th>${head}</tr></thead><tbody>${body}</tbody></table>
       <p class="hint compact">${cov.observation_count} observation(s)</p>`
    : `<p class="hint">${cov.observation_count || 0} observation(s). None tagged with a process yet.</p>`;
  const log = document.getElementById("ap-coverage-log");
  if (log) {
    log.innerHTML = (obs.observations || [])
      .map((o) => `<li>${escapeHtml(o.scope)} · ${escapeHtml(o.note)}</li>`)
      .join("");
  }
}

window.addEventListener("lloves-score-round", (event) => {
  const detail = event.detail || {};
  overlayState = detail.overlayState;
  liveSessionId = Number(detail.liveSessionId || 0);
  classId = Number(detail.classId || classId);
  setPanelVisible(String(detail.roundKind || ""));
  if (panel && !panel.hidden) {
    loadPhraseCatalog()
      .then(() => {
        renderScope();
        renderProcessesAndPhrases();
        return refreshObservations();
      })
      .catch((err) => {
        panel.querySelector("h3")?.insertAdjacentText("afterend", ` ${err.message || err}`);
      });
  }
});

if (panel) bindPanel();
if (coverageRoot) loadCoverage().catch(() => {});
