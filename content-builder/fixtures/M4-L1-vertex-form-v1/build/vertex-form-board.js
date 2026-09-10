/**
 * Small vertex-form board: f(x) = a(x − h)^2 + k.
 * Bundled JSXGraph only. No network, login, or remote computation.
 */
(function (global) {
  "use strict";

  /**
   * Expand a(x − h)^2 + k to ax^2 + bx + c.
   *
   * @param {number} a stretch / reflection
   * @param {number} h vertex x-coordinate
   * @param {number} k vertex y-coordinate
   * @returns {{a: number, b: number, c: number}}
   */
  function expandStandard(a, h, k) {
    return { a: a, b: -2 * a * h, c: a * h * h + k };
  }

  /**
   * Format a coefficient for student-facing math text.
   *
   * @param {number} n value
   * @returns {string}
   */
  function fmt(n) {
    if (Math.abs(n) < 1e-9) {
      return "0";
    }
    var r = Math.round(n * 1000) / 1000;
    return String(r);
  }

  /**
   * Write vertex form the way students write it, including plus-inside when h < 0.
   *
   * @param {number} a
   * @param {number} h
   * @param {number} k
   * @returns {string}
   */
  function vertexFormString(a, h, k) {
    var square;
    if (Math.abs(h) < 1e-9) {
      square = "x²";
    } else if (h > 0) {
      square = "(x − " + fmt(h) + ")²";
    } else {
      square = "(x + " + fmt(-h) + ")²";
    }
    var lead;
    if (Math.abs(a - 1) < 1e-9) {
      lead = "";
    } else if (Math.abs(a + 1) < 1e-9) {
      lead = "−";
    } else {
      lead = fmt(a);
    }
    var kPart = "";
    if (Math.abs(k) > 1e-9) {
      kPart = k > 0 ? " + " + fmt(k) : " − " + fmt(-k);
    }
    return "f(x) = " + lead + square + kPart;
  }

  /**
   * Write expanded standard form without extra parentheses on positive terms.
   *
   * @param {number} a
   * @param {number} b
   * @param {number} c
   * @returns {string}
   */
  function timesVar(n, variable) {
    if (Math.abs(n - 1) < 1e-9) {
      return variable;
    }
    if (Math.abs(n + 1) < 1e-9) {
      return "−" + variable;
    }
    return fmt(n) + variable;
  }

  function standardFormString(a, b, c) {
    var parts = [timesVar(a, "x²")];
    if (Math.abs(b) > 1e-9) {
      parts.push(b > 0 ? "+ " + timesVar(b, "x") : "− " + timesVar(-b, "x"));
    }
    if (Math.abs(c) > 1e-9) {
      parts.push(c > 0 ? "+ " + fmt(c) : "− " + fmt(-c));
    }
    return "f(x) = " + parts.join(" ");
  }

  /**
   * Initialize the board in a DOM element.
   *
   * @param {string} boxId element id
   * @param {object} [opts] starting / fresh parameters
   * @returns {object} control API
   */
  function initVertexFormBoard(boxId, opts) {
    opts = opts || {};
    var start = opts.starting || { a: 2, h: -3, k: 1 };
    var fresh = opts.fresh || { a: -0.5, h: 2, k: 4 };
    var state = { a: start.a, h: start.h, k: start.k };

    var board = JXG.JSXGraph.initBoard(boxId, {
      boundingbox: [-8, 10, 8, -6],
      axis: true,
      showCopyright: true,
      showNavigation: true,
      keepaspectratio: false,
    });

    var axisLine = board.create(
      "line",
      [
        function () {
          return [state.h, -20];
        },
        function () {
          return [state.h, 20];
        },
      ],
      { strokeColor: "#555", dash: 2, strokeWidth: 1, fixed: true }
    );

    var parabola = board.create(
      "functiongraph",
      [
        function (x) {
          return state.a * (x - state.h) * (x - state.h) + state.k;
        },
      ],
      { strokeColor: "#0b5cab", strokeWidth: 2 }
    );

    var vertex = board.create(
      "point",
      [
        function () {
          return state.h;
        },
        function () {
          return state.k;
        },
      ],
      {
        name: function () {
          return "(" + fmt(state.h) + ", " + fmt(state.k) + ")";
        },
        size: 4,
        fillColor: "#c45c26",
        strokeColor: "#c45c26",
        fixed: true,
      }
    );

    var eqEl = document.getElementById(opts.equationId || "vf-equation");
    var stdEl = document.getElementById(opts.standardId || "vf-standard");
    var axisEl = document.getElementById(opts.axisId || "vf-axis");
    var tableEl = document.getElementById(opts.tableId || "vf-table");
    var boxEl = document.getElementById(boxId);

    /**
     * Refresh equation text, axis line, and the shared-points table.
     */
    function renderText() {
      if (eqEl) {
        eqEl.textContent = vertexFormString(state.a, state.h, state.k);
      }
      if (axisEl) {
        axisEl.textContent = "x = " + fmt(state.h);
      }
      var st = expandStandard(state.a, state.h, state.k);
      if (stdEl) {
        stdEl.textContent = standardFormString(st.a, st.b, st.c);
      }
      if (tableEl) {
        var xs = [state.h - 2, state.h - 1, state.h, state.h + 1, state.h + 2];
        var rows = xs
          .map(function (x) {
            var y = state.a * (x - state.h) * (x - state.h) + state.k;
            return (
              "<tr><td>" +
              fmt(x) +
              "</td><td>" +
              fmt(y) +
              "</td></tr>"
            );
          })
          .join("");
        tableEl.innerHTML =
          "<thead><tr><th scope=\"col\">x</th><th scope=\"col\">f(x)</th></tr></thead><tbody>" +
          rows +
          "</tbody>";
      }
    }

    /**
     * Apply parameters and redraw.
     *
     * @param {number} a
     * @param {number} h
     * @param {number} k
     */
    function setParams(a, h, k) {
      state.a = a;
      state.h = h;
      state.k = k;
      board.update();
      renderText();
    }

    renderText();

    return {
      board: board,
      parabola: parabola,
      vertex: vertex,
      axisLine: axisLine,
      getState: function () {
        return { a: state.a, h: state.h, k: state.k };
      },
      setParams: setParams,
      resize: function () {
        if (!boxEl || !boxEl.clientWidth) {
          return;
        }
        board.resizeContainer(boxEl.clientWidth, boxEl.clientHeight || 380);
        board.fullUpdate();
      },
      start: function () {
        setParams(start.a, start.h, start.k);
      },
      diagnostic: function () {
        setParams(1, -3, 1);
      },
      fresh: function () {
        setParams(fresh.a, fresh.h, fresh.k);
      },
    };
  }

  global.initVertexFormBoard = initVertexFormBoard;
  global.expandStandard = expandStandard;
  global.vertexFormString = vertexFormString;
})(window);
