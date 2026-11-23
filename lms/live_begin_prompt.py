"""Run Live Class Begin Module prompt (MCK-494).

The teacher confirms in the page. ``begin_new_module`` commits before
the live session row is created. Test classes, Open Office, and a
jump ahead of the next planned module start with no wrap-up.
"""

from __future__ import annotations

from typing import Any, Callable


def confirm_begin_then_start(
    school: Any,
    class_id: int,
    teacher_user_id: int,
    *,
    live_module: Any,
    live_slot: Any,
    is_test: bool = False,
    is_office: bool = False,
    start: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Wrap the open module when the prompt still applies, then start.

    ``start`` must create the ``live_class_sessions`` row (the app
    passes ``_start_live_and_wake``, which calls
    ``start_live_class_session``). Wrap-up runs only after an
    unconfirmed Set Class shell is quit, and only when
    ``live_start_module_prompt`` still offers the dialog.

    Args:
        school: ``SchoolDB``.
        class_id: Game-show ``classes.id``.
        teacher_user_id: Staff user starting the class.
        live_module: Module chosen in Set Class.
        live_slot: Slot chosen in Set Class.
        is_test: Test live class. Never wraps.
        is_office: Open Office. Never wraps.
        start: Session starter. Called last.

    Returns:
        The session row from ``start``.

    Raises:
        BeginModuleRejected: A running class blocked the wrap-up.
    """
    from begin_module import BeginModuleRejected

    cid = int(class_id)
    teacher = int(teacher_user_id)
    module = live_module
    slot = live_slot

    def _start() -> dict[str, Any]:
        """Create the live session after any wrap-up has committed."""
        return start(
            school,
            cid,
            teacher,
            live_module=module,
            live_slot=slot,
            is_test=bool(is_test),
        )

    if is_test or is_office:
        return _start()
    offer = school.live_start_module_prompt(
        cid,
        module,
        is_test=False,
        is_office=False,
        live_slot=str(slot or ""),
    )
    if not offer:
        return _start()
    school.release_unset_live_shell(cid)
    if school.get_active_live_session_for_class(cid) is not None:
        raise BeginModuleRejected("End the live class first.")
    offer = school.live_start_module_prompt(
        cid,
        module,
        is_test=False,
        is_office=False,
        live_slot=str(slot or ""),
    )
    if not offer:
        return _start()
    board = school.begin_module_board(cid)
    school.begin_new_module(
        cid,
        str(board["current_module"]),
        user_id=teacher,
    )
    return _start()
