"""Admin-only teacher plan and per-feature overrides (MCK-538)."""

from __future__ import annotations

from typing import Any

from datetime import datetime
from zoneinfo import ZoneInfo

from features import (
    FEATURE_KEYS,
    FEATURE_REGISTRY,
    OVERRIDE_OFF,
    OVERRIDE_ON,
    OVERRIDE_PLAN,
    PLAN_BASIC,
    PLAN_PRO,
    _now,
    has_feature,
    plan_default,
)

TORONTO = ZoneInfo("America/Toronto")

# Plan-differentiated keys always shown as override chips on the Teachers row.
ROW_PLAN_DIFF_KEYS: tuple[str, ...] = (
    "catchup_full",
    "slides_import",
    "whiteboard",
    "media",
    "team_draw",
    "group_draw",
    "portfolio_gdoc",
    "news_wire_extras",
)

# Admin-facing labels (Plans tab + chips). Wonder-final where noted in MCK-540.
ADMIN_FEATURE_LABELS: dict[str, str] = {
    "mc_questions": "Multiple choice",
    "rank_questions": "Ranking",
    "numeric_questions": "Numeric",
    "essay_questions": "Written response",
    "live_slides": "Live slides",
    "catchup_links": "Catch-up (last class)",
    "catchup_full": "Catch-up (all classes)",
    "celebrations": "Celebrations",
    "gradebook_tracking": "Attendance & participation",
    "game_show": "Rank game show",
    "slides_import": "Import Google Slides or PowerPoint",
    "whiteboard_submit": "Show Your Work",
    "whiteboard": "Whiteboards",
    "media": "Media",
    "team_draw": "Team draw",
    "group_draw": "Group draw",
    "portfolio_gdoc": "Portfolio to Google Doc",
    "news_wire_extras": "News wire extras",
}


def feature_admin_label(key: str) -> str:
    """Human label for a registry key on the admin console."""
    return ADMIN_FEATURE_LABELS.get(str(key), str(key).replace("_", " ").title())


def format_audit_timestamp(iso: str | None) -> str | None:
    """Format a stored ISO timestamp for America/Toronto display."""
    raw = str(iso or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TORONTO)
    else:
        dt = dt.astimezone(TORONTO)
    hour = dt.strftime("%I").lstrip("0") or "12"
    return (
        f"{dt.strftime('%b')} {dt.day}, {dt.year}, "
        f"{hour}:{dt.strftime('%M')} {dt.strftime('%p')} ET"
    )


def _audit_value_label(key: str, value: str | None) -> str:
    """Map stored audit values to UI vocabulary."""
    token = str(value or "").strip().lower()
    if key == "plan":
        if token == PLAN_PRO:
            return "Pro"
        if token == PLAN_BASIC:
            return "Basic"
        return token or "—"
    if token == OVERRIDE_PLAN:
        return "Default"
    if token == OVERRIDE_ON:
        return "On"
    if token == OVERRIDE_OFF:
        return "Off"
    return token or "—"


def row_feature_keys_for_teacher(
    plan: str, features: list[dict[str, Any]]
) -> list[str]:
    """Keys whose effective value differs from the teacher plan default.

    Grandfathered ``user_features`` rows that match plan defaults are omitted
    so the Overrides column stays scannable.
    """
    normalized = str(plan or PLAN_BASIC).strip().lower()
    keys: list[str] = []
    for feat in features:
        key = str(feat["key"])
        default = bool(plan_default(normalized, key))
        effective = bool(feat.get("effective"))
        if effective != default:
            keys.append(key)
    return keys

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
    """Start ``BEGIN IMMEDIATE`` on an autocommit sqlite connection.

    Raises:
        RuntimeError: The connection is already inside a transaction.
    """
    if conn.in_transaction:
        raise RuntimeError("school connection already in a transaction")
    conn.execute("BEGIN IMMEDIATE")


def _finish_write(conn: Any, *, commit: bool) -> None:
    """End the active sqlite transaction."""
    if not conn.in_transaction:
        return
    conn.execute("COMMIT" if commit else "ROLLBACK")


def _staff_or_it_row(conn: Any, user_id: int, tenant_id: int) -> dict[str, Any] | None:
    """Load an active staff or IT user in this tenant, or ``None`` when out of scope."""
    row = conn.execute(
        """
        SELECT id, role, display_name, plan, tenant_id, archived_at
        FROM users
        WHERE id = ?
        """,
        (int(user_id),),
    ).fetchone()
    if row is None:
        return None
    if int(row["tenant_id"]) != int(tenant_id):
        return None
    if str(row["role"] or "").strip().lower() not in {"staff", "it"}:
        return None
    archived = str(row["archived_at"] or "").strip()
    if archived:
        return None
    return dict(row)


def _admin_tenant_id(school: Any, admin_user_id: int) -> int:
    """Resolve the admin actor's tenant id for scoped teacher lookups."""
    admin = school.get_user(int(admin_user_id))
    if admin is None:
        raise ValueError("teacher not found")
    return int(school.tenant_id_of(admin))


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
        last_changed: dict[int, str | None] = {}
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
            for audit in school.conn.execute(
                f"""
                SELECT teacher_user_id, MAX(created_at) AS last_at
                FROM teacher_feature_audit
                WHERE teacher_user_id IN ({marks})
                GROUP BY teacher_user_id
                """,
                ids,
            ):
                last_changed[int(audit["teacher_user_id"])] = str(audit["last_at"])
    teachers: list[dict[str, Any]] = []
    for row in rows:
        uid = int(row["id"])
        plan = str(row["plan"] or PLAN_BASIC).strip().lower()
        handle = teacher_display_handle(dict(row))
        feature_rows: list[dict[str, Any]] = []
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
        row_keys = row_feature_keys_for_teacher(plan, feature_rows)
        lc_raw = last_changed.get(uid)
        teachers.append(
            {
                "id": uid,
                "handle": handle,
                "plan": plan,
                "features": feature_rows,
                "row_feature_keys": row_keys,
                "last_changed_at": lc_raw,
                "last_changed_display": format_audit_timestamp(lc_raw),
            }
        )
    return teachers


def admin_bulk_set_teacher_plan(
    school: Any,
    admin_user_id: int,
    teacher_user_ids: list[int],
    plan: str,
) -> int:
    """Set the same plan for many teachers in one transaction.

    Every id is validated before any write. One audit row per teacher whose
    plan actually changes. Rolls back the whole batch when any id is invalid.

    Args:
        school: Open school database.
        admin_user_id: Signed-in admin ``users.id``.
        teacher_user_ids: Target teacher ids in this tenant.
        plan: ``basic`` or ``pro``.

    Returns:
        Count of teachers whose plan changed.

    Raises:
        ValueError: Invalid plan or any teacher is out of scope.
    """
    normalized = str(plan or "").strip().lower()
    if normalized not in VALID_PLANS:
        raise ValueError("plan must be basic or pro")
    unique_ids = sorted({int(x) for x in teacher_user_ids})
    if not unique_ids:
        return 0
    with school._lock:
        tenant_id = _admin_tenant_id(school, admin_user_id)
        targets: list[tuple[int, dict[str, Any]]] = []
        for teacher_id in unique_ids:
            target = _staff_or_it_row(school.conn, teacher_id, tenant_id)
            if target is None:
                raise ValueError("teacher not found")
            targets.append((teacher_id, target))
        conn = school.conn
        _begin_write(conn)
        changed = 0
        try:
            for teacher_id, target in targets:
                old = str(target.get("plan") or PLAN_BASIC).strip().lower()
                if old == normalized:
                    continue
                conn.execute(
                    "UPDATE users SET plan = ? WHERE id = ?",
                    (normalized, int(teacher_id)),
                )
                _append_audit(
                    conn,
                    admin_user_id=int(admin_user_id),
                    teacher_user_id=int(teacher_id),
                    key="plan",
                    old_value=old,
                    new_value=normalized,
                )
                changed += 1
            _finish_write(conn, commit=True)
        except Exception:
            _finish_write(conn, commit=False)
            raise
    return changed


def list_teacher_change_history(
    school: Any,
    admin_user_id: int,
    teacher_user_id: int,
) -> list[dict[str, Any]]:
    """Append-only audit rows for one teacher in the admin's tenant."""
    with school._lock:
        tenant_id = _admin_tenant_id(school, admin_user_id)
        if _staff_or_it_row(school.conn, teacher_user_id, tenant_id) is None:
            return []
        rows = school.conn.execute(
            """
            SELECT id, admin_user_id, teacher_user_id, key, old_value, new_value, created_at
            FROM teacher_feature_audit
            WHERE teacher_user_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (int(teacher_user_id),),
        ).fetchall()
        admin_handles: dict[int, str] = {}
        if rows:
            admin_ids = sorted({int(r["admin_user_id"]) for r in rows})
            marks = ",".join("?" * len(admin_ids))
            for u in school.conn.execute(
                f"SELECT id, display_name FROM users WHERE id IN ({marks})",
                admin_ids,
            ):
                admin_handles[int(u["id"])] = teacher_display_handle(dict(u))
        out: list[dict[str, Any]] = []
        for row in rows:
            key = str(row["key"])
            out.append(
                {
                    "id": int(row["id"]),
                    "when": format_audit_timestamp(str(row["created_at"])),
                    "what_key": key,
                    "what_label": "Plan" if key == "plan" else feature_admin_label(key),
                    "old_value": _audit_value_label(key, row["old_value"]),
                    "new_value": _audit_value_label(key, row["new_value"]),
                    "who": admin_handles.get(int(row["admin_user_id"]), "User"),
                }
            )
        return out


def list_tenant_feature_audit(
    school: Any,
    admin_user_id: int,
    *,
    teacher_user_id: int | None = None,
    feature_key: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Tenant-wide ``teacher_feature_audit`` rows for the Audit tab."""
    with school._lock:
        tenant_id = _admin_tenant_id(school, admin_user_id)
        teacher_rows = school.conn.execute(
            """
            SELECT id, display_name FROM users
            WHERE tenant_id = ?
              AND lower(role) IN ('staff', 'it')
              AND (archived_at IS NULL OR archived_at = '')
            """,
            (int(tenant_id),),
        ).fetchall()
        if not teacher_rows:
            return []
        teacher_handles = {
            int(r["id"]): teacher_display_handle(dict(r)) for r in teacher_rows
        }
        allowed_ids = sorted(teacher_handles)
        if teacher_user_id is not None:
            tid = int(teacher_user_id)
            if tid not in teacher_handles:
                return []
            allowed_ids = [tid]
        marks = ",".join("?" * len(allowed_ids))
        sql = f"""
            SELECT id, admin_user_id, teacher_user_id, key, old_value, new_value, created_at
            FROM teacher_feature_audit
            WHERE teacher_user_id IN ({marks})
        """
        params: list[Any] = list(allowed_ids)
        key_filter = str(feature_key or "").strip()
        if key_filter:
            if key_filter == "plan":
                sql += " AND key = 'plan'"
            elif key_filter in FEATURE_KEYS:
                sql += " AND key = ?"
                params.append(key_filter)
            else:
                return []
        df = str(date_from or "").strip()
        dt = str(date_to or "").strip()
        if df:
            sql += " AND created_at >= ?"
            params.append(df)
        if dt:
            sql += " AND created_at <= ?"
            params.append(dt if "T" in dt else f"{dt}T23:59:59")
        sql += " ORDER BY created_at DESC, id DESC LIMIT 500"
        rows = school.conn.execute(sql, params).fetchall()
        admin_handles: dict[int, str] = {}
        if rows:
            admin_ids = sorted({int(r["admin_user_id"]) for r in rows})
            amarks = ",".join("?" * len(admin_ids))
            for u in school.conn.execute(
                f"SELECT id, display_name FROM users WHERE id IN ({amarks})",
                admin_ids,
            ):
                admin_handles[int(u["id"])] = teacher_display_handle(dict(u))
        out: list[dict[str, Any]] = []
        for row in rows:
            key = str(row["key"])
            tid = int(row["teacher_user_id"])
            out.append(
                {
                    "id": int(row["id"]),
                    "when": format_audit_timestamp(str(row["created_at"])),
                    "teacher_id": tid,
                    "teacher": teacher_handles.get(tid, f"User {tid}"),
                    "who": admin_handles.get(int(row["admin_user_id"]), "User"),
                    "what_key": key,
                    "what_label": "Plan" if key == "plan" else feature_admin_label(key),
                    "old_value": _audit_value_label(key, row["old_value"]),
                    "new_value": _audit_value_label(key, row["new_value"]),
                }
            )
        return out


def feature_registry_for_admin() -> list[dict[str, Any]]:
    """Read-only Basic vs Pro registry rows for the Plans tab."""
    rows: list[dict[str, Any]] = []
    for key in FEATURE_KEYS:
        rows.append(
            {
                "key": key,
                "label": feature_admin_label(key),
                "basic": bool(FEATURE_REGISTRY[key]),
                "pro": True,
            }
        )
    return rows


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
        tenant_id = _admin_tenant_id(school, admin_user_id)
        target = _staff_or_it_row(school.conn, teacher_user_id, tenant_id)
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
        tenant_id = _admin_tenant_id(school, admin_user_id)
        target = _staff_or_it_row(school.conn, teacher_user_id, tenant_id)
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
        tenant_id = _admin_tenant_id(school, admin_user_id)
        target = _staff_or_it_row(school.conn, teacher_user_id, tenant_id)
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


def teacher_has_feature_after_reload(
    school: Any,
    admin_user_id: int,
    teacher_user_id: int,
    key: str,
) -> bool:
    """``has_feature`` using a fresh row from sqlite (next-request semantics).

    Args:
        school: Open school database.
        admin_user_id: Admin whose tenant scopes the teacher lookup.
        teacher_user_id: Teacher ``users.id``.
        key: Feature key.

    Returns:
        Effective flag after plan and override reads.
    """
    tenant_id = _admin_tenant_id(school, admin_user_id)
    row = _staff_or_it_row(school.conn, teacher_user_id, tenant_id)
    if row is None:
        return False
    return bool(has_feature(row, key))