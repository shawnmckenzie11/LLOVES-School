#!/usr/bin/env python3
"""Compile static student HTML from student-content.json only. No LMS.

Compile inputs: student-content.json, plus optional interaction-spec.json,
student-feedback.json, and student-practice-sets.json. The bank is
catalogue/pools/ plus lesson assembly.json. Retired ranking files are
not compile inputs.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "components"
REGISTRY_PATH = ROOT / "catalogue" / "components" / "registry.json"

from student_fields import assert_student_content  # noqa: E402

CARD_ICONS = {
    "explore": (
        '<svg class="card-icon" viewBox="0 0 24 24" width="20" height="20" '
        'aria-hidden="true" focusable="false">'
        '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/>'
        '<circle cx="12" cy="12" r="3"/></svg>'
    ),
    "worked-example": (
        '<svg class="card-icon" viewBox="0 0 24 24" width="20" height="20" '
        'aria-hidden="true" focusable="false">'
        '<path d="M6 4h9l3 3v13H6z" fill="none" stroke="currentColor" stroke-width="2"/>'
        '<path d="M9 10h6M9 14h6" fill="none" stroke="currentColor" stroke-width="2"/></svg>'
    ),
    "try": (
        '<svg class="card-icon" viewBox="0 0 24 24" width="20" height="20" '
        'aria-hidden="true" focusable="false">'
        '<path d="M4 20l1-6 9-9 4 4-9 9-6 2z" fill="none" stroke="currentColor" stroke-width="2"/>'
        "</svg>"
    ),
    "discuss": (
        '<svg class="card-icon" viewBox="0 0 24 24" width="20" height="20" '
        'aria-hidden="true" focusable="false">'
        '<path d="M5 6h9a3 3 0 013 3v4a3 3 0 01-3 3H9l-4 3V6z" fill="none" '
        'stroke="currentColor" stroke-width="2"/></svg>'
    ),
    "check": (
        '<svg class="card-icon" viewBox="0 0 24 24" width="20" height="20" '
        'aria-hidden="true" focusable="false">'
        '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/>'
        '<path d="M8 12l2.5 2.5L16 9" fill="none" stroke="currentColor" stroke-width="2"/></svg>'
    ),
    "optional-remark": (
        '<svg class="card-icon" viewBox="0 0 24 24" width="20" height="20" '
        'aria-hidden="true" focusable="false">'
        '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/>'
        '<path d="M12 10v6M12 7h.01" fill="none" stroke="currentColor" stroke-width="2"/></svg>'
    ),
}


def math_text(text: str) -> str:
    """Local accessible substitutions for common TeX (no CDN)."""
    t = text or ""
    t = t.replace("\\(", "").replace("\\)", "").replace("\\[", "").replace("\\]", "")
    t = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)", t)
    t = t.replace("^{2}", "²").replace("^2", "²")
    t = t.replace("^{3}", "³").replace("^3", "³")
    t = t.replace("\\times", "×").replace("\\pm", "±").replace("\\neq", "≠")
    t = t.replace("\\leq", "≤").replace("\\geq", "≥").replace("\\cdot", "·")
    t = t.replace("\\,", " ").replace("\\ ", " ")
    return escape(t)


def load_json(path: Path) -> dict:
    """Load a JSON object."""
    return json.loads(path.read_text(encoding="utf-8"))


def load_registry() -> dict:
    """Load the typed component registry, or an empty registry."""
    if not REGISTRY_PATH.is_file():
        return {"components": {}, "tasks": {}, "always_copy": ["design-tokens.css", "student.css"]}
    return load_json(REGISTRY_PATH)


def objects_by_id(student: dict) -> dict:
    """Index lesson objects by id."""
    return {item["id"]: item for item in student.get("objects") or [] if item.get("id")}


def attr(block: dict, extra: str = "") -> str:
    """data-block-id plus optional object id."""
    bid = escape(str(block.get("id") or ""))
    oid = block.get("object_id")
    bits = [f'data-block-id="{bid}"'] if bid else []
    if oid:
        bits.append(f'data-object-id="{escape(str(oid))}"')
    if extra:
        bits.append(extra)
    return (" " + " ".join(bits)) if bits else ""


def render_blocks(
    blocks: list,
    *,
    lesson_dir: Path,
    sets: dict,
    spec: dict,
    objects: dict,
    registry: dict,
) -> str:
    """Render permitted student blocks to HTML."""
    chunks: list[str] = []
    for block in blocks:
        kind = block["type"]
        if kind == "p":
            chunks.append(f"<p{attr(block)}>{math_text(block['text'])}</p>")
        elif kind == "h2":
            chunks.append(f"<h2{attr(block)}>{math_text(block['text'])}</h2>")
        elif kind == "equation":
            chunks.append(f'<p class="equation"{attr(block)}>{math_text(block["text"])}</p>')
        elif kind == "task":
            chunks.append(f'<div class="task"{attr(block)}><p>{math_text(block["prompt"])}</p></div>')
        elif kind == "figure":
            src = escape(block["src"])
            alt = escape(block["alt"])
            chunks.append(
                f'<figure class="figure"{attr(block)}><img src="{src}" alt="{alt}"></figure>'
            )
        elif kind == "example":
            inner = render_blocks(
                block["blocks"],
                lesson_dir=lesson_dir,
                sets=sets,
                spec=spec,
                objects=objects,
                registry=registry,
            )
            stage = escape(block.get("stage") or "worked")
            chunks.append(f'<div class="example is-{stage}"{attr(block)}>{inner}</div>')
        elif kind == "self-check":
            chunks.append(
                f'<div class="self-check"{attr(block)}><p>{math_text(block["prompt"])}</p></div>'
            )
        elif kind == "practice-set":
            chunks.append(_practice_set_html(block, sets))
        elif kind == "interactive":
            chunks.append(_interactive_html(block, spec, objects, registry))
        elif kind == "card":
            chunks.append(
                _card_html(
                    block,
                    lesson_dir=lesson_dir,
                    sets=sets,
                    spec=spec,
                    objects=objects,
                    registry=registry,
                )
            )
    return "\n".join(chunks)


def _card_html(
    block: dict,
    *,
    lesson_dir: Path,
    sets: dict,
    spec: dict,
    objects: dict,
    registry: dict,
) -> str:
    kind = block.get("kind") or "explore"
    label = math_text(block.get("label") or "")
    icon = CARD_ICONS.get(kind, CARD_ICONS["explore"])
    inner = render_blocks(
        block.get("blocks") or [],
        lesson_dir=lesson_dir,
        sets=sets,
        spec=spec,
        objects=objects,
        registry=registry,
    )
    oids = " ".join(block.get("object_ids") or [])
    extra = f'data-card="{escape(kind)}"'
    if oids:
        extra += f' data-object-ids="{escape(oids)}"'
    heading = f'<h3 class="card-label">{icon}<span>{label}</span></h3>'
    if kind == "optional-remark":
        return (
            f'<details class="card-block is-optional-remark"{attr(block, extra)}>'
            f"<summary>{icon}<span>{label}</span></summary>{inner}</details>"
        )
    return (
        f'<section class="card-block is-{escape(kind)}"{attr(block, extra)}>'
        f"{heading}{inner}</section>"
    )


def _practice_item_html(item: dict) -> str:
    """One assembled practice-set prompt. Pool id is a data attribute, not student copy."""
    iid = escape(str(item.get("id") or ""))
    extra = f' data-item-id="{iid}"' if iid else ""
    return f'<div class="practice-item"{extra}><p>{math_text(item["prompt"])}</p></div>'


def _practice_set_html(block: dict, sets: dict) -> str:
    """Render every item in the assembled set: visible_count, then extras under One more to try."""
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
    extra = f'data-set-id="{escape(str(set_id))}"'
    parts = [f'<div class="practice"{attr(block, extra)}>']
    for item in shown:
        parts.append(_practice_item_html(item))
    if rest:
        parts.append("<details><summary>One more to try</summary>")
        for item in rest:
            parts.append(_practice_item_html(item))
        parts.append("</details>")
    parts.append("</div>")
    return "\n".join(parts)


def _interactive_html(block: dict, spec: dict, objects: dict, registry: dict) -> str:
    spec_id = block["spec_id"]
    task = (registry.get("tasks") or {}).get(spec_id) or {}
    component = (registry.get("components") or {}).get(task.get("component") or "")
    fallback = ""
    oid = block.get("object_id")
    if oid and oid in objects:
        fallback = objects[oid].get("fallback") or ""
    elif component:
        fallback = component.get("unavailable_fallback") or ""
    if not spec.get("board"):
        msg = math_text(
            fallback or "This tool is not available. Work on paper from the labelled equation."
        )
        return f'<div class="media is-unavailable"{attr(block)}><p>{msg}</p></div>'
    start = spec["board"]["starting_parameters"]
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
    fb = math_text(fallback) if fallback else "Sketch the parabola on paper from the labelled vertex-form equation."
    return f"""
<div class="cycle board-wrap" data-spec-id="{escape(spec_id)}"{attr(block)} data-feedback-state="initial" data-start-a="{start["a"]}" data-start-h="{start["h"]}" data-start-k="{start["k"]}">
  {extra}
  <p class="live-eq"><strong>Vertex form:</strong> <span data-eq></span></p>
  <p{std_class} data-standard-line><strong>Same function, expanded:</strong> <span data-std></span></p>
  <p class="live-eq"><strong>Axis of symmetry:</strong> <span data-axis></span></p>
  <figure class="board-figure">
    <div id="{box_id}" class="jxgbox" role="img" aria-label="Interactive parabola in vertex form"></div>
  </figure>
  <div class="controls">
    <label>a <input data-slider="a" type="range" min="-3" max="3" step="0.1" value="{start["a"]}"><span data-val="a"></span></label>
    <label>h <input data-slider="h" type="range" min="-5" max="5" step="0.1" value="{start["h"]}"><span data-val="h"></span></label>
    <label>k <input data-slider="k" type="range" min="-5" max="8" step="0.1" value="{start["k"]}"><span data-val="k"></span></label>
  </div>
  <div class="cycle-actions">
    <button type="button" class="btn-primary" data-action="submit">Check</button>
    <button type="button" data-action="hint">Hint</button>
    <button type="button" data-action="reset">Reset</button>
  </div>
  <p class="fallback" hidden>{fb}</p>
  <p class="feedback" hidden></p>
  <p class="hint" hidden></p>
  <h3>Shared points</h3>
  <table class="data" data-table></table>
</div>
"""


def collect_spec_ids(blocks: list) -> set[str]:
    """Collect interactive spec ids, including nested cards."""
    found: set[str] = set()
    for block in blocks:
        if block.get("type") == "interactive":
            found.add(block["spec_id"])
        if block.get("type") in {"card", "example"}:
            found |= collect_spec_ids(block.get("blocks") or [])
    return found


def assets_for_lesson(student: dict, registry: dict) -> list[str]:
    """Return component filenames to copy next to the compiled HTML."""
    names = list(registry.get("always_copy") or ["design-tokens.css", "student.css"])
    spec_ids: set[str] = set()
    for part in student.get("parts") or []:
        spec_ids |= collect_spec_ids(part.get("blocks") or [])
    tasks = registry.get("tasks") or {}
    components = registry.get("components") or {}
    for sid in spec_ids:
        task = tasks.get(sid)
        if not task:
            continue
        component = components.get(task.get("component") or "")
        if not component:
            continue
        for asset in component.get("assets") or []:
            names.append(asset)
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def lesson_uses_vertex_board(student: dict) -> bool:
    """True when this lesson embeds a vertex-form JSXGraph task."""
    spec_ids: set[str] = set()
    for part in student.get("parts") or []:
        spec_ids |= collect_spec_ids(part.get("blocks") or [])
    return bool(spec_ids & {"match-plus-inside", "predict-h", "expand-same-graph", "fresh-case"})


def copy_named_asset(components: Path, out_dir: Path, name: str) -> None:
    """Copy one registry asset into the lesson build folder."""
    src = components / name
    dest = out_dir / Path(name).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def build_lesson(
    lesson_dir: Path,
    out_dir: Path,
    *,
    components: Path | None = None,
) -> Path:
    """Write HTML from student-content.json and copy bundled assets."""
    components = components or COMPONENTS
    student = load_json(lesson_dir / "student-content.json")
    assert_student_content(student)
    spec_path = lesson_dir / "interaction-spec.json"
    spec = load_json(spec_path) if spec_path.is_file() else {}
    feedback_path = lesson_dir / "student-feedback.json"
    feedback_msgs = load_json(feedback_path) if feedback_path.is_file() else {"messages": {}}
    sets: dict = {}
    sets_path = lesson_dir / "student-practice-sets.json"
    if sets_path.is_file():
        sets = load_json(sets_path)
    registry = load_registry()
    objects = objects_by_id(student)

    out_dir.mkdir(parents=True, exist_ok=True)
    for name in assets_for_lesson(student, registry):
        src = components / name
        if src.is_file():
            copy_named_asset(components, out_dir, name)

    media_src = lesson_dir / "media"
    if media_src.is_dir():
        dest_media = out_dir / "media"
        if dest_media.exists():
            shutil.rmtree(dest_media)
        shutil.copytree(media_src, dest_media)

    objects_map = objects_by_id(student)
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
        body = render_blocks(
            part["blocks"],
            lesson_dir=lesson_dir,
            sets=sets,
            spec=spec,
            objects=objects_map,
            registry=registry,
        )
        panels.append(
            f'<section class="panel{active}" id="panel-{escape(pid)}" '
            f'role="tabpanel" aria-labelledby="tab-{escape(pid)}"{hidden}>'
            f'<div class="prose">{body}</div></section>'
        )

    credits = "".join(f"<p>{math_text(c)}</p>" for c in student.get("credits") or [])
    messages_json = json.dumps(feedback_msgs.get("messages") or {}, ensure_ascii=False)
    start = (spec.get("board") or {}).get("starting_parameters") or {}
    fresh = (spec.get("board") or {}).get("fresh_case_parameters") or {}
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

    scripts = ""
    if lesson_uses_vertex_board(student):
        scripts = """
  <script src="jsxgraphcore.js"></script>
  <script src="vertex-form-board.js"></script>
  <script src="vertex-form-checker.js"></script>
  <script src="formative-cycle.js"></script>
  <script>
    (function () {
      var messages = """ + messages_json + """;
      var hints = """ + json.dumps(hints) + """;
      var start = """ + json.dumps(start) + """;
      var fresh = """ + json.dumps(fresh) + """;
      var tabs = document.querySelectorAll('[role="tab"]');
      tabs.forEach(function (tab) {
        tab.addEventListener("click", function () {
          tabs.forEach(function (t) { t.setAttribute("aria-selected", "false"); });
          tab.setAttribute("aria-selected", "true");
          document.querySelectorAll('[role="tabpanel"]').forEach(function (panel) {
            var on = panel.getAttribute("aria-labelledby") === tab.id;
            panel.classList.toggle("active", on);
            panel.hidden = !on;
          });
          document.querySelectorAll(".cycle").forEach(function (root) {
            if (!panelHidden(root) && root._vfApi && root._vfApi.resize) {
              root._vfApi.resize();
            }
          });
        });
      });
      function panelHidden(el) {
        var panel = el.closest('[role="tabpanel"]');
        return panel && panel.hidden;
      }
      document.querySelectorAll(".cycle").forEach(function (root) {
        var specId = root.getAttribute("data-spec-id");
        var params = start;
        var box = root.querySelector(".jxgbox");
        var eq = root.querySelector("[data-eq]");
        var std = root.querySelector("[data-std]");
        var axis = root.querySelector("[data-axis]");
        var table = root.querySelector("[data-table]");
        if (!box || typeof initVertexFormBoard !== "function") return;
        if (eq && !eq.id) { eq.id = specId + "-eq"; }
        if (std && !std.id) { std.id = specId + "-std"; }
        if (axis && !axis.id) { axis.id = specId + "-axis"; }
        if (table && !table.id) { table.id = specId + "-table"; }
        var api = initVertexFormBoard(box.id, {
          starting: params,
          fresh: fresh,
          equationId: eq && eq.id,
          standardId: std && std.id,
          axisId: axis && axis.id,
          tableId: table && table.id
        });
        root._vfApi = api;
        bindFormativeCycle(root, {
          messages: messages,
          api: api,
          start: params,
          hints: hints[specId] || []
        });
      });
    })();
  </script>
"""
    else:
        scripts = """
  <script>
    (function () {
      var tabs = document.querySelectorAll('[role="tab"]');
      tabs.forEach(function (tab) {
        tab.addEventListener("click", function () {
          tabs.forEach(function (t) { t.setAttribute("aria-selected", "false"); });
          tab.setAttribute("aria-selected", "true");
          document.querySelectorAll('[role="tabpanel"]').forEach(function (panel) {
            var on = panel.getAttribute("aria-labelledby") === tab.id;
            panel.classList.toggle("active", on);
            panel.hidden = !on;
          });
        });
      });
    })();
  </script>
"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(student["title"])}</title>
  <link rel="stylesheet" href="student.css">
</head>
<body>
  <article class="lesson">
    <h1>{escape(student["title"])}</h1>
    <div class="tabs" role="tablist">
      {"".join(tabs)}
    </div>
    {"".join(panels)}
    <div class="credits">{credits}</div>
  </article>
{scripts}
</body>
</html>
"""
    if lesson_uses_vertex_board(student):
        html = html.replace(
            '<link rel="stylesheet" href="student.css">',
            '<link rel="stylesheet" href="jsxgraph.css">\n  <link rel="stylesheet" href="student.css">',
        )
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
