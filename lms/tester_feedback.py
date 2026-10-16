"""STMTG student-tester feedback sent to Linear as plain text.

The tester seat lives on the staff-meeting section only. Feedback is the
words the tester typed, after a check that it does not carry a PIN, an
email address, a real course code, or another student's name. Nothing
here logs, prints, or returns a PIN.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any, Callable

logger = logging.getLogger(__name__)

STAFF_MEETING_CODE = "STMTG"
TESTER_CODENAME = "Tester"
REAL_SECTION_CODES = ("MCR3U-2", "MCR3U", "MCF3M")

_PIN = re.compile(r"(?<!\d)\d{4}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_REAL_CODE = re.compile(r"\b(?:MCR3U-2|MCR3U|MCF3M)\b", re.IGNORECASE)
_LINEAR_URL = "https://api.linear.app/graphql"
_MAX_CHARS = 2000

_ISSUE_MUTATION = """
mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
  }
}
"""


def viewer_is_stmtg_tester(school: Any, offering: Any, student_id: Any) -> bool:
    """True when this roster id is the test student on STMTG.

    Args:
        school: School DB with ``is_test_student``.
        offering: Offering row, or None.
        student_id: Roster id from the student session.
    """
    if student_id in (None, ""):
        return False
    code = str((offering or {}).get("ontario_code") or "").strip().upper()
    if code != STAFF_MEETING_CODE:
        return False
    try:
        return bool(school.is_test_student(int(student_id)))
    except (TypeError, ValueError):
        return False


def prepare_feedback_text(text: str, *, forbidden_names: set[str] | None = None) -> str:
    """Return feedback that is safe to send, or raise ValueError.

    The raised message is generic. It does not echo the submitted text,
    so a PIN typed into the box cannot ride back in the error.

    Args:
        text: Tester's note.
        forbidden_names: Real roster names that must not appear.

    Returns:
        Stripped feedback text.

    Raises:
        ValueError: Empty, too long, or it contains a PIN, email,
            real course code, or a forbidden name.
    """
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("Enter feedback.")
    if len(raw) > _MAX_CHARS:
        raise ValueError("Feedback is too long.")
    if _PIN.search(raw) or _EMAIL.search(raw) or _REAL_CODE.search(raw):
        raise ValueError("Leave out PINs, names, and class codes.")
    folded = raw.casefold()
    for name in forbidden_names or ():
        token = str(name or "").strip()
        if len(token) >= 2 and token.casefold() in folded:
            raise ValueError("Leave out PINs, names, and class codes.")
    return raw


def feedback_issue_input(text: str, team_id: str) -> dict[str, Any]:
    """Linear ``issueCreate`` input whose description is only the note.

    Args:
        text: Checked feedback text.
        team_id: Linear team id from the environment.

    Returns:
        GraphQL variables. No student id, class id, or PIN field.
    """
    return {
        "input": {
            "teamId": team_id,
            "title": "STMTG tester feedback",
            "description": text,
        }
    }


def redact_real_roster(payload: Any, forbidden_names: set[str]) -> Any:
    """Blank exact real roster names inside a student-visible payload.

    Args:
        payload: JSON-like structure.
        forbidden_names: Names that must not be shown to the tester.

    Returns:
        A copy with those exact strings replaced by an empty string.
    """
    blocked = {str(name).strip().casefold() for name in forbidden_names if str(name).strip()}

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(value) for value in node]
        if isinstance(node, str) and node.strip().casefold() in blocked:
            return ""
        return node

    return walk(payload)


def linear_api_key() -> str:
    """Linear API key from the environment, or an empty string.

    ``LINEAR_API_KEY`` is preferred. ``LINEAR_API_TOKEN`` is the other
    name already used by some local setups. The key is never logged.
    """
    return (os.getenv("LINEAR_API_KEY") or os.getenv("LINEAR_API_TOKEN") or "").strip()


def post_tester_feedback(
    text: str,
    *,
    forbidden_names: set[str] | None = None,
    transport: Callable[[str, bytes, dict[str, str]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Send checked feedback to Linear as a new issue.

    Args:
        text: Tester's note.
        forbidden_names: Real roster names to reject.
        transport: Optional ``(url, body, headers) -> parsed JSON`` for tests.
            The default is an HTTP POST to Linear.

    Returns:
        ``{"ok": True}``. The caller's text is not returned.

    Raises:
        ValueError: The note is not safe to send, or Linear is not configured.
    """
    clean = prepare_feedback_text(text, forbidden_names=forbidden_names)
    key = linear_api_key()
    team_id = (os.getenv("LINEAR_TEAM_ID") or "").strip()
    if not key or not team_id:
        logger.info("stmtg tester feedback skipped chars=%s", len(clean))
        raise ValueError("Feedback is not available right now.")
    variables = feedback_issue_input(clean, team_id)
    body = json.dumps({"query": _ISSUE_MUTATION, "variables": variables}).encode("utf-8")
    headers = {
        "Authorization": key,
        "Content-Type": "application/json",
    }
    if transport is None:
        response = _post_linear(body, headers)
    else:
        response = transport(_LINEAR_URL, body, headers)
    if not (response.get("data") or {}).get("issueCreate", {}).get("success"):
        logger.info("stmtg tester feedback rejected chars=%s", len(clean))
        raise ValueError("Feedback is not available right now.")
    logger.info("stmtg tester feedback sent chars=%s", len(clean))
    return {"ok": True}


def _post_linear(body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    """POST one GraphQL body. The response body is not logged."""
    request = urllib.request.Request(_LINEAR_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
    except urllib.error.URLError as exc:
        logger.info("stmtg tester feedback transport failed")
        raise ValueError("Feedback is not available right now.") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.info("stmtg tester feedback transport failed")
        raise ValueError("Feedback is not available right now.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Feedback is not available right now.")
    return parsed
