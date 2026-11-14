"""Unit: ONE answer to "is this store / this person still active" (index §CORE-44).

THE CLASS, not the instance. The owner's ask was *"once the store or employee is made inactive their
data should [not] be showing on any module unless the checkbox is clicked for show inactive stores"*.
The general fact that was wrong is not "one report shows a closed store" — it is that **every caller
decided for itself what the active flag means**, and they disagreed:

  * `storeops/router._store_is_active` reads a MISSING / NULL flag as ACTIVE. That is right:
    `storeops.stores.is_active` is `BOOLEAN DEFAULT true` and NULLABLE (migration 003), so a row
    written before the column existed carries NULL and is a live store.
  * `GET /storeops/employees` filters `.eq("is_active", True)`, which in SQL/PostgREST drops NULL.
    So an employee whose flag was never set VANISHES from the roster — the opposite answer to the
    same question, on the same kind of column, in the same module.
  * `GET /core/filter-options` — the option source behind the shared StandardFilterBar, and so
    behind most reports' Stores / People dropdowns — SELECTS `is_active` for employees and never
    filters on it, and does not filter stores at all.

One fact, one home, dereferenced. This module is that home; every caller reads it.
"""
import pytest

pytestmark = pytest.mark.unit


def test_a_missing_or_null_flag_is_ACTIVE_because_the_column_defaults_true():
    from app.modules.core import roster_visibility as rv
    assert rv.is_active({}) is True, "no flag at all — a selected-columns read that omitted it"
    assert rv.is_active({"is_active": None}) is True, "NULL — a row older than the column"


def test_only_an_explicit_false_is_inactive():
    from app.modules.core import roster_visibility as rv
    assert rv.is_active({"is_active": False}) is False
    assert rv.is_active({"is_active": True}) is True


def test_the_wire_spellings_of_false_count_as_false():
    """PostgREST/CSV/form round-trips hand back strings and 0/1, and `"false"` is TRUTHY in Python —
    the trap that would make an inactive row read as active at exactly one caller."""
    from app.modules.core import roster_visibility as rv
    for falsey in ("false", "False", "FALSE", "f", "0", "no", 0):
        assert rv.is_active({"is_active": falsey}) is False, falsey
    for truthy in ("true", "t", "1", "yes", 1):
        assert rv.is_active({"is_active": truthy}) is True, truthy


def test_visible_hides_inactive_by_default_and_shows_them_on_the_optin():
    from app.modules.core import roster_visibility as rv
    rows = [{"store_code": "A"}, {"store_code": "B", "is_active": False},
            {"store_code": "C", "is_active": None}]
    assert [r["store_code"] for r in rv.visible(rows)] == ["A", "C"]
    assert [r["store_code"] for r in rv.visible(rows, include_inactive=True)] == ["A", "B", "C"]


def test_the_surface_can_say_how_many_it_hid():
    """Hiding rows SILENTLY is the thing that makes a report lie about a total. A caller that hides
    must be able to say what it hid."""
    from app.modules.core import roster_visibility as rv
    rows = [{"id": 1}, {"id": 2, "is_active": False}, {"id": 3, "is_active": "false"}]
    assert rv.hidden_count(rows) == 2
    active, inactive = rv.split(rows)
    assert [r["id"] for r in active] == [1]
    assert [r["id"] for r in inactive] == [2, 3]


def test_empty_and_none_inputs_are_an_empty_list_not_a_crash():
    from app.modules.core import roster_visibility as rv
    assert rv.visible(None) == []
    assert rv.visible([]) == []
    assert rv.hidden_count(None) == 0


def test_the_optin_has_exactly_one_spelling():
    """Twelve endpoints already spell the opt-in `include_inactive`. The home states it so a
    thirteenth cannot invent `show_inactive` / `with_inactive` and split the vocabulary."""
    from app.modules.core import roster_visibility as rv
    assert rv.INCLUDE_INACTIVE_PARAM == "include_inactive"


def test_a_different_flag_column_is_passed_in_never_guessed():
    from app.modules.core import roster_visibility as rv
    rows = [{"n": "x", "active": False}]
    assert rv.visible(rows) == rows, "the default field is is_active; `active` is not it"
    assert rv.visible(rows, field="active") == []


def test_it_reads_a_postgrest_filter_free_of_the_null_trap():
    """For the reads that must filter SERVER-side, the home states the one correct PostgREST
    expression. `is_active=eq.true` drops NULL (the live employees bug); `not.is.false` keeps it."""
    from app.modules.core import roster_visibility as rv
    assert rv.POSTGREST_ACTIVE_FILTER == ("is_active", "not.is.false")
