/**
 * Browser Sentry for every staff page and the student portal.
 *
 * MCK-183: staff pages carry ``lloves-sentry-user`` (the teacher's user id,
 * never a name or email) and ``lloves-sentry-tags`` (teacher_id,
 * teacher_kind, portal, class_id, course_code, tool). Student pages carry
 * tags only. Breadcrumbs and events are scrubbed with the same rules as
 * ``lms/sentry_wire.py``: emails, PINs, join and catch-up codes, and
 * student names (labelled or not, including ``tap by <name>`` and
 * ``code <token>``). Console lines drop, click/input crumbs keep the
 * element tag and class only, and URLs lose their query. A scrub error
 * drops the event. Session Replay stays off.
 *
 * Failed live fetches (HTTP 500 and other non-shed 5xx) and exceptions a
 * poll loop would otherwise swallow (``window.llovesSentryReport``) report.
 * ``window.llovesSentryStaffReconnect`` sends one teacher-strip episode.
 * Its payload is only cause, duration_ms, timestamp, and class_id. The
 * allowlisted cause pattern is ``LLOVES_STAFF_RECONNECT_CAUSE_SOURCE``,
 * set by ``staff_reconnect_log.js``. The poll filter does not drop that
 * tagged event. Scrubbing still runs.
 *
 * The page renders ``lloves-sentry-*`` meta tags from ``SENTRY_DSN_LIVE``.
 * No DSN is stored in this file. When the meta tag is absent the script
 * returns and the shells behave as they do today.
 *
 * Busy / 503 / reconnect / heartbeat failures stay on the soft strip in
 * ``staff_ap.js`` and ``student-portal.js``. This file drops that same
 * class of event if one escapes as an unhandled rejection. A script
 * exception still reports when the message mentions 500, unavailable, or
 * busy. The vendored browser SDK (11.1.0) has no ``enableLogs``, so
 * WARNING and above are sent from the server.
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
  var STAFF_RECONNECT_TAG = "lloves.staff_reconnect";
  var STAFF_RECONNECT_KEYS = ["cause", "duration_ms", "timestamp", "class_id"];
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
   * True for a programming error (paint, undefined, and so on).
   * A TypeError whose text is only "Failed to fetch" is a network failure.
   * @param {object} event
   * @returns {boolean}
   */
  function isProgrammingFault(event) {
    var values = event && event.exception && event.exception.values;
    if (!Array.isArray(values) || !values.length) return false;
    var network = /failed to fetch|networkerror|network request failed|load failed|aborterror|the operation was aborted|the user aborted|request aborted|^HTTP\s+\d{3}\b/i;
    return values.every(function (value) {
      var type = String((value && value.type) || "");
      var text = String((value && value.value) || "");
      if (!type || network.test(text)) return false;
      return /Error$/.test(type);
    });
  }

  /**
   * True when a soft poll/heartbeat failure should not become a fatal event.
   * A script exception still reports when its text mentions 500, unavailable,
   * or busy. An older poll breadcrumb does not hide that exception.
   * @param {object} event
   * @returns {boolean}
   */
  function shouldDropLivePollEvent(event) {
    if (!event || !SOFT_POLL_TEXT.test(eventText(event))) return false;
    if (isProgrammingFault(event)) return false;
    var requestUrl = event.request && event.request.url ? String(event.request.url) : "";
    var newest = newestFetchUrl(event);
    if (newest) return isLivePollUrl(newest);
    if (requestUrl && isLivePollUrl(requestUrl)) return true;
    return false;
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
      // Every attribute: http.request.header.referer is an array holding the
      // previous page URL with ?v= (MCK-183 #249 gate). Any value with a
      // "?" loses its query; a visit-token header attribute goes entirely.
      Object.keys(bag).forEach(function (key) {
        if (/visit-token/i.test(key)) {
          delete bag[key];
          return;
        }
        bag[key] = stripQueryValue(bag[key]);
      });
    });
    return span;
  }

  /**
   * Strip queries from a span attribute value: a string, an array of
   * strings, or a streamed ``{ value, type }`` attribute.
   * @param {unknown} value
   * @returns {unknown}
   */
  function stripQueryValue(value) {
    if (typeof value === "string") return value.indexOf("?") >= 0 || value.indexOf("#") >= 0 ? stripQuery(value) : value;
    if (Array.isArray(value)) return value.map(stripQueryValue);
    if (value && typeof value === "object" && "value" in value) {
      value.value = stripQueryValue(value.value);
    }
    return value;
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
    text = cut >= 0 ? text.slice(0, cut) : text;
    // Path tokens (student seat links, invite links) never leave the browser.
    return text
      .replace(/\/student\/s\/[^/?#]+/g, "/student/s/[token]")
      .replace(/\/invite\/[^/?#]+/g, "/invite/[token]");
  }

  /**
   * MCK-183 (#269 gate): stack frames carry the script URL, which on student
   * pages can be ``...?v=<visit token>``. Cut every frame's filename and
   * abs_path (exceptions and threads) down to the bare path.
   * @param {any} holder an object with ``stacktrace.frames``
   */
  function scrubFrames(holder) {
    var frames = holder && holder.stacktrace && holder.stacktrace.frames;
    if (!Array.isArray(frames)) return;
    for (var i = 0; i < frames.length; i++) {
      var frame = frames[i];
      if (!frame) continue;
      if (frame.filename) frame.filename = stripQuery(frame.filename);
      if (frame.abs_path) frame.abs_path = stripQuery(frame.abs_path);
      if (frame.module && /[?#]/.test(String(frame.module))) frame.module = stripQuery(frame.module);
      // Locals can hold a PIN under any name. The server drops frame vars too.
      if (frame.vars) delete frame.vars;
    }
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
  // Keep these lists aligned with ``_DROPPED_KEYS`` / ``_NOT_A_BY_NAME``
  // in sentry_wire.py. ``course_code`` is not a dropped key.
  var DROPPED_KEYS = {
    email: 1, confirm_email: 1, login_hint: 1,
    pin: 1, course_pin: 1, student_pin: 1, catchup_pin: 1, catch_up_pin: 1,
    default_pin: 1, pin_hash: 1,
    join_code: 1, session_code: 1, portal_join_code: 1, catchup_code: 1,
    catch_up_code: 1, live_access_code: 1, access_code: 1,
    name: 1, student_name: 1, first_name: 1, last_name: 1, display_name: 1,
    full_name: 1, preferred_name: 1, roster_name: 1, given_name: 1,
    family_name: 1, codename: 1, last_display: 1, username: 1,
  };
  var NOT_A_STUDENT_NAME = {
    portal: 1, state: 1, home: 1, join: 1, code: 1, pin: 1, live: 1,
    class: 1, session: 1, error: 1, failed: 1, page: 1, view: 1, tab: 1,
    board: 1, phone: 1, card: 1,
  };
  var NOT_A_BY_NAME = {
    error: 1, warning: 1, failed: 1, false: 1, true: 1, none: 1, null: 1,
    graph: 1, publish: 1, roster: 1, student: 1, portal: 1, class: 1,
    dashboard: 1, attendance: 1, state: 1, home: 1, join: 1, code: 1,
    pin: 1, live: 1, session: 1, page: 1, view: 1, tab: 1, board: 1,
    phone: 1, card: 1, monday: 1, tuesday: 1, wednesday: 1, thursday: 1,
    friday: 1, saturday: 1, sunday: 1, january: 1, february: 1, march: 1,
    april: 1, may: 1, june: 1, july: 1, august: 1, september: 1, october: 1,
    november: 1, december: 1, staff: 1, teacher: 1, admin: 1, user: 1,
    system: 1, server: 1, module: 1, lesson: 1, question: 1, answer: 1,
    team: 1, group: 1, item: 1, step: 1, today: 1, tomorrow: 1,
    yesterday: 1, someone: 1, anyone: 1,
  };
  var HEADER_ALLOW = {
    accept: 1, "accept-language": 1, "content-length": 1, "content-type": 1,
    host: 1, referer: 1, "user-agent": 1,
  };
  // All-letter tokens of join-code length that are ordinary log words.
  // Keep in sync with ``_ORDINARY_CODE_WORDS`` in sentry_wire.py.
  var ORDINARY_CODE_WORDS = {
    lookup: 1, failed: 1, failure: 1, edited: 1, opened: 1, saved: 1,
    publish: 1, import: 1, added: 1, request: 1, response: 1, status: 1,
    disk: 1, full: 1, locked: 1, database: 1, joined: 1, handler: 1,
    typed: 1, ping: 1, keep: 1, post: 1, get: 1, put: 1, head: 1, patch: 1,
    http: 1, https: 1, json: 1, html: 1, info: 1, debug: 1, warn: 1,
    trace: 1, span: 1, event: 1, from: 1, with: 1, that: 1, this: 1,
    when: 1, have: 1, been: 1, were: 1, your: 1, into: 1, over: 1, after: 1,
    before: 1, about: 1, there: 1, where: 1, which: 1, other: 1, only: 1,
    also: 1, just: 1, like: 1, some: 1, each: 1, both: 1, most: 1, many: 1,
    more: 1, back: 1, down: 1, here: 1, once: 1, year: 1, date: 1, time: 1,
    open: 1, close: 1, start: 1, stop: 1, save: 1, send: 1, load: 1, read: 1,
    file: 1, path: 1, host: 1, port: 1, type: 1, data: 1, body: 1, text: 1,
    note: 1, test: 1, drop: 1, pass: 1, skip: 1, done: 1, left: 1, next: 1,
    main: 1, base: 1, size: 1, line: 1, take: 1, quit: 1, bank: 1, true: 1,
    false: 1, none: 1, null: 1, error: 1, warning: 1, critical: 1, fatal: 1,
    course: 1, select: 1, from: 1, where: 1, insert: 1, update: 1, delete: 1,
    values: 1, table: 1, index: 1, limit: 1, order: 1, inner: 1, outer: 1,
    count: 1, union: 1, having: 1, distinct: 1, exists: 1, unique: 1,
    check: 1, column: 1, schema: 1, using: 1, case: 1, then: 1, else: 1,
    desc: 1, offset: 1, fetch: 1, pragma: 1, vacuum: 1, begin: 1, commit: 1,
    alter: 1, create: 1, explain: 1, ignore: 1, abort: 1, rollback: 1,
  };
  Object.keys(NOT_A_BY_NAME).forEach(function (word) { ORDINARY_CODE_WORDS[word] = 1; });

  /**
   * True when a string is shaped like a live join or catch-up code.
   * Lengths are 4, 5, 6, or 8, alphanumeric, with a letter.
   * @param {string} value
   * @returns {boolean}
   */
  function looksLikeJoinCode(value) {
    var text = String(value || "").trim().toUpperCase().replace(/ /g, "");
    if ([4, 5, 6, 8].indexOf(text.length) < 0) return false;
    if (!/^[A-Z0-9]+$/.test(text) || /^[0-9]+$/.test(text)) return false;
    return /[A-Z]/.test(text);
  }

  /**
   * True for a ministry course code such as MCF3M.
   * @param {string} value
   * @returns {boolean}
   */
  function isOntarioCourseCode(value) {
    return /^[A-Za-z]{3}[1-4][A-Za-z]$/.test(String(value || "").trim());
  }

  /**
   * True when a free-text token is a join or catch-up code with no cue word.
   * A letter plus a digit, length 4, 5, 6, or 8. Course codes stay. An
   * all-letter token stays unless it is all capitals and not a log word.
   * @param {string} value
   * @returns {boolean}
   */
  function shouldRedactCodeToken(value) {
    var text = String(value || "").trim();
    if (!looksLikeJoinCode(text)) return false;
    if (isOntarioCourseCode(text)) return false;
    if (/\d/.test(text)) return true;
    return (
      /^[A-Za-z]+$/.test(text) &&
      text === text.toUpperCase() &&
      !ORDINARY_CODE_WORDS[text.toLowerCase()]
    );
  }

  function isUnlabelledPin(value) {
    var text = String(value || "").trim();
    if (!/^\d{4}$/.test(text)) return false;
    var number = Number(text);
    return !(number >= 1900 && number <= 2099);
  }

  /**
   * True when this field must not be sent.
   * @param {string} key
   * @param {any} child
   * @returns {boolean}
   */
  function dropSecretField(key, child) {
    var lowered = String(key).toLowerCase();
    if (DROPPED_KEYS[lowered] || /_pin$/.test(lowered)) return true;
    if (lowered === "code" && typeof child === "string" && looksLikeJoinCode(child)) return true;
    return false;
  }

  /**
   * Cut the query from every URL or path inside free text, then redact
   * emails, PINs, join and catch-up codes, and student names.
   * Mirrors ``_scrub_text`` in sentry_wire.py.
   * @param {any} text
   * @returns {any}
   */
  function scrubText(text) {
    if (typeof text !== "string") return text;
    if (/[?#]/.test(text)) {
      text = text.replace(/((?:https?:\/\/|\/)[^\s?#"'()<>]*)[?#][^\s"'()<>]*/g, function (_m, path) {
        return stripQuery(path);
      });
    }
    text = maskPathTokens(text);
    text = text.replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, "[redacted]");
    text = text.replace(/\b(pin)\b(\s*[:=]?\s*)\d{4}\b/gi, function (_all, label, sep) {
      return label + sep + "[redacted]";
    });
    text = text.replace(
      /\b((?:join|catch-?up|session|portal|access)\s+code|session_code|portal_join_code|join_code|live_access_code|catch-?up_code)\b(\s*[:=]?\s*)[A-Za-z0-9]{4,8}\b/gi,
      function (_all, label, sep) {
        return label + sep + "[redacted]";
      }
    );
    text = text.replace(
      /\b(student(?:'s)?\s+name|first_name|last_name|full_name|display_name|codename|roster_name|name)\b(\s*[:=]\s*)([^\s,;"'&=]{1,80})/gi,
      function (_all, label, sep) {
        return label + sep + "[redacted]";
      }
    );
    text = text.replace(/\b(Student|student|Roster|roster)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b/g, function (all, label, name) {
      if (NOT_A_STUDENT_NAME[String(name).toLowerCase()]) return all;
      return label + " [redacted]";
    });
    text = text.replace(/\b[Bb]y\s+([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\b/g, function (all, name) {
      var words = String(name).split(/\s+/);
      var safe = words.length && words.every(function (word) {
        return NOT_A_BY_NAME[word.toLowerCase()];
      });
      return safe ? all : "by [redacted]";
    });
    text = text.replace(/\b(code)(\s*[:=]?\s*)([A-Za-z0-9]{4,8})\b/gi, function (all, label, sep, token, offset, whole) {
      if (!looksLikeJoinCode(token)) return all;
      var prefix = String(whole).slice(0, offset);
      if (/course[_ \t]+$/i.test(prefix)) return all;
      return label + sep + "[redacted]";
    });
    text = text.replace(/\b(?:[A-Za-z0-9]{8}|[A-Za-z0-9]{6}|[A-Za-z0-9]{5}|[A-Za-z0-9]{4})\b/g, function (token) {
      return shouldRedactCodeToken(token) ? "[redacted]" : token;
    });
    text = text.replace(/\b([A-Z][a-z]{2,})\s+([A-Z][a-z]{2,})\b/g, function (all, first, last) {
      if (NOT_A_BY_NAME[first.toLowerCase()] || NOT_A_BY_NAME[last.toLowerCase()]) return all;
      return "[redacted]";
    });
    text = text.replace(/(?<!\d)(\d{4})(?!\d)/g, function (digits) {
      return isUnlabelledPin(digits) ? "[redacted]" : digits;
    });
    return text;
  }

  /**
   * Drop secret keys and redact secret text. A repeated object is a cycle:
   * throw so the caller drops the event instead of sending it.
   * @param {any} value
   * @param {WeakSet<object>} seen
   * @returns {any}
   */
  function scrubNode(value, seen) {
    if (typeof value === "string") return scrubText(value);
    if (!value || typeof value !== "object") return value;
    if (seen.has(value)) throw new Error("cyclic sentry payload");
    seen.add(value);
    if (Array.isArray(value)) {
      for (var i = 0; i < value.length; i++) value[i] = scrubNode(value[i], seen);
      return value;
    }
    Object.keys(value).forEach(function (key) {
      if (dropSecretField(key, value[key])) {
        delete value[key];
        return;
      }
      value[key] = scrubNode(value[key], seen);
    });
    return value;
  }

  /**
   * Allowlisted request headers only, with any URL query cut.
   * @param {object} request
   */
  function scrubRequest(request) {
    if (!request || typeof request !== "object") return;
    delete request.data;
    delete request.cookies;
    delete request.query_string;
    delete request.env;
    if (typeof request.url === "string") request.url = stripQuery(request.url);
    var headers = request.headers;
    if (!headers || typeof headers !== "object") return;
    Object.keys(headers).forEach(function (name) {
      if (!HEADER_ALLOW[String(name).toLowerCase()]) {
        delete headers[name];
        return;
      }
      headers[name] = typeof headers[name] === "string" ? stripQuery(headers[name]) : "";
    });
  }

  /**
   * MCK-183 Ops LOW-1: mask seat and invite paths anywhere in free text,
   * with or without a query after them.
   * @param {any} text
   * @returns {any}
   */
  function maskPathTokens(text) {
    if (typeof text !== "string") return text;
    if (text.indexOf("/invite/") < 0 && text.indexOf("/student/s/") < 0) return text;
    return text
      .replace(/\/invite\/(?!\[token\])[^\s/?#"'()<>\[\],;]+/g, "/invite/[token]")
      .replace(/\/student\/s\/(?!\[token\])[^\s/?#"'()<>\[\],;]+/g, "/student/s/[token]");
  }

  /**
   * Scrub one breadcrumb in place. Console lines drop. A scrub error throws
   * so beforeBreadcrumb can drop the crumb.
   * @param {object} crumb
   * @returns {object|null}
   */
  function scrubBreadcrumb(crumb) {
    if (!crumb || typeof crumb !== "object") return crumb;
    scrubNode(crumb, new WeakSet());
    var category = String(crumb.category || "");
    if (category === "console") return null;
    if (category.indexOf("ui.") === 0 && crumb.message) {
      crumb.message = String(crumb.message).replace(/\[[^\]]*\]/g, "");
      crumb.message = scrubText(crumb.message);
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
   * Scrub an event in place: secrets, query strings, cookies, headers, and
   * any user field other than the teacher id. Throws on a scrub failure so
   * beforeSend drops the event.
   * @param {object} event
   * @returns {object}
   */
  function scrubEvent(event) {
    if (!event || typeof event !== "object") return event;
    scrubNode(event, new WeakSet());
    if (event.request) scrubRequest(event.request);
    var groups = [event.exception, event.threads];
    for (var g = 0; g < groups.length; g++) {
      var values = groups[g] && groups[g].values;
      if (Array.isArray(values)) values.forEach(scrubFrames);
    }
    if (typeof event.transaction === "string") {
      event.transaction = maskPathTokens(stripQuery(event.transaction));
    }
    if (event.stacktrace) scrubFrames(event);
    if (event.user) {
      event.user = event.user.id ? { id: String(event.user.id) } : undefined;
      if (!event.user) delete event.user;
    }
    var crumbs = event.breadcrumbs;
    if (Array.isArray(crumbs)) {
      event.breadcrumbs = crumbs.map(scrubBreadcrumb).filter(Boolean);
    } else if (crumbs && Array.isArray(crumbs.values)) {
      crumbs.values = crumbs.values.map(scrubBreadcrumb).filter(Boolean);
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

  /**
   * True for a teacher yellow-strip episode this file sends on purpose.
   * @param {object} event
   * @returns {boolean}
   */
  function isStaffReconnectEvent(event) {
    return Boolean(event && event.tags && event.tags[STAFF_RECONNECT_TAG]);
  }

  /**
   * The cause allowlist lives in staff_reconnect_log.js. This sender
   * refuses a cause until that module has published the pattern.
   * @param {unknown} cause
   * @returns {boolean}
   */
  function staffReconnectCauseAllowed(cause) {
    var source = globalThis.LLOVES_STAFF_RECONNECT_CAUSE_SOURCE;
    if (typeof source !== "string" || !source) return false;
    return new RegExp(source).test(String(cause || ""));
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
        if (
          !isOwnFetchFailure(event) &&
          !isStaffReconnectEvent(event) &&
          shouldDropLivePollEvent(event)
        ) {
          return null;
        }
        return scrubEvent(event);
      } catch (_err) {
        // Fail closed. The event can hold a name, a PIN, or a join code.
        return null;
      }
    },
    // Streamed tracing (SDK default) has no transaction hook: this is the scrub.
    beforeSendSpan: function (span) {
      try {
        if (shouldDropLivePollSpan(span)) return null;
        return stripSpanQueries(span);
      } catch (_err) {
        return null;
      }
    },
    beforeBreadcrumb: function (crumb) {
      try {
        var kept = filterLivePollBreadcrumb(crumb);
        return kept ? scrubBreadcrumb(kept) : kept;
      } catch (_err) {
        return null;
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

  /**
   * Send one teacher reconnect episode. Only the four allowlisted keys are
   * copied. A free-text cause, a missing key, or a non-numeric field is
   * dropped. Capped so a flickering strip cannot flood the project.
   * @param {object} payload
   */
  var MAX_RECONNECT_REPORTS = 50;
  var reconnectReportsSent = 0;
  window.llovesSentryStaffReconnect = function (payload) {
    try {
      if (!payload || typeof payload !== "object") return;
      if (typeof sentryClient.captureMessage !== "function") return;
      if (reconnectReportsSent >= MAX_RECONNECT_REPORTS) return;
      var extra = {};
      for (var i = 0; i < STAFF_RECONNECT_KEYS.length; i += 1) {
        var key = STAFF_RECONNECT_KEYS[i];
        if (!Object.prototype.hasOwnProperty.call(payload, key)) return;
        extra[key] = payload[key];
      }
      if (!staffReconnectCauseAllowed(extra.cause)) return;
      if (typeof extra.duration_ms !== "number") return;
      if (typeof extra.timestamp !== "number") return;
      if (typeof extra.class_id !== "number") return;
      var tags = {};
      tags[STAFF_RECONNECT_TAG] = "1";
      reconnectReportsSent += 1;
      sentryClient.captureMessage("Teacher live strip", {
        level: "warning",
        tags: tags,
        extra: extra,
        fingerprint: ["lloves-staff-reconnect", String(extra.cause)],
      });
    } catch (_err2) {
      /* never break the page */
    }
  };
})();
