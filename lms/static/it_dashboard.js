/**
 * Admin Dashboard JS — tabs, offerings filters, archived toggle, history fetch.
 * Plain vanilla ES2020; no external dependencies.
 */

/**
 * Escape text for table cells.
 * @param {unknown} value
 * @returns {string}
 */
function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ── Tab switching ── */

/**
 * Initialise tab buttons and panels.
 * Buttons carry data-tab="<id>"; panels are <div id="tab-<id>">.
 */
function initTabs() {
  const buttons = document.querySelectorAll(".it-tab-btn");
  const panels = document.querySelectorAll(".it-tab-panel");

  function activate(targetTab) {
    buttons.forEach((btn) => {
      const on = btn.dataset.tab === targetTab;
      btn.classList.toggle("on", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    panels.forEach((panel) => {
      panel.hidden = panel.id !== "tab-" + targetTab;
    });
    try {
      sessionStorage.setItem("it-active-tab", targetTab);
    } catch (_) {}
  }

  buttons.forEach((btn) => {
    btn.addEventListener("click", () => activate(btn.dataset.tab));
  });

  const saved = (() => {
    try {
      return sessionStorage.getItem("it-active-tab");
    } catch (_) {
      return null;
    }
  })();
  const fromQuery = new URLSearchParams(window.location.search).get("tab");
  const initial =
    fromQuery && document.getElementById("tab-" + fromQuery)
      ? fromQuery
      : saved && document.getElementById("tab-" + saved)
        ? saved
        : "staff";
  activate(initial);
}

/* ── Offerings filters ── */

/**
 * Attach semester-select, teacher-input, and course-input filters
 * to the offerings table. Filters are applied client-side against
 * data-* attributes on each <tr>.
 */
function initOfferingsFilters() {
  const table = document.getElementById("offerings-table");
  if (!table) return;

  const semesterSel = document.getElementById("filter-semester");
  const teacherInput = document.getElementById("filter-teacher");
  const courseInput = document.getElementById("filter-course");

  function applyFilters() {
    const semVal = semesterSel ? semesterSel.value : "";
    const teacherVal = teacherInput
      ? teacherInput.value.trim().toLowerCase()
      : "";
    const courseVal = courseInput ? courseInput.value.trim().toLowerCase() : "";

    const showArchived =
      document.getElementById("show-archived-offerings")?.checked ?? false;

    table.querySelectorAll("tbody tr").forEach((row) => {
      const isArchived = row.dataset.archived === "true";
      if (isArchived && !showArchived) {
        row.hidden = true;
        return;
      }
      const semMatch =
        !semVal || semVal === "all" || row.dataset.semesterId === semVal;
      const teacherMatch =
        !teacherVal ||
        (row.dataset.teacher || "").toLowerCase().includes(teacherVal);
      const courseMatch =
        !courseVal ||
        (row.dataset.code || "").toLowerCase().includes(courseVal);
      row.hidden = !(semMatch && teacherMatch && courseMatch);
    });
  }

  semesterSel?.addEventListener("change", applyFilters);
  teacherInput?.addEventListener("input", applyFilters);
  courseInput?.addEventListener("input", applyFilters);

  applyFilters();
}

/* ── Show-archived toggle ── */

/**
 * Wire the "Show archived" checkbox to toggle staff rows with
 * data-archived="true".  Hidden by default.
 */
function initArchivedToggle() {
  const checkbox = document.getElementById("show-archived");
  if (!checkbox) return;

  function applyToggle() {
    const show = checkbox.checked;
    document
      .querySelectorAll('#tab-staff tbody tr[data-archived="true"]')
      .forEach((row) => {
        row.hidden = !show;
      });
  }

  checkbox.addEventListener("change", applyToggle);
  applyToggle();
}

/* ── History fetch via <details> ── */

/**
 * For each "History" <details> element carrying data-history-url and
 * data-staff-id, fetch the JSON on first open and render a mini-table.
 * Subsequent opens use the already-rendered content.
 */
function initHistoryDetails() {
  document.querySelectorAll("details.history-details").forEach((details) => {
    let loaded = false;

    details.addEventListener("toggle", async () => {
      if (!details.open || loaded) return;
      loaded = true;

      const url = details.dataset.historyUrl;
      const container = details.querySelector(".history-body");
      if (!url || !container) return;

      container.textContent = "Loading…";

      try {
        const res = await fetch(url);
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();

        if (!data.history || data.history.length === 0) {
          container.textContent = "No past assignments.";
          return;
        }

        const tbl = document.createElement("table");
        tbl.className = "data";
        tbl.innerHTML =
          "<thead><tr><th>Semester</th><th>Course</th><th>Section</th></tr></thead>";
        const tbody = document.createElement("tbody");
        for (const row of data.history) {
          const tr = document.createElement("tr");
          tr.innerHTML =
            "<td>" +
            esc(row.semester_label || row.semester_id || "") +
            "</td><td>" +
            esc(row.ontario_code || "") +
            "</td><td>" +
            esc(row.section_code || row.ontario_code || "") +
            "</td>";
          tbody.appendChild(tr);
        }
        tbl.appendChild(tbody);
        container.textContent = "";
        container.appendChild(tbl);
      } catch (err) {
        container.textContent = "Could not load history.";
      }
    });
  });
}

/** Escape a string for safe innerHTML insertion. */
function esc(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ── Course code typeahead (assign page) ── */

/**
 * Refresh the Ontario course-code <datalist> as the admin types.
 * Uses GET /it/courses?q= so options appear in realtime, not only on blur.
 */
function initCourseCodeTypeahead() {
  const codeInput = document.getElementById("ontario_code");
  const list = document.getElementById("course-codes");
  if (!codeInput || !list) return;

  let timer = 0;
  let lastQ = null;

  /**
   * Replace datalist options from a courses JSON payload.
   * @param {{code: string, title?: string}[]} courses
   */
  function paintOptions(courses) {
    list.innerHTML = "";
    for (const row of courses || []) {
      const opt = document.createElement("option");
      opt.value = row.code || "";
      opt.label = row.title || row.code || "";
      opt.textContent = row.title || row.code || "";
      list.appendChild(opt);
    }
  }

  /**
   * Debounced fetch against /it/courses for the current input value.
   */
  async function refreshOptions() {
    const q = (codeInput.value || "").trim();
    if (q === lastQ) return;
    lastQ = q;
    try {
      const rv = await fetch("/it/courses?q=" + encodeURIComponent(q));
      if (!rv.ok) return;
      const data = await rv.json();
      paintOptions(data.courses || []);
    } catch (_) {}
  }

  codeInput.addEventListener("input", () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      refreshOptions().catch(() => {});
    }, 150);
  });
  refreshOptions().catch(() => {});
}

/* ── Base-layer picker (assign page reuse) ── */

/**
 * Keep Base layer as a single None choice (empty copied_from_offering_id).
 * Saved offerings are not listed; assign still seeds JSON or shares a library.
 */
function initBasePicker() {
  const baseSelect = document.getElementById("copied_from_offering_id");
  if (!baseSelect) return;
  baseSelect.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "None";
  none.selected = true;
  baseSelect.appendChild(none);
}

/* ── Archived offerings toggle ── */

/**
 * Toggle visibility of archived offering rows in the offerings table.
 * Controlled by the #show-archived-offerings checkbox.
 */
function initArchivedOfferingsToggle() {
  const checkbox = document.getElementById("show-archived-offerings");
  if (!checkbox) return;

  function applyToggle() {
    const show = checkbox.checked;
    document
      .querySelectorAll('#offerings-table tbody tr[data-archived="true"]')
      .forEach((row) => {
        row.hidden = !show;
      });
  }

  checkbox.addEventListener("change", applyToggle);
  applyToggle();
}

/* ── Bootstrap ── */

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initOfferingsFilters();
  initArchivedToggle();
  initArchivedOfferingsToggle();
  initHistoryDetails();
  initCourseCodeTypeahead();
  initBasePicker();
  initSettingsTab();
  initPackStatusLines();
  initLiveClassesTab();
  initLiveProblemsTab();
  initQuickPhrasesTab();
  initPermanentDeleteForms();
});

/**
 * Poll busy Pack Status cells so Admin sees one live progress line.
 */
function initPackStatusLines() {
  const cells = document.querySelectorAll(".it-pack-status[data-status-url]");
  cells.forEach((cell) => {
    const url = cell.getAttribute("data-status-url");
    if (!url) return;
    const paint = (status) => {
      const badge = cell.querySelector(".badge");
      const line = cell.querySelector(".it-pack-line");
      if (badge) {
        badge.textContent = status.badge || badge.textContent;
        badge.className = "badge " + (status.badge_class || "");
      }
      if (line) {
        const text = status.line || status.detail || "";
        line.textContent = text;
        line.title = text;
      }
      cell.dataset.packBusy = status.busy ? "true" : "false";
    };
    const tick = async () => {
      try {
        const rv = await fetch(url, { headers: { Accept: "application/json" } });
        if (rv.ok) {
          const status = await rv.json();
          paint(status);
          if (!status.busy) return;
        }
      } catch (_) {
        /* keep polling through a blip */
      }
      window.setTimeout(tick, 700);
    };
    if (cell.dataset.packBusy === "true") tick();
  });
}

/** Poll interval for the Live Classes tab (ms). */
const LIVE_CLASSES_POLL_MS = 4000;

/**
 * Format an ISO-ish started_at value for the Live Classes table.
 * @param {string|null|undefined} value
 * @returns {string}
 */
function formatLiveSessionStarted(value) {
  if (!value) return "—";
  const raw = String(value).trim();
  if (!raw) return "—";
  const normalized = /Z$|[+-]\d{2}:?\d{2}$/.test(raw) ? raw : raw + "Z";
  const dt = new Date(normalized);
  if (Number.isNaN(dt.getTime())) return raw;
  try {
    return dt.toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch (_) {
    return raw;
  }
}

/**
 * Build a teacher label from live-session API fields.
 * @param {Record<string, unknown>} row
 * @returns {string}
 */
function liveSessionTeacherLabel(row) {
  const name = String(row.teacher_display_name || "").trim();
  const email = String(row.teacher_email || "").trim();
  if (name && email) return name + " (" + email + ")";
  return name || email || "—";
}

/**
 * Build a course/section label from live-session API fields.
 * @param {Record<string, unknown>} row
 * @returns {string}
 */
function liveSessionCourseLabel(row) {
  const section = String(row.section_code || "").trim();
  if (section) return section;
  const ontario = String(row.ontario_code || "").trim();
  return ontario || "—";
}

/**
 * Poll GET /api/live-sessions/active and paint the Live Classes table.
 * Starts immediately; continues while the Admin dashboard is open.
 */
function initLiveClassesTab() {
  const tbody = document.getElementById("live-classes-tbody");
  const status = document.getElementById("live-classes-status");
  const endAllBtn = document.getElementById("live-classes-end-all");
  if (!tbody) return;

  let timer = 0;
  let inFlight = false;

  /**
   * POST end for one live session, then refresh the table.
   * @param {number} sessionId
   */
  async function endSession(sessionId) {
    const rv = await fetch(`/api/it/live-sessions/${sessionId}/end`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await rv.json().catch(() => ({}));
    if (!rv.ok || !data.ok) {
      throw new Error(data.error || "HTTP " + rv.status);
    }
  }

  /**
   * Replace tbody rows from an active-sessions payload.
   * @param {Record<string, unknown>[]} sessions
   */
  function paintSessions(sessions) {
    tbody.textContent = "";
    if (!sessions.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 6;
      td.className = "hint";
      td.textContent = "No live classes running.";
      tr.appendChild(td);
      tbody.appendChild(tr);
      if (status) status.textContent = "0 active";
      return;
    }
    for (const row of sessions) {
      const tr = document.createElement("tr");
      const cells = [
        liveSessionTeacherLabel(row),
        liveSessionCourseLabel(row),
        String(row.session_code || "—"),
        formatLiveSessionStarted(row.started_at),
        String(row.attendee_count ?? 0),
      ];
      cells.forEach((text, idx) => {
        const td = document.createElement("td");
        if (idx === 2) {
          const code = document.createElement("code");
          code.className = "live-code it-live-code";
          code.textContent = text;
          td.appendChild(code);
        } else {
          td.textContent = text;
        }
        tr.appendChild(td);
      });
      const actionTd = document.createElement("td");
      const endBtn = document.createElement("button");
      endBtn.type = "button";
      endBtn.className = "secondary it-live-end-btn";
      endBtn.textContent = "End";
      endBtn.dataset.sessionId = String(row.id || "");
      endBtn.addEventListener("click", () => {
        const id = Number(endBtn.dataset.sessionId);
        if (!Number.isFinite(id) || id <= 0) return;
        endBtn.disabled = true;
        endSession(id)
          .then(() => tick())
          .catch((err) => {
            endBtn.disabled = false;
            if (status) {
              status.textContent = "End failed: " + String(err.message || err);
            }
          });
      });
      actionTd.appendChild(endBtn);
      tr.appendChild(actionTd);
      tbody.appendChild(tr);
    }
    if (status) {
      status.textContent =
        sessions.length === 1 ? "1 active" : sessions.length + " active";
    }
  }

  /**
   * Fetch active sessions once and schedule the next poll.
   */
  async function tick() {
    if (inFlight) return;
    inFlight = true;
    try {
      const rv = await fetch("/api/live-sessions/active", {
        headers: { Accept: "application/json" },
      });
      if (!rv.ok) throw new Error("HTTP " + rv.status);
      const data = await rv.json();
      if (!data.ok) throw new Error(data.error || "Load failed");
      paintSessions(Array.isArray(data.sessions) ? data.sessions : []);
    } catch (err) {
      if (status) {
        status.textContent = "Could not refresh: " + String(err.message || err);
      }
    } finally {
      inFlight = false;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        tick().catch(() => {});
      }, LIVE_CLASSES_POLL_MS);
    }
  }

  endAllBtn?.addEventListener("click", () => {
    if (!window.confirm("End every active live class session?")) return;
    endAllBtn.disabled = true;
    fetch("/api/it/live-sessions/end-all", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: "{}",
    })
      .then(async (rv) => {
        const data = await rv.json().catch(() => ({}));
        if (!rv.ok || !data.ok) {
          throw new Error(data.error || "HTTP " + rv.status);
        }
        if (status) {
          const n = Number(data.ended_count || 0);
          status.textContent =
            n === 1 ? "Ended 1 session." : "Ended " + n + " sessions.";
        }
        return tick();
      })
      .catch((err) => {
        if (status) {
          status.textContent = "End all failed: " + String(err.message || err);
        }
      })
      .finally(() => {
        endAllBtn.disabled = false;
      });
  });

  tick().catch(() => {});
}

/**
 * Require typing the staff email, then a browser confirm, before permanent delete.
 */
function initPermanentDeleteForms() {
  document.querySelectorAll("form.it-delete-form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const expected = String(form.dataset.confirmEmail || "")
        .trim()
        .toLowerCase();
      const input = form.querySelector('input[name="confirm_email"]');
      const typed = String(input?.value || "")
        .trim()
        .toLowerCase();
      if (!expected || typed !== expected) {
        event.preventDefault();
        window.alert(
          "Type the staff email exactly to confirm permanent delete."
        );
        input?.focus();
        return;
      }
      const ok = window.confirm(
        `Permanently delete ${expected}? This cannot be undone.`
      );
      if (!ok) event.preventDefault();
    });
  });
}

/**
 * Persist Admin attendance settings from the Settings tab.
 */
function initSettingsTab() {
  const box = document.getElementById("only-live-class-days");
  const modeSelect = document.getElementById("staff-2fa-mode");
  const status = document.getElementById("settings-status");
  if (!box && !modeSelect) return;

  /**
   * POST Admin settings and show status.
   * @param {object} payload
   * @param {() => void} [revert]
   */
  async function saveSettings(payload, revert) {
    if (status) status.textContent = "Saving…";
    try {
      const rv = await fetch("/api/it/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await rv.json();
      if (!rv.ok || !data.ok) throw new Error(data.error || "Save failed");
      if (status) status.textContent = "Saved.";
      return data;
    } catch (err) {
      if (typeof revert === "function") revert();
      if (status) status.textContent = String(err.message || err);
      return null;
    }
  }

  box?.addEventListener("change", async () => {
    await saveSettings({ only_live_class_days: box.checked }, () => {
      box.checked = !box.checked;
    });
  });
  modeSelect?.addEventListener("change", async () => {
    const previous = modeSelect.dataset.saved || "first_login";
    const data = await saveSettings({ staff_2fa_mode: modeSelect.value }, () => {
      modeSelect.value = previous;
    });
    if (data && data.staff_2fa_mode) {
      modeSelect.dataset.saved = data.staff_2fa_mode;
    }
  });
}

/**
 * Load and mutate the curated live-problem bank.
 */
function initLiveProblemsTab() {
  const table = document.querySelector("#it-problems-table tbody");
  const form = document.getElementById("it-problem-form");
  if (!table || !form) return;

  async function refresh() {
    const rv = await fetch("/api/it/live-problems");
    const data = await rv.json();
    table.innerHTML = (data.problems || [])
      .map((p) => {
        const procs = (p.processes || []).join(", ");
        const active = Number(p.active) === 1;
        return `<tr>
          <td>${escapeHtml(p.title)}</td>
          <td>${escapeHtml(p.kind)}</td>
          <td>${escapeHtml(p.module_hint || "")}</td>
          <td>${escapeHtml(procs)}</td>
          <td><button type="button" class="btn secondary" data-toggle-problem="${p.id}" data-active="${active ? "0" : "1"}">${active ? "Deactivate" : "Activate"}</button></td>
        </tr>`;
      })
      .join("");
  }

  table.addEventListener("click", async (event) => {
    const btn = event.target.closest("[data-toggle-problem]");
    if (!btn) return;
    await fetch(`/api/it/live-problems/${btn.dataset.toggleProblem}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active: btn.dataset.active === "1" }),
    });
    await refresh();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const processes = (document.getElementById("it-p-proc")?.value || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    await fetch("/api/it/live-problems", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: document.getElementById("it-p-title").value,
        kind: document.getElementById("it-p-kind").value,
        module_hint: document.getElementById("it-p-hint").value,
        stem_html: document.getElementById("it-p-stem").value,
        task_html: document.getElementById("it-p-task").value,
        processes,
        ontario_code: "MCF3M",
      }),
    });
    form.reset();
    await refresh();
  });

  refresh().catch(() => {});
}

/**
 * Load and mutate quick-evidence phrases.
 */
function initQuickPhrasesTab() {
  const table = document.querySelector("#it-phrases-table tbody");
  const form = document.getElementById("it-phrase-form");
  if (!table || !form) return;

  async function refresh() {
    const rv = await fetch("/api/it/quick-phrases");
    const data = await rv.json();
    table.innerHTML = (data.phrases || [])
      .map((p) => {
        const active = Number(p.active) === 1;
        return `<tr>
          <td>${escapeHtml(p.label)}</td>
          <td>${escapeHtml(p.process_key)}</td>
          <td>${escapeHtml(p.category)}</td>
          <td>${active ? "yes" : "no"}</td>
          <td><button type="button" class="btn secondary" data-toggle-phrase="${p.id}" data-active="${active ? "0" : "1"}">${active ? "Deactivate" : "Activate"}</button></td>
        </tr>`;
      })
      .join("");
  }

  table.addEventListener("click", async (event) => {
    const btn = event.target.closest("[data-toggle-phrase]");
    if (!btn) return;
    const id = btn.dataset.togglePhrase;
    const rv = await fetch("/api/it/quick-phrases");
    const data = await rv.json();
    const existing = (data.phrases || []).find((p) => String(p.id) === String(id));
    if (!existing) return;
    await fetch(`/api/it/quick-phrases/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...existing, active: btn.dataset.active === "1" }),
    });
    await refresh();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await fetch("/api/it/quick-phrases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        label: document.getElementById("it-ph-label").value,
        process_key: document.getElementById("it-ph-key").value,
        category: document.getElementById("it-ph-cat").value,
        description: document.getElementById("it-ph-desc").value,
      }),
    });
    form.reset();
    await refresh();
  });

  refresh().catch(() => {});
}
