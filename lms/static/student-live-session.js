/**
 * Student live-session disconnect: tab close / unload best-effort leave.
 *
 * Runs only on ``/student/home`` so mood/join tabs do not disconnect siblings.
 * Sends the per-tab visit token so other student tabs in the same browser stay
 * connected.
 */
(function () {
  if (!document.body || !document.body.classList.contains("student-home")) {
    return;
  }

  var leaving = false;
  var internalNav = false;

  /**
   * Mark the next unload as an in-app student-portal navigation.
   */
  function markInternalNav() {
    internalNav = true;
  }

  document.addEventListener("submit", markInternalNav, true);
  document.addEventListener(
    "click",
    function (event) {
      var target = event.target;
      if (!target || !target.closest) return;
      var link = target.closest("a[href]");
      if (!link) return;
      try {
        var url = new URL(link.href, window.location.href);
        if (url.origin === window.location.origin && url.pathname.indexOf("/student") === 0) {
          markInternalNav();
        }
      } catch (_err) {
        /* ignore bad hrefs */
      }
    },
    true
  );

  /**
   * Resolve visit token via shared helper when loaded after student-visit-token.js.
   * @returns {string}
   */
  function visitToken() {
    if (typeof window.getStudentVisitToken === "function") {
      return window.getStudentVisitToken();
    }
    return "";
  }

  /**
   * Fire a one-shot leave beacon for this tab's attendee only.
   */
  function leaveLiveSession() {
    if (leaving || internalNav) return;
    leaving = true;
    var url = "/api/student/leave";
    var token = visitToken();
    var body = JSON.stringify({ visit_token: token });
    try {
      if (navigator.sendBeacon && token) {
        if (
          navigator.sendBeacon(
            url,
            new Blob([body], { type: "application/json" })
          )
        ) {
          return;
        }
      }
    } catch (_err) {
      /* fall through */
    }
    try {
      var headers = { "Content-Type": "application/json" };
      if (token) {
        headers["X-Student-Visit-Token"] = token;
      }
      fetch(url, {
        method: "POST",
        keepalive: true,
        credentials: "same-origin",
        headers: headers,
        body: body,
      });
    } catch (_err2) {
      /* ignore */
    }
  }

  window.addEventListener("pagehide", leaveLiveSession);
})();
