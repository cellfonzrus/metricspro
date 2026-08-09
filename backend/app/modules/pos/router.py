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
    rows = (sb().schema("storeops").table("app_users").select("employee_id")
            .eq("org_id", org_id).eq("auth_id", uid).limit(1).execute().data) or []
    return ((rows[0].get("employee_id") if rows else "") or "").strip()


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
        sa = (sb().schema("storeops").table("app_users").select("super_admin")
              .eq("auth_id", uid).eq("super_admin", True).limit(1).execute().data) or []
        if not sa:
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
    """401 unless the caller is a signed-in member of this org; returns their auth uid."""
    from app.modules.core.router import _uid_from_token
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "sign in to perform this action")
    rows = (sb().schema("storeops").table("app_users").select("id")
            .eq("org_id", org_id).eq("auth_id", uid).limit(1).execute().data) or []
    if not rows:
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
# Explicit column lists, never select('*'). pos.customers carries two things that must not ride
# along on a bulk read: `password` — the carrier ACCOUNT PIN, i.e. the credential used for
# SIM-swap and account takeover — and the ssn/driver-licence CIPHERTEXT. select('*') handed all
# three back for up to 300 customers at a time, which contradicted this module's own threat model
# ("a compromised cashier session cannot exfiltrate the customer book"). The ciphertext columns are
# read by nothing — plaintext access goes through pos.customer_pii_get, which is separately gated
# on pos_view_pii — so they are dropped from both reads. `password` survives only on the
# single-record fetch, where the edit form genuinely needs it.
CUSTOMER_READ_COLS = (
    "id,org_id,cust_number,account_type,company_name,first_name,last_name,middle_initial,dob,"
    "driver_license_state,primary_account_no,email,phone_primary,phone_secondary,address_1,"
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


# PII: ciphertext lives in pos.customers; all access via the mig-725 definer functions. Any
# signed-in employee may WRITE (front-desk data entry) and see last-4; full readback needs the
# pos_view_pii permission — same asymmetry as the standalone app (a compromised cashier session
# can overwrite one customer's SSN but cannot exfiltrate the customer book).
@router.get("/customers/{customer_id}/pii-last4")
def customer_pii_last4(customer_id: str, authorization: str = Header(default=""),
                       org_id: str = ORG_ID):
    _require_member(authorization, org_id)
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
def customer_pii_set(customer_id: str, body: dict, authorization: str = Header(default=""),
                     org_id: str = ORG_ID):
    _require_member(authorization, org_id)
    # An ABSENT key must not erase a stored value: it maps to SQL NULL, which the RPC reads as
    # "leave this column alone". Only an explicitly supplied empty string clears a field. The
    # old `body.get(k) or None` collapsed omitted and cleared into the same NULL, so saving one
    # field destroyed the other's ciphertext irreversibly.
    def _pii_arg(key: str):
        v = body.get(key)
        return None if v is None else str(v)
    sb().schema("pos").rpc("customer_pii_set", {
        "p_org": org_id, "p_customer": customer_id,
        "p_ssn": _pii_arg("ssn"),
        "p_driver_license": _pii_arg("driver_license"),
    }).execute()
    return {"ok": True}


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
            payload = [{"org_id": org_id, "code": code, "carrier": c.get("name"),
                        "store_code": None, "is_active": True} for code, _nm in fresh]
            for i in range(0, len(payload), 500):
                client.schema("pos").table("dealer_codes").insert(payload[i:i + 500]).execute()
            existing |= {code.upper() for code, _ in fresh}
            inserted_total += len(fresh)
        out.append({"carrier": c.get("name"), "configured": True,
                    "label": c.get("dealer_code_label"), "source": f"{tbl}.{col}",
                    "found": len(seen), "new": len(fresh),
                    "sample": [code for code, _ in fresh[:5]]})
    return {"carriers": out, "inserted": inserted_total if commit else 0, "committed": commit}


@router.post("/dealer-codes")
def create_dealer_code(body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    code = (body.get("code") or "").strip()
    if not code:
        raise HTTPException(400, "code required")
    r = sb().schema("pos").table("dealer_codes").insert({
        "org_id": org_id, "code": code, "carrier": (body.get("carrier") or "").strip() or None,
        "store_code": (body.get("store_code") or "").strip() or None,
        "is_active": bool(body.get("is_active", True)),
    }).execute()
    return {"dealer_code": (r.data or [{}])[0]}


@router.patch("/dealer-codes/{code_id}")
def update_dealer_code(code_id: str, body: dict, authorization: str = Header(default=""),
                       org_id: str = ORG_ID):
    _require_pos_perm(authorization, org_id, "pos_settings")
    upd = {k: body[k] for k in ("code", "carrier", "store_code", "is_active") if k in body}
    _clean_nullable(upd, ("carrier", "store_code"))
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
    # Day/month boundaries in BUSINESS_TZ (America/New_York) like the commcalc feed — a 9pm ET
    # sale belongs to today's KPI, not tomorrow's UTC date.
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    from app.modules.pos.commcalc_feed import BUSINESS_TZ
    now_local = _dt.now(BUSINESS_TZ)
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

    else:
        raise HTTPException(400, f"unknown entity '{entity}' "
                                 "(importable: customers, products, vendors, inventory, activations)")

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
