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
            "home_store": None, "role": None, "phone": None,
            "email": u.get("email"),
            "is_active": u.get("is_active", True),
            "app_role": u.get("role"),
            "has_login": bool(u.get("auth_id")),
            "app_market": u.get("market"),
            "app_store": u.get("store_code"),
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

    # Effective widgets from this employee's role (default all-on).
    widgets = {k: True for k in EMP_WIDGETS}
    au = (client.schema("storeops").table("app_users").select("role")
          .eq("employee_id", employee_id).limit(1).execute().data or [])
    role_name = au[0].get("role") if au else None
    if role_name:
        rr = (client.schema("storeops").table("roles").select("permissions")
              .eq("org_id", org_id).eq("name", role_name).limit(1).execute().data or [])
        ew = (rr[0].get("permissions") or {}).get("employee_widgets") if rr else None
        if isinstance(ew, dict):
            widgets = {k: bool(ew.get(k, True)) for k in EMP_WIDGETS}

    out = {
        "employee": {"employee_id": employee_id, "name": name, "store": emp.get("home_store"),
                     "epay_salesperson": eslp, "role": role_name, "pay_rate": float(emp.get("pay_rate") or 0)},
        "period": period, "widgets": widgets,
    }

    # Commission (current period) + tracking (all periods).
    comm = client.schema("commcalc").table("rep_commissions").select("*").eq("period", period).execute().data or []
    myc = next((c for c in comm if _is_me(c, "storeops_name", "epay_salesperson")), None)
    out["commission"] = myc
    allc = (client.schema("commcalc").table("rep_commissions")
            .select("period,period_year,period_month,total_payout,tier,kpis_met,total_kpis,storeops_name,epay_salesperson")
            .execute().data or [])
    track = [c for c in allc if _is_me(c, "storeops_name", "epay_salesperson")]
    track.sort(key=lambda r: (r.get("period_year") or 0, r.get("period_month") or 0))
    out["commission_tracking"] = track

    # Flags + chargebacks attributed to this rep.
    fl = client.schema("commcalc").table("flags").select("*").eq("period", period).execute().data or []
    myf = [f for f in fl if _is_me(f, "epay_salesperson")]
    out["flags"] = myf
    cbs = client.schema("commcalc").table("chargeback_items").select("*").eq("period", period).execute().data or []
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

