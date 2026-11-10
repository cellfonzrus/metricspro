"""The WRITE side of "our schema is behind the feed" (index §4b.1, owner defect 2026-11-10).

`core.column_tolerant` has answered "which columns does this table have" for READS since
2026-09-22. The write side had no rule, and on 2026-11-10 that cost November's store-DLAR rows: the
sweep was taught 20 newly-declared columns, the hand-applied migration that creates them had not
run, PostgREST refused the whole insert (PGRST204) — and the sweep had already deleted the period.

These pin the two pure halves. The destructive sequence itself, end to end against a double that
models PostgREST's refusals, is `backend/harness_write_tolerant_period_replace.py`.
"""
import pytest

from app.core import column_tolerant as ct


class _Resp:
    def __init__(self, data):
        self.data = data


class _Q:
    """The smallest thing shaped like a PostgREST builder: `select` decides whether it raises."""

    def __init__(self, cols, row, wanted):
        self._cols, self._row, self._wanted = cols, row, wanted

    def eq(self, *_a):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        if self._wanted == "*":
            return _Resp([dict(self._row)] if self._row else [])
        for c in str(self._wanted).split(","):
            if c.strip() and c.strip() not in self._cols:
                raise RuntimeError(f"column t.{c.strip()} does not exist")
        return _Resp([])


def _table(cols, row=None):
    class _T:
        def select(self, wanted="*", **_k):
            return _Q(set(cols), row, wanted)
    return lambda: _T()


SCOPE = (lambda q: q.eq("org_id", "o1"))


# ── writable_columns ────────────────────────────────────────────────────────────────────────────

def test_a_stored_row_names_the_tables_columns_in_one_select():
    """The cheap path: when the org has a row, its KEYS are the columns — NULLs included, so a
    column that exists and is empty is still writable."""
    present = ct.writable_columns(
        _table(("a", "b"), row={"a": 1, "b": None}), SCOPE, ["a", "b", "c"])
    assert present == frozenset({"a", "b"})


def test_an_empty_scope_still_answers_by_probing_each_column():
    """A tenant whose feed has never landed is the normal case for a NEW feed, and it must not read
    as "this table has no columns" — that would drop every figure."""
    present = ct.writable_columns(_table(("a", "b")), SCOPE, ["a", "b", "c"])
    assert present == frozenset({"a", "b"})


def test_one_absent_column_never_hides_another_columns_presence():
    """The whole point of probing per column rather than as a block (the §4b.1 defect)."""
    present = ct.writable_columns(_table(("a", "c")), SCOPE, ["a", "b", "c", "d"])
    assert present == frozenset({"a", "c"})


def test_an_unreadable_table_has_no_columns_and_does_not_raise():
    """A caller must drop everything and say so, never destroy what is stored on a failed probe."""
    def _boom():
        raise RuntimeError("relation does not exist")
    assert ct.writable_columns(_boom, SCOPE, ["a"]) == frozenset()


# ── drop_absent ────────────────────────────────────────────────────────────────────────────────

def test_absent_keys_are_stripped_and_returned_by_name():
    rows = [{"a": 1, "x": 2}, {"a": 3, "y": 4}]
    out, absent = ct.drop_absent(rows, frozenset({"a"}))
    assert out == [{"a": 1}, {"a": 3}]
    assert absent == ["x", "y"], "reported in first-seen order, so the sentence is stable"


def test_nothing_absent_hands_back_the_same_objects_not_copies():
    """Byte-identical on the happy path: this sits on every feed write and must be undetectable
    when it has nothing to do."""
    rows = [{"a": 1}]
    out, absent = ct.drop_absent(rows, frozenset({"a", "b"}))
    assert absent == []
    assert out[0] is rows[0]


def test_a_dropped_key_is_never_written_back_as_none():
    """NULL means "the source did not send this". Writing NULL for "our schema is behind" would make
    an unapplied migration indistinguishable from a figure the portal never published — which is the
    §COMMCALC-49.10 class of plausible-looking answer."""
    out, _ = ct.drop_absent([{"a": 1, "x": 9}], frozenset({"a"}))
    assert "x" not in out[0]


def test_the_columns_that_do_exist_still_land():
    """Refusing the whole row because one column is missing is what cost November."""
    out, absent = ct.drop_absent([{"total_acts": 7, "activations_total": 9}],
                                 frozenset({"total_acts"}))
    assert out == [{"total_acts": 7}] and absent == ["activations_total"]


@pytest.mark.parametrize("rows", [None, (), []])
def test_no_rows_is_not_an_error(rows):
    assert ct.drop_absent(rows, frozenset({"a"})) == ([], [])
