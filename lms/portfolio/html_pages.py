"""HTML (toggle) and Google-Doc-oriented two-column HTML for portfolios."""

from __future__ import annotations

from html import escape
from typing import Any

from portfolio.store import load_core_questions

_CSS = """
:root{--ink:#172033;--muted:#647084;--p:#5a48d6;--t:#0b8275;--line:#d9dde6;--bg:#f7f8fb;--card:#fff;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
.wrap{max-width:920px;margin:auto;padding:24px}
.switch button{border:1px solid var(--line);background:#fff;border-radius:999px;padding:9px 13px;font-weight:750;color:var(--muted);cursor:pointer}
.switch button.active{background:var(--p);color:#fff;border-color:var(--p)}
.hero{display:flex;justify-content:space-between;gap:20px;align-items:end;padding:28px 0 16px}.eyebrow{font-size:.78rem;letter-spacing:.1em;color:var(--t);font-weight:900}
h1{font-size:clamp(2.2rem,7vw,4rem);line-height:.95;margin:.15em 0}.hero p{color:var(--muted);font-weight:650}.hero-note{font-weight:800;background:#eaf7f4;padding:14px 16px;border-radius:16px;min-width:190px}
.evidence-row{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.evidence-row span{border:1px dashed #b8bec9;background:#fff;padding:18px 10px;border-radius:12px;text-align:center;color:var(--muted);font-size:.9rem}
.intro{margin:16px 0 22px;color:var(--muted)}.qcard{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:18px;margin:14px 0;box-shadow:0 10px 30px #22305a0a}
.qtop{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.num{display:grid;place-items:center;width:29px;height:29px;border-radius:50%;background:#f1eeff;color:var(--p);font-weight:900}.verb{font-weight:900;letter-spacing:.07em;color:var(--p);font-size:.82rem}.switch{margin-left:auto;display:flex;gap:5px}
.switch button{font-size:.78rem;padding:7px 10px}.flip{position:relative;min-height:90px;margin-top:11px}.panel{position:absolute;inset:0;opacity:0;transform:translateY(5px);pointer-events:none;transition:.18s ease}
.panel.active{opacity:1;transform:none;pointer-events:auto}h2{font-size:1.25rem;line-height:1.3;margin:6px 0}.words,.evidence{font-size:.88rem;color:var(--muted);margin:6px 0}.evidence{color:var(--t)}
textarea{width:100%;min-height:145px;resize:vertical;border:1px solid var(--line);border-radius:13px;padding:12px;margin-top:9px;font:inherit}
.col-table{width:100%;border-collapse:collapse;margin-top:12px}
.col-table th{background:#f1eeff;color:var(--p);text-align:left;padding:10px;border:1px solid var(--line);font-size:.82rem;letter-spacing:.04em}
.col-table td{border:1px solid var(--line);padding:12px;vertical-align:top;width:50%}
.pill{display:inline-block;background:#eaf7f4;color:var(--t);font-weight:800;border-radius:999px;padding:4px 10px;font-size:.78rem}
@media(max-width:650px){.hero{align-items:start;flex-direction:column}.hero-note{min-width:0;width:100%}.evidence-row{grid-template-columns:1fr}.switch{width:100%;margin-left:0}.switch button{flex:1}.flip{min-height:125px}}
"""

_TOGGLE_JS = """
document.querySelectorAll('.qcard').forEach(card=>{
  card.querySelectorAll('.switch button').forEach(btn=>btn.addEventListener('click',()=>{
    card.querySelectorAll('.switch button').forEach(b=>b.classList.toggle('active',b===btn));
    card.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active',p.classList.contains(btn.dataset.view)));
  }));
});
"""

QUESTION_ORDER = ("connect", "justify", "transfer")


def _hero_note_html(text: str) -> str:
    """Turn newlines in the hero note into ``<br>`` tags."""
    parts = [escape(p) for p in str(text or "").split("\n")]
    return "<br>".join(parts)


def render_toggle_html(doc: dict[str, Any]) -> str:
    """Build a course-specific Module Portfolio page with the HTML toggle.

    Args:
        doc: Assembled portfolio document (exemplar or generated).

    Returns:
        Full HTML page.
    """
    cores = load_core_questions()
    code = escape(str(doc.get("course_code") or ""))
    title = escape(str(doc.get("title") or "Module Portfolio"))
    subtitle = str(doc.get("hero_subtitle") or "")
    cards = []
    questions = doc.get("questions") or {}
    for index, key in enumerate(QUESTION_ORDER, start=1):
        core = cores.get(key) or {}
        q = questions.get(key) or {}
        verb = escape(str(core.get("verb") or key.upper()))
        course_q = escape(str(core.get("course_question") or ""))
        lens = escape(str(q.get("lens") or ""))
        words = " · ".join(str(w) for w in (q.get("useful_words") or []) if str(w).strip())
        evidence = escape(str(q.get("evidence") or "Use your screenshot + one detail from your quick reflection."))
        cards.append(
            f"""
<section class="qcard" data-card="q{index}">
  <div class="qtop">
    <span class="num">{index}</span>
    <span class="verb">{verb}</span>
    <div class="switch" role="group" aria-label="Question view">
      <button class="active" data-view="course">Course question</button>
      <button data-view="lens">Module {int(doc.get('module_number') or 1)} lens</button>
    </div>
  </div>
  <div class="flip">
    <div class="panel course active"><h2>{course_q}</h2></div>
    <div class="panel lens"><h2>{lens}</h2></div>
  </div>
  <p class="words"><b>Useful words:</b> {escape(words)}</p>
  <p class="evidence">📎 {evidence}</p>
  <textarea aria-label="Your answer to question {index}" placeholder="Write your answer here…"></textarea>
</section>
"""
        )
    evidence_row = "".join(
        f"<span>{escape(str(item))}</span>"
        for item in (doc.get("evidence_placeholders") or [])
    )
    intro = str(doc.get("intro") or "")
    intro_html = f"<p class=\"intro\"><b>Start with the course question.</b> Tap <em>Module {int(doc.get('module_number') or 1)} lens</em> only when you want a more specific version.</p>"
    if "Start with the course question" not in intro:
        intro_html = f'<p class="intro">{escape(intro)}</p>' if intro else intro_html
    else:
        intro_html = (
            "<p class=\"intro\"><b>Start with the course question.</b> "
            f"Tap <em>Module {int(doc.get('module_number') or 1)} lens</em> only when you want a more specific version.</p>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
  <div class="hero">
    <div><span class="eyebrow">{code}</span><h1>{title}</h1><p>{escape(subtitle)}</p></div>
    <div class="hero-note">{_hero_note_html(str(doc.get("hero_note") or ""))}</div>
  </div>
  <div class="evidence-row">{evidence_row}</div>
  {intro_html}
  {''.join(cards)}
</div>
<script>{_TOGGLE_JS}</script></body></html>
"""


def render_doc_html(doc: dict[str, Any]) -> str:
    """Two-column Course | Lens HTML for Drive conversion (no JS toggle).

    Args:
        doc: Assembled portfolio document.

    Returns:
        HTML Google Drive can convert to a Doc.
    """
    cores = load_core_questions()
    code = escape(str(doc.get("course_code") or ""))
    title = escape(str(doc.get("title") or "Module Portfolio"))
    subtitle = escape(str(doc.get("hero_subtitle") or ""))
    module_n = int(doc.get("module_number") or 1)
    rows = []
    questions = doc.get("questions") or {}
    for index, key in enumerate(QUESTION_ORDER, start=1):
        core = cores.get(key) or {}
        q = questions.get(key) or {}
        verb = escape(str(core.get("verb") or key.upper()))
        course_q = escape(str(core.get("course_question") or ""))
        lens = escape(str(q.get("lens") or ""))
        words = " · ".join(str(w) for w in (q.get("useful_words") or []) if str(w).strip())
        evidence = escape(str(q.get("evidence") or ""))
        rows.append(
            f"""
<section class="qcard">
  <div class="qtop">
    <span class="num">{index}</span>
    <span class="verb">{verb}</span>
    <span class="pill">Question {index}</span>
  </div>
  <table class="col-table">
    <thead><tr><th>Course question</th><th>Module {module_n} lens</th></tr></thead>
    <tbody><tr><td><h2>{course_q}</h2></td><td><h2>{lens}</h2></td></tr></tbody>
  </table>
  <p class="words"><b>Useful words:</b> {escape(words)}</p>
  <p class="evidence">📎 {evidence}</p>
  <p><b>Answer</b></p>
  <p>________________________________</p>
  <p>________________________________</p>
  <p>________________________________</p>
</section>
"""
        )
    evidence_row = "".join(
        f"<span>{escape(str(item))}</span>"
        for item in (doc.get("evidence_placeholders") or [])
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{title}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
  <div class="hero">
    <div><span class="eyebrow">{code}</span><h1>{title}</h1><p>{subtitle}</p></div>
    <div class="hero-note">{_hero_note_html(str(doc.get("hero_note") or ""))}</div>
  </div>
  <div class="evidence-row">{evidence_row}</div>
  <p class="intro"><b>Start with the course question</b> in the left column. The right column is the Module {module_n} lens.</p>
  {''.join(rows)}
</div></body></html>
"""
