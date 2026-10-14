"""MCK-215: live student engagement display from the current run event log."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from engagement_rollup import RollupRow, compute_rollup, _canonical_mode

# Publish modes that count as a team / group question for the live-class card.
GROUP_QUESTION_MODES = frozenset(
    {"group", "group_consensus", "group_submit", "group_shared"}
)


def group_question_started(events: Sequence[Mapping[str, Any]]) -> bool:
    """True when a group-mode item was offered in this run."""
    for row in events:
        if str(row.get("kind") or "") != "item.offered":
            continue
        payload = row.get("payload") or {}
        if isinstance(payload, str):
            continue
        mode = _canonical_mode(payload.get("mode"))
        if mode in GROUP_QUESTION_MODES:
            return True
    return False


def tier1_engagement_display(rows: Sequence[RollupRow], student_id: int) -> str:
    """Format Tier-1 individual tracking as ``count/offered`` (e.g. ``2/3``, ``0/0``)."""
    sid = int(student_id)
    for row in rows:
        if row.scope != "student" or int(row.student_id or 0) != sid:
            continue
        if row.signal != "individual_answer":
            continue
        count = int(row.count or 0)
        offered = row.offered
        if offered is None:
            return "0/0"
        return f"{count}/{int(offered)}"
    return "0/0"


def live_engagement_snapshot(
    events: Sequence[Mapping[str, Any]],
    *,
    class_id: int,
    live_session: int,
    run_key: str,
    student_id: int,
    team_for_student: Mapping[int, int | None],
    student_ids: Sequence[int],
) -> dict[str, Any]:
    """Student-facing engagement fields for one active live run."""
    rows = compute_rollup(
        events,
        class_id=int(class_id),
        live_session=int(live_session),
        run_key=str(run_key),
        team_for_student=team_for_student,
        student_ids=student_ids,
    )
    return {
        "tier1": tier1_engagement_display(rows, int(student_id)),
        "group_activity": group_question_started(events),
    }
