"""POS special-order VENDOR-FACING API — the inbound direction ("our API to connect to them").

A dropship vendor registered as an `inbound_api` connector (pos.vendor_connector, mig 866) integrates
by POLLING these endpoints: it pulls its queued special orders and posts back status + tracking. This
is the mirror of the outbound direction (pos/vendor_adapters.py, where WE call THEIR API).

AUTH IS DELIBERATELY SEPARATE from the member-gated POS router. A vendor is not an org member and has no
login, so this router carries NO `_require_pos_access` dependency. Instead every request authenticates
with the vendor's own bearer token: `Authorization: Bearer <token>` → SHA-256 → the one active
`inbound_api` connector whose `inbound_token_hash` matches, which resolves the (org_id, vendor_key). A
vendor therefore sees and touches ONLY its own vendor_key's orders in its own org — nothing else.

Source-hiding still holds in reverse: the vendor sees only what it needs to fulfil (ship-to store +
address, item, quantity, a reference) — never the customer's PII, the declared sale price, our cost, or
the margin.
"""
from fastapi import APIRouter, Header, HTTPException

from app.core.database import get_supabase
from app.modules.pos.vendor_adapters import token_hash

router = APIRouter(prefix="/vendor-api", tags=["pos-vendor-api"])

# Statuses a vendor is allowed to SET. 'requested' is our pre-placement state and 'delivered' means the
# store handed the item to the customer — neither is the vendor's to assert.
_VENDOR_SETTABLE = ("ordered", "shipped", "received", "cancelled")


def _sb():
    return get_supabase()


def _connector_from_token(authorization: str) -> dict:
    """Resolve the caller to its inbound connector, or 401. Fails closed on any lookup fault."""
    tok = ""
    if authorization and authorization.lower().startswith("bearer "):
        tok = authorization[7:].strip()
    if not tok:
        raise HTTPException(401, "vendor access token required")
    try:
        rows = (_sb().schema("pos").table("vendor_connector").select("*")
                .eq("inbound_token_hash", token_hash(tok))
                .eq("integration_mode", "inbound_api").eq("is_active", True)
                .limit(1).execute().data) or []
    except Exception:
        raise HTTPException(401, "could not verify vendor access")
    if not rows:
        raise HTTPException(401, "invalid vendor access token")
    return rows[0]


@router.get("/orders")
def vendor_pull_orders(authorization: str = Header(default="")):
    """The vendor pulls its queued (not-yet-placed) special orders. Returns only fulfilment fields —
    no customer PII, no price/cost/margin."""
    conn = _connector_from_token(authorization)
    org_id, vkey = conn["org_id"], conn["vendor_key"]
    orders = (_sb().schema("pos").table("special_orders").select("*")
              .eq("org_id", org_id).eq("vendor", vkey).eq("status", "requested")
              .order("created_at").limit(200).execute().data) or []
    if not orders:
        return {"orders": []}
    # Ship-to store address (the vendor needs somewhere to ship) and the vendor SKU per product.
    stores = {s.get("store_code"): s.get("address")
              for s in ((_sb().schema("storeops").table("stores").select("store_code,address")
                         .eq("org_id", org_id).execute().data) or [])}
    pids = list({o.get("product_id") for o in orders if o.get("product_id")})
    skus = {}
    if pids:
        for v in ((_sb().schema("pos").table("special_order_vendor").select("product_id,vendor_sku")
                   .eq("org_id", org_id).in_("product_id", pids).execute().data) or []):
            skus[v.get("product_id")] = v.get("vendor_sku")
    out = []
    for o in orders:
        ship = o.get("ship_to_store") or o.get("store_code")
        out.append({"order_id": o.get("id"), "order_no": o.get("order_no"),
                    "vendor_sku": skus.get(o.get("product_id")),
                    "description": o.get("description"), "qty": o.get("qty"),
                    "ship_to_store": ship, "ship_to_address": stores.get(ship),
                    "created_at": o.get("created_at")})
    return {"orders": out}


@router.post("/orders/{order_id}/status")
def vendor_post_status(order_id: str, body: dict, authorization: str = Header(default="")):
    """The vendor posts back status ('ordered'|'shipped'|'received'|'cancelled'), plus optional
    tracking and its own order reference. Scoped to the vendor's own org + vendor_key."""
    conn = _connector_from_token(authorization)
    org_id, vkey = conn["org_id"], conn["vendor_key"]
    upd = {}
    st = str(body.get("status") or "").strip()
    if st:
        if st not in _VENDOR_SETTABLE:
            raise HTTPException(400, f"status must be one of {', '.join(_VENDOR_SETTABLE)}")
        upd["status"] = st
    for src, col in (("tracking", "tracking"), ("vendor_order_ref", "vendor_order_ref"),
                     ("order_ref", "vendor_order_ref")):
        if body.get(src):
            upd[col] = str(body[src])
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (_sb().schema("pos").table("special_orders").update(upd)
         .eq("org_id", org_id).eq("vendor", vkey).eq("id", order_id).execute())
    if not r.data:
        raise HTTPException(404, "order not found for this vendor")
    o = r.data[0]
    return {"order_id": o.get("id"), "status": o.get("status"),
            "tracking": o.get("tracking"), "vendor_order_ref": o.get("vendor_order_ref")}
