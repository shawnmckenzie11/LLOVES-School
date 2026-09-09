#!/usr/bin/env python3
"""Validate module question banks under catalogue/banks/.

Loads bank-item.schema.json and module-bank.schema.json with jsonschema when
available (prefer content-builder/.venv). Validates every module.json and every
items/*.json. Exits non-zero on failure. Prints summary counts.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANKS = ROOT / "catalogue" / "banks"
ITEM_SCHEMA_PATH = BANKS / "bank-item.schema.json"
MODULE_SCHEMA_PATH = BANKS / "module-bank.schema.json"


def _load_json(path: Path) -> object:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _try_jsonschema():
    try:
        import jsonschema
        from jsonschema import Draft202012Validator

        return jsonschema, Draft202012Validator
    except ImportError:
        return None, None


def main() -> int:
    if not BANKS.is_dir():
        print(f"missing banks dir: {BANKS}", file=sys.stderr)
        return 1

    item_schema = _load_json(ITEM_SCHEMA_PATH)
    module_schema = _load_json(MODULE_SCHEMA_PATH)

    jsonschema, Draft202012Validator = _try_jsonschema()
    if Draft202012Validator is None:
        print(
            "WARNING: jsonschema not installed; structural checks only "
            "(pip install jsonschema in content-builder/.venv)",
            file=sys.stderr,
        )
        item_validator = None
        module_validator = None
    else:
        item_validator = Draft202012Validator(item_schema)
        module_validator = Draft202012Validator(module_schema)

    failures: list[str] = []
    items: list[dict] = []
    modules_checked = 0

    for module_path in sorted(BANKS.glob("**/module.json")):
        modules_checked += 1
        try:
            module_data = _load_json(module_path)
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{module_path}: load error: {exc}")
            continue

        if module_validator is not None:
            for err in sorted(module_validator.iter_errors(module_data), key=str):
                failures.append(f"{module_path}: {err.message}")
        elif not isinstance(module_data, dict):
            failures.append(f"{module_path}: not an object")

        items_dir = module_path.parent / "items"
        declared = module_data.get("items") if isinstance(module_data, dict) else None
        declared_ids: list[str] = []
        if isinstance(declared, list):
            for entry in declared:
                if isinstance(entry, str):
                    declared_ids.append(entry)
                elif isinstance(entry, dict) and isinstance(entry.get("id"), str):
                    declared_ids.append(entry["id"])

        found_ids: set[str] = set()
        for item_path in sorted(items_dir.glob("*.json")) if items_dir.is_dir() else []:
            try:
                item = _load_json(item_path)
            except (OSError, json.JSONDecodeError) as exc:
                failures.append(f"{item_path}: load error: {exc}")
                continue
            if not isinstance(item, dict):
                failures.append(f"{item_path}: not an object")
                continue
            if item_validator is not None:
                for err in sorted(item_validator.iter_errors(item), key=str):
                    failures.append(f"{item_path}: {err.message}")
            # Nelson / excluded policy
            if item.get("source") == "nelson_functions_11":
                if item.get("stem_student") is not None:
                    failures.append(
                        f"{item_path}: Nelson item must have stem_student null"
                    )
                if item.get("student_html_allowed") is not False:
                    failures.append(
                        f"{item_path}: Nelson item must have student_html_allowed false"
                    )
                if item.get("permitted_use_status") != "excluded":
                    failures.append(
                        f"{item_path}: Nelson item must have permitted_use_status excluded"
                    )
            if item.get("student_html_allowed") is False and item.get("stem_student"):
                failures.append(
                    f"{item_path}: stem_student must be null when student_html_allowed is false"
                )
            item_id = item.get("id")
            if isinstance(item_id, str):
                found_ids.add(item_id)
                if item_path.stem != item_id:
                    failures.append(
                        f"{item_path}: filename stem {item_path.stem!r} != id {item_id!r}"
                    )
            items.append(item)

        for item_id in declared_ids:
            if item_id not in found_ids:
                failures.append(
                    f"{module_path}: declared item {item_id!r} missing under items/"
                )
        for item_id in sorted(found_ids - set(declared_ids)):
            failures.append(
                f"{module_path}: item file {item_id!r} not listed in module.json items"
            )

    # Counts
    by_source = Counter(i.get("source") for i in items)
    by_review = Counter(i.get("review_status") for i in items)
    student_ok = sum(1 for i in items if i.get("student_html_allowed") is True)

    print(f"modules: {modules_checked}")
    print(f"total items: {len(items)}")
    print(f"student_html_allowed: {student_ok}")
    print("by source:")
    for key, count in sorted(by_source.items(), key=lambda kv: (str(kv[0]), kv[1])):
        print(f"  {key}: {count}")
    print("by review_status:")
    for key, count in sorted(by_review.items(), key=lambda kv: (str(kv[0]), kv[1])):
        print(f"  {key}: {count}")

    if failures:
        print("FAIL", file=sys.stderr)
        for msg in failures:
            print(f"- {msg}", file=sys.stderr)
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
