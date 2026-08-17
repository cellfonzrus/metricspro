"""HR Letters / Template-Library — /api/v1/hr/letters/*  (owner directive 2026-07-26)

A configurable letter system built on TOP of existing detection data — it invents no new recon math:
  - late clock-in strikes: read storeops.shifts (scheduled start) + storeops.timelog (punches,
    MULTI-SESSION safe — always takes the EARLIEST punch of the day, never assumes one row/day).
  - cash shortage: commcalc.closing_attempt (the daily-closing recon's per-rep cash/credit variance
    log — already written by closing/router.py's `_log_attempt`, read-only here).
  - inventory shortage: commcalc.flags rows from the asset module's RMA/appeal/inventory-recon sync
    (`asset_rma` / `asset_appeal` / `inventory_recon` sources), matched to the employee.
  - accessory shortfall: commcalc.chargeback_items (source='accessory_over') + a configured default
    from commcalc.ops_chargeback_policy(reason='accessory_shortfall') when no specific incident exists.
  - KPI / commission letters: commcalc.rep_commissions (kpi_values/kpis_met/total_kpis is the EXISTING
    per-rep KPI snapshot; total_payout is the commission figure) — no re-derivation of targets/tiers.

Delivery is per-template ('auto' | 'approval'); every automated fire is idempotent (dedupe_key +
partial unique index) and every send/queue/approve/reject is logged to storeops.sent_letter (visible
on the employee's HR record). Automation (late-checkin nightly, metrics-miss monthly) is fired by
pg_cron hitting */run-due, the SAME NOTIFY_RUN_SECRET-guarded pattern as notify/closing/asset sweeps —
no new scheduler. Multi-tenant: every read/write is org_id-scoped; every threshold (grace minutes,
rolling strike window, automation on/off) is a per-tenant config knob (storeops.tenants.hr_letters_config).

Degrades gracefully everywhere (contract §5): migration 408 not yet run -> templates/sent/queue read
as empty, /send returns a clear 400, the run-due sweeps no-op per tenant rather than 500ing.
"""
from __future__ import annotations

import calendar
import html as _html
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException

from app.core.config import settings
from app.core.run_secret import verify_notify_secret
from app.core.database import get_supabase

from .letters_logic import (
    clamp_int, compute_lateness, grace_minutes_from_config, render_template,
    strike_window_days_from_config, tier_for_strike_count, tokens_in,
)
from .letters_defaults import CATEGORY_LABELS, CATEGORY_MERGE_FIELDS, COMMON_FIELDS, DEFAULT_TEMPLATES

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

router = APIRouter(prefix="/letters", tags=["HR Letters"])
ORG_ID = "00000000-0000-0000-0000-000000000001"

DEFAULT_LETTERS_CONFIG = {"late_clockin": {"enabled": False, "grace_minutes": 5, "strike_window_days": 90},
                          "metrics_miss": {"enabled": False}}

_INVENTORY_FLAG_SOURCES = ("asset_rma", "asset_appeal", "inventory_recon")
_MONTHS_LOCAL = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}


def _so():
    return get_supabase().schema("storeops")


def _cc():
    return get_supabase().schema("commcalc")


# ── small pure helpers (money/name/date formatting — no I/O) ──────────────────────────────────────
def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _money(v) -> str:
    return f"${_f(v):,.2f}"


def _norm(s) -> str:
    return str(s or "").strip().upper()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_date(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _fmt_time(iso_str) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        try:
            return dt.strftime("%-I:%M %p")
        except ValueError:
            return dt.strftime("%I:%M %p").lstrip("0")
    except Exception:
        s = str(iso_str)
        return s[11:16] if len(s) >= 16 else s


def _parse_period_ym(period):
    """'2026-06' or 'June 2026' -> (year, month) or (None, None)."""
    parts = (period or "").strip().split()
    if len(parts) == 2 and parts[0].lower() in _MONTHS_LOCAL:
        try:
            return int(parts[1]), _MONTHS_LOCAL[parts[0].lower()]
        except Exception:
            return None, None
    if len(parts) == 1 and "-" in parts[0]:
        try:
            a, b = parts[0].split("-")[:2]
            return int(a), int(b)
        except Exception:
            return None, None
    return None, None


def _pvariants_local(period):
    """Period stored as either 'June 2026' or '2026-06' across tables — match both (self-contained
    copy of hr/router.py's `_pvariants`, kept local to avoid a circular import at module-load time —
    see the include_router() note at the bottom of hr/router.py)."""
    out = {(period or "").strip()}
    y, m = _parse_period_ym(period)
    if y and m:
        out.add(f"{y:04d}-{m:02d}")
        out.add(f"{calendar.month_name[m]} {y}")
    return [p for p in out if p]


def _shift_period(period, delta_months: int) -> str:
    y, m = _parse_period_ym(period)
    if y is None:
        return ""
    idx = (y * 12 + (m - 1)) + delta_months
    ny, nm = divmod(idx, 12)
    return f"{ny:04d}-{nm + 1:02d}"


def _default_prior_period(org_id, tenant=None) -> str:
    """The most recently COMPLETED calendar month, business-local for this tenant."""
    today = _biz_today(org_id, tenant)
    first_of_this_month = today.replace(day=1)
    last_month_end = first_of_this_month - timedelta(days=1)
    return last_month_end.strftime("%Y-%m")


# ── business timezone (per-tenant storeops.tenants.timezone, else the global default) ─────────────
def _biz_tz_for(org_id, tenant=None) -> str:
    t = tenant if tenant is not None else _tenant_row(org_id)
    tz = (str((t or {}).get("timezone") or "")).strip()
    return tz or settings.BUSINESS_TZ or "America/New_York"


def _biz_today(org_id, tenant=None) -> date:
    try:
        return datetime.now(timezone.utc).astimezone(ZoneInfo(_biz_tz_for(org_id, tenant))).date()
    except Exception:
        return datetime.now(timezone.utc).date()


# ── auth gates ──────────────────────────────────────────────────────────────────────────────────
def _rbac_enforced() -> bool:
    try:
        rows = _so().table("app_config").select("rbac_enabled").eq("id", 1).limit(1).execute().data or []
        return bool(rows and rows[0].get("rbac_enabled"))
    except Exception:
        return False


def _require_hr_or_admin(authorization: str):
    """Same gate as hr/router.py's `_require_hr_or_admin` (admin / super_admin / HR-titled role, or
    permissions.hr==true). Duplicated locally (small, self-contained) rather than imported, to avoid a
    circular import at module-load time (hr/router.py imports THIS module to mount its sub-router)."""
    from app.modules.core.router import _uid_from_token
    uid = _uid_from_token(authorization)
    if not uid:
        if _rbac_enforced():
            raise HTTPException(401, "Sign in as HR or an admin to manage HR letters.")
        return (ORG_ID, "(open-app)", "open")
    from app.core.tenant_middleware import caller_app_user_http
    u = caller_app_user_http(uid)
    if not u:
        raise HTTPException(403, "Your login isn't recognized for the company you are working in.")
    role = (u.get("role") or "").lower()
    ok = bool(u.get("super_admin")) or role == "admin" or "hr" in role
    if not ok:
        try:
            rr = (_so().table("roles").select("permissions")
                  .eq("org_id", u.get("org_id") or ORG_ID).eq("name", u.get("role")).limit(1).execute().data) or []
            if ((rr[0].get("permissions") if rr else {}) or {}).get("hr"):
                ok = True
        except Exception:
            pass
    if not ok:
        raise HTTPException(403, "Only HR managers and admins can manage HR letters.")
    return (u.get("org_id") or ORG_ID, u.get("email"), role)


def _require_letters_admin(authorization, x_active_org, org_id):
    """Per-setting edit-permission gate (SETTING_AREAS 'hr_letters' pattern) for TEMPLATE/CONFIG edits
    (not for sending/approving — those use the baseline `_require_hr_or_admin`). Falls back to that
    baseline when the caller can't be resolved via the settings-area path (RBAC off, or before the
    NEEDS-CORE SETTING_AREAS registration lands — see the people handoff), so this never blocks a
    legitimate admin on a resolution hiccup."""
    try:
        from app.modules.core.router import _can_edit_setting, _resolve_caller, _uid_from_token
        uid = _uid_from_token(authorization)
        if uid:
            caller = _resolve_caller(get_supabase(), uid, x_active_org)
            if caller and caller.get("org_id"):
                if not _can_edit_setting(caller, "hr_letters"):
                    raise HTTPException(403, "You don't have permission to edit HR letter templates.")
                return caller.get("org_id")
    except HTTPException:
        raise
    except Exception:
        pass
    oid, _email, _role = _require_hr_or_admin(authorization)
    return oid


# ── tenant / employee / template lookups ───────────────────────────────────────────────────────────
def _tenant_row(org_id) -> dict:
    try:
        rows = (_so().table("tenants").select("*").eq("org_id", org_id).limit(1).execute().data) or []
        return rows[0] if rows else {"org_id": org_id}
    except Exception:
        return {"org_id": org_id}


def _find_employee(org_id, employee_id):
    if not employee_id:
        return None
    try:
        rows = (_so().table("employees").select("*").eq("org_id", org_id)
                .eq("employee_id", employee_id).limit(1).execute().data) or []
        return rows[0] if rows else None
    except Exception:
        return None


def _emp_keys(emp) -> set:
    if not emp:
        return set()
    return {_norm(emp.get("name")), _norm(emp.get("epay_salesperson"))} - {""}


def _store_label(org_id, store_code) -> str:
    if not store_code:
        return ""
    try:
        rows = (_so().table("stores").select("address").eq("org_id", org_id)
                .eq("store_code", store_code).limit(1).execute().data) or []
        return (rows[0].get("address") or "").strip() if rows else ""
    except Exception:
        return ""


def _get_template(org_id, template_key):
    try:
        rows = (_so().table("letter_template").select("*").eq("org_id", org_id)
                .eq("template_key", template_key).limit(1).execute().data) or []
        return rows[0] if rows else None
    except Exception:
        return None


def _ensure_letter_templates(org_id):
    """Lazy per-org seed: insert any DEFAULT_TEMPLATES rows this org doesn't have yet. Never touches a
    row the org already has (including one they've edited) — additive-only, safe to call on every
    read. This is how the feature reaches every tenant (existing + brand new) without needing the
    SEED_VERSION/entitlements.py bump (a SHARED core/** file — see NEEDS CORE in the people handoff for
    the follow-up that would make this the primary path instead of just the always-safe fallback)."""
    try:
        existing = (_so().table("letter_template").select("template_key")
                    .eq("org_id", org_id).execute().data) or []
    except Exception:
        return
    have = {r.get("template_key") for r in existing}
    missing = []
    for (tkey, cat, tier, label, subject, body, mode) in DEFAULT_TEMPLATES:
        if tkey in have:
            continue
        missing.append({"org_id": org_id, "template_key": tkey, "category": cat, "escalation_tier": tier,
                        "label": label, "subject": subject, "body": body, "delivery_mode": mode,
                        "active": True, "is_default": True})
    if missing:
        try:
            _so().table("letter_template").insert(missing).execute()
        except Exception:
            pass


# ── merge-field builders (best-effort, NEVER raise — a lookup miss = a blank/zero + a note) ───────
def _common_merge(org_id, tenant, employee) -> dict:
    name = (employee or {}).get("name") or ""
    first = name.split()[0] if name else ""
    store_code = (employee or {}).get("home_store") or ""
    return {
        "employee_name": name, "employee_first_name": first,
        "company_name": (tenant or {}).get("name") or "Management",
        "store_name": _store_label(org_id, store_code) or store_code,
        "today_date": date.today().isoformat(),
        "sender_name": "HR",
    }


def _cash_shortage_lookup(org_id, employee, incident_date=None) -> dict:
    if not employee:
        return {"note": "Select an employee to look up recon data."}
    keys = _emp_keys(employee)
    try:
        q = _cc().table("closing_attempt").select(
            "close_date,store_code,employee_name,entered_cash,b2b_cash,cash_dir").eq("org_id", org_id)
        if incident_date:
            q = q.eq("close_date", incident_date)
        rows = q.order("close_date", desc=True).limit(300).execute().data or []
    except Exception:
        return {"note": "Closing recon data unavailable (is migration 103 applied?)."}
    cand = [r for r in rows if _norm(r.get("employee_name")) in keys and r.get("cash_dir") == "short"]
    if not cand:
        cand = [r for r in rows if _norm(r.get("employee_name")) in keys]
    if not cand:
        suffix = f" on {incident_date}" if incident_date else ""
        return {"note": f"No matching closing recon row found for this employee{suffix}."}
    r = cand[0]
    amt = abs(_f(r.get("entered_cash")) - _f(r.get("b2b_cash")))
    note = None if r.get("cash_dir") == "short" else "Most recent recon row wasn't flagged short — verify before sending."
    return {"incident_date": r.get("close_date"), "amount_label": _money(amt), "note": note}


def _inventory_shortage_lookup(org_id, employee, incident_date=None) -> dict:
    """GATE-1 MINOR-2 fix: `commcalc.flags` rows from the asset module's RMA/appeal/inventory-recon
    sync (asset/router.py ~845/1964/2103) are STORE-LEVEL — they never populate `epay_salesperson`
    (matching on it was silently dead code, always zero candidates). The only honest match available
    is the employee's home store vs. the flag's `store_address`; the result is explicitly labeled
    store-level (not attributable to one person) so HR knows to verify before sending."""
    if not employee:
        return {"note": "Select an employee to look up recon data."}
    home_store = (employee or {}).get("home_store") or ""
    store_label = _store_label(org_id, home_store) if home_store else ""
    if not store_label:
        return {"note": "This employee has no home store on file — can't match a store-level inventory flag."}
    try:
        rows = (_cc().table("flags").select("*").eq("org_id", org_id)
                .in_("source", list(_INVENTORY_FLAG_SOURCES))
                .order("created_at", desc=True).limit(300).execute().data) or []
    except Exception:
        return {"note": "Flags data unavailable."}
    target = _norm(store_label)
    cand = [r for r in rows if _norm(r.get("store_address")) == target]
    if incident_date:
        ym = str(incident_date)[:7]
        dated = [r for r in cand if str(r.get("created_at") or "")[:7] == ym]
        cand = dated or cand
    if not cand:
        return {"note": f"No matching inventory/RMA/appeal flag found at {store_label} (store-level only)."}
    r = cand[0]
    detail = r.get("description") or r.get("flag_type") or "Inventory discrepancy"
    return {"incident_date": str(r.get("created_at") or "")[:10],
           "detail": f"Store-level shortfall at {store_label}: {detail}",
           "amount_label": _money(r.get("amount")),
           "note": (f"This is a STORE-LEVEL flag at {store_label}, not attributed to a specific "
                    f"employee by the source data — verify this employee is responsible before sending.")}


def _accessory_shortfall_lookup(org_id, employee, incident_date=None, period=None) -> dict:
    """GATE-1 MINOR-1 fix: production writes the per-rep accessory-over-threshold record to
    `commcalc.chargeback_review` (source='accessory_over', status='assigned', `assigned_rep` = the
    rep's name — commcalc/router.py `accessory_flags_push`), NOT `chargeback_items.source`
    ('chargeback_review' is what lands there, referencing the review row by `source_ref` — matching
    chargeback_items.source=='accessory_over' was silently dead code, always zero candidates). Reads
    chargeback_review directly instead. The `ops_chargeback_policy` configured-default fallback was
    REMOVED (not fixed) — 'accessory_shortfall' isn't in retail-ops' REASONS registry and can't be
    created via the pick-don't-type Ops Chargeback Amounts UI (`ops_chargebacks.py` `put_policy` only
    accepts a reason the registry/history already knows), so that branch could never actually fire in
    production; inventing a new reason there is retail-ops' call, not this module's to make."""
    if not employee:
        return {"note": "Select an employee to look up recon data."}
    keys = _emp_keys(employee)
    try:
        rows = (_cc().table("chargeback_review").select("*").eq("org_id", org_id)
                .eq("source", "accessory_over").eq("status", "assigned").execute().data) or []
    except Exception:
        return {"note": "Accessory chargeback data unavailable (is migration 036 applied?)."}
    cand = [r for r in rows if _norm(r.get("assigned_rep")) in keys]
    if period:
        pv = set(_pvariants_local(period))
        narrowed = [r for r in cand if str(r.get("period") or "") in pv]
        cand = narrowed or cand
    if incident_date:
        dated = [r for r in cand if str(r.get("occurred_date") or "")[:10] == str(incident_date)[:10]]
        cand = dated or cand
    if not cand:
        suffix = f" for {period}" if period else ""
        return {"note": f"No accessory chargeback found for this employee{suffix}."}
    total = sum(_f(r.get("amount")) for r in cand)
    descs = sorted({(r.get("detail") or "")[:80] for r in cand if r.get("detail")})
    return {"incident_date": str(cand[0].get("occurred_date") or "")[:10] or None,
           "detail": ", ".join(descs[:3]) or "Accessory chargeback", "amount_label": _money(total)}


def _rep_commissions_row(org_id, employee, period):
    if not employee or not period:
        return None
    keys = _emp_keys(employee)
    try:
        rows = (_cc().table("rep_commissions")
                .select("period,storeops_name,epay_salesperson,total_payout,subtotal,kpis_met,total_kpis,kpi_values")
                .eq("org_id", org_id).in_("period", _pvariants_local(period)).execute().data) or []
    except Exception:
        return None
    for r in rows:
        for k in (r.get("storeops_name"), r.get("epay_salesperson")):
            if k and _norm(k) in keys:
                return r
    return None


_KPI_LABELS = {"atu": "ATU", "protect": "Protect", "boostapp": "Carrier App", "familyplan": "Family Plan",
              "byod": "BYOD", "tmr3": "3MR", "aal": "AAL"}


def _kpi_summary_from_row(row) -> str | None:
    """Plain-language summary from the EXISTING per-rep snapshot only (rep_commissions.kpi_values is a
    flat {key: percent} dict, kpis_met/total_kpis is the already-computed pass count) — no re-derivation
    of per-metric targets (those live in per-carrier payout config; not duplicated here)."""
    if not row:
        return None
    met, total = row.get("kpis_met"), row.get("total_kpis")
    kv = row.get("kpi_values") or {}
    parts = []
    if isinstance(kv, dict):
        for k, v in kv.items():
            try:
                parts.append(f"{_KPI_LABELS.get(k, str(k).upper())}: {float(v):.1f}%")
            except (TypeError, ValueError):
                continue
    header = f"Met {met} of {total} tracked KPIs." if (met is not None and total is not None) else ""
    detail = ("  " + " · ".join(parts)) if parts else ""
    combined = (header + "\n" + detail).strip()
    return combined or None


def _is_kpi_miss(row) -> bool:
    if not row:
        return False
    try:
        total = int(row.get("total_kpis") or 0)
        met = int(row.get("kpis_met") or 0)
    except (TypeError, ValueError):
        return False
    return total > 0 and met < total


def build_merge_defaults(org_id, employee, category, incident_date=None, period=None) -> dict:
    """Everything the Send-Letter page prefills for one (employee, category): system-default values +
    which date/period they were derived from + any 'no data found' notes. NEVER raises."""
    tenant = _tenant_row(org_id)
    merge = _common_merge(org_id, tenant, employee)
    derived_incident_date, derived_period, notes = incident_date, period, []

    if category == "late_clockin":
        info_row = None
        if employee and employee.get("employee_id"):
            try:
                q = (_so().table("late_clockin_strike").select("*").eq("org_id", org_id)
                     .eq("employee_id", employee["employee_id"]))
                q = q.eq("work_date", incident_date) if incident_date else q.order("work_date", desc=True)
                rows = q.limit(1).execute().data or []
                info_row = rows[0] if rows else None
            except Exception:
                info_row = None
        if info_row:
            derived_incident_date = derived_incident_date or info_row.get("work_date")
            merge.update({
                "incident_date": info_row.get("work_date"), "scheduled_start": info_row.get("scheduled_start"),
                "actual_clockin": _fmt_time(info_row.get("first_punch_at")),
                "minutes_late": info_row.get("minutes_late"), "grace_minutes": info_row.get("grace_minutes"),
                "strike_count": info_row.get("strike_number"),
            })
        else:
            notes.append("No recorded late clock-in strike found for this employee — fill these fields in manually.")
            merge.update({"incident_date": incident_date or "", "scheduled_start": "", "actual_clockin": "",
                         "minutes_late": "", "grace_minutes": "", "strike_count": ""})

    elif category == "cash_shortage":
        d = _cash_shortage_lookup(org_id, employee, incident_date)
        derived_incident_date = derived_incident_date or d.get("incident_date")
        merge.update({"incident_date": d.get("incident_date") or incident_date or "",
                     "shortage_amount": d.get("amount_label") or "$0.00"})
        if d.get("note"):
            notes.append(d["note"])

    elif category == "inventory_shortage":
        d = _inventory_shortage_lookup(org_id, employee, incident_date)
        derived_incident_date = derived_incident_date or d.get("incident_date")
        merge.update({"incident_date": d.get("incident_date") or incident_date or "",
                     "shortage_detail": d.get("detail") or "", "shortage_amount": d.get("amount_label") or "$0.00"})
        if d.get("note"):
            notes.append(d["note"])

    elif category == "accessory_shortfall":
        d = _accessory_shortfall_lookup(org_id, employee, incident_date, period)
        derived_incident_date = derived_incident_date or d.get("incident_date")
        merge.update({"incident_date": d.get("incident_date") or incident_date or "",
                     "shortfall_detail": d.get("detail") or "", "shortfall_amount": d.get("amount_label") or "$0.00"})
        if d.get("note"):
            notes.append(d["note"])

    elif category in ("kpi_miss", "metrics_miss_2consec"):
        derived_period = derived_period or _default_prior_period(org_id, tenant)
        prior_period = _shift_period(derived_period, -1)
        row = _rep_commissions_row(org_id, employee, derived_period)
        merge.update({"period": derived_period or "", "prior_period": prior_period or "",
                     "kpi_summary": _kpi_summary_from_row(row) or "No KPI snapshot found for this period.",
                     "commission_amount": _money(row.get("total_payout")) if row else "$0.00"})
        if not row:
            notes.append("No commission/KPI snapshot found for this employee/period.")

    elif category == "commission_statement":
        derived_period = derived_period or _default_prior_period(org_id, tenant)
        row = _rep_commissions_row(org_id, employee, derived_period)
        merge.update({"period": derived_period or "",
                     "commission_amount": _money(row.get("total_payout")) if row else "$0.00"})
        if not row:
            notes.append("No commission snapshot found for this employee/period.")

    return {"merge": merge, "derived_incident_date": derived_incident_date, "derived_period": derived_period,
           "notes": notes, "available_fields": CATEGORY_MERGE_FIELDS.get(category, COMMON_FIELDS)}


# ── send / queue / dispatch core (ONE function for both manual sends and automated fires) ─────────
def _letter_html(subject, body) -> str:
    safe = _html.escape(body or "").replace("\n", "<br>")
    return (f"<div style='font-family:Arial,sans-serif;max-width:560px'>"
            f"<h2 style='color:#1E3A5F;margin:0 0 12px'>{_html.escape(subject or '')}</h2>"
            f"<div style='color:#222;font-size:14px;line-height:1.6'>{safe}</div>"
            f"<p style='color:#999;font-size:11px;margin-top:24px'>Sent by MetricsPro HR.</p></div>")


async def _send_email_for_letter(employee, subject, body):
    addr = (employee or {}).get("email")
    if not addr:
        return False, "employee has no email on file"
    try:
        from app.modules.notify.channels import email_resend
        await email_resend.send_email(addr, subject, _letter_html(subject, body), [])
        return True, None
    except Exception as e:
        return False, str(e)[:300]


async def _create_and_dispatch_letter(org_id, tenant, employee, template, merge, *, incident_date=None,
                                      period=None, trigger="manual", dedupe_key=None, sender=None,
                                      force_send=False, subject_override=None, body_override=None):
    """The ONE send path — used identically by the manual Send-Letter page and every automated sweep.
    delivery_mode drives what happens for an AUTOMATED fire ('auto' sends now, 'approval' queues);
    `force_send` (only ever passed by a human clicking Send) always sends now regardless of mode.
    `subject_override`/`body_override` (only ever passed by a human editing the preview on the
    Send-Letter page) are sent VERBATIM instead of re-rendering the template — directive #5's "fully
    editable before send" applies to the final wording, not just the merge-field values.
    Idempotent when `dedupe_key` is given: the INSERT is the ONLY thing gated by the unique index —
    only the winner of that race ever attempts an email send, so a re-run sweep never double-sends."""
    subject = subject_override if subject_override is not None else render_template(template.get("subject"), merge)
    body = body_override if body_override is not None else render_template(template.get("body"), merge)
    mode = template.get("delivery_mode") or "approval"
    send_now = bool(force_send) or mode == "auto"
    row = {
        "org_id": org_id, "employee_id": (employee or {}).get("employee_id"),
        "employee_name": (employee or {}).get("name"), "employee_email": (employee or {}).get("email"),
        "template_key": template.get("template_key"), "category": template.get("category"),
        "escalation_tier": template.get("escalation_tier"), "subject": subject, "body": body,
        "merge_data": merge, "incident_date": incident_date, "period": period, "delivery_mode": mode,
        "status": "sending" if send_now else "queued_approval", "trigger": trigger,
        "sender": sender or ("system" if trigger == "auto" else None), "dedupe_key": dedupe_key,
    }
    try:
        ins = _so().table("sent_letter").insert(row).execute()
    except Exception:
        return None  # a prior/concurrent run already claimed this dedupe_key — never double-send
    saved = dict((ins.data or [row])[0])
    lid = saved.get("id")
    if not send_now:
        saved["status"] = "queued_approval"
        return saved
    ok, err = await _send_email_for_letter(employee, subject, body)
    final_status = "sent" if ok else "failed"
    try:
        if lid:
            _so().table("sent_letter").update({"status": final_status, "send_error": err}) \
                .eq("org_id", org_id).eq("id", lid).execute()
    except Exception:
        pass
    saved["status"], saved["send_error"] = final_status, err
    return saved


# ════════════════════════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/templates")
def list_templates(org_id: str = ORG_ID, authorization: str = Header(default="")):
    _require_hr_or_admin(authorization)
    _ensure_letter_templates(org_id)
    try:
        rows = (_so().table("letter_template").select("*").eq("org_id", org_id)
                .order("category").execute().data) or []
    except Exception as e:
        raise HTTPException(400, f"Could not load templates — is migration 408 applied? {e}")
    return {"templates": rows, "categories": CATEGORY_LABELS, "merge_fields": CATEGORY_MERGE_FIELDS}


@router.put("/templates/{template_key}")
def update_template(template_key: str, body: dict, org_id: str = ORG_ID,
                    authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    _require_letters_admin(authorization, x_active_org, org_id)
    existing = _get_template(org_id, template_key)
    if not existing:
        raise HTTPException(404, "unknown template_key for this org — is migration 408 applied?")
    upd = {}
    if "subject" in body:
        s = str(body.get("subject") or "").strip()
        if not s:
            raise HTTPException(400, "subject cannot be blank")
        upd["subject"] = s
    if "body" in body:
        b = str(body.get("body") or "").strip()
        if not b:
            raise HTTPException(400, "body cannot be blank")
        upd["body"] = b
    if "delivery_mode" in body:
        dm = (body.get("delivery_mode") or "").strip()
        if dm not in ("auto", "approval"):
            raise HTTPException(400, "delivery_mode must be 'auto' or 'approval'")
        upd["delivery_mode"] = dm
    if "active" in body:
        upd["active"] = bool(body["active"])
    if not upd:
        return existing
    upd["is_default"] = False
    upd["updated_at"] = _now_iso()
    try:
        r = _so().table("letter_template").update(upd).eq("org_id", org_id).eq("template_key", template_key).execute()
    except Exception as e:
        raise HTTPException(500, f"save failed: {e}")
    return (r.data or [dict(existing, **upd)])[0]


@router.get("/merge-defaults")
def merge_defaults(org_id: str = ORG_ID, employee_id: str = "", template_key: str = "", category: str = "",
                   incident_date: str = "", period: str = "", authorization: str = Header(default="")):
    _require_hr_or_admin(authorization)
    employee = _find_employee(org_id, employee_id) if employee_id else None
    if employee_id and not employee:
        raise HTTPException(404, "employee not found")
    cat = category
    tpl = None
    if template_key:
        tpl = _get_template(org_id, template_key)
        cat = cat or (tpl or {}).get("category") or ""
    if not cat:
        raise HTTPException(400, "category or a valid template_key is required")
    out = build_merge_defaults(org_id, employee, cat, incident_date or None, period or None)
    out["template"] = tpl
    return out


@router.post("/send")
async def send_letter(body: dict, org_id: str = ORG_ID, authorization: str = Header(default="")):
    _, email, _role = _require_hr_or_admin(authorization)
    employee_id = (body.get("employee_id") or "").strip()
    template_key = (body.get("template_key") or "").strip()
    if not employee_id or not template_key:
        raise HTTPException(400, "employee_id and template_key are required")
    employee = _find_employee(org_id, employee_id)
    if not employee:
        raise HTTPException(404, "employee not found")
    template = _get_template(org_id, template_key)
    if not template:
        raise HTTPException(400, f"unknown template_key '{template_key}' — is migration 408 applied?")
    if not template.get("active", True):
        raise HTTPException(400, "this template is inactive — activate it in the template library first")
    incident_date = (body.get("incident_date") or "").strip() or None
    period = (body.get("period") or "").strip() or None
    defaults = build_merge_defaults(org_id, employee, template.get("category"), incident_date, period)
    merge = dict(defaults.get("merge") or {})
    for k, v in (body.get("merge_overrides") or {}).items():
        if v is not None and str(v).strip() != "":
            merge[k] = v
    incident_final = incident_date or defaults.get("derived_incident_date")
    period_final = period or defaults.get("derived_period")
    tenant = _tenant_row(org_id)
    subject_override = body.get("subject")
    body_override = body.get("body")
    letter = await _create_and_dispatch_letter(
        org_id, tenant, employee, template, merge, incident_date=incident_final, period=period_final,
        trigger="manual", sender=email, force_send=bool(body.get("force_send")),
        subject_override=(str(subject_override) if subject_override else None),
        body_override=(str(body_override) if body_override else None))
    if not letter:
        raise HTTPException(500, "Could not create the letter record — is migration 408 applied?")
    return letter


@router.get("/queue")
def list_queue(org_id: str = ORG_ID, authorization: str = Header(default="")):
    _require_hr_or_admin(authorization)
    try:
        rows = (_so().table("sent_letter").select("*").eq("org_id", org_id)
                .in_("status", ["queued_approval", "failed"]).order("created_at", desc=True).execute().data) or []
    except Exception:
        rows = []
    return {"queue": rows}


@router.post("/queue/{letter_id}/approve")
async def approve_letter(letter_id: str, body: dict = None, org_id: str = ORG_ID,
                         authorization: str = Header(default="")):
    """GATE-1 LOW-1 fix: the letter is CLAIMED (status -> 'sending') by an UPDATE that only ever
    matches while status is still 'queued_approval'/'failed' — the exact same "filtered update as an
    atomic claim" idiom the payroll-chargeback decision endpoint and the sent_letter dedupe_key
    already rely on elsewhere in this codebase. Zero rows affected means someone/something already
    claimed or resolved this letter (already sending/sent/rejected) — refused, never a duplicate
    disciplinary send. The email is only ever sent AFTER a successful claim; a failure to record the
    FINAL status (after the email is already out) is surfaced as a warning in the response, never a
    bare 500 that could look like "nothing happened" to the caller."""
    _, email, _role = _require_hr_or_admin(authorization)
    body = body or {}
    claim_patch = {"status": "sending"}
    if body.get("subject"):
        claim_patch["subject"] = str(body["subject"])
    if body.get("body"):
        claim_patch["body"] = str(body["body"])
    claim = (_so().table("sent_letter").update(claim_patch).eq("org_id", org_id).eq("id", letter_id)
             .in_("status", ["queued_approval", "failed"]).execute())
    claimed = claim.data or []
    if not claimed:
        existing = (_so().table("sent_letter").select("status").eq("org_id", org_id)
                    .eq("id", letter_id).limit(1).execute().data) or []
        if not existing:
            raise HTTPException(404, "letter not found")
        raise HTTPException(409, f"letter is already '{existing[0].get('status')}' — nothing to approve")
    row = claimed[0]
    subject = row.get("subject") or ""
    letter_body = row.get("body") or ""
    employee = {"employee_id": row.get("employee_id"), "name": row.get("employee_name"),
               "email": row.get("employee_email")}
    ok, err = await _send_email_for_letter(employee, subject, letter_body)
    final_status = "approved_sent" if ok else "failed"
    final_patch = {"status": final_status, "approved_by": email, "approved_at": _now_iso(), "send_error": err}
    try:
        r = _so().table("sent_letter").update(final_patch).eq("org_id", org_id).eq("id", letter_id).execute()
        return (r.data or [dict(row, **final_patch)])[0]
    except Exception as e:
        out = dict(row, **final_patch)
        out["warning"] = (f"The email was already sent (status={final_status}) but recording the final "
                          f"status failed — re-check this letter's row manually: {e}")
        return out


@router.post("/queue/{letter_id}/reject")
def reject_letter(letter_id: str, body: dict = None, org_id: str = ORG_ID, authorization: str = Header(default="")):
    _, email, _role = _require_hr_or_admin(authorization)
    body = body or {}
    rows = (_so().table("sent_letter").select("*").eq("org_id", org_id).eq("id", letter_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "letter not found")
    if rows[0].get("status") not in ("queued_approval", "failed"):
        raise HTTPException(409, f"letter is already '{rows[0].get('status')}'")
    upd = {"status": "rejected", "rejected_reason": (body.get("reason") or "").strip() or None,
          "approved_by": email, "approved_at": _now_iso()}
    r = _so().table("sent_letter").update(upd).eq("org_id", org_id).eq("id", letter_id).execute()
    return (r.data or [dict(rows[0], **upd)])[0]


@router.get("/periods")
def list_periods(org_id: str = ORG_ID, authorization: str = Header(default="")):
    """GATE-1 NIT-1: real `commcalc.rep_commissions` periods for this org — RULE THREE pick-don't-type
    source for the Send-Letter page's Period field (kpi_miss/commission_statement/metrics_miss_2consec),
    instead of a free-text box. Most-recent first."""
    _require_hr_or_admin(authorization)
    try:
        rows = (_cc().table("rep_commissions").select("period").eq("org_id", org_id).execute().data) or []
    except Exception:
        return {"periods": []}
    periods = sorted({(r.get("period") or "").strip() for r in rows if r.get("period")}, reverse=True)
    return {"periods": periods}


@router.get("/sent")
def list_sent(org_id: str = ORG_ID, employee_id: str = "", category: str = "", limit: int = 200,
             authorization: str = Header(default="")):
    _require_hr_or_admin(authorization)
    try:
        q = _so().table("sent_letter").select("*").eq("org_id", org_id)
        if employee_id:
            q = q.eq("employee_id", employee_id)
        if category:
            q = q.eq("category", category)
        rows = q.order("created_at", desc=True).limit(min(max(limit, 1), 1000)).execute().data or []
    except Exception:
        rows = []
    return {"letters": rows}


# ── automation config (per-tenant knobs — RULE TWO) ────────────────────────────────────────────────
@router.get("/config")
def get_letters_config(org_id: str = ORG_ID, authorization: str = Header(default="")):
    _require_hr_or_admin(authorization)
    tenant = _tenant_row(org_id)
    raw = tenant.get("hr_letters_config") or {}
    lc = dict(DEFAULT_LETTERS_CONFIG["late_clockin"])
    lc.update(raw.get("late_clockin") or {})
    lc["grace_minutes"] = grace_minutes_from_config(lc)
    lc["strike_window_days"] = strike_window_days_from_config(lc)
    lc["enabled"] = bool(lc.get("enabled"))
    mm = dict(DEFAULT_LETTERS_CONFIG["metrics_miss"])
    mm.update(raw.get("metrics_miss") or {})
    mm["enabled"] = bool(mm.get("enabled"))
    return {"late_clockin": lc, "metrics_miss": mm}


@router.put("/config")
def put_letters_config(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                       x_active_org: str = Header(default="")):
    _require_letters_admin(authorization, x_active_org, org_id)
    lc_in, mm_in = (body.get("late_clockin") or {}), (body.get("metrics_miss") or {})
    cfg = {
        "late_clockin": {"enabled": bool(lc_in.get("enabled")),
                        "grace_minutes": grace_minutes_from_config(lc_in),
                        "strike_window_days": strike_window_days_from_config(lc_in)},
        "metrics_miss": {"enabled": bool(mm_in.get("enabled"))},
    }
    try:
        rows = _so().table("tenants").select("org_id").eq("org_id", org_id).limit(1).execute().data or []
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 408 first: {e}")
    if not rows:
        raise HTTPException(404, "no tenant record for this org — complete tenant setup first")
    try:
        _so().table("tenants").update({"hr_letters_config": cfg}).eq("org_id", org_id).execute()
    except Exception as e:
        raise HTTPException(500, f"save failed — run migration 408 first: {e}")
    return cfg


# ════════════════════════════════════════════════════════════════════════════════════════════════
# AUTOMATION SWEEPS (reuse the existing */run-due · NOTIFY_RUN_SECRET pattern — no new scheduler)
# ════════════════════════════════════════════════════════════════════════════════════════════════
async def _run_late_checkin_for_org(org_id, tenant, eval_date: date) -> dict:
    cfg = (tenant.get("hr_letters_config") or {}).get("late_clockin") or {}
    grace = grace_minutes_from_config(cfg)
    window_days = strike_window_days_from_config(cfg)
    tzname = _biz_tz_for(org_id, tenant)
    eval_iso = eval_date.isoformat()

    shifts = (_so().table("shifts").select("employee_id,employee_name,store_code,start_time")
              .eq("org_id", org_id).eq("shift_date", eval_iso).eq("is_deleted", False).execute().data) or []
    by_emp = {}
    for s in shifts:
        eid = (s.get("employee_id") or "").strip()
        if not eid:
            continue
        st = (s.get("start_time") or "").strip()
        cur = by_emp.get(eid)
        if not cur or (st and (not cur.get("start_time") or st < cur["start_time"])):
            by_emp[eid] = {"employee_id": eid, "employee_name": s.get("employee_name"),
                          "store_code": s.get("store_code"), "start_time": st}
    if not by_emp:
        return {"scheduled": 0, "late": 0, "letters": 0}

    eids = list(by_emp.keys())
    tl = (_so().table("timelog").select("employee_id,clock_in")
          .eq("org_id", org_id).eq("work_date", eval_iso).in_("employee_id", eids).execute().data) or []
    punches_by_emp = {}
    for p in tl:
        eid = (p.get("employee_id") or "").strip()
        if eid:
            punches_by_emp.setdefault(eid, []).append(p)

    existing = (_so().table("late_clockin_strike").select("employee_id")
                .eq("org_id", org_id).eq("work_date", eval_iso).execute().data) or []
    done = {(r.get("employee_id") or "").strip() for r in existing}

    late_count = letters_sent = 0
    for eid, info in by_emp.items():
        if eid in done:
            continue
        result = compute_lateness(info.get("start_time"), punches_by_emp.get(eid, []), grace, eval_date, tzname)
        if not result:
            continue
        late_count += 1
        window_start = (eval_date - timedelta(days=window_days)).isoformat()
        prior = (_so().table("late_clockin_strike").select("id").eq("org_id", org_id).eq("employee_id", eid)
                 .gte("work_date", window_start).lt("work_date", eval_iso).execute().data) or []
        strike_number = len(prior) + 1
        tier = tier_for_strike_count(strike_number)
        strike_row = {"org_id": org_id, "employee_id": eid, "employee_name": info.get("employee_name"),
                      "store_code": info.get("store_code"), "work_date": eval_iso,
                      "scheduled_start": info.get("start_time"), "grace_minutes": grace,
                      "first_punch_at": result["first_punch_at"], "minutes_late": result["minutes_late"],
                      "strike_number": strike_number, "tier": tier}
        try:
            ins = _so().table("late_clockin_strike").insert(strike_row).execute()
            strike_id = (ins.data or [{}])[0].get("id")
        except Exception:
            continue  # unique-constraint collision with a concurrent run — already handled, skip

        employee = _find_employee(org_id, eid) or {"employee_id": eid, "name": info.get("employee_name")}
        template = _get_template(org_id, f"late_clockin_tier{tier}")
        letter_id = None
        if template and template.get("active", True):
            merge = _common_merge(org_id, tenant, employee)
            merge.update({"incident_date": eval_iso, "scheduled_start": info.get("start_time"),
                         "actual_clockin": _fmt_time(result["first_punch_at"]),
                         "minutes_late": result["minutes_late"], "grace_minutes": grace,
                         "strike_count": strike_number})
            letter = await _create_and_dispatch_letter(
                org_id, tenant, employee, template, merge, incident_date=eval_iso, trigger="auto",
                dedupe_key=f"late_clockin:{eid}:{eval_iso}")
            if letter:
                letter_id = letter.get("id")
                if letter.get("status") in ("sent", "queued_approval"):
                    letters_sent += 1
        if strike_id and letter_id:
            try:
                _so().table("late_clockin_strike").update({"sent_letter_id": letter_id}) \
                    .eq("org_id", org_id).eq("id", strike_id).execute()
            except Exception:
                pass
    return {"scheduled": len(by_emp), "late": late_count, "letters": letters_sent}


async def _run_metrics_miss_for_org(org_id, tenant) -> dict:
    cfg = (tenant.get("hr_letters_config") or {}).get("metrics_miss") or {}
    if not cfg.get("enabled"):
        return {"skipped": "disabled"}
    period = _default_prior_period(org_id, tenant)
    prior_period = _shift_period(period, -1)
    template = _get_template(org_id, "metrics_miss_2consec")
    if not template:
        return {"skipped": "template missing — is migration 408 applied?"}
    emps = (_so().table("employees").select("employee_id,name,email,home_store,epay_salesperson")
            .eq("org_id", org_id).eq("is_active", True).execute().data) or []
    fired = 0
    for e in emps:
        row_cur = _rep_commissions_row(org_id, e, period)
        row_prior = _rep_commissions_row(org_id, e, prior_period)
        if not (_is_kpi_miss(row_cur) and _is_kpi_miss(row_prior)):
            continue
        merge = _common_merge(org_id, tenant, e)
        merge.update({"period": period, "prior_period": prior_period,
                     "kpi_summary": _kpi_summary_from_row(row_cur) or "No KPI snapshot.",
                     "commission_amount": _money((row_cur or {}).get("total_payout"))})
        letter = await _create_and_dispatch_letter(
            org_id, tenant, e, template, merge, period=period, trigger="auto",
            dedupe_key=f"metrics_miss_2consec:{e.get('employee_id')}:{period}")
        if letter:
            fired += 1
    return {"checked": len(emps), "fired": fired, "period": period, "prior_period": prior_period}


@router.post("/late-checkin/run-due")
async def late_checkin_run_due(x_notify_secret: str = Header(default=""), eval_date: str = ""):
    """pg_cron entrypoint (same NOTIFY_RUN_SECRET guard as notify/closing/asset run-due sweeps).
    Schedule once daily (e.g. 03:00 business-local) so `eval_date` defaults to a FULLY COMPLETED
    business day (yesterday) — evaluating "today" mid-shift would false-negative anyone who simply
    hasn't clocked in yet. Idempotent per (org, employee, work_date) — safe to re-run/retry."""
    if not verify_notify_secret(x_notify_secret):
        raise HTTPException(403, "forbidden")
    try:
        tens = _so().table("tenants").select("org_id,name,hr_letters_config,timezone").execute().data or []
    except Exception as e:
        # GATE-1 LOW-2: pg_cron can fire before migration 408 has run — degrade cleanly (no tenant has
        # `hr_letters_config` yet, so there's nothing to check) instead of a bare 500.
        return {"tenants_checked": 0, "results": [], "note": f"hr_letters_config unavailable — is migration 408 applied? {e}"}
    results = []
    for t in tens:
        cfg = (t.get("hr_letters_config") or {}).get("late_clockin") or {}
        if not cfg.get("enabled"):
            continue
        oid = t.get("org_id")
        _ensure_letter_templates(oid)
        d = _parse_date(eval_date) if eval_date else (_biz_today(oid, t) - timedelta(days=1))
        try:
            r = await _run_late_checkin_for_org(oid, t, d)
        except Exception as e:
            r = {"error": str(e)[:300]}
        results.append({"org_id": oid, "eval_date": d.isoformat() if d else None, **r})
    return {"tenants_checked": len(tens), "results": results}


@router.post("/metrics-miss/run-due")
async def metrics_miss_run_due(x_notify_secret: str = Header(default="")):
    """pg_cron entrypoint — schedule monthly (e.g. the 2nd of the month, after the prior month's
    commissions have been calculated). Idempotent per (org, employee, period)."""
    if not verify_notify_secret(x_notify_secret):
        raise HTTPException(403, "forbidden")
    try:
        tens = _so().table("tenants").select("org_id,name,hr_letters_config,timezone").execute().data or []
    except Exception as e:
        return {"tenants_checked": 0, "results": [], "note": f"hr_letters_config unavailable — is migration 408 applied? {e}"}
    results = []
    for t in tens:
        cfg = (t.get("hr_letters_config") or {}).get("metrics_miss") or {}
        if not cfg.get("enabled"):
            continue
        oid = t.get("org_id")
        _ensure_letter_templates(oid)
        try:
            r = await _run_metrics_miss_for_org(oid, t)
        except Exception as e:
            r = {"error": str(e)[:300]}
        results.append({"org_id": oid, **r})
    return {"tenants_checked": len(tens), "results": results}
