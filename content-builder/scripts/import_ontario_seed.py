#!/usr/bin/env python3
"""Seed Ontario sample wording for this lesson from the verified seed file."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from catalogue import utc_now, write_record  # noqa: E402

SEED = Path(__file__).resolve().parents[2] / "lms" / "seeds" / "mcf3m_expectations.json"


def _specific(code: str) -> dict:
    data = json.loads(SEED.read_text(encoding="utf-8"))
    for strand in data["strands"]:
        for spec in strand.get("specific", []):
            if spec.get("code") == code:
                return spec
    raise SystemExit(f"{code} not in {SEED}")


def main() -> int:
    """Write seed-backed records. Statements are copied, not rewritten."""
    a27 = _specific("A2.7")
    examples = a27.get("examples") or []
    write_record(
        {
            "id": "ontario-seed-mcf3m-A2.7",
            "source": "ontario-seed",
            "source_id": "A2.7",
            "source_url": None,
            "source_revision": "lms/seeds/mcf3m_expectations.json",
            "resource_type": "expectation",
            "title": "MCF3M A2.7 (verbatim seed)",
            "content_or_local_path": a27["statement"],
            "topics": a27.get("topics") or [],
            "prerequisites": [],
            "representations": ["equation", "graph"],
            "reading_demand": None,
            "mathematical_difficulty": None,
            "context": "Ontario specific expectation. Do not paraphrase as student copy.",
            "instructional_roles": ["curriculum identity"],
            "answer": None,
            "answer_verification": None,
            "embed_configuration": None,
            "fallback": None,
            "licence": "Queen's Printer for Ontario (curriculum excerpt in verified seed)",
            "attribution": str(SEED),
            "permitted_use_status": "restricted",
            "access_test_result": "quoted from committed seed",
            "checked_at": utc_now(),
            "review_status": "approved",
        }
    )
    if examples:
        write_record(
            {
                "id": "ontario-seed-mcf3m-A2.7-sample",
                "source": "ontario-seed",
                "source_id": "A2.7-example-0",
                "source_url": None,
                "source_revision": "lms/seeds/mcf3m_expectations.json",
                "resource_type": "example",
                "title": "MCF3M A2.7 sample problem (verbatim seed)",
                "content_or_local_path": examples[0],
                "topics": ["vertex form", "standard form"],
                "prerequisites": [],
                "representations": ["equation", "graph"],
                "reading_demand": "low",
                "mathematical_difficulty": "routine",
                "context": "Ministry sample in the seed. Guided example / practice candidate.",
                "instructional_roles": [
                    "supports practice",
                    "connects graph to equation",
                    "tests equivalent representations",
                ],
                "answer": None,
                "answer_verification": "Seed has no key for this sample.",
                "embed_configuration": None,
                "fallback": None,
                "licence": "Queen's Printer for Ontario (curriculum excerpt in verified seed)",
                "attribution": str(SEED),
                "permitted_use_status": "restricted",
                "access_test_result": "quoted from committed seed",
                "checked_at": utc_now(),
                "review_status": "selected",
            }
        )
    print("imported ontario-seed A2.7")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
