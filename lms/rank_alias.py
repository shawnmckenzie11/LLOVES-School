"""MCK-176: per-item aliases for rank option ids shown to students.

Old option ids were minted ``o1..oN`` in typed order, and a teacher often
types an answer-order rank in key order. Sorting the ids a student can see
(``data-rank-id`` or ``rank_options[].id``) would then give the key away.

Students never see a real id for an answer-order rank. They see an alias:
a short HMAC of ``(scope, option id)`` under the app secret. The scope is
the item (its placement key), so an alias is the same on every reload, in
every worker, and for every member of a group, but tells nothing about
another item. Student input is translated back with :meth:`RankAliases.back`.

MCK-171 can reuse :func:`rank_aliases` for ``race.options[].id``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

DEV_SECRET = "lloves-dev-secret-change-me"
ALIAS_PREFIX = "r"
ALIAS_HEX = 10

# Keys whose values hold option ids in a student-facing tree.
ID_LIST_KEYS = frozenset({"order", "submitted_order"})
ID_KEYS = frozenset({"option_id"})
# Keys whose list rows may carry an option ``id``.
ROW_LIST_KEYS = frozenset({"rank_options", "options", "choices"})

def rank_alias_secret() -> str:
    """``FLASK_SECRET_KEY``, the same default the app uses.

    ``SchoolDB`` passes ``app.secret_key`` instead when the app bound it.
    """
    return os.getenv("FLASK_SECRET_KEY", DEV_SECRET)


def rank_option_alias(scope: Any, option_id: Any, *, secret: str | None = None) -> str:
    """Return the student alias of one option id.

    Args:
        scope: Item scope (placement key, else item id).
        option_id: Real option id.
        secret: Override secret (tests); defaults to :func:`rank_alias_secret`.
    """
    key = (secret or rank_alias_secret()).encode("utf-8")
    msg = f"{scope}\x1f{option_id}".encode("utf-8")
    digest = hmac.new(key, msg, hashlib.sha256).hexdigest()
    return f"{ALIAS_PREFIX}{digest[:ALIAS_HEX]}"


class RankAliases:
    """Two-way map between real option ids and student aliases for one item."""

    def __init__(self, scope: Any, option_ids: list[str], *, secret: str | None = None):
        self.scope = str(scope or "")
        self.ids = [str(opt) for opt in option_ids]
        self._out: dict[str, str] = {}
        for opt in self.ids:
            alias = rank_option_alias(self.scope, opt, secret=secret)
            salt = 0
            while alias in self._out.values():  # pragma: no cover - 40-bit clash
                salt += 1
                alias = rank_option_alias(f"{self.scope}\x1f{salt}", opt, secret=secret)
            self._out[opt] = alias
        self._back = {alias: opt for opt, alias in self._out.items()}

    def out(self, option_id: Any) -> Any:
        """Alias for a real id; anything else is returned unchanged."""
        if isinstance(option_id, str):
            return self._out.get(option_id, option_id)
        return option_id

    def back(self, value: Any) -> str:
        """Real id for an alias (or a real id from an older page).

        Raises:
            ValueError: Unknown alias or id.
        """
        token = str(value or "").strip()
        if token in self._back:
            return self._back[token]
        if token in self._out:
            return token
        raise ValueError("unknown rank option")

    def back_list(self, values: Any) -> Any:
        """Translate a submitted order; non-lists are returned unchanged.

        Raises:
            ValueError: Any unknown alias or id.
        """
        if not isinstance(values, (list, tuple)):
            return values
        return [self.back(value) for value in values]

    def tree(self, node: Any) -> Any:
        """Copy ``node`` with every option id in it replaced by its alias.

        Rewrites ``rank_options[].id`` (and ``options`` / ``choices`` rows),
        ``option_id``, and the ``order`` /
        ``submitted_order`` id lists. Other keys are left alone.
        """
        if isinstance(node, list):
            return [self.tree(value) for value in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in ROW_LIST_KEYS and isinstance(value, list):
                out[key] = [
                    {**row, "id": self.out(row.get("id"))} if isinstance(row, dict) else row
                    for row in value
                ]
            elif key in ID_KEYS:
                out[key] = self.out(value)
            elif key in ID_LIST_KEYS and isinstance(value, list):
                out[key] = [self.out(v) if isinstance(v, str) else self.tree(v) for v in value]
            else:
                out[key] = self.tree(value)
        return out


def rank_aliases(scope: Any, option_ids: list[str], *, secret: str | None = None) -> RankAliases:
    """Build the alias map for one item (MCK-171 entry point).

    Args:
        scope: Item scope (``SchoolDB.rank_alias_scope``).
        option_ids: Real option ids.
        secret: Override secret (tests).
    """
    return RankAliases(scope, option_ids, secret=secret)
