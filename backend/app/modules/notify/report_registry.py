"""Report registry — server twin of each page's ExportPayload.

Each entry's async `build(org_id, filters)` calls the EXISTING route-handler
functions in the asset / commcalc routers (they are plain async functions taking
kwargs and returning dicts/lists) and reshapes the result into a render payload
(see render.py). On-demand and scheduled sends share this code, so output matches
the browser export in frontend/src/lib/export.tsx.
"""
from datetime import date

from app.modules.asset import router as A
from app.modules.commcalc import router as C


# ── helpers ───────────────────────────────────────────────────────────────────
def d10(v):
    return str(v)[:10] if v else ""


def _resolve_period(filters: dict) -> str:
    """Period reports expect a 'Month YYYY' label. Accept a literal, or the
    tokens 'current'/'last' so recurring subscriptions stay meaningful."""
    p = (filters or {}).get("period")
    if p and str(p).lower() not in ("current", "this", "now"):
        if str(p).lower() in ("last", "previous", "prev"):
            t = date.today()
            y, m = (t.year - 1, 12) if t.month == 1 else (t.year, t.month - 1)
            return date(y, m, 1).strftime("%B %Y")
        return str(p)
    return date.today().strftime("%B %Y")


def _dict_rows(d: dict, key_name: str) -> list:
    """Turn {k: {..}} (e.g. by_status) into [{key_name:k, ..}] rows."""
    return [{key_name: k, **(v or {})} for k, v in (d or {}).items()]


def _qs(filters: dict, keys) -> str:
    parts = []
    for k in keys:
        v = (filters or {}).get(k)
        if v not in (None, ""):
            parts.append(f"{k}={v}")
    return ("?" + "&".join(parts)) if parts else ""


# ── reusable column sets ──────────────────────────────────────────────────────
DATE = lambda hdr, key: {"header": hdr, "fn": (lambda r, k=key: d10(r.get(k)))}


# ── builders ──────────────────────────────────────────────────────────────────
async def _asset_ledger(org_id, f):
    s = await A.get_asset_summary(org_id=org_id)
    sheets = []
    if s.get("loaded"):
        sheets.append({"name": "By Status", "rows": _dict_rows(s.get("by_status"), "status"), "columns": [
            {"header": "Status", "key": "status"},
            {"header": "Devices", "key": "count", "align": "right"},
            {"header": "Open Balance", "key": "owed", "money": True},
            {"header": "Reimbursed", "key": "reimbursed", "money": True},
            {"header": "Fees", "key": "fees", "money": True},
        ]})
        sheets.append({"name": "By Category", "rows": _dict_rows(s.get("by_category"), "category"), "columns": [
            {"header": "Category", "key": "category"},
            {"header": "Devices", "key": "count", "align": "right"},
            {"header": "Open Balance", "key": "owed", "money": True},
            {"header": "Fees", "key": "fees", "money": True},
        ]})
    return {"title": "Asset Ledger", "subtitle": "Open balances & fees by status / category",
            "filename": "asset-ledger", "sheets": sheets}


async def _inventory_aging(org_id, f):
    data = await A.get_aging(org_id=org_id, store=f.get("store", "") or "", market=f.get("market", "") or "",
                             month=f.get("month"), year=f.get("year"))
    cols = [
        {"header": "Store", "key": "store"},
        {"header": "Market", "key": "market"},
        {"header": "Device", "key": "device_model"},
        {"header": "IMEI/ESN", "key": "esn_imei"},
        DATE("Acquired", "acquired_date"),
        {"header": "Days Aged", "key": "days_aged", "align": "right"},
        DATE("Due Date", "due_date"),
        {"header": "Owed", "key": "owed_to_vip", "money": True},
        {"header": "Selling Price", "key": "selling_price", "money": True},
    ]
    b = data.get("buckets") or {}
    sheets = [
        {"name": "45-60 Day Warning", "rows": (b.get("warn") or {}).get("rows") or [], "columns": cols},
        {"name": "Over 60 (Missed)", "rows": (b.get("missed") or {}).get("rows") or [], "columns": cols},
        {"name": "Under 45 Days", "rows": (b.get("under45") or {}).get("rows") or [], "columns": cols},
    ]
    return {"title": "Inventory Aging", "subtitle": f"As of {data.get('today', '')}",
            "filename": "inventory-aging", "sheets": sheets}


async def _rma(org_id, f):
    data = await A.get_rma(org_id=org_id, store=f.get("store", "") or "", market=f.get("market", "") or "",
                           month=f.get("month"), year=f.get("year"))
    cols = [
        {"header": "Store", "key": "store"},
        {"header": "Market", "key": "market"},
        {"header": "Device", "key": "device_model"},
        {"header": "IMEI/ESN", "key": "esn_imei"},
        DATE("Sold", "date_sold"),
        {"header": "Owed", "key": "owed_to_vip", "money": True},
        {"header": "Reimbursed", "key": "reimbursement", "money": True},
        {"header": "Selling Price", "key": "selling_price", "money": True},
        DATE("Reimb Date", "reimbursement_date"),
        {"header": "Shortfall", "key": "_shortfall", "money": True},
    ]
    b = data.get("buckets") or {}
    sheets = [
        {"name": "Not Reimbursed", "rows": (b.get("none") or {}).get("rows") or [], "columns": cols},
        {"name": "Reimbursed Short", "rows": (b.get("short") or {}).get("rows") or [], "columns": cols},
        {"name": "Reimbursed Full", "rows": (b.get("full") or {}).get("rows") or [], "columns": cols},
    ]
    return {"title": "RMA Reconciliation", "subtitle": f"Net loss ${data.get('net_loss', 0):,.2f}",
            "filename": "rma-reconciliation", "sheets": sheets}


async def _owed_weekly(org_id, f):
    thursday = f.get("thursday")
    if not thursday:
        raise ValueError("owed_weekly requires a 'thursday' (billing Friday, YYYY-MM-DD) filter")
    data = await A.get_owed_weekly(thursday=thursday, org_id=org_id,
                                   store=f.get("store", "") or "", market=f.get("market", "") or "")
    sheets = [
        {"name": "By Store", "rows": data.get("by_store") or [], "columns": [
            {"header": "Store", "key": "store"},
            {"header": "Market", "key": "market"},
            {"header": "Sold #", "key": "sold_count", "align": "right"},
            {"header": "Sold Owed", "key": "sold_owed", "money": True},
            {"header": "Aged #", "key": "aging_count", "align": "right"},
            {"header": "Aged Owed", "key": "aging_owed", "money": True},
            {"header": "Total Owed", "key": "total_owed", "money": True},
        ]},
        {"name": "Devices", "rows": data.get("rows") or [], "columns": [
            {"header": "Store", "key": "store"},
            {"header": "Device", "key": "device_model"},
            {"header": "IMEI/ESN", "key": "esn_imei"},
            {"header": "Phone", "key": "phone_number"},
            {"header": "Contract", "key": "contract_type"},
            {"header": "Path", "key": "bill_path"},
            DATE("Sold", "date_sold"),
            DATE("Due", "due_date"),
            {"header": "Owed", "key": "owed_to_vip", "money": True},
        ]},
    ]
    return {"title": "Weekly Owed-to-VIP", "subtitle": f"Billing Friday {thursday}",
            "filename": f"owed-weekly-{thursday}", "sheets": sheets}


def _charges_builder(group_slug, label):
    async def build(org_id, f):
        common = dict(org_id=org_id, store=f.get("store", "") or "", market=f.get("market", "") or "",
                      month=f.get("month"), year=f.get("year"), week_friday=f.get("week_friday", "") or "")
        rows_data = await A.get_charge_rows(group=group_slug, **common)
        summ = await A.get_charges_summary(**common)
        g = (summ.get("groups") or {}).get(group_slug) or {}
        sheets = [
            {"name": "Line Items", "rows": rows_data.get("rows") or [], "columns": [
                {"header": "Store", "key": "store"},
                {"header": "Market", "key": "market"},
                {"header": "Category", "key": "category"},
                {"header": "Device", "key": "device_model"},
                {"header": "IMEI/ESN", "key": "esn_imei"},
                {"header": "Phone", "key": "phone_number"},
                DATE("Date", "period_date"),
                {"header": "Owed", "key": "owed_to_vip", "money": True},
                {"header": "Selling Price", "key": "selling_price", "money": True},
            ]},
            {"name": "By Store", "rows": g.get("by_store") or [], "columns": [
                {"header": "Store", "key": "store"},
                {"header": "Market", "key": "market"},
                {"header": "Items", "key": "count", "align": "right"},
                {"header": "Owed", "key": "owed", "money": True},
            ]},
            {"name": "By Category", "rows": g.get("by_category") or [], "columns": [
                {"header": "Category", "key": "category"},
                {"header": "Items", "key": "count", "align": "right"},
                {"header": "Owed", "key": "owed", "money": True},
            ]},
        ]
        return {"title": label, "subtitle": f"Total owed ${g.get('owed', 0):,.2f}",
                "filename": group_slug, "sheets": sheets}
    return build


async def _charges_dashboard(org_id, f):
    summ = await A.get_charges_summary(
        org_id=org_id, store=f.get("store", "") or "", market=f.get("market", "") or "",
        month=f.get("month"), year=f.get("year"), week_friday=f.get("week_friday", "") or "")
    groups = (summ.get("groups") or {})
    rows = [{"group": g.get("label"), "count": g.get("count"), "owed": g.get("owed")}
            for g in groups.values()]
    tl = summ.get("total_loss") or {}
    rows.append({"group": "— TOTAL LOSS (appeals + RMA) —", "count": "", "owed": tl.get("total", 0)})
    return {"title": "Charges Dashboard", "subtitle": "Charge groups + total loss",
            "filename": "charges-dashboard", "sheets": [
                {"name": "Charge Groups", "rows": rows, "columns": [
                    {"header": "Charge Group", "key": "group"},
                    {"header": "Items", "key": "count", "align": "right"},
                    {"header": "Owed", "key": "owed", "money": True},
                ]}]}


async def _vip_invoices(org_id, f):
    period = f.get("period", "") or ""
    location = f.get("location", "") or ""
    status = f.get("status", "") or ""
    summary = await C.vip_summary(org_id=org_id, period=period, location=location, status=status)
    invoices = await C.vip_invoices_list(org_id=org_id, period=period, location=location, status=status)
    money_store = lambda hdr, key: {"header": hdr, "key": key, "money": True}
    sheets = [
        {"name": "Invoices", "rows": invoices or [], "columns": [
            {"header": "Invoice #", "key": "invoice_number"},
            DATE("Date", "created_on"),
            {"header": "Store", "key": "location"},
            {"header": "Status", "key": "status"},
            money_store("Subtotal", "sub_total"),
            money_store("Shipping", "shipping"),
            money_store("Discount", "discount"),
            money_store("Other Cost", "other_cost"),
            money_store("Other Deductions", "other_deductions"),
            money_store("Tax", "tax"),
            money_store("Grand Total", "grand_total"),
        ]},
        {"name": "Fees by Store", "rows": (summary.get("by_store") or []), "columns": [
            {"header": "Store", "key": "location"},
            {"header": "Invoices", "key": "invoices", "align": "right"},
            money_store("Shipping", "shipping"),
            money_store("Discount", "discount"),
            money_store("Other Cost", "other_cost"),
            money_store("Other Deductions", "other_deductions"),
            money_store("Tax", "tax"),
            money_store("Grand Total", "grand_total"),
        ]},
    ]
    return {"title": "VIP Invoices", "subtitle": period or "All periods",
            "filename": "vip-invoices", "sheets": sheets}


async def _flags(org_id, f):
    period = _resolve_period(f)
    rows = await C.get_flags(period=period, org_id=org_id)
    return {"title": "Flags", "subtitle": period, "filename": f"flags-{period.replace(' ', '-')}",
            "sheets": [{"name": "Flags", "rows": rows or [], "columns": [
                {"header": "Flag Type", "key": "flag_type"},
                {"header": "Severity", "key": "severity"},
                {"header": "Store", "key": "store_address"},
                {"header": "Rep", "key": "epay_salesperson"},
                {"header": "MDN", "key": "mdn"},
                {"header": "IMEI", "key": "imei"},
                {"header": "Amount", "key": "amount", "money": True},
                {"header": "Description", "key": "description"},
            ]}]}


async def _commissions(org_id, f):
    period = _resolve_period(f)
    rows = await C.get_commissions(period=period, org_id=org_id)
    return {"title": "Commissions", "subtitle": period, "filename": f"commissions-{period.replace(' ', '-')}",
            "sheets": [{"name": "Rep Payouts", "rows": rows or [], "columns": [
                {"header": "Rep", "key": "epay_salesperson"},
                {"header": "Name", "key": "storeops_name"},
                {"header": "Store", "key": "store"},
                {"header": "Tier", "key": "tier", "align": "right"},
                {"header": "Premium", "key": "premium_acts", "align": "right"},
                {"header": "BYOD", "key": "byod_acts", "align": "right"},
                {"header": "Upgrade", "key": "upgrade_acts", "align": "right"},
                {"header": "Subtotal", "key": "subtotal", "money": True},
                {"header": "Total Payout", "key": "total_payout", "money": True},
                {"header": "Chargeback", "key": "chargeback_deduction", "money": True},
                {"header": "Final Payout", "key": "final_payout", "money": True},
            ]}]}


async def _gp(org_id, f):
    period = _resolve_period(f)
    data = await C.get_gp_report(period=period, view="store", market=f.get("market", "") or "", org_id=org_id)
    return {"title": "Gross Profit", "subtitle": period, "filename": f"gp-{period.replace(' ', '-')}",
            "sheets": [{"name": "By Store", "rows": data.get("store_rows") or [], "columns": [
                {"header": "Store", "key": "store"},
                {"header": "Market", "key": "market"},
                {"header": "Total Rev", "key": "total_rev", "money": True},
                {"header": "Comm", "key": "comm", "money": True},
                {"header": "Reimb", "key": "reimb", "money": True},
                {"header": "MDF", "key": "mdf", "money": True},
                {"header": "MI", "key": "mi", "money": True},
                {"header": "ATU", "key": "atu", "money": True},
                {"header": "Rep Pay", "key": "rep_pay", "money": True},
                {"header": "Expenses", "key": "exp_total", "money": True},
                {"header": "Net Profit", "key": "net_profit", "money": True},
                {"header": "Net excl MDF", "key": "net_excl_mdf", "money": True},
            ]}]}


async def _discrepancy(org_id, f):
    period = _resolve_period(f)
    data = await C.get_discrepancy_results(period=period, org_id=org_id)
    summary = data.get("summary") or []
    line_items = []
    for s in summary:
        line_items.extend(s.get("rows") or [])
    sheets = [
        {"name": "By Store", "rows": summary, "columns": [
            {"header": "Store", "key": "store"},
            {"header": "Flagged", "key": "flagged_count", "align": "right"},
            {"header": "Total Gap", "key": "total_gap", "money": True},
        ]},
        {"name": "Line Items", "rows": line_items, "columns": [
            {"header": "Store", "key": "store"},
            {"header": "Rep", "key": "rep_username"},
            {"header": "Comp Type", "key": "comp_type"},
            {"header": "Device", "key": "device_model"},
            {"header": "Plan", "key": "customer_plan"},
            {"header": "MDN", "key": "mdn"},
            {"header": "IMEI", "key": "imei"},
            {"header": "Expected", "key": "expected_amount", "money": True},
            {"header": "Received", "key": "received_amount", "money": True},
            {"header": "Gap", "key": "gap", "money": True},
            {"header": "Status", "key": "status"},
        ]},
    ]
    return {"title": "Pay Discrepancy", "subtitle": f"{period} — total gap ${data.get('total_gap_usd', 0):,.2f}",
            "filename": f"discrepancy-{period.replace(' ', '-')}", "sheets": sheets}


async def _phantom(org_id, f):
    period = _resolve_period(f)
    data = await C.get_phantom_payments(period=period, org_id=org_id)
    by_store = data.get("by_store") or []
    payments = []
    for s in by_store:
        payments.extend(s.get("rows") or [])
    sheets = [
        {"name": "By Store", "rows": by_store, "columns": [
            {"header": "Store", "key": "business_address"},
            {"header": "Count", "key": "count", "align": "right"},
            {"header": "Total", "key": "total", "money": True},
        ]},
        {"name": "Payments", "rows": payments, "columns": [
            {"header": "Store", "key": "business_address"},
            {"header": "Payment Type", "key": "payment_type"},
            {"header": "IMEI", "key": "imei"},
            {"header": "MDN", "key": "mdn"},
            DATE("Date", "payment_date"),
            {"header": "Amount", "key": "amount", "money": True},
        ]},
    ]
    return {"title": "Phantom Payments", "subtitle": f"{period} — ${data.get('phantom_total', 0):,.2f} unmatched",
            "filename": f"phantom-{period.replace(' ', '-')}", "sheets": sheets}


async def _top_sellers(org_id, f):
    period = _resolve_period(f)
    data = await C.get_top_sellers(period=period, limit=int(f.get("limit") or 25), org_id=org_id)
    return {"title": "Top Sellers", "subtitle": period, "filename": f"top-sellers-{period.replace(' ', '-')}",
            "sheets": [{"name": "Top Sellers", "rows": data.get("top_sellers") or [], "columns": [
                {"header": "Model", "key": "model"},
                {"header": "Units", "key": "units", "align": "right"},
                {"header": "Sample Desc", "key": "sample_desc"},
            ]}]}


# ── registry ──────────────────────────────────────────────────────────────────
REPORTS = {
    "asset_ledger": {
        "label": "Asset Ledger", "filters": [],
        "live_path": lambda f: "/commcalc/asset", "build": _asset_ledger},
    "inventory_aging": {
        "label": "Inventory Aging", "filters": ["store", "market", "month", "year"],
        "live_path": lambda f: "/commcalc/asset/aging" + _qs(f, ["store", "market", "month", "year"]),
        "build": _inventory_aging},
    "rma": {
        "label": "RMA Reconciliation", "filters": ["store", "market", "month", "year"],
        "live_path": lambda f: "/commcalc/asset/charges/rma" + _qs(f, ["store", "market", "month", "year"]),
        "build": _rma},
    "owed_weekly": {
        "label": "Weekly Owed-to-VIP", "filters": ["thursday", "store", "market"],
        "live_path": lambda f: "/commcalc/asset/owed-weekly" + _qs(f, ["thursday", "store", "market"]),
        "build": _owed_weekly},
    "charges_appeals": {
        "label": "Charges — Appeals & Denied", "filters": ["store", "market", "month", "year", "week_friday"],
        "live_path": lambda f: "/commcalc/asset/charges/appeals" + _qs(f, ["store", "market", "month", "year"]),
        "build": _charges_builder("appeals", "Charges — Appeals & Denied Payments")},
    "charges_vip_fees": {
        "label": "Charges — VIP Fees", "filters": ["store", "market", "month", "year", "week_friday"],
        "live_path": lambda f: "/commcalc/asset/charges/vip_fees" + _qs(f, ["store", "market", "month", "year"]),
        "build": _charges_builder("vip_fees", "Charges — VIP Fees")},
    "charges_stock_balance": {
        "label": "Charges — Stock Balancing", "filters": ["store", "market", "month", "year", "week_friday"],
        "live_path": lambda f: "/commcalc/asset/charges/stock_balance" + _qs(f, ["store", "market", "month", "year"]),
        "build": _charges_builder("stock_balance", "Charges — Stock Balancing / Returns")},
    "charges_recon": {
        "label": "Charges — Reconciliation Oddities", "filters": ["store", "market", "month", "year", "week_friday"],
        "live_path": lambda f: "/commcalc/asset/charges/recon_oddity" + _qs(f, ["store", "market", "month", "year"]),
        "build": _charges_builder("recon_oddity", "Charges — Reconciliation Oddities")},
    "charges_dashboard": {
        "label": "Charges Dashboard", "filters": ["store", "market", "month", "year", "week_friday"],
        "live_path": lambda f: "/commcalc/asset/dashboard" + _qs(f, ["store", "market", "month", "year"]),
        "build": _charges_dashboard},
    "vip_invoices": {
        "label": "VIP Invoices", "filters": ["period", "location", "status"],
        "live_path": lambda f: "/commcalc/vip" + _qs(f, ["period", "location", "status"]),
        "build": _vip_invoices},
    "flags": {
        "label": "Flags", "filters": ["period"],
        "live_path": lambda f: "/commcalc/flags", "build": _flags},
    "commissions": {
        "label": "Commissions", "filters": ["period"],
        "live_path": lambda f: "/commcalc/reports", "build": _commissions},
    "gp": {
        "label": "Gross Profit", "filters": ["period", "market"],
        "live_path": lambda f: "/commcalc/gp", "build": _gp},
    "discrepancy": {
        "label": "Pay Discrepancy", "filters": ["period"],
        "live_path": lambda f: "/commcalc/discrepancy", "build": _discrepancy},
    "phantom": {
        "label": "Phantom Payments", "filters": ["period"],
        "live_path": lambda f: "/commcalc/discrepancy", "build": _phantom},
    "top_sellers": {
        "label": "Top Sellers", "filters": ["period", "limit"],
        "live_path": lambda f: "/commcalc/kpi", "build": _top_sellers},
}


def list_reports():
    return [{"key": k, "label": v["label"], "filters": v["filters"]} for k, v in REPORTS.items()]


async def build_payload(report_key: str, org_id: str, filters: dict) -> dict:
    spec = REPORTS.get(report_key)
    if not spec:
        raise KeyError(f"unknown report '{report_key}'")
    payload = await spec["build"](org_id, filters or {})
    payload["live_path"] = spec["live_path"](filters or {})
    return payload
