/**
 * Student live-session presence: heartbeat while the tab is open.
 *
 * Runs only on ``/student/home``. Tab close/refresh no longer leaves — a
 * ~10s heartbeat (plus staff overlay sweep) sets ``left_at`` after missed
 * beats. Explicit Leave still posts ``/api/student/leave``.
 */
(function () {
  if (!document.body || !document.body.classList.contains("student-home")) {
    return;
  }

  var HEARTBEAT_MS = 10000;

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
   * POST a presence heartbeat for this tab's attendee.
   */
  function sendHeartbeat() {
    var url = "/api/student/heartbeat";
    var token = visitToken();
    var body = JSON.stringify({ visit_token: token });
    try {
      if (navigator.sendBeacon && token && document.visibilityState === "hidden") {
        navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
        return;
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

  sendHeartbeat();
  window.setInterval(sendHeartbeat, HEARTBEAT_MS);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") {
      sendHeartbeat();
    }
  });
  window.addEventListener("pageshow", sendHeartbeat);
})();
