#!/usr/bin/env python3
"""Find and rank MathNet problems for approachable portfolio anchors.

The script downloads MathNet's published Parquet shards, filters an exact
hierarchical topic tag, and writes both the complete filtered index and a
ranked shortlist. Problem statements remain in MathNet; outputs contain IDs,
metadata, links, and short previews only.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

import pyarrow.parquet as pq

DATASET = "ShadenA/MathNet"
DEFAULT_TOPIC = "Algebra > Intermediate Algebra > Quadratic functions"
PARQUET_ENDPOINT = "https://datasets-server.huggingface.co/parquet"
VIEWER_URL = "https://huggingface.co/datasets/ShadenA/MathNet/viewer/all/train?row={}"

CONTEXT_TERMS = {
    "ant", "spider", "train", "coin", "playground", "student", "teacher",
    "class", "game", "player", "notebook", "grade", "flag", "cans",
    "garden", "fence", "road", "car", "ticket", "price", "cost", "money",
    "age", "birthday", "clock", "time", "distance", "speed", "paint",
    "water", "flask", "animal", "farm", "photo", "card", "box", "ball",
    "race", "catch", "walk", "run", "rectangle", "square",
}
ABSTRACT_TERMS = {
    "prove that", "polynomial", "quadratic equation", "quadratic function",
    "real roots", "coefficient", "discriminant", "for all real",
    "positive real numbers", "determine all real",
}


def fetch_json(url: str) -> dict[str, Any]:
    """Return a decoded JSON object from ``url``."""
    request = urllib.request.Request(url, headers={"User-Agent": "ALC-MathNet-selector/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def download_file(url: str, destination: Path) -> None:
    """Download ``url`` atomically when ``destination`` is not cached."""
    if destination.exists() and destination.stat().st_size > 8:
        with destination.open("rb") as handle:
            handle.seek(-4, 2)
            if handle.read() == b"PAR1":
                return
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "ALC-MathNet-selector/1.0"})
    with urllib.request.urlopen(request, timeout=240) as response, temporary.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            out.write(chunk)
    temporary.replace(destination)


def parquet_urls() -> list[str]:
    """Return ordered URLs for the MathNet ``all/train`` Parquet shards."""
    query = urllib.parse.urlencode({"dataset": DATASET})
    payload = fetch_json(f"{PARQUET_ENDPOINT}?{query}")
    return [
        item["url"]
        for item in payload["parquet_files"]
        if item["config"] == "all" and item["split"] == "train"
    ]


def iter_rows(cache_dir: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    """Yield ``(global_row_index, row)`` pairs from every dataset shard."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    row_index = 0
    for shard_number, url in enumerate(parquet_urls()):
        path = cache_dir / f"{shard_number:04d}.parquet"
        download_file(url, path)
        for row in pq.read_table(path).to_pylist():
            yield row_index, row
            row_index += 1


def accessibility_score(row: dict[str, Any]) -> tuple[float, list[str]]:
    """Score a row for approachable wording and record human-readable reasons."""
    text = row.get("problem_markdown") or ""
    lower = text.lower()
    contexts = sorted(term for term in CONTEXT_TERMS if term in lower)
    abstract = sorted(term for term in ABSTRACT_TERMS if term in lower)
    score = 3.0 * len(contexts) - 2.5 * len(abstract)
    if 80 <= len(text) <= 450:
        score += 4
    score -= max(0, len(text) - 500) / 80
    score -= 0.6 * text.count("$$")
    score -= 0.2 * max(0, text.count("$") - 12)
    if row.get("language") not in (None, "English"):
        score -= 12
    if row.get("problem_type") != "proof only":
        score += 2
    if row.get("final_answer"):
        score += 1
    reasons = []
    if contexts:
        reasons.append("recognizable context: " + ", ".join(contexts[:5]))
    if 80 <= len(text) <= 450:
        reasons.append("short setup")
    if not abstract:
        reasons.append("does not announce formal quadratic machinery")
    return round(score, 2), reasons


def compact_record(row_index: int, row: dict[str, Any], score: float, reasons: list[str]) -> dict[str, Any]:
    """Build a compact, traceable record without republishing full solutions."""
    preview = re.sub(r"\s+", " ", row.get("problem_markdown") or "").strip()[:180]
    return {
        "id": row.get("id"),
        "row": row_index,
        "viewer_url": VIEWER_URL.format(row_index),
        "country": row.get("country"),
        "competition": row.get("competition"),
        "language": row.get("language"),
        "problem_type": row.get("problem_type"),
        "topics": row.get("topics_flat") or [],
        "has_images": bool(row.get("images")),
        "accessibility_score": score,
        "selection_reasons": reasons,
        "preview": preview,
    }


def select(topic: str, cache_dir: Path) -> list[dict[str, Any]]:
    """Return every exact-topic match ordered by accessibility score."""
    matches = []
    for row_index, row in iter_rows(cache_dir):
        if topic not in (row.get("topics_flat") or []):
            continue
        score, reasons = accessibility_score(row)
        matches.append(compact_record(row_index, row, score, reasons))
    return sorted(matches, key=lambda item: (-item["accessibility_score"], item["id"] or ""))


def main() -> None:
    """Run the exact-topic selector and write complete and shortlisted JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--cache-dir", type=Path, default=Path(".local-data/mathnet"))
    parser.add_argument("--output", type=Path, default=Path(".local-data/mathnet-quadratic-all.json"))
    parser.add_argument("--shortlist", type=Path, default=Path(".local-data/mathnet-quadratic-shortlist.json"))
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    ranked = select(args.topic, args.cache_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(ranked, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.shortlist.write_text(json.dumps(ranked[: args.limit], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Matched {len(ranked)} problems for {args.topic!r}.")
    print(f"Complete index: {args.output}")
    print(f"Shortlist: {args.shortlist}")


if __name__ == "__main__":
    main()
