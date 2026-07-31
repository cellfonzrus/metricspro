"""Account Module router — companies + store assignment (#multi-company), manual journal
entries, the compute/compute-on-demand engine, P&L, Balance Sheet, and the #10 reconciliation."""
from fastapi import APIRouter, HTTPException, Header
from fastapi.concurrency import run_in_threadpool
from datetime import datetime, timezone

from app.core.database import get_supabase
from app.core.config import settings
from app.modules.account import coa, engine, autocompute, report_gates
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
async def list_companies(org_id: str = ORG_ID):
    require_org(org_id)
    rows = (sb().schema("commcalc").table("companies").select("*")
            .eq("org_id", org_id).order("name").execute().data) or []
    return {"companies": rows}


@router.post("/companies")
async def create_company(body: dict, org_id: str = ORG_ID):
    require_org(org_id)
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    row = {"org_id": org_id, "name": name,
           "legal_name": (body.get("legal_name") or name).strip(),
           "ein": (body.get("ein") or "").strip() or None}
    res = sb().schema("commcalc").table("companies").insert(row).execute()
    return {"company": (res.data or [row])[0]}


@router.patch("/companies/{company_id}")
async def update_company(company_id: str, body: dict, org_id: str = ORG_ID):
    require_org(org_id)
    upd = {k: body[k] for k in ("name", "legal_name", "ein") if k in body}
    if not upd:
        raise HTTPException(400, "nothing to update")
    sb().schema("commcalc").table("companies").update(upd) \
        .eq("org_id", org_id).eq("id", company_id).execute()
    return {"ok": True}


# ── stores (registry + current company assignment) ────────────────────────────────────────────
@router.get("/stores")
async def list_stores(org_id: str = ORG_ID):
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


@router.post("/companies/assign")
async def assign_stores(body: dict, org_id: str = ORG_ID):
    """Body: {assignments:[{store_address, company_id}]}. Upserts the store→company map."""
    require_org(org_id)
    rows = body.get("assignments") or []
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
async def get_journal(period: str, org_id: str = ORG_ID):
    require_org(org_id)
    rows = (sb().schema("commcalc").table("journal_entries").select("*")
            .eq("org_id", org_id).eq("period", period).order("statement").execute().data) or []
    return {"period": period, "entries": rows,
            "account_types": {"pl": sorted(PL_TYPES), "balance_sheet": sorted(BS_TYPES)}}


@router.put("/journal/{period}")
async def put_journal(period: str, body: dict, org_id: str = ORG_ID):
    """Replace all journal entries for the period. Body: {rows:[{statement, account_type,
    account_line, amount, company_id?, store_address?, entry_date?, memo?}]}."""
    require_org(org_id)
    pm, py = coa.parse_period(period)
    rows = body.get("rows") or []
    client = sb()
    ins = []
    for r in rows:
        statement = (r.get("statement") or "").strip()
        atype = (r.get("account_type") or "").strip()
        line = (r.get("account_line") or "").strip()
        try:
            amt = float(r.get("amount") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if statement not in ("pl", "balance_sheet") or not line:
            continue
        if statement == "pl" and atype not in PL_TYPES:
            continue
        if statement == "balance_sheet" and atype not in BS_TYPES:
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
    return {"saved": len(ins), "period": period}


# ── editable inventory value (real-time b2bsoft Inventory Aging → Balance Sheet) ───────────────
@router.get("/inventory-values")
async def list_inventory_values(org_id: str = ORG_ID):
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


@router.put("/inventory-values")
async def put_inventory_values(body: dict, org_id: str = ORG_ID):
    """Set/clear the manual inventory override per store. Body: {rows:[{store, manual_value,
    note?}]}. manual_value null/'' clears the override (the swept value then drives the BS).
    Never touches swept_value. Re-compute statements to apply to a stored Balance Sheet."""
    require_org(org_id)
    client = sb()
    saved = 0
    for r in (body.get("rows") or []):
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
async def get_config(org_id: str = ORG_ID):
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
    return {"org_id": org_id, "config": cfg, "is_default": not saved,
            "defaults": {"accessory_cogs_pct": coa.ACCESSORY_COGS_PCT}}


@router.put("/config")
async def put_config(body: dict, org_id: str = ORG_ID):
    """Set this tenant's finance/accounting config. Body: {accessory_cogs_pct} (0..1). MONEY-TOUCHING —
    changing the accessory COGS % moves this tenant's Accessory cost / Gross Profit; RECOMPUTE the
    period statements afterward for it to take effect on the stored P&L."""
    require_org(org_id)
    try:
        pct = float(body.get("accessory_cogs_pct"))
    except (TypeError, ValueError):
        raise HTTPException(400, "accessory_cogs_pct must be a number between 0 and 1")
    if not (0 <= pct <= 1):
        raise HTTPException(400, "accessory_cogs_pct must be between 0 and 1 (e.g. 0.20 = 20%)")
    row = {"org_id": org_id, "accessory_cogs_pct": round(pct, 6),
           "updated_at": datetime.now(timezone.utc).isoformat()}
    try:
        sb().schema("commcalc").table("account_config").upsert(row, on_conflict="org_id").execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 611 first: {e}")
    return {"ok": True, "org_id": org_id, "config": coa._account_config(sb(), org_id)}


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
        return await run_in_threadpool(engine.compute_and_store, sb(), org_id, period)
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
    if not settings.NOTIFY_RUN_SECRET or x_notify_secret != settings.NOTIFY_RUN_SECRET:
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


@router.get("/overview/{period}")
async def overview(period: str, org_id: str = ORG_ID):
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
        else:
            s["assets"] = p.get("assets_total")
            s["balanced"] = p.get("balanced")
        s["computed_at"] = r.get("computed_at")
        s["model"] = r.get("model")
    return {"period": period, "computed": bool(rows), "companies": companies,
            "scopes": sorted(scopes.values(),
                             key=lambda x: (0 if x["scope_key"] == "consolidated"
                                            else 1 if x["scope_key"].startswith("company:") else 2,
                                            x.get("scope_label") or ""))}


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
async def sync_recon_flags(period: str, tolerance: float = 1.0, date_col: str = "mi_activation_date",
                           org_id: str = ORG_ID):
    require_org(org_id)
    from app.modules.account import recon
    return recon.sync_flags(sb(), org_id, period, tolerance, date_col)


@router.get("/credit-memos/{period}")
async def get_credit_memos(period: str, org_id: str = ORG_ID):
    require_org(org_id)
    rows = (sb().schema("commcalc").table("vip_credit_memos").select("*")
            .eq("org_id", org_id).eq("period", period).order("created_on").execute().data) or []
    return {"period": period, "credit_memos": rows, "count": len(rows)}


@router.post("/credit-memos/sweep")
async def sweep_credit_memos(org_id: str = ORG_ID):
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
async def residual_per_sub(months: int = 6, authorization: str = Header(default=""), org_id: str = ORG_ID):
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
async def health():
    return {"ok": True, "engine_configured": bool(settings.ANTHROPIC_API_KEY),
            "model": settings.ACCOUNT_ENGINE_MODEL}
