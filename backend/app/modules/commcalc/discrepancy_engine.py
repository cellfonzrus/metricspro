import re
from datetime import date
from dateutil.relativedelta import relativedelta
from app.core.database import get_supabase

ORG_ID = "00000000-0000-0000-0000-000000000001"

# ── Activation type classification (from sales.contract_type) ──────────
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

# Comp types on a payment lag - shown but not counted in gap
LAGGED = {"MI", "ATUMI"}
# Comp types we track but never flag as underpaid
INFORMATIONAL = {"SIMCR"}
LAG_MONTHS = {"MI": 2, "ATUMI": 2}


def parse_payment_type(pt_raw):
    """Map a payment_type / compensation_type label to (comp_type, month)."""
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
    if any(x in pt for x in ["promo upgrade", "promo pic", "promo new act",
                              "exclusive upgrade", "device reimbursement",
                              "device discount", "trade-in"]):
        return ("DEVICE_REIMB", month)
    return ("UNMAPPED:" + pt[:40], month)


def _period_label(period):
    y, m = int(period[:4]), int(period[5:7])
    return date(y, m, 1).strftime("%B %Y")


def _plan_category(plan_name):
    if not plan_name:
        return "phone"
    p = plan_name.lower()
    if any(x in p for x in ["tablet", "watch", "hotspot", "ipad"]):
        return "tablet"
    return "phone"


def _months_between(start, end):
    d = relativedelta(end, start)
    return d.years * 12 + d.months + 1


def _get_rate(comp_type, plan_cat, as_of, rates):
    cands = [
        r for r in rates
        if r["comp_type"] == comp_type
        and (r["plan_category"] is None or r["plan_category"] == plan_cat)
        and date.fromisoformat(r["effective_date"]) <= as_of
    ]
    if not cands:
        return None
    return float(max(cands, key=lambda r: r["effective_date"])["value"])


def _parse_mrc_from_plan(product_desc):
    """Last-resort: pull a plausible MRC from a plan product_desc string."""
    if not product_desc:
        return 0.0
    # Find all $NN amounts; take the first reasonable plan-sized one
    amounts = [float(x) for x in re.findall(r"\$(\d{2,3})(?:\.\d+)?", product_desc)]
    plan_amts = [a for a in amounts if 15 <= a <= 120]
    return plan_amts[0] if plan_amts else 0.0


def run_discrepancy(period):
    client = get_supabase()
    year, month = int(period[:4]), int(period[5:7])
    period_start = date(year, month, 1)
    plabel = _period_label(period)

    # ── Reference rates ──────────────────────────────────────────────
    rates = (client.schema("commcalc").table("comp_rates")
             .select("*").eq("org_id", ORG_ID).execute().data) or []

    # ── MI lines for this period: index by phone_number and device_serial ──
    mi_rows = (client.schema("commcalc").table("raw_mi")
               .select("device_serial,phone_number,customer_plan,base_mrc,"
                       "mi_activation_date,subscriber_status,rep_username")
               .eq("org_id", ORG_ID).eq("period", plabel).execute().data) or []
    mi_by_mdn, mi_by_imei = {}, {}
    for m in mi_rows:
        mdn = (m.get("phone_number") or "").strip()
        imei = (m.get("device_serial") or "").strip()
        if mdn:
            mi_by_mdn[mdn] = m
        if imei:
            mi_by_imei[imei] = m

    # ── Commissionable sales (non-blank contract_type) ───────────────
    sales = (client.schema("commcalc").table("raw_sales")
             .select("serial_1,store,salesperson,contract_type,mdn,product_desc,"
                     "ext_price,trans_date,tender_type")
             .eq("org_id", ORG_ID).eq("period", plabel)
             .neq("contract_type", "").execute().data) or []

    # ── Payments for this period: index by (mdn|imei, comp_type, month) ──
    pays = (client.schema("commcalc").table("raw_payment_detail")
            .select("imei,payment_type,amount,mdn,period_month,period_year")
            .eq("org_id", ORG_ID).eq("period_month", month)
            .eq("period_year", year).execute().data) or []
    pay_mdn, pay_imei = {}, {}
    for p in pays:
        comp, pmonth = parse_payment_type(p.get("payment_type"))
        amt = float(p.get("amount") or 0)
        mdn = (p.get("mdn") or "").strip()
        imei = (p.get("imei") or "").strip()
        if mdn:
            pay_mdn[(mdn, comp, pmonth)] = pay_mdn.get((mdn, comp, pmonth), 0.0) + amt
        if imei:
            pay_imei[(imei, comp, pmonth)] = pay_imei.get((imei, comp, pmonth), 0.0) + amt

    def received_for(mdn, imei, comp, m):
        """MDN-first, then IMEI fallback. Try exact month, then month-agnostic."""
        for key_dict, key in (
            (pay_mdn, (mdn, comp, m)), (pay_imei, (imei, comp, m)),
        ):
            if key in key_dict:
                return key_dict[key]
        # month-agnostic fallback (sum any month for this id+comp)
        total = 0.0
        found = False
        for kd, ident in ((pay_mdn, mdn), (pay_imei, imei)):
            for (kid, kcomp, _km), v in kd.items():
                if kid == ident and kcomp == comp:
                    total += v
                    found = True
            if found:
                return total
        return 0.0

    # Clear previous results
    client.schema("commcalc").table("discrepancy_results")\
        .delete().eq("org_id", ORG_ID).eq("period", period).execute()

    results = []
    total_gap = 0.0

    for s in sales:
        imei = (s.get("serial_1") or "").strip()
        mdn = (s.get("mdn") or "").strip()
        store = s.get("store") or "Unknown"
        act_raw = (s.get("contract_type") or "").lower().strip()
        act_type = ACTIVATION_TYPE_MAP.get(act_raw, "new_act")
        if act_type == "excluded":
            continue

        # Match to MI line for MRC + activation date + plan + active status
        mi = (mi_by_mdn.get(mdn) or mi_by_imei.get(imei))
        if mi:
            mrc = float(mi.get("base_mrc") or 0)
            plan = mi.get("customer_plan") or ""
            act_str = mi.get("mi_activation_date")
            rep = mi.get("rep_username") or s.get("salesperson") or ""
            in_mi = True
        else:
            mrc = _parse_mrc_from_plan(s.get("product_desc"))
            plan = s.get("product_desc") or ""
            act_str = s.get("trans_date")
            rep = s.get("salesperson") or ""
            in_mi = False

        # Activation date -> bounty month
        try:
            act_date = date.fromisoformat(str(act_str)[:10]) if act_str else period_start
        except Exception:
            act_date = period_start
        bmonth = _months_between(act_date, period_start)
        plan_cat = _plan_category(plan)

        def add(comp_type, expected, match_month, status_override=None):
            rec = received_for(mdn, imei, comp_type, match_month)
            gap = round(expected - rec, 2)
            if status_override:
                status = status_override
            elif comp_type in INFORMATIONAL:
                status = "info"
            elif comp_type in LAGGED:
                status = "lagged"
            elif gap > 0.50:
                status = "open"
            else:
                status = "ok"
            results.append({
                "org_id": ORG_ID, "period": period, "imei": imei or "(byod)",
                "mdn": mdn, "store": store, "rep_username": rep,
                "activation_date": act_date.isoformat(), "activation_type": act_raw,
                "device_model": (s.get("product_desc") or "")[:200], "customer_plan": plan[:200],
                "commissionable_mrc": mrc, "bounty_month": match_month or bmonth,
                "comp_type": comp_type, "expected_amount": round(expected, 2),
                "received_amount": round(rec, 2), "gap": gap, "status": status,
            })
            return gap if status == "open" else 0.0

        # Flag sold-but-not-in-MI (and skip MRC bounties we can't size)
        if not in_mi and mrc == 0:
            add("SOLD_NOT_IN_MI", 0.0, bmonth, status_override="open")
            continue

        # ── Same-month bounties ──
        if act_type in ("new_act", "port_in", "byod") and 1 <= bmonth <= 6:
            r = _get_rate("NAB", plan_cat, period_start, rates)
            if r and mrc > 0:
                total_gap += add("NAB", mrc * r, bmonth)
        if 1 <= bmonth <= 6:
            r = _get_rate("SSLB", plan_cat, period_start, rates)
            if r and mrc > 0:
                total_gap += add("SSLB", mrc * r, bmonth)
        if act_type in ("new_act", "port_in", "byod", "aal") and 1 <= bmonth <= 6:
            r = _get_rate("BRB", plan_cat, period_start, rates)
            if r and mrc > 0:
                total_gap += add("BRB", mrc * r, bmonth)
        if act_type == "upgrade" and 1 <= bmonth <= 6:
            r = _get_rate("DUPGB", plan_cat, period_start, rates)
            if r and mrc > 0:
                total_gap += add("DUPGB", mrc * r, bmonth)
        if act_type in ("new_act", "port_in") and 1 <= bmonth <= 6:
            tender = (s.get("tender_type") or "").lower()
            if "devfi" in tender or "financing" in tender:
                r = _get_rate("ISDFB", plan_cat, period_start, rates)
                if r and mrc > 0:
                    total_gap += add("ISDFB", mrc * r, bmonth)

        # ── Lagged: MI / ATUMI (2-month lag, not counted in gap) ──
        for comp_type in ("MI", "ATUMI"):
            lag = LAG_MONTHS.get(comp_type, 2)
            tgt = bmonth - lag
            if tgt < 1:
                continue
            r = _get_rate(comp_type, plan_cat, period_start, rates)
            if r and mrc > 0:
                add(comp_type, mrc * r, tgt)

        # ── SIMCR (info, month 1) ──
        if bmonth == 1:
            r = _get_rate("SIMCR", plan_cat, period_start, rates)
            if r:
                add("SIMCR", r, 1)

    # Dedupe by (imei, mdn, comp_type, bounty_month) - keep largest gap
    seen = {}
    for r in results:
        if r["status"] == "ok":
            continue
        key = (r["imei"], r["mdn"], r["comp_type"], r["bounty_month"])
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
        "commissionable_activations": len(sales),
        "discrepancy_rows_saved": len(rows_to_save),
        "flagged_underpaid": flagged,
        "total_gap_usd": round(total_gap, 2),
    }
