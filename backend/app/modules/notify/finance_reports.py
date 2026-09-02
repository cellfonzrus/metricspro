"""Finance report-registry entries — the ON-DEMAND financial statement (owner directive
2026-09-02: statements "whenever required, platform wide").

One entry, `financial_statement`: a FRESH P&L + Balance Sheet + Cash Flow for any org / period /
scope, computed at send time by `account.statement_engine.statement()` — the same platform
service `GET /account/statement/{period}` fronts. Unlike the snapshot-reading `account_pl` /
`account_balance_sheet` entries (which stay: they mirror the pages byte-for-byte), this one never
answers "not computed yet": scheduled and on-demand sends always carry current numbers.

Same entry shape as WORKFORCE_REPORTS, spliced into report_registry.REPORTS; every app import is
LAZY (inside the builder) so the registry stays importable offline. The blocking statement build
hops to a worker thread (the account module's SEV-1 event-loop rule)."""


def _resolve_period(filters):
    """'Month YYYY' literal, or 'current'/'last' tokens — the registry's period convention."""
    from datetime import date
    p = (filters or {}).get("period")
    if p and str(p).lower() not in ("current", "this", "now"):
        if str(p).lower() in ("last", "previous", "prev"):
            t = date.today()
            y, m = (t.year - 1, 12) if t.month == 1 else (t.year, t.month - 1)
            return date(y, m, 1).strftime("%B %Y")
        return str(p)
    return date.today().strftime("%B %Y")


_SEC_COLS = [
    {"header": "Section", "key": "section"},
    {"header": "Line", "key": "line"},
    {"header": "Amount", "key": "amount", "money": True},
]


def _sheet_rows(st, subtotal_prefix="Total"):
    rows = []
    for s in (st or {}).get("sections") or []:
        for ln in s.get("lines") or []:
            rows.append({"section": s.get("name"), "line": ln.get("label"), "amount": ln.get("amount")})
        rows.append({"section": s.get("name"), "line": f"{subtotal_prefix} — {s.get('name')}",
                     "amount": s.get("subtotal")})
    return rows


async def _financial_statement(org_id, f):
    from fastapi.concurrency import run_in_threadpool
    from app.core.database import get_supabase
    from app.modules.account import statement_engine

    period = _resolve_period(f)
    scope = (f or {}).get("scope") or "consolidated"
    data = await run_in_threadpool(statement_engine.statement, get_supabase(), org_id, period, scope)
    if not data.get("computed"):
        raise ValueError(f"Financial statement unavailable for {period} / {scope}: "
                         f"{data.get('note') or 'unknown scope'}")
    label = data.get("scope_label") or scope
    slug = "".join(c if c.isalnum() else "-" for c in str(scope)).strip("-") or "scope"
    sheets = []
    pl = data.get("pl") or {}
    pl_rows = _sheet_rows(pl, "Subtotal")
    pl_rows += [{"section": "Totals", "line": "Gross Profit", "amount": pl.get("gross_profit")},
                {"section": "Totals", "line": "Net Operating Income", "amount": pl.get("net_operating_income")},
                {"section": "Totals", "line": "Net Income", "amount": pl.get("net_income")}]
    sheets.append({"name": "P&L", "rows": pl_rows, "columns": _SEC_COLS})
    bs = data.get("balance_sheet") or {}
    bs_rows = _sheet_rows(bs)
    bs_rows.append({"section": "Totals", "line": "Liabilities + Equity",
                    "amount": round((bs.get("liabilities_total") or 0) + (bs.get("equity_total") or 0), 2)})
    sheets.append({"name": "Balance Sheet", "rows": bs_rows, "columns": _SEC_COLS})
    cf = data.get("cash_flow") or {}
    cf_rows = _sheet_rows(cf, "Net cash —")
    cf_rows.append({"section": "Totals", "line": "Implied change in cash",
                    "amount": cf.get("implied_cash_change")})
    sheets.append({"name": "Cash Flow", "rows": cf_rows, "columns": _SEC_COLS})
    sub = f"{period} · computed on demand"
    if bs and not bs.get("balanced"):
        sub += f" · BS out of balance by ${abs(bs.get('imbalance') or 0):,.2f}"
    return {"title": f"Financial Statements — {label}", "subtitle": sub,
            "filename": f"financial-statements-{slug}-{period.replace(' ', '-')}",
            "sheets": sheets}


FINANCE_REPORTS = {
    "financial_statement": {
        "label": "Financial Statements (on demand)",
        "filters": ["period", "scope"],
        "live_path": lambda f: "/accounts",
        "build": _financial_statement,
    },
}
