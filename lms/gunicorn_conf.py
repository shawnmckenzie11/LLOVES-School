"""Gunicorn process model for the Fly image.

``lms/Dockerfile`` loads this file. The numbers live in ``serve_capacity``
so tests can lock them. ``--chdir lms`` is on the command line; this module
inserts its own directory so the import works before that chdir.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LMS = Path(__file__).resolve().parent
if str(_LMS) not in sys.path:
    sys.path.insert(0, str(_LMS))

from serve_capacity import (  # noqa: E402
    BACKLOG,
    GRACEFUL_TIMEOUT_SECONDS,
    KEEPALIVE_SECONDS,
    THREADS,
    WORKER_CONNECTIONS,
    WORKER_TIMEOUT_SECONDS,
    WORKERS,
)

bind = "0.0.0.0:8080"
worker_class = "gthread"
workers = WORKERS
threads = THREADS
worker_connections = WORKER_CONNECTIONS
keepalive = KEEPALIVE_SECONDS
timeout = WORKER_TIMEOUT_SECONDS
graceful_timeout = GRACEFUL_TIMEOUT_SECONDS
backlog = BACKLOG
