"""Account Module router — companies + store assignment (#multi-company), manual journal
entries, the compute/compute-on-demand engine, P&L, Balance Sheet, and the #10 reconciliation."""
from typing import Any
from fastapi import APIRouter, HTTPException, Header
from fastapi.concurrency import run_in_threadpool
from datetime import datetime, timezone

from app.core.database import get_supabase
from app.core.config import settings
from app.core.run_secret import verify_notify_secret
from app.core.schemas import LaxModel
from app.modules.account import coa, engine, autocompute, report_gates, statement_engine
# Settings/imports audit (2026-07-26): importing this module REGISTERS the finance domain's checks with
# platform-core's admin-attention feed (GET /core/attention). It is read-only diagnostics and is fully
# guarded internally — if core.import_health is unavailable the import is inert, so finance never breaks
# because core moved. No shared file is touched by this wiring.
try:                                              # noqa: SIM105 - deliberate belt-and-braces guard
    from app.modules.account import finance_attention  # noqa: F401
except Exception:
    pass

router = APIRouter(prefix="/account", tags=["Account"])
ORG_ID = "00000000-0000-0000-0000-000000000001"

PL_TYPES = {"revenue", "cogs", "opex", "other"}
BS_TYPES = {"asset", "liability", "equity"}


def sb():
    return get_supabase()


def require_org(org_id: str):
    if not org_id:
        raise HTTPException(400, "org_id required")


# ── companies ────────────────────────────────────────────────────────────────────────────────
@router.get("/companies")
def list_companies(org_id: str = ORG_ID):
    require_org(org_id)
    rows = (sb().schema("commcalc").table("companies").select("*")
            .eq("org_id", org_id).order("name").execute().data) or []
    return {"companies": rows}


class CompanyCreateIn(LaxModel):
    name: str = ""
    legal_name: str = ""
    ein: str = ""


class CompanyUpdateIn(LaxModel):
    name: str = ""
    legal_name: str = ""
    ein: str = ""


@router.post("/companies")
def create_company(body: CompanyCreateIn, org_id: str = ORG_ID):
    require_org(org_id)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    row = {"org_id": org_id, "name": name,
           "legal_name": (body.legal_name or name).strip(),
           "ein": (body.ein or "").strip() or None}
    res = sb().schema("commcalc").table("companies").insert(row).execute()
    return {"company": (res.data or [row])[0]}


@router.patch("/companies/{company_id}")
def update_company(company_id: str, body: CompanyUpdateIn, org_id: str = ORG_ID):
    require_org(org_id)
    sent = body.model_fields_set
    upd = {k: getattr(body, k) for k in ("name", "legal_name", "ein") if k in sent}
    if not upd:
        raise HTTPException(400, "nothing to update")
    sb().schema("commcalc").table("companies").update(upd) \
        .eq("org_id", org_id).eq("id", company_id).execute()
    return {"ok": True}


# ── stores (registry + current company assignment) ────────────────────────────────────────────
@router.get("/stores")
def list_stores(org_id: str = ORG_ID):
    """Assignable stores + each store's current company assignment. Sources, canonicalized through
    coa.store_resolver so one physical store never appears twice:
      1. store_mapping (canonical registry, with market),
      2. any already-assigned store_companies address,
      3. stores that appear in the tenant's SALES DATA (raw_sales ∪ daily_sales_feed) — so a tenant
         whose store_mapping was never populated (e.g. a fresh non-Boost tenant like luxelink) still
         sees stores to assign instead of an empty menu. (Was #3 missing → "Companies shows no stores".)"""
    require_org(org_id)
    client = sb()
    resolve = coa.store_resolver(client, org_id)
    mapping = coa._fetch_all(client, "store_mapping", "store_address,market", {"org_id": org_id})
    assigns = {(_a := (r.get("store_address") or "").strip()): r.get("company_id")
               for r in coa._fetch_all(client, "store_companies", "store_address,company_id", {"org_id": org_id})}
    companies = (client.schema("commcalc").table("companies").select("id,name")
                 .eq("org_id", org_id).execute().data) or []
    co_name = {c["id"]: c["name"] for c in companies}
    seen, out = set(), []

    def _push(sa, market, source):
        sa = (sa or "").strip()
        if not sa or sa in seen:
            return
        seen.add(sa)
        cid = assigns.get(sa)
        out.append({"store_address": sa, "market": market,
                    "company_id": cid, "company_name": co_name.get(cid), "source": source})

    for r in mapping:
        _push(r.get("store_address"), r.get("market"), "store_mapping")
    for sa in assigns:
        _push(sa, None, "assigned")
    # sales-derived stores (canonicalized) — collapses onto a mapped address when the resolver knows it,
    # so a mapped store is never duplicated; a genuinely-unmapped store is added under its raw name.
    for tbl in ("raw_sales", "daily_sales_feed"):
        try:
            for r in coa._fetch_all(client, tbl, "store", {"org_id": org_id}, cap=60000):
                _push(resolve(coa._norm_store(r.get("store"))), None, "sales")
        except Exception:
            pass
    out.sort(key=lambda x: x["store_address"])
    return {"stores": out, "companies": companies}


class AssignStoresIn(LaxModel):
    assignments: list = []


@router.post("/companies/assign")
def assign_stores(body: AssignStoresIn, org_id: str = ORG_ID):
    """Body: {assignments:[{store_address, company_id}]}. Upserts the store→company map."""
    require_org(org_id)
    rows = body.assignments or []
    client = sb()
    saved = 0
    for r in rows:
        sa = (r.get("store_address") or "").strip()
        if not sa:
            continue
        rec = {"org_id": org_id, "store_address": sa, "company_id": r.get("company_id") or None,
               "updated_at": datetime.now(timezone.utc).isoformat()}
        client.schema("commcalc").table("store_companies").delete() \
            .eq("org_id", org_id).eq("store_address", sa).execute()
        client.schema("commcalc").table("store_companies").insert(rec).execute()
        saved += 1
    return {"saved": saved}


# ── manual journal entries ──────────────────────────────────────────────────────────────────
@router.get("/journal/{period}")
def get_journal(period: str, org_id: str = ORG_ID):
    require_org(org_id)
    rows = (sb().schema("commcalc").table("journal_entries").select("*")
            .eq("org_id", org_id).eq("period", period).order("statement").execute().data) or []
    return {"period": period, "entries": rows,
            "account_types": {"pl": sorted(PL_TYPES), "balance_sheet": sorted(BS_TYPES)}}


class PutJournalIn(LaxModel):
    rows: Any = None


@router.put("/journal/{period}")
def put_journal(period: str, body: PutJournalIn, org_id: str = ORG_ID):
    """Replace all journal entries for the period. Body: {rows:[{statement, account_type,
    account_line, amount, company_id?, store_address?, entry_date?, memo?}]}."""
    require_org(org_id)
    pm, py = coa.parse_period(period)
    rows = body.rows or []
    client = sb()
    ins, rejected = [], []
    for r in rows:
        statement = (r.get("statement") or "").strip()
        atype = (r.get("account_type") or "").strip()
        line = (r.get("account_line") or "").strip()
        try:
            amt = float(r.get("amount") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        # An entered amount that silently vanishes is a defect (owner report 2026-09-02) — a row
        # this endpoint cannot accept is now REPORTED back with its reason, never dropped mutely.
        if statement not in ("pl", "balance_sheet") or not line:
            rejected.append({"account_line": line or "(blank)",
                             "reason": "missing account line" if not line
                             else f"unknown statement '{statement}'"})
            continue
        if statement == "pl" and atype not in PL_TYPES:
            rejected.append({"account_line": line, "reason": f"P&L type must be one of {sorted(PL_TYPES)}"})
            continue
        if statement == "balance_sheet" and atype not in BS_TYPES:
            rejected.append({"account_line": line,
                             "reason": f"Balance-sheet type must be one of {sorted(BS_TYPES)}"})
            continue
        ins.append({"org_id": org_id, "period": period, "period_month": pm, "period_year": py,
                    "company_id": r.get("company_id") or None,
                    "store_address": (r.get("store_address") or "").strip() or None,
                    "entry_date": (r.get("entry_date") or None),
                    "statement": statement, "account_type": atype, "account_line": line,
                    "amount": amt, "memo": (r.get("memo") or "").strip() or None})
    client.schema("commcalc").table("journal_entries").delete() \
        .eq("org_id", org_id).eq("period", period).execute()
    for i in range(0, len(ins), 500):
        client.schema("commcalc").table("journal_entries").insert(ins[i:i + 500]).execute()
    # Advisory: which entries resolved to a company (saved company_id, or the typed designation in
    # the store/memo text — balance_sheet.journal_company_matcher). Display-only; the statements
    # apply the same matcher at assembly time, so this just lets the UI confirm the attribution.
    resolved = []
    try:
        from app.modules.account import balance_sheet as _bs
        companies = (client.schema("commcalc").table("companies").select("id,name")
                     .eq("org_id", org_id).execute().data) or []
        co_name = {c["id"]: c["name"] for c in companies}
        matcher = _bs.journal_company_matcher(companies)
        for e in ins:
            cid = _bs.entry_company(e, matcher)
            resolved.append({"account_line": e["account_line"], "amount": e["amount"],
                            "company": co_name.get(cid)})
    except Exception:
        resolved = []
    return {"saved": len(ins), "period": period, "rejected": rejected, "resolved": resolved}


# ── editable inventory value (real-time b2bsoft Inventory Aging → Balance Sheet) ───────────────
@router.get("/inventory-values")
def list_inventory_values(org_id: str = ORG_ID):
    """Per-store inventory value for the Balance Sheet: the swept value (b2bsoft Inventory
    Aging), an optional manual override, and the effective value used on the BS
    (manual_value if set, else swept_value). Lists every canonical store so any can be edited.
    Also returns the b2bsoft sweep status (no secrets)."""
    require_org(org_id)
    client = sb()
    resolve = coa.store_resolver(client, org_id)
    by_store = {}
    try:
        for r in coa._fetch_all(client, "inventory_value",
                                "store,swept_value,manual_value,as_of_date,source,note",
                                {"org_id": org_id}):
            st = resolve(coa._norm_store(r.get("store"))) or coa._norm_store(r.get("store"))
            if not st:
                continue
            by_store[st] = {"store": st, "swept_value": r.get("swept_value"),
                            "manual_value": r.get("manual_value"), "as_of_date": r.get("as_of_date"),
                            "source": r.get("source"), "note": r.get("note")}
    except Exception:
        pass
    # include every canonical store (so the grid lists them all, even with no value yet)
    for m in coa._fetch_all(client, "store_mapping", "store_address", {"org_id": org_id}):
        sa = coa._norm_store(m.get("store_address"))
        if sa and sa not in by_store:
            by_store[sa] = {"store": sa, "swept_value": None, "manual_value": None,
                            "as_of_date": None, "source": None, "note": None}
    out = []
    for v in by_store.values():
        mv, sv = v["manual_value"], v["swept_value"]
        v["effective"] = mv if mv is not None else sv
        v["effective_source"] = "manual" if mv is not None else ("b2bsoft" if sv is not None else None)
        out.append(v)
    out.sort(key=lambda x: x["store"])

    sweep = None
    try:
        cfg = (client.schema("commcalc").table("b2b_sweep_config")
               .select("enabled,frequency,hour,timezone,last_run_at,last_status,last_detail,"
                       "next_run_at,portal_user").eq("org_id", org_id).execute().data or [None])[0]
        if cfg:
            sweep = {k: cfg.get(k) for k in ("enabled", "frequency", "hour", "timezone",
                                             "last_run_at", "last_status", "last_detail", "next_run_at")}
            sweep["has_credentials"] = bool(cfg.get("portal_user"))
    except Exception:
        pass
    return {"rows": out, "sweep": sweep,
            "total_effective": round(sum(coa.safe_float(r["effective"]) for r in out), 2)}


class PutInventoryValuesIn(LaxModel):
    rows: Any = None


@router.put("/inventory-values")
def put_inventory_values(body: PutInventoryValuesIn, org_id: str = ORG_ID):
    """Set/clear the manual inventory override per store. Body: {rows:[{store, manual_value,
    note?}]}. manual_value null/'' clears the override (the swept value then drives the BS).
    Never touches swept_value. Re-compute statements to apply to a stored Balance Sheet."""
    require_org(org_id)
    client = sb()
    saved = 0
    for r in (body.rows or []):
        st = coa._norm_store(r.get("store"))
        if not st:
            continue
        mv = r.get("manual_value")
        if mv in (None, "", "null"):
            mv = None
        else:
            try:
                mv = round(float(mv), 2)
            except (TypeError, ValueError):
                continue
        rec = {"org_id": org_id, "store": st, "manual_value": mv,
               "note": (r.get("note") or "").strip() or None,
               "updated_at": datetime.now(timezone.utc).isoformat()}
        client.schema("commcalc").table("inventory_value").upsert(rec, on_conflict="org_id,store").execute()
        saved += 1
    return {"saved": saved}


# ── per-org accounting config (booking rates — mig 611) ────────────────────────────────────────
@router.get("/config")
def get_config(org_id: str = ORG_ID):
    """This tenant's finance/accounting config (currently the accessory COGS %). Returns the resolved
    values with the historical Boost defaults filled in, so a tenant with no saved row reads the same
    0.20 the code used to hard-code. `is_default` tells the UI whether a row has been explicitly saved."""
    require_org(org_id)
    cfg = coa._account_config(sb(), org_id)
    saved = False
    try:
        saved = bool((sb().schema("commcalc").table("account_config").select("org_id")
                      .eq("org_id", org_id).limit(1).execute().data))
    except Exception:
        saved = False
    # RULE THREE (pick-don't-type): the service-fee picker offers the product descriptions this tenant's
    # OWN sales actually carry — never a typed string, never a hard-coded vendor name. Capped and
    # frequency-ordered so the list stays usable; failure degrades to an empty option list (the saved
    # picks still render), never a 500 on a settings read.
    options = []
    try:
        from collections import Counter as _C
        seen = _C()
        for t in ("raw_sales", "daily_sales_feed"):
            try:
                for r in (sb().schema("commcalc").table(t).select("product_desc")
                          .eq("org_id", org_id).limit(100000).execute().data) or []:
                    p = str(r.get("product_desc") or "").strip()
                    if p:
                        seen[p] += 1
            except Exception:
                continue
        options = [p for p, _ in seen.most_common(400)]
    except Exception:
        options = []
    # RULE THREE for the PAYROLL-NAME picker too (mig 621, owner ruling K2): the options are the
    # tenant's OWN `store_expenses.expense_name` values, so a name can never be mistyped into a list
    # that then silently matches nothing. Frequency-ordered; a failure degrades to an empty option
    # list (saved picks still render) rather than a 500 on a settings read.
    payroll_options = []
    try:
        from collections import Counter as _C2
        seen2 = _C2()
        for r in (sb().schema("commcalc").table("store_expenses").select("expense_name")
                  .eq("org_id", org_id).limit(100000).execute().data) or []:
            n = str(r.get("expense_name") or "").strip()
            if n:
                seen2[n] += 1
        payroll_options = [n for n, _ in seen2.most_common(300)]
    except Exception:
        payroll_options = []
    return {"org_id": org_id,
            "config": {**cfg,
                       "service_fee_products": cfg["service_fee_products_list"],
                       "payroll_expense_names": cfg["payroll_expense_names_list"]},
            "is_default": not saved,
            "service_fee_product_options": options,
            "payroll_expense_name_options": payroll_options,
            "defaults": {"accessory_cogs_pct": coa.ACCESSORY_COGS_PCT, "service_fee_products": [],
                         "payroll_expense_names": [], "payroll_expense_routes": {},
                         "device_cogs_mode": "off"}}


class AccountPutConfigIn(LaxModel):
    accessory_cogs_pct: Any = None
    service_fee_products: Any = None
    payroll_expense_names: Any = None
    payroll_expense_routes: Any = None
    device_cogs_mode: Any = None


@router.put("/config")
def put_config(body: AccountPutConfigIn, org_id: str = ORG_ID):
    """Set this tenant's finance/accounting config. Body may carry either knob, independently:
      accessory_cogs_pct    0..1 — accessory COGS as a fraction of gross accessory sales.
      service_fee_products  [str] — sale-line products that are FEE INCOME to the store (mig 613),
                            booked to the `service_income` revenue line at full price with no COGS.
      payroll_expense_names [str] — expense names that ARE payroll (mig 621, ruling K2). Any such row
                            makes payroll authoritative and SUPPRESSES the shifts x rate estimate.
      payroll_expense_routes {name: 'wages'|'payroll_expenses'} — optional per-name line override.
                            Moves a dollar between two OPEX lines; never changes net income.
      device_cogs_mode      'off'|'auto'|'invoice'|'pos' (mig 621, ruling K3). 'off' = POS-only
                            (default, pre-621). 'auto' = invoice-first with a POS fallback.
                            'invoice' = never fall back to POS (POS cost on a subsidised handset is
                            NEGATIVE). See device_cogs.py.
    A key that is ABSENT is left untouched. MONEY-TOUCHING — both move reported revenue / Gross Profit;
    RECOMPUTE the period statements afterward for either to take effect on the stored P&L."""
    require_org(org_id)
    cur = coa._account_config(sb(), org_id)
    row = {"org_id": org_id, "updated_at": datetime.now(timezone.utc).isoformat()}
    # PARTIAL SAVE: each knob is written only when the body actually carries it, so the service-fee
    # picker cannot silently rewrite the accessory COGS % (and vice-versa). An absent key keeps the
    # tenant's current value; this endpoint used to REQUIRE accessory_cogs_pct on every call.
    if "accessory_cogs_pct" in body.model_fields_set:
        try:
            pct = float(body.accessory_cogs_pct)
        except (TypeError, ValueError):
            raise HTTPException(400, "accessory_cogs_pct must be a number between 0 and 1")
        if not (0 <= pct <= 1):
            raise HTTPException(400, "accessory_cogs_pct must be between 0 and 1 (e.g. 0.20 = 20%)")
        row["accessory_cogs_pct"] = round(pct, 6)
    else:
        row["accessory_cogs_pct"] = round(float(cur["accessory_cogs_pct"]), 6)
    if "service_fee_products" in body.model_fields_set:
        raw = body.service_fee_products or []
        if not isinstance(raw, list):
            raise HTTPException(400, "service_fee_products must be a list of product descriptions")
        # de-duplicate case-insensitively but KEEP the observed spelling (it is what the picker shows)
        picked, seen = [], set()
        for p in raw:
            s = str(p or "").strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                picked.append(s)
        row["service_fee_products"] = picked
    # ── OWNER RULING K2 (mig 621) — which expense names ARE payroll ─────────────────────────────
    # Listing a name makes payroll AUTHORITATIVE for the period and SUPPRESSES the StoreOps
    # shifts×rate estimate. That is the whole point: luxelink keys payroll by hand, so it was getting
    # a $145,358.27 estimate ON TOP of $108,430.59 of real salary rows.
    if "payroll_expense_names" in body.model_fields_set:
        raw = body.payroll_expense_names or []
        if not isinstance(raw, list):
            raise HTTPException(400, "payroll_expense_names must be a list of expense names")
        picked, seen = [], set()
        for n in raw:
            s = str(n or "").strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                picked.append(s)
        row["payroll_expense_names"] = picked
    if "payroll_expense_routes" in body.model_fields_set:
        raw = body.payroll_expense_routes or {}
        if not isinstance(raw, dict):
            raise HTTPException(400, "payroll_expense_routes must be a {expense name: line} object")
        routes = {}
        for k, v in raw.items():
            name, lk = str(k or "").strip(), str(v or "").strip()
            if not name:
                continue
            if lk not in ("wages", "payroll_expenses"):
                raise HTTPException(
                    400, f"payroll_expense_routes['{name}'] must be 'wages' or 'payroll_expenses'")
            routes[name] = lk
        row["payroll_expense_routes"] = routes
    # ── OWNER RULING K3 (mig 621) — device COGS recognition mode ────────────────────────────────
    if "device_cogs_mode" in body.model_fields_set:
        mode = str(body.device_cogs_mode or "").strip()
        if mode not in ("off", "auto", "invoice", "pos"):
            raise HTTPException(400, "device_cogs_mode must be one of: off, auto, invoice, pos")
        row["device_cogs_mode"] = mode
    try:
        sb().schema("commcalc").table("account_config").upsert(row, on_conflict="org_id").execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 611/621 first: {e}")
    _new = coa._account_config(sb(), org_id)
    return {"ok": True, "org_id": org_id,
            "config": {**_new,
                       "service_fee_products": _new["service_fee_products_list"],
                       "payroll_expense_names": _new["payroll_expense_names_list"]}}


# ── compute (build + persist all snapshots) ───────────────────────────────────────────────────
@router.post("/compute/{period}")
async def compute(period: str, org_id: str = ORG_ID):
    require_org(org_id)
    try:
        # SEV-1 2026-07-30 — compute_and_store() is SYNCHRONOUS and blocking end to end (dozens of
        # Supabase round-trips plus, when ANTHROPIC_API_KEY is set, one Claude narrative call). Called
        # directly from this `async def` it ran ON the single uvicorn event loop, so a slow compute
        # stalled EVERY endpoint; with the SDK's 600s x 2-retry default that was a ~30-minute
        # platform-wide freeze from one request (same failure that hit /helpdesk/ai-assist).
        # run_in_threadpool moves the identical sync code to a worker thread: same calls, same order,
        # same exceptions, byte-identical numbers — the loop just stays free to serve other requests.
        # 2026-09-02 (owner balance-sheet defects): compute now runs through
        # statement_engine.compute_and_store — the same deterministic assembly PLUS the
        # balance-sheet truths (handset payables, inventory basis, fixed journal company scoping,
        # dual-spelling journal read) and a stored Cash Flow. engine.compute_and_store remains
        # untouched for compatibility; defaults are byte-identical (harness_statement_engine.py).
        return await run_in_threadpool(statement_engine.compute_and_store, sb(), org_id, period)
    except Exception as e:
        raise HTTPException(500, f"compute failed: {type(e).__name__}: {e}")


# ── scheduled auto-recompute (called by Supabase pg_cron via pg_net) ───────────────────────────
@router.post("/run-due")
async def run_due(x_notify_secret: str = Header(default=""), only_org: str = "", force: bool = False):
    """Recompute the current + prior period statements for every tenant with account data, but only
    where STALE (never computed, or a fresh upload landed since) — so a new tenant's books stop
    reading {"computed": false} and existing tenants' statements track their own uploads. Idempotent
    and cheap on a quiet tick. Secret-guarded (same x-notify-secret / NOTIFY_RUN_SECRET as
    /notify/run-due); each tenant runs under core.run_for_tenant (money_scope="none"). `only_org`
    targets a single tenant; `force=true` recomputes regardless of staleness. This changes WHEN
    compute runs, never WHAT it computes."""
    if not verify_notify_secret(x_notify_secret):
        raise HTTPException(403, "forbidden")
    # SEV-1 2026-07-30 — same reason as /compute, and worse here: this sweep walks EVERY tenant x 2
    # periods, so it is N x (all the blocking Supabase work + one Claude narrative). On the event loop
    # a single slow tick froze the whole backend. Off to a worker thread; the sweep itself is
    # unchanged (still sequential, still one tenant at a time, still WHEN not WHAT).
    return await run_in_threadpool(autocompute.recompute_due, sb(),
                                   only_org=(only_org or None), force=force)


# ── read snapshots ────────────────────────────────────────────────────────────────────────────
def _read(period, st_type, scope, org_id):
    rows = (sb().schema("commcalc").table("account_statements").select("*")
            .eq("org_id", org_id).eq("period", period).eq("statement_type", st_type)
            .eq("scope_key", scope).execute().data) or []
    return rows[0] if rows else None


def _filtered_read(period, st_type, scope, stores, markets, org_id):
    """RULE FIVE (§3d) store/market filter for the AGGREGATED statements: re-attribute the P&L / BS
    to the selected store(s) by SUMMING the per-store snapshots (read-only; no money recompute). Only
    reached when a store OR market filter is active — with no filter the caller returns the stored
    snapshot byte-for-byte (behaviour unchanged). Company-wide lines read $0 here (documented note)."""
    from app.modules.account import statement_filter
    base = _read(period, st_type, "consolidated", org_id)   # for staleness + line skeleton
    stale = autocompute.staleness(sb(), org_id, period,
                                  computed_at=(base.get("computed_at") if base else None))
    if not base:
        return {"period": period, "scope": "filtered", "computed": False, "filtered": True, **stale}
    f = statement_filter.filtered_statement(sb(), org_id, period, st_type, scope, stores, markets)
    return {"period": period, "computed": True, "narrative": None, "model": None,
            "crosscheck_ok": None, **f, **stale}


@router.get("/pl/{period}")
async def get_pl(period: str, scope: str = "consolidated", stores: str = "", markets: str = "",
                 org_id: str = ORG_ID):
    require_org(org_id)
    if (stores or "").strip() or (markets or "").strip():
        return _filtered_read(period, "pl", scope, stores, markets, org_id)
    row = _read(period, "pl", scope, org_id)
    # Staleness banner: computed_at + the newest relevant ingest so the page can prompt a recompute
    # when a fresh upload has landed since (or the books were never computed). Never changes numbers.
    stale = autocompute.staleness(sb(), org_id, period, computed_at=(row.get("computed_at") if row else None))
    if not row:
        return {"period": period, "scope": scope, "computed": False, **stale}
    return {"period": period, "scope": scope, "computed": True,
            "statement": row["payload"], "narrative": row.get("narrative"),
            "model": row.get("model"), "crosscheck_ok": row.get("crosscheck_ok"), **stale}


@router.get("/balance-sheet/{period}")
async def get_bs(period: str, scope: str = "consolidated", stores: str = "", markets: str = "",
                 org_id: str = ORG_ID):
    require_org(org_id)
    if (stores or "").strip() or (markets or "").strip():
        return _filtered_read(period, "balance_sheet", scope, stores, markets, org_id)
    row = _read(period, "balance_sheet", scope, org_id)
    stale = autocompute.staleness(sb(), org_id, period, computed_at=(row.get("computed_at") if row else None))
    if not row:
        return {"period": period, "scope": scope, "computed": False, **stale}
    return {"period": period, "scope": scope, "computed": True,
            "statement": row["payload"], "narrative": row.get("narrative"),
            "model": row.get("model"), "crosscheck_ok": row.get("crosscheck_ok"), **stale}


@router.get("/cash-flow/{period}")
async def get_cf(period: str, scope: str = "consolidated", org_id: str = ORG_ID):
    """The stored derived Cash Flow snapshot (statement_type 'cash_flow', written by
    statement_engine.compute_and_store alongside the P&L / Balance Sheet). Not computed yet ⇒
    {"computed": false} with the staleness block, same contract as the other statement reads."""
    require_org(org_id)
    row = _read(period, "cash_flow", scope, org_id)
    stale = autocompute.staleness(sb(), org_id, period, computed_at=(row.get("computed_at") if row else None))
    if not row:
        return {"period": period, "scope": scope, "computed": False, **stale}
    return {"period": period, "scope": scope, "computed": True,
            "statement": row["payload"], "model": row.get("model"), **stale}


@router.get("/statement/{period}")
async def on_demand_statement(period: str, scope: str = "consolidated",
                              kinds: str = "pl,balance_sheet,cash_flow", org_id: str = ORG_ID):
    """THE on-demand financial-statement service (owner directive 2026-09-02): a FRESH P&L /
    Balance Sheet / Cash Flow for ANY org, period and scope, computed NOW from the live bookings —
    no snapshot required, nothing persisted. `kinds` narrows the payload (comma-separated).
    Platform-wide: other modules and the notify report registry call the same
    statement_engine.statement() this endpoint fronts. Org-scoped, fail-closed on unknown scopes."""
    require_org(org_id)
    ks = tuple(k.strip() for k in (kinds or "").split(",")
               if k.strip() in ("pl", "balance_sheet", "cash_flow")) or ("pl", "balance_sheet", "cash_flow")
    try:
        # Same SEV-1 worker-thread rule as /compute: statement_engine does bulk Supabase reads.
        return await run_in_threadpool(statement_engine.statement, sb(), org_id, period, scope, ks)
    except Exception as e:
        raise HTTPException(500, f"on-demand statement failed: {type(e).__name__}: {e}")


@router.get("/inventory-recon")
async def inventory_recon(org_id: str = ORG_ID):
    """Balance-sheet inventory tie-out (owner defect #1, 2026-09-02): per store, the emailed
    Inventory Aging report totals (inventory_value.swept_value) vs the unsold-phone device ledger
    (inventory_aging_device at each store's current snapshot) vs manual overrides vs the effective
    BS value under the org's configured basis — with the unplaced/superseded device counts so a
    ghost row can never hide. Read-only; feeds the reconciliation tab."""
    require_org(org_id)
    try:
        return await run_in_threadpool(statement_engine.inventory_reconciliation, sb(), org_id)
    except Exception as e:
        raise HTTPException(500, f"inventory recon failed: {type(e).__name__}: {e}")


@router.get("/overview/{period}")
def overview(period: str, org_id: str = ORG_ID):
    """Headline numbers + the list of computed scopes for the dashboard + filter dropdowns."""
    require_org(org_id)
    rows = (sb().schema("commcalc").table("account_statements")
            .select("statement_type,scope_key,scope_label,payload,crosscheck_ok,computed_at,model")
            .eq("org_id", org_id).eq("period", period).execute().data) or []
    companies = (sb().schema("commcalc").table("companies").select("id,name")
                 .eq("org_id", org_id).order("name").execute().data) or []
    scopes = {}
    for r in rows:
        sk = r["scope_key"]
        s = scopes.setdefault(sk, {"scope_key": sk, "scope_label": r.get("scope_label")})
        p = r.get("payload") or {}
        if r["statement_type"] == "pl":
            s["revenue"] = sum(sec["subtotal"] for sec in p.get("sections", []) if sec["type"] == "revenue")
            s["gross_profit"] = p.get("gross_profit")
            s["net_income"] = p.get("net_income")
        elif r["statement_type"] == "balance_sheet":
            s["assets"] = p.get("assets_total")
            s["balanced"] = p.get("balanced")
        elif r["statement_type"] == "cash_flow":
            # additive since 2026-09-02 — must never clobber the BS columns above
            s["cash_flow_tied"] = p.get("tied")
        s["computed_at"] = r.get("computed_at")
        s["model"] = r.get("model")
    return {"period": period, "computed": bool(rows), "companies": companies,
            "scopes": sorted(scopes.values(),
                             key=lambda x: (0 if x["scope_key"] == "consolidated"
                                            else 1 if x["scope_key"].startswith("company:") else 2,
                                            x.get("scope_label") or ""))}


# ── Narrative banner (owner 2026-08-29 modernization track) ──────────────────────────────────────
# A deterministic plain-English summary of the consolidated P&L vs the prior month, computed from the SAME
# `account_statements` the dashboard reads (no LLM, no API key — this is separate from the optional Claude
# statement-narrative engine and always works). Reuses the shared <NarrativeBanner> component on the
# frontend. Never raises → {available: false} hides the banner.
_ACC_MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def _acc_pct_change(cur, prior):
    try:
        c, p = float(cur or 0), float(prior or 0)
    except (TypeError, ValueError):
        return None
    return None if p == 0 else round((c - p) / p * 100.0, 1)


def _acc_money(v):
    """'$1,234' / '-$1,234' — a signed dollar figure with no cents (statements headline in whole dollars)."""
    v = float(v or 0)
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


def _consolidated_pl(client, org_id, period):
    """The consolidated P&L headline numbers for `period` from account_statements, or None when that period
    has no computed consolidated P&L. Matches BOTH period spellings via `period_keys` (the finance-wide
    month-name/numeric duality)."""
    from app.modules.account._period import period_keys
    rows = (client.schema("commcalc").table("account_statements")
            .select("payload")
            .eq("org_id", org_id).in_("period", list(period_keys(period)))
            .eq("scope_key", "consolidated").eq("statement_type", "pl").execute().data) or []
    if not rows:
        return None
    pl = rows[0].get("payload") or {}
    rev = sum(sec.get("subtotal", 0) for sec in pl.get("sections", []) if sec.get("type") == "revenue")
    return {"revenue": rev, "gross_profit": pl.get("gross_profit") or 0, "net_income": pl.get("net_income") or 0}


def _account_narrative(client, org_id, period):
    try:
        from app.modules.account._period import parse_period
        m, y = parse_period(period)
        if not (1 <= m <= 12 and y):
            return {"period": period, "available": False}
        pm, py = (12, y - 1) if m == 1 else (m - 1, y)
        prior_label = f"{_ACC_MONTHS[pm]} {py}"

        cur = _consolidated_pl(client, org_id, period)
        if not cur:
            return {"period": period, "available": False}
        prior = _consolidated_pl(client, org_id, prior_label)

        ni, rev, gp = cur["net_income"], cur["revenue"], cur["gross_profit"]
        margin = round(gp / rev * 100, 1) if rev else None
        cur_txt = (f"net income of {_acc_money(ni)}" if ni >= 0 else f"a net loss of ${abs(ni):,.0f}")

        if not prior:                                      # first computed month — no comparison
            head = f"Consolidated {cur_txt} in {period} on {_acc_money(rev)} revenue."
            bullets = [f"Gross profit {_acc_money(gp)}" + (f" ({margin:.0f}% margin)." if margin is not None else ".")]
            return {"period": period, "available": True, "comparative": False, "tone": "flat",
                    "headline": head, "bullets": bullets,
                    "facts": {"net_income": ni, "revenue": rev, "gross_profit": gp, "margin": margin}}

        pni, prev, pgp = prior["net_income"], prior["revenue"], prior["gross_profit"]
        delta = ni - pni
        tone = "up" if delta > 0 else "down" if delta < 0 else "flat"
        phrase = "ahead of" if delta > 0 else "behind" if delta < 0 else "level with"
        # A percentage is only meaningful when the prior base is positive and same-signed; otherwise state
        # the direction and both figures rather than a misleading percent off a negative base.
        ni_pct = _acc_pct_change(ni, pni) if (pni > 0 and ni >= 0) else None
        pct_txt = f"{abs(ni_pct):.0f}% " if ni_pct is not None else ""
        head = f"Consolidated {cur_txt} in {period} — {pct_txt}{phrase} last month ({_acc_money(pni)})."

        bullets = []
        rev_pct = _acc_pct_change(rev, prev)
        rev_dir = "up" if rev - prev > 0 else "down"
        bullets.append(f"Revenue {_acc_money(rev)}"
                       + (f" ({abs(rev_pct):.0f}% {rev_dir} vs last month)." if rev_pct is not None else "."))
        gp_pct = _acc_pct_change(gp, pgp)
        gp_dir = "up" if gp - pgp > 0 else "down"
        bullets.append(f"Gross profit {_acc_money(gp)}"
                       + (f" ({margin:.0f}% margin," if margin is not None else " (")
                       + (f" {abs(gp_pct):.0f}% {gp_dir} vs last month)." if gp_pct is not None else " no prior comparison)."))

        return {"period": period, "prior_period": prior_label, "available": True, "comparative": True,
                "tone": tone, "headline": head, "bullets": bullets,
                "facts": {"net_income": ni, "prior_net_income": pni, "revenue": rev, "prior_revenue": prev,
                          "gross_profit": gp, "margin": margin, "net_income_pct": ni_pct, "revenue_pct": rev_pct}}
    except Exception:
        return {"period": period, "available": False}


@router.get("/overview/{period}/narrative")
def overview_narrative(period: str, org_id: str = ORG_ID):
    """Plain-English summary of the consolidated P&L for `period` vs the prior month — net income headline,
    revenue and gross-profit / margin trend. Computed from the SAME `account_statements` the dashboard
    reads, so it can never disagree with the figures. DISPLAY-ONLY; hidden until statements are computed."""
    require_org(org_id)
    return _account_narrative(sb(), org_id, period)


# ── #10 reconciliation (VIP credit-memo residual vs MI + ATU) ────────────────────────────────
@router.get("/recon/{period}")
async def get_recon(period: str, tolerance: float = 1.0, date_col: str = "mi_activation_date",
                    analyze: bool = False, org_id: str = ORG_ID):
    require_org(org_id)
    from app.modules.account import recon
    # SEV-1 2026-07-30 — reconcile() is blocking (bulk Supabase reads; with analyze=true also one
    # Claude missed-days call). On the event loop, `?analyze=true` could freeze the whole backend for
    # up to ~30 min. Worker thread: identical sync code, identical output.
    return await run_in_threadpool(recon.reconcile, sb(), org_id, period, tolerance, date_col,
                                   analyze=analyze)


@router.post("/recon/{period}/sync-flags")
def sync_recon_flags(period: str, tolerance: float = 1.0, date_col: str = "mi_activation_date",
                           org_id: str = ORG_ID):
    require_org(org_id)
    from app.modules.account import recon
    return recon.sync_flags(sb(), org_id, period, tolerance, date_col)


@router.get("/credit-memos/{period}")
def get_credit_memos(period: str, org_id: str = ORG_ID):
    require_org(org_id)
    rows = (sb().schema("commcalc").table("vip_credit_memos").select("*")
            .eq("org_id", org_id).eq("period", period).order("created_on").execute().data) or []
    return {"period": period, "credit_memos": rows, "count": len(rows)}


@router.post("/credit-memos/sweep")
def sweep_credit_memos(org_id: str = ORG_ID):
    """On-demand scrape of VIP credit memos (Weekly Incentive Credit) using the VIP sweep
    credentials already stored in commcalc.vip_sweep_config (backend-only). Auto-scheduling
    alongside the invoice sweep is a documented fast-follow."""
    require_org(org_id)
    client = sb()
    cfg = (client.schema("commcalc").table("vip_sweep_config").select("portal_user,portal_pass")
           .eq("org_id", org_id).execute().data or [None])[0]
    if not cfg or not cfg.get("portal_user") or not cfg.get("portal_pass"):
        raise HTTPException(400, "Distributor portal credentials not set — configure them on the Distributor sweep page first.")
    try:
        from app.modules.commcalc import vip_sweep
        from app.modules.commcalc.router import _vip_money, _vip_int, _vip_ts, _vip_period
        helpers = (_vip_money, _vip_int, _vip_ts, _vip_period)
        return vip_sweep.run_creditmemo_sweep(client, org_id, cfg["portal_user"], cfg["portal_pass"], helpers)
    except Exception as e:
        raise HTTPException(500, f"credit-memo sweep failed: {type(e).__name__}: {e}")


# ── residual per subscriber (MI+ATU) per store, month over month + commission overlay ─────────
@router.get("/residual-per-sub")
def residual_per_sub(months: int = 6, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Residual (MI+ATU) per SUBSCRIBER per store, month over month, with each month's commission
    (rep_commissions.total_payout) alongside it — built to show the effect of lower commissions on the
    residual payout over time. Store/market filtering is client-side, so every store's monthly series
    is returned. Aggregated in Postgres via commcalc.residual_per_sub_by_store (falls back to a bounded
    Python aggregation until migration 101 is run).

    PERMISSION (owner directive 2026-07-29): DEFAULT-CLOSED. This one endpoint serves BOTH gated
    reports — the Residual per Subscriber page AND the residual/commission series on the Trends hub —
    so it accepts EITHER grant ('residual_per_sub' or 'account_trends'); a Trends grantee already sees
    this series on their own page. Super-admins / scope-'all' roles / role 'admin' always pass; anyone
    else needs an explicit per-role grant. Resolution failure degrades CLOSED (403), never open.
    Note: `residual_subs.compute` itself is NOT gated — commcalc's What-If simulator calls it directly
    and carries its own carrier_residual gate (cross-module note in the finance handoff)."""
    require_org(org_id)
    report_gates.require_report_grant(
        authorization, (report_gates.RESIDUAL_PER_SUB, report_gates.ACCOUNT_TRENDS),
        report="Residual per Subscriber")
    from app.modules.account import residual_subs
    return residual_subs.compute(sb(), org_id, months=max(1, min(int(months or 6), 36)))


# ── health ──────────────────────────────────────────────────────────────────────────────────
@router.get("/health")
def health():
    return {"ok": True, "engine_configured": bool(settings.ANTHROPIC_API_KEY),
            "model": settings.ACCOUNT_ENGINE_MODEL}
