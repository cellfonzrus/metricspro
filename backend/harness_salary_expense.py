"""Proof harness — salary -> store expenses, THREE-STATE hours (owner directive 2026-09-08:
"pull the exact salaries paid as per the schedule and update the same in the expenses as those are
not getting updated for a lot of stores … if we have the actual hours then the salary is based on
actual hours if not then the salary is based on scheduled hours for that month").

DB-free, pure stdlib. Proves backend/app/modules/storeops/salary_expense.py:

  1. THE THREE STATES, and which ONE triggers the scheduled-hours fallback:
       measured-and-nonzero -> pay the measurement, no fallback;
       measured-and-ZERO    -> pay 0, NO FALLBACK (a measured zero is a fact, not a gap);
       NOT measured         -> and only here, fall back to that day's scheduled hours.
  2. `shifts.actual_hours == 0` is NOT evidence of measurement.
  3. Store-code folding: case/alias variants of ONE store collapse onto one canonical code; a code
     that folds nowhere is REPORTED, never booked to an invented store and never dropped.
  4. The WITHHOLD rule: a store-month with no measured hours AND no schedule is never booked as
     $0.00 — it is reported with a reason.
  5. Config is config (RULE TWO): the line label and the fallback policy come from per-org rows over
     house defaults; no tenant name appears anywhere.

REGRESSIONS reproducing the REPORTED live defect (stores whose salary is not updating) — measured
2026-09-08 against the live Luxelink tenant, org 854f6d7b-…-646f560d4f4c, Jul+Aug 2026:

  R1  "actual_hours is 0 on 1,486 of 1,486 shift rows while 1,146 CLOSED punches exist." A reader
      that treats `actual_hours` as the measurement sees a tenant with NO actual hours at all. The
      punches are the measurement; the harness pins that the punch wins and the day is MEASURED.
  R2  Store-code drift: 63 distinct raw shift/timelog store codes for 20 real stores; only 36 bind
      exactly. 'CICERO'+'Cicero' are one store whose payroll was being split in two, and codes like
      '3248 LAWARANCE' (493.1h / $9,123.09 in Aug alone) bound to NOTHING.
  R3  Three July stores (3352 26th, 3735 26th, Chicago heights) had NO 'Employee Salaries' row at
      all while carrying 250.8h / 246.7h / 377.9h of MEASURED punch hours — $16,290.15 of real
      payroll missing from July gross profit. Pinned as: a measured store is ALWAYS booked.
  R4  A store-month with no evidence at all must not be flattened to $0.00 — pinned as withheld.
  R5  A SALARIED employee's `pay_rate` holds PER-PERIOD pay, not an hourly rate (live: 9 scheduled
      hours x $3,692.30 = $33,230.70 of phantom wages at one store). Pinned as: this engine consumes
      the already-derived per-store amount and never multiplies hours by a rate itself.

Run: python3 backend/harness_salary_expense.py   (exit 0 = all proofs hold)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.storeops.salary_expense import (   # noqa: E402
    MEASURED_NONZERO, MEASURED_ZERO, NOT_MEASURED,
    STATE_MEASURED, STATE_MIXED, STATE_SCHEDULED, STATE_NO_DATA,
    DEFAULT_CONFIG, resolve_config, day_measurement, hours_for_shift,
    build_store_folder, fold_store_rows, store_state, build_salary_expense, ledger_rows,
)

FAIL = 0
CHECKS = 0


def check(name, cond):
    global FAIL, CHECKS
    CHECKS += 1
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAIL += 1


# ── 1. the three states ──────────────────────────────────────────────────────────────────────────
print("1. THREE states of 'actual hours' — and which one falls back to the schedule")
PUNCH = {"E1": {"2026-08-03": 7.5, "2026-08-04": 0.0}}      # 04: clocked in, measured ZERO
MANUAL = {"E1": {"2026-08-05": 6.0}}                        # a DM correction

check("measured & nonzero -> MEASURED_NONZERO, 7.5h",
      day_measurement("E1", "2026-08-03", MANUAL, PUNCH) == (MEASURED_NONZERO, 7.5))
check("measured & ZERO -> MEASURED_ZERO (NOT 'not measured')",
      day_measurement("E1", "2026-08-04", MANUAL, PUNCH) == (MEASURED_ZERO, 0.0))
check("no punch, no correction -> NOT_MEASURED",
      day_measurement("E1", "2026-08-06", MANUAL, PUNCH) == (NOT_MEASURED, 0.0))
check("a manual correction outranks a punch on the same day",
      day_measurement("E1", "2026-08-05", MANUAL, {"E1": {"2026-08-05": 99.0}}) == (MEASURED_NONZERO, 6.0))
check("an unknown employee is NOT_MEASURED, never zero-by-default",
      day_measurement("E9", "2026-08-03", MANUAL, PUNCH) == (NOT_MEASURED, 0.0))

print("2. the fallback is triggered by NOT_MEASURED ONLY")
sched8 = {"employee_id": "E1", "shift_date": "2026-08-03", "scheduled_hours": 8.0, "actual_hours": 0}
check("measured-nonzero day: the shift contributes 0 (the punch carries the hours, at its own store)",
      hours_for_shift(sched8, MANUAL, PUNCH) == (0.0, MEASURED_NONZERO))
zero_day = dict(sched8, shift_date="2026-08-04")
check("R-CRITICAL measured-ZERO day: 0 hours and NO fallback to the 8 scheduled hours",
      hours_for_shift(zero_day, MANUAL, PUNCH) == (0.0, MEASURED_ZERO))
gap_day = dict(sched8, shift_date="2026-08-06")
check("NOT-measured day: falls back to the 8.0 SCHEDULED hours (the owner's rule)",
      hours_for_shift(gap_day, MANUAL, PUNCH) == (8.0, NOT_MEASURED))
check("a not-measured day with no schedule contributes 0 hours, still labelled NOT_MEASURED",
      hours_for_shift(dict(gap_day, scheduled_hours=0), MANUAL, PUNCH) == (0.0, NOT_MEASURED))

print("3. REGRESSION R1 — 'actual_hours == 0' is NOT evidence of measurement (live Luxelink shape)")
# Every shift row carries actual_hours = 0; the measurement lives entirely in the punch feed.
lux_shifts = [{"employee_id": "E1", "shift_date": "2026-08-%02d" % d,
               "scheduled_hours": 8.0, "actual_hours": 0} for d in (3, 4, 6)]
provs = [hours_for_shift(s, MANUAL, PUNCH)[1] for s in lux_shifts]
check("with actual_hours 0 everywhere the three days still classify differently",
      provs == [MEASURED_NONZERO, MEASURED_ZERO, NOT_MEASURED])
check("a two-state reader ('act if act>0 else sched') would have paid the schedule on ALL THREE",
      all(float(s["actual_hours"] or 0) == 0 for s in lux_shifts))
check("…and on the measured-ZERO day that would have invented 8 phantom hours",
      hours_for_shift(lux_shifts[1], MANUAL, PUNCH)[0] == 0.0)
check("with NO punch feed at all every day is NOT_MEASURED -> the schedule stands in (owner's rule)",
      [hours_for_shift(s, {}, {})[1] for s in lux_shifts] == [NOT_MEASURED] * 3)

# ── 4. store-code folding ────────────────────────────────────────────────────────────────────────
print("4. REGRESSION R2 — store-code folding (63 raw codes / 20 real stores; only 36 bound exactly)")
ROSTER = ["Cicero", "Cermark", "lawrence", "3352 26th", "Lefferts"]
ALIASES = {"3248 lawrence chicago": "lawrence", "2317 cicero cicero": "Cicero"}
fold = build_store_folder(ROSTER, lambda s: ALIASES.get(str(s).strip().lower(), s))
check("exact roster spelling binds exactly", fold("Cicero") == ("Cicero", "exact"))
check("R2 UPPERCASE variant folds onto the roster spelling",
      fold("CICERO")[0] == "Cicero" and fold("CICERO")[1] != "exact")
check("R2 uppercase '3352 26TH' folds onto '3352 26th'",
      fold("3352 26TH")[0] == "3352 26th" and fold("3352 26TH")[1] != "exact")
check("R2 the fold is reported as a fold, never as an exact bind",
      fold("CICERO")[1] in ("case_fold", "resolver+case_fold"))
check("an address resolves through the platform's existing resolver",
      fold("3248 Lawrence Chicago")[0] == "lawrence")
check("R2 '3248 LAWARANCE' binds to NOTHING and is reported, not guessed",
      fold("3248 LAWARANCE") == (None, "unbound"))
check("a blank store string is never booked", fold("   ") == (None, "blank"))

rows = [{"store_code": "CICERO", "hours": 236.7, "amount": 4887.74},
        {"store_code": "Cicero", "hours": 49.0, "amount": 835.45},
        {"store_code": "3248 LAWARANCE", "hours": 493.1, "amount": 9123.09},
        {"store_code": "Lefferts", "hours": 10.5, "amount": 157.95}]
by_code, unbound = fold_store_rows(rows, fold)
check("R2 'CICERO' + 'Cicero' collapse onto ONE store (a store was being shown half its payroll)",
      set(by_code) == {"Cicero", "Lefferts"})
check("…and its amount is the SUM, to the cent", by_code["Cicero"]["amount"] == 5723.19)
check("…and both raw spellings are recorded so the fold is auditable",
      by_code["Cicero"]["raw_codes"] == ["CICERO", "Cicero"] and by_code["Cicero"]["folded"] is True)
check("R2 the unbound code is reported WITH its money, never silently dropped",
      len(unbound) == 1 and unbound[0]["store_code"] == "3248 LAWARANCE"
      and unbound[0]["amount"] == 9123.09 and unbound[0]["hours"] == 493.1)
check("…and it is NOT booked to any store", "3248 LAWARANCE" not in by_code)

# ── 5. store-month rollup states ─────────────────────────────────────────────────────────────────
print("5. store-month state")
check("all measured -> measured", store_state(120.0, 0.0) == STATE_MEASURED)
check("some of each -> mixed", store_state(120.0, 8.0) == STATE_MIXED)
check("nothing measured all month -> scheduled_fallback", store_state(0.0, 40.0) == STATE_SCHEDULED)
check("neither -> no_data (NOT 'zero')", store_state(0.0, 0.0) == STATE_NO_DATA)

# ── 6. the booking decision ──────────────────────────────────────────────────────────────────────
print("6. REGRESSIONS R3 + R4 — what gets booked, and what is withheld and reported")
BY_CODE = {
    # R3: measured all month, and (live) had NO salary row on the sheet at all.
    "3352 26th":       {"store_code": "3352 26th", "hours": 250.8, "amount": 5266.17, "raw_codes": ["3352 26TH", "3352 26th"], "folded": True},
    "Chicago heights": {"store_code": "Chicago heights", "hours": 377.9, "amount": 6686.31, "raw_codes": ["Chicago heights"], "folded": False},
    # the owner's fallback case: nothing measured, a schedule exists.
    "Cermark":         {"store_code": "Cermark", "hours": 28.0, "amount": 511.57, "raw_codes": ["Cermark"], "folded": False},
    # mixed.
    "Cicero":          {"store_code": "Cicero", "hours": 285.7, "amount": 5723.19, "raw_codes": ["CICERO", "Cicero"], "folded": True},
}
SPLIT = {
    "3352 26th":       {"measured_hours": 250.8, "scheduled_hours": 0.0},
    "Chicago heights": {"measured_hours": 377.9, "scheduled_hours": 0.0},
    "Cermark":         {"measured_hours": 0.0,   "scheduled_hours": 28.0},
    "Cicero":          {"measured_hours": 236.7, "scheduled_hours": 49.0},
}
# 'Lefferts' is on the roster but has NO payroll rows and NO schedule at all this period.
FULL_ROSTER = ["3352 26th", "Chicago heights", "Cermark", "Cicero", "Lefferts"]
res = build_salary_expense(BY_CODE, SPLIT, FULL_ROSTER, None, unbound)
booked = {c["store"]: c["amount"] for c in res["cells"]}
states = {s["store_code"]: s["hours_state"] for s in res["stores"]}

check("R3 a MEASURED store is always booked — $5,266.17 reaches the month's expenses",
      booked.get("3352 26th") == 5266.17)
check("R3 the second measured store is booked too", booked.get("Chicago heights") == 6686.31)
check("the owner's fallback: an unmeasured store books its SCHEDULED figure",
      booked.get("Cermark") == 511.57 and states["Cermark"] == STATE_SCHEDULED)
check("a mixed store books, and is flagged mixed",
      booked.get("Cicero") == 5723.19 and states["Cicero"] == STATE_MIXED)
check("R4 a store with NO evidence is NOT in the cells (never pushed as $0.00)",
      "Lefferts" not in booked)
check("R4 …its state is no_data, not a zero amount", states["Lefferts"] == STATE_NO_DATA)
check("R4 …and it is REPORTED with a reason the reader can act on",
      any(w["store_code"] == "Lefferts" and "data gap" in (w.get("withheld_reason") or "")
          for w in res["withheld"]))
check("a roster store with no rows still appears in `stores` (it cannot go missing)",
      "Lefferts" in states)
check("totals: booked = the four cells, to the cent",
      res["totals"]["booked"] == round(5266.17 + 6686.31 + 511.57 + 5723.19, 2))
check("totals: the unbound money is carried through, separately from booked",
      res["totals"]["unbound"] == 9123.09 and res["totals"]["unbound_hours"] == 493.1)
check("totals: one scheduled-fallback store and one no-data store are counted for the reader",
      res["totals"]["stores_scheduled_fallback"] == 1 and res["totals"]["stores_no_data"] == 1)
check("no cell ever carries a None/negative amount",
      all(isinstance(c["amount"], float) and c["amount"] >= 0 for c in res["cells"]))

print("7. REGRESSION R5 — this engine never multiplies hours by a rate")
check("a store's booked amount is EXACTLY the per-store figure handed in (salaried pay-basis is "
      "payroll_salary.py's job, upstream — 9h x a $3,692.30 per-period pay_rate is never computed here)",
      booked.get("Cicero") == BY_CODE["Cicero"]["amount"])
check("the engine exposes no rate or hours-times-rate path at all",
      not any("rate" in n for n in dir(__import__(
          "app.modules.storeops.salary_expense", fromlist=["x"]))))

# ── 8. config, never code ────────────────────────────────────────────────────────────────────────
print("8. RULE TWO — config, never code")
check("house default label is 'Gross Payroll'", DEFAULT_CONFIG["line_label"] == "Gross Payroll")
check("no-row org == house defaults", resolve_config(None) == resolve_config({}))
cfg = resolve_config({"line_label": "Employee Salaries"})
check("a per-org row renames the line without touching code", cfg["line_label"] == "Employee Salaries")
check("a partial row keeps every other house default",
      cfg["book_scheduled_fallback"] is True and cfg["expense_type"] == "Fixed")
off = build_salary_expense(BY_CODE, SPLIT, FULL_ROSTER, {"book_scheduled_fallback": False}, [])
check("an org that opts out of unmeasured estimates withholds the scheduled-fallback store",
      all(c["store"] != "Cermark" for c in off["cells"])
      and any(w["store_code"] == "Cermark" for w in off["withheld"]))
check("…and still books every MEASURED store (the opt-out is narrow)",
      {c["store"] for c in off["cells"]} == {"3352 26th", "Chicago heights", "Cicero"})
check("book_no_data_as_zero is FALSE and stays false under a partial row",
      resolve_config({"line_label": "x"})["book_no_data_as_zero"] is False)
check("a blank label falls back to the house default, never to an empty expense name",
      resolve_config({"line_label": "   "})["line_label"] == "Gross Payroll")

# ── 9. the ledger carries the truth store_expenses cannot ────────────────────────────────────────
print("9. the audit ledger persists the three-state truth (store_expenses cannot represent 'unknown')")
lrows = ledger_rows("ORG", "2026-08", res, run_by="tester")
by_store = {r["store"]: r for r in lrows}
check("every roster store gets a ledger row, booked or not", len(lrows) == 5)
check("the withheld store is persisted with booked=false and wages 0 — a REPORT, not a $0 expense",
      by_store["Lefferts"]["booked"] is False and by_store["Lefferts"]["wages"] == 0
      and by_store["Lefferts"]["hours_state"] == STATE_NO_DATA)
check("a booked store persists its measured/scheduled split",
      by_store["Cicero"]["measured_hours"] == 236.7 and by_store["Cicero"]["scheduled_hours"] == 49.0)
check("the raw spellings that folded onto a code are persisted for audit",
      by_store["Cicero"]["raw_store_codes"] == "CICERO, Cicero")
check("every row is org-stamped and period-stamped",
      all(r["org_id"] == "ORG" and r["period"] == "2026-08" for r in lrows))

print()
print("checks run: %d   failures: %d" % (CHECKS, FAIL))
print("ALL PROOFS HOLD" if not FAIL else "PROOFS FAILED")
sys.exit(1 if FAIL else 0)
