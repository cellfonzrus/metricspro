from datetime import date
from dateutil.relativedelta import relativedelta
from app.core.database import get_supabase

ORG_ID = "00000000-0000-0000-0000-000000000001"

# Payment detail type → comp_type mapping
PAYMENT_TYPE_MAP = {
    "new activation bounty": "NAB",
    "nab": "NAB",
    "simplified sim loading bounty": "SSLB",
    "sslb": "SSLB",
    "boost ready bounty": "BRB",
    "brb": "BRB",
    "device upgrade bounty": "DUPGB",
    "dupgb": "DUPGB",
    "monthly incentive": "MI",
    "mi": "MI",
    "residual": "MI",
    "auto top up monthly incentive": "ATUMI",
    "atumi": "ATUMI",
    "auto top up": "ATUMI",
    "in-store device financing bounty": "ISDFB",
    "isdfb": "ISDFB",
    "device financing bounty": "DFB",
    "dfb": "DFB",
    "sim card reimbursement": "SIMCR",
    "simcr": "SIMCR",
    "sim reimbursement": "SIMCR",
    "promo new act offer": "DEVICE_REIMB",
    "pnao": "DEVICE_REIMB",
    "promo pic offer": "DEVICE_REIMB",
    "pic": "DEVICE_REIMB",
    "promo upgrade": "DEVICE_REIMB",
    "pu": "DEVICE_REIMB",
    "exclusive upgrade offer": "DEVICE_REIMB",
    "euo": "DEVICE_REIMB",
    "byod spiff": "BYOD_SPIFF",
    "2026 q2 promo upgrade": "DEVICE_REIMB",
    "q2 promo pic offer": "DEVICE_REIMB",
    "q2 promo new act offer": "DEVICE_REIMB",
    "unl premium 2-month promo": "DEVICE_REIMB",
}

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

PLAN_CATEGORY_MAP = {
    # phone plans → 'phone'
    "unlimited": "phone",
    "unlimited+": "phone",
    "unlimited premium": "phone",
    "$25": "phone",
    "$50": "phone",
    "$60": "phone",
}

def _get_plan_category(plan_name: str) -> str:
    if not plan_name:
        return "phone"
    p = plan_name.lower()
    if any(x in p for x in ["tablet", "watch", "hotspot", "ipad"]):
        return "tablet"
    return "phone"

def _months_between(start: date, end: date) -> int:
    delta = relativedelta(end, start)
    return delta.years * 12 + delta.months + 1  # +1 = month 1 is activation month

def _get_comp_rate(comp_type: str, plan_cat: str, as_of: date, rates: list) -> float | None:
    """Find most recent rate for comp_type + plan_cat on or before as_of."""
    candidates = [
        r for r in rates
        if r["comp_type"] == comp_type
        and (r["plan_category"] is None or r["plan_category"] == plan_cat)
        and date.fromisoformat(r["effective_date"]) <= as_of
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda r: r["effective_date"])
    return float(best["value"])

def _get_hotsheet_row(device_model: str, activation_date: date, hotsheet: list) -> dict | None:
    """Find most recent hotsheet row for device on or before activation_date."""
    if not device_model:
        return None
    model_lower = device_model.lower().strip()
    candidates = [
        h for h in hotsheet
        if h["device_model"].lower().strip() in model_lower
        or model_lower in h["device_model"].lower().strip()
    ]
    candidates = [
        h for h in candidates
        if date.fromisoformat(h["effective_date"]) <= activation_date
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda h: h["effective_date"])

def run_discrepancy(period: str) -> dict:
    """
    Main discrepancy engine. period = 'YYYY-MM'
    Returns summary dict with counts and total gap.
    """
    client = get_supabase()
    year, month = int(period[:4]), int(period[5:7])
    period_start = date(year, month, 1)
    # period end = last day of month
    period_end = (period_start + relativedelta(months=1)) - relativedelta(days=1)

    # ── Load reference data ──────────────────────────────────────────────
    rates_resp = client.schema("commcalc").table("comp_rates")        .select("*").eq("org_id", ORG_ID).execute()
    rates = rates_resp.data or []

    hotsheet_resp = client.schema("commcalc").table("hotsheet")        .select("*").eq("org_id", ORG_ID).execute()
    hotsheet = hotsheet_resp.data or []

    # ── Load active IMEIs from MI report ─────────────────────────────────
    mi_resp = client.schema("commcalc").table("raw_mi").select("device_serial,phone_number,subscriber_id,customer_plan,commissionable_mrc,mi_activation_date,rep_username,door_type,report_month,subscriber_status").eq("org_id", ORG_ID).eq("subscriber_status", "ACTIVE").execute()
    mi_rows = mi_resp.data or []

    # ── Load sales for store + activation type context ────────────────────
    sales_resp = client.schema("commcalc").table("raw_sales")        .select("serial_1,store,salesperson,contract_type,mdn,product_desc,ext_price,trans_date")        .eq("org_id", ORG_ID)        .execute()
    sales_by_imei = {}
    for s in (sales_resp.data or []):
        imei = (s.get("serial_1") or "").strip()
        if imei:
            sales_by_imei[imei] = s

    # ── Load payment detail for the period ───────────────────────────────
    pd_resp = client.schema("commcalc").table("raw_payment_detail")        .select("imei,payment_type,amount,mdn,payment_date")        .eq("org_id", ORG_ID)        .gte("payment_date", period_start.isoformat())        .lte("payment_date", period_end.isoformat())        .execute()

    # Aggregate received by IMEI + comp_type
    received = {}  # (imei, comp_type) → total amount
    for pd_row in (pd_resp.data or []):
        imei = (pd_row.get("imei") or "").strip()
        pt = (pd_row.get("payment_type") or "").lower().strip()
        comp = PAYMENT_TYPE_MAP.get(pt)
        if not comp:
            # Try partial match
            for key, val in PAYMENT_TYPE_MAP.items():
                if key in pt or pt in key:
                    comp = val
                    break
        if not comp:
            comp = "UNMAPPED_" + pt[:30]
        key = (imei, comp)
        received[key] = received.get(key, 0.0) + float(pd_row.get("amount") or 0)

    # ── Clear previous results for this period ────────────────────────────
    client.schema("commcalc").table("discrepancy_results")        .delete().eq("org_id", ORG_ID).eq("period", period).execute()

    results = []
    total_gap = 0.0
    flagged_count = 0

    for mi in mi_rows:
        imei = (mi.get("device_serial") or "").strip()
        if not imei:
            continue

        act_date_str = mi.get("mi_activation_date")
        if not act_date_str:
            continue
        try:
            act_date = date.fromisoformat(str(act_date_str)[:10])
        except Exception:
            continue

        bounty_month = _months_between(act_date, period_start)
        mrc = float(mi.get("commissionable_mrc") or 0)
        plan = mi.get("customer_plan") or ""
        plan_cat = _get_plan_category(plan)
        mdn = mi.get("phone_number") or ""

        sale = sales_by_imei.get(imei, {})
        store = sale.get("store") or mi.get("door_type") or "Unknown"
        device_model = sale.get("product_desc") or ""
        activation_raw = (sale.get("contract_type") or "").lower().strip()
        act_type = ACTIVATION_TYPE_MAP.get(activation_raw, "new_act")

        if act_type == "excluded":
            continue

        hs = _get_hotsheet_row(device_model, act_date, hotsheet)

        def add_result(comp_type, expected, imei=imei):
            rec = received.get((imei, comp_type), 0.0)
            gap = round(expected - rec, 2)
            total = expected
            results.append({
                "org_id": ORG_ID,
                "period": period,
                "imei": imei,
                "mdn": mdn,
                "store": store,
                "rep_username": mi.get("rep_username") or sale.get("salesperson") or "",
                "activation_date": act_date.isoformat(),
                "activation_type": activation_raw,
                "device_model": device_model,
                "customer_plan": plan,
                "commissionable_mrc": mrc,
                "bounty_month": bounty_month,
                "comp_type": comp_type,
                "expected_amount": round(expected, 2),
                "received_amount": round(rec, 2),
                "gap": gap,
                "status": "open" if gap > 0.50 else "ok",
            })
            return gap if gap > 0.50 else 0.0

        # ── NAB (months 1–6, new activations + port-ins, not upgrades) ──
        if act_type in ("new_act", "port_in", "byod") and 1 <= bounty_month <= 6:
            rate = _get_comp_rate("NAB", plan_cat, period_start, rates)
            if rate and mrc > 0:
                total_gap += add_result("NAB", mrc * rate)

        # ── MI (ongoing, all eligible) ──
        rate = _get_comp_rate("MI", plan_cat, period_start, rates)
        if rate and mrc > 0:
            total_gap += add_result("MI", mrc * rate)

        # ── SSLB (months 1–6) ──
        if 1 <= bounty_month <= 6:
            rate = _get_comp_rate("SSLB", plan_cat, period_start, rates)
            if rate and mrc > 0:
                total_gap += add_result("SSLB", mrc * rate)

        # ── BRB (months 1–6, new activations only, BR stores) ──
        if act_type in ("new_act", "port_in", "byod", "aal") and 1 <= bounty_month <= 6:
            rate = _get_comp_rate("BRB", plan_cat, period_start, rates)
            if rate and mrc > 0:
                total_gap += add_result("BRB", mrc * rate)

        # ── DUPGB (months 1–6, upgrades only) ──
        if act_type == "upgrade" and 1 <= bounty_month <= 6:
            rate = _get_comp_rate("DUPGB", plan_cat, period_start, rates)
            if rate and mrc > 0:
                total_gap += add_result("DUPGB", mrc * rate)

        # ── ISDFB (months 1–6, if DevFi in sales) ──
        if act_type in ("new_act", "port_in") and 1 <= bounty_month <= 6:
            tender = (sale.get("tender_type") or "").lower()
            if "devfi" in tender or "financing" in tender or "device financing" in tender:
                rate = _get_comp_rate("ISDFB", plan_cat, period_start, rates)
                if rate and mrc > 0:
                    total_gap += add_result("ISDFB", mrc * rate)

        # ── SIMCR ($2.50 flat, month 1 only) ──
        if bounty_month == 1:
            rate = _get_comp_rate("SIMCR", plan_cat, period_start, rates)
            if rate:
                total_gap += add_result("SIMCR", rate)

        # ── Device Reimbursement (hotsheet-based) ──
        if hs and bounty_month == 1:
            promo_map = {
                "new_act": hs.get("promo_non_port"),
                "port_in": hs.get("promo_port_in"),
                "upgrade": hs.get("promo_upgrade"),
                "aal": hs.get("promo_aal"),
                "byod": None,  # BYOD no device reimb
            }
            promo_price = promo_map.get(act_type)
            srp = hs.get("srp")
            if srp and promo_price is not None and srp > promo_price:
                expected_reimb = srp - promo_price
                total_gap += add_result("DEVICE_REIMB", expected_reimb)

        # ── BYOD SPIFF (flat M2 + M3 only, BYOD activations) ──
        if act_type == "byod" and bounty_month in (2, 3):
            plan_lower = plan.lower()
            if "premium" in plan_lower:
                spiff_type = "BYOD_SPIFF_UNL_PREM"
            elif "plus" in plan_lower or "+" in plan_lower:
                spiff_type = "BYOD_SPIFF_UNL_PLUS"
            else:
                spiff_type = "BYOD_SPIFF_UNL"
            rate = _get_comp_rate(spiff_type, plan_cat, period_start, rates)
            if rate:
                total_gap += add_result(spiff_type, rate)

    # Filter to only rows worth saving (gap > $0.50 or received > 0)
    rows_to_save = [r for r in results if r["gap"] > 0.50 or r["received_amount"] > 0]
    flagged_count = len([r for r in rows_to_save if r["gap"] > 0.50])

    # Batch insert
    BATCH = 500
    for i in range(0, len(rows_to_save), BATCH):
        client.schema("commcalc").table("discrepancy_results")            .insert(rows_to_save[i:i+BATCH]).execute()

    return {
        "period": period,
        "total_imeis_checked": len(mi_rows),
        "discrepancy_rows_saved": len(rows_to_save),
        "flagged_underpaid": flagged_count,
        "total_gap_usd": round(total_gap, 2),
    }
