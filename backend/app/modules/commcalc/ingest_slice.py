"""Pure slice-scoping rules for ingest replaces — WHAT an uploaded file is allowed to delete.

STDLIB-ONLY on purpose: these rules decide which existing rows an upload retires, i.e. they are
money-destroying if wrong, so they are proven DB-free by backend/harness_ma_slice_replace.py and
backend/harness_ingest_partition_replace.py. Keep this module import-clean (no pandas / fastapi /
supabase) so the proofs stay runnable anywhere.

A manual replace used to delete EVERYTHING in (org, period) — or, for date-grain feeds, everything
in (org, day) — before inserting. That is correct only when a scope holds exactly ONE
independently-uploaded slice. It does not here: one org can hold TWO companies / TWO master-agent
portals, each with its own export, and stores whose sales arrive in separate files. MEASURED DAMAGE:

  • 2026-07-29 `MA Daily Tx SubMA.xls` saved 16,409 July rows — July then held 4,902, Nova only.
  • 2026-08-04 `MA Daily Tx SubMA (1).xls` saved 3,417 (Jul+Aug, Luxelink) — 1,903 survived
    (Aug 1–3); the July half was destroyed on 2026-08-11 by `MA Daily Tx SubMA Nova July.xls`.
  • 2026-08-08 22:00 file (2) saved 3,006 August rows — destroyed 16 MINUTES later by file (3).
  • `raw_sales` June holds 6 of 20 stores — the same fingerprint.
  • 2026-09-02 (the DATE_KEYED per-day path this module now also covers): uploading the Novawave
    August MA Commission wiped the LuxeLink=VidaPay Chicago rows for every day the file contained —
    raw_ma_commission August 824 → 364 rows, 750+ devices un-paid in the reconciliation. Same org
    (854f6d7b-…), two portals, one table.

THE SCOPE IS TWO-DIMENSIONAL, and both halves are load-bearing:
  PARTITION — the column that says WHOSE slice this is (the MA account, the store). Without it, one
              company's upload deletes the other's rows for that period/day.
  DATE      — the file's own date column: a [min, max] range for period-replace paths
              (replace_scope/apply_scope), or the exact day-set for the per-day DATE_KEYED path
              (day_replace_filters). Without it, an Aug 4–8 file for the SAME account still deletes
              that account's Aug 1–3 rows — which is exactly the 08-08 pair.
Together they mean: **a file replaces its own slice and nothing else.** Re-uploading the identical
file is still idempotent (same partition, same dates ⇒ delete-then-insert, never a duplicate).

Any table not listed here, or a file whose partition column is blank on ANY row, keeps the wider
legacy behaviour byte-for-byte — an honest whole-scope delete, never a silent no-delete that would
double-count on the next upload.

These are TABLE-STRUCTURE facts (which column on each raw table identifies the feeding account),
not carrier/tenant policy — same standing as the DATE_KEYED date-column map, so no config table
(RULE TWO does not apply to schema facts; there is nothing per-org to override).
"""

INGEST_PARTITION = {
    "raw_ma_daily_tx":    {"partition": "account_id",          "date": "tx_date"},
    "raw_ma_commission":  {"partition": "merchant_account_id", "date": "tx_date"},
    # tspid = the dealer's account on the fulfillment marketplace (mig 083). Added 2026-09-02 with
    # the incident fix — same two-portal exposure as the other raw_ma_* tables.
    "raw_ma_fulfillment": {"partition": "tspid",               "date": "date_ordered"},
    "raw_sales":          {"partition": "store",               "date": "trans_date"},
}


def replace_scope(table, mapped):
    """The (partition_col, values, date_col, lo, hi) a file may replace — or None to keep the legacy
    wide delete. None is returned whenever the file cannot prove its own slice: unknown table,
    no partition values, or no usable dates. Narrowing on a GUESS would strand rows the next upload
    then silently deletes, so the honest fallback is the old behaviour plus a note."""
    spec = INGEST_PARTITION.get(table)
    if not spec or not mapped:
        return None
    pcol, dcol = spec["partition"], spec["date"]
    vals = sorted({str(r.get(pcol)).strip() for r in mapped
                   if r.get(pcol) is not None and str(r.get(pcol)).strip() != ""})
    if not vals or len(vals) != len({v for v in vals}):
        return None
    # Every row must carry the partition value; one blank row means the file's slice is not provable.
    if any(r.get(pcol) is None or str(r.get(pcol)).strip() == "" for r in mapped):
        return None
    dates = sorted({str(r.get(dcol))[:10] for r in mapped
                    if r.get(dcol) and str(r.get(dcol))[:10]})
    if not dates:
        return None
    return {"partition_col": pcol, "values": vals, "date_col": dcol, "lo": dates[0], "hi": dates[-1]}


def apply_scope(q, scope):
    """Narrow a delete/select to the file's own slice (period-replace paths). Kept in ONE place so
    the snapshot and the delete can never disagree about what is being removed — a restore that
    covers a different slice than the delete is worse than no restore at all."""
    if not scope:
        return q
    return (q.in_(scope["partition_col"], scope["values"])
             .gte(scope["date_col"], scope["lo"]).lte(scope["date_col"], scope["hi"]))


def day_replace_filters(table, mapped, day_col, feed_dates):
    """Filters for the DATE_KEYED per-day replace path (/upload/{file_type}) — the 2026-09-02 fix.

    Returns (filters, scope):
      filters — [('in', column, values), …] the delete/select must apply IN ADDITION to the
                caller's org_id filter (tenancy stays the caller's job, multi-tenant rule).
      scope   — the account slice actually proven from the file (replace_scope result), or None.

    When every incoming row carries the table's partition value (INGEST_PARTITION), the replace
    narrows to (org, day-set, account-set): two portals feeding the same (org, day) coexist, and a
    re-upload still replaces its own slice cleanly (idempotent — same days, same accounts). When
    the slice is NOT provable (unknown table, or any blank account value), the filters degrade to
    the legacy (org, day-set) whole-day replace — a REAL delete either way, never a silent
    no-delete that double-counts on re-upload."""
    filters = [("in", day_col, list(feed_dates))]
    scope = replace_scope(table, mapped)
    if scope:
        filters.append(("in", scope["partition_col"], scope["values"]))
    return filters, scope


def apply_filters(q, filters):
    """Apply day_replace_filters output to a supabase-style query builder. One place, so the
    prior-count, the snapshot and the delete can never disagree about the slice."""
    for kind, col, val in filters:
        q = q.in_(col, val) if kind == "in" else q.eq(col, val)
    return q
