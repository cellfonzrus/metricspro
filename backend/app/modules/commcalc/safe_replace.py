"""Atomic-in-effect REPLACE for a scoped set of raw rows (period, or a list of days).

THE DEFECT THIS FIXES (live, 2026-08-09). Every replace path in this module was written
delete-then-insert:

    delete(period)          # committed immediately
    for batch in rows:      # <-- if ANY batch raises here...
        insert(batch)       #     the period is already gone and the replacement never lands

`commcalc.raw_comp_report` April 2026 held 10,431 rows. The owner uploaded the April comp file, the
insert failed on row 0 (`invalid input syntax for type integer: "1.0"` — see calculator.safe_int),
and April was left with ZERO rows. Every retry of a bad file destroyed the month again. There was a
guard for "the file mapped to zero rows" but none for "mapped fine, then the insert failed", which
is the more common failure: a type mismatch, a column drift, a dropped connection mid-upload.

THE INVARIANT. No existing row is removed until the full replacement is known to have landed:

    insert the new rows first  ->  all batches OK?  ->  yes: delete exactly the OLD rows
                                                    ->  no:  delete exactly the rows WE inserted,
                                                             leaving the scope byte-identical, then
                                                             raise with that fact stated

A failure is therefore a no-op, and a retry of a broken file is harmless. This also subsumes what
the sweep's REPLACE_MIN_ROWS / REPLACE_MIN_RETAIN partial-collapse guard is groping at from the
other end: that guard refuses a suspiciously SMALL pull, this refuses a FAILED one. They are
complementary and both still apply.

HOW OLD AND NEW ARE TOLD APART. Preferably by `created_at`, which Postgres stamps itself, so there
is no app/db clock skew: the first insert response tells us the database's own timestamp for the
new rows, and everything in the scope older than that is by definition the previous load. Three
tables in this module have no `created_at` (notably `daily_sales_feed`), so for those the old row
ids are paged out FIRST and deleted by id. The strategy is chosen BEFORE anything is written, never
half-way through.

KNOWN LIMIT, stated rather than hidden: two concurrent replaces of the SAME scope still interleave
badly — the loser's rollback can delete the winner's rows. That is true of the delete-then-insert
code this replaces as well (where it was strictly worse), and serialising uploads is a separate
change. Reads taken DURING a successful swap briefly see old+new together; previously they saw an
empty period for the whole insert, so the exposure window is the same length and the failure mode
is now "transiently doubled" rather than "transiently zero, permanently zero if the insert dies".
"""

_CHUNK = 500          # insert batch size — unchanged from the call sites this replaces
_ID_DELETE_CHUNK = 150  # ids per DELETE ... IN (...) so the PostgREST URL stays well under limits
_PAGE = 1000          # id pagination page size for the no-created_at fallback


class ReplaceFailed(Exception):
    """The insert failed. Carries whether the previous data was successfully preserved."""

    def __init__(self, message, *, restored, inserted, orphaned=0):
        super().__init__(message)
        self.restored = restored      # True  = the scope is exactly as it was before the call
        self.inserted = inserted      # how many rows had been inserted when it broke
        self.orphaned = orphaned      # rows we inserted but could NOT clean up (restored=False)


def _tbl(client, schema, table):
    return client.schema(schema).table(table)


def _scope_count(client, schema, table, scope):
    q = _tbl(client, schema, table).select("id", count="exact")
    return (scope(q).limit(1).execute().count) or 0


def _scope_has_created_at(client, schema, table, scope):
    """Does this table carry created_at? Decided from a real row in the scope, falling back to a
    bare table probe when the scope is empty."""
    try:
        r = scope(_tbl(client, schema, table).select("*")).limit(1).execute().data
        if r:
            return "created_at" in r[0]
        r = _tbl(client, schema, table).select("*").limit(1).execute().data
        return bool(r) and "created_at" in r[0]
    except Exception:
        return False


def _page_scope_ids(client, schema, table, scope):
    """Every id in the scope, ORDERED and paged. The ordering is not cosmetic: an unordered range
    read silently truncates and would leave old rows behind (see the raw_sales .limit(60000) bug)."""
    out, start = [], 0
    while True:
        q = scope(_tbl(client, schema, table).select("id")).order("id").range(start, start + _PAGE - 1)
        rows = q.execute().data or []
        out.extend(r["id"] for r in rows if r.get("id"))
        if len(rows) < _PAGE:
            return out
        start += _PAGE


def _delete_ids(client, schema, table, ids):
    """Delete by explicit id in URL-safe chunks. Returns how many we failed to delete."""
    failed = 0
    for i in range(0, len(ids), _ID_DELETE_CHUNK):
        chunk = ids[i:i + _ID_DELETE_CHUNK]
        try:
            _tbl(client, schema, table).delete().in_("id", chunk).execute()
        except Exception:
            failed += len(chunk)
    return failed


def safe_replace(client, table, rows, scope, *, schema="commcalc", chunk=_CHUNK, label=""):
    """Replace the rows matching `scope` with `rows`, without ever leaving the scope empty on error.

    `scope` is a callable applying this replace's filters to a query, e.g.
        lambda q: q.eq("org_id", org_id).in_("period", _pvariants(period))
    It MUST include org_id — this helper does not add tenancy for you (multi-tenant rule).

    Returns {saved, prior, removed, mode, warning}. Raises ReplaceFailed if the insert failed;
    `.restored` on that exception says whether the previous data survived (it normally does).
    """
    prior = _scope_count(client, schema, table, scope)

    # An empty replacement never deletes anything. The callers already guard this; keeping it here
    # means the invariant holds no matter who calls next.
    if not rows:
        return {"saved": 0, "prior": prior, "removed": 0, "mode": "skipped_empty",
                "warning": ("refused to replace %d existing row(s) with an empty set%s"
                            % (prior, f" ({label})" if label else "")) if prior else None}

    use_created_at = _scope_has_created_at(client, schema, table, scope) if prior else True
    old_ids = [] if use_created_at else _page_scope_ids(client, schema, table, scope)

    inserted_ids, t0, saved = [], None, 0
    try:
        for i in range(0, len(rows), chunk):
            batch = rows[i:i + chunk]
            resp = _tbl(client, schema, table).insert(batch).execute()
            data = resp.data or []
            inserted_ids.extend(r["id"] for r in data if isinstance(r, dict) and r.get("id"))
            if t0 is None:
                stamps = [r.get("created_at") for r in data
                          if isinstance(r, dict) and r.get("created_at")]
                if stamps:
                    t0 = min(stamps)
            saved += len(batch)
    except Exception as e:
        # ROLL BACK exactly what we added; the previous load is still untouched underneath.
        orphaned = 0
        if inserted_ids:
            orphaned = _delete_ids(client, schema, table, inserted_ids)
        elif saved and t0:
            try:
                scope(_tbl(client, schema, table).delete()).gte("created_at", t0).execute()
            except Exception:
                orphaned = saved
        restored = orphaned == 0
        raise ReplaceFailed(
            f"{e}"
            + (f" — the existing {prior} row(s) were left untouched (nothing was deleted)."
               if restored else
               f" — WARNING: {orphaned} partially-inserted row(s) could not be cleaned up; "
               f"the previous {prior} row(s) are still present alongside them."),
            restored=restored, inserted=saved, orphaned=orphaned) from e

    # The insert landed in full. NOW retire the previous load.
    removed = 0
    warning = None
    if prior:
        try:
            if use_created_at and t0:
                scope(_tbl(client, schema, table).delete()).lt("created_at", t0).execute()
                removed = prior
            elif old_ids:
                failed = _delete_ids(client, schema, table, old_ids)
                removed = len(old_ids) - failed
                if failed:
                    warning = (f"{failed} superseded row(s) could not be removed — "
                               f"this scope now holds duplicates and needs a manual cleanup.")
            else:
                warning = ("could not identify the previous rows to retire (no created_at and no "
                           "ids); this scope now holds both the old and the new load.")
        except Exception as de:
            warning = (f"new rows are safely in place, but removing the previous load failed: {de}. "
                       f"This scope now holds duplicates and needs a manual cleanup.")

    return {"saved": saved, "prior": prior, "removed": removed,
            "mode": "swapped" if prior else "inserted", "warning": warning}
