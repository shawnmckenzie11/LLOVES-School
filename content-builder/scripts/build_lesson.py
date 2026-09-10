#!/usr/bin/env python3
"""Compile static student HTML from student-content.json only. No LMS."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "components"

from student_fields import assert_student_content  # noqa: E402


def math_text(text: str) -> str:
    """Strip TeX delimiters for a static page (no CDN)."""
    t = text.replace("\\(", "").replace("\\)", "").replace("\\[", "").replace("\\]", "")
    t = t.replace("\\frac{1}{2}", "½")
    t = t.replace("^2", "²")
    t = t.replace("^{2}", "²")
    return escape(t)


def render_blocks(blocks: list, *, lesson_dir: Path, sets: dict, spec: dict) -> str:
    """Render permitted student blocks to HTML."""
    chunks: list[str] = []
    for block in blocks:
        kind = block["type"]
        if kind == "p":
            chunks.append(f"<p>{math_text(block['text'])}</p>")
        elif kind == "h2":
            chunks.append(f"<h2>{math_text(block['text'])}</h2>")
        elif kind == "equation":
            chunks.append(f'<p class="equation">{math_text(block["text"])}</p>')
        elif kind == "task":
            chunks.append(f'<div class="task"><p>{math_text(block["prompt"])}</p></div>')
        elif kind == "figure":
            src = escape(block["src"])
            alt = escape(block["alt"])
            chunks.append(
                f'<figure class="figure"><img src="{src}" alt="{alt}"></figure>'
            )
        elif kind == "example":
            inner = render_blocks(block["blocks"], lesson_dir=lesson_dir, sets=sets, spec=spec)
            stage = escape(block.get("stage") or "worked")
            chunks.append(f'<div class="example is-{stage}">{inner}</div>')
        elif kind == "self-check":
            chunks.append(
                f'<div class="self-check"><p>{math_text(block["prompt"])}</p></div>'
            )
        elif kind == "practice-set":
            chunks.append(_practice_set_html(block, sets))
        elif kind == "interactive":
            chunks.append(_interactive_html(block["spec_id"], spec))
        elif kind == "goal":
            chunks.append(
                f'<div class="goal"><p class="goal-label">Goal</p>'
                f'<p>{math_text(block["text"])}</p></div>'
            )
        elif kind == "reflect":
            title = math_text(block.get("title") or "Reflect")
            items = []
            for q in block.get("questions") or []:
                qid = escape(str(q.get("id") or ""))
                prompt = math_text(q.get("prompt") or "")
                label = f'<span class="reflect-id">{qid}</span> ' if qid else ""
                items.append(f"<li>{label}{prompt}</li>")
            chunks.append(
                f'<section class="reflect" aria-label="Reflect">'
                f"<h2>{title}</h2><ol class=\"reflect-list\">{''.join(items)}</ol></section>"
            )
        elif kind == "key-idea":
            chunks.append(
                f'<section class="key-idea" aria-label="Key idea">'
                f'<p class="chrome-label">Key idea</p>'
                f'<p>{math_text(block["text"])}</p></section>'
            )
        elif kind == "need-to-know":
            lis = "".join(f"<li>{math_text(item)}</li>" for item in block.get("items") or [])
            chunks.append(
                f'<section class="need-to-know" aria-label="Need to know">'
                f'<p class="chrome-label">Need to know</p>'
                f'<ul>{lis}</ul></section>'
            )
        elif kind == "dual-path":
            intro = ""
            if block.get("prompt"):
                intro = f'<p class="dual-path-prompt">{math_text(block["prompt"])}</p>'
            path_html = []
            for path in block.get("paths") or []:
                label = math_text(path.get("label") or "Path")
                voice = path.get("voice")
                voice_html = (
                    f'<p class="path-voice">{math_text(voice)}</p>' if voice else ""
                )
                inner = render_blocks(
                    path.get("blocks") or [], lesson_dir=lesson_dir, sets=sets, spec=spec
                )
                path_html.append(
                    f'<div class="path"><h3 class="path-label">{label}</h3>'
                    f"{voice_html}{inner}</div>"
                )
            chunks.append(
                f'<div class="dual-path example is-worked">{intro}'
                f'<div class="path-grid">{"".join(path_html)}</div></div>'
            )
        elif kind == "support-fade":
            title = math_text(block.get("title") or "Check → Practise → Extend")
            process = block.get("process_name")
            process_html = (
                f'<p class="support-fade-process">{math_text(process)}</p>' if process else ""
            )
            stages = block.get("stages") or {}
            chips = []
            for key, label in (
                ("worked", "Check / worked"),
                ("practise", "Practise"),
                ("extend", "Extend"),
            ):
                if stages.get(key):
                    chips.append(
                        f'<li><span class="fade-stage">{label}</span> '
                        f"{math_text(stages[key])}</li>"
                    )
            chunks.append(
                f'<aside class="support-fade" aria-label="Practice support">'
                f"<p class=\"chrome-label\">{title}</p>{process_html}"
                f'<ol class="fade-stages">{"".join(chips)}</ol></aside>'
            )
    return "\n".join(chunks)


def _practice_set_html(block: dict, sets: dict) -> str:
    set_id = block["set_id"]
    visible = int(block.get("visible_count") or 1)
    items = []
    for group in sets.get("sets") or []:
        if group.get("id") == set_id:
            items = group.get("items") or []
            break
    if not items:
        return ""
    shown = items[:visible]
    rest = items[visible:]
    parts = ['<div class="practice">']
    for item in shown:
        parts.append(f'<div class="practice-item"><p>{math_text(item["prompt"])}</p></div>')
    if rest:
        parts.append("<details><summary>Try another</summary>")
        for item in rest:
            parts.append(f'<div class="practice-item"><p>{math_text(item["prompt"])}</p></div>')
        parts.append("</details>")
    parts.append("</div>")
    return "\n".join(parts)


def _interactive_html(spec_id: str, spec: dict) -> str:
    start = spec["board"]["starting_parameters"]
    params = start
    extra = ""
    if spec_id == "predict-h":
        extra = (
            '<label>Predicted vertex x '
            '<input data-predicted-x type="number" step="0.1"></label>'
        )
    if spec_id == "expand-same-graph":
        extra = (
            '<div class="expand-inputs">'
            '<label>a <input data-standard="a" type="number" step="any" value="2"></label>'
            '<label>b <input data-standard="b" type="number" step="any"></label>'
            '<label>c <input data-standard="c" type="number" step="any"></label>'
            "</div>"
        )
    if spec_id == "fresh-case":
        extra = (
            '<div class="named-reading">'
            '<label>Vertex x <input data-named="x" type="number" step="0.1"></label>'
            '<label>Vertex y <input data-named="y" type="number" step="any"></label>'
            '<label>Axis <input data-named="axis" type="text" placeholder="x = …"></label>'
            '<label>Maximum or minimum <input data-named="extreme" type="text"></label>'
            "</div>"
        )
    hide_std = True
    std_class = ' class="live-eq" hidden' if hide_std else ' class="live-eq"'
    box_id = f"board-{spec_id}"
    return f"""
<div class="cycle board-wrap" data-spec-id="{escape(spec_id)}" data-start-a="{params["a"]}" data-start-h="{params["h"]}" data-start-k="{params["k"]}">
  {extra}
  <p class="live-eq"><strong>Vertex form:</strong> <span data-eq></span></p>
  <p{std_class} data-standard-line><strong>Same function, expanded:</strong> <span data-std></span></p>
  <p class="live-eq"><strong>Axis of symmetry:</strong> <span data-axis></span></p>
  <figure class="board-figure">
    <div id="{box_id}" class="jxgbox" role="img" aria-label="Interactive parabola in vertex form"></div>
  </figure>
  <div class="controls">
    <label>a <input data-slider="a" type="range" min="-3" max="3" step="0.1" value="{params["a"]}"><span data-val="a"></span></label>
    <label>h <input data-slider="h" type="range" min="-5" max="5" step="0.1" value="{params["h"]}"><span data-val="h"></span></label>
    <label>k <input data-slider="k" type="range" min="-5" max="8" step="0.1" value="{params["k"]}"><span data-val="k"></span></label>
  </div>
  <div class="cycle-actions">
    <button type="button" class="btn-primary" data-action="submit">Check</button>
    <button type="button" data-action="hint">Hint</button>
    <button type="button" data-action="reset">Reset</button>
  </div>
  <p class="feedback" hidden></p>
  <p class="hint" hidden></p>
  <h3>Shared points</h3>
  <table class="data" data-table></table>
</div>
"""


def build_lesson(lesson_dir: Path, out_dir: Path) -> Path:
    """Write HTML from student-content.json and copy bundled assets."""
    student = json.loads((lesson_dir / "student-content.json").read_text(encoding="utf-8"))
    assert_student_content(student)
    spec = json.loads((lesson_dir / "interaction-spec.json").read_text(encoding="utf-8"))
    feedback_msgs = json.loads((lesson_dir / "student-feedback.json").read_text(encoding="utf-8"))
    sets = {}
    sets_path = lesson_dir / "student-practice-sets.json"
    if sets_path.is_file():
        sets = json.loads(sets_path.read_text(encoding="utf-8"))

    out_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "jsxgraph/jsxgraphcore.js",
        "jsxgraph/jsxgraph.css",
        "jsxgraph/LICENSE.MIT",
        "vertex-form-board.js",
        "vertex-form-checker.js",
        "formative-cycle.js",
        "design-tokens.css",
        "student.css",
    ):
        src = COMPONENTS / name
        dest = out_dir / Path(name).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    media_src = lesson_dir / "media"
    if media_src.is_dir():
        dest_media = out_dir / "media"
        if dest_media.exists():
            shutil.rmtree(dest_media)
        shutil.copytree(media_src, dest_media)

    panels = []
    tabs = []
    for i, part in enumerate(student["parts"]):
        pid = part["id"]
        selected = "true" if i == 0 else "false"
        hidden = "" if i == 0 else " hidden"
        active = " active" if i == 0 else ""
        tabs.append(
            f'<button type="button" role="tab" id="tab-{escape(pid)}" '
            f'aria-controls="panel-{escape(pid)}" aria-selected="{selected}">'
            f'{escape(part["nav_label"])}</button>'
        )
        body = render_blocks(part["blocks"], lesson_dir=lesson_dir, sets=sets, spec=spec)
        panels.append(
            f'<section class="panel{active}" id="panel-{escape(pid)}" '
            f'role="tabpanel" aria-labelledby="tab-{escape(pid)}"{hidden}>'
            f'<div class="prose">{body}</div></section>'
        )

    credits = "".join(f"<p>{math_text(c)}</p>" for c in student.get("credits") or [])
    messages_json = json.dumps(feedback_msgs.get("messages") or {}, ensure_ascii=False)
    start = spec["board"]["starting_parameters"]
    fresh = spec["board"]["fresh_case_parameters"]
    hints = {
        "match-plus-inside": [
            "hint-match-1-live-equation",
            "hint-match-2-brackets-to-h",
            "hint-match-3-vertex-marker",
        ],
        "predict-h": [
            "hint-predict-h-1-name-x-first",
            "hint-predict-h-2-watch-marker",
            "hint-predict-h-3-equation-after-move",
        ],
        "expand-same-graph": [
            "hint-expand-1-square-the-binomial",
            "hint-expand-2-distribute-a",
            "hint-expand-3-add-k",
        ],
        "fresh-case": [
            "hint-fresh-1-match-brackets",
            "hint-fresh-2-axis-vertical",
            "hint-fresh-3-sign-of-a",
        ],
    }

    goal_bits = []
    if student.get("goal"):
        goal_bits.append(
            f'<p class="lesson-goal"><span class="chrome-label">Goal</span> '
            f'{math_text(student["goal"])}</p>'
        )
    if student.get("you_will_need"):
        goal_bits.append(
            f'<p class="you-will-need"><span class="chrome-label">You will need</span> '
            f'{math_text(student["you_will_need"])}</p>'
        )
    goal_meta = f'<div class="lesson-meta">{"".join(goal_bits)}</div>' if goal_bits else ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(student["title"])}</title>
  <link rel="stylesheet" href="jsxgraph.css">
  <link rel="stylesheet" href="student.css">
</head>
<body>
  <article class="lesson">
    <h1>{escape(student["title"])}</h1>
    {goal_meta}
    <div class="tabs" role="tablist">
      {"".join(tabs)}
    </div>
    {"".join(panels)}
    <div class="credits">{credits}</div>
  </article>
  <script src="jsxgraphcore.js"></script>
  <script src="vertex-form-board.js"></script>
  <script src="vertex-form-checker.js"></script>
  <script src="formative-cycle.js"></script>
  <script>
    (function () {{
      var messages = {messages_json};
      var hints = {json.dumps(hints)};
      var start = {json.dumps(start)};
      var fresh = {json.dumps(fresh)};
      var tabs = document.querySelectorAll('[role="tab"]');
      tabs.forEach(function (tab) {{
        tab.addEventListener("click", function () {{
          tabs.forEach(function (t) {{ t.setAttribute("aria-selected", "false"); }});
          tab.setAttribute("aria-selected", "true");
          document.querySelectorAll('[role="tabpanel"]').forEach(function (panel) {{
            var on = panel.getAttribute("aria-labelledby") === tab.id;
            panel.classList.toggle("active", on);
            panel.hidden = !on;
          }});
          document.querySelectorAll(".cycle").forEach(function (root) {{
            if (!panelHidden(root) && root._vfApi && root._vfApi.resize) {{
              root._vfApi.resize();
            }}
          }});
        }});
      }});
      function panelHidden(el) {{
        var panel = el.closest('[role="tabpanel"]');
        return panel && panel.hidden;
      }}
      document.querySelectorAll(".cycle").forEach(function (root) {{
        var specId = root.getAttribute("data-spec-id");
        var params = start;
        var box = root.querySelector(".jxgbox");
        var eq = root.querySelector("[data-eq]");
        var std = root.querySelector("[data-std]");
        var axis = root.querySelector("[data-axis]");
        var table = root.querySelector("[data-table]");
        if (eq && !eq.id) {{ eq.id = specId + "-eq"; }}
        if (std && !std.id) {{ std.id = specId + "-std"; }}
        if (axis && !axis.id) {{ axis.id = specId + "-axis"; }}
        if (table && !table.id) {{ table.id = specId + "-table"; }}
        var api = initVertexFormBoard(box.id, {{
          starting: params,
          fresh: fresh,
          equationId: eq && eq.id,
          standardId: std && std.id,
          axisId: axis && axis.id,
          tableId: table && table.id
        }});
        root._vfApi = api;
        bindFormativeCycle(root, {{
          messages: messages,
          api: api,
          start: params,
          hints: hints[specId] || []
        }});
      }});
    }})();
  </script>
</body>
</html>
"""
    # json import used above
    index = out_dir / "index.html"
    index.write_text(re.sub(r"\n{3,}", "\n\n", html), encoding="utf-8")
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
