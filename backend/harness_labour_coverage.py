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
