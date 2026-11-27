"""Admin-only teacher plan and per-feature overrides (MCK-538)."""

from __future__ import annotations

import sqlite3
from typing import Any

from features import (
    FEATURE_KEYS,
    OVERRIDE_OFF,
    OVERRIDE_ON,
    OVERRIDE_PLAN,
    PLAN_BASIC,
    PLAN_PRO,
    _now,
    has_feature,
    plan_default,
)

VALID_PLANS = frozenset({PLAN_BASIC, PLAN_PRO})
VALID_OVERRIDE_MODES = frozenset({OVERRIDE_ON, OVERRIDE_OFF, OVERRIDE_PLAN})


def teacher_display_handle(row: dict[str, Any]) -> str:
    """Return the teacher label shown on the admin page (no email).

    Args:
        row: ``users`` row with ``display_name``.

    Returns:
        Stripped display name, or a synthetic id label when blank.
    """
    name = str(row.get("display_name") or "").strip()
    if name:
        return name
    return f"User {int(row['id'])}"


def _override_token(enabled: int | bool) -> str:
    """Map a stored override flag to audit vocabulary."""
    return OVERRIDE_ON if int(enabled) != 0 else OVERRIDE_OFF


def _read_feature_override(conn: Any, user_id: int, key: str) -> str | None:
    """Return ``on``/``off`` when a row exists, else ``None`` for plan-follow."""
    row = conn.execute(
        """
        SELECT enabled FROM user_features
        WHERE user_id = ? AND feature_key = ?
        """,
        (int(user_id), str(key)),
    ).fetchone()
    if row is None:
        return None
    return _override_token(row["enabled"])


def _append_audit(
    conn: Any,
    *,
    admin_user_id: int,
    teacher_user_id: int,
    key: str,
    old_value: str,
    new_value: str,
) -> None:
    """Insert one audit row. Caller holds the write transaction."""
    conn.execute(
        """
        INSERT INTO teacher_feature_audit (
            admin_user_id, teacher_user_id, key, old_value, new_value, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(admin_user_id),
            int(teacher_user_id),
            str(key),
            str(old_value),
            str(new_value),
            _now(),
        ),
    )


def _begin_write(conn: Any) -> None:
    """Start ``BEGIN IMMEDIATE`` on an autocommit sqlite connection."""
    if conn.in_transaction:
        conn.execute("ROLLBACK")
    conn.execute("BEGIN IMMEDIATE")


def _finish_write(conn: Any, *, commit: bool) -> None:
    """End the active sqlite transaction."""
    if not conn.in_transaction:
        return
    conn.execute("COMMIT" if commit else "ROLLBACK")


def _staff_or_it_row(conn: Any, user_id: int) -> dict[str, Any] | None:
    """Load a staff or IT user, or ``None`` when missing or wrong role."""
    row = conn.execute(
        """
        SELECT id, role, display_name, plan
        FROM users
        WHERE id = ?
        """,
        (int(user_id),),
    ).fetchone()
    if row is None:
        return None
    if str(row["role"] or "").strip().lower() not in {"staff", "it"}:
        return None
    return dict(row)


def list_teachers_for_plan_admin(school: Any, tenant_id: int) -> list[dict[str, Any]]:
    """Staff and IT accounts in this school, id and handle only.

    Args:
        school: Open school database.
        tenant_id: Tenant filter for ``users``.

    Returns:
        Rows sorted by id with ``id``, ``handle``, ``plan``, and ``features``.
    """
    with school._lock:
        rows = school.conn.execute(
            """
            SELECT id, role, display_name, plan
            FROM users
            WHERE tenant_id = ?
              AND lower(role) IN ('staff', 'it')
              AND (archived_at IS NULL OR archived_at = '')
            ORDER BY id
            """,
            (int(tenant_id),),
        ).fetchall()
        overrides: dict[int, dict[str, str]] = {}
        for row in rows:
            overrides[int(row["id"])] = {}
        if rows:
            marks = ",".join("?" * len(rows))
            ids = [int(row["id"]) for row in rows]
            for uf in school.conn.execute(
                f"""
                SELECT user_id, feature_key, enabled
                FROM user_features
                WHERE user_id IN ({marks})
                """,
                ids,
            ):
                overrides[int(uf["user_id"])][str(uf["feature_key"])] = _override_token(
                    uf["enabled"]
                )
    teachers: list[dict[str, Any]] = []
    for row in rows:
        uid = int(row["id"])
        plan = str(row["plan"] or PLAN_BASIC).strip().lower()
        handle = teacher_display_handle(dict(row))
        feature_rows: list[dict[str, Any]] = []
        user_stub = {"id": uid, "plan": plan}
        for key in FEATURE_KEYS:
            stored = overrides.get(uid, {}).get(key)
            if stored is None:
                source = OVERRIDE_PLAN
                effective = plan_default(plan, key)
            else:
                source = stored
                effective = stored == OVERRIDE_ON
            feature_rows.append(
                {
                    "key": key,
                    "source": source,
                    "effective": bool(effective),
                }
            )
        teachers.append(
            {
                "id": uid,
                "handle": handle,
                "plan": plan,
                "features": feature_rows,
            }
        )
    return teachers


def admin_set_teacher_plan(
    school: Any,
    admin_user_id: int,
    teacher_user_id: int,
    plan: str,
) -> bool:
    """Set ``users.plan`` when it changes and write one audit row.

    Args:
        school: Open school database.
        admin_user_id: Signed-in admin ``users.id``.
        teacher_user_id: Target teacher.
        plan: ``basic`` or ``pro``.

    Returns:
        True when the plan was updated.

    Raises:
        ValueError: Invalid plan or target user.
    """
    normalized = str(plan or "").strip().lower()
    if normalized not in VALID_PLANS:
        raise ValueError("plan must be basic or pro")
    with school._lock:
        target = _staff_or_it_row(school.conn, teacher_user_id)
        if target is None:
            raise ValueError("teacher not found")
        old = str(target.get("plan") or PLAN_BASIC).strip().lower()
        if old == normalized:
            return False
        conn = school.conn
        _begin_write(conn)
        try:
            conn.execute(
                "UPDATE users SET plan = ? WHERE id = ?",
                (normalized, int(teacher_user_id)),
            )
            _append_audit(
                conn,
                admin_user_id=int(admin_user_id),
                teacher_user_id=int(teacher_user_id),
                key="plan",
                old_value=old,
                new_value=normalized,
            )
            _finish_write(conn, commit=True)
        except Exception:
            _finish_write(conn, commit=False)
            raise
    return True


def admin_set_teacher_override(
    school: Any,
    admin_user_id: int,
    teacher_user_id: int,
    feature_key: str,
    mode: str,
) -> bool:
    """Set one feature to on, off, or follow-plan.

    Args:
        school: Open school database.
        admin_user_id: Signed-in admin ``users.id``.
        teacher_user_id: Target teacher.
        feature_key: Registry key only.
        mode: ``on``, ``off``, or ``plan``.

    Returns:
        True when storage or audit changed.

    Raises:
        ValueError: Invalid key, mode, or target user.
    """
    key = str(feature_key or "").strip()
    if key not in FEATURE_KEYS:
        raise ValueError("unknown feature key")
    normalized = str(mode or "").strip().lower()
    if normalized not in VALID_OVERRIDE_MODES:
        raise ValueError("mode must be on, off, or plan")
    with school._lock:
        target = _staff_or_it_row(school.conn, teacher_user_id)
        if target is None:
            raise ValueError("teacher not found")
        conn = school.conn
        stored = _read_feature_override(conn, teacher_user_id, key)
        old = stored if stored is not None else OVERRIDE_PLAN
        if normalized == OVERRIDE_PLAN:
            if stored is None:
                return False
            new = OVERRIDE_PLAN
        elif normalized == OVERRIDE_ON:
            new = OVERRIDE_ON
            if stored == OVERRIDE_ON:
                return False
        else:
            new = OVERRIDE_OFF
            if stored == OVERRIDE_OFF:
                return False
        _begin_write(conn)
        try:
            if normalized == OVERRIDE_PLAN:
                conn.execute(
                    """
                    DELETE FROM user_features
                    WHERE user_id = ? AND feature_key = ?
                    """,
                    (int(teacher_user_id), key),
                )
            else:
                flag = 1 if normalized == OVERRIDE_ON else 0
                conn.execute(
                    """
                    INSERT INTO user_features (user_id, feature_key, enabled, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, feature_key) DO UPDATE SET
                        enabled = excluded.enabled,
                        updated_at = excluded.updated_at
                    """,
                    (int(teacher_user_id), key, flag, _now()),
                )
            _append_audit(
                conn,
                admin_user_id=int(admin_user_id),
                teacher_user_id=int(teacher_user_id),
                key=key,
                old_value=old,
                new_value=new,
            )
            _finish_write(conn, commit=True)
        except Exception:
            _finish_write(conn, commit=False)
            raise
    return True


def admin_reset_teacher_overrides(
    school: Any,
    admin_user_id: int,
    teacher_user_id: int,
) -> int:
    """Delete every override for one teacher and audit each removal.

    Args:
        school: Open school database.
        admin_user_id: Signed-in admin ``users.id``.
        teacher_user_id: Target teacher.

    Returns:
        Count of overrides removed (and audited).

    Raises:
        ValueError: Target is not staff or IT.
    """
    with school._lock:
        target = _staff_or_it_row(school.conn, teacher_user_id)
        if target is None:
            raise ValueError("teacher not found")
        conn = school.conn
        rows = conn.execute(
            """
            SELECT feature_key, enabled FROM user_features
            WHERE user_id = ?
            ORDER BY feature_key
            """,
            (int(teacher_user_id),),
        ).fetchall()
        if not rows:
            return 0
        _begin_write(conn)
        try:
            for row in rows:
                key = str(row["feature_key"])
                old = _override_token(row["enabled"])
                conn.execute(
                    """
                    DELETE FROM user_features
                    WHERE user_id = ? AND feature_key = ?
                    """,
                    (int(teacher_user_id), key),
                )
                _append_audit(
                    conn,
                    admin_user_id=int(admin_user_id),
                    teacher_user_id=int(teacher_user_id),
                    key=key,
                    old_value=old,
                    new_value=OVERRIDE_PLAN,
                )
            _finish_write(conn, commit=True)
        except Exception:
            _finish_write(conn, commit=False)
            raise
    return len(rows)


def teacher_has_feature_after_reload(school: Any, teacher_user_id: int, key: str) -> bool:
    """``has_feature`` using a fresh row from sqlite (next-request semantics).

    Args:
        school: Open school database.
        teacher_user_id: Teacher ``users.id``.
        key: Feature key.

    Returns:
        Effective flag after plan and override reads.
    """
    row = _staff_or_it_row(school.conn, teacher_user_id)
    if row is None:
        return False
    return bool(has_feature(row, key))


def audit_row_count(conn: Any, teacher_user_id: int) -> int:
    """Count audit rows for one teacher (tests only)."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM teacher_feature_audit
        WHERE teacher_user_id = ?
        """,
        (int(teacher_user_id),),
    ).fetchone()
    return int(row["n"])


def assert_audit_append_only(conn: sqlite3.Connection) -> None:
    """Raise ``IntegrityError`` when update or delete is allowed on audit rows."""
    conn.execute(
        """
        INSERT INTO teacher_feature_audit (
            admin_user_id, teacher_user_id, key, old_value, new_value, created_at
        ) VALUES (0, 0, 'plan', 'basic', 'pro', '2000-01-01T00:00:00')
        """
    )
    audit_id = int(
        conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    )
    try:
        conn.execute(
            "UPDATE teacher_feature_audit SET new_value = 'basic' WHERE id = ?",
            (audit_id,),
        )
        raise AssertionError("audit update should have been refused")
    except sqlite3.IntegrityError:
        pass
    try:
        conn.execute("DELETE FROM teacher_feature_audit WHERE id = ?", (audit_id,))
        raise AssertionError("audit delete should have been refused")
    except sqlite3.IntegrityError:
        pass