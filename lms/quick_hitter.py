"""Quick-hitter question chain — shared packaging for live-prompt rides.

Rides use live-prompt as the primary channel. ``active_media`` stays
for visuals (Real-slice etc.) only. Not a gradebook writeback.

Minds-On (``ride=minds_on``) is **one MC** — ``items`` length 1. Do not
build a Minds-On carousel. Meet (``ride=meet_team``) is a length 2–3
QH pack scoped to MEET (A → C → B; drop C first). CONS
(``ride=cons``) is the multi-item ride (3–5; C1 is 5).
"""

from __future__ import annotations

from typing import Any

QUICK_HITTER_ARTIFACT_ID = "quick-hitter-question-chain"
QUICK_HITTER_CHANNEL = "live-prompt"
RIDE_MINDS_ON = "minds_on"
RIDE_MEET_TEAM = "meet_team"
RIDE_CONS = "cons"
CLEAR_ON_TEAM_CHALLENGE = "team_challenge_start"


def quick_hitter_packaging(
    *,
    ride: str,
    chain_index: int = 1,
    chain_length: int = 1,
    ephemeral: bool = True,
    durable_store: bool = False,
    clear_on: str | None = None,
) -> dict[str, Any]:
    """Return shared artifact fields for one ride of the question chain.

    Args:
        ride: ``minds_on``, ``meet_team``, or ``cons``.
        chain_index: 1-based item index on this ride.
        chain_length: 1 for Minds-On; 2–3 for Meet; 3–5 for CONS.
        ephemeral: True when the prompt is session-scoped.
        durable_store: False — do not write to a durable student store.
        clear_on: Optional clear trigger (``team_challenge_start`` for Minds-On).
    """
    payload: dict[str, Any] = {
        "artifact_id": QUICK_HITTER_ARTIFACT_ID,
        "ride": ride,
        "channel": QUICK_HITTER_CHANNEL,
        "ephemeral": bool(ephemeral),
        "durable_store": bool(durable_store),
        "chain_index": int(chain_index),
        "chain_length": int(chain_length),
    }
    if clear_on:
        payload["clear_on"] = str(clear_on)
    return payload
