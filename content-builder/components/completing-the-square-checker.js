/**
 * Deterministic completing-the-square checkers. No JSXGraph.
 * Used by the M4-L2 formative cycle. Conditions match feedback-spec.json.
 */
(function (global) {
  "use strict";

  /**
   * Parse a student number, including unicode minus and simple fractions.
   *
   * @param {*} value raw input
   * @returns {number}
   */
  function parseNum(value) {
    if (value == null || value === "") {
      return NaN;
    }
    if (typeof value === "number") {
      return value;
    }
    var text = String(value)
      .trim()
      .replace(/−/g, "-")
      .replace(/\s+/g, "");
    if (!text) {
      return NaN;
    }
    if (text.indexOf("/") >= 0) {
      var parts = text.split("/");
      var num = parseFloat(parts[0]);
      var den = parseFloat(parts[1]);
      if (!isFinite(num) || !isFinite(den) || den === 0) {
        return NaN;
      }
      return num / den;
    }
    return parseFloat(text);
  }

  function near(value, target, tol) {
    if (value !== value) {
      return false;
    }
    return Math.abs(value - target) <= tol;
  }

  function axisIs(text, n) {
    var compact = String(text || "")
      .toLowerCase()
      .replace(/−/g, "-")
      .replace(/\s+/g, "");
    var nInt = n === Math.floor(n) ? String(n) : String(n);
    return (
      compact === "x=" + nInt ||
      compact === "x=+" + nInt ||
      compact === "x=" + String(n)
    );
  }

  function extremeIs(text, kind) {
    var token = String(text || "")
      .trim()
      .toLowerCase();
    if (kind === "minimum") {
      return token.indexOf("min") === 0;
    }
    return token.indexOf("max") === 0;
  }

  function choiceKind(text) {
    var token = String(text || "")
      .trim()
      .toLowerCase()
      .replace(/_/g, "-")
      .replace(/\s+/g, "-");
    if (token === "factor" || token === "factoring") {
      return "factor";
    }
    if (
      token === "complete-the-square" ||
      token === "completing-the-square" ||
      token === "cts" ||
      token === "complete"
    ) {
      return "complete-the-square";
    }
    return token;
  }

  function classifyExpandCtsRevisit(data) {
    var sa = parseNum(data.standard_a);
    var sb = parseNum(data.standard_b);
    var sc = parseNum(data.standard_c);
    var a = parseNum(data.a);
    var h = parseNum(data.h);
    var k = parseNum(data.k);
    var abcOk = near(sa, 3, 0) && near(sb, -6, 0) && near(sc, 7, 0);
    var ahkOk = near(a, 3, 0) && near(h, 1, 0) && near(k, 4, 0);
    if (abcOk || ahkOk) {
      return "all-match";
    }
    return "standard-or-recovered-off";
  }

  function classifyDiagramEx960(data) {
    var side = parseNum(data.side_length);
    var leftover = parseNum(data.leftover_constant);
    if (near(side, 3, 0) && near(leftover, -4, 0)) {
      return "all-match";
    }
    if (!near(side, 3, 0)) {
      return "side-off";
    }
    if (near(side, 3, 0) && !near(leftover, -4, 0)) {
      return "leftover-off";
    }
    return "other-mismatch";
  }

  function classifyAlgebraicEx960(data) {
    var a = parseNum(data.a);
    var h = parseNum(data.h);
    var k = parseNum(data.k);
    if (near(a, 1, 0) && near(h, -3, 0) && near(k, -4, 0)) {
      return "all-match";
    }
    if (near(h, 3, 0) && near(a, 1, 0)) {
      return "wrong-h-sign";
    }
    if (near(h, -3, 0) && !near(k, -4, 0) && near(a, 1, 0)) {
      return "correct-h-wrong-k";
    }
    if (!near(h, -3, 0) && !near(h, 3, 0) && near(a, 1, 0)) {
      return "wrong-h-sign-or-half";
    }
    return "other-mismatch";
  }

  function classifyAlgebraicTry9119(data) {
    var a = parseNum(data.a);
    var h = parseNum(data.h);
    var k = parseNum(data.k);
    if (near(a, 1, 0) && near(h, -1, 0) && near(k, -4, 0)) {
      return "all-match";
    }
    if (near(a, 1, 0) && near(h, -3, 0) && near(k, -4, 0)) {
      return "copied-960-target";
    }
    if (near(h, 1, 0) && near(a, 1, 0)) {
      return "wrong-h-positive";
    }
    if (near(h, -1, 0) && !near(k, -4, 0) && near(a, 1, 0)) {
      return "correct-h-wrong-k";
    }
    if (!near(h, -1, 0) && !near(h, 1, 0) && near(a, 1, 0)) {
      return "wrong-h-sign-or-half";
    }
    return "other-mismatch";
  }

  function classifyFactorAEx959(data) {
    var a = parseNum(data.a);
    var h = parseNum(data.h);
    var k = parseNum(data.k);
    if (near(a, -3, 0) && near(h, -1, 0) && near(k, 2, 0)) {
      return "all-match";
    }
    if (near(a, 1, 0) || near(a, 3, 0)) {
      return "forget-factor-a";
    }
    if (near(a, -3, 0) && (!near(h, -1, 0) || !near(k, 2, 0))) {
      return "correct-a-wrong-hk";
    }
    return "other-mismatch";
  }

  function classifyRationalHalf(data) {
    var a = parseNum(data.a);
    var h = parseNum(data.h);
    var k = parseNum(data.k);
    if (near(a, 1, 0) && near(h, -0.25, 0.0001) && near(k, 0.9375, 0.0001)) {
      return "all-match";
    }
    if (!near(h, -0.25, 0.0001) && near(a, 1, 0)) {
      return "wrong-half-of-b";
    }
    if (near(h, -0.25, 0.0001) && !near(k, 0.9375, 0.0001) && near(a, 1, 0)) {
      return "correct-h-wrong-k";
    }
    return "other-mismatch";
  }

  function classifyVerifyTry9118(data) {
    var a = parseNum(data.a);
    var h = parseNum(data.h);
    var k = parseNum(data.k);
    if (near(a, 2, 0) && near(h, 2, 0) && near(k, -5, 0)) {
      return "all-match";
    }
    if (near(a, 1, 0)) {
      return "factor-2-needed";
    }
    if (near(a, 2, 0) && (!near(h, 2, 0) || !near(k, -5, 0))) {
      return "correct-a-wrong-hk";
    }
    return "other-mismatch";
  }

  function classifySketchTry9120(data) {
    var a = parseNum(data.a);
    var h = parseNum(data.h);
    var k = parseNum(data.k);
    var vx = parseNum(data.named_vertex_x);
    var vy = parseNum(data.named_vertex_y);
    var axisOk = axisIs(data.named_axis, 4);
    var extremeOk = extremeIs(data.named_extreme, "minimum");
    var ahkOk = near(a, 1, 0) && near(h, 4, 0) && near(k, -4, 0);
    var vertexOk = near(vx, 4, 0) && near(vy, -4, 0);
    if (ahkOk && vertexOk && axisOk && extremeOk) {
      return "reading-match";
    }
    if (!vertexOk) {
      return "named-vertex-differs";
    }
    if (vertexOk && !axisOk) {
      return "named-axis-differs";
    }
    if (vertexOk && axisOk && !extremeOk) {
      return "named-extreme-differs";
    }
    return "named-vertex-differs";
  }

  function classifyStrategy(data) {
    var choiceA = choiceKind(data.choice_A);
    var choiceB = choiceKind(data.choice_B);
    if (choiceA === "factor" && choiceB === "complete-the-square") {
      return "choices-match";
    }
    return "choices-differ";
  }

  function classifyFreshHook(data) {
    var a = parseNum(data.a);
    var h = parseNum(data.h);
    var k = parseNum(data.k);
    var vx = parseNum(data.named_vertex_x);
    var vy = parseNum(data.named_vertex_y);
    var axisOk = axisIs(data.named_axis, 3);
    var extremeOk = extremeIs(data.named_extreme, "maximum");
    var ahkOk = near(a, -1, 0) && near(h, 3, 0) && near(k, 4, 0);
    var vertexOk = near(vx, 3, 0) && near(vy, 4, 0);
    var rewriteSubmitted = a === a || h === h || k === k;
    if (ahkOk && vertexOk && axisOk && extremeOk) {
      return "reading-match";
    }
    if (rewriteSubmitted && near(a, 1, 0)) {
      return "factor-neg-needed";
    }
    if (!vertexOk) {
      return "named-vertex-differs";
    }
    if (vertexOk && !axisOk) {
      return "named-axis-differs";
    }
    if (vertexOk && axisOk && !extremeOk) {
      return "named-extreme-differs";
    }
    return "named-vertex-differs";
  }

  var classifiers = {
    "expand-cts-revisit": classifyExpandCtsRevisit,
    "diagram-ex960": classifyDiagramEx960,
    "algebraic-ex960": classifyAlgebraicEx960,
    "algebraic-try9119": classifyAlgebraicTry9119,
    "factor-a-ex959": classifyFactorAEx959,
    "rational-half": classifyRationalHalf,
    "verify-try9118": classifyVerifyTry9118,
    "sketch-try9120": classifySketchTry9120,
    "strategy-cts-vs-factor": classifyStrategy,
    "fresh-hook-cts": classifyFreshHook,
  };

  /**
   * Classify one submitted task.
   *
   * @param {string} taskId feedback-spec task id
   * @param {object} data student evidence
   * @returns {string} first matching condition id
   */
  function classify(taskId, data) {
    var fn = classifiers[taskId];
    if (!fn) {
      return "other-mismatch";
    }
    return fn(data || {});
  }

  global.CompletingTheSquareChecker = {
    parseNum: parseNum,
    classify: classify,
  };
})(window);
