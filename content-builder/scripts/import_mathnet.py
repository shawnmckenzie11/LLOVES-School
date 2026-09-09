#!/usr/bin/env python3
"""Import a small reviewed MathNet slice for vertex-form transfer.

Uses the local DuckDB cache (``.local-data/mathnet/all_text.duckdb``). Does not
invent keys. MathNet is olympiad-oriented; imported items stay marked as such.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402

from catalogue import utc_now, write_record  # noqa: E402

DB = Path(__file__).resolve().parents[2] / ".local-data" / "mathnet" / "all_text.duckdb"
TOPIC = "Algebra > Intermediate Algebra > Quadratic functions"
DATASET_URL = "https://huggingface.co/datasets/ShadenA/MathNet"
LICENCE = "CC BY 4.0 unless the contest/country asserts otherwise; see row country/competition"


def _row(con: duckdb.DuckDBPyConnection, item_id: str) -> dict:
    rec = con.execute(
        """
        SELECT id, country, competition, language, problem_type,
               problem_markdown, final_answer
        FROM mathnet WHERE id = ?
        """,
        [item_id],
    ).fetchone()
    if rec is None:
        raise SystemExit(f"MathNet id {item_id} not in {DB}")
    keys = (
        "id",
        "country",
        "competition",
        "language",
        "problem_type",
        "problem_markdown",
        "final_answer",
    )
    return dict(zip(keys, rec))


def main() -> int:
    """Write reviewed records. Skip items whose 'vertex' is geometric, not parabola."""
    if not DB.is_file():
        print(f"missing {DB}", file=sys.stderr)
        return 1
    con = duckdb.connect(str(DB), read_only=True)
    # 0gz2: parabola through two points, vertex x-coordinate — closest to this lesson,
    # still contest-hard. 0i5b uses 'vertex' as a cone point — excluded.
    item = _row(con, "0gz2")
    write_record(
        {
            "id": "mathnet-0gz2",
            "source": "mathnet",
            "source_id": "0gz2",
            "source_url": f"https://mathnet.mit.edu/explorer.html?id=0gz2",
            "source_revision": "local-duckdb:.local-data/mathnet/all_text.duckdb",
            "resource_type": "challenge",
            "title": "MathNet 0gz2: parabola through two points, vertex x-coordinate",
            "content_or_local_path": item["problem_markdown"],
            "topics": [TOPIC, "vertex of a parabola"],
            "prerequisites": ["parabola in general form", "vertex x-coordinate"],
            "representations": ["equation", "graph", "verbal"],
            "reading_demand": "high",
            "mathematical_difficulty": "olympiad",
            "context": (
                f"{item['country']} / {item['competition']}. "
                "Not a Grade 11 worksheet. Use only as a transfer candidate after rewrite."
            ),
            "instructional_roles": ["tests transfer"],
            "answer": item["final_answer"],
            "answer_verification": (
                "Copied from MathNet final_answer convenience field; LLM-assisted, not official key"
            ),
            "embed_configuration": None,
            "fallback": "Keep original stem in source field; do not use olympiad stem as student copy.",
            "licence": LICENCE,
            "attribution": (
                f"MathNet id 0gz2, {item['competition']}, {item['country']}. "
                f"{DATASET_URL}. Language: {item['language']}."
            ),
            "permitted_use_status": "unknown",
            "access_test_result": "read from local DuckDB cache",
            "checked_at": utc_now(),
            "review_status": "selected",
        }
    )
    print("imported mathnet-0gz2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
