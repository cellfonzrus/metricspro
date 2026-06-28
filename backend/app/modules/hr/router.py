"""HR module API — /api/v1/hr/*

A permission-gated VIEW layer over the salary + commission data that already lives in StoreOps
(employees, shifts, pay_rate) and CommCalc (rep_commissions, chargebacks). It does NOT move any
data — commission is still computed in CommCalc. Reads are scoped to the signed-in manager's org
span (reusing the StoreOps span helpers), so HR figures respect the same boundaries as the rest of
the app. The HR employees / payroll / time-off pages reuse the existing scoped StoreOps endpoints;
this router adds the one genuinely new thing: per-employee TOTAL COMPENSATION (wages + commission).
"""
import calendar
from fastapi import APIRouter, Header
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
        rows.append({"employee_id": e.get("employee_id"), "name": e.get("name"),
                     "store": e.get("home_store"), "pay_rate": rate, "hours": hrs,
                     "wages": wages, "commission": commission, "chargebacks": cb, "total_comp": total})
        tot_w += wages; tot_c += commission; tot_cb += cb

    rows.sort(key=lambda r: -r["total_comp"])
    return {"period": period, "rows": rows,
            "totals": {"wages": round(tot_w, 2), "commission": round(tot_c, 2),
                       "chargebacks": round(tot_cb, 2), "total_comp": round(tot_w + tot_c - tot_cb, 2),
                       "employees": len(rows)}}
