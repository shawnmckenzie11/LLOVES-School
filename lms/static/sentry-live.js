/**
 * Browser Sentry for Run Live Class and the student portal.
 *
 * The page renders ``lloves-sentry-*`` meta tags from ``SENTRY_DSN_LIVE``.
 * No DSN is stored in this file. When the meta tag is absent the script
 * returns and the shells behave as they do today.
 *
 * Busy / 503 / reconnect / heartbeat failures stay on the soft strip in
 * ``staff_ap.js`` and ``student-portal.js``. This file drops that same
 * class of event if one escapes as an unhandled rejection. A script
 * exception while painting a poll response still reports.
 */
(function () {
  var POLL_URL =
    /\/api\/student\/(?:state|heartbeat)(?:[/?#]|$)|\/api\/live-sessions\/\d+\/state(?:[/?#]|$)|\/health(?:[/?#]|$)/;
  var SOFT_POLL_TEXT =
    /failed to fetch|networkerror|network request failed|load failed|aborterror|the operation was aborted|the user aborted|request aborted|server overloaded|state unavailable|database is locked|service unavailable|\bhttp\s+\d{3}\b|\b503\b|\b502\b|\b504\b|\b500\b|\b408\b|\b429\b|reconnecting|group status did not update|updating group status|\bbusy\b|\boverloaded\b|\bunavailable\b/i;
  var TRACE_SAMPLE_RATE = 0.05;

  /**
   * Read one meta tag's content.
   * @param {string} name
   * @returns {string}
   */
  function metaContent(name) {
    if (!document || typeof document.querySelector !== "function") return "";
    var el = document.querySelector('meta[name="' + name + '"]');
    if (!el) return "";
    var raw = el.content != null ? el.content : el.getAttribute && el.getAttribute("content");
    return String(raw || "").trim();
  }

  /**
   * True when ``url`` is a live-shell poll or the health check.
   * @param {string} url
   * @returns {boolean}
   */
  function isLivePollUrl(url) {
    return POLL_URL.test(String(url || ""));
  }

  /**
   * Join the event message and exception text. Breadcrumbs are excluded.
   * @param {object} event
   * @returns {string}
   */
  function eventText(event) {
    var parts = [];
    if (event && event.message) parts.push(String(event.message));
    var values = event && event.exception && event.exception.values;
    if (Array.isArray(values)) {
      values.forEach(function (value) {
        parts.push(String((value && value.type) || ""));
        parts.push(String((value && value.value) || ""));
      });
    }
    return parts.join(" ");
  }

  /**
   * Fetch and XHR breadcrumb URLs, oldest first.
   * @param {object} event
   * @returns {string[]}
   */
  function breadcrumbUrls(event) {
    var crumbs = (event && event.breadcrumbs) || [];
    var urls = [];
    if (!Array.isArray(crumbs)) return urls;
    crumbs.forEach(function (crumb) {
      if (!crumb) return;
      if (crumb.category !== "fetch" && crumb.category !== "xhr") return;
      var data = crumb.data || {};
      urls.push(String(data.url || data.to || crumb.message || ""));
    });
    return urls;
  }

  /**
   * Newest fetch/XHR URL on the event, or an empty string.
   * @param {object} event
   * @returns {string}
   */
  function newestFetchUrl(event) {
    var crumbs = (event && event.breadcrumbs) || [];
    if (!Array.isArray(crumbs)) return "";
    for (var i = crumbs.length - 1; i >= 0; i -= 1) {
      var crumb = crumbs[i];
      if (!crumb) continue;
      if (crumb.category !== "fetch" && crumb.category !== "xhr") continue;
      var data = crumb.data || {};
      return String(data.url || data.to || crumb.message || "");
    }
    return "";
  }

  /**
   * True when a soft poll/heartbeat failure should not become a fatal event.
   * @param {object} event
   * @returns {boolean}
   */
  function shouldDropLivePollEvent(event) {
    if (!event || !SOFT_POLL_TEXT.test(eventText(event))) return false;
    var requestUrl = event.request && event.request.url ? String(event.request.url) : "";
    var newest = newestFetchUrl(event);
    if (newest) return isLivePollUrl(newest);
    if (requestUrl && isLivePollUrl(requestUrl)) return true;
    return breadcrumbUrls(event).some(isLivePollUrl);
  }

  /**
   * True when a streamed span describes a live poll.
   * Streamed tracing ignores beforeSendTransaction, so poll spans drop here.
   * @param {object} span
   * @returns {boolean}
   */
  function shouldDropLivePollSpan(span) {
    if (!span) return false;
    var data = span.data || {};
    var url = String(
      data.url || data["http.url"] || data["url.full"] || ""
    );
    return (
      isLivePollUrl(String(span.description || "")) ||
      isLivePollUrl(url) ||
      isLivePollUrl(String(span.op || ""))
    );
  }

  /**
   * Drop successful poll breadcrumbs so they do not crowd the trail.
   * Failed polls stay so shouldDropLivePollEvent can see the URL.
   * @param {object} crumb
   * @returns {object|null}
   */
  function filterLivePollBreadcrumb(crumb) {
    if (!crumb) return crumb;
    if (crumb.category !== "fetch" && crumb.category !== "xhr") return crumb;
    var data = crumb.data || {};
    var url = String(data.url || data.to || "");
    if (!isLivePollUrl(url)) return crumb;
    var status = Number(data.status_code);
    if (!status || status >= 400) return crumb;
    return null;
  }

  /**
   * Append poll-aware browser tracing without removing the default integrations.
   * @param {Array<object>} defaults
   * @returns {Array<object>}
   */
  function withPollAwareTracing(defaults) {
    var client = window.Sentry;
    if (!client || typeof client.browserTracingIntegration !== "function") {
      return defaults || [];
    }
    var tracing = client.browserTracingIntegration({
      shouldCreateSpanForRequest: function (url) {
        return !isLivePollUrl(url);
      },
    });
    var next = [];
    var seen = false;
    (defaults || []).forEach(function (item) {
      if (item && item.name === "BrowserTracing") {
        if (!seen) next.push(tracing);
        seen = true;
        return;
      }
      next.push(item);
    });
    if (!seen) next.push(tracing);
    return next;
  }

  var sentryClient = window.Sentry;
  if ((!sentryClient || typeof sentryClient.init !== "function") && typeof Sentry !== "undefined") {
    sentryClient = Sentry;
    window.Sentry = sentryClient;
  }
  if (!sentryClient || typeof sentryClient.init !== "function") return;

  var dsn = metaContent("lloves-sentry-dsn");
  if (!dsn) return;

  var environment = metaContent("lloves-sentry-environment") || "development";
  var release = metaContent("lloves-sentry-release");
  var options = {
    dsn: dsn,
    environment: environment,
    sendDefaultPii: false,
    tracesSampleRate: TRACE_SAMPLE_RATE,
    tracePropagationTargets: [
      /^\/(?!api\/student\/(?:state|heartbeat)(?:[/?#]|$)|api\/live-sessions\/\d+\/state(?:[/?#]|$)|health(?:[/?#]|$))/,
    ],
    integrations: withPollAwareTracing,
    beforeSend: function (event) {
      try {
        if (shouldDropLivePollEvent(event)) return null;
      } catch (_err) {
        return event;
      }
      return event;
    },
    // Streamed tracing ignores beforeSendTransaction. Drop poll spans here.
    beforeSendSpan: function (span) {
      try {
        if (shouldDropLivePollSpan(span)) return null;
      } catch (_err) {
        return span;
      }
      return span;
    },
    beforeBreadcrumb: function (crumb) {
      try {
        return filterLivePollBreadcrumb(crumb);
      } catch (_err) {
        return crumb;
      }
    },
  };
  if (release) options.release = release;
  sentryClient.init(options);
})();
