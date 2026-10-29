"""Shared route helpers and the bare-endpoint blueprint.

Lane modules register Flask blueprints. Endpoint names stay the
view function names so existing ``url_for`` calls keep working.
"""

from __future__ import annotations

from types import SimpleNamespace

from flask import Blueprint
from flask.blueprints import BlueprintSetupState
from app import (
    _catchup_student_for_class,
    _json_error,
    _ready_library,
    current_user,
    jsonify,
    request,
    request_client_ip,
    resolve_student_live_context,
    session,
)


class BareEndpointState(BlueprintSetupState):
    """Register blueprint rules under the view function's own name.

    Flask prefixes endpoints with the blueprint name. LLOVES templates,
    tests, and the URL-map snapshot call ``url_for`` with the bare name.
    """

    def add_url_rule(
        self,
        rule,
        endpoint=None,
        view_func=None,
        **options,
    ):
        """Add one rule without prefixing its endpoint.

        URL prefixes and subdomains still apply. This app's lane
        blueprints set neither, so the rule string stays as written.

        Args:
            rule: URL pattern.
            endpoint: Explicit endpoint. Empty uses the view name.
            view_func: View callable stored for the endpoint.
            **options: Rule options Flask already accepted on ``@app.route``.
        """
        if self.url_prefix is not None:
            if rule:
                rule = "/".join((self.url_prefix.rstrip("/"), rule.lstrip("/")))
            else:
                rule = self.url_prefix
        options.setdefault("subdomain", self.subdomain)
        if endpoint is None:
            endpoint = view_func.__name__
        defaults = self.url_defaults
        if "defaults" in options:
            defaults = dict(defaults, **options.pop("defaults"))
        self.app.add_url_rule(
            rule,
            endpoint,
            view_func,
            defaults=defaults,
            **options,
        )


class LaneBlueprint(Blueprint):
    """Blueprint whose endpoints stay the original function names."""

    def make_setup_state(self, app, options, first_registration=False):
        """Build setup state that does not prefix endpoint names.

        Args:
            app: Application the blueprint is registering on.
            options: Options passed to ``register_blueprint``.
            first_registration: True the first time this blueprint is registered.

        Returns:
            Setup state used for each recorded route.
        """
        return BareEndpointState(self, app, options, first_registration)


def bind_shared(app, school):
    """Bind helpers that more than one route lane calls.

    Each helper closes over the app and school for this process, the same
    way the old page and game registrars closed over them.

    Args:
        app: Flask application. Helpers log through it.
        school: Open school database for this process.

    Returns:
        The helper functions, keyed by their original names.
    """

    def _audit(
        action: str,
        resource_type: str,
        *,
        resource_id: str | int | None = None,
        student_id: int | None = None,
        detail: dict[str, Any] | None = None,
        actor: dict[str, Any] | None = None,
    ) -> None:
        """Write an access/admin audit row; never raise to the request."""
        user = actor if actor is not None else current_user()
        try:
            school.record_access_event(
                action=action,
                resource_type=resource_type,
                actor_user_id=int(user["id"]) if user else None,
                actor_role=str((user or {}).get("role") or "unknown"),
                tenant_id=school.tenant_id_of(user) if user else None,
                resource_id=resource_id,
                student_id=student_id,
                ip=request_client_ip(),
                detail=detail,
            )
        except Exception:  # noqa: BLE001 - pages must still render
            app.logger.exception("access audit write failed")

    def _content_questions_scope(class_id: int):
        """MCK-79: course code and selectable modules for Contest Questions.

        Contest Questions come from ``live_problems`` (by course), but the
        section lives in Import from bank, so a ready module pack is still
        required, as for the rest of the picker.

        Returns:
            ``(ontario_code, modules, None)`` or ``(None, [], error_response)``.
        """
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return None, [], (jsonify({"ok": False, "error": "Forbidden"}), 403)
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, error = _ready_library(school, cls)
        if not library_id:
            return None, [], (
                jsonify({"ok": False, "error": error or "No module pack"}),
                404,
            )
        try:
            from live_content_questions import selectable_live_modules
        except ImportError:
            from lms.live_content_questions import selectable_live_modules
        code = str(cls.get("ontario_code") or "MCF3M").strip().upper()
        return code, selectable_live_modules(code), None

    def _student_live_context() -> dict[str, Any] | None:
        """Return token- or cookie-scoped student live context."""
        return resolve_student_live_context(school, session)

    def _positive_class_id(value: Any) -> int | None:
        """Return a positive class id, or None when the value is unusable.

        Args:
            value: Raw class id from a dict, session, or database row.
        """
        if value in (None, ""):
            return None
        try:
            cid = int(value)
        except (TypeError, ValueError):
            return None
        return cid if cid > 0 else None

    def _bound_student_class_id(ctx: dict[str, Any] | None) -> int | None:
        """Resolve the signed-in student's class without a KeyError.

        Order matches other student endpoints: the live context, then the
        Flask session, then the live-session row.

        Args:
            ctx: ``resolve_student_live_context`` result, or None.

        Returns:
            Classes primary key, or None when every fallback is empty.
        """
        sources: list[Any] = []
        if isinstance(ctx, dict):
            sources.append(ctx.get("class_id"))
        sources.append(session.get("student_class_id"))
        for source in sources:
            cid = _positive_class_id(source)
            if cid is not None:
                return cid
        live_id = ctx.get("live_session_id") if isinstance(ctx, dict) else None
        if not live_id:
            live_id = session.get("student_live_session_id")
        if not live_id:
            return None
        try:
            row = school.get_live_session(int(live_id))
        except (TypeError, ValueError):
            return None
        if not isinstance(row, dict):
            return None
        return _positive_class_id(row.get("class_id"))

    def _student_identity() -> tuple[dict[str, Any], int, int] | None:
        """Return offering and roster ids when the student session is bound.

        ``class_id`` is read with ``.get``. A missing key falls back to the
        student session and then the live-session row.

        Returns:
            ``(offering, class_id, student_id)`` or ``None`` if incomplete.
        """
        ctx = _student_live_context()
        if ctx is None:
            return None
        offering = ctx.get("offering")
        class_id = _bound_student_class_id(ctx)
        if not offering or class_id is None:
            return None
        return offering, class_id, ctx.get("student_id")

    def _can_access_class_live_asset(class_id: int) -> bool:
        """Return True when class staff, a bound live student, or a student
        signed in on one of the class's catch-up links may fetch assets.

        ``@login_required`` is staff-only, so image ``<img>`` tags from the
        student portal must use this check instead. Visit-token cookies and
        Flask student session both resolve through ``_student_live_context``.

        Args:
            class_id: Game-show ``classes.id``.
        """
        user = current_user()
        if user is not None and school.teacher_owns_class(
            int(user["id"]), int(class_id)
        ):
            return True
        ctx = _student_live_context()
        if ctx and int(ctx.get("class_id") or 0) == int(class_id):
            return True
        return _catchup_student_for_class(school, int(class_id)) is not None

    def _viewer_is_stmtg_tester(offering: Any, student_id: Any) -> bool:
        """True when the bound student is the STMTG test seat."""
        from tester_feedback import viewer_is_stmtg_tester

        return viewer_is_stmtg_tester(school, offering, student_id)

    def _require_class_staff(class_id: int) -> tuple[Any, int] | None:
        """Return a JSON 403/401 tuple if the current user cannot score this class."""
        user = current_user()
        if user is None:
            return jsonify({"ok": False, "error": "Authentication required."}), 401
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        return None

    def _reject_basic_roster(class_id: int) -> None:
        """Raise when a free-plan class is already at the seat cap.

        Args:
            class_id: Game-show ``classes.id``.

        Raises:
            ValueError: The named roster is already at 35.
        """
        try:
            from features import limits_apply
        except ImportError:
            from lms.features import limits_apply
        try:
            from feature_gates import BASIC_SEAT_CAP
        except ImportError:
            from lms.feature_gates import BASIC_SEAT_CAP
        user = current_user()
        if user is None or not limits_apply(user):
            return
        try:
            count = len(school.class_codenames(int(class_id)))
        except Exception:
            return
        if count >= BASIC_SEAT_CAP:
            raise ValueError("The basic plan allows 35 students in a class.")

    def _staff_post(class_id: int, handler):
        """Run a GameShowDB mutation for a staff-owned class."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        try:
            return jsonify({"ok": True, **handler(body)})
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    return SimpleNamespace(
        _audit=_audit,
        _content_questions_scope=_content_questions_scope,
        _student_live_context=_student_live_context,
        _positive_class_id=_positive_class_id,
        _bound_student_class_id=_bound_student_class_id,
        _student_identity=_student_identity,
        _can_access_class_live_asset=_can_access_class_live_asset,
        _viewer_is_stmtg_tester=_viewer_is_stmtg_tester,
        _require_class_staff=_require_class_staff,
        _reject_basic_roster=_reject_basic_roster,
        _staff_post=_staff_post,
    )
