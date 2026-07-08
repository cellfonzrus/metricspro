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


def _is_accessory_line(dept, cat):
    """Accessory line in the B2B sales export — the 'Ondigo' department is the accessory-GP
    convention used elsewhere (daily_sales_actuals.acc_gp); also catch any 'accessor*' label."""
    d = (dept or "").strip().lower()
    c = (cat or "").strip().lower()
    return d == "ondigo" or "accessor" in d or "accessor" in c


def _sales_index(client, org_id):
    """phone(mdn) / serial → device line {model, sold, gp, store, sale_date, rep, trans_id}, PLUS
    accessory $ summed per trans_id (accessories share the device's transaction, with no mdn of their
    own). The device line is the highest-value line on the phone/serial; cost = sold − gp."""
    by_phone, by_serial, acc_by_trans = {}, {}, {}
    for r in _fetch_all(client, "raw_sales",
                        "store,salesperson,product_desc,ext_price,gp,trans_id,trans_date,mdn,serial_1,department,category,contract_type",
                        {"org_id": org_id}):
        price = safe_float(r.get("ext_price"))
        tid = (r.get("trans_id") or "").strip()
        if tid and _is_accessory_line(r.get("department"), r.get("category")):
            acc_by_trans[tid] = acc_by_trans.get(tid, 0.0) + price
        info = {"model": (r.get("product_desc") or "").strip(),
                "sold": price, "gp": safe_float(r.get("gp")),
                "store": (r.get("store") or "").strip(),
                "sale_date": r.get("trans_date"),
                "rep": (r.get("salesperson") or "").strip(),
                "trans_id": tid}
        mdn = (r.get("mdn") or "").strip()
        ser = (r.get("serial_1") or "").strip()
        # keep the highest-value line per phone/serial — that's the device sale (model + price),
        # not the $0 plan/accessory lines on the same transaction.
        if mdn and (mdn not in by_phone or info["sold"] > by_phone[mdn]["sold"]):
            by_phone[mdn] = info
        if ser and (ser not in by_serial or info["sold"] > by_serial[ser]["sold"]):
            by_serial[ser] = info
    return by_phone, by_serial, acc_by_trans


def analyze(client, org_id, period, window_days=90, rep="", store_keys=None):
    """store_keys: None = unrestricted; else a set of UPPER store keys (codes + addresses) the caller may
    see (RBAC scope). Subscribers are scoped to those stores via raw_mi.salesforce_id → store_mapping."""
    cy, cm = _cohort_month(period)
    cohort_label = date(cy, cm, 1).strftime("%B %Y")
    namemap = _name_map(client, org_id)
    by_phone, by_serial, acc_by_trans = _sales_index(client, org_id)

    # RBAC scope: salesforce_id → {store_code, store_address} (UPPER) so subs can be filtered to a
    # manager's stores. Only built when scoping is active.
    sfid_keys = {}
    if store_keys is not None:
        for m in _fetch_all(client, "store_mapping", "salesforce_id,store_code,store_address", {"org_id": org_id}):
            sf = (m.get("salesforce_id") or "").strip()
            if sf:
                sfid_keys[sf] = {(m.get("store_code") or "").strip().upper(), (m.get("store_address") or "").strip().upper()} - {""}

    # Dedupe raw_mi to one row per subscriber, preferring the row that carries a deactivation
    # date (the churn signal) so we don't miss subs that dropped off later MI reports.
    rows = _fetch_all(client, "raw_mi",
                      "subscriber_id,subscriber_status,phone_number,device_serial,mi_activation_date,"
                      "mi_deactivation_date,base_mrc,commissionable_mrc,customer_plan,rep_username,period,salesforce_id",
                      {"org_id": org_id})
    subs = {}
    for r in rows:
        key = (r.get("subscriber_id") or r.get("phone_number") or "").strip()
        if not key:
            continue
        if store_keys is not None:   # RBAC: keep only subs whose store is in the caller's scope
            if not (sfid_keys.get((r.get("salesforce_id") or "").strip(), set()) & store_keys):
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
        agg = reps.setdefault(login, {"rep_login": login, "rep": name, "cohort": 0, "churned": 0,
                                      "emp_loss": 0, "cust_loss": 0, "mixed_loss": 0})
        agg["cohort"] += 1

        deact = _d(r.get("mi_deactivation_date"))
        days = (deact - act).days if deact else None
        lost_before_3rd = bool(deact and days <= window_days)
        if not lost_before_3rd:
            continue
        agg["churned"] += 1
        phone = (r.get("phone_number") or "").strip()
        serial = (r.get("device_serial") or "").strip()
        sale = by_phone.get(phone) or by_serial.get(serial) or {}
        mrc = safe_float(r.get("base_mrc")) or safe_float(r.get("commissionable_mrc"))
        sold = round(safe_float(sale.get("sold")), 2)
        gp = round(safe_float(sale.get("gp")), 2)
        device_cost = round(sold - gp, 2) if sold else 0.0
        acc = round(acc_by_trans.get(sale.get("trans_id") or "", 0.0), 2)

        # Customer- vs employee-driven LOSS heuristic (transparent + tunable). The sale signals an
        # employee-driven loss when the rep gave away margin (sold at/below cost) and/or didn't attach
        # any accessory — a low-value sale. A clean sale (positive margin + accessory attached) that
        # still churned reads as customer-driven. fast-churn (<=30d) is shown as an extra signal.
        below_cost = bool(sold) and gp <= 0
        no_attach = acc <= 0
        fast = days is not None and days <= 30
        score = (1 if below_cost else 0) + (1 if no_attach else 0)
        loss_type = "employee" if score >= 2 else ("customer" if score == 0 else "mixed")
        reasons = []
        if below_cost: reasons.append("sold at/below cost")
        if no_attach: reasons.append("no accessory attach")
        if fast: reasons.append(f"churned in {days}d")
        agg[{"employee": "emp_loss", "customer": "cust_loss", "mixed": "mixed_loss"}[loss_type]] += 1

        churned_rows.append({
            "rep": name, "rep_login": login,
            "phone_number": phone,
            "device_model": sale.get("model") or r.get("customer_plan") or "",
            "charged_mrc": round(mrc, 2),
            "sold_for": sold,
            "device_cost": device_cost,
            "margin": gp,
            "accessory_sale": acc,
            "loss_type": loss_type,
            "loss_reasons": reasons,
            "fast_churn": fast,
            "store": sale.get("store") or "",
            "activation_date": r.get("mi_activation_date"),
            "churn_date": r.get("mi_deactivation_date"),
            "days_active": days,
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
                   "lost_mrc": round(sum(c["charged_mrc"] for c in churned_rows), 2),
                   "lost_accessory": round(sum(c["accessory_sale"] for c in churned_rows), 2),
                   "employee_driven": sum(1 for c in churned_rows if c["loss_type"] == "employee"),
                   "customer_driven": sum(1 for c in churned_rows if c["loss_type"] == "customer"),
                   "mixed": sum(1 for c in churned_rows if c["loss_type"] == "mixed")},
        "reps": summary,
        "churned": churned_rows,
        "note": ("3MR cohort = subscribers a rep activated in " + cohort_label +
                 f"; 'churned before 3rd bill' = deactivated within {window_days} days of activation. "
                 "Device model / cost (sold − GP) / accessory attach come from the matching B2B sale "
                 "(by phone or serial). Loss type: EMPLOYEE-driven = sold at/below cost AND/OR no "
                 "accessory attach; CUSTOMER-driven = a clean sale (margin + attach) that still churned."),
    }
