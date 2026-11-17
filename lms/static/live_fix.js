/**
 * In-class Flag, student problem report, and End Class report.
 * Notes post once. This file does not poll.
 */
(function () {
  const actions = [];

  /**
   * Keep the last ten button ids. Labels and names are ignored.
   * @param {Event} event
   * @returns {void}
   */
  function rememberAction(event) {
    const target = event.target instanceof Element ? event.target : null;
    const el = target ? target.closest("button, [role='button']") : null;
    if (!(el instanceof Element)) return;
    const id = el.id || el.getAttribute("data-action") || "";
    if (!/^[A-Za-z][A-Za-z0-9_.:-]{0,63}$/.test(id)) return;
    if (id.indexOf("live-fix") === 0) return;
    actions.push(id);
    if (actions.length > 10) actions.shift();
  }

  /**
   * Recent button ids for the next note.
   * @returns {string[]}
   */
  function recentActions() {
    return actions.slice(-10);
  }

  /**
   * True when this browser can dictate into a text field.
   * @returns {boolean}
   */
  function dictationSupported() {
    return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
  }

  /**
   * Fill an input from the browser speech recognizer. No network call.
   * @param {HTMLInputElement} input
   * @param {HTMLButtonElement} button
   * @returns {void}
   */
  function startDictation(input, button) {
    const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Ctor) return;
    const rec = new Ctor();
    rec.lang = "en-CA";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.onresult = function (event) {
      const text = (event.results && event.results[0] && event.results[0][0] && event.results[0][0].transcript) || "";
      const extra = String(text).trim();
      if (!extra) return;
      input.value = input.value ? input.value + " " + extra : extra;
    };
    rec.onend = function () {
      button.setAttribute("aria-pressed", "false");
    };
    button.setAttribute("aria-pressed", "true");
    try {
      rec.start();
    } catch (_err) {
      button.setAttribute("aria-pressed", "false");
    }
  }

  /**
   * POST a note and do not wait on the class. Failures stay on the status line.
   * @param {string} url
   * @param {string} note
   * @param {HTMLElement|null} status
   * @returns {void}
   */
  function sendNote(url, note, status) {
    const body = JSON.stringify({ note: note, actions: recentActions() });
    fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: body,
      keepalive: true,
    })
      .then(function (res) {
        if (res.ok) return;
        if (status) status.textContent = "Not saved. The class is still running.";
      })
      .catch(function () {
        if (status) status.textContent = "Not saved. The class is still running.";
      });
  }

  /**
   * Live session id from the teacher shell, when a class is open.
   * @returns {string}
   */
  function liveSessionId() {
    const root = document.getElementById("ap-root");
    return root ? String(root.getAttribute("data-live-session-id") || "") : "";
  }

  /**
   * Class id from the live shell or the attendance report list.
   * @returns {string}
   */
  function classId() {
    const root = document.getElementById("ap-root") || document.getElementById("live-fix-history");
    return root ? String(root.getAttribute("data-class-id") || "") : "";
  }

  /**
   * Paint the teacher's flags. Notes are text, never HTML.
   * @param {HTMLElement} list
   * @param {Array<Record<string, unknown>>} flags
   * @param {string} sessionId
   * @returns {void}
   */
  function paintFlags(list, flags, sessionId) {
    list.replaceChildren();
    flags.forEach(function (flag) {
      const item = document.createElement("li");
      const text = document.createElement("span");
      const title = String(flag.tidy_title || flag.note || "Flag");
      text.textContent = title;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "live-fix-delete";
      remove.textContent = "Delete";
      remove.addEventListener("click", function () {
        fetch("/api/live-sessions/" + sessionId + "/flags/" + String(flag.id), {
          method: "DELETE",
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        }).then(function (res) {
          if (res.ok) item.remove();
        });
      });
      item.appendChild(text);
      item.appendChild(remove);
      list.appendChild(item);
    });
  }

  /**
   * Load flags once, when the teacher opens the box.
   * @param {HTMLElement} list
   * @param {string} sessionId
   * @returns {void}
   */
  function loadFlags(list, sessionId) {
    if (!sessionId) return;
    fetch("/api/live-sessions/" + sessionId + "/flags", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (res) {
        return res.ok ? res.json() : { flags: [] };
      })
      .then(function (body) {
        paintFlags(list, Array.isArray(body.flags) ? body.flags : [], sessionId);
      })
      .catch(function () {});
  }

  /**
   * Open the teacher note box and focus the line.
   * @returns {void}
   */
  function openTeacherFlag() {
    const dialog = document.getElementById("live-fix-dialog");
    const input = document.getElementById("live-fix-note");
    const list = document.getElementById("live-fix-list");
    if (!(dialog instanceof HTMLDialogElement) || !(input instanceof HTMLInputElement)) return;
    if (typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
    input.focus();
    if (list instanceof HTMLElement) loadFlags(list, liveSessionId());
  }

  /**
   * Wire the teacher Flag button, Shift+F, and optional dictation.
   * @returns {void}
   */
  function bindTeacher() {
    const button = document.getElementById("live-fix-flag");
    const dialog = document.getElementById("live-fix-dialog");
    const form = document.getElementById("live-fix-form");
    const input = document.getElementById("live-fix-note");
    const cancel = document.getElementById("live-fix-cancel");
    const mic = document.getElementById("live-fix-mic");
    const status = document.getElementById("live-fix-status");
    if (!(button instanceof HTMLButtonElement) || !(dialog instanceof HTMLDialogElement)) return;
    if (!(form instanceof HTMLFormElement) || !(input instanceof HTMLInputElement)) return;
    button.addEventListener("click", function () {
      if (status) status.textContent = "";
      openTeacherFlag();
    });
    cancel?.addEventListener("click", function () {
      dialog.close();
    });
    if (mic instanceof HTMLButtonElement && dictationSupported()) {
      mic.hidden = false;
      mic.addEventListener("click", function () {
        startDictation(input, mic);
      });
    }
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      const sessionId = liveSessionId();
      if (!sessionId) {
        if (status) status.textContent = "Start the class first.";
        return;
      }
      const note = input.value;
      input.value = "";
      dialog.close();
      sendNote("/api/live-sessions/" + sessionId + "/flags", note, status);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "F" && event.key !== "f") return;
      if (!event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return;
      const target = event.target;
      if (target instanceof HTMLElement) {
        const tag = target.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable) return;
      }
      event.preventDefault();
      openTeacherFlag();
    });
  }

  /**
   * Wire the student Report a problem control.
   * @returns {void}
   */
  function bindStudent() {
    const button = document.getElementById("live-fix-student");
    const dialog = document.getElementById("live-fix-student-dialog");
    const form = document.getElementById("live-fix-student-form");
    const input = document.getElementById("live-fix-student-note");
    const cancel = document.getElementById("live-fix-student-cancel");
    const mic = document.getElementById("live-fix-student-mic");
    const status = document.getElementById("live-fix-student-status");
    if (!(button instanceof HTMLButtonElement) || !(dialog instanceof HTMLDialogElement)) return;
    if (!(form instanceof HTMLFormElement) || !(input instanceof HTMLInputElement)) return;
    button.addEventListener("click", function () {
      if (status) status.textContent = "";
      if (typeof dialog.showModal === "function") dialog.showModal();
      input.focus();
    });
    cancel?.addEventListener("click", function () {
      dialog.close();
    });
    if (mic instanceof HTMLButtonElement && dictationSupported()) {
      mic.hidden = false;
      mic.addEventListener("click", function () {
        startDictation(input, mic);
      });
    }
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      const note = input.value;
      input.value = "";
      dialog.close();
      sendNote("/api/student/flags", note, status);
    });
  }

  /**
   * One flag block inside the report. Text only.
   * @param {HTMLElement} parent
   * @param {Record<string, unknown>} flag
   * @returns {void}
   */
  function appendFlag(parent, flag) {
    const block = document.createElement("p");
    const title = document.createElement("strong");
    title.textContent = String(flag.tidy_title || flag.note || "Note");
    block.appendChild(title);
    const description = String(flag.tidy_description || "");
    if (description && description !== String(flag.tidy_title || "")) {
      block.appendChild(document.createTextNode(" " + description));
    } else if (flag.tidy_title && flag.note) {
      block.appendChild(document.createTextNode(" " + String(flag.note)));
    }
    const meta = document.createElement("span");
    meta.className = "live-fix-meta";
    const bits = [
      String(flag.reporter_role || ""),
      String(flag.reporter_anon || ""),
      String(flag.content_type || ""),
      String(flag.question_id || ""),
    ].filter(Boolean);
    const actionText = Array.isArray(flag.actions) ? flag.actions.join(", ") : "";
    if (actionText) bits.push(actionText);
    meta.textContent = bits.join(" · ");
    block.appendChild(document.createElement("br"));
    block.appendChild(meta);
    parent.appendChild(block);
  }

  /**
   * Paint a stored report into the dismissable dialog.
   * @param {Record<string, unknown>} report
   * @returns {void}
   */
  function paintReport(report) {
    const body = document.getElementById("live-fix-report-body");
    const dialog = document.getElementById("live-fix-report");
    if (!(body instanceof HTMLElement) || !(dialog instanceof HTMLDialogElement)) return;
    body.replaceChildren();
    const groups = Array.isArray(report.flags_by_slide) ? report.flags_by_slide : [];
    if (!groups.length) {
      const empty = document.createElement("p");
      empty.textContent = "No in-class notes.";
      body.appendChild(empty);
    }
    groups.forEach(function (group) {
      const section = document.createElement("section");
      section.className = "live-fix-slide";
      const heading = document.createElement("h3");
      heading.textContent = group.slide == null ? "Slide unknown" : "Slide " + String(group.slide);
      section.appendChild(heading);
      const flags = Array.isArray(group.flags) ? group.flags : [];
      flags.forEach(function (flag) {
        appendFlag(section, flag);
      });
      body.appendChild(section);
    });
    const sentry = report.sentry && typeof report.sentry === "object" ? report.sentry : {};
    const sentryHead = document.createElement("h3");
    sentryHead.textContent = "Sentry";
    body.appendChild(sentryHead);
    const issues = Array.isArray(sentry.issues) ? sentry.issues : [];
    if (!sentry.configured) {
      const skipped = document.createElement("p");
      skipped.textContent = "Sentry was not checked.";
      body.appendChild(skipped);
    } else if (!issues.length) {
      const none = document.createElement("p");
      none.textContent = "No Sentry issues in this class window.";
      body.appendChild(none);
    } else {
      issues.forEach(function (issue) {
        const line = document.createElement("p");
        line.textContent =
          String(issue.project || "") +
          " " +
          String(issue.id || "") +
          " " +
          String(issue.title || "") +
          " · count " +
          String(issue.count || 0) +
          " · " +
          String(issue.first_seen || "") +
          " – " +
          String(issue.last_seen || "");
        body.appendChild(line);
      });
    }
    if (typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
  }

  /**
   * Fetch one report and show it.
   * @param {string} url
   * @returns {void}
   */
  function openReport(url) {
    fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (res) {
        return res.ok ? res.json() : null;
      })
      .then(function (body) {
        if (body && body.report) paintReport(body.report);
      })
      .catch(function () {});
  }

  /**
   * List past reports on Attendance and open the one End Class just made.
   * @returns {void}
   */
  function bindReports() {
    const host = document.getElementById("live-fix-history");
    const list = document.getElementById("live-fix-history-list");
    const close = document.getElementById("live-fix-report-close");
    const dialog = document.getElementById("live-fix-report");
    const cid = classId();
    if (!(host instanceof HTMLElement) || !(list instanceof HTMLElement) || !cid) return;
    close?.addEventListener("click", function () {
      if (dialog instanceof HTMLDialogElement) dialog.close();
    });
    fetch("/api/staff/class/" + cid + "/live-fix-reports", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (res) {
        return res.ok ? res.json() : { reports: [] };
      })
      .then(function (body) {
        const reports = Array.isArray(body.reports) ? body.reports : [];
        if (!reports.length) return;
        host.hidden = false;
        reports.forEach(function (row) {
          const item = document.createElement("li");
          const button = document.createElement("button");
          button.type = "button";
          const when = String(row.ended_at || "Ended class");
          button.textContent =
            when +
            " · " +
            String(row.flag_count || 0) +
            " notes · " +
            String(row.sentry_count || 0) +
            " Sentry";
          button.addEventListener("click", function () {
            openReport(
              "/api/staff/class/" + cid + "/live-fix-reports/" + String(row.session_id)
            );
          });
          item.appendChild(button);
          list.appendChild(item);
        });
      })
      .catch(function () {});
    const params = new URLSearchParams(window.location.search);
    const opened = params.get("fix_report");
    if (opened) openReport("/api/staff/class/" + cid + "/live-fix-reports/" + opened);
  }

  document.addEventListener("click", rememberAction, true);
  bindTeacher();
  bindStudent();
  bindReports();
})();
