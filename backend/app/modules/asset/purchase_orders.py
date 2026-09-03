"""Purchase Orders — proposed PO (from the forecasting/recommendation source), receiving, sold-tally, and
unsold-inventory-aging (mod-asset, migration band 300-399, mig 301). Mounted onto the asset module's own
`router` (backend/app/modules/asset/router.py appends `router.include_router(po_router)` at the bottom) so
every endpoint here lives under the SAME /api/v1/asset prefix already registered in main.py — no main.py
change needed.

Cross-module read (NOT an edit — payables/router.py and payables/engine.py are mod-finance-owned and
untouched here): the Proposed-PO "recommended phones" feature is spec'd to read the Forecasting & Vendor
Payables engine's output. `GET /api/v1/payables/forecast` already IS an org-scoped read API for that — but
it filters by a single `store` only, not `market` (which this page's standard filter bar needs), and
importing its `forecast()` function in-process to call directly would create an asset<->payables import
cycle at module-LOAD time (this file is imported from the BOTTOM of asset/router.py, which payables/router.py
itself imports FROM — a fragile A-imports-B-imports-A shape). Rather than risk that or add a live HTTP
round-trip to another module's endpoint, `po_recommendations` below independently reads the SAME underlying
org-scoped tables (`commcalc.raw_sales` device lines + `commcalc.asset_ledger` on-hand) with equivalent
velocity/on-hand logic. See this module's handoff for the concrete cross-module ask (a `market` param on
`/payables/forecast` would let this delegate instead of duplicating ~30 lines of aggregation).

IMPORT SHAPE — deliberately NOT importing from `app.modules.asset.router` at module-load time: this file
is itself imported from the BOTTOM of asset/router.py (`router.include_router(po_router)`), so a top-level
`from app.modules.asset.router import ...` here would make the two modules mutually import each other. That
resolves fine ONLY when asset/router.py happens to be the first of the pair to start loading (true in the
real app via main.py) — but breaks with a real ImportError if anything ever imports
`app.modules.asset.purchase_orders` directly first (e.g. a focused test). Fixed by keeping this file's own
copies of the couple of trivial universal helpers it needs (`_norm_imei`, `_is_missing_schema_error`,
`ORG_ID` — 3 lines each, no business logic, same precedent as payables/router.py defining its OWN `ORG_ID`
rather than importing asset's) and reaching for the one non-trivial reused helper, `_epay_payments_map`
(the hotsheet-recon/appeals IMEI↔ePay join this module reuses for Sold Tally), via a LAZY import inside the
one function that calls it — safe regardless of load order, since by the time any request handler actually
runs, every module has finished loading.
"""
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Header

from app.core.database import get_supabase
from app.core.schemas import LaxModel

router = APIRouter()

ORG_ID = "00000000-0000-0000-0000-000000000001"


class CreateVendorIn(LaxModel):
    name: Any = None
    contact_name: Any = None
    email: Any = None
    phone: Any = None
    terms: Any = None
    notes: Any = None


class UpdateVendorIn(LaxModel):
    name: Any = None
    contact_name: Any = None
    email: Any = None
    phone: Any = None
    terms: Any = None
    notes: Any = None
    is_active: Any = None


class PutPoSettingsIn(LaxModel):
    aging_flag_days: Any = None


class CreatePoIn(LaxModel):
    lines: Any = None
    vendor_id: Any = None
    ship_to_store: Any = None
    market: Any = None
    status: Any = None
    order_date: Any = None
    buyer: Any = None
    expected_delivery_date: Any = None
    notes: Any = None
    source: Any = None


class UpdatePoIn(LaxModel):
    status: Any = None
    vendor_id: Any = None
    order_date: Any = None
    ship_to_store: Any = None
    market: Any = None
    buyer: Any = None
    expected_delivery_date: Any = None
    notes: Any = None


class ReceivePoLineIn(LaxModel):
    po_line_id: Any = None
    qty_received: Any = None
    received_date: Any = None
    units: Any = None
    received_by: Any = None
    notes: Any = None
_MIGRATION_MSG = ("Purchase Orders migration pending — ask the operator to run "
                   "database/migrations/301_asset_purchase_orders.sql in the Supabase SQL Editor.")
_PO_STATUSES = ("draft", "submitted", "partially_received", "received", "closed", "cancelled")
_DEV_DEPTS = {"android - xp", "iphone - xp", "tablet - xp"}   # mirrors payables/router.py DEV_DEPTS (device box lines)
# PostgREST's "this relation/function doesn't exist" error signatures (schema-cache miss because mig 301
# hasn't run) — same markers asset/router.py's own _is_missing_schema_error uses (mig-300 precedent).
_MISSING_SCHEMA_MARKERS = ("PGRST202", "PGRST205", "PGRST203", "schema cache", "does not exist")


def sb():
    return get_supabase()


def _norm_imei(v):
    s = str(v or "").strip().upper()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _is_missing_schema_error(exc: Exception) -> bool:
    s = str(exc)
    return any(m in s for m in _MISSING_SCHEMA_MARKERS)


def _int(v, d=0):
    try:
        return int(round(float(v)))
    except Exception:
        return d


def _float(v, d=0.0):
    try:
        return round(float(v), 2)
    except Exception:
        return d


def _is_device_line(dept, category):
    """A physical-phone sale line — same rule as payables/router.py's _is_device_line (duplicated here,
    not imported, to avoid the module-load cycle described above; it's 2 lines of universal classification,
    not business logic worth coupling on)."""
    return (str(category or "").strip().lower() == "cellphone") or ((dept or "").strip().lower() in _DEV_DEPTS)


# ── per-setting admin gate (mirrors commcalc/router.py's _require_commission_admin) ─────────────────────
def _require_po_admin(authorization: str, org_id: str):
    """Vendor-roster writes + the aging-threshold setting are gated on the 'asset_purchase_orders'
    settings area (core._can_edit_setting). That area isn't registered in core.SETTING_AREAS yet (see
    NEEDS CORE in the handoff) — _can_edit_setting's default rule (full-scope admin / role=='admin') still
    enforces correctly without that registration; registering it only adds a per-role toggle in the Roles
    UI. Degrades OPEN when the caller can't be resolved (RBAC off) so it never locks the house org out of
    its own vendor list."""
    try:
        from app.modules.core.router import _uid_from_token, _resolve_caller, _can_edit_setting
        uid = _uid_from_token(authorization)
        caller = _resolve_caller(sb(), uid) if uid else None
        if caller is None:
            return
        if not _can_edit_setting(caller, "asset_purchase_orders"):
            raise HTTPException(403, "Only an administrator may edit Purchase Orders vendors/settings for this tenant.")
    except HTTPException:
        raise
    except Exception:
        return  # never 500 a write over a resolution error


def _validate_status_transition(current: str, new: str):
    if new not in _PO_STATUSES:
        raise HTTPException(400, f"Unknown status '{new}'. Must be one of {', '.join(_PO_STATUSES)}.")
    if new == current:
        return
    if current in ("closed", "cancelled"):
        raise HTTPException(400, f"This PO is {current} — no further status changes.")
    if new == "cancelled" and current not in ("draft", "submitted"):
        raise HTTPException(400, "Only a draft/submitted PO with nothing received yet can be cancelled — close it instead.")


# ── Vendors (RULE THREE pick-don't-type source + its own manage-vendors surface) ────────────────────────
@router.get("/po/vendors")
def list_vendors(active_only: bool = True, org_id: str = ORG_ID):
    client = sb()
    try:
        q = client.schema("commcalc").table("po_vendor").select("*").eq("org_id", org_id)
        if active_only:
            q = q.eq("is_active", True)
        rows = q.order("name").execute().data or []
        return {"migrated": True, "rows": rows}
    except Exception as e:
        if _is_missing_schema_error(e):
            return {"migrated": False, "rows": [], "note": _MIGRATION_MSG}
        raise HTTPException(500, str(e))


@router.post("/po/vendors")
def create_vendor(body: CreateVendorIn, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_po_admin(authorization, org_id)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Vendor name is required.")
    client = sb()
    try:
        existing = (client.schema("commcalc").table("po_vendor").select("id")
                    .eq("org_id", org_id).ilike("name", name).limit(1).execute().data) or []
        if existing:
            raise HTTPException(400, f"A vendor named '{name}' already exists.")
        row = {
            "org_id": org_id, "name": name,
            "contact_name": body.contact_name or None, "email": body.email or None,
            "phone": body.phone or None, "terms": body.terms or None,
            "notes": body.notes or None, "is_active": True,
        }
        res = client.schema("commcalc").table("po_vendor").insert(row).execute()
        return {"ok": True, "vendor": (res.data or [row])[0]}
    except HTTPException:
        raise
    except Exception as e:
        if _is_missing_schema_error(e):
            raise HTTPException(400, _MIGRATION_MSG)
        raise HTTPException(500, str(e))


@router.patch("/po/vendors/{vendor_id}")
def update_vendor(vendor_id: str, body: UpdateVendorIn, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_po_admin(authorization, org_id)
    patch = {}
    for f in ("name", "contact_name", "email", "phone", "terms", "notes", "is_active"):
        if f in body.model_fields_set:
            patch[f] = getattr(body, f)
    if not patch:
        return {"ok": True, "unchanged": True}
    patch["updated_at"] = datetime.now(timezone.utc).isoformat()
    client = sb()
    try:
        client.schema("commcalc").table("po_vendor").update(patch).eq("org_id", org_id).eq("id", vendor_id).execute()
        return {"ok": True}
    except Exception as e:
        if _is_missing_schema_error(e):
            raise HTTPException(400, _MIGRATION_MSG)
        raise HTTPException(500, str(e))


# ── Settings (aging_flag_days, tenant-configurable, default 10 — RULE TWO) ──────────────────────────────
@router.get("/po/settings")
def get_po_settings(org_id: str = ORG_ID):
    client = sb()
    try:
        r = (client.schema("commcalc").table("po_settings").select("*")
             .eq("org_id", org_id).limit(1).execute().data) or []
        days = r[0].get("aging_flag_days") if r else None
        return {"migrated": True, "aging_flag_days": days if days is not None else 10}
    except Exception as e:
        if _is_missing_schema_error(e):
            return {"migrated": False, "aging_flag_days": 10, "note": _MIGRATION_MSG}
        raise HTTPException(500, str(e))


@router.put("/po/settings")
def put_po_settings(body: PutPoSettingsIn, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_po_admin(authorization, org_id)
    days = _int(body.aging_flag_days, -1)
    if days < 1 or days > 365:
        raise HTTPException(400, "aging_flag_days must be an integer between 1 and 365.")
    client = sb()
    row = {"org_id": org_id, "aging_flag_days": days, "updated_at": datetime.now(timezone.utc).isoformat()}
    try:
        client.schema("commcalc").table("po_settings").upsert(row, on_conflict="org_id").execute()
        return {"ok": True, "aging_flag_days": days}
    except Exception as e:
        if _is_missing_schema_error(e):
            raise HTTPException(400, _MIGRATION_MSG)
        raise HTTPException(500, str(e))


# ── Model/SKU options (pick-or-create — RULE THREE) ──────────────────────────────────────────────────────
@router.get("/po/model-options")
def po_model_options(org_id: str = ORG_ID):
    client = sb()
    out = set()
    try:
        for r in (client.schema("commcalc").table("device_model_alias").select("canonical_model")
                  .eq("org_id", org_id).execute().data or []):
            m = (r.get("canonical_model") or "").strip()
            if m:
                out.add(m)
    except Exception:
        pass
    try:
        page = 0
        while True:
            chunk = (client.schema("commcalc").table("asset_ledger").select("device_model")
                     .eq("org_id", org_id).range(page * 1000, page * 1000 + 999).execute().data) or []
            for r in chunk:
                m = (r.get("device_model") or "").strip()
                if m:
                    out.add(m)
            if len(chunk) < 1000:
                break
            page += 1
            if page > 60:
                break
    except Exception:
        pass
    return {"models": sorted(out)}


# ── Recommendations (Proposed PO source data) ────────────────────────────────────────────────────────────
def _catalog_cost_map(client, org_id):
    out = {}
    try:
        for r in (client.schema("commcalc").table("raw_catalog").select("product_desc,sku,cost")
                  .eq("org_id", org_id).execute().data or []):
            desc = (r.get("product_desc") or "").strip().lower()
            cost = r.get("cost")
            if desc and cost is not None and desc not in out:
                out[desc] = _float(cost)
    except Exception:
        pass
    return out


@router.get("/po/recommendations")
def po_recommendations(stores: str = "", market: str = "", lookback: int = 7, horizon: int = 14,
                        org_id: str = ORG_ID):
    """Per (store, canonical device model): recent sales velocity vs current on-hand → recommend_qty.
    `stores` is a comma-separated list (the standard filter bar's multi-select); `market` narrows further
    via store_mapping. Boost-shape coverage only (raw_sales device lines + asset_ledger on-hand On
    Inventory) — see module docstring + handoff for the Total/raw_ma_commission parity gap (deferred, not
    silently guessed at)."""
    client = sb()
    lookback = max(1, min(_int(lookback, 7), 365))
    horizon = max(1, min(_int(horizon, 14), 365))
    store_list = [s.strip() for s in (stores or "").split(",") if s.strip()]

    alias = {}
    try:
        for r in (client.schema("commcalc").table("device_model_alias").select("raw_model,canonical_model")
                  .eq("org_id", org_id).execute().data or []):
            k = (r.get("raw_model") or "").strip().lower()
            if k:
                alias[k] = r.get("canonical_model") or r.get("raw_model")
    except Exception:
        pass

    # THE canonical union store→market resolver (core.scope; 2026-09-03 "1115 Liberty Ave"/LI
    # class fix — was a store_mapping-only address map).
    try:
        from app.core import scope as _cscope
        _po_resolve_market, _ = _cscope.store_market_resolver(client, org_id)
    except Exception:
        _po_resolve_market = lambda s: ""

    def _canon(raw):
        base = str(raw or "").split(" - ")[0].strip()
        return alias.get(base.lower(), base) or base

    def _market_of(store):
        return _po_resolve_market(store) or None

    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=lookback)).isoformat()
    agg = {}

    def bucket(store, model):
        key = (store or "Unknown", model)
        return agg.setdefault(key, {"store": store or "Unknown", "market": _market_of(store),
                                    "device_model": model, "units_sold": 0, "on_hand": 0})

    try:
        page = 0
        while True:
            q = (client.schema("commcalc").table("raw_sales")
                 .select("store,product_desc,department,category,voided,trans_type,salesperson,trans_date")
                 .eq("org_id", org_id).gte("trans_date", cutoff))
            if store_list:
                q = q.in_("store", store_list)
            chunk = q.range(page * 1000, page * 1000 + 999).execute().data or []
            for r in chunk:
                if str(r.get("voided") or "").upper() == "YES" or (r.get("trans_type") or "") == "Return":
                    continue
                if not _is_device_line(r.get("department"), r.get("category")):
                    continue
                sp = (r.get("salesperson") or "").strip().lower()
                if not sp or sp == "admin":
                    continue
                bucket(r.get("store"), _canon(r.get("product_desc")))["units_sold"] += 1
            if len(chunk) < 1000:
                break
            page += 1
            if page > 60:
                break
    except Exception:
        pass

    try:
        page = 0
        while True:
            q = (client.schema("commcalc").table("asset_ledger")
                 .select("store,device_model,date_sold,category").eq("org_id", org_id))
            if store_list:
                q = q.in_("store", store_list)
            chunk = q.range(page * 1000, page * 1000 + 999).execute().data or []
            for r in chunk:
                if r.get("date_sold") or "on inventory" not in (r.get("category") or "").lower():
                    continue
                bucket(r.get("store"), _canon(r.get("device_model")))["on_hand"] += 1
            if len(chunk) < 1000:
                break
            page += 1
            if page > 60:
                break
    except Exception:
        pass

    cost_map = _catalog_cost_map(client, org_id)
    out = []
    for b in agg.values():
        if market and (b.get("market") or "") != market:
            continue
        rate = b["units_sold"] / lookback
        projected = int(round(rate * horizon))
        recommend = max(0, projected - b["on_hand"])
        if recommend <= 0 and b["units_sold"] == 0 and b["on_hand"] == 0:
            continue
        out.append({
            **b, "avg_daily_velocity": round(rate, 2), "projected_demand": projected,
            "recommend_qty": recommend,
            "suggested_unit_cost": cost_map.get(b["device_model"].strip().lower(), 0),
        })
    out.sort(key=lambda x: (-x["recommend_qty"], -x["units_sold"]))
    return {"lookback": lookback, "horizon": horizon, "rows": out, "total": len(out)}


# ── PO number allocation ─────────────────────────────────────────────────────────────────────────────────
def _next_po_number(client, org_id):
    res = client.schema("commcalc").rpc("next_po_number", {"p_org_id": org_id}).execute()
    d = res.data
    if isinstance(d, str) and d:
        return d
    if isinstance(d, list) and d:
        first = d[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return first.get("next_po_number") or next(iter(first.values()))
    raise RuntimeError(f"unexpected next_po_number response: {d!r}")


# ── Purchase orders (header + lines) ─────────────────────────────────────────────────────────────────────
@router.get("/po")
def list_pos(store: str = "", market: str = "", status: str = "", vendor_id: str = "",
             date_from: str = "", date_to: str = "", org_id: str = ORG_ID):
    client = sb()
    try:
        q = client.schema("commcalc").table("purchase_order").select("*").eq("org_id", org_id)
        if store:
            q = q.eq("ship_to_store", store)
        if market:
            q = q.eq("market", market)
        if status:
            q = q.eq("status", status)
        if vendor_id:
            q = q.eq("vendor_id", vendor_id)
        if date_from:
            q = q.gte("order_date", date_from)
        if date_to:
            q = q.lte("order_date", date_to)
        rows = q.order("order_date", desc=True).limit(2000).execute().data or []
        return {"migrated": True, "rows": rows, "total": len(rows)}
    except Exception as e:
        if _is_missing_schema_error(e):
            return {"migrated": False, "rows": [], "total": 0, "note": _MIGRATION_MSG}
        raise HTTPException(500, str(e))


@router.post("/po")
def create_po(body: CreatePoIn, org_id: str = ORG_ID):
    client = sb()
    lines_in = body.lines or []
    if not lines_in:
        raise HTTPException(400, "At least one line item is required.")
    vendor_id = body.vendor_id or None
    vendor_name = None
    try:
        if vendor_id:
            v = (client.schema("commcalc").table("po_vendor").select("name")
                 .eq("org_id", org_id).eq("id", vendor_id).limit(1).execute().data) or []
            vendor_name = v[0]["name"] if v else None
        po_number = _next_po_number(client, org_id)
    except Exception as e:
        if _is_missing_schema_error(e):
            raise HTTPException(400, _MIGRATION_MSG)
        raise HTTPException(500, f"Could not allocate a PO number: {e}")

    subtotal = 0.0
    line_rows = []
    for i, l in enumerate(lines_in):
        qty = _int(l.get("qty_ordered") if l.get("qty_ordered") is not None else l.get("qty"))
        cost = _float(l.get("unit_cost"))
        ext = round(qty * cost, 2)
        subtotal += ext
        line_rows.append({
            "org_id": org_id, "line_no": i + 1,
            "sku": (l.get("sku") or None), "device_model": l.get("device_model") or l.get("model") or "Unknown",
            "qty_ordered": qty, "unit_cost": cost, "extended_cost": ext, "qty_received": 0,
            "store": l.get("store") or body.ship_to_store or None,
            "market": l.get("market") or body.market or None,
            "notes": l.get("notes") or None,
        })
    status = body.status if body.status in ("draft", "submitted") else "draft"
    po_row = {
        "org_id": org_id, "po_number": po_number,
        "order_date": body.order_date or datetime.now(timezone.utc).date().isoformat(),
        "vendor_id": vendor_id, "vendor_name_snapshot": vendor_name,
        "ship_to_store": body.ship_to_store or None, "market": body.market or None,
        "buyer": body.buyer or None, "status": status,
        "subtotal": round(subtotal, 2), "total": round(subtotal, 2),
        "expected_delivery_date": body.expected_delivery_date or None,
        "notes": body.notes or None, "source": body.source or "manual",
        "created_by": body.buyer or None,
    }
    try:
        pres = client.schema("commcalc").table("purchase_order").insert(po_row).execute()
        po_id = (pres.data or [{}])[0].get("id")
        for lr in line_rows:
            lr["po_id"] = po_id
        for i in range(0, len(line_rows), 500):
            client.schema("commcalc").table("purchase_order_line").insert(line_rows[i:i + 500]).execute()
        return {"ok": True, "id": po_id, "po_number": po_number}
    except Exception as e:
        if _is_missing_schema_error(e):
            raise HTTPException(400, _MIGRATION_MSG)
        raise HTTPException(500, str(e))


@router.get("/po/open")
def list_open_pos(store: str = "", market: str = "", org_id: str = ORG_ID):
    """Open POs (submitted / partially_received) with remaining qty per line — the Receiving page's list."""
    client = sb()
    try:
        q = (client.schema("commcalc").table("purchase_order").select("*")
             .eq("org_id", org_id).in_("status", ["submitted", "partially_received"]))
        if store:
            q = q.eq("ship_to_store", store)
        if market:
            q = q.eq("market", market)
        pos = q.order("order_date").execute().data or []
        if not pos:
            return {"migrated": True, "rows": []}
        ids = [p["id"] for p in pos]
        lines = []
        for i in range(0, len(ids), 200):
            lines.extend((client.schema("commcalc").table("purchase_order_line").select("*")
                          .eq("org_id", org_id).in_("po_id", ids[i:i + 200]).execute().data) or [])
        by_po = {}
        for l in lines:
            by_po.setdefault(l["po_id"], []).append(l)
        out = []
        for p in pos:
            for l in by_po.get(p["id"], []):
                remaining = _int(l.get("qty_ordered")) - _int(l.get("qty_received"))
                if remaining <= 0:
                    continue
                out.append({
                    "po_id": p["id"], "po_number": p["po_number"], "status": p["status"],
                    "ship_to_store": p.get("ship_to_store"), "market": p.get("market"),
                    "order_date": p.get("order_date"), "expected_delivery_date": p.get("expected_delivery_date"),
                    "vendor_name": p.get("vendor_name_snapshot"),
                    "po_line_id": l["id"], "sku": l.get("sku"), "device_model": l.get("device_model"),
                    "qty_ordered": l.get("qty_ordered"), "qty_received": l.get("qty_received"),
                    "remaining": remaining, "unit_cost": l.get("unit_cost"),
                })
        return {"migrated": True, "rows": out}
    except Exception as e:
        if _is_missing_schema_error(e):
            return {"migrated": False, "rows": [], "note": _MIGRATION_MSG}
        raise HTTPException(500, str(e))


@router.get("/po/{po_id}")
def get_po(po_id: str, org_id: str = ORG_ID):
    client = sb()
    try:
        h = (client.schema("commcalc").table("purchase_order").select("*")
             .eq("org_id", org_id).eq("id", po_id).limit(1).execute().data) or []
        if not h:
            raise HTTPException(404, "Purchase order not found.")
        header = h[0]
        lines = (client.schema("commcalc").table("purchase_order_line").select("*")
                 .eq("org_id", org_id).eq("po_id", po_id).order("line_no").execute().data) or []
        receipts = (client.schema("commcalc").table("po_receipt").select("*")
                    .eq("org_id", org_id).eq("po_id", po_id).order("received_date").execute().data) or []
        vendor = None
        if header.get("vendor_id"):
            v = (client.schema("commcalc").table("po_vendor").select("*")
                 .eq("org_id", org_id).eq("id", header["vendor_id"]).limit(1).execute().data) or []
            vendor = v[0] if v else None
        return {"migrated": True, "header": header, "lines": lines, "receipts": receipts, "vendor": vendor}
    except HTTPException:
        raise
    except Exception as e:
        if _is_missing_schema_error(e):
            raise HTTPException(400, _MIGRATION_MSG)
        raise HTTPException(500, str(e))


@router.patch("/po/{po_id}")
def update_po(po_id: str, body: UpdatePoIn, org_id: str = ORG_ID):
    client = sb()
    try:
        cur = (client.schema("commcalc").table("purchase_order").select("status")
               .eq("org_id", org_id).eq("id", po_id).limit(1).execute().data) or []
        if not cur:
            raise HTTPException(404, "Purchase order not found.")
        patch = {}
        if "status" in body.model_fields_set:
            _validate_status_transition(cur[0]["status"], body.status)
            patch["status"] = body.status
        for f in ("vendor_id", "order_date", "ship_to_store", "market", "buyer", "expected_delivery_date", "notes"):
            if f in body.model_fields_set:
                patch[f] = getattr(body, f)
        if not patch:
            return {"ok": True, "unchanged": True}
        patch["updated_at"] = datetime.now(timezone.utc).isoformat()
        client.schema("commcalc").table("purchase_order").update(patch).eq("org_id", org_id).eq("id", po_id).execute()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        if _is_missing_schema_error(e):
            raise HTTPException(400, _MIGRATION_MSG)
        raise HTTPException(500, str(e))


def _recompute_po_status(client, org_id, po_id):
    po = (client.schema("commcalc").table("purchase_order").select("status")
          .eq("org_id", org_id).eq("id", po_id).limit(1).execute().data) or []
    if not po or po[0].get("status") in ("closed", "cancelled"):
        return
    cur = po[0]["status"]
    lines = (client.schema("commcalc").table("purchase_order_line").select("qty_ordered,qty_received")
             .eq("org_id", org_id).eq("po_id", po_id).execute().data) or []
    ordered = sum(_int(l.get("qty_ordered")) for l in lines)
    received = sum(_int(l.get("qty_received")) for l in lines)
    new_status = cur
    if ordered > 0 and received > 0:
        new_status = "received" if received >= ordered else "partially_received"
    if new_status != cur:
        client.schema("commcalc").table("purchase_order").update(
            {"status": new_status, "updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("org_id", org_id).eq("id", po_id).execute()


@router.post("/po/{po_id}/receive")
def receive_po_line(po_id: str, body: ReceivePoLineIn, org_id: str = ORG_ID):
    """Receive against ONE PO line — qty + date, optional per-unit IMEI/serial capture. Partial receipts
    supported (call this again later against the same line for the remainder). Auto-advances PO status."""
    client = sb()
    po_line_id = body.po_line_id
    qty = _int(body.qty_received)
    if not po_line_id or qty <= 0:
        raise HTTPException(400, "po_line_id and a positive qty_received are required.")
    received_date = body.received_date or datetime.now(timezone.utc).date().isoformat()
    units = body.units or []
    try:
        line = (client.schema("commcalc").table("purchase_order_line").select("*")
                .eq("org_id", org_id).eq("id", po_line_id).eq("po_id", po_id).limit(1).execute().data) or []
        if not line:
            raise HTTPException(404, "PO line not found on this purchase order.")
        line = line[0]
        receipt = {
            "org_id": org_id, "po_id": po_id, "po_line_id": po_line_id,
            "received_date": received_date, "qty_received": qty,
            "received_by": body.received_by or None, "notes": body.notes or None,
        }
        rres = client.schema("commcalc").table("po_receipt").insert(receipt).execute()
        receipt_id = (rres.data or [{}])[0].get("id")
        urows = []
        for u in (units[:qty] if qty else units):
            imei = (u.get("imei") or "").strip() or None
            serial = (u.get("serial") or "").strip() or None
            if not imei and not serial:
                continue
            urows.append({"org_id": org_id, "receipt_id": receipt_id, "po_line_id": po_line_id,
                          "imei": imei, "serial": serial})
        if urows:
            client.schema("commcalc").table("po_receipt_unit").insert(urows).execute()
        new_qty_received = _int(line.get("qty_received")) + qty
        client.schema("commcalc").table("purchase_order_line").update(
            {"qty_received": new_qty_received}).eq("org_id", org_id).eq("id", po_line_id).execute()
        _recompute_po_status(client, org_id, po_id)
        return {"ok": True, "receipt_id": receipt_id, "line_qty_received": new_qty_received,
                "units_captured": len(urows)}
    except HTTPException:
        raise
    except Exception as e:
        if _is_missing_schema_error(e):
            raise HTTPException(400, _MIGRATION_MSG)
        raise HTTPException(500, str(e))


# ── Sold Tally + Aging shared unit-gathering ─────────────────────────────────────────────────────────────
def _po_settings_days(client, org_id):
    try:
        r = (client.schema("commcalc").table("po_settings").select("aging_flag_days")
             .eq("org_id", org_id).limit(1).execute().data) or []
        return _int(r[0].get("aging_flag_days"), 10) if r else 10
    except Exception:
        return 10


def _po_scope(client, org_id, store="", market="", po_id=""):
    q = (client.schema("commcalc").table("purchase_order")
         .select("id,po_number,ship_to_store,market,status").eq("org_id", org_id))
    if po_id:
        q = q.eq("id", po_id)
    if store:
        q = q.eq("ship_to_store", store)
    if market:
        q = q.eq("market", market)
    rows = q.execute().data or []
    return {r["id"]: r for r in rows}


def _gather_received_units(client, org_id, store="", market="", po_id=""):
    """Every received unit: EXACT (a captured imei/serial — qty=1 each) or ESTIMATED (a receipt's
    remaining qty with no serial captured — one bucketed row, qty=N). Never silently drops the
    unserialized remainder; callers must label confidence explicitly in the UI."""
    pos = _po_scope(client, org_id, store=store, market=market, po_id=po_id)
    if not pos:
        return []
    po_ids = list(pos.keys())
    lines = {}
    for i in range(0, len(po_ids), 200):
        for l in (client.schema("commcalc").table("purchase_order_line").select("*")
                  .eq("org_id", org_id).in_("po_id", po_ids[i:i + 200]).execute().data) or []:
            lines[l["id"]] = l
    if not lines:
        return []
    receipts = []
    for i in range(0, len(po_ids), 200):
        receipts.extend((client.schema("commcalc").table("po_receipt").select("*")
                         .eq("org_id", org_id).in_("po_id", po_ids[i:i + 200]).execute().data) or [])
    if not receipts:
        return []
    receipt_ids = [r["id"] for r in receipts]
    units_by_receipt = {}
    for i in range(0, len(receipt_ids), 200):
        for u in (client.schema("commcalc").table("po_receipt_unit").select("*")
                  .eq("org_id", org_id).in_("receipt_id", receipt_ids[i:i + 200]).execute().data) or []:
            units_by_receipt.setdefault(u["receipt_id"], []).append(u)

    out = []
    for r in receipts:
        line = lines.get(r.get("po_line_id"))
        po = pos.get(r.get("po_id"))
        if not line or not po:
            continue
        base = {
            "po_id": r.get("po_id"), "po_number": po.get("po_number"),
            "po_line_id": line["id"], "sku": line.get("sku"), "device_model": line.get("device_model"),
            "store": po.get("ship_to_store"), "market": po.get("market"),
            "received_date": r.get("received_date"),
        }
        serial_units = units_by_receipt.get(r["id"], [])
        for su in serial_units:
            imei = su.get("imei") or su.get("serial")
            out.append({**base, "imei": imei, "confidence": "exact" if imei else "estimated", "qty": 1})
        remainder = _int(r.get("qty_received")) - len(serial_units)
        if remainder > 0:
            out.append({**base, "imei": None, "confidence": "estimated", "qty": remainder})
    return out


def _sold_imei_set(client, org_id, imeis):
    want = {_norm_imei(i) for i in imeis if i}
    if not want:
        return set()
    cand = set()
    for i in imeis:
        if not i:
            continue
        cand.add(str(i).strip())
        n = _norm_imei(i)
        cand.add(n)
        cand.add(n + ".0")
    cand.discard("")
    cand = list(cand)
    found = set()
    for j in range(0, len(cand), 200):
        chunk = (client.schema("commcalc").table("raw_sales").select("serial_1,voided,trans_type")
                 .eq("org_id", org_id).in_("serial_1", cand[j:j + 200]).execute().data) or []
        for r in chunk:
            if str(r.get("voided") or "").upper() == "YES" or (r.get("trans_type") or "") == "Return":
                continue
            k = _norm_imei(r.get("serial_1"))
            if k in want:
                found.add(k)
    return found


def _commission_types(client, org_id):
    out = set()
    try:
        for r in (client.schema("commcalc").table("payment_categories").select("description,category")
                  .eq("org_id", org_id).execute().data or []):
            if (r.get("category") or "").strip().lower() == "commission":
                d = (r.get("description") or "").strip().lower()
                if d:
                    out.add(d)
    except Exception:
        pass
    return out


def _estimate_sold_split(client, org_id, est_units, exclude_imeis):
    """Coarse, qty-level estimate for units received WITHOUT a serial: per (store, canonical device
    model), compares total estimated-received qty since the earliest receive date in that group against
    device-line sales recorded in raw_sales for that store+model since then (excluding sales already
    claimed by an exact-serial match). Returns {(store, model_lower): (estimated_sold, estimated_unsold)}.
    Callers MUST label these rows 'estimated (no serial captured)' — never presented as an exact match."""
    if not est_units:
        return {}
    groups = {}
    for u in est_units:
        key = (u["store"] or "", (u["device_model"] or "").strip().lower())
        g = groups.setdefault(key, {"qty": 0, "min_date": u["received_date"]})
        g["qty"] += u["qty"]
        if u["received_date"] and (not g["min_date"] or u["received_date"] < g["min_date"]):
            g["min_date"] = u["received_date"]
    out = {}
    for (store, model_lower), g in groups.items():
        sold_count = 0
        try:
            page = 0
            while True:
                q = (client.schema("commcalc").table("raw_sales")
                     .select("serial_1,product_desc,department,category,voided,trans_type,trans_date")
                     .eq("org_id", org_id))
                if store:
                    q = q.eq("store", store)
                if g["min_date"]:
                    q = q.gte("trans_date", g["min_date"])
                chunk = q.range(page * 1000, page * 1000 + 999).execute().data or []
                for r in chunk:
                    if str(r.get("voided") or "").upper() == "YES" or (r.get("trans_type") or "") == "Return":
                        continue
                    if not _is_device_line(r.get("department"), r.get("category")):
                        continue
                    if str(r.get("product_desc") or "").split(" - ")[0].strip().lower() != model_lower:
                        continue
                    s1 = _norm_imei(r.get("serial_1"))
                    if s1 and s1 in exclude_imeis:
                        continue
                    sold_count += 1
                if len(chunk) < 1000:
                    break
                page += 1
                if page > 40:
                    break
        except Exception:
            sold_count = 0
        estimated_sold = min(g["qty"], sold_count)
        out[(store, model_lower)] = (estimated_sold, g["qty"] - estimated_sold)
    return out


@router.get("/po/tally")
def po_tally(store: str = "", market: str = "", po_id: str = "", org_id: str = ORG_ID):
    """Buckets received units into sold_with_commission / sold_no_commission / unsold. SOLD is matched by
    IMEI against raw_sales (exact) when a serial was captured at receiving, else by a qty-level
    store+model window estimate (explicitly labeled). COMMISSION RECEIVED is checked by IMEI against
    ePay raw_payment_detail (via the asset module's own _epay_payments_map — same join this module
    already uses for hotsheet-recon/appeals), filtered to payment_categories.category='Commission' when
    that classification table is configured; if it isn't, falls back to 'any payment on this IMEI' with
    commission_basis='any_payment_fallback' so the UI can show the caveat instead of a false-precise
    number. Unserialized (estimated) units can never confirm commission by IMEI — always
    commission_basis='unknown_no_serial'."""
    from app.modules.asset.router import _epay_payments_map  # lazy — see module docstring "IMPORT SHAPE"
    client = sb()
    try:
        units = _gather_received_units(client, org_id, store=store, market=market, po_id=po_id)
    except Exception as e:
        if _is_missing_schema_error(e):
            return {"migrated": False, "rows": [], "summary": {}, "note": _MIGRATION_MSG}
        raise HTTPException(500, str(e))
    if not units:
        return {"migrated": True, "rows": [],
                "summary": {"sold_with_commission": 0, "sold_no_commission": 0, "unsold": 0}}

    exact_units = [u for u in units if u["confidence"] == "exact"]
    imeis = [u["imei"] for u in exact_units]
    sold_set = _sold_imei_set(client, org_id, imeis) if imeis else set()
    epay_map = _epay_payments_map(client, org_id, imeis) if imeis else {}
    commission_types = _commission_types(client, org_id)

    est_units = [u for u in units if u["confidence"] == "estimated"]
    est_split = _estimate_sold_split(client, org_id, est_units, exclude_imeis=set(imeis))

    rows = []
    summary = {"sold_with_commission": 0, "sold_no_commission": 0, "unsold": 0}
    for u in exact_units:
        k = _norm_imei(u["imei"])
        sold = k in sold_set
        entries = epay_map.get(k, [])
        if commission_types:
            comm_amt = sum(e["amount"] for e in entries if (e.get("type") or "").strip().lower() in commission_types)
            basis = "category"
        else:
            comm_amt = sum(e["amount"] for e in entries)
            basis = "any_payment_fallback"
        if not sold:
            b = "unsold"
        elif comm_amt > 0:
            b = "sold_with_commission"
        else:
            b = "sold_no_commission"
        summary[b] += 1
        rows.append({**u, "sold": sold, "commission_amount": round(comm_amt, 2), "commission_basis": basis, "bucket": b})

    # Allocate each (store, model) group's estimated-sold pool across its estimated rows FIFO by receipt
    # (est_units is already in receipt order from _gather_received_units) — an unserialized unit has no
    # identity to match individually, so "which receipt's units sold" is inherently unknowable; crediting
    # the OLDEST receipt first is the standard, defensible inventory-aging assumption (oldest stock turns
    # over first), not an arbitrary pick. A later receipt's remainder is more likely to show as unsold —
    # correctly bleeding into the Aging report rather than the earliest one.
    for eu in est_units:
        key = (eu["store"] or "", (eu["device_model"] or "").strip().lower())
        sold_qty, unsold_qty = est_split.get(key, (0, eu["qty"]))
        take_sold = min(eu["qty"], sold_qty)
        take_unsold = eu["qty"] - take_sold
        est_split[key] = (sold_qty - take_sold, unsold_qty)
        if take_sold:
            summary["sold_no_commission"] += take_sold
            rows.append({**eu, "qty": take_sold, "sold": True, "commission_amount": None,
                        "commission_basis": "unknown_no_serial", "bucket": "sold_no_commission"})
        if take_unsold:
            summary["unsold"] += take_unsold
            rows.append({**eu, "qty": take_unsold, "sold": False, "commission_amount": None,
                        "commission_basis": "unknown_no_serial", "bucket": "unsold"})
    return {"migrated": True, "rows": rows, "summary": summary,
            "as_of": datetime.now(timezone.utc).date().isoformat()}


def _age_days(received_date, today):
    if not received_date:
        return None
    try:
        d = datetime.strptime(str(received_date)[:10], "%Y-%m-%d").date()
        return (today - d).days
    except Exception:
        return None


@router.get("/po/aging")
def po_aging(store: str = "", market: str = "", po_id: str = "", org_id: str = ORG_ID):
    """Unsold Inventory Aging — this module's OWN report (separate from /asset/aging, which is
    asset_ledger-based VIP consignment). received-but-unsold PO units aged from received_date, flagged
    when age_days > po_settings.aging_flag_days (management-configurable, default 10)."""
    client = sb()
    try:
        threshold = _po_settings_days(client, org_id)
        units = _gather_received_units(client, org_id, store=store, market=market, po_id=po_id)
    except Exception as e:
        if _is_missing_schema_error(e):
            return {"migrated": False, "rows": [], "threshold_days": 10, "flagged": 0, "total_unsold": 0, "note": _MIGRATION_MSG}
        raise HTTPException(500, str(e))
    if not units:
        return {"migrated": True, "rows": [], "threshold_days": threshold, "flagged": 0, "total_unsold": 0}

    exact_units = [u for u in units if u["confidence"] == "exact"]
    imeis = [u["imei"] for u in exact_units]
    sold_set = _sold_imei_set(client, org_id, imeis) if imeis else set()
    est_units = [u for u in units if u["confidence"] == "estimated"]
    est_split = _estimate_sold_split(client, org_id, est_units, exclude_imeis=set(imeis))

    today = datetime.now(timezone.utc).date()
    rows = []
    for u in exact_units:
        if _norm_imei(u["imei"]) in sold_set:
            continue
        age = _age_days(u["received_date"], today)
        rows.append({**u, "age_days": age, "flagged": age is not None and age > threshold})
    for eu in est_units:  # same FIFO-by-receipt pool allocation as po_tally (see its comment) — keeps the two reports consistent
        key = (eu["store"] or "", (eu["device_model"] or "").strip().lower())
        sold_qty, unsold_qty = est_split.get(key, (0, eu["qty"]))
        take_sold = min(eu["qty"], sold_qty)
        take_unsold = eu["qty"] - take_sold
        est_split[key] = (sold_qty - take_sold, unsold_qty)
        if take_unsold:
            age = _age_days(eu["received_date"], today)
            rows.append({**eu, "qty": take_unsold, "age_days": age, "flagged": age is not None and age > threshold})
    rows.sort(key=lambda r: -(r.get("age_days") or 0))
    flagged = sum((r.get("qty", 1) if r.get("flagged") else 0) for r in rows)
    total_unsold = sum(r.get("qty", 1) for r in rows)
    return {"migrated": True, "rows": rows, "threshold_days": threshold, "flagged": flagged, "total_unsold": total_unsold}
