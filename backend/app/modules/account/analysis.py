"""Financial-analysis series assembly — chart-ready trends from the STORED statement snapshots
(finance roadmap Phase 3, owner directive 2026-09-02: "add other financial analysis options, bars
charts, projections, etc whatever a top of the line system should have").

ONE MATH PATH (roadmap §Phase 3 rule): every number here is read from `commcalc.account_statements`
payloads — the snapshots `statement_engine.compute_and_store` wrote — NEVER recomputed from raw
sources. A chart can therefore never disagree with the P&L / Balance Sheet pages; if a month looks
wrong on a chart it is the snapshot that is wrong, and the fix is a recompute (now automatic —
mig 940), not a second formula.

PURE + STDLIB-ONLY: rows in, chart-ready dict out. No DB, no app imports beyond the finance-local
`_period` (itself pure) — so the proof harness (backend/harness_financial_analysis.py) exercises
the real code with fixtures and no environment. The router does the one org-scoped read and passes
the rows here.

WHAT IT SERVES (the Financial Analysis page + any caller wanting series):
  • monthly    — consolidated P&L + BS trend per computed month (revenue/COGS/opex/GP/NI, margins,
                 cash & equivalents, assets/liabilities/equity, inventory);
  • expense_breakdown / expense_lines — per-month OPEX composition (stacked-bar ready) + the
                 latest month's composition with % shares;
  • companies / stores — per-scope comparison series (revenue/GP/NI per month) from the SAME
                 snapshots the scope dropdowns read;
  • ratios ride on `monthly` (gross_margin_pct, opex_ratio_pct, net_margin_pct) — None (never a
                 fake 0) when the denominator is 0.

Org-scoping and permissions live in the router (`GET /account/analysis`, gated by the
'account_trends' data grant — the charts hub gate). Rows arriving here are already one org's.
"""
from app.modules.account import _period

CASH_KEYS = ("cash", "store_cash_on_hand")   # = statement_engine.CF_CASH_KEYS (kept literal so
# this module stays app-import-free; harness pins the two tuples equal so they can never drift)


def _r2(x):
    try:
        return round(float(x or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _pct(part, whole):
    """Percentage (1dp) or None when the base is 0/invalid — a chart must show a GAP, not a fake 0."""
    p, w = _r2(part), _r2(whole)
    if not w:
        return None
    return round(p / w * 100.0, 1)


def _month_key(period):
    """Sortable (year, month) for a period under EITHER spelling; None when unparseable."""
    m, y = _period.parse_period(period or "")
    if 1 <= m <= 12 and y:
        return (y, m)
    return None


def _canon_label(period):
    """Canonical month-name spelling ('August 2026') so the same month never appears twice when
    snapshots were stored under both spellings."""
    m, y = _period.parse_period(period or "")
    if 1 <= m <= 12 and y:
        return f"{_period._MONTHS[m]} {y}"
    return str(period or "")


def _sec_subtotal(payload, sec_type):
    for sec in (payload or {}).get("sections", []) or []:
        if sec.get("type") == sec_type:
            return _r2(sec.get("subtotal"))
    return 0.0


def _sec_lines(payload, sec_type):
    for sec in (payload or {}).get("sections", []) or []:
        if sec.get("type") == sec_type:
            return sec.get("lines") or []
    return []


def _line_amount(payload, sec_type, keys):
    """Sum of the named line keys inside one section (missing keys sum as 0)."""
    want = set(keys)
    return _r2(sum(_r2(ln.get("amount")) for ln in _sec_lines(payload, sec_type)
                   if ln.get("key") in want))


# ── THE ONE HOME: which statement sections ARE "expenses" (owner request 2026-09-21) ────────────
# The Account hub grew an Expenses column, and the only defensible number for it is the one the P&L
# already reports for that same scope and period. So "expenses" is DEFINED here, once, as the
# statement sections that sit BELOW gross profit — the sections `engine._assemble` subtracts from
# gross profit to reach net income (statement_engine.PL_SECTIONS: Operating Expenses + Other).
# Identity, and it is the proof: gross_profit − expenses == net_income, for every scope, always.
#
# Nothing re-sums a source table to answer this question. If a future ruling reclassifies a dollar
# between `store_opex` and (say) `wages`, or adds a whole new expense SECTION, the hub moves with the
# P&L in the same recompute because both read this. `harness_account_expenses_one_home.py` fails the
# build if a caller stops dereferencing this tuple or a new below-GP section appears unregistered.
EXPENSE_SECTIONS = ("opex", "other")


def pl_totals(payload):
    """Headline P&L numbers straight from a stored snapshot payload (subtotals recomputed from the
    sections exactly like /account/overview does, so this can never disagree with the dashboard).

    `expenses` = Σ EXPENSE_SECTIONS subtotals — the statement's OWN expense total (Operating
    Expenses + Other), the single figure every expense surface in the finance hub reads."""
    rev = _sec_subtotal(payload, "revenue")
    cogs = _sec_subtotal(payload, "cogs")
    opex = _sec_subtotal(payload, "opex")
    other = _sec_subtotal(payload, "other")
    return {"revenue": rev, "cogs": cogs, "opex": opex, "other": other,
            "expenses": _r2(sum(_sec_subtotal(payload, t) for t in EXPENSE_SECTIONS)),
            "gross_profit": _r2((payload or {}).get("gross_profit")),
            "net_income": _r2((payload or {}).get("net_income"))}


# ── THE P&L's OWN CROSSCHECK (owner report 2026-10-06) ──────────────────────────────────────────
# OWNER: *"make sure all expenses and every commission and residual is assigned properly and p&l
# calculated properly then the data for all reports should be aligned"*.
#
# THE DEFECT, measured live. `commcalc.account_statements.crosscheck_ok` is persisted as
# `stmt.get("balanced", stmt.get("tied", True))` (`statement_engine.py`). `balanced` is set ONLY on
# a balance sheet (`engine._assemble`) and `tied` ONLY on a cash flow, so a P&L payload carries
# NEITHER key and the expression falls through to the literal `True` — every time, for every scope,
# for every month. House org: all **289** stored P&L snapshots say `crosscheck_ok = true`, and not
# one of them was ever checked. `account/router.py` serves that flag to the browser.
#
# THE CLASS, not the instance: **the platform reported an ABSENCE as a RESULT.** "I never checked
# this" was stored, and served, as "checked and fine" — the same shape as the pay path reporting
# money it could not place as money that did not exist (index §19.48). The house vocabulary already
# says ABSENCE IS NEVER A FINDING; this is that rule inverted, an absence reported as a PASS.
#
# The identity was never in doubt and never computed: EXPENSE_SECTIONS above already states it in
# prose — *"gross_profit − expenses == net_income, for every scope, always"* — and asserts it as a
# property of the design. This turns that sentence into the check, so the claim is tested rather
# than trusted.
#
# TRI-STATE, deliberately. `verdict` is "passed" / "failed" / "not_measured" and `ok` is None when
# nothing could be evaluated, because a boolean cannot tell "checked and fine" from "never checked"
# — which is the whole defect. A payload with no sections at all yields "not_measured", never a
# pass.
#
# PURE. No DB, no network, no clock. Callers: `statement_engine._persist` (the stored flag) and
# `account/router` (the served one), so the two can never disagree.

# A dollar of tolerance, matching the balance sheet's own `abs(imbalance) < 1.0` — the sections are
# each rounded to cents before they are summed, so sub-dollar drift is rounding, not a defect.
CROSSCHECK_TOLERANCE = 1.0


def pl_crosscheck(payload):
    """Did this P&L actually tie? Returns the identities, their verdict, and the dollar gap.

    Two identities, both of them already properties of the design rather than new rulings:
      · `gross_profit` == revenue − COGS
      · `net_income`   == gross_profit − expenses   (expenses = Σ EXPENSE_SECTIONS)

    A payload that carries no sections cannot be judged, so the verdict is `not_measured` and `ok`
    is None. That distinction is the point: the column it feeds used to default to True.
    """
    sections = (payload or {}).get("sections") or []
    t = pl_totals(payload)
    if not sections:
        return {"verdict": "not_measured", "ok": None,
                "reason": "the payload carries no sections, so neither identity can be evaluated",
                "identities": [], "worst_gap": None}

    checks = [
        {"name": "gross_profit == revenue - cogs",
         "expected": _r2(t["revenue"] - t["cogs"]), "reported": t["gross_profit"]},
        {"name": "net_income == gross_profit - expenses",
         "expected": _r2(t["gross_profit"] - t["expenses"]), "reported": t["net_income"]},
    ]
    for c in checks:
        c["gap"] = _r2(c["reported"] - c["expected"])
        c["holds"] = abs(c["gap"]) < CROSSCHECK_TOLERANCE
    worst = max(checks, key=lambda c: abs(c["gap"]))
    failed = [c for c in checks if not c["holds"]]
    return {
        "verdict": "passed" if not failed else "failed",
        "ok": not failed,
        "reason": "" if not failed else "; ".join(
            f'{c["name"]} is out by {c["gap"]}' for c in failed),
        "identities": checks,
        "worst_gap": worst["gap"],
    }


def statement_crosscheck(st_type, payload):
    """The ONE answer to "was this statement checked, and did it pass?", for any statement type.

    Replaces `stmt.get("balanced", stmt.get("tied", True))`, whose fall-through default made an
    unchecked P&L indistinguishable from a passing one. Each type names the key that carries its
    own verdict; a type with no key is `not_measured` rather than a silent pass.
    """
    payload = payload or {}
    if st_type == "pl":
        return pl_crosscheck(payload)
    key = {"balance_sheet": "balanced", "cash_flow": "tied"}.get(st_type)
    if key is None or key not in payload:
        return {"verdict": "not_measured", "ok": None,
                "reason": f"statement type {st_type!r} declares no crosscheck identity",
                "identities": [], "worst_gap": None}
    held = bool(payload.get(key))
    gap = payload.get("imbalance") if st_type == "balance_sheet" else None
    return {"verdict": "passed" if held else "failed", "ok": held,
            "reason": "" if held else f"{key} is false",
            "identities": [{"name": key, "holds": held, "gap": _r2(gap) if gap is not None else None}],
            "worst_gap": _r2(gap) if gap is not None else None}


def bs_totals(payload):
    """Headline BS numbers from a stored snapshot: cash & equivalents = the CASH_KEYS asset lines
    summed (a pre-938 payload simply has no store_cash_on_hand line → sums as 0, byte-identical)."""
    return {"cash": _line_amount(payload, "asset", CASH_KEYS),
            "inventory": _line_amount(payload, "asset", ("inventory",)),
            "assets": _r2((payload or {}).get("assets_total")),
            "liabilities": _r2((payload or {}).get("liabilities_total")),
            "equity": _r2((payload or {}).get("equity_total"))}


def dedupe_latest(rows):
    """THE "freshest wins" rule for stored statements (index §19.47, §19.50).

    {(month_key, statement_type, scope_key): row} keeping the newest computed_at — the same month
    stored under both period spellings (or recomputed) must count once, freshest wins. Public
    because EVERY reader that widens its period filter to both spellings needs it: widening without
    deduping turns one month into two and SUMS them. `statement_filter.filtered_statement`
    dereferences it for exactly that reason."""
    out = {}
    for r in rows or []:
        mk = _month_key(r.get("period"))
        if mk is None:
            continue
        k = (mk, r.get("statement_type"), r.get("scope_key"))
        cur = out.get(k)
        if cur is None or str(r.get("computed_at") or "") >= str(cur.get("computed_at") or ""):
            out[k] = r
    return out


def assemble(rows, months=12, own_company_ids=None, companies=None):
    """The chart-ready analysis payload. `rows` = account_statements rows (dicts with period,
    statement_type, scope_key, scope_label, payload, computed_at) for ONE org — every statement
    type/scope welcome; unknown ones are ignored. `months` = trailing window (by computed months,
    chronological). Nothing here re-derives money: it reads what the snapshots say.

    `own_company_ids` (canonical inventory from coa.org_companies, owner directive 2026-09-04):
    when given, `company:<id>` scopes NOT in the org's own entity inventory are dropped
    (coa.filter_org_scopes — the same fail-closed rule as the statement scope dropdowns), so a
    stale or foreign-entity snapshot can never chart in the per-company comparison. None (the
    pure/legacy shape) skips the check.

    `companies` (the same `org_companies` rows): the per-company comparison series takes its label
    from THE one home `coa.scope_display_label` (§13b.1, owner report 2026-10-03) instead of echoing
    the snapshot's stored `scope_label` — a renamed entity relabels its own series, and a snapshot
    with no stored label never charts as `company:<uuid>`. None keeps the pure/legacy fallback."""
    months = max(1, min(int(months or 12), 36))
    if own_company_ids is not None:
        from app.modules.account.coa import filter_org_scopes
        rows = filter_org_scopes(rows, own_company_ids)
    idx = dedupe_latest(rows)

    # the month axis = months with a computed CONSOLIDATED P&L, chronological, trailing window
    month_keys = sorted({mk for (mk, st, sc) in idx if st == "pl" and sc == "consolidated"})[-months:]
    labels = [f"{_period._MONTHS[m]} {y}" for (y, m) in month_keys]
    if not month_keys:
        return {"computed": False, "months": [], "monthly": [], "expense_lines": {},
                "expense_breakdown": [], "expense_composition_latest": [],
                "companies": [], "stores": [],
                "note": "No computed statements yet — compute a period first (or wait for the "
                        "auto-recompute sweep)."}

    # ── consolidated monthly trend (P&L + BS + ratios) ──────────────────────────────────────────
    monthly = []
    for mk, label in zip(month_keys, labels):
        pl = (idx.get((mk, "pl", "consolidated")) or {}).get("payload") or {}
        bs = (idx.get((mk, "balance_sheet", "consolidated")) or {}).get("payload") or {}
        p, b = pl_totals(pl), bs_totals(bs)
        monthly.append({"period": label, **p, **b,
                        "gross_margin_pct": _pct(p["gross_profit"], p["revenue"]),
                        "opex_ratio_pct": _pct(p["opex"], p["revenue"]),
                        "net_margin_pct": _pct(p["net_income"], p["revenue"])})

    # ── OPEX composition per month (stacked-bar ready) ──────────────────────────────────────────
    expense_lines, expense_breakdown = {}, []
    for mk, label in zip(month_keys, labels):
        pl = (idx.get((mk, "pl", "consolidated")) or {}).get("payload") or {}
        row = {"period": label}
        for ln in _sec_lines(pl, "opex"):
            key = ln.get("key") or "other_opex"
            expense_lines.setdefault(key, ln.get("label") or key)
            row[key] = _r2(row.get(key, 0.0) + _r2(ln.get("amount")))
        expense_breakdown.append(row)
    latest = expense_breakdown[-1] if expense_breakdown else {}
    latest_total = _r2(sum(v for k, v in latest.items() if k != "period"))
    composition = sorted(
        ({"key": k, "label": expense_lines.get(k, k), "amount": _r2(v),
          "pct": _pct(v, latest_total)}
         for k, v in latest.items() if k != "period"),
        key=lambda x: -abs(x["amount"]))

    # ── per-company / per-store comparison series ───────────────────────────────────────────────
    def _scope_series(prefix):
        from app.modules.account.coa import scope_display_label   # local: coa imports analysis' kin
        scopes = {}
        for (mk, st, sc), r in idx.items():
            if st != "pl" or not str(sc or "").startswith(prefix) or mk not in set(month_keys):
                continue
            scopes.setdefault(sc, {"scope_key": sc,
                                   "label": scope_display_label(sc, r.get("scope_label"), companies),
                                   "by_month": {}})
            p = pl_totals(r.get("payload") or {})
            scopes[sc]["by_month"][mk] = {"revenue": p["revenue"],
                                          "gross_profit": p["gross_profit"],
                                          "net_income": p["net_income"]}
        out = []
        for sc in sorted(scopes, key=lambda s: scopes[s]["label"] or s):
            ent = scopes[sc]
            series = []
            for mk, label in zip(month_keys, labels):
                pt = ent["by_month"].get(mk) or {"revenue": 0.0, "gross_profit": 0.0,
                                                 "net_income": 0.0}
                series.append({"period": label, **pt})
            out.append({"scope_key": ent["scope_key"], "label": ent["label"], "series": series})
        return out

    return {"computed": True, "months": labels, "monthly": monthly,
            "expense_lines": expense_lines, "expense_breakdown": expense_breakdown,
            "expense_composition_latest": composition,
            "companies": _scope_series("company:"), "stores": _scope_series("store:"),
            "note": "All figures read from the stored statement snapshots (one math path) — "
                    "recompute a period to refresh its point."}
