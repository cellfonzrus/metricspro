"""Account Module router — companies + store assignment (#multi-company), manual journal
entries, the compute/compute-on-demand engine, P&L, Balance Sheet, and the #10 reconciliation."""
from fastapi import APIRouter, HTTPException, Header
from datetime import datetime, timezone

from app.core.database import get_supabase
from app.core.config import settings
from app.modules.account import coa, engine

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
    """Stores from store_mapping (canonical, with market) + any already-assigned addresses,
    each with its current company assignment."""
    require_org(org_id)
    client = sb()
    mapping = coa._fetch_all(client, "store_mapping", "store_address,market", {"org_id": org_id})
    assigns = {(_a := (r.get("store_address") or "").strip()): r.get("company_id")
               for r in coa._fetch_all(client, "store_companies", "store_address,company_id", {"org_id": org_id})}
    companies = (client.schema("commcalc").table("companies").select("id,name")
                 .eq("org_id", org_id).execute().data) or []
    co_name = {c["id"]: c["name"] for c in companies}
    seen, out = set(), []
    for r in mapping:
        sa = (r.get("store_address") or "").strip()
        if not sa or sa in seen:
            continue
        seen.add(sa)
        cid = assigns.get(sa)
        out.append({"store_address": sa, "market": r.get("market"),
                    "company_id": cid, "company_name": co_name.get(cid)})
    for sa, cid in assigns.items():
        if sa and sa not in seen:
            out.append({"store_address": sa, "market": None,
                        "company_id": cid, "company_name": co_name.get(cid)})
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


# ── compute (build + persist all snapshots) ───────────────────────────────────────────────────
@router.post("/compute/{period}")
async def compute(period: str, org_id: str = ORG_ID):
    require_org(org_id)
    try:
        return engine.compute_and_store(sb(), org_id, period)
    except Exception as e:
        raise HTTPException(500, f"compute failed: {type(e).__name__}: {e}")


# ── read snapshots ────────────────────────────────────────────────────────────────────────────
def _read(period, st_type, scope, org_id):
    rows = (sb().schema("commcalc").table("account_statements").select("*")
            .eq("org_id", org_id).eq("period", period).eq("statement_type", st_type)
            .eq("scope_key", scope).execute().data) or []
    return rows[0] if rows else None


@router.get("/pl/{period}")
async def get_pl(period: str, scope: str = "consolidated", org_id: str = ORG_ID):
    require_org(org_id)
    row = _read(period, "pl", scope, org_id)
    if not row:
        return {"period": period, "scope": scope, "computed": False}
    return {"period": period, "scope": scope, "computed": True,
            "statement": row["payload"], "narrative": row.get("narrative"),
            "model": row.get("model"), "computed_at": row.get("computed_at"),
            "crosscheck_ok": row.get("crosscheck_ok")}


@router.get("/balance-sheet/{period}")
async def get_bs(period: str, scope: str = "consolidated", org_id: str = ORG_ID):
    require_org(org_id)
    row = _read(period, "balance_sheet", scope, org_id)
    if not row:
        return {"period": period, "scope": scope, "computed": False}
    return {"period": period, "scope": scope, "computed": True,
            "statement": row["payload"], "narrative": row.get("narrative"),
            "model": row.get("model"), "computed_at": row.get("computed_at"),
            "crosscheck_ok": row.get("crosscheck_ok")}


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
    return recon.reconcile(sb(), org_id, period, tolerance, date_col, analyze=analyze)


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
        raise HTTPException(400, "VIP portal credentials not set — configure them on the VIP sweep page first.")
    try:
        from app.modules.commcalc import vip_sweep
        from app.modules.commcalc.router import _vip_money, _vip_int, _vip_ts, _vip_period
        helpers = (_vip_money, _vip_int, _vip_ts, _vip_period)
        return vip_sweep.run_creditmemo_sweep(client, org_id, cfg["portal_user"], cfg["portal_pass"], helpers)
    except Exception as e:
        raise HTTPException(500, f"credit-memo sweep failed: {type(e).__name__}: {e}")


# ── health ──────────────────────────────────────────────────────────────────────────────────
@router.get("/health")
async def health():
    return {"ok": True, "engine_configured": bool(settings.ANTHROPIC_API_KEY),
            "model": settings.ACCOUNT_ENGINE_MODEL}
