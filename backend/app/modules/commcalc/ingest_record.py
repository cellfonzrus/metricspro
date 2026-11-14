"""THE ONE HOME for WRITING the ingest journals — "this ingest happened, and this is what it did".

Two tables answer that question for every surface that shows it: `commcalc.upload_trace` (mig 202,
the owner's debug-first record of EVERY ingest path) and the older `commcalc.upload_log` (mig 007,
which the Upload page's history and the connectors page read per `file_type`). Nothing here decides
anything; it writes, and it never raises into an ingest.

WHY THIS MODULE EXISTS (owner report 2026-11-14, "please check and fix, i need these uploads to work
automatically"). Two of the three Data Imports tiles showed a MANUAL upload from 8 October while the
ePay owners-portal sweep had been landing their rows automatically every night. The rows were there;
the RECORD was not. The sweep has three storage paths — the month replace, the per-day replace and
the DTD `ingest` hook — and each decided for itself whether to write a landing record. Exactly one
of them did, so one report's tile was right and two were a month stale. mig 202's own comment claims
"EVERY path … manual upload, the email sweep, the FTP sweep, and the feed→raw_sales promotion"; the
portal sweep was never one of them.

The CLASS, not the instance: "a feed received data, when, and how many rows" must have ONE writer
that every lander dereferences, or a new storage path is silent by default. `router._write_upload_trace`
was already that home for the upload endpoints, but it lives in `router`, which imports the sweep —
so the sweep could not reach it and grew its own half-record instead. The home moved here, where
both can read it, and `harness_upload_trace_coverage` fails the build on a second writer.
"""
SCHEMA = "commcalc"
TRACE_TABLE = "upload_trace"
LOG_TABLE = "upload_log"

# Which table each upload_type lands in — so a trace record can name the target table without
# threading a local TABLE_MAP through every caller. Kept in sync with router.upload_file's TABLE_MAP.
TARGET_TABLE = {
    "sales": "raw_sales", "daily_sales": "daily_sales_feed", "payment_detail": "raw_payment_detail",
    "mi_report": "raw_mi", "dlar_rep": "raw_dlar_rep", "dlar_store": "raw_dlar_store",
    "catalog": "raw_catalog", "master_cats": "raw_categories", "comp_report": "raw_comp_report",
    "ma_commission": "raw_ma_commission", "ma_daily_tx": "raw_ma_daily_tx",
    "ma_fulfillment": "raw_ma_fulfillment", "x_report": "pos_tender_summary",
    "inventory_aging": "inventory_aging", "ma_overview": "ma_overview_upload",
}


def table_name(table):
    """A relation name as the journals record it: bare, with any schema prefix dropped.

    A report declares its target table either bare (`raw_comp_report`) or schema-qualified
    (`commcalc.raw_epay_daily_tx`); both name the same relation, and two spellings in one column
    would make "which table did this land in" unanswerable by equality."""
    return str(table or "").strip().split(".")[-1] or None


def write_trace(client, row):
    """Insert ONE row into commcalc.upload_trace. The ONLY insert into that table in the codebase.

    NON-RAISING by contract: a missing table (mig 202 unrun) or a transient failure is printed and
    swallowed, because a diagnostic must never be the reason an ingest fails. Returns True when the
    row was written."""
    try:
        client.schema(SCHEMA).table(TRACE_TABLE).insert(row).execute()
        return True
    except Exception as e:
        print(f"WARN upload_trace insert skipped (run mig 202?): {e}")
        return False


def write_log(client, row):
    """Insert ONE row into commcalc.upload_log (mig 007) — the Upload page's history and the
    connectors page's "last upload per report". Non-raising, same contract as write_trace."""
    try:
        client.schema(SCHEMA).table(LOG_TABLE).insert(row).execute()
        return True
    except Exception as e:
        print(f"WARN upload_log insert skipped (run 007_upload_log.sql?): {e}")
        return False


def sweep_landed(client, org_id, *, upload_type, target_table, label, rows, mode,
                 periods=None, date_counts=None, period_label=None, source="portal",
                 filename=None, note=None):
    """Record that an UNATTENDED pull landed rows, in both journals, exactly as a manual upload is
    recorded — so every surface that answers "when did this report last receive data" sees the
    sweep's work without knowing a sweep exists.

    A zero-row pull is NOT recorded as a landing. Whether this particular zero is the source's own
    answer or a failure is not a journal's question, and it is already answered on the report's own
    `report_definitions` row by `report_status` (index §COMMCALC-68); a zero written here would make
    every quiet day read as "an attempt saved nothing" on the tile forever.

    Returns {"trace": bool, "log": bool, "recorded": bool}."""
    tbl = table_name(target_table)
    if not rows:
        return {"trace": False, "log": False, "recorded": False, "reason": "nothing_landed"}
    row = {
        "org_id": org_id, "source": source, "filename": filename or label,
        "upload_type": upload_type, "target_table": tbl or TARGET_TABLE.get(upload_type),
        "rows_in": int(rows), "rows_saved": int(rows), "status": "ok",
        "periods": periods or None, "date_counts": date_counts or None,
        "note": (str(note)[:500] if note else f"{label} ({mode})"),
    }
    trace = write_trace(client, row)
    log = write_log(client, {"org_id": org_id, "file_type": upload_type,
                             "period": period_label, "filename": filename or label,
                             "rows_saved": int(rows)})
    return {"trace": trace, "log": log, "recorded": bool(trace or log)}
