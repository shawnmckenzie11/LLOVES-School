#!/usr/bin/env python3
"""Decide whether a pull request needs the live-class load jobs.

Copy-only diffs (templates, text, CSS, docs, markdown) skip the fan-out
guard, the 30-seat load budget, and the slide-forward load. Anything else
runs them. Live-class state, polling, news/postcards, sessions, auth, and
DB paths run them even when the file also looks like a template or a
stylesheet.

Push events, an empty diff, and a failed classification run the load jobs.
The lists below are the path globs. A path that matches none of
``SAFE_GLOBS`` is heavy.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
import sys

# A pull request skips the load jobs only when every changed path matches
# one of these and none of HEAVY_GLOBS.
SAFE_GLOBS: tuple[str, ...] = (
    "**/*.md",
    "**/*.markdown",
    "**/*.rst",
    "**/*.css",
    "**/*.html",
    "**/*.htm",
    "**/*.txt",
)

# Force the load jobs. These win over SAFE_GLOBS. Python, JS, JSON, and
# workflow files are already heavy because they are not in SAFE_GLOBS.
HEAVY_GLOBS: tuple[str, ...] = (
    "lms/live_*.py",
    "lms/end_live_run_mode.py",
    "lms/formative_live_board.py",
    "lms/engagement_live.py",
    "lms/state_artifact.py",
    "lms/school_db.py",
    "lms/**/*db*.py",
    "lms/**/*migration*",
    "lms/**/*session*",
    "lms/**/*poll*",
    "lms/**/*news*",
    "lms/**/*postcard*",
    "lms/**/*auth*",
    "lms/static/**/*live*.js",
    "lms/static/**/*live*.mjs",
    "lms/static/**/*poll*",
    "lms/static/**/*session*",
    "lms/static/**/*news*",
    "lms/templates/google_*.html",
    "lms/templates/verify.html",
    "lms/templates/invite.html",
    "lms/templates/request_access.html",
    "**/*.sql",
    "**/*requirements*.txt",
)


def glob_match(path: str, pattern: str) -> bool:
    """Return True when ``path`` matches a slash-separated glob.

    ``**`` matches zero or more path segments. ``*`` does not cross a slash.
    """
    path_parts = _parts(path)
    pat_parts = _parts(pattern)
    return _match_parts(path_parts, pat_parts)


def matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    """Return True when ``path`` matches any glob in ``patterns``."""
    return any(glob_match(path, pattern) for pattern in patterns)


def heavy_checks_required(paths: list[str]) -> bool:
    """Return True unless every path is copy-only.

    An empty list runs the load jobs. A heavy glob forces them even when
    the same path matches a safe glob.
    """
    if not paths:
        return True
    for path in paths:
        if matches_any(path, HEAVY_GLOBS) or not matches_any(path, SAFE_GLOBS):
            return True
    return False


def heavy_for_event(event_name: str, paths: list[str] | None) -> bool:
    """Return True when this GitHub event must run the load jobs.

    Only a pull_request may skip them, and only from a real file list.
    """
    if event_name != "pull_request":
        return True
    if paths is None:
        return True
    return heavy_checks_required(paths)


def changed_paths(base: str, head: str) -> list[str]:
    """Return paths touched between ``base`` and ``head``, including renames.

    Both sides of a rename are included so a move out of a heavy path still
    runs the load jobs.
    """
    out = subprocess.check_output(
        ["git", "diff", "--name-status", "--find-renames", f"{base}...{head}"],
        text=True,
    )
    paths: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        paths.extend(_unquote(field) for field in fields[1:] if field)
    return paths


def write_github_output(heavy: bool) -> None:
    """Append ``heavy=true|false`` to ``GITHUB_OUTPUT`` when that file exists."""
    value = "true" if heavy else "false"
    dest = os.environ.get("GITHUB_OUTPUT", "")
    line = f"heavy={value}\n"
    if dest:
        with open(dest, "a", encoding="utf-8") as handle:
            handle.write(line)
    else:
        sys.stdout.write(line)


def main(argv: list[str] | None = None) -> int:
    """Classify the current event and record the heavy-job flag.

    ``--self-test`` checks the globs and does not read the repository.
    Any failure while listing a pull-request diff records heavy=true.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        _self_test()
        print("ci_path_class self-test ok")
        return 0
    if args:
        print(f"unexpected args: {args}", file=sys.stderr)
        write_github_output(True)
        return 0
    event = os.environ.get("EVENT_NAME", "")
    try:
        paths: list[str] | None
        if event != "pull_request":
            paths = None
        else:
            base = os.environ.get("BASE_SHA", "")
            head = os.environ.get("HEAD_SHA", "")
            if not base or not head:
                raise RuntimeError("pull_request diff is missing BASE_SHA or HEAD_SHA")
            paths = changed_paths(base, head)
        heavy = heavy_for_event(event, paths)
    except Exception as exc:
        print(f"classification failed ({exc}); running the load jobs", file=sys.stderr)
        heavy = True
    write_github_output(heavy)
    print(f"heavy={str(heavy).lower()}")
    return 0


def _parts(value: str) -> list[str]:
    """Split a repo path or glob into non-empty slash segments."""
    cleaned = value.replace("\\", "/").strip()
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return [part for part in cleaned.split("/") if part and part != "."]


def _match_parts(path: list[str], pattern: list[str]) -> bool:
    """Match path segments against glob segments, with ``**``."""
    if not pattern:
        return not path
    if pattern[0] == "**":
        if _match_parts(path, pattern[1:]):
            return True
        if path and _match_parts(path[1:], pattern):
            return True
        return False
    if not path:
        return False
    if fnmatch.fnmatchcase(path[0], pattern[0]):
        return _match_parts(path[1:], pattern[1:])
    return False


def _unquote(field: str) -> str:
    """Strip the quotes git adds around unusual path names."""
    if len(field) >= 2 and field[0] == '"' and field[-1] == '"':
        return field[1:-1]
    return field


def _self_test() -> None:
    """Fail if a known path is classified on the wrong side of the line."""
    safe = [
        "lms/templates/staff/home.html",
        "lms/static/lloves.css",
        "lms/static/whiteboard_formative.css",
        "README.md",
        "frameworks/school.md",
        "docs/notes.md",
        "agents/semester-context.md",
        "content-builder/docs/pipeline-readiness.md",
        "lms/static/live-media/m1c1-c1-real-slice.html",
        "content-builder/packages/MCF3M-builder-input/sources/overall-A-extracted.txt",
        "lms/templates/staff/_whats_new.html",
    ]
    heavy = [
        "lms/school_db.py",
        "lms/live_teacher_state.py",
        "lms/live_news_wire.py",
        "lms/live_class_run.py",
        "lms/auth.py",
        "lms/catchup_session.py",
        "lms/state_artifact.py",
        "lms/end_live_run_mode.py",
        "lms/formative_live_board.py",
        "lms/engagement_live.py",
        "lms/static/live_poll_feel.js",
        "lms/static/live_news_wire.js",
        "lms/static/student-live-session.js",
        "lms/static/formative_live_board.js",
        "lms/templates/google_auth.html",
        "lms/templates/google_setup.html",
        "lms/templates/verify.html",
        "lms/templates/invite.html",
        "lms/templates/request_access.html",
        "lms/requirements.txt",
        "requirements.txt",
        "scripts/mathnet-requirements.txt",
        ".github/workflows/ci.yml",
        "lms/test_fanout_guard.py",
        "frameworks/semester.json",
        "lms/mck210_catchup_migration.py",
    ]
    for path in safe:
        if heavy_checks_required([path]):
            raise SystemExit(f"expected copy-only: {path}")
    for path in heavy:
        if not heavy_checks_required([path]):
            raise SystemExit(f"expected heavy: {path}")
    if not heavy_checks_required([]):
        raise SystemExit("empty diff must be heavy")
    if not heavy_checks_required(["lms/templates/staff/home.html", "lms/school_db.py"]):
        raise SystemExit("mixed diff must be heavy")
    if not heavy_for_event("push", ["README.md"]):
        raise SystemExit("push must be heavy")
    if not heavy_for_event("pull_request", None):
        raise SystemExit("missing diff must be heavy")
    if heavy_for_event("pull_request", ["lms/static/lloves.css"]):
        raise SystemExit("css pull request must be copy-only")
    if not glob_match("lms/static/student-live-session.js", "lms/**/*session*"):
        raise SystemExit("session glob missed student-live-session.js")
    if glob_match("lms/templates/staff/_whats_new.html", "lms/**/*news*"):
        raise SystemExit("whats_new must not match the news glob")


if __name__ == "__main__":
    sys.exit(main())
