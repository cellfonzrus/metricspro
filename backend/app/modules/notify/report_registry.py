"""Report registry — server twin of each page's ExportPayload.

Each entry's async `build(org_id, filters)` calls the EXISTING route-handler
functions in the asset / commcalc routers (they are plain async functions taking
kwargs and returning dicts/lists) and reshapes the result into a render payload
(see render.py). On-demand and scheduled sends share this code, so output matches
the browser export in frontend/src/lib/export.tsx.

TWO OPT-IN EXTRAS a builder may declare on its registry entry (default off, so the other
builders keep the plain `build(org_id, filters)` shape):

  "wants_auth": True  → build(org_id, filters, authorization=<the CALLER's Authorization header>)
        These route-handlers are `async def h(..., authorization: str = Header(default=""))`.
        Called in-process WITHOUT that kwarg, Python binds the FastAPI `Header` SENTINEL OBJECT
        (not a str) and the handler's `caller_scope(authorization, ...)` blew up with
        `AttributeError: 'Header' object has no attribute 'lower'` deep in core's `_uid_from_token`
        — a hard 500 on POST /notify/send for every report that scopes by caller (2026-07-17
        failure_log rows). Passing the real header also satisfies AGENT_CONTRACT §3c: an export
        respects the caller's permission gates. A scheduled run has no caller → "" → the handler's
        own "no token = org-wide" path, i.e. exactly what an admin-configured subscription means.

  "wants_tz": True    → build(org_id, filters, tz=<the subscription's timezone>)
        Relative date filters ("this week's billing Friday") must resolve against the TENANT's
        business day, not the server's UTC day, or a schedule that fires late in the evening
        local time lands on the next day's date.
"""
from datetime import date

from app.modules.asset import router as A
from app.modules.commcalc import router as C
from app.modules.account import router as AC
# Pure filter resolution lives in a LEAF module (no cross-module imports) so the attention provider
# can validate a saved schedule without importing these routers. Re-exported here because this is
# the module every caller already talks to.
from .report_filters import (ReportConfigError, business_today as _business_today,      # noqa: F401
                             resolve_billing_friday as _resolve_billing_friday,
                             validate_filters as _validate_filters)
# Payroll & Workforce entries (W3) live in their own module — same entry shape, spliced into
# REPORTS below. That module keeps every app import LAZY (its builders call the storeops handlers
# in-process at build time), so importing it here adds no import-time weight and it stays provable
# offline (harness_workforce_report_registry.py).
from .workforce_reports import WORKFORCE_REPORTS


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


async def _owed_weekly(org_id, f, tz=""):
    # A blank / relative `thursday` resolves to the CURRENT billing Friday (same default the
    # owed-weekly page opens on) instead of raising — a recurring subscription cannot carry a
    # correct fixed date. asset's Friday billing trigger is untouched: we only pick the date.
    thursday = _resolve_billing_friday(f, tz)
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
    return {"title": "Weekly Owed-to-Distributor", "subtitle": f"Billing Friday {thursday}",
            "filename": f"owed-weekly-{thursday}", "sheets": sheets,
            # RESOLVED filters → the "View live report" link opens the same week the file covers
            # (a blank/relative `thursday` would otherwise link to the page's own default).
            "live_filters": {**(f or {}), "thursday": thursday}}


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
    return {"title": "Distributor Invoices", "subtitle": period or "All periods",
            "filename": "vip-invoices", "sheets": sheets}


async def _flags(org_id, f, authorization=""):
    period = _resolve_period(f)
    rows = await C.get_flags(period=period, org_id=org_id, authorization=authorization)
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


async def _commissions(org_id, f, authorization=""):
    period = _resolve_period(f)
    rows = await C.get_commissions(period=period, org_id=org_id, authorization=authorization)
    return {"title": "Incentives", "subtitle": period, "filename": f"commissions-{period.replace(' ', '-')}",
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


async def _gp(org_id, f, authorization=""):
    period = _resolve_period(f)
    data = await C.get_gp_report(period=period, view="store", market=f.get("market", "") or "", org_id=org_id,
                                 authorization=authorization)
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


async def _sales_recon(org_id, f):
    from app.modules.commcalc import sales_recon as SR
    period = _resolve_period(f)
    data = SR.run_sales_recon(period)
    s = data["summary"]
    leaks = [r for r in data["rows"] if r["bucket"] in ("missing_in_monthly", "amount_mismatch")]
    sheets = [
        {"name": "Leaks & Mismatches", "rows": leaks, "columns": [
            {"header": "Bucket", "key": "bucket"},
            {"header": "Trans ID", "key": "trans_id"},
            {"header": "Store", "key": "store"},
            {"header": "Rep", "key": "salesperson"},
            DATE("Date", "trans_date"),
            {"header": "Monthly", "key": "monthly_total", "money": True},
            {"header": "Daily", "key": "daily_total", "money": True},
            {"header": "Delta", "key": "delta", "money": True},
        ]},
        {"name": "By Store", "rows": data["by_store"], "columns": [
            {"header": "Store", "key": "store"},
            {"header": "Missing in Monthly", "key": "missing_in_monthly", "align": "right"},
            {"header": "Amount Mismatch", "key": "amount_mismatch", "align": "right"},
            {"header": "Missing in Daily", "key": "missing_in_daily", "align": "right"},
            {"header": "Net Delta", "key": "delta_total", "money": True},
        ]},
    ]
    return {"title": "Sales Feed Recon",
            "subtitle": f"{period} — {s['missing_in_monthly']} leak(s) (${s['missing_in_monthly_total']:,.2f}) · "
                        f"{s['amount_mismatch']} mismatch(es)",
            "filename": f"sales-recon-{period.replace(' ', '-')}", "sheets": sheets}


async def _storeops_schedule(org_id, f):
    """Week schedule from storeops.shifts → emailable/WhatsApp-able report. The server twin of the
    schedule page's export, so 'publish & notify' delivers the same grid the page shows."""
    from datetime import timedelta
    from app.core.database import get_supabase
    ws = (f or {}).get("week_start")
    try:
        start = date.fromisoformat(str(ws)[:10]) if ws else date.today()
    except Exception:
        start = date.today()
    start = start - timedelta(days=start.weekday())   # snap to Monday
    end = start + timedelta(days=6)
    q = (get_supabase().schema("storeops").table("shifts").select("*")
         .eq("org_id", org_id)
         .eq("is_deleted", False)
         .gte("shift_date", start.isoformat()).lte("shift_date", end.isoformat()))
    sc = (f or {}).get("store_code")
    if sc:
        q = q.eq("store_code", sc)
    shifts = q.order("shift_date").execute().data or []

    def _wd(s):
        try:
            return date.fromisoformat(str(s)[:10]).strftime("%a")
        except Exception:
            return ""

    rows, by_emp = [], {}
    for s in shifts:
        st, en = str(s.get("start_time") or "")[:5], str(s.get("end_time") or "")[:5]
        rows.append({"day": _wd(s.get("shift_date")), "date": str(s.get("shift_date") or "")[:10],
                     "store": s.get("store_code") or "", "employee": s.get("employee_name") or "",
                     "shift": f"{st}–{en}".strip("–"), "hours": s.get("scheduled_hours") or 0,
                     "role": s.get("role") or ""})
        k = s.get("employee_name") or "—"
        e = by_emp.setdefault(k, {"employee": k, "shifts": 0, "hours": 0.0})
        e["shifts"] += 1
        try:
            e["hours"] += float(s.get("scheduled_hours") or 0)
        except Exception:
            pass
    by_emp_list = sorted(by_emp.values(), key=lambda e: e["employee"])
    for e in by_emp_list:
        e["hours"] = round(e["hours"], 2)
    total_hours = round(sum(e["hours"] for e in by_emp_list), 2)
    return {"title": "Store Schedule",
            "subtitle": f"Week of {start.isoformat()} – {end.isoformat()} · {len(shifts)} shifts · {total_hours} hrs",
            "filename": f"schedule-{start.isoformat()}",
            "sheets": [
                {"name": "Shifts", "rows": rows, "columns": [
                    {"header": "Day", "key": "day"}, {"header": "Date", "key": "date"},
                    {"header": "Store", "key": "store"}, {"header": "Employee", "key": "employee"},
                    {"header": "Shift", "key": "shift"},
                    {"header": "Hours", "key": "hours", "align": "right"},
                    {"header": "Role", "key": "role"}]},
                {"name": "By Employee", "rows": by_emp_list, "columns": [
                    {"header": "Employee", "key": "employee"},
                    {"header": "Shifts", "key": "shifts", "align": "right"},
                    {"header": "Hours", "key": "hours", "align": "right"}]},
            ]}


async def _top_sellers(org_id, f):
    period = _resolve_period(f)
    data = await C.get_top_sellers(period=period, limit=int(f.get("limit") or 25), org_id=org_id)
    return {"title": "Top Sellers", "subtitle": period, "filename": f"top-sellers-{period.replace(' ', '-')}",
            "sheets": [{"name": "Top Sellers", "rows": data.get("top_sellers") or [], "columns": [
                {"header": "Model", "key": "model"},
                {"header": "Units", "key": "units", "align": "right"},
                {"header": "Sample Desc", "key": "sample_desc"},
            ]}]}


async def _action_plan(org_id, f, authorization=""):
    period = _resolve_period(f)
    data = await C.get_action_plan(period=period, org_id=org_id,
                                   store_code=f.get("store_code", "") or "",
                                   rep=f.get("rep", "") or "",
                                   authorization=authorization)
    rows, metric_rows = [], []
    for s in (data.get("stores") or []):
        label = s.get("address") or s.get("store_code")
        for it in (s.get("items") or []):
            rows.append({"scope": "Store", "store": label, "rep": "",
                         "severity": it["severity"], "focus": it["title"], "detail": it["detail"]})
        for m in (s.get("metrics") or []):
            metric_rows.append({"store": label, "metric": m["label"], "target": m["target"],
                                "achieved": m["achieved"], "need": m["need"], "pace": m["pace"]})
        for rp in (s.get("reps") or []):
            for it in (rp.get("items") or []):
                rows.append({"scope": "Rep", "store": label, "rep": rp.get("rep"),
                             "severity": it["severity"], "focus": it["title"], "detail": it["detail"]})
    summ = data.get("summary") or {}
    return {"title": "Daily Action Plan",
            "subtitle": f"{period} · as of {data.get('today', '')} · "
                        f"{summ.get('critical', 0)} critical / {summ.get('warning', 0)} warning · "
                        f"${summ.get('commission_at_risk', 0):,.0f} commission at risk",
            "filename": f"action-plan-{period.replace(' ', '-')}",
            "sheets": [
                {"name": "Action Items", "rows": rows, "columns": [
                    {"header": "Scope", "key": "scope"},
                    {"header": "Store", "key": "store"},
                    {"header": "Rep", "key": "rep"},
                    {"header": "Severity", "key": "severity"},
                    {"header": "Focus", "key": "focus"},
                    {"header": "Detail", "key": "detail"},
                ]},
                {"name": "Store Metrics", "rows": metric_rows, "columns": [
                    {"header": "Store", "key": "store"},
                    {"header": "Metric", "key": "metric"},
                    {"header": "Target", "key": "target", "align": "right"},
                    {"header": "Achieved", "key": "achieved", "align": "right"},
                    {"header": "Need", "key": "need", "align": "right"},
                    {"header": "Pace/day", "key": "pace", "align": "right"},
                ]},
            ]}


# ── account module: P&L + Balance Sheet (reads persisted snapshots) ────────────
_PL_SEC = {"revenue": "Revenue", "cogs": "Cost of Goods Sold",
           "opex": "Operating Expenses", "other": "Other"}
_BS_SEC = {"asset": "Assets", "liability": "Liabilities", "equity": "Equity"}


def _acct_slug(s: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in str(s or "")).strip("-")
    return out or "scope"


def _stmt_rows(st: dict, sec_labels: dict, subtotal_prefix: str) -> list:
    rows = []
    for s in (st.get("sections") or []):
        title = sec_labels.get(s.get("type"), s.get("type"))
        for ln in (s.get("lines") or []):
            rows.append({"section": title, "line": ln.get("label"), "amount": ln.get("amount")})
        rows.append({"section": title, "line": f"{subtotal_prefix} {title}", "amount": s.get("subtotal")})
    return rows


_ACCT_COLS = [
    {"header": "Section", "key": "section"},
    {"header": "Line", "key": "line"},
    {"header": "Amount", "key": "amount", "money": True},
]


async def _account_pl(org_id, f):
    period = _resolve_period(f)
    scope = f.get("scope", "") or "consolidated"
    data = await AC.get_pl(period=period, scope=scope, org_id=org_id)
    if not data.get("computed"):
        raise ValueError(f"P&L not computed for {period} / {scope} — open /accounts and click "
                         "'Compute statements' first.")
    st = data.get("statement") or {}
    rows = _stmt_rows(st, _PL_SEC, "Subtotal —")
    rows.append({"section": "Totals", "line": "Gross Profit", "amount": st.get("gross_profit")})
    rows.append({"section": "Totals", "line": "Net Operating Income", "amount": st.get("net_operating_income")})
    rows.append({"section": "Totals", "line": "Net Income", "amount": st.get("net_income")})
    return {"title": f"Profit & Loss — {st.get('scope_label') or scope}",
            "subtitle": f"{period} · cash basis",
            "filename": f"pl-{_acct_slug(scope)}-{period.replace(' ', '-')}",
            "sheets": [{"name": "P&L", "rows": rows, "columns": _ACCT_COLS}]}


async def _account_balance_sheet(org_id, f):
    period = _resolve_period(f)
    scope = f.get("scope", "") or "consolidated"
    data = await AC.get_bs(period=period, scope=scope, org_id=org_id)
    if not data.get("computed"):
        raise ValueError(f"Balance Sheet not computed for {period} / {scope} — open /accounts and "
                         "click 'Compute statements' first.")
    st = data.get("statement") or {}
    rows = _stmt_rows(st, _BS_SEC, "Total")
    rows.append({"section": "Totals", "line": "Liabilities + Equity",
                 "amount": round((st.get("liabilities_total") or 0) + (st.get("equity_total") or 0), 2)})
    sub = f"{period} · point-in-time"
    if not st.get("balanced"):
        sub += f" · OUT OF BALANCE by ${abs(st.get('imbalance') or 0):,.2f}"
    return {"title": f"Balance Sheet — {st.get('scope_label') or scope}", "subtitle": sub,
            "filename": f"balance-sheet-{_acct_slug(scope)}-{period.replace(' ', '-')}",
            "sheets": [{"name": "Balance Sheet", "rows": rows, "columns": _ACCT_COLS}]}


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
        "label": "Weekly Owed-to-Distributor", "filters": ["thursday", "store", "market"],
        "live_path": lambda f: "/commcalc/asset/owed-weekly" + _qs(f, ["thursday", "store", "market"]),
        "build": _owed_weekly, "wants_tz": True},
    "charges_appeals": {
        "label": "Charges — Appeals & Denied", "filters": ["store", "market", "month", "year", "week_friday"],
        "live_path": lambda f: "/commcalc/asset/charges/appeals" + _qs(f, ["store", "market", "month", "year"]),
        "build": _charges_builder("appeals", "Charges — Appeals & Denied Payments")},
    "charges_vip_fees": {
        "label": "Charges — Distributor Fees", "filters": ["store", "market", "month", "year", "week_friday"],
        "live_path": lambda f: "/commcalc/asset/charges/vip_fees" + _qs(f, ["store", "market", "month", "year"]),
        "build": _charges_builder("vip_fees", "Charges — Distributor Fees")},
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
        "label": "Distributor Invoices", "filters": ["period", "location", "status"],
        "live_path": lambda f: "/commcalc/vip" + _qs(f, ["period", "location", "status"]),
        "build": _vip_invoices},
    "flags": {
        "label": "Flags", "filters": ["period"],
        "live_path": lambda f: "/commcalc/flags", "build": _flags, "wants_auth": True},
    "commissions": {
        "label": "Incentives", "filters": ["period"],
        "live_path": lambda f: "/commcalc/reports", "build": _commissions, "wants_auth": True},
    "gp": {
        "label": "Gross Profit", "filters": ["period", "market"],
        "live_path": lambda f: "/commcalc/gp", "build": _gp, "wants_auth": True},
    "discrepancy": {
        "label": "Pay Discrepancy", "filters": ["period"],
        "live_path": lambda f: "/commcalc/discrepancy", "build": _discrepancy},
    "phantom": {
        "label": "Phantom Payments", "filters": ["period"],
        "live_path": lambda f: "/commcalc/discrepancy", "build": _phantom},
    "sales_recon": {
        "label": "Sales Feed Recon", "filters": ["period"],
        "live_path": lambda f: "/commcalc/sales-recon" + _qs(f, ["period"]),
        "build": _sales_recon},
    "storeops_schedule": {
        "label": "Store Schedule (Week)", "filters": ["week_start", "store_code"],
        "live_path": lambda f: "/storeops/schedule",
        "build": _storeops_schedule},
    "top_sellers": {
        "label": "Top Sellers", "filters": ["period", "limit"],
        "live_path": lambda f: "/commcalc/kpi", "build": _top_sellers},
    "action_plan": {
        "label": "Daily Action Plan", "filters": ["period", "store_code", "rep"],
        "live_path": lambda f: "/commcalc/targets/action-plan" + _qs(f, ["store_code", "rep"]),
        "build": _action_plan, "wants_auth": True},
    "account_pl": {
        "label": "Profit & Loss (Account Module)", "filters": ["period", "scope"],
        "live_path": lambda f: "/accounts/pl" + _qs(f, ["scope"]),
        "build": _account_pl},
    "account_balance_sheet": {
        "label": "Balance Sheet (Account Module)", "filters": ["period", "scope"],
        "live_path": lambda f: "/accounts/balance-sheet" + _qs(f, ["scope"]),
        "build": _account_balance_sheet},
    # Payroll & Workforce (W3): payroll / hours approval / payroll tax / payroll expenses /
    # attendance / lateness — see workforce_reports.py for the builders, the shared pay-period
    # default, and the mig-434 pay-visibility posture of each entry.
    **WORKFORCE_REPORTS,
}


def list_reports():
    return [{"key": k, "label": v["label"], "filters": v["filters"]} for k, v in REPORTS.items()]


def validate_filters(report_key: str, filters: dict) -> None:
    """Raise ReportConfigError if this report's SAVED FILTERS can't produce a report — see
    report_filters.validate_filters. This wrapper supplies the live report-key list."""
    _validate_filters(report_key, filters, known_keys=REPORTS)


async def build_payload(report_key: str, org_id: str, filters: dict, *,
                        authorization: str = "", tz: str = "") -> dict:
    """Build one report payload. `authorization` is the CALLER's header (on-demand sends) and `tz`
    the subscription's timezone (scheduled sends); each reaches only the builders that opted in —
    see the module docstring."""
    spec = REPORTS.get(report_key)
    if not spec:
        raise KeyError(f"unknown report '{report_key}'")
    extra = {}
    if spec.get("wants_auth"):
        extra["authorization"] = authorization if isinstance(authorization, str) else ""
    if spec.get("wants_tz"):
        extra["tz"] = tz if isinstance(tz, str) else ""
    payload = await spec["build"](org_id, filters or {}, **extra)
    # A builder that RESOLVED relative filters hands back `live_filters` so the live-report link
    # points at exactly what was sent; everyone else links off the filters as given.
    payload["live_path"] = spec["live_path"](payload.pop("live_filters", None) or filters or {})
    return payload
