"""MCK-59: end-of-class engagement summary from ``engagement_rollup`` rows."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from engagement_rubric import (
    apply_evidence_floor,
    format_engagement_level,
    level_for_score,
    load_rubric,
    weighted_score,
)
from engagement_rollup import SIGNALS


def _cells_for_student(rows: Sequence[Mapping[str, Any]], student_id: int) -> dict[str, dict[str, Any]]:
    """Index student-scope rollup rows by signal."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if str(row.get("scope") or "") != "student":
            continue
        if int(row.get("student_id") or 0) != int(student_id):
            continue
        out[str(row.get("signal") or "")] = dict(row)
    return out


def _group_cells(rows: Sequence[Mapping[str, Any]], team_id: int) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if str(row.get("scope") or "") != "group":
            continue
        if int(row.get("team_id") or 0) != int(team_id):
            continue
        out[str(row.get("signal") or "")] = dict(row)
    return out


def _reason_line(cells: dict[str, dict[str, Any]], level: int | None, evidence: int, rubric: dict[str, Any]) -> str:
    """One-line teacher reason for the suggested engagement level."""
    floor = int(rubric.get("evidence_floor") or 0)
    if floor > 0 and evidence < floor:
        return (
            f"Only {evidence} logged signal(s) — below the evidence floor ({floor}); "
            "treat as quiet while present."
        )
    highlights: list[str] = []
    ind = cells.get("individual_answer")
    if ind and ind.get("offered") not in (None, 0):
        highlights.append(
            f"{int(ind.get('count') or 0)}/{int(ind['offered'])} individual while present"
        )
    for sig, label in (
        ("group_vote", "group votes"),
        ("group_draft", "draft edits"),
        ("group_submit", "group submits"),
    ):
        row = cells.get(sig)
        if not row or int(row.get("count") or 0) <= 0:
            continue
        share = row.get("share_of_group")
        if share is not None:
            highlights.append(f"{int(100 * float(share))}% of team {label}")
        else:
            highlights.append(f"{int(row.get('count') or 0)} {label}")
    if not highlights:
        return "No logged digital evidence this run; verbal or paper work may still count."
    if level is not None and level >= 3:
        return "; ".join(highlights[:2]) + "."
    return "; ".join(highlights[:2]) + " — room to grow engagement."


def evidence_counts(cells: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact evidence list for the UI."""
    out: list[dict[str, Any]] = []
    for signal in SIGNALS:
        row = cells.get(signal)
        if not row:
            continue
        count = int(row.get("count") or 0)
        if count <= 0 and row.get("offered") in (None, 0):
            continue
        out.append(
            {
                "signal": signal,
                "count": count,
                "offered": row.get("offered"),
                "share_of_group": row.get("share_of_group"),
            }
        )
    return out


def suggest_student(
    rows: Sequence[Mapping[str, Any]],
    student_id: int,
    *,
    rubric: dict[str, Any] | None = None,
    override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Suggested engagement level + reason for one student."""
    cfg = rubric or load_rubric()
    cells = _cells_for_student(rows, student_id)
    score, evidence = weighted_score(cells, cfg)
    raw_level = level_for_score(score, cfg)
    suggested = apply_evidence_floor(raw_level, evidence, cfg)
    payload: dict[str, Any] = {
        "student_id": int(student_id),
        "suggested_level": suggested,
        "suggested_label": format_engagement_level(suggested, cfg),
        "reason": _reason_line(cells, suggested, evidence, cfg),
        "evidence": evidence_counts(cells),
        "score": round(score, 3) if score is not None else None,
    }
    if override:
        payload["override"] = dict(override)
        eff = override.get("level")
        if eff is not None:
            payload["effective_label"] = format_engagement_level(
                int(eff), cfg, set_by_teacher=True
            )
    return payload


def build_summary(
    *,
    rows: Sequence[Mapping[str, Any]],
    roster: Sequence[Mapping[str, Any]],
    team_for_student: Mapping[int, int | None],
    team_names: Mapping[int, str],
    run_key: str,
    live_label: str,
    overrides: Mapping[int, Mapping[str, Any]] | None = None,
    rubric: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble group blocks and per-student lines for one run."""
    cfg = rubric or load_rubric()
    overrides = overrides or {}
    by_team: dict[int | None, list[dict[str, Any]]] = {}
    for person in roster:
        sid = int(person["id"])
        tid = team_for_student.get(sid)
        tid_key: int | None = int(tid) if tid not in (None, "") else None
        student_row = suggest_student(
            rows,
            sid,
            rubric=cfg,
            override=overrides.get(sid),
        )
        student_row["codename"] = str(person.get("codename") or person.get("name") or "Student")
        by_team.setdefault(tid_key, []).append(student_row)

    groups: list[dict[str, Any]] = []
    for tid in sorted((k for k in by_team if k is not None), key=lambda x: int(x)):
        groups.append(
            {
                "team_id": int(tid),
                "team_name": str(team_names.get(int(tid)) or f"Group {tid}"),
                "group_evidence": evidence_counts(_group_cells(rows, int(tid))),
                "students": sorted(by_team[int(tid)], key=lambda s: str(s.get("codename") or "")),
            }
        )
    ungrouped = sorted(by_team.get(None) or [], key=lambda s: str(s.get("codename") or ""))
    return {
        "run_key": str(run_key),
        "live_label": live_label,
        "level_kind": str(cfg.get("level_kind") or "engagement"),
        "rubric": {
            "level_label": cfg.get("level_label"),
            "rules": list(cfg.get("rules") or []),
            "evidence_floor": cfg.get("evidence_floor"),
        },
        "blind_spots": list(cfg.get("blind_spots") or []),
        "groups": groups,
        "ungrouped": ungrouped,
    }


KEY_ENGAGEMENT_SUMMARY_EMPTY = "engagement.summary_empty"
COPY_ENGAGEMENT_SUMMARY_EMPTY = (
    "No class runs yet. Your summary shows up here after your next class ends."
)
_ENGAGEMENT_COPY_FALLBACKS = {
    KEY_ENGAGEMENT_SUMMARY_EMPTY: COPY_ENGAGEMENT_SUMMARY_EMPTY,
}


def engagement_copy(key: str) -> dict[str, str]:
    """Return ``{key, text}`` for staff engagement UI strings."""
    return {"key": key, "text": _ENGAGEMENT_COPY_FALLBACKS.get(key, key)}
