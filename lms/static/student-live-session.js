/**
 * Student live-session disconnect: tab close / unload best-effort leave.
 *
 * Posts to ``/api/student/leave`` so the server can set ``left_at`` and wipe
 * mood/character for this visit. Skips in-app navigations (mood → character
 * → home, form posts) so the join flow is not torn down mid-visit.
 */
(function () {
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
   * Fire a one-shot leave beacon for the bound live session.
   */
  function leaveLiveSession() {
    if (leaving || internalNav) return;
    leaving = true;
    var url = "/api/student/leave";
    try {
      if (
        navigator.sendBeacon &&
        navigator.sendBeacon(url, new Blob([], { type: "application/json" }))
      ) {
        return;
      }
    } catch (_err) {
      /* fall through */
    }
    try {
      fetch(url, {
        method: "POST",
        keepalive: true,
        credentials: "same-origin",
      });
    } catch (_err2) {
      /* ignore */
    }
  }

  window.addEventListener("pagehide", leaveLiveSession);
})();
