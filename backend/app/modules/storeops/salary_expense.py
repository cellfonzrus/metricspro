"""storeops Salary Expense engine — pure, DB-free functions (proof: backend/harness_salary_expense.py).

OWNER DIRECTIVE 2026-09-08 (verbatim): "then we need to pull the exact salaries paid as per the
schedule and update the same in the expenses as those are not getting updated for a lot of stores,
if we have the actual hours then the salary is based on actual hours if not then the salary is based
on scheduled hours for that month to go in the gross profit for that month".

WHAT THIS MODULE IS AND IS NOT
------------------------------
It is NOT a second salary derivation. The platform already has exactly one per-store salary figure —
`storeops/router.py::get_payroll_by_store` (punch-driven pay + manual corrections + scheduled
fallback + `payroll_salary.py`'s salaried pay-basis allocation + lunch deduction + inactive-employee
handling). This module consumes THAT figure. What it adds is the part the write path was missing:

  1. THREE-STATE HOURS PROVENANCE (the owner's hardest rule). "Actual hours" is not a boolean.
  2. STORE-CODE CANONICALIZATION before a dollar is booked to a store.
  3. A WITHHOLD RULE so a store-month with no payroll evidence is REPORTED, never booked as $0.00.

THE THREE STATES (and which one triggers the scheduled-hours fallback)
---------------------------------------------------------------------
Evidence is per (employee, calendar day) — the same evidence maps the pay path already builds in
`router._punch_driven_day_maps` (manual_days = a shift with actual_hours > 0, i.e. a human DM
correction; punch_days = a CLOSED timelog punch, clock_out set AND hours not null):

  MEASURED_NONZERO  a closed punch or a manual correction exists AND its hours are > 0.
                    -> pay those hours. No fallback.
  MEASURED_ZERO     a closed punch or a manual correction exists for that day and its hours are 0
                    (the store was closed, the employee clocked in and straight back out, the day was
                    zeroed by a DM). THE MEASUREMENT SAYS ZERO, SO ZERO IS THE TRUTH.
                    -> pay 0. **NO FALLBACK TO SCHEDULED.** Falling back here would invent wages for
                    a day somebody measured as not worked.
  NOT_MEASURED      no punch and no manual correction for that (employee, day) at all — no time
                    clock at that store, an approval never filed, a feed gap.
                    -> and ONLY here: fall back to that day's SCHEDULED hours.

So: **the scheduled-hours fallback is triggered by NOT_MEASURED, never by a measured zero.**

`shifts.actual_hours == 0` IS NOT EVIDENCE OF MEASUREMENT. That column is 0 (never NULL) on every one
of the live Luxelink tenant's 1,486 Jul+Aug 2026 shift rows while 1,146 CLOSED punches exist in
`storeops.timelog` for the same months — a column that is always 0 cannot distinguish "worked zero"
from "nobody wrote a number here". Reading `actual_hours` alone and treating 0 as "not measured" is
the two-state mistake this module exists to prevent; the punch/manual maps are the measurement.

STORE-MONTH ROLLUP STATE (what the reader is shown)
---------------------------------------------------
  measured            every paid hour at this store came from a measurement.
  mixed               some measured, some scheduled-fallback. The amount is still booked; the reader
                      is told how much of it is an estimate.
  scheduled_fallback  nothing at this store was measured all month; the whole figure is the schedule.
                      Booked (that is the owner's rule) and FLAGGED — an entire unmeasured store-month
                      is a time-clock/approval gap worth reporting.
  no_data             no measurement AND no schedule. There is nothing to derive a salary from.
                      WITHHELD — never booked, never rendered as $0.00, always reported. A store
                      showing $0 salary because nobody clocked in is a DATA DEFECT, not a number.

WITHHELD IS NOT ZERO. `commcalc.store_expenses` cannot represent "unknown" (the system-line receiver
drops zero-amount cells), so a withheld store-month is carried in the RUN RESULT and in the ledger
(`storeops.payroll_gross_ledger.hours_state`, migration 435) instead of being flattened into a cell.

RULE TWO. Nothing here is tenant-specific. The line label, the expense type and whether an
all-unmeasured store-month is still booked are per-org CONFIG rows merged over the house defaults
below; no tenant, carrier or store name appears in this file.
"""
from typing import Callable, Dict, Iterable, List, Optional, Tuple

# ── per-(employee, day) hours provenance ─────────────────────────────────────────────────────────
MEASURED_NONZERO = "measured_nonzero"
MEASURED_ZERO = "measured_zero"
NOT_MEASURED = "not_measured"

# ── per-store-month rollup state ─────────────────────────────────────────────────────────────────
STATE_MEASURED = "measured"
STATE_MIXED = "mixed"
STATE_SCHEDULED = "scheduled_fallback"
STATE_NO_DATA = "no_data"

#: Store-month states that are BOOKED to expenses. `no_data` is deliberately absent — see module docstring.
BOOKABLE_STATES = (STATE_MEASURED, STATE_MIXED, STATE_SCHEDULED)

# ── config (RULE TWO — house defaults, per-org rows override; storeops.salary_expense_config, mig 435) ──
DEFAULT_CONFIG = {
    "enabled": True,
    # The expense_name the system line carries on the Expenses sheet / in the P&L.
    "line_label": "Gross Payroll",
    "expense_type": "Fixed",
    # Book a store-month whose hours are ENTIRELY scheduled fallback? True = the owner's rule
    # ("if not [actual] then the salary is based on scheduled hours"). A tenant that would rather see
    # nothing than an unmeasured estimate can turn this off per org.
    "book_scheduled_fallback": True,
    # NEVER a config knob in the house default: booking `no_data` as $0.00 would paper over a defect.
    # Left here explicitly so the answer is visible rather than implied.
    "book_no_data_as_zero": False,
}
_CONFIG_FIELDS = tuple(DEFAULT_CONFIG.keys())


def resolve_config(org_row: Optional[dict] = None) -> dict:
    """Merge a (possibly partial / absent) per-org config row over the house defaults. Same shape and
    posture as payroll_expenses.resolve_tax_config — an org with no row behaves exactly as the house
    default, so a tenant that never configures anything still gets the owner's rule."""
    eff = dict(DEFAULT_CONFIG)
    for f in _CONFIG_FIELDS:
        v = (org_row or {}).get(f)
        if v is not None:
            eff[f] = v
    eff["enabled"] = bool(eff["enabled"])
    eff["book_scheduled_fallback"] = bool(eff["book_scheduled_fallback"])
    eff["book_no_data_as_zero"] = bool(eff["book_no_data_as_zero"])
    eff["line_label"] = str(eff["line_label"] or DEFAULT_CONFIG["line_label"]).strip() or DEFAULT_CONFIG["line_label"]
    eff["expense_type"] = str(eff["expense_type"] or "Fixed").strip() or "Fixed"
    return eff


# ── 1. the three-state classifier ────────────────────────────────────────────────────────────────
def day_measurement(employee_id, day: str,
                    manual_hours_by_day: Dict[object, Dict[str, float]],
                    punch_hours_by_day: Dict[object, Dict[str, float]]) -> Tuple[str, float]:
    """(state, measured_hours) for one (employee, day).

    manual_hours_by_day / punch_hours_by_day: {employee_id: {'YYYY-MM-DD': hours}} — a day is present
    IFF a measurement exists for it, whatever its hours are (INCLUDING 0.0). Presence is the
    measurement; the value is only the number. A manual correction outranks a punch (the pay path's
    long-standing rule: a human DM correction always wins).

    Returns MEASURED_ZERO — not NOT_MEASURED — for a present-but-zero measurement, which is the whole
    point of this function: only NOT_MEASURED may fall back to the schedule."""
    d = str(day or "")[:10]
    m = (manual_hours_by_day or {}).get(employee_id) or {}
    if d in m:
        h = float(m[d] or 0)
        return (MEASURED_NONZERO if h > 0 else MEASURED_ZERO), h
    p = (punch_hours_by_day or {}).get(employee_id) or {}
    if d in p:
        h = float(p[d] or 0)
        return (MEASURED_NONZERO if h > 0 else MEASURED_ZERO), h
    return NOT_MEASURED, 0.0


def hours_for_shift(shift: dict,
                    manual_hours_by_day: Dict[object, Dict[str, float]],
                    punch_hours_by_day: Dict[object, Dict[str, float]]) -> Tuple[float, str]:
    """(hours_this_shift_contributes, provenance) for ONE scheduled shift row.

    Mirrors `router._shift_actual_contribution`'s pay arithmetic exactly — a measured day contributes
    0 FROM THE SHIFT because the measurement itself is counted separately at the store it happened at
    (a floater's punch belongs to the store they actually stood in, not the store they were pencilled
    into) — and additionally NAMES the provenance so the caller can roll it up and show the reader.

    The only behavioural change versus reading `actual_hours` alone: a measured-ZERO day contributes
    0 and is labelled MEASURED_ZERO instead of silently falling through to `scheduled_hours`."""
    eid = shift.get("employee_id")
    day = str(shift.get("shift_date") or "")[:10]
    state, _h = day_measurement(eid, day, manual_hours_by_day, punch_hours_by_day)
    if state == NOT_MEASURED:
        return float(shift.get("scheduled_hours") or 0), NOT_MEASURED
    return 0.0, state


# ── 2. store-code canonicalization ───────────────────────────────────────────────────────────────
def build_store_folder(roster: Iterable[str], resolve: Optional[Callable[[str], str]] = None):
    """fold(raw_store_string) -> (canonical_store_code | None, how).

    `roster` is the org's `storeops.stores.store_code` set — the ONLY codes the Expenses sheet renders,
    so a dollar that does not fold onto one of them is money that lands nowhere. `resolve` is the
    platform's existing canonical resolver (`commcalc.router._store_code_resolver`: store_aliases →
    store_mapping address → storeops address → already-a-code). It is used AS-IS and first; this
    function only adds the step that resolver deliberately does not take — an EXACT-CASE fold onto the
    roster spelling, because `_store_code_resolver` returns the caller's own casing for a
    already-a-code hit and `store_expenses.store_code` is matched by exact string in the UI.

    Returns (None, 'unbound') when nothing folds. UNBOUND IS REPORTED, NEVER GUESSED: an unrecognised
    store string is a store-setup defect (a missing alias, a renamed store), and booking its wages to
    an invented code — or silently dropping them — is how a store's whole payroll disappears."""
    by_upper: Dict[str, str] = {}
    for c in roster or []:
        c = str(c or "").strip()
        if c:
            by_upper.setdefault(c.upper(), c)

    def fold(raw) -> Tuple[Optional[str], str]:
        s = str(raw or "").strip()
        if not s:
            return None, "blank"
        if s in by_upper.values() or by_upper.get(s.upper()) == s:
            return by_upper[s.upper()], "exact"
        if resolve is not None:
            try:
                r = str(resolve(s) or "").strip()
            except Exception:
                r = ""
            if r:
                hit = by_upper.get(r.upper())
                if hit:
                    return hit, ("exact" if r == hit else "resolver+case_fold")
        hit = by_upper.get(s.upper())
        if hit:
            return hit, "case_fold"
        return None, "unbound"

    return fold


def fold_store_rows(rows: List[dict], fold, amount_key: str = "amount",
                    code_key: str = "store_code") -> Tuple[Dict[str, dict], List[dict]]:
    """Collapse per-store rows onto canonical codes. Returns (by_code, unbound).

    Two rows for the SAME store under different spellings ('CICERO' and 'Cicero') are SUMMED onto one
    canonical code — the drift that otherwise shows a store half its own payroll. `unbound` carries
    every row that folded nowhere, with its raw code, so the run reports it instead of losing it."""
    by_code: Dict[str, dict] = {}
    unbound: List[dict] = []
    for r in rows or []:
        raw = r.get(code_key)
        code, how = fold(raw)
        amt = float(r.get(amount_key) or 0)
        hrs = float(r.get("hours") or 0)
        if code is None:
            unbound.append({"store_code": str(raw or "").strip(), "hours": round(hrs, 2),
                            "amount": round(amt, 2), "reason": how})
            continue
        d = by_code.setdefault(code, {"store_code": code, "hours": 0.0, "amount": 0.0,
                                      "raw_codes": [], "folded": False})
        d["hours"] += hrs
        d["amount"] += amt
        raw_s = str(raw or "").strip()
        if raw_s not in d["raw_codes"]:
            d["raw_codes"].append(raw_s)
        if how != "exact":
            d["folded"] = True
    for d in by_code.values():
        d["hours"] = round(d["hours"], 2)
        d["amount"] = round(d["amount"], 2)
        d["raw_codes"].sort()
    return by_code, unbound


# ── 3. store-month rollup state ──────────────────────────────────────────────────────────────────
def store_state(measured_hours: float, scheduled_hours: float) -> str:
    """The reader-facing state for one store-month. `measured_hours` counts only hours that came from
    a measurement (a punch or a manual correction); `scheduled_hours` counts only hours that stood in
    for a NOT_MEASURED day. A store with neither is `no_data` — NOT zero."""
    m = float(measured_hours or 0)
    s = float(scheduled_hours or 0)
    if m > 0 and s > 0:
        return STATE_MIXED
    if m > 0:
        return STATE_MEASURED
    if s > 0:
        return STATE_SCHEDULED
    return STATE_NO_DATA


def build_salary_expense(by_code: Dict[str, dict],
                         hours_split: Dict[str, dict],
                         roster: Iterable[str],
                         cfg: dict,
                         unbound: Optional[List[dict]] = None) -> dict:
    """The engine's answer for one period.

    by_code:     {canonical_store_code: {'store_code','hours','amount', ...}} — the CANONICAL per-store
                 salary figure (from get_payroll_by_store, folded by fold_store_rows).
    hours_split: {canonical_store_code: {'measured_hours':…, 'scheduled_hours':…}} — the provenance
                 split for the same stores.
    roster:      every store_code in the org, so a store with NO payroll rows at all is reported as
                 `no_data` rather than being invisible.

    Returns {cells, stores, withheld, unbound, totals}:
      cells    — what to push to POST /commcalc/expenses/{period}/system-line. BOOKABLE states only.
      stores   — one entry per store_code in the roster (plus any folded code outside it), each with
                 amount, hours, the measured/scheduled split and its state, for the reader.
      withheld — the store-months deliberately NOT booked, each with a reason. THIS IS THE REPORT.
      unbound  — wages whose raw store string folded onto no store at all.
    """
    cfg = resolve_config(cfg if isinstance(cfg, dict) else None)
    codes = {str(c or "").strip() for c in (roster or []) if str(c or "").strip()}
    codes |= set(by_code or {})
    stores, cells, withheld = [], [], []
    booked_total = withheld_total = 0.0
    for code in sorted(codes):
        row = (by_code or {}).get(code) or {}
        split = (hours_split or {}).get(code) or {}
        measured = float(split.get("measured_hours") or 0)
        scheduled = float(split.get("scheduled_hours") or 0)
        amount = round(float(row.get("amount") or 0), 2)
        state = store_state(measured, scheduled)
        entry = {"store_code": code, "amount": amount,
                 "hours": round(float(row.get("hours") or 0), 2),
                 "measured_hours": round(measured, 2), "scheduled_hours": round(scheduled, 2),
                 "hours_state": state, "raw_codes": list(row.get("raw_codes") or []),
                 "folded": bool(row.get("folded"))}
        book = state in BOOKABLE_STATES
        if state == STATE_SCHEDULED and not cfg["book_scheduled_fallback"]:
            book = False
            entry["withheld_reason"] = ("no hours were measured at this store all period and this org "
                                        "is configured not to book an unmeasured estimate")
        elif state == STATE_NO_DATA:
            book = False
            entry["withheld_reason"] = ("no measured hours and no schedule for this store this period "
                                        "— there is nothing to derive a salary from. Reported, not "
                                        "booked as $0.00: a store with no payroll evidence is a data "
                                        "gap (no time clock, no schedule uploaded), not a store that "
                                        "spent nothing on wages.")
        entry["booked"] = book
        stores.append(entry)
        if book:
            cells.append({"store": code, "amount": amount})
            booked_total += amount
        else:
            withheld.append(entry)
            withheld_total += amount
    ub = list(unbound or [])
    return {
        "cells": cells,
        "stores": stores,
        "withheld": withheld,
        "unbound": ub,
        "config": cfg,
        "totals": {
            "booked": round(booked_total, 2),
            "withheld": round(withheld_total, 2),
            "unbound": round(sum(float(u.get("amount") or 0) for u in ub), 2),
            "unbound_hours": round(sum(float(u.get("hours") or 0) for u in ub), 2),
            "stores_booked": len(cells),
            "stores_withheld": len(withheld),
            "stores_scheduled_fallback": sum(1 for s in stores if s["hours_state"] == STATE_SCHEDULED),
            "stores_no_data": sum(1 for s in stores if s["hours_state"] == STATE_NO_DATA),
        },
    }


def ledger_rows(org_id: str, period: str, result: dict, run_by: Optional[str] = None) -> List[dict]:
    """One row per (org, period, store) for `storeops.payroll_gross_ledger` — the audit trail of what
    this run decided, INCLUDING the store-months it deliberately withheld (`booked=false`), which is
    the only place the three-state truth can be persisted: `commcalc.store_expenses` has no way to say
    "unknown" (its receiver drops zero-amount cells), so a withheld store would otherwise vanish.

    `measured_hours` / `scheduled_hours` / `hours_state` / `booked` / `raw_store_codes` are the
    migration-435 columns; a pre-435 database simply rejects the insert and the router reports the
    ledger as skipped exactly as it already does for a pre-405 database."""
    rows = []
    for s in result.get("stores") or []:
        rows.append({
            "org_id": org_id, "period": period, "store": s["store_code"],
            "wages": s["amount"] if s.get("booked") else 0,
            "headcount": int(s.get("headcount") or 0),
            "measured_hours": s.get("measured_hours", 0),
            "scheduled_hours": s.get("scheduled_hours", 0),
            "hours_state": s.get("hours_state"),
            "booked": bool(s.get("booked")),
            "raw_store_codes": ", ".join(s.get("raw_codes") or []) or None,
            "run_by": run_by,
        })
    return rows
