"""Stable whiteboard team buckets keyed by ``team:<sort_order>`` (MCK-218).

Game-show ``game_teams.id`` values are recreated on lock restore; ink must
stay on sort-order buckets for the live run.

The SQL lives in ``tools/math-game-show/board_pins.py`` so the game database
does not import this LMS module. The standalone game-show app has no ``lms``
package on ``sys.path``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_MGS_DIR = Path(__file__).resolve().parents[1] / "tools" / "math-game-show"
if str(_MGS_DIR) not in sys.path:
    sys.path.append(str(_MGS_DIR))

from board_pins import (  # noqa: E402
    active_live_run_key,
    assignment_board_key,
    assignment_sort_order,
    assignment_sort_orders,
    board_key_for_sort_order,
    delete_board_assignments_for_run,
    record_board_assignment,
    sync_assignments_from_game_state,
    team_sort_order,
)

__all__ = [
    "active_live_run_key",
    "assignment_board_key",
    "assignment_sort_order",
    "assignment_sort_orders",
    "board_key_for_sort_order",
    "delete_board_assignments_for_run",
    "record_board_assignment",
    "sync_assignments_from_game_state",
    "team_sort_order",
]
