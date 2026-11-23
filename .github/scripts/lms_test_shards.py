#!/usr/bin/env python3
"""Split the LMS unittest modules into four balanced shards.

``test_fanout_guard.py`` stays out of these shards. The fan-out guard job
runs it. ``test_boot_schema_race.py`` and ``test_mck365_*`` are always in
the plan, and CI fails the aggregate when an ``lms/`` change did not run them.

Weights are file size, with a fixed boost for modules that launch Chrome or
LibreOffice. Shards are a longest-processing-time split so one large module
does not share a shard with the next-largest one.

The aggregate prints the ten slowest modules. A module over 90 seconds or a
shard over 8 minutes is a warning, not a failure.
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LMS = REPO / "lms"
SHARDS = 4
EXCLUDED = frozenset({"test_fanout_guard.py"})
SOFT_MODULE_SECONDS = 90
SOFT_SHARD_SECONDS = 8 * 60
_TOOL_BOOST = 200_000


def suite_modules() -> list[str]:
    """Return ``lms/test_*.py`` names, without the fan-out guard module."""
    names = sorted(path.name for path in LMS.glob("test_*.py"))
    return [name for name in names if name not in EXCLUDED]


def mandatory_modules(names: list[str] | None = None) -> list[str]:
    """Return the schema-race and MCK-365 modules that every ``lms/`` change runs.

    Args:
        names: Module file names. Defaults to the current suite.

    Returns:
        Names that must appear in the shard plan.
    """
    pool = suite_modules() if names is None else names
    return [
        name
        for name in pool
        if name == "test_boot_schema_race.py" or name.startswith("test_mck365_")
    ]


def module_weight(path: Path) -> int:
    """Return a packing weight for one test module.

    Args:
        path: ``lms/test_*.py`` file.

    Returns:
        Byte size, plus a boost when the file launches Chrome or LibreOffice.
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    weight = path.stat().st_size
    if "LLOVES_E2E_CHROME" in text or "playwright" in text or "soffice" in text:
        weight += _TOOL_BOOST
    return weight


def assign_shards(names: list[str] | None = None, shards: int = SHARDS) -> list[list[str]]:
    """Pack modules into ``shards`` buckets, heaviest first.

    Equal loads go to the lower shard index. Modules inside a shard are
    sorted by name, which is the order they run.

    Args:
        names: Module file names. Defaults to the current suite.
        shards: How many buckets to fill.

    Returns:
        One list of module names per shard.
    """
    chosen = suite_modules() if names is None else list(names)
    weighted = [(name, module_weight(LMS / name)) for name in chosen]
    ordered = sorted(weighted, key=lambda item: (-item[1], item[0]))
    buckets: list[list[str]] = [[] for _ in range(shards)]
    loads = [0] * shards
    for name, weight in ordered:
        index = min(range(shards), key=lambda i: (loads[i], i))
        buckets[index].append(name)
        loads[index] += weight
    for bucket in buckets:
        bucket.sort()
    return buckets


def shard_tools(names: list[str]) -> dict[str, bool]:
    """Return whether a shard needs Chrome or LibreOffice installed.

    Args:
        names: Module file names in one shard.

    Returns:
        ``chrome`` and ``soffice`` flags.
    """
    chrome = False
    soffice = False
    for name in names:
        text = (LMS / name).read_text(encoding="utf-8", errors="ignore")
        if "LLOVES_E2E_CHROME" in text or "playwright" in text:
            chrome = True
        if "soffice" in text:
            soffice = True
    return {"chrome": chrome, "soffice": soffice}


def check_plan(shards: int = SHARDS) -> None:
    """Raise ``SystemExit`` when the plan drops or duplicates a module.

    Args:
        shards: Expected bucket count.
    """
    names = suite_modules()
    buckets = assign_shards(names, shards)
    if len(buckets) != shards or any(not bucket for bucket in buckets):
        raise SystemExit(f"expected {shards} non-empty shards")
    flat = [name for bucket in buckets for name in bucket]
    if len(flat) != len(set(flat)):
        raise SystemExit("a module is in more than one shard")
    if set(flat) != set(names):
        raise SystemExit("shard plan does not match the suite")
    if EXCLUDED & set(flat):
        raise SystemExit("fan-out guard must stay out of the shards")
    missing = [name for name in mandatory_modules(names) if name not in set(flat)]
    if missing:
        raise SystemExit(f"mandatory modules missing: {missing}")
    on_disk = {path.name for path in LMS.glob("test_*.py")}
    if on_disk - set(names) != EXCLUDED:
        raise SystemExit("suite exclusion drifted from test_fanout_guard.py")


def assert_sqlite_heartbeat_env() -> None:
    """Refuse ``LIVE_DATABASE_URL`` and ``DATABASE_URL``.

    Those attach Postgres to every ``create_app()`` and move the sqlite
    heartbeat tests off sqlite. ``LIVE_PRESENCE_TEST_URL`` is allowed.

    Raises:
        SystemExit: One of the forbidden variables is set.
    """
    found = [
        key
        for key in ("LIVE_DATABASE_URL", "DATABASE_URL")
        if os.environ.get(key, "").strip()
    ]
    if found:
        raise SystemExit(f"refusing to run with {', '.join(found)} set")


def _write_timings(path: Path, payload: dict) -> None:
    """Atomically replace a shard timings file.

    Args:
        path: Destination JSON path.
        payload: Shard timings.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_shard(shard: int, shards: int, timings_path: Path) -> int:
    """Run one shard in-process and write per-module timings.

    Args:
        shard: 1-based shard index.
        shards: How many shards the plan has.
        timings_path: Where to write the JSON timings.

    Returns:
        0 when every module passed, 1 otherwise.
    """
    if shard < 1 or shard > shards:
        raise SystemExit(f"shard {shard} is outside 1..{shards}")
    assert_sqlite_heartbeat_env()
    check_plan(shards)
    sys.path.insert(0, str(LMS))
    modules = assign_shards(shards=shards)[shard - 1]
    loader = unittest.TestLoader()
    runner = unittest.TextTestRunner(verbosity=2)
    rows: list[dict] = []
    failed = False
    wall_start = time.perf_counter()
    for name in modules:
        started = time.perf_counter()
        tests = 0
        module_failed = False
        try:
            suite = loader.loadTestsFromName(name[:-3])
            tests = suite.countTestCases()
            result = runner.run(suite)
            module_failed = not result.wasSuccessful()
        except Exception as exc:  # noqa: BLE001 — one bad import must not hide the rest
            module_failed = True
            print(f"FAIL load {name}: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        elapsed = time.perf_counter() - started
        rows.append(
            {
                "module": name,
                "tests": tests,
                "seconds": round(elapsed, 3),
                "failed": module_failed,
            }
        )
        failed = failed or module_failed
        _write_timings(
            timings_path,
            _payload(shard, shards, rows, time.perf_counter() - wall_start, failed),
        )
        if elapsed >= SOFT_MODULE_SECONDS:
            print(
                f"::warning::{name} took {elapsed:.0f}s, over the {SOFT_MODULE_SECONDS}s soft budget",
                flush=True,
            )
    wall = time.perf_counter() - wall_start
    payload = _payload(shard, shards, rows, wall, failed)
    _write_timings(timings_path, payload)
    if wall >= SOFT_SHARD_SECONDS:
        print(
            f"::warning::shard {shard} took {wall / 60:.1f} min, over the {SOFT_SHARD_SECONDS // 60} min soft budget",
            flush=True,
        )
    print(
        f"shard {shard}/{shards} modules={len(rows)} tests={payload['tests']} "
        f"seconds={payload['seconds']:.0f} failures={sum(1 for row in rows if row['failed'])}",
        flush=True,
    )
    return 1 if failed else 0


def _payload(shard: int, shards: int, rows: list[dict], wall: float, failed: bool) -> dict:
    """Build the timings document for one shard.

    Args:
        shard: 1-based shard index.
        shards: Shard count.
        rows: Per-module rows so far.
        wall: Seconds since the shard started.
        failed: Whether any module has failed.

    Returns:
        JSON-ready timings.
    """
    return {
        "shard": shard,
        "shards": shards,
        "modules": rows,
        "tests": sum(int(row["tests"]) for row in rows),
        "seconds": round(wall, 3),
        "failed": failed,
    }


def _load_timing_files(directory: Path) -> list[dict]:
    """Read every shard timings JSON under ``directory``.

    Args:
        directory: Artifact download directory.

    Returns:
        Parsed timing documents. Unreadable files are skipped.
    """
    docs: list[dict] = []
    if not directory.is_dir():
        return docs
    for path in sorted(directory.rglob("*.json")):
        try:
            docs.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return docs


def _step_summary(text: str) -> None:
    """Print ``text`` and append it to the job summary when that file exists.

    Args:
        text: Markdown or plain lines.
    """
    print(text, flush=True)
    dest = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if dest:
        with open(dest, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def render_report(docs: list[dict], *, lms_touched_flag: str) -> tuple[str, list[str]]:
    """Build the slowest-module table and the hard parity errors.

    Args:
        docs: Shard timing documents.
        lms_touched_flag: ``true``, ``false``, or empty. Empty means unknown
            and still requires the mandatory modules.

    Returns:
        The report text, and hard errors. Soft budget notes are inside the text.
    """
    rows: list[dict] = []
    for doc in docs:
        shard = doc.get("shard")
        for row in doc.get("modules") or []:
            if isinstance(row, dict) and row.get("module"):
                rows.append({**row, "shard": shard})
    seen = {str(row["module"]) for row in rows}
    expected = suite_modules()
    missing = sorted(set(expected) - seen)
    extra_excluded = sorted(EXCLUDED & seen)
    errors: list[str] = []
    if missing:
        errors.append(f"dropped modules: {', '.join(missing)}")
    if extra_excluded:
        errors.append(f"fan-out module ran inside a shard: {', '.join(extra_excluded)}")
    required = mandatory_modules(expected)
    if lms_touched_flag.strip().lower() != "false":
        absent = [name for name in required if name not in seen]
        if absent:
            errors.append(f"lms/ change missing mandatory modules: {', '.join(absent)}")
    ranked = sorted(rows, key=lambda row: (-float(row.get("seconds") or 0), str(row["module"])))
    lines = [
        "| # | module | seconds | tests | shard |",
        "|---|---|---:|---:|---:|",
    ]
    for index, row in enumerate(ranked[:10], start=1):
        lines.append(
            f"| {index} | {row['module']} | {float(row.get('seconds') or 0):.1f} | "
            f"{int(row.get('tests') or 0)} | {row.get('shard')} |"
        )
    if len(lines) == 2:
        lines.append("| | (no module timings) | | | |")
    tests = sum(int(row.get("tests") or 0) for row in rows)
    slowest = max((float(doc.get("seconds") or 0) for doc in docs), default=0.0)
    lines.append("")
    lines.append(
        f"LMS shards: modules={len(seen)} tests={tests} "
        f"slowest_shard_s={slowest:.0f} dropped={len(missing)}"
    )
    for row in ranked:
        if float(row.get("seconds") or 0) >= SOFT_MODULE_SECONDS:
            lines.append(
                f"::warning::{row['module']} took {float(row['seconds']):.0f}s, "
                f"over the {SOFT_MODULE_SECONDS}s soft budget"
            )
    for doc in docs:
        wall = float(doc.get("seconds") or 0)
        if wall >= SOFT_SHARD_SECONDS:
            lines.append(
                f"::warning::shard {doc.get('shard')} took {wall / 60:.1f} min, "
                f"over the {SOFT_SHARD_SECONDS // 60} min soft budget"
            )
    return "\n".join(lines), errors


def aggregate(timings_dir: Path, shard_result: str, reuse_skip: str, lms_flag: str) -> int:
    """Exit 0 only when every shard passed, or a verified CI reuse skipped them.

    A soft budget warning does not fail the run. Dropped modules do.

    Args:
        timings_dir: Directory of shard timing JSON files.
        shard_result: GitHub ``needs`` result for the matrix.
        reuse_skip: ``true`` when deploy reused a green CI run.
        lms_flag: Classify output. Anything other than ``false`` requires
            the mandatory modules.

    Returns:
        Process exit code.
    """
    if reuse_skip == "true" and shard_result == "skipped":
        _step_summary("LMS test suite: reused a green ci.yml run; shards were not re-run.")
        return 0
    docs = _load_timing_files(timings_dir)
    report, errors = render_report(docs, lms_touched_flag=lms_flag)
    _step_summary(report)
    if shard_result != "success":
        errors.append(f"shard result is {shard_result or 'missing'}, want success")
    for error in errors:
        print(f"::error::{error}", flush=True)
    return 1 if errors else 0


def _write_plan_outputs(shard: int, shards: int) -> None:
    """Write Chrome and LibreOffice flags for one shard to ``GITHUB_OUTPUT``.

    Args:
        shard: 1-based shard index.
        shards: Shard count.
    """
    check_plan(shards)
    names = assign_shards(shards=shards)[shard - 1]
    tools = shard_tools(names)
    print(f"shard {shard}/{shards} modules={len(names)} chrome={tools['chrome']} soffice={tools['soffice']}")
    dest = os.environ.get("GITHUB_OUTPUT", "")
    if not dest:
        return
    with open(dest, "a", encoding="utf-8") as handle:
        handle.write(f"chrome={'true' if tools['chrome'] else 'false'}\n")
        handle.write(f"soffice={'true' if tools['soffice'] else 'false'}\n")


def main(argv: list[str] | None = None) -> int:
    """Plan, run, or aggregate a shard.

    Args:
        argv: CLI arguments, without the program name.

    Returns:
        Process exit code.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["check"]:
        check_plan()
        buckets = assign_shards()
        weights = [sum(module_weight(LMS / name) for name in bucket) for bucket in buckets]
        print(f"shards={SHARDS} modules={sum(len(bucket) for bucket in buckets)} weights={weights}")
        return 0
    if args[:1] == ["plan"] and "--shard" in args:
        shard = int(args[args.index("--shard") + 1])
        shards = SHARDS
        if "--shards" in args:
            shards = int(args[args.index("--shards") + 1])
        _write_plan_outputs(shard, shards)
        return 0
    if args[:1] == ["run"] and "--shard" in args and "--timings" in args:
        shard = int(args[args.index("--shard") + 1])
        shards = SHARDS
        if "--shards" in args:
            shards = int(args[args.index("--shards") + 1])
        timings = Path(args[args.index("--timings") + 1])
        return run_shard(shard, shards, timings)
    if args[:1] == ["aggregate"]:
        directory = Path(os.environ.get("TIMINGS_DIR", "timings"))
        return aggregate(
            directory,
            os.environ.get("SHARD_RESULT", ""),
            os.environ.get("REUSE_SKIP", ""),
            os.environ.get("LMS_TOUCHED", ""),
        )
    print("usage: lms_test_shards.py check | plan --shard N | run --shard N --timings FILE | aggregate", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
