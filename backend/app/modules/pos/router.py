"""POS module — Phase 0: product catalog (products, departments, categories).

The POS-inside-MetricsPro port (see pos-system INTEGRATION_PLAN.md). Identity comes from the
platform: employees are storeops.employees (TEXT employee_id business key), stores are
storeops.stores.store_code, RBAC is the roles JSONB `modules.pos` key. This router owns the
pos.* schema (mig 724). Later phases add customers, inventory, sales/checkout, activations.
"""
from fastapi import APIRouter, HTTPException

from app.core.database import get_supabase

router = APIRouter(prefix="/pos", tags=["pos"])
ORG_ID = "00000000-0000-0000-0000-000000000001"

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
    ins["org_id"] = org_id
    r = sb().schema("pos").table("products").insert(ins).execute()
    return {"product": (r.data or [{}])[0]}


@router.patch("/products/{product_id}")
def update_product(product_id: str, body: dict, org_id: str = ORG_ID):
    upd = _clean(body)
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (sb().schema("pos").table("products").update(upd)
         .eq("org_id", org_id).eq("id", product_id).execute())
    if not r.data:
        raise HTTPException(404, "not found")
    return {"product": r.data[0]}
