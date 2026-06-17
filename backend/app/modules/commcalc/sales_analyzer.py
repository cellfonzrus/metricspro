"""Sales Analyzer — 3-Month Retention (3MR) behavior per rep.

For a selected period P, takes each rep's activations from ~3 months earlier (the cohort month
= P − 3) and finds which of those subscribers churned — cancelled / ported out / suspended /
deactivated — BEFORE their 3rd bill payment (i.e. within `window_days`, default 90, of
activation). Surfaces a per-rep 3MR summary plus the churned line items: phone model, monthly
charge (MRC), what it was sold for, activation + churn dates, and the store.

Data: commcalc.raw_mi (subscriber_status, phone_number, device_serial, mi_activation_date,
mi_deactivation_date, base_mrc/commissionable_mrc, rep_username) joined to commcalc.raw_sales
(mdn / serial_1 → product_desc, ext_price, store, trans_date) for the device + sale detail.
Rep display name resolves via commcalc.name_map (epay_login → storeops_name), same as the
commission calculator. 3MR % here is cohort-based from MI and may differ slightly from the
official DLAR 3MR KPI (different denominator); the value is the drill-down it can't give.
"""
from datetime import date
from app.modules.commcalc.calculator import parse_period, safe_float


def _fetch_all(client, table, select, eqs=None, page=1000, cap=300000):
    out, start = [], 0
    while start < cap:
        q = client.schema("commcalc").table(table).select(select)
        for k, v in (eqs or {}).items():
            q = q.eq(k, v)
        rows = (q.range(start, start + page - 1).execute().data) or []
        out.extend(rows)
        if len(rows) < page:
            break
        start += page
    return out


def _d(s):
    """'YYYY-MM-DD...' → date or None."""
    s = str(s or "").strip()
    if len(s) < 10 or not s[:4].isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[5:7]), int(s[8:10]))
    except (ValueError, TypeError):
        return None


def _cohort_month(period):
    """The activation cohort month = the selected period minus 3 calendar months."""
    pm = parse_period(period)
    y, m = pm["year"], pm["month"]
    m -= 3
    while m <= 0:
        m += 12
        y -= 1
    return y, m


def _churn_reason(status):
    s = (status or "").strip().lower()
    if "cancel" in s:
        return "Cancelled"
    if "port" in s:
        return "Ported Out"
    if "suspend" in s:
        return "Suspended"
    if "deactiv" in s:
        return "Deactivated"
    return (status or "").strip() or "Lost"


def _name_map(client, org_id):
    out = {}
    try:
        for n in _fetch_all(client, "name_map", "epay_login,storeops_name", {"org_id": org_id}):
            lo = (n.get("epay_login") or "").lower().strip()
            if lo:
                out[lo] = n.get("storeops_name") or ""
    except Exception:
        pass
    return out


def _sales_index(client, org_id):
    """phone(mdn) / serial → {model, sold, store, sale_date, rep} from raw_sales."""
    by_phone, by_serial = {}, {}
    for r in _fetch_all(client, "raw_sales",
                        "store,salesperson,product_desc,ext_price,trans_date,mdn,serial_1,department,contract_type",
                        {"org_id": org_id}):
        # prefer device lines (a model/price), skip pure plan/accessory rows for the device detail
        info = {"model": (r.get("product_desc") or "").strip(),
                "sold": safe_float(r.get("ext_price")),
                "store": (r.get("store") or "").strip(),
                "sale_date": r.get("trans_date"),
                "rep": (r.get("salesperson") or "").strip()}
        mdn = (r.get("mdn") or "").strip()
        ser = (r.get("serial_1") or "").strip()
        # keep the highest-value line per phone/serial — that's the device sale (model + price),
        # not the $0 plan/accessory lines on the same transaction.
        if mdn and (mdn not in by_phone or info["sold"] > by_phone[mdn]["sold"]):
            by_phone[mdn] = info
        if ser and (ser not in by_serial or info["sold"] > by_serial[ser]["sold"]):
            by_serial[ser] = info
    return by_phone, by_serial


def analyze(client, org_id, period, window_days=90, rep=""):
    cy, cm = _cohort_month(period)
    cohort_label = date(cy, cm, 1).strftime("%B %Y")
    namemap = _name_map(client, org_id)
    by_phone, by_serial = _sales_index(client, org_id)

    # Dedupe raw_mi to one row per subscriber, preferring the row that carries a deactivation
    # date (the churn signal) so we don't miss subs that dropped off later MI reports.
    rows = _fetch_all(client, "raw_mi",
                      "subscriber_id,subscriber_status,phone_number,device_serial,mi_activation_date,"
                      "mi_deactivation_date,base_mrc,commissionable_mrc,customer_plan,rep_username,period")
    subs = {}
    for r in rows:
        key = (r.get("subscriber_id") or r.get("phone_number") or "").strip()
        if not key:
            continue
        cur = subs.get(key)
        if cur is None or (r.get("mi_deactivation_date") and not cur.get("mi_deactivation_date")):
            subs[key] = r

    reps = {}   # rep_login -> aggregates
    churned_rows = []
    for r in subs.values():
        act = _d(r.get("mi_activation_date"))
        if not act or (act.year, act.month) != (cy, cm):
            continue                                   # only the 3-months-ago cohort
        login = (r.get("rep_username") or "").strip()
        name = namemap.get(login.lower()) or login or "(unknown rep)"
        agg = reps.setdefault(login, {"rep_login": login, "rep": name,
                                      "cohort": 0, "churned": 0})
        agg["cohort"] += 1

        deact = _d(r.get("mi_deactivation_date"))
        lost_before_3rd = bool(deact and (deact - act).days <= window_days)
        if not lost_before_3rd:
            continue
        agg["churned"] += 1
        phone = (r.get("phone_number") or "").strip()
        serial = (r.get("device_serial") or "").strip()
        sale = by_phone.get(phone) or by_serial.get(serial) or {}
        mrc = safe_float(r.get("base_mrc")) or safe_float(r.get("commissionable_mrc"))
        churned_rows.append({
            "rep": name, "rep_login": login,
            "phone_number": phone,
            "device_model": sale.get("model") or r.get("customer_plan") or "",
            "charged_mrc": round(mrc, 2),
            "sold_for": round(safe_float(sale.get("sold")), 2),
            "store": sale.get("store") or "",
            "activation_date": r.get("mi_activation_date"),
            "churn_date": r.get("mi_deactivation_date"),
            "days_active": (deact - act).days,
            "reason": _churn_reason(r.get("subscriber_status")),
            "plan": (r.get("customer_plan") or "").strip(),
        })

    summary = []
    for a in reps.values():
        coh, ch = a["cohort"], a["churned"]
        retained = coh - ch
        a["retained"] = retained
        a["retention_pct"] = round(100.0 * retained / coh, 1) if coh else 0.0
        a["churn_pct"] = round(100.0 * ch / coh, 1) if coh else 0.0
        summary.append(a)
    summary.sort(key=lambda x: (-x["churned"], x["rep"]))
    churned_rows.sort(key=lambda x: (x["rep"], x["churn_date"] or ""))

    if rep:
        rl = rep.lower()
        summary = [s for s in summary if rl in (s["rep"] or "").lower() or rl in (s["rep_login"] or "").lower()]
        churned_rows = [c for c in churned_rows if rl in (c["rep"] or "").lower() or rl in (c["rep_login"] or "").lower()]

    tot_cohort = sum(a["cohort"] for a in summary)
    tot_churn = sum(a["churned"] for a in summary)
    return {
        "period": period,
        "cohort_month": cohort_label,
        "window_days": window_days,
        "totals": {"cohort": tot_cohort, "churned": tot_churn,
                   "retained": tot_cohort - tot_churn,
                   "retention_pct": round(100.0 * (tot_cohort - tot_churn) / tot_cohort, 1) if tot_cohort else 0.0,
                   "lost_value_sold": round(sum(c["sold_for"] for c in churned_rows), 2),
                   "lost_mrc": round(sum(c["charged_mrc"] for c in churned_rows), 2)},
        "reps": summary,
        "churned": churned_rows,
        "note": ("3MR cohort = subscribers a rep activated in " + cohort_label +
                 f"; 'churned before 3rd bill' = deactivated within {window_days} days of activation. "
                 "Device model / sold-for / store come from the matching B2B sale (by phone or serial)."),
    }
