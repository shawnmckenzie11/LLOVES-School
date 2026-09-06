"""Filesystem roots and school identity for the LLOVES LMS."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
ROOT = REPO_ROOT
DATA_DIR = LMS_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "lloves.sqlite"
TEMPLATES = LMS_DIR / "templates"
GAME_SHOW = REPO_ROOT / "tools" / "math-game-show"
MGS_DIR = GAME_SHOW
GAME_TEMPLATES = GAME_SHOW / "templates"
GAME_STATIC = GAME_SHOW / "static"
SCRIPTS_DIR = REPO_ROOT / "scripts"
SEMESTER_JSON = REPO_ROOT / "frameworks" / "semester.json"

# Curriculum seeds and Ministry PDFs live under lms/ (no courses/ tree).
SEEDS_DIR = LMS_DIR / "seeds"
MCF3M_SEED = SEEDS_DIR / "mcf3m_expectations.json"
MCF3M_EXPECTATIONS = MCF3M_SEED
CURRICULUM_SOURCES = LMS_DIR / "sources" / "ontario-curriculum"
ONTARIO_SOURCES_DIR = CURRICULUM_SOURCES
MATH_CURRICULUM_PDF = CURRICULUM_SOURCES / "ontario-math-curriculum-gr-11-12.pdf"
SYLLABUS_DATA_DIR = DATA_DIR / "syllabus"

# Public product name (swap SCHOOL_DISPLAY / SCHOOL_NAME for a later A/B).
# SCHOOL_SHORT stays LLOVES as the internal alias (health JSON, sqlite, Fly).
SCHOOL_DISPLAY = "ALC"
SCHOOL_NAME = "ALC"
SCHOOL_TAGLINE = "Attendance & Live Classes"
SCHOOL_SHORT = "LLOVES"
DEFAULT_IT_EMAIL = "solutions@mckenzian.com"
IT_EMAIL_DEFAULT = DEFAULT_IT_EMAIL
LIVE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MCKENZIAN_HOME = "https://www.mckenzian.com"
MCKENZIAN_CREDIT_LABEL = "Built by McKenzian Solutions"


def mckenzian_credit_url() -> str:
    """Return the labeled McKenzian credit href with campaign UTMs."""
    query = urlencode(
        {
            "utm_source": "alc",
            "utm_medium": "referral",
            "utm_campaign": "built_by_credit",
            "utm_content": "alc_footer",
        }
    )
    return f"{MCKENZIAN_HOME}/?{query}"


def public_brand() -> dict[str, str]:
    """Jinja context for customer-facing ALC copy (LLOVES remains internal)."""
    return {
        "school_name": SCHOOL_NAME,
        "school_display": SCHOOL_DISPLAY,
        "school_tagline": SCHOOL_TAGLINE,
        "school_short": SCHOOL_SHORT,
        "mckenzian_credit_url": mckenzian_credit_url(),
        "mckenzian_credit_label": MCKENZIAN_CREDIT_LABEL,
    }
