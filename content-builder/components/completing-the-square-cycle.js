/**
 * Formative cycle for M4-L2 completing-the-square tasks.
 * Wrapper is independent of JSXGraph; CompletingTheSquareChecker supplies math.
 */
(function (global) {
  "use strict";

  var PASS = {
    "expand-cts-revisit": "all-match",
    "diagram-ex960": "all-match",
    "algebraic-ex960": "all-match",
    "algebraic-try9119": "all-match",
    "factor-a-ex959": "all-match",
    "rational-half": "all-match",
    "verify-try9118": "all-match",
    "sketch-try9120": "reading-match",
    "strategy-cts-vs-factor": "choices-match",
    "fresh-hook-cts": "reading-match",
  };

  var MESSAGE = {
    "expand-cts-revisit": {
      "all-match": "msg-revisit-match",
      "standard-or-recovered-off": "msg-revisit-off",
    },
    "diagram-ex960": {
      "all-match": "msg-diagram-match",
      "side-off": "msg-diagram-side-off",
      "leftover-off": "msg-diagram-leftover-off",
      "other-mismatch": "msg-diagram-other",
    },
    "algebraic-ex960": {
      "all-match": "msg-alg960-match",
      "wrong-h-sign": "msg-alg960-h-sign",
      "correct-h-wrong-k": "msg-alg960-h-ok-k-off",
      "wrong-h-sign-or-half": "msg-alg960-h-off",
      "other-mismatch": "msg-alg960-other",
    },
    "algebraic-try9119": {
      "all-match": "msg-try9119-match",
      "copied-960-target": "msg-try9119-copied-960",
      "wrong-h-positive": "msg-try9119-h-positive",
      "correct-h-wrong-k": "msg-try9119-h-ok-k-off",
      "wrong-h-sign-or-half": "msg-try9119-h-off",
      "other-mismatch": "msg-try9119-other",
    },
    "factor-a-ex959": {
      "all-match": "msg-factor-a-match",
      "forget-factor-a": "msg-factor-a-forgot",
      "correct-a-wrong-hk": "msg-factor-a-hk-off",
      "other-mismatch": "msg-factor-a-other",
    },
    "rational-half": {
      "all-match": "msg-rational-match",
      "wrong-half-of-b": "msg-rational-h-off",
      "correct-h-wrong-k": "msg-rational-k-off",
      "other-mismatch": "msg-rational-other",
    },
    "verify-try9118": {
      "all-match": "msg-verify-match",
      "factor-2-needed": "msg-verify-factor-two",
      "correct-a-wrong-hk": "msg-verify-hk-off",
      "other-mismatch": "msg-verify-other",
    },
    "sketch-try9120": {
      "reading-match": "msg-sketch-match",
      "named-vertex-differs": "msg-sketch-vertex-off",
      "named-axis-differs": "msg-sketch-axis-off",
      "named-extreme-differs": "msg-sketch-extreme-off",
    },
    "strategy-cts-vs-factor": {
      "choices-match": "msg-strategy-match",
      "choices-differ": "msg-strategy-differ",
    },
    "fresh-hook-cts": {
      "reading-match": "msg-fresh-hook-match",
      "factor-neg-needed": "msg-fresh-hook-forgot-a",
      "named-vertex-differs": "msg-fresh-hook-vertex-off",
      "named-axis-differs": "msg-fresh-hook-axis-off",
      "named-extreme-differs": "msg-fresh-hook-extreme-off",
    },
  };

  var AFTER_PASS = {
    "expand-cts-revisit": "msg-revisit-same-curve",
    "verify-try9118": "msg-verify-tech-same-curve",
  };

  var TOAST = {
    "expand-cts-revisit": "toast-advance-to-diagram",
    "diagram-ex960": "toast-advance-to-algebraic",
    "algebraic-ex960": "toast-advance-to-guided",
    "algebraic-try9119": "toast-advance-to-factor-a",
    "factor-a-ex959": "toast-advance-to-rational",
    "rational-half": "toast-advance-to-verify",
    "verify-try9118": "toast-advance-to-sketch",
    "sketch-try9120": "toast-advance-to-consolidation",
  };

  function val(root, sel) {
    var el = root.querySelector(sel);
    return el ? el.value : "";
  }

  function readEvidence(root, specId) {
    var data = {
      standard_a: val(root, "[data-standard=a]"),
      standard_b: val(root, "[data-standard=b]"),
      standard_c: val(root, "[data-standard=c]"),
      a: val(root, "[data-ahk=a]"),
      h: val(root, "[data-ahk=h]"),
      k: val(root, "[data-ahk=k]"),
      side_length: val(root, "[data-side]"),
      leftover_constant: val(root, "[data-leftover]"),
      named_vertex_x: val(root, "[data-named=x]"),
      named_vertex_y: val(root, "[data-named=y]"),
      named_axis: val(root, "[data-named=axis]"),
      named_extreme: val(root, "[data-named=extreme]"),
      choice_A: val(root, "[data-choice=A]"),
      choice_B: val(root, "[data-choice=B]"),
    };
    if (specId === "expand-cts-revisit" || specId === "verify-try9118") {
      data.graphs_coincide_self_check = val(root, "[data-coincide]");
    }
    return data;
  }

  function inputsReady(root, specId) {
    var pred = root.querySelector("[data-prediction]");
    if (pred && !String(pred.value || "").trim()) {
      return "Write a prediction first, then check.";
    }
    if (specId === "diagram-ex960") {
      if (!val(root, "[data-side]") || !val(root, "[data-leftover]")) {
        return "Enter the side length and the leftover, then check.";
      }
    }
    if (
      specId === "algebraic-ex960" ||
      specId === "algebraic-try9119" ||
      specId === "factor-a-ex959" ||
      specId === "rational-half" ||
      specId === "verify-try9118"
    ) {
      if (!val(root, "[data-ahk=a]") || !val(root, "[data-ahk=h]") || !val(root, "[data-ahk=k]")) {
        return "Enter a, h, and k, then check.";
      }
    }
    if (specId === "expand-cts-revisit") {
      var abc =
        val(root, "[data-standard=a]") &&
        val(root, "[data-standard=b]") &&
        val(root, "[data-standard=c]");
      var ahk = val(root, "[data-ahk=a]") && val(root, "[data-ahk=h]") && val(root, "[data-ahk=k]");
      if (!abc && !ahk) {
        return "Enter the expanded coefficients or the recovered a, h, and k, then check.";
      }
    }
    if (specId === "sketch-try9120" || specId === "fresh-hook-cts") {
      if (
        !val(root, "[data-ahk=a]") ||
        !val(root, "[data-ahk=h]") ||
        !val(root, "[data-ahk=k]") ||
        !val(root, "[data-named=x]") ||
        !val(root, "[data-named=y]") ||
        !String(val(root, "[data-named=axis]") || "").trim() ||
        !String(val(root, "[data-named=extreme]") || "").trim()
      ) {
        return "Name a, h, k, the vertex, the axis, and max or min, then check.";
      }
    }
    if (specId === "strategy-cts-vs-factor") {
      if (!val(root, "[data-choice=A]") || !val(root, "[data-choice=B]")) {
        return "Choose a strategy for A and for B, then check.";
      }
    }
    return "";
  }

  function revealLocked(specId) {
    document.querySelectorAll('[data-reveal-after="' + specId + '"]').forEach(function (el) {
      el.hidden = false;
      el.querySelectorAll(".cycle").forEach(function (root) {
        if (root._ctsApi && root._ctsApi.resize) {
          root._ctsApi.resize();
        }
      });
    });
  }

  /**
   * Wire one .cycle[data-spec-id] node for a completing-the-square task.
   *
   * @param {HTMLElement} root
   * @param {object} opts messages, hints, optional board API
   */
  function bindCtsCycle(root, opts) {
    var specId = root.getAttribute("data-spec-id");
    var messages = opts.messages || {};
    var api = opts.api;
    var feedback = root.querySelector(".feedback");
    var hintEl = root.querySelector(".hint");
    var toastEl = root.querySelector(".toast");
    var liveEq = root.querySelector("[data-live-eq]");
    var hintIndex = 0;

    function showFeedback(kind, messageId) {
      feedback.hidden = false;
      feedback.classList.remove("is-ok", "is-retry");
      feedback.classList.add(kind === "ok" ? "is-ok" : "is-retry");
      feedback.textContent = messages[messageId] || "";
    }

    function showToast(messageId) {
      if (!toastEl || !messageId) {
        return;
      }
      toastEl.hidden = false;
      toastEl.textContent = messages[messageId] || "";
    }

    function submit() {
      var blocked = inputsReady(root, specId);
      if (blocked) {
        feedback.hidden = false;
        feedback.classList.remove("is-ok");
        feedback.classList.add("is-retry");
        feedback.textContent = blocked;
        return;
      }
      var data = readEvidence(root, specId);
      var condition = CompletingTheSquareChecker.classify(specId, data);
      var ok = condition === PASS[specId];
      var map = MESSAGE[specId] || {};
      showFeedback(ok ? "ok" : "retry", map[condition]);
      if (ok) {
        var a = CompletingTheSquareChecker.parseNum(data.a);
        var h = CompletingTheSquareChecker.parseNum(data.h);
        var k = CompletingTheSquareChecker.parseNum(data.k);
        if (api && api.reveal && a === a && h === h && k === k) {
          api.reveal(a, h, k);
        }
        if (liveEq && typeof ctsVertexFormString === "function" && a === a) {
          liveEq.hidden = false;
          liveEq.textContent = ctsVertexFormString(a, h, k);
        }
        if (AFTER_PASS[specId]) {
          var extra = root.querySelector("[data-after-pass]");
          if (extra) {
            extra.hidden = false;
            extra.textContent = messages[AFTER_PASS[specId]] || "";
          }
        }
        showToast(TOAST[specId]);
        revealLocked(specId);
        var tryAnother = root.querySelector("[data-action=another]");
        if (tryAnother) {
          tryAnother.hidden = false;
        }
      } else if (api && api.hide) {
        api.hide();
      }
    }

    function reset() {
      root.querySelectorAll("input, select, textarea").forEach(function (el) {
        if (el.type === "checkbox") {
          el.checked = false;
        } else {
          el.value = "";
        }
      });
      feedback.hidden = true;
      hintEl.hidden = true;
      if (toastEl) {
        toastEl.hidden = true;
      }
      if (liveEq) {
        liveEq.hidden = true;
        liveEq.textContent = "";
      }
      var extra = root.querySelector("[data-after-pass]");
      if (extra) {
        extra.hidden = true;
      }
      if (api && api.hide) {
        api.hide();
      }
    }

    var submitBtn = root.querySelector("[data-action=submit]");
    if (submitBtn) {
      submitBtn.addEventListener("click", submit);
    }
    var resetBtn = root.querySelector("[data-action=reset]");
    if (resetBtn) {
      resetBtn.addEventListener("click", reset);
    }
    var hintBtn = root.querySelector("[data-action=hint]");
    var hints = opts.hints || [];
    if (hintBtn && hints.length) {
      hintBtn.addEventListener("click", function () {
        var id = hints[hintIndex % hints.length];
        hintIndex += 1;
        hintEl.hidden = false;
        hintEl.textContent = messages[id] || "";
      });
    }
    var anotherBtn = root.querySelector("[data-action=another]");
    if (anotherBtn) {
      anotherBtn.addEventListener("click", function () {
        revealLocked(specId + "-try-another");
      });
    }
    if (api && api.resize) {
      api.resize();
    }
  }

  global.bindCtsCycle = bindCtsCycle;
})(window);
