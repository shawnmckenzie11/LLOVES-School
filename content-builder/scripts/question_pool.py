#!/usr/bin/env python3
"""Question-pool validation, FTS index, ranking, and deduplication.

The pool is not LMS sqlite. Nelson wording never enters accepted student_facing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "catalogue" / "contracts" / "question-pool.schema.json"
POOL_DIR = ROOT / "catalogue" / "pools"
INDEX_PATH = ROOT / "index" / "pool.sqlite"
HIGHER_ORDER = frozenset({"analyze", "evaluate", "create"})
NELSON_LEAKS = (
    "bungee",
    "cigarettes sold",
    "nelson functions 11",
    "libfile_",
)

from student_authoring import load_json  # noqa: E402


def normalize_prompt(text: str) -> str:
    """Collapse whitespace for hashing and near-duplicate checks."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def prompt_hash(text: str) -> str:
    """SHA-256 of the normalized student-facing prompt."""
    return hashlib.sha256(normalize_prompt(text).encode("utf-8")).hexdigest()


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", normalize_prompt(text)) if len(t) > 2}


def jaccard(a: str, b: str) -> float:
    """Token Jaccard similarity of two prompts."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def pool_path(course: str, lesson_id: str) -> Path:
    """Return the committed pool JSON path."""
    return POOL_DIR / course / f"{lesson_id}.json"


def load_schema() -> dict[str, Any]:
    """Load the question-pool schema."""
    return load_json(SCHEMA_PATH)


def validate_pool(doc: dict[str, Any]) -> None:
    """Raise if the pool fails schema, rights, or uniqueness gates."""
    jsonschema.validate(doc, load_schema())
    seen_id: set[str] = set()
    seen_hash: set[str] = set()
    for item in doc.get("candidates") or []:
        cid = item["id"]
        if cid in seen_id:
            raise ValueError(f"duplicate candidate id: {cid}")
        seen_id.add(cid)
        digest = item["lineage"]["prompt_hash"]
        expected = prompt_hash(item["student_facing"])
        if digest != expected:
            raise ValueError(f"{cid} prompt_hash mismatch")
        if item.get("status") == "accepted":
            if digest in seen_hash:
                raise ValueError(f"duplicate accepted prompt hash: {cid}")
            seen_hash.add(digest)
            if item.get("student_export") is False:
                raise ValueError(f"accepted {cid} must allow student_export or be excluded")
            blob = item["student_facing"].lower()
            for needle in NELSON_LEAKS:
                if needle in blob:
                    raise ValueError(f"accepted {cid} leaks restricted source wording")
            if not item["verification"].get("verified"):
                raise ValueError(f"accepted {cid} is not verified")
            if item["verification"]["answer_kind"] == "source_key" and not item["verification"].get("source_key"):
                raise ValueError(f"{cid} claims a source key but has none")
            if item["verification"]["answer_kind"] == "authored" and not item["verification"].get("authored_solution"):
                raise ValueError(f"accepted {cid} needs an authored solution")


def accepted(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Return accepted candidates."""
    return [c for c in doc.get("candidates") or [] if c.get("status") == "accepted"]


def distinct_families(items: list[dict[str, Any]]) -> set[str]:
    """Family ids that are not numeric variants of another accepted item."""
    return {c["family_id"] for c in items if not c.get("variant_of")}


def coverage_report(doc: dict[str, Any]) -> dict[str, Any]:
    """Count accepted items and higher-order new families."""
    ok = accepted(doc)
    families = distinct_families(ok)
    new_families = {
        c["family_id"]
        for c in ok
        if c.get("expansion_recipe") or c["bloom"]["primary"] in HIGHER_ORDER
    }
    higher = {fid for fid in new_families if any(
        c["family_id"] == fid and c["bloom"]["primary"] in HIGHER_ORDER for c in ok
    )}
    pct = (len(higher) / len(new_families)) if new_families else 0.0
    return {
        "accepted": len(ok),
        "distinct_families": sorted(families),
        "new_or_higher_order_families": sorted(new_families),
        "higher_order_new_family_pct": pct,
        "target": (doc.get("coverage") or {}).get("target_accepted") or [24, 36],
    }


def iter_pool_docs(root: Path | None = None) -> list[tuple[str, str, dict[str, Any]]]:
    """Load every pool JSON under catalogue/pools."""
    base = (root or ROOT) / "catalogue" / "pools"
    docs = []
    if not base.is_dir():
        return []
    for path in sorted(base.glob("*/*.json")):
        if path.name.startswith("_"):
            continue
        doc = load_json(path)
        docs.append((path.parent.name, doc.get("lesson_id") or path.stem, doc))
    return docs


def rebuild_index(db_path: Path | None = None, *, root: Path | None = None) -> Path:
    """Rebuild the FTS index from committed pool JSON."""
    dest = db_path or INDEX_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(dest)
    try:
        con.execute("DROP TABLE IF EXISTS pool_items")
        con.execute("DROP TABLE IF EXISTS pool_fts")
        con.execute(
            """
            CREATE TABLE pool_items (
                id TEXT PRIMARY KEY,
                lesson_id TEXT,
                course_code TEXT,
                family_id TEXT,
                bloom TEXT,
                knowledge TEXT,
                task_family TEXT,
                status TEXT,
                representation TEXT,
                prompt_hash TEXT,
                json TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE VIRTUAL TABLE pool_fts USING fts5(
                id, title, student_facing, family_id, bloom, task_family
            )
            """
        )
        for course, lesson_id, doc in iter_pool_docs(root):
            validate_pool(doc)
            for item in doc.get("candidates") or []:
                reps = item.get("representations") or {}
                rep = " ".join((reps.get("input") or []) + (reps.get("output") or []))
                blob = json.dumps(item, ensure_ascii=False)
                con.execute(
                    """
                    INSERT INTO pool_items (
                        id, lesson_id, course_code, family_id, bloom, knowledge,
                        task_family, status, representation, prompt_hash, json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["id"],
                        lesson_id,
                        course,
                        item.get("family_id"),
                        item["bloom"]["primary"],
                        item.get("knowledge"),
                        item.get("task_family"),
                        item.get("status"),
                        rep,
                        item["lineage"]["prompt_hash"],
                        blob,
                    ),
                )
                con.execute(
                    """
                    INSERT INTO pool_fts (id, title, student_facing, family_id, bloom, task_family)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["id"],
                        item.get("title") or "",
                        item.get("student_facing") or "",
                        item.get("family_id") or "",
                        item["bloom"]["primary"],
                        item.get("task_family") or "",
                    ),
                )
        con.commit()
    finally:
        con.close()
    return dest


def search_pool(
    query: str = "",
    *,
    lesson_id: str | None = None,
    family_id: str | None = None,
    bloom: str | None = None,
    status: str = "accepted",
    representation: str | None = None,
    task_family: str | None = None,
    limit: int = 40,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """FTS search with structured filters. Ranking prefers coverage gaps."""
    dest = db_path or INDEX_PATH
    if not dest.is_file():
        rebuild_index(dest)
    con = sqlite3.connect(dest)
    con.row_factory = sqlite3.Row
    try:
        family_counts = {
            row["family_id"]: row["n"]
            for row in con.execute(
                "SELECT family_id, COUNT(*) AS n FROM pool_items WHERE status = 'accepted' GROUP BY family_id"
            )
        }
        sql = "SELECT pool_items.json AS json, pool_items.family_id AS family_id FROM pool_items"
        params: list[Any] = []
        clauses: list[str] = []
        if query.strip():
            sql += " JOIN pool_fts ON pool_items.id = pool_fts.id"
            clauses.append("pool_fts MATCH ?")
            params.append(query.strip())
        if lesson_id:
            clauses.append("pool_items.lesson_id = ?")
            params.append(lesson_id)
        if family_id:
            clauses.append("pool_items.family_id = ?")
            params.append(family_id)
        if bloom:
            clauses.append("pool_items.bloom = ?")
            params.append(bloom)
        if status:
            clauses.append("pool_items.status = ?")
            params.append(status)
        if representation:
            clauses.append("pool_items.representation LIKE ?")
            params.append(f"%{representation}%")
        if task_family:
            clauses.append("pool_items.task_family = ?")
            params.append(task_family)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " LIMIT ?"
        params.append(limit * 3)
        rows = con.execute(sql, params).fetchall()
        scored = []
        for row in rows:
            item = json.loads(row["json"])
            gap = 0 if family_counts.get(row["family_id"], 0) <= 1 else 1
            higher = 0 if item["bloom"]["primary"] in HIGHER_ORDER else 1
            scored.append((gap, higher, item.get("title") or "", item))
        scored.sort(key=lambda t: (t[0], t[1], t[2]))
        return [item for _, _, _, item in scored[:limit]]
    finally:
        con.close()


def near_duplicates(doc: dict[str, Any], threshold: float = 0.86) -> list[tuple[str, str, float]]:
    """Pairs of accepted prompts that are too similar."""
    items = accepted(doc)
    hits = []
    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            score = jaccard(a["student_facing"], b["student_facing"])
            if score >= threshold:
                hits.append((a["id"], b["id"], score))
    return hits


def main() -> int:
    """Validate pools, rebuild FTS, and print a coverage summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--lesson", default="M4-L1-vertex-form")
    parser.add_argument("--family")
    parser.add_argument("--bloom")
    args = parser.parse_args()
    if args.rebuild or not INDEX_PATH.is_file():
        rebuild_index()
    if args.query or args.family or args.bloom:
        hits = search_pool(
            args.query,
            lesson_id=args.lesson,
            family_id=args.family,
            bloom=args.bloom,
        )
        print(json.dumps([{"id": h["id"], "family_id": h["family_id"], "bloom": h["bloom"]["primary"], "title": h["title"]} for h in hits], indent=2))
        return 0
    for _course, lesson_id, doc in iter_pool_docs():
        validate_pool(doc)
        dups = near_duplicates(doc)
        if dups:
            raise SystemExit(f"near-duplicates in {lesson_id}: {dups}")
        print(json.dumps({"lesson_id": lesson_id, **coverage_report(doc)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
