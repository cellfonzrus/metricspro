"""WHAT THIS PAY PERIOD COSTS THE COMPANY — the employer burden carried onto the payroll report's
own rows (index §STOREOPS-08).

Owner, 2026-11-12: *"in the payroll report add anopthe rtil for the payroll tax paid for that period
and a another tile shows the total of payroll and payroll tax as a cost of payroll to teh company"*.

THIS MODULE OWNS NO TAX RULE. The employer tax rule has had one home since migration 404 —
`payroll_expenses.compute_payroll_tax_from_wages`: FICA Social Security + Medicare + FUTA + SUTA, at
the org's own configured rates, with PER-EMPLOYEE CUMULATIVE CALENDAR-YEAR wage-base caps, split
across stores by wage share. The HR Payroll Expenses page books from it and the P&L carries what it
books. A second formula here would be two answers to "what did payroll cost", which is the duplicate
derivation the index rules forbid — so this module RESOLVES THROUGH it and does nothing but carry the
answer back onto the rows the report already serves. It spells no rate, no wage base and no dollar.

WHY IT TAKES WAGES AND NOT HOURS. `GET /storeops/payroll` serves one row per person per pay period
with the pay those hours produced — and for a SALARIED employee it serves a 0-hour row carrying their
period pay (`payroll_salary.py`). Asking the hours door for that person's tax answers $0.00. So the
one home grew a wages-keyed entry point and the hours door now delegates to it; the cap ladder and
the per-store split stayed in exactly one place (`harness_payroll_employer_cost.py` t1-t3).

BOTH BASES, BECAUSE THE REPORT PRINTS BOTH. The report shows Scheduled Pay and Actual Pay side by
side, and the owner's figure of 2026-11-12 ($110,571.59) was the SCHEDULED one. A tile that silently
taxed one of them would not tie to the column beside it, so every row carries the tax and the total
cost on each basis and the screen says which it is showing.

ABSENCE IS NEVER ZERO (§CORE-17.3i). A row the producer did not pay — nothing measured and not ticked
to be paid from the schedule — carries NO pay figure, so it is given NO cost figure and is not
counted. A $0.00 employer cost there would say the company employed that person for free.

A PAY-GATED CALLER CARRIES NO COST EITHER (mig 434, `pay_visibility`). The route strips the money
keys from a row before this module ever sees it, and a row with no pay key gets no cost key — never a
$0.00, which is the same doctrine the money drill-down panels already apply.

THE TAX BUCKET CAN BE OFF, AND THAT IS STATED. `payroll_tax_config.enabled = false` is a real
configuration, and the one home answers empty for it. The result then says `tax_enabled: False` so the
surface can say "not configured" rather than printing a $0.00 tax and a total cost that implies a free
payroll.

💰 MOVES NO MONEY. It books nothing, writes nothing and changes no wage or payout figure: the pay
numbers are the producer's own, and the tax is the HR page's own rule read a second time for display.
PURE: no I/O, no framework import, no clock, no org / carrier / tenant / store / product name
(RULE TWO), and no rate, wage base or threshold of its own.
"""
from typing import Dict, List, Optional

from app.modules.storeops import payroll_hours_basis as _hb
from app.modules.storeops.payroll_expenses import compute_payroll_tax_from_wages

#: DID THIS ROW'S PERIOD PRODUCE PAID HOURS — the one home's own row-level rule
#: (`payroll_hours_basis.row_is_paid`), bound by identity. Not a copy, and not a rule of this
#: module's own: a basis nobody has ruled on reads as not payable here exactly as it does there.
_paid_hours = _hb.row_is_paid


def carries_pay(row, pay_key) -> bool:
    """PURE: did the report actually PAY this row — the question the BURDEN follows, which is not
    quite the question the HOURS panels ask.

    MEASURED ON LIVE DATA, 2026-11-12, and the reason this predicate exists. Three LuxeLink employees
    in October — $16,607.15 between them — are salaried: `GET /storeops/payroll` serves them a row
    with their period pay and an EMPTY `hours_by_basis`, because a salary allocation is not read out
    of any day's hours at all (`payroll_salary.py`). Asking `row_is_paid` alone would give those
    three no employer tax, so the cost tile would have shown the company paying $110,571.59 and
    owing tax on only $93,964.44 of it — understating the burden while looking entirely plausible.

    So the rule is: the row carries a pay FIGURE, and either paid hours produced it OR the figure is
    non-zero (the salary case). A `$0.00` with nothing measured behind it is NOT paid — that is the
    §CORE-17.3i row, and giving it a $0.00 burden would say the company employed somebody for free.
    A row whose pay key was deleted by `pay_visibility.strip_pay` carries no figure at all, so a
    pay-gated caller is served no burden either."""
    row = row or {}
    if pay_key not in row or row.get(pay_key) is None:
        return False
    return _paid_hours(row) or float(row.get(pay_key) or 0) != 0.0

# The producer's own pay keys, per basis. Named here once so a renamed key is a one-line change and
# never a silently missing tile.
ACTUAL_PAY = "actual_pay"
SCHEDULED_PAY = "scheduled_pay"

TAX_LEGS = ("fica_ss", "medicare", "futa", "suta")


def _wages(rows, pay_key) -> Dict[str, Dict[str, float]]:
    """{employee_id: {store: wages}} off the producer's rows, skipping a row with no pay figure at
    all — that is the un-paid / pay-gated row, and it must not become a $0.00 wage."""
    out: Dict[str, Dict[str, float]] = {}
    for r in rows or ():
        eid = (r or {}).get("employee_id")
        if not eid or not carries_pay(r, pay_key):
            continue
        store = str((r.get("store") or "")).strip()
        d = out.setdefault(eid, {})
        d[store] = round(d.get(store, 0.0) + float(r.get(pay_key) or 0), 2)
    return out


def _per_row_tax(rows, pay_key, tax_cfg, ytd_taxable_before):
    """The one home's answer, indexed back to the rows it came from: {(employee_id, store): leg dict}.

    The split the one home performs is per (employee, store), which is exactly this report's row
    grain — one person at the store they worked the most of the period — so no apportionment of this
    module's own is needed or performed."""
    res = compute_payroll_tax_from_wages(_wages(rows, pay_key), tax_cfg, ytd_taxable_before)
    out = {}
    for eid, emp in (res.get("employees") or {}).items():
        for store, legs in (emp.get("by_store") or {}).items():
            out[(eid, store)] = legs
    return out


def employer_cost_rows(rows: List[dict], tax_cfg: dict,
                        ytd_taxable_before: Optional[Dict[str, Dict[str, float]]] = None) -> dict:
    """Return `{"rows": [...], "totals": {...}, "tax_enabled": bool}`.

    `rows` are the producer's payroll rows, UNCHANGED — a copy is returned with the cost keys added,
    so a caller that passes the endpoint's own list keeps it intact.

    Added per row, and ONLY where that row carries the pay figure it is derived from:
      `employer_tax`           the four legs summed, on ACTUAL pay
      `tax_fica_ss` / `tax_medicare` / `tax_futa` / `tax_suta`   the legs, carried not recomputed
      `employer_cost`          actual pay + employer tax — the cost of this person to the company
      `employer_tax_scheduled` / `employer_cost_scheduled`       the same pair on SCHEDULED pay
    """
    enabled = bool((tax_cfg or {}).get("enabled", True))
    act_tax = _per_row_tax(rows, ACTUAL_PAY, tax_cfg, ytd_taxable_before)
    sch_tax = _per_row_tax(rows, SCHEDULED_PAY, tax_cfg, ytd_taxable_before)

    out_rows: List[dict] = []
    totals = {"actual_pay": 0.0, "scheduled_pay": 0.0, "employer_tax": 0.0,
              "employer_tax_scheduled": 0.0, "employer_cost": 0.0,
              "employer_cost_scheduled": 0.0, "counted_rows": 0,
              **{f"tax_{leg}": 0.0 for leg in TAX_LEGS}}

    for r in rows or ():
        row = dict(r or {})
        key = (row.get("employee_id"), str(row.get("store") or "").strip())
        paid_actual = carries_pay(row, ACTUAL_PAY)
        paid_sched = carries_pay(row, SCHEDULED_PAY)

        if paid_actual:
            legs = act_tax.get(key) or {}
            tax = round(float(legs.get("total") or 0.0), 2)
            pay = round(float(row.get(ACTUAL_PAY) or 0), 2)
            for leg in TAX_LEGS:
                row[f"tax_{leg}"] = round(float(legs.get(leg) or 0.0), 2)
                totals[f"tax_{leg}"] = round(totals[f"tax_{leg}"] + row[f"tax_{leg}"], 2)
            row["employer_tax"] = tax
            row["employer_cost"] = round(pay + tax, 2)
            totals["actual_pay"] = round(totals["actual_pay"] + pay, 2)
            totals["employer_tax"] = round(totals["employer_tax"] + tax, 2)
            totals["employer_cost"] = round(totals["employer_cost"] + row["employer_cost"], 2)
            totals["counted_rows"] += 1

        if paid_sched:
            legs = sch_tax.get(key) or {}
            tax = round(float(legs.get("total") or 0.0), 2)
            pay = round(float(row.get(SCHEDULED_PAY) or 0), 2)
            row["employer_tax_scheduled"] = tax
            row["employer_cost_scheduled"] = round(pay + tax, 2)
            totals["scheduled_pay"] = round(totals["scheduled_pay"] + pay, 2)
            totals["employer_tax_scheduled"] = round(totals["employer_tax_scheduled"] + tax, 2)
            totals["employer_cost_scheduled"] = round(
                totals["employer_cost_scheduled"] + row["employer_cost_scheduled"], 2)

        out_rows.append(row)

    return {"rows": out_rows, "totals": totals, "tax_enabled": enabled}
