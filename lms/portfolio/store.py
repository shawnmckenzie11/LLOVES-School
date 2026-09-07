"""Load cores, locked M1 exemplars, generated module JSON, and the rubric."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from paths import REPO_ROOT

PACKAGE_DIR = Path(__file__).resolve().parent
CORE_QUESTIONS_PATH = PACKAGE_DIR / "core_questions.json"
RUBRIC_PATH = PACKAGE_DIR / "rubric.json"
EXEMPLARS_DIR = PACKAGE_DIR / "exemplars"


def _read_json(path: Path) -> Any:
    """Parse a UTF-8 JSON file.

    Args:
        path: File to read.

    Returns:
        Parsed object.

    Raises:
        FileNotFoundError: Missing path.
        ValueError: Invalid JSON.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {path}") from exc


def load_core_questions() -> dict[str, Any]:
    """Return the stable CONNECT / JUSTIFY / TRANSFER course stems."""
    payload = _read_json(CORE_QUESTIONS_PATH)
    if not isinstance(payload, dict):
        raise ValueError("core_questions.json must be an object")
    return payload


def load_rubric() -> dict[str, Any]:
    """Return the Ontario portfolio rubric + classifier bullets."""
    payload = _read_json(RUBRIC_PATH)
    if not isinstance(payload, dict):
        raise ValueError("rubric.json must be an object")
    return payload


def exemplar_path(course_code: str, module_number: int) -> Path:
    """Repo path for a locked exemplar JSON.

    Args:
        course_code: Ontario code.
        module_number: 1-based module index.
    """
    code = str(course_code or "").strip().upper()
    return EXEMPLARS_DIR / code / f"M{int(module_number)}.json"


def generated_path(course_code: str, module_number: int, cache_root: Path | None = None) -> Path:
    """``.local-data/curriculum/{CODE}/portfolios/M{n}.json``.

    Args:
        course_code: Ontario code.
        module_number: 1-based module index.
        cache_root: Optional curriculum cache override.
    """
    try:
        from slide_builder import local_curriculum_course_dir
    except ImportError:
        from lms.slide_builder import local_curriculum_course_dir
    course_dir = local_curriculum_course_dir(course_code, cache_root=cache_root)
    return course_dir / "portfolios" / f"M{int(module_number)}.json"


def load_exemplar(course_code: str, module_number: int) -> dict[str, Any] | None:
    """Load a locked exemplar when present.

    Args:
        course_code: Ontario code.
        module_number: 1-based module index.
    """
    path = exemplar_path(course_code, module_number)
    if not path.is_file():
        return None
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else None


def load_generated(
    course_code: str,
    module_number: int,
    cache_root: Path | None = None,
) -> dict[str, Any] | None:
    """Load a previously generated module JSON from the curriculum cache.

    Args:
        course_code: Ontario code.
        module_number: 1-based module index.
        cache_root: Optional curriculum cache override.
    """
    path = generated_path(course_code, module_number, cache_root=cache_root)
    if not path.is_file():
        return None
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else None


def save_generated(
    payload: dict[str, Any],
    *,
    course_code: str,
    module_number: int,
    cache_root: Path | None = None,
) -> Path:
    """Write generated ``M{n}.json`` next to ``module-strand-map.json``.

    Refuses to overwrite a locked M1 exemplar in the repo. Generated copies
    still land under ``.local-data`` for later modules (and a working copy of M1).

    Args:
        payload: Portfolio module document.
        course_code: Ontario code.
        module_number: 1-based module index.
        cache_root: Optional curriculum cache override.

    Returns:
        Path written.
    """
    dest = generated_path(course_code, module_number, cache_root=cache_root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest


def local_submissions_dir(
    course_code: str,
    module_number: int,
    *,
    repo_root: Path | None = None,
) -> Path:
    """Local fallback folder ``.local-data/portfolio-submissions/{CODE}/M{n}/``.

    Args:
        course_code: Ontario code.
        module_number: 1-based module index.
        repo_root: Repo root override (tests).
    """
    root = Path(repo_root or REPO_ROOT)
    code = str(course_code or "").strip().upper() or "_"
    return root / ".local-data" / "portfolio-submissions" / code / f"M{int(module_number)}"


def load_module_portfolio(
    course_code: str,
    module_number: int,
    cache_root: Path | None = None,
) -> dict[str, Any] | None:
    """Prefer locked exemplar for M1, else generated JSON.

    Args:
        course_code: Ontario code.
        module_number: 1-based module index.
        cache_root: Optional curriculum cache override.
    """
    if int(module_number) == 1:
        locked = load_exemplar(course_code, 1)
        if locked is not None:
            return locked
    generated = load_generated(course_code, module_number, cache_root=cache_root)
    if generated is not None:
        return generated
    return load_exemplar(course_code, module_number)
