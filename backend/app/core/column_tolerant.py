"""ANY-SUBSET COLUMN READS — the ONE reading rule for a table whose columns arrive by hand-applied
migrations (index §4b.1; owner defect 2026-09-22).

THE CLASS THIS CLOSES. A per-org config reader that selected column SETS as blocks, newest set first,
falling back to an older block when the select failed, let ONE missing OLDER column hide every NEWER
one: on the live tenant `commission_org_config` carried the mig-1013 switch `pl_commission_source`
but not the mig-996 column `pl_device_margin_presentation`, so the mig-1013 block AND the mig-996 block
both failed and the ladder fell to the mig-934 block — which predates the switch. The owner chose the
ledger on the panel; the P&L read `feeds`; nothing said so. Migrations are applied by hand and not
always in order, so a reader must tolerate ANY subset of its columns and REPORT what is missing.

THE RULE (every reader, no exceptions — `harness_any_columns_lock.py` fails the build on a ladder):
  • a per-org CONFIG row (one row, a few dozen columns) is read with `select("*")` and the reader
    takes the keys that are present — `read_row`;
  • a wide, hot DATA table (a ledger, a feed) keeps its explicit column list, and each OPTIONAL column
    is PROBED INDIVIDUALLY (`select(col).limit(1)`, org-scoped) before the one real select —
    `present_columns` + `select_list`; never a block that can fail on an unrelated column;
  • what is missing is RETURNED (`missing`), so a panel or a statement can say "apply migration X"
    instead of silently reading a default.

Byte-identical when every column exists: `read_row` hands back the same row the block select would
have, and `select_list` spells the same column list the widest block did. NEVER raises — a missing
table reads as no row with every expected column missing.

`table` is a ZERO-ARG callable returning a FRESH query builder (`lambda: client.schema("commcalc")
.table("commission_org_config")`) because a PostgREST builder is single-use and this module issues
more than one query. `scope` applies the ORG SCOPE to a select builder (`lambda q: q.eq("org_id",
org_id)`); it is required, never optional — an unscoped probe is still a read of another tenant's
row existence. No DB, no client import: any object with `.select().eq().limit().execute().data` works,
which is what the proof harnesses drive it with.
"""


class RowRead:
    """The result of `read_row`: the org's row (or None), the columns PRESENT, the expected columns
    MISSING (in the caller's order), and whether the table itself could be read."""
    __slots__ = ("row", "present", "missing", "readable")

    def __init__(self, row, present, missing, readable):
        self.row, self.present, self.missing, self.readable = row, frozenset(present), list(missing), bool(readable)

    def get(self, key, default=None):
        return (self.row or {}).get(key, default)

    def __repr__(self):
        return f"RowRead(row={'yes' if self.row is not None else 'none'}, missing={self.missing}, readable={self.readable})"


def present_columns(table, scope, columns):
    """PROBE PER COLUMN: the subset of `columns` the table has, each asked for on its own
    (`select(col)` scoped, `limit(1)`) so no column's absence can hide another's presence. An empty
    result set still answers (PostgREST validates the column list before it filters), so this works
    for an org with no row. A table that cannot be read has no columns."""
    out = set()
    for c in columns or ():
        try:
            scope(table().select(c)).limit(1).execute()
            out.add(c)
        except Exception:
            continue
    return frozenset(out)


def read_row(table, scope, expected=()):
    """ONE org-scoped row, tolerant of ANY subset of columns: `select("*")`, take what is there.
    `expected` names the columns the caller reads, so `missing` can be reported; when the org has NO
    row, the expected columns are probed individually (the only way to know a column exists without
    a row to show it), so `missing` is right either way."""
    expected = tuple(expected or ())
    try:
        rows = (scope(table().select("*")).limit(1).execute().data) or []
    except Exception:
        return RowRead(None, (), expected, False)
    if rows:
        row = dict(rows[0] or {})
        present = frozenset(row.keys())
        return RowRead(row, present, [c for c in expected if c not in present], True)
    present = present_columns(table, scope, expected)
    return RowRead(None, present, [c for c in expected if c not in present], True)


def select_list(required, optional, present):
    """The column list for the ONE real select over a wide table: every required column plus each
    optional column that `present_columns` found. Spells exactly what the widest block spelled when
    every column exists, in the same order, so the read is byte-identical there."""
    return ",".join(list(required) + [c for c in optional if c in present])
