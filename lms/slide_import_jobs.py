"""Background slide import jobs.

Conversion and link download run off the request thread so a deck that
takes half a minute does not sit inside the worker timeout. The job row
stores status and a small JSON result, never the PowerPoint or the PNGs.
"""

from __future__ import annotations

import fcntl
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Slot wait is separate. Conversion and rendering stop at 80 seconds.
# This clock starts when the job takes a slot, and it stays above that
# 80 seconds so a deck that finishes on time is still running at save.
JOB_BUDGET_SECONDS = 100
SLOT_WAIT_SECONDS = 30
# Two LibreOffice conversions at once, shared by every gunicorn worker.
MAX_CONCURRENT_SLIDE_IMPORTS = 2
_SLOT_DIR = Path("/tmp/lloves-slide-import-slots")


class _HeldImportSlot:
    """One machine-wide import slot held by an exclusive flock.

    The lock lives on the open file, so it drops when this process
    exits, including a gunicorn worker that dies mid-import.

    Args:
        fd: Descriptor that already holds ``LOCK_EX``.
    """

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def release(self) -> None:
        """Unlock the slot and close its descriptor."""
        fd = self._fd
        self._fd = -1
        if fd < 0:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def acquire_import_slot(timeout: float = 30) -> _HeldImportSlot | None:
    """Take one of the two machine-wide slide import slots.

    ``flock`` is per open-file description, so threads in one worker
    and processes in the other workers share the same two slots.
    A worker that dies releases its slot without a decrement.

    Args:
        timeout: Seconds to wait for a free slot.

    Returns:
        A slot whose ``release`` drops the lock, or None when the wait
        expires.
    """
    deadline = time.monotonic() + timeout
    fds: list[int] = []
    _SLOT_DIR.mkdir(mode=0o755, exist_ok=True)
    try:
        for index in range(MAX_CONCURRENT_SLIDE_IMPORTS):
            path = _SLOT_DIR / f"slot-{index}"
            fds.append(os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o644))
        while True:
            for index, fd in enumerate(fds):
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                fds[index] = -1
                return _HeldImportSlot(fd)
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)
    finally:
        for fd in fds:
            if fd >= 0:
                os.close(fd)


class SlideImportFailed(Exception):
    """The import finished with an error the teacher can see.

    Args:
        message: Staff-facing text.
        needs_slides_read: True when the deck needs the read-only consent.
    """

    def __init__(self, message: str, *, needs_slides_read: bool = False) -> None:
        super().__init__(message)
        self.needs_slides_read = bool(needs_slides_read)


def start_slide_import(school: Any, job_id: str, work: Callable[[], dict]) -> None:
    """Run ``work`` on a daemon thread and record the outcome.

    Args:
        school: ``SchoolDB`` with the slide-import job methods.
        job_id: Row created by the request before this call.
        work: Returns the JSON object stored on success. It must not
            touch the Flask request. Raise ``SlideImportFailed`` or
            ``ValueError`` for a teacher-facing error. At most two
            imports run at once on this machine; the rest wait for a free slot.
            The budget starts when a slot is taken, not while the job waits.
    """

    def runner() -> None:
        """Record success or a teacher-facing failure for one job."""
        slot = None
        try:
            expire = getattr(school, "fail_expired_slide_import_jobs", None)
            if expire is not None:
                expire(JOB_BUDGET_SECONDS)
            slot = acquire_import_slot(SLOT_WAIT_SECONDS)
            if slot is None:
                school.fail_slide_import_job(
                    job_id,
                    "Another deck is still being attached. Try again in a minute.",
                    needs_slides_read=False,
                    http_status=400,
                )
                return
            note = getattr(school, "note_slide_import_started", None)
            if note is not None:
                note(job_id)
            result = work()
            if not isinstance(result, dict):
                raise SlideImportFailed("Could not attach that deck.")
            school.finish_slide_import_job(job_id, result)
        except SlideImportFailed as exc:
            school.fail_slide_import_job(
                job_id,
                str(exc),
                needs_slides_read=exc.needs_slides_read,
                http_status=403 if exc.needs_slides_read else 400,
            )
        except ValueError as exc:
            school.fail_slide_import_job(
                job_id, str(exc), needs_slides_read=False, http_status=400
            )
        except Exception:
            logger.exception("slide import failed")
            school.fail_slide_import_job(
                job_id,
                "Could not attach that deck.",
                needs_slides_read=False,
                http_status=502,
            )
        finally:
            if slot is not None:
                slot.release()

    threading.Thread(
        target=runner,
        name="slide-import",
        daemon=True,
    ).start()


def follow_slide_import(client: Any, response: Any, *, timeout: float = 30) -> Any:
    """Poll a test client until a slide import leaves ``running``.

    Args:
        client: Flask test client signed in as the same teacher.
        response: POST response. Non-202 responses are returned as-is.
        timeout: Seconds to wait for a terminal status.

    Returns:
        The terminal poll response, or ``response`` when it was not a job.
    """
    if getattr(response, "status_code", None) != 202:
        return response
    body = response.get_json() or {}
    job_id = str(body.get("job_id") or "")
    deadline = time.monotonic() + timeout
    latest = response
    while time.monotonic() < deadline:
        latest = client.get(f"/api/slide-imports/{job_id}")
        data = latest.get_json() or {}
        if data.get("status") != "running":
            return latest
        time.sleep(0.02)
    return latest
