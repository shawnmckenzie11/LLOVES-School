#!/usr/bin/env python3
"""Compile a static three-tab lesson from instruction JSON. No LMS."""

from __future__ import annotations

import argparse
import json
import shutil
import textwrap
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "components"


def _para(text: str) -> str:
    """Escape and wrap paragraphs."""
    chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
    parts: list[str] = []
    for chunk in chunks:
        if "\n" in chunk and all(len(line) < 80 for line in chunk.split("\n")):
            parts.append(f"<pre>{escape(chunk)}</pre>")
        else:
            parts.append("<p>" + "<br>\n".join(escape(line) for line in chunk.split("\n")) + "</p>")
    return "\n".join(parts)


def build_lesson(lesson_dir: Path, out_dir: Path) -> Path:
    """Write HTML and copy bundled assets into ``out_dir``."""
    instruction = json.loads((lesson_dir / "instruction.json").read_text(encoding="utf-8"))
    brief = json.loads((lesson_dir / "lesson-brief.json").read_text(encoding="utf-8"))
    spec = json.loads((lesson_dir / "interaction-spec.json").read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "jsxgraph/jsxgraphcore.js",
        "jsxgraph/jsxgraph.css",
        "jsxgraph/LICENSE.MIT",
        "vertex-form-board.js",
        "lesson.css",
    ):
        src = COMPONENTS / name
        dest = out_dir / Path(name).name if name.startswith("jsxgraph/") else out_dir / name
        if name.startswith("jsxgraph/"):
            dest = out_dir / Path(name).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    start = spec["board"]["starting_parameters"]
    fresh = spec["board"]["fresh_case_parameters"]
    student_fallback = spec.get("fallback_student") or spec["fallback"]
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(brief.get("title", instruction["lesson_id"]))}</title>
  <link rel="stylesheet" href="jsxgraph.css">
  <link rel="stylesheet" href="lesson.css">
</head>
<body>
  <article class="lesson">
    <h1>{escape(brief.get("title", ""))}</h1>
    <p class="meta">{escape(brief.get("course_code", ""))} · Module {escape(str(brief.get("module", "")))} · {escape(instruction["lesson_id"])}</p>
    <div class="tabs" role="tablist">
      <button type="button" role="tab" id="tab-minds" aria-controls="panel-minds" aria-selected="true">Minds On</button>
      <button type="button" role="tab" id="tab-action" aria-controls="panel-action" aria-selected="false">Action</button>
      <button type="button" role="tab" id="tab-cons" aria-controls="panel-cons" aria-selected="false">Consolidation</button>
    </div>
    <section class="panel active" id="panel-minds" role="tabpanel" aria-labelledby="tab-minds">
      <div class="prose">{_para(instruction["minds_on"]["student"])}</div>
    </section>
    <section class="panel" id="panel-action" role="tabpanel" aria-labelledby="tab-action" hidden>
      <div class="prose">{_para(instruction["action"]["student"])}</div>
      <h2>Guided example</h2>
      <div class="prose">{_para(instruction["action"]["guided_example"])}</div>
      <h2>Graph tool</h2>
      <div class="prose">{_para(instruction["action"]["interactive_bridge"])}</div>
      <div class="board-wrap">
        <p class="live-eq"><strong>Vertex form:</strong> <span id="vf-equation"></span></p>
        <p class="live-eq"><strong>Same function, expanded:</strong> <span id="vf-standard"></span></p>
        <p class="live-eq"><strong>Axis of symmetry:</strong> <span id="vf-axis"></span></p>
        <figure class="board-figure">
          <div id="vertex-board" role="img" aria-label="Interactive parabola in vertex form"></div>
          <figcaption class="sr-only">Parabola in vertex form. Vertex marker, dashed axis of symmetry, and sliders for a, h, and k. Live equations and a table of points sit beside the graph.</figcaption>
        </figure>
        <div class="controls">
          <label for="slider-a">a <input id="slider-a" type="range" min="-3" max="3" step="0.1" value="{start["a"]}"><span id="val-a"></span></label>
          <label for="slider-h">h <input id="slider-h" type="range" min="-5" max="5" step="0.1" value="{start["h"]}"><span id="val-h"></span></label>
          <label for="slider-k">k <input id="slider-k" type="range" min="-5" max="8" step="0.1" value="{start["k"]}"><span id="val-k"></span></label>
        </div>
        <div class="toolbar">
          <button type="button" id="btn-start">Starting equation</button>
          <button type="button" id="btn-diagnostic">Match f(x) = (x + 3)² + 1</button>
          <button type="button" id="btn-fresh">Fresh case</button>
        </div>
        <h3>Shared points</h3>
        <table class="data" id="vf-table"></table>
      </div>
      <h2>Practice</h2>
      <div class="prose">{_para(instruction["action"]["practice"])}</div>
      <details class="fallback">
        <summary>If the graph tool does not run</summary>
        <div class="prose">{_para(student_fallback)}</div>
      </details>
    </section>
    <section class="panel" id="panel-cons" role="tabpanel" aria-labelledby="tab-cons" hidden>
      <div class="prose">{_para(instruction["consolidation"]["student"])}</div>
    </section>
    <p class="attr">Graph tool: JSXGraph (MIT). OpenStax excerpts: CC BY-NC-SA 4.0. Access for free at https://openstax.org/books/college-algebra-2e/pages/1-introduction-to-prerequisites. Ontario sample: Queen’s Printer excerpt via the verified seed. No student login or remote computation.</p>
  </article>
  <script src="jsxgraphcore.js"></script>
  <script src="vertex-form-board.js"></script>
  <script>
    (function () {{
      var tabs = document.querySelectorAll('[role="tab"]');
      var panels = {{
        "tab-minds": "panel-minds",
        "tab-action": "panel-action",
        "tab-cons": "panel-cons"
      }};
      var api = initVertexFormBoard("vertex-board", {{
        starting: {json.dumps(start)},
        fresh: {json.dumps(fresh)},
        equationId: "vf-equation",
        standardId: "vf-standard",
        axisId: "vf-axis",
        tableId: "vf-table"
      }});
      tabs.forEach(function (tab) {{
        tab.addEventListener("click", function () {{
          tabs.forEach(function (t) {{ t.setAttribute("aria-selected", "false"); }});
          tab.setAttribute("aria-selected", "true");
          Object.keys(panels).forEach(function (id) {{
            var panel = document.getElementById(panels[id]);
            var on = id === tab.id;
            panel.classList.toggle("active", on);
            panel.hidden = !on;
          }});
          if (tab.id === "tab-action") {{
            api.resize();
          }}
        }});
      }});
      var sa = document.getElementById("slider-a");
      var sh = document.getElementById("slider-h");
      var sk = document.getElementById("slider-k");
      var lastA = parseFloat(sa.value);
      function syncLabels() {{
        document.getElementById("val-a").textContent = sa.value;
        document.getElementById("val-h").textContent = sh.value;
        document.getElementById("val-k").textContent = sk.value;
      }}
      function fromSliders() {{
        var a = parseFloat(sa.value);
        if (Math.abs(a) < 1e-9) {{
          a = lastA >= 0 ? 0.1 : -0.1;
          sa.value = String(a);
        }}
        lastA = a;
        api.setParams(a, parseFloat(sh.value), parseFloat(sk.value));
        syncLabels();
      }}
      sa.addEventListener("input", fromSliders);
      sh.addEventListener("input", fromSliders);
      sk.addEventListener("input", fromSliders);
      function applyState(fn) {{
        fn();
        var s = api.getState();
        sa.value = s.a;
        sh.value = s.h;
        sk.value = s.k;
        lastA = s.a;
        syncLabels();
      }}
      document.getElementById("btn-start").addEventListener("click", function () {{ applyState(api.start); }});
      document.getElementById("btn-diagnostic").addEventListener("click", function () {{ applyState(api.diagnostic); }});
      document.getElementById("btn-fresh").addEventListener("click", function () {{ applyState(api.fresh); }});
      syncLabels();
    }})();
  </script>
</body>
</html>
"""
    index = out_dir / "index.html"
    index.write_text(textwrap.dedent(html), encoding="utf-8")
    return index


def main() -> int:
    """CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lesson",
        default="content-builder/lessons/MCF3M/M4-L1-vertex-form",
        type=Path,
    )
    parser.add_argument(
        "--out",
        default="content-builder/build/MCF3M/M4-L1-vertex-form",
        type=Path,
    )
    args = parser.parse_args()
    path = build_lesson(args.lesson.resolve(), args.out.resolve())
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
