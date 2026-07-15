"""storeops Payroll Expenses engine — pure, DB-free functions.

Two employer-burden cost buckets, computed on the payroll run and rolled into ONE ADDITIVE Store
Expenses line ("Payroll Expenses", source_key='payroll_expenses'):

  1. PAYROLL TAX (auto-computed; the RATES are config, never the fact that it runs) — employer-side
     FICA Social Security + Medicare + FUTA + SUTA/state unemployment, on the SAME wages/hours basis
     `/payroll-by-store` and the PTO accrual engine already use (shifts x employees.pay_rate, hours =
     actual_hours if clocked else scheduled_hours, attributed to the shift's own store_code).

     Wage-base CAPS (SS/FUTA/SUTA) are tracked PER-EMPLOYEE, CUMULATIVE FOR THE CALENDAR YEAR — a
     naive "cap the wages of just THIS period" check would almost never bind (a $7,000 FUTA wage base
     is usually exhausted by Q1-Q2 for any continuously-employed rep, and a single month's wages
     rarely alone exceed $168,600), which would silently OVERSTATE FUTA/SUTA all year and eventually
     understate FICA once an earner crosses the SS cap. The router resolves "wages already taxed
     toward each cap so far this calendar year" the SAME way pto_accrual.py resolves prior_balance:
     sum this employee's persisted ledger rows for periods in the same year, before this period
     (`storeops.payroll_tax_ledger`, one row per (org, period, employee)) — so a mid-year first run is
     safe (defaults to 0 consumed = correct for a tenant that never ran this before) and a re-run of
     a past period is idempotent (delete-by-period then insert, same pattern as the PTO ledger).

  2. PAYROLL EXPENSE ITEMS (fully operator-customizable — Unemployment Insurance / Workers Comp /
     anything else added on the HR "Payroll Expenses" page), each with its own calc_method:
       pct_wages     = rate * wages                (wages optionally capped at wage_cap, PER PERIOD —
                                                      see note below, this is NOT the same YTD-cumulative
                                                      cap the tax bucket uses)
       per_100_wages = rate * wages / 100
       per_employee  = rate * headcount
       fixed         = rate                        (a flat amount)
     scope='store' computes each store independently off that store's own wages/headcount.
     scope='company' computes ONE company-wide amount off company totals, then ALLOCATES it across
     stores proportional to each store's share of company wages (the per-task-order choice for
     "fixed [company-wide]", applied uniformly to every calc_method under scope='company' for
     consistency — documented in docs/handoffs/people.md).
     wage_cap here is a simple PER-PERIOD, PER-BASIS cap (min(wages_used, wage_cap)) — NOT a
     cumulative annual cap like the payroll-tax wage bases. It exists so an operator can express "this
     rate only applies to the first $X of wages this period" for a custom item; it is not trying to
     model any specific government wage-base law (those already have their own dedicated fields in
     payroll_tax_config). Flagged as a documented simplification, not an oversight.

This module NEVER changes a wage/payout number — every input here (hours, pay_rate, headcount) is
READ-ONLY. It only produces new, additive cost figures. The router (router.py) does all I/O; this file
is pure so it's unit-testable without a database (see harness_payroll_expenses.py).
"""
from typing import Dict, List, Optional

# ── payroll tax config (RULE TWO — nothing hard-coded; storeops.payroll_tax_config, one row per org) ─
DEFAULT_TAX_CONFIG = {
    "enabled": True,
    "fica_ss_rate": 0.062,          # employer FICA Social Security
    "fica_ss_wage_base": 168600.0,  # 2026-ish federal SS wage base — tenant-editable, not re-seeded automatically
    "medicare_rate": 0.0145,        # employer Medicare — no wage cap
    "futa_rate": 0.006,             # federal unemployment, effective post-credit rate
    "futa_wage_base": 7000.0,       # federal FUTA wage base (statutory, rarely changes)
    "suta_rate": 0.027,             # STATE unemployment — varies a LOT by state/tenant; 2.7% is a
                                     # commonly-cited generic "new employer" placeholder, NOT any
                                     # specific state's real rate — the tenant must set their own.
    "suta_wage_base": 9000.0,       # ditto — a generic placeholder, tenant-editable.
}
_TAX_FIELDS = tuple(DEFAULT_TAX_CONFIG.keys())

CALC_METHODS = ("pct_wages", "per_100_wages", "per_employee", "fixed")
ITEM_SCOPES = ("store", "company")


def resolve_tax_config(org_row: Optional[dict] = None) -> dict:
    """Merge a (possibly partial / absent) org config row over the code defaults. A single org-level
    row is sufficient here (unlike PTO's employee/role layering) — payroll tax rates are a jurisdiction
    fact about the legal entity/tenant, not something that varies per employee or role."""
    eff = dict(DEFAULT_TAX_CONFIG)
    if org_row:
        for f in _TAX_FIELDS:
            v = org_row.get(f)
            if v is not None:
                eff[f] = v
    for f in ("fica_ss_rate", "fica_ss_wage_base", "medicare_rate", "futa_rate",
              "futa_wage_base", "suta_rate", "suta_wage_base"):
        eff[f] = float(eff[f])
    eff["enabled"] = bool(eff["enabled"])
    return eff


# ── shared wage/headcount basis (built from pto_accrual.hours_worked_from_shifts' same shape) ────────
def wages_by_store_from_hours(hours_by_employee_store: Dict[str, Dict[str, float]],
                               rates: Dict[str, float]) -> Dict[str, float]:
    """{employee_id: {store: hours}} + {employee_id: rate} -> {store: wages}. Same wages = hours *
    pay_rate basis as /payroll-by-store, summed per store."""
    out: Dict[str, float] = {}
    for eid, by_store in (hours_by_employee_store or {}).items():
        rate = float(rates.get(eid, 0.0))
        for store, hrs in (by_store or {}).items():
            out[store] = out.get(store, 0.0) + float(hrs) * rate
    return out


def headcount_by_store_from_hours(hours_by_employee_store: Dict[str, Dict[str, float]]) -> Dict[str, int]:
    """Distinct employees with > 0 hours at each store this period (the 'per_employee' calc basis)."""
    out: Dict[str, int] = {}
    for eid, by_store in (hours_by_employee_store or {}).items():
        for store, hrs in (by_store or {}).items():
            if float(hrs or 0) > 0:
                out[store] = out.get(store, 0) + 1
    return out


# ── payroll tax engine ──────────────────────────────────────────────────────────────────────────────
def compute_payroll_tax(hours_by_employee_store: Dict[str, Dict[str, float]],
                         rates: Dict[str, float],
                         tax_cfg: dict,
                         ytd_taxable_before: Optional[Dict[str, Dict[str, float]]] = None) -> dict:
    """Per-employee employer payroll tax for one period, split across the stores the employee worked
    (proportional to each store's wage share — same attribution style as pto_accrual.compute_pto).

    ytd_taxable_before: {employee_id: {"ss": hrs$, "futa": $, "suta": $}} — TAXABLE wages already
    counted toward each cap in EARLIER periods of the SAME calendar year (0 default = a mid-year first
    run, or a tenant that has never run this before, correctly treats every wage-base as untouched).

    Returns {"employees": {eid: {...,"by_store": {store: {...}}}},
             "stores": {store: {"fica_ss","medicare","futa","suta","total","wages"}}}
    enabled=False on tax_cfg produces an empty result (the whole tax bucket is off), same convention
    as PTO's per-employee enabled flag.
    """
    ytd_taxable_before = ytd_taxable_before or {}
    if not tax_cfg.get("enabled", True):
        return {"employees": {}, "stores": {}}

    employees: Dict[str, dict] = {}
    stores: Dict[str, dict] = {}

    def _bump(store: str, wages: float, ss: float, medicare: float, futa: float, suta: float):
        if not store:
            return
        d = stores.setdefault(store, {"store": store, "wages": 0.0, "fica_ss": 0.0, "medicare": 0.0,
                                       "futa": 0.0, "suta": 0.0, "total": 0.0})
        d["wages"] += wages
        d["fica_ss"] += ss
        d["medicare"] += medicare
        d["futa"] += futa
        d["suta"] += suta
        d["total"] += (ss + medicare + futa + suta)

    for eid in sorted(hours_by_employee_store or {}):
        by_store_hours = hours_by_employee_store.get(eid) or {}
        rate = float(rates.get(eid, 0.0))
        wages_by_store = {s: float(h) * rate for s, h in by_store_hours.items()}
        total_wages = sum(wages_by_store.values())
        if total_wages <= 0:
            continue

        ytd = ytd_taxable_before.get(eid) or {}
        ss_before = float(ytd.get("ss", 0.0))
        futa_before = float(ytd.get("futa", 0.0))
        suta_before = float(ytd.get("suta", 0.0))

        ss_room = max(0.0, tax_cfg["fica_ss_wage_base"] - ss_before)
        ss_taxable = min(total_wages, ss_room)
        ss_tax = ss_taxable * tax_cfg["fica_ss_rate"]

        medicare_taxable = total_wages   # no wage cap on employer Medicare
        medicare_tax = medicare_taxable * tax_cfg["medicare_rate"]

        futa_room = max(0.0, tax_cfg["futa_wage_base"] - futa_before)
        futa_taxable = min(total_wages, futa_room)
        futa_tax = futa_taxable * tax_cfg["futa_rate"]

        suta_room = max(0.0, tax_cfg["suta_wage_base"] - suta_before)
        suta_taxable = min(total_wages, suta_room)
        suta_tax = suta_taxable * tax_cfg["suta_rate"]

        total_tax = ss_tax + medicare_tax + futa_tax + suta_tax

        by_store_detail = {}
        for store, w in wages_by_store.items():
            share = (w / total_wages) if total_wages else 0.0
            d = {
                "wages": round(w, 2),
                "fica_ss": round(ss_tax * share, 2),
                "medicare": round(medicare_tax * share, 2),
                "futa": round(futa_tax * share, 2),
                "suta": round(suta_tax * share, 2),
            }
            d["total"] = round(d["fica_ss"] + d["medicare"] + d["futa"] + d["suta"], 2)
            by_store_detail[store] = d
            _bump(store, d["wages"], d["fica_ss"], d["medicare"], d["futa"], d["suta"])

        employees[eid] = {
            "employee_id": eid,
            "wages": round(total_wages, 2),
            "ss_taxable_wages": round(ss_taxable, 2), "fica_ss_tax": round(ss_tax, 2),
            "medicare_taxable_wages": round(medicare_taxable, 2), "medicare_tax": round(medicare_tax, 2),
            "futa_taxable_wages": round(futa_taxable, 2), "futa_tax": round(futa_tax, 2),
            "suta_taxable_wages": round(suta_taxable, 2), "suta_tax": round(suta_tax, 2),
            "total_tax": round(total_tax, 2),
            "by_store": by_store_detail,
        }

    for d in stores.values():
        for k in ("wages", "fica_ss", "medicare", "futa", "suta", "total"):
            d[k] = round(d[k], 2)
    return {"employees": employees, "stores": stores}


# ── payroll expense items engine ────────────────────────────────────────────────────────────────────
def _apply_calc(basis_wages: float, basis_headcount: int, calc_method: str,
                 rate: float, wage_cap: Optional[float]) -> float:
    w = basis_wages if wage_cap is None else min(basis_wages, wage_cap)
    if calc_method == "pct_wages":
        return w * rate
    if calc_method == "per_100_wages":
        return (w / 100.0) * rate
    if calc_method == "per_employee":
        return basis_headcount * rate
    if calc_method == "fixed":
        return rate
    return 0.0


def compute_expense_items(wages_by_store: Dict[str, float], headcount_by_store: Dict[str, int],
                           company_headcount: int, items: List[dict]) -> dict:
    """items: [{key, name, calc_method, rate_or_amount, wage_cap, scope, enabled}] (a disabled item is
    skipped entirely — same "off means nothing happens" convention as PTO's per-employee enabled flag).

    Returns {"stores": {store: {item_key: amount}},
             "items": [{key,label,calc_method,scope,rate_or_amount,wage_cap,company_amount,by_store}]}
    """
    company_wages = sum(wages_by_store.values())
    stores: Dict[str, Dict[str, float]] = {}
    detail: List[dict] = []

    for it in items or []:
        if not it.get("enabled", True):
            continue
        key = str(it.get("key") or it.get("id") or "").strip()
        if not key:
            continue
        label = it.get("name") or key
        method = it.get("calc_method") if it.get("calc_method") in CALC_METHODS else "fixed"
        rate = float(it.get("rate_or_amount") or 0)
        cap_raw = it.get("wage_cap")
        cap = None if cap_raw in (None, "") else float(cap_raw)
        scope = it.get("scope") if it.get("scope") in ITEM_SCOPES else "store"

        by_store_amt: Dict[str, float] = {}
        if scope == "store":
            for store, w in wages_by_store.items():
                hc = headcount_by_store.get(store, 0)
                amt = _apply_calc(w, hc, method, rate, cap)
                if amt:
                    by_store_amt[store] = amt
        else:  # company: one company-wide figure, allocated across stores by wage share
            company_amt = _apply_calc(company_wages, company_headcount, method, rate, cap)
            if company_amt and company_wages > 0:
                for store, w in wages_by_store.items():
                    share = w / company_wages
                    by_store_amt[store] = company_amt * share
            # company_wages<=0 with a nonzero company_amt (e.g. a per_employee/fixed item and no wage
            # data at all this period) has no wage basis to allocate against — documented limitation,
            # the amount is not silently invented against an arbitrary store.

        by_store_amt = {s: round(a, 2) for s, a in by_store_amt.items() if round(a, 2) != 0}
        for store, amt in by_store_amt.items():
            d = stores.setdefault(store, {})
            d[key] = round(d.get(key, 0.0) + amt, 2)
        detail.append({
            "key": key, "label": label, "calc_method": method, "scope": scope,
            "rate_or_amount": rate, "wage_cap": cap,
            "company_amount": round(sum(by_store_amt.values()), 2),
            "by_store": by_store_amt,
        })
    return {"stores": stores, "items": detail}


# ── rollup (the SINGLE additive "Payroll Expenses" line the owner asked for) ───────────────────────
def rollup_cells(tax_stores: Dict[str, dict], item_stores: Dict[str, Dict[str, float]]) -> List[dict]:
    """tax bucket total + Σ every enabled item, per store, combined into ONE amount per store — the
    `cells` payload for POST .../expenses/{period}/system-line (source_key='payroll_expenses',
    label='Payroll Expenses'). Includes every store touched by EITHER bucket even at cost 0 (clears a
    stale prior value on the receiver's idempotent replace, same convention as PTO's
    expense_cells_from_stores)."""
    all_stores = set(tax_stores or {}) | set(item_stores or {})
    out = []
    for s in sorted(all_stores):
        tax_total = (tax_stores.get(s) or {}).get("total", 0.0)
        item_total = sum((item_stores.get(s) or {}).values())
        out.append({"store": s, "amount": round(tax_total + item_total, 2)})
    return out


# ── ledger row shaping (pure — the router just deletes-by-period then inserts what this returns) ────
TAX_COMPONENT_LABELS = {
    "fica_ss": "FICA Social Security (employer)",
    "medicare": "Medicare (employer)",
    "futa": "FUTA",
    "suta": "SUTA / State Unemployment",
}


def tax_ledger_rows(org_id: str, period: str, tax_result: dict, run_by: Optional[str] = None) -> List[dict]:
    """One row per (org, period, employee) — the source of truth `_payex_gather` sums (by calendar
    year, before `period`) to compute NEXT period's YTD wage-base room. An employee with zero wages
    this period produces no row (nothing to persist, matches PTO ledger's convention)."""
    rows = []
    for eid, e in (tax_result.get("employees") or {}).items():
        rows.append({
            "org_id": org_id, "period": period, "employee_id": eid,
            "wages": e["wages"],
            "ss_taxable_wages": e["ss_taxable_wages"], "fica_ss_tax": e["fica_ss_tax"],
            "medicare_taxable_wages": e["medicare_taxable_wages"], "medicare_tax": e["medicare_tax"],
            "futa_taxable_wages": e["futa_taxable_wages"], "futa_tax": e["futa_tax"],
            "suta_taxable_wages": e["suta_taxable_wages"], "suta_tax": e["suta_tax"],
            "total_tax": e["total_tax"], "run_by": run_by,
        })
    return rows


def expense_ledger_rows(org_id: str, period: str, tax_result: dict, item_result: dict,
                         run_by: Optional[str] = None) -> List[dict]:
    """One row per (org, period, store, component) — the itemized breakdown the HR page renders (tax
    components broken out fica_ss/medicare/futa/suta, plus one row per enabled payroll_expense_item
    per store it touched). This is what the HR page reads; the rolled-up push (`rollup_cells`) is
    derived independently from the same `tax_result`/`item_result`, not from these rows, so the two
    can never drift."""
    rows = []
    for store, d in (tax_result.get("stores") or {}).items():
        for comp in ("fica_ss", "medicare", "futa", "suta"):
            amt = d.get(comp, 0.0)
            if not amt:
                continue
            rows.append({"org_id": org_id, "period": period, "store": store,
                         "component_type": "tax", "component_key": comp,
                         "label": TAX_COMPONENT_LABELS[comp], "amount": round(amt, 2), "run_by": run_by})
    for it in (item_result.get("items") or []):
        for store, amt in (it.get("by_store") or {}).items():
            if not amt:
                continue
            rows.append({"org_id": org_id, "period": period, "store": store,
                         "component_type": "item", "component_key": it["key"],
                         "label": it["label"], "amount": round(amt, 2), "run_by": run_by})
    return rows
