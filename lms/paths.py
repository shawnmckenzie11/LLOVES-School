"""Filesystem roots and school identity for the LLOVES LMS."""

from __future__ import annotations

from pathlib import Path

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

SCHOOL_NAME = "Learning Live Online Virtually & Explicitly School"
SCHOOL_SHORT = "LLOVES"
DEFAULT_IT_EMAIL = "solutions@mckenzian.com"
IT_EMAIL_DEFAULT = DEFAULT_IT_EMAIL
LIVE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
