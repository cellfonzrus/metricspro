"""Chart of Accounts — deterministic aggregation (the authoritative numbers).

Maps the app's existing money data into the confirmed chart of accounts
(CHART_OF_ACCOUNTS.md, cash basis, multi-company) and returns per-line totals,
broken out by store where the source carries a store key so the P&L / Balance
Sheet can be scoped consolidated / per-company / per-store.

Sources (columns verified against the live backend):
  • raw_mi              actual_mi_payout, actual_atu_payout            (company-wide)
  • raw_comp_report     business_address, compensation_type, payment_amount
  • raw_sales           department, ext_price, gp, voided, store
  • asset_ledger        owed_to_vip, reimbursement, reimbursement_date, selling_price,
                        status (== "On Inventory" ⇒ unsold), category, store, acquired_date
  • rep_commissions     total_payout, store
  • chargeback_items    amount, deduct, store
  • vip_invoices        grand_total, shipping, other_cost, status, location
  • vip_paygo_payments  amount, batch_type (pending|approved), dealer, period
  • store_expenses      amount, expense_name, expense_type, store_code  (store_code → store_address)
  • journal_entries     MANUAL P&L + Balance-Sheet lines
  • store_companies     store_address → company_id (Default Company otherwise)

Lines whose source has no usable store key (MI/ATU residual, carrier comp w/o a
matching store) are "company-wide": they appear in the CONSOLIDATED view only and
read 0 (with a note) under a company/store filter — honest beats mis-attributed.
"""
from app.modules.commcalc.calculator import safe_float

ORG_ID = "00000000-0000-0000-0000-000000000001"

DEVICE_DEPTS = {"Android - XP", "IPHONE - XP", "TABLET - XP"}
ACCESSORY_DEPT = "Ondigo"
VIP_FEE_CATS = {"PROCESSING FEE", "SHIPPING", "SIM KIT"}

# ── chart-of-accounts line specs ───────────────────────────────────────────────────────────
# section ∈ revenue|cogs|opex|other (P&L) and asset|liability|equity (Balance Sheet)
# kind: "auto" (derived here), "auto*" (derived once a dependency ships; degrades to 0),
#       "manual" (entered via journal_entries), "computed" (derived from other lines)
PL_SPEC = [
    ("carrier_comm",  "Carrier commissions & incentives",            "revenue", "auto",  "company"),
    ("mi_income",     "MI residual income",                          "revenue", "auto",  "company"),
    ("atu_income",    "ATU income",                                  "revenue", "auto",  "company"),
    ("accessory_rev", "Accessory sales revenue",                     "revenue", "auto",  "store"),
    ("device_rev",    "Device sales revenue",                        "revenue", "auto",  "store"),
    ("vip_reimb",     "Device-financing reimbursements (VIP)",       "revenue", "auto",  "store"),
    ("vip_device_pay","VIP device payments (PayGo, paid)",           "cogs",    "auto",  "store"),
    ("accessory_cost","Accessory cost",                              "cogs",    "auto",  "store"),
    ("device_cost",   "Device cost",                                 "cogs",    "auto",  "store"),
    ("vip_fees",      "VIP fees paid (shipping / SIM kit / processing)", "cogs", "auto", "store"),
    ("rep_comm",      "Rep commissions paid",                        "opex",    "auto",  "store"),
    ("wages",         "Wages / hourly payroll",                      "opex",    "manual","store"),
    ("chargebacks",   "Chargebacks / clawbacks",                     "opex",    "auto",  "store"),
    ("store_opex",    "Store operating expenses (rent / utilities / supplies)", "opex", "auto", "store"),
]
BS_SPEC = [
    ("cash",            "Cash / bank",                               "asset",     "manual",  None),
    ("inventory",       "Inventory — on-hand device value",          "asset",     "auto",    "store"),
    ("inter_store_recv","Inter-store receivable (lent to stores)",   "asset",     "auto*",   "store"),
    ("fixtures",        "Fixtures / equipment",                      "asset",     "manual",  None),
    ("owed_vip",        "Owed to VIP (device financing)",            "liability", "auto",    "store"),
    ("vip_ap",          "Accounts payable — VIP invoices unpaid",    "liability", "auto",    "store"),
    ("inter_store_pay", "Inter-store payable (borrowed)",            "liability", "auto*",   "store"),
    ("chargeback_res",  "Chargeback reserve",                        "liability", "auto",    "store"),
    ("owner_capital",   "Owner capital / contributions",             "equity",    "manual",  None),
    ("retained",        "Retained earnings (accumulated net income)","equity",    "computed",None),
]
PL_LABEL = {k: lbl for k, lbl, *_ in PL_SPEC}
BS_LABEL = {k: lbl for k, lbl, *_ in BS_SPEC}


def _norm_store(s):
    return (str(s or "").strip()) or None


def parse_period(period: str):
    """'June 2026' -> (6, 2026). Also accepts 'YYYY-MM'."""
    months = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
              "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}
    p = (period or "").strip().lower()
    if "-" in p and p.split("-")[0].isdigit():
        y, m = p.split("-")[:2]
        return int(m), int(y)
    parts = p.split()
    mo = months.get(parts[0], 0) if parts else 0
    yr = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return mo, yr


def _in_period(date_str, pm, py):
    """date_str like 'YYYY-MM-DD...' (or 'MM/DD/YYYY') falls in period (pm,py)?"""
    s = str(date_str or "").strip()
    if not s:
        return False
    try:
        if "-" in s[:10] and s[:4].isdigit():
            y, m = int(s[:4]), int(s[5:7])
        elif "/" in s:
            a, b, c = (s[:10].split("/") + ["", "", ""])[:3]
            m, y = int(a), int(c[:4]) if c[:4].isdigit() else 0
        else:
            return False
        return m == pm and y == py
    except Exception:
        return False


def _fetch_all(client, table, select, eqs=None, page=1000, cap=200000):
    """Paginated select of an entire (org-scoped) table — supabase caps a query at 1000 rows."""
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


def store_company_map(client, org_id):
    """store_address (normalized) -> company_id, plus a default-company id."""
    companies = (client.schema("commcalc").table("companies").select("id,name")
                 .eq("org_id", org_id).execute().data) or []
    default_id = next((c["id"] for c in companies if c["name"] == "Default Company"),
                      (companies[0]["id"] if companies else None))
    mp = {}
    for r in (_fetch_all(client, "store_companies", "store_address,company_id", {"org_id": org_id})):
        sa = _norm_store(r.get("store_address"))
        if sa:
            mp[sa.upper()] = r.get("company_id")
    return mp, default_id, companies


def store_code_to_address(client, org_id):
    """store_expenses keys by store_code; map it to the canonical store_address."""
    out = {}
    for r in (_fetch_all(client, "store_mapping", "store_code,store_address", {"org_id": org_id})):
        c, a = _norm_store(r.get("store_code")), _norm_store(r.get("store_address"))
        if c and a:
            out[c] = a
    return out


# ── per-line aggregation: each store-keyed line → {store_address: amount}; company-wide → scalar
def build_inputs(client, org_id, period):
    """Aggregate every chart-of-accounts line for `period`. Returns a dict:
       { line_key: {"by_store": {store: amt}, "company_wide": amt, "detail": {label: amt}} }
       'detail' carries drill-down sub-lines (e.g. each expense name)."""
    pm, py = parse_period(period)
    code2addr = store_code_to_address(client, org_id)

    L = {k: {"by_store": {}, "company_wide": 0.0, "detail": {}} for k, *_ in PL_SPEC + BS_SPEC}

    def add(key, store, amt, detail_label=None):
        amt = round(safe_float(amt), 2)
        if not amt:
            return
        if store:
            s = _norm_store(store)
            L[key]["by_store"][s] = round(L[key]["by_store"].get(s, 0.0) + amt, 2)
        else:
            L[key]["company_wide"] = round(L[key]["company_wide"] + amt, 2)
        if detail_label:
            L[key]["detail"][detail_label] = round(L[key]["detail"].get(detail_label, 0.0) + amt, 2)

    # raw_mi — MI + ATU residual (company-wide)
    try:
        for r in _fetch_all(client, "raw_mi", "actual_mi_payout,actual_atu_payout",
                            {"org_id": org_id, "period": period}):
            add("mi_income", None, r.get("actual_mi_payout"))
            add("atu_income", None, r.get("actual_atu_payout"))
    except Exception:
        pass

    # raw_comp_report — carrier commissions/incentives (store via business_address if it matches)
    try:
        for r in _fetch_all(client, "raw_comp_report",
                            "business_address,payment_amount,period", {"org_id": org_id, "period": period}):
            add("carrier_comm", _norm_store(r.get("business_address")), r.get("payment_amount"))
    except Exception:
        pass

    # raw_sales — accessory/device revenue + cost (store)
    try:
        for r in _fetch_all(client, "raw_sales", "department,ext_price,gp,voided,store",
                            {"org_id": org_id, "period": period}):
            if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
                continue
            dept = (r.get("department") or "").strip()
            ext, gp = safe_float(r.get("ext_price")), safe_float(r.get("gp"))
            st = _norm_store(r.get("store"))
            if dept == ACCESSORY_DEPT:
                add("accessory_rev", st, ext)
                add("accessory_cost", st, ext - gp)
            elif dept in DEVICE_DEPTS:
                add("device_rev", st, ext)
                add("device_cost", st, ext - gp)
    except Exception:
        pass

    # asset_ledger — reimbursement income (cash, by reimbursement_date), VIP fees (COGS),
    # inventory value (BS), owed-to-VIP (BS). One scan, multiple lines.
    try:
        for r in _fetch_all(client, "asset_ledger",
                            "store,category,status,owed_to_vip,reimbursement,reimbursement_date,selling_price"):
            st = _norm_store(r.get("store"))
            cat = (r.get("category") or "").strip().upper()
            status = (r.get("status") or "").strip()
            owed = safe_float(r.get("owed_to_vip"))
            reimb = safe_float(r.get("reimbursement"))
            unsold = status.lower() == "on inventory"
            # reimbursement income — recognized in the period it was received
            if reimb and _in_period(r.get("reimbursement_date"), pm, py):
                add("vip_reimb", st, reimb)
            # VIP fee categories — booked as paid in the period (best-effort: count all on the books)
            if cat in VIP_FEE_CATS:
                add("vip_fees", st, owed, detail_label=cat.title())
            # Balance Sheet (point-in-time, current): unsold on-hand value + outstanding payable
            if unsold:
                add("inventory", st, r.get("selling_price"))
                add("owed_vip", st, owed)
    except Exception:
        pass

    # vip_invoices — VIP fees paid (shipping + other_cost) + unpaid AP (BS)
    try:
        for r in _fetch_all(client, "vip_invoices", "location,shipping,other_cost,grand_total,status,period",
                            {"org_id": org_id, "period": period}):
            st = _norm_store(r.get("location"))
            add("vip_fees", st, safe_float(r.get("shipping")) + safe_float(r.get("other_cost")),
                detail_label="Invoice shipping/other")
        for r in _fetch_all(client, "vip_invoices", "location,grand_total,status"):
            status = (r.get("status") or "").strip().lower()
            if status not in ("paid in full", "voided", "paid", "void"):
                add("vip_ap", _norm_store(r.get("location")), r.get("grand_total"))
    except Exception:
        pass

    # vip_paygo_payments — cash paid to VIP this period (approved batches) + current owed (pending, BS)
    try:
        for r in _fetch_all(client, "vip_paygo_payments", "dealer,amount,amount_overdue,batch_type,period",
                            {"org_id": org_id, "period": period}):
            if (r.get("batch_type") or "").lower() == "approved":
                add("vip_device_pay", _norm_store(r.get("dealer")), r.get("amount"))
        for r in _fetch_all(client, "vip_paygo_payments", "dealer,amount,batch_type"):
            if (r.get("batch_type") or "").lower() == "pending":
                add("owed_vip", _norm_store(r.get("dealer")), r.get("amount"))
    except Exception:
        pass

    # rep_commissions — rep commissions paid (opex)
    try:
        for r in _fetch_all(client, "rep_commissions", "store,total_payout,period",
                            {"org_id": org_id, "period": period}):
            add("rep_comm", _norm_store(r.get("store")), r.get("total_payout"))
    except Exception:
        pass

    # chargeback_items — clawbacks (opex) + reserve (BS, expected/undeducted)
    try:
        for r in _fetch_all(client, "chargeback_items", "store,amount,deduct,decided_at,period",
                            {"org_id": org_id, "period": period}):
            st = _norm_store(r.get("store"))
            amt = safe_float(r.get("amount"))
            if r.get("deduct"):
                add("chargebacks", st, amt)
            if not r.get("decided_at"):
                add("chargeback_res", st, amt)
    except Exception:
        pass

    # store_expenses — operating expenses (opex), drill by expense_name; key by store_code→address
    try:
        for r in _fetch_all(client, "store_expenses", "store_code,expense_name,expense_type,amount,period",
                            {"org_id": org_id, "period": period}):
            sa = code2addr.get(_norm_store(r.get("store_code")), _norm_store(r.get("store_code")))
            label = (r.get("expense_name") or "Expense").strip()
            add("store_opex", sa, r.get("amount"), detail_label=label)
    except Exception:
        pass

    # #6 inter-store borrowings (auto*, migration 018) — receivable/payable. Degrade to 0 if absent.
    try:
        bor = _fetch_all(client, "store_borrowings",
                         "from_store,to_store,amount,repaid", {"org_id": org_id})
        for r in bor:
            amt = safe_float(r.get("amount")) - safe_float(r.get("repaid"))
            if amt <= 0:
                continue
            add("inter_store_recv", _norm_store(r.get("to_store")), amt)     # lender is owed
            add("inter_store_pay", _norm_store(r.get("from_store")), amt)    # borrower owes
    except Exception:
        pass

    return L
