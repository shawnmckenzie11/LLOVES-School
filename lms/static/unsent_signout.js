/**
 * MCK-42: after a student sign-out the server redirects here with
 * ``?signed_out=1``. Clear this device's unsent-answer outbox so the next
 * student cannot read the previous one's answers. Classic script, no deps.
 */
(function () {
  var KEY = "lloves-unsent-answers";
  try {
    var params = new URLSearchParams(window.location.search);
    if (params.get("signed_out") !== "1") return;
    try {
      window.localStorage.removeItem(KEY);
    } catch (_err) {
      /* storage blocked: nothing persisted */
    }
    params.delete("signed_out");
    var qs = params.toString();
    var url = window.location.pathname + (qs ? "?" + qs : "") + window.location.hash;
    if (window.history && window.history.replaceState) window.history.replaceState(null, "", url);
  } catch (_err) {
    /* old browser: leave the page as is */
  }
})();
