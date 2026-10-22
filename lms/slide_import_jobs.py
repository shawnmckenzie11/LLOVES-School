"""Background slide import jobs.

Conversion and link download run off the request thread so a deck that
takes half a minute does not sit inside the worker timeout. The job row
stores status and a small JSON result, never the PowerPoint or the PNGs.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Link download stays at the existing 45 second cap. Office conversion
# plus rendering stays under this budget, inside the 120 second worker.
JOB_BUDGET_SECONDS = 90
# Two LibreOffice conversions at once. Further imports wait for a slot.
MAX_CONCURRENT_SLIDE_IMPORTS = 2
_IMPORT_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_SLIDE_IMPORTS)


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
            imports run at once; the rest wait for a free slot.
    """

    def runner() -> None:
        """Record success or a teacher-facing failure for one job."""
        if not _IMPORT_SLOTS.acquire(timeout=30):
            school.fail_slide_import_job(
                job_id,
                "Another deck is still being attached. Try again in a minute.",
                needs_slides_read=False,
                http_status=400,
            )
            return
        try:
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
            _IMPORT_SLOTS.release()

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
