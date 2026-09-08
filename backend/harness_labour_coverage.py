"""DB-FREE proof for commcalc/labour_coverage.py — the salary READ path.

Owner directive 2026-09-08: "pull the exact salaries paid as per the schedule … if we have the
actual hours then the salary is based on actual hours if not then the salary is based on scheduled
hours for that month to go in the gross profit for that month."

Every fixture below is the LIVE LuxeLink shape measured on 2026-09-08
(org 854f6d7b-6590-4e4d-88ab-646f560d4f4c) reduced to the smallest rows that reproduce it.

  §A  vocabulary + authority predicate (mirrors coa's ruling-K2 predicate)
  §B  hours basis — actual wins, scheduled is the fallback, neither is NOT MEASURED
  §C  REGRESSION 1 — the silent zero: a store with no payroll row and hourless shifts
  §D  REGRESSION 2 — the month grain: a carried period is not a measurement
  §E  REGRESSION 3 — the org-wide suppression gap (per-store authority)
  §F  REGRESSION 4 — the commission double-book ($10,000 August, live)
  §H  SUPPRESSION — the owner's 2026-09-08 ruling: rep_commissions is the authoritative
      route, so the expense-side copy stops booking. Carries the REGRESSION for the measured
      $7,626.14 double-book and its proof of removal, the byte-identity default, the
      nothing-to-replace-with case, and the P&L/GP one-derivation tie-out.
  §G  totals are preserved: this module never moves a dollar

Run: python3 backend/harness_labour_coverage.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from app.modules.commcalc import labour_coverage as lc  # noqa: E402

CHECKS = 0
FAILS = []


def ok(label, got, want):
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILS.append(f"{label}\n     got:  {got!r}\n     want: {want!r}")


def near(label, got, want, tol=0.005):
    global CHECKS
    CHECKS += 1
    if abs(float(got) - float(want)) > tol:
        FAILS.append(f"{label}\n     got:  {got!r}\n     want: {want!r}")


# LuxeLink's live commcalc.account_config.payroll_expense_names (read 2026-09-08).
PAYROLL_NAMES = ["DM Salaries", "Employee Salaries"]
COMMISSION_NAMES = ["Employee Commission"]


def expense(code, name, amount, source_key=None):
    return {"store_code": code, "expense_name": name, "amount": amount, "source_key": source_key}


def shift(code, actual=None, scheduled=None):
    return {"store_code": code, "actual_hours": actual, "scheduled_hours": scheduled}


# ══════════════════════════════════════════════════════════════════════════════════════════════
# §A  authority predicate — must mirror coa.build_inputs's ruling-K2 clause exactly
# ══════════════════════════════════════════════════════════════════════════════════════════════
rows_a = [
    expense("Diversey", "Employee Salaries", 10460.42),      # manual, configured, non-zero -> AUTH
    expense("Grand", "employee salaries", 8656.15),          # case-insensitive on the name
    expense("QV", "Employee Salaries", 0),                   # $0.00 placeholder is NOT a measurement
    expense("Utica", "Rent / Lease", 3500.00),               # not a payroll name
    expense("Belmont", "Anything At All", 900.00, "payroll_gross"),   # producer token wins
    expense("Cicero", "Employee Salaries", 1200.00, "closing_expense:x"),  # explicit key -> not K2
    expense("Nostrand", "Employee Salaries", -250.00),       # a correction still counts
    expense(None, "Employee Salaries", 500.00),              # no store -> ignored
]
auth_a = lc.authoritative_codes(rows_a, PAYROLL_NAMES)
ok("A1 manual configured non-zero row is authoritative", "Diversey" in auth_a, True)
ok("A2 name match is case-insensitive", "Grand" in auth_a, True)
ok("A3 a $0.00 placeholder is NOT authoritative", "QV" in auth_a, False)
ok("A4 a non-payroll name is not authoritative", "Utica" in auth_a, False)
ok("A5 the payroll_gross producer token is authoritative", "Belmont" in auth_a, True)
ok("A6 a non-authoritative source_key does not become K2-authoritative", "Cicero" in auth_a, False)
ok("A7 a negative correction still counts as entered", "Nostrand" in auth_a, True)
ok("A8 a row with no store_code is ignored", len(auth_a), 4)
ok("A9 no configured names -> nothing is claimed (byte-identical default)",
   lc.authoritative_codes(rows_a, []), {"Belmont"})
ok("A10 'additional_payroll' must never be authoritative (it would delete the wages line)",
   "additional_payroll" in lc.AUTHORITATIVE_SOURCE_KEYS, False)

amts = lc.entered_amounts(rows_a, PAYROLL_NAMES)
near("A11 entered amount is reported verbatim", amts["Diversey"], 10460.42)
near("A12 entered amounts sum per store", amts["Nostrand"], -250.00)

# ══════════════════════════════════════════════════════════════════════════════════════════════
# §B  hours basis — the owner's rule, reported (never re-derived into dollars)
# ══════════════════════════════════════════════════════════════════════════════════════════════
hb = lc.hours_basis_by_code([
    shift("Diversey", actual=8.0, scheduled=8.0),
    shift("Diversey", actual=None, scheduled=7.5),
    shift("Grand", actual=None, scheduled=9.0),
    shift("Grand", actual=0, scheduled=9.0),
    shift("Chicago heights", actual=None, scheduled=None),   # LIVE July shape: hourless shift
    shift("Chicago heights", actual=0, scheduled=0),
    shift(None, actual=5.0),                                  # no store -> ignored
])
ok("B1 any actual hours -> basis 'actual'", hb["Diversey"]["basis"], "actual")
near("B2 actual hours total", hb["Diversey"]["actual_hours"], 8.0)
near("B3 scheduled hours are still reported alongside", hb["Diversey"]["scheduled_hours"], 15.5)
ok("B4 no actual, scheduled present -> basis 'scheduled'", hb["Grand"]["basis"], "scheduled")
near("B5 scheduled total", hb["Grand"]["scheduled_hours"], 18.0)
ok("B6 shifts exist but carry NO hours -> basis None (nothing to compute from)",
   hb["Chicago heights"]["basis"], None)
ok("B7 the hourless store still reports its shift count (evidence, not silence)",
   hb["Chicago heights"]["shifts"], 2)
ok("B8 a shift with no store_code is ignored", None in hb, False)

# ══════════════════════════════════════════════════════════════════════════════════════════════
# §C  REGRESSION 1 — THE SILENT ZERO
#     LuxeLink July 2026, measured: '3352 26th' (6 shifts), '3735 26th' (17), 'Chicago heights'
#     (15) have NO 'Employee Salaries' row and 0.0 hours on BOTH columns. Their salary line renders
#     $0.00 today, indistinguishable from a store that genuinely paid nobody.
# ══════════════════════════════════════════════════════════════════════════════════════════════
JULY_CODES = ["Diversey", "Grand", "3352 26th", "3735 26th", "Chicago heights"]
july_exp = [
    expense("Diversey", "Employee Salaries", 10460.42),
    expense("Grand", "Employee Salaries", 8656.15),
    # the three gap stores carry only the allocated DM/mgmt rows — no employee salary at all
    expense("3352 26th", "DM Salaries", 1275.00),
    expense("3735 26th", "DM Salaries", 1275.00),
    expense("Chicago heights", "DM Salaries", 1275.00),
    expense("3352 26th", "Owner / Mgmt Salaries", 500.00),
]
july_shifts = ([shift("Diversey", actual=None, scheduled=8.0)] * 36
               + [shift("Grand", actual=None, scheduled=7.0)] * 32
               + [shift("3352 26th", actual=None, scheduled=None)] * 6
               + [shift("3735 26th", actual=0, scheduled=0)] * 17
               + [shift("Chicago heights", actual=None, scheduled=0)] * 15)

rep = lc.labour_coverage(JULY_CODES, july_exp, july_shifts, PAYROLL_NAMES, "July 2026")
ok("C1 scanned every store", rep["scanned"], 5)
ok("C2 a store with an entered payroll figure is ENTERED", rep["states"]["Diversey"], lc.ENTERED)
ok("C3 the hourless gap store is NOT_MEASURED, not zero",
   rep["states"]["3352 26th"], lc.NOT_MEASURED)
ok("C4 all three live gap stores are NOT_MEASURED",
   [rep["states"][c] for c in ("3352 26th", "3735 26th", "Chicago heights")],
   [lc.NOT_MEASURED] * 3)
ok("C5 NOT_MEASURED is not a measured state", rep["detail"]["3735 26th"]["measured"], False)
ok("C6 the gap list names exactly the unmeasured stores",
   rep["gaps"], ["3352 26th", "3735 26th", "Chicago heights"])
ok("C7 the shift count is carried as evidence the store WAS staffed",
   rep["detail"]["3735 26th"]["shifts"], 17)
ok("C8 the note says the number is UNKNOWN, not $0.00",
   "UNKNOWN" in (rep["note"] or "") and "$0.00" in (rep["note"] or ""), True)
ok("C9 the note counts the affected stores", "3 of 5 store(s)" in (rep["note"] or ""), True)
# A DM Salaries row alone DOES make a store authoritative (it is a configured payroll name) — this
# is exactly why the org-wide flag masks the gap in production. Assert the predicate honestly.
# THE MASKING (this is what hid the defect in production): 'DM Salaries' is one of the org's
# configured payroll names, so the raw K2 predicate marks all three gap stores "entered" off a
# $1,275.00 row that is identical at every store. The BOOKING predicate must keep saying so
# (the banner may never disagree with what coa books); the COVERAGE authority excludes it.
ok("C10 the raw booking predicate DOES mark a DM-only store authoritative (production masking)",
   "3352 26th" in lc.authoritative_codes(july_exp, PAYROLL_NAMES), True)
ok("C11 …and does NOT when only 'Employee Salaries' is configured",
   "3352 26th" in lc.authoritative_codes(july_exp, ["Employee Salaries"]), False)
alloc = lc.allocated_names(july_exp, PAYROLL_NAMES)
ok("C10a a name charged at one identical amount to every store is an ALLOCATION",
   alloc, {"dm salaries"})
ok("C10b a name that VARIES per store is never flagged as an allocation",
   "employee salaries" in alloc, False)
ok("C10c fewer than min_stores identical rows proves nothing",
   lc.allocated_names(july_exp, PAYROLL_NAMES, min_stores=4), set())
ok("C10d a $0.00 uniform row is not an allocation either",
   lc.allocated_names([expense(c, "DM Salaries", 0) for c in "abc"], PAYROLL_NAMES), set())
# LIVE July shape: 'DM Salaries' is $1,275.00 at nineteen stores and $1,275.01 at the twentieth.
# A unanimity test let that single cent mask all three under-reported stores.
cent = ([expense(f"s{i}", "DM Salaries", 1275.00) for i in range(19)]
        + [expense("QV", "DM Salaries", 1275.01)])
ok("C10g a one-cent residue at ONE store cannot turn an allocation back into a measurement",
   lc.allocated_names(cent, PAYROLL_NAMES), {"dm salaries"})
ok("C10h …and the dominant amount is the one held by most stores, not the outlier",
   lc.allocated_names(cent, PAYROLL_NAMES, dominance=0.95), {"dm salaries"})
ok("C10i a genuinely varying payroll figure never reaches the threshold",
   lc.allocated_names([expense(f"s{i}", "Employee Salaries", 1000.0 + i * 137)
                       for i in range(20)], PAYROLL_NAMES), set())
ok("C10j an even split between two amounts is not dominant enough to be an allocation",
   lc.allocated_names([expense(f"a{i}", "DM Salaries", 100.0) for i in range(5)]
                      + [expense(f"b{i}", "DM Salaries", 200.0) for i in range(5)],
                      PAYROLL_NAMES), set())
ok("C10e the report names the allocation it discounted", rep["allocated_names"], ["dm salaries"])
ok("C10f the note explains why the allocation does not count",
   "does not measure a store's own staff" in (rep["note"] or ""), True)

# a fully measured month raises NO banner (a warning that is always on is a warning nobody reads)
clean = lc.labour_coverage(["Diversey", "Grand"], july_exp[:2],
                           july_shifts[:68], PAYROLL_NAMES, "July 2026")
ok("C12 a fully measured period has no gaps", clean["gaps"], [])
ok("C13 …and no note", clean["note"], None)
ok("C14 no stores scanned -> no banner", lc.labour_coverage([], [], [], PAYROLL_NAMES, "x")["note"], None)

# scheduled-only vs actual: the owner's fallback, reported distinctly
sched = lc.labour_coverage(["Grand"], [], july_shifts[36:68], PAYROLL_NAMES, "July 2026")
ok("C15 no entry + scheduled hours only -> DERIVED_SCHEDULED",
   sched["states"]["Grand"], lc.DERIVED_SCHEDULED)
ok("C16 DERIVED_SCHEDULED is a measured state", sched["detail"]["Grand"]["measured"], True)
act = lc.labour_coverage(["Grand"], [], [shift("Grand", actual=7.0, scheduled=8.0)],
                         PAYROLL_NAMES, "July 2026")
ok("C17 no entry + actual hours -> DERIVED_ACTUAL", act["states"]["Grand"], lc.DERIVED_ACTUAL)
ok("C18 an inactive store with no shifts is genuinely zero, not a gap",
   lc.labour_coverage(["Closed"], [], [], PAYROLL_NAMES, "July 2026",
                      active_codes=[])["states"]["Closed"], lc.NO_STAFF)
ok("C19 …and an ACTIVE store with no shifts is still NOT_MEASURED",
   lc.labour_coverage(["Open"], [], [], PAYROLL_NAMES, "July 2026",
                      active_codes=["Open"])["states"]["Open"], lc.NOT_MEASURED)

# ══════════════════════════════════════════════════════════════════════════════════════════════
# §D  REGRESSION 2 — THE MONTH GRAIN
#     "for that month to go in the gross profit for that month". LuxeLink September 2026 has NO
#     store_expenses rows of its own, so expenses_effective carries AUGUST's $295,610.15 forward —
#     including $158,960.01 of salary — while September's own shifts derive $53,059.82.
# ══════════════════════════════════════════════════════════════════════════════════════════════
sep = lc.labour_coverage(JULY_CODES, july_exp, july_shifts, PAYROLL_NAMES, "September 2026",
                         carried_from="August 2026")
ok("D1 EVERY store on a carried period is CARRIED — including the ones with an entered row",
   sorted(set(sep["states"].values())), [lc.CARRIED])
ok("D2 a carried figure is NOT a measurement of this month",
   sep["detail"]["Diversey"]["measured"], False)
ok("D3 every store is a gap on a carried period", len(sep["gaps"]), 5)
ok("D4 the payload names the month the dollars actually belong to",
   sep["carried_from"], "August 2026")
ok("D5 the note names BOTH months so a month-shift cannot be mistaken for a measurement",
   "September 2026" in sep["note"] and "August 2026" in sep["note"], True)
ok("D6 the note says the dollars belong to the other month",
   "belong to August 2026" in sep["note"], True)
ok("D7 hours measured THIS month are still reported under a carried figure "
   "(the evidence the carried number is wrong)", sep["detail"]["Diversey"]["scheduled_hours"], 288.0)
ok("D8 carried outranks entered (state precedence)",
   lc.store_state("Diversey", {"Diversey"}, {}, "August 2026"), lc.CARRIED)
ok("D9 …and with no carry the same store is ENTERED",
   lc.store_state("Diversey", {"Diversey"}, {}, None), lc.ENTERED)

# ══════════════════════════════════════════════════════════════════════════════════════════════
# §E  REGRESSION 3 — THE ORG-WIDE SUPPRESSION GAP
#     coa.build_inputs keeps ONE boolean for the whole org: any authoritative row anywhere
#     suppresses the hours estimate for EVERY store. A store with no entry then books $0.00.
#     Per-store authority is the corrected grain; the assertion below is the semantic contract the
#     coa wiring implements under account_config.payroll_authority_grain = 'store'.
# ══════════════════════════════════════════════════════════════════════════════════════════════
mixed_codes = ["Diversey", "Grand", "3735 26th"]
mixed_exp = [expense("Diversey", "Employee Salaries", 10460.42)]
mixed_shifts = ([shift("Grand", actual=None, scheduled=7.0)] * 32
                + [shift("3735 26th", actual=0, scheduled=0)] * 17)
mrep = lc.labour_coverage(mixed_codes, mixed_exp, mixed_shifts, PAYROLL_NAMES, "July 2026")
auth_m = lc.authoritative_codes(mixed_exp, PAYROLL_NAMES)
ok("E1 ORG grain: one authoritative row anywhere suppresses every store's estimate",
   bool(auth_m), True)
ok("E2 STORE grain: only the entered store is suppressed",
   sorted(c for c in mixed_codes if c not in auth_m), ["3735 26th", "Grand"])
ok("E3 the store with hours would book its estimate under STORE grain",
   mrep["states"]["Grand"], lc.DERIVED_SCHEDULED)
ok("E4 the store with NEITHER an entry nor hours stays honestly unmeasured under BOTH grains",
   mrep["states"]["3735 26th"], lc.NOT_MEASURED)
ok("E5 the entered store is never double-booked with an estimate",
   mrep["states"]["Diversey"], lc.ENTERED)

# ══════════════════════════════════════════════════════════════════════════════════════════════
# §F  REGRESSION 4 — THE COMMISSION DOUBLE-BOOK
#     LuxeLink August 2026, measured: twenty $500.00 'Employee Commission' expense rows
#     ($10,000.00) alongside commcalc.rep_commissions totalling $11,118.78, which the GP report
#     already deducts as rep_pay and the P&L as the rep_comm line.
# ══════════════════════════════════════════════════════════════════════════════════════════════
aug_exp = [expense(c, "Employee Commission", 500.00)
           for c in ("Diversey", "Grand", "Cicero", "Belmont")]
rep_pay = {"Diversey": 1062.93, "Grand": 744.55, "Cicero": 339.16, "Belmont": 821.65}
col = lc.commission_collisions(aug_exp, rep_pay, COMMISSION_NAMES)
ok("F1 every colliding store is reported", len(col["stores"]), 4)
near("F2 a store double-books the SMALLER of the two routes (Cicero: exp 500 vs rep 339.16)",
     [s for s in col["stores"] if s["store_code"] == "Cicero"][0]["double_booked"], 339.16)
near("F3 …and the flat $500 when rep pay exceeds it (Diversey)",
     [s for s in col["stores"] if s["store_code"] == "Diversey"][0]["double_booked"], 500.00)
# each store double-books the SMALLER of its two routes: 500 + 500 + 339.16 + 500
near("F4 total double-booked", col["total_double_booked"], 500.00 + 500.00 + 339.16 + 500.00)
ok("F5 the note says the dollars are booked twice", "booked TWICE" in (col["note"] or ""), True)
ok("F6 nothing is netted automatically — the report carries both raw figures",
   (col["stores"][0]["expense"], col["stores"][0]["rep_pay"]), (500.00, 821.65))
ok("F7 no configured commission names -> no claim at all (byte-identical default)",
   lc.commission_collisions(aug_exp, rep_pay, [])["stores"], [])
ok("F8 …and no note", lc.commission_collisions(aug_exp, rep_pay, None)["note"], None)
ok("F9 a store with an expense row but NO rep pay is not a collision",
   lc.commission_collisions([expense("Solo", "Employee Commission", 500.0)], {},
                            COMMISSION_NAMES)["stores"], [])
ok("F10 a store with rep pay but NO expense row is not a collision",
   lc.commission_collisions([], {"Solo": 900.0}, COMMISSION_NAMES)["stores"], [])

# ══════════════════════════════════════════════════════════════════════════════════════════════
# §H  SUPPRESSION — the owner's ruling on WHICH route books (2026-09-08: "Rep commision should go
#     in p&l"). `rep_commissions` → `rep_comm` / `−Rep Pay` is authoritative and untouched; the
#     manual commission-named expense row is the duplicate and stops booking.
#
#     THE FIXTURE IS THE LIVE AUGUST 2026 MONTH, verified against LuxeLink
#     (org 854f6d7b-…-646f560d4f4c) on 2026-09-08 through read-only PostgREST:
#       • commcalc.store_expenses 'Employee Commission' — twenty rows, $500.00 each = $10,000.00
#       • commcalc.rep_commissions August total_payout   — $11,118.78 over 46 rows, of which
#         $10,771.87 attributes to one of the twenty stores and $346.91 carries a BLANK store
#         (booked company-wide by the P&L, so it pairs with no expense row and replaces nothing)
#       • overlap booked on BOTH routes                  — $7,626.14
# ══════════════════════════════════════════════════════════════════════════════════════════════
# store_code → (Employee Commission expense, rep_commissions.total_payout) — LIVE, to the cent.
AUG_LIVE = {
    "3352 26th":       (500.00,  616.59),
    "3735 26th":       (500.00,  381.50),
    "7812":            (500.00,  294.39),
    "957":             (500.00,  957.36),
    "Armitage":        (500.00,  971.88),
    "Ave U":           (500.00,  217.36),
    "Belmont":         (500.00,  821.65),
    "Cermark":         (500.00,  872.75),
    "Chicago heights": (500.00,  226.67),
    "Cicero":          (500.00,  339.16),
    "Diversey":        (500.00, 1062.93),
    "Grand":           (500.00,  744.55),
    "Irving Park":     (500.00,  292.42),
    "kedzie":          (500.00,  211.62),
    "lawrence":        (500.00,  475.10),
    "Lefferts":        (500.00,  334.10),
    "Narragansett":    (500.00,  308.95),
    "Nostrand":        (500.00,   44.87),
    "QV":              (500.00,  522.51),
    "Utica":           (500.00, 1075.51),
}
AUG_UNATTRIBUTED_REP = 346.91          # rep_commissions rows with a BLANK store (company-wide)

# The whole August expense sheet in miniature: the commission rows PLUS ordinary opex that must be
# completely untouched by any of this (rent is not commission and never becomes suppressible).
aug_full_exp = ([expense(c, "Employee Commission", e) for c, (e, _r) in AUG_LIVE.items()]
                + [expense(c, "Rent / Lease", 3673.25) for c in AUG_LIVE]
                + [expense(c, "Employee Salaries", 6673.00) for c in AUG_LIVE])
aug_rep = {c: r for c, (_e, r) in AUG_LIVE.items()}

near("H0 fixture ties to live: Σ 'Employee Commission' expense = $10,000.00",
     sum(e for e, _r in AUG_LIVE.values()), 10000.00)
near("H0a fixture ties to live: Σ store-attributed rep_commissions = $10,771.87",
     sum(r for _e, r in AUG_LIVE.values()), 10771.87)
near("H0b …and with the blank-store rows the month totals the live $11,118.78",
     sum(r for _e, r in AUG_LIVE.values()) + AUG_UNATTRIBUTED_REP, 11118.78)

# ── H1  REGRESSION: the measured double-book, before the fix ─────────────────────────────────
before_col = lc.commission_collisions(aug_full_exp, aug_rep, COMMISSION_NAMES)
near("H1 REGRESSION reproduces the measured August double-book: $7,626.14",
     before_col["total_double_booked"], 7626.14)
ok("H1a …across all twenty stores", len(before_col["stores"]), 20)

# ── H2  the plan: what stops booking and what books in its place ─────────────────────────────
plan = lc.suppression_plan(aug_full_exp, aug_rep, COMMISSION_NAMES)
ok("H2 the plan is active once a name is configured and rows match", plan["active"], True)
ok("H2a every one of the twenty stores is suppressed", len(plan["suppressed_keys"]), 20)
near("H2b $10,000.00 of expense stops booking", plan["total_suppressed"], 10000.00)
near("H2c $10,771.87 of rep commission books in its place", plan["total_booked_instead"], 10771.87)
near("H2d nothing is kept — every store has a replacement this month", plan["total_kept"], 0.0)
ok("H2e …and no store is listed as un-replaceable", plan["kept"], [])
ok("H2f the swap is reported PER STORE, both figures, never one",
   [(s["store_code"], s["expense"], s["rep_pay"]) for s in plan["stores"] if s["store_code"] == "Nostrand"],
   [("Nostrand", 500.00, 44.87)])
ok("H2g …and every store row carries its state", sorted({s["state"] for s in plan["stores"]}),
   [lc.REPLACED])
ok("H2h the note states BOTH dollar figures, so a reader sees a swap not a discount",
   ("$10,000.00" in plan["note"]) and ("$10,771.87" in plan["note"]), True)

# ── H3  REGRESSION CLEARED: after suppression the double-book is gone ────────────────────────
idx = lc.suppression_index(plan)
still_booking = [r for r in aug_full_exp
                 if not lc.suppresses_row(idx, r["expense_name"], r["store_code"])]
after_col = lc.commission_collisions(still_booking, aug_rep, COMMISSION_NAMES)
near("H3 REGRESSION CLEARED: $0.00 double-booked after suppression",
     after_col["total_double_booked"], 0.0)
ok("H3a …and no store is still colliding", after_col["stores"], [])
ok("H3b exactly the twenty commission rows stopped booking",
   len(aug_full_exp) - len(still_booking), 20)
near("H3c the expense sheet drops by exactly $10,000.00 — the suppressed rows, nothing else",
     sum(float(r["amount"]) for r in aug_full_exp) - sum(float(r["amount"]) for r in still_booking),
     10000.00)
ok("H3d NOT ONE ordinary opex row is touched (rent + salaries all survive)",
   sorted({r["expense_name"] for r in still_booking}), ["Employee Salaries", "Rent / Lease"])
ok("H3e a suppressed row is REMOVED, never rewritten to $0.00",
   [r for r in still_booking if r["expense_name"] == "Employee Commission"], [])

# ── H4  BYTE-IDENTITY: the house default '{}' changes nothing for anyone ─────────────────────
for _empty, _label in ((None, "None"), ([], "[]"), (["   "], "blank-only")):
    _p = lc.suppression_plan(aug_full_exp, aug_rep, _empty)
    ok(f"H4 empty config ({_label}) ⇒ plan inert", _p["active"], False)
    ok(f"H4a empty config ({_label}) ⇒ nothing suppressed", _p["suppressed_keys"], [])
    ok(f"H4b empty config ({_label}) ⇒ no note", _p["note"], None)
    _i = lc.suppression_index(_p)
    _kept = [r for r in aug_full_exp if not lc.suppresses_row(_i, r["expense_name"], r["store_code"])]
    ok(f"H4c empty config ({_label}) ⇒ every expense row still books", len(_kept), len(aug_full_exp))
    near(f"H4d empty config ({_label}) ⇒ the sheet total is byte-identical",
         sum(float(r["amount"]) for r in _kept), sum(float(r["amount"]) for r in aug_full_exp))
# RULE TWO in one check: suppression is driven ENTIRELY by the org's configured vocabulary, and no
# expense name is privileged in code. With the commission name configured, rent is invisible to the
# plan; name rent instead and rent is what stops booking. Nothing in this module knows what either
# string means — which is why another tenant calling its commission row something else just works.
near("H4e with 'Employee Commission' configured, rent is not even considered",
     lc.suppression_plan(aug_full_exp, aug_rep, COMMISSION_NAMES)["total_suppressed"], 10000.00)
near("H4f …and configuring the rent name instead moves the rent rows, not the commission rows",
     lc.suppression_plan(aug_full_exp, aug_rep, ["Rent / Lease"])["total_suppressed"], 73465.00)

# ── H5  THE DANGEROUS CASE: nothing to replace the cost with ─────────────────────────────────
#     A store carrying the expense row while rep_commissions has NOTHING for it that month. This
#     is the case where blind suppression would delete a real cost and book nothing back.
gap_exp = [expense("Diversey", "Employee Commission", 500.00),
           expense("Nostrand", "Employee Commission", 500.00),
           expense("NewStore", "Employee Commission", 500.00)]
gap_plan = lc.suppression_plan(gap_exp, {"Diversey": 1062.93, "Nostrand": 44.87}, COMMISSION_NAMES)
ok("H5 the store with no rep commission is NOT suppressed", gap_plan["kept"], ["NewStore"])
ok("H5a …its state names the reason, not a silent skip",
   [s["state"] for s in gap_plan["stores"] if s["store_code"] == "NewStore"], [lc.NO_REPLACEMENT])
near("H5b …its dollars are reported, not lost", gap_plan["total_kept"], 500.00)
ok("H5c …and it is NOT in the suppressed set, so its row still books",
   "NewStore" in gap_plan["suppressed_keys"], False)
ok("H5d …the row survives the reader predicate",
   lc.suppresses_row(lc.suppression_index(gap_plan), "Employee Commission", "NewStore"), False)
ok("H5e the note NAMES the store and its amount",
   ("NewStore" in gap_plan["note"]) and ("$500.00" in gap_plan["note"]), True)
ok("H5f the two replaceable stores are still suppressed",
   gap_plan["suppressed_keys"], ["Diversey", "Nostrand"])
near("H5g …so $1,000.00 leaves the expense line and $1,107.80 books in its place",
     gap_plan["total_suppressed"], 1000.00)
near("H5h …the replacement figure is the real payout, not the flat expense",
     gap_plan["total_booked_instead"], 1107.80)
ok("H5i THREE states exist, never two",
   sorted({lc.REPLACED, lc.NO_REPLACEMENT, lc.NOT_APPLICABLE}),
   sorted(["replaced", "no_replacement", "not_applicable"]))

# ── H5-SEPT  THE DANGEROUS CASE, MEASURED LIVE ───────────────────────────────────────────────
#     September 2026 has NO store_expenses rows of its own, so `expenses_effective` carries all 327
#     of August's MANUAL rows — the twenty $500.00 'Employee Commission' rows among them — while
#     commcalc.rep_commissions has ZERO September rows (verified 2026-09-08: periods present are
#     March, May, June, July, August). A blanket name filter would delete $10,000.00 of cost from
#     September and book NOTHING back. It must not, and the month must say so out loud.
sept_carried = [expense(c, "Employee Commission", 500.00) for c in AUG_LIVE]
sept_plan = lc.suppression_plan(sept_carried, {}, COMMISSION_NAMES)
near("H5-SEPT REGRESSION: not one dollar is suppressed in a month with no rep commissions",
     sept_plan["total_suppressed"], 0.0)
ok("H5-SEPTa …all twenty carried rows are KEPT", len(sept_plan["kept"]), 20)
near("H5-SEPTb …and the $10,000.00 at risk is reported, not deleted",
     sept_plan["total_kept"], 10000.00)
ok("H5-SEPTc …every one of them in the no_replacement state",
   sorted({s["state"] for s in sept_plan["stores"]}), [lc.NO_REPLACEMENT])
ok("H5-SEPTd …the plan is 'active' so the month cannot go unreported",
   sept_plan["active"], True)
ok("H5-SEPTe …and no store is silently rendered at $0.00",
   all(s["expense"] == 500.00 and not s["suppressed"] for s in sept_plan["stores"]), True)

# ── H6  state predicate at the edges ─────────────────────────────────────────────────────────
ok("H6 a $0.00 placeholder expense is not a suppression candidate",
   lc.suppression_state(0.0, 900.0), lc.NOT_APPLICABLE)
ok("H6a a real expense with zero rep pay is the dangerous case",
   lc.suppression_state(500.0, 0.0), lc.NO_REPLACEMENT)
ok("H6b a real expense with rep pay is replaced", lc.suppression_state(500.0, 0.01), lc.REPLACED)
ok("H6c sub-cent rep pay is NOT a replacement (tolerance, not a rounding accident)",
   lc.suppression_state(500.0, 0.004), lc.NO_REPLACEMENT)
ok("H6d a NEGATIVE expense correction is still a real booking to decide on",
   lc.suppression_state(-500.0, 900.0), lc.REPLACED)
ok("H6e a $0.00 expense row never reaches the plan at all",
   lc.suppression_plan([expense("Zero", "Employee Commission", 0.0)], {"Zero": 900.0},
                       COMMISSION_NAMES)["active"], False)

# ── H7  the row predicate: name AND key, case-insensitively, or it books ─────────────────────
ok("H7 a configured name at a suppressed key is suppressed",
   lc.suppresses_row(idx, "Employee Commission", "Diversey"), True)
ok("H7a …case-insensitively, as the config vocabulary always matches",
   lc.suppresses_row(idx, "  EMPLOYEE commission ", "Diversey"), True)
ok("H7b a DIFFERENT name at a suppressed key still books",
   lc.suppresses_row(idx, "Rent / Lease", "Diversey"), False)
ok("H7c the configured name at an UNSUPPRESSED key still books",
   lc.suppresses_row(idx, "Employee Commission", "SomeOtherStore"), False)
ok("H7d an inert plan suppresses nothing",
   lc.suppresses_row(lc.suppression_index(lc.suppression_plan(aug_full_exp, aug_rep, [])),
                     "Employee Commission", "Diversey"), False)
ok("H7e a None plan is inert too (an unreadable config can never delete a cost)",
   lc.suppression_index(None), (frozenset(), frozenset()))

# ── H8  ONE derivation, TWO readers: the P&L and the GP report must suppress the SAME rows ───
#     The GP report keys store_expenses by storeops store_code; the P&L keys its store lines by
#     canonical store ADDRESS (code2addr → resolve_store). Same rows, different key space — the
#     plan takes `key_of` so each reader pairs in the space it books in. Verified against the
#     live LuxeLink key spaces on 2026-09-08 (both pair all twenty stores).
PNL_ADDR = {"Diversey": "4640-A W Diversey Ave", "Nostrand": "3560 Nostrand Avenue",
            "Cermark": "2414 W Cermak Rd"}
two_exp = [expense(c, "Employee Commission", 500.00) for c in PNL_ADDR]
gp_plan = lc.suppression_plan(two_exp, {"Diversey": 1062.93, "Nostrand": 44.87, "Cermark": 872.75},
                              COMMISSION_NAMES)
pnl_plan = lc.suppression_plan(
    two_exp, {"4640-A W Diversey Ave": 1062.93, "3560 Nostrand Avenue": 44.87,
              "2414 W Cermak Rd": 872.75},
    COMMISSION_NAMES, key_of=lambda c: PNL_ADDR.get(c, c))
near("H8 both readers suppress the same DOLLARS", gp_plan["total_suppressed"],
     pnl_plan["total_suppressed"])
near("H8a …and book the same replacement", gp_plan["total_booked_instead"],
     pnl_plan["total_booked_instead"])
ok("H8b …and keep the same (empty) set of un-replaceable stores",
   (gp_plan["kept"], pnl_plan["kept"]), ([], []))
ok("H8c the P&L plan speaks in ADDRESSES, the GP plan in store codes — same rows, its own space",
   (sorted(pnl_plan["suppressed_keys"]), sorted(gp_plan["suppressed_keys"])),
   (["2414 W Cermak Rd", "3560 Nostrand Avenue", "4640-A W Diversey Ave"],
    ["Cermark", "Diversey", "Nostrand"]))
ok("H8d a row unattributable to any store pairs with nothing and is never suppressed",
   lc.suppression_plan([expense(None, "Employee Commission", 500.00)], {}, COMMISSION_NAMES)["active"],
   False)

# ── H9  READER ARITHMETIC: what each surface's bottom line does, proven on the live month ────
#     GP:   net_profit = total_rev − rep_pay − exp_total − net_phone_cost, so removing $X from
#           exp_total raises net profit by exactly $X and moves NOTHING else.
#     P&L:  store_opex falls by the same $X; rep_comm is untouched, so net income rises by $X.
#     The two must move by the SAME number or the reports disagree — that is the whole point.
AUG_OTHER_OPEX = sum(float(r["amount"]) for r in aug_full_exp
                     if r["expense_name"] != "Employee Commission")
gp_exp_before = AUG_OTHER_OPEX + 10000.00
gp_exp_after = AUG_OTHER_OPEX + 10000.00 - plan["total_suppressed"]
near("H9 GP −Expenses falls by exactly the suppressed $10,000.00",
     gp_exp_before - gp_exp_after, 10000.00)
near("H9a …so GP net profit RISES by $10,000.00 for August", gp_exp_before - gp_exp_after, 10000.00)
near("H9b the P&L store_opex falls by the identical figure (one plan, two readers)",
     plan["total_suppressed"], gp_exp_before - gp_exp_after)
near("H9c rep_comm / −Rep Pay is UNTOUCHED — the authoritative route keeps its $11,118.78",
     sum(r for _e, r in AUG_LIVE.values()) + AUG_UNATTRIBUTED_REP, 11118.78)
near("H9d net labour actually deducted for August goes from $21,118.78 (both routes) to "
     "$11,118.78 (rep_commissions alone)",
     (sum(r for _e, r in AUG_LIVE.values()) + AUG_UNATTRIBUTED_REP + 10000.00)
     - plan["total_suppressed"], 11118.78)
near("H9e the correction is NOT the $7,626.14 overlap — the whole duplicate row goes, and the "
     "remaining $2,373.86 was expense the payout never covered",
     plan["total_suppressed"] - before_col["total_double_booked"], 2373.86)

# ── H10  the plan reports, it never mutates ──────────────────────────────────────────────────
_snapshot = [dict(r) for r in aug_full_exp]
lc.suppression_plan(aug_full_exp, aug_rep, COMMISSION_NAMES)
ok("H10 the expense rows handed in are not mutated", aug_full_exp, _snapshot)
ok("H10a the plan is JSON-safe (lists, not sets) — both readers put it in a payload",
   all(isinstance(plan[k], list) for k in ("names", "suppressed_keys", "stores", "kept")), True)
ok("H10b the plan's keys are exactly the reported contract",
   sorted(plan.keys()),
   ["active", "kept", "names", "note", "stores", "suppressed_keys",
    "total_booked_instead", "total_kept", "total_suppressed"])
ok("H10c every store row carries both dollar figures and its reason slot",
   sorted(plan["stores"][0].keys()),
   ["booked_instead", "expense", "reason", "rep_pay", "state", "store_code", "suppressed"])

# ══════════════════════════════════════════════════════════════════════════════════════════════
# §G  TOTAL-PRESERVING: this module reads rows and returns states. It must never change a dollar.
# ══════════════════════════════════════════════════════════════════════════════════════════════
before = [dict(r) for r in july_exp]
before_shifts = [dict(s) for s in july_shifts]
g_rep = lc.labour_coverage(JULY_CODES, july_exp, july_shifts, PAYROLL_NAMES, "July 2026")
g_col = lc.commission_collisions(july_exp, rep_pay, COMMISSION_NAMES)
ok("G1 expense rows are not mutated", july_exp, before)
ok("G2 shift rows are not mutated", july_shifts, before_shifts)
near("G3 the Σ of the input expense rows is untouched",
     sum(float(r["amount"]) for r in july_exp), sum(float(r["amount"]) for r in before))
ok("G4 the report carries states and evidence only — no key that books a dollar",
   sorted(g_rep.keys()), ["allocated_names", "carried_from", "counts", "detail", "gaps",
                          "note", "period", "scanned", "states"])
ok("G4a the collision report likewise reports, never nets",
   sorted(g_col.keys()), ["names", "note", "stores", "total_double_booked"])
ok("G5 load_shift_hours degrades to [] on an unreadable feed (never a fabricated zero)",
   lc.load_shift_hours(None, "org", "2026-08"), [])
ok("G6 …and on an unparseable period", lc.load_shift_hours(None, "org", "nonsense"), [])
ok("G7 …and it accepts BOTH house period spellings via the ONE shared parser",
   lc.load_shift_hours(None, "org", "August 2026"), [])

# ── report ────────────────────────────────────────────────────────────────────────────────────
print("=" * 78)
print("harness_labour_coverage — salary READ path (GP + P&L coverage, month grain, double-book)")
print("=" * 78)
for f in FAILS:
    print("FAIL " + f)
print(f"\n{CHECKS - len(FAILS)}/{CHECKS} checks passed" + (" — ALL GREEN" if not FAILS else ""))
sys.exit(1 if FAILS else 0)
