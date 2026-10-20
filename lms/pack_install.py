"""Lean module-pack install and restart recovery for IT course assignment.

The upload handler stores ``course.imscc`` and returns. Unpack, ingest, and
syllabus seed run afterward. If that worker dies, the cartridge is still on
disk and :func:`resume_interrupted_packs` runs the same steps again. Ingest
upserts, so a retry does not duplicate rows.
"""

from __future__ import annotations

import fcntl
import logging
import os
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


def schedule_pack_resume(school: Any) -> None:
    """Resume abandoned cartridge installs on a daemon thread.

    Boot calls this once so ``create_app`` can finish serving while a
    saved pack is retried. The thread is a daemon, so process shutdown
    does not wait on it.

    Args:
        school: Open school database.
    """

    def _run() -> None:
        """Scan library folders and retry abandoned installs."""
        try:
            resume_interrupted_packs(school)
        except Exception:
            logger.exception("module pack resume failed")

    threading.Thread(target=_run, name="pack-resume", daemon=True).start()



def _claim_install(dest_root: Path) -> int | None:
    """Take an exclusive lock so two workers do not install one library.

    Args:
        dest_root: ``libraries/<id>/`` folder.

    Returns:
        Open file descriptor to hold until release, or None if another
        process already holds the lock.
    """
    path = Path(dest_root) / "install.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    return fd


def _release_install(fd: int) -> None:
    """Release an install lock acquired by :func:`_claim_install`.

    Args:
        fd: File descriptor returned by the claim.
    """
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _discard_unpacked(unpacked: Path) -> None:
    """Delete a materialized cartridge tree after its rows are stored.

    Args:
        unpacked: Working tree under the library folder.
    """
    try:
        if Path(unpacked).is_dir():
            shutil.rmtree(unpacked, ignore_errors=True)
    except OSError:
        logger.warning("Could not remove unpacked tree")


@contextmanager
def _heartbeat(dest_root: Path, stage: str, detail: str) -> Iterator[None]:
    """Refresh install status while a long stage runs.

    Args:
        dest_root: Library folder.
        stage: Busy stage name.
        detail: Teacher-facing line to leave unchanged.
    """
    from modules import write_pack_status

    stop = threading.Event()

    def beat() -> None:
        """Rewrite pack status until the surrounding stage finishes."""
        while not stop.wait(8.0):
            write_pack_status(dest_root, stage=stage, detail=detail)

    worker = threading.Thread(target=beat, daemon=True, name="pack-heartbeat")
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=1.0)


def _offering_for_library(school: Any, library_id: int) -> int | None:
    """Return one offering id attached to ``library_id``, if any.

    Args:
        school: Open school database.
        library_id: ``content_libraries.id``.
    """
    with school._lock:  # noqa: SLF001 - shared connection lock
        row = school.conn.execute(
            """
            SELECT id FROM course_offerings
            WHERE library_id = ?
            ORDER BY id
            LIMIT 1
            """,
            (int(library_id),),
        ).fetchone()
    if row is None:
        return None
    return int(row["id"])


def install_saved_module_pack(
    school: Any,
    offering_id: int,
    stored: Path,
    dest_root: Path,
) -> None:
    """Validate, stream, ingest, and seed one stored cartridge.

    Safe to call again after a crash. A second call while the first holds
    the install lock returns without changing status.

    Args:
        school: Open school database.
        offering_id: ``course_offerings.id`` that owns the library.
        stored: Path to ``course.imscc``.
        dest_root: ``libraries/<id>/`` folder.
    """
    try:
        from components import ensure_ingested
        from modules import (
            explain_pack_exception,
            install_uploaded_module_pack,
            write_pack_status,
        )
        from pack_progress import finish_install_detail
        from syllabus_seed import seed_syllabus_from_due_dates
    except ImportError:
        from lms.components import ensure_ingested
        from lms.modules import (
            explain_pack_exception,
            install_uploaded_module_pack,
            write_pack_status,
        )
        from lms.pack_progress import finish_install_detail
        from lms.syllabus_seed import seed_syllabus_from_due_dates

    dest_root = Path(dest_root)
    stored = Path(stored)
    lock = _claim_install(dest_root)
    if lock is None:
        logger.info("Pack install already running for library %s", dest_root.name)
        return
    try:
        try:
            status = install_uploaded_module_pack(stored, dest_root)
            if not status.get("ok"):
                return
            school.set_offering_imscc(int(offering_id), str(stored))
            offering = school.get_offering(int(offering_id))
            library_id = offering.get("library_id")
            unpacked = dest_root / "unpacked"
            if library_id and unpacked.is_dir():
                ingest_detail = "Loading modules, pages, and assessments…"
                write_pack_status(dest_root, stage="ingest", detail=ingest_detail)
                with _heartbeat(dest_root, "ingest", ingest_detail):
                    ensure_ingested(
                        school,
                        school.data_dir,
                        int(library_id),
                        unpacked,
                        force=True,
                    )
                _discard_unpacked(unpacked)
                write_pack_status(
                    dest_root,
                    stage="syllabus",
                    detail="Placing tests, quizzes, and assignments on the syllabus…",
                )
                try:
                    seed_syllabus_from_due_dates(
                        school, offering, data_dir=school.data_dir
                    )
                except Exception:  # noqa: BLE001 — pack still usable without seed
                    logger.exception("Syllabus seed from due dates failed")
            write_pack_status(
                dest_root,
                stage="done",
                detail=finish_install_detail(school, library_id),
            )
        except Exception as exc:  # noqa: BLE001 — surface on the status poll
            logger.exception("Background module-pack install failed")
            message = explain_pack_exception(exc)
            write_pack_status(dest_root, stage="error", detail=message, error=message)
    finally:
        _release_install(lock)


def resume_interrupted_packs(school: Any) -> int:
    """Retry lean installs left unfinished when a worker died.

    Scans ``libraries/<id>/`` for a ``course.imscc`` whose status is an
    abandoned busy stage. Corrupt packs are left alone.

    Args:
        school: Open ``SchoolDB``.

    Returns:
        How many library folders were retried.
    """
    try:
        from modules import pack_needs_resume
    except ImportError:
        from lms.modules import pack_needs_resume

    root = Path(school.data_dir) / "libraries"
    if not root.is_dir():
        return 0
    retried = 0
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        if not folder.name.isdigit():
            continue
        if not pack_needs_resume(folder):
            continue
        offering_id = _offering_for_library(school, int(folder.name))
        if offering_id is None:
            logger.info("Skipping pack resume for library %s; no offering", folder.name)
            try:
                from modules import write_pack_status
            except ImportError:
                from lms.modules import write_pack_status
            message = (
                "Module pack install stopped before it finished. "
                "Upload the .imscc again."
            )
            write_pack_status(folder, stage="error", detail=message, error=message)
            continue
        install_saved_module_pack(
            school, offering_id, folder / "course.imscc", folder
        )
        retried += 1
    return retried
