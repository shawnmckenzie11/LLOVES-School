/**
 * Deterministic vertex-form checkers. No JSXGraph. Used by the formative cycle.
 */
(function (global) {
  "use strict";

  function expandStandard(a, h, k) {
    return { a: a, b: -2 * a * h, c: a * h * h + k };
  }

  function near(value, target, tol) {
    return Math.abs(value - target) <= tol;
  }

  function classifyMatch(a, h, k, tol) {
    tol = tol == null ? 0.15 : tol;
    if (Math.abs(a) < 0.15) {
      return "a-near-zero";
    }
    if (near(a, 1, tol) && near(h, -3, tol) && near(k, 1, tol)) {
      return "all-match";
    }
    if (near(h, -3, tol) && near(k, 1, tol) && a * 1 < 0 && Math.abs(a) >= 0.15) {
      return "correct-vertex-wrong-opening";
    }
    if (near(h, -3, tol) && near(k, 1, tol) && a > 0 && !near(a, 1, tol)) {
      return "correct-position-wrong-abs-a";
    }
    if (near(h, -3, tol) && !near(k, 1, tol) && Math.abs(a) >= 0.15) {
      return "correct-h-wrong-k";
    }
    return "other-mismatch";
  }

  function classifyPredictH(a, h, k, predicted, tol) {
    tol = tol == null ? 0.15 : tol;
    if (predicted !== predicted) {
      return "missing-prediction";
    }
    if (!near(a, 2, tol) || !near(k, 1, tol)) {
      return "uncertain-cause";
    }
    if (near(h, -3, tol)) {
      return "h-not-moved";
    }
    if (!near(h, 2, tol) || !near(predicted, 2, tol)) {
      return "vertex-x-differs-from-prediction";
    }
    return "vertex-x-matches-prediction";
  }

  function classifyExpand(sa, sb, sc) {
    var expected = expandStandard(2, -3, 1);
    if (near(sa, expected.a, 1e-6) && near(sb, expected.b, 1e-6) && near(sc, expected.c, 1e-6)) {
      return "coefficients-match";
    }
    return "coefficients-differ";
  }

  function classifyFresh(vx, vy, axis, extreme, tol) {
    tol = tol == null ? 0.15 : tol;
    var axisNorm = String(axis || "")
      .toLowerCase()
      .replace(/\s+/g, "")
      .replace("−", "-");
    var axisOk = axisNorm === "x=2" || axisNorm === "x=+2";
    var extremeOk = String(extreme || "")
      .trim()
      .toLowerCase()
      .indexOf("max") === 0;
    if (!near(vx, 2, tol) || !near(vy, 4, tol)) {
      return "named-vertex-differs";
    }
    if (!axisOk) {
      return "named-axis-differs";
    }
    if (!extremeOk) {
      return "named-extreme-differs";
    }
    return "reading-match";
  }

  global.VertexFormChecker = {
    expandStandard: expandStandard,
    classifyMatch: classifyMatch,
    classifyPredictH: classifyPredictH,
    classifyExpand: classifyExpand,
    classifyFresh: classifyFresh,
  };
})(window);
