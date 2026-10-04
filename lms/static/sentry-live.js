/**
 * Browser Sentry for every staff page and the student portal.
 *
 * MCK-183: staff pages carry ``lloves-sentry-user`` (the teacher's user id,
 * never a name or email) and ``lloves-sentry-tags`` (teacher_id,
 * teacher_kind, portal, class_id, course_code, tool). Student pages carry
 * tags only. Breadcrumbs are scrubbed so roster names on screen never ride
 * along: console lines drop, click/input crumbs keep the element tag and
 * class only, and URLs lose their query. Session Replay stays off.
 *
 * Failed live fetches (HTTP 500 and other non-shed 5xx) and exceptions a
 * poll loop would otherwise swallow (``window.llovesSentryReport``) report.
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
  // Busy / shed / proxy flaps. Those stay on the soft reconnect strip.
  var SOFT_HTTP_STATUS = { 502: true, 503: true, 504: true };
  var FETCH_FAILURE_TAG = "lloves.fetch_failed";
  var reportedFetchFailures = {};

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
  var SPAN_QUERY_KEYS = ["url.query", "http.query", "http.fragment"];
  var SPAN_URL_KEYS = ["url", "http.url", "url.full", "http.target", "server.address.full"];

  /**
   * Remove query strings (the student ``?v=`` visit token) from one span or
   * span-like object in place: name/description and URL attributes.
   * @param {object} span
   * @returns {object}
   */
  function stripSpanQueries(span) {
    if (!span || typeof span !== "object") return span;
    ["description", "name", "op_description"].forEach(function (key) {
      if (typeof span[key] === "string") span[key] = stripQuery(span[key]);
    });
    [span.data, span.attributes].forEach(function (bag) {
      if (!bag || typeof bag !== "object") return;
      SPAN_QUERY_KEYS.forEach(function (key) {
        delete bag[key];
      });
      SPAN_URL_KEYS.forEach(function (key) {
        if (typeof bag[key] === "string") bag[key] = stripQuery(bag[key]);
      });
    });
    return span;
  }

  /**
   * Same scrub for a whole transaction event: name, request URL, the trace
   * context and every child span.
   * @param {object} event
   * @returns {object}
   */
  function scrubTransaction(event) {
    if (!event) return event;
    if (typeof event.transaction === "string") event.transaction = stripQuery(event.transaction);
    if (event.request) {
      if (event.request.url) event.request.url = stripQuery(event.request.url);
      delete event.request.query_string;
      delete event.request.cookies;
      delete event.request.data;
    }
    if (event.contexts && event.contexts.trace) stripSpanQueries(event.contexts.trace);
    (event.spans || []).forEach(stripSpanQueries);
    return event;
  }

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


  /**
   * Parse the JSON tag meta. Bad JSON means no tags.
   * @returns {Object<string, string>}
   */
  function metaTags() {
    var raw = metaContent("lloves-sentry-tags");
    if (!raw) return {};
    try {
      var parsed = JSON.parse(raw);
      var out = {};
      if (!parsed || typeof parsed !== "object") return out;
      Object.keys(parsed).forEach(function (key) {
        var value = parsed[key];
        if (value === null || value === undefined || value === "") return;
        out[key] = String(value);
      });
      return out;
    } catch (_err) {
      return {};
    }
  }

  /**
   * Drop the query string and hash from a URL so no typed text rides along.
   * @param {string} url
   * @returns {string}
   */
  function stripQuery(url) {
    var text = String(url || "");
    var cut = text.search(/[?#]/);
    return cut >= 0 ? text.slice(0, cut) : text;
  }

  /**
   * Path template for grouping: ids and tokens become placeholders.
   * @param {string} url
   * @returns {string}
   */
  function pathTemplate(url) {
    var path = stripQuery(url).replace(/^[a-z]+:\/\/[^/]+/i, "");
    return path
      .split("/")
      .map(function (part) {
        if (/^\d+$/.test(part)) return ":id";
        if (part.length >= 16 && /^[A-Za-z0-9_-]+$/.test(part)) return ":token";
        return part;
      })
      .join("/");
  }

  /**
   * Scrub one breadcrumb in place. Roster names show on screen, so DOM
   * attribute text, console output, and query strings never leave.
   * @param {object} crumb
   * @returns {object|null}
   */
  function scrubBreadcrumb(crumb) {
    if (!crumb) return crumb;
    var category = String(crumb.category || "");
    if (category === "console") return null;
    if (category.indexOf("ui.") === 0 && crumb.message) {
      crumb.message = String(crumb.message).replace(/\[[^\]]*\]/g, "");
    }
    if (category === "navigation" && crumb.data) {
      if (crumb.data.from) crumb.data.from = stripQuery(crumb.data.from);
      if (crumb.data.to) crumb.data.to = stripQuery(crumb.data.to);
    }
    if ((category === "fetch" || category === "xhr") && crumb.data && crumb.data.url) {
      crumb.data.url = stripQuery(crumb.data.url);
    }
    return crumb;
  }

  /**
   * Scrub an event in place: query strings, referer, and any user field
   * other than the teacher id.
   * @param {object} event
   * @returns {object}
   */
  function scrubEvent(event) {
    if (!event) return event;
    if (event.request) {
      if (event.request.url) event.request.url = stripQuery(event.request.url);
      delete event.request.query_string;
      delete event.request.cookies;
      delete event.request.data;
      var headers = event.request.headers;
      if (headers) {
        if (headers.Referer) headers.Referer = stripQuery(headers.Referer);
        if (headers.referer) headers.referer = stripQuery(headers.referer);
      }
    }
    if (event.user) {
      event.user = event.user.id ? { id: String(event.user.id) } : undefined;
      if (!event.user) delete event.user;
    }
    if (Array.isArray(event.breadcrumbs)) {
      event.breadcrumbs = event.breadcrumbs
        .map(function (crumb) {
          return scrubBreadcrumb(crumb);
        })
        .filter(Boolean);
    }
    return event;
  }

  /**
   * True when an HTTP status from a same-site fetch should report.
   * 5xx except the busy / proxy statuses that stay on the reconnect strip.
   * @param {number} status
   * @returns {boolean}
   */
  function isReportableStatus(status) {
    return status >= 500 && status <= 599 && !SOFT_HTTP_STATUS[status];
  }

  /**
   * Same-site URL that belongs to the app (not Google, not the Sentry ingest).
   * @param {string} url
   * @returns {boolean}
   */
  function isAppUrl(url) {
    var text = String(url || "");
    if (/^[a-z]+:\/\//i.test(text)) {
      var origin = (window.location && window.location.origin) || "";
      if (!origin || text.indexOf(origin + "/") !== 0) return false;
      text = text.slice(origin.length);
    }
    return /^\/(?:api|staff|student|auth)\//.test(text);
  }

  /**
   * Report one failed live fetch, at most once per method + path + status
   * per page. Only the method, path template and status are sent.
   * @param {object} client
   * @param {string} method
   * @param {string} url
   * @param {number} status
   */
  function reportFetchFailure(client, method, url, status) {
    var template = pathTemplate(url);
    var key = method + " " + template + " " + status;
    if (reportedFetchFailures[key]) return;
    reportedFetchFailures[key] = true;
    if (!client || typeof client.captureMessage !== "function") return;
    var context = {
      level: "error",
      tags: { "http.status_code": String(status), "http.path": template },
      fingerprint: ["lloves-fetch", method, template, String(status)],
    };
    context.tags[FETCH_FAILURE_TAG] = "1";
    client.captureMessage("Live fetch failed: " + key, context);
  }

  /**
   * True for a refused student write (rank turn, vote, answer, ...): a 4xx
   * on a non-GET ``/api/student/`` request. Board ops and presence are normal.
   * @param {string} method
   * @param {string} url
   * @param {number} status
   * @returns {boolean}
   */
  function isStudentStepRejection(method, url, status) {
    if (method === "GET" || status < 400 || status > 499) return false;
    if (!isAppUrl(url)) return false;
    var path = stripQuery(url).replace(/^[a-z]+:\/\/[^/]+/i, "");
    if (path.indexOf("/api/student/") !== 0) return false;
    return !/\/(?:ops|canvas-presence)\/?$/.test(path);
  }

  /**
   * A warning breadcrumb so the next error on this phone shows the refused
   * step. Breadcrumbs ride along with errors only, so this costs nothing
   * on its own; the server writes the matching Logs line.
   * @param {object} client
   * @param {string} method
   * @param {string} url
   * @param {number} status
   */
  function noteStudentStepRejection(client, method, url, status) {
    if (!client || typeof client.addBreadcrumb !== "function") return;
    client.addBreadcrumb({
      category: "student.step",
      level: "warning",
      message: "Step refused: " + method + " " + pathTemplate(url) + " " + status,
      data: { method: method, path: pathTemplate(url), status_code: status },
    });
  }

  /**
   * Wrap ``window.fetch`` so app 5xx responses report. The response is
   * returned untouched; the page's own error handling still runs.
   * @param {object} client
   */
  function installFetchFailureReporting(client) {
    if (typeof window.fetch !== "function" || window.fetch.__llovesWrapped) return;
    var original = window.fetch;
    var wrapped = function (input, init) {
      var url = typeof input === "string" ? input : (input && input.url) || "";
      var method = String(
        (init && init.method) || (input && typeof input === "object" && input.method) || "GET"
      ).toUpperCase();
      return original.apply(this, arguments).then(function (response) {
        try {
          if (response && isReportableStatus(Number(response.status)) && isAppUrl(url)) {
            reportFetchFailure(client, method, url, Number(response.status));
          } else if (response && isStudentStepRejection(method, url, Number(response.status))) {
            noteStudentStepRejection(client, method, url, Number(response.status));
          }
        } catch (_err) {
          /* telemetry never changes the response */
        }
        return response;
      });
    };
    wrapped.__llovesWrapped = true;
    window.fetch = wrapped;
  }

  /**
   * True for the fetch-failure events this file creates on purpose.
   * @param {object} event
   * @returns {boolean}
   */
  function isOwnFetchFailure(event) {
    return Boolean(event && event.tags && event.tags[FETCH_FAILURE_TAG]);
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
        if (!isOwnFetchFailure(event) && shouldDropLivePollEvent(event)) return null;
        return scrubEvent(event);
      } catch (_err) {
        return event;
      }
    },
    // Streamed tracing ignores beforeSendTransaction. Drop poll spans here.
    beforeSendSpan: function (span) {
      try {
        if (shouldDropLivePollSpan(span)) return null;
        return stripSpanQueries(span);
      } catch (_err) {
        return span;
      }
    },
    beforeSendTransaction: function (event) {
      try {
        return scrubTransaction(event);
      } catch (_err) {
        return event;
      }
    },
    beforeBreadcrumb: function (crumb) {
      try {
        var kept = filterLivePollBreadcrumb(crumb);
        return kept ? scrubBreadcrumb(kept) : kept;
      } catch (_err) {
        return crumb;
      }
    },
  };
  if (release) options.release = release;
  sentryClient.init(options);

  var userId = metaContent("lloves-sentry-user");
  if (userId && typeof sentryClient.setUser === "function") {
    sentryClient.setUser({ id: userId });
  }
  var tags = metaTags();
  if (Object.keys(tags).length && typeof sentryClient.setTags === "function") {
    sentryClient.setTags(tags);
  }
  installFetchFailureReporting(sentryClient);

  /**
   * Report an exception a page caught on purpose (a poll loop that paints
   * the reconnect strip instead of throwing). Safe to call without Sentry.
   * @param {unknown} err
   * @param {string} where
   */
  var MAX_REPORTS_PER_PAGE = 3;
  var reportsSent = 0;
  window.llovesSentryReport = function (err, where) {
    try {
      if (typeof sentryClient.captureException !== "function") return;
      // A paint bug in a 1 s poll would otherwise send one event per beat.
      if (reportsSent >= MAX_REPORTS_PER_PAGE) return;
      reportsSent += 1;
      sentryClient.captureException(err, { tags: { "lloves.where": String(where || "") } });
    } catch (_err) {
      /* never break the page */
    }
  };
})();
