"""A REPORT'S OWN VERDICT BELONGS ON THE REPORT'S OWN ROW — the one home (index §COMMCALC-68).

Found 2026-11-10 23:40, while trying to read why the Comprehensive Compensation pull had never
landed a row. `commcalc.report_definitions` carries `sweep_last_status` and `sweep_last_detail`
PER REPORT, and a user-visible surface already READS them: `router._setup_documents` sets a
report's `urgent` flag and its `auto_error` text from exactly those two columns. **Nothing has ever
written them.** Both are NULL on every row of every tenant, so a report whose automatic pull fails
every single night shows as not urgent, with no error, on the surface built to show precisely that.

THE CLASS, NOT THE INSTANCE. Every leg of the sweep writes instead to ONE shared column on the
connector's config row — `epay_sweep_config.last_detail` — overwritten by whichever leg ran last.
The comp leg runs at 23:30 America/New_York; the Daily Transaction Detail leg runs every thirty
minutes. So comp's sentence is erased within half an hour, every night. That is not comp's bug: a
run that pulls N reports and records ONE verdict cannot tell anyone which report the verdict is
about, for any N > 1. Three rounds of diagnosis this evening read the transaction report's failures
and reasoned about comp because of it.

The registry written but not wired, for the fourth time here (index §19.18). So this module is the
WRITER, and `harness_empty_pull_verdict.py` fails the build if the caller stops calling it.

ONE FACT, ONE HOME: which columns a report's verdict lives in is `REPORT_STATUS_COLUMNS` here;
which registry row a sweep key belongs to is `epay_sweep.registry_key_of` (dereferencing the
report's own declaration), never restated; and how a long sentence is shortened is
`core.status_detail.fit`, which keeps BOTH ENDS — a report's conclusion is written last, so a head
cut throws away the only part worth reading (§CORE-?? / status_detail's own docstring).

PURE except for `write`, which takes the client. Proof: `harness_empty_pull_verdict.py`.
"""
from app.core import status_detail as _status_detail

#: THE columns a per-report sweep verdict is written to, named ONCE. `router._setup_documents`
#: reads these same two; a third spelling anywhere is the divergence this module exists to stop.
REPORT_STATUS_COLUMNS = ("sweep_last_status", "sweep_last_detail")

#: `commcalc.report_definitions.sweep_last_detail` is text, but the surface shows the first 300
#: characters and the operator reads it in a tooltip. Same budget as the connector's shared column
#: so the two cannot disagree about what fits.
DETAIL_CAP = 600

#: The table and the key its rows are addressed by.
TABLE = "report_definitions"
SCHEMA = "commcalc"


def rows_for(per_report):
    """`{epay_report_key: (status, detail)}` → one row per report, ready to write. PURE.

    The row is keyed by the REGISTRY key (`report_definitions.report_key`), resolved through the
    report's own declaration rather than guessed from the sweep key — `mi` is configured as
    `mi_report`, and that mapping already exists in exactly one place.

    Each sentence is FITTED, not cut: a sweep's conclusion ("0 rows, and the date filter never
    took") is written last."""
    from app.modules.commcalc import epay_sweep as _es

    out = []
    for key, verdict in (per_report or {}).items():
        try:
            rkey = _es.registry_key_of(key)
        except KeyError:
            # A verdict about a report nobody declared is a bug in the caller, not something to
            # write to a guessed row. Say so where an operator will see it, and carry on.
            print(f"WARN report_status: no declared report for sweep key {key!r}; verdict dropped")
            continue
        status, detail = (verdict if isinstance(verdict, (tuple, list)) and len(verdict) == 2
                          else (str(verdict or ""), ""))
        out.append({"report_key": rkey,
                    "sweep_last_status": str(status or "").strip().lower() or "error",
                    "sweep_last_detail": _status_detail.fit(detail, DETAIL_CAP)})
    return out


def write(client, org_id, per_report):
    """Write each report's own verdict to its own `report_definitions` row.

    TWO ROUND TRIPS, whatever the number of reports — not one per report. A sweep is one click, but
    `harness_round_trip_ratchet.py` holds the whole estate to "a loop must not contain a query
    filtered by the loop variable", and a recorder is no place to spend the exemption: the first
    trip asks which of these reports this tenant has actually registered, the second writes them all
    at once. Filtering to the rows that EXIST is also what makes the upsert safe — it can only
    update, never invent a registry row for a report the tenant never registered (which would then
    appear on the Connectors page as a report nobody set up).

    Never raises: this is the recorder of what happened, so it must not be the reason a run is
    reported as having failed. Returns the number of rows written."""
    rows = rows_for(per_report)
    if not rows:
        return 0
    want = {r["report_key"]: r for r in rows}
    try:
        have = ((client.schema(SCHEMA).table(TABLE).select("report_key")
                 .eq("org_id", org_id).in_("report_key", sorted(want)).execute()).data or [])
    except Exception as e:
        print(f"WARN report_status: could not read {TABLE} to record verdicts: "
              f"{type(e).__name__}: {e}")
        return 0
    found = {str(h.get("report_key") or "") for h in have}
    missing = sorted(set(want) - found)
    if missing:
        # REPORTED, never papered over: a report the sweep pulls for an org that has no registry
        # row for it has nowhere to show its status, and that is a setup gap the operator must fix.
        print(f"WARN report_status: org {org_id} has no {TABLE} row for {missing} — "
              f"their verdicts have nowhere to be shown")
    payload = [{"org_id": org_id, **want[k]} for k in sorted(found & set(want))]
    if not payload:
        return 0
    try:
        (client.schema(SCHEMA).table(TABLE)
         .upsert(payload, on_conflict="org_id,report_key").execute())
    except Exception as e:
        print(f"WARN report_status: could not record verdicts for {sorted(found & set(want))}: "
              f"{type(e).__name__}: {e}")
        return 0
    return len(payload)
