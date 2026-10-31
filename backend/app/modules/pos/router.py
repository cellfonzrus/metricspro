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
import re as _re
import datetime as _dt
from datetime import datetime as _datetime, timezone as _timezone

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile

from app.core.config import settings
from app.core.database import get_supabase
from app.core import upload_format as _upload_format
from app.modules.pos import held_sale as _held
from app.modules.pos import stock_landing as _stock
from app.modules.pos import working_store as _wstore
from app.modules.pos import tax_precedence as _taxrule

ORG_ID = "00000000-0000-0000-0000-000000000001"


def _utcnow() -> str:
    """The server's own time, ISO-8601 UTC. The pure helpers take a clock as an argument so a
    harness can pin it; this is the one place the real clock is read."""
    return _datetime.now(_timezone.utc).isoformat()


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


# ── Catalog suggestions (setup wizard: "define what you have", smartly) ──────────────────────────────
# The Catalog steps used to dead-end for a fresh store: the only offer was "import from your sales
# history", which is empty for a store that never fed one. These two endpoints add the third path —
# pickable, pre-built catalog COMBINATIONS tailored by this store's own data and by the common
# taxonomy other stores use. See app/modules/pos/catalog_suggest.py for the privacy rules.
from app.modules.pos import catalog_suggest as _catsug


@router.get("/catalog/suggest")
def catalog_suggest(org_id: str = ORG_ID):
    """What this store already has + pickable presets + product candidates from its own devices +
    the department/category structure common to other stores in the system. Read-only."""
    return _catsug.build_suggestions(sb(), org_id)


@router.post("/catalog/apply-suggestion")
def catalog_apply_suggestion(body: dict, org_id: str = ORG_ID):
    """Create the picked departments / categories / system-categories, idempotently (existing names
    are skipped). Body: {departments:[{short_name,full_name?,system_category?}],
    categories:[{name,department?}], system_categories:[name,...]}."""
    return _catsug.apply_suggestion(
        sb(), org_id,
        departments=body.get("departments") or [],
        categories=body.get("categories") or [],
        system_categories=body.get("system_categories") or [])


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
# Filtering lives in ONE place — pos/product_filters.py — for the register's product picker and the
# catalog page alike (owner 2026-09-24). Every param is optional; with none of the new ones the query
# is the one this handler always built.
from app.modules.pos import product_filters as _pf


@router.get("/products")
def list_products(search: str = "", department_id: str = "", system_category: str = "",
                  active_only: bool = True, category_id: str = "", manufacturer: str = "",
                  inventory_type: str = "", in_stock: bool = False, store_code: str = "",
                  org_id: str = ORG_ID):
    """The org's products, newest first, capped at 500. Optional filters (blank = any):
    department_id, category_id, system_category, manufacturer (exact), inventory_type
    (standard / serial), in_stock (a serial unit `in_stock` or standard qty_on_hand > 0 — at
    `store_code` when given), combined with `search` (name / full name / UPC)."""
    if inventory_type and inventory_type not in _pf.INVENTORY_TYPES:
        raise HTTPException(400, f"inventory_type must be one of {', '.join(_pf.INVENTORY_TYPES)}")
    client = sb()
    rows = _pf.list_rows(
        client, org_id, search=search, active_only=active_only,
        filters=_pf.clean(department_id=department_id, category_id=category_id,
                          system_category=system_category, manufacturer=manufacturer,
                          inventory_type=inventory_type),
        in_stock=in_stock, store_code=store_code)
    # Resolve department/category names in Python (cheap: both lists are small) rather than
    # relying on PostgREST embeds from a non-default schema.
    cat = catalog(org_id)
    dname = {d["id"]: d["short_name"] for d in cat["departments"]}
    cname = {c["id"]: c["name"] for c in cat["categories"]}
    for r in rows:
        r["department_name"] = dname.get(r.get("department_id"))
        r["category_name"] = cname.get(r.get("category_id"))
    return {"products": rows}


@router.get("/products/manufacturers")
def list_product_manufacturers(active_only: bool = True, org_id: str = ORG_ID):
    """The Manufacturer filter's options: the org's distinct non-blank manufacturers, A→Z."""
    return {"manufacturers": _pf.manufacturers(sb(), org_id, active_only)}


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
    clause = _pf.search_clause(search)     # the one product search clause (pos/product_filters)
    if clause:
        q = q.or_(clause)
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
                           "font_size_px", "layout")
#: The template fields migration 1089 adds. A PATCH that sets one before the migration is applied is
#: reported as exactly that, never as a plain 400 — the admin screen then says what to run.
RECEIPT_TEMPLATE_MIG_1089 = ("layout",)


def _clean_customer(body: dict) -> dict:
    out = {k: body[k] for k in CUSTOMER_FIELDS if k in body}
    for k in ("dob", "company_name", "middle_initial",
              "primary_account_no", "password", "email", "phone_secondary",
              "address_2", "referral_source"):
        if k in out and out[k] == "":
            out[k] = None
    return out


# The carrier account PIN (`password`) is the credential behind SIM-swap / account-takeover — the
# single most damaging field to leak from the customer book. SSN/DL already ride the mig-725 pgcrypto
# vault; the PIN did not. Seal it as application-layer ciphertext ('enc:v1:') on every write and open
# it only on the single-record edit fetch. crypto.encrypt is idempotent and a no-op without a key, so
# this is safe to run before the backfill and on already-sealed values.
def seal_customer_pin(d: dict) -> dict:
    """THE one home for "what sealing a stored carrier PIN means" (index §CORE-04).

    PUBLIC, and not for tidiness: `core.encryption_backfill.backfill_customer_pins` calls it too, so
    the sweep that seals the OLD rows and the writers that seal the NEW ones cannot disagree about
    the primitive. The sweep used to call `crypto.encrypt` itself. That agreed with this function by
    luck — one text column, one primitive — and the identical half-dereference on the portal sweep
    did NOT agree, because a JSONB column needs a different primitive: three live portal sessions
    sat in plain text through every boot (found 2026-10-13). Reading a registry for WHICH columns
    while deciding HOW to seal them yourself is the second home, whether or not it has bitten yet.

    `crypto.encrypt` passes an already-sealed value through, so this is safe to call on a stored row.
    """
    if d.get("password"):
        from app.core import crypto
        d = dict(d)
        d["password"] = crypto.encrypt(d["password"])
    return d


def _open_customer_pin(row):
    if row and row.get("password"):
        from app.core import crypto
        row = dict(row)
        row["password"] = crypto.decrypt(row["password"])
    return row


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


def _caller_login_store(authorization: str, org_id: str) -> str:
    """The store this login is PINNED to, if any — a hint for the picker, never an answer.

    §POS-14: a pin is what somebody typed on a form once (the same point core.scope makes about
    visibility). It pre-selects the confirm dialog for a single-store cashier and is ignored when it
    falls outside the caller's span."""
    try:
        from app.modules.core.router import _uid_from_token
        uid = _uid_from_token(authorization)
        if not uid:
            return ""
        rows = (sb().schema("storeops").table("app_users").select("store_code")
                .eq("org_id", org_id).eq("auth_id", uid).limit(1).execute().data) or []
        return str((rows[0] or {}).get("store_code") or "").strip() if rows else ""
    except Exception:
        return ""


def _working_span(authorization: str, org_id: str) -> dict:
    """THE caller's ringable span, assembled for `pos/working_store` (index §POS-14).

    Active stores only — offering a closed store as the place a sale happened is never correct —
    intersected with the caller's reporting span. `_caller_store_keyset` FAILS CLOSED (403), so a
    scope hiccup cannot widen this to the whole tenant."""
    client = sb()
    try:
        # `store_code` and `address` ONLY — the two keys `scope_keyset` widens a grant to. This
        # deliberately does NOT ask for `market`: `working_store.span` has no use for it, and a
        # market is a VOCABULARY whose one resolver is `core.scope` (the §13c class). Reading a
        # vocabulary you do not need is how a second, unpinned answer to "which market is this
        # store in" gets built by accident. harness_market_resolution_guard.py enforces that.
        roster = (client.schema("storeops").table("stores")
                  .select("store_code,address,is_active")
                  .eq("org_id", org_id).order("store_code").limit(2000).execute().data) or []
    except Exception:
        roster = []
    roster = [r for r in roster if r.get("is_active") is not False]
    keyset = _caller_store_keyset(authorization, org_id)
    return _wstore.span(roster, keyset, login_store=_caller_login_store(authorization, org_id))


@router.get("/working-store")
def working_store(store_code: str = "", authorization: str = Header(default=""),
                  org_id: str = ORG_ID):
    """Which store may this caller ring a sale at, and is `store_code` a CONFIRMED choice?

    THE one home every POS screen asks instead of spelling its own ladder (index §POS-14). It
    answers the span, the login's pin, whether a confirmation is owed, and — for the store the
    terminal says was picked — the BASIS of it: `confirmed`, `sole_store`, or `unconfirmed`, which
    carries no store_code at all. There is no positional fallback: `stores[0]` is what four screens
    used to do, and it logged sales and printed receipts under a store nobody had chosen.

    It also carries the effective TAX for the resolved store, through the same one home the Settings
    grid reads (`tax_precedence`), so the register cannot answer that question itself either."""
    span_ = _working_span(authorization, org_id)
    verdict = _wstore.resolve(span_, store_code)
    tax = None
    if verdict["store_code"]:
        tax = resolve_tax_code(store_code=verdict["store_code"], org_id=org_id)
    return {"stores": span_["stores"], "login_store": span_["login_store"],
            "must_confirm": span_["must_confirm"], **verdict, "tax": tax}


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
    """The customer book — the POS sale screen's customer search and the Account Manager list. The text finds a
    customer by any one column (as before), by the FULL name ('Jane Doe' — first AND last; the single-column ILIKE
    found nobody), by a phone number on the customer's LINES, by an also-known-as name, or by the customer #
    (`customer_master.search_ids`, org-scoped). A customer merged into another is never listed (its sales and
    lines live on the survivor)."""
    _require_member(authorization, org_id)
    client = sb()
    ids = _cmaster.search_ids(client, org_id, search)
    hide_merged = _cmaster.identity_schema(client, org_id)["merged_into"]

    def base():
        q = client.schema("pos").table("customers").select(CUSTOMER_READ_COLS).eq("org_id", org_id)
        if active_only:
            q = q.eq("is_active", True)
        return q.is_("merged_into", "null") if hide_merged else q

    if ids is None:
        rows = base().order("created_at", desc=True).limit(300).execute().data or []
    else:
        rows = []
        for chunk in _cmaster.in_chunks(ids):
            rows += base().in_("id", chunk).execute().data or []
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return {"customers": rows[:300]}


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
    return {"customer": _open_customer_pin(rows[0])}


@router.post("/customers")
def create_customer(body: dict, org_id: str = ORG_ID):
    ins = _clean_customer(body)
    if not (ins.get("first_name") or ins.get("last_name") or ins.get("company_name")):
        raise HTTPException(400, "a name is required")
    ins["org_id"] = org_id
    r = sb().schema("pos").table("customers").insert(seal_customer_pin(ins)).execute()
    return {"customer": _open_customer_pin((r.data or [{}])[0])}


@router.patch("/customers/{customer_id}")
def update_customer(customer_id: str, body: dict, org_id: str = ORG_ID):
    upd = _clean_customer(body)
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (sb().schema("pos").table("customers").update(seal_customer_pin(upd))
         .eq("org_id", org_id).eq("id", customer_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"customer": _open_customer_pin(r.data[0])}


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


# ── The customer master (owner 2026-09-24; index §30.16) ─────────────────────────────────────────────
# One entry per phone number the customer carries (its device, its plan, the dated sales on it — each opens, edits
# through PATCH /pos/activations/{id} and takes notes through /pos/activations/{id}/notes), what they paid per
# invoice, the other names they went by, and merge / un-merge. The decisions live in pos/customer_identity; the
# reads and writes in pos/customer_master. Merging needs migration 1017 — without it the answer says so.
from app.modules.pos import customer_master as _cmaster


def _customer_or_404(customer_id: str, org_id: str):
    rows = (sb().schema("pos").table("customers").select("id").eq("org_id", org_id).eq("id", customer_id)
            .limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "customer not found")


@router.get("/customers/{customer_id}/lines")
def customer_lines(customer_id: str, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """ONE ENTRY PER PHONE NUMBER: the number, its device (IMEI), its plan, first / last date, and the dated sales on
    that line (invoice #, the receipt to print, the sale total, what the receipt prints for the line, notes count)."""
    _require_member(authorization, org_id)
    _customer_or_404(customer_id, org_id)
    return _cmaster.lines_payload(sb(), org_id, customer_id)


@router.get("/customers/{customer_id}/invoices")
def customer_invoices(customer_id: str, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """PER INVOICE what the customer paid (Σ the customer's payment lines), the total, the balance, the receipt."""
    _require_member(authorization, org_id)
    _customer_or_404(customer_id, org_id)
    return _cmaster.invoices_payload(sb(), org_id, customer_id)


@router.get("/customers/{customer_id}/aliases")
def customer_aliases(customer_id: str, org_id: str = ORG_ID, authorization: str = Header(default="")):
    """The other names this customer went by (combined on a shared phone line, or merged), the customers merged
    into it, the customer it was merged into; says "apply migration 1017" until the table exists."""
    _require_member(authorization, org_id)
    _customer_or_404(customer_id, org_id)
    return _cmaster.aliases_payload(sb(), org_id, customer_id)


@router.post("/customers/{customer_id}/merge")
def customer_merge(customer_id: str, body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Merge THIS customer into `into`: its sales, receipts, phone lines, trade-ins, special orders, notes and names
    move to `into`; this one is kept (inactive, merged_into = into) with the list of what moved, so Un-merge puts
    exactly that back. Body: {into, reason?}. Gated `pos_customers_merge` (a full-scope admin by default)."""
    _require_pos_perm(authorization, org_id, "pos_customers_merge")
    into = str(body.get("into") or "").strip()
    who = _caller_employee(authorization, org_id) or None
    out = _cmaster.merge_customers(sb(), org_id, customer_id, into, who=who, reason=(body.get("reason") or "").strip() or None)
    if not out["ok"]:
        raise HTTPException(409 if _cmaster.MIGRATION_WORDS in out["words"] else 400, " ".join(out["words"]))
    return out


@router.post("/customers/{customer_id}/unmerge")
def customer_unmerge(customer_id: str, body: dict = None, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Undo a merge (or a placeholder detach) of THIS customer from its recorded move list. Body: {reason?}."""
    _require_pos_perm(authorization, org_id, "pos_customers_merge")
    who = _caller_employee(authorization, org_id) or None
    out = _cmaster.unmerge(sb(), org_id, customer_id, who=who, reason=((body or {}).get("reason") or "").strip() or None)
    if not out["ok"]:
        raise HTTPException(409 if _cmaster.MIGRATION_WORDS in out["words"] else 400, " ".join(out["words"]))
    return out


@router.post("/customers/dedupe")
def customers_dedupe(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The one-time cleanup of customers created before the matcher (the house counted-confirm): {dry_run: true} (or
    no confirm_count) PLANS — exact same-name duplicates merged into the first created, placeholder customers
    ('Walk In') detached — and writes nothing; send it again with confirm_count = the planned count to apply (a
    different count is refused with the fresh plan). Every change can be undone with Un-merge. Gated like merge."""
    _require_pos_perm(authorization, org_id, "pos_customers_merge")
    confirm = body.get("confirm_count")
    if body.get("dry_run", True) is not False or confirm in (None, ""):
        return {"dry_run": True, **_cmaster.dedupe_plan(sb(), org_id)}
    who = _caller_employee(authorization, org_id) or None
    out = _cmaster.dedupe_apply(sb(), org_id, int(confirm), who=who, reason=(body.get("reason") or "").strip() or None)
    if not out.get("applied"):
        raise HTTPException(409, " ".join(out["words"]))
    return {"dry_run": False, **out}


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
def add_inventory_serial(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Receive one serialised unit — through THE landing guard (index §11b): a duplicate IMEI or an already-sold
    device answers 409 with the evidence unless the body carries confirm_duplicate: true."""
    from app.modules.pos import inventory_integrity_router as _iir
    return _iir.receive_unit(body, authorization, org_id)


@router.patch("/inventory/serial/{unit_id}")
def update_inventory_serial(unit_id: str, body: dict, authorization: str = Header(default=""),
                            org_id: str = ORG_ID):
    """Edit one unit — a status change goes through the adjustment ledger, a new serial / IMEI through the landing
    guard, an integrity field needs pos_inventory_adjust (index §11b)."""
    from app.modules.pos import inventory_integrity_router as _iir
    return _iir.edit_unit(unit_id, body, authorization, org_id)


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


@router.post("/inventory/standard")
def receive_inventory_standard(body: dict, authorization: str = Header(default=""),
                               org_id: str = ORG_ID):
    """Put standard (qty-tracked) stock on hand at ONE store — the single-row entry the register's
    "create this product and we have n of them" flow needs, and the first such path to exist
    (before it, qty on hand only arrived through the CSV count importer or an inter-store transfer).

    Body: {product_id, store_code, qty, mode?: add|set, qty_on_order?, qty_reserved?, bin_location?}.
    `mode` declares WHICH fact is being recorded — `add` (default) is a receipt and adds to what is
    on hand, `set` is a physical count and replaces it. THE arithmetic lives in
    `pos/stock_landing.plan`, never here, so the register and any later caller cannot drift apart.

    Refuses a serialised product: a serial-tracked unit lands through POST /pos/inventory/serial,
    behind the duplicate-IMEI landing guard (index §11b). Writing a qty for it would create stock
    with no unit behind it."""
    _require_pos_perm(authorization, org_id, "pos_inventory_adjust")
    body = body or {}
    pid = (body.get("product_id") or "").strip()
    store = (body.get("store_code") or "").strip()
    if not pid:
        raise HTTPException(400, "product_id required")
    keyset = _caller_store_keyset(authorization, org_id)
    if store and not _span_filter([{"store_code": store}], keyset):
        raise HTTPException(403, "that store is outside your reporting span")
    client = sb()
    prod = (client.schema("pos").table("products").select("id,short_name,inventory_type")
            .eq("org_id", org_id).eq("id", pid).limit(1).execute().data) or []
    if not prod:
        raise HTTPException(404, "product not found")
    if (prod[0].get("inventory_type") or "standard") == "serial":
        raise HTTPException(400, "this is a serial-tracked product — receive each unit through "
                                 "POST /pos/inventory/serial so the duplicate-IMEI guard applies")
    rows = (client.schema("pos").table("inventory_standard").select("*")
            .eq("org_id", org_id).eq("product_id", pid).eq("store_code", store)
            .limit(5).execute().data) or []
    try:
        planned = _stock.plan(rows, pid, store, body.get("qty"),
                              mode=body.get("mode") or _stock.MODE_ADD, extra=body)
    except _stock.StockError as e:
        raise HTTPException(400, str(e)) from None
    if planned["action"] == _stock.ACTION_INSERT:
        client.schema("pos").table("inventory_standard").insert(
            {**planned["row"], "org_id": org_id}).execute()
    elif planned["action"] == _stock.ACTION_UPDATE:
        (client.schema("pos").table("inventory_standard").update(planned["row"])
         .eq("org_id", org_id).eq("product_id", pid).eq("store_code", store).execute())
    return {"stock": {"product_id": pid, "store_code": store,
                      "qty_on_hand": planned["after"]},
            "landing": {"action": planned["action"], "before": planned["before"],
                        "after": planned["after"], "mode": planned["mode"]},
            "message": _stock.sentence(planned, prod[0].get("short_name"))}


# ── Tax codes ──────────────────────────────────────────────────────────────────────────────────────
# rate is a PERCENT (8.875); the register converts to a fraction for sale_items.tax_rate.
@router.get("/tax-codes")
def list_tax_codes(org_id: str = ORG_ID):
    rows = (sb().schema("pos").table("tax_codes").select("*").eq("org_id", org_id)
            .order("created_at").limit(200).execute().data) or []
    return {"tax_codes": rows}


def _resolve_tax(codes, store_code, market):
    """THE tax precedence — store_code > market > org-wide — DEREFERENCED, never re-derived.

    The rule itself lives in `pos/tax_precedence.pick` (index §POS-14). It was written here, and the
    register then grew a second, shorter copy of it that skipped the MARKET rung: a rate set on a
    market read as `effective` in the Settings grid and charged the org-wide rate (or nothing) at
    the till. It is pure and DB-free there, so the browser reaches the same rule through
    GET /pos/tax-codes/resolve instead of owning a second opinion about what the customer owes.

    pos.checkout does not compute tax — it stores the tax_total it is given — so every caller must
    reach a rate through here."""
    return _taxrule.pick(codes, store_code, market)


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
            # THE canonical union map (core.scope.market_by_code; 2026-09-03 LI-class fix — was a
            # storeops.stores-only read, so a market spelled only in store_mapping never matched a
            # market-scoped tax code and the register silently fell through to the org rung).
            from app.core import scope as _cscope
            market = _cscope.market_by_code(client, org_id).get(sc.upper(), "")
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
    _mkt = {}
    try:   # canonical union overlay (core.scope; 2026-09-03 LI-class fix) — blank rows only
        from app.core import scope as _cscope
        _mkt = _cscope.market_by_code(client, org_id)
    except Exception:
        _mkt = {}
    agg = {}
    for s in rows:
        if s.get("is_active") is False:
            continue
        m = (s.get("market") or "").strip() or _mkt.get(str(s.get("store_code") or "").strip().upper(), "")
        if m:
            agg[m] = agg.get(m, 0) + 1
    # §13c enumeration doctrine (owner 2026-09-04, B-1115/LI class): a market whose stores live only
    # in commcalc.store_mapping (no storeops roster row — the mirror of the B-1115 shape) never made
    # this dropdown, so its tax rate could not be configured. Add every canonical-vocabulary market
    # missing above, counting its union-index member stores. Existing counts are byte-identical.
    try:
        from app.core import scope as _cscope
        _idx = _cscope.market_index(client, org_id)
        _have = {k.strip().casefold() for k in agg}
        for mk in (_idx.get("markets") or []):
            if mk.strip().casefold() not in _have:
                agg[mk] = len(((_idx.get("by_market") or {}).get(mk.lower()) or {}).get("codes") or ())
    except Exception as e:
        print(f"WARN pos tax-code markets canonical vocabulary union failed: {e}")
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
    try:   # canonical union overlay (core.scope; 2026-09-03 LI-class fix) — blank rows only
        from app.core import scope as _cscope
        _mkt = _cscope.market_by_code(client, org_id)
        for s in stores:
            if not (s.get("market") or "").strip():
                s["market"] = _mkt.get(str(s.get("store_code") or "").strip().upper(), s.get("market"))
    except Exception:
        pass
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



# ── THE WHOLESALE CUSTOMER AND THEIR W-9 (owner 2026-10-30, index §POS-16) ───────────────────────
# `pos/wholesale_w9.py` is THE home for "may I sell to this customer, and is this sale taxable".
# Every route below and the checkout DEREFERENCE it: none of them judges a certificate, words a
# refusal, or decides that a sale is tax-free.
#
# ADAPTIVE, like §POS-01's held cart and §POS-12's amendments: until mig 1107 is applied
# `pos.customer_tax_doc` is absent. Reads then answer as though no certificate exists (which is the
# truth), the register shows the setup note instead of a block, and the WRITES refuse by name —
# a confirmation we cannot store must not be reported as stored.
W9_BUCKET = "customer-tax-docs"
W9_MIGRATION = ("apply migration 1107 (database/migrations/1107_pos_wholesale_w9.sql) — until it is "
                "applied a wholesale customer's W-9 cannot be recorded")


def _w9_cfg(client, org_id: str, store_code: str = "") -> dict:
    """The W-9 settings, resolved through the ONE config home. The keys are named by the one home."""
    rows = _pos_settings_rows(client, org_id, store_code)
    return {k: _poscfg.effective(rows, k, store_code)
            for k in (_w9.EXEMPT_DEFAULT_KEY, _w9.VALID_MONTHS_KEY, _w9.BLOCK_SALE_KEY,
                      _w9.FORM_URL_KEY)}


def _w9_doc(client, org_id: str, customer_id: str):
    """This customer's W-9 row, or None. `(False, None)` when mig 1107 is not applied yet."""
    try:
        rows = (client.schema("pos").table("customer_tax_doc").select("*")
                .eq("org_id", org_id).eq("customer_id", customer_id).eq("kind", _w9.W9_KIND)
                .limit(1).execute().data) or []
    except Exception:
        return False, None
    return True, (rows[0] if rows else None)


def _customer_row_or_none(client, org_id: str, customer_id: str):
    """This customer's row, or None when there is no id, no row, or the read failed."""
    cid = str(customer_id or "").strip()
    if not cid:
        return None
    try:
        rows = (client.schema("pos").table("customers").select("*")
                .eq("org_id", org_id).eq("id", cid).limit(1).execute().data) or []
    except Exception:
        return None
    return rows[0] if rows else None


def _customer_row(client, org_id: str, customer_id: str):
    rows = (client.schema("pos").table("customers").select("*")
            .eq("org_id", org_id).eq("id", customer_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "that customer could not be found")
    return rows[0]


def _w9_decision(client, org_id: str, customer, store_code: str = "", *, is_admin=False,
                 override=None):
    """The one decision, with today's date. Returns `(decision, table_present, doc)`."""
    present, doc = _w9_doc(client, org_id, (customer or {}).get("id") or "")
    cfg = _w9_cfg(client, org_id, store_code)
    d = _w9.may_sell(customer, doc, cfg, now=_dt.date.today(), is_admin=is_admin, override=override)
    return d, present, doc


def _is_pos_admin(authorization: str, org_id: str) -> bool:
    """May this login go past a missing certificate? The caller's OWN rights, read the way every
    other POS gate reads them — never a body field claiming them."""
    try:
        ctx = _caller_ctx(authorization, org_id) or {}
    except Exception:
        return False
    if ctx.get("super_admin"):
        return True
    if str(ctx.get("role") or "").strip().lower() in ("admin", "owner"):
        return True
    return bool((ctx.get("perms") or {}).get("pos_settings"))


@router.get("/customer-account-types")
def customer_account_types(org_id: str = ORG_ID):
    """The customer types this platform knows (`wholesale_w9.ACCOUNT_TYPES`) — read by the customer
    form so the words are not spelled a third time (the CHECK constraint is the second, generated
    from the same list by mig 1107)."""
    return {"account_types": list(_w9.ACCOUNT_TYPES), "wholesale": _w9.WHOLESALE}


@router.get("/customers/{customer_id}/w9")
def customer_w9(customer_id: str, store_code: str = "", authorization: str = Header(default=""),
                org_id: str = ORG_ID):
    """What the register needs to draw the W-9 panel for one customer."""
    client = sb()
    customer = _customer_row(client, org_id, customer_id)
    is_admin = _is_pos_admin(authorization, org_id)
    d, present, doc = _w9_decision(client, org_id, customer, store_code, is_admin=is_admin)
    cfg = _w9_cfg(client, org_id, store_code)
    return {**{k: v for k, v in d.items() if k != "override_by"},
            "form_url": _w9.blank_form_url(cfg),
            "accepted_formats": list(_upload_format.DEFAULT_FORMATS),
            "requested_at": (doc or {}).get("requested_at"),
            "requested_to_email": (doc or {}).get("requested_to_email"),
            "submitted_at": (doc or {}).get("submitted_at"),
            "received_at": (doc or {}).get("received_at"),
            "link_live": _w9.token_live(doc, now=_dt.datetime.utcnow()),
            "available": bool(present),
            "setup_note": None if present else W9_MIGRATION}


@router.post("/customers/{customer_id}/w9/confirm")
def confirm_customer_w9(customer_id: str, body: dict = None,
                        authorization: str = Header(default=""), org_id: str = ORG_ID):
    """A person confirms the filled-out, signed W-9 is in hand (owner: the exemption follows THIS).

    A customer's own upload never reaches this state by itself — it lands as `submitted` and waits
    for a human, because this is the row an exemption rests on.
    """
    body = body or {}
    client = sb()
    customer = _customer_row(client, org_id, customer_id)
    if not _w9.is_wholesale(customer):
        raise HTTPException(400, "that customer is not on wholesale terms, so no W-9 is needed")
    present, doc = _w9_doc(client, org_id, customer_id)
    if not present:
        raise HTTPException(503, W9_MIGRATION)
    who = _caller_employee(authorization, org_id) or ""
    row = {"status": _w9.STATUS_RECEIVED,
           "received_at": str(body.get("received_at") or _dt.date.today().isoformat())[:10],
           "confirmed_by": who,
           "expires_at": (str(body.get("expires_at"))[:10] if body.get("expires_at") else None),
           "note": (str(body.get("note") or "").strip() or None),
           # the door closes: a confirmed certificate needs no open upload link
           "return_token": None, "token_expires_at": None,
           "updated_at": _dt.datetime.utcnow().isoformat() + "Z"}
    if doc:
        (client.schema("pos").table("customer_tax_doc").update(row)
         .eq("org_id", org_id).eq("id", doc["id"]).execute())
    else:
        client.schema("pos").table("customer_tax_doc").insert(
            {**row, "org_id": org_id, "customer_id": customer_id, "kind": _w9.W9_KIND}).execute()
    d, _p, _d = _w9_decision(client, org_id, customer)
    return {"confirmed": True, "confirmed_by": who, "decision": d,
            "message": "W-9 recorded as received. Wholesale sales to this customer are now "
                       + ("tax-free." if d["tax_exempt"] else "allowed.")}


@router.post("/customers/{customer_id}/w9/request")
async def request_customer_w9(customer_id: str, body: dict = None,
                              authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Email this customer the blank form and a one-use link to send it back.

    The LINK goes to the customer's mailbox; the screen that asked is told the address and that it
    went, not handed the raw token to display or log (§POS-16.6).
    """
    body = body or {}
    client = sb()
    customer = _customer_row(client, org_id, customer_id)
    to = (str(body.get("email") or "").strip() or str(customer.get("email") or "").strip())
    if not to or "@" not in to:
        raise HTTPException(400, "no email address for this customer — type one, or add one to "
                                 "their profile first")
    present, doc = _w9_doc(client, org_id, customer_id)
    if not present:
        raise HTTPException(503, W9_MIGRATION)
    from app.modules.notify.channels import email_resend as _mail
    if not _mail.is_configured():
        raise HTTPException(503, "emailing isn't switched on for this platform yet "
                                 "(no mail provider configured)")
    cfg = _w9_cfg(client, org_id)
    token = _w9.new_token()
    now = _dt.datetime.utcnow()
    company = _tenant.display_name(client, org_id)
    form_url = _w9.blank_form_url(cfg)
    link = _w9.return_link(settings.APP_PUBLIC_URL, token)
    name = " ".join(x for x in [customer.get("first_name"), customer.get("last_name")] if x).strip()
    try:
        mid = await _mail.send_email(
            to, _w9.request_subject(company), _w9.request_html(company, form_url, link, name),
            None, from_name=company or None)
    except Exception as e:
        raise HTTPException(502, f"the mail provider refused it: {str(e)[:200]}")
    row = {"status": _w9.STATUS_REQUESTED, "requested_at": now.isoformat() + "Z",
           "requested_by": _caller_employee(authorization, org_id) or "",
           "requested_to_email": to, "return_token": token,
           "token_expires_at": _w9.token_expiry(now).isoformat() + "Z",
           "updated_at": now.isoformat() + "Z"}
    if doc:
        (client.schema("pos").table("customer_tax_doc").update(row)
         .eq("org_id", org_id).eq("id", doc["id"]).execute())
    else:
        client.schema("pos").table("customer_tax_doc").insert(
            {**row, "org_id": org_id, "customer_id": customer_id, "kind": _w9.W9_KIND}).execute()
    return {"sent": True, "to": to, "link": link, "provider_message_id": mid or None,
            "expires_in_days": _w9.RETURN_LINK_DAYS,
            "message": f"Form W-9 emailed to {to} with a link to send it back. The link stops "
                       f"working in {_w9.RETURN_LINK_DAYS} days."}


# ── the customer's return link (ANONYMOUS, token-gated) ─────────────────────────────────────────
# The one-use token IS the auth and reaches ONLY this customer's upload slot. Unknown, expired and
# already-used all answer the SAME 404 with the SAME body, so a probe learns nothing. The page is
# told who is asking and what file to send — never anything from the customer's record.
def _w9_by_token(token: str):
    try:
        rows = (sb().schema("pos").table("customer_tax_doc").select("*")
                .eq("return_token", (token or "").strip()).limit(1).execute().data) or []
    except Exception:
        rows = []
    doc = rows[0] if rows else None
    if not doc or not _w9.token_live(doc, now=_dt.datetime.utcnow()):
        raise HTTPException(404, _w9.LINK_DEAD)
    return doc


def _store_tax_doc_file(org_id: str, customer_id: str, data: bytes, filename: str, fmt: str):
    """Put the returned document in the private bucket. Separate so the upload route has one job."""
    import uuid as _uuid
    safe = (filename or "w9").replace("/", "_")[-80:]
    path = f"{org_id}/{customer_id}/{_uuid.uuid4().hex}_{safe}"
    c = sb()
    try:
        c.storage.get_bucket(W9_BUCKET)
    except Exception:
        try:
            c.storage.create_bucket(W9_BUCKET)   # private by default
        except Exception:
            pass
    try:
        c.storage.from_(W9_BUCKET).upload(path, data, {"content-type": "application/octet-stream"})
    except Exception as e:
        raise HTTPException(500, f"the upload could not be stored: {str(e)[:160]}")
    return {"path": path, "format": fmt}


@router.get("/public/w9/{token}")
def public_w9_meta(token: str):
    """What the customer's browser loads: who is asking, the blank form, what file to send."""
    doc = _w9_by_token(token)
    cfg = _w9_cfg(sb(), doc["org_id"])
    company = _tenant.display_name(sb(), doc["org_id"])
    return {"ok": True, "company_name": company, "form_url": _w9.blank_form_url(cfg),
            "accepted_formats": list(_upload_format.DEFAULT_FORMATS),
            "what_we_need": "a completed, signed Form W-9"}


@router.post("/public/w9/{token}/upload")
async def public_w9_upload(token: str, file: UploadFile = File(...)):
    """The customer sends the completed form back.

    It lands as `submitted`, NOT as received: the exemption follows a person confirming the form is
    in hand (the owner's wording), and an anonymous caller cannot be that person. The token is
    cleared here — one use, then the door closes.
    """
    doc = _w9_by_token(token)
    data = await file.read()
    ok, detected = _upload_format.allowed(data, file.filename or "", _upload_format.DEFAULT_FORMATS)
    if not ok:
        raise HTTPException(400, _upload_format.refusal_sentence(
            _upload_format.DEFAULT_FORMATS, detected))
    stored = _store_tax_doc_file(doc["org_id"], doc["customer_id"], data, file.filename or "", detected)
    now = _dt.datetime.utcnow().isoformat() + "Z"
    (sb().schema("pos").table("customer_tax_doc")
     .update({"status": "submitted", "submitted_at": now, "file_path": stored["path"],
              "file_format": stored["format"], "return_token": None, "token_expires_at": None,
              "updated_at": now})
     .eq("org_id", doc["org_id"]).eq("id", doc["id"]).execute())
    return {"received": True,
            "message": "Thank you — your Form W-9 has been sent. Nothing else is needed from you."}


# ── The tenders a register may take (owner 2026-10-30, index §POS-15) ─────────────────────────────
# `pos/tender_vocabulary.py` is THE home for "which tenders exist, which this org offers, and what a
# Terms sale needs". This endpoint and the checkout below both DEREFERENCE it; neither spells a
# tender word, a day count or a refusal of its own. The register reads this instead of carrying its
# own array of buttons, which is what made adding one tender type a three-file edit before.
def _tender_cfg(client, org_id: str, store_code: str = "") -> dict:
    """The register settings the tender vocabulary asks about, resolved through the ONE config home
    (store override → org default → declared default). The KEYS are named by the vocabulary, so a
    new setting is added in one place."""
    rows = _pos_settings_rows(client, org_id, store_code)
    return {k: _poscfg.effective(rows, k, store_code)
            for k in (_tender.SUSPEND_CARDS_KEY, _tender.TERMS_ENABLED_KEY, _tender.TERMS_DAYS_KEY)}


TERMS_MIGRATION = ("apply migration 1106 (database/migrations/1106_pos_terms_tender.sql) — until it "
                   "is applied the database will not accept a sale paid on account")


def _terms_schema_ready(client) -> bool:
    """Is the database ready to STORE a terms sale (§POS-15, mig 1106)?

    This is not belt-and-braces. `pos.sale_payments` carries `CHECK (payment_method IN …)` from mig
    725, and until 1106 widens it the database REFUSES a terms leg. Offering the button before then
    would put a Terms key on every register that fails at the till — the cashier's sale lost, with a
    constraint error for an explanation. So the tender is offered only once its storage exists.
    The probe asks for the column 1106 adds; any failure reads as "not applied", never as ready."""
    try:
        client.schema("pos").table("sale_payments").select("terms_days").limit(1).execute()
        return True
    except Exception:
        return False


@router.get("/tender-types")
def tender_types(store_code: str = "", org_id: str = ORG_ID):
    """Which tenders THIS register offers, each with the facts the screen needs to draw it."""
    client = sb()
    cfg = _tender_cfg(client, org_id, store_code)
    ready = _terms_schema_ready(client)
    if not ready:
        # The org's own declaration is left alone — this is the SCHEMA not being ready, which is a
        # platform fact and not a change to what the tenant asked for. When 1106 lands, the tender
        # appears with no further configuration.
        cfg = {**cfg, _tender.TERMS_ENABLED_KEY: False}
    return {"tenders": _tender.offered(cfg),
            "terms_day_options": list(_tender.terms_day_options(cfg)),
            "terms_available": ready,
            **({} if ready else {"setup_note": TERMS_MIGRATION})}


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
    # WHICH STORE IS THIS SALE FOR (index §POS-14, owner 2026-10-30). This gate did not exist: the
    # endpoint took whatever store the body named — any store in the tenant — and took NO store at
    # all, which `pos.checkout` writes as NULL and `_span_filter` then shows to every store-scoped
    # manager in the tenant. A sale nobody can attribute is not a sale, so the refusal is the one
    # sentence `working_store` owns rather than a second wording invented here.
    # ONE span, read once: the refusal and the spelling must be two readings of the SAME answer. Two
    # reads could disagree if the roster read failed between them, and the second one losing would
    # write a NULL store onto a sale the first one had just authorised.
    _span = _working_span(authorization, org_id)
    _refusal = _wstore.sale_refusal(_span, sale.get("store_code"))
    if _refusal:
        raise HTTPException(403, _refusal)
    # The ROSTER'S spelling, not the terminal's. Store codes are compared without case folding
    # across this platform, and a lower-cased code once became a phantom store holding a day's money.
    sale["store_code"] = _wstore.resolve(_span, sale.get("store_code"))["store_code"]
    # ── THE WHOLESALE GATE AND THE TAX (§POS-16), asked before a single row is written ──
    # Both gates below run AFTER the working-store gate above, and must: that gate resolves
    # `sale["store_code"]` to the roster's own spelling, and every per-store declaration read here
    # (the tenders this org offers, the W-9 refresh period, the tax rate) is keyed by that code.
    # Reading the terminal's spelling instead is how a lower-cased code once became a phantom store.
    # `pos.checkout` stores the figures it is handed, so without this a till could ring a wholesale
    # sale with no certificate on file, or charge no tax on one that has none, and be obeyed.
    # A customer id that resolves to no row reads as "no customer", never as a refusal: the gate's
    # job is to stop an unsupported exemption, and `pos.sales.customer_id` has a foreign key that
    # already refuses a sale pointing at nobody. Failing the whole sale on a read would be a new
    # way for a till to stop working.
    customer = _customer_row_or_none(sb(), org_id, sale.get("customer_id"))
    # The override comes from the CALLER's own rights, never from the body: a body field claiming
    # an override is exactly the shape this gate exists to refuse.
    wants_override = bool(sale.pop("w9_override", False))
    sale.pop("w9_override_by", None)
    is_admin = _is_pos_admin(authorization, org_id) if customer is not None else False
    w9d = {"blocked": False, "tax_exempt": False, "exempt_basis": None, "override_by": None}
    if customer is not None:
        w9d, _present, _doc = _w9_decision(
            sb(), org_id, customer, sale.get("store_code") or "", is_admin=is_admin,
            override=(eid if wants_override else None))
        if w9d["blocked"]:
            raise HTTPException(400, w9d["refusal"])
    # WHAT TAX DO THESE LINES CARRY. Asked only so that a TAX-EXEMPT sale can be refused if it
    # carries tax anyway — never to second-guess the sale's own `tax_total`. An earlier revision of
    # this gate refused a sale whose `tax_total` disagreed with its lines; main's working-store
    # tests showed why that was wrong: the server resolves the rate itself (§POS-14), so a till
    # holding a stale rate sends a figure that legitimately differs, and refusing it would stop a
    # register from ringing anything. A disagreement there is not the till lying — it is the till
    # being out of date, which is not this gate's business.
    def _n(v):
        try:
            return round(float(v or 0), 2)
        except (TypeError, ValueError):
            return 0.0

    def _line_tax(i):
        """The tax ONE line carries, in whichever shape its caller speaks: the register sends
        `tax_value` already multiplied by qty, while the activation leg and other callers send a
        `tax_rate` against the line's extended price. Reading only the first shape would read a
        perfectly taxed line as untaxed, which on an exempt sale is the wrong way round to be
        wrong."""
        if i.get("tax_value") not in (None, ""):
            return _n(i.get("tax_value"))
        if i.get("tax_rate") not in (None, ""):
            ext = i.get("extended_price")
            if ext in (None, ""):
                ext = _n(i.get("qty") or 1) * _n(i.get("unit_price")) - _n(i.get("discount"))
            return round(_n(i.get("tax_rate")) * _n(ext), 2)
        return 0.0

    line_tax = round(sum(_line_tax(i) for i in items if isinstance(i, dict)), 2)
    if w9d["tax_exempt"]:
        if line_tax > 0.009 or _n(sale.get("tax_total")) > 0.009:
            raise HTTPException(400, "this is a tax-exempt wholesale sale, so no line may carry "
                                     "sales tax — clear the cart and re-add the items, or ask an "
                                     "admin if the exemption is wrong")
        sale["tax_exempt_basis"] = w9d["exempt_basis"]
    else:
        sale["tax_exempt_basis"] = None
    sale["w9_override_by"] = w9d["override_by"]

    # THE TENDER DECISION (§POS-15). `pos.checkout` stores what it is handed, so a till sending a
    # tender this org does not offer, a Terms sale with nobody to owe it, or a day count nobody
    # declared would otherwise be obeyed. The refusal is the one home's own sentence.
    # EVERY payment leg is asked about, not just the first: a sale split across a card and an
    # account is the case where validating one leg and trusting the other writes the wrong balance.
    tcfg = _tender_cfg(sb(), org_id, sale.get("store_code") or "")
    # A screen loaded before mig 1106 landed (or cached since) can still SEND a terms leg. Refuse it
    # by name here rather than let `pos.sale_payments`'s CHECK constraint refuse it as a 500.
    if not _terms_schema_ready(sb()):
        tcfg = {**tcfg, _tender.TERMS_ENABLED_KEY: False}
    checked, owed = [], 0.0
    for raw in (payments or []):
        pay = dict(raw) if isinstance(raw, dict) else {}
        plan = _tender.plan_payment(
            pay.get("payment_method"), tcfg, customer_id=sale.get("customer_id"),
            terms_days=pay.get("terms_days"),
            sale_date=sale.get("date") or sale.get("created_at"),
            total=pay.get("amount") if len(payments) > 1 else sale.get("total"))
        if plan["refusal"]:
            raise HTTPException(400, plan["refusal"])
        pay["terms_days"] = plan["terms_days"]
        pay["due_date"] = plan["due_date"].isoformat() if plan["due_date"] else None
        owed += plan["balance"]
        checked.append(pay)
    payments = checked
    # The figure is OURS, not the body's: what is owed is the sum of the legs that did not settle,
    # whatever balance the screen sent.
    sale["balance"] = round(owed, 2)
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


# ── Editing a rung sale in place (mig 1103, index §POS-12) ────────────────────────────────────────
# Owner 2026-10-29: "im not able to edit the receipt ot add more tiems to teh same rec3ipt", and,
# offered three ways, he chose editing in place over re-ringing under a new number.
#
# BOTH endpoints dereference `pos/sale_amendment.py` for every judgement and every figure. Neither
# decides whether a sale may be edited, what a line comes to, or what changed — a second opinion on
# any of those here is how the Sale Log's button and the save would come to disagree.
#
# A SEPARATE GRANT. `pos_amend`, not `pos_void`: rewriting a tendered record is a different and
# larger right than cancelling one, and the owner may want a shift manager to hold one without the
# other. Unknown keys default to full-scope admin in `_pos_grant`, so this starts off manager-only
# without a seeded row.
#
# ADAPTIVE, like §POS-01's held cart: until mig 1103 is applied the history table and the function
# are absent. The plan endpoint then answers `available: false` with the setup note, so the Sale Log
# simply does not offer the button rather than offering one that errors.
from app.modules.pos import sale_amendment as _amend
# The cash / credit mismatch an edit leaves behind (§POS-17). A SECOND one home beside the first,
# not a bigger one: `sale_amendment` answers "may this be edited and what does it come to", and
# `sale_settlement` answers "what was tendered against it and what does that leave outstanding".
# The second question has callers the first does not (the void path, and the mismatch report), which
# is precisely why it is its own module rather than more functions on that one.
from app.modules.pos import sale_settlement as _settle


def _tender_band(client, org_id: str, store_code: str = ""):
    """The declared tender-mismatch band for this store, read through the ONE config home and named
    by the one home for the setting — never a default spelled beside this call (RULE TWO)."""
    rows = _pos_settings_rows(client, org_id, store_code)
    return _poscfg.effective(rows, _settle.BAND_SETTING, store_code)


def _sale_payments(client, org_id: str, sale_id: str) -> list:
    """What was tendered against this sale, as recorded. An EMPTY list is returned on absence and is
    never turned into a $0.00 payment — `sale_settlement.tendered` is the one place that distinction
    is made, and 4,603 of the 4,605 live sales depend on it being made correctly."""
    return (client.schema("pos").table("sale_payments")
            .select("payment_method,amount,card_last_four,created_at")
            .eq("org_id", org_id).eq("sale_id", sale_id)
            .order("created_at").limit(100).execute().data) or []


def _tender_log_available(client, org_id: str) -> bool:
    """Whether mig 1108 has been applied. A PROBE, not a claim — the same shape as
    `_amend_available`: ask the database for the column and let it answer."""
    try:
        (client.schema("pos").table("sale_amendments").select("tender_variance")
         .eq("org_id", org_id).limit(1).execute())
        return True
    except Exception:
        return False


def _amend_elevated(authorization: str, org_id: str) -> bool:
    """Whether this caller reaches further back than an ordinary POS user (§POS-12.9).

    THE RUNG IS A GRANT, never a role name — `sale_amendment.ELEVATED_GRANT`, resolved by the same
    `_pos_grant` precedence every other POS right uses. An unseeded key defaults to a full-scope
    admin there, which is exactly the owner's ask ("30 days nback for the admin and same day for teh
    sales rep or store manager") with no configuration; a tenant that wants a named role to hold it
    grants the key in the Roles screen. FAILS CLOSED: an unresolvable caller is not elevated."""
    return _pos_grant(_caller_ctx(authorization, org_id), _amend.ELEVATED_GRANT)


def _amend_window(client, org_id: str, store_code: str = "", elevated: bool = False):
    """How far back THIS caller may reopen a sale, read through the ONE config home, named by the
    one home for both settings, and reconciled by the one home for the rung. No default and no
    reconciliation of the two numbers is spelled beside this call: both belong to the one home, or
    this becomes a second answer to the same question and the two drift on the first change."""
    rows = _pos_settings_rows(client, org_id, store_code)
    return _amend.window_for(
        elevated,
        declared=_poscfg.effective(rows, _amend.WINDOW_SETTING, store_code),
        declared_elevated=_poscfg.effective(rows, _amend.ELEVATED_WINDOW_SETTING, store_code),
    )


def _sale_with_items(client, org_id: str, sale_id: str):
    rows = (client.schema("pos").table("sales").select("*")
            .eq("org_id", org_id).eq("id", sale_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "that sale could not be found")
    sale = rows[0]
    items = (client.schema("pos").table("sale_items").select("*")
             .eq("sale_id", sale_id).order("created_at").execute().data) or []
    return sale, items


@router.get("/sales/{sale_id}/amend-plan")
def sale_amend_plan(sale_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """May this sale be edited, and with what lines. READ ONLY — nothing is written here, so the
    Sale Log can ask before it offers the button and the till can show the sentence before it saves.
    """
    _require_pos_perm(authorization, org_id, "pos_amend")
    client = sb()
    sale, items = _sale_with_items(client, org_id, sale_id)
    if not _span_filter([{"store_code": sale.get("store_code") or ""}],
                        _caller_store_keyset(authorization, org_id)):
        raise HTTPException(403, "that store is outside your reporting span")
    # WHICH RUNG IS ASKING (§POS-12.9). Resolved once and passed in, both so the sentence can name
    # the way forward when an administrator could still do it and so the Sale Log can say which
    # window it is offering the button under.
    _store = sale.get("store_code") or ""
    elevated = _amend_elevated(authorization, org_id)
    win = _amend_window(client, org_id, _store, elevated)
    reason = _amend.refusal(sale, window_days=win,
                            elevated_window_days=_amend_window(client, org_id, _store, True))
    try:
        cart = _amend.cart_from(items) if reason is None else []
    except _amend.AmendmentError as e:
        reason, cart = str(e), []
    # What stands tendered against this sale RIGHT NOW (§POS-17), so the till can show the cashier
    # what was taken before they move what is owed. The verdict is deliberately NOT previewed from
    # here: a cart the cashier has not sent yet is not a figure this endpoint has seen, and the
    # browser must not derive the mismatch itself. The dry run on the save route is what previews it,
    # and it is the SAME call that writes, so a preview cannot disagree with the save.
    payments = _sale_payments(client, org_id, sale_id)
    standing = _settle.outstanding(sale, payments,
                                   band=_tender_band(client, org_id, sale.get("store_code") or ""))
    return {
        "sale_id": sale_id,
        "transaction_id": sale.get("transaction_id"),
        "revision": _amend.current_revision(sale),
        "allowed": reason is None,
        "reason": reason,
        "cart": cart,
        "customer_id": sale.get("customer_id"),
        "is_activation_sale": bool(sale.get("is_activation_sale")),
        "store_code": sale.get("store_code"),
        "available": _amend_available(client, org_id),
        # HOW FAR BACK THIS CALLER MAY REACH, and whether they hold the longer window (§POS-12.9).
        # Stated rather than left to be inferred, so the Sale Log can word what it offers without
        # re-deriving the number — the same reason the refusal sentence comes from the server.
        "window_days": win,
        "elevated": elevated,
        "tender": standing,
        "tender_log_available": _tender_log_available(client, org_id),
    }


def _amend_available(client, org_id: str) -> bool:
    """Whether mig 1103 has been applied. A probe, not a claim: the Sale Log offers no edit button
    on a database that cannot record the version it would supersede."""
    try:
        (client.schema("pos").table("sale_amendments").select("id")
         .eq("org_id", org_id).limit(1).execute())
        return True
    except Exception:
        return False


@router.put("/sales/{sale_id}/items")
def amend_sale_items(sale_id: str, body: dict, authorization: str = Header(default=""),
                     org_id: str = ORG_ID):
    """Save an edited sale, keeping its transaction number. Body: `{items: [...], reason?, notes?}`.

    THE FIGURES ARE OURS, NOT THE BODY'S. `pos.checkout` stores the totals it is handed, which is
    tolerable for a sale the browser just priced. It is not tolerable for an edit, whose purpose is
    to move those figures — so every line's `tax_value` / `extended_price` and all four sale totals
    are RE-DERIVED here through the one home, and whatever the client sent for them is discarded.
    """
    _require_pos_perm(authorization, org_id, "pos_amend")
    client = sb()
    sale, items = _sale_with_items(client, org_id, sale_id)
    if not _span_filter([{"store_code": sale.get("store_code") or ""}],
                        _caller_store_keyset(authorization, org_id)):
        raise HTTPException(403, "that store is outside your reporting span")
    if not _amend_available(client, org_id):
        raise HTTPException(503, "editing a rung sale needs migration 1103 — it has not been "
                                 "applied, so the version this edit would supersede could not be "
                                 "kept on file")
    try:
        _elevated = _amend_elevated(authorization, org_id)
        _store = sale.get("store_code") or ""
        plan = _amend.plan(sale, items, body.get("items"),
                           window_days=_amend_window(client, org_id, _store, _elevated),
                           elevated=_elevated,
                           elevated_window_days=_amend_window(client, org_id, _store, True))
    except _amend.AmendmentError as e:
        raise HTTPException(400, str(e)) from None
    if not plan["allowed"]:
        raise HTTPException(400, plan["reason"])

    # ── the cash / credit mismatch this edit leaves behind (§POS-17) ─────────────────────────────
    # Measured against the RESTATED total, because that is what will be owed once this write lands,
    # and against the tender as RECORDED, which this write does not touch. Both figures come from
    # the one home; nothing here subtracts anything itself.
    payments = _sale_payments(client, org_id, sale_id)
    after = {**sale, **plan["totals"]}
    band = _tender_band(client, org_id, sale.get("store_code") or "")
    settlement = _settle.outstanding(after, payments, band=band)
    tender = _settle.record(after, payments, band=band)
    tender_sentence = _settle.sentence(after, settlement)

    # THE DRY RUN. The preview the till shows before it commits is THIS call with nothing written,
    # so the figure the cashier agreed to and the figure that lands are the same computation rather
    # than two that happen to agree today. A browser deriving its own preview is the sibling
    # derivation the house rules forbid, and this is the shape that makes one unnecessary.
    if body.get("dry_run"):
        return {"dry_run": True, "revision": plan["revision"], "totals": plan["totals"],
                "changed": plan["diff"]["summary"], "message": plan["sentence"],
                "tender": settlement, "tender_message": tender_sentence,
                "tender_log_available": _tender_log_available(client, org_id)}

    # AN EDIT THAT CREATES UNEXPLAINED MONEY DOES NOT PROCEED UNLESS THE EXPLANATION CAN BE FILED.
    # Exactly §POS-12's argument for refusing when the superseded version cannot be kept, applied to
    # the second record that makes an edit answerable. A SETTLED edit needs no log row, so it is not
    # held up by a migration it does not use.
    if settlement["mismatch"] and not _tender_log_available(client, org_id):
        raise HTTPException(503, "this edit would leave " + tender_sentence.rstrip(".") +
                                 ", and migration 1108 has not been applied, so that mismatch "
                                 "could not be logged — the edit was not saved")

    patch = {**plan["totals"],
             "change_summary": plan["diff"]["summary"],
             "change_detail": plan["diff"],
             "notes": (body.get("notes") or "").strip() or None,
             # Travels in the patch rather than as a new parameter, so mig 1108 replaces
             # `pos.amend_sale` at its existing signature and creates no overload.
             "tender": tender}
    try:
        r = client.schema("pos").rpc("amend_sale", {
            "p_org": org_id, "p_sale": sale_id,
            "p_sale_patch": patch, "p_items": plan["items"],
            "p_employee": _caller_employee(authorization, org_id) or None,
            "p_reason": (body.get("reason") or "").strip() or None,
        }).execute()
    except Exception as e:
        raise HTTPException(400, f"could not save the edit: {e}")
    return {"sale": r.data, "revision": plan["revision"], "totals": plan["totals"],
            "changed": plan["diff"]["summary"], "message": plan["sentence"],
            "tender": settlement, "tender_message": tender_sentence}


@router.get("/sales/{sale_id}/amendments")
def sale_amendments(sale_id: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Every version this sale has passed through, newest first. What makes an edited sale
    answerable: the figures the customer's printed receipt was struck from are still here."""
    _require_pos_perm(authorization, org_id, "pos_amend")
    client = sb()
    sale, _items = _sale_with_items(client, org_id, sale_id)
    if not _span_filter([{"store_code": sale.get("store_code") or ""}],
                        _caller_store_keyset(authorization, org_id)):
        raise HTTPException(403, "that store is outside your reporting span")
    try:
        rows = (client.schema("pos").table("sale_amendments")
                .select("revision,change_summary,total_before,total_after,reason,amended_by,created_at")
                .eq("org_id", org_id).eq("sale_id", sale_id)
                .order("revision", desc=True).limit(100).execute().data) or []
    except Exception:
        return {"revisions": [], "available": False,
                "setup_note": "apply migration 1103 to keep an edit history"}
    return {"revisions": rows, "available": True,
            "revision": _amend.current_revision(sale)}


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


# ── The suspended sale: ONE held cart per register (mig 1088, index §POS-01) ──────────────────────
# Owner 2026-10-16: "if a person leaves the cash register it gets blank if you come back it shoudl
# automatically store for the next session in suspensed state". The cart is a property of the
# TERMINAL, keyed on the same (org, store, register) triple as register_sessions above — so "the
# next session" is literal. All three endpoints dereference pos/held_sale.py; none of them writes a
# sale, burns a transaction number or moves stock.
#
# ADAPTIVE: before migration 1088 is applied the table is absent. Every one of these answers
# `available: false` instead of erroring, and the register then behaves exactly as it did before the
# feature existed (a cart lost on reload) rather than refusing to work.
_HELD_TABLE = "held_sales"


def _held_unavailable(exc):
    return {"held": None, "available": False,
            "setup_note": f"apply migration 1088 ({str(exc)[:120]})"}


def _held_terminal(store_code, register_number, authorization, org_id):
    try:
        t = _held.terminal(store_code, register_number)
    except _held.HeldSaleError as e:
        raise HTTPException(400, str(e)) from None
    if not _span_filter([{"store_code": t["store_code"]}],
                        _caller_store_keyset(authorization, org_id)):
        raise HTTPException(403, "that store is outside your reporting span")
    return t


@router.get("/held-sale")
def get_held_sale(store_code: str, register_number: int = 1,
                  authorization: str = Header(default=""), org_id: str = ORG_ID):
    """The cart left on this register, or null. Restored VERBATIM — the prices are the ones that
    were on the lines when it was suspended, and `held_at` says how old they are."""
    t = _held_terminal(store_code, register_number, authorization, org_id)
    try:
        rows = (sb().schema("pos").table(_HELD_TABLE).select("*")
                .eq("org_id", org_id).eq("store_code", t["store_code"])
                .eq("register_number", t["register_number"]).limit(1).execute().data) or []
    except Exception as e:
        return _held_unavailable(e)
    held = _held.restore(rows[0] if rows else None)
    return {"held": held, "available": True, "message": _held.sentence(held)}


@router.put("/held-sale")
def put_held_sale(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Suspend (or re-suspend) this register's cart. Body: {store_code, register_number?, cart: [],
    customer_id?, is_activation_sale?, note?}.

    An EMPTY cart with nothing attached CLEARS the hold rather than storing a row — otherwise every
    visit to an idle register would leave a suspended sale behind. `held_by` is the caller's own
    employee id, never a body field (the same anti-spoofing stance as the drawer sessions)."""
    body = body or {}
    t = _held_terminal(body.get("store_code"), body.get("register_number"), authorization, org_id)
    try:
        cart = _held.clean_cart(body.get("cart"))
        ctx = _held.clean_context(body)
        if _held.is_empty(cart, ctx):
            return _delete_held(t, org_id)
        row = _held.hold_row(t["store_code"], t["register_number"], cart, body,
                             held_by=(_caller_employee(authorization, org_id) or None),
                             now=_utcnow())
    except _held.HeldSaleError as e:
        raise HTTPException(400, str(e)) from None
    client = sb()
    try:
        existing = (client.schema("pos").table(_HELD_TABLE).select("id")
                    .eq("org_id", org_id).eq("store_code", t["store_code"])
                    .eq("register_number", t["register_number"]).limit(1).execute().data) or []
        if existing:
            # held_at is the moment the sale was FIRST suspended — an autosave keeps it, so the age
            # shown to the next cashier is the age of the sale and not of the last keystroke.
            (client.schema("pos").table(_HELD_TABLE)
             .update({k: v for k, v in row.items() if k != "held_at"})
             .eq("org_id", org_id).eq("id", existing[0]["id"]).execute())
        else:
            client.schema("pos").table(_HELD_TABLE).insert({**row, "org_id": org_id}).execute()
    except Exception as e:
        return _held_unavailable(e)
    stored = _held.restore({**row, "held_at": row.get("held_at")})
    return {"held": stored, "available": True, "message": _held.sentence(stored)}


def _delete_held(t, org_id):
    try:
        (sb().schema("pos").table(_HELD_TABLE).delete()
         .eq("org_id", org_id).eq("store_code", t["store_code"])
         .eq("register_number", t["register_number"]).execute())
    except Exception as e:
        return _held_unavailable(e)
    return {"held": None, "available": True, "message": "No suspended sale on this register."}


@router.delete("/held-sale")
def delete_held_sale(store_code: str, register_number: int = 1,
                     authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Drop this register's suspended cart — the cashier discarded it, or the sale checked out."""
    t = _held_terminal(store_code, register_number, authorization, org_id)
    return _delete_held(t, org_id)


# ── POS settings (config kv) ───────────────────────────────────────────────────────────────────────
# Raw rows out; the frontend registry (lib/pos-config.ts) resolves store override → org default →
# code default and renders the inheritance badges.
@router.get("/settings")
def list_settings(store_code: str = "", org_id: str = ORG_ID):
    """Every POS setting row for this org (and this store), with the bank numbers MASKED.

    This endpoint is the register's own config read, so any signed-in POS user reaches it and every
    POS screen receives what it answers. The two pay-by-bank settings (§POS-06) are the only values
    here that must not travel in full: `receipt_remit.KEYS` names them, `receipt_remit.mask` shows
    their last four, and the full numbers are read only by the receipt builder, server-side. The
    editor for them is `PUT /pos/receipt-remit`, which takes the whole number — a masked value is
    never accepted as an update, so the mask cannot be saved back over the real one.
    """
    q = (sb().schema("pos").table("pos_settings").select("store_code,key,value")
         .eq("org_id", org_id))
    if store_code:
        # THE STORE CODE IS CHECKED BEFORE IT IS INTERPOLATED, through the ONE home for that
        # decision (security pass 2026-10-30, index §POS-17) — this endpoint built the same
        # f-string filter from the same raw query parameter, and any signed-in POS user reaches it.
        #
        # FAIL-SAFE MEANS NARROW HERE, and that differs from `_pos_settings_rows` on purpose: that
        # caller hands its rows to `pos_config_effective`, which matches the store in Python, so
        # dropping the filter changes nothing. THIS one returns the rows to the browser, so dropping
        # the filter would hand back every store's rows. A refused code therefore reads the org
        # defaults ALONE.
        store_filter = _safe_settings_store_filter(store_code)
        q = q.or_(store_filter) if store_filter else q.is_("store_code", "null")
    rows = q.limit(1000).execute().data or []
    out = []
    for row in rows:
        if (row or {}).get("key") in _remit.KEYS:
            row = dict(row)
            row["value"] = _remit.mask(_remit.open_setting_value(row.get("value")))
        out.append(row)
    return {"settings": out}


def upsert_pos_setting(client, org_id: str, key: str, value, store: str = None) -> dict:
    """THE one writer of a pos.pos_settings row (org default when `store` is None, else the store
    override). Factored out of the PUT below (2026-09-22) so the POS wizard's own rules — which landed
    sources carry a tenant's plan names (core/plan_sources, key `plan_sources`) — are saved through the
    same path as every other POS setting, never a second insert/update of this table."""
    q = (client.schema("pos").table("pos_settings")
         .update({"value": value, "updated_at": "now()"})
         .eq("org_id", org_id).eq("key", key))
    q = q.eq("store_code", store) if store else q.is_("store_code", "null")
    r = q.execute()
    if not r.data:
        r = client.schema("pos").table("pos_settings").insert(
            {"org_id": org_id, "store_code": store, "key": key, "value": value}).execute()
    return (r.data or [{}])[0]


@router.put("/settings")
def upsert_setting(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    key = (body.get("key") or "").strip()
    if not key or "value" not in body:
        raise HTTPException(400, "key and value required")
    store = (body.get("store_code") or "").strip() or None
    return {"setting": upsert_pos_setting(sb(), org_id, key, body["value"], store)}


@router.put("/receipt-remit")
def upsert_receipt_remit(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Declare (or clear) the account a customer is told to pay into — the two boxes in the register
    setup (owner 2026-10-21, §POS-06).

    Why these two settings have an endpoint of their own rather than riding `PUT /pos/settings`:
    they are the only POS settings stored ENCRYPTED, they are the only ones validated before they are
    stored, and `GET /pos/settings` hands them back masked — so a generic save would write the mask
    over the real number the first time anybody pressed Save on that screen. The value still lands
    through `upsert_pos_setting`, the one writer of this table; this adds no second insert.

    Gated on `pos_settings`, like every other setting write. That gate is the point as much as the
    storage is: a cashier who could type a routing number onto a customer's receipt could redirect
    that customer's payment, and a receipt is a document people trust.

    Body `{routing_number?, account_number?, store_code?}`. A blank value CLEARS that number (and a
    store with only one of the two prints neither). Answers the masked view, never the numbers.
    """
    _require_pos_perm(authorization, org_id, "pos_settings")
    store = (body.get("store_code") or "").strip() or None
    try:
        pairs = []
        if "routing_number" in body:
            pairs.append((_remit.ROUTING_KEY, _remit.clean_routing(body.get("routing_number"))))
        if "account_number" in body:
            pairs.append((_remit.ACCOUNT_KEY, _remit.clean_account(body.get("account_number"))))
    except _remit.RemitError as e:
        raise HTTPException(400, str(e))
    if not pairs:
        raise HTTPException(400, "routing_number or account_number required")
    client = sb()
    for key, cleaned in pairs:
        upsert_pos_setting(client, org_id, key, _remit.seal_setting_value(cleaned), store)
    rows = _pos_settings_rows(client, org_id, store or "")
    from app.core import crypto
    return {"remit": _remit.masked_view(rows, store or ""),
            # So the screen can say plainly that the numbers are NOT sealed on a server with no key
            # configured, rather than implying a protection that is not there.
            "encryption_enabled": crypto.is_enabled()}


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
    """The org's default template, created on first read (parity with the standalone app's per-org
    seeded row) — plus what the ADMIN screen needs to edit it honestly (§POS-02): the two layouts it
    may choose with the tenant's own POS named in the second, a sentence when the stored choice is
    not what will print, and the company name a receipt and its emailed copy come FROM.
    """
    client = sb()
    rows = (client.schema("pos").table("receipt_templates").select("*")
            .eq("org_id", org_id).eq("is_default", True).limit(1).execute().data) or []
    if rows:
        template = rows[0]
    else:
        r = client.schema("pos").table("receipt_templates").insert(
            {"org_id": org_id, "name": "Default", "is_default": True}).execute()
        template = (r.data or [{}])[0]
    fmt = _declared_format(client, org_id)
    tenant_name = _tenant.display_name(client, org_id)
    brand = _poscfg.brand(_pos_settings_rows(client, org_id), "")
    stored_layout = template.get("layout")
    return {
        "template": template,
        "layout_options": _sr.layout_options(fmt["label"]),
        "layout": _sr.clean_layout(stored_layout, format_available=fmt["available"]),
        "layout_note": _sr.layout_note(stored_layout, format_available=fmt["available"]),
        "pos_format": {"declared": fmt["pos"], "label": fmt["label"],
                       "reason": fmt["reason"]},
        # What a customer sees a receipt come from — shown on the screen so the admin can tell
        # whether onboarding named the business, rather than discovering it from a sent email.
        "from_name": _sr.company_name(brand, tenant_name),
        "from_name_source": ("receipt branding" if (brand.get("company_name") or "").strip()
                             else ("onboarding" if tenant_name else "not set")),
        "layout_column_ready": "layout" in (template or {}),
    }


@router.patch("/receipt-template/{template_id}")
def update_receipt_template(template_id: str, body: dict,
                            authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    upd = {k: body[k] for k in RECEIPT_TEMPLATE_FIELDS if k in body}
    if not upd:
        raise HTTPException(400, "nothing to update")
    if "footer_text" in upd and upd["footer_text"] is None:
        upd["footer_text"] = ""   # column is NOT NULL; a cleared footer means empty
    if "layout" in upd:
        # Only the two declared layouts are storable, so a typo cannot leave a tenant printing
        # something nobody chose (§POS-02 — the renderer falls back loudly, not silently).
        want = str(upd["layout"] or "").strip().lower() or _sr.LAYOUT_THERMAL
        if want not in _sr.LAYOUTS:
            raise HTTPException(400, f"'{upd['layout']}' is not a receipt layout "
                                     f"(choose one of: {', '.join(_sr.LAYOUTS)})")
        upd["layout"] = want
    upd["updated_at"] = "now()"
    try:
        r = (sb().schema("pos").table("receipt_templates").update(upd)
             .eq("org_id", org_id).eq("id", template_id).execute())
    except Exception as e:
        # ADAPTIVE: everything but the layout choice works before migration 1089 is applied, and the
        # screen is told exactly that instead of showing a failed save it cannot explain.
        if any(f in upd for f in RECEIPT_TEMPLATE_MIG_1089):
            raise HTTPException(400, "the receipt layout choice needs migration 1089 "
                                     f"(1089_pos_receipt_layout.sql) applied first — {str(e)[:160]}")
        raise HTTPException(400, f"could not save the template: {str(e)[:200]}")
    if not r.data:
        raise HTTPException(404, "not found")
    return {"template": r.data[0]}


# ── THE RECEIPT OF A SALE: one home for print, reprint and email (index §POS-02) ──────────────────
# Owner 2026-10-17: "admin should be able to edit the receipt, teh template for the receipt for vzone
# ahoul be same as uplaoded from rq, the email option should show which email to send to and it should
# be editable, the from email address should show the company name Vzone Wireless or whatever name is
# set up during onboarding".
#
# The HTML is built by `pos/sale_receipt.py` (PURE) and nowhere else — the register prints what this
# returns instead of assembling its own copy in the browser, which is what let an admin's template
# edits apply to one receipt and not the other. The format path hands the same structured Document an
# IMPORTED receipt of that POS is parsed into to the same renderer (`receipt_formats.render`), so a
# tenant printing "in its POS's layout" prints the layout its own uploads are reprinted in.
from app.modules.pos import sale_receipt as _sr
from app.modules.pos import pos_config_effective as _poscfg
from app.modules.pos import tender_vocabulary as _tender
from app.modules.pos import wholesale_w9 as _w9
from app.modules.pos import receipt_escpos as _escpos
from app.modules.pos import receipt_remit as _remit
from app.modules.pos import customer_bill_to as _bill_to
from app.modules.pos import receipt_pdf_render as _rpdf_out
# §POS-14 — the Document DRAWN, injected the same way `render_words` is, so the PDF
# module keeps deciding nothing about content.
from app.modules.pos.receipt_formats import render_pdf as _rpdf_doc
from app.modules.pos import receipt_store_block as _shop
from app.modules.notify import whatsapp_window as _wa_window
from app.modules.core import tenant_identity as _tenant


#: What a store code may contain before it is allowed into a PostgREST filter STRING.
#:
#: WHY A CHARSET AND NOT AN ESCAPE. `.eq()` is parameterised by supabase-py, but `.or_()` takes a
#: fragment of PostgREST's own filter GRAMMAR, in which `,` separates disjuncts and `.` separates
#: column / operator / value. There is nothing to escape with — a value carrying either character is
#: not a store code, it is a second filter. So the value is CHECKED, and refused if it is not one.
#:
#: Verified non-breaking against production 2026-10-30: all 51 `storeops.stores` codes use only
#: letters, digits, spaces and hyphens. `/`, `&`, `'` and `_` are admitted as plausible future store
#: names; `.`, `,`, `(`, `)`, `:` and `"` are exactly the grammar characters and are not.
_SETTINGS_STORE_OK = _re.compile(r"[A-Za-z0-9 _/&'-]+")


def _safe_settings_store_filter(store_code: str):
    """The `or` filter for this store code, or **None** when the value may not be interpolated.

    None means "read the org's rows without a store filter", which is the FAIL-SAFE answer and not a
    degraded one: `pos_config_effective.effective` already matches the store override by exact string
    in Python, so an org-wide read resolves to the same value. Refusing therefore narrows what is
    returned, never widens it.
    """
    code = (store_code or "").strip()
    if not code or not _SETTINGS_STORE_OK.fullmatch(code):
        return None
    return f"store_code.is.null,store_code.eq.{code}"


def _pos_settings_rows(client, org_id: str, store_code: str = ""):
    """The POS config rows this org (and optionally this store) has — read ONCE per receipt and
    resolved through `pos_config_effective`, which is the server's one home for the store-override →
    org-default → declared-default rule the browser applies.

    THE STORE CODE IS CHECKED BEFORE IT IS INTERPOLATED (security pass 2026-10-30, index §POS-17).
    It used to go straight into the `or` filter as an f-string. Every caller then passed a value read
    from a database row or sat behind the `pos_settings` grant, so nothing user-supplied reached it —
    and `GET /pos/reports/tender-mismatch` was the first path that would have. Fixed HERE rather than
    at that one caller, because the class is "a store code is interpolated into filter grammar" and
    the next caller to pass a query parameter must not have to know that. Pinned by
    `harness_pos_sale_settlement.py` §L.
    """
    try:
        q = (client.schema("pos").table("pos_settings").select("store_code,key,value")
             .eq("org_id", org_id))
        store_filter = _safe_settings_store_filter(store_code)
        if store_filter:
            q = q.or_(store_filter)
        return q.limit(1000).execute().data or []
    except Exception:
        return []


def _receipt_template_row(client, org_id: str) -> dict:
    try:
        rows = (client.schema("pos").table("receipt_templates").select("*")
                .eq("org_id", org_id).eq("is_default", True).limit(1).execute().data) or []
    except Exception:
        rows = []
    return rows[0] if rows else {}


def _declared_format(client, org_id: str) -> dict:
    """What POS this tenant declared and what its receipt format is called — read from the ONE home,
    `sales_from_reports.declared_format` (`DECLARED_FORMAT_KEYS`), never unpacked here.

    This router used to derive it three times and in two different shapes; the print path kept the
    MODULE and the admin screen kept the registry ROW, and the day a `.get("label")` was copied from
    one onto the other every receipt of the one tenant with a declared POS crashed. Nothing in this
    module reads `resolve_format` any more, so the two cannot drift apart again (pinned by
    harness_pos_receipt_render.py §M)."""
    from app.modules.pos import sales_from_reports as _sfr_local
    return _sfr_local.declared_format(client, org_id)


def _receipt_bundle(client, org_id: str, sale: dict, *, typed_email=None,
                    want_layout=None, want_paper=None, want_destination=None,
                    want_remit=None, want_pdf=False, typed_phone=None) -> dict:
    """Everything a receipt needs, read once: the template, the effective POS config, the store
    block, the customer, the tenant's name and the declared format. One reader, so the printed copy
    and the emailed copy can never be built from different inputs.

    `want_layout` / `want_paper` are what the print screen asked for FOR THIS PRINT ONLY (owner
    2026-10-19, §POS-03): nothing is stored, and they are resolved through the same
    `sale_receipt.print_choice` the store's and the org's own settings go through, so a request
    cannot reach a layout or a paper width a setting could not.

    `want_destination` is where this copy GOES (owner 2026-10-20, §POS-04): the operating system's
    print dialog, as every store has always had, or the till printer itself through the helper on the
    counter PC. A store's own setting is only the DEFAULT; both are offered on every print.

    `typed_phone` is the number a cashier typed for a WhatsApp copy (owner 2026-10-21, §POS-09),
    shown before anything is sent exactly as the email address is.

    `want_pdf` builds the receipt as a FILE as well (owner 2026-10-21, §POS-08) — the same two
    content homes, a third medium. Only a path that is going to ATTACH one asks for it, because a
    print screen has no use for a file and building one costs a page render.

    `want_remit` is the pay-by-bank tick (owner 2026-10-21, §POS-06) — print the account this store
    told the customer to pay into. Off unless this print asks for it, and it cannot be turned on at
    all unless the register setup declares BOTH numbers.
    """
    store_code = (sale.get("store_code") or "").strip()
    rows = _pos_settings_rows(client, org_id, store_code)
    template_row = _receipt_template_row(client, org_id)
    template = _sr.template_view(template_row)
    brand = _poscfg.brand(rows, store_code)
    tenant_name = _tenant.display_name(client, org_id)
    customer = {}
    if sale.get("customer_id"):
        try:
            crows = (client.schema("pos").table("customers")
                     .select("id,first_name,last_name,company_name,email,phone_primary,"
                             + _bill_to.COLUMNS)
                     .eq("org_id", org_id).eq("id", sale["customer_id"]).limit(1).execute().data) or []
            customer = crows[0] if crows else {}
        except Exception:
            customer = {}
    # This store's own row, read through the columns that table ACTUALLY has (`_shop.STORE_COLUMNS`,
    # §POS-10). It used to ask for a `name` column `storeops.stores` does not have: PostgREST
    # answered 42703, the `except` below swallowed it, and every receipt printed in the tenant's own
    # POS layout went out with no address on it. The row is the FALLBACK — the receipt-branding rows
    # win — so a read that fails again costs the receipt nothing the admin has filled in.
    store_row, store_name = {}, store_code
    try:
        srows = (client.schema("storeops").table("stores").select(_shop.STORE_COLUMNS)
                 .eq("org_id", org_id).eq("store_code", store_code).limit(1).execute().data) or []
        store_row = srows[0] if srows else {}
    except Exception:
        store_row = {}
    fmt = _declared_format(client, org_id)
    snap = _sr.snapshot_view(sale.get("receipt"), sale)
    printer = _poscfg.printer(rows, store_code)
    remit = _remit.declared(rows, store_code)
    choice = _sr.print_choice(template, want_layout=want_layout, want_paper=want_paper,
                              store_layout=printer["layout"], store_paper=printer["paper_width_mm"],
                              format_available=fmt["available"],
                              want_destination=want_destination, store_direct=printer["direct"],
                              want_remit=want_remit, remit_available=remit["available"])
    template = _sr.with_choice(template, choice)
    settings_text = {k: _poscfg.text(rows, k, store_code)
                     for k in ("email_receipt_subject", "email_receipt_body")}
    # Named once: the browser's copy and the printer's byte stream are handed the SAME inputs, or
    # the cut slip could say something the paper one does not.
    blocks = _poscfg.disclaimers(rows, store_code)
    fold_discount = _poscfg.flag(rows, "receipt_coupon_in_retail_price", store_code)
    employee_name = (sale.get("employee_id") or "") if template["show_employee"] else ""
    # The one paragraph, built once. Every medium below is handed THIS string, so the paper copy, the
    # emailed copy, the printer's byte stream and the tenant's own POS layout cannot word it or
    # number it differently.
    remit_block = _remit.block(remit, include=choice["remit"])
    # The customer's own address, written out once (§POS-07). Owner 2026-10-21: the receipt shows the
    # complete address this platform holds for them. Blank for a walk-in with no record, and the
    # wording is `customer_bill_to`'s, never this reader's.
    customer_lines = _bill_to.lines(customer)
    html = _sr.html(
        snap, template,
        render_document=_rrender.render_html, fmt=fmt["module"],
        brand=brand, disclaimer_blocks=blocks,
        store_name=store_name, store_row=store_row,
        fold_discount=fold_discount,
        tenant_name=tenant_name,
        employee_name=employee_name,
        short_customer_name=_poscfg.flag(rows, "receipt_customer_short_name", store_code),
        remit_block=remit_block,
        customer_lines=customer_lines,
    )
    # The same receipt as a FILE (§POS-08), from the same inputs and the same layout rule — built
    # only when something is going to attach it. A file that cannot be built never costs the customer
    # their receipt: what can still be sent is sent, and the answer says the file is missing.
    pdf_bytes, pdf_error = b"", ""
    if want_pdf:
        try:
            pdf_bytes = _rpdf_out.receipt_pdf(
                snap, template, fmt=fmt["module"], render_words=_rrender.render_words,
                render_pdf=_rpdf_doc.render_pdf,
                brand=brand, disclaimer_blocks=blocks,
                store_name=store_name, store_row=store_row,
                fold_discount=fold_discount, tenant_name=tenant_name,
                employee_name=employee_name,
                short_customer_name=_poscfg.flag(rows, "receipt_customer_short_name", store_code),
                remit_block=remit_block, customer_lines=customer_lines)
        except Exception as exc:
            pdf_bytes, pdf_error = b"", f"{type(exc).__name__}: {str(exc)[:160]}"
    return {
        "html": html,
        "pdf_bytes": pdf_bytes,
        "pdf_error": pdf_error,
        "pdf_filename": _rpdf_out.filename(snap["transaction_id"]),
        "layout": choice["layout"],
        "layout_note": choice["layout_note"],
        "layout_source": choice["layout_source"],
        "paper_width_mm": choice["paper_width_mm"],
        "paper_source": choice["paper_source"],
        "layout_options": _sr.layout_options(fmt["label"]),
        "paper_options": _sr.paper_options(),
        "printer_label": printer["label"],
        "destination": choice["destination"],
        "destination_source": choice["destination_source"],
        "destination_options": _sr.destination_options(printer["label"],
                                                       helper_ready=bool(printer["helper_url"])),
        "helper_url": printer["helper_url"],
        # THE TICK AND WHAT IT WOULD PRINT (§POS-06). The masked numbers, never the full ones: these
        # fields exist so the print screen can say WHICH account the customer will be told to pay
        # into before anybody prints it. The full numbers reach the browser only inside `html` /
        # `escpos_base64`, and only when this print actually asked for the block.
        "remit": choice["remit"],
        "remit_source": choice["remit_source"],
        "remit_available": remit["available"],
        "remit_routing_masked": _remit.mask(remit["routing"]),
        "remit_account_masked": _remit.mask(remit["account"]),
        # The printer's own byte stream, built ONLY when this copy is going to the printer — the
        # register is its courier, not its reader, and a dialog print has no use for it. Both media
        # render `sale_receipt.slip_model`, so the cut slip and the paper copy cannot differ.
        "escpos_base64": (_escpos.payload_base64(
            _sr.slip_model(snap, template, brand=brand, disclaimer_blocks=blocks,
                           store_name=store_name, fold_discount=fold_discount,
                           tenant_name=tenant_name, employee_name=employee_name,
                           remit_block=remit_block, customer_lines=customer_lines))
            if choice["destination"] == _sr.DEST_DIRECT else ""),
        "email": _sr.email_view(snap, brand, template, settings_text, customer=customer,
                                typed=typed_email, tenant_name=tenant_name),
        # Where this receipt BILLS TO, and — for the cashier, never for the receipt — whether the
        # record holds an address at all (§POS-11). The owner asked for the address on the Bill To;
        # it prints whenever the record has one, and 1 of 1,636 records on the reporting tenant did.
        # A renderer cannot print a fact nobody collected, so the screen that can collect it says so.
        "bill_to": _bill_to.view(customer),
        # The SAME question for the other channel (§POS-09), answered by the same one home: who
        # would this receipt go to, whose number is it, and can it be sent at all. What counts as a
        # phone number is WhatsApp's own rule, dereferenced — never a second normalisation here.
        "whatsapp": _sr.whatsapp_view(snap, brand, template, settings_text, customer=customer,
                                      typed=typed_phone, tenant_name=tenant_name,
                                      digits=_wa_window.digits),
        "snapshot": snap,
        "tenant_name": tenant_name,
        "company_name": _sr.company_name(brand, tenant_name),
    }


def _load_sale(client, org_id: str, sale_id: str) -> dict:
    rows = (client.schema("pos").table("sales").select("*")
            .eq("org_id", org_id).eq("id", sale_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "sale not found")
    return rows[0]


#: The sale the admin screen previews a template against. Not a tenant's data and not a real sale —
#: a fixed set of lines chosen to exercise every toggle the editor offers (a serialised device, a
#: discounted accessory, tax, change), so what the admin sees is this renderer's own output and not a
#: hand-drawn imitation of it. RULE TWO: no carrier, tenant or store name appears in it.
PREVIEW_SNAPSHOT = {
    "transactionId": 4821,
    "date": "08/06/2026 2:41 PM",
    "customerName": "John Smith",
    "items": [
        {"description": "Smartphone 128GB", "serial": "358291470012345", "qty": 1,
         "unit_price": 999.00, "discount": 50.00, "line_total": 949.00},
        {"description": "Clear case", "serial": "", "qty": 1,
         "unit_price": 19.99, "discount": 0, "line_total": 19.99},
    ],
    "subtotal": 968.99, "discountTotal": 50.00, "taxTotal": 86.00, "total": 1054.99,
    "method": "card", "tendered": 1054.99, "change": 0,
}


#: The sample customer the preview's bill-to block is drawn from — a RECORD, read through
#: `customer_bill_to` like a real one, so the admin sees the block their toggle actually produces.
#: RULE TWO: an invented street, no tenant's own.
PREVIEW_CUSTOMER = {"address_1": "42 Example Avenue", "address_2": "Apt 3B",
                    "city": "Anytown", "state": "NY", "zip": "11219"}

#: The sample STORE the preview's header block is drawn from — a row shaped like `storeops.stores`
#: (its real columns, §POS-10), so the admin sees the block their branding actually produces and the
#: preview exercises the same fallback a shop with no branding address gets. RULE TWO: invented.
PREVIEW_STORE = {"store_code": "STORE 1", "address": "1 Example Street", "market": "NY",
                 "phone": "(555) 010-0100"}


@router.post("/receipt-template/preview")
def preview_receipt_template(body: dict = None, org_id: str = ORG_ID):
    """What a receipt would look like with the template the admin is EDITING — rendered by the same
    one home that prints the real thing (§POS-02), against a fixed sample sale.

    The screen used to draw its own imitation of a till slip, with a store's name hard-coded into it;
    an imitation cannot tell an admin whether their change worked. Nothing is written here.
    """
    body = body or {}
    client = sb()
    draft = dict(_receipt_template_row(client, org_id))
    draft.update({k: v for k, v in (body.get("template") or {}).items()
                  if k in RECEIPT_TEMPLATE_FIELDS})
    template = _sr.template_view(draft)
    rows = _pos_settings_rows(client, org_id)
    brand = _poscfg.brand(rows, "")
    tenant_name = _tenant.display_name(client, org_id)
    fmt = _declared_format(client, org_id)
    snap = _sr.snapshot_view(PREVIEW_SNAPSHOT)
    html = _sr.html(
        snap, template, render_document=_rrender.render_html, fmt=fmt["module"], brand=brand,
        disclaimer_blocks=_poscfg.disclaimers(rows, ""),
        store_name="Your store", store_row=PREVIEW_STORE,
        fold_discount=_poscfg.flag(rows, "receipt_coupon_in_retail_price", ""),
        tenant_name=tenant_name,
        employee_name="A. Rep" if template["show_employee"] else "",
        short_customer_name=_poscfg.flag(rows, "receipt_customer_short_name", ""),
        customer_lines=_bill_to.lines(PREVIEW_CUSTOMER),
    )
    return {"html": html, "sample": True,
            "layout": _sr.clean_layout(template["layout"], format_available=fmt["available"]),
            "layout_note": _sr.layout_note(template["layout"], format_available=fmt["available"])}


#: What the receipt response carries. Named once so GET and the re-render answer the same shape.
_RECEIPT_KEYS = ("html", "layout", "layout_note", "layout_source", "paper_width_mm", "paper_source",
                 "layout_options", "paper_options", "printer_label", "destination",
                 "destination_source", "destination_options", "helper_url", "escpos_base64",
                 "remit", "remit_source", "remit_available", "remit_routing_masked",
                 "remit_account_masked", "email", "bill_to", "company_name")


@router.get("/sales/{sale_id}/receipt")
def sale_receipt(sale_id: str, org_id: str = ORG_ID, layout: str = "", paper_width_mm: str = "",
                 destination: str = "", remit: str = ""):
    """THE receipt of this sale as print-ready HTML, plus what the print and email screen must show:
    the formats and paper widths this store can print, which one this copy used and why, the printer
    the store declared, and the address the email would go to. The register prints this; it builds no
    receipt of its own.

    `layout` / `paper_width_mm` are FOR THIS PRINT ONLY (owner 2026-10-19, §POS-03) — a counter with
    a thermal roll and an office printer asks for each in turn without changing any setting. Omitted,
    the answer is the store's own declaration, else the company template.
    """
    client = sb()
    sale = _load_sale(client, org_id, sale_id)
    try:
        bundle = _receipt_bundle(client, org_id, sale, want_layout=layout, want_paper=paper_width_mm,
                                 want_destination=destination, want_remit=remit)
    except _sr.ReceiptError as e:
        raise HTTPException(400, str(e))
    return {"receipt": {k: bundle[k] for k in _RECEIPT_KEYS}, "sale_id": sale_id}


@router.post("/sales/{sale_id}/email-receipt")
async def email_sale_receipt(sale_id: str, body: dict = None,
                             authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Email this sale's receipt. `to` is the address the cashier SAW and could edit; blank falls back
    to the customer's own email, and neither is invented — a receipt with no address to send to is
    refused and says so rather than disappearing.

    The From line carries the tenant's own company name (the receipt-branding override, else the name
    set up at onboarding), through the one home for it — never the platform's name on a store's mail.
    """
    body = body or {}
    client = sb()
    sale = _load_sale(client, org_id, sale_id)
    try:
        bundle = _receipt_bundle(client, org_id, sale, want_pdf=True, typed_email=body.get("to"),
                                 want_layout=body.get("layout"),
                                 want_paper=body.get("paper_width_mm"),
                                 want_remit=body.get("remit"))
    except _sr.ReceiptError as e:
        raise HTTPException(400, str(e))
    view = bundle["email"]
    if not view["to"]:
        raise HTTPException(400, "no email address — type one, or add one to the customer's profile")
    if not view["valid"]:
        raise HTTPException(400, f"'{view['to']}' does not look like an email address")
    from app.modules.notify.channels import email_resend as _mail
    if not _mail.is_configured():
        raise HTTPException(503, "emailing receipts isn't switched on for this platform yet "
                                 "(no mail provider configured)")
    html = _sr.body_html(view["body_declared"], bundle["company_name"]) + bundle["html"]
    # The receipt rides BOTH ways (owner 2026-10-21, §POS-08): in the body, where the customer reads
    # it without opening anything, and as a file they can keep, forward or print. A file that could
    # not be built does not hold up the mail — it is reported instead.
    attachments = ([(bundle["pdf_filename"], bundle["pdf_bytes"], "application/pdf")]
                   if bundle["pdf_bytes"] else [])
    try:
        mid = await _mail.send_email(view["to"], view["subject"], html, attachments,
                                     from_name=bundle["company_name"] or None)
    except Exception as e:
        raise HTTPException(502, f"the mail provider refused it: {str(e)[:200]}")
    msg = f"Receipt emailed to {view['to']}."
    if not attachments:
        msg += " The PDF copy could not be built, so it went in the body only."
    return {"sent": True, "to": view["to"], "from_name": view["from_name"],
            "subject": view["subject"], "provider_message_id": mid or None,
            "attached": bool(attachments), "attachment_name": bundle["pdf_filename"],
            "attachment_error": bundle["pdf_error"] or None,
            "message": msg}


@router.post("/sales/{sale_id}/whatsapp-receipt")
async def whatsapp_sale_receipt(sale_id: str, body: dict = None,
                                authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Send this sale's receipt to the customer over WhatsApp (owner 2026-10-21, §POS-09).

    THE SAME RECEIPT, a different channel. The file is the §POS-08 PDF — built from the same two
    content homes the paper copy and the mail are built from — and the line that rides with it is the
    same one the email subject carries, so a customer who gets both does not get two receipts.

    `to` is the number the cashier SAW and could edit; blank falls back to the number on the
    customer's record, and neither is invented. Delivery walks the channel's own ladder
    (`whatsapp_meta.send_document_detailed`: the approved document template, else a free-form
    document inside Meta's 24-hour window), because WHEN a business may send a file is Meta's rule
    and it already has one home here.
    """
    body = body or {}
    client = sb()
    sale = _load_sale(client, org_id, sale_id)
    try:
        bundle = _receipt_bundle(client, org_id, sale, want_pdf=True,
                                 typed_phone=body.get("to"),
                                 want_layout=body.get("layout"),
                                 want_paper=body.get("paper_width_mm"),
                                 want_remit=body.get("remit"))
    except _sr.ReceiptError as e:
        raise HTTPException(400, str(e))
    view = bundle["whatsapp"]
    if not view["to"]:
        raise HTTPException(400, "no phone number — type one, or add one to the customer's profile")
    if not view["valid"]:
        raise HTTPException(400, f"'{view['to']}' does not look like a phone number with its "
                                 "country code")
    from app.modules.notify.channels import whatsapp_meta as _wa
    if not _wa.is_configured():
        raise HTTPException(503, "sending receipts on WhatsApp isn't switched on for this platform "
                                 "yet (no WhatsApp sender configured)")
    # A receipt is worth nothing without the receipt: if the file could not be built, the customer is
    # not sent a bare line claiming one is attached.
    if not bundle["pdf_bytes"]:
        raise HTTPException(503, "the receipt file could not be built, so there is nothing to send "
                                 f"on WhatsApp{(' — ' + bundle['pdf_error']) if bundle['pdf_error'] else ''}")
    try:
        res = await _wa.send_document_detailed(view["to"], bundle["pdf_bytes"], "application/pdf",
                                               bundle["pdf_filename"], view["caption"])
    except Exception as e:
        raise HTTPException(502, f"WhatsApp refused it: {str(e)[:200]}")
    return {"sent": bool((res or {}).get("message_id")), "to": view["to"],
            "from_name": view["from_name"], "caption": view["caption"],
            "attachment_name": bundle["pdf_filename"],
            "attached": bool((res or {}).get("attached")),
            "route": (res or {}).get("route") or "",
            "provider_message_id": (res or {}).get("message_id") or None,
            "message": (f"Receipt sent to {view['to']} on WhatsApp."
                        if (res or {}).get("attached")
                        else f"WhatsApp would not carry the file to {view['to']} right now, so the "
                             "message went without it — the customer can be emailed the PDF.")}


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
    # THE SIBLING of the checkout gate (index §POS-14). An activation is the carrier leg of the
    # same sale and carries the same store_code into the commission feed, so it answered the same
    # question — "which store is this for?" — with the same absence of an answer. Same one home,
    # same sentence, same canonical spelling.
    _aspan = _working_span(authorization, org_id)
    _arefusal = _wstore.sale_refusal(_aspan, ins.get("store_code"))
    if _arefusal:
        raise HTTPException(403, _arefusal)
    ins["store_code"] = _wstore.resolve(_aspan, ins.get("store_code"))["store_code"]
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
            # PAGED, NOT limit(50000) (2026-09-08). The single capped read silently dropped the
            # tail: live Cellfonz raw_mi is 234,724 rows and the first 50,000 of them contain 26 of
            # the tenant's 28 Salesforce IDs, so two doors were missing from every sync with no
            # error anywhere. A dealer code that never gets imported is an activation that never
            # gets paid — exactly what this task's own `why` warns about — so this reads to the end.
            rows, start, page = [], 0, 10000
            while True:
                batch = (client.schema("commcalc").table(tbl).select(sel)
                         .eq("org_id", org_id).range(start, start + page - 1).execute().data) or []
                rows.extend(batch)
                if len(batch) < page:
                    break
                start += page
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


@router.get("/reports/tender-mismatch")
def report_tender_mismatch(date_from: str = "", date_to: str = "", store_code: str = "",
                           employee_id: str = "", kind: str = "mismatch",
                           authorization: str = Header(default=""), org_id: str = ORG_ID):
    """THE LOG of the cash / credit mismatch (§POS-17) — owner ask 2026-10-30.

    DERIVED LIVE over `pos.sales` and `pos.sale_payments` rather than read from the filed rows
    alone, and that is the design point, not an implementation detail: an edit is only ONE of the
    two writes that can move the tender and the total apart. `pos.void_sale` (mig 725) is the other,
    and it files nothing. Deriving the comparison here means the void sibling is covered by the same
    one home, on the same screen, without reopening a function that moves money correctly today.

    The filed row (mig 1108) is joined on where it exists, because it carries what the till SAW at
    the moment of the edit and who has settled it since — facts no live derivation can recover.

    NOT MEASURABLE IS NOT A FINDING. 4,603 of the 4,605 live sales are imported receipt history with
    no payment row at all (measured 2026-10-30). Those are counted in `summary` and named in
    `blockers`; they are not listed as mismatches, and `kind=unmeasured` is how somebody who wants
    to see them asks. A report that listed them by default would be 4,603 accusations.
    """
    client = sb()
    # THE STORE ASKED FOR IS CHECKED AGAINST THE CALLER'S SPAN BEFORE IT IS USED, the same answer
    # `adjust_inventory` gives. Span-filtering the RESULT is not enough on its own here: the store
    # code also resolves this report's band, so an unchecked value would reach a config read for a
    # store the caller may not see.
    keyset = _caller_store_keyset(authorization, org_id)
    if store_code and not _span_filter([{"store_code": store_code}], keyset):
        raise HTTPException(403, "that store is outside your reporting span")
    q = (client.schema("pos").table("sales")
         .select("id,transaction_id,created_at,total,balance,status,voided_at,revision,"
                 "amended_at,amended_by,customer_id,employee_id,store_code")
         .eq("org_id", org_id))
    if date_from:
        q = q.gte("created_at", _date_bound(date_from, end=False))
    if date_to:
        q = q.lte("created_at", _date_bound(date_to, end=True))
    if store_code:
        q = q.eq("store_code", store_code)
    if employee_id:
        q = q.eq("employee_id", employee_id)
    sales = q.order("created_at", desc=True).limit(1000).execute().data or []
    sales = _span_filter(sales, keyset)
    if not sales:
        return {"rows": [], "summary": _settle.summarise([]), "blockers": [],
                "log_available": _tender_log_available(client, org_id)}

    ids = [s["id"] for s in sales]
    pays: dict = {}
    for chunk in (ids[i:i + 100] for i in range(0, len(ids), 100)):
        for row in ((client.schema("pos").table("sale_payments")
                     .select("sale_id,payment_method,amount,card_last_four")
                     .eq("org_id", org_id).in_("sale_id", chunk).limit(1000).execute().data) or []):
            pays.setdefault(row["sale_id"], []).append(row)

    filed: dict = {}
    log_available = _tender_log_available(client, org_id)
    if log_available:
        for chunk in (ids[i:i + 100] for i in range(0, len(ids), 100)):
            for row in ((client.schema("pos").table("sale_amendments")
                         .select("sale_id,revision,tender_variance,tender_verdict,"
                                 "tender_direction,tender_measured,tender_reason,settled_at,"
                                 "settled_by,settled_method,settled_amount,created_at")
                         .eq("org_id", org_id).in_("sale_id", chunk)
                         .order("revision", desc=True).limit(1000).execute().data) or []):
                filed.setdefault(row["sale_id"], row)   # the newest revision, ordered above

    # The band is the ORG's, read once for the whole report — never per row, so one store's
    # declaration cannot silently score another store's sale on this screen.
    band = _tender_band(client, org_id, store_code)
    emp = _employee_names(org_id)
    cust = _customer_names(org_id, [s.get("customer_id") for s in sales])
    assessed = []
    for s in sales:
        out = _settle.outstanding(s, pays.get(s["id"], []), band=band)
        f = filed.get(s["id"]) or {}
        assessed.append({
            "sale_id": s["id"], "transaction_id": s.get("transaction_id"),
            "created_at": s.get("created_at"), "store_code": s.get("store_code"),
            "employee_id": s.get("employee_id"),
            "employee_name": emp.get((s.get("employee_id") or "").strip()),
            "customer_name": (cust.get(s.get("customer_id")) or {}).get("name"),
            "status": s.get("status"), "revision": _amend.current_revision(s),
            "was_edited": bool(s.get("revision") and int(s["revision"] or 1) > 1),
            "was_voided": str(s.get("status") or "").lower() == "voided" or bool(s.get("voided_at")),
            "total": out["expected"]["total"], "balance": out["expected"]["balance"],
            "expected": out["expected"]["amount"], "tendered": out["tendered"]["total"],
            "methods": ", ".join(out["tendered"]["methods"]),
            "variance": out["variance"], "verdict": out["verdict"],
            "direction": out["direction"], "measured": out["measured"],
            "reason": out["reason_label"],
            "mismatch": out["mismatch"],
            "attributed_to": (out["attribution"]["method"]
                              or ("more than one tender" if out["attribution"]["ambiguous"] else None)),
            # Only a FILED row can say this, which is why it is joined rather than derived.
            "settled_at": f.get("settled_at"), "settled_by": f.get("settled_by"),
            "settled_method": f.get("settled_method"), "settled_amount": f.get("settled_amount"),
            "logged": bool(f) and f.get("tender_measured") is not None,
        })

    summary = _settle.summarise(assessed)
    if kind == "unmeasured":
        rows = [r for r in assessed if not r["measured"]]
    elif kind == "collect":
        rows = [r for r in assessed if r["direction"] == _settle.COLLECT]
    elif kind == "refund":
        rows = [r for r in assessed if r["direction"] == _settle.REFUND]
    elif kind == "all":
        rows = assessed
    else:
        rows = [r for r in assessed if r["mismatch"]]
    return {"rows": rows, "summary": summary, "blockers": _settle.blockers(assessed),
            "band": band, "log_available": log_available, "kind": kind}


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
            p = seal_customer_pin(_clean_customer(row))
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
                                 "activations, tax codes)")

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


# ── Receipt import (secondary-POS: photograph a primary-system receipt → a sale) ──────────────────
# See app/modules/pos/receipt_import.py + migration 864. Router-level `_require_pos_access` already
# gates these to a signed-in org member; the sale is attributed to the uploader (never a body field).
from app.modules.pos import receipt_import as _receipt


def _decode_image(image_b64: str) -> tuple[bytes, str]:
    """Accept a bare base64 string or a data URL ('data:image/png;base64,....'). Returns (bytes, ext)."""
    import base64 as _b64
    s = image_b64 or ""
    ext = "jpg"
    if s.startswith("data:"):
        header, _, s = s.partition(",")
        if "png" in header:
            ext = "png"
    try:
        return _b64.b64decode(s), ext
    except Exception:
        raise HTTPException(400, "image must be base64-encoded")


@router.post("/receipt-import")
def receipt_import_create(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Photograph → OCR → (optionally) create a sale. Body: {image (base64 or data URL), ext?,
    store_code?, notes?, dry_run?}. dry_run=true returns the parsed preview WITHOUT writing, so the
    UI can show the fields for confirmation first; omit / false to actually create the sale."""
    if not (body.get("image") or "").strip():
        raise HTTPException(400, "image is required")
    raw, ext = _decode_image(body["image"])
    parsed, raw_ocr = _receipt.ocr_receipt(raw, body.get("ext") or ext)

    if body.get("dry_run"):
        return {"dry_run": True, "parsed": parsed, "raw_ocr": raw_ocr}

    # Nothing usable came back (no vision key, unreadable photo) → don't fabricate a sale; hand the
    # preview back so the client can fall to manual entry.
    if not (parsed.get("items") or parsed.get("total") or parsed.get("customer_name")):
        return {"imported": False, "parsed": parsed, "raw_ocr": raw_ocr,
                "message": "Couldn't read the receipt automatically — enter it manually."}

    uploader = _caller_employee(authorization, org_id) or None
    result = _receipt.import_receipt(
        sb(), org_id=org_id, store_code=(body.get("store_code") or "").strip() or None,
        uploaded_by=uploader, parsed=parsed, raw_ocr=raw_ocr,
        notes=(body.get("notes") or "").strip() or None)
    return {"imported": True, "parsed": parsed, **result}


@router.get("/receipt-imports")
def receipt_imports_list(q: str = "", imei: str = "", phone: str = "", customer: str = "",
                         store_code: str = "", limit: int = 50, org_id: str = ORG_ID):
    """Search imported receipts by IMEI / phone / customer name (or a free-text q across all three
    + device). Powers the 'find that imported sale' lookup on both the app and desktop.

    Name / phone / device are plaintext, so they search by ILIKE (unchanged). The IMEI is encrypted at
    rest, so it searches against its keyed-HMAC BLIND-INDEX column (imei_bidx, exact match); when no
    key is configured (or for legacy plaintext rows) we fall back to ILIKE on the imei column."""
    import re as _re
    from app.core import crypto

    keyed = crypto.is_enabled()

    def _imei_token(v: str):
        return crypto.blind_index(v, mode="digits") if keyed else None

    query = (sb().schema("pos").table("receipt_imports")
             .select("id,store_code,sale_id,customer_id,status,imei,phone,customer_name,"
                     "device_name,total,sale_date,notes,created_at,imei_bidx")
             .eq("org_id", org_id))
    if store_code.strip():
        query = query.eq("store_code", store_code.strip())

    if imei.strip():
        tok = _imei_token(imei)
        query = query.eq("imei_bidx", tok) if tok else query.ilike("imei", f"%{imei.strip()}%")
    if phone.strip():
        digits = _re.sub(r"\D", "", phone)
        query = query.ilike("phone", f"%{digits}%")
    if customer.strip():
        query = query.ilike("customer_name", f"%{customer.strip()}%")
    if q.strip():
        s = q.strip().replace(",", " ")
        # Free text matches the plaintext columns; if it's a full IMEI, also match the blind index
        # (the imei column itself is ciphertext, so an ILIKE on it can't hit).
        ors = [f"phone.ilike.%{s}%", f"customer_name.ilike.%{s}%", f"device_name.ilike.%{s}%"]
        digits = _re.sub(r"\D", "", s)
        itok = _imei_token(digits) if 14 <= len(digits) <= 16 else None
        ors.append(f"imei_bidx.eq.{itok}" if itok else f"imei.ilike.%{s}%")
        query = query.or_(",".join(ors))

    rows = query.order("created_at", desc=True).limit(min(max(limit, 1), 200)).execute().data or []
    return {"receipt_imports": [_receipt.decrypt_receipt_row(r) for r in rows]}


@router.get("/receipt-imports/{import_id}")
def receipt_imports_get(import_id: str, org_id: str = ORG_ID):
    rows = (sb().schema("pos").table("receipt_imports").select("*")
            .eq("org_id", org_id).eq("id", import_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "receipt import not found")
    return {"receipt_import": _receipt.decrypt_receipt_row(rows[0])}


# ── Structured, per-POS receipt import (migration 866) — editable + reprintable ───────────────────
# Upload a PDF from a KNOWN POS format (RQ / B2B / …), parse it to one editable Document, edit the
# description/qty/tax/price, and reprint it later in the SAME layout. Formats live in
# app/modules/pos/receipt_formats; nothing about a layout is hardcoded here.
from app.modules.pos import receipt_pdf as _rpdf
from app.modules.pos.receipt_formats import registry as _rfmt, render as _rrender


@router.get("/receipt-import/formats")
def receipt_import_formats(org_id: str = ORG_ID):
    """The POS formats the upload picker offers + this tenant's remembered default (asked on upload:
    'which POS are you uploading from — RQ / B2B?')."""
    default = None
    try:
        rows = (sb().schema("pos").table("receipt_import_prefs").select("default_source")
                .eq("org_id", org_id).limit(1).execute().data) or []
        default = rows[0].get("default_source") if rows else None
    except Exception:
        pass
    return {"formats": _rfmt.list_formats(), "default_source": default}


@router.post("/receipt-import/structured")
def receipt_import_structured(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Parse a PDF receipt of a chosen POS format into an editable Document. Body: {file|image (PDF
    base64 or data URL), pos_source ('rq'|'b2b'|…), store_code?, notes?, dry_run?}. dry_run=true
    returns the Document WITHOUT writing (for the confirm/edit screen); else stores it + a summary sale."""
    src = (body.get("pos_source") or "").strip().lower()
    if not _rfmt.get(src):
        raise HTTPException(400, f"unknown pos_source; choose one of {[f['source'] for f in _rfmt.list_formats()]}")

    # Commit can carry the EDITED document from the preview (description/qty/tax/price already changed),
    # so the user's edits are saved in one call without re-parsing the PDF. dry_run always parses fresh.
    edited = body.get("document") if isinstance(body.get("document"), dict) and body.get("document") else None
    if edited and not body.get("dry_run"):
        doc = edited
    else:
        raw, _ext = _decode_image(body.get("file") or body.get("image") or "")
        if not _rpdf.is_pdf(raw):
            raise HTTPException(400, "a PDF receipt is required for structured import")
        words = _rpdf.extract_pages_words(raw)
        if not words:
            raise HTTPException(422, "could not read text from this PDF")
        doc = _rfmt.parse(src, words)
        try:  # remember the tenant's POS choice so the picker pre-selects it next time
            sb().schema("pos").table("receipt_import_prefs").upsert(
                {"org_id": org_id, "default_source": src}, on_conflict="org_id").execute()
        except Exception:
            pass
    # keep derived fields in sync with any edits before storage
    if edited and not body.get("dry_run"):
        from app.modules.pos.receipt_formats import base as _rbase
        doc["derived"] = _rbase.compute_derived(doc)
    if body.get("dry_run"):
        return {"dry_run": True, "pos_source": src, "document": doc}
    uploader = _caller_employee(authorization, org_id) or None
    result = _receipt.import_structured(
        sb(), org_id=org_id, pos_source=src, document=doc, uploaded_by=uploader,
        store_code=(body.get("store_code") or "").strip() or None,
        notes=(body.get("notes") or "").strip() or None)
    return {"imported": True, "pos_source": src, "document": doc, **result}


@router.patch("/receipt-imports/{import_id}/document")
def receipt_import_update_document(import_id: str, body: dict, org_id: str = ORG_ID):
    """Save edits to a structured receipt's Document (description / qty / tax / price). Recomputes the
    search + summary fields so an edit stays findable and the totals track it."""
    doc = body.get("document")
    if not isinstance(doc, dict):
        raise HTTPException(400, "document (object) is required")
    updated = _receipt.update_structured_document(sb(), org_id, import_id, doc)
    return {"ok": True, "document": updated}


@router.get("/receipt-imports/{import_id}/print")
def receipt_import_print(import_id: str, editable: bool = False, org_id: str = ORG_ID):
    """Reprint a stored receipt in its ORIGINAL layout — HTML, ready for the browser's Print → PDF.
    editable=true returns the same markup with contenteditable hooks for an in-place editor."""
    from fastapi.responses import HTMLResponse
    rows = (sb().schema("pos").table("receipt_imports").select("document")
            .eq("org_id", org_id).eq("id", import_id).limit(1).execute().data) or []
    if not rows or not rows[0].get("document"):
        raise HTTPException(404, "no printable document for this import")
    return HTMLResponse(content=_rrender.render_html(rows[0]["document"], editable=bool(editable)))


# ── Sales REBUILT from the landed sales reports (owner 2026-09-21; index §30.14) ───────────────────
# The two report kinds the intake / Upload page already land (the invoice-level export with its tender
# columns, the line-level export) are combined per invoice number into ONE structured receipt document,
# imported through the same importer a scanned receipt uses, and reprinted through the format registered
# for the tenant's DECLARED POS. The rebuild also runs on every intake commit of either kind; this
# endpoint is the same consumer offered for a period ("Rebuild sales from the landed reports").
from app.modules.pos import sales_from_reports as _sfr
from app.modules.pos import vendor_paid_lines as _vpl


@router.post("/sales-from-reports/rebuild")
def sales_from_reports_rebuild(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Body: {from: 'YYYY-MM-DD', to: 'YYYY-MM-DD', stores?: [store strings], dry_run?}. Every invoice of
    the slice present in the invoice landing becomes a POS sale (lines from the line landing by invoice
    number; a header with no lines still becomes a sale and says so). Keyed org × invoice #: re-running
    replaces, never duplicates. Answers the counts, the ties in words and the per-invoice reports."""
    lo, hi = (body.get("from") or "").strip()[:10], (body.get("to") or "").strip()[:10]
    if not (lo and hi) or lo > hi:
        raise HTTPException(400, "from and to (YYYY-MM-DD, from ≤ to) are required")
    stores = body.get("stores") if isinstance(body.get("stores"), list) and body.get("stores") else None
    who = _caller_employee(authorization, org_id) or None
    return _sfr.rebuild_or_start(sb(), org_id, lo, hi, stores=stores, who=who, dry_run=bool(body.get("dry_run")))


@router.get("/sales-from-reports/job")
def sales_from_reports_job(org_id: str = ORG_ID):
    """The background rebuild's state (running / done / failed, months done of total, the running counts,
    the words) — what the receipts page shows while a large rebuild runs. Read-only."""
    job = _sfr.load_job(sb(), org_id)
    return {"job": job, "running": _sfr.job_running(job)}


@router.get("/sales-from-reports")
def sales_from_reports_list(from_: str = Query("", alias="from"), to: str = "", org_id: str = ORG_ID):
    """The POS sales rebuilt from the reports in a date range (newest first): invoice #, date, store,
    customer, total, the payment lines, lines found, the tie words; each prints through
    GET /pos/receipt-imports/{id}/print (the one renderer) and opens for editing like any import."""
    fmt = _declared_format(sb(), org_id)
    return {"pos": fmt["pos"], "format_label": fmt["label"],
            "format_reason": fmt["reason"], "sales": _sfr.list_rebuilt(sb(), org_id, from_ or None, to or None)}


@router.get("/sales-from-reports/vendor-paid-lines")
def sales_from_reports_vendor_paid(from_: str = Query("", alias="from"), to: str = "", org_id: str = ORG_ID):
    """WHICH LINES THE VENDOR PAYS (owner 2026-09-24: "$0.00 like register"; index §30.14a): the saved rule
    (pos.pos_settings key `vendor_paid_lines`) with its proof over the period, and the PROPOSAL learned from
    the period's own lines — the category segments whose lines add up to each invoice's vendor-paid tenders.
    Read-only."""
    lo, hi = (from_ or "").strip()[:10], (to or "").strip()[:10]
    if not (lo and hi) or lo > hi:
        raise HTTPException(400, "from and to (YYYY-MM-DD, from ≤ to) are required")
    return _sfr.vendor_paid_evidence(sb(), org_id, lo, hi)


@router.put("/sales-from-reports/vendor-paid-lines")
def sales_from_reports_vendor_paid_save(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Body: {from, to, tokens: [category segments]}. Saves the rule the person confirmed — refused (400, the
    sentences) when a segment also names goods with a cost, or when the rule ties no more invoices than no
    rule over the period. Written through `upsert_pos_setting` (the one writer) and READ BACK. An empty
    list clears the rule (every line prints its amount again)."""
    _require_pos_perm(authorization, org_id, "pos_settings")
    lo, hi = (body.get("from") or "").strip()[:10], (body.get("to") or "").strip()[:10]
    toks = body.get("tokens")
    if not isinstance(toks, list):
        raise HTTPException(400, "tokens (a list of category words) is required")
    toks = _vpl.resolve_config({"tokens": toks})["tokens"]
    if toks:
        if not (lo and hi) or lo > hi:
            raise HTTPException(400, "from and to (YYYY-MM-DD, from ≤ to) are required to prove the rule")
        ev = _sfr.vendor_paid_evidence(sb(), org_id, lo, hi, tokens=toks)
        if ev["refusals"]:
            raise HTTPException(400, " ".join(ev["refusals"]))
    from datetime import datetime as _dt
    who = _caller_employee(authorization, org_id) or None
    upsert_pos_setting(sb(), org_id, _vpl.CONFIG_KEY,
                       {"tokens": toks, "confirmed_by": who, "confirmed_at": _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")})
    back = _sfr.load_vendor_paid_rule(sb(), org_id)
    if back["tokens"] != toks:
        raise HTTPException(500, "the rule did not read back as saved — nothing changed on the receipts; try again")
    return {"saved": back, "words": (f"saved: {len(toks)} word(s) — rebuild the period so the receipts print those lines at $0.00"
                                     if toks else "cleared: every line prints its amount again — rebuild the period")}


# ── Vendor rebate / activation report (carrier commission + rebate history XLSX) ───────────────────
# Upload the carrier's rebate/commission export → classify each line (device rebate vs commission),
# compute device gross profit (Unit Rebate − Related Cost), and summarize per store/period. This
# PREVIEW endpoint is READ-ONLY: it writes nothing, so the numbers can be verified before any
# customer/activation/P&L write is enabled. See app/modules/pos/vendor_rebate_report.py.
from app.modules.pos import vendor_rebate_report as _vrr


@router.post("/activation-report/preview")
def activation_report_preview(body: dict, org_id: str = ORG_ID):
    """Parse a vendor rebate/commission history workbook and return the classification + totals +
    per-store/period P&L summary WITHOUT writing anything. Body: {file (base64 .xlsx or data URL)}."""
    raw, _ext = _decode_image(body.get("file") or body.get("image") or "")
    if not _vrr.is_xlsx(raw):
        raise HTTPException(400, "an .xlsx vendor rebate report is required")
    rows = _vrr.read_xlsx(raw)
    if not rows:
        raise HTTPException(422, "could not read rows from this workbook")
    res = _vrr.normalize_report(rows)
    # cap the row-level sample so the preview payload stays small; totals/summary cover everything
    sample = res["activations"][:25]
    return {
        "totals": res["totals"],
        "families": res["families"],
        "summary_by_store_period": res["summary_by_store_period"],
        "sample": sample,
    }


@router.post("/activation-report/import")
def activation_report_import(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Import a vendor rebate/commission report: create customers (deduped by phone) + activations
    (deduped by cell/date) and UPSERT the P&L ledger (commission → carrier_comm, device rebate →
    device_rebate contra-COGS). Body: {file (base64 .xlsx), store_code?, dry_run?}. dry_run=true
    returns the same preview WITHOUT writing — the confirm step before anything lands."""
    raw, _ext = _decode_image(body.get("file") or body.get("image") or "")
    if not _vrr.is_xlsx(raw):
        raise HTTPException(400, "an .xlsx vendor rebate report is required")
    rows = _vrr.read_xlsx(raw)
    if not rows:
        raise HTTPException(422, "could not read rows from this workbook")
    res = _vrr.normalize_report(rows)
    if body.get("dry_run"):
        return {"dry_run": True, "totals": res["totals"], "families": res["families"],
                "summary_by_store_period": res["summary_by_store_period"], "sample": res["activations"][:25]}
    uploader = _caller_employee(authorization, org_id) or None
    result = _vrr.import_report(sb(), org_id, res,
                                store_code=(body.get("store_code") or "").strip() or None,
                                uploaded_by=uploader)
    return {"imported": True, **result}
