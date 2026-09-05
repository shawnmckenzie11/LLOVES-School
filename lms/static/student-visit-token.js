/**
 * Per-tab student visit token: URL ?v=, DOM, then sessionStorage.
 *
 * Multiple student tabs in one browser share a Flask session cookie; the opaque
 * visit token scopes API calls and tab-close leave to one attendee row.
 */
(function () {
  var STORAGE_KEY = "lloves-student-v";

  /**
   * Persist a token for this tab's sessionStorage bucket.
   * @param {string} token
   */
  function persist(token) {
    if (!token) return;
    try {
      sessionStorage.setItem(STORAGE_KEY, token);
    } catch (_err) {
      /* private mode / quota */
    }
  }

  /**
   * Read ?v= from the current location.
   * @returns {string}
   */
  function fromUrl() {
    try {
      return new URLSearchParams(window.location.search).get("v") || "";
    } catch (_err) {
      return "";
    }
  }

  /**
   * Read token from body dataset or mood form hidden input.
   * @returns {string}
   */
  function fromDom() {
    var body = document.body;
    if (body && body.dataset && body.dataset.visitToken) {
      return body.dataset.visitToken;
    }
    var input = document.querySelector('input[name="visit_token"]');
    return input && input.value ? input.value : "";
  }

  /**
   * Resolve the visit token for this tab (URL wins, then DOM, then storage).
   * @returns {string}
   */
  window.getStudentVisitToken = function () {
    var urlToken = fromUrl();
    if (urlToken) {
      persist(urlToken);
      return urlToken;
    }
    var domToken = fromDom();
    if (domToken) {
      persist(domToken);
      return domToken;
    }
    try {
      return sessionStorage.getItem(STORAGE_KEY) || "";
    } catch (_err2) {
      return "";
    }
  };

  /**
   * Merge visit-token header into a fetch init object.
   * @param {RequestInit} [init]
   * @returns {RequestInit}
   */
  window.studentVisitFetchInit = function (init) {
    init = init || {};
    var headers = new Headers(init.headers || {});
    var token = window.getStudentVisitToken();
    if (token) {
      headers.set("X-Student-Visit-Token", token);
    }
    return Object.assign({}, init, { headers: headers });
  };
})();
