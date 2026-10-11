/**
 * Formative cycle: start → manipulate → submit → check → feedback → retry.
 * Tool-agnostic wrapper; VertexFormChecker supplies math.
 */
(function (global) {
  "use strict";

  function fill(template, values) {
    return String(template || "").replace(/\{([a-zA-Z_]+)\}/g, function (_, key) {
      return values[key] == null ? "" : String(values[key]);
    });
  }

  function fmt(n) {
    if (Math.abs(n) < 1e-9) {
      return "0";
    }
    return String(Math.round(n * 1000) / 1000);
  }

  /**
   * Wire one .cycle[data-spec-id] node.
   *
   * @param {HTMLElement} root
   * @param {object} opts messages, board API, starting/fresh params
   */
  function bindCycle(root, opts) {
    var specId = root.getAttribute("data-spec-id");
    var messages = opts.messages || {};
    var api = opts.api;
    var sa = root.querySelector("[data-slider=a]");
    var sh = root.querySelector("[data-slider=h]");
    var sk = root.querySelector("[data-slider=k]");
    var feedback = root.querySelector(".feedback");
    var hintEl = root.querySelector(".hint");
    var hintIndex = 0;
    var lastA = sa ? parseFloat(sa.value) : 0;

    function stateFromSliders() {
      var a = sa ? parseFloat(sa.value) : 0;
      if (Math.abs(a) < 1e-9) {
        a = lastA >= 0 ? 0.1 : -0.1;
        if (sa) {
          sa.value = String(a);
        }
      }
      lastA = a;
      return {
        a: a,
        h: sh ? parseFloat(sh.value) : 0,
        k: sk ? parseFloat(sk.value) : 0,
      };
    }

    function syncBoard() {
      var s = stateFromSliders();
      if (api && api.setParams) {
        api.setParams(s.a, s.h, s.k);
      }
      var va = root.querySelector("[data-val=a]");
      var vh = root.querySelector("[data-val=h]");
      var vk = root.querySelector("[data-val=k]");
      if (va) {
        va.textContent = sa.value;
      }
      if (vh) {
        vh.textContent = sh.value;
      }
      if (vk) {
        vk.textContent = sk.value;
      }
    }

    function showFeedback(kind, messageId, values) {
      var text = fill(messages[messageId] || "", values);
      feedback.hidden = false;
      feedback.classList.remove("is-ok", "is-retry");
      feedback.classList.add(kind === "ok" ? "is-ok" : "is-retry");
      feedback.textContent = text;
    }

    function submit() {
      var s = stateFromSliders();
      var values = {
        a: fmt(s.a),
        h: fmt(s.h),
        k: fmt(s.k),
        observed_h: fmt(s.h),
      };
      var condition;
      var ok = false;
      if (specId === "match-plus-inside") {
        condition = VertexFormChecker.classifyMatch(s.a, s.h, s.k);
        var map = {
          "a-near-zero": "msg-match-a-near-zero",
          "all-match": "msg-match-all",
          "correct-vertex-wrong-opening": "msg-match-wrong-opening",
          "correct-position-wrong-abs-a": "msg-match-wrong-abs-a",
          "correct-h-wrong-k": "msg-match-h-not-k",
          "other-mismatch": "msg-match-other",
        };
        ok = condition === "all-match";
        showFeedback(ok ? "ok" : "retry", map[condition], values);
      } else if (specId === "predict-h") {
        var predictedField = root.querySelector("[data-predicted-x]");
        var predicted = parseFloat(predictedField && predictedField.value);
        if (predicted !== predicted) {
          showFeedback("retry", "msg-predict-h-not-moved", values);
          feedback.textContent = "Write the vertex x you expect, then move h and check.";
          return;
        }
        values.predicted_vertex_x = fmt(predicted);
        condition = VertexFormChecker.classifyPredictH(s.a, s.h, s.k, predicted);
        var pmap = {
          "uncertain-cause": "msg-predict-h-uncertain",
          "h-not-moved": "msg-predict-h-not-moved",
          "missing-prediction": "hint-predict-h-1-name-x-first",
          "vertex-x-matches-prediction": "msg-predict-h-match",
          "vertex-x-differs-from-prediction": "msg-predict-h-differ",
        };
        ok = condition === "vertex-x-matches-prediction";
        showFeedback(ok ? "ok" : "retry", pmap[condition], values);
      } else if (specId === "expand-same-graph") {
        var ea = parseFloat(root.querySelector("[data-standard=a]").value);
        var eb = parseFloat(root.querySelector("[data-standard=b]").value);
        var ec = parseFloat(root.querySelector("[data-standard=c]").value);
        if (ea !== ea || eb !== eb || ec !== ec) {
          showFeedback("retry", "hint-expand-1-square-the-binomial", values);
          feedback.textContent = "Enter numbers for a, b, and c from your expansion, then check.";
          return;
        }
        values.standard_a = fmt(ea);
        values.standard_b = fmt(eb);
        values.standard_c = fmt(ec);
        condition = VertexFormChecker.classifyExpand(ea, eb, ec);
        ok = condition === "coefficients-match";
        showFeedback(ok ? "ok" : "retry", ok ? "msg-expand-match" : "msg-expand-differ", values);
        if (ok) {
          var stdLine = root.querySelector("[data-standard-line]");
          if (stdLine) {
            stdLine.hidden = false;
          }
        }
      } else if (specId === "fresh-case") {
        var vx = parseFloat(root.querySelector("[data-named=x]").value);
        var vy = parseFloat(root.querySelector("[data-named=y]").value);
        var axis = root.querySelector("[data-named=axis]").value;
        var extreme = root.querySelector("[data-named=extreme]").value;
        if (vx !== vx || vy !== vy || !String(axis || "").trim() || !String(extreme || "").trim()) {
          showFeedback("retry", "hint-fresh-1-match-brackets", values);
          feedback.textContent = "Name the vertex, the axis, and whether this is a maximum or a minimum, then check.";
          return;
        }
        values.named_vertex_x = fmt(vx);
        values.named_vertex_y = fmt(vy);
        values.named_axis = axis;
        condition = VertexFormChecker.classifyFresh(vx, vy, axis, extreme);
        var fmap = {
          "reading-match": "msg-fresh-reading-match",
          "named-vertex-differs": "msg-fresh-vertex-differs",
          "named-axis-differs": "msg-fresh-axis-differs",
          "named-extreme-differs": "msg-fresh-extreme-differs",
        };
        ok = condition === "reading-match";
        showFeedback(ok ? "ok" : "retry", fmap[condition], values);
      }
    }

    function resetBoard(params) {
      if (sa) {
        sa.value = params.a;
      }
      if (sh) {
        sh.value = params.h;
      }
      if (sk) {
        sk.value = params.k;
      }
      lastA = params.a;
      if (api && api.setParams) {
        api.setParams(params.a, params.h, params.k);
      }
      syncBoard();
    }

    if (sa) {
      sa.addEventListener("input", syncBoard);
    }
    if (sh) {
      sh.addEventListener("input", syncBoard);
    }
    if (sk) {
      sk.addEventListener("input", syncBoard);
    }
    var submitBtn = root.querySelector("[data-action=submit]");
    if (submitBtn) {
      submitBtn.addEventListener("click", submit);
    }
    var resetBtn = root.querySelector("[data-action=reset]");
    if (resetBtn) {
      resetBtn.addEventListener("click", function () {
        resetBoard(opts.start);
        feedback.hidden = true;
        hintEl.hidden = true;
      });
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
    syncBoard();
    if (api && api.resize) {
      api.resize();
    }
  }

  global.bindFormativeCycle = bindCycle;
})(window);
