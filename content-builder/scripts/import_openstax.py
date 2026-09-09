#!/usr/bin/env python3
"""Import a small OpenStax College Algebra 2e vertex-form slice into the catalogue.

Reads the shallow clone under ``content-builder/cache/osbooks-college-algebra-bundle``.
Does not invent answers. OpenStax algebraic “rewrite in vertex form” items are
completing the square (Ontario A2.8) and are not imported for M4 Lesson 1.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Allow ``python scripts/import_openstax.py`` from repo or content-builder.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from catalogue import ROOT, utc_now, write_record  # noqa: E402

CLONE = ROOT / "cache" / "osbooks-college-algebra-bundle"
MODULE = CLONE / "modules" / "m51274" / "index.cnxml"
PUBLIC_URL = "https://openstax.org/books/college-algebra-2e/pages/5-1-quadratic-functions"
LICENCE = "CC BY-NC-SA 4.0"
ATTRIBUTION = (
    "OpenStax College Algebra 2e, Quadratic Functions (m51274). "
    "CC BY-NC-SA 4.0. https://openstax.org/details/books/college-algebra-2e"
)


def _git_revision() -> str | None:
    if not CLONE.is_dir():
        return None
    proc = subprocess.run(
        ["git", "-C", str(CLONE), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() or None


def main() -> int:
    """Write reviewed OpenStax records for this lesson."""
    if not MODULE.is_file():
        print(
            "missing clone; run:\n"
            "  git clone --depth 1 https://github.com/openstax/osbooks-college-algebra-bundle.git "
            "content-builder/cache/osbooks-college-algebra-bundle",
            file=sys.stderr,
        )
        return 1
    revision = _git_revision()
    rel = str(MODULE.relative_to(ROOT.parent)) if False else str(MODULE)
    local = "cache/osbooks-college-algebra-bundle/modules/m51274/index.cnxml"
    write_record(
        {
            "id": "openstax-ca2e-m51274-vertex-form-definition",
            "source": "openstax",
            "source_id": "m51274#term-00015",
            "source_url": PUBLIC_URL,
            "source_revision": revision,
            "resource_type": "explanation",
            "title": "OpenStax: vertex form of a quadratic function",
            "content_or_local_path": local,
            "topics": ["vertex form", "quadratic functions", "parabola"],
            "prerequisites": ["graph of y = x^2"],
            "representations": ["equation", "graph"],
            "reading_demand": "moderate",
            "mathematical_difficulty": "routine",
            "context": (
                "OpenStax names f(x) = a(x − h)^2 + k the standard form of a quadratic "
                "and also the vertex form. Ontario calls that expression vertex form and "
                "calls ax^2 + bx + c standard form. Quote OpenStax; do not rename their terms "
                "in the source field."
            ),
            "instructional_roles": [
                "connects graph to equation",
                "names vertex (h, k) from the equation",
            ],
            "answer": None,
            "answer_verification": None,
            "embed_configuration": None,
            "fallback": "Read the local CNXML excerpt at content_or_local_path.",
            "licence": LICENCE,
            "attribution": ATTRIBUTION,
            "permitted_use_status": "non-commercial",
            "access_test_result": "local CNXML present in cache clone",
            "checked_at": utc_now(),
            "review_status": "selected",
        }
    )
    write_record(
        {
            "id": "openstax-ca2e-m51274-advantage-verbal",
            "source": "openstax",
            "source_id": "m51274#fs-id1165135361332",
            "source_url": PUBLIC_URL,
            "source_revision": revision,
            "resource_type": "exercise",
            "title": "OpenStax verbal: advantage of this form",
            "content_or_local_path": local,
            "topics": ["vertex form"],
            "prerequisites": [],
            "representations": ["verbal", "equation"],
            "reading_demand": "low",
            "mathematical_difficulty": "routine",
            "context": (
                "Stem (verbatim sense): Explain the advantage of writing a quadratic "
                "function in standard form. OpenStax solution: When written in that form, "
                "the vertex can be easily identified."
            ),
            "instructional_roles": ["supports practice", "connects graph to equation"],
            "answer": "When written in that form, the vertex can be easily identified.",
            "answer_verification": "Copied from OpenStax solution element fs-id1165135361339",
            "embed_configuration": None,
            "fallback": None,
            "licence": LICENCE,
            "attribution": ATTRIBUTION,
            "permitted_use_status": "non-commercial",
            "access_test_result": "local CNXML present in cache clone",
            "checked_at": utc_now(),
            "review_status": "selected",
        }
    )
    print(f"imported OpenStax m51274 at {revision} from {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
