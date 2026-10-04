"""Capacity limits for the one 1 GB Fly machine.

Alc on Fly v189 (1 worker, 2 threads, timeout 600) stayed wedged through a
soft restart and a hard restart: clients re-flooded the two threads and
``/health`` never returned. The machine command that restored ``/health``
200 on that same shared-cpu-1x / 1 GB box was 4 workers, 8 threads, and
timeout 120. Those three numbers are on the image ``CMD`` and on the Fly
process command so the next Deploy cannot snap back to 1×2.

That capacity is not a soft-restart fix. On 2026-09-28, with the live
command already at 4×8 and timeout 120, ``fly machines restart`` wedged
``/health`` again (curl timeout about 90s). ``fly machines update`` (a
fresh launch of the same command) restored ``/health`` 200. Restart binds
the listen socket before workers finish ``create_app()``, and the live
poll clients fill that queue. Fresh launch cuts traffic over after the
process is serving. Ops recovery under poll load is a fresh launch or an
image deploy.

Four workers share one machine. They are not a second Fly machine. Each
worker is its own process, so sqlite writers can wait on ``busy_timeout``,
and the presence pool (4 connections) is per process.

gunicorn's default ``worker_connections`` is 1000 and its default listen
backlog is 2048. The caps here are 32 connections per worker and a backlog
of 64 on the one listen socket, with keepalive of 1 second. Live ``/state``
also sends ``Connection: close``. For the first seconds after a worker
loads, ``/state`` is retry JSON so the boot queue cannot pin every thread.
``/health`` stays on the normal path during that window.

``timeout`` is 120 seconds. A worker that stops notifying the arbiter (the
epoll wedge) is killed after two minutes, not ten. ``gthread`` still
notifies during a normal request, so poll slices use ``POLL_BUDGET_SECONDS``
rather than waiting for that kill.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)

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
# MCK-180: fly.toml top-level kill_signal / kill_timeout. SIGTERM is
# gunicorn's graceful stop (SIGINT, Fly's default, is a quick stop). The
# timeout covers the graceful window plus a few seconds to exit, and stays
# within Fly's 300 s maximum.
FLY_KILL_SIGNAL = "SIGTERM"
FLY_KILL_TIMEOUT_SECONDS = 35
FLY_KILL_TIMEOUT_MAX_SECONDS = 300
# One listen socket, shared by every worker. gunicorn's default is 2048.
# Past this, TCP is refused while workers are still inside create_app().
BACKLOG = 64
# Stop starting another ``/state`` slice. Well under the worker timeout.
POLL_BUDGET_SECONDS = 8.0
# Heavy ``/state`` builds. The other threads stay free for ``/health`` and
# for the shed response.
POLL_IN_FLIGHT = 5
# After create_app(), shed /state this long so the boot backlog drains
# as retry JSON. /health is not shed. Tests leave the window disarmed.
WORKER_WARMUP_SECONDS = 8.0

_poll_budget = threading.local()
_POLL_SLOTS = threading.BoundedSemaphore(POLL_IN_FLIGHT)
# None: this process is not in a gunicorn boot window.
_warmup_until: float | None = None


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


def running_under_gunicorn() -> bool:
    """True when this process was started by gunicorn.

    gunicorn sets ``SERVER_SOFTWARE`` in the master before it forks, so
    the worker sees it inside ``create_app``. The Flask dev server and
    unit tests do not.

    Returns:
        Whether the boot poll shed should arm.
    """
    return os.environ.get("SERVER_SOFTWARE", "").startswith("gunicorn/")


def arm_worker_warmup(now: float | None = None) -> float:
    """Start the window that sheds live polls after this worker loads.

    Call this at the end of ``create_app``, which gunicorn runs before
    ``accept()``. The listen socket is already open, so a soft restart
    has queued polls. Those requests get retry JSON until the deadline.
    A later call resets the deadline.

    Args:
        now: Monotonic timestamp. Tests pass a value; production uses
            ``time.monotonic``.

    Returns:
        The monotonic deadline.
    """
    global _warmup_until
    stamp = time.monotonic() if now is None else now
    _warmup_until = stamp + WORKER_WARMUP_SECONDS
    # Warning, not info: gunicorn does not configure this logger, and the
    # process stderr is what Fly keeps. One line per worker at boot.
    logger.warning(
        "live poll warmup: shedding /state for %.0fs so /health can answer",
        WORKER_WARMUP_SECONDS,
    )
    return _warmup_until


def clear_worker_warmup() -> None:
    """Disarm the boot shed. Tests call this so later polls build state."""
    global _warmup_until
    _warmup_until = None


def worker_warmup_active(now: float | None = None) -> bool:
    """True while boot-time ``/state`` polls should be shed.

    Args:
        now: Monotonic timestamp. Defaults to ``time.monotonic``.

    Returns:
        False when the window was never armed or the deadline has passed.
    """
    deadline = _warmup_until
    if deadline is None:
        return False
    stamp = time.monotonic() if now is None else now
    return stamp < deadline


def maybe_arm_worker_warmup(*, testing: bool) -> None:
    """Arm the boot shed for a production gunicorn worker.

    Args:
        testing: ``create_app(testing=True)``. Tests keep the window off
            even if ``SERVER_SOFTWARE`` is set in the environment.
    """
    if testing or not running_under_gunicorn():
        return
    arm_worker_warmup()


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
