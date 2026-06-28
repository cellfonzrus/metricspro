"""Core / RBAC router — role management + employee account provisioning.

Powers the Role Assignment module (migration 015). Roles live in storeops.roles with an
editable permissions JSONB; the logged-in identity lives in storeops.app_users (linked to a
Supabase Auth account). Everything is in the `storeops` schema because that schema is already
exposed to PostgREST (the supabase-py client talks to PostgREST, which only serves exposed
schemas — `core` is not exposed). Employees authenticate with email+password; this router
creates the auth accounts (service key) and assigns roles. The frontend never reads these
tables directly — it calls the token-verified /core/me — so the tables stay backend-only (RLS
with no anon policy; the service role bypasses RLS).
"""
import os
import re
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Header
from app.core.database import get_supabase, get_supabase_admin

router = APIRouter(prefix="/core", tags=["Core / RBAC"])
ORG_ID = "00000000-0000-0000-0000-000000000001"


def sb():
    return get_supabase()


# ── App config: the master "enforce login" switch ─────────────────────────────────────
@router.get("/auth-config")
async def get_auth_config():
    """PUBLIC: tells the frontend whether to enforce login. Default false (app open) so the
    deploy never locks anyone out; the admin flips it on once everyone is provisioned.
    Returns false if migration 015 hasn't run yet (table missing)."""
    try:
        rows = sb().schema("storeops").table("app_config").select("rbac_enabled") \
            .eq("id", 1).limit(1).execute().data or []
        return {"rbac_enabled": bool(rows[0]["rbac_enabled"]) if rows else False}
    except Exception:
        return {"rbac_enabled": False}


@router.put("/auth-config")
async def set_auth_config(body: dict, org_id: str = ORG_ID):
    """Flip login enforcement on/off (from the Roles admin). Once ON, every user must sign in."""
    enabled = bool(body.get("rbac_enabled"))
    sb().schema("storeops").table("app_config").upsert(
        {"id": 1, "org_id": org_id, "rbac_enabled": enabled,
         "updated_at": datetime.now(timezone.utc).isoformat()}, on_conflict="id").execute()
    return {"rbac_enabled": enabled}


# ── Portal reports: which reports are surfaced in the employee portal + to which roles (mig 052) ──
@router.get("/portal-reports")
async def get_portal_reports(org_id: str = ORG_ID):
    """Per-report portal config keyed by href: {href: {enabled, roles[], label, category}}. The
    Reports hub merges this over the report catalog; the portal/employee surfaces read it to gate
    what each role sees. Empty {} if migration 052 hasn't run."""
    try:
        rows = sb().schema("storeops").table("portal_reports").select("*").eq("org_id", org_id).execute().data or []
    except Exception:
        return {"config": {}}
    return {"config": {r["href"]: {"enabled": bool(r.get("enabled", True)), "roles": r.get("roles") or [],
                                   "label": r.get("label"), "category": r.get("category")} for r in rows if r.get("href")}}


@router.put("/portal-reports")
async def set_portal_report(body: dict, org_id: str = ORG_ID):
    """Upsert one report's portal config. Body: {href, enabled, roles[], label?, category?}."""
    href = (body.get("href") or "").strip()
    if not href:
        raise HTTPException(400, "href required")
    row = {"org_id": org_id, "href": href,
           "enabled": bool(body.get("enabled", True)),
           "roles": body.get("roles") or [],
           "label": body.get("label"), "category": body.get("category"),
           "updated_at": datetime.now(timezone.utc).isoformat()}
    try:
        sb().schema("storeops").table("portal_reports").upsert(row, on_conflict="org_id,href").execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 052 first: {e}")
    return {"ok": True, "href": href}


# ── Identity (token-verified "who am I") ───────────────────────────────────────────────
def _uid_from_token(authorization: str):
    """Validate the Supabase Auth JWT (server-side) and return its auth user id, or None."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        resp = get_supabase_admin().auth.get_user(token)
        user = getattr(resp, "user", None) or resp
        return getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    except Exception:
        return None


@router.get("/me")
async def whoami(authorization: str = Header(default="")):
    """The logged-in user's profile + resolved role permissions. Token-verified — the frontend
    sends the Supabase session access token as `Authorization: Bearer <token>`."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    client = sb()
    rows = client.schema("storeops").table("app_users").select("*").eq("auth_id", uid).limit(1).execute().data or []
    if not rows:
        return {"provisioned": False, "user": None, "permissions": {}}
    u = rows[0]
    perms = {}
    if u.get("role"):
        rr = client.schema("storeops").table("roles").select("display_name,permissions") \
            .eq("org_id", u.get("org_id") or ORG_ID).eq("name", u["role"]).limit(1).execute().data or []
        if rr:
            perms = rr[0].get("permissions") or {}
            u["role_display"] = rr[0].get("display_name")
    # best-effort last_login stamp
    try:
        client.schema("storeops").table("app_users").update(
            {"last_login": datetime.now(timezone.utc).isoformat()}).eq("id", u["id"]).execute()
    except Exception:
        pass
    return {"provisioned": True, "user": u, "permissions": perms,
            "active": bool(u.get("is_active", True))}


@router.post("/me/password-changed")
async def password_changed(authorization: str = Header(default="")):
    """Clear the must_reset_password flag after the user sets a new password."""
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    sb().schema("storeops").table("app_users").update({"must_reset_password": False}) \
        .eq("auth_id", uid).execute()
    return {"ok": True}


# ── Tenants (SaaS logins) — super-admin onboarding ────────────────────────────────────
# A tenant = an org_id + metadata. Super-admins create tenants and provision each tenant's first
# admin login; that admin then manages their own company's users. ADDITIVE — single-tenant app
# is unaffected (org_id-from-session is a later phase). Backend is the guard (super_admin gate).
def _app_user_from_token(authorization: str):
    uid = _uid_from_token(authorization)
    if not uid:
        return None
    rows = sb().schema("storeops").table("app_users").select("*").eq("auth_id", uid).limit(1).execute().data or []
    return rows[0] if rows else None


def _require_super_admin(authorization: str):
    """Super-admin = the super_admin flag, OR (bootstrap) a house-org admin — so the very first
    operator is never locked out before the flag is seeded."""
    u = _app_user_from_token(authorization)
    ok = u and (u.get("super_admin") or (u.get("org_id") == ORG_ID and u.get("role") == "admin"))
    if not ok:
        raise HTTPException(403, "super-admin only")
    return u


def _mods(**on):
    base = {k: False for k in ("commissions", "targets", "asset", "vip", "storeops", "notify", "helpdesk", "admin")}
    base.update(on)
    return base


# the role set seeded into every new tenant (mirror of migration 015 + helpdesk); the tenant admin
# edits them afterward on their own Roles & Access.
_BASE_ROLES = [
    ("admin", "Admin", {"modules": _mods(commissions=True, targets=True, asset=True, vip=True, storeops=True, notify=True, helpdesk=True, admin=True), "scope": "all", "home": "/commcalc"}),
    ("market_manager", "Market Manager", {"modules": _mods(commissions=True, targets=True, asset=True, vip=True, storeops=True, notify=True, helpdesk=True), "scope": "market", "home": "/commcalc/targets"}),
    ("store_manager", "Store Manager", {"modules": _mods(commissions=True, targets=True, asset=True, storeops=True, helpdesk=True), "scope": "store", "home": "/commcalc/targets"}),
    ("sales_rep", "Sales Rep", {"modules": _mods(targets=True, helpdesk=True), "scope": "self", "home": "/commcalc/targets/my"}),
]
_DEFAULT_TENANT_MODULES = ["commissions", "targets", "asset", "storeops", "closing", "notify", "helpdesk"]


@router.get("/tenants")
async def list_tenants(authorization: str = Header(default="")):
    _require_super_admin(authorization)
    client = sb()
    tens = client.schema("storeops").table("tenants").select("*").order("created_at").execute().data or []
    users = client.schema("storeops").table("app_users").select("org_id,auth_id").execute().data or []
    cnt: dict = {}
    for u in users:
        o = u.get("org_id")
        c = cnt.setdefault(o, {"users": 0, "logins": 0})
        c["users"] += 1
        if u.get("auth_id"):
            c["logins"] += 1
    for t in tens:
        t.update(cnt.get(t["org_id"], {"users": 0, "logins": 0}))
    return {"tenants": tens}


def _provision_tenant(client, name, admin_email, admin_name=None, password=None, slug=None, must_reset=True):
    """Create a tenant (org_id) + seed base roles + module entitlements + provision its first admin
    login. Shared by super-admin create-tenant AND self-serve signup. Returns the temp password only
    when one was auto-generated (super-admin flow), not when the caller chose it (signup)."""
    new_org = str(uuid.uuid4())
    slug = (slug or re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-"))[:60]
    client.schema("storeops").table("tenants").insert({"org_id": new_org, "name": name, "slug": slug}).execute()
    client.schema("storeops").table("roles").insert(
        [{"org_id": new_org, "name": n, "display_name": d, "permissions": p} for (n, d, p) in _BASE_ROLES]).execute()
    try:
        client.schema("storeops").table("tenant_modules").insert(
            [{"org_id": new_org, "module_key": m, "is_enabled": True} for m in _DEFAULT_TENANT_MODULES]).execute()
    except Exception:
        pass  # tenant_modules (mig 053) may be absent in some envs — non-fatal
    pw = password or ("Mp" + secrets.token_urlsafe(6))
    auth_id, created, err = _create_or_link_auth(get_supabase_admin(), admin_email, pw)
    client.schema("storeops").table("app_users").insert({
        "org_id": new_org, "auth_id": auth_id, "email": admin_email,
        "full_name": admin_name or f"{name} Admin", "role": "admin",
        "is_active": True, "must_reset_password": must_reset, "super_admin": False,
    }).execute()
    return {"org_id": new_org, "name": name, "admin_email": admin_email,
            "temp_password": (None if password else pw), "auth_created": created, "auth_error": err}


@router.post("/tenants")
async def create_tenant(body: dict, authorization: str = Header(default="")):
    """Super-admin: create a tenant + provision its first admin login (returns a temp password)."""
    _require_super_admin(authorization)
    name = (body.get("name") or "").strip()
    admin_email = (body.get("admin_email") or "").strip().lower()
    if not name or not admin_email:
        raise HTTPException(400, "name and admin_email required")
    return _provision_tenant(sb(), name, admin_email, body.get("admin_name"),
                             password=body.get("temp_password"), slug=body.get("slug"), must_reset=True)


def _signups_open() -> bool:
    return os.environ.get("SIGNUPS_OPEN", "").lower() in ("1", "true", "yes")


@router.get("/signup-status")
async def signup_status():
    """PUBLIC: whether self-serve signup is open (env SIGNUPS_OPEN). The /signup page reads this."""
    return {"open": _signups_open()}


@router.post("/signup")
async def signup(body: dict):
    """PUBLIC self-serve signup — GATED on env SIGNUPS_OPEN (default OFF). Creates a new company + its
    admin login with the chosen password. ⚠️ v1 auto-confirms the email — add real email verification
    + rate-limit/captcha before opening this to the public internet."""
    if not _signups_open():
        raise HTTPException(403, "signups are closed")
    name = (body.get("name") or "").strip()
    admin_email = (body.get("admin_email") or "").strip().lower()
    password = body.get("password") or ""
    if not name or not admin_email or len(password) < 8:
        raise HTTPException(400, "company name, email, and an 8+ character password are required")
    if "@" not in admin_email or "." not in admin_email.split("@")[-1]:
        raise HTTPException(400, "a valid email is required")
    client = sb()
    if client.schema("storeops").table("app_users").select("id").eq("email", admin_email).limit(1).execute().data:
        raise HTTPException(409, "an account with this email already exists")
    res = _provision_tenant(client, name, admin_email, body.get("admin_name"), password=password, must_reset=False)
    return {"org_id": res["org_id"], "name": name, "admin_email": admin_email,
            "message": "Company created — sign in with your email and password."}


@router.patch("/tenants/{org_id}")
async def update_tenant(org_id: str, body: dict, authorization: str = Header(default="")):
    _require_super_admin(authorization)
    upd = {}
    if "name" in body:
        upd["name"] = body["name"]
    if "is_active" in body:
        upd["is_active"] = bool(body["is_active"])
    if not upd:
        raise HTTPException(400, "nothing to update")
    sb().schema("storeops").table("tenants").update(upd).eq("org_id", org_id).execute()
    return {"ok": True}


# ── Roles ────────────────────────────────────────────────────────────────────────────
def _level_role_perms(rank: int) -> dict:
    """Starting permissions for an org-level-derived role, scaled by org depth. The admin then
    tunes them on the Role Permissions tab. RBAC is gated OFF until enforce-login is on, so these
    grant nothing until the admin both assigns the role AND turns enforcement on."""
    def M(**on):
        base = {k: False for k in ("commissions", "targets", "asset", "vip", "storeops", "notify", "admin")}
        base.update(on)
        return base
    if rank <= 1:    # Executive / Director — company-wide leadership
        return {"modules": M(commissions=True, targets=True, asset=True, vip=True, storeops=True, notify=True),
                "scope": "all", "home": "/commcalc"}
    if rank <= 3:    # Regional / District manager — market scope
        return {"modules": M(commissions=True, targets=True, asset=True, storeops=True, notify=True),
                "scope": "market", "home": "/commcalc/targets"}
    if rank == 4:    # Store manager — store scope
        return {"modules": M(commissions=True, targets=True, asset=True, storeops=True),
                "scope": "store", "home": "/commcalc/targets"}
    return {"modules": M(targets=True), "scope": "self", "home": "/commcalc/targets/my"}  # rep / consultant


def _ensure_roles_for_levels(client, org_id: str) -> None:
    """Make every org-chart level (Executive, Director, Regional/District Manager, …) an
    assignable, editable access role so it shows up in the Roles & Access dropdown. Idempotent:
    only INSERTS a role for a level that has no matching role name yet — never updates/clobbers an
    existing (possibly admin-edited) role. Levels that already match a seeded role (e.g. a level
    named to collide with 'store_manager') are left alone."""
    levels = (client.schema("storeops").table("org_levels")
              .select("name,rank").eq("org_id", org_id).execute().data or [])
    if not levels:
        return
    existing = {r["name"] for r in (client.schema("storeops").table("roles")
                .select("name").eq("org_id", org_id).execute().data or [])}
    new_rows, seen = [], set()
    for lv in levels:
        nm = re.sub(r"[^a-z0-9]+", "_", (lv.get("name") or "").lower()).strip("_")
        if not nm or nm in existing or nm in seen:
            continue
        seen.add(nm)
        new_rows.append({"org_id": org_id, "name": nm, "display_name": lv.get("name"),
                         "permissions": _level_role_perms(lv.get("rank") or 0)})
    if new_rows:
        client.schema("storeops").table("roles").insert(new_rows).execute()


@router.get("/roles")
async def list_roles(org_id: str = ORG_ID):
    client = sb()
    try:
        _ensure_roles_for_levels(client, org_id)   # org-chart levels become assignable roles
    except Exception:
        pass  # never block the roles list if the level→role sync fails
    rows = client.schema("storeops").table("roles").select("*").eq("org_id", org_id) \
        .order("id").execute().data or []
    return {"roles": rows}


@router.post("/roles")
async def create_role(body: dict, org_id: str = ORG_ID):
    name = (body.get("name") or "").strip().lower().replace(" ", "_")
    if not name:
        raise HTTPException(400, "name required")
    row = {"org_id": org_id, "name": name,
           "display_name": body.get("display_name") or name.replace("_", " ").title(),
           "permissions": body.get("permissions") or {}}
    res = sb().schema("storeops").table("roles").upsert(row, on_conflict="org_id,name").execute()
    return (res.data or [{}])[0]


@router.put("/roles/{role_id}")
async def update_role(role_id: int, body: dict):
    upd = {}
    if "display_name" in body:
        upd["display_name"] = body["display_name"]
    if "permissions" in body:
        upd["permissions"] = body["permissions"]
    if not upd:
        raise HTTPException(400, "nothing to update")
    res = sb().schema("storeops").table("roles").update(upd).eq("id", role_id).execute()
    if not res.data:
        raise HTTPException(404, "role not found")
    return res.data[0]


# ── Users (app accounts) + provisioning ────────────────────────────────────────────────
@router.get("/users")
async def list_users(org_id: str = ORG_ID):
    """All app users (core.users) with their role + login state. Degrades to [] if migration
    015 hasn't run (so the admin page doesn't hard-error out of order)."""
    try:
        rows = sb().schema("storeops").table("app_users").select("*") \
            .eq("org_id", org_id).order("full_name").execute().data or []
    except Exception:
        return {"users": [], "ready": False}
    for r in rows:
        r["has_login"] = bool(r.get("auth_id"))
    return {"users": rows, "ready": True}


def _ensure_employee(client, org_id, email, full_name=None, store_code=None, emp_emails=None):
    """Make sure a storeops.employees row exists for a person added via Roles, so they
    propagate to the Employee list and become editable there. No-op if one already exists
    (matched by email). Returns True if a new row was created."""
    email = (email or "").strip().lower()
    if not email:
        return False
    if emp_emails is None:
        existing = client.schema("storeops").table("employees").select("email") \
            .eq("org_id", org_id).execute().data or []
        emp_emails = {(e.get("email") or "").lower() for e in existing if e.get("email")}
    if email in emp_emails:
        return False
    try:
        client.schema("storeops").table("employees").insert({
            "org_id": org_id, "name": (full_name or email), "email": email,
            "home_store": store_code or None, "is_active": True,
        }).execute()
        emp_emails.add(email)
        return True
    except Exception:
        return False


@router.get("/employees")
async def list_employees(org_id: str = ORG_ID):
    """The storeops.employees roster + whether each already has an app login + assigned role.
    Drives the assignment grid (assign a role, then create logins)."""
    client = sb()
    emps = client.schema("storeops").table("employees").select(
        "id,employee_id,name,home_store,role,pay_rate,email,phone,is_active") \
        .eq("org_id", org_id).order("name").execute().data or []
    try:
        users = client.schema("storeops").table("app_users").select("*").eq("org_id", org_id) \
            .execute().data or []
    except Exception:
        users = []   # migration 015 not run yet → no assignments to merge

    # Backfill: any app_user with no matching employee gets a roster row, so manually-added
    # Roles users show up in the Employee list and are editable (not stuck as synthetic rows).
    emp_emails = {(e.get("email") or "").lower() for e in emps if e.get("email")}
    emp_eids = {e.get("employee_id") for e in emps if e.get("employee_id")}
    created = 0
    for u in users:
        ue = (u.get("email") or "").lower()
        if not ue or ue in emp_emails:
            continue
        if u.get("employee_id") and u.get("employee_id") in emp_eids:
            continue
        if _ensure_employee(client, org_id, ue, u.get("full_name"), u.get("store_code"), emp_emails):
            created += 1
    if created:
        emps = client.schema("storeops").table("employees").select(
            "id,employee_id,name,home_store,role,pay_rate,email,phone,is_active") \
            .eq("org_id", org_id).order("name").execute().data or []
    by_email = {(u.get("email") or "").lower(): u for u in users if u.get("email")}
    by_emp = {u.get("employee_id"): u for u in users if u.get("employee_id")}
    out = []
    matched = set()
    for e in emps:
        u = by_emp.get(e.get("employee_id")) or by_email.get((e.get("email") or "").lower())
        if u:
            matched.add(u.get("id"))
        out.append({
            **e,
            "app_role": (u or {}).get("role"),
            "has_login": bool((u or {}).get("auth_id")),
            "app_market": (u or {}).get("market"),
            "app_store": (u or {}).get("store_code"),
            "widget_overrides": (u or {}).get("widget_overrides"),
        })
    # Surface app_users that have no matching employee (manually added via "Add a person")
    # so they stay visible/manageable. Negative synthetic ids never collide with employee ids.
    syn = 0
    for u in users:
        if u.get("id") in matched:
            continue
        syn += 1
        out.append({
            "id": -syn,
            "employee_id": u.get("employee_id"),
            "name": u.get("full_name") or u.get("email"),
            "home_store": None, "role": None, "phone": None, "pay_rate": None,
            "email": u.get("email"),
            "is_active": u.get("is_active", True),
            "app_role": u.get("role"),
            "has_login": bool(u.get("auth_id")),
            "app_market": u.get("market"),
            "app_store": u.get("store_code"),
            "widget_overrides": u.get("widget_overrides"),
            "manual": True,
        })
    return {"employees": out, "with_email": sum(1 for e in emps if (e.get("email") or "").strip())}


@router.post("/users/assign")
async def assign_role(body: dict, org_id: str = ORG_ID):
    """Upsert a core.users row (assign role + scope). Keyed on (org_id, email). Does NOT create
    the auth login — call /users/create-login (or bulk-provision) for that."""
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    role = (body.get("role") or "").strip()
    client = sb()
    cur = client.schema("storeops").table("app_users").select("*").eq("org_id", org_id) \
        .eq("email", email).limit(1).execute().data or []
    row = {
        "org_id": org_id, "email": email,
        "full_name": body.get("full_name") or (cur[0].get("full_name") if cur else None),
        "role": role or (cur[0].get("role") if cur else "sales_rep"),
        "market": body.get("market"),
        "store_code": body.get("store_code"),
        "store_codes": body.get("store_codes"),
        "employee_id": body.get("employee_id") or (cur[0].get("employee_id") if cur else None),
        "is_active": body.get("is_active", True if not cur else cur[0].get("is_active")),
    }
    if cur:
        res = client.schema("storeops").table("app_users").update(row).eq("id", cur[0]["id"]).execute()
    else:
        res = client.schema("storeops").table("app_users").insert(row).execute()
    # Propagate to the Employee roster so the person is editable in the Employee list too.
    _ensure_employee(client, org_id, email, row["full_name"], row["store_code"])
    return (res.data or [{}])[0]


@router.post("/users/bulk-assign")
async def bulk_assign(body: dict, org_id: str = ORG_ID):
    """Bulk upsert app_users (assign roles) from a list — powers the employee-sheet upload and
    the multi-add form. Body: {users:[{email, full_name, role, market, store_code}]}. Does NOT
    create logins (call /users/bulk-provision or per-row create-login after). Role names are
    validated against storeops.roles; bad rows are reported, the rest still apply."""
    users = body.get("users")
    if not isinstance(users, list) or not users:
        raise HTTPException(400, "users[] required")
    client = sb()
    valid = {r["name"] for r in (client.schema("storeops").table("roles")
             .select("name").eq("org_id", org_id).execute().data or [])}
    _existing_emp = client.schema("storeops").table("employees").select("email") \
        .eq("org_id", org_id).execute().data or []
    emp_emails = {(e.get("email") or "").lower() for e in _existing_emp if e.get("email")}
    assigned, errors = 0, []
    for i, u in enumerate(users):
        email = (u.get("email") or "").strip().lower()
        role = (u.get("role") or "").strip()
        if not email or "@" not in email:
            errors.append({"row": i + 1, "email": email, "error": "missing/invalid email"})
            continue
        if role and role not in valid:
            errors.append({"row": i + 1, "email": email, "error": f"unknown role '{role}'"})
            continue
        row = {
            "org_id": org_id, "email": email,
            "full_name": (u.get("full_name") or None),
            "role": role or "sales_rep",
            "market": (u.get("market") or None),
            "store_code": (u.get("store_code") or None),
            "is_active": True,
        }
        try:
            cur = client.schema("storeops").table("app_users").select("id") \
                .eq("org_id", org_id).eq("email", email).limit(1).execute().data or []
            if cur:
                client.schema("storeops").table("app_users").update(row).eq("id", cur[0]["id"]).execute()
            else:
                client.schema("storeops").table("app_users").insert(row).execute()
            _ensure_employee(client, org_id, email, row["full_name"], row["store_code"], emp_emails)
            assigned += 1
        except Exception as e:
            errors.append({"row": i + 1, "email": email, "error": str(e)})
    return {"assigned": assigned, "errors": errors, "total": len(users)}


def _find_auth_user_by_email(admin, email):
    """Best-effort lookup of an existing Supabase Auth user id by email."""
    try:
        resp = admin.auth.admin.list_users()
        users = resp if isinstance(resp, list) else getattr(resp, "users", []) or []
        for u in users:
            ue = getattr(u, "email", None) or (u.get("email") if isinstance(u, dict) else None)
            if ue and ue.lower() == email.lower():
                return getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else None)
    except Exception:
        pass
    return None


def _create_or_link_auth(admin, email, temp_pw):
    """Create a Supabase Auth account (email confirmed) for `email`, or link the existing one.
    Returns (auth_id, created_bool, error_or_None)."""
    try:
        resp = admin.auth.admin.create_user({
            "email": email, "password": temp_pw, "email_confirm": True,
        })
        user = getattr(resp, "user", None) or resp
        auth_id = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
        if auth_id:
            return auth_id, True, None
        return None, False, "no id returned"
    except Exception as e:
        # already registered → find it and (re)set the temp password
        existing = _find_auth_user_by_email(admin, email)
        if existing:
            try:
                admin.auth.admin.update_user_by_id(existing, {"password": temp_pw})
            except Exception:
                pass
            return existing, False, None
        return None, False, str(e)[:200]


@router.post("/users/create-login")
async def create_login(body: dict, org_id: str = ORG_ID):
    """Create (or relink) the Supabase Auth account for ONE assigned user and store auth_id.
    Returns the temp password so the admin can hand it out (user resets on first login)."""
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    client = sb()
    cur = client.schema("storeops").table("app_users").select("*").eq("org_id", org_id) \
        .eq("email", email).limit(1).execute().data or []
    if not cur:
        raise HTTPException(400, "assign a role to this email first (/users/assign)")
    admin = get_supabase_admin()
    temp_pw = body.get("temp_password") or ("Mp" + secrets.token_urlsafe(6))
    auth_id, created, err = _create_or_link_auth(admin, email, temp_pw)
    if not auth_id:
        raise HTTPException(500, f"could not create login: {err}")
    client.schema("storeops").table("app_users").update(
        {"auth_id": auth_id, "must_reset_password": True}).eq("id", cur[0]["id"]).execute()
    return {"email": email, "created": created, "temp_password": temp_pw, "auth_id": auth_id}


@router.post("/users/bulk-provision")
async def bulk_provision(body: dict, org_id: str = ORG_ID):
    """Create logins for every assigned core.users row that has an email and no auth_id yet
    (optionally limited to body['emails']). Returns the per-user temp passwords to distribute."""
    client = sb()
    rows = client.schema("storeops").table("app_users").select("*").eq("org_id", org_id) \
        .execute().data or []
    want = set((e or "").lower() for e in (body.get("emails") or []))
    admin = get_supabase_admin()
    created, skipped, results = 0, 0, []
    for u in rows:
        email = (u.get("email") or "").strip().lower()
        if not email or u.get("auth_id"):
            skipped += 1
            continue
        if want and email not in want:
            continue
        temp_pw = "Mp" + secrets.token_urlsafe(6)
        auth_id, was_new, err = _create_or_link_auth(admin, email, temp_pw)
        if not auth_id:
            results.append({"email": email, "ok": False, "error": err})
            continue
        client.schema("storeops").table("app_users").update(
            {"auth_id": auth_id, "must_reset_password": True}).eq("id", u["id"]).execute()
        created += 1
        results.append({"email": email, "ok": True, "temp_password": temp_pw,
                        "name": u.get("full_name"), "role": u.get("role")})
    return {"created": created, "skipped": skipped, "results": results}


@router.post("/users/delete")
async def delete_user(body: dict, org_id: str = ORG_ID):
    """Hard-delete an app user: remove the storeops.app_users row AND its Supabase Auth account."""
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    client = sb()
    rows = client.schema("storeops").table("app_users").select("auth_id") \
        .eq("org_id", org_id).eq("email", email).limit(1).execute().data or []
    auth_id = rows[0].get("auth_id") if rows else None
    client.schema("storeops").table("app_users").delete() \
        .eq("org_id", org_id).eq("email", email).execute()
    auth_deleted = False
    if auth_id:
        try:
            get_supabase_admin().auth.admin.delete_user(auth_id)
            auth_deleted = True
        except Exception:
            pass
    return {"deleted": bool(rows), "auth_deleted": auth_deleted}


@router.post("/users/deactivate")
async def deactivate_user(body: dict, org_id: str = ORG_ID):
    """Soft-disable an app user (keeps the auth account; flip is_active)."""
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    res = sb().schema("storeops").table("app_users").update(
        {"is_active": bool(body.get("is_active", False))}) \
        .eq("org_id", org_id).eq("email", email).execute()
    return {"updated": len(res.data or [])}


def purge_app_user(org_id, *, email=None, employee_id=None, hard=True):
    """Remove (or deactivate) the login(s) for a person, matched by email AND/OR employee_id.

    Called by employee delete/deactivate from BOTH the StoreOps roster and the Roles & Access
    page, so removing an employee is reflected everywhere instead of leaving the app_users row +
    Supabase Auth account dangling as a "ghost" manual user in Roles (the reported delete-sync bug).
      hard=True  → delete the app_users row(s) + the Supabase Auth account(s).
      hard=False → flip is_active=False (keeps the auth account but blocks access).
    No-op (matched:0) if no email/employee_id or nothing matches."""
    email = (email or "").strip().lower()
    employee_id = (str(employee_id).strip() or None) if employee_id is not None else None
    if not email and not employee_id:
        return {"matched": 0}
    client = get_supabase()
    tbl = lambda: client.schema("storeops").table("app_users")
    found = {}
    try:
        if email:
            for r in (tbl().select("id,auth_id,email,employee_id")
                      .eq("org_id", org_id).eq("email", email).execute().data or []):
                found[r["id"]] = r
        if employee_id:
            for r in (tbl().select("id,auth_id,email,employee_id")
                      .eq("org_id", org_id).eq("employee_id", employee_id).execute().data or []):
                found[r["id"]] = r
    except Exception:
        return {"matched": 0}
    rows = list(found.values())
    if not rows:
        return {"matched": 0}
    if not hard:
        for r in rows:
            tbl().update({"is_active": False}).eq("id", r["id"]).execute()
        return {"matched": len(rows), "deactivated": len(rows)}
    ids = [r["id"] for r in rows]
    tbl().delete().in_("id", ids).execute()
    admin, auth_deleted = None, 0
    for r in rows:
        if r.get("auth_id"):
            try:
                if admin is None:
                    admin = get_supabase_admin()
                admin.auth.admin.delete_user(r["auth_id"])
                auth_deleted += 1
            except Exception:
                pass
    return {"matched": len(rows), "deleted": len(ids), "auth_deleted": auth_deleted}


@router.post("/employees/purge")
async def purge_employee(body: dict, org_id: str = ORG_ID):
    """Delete or deactivate a person from BOTH the StoreOps roster and the Roles module in one
    call (the Roles & Access remove action). Identify by employee_pk (storeops.employees.id) for
    a real employee, or by email/employee_id for a manually-added Roles user (which has no
    employees row). mode='delete' hard-removes the employees row + login + Supabase Auth account;
    mode='deactivate' flips both is_active=False and revokes access (keeps the auth account)."""
    mode = (body.get("mode") or "delete").strip().lower()
    hard = mode != "deactivate"
    emp_pk = body.get("employee_pk", body.get("id"))
    email = (body.get("email") or "").strip().lower()
    employee_id = body.get("employee_id")
    client = sb()
    name = None
    # Synthetic negative ids are Roles-only manual users (no employees row) — skip the roster op.
    synthetic = False
    try:
        synthetic = int(str(emp_pk)) < 0
    except (TypeError, ValueError):
        synthetic = False
    if emp_pk not in (None, "") and not synthetic:
        rows = client.schema("storeops").table("employees").select("id,name,email,employee_id") \
            .eq("org_id", org_id).eq("id", str(emp_pk)).limit(1).execute().data or []
        if rows:
            name = rows[0].get("name")
            email = email or (rows[0].get("email") or "").lower()
            employee_id = employee_id or rows[0].get("employee_id")
            if hard:
                client.schema("storeops").table("employees").delete().eq("id", str(emp_pk)).execute()
            else:
                client.schema("storeops").table("employees").update({"is_active": False}) \
                    .eq("id", str(emp_pk)).execute()
    login = purge_app_user(org_id, email=email, employee_id=employee_id, hard=hard)
    return {"ok": True, "mode": mode, "name": name, "login": login}


# ── Employee Dashboard (role-gated widgets) ───────────────────────────────────
EMP_WIDGETS = ["schedule", "timeoff", "hours", "commission", "targets",
               "report_card", "commission_tracking", "flags", "chargebacks"]


def _emp_period(period):
    import calendar as _cal
    if period:
        return period
    n = datetime.now(timezone.utc)
    return f"{_cal.month_name[n.month]} {n.year}"


@router.get("/employee-widgets")
def employee_widgets_keys():
    """The canonical widget list (for the roles manager toggles)."""
    return {"widgets": EMP_WIDGETS}


@router.get("/employee-dashboard")
def employee_dashboard(employee_id: str = "", period: str = "", org_id: str = ORG_ID):
    """One bundle for an employee's self-service dashboard, plus the effective widget
    visibility (role employee_widgets). Sections compute regardless; the frontend hides
    disabled widgets. employee_id identifies the rep (admins pass it; a self-scoped rep is
    pinned to their own by the frontend)."""
    from datetime import date as _date, timedelta as _td
    if not employee_id:
        raise HTTPException(400, "employee_id required")
    client = sb()
    emp = (client.schema("storeops").table("employees").select("*")
           .eq("employee_id", employee_id).limit(1).execute().data or [])
    if not emp:
        raise HTTPException(404, "employee not found")
    emp = emp[0]
    name = (emp.get("name") or "").strip()
    eslp = (emp.get("epay_salesperson") or "").strip()
    period = _emp_period(period)
    keys_upper = {name.upper(), eslp.upper()} - {""}

    def _is_me(rec, *fields):
        for f in fields:
            if (rec.get(f) or "").strip().upper() in keys_upper:
                return True
        return False

    # Effective widgets = role default (all-on if no role), then this employee's
    # per-person overrides applied on top (#1b).
    widgets = {k: True for k in EMP_WIDGETS}
    au = (client.schema("storeops").table("app_users").select("role,widget_overrides")
          .eq("employee_id", employee_id).limit(1).execute().data or [])
    role_name = au[0].get("role") if au else None
    if role_name:
        rr = (client.schema("storeops").table("roles").select("permissions")
              .eq("org_id", org_id).eq("name", role_name).limit(1).execute().data or [])
        ew = (rr[0].get("permissions") or {}).get("employee_widgets") if rr else None
        if isinstance(ew, dict):
            widgets = {k: bool(ew.get(k, True)) for k in EMP_WIDGETS}
    ovr = au[0].get("widget_overrides") if au else None
    if isinstance(ovr, dict):
        for k, v in ovr.items():
            if k in widgets:
                widgets[k] = bool(v)

    out = {
        "employee": {"employee_id": employee_id, "name": name, "store": emp.get("home_store"),
                     "epay_salesperson": eslp, "role": role_name, "pay_rate": float(emp.get("pay_rate") or 0)},
        "period": period, "widgets": widgets,
    }

    # Commission (current period) + tracking (all periods).
    comm = client.schema("commcalc").table("rep_commissions").select("*").eq("org_id", org_id).eq("period", period).execute().data or []
    myc = next((c for c in comm if _is_me(c, "storeops_name", "epay_salesperson")), None)
    out["commission"] = myc
    allc = (client.schema("commcalc").table("rep_commissions")
            .select("period,period_year,period_month,total_payout,tier,kpis_met,total_kpis,storeops_name,epay_salesperson")
            .eq("org_id", org_id).execute().data or [])
    track = [c for c in allc if _is_me(c, "storeops_name", "epay_salesperson")]
    track.sort(key=lambda r: (r.get("period_year") or 0, r.get("period_month") or 0))
    out["commission_tracking"] = track

    # Flags + chargebacks attributed to this rep.
    fl = client.schema("commcalc").table("flags").select("*").eq("org_id", org_id).eq("period", period).execute().data or []
    myf = [f for f in fl if _is_me(f, "epay_salesperson")]
    out["flags"] = myf
    cbs = client.schema("commcalc").table("chargeback_items").select("*").eq("org_id", org_id).eq("period", period).execute().data or []
    mycb = [c for c in cbs if _is_me(c, "epay_salesperson")]
    out["chargebacks"] = mycb

    # Schedule (upcoming 7 days) + hours (current month) from storeops.shifts.
    today = _date.today()
    out["schedule"] = (client.schema("storeops").table("shifts").select("*")
                       .eq("is_deleted", False).eq("employee_id", employee_id)
                       .gte("shift_date", today.isoformat())
                       .lte("shift_date", (today + _td(days=7)).isoformat())
                       .order("shift_date").execute().data or [])
    ym = f"{today.year}-{today.month:02d}"
    nxt = f"{today.year + 1}-01-01" if today.month == 12 else f"{today.year}-{today.month + 1:02d}-01"
    msh = (client.schema("storeops").table("shifts").select("scheduled_hours,actual_hours")
           .eq("is_deleted", False).eq("employee_id", employee_id)
           .gte("shift_date", f"{ym}-01").lt("shift_date", nxt).execute().data or [])
    rate = float(emp.get("pay_rate") or 0)
    sh = sum(float(s.get("scheduled_hours") or 0) for s in msh)
    ah = sum((float(s.get("actual_hours") or 0) or float(s.get("scheduled_hours") or 0)) for s in msh)
    out["hours"] = {"scheduled_hours": round(sh, 1), "actual_hours": round(ah, 1),
                    "pay_rate": rate, "scheduled_pay": round(sh * rate, 2),
                    "actual_pay": round(ah * rate, 2), "shifts": len(msh)}

    # Report card = full performance summary.
    out["report_card"] = {
        "tier": (myc or {}).get("tier"),
        "kpis_met": (myc or {}).get("kpis_met"),
        "total_kpis": (myc or {}).get("total_kpis"),
        "kpi_values": (myc or {}).get("kpi_values") or {},
        "commission_earned": (myc or {}).get("final_payout") if myc and myc.get("final_payout") is not None else (myc or {}).get("total_payout"),
        "flags_count": len(myf),
        "chargebacks_count": len(mycb),
        "chargebacks_total": round(sum(float(c.get("amount") or 0) for c in mycb), 2),
    }
    out["targets"] = {"acc_target": (myc or {}).get("acc_target"), "acc_comm": (myc or {}).get("acc_comm")}
    return out


@router.put("/employee-widgets")
async def set_employee_widget_overrides(body: dict, org_id: str = ORG_ID):
    """Per-employee Employee-Dashboard widget overrides (#1b). Body:
    {employee_id?, email?, widget_overrides: {widget_key: bool} | null}. Writes onto the
    person's storeops.app_users row (so they must be assigned a role first). null/{} = clear
    (inherit the role default). Unknown widget keys are dropped."""
    eid = (body.get("employee_id") or "").strip()
    email = (body.get("email") or "").strip().lower()
    if not eid and not email:
        raise HTTPException(400, "employee_id or email required")
    raw = body.get("widget_overrides")
    if raw in (None, {}):
        ovr = None
    elif isinstance(raw, dict):
        ovr = {k: bool(v) for k, v in raw.items() if k in EMP_WIDGETS}
        ovr = ovr or None
    else:
        raise HTTPException(400, "widget_overrides must be an object or null")
    client = sb()
    q = client.schema("storeops").table("app_users").select("id").eq("org_id", org_id)
    q = q.eq("employee_id", eid) if eid else q.eq("email", email)
    rows = q.limit(1).execute().data or []
    if not rows:
        raise HTTPException(404, "no app login for this person — assign a role first")
    client.schema("storeops").table("app_users").update({"widget_overrides": ovr}) \
        .eq("id", rows[0]["id"]).execute()
    return {"ok": True, "employee_id": eid or None, "email": email or None, "widget_overrides": ovr}

