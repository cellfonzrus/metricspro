"""PER-STORE BACK-OFFICE FEED ADAPTER — the processor's back office, where every store has its own login.

WHY THIS FEED EXISTS. Owner, 2026-11-10: the payment processor's back office issues one login per
store, and he asked for every available report pulled per store "first time from the beginning of the
year 2026 till date then update very day autonomously", for the processor reconciliation.

WHAT IT IS, EXACTLY. The fourth adapter of `portal_report_pull.py` (index §COMMCALC-07) — it supplies
the four contract functions and nothing else. The login, the second factor, the durable session, the
cron, the route gate, the session-health chip, the cooldown and the pull loop are the EXISTING
machinery, unchanged and not copied. What is new is only the two general facts this feed was the first
to need, each in its own home:

  • **a login can belong to a STORE.** Every row pulled through a store's own login is that store's.
    `data_source.store_code` is the declaration, and `epay_ingest.resolve_stores` now takes it as the
    answer for a terminal the declared merchant registry cannot place — one home, so the intake and
    any later caller inherit it rather than re-deciding.
  • **a pull can owe HISTORY.** `commcalc/portal_backfill.py`, consulted by the loop in the one place
    the window is computed.

A CONNECTOR KIND, NOT A VENDOR (RULE TWO). `CONNECTOR = "processor_backoffice"` is the
`data_source.processor` of *a payment processor's per-store back office*. No vendor's name, host,
menu wording or report name appears in this repository: the portal's address is
`data_source.portal_url`, its report list and each report's LANDING TARGET are
`data_source.portal_reports`, its selectors and filters are `data_source.portal_calibration` captured
once on the live-login screencast, and the store is `data_source.store_code`.

THE ALL-TENANTS RULE, applied. Nothing here has a house default that is really one tenant's words: a
source that has declared no report pulls nothing and says so, and a login with no store and no
declared terminal mapping REFUSES rather than landing money under no store. The only house vocabulary
is the processor report's own COLUMN names (a feed shape, not a tenant's words), and a tenant may
extend it through `portal_calibration.columns`.

WHAT LANDS, AND WHAT IS ONLY EVIDENCE. A declared report names its `target`. The targets this version
knows are in `TARGETS`; today that is the processor's transaction detail, which lands through the
feed's EXISTING ingest (`epay_ingest` → `commcalc.raw_epay_daily_tx`, already read by the bill-pay and
fee recons) rather than a sibling table. A report with no declared target, or one naming a target this
version does not know, lands NOTHING and is reported with the columns it published, so the next report
is mapped from what it actually contains instead of guessed at. A zero-row pull is never a green tick.

Proof: `backend/harness_processor_backoffice.py` + `tests/unit/test_processor_backoffice_sweep.py`
(DB-free, browser-free) and `tests/integration/test_processor_backoffice_pull.py`.
"""
from app.modules.commcalc import portal_report_pull as prp
from app.modules.commcalc import report_columns as rc


def _feed():
    """The processor transaction feed's existing ingest, imported LAZILY.

    Not a style choice: `epay_ingest` reaches `app.core.database`, which imports the Supabase client,
    and this module is imported at module scope by `connector_registry` — which several CI locks run
    under a bare python with nothing installed. A top-level import here turns those locks into a
    ModuleNotFoundError before their first check. The landing needs the feed; naming it does not."""
    from app.modules.commcalc import epay_ingest
    return epay_ingest


# The `data_source.processor` value this feed serves. Spelled ONCE, here.
CONNECTOR = "processor_backoffice"

DEFAULT_WINDOW_DAYS = 7
SESSION_TTL_HOURS = 8

# The processor transaction-detail report's own column names → the fields its ingest already knows
# (`epay_ingest.COLUMNS`, dereferenced at use so this cannot drift from it). House vocabulary for a FEED
# SHAPE, which is the one thing a house default is right for; a tenant adds spellings through
# `portal_calibration.columns`.
TX_COLUMNS = {
    "TransactionID": ("transactionid", "trans id", "transaction id", "txn id"),
    "TransactionSourceID": ("transactionsourceid", "transaction source id", "source id"),
    "InvoiceID": ("invoiceid", "invoice id", "invoice"),
    "SettlementDate": ("settlementdate", "settlement date", "settle date", "business date"),
    "SettlementDay": ("settlementday", "settlement day"),
    "TerminalID": ("terminalid", "terminal id", "terminal"),
    "UserName": ("username", "user name", "user"),
    "Product": ("product code", "product"),
    "ProductTitle": ("producttitle", "product title", "product description"),
    "Type": ("transaction type", "type"),
    "HostTimeStamp": ("hosttimestamp", "host timestamp", "timestamp"),
    "ControlNumber": ("controlnumber", "control number"),
    "Retail": ("retail", "amount"),
    "Discount": ("discount",),
    "Cost": ("cost",),
    "Commission": ("commission",),
}

# A report must be recognizable before a single row of it is believed: without the transaction's own
# identity there is no idempotency key, so landing it would duplicate on the next pull.
TX_REQUIRED = ("TransactionID",)


# ── THE ADAPTER CONTRACT (portal_report_pull.REQUIRED) ───────────────────────────────────────────
def connector_keys():
    return (CONNECTOR,)


def portal_descriptor(connector=None):
    """The feed's house descriptor. `base_url` is deliberately None: the portal's address is the
    operator's declaration on the source row, not a constant in this repo."""
    if connector is not None and prp.connector(connector) != CONNECTOR:
        return None
    return {"key": CONNECTOR,
            "label": "Payment processor back office (per-store logins)",
            "base_url": None,
            "date_window_days": DEFAULT_WINDOW_DAYS,
            "session_ttl_hours": SESSION_TTL_HOURS}


def spec_key(item):
    """THE key of one declared report, whichever shape it was declared in — the §COMMCALC-39 rule,
    dereferenced from the sibling adapter that is its one home."""
    from app.modules.commcalc import pos_report_sweep as prs
    return prs.spec_key(item)


def report_specs(connector=None, enabled_keys=None, src_row=None):
    """The reports to pull, DECLARED on the source row (`data_source.portal_reports`, jsonb).

    Each entry may be a bare key or an object; an object may name its `target` (where its rows land)
    and its `nav` words. A target is never defaulted — an unmapped report is reported with its shape,
    which is how it gets mapped rather than guessed at.
    """
    declared = ((src_row or {}).get("portal_reports")
                if src_row is not None else enabled_keys) or []
    if isinstance(declared, dict):
        declared = [declared]
    out = []
    for item in declared:
        key = spec_key(item)
        if not key:
            continue
        if isinstance(item, dict):
            nav = item.get("nav") or item.get("nav_hints") or ()
            spec = {"key": key,
                    "label": str(item.get("label") or key),
                    "nav": tuple(str(w) for w in nav if str(w).strip()),
                    "export": str(item.get("export") or "csv"),
                    "target": str(item.get("target") or "").strip().lower(),
                    "grain": "transaction"}
        else:
            spec = {"key": key, "label": str(item).strip(),
                    "nav": tuple(w for w in key.split("_") if len(w) > 2),
                    "export": "csv", "target": "", "grain": "transaction"}
        out.append(spec)
    return tuple(out)


def setup_hint(src_row=None):
    """What an operator must do before this connector can pull anything. The loop appends it when no
    report is declared, so an unconfigured source reads as unconfigured — not as a failed pull."""
    return ("No report is declared on this login yet. Open the live login, reach each report once, "
            "and save it on this login (its name, the menu words that reach it, its filters and where "
            "its rows should land) — then the daily pull rides the saved session. Set this login's "
            "store, and the date its history should start from, on the same screen.")


# ── parsing (pure) ───────────────────────────────────────────────────────────────────────────────
def _extra_columns(src_row=None, spec=None):
    """A tenant's own column spellings, declared on the source and narrowed per report."""
    cal = ((src_row or {}).get("portal_calibration") or {})
    if not isinstance(cal, dict):
        return {}
    out = dict(cal.get("columns") or {})
    per = cal.get((spec or {}).get("key")) if isinstance(spec, dict) else None
    if isinstance(per, dict):
        out.update(per.get("columns") or {})
    return out


def read_table(payload, filename=""):
    """Payload → list-of-lists, with the banner above the real header skipped by THIS feed's own
    vocabulary (the parsing itself is the one home's)."""
    return prp.read_table(payload, filename,
                          header_score=lambda row: rc.header_score(row, TX_COLUMNS))


def shape_of(payload, filename=""):
    """What a report PUBLISHED: its header and how many rows followed. Evidence, not a landing — this
    is what an unmapped report contributes, so the next one is mapped from fact."""
    table = prp.read_table(payload, filename)
    if not table:
        return {"columns": [], "rows": 0}
    header = [str(c).strip() for c in table[0]]
    return {"columns": header, "rows": max(0, len(table) - 1)}


def records_from_payload(payload, filename="", src_row=None, spec=None):
    """One downloaded transaction-detail export → the record dicts this feed's ingest already reads.

    PURE. Columns are matched through the ONE column vocabulary (§COMMCALC-38), so a portal that
    prints "Trans ID" still lands. A payload whose header carries none of the required fields yields
    NOTHING — a report we cannot identify is not parsed into plausible-looking rows.
    """
    table = read_table(payload, filename)
    if len(table) < 2:
        return []
    extra = _extra_columns(src_row, spec)
    idx = rc.map_headers(table[0], TX_COLUMNS, extra)
    if any(f not in idx for f in TX_REQUIRED):
        return []
    out = []
    for row in table[1:]:
        if not any(str(c).strip() for c in row):
            continue
        rec = {}
        for field, i in idx.items():
            if field in _feed().COLUMNS:
                rec[field] = rc.cell(row, i)
        if str(rec.get("TransactionID") or "").strip():
            out.append(rec)
    return out


# ── landing ──────────────────────────────────────────────────────────────────────────────────────
def _batch(src_row, spec, filename):
    """The `source_batch` stamped on every landed row: which login, which report, which file. An
    evidence trail that names the login is what makes a per-store pull auditable per store."""
    return ("%s:%s:%s:%s" % (CONNECTOR, (src_row or {}).get("id") or "?",
                             (spec or {}).get("key") or "?", (filename or "")[:60]))[:200]


def _land_processor_daily_tx(client, org_id, src_row, spec, payload, filename):
    """The processor's transaction detail, landed through the feed's EXISTING ingest — never a sibling
    table. The login's store is passed as the answer for a terminal the declared registry cannot
    place; the registry still wins where it has an answer."""
    store = str((src_row or {}).get("store_code") or "").strip()
    recs = records_from_payload(payload, filename, src_row=src_row, spec=spec)
    if not recs:
        return {"rows_ingested": 0, "delivered": False, "shape": shape_of(payload, filename),
                "error": ("This export does not carry the transaction columns this report lands "
                          "(a transaction id is required). Re-calibrate the report, or declare the "
                          "portal's column spellings on this login."),
                "unresolved": [{"report": (spec or {}).get("key"),
                                "why": "the export's columns were not recognized"}]}
    res = _feed().ingest(org_id, recs, source_batch=_batch(src_row, spec, filename),
                             client=client, store_code=store or None)
    saved = int(res.get("saved") or 0)
    unresolved = [{"terminal_id": t.get("terminal_id"), "user_name": t.get("user_name"),
                   "why": "this terminal has no store mapping and this login declares no store"}
                  for t in (res.get("unresolved_terminals") or [])]
    out = {"rows_ingested": saved, "delivered": saved > 0, "rows_parsed": len(recs)}
    if unresolved:
        out["unresolved"] = unresolved[:50]
    return out


# Which declared target lands where. A target this version does not know is REFUSED by name, never
# guessed at — the same posture `derived_build_status` takes to an unknown build key.
TARGETS = {"processor_daily_tx": _land_processor_daily_tx}


def ingest_report(client, org_id, src_row, connector, spec, payload, filename=""):
    """Parse → persist ONE report. `delivered` is True only when rows actually landed.

    Three honest refusals, each with the one sentence an operator can act on: a login with no store
    and no declared terminal mapping (money under no store is worse than money that waited), a report
    with no declared target, and a target this version does not know. Each carries the report's own
    column shape, so what it contains is known rather than guessed.
    """
    spec = dict(spec or {})
    target = str(spec.get("target") or "").strip().lower()
    base = {"report": spec.get("key"), "label": spec.get("label"),
            "rows_ingested": 0, "delivered": False}
    if not target:
        shape = shape_of(payload, filename)
        return {**base, "shape": shape,
                "error": ("This report has no landing target declared, so its rows were not stored. "
                          "Declare where it lands on this login (the columns it published are "
                          "recorded with this run)."),
                "unresolved": [{"report": spec.get("key"), "columns": shape["columns"][:40],
                                "why": "no landing target is declared for this report"}]}
    fn = TARGETS.get(target)
    if fn is None:
        return {**base, "shape": shape_of(payload, filename),
                "error": ("This version does not know the landing target '%s'. Declared targets: %s."
                          % (target, ", ".join(sorted(TARGETS)))),
                "unresolved": [{"report": spec.get("key"), "target": target,
                                "why": "unknown landing target"}]}
    store = str((src_row or {}).get("store_code") or "").strip()
    if not store and not _has_terminal_map(org_id):
        return {**base, "shape": shape_of(payload, filename),
                "error": ("This login has no store declared and this company has no terminal→store "
                          "mapping, so nothing could be placed. Set the store on this login."),
                "unresolved": [{"source_id": (src_row or {}).get("id"),
                                "why": "this login declares no store"}]}
    return {**base, **fn(client, org_id, src_row, spec, payload, filename)}


def _has_terminal_map(org_id):
    """Does this company declare any terminal→store mapping at all? Guarded: when it cannot be read,
    the answer is NO, which makes the refusal above the default rather than the exception."""
    try:
        from app.modules.storeops import merchant_ids as _mids
        return bool(_mids.resolve_map(org_id, _feed().PROCESSOR))
    except Exception:
        return False


def run_backoffice_sweep(client, org_id, src_row):
    """The SCHEDULED (cold) pull — `router._SOURCE_SCRAPERS` dispatches here. The restore itself is the
    one home's `run_cold_sweep`."""
    return prp.run_cold_sweep(client, org_id, src_row)
