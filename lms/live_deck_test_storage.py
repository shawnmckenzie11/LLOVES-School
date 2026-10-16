"""Storage slot keys for test live class decks (MCK-269)."""

from __future__ import annotations

TEST_DECK_SLOT_PREFIX = "__TEST_"


def test_deck_storage_slot(live_session_id: int) -> str:
    """Return the sqlite ``slot`` token that holds one test run's deck copy.

    Args:
        live_session_id: ``live_class_sessions.id`` for the test run.

    Returns:
        A slot string such as ``__TEST_42`` that never collides with ``C1``.
    """
    return f"{TEST_DECK_SLOT_PREFIX}{int(live_session_id)}"


def parse_test_deck_storage_slot(slot: str) -> int | None:
    """Parse a test deck storage slot back to a live session id.

    Args:
        slot: Stored slot token.

    Returns:
        The session id when ``slot`` is a test deck key, else ``None``.
    """
    text = str(slot or "").strip().upper()
    prefix = TEST_DECK_SLOT_PREFIX.upper()
    if not text.startswith(prefix):
        return None
    tail = text[len(prefix) :]
    if not tail.isdigit():
        return None
    return int(tail)
