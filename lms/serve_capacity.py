"""Capacity limits for the one 1 GB Fly machine.

Alc on Fly v189 (1 worker, 2 threads, timeout 600) stayed wedged through a
soft restart and a hard restart: clients re-flooded the two threads and
``/health`` never returned. The machine command that restored ``/health``
200 on that same shared-cpu-1x / 1 GB box was 4 workers, 8 threads, and
timeout 120. Those three numbers are on the image ``CMD`` and on the Fly
process command so the next Deploy cannot snap back to 1×2.

Four workers share one machine. They are not a second Fly machine. Each
worker is its own process, so sqlite writers can wait on ``busy_timeout``,
and the presence pool (4 connections) is per process. That cost is what
kept ``/health`` up under the re-flood. Do not go back to one worker.

gunicorn's default ``worker_connections`` is 1000. On the wedged worker that
cap filled with ESTABLISHED sockets. The cap here is 32 per worker (128
across four), with keepalive of 1 second. Live ``/state`` also sends
``Connection: close``.

``timeout`` is 120 seconds. A worker that stops notifying the arbiter (the
epoll wedge) is killed after two minutes, not ten. ``gthread`` still
notifies during a normal request, so poll slices use ``POLL_BUDGET_SECONDS``
rather than waiting for that kill.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator

# Proven on live alc after 1 worker × 2 threads re-wedged on restart.
# Four processes on the one 1 GB machine, not a second Fly machine.
WORKERS = 4
# Eight threads per worker. ``/health`` can run while a few ``/state`` polls
# are slow, and three other workers still accept if one worker wedges.
THREADS = 8
# Per worker. Not gunicorn's 1000. Four workers × 32 = 128 sockets, not ~1k.
WORKER_CONNECTIONS = 32
KEEPALIVE_SECONDS = 1
# A silent worker is killed after 120s. The old 600s left the wedge in place.
WORKER_TIMEOUT_SECONDS = 120
GRACEFUL_TIMEOUT_SECONDS = 30
# Kernel accept queue per worker. Past this, new clients fail fast.
BACKLOG = 64
# Stop starting another ``/state`` slice. Well under the worker timeout.
POLL_BUDGET_SECONDS = 8.0
# Heavy ``/state`` builds. The other threads stay free for ``/health`` and
# for the shed response.
POLL_IN_FLIGHT = 5

_poll_budget = threading.local()
_POLL_SLOTS = threading.BoundedSemaphore(POLL_IN_FLIGHT)


class PollBudgetExceeded(Exception):
    """A live poll stopped before starting another slow slice."""


def missing_live_session(exc: BaseException) -> bool:
    """True when ``exc`` is the gone-Meet ``KeyError``.

    Field builders raise ``KeyError`` with text that starts with
    ``live session``. Polls must abort on that. A traceback per field is
    what pegged the shared CPU after a Meet row disappeared.

    Args:
        exc: Error from a ``/state`` builder or route.

    Returns:
        Whether ``exc`` names a missing live session.
    """
    if not isinstance(exc, KeyError):
        return False
    text = " ".join(str(part) for part in exc.args)
    return "live session" in text


def begin_poll_budget() -> tuple[float | None, bool]:
    """Start a poll budget unless this thread already has one.

    Returns:
        ``(previous, started)``. Pass the tuple to ``end_poll_budget``.
        A nested call keeps the outer deadline so a fallback poll cannot
        extend the budget by calling the assembler again.
    """
    previous = getattr(_poll_budget, "deadline", None)
    if previous is None:
        _poll_budget.deadline = time.monotonic() + POLL_BUDGET_SECONDS
        return None, True
    return previous, False


def end_poll_budget(token: tuple[float | None, bool]) -> None:
    """Restore the poll budget saved by ``begin_poll_budget``.

    Args:
        token: The ``(previous, started)`` pair from ``begin_poll_budget``.
    """
    previous, started = token
    if started:
        _poll_budget.deadline = None
    else:
        _poll_budget.deadline = previous


def poll_budget_expired() -> bool:
    """True when this thread's poll budget is already spent.

    Returns:
        False when no budget is active, so non-poll callers still run.
    """
    deadline = getattr(_poll_budget, "deadline", None)
    if deadline is None:
        return False
    return time.monotonic() >= deadline


def ensure_poll_budget() -> None:
    """Raise when this thread should stop starting another poll slice.

    Raises:
        PollBudgetExceeded: The budget set by ``begin_poll_budget`` is spent.
    """
    if poll_budget_expired():
        raise PollBudgetExceeded("live poll budget exceeded")


@contextmanager
def poll_slot(*, enabled: bool = True) -> Iterator[bool]:
    """Reserve one heavy ``/state`` slot, or shed when the cap is full.

    The cap is non-blocking. A full cap returns immediately so the thread
    can answer ``/health`` and a retry JSON instead of joining the sqlite
    lock. Tests pass ``enabled=False`` so in-process fan-out still runs
    the builders.

    Args:
        enabled: When False, always admit and do not touch the semaphore.

    Yields:
        True when the caller may build state.
    """
    if not enabled:
        yield True
        return
    admitted = _POLL_SLOTS.acquire(blocking=False)
    try:
        yield admitted
    finally:
        if admitted:
            _POLL_SLOTS.release()


def image_gunicorn_command() -> str:
    """Return the gunicorn command the image and Fly process must run.

    The flags match the alc tourniquet (4 workers, 8 threads, timeout 120,
    bind ``0.0.0.0:8080``, chdir ``lms``) and keep the socket bounds on the
    same line so a Deploy cannot drop them.

    Returns:
        One shell command, without a shell wrapper.
    """
    return (
        "gunicorn "
        "--worker-class gthread "
        f"--workers {WORKERS} "
        f"--threads {THREADS} "
        f"--worker-connections {WORKER_CONNECTIONS} "
        f"--keep-alive {KEEPALIVE_SECONDS} "
        f"--timeout {WORKER_TIMEOUT_SECONDS} "
        f"--graceful-timeout {GRACEFUL_TIMEOUT_SECONDS} "
        f"--backlog {BACKLOG} "
        "--bind 0.0.0.0:8080 "
        "--chdir lms "
        "app:create_app()"
    )


def is_live_state_poll(path: str) -> bool:
    """True for the student and staff live ``/state`` poll URLs.

    Args:
        path: Request path, without a query string preferred.

    Returns:
        Whether the response should close the HTTP connection.
    """
    text = str(path or "").split("?", 1)[0]
    if len(text) > 1:
        text = text.rstrip("/")
    if text == "/api/student/state":
        return True
    parts = text.strip("/").split("/")
    return (
        len(parts) == 4
        and parts[0] == "api"
        and parts[1] == "live-sessions"
        and parts[2].isdigit()
        and parts[3] == "state"
    )
