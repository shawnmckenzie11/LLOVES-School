/**
 * Completing-the-square boards: standard/vertex overlay and sketch features.
 * Bundled JSXGraph only. No network, login, or remote computation.
 */
(function (global) {
  "use strict";

  function fmt(n) {
    if (Math.abs(n) < 1e-9) {
      return "0";
    }
    return String(Math.round(n * 1000) / 1000);
  }

  function timesVar(n, variable) {
    if (Math.abs(n - 1) < 1e-9) {
      return variable;
    }
    if (Math.abs(n + 1) < 1e-9) {
      return "−" + variable;
    }
    return fmt(n) + variable;
  }

  /**
   * Write vertex form including plus-inside when h < 0.
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
   * Write expanded standard form.
   *
   * @param {number} a
   * @param {number} b
   * @param {number} c
   * @returns {string}
   */
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

  function fillTable(tableEl, a, h, k) {
    if (!tableEl) {
      return;
    }
    var xs = [h - 2, h - 1, h, h + 1, h + 2];
    var rows = xs
      .map(function (x) {
        var y = a * (x - h) * (x - h) + k;
        return "<tr><td>" + fmt(x) + "</td><td>" + fmt(y) + "</td></tr>";
      })
      .join("");
    tableEl.innerHTML =
      '<thead><tr><th scope="col">x</th><th scope="col">f(x)</th></tr></thead><tbody>' +
      rows +
      "</tbody>";
  }

  /**
   * Overlay a given standard-form curve with a submitted vertex-form curve.
   *
   * @param {string} boxId
   * @param {object} opts abc given standard coefficients; reveal after submit
   * @returns {object} control API
   */
  function initCtsEquivalenceBoard(boxId, opts) {
    opts = opts || {};
    var given = opts.abc || { a: 1, b: 0, c: 0 };
    var state = { a: given.a, h: 0, k: given.c, revealed: false };
    var boxEl = document.getElementById(boxId);
    var board = JXG.JSXGraph.initBoard(boxId, {
      boundingbox: [-8, 12, 8, -8],
      axis: true,
      showCopyright: false,
      showNavigation: true,
      keepaspectratio: false,
    });

    board.create(
      "functiongraph",
      [
        function (x) {
          return given.a * x * x + given.b * x + given.c;
        },
      ],
      { strokeColor: "#555", strokeWidth: 2, dash: 2 }
    );

    var vertexCurve = board.create(
      "functiongraph",
      [
        function (x) {
          if (!state.revealed) {
            return NaN;
          }
          return state.a * (x - state.h) * (x - state.h) + state.k;
        },
      ],
      { strokeColor: "#0b5cab", strokeWidth: 2 }
    );

    var vertex = board.create(
      "point",
      [
        function () {
          return state.revealed ? state.h : NaN;
        },
        function () {
          return state.revealed ? state.k : NaN;
        },
      ],
      {
        name: function () {
          if (!state.revealed) {
            return "";
          }
          return "(" + fmt(state.h) + ", " + fmt(state.k) + ")";
        },
        size: 4,
        fillColor: "#c45c26",
        strokeColor: "#c45c26",
        fixed: true,
      }
    );

    var axisLine = board.create(
      "line",
      [
        function () {
          return [state.revealed ? state.h : NaN, -20];
        },
        function () {
          return [state.revealed ? state.h : NaN, 20];
        },
      ],
      { strokeColor: "#555", dash: 2, strokeWidth: 1, fixed: true }
    );

    function reveal(a, h, k) {
      state.a = a;
      state.h = h;
      state.k = k;
      state.revealed = true;
      board.update();
      if (opts.equationEl) {
        opts.equationEl.textContent = vertexFormString(a, h, k);
      }
      if (opts.standardEl) {
        opts.standardEl.textContent = standardFormString(given.a, given.b, given.c);
      }
      if (opts.axisEl) {
        opts.axisEl.textContent = "x = " + fmt(h);
      }
      fillTable(opts.tableEl, a, h, k);
    }

    function hide() {
      state.revealed = false;
      board.update();
      if (opts.equationEl) {
        opts.equationEl.textContent = "";
      }
      if (opts.axisEl) {
        opts.axisEl.textContent = "";
      }
      if (opts.tableEl) {
        opts.tableEl.innerHTML = "";
      }
    }

    return {
      board: board,
      vertexCurve: vertexCurve,
      vertex: vertex,
      axisLine: axisLine,
      reveal: reveal,
      hide: hide,
      resize: function () {
        if (!boxEl || !boxEl.clientWidth) {
          return;
        }
        board.resizeContainer(boxEl.clientWidth, boxEl.clientHeight || 320);
        board.fullUpdate();
      },
    };
  }

  /**
   * Sketch board: hide the curve until the student submits a, h, k.
   *
   * @param {string} boxId
   * @param {object} opts
   * @returns {object} control API
   */
  function initCtsSketchBoard(boxId, opts) {
    opts = opts || {};
    var state = { a: 1, h: 0, k: 0, revealed: false };
    var boxEl = document.getElementById(boxId);
    var board = JXG.JSXGraph.initBoard(boxId, {
      boundingbox: [-8, 12, 8, -8],
      axis: true,
      showCopyright: false,
      showNavigation: true,
      keepaspectratio: false,
    });

    board.create(
      "functiongraph",
      [
        function (x) {
          if (!state.revealed) {
            return NaN;
          }
          return state.a * (x - state.h) * (x - state.h) + state.k;
        },
      ],
      { strokeColor: "#0b5cab", strokeWidth: 2 }
    );

    board.create(
      "point",
      [
        function () {
          return state.revealed ? state.h : NaN;
        },
        function () {
          return state.revealed ? state.k : NaN;
        },
      ],
      {
        name: function () {
          if (!state.revealed) {
            return "";
          }
          return "(" + fmt(state.h) + ", " + fmt(state.k) + ")";
        },
        size: 4,
        fillColor: "#c45c26",
        strokeColor: "#c45c26",
        fixed: true,
      }
    );

    board.create(
      "line",
      [
        function () {
          return [state.revealed ? state.h : NaN, -20];
        },
        function () {
          return [state.revealed ? state.h : NaN, 20];
        },
      ],
      { strokeColor: "#555", dash: 2, strokeWidth: 1, fixed: true }
    );

    function reveal(a, h, k) {
      state.a = a;
      state.h = h;
      state.k = k;
      state.revealed = true;
      board.update();
      if (opts.equationEl) {
        opts.equationEl.textContent = vertexFormString(a, h, k);
      }
      if (opts.axisEl) {
        opts.axisEl.textContent = "x = " + fmt(h);
      }
    }

    function hide() {
      state.revealed = false;
      board.update();
      if (opts.equationEl) {
        opts.equationEl.textContent = "";
      }
      if (opts.axisEl) {
        opts.axisEl.textContent = "";
      }
    }

    return {
      board: board,
      reveal: reveal,
      hide: hide,
      resize: function () {
        if (!boxEl || !boxEl.clientWidth) {
          return;
        }
        board.resizeContainer(boxEl.clientWidth, boxEl.clientHeight || 320);
        board.fullUpdate();
      },
    };
  }

  global.initCtsEquivalenceBoard = initCtsEquivalenceBoard;
  global.initCtsSketchBoard = initCtsSketchBoard;
  global.ctsVertexFormString = vertexFormString;
  global.ctsStandardFormString = standardFormString;
})(window);
