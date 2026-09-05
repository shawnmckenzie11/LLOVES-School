"""Shared keys for live-class slides and Ontario process evidence."""

from __future__ import annotations

PROCESS_KEYS: tuple[str, ...] = (
    "problem_solving",
    "reasoning_proving",
    "reflecting",
    "tools_strategies",
    "connecting",
    "representing",
    "communicating",
)

PROCESS_NAME_TO_KEY: dict[str, str] = {
    "Problem Solving": "problem_solving",
    "Reasoning and Proving": "reasoning_proving",
    "Reflecting": "reflecting",
    "Selecting Tools and Computational Strategies": "tools_strategies",
    "Connecting": "connecting",
    "Representing": "representing",
    "Communicating": "communicating",
}

SLIDES_OPERATOR_EMAILS: frozenset[str] = frozenset(
    {
        "solutions@mckenzian.com",
        "shawnmckenzie11.sm@gmail.com",
    }
)

SLIDES_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive.file",
)

PROBLEM_KINDS: tuple[str, ...] = ("warmup", "contest", "standard")
PHRASE_CATEGORIES: tuple[str, ...] = ("team_problem", "consolidation", "general")
OBSERVATION_SCOPES: tuple[str, ...] = ("student", "students", "team", "class")
EVIDENCE_STRENGTHS: tuple[str, ...] = ("emerging", "developing", "clear")

MOCK_SLIDES_REFRESH_TOKEN = "mock-local-slides"


def process_key_from_name(name: str) -> str | None:
    """Map a Ministry process display name to a stable database key.

    Args:
        name: Process title from ``mcf3m_expectations.json``.

    Returns:
        Canonical key, or None if the name is unknown.
    """
    return PROCESS_NAME_TO_KEY.get((name or "").strip())


def is_slides_operator_email(email: str | None) -> bool:
    """True when this Google account may connect Slides/Drive.

    Args:
        email: Signed-in user email.
    """
    return str(email or "").strip().lower() in SLIDES_OPERATOR_EMAILS
