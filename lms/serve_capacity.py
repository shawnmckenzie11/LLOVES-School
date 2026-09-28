"""Capacity limits for the one 1 GB Fly machine.

Production is one ``gthread`` worker. A second worker would duplicate the
interpreter and the sqlite catalogue and is not used on shared-cpu-1x /
1 GB. Eight threads share that process so ``/health`` can still answer
while a few live ``/state`` polls are slow. The presence pool stays
smaller than the thread count: extra polls wait briefly and return retry
JSON instead of opening more Postgres sockets.

gunicorn's default ``worker_connections`` is 1000. On alc that cap filled
with ESTABLISHED sockets (~1000 fds) while the two request threads sat in
``epoll`` / ``futex`` and ``/health`` returned zero bytes. ``worker_connections``
here is a small multiple of the thread count. Live ``/state`` responses
also send ``Connection: close`` so a finished poll does not sit in the
keepalive set. Keepalive itself is 1 second.

``timeout`` stays 600 seconds because an IMSCC unpack can run that long.
``gthread`` does not abort a request at that timeout — the worker still
notifies the arbiter — so poll builders stop on their own after
``POLL_BUDGET_SECONDS``.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator

# One process. Two processes contend on ``lloves.sqlite`` and double RSS.
WORKERS = 1
# Was 2. A couple of slow ``/state`` calls then occupied every thread, so
# ``/health`` could not start. Eight is enough for one class of short polls
# plus staff, and still one worker on 1 GB.
THREADS = 8
# 8 in flight plus a small keepalive/pending margin. Not gunicorn's 1000.
WORKER_CONNECTIONS = 32
KEEPALIVE_SECONDS = 1
# IMSCC unpack. Polls use ``POLL_BUDGET_SECONDS``, not this.
WORKER_TIMEOUT_SECONDS = 600
GRACEFUL_TIMEOUT_SECONDS = 30
# Kernel accept queue. Past this, new clients fail fast instead of becoming
# fds inside the wedged worker.
BACKLOG = 64
# Stop starting another ``/state`` slice. Well under the 600s worker timeout
# and in the same range as the presence TCP user timeout.
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
