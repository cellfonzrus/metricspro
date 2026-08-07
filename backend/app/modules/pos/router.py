"""POS module — Phase 0: product catalog. Phase 1: customers (+ encrypted PII), inventory,
sales/checkout (atomic pos.checkout RPC), register drawer sessions, POS config kv, tax codes,
receipt templates (mig 724 + 725).

The POS-inside-MetricsPro port (see pos-system INTEGRATION_PLAN.md). Identity comes from the
platform: employees are storeops.employees (TEXT employee_id business key), stores are
storeops.stores.store_code, RBAC is the roles JSONB `modules.pos` key. This router owns the
pos.* schema. Later phases add activations, vendors/POs, transfers, reports, import.

Gated actions (PII reveal, void, settings/tax writes) use fine-grained keys in the caller role's
permissions JSONB — `pos_view_pii`, `pos_void`, `pos_settings` — with role scope 'all' (org-wide
admin) implying all three, so existing admin roles work before the roles UI grows checkboxes.
"""
from fastapi import APIRouter, Header, HTTPException

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


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# Phase 1 — customers, inventory, sales/checkout, register, settings (mig 725)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════

CUSTOMER_FIELDS = ("account_type", "company_name", "first_name", "last_name", "middle_initial",
                   "dob", "driver_license_state", "primary_account_no", "password", "email",
                   "phone_primary", "phone_secondary", "address_1", "address_2", "city", "state",
                   "zip", "referral_source", "credit_limit", "accept_checks", "is_active")
SERIAL_FIELDS = ("product_id", "store_code", "serial_number", "imei", "sim_card", "color",
                 "storage", "condition", "status", "cost", "date_received", "po_number")
TAX_CODE_FIELDS = ("name", "rate", "store_code", "is_active")
RECEIPT_TEMPLATE_FIELDS = ("name", "header_text", "footer_text", "show_store_name",
                           "show_customer", "show_employee", "show_serials",
                           "show_tax_breakdown", "show_discounts", "paper_width_mm",
                           "font_size_px")


def _clean_customer(body: dict) -> dict:
    out = {k: body[k] for k in CUSTOMER_FIELDS if k in body}
    for k in ("dob", "company_name", "middle_initial", "driver_license_state",
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
    rows = (sb().table("app_users").select("employee_id")
            .eq("org_id", org_id).eq("auth_id", uid).limit(1).execute().data) or []
    return ((rows[0].get("employee_id") if rows else "") or "").strip()


def _caller_perms(authorization: str, org_id: str) -> dict:
    """roles.permissions jsonb for the caller's role ({} when unresolvable)."""
    from app.modules.core.router import _uid_from_token
    uid = _uid_from_token(authorization)
    if not uid:
        return {}
    rows = (sb().table("app_users").select("role")
            .eq("org_id", org_id).eq("auth_id", uid).limit(1).execute().data) or []
    role = ((rows[0].get("role") if rows else "") or "").strip()
    if not role:
        return {}
    rr = (sb().table("roles").select("permissions")
          .eq("org_id", org_id).eq("name", role).limit(1).execute().data) or []
    return (rr[0].get("permissions") or {}) if rr else {}


def _require_pos_perm(authorization: str, org_id: str, key: str):
    """403 unless the caller's role grants `key` (permissions jsonb) or has org-wide scope."""
    perms = _caller_perms(authorization, org_id)
    if perms.get(key) is True:
        return
    if (perms.get("scope") or "all") == "all":
        return
    raise HTTPException(403, f"your role does not allow this action ({key})")


# ── Customers ──────────────────────────────────────────────────────────────────────────────────────
@router.get("/customers")
def list_customers(search: str = "", active_only: bool = True, org_id: str = ORG_ID):
    q = sb().schema("pos").table("customers").select("*").eq("org_id", org_id)
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


# PII: ciphertext lives in pos.customers; all access via the mig-725 definer functions. Any
# signed-in employee may WRITE (front-desk data entry) and see last-4; full readback needs the
# pos_view_pii permission — same asymmetry as the standalone app (a compromised cashier session
# can overwrite one customer's SSN but cannot exfiltrate the customer book).
@router.get("/customers/{customer_id}/pii-last4")
def customer_pii_last4(customer_id: str, org_id: str = ORG_ID):
    rows = sb().schema("pos").rpc("customer_pii_last4",
                                  {"p_org": org_id, "p_customer": customer_id}).execute().data or []
    return rows[0] if rows else {"ssn_last4": None, "dl_last4": None}


@router.get("/customers/{customer_id}/pii")
def customer_pii_get(customer_id: str, authorization: str = Header(default=""),
                     org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_view_pii")
    rows = sb().schema("pos").rpc("customer_pii_get",
                                  {"p_org": org_id, "p_customer": customer_id}).execute().data or []
    return rows[0] if rows else {"ssn": None, "driver_license_num": None}


@router.post("/customers/{customer_id}/pii")
def customer_pii_set(customer_id: str, body: dict, org_id: str = ORG_ID):
    sb().schema("pos").rpc("customer_pii_set", {
        "p_org": org_id, "p_customer": customer_id,
        "p_ssn": body.get("ssn") or None,
        "p_driver_license": body.get("driver_license") or None,
    }).execute()
    return {"ok": True}


# ── Inventory ──────────────────────────────────────────────────────────────────────────────────────
@router.get("/inventory/serial")
def list_inventory_serial(search: str = "", store_code: str = "", status: str = "",
                          product_id: str = "", org_id: str = ORG_ID):
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
    prods = {p["id"]: p for p in (sb().schema("pos").table("products")
             .select("id,short_name,product_code").eq("org_id", org_id)
             .limit(2000).execute().data or [])}
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
def list_inventory_standard(store_code: str = "", org_id: str = ORG_ID):
    q = sb().schema("pos").table("inventory_standard").select("*").eq("org_id", org_id)
    if store_code:
        q = q.eq("store_code", store_code)
    rows = q.order("updated_at", desc=True).limit(1000).execute().data or []
    prods = {p["id"]: p for p in (sb().schema("pos").table("products")
             .select("id,short_name,product_code,retail_price").eq("org_id", org_id)
             .limit(2000).execute().data or [])}
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
    if "store_code" in upd and upd["store_code"] == "":
        upd["store_code"] = None
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


# ── Sales ──────────────────────────────────────────────────────────────────────────────────────────
@router.get("/sales")
def list_sales(date_from: str = "", date_to: str = "", store_code: str = "",
               employee_id: str = "", status: str = "", org_id: str = ORG_ID):
    q = sb().schema("pos").table("sales").select("*").eq("org_id", org_id)
    if date_from:
        q = q.gte("created_at", date_from)
    if date_to:
        q = q.lte("created_at", date_to)
    if store_code:
        q = q.eq("store_code", store_code)
    if employee_id:
        q = q.eq("employee_id", employee_id)
    if status:
        q = q.eq("status", status)
    rows = q.order("created_at", desc=True).limit(500).execute().data or []
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
    if eid:
        sale["employee_id"] = eid   # never trust a body-supplied rep on the money path
    elif not (sale.get("employee_id") or "").strip():
        raise HTTPException(403, "your login isn't linked to an employee record — "
                                 "ask an admin to set your Employee ID in Roles & Access")
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
