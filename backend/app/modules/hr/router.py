"""HR module API — /api/v1/hr/*

A permission-gated VIEW layer over the salary + commission data that already lives in StoreOps
(employees, shifts, pay_rate) and CommCalc (rep_commissions, chargebacks). It does NOT move any
data — commission is still computed in CommCalc. Reads are scoped to the signed-in manager's org
span (reusing the StoreOps span helpers), so HR figures respect the same boundaries as the rest of
the app. The HR employees / payroll / time-off pages reuse the existing scoped StoreOps endpoints;
this router adds the one genuinely new thing: per-employee TOTAL COMPENSATION (wages + commission).
"""
import base64
import calendar
import re
import secrets
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Header, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from app.core.database import get_supabase
from app.core.config import settings
from app.core import crypto
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

    # Auto-send the onboarding invite (product-owner default). Way 1 = a no-login token LINK when a
    # DOB gate is supplied; otherwise a temp portal LOGIN (way 2). Skip with send_invite=false, or
    # when the caller already provisioned a full login above.
    invite = None
    if email and body.get("send_invite", True) and emp.get("employee_id") and not login:
        method = (body.get("invite_method") or ("link" if (body.get("dob") or "").strip() else "login")).strip()
        try:
            invite = await _send_invite(org_id, emp, method,
                                        dob=(body.get("dob") or "").strip() or None,
                                        ssn4=(body.get("ssn4") or "").strip() or None,
                                        role_name=role or None, send_email_flag=True, actor="HR")
        except Exception as e:
            invite = {"ok": False, "error": str(e)[:200]}
    return {"employee": emp, "assigned_role": assigned, "login": login, "invite": invite,
            "note": (None if email or not (role or has_scope)
                     else "Role/scope ignored — an email is required to assign a role or create a login.")}


@router.patch("/employees/{emp_id}")
async def hr_update_employee(emp_id: str, body: dict, org_id: str = ORG_ID):
    """Update a person from HR. Updates the roster row (if roster fields are present) and, when a
    role/scope + email is given, re-syncs the app_users assignment so the login stays in step."""
    from app.modules.storeops.router import EMP_FIELDS, update_employee
    res = None
    if any(k in body for k in EMP_FIELDS):
        res = update_employee(emp_id, body, org_id)   # sync handler; raises 404 if missing; org-scoped
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
ONBOARD_STATUSES = ["pending", "submitted", "verified", "na", "returned"]
SEED_STATES = ["NY", "NJ", "DE", "PA", "IL", "CT", "MA", "IN"]
TASK_FIELDS = ["category_id", "key", "label", "description", "owner_role", "doc_url", "doc_label",
               "is_fillable", "requires_upload", "applies_state", "sort_order", "is_active",
               "requires_signature", "form_fields", "is_mandatory", "work_auth"]


# ════════════════════════════════════════════════════════════════════════════════════════════════
# COMPLIANCE PACK (migration 401) — mandatory-doc reopen/reconcile (item 1), upload format enforcement
# (item 2), direct-deposit disclaimer + ABA routing lookup (item 3), work-auth blocking gate (item 4).
# Every threshold/text/provider is config-driven off storeops.tenants (SAP-configurable rule) with a safe
# in-code default when 401 hasn't run yet — nothing here 500s on a pre-401 database.
#
# ROOT CAUSE (item 1): the checklist total/done was ALREADY computed live against the current template
# every time (onboarding_for_employee below joins the live onboarding_task rows with per-employee status —
# there is no per-employee snapshot anywhere in this file). The Brenda Romero / Eduardo Brito "5/5 without
# the IL W-4" vs Jose Utero "6/6 with it" split is a STATE-MATCHING bug, not a snapshot bug:
# onboarding_task.applies_state is an exact 2-letter code, but the intake 'state' field is free text — an
# employee who typed "Illinois" (or any non-2-letter variant) never string-matches 'IL', so the task is
# excluded from BOTH the numerator and denominator of their checklist instead of showing as outstanding.
# _normalize_state below closes that. The second half of the fix is _blocking_gate + onboarding_reconcile:
# there was no MANDATORY flag independent of "is this task currently visible for this employee", and
# nothing proactively reopened/notified a hire whose live total changed after they looked "done".
# ════════════════════════════════════════════════════════════════════════════════════════════════
_STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "washington d c": "DC", "puerto rico": "PR",
}


def _normalize_state(val):
    """Free-text 'which state do you work in' -> a 2-letter code, so 'Illinois' / 'illinois' / 'IL' /
    'Washington D.C.' all resolve the same way a <select> would have. Falls back to the raw uppercased
    input (the OLD behavior) when nothing matches, so an unrecognized value is never worse than before —
    it just still won't match a state-gated task, which is now surfaced via needs_work_state /
    _blocking_gate rather than silently dropped."""
    v = (val or "").strip()
    if not v:
        return None
    up = v.upper()
    if len(up) == 2 and up.isalpha():
        return up
    # strip punctuation (periods in "D.C.", etc.) before collapsing whitespace, so the dict key matches
    key = re.sub(r"[^a-z]+", " ", v.strip().lower()).strip()
    key = re.sub(r"\s+", " ", key)
    return _STATE_ABBR.get(key, up)


def _aba_checksum_valid(routing) -> bool:
    """Standard ABA routing-number checksum (pure function, no I/O, no network): 9 digits, weights
    3-7-1 repeating three times, valid iff the weighted sum is a multiple of 10."""
    d = re.sub(r"\D", "", routing or "")
    if len(d) != 9:
        return False
    n = [int(c) for c in d]
    total = 3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + 1 * (n[2] + n[5] + n[8])
    return total % 10 == 0


_DEFAULT_DD_DISCLAIMER = (
    "By providing bank account information for direct deposit, I certify the routing and account numbers "
    "above are correct. If I submit incorrect information, my employer and the payroll processing company "
    "are NOT liable for any loss, delay, or misdirection of my wages that results.")
_DEFAULT_WORK_AUTH_NOTICE = (
    "Your work-authorization documents (Form I-9 support documents) are still outstanding. "
    "Your payroll will be delayed until these documents are submitted.")
_DEFAULT_UPLOAD_FORMATS = ["pdf", "jpeg"]
_DEFAULT_ROUTING_URL = "https://www.routingnumbers.info/api/data.json?rn={routing}"


def _tenant_row(org_id):
    try:
        rows = (_so().table("tenants").select(
            "onboarding_upload_formats,dd_disclaimer_text,work_auth_notice_text,"
            "routing_lookup_enabled,routing_lookup_url").eq("org_id", org_id).limit(1).execute().data) or []
        return rows[0] if rows else {}
    except Exception:
        return {}   # migration 401 (or storeops.tenants itself) not applied yet — degrade to defaults


def _tenant_config(org_id):
    """Every onboarding config value this pack introduces, org-scoped (SAP-configurable rule), with a
    hardcoded fallback ONLY when migration 401 hasn't run — never a silent divergence once it has."""
    t = _tenant_row(org_id)
    return {
        "upload_allowed_formats": t.get("onboarding_upload_formats") or list(_DEFAULT_UPLOAD_FORMATS),
        "dd_disclaimer_text": t.get("dd_disclaimer_text") or _DEFAULT_DD_DISCLAIMER,
        "work_auth_notice_text": t.get("work_auth_notice_text") or _DEFAULT_WORK_AUTH_NOTICE,
        "routing_lookup_enabled": t.get("routing_lookup_enabled", True) is not False,
    }


_MAGIC = {
    "pdf": (lambda b: b[:5] == b"%PDF-"),
    "jpeg": (lambda b: b[:3] == b"\xff\xd8\xff"),
    "png": (lambda b: b[:8] == b"\x89PNG\r\n\x1a\n"),
}
_EXT_ALIASES = {"jpg": "jpeg", "jpeg": "jpeg", "pdf": "pdf", "png": "png"}


def _sniff_format(data: bytes):
    """The REAL format of an uploaded file, from its magic bytes — a renamed .exe never passes as a PDF.
    Returns a normalized format key ('pdf'|'jpeg'|'png') or None if unrecognized."""
    head = (data or b"")[:16]
    for fmt, test in _MAGIC.items():
        try:
            if test(head):
                return fmt
        except Exception:
            continue
    return None


def _format_allowed(data: bytes, filename: str, allowed):
    """(ok, detected_format) — ok iff the MAGIC BYTES sniff to a format in the tenant's allow-list.
    The extension is used ONLY to label an unrecognized file in the error message — it can never grant
    access on its own. This is deliberate: a renamed .exe (or anything whose header doesn't match a
    known signature) must be rejected even when its filename says ".pdf" — an extension-only check is
    exactly the hole "not just extension" (item 2) closes. A .txt renamed to .pdf, a zip bomb, a script
    — none of these have a PDF/JPEG/PNG magic header, so none of them pass regardless of filename."""
    allow = {_EXT_ALIASES.get(a.strip().lower(), a.strip().lower()) for a in (allowed or _DEFAULT_UPLOAD_FORMATS)}
    sniffed = _sniff_format(data)
    if not sniffed:
        ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
        return False, (_EXT_ALIASES.get(ext, ext) or None)   # unrecognized header — reject regardless of name
    return sniffed in allow, sniffed


def _check_upload_format(org_id, data, filename):
    allowed = _tenant_config(org_id)["upload_allowed_formats"]
    ok, fmt = _format_allowed(data, filename, allowed)
    return ok, fmt, allowed


def _routing_lookup(org_id, routing):
    """ABA checksum (always, local) + an OPTIONAL online bank-name lookup (config-driven provider,
    gracefully degrades to checksum-only when disabled / unreachable / migration 401 not run). Never
    blocks on the network — a slow/dead provider just means no bank-name suggestion, not a failed
    submission. The lookup happens on ENTRY, before storage; nothing here writes to the database."""
    valid = _aba_checksum_valid(routing)
    out = {"routing": re.sub(r"\D", "", routing or ""), "valid_checksum": valid,
           "bank_name": None, "source": "checksum"}
    if not valid:
        return out
    t = _tenant_row(org_id)
    if t.get("routing_lookup_enabled") is False:
        return out
    url_tpl = t.get("routing_lookup_url") or _DEFAULT_ROUTING_URL
    try:
        import requests
        resp = requests.get(url_tpl.replace("{routing}", out["routing"]), timeout=4)
        if resp.ok:
            body = resp.json()
            data = body.get("data") if isinstance(body, dict) else None
            name = (data or {}).get("customer_name") if isinstance(data, dict) else None
            if not name and isinstance(body, dict):
                name = body.get("customer_name") or body.get("bank") or body.get("name")
            if name:
                out["bank_name"] = str(name).strip()
                out["source"] = "lookup"
    except Exception:
        pass   # provider down/unreachable/misconfigured — checksum-only result stands
    return out



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


def _sign_onboard_path(path, expires=3600):
    """A time-limited signed URL for a private onboarding-docs object (uploads + item templates).
    Returns None on any error / empty path so callers can degrade to 'no link'."""
    if not path:
        return None
    try:
        res = get_supabase().storage.from_(ONBOARD_BUCKET).create_signed_url(path, expires)
        if isinstance(res, dict):
            return res.get("signedURL") or res.get("signed_url") or res.get("signedUrl")
        return res
    except Exception:
        return None


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


# ── Per-item DEFAULT TEMPLATE document (migration 080) ──────────────────────────────────────────────
# HR uploads the blank/standard file for an item once; every hire downloads it from their portal. Stored
# in the private onboarding-docs bucket under templates/{org}/{task}/…; onboarding_task.template_path/name
# point at it. Separate from the per-employee UPLOAD (employee_onboarding.document_path) of the completed doc.
@router.post("/onboarding/tasks/{task_id}/template")
async def onboarding_upload_template(task_id: str, file: UploadFile = File(...), org_id: str = ORG_ID):
    """Attach (or replace) the default template document HR sends to every hire for this item."""
    data = await file.read()
    safe = (file.filename or "template").replace("/", "_")
    path = f"templates/{org_id}/{task_id}/{uuid.uuid4().hex}_{safe}"
    c = _ensure_onboard_bucket()
    try:
        c.storage.from_(ONBOARD_BUCKET).upload(path, data, {"content-type": file.content_type or "application/octet-stream"})
    except Exception as e:
        raise HTTPException(500, f"upload failed: {e}")
    try:
        r = (_so().table("onboarding_task").update({"template_path": path, "template_name": safe})
             .eq("org_id", org_id).eq("id", task_id).execute())
    except Exception as e:
        raise HTTPException(400, f"Could not attach template — is migration 080 applied? {str(e)[:140]}")
    if not (r.data or []):
        raise HTTPException(404, "task not found")
    return {"ok": True, "template_name": safe, "template_url": _sign_onboard_path(path)}


@router.get("/onboarding/tasks/{task_id}/template")
def onboarding_get_template(task_id: str, org_id: str = ORG_ID):
    """A 1-hour signed URL to view/download an item's default template document."""
    try:
        rows = (_so().table("onboarding_task").select("template_path,template_name")
                .eq("org_id", org_id).eq("id", task_id).limit(1).execute().data) or []
    except Exception:
        rows = []
    if not rows or not rows[0].get("template_path"):
        raise HTTPException(404, "no template")
    url = _sign_onboard_path(rows[0]["template_path"])
    if not url:
        raise HTTPException(500, "could not sign url")
    return {"url": url, "template_name": rows[0].get("template_name")}


@router.delete("/onboarding/tasks/{task_id}/template")
def onboarding_delete_template(task_id: str, org_id: str = ORG_ID):
    """Detach an item's default template (clears the pointer + best-effort deletes the stored object)."""
    rows = (_so().table("onboarding_task").select("template_path")
            .eq("org_id", org_id).eq("id", task_id).limit(1).execute().data) or []
    path = rows[0].get("template_path") if rows else None
    try:
        _so().table("onboarding_task").update({"template_path": None, "template_name": None}) \
            .eq("org_id", org_id).eq("id", task_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not remove template: {str(e)[:140]}")
    if path:
        try:
            get_supabase().storage.from_(ONBOARD_BUCKET).remove([path])
        except Exception:
            pass
    return {"ok": True}


# ── Item 6: per-item COMPLETED SAMPLE (migration 401) — mirrors the template pattern above exactly, one
# column pair (sample_path/sample_name) instead of (template_path/template_name). The employee sees "view
# completed sample" before filling; HR reviews a submission side-by-side with it (frontend link-through —
# the completeness check from mig 082 is unchanged, this is a human-review aid layered on top).
@router.post("/onboarding/tasks/{task_id}/sample")
async def onboarding_upload_sample(task_id: str, file: UploadFile = File(...), org_id: str = ORG_ID):
    """Attach (or replace) a COMPLETED SAMPLE for this item — what a correctly filled-out submission
    looks like, uploaded once per tenant by an admin."""
    data = await file.read()
    safe = (file.filename or "sample").replace("/", "_")
    path = f"templates/{org_id}/{task_id}/sample_{uuid.uuid4().hex}_{safe}"
    c = _ensure_onboard_bucket()
    try:
        c.storage.from_(ONBOARD_BUCKET).upload(path, data, {"content-type": file.content_type or "application/octet-stream"})
    except Exception as e:
        raise HTTPException(500, f"upload failed: {e}")
    try:
        r = (_so().table("onboarding_task").update({"sample_path": path, "sample_name": safe})
             .eq("org_id", org_id).eq("id", task_id).execute())
    except Exception as e:
        raise HTTPException(400, f"Could not attach sample — is migration 401 applied? {str(e)[:140]}")
    if not (r.data or []):
        raise HTTPException(404, "task not found")
    return {"ok": True, "sample_name": safe, "sample_url": _sign_onboard_path(path)}


@router.get("/onboarding/tasks/{task_id}/sample")
def onboarding_get_sample(task_id: str, org_id: str = ORG_ID):
    """A 1-hour signed URL to view an item's completed-sample document."""
    try:
        rows = (_so().table("onboarding_task").select("sample_path,sample_name")
                .eq("org_id", org_id).eq("id", task_id).limit(1).execute().data) or []
    except Exception:
        rows = []
    if not rows or not rows[0].get("sample_path"):
        raise HTTPException(404, "no sample document")
    url = _sign_onboard_path(rows[0]["sample_path"])
    if not url:
        raise HTTPException(500, "could not sign url")
    return {"url": url, "sample_name": rows[0].get("sample_name")}


@router.delete("/onboarding/tasks/{task_id}/sample")
def onboarding_delete_sample(task_id: str, org_id: str = ORG_ID):
    """Detach an item's completed sample (clears the pointer + best-effort deletes the stored object)."""
    rows = (_so().table("onboarding_task").select("sample_path")
            .eq("org_id", org_id).eq("id", task_id).limit(1).execute().data) or []
    path = rows[0].get("sample_path") if rows else None
    try:
        _so().table("onboarding_task").update({"sample_path": None, "sample_name": None}) \
            .eq("org_id", org_id).eq("id", task_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not remove sample: {str(e)[:140]}")
    if path:
        try:
            get_supabase().storage.from_(ONBOARD_BUCKET).remove([path])
        except Exception:
            pass
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
    mandatory_total, mandatory_done, wa_pending = 0, 0, []
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
                          "submitted_at": rec.get("submitted_at"),
                          # mig 082 doc flow (None-safe on a pre-082 database)
                          "missing_fields": rec.get("missing_fields"), "returned_reason": rec.get("returned_reason"),
                          "returned_at": rec.get("returned_at"), "returned_by": rec.get("returned_by"),
                          "signed_at": rec.get("signed_at"), "signed_name": rec.get("signed_name"),
                          "has_signature": bool(rec.get("signature_path")),
                          "form_data": rec.get("form_data"), "validation": rec.get("validation"),
                          # migration 401: mandatory flag + work-auth blocking flag + completed-sample link
                          "is_mandatory": t.get("is_mandatory", True) is not False,
                          "work_auth": bool(t.get("work_auth")),
                          "sample_name": t.get("sample_name"), "sample_url": _sign_onboard_path(t.get("sample_path"))})
            total += 1
            is_mand = t.get("is_mandatory", True) is not False
            if is_mand:
                mandatory_total += 1
            ok_done = status in ("verified", "na")
            if ok_done:
                done += 1
                if is_mand:
                    mandatory_done += 1
            elif t.get("work_auth"):
                wa_pending.append(t.get("label"))
        if tasks:
            cats.append({**{k: c[k] for k in c if k != "tasks"}, "tasks": tasks})
    emp = (so.table("employees").select("name,home_store").eq("org_id", org_id)
           .eq("employee_id", employee_id).limit(1).execute().data) or [{}]
    wf = (prof or {}).get("workflow_status") or "invited"
    stored = dict((prof or {}).get("intake_data") or {})
    pub_fields = _public_intake_fields(org_id)
    intake_values = {f["key"]: stored.get(f["key"], "") for f in pub_fields if not f["sensitive"]}
    sensitive_on_file = [f["label"] for f in pub_fields if f["sensitive"] and str(stored.get(f["key"], "")).strip()]
    tenant_cfg = _tenant_config(org_id)
    return {"ready": True, "employee_id": employee_id, "employee_name": emp[0].get("name"),
            "work_state": work_state, "profile": _public_profile(prof),
            "needs_work_state": bool(has_state_tasks and not work_state),
            "workflow_status": wf, "workflow_label": STATUS_LABELS.get(wf, wf),
            "workflow_statuses": [{"key": s, "label": STATUS_LABELS[s]} for s in WORKFLOW_STATUSES],
            "invite_method": (prof or {}).get("invite_method"),
            "intake_submitted": bool((prof or {}).get("intake_submitted_at")),
            "intake_fields": pub_fields, "intake_values": intake_values,
            "sensitive_on_file": sensitive_on_file,
            "categories": cats, "progress": {"total": total, "done": done},
            # migration 401 (items 1 / 3 / 4): mandatory-only progress, work-auth blocking notice,
            # DD-disclaimer status, and the tenant's configurable text/format settings in one place so
            # every portal surface (HR view, logged-in employee, credential-less token) can show them.
            "mandatory_progress": {"total": mandatory_total, "done": mandatory_done},
            "work_auth_pending": wa_pending, "work_auth_notice": tenant_cfg["work_auth_notice_text"] if wa_pending else None,
            "dd_disclaimer_signed": bool((prof or {}).get("dd_disclaimer_signed_at")),
            "tenant_config": tenant_cfg,
            "owner_labels": OWNER_ROLE_LABELS, "states": SEED_STATES}


@router.patch("/onboarding/employee/{employee_id}")
def onboarding_set_profile(employee_id: str, body: dict, org_id: str = ORG_ID):
    """Set the employee's work_state (drives which state tax form shows)."""
    upd = {"org_id": org_id, "employee_id": employee_id}
    if "work_state" in body:
        upd["work_state"] = _normalize_state(body.get("work_state"))
    try:
        _so().table("employee_onboarding_profile").upsert(upd, on_conflict="org_id,employee_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save — is migration 073 applied? {e}")
    return {"ok": True, "work_state": upd.get("work_state")}


# ── Sensitive PII: encrypted at rest, revealed only to HR / admins (audited) ───────────────────
def _rbac_enforced() -> bool:
    try:
        rows = get_supabase().schema("storeops").table("app_config").select("rbac_enabled") \
            .eq("id", 1).limit(1).execute().data or []
        return bool(rows and rows[0].get("rbac_enabled"))
    except Exception:
        return False


def _require_hr_or_admin(authorization: str):
    """Resolve the caller from their token and confirm they may see sensitive employee PII (admin,
    super-admin, or an HR-titled role). Returns (org_id, email, role). Resolves the caller's OWN
    tenant from auth_id (globally unique) so a reveal only ever exposes that tenant's data. When
    login enforcement is OFF (open app) an unauthenticated caller is allowed for parity with the
    rest of the app, but the access is still audited."""
    from app.modules.core.router import _uid_from_token
    uid = _uid_from_token(authorization)
    if not uid:
        if _rbac_enforced():
            raise HTTPException(401, "Sign in as HR or an admin to view sensitive information.")
        return (ORG_ID, "(open-app)", "open")
    rows = (get_supabase().schema("storeops").table("app_users")
            .select("org_id,email,role,super_admin").eq("auth_id", uid).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(403, "Your login isn't recognized.")
    u = rows[0]
    role = (u.get("role") or "").lower()
    ok = bool(u.get("super_admin")) or role in ("admin",) or "hr" in role
    if not ok:
        # allow a custom role explicitly scoped to HR management (permissions.hr == true)
        try:
            rr = (get_supabase().schema("storeops").table("roles").select("permissions")
                  .eq("org_id", u.get("org_id") or ORG_ID).eq("name", u.get("role")).limit(1).execute().data) or []
            if ((rr[0].get("permissions") if rr else {}) or {}).get("hr"):
                ok = True
        except Exception:
            pass
    if not ok:
        raise HTTPException(403, "Only HR managers and admins can view sensitive employee information.")
    return (u.get("org_id") or ORG_ID, u.get("email"), role)


@router.get("/onboarding/employee/{employee_id}/sensitive")
def onboarding_reveal_sensitive(employee_id: str, authorization: str = Header(default="")):
    """Decrypted sensitive intake values (bank / SSN / A-Number) for an authorized HR manager or
    admin — the ONLY path that returns these values; everyone else sees just 'on file' labels. The
    access is written to the onboarding_event audit trail (who viewed which fields, when)."""
    org_id, email, role = _require_hr_or_admin(authorization)
    so = _so()
    prof = _get_profile(so, org_id, employee_id) or {}
    stored = dict(prof.get("intake_data") or {})
    out = {}
    for f in _public_intake_fields(org_id):
        if not f.get("sensitive"):
            continue
        raw = stored.get(f["key"], "")
        if not str(raw).strip():
            continue
        val = crypto.decrypt(raw)
        out[f["key"]] = {
            "label": f["label"],
            "value": ("(unavailable — encryption key rotated/lost)" if val is None else val),
            "encrypted": crypto.is_encrypted(raw)}
    _log_event(org_id, employee_id, "sensitive_viewed", actor=email,
               detail={"by": email, "role": role, "fields": sorted(out.keys())})
    return {"employee_id": employee_id, "values": out,
            "encryption_enabled": crypto.is_enabled()}


@router.get("/security-status")
def hr_security_status(authorization: str = Header(default="")):
    """Whether sensitive-field encryption is active (a key is configured). Admins/HR read it so the
    UI can warn that PII is stored in the clear until FIELD_ENCRYPTION_KEY is set."""
    _require_hr_or_admin(authorization)
    return {"encryption_enabled": crypto.is_enabled()}


@router.post("/onboarding/encrypt-existing")
def onboarding_encrypt_existing(authorization: str = Header(default="")):
    """One-time (idempotent) backfill: encrypt any sensitive intake values still stored as plaintext,
    for every employee in the caller's tenant. Admin/HR only. No-op per value if already encrypted or
    if no key is configured. Safe to run repeatedly."""
    org_id, email, role = _require_hr_or_admin(authorization)
    if not crypto.is_enabled():
        raise HTTPException(400, "Set FIELD_ENCRYPTION_KEY on the backend first, then run this.")
    so = _so()
    sens_keys = [f["key"] for f in _public_intake_fields(org_id) if f.get("sensitive")]
    if not sens_keys:
        return {"ok": True, "profiles_scanned": 0, "values_encrypted": 0, "note": "no sensitive fields configured"}
    profiles = (so.table("employee_onboarding_profile").select("employee_id,intake_data")
                .eq("org_id", org_id).execute().data) or []
    scanned, enc_count = 0, 0
    for p in profiles:
        data = dict(p.get("intake_data") or {})
        changed = False
        for k in sens_keys:
            v = data.get(k)
            if v and str(v).strip() and not crypto.is_encrypted(v):
                data[k] = crypto.encrypt(v)
                changed = True
                enc_count += 1
        scanned += 1
        if changed:
            try:
                so.table("employee_onboarding_profile").update({"intake_data": data}) \
                    .eq("org_id", org_id).eq("employee_id", p["employee_id"]).execute()
            except Exception:
                pass
    _log_event(org_id, None, "sensitive_backfill_encrypted", actor=email,
               detail={"by": email, "profiles": scanned, "encrypted": enc_count})
    return {"ok": True, "profiles_scanned": scanned, "values_encrypted": enc_count}


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
    # Item 2: server-side format enforcement (magic bytes, not just extension), tenant-configurable
    # allow-list (default pdf/jpeg — storeops.tenants.onboarding_upload_formats). Applies to every
    # onboarding upload (driver's license, every filled/signed document) since they all funnel through
    # this one function regardless of which of the 3 upload endpoints (HR / logged-in employee / public
    # token) was called.
    ok_fmt, detected_fmt, allowed_fmts = _check_upload_format(org_id, data, safe)
    if not ok_fmt:
        allowed_label = "/".join(a.upper() for a in allowed_fmts)
        raise HTTPException(400, f"Only {allowed_label} files are accepted here"
                                  + (f" — this looks like a {detected_fmt.upper()} file" if detected_fmt else
                                     " — this file's format could not be recognized") + f". Please upload a {allowed_label} file.")
    path = f"{org_id}/{employee_id}/{uuid.uuid4().hex}_{safe}"
    c = _ensure_onboard_bucket()
    try:
        c.storage.from_(ONBOARD_BUCKET).upload(path, data, {"content-type": file.content_type or "application/octet-stream"})
    except Exception as e:
        raise HTTPException(500, f"upload failed: {e}")
    # Completeness + signature check (mig 082). Only FILLABLE PDFs are machine-checkable; flat scans
    # and images pass through as 'submitted' for HR to eyeball (HR can still Return them manually).
    task = _task_row(org_id, task_id)
    check = _pdf_form_check(data) if safe.lower().endswith(".pdf") else {"checkable": False, "reason": "not a PDF"}
    missing = list(check.get("missing") or [])
    if check.get("checkable"):
        if (check.get("fields") or 0) > 0 and (check.get("filled") or 0) == 0:
            missing.insert(0, "The form came back blank — please fill it out")
        if task.get("requires_signature", True) and check.get("signed") is False:
            missing.append("Signature")
    row = {"org_id": org_id, "employee_id": employee_id, "task_id": task_id, "status": "submitted",
           "document_path": path, "document_name": safe, "submitted_at": _now_iso(), "updated_at": _now_iso(),
           "note": (f"uploaded by {who}" if who else None),
           "validation": {**check, "missing": missing},
           "missing_fields": None, "returned_reason": None, "returned_at": None, "returned_by": None}
    if missing:
        row.update({"status": "returned", "missing_fields": missing,
                    "returned_reason": "Automatic check: the document came back incomplete.",
                    "returned_at": _now_iso(), "returned_by": "system"})
    try:
        _so().table("employee_onboarding").upsert(row, on_conflict="org_id,employee_id,task_id").execute()
    except Exception:
        # pre-082 database — fall back to the legacy row shape so uploads never break
        legacy = {k: row[k] for k in ("org_id", "employee_id", "task_id", "document_path",
                                      "document_name", "submitted_at", "updated_at", "note")}
        legacy["status"] = "submitted"
        try:
            _so().table("employee_onboarding").upsert(legacy, on_conflict="org_id,employee_id,task_id").execute()
        except Exception as e:
            raise HTTPException(400, f"Could not record upload — is migration 073 applied? {e}")
        return {"ok": True, "document_name": safe, "status": "submitted"}
    if missing:
        _log_event(org_id, employee_id, "doc_returned", actor="system",
                   detail={"task": task.get("label"), "missing": missing, "auto": True})
        emailed = await _notify_return(org_id, employee_id, task, missing, row["returned_reason"])
        return {"ok": True, "document_name": safe, "status": "returned", "missing": missing, "emailed": emailed,
                "note": "This document looks incomplete — it was returned with the missing items listed."}
    return {"ok": True, "document_name": safe, "status": "submitted",
            "checked": bool(check.get("checkable")),
            "note": None if check.get("checkable") else
            "Not machine-checkable (scan/photo or flat PDF) — HR will review the signature by eye."}


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
    bundle = _onboarding_bundle(prof["org_id"], prof["employee_id"])
    bundle.pop("employee_id", None)   # don't leak the internal id to a credential-less caller
    return bundle


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
    res = await _do_onboard_upload(prof["org_id"], prof["employee_id"], task_id, file, "employee")
    _recompute_status(_so(), prof["org_id"], prof["employee_id"], actor="employee")
    return res


# ════════════════════════════════════════════════════════════════════════════════════════════════
# ONBOARDING WORKFLOW (migration 077) — connects the checklist to the employee portal.
#   • A per-hire STATE MACHINE (invited → in_progress → docs_submitted → docs_verified → provisioned
#     → active) with an append-only audit trail (onboarding_event); any step is HR-overridable.
#   • Two invite paths: a no-login token LINK (DOB/last-4 gate) OR a temp portal LOGIN (way 2).
#   • STRUCTURED intake capture (configurable onboarding_intake_field) → stored as intake_data AND
#     propagated onto storeops.employees; "which state are you in?" drives the tax-form filter.
#   • Auto-provisioning (create login + assign role + email credentials), gated on docs_verified.
# All storeops.*; every endpoint degrades gracefully (clear 400, never 500) if 077 isn't applied.
# ════════════════════════════════════════════════════════════════════════════════════════════════
WORKFLOW_STATUSES = ["invited", "in_progress", "docs_submitted", "docs_verified", "provisioned", "active"]
STATUS_ORDER = {s: i for i, s in enumerate(WORKFLOW_STATUSES)}
STATUS_LABELS = {
    "invited": "Invited", "in_progress": "In progress", "docs_submitted": "Docs submitted",
    "docs_verified": "Docs verified", "provisioned": "Provisioned", "active": "Active",
}
INTAKE_FIELD_COLS = ["key", "label", "section", "field_type", "options", "required",
                     "propagate_to", "sensitive", "help_text", "sort_order", "is_active"]
# storeops.employees columns an intake field is allowed to propagate into (allow-list — a config
# row can never write an arbitrary column).
_PROPAGATABLE = {"legal_name", "address_line1", "address_line2", "city", "state", "zip",
                 "date_of_birth", "phone", "emergency_name", "emergency_phone", "emergency_relation"}


# ── Intake-field config (the tenant-customizable capture form) ───────────────────────────────────
def _intake_fields(org_id, active_only=True):
    try:
        rows = (_so().table("onboarding_intake_field").select("*").eq("org_id", org_id)
                .order("sort_order").execute().data) or []
    except Exception:
        return []
    return [r for r in rows if r.get("is_active", True)] if active_only else rows


def _public_intake_fields(org_id):
    """Field definitions safe to render in a portal (no internal flags)."""
    return [{"key": f["key"], "label": f["label"], "section": f.get("section") or "personal",
             "field_type": f.get("field_type") or "text", "options": f.get("options"),
             "required": bool(f.get("required")), "sensitive": bool(f.get("sensitive")),
             "help_text": f.get("help_text")} for f in _intake_fields(org_id)]


@router.get("/onboarding/intake-fields")
def intake_fields_list(include_inactive: bool = False, org_id: str = ORG_ID):
    """The configurable employee-intake capture form. ready:false if 077 not applied."""
    try:
        rows = (_so().table("onboarding_intake_field").select("*").eq("org_id", org_id)
                .order("sort_order").execute().data)
    except Exception:
        return {"ready": False, "fields": [], "sections": ["personal", "address", "emergency", "work_eligibility", "tax", "direct_deposit", "policies", "custom"]}
    if not include_inactive:
        rows = [r for r in (rows or []) if r.get("is_active", True)]
    return {"ready": True, "fields": rows or [],
            "sections": ["personal", "address", "emergency", "work_eligibility", "tax", "direct_deposit", "policies", "custom"],
            "propagatable": sorted(_PROPAGATABLE)}


@router.post("/onboarding/intake-fields")
def intake_field_save(body: dict, org_id: str = ORG_ID):
    label = (body.get("label") or "").strip()
    if not label:
        raise HTTPException(400, "label required")
    row = {k: body[k] for k in INTAKE_FIELD_COLS if k in body}
    row.update({"org_id": org_id, "label": label})
    row.setdefault("key", _slug(label))
    row["key"] = _slug(row["key"])
    prop = (row.get("propagate_to") or "").strip() or None
    if prop and prop not in _PROPAGATABLE:
        raise HTTPException(400, f"propagate_to must be one of {sorted(_PROPAGATABLE)} (or blank)")
    row["propagate_to"] = prop
    try:
        r = so_upsert("onboarding_intake_field", row, "org_id,key")
    except Exception as e:
        raise HTTPException(400, f"Could not save field — is migration 077 applied? {e}")
    return (r or [row])[0]


@router.patch("/onboarding/intake-fields/{field_id}")
def intake_field_update(field_id: str, body: dict, org_id: str = ORG_ID):
    upd = {k: body[k] for k in INTAKE_FIELD_COLS if k in body}
    if "propagate_to" in upd:
        prop = (upd.get("propagate_to") or "").strip() or None
        if prop and prop not in _PROPAGATABLE:
            raise HTTPException(400, f"propagate_to must be one of {sorted(_PROPAGATABLE)} (or blank)")
        upd["propagate_to"] = prop
    r = _so().table("onboarding_intake_field").update(upd).eq("org_id", org_id).eq("id", field_id).execute()
    return (r.data or [{}])[0]


@router.delete("/onboarding/intake-fields/{field_id}")
def intake_field_delete(field_id: str, org_id: str = ORG_ID):
    _so().table("onboarding_intake_field").delete().eq("org_id", org_id).eq("id", field_id).execute()
    return {"ok": True}


# ── Workflow status + audit trail ────────────────────────────────────────────────────────────────
def _log_event(org_id, employee_id, event_type, *, from_status=None, to_status=None,
               actor=None, reason=None, is_override=False, detail=None):
    try:
        _so().table("onboarding_event").insert({
            "org_id": org_id, "employee_id": employee_id, "event_type": event_type,
            "from_status": from_status, "to_status": to_status, "actor": actor,
            "reason": reason, "is_override": is_override, "detail": detail}).execute()
    except Exception:
        pass  # audit is best-effort; never block the operation


def _set_status(so, org_id, employee_id, to_status, *, actor=None, reason=None, is_override=False):
    prof = _get_profile(so, org_id, employee_id)
    frm = (prof or {}).get("workflow_status") or "invited"
    if to_status == frm:
        return frm
    upd = {"org_id": org_id, "employee_id": employee_id, "workflow_status": to_status}
    if to_status == "provisioned":
        upd["provisioned_at"] = _now_iso()
    try:
        so.table("employee_onboarding_profile").upsert(upd, on_conflict="org_id,employee_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not update status — is migration 077 applied? {e}")
    _log_event(org_id, employee_id, "override" if is_override else "status_change",
               from_status=frm, to_status=to_status, actor=actor, reason=reason, is_override=is_override)
    return to_status


def _employee_tasks(org_id, employee_id):
    """The employee-owned, state-applicable tasks (with merged per-employee status)."""
    data = onboarding_for_employee(employee_id, org_id=org_id)
    return [t for c in data.get("categories", []) for t in c.get("tasks", [])
            if t.get("owner_role") == "employee"]


def _recompute_status(so, org_id, employee_id, actor="system"):
    """Derive forward progress from task + intake state. Never auto-advances past docs_verified
    (provisioning is explicit) and never moves a provisioned/active hire backward."""
    prof = _get_profile(so, org_id, employee_id)
    if not prof:
        return None
    cur = prof.get("workflow_status") or "invited"
    if STATUS_ORDER.get(cur, 0) >= STATUS_ORDER["provisioned"]:
        return cur
    tasks = _employee_tasks(org_id, employee_id)
    intake_required = bool(_intake_fields(org_id))
    intake_done = bool(prof.get("intake_submitted_at")) or not intake_required
    touched = lambda t: t.get("status") in ("submitted", "verified", "na")
    ok = lambda t: t.get("status") in ("verified", "na")
    any_touched = bool(prof.get("intake_submitted_at")) or any(touched(t) for t in tasks)
    all_touched = intake_done and (all(touched(t) for t in tasks) if tasks else True)
    all_verified = intake_done and (all(ok(t) for t in tasks) if tasks else True)
    has_work = bool(tasks) or intake_required
    if all_verified and has_work:
        target = "docs_verified"
    elif all_touched and has_work and (tasks or prof.get("intake_submitted_at")):
        target = "docs_submitted"
    elif any_touched:
        target = "in_progress"
    else:
        target = cur
    if STATUS_ORDER.get(target, 0) > STATUS_ORDER.get(cur, 0):
        return _set_status(so, org_id, employee_id, target, actor=actor)
    return cur


# ── Structured intake capture + propagation ──────────────────────────────────────────────────────
def _apply_intake(org_id, employee_id, data: dict, actor="employee"):
    """Validate submitted values against the active field config, merge into intake_data, propagate
    the mapped operational fields onto storeops.employees, sync work_state, mark the personal-info
    task submitted, and advance the workflow. Returns {ok, propagated:[...]}."""
    so = _so()
    fields = _intake_fields(org_id)
    if not fields:
        raise HTTPException(400, "No intake form is configured — run migration 077 (or add fields in HR → Onboarding).")
    by_key = {f["key"]: f for f in fields}
    missing = [f["label"] for f in fields if f.get("required")
               and not str(data.get(f["key"], "")).strip()]
    if missing:
        raise HTTPException(400, "Please fill in: " + ", ".join(missing))
    prof = _get_profile(so, org_id, employee_id) or {}
    # Item 3a: direct-deposit disclaimer gate. If this submission carries a value for any field in the
    # direct_deposit section, the employee must have initialed the disclaimer — either already on file,
    # or included in THIS submission as data['dd_disclaimer_initials'] (a protocol-level key, not a
    # configured onboarding_intake_field — always accepted, never propagated/stored as a regular field).
    dd_keys_present = [k for k in (data or {}) if by_key.get(k, {}).get("section") == "direct_deposit"
                       and str(data.get(k, "")).strip()]
    if dd_keys_present and not prof.get("dd_disclaimer_signed_at"):
        initials = (data.get("dd_disclaimer_initials") or "").strip()
        if not initials:
            raise HTTPException(400, "Please type your initials to acknowledge the direct-deposit "
                                      "disclaimer before saving your bank details.")
        _sign_dd_disclaimer(org_id, employee_id, initials, actor=actor)
    stored = dict(prof.get("intake_data") or {})
    emp_upd, propagated = {}, []
    for k, v in (data.items() if isinstance(data, dict) else []):
        f = by_key.get(k)
        if not f:
            continue
        val = (str(v).strip() if v is not None else "")
        if f.get("sensitive"):
            # Sensitive PII (SSN/bank/A-Number): store ENCRYPTED, and never propagate it onto the
            # employees table (it stays only in intake_data, as ciphertext). Blank stays blank.
            stored[k] = crypto.encrypt(val) if val else val
            continue
        stored[k] = val
        col = f.get("propagate_to")
        if col and col in _PROPAGATABLE and val != "":
            emp_upd[col] = _normalize_state(val) if col == "state" else val
            propagated.append(col)
    # persist intake_data + timestamp
    pupd = {"org_id": org_id, "employee_id": employee_id, "intake_data": stored,
            "intake_submitted_at": _now_iso()}
    # "which state are you in?" also drives the tax-form filter (work_state). NORMALIZED (item 1 root
    # cause fix) so "Illinois"/"illinois"/"IL" all resolve to the same 2-letter code onboarding_task.
    # applies_state expects — a raw free-text mismatch used to make a mandated state form silently
    # disappear from BOTH the numerator and denominator of the checklist instead of showing as outstanding.
    state_val = _normalize_state(data.get("state") or data.get("work_state") or "")
    if state_val:
        pupd["work_state"] = state_val
    try:
        so.table("employee_onboarding_profile").upsert(pupd, on_conflict="org_id,employee_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save your information — is migration 077 applied? {e}")
    if emp_upd:
        try:
            so.table("employees").update(emp_upd).eq("org_id", org_id).eq("employee_id", employee_id).execute()
        except Exception:
            pass  # propagation is best-effort; intake_data still holds the source of truth
    # mark the personal-info checklist task as submitted (data captured in-app, no PDF needed)
    try:
        pi = (so.table("onboarding_task").select("id").eq("org_id", org_id)
              .eq("key", "personal_info").limit(1).execute().data) or []
        if pi:
            so.table("employee_onboarding").upsert(
                {"org_id": org_id, "employee_id": employee_id, "task_id": pi[0]["id"],
                 "status": "submitted", "submitted_at": _now_iso(), "updated_at": _now_iso(),
                 "note": "captured via intake form"}, on_conflict="org_id,employee_id,task_id").execute()
    except Exception:
        pass
    _log_event(org_id, employee_id, "intake_submitted", actor=actor,
               detail={"fields": list(stored.keys()), "propagated": propagated})
    _recompute_status(so, org_id, employee_id, actor=actor)
    return {"ok": True, "propagated": propagated, "work_state": pupd.get("work_state")}


def _set_work_state(org_id, employee_id, state, actor="employee"):
    st = _normalize_state(state)
    _so().table("employee_onboarding_profile").upsert(
        {"org_id": org_id, "employee_id": employee_id, "work_state": st},
        on_conflict="org_id,employee_id").execute()
    _log_event(org_id, employee_id, "state_set", actor=actor, detail={"work_state": st})
    return st


# ── Direct-deposit disclaimer (item 3a) + ABA routing-number lookup (item 3b) ───────────────────────
def _sign_dd_disclaimer(org_id, employee_id, initials, actor="employee"):
    """Store the employee's typed initials + a timestamp in the audit trail (onboarding_event) AND on
    the profile (dd_disclaimer_initials/signed_at — queryable without decrypting anything). Called either
    directly (dedicated endpoint) or inline from _apply_intake when DD fields are submitted for the
    first time."""
    initials = (initials or "").strip()
    if not initials:
        raise HTTPException(400, "Type your initials to confirm the direct-deposit disclaimer.")
    now = _now_iso()
    try:
        _so().table("employee_onboarding_profile").upsert(
            {"org_id": org_id, "employee_id": employee_id,
             "dd_disclaimer_initials": initials[:12], "dd_disclaimer_signed_at": now},
            on_conflict="org_id,employee_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save — is migration 401 applied? {e}")
    _log_event(org_id, employee_id, "dd_disclaimer_signed", actor=actor,
               detail={"initials": initials[:12], "at": now})
    return {"ok": True, "signed_at": now, "initials": initials[:12]}


@router.post("/onboarding/me/dd-disclaimer")
def onboarding_me_dd_disclaimer(body: dict, authorization: str = Header(default="")):
    me = _me_from_token(authorization)
    return _sign_dd_disclaimer(me["org_id"], me["employee_id"], (body or {}).get("initials"), actor="employee")


@router.post("/public/onboarding/{token}/dd-disclaimer")
def public_onboarding_dd_disclaimer(token: str, body: dict):
    prof = _profile_by_token(token)
    if not _token_valid(prof):
        raise HTTPException(404, "This onboarding link is invalid or has expired.")
    if not _check_gate(prof, (body or {}).get("value")):
        raise HTTPException(403, "Identity check failed.")
    return _sign_dd_disclaimer(prof["org_id"], prof["employee_id"], (body or {}).get("initials"), actor="employee")


@router.get("/onboarding/me/routing-lookup")
def onboarding_me_routing_lookup(routing: str, authorization: str = Header(default="")):
    """ABA checksum + an optional bank-name lookup so the employee can confirm 'You're entering an
    account at CHASE — correct?' before submitting. Never blocks on a slow/dead provider (see
    _routing_lookup) — this is a UX confirmation aid, not a validation gate on submission."""
    me = _me_from_token(authorization)
    return _routing_lookup(me["org_id"], routing)


@router.get("/public/onboarding/{token}/routing-lookup")
def public_onboarding_routing_lookup(token: str, routing: str, value: str = ""):
    prof = _profile_by_token(token)
    if not _token_valid(prof):
        raise HTTPException(404, "This onboarding link is invalid or has expired.")
    if not _check_gate(prof, value):
        raise HTTPException(403, "Identity check failed.")
    return _routing_lookup(prof["org_id"], routing)


# ── Invite engine (way 1 = token link · way 2 = temp portal login) ───────────────────────────────
def _invite_email_html(first_name, method, url, email=None, temp_pw=None, task_labels=None):
    greet = f"Hi {first_name}," if first_name else "Hi,"
    docs = ""
    if task_labels:
        items = "".join(f"<li>{t}</li>" for t in task_labels[:30])
        docs = ("<p style='margin:16px 0 6px'><b>What you'll complete:</b></p>"
                f"<ul style='margin:0 0 8px 18px;padding:0'>{items}</ul>")
    if method == "login":
        creds = (f"<p style='margin:12px 0'>Sign in with:<br><b>Email:</b> {email}<br>"
                 f"<b>Temporary password:</b> {temp_pw}</p>"
                 "<p style='font-size:13px;color:#555'>You'll be asked to set your own password on first sign-in.</p>")
        cta = f"<a href='{url}' style='display:inline-block;background:#111;color:#fff;padding:11px 20px;border-radius:8px;text-decoration:none'>Open the employee portal</a>"
    else:
        creds = "<p style='font-size:13px;color:#555'>You'll verify your identity (date of birth) to open your checklist — no password needed.</p>"
        cta = f"<a href='{url}' style='display:inline-block;background:#111;color:#fff;padding:11px 20px;border-radius:8px;text-decoration:none'>Start onboarding</a>"
    return (f"<div style='font-family:system-ui,Arial,sans-serif;max-width:560px;margin:auto;color:#111'>"
            f"<p>{greet}</p><p>Welcome aboard! Please complete your new-hire onboarding — fill in your "
            f"personal information and upload your signed forms.</p>{docs}{creds}<p style='margin:18px 0'>{cta}</p>"
            f"<p style='font-size:12px;color:#888'>If the button doesn't work, copy this link:<br>{url}</p></div>")


async def _send_invite(org_id, employee, method="link", *, dob=None, ssn4=None, role_name=None,
                       expires_days=30, actor="HR", send_email_flag=True):
    """Prepare + (optionally) email an onboarding invite. Returns a per-employee result dict.
    method='link' → mint a token portal (needs a DOB or last-4 gate).
    method='login' → ensure an app_users role + a Supabase login, email the temp credentials."""
    so = _so()
    email = (employee.get("email") or "").strip().lower()
    employee_id = employee.get("employee_id")
    first = (str(employee.get("name") or "").split() or [""])[0]
    if not employee_id:
        return {"employee_id": None, "ok": False, "error": "no employee_id"}
    # seed a profile row + stamp invite metadata / status
    base = {"org_id": org_id, "employee_id": employee_id, "invite_method": method,
            "invited_at": _now_iso()}
    result = {"employee_id": employee_id, "name": employee.get("name"), "email": email, "method": method}

    if method == "login":
        if not email:
            return {**result, "ok": False, "error": "email required for a login invite"}
        try:
            from app.modules.core.router import assign_role, create_login as core_create_login
            # ensure an app_users row exists so create_login can attach the auth account
            await assign_role({"email": email, "full_name": employee.get("name"),
                               "role": (role_name or "sales_rep"), "employee_id": employee_id}, org_id)
            login = await core_create_login({"email": email}, org_id)
        except Exception as e:
            return {**result, "ok": False, "error": str(e)[:200]}
        url = f"{settings.APP_PUBLIC_URL}/portal"
        try:
            so.table("employee_onboarding_profile").upsert(base, on_conflict="org_id,employee_id").execute()
        except Exception as e:
            return {**result, "ok": False, "error": f"profile save failed (migration 077?): {str(e)[:120]}"}
        result["temp_password"] = login.get("temp_password")
        result["portal_url"] = url
    else:  # link
        gate_kind = "dob" if dob else ("ssn4" if ssn4 else None)
        gate_val = dob or ssn4
        if not gate_kind:
            return {**result, "ok": False,
                    "error": "a date of birth (or last-4 SSN) is required for a link invite — use the login method instead"}
        token = secrets.token_urlsafe(24)
        row = {**base, "access_token": token, "verify_kind": gate_kind, "token_active": True,
               "verify_dob": None, "verify_ssn4": None, "token_expires_at": None}
        if gate_kind == "dob":
            row["verify_dob"] = str(gate_val)[:10]
        else:
            row["verify_ssn4"] = re.sub(r"\D", "", str(gate_val))[-4:]
        if expires_days:
            try:
                row["token_expires_at"] = (datetime.utcnow() + timedelta(days=int(expires_days))).isoformat()
            except Exception:
                pass
        try:
            so.table("employee_onboarding_profile").upsert(row, on_conflict="org_id,employee_id").execute()
        except Exception as e:
            return {**result, "ok": False, "error": f"token mint failed (migration 077?): {str(e)[:120]}"}
        url = f"{settings.APP_PUBLIC_URL}/onboard/{token}"
        result["token"] = token
        result["portal_url"] = url

    # ensure the hire is at least 'invited'
    try:
        cur = _get_profile(so, org_id, employee_id) or {}
        if not cur.get("workflow_status") or cur.get("workflow_status") == "invited":
            _set_status(so, org_id, employee_id, "invited", actor=actor)
        _log_event(org_id, employee_id, "invited", actor=actor,
                   detail={"method": method, "sent_to": email})
    except Exception:
        pass

    result["ok"] = True
    result["emailed"] = False
    if send_email_flag and email:
        try:
            labels = [t.get("label") for t in _employee_tasks(org_id, employee_id)][:12]
        except Exception:
            labels = None
        try:
            from app.modules.notify.channels.email_resend import send_email, is_configured
            if is_configured():
                await send_email(email, "Complete your onboarding",
                                 _invite_email_html(first, method, result["portal_url"], email,
                                                    result.get("temp_password"), labels))
                result["emailed"] = True
            else:
                result["email_note"] = "email not configured (RESEND_API_KEY unset) — hand the link/credentials over manually"
        except Exception as e:
            result["email_note"] = f"send failed: {str(e)[:160]}"
    return result


@router.post("/onboarding/employee/{employee_id}/invite")
async def onboarding_invite_one(employee_id: str, body: dict, org_id: str = ORG_ID):
    """Invite/re-invite ONE hire. Body: method('link'|'login'), dob?/ssn4? (link gate), role_name?,
    expires_days?, send_email? (default true). Returns the link or temp credentials."""
    so = _so()
    emp = (so.table("employees").select("employee_id,name,email")
           .eq("org_id", org_id).eq("employee_id", employee_id).limit(1).execute().data) or []
    if not emp:
        raise HTTPException(404, "employee not found")
    res = await _send_invite(org_id, emp[0], (body.get("method") or "link").strip(),
                             dob=(body.get("dob") or "").strip() or None,
                             ssn4=(body.get("ssn4") or "").strip() or None,
                             role_name=(body.get("role_name") or "").strip() or None,
                             expires_days=body.get("expires_days", 30),
                             actor=(body.get("actor") or "HR"),
                             send_email_flag=body.get("send_email", True))
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "invite failed")
    return res


@router.post("/onboarding/invite-bulk")
async def onboarding_invite_bulk(body: dict, org_id: str = ORG_ID):
    """Invite MANY hires in one action (for a roster of existing staff). Body:
    method('link'|'login', default 'login' — link needs a per-person DOB we usually don't have),
    employee_ids?[] (omit + all_incomplete=true → everyone without a completed onboarding),
    all_incomplete?, send_email? (default true). Returns a per-employee result summary."""
    so = _so()
    method = (body.get("method") or "login").strip()
    ids = [str(i).strip() for i in (body.get("employee_ids") or []) if str(i).strip()]
    emps = (so.table("employees").select("employee_id,name,email")
            .eq("org_id", org_id).eq("is_active", True).execute().data) or []
    if ids:
        emps = [e for e in emps if e.get("employee_id") in set(ids)]
    elif body.get("all_incomplete"):
        try:
            done = {p["employee_id"] for p in ((so.table("employee_onboarding_profile")
                    .select("employee_id,workflow_status").eq("org_id", org_id).execute().data) or [])
                    if p.get("workflow_status") in ("provisioned", "active")}
        except Exception:
            done = set()
        emps = [e for e in emps if e.get("employee_id") and e.get("employee_id") not in done]
    else:
        raise HTTPException(400, "pass employee_ids[] or all_incomplete=true")
    if method == "link":
        raise HTTPException(400, "bulk link invites need a per-person DOB gate — use method 'login' for a roster, "
                                 "or invite link-gated hires one at a time.")
    results = []
    for e in emps:
        results.append(await _send_invite(org_id, e, "login",
                                          role_name=(body.get("role_name") or "").strip() or None,
                                          send_email_flag=body.get("send_email", True), actor=body.get("actor") or "HR"))
    ok = sum(1 for r in results if r.get("ok"))
    emailed = sum(1 for r in results if r.get("emailed"))
    return {"invited": ok, "emailed": emailed, "total": len(results), "results": results}


# ── Item 4: work-authorization blocking gate — a HARD compliance floor, distinct from the general HR
# "override" the rest of the workflow allows. Server-enforced: the checks below run on EVERY path that
# can reach 'provisioned'/'active' (onboarding_advance + onboarding_provision), not just the UI. A hire
# needs an explicit, separately-audited compliance_override to bypass it (see each call site) — the
# general docs_verified `override` flag on /provision does NOT bypass this.
def _blocking_gate(org_id, employee_id):
    """Returns (blocked: bool, reasons: dict). reasons['work_auth'] = outstanding work_auth=true task
    labels (I-9 support docs — item 4). reasons['state_undetermined'] = True when the current template
    has a mandatory state-gated task and this employee's work_state is still unknown, so we genuinely
    cannot tell yet whether a required state form applies (item 1's other root-cause thread — see the
    migration 401 header)."""
    data = onboarding_for_employee(employee_id, org_id=org_id)
    if not data.get("ready"):
        return False, {}
    reasons = {}
    wa = [t.get("label") for c in data.get("categories", []) for t in c.get("tasks", [])
          if t.get("work_auth") and t.get("status") not in ("verified", "na")]
    if wa:
        reasons["work_auth"] = wa
    if data.get("needs_work_state"):
        reasons["state_undetermined"] = True
    return bool(reasons), reasons


def _compliance_block_message(reasons):
    parts = []
    if reasons.get("work_auth"):
        parts.append("work-authorization document(s) outstanding: " + ", ".join(reasons["work_auth"]))
    if reasons.get("state_undetermined"):
        parts.append("the employee's work state hasn't been confirmed yet (a state tax form may still be required)")
    return ("Cannot advance to provisioned/active — " + "; ".join(parts) + ". This is a hard compliance "
            "floor (item 4) — pass override_compliance=true with compliance_override_reason to bypass it "
            "(separately audited from the general docs override).")


# ── Workflow transitions + provisioning ──────────────────────────────────────────────────────────
@router.post("/onboarding/employee/{employee_id}/advance")
def onboarding_advance(employee_id: str, body: dict, org_id: str = ORG_ID):
    """HR moves the workflow to a specific status. An out-of-order move is recorded as an OVERRIDE
    (with reason) but always allowed — the flow stays in the system, HR stays in control. EXCEPTION
    (item 4): moving to provisioned/active is hard-gated on work-authorization docs + a known work
    state; see _blocking_gate."""
    to = (body.get("to_status") or "").strip()
    if to not in WORKFLOW_STATUSES:
        raise HTTPException(400, f"to_status must be one of {WORKFLOW_STATUSES}")
    gate_reasons = {}
    if to in ("provisioned", "active"):
        blocked, gate_reasons = _blocking_gate(org_id, employee_id)
        if blocked and not (body.get("override_compliance") and (body.get("compliance_override_reason") or "").strip()):
            raise HTTPException(400, {"code": "compliance_blocked",
                                      "message": _compliance_block_message(gate_reasons), "reasons": gate_reasons})
    so = _so()
    prof = _get_profile(so, org_id, employee_id) or {}
    cur = prof.get("workflow_status") or "invited"
    is_override = STATUS_ORDER.get(to, 0) != STATUS_ORDER.get(cur, 0) + 1
    st = _set_status(so, org_id, employee_id, to, actor=(body.get("actor") or "HR"),
                     reason=(body.get("reason") or None), is_override=is_override)
    if gate_reasons:   # the gate was blocked above and explicitly overridden to get here
        _log_event(org_id, employee_id, "compliance_override", actor=(body.get("actor") or "HR"),
                   reason=(body.get("compliance_override_reason") or None), is_override=True, detail=gate_reasons)
    return {"ok": True, "workflow_status": st, "was_override": is_override}


@router.post("/onboarding/employee/{employee_id}/provision")
async def onboarding_provision(employee_id: str, body: dict, org_id: str = ORG_ID):
    """Auto-provision the hire: create their login, assign their role/scope, email the credentials,
    and move to 'provisioned'. Gated on docs_verified UNLESS body.override=true (with a reason —
    the override is recorded). Body: role_name?, market?/store_code?/store_codes?, override?, reason?,
    send_email? (default true)."""
    so = _so()
    emp = (so.table("employees").select("employee_id,name,email")
           .eq("org_id", org_id).eq("employee_id", employee_id).limit(1).execute().data) or []
    if not emp:
        raise HTTPException(404, "employee not found")
    email = (emp[0].get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "this employee has no email — add one before provisioning a login")
    prof = _get_profile(so, org_id, employee_id) or {}
    cur = prof.get("workflow_status") or "invited"
    override = bool(body.get("override"))
    if STATUS_ORDER.get(cur, 0) < STATUS_ORDER["docs_verified"] and not override:
        raise HTTPException(400, {"code": "docs_incomplete",
                                  "message": f"Documents aren't verified yet (status: {STATUS_LABELS.get(cur, cur)}). "
                                             "Verify the checklist first, or override with a reason."})
    # Item 4: work-auth blocking gate — NOT bypassed by the general docs `override` above. Needs its own
    # explicit, separately-audited override_compliance + reason.
    gate_blocked, gate_reasons = _blocking_gate(org_id, employee_id)
    if gate_blocked and not (body.get("override_compliance") and (body.get("compliance_override_reason") or "").strip()):
        raise HTTPException(400, {"code": "compliance_blocked",
                                  "message": _compliance_block_message(gate_reasons), "reasons": gate_reasons})
    from app.modules.core.router import assign_role, create_login as core_create_login
    role = (body.get("role_name") or "sales_rep").strip()
    await assign_role({"email": email, "full_name": emp[0].get("name"), "role": role,
                       "market": body.get("market"), "store_code": body.get("store_code"),
                       "store_codes": body.get("store_codes"), "employee_id": employee_id}, org_id)
    try:
        login = await core_create_login({"email": email}, org_id)
    except Exception as e:
        raise HTTPException(400, f"could not create login: {str(e)[:200]}")
    _set_status(so, org_id, employee_id, "provisioned",
                actor=(body.get("actor") or "HR"), reason=(body.get("reason") or None), is_override=override)
    _log_event(org_id, employee_id, "provisioned", actor=(body.get("actor") or "HR"),
               reason=(body.get("reason") or None), is_override=override,
               detail={"role": role, "email": email})
    if gate_blocked:   # the compliance gate was blocked above and explicitly overridden to get here
        _log_event(org_id, employee_id, "compliance_override", actor=(body.get("actor") or "HR"),
                   reason=(body.get("compliance_override_reason") or None), is_override=True, detail=gate_reasons)
    emailed = False
    if body.get("send_email", True):
        try:
            from app.modules.notify.channels.email_resend import send_email, is_configured
            if is_configured():
                first = (str(emp[0].get("name") or "").split() or [""])[0]
                await send_email(email, "Your MetricsPro account is ready",
                                 _invite_email_html(first, "login", f"{settings.APP_PUBLIC_URL}/portal",
                                                    email, login.get("temp_password"), None))
                emailed = True
        except Exception:
            pass
    return {"ok": True, "workflow_status": "provisioned", "role": role,
            "temp_password": login.get("temp_password"), "emailed": emailed, "was_override": override}


@router.get("/onboarding/employee/{employee_id}/events")
def onboarding_events(employee_id: str, org_id: str = ORG_ID):
    """The workflow audit trail (newest first)."""
    try:
        rows = (_so().table("onboarding_event").select("*").eq("org_id", org_id)
                .eq("employee_id", employee_id).order("created_at", desc=True).limit(200).execute().data) or []
    except Exception:
        rows = []
    return {"events": rows, "status_labels": STATUS_LABELS}


# ── Item 1: mandatory-document reconciliation / backfill ────────────────────────────────────────────
async def _notify_mandatory_added(org_id, employee_id, missing_labels, state_undetermined):
    """Email a hire that a NEW mandatory document now applies to their checklist ('reopen & notify').
    Mirrors _notify_return's shape/posture; best-effort (returns False, never raises, if email isn't
    configured or the employee has no address on file)."""
    emp = _employee_row(org_id, employee_id)
    email = (emp.get("email") or "").strip()
    if not email:
        return False
    first = (str(emp.get("name") or "").split() or [""])[0]
    url = _portal_link(org_id, employee_id)
    items = "".join(f"<li>{m}</li>" for m in (missing_labels or [])[:30])
    if state_undetermined:
        items += "<li>Please confirm your work state — a state tax form may now be required</li>"
    html = (f"<div style='font-family:system-ui,Arial,sans-serif;max-width:560px;margin:auto;color:#111'>"
            f"<p>Hi {first or 'there'},</p>"
            f"<p>Your onboarding checklist has a new required item:</p>"
            f"<ul style='margin:0 0 12px 18px'>{items or '<li>see your onboarding portal</li>'}</ul>"
            f"<p style='margin:18px 0'><a href='{url}' style='display:inline-block;background:#111;color:#fff;"
            f"padding:11px 20px;border-radius:8px;text-decoration:none'>Complete it now</a></p>"
            f"<p style='font-size:12px;color:#888'>If the button doesn't work, copy this link:<br>{url}</p></div>")
    try:
        from app.modules.notify.channels.email_resend import send_email, is_configured
        if is_configured():
            await send_email(email, "Action needed: a new onboarding document is required", html)
            return True
    except Exception:
        pass
    return False


@router.post("/onboarding/reconcile")
async def onboarding_reconcile(body: dict, org_id: str = ORG_ID):
    """Item 1's backfill path. The checklist total/done is ALREADY computed live against the CURRENT
    template every time (see onboarding_for_employee — no per-employee snapshot exists anywhere in this
    file), so a newly mandatory task automatically shows up as outstanding for every in-flight AND
    completed hire the next time anyone loads their page. What this endpoint adds is the PROACTIVE half:
    scan the whole active roster now, report who is affected, and (when not a dry run) log an audited
    'mandatory_reopened' event + email each one — rather than waiting for someone to notice a checklist
    that used to read 100%.

    dry_run (default True) returns the report ONLY — nothing is written, nobody is emailed. Review this
    before running with dry_run=false. notify (default True, only consulted when dry_run=false) controls
    whether affected employees are emailed the missing item(s). Never regresses a provisioned/active
    hire's workflow_status (matches _recompute_status's existing 'never move backward' invariant) — this
    surfaces + notifies, it does not revoke a login."""
    dry_run = body.get("dry_run", True) is not False
    notify = body.get("notify", True) is not False
    actor = (body.get("actor") or "HR").strip()
    so = _so()
    try:
        emps = (so.table("employees").select("employee_id,name").eq("org_id", org_id)
                .eq("is_active", True).order("name").execute().data) or []
    except Exception as e:
        raise HTTPException(400, f"Could not load the roster: {e}")
    report, affected = [], 0
    for e in emps:
        eid = e.get("employee_id")
        if not eid:
            continue
        data = onboarding_for_employee(eid, org_id=org_id)
        if not data.get("ready"):
            continue
        missing = [{"task_id": t["id"], "key": t.get("key"), "label": t.get("label"), "category": c.get("label")}
                   for c in data.get("categories", []) for t in c.get("tasks", [])
                   if (t.get("is_mandatory", True) is not False) and t.get("status") not in ("verified", "na")]
        state_undetermined = bool(data.get("needs_work_state"))
        if not missing and not state_undetermined:
            continue
        affected += 1
        row = {"employee_id": eid, "employee_name": e.get("name"),
               "workflow_status": data.get("workflow_status"),
               "missing_mandatory": missing, "state_undetermined": state_undetermined, "notified": False}
        if not dry_run:
            _log_event(org_id, eid, "mandatory_reopened", actor=actor,
                       detail={"missing": [m["label"] for m in missing], "state_undetermined": state_undetermined})
            if notify:
                row["notified"] = await _notify_mandatory_added(
                    org_id, eid, [m["label"] for m in missing], state_undetermined)
        report.append(row)
    return {"ok": True, "dry_run": dry_run, "generated_at": _now_iso(),
            "employees_scanned": len(emps), "employees_affected": affected, "report": report}


# ── Self-service portal (way 2: the logged-in employee completes their own onboarding) ───────────
def _me_from_token(authorization):
    from app.modules.core.router import _uid_from_token
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "not authenticated")
    rows = (get_supabase().schema("storeops").table("app_users")
            .select("org_id,employee_id,email,full_name,role").eq("auth_id", uid).limit(1).execute().data) or []
    if not rows or not rows[0].get("employee_id"):
        raise HTTPException(403, "no employee record is linked to this login")
    return rows[0]


def _onboarding_bundle(org_id, employee_id):
    """The employee-facing checklist + intake form + current (non-sensitive) intake values."""
    data = onboarding_for_employee(employee_id, org_id=org_id)
    prof = _get_profile(_so(), org_id, employee_id) or {}
    stored = dict(prof.get("intake_data") or {})
    fields = _public_intake_fields(org_id)
    # never echo sensitive values (bank/routing) back to the client
    values = {f["key"]: stored.get(f["key"], "") for f in fields if not f["sensitive"]}
    emp_cats = []
    for c in data.get("categories", []):
        tasks = [{"id": t["id"], "label": t["label"], "description": t.get("description"),
                  "doc_url": t.get("doc_url"), "doc_label": t.get("doc_label"),
                  "requires_upload": t.get("requires_upload"), "status": t.get("status"),
                  "has_document": t.get("has_document"), "document_name": t.get("document_name"),
                  "template_name": t.get("template_name"),
                  "template_url": _sign_onboard_path(t.get("template_path")),
                  # mig 082: online fill & sign + returned-for-corrections (None-safe pre-082)
                  "requires_signature": t.get("requires_signature", True),
                  "form_fields": t.get("form_fields"),
                  "missing_fields": t.get("missing_fields"), "returned_reason": t.get("returned_reason"),
                  "signed_at": t.get("signed_at"),
                  # migration 401 (items 4 / 6): work-auth badge + "view completed sample" link
                  "work_auth": t.get("work_auth"), "sample_name": t.get("sample_name"), "sample_url": t.get("sample_url")}
                 for t in c.get("tasks", []) if t.get("owner_role") == "employee"]
        if tasks:
            emp_cats.append({"key": c["key"], "label": c["label"], "tasks": tasks})
    return {"ok": True, "ready": data.get("ready", False), "employee_id": employee_id,
            "has_profile": bool(prof), "invite_method": prof.get("invite_method"),
            "first_name": (str(data.get("employee_name") or "").split() or [""])[0],
            "work_state": data.get("work_state"), "needs_work_state": data.get("needs_work_state"),
            "workflow_status": prof.get("workflow_status") or "invited",
            "intake_fields": fields, "intake_values": values,
            "intake_submitted": bool(prof.get("intake_submitted_at")),
            "categories": emp_cats, "progress": data.get("progress"), "states": SEED_STATES,
            # migration 401: mandatory-only progress, work-auth persistent notice (item 4), DD-disclaimer
            # ack status (item 3a), and the tenant's configurable text/upload-format settings (items 2/3/4)
            "mandatory_progress": data.get("mandatory_progress"),
            "work_auth_pending": data.get("work_auth_pending"), "work_auth_notice": data.get("work_auth_notice"),
            "dd_disclaimer_signed": data.get("dd_disclaimer_signed"),
            "tenant_config": data.get("tenant_config")}


@router.get("/onboarding/me")
def onboarding_me(authorization: str = Header(default="")):
    me = _me_from_token(authorization)
    return _onboarding_bundle(me["org_id"], me["employee_id"])


@router.post("/onboarding/me/state")
def onboarding_me_state(body: dict, authorization: str = Header(default="")):
    me = _me_from_token(authorization)
    st = _set_work_state(me["org_id"], me["employee_id"], body.get("work_state") or body.get("state"), actor="employee")
    return {"ok": True, "work_state": st}


@router.post("/onboarding/me/intake")
def onboarding_me_intake(body: dict, authorization: str = Header(default="")):
    me = _me_from_token(authorization)
    return _apply_intake(me["org_id"], me["employee_id"], body or {}, actor="employee")


@router.post("/onboarding/me/upload")
async def onboarding_me_upload(task_id: str = Form(...), file: UploadFile = File(...),
                               authorization: str = Header(default="")):
    me = _me_from_token(authorization)
    tk = (get_supabase().schema("storeops").table("onboarding_task").select("owner_role")
          .eq("org_id", me["org_id"]).eq("id", task_id).limit(1).execute().data) or []
    if not tk or tk[0].get("owner_role") != "employee":
        raise HTTPException(403, "That item can't be uploaded here.")
    res = await _do_onboard_upload(me["org_id"], me["employee_id"], task_id, file, "employee")
    _recompute_status(_so(), me["org_id"], me["employee_id"], actor="employee")
    return res


# ── Public (token) intake + state, mirroring the self endpoints ──────────────────────────────────
@router.post("/public/onboarding/{token}/state")
def public_onboarding_state(token: str, body: dict):
    prof = _profile_by_token(token)
    if not _token_valid(prof):
        raise HTTPException(404, "This onboarding link is invalid or has expired.")
    if not _check_gate(prof, body.get("value")):
        raise HTTPException(403, "Identity check failed.")
    st = _set_work_state(prof["org_id"], prof["employee_id"], body.get("work_state") or body.get("state"), actor="employee")
    return {"ok": True, "work_state": st}


@router.post("/public/onboarding/{token}/intake")
def public_onboarding_intake(token: str, body: dict):
    prof = _profile_by_token(token)
    if not _token_valid(prof):
        raise HTTPException(404, "This onboarding link is invalid or has expired.")
    if not _check_gate(prof, (body or {}).get("value")):
        raise HTTPException(403, "Identity check failed.")
    payload = {k: v for k, v in (body or {}).items() if k != "value"}
    return _apply_intake(prof["org_id"], prof["employee_id"], payload, actor="employee")


# ════════════════════════════════════════════════════════════════════════════════════════════════
# DOCUMENT SEND/RETURN FLOW (migration 082) — the HR "Documents" board (who was SENT the packet,
# what came BACK), online FILL & SIGN (form fields + a drawn signature stored in the private
# bucket), and the returned-for-corrections loop: online submissions are field-checked
# deterministically before they're accepted; uploaded FILLABLE PDFs get an AcroForm completeness +
# signature check; flat scans route to HR review. Anything incomplete is RETURNED to the employee
# (status 'returned') with the exact missing fields listed, in the portal AND by email.
# ════════════════════════════════════════════════════════════════════════════════════════════════

def _task_row(org_id, task_id):
    rows = (_so().table("onboarding_task").select("*").eq("org_id", org_id)
            .eq("id", task_id).limit(1).execute().data) or []
    return rows[0] if rows else {}


def _employee_row(org_id, employee_id):
    rows = (_so().table("employees").select("employee_id,name,email").eq("org_id", org_id)
            .eq("employee_id", employee_id).limit(1).execute().data) or []
    return rows[0] if rows else {}


def _pdf_form_check(data):
    """Best-effort completeness/signature check on an uploaded PDF. FILLABLE (AcroForm) PDFs are
    checkable; flat/scanned PDFs return checkable:False and go to HR for a by-eye review. Reports:
      missing — empty fields the form itself marks REQUIRED (auto-return material)
      empty   — every other empty text/choice field (real forms leave optional steps blank, so
                these DON'T auto-return; HR sees them for a one-click manual return)
      filled  — how many fields carry a value (0 on a form with fields = came back blank)
      signed  — True/False when the form has a signature(-labeled) field, None when it has none
    Labels prefer the human tooltip (/TU) over internal names; UTF-16 names are decoded."""
    try:
        import io as _io
        from pdfminer.pdfparser import PDFParser
        from pdfminer.pdfdocument import PDFDocument
        from pdfminer.pdftypes import resolve1
        from pdfminer.psparser import PSLiteral
        doc = PDFDocument(PDFParser(_io.BytesIO(data)))
        root = resolve1(doc.catalog) or {}
        acro = resolve1(root.get("AcroForm")) if root.get("AcroForm") else None
        field_refs = resolve1(acro.get("Fields")) if acro else None
        if not field_refs:
            return {"checkable": False, "reason": "no fillable form fields (flat/scanned PDF)"}
        missing, empty = [], []
        n = filled = sig_fields = sig_signed = 0

        def _txt(v):
            if isinstance(v, PSLiteral):
                v = v.name
            if isinstance(v, bytes):
                try:
                    if v[:2] in (b"\xfe\xff", b"\xff\xfe") or b"\x00" in v[:8]:
                        return v.decode("utf-16", "ignore").lstrip("\ufeff")
                    return v.decode("utf-8", "ignore")
                except Exception:
                    return v.decode("latin-1", "ignore")
            return "" if v is None else str(v)

        def walk(refs, prefix=""):
            nonlocal n, filled, sig_fields, sig_signed
            for ref in (refs or []):
                try:
                    f = resolve1(ref) or {}
                except Exception:
                    continue
                name = ((prefix + ".") if prefix else "") + _txt(f.get("T")).strip()
                # human label: tooltip if the form carries one, else the field path minus the
                # boilerplate wrapper segments ("topmostSubform[0].Page1[0].Step1a[0].f1_01[0]"
                # -> "Page1.Step1a.f1_01")
                pretty = ".".join(p for p in (re.sub(r"\[\d+\]$", "", s) for s in name.split("."))
                                  if p and p.lower() not in ("topmostsubform", "form1"))
                label = _txt(f.get("TU")).strip() or pretty or "unnamed field"
                ft = str(f.get("FT") or "")
                kids = f.get("Kids")
                if kids and not ft:      # a pure container node — recurse
                    try:
                        walk(resolve1(kids), name)
                    except Exception:
                        pass
                    continue
                n += 1
                try:
                    val = resolve1(f.get("V"))
                except Exception:
                    val = f.get("V")
                sval = _txt(val).strip()
                has_val = val is not None and sval not in ("", "Off")
                if has_val:
                    filled += 1
                try:
                    required = bool(int(resolve1(f.get("Ff")) or 0) & 2)
                except Exception:
                    required = False
                if "Sig" in ft or "sign" in name.lower() or "signature" in label.lower():
                    sig_fields += 1
                    sig_signed += 1 if has_val else 0
                elif ("Tx" in ft or "Ch" in ft) and not has_val:
                    (missing if required else empty).append(label)

        walk(field_refs)
        signed = (sig_signed > 0) if sig_fields else None
        return {"checkable": True, "fields": n, "filled": filled,
                "missing": missing[:40], "empty": empty[:40], "signed": signed}
    except Exception as e:
        return {"checkable": False, "reason": f"could not inspect PDF: {str(e)[:120]}"}


def _portal_link(org_id, employee_id):
    prof = _get_profile(_so(), org_id, employee_id) or {}
    if prof.get("access_token") and prof.get("token_active"):
        return f"{settings.APP_PUBLIC_URL}/onboard/{prof['access_token']}"
    return f"{settings.APP_PUBLIC_URL}/portal"


async def _notify_return(org_id, employee_id, task, missing, reason):
    """Email the employee that a document came back incomplete, listing exactly what to fix."""
    emp = _employee_row(org_id, employee_id)
    email = (emp.get("email") or "").strip()
    if not email:
        return False
    first = (str(emp.get("name") or "").split() or [""])[0]
    url = _portal_link(org_id, employee_id)
    items = "".join(f"<li>{m}</li>" for m in (missing or [])[:30]) or "<li>see the note from HR</li>"
    html = (f"<div style='font-family:system-ui,Arial,sans-serif;max-width:560px;margin:auto;color:#111'>"
            f"<p>Hi {first or 'there'},</p>"
            f"<p>Your <b>{task.get('label') or 'onboarding document'}</b> needs another look before we can accept it:</p>"
            f"<ul style='margin:0 0 12px 18px'>{items}</ul>"
            + (f"<p style='font-size:13px;color:#555'>{reason}</p>" if reason else "") +
            f"<p style='margin:18px 0'><a href='{url}' style='display:inline-block;background:#111;color:#fff;"
            f"padding:11px 20px;border-radius:8px;text-decoration:none'>Fix and resubmit</a></p>"
            f"<p style='font-size:12px;color:#888'>If the button doesn't work, copy this link:<br>{url}</p></div>")
    try:
        from app.modules.notify.channels.email_resend import send_email, is_configured
        if is_configured():
            await send_email(email, f"Action needed: {task.get('label') or 'onboarding document'}", html)
            return True
    except Exception:
        pass
    return False


async def _do_onboard_sign(org_id, employee_id, task_id, form_data, signature, signed_name, who="employee"):
    """Online FILL & SIGN: validate the task's configured fields, store the drawn signature as a
    PNG in the private bucket, mark the item submitted. Deterministic — a missing field 400s with
    the exact list, so an incomplete online submission is bounced BEFORE it enters the system."""
    task = _task_row(org_id, task_id)
    if not task:
        raise HTTPException(404, "unknown onboarding item")
    form_data = form_data if isinstance(form_data, dict) else {}
    fields = [f for f in (task.get("form_fields") or []) if isinstance(f, dict)]
    missing = [f.get("label") or f.get("key") for f in fields
               if f.get("required", True) and not str(form_data.get(f.get("key") or f.get("label") or "", "")).strip()]
    if task.get("requires_signature", True) and not (signature or "").strip():
        missing.append("Signature")
    if missing:
        raise HTTPException(400, "Please complete: " + ", ".join(str(m) for m in missing))
    sig_path = None
    if (signature or "").strip():
        try:
            png = base64.b64decode(signature.split(",", 1)[1] if "," in signature else signature)
        except Exception:
            raise HTTPException(400, "The signature image could not be read — please sign again.")
        sig_path = f"{org_id}/{employee_id}/sig_{uuid.uuid4().hex}.png"
        try:
            _ensure_onboard_bucket().storage.from_(ONBOARD_BUCKET).upload(sig_path, png, {"content-type": "image/png"})
        except Exception as e:
            raise HTTPException(500, f"could not store the signature: {e}")
    row = {"org_id": org_id, "employee_id": employee_id, "task_id": task_id, "status": "submitted",
           "form_data": form_data or None, "signature_path": sig_path,
           "signed_name": (signed_name or "").strip() or None, "signed_at": _now_iso(),
           "submitted_at": _now_iso(), "updated_at": _now_iso(),
           "document_name": "Signed online", "note": f"filled & signed online by {who}",
           "missing_fields": None, "returned_reason": None, "returned_at": None, "returned_by": None,
           "validation": {"checkable": True, "missing": [], "signed": bool(sig_path), "online": True}}
    try:
        _so().table("employee_onboarding").upsert(row, on_conflict="org_id,employee_id,task_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save — is migration 082 applied? {e}")
    _log_event(org_id, employee_id, "doc_signed_online", actor=who,
               detail={"task": task.get("label"), "fields": sorted(form_data.keys())})
    _recompute_status(_so(), org_id, employee_id, actor=who)
    return {"ok": True, "status": "submitted", "signed": bool(sig_path)}


@router.post("/public/onboarding/{token}/sign")
async def public_onboarding_sign(token: str, body: dict):
    """Credential-less portal: fill & sign an item online (gate re-checked on every call)."""
    prof = _profile_by_token(token)
    if not _token_valid(prof):
        raise HTTPException(404, "This onboarding link is invalid or has expired.")
    if not _check_gate(prof, (body or {}).get("value")):
        raise HTTPException(403, "Identity check failed.")
    task = _task_row(prof["org_id"], str((body or {}).get("task_id") or ""))
    if not task or task.get("owner_role") != "employee":
        raise HTTPException(403, "That item can't be signed from this portal.")
    return await _do_onboard_sign(prof["org_id"], prof["employee_id"], task["id"],
                                  (body or {}).get("form_data"), (body or {}).get("signature") or "",
                                  (body or {}).get("signed_name") or "")


@router.post("/onboarding/me/sign")
async def onboarding_me_sign(body: dict, authorization: str = Header(default="")):
    """Logged-in portal: fill & sign an item online."""
    me = _me_from_token(authorization)
    task = _task_row(me["org_id"], str((body or {}).get("task_id") or ""))
    if not task or task.get("owner_role") != "employee":
        raise HTTPException(403, "That item can't be signed here.")
    return await _do_onboard_sign(me["org_id"], me["employee_id"], task["id"],
                                  (body or {}).get("form_data"), (body or {}).get("signature") or "",
                                  (body or {}).get("signed_name") or "")


@router.post("/onboarding/employee/{employee_id}/task/{task_id}/return")
async def onboarding_return_task(employee_id: str, task_id: str, body: dict, org_id: str = ORG_ID):
    """HR sends a submitted document BACK for corrections (a scan the auto-check can't read, a
    missed signature, the wrong form…), listing what's missing. The employee sees the item flagged
    in their portal and gets an email with the exact list."""
    task = _task_row(org_id, task_id)
    if not task:
        raise HTTPException(404, "unknown onboarding item")
    missing = [str(m).strip() for m in (body.get("missing_fields") or []) if str(m).strip()]
    reason = (body.get("reason") or "").strip() or None
    if not missing and not reason:
        raise HTTPException(400, "List the missing fields (or give a reason) so the employee knows what to fix.")
    row = {"org_id": org_id, "employee_id": employee_id, "task_id": task_id, "status": "returned",
           "missing_fields": missing or None, "returned_reason": reason,
           "returned_at": _now_iso(), "returned_by": (body.get("actor") or "HR"), "updated_at": _now_iso()}
    try:
        _so().table("employee_onboarding").upsert(row, on_conflict="org_id,employee_id,task_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not return — is migration 082 applied? {e}")
    _log_event(org_id, employee_id, "doc_returned", actor=body.get("actor") or "HR",
               detail={"task": task.get("label"), "missing": missing, "reason": reason})
    emailed = await _notify_return(org_id, employee_id, task, missing, reason)
    return {"ok": True, "emailed": emailed}


@router.get("/onboarding/employee/{employee_id}/task/{task_id}/signature")
def onboarding_signature_url(employee_id: str, task_id: str, org_id: str = ORG_ID):
    """A 1-hour signed URL for the online-drawn signature image (HR verification view)."""
    rows = (_so().table("employee_onboarding").select("signature_path").eq("org_id", org_id)
            .eq("employee_id", employee_id).eq("task_id", task_id).limit(1).execute().data) or []
    if not rows or not rows[0].get("signature_path"):
        raise HTTPException(404, "no signature on file")
    url = _sign_onboard_path(rows[0]["signature_path"])
    if not url:
        raise HTTPException(500, "could not sign the url")
    return {"url": url}


@router.get("/onboarding/doc-status")
def onboarding_doc_status(org_id: str = ORG_ID):
    """The HR Documents board: one row per active roster employee — whether the onboarding packet
    was SENT (docs_sent_at / invited_at), and what came BACK per item (pending / submitted /
    returned / verified counts + labels) — so HR can checkbox-select who still needs the packet and
    chase exactly what's missing. Three queries total, applicability computed in Python."""
    so = _so()
    tmpl = onboarding_template(org_id=org_id)
    if not tmpl.get("ready"):
        return {"ready": False, "employees": []}
    tasks = [t for c in tmpl["categories"] for t in c["tasks"] if t.get("owner_role") == "employee"]
    emps = (so.table("employees").select("employee_id,name,email,is_active").eq("org_id", org_id)
            .eq("is_active", True).order("name").execute().data) or []
    profs = {p.get("employee_id"): p for p in ((so.table("employee_onboarding_profile").select("*")
             .eq("org_id", org_id).execute().data) or [])}
    recs = {}
    try:
        rows = (so.table("employee_onboarding")
                .select("employee_id,task_id,status,submitted_at,returned_at,updated_at")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        rows = []
    for r in rows:
        recs.setdefault(r.get("employee_id"), {})[r.get("task_id")] = r
    out = []
    for e in emps:
        eid = e.get("employee_id")
        if not eid:
            continue
        prof = profs.get(eid) or {}
        ws = prof.get("work_state")
        mine = [t for t in tasks if not t.get("applies_state") or t.get("applies_state") == ws]
        st = recs.get(eid, {})
        counts = {"total": len(mine), "pending": 0, "submitted": 0, "returned": 0, "verified": 0}
        pending_labels, returned_labels, last = [], [], None
        for t in mine:
            r = st.get(t["id"]) or {}
            s = r.get("status") or "pending"
            bucket = s if s in ("pending", "submitted", "returned", "verified") else ("verified" if s == "na" else "pending")
            counts[bucket] += 1
            if bucket == "pending":
                pending_labels.append(t.get("label"))
            elif bucket == "returned":
                returned_labels.append(t.get("label"))
            ts = r.get("updated_at") or r.get("submitted_at")
            if ts and (not last or ts > last):
                last = ts
        out.append({"employee_id": eid, "name": e.get("name"), "email": e.get("email"),
                    "workflow_status": prof.get("workflow_status"),
                    "invited_at": prof.get("invited_at"), "docs_sent_at": prof.get("docs_sent_at"),
                    "invite_method": prof.get("invite_method"),
                    "intake_submitted": bool(prof.get("intake_submitted_at")),
                    "sent": bool(prof.get("docs_sent_at") or prof.get("invited_at")),
                    "accounting_forwarded_at": prof.get("accounting_forwarded_at"),
                    "accounting_forwarded_to": prof.get("accounting_forwarded_to"),
                    **counts,
                    "pending_labels": pending_labels[:12], "returned_labels": returned_labels[:12],
                    "last_activity": last})
    return {"ready": True, "employees": out}


@router.post("/onboarding/send-documents")
async def onboarding_send_documents(body: dict, org_id: str = ORG_ID):
    """The Documents-board checkbox action: (re)send the onboarding packet to the selected people.
    Per person: an existing identity gate (DOB / last-4) re-issues their token LINK; otherwise a
    portal LOGIN invite goes to the roster email. Stamps docs_sent_at so the board shows who's been
    sent. Body: employee_ids[], send_email? (default true), actor?"""
    ids = [str(i).strip() for i in (body.get("employee_ids") or []) if str(i).strip()]
    if not ids:
        raise HTTPException(400, "pass employee_ids[]")
    so = _so()
    results = []
    for eid in ids:
        emp = _employee_row(org_id, eid)
        if not emp:
            results.append({"employee_id": eid, "ok": False, "error": "not on the roster"})
            continue
        prof = _get_profile(so, org_id, eid) or {}
        method, dob, ssn4 = "login", None, None
        if prof.get("verify_dob"):
            method, dob = "link", str(prof.get("verify_dob"))[:10]
        elif prof.get("verify_ssn4"):
            method, ssn4 = "link", prof.get("verify_ssn4")
        elif not (emp.get("email") or "").strip():
            results.append({"employee_id": eid, "name": emp.get("name"), "ok": False,
                            "error": "no email on file and no identity gate — add an email, or invite them one-on-one with a DOB"})
            continue
        res = await _send_invite(org_id, emp, method, dob=dob, ssn4=ssn4,
                                 send_email_flag=body.get("send_email", True),
                                 actor=body.get("actor") or "HR")
        if res.get("ok"):
            try:
                so.table("employee_onboarding_profile").upsert(
                    {"org_id": org_id, "employee_id": eid, "docs_sent_at": _now_iso()},
                    on_conflict="org_id,employee_id").execute()
            except Exception:
                pass  # pre-082 — invited_at still marks them as sent
            _log_event(org_id, eid, "docs_sent", actor=body.get("actor") or "HR",
                       detail={"method": method, "sent_to": res.get("email")})
        results.append(res)
    ok = sum(1 for r in results if r.get("ok"))
    emailed = sum(1 for r in results if r.get("emailed"))
    return {"sent": ok, "emailed": emailed, "total": len(results), "results": results}


# ── Completed-paperwork review/approval + forward to accounting (customizable) — migration 100 ─────
def _recipient_list(v):
    """Normalize a recipients value (list OR comma/;/newline-separated string) → clean list."""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v or "").replace(";", ",").replace("\n", ",").split(",") if x.strip()]


def _accounting_settings(org_id):
    """The org's accounting-forward config row ({} if unset / migration 100 not applied)."""
    try:
        rows = (_so().table("hr_onboarding_settings").select("*").eq("org_id", org_id).limit(1).execute().data) or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def _completed_docs_for(org_id, employee_id, expires=604800):
    """Secure (7-day) download links to every uploaded onboarding document for a hire, labelled by task.
    Used to forward the completed packet to accounting without giving them app access."""
    tmpl = onboarding_template(org_id=org_id)
    labels = {t["id"]: t["label"] for c in tmpl.get("categories", []) for t in c.get("tasks", [])}
    try:
        rows = (_so().table("employee_onboarding")
                .select("task_id,status,document_path,document_name,signed_at")
                .eq("org_id", org_id).eq("employee_id", employee_id).execute().data) or []
    except Exception:
        rows = []
    docs = []
    for r in rows:
        p = r.get("document_path")
        if not p:
            continue
        docs.append({"label": labels.get(r.get("task_id"), "Document"),
                     "name": r.get("document_name"), "signed_at": r.get("signed_at"),
                     "url": _sign_onboard_path(p, expires=expires)})
    return docs


@router.get("/onboarding/accounting-settings")
def onboarding_get_accounting_settings(org_id: str = ORG_ID):
    """The CUSTOMIZABLE accounting-forward destination (email recipients + subject/message template)."""
    from app.modules.notify.channels.email_resend import is_configured as _email_ok
    try:
        rows = (_so().table("hr_onboarding_settings").select("*").eq("org_id", org_id).limit(1).execute().data) or []
        s = rows[0] if rows else {}
        ready = True
    except Exception:
        s, ready = {}, False   # migration 100 not applied yet
    return {"ready": ready, "email_configured": _email_ok(),
            "emails": s.get("accounting_emails") or [], "whatsapps": s.get("accounting_whatsapps") or [],
            "subject": s.get("forward_subject") or "", "message": s.get("forward_message") or "",
            "include_portal_link": s.get("include_portal_link", True)}


@router.put("/onboarding/accounting-settings")
def onboarding_set_accounting_settings(body: dict, org_id: str = ORG_ID):
    """Save the accounting-forward destination. emails/whatsapps accept a list or a comma-separated string."""
    row = {"org_id": org_id,
           "accounting_emails": _recipient_list(body.get("emails")),
           "accounting_whatsapps": _recipient_list(body.get("whatsapps")),
           "forward_subject": (body.get("subject") or "").strip() or None,
           "forward_message": (body.get("message") or "").strip() or None,
           "include_portal_link": body.get("include_portal_link", True) is not False,
           "updated_at": _now_iso()}
    try:
        _so().table("hr_onboarding_settings").upsert(row, on_conflict="org_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save — is migration 100 applied? ({e})")
    return {"ok": True}


def _is_complete_row(st):
    """A doc-status row is 'complete' when there ARE applicable items and none are pending/returned."""
    return bool(st) and st.get("total", 0) > 0 and st.get("pending", 0) == 0 and st.get("returned", 0) == 0


@router.post("/onboarding/employee/{employee_id}/approve")
def onboarding_approve(employee_id: str, body: dict, org_id: str = ORG_ID):
    """Mark a completed hire's paperwork REVIEWED & APPROVED → advance the workflow to docs_verified.
    Guards that the employee-owned checklist is actually all back (override with body.force=true)."""
    so = _so()
    st = {r["employee_id"]: r for r in onboarding_doc_status(org_id=org_id).get("employees", [])}.get(employee_id)
    if not body.get("force") and not _is_complete_row(st):
        raise HTTPException(400, "Not all documents are back yet — review the outstanding items, or pass force=true.")
    prof = _get_profile(so, org_id, employee_id) or {}
    cur = prof.get("workflow_status") or "invited"
    is_override = STATUS_ORDER.get("docs_verified", 0) != STATUS_ORDER.get(cur, 0) + 1
    new = _set_status(so, org_id, employee_id, "docs_verified", actor=(body.get("actor") or "HR"),
                      reason=(body.get("reason") or "Onboarding paperwork reviewed & approved"), is_override=is_override)
    return {"ok": True, "workflow_status": new}


@router.post("/onboarding/employee/{employee_id}/forward-accounting")
async def onboarding_forward_accounting(employee_id: str, body: dict, org_id: str = ORG_ID):
    """Forward a completed hire's paperwork to the (customizable) accounting recipients: an email with a
    per-document summary + secure 7-day download links. Recipients default to the saved settings; body may
    override emails/subject/message. Stamps accounting_forwarded_at + audits. Body: emails?, subject?,
    message?, actor?, force? (skip the completeness guard)."""
    from app.modules.notify.channels.email_resend import send_email, is_configured
    so = _so()
    emp = _employee_row(org_id, employee_id)
    if not emp:
        raise HTTPException(404, "employee not found")
    if not is_configured():
        raise HTTPException(400, "Email isn't configured (RESEND_API_KEY + NOTIFY_FROM_EMAIL) — can't forward.")
    s = _accounting_settings(org_id)
    emails = _recipient_list(body.get("emails")) or (s.get("accounting_emails") or [])
    if not emails:
        raise HTTPException(400, "No accounting recipient configured — set one in the Completed tab settings (or pass emails).")
    if not body.get("force"):
        st = {r["employee_id"]: r for r in onboarding_doc_status(org_id=org_id).get("employees", [])}.get(employee_id)
        if not _is_complete_row(st):
            raise HTTPException(400, "This hire's paperwork isn't complete yet — forward once everything is back (or pass force=true).")
    docs = _completed_docs_for(org_id, employee_id)
    name = emp.get("name") or employee_id
    subject = (body.get("subject") or s.get("forward_subject") or "Completed onboarding paperwork — {name}").replace("{name}", name)
    intro = (body.get("message") or s.get("forward_message")
             or "The onboarding paperwork below is complete and approved. Secure download links (valid 7 days) are included for the accounting file.")
    rows_html = "".join(
        '<tr><td style="padding:6px 10px;border-top:1px solid #eee">' + str(d.get("label") or "Document") + "</td>"
        '<td style="padding:6px 10px;border-top:1px solid #eee">'
        + (f'<a href="{d["url"]}">{d.get("name") or "Download"}</a>' if d.get("url") else "(link unavailable)")
        + "</td></tr>"
        for d in docs)
    html = ('<div style="font-family:system-ui,Arial,sans-serif;font-size:14px;color:#111">'
            f"<p>{intro}</p><p><b>Employee:</b> {name} ({employee_id})</p>"
            '<table style="border-collapse:collapse;font-size:13px"><thead><tr>'
            '<th style="text-align:left;padding:6px 10px">Document</th>'
            '<th style="text-align:left;padding:6px 10px">File</th></tr></thead><tbody>'
            + (rows_html or '<tr><td colspan="2" style="padding:6px 10px">No uploaded files on record.</td></tr>')
            + "</tbody></table>")
    if s.get("include_portal_link", True):
        html += f'<p style="margin-top:12px;color:#666;font-size:12px">Sent from MetricsPro · onboarding {employee_id}</p>'
    html += "</div>"
    sent, errors = [], []
    for to in emails:
        try:
            await send_email(to, subject, html)
            sent.append(to)
        except Exception as e:
            errors.append({"to": to, "error": str(e)[:200]})
    if sent:
        try:
            so.table("employee_onboarding_profile").upsert(
                {"org_id": org_id, "employee_id": employee_id,
                 "accounting_forwarded_at": _now_iso(), "accounting_forwarded_to": ", ".join(sent)},
                on_conflict="org_id,employee_id").execute()
        except Exception:
            pass  # pre-100 — the forward still went out; just no stamp
        _log_event(org_id, employee_id, "forwarded_accounting", actor=(body.get("actor") or "HR"),
                   detail={"to": sent, "docs": len(docs)})
    if not sent:
        raise HTTPException(400, f"Forward failed for all recipients: {errors}")
    return {"ok": True, "sent_to": sent, "docs": len(docs), "errors": errors}


# ════════════════════════════════════════════════════════════════════════════════════════════════
# ITEM 5 — Compliance document repository. A VIEW + bulk export over the SAME onboarding-docs bucket
# and employee_onboarding rows the Documents board (mig 082) already tracks — no second store, no new
# table. Lists every uploaded/signed document across the roster, employee-grouped, filterable, each row
# carrying its own file AND (when the item was signed online) its signature page. Bulk "pick up at once"
# export = one ZIP, organized /EmployeeName/DocumentLabel.ext (+ _signature.png alongside it).
# ════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/onboarding/compliance-documents")
def onboarding_compliance_documents(org_id: str = ORG_ID, q: str = "", employee_id: str = ""):
    """One row per uploaded/signed onboarding document, across the whole roster, sorted by employee
    name then document label. Filter with q (name/id/email substring) or employee_id (exact). Does NOT
    eagerly sign a URL per row (could be hundreds) — click-through uses the existing per-task
    /onboarding/employee/{id}/task/{task_id}/doc and .../signature endpoints, already org-scoped."""
    so = _so()
    tmpl = onboarding_template(org_id=org_id, include_inactive=True)
    label_of = {t["id"]: t["label"] for c in tmpl.get("categories", []) for t in c.get("tasks", [])}
    cat_of = {t["id"]: c["label"] for c in tmpl.get("categories", []) for t in c.get("tasks", [])}
    emps = {e["employee_id"]: e for e in ((so.table("employees").select("employee_id,name,email")
            .eq("org_id", org_id).execute().data) or [])}
    try:
        rows = (so.table("employee_onboarding")
                .select("employee_id,task_id,status,document_path,document_name,signature_path,"
                        "signed_at,signed_name,verified_by,verified_at,submitted_at")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        rows = []
    out = []
    for r in rows:
        if not (r.get("document_path") or r.get("signature_path")):
            continue   # nothing actually on file for this task yet
        eid = r.get("employee_id")
        if employee_id and eid != employee_id:
            continue
        emp = emps.get(eid) or {}
        name = emp.get("name") or eid
        if q and q.lower() not in f"{name} {eid} {emp.get('email') or ''}".lower():
            continue
        out.append({
            "employee_id": eid, "employee_name": name, "employee_email": emp.get("email"),
            "task_id": r.get("task_id"), "document_label": label_of.get(r.get("task_id")) or "Document",
            "category": cat_of.get(r.get("task_id")),
            "status": r.get("status"), "document_name": r.get("document_name"),
            "has_document": bool(r.get("document_path")), "has_signature_page": bool(r.get("signature_path")),
            "signed_at": r.get("signed_at") or r.get("verified_at") or r.get("submitted_at"),
            "signed_name": r.get("signed_name"), "verified_by": r.get("verified_by")})
    out.sort(key=lambda d: (d["employee_name"] or "", d["document_label"] or ""))
    return {"ready": tmpl.get("ready", True), "documents": out, "count": len(out)}


@router.get("/onboarding/compliance-documents/export")
def onboarding_compliance_export(org_id: str = ORG_ID, employee_id: str = ""):
    """Bulk 'pick up at once' export: one ZIP built live from the SAME storage bucket the Documents
    board already uses — no second copy of the files is kept anywhere. Pass employee_id to export just
    one person's folder; omit it for the whole org (one zip of all, organized /EmployeeName/Doc.ext)."""
    import io as _io
    import zipfile
    so = _so()
    tmpl = onboarding_template(org_id=org_id, include_inactive=True)
    label_of = {t["id"]: t["label"] for c in tmpl.get("categories", []) for t in c.get("tasks", [])}
    emps = {e["employee_id"]: e for e in ((so.table("employees").select("employee_id,name")
            .eq("org_id", org_id).execute().data) or [])}
    try:
        q = so.table("employee_onboarding").select(
            "employee_id,task_id,document_path,document_name,signature_path").eq("org_id", org_id)
        if employee_id:
            q = q.eq("employee_id", employee_id)
        rows = (q.execute().data) or []
    except Exception:
        rows = []
    bucket = get_supabase().storage.from_(ONBOARD_BUCKET)
    buf = _io.BytesIO()
    included = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in rows:
            eid = r.get("employee_id")
            emp_name = (emps.get(eid) or {}).get("name") or eid or "unknown"
            safe_emp = re.sub(r"[^A-Za-z0-9 _.-]+", "_", emp_name).strip() or (eid or "unknown")
            doc_label = label_of.get(r.get("task_id")) or "Document"
            safe_doc = re.sub(r"[^A-Za-z0-9 _.-]+", "_", doc_label).strip() or "document"
            if r.get("document_path"):
                try:
                    file_bytes = bucket.download(r["document_path"])
                    dn = r.get("document_name") or ""
                    ext = dn.rsplit(".", 1)[-1] if "." in dn else "pdf"
                    zf.writestr(f"{safe_emp}/{safe_doc}.{ext}", file_bytes)
                    included += 1
                except Exception:
                    continue   # a single unreadable file shouldn't fail the whole export
            if r.get("signature_path"):
                try:
                    sig_bytes = bucket.download(r["signature_path"])
                    zf.writestr(f"{safe_emp}/{safe_doc}_signature.png", sig_bytes)
                except Exception:
                    pass
    if included == 0:
        raise HTTPException(404, "No documents found to export.")
    buf.seek(0)
    fname = f"onboarding-documents-{employee_id or 'all'}.zip"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})
