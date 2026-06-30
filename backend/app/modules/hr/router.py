"""HR module API — /api/v1/hr/*

A permission-gated VIEW layer over the salary + commission data that already lives in StoreOps
(employees, shifts, pay_rate) and CommCalc (rep_commissions, chargebacks). It does NOT move any
data — commission is still computed in CommCalc. Reads are scoped to the signed-in manager's org
span (reusing the StoreOps span helpers), so HR figures respect the same boundaries as the rest of
the app. The HR employees / payroll / time-off pages reuse the existing scoped StoreOps endpoints;
this router adds the one genuinely new thing: per-employee TOTAL COMPENSATION (wages + commission).
"""
import calendar
import re
import secrets
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Header, HTTPException, UploadFile, File, Form
from app.core.database import get_supabase
from app.modules.storeops.router import scope_keyset, in_keyset

router = APIRouter(prefix="/hr", tags=["HR"])
ORG_ID = "00000000-0000-0000-0000-000000000001"

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}


def _so():
    return get_supabase().schema("storeops")


def _cc():
    return get_supabase().schema("commcalc")


def _month_range(period: str):
    """'June 2026' or '2026-06' -> ('2026-06-01', '2026-07-01'). (None, None) if unparseable."""
    parts = (period or "").strip().split()
    y = mo = None
    if len(parts) == 2 and parts[0].lower() in _MONTHS:
        try:
            mo, y = _MONTHS[parts[0].lower()], int(parts[1])
        except Exception:
            return None, None
    elif len(parts) == 1 and "-" in parts[0]:
        try:
            a, b = parts[0].split("-")[:2]
            y, mo = int(a), int(b)
        except Exception:
            return None, None
    if not y or not mo:
        return None, None
    nxt = f"{y + 1}-01-01" if mo == 12 else f"{y}-{mo + 1:02d}-01"
    return f"{y}-{mo:02d}-01", nxt


def _pvariants(period):
    """Period stored variously as 'June 2026' or '2026-06' across tables — match both."""
    out = {(period or "").strip()}
    start, _ = _month_range(period)
    if start:
        y, mo = start.split("-")[:2]
        out.add(f"{y}-{mo}")
        out.add(f"{calendar.month_name[int(mo)]} {y}")
    return [p for p in out if p]


# ── Employee management (HR is the single front door to create a person) ───────────────────────
# "Create once → available everywhere." One controller creates the StoreOps roster row (+ a stable
# employee_id), then — if a role/scope is given — upserts the app_users role assignment, and
# optionally provisions a login. Scheduling / payroll / org-tree / commissions all key off
# employee_id, so no extra writes are needed for the person to appear there. Reuses the existing
# storeops + core helpers (no new business logic) so behavior matches the other create paths.

@router.get("/employees")
async def hr_list_employees(org_id: str = ORG_ID):
    """The roster + role/login state (delegates to the same merge core/Roles uses)."""
    from app.modules.core.router import list_employees
    return await list_employees(org_id)


@router.post("/employees")
async def hr_create_employee(body: dict, org_id: str = ORG_ID):
    """Create a person from HR. Body: name (req), email?, phone?, home_store?, job title (role)?,
    pay_rate?, employee_id?, plus optional app fields: role_name (RBAC role), market?, store_code?,
    store_codes?[], create_login?. De-dupes by email. Returns the employee + any login temp password."""
    from app.modules.storeops.router import EMP_FIELDS, _ensure_employee_id
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    so = _so()
    email = (body.get("email") or "").strip().lower()

    emp = None
    if email:
        ex = (so.table("employees").select("*").eq("org_id", org_id)
              .ilike("email", email).limit(1).execute().data) or []
        emp = ex[0] if ex else None
    if not emp:
        row = {k: body[k] for k in EMP_FIELDS if k in body}
        row["org_id"] = org_id
        row["name"] = name
        if email:
            row["email"] = email
        if row.get("is_active") is None:
            row["is_active"] = True
        # blank employee_id is TEXT UNIQUE → drop it so it's NULL, then auto-assign E<pk> below.
        if not str(row.get("employee_id") or "").strip():
            row.pop("employee_id", None)
        r = so.table("employees").insert(row).execute()
        emp = _ensure_employee_id(r.data[0]) if r.data else row

    # Role + scope (optional) — app_users is keyed on email, so a role needs one.
    role = (body.get("role_name") or body.get("app_role") or "").strip()
    has_scope = any(body.get(k) for k in ("market", "store_code", "store_codes"))
    assigned, login = None, None
    if email and (role or has_scope):
        from app.modules.core.router import assign_role
        await assign_role({
            "email": email, "full_name": name, "role": role or "sales_rep",
            "market": body.get("market"), "store_code": body.get("store_code"),
            "store_codes": body.get("store_codes"), "employee_id": emp.get("employee_id"),
        }, org_id)
        assigned = role or "sales_rep"
        if body.get("create_login"):
            from app.modules.core.router import create_login as core_create_login
            try:
                login = await core_create_login({"email": email}, org_id)
            except Exception as e:
                login = {"error": str(e)[:200]}
    elif (role or has_scope) and not email:
        # surface why the role didn't stick (matches the Roles page rule)
        assigned = None
    return {"employee": emp, "assigned_role": assigned, "login": login,
            "note": (None if email or not (role or has_scope)
                     else "Role/scope ignored — an email is required to assign a role or create a login.")}


@router.patch("/employees/{emp_id}")
async def hr_update_employee(emp_id: str, body: dict, org_id: str = ORG_ID):
    """Update a person from HR. Updates the roster row (if roster fields are present) and, when a
    role/scope + email is given, re-syncs the app_users assignment so the login stays in step."""
    from app.modules.storeops.router import EMP_FIELDS, update_employee
    res = None
    if any(k in body for k in EMP_FIELDS):
        res = update_employee(emp_id, body)   # sync handler; raises 404 if missing
    email = (body.get("email") or (res or {}).get("email") or "").strip().lower()
    role = (body.get("role_name") or body.get("app_role") or "").strip()
    has_scope = any(k in body for k in ("market", "store_code", "store_codes"))
    if email and (role or has_scope):
        from app.modules.core.router import assign_role
        await assign_role({
            "email": email, "full_name": body.get("name") or (res or {}).get("name"),
            "role": role or None, "market": body.get("market"),
            "store_code": body.get("store_code"), "store_codes": body.get("store_codes"),
            "employee_id": (res or {}).get("employee_id"),
        }, org_id)
    return res or {"ok": True, "id": emp_id}


@router.get("/compensation")
def compensation(period: str, authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Per-employee total compensation for a period: wages (hours × pay_rate, from shifts) +
    commission (rep_commissions total_payout) − chargeback deductions. Span-scoped to the caller."""
    so, cc = _so(), _cc()
    emps = (so.table("employees")
            .select("employee_id,name,home_store,pay_rate,is_active,epay_salesperson")
            .eq("org_id", org_id).eq("is_active", True).execute().data) or []
    ks = scope_keyset(authorization, org_id)   # None = unrestricted (admin / enforcement off)
    if ks is not None:
        emps = [e for e in emps if in_keyset(ks, e.get("home_store"))]

    # Wages — sum the month's shift hours (actual, falling back to scheduled) × pay_rate.
    start, nxt = _month_range(period)
    hours_by_eid = {}
    if start:
        shifts = (so.table("shifts").select("employee_id,scheduled_hours,actual_hours")
                  .eq("org_id", org_id).eq("is_deleted", False)
                  .gte("shift_date", start).lt("shift_date", nxt).execute().data) or []
        for s in shifts:
            eid = s.get("employee_id")
            if not eid:
                continue
            h = float(s.get("actual_hours") or 0) or float(s.get("scheduled_hours") or 0)
            hours_by_eid[eid] = hours_by_eid.get(eid, 0.0) + h

    # Commission — rep_commissions for the period, keyed by either name field.
    comm = (cc.table("rep_commissions")
            .select("storeops_name,epay_salesperson,total_payout,subtotal")
            .eq("org_id", org_id).in_("period", _pvariants(period)).execute().data) or []
    comm_by_key = {}
    for c in comm:
        for k in (c.get("storeops_name"), c.get("epay_salesperson")):
            if k:
                comm_by_key[str(k).strip().upper()] = c

    # Chargeback deductions (only those flagged to deduct).
    cb_by_key = {}
    try:
        cbs = (cc.table("chargeback_items").select("epay_salesperson,amount,deduct")
               .eq("org_id", org_id).in_("period", _pvariants(period)).execute().data) or []
        for c in cbs:
            if c.get("deduct") is False:
                continue
            k = str(c.get("epay_salesperson") or "").strip().upper()
            if k:
                cb_by_key[k] = cb_by_key.get(k, 0.0) + float(c.get("amount") or 0)
    except Exception:
        pass

    rows, tot_w, tot_c, tot_cb = [], 0.0, 0.0, 0.0
    for e in emps:
        rate = float(e.get("pay_rate") or 0)
        hrs = round(hours_by_eid.get(e.get("employee_id"), 0.0), 1)
        wages = round(hrs * rate, 2)
        keys = {str(e.get("name") or "").strip().upper(),
                str(e.get("epay_salesperson") or "").strip().upper()} - {""}
        cr = next((comm_by_key[k] for k in keys if k in comm_by_key), None)
        commission = round(float((cr or {}).get("total_payout") or 0), 2)
        cb = round(sum(cb_by_key[k] for k in keys if k in cb_by_key), 2)
        if hrs == 0 and commission == 0 and not rate:
            continue   # nothing to show for this person
        total = round(wages + commission - cb, 2)
        # Annualized projection: this period's total comp run-rate × 12 months.
        annualized = round(total * 12, 2)
        rows.append({"employee_id": e.get("employee_id"), "name": e.get("name"),
                     "store": e.get("home_store"), "pay_rate": rate, "hours": hrs,
                     "base_salary": wages, "commission": commission, "chargebacks": cb,
                     "total_comp": total, "annualized": annualized})
        tot_w += wages; tot_c += commission; tot_cb += cb

    rows.sort(key=lambda r: -r["total_comp"])
    total_comp = round(tot_w + tot_c - tot_cb, 2)
    return {"period": period, "rows": rows,
            "totals": {"base_salary": round(tot_w, 2), "commission": round(tot_c, 2),
                       "chargebacks": round(tot_cb, 2), "total_comp": total_comp,
                       "annualized": round(total_comp * 12, 2), "employees": len(rows)}}


# ════════════════════════════════════════════════════════════════════════════════════════════════
# ONBOARDING CHECKLIST (migration 073) — a configurable, collapsible-by-category checklist HR runs for
# every new hire. Each item has an OWNER role (employee / HR / DM / Market Manager), an optional live
# state/federal document LINK, and per-employee status + uploaded document (verified by HR). A pre-start
# employee (no login yet) reaches their own items through a credential-less QR portal guarded by a
# DOB / last-4-SSN gate. All storeops.* tables; every endpoint degrades gracefully if 073 isn't run.
# ════════════════════════════════════════════════════════════════════════════════════════════════
ONBOARD_BUCKET = "onboarding-docs"
OWNER_ROLES = ["employee", "hr", "dm", "market_manager"]
OWNER_ROLE_LABELS = {"employee": "Employee", "hr": "HR", "dm": "District Manager", "market_manager": "Market Manager"}
ONBOARD_STATUSES = ["pending", "submitted", "verified", "na"]
SEED_STATES = ["NY", "NJ", "DE", "PA", "IL", "CT", "MA", "IN"]
TASK_FIELDS = ["category_id", "key", "label", "description", "owner_role", "doc_url", "doc_label",
               "is_fillable", "requires_upload", "applies_state", "sort_order", "is_active"]


def _now_iso():
    return datetime.utcnow().isoformat()


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")[:60] or "item"


def _ensure_onboard_bucket():
    c = get_supabase()
    try:
        c.storage.get_bucket(ONBOARD_BUCKET)
    except Exception:
        try:
            c.storage.create_bucket(ONBOARD_BUCKET)   # private by default
        except Exception:
            pass
    return c


# ── Template: categories (collapsible) + tasks ─────────────────────────────────────────────────────
@router.get("/onboarding/template")
def onboarding_template(include_inactive: bool = False, org_id: str = ORG_ID):
    """The checklist DEFINITION: categories (collapsible) each carrying their tasks. ready:false if 073
    isn't applied — the UI then shows a 'run migration 073' hint instead of erroring."""
    so = _so()
    try:
        cats = (so.table("onboarding_category").select("*").eq("org_id", org_id)
                .order("sort_order").execute().data) or []
        tasks = (so.table("onboarding_task").select("*").eq("org_id", org_id)
                 .order("sort_order").execute().data) or []
    except Exception:
        return {"ready": False, "categories": [], "owner_roles": OWNER_ROLES,
                "owner_labels": OWNER_ROLE_LABELS, "states": SEED_STATES}
    if not include_inactive:
        cats = [c for c in cats if c.get("is_active", True)]
        tasks = [t for t in tasks if t.get("is_active", True)]
    by_cat = {}
    for t in tasks:
        by_cat.setdefault(t.get("category_id"), []).append(t)
    out = [{**c, "tasks": by_cat.get(c["id"], [])} for c in cats]
    return {"ready": True, "categories": out, "owner_roles": OWNER_ROLES,
            "owner_labels": OWNER_ROLE_LABELS, "states": SEED_STATES}


@router.post("/onboarding/categories")
def onboarding_save_category(body: dict, org_id: str = ORG_ID):
    label = (body.get("label") or "").strip()
    if not label:
        raise HTTPException(400, "label required")
    row = {"org_id": org_id, "key": (body.get("key") or _slug(label)).strip(),
           "label": label, "sort_order": int(body.get("sort_order") or 100)}
    try:
        r = so_upsert("onboarding_category", row, "org_id,key")
    except Exception as e:
        raise HTTPException(400, f"Could not save category — is migration 073 applied? {e}")
    return (r or [row])[0]


@router.patch("/onboarding/categories/{cat_id}")
def onboarding_update_category(cat_id: str, body: dict, org_id: str = ORG_ID):
    upd = {k: body[k] for k in ("label", "sort_order", "is_active") if k in body}
    r = _so().table("onboarding_category").update(upd).eq("org_id", org_id).eq("id", cat_id).execute()
    return (r.data or [{}])[0]


@router.delete("/onboarding/categories/{cat_id}")
def onboarding_delete_category(cat_id: str, org_id: str = ORG_ID):
    _so().table("onboarding_category").delete().eq("org_id", org_id).eq("id", cat_id).execute()
    return {"ok": True}


@router.post("/onboarding/tasks")
def onboarding_save_task(body: dict, org_id: str = ORG_ID):
    label = (body.get("label") or "").strip()
    if not label:
        raise HTTPException(400, "label required")
    row = {k: body[k] for k in TASK_FIELDS if k in body}
    row.update({"org_id": org_id, "label": label})
    row.setdefault("key", _slug(label))
    row.setdefault("owner_role", "employee")
    if (row.get("applies_state") or "").strip() == "":
        row["applies_state"] = None
    elif row.get("applies_state"):
        row["applies_state"] = row["applies_state"].strip().upper()
    try:
        r = so_upsert("onboarding_task", row, "org_id,key")
    except Exception as e:
        raise HTTPException(400, f"Could not save task — is migration 073 applied? {e}")
    return (r or [row])[0]


@router.patch("/onboarding/tasks/{task_id}")
def onboarding_update_task(task_id: str, body: dict, org_id: str = ORG_ID):
    upd = {k: body[k] for k in TASK_FIELDS if k in body}
    if "applies_state" in upd:
        upd["applies_state"] = (upd["applies_state"] or "").strip().upper() or None
    r = _so().table("onboarding_task").update(upd).eq("org_id", org_id).eq("id", task_id).execute()
    return (r.data or [{}])[0]


@router.delete("/onboarding/tasks/{task_id}")
def onboarding_delete_task(task_id: str, org_id: str = ORG_ID):
    _so().table("onboarding_task").delete().eq("org_id", org_id).eq("id", task_id).execute()
    return {"ok": True}


def so_upsert(table, row, on_conflict):
    return _so().table(table).upsert(row, on_conflict=on_conflict).execute().data


# ── Per-employee checklist ─────────────────────────────────────────────────────────────────────────
def _get_profile(so, org_id, employee_id):
    rows = (so.table("employee_onboarding_profile").select("*").eq("org_id", org_id)
            .eq("employee_id", employee_id).limit(1).execute().data) or []
    return rows[0] if rows else None


def _public_profile(prof):
    if not prof:
        return None
    return {"work_state": prof.get("work_state"), "has_token": bool(prof.get("access_token")),
            "token_active": prof.get("token_active"), "verify_kind": prof.get("verify_kind"),
            "token_expires_at": prof.get("token_expires_at")}


@router.get("/onboarding/employee/{employee_id}")
def onboarding_for_employee(employee_id: str, org_id: str = ORG_ID):
    """The new hire's checklist: every active task merged with their status / uploaded doc / verification.
    State-specific tax forms appear only once the employee's work_state is set (and matches)."""
    so = _so()
    tmpl = onboarding_template(org_id=org_id)
    if not tmpl.get("ready"):
        return {"ready": False, "employee_id": employee_id, "categories": []}
    prof = _get_profile(so, org_id, employee_id)
    work_state = (prof or {}).get("work_state")
    st = {r["task_id"]: r for r in ((so.table("employee_onboarding").select("*")
          .eq("org_id", org_id).eq("employee_id", employee_id).execute().data) or [])}
    cats, total, done, has_state_tasks = [], 0, 0, False
    for c in tmpl["categories"]:
        tasks = []
        for t in c["tasks"]:
            ast = t.get("applies_state")
            if ast:
                has_state_tasks = True
                if not work_state or ast != work_state:
                    continue   # other-state (or unset) — hide until the work state is chosen
            rec = st.get(t["id"]) or {}
            status = rec.get("status") or "pending"
            tasks.append({**t, "status": status, "note": rec.get("note"),
                          "document_name": rec.get("document_name"), "has_document": bool(rec.get("document_path")),
                          "verified_by": rec.get("verified_by"), "verified_at": rec.get("verified_at"),
                          "submitted_at": rec.get("submitted_at")})
            total += 1
            if status in ("verified", "na"):
                done += 1
        if tasks:
            cats.append({**{k: c[k] for k in c if k != "tasks"}, "tasks": tasks})
    emp = (so.table("employees").select("name,home_store").eq("org_id", org_id)
           .eq("employee_id", employee_id).limit(1).execute().data) or [{}]
    return {"ready": True, "employee_id": employee_id, "employee_name": emp[0].get("name"),
            "work_state": work_state, "profile": _public_profile(prof),
            "needs_work_state": bool(has_state_tasks and not work_state),
            "categories": cats, "progress": {"total": total, "done": done},
            "owner_labels": OWNER_ROLE_LABELS, "states": SEED_STATES}


@router.patch("/onboarding/employee/{employee_id}")
def onboarding_set_profile(employee_id: str, body: dict, org_id: str = ORG_ID):
    """Set the employee's work_state (drives which state tax form shows)."""
    upd = {"org_id": org_id, "employee_id": employee_id}
    if "work_state" in body:
        upd["work_state"] = (body.get("work_state") or "").strip().upper() or None
    try:
        _so().table("employee_onboarding_profile").upsert(upd, on_conflict="org_id,employee_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save — is migration 073 applied? {e}")
    return {"ok": True, "work_state": upd.get("work_state")}


@router.post("/onboarding/employee/{employee_id}/task/{task_id}")
def onboarding_update_status(employee_id: str, task_id: str, body: dict, org_id: str = ORG_ID):
    """HR/DM/MM marks a task verified / not-applicable, or adds a note. status=verified stamps who+when."""
    status = (body.get("status") or "").strip()
    if status and status not in ONBOARD_STATUSES:
        raise HTTPException(400, f"bad status '{status}'")
    row = {"org_id": org_id, "employee_id": employee_id, "task_id": task_id, "updated_at": _now_iso()}
    if status:
        row["status"] = status
        if status == "verified":
            row["verified_by"] = (body.get("verified_by") or "").strip() or None
            row["verified_at"] = _now_iso()
    if "note" in body:
        row["note"] = body.get("note")
    try:
        _so().table("employee_onboarding").upsert(row, on_conflict="org_id,employee_id,task_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not update — is migration 073 applied? {e}")
    return {"ok": True}


async def _do_onboard_upload(org_id, employee_id, task_id, file, who):
    data = await file.read()
    safe = (file.filename or "file").replace("/", "_")
    path = f"{org_id}/{employee_id}/{uuid.uuid4().hex}_{safe}"
    c = _ensure_onboard_bucket()
    try:
        c.storage.from_(ONBOARD_BUCKET).upload(path, data, {"content-type": file.content_type or "application/octet-stream"})
    except Exception as e:
        raise HTTPException(500, f"upload failed: {e}")
    row = {"org_id": org_id, "employee_id": employee_id, "task_id": task_id, "status": "submitted",
           "document_path": path, "document_name": safe, "submitted_at": _now_iso(), "updated_at": _now_iso(),
           "note": (f"uploaded by {who}" if who else None)}
    try:
        _so().table("employee_onboarding").upsert(row, on_conflict="org_id,employee_id,task_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not record upload — is migration 073 applied? {e}")
    return {"ok": True, "document_name": safe}


@router.post("/onboarding/employee/{employee_id}/upload")
async def onboarding_upload(employee_id: str, task_id: str = Form(...), file: UploadFile = File(...),
                            uploader: str = Form(""), org_id: str = ORG_ID):
    """HR uploads a completed document on the employee's behalf (status → submitted)."""
    return await _do_onboard_upload(org_id, employee_id, task_id, file, uploader or "HR")


@router.get("/onboarding/employee/{employee_id}/task/{task_id}/doc")
def onboarding_doc_url(employee_id: str, task_id: str, org_id: str = ORG_ID):
    """A 1-hour signed URL so HR can view/verify the uploaded document."""
    rows = (_so().table("employee_onboarding").select("document_path").eq("org_id", org_id)
            .eq("employee_id", employee_id).eq("task_id", task_id).limit(1).execute().data) or []
    if not rows or not rows[0].get("document_path"):
        raise HTTPException(404, "no document")
    try:
        res = get_supabase().storage.from_(ONBOARD_BUCKET).create_signed_url(rows[0]["document_path"], 3600)
        url = (res.get("signedURL") or res.get("signed_url") or res.get("signedUrl")) if isinstance(res, dict) else res
        return {"url": url}
    except Exception as e:
        raise HTTPException(500, f"could not sign url: {e}")


# ── Credential-less QR access (token + DOB/last-4 gate) ─────────────────────────────────────────────
@router.post("/onboarding/employee/{employee_id}/token")
def onboarding_mint_token(employee_id: str, body: dict, org_id: str = ORG_ID):
    """Issue (or rotate) the QR access token + identity gate. Body: verify_kind ('dob'|'ssn4'),
    verify_value, expires_days? Returns the token + the portal path the QR should encode."""
    kind = (body.get("verify_kind") or "dob").strip()
    val = (body.get("verify_value") or "").strip()
    if kind not in ("dob", "ssn4"):
        raise HTTPException(400, "verify_kind must be 'dob' or 'ssn4'")
    if not val:
        raise HTTPException(400, "verify_value required (the employee's DOB or last-4 SSN)")
    token = secrets.token_urlsafe(24)
    row = {"org_id": org_id, "employee_id": employee_id, "access_token": token, "verify_kind": kind,
           "token_active": True, "verify_dob": None, "verify_ssn4": None, "token_expires_at": None}
    if kind == "dob":
        row["verify_dob"] = val[:10]
    else:
        row["verify_ssn4"] = re.sub(r"\D", "", val)[-4:]
    days = body.get("expires_days")
    if days:
        try:
            row["token_expires_at"] = (datetime.utcnow() + timedelta(days=int(days))).isoformat()
        except Exception:
            pass
    try:
        _so().table("employee_onboarding_profile").upsert(row, on_conflict="org_id,employee_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not mint token — is migration 073 applied? {e}")
    return {"ok": True, "token": token, "portal_path": f"/onboard/{token}",
            "verify_kind": kind, "token_expires_at": row.get("token_expires_at")}


@router.delete("/onboarding/employee/{employee_id}/token")
def onboarding_revoke_token(employee_id: str, org_id: str = ORG_ID):
    _so().table("employee_onboarding_profile").update({"token_active": False}) \
        .eq("org_id", org_id).eq("employee_id", employee_id).execute()
    return {"ok": True}


# ── PUBLIC portal (no auth — guarded only by the opaque token + the DOB/last-4 gate) ────────────────
def _profile_by_token(token):
    rows = (get_supabase().schema("storeops").table("employee_onboarding_profile")
            .select("*").eq("access_token", token).limit(1).execute().data) or []
    return rows[0] if rows else None


def _token_valid(prof):
    if not prof or not prof.get("token_active"):
        return False
    exp = prof.get("token_expires_at")
    if exp:
        try:
            if datetime.fromisoformat(str(exp).replace("Z", "").split("+")[0].split(".")[0]) < datetime.utcnow():
                return False
        except Exception:
            pass
    return True


def _check_gate(prof, value):
    value = (value or "").strip()
    if not value:
        return False
    if prof.get("verify_kind") == "ssn4":
        return re.sub(r"\D", "", value)[-4:] == (prof.get("verify_ssn4") or "")
    return value[:10] == str(prof.get("verify_dob") or "")[:10]


@router.get("/public/onboarding/{token}")
def public_onboarding_meta(token: str):
    """Step 1 (credential-less): returns ONLY which identity gate to show — no employee data yet."""
    prof = _profile_by_token(token)
    if not _token_valid(prof):
        raise HTTPException(404, "This onboarding link is invalid or has expired. Ask HR for a new QR code.")
    return {"ok": True, "verify_kind": prof.get("verify_kind") or "dob"}


@router.post("/public/onboarding/{token}")
def public_onboarding_view(token: str, body: dict):
    """Step 2: gate check → the employee's first name + their own checklist items (links + uploads)."""
    prof = _profile_by_token(token)
    if not _token_valid(prof):
        raise HTTPException(404, "This onboarding link is invalid or has expired.")
    if not _check_gate(prof, body.get("value")):
        raise HTTPException(403, "That didn't match our records. Please check and try again.")
    data = onboarding_for_employee(prof["employee_id"], org_id=prof["org_id"])
    first = (str(data.get("employee_name") or "").split() or [""])[0]
    cats = []
    for c in data.get("categories", []):
        tasks = [{"id": t["id"], "label": t["label"], "description": t.get("description"),
                  "doc_url": t.get("doc_url"), "doc_label": t.get("doc_label"),
                  "is_fillable": t.get("is_fillable"), "requires_upload": t.get("requires_upload"),
                  "status": t.get("status"), "has_document": t.get("has_document")}
                 for t in c.get("tasks", []) if t.get("owner_role") == "employee"]
        if tasks:
            cats.append({"key": c["key"], "label": c["label"], "tasks": tasks})
    return {"ok": True, "first_name": first, "categories": cats, "progress": data.get("progress")}


@router.post("/public/onboarding/{token}/upload")
async def public_onboarding_upload(token: str, value: str = Form(...), task_id: str = Form(...),
                                   file: UploadFile = File(...)):
    """Step 3: the employee uploads a completed/signed form. Re-checks the gate on every call."""
    prof = _profile_by_token(token)
    if not _token_valid(prof):
        raise HTTPException(404, "This onboarding link is invalid or has expired.")
    if not _check_gate(prof, value):
        raise HTTPException(403, "Identity check failed.")
    tk = (get_supabase().schema("storeops").table("onboarding_task").select("owner_role")
          .eq("org_id", prof["org_id"]).eq("id", task_id).limit(1).execute().data) or []
    if not tk or tk[0].get("owner_role") != "employee":
        raise HTTPException(403, "That item can't be uploaded from this portal.")
    return await _do_onboard_upload(prof["org_id"], prof["employee_id"], task_id, file, "employee")
