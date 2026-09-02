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


CLOSING_REPORTS = {
    "closing_envelope_report": {
        "label": "Envelope Report (Daily Closing)",
        "filters": ["date_from", "date_to", "stores", "markets", "reps", "status"],
        "live_path": lambda f: "/closing/envelope-report",
        "build": _envelope_report,
        "wants_auth": True,
    },
}
