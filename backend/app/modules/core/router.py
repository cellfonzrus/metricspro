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
import secrets
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


# ── Roles ────────────────────────────────────────────────────────────────────────────
@router.get("/roles")
async def list_roles(org_id: str = ORG_ID):
    rows = sb().schema("storeops").table("roles").select("*").eq("org_id", org_id) \
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


@router.get("/employees")
async def list_employees(org_id: str = ORG_ID):
    """The storeops.employees roster + whether each already has an app login + assigned role.
    Drives the assignment grid (assign a role, then create logins)."""
    emps = sb().schema("storeops").table("employees").select(
        "id,employee_id,name,home_store,role,email,phone,is_active") \
        .eq("org_id", org_id).order("name").execute().data or []
    try:
        users = sb().schema("storeops").table("app_users").select("*").eq("org_id", org_id) \
            .execute().data or []
    except Exception:
        users = []   # migration 015 not run yet → no assignments to merge
    by_email = {(u.get("email") or "").lower(): u for u in users if u.get("email")}
    by_emp = {u.get("employee_id"): u for u in users if u.get("employee_id")}
    out = []
    for e in emps:
        u = by_emp.get(e.get("employee_id")) or by_email.get((e.get("email") or "").lower())
        out.append({
            **e,
            "app_role": (u or {}).get("role"),
            "has_login": bool((u or {}).get("auth_id")),
            "app_market": (u or {}).get("market"),
            "app_store": (u or {}).get("store_code"),
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
    cur = sb().schema("storeops").table("app_users").select("*").eq("org_id", org_id) \
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
        res = sb().schema("storeops").table("app_users").update(row).eq("id", cur[0]["id"]).execute()
    else:
        res = sb().schema("storeops").table("app_users").insert(row).execute()
    return (res.data or [{}])[0]


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


@router.post("/selftest")
async def selftest(org_id: str = ORG_ID):
    """One-shot end-to-end RBAC verification (TEMPORARY — removed after the pre-rollout check).
    Creates a throwaway admin + rep, signs them in via Supabase Auth, validates the /me token
    path + permission resolution + role gating, then deletes them. Touches only its own temp
    users. Guarded: refuses once login enforcement is ON, so it can't be abused during ops."""
    import secrets as _s
    client = sb()
    cfg = client.schema("storeops").table("app_config").select("rbac_enabled") \
        .eq("id", 1).limit(1).execute().data or []
    if cfg and cfg[0].get("rbac_enabled"):
        raise HTTPException(403, "refusing: login enforcement is ON")
    admin = get_supabase_admin()
    steps = []
    made = []  # (email, auth_id)

    def mk(role):
        email = f"zzz-rbac-selftest-{role}-{_s.token_hex(4)}@cellfonzrus.com"
        pw = "Tt1!" + _s.token_urlsafe(8)
        r = client.schema("storeops").table("app_users").insert({
            "org_id": org_id, "email": email, "full_name": f"Selftest {role}",
            "role": role, "is_active": True, "must_reset_password": True}).execute()
        resp = admin.auth.admin.create_user({"email": email, "password": pw, "email_confirm": True})
        uid = getattr(getattr(resp, "user", None), "id", None)
        client.schema("storeops").table("app_users").update({"auth_id": uid}) \
            .eq("org_id", org_id).eq("email", email).execute()
        made.append((email, uid))
        return email, pw, uid

    try:
        for role, want_admin, want_scope in (("admin", True, "all"), ("sales_rep", False, "self")):
            email, pw, uid = mk(role)
            # sign in (GoTrue password grant) → access token
            si = get_supabase().auth.sign_in_with_password({"email": email, "password": pw})
            token = getattr(getattr(si, "session", None), "access_token", None)
            # validate token like /me does
            who = admin.auth.get_user(token)
            tok_uid = getattr(getattr(who, "user", None), "id", None)
            # resolve permissions
            urow = client.schema("storeops").table("app_users").select("*") \
                .eq("auth_id", tok_uid).limit(1).execute().data or []
            perms = {}
            if urow:
                rr = client.schema("storeops").table("roles").select("permissions") \
                    .eq("name", urow[0]["role"]).limit(1).execute().data or []
                perms = (rr[0]["permissions"] if rr else {}) or {}
            mods = perms.get("modules", {})
            steps.append({
                "role": role,
                "signed_in": bool(token),
                "token_validates": tok_uid == uid and tok_uid is not None,
                "profile_found": bool(urow),
                "scope_ok": perms.get("scope") == want_scope,
                "admin_module_ok": bool(mods.get("admin")) == want_admin,
                "targets_module": bool(mods.get("targets")),
            })
        ok = all(s["signed_in"] and s["token_validates"] and s["profile_found"]
                 and s["scope_ok"] and s["admin_module_ok"] for s in steps)
        return {"ok": ok, "steps": steps}
    finally:
        for email, uid in made:
            try:
                client.schema("storeops").table("app_users").delete() \
                    .eq("org_id", org_id).eq("email", email).execute()
            except Exception:
                pass
            if uid:
                try:
                    admin.auth.admin.delete_user(uid)
                except Exception:
                    pass


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
