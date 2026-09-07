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
    "https://www.googleapis.com/auth/drive",
)

ALC_DRIVE_FOLDER_ID = "1l8dvsNvKlXEcl8gIsNYDbvj9hB9GOM40"
CURRICULUM_DRIVE_FOLDER_ID = "1pAiRUjiS9SrTJpX5QhVcewcUusqhvRK5"
CURRICULUM_COURSE_DRIVE_FOLDERS: dict[str, str] = {
    "MAP4C": "1QQ32moB5bRQ9rSmD2KLEuY36NznHgHH5",
    "MBF3C": "16HaOxPkFpjsjqhfXl4vjNe2v7kEJkAG6",
    "MCF3M": "1ScZ4sGlasPjkD3VMY9GI7ESkZp9gjt0g",
    "MCR3U": "1x6_-n_7a-_tzV7XcronbEfHdcBGbnTLe",
    "MCT4C": "1osSRcwWzOUHuR4skdXX5jyOuKG0e3BME",
    "MCV4U": "1CQPvd5I-K9nOeTF-uLOaHUKlcihXiuyk",
    "MDM4U": "16LsqWQtN3nVZmBgqbob8kZGUcsUWP7Bz",
    "MEL3E": "1ie5PdcQkLEO30NKrso3Xd7GMyKfYSzLM",
    "MEL4E": "1NR-OsGl8tfRopGgoYmO-jXuBCM-rhjE7",
    "MHF4U": "1pbBwYp62LxBJk_e8wx6jm0oJdirUptxc",
}
DEFAULT_SLIDES_TEMPLATE_ID = "1yn2JQQNyd1AfBrrRK0_BFx4ibkdIgXTgR7bu0QzesRU"
SETTING_ALC_DRIVE_FOLDER_ID = "alc_drive_folder_id"
SETTING_DEFAULT_SLIDES_TEMPLATE_ID = "default_slides_template_id"
SETTING_DEFAULT_STYLE_GUIDE_FILE_ID = "default_style_guide_file_id"

PROBLEM_KINDS: tuple[str, ...] = ("warmup", "contest", "standard")
PHRASE_CATEGORIES: tuple[str, ...] = ("team_problem", "consolidation", "general")
OBSERVATION_SCOPES: tuple[str, ...] = ("student", "students", "team", "class")
EVIDENCE_STRENGTHS: tuple[str, ...] = ("emerging", "developing", "clear")

MOCK_SLIDES_REFRESH_TOKEN = "mock-local-slides"

DEFAULT_LIVES_PER_MODULE = 4
DEFAULT_WEEKS_PER_MODULE = 2
DEFAULT_ASYNC_LESSON_KEYWORD = "Lesson"

M1C1_TEAM_CHALLENGE_CONTEXT = (
    "Mr. M loves classes that split nicely into equal (or very similar) group "
    "sizes. His favourite class size is 16, because he can run a jigsaw with "
    "equal groups."
)
M1C1_TEAM_CHALLENGE_QUESTION = (
    "How can Mr. M quickly check if his class is nicely jigsaw-able?"
)
M1C1_TEAM_CHALLENGE_NOTES = (
    "Explain jigsaw grouping: home groups, then expert groups, then return. "
    "Connect to functions and notation, perfect squares, factoring, and "
    "integers. Quadratics, midpoint, and x^2 = 16 are nearby."
)


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
