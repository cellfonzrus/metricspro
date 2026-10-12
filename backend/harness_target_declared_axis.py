#!/usr/bin/env python3
"""
PROOF HARNESS — A TARGET FOR A CATEGORY THE BUSINESS DECLARED (index §COMMCALC-01)
=================================================================================

Owner directive 2026-10-10: *"the target needs to be set for the gross revenue per day / week /
month or backward calculate what is needed to reach a certain level ... but i dont wnat them to be
hard coded it should be a design effect"*

DB-free, no I/O, no network. Every number below is computed by the engine under test.

WHAT THIS PROVES, in the order the house rules ask for it:

 A. THE REGRESSION. The four built-in categories compute BYTE-IDENTICALLY with a tenant catalog
    present and absent. This is the claim that lets mig 1074 ship with no backfill: an existing
    tenant's targets cannot move because a different tenant declared a category.

 B. THE CLASS, NOT THE INSTANCE. The engine's axis is asked of the tenant's catalog, so a category
    nobody anticipated ("dropoff", "printing") gets a target, a per-day base, a catch-up shortfall,
    a `need` and a `pace` with NO new arithmetic. Fixing this for the UPS-store tenant alone would
    have been the patchwork the owner banned.

 C. ONE FACT, ONE HOME, DEREFERENCED. "Which number answers this metric" lives once, in
    closing/metric_catalog (`figure_unit` / `figure_of`). This asserts the targets engine
    dereferences it rather than carrying a second copy — the divergence that would let a target be
    SET in units and MEASURED in dollars.

 D. NO SECOND CALCULATION FOR WEEK OR DAY. A week target is a WINDOW over the one monthly spread.
    The windows must sum to the month exactly; a second derivation would drift the first time the
    schedule changed.

 E. THE BACKWARD CALCULATION is honest at its edges: no days left is None, not the need and not an
    infinity; over-achievement is zero, never negative.

 F. TWO HOMES FOR ONE NUMBER ARE REFUSED. A target_line row naming a built-in category is ignored
    by the fold, so hand-inserting one cannot override the column.
"""
import sys
from datetime import date

sys.path.insert(0, ".")

from app.modules.commcalc import targets_engine as T          # noqa: E402
from app.modules.closing import metric_catalog as MC           # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


# ── The fixture: a UPS-store-shaped tenant ────────────────────────────────────────────────────────
# `dropoff` is a tally, `printing` is money, `mailbox` is declared in BOTH (a count that also earns)
# — the case that decides whether a target is set on the tally or the money.
CATALOG = [
    {"key": "dropoff", "label": "Package drop-offs", "value_kind": "count"},
    {"key": "printing", "label": "Printing", "value_kind": "amount"},
    {"key": "mailbox", "label": "Mailbox rentals", "value_kind": "both"},
]

TARGET_ROW = {"activations_monthly": 100, "upgrades_monthly": 40,
              "accessories_monthly": 5000, "byod_pct": 25}

LINES = [
    {"metric_key": "dropoff", "target_value": 600, "grain": "month"},
    {"metric_key": "printing", "target_value": 3000, "grain": "month"},
    # F: a row naming a built-in. Must be IGNORED, not allowed to override accessories_monthly.
    {"metric_key": "accessories", "target_value": 999999, "grain": "month"},
]

TODAY = date(2026, 10, 12)
MONTH_END = date(2026, 10, 31)
# A 31-day October, open every day, 8 hours a day — so the per-day base is a clean 1/31 and any
# rounding drift between the weeks and the month is visible rather than hidden by uneven hours.
HOURS = {date(2026, 10, d): 8.0 for d in range(1, 32)}
# Eleven days of actuals. The declared figures arrive on the `declared` key, which is how
# closing/_declared_by_day hands them in.
ACTUALS = {
    date(2026, 10, d): {"prem": 2.0, "byod": 1.0, "upg": 1.0, "acc": 150.0,
                        "declared": {"dropoff": 23.0 / 11, "printing": 300.0 / 11}}
    for d in range(1, 12)
}


def run(catalog, lines):
    monthly = T.derive_monthly_by_cat(TARGET_ROW, 25.0, target_lines=lines, catalog=catalog)
    scope = T.compute_scope(monthly, HOURS, ACTUALS, TODAY, month_end=MONTH_END, catalog=catalog)
    return monthly, scope


print(__doc__)

print("A. REGRESSION — the four built-ins do not move when a catalog is present")
m_with, s_with = run(CATALOG, LINES)
m_bare, s_bare = run(None, None)
for cat in T.BUILTIN_CATEGORIES:
    check(f"monthly[{cat}] identical", m_with[cat] == m_bare[cat],
          f"{m_with[cat]} vs {m_bare[cat]}")
    check(f"categories[{cat}] identical",
          s_with["categories"][cat] == s_bare["categories"][cat])
check("per-day calendar identical for built-ins",
      [{c: r["cats"][c] for c in T.BUILTIN_CATEGORIES} for r in s_with["calendar"]]
      == [{c: r["cats"][c] for c in T.BUILTIN_CATEGORIES} for r in s_bare["calendar"]])
check("hours denominator identical",
      s_with["effective_hours_total"] == s_bare["effective_hours_total"])
check("bare axis is exactly the four built-ins",
      T.target_categories(None) == list(T.BUILTIN_CATEGORIES))

print("\nB. THE CLASS — a declared category gets the whole engine, with no new arithmetic")
check("axis carries built-ins then declared, no duplicates",
      T.target_categories(CATALOG) == list(T.BUILTIN_CATEGORIES) + ["dropoff", "printing", "mailbox"])
check("dropoff monthly target comes from its target_line", m_with["dropoff"] == 600.0,
      str(m_with["dropoff"]))
d = s_with["categories"]["dropoff"]
check("dropoff has achieved_mtd from the declared figures", abs(d["achieved_mtd"] - 23.0) < 0.01,
      str(d["achieved_mtd"]))
check("dropoff need = target - achieved", abs(d["need"] - 577.0) < 0.01, str(d["need"]))
check("dropoff pace is need spread over the open days left", d["pace"] > 0, str(d["pace"]))
check("dropoff carries a per-day base on the calendar",
      s_with["calendar"][0]["cats"]["dropoff"]["base"] > 0)
check("a declared category with no target_line reads 0, not a guess", m_with["mailbox"] == 0.0)
check("printing (money) target flows through", m_with["printing"] == 3000.0)

print("\nC. ONE FACT, ONE HOME — the figure rule is dereferenced, not copied")
check("target_units('both') is dollars, as figure_unit says",
      T.target_units(CATALOG)["mailbox"] == MC.figure_unit("both") == "dollars")
check("target_units('count') is count", T.target_units(CATALOG)["dropoff"] == MC.figure_unit("count"))
check("target_units('amount') is dollars", T.target_units(CATALOG)["printing"] == MC.figure_unit("amount"))
# The rule itself, asserted at its own home so a change there is caught here too.
check("figure_of reads MONEY for a money metric",
      MC.figure_of({"value_kind": "amount", "amount": 12.5, "units": 3}) == 12.5)
check("figure_of reads UNITS for a tally that also earns",
      MC.figure_of({"value_kind": "count", "amount": 360.0, "units": 2}) == 2)
check("figure_of reads MONEY for a 'both' metric",
      MC.figure_of({"value_kind": "both", "amount": 360.0, "units": 2}) == 360.0)
check("figure_of on an undeclared day is 0, never None", MC.figure_of(None) == 0)
# The engine must not re-state the rule. A literal ('amount', 'both') pair in the engine would be a
# second copy; this is the cheap textual half of the lock, the build-failing half lives in
# harness_target_axis_lock.py.
_src = open("app/modules/commcalc/targets_engine.py").read()
check("engine imports the one home", "figure_unit" in _src and "metric_catalog import" in _src)

print("\nD. WEEK AND DAY ARE WINDOWS OVER THE MONTH, NOT A SECOND CALCULATION")
weeks = T.grain_windows(s_with, grain="week")
months = T.grain_windows(s_with, grain="month")
days = T.grain_windows(s_with, grain="day")
check("the month window is exactly the monthly target",
      abs(months[0]["cats"]["dropoff"]["base"] - 600.0) < 0.01,
      str(months[0]["cats"]["dropoff"]["base"]))
_wk_sum = sum(w["cats"]["dropoff"]["base"] for w in weeks)
check("the weeks sum to the month (within one cent per window)",
      abs(_wk_sum - 600.0) <= 0.01 * len(weeks), f"{_wk_sum} over {len(weeks)} weeks")
_dy_sum = sum(w["cats"]["dropoff"]["base"] for w in days)
check("the days sum to the month (within one cent per day)",
      abs(_dy_sum - 600.0) <= 0.01 * len(days), f"{_dy_sum} over {len(days)} days")
check("one day window per open day", len(days) == 31, str(len(days)))
check("a week window carries the MONTH's outstanding need, not its own",
      abs(weeks[0]["cats"]["dropoff"]["month_need"] - 577.0) < 0.01)
check("achieved sums to the declared total across the windows",
      abs(sum((w["cats"]["dropoff"]["achieved"] or 0.0) for w in weeks) - 23.0) < 0.02)

print("\nE. THE BACKWARD CALCULATION IS HONEST AT ITS EDGES")
b = T.backward_from_goal(1200, 23, 19, open_days_per_week=7)
check("need = goal - achieved", b["need"] == 1177.0, str(b["need"]))
check("per_day closes the need over the days left", b["per_day"] == round(1177.0 / 19, 2),
      str(b["per_day"]))
check("per_week is per_day over the open days in a week", b["per_week"] == round(b["per_day"] * 7, 2))
check("no days left → per_day is None, not the need and not infinity",
      T.backward_from_goal(1200, 23, 0)["per_day"] is None)
check("over-achievement needs 0, never a negative",
      T.backward_from_goal(100, 250, 5)["need"] == 0.0)
check("the goal is a parameter, so an arbitrary level can be asked",
      T.backward_from_goal(5000, 23, 19)["need"] == 4977.0)

print("\nF. TWO HOMES FOR ONE NUMBER ARE REFUSED")
check("a target_line naming a built-in is ignored by the fold",
      m_with["accessories"] == 5000.0, str(m_with["accessories"]))
_only_builtin = T.derive_monthly_by_cat(
    TARGET_ROW, 25.0, target_lines=[{"metric_key": "upgrades", "target_value": 7}], catalog=CATALOG)
check("…even when it is the only row", _only_builtin["upgrades"] == 40.0)
check("byod stays derived from activations x byod_pct", m_with["byod"] == 25.0, str(m_with["byod"]))

print("\n" + "=" * 98)
print(f"{len(PASS)} passed / {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1)
print("A target for a declared category is the same engine, one fold, one axis, one figure rule.")
