"""Local Import banks when the tip sqlite has no IMSCC cartridge.

Alc Import lists question banks ingested from the Admin IMSCC on Fly
``/data``. That cartridge is not in git. A fresh local library is an upload
with no module-test banks, so confirmed links are only Course Wide warmups.
Core Math hides those warmups, and Import looks empty (MCF3M Module 2
included).

This module fills that gap when ``LOCAL_DEV_LOGIN`` is on:

- content-builder catalogue MC items for every course/module directory
- constructed items in ``.local-data/curriculum/{CODE}/banks`` as open prompts

It does not copy Fly data, does not invent answer keys, and does not run in
production. Alc's extra IMSCC rows stay on alc until a cartridge is uploaded
locally. Courses whose curriculum cache files are empty stay empty here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from bank_kinds import retag_module2_teaching_today
    from builder_bank_seed import seed_builder_module_bank
    from slide_builder import local_curriculum_course_dir
except ImportError:
    from lms.bank_kinds import retag_module2_teaching_today
    from lms.builder_bank_seed import seed_builder_module_bank
    from lms.slide_builder import local_curriculum_course_dir

try:
    from bank_lint import lints_library_after
except ImportError:  # package import
    from lms.bank_lint import lints_library_after


def is_curriculum_open(payload: Any) -> bool:
    """True when a stored row is a constructed curriculum prompt.

    Args:
        payload: Parsed ``questions.payload_json``.
    """
    return isinstance(payload, dict) and bool(payload.get("curriculum_open"))


def normalize_curriculum_open(
    *,
    question_id: int,
    bank_id: int,
    title: str,
    payload: Any,
    bank_title: str = "",
) -> dict[str, Any] | None:
    """Turn one curriculum cache row into an importable open prompt.

    No answer key is invented. The live item is a poll so publish does not
    score it as multiple choice.

    Args:
        question_id: ``questions.id``.
        bank_id: ``question_banks.id``.
        title: Question title.
        payload: Parsed payload. Must have ``curriculum_open``.
        bank_title: Bank label copied onto the normalized row.

    Returns:
        Normalized item, or ``None`` when the stem is empty.
    """
    blob = payload if isinstance(payload, dict) else {}
    if not is_curriculum_open(blob):
        return None
    stem = str(blob.get("stem_html") or blob.get("text") or title or "").strip()
    if not stem:
        return None
    normalized = {
        "type": "poll",
        "text": stem,
        "prompt": stem,
        "options": [],
        "choices": [],
        "points": 1,
        "curriculum_open": True,
        "response_mode": "individual",
        "publish_modes": ["individual"],
        "question_id": int(question_id),
        "bank_id": int(bank_id),
        "bank_title": str(bank_title or ""),
        "question_title": str(title or stem)[:80],
        "source_question_id": int(question_id),
        "source_bank_id": int(bank_id),
    }
    image_urls: list[str] = []
    raw_urls = blob.get("image_urls")
    if isinstance(raw_urls, list):
        image_urls.extend(str(url).strip() for url in raw_urls if str(url).strip())
    single = str(blob.get("image_url") or "").strip()
    if single and single not in image_urls:
        image_urls.insert(0, single)
    if image_urls:
        normalized["image_urls"] = image_urls
        normalized["image_url"] = image_urls[0]
    return normalized


def _catalogue_modules(course_code: str) -> list[int]:
    """Return module numbers that have a content-builder bank directory.

    Args:
        course_code: Ontario course code.
    """
    try:
        from builder_bank_seed import _BUILDER_BANKS
    except ImportError:
        from lms.builder_bank_seed import _BUILDER_BANKS

    root = _BUILDER_BANKS / str(course_code or "").strip().upper()
    if not root.is_dir():
        return []
    numbers: list[int] = []
    for path in sorted(root.glob("M*")):
        if not path.is_dir():
            continue
        suffix = path.name[1:]
        if suffix.isdigit() and 1 <= int(suffix) <= 8:
            numbers.append(int(suffix))
    return numbers


def _iter_curriculum_items(
    course_code: str, cache_root: Path | None
) -> list[tuple[int, dict[str, Any]]]:
    """Read constructed items from the local curriculum cache.

    Args:
        course_code: Ontario course code.
        cache_root: Override of ``.local-data/curriculum`` (tests).

    Returns:
        ``(module_number, item)`` pairs with a non-empty stem.
    """
    banks = local_curriculum_course_dir(course_code, cache_root) / "banks"
    if not banks.is_dir():
        return []
    found: list[tuple[int, dict[str, Any]]] = []
    for module_dir in sorted(banks.glob("M*")):
        if not module_dir.is_dir():
            continue
        suffix = module_dir.name[1:]
        if not suffix.isdigit():
            continue
        module_number = int(suffix)
        if not 1 <= module_number <= 8:
            continue
        for path in sorted(module_dir.glob("*/items.json")):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            raw_items = document.get("items") if isinstance(document, dict) else document
            if not isinstance(raw_items, list):
                continue
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                stem = str(item.get("stem") or "").strip()
                if stem:
                    found.append((module_number, item))
    return found


def _payload_for_curriculum_item(
    course_code: str,
    module_number: int,
    item: dict[str, Any],
    *,
    stem: str,
    smart_id: str,
    cache_root: Path | None,
) -> dict[str, Any]:
    """Build the stored payload for one curriculum or cartridge bank item.

    Cartridge rows carry choices and are real multiple-choice questions.
    Older constructed rows stay open prompts.

    Args:
        course_code: Ontario course code.
        module_number: One-based module index.
        item: Cache item.
        stem: Plain or HTML stem already required to be non-empty.
        smart_id: Stable id used as the import key.
        cache_root: Optional curriculum root for figure files.
    """
    try:
        from curriculum_bank_images import (
            figure_payload_for_item,
            stored_figure_fields,
        )
    except ImportError:
        from lms.curriculum_bank_images import (
            figure_payload_for_item,
            stored_figure_fields,
        )

    choices = item.get("choices")
    if str(item.get("origin") or "") == "canvas_cartridge" and isinstance(choices, list):
        payload = {
            "stem_html": str(item.get("stem_html") or stem),
            "text": stem,
            "points_possible": float(item.get("points_possible") or 1.0),
            "choices": choices,
            "correct_ids": list(item.get("correct_ids") or []),
            "smart_id": smart_id,
            "canvas_source": str(item.get("canvas_source") or ""),
        }
        feedback = str(item.get("feedback_html") or "").strip()
        if feedback:
            payload["feedback_html"] = feedback
        payload.update(
            stored_figure_fields(
                figure_payload_for_item(
                    course_code,
                    module_number,
                    item,
                    cache_root=cache_root,
                )
            )
        )
        urls = item.get("image_urls")
        if isinstance(urls, list) and urls:
            payload["image_urls"] = [str(url) for url in urls if str(url).strip()]
            payload["image_url"] = payload["image_urls"][0]
        return payload
    payload = {
        "stem_html": stem,
        "text": stem,
        "curriculum_open": True,
        "points_possible": 1.0,
        "choices": [],
        "smart_id": smart_id,
    }
    payload.update(
        stored_figure_fields(
            figure_payload_for_item(
                course_code,
                module_number,
                item,
                cache_root=cache_root,
            )
        )
    )
    return payload


def _retire_replaced_questions(
    school: Any,
    *,
    library_id: int,
    course_code: str,
    module_number: int,
    bank_id: int,
    keep_keys: set[str],
    now: str,
) -> None:
    """Move questions this reseed replaced onto an unlinked bank.

    Past live cards store ``source_question_id`` with no foreign key.
    The row stays so a lookup still finds it. It leaves the module bank,
    so the new cartridge questions are what teachers see. The caller
    holds ``school._lock``.

    Args:
        school: ``SchoolDB`` instance.
        library_id: ``content_libraries.id``.
        course_code: Ontario course code.
        module_number: One-based module index.
        bank_id: Module bank the new questions use.
        keep_keys: Import keys that stay on ``bank_id``.
        now: ISO timestamp for the retired bank row.
    """
    code = str(course_code or "").strip().upper()
    retired_key = f"retired-curriculum:{code}:M{int(module_number)}"
    existing = school.conn.execute(
        """
        SELECT id FROM question_banks
        WHERE library_id = ? AND import_key = ?
        """,
        (int(library_id), retired_key),
    ).fetchone()
    if existing is None:
        cur = school.conn.execute(
            """
            INSERT INTO question_banks (
                library_id, import_key, title, settings_json, created_at
            ) VALUES (?, ?, ?, '{}', ?)
            """,
            (
                int(library_id),
                retired_key,
                f"{code} Module {int(module_number)} retired questions",
                now,
            ),
        )
        retired_id = int(cur.lastrowid)
    else:
        retired_id = int(existing["id"])
    rows = school.conn.execute(
        "SELECT id, import_key FROM questions WHERE bank_id = ?",
        (int(bank_id),),
    ).fetchall()
    for row in rows:
        if str(row["import_key"]) in keep_keys:
            continue
        school.conn.execute(
            "UPDATE questions SET bank_id = ? WHERE id = ?",
            (retired_id, int(row["id"])),
        )


def _upsert_curriculum_bank(
    school: Any,
    *,
    library_id: int,
    course_code: str,
    module_number: int,
    items: list[dict[str, Any]],
    cache_root: Path | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Insert or refresh one module's curriculum open prompts.

    Rows are stored as ``multiple_choice_question`` so the Import SQL
    filter can see them. ``curriculum_open`` tells search to skip the MC
    key requirement.

    Args:
        school: ``SchoolDB`` instance.
        library_id: ``content_libraries.id``.
        course_code: Ontario course code.
        module_number: One-based module index.
        items: Cache items for this module.
        cache_root: Optional curriculum root. Figure files are read from
            here for MCF3M modules 5–7 only.
        commit: When False, leave the caller's SQL transaction open.
            The caller commits the inserts, retire moves, and any
            settings row together. This connection is in autocommit
            mode, so ``Connection.commit`` would end that ``BEGIN``.

    Returns:
        Summary with ``bank_id`` and ``count``.
    """
    from datetime import datetime, timezone

    code = str(course_code or "").strip().upper()
    import_key = f"curriculum-bank:{code}:M{int(module_number)}"
    title = f"{code} Module {int(module_number)} curriculum bank"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    question_ids: list[int] = []
    with school._lock:
        existing = school.conn.execute(
            """
            SELECT id FROM question_banks
            WHERE library_id = ? AND import_key = ?
            """,
            (int(library_id), import_key),
        ).fetchone()
        if existing is None:
            cur = school.conn.execute(
                """
                INSERT INTO question_banks (
                    library_id, import_key, title, settings_json, created_at
                ) VALUES (?, ?, ?, '{}', ?)
                """,
                (int(library_id), import_key, title, now),
            )
            bank_id = int(cur.lastrowid)
        else:
            bank_id = int(existing["id"])
            school.conn.execute(
                "UPDATE question_banks SET title = ? WHERE id = ?",
                (title, bank_id),
            )
        try:
            from curriculum_bank_images import (
                figure_payload_for_item,
                stored_figure_fields,
            )
        except ImportError:
            from lms.curriculum_bank_images import (
                figure_payload_for_item,
                stored_figure_fields,
            )
        keep_keys: set[str] = set()
        cartridge_module = any(
            str(item.get("origin") or "") == "canvas_cartridge" for item in items
        )
        for item in items:
            smart_id = str(item.get("smart_id") or item.get("id") or "").strip()
            stem = str(item.get("stem") or "").strip()
            if not smart_id:
                smart_id = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:16]
            cartridge = str(item.get("origin") or "") == "canvas_cartridge"
            q_key = (
                f"cartridge:{code}:M{int(module_number)}:{smart_id}"
                if cartridge
                else f"curriculum:{code}:M{int(module_number)}:{smart_id}"
            )
            keep_keys.add(q_key)
            payload = _payload_for_curriculum_item(
                code,
                int(module_number),
                item,
                stem=stem,
                smart_id=smart_id,
                cache_root=cache_root,
            )
            encoded = json.dumps(payload)
            q_title = stem.replace("\n", " ")[:80] or smart_id
            found = school.conn.execute(
                """
                SELECT id FROM questions
                WHERE bank_id = ? AND import_key = ?
                """,
                (bank_id, q_key),
            ).fetchone()
            if found is None:
                cur = school.conn.execute(
                    """
                    INSERT INTO questions (
                        bank_id, import_key, item_type, title, payload_json, created_at
                    ) VALUES (?, ?, 'multiple_choice_question', ?, ?, ?)
                    """,
                    (bank_id, q_key, q_title, encoded, now),
                )
                question_ids.append(int(cur.lastrowid))
            else:
                school.conn.execute(
                    """
                    UPDATE questions
                    SET title = ?, payload_json = ?
                    WHERE id = ?
                    """,
                    (q_title, encoded, int(found["id"])),
                )
                question_ids.append(int(found["id"]))
        if cartridge_module:
            _retire_replaced_questions(
                school,
                library_id=int(library_id),
                course_code=code,
                module_number=int(module_number),
                bank_id=bank_id,
                keep_keys=keep_keys,
                now=now,
            )
        if commit:
            # Autocommit mode: this is a no-op unless a BEGIN is open.
            # The boot apply passes commit=False and issues SQL COMMIT.
            school.conn.commit()
    if commit:
        # Reseeds an existing library, not only a new one. The link helper
        # drops the bank cache; say so here so a later edit does not drop it.
        school._ensure_module_bank_link(int(library_id), int(module_number), bank_id)
        invalidate = getattr(school, "invalidate_module_bank_mc_cache", None)
        if callable(invalidate):
            invalidate(int(library_id))
    return {
        "bank_id": bank_id,
        "module_number": int(module_number),
        "count": len(question_ids),
    }


@lints_library_after
def seed_library_import_banks(
    school: Any,
    library_id: int,
    course_code: str,
    *,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    """Link catalogue MCs and curriculum open prompts for one library.

    Safe to call from tests with ``cache_root``. Does not check
    ``LOCAL_DEV_LOGIN``; the local-dev wrapper does.

    Args:
        school: ``SchoolDB`` instance.
        library_id: ``content_libraries.id``.
        course_code: Ontario course code on the library.
        cache_root: Optional curriculum cache root.

    Returns:
        Counts for catalogue modules, curriculum questions, and retags.
    """
    code = str(course_code or "").strip().upper()
    catalogue: list[dict[str, Any]] = []
    for module_number in _catalogue_modules(code):
        summary = seed_builder_module_bank(
            school,
            library_id=int(library_id),
            course_code=code,
            module_number=int(module_number),
        )
        if int(summary.get("count") or 0):
            school._ensure_module_bank_link(
                int(library_id), int(module_number), int(summary["bank_id"])
            )
            catalogue.append(summary)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for module_number, item in _iter_curriculum_items(code, cache_root):
        grouped.setdefault(int(module_number), []).append(item)
    curriculum: list[dict[str, Any]] = []
    for module_number, items in sorted(grouped.items()):
        curriculum.append(
            _upsert_curriculum_bank(
                school,
                library_id=int(library_id),
                course_code=code,
                module_number=int(module_number),
                items=items,
                cache_root=cache_root,
            )
        )
    retagged = retag_module2_teaching_today(school, int(library_id))
    # Runs on every local library, including ones that already have banks.
    invalidate = getattr(school, "invalidate_module_bank_mc_cache", None)
    if callable(invalidate):
        invalidate(int(library_id))
    return {
        "course_code": code,
        "library_id": int(library_id),
        "catalogue": catalogue,
        "curriculum": curriculum,
        "curriculum_questions": sum(int(row["count"]) for row in curriculum),
        "teaching_today_retagged": int(retagged),
    }


def seed_local_question_banks(school: Any, *, cache_root: Path | None = None) -> dict[str, Any]:
    """Populate Import banks for every local library when local-dev is on.

    Args:
        school: ``SchoolDB`` instance.
        cache_root: Optional curriculum cache root.

    Returns:
        Per-library summaries, or a skipped marker when local-dev is off.
    """
    try:
        from local_dev_seed import local_dev_login_enabled
    except ImportError:
        from lms.local_dev_seed import local_dev_login_enabled

    if not local_dev_login_enabled():
        return {"skipped": True, "reason": "LOCAL_DEV_LOGIN is off"}
    rows = school.conn.execute(
        """
        SELECT id, ontario_code FROM content_libraries
        WHERE origin NOT IN ('shell', 'backfill-staging')
        """
    ).fetchall()
    libraries = [
        seed_library_import_banks(
            school,
            int(row["id"]),
            str(row["ontario_code"] or ""),
            cache_root=cache_root,
        )
        for row in rows
    ]
    return {"skipped": False, "libraries": libraries}


_MCK472_MARKER = "mck472_mcf3m_m567_cartridge_v2"
_MCK472_MODULES = (5, 6, 7)
_CARTRIDGE_NAME_RE = re.compile(
    r"/curriculum-bank-images/MCF3M/cartridge/([A-Za-z0-9][A-Za-z0-9._-]*)"
)


def _default_cartridge_image_dir() -> Path:
    """Return the committed directory the cartridge image route reads.

    Returns:
        ``lms/seeds/mcf3m_m567_images``.
    """
    try:
        from curriculum_bank_images import CARTRIDGE_IMAGE_DIR
    except ImportError:
        from lms.curriculum_bank_images import CARTRIDGE_IMAGE_DIR

    return CARTRIDGE_IMAGE_DIR


def _referenced_cartridge_names(items: list[dict[str, Any]]) -> list[str]:
    """Return cartridge filenames referenced by these bank items.

    Args:
        items: Curriculum items for MCF3M modules 5–7.

    Returns:
        Filenames in first-seen order. A name never contains a slash.
    """
    found: list[str] = []
    seen: set[str] = set()
    for item in items:
        blob = json.dumps(item, ensure_ascii=False)
        for match in _CARTRIDGE_NAME_RE.finditer(blob):
            name = match.group(1)
            if name in seen:
                continue
            seen.add(name)
            found.append(name)
    return found


def _cartridge_image_gaps(items: list[dict[str, Any]], image_dir: Path) -> list[str]:
    """Return why these items cannot be applied yet.

    Args:
        items: Curriculum items whose figure URLs must exist on disk.
        image_dir: Directory that must contain every referenced file.

    Returns:
        A one-element list when ``image_dir`` is missing, otherwise the
        referenced filenames that are not files in that directory.
        Empty when every referenced figure is present.
    """
    if not image_dir.is_dir():
        return [f"missing directory {image_dir}"]
    return [
        name
        for name in _referenced_cartridge_names(items)
        if not (image_dir / name).is_file()
    ]


def _mcf3m_m567_bank_digest() -> str:
    """Hash the committed MCF3M module 5–7 bank files.

    Returns:
        SHA-256 of those three ``items.json`` files. A missing file still
        changes the hash, so a partial checkout is applied again when the
        files arrive.
    """
    try:
        from slide_builder import local_curriculum_course_dir
    except ImportError:
        from lms.slide_builder import local_curriculum_course_dir

    banks = local_curriculum_course_dir("MCF3M", None) / "banks"
    hasher = hashlib.sha256()
    for module_number, strand in ((5, "C"), (6, "C"), (7, "B")):
        rel = f"M{module_number}/{strand}/items.json"
        hasher.update(rel.encode("utf-8"))
        path = banks / rel
        hasher.update(path.read_bytes() if path.is_file() else b"missing")
    return hasher.hexdigest()


def apply_mcf3m_m567_cartridge_banks(
    school: Any,
    *,
    image_dir: Path | None = None,
) -> dict[str, Any]:
    """Load MCF3M module 5–7 cartridge banks on boot, including production.

    ``seed_local_question_banks`` runs only when ``LOCAL_DEV_LOGIN`` is on,
    so a production process never called it. This applies the same upsert
    for every library whose course code is MCF3M, and only for modules 5,
    6, and 7. Rows are updated by import key. Replaced rows move to the
    unlinked retired bank. Nothing is deleted, and the old question ids
    stay loadable.

    The writes go to ``school.conn``, the sqlite catalogue on the Fly
    volume. Live-presence Postgres is a different store and does not
    hold these banks.

    Before any row changes, every figure those questions reference must
    be a file in ``image_dir`` (the committed seed directory by default).
    A missing directory or file logs a warning and returns without
    inserting questions, moving retired rows, or writing the settings
    key, so the next boot can try again.

    When the figures are present, the inserts, retire moves, module
    links, and the settings key commit in one ``BEGIN IMMEDIATE``.
    A ``school_settings`` marker stores the bank-file hash. The next boot
    with the same files does no writes. A new hash runs the upsert again.
    A database with no MCF3M library yet, which is a fresh local seed
    before the demo library exists, returns without writing the marker.
    ``SchoolDB.__init__`` calls this on every process after the schema
    exists. Local dev still loads the banks when it creates that library.

    Args:
        school: ``SchoolDB`` instance.
        image_dir: Directory of cartridge figures. ``None`` uses
            ``lms/seeds/mcf3m_m567_images``. Tests pass a temporary
            directory.

    Returns:
        ``libraries``, ``questions`` inserted on this pass, ``skipped``,
        and ``applied``. ``reason`` is ``missing-images`` when the apply
        was refused.
    """
    from datetime import datetime, timezone

    digest = _mcf3m_m567_bank_digest()
    rows = school.conn.execute(
        """
        SELECT id FROM content_libraries
        WHERE UPPER(COALESCE(ontario_code, '')) = 'MCF3M'
          AND COALESCE(origin, '') NOT IN ('shell', 'backfill-staging')
        """
    ).fetchall()
    if not rows:
        return {"libraries": 0, "questions": 0, "skipped": True, "applied": False}
    current = school.conn.execute(
        "SELECT value FROM school_settings WHERE key = ?",
        (_MCK472_MARKER,),
    ).fetchone()
    if current is not None and str(current["value"] or "") == digest:
        return {
            "libraries": len(rows),
            "questions": 0,
            "skipped": True,
            "applied": False,
        }
    grouped: dict[int, list[dict[str, Any]]] = {}
    for module_number, item in _iter_curriculum_items("MCF3M", None):
        if int(module_number) in _MCK472_MODULES:
            grouped.setdefault(int(module_number), []).append(item)
    figures = image_dir if image_dir is not None else _default_cartridge_image_dir()
    pending = [item for module_items in grouped.values() for item in module_items]
    gaps = _cartridge_image_gaps(pending, figures)
    if gaps:
        sample = ", ".join(gaps[:5])
        logger.warning(
            "MCF3M M5-M7 cartridge banks not applied; %s figure gap(s) under %s (%s)",
            len(gaps),
            figures,
            sample,
        )
        return {
            "libraries": len(rows),
            "questions": 0,
            "skipped": True,
            "applied": False,
            "reason": "missing-images",
        }
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    questions = 0
    with school._lock:
        if school.conn.in_transaction:
            school.conn.execute("COMMIT")
        school.conn.execute("BEGIN IMMEDIATE")
        try:
            for row in rows:
                library_id = int(row["id"])
                for module_number in _MCK472_MODULES:
                    items = grouped.get(module_number) or []
                    if not items:
                        continue
                    summary = _upsert_curriculum_bank(
                        school,
                        library_id=library_id,
                        course_code="MCF3M",
                        module_number=module_number,
                        items=items,
                        cache_root=None,
                        commit=False,
                    )
                    questions += int(summary.get("count") or 0)
                    school.conn.execute(
                        """
                        INSERT OR IGNORE INTO course_module_bank_links (
                            library_id, module_number, bank_id, confirmed_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            library_id,
                            int(module_number),
                            int(summary["bank_id"]),
                            now,
                        ),
                    )
            school.conn.execute(
                """
                INSERT INTO school_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (_MCK472_MARKER, digest, now),
            )
            school.conn.execute("COMMIT")
        except Exception:
            if school.conn.in_transaction:
                school.conn.execute("ROLLBACK")
            raise
    for row in rows:
        invalidate = getattr(school, "invalidate_module_bank_mc_cache", None)
        if callable(invalidate):
            invalidate(int(row["id"]))
    return {
        "libraries": len(rows),
        "questions": questions,
        "skipped": False,
        "applied": True,
    }
