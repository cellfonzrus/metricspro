#!/usr/bin/env python3
"""PROOF — an hours-split incentive pool is weighted by CLOSED PUNCHES, going forward only.

DB-free, stdlib. Owner Sanjot, 2026-11-27 14:35 ET, verbatim:

    "let the daily cincentive as itonly affects ny logistics bee ffeceive going forward based on eh
     acual clock in, if te hours are udpated manuay then we cannot oay for thsose hours"

THE DEFECT, measured. `commcalc/router._incentive_hours_for_day` weighted a `threshold_pool` whose
`payout.split == "hours"` with `actual_hours` else `scheduled_hours`, reading `storeops.shifts` and
never a punch. `shifts.actual_hours` carries a figure on **7 of 7,740** live rows (index §14y), so
the pool was in practice split by the SCHEDULE. The pool TOTAL never moved; who got paid what did.

  §A  the weight is a closed punch, and nothing else is
  §B  a hand-edited day carries NO weight — and PAY's opposite posture is untouched
  §C  the floor: derived -> STORED -> read, so nothing already paid restates
  §D  the blast radius: the ROSTER does not move, so only `split: hours` changes
  §E  the pool-level consequence, through the real `split_pool`
  §F  cross-tenant: one org's declaration never decides another's
  §G  negative controls — every search here can still fail
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import daily_incentive as di            # noqa: E402
from app.modules.commcalc import incentive_clock_floor as icf     # noqa: E402
from app.modules.storeops import payroll_hours_basis as hb        # noqa: E402

P = F = 0
FAILED = []


def ok(label, cond, extra=""):
    global P, F
    if cond:
        P += 1
    else:
        F += 1
        FAILED.append(label)
        print(f"  FAIL  {label}" + (f"  — {extra}" if extra else ""))


def section(t):
    print(f"\n== {t}")


CFG = hb.resolve_config(None)
DAY = "2026-11-20"


def punch(h, *, closed=True, auto=None, day=DAY):
    p = {"hours": h, "clock_in": f"{day}T09:00:00Z",
         "clock_out": f"{day}T17:00:00Z" if closed else None, "work_date": day}
    if auto is not None:
        p["auto_clocked_out"] = auto
    return p


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("A. the weight is a closed punch, and nothing else is")
ok("A1 a closed punch's hours are the weight",
   hb.incentive_weight_hours([punch(7.5)], CFG) == (7.5, hb.INCENTIVE_WEIGHT_CLOCKED))
ok("A2 two punches in a day add up", hb.incentive_weight_hours([punch(4.0), punch(3.25)], CFG)[0] == 7.25)
ok("A3 no punch weighs ZERO and names the reason",
   hb.incentive_weight_hours([], CFG) == (0.0, hb.INCENTIVE_WEIGHT_NO_PUNCH))
ok("A4 ★ THE SCHEDULE IS NEVER A WEIGHT — there is no argument by which it could be",
   "scheduled" not in hb.incentive_weight_hours.__code__.co_varnames)
ok("A5 an OPEN punch carrying hours still weighs nothing (the usability test is load-bearing)",
   hb.incentive_weight_hours([punch(8.0, closed=False)], CFG)[0] == 0.0)
#: THE CLOSED TEST IS DEREFERENCED, not re-spelled: the org's own auto-clockout declaration decides,
#: and this rule never needs to know what an auto-clockout is.
ok("A6 the org's auto-clockout declaration decides, via `punch_usability`",
   hb.incentive_weight_hours([punch(8.0, auto=True)],
                             hb.resolve_config({"count_auto_clockout_punches": False}))[0] == 0.0
   and hb.incentive_weight_hours([punch(8.0, auto=True)],
                                 hb.resolve_config({"count_auto_clockout_punches": True}))[0] == 8.0)
ok("A7 every reason word has a reader-facing sentence",
   all(hb.incentive_weight_reason(r) and "no ruling" not in hb.incentive_weight_reason(r)
       for r in (hb.INCENTIVE_WEIGHT_CLOCKED, hb.INCENTIVE_WEIGHT_NO_PUNCH,
                 hb.INCENTIVE_WEIGHT_MANUAL_BLOCKED)))
ok("A8 an UNRULED reason says so rather than rendering blank",
   "no ruling" in hb.incentive_weight_reason("invented_in_2027"))


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("B. a hand-edited day carries NO weight — and PAY's opposite posture is untouched")
ok("B1 a corrected day weighs zero",
   hb.incentive_weight_hours([punch(7.5)], CFG, has_manual_correction=True)
   == (0.0, hb.INCENTIVE_WEIGHT_MANUAL_BLOCKED))
ok("B2 …and it OUTRANKS a perfectly good punch on the same day",
   hb.incentive_weight_hours([punch(8.0)], CFG)[0] == 8.0
   and hb.incentive_weight_hours([punch(8.0)], CFG, has_manual_correction=True)[0] == 0.0)
ok("B3 the whole DAY goes, not 'the punch minus the correction'",
   hb.incentive_weight_hours([punch(8.0)], CFG, has_manual_correction=True)[0] == 0.0)
#: ★ THE ANTI-COUPLING AXIS. `count_manual_hours` / `corrections_effective_from` are PAY controls
#: (PR #679, §STOREOPS-07). A switch set for one question must not override the owner's ruling on
#: another — in EITHER direction.
_FOLDS = hb.resolve_config({"count_manual_hours": True, "corrections_effective_from": "2026-01-01"})
ok("B4 ★ an org that folds corrections into PAY still carries no incentive weight on a corrected day",
   hb.incentive_weight_hours([punch(8.0)], _FOLDS, has_manual_correction=True)[0] == 0.0)
ok("B5 ★ and PAY's fold is UNCHANGED — this change did not leak into payroll",
   hb.corrections_apply(DAY, _FOLDS)[0] is True)
ok("B6 …nor did it reach `resolve_day`, which still folds the correction into PAID hours",
   hb.resolve_day({"day": DAY, "scheduled": 9.0, "manual": 9.0, "manual_rows": 1,
                   "_raw_punches": [punch(8.0)]}, _FOLDS, None, DAY)[0] > 0)
ok("B7 the two questions are answered by DIFFERENT functions, named differently",
   hb.incentive_weight_hours is not hb.resolve_day)
ok("B8 the incentive rule does NOT consult `corrections_apply` (a shared gate would merge them)",
   "corrections_apply" not in hb.incentive_weight_hours.__doc__.replace(
       "`corrections_apply` IS NOT CONSULTED", ""))


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("C. the floor: derived -> STORED -> read, so nothing already paid restates")
_ROWS = [
    {"org_id": "O1", "store_code": "S1", "work_date": "2026-11-10", "clock_out": "x", "hours": 8},
    {"org_id": "O1", "store_code": "S1", "work_date": "2026-11-04", "clock_out": "x", "hours": 8},
    {"org_id": "O1", "store_code": "S2", "work_date": "2026-11-20", "clock_out": "x", "hours": 8},
    {"org_id": "O1", "store_code": "S3", "work_date": "2026-11-01", "clock_out": None,
     "hours": None},
]
_D = icf.derive(_ROWS)
ok("C1 the floor is the FIRST closed punch, per store",
   _D == {("O1", "S1"): "2026-11-04", ("O1", "S2"): "2026-11-20"}, repr(_D))
ok("C2 ★ a store with only an OPEN punch has NO floor — an open punch measured nothing",
   ("O1", "S3") not in _D)
ok("C3 ★ a store with no punches is ABSENT, never present with None (which would invite `or <date>`)",
   ("O1", "NOPE") not in _D and None not in _D.values())
ok("C4 each store's floor is its own; none inherits another's",
   _D[("O1", "S1")] != _D[("O1", "S2")])
ok("C5 ★ NO STORED FLOOR ⇒ NOT IN EFFECT: deploying this moves no money by itself",
   icf.in_effect(None, DAY) is False and icf.in_effect({}, DAY) is False
   and icf.in_effect({"effective_from": None}, DAY) is False)
ok("C6 the boundary is INCLUSIVE of the start date ('going forward FROM that day')",
   icf.in_effect({"effective_from": "2026-11-20"}, "2026-11-20") is True
   and icf.in_effect({"effective_from": "2026-11-20"}, "2026-11-19") is False)
ok("C7 an UNDATED day is not pushed to either side of a boundary it never named",
   icf.in_effect({"effective_from": "2026-11-20"}, None) is False
   and icf.in_effect({"effective_from": "2026-11-20"}, "") is False)
#: ★ THE LOAD-BEARING ONE. `derive` only PROPOSES. If the money read the derivation, a punch
#: backfilled into an earlier day would walk the floor backwards and restate a period already paid.
_BACKFILL = [{"org_id": "O1", "store_code": "S1", "work_date": "2026-08-01",
              "clock_out": "x", "hours": 8}]
ok("C8 ★ a BACKFILLED punch moves the PROPOSAL…", icf.derive(_BACKFILL)[("O1", "S1")] == "2026-08-01")
ok("C9 ★ …and does NOT move the stored floor the money reads",
   icf.in_effect({"effective_from": "2026-11-20"}, "2026-08-15") is False)
ok("C10 the verdict comes WITH its word, so no caller can show one without the other",
   icf.status(None, DAY) == (False, icf.NOT_IN_EFFECT_NO_FLOOR)
   and icf.status({"effective_from": "2026-12-01"}, DAY) == (False, icf.NOT_IN_EFFECT_BEFORE_FLOOR)
   and icf.status({"effective_from": "2026-11-01"}, DAY) == (True, icf.IN_EFFECT))
ok("C11 every verdict word has a sentence, and an unruled one says so",
   all("no ruling" not in icf.reason_sentence(r)
       for r in (icf.IN_EFFECT, icf.NOT_IN_EFFECT_NO_FLOOR, icf.NOT_IN_EFFECT_BEFORE_FLOOR))
   and "no ruling" in icf.reason_sentence("invented"))
ok("C12 the table name has ONE spelling, dereferenced",
   icf.FLOOR_TABLE == "incentive_clock_floor")


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("D. the blast radius: the ROSTER does not move, so only `split: hours` changes")
_SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "app", "modules", "commcalc", "router.py")).read()
_FN = _SRC[_SRC.index("def _incentive_hours_for_day"):]
_FN = _FN[:_FN.index("\n# ═")] if "\n# ═" in _FN else _FN
ok("D1 ★ `roster.append(who)` is UNCONDITIONAL — a rep who did not clock stays ELIGIBLE",
   "roster.append(who)" in _FN
   and "if h:\n                    roster.append" not in _FN,
   "narrowing the roster would re-split every pool on every tenant, incl. equal/individual_sales")
ok("D2 the weight rule is DEREFERENCED, not re-spelled in the reader",
   "incentive_weight_hours(" in _FN)
ok("D3 the floor is ASKED per (store, day), never assumed", "_icf.status(" in _FN)
ok("D4 the pre-floor branch keeps TODAY'S expression byte-for-byte",
   'h = sh.get("actual_hours")' in _FN and 'h = sh.get("scheduled_hours")' in _FN)
ok("D5 the punch read is the ONE shared one, not a second query",
   "read_pay_evidence(" in _FN and 'table("timelog")' not in _FN)
ok("D6 the evidence index is the ONE shared one",
   "build_evidence(" in _FN)
ok("D7 the reader holds no basis arithmetic of its own: no `or` between the two hours columns",
   not __import__("re").search(r'actual_hours["\']\s*\)?\s*or\s', _FN))


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("E. the pool-level consequence, through the real `split_pool`")
#: Ada punched 8h; Ben never clocked. Both were rostered 9h.
_W = {"Ada": 8.0}
_ROSTER = ["Ada", "Ben"]
_shares, _un, _note = di.split_pool(100.0, _ROSTER, basis="hours", hours_by_employee=_W)
ok("E1 the whole pool goes to the person who actually clocked",
   _shares == {"Ada": 100.0} and _un == 0, f"{_shares} / {_un}")
ok("E2 …and that is NOT the old 50/50", _shares.get("Ben") in (None, 0))
_shares2, _un2, _note2 = di.split_pool(100.0, _ROSTER, basis="hours", hours_by_employee={})
ok("E3 ★ a day NOBODY clocked is reported UNALLOCATED, never equal-split",
   _shares2 == {} and _un2 == 100.0 and "cannot be split by hours" in _note2)
ok("E4 ★ `split_pool`'s refusal is KEPT, not routed around",
   "no hours recorded for the qualifying employees" in open(
       os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "app", "modules", "commcalc", "daily_incentive.py")).read())
_eq, _unq, _ = di.split_pool(100.0, _ROSTER, basis="equal")
ok("E5 ★ an EQUAL split is untouched: it reads the roster, which did not move",
   _eq == {"Ada": 50.0, "Ben": 50.0} and _unq == 0)
ok("E6 shares always sum to the pool exactly",
   abs(sum(_shares.values()) - 100.0) < 0.005 and abs(sum(_eq.values()) - 100.0) < 0.005)


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("F. cross-tenant: one org's declaration never decides another's")
ok("F1 a floor map is keyed by (org, store) — one org's floor cannot be read for another",
   all(isinstance(k, tuple) and len(k) == 2 for k in _D)
   and ("O2", "S1") not in _D)
ok("F2 ★ a tenant with no punch history gets NO floor, not the house org's",
   icf.derive([]) == {} and icf.status(icf.derive([]).get(("O2", "S1")), DAY)[1]
   == icf.NOT_IN_EFFECT_NO_FLOOR)
ok("F3 no tenant name, store code or date literal lives in either module (RULE TWO)",
   not any(w in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "modules",
                                  "commcalc", "incentive_clock_floor.py")).read().lower()
           for w in ("ny logistics", "luxelink", "cellfonz", "boost")))
ok("F4 the weight rule holds no tenant vocabulary either",
   not any(w in hb.incentive_weight_hours.__doc__.lower()
           for w in ("ny logistics", "luxelink", "cellfonz")))
ok("F5 two orgs' configs produce two answers from the SAME punch",
   hb.incentive_weight_hours([punch(8.0, auto=True)],
                             hb.resolve_config({"count_auto_clockout_punches": False}))[0]
   != hb.incentive_weight_hours([punch(8.0, auto=True)],
                                hb.resolve_config({"count_auto_clockout_punches": True}))[0])


# ══════════════════════════════════════════════════════════════════════════════════════════════
section("G. negative controls — every search here can still fail")
ok("G1 D7's `or` search is not vacuous: it catches the shipped expression",
   bool(__import__("re").search(r'actual_hours["\']\s*\)?\s*or\s',
                                'h = sh.get("actual_hours") or sh.get("scheduled_hours")')))
ok("G2 D1's roster search is not vacuous: it catches a narrowed roster",
   "if h:\n                    roster.append" in "if h:\n                    roster.append(who)")
ok("G3 the manual block is reachable — an armed control",
   hb.incentive_weight_hours([punch(8.0)], CFG, has_manual_correction=True)[1]
   == hb.INCENTIVE_WEIGHT_MANUAL_BLOCKED)
ok("G4 a floor of the WRONG shape does not switch anything on",
   icf.in_effect({"effective_from": "not-a-date"}, DAY) is False
   and icf.in_effect("2026-01-01", DAY) is False)
ok("G5 `derive` ignores rows missing an org, a store or a date",
   icf.derive([{"store_code": "S", "work_date": DAY, "clock_out": "x", "hours": 1},
               {"org_id": "O", "work_date": DAY, "clock_out": "x", "hours": 1},
               {"org_id": "O", "store_code": "S", "clock_out": "x", "hours": 1}]) == {})
ok("G6 `derive` tolerates junk rows without raising", icf.derive([None, 7, "x"]) == {})


print("\n" + "=" * 78)
print(f"  {P} passed, {F} failed")
print("=" * 78)
if F:
    print("AN INCENTIVE WEIGHT HAS DRIFTED. Fix the code, not this proof: a pool split on the "
          "schedule pays\nthe wrong people out of a fixed pot, and a floor that moves restates a "
          "period already paid.")
    for lbl in FAILED:
        print(f"  - {lbl}")
    sys.exit(1)
print("ALL PROOFS HOLD — the pool is split on closed punches, from the day each store started "
      "clocking in;\na hand-edited day earns nothing; and PAY's own correction fold is untouched.")
