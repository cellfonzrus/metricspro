#!/usr/bin/env python3
"""PROOF — a stored multi-period GOAL, and whether the months add up to it (mig 1125). DB-free, stdlib.

The oracle is the owner's own ask, 2026-11-17: *"The goal is to make the store a platinum store which
is 1 Million in annual sale including the commission earned"*. Every figure below is CONFIG — the
goal, the horizon, the categories it is measured over and their labels. Neither `annual_goal.py` nor
`targets_engine.py` may spell "platinum", a category of this tenant's, or the figure 1,000,000; §G
fails the build if one appears.

  §A  the horizon vocabulary, and a spelling nobody knows
  §B  what counts is DECLARED — an empty measure is unmeasured, not zero
  §C  where today sits in the horizon
  §D  progress: need, per-month, run-rate, on pace
  §E  ONE arithmetic — the per-month rate IS the per-day home, with a different unit
  §F  do the monthly targets add up to the year
  §G  negative controls + RULE TWO
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from app.modules.commcalc import annual_goal as ag
from app.modules.commcalc import targets_engine as te

P = F = 0
def ok(cond, label, extra=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {label}")
    else:
        F += 1
        print(f"  FAIL  {label}  {extra}")
def eq(a, b, label):
    ok(a == b, label, f"got {a!r} want {b!r}")


_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.join(_HERE, "app/modules/commcalc/annual_goal.py")


def _code_only(path):
    """The file's CODE, with comments and docstrings thrown away — so a tenant's word may be QUOTED
    in prose (the owner's own ask is quoted at the top of the engine, and should be) while remaining
    forbidden in anything that executes, and so a DEREFERENCE check cannot be satisfied by a mention
    in a docstring. Tokens are joined by single spaces so a check can name a call the way the parser
    sees it (`_targets_engine . rate_to_close`) rather than guessing at the author's whitespace.

    THE DOCSTRINGS ARE DROPPED BY THE ONE HOME, `harnesslib.py_code_only`, and never by a private
    walk of STRING tokens here. A harness that classifies STRING tokens itself is blind to the 3.12
    f-string shape (an f-string arrives as FSTRING_START / FSTRING_MIDDLE / FSTRING_END, not as one
    STRING), and `harness_py_string_reader_lock` §C3 fails the build for it — correctly, because a
    guard that cannot see half the strings in a file reports absence as a fact. Comments are already
    gone for any that start a line; the COMMENT skip below catches the trailing ones.
    """
    import io, tokenize
    import harnesslib as _hl
    code = _hl.py_code_only(io.open(path, encoding="utf-8").read())
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(code).readline):
        if tok.type == tokenize.COMMENT:
            continue
        out.append(tok.string)
    return " ".join(out).lower()



# ── CONFIG. The tenant's declared categories (mig 1072) and the goal row (mig 1125). ─────────────
CATALOG = [
    {"key": "printing", "label": "Printing Services", "in_revenue": True},
    {"key": "mailbox_1_year", "label": "Mailbox 1-Year", "in_revenue": True},
    {"key": "2nd_day_3rd_day", "label": "2nd / 3rd Day Air", "in_revenue": True},
    {"key": "carrier_commission", "label": "Commission Earned", "in_revenue": True},
    {"key": "postpaid_count", "label": "Drop Off", "in_revenue": False},
]
GOAL = {"period": "2027", "grain": "year", "goal_value": 1000000.00, "label": "Platinum",
        "metric_keys": ["printing", "mailbox_1_year", "2nd_day_3rd_day", "carrier_commission"]}

print("\n§A  the horizon vocabulary")
eq(ag.GRAINS, ("year", "quarter"), "A1: two horizons, year and quarter")
eq(ag.grain_named("year"), "year", "A2: a year is a year")
eq(ag.grain_named("YEAR  "), "year", "A3: spelling is normalised, not rejected")
eq(ag.grain_named("decade"), "year", "A4: a horizon nobody knows falls to the one every tenant has")
eq(ag.months_in("year"), 12, "A5: a year is 12 months")
eq(ag.months_in("quarter"), 3, "A6: a quarter is 3 months")
ok(set(ag.GRAIN_MONTHS) == set(ag.GRAINS),
   "A7: every horizon in the vocabulary has a month count (no horizon without a denominator)")

print("\n§B  what counts is DECLARED")
eq(ag.measure_keys(GOAL), ["printing", "mailbox_1_year", "2nd_day_3rd_day", "carrier_commission"],
   "B1: the measure is the declared keys, in order")
eq(ag.measure_keys({"metric_keys": ["printing", "printing", " printing "]}), ["printing"],
   "B2: a key declared twice counts once")
_empty = ag.achieved({"metric_keys": []}, {"printing": 9999.0})
ok(_empty["measured"] is False, "B3: a goal naming no category is UNMEASURED")
eq(_empty["amount"], 0.0, "B4: and carries no amount")
ok("tick the categories" in _empty["why"].lower(),
   "B5: and says in words what to do about it — never a silent zero")
_none = ag.achieved(GOAL, {"something_else": 500.0})
ok(_none["measured"] is False,
   "B6: a goal whose categories are ALL unmeasured is unknown, not zero")
ok("unknown rather than zero" in _none["why"], "B7: and says exactly that")
_real = ag.achieved(GOAL, {"printing": 100.0, "mailbox_1_year": 0.0,
                           "2nd_day_3rd_day": 50.0, "carrier_commission": 25.0})
ok(_real["measured"] is True, "B8: every category present is measured")
eq(_real["amount"], 175.0, "B9: and sums them — a declared category with no sales is a REAL zero")
eq(_real["missing"], [], "B10: nothing missing")
_part = ag.achieved(GOAL, {"printing": 100.0, "2nd_day_3rd_day": 50.0})
ok(_part["measured"] is True, "B11: some categories measured is still measured")
eq(_part["amount"], 150.0, "B12: over what IS measured")
eq(_part["missing"], ["mailbox_1_year", "carrier_commission"], "B13: and names what is not")
ok("what the rest have earned" in _part["why"], "B14: and says the total is partial")
_named = ag.measure_named(GOAL, CATALOG)
eq([m["label"] for m in _named],
   ["Printing Services", "Mailbox 1-Year", "2nd / 3rd Day Air", "Commission Earned"],
   "B15: the measure reads back in the TENANT'S OWN WORDS")
ok(all(m["declared"] for m in _named), "B16: all four are declared")
_gone = ag.measure_named({"metric_keys": ["printing", "vanished"]}, CATALOG)
eq(len(_gone), 2, "B17: a category the catalog dropped is still LISTED, never silently removed")
ok(_gone[1]["declared"] is False, "B18: and marked undeclared")
eq(_gone[1]["label"], "vanished", "B19: under its key, since there is no label to show")

print("\n§C  where today sits in the horizon")
_w = ag.months_elapsed(GOAL, "2027-01-15")
eq((_w["elapsed"], _w["remaining"], _w["total"]), (1, 12, 12),
   "C1: in January, one month elapsed and twelve still to spend — today's month counts BOTH sides")
_w = ag.months_elapsed(GOAL, "2027-07-31")
eq((_w["elapsed"], _w["remaining"]), (7, 6), "C2: end of July, seven in and six left")
_w = ag.months_elapsed(GOAL, "2027-12-01")
eq((_w["elapsed"], _w["remaining"]), (12, 1), "C3: December is the last month, and still spendable")
_w = ag.months_elapsed(GOAL, "2028-01-01")
eq((_w["elapsed"], _w["remaining"]), (12, 0), "C4: past the horizon, nothing left")
ok("closed" in _w["why"], "C5: and says the horizon has closed")
_w = ag.months_elapsed(GOAL, "2026-11-17")
eq((_w["elapsed"], _w["remaining"]), (0, 12), "C6: before the horizon, nothing earned, everything left")
ok("not started" in _w["why"], "C7: and says it has not started")
_q = ag.months_elapsed({"period": "2027-04", "grain": "quarter"}, "2027-05-10")
eq((_q["elapsed"], _q["remaining"], _q["total"]), (2, 2, 3), "C8: a quarter places the same way")
_bad = ag.months_elapsed({"period": "next year", "grain": "year"}, "2027-05-10")
ok(_bad["placed"] is False, "C9: a period that cannot be read is NOT placed")
ok("could not be read" in _bad["why"], "C10: and says so rather than guessing a month")
eq(_bad["remaining"], 12, "C11: an unplaced horizon spends nothing yet")

print("\n§D  progress")
# Half the year gone, 40% earned: behind.
FIGS_H1 = {"printing": 200000.0, "mailbox_1_year": 60000.0,
           "2nd_day_3rd_day": 100000.0, "carrier_commission": 40000.0}
p = ag.progress(GOAL, FIGS_H1, "2027-06-20")
eq(p["achieved"], 400000.0, "D1: achieved is the sum over the measure")
eq(p["goal"], 1000000.0, "D2: the goal is the declared figure")
eq(p["need"], 600000.0, "D3: need is what is still owed")
eq(p["months_remaining"], 7, "D4: June leaves seven months including June")
eq(p["per_month"], 85714.29, "D5: and the flat rate that closes it over those seven")
eq(p["run_rate"], 66666.67, "D6: run-rate is earned / months elapsed")
eq(p["pace_needed"], 83333.33, "D7: against goal / 12")
ok(p["on_pace"] is False, "D8: 66,666 a month will not reach a million — behind")
eq(p["pct"], 40.0, "D9: 40% of the way there")
eq(p["label"], "Platinum", "D10: the tenant's own name for the goal is carried, never invented")
# Ahead.
p2 = ag.progress(GOAL, {k: v * 2 for k, v in FIGS_H1.items()}, "2027-06-20")
ok(p2["on_pace"] is True, "D11: twice the pace is ahead")
eq(p2["need"], 200000.0, "D12: and needs only the remainder")
# Over-achieved: a need of zero, never a negative.
p3 = ag.progress(GOAL, {"printing": 1200000.0}, "2027-11-01")
eq(p3["need"], 0.0, "D13: over-achievement needs NOTHING, not a negative")
eq(p3["per_month"], 0.0, "D14: and the rate that closes it is zero")
# Horizon closed: there is no per-month rate.
p4 = ag.progress(GOAL, FIGS_H1, "2028-03-01")
ok(p4["per_month"] is None,
   "D15: with no months left there is NO per-month rate — not the need restated")
eq(p4["need"], 600000.0, "D16: the shortfall is still stated")
# Unmeasured: on_pace is None, never False.
p5 = ag.progress({"period": "2027", "grain": "year", "goal_value": 1000000.0, "metric_keys": []},
                 FIGS_H1, "2027-06-20")
ok(p5["measured"] is False, "D17: a goal naming no category is unmeasured")
ok(p5["on_pace"] is None, "D18: and is NOT reported as behind — a store would act on nothing")
ok(p5["need"] is None and p5["per_month"] is None, "D19: and carries no need and no rate")
ok(p5["pct"] is None, "D20: and no percentage")
ok(p5["why"], "D21: and says why")
# Before the horizon starts: no run-rate out of zero months.
p6 = ag.progress(GOAL, FIGS_H1, "2026-11-17")
ok(p6["run_rate"] is None, "D22: no run-rate before the horizon starts (never a divide by zero)")
ok(p6["on_pace"] is None, "D23: and no verdict")
eq(p6["per_month"], 50000.0, "D24: but the rate that would close it over twelve months IS known")

print("\n§E  ONE arithmetic, two units")
_r = te.rate_to_close(1000.0, 400.0, 6)
eq((_r["need"], _r["per_unit"], _r["units_left"]), (600.0, 100.0, 6),
   "E1: rate_to_close subtracts and divides")
ok(te.rate_to_close(1000.0, 400.0, 0)["per_unit"] is None,
   "E2: no units left means NO rate, not the need")
eq(te.rate_to_close(100.0, 500.0, 5)["need"], 0.0, "E3: over-achievement needs zero")
ok("per_day" not in _r and "per_month" not in _r,
   "E4: the home does NOT name the unit — a caller labels it, so a month can never wear 'per day'")
# The per-day caller's contract is unchanged, byte for byte.
_b = te.backward_from_goal(1000.0, 400.0, 6, open_days_per_week=5.0)
eq(sorted(_b), ["achieved", "goal", "need", "open_days_left", "open_days_per_week", "per_day",
                "per_week"] if "open_days_per_week" in _b else
   ["achieved", "goal", "need", "open_days_left", "per_day", "per_week"],
   "E5: backward_from_goal's own fields are unchanged")
eq((_b["need"], _b["per_day"], _b["per_week"]), (600.0, 100.0, 500.0),
   "E6: and it still answers exactly what it answered before")
ok(te.backward_from_goal(1000.0, 400.0, 0)["per_day"] is None,
   "E7: including no rate with no days left")
# THE LOCK: the annual progress rate IS the shared home's answer, not a second division.
_shared = te.rate_to_close(1000000.0, 400000.0, 7)["per_unit"]
eq(ag.progress(GOAL, FIGS_H1, "2027-06-20")["per_month"], _shared,
   "E8: the annual per-month rate IS rate_to_close's answer — one arithmetic, proven equal")
# THE LOCK THAT CANNOT BE SATISFIED BY PROSE. `rate_to_close` is NAMED in the engine's docstring,
# so a text search of the whole file passes even when the code has grown its own copy — which is
# exactly how this check was first written, and exactly what arming it exposed. The dereference must
# appear in code that EXECUTES, and the engine must not carry the subtraction or the division itself.
_ecode = _code_only(_ENGINE)
ok("rate_to_close" in _ecode,
   "E9: annual_goal DEREFERENCES rate_to_close in executing code, not merely in prose")
ok("_targets_engine . rate_to_close" in _ecode or "_targets_engine.rate_to_close" in _ecode,
   "E10: through the engine it imported, so the one home is the one that answers")
ok(_ecode.count("rate_to_close") >= 1 and "/ win [ \" remaining \" ]" not in _ecode,
   "E11: and does not divide by the remaining months itself")
ok("max ( 0.0 , goal -" not in _ecode and "max(0.0, goal -" not in _ecode,
   "E12: nor subtract the achieved figure from the goal itself")

print("\n§F  do the monthly targets add up to the year")
r = ag.reconcile_monthly_targets(GOAL, 960000.0, months_counted=12)
eq(r["planned"], 960000.0, "F1: the plan is what the monthly targets sum to")
eq(r["gap"], 40000.0, "F2: and it is 40,000 short of the goal")
ok(r["reconciles"] is False, "F3: so it does not reconcile")
eq(r["months_without_target"], 0, "F4: with every month carrying a target")
eq(r["why"], "", "F5: so there is nothing to excuse it")
r2 = ag.reconcile_monthly_targets(GOAL, 1000000.0, months_counted=12)
ok(r2["reconciles"] is True, "F6: a plan that meets the goal reconciles")
eq(r2["gap"], 0.0, "F7: with no gap")
r3 = ag.reconcile_monthly_targets(GOAL, 1200000.0, months_counted=12)
ok(r3["reconciles"] is True, "F8: a plan ABOVE the goal reconciles")
eq(r3["gap"], -200000.0, "F9: and the gap states the headroom rather than clamping to zero")
r4 = ag.reconcile_monthly_targets(GOAL, 320000.0, months_counted=4)
eq(r4["months_without_target"], 8, "F10: eight months carry no target")
ok("incomplete" in r4["why"],
   "F11: and the shortfall says it is partly because the plan is INCOMPLETE — a different "
   "conversation from twelve targets each set too low")
r5 = ag.reconcile_monthly_targets({"period": "2027", "grain": "year", "goal_value": 1000000.0,
                                   "metric_keys": []}, 500000.0)
ok(r5["reconciles"] is None, "F12: a goal naming no category cannot reconcile — None, not False")
ok(r5["gap"] is None, "F13: and has no gap")
ok(r5["why"], "F14: and says why")

print("\n§G  negative controls + RULE TWO")
_src = open(_ENGINE).read().lower()
_code = _code_only(_ENGINE)
for _w in ("platinum", "mailbox", "printing", "ups", "ny logistics", "overnight", "drop off"):
    ok(_w not in _code, f"G-{_w}: no tenant word EXECUTES in the engine (RULE TWO)")
# A FIGURE is forbidden outright, prose included: a goal amount written into a comment is how the
# next reader learns to treat one tenant's number as the shape of the feature.
for _w in ("1000000", "1_000_000", "1,000,000"):
    ok(_w not in _src, f"G-{_w}: the engine does not carry a tenant's goal figure ANYWHERE")
ok("import" in _src and "sqlalchemy" not in _src and "supabase" not in _src and "requests" not in _src,
   "G-pure: the engine reaches no database and no network")
# A CALENDAR is allowed (the last day of a month needs one, leap years included); a CLOCK is not.
# `today` is always the caller's, which is what lets this file place any day it likes.
for _c in ("date.today", "datetime.now", "utcnow", "time.time"):
    ok(_c not in _code, f"G-clock-{_c}: the engine reads no clock — `today` is the caller's")
eq(ag.measure_keys(None), [], "G1: no goal row is an empty measure, never a crash")
eq(ag.progress(None, None, "2027-06-01")["goal"], 0.0, "G2: no goal row is a goal of zero")
ok(ag.progress(None, None, "2027-06-01")["measured"] is False, "G3: and unmeasured")
eq(ag.achieved(GOAL, None)["measured"], False, "G4: no figures at all is unmeasured")
eq(ag.reconcile_monthly_targets(GOAL, None, 0)["planned"], 0.0,
   "G5: no monthly targets is a plan of zero")
eq(ag.reconcile_monthly_targets(GOAL, None, 0)["months_without_target"], 12,
   "G6: and every month lacks one")

print("\n§H  the horizon's calendar days")
eq(ag.horizon_bounds(GOAL), ("2027-01-01", "2027-12-31"), "H1: a year runs Jan 1 to Dec 31")
eq(ag.horizon_bounds({"period": "2027-04", "grain": "quarter"}), ("2027-04-01", "2027-06-30"),
   "H2: Q2 runs Apr 1 to Jun 30")
eq(ag.horizon_bounds({"period": "2027-11", "grain": "quarter"}), ("2027-11-01", "2028-01-31"),
   "H3: a quarter may cross the year end")
eq(ag.horizon_bounds({"period": "2027-12", "grain": "quarter"}), ("2027-12-01", "2028-02-29"),
   "H4: and lands on a LEAP day where the calendar has one")
eq(ag.horizon_bounds({"period": "2026-12", "grain": "quarter"}), ("2026-12-01", "2027-02-28"),
   "H5: and on the 28th where it does not")
eq(ag.horizon_bounds({"period": "nope", "grain": "year"}), (None, None),
   "H6: a period that cannot be read has NO bounds — never a silently wrong span")
eq(ag.horizon_bounds({"period": "2027-13", "grain": "quarter"}), (None, None),
   "H7: nor does a month that does not exist")
eq(ag.horizon_bounds(None), (None, None), "H8: no goal row has no bounds, never a crash")
_b = ag.horizon_bounds(GOAL)
_w = ag.months_elapsed(GOAL, _b[1])
eq(_w["elapsed"], 12, "H9: the horizon's LAST day is inside it — bounds and months agree")
ok(ag.months_elapsed(GOAL, "2028-01-01")["remaining"] == 0,
   "H10: and the day after the last is outside it")

print(f"\n{'='*70}\n  {P} passed / {F} failed\n{'='*70}")
sys.exit(1 if F else 0)
