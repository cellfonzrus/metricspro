"""Merchant-portal FEED ADAPTER — the card-settlement half of the portal pull (owner directive 2026-09-04).

WHAT THIS IS, AFTER §COMMCALC-05. The pull LOOP (window of days, reach the report, press export, read
the download, the honest failure wording, the cold-session restore) is `portal_report_pull.py`, used by
every feed. This module is the part that is actually about CARD SETTLEMENT: the three portals'
descriptors, the normalizers, store attribution through the mig-902 merchant map, and the two landing
tables of mig 955. It is the pull loop's first adapter, and it satisfies that contract with four
functions — `connector_keys`, `portal_descriptor`, `report_specs`, `ingest_report`.

Before that split these two halves were one file, and the second feed to need the same loop could only
have copied it. Nothing about this module's behaviour changed in the split: the moved functions are
IMPORTED from the one home under their old names, so every caller and
`harness_merchant_portals.py` reads exactly the same code it always did.

WHAT IT REUSES — no second login engine, no second browser stack, no second scheduler (CLAUDE.md
duplicate-check build gate):
  • portal_report_pull.py       THE pull loop (index §COMMCALC-05). This module supplies the feed.
  • live_login.py               ONE live browser from login through 2FA code entry, streamed to the
                                operator, then the authenticated storage_state persisted.
  • vidapay_sweep primitives    _launch / _new_context / _proxy_arg / capture_session_state / _classify /
                                _wait_settle / _page_text / egress_hint / VidaPayAuthError.
  • commcalc.data_source        the login registry — credentials, proxy, enabled, frequency/hour,
                                next_run_at, session_state, auth_status. A merchant portal IS a
                                data_source row; mig 955 only added the fields a card processor needs.
  • /data-sources/sweep/run-due the EXISTING generic scheduler, dispatching on
                                router._SOURCE_SCRAPERS[processor] (mig 956 registers that cron).
  • storeops.merchant_ids       (mig 902) the canonical (org, processor, merchant_id) → store_code map.
                                Store attribution resolves through it. NO new mapping table.
  • portal_backoff              the portal cooldown the whole platform already respects.

NO PROD WRITES FROM A DEV BOX: ingestion goes through the injected supabase client, and every parser is
importable and provable without a browser or a database (harness_merchant_portals.py).
"""
from app.modules.commcalc import merchant_portals as mp
from app.modules.commcalc import portal_report_pull as prp

# The moved pull loop, DEREFERENCED under the names this module has always exposed. These are imports,
# not copies — `harness_portal_report_pull.py` fails the build if a second implementation appears.
from app.modules.commcalc.portal_report_pull import (            # noqa: F401
    DEFAULT_WINDOW_DAYS, MAX_WINDOW_DAYS, date_range, make_pull_fn, next_expiry,
    pull_reports_on_page, session_ttl_hint, window_days,
)

MerchantPortalError = prp.PortalPullError          # one error class for one loop


def _vp():
    """Lazy import of the shared browser driver (keeps Playwright import cost off module load)."""
    from app.modules.commcalc import vidapay_sweep as vp
    return vp


# ── THE ADAPTER CONTRACT (portal_report_pull.REQUIRED) ───────────────────────────────────────────
def connector_keys():
    """The `data_source.processor` values this feed serves. Read from the portal registry — the keys
    are spelled once, in merchant_portals.PORTALS, and nowhere else."""
    return tuple(mp.PORTAL_KEYS)


def portal_descriptor(connector):
    """The portal's descriptor (base_url, date_window_days, …) or None."""
    return mp.portal(connector)


def report_specs(connector, enabled_keys=None, src_row=None):
    """The report descriptors to pull, optionally filtered to this source's enabled list. This feed's
    reports are the portal's own fixed set, so `src_row` is accepted (the contract passes it) and
    deliberately unused — a card portal's report list is not a tenant declaration."""
    return mp.report_specs(connector, enabled_keys)


def read_table(payload, filename=""):
    """Report payload → list-of-lists, with the banner above the real header skipped by THIS feed's
    own synonym table. The parsing itself is the one home's; which row is the header is the feed's
    answer, because "how many cells map to my columns" is the only reliable test for it."""
    return prp.read_table(payload, filename, header_score=lambda r: len(mp.map_headers(r)))


def run_merchant_portal_sweep(client, org_id, src_row):
    """The SCHEDULED (cold) pull for a card portal — `router._SOURCE_SCRAPERS` dispatches here. The
    restore itself is the one home's `run_cold_sweep`; this name is kept because the scraper registry
    and the index both already point at it."""
    return prp.run_cold_sweep(client, org_id, src_row)


def parse_report(portal_key, spec, payload, *, src_row=None, filename=""):
    """One downloaded report → normalized rows, via the adapter's PURE normalizer. Batch-grain reports
    go to normalize_batches, everything else to normalize_settlement. No DB, no browser."""
    src_row = src_row or {}
    table = read_table(payload, filename)
    cal = (src_row.get("portal_calibration") or {})
    per_report_cal = dict(cal)
    per_report_cal.update((cal.get(spec["key"]) or {}) if isinstance(cal.get(spec["key"]), dict) else {})
    kw = dict(source_id=src_row.get("id"), org_id=src_row.get("org_id"),
              role_override=src_row.get("settlement_role"), calibration=per_report_cal,
              report_key=spec["key"], merchant_id_default=src_row.get("account_id"))
    if spec.get("grain") == "batch":
        return mp.normalize_batches(portal_key, table, **kw)
    return mp.normalize_settlement(portal_key, table, **kw)


# ── store attribution — through the canonical map, never guessed ─────────────────────────────────
def resolve_stores(org_id, portal_key, rows):
    """Stamp each row's store_code from storeops.store_merchant_id (mig 902), the SAME canonical
    (org, processor, merchant_id) → store_code map the ePay ingest uses. Returns the unresolved merchant
    ids so the operator can map them in the EXISTING store-setup panel — no new mapping UI, no guessing.

    A row whose merchant id is unmapped keeps store_code=None. It is still STORED (the money is real and
    the evidence must not be dropped) but it is excluded from per-store totals and reported, because
    silently attributing it to no store would make a store's card total read low in the recon."""
    from app.modules.storeops import merchant_ids as _mids
    try:
        tmap = {str(k).strip().upper(): v for k, v in (_mids.resolve_map(org_id, portal_key) or {}).items()}
    except Exception:
        tmap = {}
    unresolved = {}
    for r in rows or []:
        mid = str(r.get("merchant_id") or "").strip()
        tid = str(r.get("terminal_id") or "").strip()
        sc = tmap.get(mid.upper()) or (tmap.get(tid.upper()) if tid else None)
        r["store_code"] = sc
        if not sc:
            key = mid or tid
            if key and key not in unresolved:
                unresolved[key] = {"merchant_id": mid or None, "terminal_id": tid or None,
                                   "store_label": r.get("store_label"), "portal_key": portal_key}
    return sorted(unresolved.values(), key=lambda x: str(x.get("merchant_id") or ""))


# ── persistence ──────────────────────────────────────────────────────────────────────────────────
_SETTLEMENT_COLS = ("org_id", "source_id", "portal_key", "report_key", "settlement_role",
                    "business_date", "merchant_id", "terminal_id", "store_label", "store_code",
                    "card_brand", "gross_amount", "refund_amount", "net_amount", "fee_amount",
                    "txn_count", "currency", "batch_ref", "source_line", "raw")
_BATCH_COLS = ("org_id", "source_id", "portal_key", "report_key", "settlement_role", "deposit_date",
               "batch_date", "merchant_id", "terminal_id", "store_label", "store_code", "batch_ref",
               "deposit_amount", "fee_amount", "txn_count", "currency", "source_line", "raw")


def _project(rows, cols, org_id, source_id):
    out = []
    for r in rows or []:
        d = {c: r.get(c) for c in cols}
        d["org_id"] = org_id
        d["source_id"] = source_id
        out.append(d)
    return out


def store_settlement(client, org_id, source_id, rows, chunk=400):
    """Upsert settlement rows on the mig-955 natural key. Deduped FIRST: several export lines can share
    (merchant, day, brand) — one per terminal or per batch — and the table's grain is the day, so they
    must be SUMMED before the upsert. Upserting them one by one would let the last line overwrite the
    rest and a store's day would read as one terminal's total."""
    rows = mp.dedupe_settlement(rows or [])
    payload = _project(rows, _SETTLEMENT_COLS, org_id, source_id)
    saved = 0
    for i in range(0, len(payload), chunk):
        client.schema("commcalc").table("merchant_settlement_day").upsert(
            payload[i:i + chunk],
            on_conflict="org_id,source_id,merchant_id,business_date,card_brand").execute()
        saved += len(payload[i:i + chunk])
    return saved


def store_batches(client, org_id, source_id, rows, chunk=400):
    """Upsert funding/deposit batches on their own natural key (a different grain from settlement)."""
    payload = _project(rows or [], _BATCH_COLS, org_id, source_id)
    saved = 0
    for i in range(0, len(payload), chunk):
        client.schema("commcalc").table("merchant_settlement_batch").upsert(
            payload[i:i + chunk],
            on_conflict="org_id,source_id,merchant_id,deposit_date,batch_ref").execute()
        saved += len(payload[i:i + chunk])
    return saved


def ingest_report(client, org_id, src_row, portal_key, spec, payload, filename=""):
    """Parse → resolve stores → persist ONE report. Returns an honest per-report result: `delivered` is
    True only when rows actually landed, mirroring router._pull_delivered so the live session, the
    data_source stamp and the UI can never disagree about what success means."""
    parsed = parse_report(portal_key, spec, payload, src_row=src_row, filename=filename)
    rows = parsed["rows"]
    unresolved = resolve_stores(org_id, portal_key, rows)
    saved = 0
    if rows:
        if spec.get("grain") == "batch":
            saved = store_batches(client, org_id, src_row.get("id"), rows)
        else:
            saved = store_settlement(client, org_id, src_row.get("id"), rows)
    return {"report": spec["key"], "label": spec.get("label"), "rows_parsed": len(rows),
            "rows_ingested": saved, "delivered": saved > 0,
            "skipped": parsed["skipped"][:20], "warnings": parsed["warnings"],
            "unresolved_merchants": unresolved[:50],
            "unresolved_count": len(unresolved)}


# ── browser navigation (calibration-first, hints-second, honest failure third) ───────────────────
