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
    rows = coa.org_companies(sb(), org_id, cols="*")   # canonical entity enumeration (fail closed)
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
    companies = coa.org_companies(client, org_id)   # canonical entity enumeration (fail closed)
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
        companies = coa.org_companies(client, org_id)   # canonical entity enumeration
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
def _ensure_account_recompute_cron():
    """Self-register the GLOBAL statement-recompute pg_cron job (mig 940) so no one runs SQL by
    hand — called from the main.py startup hook on EVERY boot, exactly like the email-sweep cron
    (mig 922 / the 2026-08-25→09-01 dead-cron incident). Reads the backend's own API URL + notify
    secret from settings and calls the idempotent commcalc.ensure_account_recompute_cron RPC as
    service_role, re-embedding the CURRENT secret on every deploy (survives rotations, self-heals a
    lost job).

    NON-FATAL by design: a missing secret, the RPC not present (mig 940 not applied yet), or
    pg_cron/pg_net not installed just means auto-scheduling is skipped — boot still succeeds, and
    the staleness banner's Recompute button still works. Returns the RPC's status string (or None).
    Deterministic books: the cron only changes WHEN compute runs, never WHAT it computes."""
    try:
        url = (getattr(settings, "API_PUBLIC_URL", "") or "").strip()
        secret = (getattr(settings, "NOTIFY_RUN_SECRET", "") or "").strip()
        if not url or not secret:
            return "skipped: API_PUBLIC_URL or NOTIFY_RUN_SECRET not set"
        res = sb().schema("commcalc").rpc(
            "ensure_account_recompute_cron", {"p_url": url, "p_secret": secret}).execute()
        return res.data if isinstance(res.data, str) else (res.data or None)
    except Exception as e:
        print(f"WARN _ensure_account_recompute_cron skipped: {e}")
        return None


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


def _liabilities_due_impl(authorization, org_id, today_iso=""):
    """The Current Monetary Liabilities aggregation (owner directive 2026-09-03) — COMPOSED
    ENTIRELY of existing derivations (duplicate-check gate; window/aggregation math is pure in
    account/liabilities_due.py, proof harness_liabilities_due.py):
      · distributor payables  — statement_engine._fetch_outstanding_tx + the mig-933
        balance_sheet.handset_payable_bookings config/predicate; Boost-side owed_vip/vip_ap off
        the STORED consolidated Balance-Sheet snapshot (one math path, computed_at surfaced);
      · payroll / payroll tax — storeops payroll_raw + the payroll_tax_estimate twin, for the pay
        period(s) whose PAYDAY (core.router.pay_period_for — the one shared resolver) falls this
        week, gated FAIL-CLOSED by the mig-434 can_see_pay (denied ⇒ allowed:false, no dollars);
      · rents / insurance     — the mig-946 storeops.store_lease columns via the documented
        helpers (rent_for_month / resolve_rent_due / rent_due_window), gated whole by
        can_see_lease (ACH columns never selected, defense-in-depth on top of the gate).
    Store rows are span-filtered through storeops scope_keyset and stamped with the canonical
    market (core.scope.market_by_code, §13a)."""
    from datetime import date as _date, timedelta as _timedelta
    from app.modules.account import liabilities_due as _ld
    from app.modules.account import balance_sheet as _bs
    from app.modules.account import _period as _pd
    from app.modules.storeops import pay_visibility as _pv
    from app.modules.storeops import store_lease as _sl
    from app.modules.storeops.router import scope_keyset, in_keyset

    client = sb()
    today = str(today_iso or "")[:10] or _date.today().isoformat()
    wk_start, wk_end = _ld.week_window(today)
    period = _date.fromisoformat(today).strftime("%B %Y")
    ks = scope_keyset(authorization, org_id)          # None = unrestricted (admin / rbac off)

    # canonical store→market stamp (§13a — never a single-vocabulary join)
    try:
        from app.core import scope as _cscope
        mkt_by_code = _cscope.market_by_code(client, org_id) or {}
    except Exception:
        mkt_by_code = {}

    def _mk(store):
        return mkt_by_code.get(str(store or "").strip().upper(), "")

    out = {"as_of": today, "week": {"start": wk_start, "end": wk_end}, "period": period}

    # ── 1. distributor payables (mig-933 machinery) ─────────────────────────────────────────────
    distributor = {"configured": False, "outstanding": None, "due_this_week": None,
                   "snapshot": None}
    try:
        cfg = _bs.load_bs_config(client, org_id)
        fams = cfg.get("handset_payable_order_types") or []
        distributor["configured"] = bool(fams)
        if fams:
            # one fetch covers both questions: rows still due after the day before the week start
            fetch_from = (_date.fromisoformat(wk_start) - _timedelta(days=1)).isoformat()
            tx = statement_engine._fetch_outstanding_tx(client, org_id, fetch_from)
            _bookings, out_meta = _bs.handset_payable_bookings(tx, fams, today)
            due_rows, due_meta = _ld.payables_due_in_window(tx, fams, wk_start, wk_end)
            # store attribution = the mig-314 account→store index (same as the P&L / BS)
            acct_store = {}
            try:
                from app.modules.account import ma_store_pnl as _msp
                if _msp.load_config(client, org_id).get("store_attribution"):
                    acct_store = _msp.load_store_index(client, org_id) or {}
            except Exception:
                acct_store = {}
            resolve = coa.store_resolver(client, org_id)
            out_rows = [{"account_id": a, "amount": amt, "order_type": d}
                        for (a, amt, d) in _bookings]
            o_by, o_co = _ld.attribute_stores(out_rows, acct_store, resolve)
            d_by, d_co = _ld.attribute_stores(due_rows, acct_store, resolve)
            if ks is not None:                        # span scope: keep only in-span stores
                o_by = {s: v for s, v in o_by.items() if in_keyset(ks, s)}
                d_by = {s: v for s, v in d_by.items() if in_keyset(ks, s)}
                due_rows = [r for r in due_rows
                            if in_keyset(ks, (acct_store or {}).get(r.get("account_id")))]
            distributor["outstanding"] = {
                "total": out_meta["total"], "rows": out_meta["rows"], "as_of": today,
                "by_store": [{"store": s, "market": _mk(s), "amount": v}
                             for s, v in sorted(o_by.items())],
                "company_wide": o_co}
            distributor["due_this_week"] = {
                "total": due_meta["total"], "rows": due_rows,
                "by_store": [{"store": s, "market": _mk(s), "amount": v}
                             for s, v in sorted(d_by.items())],
                "company_wide": d_co}
    except Exception as e:
        distributor["note"] = f"handset payables unavailable: {type(e).__name__}"
    # Boost-side / invoice-side distributor position off the STORED consolidated BS snapshot
    try:
        rows = (client.schema("commcalc").table("account_statements")
                .select("period,computed_at,payload")
                .eq("org_id", org_id).eq("statement_type", "balance_sheet")
                .eq("scope_key", "consolidated")
                .in_("period", _pd.period_keys(period)).execute().data) or []
        rows.sort(key=lambda r: str(r.get("computed_at") or ""), reverse=True)
        if rows:
            snap, lines = rows[0], {}
            for sec in (snap.get("payload") or {}).get("sections", []):
                for ln in sec.get("lines", []):
                    lines[ln.get("key")] = ln.get("amount")
            distributor["snapshot"] = {
                "period": snap.get("period"), "computed_at": snap.get("computed_at"),
                "owed_vip": lines.get("owed_vip"), "vip_ap": lines.get("vip_ap"),
                "handset_payable": lines.get("handset_payable")}
    except Exception:
        pass
    out["distributor"] = distributor

    # ── 2. payroll due / payroll tax due (mig-434 gate, FAIL CLOSED) ────────────────────────────
    if not _pv.can_see_pay(authorization, org_id, client):
        out["payroll"] = {"allowed": False,
                          "note": "Pay figures are restricted for your role (org pay-visibility policy)."}
    else:
        payroll = {"allowed": True, "due": [], "current": None}
        try:
            from app.modules.core.router import pay_period_for, _pp_settings
            from app.modules.storeops.payroll_approval import _pay_settings
            from app.modules.storeops.router import payroll_raw as _praw
            from app.modules.storeops.payroll_tax_estimate import compute_pay as _ctax
            pcfg = _pay_settings(org_id) or _pp_settings({})

            def _section(p):
                data = _praw(start=p["start"], end=p["end"],
                             authorization=authorization, org_id=org_id) or {}
                agg = _ld.aggregate_payroll(data.get("rows") or [], _ctax)
                agg["by_store"] = [{"store": s, "market": _mk(s), **cell}
                                   for s, cell in sorted(agg["by_store"].items())]
                return {**p, **agg}

            for p in _ld.paydays_in_window(pcfg, pay_period_for, wk_start, wk_end):
                payroll["due"].append(_section(p))
            cur = pay_period_for(pcfg, _date.fromisoformat(today))
            payroll["current"] = _section(cur)
        except Exception as e:
            payroll["note"] = f"payroll unavailable: {type(e).__name__}"
        out["payroll"] = payroll

    # ── 3. rents + insurance due this week (mig-946 gate, FAIL CLOSED; ACH never selected) ──────
    if not _sl.can_see_lease(authorization, org_id, client):
        out["rents"] = {"allowed": False,
                        "note": "Lease/rent figures are restricted for your role."}
        out["insurance"] = {"allowed": False}
    else:
        rents = {"allowed": True, "rows": [], "total": 0.0, "unknown": 0}
        insurance = {"allowed": True, "rows": [], "total": 0.0}
        try:
            lease_rows = (client.schema("storeops").table("store_lease")
                          .select("store_code,current_rent,rent_effective_from,escalation_pct,"
                                  "rent_schedule,rent_due,lease_end,insurance_company,"
                                  "insurance_premium,insurance_premium_due,"
                                  "insurance_premium_frequency")
                          .eq("org_id", org_id).limit(5000).execute().data) or []
            if ks is not None:
                lease_rows = [r for r in lease_rows if in_keyset(ks, r.get("store_code"))]
            _roles, tenant_due = _sl.tenant_lease_config(org_id, client)
            rrows = _ld.rent_due_rows(lease_rows, tenant_due, wk_start, wk_end)
            for r in rrows:
                r["market"] = _mk(r["store_code"])
            rents["rows"] = rrows
            rents["total"], rents["unknown"] = _ld.sum_known(rrows)
            irows = _ld.insurance_due_rows(lease_rows, wk_start, wk_end)
            for r in irows:
                r["market"] = _mk(r["store_code"])
            insurance["rows"] = irows
            insurance["total"], _unk = _ld.sum_known(irows)
        except Exception as e:
            rents["note"] = f"lease data unavailable: {type(e).__name__} (run migration 946?)"
        out["rents"] = rents
        out["insurance"] = insurance

    # grand total of the KNOWN due-this-week dollars (unknown rents surface as a count, never $0)
    known = 0.0
    try:
        if distributor.get("due_this_week"):
            known += float(distributor["due_this_week"]["total"] or 0)
        if out.get("payroll", {}).get("allowed"):
            known += sum(float(p.get("gross_total") or 0) + float((p.get("tax") or {}).get("total") or 0)
                         for p in out["payroll"].get("due") or [])
        if out.get("rents", {}).get("allowed"):
            known += float(out["rents"].get("total") or 0)
            known += float(out.get("insurance", {}).get("total") or 0)
    except Exception:
        pass
    out["due_this_week_total_known"] = round(known, 2)
    return out


@router.get("/liabilities-due")
async def liabilities_due_endpoint(date: str = "", authorization: str = Header(default=""),
                                   org_id: str = ORG_ID):
    """Current Monetary Liabilities — per store, standard filters client-side (owner directive
    2026-09-03). See `_liabilities_due_impl` for the composition contract. `date` (YYYY-MM-DD)
    overrides "today" for testing/backdating a week view."""
    require_org(org_id)
    try:
        return await run_in_threadpool(_liabilities_due_impl, authorization, org_id, date)
    except Exception as e:
        raise HTTPException(500, f"liabilities-due failed: {type(e).__name__}: {e}")


@router.get("/overview/{period}")
def overview(period: str, org_id: str = ORG_ID):
    """Headline numbers + the list of computed scopes for the dashboard + filter dropdowns.

    FAIL-CLOSED SCOPE INVENTORY (owner directive 2026-09-04 — "cash flow analysis in cellfonz r us
    has … nova wave, and luxelink in the drop down menu … fix this as a system not a band aid"):
    every `company:<id>` scope offered here is cross-checked against the org's OWN canonical company
    inventory (`coa.org_companies`). A stored snapshot whose company id is not one of this tenant's
    entities — a foreign entity mis-filed under the org, or a stale snapshot for a since-deleted
    company — is DROPPED from the dropdown (`coa.filter_org_scopes`), never rendered. This is the
    single scope-picker source for the Account dashboard, P&L, Balance Sheet and Cash Flow pages."""
    require_org(org_id)
    rows = (sb().schema("commcalc").table("account_statements")
            .select("statement_type,scope_key,scope_label,payload,crosscheck_ok,computed_at,model")
            .eq("org_id", org_id).eq("period", period).execute().data) or []
    companies = coa.org_companies(sb(), org_id)   # canonical entity enumeration (fail closed)
    own_ids = {str(c["id"]) for c in companies}
    rows = coa.filter_org_scopes(rows, own_ids)   # foreign/stale company scopes never render
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


# ── financial analysis (chart-ready series from the stored snapshots — roadmap Phase 3) ───────
@router.get("/analysis")
async def financial_analysis(months: int = 12, authorization: str = Header(default=""),
                             org_id: str = ORG_ID):
    """Chart-ready financial-analysis series for the Financial Analysis page: consolidated monthly
    P&L/BS trend + margins, OPEX composition (stacked-bar ready), per-company and per-store
    comparison series. ONE MATH PATH: everything is read from the stored `account_statements`
    snapshots (`analysis.assemble`, pure) — never recomputed — so a chart can never disagree with
    the P&L / Balance Sheet pages.

    PERMISSION: gated by the 'account_trends' data grant (DEFAULT-CLOSED — the same gate as the
    Trends hub, whose charts this page supersets; admins / scope-'all' pass). Org-scoped; the read
    below carries org_id on every page and an unknown org simply has no rows (fail closed)."""
    require_org(org_id)
    report_gates.require_report_grant(authorization, report_gates.ACCOUNT_TRENDS,
                                      report="Financial Analysis")
    from app.modules.account import analysis

    def _rows():
        out = []
        for st in ("pl", "balance_sheet"):
            out.extend(coa._fetch_all(
                sb(), "account_statements",
                "period,statement_type,scope_key,scope_label,payload,computed_at",
                {"org_id": org_id, "statement_type": st}))
        return out

    try:
        rows = await run_in_threadpool(_rows)   # bulk Supabase read off the event loop (SEV-1 rule)
        own_ids = await run_in_threadpool(
            lambda: {str(c["id"]) for c in coa.org_companies(sb(), org_id)})
        return {"org_id": org_id, **analysis.assemble(rows, months=months,
                                                      own_company_ids=own_ids)}
    except Exception as e:
        raise HTTPException(500, f"analysis failed: {type(e).__name__}: {e}")


@router.get("/projection")
async def financial_projection(months: int = 24, horizon: int = 0,
                               authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Config-driven forward projection of the consolidated P&L (roadmap Phase 4): the stored-
    snapshot monthly series (the SAME `analysis.assemble` history the charts use) extended forward
    by the pure, DETERMINISTIC `projection_engine.project` — linear or seasonal-naive trend, with
    per-org growth/inflation overrides from `account_config.projection_config` (mig 941; house
    defaults otherwise). Every projected row is flagged `projected: true` and every assumption is
    listed; nothing here writes a snapshot or feeds a booked number. `horizon` > 0 overrides the
    configured horizon_months for this call (1..24 — an exploration knob, not a config write).
    PERMISSION: the same DEFAULT-CLOSED 'account_trends' grant as /account/analysis."""
    require_org(org_id)
    report_gates.require_report_grant(authorization, report_gates.ACCOUNT_TRENDS,
                                      report="Financial Projections")
    from app.modules.account import analysis, projection_engine

    def _run():
        rows = coa._fetch_all(
            sb(), "account_statements",
            "period,statement_type,scope_key,scope_label,payload,computed_at",
            {"org_id": org_id, "statement_type": "pl"})
        rows += coa._fetch_all(
            sb(), "account_statements",
            "period,statement_type,scope_key,scope_label,payload,computed_at",
            {"org_id": org_id, "statement_type": "balance_sheet"})
        monthly = analysis.assemble(rows, months=months).get("monthly") or []
        cfg = projection_engine.load_projection_config(sb(), org_id)
        if 1 <= int(horizon or 0) <= 24:
            cfg = {**cfg, "horizon_months": int(horizon)}
        return {"org_id": org_id, "actuals": monthly, **projection_engine.project(monthly, cfg)}

    try:
        return await run_in_threadpool(_run)
    except Exception as e:
        raise HTTPException(500, f"projection failed: {type(e).__name__}: {e}")


@router.get("/valuation")
async def company_valuation(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Probable company valuation (roadmap Phase 5) — an assumption-driven ESTIMATE range from the
    org's OWN stored statements: revenue/SDE/EBITDA multiples on the trailing twelve months, an
    asset-based floor from the latest Balance Sheet, and a DCF fed by the deterministic Phase-4
    projection (3×3 rate × terminal-multiple sensitivity grid). Every multiple/rate/horizon is
    per-org config with house defaults (`account_config.valuation_config`, mig 941) and each
    method cites its source; the payload carries the full assumptions block + the not-an-appraisal
    disclaimer the UI must show.

    PERMISSION: its OWN DEFAULT-CLOSED 'company_valuation' data grant — the most sensitive finance
    read; deliberately not bundled under 'account_trends'. Org-scoped, worker-thread."""
    require_org(org_id)
    report_gates.require_report_grant(authorization, report_gates.COMPANY_VALUATION,
                                      report="Company valuation")
    from app.modules.account import analysis, projection_engine, valuation

    def _run():
        rows = []
        for st in ("pl", "balance_sheet"):
            rows += coa._fetch_all(
                sb(), "account_statements",
                "period,statement_type,scope_key,scope_label,payload,computed_at",
                {"org_id": org_id, "statement_type": st})
        monthly = analysis.assemble(rows, months=36).get("monthly") or []
        cfg, cfg_src = valuation.load_valuation_config(sb(), org_id)
        pcfg = projection_engine.load_projection_config(sb(), org_id)
        proj = projection_engine.project(
            monthly, {**pcfg, "horizon_months": min(24, max(12, cfg["dcf_horizon_months"]))})
        # DCF horizon may exceed the engine's 24-month cap: extend by holding the final projected
        # month flat (stated in the projection meta) — deterministic, assumption on the record.
        fcfs, meta = None, None
        if proj.get("computed"):
            ni = [s["net_income"] for s in proj["series"]]
            want = cfg["dcf_horizon_months"]
            if len(ni) < want and ni:
                ni = ni + [ni[-1]] * (want - len(ni))
                meta_note = (f"projection horizon capped at {len(proj['series'])} months; months "
                             f"{len(proj['series']) + 1}–{want} hold the final projected month flat")
            else:
                ni, meta_note = ni[:want], None
            fcfs = ni
            meta = {"method": proj.get("method"), "history_months": proj.get("history_months"),
                    "assumptions": proj.get("assumptions"), **({"note": meta_note} if meta_note else {})}
        return {"org_id": org_id, **valuation.valuation(monthly, cfg, cfg_src, fcfs, meta)}

    try:
        return await run_in_threadpool(_run)
    except Exception as e:
        raise HTTPException(500, f"valuation failed: {type(e).__name__}: {e}")


# ── health ──────────────────────────────────────────────────────────────────────────────────
@router.get("/health")
def health():
    return {"ok": True, "engine_configured": bool(settings.ANTHROPIC_API_KEY),
            "model": settings.ACCOUNT_ENGINE_MODEL}
