"""POS module — Phase 0: product catalog. Phase 1: customers (+ encrypted PII), inventory,
sales/checkout (atomic pos.checkout RPC), register drawer sessions, POS config kv, tax codes,
receipt templates (mig 724 + 725).

The POS-inside-MetricsPro port (see pos-system INTEGRATION_PLAN.md). Identity comes from the
platform: employees are storeops.employees (TEXT employee_id business key), stores are
storeops.stores.store_code, RBAC is the roles JSONB `modules.pos` key. This router owns the
pos.* schema. Later phases add activations, vendors/POs, transfers, reports, import.

Gated actions (PII reveal, void, settings/tax writes) use fine-grained keys in the caller role's
permissions JSONB — `pos_void`, `pos_settings` — with role scope 'all' (org-wide
admin) implying all three, so existing admin roles work before the roles UI grows checkboxes.

NOT YET ENFORCED: a `modules.pos` entitlement gate. Earlier revisions of this docstring claimed
one existed; no code read `modules`, so POS visibility was enforced only client-side in rbac.ts.
Adding it server-side has to land together with seeding `pos` into MODULE_CATALOG and the roles
UI, or every existing role — none of which carries a `pos` key — would be locked out at once.
"""
from fastapi import APIRouter, Depends, Header, HTTPException

from app.core.database import get_supabase

ORG_ID = "00000000-0000-0000-0000-000000000001"


def _require_pos_access(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Router-wide gate: every POS endpoint requires a signed-in member of the org.

    Applied as an APIRouter dependency rather than per-endpoint on purpose. 34 of this module's
    endpoints had no `authorization` parameter at all — so no check was even possible at the
    handler — including POST /import/{entity}, which bulk-inserts up to 5000 rows, and which
    rbac.ts restricted to scope 'all' in the UI only. A router-level dependency cannot be
    forgotten when endpoint 35 is added, which is the property that actually matters here: the
    per-endpoint convention had already been forgotten 34 times.

    Finer-grained rights (PII reveal, void, settings writes, inventory adjust/receive, activation
    cancel) still gate individually on top of this via _require_pos_perm."""
    _require_member(authorization, org_id)


router = APIRouter(prefix="/pos", tags=["pos"],
                   dependencies=[Depends(_require_pos_access)])

PRODUCT_FIELDS = ("upc", "short_name", "full_name", "department_id", "category_id",
                  "system_category", "inventory_type", "manufacturer", "cost", "retail_price",
                  "msrp", "is_taxable", "calculate_as_profit", "body_style", "is_active",
                  "end_of_life")


def sb():
    return get_supabase()


def _clean(body: dict, fields=PRODUCT_FIELDS) -> dict:
    """Whitelist writable columns; '' → None for the UUID/optional columns."""
    out = {k: body[k] for k in fields if k in body}
    for k in ("department_id", "category_id", "upc", "full_name", "manufacturer", "body_style",
              "system_category", "msrp"):
        if k in out and out[k] in ("", "Not Set"):
            out[k] = None
    return out


# ── Catalog (departments + categories, loaded once per page) ───────────────────────────────────────
@router.get("/catalog")
def catalog(org_id: str = ORG_ID):
    client = sb()
    depts = (client.schema("pos").table("departments").select("*")
             .eq("org_id", org_id).order("short_name").limit(500).execute().data) or []
    cats = (client.schema("pos").table("categories").select("*")
            .eq("org_id", org_id).order("name").limit(1000).execute().data) or []
    return {"departments": depts, "categories": cats}


@router.post("/departments")
def create_department(body: dict, org_id: str = ORG_ID):
    name = (body.get("short_name") or "").strip()
    if not name:
        raise HTTPException(400, "short_name required")
    r = sb().schema("pos").table("departments").insert(
        {"org_id": org_id, "short_name": name, "full_name": (body.get("full_name") or name).strip()}
    ).execute()
    return {"department": (r.data or [{}])[0]}


@router.patch("/departments/{dept_id}")
def update_department(dept_id: str, body: dict, org_id: str = ORG_ID):
    upd = _clean(body, ("short_name", "full_name", "is_active"))
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (sb().schema("pos").table("departments").update(upd)
         .eq("org_id", org_id).eq("id", dept_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"department": r.data[0]}


@router.post("/categories")
def create_category(body: dict, org_id: str = ORG_ID):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    r = sb().schema("pos").table("categories").insert(
        {"org_id": org_id, "name": name, "department_id": body.get("department_id") or None}
    ).execute()
    return {"category": (r.data or [{}])[0]}


@router.patch("/categories/{cat_id}")
def update_category(cat_id: str, body: dict, org_id: str = ORG_ID):
    upd = _clean(body, ("name", "department_id", "is_active"))
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (sb().schema("pos").table("categories").update(upd)
         .eq("org_id", org_id).eq("id", cat_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"category": r.data[0]}


# ── System categories (tenant-configurable; migration 745) ──────────────────────────────────────────
# `system_category` used to be four values frozen in FOUR places at once: a CHECK constraint on
# pos.products, SYSTEM_CATEGORIES in the products page, _sys_cat() in the importer, and the filter
# below. A tenant could not add "Tablet" or "Watch", which is why 96 of luxelink's 118 products sat
# in the catch-all "Regular". It is now a per-org config table ([[saas-sap-configurable-directive]]).
# The original 4 are seeded as BUILTINS: renameable and deactivatable, never deletable, so an
# existing catalog cannot be orphaned by one careless click.
BUILTIN_SYSTEM_CATEGORIES = (("Accessory", 10), ("Cell Phone", 20), ("Regular", 30), ("Service", 40))


def _system_categories(org_id: str, active_only: bool = False) -> list:
    """The org's list, seeding the 4 builtins the first time it is asked.

    Lazy-seeding here rather than in core/onboarding.py means a tenant provisioned BEFORE 745 and
    one provisioned after both get a working list, and no shared core file has to change."""
    client = sb()
    rows = (client.schema("pos").table("system_categories").select("*")
            .eq("org_id", org_id).order("sort_order").order("name").limit(200).execute().data) or []
    if not rows:
        try:
            client.schema("pos").table("system_categories").insert(
                [{"org_id": org_id, "name": n, "sort_order": s, "is_builtin": True}
                 for n, s in BUILTIN_SYSTEM_CATEGORIES]).execute()
        except Exception:
            pass   # a concurrent first load already seeded it; the re-read settles who won
        rows = (client.schema("pos").table("system_categories").select("*")
                .eq("org_id", org_id).order("sort_order").order("name")
                .limit(200).execute().data) or []
    return [r for r in rows if r.get("is_active")] if active_only else rows


def _valid_system_category(org_id: str, value) -> None:
    """Reject a system_category that is not one of the org's own ACTIVE names.

    Migration 745 dropped the CHECK constraint, so without this the column would be free text and
    the next import could invent a fifth spelling of "Accessory" that no dropdown ever offers."""
    if value in (None, ""):
        return
    names = {(r.get("name") or "") for r in _system_categories(org_id, active_only=True)}
    if value not in names:
        raise HTTPException(
            400, f"'{value}' is not one of this company's system categories "
                 f"({', '.join(sorted(names)) or 'none configured'}) — add it first under "
                 f"Depts / Categories")


@router.get("/system-categories")
def list_system_categories(active_only: bool = False, org_id: str = ORG_ID):
    return {"system_categories": _system_categories(org_id, active_only)}


@router.post("/system-categories")
def create_system_category(body: dict, org_id: str = ORG_ID):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    existing = _system_categories(org_id)
    if any((r.get("name") or "").lower() == name.lower() for r in existing):
        raise HTTPException(400, f"'{name}' already exists")
    nxt = max([int(r.get("sort_order") or 0) for r in existing] or [0]) + 10
    row = {"org_id": org_id, "name": name, "is_builtin": False,
           "sort_order": int(body.get("sort_order") or nxt)}
    r = sb().schema("pos").table("system_categories").insert(row).execute()
    return {"system_category": (r.data or [{}])[0]}


@router.patch("/system-categories/{cat_id}")
def update_system_category(cat_id: str, body: dict, org_id: str = ORG_ID):
    """Rename / reorder / deactivate.

    A RENAME carries its products with it. pos.products stores the NAME, not a foreign key, so
    renaming the row alone would leave every product pointing at a value the dropdown no longer
    offers — the product would read as uncategorised without anyone touching it."""
    client = sb()
    cur = (client.schema("pos").table("system_categories").select("*")
           .eq("org_id", org_id).eq("id", cat_id).limit(1).execute().data) or []
    if not cur:
        raise HTTPException(404, "not found")
    cur = cur[0]
    upd = {}
    if "name" in body:
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name cannot be blank")
        if name.lower() != (cur.get("name") or "").lower() and any(
                (r.get("name") or "").lower() == name.lower() for r in _system_categories(org_id)):
            raise HTTPException(400, f"'{name}' already exists")
        upd["name"] = name
    if "sort_order" in body:
        upd["sort_order"] = int(body.get("sort_order") or 0)
    if "is_active" in body:
        upd["is_active"] = bool(body.get("is_active"))
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (client.schema("pos").table("system_categories").update(upd)
         .eq("org_id", org_id).eq("id", cat_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    moved = 0
    if upd.get("name") and upd["name"] != cur.get("name"):
        m = (client.schema("pos").table("products").update({"system_category": upd["name"]})
             .eq("org_id", org_id).eq("system_category", cur.get("name")).execute())
        moved = len(m.data or [])
    return {"system_category": r.data[0], "products_moved": moved}


@router.delete("/system-categories/{cat_id}")
def delete_system_category(cat_id: str, org_id: str = ORG_ID):
    """Only an UNUSED, non-builtin category can be deleted; anything else is deactivated instead.

    Deleting one still in use would strand its products on a name no dropdown offers — the same
    orphaning the rename path above is careful to avoid."""
    client = sb()
    cur = (client.schema("pos").table("system_categories").select("*")
           .eq("org_id", org_id).eq("id", cat_id).limit(1).execute().data) or []
    if not cur:
        raise HTTPException(404, "not found")
    cur = cur[0]
    if cur.get("is_builtin"):
        raise HTTPException(400, "a built-in category can be renamed or switched off, not deleted")
    used = (client.schema("pos").table("products").select("id")
            .eq("org_id", org_id).eq("system_category", cur.get("name")).limit(1).execute().data) or []
    if used:
        raise HTTPException(400, f"'{cur.get('name')}' is still in use — switch it off instead")
    (client.schema("pos").table("system_categories").delete()
     .eq("org_id", org_id).eq("id", cat_id).execute())
    return {"ok": True}


# ── Products ───────────────────────────────────────────────────────────────────────────────────────
@router.get("/products")
def list_products(search: str = "", department_id: str = "", system_category: str = "",
                  active_only: bool = True, org_id: str = ORG_ID):
    client = sb()
    q = client.schema("pos").table("products").select("*").eq("org_id", org_id)
    if active_only:
        q = q.eq("is_active", True)
    if department_id:
        q = q.eq("department_id", department_id)
    if system_category:
        q = q.eq("system_category", system_category)
    if search.strip():
        s = search.strip().replace("%", "").replace(",", " ")
        q = q.or_(f"short_name.ilike.%{s}%,full_name.ilike.%{s}%,upc.ilike.%{s}%")
    rows = q.order("product_code", desc=True).limit(500).execute().data or []
    # Resolve department/category names in Python (cheap: both lists are small) rather than
    # relying on PostgREST embeds from a non-default schema.
    cat = catalog(org_id)
    dname = {d["id"]: d["short_name"] for d in cat["departments"]}
    cname = {c["id"]: c["name"] for c in cat["categories"]}
    for r in rows:
        r["department_name"] = dname.get(r.get("department_id"))
        r["category_name"] = cname.get(r.get("category_id"))
    return {"products": rows}


@router.post("/products")
def create_product(body: dict, org_id: str = ORG_ID):
    ins = _clean(body)
    if not (ins.get("short_name") or "").strip():
        raise HTTPException(400, "short_name required")
    _valid_system_category(org_id, ins.get("system_category"))
    ins["org_id"] = org_id
    r = sb().schema("pos").table("products").insert(ins).execute()
    return {"product": (r.data or [{}])[0]}


@router.patch("/products/{product_id}")
def update_product(product_id: str, body: dict, org_id: str = ORG_ID):
    upd = _clean(body)
    if not upd:
        raise HTTPException(400, "nothing to update")
    if "system_category" in upd:
        _valid_system_category(org_id, upd.get("system_category"))
    r = (sb().schema("pos").table("products").update(upd)
         .eq("org_id", org_id).eq("id", product_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"product": r.data[0]}


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# Customer Special Order — Phase 1: the HIDDEN vendor catalog (mig 864).
#   • The store/customer-facing catalog (GET /special-orders/catalog) returns ONLY customer-facing
#     product fields — it never reads pos.special_order_vendor, so the vendor (Amazon) is invisible at
#     the API boundary, not merely hidden in the UI.
#   • The HQ admin surface (pos_special_order_admin) manages the item + its vendor linkage. Store staff
#     don't hold that permission, so they can neither see the vendor nor self-order from it.
# See docs/POS_SPECIAL_ORDER_PLAN.md.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
SPECIAL_ORDER_VENDOR_FIELDS = ("vendor", "vendor_sku", "vendor_url", "vendor_cost", "lead_time_days",
                               "notes", "is_active")


def _special_order_products(org_id, search="", active_only=True):
    q = (sb().schema("pos").table("products").select("*")
         .eq("org_id", org_id).eq("is_special_order", True))
    if active_only:
        q = q.eq("is_active", True)
    if (search or "").strip():
        s = search.strip().replace("%", "").replace(",", " ")
        q = q.or_(f"short_name.ilike.%{s}%,full_name.ilike.%{s}%,upc.ilike.%{s}%")
    return q.order("short_name").limit(500).execute().data or []


@router.get("/special-orders/catalog")
def special_order_catalog(search: str = "", org_id: str = ORG_ID):
    """NEUTRAL store/customer-facing special-order catalog. Returns ONLY the customer-facing product
    fields — never the vendor linkage (Amazon ASIN / URL / cost) and never the raw cost. This endpoint
    does not read pos.special_order_vendor at all, so source-hiding holds even if a UI bug tried to
    show it. Powers the POS 'Customer special order' picker."""
    rows = _special_order_products(org_id, search)
    cat = catalog(org_id)
    cname = {c["id"]: c["name"] for c in cat["categories"]}
    items = [{"id": r["id"], "product_code": r.get("product_code"), "short_name": r.get("short_name"),
              "full_name": r.get("full_name"), "category": cname.get(r.get("category_id")),
              "retail_price": r.get("retail_price"), "is_taxable": r.get("is_taxable"),
              "system_category": r.get("system_category")}
             for r in rows]
    return {"items": items}


@router.get("/special-orders/catalog/admin")
def special_order_catalog_admin(search: str = "", authorization: str = Header(default=""), org_id: str = ORG_ID):
    """HQ-only catalog WITH the vendor linkage (Amazon ASIN / URL / cost). Gated by
    pos_special_order_admin, which store staff do not hold — so they can neither see the vendor nor
    self-order from it."""
    _require_pos_perm(authorization, org_id, "pos_special_order_admin")
    rows = _special_order_products(org_id, search, active_only=False)
    ids = [r["id"] for r in rows if r.get("id")]
    vend = {}
    if ids:
        vrows = (sb().schema("pos").table("special_order_vendor").select("*")
                 .eq("org_id", org_id).in_("product_id", ids).execute().data) or []
        vend = {v.get("product_id"): v for v in vrows}
    for r in rows:
        r["vendor"] = vend.get(r["id"])
    return {"items": rows}


@router.post("/special-orders/catalog")
def create_special_order_item(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """HQ: create a special-order catalog item — a pos.products row (customer-facing) flagged
    is_special_order, plus its hidden vendor linkage (optional at create). Gated pos_special_order_admin."""
    _require_pos_perm(authorization, org_id, "pos_special_order_admin")
    ins = _clean(body)
    if not (ins.get("short_name") or "").strip():
        raise HTTPException(400, "short_name required")
    _valid_system_category(org_id, ins.get("system_category"))
    ins["org_id"] = org_id
    ins["is_special_order"] = True
    prod = (sb().schema("pos").table("products").insert(ins).execute().data or [{}])[0]
    v = {k: body[k] for k in SPECIAL_ORDER_VENDOR_FIELDS if k in body}
    if v and prod.get("id"):
        v.update({"org_id": org_id, "product_id": prod["id"]})
        sb().schema("pos").table("special_order_vendor").upsert(v, on_conflict="org_id,product_id").execute()
    return {"product": prod}


@router.patch("/special-orders/catalog/{product_id}")
def update_special_order_item(product_id: str, body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """HQ: update a special-order item's product fields and/or its vendor linkage. Gated
    pos_special_order_admin."""
    _require_pos_perm(authorization, org_id, "pos_special_order_admin")
    upd = _clean(body)
    if "system_category" in upd:
        _valid_system_category(org_id, upd.get("system_category"))
    upd["is_special_order"] = True
    r = (sb().schema("pos").table("products").update(upd)
         .eq("org_id", org_id).eq("id", product_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    v = {k: body[k] for k in SPECIAL_ORDER_VENDOR_FIELDS if k in body}
    if v:
        v.update({"org_id": org_id, "product_id": product_id})
        sb().schema("pos").table("special_order_vendor").upsert(v, on_conflict="org_id,product_id").execute()
    return {"product": r.data[0]}


# ── Customer Special Order — Phase 2: place the order + book the sale (mig 865) ──────────────────────
SPECIAL_ORDER_MIN_MARGIN_PCT = 15.0   # default price floor; owner-configurable later (plan TODO #6)


def _so_num(v, d=0.0):
    try:
        return float(v)
    except Exception:
        return d


# Vendor-internal fields on a special order. Store staff see the order's status/tracking, but NOT the
# vendor cost or the vendor's identity — same source-hiding line the catalog draws. Only a caller with
# pos_special_order_admin (HQ) sees these.
_SO_HQ_ONLY = ("captured_cost", "actual_cost", "vendor", "vendor_order_ref", "po_id")


def _has_pos_perm(authorization: str, org_id: str, key: str) -> bool:
    """Non-raising permission check (for redaction decisions). Fails closed to False."""
    try:
        return _pos_grant(_caller_ctx(authorization, org_id), key)
    except Exception:
        return False


def _redact_so(row: dict, is_admin: bool) -> dict:
    return row if is_admin else {k: v for k, v in (row or {}).items() if k not in _SO_HQ_ONLY}


@router.post("/special-orders")
def create_special_order(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Place a customer special order: enforce the margin floor, BOOK the sale (declared price →
    revenue, vendor cost → COGS; profit derives automatically), and record the order for fulfillment.
    Store-facing — the caller never sees the vendor cost; the server reads it only to book COGS and
    guard the margin, so the vendor (Amazon) stays hidden."""
    store_code = (body.get("store_code") or "").strip()
    product_id = (body.get("product_id") or "").strip()
    if not store_code or not product_id:
        raise HTTPException(400, "store_code and product_id are required")
    qty = _so_num(body.get("qty"), 1) or 1
    sale_price = _so_num(body.get("declared_sale_price") or body.get("sale_price"))
    if sale_price <= 0:
        raise HTTPException(400, "declared_sale_price is required")
    eid = _caller_employee(authorization, org_id)
    if not eid:
        raise HTTPException(403, "your login isn't linked to an employee record — ask an admin to set "
                                 "your Employee ID in Roles & Access")
    ks = _caller_store_keyset(authorization, org_id)
    if ks is not None and store_code.upper() not in {str(k).upper() for k in ks}:
        raise HTTPException(403, "this store is outside your scope")
    prows = (sb().schema("pos").table("products").select("*")
             .eq("org_id", org_id).eq("id", product_id).limit(1).execute().data) or []
    if not prows or not prows[0].get("is_special_order"):
        raise HTTPException(404, "not a special-order catalog item")
    prod = prows[0]
    vrows = (sb().schema("pos").table("special_order_vendor")
             .select("vendor_cost,vendor,vendor_sku")
             .eq("org_id", org_id).eq("product_id", product_id).limit(1).execute().data) or []
    vrow = vrows[0] if vrows else {}
    cost = _so_num(vrow.get("vendor_cost") or prod.get("cost"))
    if cost > 0:
        floor = cost * (1 + SPECIAL_ORDER_MIN_MARGIN_PCT / 100.0)
        if sale_price < floor - 0.005:
            raise HTTPException(400, f"Price too low for this item — the minimum is {floor:.2f} "
                                     f"(at least a {int(SPECIAL_ORDER_MIN_MARGIN_PCT)}% margin).")
    tax_total = _so_num(body.get("tax_total"))
    subtotal = round(sale_price * qty, 2)
    total = round(subtotal + tax_total, 2)
    tax_rate = round(tax_total / subtotal, 6) if subtotal else 0.0
    customer_id = (body.get("customer_id") or "").strip() or None
    desc = prod.get("full_name") or prod.get("short_name") or "Special order"
    sale = {"store_code": store_code, "customer_id": customer_id, "receipt_type": "sale",
            "subtotal": subtotal, "discount_total": 0, "tax_total": tax_total, "total": total,
            "balance": 0, "is_activation_sale": False, "notes": "Customer special order",
            "employee_id": eid}
    items = [{"product_id": product_id, "product_type": prod.get("system_category") or "Regular",
              "description": desc, "qty": qty, "unit_price": sale_price,
              "list_price": prod.get("retail_price") or sale_price, "cost": cost, "discount": 0,
              "tax_rate": tax_rate, "tax_value": tax_total, "extended_price": subtotal}]
    pay = body.get("payment") or {}
    payments = ([{"payment_method": pay["payment_method"], "amount": _so_num(pay.get("amount"))}]
                if pay.get("payment_method") and _so_num(pay.get("amount")) > 0 else [])
    try:
        r = sb().schema("pos").rpc("checkout", {"p_org": org_id, "p_sale": sale, "p_items": items,
                                                "p_payments": payments}).execute()
    except Exception as e:
        raise HTTPException(400, f"could not book the sale: {e}")
    srow = r.data[0] if isinstance(r.data, list) else r.data
    sale_id = srow.get("id") if isinstance(srow, dict) else None
    ship_to = (body.get("ship_to_store") or store_code)
    vendor_key = vrow.get("vendor") or "amazon"
    so = {"org_id": org_id, "store_code": store_code,
          "ship_to_store": ship_to, "customer_id": customer_id,
          "customer_name": body.get("customer_name"), "employee_id": eid, "product_id": product_id,
          "description": desc, "qty": qty, "sale_price": sale_price, "captured_cost": cost,
          "sale_id": sale_id, "status": "requested",
          "vendor": vendor_key, "notes": body.get("notes")}
    # Plug-and-play placement: resolve the product's vendor connector and let its adapter try to place
    # the order (outbound API), leave it queued (manual / inbound), etc. The sale is already booked, so
    # placement NEVER blocks the sale — a failure just leaves the order in the fulfillment queue.
    conn = _vendor_connector(org_id, vendor_key)
    try:
        from app.modules.pos.vendor_adapters import get_adapter
        placement = get_adapter(conn).place_order({
            "vendor_sku": vrow.get("vendor_sku"), "qty": qty, "ship_to": ship_to,
            "reference": (srow.get("receipt_no") if isinstance(srow, dict) else None) or sale_id})
    except Exception:
        placement = {}
    for k in ("status", "vendor_order_ref", "tracking"):
        if placement.get(k):
            so[k] = placement[k]
    if placement.get("notes"):
        so["notes"] = " ".join(x for x in (so.get("notes"), placement["notes"]) if x)
    ins = (sb().schema("pos").table("special_orders").insert(so).execute().data or [{}])[0]
    is_admin = _has_pos_perm(authorization, org_id, "pos_special_order_admin")
    return {"special_order": _redact_so(ins, is_admin), "sale": srow}


_SO_STATUSES = ("requested", "ordered", "shipped", "received", "delivered", "cancelled")


@router.get("/special-orders")
def list_special_orders(status: str = "", store_code: str = "", authorization: str = Header(default=""),
                        org_id: str = ORG_ID):
    """The caller's in-scope special orders (store-scoped for a store manager; org-wide for admin/HQ).
    The vendor linkage is NOT included here — the HQ fulfillment queue reads it separately."""
    q = sb().schema("pos").table("special_orders").select("*").eq("org_id", org_id)
    if status in _SO_STATUSES:
        q = q.eq("status", status)
    if store_code:
        q = q.eq("store_code", store_code)
    rows = q.order("created_at", desc=True).limit(500).execute().data or []
    ks = _caller_store_keyset(authorization, org_id)
    rows = _span_filter(rows, ks, field="store_code")
    is_admin = _has_pos_perm(authorization, org_id, "pos_special_order_admin")
    return {"special_orders": [_redact_so(r, is_admin) for r in rows]}


@router.get("/special-orders/{order_id}")
def get_special_order(order_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    rows = (sb().schema("pos").table("special_orders").select("*")
            .eq("org_id", org_id).eq("id", order_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "not found")
    so = rows[0]
    ks = _caller_store_keyset(authorization, org_id)
    if ks is not None and str(so.get("store_code") or "").upper() not in {str(k).upper() for k in ks}:
        # a store-scoped caller may only see their own store's orders (HQ/admin has ks=None)
        raise HTTPException(403, "outside your scope")
    is_admin = _has_pos_perm(authorization, org_id, "pos_special_order_admin")
    return {"special_order": _redact_so(so, is_admin)}


@router.patch("/special-orders/{order_id}")
def update_special_order(order_id: str, body: dict, authorization: str = Header(default=""),
                         org_id: str = ORG_ID):
    """HQ/ops fulfillment update — status, vendor order ref, tracking, and the actual-cost true-up.
    Gated by pos_special_order_admin (the vendor order ref names the vendor, so it's HQ-only)."""
    _require_pos_perm(authorization, org_id, "pos_special_order_admin")
    upd = {}
    if "status" in body:
        st = str(body.get("status") or "").strip()
        if st not in _SO_STATUSES:
            raise HTTPException(400, f"status must be one of {', '.join(_SO_STATUSES)}")
        upd["status"] = st
    for k in ("vendor_order_ref", "tracking", "ship_to_store", "notes", "po_id"):
        if k in body:
            upd[k] = body[k]
    if "actual_cost" in body:
        upd["actual_cost"] = _so_num(body.get("actual_cost"))
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (sb().schema("pos").table("special_orders").update(upd)
         .eq("org_id", org_id).eq("id", order_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"special_order": r.data[0]}


# ── Phase 4 — HQ ops fulfillment queue (gated pos_special_order_admin; vendor linkage exposed here) ──
def _so_admin_row(org_id: str, so: dict) -> dict:
    """A special-order row enriched for the HQ fulfillment queue: the product's hidden vendor linkage
    (ASIN/sku/cost) + the resolved vendor connector (mode, whether auto-order is wired). HQ-only — this
    is the ONE place the vendor is shown, exactly as the plan intends."""
    out = dict(so or {})
    vkey = so.get("vendor")
    try:
        vrows = (sb().schema("pos").table("special_order_vendor")
                 .select("vendor,vendor_sku,vendor_cost,vendor_url")
                 .eq("org_id", org_id).eq("product_id", so.get("product_id")).limit(1)
                 .execute().data) or []
        out["vendor_linkage"] = vrows[0] if vrows else None
        if vrows and not vkey:
            vkey = vrows[0].get("vendor")
    except Exception:
        out["vendor_linkage"] = None
    conn = _vendor_connector(org_id, vkey) if vkey else None
    if conn:
        mode = (conn.get("integration_mode") or "manual").strip()
        auto = (mode == "outbound_api" and bool((conn.get("api_base_url") or "").strip())
                and bool((conn.get("credential_ref") or "").strip()))
        out["connector"] = {"vendor_key": conn.get("vendor_key"),
                            "display_name": conn.get("display_name"),
                            "integration_mode": mode, "auto_order": auto}
    else:
        out["connector"] = None
    return out


@router.get("/special-orders/fulfillment")
def special_orders_fulfillment(status: str = "", store_code: str = "",
                               authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The HQ ops fulfillment queue: every special order WITH its vendor linkage + connector, so ops can
    place/refresh and advance status. HQ-only (pos_special_order_admin) — this is where the vendor lives."""
    _require_pos_perm(authorization, org_id, "pos_special_order_admin")
    q = sb().schema("pos").table("special_orders").select("*").eq("org_id", org_id)
    if status in _SO_STATUSES:
        q = q.eq("status", status)
    if store_code:
        q = q.eq("store_code", store_code)
    rows = q.order("created_at", desc=True).limit(500).execute().data or []
    return {"special_orders": [_so_admin_row(org_id, r) for r in rows]}


def _apply_placement(org_id: str, so: dict, placement: dict) -> dict:
    """Persist an adapter placement/refresh result onto a special_orders row. Only the keys the adapter
    returned are touched; notes are appended, never clobbered. Returns the updated row."""
    upd = {}
    for k in ("status", "vendor_order_ref", "tracking"):
        if placement.get(k):
            upd[k] = placement[k]
    if placement.get("notes"):
        upd["notes"] = " ".join(x for x in (so.get("notes"), placement["notes"]) if x)
    if not upd:
        return so
    r = (sb().schema("pos").table("special_orders").update(upd)
         .eq("org_id", org_id).eq("id", so.get("id")).execute())
    return (r.data[0] if r.data else {**so, **upd})


@router.post("/special-orders/{order_id}/place")
def place_special_order(order_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """HQ ops: (re)place a queued special order with its vendor via the connector's adapter. For an
    outbound-API vendor this calls the vendor API; for manual/inbound it just confirms the queue state.
    A vendor/transport failure never raises — the order stays queued for manual placement with a note."""
    _require_pos_perm(authorization, org_id, "pos_special_order_admin")
    rows = (sb().schema("pos").table("special_orders").select("*")
            .eq("org_id", org_id).eq("id", order_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "not found")
    so = rows[0]
    if so.get("status") not in ("requested", "ordered"):
        raise HTTPException(409, f"order is '{so.get('status')}' — placement only applies before shipping")
    vkey = so.get("vendor") or "amazon"
    conn = _vendor_connector(org_id, vkey)
    vrows = (sb().schema("pos").table("special_order_vendor").select("vendor_sku")
             .eq("org_id", org_id).eq("product_id", so.get("product_id")).limit(1).execute().data) or []
    try:
        from app.modules.pos.vendor_adapters import get_adapter
        placement = get_adapter(conn).place_order({
            "vendor_sku": (vrows[0].get("vendor_sku") if vrows else None), "qty": so.get("qty"),
            "ship_to": so.get("ship_to_store") or so.get("store_code"),
            "reference": so.get("sale_id") or order_id})
    except Exception as e:
        placement = {"status": "requested", "notes": f"placement error — queued for manual ({e})"}
    updated = _apply_placement(org_id, so, placement)
    return {"special_order": _so_admin_row(org_id, updated), "placement": placement}


@router.post("/special-orders/{order_id}/refresh")
def refresh_special_order(order_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """HQ ops: re-poll the vendor for status/tracking via the connector's adapter (outbound-API vendors;
    a documented no-op for manual/inbound). Never raises on a vendor failure."""
    _require_pos_perm(authorization, org_id, "pos_special_order_admin")
    rows = (sb().schema("pos").table("special_orders").select("*")
            .eq("org_id", org_id).eq("id", order_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "not found")
    so = rows[0]
    conn = _vendor_connector(org_id, so.get("vendor") or "amazon")
    try:
        from app.modules.pos.vendor_adapters import get_adapter
        placement = get_adapter(conn).refresh({
            "vendor_order_ref": so.get("vendor_order_ref"), "qty": so.get("qty")})
    except Exception as e:
        placement = {"notes": f"refresh error ({e})"}
    updated = _apply_placement(org_id, so, placement)
    return {"special_order": _so_admin_row(org_id, updated), "placement": placement}


@router.post("/special-orders/{order_id}/true-up")
def true_up_special_order(order_id: str, body: dict, authorization: str = Header(default=""),
                          org_id: str = ORG_ID):
    """HQ ops: reconcile the ACTUAL vendor (Amazon) cost at fulfillment onto the booked sale line so COGS
    — and the derived profit — is exact. Body: {actual_cost} (per unit, matching captured_cost). Updates
    the sale item's cost, stamps actual_cost on the order, and best-effort re-runs the built-in POS feed
    for the sale's period so the P&L reflects the corrected cost. HQ-only."""
    _require_pos_perm(authorization, org_id, "pos_special_order_admin")
    actual_cost = _so_num(body.get("actual_cost"), None) if body.get("actual_cost") not in (None, "") else None
    if actual_cost is None or actual_cost < 0:
        raise HTTPException(400, "actual_cost (a number >= 0, per unit) is required")
    rows = (sb().schema("pos").table("special_orders").select("*")
            .eq("org_id", org_id).eq("id", order_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "not found")
    so = rows[0]
    sale_id, product_id = so.get("sale_id"), so.get("product_id")
    cost_synced = False
    if sale_id and product_id:
        try:
            sb().schema("pos").table("sale_items").update({"cost": actual_cost}) \
                .eq("org_id", org_id).eq("sale_id", sale_id).eq("product_id", product_id).execute()
            cost_synced = True
        except Exception as e:
            raise HTTPException(400, f"could not reconcile the sale line cost: {e}")
    note = f"actual vendor cost reconciled to {actual_cost:.2f}/unit"
    upd = {"actual_cost": actual_cost, "notes": " ".join(x for x in (so.get("notes"), note) if x)}
    r = (sb().schema("pos").table("special_orders").update(upd)
         .eq("org_id", org_id).eq("id", order_id).execute())
    # Propagate the corrected COGS to commcalc (built-in POS feed). Best-effort + idempotent (the feed
    # replaces the whole period), and only when the built-in POS is on — never fails the true-up.
    refeed = None
    try:
        from app.modules.pos import commcalc_feed as _feed
        setup = _feed.get_pos_setup(org_id)
        if (setup.get("builtin_role") or "off") != "off":
            srows = (sb().schema("pos").table("sales").select("created_at")
                     .eq("org_id", org_id).eq("id", sale_id).limit(1).execute().data) or []
            when = str((srows[0].get("created_at") if srows else "") or "")
            if when:
                dt = _feed.datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(_feed.BUSINESS_TZ)
                period = dt.strftime("%B %Y")
                # 'monthly' is the raw_sales grain that carries COGS into the P&L / commission basis
                # (see the plan's "the insight") — the stream the corrected cost must reach.
                _feed.sync_period(org_id, "monthly", period)
                refeed = {"period": period, "mode": "monthly", "resynced": True}
    except Exception as e:
        refeed = {"resynced": False, "note": f"period not re-synced ({e}) — run the POS feed for the period"}
    return {"special_order": _so_admin_row(org_id, (r.data[0] if r.data else {**so, **upd})),
            "cost_synced": cost_synced, "refeed": refeed}


# ── Plug-and-play vendor connectors (mig 866) — HQ-only registry of dropship vendors ─────────────────
# A connector row makes a vendor pluggable in one of three modes: 'manual' (HQ fulfills from the queue),
# 'inbound_api' (the vendor pulls our queue + posts status, via vendor_api.py), or 'outbound_api' (we
# call the vendor's API, via pos/vendor_adapters.py). See docs/POS_SPECIAL_ORDER_PLAN.md.
VENDOR_CONNECTOR_FIELDS = ("vendor_key", "display_name", "integration_mode", "api_base_url",
                           "credential_ref", "config", "is_active")
_VENDOR_MODES = ("manual", "outbound_api", "inbound_api")


def _vendor_connector(org_id: str, vendor_key: str):
    """The active connector for (org, vendor_key), or None. Used server-side only — the connector
    (and the vendor it names) is never returned to a store/customer response."""
    if not vendor_key:
        return None
    rows = (sb().schema("pos").table("vendor_connector").select("*")
            .eq("org_id", org_id).eq("vendor_key", vendor_key).eq("is_active", True)
            .limit(1).execute().data) or []
    return rows[0] if rows else None


def _redact_connector(row: dict) -> dict:
    """Never surface the inbound token hash to the admin UI — it's an auth secret, not display data."""
    return {k: v for k, v in (row or {}).items() if k != "inbound_token_hash"}


@router.get("/vendor-connectors")
def list_vendor_connectors(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """HQ-only: the org's dropship-vendor connectors. Gated pos_special_order_admin."""
    _require_pos_perm(authorization, org_id, "pos_special_order_admin")
    rows = (sb().schema("pos").table("vendor_connector").select("*")
            .eq("org_id", org_id).order("vendor_key").execute().data) or []
    return {"connectors": [_redact_connector(r) for r in rows]}


@router.post("/vendor-connectors")
def create_vendor_connector(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """HQ-only: register a dropship vendor. For 'inbound_api', pass an `inbound_token` (the vendor's
    access token) — we store only its SHA-256 hash and RETURN THE TOKEN ONCE so you can hand it to the
    vendor; it can't be retrieved again. Gated pos_special_order_admin."""
    _require_pos_perm(authorization, org_id, "pos_special_order_admin")
    ins = {k: body[k] for k in VENDOR_CONNECTOR_FIELDS if k in body}
    if not (ins.get("vendor_key") or "").strip():
        raise HTTPException(400, "vendor_key required")
    ins["vendor_key"] = ins["vendor_key"].strip()
    mode = (ins.get("integration_mode") or "manual").strip()
    if mode not in _VENDOR_MODES:
        raise HTTPException(400, f"integration_mode must be one of {', '.join(_VENDOR_MODES)}")
    ins["integration_mode"] = mode
    ins["org_id"] = org_id
    token = (body.get("inbound_token") or "").strip()
    if token:
        from app.modules.pos.vendor_adapters import token_hash
        ins["inbound_token_hash"] = token_hash(token)
    try:
        row = (sb().schema("pos").table("vendor_connector")
               .upsert(ins, on_conflict="org_id,vendor_key").execute().data or [{}])[0]
    except Exception as e:
        raise HTTPException(400, f"could not save connector: {e}")
    out = {"connector": _redact_connector(row)}
    if token:
        out["inbound_token"] = token   # shown once — hand it to the vendor now
    return out


@router.patch("/vendor-connectors/{connector_id}")
def update_vendor_connector(connector_id: str, body: dict, authorization: str = Header(default=""),
                            org_id: str = ORG_ID):
    """HQ-only: update a connector. Passing a new `inbound_token` rotates it (returned once). Gated
    pos_special_order_admin."""
    _require_pos_perm(authorization, org_id, "pos_special_order_admin")
    upd = {k: body[k] for k in VENDOR_CONNECTOR_FIELDS if k in body}
    if "integration_mode" in upd and upd["integration_mode"] not in _VENDOR_MODES:
        raise HTTPException(400, f"integration_mode must be one of {', '.join(_VENDOR_MODES)}")
    token = (body.get("inbound_token") or "").strip()
    if token:
        from app.modules.pos.vendor_adapters import token_hash
        upd["inbound_token_hash"] = token_hash(token)
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (sb().schema("pos").table("vendor_connector").update(upd)
         .eq("org_id", org_id).eq("id", connector_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    out = {"connector": _redact_connector(r.data[0])}
    if token:
        out["inbound_token"] = token
    return out


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# Phase 1 — customers, inventory, sales/checkout, register, settings (mig 725)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════

CUSTOMER_FIELDS = ("account_type", "company_name", "first_name", "last_name", "middle_initial",
                   "dob", "primary_account_no", "password", "email",
                   "phone_primary", "phone_secondary", "address_1", "address_2", "city", "state",
                   "zip", "referral_source", "credit_limit", "accept_checks", "is_active")
SERIAL_FIELDS = ("product_id", "store_code", "serial_number", "imei", "sim_card", "color",
                 "storage", "condition", "status", "cost", "date_received", "po_number")
# `market` is writable here as of 2026-08-10: migration 741 added the column and gave it a resolver and
# a bulk-create path, but the single-row UPDATE could not set OR CLEAR it — so a market-scoped rate,
# once created, could never be moved to a store or repointed at another market. Adding it back is what
# makes the scope selector on the Sales Tax screen a round trip instead of a one-way door.
TAX_CODE_FIELDS = ("name", "rate", "store_code", "market", "is_active")
RECEIPT_TEMPLATE_FIELDS = ("name", "header_text", "footer_text", "show_store_name",
                           "show_customer", "show_employee", "show_serials",
                           "show_tax_breakdown", "show_discounts", "paper_width_mm",
                           "font_size_px")


def _clean_customer(body: dict) -> dict:
    out = {k: body[k] for k in CUSTOMER_FIELDS if k in body}
    for k in ("dob", "company_name", "middle_initial",
              "primary_account_no", "password", "email", "phone_secondary",
              "address_2", "referral_source"):
        if k in out and out[k] == "":
            out[k] = None
    return out


def _caller_employee(authorization: str, org_id: str) -> str:
    """The signed-in caller's employee_id ('' when the login isn't linked). Sales, notes and
    drawer sessions are stamped with THIS, never a body field — same anti-spoofing stance as
    the storeops time clock."""
    from app.modules.core.router import _uid_from_token  # local import avoids a circular import
    uid = _uid_from_token(authorization)
    if not uid:
        return ""
    rows = (sb().schema("storeops").table("app_users").select("employee_id")
            .eq("org_id", org_id).eq("auth_id", uid).limit(1).execute().data) or []
    return ((rows[0].get("employee_id") if rows else "") or "").strip()


def _super_admin_login(uid: str) -> bool:
    """Whether this LOGIN is a platform super-admin, regardless of which tenant it is acting on.

    `storeops.app_users.auth_id` is globally unique per login, so super-admin standing is a property
    of the PERSON, not of one membership row. That is already the platform's posture: tenant_middleware
    skips the org_id rewrite entirely for a super-admin, so the client-supplied org_id is honoured and
    cross-tenant administration works. Every POS gate must read it the same way — this lives in ONE
    place because two gates need it and the drift between them is precisely the bug being fixed here.

    FAILS CLOSED: any lookup fault returns False (not a super-admin). A gate must never ESCALATE on
    error; the surrounding caller then applies its own normal denial."""
    if not uid:
        return False
    try:
        rows = (sb().schema("storeops").table("app_users").select("id")
                .eq("auth_id", uid).eq("super_admin", True).limit(1).execute().data) or []
    except Exception:
        return False
    return bool(rows)


def _caller_ctx(authorization: str, org_id: str):
    """The caller's RBAC context — {'perms', 'role', 'super_admin'} — or None when the caller is
    UNRESOLVABLE (no/invalid token, no app_users membership, or a role name with no roles row).
    Callers must treat None as deny — an unresolvable caller is never granted anything."""
    from app.modules.core.router import _uid_from_token
    uid = _uid_from_token(authorization)
    if not uid:
        return None
    rows = (sb().schema("storeops").table("app_users").select("role, super_admin")
            .eq("org_id", org_id).eq("auth_id", uid).limit(1).execute().data) or []
    if not rows:
        # SUPER-ADMIN ADMINISTERING A TENANT THEY DO NOT BELONG TO (owner report 2026-08-09: "the POS
        # set up wizard ... is not letting me update the sales tax"). Cross-tenant administration is
        # intentional platform-wide -- tenant_middleware skips the org rewrite entirely for a
        # super-admin -- but this gate resolved membership in the ACTING org and returned None, which
        # _require_pos_perm turns into a 401 before _pos_grant's super_admin branch can run. The
        # wizard therefore LOADED (GETs carry no gate) and silently refused every save.
        # Since app_users.auth_id is globally unique, super-admin standing is a property of the LOGIN,
        # not of one membership: look it up without the org filter and, if present, grant it here.
        # A non-super-admin with no membership still returns None and is still denied.
        if not _super_admin_login(uid):
            return None
        return {"perms": {}, "role": "super_admin", "super_admin": True}
    role = ((rows[0].get("role") if rows else "") or "").strip()
    if not role:
        return None
    rr = (sb().schema("storeops").table("roles").select("permissions")
          .eq("org_id", org_id).eq("name", role).limit(1).execute().data) or []
    if not rr:
        return None
    return {"perms": rr[0].get("permissions") or {}, "role": role,
            "super_admin": bool(rows[0].get("super_admin"))}


def _pos_grant(ctx, key: str) -> bool:
    """Whether `ctx` holds POS permission `key`. Mirrors core._can_edit_setting's precedence, which
    is the platform's canonical semantic:
      1. super_admin                -> always yes
      2. explicit grant OR DENY     -> wins even over an admin, so an owner can revoke one POS
                                       right from an otherwise-full role
      3. default                    -> a full-scope admin (scope == 'all', or the 'admin' role)

    Two deliberate corrections to the previous implementation, both security fixes:
      * `scope` is now compared STRICTLY to 'all'. It read `(perms.get('scope') or 'all')`, so a
        role whose JSONB merely LACKED a 'scope' key was silently granted everything — PII reveal
        included. Every seeded role carries an explicit scope, so strictness costs nothing.
      * the key is looked up in the platform's NESTED bags (permissions.data / permissions.settings)
        as well as flat. Reading only flat made the explicit-grant branch dead code, because the
        Roles UI writes nested — which collapsed the whole gate to "scope is all, or absent"."""
    if not ctx:
        return False
    if ctx.get("super_admin"):
        return True
    perms = ctx.get("perms") or {}
    for bag in (perms.get("data") or {}, perms.get("settings") or {}, perms):
        if isinstance(bag, dict) and key in bag and isinstance(bag.get(key), bool):
            return bag[key]
    return (perms.get("scope") == "all") or ((ctx.get("role") or "").lower() == "admin")


def _require_pos_perm(authorization: str, org_id: str, key: str):
    """403 unless the caller holds `key`. Unresolvable callers are DENIED — fails closed."""
    ctx = _caller_ctx(authorization, org_id)
    if ctx is None:
        raise HTTPException(401, "sign in to perform this action")
    if _pos_grant(ctx, key):
        return
    raise HTTPException(403, f"your role does not allow this action ({key})")


def _require_member(authorization: str, org_id: str) -> str:
    """401/403 unless the caller may act on this org; returns their auth uid.

    A member of the org passes. A PLATFORM SUPER-ADMIN passes on ANY org — the same standing
    _caller_ctx already grants, and the same posture tenant_middleware takes (no org_id rewrite for a
    super-admin, so the tenant they picked in the switcher is honoured).

    WHY THIS SECOND PLACE EXISTS (2026-08-10). The 2026-08-09 fix (f4c39b2) taught `_caller_ctx` to
    recognise super-admin standing from any membership, to unblock the owner's "the POS set up wizard
    is not letting me update the sales tax". It could not work: this function is called by
    `_require_pos_access`, the APIRouter-level dependency, and a router dependency runs BEFORE the
    endpoint body — so a super-admin with no `app_users` row in the acting tenant was refused here,
    with `_caller_ctx` never reached. Measured on prod 2026-08-10: the owner's login holds exactly ONE
    membership (the house org), so on Luxelink/Vzone EVERY POS endpoint — the wizard's GETs included —
    answered 403.

    IT CANNOT LEAK. The extra branch tests one thing, `super_admin = true` on this uid, and grants
    nothing else: a non-super-admin with no membership in the acting org is still refused, which is the
    control that keeps this the opposite of the 2026-08-09 HR gate (that one ignored the tenant
    switcher and served another tenant's employees to an ordinary admin)."""
    from app.modules.core.router import _uid_from_token
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "sign in to perform this action")
    rows = (sb().schema("storeops").table("app_users").select("id")
            .eq("org_id", org_id).eq("auth_id", uid).limit(1).execute().data) or []
    if not rows and not _super_admin_login(uid):
        raise HTTPException(403, "your login is not a member of this organization")
    return uid


def _caller_store_keyset(authorization: str, org_id: str):
    """The caller's REPORTING span as a keyset (None = unrestricted), via the platform's
    canonical scope machinery — the same store-scoping every sibling module applies to reads."""
    try:
        from app.modules.storeops.router import scope_keyset
        return scope_keyset(authorization, org_id)
    except Exception as e:
        # FAIL CLOSED. `None` is the UNRESTRICTED sentinel, so swallowing the error here used to
        # hand a store-scoped caller the whole org whenever scope resolution hiccupped — the one
        # place this module chose "allow" on error. A visible 403 is the correct outcome: it is
        # recoverable and obvious, where silent org-wide exposure is neither.
        raise HTTPException(403, f"could not resolve your store scope, so access is denied: {e}")


def _span_filter(rows, keyset, field="store_code", allow_null=False):
    """Keep rows whose store is inside the caller's span.

    `allow_null` controls what happens to rows with NO store, and defaults to EXCLUDING them.
    It previously let every such row through on the theory that they are "org-level records".
    That holds for genuinely org-level tables (tax codes, settings) but NOT for the tables this
    is actually applied to — pos.sales, pos.activations and pos.inventory_serial all have a
    NULLABLE store_code, and pos.checkout writes NULL whenever the caller omits it. So the
    writer, not the reader's role, decided who could see a record: post a sale with no
    store_code and every store-scoped manager in the tenant sees it. Pass allow_null=True only
    for a table where a NULL store genuinely means org-wide."""
    if keyset is None:
        return rows
    from app.core.scope import in_keyset
    return [r for r in rows
            if (allow_null if not r.get(field) else in_keyset(keyset, r.get(field)))]


# ── Customers ──────────────────────────────────────────────────────────────────────────────────────
# Explicit column lists, never select('*'). `password` — the carrier ACCOUNT PIN, i.e. the
# credential used for SIM-swap and account takeover — must not ride along on a bulk read, which
# would contradict this module's own threat model ("a compromised cashier session cannot exfiltrate
# the customer book"). It survives only on the single-record fetch, where the edit form needs it.
#
# SSN and driver's licence are GONE from this table entirely (mig 909, owner directive): the
# platform no longer collects, stores or displays them. Do not add them back — the cheapest defence
# against a breach-notification obligation is not holding the data that triggers one.
CUSTOMER_READ_COLS = (
    "id,org_id,cust_number,account_type,company_name,first_name,last_name,middle_initial,dob,"
    "primary_account_no,email,phone_primary,phone_secondary,address_1,"
    "address_2,city,state,zip,referral_source,credit_limit,accept_checks,is_active,"
    "created_at,updated_at"
)
CUSTOMER_DETAIL_COLS = CUSTOMER_READ_COLS + ",password"


@router.get("/customers")
def list_customers(search: str = "", active_only: bool = True, org_id: str = ORG_ID,
                   authorization: str = Header(default="")):
    _require_member(authorization, org_id)
    q = sb().schema("pos").table("customers").select(CUSTOMER_READ_COLS).eq("org_id", org_id)
    if active_only:
        q = q.eq("is_active", True)
    s = search.strip().replace("%", "").replace(",", " ")
    if s:
        ors = (f"first_name.ilike.%{s}%,last_name.ilike.%{s}%,company_name.ilike.%{s}%,"
               f"phone_primary.ilike.%{s}%,phone_secondary.ilike.%{s}%,email.ilike.%{s}%,"
               f"primary_account_no.ilike.%{s}%")
        if s.isdigit():
            ors += f",cust_number.eq.{s}"
        q = q.or_(ors)
    rows = q.order("created_at", desc=True).limit(300).execute().data or []
    return {"customers": rows}


@router.get("/customers/{customer_id}")
def get_customer(customer_id: str, org_id: str = ORG_ID,
                 authorization: str = Header(default="")):
    """Single-customer fetch — deep links (?customer= / ?sale= prefills) must not depend on
    the list endpoint's newest-300 page. This is also the ONLY read that returns `password`,
    because the edit form has to round-trip it; one record at a time is a very different
    exposure from the whole book."""
    _require_member(authorization, org_id)
    rows = (sb().schema("pos").table("customers").select(CUSTOMER_DETAIL_COLS)
            .eq("org_id", org_id).eq("id", customer_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "not found")
    return {"customer": rows[0]}


@router.post("/customers")
def create_customer(body: dict, org_id: str = ORG_ID):
    ins = _clean_customer(body)
    if not (ins.get("first_name") or ins.get("last_name") or ins.get("company_name")):
        raise HTTPException(400, "a name is required")
    ins["org_id"] = org_id
    r = sb().schema("pos").table("customers").insert(ins).execute()
    return {"customer": (r.data or [{}])[0]}


@router.patch("/customers/{customer_id}")
def update_customer(customer_id: str, body: dict, org_id: str = ORG_ID):
    upd = _clean_customer(body)
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (sb().schema("pos").table("customers").update(upd)
         .eq("org_id", org_id).eq("id", customer_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"customer": r.data[0]}


@router.get("/customers/{customer_id}/notes")
def list_customer_notes(customer_id: str, org_id: str = ORG_ID):
    rows = (sb().schema("pos").table("customer_notes").select("*")
            .eq("org_id", org_id).eq("customer_id", customer_id)
            .order("created_at", desc=True).limit(200).execute().data) or []
    return {"notes": rows}


@router.post("/customers/{customer_id}/notes")
def add_customer_note(customer_id: str, body: dict,
                      authorization: str = Header(default=""), org_id: str = ORG_ID):
    note = (body.get("note") or "").strip()
    if not note:
        raise HTTPException(400, "note text required")
    severity = body.get("severity") or "normal"
    if severity not in ("normal", "important", "urgent"):
        severity = "normal"
    r = sb().schema("pos").table("customer_notes").insert({
        "org_id": org_id, "customer_id": customer_id, "note": note, "severity": severity,
        "employee_id": _caller_employee(authorization, org_id) or None,
    }).execute()
    return {"note": (r.data or [{}])[0]}


# ── Inventory ──────────────────────────────────────────────────────────────────────────────────────
def _product_names(org_id: str, ids, extra_cols=""):
    """product_id -> {short_name, product_code, ...} for JUST the ids on the page."""
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    cols = "id,short_name,product_code" + ("," + extra_cols if extra_cols else "")
    out = {}
    for i in range(0, len(ids), 100):
        for p in (sb().schema("pos").table("products").select(cols)
                  .eq("org_id", org_id).in_("id", ids[i:i + 100]).execute().data or []):
            out[p["id"]] = p
    return out


@router.get("/inventory/serial")
def list_inventory_serial(search: str = "", store_code: str = "", status: str = "",
                          product_id: str = "", authorization: str = Header(default=""),
                          org_id: str = ORG_ID):
    q = sb().schema("pos").table("inventory_serial").select("*").eq("org_id", org_id)
    if store_code:
        q = q.eq("store_code", store_code)
    if status:
        q = q.eq("status", status)
    if product_id:
        q = q.eq("product_id", product_id)
    s = search.strip().replace("%", "").replace(",", " ")
    if s:
        q = q.or_(f"serial_number.ilike.%{s}%,imei.ilike.%{s}%,sim_card.ilike.%{s}%")
    rows = q.order("created_at", desc=True).limit(500).execute().data or []
    rows = _span_filter(rows, _caller_store_keyset(authorization, org_id))
    prods = _product_names(org_id, [r.get("product_id") for r in rows])
    for r in rows:
        p = prods.get(r.get("product_id")) or {}
        r["product_name"] = p.get("short_name")
        r["product_code"] = p.get("product_code")
    return {"units": rows}


@router.post("/inventory/serial")
def add_inventory_serial(body: dict, org_id: str = ORG_ID):
    ins = {k: body[k] for k in SERIAL_FIELDS if k in body}
    for k in ("imei", "sim_card", "color", "storage", "cost", "date_received", "po_number",
              "store_code"):
        if k in ins and ins[k] == "":
            ins[k] = None
    if not (ins.get("product_id") and (ins.get("serial_number") or "").strip()):
        raise HTTPException(400, "product_id and serial_number required")
    ins["serial_number"] = ins["serial_number"].strip()
    ins["org_id"] = org_id
    r = sb().schema("pos").table("inventory_serial").insert(ins).execute()
    return {"unit": (r.data or [{}])[0]}


@router.patch("/inventory/serial/{unit_id}")
def update_inventory_serial(unit_id: str, body: dict, org_id: str = ORG_ID):
    upd = {k: body[k] for k in SERIAL_FIELDS if k in body}
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (sb().schema("pos").table("inventory_serial").update(upd)
         .eq("org_id", org_id).eq("id", unit_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"unit": r.data[0]}


@router.get("/inventory/standard")
def list_inventory_standard(store_code: str = "", authorization: str = Header(default=""),
                            org_id: str = ORG_ID):
    q = sb().schema("pos").table("inventory_standard").select("*").eq("org_id", org_id)
    if store_code:
        q = q.eq("store_code", store_code)
    rows = q.order("updated_at", desc=True).limit(1000).execute().data or []
    rows = _span_filter(rows, _caller_store_keyset(authorization, org_id))
    prods = _product_names(org_id, [r.get("product_id") for r in rows],
                           extra_cols="retail_price")
    for r in rows:
        p = prods.get(r.get("product_id")) or {}
        r["product_name"] = p.get("short_name")
        r["product_code"] = p.get("product_code")
        r["retail_price"] = p.get("retail_price")
    return {"stock": rows}


# ── Tax codes ──────────────────────────────────────────────────────────────────────────────────────
# rate is a PERCENT (8.875); the register converts to a fraction for sale_items.tax_rate.
@router.get("/tax-codes")
def list_tax_codes(org_id: str = ORG_ID):
    rows = (sb().schema("pos").table("tax_codes").select("*").eq("org_id", org_id)
            .order("created_at").limit(200).execute().data) or []
    return {"tax_codes": rows}


def _resolve_tax(codes, store_code, market):
    """THE tax precedence, in one place: store_code > market > org-wide. Inactive rows never win.

    pos.checkout does not compute tax — it stores the tax_total it is given — so every caller must
    reach a rate through here. Reimplementing this ordering anywhere else is how one screen charges
    8.875% and another charges 0% for the same store."""
    live = [c for c in (codes or []) if c.get("is_active") is not False]
    sc = (store_code or "").strip()
    mk = (market or "").strip().lower()
    if sc:
        hit = next((c for c in live if (c.get("store_code") or "").strip() == sc), None)
        if hit:
            return hit, "store"
    if mk:
        hit = next((c for c in live if (c.get("market") or "").strip().lower() == mk), None)
        if hit:
            return hit, "market"
    hit = next((c for c in live
                if not (c.get("store_code") or "").strip() and not (c.get("market") or "").strip()), None)
    return (hit, "org") if hit else (None, "none")


@router.get("/tax-codes/resolve")
def resolve_tax_code(store_code: str = "", org_id: str = ORG_ID):
    """The rate that applies at ONE store, and WHY. The register's single source of truth.

    `scope` says which rung won (store / market / org / none) so a cashier screen can show
    "inherited from market" instead of a bare number, and `none` is explicit rather than a silent 0 —
    a taxable sale with no rate charges nothing, and that is not recoverable after the customer leaves."""
    # org_id arrives already rewritten by the tenant middleware; the sibling read endpoints in this
    # module (list_tax_codes, store-grid) gate the same way — reads are org-scoped, writes carry
    # _require_pos_perm.
    client = sb()
    codes = (client.schema("pos").table("tax_codes").select("*")
             .eq("org_id", org_id).limit(1000).execute().data) or []
    market = ""
    sc = (store_code or "").strip()
    if sc:
        try:
            st = (client.schema("storeops").table("stores").select("market")
                  .eq("org_id", org_id).eq("store_code", sc).limit(1).execute().data) or []
            market = (st[0].get("market") or "") if st else ""
        except Exception:
            market = ""
    hit, scope = _resolve_tax(codes, sc, market)
    return {"store_code": sc or None, "market": market or None, "scope": scope,
            "rate": (hit or {}).get("rate"), "tax_code": hit,
            "warning": ("No tax rate applies to this store — a taxable sale would charge $0."
                        if scope == "none" else None)}


@router.get("/tax-codes/markets")
def tax_code_markets(org_id: str = ORG_ID):
    """Distinct markets with their store counts — the market dropdown, pick-don't-type."""
    client = sb()
    try:
        rows = (client.schema("storeops").table("stores").select("market,store_code,is_active")
                .eq("org_id", org_id).limit(2000).execute().data) or []
    except Exception:
        rows = []
    agg = {}
    for s in rows:
        if s.get("is_active") is False:
            continue
        m = (s.get("market") or "").strip()
        if m:
            agg[m] = agg.get(m, 0) + 1
    codes = (client.schema("pos").table("tax_codes").select("market,rate")
             .eq("org_id", org_id).limit(1000).execute().data) or []
    rate_by_market = {(c.get("market") or "").strip(): c.get("rate")
                      for c in codes if (c.get("market") or "").strip()}
    return {"markets": [{"market": m, "stores": n, "rate": rate_by_market.get(m)}
                        for m, n in sorted(agg.items())]}


@router.get("/tax-codes/store-grid")
def tax_code_store_grid(org_id: str = ORG_ID):
    """Every CONFIGURED store with the rate it currently has — the owner's dropdown (2026-08-09).

    "give the store which are already configured as a drop down menu to assign them the respective
    sales tax, if not then it should present a spreadsheet to enter." So this returns the stores the
    tenant already has, each with its existing rate (or the org-wide rate it inherits), and tells the
    caller which of the two UIs to render via `mode`:
        mode='stores' -> pick from these; mode='blank' -> no stores yet, show an empty grid.

    is_active is NULLABLE, so it is filtered IS NOT FALSE rather than == True — the platform's
    standing rule; `.eq(True)` silently drops every store whose flag was never set."""
    client = sb()
    try:
        stores = (client.schema("storeops").table("stores")
                  .select("store_code,address,market,is_active")
                  .eq("org_id", org_id).order("store_code").limit(1000).execute().data) or []
    except Exception:
        stores = []
    stores = [s for s in stores if s.get("is_active") is not False]
    codes = (client.schema("pos").table("tax_codes").select("*")
             .eq("org_id", org_id).limit(1000).execute().data) or []
    by_store = {(c.get("store_code") or "").strip(): c for c in codes if (c.get("store_code") or "").strip()}
    org_wide = next((c for c in codes if not (c.get("store_code") or "").strip()), None)
    rows = []
    for s in stores:
        hit, scope = _resolve_tax(codes, s.get("store_code"), s.get("market"))
        own = by_store.get(s.get("store_code")) or {}
        rows.append({
            "store_code": s.get("store_code"),
            "address": s.get("address"), "market": s.get("market"),
            "rate": own.get("rate"),                       # a rate set ON this store, if any
            "effective_rate": (hit or {}).get("rate"),      # what it would actually charge
            "effective_scope": scope,                       # store | market | org | none
            "tax_code_id": own.get("id"), "name": own.get("name"),
            "inherits_org_rate": scope != "store",
        })
    return {"mode": "stores" if rows else "blank", "stores": rows,
            "org_wide": org_wide, "store_count": len(rows),
            "configured": sum(1 for r in rows if r["rate"] is not None)}


@router.post("/tax-codes/bulk")
def bulk_set_tax_codes(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Assign rates to many stores at once — what the grid saves.

    Each entry is {store_code, rate, name?}. A store already carrying a rate is UPDATED rather than
    duplicated (two rates for one store is the one outcome that would silently mis-charge tax).
    An empty/absent rate on an entry means "leave this store alone", so a half-filled grid is safe
    to save; send is_active=false to retire a rate instead."""
    _require_pos_perm(authorization, org_id, "pos_settings")
    entries = list(body.get("entries") or [])
    # MARKET + MULTI-STORE (owner 2026-08-09). `market` writes ONE market-scoped row rather than
    # fanning out per store, so changing that market's rate later is one edit and cannot drift.
    # `store_codes` applies the same rate to several stores in one action.
    mk = str(body.get("market") or "").strip()
    rate_for_many = body.get("rate")
    if mk:
        try:
            mrate = float(rate_for_many)
        except (TypeError, ValueError):
            raise HTTPException(400, "rate must be a number (percent) when assigning a market")
        if not (0 <= mrate <= 30):
            raise HTTPException(400, "rate must be 0-30 (percent)")
        client0 = sb()
        cur = (client0.schema("pos").table("tax_codes").select("id")
               .eq("org_id", org_id).eq("market", mk).limit(1).execute().data) or []
        nm = str(body.get("name") or "").strip() or f"{mk} sales tax"
        if cur:
            (client0.schema("pos").table("tax_codes").update({"rate": mrate, "name": nm})
             .eq("org_id", org_id).eq("id", cur[0]["id"]).execute())
        else:
            client0.schema("pos").table("tax_codes").insert({
                "org_id": org_id, "name": nm, "rate": mrate,
                "market": mk, "store_code": None}).execute()
        if not entries and not body.get("store_codes"):
            return {"created": 0 if cur else 1, "updated": 1 if cur else 0,
                    "market": mk, "skipped": [], "total": 0}
    for scode in (body.get("store_codes") or []):
        entries.append({"store_code": scode, "rate": rate_for_many, "name": body.get("name")})
    if not isinstance(entries, list) or not entries:
        raise HTTPException(400, "entries, store_codes or market required")
    if len(entries) > 1000:
        raise HTTPException(400, "max 1000 entries per request")
    client = sb()
    codes = (client.schema("pos").table("tax_codes").select("id,store_code")
             .eq("org_id", org_id).limit(1000).execute().data) or []
    by_store = {(c.get("store_code") or "").strip(): c.get("id")
                for c in codes if (c.get("store_code") or "").strip()}
    created, updated, skipped = 0, 0, []
    for i, e in enumerate(entries):
        store = str((e or {}).get("store_code") or "").strip()
        if not store:
            skipped.append({"index": i, "message": "store_code required"})
            continue
        raw = (e or {}).get("rate")
        if raw is None or str(raw).strip() == "":
            continue                      # untouched row in a partially-filled grid
        try:
            rate = float(raw)
        except (TypeError, ValueError):
            skipped.append({"index": i, "message": "rate must be a number (percent)"})
            continue
        if not (0 <= rate <= 30):
            skipped.append({"index": i, "message": "rate must be 0-30 (percent)"})
            continue
        name = str((e or {}).get("name") or "").strip() or f"{store} sales tax"
        if store in by_store:
            (client.schema("pos").table("tax_codes").update({"rate": rate, "name": name})
             .eq("org_id", org_id).eq("id", by_store[store]).execute())
            updated += 1
        else:
            r = client.schema("pos").table("tax_codes").insert({
                "org_id": org_id, "name": name, "rate": rate, "store_code": store}).execute()
            if r.data:
                by_store[store] = r.data[0].get("id")
            created += 1
    return {"created": created, "updated": updated, "skipped": skipped,
            "total": len(entries)}


@router.post("/tax-codes")
def create_tax_code(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    name = (body.get("name") or "").strip()
    try:
        rate = float(body.get("rate"))
    except (TypeError, ValueError):
        raise HTTPException(400, "rate must be a number")
    if not name or not (0 <= rate <= 30):
        raise HTTPException(400, "name required and rate must be 0–30 (percent)")
    r = sb().schema("pos").table("tax_codes").insert({
        "org_id": org_id, "name": name, "rate": rate,
        "store_code": (body.get("store_code") or "").strip() or None,
    }).execute()
    return {"tax_code": (r.data or [{}])[0]}


@router.patch("/tax-codes/{code_id}")
def update_tax_code(code_id: str, body: dict, authorization: str = Header(default=""),
                    org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    upd = {k: body[k] for k in TAX_CODE_FIELDS if k in body}
    for k in ("store_code", "market"):
        if k in upd and str(upd[k] or "").strip() == "":
            upd[k] = None                      # "" is the UI's way of saying "clear this rung"
        elif k in upd:
            upd[k] = str(upd[k]).strip()
    # Migration 741 already REFUSES a row naming both a store and a market at the database, because
    # such a row belongs to neither rung and resolves differently depending on which query finds it
    # first. Catching it here turns a raw constraint-violation string into a sentence, and keeps the
    # two statements of the same rule from drifting.
    if (upd.get("store_code") or "") and (upd.get("market") or ""):
        raise HTTPException(400, "a tax code applies to a store OR a market, not both — "
                                 "clear one of them")
    if "rate" in upd:
        try:
            upd["rate"] = float(upd["rate"])
        except (TypeError, ValueError):
            raise HTTPException(400, "rate must be a number")
        if not (0 <= upd["rate"] <= 30):
            raise HTTPException(400, "rate must be 0–30 (percent)")
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (sb().schema("pos").table("tax_codes").update(upd)
         .eq("org_id", org_id).eq("id", code_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"tax_code": r.data[0]}


def _date_bound(v: str, end: bool) -> str:
    """Accept either a bare local date (legacy: treated as a UTC calendar day) or a full ISO
    instant (what the pages now send, computed from the store's local midnight)."""
    if len(v) == 10:
        return v + ("T23:59:59" if end else "T00:00:00")
    return v


# ── Sales ──────────────────────────────────────────────────────────────────────────────────────────
@router.get("/sales")
def list_sales(date_from: str = "", date_to: str = "", store_code: str = "",
               employee_id: str = "", status: str = "",
               authorization: str = Header(default=""), org_id: str = ORG_ID):
    q = sb().schema("pos").table("sales").select("*").eq("org_id", org_id)
    if date_from:
        q = q.gte("created_at", _date_bound(date_from, end=False))
    if date_to:
        q = q.lte("created_at", _date_bound(date_to, end=True))
    if store_code:
        q = q.eq("store_code", store_code)
    if employee_id:
        q = q.eq("employee_id", employee_id)
    if status:
        q = q.eq("status", status)
    rows = q.order("created_at", desc=True).limit(500).execute().data or []
    rows = _span_filter(rows, _caller_store_keyset(authorization, org_id))
    cust_ids = sorted({r["customer_id"] for r in rows if r.get("customer_id")})
    names = {}
    if cust_ids:
        for c in (sb().schema("pos").table("customers")
                  .select("id,first_name,last_name,company_name")
                  .eq("org_id", org_id).in_("id", cust_ids).execute().data or []):
            names[c["id"]] = (f"{c.get('first_name') or ''} {c.get('last_name') or ''}".strip()
                              or c.get("company_name") or "")
    for r in rows:
        r["customer_name"] = names.get(r.get("customer_id"))
    return {"sales": rows}


@router.get("/sales/{sale_id}")
def get_sale(sale_id: str, org_id: str = ORG_ID):
    rows = (sb().schema("pos").table("sales").select("*")
            .eq("org_id", org_id).eq("id", sale_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "not found")
    sale = rows[0]
    sale["items"] = (sb().schema("pos").table("sale_items").select("*")
                     .eq("sale_id", sale_id).order("created_at").execute().data) or []
    sale["payments"] = (sb().schema("pos").table("sale_payments").select("*")
                        .eq("sale_id", sale_id).order("created_at").execute().data) or []
    return {"sale": sale}


@router.post("/sales/checkout")
def checkout(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Atomic checkout via pos.checkout: sale + items + payments in ONE transaction (the
    standalone app's three separate inserts could strand a header-only sale). The inventory
    trigger fires inside it, so an out-of-stock block rolls the whole sale back."""
    sale = dict(body.get("sale") or {})
    items = body.get("items") or []
    payments = body.get("payments") or []
    if not items:
        raise HTTPException(400, "a sale needs at least one item")
    eid = _caller_employee(authorization, org_id)
    if not eid:
        # no body fallback, ever: an unlinked login cannot attribute a sale to anyone
        raise HTTPException(403, "your login isn't linked to an employee record — "
                                 "ask an admin to set your Employee ID in Roles & Access")
    sale["employee_id"] = eid   # never trust a body-supplied rep on the money path
    # The register may now edit unit_price (owner directive 2026-08-11). Keep what the product was
    # LISTED at so an override is visible afterwards as list_price <> unit_price — a line that
    # records only what was charged makes a discounted sale indistinguishable from a cheap product.
    # A client that sends no list_price is recorded as "no override" by pos.checkout's COALESCE.
    for _it in items:
        if isinstance(_it, dict) and _it.get("list_price") in (None, ""):
            _it["list_price"] = _it.get("unit_price")
    try:
        r = sb().schema("pos").rpc("checkout", {
            "p_org": org_id, "p_sale": sale, "p_items": items, "p_payments": payments,
        }).execute()
    except Exception as e:
        raise HTTPException(400, f"checkout failed: {e}")
    return {"sale": r.data}


@router.post("/sales/{sale_id}/void")
def void_sale(sale_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_void")
    try:
        r = sb().schema("pos").rpc("void_sale", {
            "p_org": org_id, "p_sale": sale_id,
            "p_employee": _caller_employee(authorization, org_id) or None,
        }).execute()
    except Exception as e:
        raise HTTPException(400, f"void failed: {e}")
    return {"sale": r.data}


# ── Register (cash drawer) sessions ────────────────────────────────────────────────────────────────
@router.get("/register/session")
def register_session(store_code: str, register_number: int = 1, org_id: str = ORG_ID):
    rows = (sb().schema("pos").table("register_sessions").select("*")
            .eq("org_id", org_id).eq("store_code", store_code)
            .eq("register_number", register_number).eq("status", "open")
            .limit(1).execute().data) or []
    return {"session": rows[0] if rows else None}


@router.post("/register/open")
def register_open(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    store = (body.get("store_code") or "").strip()
    if not store:
        raise HTTPException(400, "store_code required")
    try:
        r = sb().schema("pos").rpc("open_register", {
            "p_org": org_id, "p_store": store,
            "p_register": int(body.get("register_number") or 1),
            "p_employee": _caller_employee(authorization, org_id) or None,
            "p_float": float(body.get("opening_float") or 0),
            "p_denominations": body.get("denominations"),
        }).execute()
    except Exception as e:
        raise HTTPException(400, f"open failed: {e}")
    return {"session": r.data}


@router.post("/register/close")
def register_close(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    if not body.get("session_id"):
        raise HTTPException(400, "session_id required")
    try:
        r = sb().schema("pos").rpc("close_register", {
            "p_org": org_id, "p_session": body["session_id"],
            "p_counted": float(body.get("counted") or 0),
            "p_employee": _caller_employee(authorization, org_id) or None,
            "p_denominations": body.get("denominations"),
            "p_notes": (body.get("notes") or "").strip() or None,
        }).execute()
    except Exception as e:
        raise HTTPException(400, f"close failed: {e}")
    return {"session": r.data}


# ── POS settings (config kv) ───────────────────────────────────────────────────────────────────────
# Raw rows out; the frontend registry (lib/pos-config.ts) resolves store override → org default →
# code default and renders the inheritance badges.
@router.get("/settings")
def list_settings(store_code: str = "", org_id: str = ORG_ID):
    q = (sb().schema("pos").table("pos_settings").select("store_code,key,value")
         .eq("org_id", org_id))
    if store_code:
        q = q.or_(f"store_code.is.null,store_code.eq.{store_code}")
    rows = q.limit(1000).execute().data or []
    return {"settings": rows}


@router.put("/settings")
def upsert_setting(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    key = (body.get("key") or "").strip()
    if not key or "value" not in body:
        raise HTTPException(400, "key and value required")
    store = (body.get("store_code") or "").strip() or None
    q = (sb().schema("pos").table("pos_settings")
         .update({"value": body["value"], "updated_at": "now()"})
         .eq("org_id", org_id).eq("key", key))
    q = q.eq("store_code", store) if store else q.is_("store_code", "null")
    r = q.execute()
    if not r.data:
        r = sb().schema("pos").table("pos_settings").insert(
            {"org_id": org_id, "store_code": store, "key": key,
             "value": body["value"]}).execute()
    return {"setting": (r.data or [{}])[0]}


@router.delete("/settings")
def delete_setting(key: str, store_code: str = "",
                   authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Remove a row — used to revert a store override back to inherited (or clear an org default
    back to the code-side default)."""
    _require_pos_perm(authorization, org_id, "pos_settings")
    q = (sb().schema("pos").table("pos_settings").delete()
         .eq("org_id", org_id).eq("key", key))
    q = q.eq("store_code", store_code) if store_code else q.is_("store_code", "null")
    q.execute()
    return {"ok": True}


# ── Receipt template ───────────────────────────────────────────────────────────────────────────────
@router.get("/receipt-template")
def get_receipt_template(org_id: str = ORG_ID):
    """The org's default template, created on first read (parity with the standalone app's
    per-org seeded row)."""
    rows = (sb().schema("pos").table("receipt_templates").select("*")
            .eq("org_id", org_id).eq("is_default", True).limit(1).execute().data) or []
    if rows:
        return {"template": rows[0]}
    r = sb().schema("pos").table("receipt_templates").insert(
        {"org_id": org_id, "name": "Default", "is_default": True}).execute()
    return {"template": (r.data or [{}])[0]}


@router.patch("/receipt-template/{template_id}")
def update_receipt_template(template_id: str, body: dict,
                            authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    upd = {k: body[k] for k in RECEIPT_TEMPLATE_FIELDS if k in body}
    if not upd:
        raise HTTPException(400, "nothing to update")
    if "footer_text" in upd and upd["footer_text"] is None:
        upd["footer_text"] = ""   # column is NOT NULL; a cleared footer means empty
    upd["updated_at"] = "now()"
    r = (sb().schema("pos").table("receipt_templates").update(upd)
         .eq("org_id", org_id).eq("id", template_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"template": r.data[0]}


@router.get("/register/drawer-cash")
def register_drawer_cash(session_id: str, org_id: str = ORG_ID):
    """Cash currently in the drawer for an open session: opening float + cash payments on
    non-voided sales at that store since open. Drives the max_cash_in_drawer pre-checkout block."""
    rows = (sb().schema("pos").table("register_sessions").select("*")
            .eq("org_id", org_id).eq("id", session_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "session not found")
    s = rows[0]
    sales = (sb().schema("pos").table("sales").select("id,status")
             .eq("org_id", org_id).eq("store_code", s["store_code"])
             .gte("created_at", s["opened_at"]).neq("status", "voided")
             .limit(1000).execute().data) or []
    ids = [x["id"] for x in sales]
    cash = 0.0
    if ids:
        pays = (sb().schema("pos").table("sale_payments").select("amount")
                .in_("sale_id", ids).eq("payment_method", "cash")
                .limit(2000).execute().data) or []
        cash = sum(float(p.get("amount") or 0) for p in pays)
    return {"cash": float(s.get("opening_float") or 0) + cash}


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# Phase 2 — activations, vendors, purchase orders, transfers, reports, import (mig 726)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════

ACTIVATION_FIELDS = ("sale_id", "customer_id", "store_code", "carrier", "activation_date",
                     "service_plan_date", "service_plan_id", "plan_code", "plan_description",
                     "monthly_fee", "included_minutes", "service_area", "contract_type",
                     "contract_terms", "dealer_code", "cell_number", "phone_serial", "phone_model",
                     "sim_card", "mobile_phone", "account_number", "deposit_amount", "memo",
                     "description", "promotion_offered", "trade_in_credit", "special_promo",
                     "status")
VENDOR_FIELDS = ("ban", "legal_name", "short_name", "business_type", "street_one", "street_two",
                 "city", "state", "zip", "country", "tax_id", "contact_name", "phone", "fax",
                 "email", "website", "is_active")
SERVICE_PLAN_FIELDS = ("carrier", "plan_code", "plan_name", "plan_description", "monthly_fee",
                       "included_minutes", "service_area", "contract_type", "contract_terms",
                       "dealer_code", "status")


def _require_any_pos_perm(authorization: str, org_id: str, keys):
    """403 unless the caller holds at least one of `keys`. Shares _pos_grant's precedence, so an
    explicit deny on one key still lets another key grant access. Unresolvable callers are
    DENIED — fails closed like _require_pos_perm."""
    ctx = _caller_ctx(authorization, org_id)
    if ctx is None:
        raise HTTPException(401, "sign in to perform this action")
    if any(_pos_grant(ctx, k) for k in keys):
        return
    raise HTTPException(403, f"your role does not allow this action ({' / '.join(keys)})")


def _employee_names(org_id: str) -> dict:
    """employee_id -> display name, from the platform roster."""
    rows = (sb().schema("storeops").table("employees").select("employee_id,name")
            .eq("org_id", org_id).limit(2000).execute().data) or []
    return {(r.get("employee_id") or "").strip(): r.get("name")
            for r in rows if (r.get("employee_id") or "").strip()}


def _customer_names(org_id: str, ids) -> dict:
    out = {}
    ids = sorted({i for i in ids if i})
    if not ids:
        return out
    for c in (sb().schema("pos").table("customers")
              .select("id,first_name,last_name,company_name,cust_number")
              .eq("org_id", org_id).in_("id", ids).execute().data or []):
        out[c["id"]] = {
            "name": (f"{c.get('first_name') or ''} {c.get('last_name') or ''}".strip()
                     or c.get("company_name") or ""),
            "cust_number": c.get("cust_number"),
        }
    return out


def _clean_nullable(d: dict, keys) -> dict:
    for k in keys:
        if k in d and d[k] in ("", "null"):
            d[k] = None
    return d


# ── Activations ────────────────────────────────────────────────────────────────────────────────────
@router.get("/activations")
def list_activations(date_from: str = "", date_to: str = "", store_code: str = "",
                     carrier: str = "", status: str = "",
                     authorization: str = Header(default=""), org_id: str = ORG_ID):
    q = sb().schema("pos").table("activations").select("*").eq("org_id", org_id)
    if date_from:
        q = q.gte("activation_date", date_from)
    if date_to:
        q = q.lte("activation_date", date_to)
    if store_code:
        q = q.eq("store_code", store_code)
    if carrier:
        q = q.eq("carrier", carrier)
    if status:
        q = q.eq("status", status)
    rows = q.order("created_at", desc=True).limit(300).execute().data or []
    rows = _span_filter(rows, _caller_store_keyset(authorization, org_id))
    cust = _customer_names(org_id, [r.get("customer_id") for r in rows])
    for r in rows:
        c = cust.get(r.get("customer_id")) or {}
        r["customer_name"] = c.get("name")
        r["customer_cust_number"] = c.get("cust_number")
    return {"activations": rows}


@router.get("/activations/{act_id}")
def get_activation(act_id: str, org_id: str = ORG_ID):
    rows = (sb().schema("pos").table("activations").select("*")
            .eq("org_id", org_id).eq("id", act_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "not found")
    act = rows[0]
    ti = (sb().schema("pos").table("trade_ins").select("*")
          .eq("org_id", org_id).eq("activation_id", act_id).limit(1).execute().data) or []
    act["trade_in"] = ti[0] if ti else None
    if act.get("sale_id"):
        s = (sb().schema("pos").table("sales").select("id,transaction_id")
             .eq("id", act["sale_id"]).limit(1).execute().data) or []
        act["sale_transaction_id"] = s[0]["transaction_id"] if s else None
    return {"activation": act}


def _clean_activation(body: dict) -> dict:
    upd = {k: body[k] for k in ACTIVATION_FIELDS if k in body}
    _clean_nullable(upd, ("sale_id", "customer_id", "service_plan_id", "activation_date",
                          "service_plan_date", "store_code"))
    # cell == mobile: one field, write both (owner decision from the standalone app)
    if "cell_number" in upd and "mobile_phone" not in upd:
        upd["mobile_phone"] = upd["cell_number"] or None
    if upd.get("status"):
        upd["status"] = str(upd["status"]).strip().lower()   # CSV exports write 'Active'
        if upd["status"] not in ("active", "cancelled", "transferred"):
            raise HTTPException(400, "invalid status")
    return upd


@router.post("/activations")
def create_activation(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    ins = _clean_activation(body)
    eid = _caller_employee(authorization, org_id)
    if not eid:
        raise HTTPException(403, "your login isn't linked to an employee record — "
                                 "ask an admin to set your Employee ID in Roles & Access")
    ins["employee_id"] = eid
    ins["org_id"] = org_id
    if ins.get("status") == "cancelled":
        _require_pos_perm(authorization, org_id, "pos_activations_cancel")
    r = sb().schema("pos").table("activations").insert(ins).execute()
    return {"activation": (r.data or [{}])[0]}


@router.patch("/activations/{act_id}")
def update_activation(act_id: str, body: dict, authorization: str = Header(default=""),
                      org_id: str = ORG_ID):
    upd = _clean_activation(body)   # employee_id NOT writable: attribution is preserved on edits
    if not upd:
        raise HTTPException(400, "nothing to update")
    if upd.get("status") == "cancelled":
        cur = (sb().schema("pos").table("activations").select("status")
               .eq("org_id", org_id).eq("id", act_id).limit(1).execute().data) or []
        if cur and cur[0].get("status") != "cancelled":
            _require_pos_perm(authorization, org_id, "pos_activations_cancel")
    upd["updated_at"] = "now()"
    r = (sb().schema("pos").table("activations").update(upd)
         .eq("org_id", org_id).eq("id", act_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"activation": r.data[0]}


@router.get("/activations/{act_id}/notes")
def list_activation_notes(act_id: str, org_id: str = ORG_ID):
    rows = (sb().schema("pos").table("activation_notes").select("*")
            .eq("org_id", org_id).eq("activation_id", act_id)
            .order("created_at", desc=True).limit(200).execute().data) or []
    return {"notes": rows}


@router.post("/activations/{act_id}/notes")
def add_activation_note(act_id: str, body: dict, authorization: str = Header(default=""),
                        org_id: str = ORG_ID):
    note = (body.get("note") or "").strip()
    if not note:
        raise HTTPException(400, "note text required")
    severity = body.get("severity") or "normal"
    if severity not in ("normal", "important", "urgent"):
        severity = "normal"
    r = sb().schema("pos").table("activation_notes").insert({
        "org_id": org_id, "activation_id": act_id, "note": note, "severity": severity,
        "employee_id": _caller_employee(authorization, org_id) or None,
    }).execute()
    return {"note": (r.data or [{}])[0]}


# One trade-in per activation, upserted; credit removal zeroes the amount, never deletes
# (parity with the standalone app).
@router.put("/activations/{act_id}/trade-in")
def upsert_trade_in(act_id: str, body: dict, org_id: str = ORG_ID,
                    authorization: str = Header(default="")):
    _require_member(authorization, org_id)
    TI_FIELDS = ("device_description", "serial_number", "imei", "notes", "customer_id", "sale_id")
    existing = (sb().schema("pos").table("trade_ins").select("id")
                .eq("org_id", org_id).eq("activation_id", act_id).limit(1).execute().data) or []
    if existing:
        # PATCH semantics: write only the keys the caller actually sent. Building the update from
        # a fixed field list meant a one-field request NULLed serial_number, imei and notes and
        # zeroed credit_amount — overwrite-by-omission, the same bug class as customer_pii_set.
        upd = {k: (body.get(k) or None) for k in TI_FIELDS if k in body}
        if "credit_amount" in body:
            upd["credit_amount"] = float(body.get("credit_amount") or 0)
        if not upd:
            raise HTTPException(400, "nothing to update")
        r = (sb().schema("pos").table("trade_ins").update(upd)
             .eq("org_id", org_id).eq("id", existing[0]["id"]).execute())
        return {"trade_in": (r.data or [{}])[0]}
    payload = {k: body.get(k) or None for k in TI_FIELDS}
    payload["credit_amount"] = float(body.get("credit_amount") or 0)
    if not (payload.get("device_description") or "").strip():
        raise HTTPException(400, "device_description required")
    payload.update({"org_id": org_id, "activation_id": act_id})
    r = sb().schema("pos").table("trade_ins").insert(payload).execute()
    return {"trade_in": (r.data or [{}])[0]}


@router.patch("/trade-ins/{ti_id}")
def update_trade_in_status(ti_id: str, body: dict, authorization: str = Header(default=""),
                           org_id: str = ORG_ID):
    _require_any_pos_perm(authorization, org_id, ("pos_settings", "pos_inventory_adjust"))
    status = body.get("status")
    if status not in ("received", "sent_back", "written_off"):
        raise HTTPException(400, "invalid status")
    upd = {"status": status}
    if status == "sent_back":
        upd["sent_back_at"] = "now()"
    r = (sb().schema("pos").table("trade_ins").update(upd)
         .eq("org_id", org_id).eq("id", ti_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"trade_in": r.data[0]}


# ── Catalogs: service plans, dealer codes, carrier portals ─────────────────────────────────────────
@router.get("/service-plans")
def list_service_plans(include_inactive: bool = False, org_id: str = ORG_ID):
    q = sb().schema("pos").table("service_plans").select("*").eq("org_id", org_id)
    if not include_inactive:   # the activation dropdown wants active-or-null only
        q = q.or_("status.eq.active,status.is.null")
    rows = q.order("carrier").order("plan_name").limit(500).execute().data or []
    return {"service_plans": rows}


@router.post("/service-plans")
def create_service_plan(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    ins = {k: body[k] for k in SERVICE_PLAN_FIELDS if k in body}
    if not (ins.get("carrier") or "").strip() or not (ins.get("plan_name") or "").strip():
        raise HTTPException(400, "carrier and plan_name required")
    ins["org_id"] = org_id
    r = sb().schema("pos").table("service_plans").insert(ins).execute()
    return {"service_plan": (r.data or [{}])[0]}


@router.patch("/service-plans/{plan_id}")
def update_service_plan(plan_id: str, body: dict, authorization: str = Header(default=""),
                        org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    upd = {k: body[k] for k in SERVICE_PLAN_FIELDS if k in body}
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (sb().schema("pos").table("service_plans").update(upd)
         .eq("org_id", org_id).eq("id", plan_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"service_plan": r.data[0]}


@router.get("/dealer-codes")
def list_dealer_codes(org_id: str = ORG_ID):
    rows = (sb().schema("pos").table("dealer_codes").select("*").eq("org_id", org_id)
            .order("code").limit(200).execute().data) or []
    return {"dealer_codes": rows}


@router.get("/dealer-codes/sync-preview")
def dealer_codes_sync_preview(org_id: str = ORG_ID):
    """What a sync WOULD import, without writing. Same resolution as the sync below."""
    return _dealer_sync(org_id, commit=False)


@router.post("/dealer-codes/sync-from-reports")
def dealer_codes_sync(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Harvest dealer codes out of the carrier's own report data (owner directive 2026-08-09).

    The dealer code is a PER-CARRIER concept with a per-carrier name — "Salesforce ID" for Boost,
    "Account ID" for Total — so which table/column to read is config on commcalc.carrier (mig 293),
    never a branch here. A carrier nobody has mapped yet is reported as unconfigured and skipped
    rather than guessed at: seeding a tenant's POS with the wrong identifier is worse than seeding
    nothing.

    ADDITIVE. Existing rows are left exactly as they are (an operator may have corrected a
    store_code or deactivated a code by hand); only codes not already present are inserted."""
    _require_pos_perm(authorization, org_id, "pos_settings")
    return _dealer_sync(org_id, commit=True)


def _dealer_sync(org_id: str, commit: bool):
    client = sb()
    try:
        carriers = (client.schema("commcalc").table("carrier")
                    .select("id,name,dealer_code_label,dealer_code_source_table,"
                            "dealer_code_source_column,dealer_code_name_column")
                    .eq("org_id", org_id).execute().data) or []
    except Exception as e:
        raise HTTPException(400, f"carrier mapping unavailable (run migration 293?): {e}")
    existing = {(r.get("code") or "").strip().upper()
                for r in ((client.schema("pos").table("dealer_codes").select("code")
                           .eq("org_id", org_id).limit(5000).execute().data) or [])}
    out, inserted_total = [], 0
    for c in carriers:
        tbl, col = (c.get("dealer_code_source_table") or ""), (c.get("dealer_code_source_column") or "")
        namecol = (c.get("dealer_code_name_column") or "").strip()
        if not tbl or not col:
            out.append({"carrier": c.get("name"), "configured": False,
                        "hint": "Set this carrier's dealer-code source on the Carriers page."})
            continue
        sel = col + (("," + namecol) if namecol else "")
        try:
            rows = (client.schema("commcalc").table(tbl).select(sel)
                    .eq("org_id", org_id).limit(50000).execute().data) or []
        except Exception as e:
            out.append({"carrier": c.get("name"), "configured": True, "error": str(e)[:160]})
            continue
        seen = {}
        for r in rows:
            code = str(r.get(col) or "").strip()
            if not code or code.lower() in ("nan", "none", "null"):
                continue
            seen.setdefault(code.upper(), (code, str(r.get(namecol) or "").strip() if namecol else ""))
        fresh = [v for k, v in seen.items() if k not in existing]
        if commit and fresh:
            # `description` (mig 742) carries the carrier's OWN name for the code. Before 742 it was
            # read for the preview and then dropped on insert, which left the operator with a list of
            # bare six-digit numbers they could not tell apart -- and telling them apart is the entire
            # point of importing them. Degrades: a tenant whose 742 has not run gets the pre-742
            # payload rather than a failed import.
            payload = [{"org_id": org_id, "code": code, "carrier": c.get("name"),
                        "store_code": None, "is_active": True,
                        "description": (nm or None)} for code, nm in fresh]
            for i in range(0, len(payload), 500):
                chunk = payload[i:i + 500]
                try:
                    client.schema("pos").table("dealer_codes").insert(chunk).execute()
                except Exception:
                    client.schema("pos").table("dealer_codes").insert(
                        [{k: v for k, v in row.items() if k != "description"} for row in chunk]).execute()
            existing |= {code.upper() for code, _ in fresh}
            inserted_total += len(fresh)
        out.append({"carrier": c.get("name"), "configured": True,
                    "label": c.get("dealer_code_label"), "source": f"{tbl}.{col}",
                    "name_source": (f"{tbl}.{namecol}" if namecol else None),
                    "found": len(seen), "new": len(fresh),
                    "sample": [{"code": code, "description": nm or None} for code, nm in fresh[:8]]})
    return {"carriers": out, "inserted": inserted_total if commit else 0, "committed": commit}


@router.post("/dealer-codes")
def create_dealer_code(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    code = (body.get("code") or "").strip()
    if not code:
        raise HTTPException(400, "code required")
    row = {
        "org_id": org_id, "code": code, "carrier": (body.get("carrier") or "").strip() or None,
        "store_code": (body.get("store_code") or "").strip() or None,
        "is_active": bool(body.get("is_active", True)),
        "description": (str(body.get("description") or "").strip() or None),
    }
    try:
        r = sb().schema("pos").table("dealer_codes").insert(row).execute()
    except Exception:
        # mig 742 un-run -> save the code without its label rather than refusing the whole write.
        r = sb().schema("pos").table("dealer_codes").insert(
            {k: v for k, v in row.items() if k != "description"}).execute()
    return {"dealer_code": (r.data or [{}])[0]}


@router.patch("/dealer-codes/{code_id}")
def update_dealer_code(code_id: str, body: dict, authorization: str = Header(default=""),
                       org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    upd = {k: body[k] for k in ("code", "carrier", "store_code", "description", "is_active")
           if k in body}
    _clean_nullable(upd, ("carrier", "store_code", "description"))
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (sb().schema("pos").table("dealer_codes").update(upd)
         .eq("org_id", org_id).eq("id", code_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"dealer_code": r.data[0]}


@router.get("/carrier-portals")
def list_carrier_portals(org_id: str = ORG_ID):
    rows = (sb().schema("pos").table("carrier_portals").select("*").eq("org_id", org_id)
            .order("sort_order").order("carrier").limit(100).execute().data) or []
    return {"carrier_portals": rows}


@router.post("/carrier-portals")
def create_carrier_portal(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    carrier = (body.get("carrier") or "").strip()
    url = (body.get("url") or "").strip()
    if not carrier or not url:
        raise HTTPException(400, "carrier and url required")
    r = sb().schema("pos").table("carrier_portals").insert({
        "org_id": org_id, "carrier": carrier, "url": url,
        "sort_order": int(body.get("sort_order") or 0),
        "is_active": bool(body.get("is_active", True)),
    }).execute()
    return {"carrier_portal": (r.data or [{}])[0]}


@router.patch("/carrier-portals/{portal_id}")
def update_carrier_portal(portal_id: str, body: dict, authorization: str = Header(default=""),
                          org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    upd = {k: body[k] for k in ("carrier", "url", "is_active", "sort_order") if k in body}
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (sb().schema("pos").table("carrier_portals").update(upd)
         .eq("org_id", org_id).eq("id", portal_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"carrier_portal": r.data[0]}


@router.delete("/carrier-portals/{portal_id}")
def delete_carrier_portal(portal_id: str, authorization: str = Header(default=""),
                          org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    sb().schema("pos").table("carrier_portals").delete() \
        .eq("org_id", org_id).eq("id", portal_id).execute()
    return {"ok": True}


# ── Vendors ────────────────────────────────────────────────────────────────────────────────────────
@router.get("/vendors")
def list_vendors(search: str = "", search_by: str = "legal_name", business_type: str = "",
                 active_only: bool = True, org_id: str = ORG_ID):
    q = sb().schema("pos").table("vendors").select("*").eq("org_id", org_id)
    if active_only:
        q = q.eq("is_active", True)
    if business_type:
        q = q.eq("business_type", business_type)
    s = search.strip().replace("%", "").replace(",", " ")
    if s:
        if search_by == "legal_name":
            q = q.or_(f"legal_name.ilike.%{s}%,short_name.ilike.%{s}%")
        elif search_by in ("contact_name", "phone", "email"):
            q = q.ilike(search_by, f"%{s}%")
    rows = q.order("legal_name").limit(300).execute().data or []
    return {"vendors": rows}


@router.post("/vendors")
def create_vendor(body: dict, org_id: str = ORG_ID):
    ins = {k: body[k] for k in VENDOR_FIELDS if k in body}
    if not (ins.get("legal_name") or "").strip():
        raise HTTPException(400, "legal_name required")
    ins["org_id"] = org_id
    r = sb().schema("pos").table("vendors").insert(ins).execute()
    return {"vendor": (r.data or [{}])[0]}


@router.patch("/vendors/{vendor_id}")
def update_vendor(vendor_id: str, body: dict, org_id: str = ORG_ID):
    upd = {k: body[k] for k in VENDOR_FIELDS if k in body}
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (sb().schema("pos").table("vendors").update(upd)
         .eq("org_id", org_id).eq("id", vendor_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"vendor": r.data[0]}


# ── Purchase orders (header-only, matching the standalone UI) ──────────────────────────────────────
@router.get("/purchase-orders")
def list_purchase_orders(org_id: str = ORG_ID):
    rows = (sb().schema("pos").table("purchase_orders").select("*").eq("org_id", org_id)
            .order("created_at", desc=True).limit(100).execute().data) or []
    vendor_ids = sorted({r["vendor_id"] for r in rows if r.get("vendor_id")})
    names = {}
    if vendor_ids:
        for v in (sb().schema("pos").table("vendors").select("id,legal_name")
                  .eq("org_id", org_id).in_("id", vendor_ids).execute().data or []):
            names[v["id"]] = v.get("legal_name")
    for r in rows:
        r["vendor_name"] = names.get(r.get("vendor_id"))
    return {"purchase_orders": rows}


@router.post("/purchase-orders")
def create_purchase_order(body: dict, authorization: str = Header(default=""),
                          org_id: str = ORG_ID):
    if not body.get("vendor_id"):
        raise HTTPException(400, "vendor_id required")
    import time as _time
    ins = {
        "org_id": org_id,
        "po_number": (body.get("po_number") or "").strip() or f"PO-{int(_time.time() * 1000)}",
        "vendor_id": body["vendor_id"],
        "store_code": (body.get("store_code") or "").strip() or None,
        "created_by": _caller_employee(authorization, org_id) or None,
        "status": "draft",
        "order_date": body.get("order_date") or None,
        "expected_date": body.get("expected_date") or None,
        "notes": (body.get("notes") or "").strip() or None,
    }
    r = sb().schema("pos").table("purchase_orders").insert(ins).execute()
    return {"purchase_order": (r.data or [{}])[0]}


# ── Store transfers ────────────────────────────────────────────────────────────────────────────────
@router.get("/transfers")
def list_transfers(authorization: str = Header(default=""), org_id: str = ORG_ID):
    rows = (sb().schema("pos").table("store_transfers").select("*").eq("org_id", org_id)
            .order("created_at", desc=True).limit(200).execute().data) or []
    ks = _caller_store_keyset(authorization, org_id)
    if ks is not None:
        # a transfer is visible when EITHER endpoint store is in the caller's span
        from app.core.scope import in_keyset
        rows = [r for r in rows
                if in_keyset(ks, r.get("from_store_code")) or in_keyset(ks, r.get("to_store_code"))]
    items = (sb().schema("pos").table("store_transfer_items").select("transfer_id")
             .eq("org_id", org_id).limit(5000).execute().data) or []
    counts = {}
    for it in items:
        counts[it["transfer_id"]] = counts.get(it["transfer_id"], 0) + 1
    for r in rows:
        r["item_count"] = counts.get(r["id"], 0)
    return {"transfers": rows}


@router.get("/transfers/{transfer_id}")
def get_transfer(transfer_id: str, org_id: str = ORG_ID):
    rows = (sb().schema("pos").table("store_transfers").select("*")
            .eq("org_id", org_id).eq("id", transfer_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "not found")
    t = rows[0]
    items = (sb().schema("pos").table("store_transfer_items").select("*")
             .eq("transfer_id", transfer_id).order("created_at").execute().data) or []
    prods = {p["id"]: p for p in (sb().schema("pos").table("products")
             .select("id,short_name,product_code").eq("org_id", org_id)
             .limit(2000).execute().data or [])}
    for it in items:
        p = prods.get(it.get("product_id")) or {}
        it["product_name"] = p.get("short_name")
        it["product_code"] = p.get("product_code")
    t["items"] = items
    return {"transfer": t}


@router.post("/transfers")
def create_transfer(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    items = body.get("items") or []
    if not items:
        raise HTTPException(400, "a transfer needs at least one item")
    import secrets as _secrets
    from datetime import datetime as _dt, timezone as _tz
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford-ish, same idea as the old app
    number = ("ST-" + _dt.now(_tz.utc).strftime("%y%m%d") + "-"
              + "".join(_secrets.choice(alphabet) for _ in range(4)))
    header = {
        "transfer_number": (body.get("transfer_number") or "").strip() or number,
        "from_store_code": body.get("from_store_code"),
        "to_store_code": body.get("to_store_code"),
        "created_by": _caller_employee(authorization, org_id) or None,
        "notes": body.get("notes") or "",
    }
    try:
        r = sb().schema("pos").rpc("create_transfer", {
            "p_org": org_id, "p_header": header, "p_items": items,
        }).execute()
    except Exception as e:
        raise HTTPException(400, f"create failed: {e}")
    return {"transfer": r.data}


@router.post("/transfers/{transfer_id}/ship")
def ship_transfer(transfer_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_inventory_adjust")
    try:
        r = sb().schema("pos").rpc("ship_transfer",
                                   {"p_org": org_id, "p_transfer": transfer_id}).execute()
    except Exception as e:
        raise HTTPException(400, f"ship failed: {e}")
    return {"transfer": r.data}


@router.post("/transfers/{transfer_id}/receive")
def receive_transfer(transfer_id: str, authorization: str = Header(default=""),
                     org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_inventory_receive")
    try:
        r = sb().schema("pos").rpc("receive_transfer",
                                   {"p_org": org_id, "p_transfer": transfer_id}).execute()
    except Exception as e:
        raise HTTPException(400, f"receive failed: {e}")
    return {"transfer": r.data}


@router.post("/transfers/{transfer_id}/cancel")
def cancel_transfer(transfer_id: str, authorization: str = Header(default=""),
                    org_id: str = ORG_ID):
    """Guarded status flip — no inventory moved while pending, so nothing to restore."""
    _require_pos_perm(authorization, org_id, "pos_inventory_adjust")
    r = (sb().schema("pos").table("store_transfers").update({"status": "cancelled"})
         .eq("org_id", org_id).eq("id", transfer_id).eq("status", "pending").execute())
    if not r.data:
        raise HTTPException(409, "only pending transfers can be cancelled "
                                 "(it may have just been shipped)")
    return {"transfer": r.data[0]}


# ── Reports ────────────────────────────────────────────────────────────────────────────────────────
@router.get("/reports/kpis")
def report_kpis(org_id: str = ORG_ID, authorization: str = Header(default="")):
    # Store-scoped. Without this a single-store manager was shown ORG-WIDE revenue for today,
    # the week and the month, plus the org's whole in-stock unit count. Customer and product
    # counts stay org-wide deliberately: both catalogs are org-level, with no store dimension
    # to filter on.
    ks = _caller_store_keyset(authorization, org_id)
    # Day/month boundaries in the TENANT's business zone (migration 085; house default Eastern) like
    # the commcalc feed — a 9pm-local sale belongs to today's KPI, not tomorrow's UTC date, and a
    # Central-time tenant is bucketed in Central, not Eastern (owner report 2026-08-15).
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    from app.modules.pos.commcalc_feed import business_tz
    now_local = _dt.now(business_tz(org_id))
    today = now_local.replace(hour=0, minute=0, second=0, microsecond=0) \
        .astimezone(_tz.utc).isoformat()
    week = (_dt.now(_tz.utc) - _td(days=7)).isoformat()
    month = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0) \
        .astimezone(_tz.utc).isoformat()
    out = {}
    for key, since in (("today", today), ("week", week), ("month", month)):
        rows = (sb().schema("pos").table("sales").select("total,store_code")
                .eq("org_id", org_id).eq("status", "completed")
                .gte("created_at", since).limit(5000).execute().data) or []
        rows = _span_filter(rows, ks)
        out[key] = {"count": len(rows), "total": sum(float(r.get("total") or 0) for r in rows)}
    out["customers"] = len((sb().schema("pos").table("customers").select("id")
                            .eq("org_id", org_id).eq("is_active", True)
                            .limit(10000).execute().data) or [])
    out["products"] = len((sb().schema("pos").table("products").select("id")
                           .eq("org_id", org_id).eq("is_active", True)
                           .limit(10000).execute().data) or [])
    out["in_stock_units"] = len(_span_filter(
        (sb().schema("pos").table("inventory_serial").select("id,store_code")
         .eq("org_id", org_id).eq("status", "in_stock")
         .limit(10000).execute().data) or [], ks))
    return out


@router.get("/reports/sales")
def report_sales(date_from: str = "", date_to: str = "", store_code: str = "",
                 employee_id: str = "", kind: str = "daily",
                 authorization: str = Header(default=""), org_id: str = ORG_ID):
    q = (sb().schema("pos").table("sales")
         .select("id,transaction_id,created_at,total,discount_total,receipt_type,status,"
                 "voided_at,customer_id,employee_id,store_code")
         .eq("org_id", org_id))
    if date_from:
        q = q.gte("created_at", _date_bound(date_from, end=False))
    if date_to:
        q = q.lte("created_at", _date_bound(date_to, end=True))
    if store_code:
        q = q.eq("store_code", store_code)
    if employee_id:
        q = q.eq("employee_id", employee_id)
    if kind == "voids":
        q = q.or_("status.eq.voided,voided_at.not.is.null")
    elif kind == "discounts":
        q = q.gt("discount_total", 0)
    rows = q.order("created_at", desc=True).limit(500).execute().data or []
    rows = _span_filter(rows, _caller_store_keyset(authorization, org_id))
    cust = _customer_names(org_id, [r.get("customer_id") for r in rows])
    emp = _employee_names(org_id)
    for r in rows:
        r["customer_name"] = (cust.get(r.get("customer_id")) or {}).get("name")
        r["employee_name"] = emp.get((r.get("employee_id") or "").strip())
    return {"rows": rows}


@router.get("/reports/activations")
def report_activations(date_from: str = "", date_to: str = "", store_code: str = "",
                       employee_id: str = "", authorization: str = Header(default=""),
                       org_id: str = ORG_ID):
    q = (sb().schema("pos").table("activations")
         .select("id,activation_number,carrier,activation_date,monthly_fee,cell_number,"
                 "mobile_phone,status,customer_id,employee_id,store_code")
         .eq("org_id", org_id))
    if date_from:
        q = q.gte("activation_date", date_from)
    if date_to:
        q = q.lte("activation_date", date_to)
    if store_code:
        q = q.eq("store_code", store_code)
    if employee_id:
        q = q.eq("employee_id", employee_id)
    rows = q.order("activation_date", desc=True).limit(500).execute().data or []
    rows = _span_filter(rows, _caller_store_keyset(authorization, org_id))
    cust = _customer_names(org_id, [r.get("customer_id") for r in rows])
    emp = _employee_names(org_id)
    for r in rows:
        r["customer_name"] = (cust.get(r.get("customer_id")) or {}).get("name")
        r["employee_name"] = emp.get((r.get("employee_id") or "").strip())
    return {"rows": rows}


@router.get("/reports/trade-ins")
def report_trade_ins(date_from: str = "", date_to: str = "", employee_id: str = "",
                     status: str = "", org_id: str = ORG_ID,
                     authorization: str = Header(default="")):
    ks = _caller_store_keyset(authorization, org_id)
    q = sb().schema("pos").table("trade_ins").select("*").eq("org_id", org_id)
    if date_from:
        q = q.gte("received_at", date_from)
    if date_to:
        q = q.lte("received_at", date_to + "T23:59:59")
    if status:
        q = q.eq("status", status)
    rows = q.order("received_at", desc=True).limit(500).execute().data or []
    act_ids = sorted({r["activation_id"] for r in rows if r.get("activation_id")})
    acts = {}
    if act_ids:
        for a in (sb().schema("pos").table("activations")
                  .select("id,activation_number,employee_id,store_code")
                  .eq("org_id", org_id).in_("id", act_ids).execute().data or []):
            acts[a["id"]] = a
    # trade_ins carries no store of its own, so the span is resolved through the parent
    # activation. Without this, every trade-in org-wide — device, serial, IMEI, credit amount and
    # customer name — was returned to a single-store manager.
    if ks is not None:
        from app.core.scope import in_keyset
        rows = [r for r in rows
                if in_keyset(ks, (acts.get(r.get("activation_id")) or {}).get("store_code"))]
    if employee_id:
        rows = [r for r in rows
                if (acts.get(r.get("activation_id")) or {}).get("employee_id") == employee_id]
    cust = _customer_names(org_id, [r.get("customer_id") for r in rows])
    for r in rows:
        a = acts.get(r.get("activation_id")) or {}
        r["activation_number"] = a.get("activation_number")
        r["customer_name"] = (cust.get(r.get("customer_id")) or {}).get("name")
    return {"rows": rows}


# ── CSV import (server side: dedupe + batched insert with per-row error attribution) ──────────────
# The page does parsing/coercion/column mapping; rows arrive here as ready-to-insert dicts.
# Duplicates (against the DB and within the batch) are SKIPPED, never overwritten — parity with
# the standalone importer. Stores/locations are NOT importable (storeops owns stores).

def _digits(s) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _fetch_all(table: str, cols: str, org_id: str):
    out, page = [], 0
    while True:
        rows = (sb().schema("pos").table(table).select(cols).eq("org_id", org_id)
                .range(page * 1000, page * 1000 + 999).execute().data) or []
        out.extend(rows)
        if len(rows) < 1000:
            return out
        page += 1


def _batch_insert(table: str, rows: list, org_id: str):
    """Insert in batches of 100; on batch failure retry row-by-row so errors attribute to
    exact rows. Returns (inserted_count, errors=[{index, message}]). `rows` items are
    (original_index, payload) pairs."""
    inserted, errors = 0, []
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        payloads = [{**p, "org_id": org_id} for _, p in chunk]
        try:
            sb().schema("pos").table(table).insert(payloads).execute()
            inserted += len(chunk)
        except Exception:
            for idx, p in chunk:
                try:
                    sb().schema("pos").table(table).insert({**p, "org_id": org_id}).execute()
                    inserted += 1
                except Exception as e:
                    errors.append({"index": idx, "message": str(e)[:300]})
    return inserted, errors


@router.post("/import/{entity}")
def import_rows(entity: str, body: dict, org_id: str = ORG_ID):
    rows = body.get("rows") or []
    if not isinstance(rows, list) or not rows:
        raise HTTPException(400, "rows required")
    if len(rows) > 5000:
        raise HTTPException(400, "max 5000 rows per import request")
    skipped, to_insert = [], []

    if entity == "customers":
        existing = _fetch_all("customers", "phone_primary,email", org_id)
        seen = {k for r in existing for k in
                (_digits(r.get("phone_primary")), (r.get("email") or "").strip().lower()) if k}
        for i, row in enumerate(rows):
            p = _clean_customer(row)
            key = _digits(p.get("phone_primary")) or (p.get("email") or "").strip().lower()
            if key and key in seen:
                skipped.append({"index": i, "message": "duplicate (phone/email already exists)"})
                continue
            if key:
                seen.add(key)
            to_insert.append((i, p))
        inserted, errors = _batch_insert("customers", to_insert, org_id)

    elif entity == "vendors":
        existing = _fetch_all("vendors", "legal_name", org_id)
        seen = {(r.get("legal_name") or "").strip().lower() for r in existing}
        for i, row in enumerate(rows):
            p = {k: row[k] for k in VENDOR_FIELDS if k in row}
            key = (p.get("legal_name") or "").strip().lower()
            if not key:
                skipped.append({"index": i, "message": "legal_name required"})
                continue
            if key in seen:
                skipped.append({"index": i, "message": "duplicate (legal_name already exists)"})
                continue
            seen.add(key)
            to_insert.append((i, p))
        inserted, errors = _batch_insert("vendors", to_insert, org_id)

    elif entity == "products":
        existing = _fetch_all("products", "upc", org_id)
        seen = {(r.get("upc") or "").strip() for r in existing if (r.get("upc") or "").strip()}
        cat = catalog(org_id)
        dmap = {d["short_name"].strip().lower(): d["id"] for d in cat["departments"]}
        cmap = {c["name"].strip().lower(): c["id"] for c in cat["categories"]}
        for i, row in enumerate(rows):
            p = _clean(row)
            dname = (row.get("department") or "").strip()
            cname = (row.get("category") or "").strip()
            if dname:
                did = dmap.get(dname.lower())
                if not did:
                    d = sb().schema("pos").table("departments").insert(
                        {"org_id": org_id, "short_name": dname, "full_name": dname}
                    ).execute().data
                    did = d[0]["id"] if d else None
                    if did:
                        dmap[dname.lower()] = did
                p["department_id"] = did
            if cname:
                cid = cmap.get(cname.lower())
                if not cid:
                    c = sb().schema("pos").table("categories").insert(
                        {"org_id": org_id, "name": cname,
                         "department_id": p.get("department_id")}
                    ).execute().data
                    cid = c[0]["id"] if c else None
                    if cid:
                        cmap[cname.lower()] = cid
                p["category_id"] = cid
            upc = (p.get("upc") or "").strip()
            if upc and upc in seen:
                skipped.append({"index": i, "message": "duplicate (UPC already exists)"})
                continue
            if upc:
                seen.add(upc)
            to_insert.append((i, p))
        inserted, errors = _batch_insert("products", to_insert, org_id)

    elif entity == "inventory":
        prods = _fetch_all("products", "id,upc,short_name", org_id)
        by_upc = {(p.get("upc") or "").strip(): p["id"] for p in prods if (p.get("upc") or "").strip()}
        by_name = {(p.get("short_name") or "").strip().lower(): p["id"] for p in prods}
        existing = _fetch_all("inventory_standard", "product_id,store_code", org_id)
        seen = {f"{r['product_id']}|{r['store_code']}" for r in existing}
        for i, row in enumerate(rows):
            pid = by_upc.get((row.get("upc") or "").strip()) \
                or by_name.get((row.get("product_name") or "").strip().lower())
            if not pid:
                skipped.append({"index": i, "message": "product not found (by UPC or name)"})
                continue
            store = (row.get("store_code") or "").strip()
            if not store:
                skipped.append({"index": i, "message": "store_code required"})
                continue
            key = f"{pid}|{store}"
            if key in seen:
                skipped.append({"index": i, "message": "duplicate (product already counted at store)"})
                continue
            seen.add(key)
            p = {"product_id": pid, "store_code": store,
                 "qty_on_hand": int(row.get("qty_on_hand") or 0),
                 "qty_on_order": int(row.get("qty_on_order") or 0),
                 "qty_reserved": int(row.get("qty_reserved") or 0),
                 "bin_location": (row.get("bin_location") or "").strip() or None}
            to_insert.append((i, p))
        inserted, errors = _batch_insert("inventory_standard", to_insert, org_id)

    elif entity == "activations":
        existing = _fetch_all("activations", "cell_number,activation_date", org_id)
        seen = {f"{_digits(r.get('cell_number'))}|{r.get('activation_date') or ''}"
                for r in existing if _digits(r.get("cell_number"))}
        custs = _fetch_all("customers",
                           "id,phone_primary,email,first_name,last_name,company_name", org_id)
        by_phone, by_email, by_name = {}, {}, {}
        for c in custs:
            ph = _digits(c.get("phone_primary"))
            if ph:
                by_phone.setdefault(ph, []).append(c["id"])
            em = (c.get("email") or "").strip().lower()
            if em:
                by_email.setdefault(em, []).append(c["id"])
            nm = (f"{c.get('first_name') or ''} {c.get('last_name') or ''}".strip()
                  or (c.get("company_name") or "").strip()).lower()
            if nm:
                by_name.setdefault(nm, []).append(c["id"])
        for i, row in enumerate(rows):
            try:
                p = _clean_activation({k: v for k, v in row.items() if k in ACTIVATION_FIELDS})
            except HTTPException as e:
                # one bad row must not abort the whole import — per-row attribution contract
                skipped.append({"index": i, "message": str(e.detail)})
                continue
            if row.get("notes"):
                p["notes"] = row["notes"]
            key = f"{_digits(p.get('cell_number'))}|{p.get('activation_date') or ''}"
            if _digits(p.get("cell_number")) and key in seen:
                skipped.append({"index": i, "message": "duplicate (cell number + date)"})
                continue
            matches = (by_phone.get(_digits(row.get("customer_phone"))) if row.get("customer_phone") else None) \
                or (by_email.get((row.get("customer_email") or "").strip().lower()) if row.get("customer_email") else None) \
                or (by_name.get((row.get("customer_name") or "").strip().lower()) if row.get("customer_name") else None)
            if row.get("customer_phone") or row.get("customer_email") or row.get("customer_name"):
                if not matches:
                    skipped.append({"index": i, "message": "customer not found"})
                    continue
                if len(matches) > 1:
                    skipped.append({"index": i, "message": "ambiguous customer match"})
                    continue
                p["customer_id"] = matches[0]
            if _digits(p.get("cell_number")):
                seen.add(key)
            to_insert.append((i, p))
        inserted, errors = _batch_insert("activations", to_insert, org_id)

    elif entity == "tax_codes":
        # A downloadable template with no importer is a round trip that only looks complete: the
        # tenant fills it in, uploads, and gets "unknown entity". rate is a PERCENT (8.875), and a
        # blank store_code is the ORG-WIDE rate, matching the template's own note.
        existing = _fetch_all("tax_codes", "name,store_code", org_id)
        seen = {((r.get("name") or "").strip().lower(), (r.get("store_code") or "").strip())
                for r in existing}
        for i, row in enumerate(rows):
            name = str(row.get("name") or "").strip()
            store = str(row.get("store_code") or "").strip() or None
            if not name:
                skipped.append({"index": i, "message": "name required"})
                continue
            try:
                rate = float(row.get("rate"))
            except (TypeError, ValueError):
                skipped.append({"index": i, "message": "rate must be a number (percent, e.g. 8.875)"})
                continue
            if not (0 <= rate <= 30):
                skipped.append({"index": i, "message": "rate must be 0-30 (percent)"})
                continue
            key = (name.lower(), store or "")
            if key in seen:
                skipped.append({"index": i, "message": "duplicate (same name + store already exists)"})
                continue
            seen.add(key)
            to_insert.append((i, {"name": name, "rate": rate, "store_code": store,
                                  "is_active": row.get("is_active", True) is not False}))
        inserted, errors = _batch_insert("tax_codes", to_insert, org_id)

    else:
        raise HTTPException(400, f"unknown entity '{entity}' "
                                 "(importable: customers, products, vendors, inventory, "
                                 "activations, tax_codes)")

    return {"inserted": inserted, "skipped": skipped, "errors": errors,
            "total": len(rows)}


# ── CommCalc feed: built-in POS stream + tenant POS-source setup (mig 727) ────────────────────────
# Design: SAAS_FRAMEWORK.md §8 (streams never merge). The module writes its own
# commcalc.pos_builtin_* tables; promotion into daily_sales_feed/raw_sales happens ONLY for
# tenants whose core.tenant_pos_setup.builtin_role = 'primary'.
from app.modules.pos import commcalc_feed as _feed


@router.get("/commcalc/setup")
def get_commcalc_setup(org_id: str = ORG_ID):
    return {"setup": _feed.get_pos_setup(org_id)}


@router.put("/commcalc/setup")
def put_commcalc_setup(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    upd = {k: body[k] for k in ("builtin_role", "external_role", "secondary_mode",
                                "separate_registers", "notes") if k in body}
    if not upd:
        raise HTTPException(400, "nothing to update")
    if upd.get("builtin_role") == "primary" and upd.get("external_role") == "primary":
        raise HTTPException(400, "only one POS can be primary")
    upd["updated_at"] = "now()"
    r = (sb().schema("core").table("tenant_pos_setup").update(upd)
         .eq("org_id", org_id).execute())
    if not r.data:
        ins = {"org_id": org_id, **{k: v for k, v in upd.items() if k != "updated_at"}}
        r = sb().schema("core").table("tenant_pos_setup").insert(ins).execute()
    return {"setup": (r.data or [{}])[0]}


@router.post("/commcalc/sync")
def commcalc_sync(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    return _feed.sync_period(org_id, body.get("mode") or "daily", body.get("period"))


@router.get("/commcalc/status")
def commcalc_status(org_id: str = ORG_ID):
    return _feed.feed_status(org_id)
