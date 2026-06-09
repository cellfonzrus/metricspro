import re
from datetime import date
from dateutil.relativedelta import relativedelta
from app.core.database import get_supabase

ORG_ID = "00000000-0000-0000-0000-000000000001"

# Map a payment_type label to (comp_type, month_number_or_None)
def parse_payment_type(pt_raw: str):
    pt = (pt_raw or "").lower().strip()
    month = None
    m = re.search(r"month\s*(\d+)", pt)
    if m:
        month = int(m.group(1))

    if "new activation bounty" in pt:
        return ("NAB", month)
    if "simplified sim loading" in pt:
        return ("SSLB", month)
    if "boost ready bounty" in pt:
        return ("BRB", month)
    if "device upgrade bounty" in pt:
        return ("DUPGB", month)
    if "in-store device financing" in pt:
        return ("ISDFB", month)
    if "device financing bounty" in pt:
        return ("DFB", month)
    if "byod spiff" in pt:
        return ("BYOD_SPIFF", month)
    if "monthly incentive" in pt or "residual" in pt:
        return ("MI", month)
    if "auto top" in pt or "atu" in pt:
        return ("ATUMI", month)
    if "sim card reimbursement" in pt or "sim reimbursement" in pt:
        return ("SIMCR", month)
    # Device reimbursement promos
    if any(x in pt for x in ["promo upgrade", "promo pic", "promo new act",
                              "exclusive upgrade", "device reimbursement",
                              "device discount", "trade-in"]):
        return ("DEVICE_REIMB", month)
    return ("UNMAPPED:" + pt[:40], month)


ACTIVATION_TYPE_MAP = {
    "activation": "new_act",
    "activation add a line": "aal",
    "eligible port-in activation": "port_in",
    "eligible port-in add a line": "aal",
    "pml ineligible port in activation": "port_in",
    "upgrade": "upgrade",
    "byod": "byod",
    "byod add a line": "byod_aal",
    "byod port-in": "byod",
    "byod port-in add a line": "byod_aal",
    "byod swap": "excluded",
    "swap": "excluded",
}

# Comp types that pay on a lag (months). Editable concept; default 2.
LAG_MONTHS = {"MI": 2, "ATUMI": 2}

# Comp types we track but never flag as underpaid (informational)
INFORMATIONAL = {"SIMCR"}


def _period_to_label(period: str) -> str:
    """'2026-04' -> 'April 2026'"""
    year, month = int(period[:4]), int(period[5:7])
    return date(year, month, 1).strftime("%B %Y")


def _plan_category(plan_name: str) -> str:
    if not plan_name:
        return "phone"
    p = plan_name.lower()
    if any(x in p for x in ["tablet", "watch", "hotspot", "ipad"]):
        return "tablet"
    return "phone"


def _months_between(start: date, end: date) -> int:
    d = relativedelta(end, start)
    return d.years * 12 + d.months + 1  # activation month = month 1


def _get_comp_rate(comp_type, plan_cat, as_of, rates):
    cands = [
        r for r in rates
        if r["comp_type"] == comp_type
        and (r["plan_category"] is None or r["plan_category"] == plan_cat)
        and date.fromisoformat(r["effective_date"]) <= as_of
    ]
    if not cands:
        return None
    best = max(cands, key=lambda r: r["effective_date"])
    return float(best["value"])


def _get_hotsheet_row(device_model, sale_date, hotsheet):
    if not device_model:
        return None
    model_l = device_model.lower().strip()
    cands = [
        h for h in hotsheet
        if h["device_model"].lower().strip() in model_l
        or model_l in h["device_model"].lower().strip()
    ]
    cands = [h for h in cands if date.fromisoformat(h["effective_date"]) <= sale_date]
    if not cands:
        return None
    return max(cands, key=lambda h: h["effective_date"])


def run_discrepancy(period: str) -> dict:
    client = get_supabase()
    year, month = int(period[:4]), int(period[5:7])
    period_start = date(year, month, 1)
    period_label = _period_to_label(period)

    # ── Reference data ───────────────────────────────────────────────
    rates = (client.schema("commcalc").table("comp_rates")
             .select("*").eq("org_id", ORG_ID).execute().data) or []
    hotsheet = (client.schema("commcalc").table("hotsheet")
                .select("*").eq("org_id", ORG_ID).execute().data) or []

    # ── Active MI rows for THIS period only ──────────────────────────
    mi_rows = (client.schema("commcalc").table("raw_mi")
               .select("device_serial,phone_number,customer_plan,commissionable_mrc,"
                       "mi_activation_date,rep_username,door_type,subscriber_status,period")
               .eq("org_id", ORG_ID)
               .eq("subscriber_status", "ACTIVE")
               .eq("period", period_label)
               .execute().data) or []

    # ── Sales (all months) keyed by IMEI ─────────────────────────────
    sales = (client.schema("commcalc").table("raw_sales")
             .select("serial_1,store,salesperson,contract_type,mdn,product_desc,"
                     "ext_price,trans_date,tender_type")
             .eq("org_id", ORG_ID).execute().data) or []
    sales_by_imei = {}
    for s in sales:
        imei = (s.get("serial_1") or "").strip()
        if imei:
            sales_by_imei[imei] = s

    # ── Payments for THIS period, keyed by (imei, comp_type, month) ──
    pays = (client.schema("commcalc").table("raw_payment_detail")
            .select("imei,payment_type,amount,mdn,payment_date,period_month,period_year")
            .eq("org_id", ORG_ID)
            .eq("period_month", month)
            .eq("period_year", year)
            .execute().data) or []

    received = {}  # (imei, comp_type, month) -> amount
    received_any = {}  # (imei, comp_type) -> amount (month-agnostic)
    for p in pays:
        imei = (p.get("imei") or "").strip()
        comp, pmonth = parse_payment_type(p.get("payment_type"))
        amt = float(p.get("amount") or 0)
        received[(imei, comp, pmonth)] = received.get((imei, comp, pmonth), 0.0) + amt
        received_any[(imei, comp)] = received_any.get((imei, comp), 0.0) + amt

    # Clear previous results for this period
    client.schema("commcalc").table("discrepancy_results")\
        .delete().eq("org_id", ORG_ID).eq("period", period).execute()

    results = []
    total_gap = 0.0

    for mi in mi_rows:
        imei = (mi.get("device_serial") or "").strip()
        if not imei:
            continue
        act_str = mi.get("mi_activation_date")
        if not act_str:
            continue
        try:
            act_date = date.fromisoformat(str(act_str)[:10])
        except Exception:
            continue

        bmonth = _months_between(act_date, period_start)
        mrc = float(mi.get("base_mrc") or mi.get("commissionable_mrc") or 0)
        plan = mi.get("customer_plan") or ""
        plan_cat = _plan_category(plan)
        mdn = mi.get("phone_number") or ""
        sale = sales_by_imei.get(imei, {})
        store = sale.get("store") or mi.get("door_type") or "Unknown"
        device_model = sale.get("product_desc") or ""
        act_raw = (sale.get("contract_type") or "").lower().strip()
        act_type = ACTIVATION_TYPE_MAP.get(act_raw, "new_act")
        if act_type == "excluded":
            continue

        def add(comp_type, expected, match_month, status_override=None):
            # For lagged comp, expected was due (lag) months ago; check if it
            # arrived in THIS period's payments.
            rec = received.get((imei, comp_type, match_month))
            if rec is None:
                rec = received_any.get((imei, comp_type), 0.0)
            rec = rec or 0.0
            gap = round(expected - rec, 2)
            if status_override:
                status = status_override
            elif comp_type in INFORMATIONAL:
                status = "info"
            elif gap > 0.50:
                status = "open"
            else:
                status = "ok"
            results.append({
                "org_id": ORG_ID, "period": period, "imei": imei, "mdn": mdn,
                "store": store, "rep_username": mi.get("rep_username") or sale.get("salesperson") or "",
                "activation_date": act_date.isoformat(), "activation_type": act_raw,
                "device_model": device_model, "customer_plan": plan,
                "commissionable_mrc": mrc, "bounty_month": match_month or bmonth,
                "comp_type": comp_type, "expected_amount": round(expected, 2),
                "received_amount": round(rec, 2), "gap": gap, "status": status,
            })
            return gap if status == "open" else 0.0

        # ── Same-month bounties (paid 3-day lag, month-specific labels) ──
        if act_type in ("new_act", "port_in", "byod") and 1 <= bmonth <= 6:
            r = _get_comp_rate("NAB", plan_cat, period_start, rates)
            if r and mrc > 0:
                total_gap += add("NAB", mrc * r, bmonth)

        if 1 <= bmonth <= 6:
            r = _get_comp_rate("SSLB", plan_cat, period_start, rates)
            if r and mrc > 0:
                total_gap += add("SSLB", mrc * r, bmonth)

        if act_type in ("new_act", "port_in", "byod", "aal") and 1 <= bmonth <= 6:
            r = _get_comp_rate("BRB", plan_cat, period_start, rates)
            if r and mrc > 0:
                total_gap += add("BRB", mrc * r, bmonth)

        if act_type == "upgrade" and 1 <= bmonth <= 6:
            r = _get_comp_rate("DUPGB", plan_cat, period_start, rates)
            if r and mrc > 0:
                total_gap += add("DUPGB", mrc * r, bmonth)

        if act_type in ("new_act", "port_in") and 1 <= bmonth <= 6:
            tender = (sale.get("tender_type") or "").lower()
            if "devfi" in tender or "financing" in tender:
                r = _get_comp_rate("ISDFB", plan_cat, period_start, rates)
                if r and mrc > 0:
                    total_gap += add("ISDFB", mrc * r, bmonth)

        # ── Lagged comp (MI / ATUMI): expected (lag) months ago, paid now ──
        for comp_type in ("MI", "ATUMI"):
            lag = LAG_MONTHS.get(comp_type, 2)
            # the month being reconciled this period
            target_bmonth = bmonth - lag
            if target_bmonth < 1:
                continue
            r = _get_comp_rate(comp_type, plan_cat, period_start, rates)
            if r and mrc > 0:
                total_gap += add(comp_type, mrc * r, target_bmonth)

        # ── SIMCR (informational, month 1) ──
        if bmonth == 1:
            r = _get_comp_rate("SIMCR", plan_cat, period_start, rates)
            if r:
                add("SIMCR", r, 1)

        # ── Device reimbursement (hotsheet: SRP - promo) ──
        if bmonth == 1:
            hs = _get_hotsheet_row(device_model, act_date, hotsheet)
            if hs:
                promo_map = {
                    "new_act": hs.get("promo_non_port"),
                    "port_in": hs.get("promo_port_in"),
                    "upgrade": hs.get("promo_upgrade"),
                    "aal": hs.get("promo_aal"),
                    "byod": None,
                }
                promo = promo_map.get(act_type)
                srp = hs.get("srp")
                if srp is not None and promo is not None and float(srp) > float(promo):
                    total_gap += add("DEVICE_REIMB", float(srp) - float(promo), None)

    # Dedupe by (imei, comp_type, bounty_month); keep largest gap
    seen = {}
    for r in results:
        if r["status"] == "ok":
            continue  # don't store perfectly-matched rows
        key = (r["imei"], r["comp_type"], r["bounty_month"])
        if key not in seen or r["gap"] > seen[key]["gap"]:
            seen[key] = r
    rows_to_save = list(seen.values())
    flagged = len([r for r in rows_to_save if r["status"] == "open"])

    BATCH = 500
    for i in range(0, len(rows_to_save), BATCH):
        client.schema("commcalc").table("discrepancy_results")\
            .insert(rows_to_save[i:i + BATCH]).execute()

    return {
        "period": period,
        "total_imeis_checked": len(mi_rows),
        "discrepancy_rows_saved": len(rows_to_save),
        "flagged_underpaid": flagged,
        "total_gap_usd": round(total_gap, 2),
    }
