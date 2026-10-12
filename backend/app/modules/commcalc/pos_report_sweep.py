"""POS-REPORT FEED ADAPTER — the portal that publishes what a store actually sold, per worker, per day.

WHY THIS FEED EXISTS. Owner, 2026-10-12: the tenant's reps declare the day's categories at closing,
and the back-office portal publishes its OWN report of the same day. He asked for the portal's report
to be pulled daily and *"checked against what the employees declare daily"*. This module is the pull
side of that; the comparison is its own change and reads these rows.

WHAT IT IS, EXACTLY. The second adapter of `portal_report_pull.py` (index §COMMCALC-05) — it supplies
the four contract functions and nothing else. The login, the second factor, the durable session, the
cron, the route gate, the session-health chip, the cooldown and the pull loop are all the EXISTING
machinery, unchanged and not copied. What is new here is only: which reports to pull (config on the
source row), how a report's lines become declared-category figures (`pos_report_feed`, PURE), and the
one table they land in.

A CONNECTOR KIND, NOT A VENDOR. `CONNECTOR = "pos_report"` is the `data_source.processor` of *a
back-office portal that publishes a daily sales report* — the same posture as
`supply/portal.SUPPLY_PROCESSOR`. No vendor's name, URL, menu wording or report name appears in this
repository: the portal's address is `data_source.portal_url`, its report list is
`data_source.portal_reports`, its selectors are `data_source.portal_calibration` captured once on the
live-login screencast, and its line-label → declared-category mapping is
`portal_calibration.line_map`. That is RULE TWO, and it is also the owner's own requirement that none
of this be hard-coded. The consequence is deliberate and stated: **this connector does nothing at all
until an operator declares a portal and at least one report on a source row** — and it says so rather
than pulling zero rows and looking healthy.

Proof: `backend/harness_pos_report_feed.py` (the parser + the adapter contract, DB-free and
browser-free).
"""
from app.modules.commcalc import pos_report_feed as feed
from app.modules.commcalc import portal_report_pull as prp

# The `data_source.processor` value this feed serves. Spelled ONCE, here.
CONNECTOR = "pos_report"

# The table these rows land in, named only by this module (the §76 convention: a table is named by its
# owning module through a constant, and the lock fails the build on a second speller).
TABLE = "portal_metric_day"
SCHEMA = "commcalc"

# The grain of a landed row, and the natural key the upsert restates a day on. Mirrors the unique
# index in migration 1078 — the harness reads both and fails if they drift.
ON_CONFLICT = "org_id,source_id,business_date,store_key,employee_key,metric_key"

_LAND_COLS = ("org_id", "source_id", "portal_key", "report_key", "business_date", "store_code",
              "store_key", "store_label", "employee_name", "employee_key", "metric_key",
              "metric_basis", "line_label", "units", "amount", "source_line")

DEFAULT_WINDOW_DAYS = 7
SESSION_TTL_HOURS = 8


# ── THE ADAPTER CONTRACT (portal_report_pull.REQUIRED) ───────────────────────────────────────────
def connector_keys():
    return (CONNECTOR,)


def portal_descriptor(connector=None):
    """The feed's house descriptor. `base_url` is deliberately None: the portal's address is the
    operator's declaration on the source row, not a constant in this repo."""
    if connector is not None and prp.connector(connector) != CONNECTOR:
        return None
    return {"key": CONNECTOR,
            "label": "Back-office POS report (declared categories)",
            "base_url": None,
            "date_window_days": DEFAULT_WINDOW_DAYS,
            "session_ttl_hours": SESSION_TTL_HOURS}


def report_specs(connector=None, enabled_keys=None, src_row=None):
    """The reports to pull, DECLARED on the source row (`data_source.portal_reports`, jsonb).

    Two accepted shapes, because the merchant feed's column already carries one of them:
      • a list of objects  `[{"key": "...", "label": "...", "nav": ["...", "..."]}]` — the full
        declaration, which is what this connector needs;
      • a list of strings  `["worker_sales_by_category"]` — a bare key; the key's own words become the
        navigation hint, which is enough on a portal whose menu says what the report is called.
    `enabled_keys` filters an object list the same way it filters the merchant feed's.
    """
    declared = ((src_row or {}).get("portal_reports")
                if src_row is not None else enabled_keys) or []
    if isinstance(declared, dict):
        declared = [declared]
    want = None
    if src_row is not None and enabled_keys and isinstance(enabled_keys, (list, tuple, set)):
        want = {str(k).strip().lower() for k in enabled_keys if isinstance(k, str)}
    out = []
    for item in declared:
        if isinstance(item, str):
            key = item.strip().lower().replace(" ", "_").replace("-", "_")
            if not key:
                continue
            spec = {"key": key, "label": item.strip(),
                    "nav": tuple(w for w in key.split("_") if len(w) > 2),
                    "export": "csv", "grain": "metric_day"}
        elif isinstance(item, dict):
            key = str(item.get("key") or "").strip().lower()
            if not key:
                continue
            nav = item.get("nav") or item.get("nav_hints") or ()
            spec = {"key": key, "label": str(item.get("label") or key),
                    "nav": tuple(str(w) for w in nav if str(w).strip()),
                    "export": str(item.get("export") or "csv"),
                    "grain": "metric_day",
                    "line_map": item.get("line_map") or {}}
        else:
            continue
        if want is not None and spec["key"] not in want:
            continue
        out.append(spec)
    return tuple(out)


def setup_hint(src_row=None):
    """What an operator must do before this connector can pull anything. The loop appends it when no
    report is declared, so an unconfigured source reads as unconfigured — not as a failed pull."""
    return ("No report is declared on this source yet. Open the live login, reach the daily report "
            "once, and save it on this source (its name, the menu words that reach it, and the export "
            "control) — then the daily pull rides the saved session.")


# ── ingest ───────────────────────────────────────────────────────────────────────────────────────
def _catalog(client, org_id):
    """The tenant's declared-metric catalog, DEREFERENCED from its single construction site. Building
    it here would be the second construction site `harness_metric_catalog_lock.py` exists to forbid."""
    try:
        from app.modules.closing.router import _metric_catalog
        return _metric_catalog(client, org_id) or []
    except Exception:
        return []


def _store_resolver(client, org_id):
    """The platform's ONE store resolver (`account/store_identity` via `coa.store_resolver`). None on
    any failure — `normalize` then keeps the portal's own spelling and reports the row as unplaced,
    which is the honest degradation rather than a guess."""
    try:
        from app.modules.account import coa
        return coa.store_resolver(client, org_id)
    except Exception:
        return None


def _line_map(src_row, spec):
    """The tenant's portal-line → declared-category mapping: the source's own map, overlaid by the
    report's. One fact, one home (`portal_calibration`), with the narrower declaration winning."""
    cal = (src_row or {}).get("portal_calibration") or {}
    out = dict((cal.get("line_map") or {}) if isinstance(cal, dict) else {})
    per_report = (cal.get(spec.get("key")) or {}) if isinstance(cal, dict) else {}
    if isinstance(per_report, dict):
        out.update(per_report.get("line_map") or {})
    out.update((spec.get("line_map") or {}))
    return out


def read_table(payload, filename=""):
    """Payload → list-of-lists, with the banner above the real header skipped by THIS feed's own
    synonym table (the parsing itself is the one home's)."""
    return prp.read_table(payload, filename, header_score=feed.header_score)


def parse_report(connector, spec, payload, *, src_row=None, filename="", catalog=None,
                 resolve_store=None):
    """One downloaded report → normalized rows. No DB, no browser (the catalog and the resolver are
    passed in, which is what keeps `pos_report_feed` pure and provable)."""
    src_row = src_row or {}
    cal = (src_row.get("portal_calibration") or {})
    per_report = (cal.get(spec["key"]) or {}) if isinstance(cal.get(spec["key"]), dict) else {}
    merged_cal = dict(cal if isinstance(cal, dict) else {})
    merged_cal.update(per_report)
    return feed.normalize(read_table(payload, filename),
                          catalog=catalog, line_map=_line_map(src_row, spec),
                          calibration=merged_cal, org_id=src_row.get("org_id"),
                          source_id=src_row.get("id"), connector=prp.connector(connector),
                          report_key=spec.get("key"), resolve_store=resolve_store,
                          store_code_default=src_row.get("account_id"))


def _project(rows, org_id, source_id):
    """Rows → the table's columns, with the two KEY columns the upsert needs. `store_key` and
    `employee_key` are the normalized spellings the natural key is built on, so two spellings of one
    store-day-worker-category restate each other instead of landing twice. They are DERIVED here, by
    the same `feed.norm` the matcher uses — never a second normalizer."""
    out = []
    for r in rows or []:
        d = dict(r)
        d["org_id"] = org_id
        d["source_id"] = source_id
        d["store_key"] = feed.norm(d.get("store_code") or d.get("store_label")) or ""
        d["employee_key"] = feed.norm(d.get("employee_name")) or ""
        out.append({c: d.get(c) for c in _LAND_COLS})
    return out


def store_rows(client, org_id, source_id, rows, chunk=400):
    """Upsert on the mig-1078 natural key, so a re-pull of a day RESTATES it instead of duplicating.
    `feed.dedupe` has already summed the lines that share the grain — upserting them one at a time
    would let the last line overwrite the rest and a category's day would read as one product's
    total."""
    payload = _project(rows, org_id, source_id)
    saved = 0
    for i in range(0, len(payload), chunk):
        client.schema(SCHEMA).table(TABLE).upsert(payload[i:i + chunk],
                                                  on_conflict=ON_CONFLICT).execute()
        saved += len(payload[i:i + chunk])
    return saved


def ingest_report(client, org_id, src_row, connector, spec, payload, filename=""):
    """Parse → persist ONE report. `delivered` is True only when rows actually landed, mirroring
    `router._pull_delivered`, so the live session, the data_source stamp and the UI can never disagree
    about what success means. An unmapped report line is carried up as an `unresolved` item — a human
    maps it or adds the category, and until then its figures are reported, never folded into another
    category and never counted as a store's."""
    catalog = _catalog(client, org_id)
    if not catalog:
        return {"report": spec["key"], "label": spec.get("label"), "rows_ingested": 0,
                "delivered": False,
                "error": "This tenant has declared no closing categories yet, so a report line has "
                         "nothing to map to. Add the categories on the closing-categories screen."}
    parsed = parse_report(connector, spec, payload, src_row=src_row, filename=filename,
                          catalog=catalog, resolve_store=_store_resolver(client, org_id))
    rows = parsed["rows"]
    saved = store_rows(client, org_id, (src_row or {}).get("id"), rows) if rows else 0
    unplaced = [r for r in rows if not r.get("store_code")]
    unresolved = [{"line": u["line"], "basis": u["basis"], "amount": u["amount"], "units": u["units"]}
                  for u in (parsed.get("unmapped") or [])]
    for r in unplaced[:20]:
        item = {"store_label": r.get("store_label"), "why": "this store spelling is not mapped"}
        if item not in unresolved:
            unresolved.append(item)
    return {"report": spec["key"], "label": spec.get("label"), "rows_parsed": len(rows),
            "rows_ingested": saved, "delivered": saved > 0,
            "skipped": (parsed.get("skipped") or [])[:20],
            "warnings": parsed.get("warnings") or [],
            "stale_map": parsed.get("stale_map") or [],
            "unresolved": unresolved[:50], "unresolved_count": len(unresolved)}


def run_pos_report_sweep(client, org_id, src_row):
    """The SCHEDULED (cold) pull — `router._SOURCE_SCRAPERS` dispatches here. The restore itself is the
    one home's `run_cold_sweep`."""
    return prp.run_cold_sweep(client, org_id, src_row)
