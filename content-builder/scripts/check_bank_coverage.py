#!/usr/bin/env python3
"""Report process_slot coverage for a module bank.

Compares each process_slots[].min_approved_items against counts of
student_html_allowed items tagged for that process with review_status in
{approved, locked} (and optionally selected).

Exit codes:
  0 — approved/locked coverage meets all mins (selected-only shortfalls warn)
  1 — approved/locked coverage below a min, or --strict-selected and selected
      pool (approved+locked+selected) still below a min
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANKS = ROOT / "catalogue" / "banks"

ALWAYS_EXCLUDE_SOURCES = frozenset({"nelson_functions_11"})
APPROVED_STATUSES = frozenset({"approved", "locked"})
SELECTED_STATUS = "selected"


def _load_json(path: Path) -> object:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _item_id(entry: object) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and isinstance(entry.get("id"), str):
        return entry["id"]
    return None


def _student_pool_item(item: dict) -> bool:
    if item.get("source") in ALWAYS_EXCLUDE_SOURCES:
        return False
    if item.get("permitted_use_status") == "excluded":
        return False
    if item.get("student_html_allowed") is not True:
        return False
    return True


def load_module_items(course: str, module: int) -> tuple[dict, list[dict]]:
    mod_path = BANKS / course / f"M{module}" / "module.json"
    if not mod_path.is_file():
        raise FileNotFoundError(f"module bank not found: {mod_path}")
    module_data = _load_json(mod_path)
    if not isinstance(module_data, dict):
        raise ValueError(f"{mod_path}: not an object")
    items_dir = mod_path.parent / "items"
    items: list[dict] = []
    for entry in module_data.get("items") or []:
        item_id = _item_id(entry)
        if not item_id:
            continue
        path = items_dir / f"{item_id}.json"
        if not path.is_file():
            print(f"WARNING: missing item file {path}", file=sys.stderr)
            continue
        item = _load_json(path)
        if isinstance(item, dict):
            items.append(item)
    return module_data, items


def coverage_report(
    module_data: dict,
    items: list[dict],
    *,
    count_selected: bool,
) -> tuple[list[dict], list[str], list[str]]:
    """Return (rows, approved_gaps, selected_gaps)."""
    slots = module_data.get("process_slots") or []
    approved_by: dict[str, list[str]] = defaultdict(list)
    selected_by: dict[str, list[str]] = defaultdict(list)

    for item in items:
        if not _student_pool_item(item):
            continue
        status = item.get("review_status")
        tags = item.get("process_tags") or []
        item_id = item.get("id") or "?"
        for tag in tags:
            if status in APPROVED_STATUSES:
                approved_by[tag].append(item_id)
            elif status == SELECTED_STATUS:
                selected_by[tag].append(item_id)

    rows: list[dict] = []
    approved_gaps: list[str] = []
    selected_gaps: list[str] = []

    for slot in slots:
        if not isinstance(slot, dict):
            continue
        slot_id = slot.get("id") or "?"
        minimum = int(slot.get("min_approved_items") or 0)
        approved_ids = approved_by.get(slot_id, [])
        selected_ids = selected_by.get(slot_id, [])
        approved_n = len(approved_ids)
        selected_n = len(selected_ids)
        pool_n = approved_n + selected_n
        approved_ok = approved_n >= minimum
        selected_pool_ok = pool_n >= minimum
        row = {
            "process_id": slot_id,
            "name": slot.get("name"),
            "min_approved_items": minimum,
            "approved_locked": approved_n,
            "selected": selected_n,
            "approved_locked_plus_selected": pool_n,
            "approved_ids": approved_ids,
            "selected_ids": selected_ids,
            "approved_met": approved_ok,
            "selected_pool_met": selected_pool_ok,
        }
        rows.append(row)
        if not approved_ok:
            approved_gaps.append(
                f"{slot_id}: approved/locked {approved_n} < min {minimum}"
            )
        if count_selected and not selected_pool_ok:
            selected_gaps.append(
                f"{slot_id}: approved+selected {pool_n} < min {minimum}"
            )
        elif not count_selected and not approved_ok and selected_n:
            # Soft note: selected could close the gap after human gate
            pass

    return rows, approved_gaps, selected_gaps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check module bank process_slot coverage vs min_approved_items."
    )
    parser.add_argument("--course", default="MCF3M")
    parser.add_argument("--module", type=int, default=4)
    parser.add_argument(
        "--include-selected",
        action="store_true",
        help="Also report selected counts (default: still shown; used for warnings).",
    )
    parser.add_argument(
        "--strict-selected",
        action="store_true",
        help="Exit non-zero if approved+locked+selected still below mins.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON summary on stdout.",
    )
    args = parser.parse_args(argv)

    try:
        module_data, items = load_module_items(args.course, args.module)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    rows, approved_gaps, selected_gaps = coverage_report(
        module_data,
        items,
        count_selected=True,
    )

    student_items = [i for i in items if _student_pool_item(i)]
    by_status: dict[str, int] = defaultdict(int)
    for i in student_items:
        by_status[str(i.get("review_status"))] += 1

    if args.json:
        payload = {
            "course": args.course,
            "module": args.module,
            "student_html_allowed_items": len(student_items),
            "by_review_status": dict(by_status),
            "slots": rows,
            "approved_gaps": approved_gaps,
            "selected_pool_gaps": selected_gaps,
        }
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print(f"bank {args.course}/M{args.module}")
        print(
            f"student_html_allowed items (excl. Nelson/excluded): "
            f"{len(student_items)}  by_status={dict(by_status)}"
        )
        print()
        header = (
            f"{'process_id':<28} {'min':>3} {'appr/lock':>9} "
            f"{'selected':>8} {'pool':>5}  status"
        )
        print(header)
        print("-" * len(header))
        for row in rows:
            if row["approved_met"]:
                status = "OK"
            elif row["selected_pool_met"]:
                status = "WARN (selected-only)"
            else:
                status = "GAP"
            print(
                f"{row['process_id']:<28} {row['min_approved_items']:>3} "
                f"{row['approved_locked']:>9} {row['selected']:>8} "
                f"{row['approved_locked_plus_selected']:>5}  {status}"
            )
            if row["approved_ids"]:
                print(f"  approved/locked: {', '.join(row['approved_ids'])}")
            if row["selected_ids"]:
                print(f"  selected: {', '.join(row['selected_ids'])}")

        print()
        if approved_gaps:
            print("Approved/locked coverage below mins:")
            for g in approved_gaps:
                print(f"  - {g}")
        else:
            print("Approved/locked coverage: all mins met.")

        soft_selected_only = [
            r
            for r in rows
            if (not r["approved_met"]) and r["selected_pool_met"]
        ]
        if soft_selected_only:
            print(
                "WARN: some slots meet mins only with selected items "
                "(awaiting Shawn’s selected→approved gate):"
            )
            for r in soft_selected_only:
                print(
                    f"  - {r['process_id']}: selected={r['selected']} "
                    f"(approved/locked={r['approved_locked']}, "
                    f"min={r['min_approved_items']})"
                )

        hard_gaps = [r for r in rows if not r["selected_pool_met"]]
        if hard_gaps:
            print("Hard gaps (even counting selected):")
            for r in hard_gaps:
                print(
                    f"  - {r['process_id']}: pool={r['approved_locked_plus_selected']} "
                    f"< min={r['min_approved_items']}"
                )

    sys.stdout.flush()
    # Exit policy
    selected_only_covers = any(
        (not r["approved_met"]) and r["selected_pool_met"] for r in rows
    )
    if approved_gaps:
        # Spec: approved coverage below mins → non-zero, UNLESS the shortfall
        # is selected-only (warn + exit 0). Hard fail only when even the
        # selected pool cannot meet a min, or when no selected cushion exists
        # and we treat approved gaps strictly.
        #
        # Standing rule from task: "Exit non-zero if approved coverage below
        # mins (selected-only should warn but exit 0 unless --strict-selected)".
        hard_approved_fail = any(not r["selected_pool_met"] for r in rows)
        if hard_approved_fail:
            print(
                "FAIL: coverage below mins even counting selected.",
                file=sys.stderr,
            )
            return 1
        # selected-only shortfalls: warn, exit 0 (unless --strict-selected)
        if args.strict_selected:
            print(
                "FAIL: --strict-selected and approved/locked below mins "
                "(selected items do not count as approved).",
                file=sys.stderr,
            )
            return 1
        print(
            "PASS with warnings: approved mins unmet but selected pool covers "
            "(awaiting Shawn’s selected→approved gate; exit 0).",
            file=sys.stderr,
        )
        return 0
    if args.strict_selected and selected_gaps:
        print(
            "FAIL: --strict-selected and approved+selected still below mins.",
            file=sys.stderr,
        )
        return 1
    if selected_only_covers:
        print(
            "PASS with warnings: approved mins unmet but selected pool covers "
            "(exit 0 unless --strict-selected).",
            file=sys.stderr,
        )
        return 0
    print("PASS", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
