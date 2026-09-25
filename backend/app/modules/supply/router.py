"""Supply Ordering API — /api/v1/supply/*  (index §36, mig 1021; module key `supply_ordering`).

OWNER REQUEST 2026-09-25 (abridged): a store-operations dashboard with the comparison-prices module; the user
adds items to the cheapest vendor's cart from the platform and places the order; the order confirmation is
captured back and updated in the system; each vendor has a free-shipping threshold and an approximate
delivery time, both defined by the tenant when setting up the vendor.

DESIGN
  • Every decision is in `ordering_logic` (pure) / `pricing_core` (pure, THE one copy the kit also runs).
    This file is HTTP; `store` is the database; `portal` is the browser. Proven by harness_supply_ordering.py.
  • Gated: every endpoint depends on require_module("supply_ordering"). The module's vertical scope is DATA
    (core.module_catalog.applies_to_vertical, mig 1020) — no vertical or vendor is spelled here.
  • org_id is a QUERY PARAM on every endpoint (the tenant middleware rewrites it from the JWT); every read
    filters it and every insert stamps it (store.py) — harness_org_scope_guard.py scans this package.
  • REUSED, never re-built: the vendor roster (commcalc.po_vendor), the PO + lines + numbering + lifecycle
    (mig 301 / asset.purchase_orders), the login + its secrets (commcalc.data_source through commcalc's own
    save_data_source — SSRF guard, import-admin gate, write-only password), the live browser
    (commcalc/live_login.start_session + the commcalc /data-sources/{sid}/live-login/{state,frame,input,cancel}
    stream the page renders), the scheduled read (commcalc /data-sources/sweep/run-due → _SOURCE_SCRAPERS).
  • A missing migration degrades to a named message, never a 500 that takes the page down.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile

from app.core.database import get_supabase
from app.core.schemas import LaxModel
from app.modules.core.entitlements import require_module
from app.modules.supply import ordering_logic as L
from app.modules.supply import pricing_core as core
from app.modules.supply import store

router = APIRouter(prefix="/supply", tags=["Supply Ordering"],
                   dependencies=[Depends(require_module("supply_ordering"))])

ORG_ID = "00000000-0000-0000-0000-000000000001"   # house org; middleware rewrites the query param
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def sb():
    return get_supabase()


def _who(authorization: str) -> str | None:
    """Display name of the signed-in user (the same core primitives every module resolves callers with)."""
    try:
        from app.modules.core.router import _uid_from_token
        uid = _uid_from_token(authorization)
        if not uid:
            return None
        try:
            from app.core.tenant_middleware import caller_app_user
            u = caller_app_user(uid, "id,full_name,email") or {}
        except Exception:
            u = {}
        return str(u.get("full_name") or u.get("email") or uid)[:120]
    except Exception:
        return None


def _require_vendor_admin(authorization: str, org_id: str):
    """Vendor setup is the Purchase Orders vendor roster — the SAME admin gate (asset.purchase_orders)."""
    from app.modules.asset.purchase_orders import _require_po_admin
    _require_po_admin(authorization, org_id)


def _migration_guard(e: Exception):
    if isinstance(e, HTTPException):
        raise e
    if store.missing_schema(e):
        raise HTTPException(400, store.MIGRATION_MSG)
    raise HTTPException(500, str(e)[:400])


def _vendor_public(v, login=None, last_seen=None):
    out = dict(v)
    rec, rerr = L.parse_recipe((v.get("portal_config") or {}).get("ordering"), L.vendor_hosts(v))
    out["recipe"] = L.recipe_status(rec)
    out["recipe_errors"] = rerr
    out["login"] = login
    out["catalog_seen_at"] = last_seen
    out["attention"] = L.vendor_attention(v, login, last_seen)
    return out


# ══ VENDORS ══════════════════════════════════════════════════════════════════════════════════════════
class VendorIn(LaxModel):
    name: Any = None
    contact_name: Any = None
    email: Any = None
    phone: Any = None
    terms: Any = None
    notes: Any = None
    portal_url: Any = None
    catalog_urls: Any = None
    portal_config: Any = None
    free_shipping_threshold: Any = None
    shipping_fee_below_threshold: Any = None
    delivery_days_min: Any = None
    delivery_days_max: Any = None
    is_price_source: Any = None
    is_active: Any = None


@router.get("/vendors")
def list_vendors(active_only: bool = False, org_id: str = ORG_ID):
    client = sb()
    try:
        vendors = store.load_vendors(client, org_id, active_only=active_only)
    except Exception as e:
        if store.missing_schema(e):
            return {"migrated": False, "rows": [], "note": store.MIGRATION_MSG}
        raise HTTPException(500, str(e)[:400])
    logins = store.load_logins(client, org_id, [v.get("data_source_id") for v in vendors])
    seen = {}
    for v in vendors:
        try:
            lr = store.latest_run(client, org_id, v["id"])
            seen[v["id"]] = (lr or {}).get("seen_at")
        except Exception:
            seen[v["id"]] = None
    return {"migrated": True, "processor": _processor(),
            "rows": [_vendor_public(v, logins.get(v.get("data_source_id")), seen.get(v["id"])) for v in vendors]}


def _processor():
    from app.modules.supply.portal import SUPPLY_PROCESSOR
    return SUPPLY_PROCESSOR


@router.post("/vendors")
def create_vendor(body: VendorIn, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_vendor_admin(authorization, org_id)
    raw = {k: getattr(body, k) for k in body.model_fields_set}
    clean, errors = L.validate_vendor(raw)
    if errors:
        raise HTTPException(400, " ".join(errors))
    client = sb()
    try:
        dup = [v for v in store.load_vendors(client, org_id) if (v.get("name") or "").lower() == clean["name"].lower()]
        if dup:
            raise HTTPException(400, f"A vendor named '{clean['name']}' already exists — edit it instead.")
        clean["is_active"] = True
        return {"ok": True, "vendor": store.insert_vendor(client, org_id, clean)}
    except Exception as e:
        _migration_guard(e)


@router.patch("/vendors/{vendor_id}")
def update_vendor(vendor_id: str, body: VendorIn, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_vendor_admin(authorization, org_id)
    client = sb()
    try:
        cur = store.vendor_by_id(client, org_id, vendor_id)
        if not cur:
            raise HTTPException(404, "Vendor not found.")
        raw = {k: getattr(body, k) for k in body.model_fields_set if k != "is_active"}
        clean, errors = L.validate_vendor(raw, existing=cur)
        if errors:
            raise HTTPException(400, " ".join(errors))
        if "is_active" in body.model_fields_set:
            clean["is_active"] = bool(body.is_active)
        if clean:
            store.update_vendor(client, org_id, vendor_id, clean)
        return {"ok": True}
    except Exception as e:
        _migration_guard(e)


class VendorLoginIn(LaxModel):
    username: Any = None
    password: Any = None
    account_id: Any = None
    portal_url: Any = None
    proxy_url: Any = None
    enabled: Any = None          # scheduled catalog read on/off (the existing data-sources scheduler)
    frequency: Any = None
    hour: Any = None


@router.put("/vendors/{vendor_id}/login")
def save_vendor_login(vendor_id: str, body: VendorLoginIn, authorization: str = Header(default=""),
                      org_id: str = ORG_ID):
    """The vendor's portal login = a commcalc.data_source row, written by commcalc's OWN save_data_source
    (SSRF guard on the address, import-admin gate, blank password keeps the saved one, secrets never
    returned). This endpoint only fills in the supply defaults and links the row to the vendor."""
    from app.modules.commcalc.router import SaveDataSourceIn, save_data_source
    client = sb()
    try:
        v = store.vendor_by_id(client, org_id, vendor_id)
    except Exception as e:
        _migration_guard(e)
    if not v:
        raise HTTPException(404, "Vendor not found.")
    payload = {"processor": _processor(), "label": f"Supply vendor — {v.get('name')}"[:120],
               "portal_url": body.portal_url or v.get("portal_url") or ((v.get("portal_config") or {}).get("login") or {}).get("url")}
    for k in ("username", "password", "account_id", "proxy_url", "frequency", "hour"):
        if k in body.model_fields_set:
            payload[k] = getattr(body, k)
    payload["enabled"] = bool(body.enabled) if "enabled" in body.model_fields_set else False
    if v.get("data_source_id"):
        payload["id"] = v["data_source_id"]
    if not payload.get("portal_url"):
        raise HTTPException(400, "Set the vendor's portal (login page) URL first.")
    res = save_data_source(SaveDataSourceIn(**payload), org_id=org_id, authorization=authorization)
    sid = (res or {}).get("id")
    if sid and sid != v.get("data_source_id"):
        store.update_vendor(client, org_id, vendor_id, {"data_source_id": sid})
    return {"ok": True, "data_source_id": sid,
            "login": store.load_logins(client, org_id, [sid]).get(sid)}


@router.post("/vendors/{vendor_id}/catalog/read")
def read_vendor_catalog(vendor_id: str, confirm: bool = False, org_id: str = ORG_ID):
    """Read this vendor's catalog NOW through the EXISTING live-login path: commcalc's
    /data-sources/{sid}/live-login/start (route gate, cooldown, SERVICE_ROLE/BROWSER_SERVICE_URL handling),
    whose post-login pull dispatches a supply login to portal.catalog_pull_on_page. The page streams the
    session from the commcalc live-login endpoints. If this deploy cannot launch a browser, that endpoint
    says so (503 with the reason) — nothing here pretends a read happened."""
    from app.core.service_role import require_browser_service
    require_browser_service()
    client = sb()
    try:
        v = store.vendor_by_id(client, org_id, vendor_id)
    except Exception as e:
        _migration_guard(e)
    if not v:
        raise HTTPException(404, "Vendor not found.")
    if not v.get("data_source_id"):
        raise HTTPException(400, "Save this vendor's portal login first (Supply → Vendors → Login).")
    if not L.vendor_scrape_config(v).get("start_urls"):
        raise HTTPException(400, "Add the vendor's catalog links first.")
    from app.modules.commcalc.router import live_login_start
    res = live_login_start(v["data_source_id"], org_id=org_id, confirm=confirm)
    return {"sid": v["data_source_id"], **(res or {})}


@router.get("/vendors/{vendor_id}/catalog")
def vendor_catalog(vendor_id: str, limit: int = 500, org_id: str = ORG_ID):
    client = sb()
    try:
        rows, seen = store.latest_rows(client, org_id, [vendor_id])
    except Exception as e:
        if store.missing_schema(e):
            return {"migrated": False, "rows": [], "note": store.MIGRATION_MSG}
        raise HTTPException(500, str(e)[:400])
    rows.sort(key=lambda r: (r.get("name") or "").lower())
    return {"migrated": True, "seen_at": seen.get(vendor_id), "total": len(rows), "rows": rows[:max(1, min(limit, 5000))]}


# ══ CATALOG UPLOAD (route a — the kit's products.json / products.csv; no browser needed) ═════════════
@router.post("/catalog/upload")
async def catalog_upload(file: UploadFile = File(...), vendor_id: str = Form(default=""), org_id: str = ORG_ID,
                         authorization: str = Header(default="")):
    """Land the kit's products file. Each row's `vendor` key maps to a vendor (portal_config.key, else the
    name); `vendor_id` forces every row onto one vendor. Unmapped keys are REPORTED, never guessed."""
    _require_vendor_admin(authorization, org_id)
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "That file is larger than 20 MB.")
    try:
        rows = L.parse_products_upload(file.filename, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    client = sb()
    try:
        vendors = store.load_vendors(client, org_id)
    except Exception as e:
        _migration_guard(e)
    ids = {v["id"] for v in vendors}
    buckets, unmapped = {}, {}
    for r in rows:
        vid = vendor_id if vendor_id else L.map_kit_vendor(r.get("vendor"), vendors)
        if not vid or vid not in ids:
            k = str(r.get("vendor") or "(blank)")
            unmapped[k] = unmapped.get(k, 0) + 1
            continue
        buckets.setdefault(vid, []).append(r)
    landed = []
    try:
        for vid, vrows in buckets.items():
            res = store.land_catalog(client, org_id, vid, vrows, source="upload")
            landed.append({"vendor_id": vid, **{k: res[k] for k in ("rows_ingested", "rejected", "rejects", "run_id")}})
    except Exception as e:
        _migration_guard(e)
    return {"ok": bool(landed), "landed": landed,
            "unmapped": [{"vendor_key": k, "rows": n} for k, n in sorted(unmapped.items())],
            "note": ("Rows whose vendor key matches no vendor were not landed — set that key in the vendor's "
                     "portal config ('key') or pick the vendor on upload." if unmapped else None)}


# ══ COMPARE ══════════════════════════════════════════════════════════════════════════════════════════
@router.get("/compare")
def compare(q: str = "", only_compared: bool = False, limit: int = 1000, org_id: str = ORG_ID):
    """The newest catalog snapshot of every active vendor, grouped into "the same product" across vendors
    (pricing_core.group_products) and compared (pricing_core.comparison_rows: per-unit when every member
    states its pack, out of stock never wins, a cheaper out-of-stock option is SAID)."""
    client = sb()
    try:
        vendors = store.load_vendors(client, org_id, active_only=True)
        rows, seen = store.latest_rows(client, org_id, [v["id"] for v in vendors])
    except Exception as e:
        if store.missing_schema(e):
            return {"migrated": False, "rows": [], "vendors": [], "note": store.MIGRATION_MSG}
        raise HTTPException(500, str(e)[:400])
    products = [L.offer_from_row(r) for r in rows]
    if q.strip():
        products = [p for p in products if core.search_score(q, p) >= 0.5 or q.strip().lower() in (p["name"] or "").lower()]
    vids = [v["id"] for v in vendors]
    groups = core.group_products(products)
    compared, singles = core.comparison_rows(groups, vids)
    names = {v["id"]: v.get("name") for v in vendors}

    def _offer(p):
        if not p:
            return None
        return {k: p.get(k) for k in ("row_id", "name", "sku", "price", "list_price", "pack_qty", "availability",
                                      "stock_qty", "url")} | {"unit_price": core.unit_price(p)}

    out = []
    for r in compared:
        out.append({"product": r["product"], "match": r["match"], "match_method": r["match_method"],
                    "basis": r["basis"], "best_vendor": r["best_vendor"], "best_price": r["best_price"],
                    "next_price": r["next_price"], "savings": r["savings"], "savings_pct": r["savings_pct"],
                    "note": r["note"], "offers": {vid: _offer(r.get(vid)) for vid in vids if r.get(vid)}})
    if not only_compared:
        for p in singles:
            out.append({"product": p["name"], "match": "only one vendor", "match_method": "", "basis": "listed price",
                        "best_vendor": p["vendor"] if p.get("availability") != "out_of_stock" else None,
                        "best_price": p.get("price"), "next_price": None, "savings": None, "savings_pct": None,
                        "note": "", "offers": {p["vendor"]: _offer(p)}})
    return {"migrated": True, "vendors": [{"id": v["id"], "name": names[v["id"]], "catalog_seen_at": seen.get(v["id"])}
                                          for v in vendors],
            "total": len(out), "compared": len(compared), "rows": out[:max(1, min(limit, 5000))]}


# ══ CART ═════════════════════════════════════════════════════════════════════════════════════════════
class CartIn(LaxModel):
    items: Any = None                 # [{key, label, qty, offer_ids: [catalog row ids]}]
    max_delivery_days: Any = None
    allow_backorder: Any = None
    allow_unknown: Any = None


def _plan(client, org_id, body: CartIn):
    items_in = [i for i in (body.items or []) if isinstance(i, dict)]
    if not items_in:
        raise HTTPException(400, "The cart is empty — add items from Price Compare.")
    ids = sorted({oid for it in items_in for oid in (it.get("offer_ids") or []) if oid})
    rows = store.catalog_rows_by_ids(client, org_id, ids)
    by_id = {r["id"]: r for r in rows}
    vendors = store.load_vendors(client, org_id, ids={r["vendor_id"] for r in rows}, active_only=True)
    items = [{"key": it.get("key") or str(n), "label": it.get("label"), "qty": it.get("qty"), "basis": it.get("basis"),
              "offers": [L.offer_from_row(by_id[o]) for o in (it.get("offer_ids") or []) if o in by_id]}
             for n, it in enumerate(items_in)]
    terms = {v["id"]: v for v in vendors}
    plan = L.optimize_cart(items, terms, max_delivery_days=body.max_delivery_days,
                           allow_backorder=bool(body.allow_backorder),
                           allow_unknown=body.allow_unknown is not False)
    for vb in plan["vendors"]:
        vb["recipe"] = L.recipe_status(L.parse_recipe((terms[vb["vendor"]].get("portal_config") or {}).get("ordering"))[0])
    return plan


@router.post("/cart/optimize")
def cart_optimize(body: CartIn, org_id: str = ORG_ID):
    client = sb()
    try:
        return {"ok": True, "plan": _plan(client, org_id, body)}
    except Exception as e:
        _migration_guard(e)


@router.post("/cart/place")
def cart_place(body: CartIn, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Turn the cart into ONE purchase_order per vendor (mig-301 PO + lines, numbered by next_po_number,
    status draft, source supply_cart). The plan is RE-COMPUTED here from stored prices — a client-sent plan
    is never trusted. Placing at the vendor is the next, human-assisted step (open-session)."""
    client = sb()
    who = _who(authorization)
    try:
        plan = _plan(client, org_id, body)
        if not plan["vendors"]:
            raise HTTPException(400, "Nothing in the cart can be ordered: " +
                                "; ".join(f"{u['label']}: {u['reason']}" for u in plan["unfillable"])[:400])
        cart_ref = str(uuid.uuid4())
        orders = [store.create_order(client, org_id, d, cart_ref, who) for d in L.po_drafts_from_plan(plan)]
        return {"ok": True, "cart_ref": cart_ref, "orders": orders, "plan": plan}
    except Exception as e:
        _migration_guard(e)


# ══ ORDERS ═══════════════════════════════════════════════════════════════════════════════════════════
@router.get("/orders")
def list_orders(status: str = "", org_id: str = ORG_ID):
    client = sb()
    try:
        return {"migrated": True, "rows": store.list_orders(client, org_id, status or None)}
    except Exception as e:
        if store.missing_schema(e):
            return {"migrated": False, "rows": [], "note": store.MIGRATION_MSG}
        raise HTTPException(500, str(e)[:400])


@router.get("/orders/{po_id}")
def get_order(po_id: str, org_id: str = ORG_ID):
    client = sb()
    try:
        head, lines = store.order_detail(client, org_id, po_id)
    except Exception as e:
        _migration_guard(e)
    if not head:
        raise HTTPException(404, "Supply order not found.")
    vendor = store.vendor_by_id(client, org_id, head.get("vendor_id")) if head.get("vendor_id") else None
    rec = L.parse_recipe(((vendor or {}).get("portal_config") or {}).get("ordering"))[0]
    from app.modules.supply import portal
    live = None
    if vendor and vendor.get("data_source_id"):
        from app.modules.commcalc import live_login
        st = portal.order_state(org_id, vendor["data_source_id"])
        sess = live_login.get_session(vendor["data_source_id"], org_id)
        alive = bool(sess is not None and not sess.is_terminal())
        live = {"sid": vendor["data_source_id"], "for_this_order": bool(alive and st and st.get("po_id") == po_id),
                "mode": (st or {}).get("mode")}
    return {"header": head, "lines": lines, "vendor": _vendor_public(vendor) if vendor else None,
            "recipe": L.recipe_status(rec), "can_submit": bool(rec.get("submit_steps")), "live": live}


def _order_and_vendor(client, org_id, po_id):
    head, lines = store.order_detail(client, org_id, po_id)
    if not head:
        raise HTTPException(404, "Supply order not found.")
    vendor = store.vendor_by_id(client, org_id, head.get("vendor_id")) if head.get("vendor_id") else None
    if not vendor:
        raise HTTPException(400, "This order's vendor no longer exists.")
    return head, lines, vendor


@router.post("/orders/{po_id}/open-session")
def open_order_session(po_id: str, confirm: bool = False, org_id: str = ORG_ID):
    """Open the ASSISTED order session: REUSES commcalc/live_login.start_session on the vendor's login row,
    with a post-login action (portal.order_session) that adds every line to the vendor's cart by the vendor's
    recipe, opens the review page and captures the vendor's cart total + screenshot onto this PO. It never
    submits. Stream it from the commcalc /data-sources/{sid}/live-login/{frame,input,state,cancel} endpoints."""
    from app.core.service_role import require_browser_service
    require_browser_service()
    client = sb()
    try:
        head, lines, vendor = _order_and_vendor(client, org_id, po_id)
    except Exception as e:
        _migration_guard(e)
    if head.get("status") not in ("draft", "submitted"):
        raise HTTPException(400, f"This order is {head.get('status')} — nothing to place.")
    sid = vendor.get("data_source_id")
    if not sid:
        raise HTTPException(400, "Save this vendor's portal login first (Supply → Vendors → Login) — or place the "
                                 "order on the vendor's site and type the confirmation number here.")
    from app.modules.commcalc import live_login
    from app.modules.commcalc import router as cc
    from app.modules.supply import portal
    src = store.login_row_full(client, org_id, sid)
    if not src:
        raise HTTPException(400, "The vendor's login row is missing — save the login again.")
    pol = cc._source_route_policy(client, org_id, src)
    if cc._route_closed(pol):
        return cc._crp().refusal(pol, {"phase": "route_disabled"})
    if not (src.get("password") and (src.get("username") or src.get("account_id"))):
        raise HTTPException(400, "The vendor's login has no user id / password yet.")
    cool = cc._source_cooldown(client, sid, org_id, row=src)
    if cool.get("blocked") and not confirm:
        return cc._blocked_payload(cool, {"phase": "blocked"})
    meta_lines = (head.get("supply_meta") or {}).get("lines") or [
        {"line_no": ln.get("line_no"), "sku": ln.get("sku"), "name": ln.get("device_model"), "qty": ln.get("qty_ordered"),
         "url": None} for ln in lines]
    pull_fn, state = portal.order_session(client, org_id, vendor, po_id, meta_lines)
    row = dict(src)
    row["auto_pull_after_login"] = True          # the cart build IS the post-login action of this session
    live_login.start_session(sid, org_id, row, cc._live_persist(client, sid, org_id),
                             cc._live_persist_shot(client, sid, org_id), pull_fn,
                             portal.cart_persist(client, org_id, po_id))
    portal.register_order_session(org_id, sid, state)
    rs = L.recipe_status(state["recipe"])
    return {"ok": True, "sid": sid, "recipe": rs, "can_submit": bool(state["recipe"].get("submit_steps")),
            "message": ("Signing in to the vendor in a live window. " + rs["text"] +
                        " Nothing is ordered until you place it.")}


def _live_pull(org_id, sid, po_id, mode, timeout=240):
    from app.modules.commcalc import live_login
    from app.modules.supply import portal
    st = portal.order_state(org_id, sid)
    sess = live_login.get_session(sid, org_id)
    if not st or st.get("po_id") != po_id or sess is None:
        raise HTTPException(400, "No live vendor session is open for this order — press 'Open vendor session' "
                                 "(or type the confirmation number).")
    if not sess.can_pull():
        raise HTTPException(409, "The vendor session is not signed in yet (or has closed) — finish the sign-in in "
                                 "the live window first.")
    st["mode"] = mode
    res = sess.run_pull_blocking(timeout)
    if res is None:
        raise HTTPException(504, "The vendor session did not answer in time — look at the live window.")
    return res


@router.post("/orders/{po_id}/capture")
def capture_confirmation(po_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Read the page the live vendor window is on NOW. When it is the vendor's confirmation page (the
    configured pattern), the order number / total / screenshot are written back and the PO becomes
    submitted. When it is not, nothing is written and the answer says so."""
    from app.core.service_role import require_browser_service
    require_browser_service()
    client = sb()
    try:
        head, _lines, vendor = _order_and_vendor(client, org_id, po_id)
    except Exception as e:
        _migration_guard(e)
    res = _live_pull(org_id, vendor.get("data_source_id"), po_id, "capture")
    return _write_capture(client, org_id, po_id, res, authorization)


@router.post("/orders/{po_id}/submit")
def submit_order(po_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The HUMAN's explicit Confirm after seeing the captured vendor total. Runs the vendor's configured
    submit steps — refused when the recipe has none (the human then clicks Place order in the live window)."""
    from app.core.service_role import require_browser_service
    require_browser_service()
    client = sb()
    try:
        head, _lines, vendor = _order_and_vendor(client, org_id, po_id)
    except Exception as e:
        _migration_guard(e)
    rec = L.parse_recipe((vendor.get("portal_config") or {}).get("ordering"), L.vendor_hosts(vendor))[0]
    if not rec.get("submit_steps"):
        raise HTTPException(400, "This vendor has no submit step configured — press the vendor's Place-order button "
                                 "in the live window, then Capture confirmation.")
    if not (head.get("cart_evidence") or {}).get("captured_at"):
        raise HTTPException(400, "The vendor cart has not been captured yet — wait for the cart review, check the "
                                 "vendor's total, then confirm.")
    res = _live_pull(org_id, vendor.get("data_source_id"), po_id, "submit")
    return _write_capture(client, org_id, po_id, res, authorization)


def _write_capture(client, org_id, po_id, res, authorization):
    if not isinstance(res, dict) or res.get("ok") is False:
        return {"ok": False, "confirmed": False, "message": (res or {}).get("error") or "The session could not read the page."}
    if not res.get("confirmed"):
        return {"ok": True, "confirmed": False, "message": res.get("status"), "page_url": res.get("page_url"),
                "shot": res.get("shot")}
    from datetime import datetime, timezone
    who = _who(authorization)
    ev = {"method": "live_" + (res.get("mode") or "capture"), "order_ref": res.get("order_ref"),
          "total": res.get("total"), "matched": res.get("matched"), "page_url": res.get("page_url"),
          "shot": res.get("shot"), "captured_by": who, "captured_at": datetime.now(timezone.utc).isoformat()}
    patch = store.record_confirmation(client, org_id, po_id, ev, who)
    return {"ok": True, "confirmed": True, "order_ref": ev["order_ref"], "total": ev["total"],
            "needs_ref": not ev["order_ref"], "status": (patch or {}).get("status"), "message": res.get("status")}


class ManualConfirmIn(LaxModel):
    vendor_order_ref: Any = None
    vendor_order_total: Any = None
    note: Any = None


@router.post("/orders/{po_id}/confirm-manual")
def confirm_manual(po_id: str, body: ManualConfirmIn, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Always-available fallback: type the vendor's confirmation number (placed by phone, on the vendor
    site, or when the live capture could not read it). SEAM: an emailed confirmation parsed from the
    tenant mailbox would land here with method='email' — not built."""
    who = _who(authorization)
    ev, errs = L.manual_confirmation(body.vendor_order_ref, body.vendor_order_total, body.note, who)
    if errs:
        raise HTTPException(400, " ".join(errs))
    client = sb()
    try:
        patch = store.record_confirmation(client, org_id, po_id, ev, who)
    except Exception as e:
        _migration_guard(e)
    if patch is None:
        raise HTTPException(404, "Supply order not found.")
    return {"ok": True, "status": patch.get("status"), "order_ref": ev["order_ref"]}


class StatusIn(LaxModel):
    status: Any = None


@router.post("/orders/{po_id}/status")
def order_status(po_id: str, body: StatusIn, org_id: str = ORG_ID):
    """Move a supply PO through the SAME mig-301 lifecycle (asset.purchase_orders._validate_status_transition)
    — cancel a draft, close a received one. Receiving stays on the Purchase Orders receiving screen."""
    client = sb()
    try:
        st = store.set_status(client, org_id, po_id, str(body.status or "").strip())
    except Exception as e:
        _migration_guard(e)
    if st is None:
        raise HTTPException(404, "Supply order not found.")
    return {"ok": True, "status": st}


# ══ SUMMARY (the store-operations dashboard tiles) ═══════════════════════════════════════════════════
@router.get("/summary")
def summary(org_id: str = ORG_ID):
    """open orders · spend this month · savings this month · vendors needing attention."""
    client = sb()
    try:
        orders = store.list_orders(client, org_id, limit=2000)
        vendors = store.load_vendors(client, org_id, active_only=True)
    except Exception as e:
        if store.missing_schema(e):
            return {"migrated": False, "note": store.MIGRATION_MSG, "open_orders": 0, "spend_mtd": 0.0,
                    "savings_mtd": 0.0, "vendors_needing_attention": 0, "attention": []}
        raise HTTPException(500, str(e)[:400])
    logins = store.load_logins(client, org_id, [v.get("data_source_id") for v in vendors])
    att = {}
    for v in vendors:
        lr = None
        try:
            lr = store.latest_run(client, org_id, v["id"])
        except Exception:
            pass
        att[v["id"]] = L.vendor_attention(v, logins.get(v.get("data_source_id")), (lr or {}).get("seen_at"))
    tiles = L.summary_tiles(orders, att)
    names = {v["id"]: v.get("name") for v in vendors}
    tiles["attention"] = [{"vendor_id": vid, "vendor_name": names.get(vid),
                           "reasons": [r["text"] for r in att[vid] if r["severity"] == "warn"]}
                          for vid in tiles.pop("vendor_ids_needing_attention")]
    tiles["migrated"] = True
    tiles["links"] = {"orders": "/supply/orders", "vendors": "/supply/vendors", "compare": "/supply/compare",
                      "cart": "/supply/cart"}
    return tiles
