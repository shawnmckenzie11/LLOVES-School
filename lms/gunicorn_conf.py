"""Gunicorn process model for local ``--config`` checks.

The image ``CMD`` and ``fly.toml`` ``[processes]`` repeat these flags on the
command line. A Deploy that only loaded this file could be overridden by a
machine command; the Dockerfile line is what ships. Numbers live in
``serve_capacity`` so the three copies cannot drift in tests.
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
