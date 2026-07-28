from fastapi import APIRouter, UploadFile, File, HTTPException
from datetime import datetime, timezone
from app.core.database import get_supabase

router = APIRouter()
ORG_ID = "00000000-0000-0000-0000-000000000001"

def sb():
    return get_supabase()


# ── asset-2: transactional staging-swap for the ledger upload (mig 300) ───────────────────────
# Today's wipe-and-replace is a plain DELETE-then-batched-INSERT straight into the LIVE
# commcalc.asset_ledger with no try/except around the insert loop — if (say) batch 3 of 9 fails,
# the org's old rows are already gone and the remaining batches never run, leaving a PARTIAL
# ledger. Fix: stage every row into an org-scoped scratch table first (same batched-insert code,
# so a failure there never touches the live table), then swap it into asset_ledger with ONE
# Postgres function call (mig 300's commcalc.asset_ledger_swap_from_staging) — PostgREST wraps a
# single RPC call in one transaction, so the function's internal delete+insert either both land
# or neither does. Degrades to today's exact direct-write behavior (loudly logged) if mig 300
# hasn't been run yet — see _staging_available().
_ASSET_STAGING_TABLE = "asset_ledger_staging"
_ASSET_SWAP_RPC = "asset_ledger_swap_from_staging"
# PostgREST's "this relation/function doesn't exist" error signatures (schema-cache miss because
# the migration hasn't been run) — used to tell "mig 300 not applied yet" apart from a real data
# error surfaced by the swap function itself (which must NOT be swallowed).
_MISSING_SCHEMA_MARKERS = ("PGRST202", "PGRST205", "PGRST203", "schema cache", "does not exist")


def _is_missing_schema_error(exc: Exception) -> bool:
    s = str(exc)
    return any(m in s for m in _MISSING_SCHEMA_MARKERS)


def _staging_available(client) -> bool:
    """Cheap probe: does commcalc.asset_ledger_staging exist (i.e. was mig 300 run)? Returns
    False on ANY error — a transient probe failure just means this one upload takes the already-
    proven legacy path; the next upload probes again, so we never hard-fail an upload over a
    flaky check."""
    try:
        client.schema("commcalc").table(_ASSET_STAGING_TABLE).select("org_id").limit(1).execute()
        return True
    except Exception:
        return False


def _log_degraded_upload_mode(client, org_id: str, reason: str):
    """Loud, best-effort warning that this upload used the non-atomic legacy path (mig 300 missing
    or its RPC unreachable). Never raises — logging must not be able to fail an otherwise-good
    upload."""
    msg = (
        "Asset ledger upload used the LEGACY non-atomic delete+insert path "
        f"({reason}) — run migration 300_asset_ledger_staging_swap.sql in the Supabase SQL "
        "Editor to enable the atomic staging-swap (until then, a failed batch mid-upload can "
        "leave a PARTIAL ledger)."
    )
    print(f"[asset upload] WARNING org={org_id}: {msg}")
    try:
        client.schema("core").table("failure_log").insert({
            "org_id": org_id,
            "category": "asset_upload_degraded_mode",
            "severity": "warning",
            "source": "asset/upload",
            "message": msg,
            "remediation": "Run migration 300_asset_ledger_staging_swap.sql (Supabase SQL Editor).",
        }).execute()
    except Exception as _e:
        # core.failure_log itself may not exist (mig 112 not run) — the print above is the floor.
        print(f"[asset upload] failure_log write also failed: {_e}")


# settings-audit (2026-07-26): a general-purpose sibling to _log_degraded_upload_mode for the OTHER
# post-ingest pipeline steps (market backfill / selling-price backfill / flag syncs) — today those
# only `print()` on failure, so an admin has zero way to learn one degraded short of reading Railway
# logs. Writing a core.failure_log row makes the SAME failure visible to
# attention.py's `_p_asset_pipeline_issues` provider (login-popup surfaced) and to /failures.
# Never raises. Respects storeops.tenants.failure_log_disabled_categories (mig 112) — the same
# per-tenant opt-out core.router.py's own POST /failures honors — so a tenant that has explicitly
# muted a category via that admin UI doesn't get it re-surfaced here through a side door.
def _log_asset_pipeline_issue(client, org_id: str, category: str, message: str):
    try:
        t = (client.schema("storeops").table("tenants")
             .select("failure_log_disabled_categories").eq("org_id", org_id)
             .limit(1).execute().data) or []
        disabled = [str(d).strip().lower() for d in ((t[0].get("failure_log_disabled_categories")
                    if t else None) or [])]
        if category.strip().lower() in disabled:
            print(f"[asset upload] {category} logging suppressed by tenant preference org={org_id}")
            return
    except Exception:
        pass   # tenants table / column missing — fail open (still log), never fail open on writing
    print(f"[asset upload] WARNING org={org_id} [{category}]: {message}")
    try:
        client.schema("core").table("failure_log").insert({
            "org_id": org_id,
            "category": category,
            "severity": "warning",
            "source": "asset/upload",
            "message": message,
            "remediation": "Open the Asset Ledger page and re-run the upload; if this keeps "
                           "happening, share this message with an engineer.",
        }).execute()
    except Exception as _e:
        print(f"[asset upload] failure_log write also failed: {_e}")


def _stage_and_swap_ledger(client, org_id: str, rows: list[dict]) -> str:
    """Ingest `rows` into the org-scoped staging table (same 500-row batches as before), then
    atomically swap them into commcalc.asset_ledger via the mig-300 RPC. If any staging batch
    raises, the LIVE ledger is never touched — this only cleans up the partial stage and
    re-raises so the caller (upload endpoint) reports the failure loudly. Falls back to the
    legacy direct-write path (with a loud log) if the RPC specifically doesn't exist yet."""
    # Clear any stale rows left by a previous failed attempt for this org before restaging.
    client.schema("commcalc").table(_ASSET_STAGING_TABLE).delete().eq("org_id", org_id).execute()
    try:
        for i in range(0, len(rows), 500):
            client.schema("commcalc").table(_ASSET_STAGING_TABLE) \
                .insert(rows[i:i + 500]).execute()
    except Exception:
        # Partial stage — the LIVE ledger was NEVER touched. Clean the scratch rows so a retry
        # starts clean, then re-raise: this is a real data/parse problem, not a missing-migration
        # one, so it must surface to the caller, not be silently downgraded.
        try:
            client.schema("commcalc").table(_ASSET_STAGING_TABLE) \
                .delete().eq("org_id", org_id).execute()
        except Exception:
            pass
        raise

    try:
        resp = client.schema("commcalc").rpc(_ASSET_SWAP_RPC, {
            "p_org_id": org_id,
            "p_expected_rows": len(rows),
        }).execute()
    except Exception as e:
        if _is_missing_schema_error(e):
            # mig 300's table exists (we just staged into it) but the function doesn't — an
            # inconsistent/partial migration apply. Fall back to legacy so the upload still
            # completes; staged rows are orphaned scratch data, harmless, cleared on next attempt.
            _log_degraded_upload_mode(client, org_id, f"asset_ledger_swap_from_staging RPC missing: {e}")
            client.schema("commcalc").table("asset_ledger").delete().eq("org_id", org_id).execute()
            for i in range(0, len(rows), 500):
                client.schema("commcalc").table("asset_ledger").insert(rows[i:i + 500]).execute()
            try:
                client.schema("commcalc").table(_ASSET_STAGING_TABLE) \
                    .delete().eq("org_id", org_id).execute()
            except Exception:
                pass
            return "legacy_direct"
        # A real failure INSIDE the swap function (e.g. its own row-count guard) rolls back its
        # own transaction automatically — the live ledger is untouched. Surface it; do not
        # silently fall back to a raw write over real data trouble.
        raise

    data = resp.data
    swapped = None
    if isinstance(data, list) and data:
        swapped = data[0].get("rows_swapped")
    elif isinstance(data, dict):
        swapped = data.get("rows_swapped")
    if swapped is not None and int(swapped) != len(rows):
        raise RuntimeError(
            f"asset_ledger swap row-count mismatch: staged {len(rows)}, swapped {swapped}"
        )
    return "staged_swap"


def process_asset_ledger_bytes(file_bytes: bytes, org_id: str) -> dict:
    """Parse Asset_Lending.xlsx bytes → refresh commcalc.asset_ledger via an org-scoped
    STAGING-TABLE + ATOMIC-SWAP (mig 300; see _stage_and_swap_ledger) → backfill market +
    selling price + sync appeal/RMA/undercharge flags. Shared by the manual upload AND the VIP
    auto-sweep — signature is a load-bearing cross-module import (commcalc/vip_sweep.py) and must
    not change. Raises ValueError if the file parses to zero rows (so an empty/bad download never
    wipes the ledger). Falls back to the pre-mig-300 direct delete+insert (loudly logged) if the
    staging infra hasn't been migrated in yet — see _staging_available()."""
    from app.modules.asset.asset_parser import parse_asset_ledger
    rows = parse_asset_ledger(file_bytes, org_id)
    if not rows:
        raise ValueError("No rows parsed from Asset_Lending file")

    client = sb()
    if _staging_available(client):
        swap_mode = _stage_and_swap_ledger(client, org_id, rows)
    else:
        _log_degraded_upload_mode(client, org_id, "commcalc.asset_ledger_staging table missing")
        client.schema("commcalc").table("asset_ledger").delete().eq("org_id", org_id).execute()
        for i in range(0, len(rows), 500):
            client.schema("commcalc").table("asset_ledger").insert(rows[i:i + 500]).execute()
        swap_mode = "legacy_direct"

    # settings-audit (2026-07-26): _backfill_market used to run UNGUARDED — an exception here (e.g.
    # a transient read error on commcalc.store_mapping) would propagate straight out of this
    # function and skip selling-price backfill + every flag sync below it, even though the ledger
    # swap above had already succeeded. Now resilient AND surfaced, matching its four siblings.
    try:
        _backfill_market(client, org_id)
    except Exception as _e:
        _log_asset_pipeline_issue(
            client, org_id, "asset_market_backfill_failed",
            f"Market backfill did not finish on the last upload ({_e}). Some rows may have no "
            f"market (or a stale one), which drops them out of market-filtered asset reports "
            f"(Charges Dashboard, RMA, Aging, Owed-Weekly).")
    try:
        _backfill_selling_price(client, org_id)
    except Exception as _e:
        _log_asset_pipeline_issue(
            client, org_id, "asset_selling_price_backfill_failed",
            f"Selling-price backfill failed on the last upload (run migration "
            f"009_asset_selling_price.sql?): {_e}")
    try:
        _sync_appeal_flags(client, org_id)
    except Exception as _e:
        _log_asset_pipeline_issue(
            client, org_id, "asset_appeal_flag_sync_failed",
            f"Appeal flag sync failed on the last upload — Appeals & Denied Payments flags were "
            f"not refreshed: {_e}")
    try:
        _sync_rma_flags(client, org_id)
    except Exception as _e:
        _log_asset_pipeline_issue(
            client, org_id, "asset_rma_flag_sync_failed",
            f"RMA flag sync failed on the last upload — RMA flags were not refreshed: {_e}")
    try:
        _sync_undercharge_flags(client, org_id)
    except Exception as _e:
        _log_asset_pipeline_issue(
            client, org_id, "asset_undercharge_flag_sync_failed",
            f"Undercharge flag sync failed on the last upload: {_e}")
    return {"rows_imported": len(rows), "swap_mode": swap_mode}


@router.post("/upload")
async def upload_asset_ledger(file: UploadFile = File(...), org_id: str = ORG_ID):
    """Upload Asset_Lending.xlsx — stages then atomically swaps into the org's ledger (mig 300;
    falls back to legacy clear-then-reinsert if that migration hasn't run yet)."""
    file_bytes = await file.read()
    try:
        res = process_asset_ledger_bytes(file_bytes, org_id)
    except Exception as e:
        import traceback
        raise HTTPException(status_code=400, detail=f"Parse error: {e}\n{traceback.format_exc()}")
    return {"status": "ok", "rows_imported": res["rows_imported"], "swap_mode": res.get("swap_mode")}


# Stores whose asset address differs from store_mapping, plus the two not in it.
MARKET_OVERRIDES = {
    "1 S 60th St": "PA",
    "116-36 Springfield Blvd": "LI",
    "1598 Mt Ephraim Ave": "PA",
    "1710 W 4Th Street": "PA",
    "2778 Mount Ephraim Ave": "PA",
    "2778 Mt Ephraim Ave": "PA",
    "4712 White Plains Road": "NYC",
    "5135 Bergenline Ave": "NJ",
    "5619 N Broad St": "PA",
    "5619 N Broad Street": "PA",
    "586 Main Ave": "NJ",
    "6507 Castor Ave": "PA",
    "652 Communipaw Ave": "NJ",
}


def _backfill_market(client, org_id: str):
    """Populate asset_ledger.market: exact match to store_mapping, then overrides."""
    # Build address(lower) -> market map from store_mapping
    sm = client.schema("commcalc").table("store_mapping") \
        .select("store_address,market").eq("org_id", org_id).execute().data or []
    addr_to_market = {}
    for m in sm:
        a = (m.get("store_address") or "").strip().lower()
        if a and m.get("market"):
            addr_to_market[a] = m["market"]

    # Distinct asset stores
    stores = set()
    page = 0; PAGE = 1000
    while True:
        start = page * PAGE
        chunk = client.schema("commcalc").table("asset_ledger") \
            .select("store").eq("org_id", org_id) \
            .range(start, start + PAGE - 1).execute().data or []
        for r in chunk:
            if r.get("store"):
                stores.add(r["store"])
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 100:
            break

    # Resolve each store's market (exact match first, then overrides) and update
    for store in stores:
        market = addr_to_market.get(store.strip().lower()) or MARKET_OVERRIDES.get(store)
        if market:
            client.schema("commcalc").table("asset_ledger") \
                .update({"market": market}).eq("org_id", org_id).eq("store", store).execute()


def _store_canon_map(client, org_id: str) -> dict:
    """Lowercased store_mapping.store_address -> canonical address. Used to resolve a b2bsoft
    export's raw store text onto the same canonical store key the rest of the app (including the
    Account module's Balance Sheet, via coa.py's store_resolver) uses, so a $ value lands under
    one consistent store bucket instead of a second, mis-spelled one. Never drops a store —
    callers fall back to the raw trimmed string when there's no match (an unmapped store is still
    worth keeping, just uncanonicalized)."""
    sm = client.schema("commcalc").table("store_mapping") \
        .select("store_address").eq("org_id", org_id).execute().data or []
    return {(m.get("store_address") or "").strip().lower(): (m.get("store_address") or "").strip()
            for m in sm if m.get("store_address")}


def _canon_store(raw: str, addr_map: dict) -> str:
    s = (raw or "").strip()
    return addr_map.get(s.lower(), s) if s else s


@router.get("/summary")
async def get_asset_summary(org_id: str = ORG_ID, store: str = "", market: str = "",
                            date_from: str = "", date_to: str = ""):
    """High-level totals + breakdowns for the summary dashboard.
    Optional dashboard filters: store, market, acquired_date range (date_from/date_to)."""
    client = sb()
    q = client.schema("commcalc").table("asset_ledger") \
        .select("status,category,owed_to_vip,on_inventory,reimbursement,commissions,total_owed,total_reimbursed,store,market,acquired_date") \
        .eq("org_id", org_id)
    if store:
        q = q.eq("store", store)
    if market:
        q = q.eq("market", market)
    if date_from:
        q = q.gte("acquired_date", date_from)
    if date_to:
        q = q.lte("acquired_date", date_to)
    resp = q.execute()

    rows = resp.data or []
    if not rows:
        return {"loaded": False}

    total_rows = len(rows)
    total_fees = sum(float(r.get("commissions") or 0) for r in rows)
    # open balance = owed_to_vip for Open status only
    total_open = sum(float(r.get("owed_to_vip") or 0) for r in rows if (r.get("status") or "") == "Open")
    # reimbursed = sum of reimbursement col (actual Boost payments received)
    total_reimbursed = sum(float(r.get("reimbursement") or 0) for r in rows)
    # all-time owed = all owed_to_vip
    total_owed = sum(float(r.get("owed_to_vip") or 0) for r in rows)
    # on inventory = owed_to_vip for On Inventory category
    on_inventory = sum(float(r.get("owed_to_vip") or 0) for r in rows if "On Inventory" in (r.get("category") or ""))

    # By status
    by_status: dict = {}
    for r in rows:
        s = r.get("status") or "Unknown"
        if s not in by_status:
            by_status[s] = {"count": 0, "owed": 0, "reimbursed": 0, "fees": 0}
        by_status[s]["count"] += 1
        by_status[s]["owed"] += float(r.get("owed_to_vip") or 0)
        by_status[s]["reimbursed"] += float(r.get("reimbursement") or 0)
        by_status[s]["fees"] += float(r.get("commissions") or 0)

    # By category
    by_category: dict = {}
    for r in rows:
        c = r.get("category") or "Unknown"
        if c not in by_category:
            by_category[c] = {"count": 0, "owed": 0, "fees": 0}
        by_category[c]["count"] += 1
        by_category[c]["owed"] += float(r.get("owed_to_vip") or 0)
        by_category[c]["fees"] += float(r.get("commissions") or 0)

    return {
        "loaded": True,
        "total_rows": total_rows,
        "total_fees": round(total_fees, 2),
        "total_open_balance": round(total_open, 2),
        "total_reimbursed": round(total_reimbursed, 2),
        "total_owed_alltime": round(total_owed, 2),
        "on_inventory": round(on_inventory, 2),
        "by_status": by_status,
        "by_category": by_category,
    }


@router.get("/category-detail")
async def get_category_detail(
    category: str,
    org_id: str = ORG_ID,
    limit: int = 500,
    offset: int = 0,
    store: str = "",
    market: str = "",
    date_from: str = "",
    date_to: str = "",
):
    """Drill-down for one category: status breakdown (all rows) + paginated device rows.
    Honors the dashboard filters: store, market, acquired_date range."""
    client = sb()

    def _af(q):
        if store:
            q = q.eq("store", store)
        if market:
            q = q.eq("market", market)
        if date_from:
            q = q.gte("acquired_date", date_from)
        if date_to:
            q = q.lte("acquired_date", date_to)
        return q

    # Pull every row in this category for an accurate status tally.
    # Select only the light columns needed for the breakdown.
    tally_rows = []
    page = 0
    PAGE = 1000
    while True:
        start = page * PAGE
        resp = _af(client.schema("commcalc").table("asset_ledger") \
            .select("status,owed_to_vip,reimbursement,commissions") \
            .eq("org_id", org_id).eq("category", category)) \
            .range(start, start + PAGE - 1).execute()
        chunk = resp.data or []
        tally_rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 100:  # hard safety stop (100k rows)
            break

    by_status: dict = {}
    for r in tally_rows:
        s = r.get("status") or "Unknown"
        if s not in by_status:
            by_status[s] = {"count": 0, "owed": 0, "reimbursed": 0, "fees": 0}
        by_status[s]["count"] += 1
        by_status[s]["owed"] += float(r.get("owed_to_vip") or 0)
        by_status[s]["reimbursed"] += float(r.get("reimbursement") or 0)
        by_status[s]["fees"] += float(r.get("commissions") or 0)
    for s in by_status:
        by_status[s]["owed"] = round(by_status[s]["owed"], 2)
        by_status[s]["reimbursed"] = round(by_status[s]["reimbursed"], 2)
        by_status[s]["fees"] = round(by_status[s]["fees"], 2)

    # Paginated device rows for the table.
    rows_resp = _af(client.schema("commcalc").table("asset_ledger") \
        .select("id,store,esn_imei,phone_number,device_model,contract_type,status,date_sold,sfid,owed_to_vip,reimbursement,commissions,selling_price,notes") \
        .eq("org_id", org_id).eq("category", category)) \
        .order("date_sold", desc=True).range(offset, offset + limit - 1).execute()

    rows = _attach_vip_invoices(client, org_id, rows_resp.data or [])

    return {
        "category": category,
        "total_in_category": len(tally_rows),
        "by_status": by_status,
        "rows": rows,
        "offset": offset,
        "limit": limit,
    }


@router.get("/filter-options")
async def get_filter_options(org_id: str = ORG_ID):
    """Distinct stores + markets for the report dropdowns."""
    client = sb()
    rows = []
    page = 0; PAGE = 1000
    while True:
        start = page * PAGE
        chunk = client.schema("commcalc").table("asset_ledger") \
            .select("store,market").eq("org_id", org_id) \
            .range(start, start + PAGE - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 100:
            break
    markets = set()
    store_to_market = {}
    for r in rows:
        if r.get("market"):
            markets.add(r["market"])
        if r.get("store"):
            store_to_market[r["store"]] = r.get("market")
    stores = [{"store": k, "market": v} for k, v in store_to_market.items()]
    stores.sort(key=lambda x: x["store"])
    return {"markets": sorted(markets), "stores": stores}


# ── Inter-store borrowed-money tracking (#6 / roadmap 6a) ─────────────────────
# A store can fund asset purchases with money borrowed from another store. Each
# borrowing is a debt (borrower owes lender); paybacks reduce the outstanding.
def _borrow_store_market(client, org_id):
    """store(lower) -> market, from asset_ledger (so a new borrowing inherits the
    borrower store's market for the reconciliation filters)."""
    m = {}
    page = 0; PAGE = 1000
    while True:
        chunk = client.schema("commcalc").table("asset_ledger").select("store,market") \
            .eq("org_id", org_id).range(page * PAGE, page * PAGE + PAGE - 1).execute().data or []
        for r in chunk:
            if r.get("store") and r.get("market"):
                m.setdefault((r["store"] or "").strip().lower(), r["market"])
        if len(chunk) < PAGE or page > 100:
            break
        page += 1
    return m


def _borrowings_with_outstanding(client, org_id):
    """All borrowings joined with their payments → repaid + outstanding per loan."""
    loans = client.schema("commcalc").table("store_borrowings").select("*") \
        .eq("org_id", org_id).order("borrowed_date", desc=True).execute().data or []
    pays = client.schema("commcalc").table("store_borrowing_payments").select("*") \
        .eq("org_id", org_id).execute().data or []
    paid_by_loan = {}
    pays_by_loan = {}
    for p in pays:
        lid = p.get("borrowing_id")
        paid_by_loan[lid] = paid_by_loan.get(lid, 0.0) + float(p.get("amount") or 0)
        pays_by_loan.setdefault(lid, []).append(p)
    out = []
    for l in loans:
        amt = float(l.get("amount") or 0)
        repaid = round(paid_by_loan.get(l["id"], 0.0), 2)
        outstanding = round(amt - repaid, 2)
        out.append({**l, "amount": round(amt, 2), "repaid": repaid,
                    "outstanding": outstanding, "settled": outstanding <= 0.005,
                    "payments": sorted(pays_by_loan.get(l["id"], []),
                                       key=lambda p: p.get("paid_date") or "")})
    return out


@router.get("/borrowings")
async def list_borrowings(org_id: str = ORG_ID, store: str = "", market: str = "", status: str = ""):
    """Borrowing ledger. Filters: store (matches borrower OR lender), market (borrower),
    status open|settled. Each row carries repaid + outstanding + its payments."""
    rows = _borrowings_with_outstanding(sb(), org_id)
    if store:
        rows = [r for r in rows if store in (r.get("borrower_store"), r.get("lender_store"))]
    if market:
        rows = [r for r in rows if (r.get("market") or "") == market]
    if status == "open":
        rows = [r for r in rows if not r["settled"]]
    elif status == "settled":
        rows = [r for r in rows if r["settled"]]
    tot_borrowed = round(sum(r["amount"] for r in rows), 2)
    tot_repaid = round(sum(r["repaid"] for r in rows), 2)
    return {"borrowings": rows, "count": len(rows),
            "total_borrowed": tot_borrowed, "total_repaid": tot_repaid,
            "total_outstanding": round(tot_borrowed - tot_repaid, 2)}


@router.post("/borrowings")
async def create_borrowing(body: dict, org_id: str = ORG_ID):
    """Log a borrowing. Body: {borrower_store, lender_store, amount, borrowed_date?, note?,
    market?}. Market defaults to the borrower store's market."""
    borrower = (body.get("borrower_store") or "").strip()
    lender = (body.get("lender_store") or "").strip()
    if not borrower or not lender:
        raise HTTPException(400, "borrower_store and lender_store required")
    if borrower == lender:
        raise HTTPException(400, "borrower and lender must be different stores")
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(400, "amount must be a number")
    if amount <= 0:
        raise HTTPException(400, "amount must be greater than 0")
    market = (body.get("market") or "").strip() or \
        _borrow_store_market(sb(), org_id).get(borrower.lower())
    row = {
        "org_id": org_id, "borrower_store": borrower, "lender_store": lender,
        "market": market, "amount": amount,
        "borrowed_date": body.get("borrowed_date") or datetime.now(timezone.utc).date().isoformat(),
        "note": (body.get("note") or "").strip() or None,
    }
    r = sb().schema("commcalc").table("store_borrowings").insert(row).execute()
    return (r.data or [row])[0]


@router.patch("/borrowings/{borrowing_id}")
async def update_borrowing(borrowing_id: str, body: dict, org_id: str = ORG_ID):
    """Edit a borrowing (borrower/lender/amount/date/note/market)."""
    fields = ("borrower_store", "lender_store", "market", "amount", "borrowed_date", "note")
    row = {k: body[k] for k in fields if k in body}
    if "amount" in row:
        try:
            row["amount"] = float(row["amount"])
        except (TypeError, ValueError):
            raise HTTPException(400, "amount must be a number")
    if not row:
        raise HTTPException(400, "no valid fields to update")
    r = sb().schema("commcalc").table("store_borrowings").update(row) \
        .eq("id", borrowing_id).eq("org_id", org_id).execute()
    if not r.data:
        raise HTTPException(404, "borrowing not found")
    return r.data[0]


@router.delete("/borrowings/{borrowing_id}")
async def delete_borrowing(borrowing_id: str, org_id: str = ORG_ID):
    """Delete a borrowing (its payments cascade)."""
    sb().schema("commcalc").table("store_borrowings").delete() \
        .eq("id", borrowing_id).eq("org_id", org_id).execute()
    return {"deleted": borrowing_id}


@router.post("/borrowings/{borrowing_id}/payment")
async def add_borrowing_payment(borrowing_id: str, body: dict, org_id: str = ORG_ID):
    """Record a payback against a borrowing. Body: {amount, paid_date?, note?}."""
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(400, "amount must be a number")
    if amount <= 0:
        raise HTTPException(400, "amount must be greater than 0")
    loan = sb().schema("commcalc").table("store_borrowings").select("id") \
        .eq("id", borrowing_id).eq("org_id", org_id).limit(1).execute().data or []
    if not loan:
        raise HTTPException(404, "borrowing not found")
    row = {
        "org_id": org_id, "borrowing_id": borrowing_id, "amount": amount,
        "paid_date": body.get("paid_date") or datetime.now(timezone.utc).date().isoformat(),
        "note": (body.get("note") or "").strip() or None,
    }
    r = sb().schema("commcalc").table("store_borrowing_payments").insert(row).execute()
    return (r.data or [row])[0]


@router.delete("/borrowing-payment/{payment_id}")
async def delete_borrowing_payment(payment_id: str, org_id: str = ORG_ID):
    """Undo a payback."""
    sb().schema("commcalc").table("store_borrowing_payments").delete() \
        .eq("id", payment_id).eq("org_id", org_id).execute()
    return {"deleted": payment_id}


@router.get("/borrowings/summary")
async def borrowings_summary(org_id: str = ORG_ID, store: str = "", market: str = ""):
    """Reconciliation: who owes whom, and net position per store. Filters store/market."""
    rows = _borrowings_with_outstanding(sb(), org_id)
    if market:
        rows = [r for r in rows if (r.get("market") or "") == market]
    if store:
        rows = [r for r in rows if store in (r.get("borrower_store"), r.get("lender_store"))]
    # who owes whom (borrower -> lender) with outstanding > 0
    pair = {}
    for r in rows:
        k = (r["borrower_store"], r["lender_store"])
        d = pair.setdefault(k, {"borrower_store": r["borrower_store"], "lender_store": r["lender_store"],
                                "market": r.get("market"), "borrowed": 0.0, "repaid": 0.0,
                                "outstanding": 0.0, "loans": 0})
        d["borrowed"] += r["amount"]; d["repaid"] += r["repaid"]
        d["outstanding"] += r["outstanding"]; d["loans"] += 1
    pairs = [{**v, "borrowed": round(v["borrowed"], 2), "repaid": round(v["repaid"], 2),
              "outstanding": round(v["outstanding"], 2)} for v in pair.values()]
    pairs.sort(key=lambda x: -x["outstanding"])
    # per-store net: owes (as borrower) vs owed (as lender)
    by_store = {}
    for r in rows:
        b = by_store.setdefault(r["borrower_store"], {"store": r["borrower_store"], "owes": 0.0, "owed": 0.0})
        b["owes"] += r["outstanding"]
        l = by_store.setdefault(r["lender_store"], {"store": r["lender_store"], "owes": 0.0, "owed": 0.0})
        l["owed"] += r["outstanding"]
    net = [{"store": v["store"], "owes": round(v["owes"], 2), "owed": round(v["owed"], 2),
            "net": round(v["owed"] - v["owes"], 2)} for v in by_store.values()]
    net.sort(key=lambda x: -abs(x["net"]))
    return {"pairs": pairs, "by_store": net,
            "total_outstanding": round(sum(p["outstanding"] for p in pairs), 2)}


# ── On-inventory ↔ b2bsoft inventory reconciliation (#7) ──────────────────────
INV_BUCKETS = ["iphone", "android", "tablet", "watch", "hotspot"]


def _inv_bucket(s):
    """Map a device model OR a b2bsoft category label to one of the 5 reconciled
    buckets (or None to exclude: SIM kits, accessories, anything else)."""
    t = (s or "").lower()
    if not t:
        return None
    if "watch" in t:
        return "watch"
    if "ipad" in t or "tablet" in t or " tab" in t or t.endswith("tab") or "tab " in t:
        return "tablet"
    if any(w in t for w in ("hotspot", "mifi", "jetpack", "modem", "internet")):
        return "hotspot"
    if "iphone" in t:
        return "iphone"
    if any(w in t for w in ("samsung", "galaxy", "motorola", "moto ", "google", "pixel",
                            "android", "celero", "oneplus", "tcl", "nokia", "blu ")):
        return "android"
    if "apple" in t:   # bare Apple inventory that isn't iPad/Watch -> iPhone
        return "iphone"
    return None


def _asset_oninv_by_bucket(client, org_id, store="", market=""):
    """Per-store counts of On-Inventory (unsold) devices in each of the 5 buckets,
    classified from device_model."""
    rows = []
    page = 0; PAGE = 1000
    while True:
        q = client.schema("commcalc").table("asset_ledger") \
            .select("store,market,device_model") \
            .eq("org_id", org_id).is_("date_sold", "null").ilike("category", "%On Inventory%")
        if store:
            q = q.eq("store", store)
        if market:
            q = q.eq("market", market)
        chunk = q.range(page * PAGE, page * PAGE + PAGE - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < PAGE or page > 50:
            break
        page += 1
    by_store = {}
    for r in rows:
        b = _inv_bucket(r.get("device_model"))
        if not b:
            continue
        s = r.get("store") or "(unknown)"
        d = by_store.setdefault(s, {"store": s, "market": r.get("market"),
                                    **{k: 0 for k in INV_BUCKETS}})
        if not d["market"] and r.get("market"):
            d["market"] = r.get("market")
        d[b] += 1
    return by_store


@router.get("/inventory-recon")
async def inventory_recon(org_id: str = ORG_ID, store: str = "", market: str = "", as_of: str = ""):
    """Reconcile asset On-Inventory (classified into iphone/android/tablet/watch/hotspot)
    against the latest b2bsoft inventory snapshot, per store + category. Differences
    (asset − b2b) surface per cell; b2b stores not seen in asset are surfaced separately."""
    client = sb()
    asset = _asset_oninv_by_bucket(client, org_id, store, market)

    # b2b side: pick the snapshot date (latest unless as_of given)
    q = client.schema("commcalc").table("b2b_inventory").select("*").eq("org_id", org_id)
    b2b_rows = q.execute().data or []
    dates = sorted({r.get("as_of_date") for r in b2b_rows if r.get("as_of_date")}, reverse=True)
    snap = as_of or (dates[0] if dates else "")
    b2b = {}
    for r in b2b_rows:
        if snap and r.get("as_of_date") != snap:
            continue
        s = r.get("store") or "(unknown)"
        cat = (r.get("category") or "").lower()
        if cat not in INV_BUCKETS:
            cat = _inv_bucket(cat)
        if not cat:
            continue
        d = b2b.setdefault(s, {k: 0 for k in INV_BUCKETS})
        d[cat] += int(r.get("qty") or 0)
    if store:
        b2b = {k: v for k, v in b2b.items() if k == store}

    out = []
    all_stores = set(asset) | set(b2b)
    for s in sorted(all_stores):
        a = asset.get(s, {"store": s, "market": None, **{k: 0 for k in INV_BUCKETS}})
        bb = b2b.get(s, {k: 0 for k in INV_BUCKETS})
        cats = {}
        total_abs = 0
        for k in INV_BUCKETS:
            av, bv = int(a.get(k, 0)), int(bb.get(k, 0))
            cats[k] = {"asset": av, "b2b": bv, "diff": av - bv}
            total_abs += abs(av - bv)
        out.append({"store": s, "market": a.get("market"), "categories": cats,
                    "total_abs_diff": total_abs, "in_asset": s in asset, "in_b2b": s in b2b})
    out.sort(key=lambda r: -r["total_abs_diff"])
    return {
        "as_of": snap, "available_dates": dates, "b2b_loaded": bool(b2b_rows),
        "buckets": INV_BUCKETS, "rows": out,
        "mismatch_stores": sum(1 for r in out if r["total_abs_diff"] > 0),
        "total_abs_diff": sum(r["total_abs_diff"] for r in out),
        "b2b_only_stores": sorted([r["store"] for r in out if r["in_b2b"] and not r["in_asset"]]),
    }


@router.post("/b2b-inventory/upload")
async def upload_b2b_inventory(body: dict, org_id: str = ORG_ID):
    """Manual b2bsoft inventory load (until the portal sweep is wired). Body:
    {as_of_date, rows:[{store, category, qty, value?}]}. Category is normalized to a bucket;
    unmappable categories are skipped + reported (for the qty/category recon below). Replaces
    that date's b2b_inventory snapshot.

    LINK — Balance Sheet inventory value (asset-8, 2026-07-15, OWNER REPORT "Inventory values
    are not showing because inventory aging is not importing from b2b"). Root cause traced
    end-to-end: the asset module's own /aging (Inventory Aging) report is entirely VIP-ledger
    sourced (asset_ledger.acquired_date) and was never b2b-dependent — it already works. The real
    gap is the Balance Sheet inventory line (account/coa.py), which reads
    commcalc.inventory_value (swept_value/manual_value) with an asset_ledger fallback: a
    non-Boost tenant (e.g. a Total-only dealer with few/no VIP-financed on-hand devices) has a
    thin asset_ledger fallback AND an empty inventory_value (the b2bsoft portal-sweep that would
    populate it — commcalc/b2b_sweep.py, mod-commission-owned — is either not configured or,
    once wired, parses 0 stores against that tenant's real export shape), so the BS inventory
    line is genuinely near-empty, not just stale. Fix: when an uploaded row ALSO carries a $
    value (most b2bsoft/POS "Inventory Aging" exports have a cost/retail column alongside qty),
    aggregate it per store — canonicalized through store_mapping so it lands under the SAME
    store key the rest of the app uses, org-scoped — and upsert it into
    commcalc.inventory_value, the exact same table + shape (org_id, store, swept_value,
    as_of_date, source) the portal-sweep would have written, and what the Balance Sheet + the
    Account module's Inventory Values page (/accounts/inventory) already read. This value total
    is independent of category/qty mapping (a row can be unmappable to one of the 5 recon
    buckets — e.g. "Accessory" or "SIM Kit" — and still count toward the store's on-hand $
    value), and independent of whether asset_ledger has any rows for this org at all, so it
    works uniformly for every tenant, not just Boost/VIP ones. Degrades silently if
    commcalc.inventory_value doesn't exist yet (pre-migration-026 tenant) — the qty/category
    recon upload below is unaffected either way."""
    as_of = (body.get("as_of_date") or "").strip()
    if not as_of:
        raise HTTPException(400, "as_of_date required")
    rows = body.get("rows") or []
    if not isinstance(rows, list) or not rows:
        raise HTTPException(400, "rows[] required")
    client = sb()
    addr_map = _store_canon_map(client, org_id)
    agg = {}
    value_by_store = {}
    skipped = []
    for i, r in enumerate(rows):
        s_raw = (str(r.get("store") or "")).strip()
        s = _canon_store(s_raw, addr_map)
        bucket = _inv_bucket(r.get("category"))
        try:
            qty = int(float(r.get("qty")))
        except (TypeError, ValueError):
            qty = None
        val = None
        if r.get("value") not in (None, ""):
            try:
                val = float(str(r.get("value")).replace("$", "").replace(",", "").strip())
            except (TypeError, ValueError):
                val = None
        if s and val is not None:
            value_by_store[s] = value_by_store.get(s, 0.0) + val
        if not s or not bucket or qty is None:
            skipped.append({"row": i + 1, "store": s_raw, "category": r.get("category")})
            continue
        agg[(s, bucket)] = agg.get((s, bucket), 0) + qty
    # Replace this date's snapshot, then insert the aggregated rows.
    client.schema("commcalc").table("b2b_inventory").delete() \
        .eq("org_id", org_id).eq("as_of_date", as_of).execute()
    payload = [{"org_id": org_id, "store": s, "category": b, "qty": q,
                "as_of_date": as_of, "source": "upload"} for (s, b), q in agg.items()]
    if payload:
        client.schema("commcalc").table("b2b_inventory").insert(payload).execute()

    # LINK — write the $ value side into commcalc.inventory_value (Balance Sheet). Best-effort:
    # a tenant that hasn't run migration 026 yet still gets the qty/category recon above.
    inventory_value_stores = 0
    inventory_value_total = 0.0
    if value_by_store:
        try:
            for store_key, total in value_by_store.items():
                rec = {"org_id": org_id, "store": store_key, "swept_value": round(total, 2),
                       "as_of_date": as_of, "source": "asset_b2b_upload",
                       "updated_at": datetime.now(timezone.utc).isoformat()}
                client.schema("commcalc").table("inventory_value") \
                    .upsert(rec, on_conflict="org_id,store").execute()
            inventory_value_stores = len(value_by_store)
            inventory_value_total = round(sum(value_by_store.values()), 2)
        except Exception as _e:
            # commcalc.inventory_value may not exist yet (migration 026 not run for this tenant) —
            # never let that break the qty/category recon upload above, which already succeeded.
            print(f"[asset b2b-inventory upload] inventory_value write skipped (mig 026 not run?): {_e}")

    return {"loaded": len(payload), "skipped": len(skipped), "as_of_date": as_of,
            "skipped_rows": skipped[:20],
            "inventory_value_stores": inventory_value_stores,
            "inventory_value_total": inventory_value_total}


@router.post("/sync-inventory-flags")
async def sync_inventory_flags(org_id: str = ORG_ID):
    """Flag stores whose asset On-Inventory disagrees with b2bsoft, per category."""
    recon = await inventory_recon(org_id=org_id)
    client = sb()
    as_of = recon.get("as_of") or ""
    period, pm, py = "Inventory", None, None
    if as_of:
        try:
            py, pm, _ = [int(x) for x in str(as_of)[:10].split("-")]
            period = datetime(py, pm, 1).strftime("%B %Y")
        except Exception:
            pass
    client.schema("commcalc").table("flags").delete() \
        .eq("org_id", org_id).eq("source", "inventory_recon").execute()
    out = []
    for r in recon["rows"]:
        for k in INV_BUCKETS:
            diff = r["categories"][k]["diff"]
            if diff == 0:
                continue
            a, b = r["categories"][k]["asset"], r["categories"][k]["b2b"]
            out.append({
                "org_id": org_id, "period": period, "period_month": pm, "period_year": py,
                "flag_type": f"Inventory mismatch — {k}", "source": "inventory_recon",
                "severity": "warning", "store_address": r["store"],
                "amount": abs(diff),
                "description": f"{r['store']} {k}: asset on-inventory {a} vs b2bsoft {b} "
                               f"({'+' if diff > 0 else ''}{diff}) as of {as_of}"
                               + (f" [{r['market']}]" if r.get("market") else ""),
            })
    if out:
        for i in range(0, len(out), 500):
            client.schema("commcalc").table("flags").insert(out[i:i + 500]).execute()
    return {"flagged": len(out), "as_of": as_of, "b2b_loaded": recon["b2b_loaded"]}


@router.get("/owed-weekly")
async def get_owed_weekly(
    thursday: str,   # NOTE: this is the billing FRIDAY date (YYYY-MM-DD), matched against
                     # asset_ledger.billing_friday. The param/field is historically named
                     # `thursday`; kept for backward-compat with stored notify subscriptions.
    org_id: str = ORG_ID,
    store: str = "",
    market: str = "",
    weeks_ahead: int = 8,
    limit: int = 200,
    offset: int = 0,
):
    """VIP weekly collection report for a chosen billing Friday, plus upcoming forecast.
    VIP bills on Friday; `thursday` carries the Friday date (legacy name)."""
    from datetime import datetime, timedelta
    client = sb()

    # market / store accept a single value OR a comma-separated list (multi-select filter).
    store_list = [s.strip() for s in store.split(",") if s.strip()]
    market_list = [m.strip() for m in market.split(",") if m.strip()]

    def base(select_cols):
        q = client.schema("commcalc").table("asset_ledger").select(select_cols).eq("org_id", org_id)
        if store_list:
            q = q.in_("store", store_list)
        if market_list:
            q = q.in_("market", market_list)
        return q

    def fetch_all(select_cols, apply_filter):
        out = []
        page = 0; PAGE = 1000
        while True:
            start = page * PAGE
            q = apply_filter(base(select_cols)).range(start, start + PAGE - 1)
            chunk = q.execute().data or []
            out.extend(chunk)
            if len(chunk) < PAGE:
                break
            page += 1
            if page > 50:
                break
        return out

    # Devices billing on the selected Thursday
    due_rows = fetch_all(
        "store,market,bill_path,owed_to_vip",
        lambda q: q.eq("billing_friday", thursday),
    )

    sold_c = sold_o = aging_c = aging_o = 0.0
    store_map = {}
    for r in due_rows:
        o = float(r.get("owed_to_vip") or 0)
        is_aging = r.get("bill_path") == "aging"
        if is_aging:
            aging_c += 1; aging_o += o
        else:
            sold_c += 1; sold_o += o
        s = r.get("store") or "\u2014"
        if s not in store_map:
            store_map[s] = {"store": s, "market": r.get("market"),
                            "sold_count": 0, "sold_owed": 0.0,
                            "aging_count": 0, "aging_owed": 0.0}
        if is_aging:
            store_map[s]["aging_count"] += 1; store_map[s]["aging_owed"] += o
        else:
            store_map[s]["sold_count"] += 1; store_map[s]["sold_owed"] += o

    by_store = []
    for s in store_map.values():
        s["sold_owed"] = round(s["sold_owed"], 2)
        s["aging_owed"] = round(s["aging_owed"], 2)
        s["total_owed"] = round(s["sold_owed"] + s["aging_owed"], 2)
        by_store.append(s)
    by_store.sort(key=lambda x: x["total_owed"], reverse=True)

    due_this_week = {
        "sold":  {"count": int(sold_c),  "owed": round(sold_o, 2)},
        "aging": {"count": int(aging_c), "owed": round(aging_o, 2)},
        "total": {"count": int(sold_c + aging_c), "owed": round(sold_o + aging_o, 2)},
    }

    # Upcoming Thursdays forecast
    th = datetime.strptime(thursday, "%Y-%m-%d").date()
    end = (th + timedelta(weeks=weeks_ahead)).isoformat()
    up_rows = fetch_all(
        "bill_path,owed_to_vip,billing_friday",
        lambda q: q.gt("billing_friday", thursday).lte("billing_friday", end),
    )
    up_map = {}
    for r in up_rows:
        t = r.get("billing_friday")
        if not t:
            continue
        if t not in up_map:
            up_map[t] = {"thursday": t, "sold_owed": 0.0, "aging_owed": 0.0, "count": 0}
        o = float(r.get("owed_to_vip") or 0)
        up_map[t]["count"] += 1
        if r.get("bill_path") == "aging":
            up_map[t]["aging_owed"] += o
        else:
            up_map[t]["sold_owed"] += o
    upcoming = []
    for t in sorted(up_map.keys()):
        e = up_map[t]
        e["sold_owed"] = round(e["sold_owed"], 2)
        e["aging_owed"] = round(e["aging_owed"], 2)
        e["total_owed"] = round(e["sold_owed"] + e["aging_owed"], 2)
        upcoming.append(e)

    # Device rows for the selected Thursday (paginated)
    rows_resp = base("id,store,market,esn_imei,phone_number,device_model,contract_type,status,date_sold,due_date,bill_path,owed_to_vip") \
        .eq("billing_friday", thursday).order("owed_to_vip", desc=True) \
        .range(offset, offset + limit - 1).execute()
    week_rows = _attach_vip_invoices(client, org_id, rows_resp.data or [])

    return {
        "thursday": thursday,
        "filters": {"store": store or None, "market": market or None},
        "due_this_week": due_this_week,
        "by_store": by_store,
        "upcoming": upcoming,
        "rows": week_rows,
        "total_due_rows": len(due_rows),
        "offset": offset,
        "limit": limit,
    }


@router.get("/aging")
async def get_aging(
    org_id: str = ORG_ID,
    store: str = "",
    market: str = "",
    month: int = None,
    year: int = None,
):
    """Unsold On-Inventory aging report. Buckets by days since acquired_date (as of today).
    Optional month/year narrows to devices ACQUIRED in that period."""
    from datetime import date
    client = sb()

    def _acq_in_period(r):
        if month is None and year is None:
            return True
        a = r.get("acquired_date")
        if not a:
            return False
        try:
            py, pm, _ = [int(x) for x in str(a)[:10].split("-")]
        except Exception:
            return False
        if year is not None and int(year) != py:
            return False
        if month is not None and int(month) != pm:
            return False
        return True

    def fetch(extra):
        out = []
        page = 0; PAGE = 1000
        while True:
            start = page * PAGE
            q = client.schema("commcalc").table("asset_ledger") \
                .select("id,store,market,esn_imei,phone_number,device_model,category,status,acquired_date,due_date,date_sold,owed_to_vip,reimbursement,selling_price") \
                .eq("org_id", org_id).is_("date_sold", "null").ilike("category", "%On Inventory%")
            if store:
                q = q.eq("store", store)
            if market:
                q = q.eq("market", market)
            q = extra(q).range(start, start + PAGE - 1)
            chunk = q.execute().data or []
            out.extend(chunk)
            if len(chunk) < PAGE:
                break
            page += 1
            if page > 50:
                break
        return out

    rows = [r for r in fetch(lambda q: q) if _acq_in_period(r)]
    today = date.today()

    def days_aged(r):
        a = r.get("acquired_date")
        if not a:
            return None
        try:
            y, m, d = map(int, str(a)[:10].split("-"))
            return (today - date(y, m, d)).days
        except Exception:
            return None

    buckets = {
        "under45": {"count": 0, "owed": 0.0, "rows": []},
        "warn":    {"count": 0, "owed": 0.0, "rows": []},   # 45-60
        "missed":  {"count": 0, "owed": 0.0, "rows": []},   # >60
    }
    zero_rows = []  # plain On Inventory, $0 owed

    for r in rows:
        owed = float(r.get("owed_to_vip") or 0)
        if owed <= 0:
            zero_rows.append(r)
            continue
        d = days_aged(r)
        r["days_aged"] = d
        if d is None:
            continue
        if d < 45:
            b = "under45"
        elif d <= 60:
            b = "warn"
        else:
            b = "missed"
        buckets[b]["count"] += 1
        buckets[b]["owed"] += owed
        buckets[b]["rows"].append(r)

    for b in buckets.values():
        b["owed"] = round(b["owed"], 2)
        b["rows"].sort(key=lambda x: (x.get("days_aged") or 0), reverse=True)

    # Attach VIP invoice # + date to every device row we return (in place).
    zero_returned = zero_rows[:500]
    _attach_vip_invoices(client, org_id,
                         buckets["under45"]["rows"] + buckets["warn"]["rows"]
                         + buckets["missed"]["rows"] + zero_returned)
    _attach_investigation(client, org_id,
                          buckets["under45"]["rows"] + buckets["warn"]["rows"]
                          + buckets["missed"]["rows"] + zero_returned)

    # data freshness: max FileDate from raw_row
    fd = None
    sample = client.schema("commcalc").table("asset_ledger") \
        .select("raw_row").eq("org_id", org_id).limit(1).execute().data or []
    if sample and sample[0].get("raw_row"):
        fd = sample[0]["raw_row"].get("FileDate")
        if fd:
            fd = str(fd)[:10]

    return {
        "today": today.isoformat(),
        "data_as_of": fd,
        "buckets": buckets,
        "zero_inventory": {"count": len(zero_rows), "rows": zero_returned},
        "totals": {
            "flagged_count": sum(b["count"] for b in buckets.values()),
            "flagged_owed": round(sum(b["owed"] for b in buckets.values()), 2),
        },
    }


def _attach_investigation(client, org_id, rows):
    """Attach physically_missing + investigation_remark to device rows (by ESN/IMEI) from the side
    asset_investigation table. Best-effort so a not-yet-run migration can't break the report."""
    imeis = [r.get("esn_imei") for r in rows if r.get("esn_imei")]
    if not imeis:
        return
    inv = {}
    try:
        for i in range(0, len(imeis), 300):
            chunk = (client.schema("commcalc").table("asset_investigation")
                     .select("esn_imei,physically_missing,remark,investigated_by,updated_at")
                     .eq("org_id", org_id).in_("esn_imei", imeis[i:i + 300]).execute().data) or []
            for x in chunk:
                inv[x.get("esn_imei")] = x
    except Exception:
        return
    for r in rows:
        x = inv.get(r.get("esn_imei"))
        r["physically_missing"] = bool(x and x.get("physically_missing"))
        r["investigation_remark"] = (x or {}).get("remark") or ""


@router.post("/investigation")
async def set_investigation(body: dict, org_id: str = ORG_ID):
    """Record an aging investigation for a device (physically-missing flag + remark). Upsert by ESN/IMEI
    so it survives asset_ledger re-uploads."""
    client = sb()
    imei = (body.get("esn_imei") or "").strip()
    if not imei:
        raise HTTPException(400, "esn_imei required")
    row = {"org_id": org_id, "esn_imei": imei,
           "physically_missing": bool(body.get("physically_missing")),
           "remark": (body.get("remark") or "").strip() or None,
           "investigated_by": (body.get("investigated_by") or "").strip() or None,
           "updated_at": datetime.now(timezone.utc).isoformat()}
    client.schema("commcalc").table("asset_investigation").upsert(row, on_conflict="org_id,esn_imei").execute()
    return {"ok": True, "esn_imei": imei, "physically_missing": row["physically_missing"]}


@router.get("/missing-phones")
async def get_missing_phones(org_id: str = ORG_ID, store: str = "", market: str = ""):
    """Devices a user flagged as physically MISSING during aging investigation, joined to asset_ledger
    for device detail + the owed-to-distributor exposure. The list to investigate."""
    client = sb()
    flagged = (client.schema("commcalc").table("asset_investigation")
               .select("esn_imei,remark,investigated_by,updated_at")
               .eq("org_id", org_id).eq("physically_missing", True).limit(50000).execute().data) or []
    by_imei = {f.get("esn_imei"): f for f in flagged if f.get("esn_imei")}
    if not by_imei:
        return {"rows": [], "count": 0, "owed_total": 0.0}
    imeis = list(by_imei)
    dev = []
    for i in range(0, len(imeis), 300):
        dev.extend((client.schema("commcalc").table("asset_ledger")
                    .select("store,market,esn_imei,phone_number,device_model,category,status,acquired_date,due_date,owed_to_vip")
                    .eq("org_id", org_id).in_("esn_imei", imeis[i:i + 300]).execute().data) or [])
    seen, out = set(), []
    for r in dev:
        im = r.get("esn_imei")
        if im in seen:
            continue
        seen.add(im)
        if store and r.get("store") != store:
            continue
        if market and r.get("market") != market:
            continue
        f = by_imei.get(im, {})
        out.append({**r, "remark": f.get("remark") or "", "investigated_by": f.get("investigated_by"),
                    "flagged_at": f.get("updated_at")})
    # flagged devices no longer in the ledger (dropped off a later upload) — still surface them
    if not (store or market):
        in_ledger = {r.get("esn_imei") for r in dev}
        for im, f in by_imei.items():
            if im not in in_ledger:
                out.append({"esn_imei": im, "store": None, "market": None, "device_model": None,
                            "owed_to_vip": None, "not_in_ledger": True, "remark": f.get("remark") or "",
                            "investigated_by": f.get("investigated_by"), "flagged_at": f.get("updated_at")})
    out.sort(key=lambda x: -(float(x.get("owed_to_vip") or 0)))
    return {"rows": out, "count": len(out),
            "owed_total": round(sum(float(r.get("owed_to_vip") or 0) for r in out), 2)}


@router.get("/aging-rebate")
async def get_aging_rebate(org_id: str = ORG_ID, store: str = "", market: str = ""):
    """Devices STILL in Inventory Aging (unsold On-Inventory) but for which a REBATE was received — i.e.
    they were effectively sold/activated, so they can be taken OUT of inventory. Each is matched to its
    ePay rebate (raw_payment_detail by IMEI, with the rebate date) and to a sale (raw_sales serial). A
    rebate with NO matching sale on record is flagged for investigation."""
    client = sb()

    def _fl(v):
        try:
            return float(v or 0)
        except Exception:
            return 0.0

    def base(cols):
        q = (client.schema("commcalc").table("asset_ledger").select(cols).eq("org_id", org_id)
             .is_("date_sold", "null").ilike("category", "%On Inventory%"))
        if store:
            q = q.eq("store", store)
        if market:
            q = q.eq("market", market)
        return q
    rows, page = [], 0
    while True:
        chunk = (base("store,market,esn_imei,phone_number,device_model,category,status,acquired_date,due_date,owed_to_vip,reimbursement")
                 .range(page * 1000, page * 1000 + 999).execute().data) or []
        rows.extend(chunk)
        if len(chunk) < 1000 or page > 50:
            break
        page += 1

    cand = [r for r in rows if _fl(r.get("reimbursement")) > 0]   # rebate received but still in inventory
    imeis = [str(r.get("esn_imei") or "").strip() for r in cand if r.get("esn_imei")]
    rebate_by_imei, sale_by_imei = {}, {}
    for i in range(0, len(imeis), 200):
        batch = imeis[i:i + 200]
        try:
            for p in (client.schema("commcalc").table("raw_payment_detail")
                      .select("imei,amount,payment_date").eq("org_id", org_id).in_("imei", batch).execute().data or []):
                im = str(p.get("imei") or "").strip()
                if not im:
                    continue
                d = rebate_by_imei.setdefault(im, {"amount": 0.0, "dates": set()})
                d["amount"] += _fl(p.get("amount"))
                if p.get("payment_date"):
                    d["dates"].add(str(p.get("payment_date"))[:10])
        except Exception:
            pass
        try:
            for s in (client.schema("commcalc").table("raw_sales")
                      .select("serial_1,trans_date,product_desc").eq("org_id", org_id).in_("serial_1", batch).execute().data or []):
                im = str(s.get("serial_1") or "").strip()
                if im and im not in sale_by_imei:
                    sale_by_imei[im] = {"trans_date": str(s.get("trans_date") or "")[:10], "product_desc": s.get("product_desc")}
        except Exception:
            pass

    out = []
    for r in cand:
        im = str(r.get("esn_imei") or "").strip()
        reb = rebate_by_imei.get(im)
        sale = sale_by_imei.get(im)
        reb_dates = sorted(reb["dates"]) if reb else []
        out.append({
            "esn_imei": im, "store": r.get("store"), "market": r.get("market"),
            "device_model": r.get("device_model"), "acquired_date": r.get("acquired_date"),
            "owed_to_vip": round(_fl(r.get("owed_to_vip")), 2), "rebate": round(_fl(r.get("reimbursement")), 2),
            "rebate_date": reb_dates[-1] if reb_dates else None,
            "sale_found": bool(sale), "sale_date": (sale or {}).get("trans_date"),
            "unmatched": not bool(sale),   # rebate received but NO sale on record for this IMEI → investigate
        })
    out.sort(key=lambda x: (0 if x["unmatched"] else 1, -(x["owed_to_vip"] or 0)))
    return {"rows": out, "count": len(out),
            "totals": {"rebate": round(sum(x["rebate"] for x in out), 2),
                       "owed": round(sum(x["owed_to_vip"] for x in out), 2),
                       "unmatched": sum(1 for x in out if x["unmatched"]), "stores": len({x["store"] for x in out})}}


@router.get("/on-inventory-by-store")
async def get_on_inventory_by_store(
    org_id: str = ORG_ID,
    store: str = "",
    market: str = "",
    month: int = None,
    year: int = None,
):
    """On-Inventory exposure rolled up per store: how many unsold devices each store holds
    and the $ owed to VIP, with the same aging buckets as the Inventory Aging report
    (<45 / 45-60 WARN / >60 MISSED, measured from acquired_date as of today). Optional
    month/year narrows to devices ACQUIRED in that period. Numbers reconcile with /aging."""
    from datetime import date
    client = sb()

    def _acq_in_period(r):
        if month is None and year is None:
            return True
        a = r.get("acquired_date")
        if not a:
            return False
        try:
            py, pm, _ = [int(x) for x in str(a)[:10].split("-")]
        except Exception:
            return False
        if year is not None and int(year) != py:
            return False
        if month is not None and int(month) != pm:
            return False
        return True

    rows = []
    page = 0; PAGE = 1000
    while True:
        start = page * PAGE
        q = client.schema("commcalc").table("asset_ledger") \
            .select("store,market,acquired_date,owed_to_vip") \
            .eq("org_id", org_id).is_("date_sold", "null").ilike("category", "%On Inventory%")
        if store:
            q = q.eq("store", store)
        if market:
            q = q.eq("market", market)
        chunk = q.range(start, start + PAGE - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 50:
            break
    rows = [r for r in rows if _acq_in_period(r)]
    today = date.today()

    def days_aged(a):
        if not a:
            return None
        try:
            y, m, d = map(int, str(a)[:10].split("-"))
            return (today - date(y, m, d)).days
        except Exception:
            return None

    def blank(s, mkt):
        return {"store": s, "market": mkt, "count": 0, "owed": 0.0,
                "under45_count": 0, "under45_owed": 0.0,
                "warn_count": 0, "warn_owed": 0.0,
                "missed_count": 0, "missed_owed": 0.0,
                "zero_count": 0}

    by_store: dict = {}
    for r in rows:
        s = r.get("store") or "(unknown)"
        row = by_store.setdefault(s, blank(s, r.get("market")))
        if not row["market"] and r.get("market"):
            row["market"] = r.get("market")
        owed = float(r.get("owed_to_vip") or 0)
        row["count"] += 1
        if owed <= 0:
            row["zero_count"] += 1
            continue
        row["owed"] += owed
        d = days_aged(r.get("acquired_date"))
        if d is None:
            continue
        if d < 45:
            bk = "under45"
        elif d <= 60:
            bk = "warn"
        else:
            bk = "missed"
        row[f"{bk}_count"] += 1
        row[f"{bk}_owed"] += owed

    stores = []
    for v in by_store.values():
        for k in ("owed", "under45_owed", "warn_owed", "missed_owed"):
            v[k] = round(v[k], 2)
        stores.append(v)
    stores.sort(key=lambda x: x["owed"], reverse=True)

    # data freshness: max FileDate from raw_row (same signal the Aging report uses)
    fd = None
    sample = client.schema("commcalc").table("asset_ledger") \
        .select("raw_row").eq("org_id", org_id).limit(1).execute().data or []
    if sample and sample[0].get("raw_row"):
        fd = sample[0]["raw_row"].get("FileDate")
        if fd:
            fd = str(fd)[:10]

    totals = {
        "store_count": len(stores),
        "device_count": sum(s["count"] for s in stores),
        "owed": round(sum(s["owed"] for s in stores), 2),
        "missed_owed": round(sum(s["missed_owed"] for s in stores), 2),
        "warn_owed": round(sum(s["warn_owed"] for s in stores), 2),
        "zero_count": sum(s["zero_count"] for s in stores),
    }
    return {"today": today.isoformat(), "data_as_of": fd, "stores": stores, "totals": totals}


# ---- Asset charge classification (single source of truth) ----
CHARGE_GROUPS = {
    "vip_fees":      ["PROCESSING FEE", "SHIPPING", "SIM KIT"],
    "stock_balance": ["Stock Balancing"],
    "appeals":       ["Appeal Denied. Details in Boost Appeals Status",
                      "Re-Escalation",
                      "Over 10 Days Missing Reimbursement (CheckElevate/Submit Appeal)",
                      "Missing 1st MRC",
                      "Failed Activation. Check Boost Payment Status"],
    "recon_oddity":  ["Phone Number Paid to Different ESN", "No Elevate Data. Received Commissions",
                      "Non-Promo Elevate Coupon", "Exchange/Return"],
}
GROUP_LABELS = {
    "vip_fees": "Distributor Fees", "stock_balance": "Stock Balancing / Returns",
    "appeals": "Appeals & Denied Payments", "recon_oddity": "Reconciliation Oddities",
}

def _cat_to_group(cat: str):
    for g, cats in CHARGE_GROUPS.items():
        if cat in cats:
            return g
    return None


def _fetch_asset_rows(client, org_id, store="", market="", select="*"):
    rows = []
    page = 0; PAGE = 1000
    while True:
        start = page * PAGE
        q = client.schema("commcalc").table("asset_ledger").select(select).eq("org_id", org_id)
        if store:
            q = q.eq("store", store)
        if market:
            q = q.eq("market", market)
        chunk = q.range(start, start + PAGE - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 60:
            break
    return rows


def _row_period_date(r):
    """Date that places a charge in a period: PAYG > date_sold > acquired."""
    return r.get("payg_date") or r.get("date_sold") or r.get("acquired_date")


def _in_period(r, month=None, year=None, week_friday=None):
    if week_friday:
        bf = r.get("billing_friday")
        return str(bf)[:10] == week_friday if bf else False
    if year is None and month is None:
        return True
    d = _row_period_date(r)
    if not d:
        return False
    try:
        py, pm, _ = [int(x) for x in str(d)[:10].split("-")]
    except Exception:
        return False
    if year is not None and int(year) != py:
        return False
    if month is not None and int(month) != pm:
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# HOTSHEET EXPECTED-vs-PAID RECONCILIATION
# For each ACTIVATED device, compare the ACTUAL Boost reimbursement
# (asset_ledger.reimbursement) against the EXPECTED promo from the pricing hotsheet
# (commcalc.hotsheet) for that device_model, effective as of the device's
# acquired_date, on the promo column selected by the activation type (contract_type).
# Read-only report; the flag-sync is a separate manual POST (no auto-write).
# ─────────────────────────────────────────────────────────────────────────────

# contract_type -> which hotsheet promo column is "expected".
# Precedence Upgrade > AAL > Port-In > Non-Port, derived from the commission engine's
# contract-type taxonomy (commcalc/calculator.py). The combined-type precedence
# (e.g. "Port-In Add A Line" -> AAL) is the ONE business assumption here — flip the
# order below if Boost's hotsheet treats those as Port-In instead.
def _promo_type(contract_type):
    ct = (contract_type or "").strip().lower()
    if not ct:
        return None
    if "upgrade" in ct:
        return "promo_upgrade"
    if "add a line" in ct or ct == "aal" or ct.endswith(" aal"):
        return "promo_aal"
    if "port" in ct:                 # port-in: new line, number ported
        return "promo_port_in"
    return "promo_non_port"          # plain Activation / non-ported new line / BYOD

_PROMO_LABEL = {
    "promo_port_in": "Port-In", "promo_non_port": "Non-Port",
    "promo_upgrade": "Upgrade", "promo_aal": "AAL",
}


def _norm_model(s):
    return " ".join((s or "").strip().lower().split())


def _hotsheet_lookup(client, org_id):
    """norm_model -> list of (effective_date 'YYYY-MM-DD', {promo_*: raw}) sorted ascending."""
    rows = (client.schema("commcalc").table("hotsheet")
            .select("device_model,effective_date,promo_port_in,promo_non_port,promo_upgrade,promo_aal")
            .eq("org_id", org_id).execute().data) or []
    by_model = {}
    for r in rows:
        m = _norm_model(r.get("device_model"))
        if not m:
            continue
        by_model.setdefault(m, []).append((
            str(r.get("effective_date") or "")[:10],
            {k: r.get(k) for k in ("promo_port_in", "promo_non_port", "promo_upgrade", "promo_aal")},
        ))
    for m in by_model:
        by_model[m].sort(key=lambda t: t[0])
    return by_model


def _effective_promos(entries, acquired_date):
    """Pick the hotsheet row effective as-of acquired_date (latest effective_date <= acquired_date;
    fall back to the earliest hotsheet if the device predates every one)."""
    if not entries:
        return None, None
    a = str(acquired_date or "")[:10]
    chosen = None
    for eff, promos in entries:                      # ascending
        if a and eff and eff <= a:
            chosen = (eff, promos)
        elif not a:
            chosen = (eff, promos)
    if chosen is None:
        chosen = entries[0]                          # older than any hotsheet -> earliest
    return chosen[1], chosen[0]


def _compute_hotsheet_recon(client, org_id, store="", market="", month=None, year=None, tolerance=1.0):
    hs = _hotsheet_lookup(client, org_id)
    hotsheet_loaded = bool(hs)
    rows = _fetch_asset_rows(
        client, org_id, store=store, market=market,
        select=("store,market,esn_imei,phone_number,device_model,contract_type,"
                "category,status,date_sold,acquired_date,reimbursement,owed_to_vip"),
    )

    def _in_period(r):
        if month is None and year is None:
            return True
        a = r.get("acquired_date")
        if not a:
            return False
        try:
            py, pm, _ = [int(x) for x in str(a)[:10].split("-")]
        except Exception:
            return False
        if year is not None and int(year) != py:
            return False
        if month is not None and int(month) != pm:
            return False
        return True

    tol = float(tolerance or 0)
    buckets = ["matched", "underpaid", "overpaid", "no_expected", "no_hotsheet", "unmapped_type"]
    summary = {b: {"count": 0, "expected": 0.0, "actual": 0.0, "variance": 0.0} for b in buckets}
    by_type = {}
    items = []
    unmatched_models = {}
    model_display = {}
    unmapped_types = {}
    skipped_unactivated = 0

    def _accum(bucket, expected, actual, ptype, is_under):
        s = summary[bucket]
        s["count"] += 1
        s["expected"] += expected
        s["actual"] += actual
        s["variance"] += (actual - expected)
        if ptype:
            lbl = _PROMO_LABEL[ptype]
            t = by_type.setdefault(lbl, {"count": 0, "expected": 0.0, "actual": 0.0,
                                         "variance": 0.0, "underpaid_count": 0})
            t["count"] += 1
            t["expected"] += expected
            t["actual"] += actual
            t["variance"] += (actual - expected)
            if is_under:
                t["underpaid_count"] += 1

    for r in rows:
        if not _in_period(r):
            continue
        # Exclude pure unsold On-Inventory (never activated -> no reimbursement expected).
        # Same definition as the On-Inventory report: date_sold null AND category ~ On Inventory.
        if (not r.get("date_sold")) and ("on inventory" in (r.get("category") or "").lower()):
            skipped_unactivated += 1
            continue

        ct = (r.get("contract_type") or "").strip()
        ptype = _promo_type(ct)
        actual = float(r.get("reimbursement") or 0)
        model = r.get("device_model") or ""
        nm = _norm_model(model)
        model_display.setdefault(nm, model)

        item = {
            "store": r.get("store"), "market": r.get("market"),
            "imei": r.get("esn_imei"), "mdn": r.get("phone_number"),
            "device_model": model, "contract_type": ct,
            "promo_type": _PROMO_LABEL.get(ptype) if ptype else None,
            "acquired_date": (str(r.get("acquired_date"))[:10] if r.get("acquired_date") else None),
            "actual": round(actual, 2), "expected": None, "variance": None,
            "effective_date": None, "bucket": None,
        }

        if ptype is None:
            item["bucket"] = "unmapped_type"
            if ct:
                unmapped_types[ct] = unmapped_types.get(ct, 0) + 1
            _accum("unmapped_type", 0.0, actual, None, False)
            items.append(item)
            continue

        entries = hs.get(nm)
        if not entries:
            item["bucket"] = "no_hotsheet"
            unmatched_models[nm] = unmatched_models.get(nm, 0) + 1
            _accum("no_hotsheet", 0.0, actual, ptype, False)
            items.append(item)
            continue

        promos, eff = _effective_promos(entries, r.get("acquired_date"))
        item["effective_date"] = eff
        exp_raw = promos.get(ptype) if promos else None
        if exp_raw is None or str(exp_raw).strip() == "":
            item["bucket"] = "no_expected"
            _accum("no_expected", 0.0, actual, ptype, False)
            items.append(item)
            continue

        expected = float(exp_raw or 0)
        variance = round(actual - expected, 2)
        item["expected"] = round(expected, 2)
        item["variance"] = variance
        if abs(variance) <= tol:
            bucket = "matched"
        elif actual < expected:
            bucket = "underpaid"
        else:
            bucket = "overpaid"
        item["bucket"] = bucket
        _accum(bucket, expected, actual, ptype, bucket == "underpaid")
        items.append(item)

    for b in summary:
        for k in ("expected", "actual", "variance"):
            summary[b][k] = round(summary[b][k], 2)
    for lbl in by_type:
        for k in ("expected", "actual", "variance"):
            by_type[lbl][k] = round(by_type[lbl][k], 2)

    return {
        "hotsheet_loaded": hotsheet_loaded,
        "tolerance": tol,
        "device_count": len(items),
        "skipped_unactivated": skipped_unactivated,
        "summary": summary,
        "underpaid_total": round(-summary["underpaid"]["variance"], 2),   # positive $ shortfall
        "overpaid_total": round(summary["overpaid"]["variance"], 2),
        "by_type": [{"promo_type": k, **v} for k, v in sorted(by_type.items())],
        "items": items,
        "unmatched_models": sorted(
            [{"device_model": model_display.get(m, m), "count": c} for m, c in unmatched_models.items()],
            key=lambda x: -x["count"])[:200],
        "unmapped_contract_types": sorted(
            [{"contract_type": t, "count": c} for t, c in unmapped_types.items()],
            key=lambda x: -x["count"]),
    }


@router.get("/hotsheet-recon")
async def hotsheet_recon(org_id: str = ORG_ID, store: str = "", market: str = "",
                         month: int = None, year: int = None, tolerance: float = 1.0):
    """Expected (pricing hotsheet promo) vs actual (Boost reimbursement) per activated device.
    Buckets: matched / underpaid / overpaid / no_expected (model on hotsheet but blank for that
    promo type) / no_hotsheet (model not on any hotsheet) / unmapped_type. The promo column is
    chosen from contract_type (Upgrade>AAL>Port-In>Non-Port). Unsold On-Inventory is excluded."""
    return _compute_hotsheet_recon(sb(), org_id, store=store, market=market,
                                   month=month, year=year, tolerance=tolerance)


def _sync_hotsheet_flags(client, org_id, tolerance=1.0):
    """Underpaid devices -> commcalc.flags (delete-first by source then insert).
    actual==0 & expected>0 -> critical (Boost paid nothing); partial short -> warning."""
    import datetime as _dt
    recon = _compute_hotsheet_recon(client, org_id, tolerance=tolerance)
    flags = []
    for it in recon["items"]:
        if it.get("bucket") != "underpaid":
            continue
        expected = float(it.get("expected") or 0)
        actual = float(it.get("actual") or 0)
        shortfall = round(expected - actual, 2)
        sev = "critical" if actual <= 0 < expected else "warning"
        d = it.get("acquired_date")
        period, pm, py = "Unknown", None, None
        if d:
            try:
                py, pm, _ = [int(x) for x in str(d)[:10].split("-")]
                period = _dt.date(py, pm, 1).strftime("%B %Y")
            except Exception:
                pass
        flags.append({
            "org_id": org_id, "period": period, "period_month": pm, "period_year": py,
            "flag_type": "Hotsheet Underpayment", "source": "asset_hotsheet",
            "severity": sev, "store_address": it.get("store"),
            "imei": it.get("imei"), "mdn": it.get("mdn"),
            "amount": shortfall, "phone_model": it.get("device_model"),
            "description": (f"{it.get('promo_type') or '?'} promo: hotsheet expected {expected:.2f}, "
                            f"Carrier reimbursed {actual:.2f} (short {shortfall:.2f})"),
        })
    client.schema("commcalc").table("flags").delete() \
        .eq("org_id", org_id).eq("source", "asset_hotsheet").execute()
    for i in range(0, len(flags), 500):
        client.schema("commcalc").table("flags").insert(flags[i:i + 500]).execute()
    return len(flags)


@router.post("/sync-hotsheet-flags")
async def sync_hotsheet_flags(org_id: str = ORG_ID, tolerance: float = 1.0):
    """Manually write hotsheet-underpayment flags (opt-in; not auto-run on upload)."""
    return {"flags_written": _sync_hotsheet_flags(sb(), org_id, tolerance)}


@router.get("/charges-summary")
async def get_charges_summary(org_id: str = ORG_ID, store: str = "", market: str = "", month: int = None, year: int = None, week_friday: str = ""):
    """Charge groups + Total Loss via Postgres aggregation (fast). Totals only — no row lists."""
    client = sb()
    params = {
        "p_org_id": org_id,
        "p_store": store or None,
        "p_market": market or None,
        "p_month": month,
        "p_year": year,
        "p_week_friday": week_friday or None,
    }
    agg = client.schema("commcalc").rpc("asset_charges_summary", params).execute().data or []

    groups = {}
    for gk in CHARGE_GROUPS:
        groups[gk] = {"key": gk, "label": GROUP_LABELS[gk],
                      "count": 0, "owed": 0.0, "by_category": {}, "by_store": {}}

    for row in agg:
        gk = _cat_to_group(row.get("category") or "")
        if not gk:
            continue
        cnt = int(row.get("cnt") or 0)
        owed = float(row.get("owed") or 0)
        G = groups[gk]
        G["count"] += cnt
        G["owed"] += owed
        c = row.get("category") or "Unknown"
        G["by_category"].setdefault(c, {"category": c, "count": 0, "owed": 0.0})
        G["by_category"][c]["count"] += cnt
        G["by_category"][c]["owed"] += owed
        s = row.get("store") or "—"
        G["by_store"].setdefault(s, {"store": s, "market": row.get("market"), "count": 0, "owed": 0.0})
        G["by_store"][s]["count"] += cnt
        G["by_store"][s]["owed"] += owed

    for G in groups.values():
        G["owed"] = round(G["owed"], 2)
        G["by_category"] = sorted(({**v, "owed": round(v["owed"], 2)} for v in G["by_category"].values()),
                                  key=lambda x: x["owed"], reverse=True)
        G["by_store"] = sorted(({**v, "owed": round(v["owed"], 2)} for v in G["by_store"].values()),
                               key=lambda x: x["owed"], reverse=True)

    # Total Loss = denied appeals owed + RMA net loss (unreimbursed full + shortfall)
    appeals_loss = round(sum(v["owed"] for v in groups["appeals"]["by_category"]), 2)
    rma_loss = 0.0
    for row in agg:
        if (row.get("category") or "") == "RMA":
            owed = float(row.get("owed") or 0)
            reimb = float(row.get("reimb") or 0)
            if reimb <= 0:
                rma_loss += owed
            elif reimb < owed - 0.01:
                rma_loss += (owed - reimb)
    rma_loss = round(rma_loss, 2)

    return {
        "groups": groups,
        "total_loss": {"total": round(appeals_loss + rma_loss, 2), "appeals": appeals_loss, "rma": rma_loss},
        "filters": {"store": store or None, "market": market or None, "month": month, "year": year, "week_friday": week_friday or None},
    }


def _epay_evidence(epay):
    """Compact one-line summary of a device's ePay Payment Detail lines, grouped by
    payment type. e.g. 'ePay paid $123.45 (MI $80.00; ATU $43.45)'. Empty list → ''."""
    if not epay:
        return ""
    sums = {}
    for p in epay:
        t = p.get("type") or "—"
        sums[t] = sums.get(t, 0.0) + float(p.get("amount") or 0)
    total = round(sum(sums.values()), 2)
    parts = [f"{t} ${a:,.2f}" for t, a in sorted(sums.items(), key=lambda x: -x[1])]
    tail = "; ".join(parts[:4]) + (f"; +{len(parts) - 4} more" if len(parts) > 4 else "")
    return f"ePay paid ${total:,.2f} ({tail})"


def _appeal_reason(r, epay=None, epay_loaded=False):
    """Human-readable reason an appeal row is a loss. There is no single denial-reason
    column, so we build it from the category plus the concrete raw_row signals when present
    (notably 'PN paid to ESN', i.e. the phone number's credit was paid against a DIFFERENT
    device), and — when the ePay Payment Detail Report is loaded — the actual payments Boost
    made for this device (joined by IMEI), which is the true per-appeal evidence."""
    cat = (r.get("category") or "").strip()
    raw = r.get("raw_row") or {}
    pn_esn = (raw.get("PN paid to ESN") or "").strip() if raw.get("PN paid to ESN") else ""
    reimb_pn = (raw.get("Reimbursement on PN") or "").strip() if raw.get("Reimbursement on PN") else ""
    pn_note = ""
    if pn_esn:
        pn_note = f"phone number's credit paid to a different ESN ({pn_esn})"
        if reimb_pn:
            pn_note += f", ${reimb_pn} reimbursed there"
    base = {
        "Re-Escalation": "Re-escalation submitted to Carrier — awaiting decision",
        "Missing 1st MRC": "Missing 1st month recurring charge (1st MRC) — no Carrier payment received",
        "Failed Activation. Check Boost Payment Status": "Failed activation — check Carrier payment status",
        "Over 10 Days Missing Reimbursement (CheckElevate/Submit Appeal)":
            "Over 10 days missing reimbursement — check Elevate / submit appeal",
    }.get(cat)
    # Concrete ePay evidence (when the Payment Detail Report is loaded): what Boost actually
    # paid for this device, or that nothing was paid — the true per-appeal denial signal.
    ev = _epay_evidence(epay)
    if ev:
        epay_note = ev
    elif epay_loaded:
        epay_note = "ePay: no payment found for this device"
    else:
        epay_note = ""

    if cat.startswith("Appeal Denied"):
        reason = (f"Appeal denied — {pn_note}." if pn_note else "Appeal denied.")
        reason += f" {epay_note}." if epay_note else " See Carrier Payment Detail Report (ePay)."
        return reason
    if base:
        parts = [base]
    else:
        parts = [cat or "—"]
    if pn_note:
        parts.append(pn_note)
    if epay_note:
        parts.append(epay_note)
    return " · ".join(parts)


@router.get("/charge-rows")
async def get_charge_rows(
    group: str,
    org_id: str = ORG_ID,
    store: str = "",
    market: str = "",
    month: int = None,
    year: int = None,
    week_friday: str = "",
    limit: int = 500,
    offset: int = 0,
):
    """Per-device line items for one charge group (appeals / vip_fees / stock_balance / recon_oddity).

    Returns IMEI/ESN, store, market, device and the period date so the charge-group
    report pages can show real line items (the /charges-summary endpoint is totals-only).
    Filtered by store / market / period; period filter mirrors _in_period().
    """
    cats = CHARGE_GROUPS.get(group)
    if not cats:
        raise HTTPException(status_code=400, detail=f"Unknown charge group '{group}'")
    client = sb()

    # Pull every row in this group's categories (bounded subset, not the whole ledger),
    # honoring store/market filters in the query; period is filtered in Python below.
    # For appeals we also need raw_row to derive the denial reason.
    sel = ("id,store,market,esn_imei,phone_number,device_model,category,status,"
           "date_sold,payg_date,acquired_date,billing_friday,owed_to_vip,reimbursement,commissions,selling_price,notes")
    if group == "appeals":
        sel += ",raw_row"
    rows = []
    page = 0; PAGE = 1000
    while True:
        start = page * PAGE
        q = client.schema("commcalc").table("asset_ledger") \
            .select(sel) \
            .eq("org_id", org_id).in_("category", cats)
        if store:
            q = q.eq("store", store)
        if market:
            q = q.eq("market", market)
        chunk = q.range(start, start + PAGE - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 60:
            break

    # Period filter (same rules as the summary): PAYG > date_sold > acquired, or billing_friday for week.
    if week_friday or month is not None or year is not None:
        rows = [r for r in rows if _in_period(r, month=month, year=year, week_friday=week_friday or None)]

    # Attach the period date used for placement so the report can show it.
    for r in rows:
        r["period_date"] = _row_period_date(r)

    rows.sort(key=lambda x: float(x.get("owed_to_vip") or 0), reverse=True)

    total = len(rows)
    total_owed = round(sum(float(r.get("owed_to_vip") or 0) for r in rows), 2)
    page_rows = _attach_vip_invoices(client, org_id, rows[offset:offset + limit])

    # Appeals: derive the denial reason — joining the ePay Payment Detail Report (by IMEI)
    # for the true per-appeal evidence (what Boost actually paid) — then drop bulky raw_row.
    epay_loaded = False
    if group == "appeals":
        epay_loaded = _epay_has_data(client, org_id)
        epay_map = _epay_payments_map(client, org_id, [r.get("esn_imei") for r in page_rows])
        for r in page_rows:
            pays = epay_map.get(_norm_imei(r.get("esn_imei")), [])
            r["epay_payments"] = pays
            r["denial_reason"] = _appeal_reason(r, pays, epay_loaded)
            r.pop("raw_row", None)

    return {
        "group": group,
        "label": GROUP_LABELS.get(group, group),
        "rows": page_rows,
        "total": total,
        "total_owed": total_owed,
        "offset": offset,
        "limit": limit,
        "epay_loaded": epay_loaded,
        "filters": {"store": store or None, "market": market or None,
                    "month": month, "year": year, "week_friday": week_friday or None},
    }


def _sync_appeal_flags(client, org_id):
    """Write appeal-group asset rows into commcalc.flags (delete-first + insert, keyed on source)."""
    rows = _fetch_asset_rows(
        client, org_id, select="store,esn_imei,phone_number,device_model,category,owed_to_vip,payg_date,date_sold,acquired_date",
    )
    appeal_cats = set(CHARGE_GROUPS["appeals"])
    flags = []
    for r in rows:
        if (r.get("category") or "") not in appeal_cats:
            continue
        d = r.get("payg_date") or r.get("date_sold") or r.get("acquired_date")
        period = "Unknown"; pm = None; py = None
        if d:
            try:
                py, pm, _ = [int(x) for x in str(d)[:10].split("-")]
                period = __import__("datetime").date(py, pm, 1).strftime("%B %Y")
            except Exception:
                pass
        flags.append({
            "org_id": org_id, "period": period, "period_month": pm, "period_year": py,
            "flag_type": "Asset Appeal / Denied Payment", "source": "asset_appeal",
            "severity": "critical", "store_address": r.get("store"),
            "imei": r.get("esn_imei"), "mdn": r.get("phone_number"),
            "amount": float(r.get("owed_to_vip") or 0),
            "phone_model": r.get("device_model"),
            "description": f"Carrier {r.get('category')} — potential unpaid/denied amount",
        })

    # delete-first then plain insert (dedup pattern)
    client.schema("commcalc").table("flags").delete() \
        .eq("org_id", org_id).eq("source", "asset_appeal").execute()
    for i in range(0, len(flags), 500):
        client.schema("commcalc").table("flags").insert(flags[i:i+500]).execute()
    return len(flags)


@router.post("/sync-appeal-flags")
async def sync_appeal_flags(org_id: str = ORG_ID):
    """Manual refresh: rewrite appeal flags from current asset data."""
    client = sb()
    n = _sync_appeal_flags(client, org_id)
    return {"status": "ok", "appeal_flags_written": n}


def _classify_rma(r):
    """Return (bucket, owed, reimb) for an RMA row. bucket in full/short/none."""
    try: owed = float(r.get("owed_to_vip") or 0)
    except Exception: owed = 0.0
    try: reimb = float(r.get("reimbursement") or 0)
    except Exception: reimb = 0.0
    rd = r.get("reimbursement_date")
    has_date = rd not in (None, "", "nan", "NaT", "None")
    got = reimb > 0 or has_date
    if not got:
        return "none", owed, reimb
    if reimb < owed - 0.01:
        return "short", owed, reimb
    return "full", owed, reimb


@router.get("/rma")
async def get_rma(org_id: str = ORG_ID, store: str = "", market: str = "", month: int = None, year: int = None):
    """RMA reconciliation via Postgres aggregation. Buckets from per-device rows for accuracy.
    Optional month/year narrows to devices SOLD in that period (date_sold)."""
    client = sb()

    def _sold_in_period(r):
        if month is None and year is None:
            return True
        ds = r.get("date_sold")
        if not ds:
            return False
        try:
            py, pm, _ = [int(x) for x in str(ds)[:10].split("-")]
        except Exception:
            return False
        if year is not None and int(year) != py:
            return False
        if month is not None and int(month) != pm:
            return False
        return True
    # We still need per-device classification (short vs none vs full), so fetch only RMA rows.
    rows = []
    page = 0; PAGE = 1000
    while True:
        start = page * PAGE
        q = client.schema("commcalc").table("asset_ledger") \
            .select("id,store,market,esn_imei,phone_number,device_model,category,status,date_sold,owed_to_vip,reimbursement,reimbursement_date,selling_price") \
            .eq("org_id", org_id).eq("category", "RMA")
        if store:
            q = q.eq("store", store)
        if market:
            q = q.eq("market", market)
        chunk = q.range(start, start + PAGE - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 10:
            break

    rows = [r for r in rows if _sold_in_period(r)]

    buckets = {k: {"count": 0, "owed": 0.0, "reimb": 0.0, "rows": []} for k in ("full", "short", "none")}
    for r in rows:
        b, owed, reimb = _classify_rma(r)
        r["_bucket"] = b
        r["_shortfall"] = round(owed - reimb, 2) if b in ("short", "none") else 0.0
        buckets[b]["count"] += 1
        buckets[b]["owed"] += owed
        buckets[b]["reimb"] += reimb
        buckets[b]["rows"].append(r)

    for b in buckets.values():
        b["owed"] = round(b["owed"], 2)
        b["reimb"] = round(b["reimb"], 2)
        b["rows"].sort(key=lambda x: float(x.get("owed_to_vip") or 0), reverse=True)

    net_loss = round(buckets["none"]["owed"] + (buckets["short"]["owed"] - buckets["short"]["reimb"]), 2)

    # Attach VIP invoice # + date to every device row we return (in place).
    _attach_vip_invoices(client, org_id,
                         buckets["full"]["rows"] + buckets["short"]["rows"] + buckets["none"]["rows"])

    return {
        "buckets": buckets,
        "net_loss": net_loss,
        "total_rma": len(rows),
        "filters": {"store": store or None, "market": market or None},
    }


def _sync_rma_flags(client, org_id):
    """Write RMA flags: not-reimbursed=critical, short=warning. Delete-first + insert."""
    rows = _fetch_asset_rows(
        client, org_id,
        select="store,esn_imei,phone_number,device_model,category,status,date_sold,owed_to_vip,reimbursement,reimbursement_date,payg_date,acquired_date",
    )
    flags = []
    for r in rows:
        if (r.get("category") or "") != "RMA":
            continue
        b, owed, reimb = _classify_rma(r)
        if b == "full":
            continue
        sev = "critical" if b == "none" else "warning"
        d = r.get("date_sold") or r.get("payg_date") or r.get("acquired_date")
        period = "Unknown"; pm = None; py = None
        if d:
            try:
                py, pm, _ = [int(x) for x in str(d)[:10].split("-")]
                period = __import__("datetime").date(py, pm, 1).strftime("%B %Y")
            except Exception:
                pass
        shortfall = round(owed - reimb, 2)
        desc = ("RMA not reimbursed — full amount uncredited" if b == "none"
                else f"RMA short-paid — owed {owed}, reimbursed {reimb} (short {shortfall})")
        flags.append({
            "org_id": org_id, "period": period, "period_month": pm, "period_year": py,
            "flag_type": "RMA Reimbursement Gap", "source": "asset_rma",
            "severity": sev, "store_address": r.get("store"),
            "imei": r.get("esn_imei"), "mdn": r.get("phone_number"),
            "amount": shortfall, "phone_model": r.get("device_model"),
            "description": desc,
        })

    client.schema("commcalc").table("flags").delete() \
        .eq("org_id", org_id).eq("source", "asset_rma").execute()
    for i in range(0, len(flags), 500):
        client.schema("commcalc").table("flags").insert(flags[i:i+500]).execute()
    return len(flags)


@router.post("/sync-rma-flags")
async def sync_rma_flags(org_id: str = ORG_ID):
    """Manual refresh of RMA flags."""
    client = sb()
    n = _sync_rma_flags(client, org_id)
    return {"status": "ok", "rma_flags_written": n}


# ── Selling price (from sales) + undercharge flag ────────────────────────────
def _norm_imei(v):
    s = str(v or "").strip().upper()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _epay_has_data(client, org_id):
    """True iff the ePay Payment Detail Report has any rows for this org. Lets callers say
    'no payment found' (meaningful) vs stay silent (table simply not loaded)."""
    try:
        d = client.schema("commcalc").table("raw_payment_detail") \
            .select("id").eq("org_id", org_id).limit(1).execute().data or []
        return bool(d)
    except Exception:
        return False


def _epay_payments_map(client, org_id, imeis):
    """For a bounded page of asset IMEIs (asset_ledger.esn_imei), the ePay payments Boost
    made for that device, from commcalc.raw_payment_detail joined on `imei` (across ALL
    periods so the device's full payment history is captured). Returns {norm_imei: [
    {type, amount, date, period}, ...]} sorted by date. Mirrors _vip_invoice_map: queries
    raw + normalized + '.0' variants via chunked .in_() so the page stays bounded and fast."""
    keys = {_norm_imei(i) for i in imeis if i}
    if not keys:
        return {}
    candidates = set()
    for i in imeis:
        if not i:
            continue
        candidates.add(str(i).strip())   # raw
        n = _norm_imei(i)
        candidates.add(n)                # normalized
        candidates.add(n + ".0")         # in case ePay stored a trailing .0
    candidates.discard("")
    cand = list(candidates)
    out = {}
    for j in range(0, len(cand), 200):
        chunk = client.schema("commcalc").table("raw_payment_detail") \
            .select("imei,payment_type,amount,payment_date,period") \
            .eq("org_id", org_id).in_("imei", cand[j:j + 200]).execute().data or []
        for r in chunk:
            k = _norm_imei(r.get("imei"))
            if k not in keys:
                continue
            out.setdefault(k, []).append({
                "type": (r.get("payment_type") or "").strip() or "—",
                "amount": round(float(r.get("amount") or 0), 2),
                "date": (str(r.get("payment_date"))[:10] if r.get("payment_date") else None),
                "period": r.get("period"),
            })
    for k in out:
        out[k].sort(key=lambda p: (p["date"] or ""))
    return out


def _vip_invoice_map(client, org_id, imeis):
    """For a bounded page of asset IMEIs (asset_ledger.esn_imei), the VIP invoice (# + date)
    the device appears on, from commcalc.vip_invoice_devices. The asset "ESN/IMEI" column
    is what VIP stores as the device SERIAL (verified: ~99.6% of asset IMEIs match
    vip_invoice_devices.serial; the VIP `imei` column is a different identifier and matches
    almost nothing), so we join on `serial`. Keyed by normalized value. When a device is on
    more than one invoice, keeps the earliest (the original device-purchase invoice).

    Queries by raw + normalized variants via .in_() (the page is small, so this is bounded
    and fast — no full 46k-row device scan). Mirrors _imei_salesperson_map."""
    keys = {_norm_imei(i) for i in imeis if i}
    if not keys:
        return {}
    candidates = set()
    for i in imeis:
        if not i:
            continue
        candidates.add(str(i).strip())   # raw
        n = _norm_imei(i)
        candidates.add(n)                # normalized (trimmed, upper, .0 stripped)
        candidates.add(n + ".0")         # in case VIP stored the value with a trailing .0
    candidates.discard("")
    cand = list(candidates)
    out = {}  # norm_imei -> (created_on_str, invoice_number)
    for j in range(0, len(cand), 200):  # chunk .in_() to keep request URLs sane
        chunk = client.schema("commcalc").table("vip_invoice_devices") \
            .select("serial,invoice_number,created_on") \
            .eq("org_id", org_id).in_("serial", cand[j:j + 200]).execute().data or []
        for r in chunk:
            k = _norm_imei(r.get("serial"))
            if k not in keys:
                continue
            d = str(r.get("created_on") or "")
            prev = out.get(k)
            # keep the earliest invoice with a date; fall back to filling a missing date
            if prev is None or (d and (not prev[0] or d < prev[0])):
                out[k] = (d, r.get("invoice_number"))
    return {
        k: {"vip_invoice_number": v[1], "vip_invoice_date": (v[0][:10] if v[0] else None)}
        for k, v in out.items()
    }


def _attach_vip_invoices(client, org_id, rows):
    """Decorate asset rows in place with vip_invoice_number / vip_invoice_date (None if no
    matching VIP invoice). `rows` must carry esn_imei. Safe on empty / no-overlap."""
    if not rows:
        return rows
    vip_map = _vip_invoice_map(client, org_id, [r.get("esn_imei") for r in rows])
    for r in rows:
        v = vip_map.get(_norm_imei(r.get("esn_imei")))
        r["vip_invoice_number"] = v["vip_invoice_number"] if v else None
        r["vip_invoice_date"] = v["vip_invoice_date"] if v else None
    return rows


def _backfill_selling_price(client, org_id):
    """Set asset_ledger.selling_price from raw_sales (device-line Ext Price by IMEI),
    via the Postgres RPC (one UPDATE...FROM join — fast). Returns rows updated."""
    res = client.schema("commcalc").rpc(
        "backfill_asset_selling_price", {"p_org_id": org_id}).execute()
    return res.data if isinstance(res.data, int) else (res.data or 0)


def _imei_salesperson_map(client, org_id, imeis):
    """For the (few) flagged IMEIs, the rep on the priciest matching sales line."""
    want = {_norm_imei(i) for i in imeis if i}
    if not want:
        return {}
    out = {}  # imei_key -> (price, salesperson)
    page = 0
    PAGE = 1000
    while True:
        start = page * PAGE
        chunk = client.schema("commcalc").table("raw_sales") \
            .select("serial_1,ext_price,salesperson,voided,trans_type") \
            .eq("org_id", org_id).range(start, start + PAGE - 1).execute().data or []
        for r in chunk:
            if str(r.get("voided") or "").upper() == "YES" or str(r.get("trans_type") or "") == "Return":
                continue
            k = _norm_imei(r.get("serial_1"))
            if k not in want:
                continue
            try:
                p = float(r.get("ext_price") or 0)
            except Exception:
                p = 0.0
            if k not in out or p > out[k][0]:
                out[k] = (p, r.get("salesperson"))
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 200:
            break
    return {k: v[1] for k, v in out.items()}


def _sync_undercharge_flags(client, org_id):
    """Flag sold devices where cost (owed_to_vip) > reimbursement + selling_price.
    Only devices that were actually sold (have a selling_price match). Delete-first + insert."""
    rows = _fetch_asset_rows(
        client, org_id,
        select="store,esn_imei,phone_number,device_model,category,status,date_sold,"
               "owed_to_vip,reimbursement,selling_price,payg_date,acquired_date",
    )
    candidates = []
    for r in rows:
        if not r.get("esn_imei"):
            continue
        sp = r.get("selling_price")
        if sp is None:  # no matching sale → can't judge the charge
            continue
        try:
            cost = float(r.get("owed_to_vip") or 0)
            reimb = float(r.get("reimbursement") or 0)
            sell = float(sp or 0)
        except Exception:
            continue
        if cost <= 0:
            continue
        gap = round(cost - reimb - sell, 2)
        if gap > 0.01:  # undercharge / uncovered cost
            r["_gap"] = gap
            r["_cost"] = cost
            r["_reimb"] = reimb
            r["_sell"] = sell
            candidates.append(r)

    sp_map = _imei_salesperson_map(client, org_id, [r["esn_imei"] for r in candidates])

    flags = []
    for r in candidates:
        gap = r["_gap"]
        d = r.get("date_sold") or r.get("payg_date") or r.get("acquired_date")
        period = "Unknown"; pm = None; py = None
        if d:
            try:
                py, pm, _ = [int(x) for x in str(d)[:10].split("-")]
                period = __import__("datetime").date(py, pm, 1).strftime("%B %Y")
            except Exception:
                pass
        sev = "critical" if gap >= 100 else "warning"
        rep = sp_map.get(_norm_imei(r.get("esn_imei")))
        flags.append({
            "org_id": org_id, "period": period, "period_month": pm, "period_year": py,
            "flag_type": "Device Undercharge", "source": "asset_undercharge",
            "severity": sev, "store_address": r.get("store"),
            "epay_salesperson": rep,
            "imei": r.get("esn_imei"), "mdn": r.get("phone_number"),
            "amount": gap, "phone_model": r.get("device_model"),
            "description": (f"Cost {r['_cost']:.2f} > reimbursement {r['_reimb']:.2f} + "
                            f"selling price {r['_sell']:.2f} — uncovered {gap:.2f}"),
            "coaching_note": (f"This device cost {r['_cost']:.2f}. After reimbursement "
                              f"({r['_reimb']:.2f}) and the customer price ({r['_sell']:.2f}), "
                              f"{gap:.2f} of the cost was not recovered. Coach "
                              f"{rep or 'the rep'} to charge enough to cover device cost less "
                              f"reimbursement."),
        })

    client.schema("commcalc").table("flags").delete() \
        .eq("org_id", org_id).eq("source", "asset_undercharge").execute()
    for i in range(0, len(flags), 500):
        client.schema("commcalc").table("flags").insert(flags[i:i+500]).execute()
    return len(flags)


@router.post("/backfill-selling-price")
async def backfill_selling_price(org_id: str = ORG_ID):
    """Manual refresh: re-pull selling prices from sales, then re-sync undercharge flags.
    Run after uploading new sales data without re-uploading the asset file."""
    client = sb()
    updated = _backfill_selling_price(client, org_id)
    flags = _sync_undercharge_flags(client, org_id)
    return {"status": "ok", "rows_priced": updated, "undercharge_flags_written": flags}


@router.post("/sync-undercharge-flags")
async def sync_undercharge_flags(org_id: str = ORG_ID):
    """Manual refresh of undercharge flags from current selling_price values."""
    client = sb()
    n = _sync_undercharge_flags(client, org_id)
    return {"status": "ok", "undercharge_flags_written": n}


@router.get("/ledger")
async def get_asset_ledger(
    org_id: str = ORG_ID,
    status: str = "",
    category: str = "",
    search: str = "",
    limit: int = 200,
    offset: int = 0,
):
    """Paginated ledger with optional filters."""
    client = sb()
    q = client.schema("commcalc").table("asset_ledger") \
        .select("id,esn_imei,phone_number,contract_type,category,status,date_sold,sfid,owed_to_vip,on_inventory,reimbursement,commissions,total_owed,total_reimbursed,selling_price,store,market,notes") \
        .eq("org_id", org_id)

    if status:
        q = q.eq("status", status)
    if category:
        q = q.eq("category", category)
    if search:
        q = q.ilike("esn_imei", f"%{search}%")

    q = q.order("date_sold", desc=True).range(offset, offset + limit - 1)
    resp = q.execute()
    return {"rows": resp.data or [], "offset": offset, "limit": limit}


# ── Marketplace Purchases (VidaPay "MA - Marketplace Handset Fulfillment Orders") ──────────────
# OWNER REQUEST 2026-07-15: "similar to the asset landing which shows the purchases" — a purchases/
# landing-style view over the report mod-commission's report-pull engine now ingests
# (backend/app/modules/commcalc/report_pull.py) into commcalc.raw_ma_fulfillment (mig 083). This
# module reads it ONLY via the purpose-named read view commcalc.raw_ma_marketplace_orders
# (migration 207, mod-commission-owned) — never the raw table directly, so a future column/shape
# change to raw_ma_fulfillment has one seam to fix. Org-scoped throughout. No migration needed here
# (band 300-399 untouched) — this is a read-only report over an existing view.
_MP_VIEW = "raw_ma_marketplace_orders"
_MP_COLS = ("id,date_ordered,date_filled,date_shipped,order_number,order_status,order_type,"
            "tspid,business_name,business_address,city,state,zip,product_name,"
            "number_ordered,price,tracking_number")


def _mp_fetch_rows(client, org_id: str, cols: str, date_from: str = "", date_to: str = "",
                    status: str = "", order_type: str = ""):
    """Paginated, org-scoped fetch from the marketplace-orders view. SQL-level filters for the
    real columns (date range on date_ordered, order_status, order_type exact match — all three are
    picked from values this same view returns via the filter-options endpoint below, so an exact
    match always hits). `business` is deliberately NOT filtered here — it's resolved through
    _canon_store/_store_canon_map in Python by the caller, because the raw business_address text
    can vary in spelling across rows for what is really the same store (same precedent as
    _backfill_market's own resolution), so filtering by canonical name has to happen after that
    resolution, not as a raw-text SQL match.
    Returns None (not []) when the view/table doesn't exist yet (migration 207 not run, or the
    VidaPay report pull has never populated it for this org) — callers turn that into
    `available: false` instead of a 500, and the frontend shows a "run the VidaPay pull" note."""
    rows, page, PAGE = [], 0, 1000
    while True:
        start = page * PAGE
        q = client.schema("commcalc").table(_MP_VIEW).select(cols).eq("org_id", org_id)
        if date_from:
            q = q.gte("date_ordered", date_from)
        if date_to:
            q = q.lte("date_ordered", date_to)
        if status:
            q = q.eq("order_status", status)
        if order_type:
            q = q.eq("order_type", order_type)
        try:
            chunk = q.range(start, start + PAGE - 1).execute().data or []
        except Exception as e:
            if _is_missing_schema_error(e):
                return None
            raise
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        page += 1
        if page > 100:
            break
    return rows


def _mp_num(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


@router.get("/marketplace-purchases")
async def get_marketplace_purchases(org_id: str = ORG_ID, date_from: str = "", date_to: str = "",
                                     business: str = "", status: str = "", order_type: str = ""):
    """Marketplace/handset-fulfillment purchase orders — the VidaPay 'MA - Marketplace Handset
    Fulfillment Orders' report, pulled by the commission report-pull engine and read here via the
    org-scoped view commcalc.raw_ma_marketplace_orders (mig 207). Asset-landing-style: per-order
    rows (date ordered/filled/shipped, order #, status, order type, product, qty, price, tracking
    #, business/store), filterable by date range / business / status / order type. Degrades to
    `available: false` (empty rows, no error) if the view isn't there yet — pre-mig-207, or the
    report pull has never run for this org — instead of a 500, per contract §5 (a missing
    migration must never break an unrelated page)."""
    client = sb()
    raw_rows = _mp_fetch_rows(client, org_id, _MP_COLS, date_from, date_to, status, order_type)
    if raw_rows is None:
        return {"available": False, "rows": [], "count": 0,
                "totals": {"orders": 0, "qty": 0.0, "price": 0.0}, "by_status": {},
                "note": "No marketplace orders yet — run the VidaPay report pull to populate this view."}

    addr_map = _store_canon_map(client, org_id)
    rows = []
    by_status: dict = {}
    total_qty = 0.0
    total_price = 0.0
    for r in raw_rows:
        biz_addr = r.get("business_address") or ""
        canon = _canon_store(biz_addr, addr_map) if biz_addr else (r.get("business_name") or "")
        if business and canon != business:
            continue
        qty = _mp_num(r.get("number_ordered"))
        price = _mp_num(r.get("price"))
        total_qty += qty
        total_price += price
        st = r.get("order_status") or "(unknown)"
        b = by_status.setdefault(st, {"count": 0, "qty": 0.0, "price": 0.0})
        b["count"] += 1
        b["qty"] += qty
        b["price"] += price
        rows.append({**r, "store": canon})

    rows.sort(key=lambda x: (x.get("date_ordered") or ""), reverse=True)
    for _st, b in by_status.items():
        b["qty"] = round(b["qty"], 2)
        b["price"] = round(b["price"], 2)

    return {"available": True, "rows": rows, "count": len(rows),
            "totals": {"orders": len(rows), "qty": round(total_qty, 2), "price": round(total_price, 2)},
            "by_status": by_status}


@router.get("/marketplace-purchases/filter-options")
async def get_marketplace_purchases_filter_options(org_id: str = ORG_ID):
    """Distinct businesses (canonicalized through store_mapping)/statuses/order-types for the page's
    picker filters (RULE THREE — dropdown over existing values, never free text). Degrades to
    `available: false` (empty lists) if the view doesn't exist yet."""
    client = sb()
    raw_rows = _mp_fetch_rows(client, org_id, "business_name,business_address,order_status,order_type")
    if raw_rows is None:
        return {"available": False, "businesses": [], "statuses": [], "order_types": []}

    addr_map = _store_canon_map(client, org_id)
    businesses, statuses, order_types = set(), set(), set()
    for r in raw_rows:
        biz_addr = r.get("business_address") or ""
        canon = _canon_store(biz_addr, addr_map) if biz_addr else (r.get("business_name") or "")
        if canon:
            businesses.add(canon)
        if r.get("order_status"):
            statuses.add(r["order_status"])
        if r.get("order_type"):
            order_types.add(r["order_type"])
    return {"available": True, "businesses": sorted(businesses),
            "statuses": sorted(statuses), "order_types": sorted(order_types)}


# ── Purchase Orders (asset-11, mig 301) — proposed PO / receiving / sold-tally / aging ────────────
# Own file (purchase_orders.py, same package) so this router.py diff stays a 2-line mount; every PO
# endpoint lands under the SAME /api/v1/asset prefix main.py already registers — no main.py change.
from app.modules.asset.purchase_orders import router as _po_router  # noqa: E402
router.include_router(_po_router)

# ── settings-audit (2026-07-26) — admin-attention providers (no router; import-time registration
# only, mirrors the include above). See attention.py's module docstring for what each check does
# and why it's not a duplicate of the centrally-derived checks in core/import_health.py.
from app.modules.asset import attention as _asset_attention  # noqa: F401,E402

# ── On-Inventory 3-Way Rebate Recon (2026-07-28, OWNER DIRECTIVE, mig 310) — own file (same
# precedent as purchase_orders.py above) so this router.py diff stays a 2-line mount; every endpoint
# lands under the SAME /api/v1/asset prefix. Self-registers its own admin-attention provider at
# import time — see oninv_recon.py's module docstring for why that's not done via attention.py.
from app.modules.asset.oninv_recon import router as _oninv_recon_router  # noqa: E402
router.include_router(_oninv_recon_router)


