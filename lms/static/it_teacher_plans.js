/**
 * MCK-540: IT teacher plans admin console (Teachers / Plans / Audit tabs).
 */
(function () {
  "use strict";

  const root = document.getElementById("it-teacher-plans-root");
  if (!root) return;

  const LABELS = JSON.parse(root.dataset.labels || "{}");
  const REGISTRY = JSON.parse(root.dataset.registry || "[]");
  const SCHOOL = root.dataset.school || "";

  const state = {
    teachers: [],
    loading: true,
    loadError: false,
    search: "",
    planFilter: "all",
    selected: new Set(),
    activeTeacherId: null,
    history: [],
    audit: [],
    auditFilters: { teacher_id: "", feature_key: "", from: "", to: "" },
    tab: "teachers",
    toast: null,
    bulkBanner: null,
  };

  const els = {
    teachersBody: root.querySelector("[data-teachers-body]"),
    teachersEmpty: root.querySelector("[data-teachers-empty]"),
    teachersLoading: root.querySelector("[data-teachers-loading]"),
    search: root.querySelector("[data-search]"),
    planFilter: root.querySelector("[data-plan-filter]"),
    bulkBtn: root.querySelector("[data-bulk-plan]"),
    selectAll: root.querySelector("[data-select-all]"),
    stage: root.querySelector("[data-stage]"),
    drawer: root.querySelector("[data-drawer]"),
    drawerBody: root.querySelector("[data-drawer-body]"),
    drawerTitle: root.querySelector("[data-drawer-subtitle]"),
    toastHost: root.querySelector("[data-toast-host]"),
    bulkBanner: root.querySelector("[data-bulk-banner]"),
    auditBody: root.querySelector("[data-audit-body]"),
    auditTeacher: root.querySelector("[data-audit-teacher]"),
    auditFeature: root.querySelector("[data-audit-feature]"),
    auditFrom: root.querySelector("[data-audit-from]"),
    auditTo: root.querySelector("[data-audit-to]"),
    tabs: root.querySelectorAll("[data-tab]"),
    panes: root.querySelectorAll("[data-pane]"),
    confirmHost: root.querySelector("[data-confirm-host]"),
  };

  function labelFor(key) {
    return LABELS[key] || key;
  }

  function planTitle(plan) {
    return plan === "pro" ? "Pro" : "Basic";
  }

  function chipState(feat) {
    const source = feat.source;
    const on = !!feat.effective;
    if (source === "plan") {
      return { cls: "", text: `Default · ${on ? "On" : "Off"}` };
    }
    if (source === "on") {
      return { cls: "on", text: "On" };
    }
    return { cls: "off", text: "Off" };
  }

  function nextMode(feat) {
    if (feat.source === "plan") return "on";
    if (feat.source === "on") return "off";
    return "plan";
  }

  function filteredTeachers() {
    const q = state.search.trim().toLowerCase();
    return state.teachers.filter((t) => {
      if (state.planFilter !== "all" && t.plan !== state.planFilter) return false;
      if (!q) return true;
      if (String(t.handle || "").toLowerCase().includes(q)) return true;
      return String(t.id).includes(q);
    });
  }

  async function api(path, options) {
    const res = await fetch(path, {
      credentials: "same-origin",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error("request failed");
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  async function loadTeachers() {
    state.loading = true;
    state.loadError = false;
    renderTeachersShell();
    try {
      const data = await api("/it/api/teacher-plans");
      state.teachers = data.teachers || [];
      state.loading = false;
    } catch (_e) {
      state.loading = false;
      state.loadError = true;
    }
    renderTeachers();
    refreshAuditTeacherOptions();
  }

  function showToast(message, undoAction) {
    if (!els.toastHost) return;
    els.toastHost.innerHTML = "";
    const box = document.createElement("div");
    box.className = "it-tp-toast";
    box.setAttribute("role", "status");
    const text = document.createElement("span");
    text.textContent = message;
    box.appendChild(text);
    if (undoAction) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Undo";
      btn.addEventListener("click", () => undoAction());
      box.appendChild(btn);
    }
    els.toastHost.appendChild(box);
    state.toast = box;
    window.setTimeout(() => {
      if (state.toast === box) {
        box.remove();
        state.toast = null;
      }
    }, 8000);
  }

  function confirmDialog(message, primaryLabel, onConfirm) {
    if (!els.confirmHost) {
      if (window.confirm(message)) onConfirm();
      return;
    }
    els.confirmHost.innerHTML = "";
    const backdrop = document.createElement("div");
    backdrop.className = "it-tp-confirm-backdrop";
    const panel = document.createElement("div");
    panel.className = "it-tp-confirm";
    const p = document.createElement("p");
    p.textContent = message;
    panel.appendChild(p);
    const actions = document.createElement("div");
    actions.className = "it-tp-confirm-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "it-tp-btn secondary";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", () => backdrop.remove());
    const ok = document.createElement("button");
    ok.type = "button";
    ok.className = "it-tp-btn";
    ok.textContent = primaryLabel;
    ok.addEventListener("click", () => {
      backdrop.remove();
      onConfirm();
    });
    actions.appendChild(cancel);
    actions.appendChild(ok);
    panel.appendChild(actions);
    backdrop.appendChild(panel);
    backdrop.addEventListener("click", (ev) => {
      if (ev.target === backdrop) backdrop.remove();
    });
    els.confirmHost.appendChild(backdrop);
  }

  function teacherById(id) {
    return state.teachers.find((t) => Number(t.id) === Number(id));
  }

  function featByKey(teacher, key) {
    return (teacher.features || []).find((f) => f.key === key);
  }

  async function postPlan(teacherId, plan, undoCtx) {
    try {
      await api(`/it/api/teacher-plans/${teacherId}/plan`, {
        method: "POST",
        body: JSON.stringify({ plan }),
      });
      await loadTeachers();
      showToast(`Plan updated to ${planTitle(plan)}`, undoCtx ? () => undoWrite(undoCtx) : null);
    } catch (_e) {
      showToast("Couldn't save. Try again.");
    }
  }

  async function postOverride(teacherId, featureKey, mode, undoCtx) {
    try {
      await api(`/it/api/teacher-plans/${teacherId}/features/${featureKey}`, {
        method: "POST",
        body: JSON.stringify({ mode }),
      });
      await loadTeachers();
      const name = labelFor(featureKey);
      let msg = `${name} turned on`;
      if (mode === "off") msg = `${name} turned off`;
      if (mode === "plan") msg = `${name} reset to default`;
      showToast(msg, undoCtx ? () => undoWrite(undoCtx) : null);
    } catch (_e) {
      showToast("Couldn't save. Try again.");
    }
  }

  async function undoWrite(ctx) {
    try {
      if (ctx.kind === "plan") {
        await api(`/it/api/teacher-plans/${ctx.teacherId}/plan`, {
          method: "POST",
          body: JSON.stringify({ plan: ctx.oldPlan }),
        });
      } else {
        await api(`/it/api/teacher-plans/${ctx.teacherId}/features/${ctx.featureKey}`, {
          method: "POST",
          body: JSON.stringify({ mode: ctx.oldMode }),
        });
      }
      await loadTeachers();
      showToast("Change undone");
    } catch (_e) {
      showToast("Couldn't undo. Try again.");
    }
  }

  function openDrawer(teacherId) {
    const teacher = teacherById(teacherId);
    if (!teacher || !els.drawer) return;
    state.activeTeacherId = teacherId;
    els.stage.classList.add("with-drawer");
    els.drawer.hidden = false;
    if (els.drawerTitle) {
      els.drawerTitle.textContent = `${teacher.handle} · ${SCHOOL}`;
    }
    els.drawerBody.innerHTML = "<p class='it-tp-loading'>Loading…</p>";
    api(`/it/api/teacher-plans/${teacherId}/history`)
      .then((data) => {
        const rows = data.history || [];
        if (!rows.length) {
          els.drawerBody.innerHTML = "<p class='it-tp-empty'>No changes yet.</p>";
          return;
        }
        els.drawerBody.innerHTML = rows
          .map(
            (row) => `
          <div class="hist-item">
            <span class="dot"></span>
            <div>
              <div class="hist-when">${row.when || ""}</div>
              <div class="hist-what">${row.what_label}
                <span class="change">${row.old_value} → ${row.new_value}</span>
              </div>
              <div class="hist-who">${row.who || ""}</div>
            </div>
          </div>`
          )
          .join("");
      })
      .catch(() => {
        els.drawerBody.innerHTML =
          "<p class='it-tp-empty'>Couldn't load history.</p>";
      });
    renderTeachers();
  }

  function closeDrawer() {
    state.activeTeacherId = null;
    if (els.stage) els.stage.classList.remove("with-drawer");
    if (els.drawer) els.drawer.hidden = true;
    renderTeachers();
  }

  function renderTeachersShell() {
    if (els.teachersLoading) els.teachersLoading.hidden = !state.loading;
    if (els.teachersEmpty) els.teachersEmpty.hidden = true;
    if (els.teachersBody) els.teachersBody.innerHTML = "";
  }

  function renderTeachers() {
    if (state.loading) return;
    if (state.loadError) {
      if (els.teachersEmpty) {
        els.teachersEmpty.hidden = false;
        els.teachersEmpty.textContent =
          "Couldn't load teachers. Refresh to try again.";
      }
      return;
    }
    const rows = filteredTeachers();
    if (!rows.length) {
      if (els.teachersEmpty) {
        els.teachersEmpty.hidden = false;
        els.teachersEmpty.textContent = "No teachers in this school yet";
      }
      if (els.teachersBody) els.teachersBody.innerHTML = "";
      return;
    }
    if (els.teachersEmpty) els.teachersEmpty.hidden = true;
  if (els.teachersLoading) els.teachersLoading.hidden = true;
    const selectedCount = state.selected.size;
    if (els.bulkBtn) {
      els.bulkBtn.disabled = selectedCount === 0;
      els.bulkBtn.textContent =
        selectedCount > 0
          ? `Set plan for ${selectedCount} teachers`
          : "Set plan for 0 teachers";
    }
    if (!els.teachersBody) return;
    els.teachersBody.innerHTML = "";
    rows.forEach((teacher) => {
      const tr = document.createElement("tr");
      tr.dataset.teacherId = String(teacher.id);
      if (Number(state.activeTeacherId) === Number(teacher.id)) {
        tr.classList.add("it-tp-row-active");
      }
      const checked = state.selected.has(Number(teacher.id));
      const last = teacher.last_changed_display || "Never";
      const planCls = teacher.plan === "pro" ? "pro" : "";
      const keys = teacher.row_feature_keys || [];
      const chips = keys
        .map((key) => {
          const feat = featByKey(teacher, key) || {
            key,
            source: "plan",
            effective: false,
          };
          const chip = chipState(feat);
          return `<button type="button" class="it-tp-ov ${chip.cls}" data-chip-key="${key}" data-teacher-id="${teacher.id}">
            <span class="feat-name">${labelFor(key)}</span>
            <span class="state">${chip.text}</span>
          </button>`;
        })
        .join("");
      tr.innerHTML = `
        <td class="it-tp-checkcol"><input type="checkbox" data-row-select="${teacher.id}" ${checked ? "checked" : ""} aria-label="Select ${teacher.handle}"></td>
        <td class="teacher-name">${teacher.handle}</td>
        <td class="school">${SCHOOL}</td>
        <td><select class="it-tp-plan-select ${planCls}" data-plan-select="${teacher.id}" aria-label="Plan for ${teacher.handle}">
          <option value="basic" ${teacher.plan === "basic" ? "selected" : ""}>Basic</option>
          <option value="pro" ${teacher.plan === "pro" ? "selected" : ""}>Pro</option>
        </select></td>
        <td><div class="it-tp-overrides">${chips}</div></td>
        <td class="last ${teacher.last_changed_at ? "" : "muted"}">${last}</td>`;
      tr.addEventListener("click", (ev) => {
        const tag = ev.target;
        if (
          tag.closest("input") ||
          tag.closest("select") ||
          tag.closest("[data-chip-key]")
        ) {
          return;
        }
        openDrawer(teacher.id);
      });
      tr.querySelector(`[data-row-select="${teacher.id}"]`).addEventListener("change", (ev) => {
        const id = Number(teacher.id);
        if (ev.target.checked) state.selected.add(id);
        else state.selected.delete(id);
        renderTeachers();
      });
      const sel = tr.querySelector(`[data-plan-select="${teacher.id}"]`);
      sel.addEventListener("change", () => {
        const newPlan = sel.value;
        const oldPlan = teacher.plan;
        if (newPlan === oldPlan) return;
        sel.value = oldPlan;
        confirmDialog(
          `Change plan to ${planTitle(newPlan)} for ${teacher.handle}?`,
          `Set ${planTitle(newPlan)}`,
          () =>
            postPlan(teacher.id, newPlan, {
              kind: "plan",
              teacherId: teacher.id,
              oldPlan,
            })
        );
      });
      tr.querySelectorAll("[data-chip-key]").forEach((btn) => {
        btn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          const key = btn.dataset.chipKey;
          const feat = featByKey(teacher, key);
          if (!feat) return;
          const mode = nextMode(feat);
          const name = labelFor(key);
          let msg = `Turn ${name} on for ${teacher.handle}?`;
          let primary = "Turn on";
          if (mode === "off") {
            msg = `Turn ${name} off for ${teacher.handle}?`;
            primary = "Turn off";
          } else if (mode === "plan") {
            msg = `Reset ${name} to default for ${teacher.handle}?`;
            primary = "Reset";
          }
          const undoMode = feat.source;
          confirmDialog(msg, primary, () =>
            postOverride(teacher.id, key, mode, {
              kind: "override",
              teacherId: teacher.id,
              featureKey: key,
              oldMode: undoMode,
            })
          );
        });
      });
      els.teachersBody.appendChild(tr);
    });
  }

  async function bulkSetPlan() {
    const ids = Array.from(state.selected);
    if (!ids.length) return;
    const targetSel = root.querySelector("[data-bulk-target-plan]");
    const normalized = targetSel ? String(targetSel.value || "pro") : "pro";
    if (els.bulkBanner) els.bulkBanner.hidden = true;
    confirmDialog(
      `Set plan to ${planTitle(normalized)} for ${ids.length} teachers?`,
      `Set ${planTitle(normalized)}`,
      async () => {
        try {
          await api("/it/api/teacher-plans/bulk-plan", {
            method: "POST",
            body: JSON.stringify({ teacher_ids: ids, plan: normalized }),
          });
          state.selected.clear();
          await loadTeachers();
          showToast(`Plan updated to ${planTitle(normalized)}`);
        } catch (_e) {
          if (els.bulkBanner) {
            els.bulkBanner.hidden = false;
            els.bulkBanner.textContent =
              "Nothing was changed. One or more teachers couldn't be updated.";
          }
        }
      }
    );
  }

  function refreshAuditTeacherOptions() {
    if (!els.auditTeacher) return;
    const current = els.auditTeacher.value;
    els.auditTeacher.innerHTML =
      '<option value="">All teachers</option>' +
      state.teachers
        .map(
          (t) =>
            `<option value="${t.id}" ${String(t.id) === current ? "selected" : ""}>${t.handle}</option>`
        )
        .join("");
  }

  async function loadAudit() {
    if (!els.auditBody) return;
    els.auditBody.innerHTML =
      "<tr><td colspan='5' class='it-tp-loading'>Loading teachers…</td></tr>";
    const q = new URLSearchParams();
    const f = state.auditFilters;
    if (f.teacher_id) q.set("teacher_id", f.teacher_id);
    if (f.feature_key) q.set("feature_key", f.feature_key);
    if (f.from) q.set("from", f.from);
    if (f.to) q.set("to", f.to);
    try {
      const data = await api(`/it/api/teacher-plans/audit?${q.toString()}`);
      const rows = data.audit || [];
      if (!rows.length) {
        els.auditBody.innerHTML =
          "<tr><td colspan='5' class='it-tp-empty'>No audit entries match these filters.</td></tr>";
        return;
      }
      els.auditBody.innerHTML = rows
        .map(
          (row) => `<tr>
          <td class="last">${row.when || ""}</td>
          <td class="teacher-name">${row.teacher}</td>
          <td>${row.who}</td>
          <td>${row.what_label}</td>
          <td><span class="change">${row.old_value} → ${row.new_value}</span></td>
        </tr>`
        )
        .join("");
    } catch (_e) {
      els.auditBody.innerHTML =
        "<tr><td colspan='5' class='it-tp-empty'>Couldn't load teachers. Refresh to try again.</td></tr>";
    }
  }

  function renderPlansTab() {
    const body = root.querySelector("[data-plans-body]");
    if (!body) return;
    body.innerHTML = REGISTRY.map(
      (row) => `<tr>
        <td>${row.label}</td>
        <td class="col-plan">${row.basic ? "✓" : "—"}</td>
        <td class="col-plan">✓</td>
      </tr>`
    ).join("");
  }

  function switchTab(name) {
    state.tab = name;
    els.tabs.forEach((tab) => {
      const on = tab.dataset.tab === name;
      tab.classList.toggle("on", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
    });
    els.panes.forEach((pane) => {
      pane.hidden = pane.dataset.pane !== name;
    });
    if (name === "audit") loadAudit();
    if (name === "plans") renderPlansTab();
  }

  els.tabs.forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });

  if (els.search) {
    els.search.addEventListener("input", () => {
      state.search = els.search.value;
      renderTeachers();
    });
  }
  if (els.planFilter) {
    els.planFilter.addEventListener("change", () => {
      state.planFilter = els.planFilter.value;
      renderTeachers();
    });
  }
  if (els.bulkBtn) {
    els.bulkBtn.addEventListener("click", bulkSetPlan);
  }
  if (els.selectAll) {
    els.selectAll.addEventListener("change", () => {
      const rows = filteredTeachers();
      if (els.selectAll.checked) {
        rows.forEach((t) => state.selected.add(Number(t.id)));
      } else {
        rows.forEach((t) => state.selected.delete(Number(t.id)));
      }
      renderTeachers();
    });
  }
  const closeBtn = root.querySelector("[data-drawer-close]");
  if (closeBtn) closeBtn.addEventListener("click", closeDrawer);

  [els.auditTeacher, els.auditFeature, els.auditFrom, els.auditTo].forEach((el) => {
    if (!el) return;
    el.addEventListener("change", () => {
      state.auditFilters = {
        teacher_id: els.auditTeacher ? els.auditTeacher.value : "",
        feature_key: els.auditFeature ? els.auditFeature.value : "",
        from: els.auditFrom ? els.auditFrom.value : "",
        to: els.auditTo ? els.auditTo.value : "",
      };
      if (state.tab === "audit") loadAudit();
    });
  });

  loadTeachers();
})();