"""Daily-closing report-registry entries (owner directive 2026-09-02).

`closing_envelope_report`: the Envelope report — every envelope (daily_closing row) in a date
range with the management count / over-short / comment / chargeback state, built by the SAME
in-process handler the live page reads (`closing.router.envelope_report` — never a second query
path), so a scheduled/emailed copy can never disagree with the screen.

Same entry shape as WORKFORCE_REPORTS / FINANCE_REPORTS, spliced into report_registry.REPORTS.
Every app import is LAZY (inside the builder) so the registry stays importable offline.
`wants_auth`: the caller's span keyset rides their own header on an on-demand send; a scheduled
run's blank header takes the org-wide path (same posture as the W3 workforce entries).
"""


def _resolve_range(filters):
    """date_from/date_to filters, defaulting to the current month (the endpoint's own default)."""
    f = filters or {}
    return (str(f.get("date_from") or "") or None, str(f.get("date_to") or "") or None)


_ENV_COLS = [
    {"header": "Date", "key": "close_date"},
    {"header": "Store", "key": "store_address"},
    {"header": "Market", "key": "market"},
    {"header": "Employee", "key": "employee_name"},
    {"header": "Declared cash", "key": "declared_cash", "money": True},
    {"header": "Counted", "key": "counted_amount", "money": True},
    {"header": "Variance", "key": "variance", "money": True},
    {"header": "Status", "key": "status"},
    {"header": "Comment", "key": "comment"},
    {"header": "Counted by", "key": "counted_by"},
    {"header": "Chargeback", "key": "chargeback_status"},
    {"header": "Chargeback $", "key": "chargeback_amount", "money": True},
    {"header": "DM verified", "key": "dm_verified"},
]


async def _envelope_report(org_id, f, authorization=""):
    from fastapi.concurrency import run_in_threadpool
    from app.modules.closing import router as closing_router

    date_from, date_to = _resolve_range(f)
    status = str((f or {}).get("status") or "")
    data = await run_in_threadpool(
        closing_router.envelope_report, date_from, date_to, None,
        (f or {}).get("markets"), (f or {}).get("stores"), (f or {}).get("reps"),
        status, authorization, org_id)
    rows = [{**r, "dm_verified": ("Yes" if r.get("dm_verified") else "No")}
            for r in (data.get("rows") or [])]
    t = data.get("totals") or {}
    sub = (f"{data.get('date_from')} → {data.get('date_to')} · {t.get('envelopes', 0)} envelopes · "
           f"{t.get('short', 0)} short (${t.get('short_total', 0):,.2f}) · "
           f"{t.get('over', 0)} over (${t.get('over_total', 0):,.2f}) · "
           f"{t.get('chargebacks', 0)} chargeback(s) ${t.get('chargeback_total', 0):,.2f}")
    return {"title": "Envelope Report", "subtitle": sub,
            "filename": f"envelope-report_{data.get('date_from')}_{data.get('date_to')}",
            "sheets": [{"name": "Envelopes", "rows": rows, "columns": _ENV_COLS}]}


_RECON_COLS = [
    {"header": "Day", "key": "day"},
    {"header": "Store", "key": "store_name"},
    {"header": "Market", "key": "market"},
    {"header": "Declared bill pay (cash)", "key": "epay_cash_declared", "money": True},
    {"header": "Declared bill pay (card)", "key": "epay_credit_declared", "money": True},
    {"header": "Sales-tx bill pay", "key": "sales_billpay", "money": True},
    {"header": "Sales-tx on card", "key": "sales_billpay_card", "money": True},
    {"header": "Processor bill pay", "key": "pos_billpay", "money": True},
    {"header": "Δ declared−sales", "key": "delta_declared_sales", "money": True},
    {"header": "Δ declared−processor", "key": "billpay_delta", "money": True},
    {"header": "3-way status", "key": "three_way_status"},
]


async def _billpay_recon_report(org_id, f, authorization=""):
    """The 3-way bill-payment recon (owner 2026-09-02 #2) as a scheduled/emailed report — the
    SAME in-process handler the Cash Recon (Management) screen reads (cash_recon_management),
    so the emailed copy can never disagree with the screen. Inherits the endpoint's
    market-manager-and-above gate: an on-demand send rides the caller's token; a scheduled run's
    blank header passes only where the platform's login master switch is off (fail-closed —
    the gate's own rule, never bypassed for email)."""
    from fastapi.concurrency import run_in_threadpool
    from app.modules.closing import router as closing_router

    date_from, date_to = _resolve_range(f)
    if not (date_from and date_to):
        # scheduled-run default: the trailing 7 days (the endpoint itself requires a range)
        from datetime import date as _d, timedelta as _td
        date_to = date_to or _d.today().isoformat()
        date_from = date_from or (_d.fromisoformat(date_to) - _td(days=6)).isoformat()
    data = await run_in_threadpool(
        closing_router.cash_recon_management, "", date_from, date_to,
        1.0, authorization, org_id)
    t = data.get("totals") or {}
    tw = data.get("three_way") or {}
    sub = (f"{data.get('start')} → {data.get('end')} · declared ${t.get('epay_declared') or 0:,.2f} · "
           f"sales-tx {('$%s' % format(t.get('sales_billpay'), ',.2f')) if t.get('sales_billpay') is not None else 'no data'} · "
           f"processor ${t.get('pos_billpay') or 0:,.2f} · "
           f"{tw.get('mismatched', 0)} mismatched store-day(s)")
    return {"title": "Bill Payment 3-Way Recon", "subtitle": sub,
            "filename": f"billpay-3way-recon_{data.get('start')}_{data.get('end')}",
            "sheets": [{"name": "3-Way Recon", "rows": data.get("rows") or [],
                        "columns": _RECON_COLS}]}


CLOSING_REPORTS = {
    "closing_envelope_report": {
        "label": "Envelope Report (Daily Closing)",
        "filters": ["date_from", "date_to", "stores", "markets", "reps", "status"],
        "live_path": lambda f: "/closing/envelope-report",
        "build": _envelope_report,
        "wants_auth": True,
    },
    # 3-way bill-payment recon (owner directive 2026-09-02 #2) — the cash-recon-management
    # endpoint in-process (W3 pattern; declared vs sales-tx vs processor, per store/day).
    "closing_billpay_recon": {
        "label": "Bill Payment 3-Way Recon (Daily Closing)",
        "filters": ["date_from", "date_to"],
        "live_path": lambda f: "/closing/cash-recon-management",
        "build": _billpay_recon_report,
        "wants_auth": True,
    },
}
