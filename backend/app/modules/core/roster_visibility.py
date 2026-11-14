"""THE ONE HOME for "is this store / this person still active", and for hiding the ones that are not.

OWNER DIRECTIVE 2026-11-14: *"once the store or employee is made inactive their data should [not] be
showing on any module unless the checkbox is clicked for show inactive stores"*.

THE CLASS, NOT THE INSTANCE (CLAUDE.md, "A fix is a DESIGN fix or it is not a fix"). The general fact
that was wrong is not "one report still lists a closed store". It is that **every caller decided for
itself what the active flag means**, and they disagreed with each other:

  * `storeops/router._store_is_active` read a MISSING / NULL flag as ACTIVE — correct, because
    `storeops.stores.is_active` and `storeops.employees.is_active` are both `BOOLEAN DEFAULT true`
    and NULLABLE (migration 003), so a row written before the column existed carries NULL and is a
    live store.
  * `GET /storeops/employees` filtered `.eq("is_active", True)`, which drops NULL server-side — the
    OPPOSITE answer to the same question, on the same kind of column, in the same module. An
    employee whose flag was never set simply vanished from the roster.
  * `GET /core/filter-options` — the option source behind the shared `StandardFilterBar`, and so
    behind most reports' Stores / People dropdowns — SELECTED `is_active` for employees and never
    filtered on it, and never filtered stores at all. Every adopting report offered closed stores
    and terminated people as things to filter by.

So: one fact, one home, dereferenced — never copied. Callers READ this module. The frontend twin is
`frontend/src/lib/roster-visibility.ts` (same rule, same NULL posture, proved to agree), and the
user-facing control is the `StandardFilterBar` "Show inactive" checkbox, default OFF.

`backend/harness_roster_visibility.py` FAILS THE BUILD if a caller stops dereferencing this module or
a second copy of the predicate appears.

TWO RULES THAT ARE EASY TO GET WRONG, STATED ONCE HERE SO NOBODY RE-DERIVES THEM:

1. **NULL / missing means ACTIVE.** It matches the column's own DEFAULT and the frontend's long-
   established `is_active !== false` convention. Only an EXPLICIT false is inactive. Anything else
   silently deletes rows nobody deactivated.
2. **`"false"` is TRUTHY in Python.** A flag that has been through PostgREST, a CSV import, a form
   post or a JSON body can arrive as the STRING `"false"` or as `0`. A naive `if row["is_active"]`
   therefore reads an inactive row as active at exactly the one caller that got its value off the
   wire. `is_active()` below normalises all of it.

HIDING IS NEVER SILENT. `hidden_count` / `split` exist so a surface that hides rows can SAY what it
hid. A report that quietly drops a closed store's September money is a report that lies about
September, which is why the money surfaces keep the rows and name the count instead.
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = [
    "ACTIVE_FIELD", "INCLUDE_INACTIVE_PARAM", "POSTGREST_ACTIVE_FILTER",
    "is_active", "visible", "hidden_count", "split",
]

#: The column every roster relation spells this fact in (`storeops.stores`, `storeops.employees`).
ACTIVE_FIELD = "is_active"

#: THE ONE SPELLING of the opt-in. Twelve endpoints already use it (`GET /storeops/employees`,
#: `GET /storeops/stores`, `GET /hr/employee-database`, the commission-plan roster …); naming it here
#: stops a thirteenth inventing `show_inactive` / `with_inactive` and splitting the vocabulary.
INCLUDE_INACTIVE_PARAM = "include_inactive"

#: THE CORRECT SERVER-SIDE FILTER, for the reads that must narrow in the database rather than in
#: Python: `(column, "not.is.false")` — i.e. `is_active IS NOT FALSE`, which KEEPS NULL.
#: `is_active=eq.true` is the trap: it drops NULL, and that was the live employees-roster defect.
#: Pass it as a raw string through postgrest-py's `.filter(col, "not.is", "false")` — never
#: `.not_.is_(col, False)`, whose f-string yields the literal `False` (capital F), which PostgREST
#: does not recognise as a boolean. Where the rows are already fetched, prefer `visible()`: Python-
#: side is cheaper to prove and carries no wire risk.
POSTGREST_ACTIVE_FILTER = (ACTIVE_FIELD, "not.is.false")

#: Every spelling of "no" that reaches this platform from a database, a spreadsheet or a form.
_FALSE_WORDS = {"false", "f", "0", "no", "n", "off"}


def _get(row: Any, field: str) -> Any:
    if isinstance(row, dict):
        return row.get(field)
    return getattr(row, field, None)


def is_active(row: Any, field: str = ACTIVE_FIELD) -> bool:
    """True unless the row says, EXPLICITLY, that it is not active.

    NULL / missing -> True (the column's own DEFAULT; see rule 1 in the module docstring).
    `False`, `0`, `"false"`, `"f"`, `"no"`, `"off"` (any casing / spacing) -> False (rule 2).
    """
    v = _get(row, field)
    if v is None:
        return True
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() not in _FALSE_WORDS


def visible(rows: Iterable[Any] | None, include_inactive: bool = False,
            field: str = ACTIVE_FIELD) -> list:
    """The rows a surface should show: active-only BY DEFAULT, everything on the explicit opt-in.

    The default is the whole point of the directive — a caller that forgets to think about it hides
    inactive rows, rather than showing them. `include_inactive=True` is what the "Show inactive"
    checkbox and the `include_inactive` query parameter resolve to.
    """
    out = list(rows or [])
    if include_inactive:
        return out
    return [r for r in out if is_active(r, field)]


def hidden_count(rows: Iterable[Any] | None, field: str = ACTIVE_FIELD) -> int:
    """How many rows `visible()` would hide — what a surface needs to SAY so hiding is not silent."""
    return sum(1 for r in (rows or []) if not is_active(r, field))


def split(rows: Iterable[Any] | None, field: str = ACTIVE_FIELD) -> tuple[list, list]:
    """`(active, inactive)`, order preserved — for a surface that renders the inactive ones apart
    (greyed, in their own section) instead of dropping them."""
    rows = list(rows or [])
    active: list = []
    inactive: list = []
    for r in rows:
        (active if is_active(r, field) else inactive).append(r)
    return active, inactive

