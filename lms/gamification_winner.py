"""MCK-240 Winner page: group scoreboard and an anonymous MVP vote.

Gamification is an optional add-on. The teacher turns the Winner page on.
The page shows codenames only.

Votes are split on purpose. ``gamification_mvp_voters`` records that a
student voted (session and voter, no choice). ``gamification_mvp_tallies``
records nominee totals (session and nominee, no voter). The tables share
no timestamp, no rowid order, and no log line.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

# Placeholder copy. Wonder replaces the wording; keys stay stable.
WINNER_COPY: dict[str, str] = {
    "winner.toggle": "Winner page",
    "winner.kicker": "Winner",
    "winner.scoreboard_label": "Scoreboard",
    "winner.points_label": "points",
    "winner.poll_prompt": "Vote for your MVP",
    "winner.poll_thanks": "Vote recorded",
    "winner.poll_error": "Try again",
    "winner.waiting": "Waiting for votes",
    "winner.not_voting": "The winning team is voting",
    "winner.mvp_kicker": "MVP",
    "winner.mvp_reward": "Avatar reward",
    "winner.mvp_tie": "Tied",
    "winner.empty": "No teams yet",
    "winner.tie": "Tie",
}

VOTE_DDL = """
CREATE TABLE IF NOT EXISTS gamification_mvp_voters (
    session_id INTEGER NOT NULL,
    voter_id INTEGER NOT NULL,
    PRIMARY KEY (session_id, voter_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS gamification_mvp_tallies (
    session_id INTEGER NOT NULL,
    nominee_id INTEGER NOT NULL,
    tally INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, nominee_id)
) WITHOUT ROWID;
"""


def mvp_source_ref(session_id: int) -> str:
    """Avatar grant key for the MVP of one session.

    The key names the session, not a voter and not a ballot row.

    Args:
        session_id: ``live_class_sessions.id``.
    """
    return f"mvp:{int(session_id)}"


def team_display_score(espn: float, solo_points: float) -> float:
    """Team total after solo automated points are removed.

    Group awards and challenge payouts stay inside ``espn``. ``solo_points``
    is the part of that total that came from individual scores.

    Args:
        espn: Live team total (member credits plus the team bucket).
        solo_points: Individual points that must not count.
    """
    solo = float(solo_points or 0)
    if solo < 0:
        solo = 0.0
    return round(float(espn or 0) - solo, 1)


def ranked_group_teams(
    raw_teams: list[dict[str, Any]],
    event_sums: dict[int, float],
    allowed: dict[int, float],
) -> list[dict[str, Any]]:
    """Rank named teams on group points and challenges.

    The Class bucket is skipped when real teams exist. Each member keeps
    an id for the vote writer; the student payload strips it.

    Args:
        raw_teams: ``game_state`` team rows, including members.
        event_sums: Individual point events per roster id.
        allowed: Challenge and group points inside those events.
    """
    named = [
        team
        for team in raw_teams
        if str(team.get("name") or "") != "Class"
    ]
    pool = named or list(raw_teams)
    ranked: list[dict[str, Any]] = []
    for team in pool:
        members: list[dict[str, Any]] = []
        solo = 0.0
        for member in team.get("members") or []:
            if not isinstance(member, dict):
                continue
            try:
                sid = int(member.get("id") or 0)
            except (TypeError, ValueError):
                continue
            codename = str(member.get("codename") or "").strip()
            if not sid or not codename:
                continue
            members.append({"id": sid, "codename": codename})
            gap = float(event_sums.get(sid) or 0) - float(allowed.get(sid) or 0)
            if gap > 0:
                solo += gap
        ranked.append(
            {
                "id": int(team.get("id") or 0),
                "name": str(team.get("name") or ""),
                "score": team_display_score(float(team.get("score") or 0), solo),
                "members": members,
            }
        )
    ranked.sort(key=lambda row: (-float(row["score"]), row["name"].casefold(), row["id"]))
    return ranked


def unique_leader(teams: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The single highest team, or None when the top score is tied.

    Args:
        teams: Rows from :func:`ranked_group_teams`, highest score first.
    """
    if not teams:
        return None
    top = float(teams[0]["score"])
    leaders = [team for team in teams if float(team["score"]) == top]
    if len(leaders) != 1:
        return None
    return leaders[0]


def student_event_sums(conn: sqlite3.Connection, game_id: int) -> dict[int, float]:
    """Sum individual point events for one game.

    Team awards are ``to_kind = 'team'`` and are not included.

    Args:
        conn: Game-show sqlite connection.
        game_id: ``games.id``.
    """
    if not game_id:
        return {}
    rows = conn.execute(
        """
        SELECT to_id AS student_id, SUM(amount) AS points
        FROM point_events
        WHERE game_id = ? AND to_kind = 'student'
        GROUP BY to_id
        """,
        (int(game_id),),
    ).fetchall()
    return {int(row["student_id"]): float(row["points"] or 0) for row in rows}


def decide_mvp(
    members: list[dict[str, Any]],
    tallies: dict[int, int],
    eligible_ids: set[int],
    voter_ids: set[int],
) -> dict[str, Any]:
    """Name the MVP after every eligible teammate has voted.

    Args:
        members: Winning-team ``{id, codename}`` rows.
        tallies: Nominee id to count. No voter ids.
        eligible_ids: Joined members of the winning team who must vote.
        voter_ids: Students who have a ballot. No nominee ids.

    Returns:
        ``status`` ``waiting``, ``tie``, or ``mvp``. ``student_id`` is set
        only for ``mvp`` so the avatar grant can be written.
    """
    if not eligible_ids or not eligible_ids.issubset(voter_ids):
        return {"status": "waiting", "student_id": None, "codename": "", "tied": []}
    best = 0
    leaders: list[dict[str, Any]] = []
    for member in members:
        sid = int(member["id"])
        count = int(tallies.get(sid) or 0)
        if count <= 0 or count < best:
            continue
        if count > best:
            best = count
            leaders = [member]
        else:
            leaders.append(member)
    if len(leaders) != 1:
        return {
            "status": "tie",
            "student_id": None,
            "codename": "",
            "tied": [str(member["codename"]) for member in leaders],
        }
    winner = leaders[0]
    return {
        "status": "mvp",
        "student_id": int(winner["id"]),
        "codename": str(winner["codename"]),
        "tied": [],
    }


def format_winner_page(
    *,
    enabled: bool,
    teams: list[dict[str, Any]],
    viewer_id: int | None,
    voted: bool,
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Student payload. Codenames and copy keys only.

    Args:
        enabled: Teacher opened the Winner page.
        teams: Ranked teams from :func:`ranked_group_teams`.
        viewer_id: Roster id of the student reading the page.
        voted: True when this viewer already has a ballot.
        decision: Result of :func:`decide_mvp`.
    """
    page: dict[str, Any] = {"on": bool(enabled), "copy": dict(WINNER_COPY)}
    if not enabled:
        return page
    leader = unique_leader(teams)
    public_teams: list[dict[str, Any]] = []
    for team in teams:
        public_teams.append(
            {
                "name": team["name"],
                "score": team["score"],
                "codenames": [str(member["codename"]) for member in team["members"]],
                "leading": bool(leader is not None and int(team["id"]) == int(leader["id"])),
            }
        )
    eligible = False
    options: list[str] = []
    if leader is not None and viewer_id not in (None, ""):
        member_ids = {int(member["id"]) for member in leader["members"]}
        eligible = int(viewer_id) in member_ids
        if eligible and decision.get("status") == "waiting" and not voted:
            options = sorted(
                (str(member["codename"]) for member in leader["members"]),
                key=str.casefold,
            )
    mvp = None
    tie = None
    if decision.get("status") == "mvp" and decision.get("codename"):
        mvp = {"codename": str(decision["codename"]), "reward": True}
    elif decision.get("status") == "tie":
        tie = {"codenames": [str(name) for name in decision.get("tied") or []]}
    page.update(
        {
            "scoreboard": public_teams,
            "winner_name": str(leader["name"]) if leader is not None else "",
            "poll": {
                "open": bool(options),
                "eligible": eligible,
                "voted": bool(voted) if eligible else False,
                "options": options,
            },
            "mvp": mvp,
            "tie": tie,
        }
    )
    return page


def run_write(conn: sqlite3.Connection, write: Callable[[], None]) -> None:
    """Run ``write`` in one transaction when the caller has none open.

    Args:
        conn: School sqlite connection (autocommit or an open transaction).
        write: Statements to apply. Must not commit on its own.
    """
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN IMMEDIATE")
    try:
        write()
        if own:
            conn.execute("COMMIT")
    except Exception:
        if own and conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def seed_tallies(conn: sqlite3.Connection, session_id: int, nominee_ids: list[int]) -> None:
    """Insert a zero tally for each nominee. Existing counts stay.

    Rows are keyed by nominee id, not by vote order.

    Args:
        conn: School sqlite connection.
        session_id: ``live_class_sessions.id``.
        nominee_ids: Winning-team roster ids.
    """
    sid = int(session_id)
    for nominee_id in sorted({int(value) for value in nominee_ids}):
        conn.execute(
            """
            INSERT INTO gamification_mvp_tallies (session_id, nominee_id, tally)
            VALUES (?, ?, 0)
            ON CONFLICT (session_id, nominee_id) DO NOTHING
            """,
            (sid, int(nominee_id)),
        )


def insert_voter(conn: sqlite3.Connection, session_id: int, voter_id: int) -> bool:
    """Record that this student voted. The choice is not stored.

    Args:
        conn: School sqlite connection.
        session_id: ``live_class_sessions.id``.
        voter_id: Roster ``students.id``.

    Returns:
        True when this insert is the student's first ballot.
    """
    cur = conn.execute(
        """
        INSERT INTO gamification_mvp_voters (session_id, voter_id)
        VALUES (?, ?)
        ON CONFLICT (session_id, voter_id) DO NOTHING
        """,
        (int(session_id), int(voter_id)),
    )
    return int(cur.rowcount or 0) == 1


def bump_tally(conn: sqlite3.Connection, session_id: int, nominee_id: int) -> None:
    """Add one to a nominee tally. The voter is not stored.

    Args:
        conn: School sqlite connection.
        session_id: ``live_class_sessions.id``.
        nominee_id: Roster id of the teammate who received the vote.

    Raises:
        ValueError: No seeded tally row for that nominee.
    """
    cur = conn.execute(
        """
        UPDATE gamification_mvp_tallies
        SET tally = tally + 1
        WHERE session_id = ? AND nominee_id = ?
        """,
        (int(session_id), int(nominee_id)),
    )
    if int(cur.rowcount or 0) != 1:
        raise ValueError("Pick a teammate")


def cast_split(
    conn: sqlite3.Connection,
    session_id: int,
    voter_id: int,
    nominee_id: int,
    nominee_ids: list[int],
) -> str:
    """Seed tallies, then write the ballot and the tally as separate statements.

    A second ballot from the same voter does not change the tally.

    Args:
        conn: School sqlite connection.
        session_id: ``live_class_sessions.id``.
        voter_id: Roster id of the student voting.
        nominee_id: Roster id of the chosen teammate.
        nominee_ids: Every winning-team roster id, seeded before the ballot.

    Returns:
        ``cast`` or ``duplicate``.
    """
    outcome = "cast"

    def write() -> None:
        nonlocal outcome
        seed_tallies(conn, session_id, nominee_ids)
        if not insert_voter(conn, session_id, voter_id):
            outcome = "duplicate"
            return
        try:
            bump_tally(conn, session_id, nominee_id)
        except ValueError:
            conn.execute(
                """
                DELETE FROM gamification_mvp_voters
                WHERE session_id = ? AND voter_id = ?
                """,
                (int(session_id), int(voter_id)),
            )
            raise

    run_write(conn, write)
    return outcome


def purge_vote_tables(conn: sqlite3.Connection, session_id: int) -> None:
    """Delete ballots and tallies for one session.

    Args:
        conn: School sqlite connection.
        session_id: ``live_class_sessions.id``.
    """
    sid = int(session_id)

    def write() -> None:
        conn.execute(
            "DELETE FROM gamification_mvp_voters WHERE session_id = ?",
            (sid,),
        )
        conn.execute(
            "DELETE FROM gamification_mvp_tallies WHERE session_id = ?",
            (sid,),
        )

    run_write(conn, write)


def voter_id_set(conn: sqlite3.Connection, session_id: int) -> set[int]:
    """Roster ids that already have a ballot. No nominee column.

    Args:
        conn: School sqlite connection.
        session_id: ``live_class_sessions.id``.
    """
    rows = conn.execute(
        """
        SELECT voter_id FROM gamification_mvp_voters
        WHERE session_id = ?
        """,
        (int(session_id),),
    ).fetchall()
    return {int(row["voter_id"]) for row in rows}


def tally_map(conn: sqlite3.Connection, session_id: int) -> dict[int, int]:
    """Nominee totals. No voter column.

    Args:
        conn: School sqlite connection.
        session_id: ``live_class_sessions.id``.
    """
    rows = conn.execute(
        """
        SELECT nominee_id, tally FROM gamification_mvp_tallies
        WHERE session_id = ?
        """,
        (int(session_id),),
    ).fetchall()
    return {int(row["nominee_id"]): int(row["tally"] or 0) for row in rows}


def voter_count(conn: sqlite3.Connection, session_id: int) -> int:
    """How many ballots this session has.

    Args:
        conn: School sqlite connection.
        session_id: ``live_class_sessions.id``.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM gamification_mvp_voters WHERE session_id = ?",
        (int(session_id),),
    ).fetchone()
    if row is None:
        return 0
    return int(row["n"] or 0)
