"""SUPPLIER-INVOICE FEED ADAPTER — what a supplier BILLED us, pulled from the supplier's own portal.

WHY THIS FEED EXISTS. Owner, 2026-10-27: *"i want to pull in invoices from ups and then compare to
the sales reports to make sure every single shipment is being paid for and cash collected by the
sales reps"*. He had already created the login row; it sat on `processor = 'other'`, which no
dispatcher serves, so Run now stamped *"scraper for 'other' not wired yet"* and stopped. This module
is the pull side. The reconciliation is its own change and READS these rows.

WHAT IT IS, EXACTLY. The THIRD adapter of `portal_report_pull.py` (index §COMMCALC-07) — it supplies
the four contract functions and nothing else. The login, the second factor, the durable session, the
cron, the route gate, the session-health chip, the cooldown, the declared per-report filters
(§COMMCALC-35) and the pull loop are all the EXISTING machinery, unchanged and not copied. What is
new here is only: which invoice reports to pull (config on the source row), how an invoice line is
read (`supplier_invoice_feed`, PURE) and the one table they land in.

A CONNECTOR KIND, NOT A SUPPLIER. `CONNECTOR = "supplier_invoice"` is the `data_source.processor` of
*a supplier portal that publishes the invoices it has billed us* — the same posture as
`pos_report_sweep.CONNECTOR` and `supply.portal.SUPPLY_PROCESSOR`. No supplier's name, URL, menu
wording, report name or service name appears in this repository: the portal's address is
`data_source.portal_url`, its report list is `data_source.portal_reports`, its selectors and filters
are `data_source.portal_calibration` captured once on the live-login screencast, and its column
vocabulary is `portal_calibration.column_synonyms`. That is RULE TWO, and it is also the owner's own
standing requirement that none of this be hard-coded. The consequence is deliberate and stated: this
connector does nothing at all until an operator declares a portal and at least one report — and it
says so rather than pulling zero rows and looking healthy.

Proof: `backend/harness_supplier_invoice_feed.py` (the parser + the adapter contract, DB-free and
browser-free).
"""
from app.modules.commcalc import portal_report_pull as prp
from app.modules.commcalc import supplier_invoice_feed as feed

# The `data_source.processor` value this feed serves. Spelled ONCE, here — `router._SOURCE_SCRAPERS`
# and the connector-registry seed both dereference this constant rather than re-spelling it.
CONNECTOR = "supplier_invoice"

# The table these rows land in, named only by this module (the §76 convention: a table is named by
# its owning module through a constant, and the lock fails the build on a second speller).
TABLE = "supplier_invoice_line"
SCHEMA = "commcalc"

# The grain of a landed row, and the natural key a re-pull RESTATES an invoice line on. Mirrors the
# unique index in the migration — the harness reads both and fails if they drift.
ON_CONFLICT = "org_id,source_id,invoice_number,shipment_key,service_key,charge_date"

_LAND_COLS = ("org_id", "source_id", "supplier_key", "report_key", "invoice_number", "invoice_date",
              "due_date", "charge_date", "store_code", "store_key", "store_label", "shipment_ref",
              "shipment_key", "counter_ref", "counter_key", "service", "service_key", "quantity",
              "weight", "amount", "source_line")

DEFAULT_WINDOW_DAYS = 31
SESSION_TTL_HOURS = 8


# ── THE ADAPTER CONTRACT (portal_report_pull.REQUIRED) ───────────────────────────────────────────
def connector_keys():
    return (CONNECTOR,)


def portal_descriptor(connector=None):
    """The feed's house descriptor. `base_url` is deliberately None: the portal's address is the
    operator's declaration on the source row, not a constant in this repo.

    The window is a MONTH, not the POS feed's week, because a supplier bills on its own cycle and a
    week-long window would miss an invoice that arrives fortnightly. It is still only a default — the
    source's `portal_window_days` overrides it."""
    if connector is not None and prp.connector(connector) != CONNECTOR:
        return None
    return {"key": CONNECTOR,
            "label": "Supplier invoices (what a supplier billed us)",
            "base_url": None,
            "date_window_days": DEFAULT_WINDOW_DAYS,
            "session_ttl_hours": SESSION_TTL_HOURS}


def report_specs(connector=None, enabled_keys=None, src_row=None):
    """The invoice reports to pull, DECLARED on the source row (`data_source.portal_reports`).

    The SAME declaration shapes the POS-report feed accepts, read by the SAME code: a bare key, or
    the full object. Re-deriving it here would be a second reader of one config column, so this
    dereferences `pos_report_sweep.report_specs` and only overrides the grain word."""
    from app.modules.commcalc.pos_report_sweep import report_specs as _specs
    out = []
    for spec in _specs(connector=None, enabled_keys=enabled_keys, src_row=src_row):
        s = dict(spec)
        s["grain"] = "invoice_line"
        out.append(s)
    return tuple(out)


def setup_hint(src_row=None):
    """What an operator must do before this connector can pull anything. The loop appends it when no
    report is declared, so an unconfigured source reads as unconfigured — not as a failed pull."""
    return ("No invoice report is declared on this source yet. Open the live login, reach the "
            "invoice or billing-detail report once, and save it on this source (its name, the menu "
            "words that reach it, its date filters and the export control) — then the scheduled "
            "pull rides the saved session.")


# ── ingest ───────────────────────────────────────────────────────────────────────────────────────
def _store_resolver(client, org_id):
    """The platform's ONE store resolver (`account/store_identity` via `coa.store_resolver`, §64).
    None on any failure — `normalize` then keeps the supplier's own spelling and the row reads as
    unplaced, which is the honest degradation rather than a guess."""
    try:
        from app.modules.account import coa
        return coa.store_resolver(client, org_id)
    except Exception:
        return None


def read_table(payload, filename=""):
    """Payload → list-of-lists, with the banner above the real header skipped by THIS feed's own
    synonym table (the parsing itself is the one home's)."""
    return prp.read_table(payload, filename, header_score=feed.header_score)


def parse_report(connector, spec, payload, *, src_row=None, filename="", resolve_store=None):
    """One downloaded invoice report → normalized lines. No DB, no browser (the resolver is passed
    in, which is what keeps `supplier_invoice_feed` pure and provable)."""
    src_row = src_row or {}
    cal = (src_row.get("portal_calibration") or {})
    cal = cal if isinstance(cal, dict) else {}
    per_report = cal.get(spec["key"]) if isinstance(cal.get(spec["key"]), dict) else {}
    merged = dict(cal)
    merged.update(per_report or {})
    return feed.normalize(read_table(payload, filename), calibration=merged,
                          org_id=src_row.get("org_id"), source_id=src_row.get("id"),
                          connector=prp.connector(connector), report_key=spec.get("key"),
                          resolve_store=resolve_store,
                          store_code_default=src_row.get("account_id"))


def _project(rows, org_id, source_id):
    """Rows → the table's columns, with the KEY columns the upsert needs. `service_key` is DERIVED
    here by the same `match_key` the recon will use — never a second normalizer."""
    out = []
    for r in rows or []:
        d = dict(r)
        d["org_id"] = org_id
        d["source_id"] = source_id
        d["service_key"] = feed.match_key(d.get("service"))
        d["shipment_key"] = d.get("shipment_key") or ""
        d["counter_key"] = d.get("counter_key") or ""
        d["store_key"] = d.get("store_key") or ""
        out.append({c: d.get(c) for c in _LAND_COLS})
    return out


def store_rows(client, org_id, source_id, rows, chunk=400):
    """Upsert on the natural key, so a re-pull of an invoice RESTATES its lines instead of
    duplicating them. `feed.dedupe` has already summed the lines that share the grain — upserting
    them one at a time would let the last line overwrite the rest and a shipment's charge would read
    as one surcharge's amount."""
    payload = _project(feed.dedupe(rows), org_id, source_id)
    saved = 0
    for i in range(0, len(payload), chunk):
        client.schema(SCHEMA).table(TABLE).upsert(payload[i:i + chunk],
                                                  on_conflict=ON_CONFLICT).execute()
        saved += len(payload[i:i + chunk])
    return saved


def ingest_report(client, org_id, src_row, connector, spec, payload, filename=""):
    """Parse → persist ONE invoice report. `delivered` is True only when rows actually landed,
    mirroring `router._pull_delivered`, so the live session, the data_source stamp and the UI can
    never disagree about what success means.

    A line the export could not account for is carried up as an `unresolved` item — a human declares
    the missing column or fixes the export, and until then its charge is REPORTED rather than folded
    into another line or counted as a store's."""
    parsed = parse_report(connector, spec, payload, src_row=src_row, filename=filename,
                          resolve_store=_store_resolver(client, org_id))
    rows = parsed["rows"]
    saved = store_rows(client, org_id, (src_row or {}).get("id"), rows) if rows else 0
    unresolved = []
    for s in (parsed.get("skipped") or [])[:50]:
        item = {"why": s.get("why"), "invoice_number": s.get("invoice_number")}
        if item not in unresolved:
            unresolved.append(item)
    for r in rows:
        if not r.get("store_code"):
            item = {"store_label": r.get("store_label"),
                    "why": "this store spelling is not mapped"}
            if item not in unresolved:
                unresolved.append(item)
    return {"report": spec["key"], "label": spec.get("label"), "rows_parsed": len(rows),
            "rows_ingested": saved, "delivered": saved > 0,
            "warnings": parsed.get("warnings") or [],
            "invoice_totals": feed.totals_by_invoice(rows),
            "unresolved": unresolved[:50], "unresolved_count": len(unresolved)}


def run_supplier_invoice_sweep(client, org_id, src_row):
    """The SCHEDULED (cold) pull — `router._SOURCE_SCRAPERS` dispatches here. The restore itself is
    the one home's `run_cold_sweep`."""
    return prp.run_cold_sweep(client, org_id, src_row)
