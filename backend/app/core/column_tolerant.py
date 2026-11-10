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
    instead of silently reading a default. (WHO reads those words is `core/setup_notice.py`'s job, index
    §19.36: a tenant gets the one neutral sentence at the API boundary, the platform super admin the detail.)

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


# ── WHAT THE READ FOUND ABSENT IS A REPORT ABOUT THE READ, NOT A SWITCH ─────────────────────────
# Every config reader built on `read_row` returns its switches PLUS two keys saying what the row did
# not have (`ma_store_pnl.load_config`, `residual_subs.load_ma_pnl_config`, `billpay_pl.load_config`,
# the plan/prospect readers). Those two keys are a statement about the READ, so a caller comparing a
# loaded config to its defaults must compare the DECISIONS and assert the report separately.
#
# THE CLASS this exists to close: three proof harnesses pinned a config by whole-dict equality
# (`load_config(stub) == default_config()`), which was true before the readers started reporting
# absence and silently false afterwards — 2 reds in `harness_ma_store_pnl.py`, 5 in
# `harness_ma_tx_pnl.py`. A fourth caller had already written the same strip list by hand. One fact,
# one home: the key NAMES live here, beside the reader that writes them, and every comparison
# dereferences `switches` / `absence_report` instead of spelling them again.
ABSENCE_KEYS = ("config_columns_missing", "config_migrations_missing")


def switches(cfg):
    """PURE: a resolved config's DECISIONS — the same mapping without the read's absence report.
    Use it to compare a loaded config against its defaults; use `absence_report` to assert what the
    read found missing. Neither key is a decision and neither may be compared as one."""
    return {k: v for k, v in dict(cfg or {}).items() if k not in ABSENCE_KEYS}


def absence_report(cfg):
    """PURE: just the absence report — `{config_columns_missing, config_migrations_missing}`, each
    defaulting to `[]` so a reader that predates the report reads as "nothing known absent" rather
    than as a missing key."""
    cfg = dict(cfg or {})
    return {k: list(cfg.get(k) or ()) for k in ABSENCE_KEYS}


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


# ── THE SAME RULE ON THE WRITE SIDE (owner defect 2026-11-10, index §4b.1) ──────────────────────
# WHAT HAPPENED. Everything above makes a READ tolerant of a column a hand-applied migration has not
# created yet. The WRITE side had no such rule, and on 2026-11-10 that cost a month of carrier data:
# `dlar_sweep` was taught to land 20 newly-declared store columns, the migration that creates them
# had not been applied, and the insert 400'd with
#
#     PGRST204: Could not find the 'activations_total' column of 'raw_dlar_store' in the schema cache
#
# The sweep deletes the period's rows and then inserts, so the delete committed, the insert never
# landed, and November's 28 store rows were gone. (`safe_replace` is the fix for the DELETE half and
# is dereferenced now; this is the fix for the COLUMN half — otherwise the writer merely fails
# safely four times a day and still lands nothing until a human applies a migration.)
#
# WHY THIS IS THE SAME CLASS AND NOT A SECOND ONE. "Which columns does this table actually have"
# already has one answer in this module, and a write that assumes MORE than a read dares to is the
# §19.18 copy wearing a verb. A declared column the table lacks is DROPPED and REPORTED — never
# guessed, never silently kept, and never allowed to refuse the columns that do exist, because
# refusing them all is how one unapplied migration hides every arriving figure.
#
# ORDERING, stated because it is the whole point: PROBE, then write. A caller that probes after it
# has already deleted has learned nothing in time.
def writable_columns(table, scope, columns):
    """WHICH OF `columns` THIS TABLE HAS, asked BEFORE a write. One `select("*")` when the scope has
    a row (its keys ARE the table's columns, NULLs included), falling back to the per-column probe
    when it does not — the same two strategies `read_row` chooses between, for the same reason.
    NEVER raises: an unreadable table has no columns, so a caller drops everything and says so
    rather than destroying what is already stored. `scope` is required and must carry the org."""
    cols = tuple(columns or ())
    try:
        rows = (scope(table().select("*")).limit(1).execute().data) or []
    except Exception:
        rows = []
    if rows:
        have = frozenset(dict(rows[0] or {}).keys())
        return frozenset(c for c in cols if c in have)
    return present_columns(table, scope, cols)


def drop_absent(rows, present):
    """PURE. `(rows_without_absent_keys, absent_keys_in_first_seen_order)`.

    Byte-identical when every key is present: the SAME row objects are handed back, not copies, so a
    caller cannot tell this ran. That matters — this sits on the path of every feed write, and a
    silent re-shaping of rows that needed no re-shaping is its own defect.

    What is dropped is RETURNED, so the run's evidence can name the column and the migration a
    person must apply. A dropped key is never replaced with None: NULL means "the source did not
    send this", and writing it for "our schema is behind" would make an unapplied migration
    indistinguishable from a figure the portal never published."""
    rows = list(rows or ())
    present = frozenset(present or ())
    absent, seen = [], set()
    for r in rows:
        for k in (r or {}):
            if k not in present and k not in seen:
                seen.add(k)
                absent.append(k)
    if not absent:
        return rows, []
    return [{k: v for k, v in (r or {}).items() if k in present} for r in rows], absent
