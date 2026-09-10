"""JSON catalogue records plus a rebuildable SQLite search index.

The index is not the school LMS database. Downloaded source clones stay in
``content-builder/cache/``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE_DIR = ROOT / "catalogue" / "resources"
INDEX_DIR = ROOT / "index"
INDEX_PATH = INDEX_DIR / "resources.sqlite"
SCHEMA_PATH = ROOT / "catalogue" / "resource.schema.json"


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_schema() -> dict[str, Any]:
    """Load the committed JSON Schema for one resource record."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_record(record: dict[str, Any]) -> None:
    """Raise jsonschema.ValidationError when the record is incomplete."""
    import jsonschema

    jsonschema.validate(record, load_schema())


def write_record(record: dict[str, Any], *, validate: bool = True) -> Path:
    """Write one catalogue JSON file. Returns the path written."""
    if validate:
        validate_record(record)
    CATALOGUE_DIR.mkdir(parents=True, exist_ok=True)
    path = CATALOGUE_DIR / f"{record['id']}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def iter_records() -> list[dict[str, Any]]:
    """Load every ``catalogue/resources/*.json`` record."""
    if not CATALOGUE_DIR.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(CATALOGUE_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _join(values: Any) -> str:
    if not values:
        return ""
    if isinstance(values, list):
        return " | ".join(str(v) for v in values)
    return str(values)


def rebuild_index(db_path: Path | None = None) -> Path:
    """Rebuild the SQLite FTS index from committed JSON records."""
    dest = db_path or INDEX_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    records = iter_records()
    con = sqlite3.connect(dest)
    try:
        con.execute("DROP TABLE IF EXISTS resources")
        con.execute("DROP TABLE IF EXISTS resources_fts")
        con.execute(
            """
            CREATE TABLE resources (
                id TEXT PRIMARY KEY,
                source TEXT,
                source_id TEXT,
                resource_type TEXT,
                title TEXT,
                topics TEXT,
                instructional_roles TEXT,
                review_status TEXT,
                licence TEXT,
                json TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE VIRTUAL TABLE resources_fts USING fts5(
                id, title, topics, instructional_roles, content
            )
            """
        )
        for rec in records:
            blob = json.dumps(rec, ensure_ascii=False)
            con.execute(
                """
                INSERT INTO resources (
                    id, source, source_id, resource_type, title,
                    topics, instructional_roles, review_status, licence, json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.get("id"),
                    rec.get("source"),
                    rec.get("source_id"),
                    rec.get("resource_type"),
                    rec.get("title"),
                    _join(rec.get("topics")),
                    _join(rec.get("instructional_roles")),
                    rec.get("review_status"),
                    rec.get("licence"),
                    blob,
                ),
            )
            content = " ".join(
                str(part)
                for part in (
                    rec.get("title"),
                    rec.get("content_or_local_path"),
                    rec.get("context"),
                    rec.get("attribution"),
                )
                if part
            )
            con.execute(
                """
                INSERT INTO resources_fts (id, title, topics, instructional_roles, content)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    rec.get("id"),
                    rec.get("title") or "",
                    _join(rec.get("topics")),
                    _join(rec.get("instructional_roles")),
                    content,
                ),
            )
        con.commit()
    finally:
        con.close()
    return dest


def search(
    query: str = "",
    *,
    source: str | None = None,
    resource_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Keyword search with optional source/type filters."""
    if not INDEX_PATH.is_file():
        rebuild_index()
    con = sqlite3.connect(INDEX_PATH)
    con.row_factory = sqlite3.Row
    try:
        sql = "SELECT resources.json AS json FROM resources"
        params: list[Any] = []
        clauses: list[str] = []
        if query.strip():
            sql += " JOIN resources_fts ON resources.id = resources_fts.id"
            clauses.append("resources_fts MATCH ?")
            params.append(query.strip())
        if source:
            clauses.append("resources.source = ?")
            params.append(source)
        if resource_type:
            clauses.append("resources.resource_type = ?")
            params.append(resource_type)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " LIMIT ?"
        params.append(limit)
        rows = con.execute(sql, params).fetchall()
        return [json.loads(row["json"]) for row in rows]
    finally:
        con.close()


if __name__ == "__main__":
    path = rebuild_index()
    print(f"indexed {len(iter_records())} records → {path}")
