/**
 * Attendance & Participation tabs switch in the page.
 *
 * The address bar keeps ``?tab=ap&view=`` so a refresh opens the same
 * tab. A normal click does not reload. Roster & PINs markup is fetched
 * the first time that tab opens, from the staff route.
 */
(function () {
  const root = document.getElementById("ap-root");
  if (!root) return;

  const VIEWS = ["overview", "past", "attendance", "participation", "feedback", "roster", "engagement"];

  /**
   * View token from a tab link or the current query.
   * @param {string} raw
   * @returns {string}
   */
  function viewName(raw) {
    const token = String(raw || "").trim().toLowerCase();
    return VIEWS.indexOf(token) >= 0 ? token : "overview";
  }

  /**
   * Show one panel and mark its tab. Does not navigate.
   * @param {string} view
   * @returns {void}
   */
  function show(view) {
    const name = viewName(view);
    root.dataset.apView = name;
    document.querySelectorAll("#ap-root [data-ap-panel]").forEach((panel) => {
      const on = panel.getAttribute("data-ap-panel") === name;
      panel.hidden = !on;
    });
    document.querySelectorAll("#ap-root [data-ap-tab]").forEach((tab) => {
      const on = tab.getAttribute("data-ap-tab") === name;
      tab.classList.toggle("on", on);
      if (on) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    });
    if (name === "roster") {
      void loadRoster();
    }
  }

  /**
   * Write the view into the query string without a reload.
   * @param {string} view
   * @returns {void}
   */
  function remember(view) {
    const url = new URL(window.location.href);
    url.searchParams.set("tab", "ap");
    url.searchParams.set("view", viewName(view));
    window.history.pushState({ apView: viewName(view) }, "", url);
  }

  /**
   * Fetch Roster & PINs the first time that tab is shown.
   * @returns {Promise<void>}
   */
  async function loadRoster() {
    const panel = document.getElementById("ap-panel-roster");
    if (!(panel instanceof HTMLElement) || panel.dataset.loaded === "1" || panel.dataset.loading === "1") {
      return;
    }
    const classId = String(root.dataset.classId || "");
    if (!classId) return;
    panel.dataset.loading = "1";
    try {
      const response = await fetch(`/api/staff/class/${classId}/roster-pins`, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "text/html" },
      });
      if (!response.ok) {
        panel.dataset.loading = "";
        return;
      }
      panel.innerHTML = await response.text();
      panel.dataset.loaded = "1";
      panel.dataset.loading = "";
      if (!document.querySelector("script[data-roster-pins]")) {
        const script = document.createElement("script");
        script.type = "module";
        script.src = "/static/staff_roster.js";
        script.dataset.rosterPins = "1";
        document.body.appendChild(script);
      }
    } catch (_err) {
      panel.dataset.loading = "";
    }
  }

  document.querySelectorAll("#ap-root [data-ap-tab]").forEach((tab) => {
    tab.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
        return;
      }
      event.preventDefault();
      const view = viewName(tab.getAttribute("data-ap-tab"));
      show(view);
      remember(view);
    });
  });

  window.addEventListener("popstate", () => {
    const view = new URL(window.location.href).searchParams.get("view");
    show(viewName(view));
  });

  const initial = new URL(window.location.href).searchParams.get("view") || root.dataset.apView;
  show(viewName(initial));
})();
