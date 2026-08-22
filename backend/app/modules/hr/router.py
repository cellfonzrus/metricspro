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
import inspect
import re
import secrets
import uuid
from datetime import datetime, timedelta, date as _date
from typing import Any, Optional
from fastapi import APIRouter, Header, HTTPException, UploadFile, File, Form, Response
from fastapi.responses import StreamingResponse
from app.core.database import get_supabase
from app.core.schemas import LaxModel
from app.core.config import settings
from app.core import crypto
from app.modules.storeops.router import scope_emp_ids, _tenant_pp_settings, _employees_with_pay_fields
from app.modules.storeops import payroll_salary

router = APIRouter(prefix="/hr", tags=["HR"])
ORG_ID = "00000000-0000-0000-0000-000000000001"

async def _maybe_await(value):
    """core.router's list_employees/assign_role/create_login are `async def` today (nav-perf 2026-08-04
    ask: this file's `await` on them is the ONLY reason platform-core can't convert them to sync `def`
    for threadpool dispatch). Calling the function always executes it correctly either way; the only
    question is whether the RESULT needs an `await`. Resilient to either shape so this file needs no
    follow-up edit whenever core.router converts."""
    return (await value) if inspect.isawaitable(value) else value


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
    return await _maybe_await(list_employees(org_id))


@router.post("/employees")
async def hr_create_employee(body: dict, org_id: str = ORG_ID,
                             authorization: str = Header(default=""),
                             x_active_org: str = Header(default="")):
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
        await _maybe_await(assign_role({
            "email": email, "full_name": name, "role": role or "sales_rep",
            "market": body.get("market"), "store_code": body.get("store_code"),
            "store_codes": body.get("store_codes"), "employee_id": emp.get("employee_id"),
        }, org_id, authorization=authorization, x_active_org=x_active_org))
        assigned = role or "sales_rep"
        if body.get("create_login"):
            from app.modules.core.router import create_login as core_create_login
            try:
                login = await _maybe_await(core_create_login({"email": email}, org_id,
                                                             authorization=authorization,
                                                             x_active_org=x_active_org))
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
                                        role_name=role or None, send_email_flag=True, actor="HR",
                                        authorization=authorization, x_active_org=x_active_org)
        except Exception as e:
            invite = {"ok": False, "error": str(e)[:200]}
    return {"employee": emp, "assigned_role": assigned, "login": login, "invite": invite,
            "note": (None if email or not (role or has_scope)
                     else "Role/scope ignored — an email is required to assign a role or create a login.")}


@router.patch("/employees/{emp_id}")
async def hr_update_employee(emp_id: str, body: dict, authorization: str = Header(default=""), org_id: str = ORG_ID,
                             x_active_org: str = Header(default="")):
    """Update a person from HR. Updates the roster row (if roster fields are present) and, when a
    role/scope + email is given, re-syncs the app_users assignment so the login stays in step.

    `authorization` is threaded through to storeops.update_employee (2026-07-27) so its manager gate
    on pay_rate/pay_basis/pay_amount/termination_date (_PAY_GATED_FIELDS) also applies when a pay
    field is edited via HR (the HR "Employees & Pay" page is the primary pay-setup surface)."""
    from app.modules.storeops.router import EMP_FIELDS, update_employee
    res = None
    if any(k in body for k in EMP_FIELDS):
        res = update_employee(emp_id, body, authorization=authorization, org_id=org_id)   # sync handler; raises 404 if missing; org-scoped
    email = (body.get("email") or (res or {}).get("email") or "").strip().lower()
    role = (body.get("role_name") or body.get("app_role") or "").strip()
    has_scope = any(k in body for k in ("market", "store_code", "store_codes"))
    if email and (role or has_scope):
        from app.modules.core.router import assign_role
        await _maybe_await(assign_role({
            "email": email, "full_name": body.get("name") or (res or {}).get("name"),
            "role": role or None, "market": body.get("market"),
            "store_code": body.get("store_code"), "store_codes": body.get("store_codes"),
            "employee_id": (res or {}).get("employee_id"),
        }, org_id, authorization=authorization, x_active_org=x_active_org))
    return res or {"ok": True, "id": emp_id}


@router.get("/compensation")
def compensation(period: str, authorization: str = Header(default=""), org_id: str = ORG_ID,
                  response: Response = None):
    """Per-employee total compensation for a period: wages (hours × pay_rate, from shifts) +
    commission (rep_commissions total_payout) − chargeback deductions. Span-scoped to the caller."""
    so, cc = _so(), _cc()
    emps = _employees_with_pay_fields(org_id, "employee_id,name,home_store,pay_rate,is_active,epay_salesperson")
    emps = [e for e in emps if e.get("is_active") is True]   # matches the prior .eq("is_active", True)

    # Wages — sum the month's shift hours (actual, falling back to scheduled) × pay_rate.
    start, nxt = _month_range(period)
    # Span scoping (owner directive 2026-08-07 — "employees could be at any store whether it is
    # their home store or not") — resolve by HOME STORE **union WHERE THEY ACTUALLY WORKED** this
    # period, via storeops.scope_emp_ids -> app.core.scope.reporting_employee_ids, NOT home_store
    # alone. A rep borrowed into a DM's span all month must show up on that DM's Total Compensation
    # report; a rep homed in-span but working elsewhere all month must not. Bounded to THIS report's
    # own period (since=start, until=last day of the period, i.e. the day before `nxt`) so the
    # "worked at" resolution never scans full shift/timelog history — same discipline
    # `_emp_ids_window_from_rows` established for the other scope_emp_ids call sites in
    # storeops/router.py. `eids is None` = unrestricted (admin / enforcement off) — unchanged from
    # before this fix, byte-identical for those callers (no extra reads, no filtering).
    period_until = (_date.fromisoformat(nxt) - timedelta(days=1)).isoformat() if start else None
    eids = scope_emp_ids(authorization, org_id, since=start or None, until=period_until)
    if eids is not None:
        emps = [e for e in emps if str(e.get("employee_id")) in eids]

    # Salary pay-basis (2026-07-27) — resolved ONCE for the whole period, reused per employee below.
    # Degrades to weekly-Monday defaults on any tenant-settings read failure (payroll_salary never
    # raises past this call).
    pp_settings = _tenant_pp_settings(org_id)
    period_lo = _date.fromisoformat(start) if start else None
    period_hi = (_date.fromisoformat(nxt) - timedelta(days=1)) if start else None
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
    salary_override_failed = False   # Gate-1 N5 — never a silent revert-to-hourly (see below)
    for e in emps:
        rate = float(e.get("pay_rate") or 0)
        hrs = round(hours_by_eid.get(e.get("employee_id"), 0.0), 1)
        wages = round(hrs * rate, 2)
        # Salary pay-basis override (2026-07-27) — SAME shared payroll_salary.derive_salary_pay used
        # by GET /payroll, so Total Compensation's "Base salary" never disagrees with the Payroll
        # Report for the same employee/period. `basis` stays 'hourly' (no-op) unless the pay_basis
        # column is present AND actually set to something else with a usable pay_amount.
        basis, salary_meta = "hourly", {}
        if "pay_basis" in e and period_lo and period_hi:
            basis, amount = payroll_salary.resolve_pay_basis(e)
            if basis != "hourly" and amount and amount > 0:
                try:
                    derived = payroll_salary.derive_salary_pay(
                        basis, amount, pp_settings, period_lo, period_hi,
                        payroll_salary.parse_date(e.get("hire_date")),
                        payroll_salary.parse_date(e.get("termination_date")))
                except Exception as ex:
                    derived = None
                    salary_override_failed = True
                    print(f"WARN salary pay-basis override failed for org {org_id} employee "
                          f"{e.get('employee_id')} on GET /compensation: {ex}")
                if derived is not None:
                    wages = derived["amount"]
                    salary_meta = {"pay_basis": basis, "salary_period_pay": derived["period_pay"],
                                    "salary_prorated": derived["prorated"]}
        keys = {str(e.get("name") or "").strip().upper(),
                str(e.get("epay_salesperson") or "").strip().upper()} - {""}
        cr = next((comm_by_key[k] for k in keys if k in comm_by_key), None)
        commission = round(float((cr or {}).get("total_payout") or 0), 2)
        cb = round(sum(cb_by_key[k] for k in keys if k in cb_by_key), 2)
        # A salaried employee with a configured pay_amount always shows even with 0 hours/rate/comm
        # this period (they still earn their salary) — everyone else keeps the original "nothing to
        # show" skip.
        if hrs == 0 and commission == 0 and not rate and not salary_meta:
            continue   # nothing to show for this person
        total = round(wages + commission - cb, 2)
        # Annualized projection: this period's total comp run-rate × 12 months.
        annualized = round(total * 12, 2)
        rows.append({"employee_id": e.get("employee_id"), "name": e.get("name"),
                     "store": e.get("home_store"), "pay_rate": rate, "hours": hrs,
                     "base_salary": wages, "commission": commission, "chargebacks": cb,
                     "total_comp": total, "annualized": annualized, **salary_meta})
        tot_w += wages; tot_c += commission; tot_cb += cb

    rows.sort(key=lambda r: -r["total_comp"])
    total_comp = round(tot_w + tot_c - tot_cb, 2)
    out = {"period": period, "rows": rows,
           "totals": {"base_salary": round(tot_w, 2), "commission": round(tot_c, 2),
                      "chargebacks": round(tot_cb, 2), "total_comp": total_comp,
                      "annualized": round(total_comp * 12, 2), "employees": len(rows)}}
    # Gate-1 N5 — this endpoint returns a dict (unlike GET /payroll's bare array), so the warning is
    # additive both as a response key AND a header for consistency with the other two salary surfaces.
    if salary_override_failed:
        out["salary_override_warning"] = "one or more employees' salary pay-basis figure could not be computed this period — see server logs"
        if response is not None:
            try:
                response.headers["X-Salary-Override-Warning"] = "salary override failed for one or more employees on GET /compensation"
            except Exception:
                pass
    return out


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


# ════════════════════════════════════════════════════════════════════════════════════════════════
# MULTI-FILE DOCUMENTS (migration 402, people-4) — a document/task holds a LIST of files (SS-card
# front + back, a multi-page form, …). New uploads APPEND; nothing is ever auto-deleted. Delete is a
# separate, explicit, always-audited action with two permission tiers:
#   ADMIN/HR  — may delete any file, any time.
#   EMPLOYEE  — may delete only a file THEY uploaded, and only while the owning task is still 'pending'
#               (never once it has moved to submitted/returned/verified/na). This is deliberately the
#               ONLY status that counts as "still in their editable in-progress state" — it is not
#               vacuous: a task legitimately sits at 'pending' with files still attached whenever HR
#               uses the existing "↺ Reset" action, or before the very first file of a task lands. The
#               day-to-day "I picked the wrong photo" case is handled client-side (the portal stages
#               picked files locally and lets the employee drop one from the batch BEFORE it's ever
#               POSTed — nothing to delete server-side because nothing was sent yet); this gate is the
#               server-enforced backstop for the case where a file genuinely IS already on the server
#               and the task hasn't left 'pending'. Flagged for Gate 2 exactly like the compliance
#               pack's override-hatch call: if the owner wants employees to also self-delete out of a
#               freshly-'submitted' (not yet reviewed) task, that is a bigger state-machine change and
#               deliberately NOT what this package builds — tell us at Gate 2 and we'll widen it.
# ════════════════════════════════════════════════════════════════════════════════════════════════
def _employee_can_delete_document(task_status, file_uploaded_role):
    """Pure function — the one and only rule an employee-initiated delete is judged against. Kept
    separate from any DB/HTTP concern so it's directly provable (see the proof harness)."""
    return task_status == "pending" and file_uploaded_role == "employee"


def _doc_row(org_id, employee_id, task_id):
    rows = (_so().table("employee_onboarding").select("*").eq("org_id", org_id)
            .eq("employee_id", employee_id).eq("task_id", task_id).limit(1).execute().data) or []
    return rows[0] if rows else {}


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


class OnboardingSaveCategoryIn(LaxModel):
    label: str = ""
    key: str = ""
    sort_order: Any = None


@router.post("/onboarding/categories")
def onboarding_save_category(body: OnboardingSaveCategoryIn, org_id: str = ORG_ID):
    label = (body.label or "").strip()
    if not label:
        raise HTTPException(400, "label required")
    row = {"org_id": org_id, "key": (body.key or _slug(label)).strip(),
           "label": label, "sort_order": int(body.sort_order or 100)}
    try:
        r = so_upsert("onboarding_category", row, "org_id,key")
    except Exception as e:
        raise HTTPException(400, f"Could not save category — is migration 073 applied? {e}")
    return (r or [row])[0]


class OnboardingUpdateCategoryIn(LaxModel):
    label: Any = None
    sort_order: Any = None
    is_active: Any = None


@router.patch("/onboarding/categories/{cat_id}")
def onboarding_update_category(cat_id: str, body: OnboardingUpdateCategoryIn, org_id: str = ORG_ID):
    upd = {k: getattr(body, k) for k in ("label", "sort_order", "is_active") if k in body.model_fields_set}
    r = _so().table("onboarding_category").update(upd).eq("org_id", org_id).eq("id", cat_id).execute()
    return (r.data or [{}])[0]


@router.delete("/onboarding/categories/{cat_id}")
def onboarding_delete_category(cat_id: str, org_id: str = ORG_ID):
    _so().table("onboarding_category").delete().eq("org_id", org_id).eq("id", cat_id).execute()
    return {"ok": True}


class OnboardingTaskIn(LaxModel):
    category_id: Any = None
    key: Any = None
    label: Any = None
    description: Any = None
    owner_role: Any = None
    doc_url: Any = None
    doc_label: Any = None
    is_fillable: Any = None
    requires_upload: Any = None
    applies_state: Any = None
    sort_order: Any = None
    is_active: Any = None
    requires_signature: Any = None
    form_fields: Any = None
    is_mandatory: Any = None
    work_auth: Any = None


@router.post("/onboarding/tasks")
def onboarding_save_task(body: OnboardingTaskIn, org_id: str = ORG_ID):
    label = (body.label or "").strip()
    if not label:
        raise HTTPException(400, "label required")
    row = {k: getattr(body, k) for k in TASK_FIELDS if k in body.model_fields_set}
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
def onboarding_update_task(task_id: str, body: OnboardingTaskIn, org_id: str = ORG_ID):
    upd = {k: getattr(body, k) for k in TASK_FIELDS if k in body.model_fields_set}
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
            # migration 402: the full multi-file list, each entry annotated with whether the EMPLOYEE (as
            # opposed to HR/admin) may delete it right now — see _employee_can_delete_document. Computed
            # here, once, server-side, so every caller (this HR-side payload AND _onboarding_bundle's
            # employee-facing trim of it below) renders the identical rule instead of re-deriving it.
            docs_raw = rec.get("documents") or []
            documents = [{**f, "employee_can_delete": _employee_can_delete_document(status, f.get("uploaded_role"))}
                        for f in docs_raw]
            tasks.append({**t, "status": status, "note": rec.get("note"),
                          "document_name": rec.get("document_name"),
                          "has_document": bool(rec.get("document_path")) or bool(documents),
                          "documents": documents,
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
            # DEFECT FIX (2026-07-14): "mandatory-DOC reconcile" (below, and this mandatory_progress) must
            # only count tasks the EMPLOYEE can act on. Before this fix, is_mand ignored owner_role, so
            # every HR/DM/market_manager checklist step (never individually toggled 'verified' for the
            # existing roster) counted as an outstanding "mandatory document" for literally every hire —
            # this is the actual root cause of the reconcile dry-run showing everyone as missing
            # everything, including employees (e.g. Jose Utero) whose real paperwork was 100% complete.
            # onboarding_doc_status (the Documents board, same file) already scopes this identical concept
            # to owner_role == 'employee'; this brings mandatory_progress + the reconcile join in line with
            # that precedent. A task's own "is_mandatory" flag (shown per-row above) is UNCHANGED — an
            # admin can still mark any task optional regardless of owner — this only narrows what counts
            # toward the aggregate "document" total/done and what the reconcile treats as a real gap.
            is_mand = (t.get("is_mandatory", True) is not False) and t.get("owner_role") == "employee"
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


class OnboardingSetProfileIn(LaxModel):
    work_state: Any = None


@router.patch("/onboarding/employee/{employee_id}")
def onboarding_set_profile(employee_id: str, body: OnboardingSetProfileIn, org_id: str = ORG_ID):
    """Set the employee's work_state (drives which state tax form shows)."""
    upd = {"org_id": org_id, "employee_id": employee_id}
    if "work_state" in body.model_fields_set:
        upd["work_state"] = _normalize_state(body.work_state)
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
    from app.core.tenant_middleware import caller_app_user_http
    u = caller_app_user_http(uid)
    if not u:
        raise HTTPException(403, "Your login isn't recognized for the company you are working in.")
    role = (u.get("role") or "").lower()
    ok = bool(u.get("super_admin")) or role in ("admin",) or "hr" in role
    if not ok:
        # Allow a custom role granted HR management. The Roles UI writes the HR grant as
        # permissions.modules.hr (the module toggle / "HR" template) — the SAME key the frontend
        # route guard and the rest of the app gate `/hr` on. The older top-level permissions.hr is
        # kept for backward-compatibility, but no UI path writes it, so a module-granted HR manager
        # would otherwise reach every HR page yet be denied THIS sensitive-reveal endpoint alone.
        try:
            rr = (get_supabase().schema("storeops").table("roles").select("permissions")
                  .eq("org_id", u.get("org_id") or ORG_ID).eq("name", u.get("role")).limit(1).execute().data) or []
            _perms = ((rr[0].get("permissions") if rr else {}) or {})
            if _perms.get("hr") or (_perms.get("modules") or {}).get("hr"):
                ok = True
        except Exception:
            pass
    if not ok:
        raise HTTPException(403, "Only HR managers and admins can view sensitive employee information.")
    return (u.get("org_id") or ORG_ID, u.get("email"), role)


@router.get("/onboarding/employee/{employee_id}/sensitive")
def onboarding_reveal_sensitive(employee_id: str, authorization: str = Header(default="")):
    """Decrypted sensitive intake values (bank / A-Number / any field a tenant marked private) for an authorized HR manager or
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


# ════════════════════════════════════════════════════════════════════════════════════════════════
# EMPLOYEE DATABASE report (owner directive 2026-07-29) — one exportable row per employee across
# storeops.employees (operational roster) + HR onboarding intake (employee_onboarding_profile.
# intake_data, Fernet-encrypted at rest) + the Documents board (workflow/onboarding state).
# Selection (which employees / which columns) is pick-don't-type on the frontend (RULE THREE) and
# drives BOTH the on-screen table AND every export identically (RULE FOUR).
#
# MASKING IS SERVER-SIDE, NEVER CLIENT-ONLY: SSN + direct-deposit ROUTING/ACCOUNT numbers are
# ALWAYS rendered masked (last 4 real, everything before it masked) in this endpoint's response
# UNLESS the caller passes reveal=true AND passes the STRICT admin/super-admin gate
# (`_require_admin_reveal`, below) — narrower than this file's existing `_require_hr_or_admin` (the
# base HR/admin page gate), per the owner's literal "only show the full number to the admin". A
# non-admin/non-reveal caller's response payload never contains a full value in the first place —
# reveal=true from a caller who fails the strict gate is REJECTED (403) BEFORE any employee row is
# read.
#
# WHAT'S ACTUALLY COLLECTED TODAY (investigated, not assumed — see docs/handoffs/people.md for the
# full write-up):
#   • name / phone / address / date_of_birth — real, plaintext columns on storeops.employees
#     (propagated from the onboarding intake form, migration 077). Shown as-is, never masked (not
#     classified sensitive anywhere else in this app either).
#   • email — storeops.employees.email (login/notification address). `personal_email` (a separate,
#     UNPROPAGATED intake field, migration 079) is surfaced alongside when the employee gave one.
#   • direct deposit (bank name / routing / account / type) — REAL, encrypted-at-rest values inside
#     employee_onboarding_profile.intake_data (migration 079 seeds dd_bank_name/dd_routing/
#     dd_account/dd_account_type as `sensitive` intake fields). Discovered dynamically PER ORG from
#     the tenant's actual onboarding_intake_field config (section='direct_deposit') rather than a
#     hard-coded key list (RULE TWO) — a tenant that renames/adds a direct-deposit field is picked
#     up automatically. Routing + account are masked by default (`_dd_field_is_masked`); bank name
#     and account type (Checking/Savings) are not — see that helper's docstring; OWNER-OVERRIDABLE,
#     flagged in the handoff.
#   • SSN — NOT HELD ANYWHERE in this product. Full SSN was never captured (migration 079's own
#     comment: "full SSN is intentionally NOT captured", kept only inside the uploaded W-4/I-9 PDFs,
#     which payroll and tax filing genuinely need). The one SSN-shaped value that did exist —
#     employee_onboarding_profile.verify_ssn4, a last-4 used only as an identity gate on the
#     credential-less onboarding link — was REMOVED by migration 909 on the owner's instruction to
#     take this data category out of the system. The onboarding gate is date-of-birth only now.
#     There is deliberately no SSN column in the field catalog below: a column that renders
#     "(not collected)" still tells every reader the product expects to hold one, and the point of
#     the removal is that it does not. Do not reintroduce SSN storage without an explicit owner
#     decision — not holding it is what keeps a breach here out of notification territory.
#   • Document status — reuses `onboarding_doc_status()` (the SAME Documents-board computation, same
#     scope: ACTIVE roster only) rather than re-deriving it. An inactive employee (only reachable via
#     include_inactive=true) shows an honest "(inactive — not on Documents board)" rather than a
#     fabricated status.
# ════════════════════════════════════════════════════════════════════════════════════════════════
_DD_MASK_HINT = re.compile(r"(account|routing)", re.I)
_DD_UNMASK_HINT = re.compile(r"type", re.I)  # e.g. dd_account_type (Checking/Savings) — nothing to mask


def _mask_last4(raw) -> str:
    """Show only the last 4 characters of `raw`; everything before it becomes 'x'. Empty/None -> ''.
    A value of 4 chars or fewer is masked in full (nothing safe to reveal as 'the rest')."""
    s = ("" if raw is None else str(raw)).strip()
    if not s:
        return ""
    if len(s) <= 4:
        return "x" * len(s)
    return ("x" * (len(s) - 4)) + s[-4:]



def _dd_field_is_masked(key: str) -> bool:
    """Which direct-deposit intake fields get last-4 masking by default: account + routing numbers
    (the actual money-movement identifiers) — NOT the bank name (not itself sensitive) and NOT a
    '_type' select field (e.g. Checking/Savings has no meaningful 'last 4'). Matched by KEY NAME so a
    tenant's custom direct-deposit field is covered by the same rule without a hard-coded key list
    (RULE TWO). OWNER-OVERRIDABLE: routing numbers are semi-public bank identifiers in most fintech
    UIs (not secret on their own); masked here anyway, conservatively, because paired with the
    account number they're what actually moves money — flagged in docs/handoffs/people.md."""
    k = (key or "").lower()
    if _DD_UNMASK_HINT.search(k):
        return False
    return bool(_DD_MASK_HINT.search(k))


def _require_admin_reveal(authorization: str):
    """STRICTER than `_require_hr_or_admin` above: admin / super_admin ONLY — no generic 'hr'-titled
    role, no custom permissions.hr grant. Used only for the Employee Database report's 'show full
    direct-deposit numbers' reveal (owner directive 2026-07-29: 'only show the full number to
    the admin'). Same resolution mechanics + same open-app parity fallback as `_require_hr_or_admin`
    (this file, above) so both gates log/behave identically apart from the stricter `ok` predicate."""
    from app.modules.core.router import _uid_from_token
    uid = _uid_from_token(authorization)
    if not uid:
        if _rbac_enforced():
            raise HTTPException(401, "Sign in as an admin to reveal full sensitive values.")
        return (ORG_ID, "(open-app)", "open")
    from app.core.tenant_middleware import caller_app_user_http
    u = caller_app_user_http(uid)
    if not u:
        raise HTTPException(403, "Your login isn't recognized for the company you are working in.")
    role = (u.get("role") or "").lower()
    if not (bool(u.get("super_admin")) or role == "admin"):
        raise HTTPException(403, "Only admins/super-admins can reveal full sensitive values (direct-deposit numbers and any field this company marked private).")
    return (u.get("org_id") or ORG_ID, u.get("email"), role)


# Always-present columns (identity/contact/address/personal/onboarding) — never masked, gated only
# by the page-level HR/admin gate. `section` groups the frontend's column picker.
_EMPDB_BASE_FIELDS = [
    {"key": "employee_id",     "label": "Employee ID",             "section": "identity"},
    {"key": "name",            "label": "Name",                    "section": "identity"},
    {"key": "role",            "label": "Role / Title",            "section": "identity"},
    {"key": "home_store",      "label": "Home Store",              "section": "identity"},
    {"key": "is_active",       "label": "Active",                  "section": "identity"},
    {"key": "hire_date",       "label": "Hire Date",                "section": "identity"},
    {"key": "phone",           "label": "Phone",                    "section": "contact"},
    {"key": "email",           "label": "Email",                    "section": "contact"},
    {"key": "personal_email",  "label": "Personal Email (intake)",  "section": "contact"},
    {"key": "address_line1",   "label": "Address line 1",            "section": "address"},
    {"key": "address_line2",   "label": "Address line 2",            "section": "address"},
    {"key": "city",            "label": "City",                     "section": "address"},
    {"key": "state",           "label": "State",                     "section": "address"},
    {"key": "zip",             "label": "ZIP",                        "section": "address"},
    {"key": "date_of_birth",   "label": "Date of Birth",             "section": "personal"},
    {"key": "doc_status",      "label": "Document Status",           "section": "onboarding"},
    {"key": "workflow_status", "label": "Onboarding Stage",          "section": "onboarding"},
    {"key": "docs_sent_at",    "label": "Packet Sent",               "section": "onboarding"},
]


def _empdb_dd_fields(org_id):
    """Direct-deposit columns discovered from the tenant's OWN onboarding_intake_field config
    (section='direct_deposit'), never a hard-coded key list (RULE TWO). Empty list if the tenant has
    no direct-deposit fields configured (never fabricated)."""
    out = []
    for f in _public_intake_fields(org_id):
        if (f.get("section") or "") != "direct_deposit":
            continue
        out.append({"key": f["key"], "label": f["label"], "section": "direct_deposit",
                     "sensitive": True, "masked": _dd_field_is_masked(f["key"])})
    return out


@router.get("/employee-database/fields")
def hr_employee_database_fields(authorization: str = Header(default="")):
    """Column CATALOG for the Employee Database report's field/column picker — metadata only, never
    a PII value. Gated identically to the report itself (HR/admin)."""
    org_id, _email, _role = _require_hr_or_admin(authorization)
    return {"fields": _EMPDB_BASE_FIELDS + _empdb_dd_fields(org_id)}


@router.get("/employee-database")
def hr_employee_database(employee_ids: str = "", fields: str = "", include_inactive: bool = True,
                          reveal: bool = False, authorization: str = Header(default="")):
    """The Employee Database report (owner directive 2026-07-29): one row per employee, every PII
    field this app actually collects, plus document status from the Documents board. `employee_ids`
    / `fields` (both comma-separated) are the server-side honoring of the frontend's pick-don't-type
    employee multi-select + column picker — omit either to get the full roster / every column.

    GATE FIRST, before any employee row is read (contract proof requirement): reveal=true requires
    the STRICT admin/super-admin gate (`_require_admin_reveal` — 403 before any data read if the
    caller doesn't qualify); reveal=false (the default) uses the page's own HR/admin gate
    (`_require_hr_or_admin`). Every reveal=true call that passes the gate is written to the SAME
    onboarding_event audit trail the existing sensitive-reveal endpoint uses (`_log_event`).

    org_id is resolved from the CALLER'S OWN membership, not a query param — the same stricter
    posture already used by this file's other maximum-sensitivity endpoints (the sensitive-reveal
    endpoint above, /security-status, /onboarding/encrypt-existing) given the class of data here."""
    if reveal:
        org_id, email, role = _require_admin_reveal(authorization)
    else:
        org_id, email, role = _require_hr_or_admin(authorization)

    so = _so()
    ids = [i.strip() for i in employee_ids.split(",") if i.strip()]
    field_keys = [f.strip() for f in fields.split(",") if f.strip()]
    want = (lambda k: (not field_keys) or (k in field_keys))

    q = so.table("employees").select("*").eq("org_id", org_id)
    if not include_inactive:
        q = q.eq("is_active", True)
    if ids:
        q = q.in_("employee_id", ids)
    try:
        emps = q.order("name").execute().data or []
    except Exception:
        # Degrade: a not-yet-migrated tenant may be missing a newer column (hire_date/date_of_birth,
        # migration 077) from PostgREST's schema cache — fall back to the always-safe core columns
        # rather than 500ing the whole report.
        q2 = so.table("employees").select("employee_id,name,home_store,role,is_active,email,phone").eq("org_id", org_id)
        if not include_inactive:
            q2 = q2.eq("is_active", True)
        if ids:
            q2 = q2.in_("employee_id", ids)
        emps = q2.order("name").execute().data or []

    emp_ids_in_scope = [e.get("employee_id") for e in emps if e.get("employee_id")]

    profs = {}
    if emp_ids_in_scope:
        try:
            prows = (so.table("employee_onboarding_profile")
                     .select("employee_id,intake_data,workflow_status,docs_sent_at,invited_at")
                     .eq("org_id", org_id).in_("employee_id", emp_ids_in_scope).execute().data) or []
            profs = {p.get("employee_id"): p for p in prows}
        except Exception:
            profs = {}

    # Document status — reuse the SAME Documents-board computation (active-roster scope, matching
    # its existing product semantics exactly; never re-derived/duplicated).
    doc_by_id = {}
    try:
        ds = onboarding_doc_status(org_id=org_id)
        if ds.get("ready"):
            doc_by_id = {r.get("employee_id"): r for r in ds.get("employees", [])}
    except Exception:
        doc_by_id = {}

    dd_defs = _empdb_dd_fields(org_id)
    out_rows = []
    for e in emps:
        eid = e.get("employee_id")
        row: dict = {"employee_id": eid}
        if want("name"):
            row["name"] = e.get("legal_name") or e.get("name")
        if want("role"):
            row["role"] = e.get("role")
        if want("home_store"):
            row["home_store"] = e.get("home_store")
        if want("is_active"):
            row["is_active"] = e.get("is_active") is not False
        if want("hire_date"):
            row["hire_date"] = e.get("hire_date")
        if want("phone"):
            row["phone"] = e.get("phone")
        if want("email"):
            row["email"] = e.get("email")
        if want("address_line1"):
            row["address_line1"] = e.get("address_line1")
        if want("address_line2"):
            row["address_line2"] = e.get("address_line2")
        if want("city"):
            row["city"] = e.get("city")
        if want("state"):
            row["state"] = e.get("state")
        if want("zip"):
            row["zip"] = e.get("zip")
        if want("date_of_birth"):
            row["date_of_birth"] = e.get("date_of_birth")

        prof = profs.get(eid) or {}
        intake = dict(prof.get("intake_data") or {})
        if want("personal_email"):
            row["personal_email"] = intake.get("personal_email") or ""

        for d in dd_defs:
            k = d["key"]
            if not want(k):
                continue
            raw = intake.get(k)
            if not raw or not str(raw).strip():
                row[k] = ""
                continue
            val = crypto.decrypt(raw)
            if val is None:
                row[k] = "(unavailable — encryption key rotated/lost)"
            elif d.get("masked") and not reveal:
                row[k] = _mask_last4(val)
            else:
                row[k] = val

        doc = doc_by_id.get(eid)
        if want("doc_status"):
            if doc:
                row["doc_status"] = f"{doc.get('verified', 0)}/{doc.get('total', 0)} verified" + \
                    (f" · {doc.get('pending')} pending" if doc.get("pending") else "")
            else:
                row["doc_status"] = "(inactive — not on Documents board)" if e.get("is_active") is False \
                    else "(no onboarding record)"
        if want("workflow_status"):
            row["workflow_status"] = (doc or {}).get("workflow_status") or prof.get("workflow_status")
        if want("docs_sent_at"):
            row["docs_sent_at"] = (doc or {}).get("docs_sent_at") or prof.get("docs_sent_at") or prof.get("invited_at")

        out_rows.append(row)

    if reveal:
        _log_event(org_id, None, "employee_database_reveal", actor=email,
                   detail={"by": email, "role": role, "employee_ids": emp_ids_in_scope,
                           "fields": [d["key"] for d in dd_defs if d.get("masked")]})

    return {"ready": True, "reveal": bool(reveal), "encryption_enabled": crypto.is_enabled(),
            "fields": _EMPDB_BASE_FIELDS + dd_defs, "employees": out_rows}


class OnboardingUpdateStatusIn(LaxModel):
    status: Any = None
    verified_by: Any = None
    note: Any = None


@router.post("/onboarding/employee/{employee_id}/task/{task_id}")
def onboarding_update_status(employee_id: str, task_id: str, body: OnboardingUpdateStatusIn, org_id: str = ORG_ID):
    """HR/DM/MM marks a task verified / not-applicable, or adds a note. status=verified stamps who+when."""
    status = (body.status or "").strip()
    if status and status not in ONBOARD_STATUSES:
        raise HTTPException(400, f"bad status '{status}'")
    row = {"org_id": org_id, "employee_id": employee_id, "task_id": task_id, "updated_at": _now_iso()}
    if status:
        row["status"] = status
        if status == "verified":
            row["verified_by"] = (body.verified_by or "").strip() or None
            row["verified_at"] = _now_iso()
    if "note" in body.model_fields_set:
        row["note"] = body.note
    try:
        _so().table("employee_onboarding").upsert(row, on_conflict="org_id,employee_id,task_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not update — is migration 073 applied? {e}")
    return {"ok": True}


async def _do_onboard_upload(org_id, employee_id, task_id, file, who, uploaded_role="employee"):
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
    # migration 402: APPEND to the task's document list — this is the fix for the "uploading a new file
    # deletes the previous one" bug. Read-modify-write on the existing row (same low-concurrency posture
    # every other upsert in this file already has — a per-task upload isn't a high-contention path).
    existing = _doc_row(org_id, employee_id, task_id)
    docs_list = list(existing.get("documents") or [])
    file_id = uuid.uuid4().hex
    docs_list.append({"id": file_id, "path": path, "name": safe,
                      "content_type": file.content_type or "application/octet-stream",
                      "uploaded_at": _now_iso(), "uploaded_by": who, "uploaded_role": uploaded_role})
    row = {"org_id": org_id, "employee_id": employee_id, "task_id": task_id, "status": "submitted",
           "document_path": path, "document_name": safe, "documents": docs_list,
           "submitted_at": _now_iso(), "updated_at": _now_iso(),
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
        # migration 402 not yet applied (no `documents` column) — degrade to the pre-402 single-document
        # behavior: the row still upserts, just without the file list (the LAST upload replaces
        # document_path, exactly like before this package). Multi-file/delete only activate once 402 runs.
        row_pre402 = {k: v for k, v in row.items() if k != "documents"}
        try:
            _so().table("employee_onboarding").upsert(row_pre402, on_conflict="org_id,employee_id,task_id").execute()
        except Exception:
            # pre-082 database — fall back further to the legacy row shape so uploads never break
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
                "file_id": file_id, "documents_count": len(docs_list),
                "note": "This document looks incomplete — it was returned with the missing items listed."}
    return {"ok": True, "document_name": safe, "status": "submitted",
            "checked": bool(check.get("checkable")), "file_id": file_id, "documents_count": len(docs_list),
            "note": None if check.get("checkable") else
            "Not machine-checkable (scan/photo or flat PDF) — HR will review the signature by eye."}


@router.post("/onboarding/employee/{employee_id}/upload")
async def onboarding_upload(employee_id: str, task_id: str = Form(...), file: UploadFile = File(...),
                            uploader: str = Form(""), org_id: str = ORG_ID):
    """HR uploads a completed document on the employee's behalf (status → submitted, appended to the
    task's file list — migration 402)."""
    return await _do_onboard_upload(org_id, employee_id, task_id, file, uploader or "HR", uploaded_role="admin")


@router.get("/onboarding/employee/{employee_id}/task/{task_id}/doc")
def onboarding_doc_url(employee_id: str, task_id: str, org_id: str = ORG_ID):
    """A 1-hour signed URL to the MOST RECENT file on this task (back-compat single-file view — a
    pre-402 caller, or a task that only ever had one file, still works exactly as before). A task with
    multiple files should use GET .../document/{file_id} per file instead — see the `documents` list on
    onboarding_for_employee's task payload."""
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


@router.get("/onboarding/employee/{employee_id}/task/{task_id}/document/{file_id}")
def onboarding_document_url(employee_id: str, task_id: str, file_id: str, org_id: str = ORG_ID):
    """A 1-hour signed URL for ONE file in a multi-file document list (migration 402) — HR review side."""
    row = _doc_row(org_id, employee_id, task_id)
    target = next((f for f in (row.get("documents") or []) if str(f.get("id")) == str(file_id)), None)
    if not target or not target.get("path"):
        raise HTTPException(404, "no such file")
    url = _sign_onboard_path(target["path"])
    if not url:
        raise HTTPException(500, "could not sign url")
    return {"url": url, "name": target.get("name")}


def _do_onboard_delete_document(org_id, employee_id, task_id, file_id, actor, actor_role):
    """Shared delete core for the admin ('always') and employee ('only-while-pending') surfaces —
    migration 402. Removes the file from the task's `documents` list, mirrors document_path/document_name
    to whatever remains (or clears them if none), best-effort removes the storage object (the DB list is
    the source of truth for what's officially on file — a storage remove failure never blocks the delete,
    same posture as the existing template/sample delete endpoints), and always logs an audited
    onboarding_event regardless of who did it or whether the underlying storage remove succeeded."""
    row = _doc_row(org_id, employee_id, task_id)
    if not row:
        raise HTTPException(404, "no such document task for this employee")
    docs = list(row.get("documents") or [])
    target = next((f for f in docs if str(f.get("id")) == str(file_id)), None)
    if not target:
        raise HTTPException(404, "file not found")
    if actor_role == "employee" and not _employee_can_delete_document(row.get("status") or "pending", target.get("uploaded_role")):
        raise HTTPException(403, "This item has already been submitted — HR can remove a file for you from "
                                  "here on, but you can still add a replacement file yourself.")
    remaining = [f for f in docs if str(f.get("id")) != str(file_id)]
    latest = sorted(remaining, key=lambda f: f.get("uploaded_at") or "")[-1] if remaining else None
    upd = {"documents": remaining, "document_path": (latest or {}).get("path"),
           "document_name": (latest or {}).get("name"), "updated_at": _now_iso()}
    try:
        _so().table("employee_onboarding").update(upd).eq("org_id", org_id) \
            .eq("employee_id", employee_id).eq("task_id", task_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not delete — is migration 402 applied? {e}")
    path = target.get("path")
    if path:
        try:
            get_supabase().storage.from_(ONBOARD_BUCKET).remove([path])
        except Exception:
            pass   # best-effort — the DB list (already updated above) is the source of truth
    task = _task_row(org_id, task_id)
    _log_event(org_id, employee_id, "doc_deleted", actor=actor,
               detail={"task": task.get("label"), "file_name": target.get("name"), "file_id": str(file_id),
                       "deleted_by_role": actor_role, "originally_uploaded_by": target.get("uploaded_by"),
                       "originally_uploaded_role": target.get("uploaded_role")})
    return {"ok": True, "documents_count": len(remaining)}


@router.delete("/onboarding/employee/{employee_id}/task/{task_id}/document/{file_id}")
def onboarding_delete_document(employee_id: str, task_id: str, file_id: str, actor: str = "", org_id: str = ORG_ID):
    """ADMIN/HR: delete any uploaded file on this task, at any time (migration 402)."""
    return _do_onboard_delete_document(org_id, employee_id, task_id, file_id, actor or "HR", "admin")


# ── Root-cause recovery tool (migration 402) — the PRE-fix bug overwrote employee_onboarding.document_path
# on every re-upload without ever deleting the storage object, so a pre-402 "replaced" file is orphaned
# (unreferenced), not destroyed. The storage path convention (`{org}/{employee}/{uuid}_{filename}`) carries
# no task_id, so which task an orphan belonged to can't be inferred automatically — these two endpoints are
# the human-in-the-loop half: find candidate lost files by listing what's in an employee's storage folder
# that no current row points to, then let HR re-attach one to the right task by looking at its filename.
@router.get("/onboarding/employee/{employee_id}/orphaned-files")
def onboarding_orphaned_files(employee_id: str, org_id: str = ORG_ID):
    """Files sitting in this employee's onboarding-docs storage folder that no current employee_onboarding
    row references — almost always pre-402 uploads a later re-upload silently overwrote. Best-effort: a
    person still has to look at the filename to know which document it was."""
    prefix = f"{org_id}/{employee_id}"
    try:
        objects = get_supabase().storage.from_(ONBOARD_BUCKET).list(prefix) or []
    except Exception as e:
        raise HTTPException(400, f"Could not list storage — {e}")
    referenced = set()
    try:
        rows = (_so().table("employee_onboarding").select("document_path,documents,signature_path")
                .eq("org_id", org_id).eq("employee_id", employee_id).execute().data) or []
    except Exception:
        rows = []
    for r in rows:
        if r.get("document_path"):
            referenced.add(r["document_path"])
        if r.get("signature_path"):
            referenced.add(r["signature_path"])
        for f in (r.get("documents") or []):
            if f.get("path"):
                referenced.add(f["path"])
    out = []
    for o in (objects or []):
        name = o.get("name") if isinstance(o, dict) else None
        if not name or not (o.get("id") or o.get("metadata")):
            continue   # skip pseudo-folder placeholder entries the storage API can include
        full_path = f"{prefix}/{name}"
        if full_path in referenced:
            continue
        out.append({"path": full_path, "name": name,
                    "last_modified": o.get("updated_at") or o.get("created_at"),
                    "size": (o.get("metadata") or {}).get("size"),
                    "url": _sign_onboard_path(full_path)})
    out.sort(key=lambda x: x.get("last_modified") or "", reverse=True)
    return {"ok": True, "orphaned": out, "count": len(out)}


class OnboardingReattachOrphanIn(LaxModel):
    path: Any = None
    name: Any = None
    actor: Any = None


@router.post("/onboarding/employee/{employee_id}/task/{task_id}/reattach-orphan")
def onboarding_reattach_orphan(employee_id: str, task_id: str, body: OnboardingReattachOrphanIn, org_id: str = ORG_ID):
    """HR manually re-attaches a recovered orphaned file (see GET .../orphaned-files) to a task's
    document list. Never moves/deletes the storage object — just adds a `documents[]` entry pointing at
    it, exactly like a normal upload would; recorded as its own audited event (doc_recovered), distinct
    from an ordinary upload, so the trail is honest about what happened. Body: {path, name?, actor?}."""
    path = (body.path or "").strip()
    prefix = f"{org_id}/{employee_id}/"
    if not path.startswith(prefix):
        raise HTTPException(400, "that file doesn't belong to this employee")
    row = _doc_row(org_id, employee_id, task_id)
    docs = list(row.get("documents") or [])
    if any(f.get("path") == path for f in docs):
        raise HTTPException(400, "already attached to this task")
    raw_name = path.rsplit("/", 1)[-1]
    default_name = raw_name.split("_", 1)[-1] if "_" in raw_name else raw_name
    entry = {"id": uuid.uuid4().hex, "path": path, "name": (body.name or "").strip() or default_name,
             "content_type": None, "uploaded_at": _now_iso(),
             "uploaded_by": (body.actor or "HR") + " (recovered)", "uploaded_role": "recovered"}
    docs.append(entry)
    upd = {"org_id": org_id, "employee_id": employee_id, "task_id": task_id,
           "documents": docs, "document_path": path, "document_name": entry["name"], "updated_at": _now_iso()}
    try:
        _so().table("employee_onboarding").upsert(upd, on_conflict="org_id,employee_id,task_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not attach — is migration 402 applied? {e}")
    task = _task_row(org_id, task_id)
    _log_event(org_id, employee_id, "doc_recovered", actor=(body.actor or "HR"),
               detail={"task": task.get("label"), "path": path, "file_name": entry["name"]})
    return {"ok": True, "file_id": entry["id"]}


# ── Credential-less QR access (token + DOB/last-4 gate) ─────────────────────────────────────────────
class OnboardingMintTokenIn(LaxModel):
    verify_kind: Any = None
    verify_value: Any = None
    expires_days: Any = None


@router.post("/onboarding/employee/{employee_id}/token")
def onboarding_mint_token(employee_id: str, body: OnboardingMintTokenIn, org_id: str = ORG_ID):
    """Issue (or rotate) the QR access token + identity gate. Body: verify_kind ('dob'),
    verify_value, expires_days? Returns the token + the portal path the QR should encode.

    Date of birth is the ONLY gate. The last-4-SSN alternative was removed with the rest of the
    SSN data (mig 909); `verify_kind` is kept in the body so an older client sending 'dob'
    explicitly still works, and anything else is rejected rather than silently downgraded."""
    kind = (body.verify_kind or "dob").strip()
    val = (body.verify_value or "").strip()
    if kind != "dob":
        raise HTTPException(400, "verify_kind must be 'dob' — last-4 SSN verification was removed")
    if not val:
        raise HTTPException(400, "verify_value required (the employee's date of birth)")
    token = secrets.token_urlsafe(24)
    row = {"org_id": org_id, "employee_id": employee_id, "access_token": token, "verify_kind": kind,
           "token_active": True, "verify_dob": val[:10], "token_expires_at": None}
    days = body.expires_days
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
    # Date of birth only. A profile still carrying verify_kind='ssn4' cannot match here — mig 909
    # deactivates those tokens rather than leaving a door whose key no longer exists.
    if prof.get("verify_kind") not in (None, "", "dob"):
        return False
    return value[:10] == str(prof.get("verify_dob") or "")[:10]


@router.get("/public/onboarding/{token}")
def public_onboarding_meta(token: str):
    """Step 1 (credential-less): returns ONLY which identity gate to show — no employee data yet."""
    prof = _profile_by_token(token)
    if not _token_valid(prof):
        raise HTTPException(404, "This onboarding link is invalid or has expired. Ask HR for a new QR code.")
    return {"ok": True, "verify_kind": prof.get("verify_kind") or "dob"}


class PublicOnboardingGateIn(LaxModel):
    value: Any = None


@router.post("/public/onboarding/{token}")
def public_onboarding_view(token: str, body: PublicOnboardingGateIn):
    """Step 2: gate check → the employee's first name + their own checklist items (links + uploads)."""
    prof = _profile_by_token(token)
    if not _token_valid(prof):
        raise HTTPException(404, "This onboarding link is invalid or has expired.")
    if not _check_gate(prof, body.value):
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
    res = await _do_onboard_upload(prof["org_id"], prof["employee_id"], task_id, file, "employee", uploaded_role="employee")
    _recompute_status(_so(), prof["org_id"], prof["employee_id"], actor="employee")
    return res


@router.get("/public/onboarding/{token}/task/{task_id}/document/{file_id}")
def public_onboarding_document_url(token: str, task_id: str, file_id: str, value: str = ""):
    """Step 3 companion: a signed URL for ONE previously-uploaded file (migration 402)."""
    prof = _profile_by_token(token)
    if not _token_valid(prof):
        raise HTTPException(404, "This onboarding link is invalid or has expired.")
    if not _check_gate(prof, value):
        raise HTTPException(403, "Identity check failed.")
    return onboarding_document_url(prof["employee_id"], task_id, file_id, org_id=prof["org_id"])


@router.delete("/public/onboarding/{token}/task/{task_id}/document/{file_id}")
def public_onboarding_delete_document(token: str, task_id: str, file_id: str, value: str = ""):
    """Step 3 companion: the employee deletes a file THEY uploaded, only while the task is still
    'pending' (migration 402) — same rule as the logged-in portal, see _employee_can_delete_document."""
    prof = _profile_by_token(token)
    if not _token_valid(prof):
        raise HTTPException(404, "This onboarding link is invalid or has expired.")
    if not _check_gate(prof, value):
        raise HTTPException(403, "Identity check failed.")
    return _do_onboard_delete_document(prof["org_id"], prof["employee_id"], task_id, file_id, "employee", "employee")


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


class IntakeFieldIn(LaxModel):
    key: Any = None
    label: Any = None
    section: Any = None
    field_type: Any = None
    options: Any = None
    required: Any = None
    propagate_to: Any = None
    sensitive: Any = None
    help_text: Any = None
    sort_order: Any = None
    is_active: Any = None


@router.post("/onboarding/intake-fields")
def intake_field_save(body: IntakeFieldIn, org_id: str = ORG_ID):
    label = (body.label or "").strip()
    if not label:
        raise HTTPException(400, "label required")
    row = {k: getattr(body, k) for k in INTAKE_FIELD_COLS if k in body.model_fields_set}
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
def intake_field_update(field_id: str, body: IntakeFieldIn, org_id: str = ORG_ID):
    upd = {k: getattr(body, k) for k in INTAKE_FIELD_COLS if k in body.model_fields_set}
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
    #
    # BUG FIX (2026-07-27, owner report "employee completed all information but no information can be
    # seen at our end"): this used to raise a 400 for the WHOLE submission the instant DD fields carried
    # a value without initials — discarding every OTHER already-filled field (name/address/emergency
    # contact) in the SAME payload, since the portal always posts the one merged form in a single call.
    # An employee who filled in everything but missed the small initials box under the DD disclaimer
    # lost their ENTIRE submission with zero persisted trace: intake_submitted_at never got set, so the
    # admin's "Captured information" card (gated on that flag, see hr/onboarding/[employeeId]/page.tsx)
    # rendered nothing at all — exactly the reported symptom. Now: withhold ONLY the direct-deposit
    # fields until the disclaimer is acknowledged; every other field in the same submission still saves,
    # and the response tells the caller the DD section specifically still needs initials.
    dd_keys_present = [k for k in (data or {}) if by_key.get(k, {}).get("section") == "direct_deposit"
                       and str(data.get(k, "")).strip()]
    dd_disclaimer_ok = True
    if dd_keys_present and not prof.get("dd_disclaimer_signed_at"):
        initials = (data.get("dd_disclaimer_initials") or "").strip()
        if initials:
            _sign_dd_disclaimer(org_id, employee_id, initials, actor=actor)
        else:
            dd_disclaimer_ok = False
    stored = dict(prof.get("intake_data") or {})
    emp_upd, propagated = {}, []
    for k, v in (data.items() if isinstance(data, dict) else []):
        f = by_key.get(k)
        if not f:
            continue
        if f.get("section") == "direct_deposit" and not dd_disclaimer_ok:
            continue  # never store bank details before the disclaimer is acknowledged — but this must
                      # NEVER block saving the rest of the form (see bug-fix note above)
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
               detail={"fields": list(stored.keys()), "propagated": propagated,
                       **({"dd_disclaimer_pending": True} if (dd_keys_present and not dd_disclaimer_ok) else {})})
    _recompute_status(so, org_id, employee_id, actor=actor)
    resp = {"ok": True, "propagated": propagated, "work_state": pupd.get("work_state")}
    if dd_keys_present and not dd_disclaimer_ok:
        # Honest partial-success: everything else in this submission was saved; only the bank/DD
        # fields were withheld pending the disclaimer initials. Callers (portal + logged-in "me")
        # surface this distinctly (never as a plain green "saved" — see the frontend fix alongside
        # this one) so the employee knows exactly what still needs their attention.
        resp["dd_disclaimer_pending"] = True
        resp["warning"] = ("Everything else was saved. Direct-deposit details still need your typed "
                            "initials on the disclaimer above before they can be saved — add them and "
                            "save again.")
    return resp


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


class OnboardingMeDdDisclaimerIn(LaxModel):
    initials: Any = None


@router.post("/onboarding/me/dd-disclaimer")
def onboarding_me_dd_disclaimer(body: OnboardingMeDdDisclaimerIn, authorization: str = Header(default="")):
    me = _me_from_token(authorization)
    return _sign_dd_disclaimer(me["org_id"], me["employee_id"], body.initials, actor="employee")


class PublicOnboardingDdDisclaimerIn(LaxModel):
    value: Any = None
    initials: Any = None


@router.post("/public/onboarding/{token}/dd-disclaimer")
def public_onboarding_dd_disclaimer(token: str, body: PublicOnboardingDdDisclaimerIn):
    prof = _profile_by_token(token)
    if not _token_valid(prof):
        raise HTTPException(404, "This onboarding link is invalid or has expired.")
    if not _check_gate(prof, body.value):
        raise HTTPException(403, "Identity check failed.")
    return _sign_dd_disclaimer(prof["org_id"], prof["employee_id"], body.initials, actor="employee")


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


async def _send_invite(org_id, employee, method="link", *, dob=None, role_name=None,
                       expires_days=30, actor="HR", send_email_flag=True,
                       authorization="", x_active_org=""):
    """Prepare + (optionally) email an onboarding invite. Returns a per-employee result dict.
    method='link' → mint a token portal (needs a DOB gate).
    method='login' → ensure an app_users role + a Supabase login, email the temp credentials.

    `authorization`/`x_active_org` are the CALLER's own request headers, threaded through to the
    core user-write routes below. They gate on `_require_setting(..., "security")`, which resolves
    the caller from the verified JWT — an in-process call that passes nothing binds FastAPI's
    `Header(default="")` SENTINEL there, resolves to no caller, and raises 401 "not authenticated"
    (regression from the 2026-08-05 core RBAC hardening; live symptom was the Documents board
    reporting `401: not authenticated` per employee instead of sending the packet)."""
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
            await _maybe_await(assign_role({"email": email, "full_name": employee.get("name"),
                               "role": (role_name or "sales_rep"), "employee_id": employee_id}, org_id,
                               authorization=authorization, x_active_org=x_active_org))
            login = await _maybe_await(core_create_login({"email": email}, org_id,
                                                         authorization=authorization,
                                                         x_active_org=x_active_org))
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
        if not dob:
            return {**result, "ok": False,
                    "error": "a date of birth is required for a link invite — use the login method instead"}
        token = secrets.token_urlsafe(24)
        row = {**base, "access_token": token, "verify_kind": "dob", "token_active": True,
               "verify_dob": str(dob)[:10], "token_expires_at": None}
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


class OnboardingInviteOneIn(LaxModel):
    method: Any = None
    dob: Any = None
    role_name: Any = None
    expires_days: Any = 30
    actor: Any = None
    send_email: Any = True


@router.post("/onboarding/employee/{employee_id}/invite")
async def onboarding_invite_one(employee_id: str, body: OnboardingInviteOneIn, org_id: str = ORG_ID,
                                authorization: str = Header(default=""),
                                x_active_org: str = Header(default="")):
    """Invite/re-invite ONE hire. Body: method('link'|'login'), dob? (link gate), role_name?,
    expires_days?, send_email? (default true). Returns the link or temp credentials."""
    so = _so()
    emp = (so.table("employees").select("employee_id,name,email")
           .eq("org_id", org_id).eq("employee_id", employee_id).limit(1).execute().data) or []
    if not emp:
        raise HTTPException(404, "employee not found")
    res = await _send_invite(org_id, emp[0], (body.method or "link").strip(),
                             dob=(body.dob or "").strip() or None,
                             role_name=(body.role_name or "").strip() or None,
                             expires_days=body.expires_days,
                             actor=(body.actor or "HR"),
                             send_email_flag=body.send_email,
                             authorization=authorization, x_active_org=x_active_org)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "invite failed")
    return res


class OnboardingInviteBulkIn(LaxModel):
    method: Any = None
    employee_ids: Any = None
    all_incomplete: Any = None
    role_name: Any = None
    send_email: Any = True
    actor: Any = None


@router.post("/onboarding/invite-bulk")
async def onboarding_invite_bulk(body: OnboardingInviteBulkIn, org_id: str = ORG_ID,
                                 authorization: str = Header(default=""),
                                 x_active_org: str = Header(default="")):
    """Invite MANY hires in one action (for a roster of existing staff). Body:
    method('link'|'login', default 'login' — link needs a per-person DOB we usually don't have),
    employee_ids?[] (omit + all_incomplete=true → everyone without a completed onboarding),
    all_incomplete?, send_email? (default true). Returns a per-employee result summary."""
    so = _so()
    method = (body.method or "login").strip()
    ids = [str(i).strip() for i in (body.employee_ids or []) if str(i).strip()]
    emps = (so.table("employees").select("employee_id,name,email")
            .eq("org_id", org_id).eq("is_active", True).execute().data) or []
    if ids:
        emps = [e for e in emps if e.get("employee_id") in set(ids)]
    elif body.all_incomplete:
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
                                          role_name=(body.role_name or "").strip() or None,
                                          send_email_flag=body.send_email, actor=body.actor or "HR",
                                          authorization=authorization, x_active_org=x_active_org))
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
class OnboardingAdvanceIn(LaxModel):
    to_status: str = ""
    override_compliance: Any = None
    compliance_override_reason: str = ""
    actor: Any = None
    reason: Any = None


@router.post("/onboarding/employee/{employee_id}/advance")
def onboarding_advance(employee_id: str, body: OnboardingAdvanceIn, org_id: str = ORG_ID):
    """HR moves the workflow to a specific status. An out-of-order move is recorded as an OVERRIDE
    (with reason) but always allowed — the flow stays in the system, HR stays in control. EXCEPTION
    (item 4): moving to provisioned/active is hard-gated on work-authorization docs + a known work
    state; see _blocking_gate."""
    to = (body.to_status or "").strip()
    if to not in WORKFLOW_STATUSES:
        raise HTTPException(400, f"to_status must be one of {WORKFLOW_STATUSES}")
    gate_reasons = {}
    if to in ("provisioned", "active"):
        blocked, gate_reasons = _blocking_gate(org_id, employee_id)
        if blocked and not (body.override_compliance and (body.compliance_override_reason or "").strip()):
            raise HTTPException(400, {"code": "compliance_blocked",
                                      "message": _compliance_block_message(gate_reasons), "reasons": gate_reasons})
    so = _so()
    prof = _get_profile(so, org_id, employee_id) or {}
    cur = prof.get("workflow_status") or "invited"
    is_override = STATUS_ORDER.get(to, 0) != STATUS_ORDER.get(cur, 0) + 1
    st = _set_status(so, org_id, employee_id, to, actor=(body.actor or "HR"),
                     reason=(body.reason or None), is_override=is_override)
    if gate_reasons:   # the gate was blocked above and explicitly overridden to get here
        _log_event(org_id, employee_id, "compliance_override", actor=(body.actor or "HR"),
                   reason=(body.compliance_override_reason or None), is_override=True, detail=gate_reasons)
    return {"ok": True, "workflow_status": st, "was_override": is_override}


class OnboardingProvisionIn(LaxModel):
    override: Any = None
    override_compliance: Any = None
    compliance_override_reason: str = ""
    role_name: str = ""
    market: Any = None
    store_code: Any = None
    store_codes: Any = None
    actor: Any = None
    reason: Any = None
    send_email: Any = True


@router.post("/onboarding/employee/{employee_id}/provision")
async def onboarding_provision(employee_id: str, body: OnboardingProvisionIn, org_id: str = ORG_ID,
                               authorization: str = Header(default=""),
                               x_active_org: str = Header(default="")):
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
    override = bool(body.override)
    if STATUS_ORDER.get(cur, 0) < STATUS_ORDER["docs_verified"] and not override:
        raise HTTPException(400, {"code": "docs_incomplete",
                                  "message": f"Documents aren't verified yet (status: {STATUS_LABELS.get(cur, cur)}). "
                                             "Verify the checklist first, or override with a reason."})
    # Item 4: work-auth blocking gate — NOT bypassed by the general docs `override` above. Needs its own
    # explicit, separately-audited override_compliance + reason.
    gate_blocked, gate_reasons = _blocking_gate(org_id, employee_id)
    if gate_blocked and not (body.override_compliance and (body.compliance_override_reason or "").strip()):
        raise HTTPException(400, {"code": "compliance_blocked",
                                  "message": _compliance_block_message(gate_reasons), "reasons": gate_reasons})
    from app.modules.core.router import assign_role, create_login as core_create_login
    role = (body.role_name or "sales_rep").strip()
    await _maybe_await(assign_role({"email": email, "full_name": emp[0].get("name"), "role": role,
                       "market": body.market, "store_code": body.store_code,
                       "store_codes": body.store_codes, "employee_id": employee_id}, org_id,
                       authorization=authorization, x_active_org=x_active_org))
    try:
        login = await _maybe_await(core_create_login({"email": email}, org_id,
                                                     authorization=authorization,
                                                     x_active_org=x_active_org))
    except Exception as e:
        raise HTTPException(400, f"could not create login: {str(e)[:200]}")
    _set_status(so, org_id, employee_id, "provisioned",
                actor=(body.actor or "HR"), reason=(body.reason or None), is_override=override)
    _log_event(org_id, employee_id, "provisioned", actor=(body.actor or "HR"),
               reason=(body.reason or None), is_override=override,
               detail={"role": role, "email": email})
    if gate_blocked:   # the compliance gate was blocked above and explicitly overridden to get here
        _log_event(org_id, employee_id, "compliance_override", actor=(body.actor or "HR"),
                   reason=(body.compliance_override_reason or None), is_override=True, detail=gate_reasons)
    emailed = False
    if body.send_email:
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


class OnboardingReconcileIn(LaxModel):
    dry_run: Any = True
    notify: Any = True
    actor: Any = None


@router.post("/onboarding/reconcile")
async def onboarding_reconcile(body: OnboardingReconcileIn, org_id: str = ORG_ID):
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
    dry_run = body.dry_run is not False
    notify = body.notify is not False
    actor = (body.actor or "HR").strip()
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
        # DEFECT FIX (2026-07-14): same owner_role == 'employee' scope as mandatory_progress above — a
        # reconcile that emails an employee "you're missing X" must never name an HR/DM/market_manager
        # checklist step they have no way to complete (that was the false-missing-for-everyone bug).
        missing = [{"task_id": t["id"], "key": t.get("key"), "label": t.get("label"), "category": c.get("label")}
                   for c in data.get("categories", []) for t in c.get("tasks", [])
                   if (t.get("is_mandatory", True) is not False) and t.get("owner_role") == "employee"
                   and t.get("status") not in ("verified", "na")]
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
    from app.core.tenant_middleware import caller_app_user_http
    row = caller_app_user_http(uid, "org_id,employee_id,email,full_name,role")
    if not row or not row.get("employee_id"):
        raise HTTPException(403, "no employee record is linked to this login")
    return row


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
                  # migration 402: the file list this task holds, each entry trimmed to what the portal
                  # needs to render + act on (id/name/uploaded_at + the server-computed delete permission —
                  # never re-derive that rule client-side).
                  "documents": [{"id": f.get("id"), "name": f.get("name"), "uploaded_at": f.get("uploaded_at"),
                                "employee_can_delete": f.get("employee_can_delete")}
                               for f in (t.get("documents") or [])],
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


class OnboardingMeStateIn(LaxModel):
    work_state: Any = None
    state: Any = None


@router.post("/onboarding/me/state")
def onboarding_me_state(body: OnboardingMeStateIn, authorization: str = Header(default="")):
    me = _me_from_token(authorization)
    st = _set_work_state(me["org_id"], me["employee_id"], body.work_state or body.state, actor="employee")
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
    res = await _do_onboard_upload(me["org_id"], me["employee_id"], task_id, file, "employee", uploaded_role="employee")
    _recompute_status(_so(), me["org_id"], me["employee_id"], actor="employee")
    return res


@router.get("/onboarding/me/task/{task_id}/document/{file_id}")
def onboarding_me_document_url(task_id: str, file_id: str, authorization: str = Header(default="")):
    """Logged-in portal: a signed URL for ONE previously-uploaded file (migration 402)."""
    me = _me_from_token(authorization)
    return onboarding_document_url(me["employee_id"], task_id, file_id, org_id=me["org_id"])


@router.delete("/onboarding/me/task/{task_id}/document/{file_id}")
def onboarding_me_delete_document(task_id: str, file_id: str, authorization: str = Header(default="")):
    """Logged-in portal: the employee deletes a file THEY uploaded, only while the task is still
    'pending' (migration 402) — server-enforced, see _employee_can_delete_document."""
    me = _me_from_token(authorization)
    res = _do_onboard_delete_document(me["org_id"], me["employee_id"], task_id, file_id, "employee", "employee")
    _recompute_status(_so(), me["org_id"], me["employee_id"], actor="employee")
    return res


# ── Public (token) intake + state, mirroring the self endpoints ──────────────────────────────────
class PublicOnboardingStateIn(LaxModel):
    value: Any = None
    work_state: Any = None
    state: Any = None


@router.post("/public/onboarding/{token}/state")
def public_onboarding_state(token: str, body: PublicOnboardingStateIn):
    prof = _profile_by_token(token)
    if not _token_valid(prof):
        raise HTTPException(404, "This onboarding link is invalid or has expired.")
    if not _check_gate(prof, body.value):
        raise HTTPException(403, "Identity check failed.")
    st = _set_work_state(prof["org_id"], prof["employee_id"], body.work_state or body.state, actor="employee")
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


def _do_onboard_sign(org_id, employee_id, task_id, form_data, signature, signed_name, who="employee"):
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


class OnboardingSignIn(LaxModel):
    value: Any = None
    task_id: Any = None
    form_data: Any = None
    signature: Any = None
    signed_name: Any = None


@router.post("/public/onboarding/{token}/sign")
def public_onboarding_sign(token: str, body: OnboardingSignIn):
    """Credential-less portal: fill & sign an item online (gate re-checked on every call)."""
    prof = _profile_by_token(token)
    if not _token_valid(prof):
        raise HTTPException(404, "This onboarding link is invalid or has expired.")
    if not _check_gate(prof, body.value):
        raise HTTPException(403, "Identity check failed.")
    task = _task_row(prof["org_id"], str(body.task_id or ""))
    if not task or task.get("owner_role") != "employee":
        raise HTTPException(403, "That item can't be signed from this portal.")
    return _do_onboard_sign(prof["org_id"], prof["employee_id"], task["id"],
                             body.form_data, body.signature or "",
                             body.signed_name or "")


@router.post("/onboarding/me/sign")
def onboarding_me_sign(body: OnboardingSignIn, authorization: str = Header(default="")):
    """Logged-in portal: fill & sign an item online."""
    me = _me_from_token(authorization)
    task = _task_row(me["org_id"], str(body.task_id or ""))
    if not task or task.get("owner_role") != "employee":
        raise HTTPException(403, "That item can't be signed here.")
    return _do_onboard_sign(me["org_id"], me["employee_id"], task["id"],
                             body.form_data, body.signature or "",
                             body.signed_name or "")


class OnboardingReturnTaskIn(LaxModel):
    missing_fields: Any = None
    reason: Any = None
    actor: Any = None


@router.post("/onboarding/employee/{employee_id}/task/{task_id}/return")
async def onboarding_return_task(employee_id: str, task_id: str, body: OnboardingReturnTaskIn, org_id: str = ORG_ID):
    """HR sends a submitted document BACK for corrections (a scan the auto-check can't read, a
    missed signature, the wrong form…), listing what's missing. The employee sees the item flagged
    in their portal and gets an email with the exact list."""
    task = _task_row(org_id, task_id)
    if not task:
        raise HTTPException(404, "unknown onboarding item")
    missing = [str(m).strip() for m in (body.missing_fields or []) if str(m).strip()]
    reason = (body.reason or "").strip() or None
    if not missing and not reason:
        raise HTTPException(400, "List the missing fields (or give a reason) so the employee knows what to fix.")
    row = {"org_id": org_id, "employee_id": employee_id, "task_id": task_id, "status": "returned",
           "missing_fields": missing or None, "returned_reason": reason,
           "returned_at": _now_iso(), "returned_by": (body.actor or "HR"), "updated_at": _now_iso()}
    try:
        _so().table("employee_onboarding").upsert(row, on_conflict="org_id,employee_id,task_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not return — is migration 082 applied? {e}")
    _log_event(org_id, employee_id, "doc_returned", actor=body.actor or "HR",
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


class OnboardingSendDocumentsIn(LaxModel):
    employee_ids: Any = None
    send_email: Any = True
    actor: Any = None


@router.post("/onboarding/send-documents")
async def onboarding_send_documents(body: OnboardingSendDocumentsIn, org_id: str = ORG_ID,
                                    authorization: str = Header(default=""),
                                    x_active_org: str = Header(default="")):
    """The Documents-board checkbox action: (re)send the onboarding packet to the selected people.
    Per person: an existing identity gate (DOB / last-4) re-issues their token LINK; otherwise a
    portal LOGIN invite goes to the roster email. Stamps docs_sent_at so the board shows who's been
    sent. Body: employee_ids[], send_email? (default true), actor?"""
    ids = [str(i).strip() for i in (body.employee_ids or []) if str(i).strip()]
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
        method, dob = "login", None
        if prof.get("verify_dob"):
            method, dob = "link", str(prof.get("verify_dob"))[:10]
        elif not (emp.get("email") or "").strip():
            results.append({"employee_id": eid, "name": emp.get("name"), "ok": False,
                            "error": "no email on file and no identity gate — add an email, or invite them one-on-one with a DOB"})
            continue
        res = await _send_invite(org_id, emp, method, dob=dob,
                                 send_email_flag=body.send_email,
                                 actor=body.actor or "HR",
                                 authorization=authorization, x_active_org=x_active_org)
        if res.get("ok"):
            try:
                so.table("employee_onboarding_profile").upsert(
                    {"org_id": org_id, "employee_id": eid, "docs_sent_at": _now_iso()},
                    on_conflict="org_id,employee_id").execute()
            except Exception:
                pass  # pre-082 — invited_at still marks them as sent
            _log_event(org_id, eid, "docs_sent", actor=body.actor or "HR",
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
    Used to forward the completed packet to accounting without giving them app access. Migration 402:
    a task with multiple files (SS-card front + back, …) contributes one link PER FILE, labelled
    "Task (1/2)" / "Task (2/2)" so accounting can tell them apart."""
    tmpl = onboarding_template(org_id=org_id)
    labels = {t["id"]: t["label"] for c in tmpl.get("categories", []) for t in c.get("tasks", [])}
    try:
        rows = (_so().table("employee_onboarding")
                .select("task_id,status,document_path,document_name,documents,signed_at")
                .eq("org_id", org_id).eq("employee_id", employee_id).execute().data) or []
    except Exception:
        rows = []
    docs = []
    for r in rows:
        file_list = list(r.get("documents") or [])
        if not file_list and r.get("document_path"):
            file_list = [{"path": r.get("document_path"), "name": r.get("document_name")}]
        n = len(file_list)
        label = labels.get(r.get("task_id"), "Document")
        for i, f in enumerate(file_list):
            p = f.get("path")
            if not p:
                continue
            docs.append({"label": f"{label} ({i + 1}/{n})" if n > 1 else label,
                         "name": f.get("name"), "signed_at": r.get("signed_at"),
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


class OnboardingSetAccountingSettingsIn(LaxModel):
    emails: Any = None
    whatsapps: Any = None
    subject: str = ""
    message: str = ""
    include_portal_link: Any = True


@router.put("/onboarding/accounting-settings")
def onboarding_set_accounting_settings(body: OnboardingSetAccountingSettingsIn, org_id: str = ORG_ID):
    """Save the accounting-forward destination. emails/whatsapps accept a list or a comma-separated string."""
    row = {"org_id": org_id,
           "accounting_emails": _recipient_list(body.emails),
           "accounting_whatsapps": _recipient_list(body.whatsapps),
           "forward_subject": (body.subject or "").strip() or None,
           "forward_message": (body.message or "").strip() or None,
           "include_portal_link": body.include_portal_link is not False,
           "updated_at": _now_iso()}
    try:
        _so().table("hr_onboarding_settings").upsert(row, on_conflict="org_id").execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save — is migration 100 applied? ({e})")
    return {"ok": True}


def _is_complete_row(st):
    """A doc-status row is 'complete' when there ARE applicable items and none are pending/returned."""
    return bool(st) and st.get("total", 0) > 0 and st.get("pending", 0) == 0 and st.get("returned", 0) == 0


class OnboardingApproveIn(LaxModel):
    force: Any = None
    actor: Any = None
    reason: Any = None


@router.post("/onboarding/employee/{employee_id}/approve")
def onboarding_approve(employee_id: str, body: OnboardingApproveIn, org_id: str = ORG_ID):
    """Mark a completed hire's paperwork REVIEWED & APPROVED → advance the workflow to docs_verified.
    Guards that the employee-owned checklist is actually all back (override with body.force=true)."""
    so = _so()
    st = {r["employee_id"]: r for r in onboarding_doc_status(org_id=org_id).get("employees", [])}.get(employee_id)
    if not body.force and not _is_complete_row(st):
        raise HTTPException(400, "Not all documents are back yet — review the outstanding items, or pass force=true.")
    prof = _get_profile(so, org_id, employee_id) or {}
    cur = prof.get("workflow_status") or "invited"
    is_override = STATUS_ORDER.get("docs_verified", 0) != STATUS_ORDER.get(cur, 0) + 1
    new = _set_status(so, org_id, employee_id, "docs_verified", actor=(body.actor or "HR"),
                      reason=(body.reason or "Onboarding paperwork reviewed & approved"), is_override=is_override)
    return {"ok": True, "workflow_status": new}


class OnboardingForwardAccountingIn(LaxModel):
    emails: Any = None
    subject: Any = None
    message: Any = None
    actor: Any = None
    force: Any = None


@router.post("/onboarding/employee/{employee_id}/forward-accounting")
async def onboarding_forward_accounting(employee_id: str, body: OnboardingForwardAccountingIn, org_id: str = ORG_ID):
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
    emails = _recipient_list(body.emails) or (s.get("accounting_emails") or [])
    if not emails:
        raise HTTPException(400, "No accounting recipient configured — set one in the Completed tab settings (or pass emails).")
    if not body.force:
        st = {r["employee_id"]: r for r in onboarding_doc_status(org_id=org_id).get("employees", [])}.get(employee_id)
        if not _is_complete_row(st):
            raise HTTPException(400, "This hire's paperwork isn't complete yet — forward once everything is back (or pass force=true).")
    docs = _completed_docs_for(org_id, employee_id)
    name = emp.get("name") or employee_id
    subject = (body.subject or s.get("forward_subject") or "Completed onboarding paperwork — {name}").replace("{name}", name)
    intro = (body.message or s.get("forward_message")
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
        _log_event(org_id, employee_id, "forwarded_accounting", actor=(body.actor or "HR"),
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
def _date_range_ok(ts, dfrom, dto):
    """Inclusive-both-ends membership of a timestamp's plain YYYY-MM-DD date prefix in [dfrom, dto]
    (either/both may be blank = no bound on that side). UTC calendar dates — see the "honest framing"
    note on onboarding_compliance_documents's own docstring (Gate-1 N1, 2026-07-27) for what this
    does and does not sidestep. Returns True/False, or None when `ts` itself is missing/falsy — the
    caller distinguishes "confirmed out of range" from "no date on file to judge" (never conflated,
    never fabricated)."""
    if not (dfrom or dto):
        return True
    if not ts:
        return None
    d = str(ts)[:10]
    if dfrom and d < dfrom:
        return False
    if dto and d > dto:
        return False
    return True


def _compliance_not_submitted_rows(all_tasks, emps, sent_of, has_artifact, resolved_by_status,
                                    cat_of, id_filter, q, sent_from, sent_to):
    """Pure (no DB): which ACTIVE employees have an OUTSTANDING active document-producing task
    (requires_upload or is_fillable — Gate-1 N2: is_active too, a retired task is never outstanding),
    scoped by the SAME employee_ids/q/sent-range filters `onboarding_compliance_documents` applies to
    the submitted-documents list, so the count is honest under the currently active filter set.

    A task is resolved (excluded here) when EITHER `(employee_id, task_id)` is in `has_artifact` (a
    file/signature is on file — it's already in the `documents` list, not owed again here) OR in
    `resolved_by_status` (status is 'verified'/'na' even with nothing on file — Gate-1 N3: mirrors
    onboarding_for_employee's own `ok_done = status in ("verified", "na")` and onboarding_doc_status's
    bucketing, so an in-person-verified or HR-waived item is correctly done, not outstanding forever).
    Work-state gating (`applies_state`) matches onboarding_for_employee's own rule.

    Returns (rows, sent_unknown_increment) — the second value is how many ACTIVE, filter-matching
    employees had no request-sent date on file at all (adds to the caller's sent_unknown_count, which
    already separately tallies unknown-dated rows in the submitted-documents list — one combined,
    honestly-labeled tally across both listings, not two silently different numbers)."""
    doc_tasks = [t for t in all_tasks if t.get("is_active", True) and (t.get("requires_upload") or t.get("is_fillable"))]
    not_submitted = []
    sent_unknown = 0
    for eid, emp in emps.items():
        if not emp.get("is_active", True):
            continue   # departed hires aren't an actionable "outstanding request" — matches the
                       # Documents board's own active-roster scope (onboarding_doc_status)
        if id_filter and eid not in id_filter:
            continue
        name = emp.get("name") or eid
        if q and q.lower() not in f"{name} {eid} {emp.get('email') or ''}".lower():
            continue
        prof = sent_of.get(eid) or {}
        ws = prof.get("work_state")
        req_sent = prof.get("request_sent_at")
        ok_sent = _date_range_ok(req_sent, sent_from, sent_to)
        if ok_sent is None:
            sent_unknown += 1
            continue
        if ok_sent is False:
            continue
        for t in doc_tasks:
            ast = t.get("applies_state")
            if ast and ast != ws:
                continue   # not applicable to this employee's work state (same rule as onboarding_for_employee)
            key = (eid, t["id"])
            if key in has_artifact or key in resolved_by_status:
                continue   # already submitted, or resolved by status (verified in person / waived)
            not_submitted.append({"employee_id": eid, "employee_name": name, "employee_email": emp.get("email"),
                                  "task_id": t["id"], "document_label": t.get("label") or "Document",
                                  "category": cat_of.get(t["id"]), "request_sent_at": req_sent})
    not_submitted.sort(key=lambda d: (d["employee_name"] or "", d["document_label"] or ""))
    return not_submitted, sent_unknown


@router.get("/onboarding/compliance-documents")
def onboarding_compliance_documents(org_id: str = ORG_ID, q: str = "", employee_id: str = "",
                                    employee_ids: str = "",
                                    sent_from: str = "", sent_to: str = "",
                                    submitted_from: str = "", submitted_to: str = ""):
    """One row per uploaded/signed onboarding FILE, across the whole roster, sorted by employee name
    then document label then file order. Migration 402: a task with multiple files (SS-card front +
    back, …) now contributes one row per file (file_index/file_count so the UI can label them "1 of 2"),
    instead of collapsing to whichever file happened to be most recent. Filter with q (name/id/email
    substring), employee_id (single, exact, kept for the per-employee ZIP export link) and/or
    employee_ids (comma-separated business ids — the picker's multi-select, RULE THREE §3b). Does NOT
    eagerly sign a URL per row (could be hundreds) — click-through uses
    /onboarding/employee/{id}/task/{task_id}/document/{file_id} and .../signature, already org-scoped.

    OWNER DIRECTIVE 2026-07-27 — two independent, composable (AND), inclusive-both-ends date filters:
      sent_from/sent_to        — when the document REQUEST was sent to the employee.
      submitted_from/submitted_to — when the document/signature was actually submitted.
    Both compare on the plain YYYY-MM-DD date prefix of the underlying timestamp — i.e. UTC calendar
    dates, the SAME convention every other date on this page already uses (`.slice(0, 10)` on the raw
    ISO string, no timezone math). Honest framing (Gate-1 N1): this sidesteps the JS
    `new Date("YYYY-MM-DD")` off-by-one class (the frontend passes the raw <input type=date> value
    straight through, never round-tripped through `Date`) but NOT a separate storage-timezone class —
    a submission at 9pm America/New_York is stored, filtered, AND DISPLAYED as the next UTC day.
    Left as-is deliberately: display/filter/export all agree today, so a filter-only timezone fix
    would CREATE a mismatch against what the row still visibly shows. Filed as a class-wide follow-up
    in docs/handoffs/people.md (2026-07-27 fold) — derive business-local (America/New_York) dates for
    display+filter+export TOGETHER across this page and its sibling
    hr/onboarding/[employeeId]/page.tsx, not a one-off fix here.

    Timestamp provenance (audited against mig 077/082 — see docs/handoffs/people.md 2026-07-27 entry):
      - SUBMITTED is stamped per FILE (`documents[].uploaded_at`, mig 402) or per TASK
        (`employee_onboarding.submitted_at`/`signed_at`, mig 073/082) — already existed, already
        populated by every upload/sign path. No new column needed.
      - REQUEST SENT has no per-DOCUMENT timestamp anywhere in the schema — HR requests the whole
        onboarding packet at once (`employee_onboarding_profile.docs_sent_at`, mig 082, stamped by the
        Documents board's "Send documents" action; falls back to `invited_at`, mig 077, the original
        invite). That is the real product model (there is no per-document "request" event to attach a
        new column to), so every document row for an employee carries that SAME employee-level
        request_sent_at — honest, not fabricated. A tenant/employee that predates both invite paths (no
        profile row, or a profile with neither column stamped) has NO sent date on file; such rows are
        never silently dropped from an active sent-range filter without being counted (see
        `sent_unknown_count` below) — degrade-honest per contract §5, no migration 420 required for a
        read-side filter over columns that already exist and are already populated going forward.

    "Not yet submitted" honesty (owner-mandated): a submitted-range filter naturally has nothing to
    show for a task nobody has touched yet — those rows never existed in `documents` to begin with, so
    they would otherwise just silently vanish. `not_submitted` / `not_submitted_count` /
    `not_submitted_employee_count` report exactly which ACTIVE employees still have an OUTSTANDING
    document-producing task (requires_upload or is_fillable — deliberately INCLUDES hr-owned tasks like
    Handbook, since the repository already shows them once HR uploads them; "outstanding", not "the
    employee owes it" — an hr-owned task is outstanding work on the HIRE's record, not a personal debt),
    scoped by the SAME q/employee_ids/
    sent-range filters, so the count is honest under the currently active filter set, not just the
    unfiltered whole roster."""
    so = _so()
    tmpl = onboarding_template(org_id=org_id, include_inactive=True)
    all_tasks = [t for c in tmpl.get("categories", []) for t in c.get("tasks", [])]
    task_of = {t["id"]: t for t in all_tasks}
    label_of = {tid: t["label"] for tid, t in task_of.items()}
    cat_of = {}
    for c in tmpl.get("categories", []):
        for t in c.get("tasks", []):
            cat_of[t["id"]] = c["label"]
    emps = {e["employee_id"]: e for e in ((so.table("employees").select("employee_id,name,email,is_active")
            .eq("org_id", org_id).execute().data) or [])}
    # Employee-level "request sent" date (see docstring) — best-effort, a pre-077 tenant / table just
    # means every row degrades to "(no date recorded)", never a 500 and never a fabricated date.
    sent_of = {}
    try:
        for p in ((so.table("employee_onboarding_profile")
                   .select("employee_id,work_state,docs_sent_at,invited_at")
                   .eq("org_id", org_id).execute().data) or []):
            sent_of[p.get("employee_id")] = {
                "work_state": p.get("work_state"),
                "request_sent_at": p.get("docs_sent_at") or p.get("invited_at")}
    except Exception:
        pass
    # DEFECT FIX (2026-07-14, symptom 2): a swallowed exception here used to look identical to "this
    # tenant genuinely has zero documents on file" — the page would render an empty "No documents on
    # file yet" state with no indication the read itself failed (e.g. migration 082's signature_path/
    # signed_at/signed_name columns not applied yet). Track success explicitly so the page can tell the
    # two apart, per the same "never a silent 500, but never a silent lie either" degrade pattern the
    # rest of this file uses (see onboarding_update_status's "is migration 073 applied?" 400s).
    rows, fetch_ok = [], True
    try:
        rows = (so.table("employee_onboarding")
                .select("employee_id,task_id,status,document_path,document_name,documents,signature_path,"
                        "signed_at,signed_name,verified_by,verified_at,submitted_at")
                .eq("org_id", org_id).execute().data) or []
    except Exception as e:
        fetch_ok = False
        _fetch_err = str(e)[:200]

    id_filter = {s.strip() for s in employee_ids.split(",") if s.strip()}
    if employee_id:
        id_filter.add(employee_id)
    # (employee_id, task_id) -> resolved (NOT outstanding), tracked two ways per Gate-1 N3:
    #   has_artifact       — an actual file/signature is on file (drives the `out` rows below too).
    #   resolved_by_status — status is 'verified' or 'na' EVEN WITH NO FILE (HR verified an original
    #                        document in person, or explicitly waived it) — mirrors
    #                        onboarding_for_employee's own `ok_done = status in ("verified", "na")` and
    #                        onboarding_doc_status's bucketing (status='na' buckets as 'verified'), so
    #                        "not yet submitted" doesn't chase a task that IS done by this codebase's
    #                        own definition of done, forever, just because nothing was ever uploaded.
    # Computed BEFORE the artifact-less `continue` below so a status-only resolution isn't lost.
    has_artifact = set()
    resolved_by_status = set()
    out = []
    for r in rows:
        eid = r.get("employee_id")
        tid = r.get("task_id")
        if r.get("status") in ("verified", "na"):
            resolved_by_status.add((eid, tid))
        file_list = list(r.get("documents") or [])
        if not file_list and r.get("document_path"):
            # pre-402 row (migration hasn't backfilled it, or the tenant hasn't run 402 at all) — never a
            # regression, this repository still shows it exactly as a single-file row.
            file_list = [{"id": None, "path": r.get("document_path"), "name": r.get("document_name")}]
        has_file = bool(file_list) or bool(r.get("signature_path"))
        if has_file:
            has_artifact.add((eid, tid))
        if not has_file:
            continue   # nothing actually on file for this task yet -> no row to emit into `out`
        if id_filter and eid not in id_filter:
            continue
        emp = emps.get(eid) or {}
        name = emp.get("name") or eid
        if q and q.lower() not in f"{name} {eid} {emp.get('email') or ''}".lower():
            continue
        req_sent = (sent_of.get(eid) or {}).get("request_sent_at")
        n = len(file_list)
        base = {"employee_id": eid, "employee_name": name, "employee_email": emp.get("email"),
                "task_id": tid, "document_label": label_of.get(tid) or "Document",
                "category": cat_of.get(tid), "status": r.get("status"),
                "verified_by": r.get("verified_by"), "request_sent_at": req_sent}
        for i, f in enumerate(file_list):
            out.append({**base, "file_id": f.get("id"), "file_index": (i + 1) if n > 1 else None, "file_count": n,
                        "document_name": f.get("name") or r.get("document_name"),
                        "has_document": True, "has_signature_page": False,
                        "signed_at": f.get("uploaded_at") or r.get("verified_at") or r.get("submitted_at"),
                        "signed_name": r.get("signed_name")})
        if r.get("signature_path"):
            out.append({**base, "file_id": None, "file_index": None, "file_count": n,
                        "document_name": "Signed online", "has_document": False, "has_signature_page": True,
                        "signed_at": r.get("signed_at") or r.get("verified_at") or r.get("submitted_at"),
                        "signed_name": r.get("signed_name")})

    # ── OWNER DIRECTIVE 2026-07-27 — the two date-range filters (composable AND, inclusive both ends,
    # plain YYYY-MM-DD prefix comparison — see docstring). Applied AFTER building `out` so the counts
    # below (unknown/excluded) are computed against the same rows the user is looking at. ─────────────
    submitted_unknown_count = 0
    sent_unknown_count = 0
    filtered = []
    for d in out:
        ok_sub = _date_range_ok(d.get("signed_at"), submitted_from, submitted_to)
        if ok_sub is None:
            submitted_unknown_count += 1
            continue
        if ok_sub is False:
            continue
        ok_sent = _date_range_ok(d.get("request_sent_at"), sent_from, sent_to)
        if ok_sent is None:
            sent_unknown_count += 1
            continue
        if ok_sent is False:
            continue
        filtered.append(d)
    out = filtered
    out.sort(key=lambda d: (d["employee_name"] or "", d["document_label"] or "", d.get("file_index") or 0))

    # ── "Not yet submitted" honesty (owner-mandated) — active-roster employees with an outstanding
    # ACTIVE document-producing task (requires_upload or is_fillable — the only tasks that could ever
    # have produced a row above; N2 fold: is_active too, a retired task is never "outstanding"),
    # scoped by the SAME employee/q/sent-range filters so the count matches what the user is currently
    # looking at. A submitted-range filter is NEVER applied here (these rows have no submission by
    # definition — that is exactly what is being surfaced, not a range they missed). Completion (N3
    # fold) is has-an-artifact OR status in (verified, na) — mirrors onboarding_for_employee's own
    # `ok_done` and onboarding_doc_status's bucketing, so a task HR verified in person (no upload) or
    # explicitly marked N/A is correctly NOT outstanding forever. ──────────────────────────────────
    not_submitted, ns_sent_unknown = _compliance_not_submitted_rows(
        all_tasks, emps, sent_of, has_artifact, resolved_by_status, cat_of,
        id_filter, q, sent_from, sent_to)
    sent_unknown_count += ns_sent_unknown

    # Gate-1 N6: the detailed per-row list was computed for counting only and never rendered anywhere
    # (the page shows counts/banners, not a table) — return the counts, not 500 rows of dead weight.
    # `_compliance_not_submitted_rows` stays directly callable (module-level, pure) for anything that
    # DOES need the detail later (a future drill-down UI, or a harness proof) without re-adding it to
    # this payload.
    resp = {"ready": tmpl.get("ready", True), "documents": out, "count": len(out),
            "not_submitted_count": len(not_submitted),
            "not_submitted_employee_count": len({d["employee_id"] for d in not_submitted}),
            "submitted_unknown_count": submitted_unknown_count, "sent_unknown_count": sent_unknown_count}
    if not fetch_ok:
        # Surface the failure instead of a silent empty-looking page — "0 documents" and "the query
        # failed" must never render identically.
        resp["ready"] = False
        resp["error"] = f"Could not load onboarding documents — is migration 082 applied? {_fetch_err}"
    return resp


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
            "employee_id,task_id,document_path,document_name,documents,signature_path").eq("org_id", org_id)
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
            # migration 402: a task with N files writes DocName-1.ext … DocName-N.ext (numbered only when
            # there's more than one, so a single-file task's export name is unchanged from before this
            # package — no regression for the common case).
            file_list = list(r.get("documents") or [])
            if not file_list and r.get("document_path"):
                file_list = [{"path": r.get("document_path"), "name": r.get("document_name")}]
            n = len(file_list)
            for i, f in enumerate(file_list):
                p = f.get("path")
                if not p:
                    continue
                try:
                    file_bytes = bucket.download(p)
                    dn = f.get("name") or ""
                    ext = dn.rsplit(".", 1)[-1] if "." in dn else "pdf"
                    suffix = f"-{i + 1}" if n > 1 else ""
                    zf.writestr(f"{safe_emp}/{safe_doc}{suffix}.{ext}", file_bytes)
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


# ── HR Letters / Template-Library (migration 408) — mounted last so every name above (ORG_ID,
# _pvariants, _so, _cc, etc.) is already defined on this module before letters.py's lazy/no imports
# of it would matter; letters.py itself never imports FROM this file (avoids any load-order coupling
# at all — see the comment on its own _require_hr_or_admin duplicate). ────────────────────────────
from app.modules.hr import letters as _letters  # noqa: E402
router.include_router(_letters.router)


@router.get("/onboarding/attention-config")
def onboarding_attention_config(org_id: str = ORG_ID):
    """The tenant's 'stuck onboarding invite' alert threshold (days). Always returns a value (default
    7) even pre-migration-410 — see hr/attention.py's onboarding_stuck_days()."""
    from app.modules.hr import attention as _hr_attention
    return {"stuck_invite_days": _hr_attention.onboarding_stuck_days(get_supabase(), org_id)}


class PutOnboardingAttentionConfigIn(LaxModel):
    stuck_invite_days: Any = None


@router.put("/onboarding/attention-config")
def put_onboarding_attention_config(body: PutOnboardingAttentionConfigIn, org_id: str = ORG_ID,
                                    authorization: str = Header(default="")):
    """Set the tenant's 'stuck onboarding invite' alert threshold (days, clamped 1-90). HR/admin-only
    — same gate as every other HR-config write in this file."""
    org_id, _email, _role = _require_hr_or_admin(authorization)   # the caller's OWN tenant is authoritative
    try:
        days = int(body.stuck_invite_days)
    except (TypeError, ValueError):
        raise HTTPException(400, "stuck_invite_days must be a whole number of days")
    days = max(1, min(90, days))
    try:
        _so().table("tenants").update({"onboarding_stuck_days": days}).eq("org_id", org_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not save (run migration 410 first?): {str(e)[:160]}")
    return {"ok": True, "stuck_invite_days": days}


# ── Admin-attention providers (settings-audit package, 2026-07-26) ────────────────────────────────
# Contribute HR findings to the cross-module attention feed WITHOUT editing the shared
# core/import_health.py (AGENT_CONTRACT §1). Guarded: a missing/renamed core module must never break
# HR itself.
try:
    from app.modules.core.import_health import register_provider as _register_attention_provider
    from app.modules.hr import attention as _hr_attention_reg
    _hr_attention_reg.register(_register_attention_provider)
except Exception as _attn_e:
    print(f"WARN hr attention providers not registered: {_attn_e}")
