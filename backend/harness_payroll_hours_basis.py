"""HARNESS — THE ONE HOME for paid hours, and the regression that reproduces the reported defect.

OWNER ASK 2026-10-10: *"for the payroll menu need to turn off the payroll automatically being
calculated as per schedule, it is only based on actual hours clocked in unless a button is checked
for each employee separately against that employee name."*

THE DEFECT THIS REPRODUCES (§R). Paid hours were decided by `actual_hours or scheduled_hours` in six
places, over a column effectively nothing writes — MEASURED on production 2026-10-10, SEVEN of 7,740
`storeops.shifts` rows carry `actual_hours > 0`, while `storeops.timelog` holds 3,555 punches with
hours and `storeops.manual_hours` holds 569 corrections that `GET /storeops/payroll` has never read.
So the `or` fell through to the schedule on essentially every row and no reader was told. §R builds
the pre-fix expression and shows it paying the schedule where a punch measured the day, then shows
the one home refusing to do that under a clocked-only basis.

SECTIONS
  A. The basis vocabulary is an ALLOWLIST, and an unruled basis does not pay.
  B. Config: the shipped default is TODAY, and the two mode-dependent forcings are deliberate.
  C. The per-employee tick box, and why NULL falls to the org default on a pay path.
  D. Precedence: correction > usable punch > measured zero > schedule opt-in > unpaid.
  E. An OPEN or AUTO-CLOSED punch is not silently paid.
  F. `resolve_shift_row` reproduces `actual_hours or scheduled_hours` EXACTLY under the default.
  G. Coverage and THE INTERLOCK — partial coverage is refused and REPORTED, never paid as zero.
  H. `measurement_state` — the measurement question asked on its own.
  I. Determinism and degradation.
  R. THE REGRESSION: payroll silently computed from the schedule.
  P. THE 2026-10-12 REGRESSION: a pay path reaching the one home with NO EVIDENCE, and the
     $374,733.02 -> $0.00 it produced over Jun–Oct 2026 on the live house config.
  Q. THE EFFECTIVE-FROM FLOOR (owner ruling 2026-10-12, mig 1074): the clocked basis starts on a
     declared date, every day before it keeps today's schedule answer, and coverage + the interlock
     count only days on or after it.
  O. THE OWNER RULING: the clock report as a PAY SOURCE, pinned to the measured day of 2026-10-09.
  Z. ARMED negative control.

Run: cd backend && python3 harness_payroll_hours_basis.py   (DB-free, stdlib only)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.storeops import payroll_hours_basis as B  # noqa: E402
from app.modules.storeops import payroll_evidence as PE  # noqa: E402

PASS, FAIL = [], []


def check(label, got, want):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def section(t):
    print(f"\n── {t}")


DEFAULT = B.resolve_config(None)
CLOCKED = B.resolve_config({"hours_basis_mode": B.MODE_CLOCKED_ONLY,
                            "pay_from_schedule_default": False})


def shift(day="2026-10-09", sched=9.0, act=0.0, eid="E1"):
    return {"employee_id": eid, "shift_date": day, "scheduled_hours": sched, "actual_hours": act}


def punch(day="2026-10-09", hours=8.5, eid="E1", closed=True, auto=False):
    return {"employee_id": eid, "work_date": day,
            "hours": hours if closed else None,
            "clock_out": f"{day}T19:00:00Z" if closed else None,
            "auto_clocked_out": auto}


def manual(day="2026-10-09", hours=7.75, eid="E1"):
    return {"employee_id": eid, "work_date": day, "hours": hours}


def one(shifts=(), punches=(), manual_rows=(), cfg=DEFAULT, emp=None, day="2026-10-09", eid="E1"):
    ev = B.build_evidence(shifts=shifts, punches=punches, manual_rows=manual_rows)
    return B.resolve_day(ev.get((eid, day)), cfg, emp)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("A. the basis vocabulary is an ALLOWLIST")
check("A1 every ruled basis is in ALL_BASES", sorted(B.BASIS_REASONS) == sorted(B.ALL_BASES), True)
# The list is spelled out HERE, by hand, on purpose: it is the allowlist, so adding a basis to the
# module must break this line and make somebody say out loud whether the new basis pays.
check("A2 the paying bases are exactly the six ruled payable ones",
      sorted(B.PAYING_BASES),
      sorted([B.CLOCKED, B.CLOCKED_CORRECTED, B.CLOCKED_RECONCILED, B.CLOCKED_OVERRIDE,
              B.MEASURED_ZERO, B.SCHEDULE_OPT_IN]))
check("A3 NON_PAYING_BASES is DERIVED, so it cannot drift from the allowlist",
      sorted(B.NON_PAYING_BASES),
      sorted([b for b in B.ALL_BASES if b not in B.PAYING_BASES]))
check("A4 not_measured_unpaid does NOT pay", B.basis_pays_hours(B.NOT_MEASURED_UNPAID), False)
check("A5 punch_unusable does NOT pay", B.basis_pays_hours(B.PUNCH_UNUSABLE), False)
# THE DIRECTION THE NEXT MISTAKE FALLS IN. A basis nobody has ruled on must read as NOT payable, or a
# basis added later could silently start paying the schedule again — which is this whole defect.
check("A6 an UNRULED basis does not pay (allowlist, not denylist)",
      B.basis_pays_hours("some_basis_added_in_2027"), False)
check("A7 a missing basis does not pay", B.basis_pays_hours(None), False)
# A reconciled day and an overridden day ARE measurements: in both cases somebody — the two clock
# records agreeing, or a named DM — observed the day. Only the schedule opt-in pays without observing.
check("A8 measurement bases are the five that observed the day",
      sorted(B.MEASURED_BASES),
      sorted([B.CLOCKED, B.CLOCKED_CORRECTED, B.CLOCKED_RECONCILED, B.CLOCKED_OVERRIDE,
              B.MEASURED_ZERO]))
check("A9 schedule_opt_in PAYS but is NOT a measurement — the distinction the `or` destroyed",
      (B.basis_pays_hours(B.SCHEDULE_OPT_IN), B.basis_is_measurement(B.SCHEDULE_OPT_IN)),
      (True, False))
check("A10 an unruled basis still gets an explaining sentence, never a blank label",
      "no ruling" in B.basis_reason("whatever"), True)

section("B. config — the shipped default IS today")
check("B1 the house default mode is schedule-fallback (today's behaviour)",
      DEFAULT["hours_basis_mode"], B.MODE_SCHEDULE_FALLBACK)
check("B2 …and the org-level tick-box default is TRUE, so nobody is unpaid on apply",
      DEFAULT["pay_from_schedule_default"], True)
# The two mode-dependent forcings, each deliberate and each about not moving money on deploy:
check("B3 manual_hours are NOT folded into pay in the default mode (569 live rows /payroll never read)",
      DEFAULT["count_manual_hours"], False)
check("B4 …and they ARE the correction mechanism once clocked-only is on",
      CLOCKED["count_manual_hours"], True)
check("B5 an auto-closed punch IS counted in the default mode, because it already is today",
      DEFAULT["count_auto_clockout_punches"], True)
check("B6 …and is NOT a measurement under clocked-only",
      CLOCKED["count_auto_clockout_punches"], False)
check("B7 an org that explicitly says FALSE is honoured even in the default mode",
      B.resolve_config({"count_auto_clockout_punches": False})["count_auto_clockout_punches"], False)
check("B8 an unknown mode string degrades to today, never to clocked-only",
      B.resolve_config({"hours_basis_mode": "something_else"})["hours_basis_mode"],
      B.MODE_SCHEDULE_FALLBACK)
check("B9 a nonsense coverage threshold is clamped, never crashes a pay path",
      (B.resolve_config({"min_punch_coverage_pct": "abc"})["min_punch_coverage_pct"],
       B.resolve_config({"min_punch_coverage_pct": 400})["min_punch_coverage_pct"]),
      (85.0, 100.0))
check("B10 an empty row == the house defaults", B.resolve_config({}), DEFAULT)

section("C. the per-employee tick box")
check("C1 an employee ticked ON is paid from the schedule",
      B.pay_from_schedule({"pay_from_schedule": True}, CLOCKED), True)
check("C2 an employee ticked OFF is not", B.pay_from_schedule({"pay_from_schedule": False}, DEFAULT), False)
# A pre-1073 database has no column at all. On a PAY path a migration gap must fail toward today,
# never toward paying nobody.
check("C3 NULL falls to the org default (a pre-1073 database is byte-identical to today)",
      B.pay_from_schedule({}, DEFAULT), True)
check("C4 …and under a clocked-only org the same NULL falls to ITS default",
      B.pay_from_schedule({}, CLOCKED), False)
check("C5 the per-employee tick BEATS the org default in both directions",
      (B.pay_from_schedule({"pay_from_schedule": True}, CLOCKED),
       B.pay_from_schedule({"pay_from_schedule": False}, DEFAULT)), (True, False))
check("C6 the audit entry point is the one the payroll change log already uses",
      (B.CHANGE_ENTRY_POINT, B.CHANGE_FIELD), ("pay_from_schedule_change", "pay_from_schedule"))

section("D. precedence — correction > usable punch > measured zero > opt-in > unpaid")
check("D1 a manual correction wins over a punch, under clocked-only",
      one(shifts=[shift()], punches=[punch(hours=99.0)], manual_rows=[manual(hours=7.75)],
          cfg=CLOCKED)[:2], (7.75, B.CLOCKED_CORRECTED))
check("D2 a DM's shifts.actual_hours edit is the older spelling of the same act",
      one(shifts=[shift(act=8.25)], punches=[punch(hours=99.0)], cfg=CLOCKED)[:2],
      (8.25, B.CLOCKED_CORRECTED))
check("D3 a usable closed punch beats the schedule",
      one(shifts=[shift(sched=9.0)], punches=[punch(hours=8.5)], cfg=CLOCKED)[:2],
      (8.5, B.CLOCKED))
check("D4 a usable punch of ZERO hours is a MEASURED ZERO, never a fallback",
      one(shifts=[shift(sched=9.0)], punches=[punch(hours=0.0)], cfg=CLOCKED)[:2],
      (0.0, B.MEASURED_ZERO))
check("D5 nothing measured + the tick box ON pays the SCHEDULE and says so",
      one(shifts=[shift(sched=9.0)], cfg=DEFAULT)[:2], (9.0, B.SCHEDULE_OPT_IN))
check("D6 nothing measured + the tick box OFF pays NOTHING and SAYS SO",
      one(shifts=[shift(sched=9.0)], cfg=CLOCKED)[:2], (0.0, B.NOT_MEASURED_UNPAID))
check("D7 no evidence at all is NOT_MEASURED_UNPAID with 0 — absence is never a zero measurement",
      B.resolve_day(None, CLOCKED)[:2], (0.0, B.NOT_MEASURED_UNPAID))
check("D8 a NEGATIVE manual correction is still a measurement (there are negative rows live)",
      one(punches=[punch(hours=8.0)], manual_rows=[manual(hours=-2.0)], cfg=CLOCKED)[:2],
      (-2.0, B.CLOCKED_CORRECTED))
_h, _b, _detail = one(shifts=[shift(sched=9.0)], cfg=CLOCKED)
check("D9 the detail names the schedule it DID NOT pay, so nothing is invisible",
      _detail["scheduled_hours"], 9.0)
check("D10 …and says the figure does not pay", (_detail["pays"], _detail["measured"]), (False, False))

section("E. an open or auto-closed punch is not silently paid")
check("E1 an OPEN punch is not a measurement, by default",
      B.punch_usability(punch(closed=False), DEFAULT), (False, B.PUNCH_OPEN))
check("E2 …and an unmeasured, un-ticked day with one is PUNCH_UNUSABLE, not scheduled",
      one(punches=[punch(closed=False)], cfg=CLOCKED)[:2], (0.0, B.PUNCH_UNUSABLE))
check("E3 an AUTO-CLOSED punch is not a measurement under clocked-only",
      B.punch_usability(punch(auto=True), CLOCKED), (False, B.PUNCH_AUTO))
# THE LIVE CASE. `clockin_evidence.py` documents a punch left open five days that auto-closed for
# 122.19h; the real export shows the same failure at 2.96h over, at the wrong store.
check("E4 a 122.19h auto-closed runaway pays NOTHING under clocked-only",
      one(shifts=[shift(sched=9.0)], punches=[punch(hours=122.19, auto=True)], cfg=CLOCKED)[:2],
      (0.0, B.PUNCH_UNUSABLE))
check("E5 …and the unusable punch is REPORTED, not dropped",
      one(shifts=[shift(sched=9.0)], punches=[punch(hours=122.19, auto=True)],
          cfg=CLOCKED)[2]["unusable_punches"], [B.PUNCH_AUTO])
check("E6 under the DEFAULT mode that same punch still pays, because it already does today",
      one(shifts=[shift(sched=9.0)], punches=[punch(hours=122.19, auto=True)], cfg=DEFAULT)[:2],
      (122.19, B.CLOCKED))
check("E7 an unusable punch on a TICKED employee pays the schedule — and still reports the punch",
      [one(shifts=[shift(sched=9.0)], punches=[punch(auto=True)], cfg=CLOCKED,
           emp={"pay_from_schedule": True})[i] for i in (0, 1)]
      + [one(shifts=[shift(sched=9.0)], punches=[punch(auto=True)], cfg=CLOCKED,
             emp={"pay_from_schedule": True})[2]["unusable_punches"]],
      [9.0, B.SCHEDULE_OPT_IN, [B.PUNCH_AUTO]])
check("E8 an org may declare an open punch a measurement, and then it is one",
      B.punch_usability(punch(closed=False), B.resolve_config({"count_open_punches": True})),
      (True, None))

section("F. resolve_shift_row reproduces `actual_hours or scheduled_hours` EXACTLY")


def pre_fix(s):
    """The expression this change replaced, in all six of its spellings (they are the same)."""
    return float(s.get("actual_hours") or 0) or float(s.get("scheduled_hours") or 0)


_cases = [shift(act=0.0, sched=9.0), shift(act=8.25, sched=9.0), shift(act=0.0, sched=0.0),
          shift(act=7.0, sched=0.0), shift(act=0.0, sched=6.3), shift(act=9.0, sched=9.0)]
check("F1 every shape agrees with the pre-fix expression under the shipped default",
      [B.resolve_shift_row(s, DEFAULT)[0] for s in _cases], [pre_fix(s) for s in _cases])
check("F2 …and each number now carries its basis",
      [B.resolve_shift_row(s, DEFAULT)[1] for s in _cases],
      [B.SCHEDULE_OPT_IN, B.CLOCKED_CORRECTED, B.SCHEDULE_OPT_IN, B.CLOCKED_CORRECTED,
       B.SCHEDULE_OPT_IN, B.CLOCKED_CORRECTED])
# The punch-aware half: `router._shift_actual_contribution`'s three outcomes, unchanged.
_cell_punch = {"scheduled": 9.0, "shift_actual": 0.0, "shift_actual_rows": 0,
               "_raw_punches": [{"clock_out": "x", "hours": 0.0}]}
check("F3 a day a punch measured contributes 0 FROM THE SHIFT (the punch is counted at its store)",
      B.resolve_shift_row(shift(sched=9.0), DEFAULT, None, _cell_punch)[0], 0.0)
check("F4 …and says WHY it is zero, so a reader never sees a bare 0",
      "a punch measured this day" in
      B.resolve_shift_row(shift(sched=9.0), DEFAULT, None, _cell_punch)[2]["shift_contributes_zero_because"],
      True)
check("F5 a corrected row contributes ITS OWN actual_hours, never the day's sum",
      B.resolve_shift_row(shift(act=8.25), DEFAULT, None,
                          {"scheduled": 9.0, "shift_actual": 16.5, "shift_actual_rows": 2})[0], 8.25)
check("F6 an unmeasured row under clocked-only contributes NOTHING and names the basis",
      B.resolve_shift_row(shift(sched=9.0), CLOCKED, None,
                          {"scheduled": 9.0, "shift_actual": 0.0, "shift_actual_rows": 0})[:2],
      (0.0, B.NOT_MEASURED_UNPAID))
check("F7 punch_counts_for_pay: a usable punch counts unless a correction covers its day",
      (B.punch_counts_for_pay(punch(), {"shift_actual": 0.0}, DEFAULT),
       B.punch_counts_for_pay(punch(), {"shift_actual": 8.0}, DEFAULT),
       B.punch_counts_for_pay(punch(closed=False), {"shift_actual": 0.0}, DEFAULT)),
      (True, False, False))

section("G. coverage and THE INTERLOCK")
# PARTIAL coverage is the COMMON case on this estate, not the edge case — per-day punch coverage of
# scheduled days measured 2026-10-10 runs 72-85% (Cellfonz) and 68-96% (Luxelink), and every month
# before July 2026 has NO punches at all. So the interlock must trip on a THRESHOLD, not on zero.
_days = [shift(day=f"2026-10-{d:02d}", eid=f"E{d}") for d in range(1, 11)]
_punched = [punch(day=f"2026-10-{d:02d}", eid=f"E{d}", hours=8.0) for d in range(1, 8)]
_ev = B.build_evidence(shifts=_days, punches=_punched)
_cov = B.coverage(_ev, CLOCKED)
check("G1 coverage counts scheduled employee-days as the denominator", _cov["scheduled_days"], 10)
check("G2 …and measured ones as the numerator", (_cov["measured_days"], _cov["measured_pct"]), (7, 70.0))
check("G3 the unmeasured days are LISTED, with the schedule they will not be paid",
      sorted(d["date"] for d in _cov["unmeasured_days"]),
      ["2026-10-08", "2026-10-09", "2026-10-10"])
_lock = B.interlock(_cov, CLOCKED)
check("G4 70% against an 85% threshold REFUSES the clocked-only run",
      (_lock["ok"], _lock["refused"]), (False, True))
check("G5 …and the refusal is reported as a DATA DEFECT, naming the shortfall",
      ("missing-clock-data defect" in _lock["reason"]
       and "listed rather than paid as zero" in _lock["reason"]), True)
check("G6 a period at or above the threshold runs",
      B.interlock(B.coverage(B.build_evidence(shifts=_days[:8], punches=_punched), CLOCKED),
                  CLOCKED)["ok"], True)
check("G7 ZERO coverage refuses too — the June 2026 shape (4,830 scheduled h, no punches at all)",
      B.interlock(B.coverage(B.build_evidence(shifts=_days), CLOCKED), CLOCKED)["refused"], True)
check("G8 the DEFAULT mode never refuses — there is no clocked-only basis to protect",
      B.interlock(_cov, DEFAULT)["refused"], False)
check("G9 an org may choose to WARN instead of refuse, and then the run proceeds",
      B.interlock(_cov, B.resolve_config({"hours_basis_mode": B.MODE_CLOCKED_ONLY,
                                          "pay_from_schedule_default": False,
                                          "interlock_refuses": False}))["ok"], True)
check("G10 a period with no scheduled days is not a defect",
      B.interlock(B.coverage({}, CLOCKED), CLOCKED)["refused"], False)
check("G11 a ticked employee's unmeasured day is still NOT counted as measured",
      B.coverage(B.build_evidence(shifts=[shift()]), CLOCKED,
                 employee_rows=[{"employee_id": "E1", "pay_from_schedule": True}])["measured_days"], 0)

section("H. the measurement question, asked on its own")
check("H1 a correction is a measurement whatever the pay mode says",
      B.measurement_state({"manual": 7.75, "manual_rows": 1}, DEFAULT),
      (B.CLOCKED_CORRECTED, 7.75))
check("H2 a usable punch is a measurement", B.measurement_state(
      {"_raw_punches": [{"clock_out": "x", "hours": 8.5}]}, DEFAULT), (B.CLOCKED, 8.5))
check("H3 a measured zero is a measurement, and it is zero", B.measurement_state(
      {"_raw_punches": [{"clock_out": "x", "hours": 0.0}]}, DEFAULT), (B.MEASURED_ZERO, 0.0))
check("H4 a scheduled-only day is NOT a measurement",
      B.measurement_state({"scheduled": 9.0}, DEFAULT), (B.MEASUREMENT_NONE, 0.0))
check("H5 nothing at all is NOT a measurement", B.measurement_state({}, DEFAULT),
      (B.MEASUREMENT_NONE, 0.0))

section("I. determinism and degradation")
check("I1 the same evidence resolves identically twice",
      one(shifts=[shift()], punches=[punch()], cfg=CLOCKED),
      one(shifts=[shift()], punches=[punch()], cfg=CLOCKED))
check("I2 a row with no business date is dropped (a day we cannot name cannot be compared)",
      B.build_evidence(shifts=[{"employee_id": "E1", "shift_date": None, "scheduled_hours": 9}]), {})
check("I3 a row with no employee id still groups by day (salary_owed hands pre-narrowed rows)",
      list(B.build_evidence(shifts=[{"shift_date": "2026-10-09", "scheduled_hours": 9}])),
      [(None, "2026-10-09")])
check("I4 garbage hours are 0, never an exception on a pay path",
      one(punches=[{"employee_id": "E1", "work_date": "2026-10-09", "hours": "x",
                    "clock_out": "y"}], cfg=CLOCKED)[0], 0.0)
check("I5 the same evidence index resolves under BOTH configs (the policy is not baked into it)",
      (B.resolve_day(B.build_evidence(shifts=[shift()])[("E1", "2026-10-09")], DEFAULT)[1],
       B.resolve_day(B.build_evidence(shifts=[shift()])[("E1", "2026-10-09")], CLOCKED)[1]),
      (B.SCHEDULE_OPT_IN, B.NOT_MEASURED_UNPAID))

section("R. THE REGRESSION — payroll silently computed from the schedule")
# The reported defect, in the shape production is actually in: a scheduled day, a real punch, and
# `shifts.actual_hours` left at 0 because nothing writes it.
_prod_shape = shift(sched=9.0, act=0.0)
_prod_punch = punch(hours=8.47)
check("R1 PRE-FIX: the `or` expression pays the SCHEDULE and cannot see the punch at all",
      pre_fix(_prod_shape), 9.0)
check("R2 PRE-FIX: it returns a bare number — there is no basis to inspect",
      isinstance(pre_fix(_prod_shape), float), True)
_h, _b, _d = one(shifts=[_prod_shape], punches=[_prod_punch], cfg=DEFAULT)
check("R3 POST-FIX, same default behaviour: the punch is what pays, and it SAYS it is clocked",
      (_h, _b), (8.47, B.CLOCKED))
_h2, _b2, _ = one(shifts=[_prod_shape], cfg=DEFAULT)
check("R4 POST-FIX with no punch, the default still pays the schedule — but NAMES it as the schedule",
      (_h2, _b2), (9.0, B.SCHEDULE_OPT_IN))
_h3, _b3, _ = one(shifts=[_prod_shape], cfg=CLOCKED)
check("R5 THE OWNER'S ASK: with clocked-only on, the same day pays NOTHING and says why",
      (_h3, _b3, B.basis_pays_hours(_b3)), (0.0, B.NOT_MEASURED_UNPAID, False))
check("R6 …and ticking THAT employee's box brings the schedule back, for them alone",
      one(shifts=[_prod_shape], cfg=CLOCKED, emp={"pay_from_schedule": True})[:2],
      (9.0, B.SCHEDULE_OPT_IN))
# The second half of the same class: the DM's correction never reached pay either.
check("R7 PRE-FIX: a manual_hours correction is invisible to the `or` (569 live rows, never read)",
      pre_fix(_prod_shape), 9.0)
check("R8 POST-FIX under clocked-only, the correction IS the answer",
      one(shifts=[_prod_shape], manual_rows=[manual(hours=7.75)], cfg=CLOCKED)[:2],
      (7.75, B.CLOCKED_CORRECTED))


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("O. THE OWNER RULING — the clock report as a PAY SOURCE, and the reading it actually meant")
# OWNER RULING 2026-10-10, verbatim: "if they are clocking out at earlier time from the elevate then
# it means they are spending time in store to make extra hours and they ahve no more work to do, use
# that as the time used to create payroll and mention on the report why you are doing so."
#
# THE FIXTURE IS REAL. These sixteen rows are the sixteen (store, employee) pairs of 2026-10-09 where
# the B2B clock report and this platform's own kiosk BOTH measured the same person on the same day,
# taken from the supplied export and from live `storeops.timelog`, with the kiosk stamps converted to
# store-local time. They are here, by hand, because the ruling's cost is a measured fact and a
# refactor must not be able to move it quietly.
#
# Each row:  store, the report's own spelling of the person, the REPORT's clock-in / clock-out and
# its own declared decimal (truncated, as the file writes it), then OUR kiosk's clock-in / clock-out
# and the hours payroll pays today. Seconds are load-bearing: on three of these rows the two clocks
# differ by under a minute, and that is precisely where the two readings of the ruling part company.
FIXTURE = [
    ("B-103",   "Awais, Akber",         "10:16:00", "19:38:00", 9.366666, "10:14:33", "19:41:17", 9.45),
    ("B-1115",  "Dip, Nafiz",           "10:10:00", "18:47:00", 8.616666, "10:02:33", "18:50:02", 8.79),
    ("B-11634", "Asghar, Waleed",       "10:15:00", "19:02:00", 8.783333, "10:14:15", "19:04:18", 8.83),
    ("B-117",   "Khan, Amir",           "10:28:00", "19:17:00", 8.816666, "10:29:01", "19:17:54", 8.81),
    ("B-151",   "Garapati, Rohit",      "09:34:00", "18:44:00", 9.166666, "09:45:20", "20:52:54", 11.13),
    ("B-1750",  "Mart\u00ednez, LESLIEE",  "10:13:00", "18:59:00", 8.766666, "10:13:25", "18:58:17", 8.75),
    ("B-1800",  "Sharma, Anjali",       "10:18:00", "19:00:00", 8.700000, "10:09:29", "19:00:54", 8.86),
    ("B-196",   "Mohammed, Abdullah",   "09:40:00", "18:35:00", 8.916666, "09:45:02", "18:38:45", 8.90),
    ("B-3PL",   "ali, mohammad khalid", "10:11:00", "19:08:00", 8.950000, "10:10:54", "19:06:17", 8.92),
    ("B-3565",  "Hidalgo, Suanny",      "10:33:00", "19:10:00", 8.616666, "10:26:06", "19:07:44", 8.69),
    ("B-418",   "Caba, David, A",       "10:06:00", "19:20:00", 9.233333, "10:05:14", "19:20:27", 9.25),
    ("B-4712",  "shaik, Junaid",        "10:06:00", "19:00:00", 8.900000, "10:06:15", "19:17:15", 9.18),
    ("B-509",   "Mehmood, Kashif",      "09:51:00", "19:13:00", 9.366666, "09:51:11", "19:18:21", 9.45),
    ("B-5135",  "Ospina, Miguel",       "10:14:00", "19:07:00", 8.883333, "10:11:19", "19:13:31", 9.04),
    ("B-6011",  "Melez, Edgar",         "10:09:00", "19:30:00", 9.350000, "10:08:16", "19:34:10", 9.43),
    ("B-652",   "Seelam, Sunethri",     "09:44:00", "18:54:00", 9.166666, "09:58:11", "18:54:22", 8.94),
]
TODAY_H = {r[1]: r[7] for r in FIXTURE}
DAY = "2026-10-09"


def _hhmm(t):
    return f"{DAY}T{t}"


def _span(a, b):
    ah, am, asec = (int(x) for x in a.split(":"))
    bh, bm, bsec = (int(x) for x in b.split(":"))
    return round(((bh * 3600 + bm * 60 + bsec) - (ah * 3600 + am * 60 + asec)) / 3600.0, 6)


def _rec(ci, co, hours):
    return {"clock_in": _hhmm(ci), "clock_out": _hhmm(co), "hours": hours}


def _read(rule, tol=0.0):
    """Total paid hours across the fixture under one declared rule, plus the per-person figures."""
    cfg = B.resolve_config({"clock_feed_is_pay_source": True,
                            "clock_source_rule": rule,
                            "clock_agreement_tolerance_hours": tol})
    per, total = {}, 0.0
    for _code, name, bci, bco, bh, oci, oco, oh in FIXTURE:
        h, _basis, _why = B.reconcile_clock_records(_rec(oci, oco, oh), _rec(bci, bco, bh), cfg)
        per[name] = round(h, 2)
        total += h
    return round(total, 2), per


_today = round(sum(TODAY_H.values()), 2)
_a_total, _a = _read(B.RULE_EARLIER_CLOCK_OUT)
_b_total, _b = _read(B.RULE_NARROWEST_WINDOW)
_o_total, _o = _read(B.RULE_OURS_ONLY)

# ── O1-O3  the three readings, pinned to the measured hour ──────────────────────────────────────
check("O1 today (our kiosk alone) pays 146.42h across the sixteen measured pairs", _today, 146.42)
check("O2 the ruling read LITERALLY (earlier_clock_out, whole duration) pays 143.63h",
      _a_total, 143.63)
check("O3 the house default (narrowest_window) pays 142.98h", _b_total, 142.98)
check("O4 'ours_only' is exactly today — the rule can be declared OFF without removing the feed",
      _o_total, _today)

# ── O5  WHY THE HOUSE DEFAULT IS NOT THE LITERAL WORDS ──────────────────────────────────────────
# Taking the earlier-clocking-out record's WHOLE duration also takes its earlier clock-IN, so on a
# row where the report's clock-in is EARLIER than ours the literal reading pays MORE than today —
# the exact opposite of the ruling's intent ("they have no more work to do"). Three rows do this.
_pays_more = sorted(n for n, h in _a.items() if h > TODAY_H[n] + 0.001)
check("O5 the literal reading pays MORE than today on exactly these three rows",
      _pays_more, ["Khan, Amir", "Mohammed, Abdullah", "Seelam, Sunethri"])
check("O6 …and the narrowest window never pays more than today on ANY row",
      sorted(n for n, h in _b.items() if h > TODAY_H[n] + 0.001), [])

# The three divergent rows, to the hundredth, so neither reading can drift unnoticed.
check("O7 Khan, Amir      — today 8.81, literal 8.82 (MORE), house 8.80",
      (TODAY_H["Khan, Amir"], _a["Khan, Amir"], _b["Khan, Amir"]),
      (8.81, 8.82, 8.80))
check("O8 Mohammed, Abdullah — today 8.90, literal 8.92 (MORE), house 8.83",
      (_a["Mohammed, Abdullah"], _b["Mohammed, Abdullah"]), (8.92, 8.83))
check("O9 Seelam, Sunethri — today 8.94, literal 9.17 (MORE by a quarter hour), house 8.93",
      (_a["Seelam, Sunethri"], _b["Seelam, Sunethri"]), (9.17, 8.93))

# ── O10  the biggest row: the one the ruling is actually about ───────────────────────────────────
# Garapati clocked out of the kiosk at 20:52 and the clock report has him out at 18:44 — two hours of
# "spending time in store to make extra hours". Both readings cut it; the house default cuts it to
# the window the two records agree on.
check("O10 Garapati: today 11.13h, both readings cut it to about nine",
      (TODAY_H["Garapati, Rohit"], _a["Garapati, Rohit"], _b["Garapati, Rohit"]),
      (11.13, 9.17, 8.98))

# ── O11-O14  the reason travels WITH the number, never alone ─────────────────────────────────────
_h, _basis, _why = B.reconcile_clock_records(
    _rec("09:45:20", "20:52:54", 11.13), _rec("09:34:00", "18:44:00", 9.166666),
    B.resolve_config({"clock_feed_is_pay_source": True}))
check("O11 a reconciled day carries the clocked_reconciled basis", _basis, B.CLOCKED_RECONCILED)
check("O12 …and PAYS (it is a measurement, not a fallback)",
      (B.basis_pays_hours(_basis), B.basis_is_measurement(_basis)), (True, True))
check("O13 …and the reason is a SENTENCE, returned with the hours, not a label a screen adds",
      bool(_why) and _why.rstrip().endswith(".") and len(_why) > 80, True)
check("O14 …which names BOTH readings, so the DM can see what each clock alone said",
      ("11.13" in _why and "9.17" in _why), True)

# ── O15-O17  the tolerance — never silently round against the employee ──────────────────────────
_cfg_tol = B.resolve_config({"clock_feed_is_pay_source": True})
check("O15 the house tolerance is two minutes, as a CONFIG value", _cfg_tol["clock_agreement_tolerance_hours"], 0.034)
_h2, _b2, _w2 = B.reconcile_clock_records(_rec("10:00:00", "19:00:00", 9.0),
                                          _rec("10:00:00", "18:59:00", 8.983333), _cfg_tol)
check("O16 inside the tolerance the two records AGREE and OURS stands, unreconciled",
      (_h2, _b2), (9.0, None))
_h3, _b3, _w3 = B.reconcile_clock_records(_rec("10:00:00", "19:00:00", 9.0),
                                          _rec("10:00:00", "18:40:00", 8.666666), _cfg_tol)
check("O17 beyond it the rule fires and the day is reconciled", (_h3, _b3), (8.67, B.CLOCKED_RECONCILED))

# ── O18-O20  the OFF switch, and the override that out-ranks the rule ───────────────────────────
_off = B.resolve_config(None)
check("O18 the feed is NOT a pay source until the org says so", _off["clock_feed_is_pay_source"], False)
check("O19 the house default rule is the narrower reading", _off["clock_source_rule"], B.RULE_NARROWEST_WINDOW)
_ev = B.build_evidence(shifts=[shift(day=DAY, sched=9.0)],
                       punches=[punch(day=DAY, hours=11.13)],
                       feed_punches=[{"employee_id": "E1", "business_date": DAY,
                                      "clock_in": _hhmm("09:34:00"), "clock_out": _hhmm("18:44:00"),
                                      "hours": 9.166666}],
                       overrides=[{"employee_id": "E1", "work_date": DAY, "hours": 10.5,
                                   "reason": "stayed for the cash count", "by": "dm@example.com"}])
_oh, _ob, _od = B.resolve_day(_ev[("E1", DAY)],
                              B.resolve_config({"clock_feed_is_pay_source": True}))
check("O20 a DM's per-row override OUT-RANKS the declared rule", (_oh, _ob), (10.5, B.CLOCKED_OVERRIDE))
check("O21 …and the override's own reason is carried on the day, not lost",
      _od.get("override_reason"), "stayed for the cash count")
check("O22 …and the override is still a measurement that pays",
      (B.basis_pays_hours(_ob), B.basis_is_measurement(_ob)), (True, True))

# ── O23  an unruled rule name cannot quietly become some default at pay time ────────────────────
# RULE TWO's other half: a typo in the config row must not silently pick a reading. The SQL CHECK
# constraint refuses the write; this proves the reader does not invent an answer either.
check("O23 every declared rule has an explaining sentence of its own",
      sorted(B.CLOCK_SOURCE_RULES) == sorted(B.CLOCK_RULE_REASONS), True)

# ════════════════════════════════════════════════════════════════════════════════════════════════
section("P. THE 2026-10-12 REGRESSION — a pay path reaching the one home with NO EVIDENCE")
# ────────────────────────────────────────────────────────────────────────────────────────────────
# THE DEFECT, MEASURED, NOT HYPOTHESISED. `resolve_shift_row(shift, cfg, emp)` with `cell=None` can
# answer only from the shift row — i.e. from `shifts.actual_hours`, the dead column (7 of 7,740 live
# rows). FOUR pay paths called it exactly that way: `account/coa` (the P&L wage line),
# `commcalc/labour_coverage`, `storeops/pto_accrual` and `hr/router`. Under `clocked_only` with
# `pay_from_schedule_default = false` — which the house org is LIVE on — every such row therefore
# resolved `not_measured_unpaid` = ZERO, while the punches that measured those days sat in
# `storeops.timelog`. Replayed over live house shifts on 2026-10-12 the P&L wage line booked
# $374,733.02 -> $0.00 across Jun–Oct 2026. P1–P4 reproduce that; P5–P8 show the wiring fixing it.
# ────────────────────────────────────────────────────────────────────────────────────────────────
_PDAY = "2026-10-09"                     # ON OR AFTER the floor, so the clocked basis applies
_pshift = shift(day=_PDAY, sched=9.0, act=0.0)
_ppunch = punch(day=_PDAY, hours=8.5)

# P1-P2  THE DEFECT ITSELF: no evidence, so the punch cannot be seen and the day pays NOTHING.
check("P1 THE DEFECT — no `cell`: a clocked-only day resolves to ZERO even though a punch exists",
      B.resolve_shift_row(_pshift, CLOCKED)[:2], (0.0, B.NOT_MEASURED_UNPAID))
_pev = B.build_evidence(shifts=[_pshift], punches=[_ppunch])
check("P2 …and the punch WAS there all along: the evidence index holds its 8.5h",
      B.resolve_day(_pev[("E1", _PDAY)], CLOCKED)[:2], (8.5, B.CLOCKED))

# P3-P4  AT PERIOD GRAIN, which is where the money is. One rep, 20 scheduled days, every one of them
#        clocked — the shape of a REAL month, not an edge case.
_pdays = [f"2026-10-{d:02d}" for d in range(6, 26)]
_pshifts = [shift(day=d, sched=9.0) for d in _pdays]
_ppunches = [punch(day=d, hours=8.5) for d in _pdays]
_RATE = 17.0
check("P3 THE DEFECT at period grain — the wage line prices 20 fully-clocked days at $0.00",
      round(sum(B.resolve_shift_row(s, CLOCKED)[0] for s in _pshifts) * _RATE, 2), 0.0)
_pev_m = B.build_evidence(shifts=_pshifts, punches=_ppunches)
# ★ AND A BARE CELL IS NOT THE FIX ★ — the trap this harness caught while the fix was being written.
# `resolve_shift_row(..., cell)` returns 0.0 for a day a punch measured, because the Payroll screen
# counts those hours off the PUNCH row; a shift-row loop never sees a punch row, so the $0.00 simply
# changes basis. The money is only right when the DAY is resolved once and allocated back, which is
# `payroll_evidence.paid_hours_rows`.
check("P4 a bare cell swaps one ZERO for another — `clocked` 0.0 instead of `not_measured` 0.0",
      [round(sum(B.resolve_shift_row(s, CLOCKED, None,
                                     _pev_m.get((s["employee_id"], s["shift_date"])))[0]
                 for s in _pshifts) * _RATE, 2),
       B.resolve_shift_row(_pshifts[0], CLOCKED, None, _pev_m[("E1", _pdays[0])])[1]],
      [0.0, B.CLOCKED])
_prows = PE.paid_hours_rows({"evidence": _pev_m, "cfg": CLOCKED}, _pshifts, cfg=CLOCKED)
check("P4b …and the day-grain allocation prices the same month at the $2,890.00 the punches measured",
      round(sum(r["hours"] for r in _prows) * _RATE, 2), 2890.0)
check("P4c …one row per shift row, in order, each carrying its own day's basis",
      (len(_prows), _prows[0]["basis"], _prows[0]["date"]), (20, B.CLOCKED, _pdays[0]))
# AND THE DEFAULT MODE IS UNTOUCHED WHERE NOTHING MEASURED: the allocation changes which hours a day
# gets, never whose loop they land in.
_pq_sched = [shift(day=d, sched=9.0) for d in _pdays]
_pq_rows = PE.paid_hours_rows({"evidence": B.build_evidence(shifts=_pq_sched), "cfg": DEFAULT},
                              _pq_sched, cfg=DEFAULT)
check("P4d an UNMEASURED month under the house default is byte-identical to today's 180.0h",
      (round(sum(r["hours"] for r in _pq_rows), 2),
       round(sum(B.resolve_shift_row(s, DEFAULT)[0] for s in _pq_sched), 2)), (180.0, 180.0))
# A TWO-STORE DAY: the day is resolved ONCE and split in the proportions those rows always had.
_two = [shift(day=_PDAY, sched=4.0), shift(day=_PDAY, sched=5.0)]
for _r in _two:
    _r["store_code"] = "A" if _r is _two[0] else "Bb"
_two_rows = PE.paid_hours_rows({"evidence": B.build_evidence(shifts=_two), "cfg": DEFAULT},
                               _two, cfg=DEFAULT)
check("P4e a two-store day splits in its own proportions and foots exactly",
      ([r["hours"] for r in _two_rows], round(sum(r["hours"] for r in _two_rows), 2)),
      ([4.0, 5.0], 9.0))
_two_ev = B.build_evidence(shifts=_two, punches=[dict(punch(day=_PDAY, hours=8.0), store_code="Bb")])
_two_m = PE.paid_hours_rows({"evidence": _two_ev, "cfg": CLOCKED}, _two, cfg=CLOCKED)
check("P4f …and a measured two-store day foots to the PUNCH's 8.0h, placed at the punch's store",
      (round(sum(r["hours"] for r in _two_m), 2), _two_m[0]["store_code"]), (8.0, "Bb"))
# A PUNCH ON A DAY NO SHIFT COVERS IS NOT FOLDED IN — it is NAMED. Widening four reports' day
# universe is a separate question, and this fix reports it instead of answering it quietly.
check("P4g a punch on an unscheduled day is reported, never silently added to a shift-row report",
      [r["date"] for r in PE.paid_hours_rows({"evidence": B.build_evidence(
          shifts=[shift(day=_PDAY, sched=9.0)], punches=[punch(day="2026-10-10", hours=7.0)])},
          [shift(day=_PDAY, sched=9.0)], cfg=CLOCKED)], [_PDAY])
# A CALLER THAT COULD NOT READ degrades to the evidence-free answer rather than to zero.
check("P4h no evidence at all degrades to the pre-2026-10-12 arithmetic, not to $0.00",
      round(sum(r["hours"] for r in PE.paid_hours_rows(None, _pq_sched, cfg=DEFAULT)), 2), 180.0)

# P5-P6  THE INTERLOCK IS THE SECOND HALF. None of the four consulted it, so nothing stopped the
#        zero from being published. With no punches at all the period is REFUSED, not priced.
_pcov_none = B.coverage(B.build_evidence(shifts=_pshifts), CLOCKED)
_plock_none = B.interlock(_pcov_none, CLOCKED)
check("P5 a clocked-only period with NO punches is REFUSED, never paid as a shortfall",
      (_plock_none["refused"], _plock_none["measured_pct"]), (True, 0.0))
check("P6 …and the refusal carries the sentence a caller must report",
      bool(_plock_none["reason"]) and "below the" in _plock_none["reason"], True)
_plock_all = B.interlock(B.coverage(_pev_m, CLOCKED), CLOCKED)
check("P7 a fully-clocked period is NOT refused", (_plock_all["refused"], _plock_all["measured_pct"]),
      (False, 100.0))

# P8  THE DAY TRAVELS IN THE CELL, so a caller that built its evidence through `build_evidence`
#     cannot forget to pass it — the API property the floor below depends on.
check("P8 build_evidence writes the key's own date into the cell",
      _pev_m[("E1", "2026-10-07")]["day"], "2026-10-07")


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("Q. THE EFFECTIVE-FROM FLOOR (owner ruling 2026-10-12, mig 1074)")
# ────────────────────────────────────────────────────────────────────────────────────────────────
# OWNER, VERBATIM: *"Payroll from clack in should be from Oct 4 th onwards which would be the new
# cycle"*. So the clocked-only basis applies FROM 2026-10-04, and every day BEFORE it keeps today's
# schedule-based answer — which is what makes a recompute of Jun/Jul/Aug/Sep restate nothing.
# ────────────────────────────────────────────────────────────────────────────────────────────────
FLOORED = B.resolve_config({"hours_basis_mode": B.MODE_CLOCKED_ONLY,
                            "pay_from_schedule_default": False,
                            "clocked_basis_effective_from": "2026-10-04"})

check("Q1 the floor ships NULL, so no existing org has one", DEFAULT["clocked_basis_effective_from"], None)
check("Q2 …and with no floor NO day is before it — the mode applies to every day, as today",
      [B.day_is_before_floor(d, CLOCKED) for d in ("2020-01-01", "2026-06-15", "2099-12-31")],
      [False, False, False])
check("Q3 the boundary is STRICTLY BEFORE: the floor day itself is on the new basis",
      [B.day_is_before_floor(d, FLOORED)
       for d in ("2026-10-03", "2026-10-04", "2026-10-05")], [True, False, False])
check("Q4 an UNDATED day keeps today's answer rather than being assumed onto one side",
      B.day_is_before_floor(None, FLOORED), False)

# Q5-Q8  THE GUARANTEE: a pre-floor day is BYTE-IDENTICAL to this org's schedule-fallback answer.
_PRE, _POST = "2026-09-15", "2026-10-15"
for _lbl, _d, _want in (("Q5 Jun", "2026-06-15", 9.0), ("Q6 Sep", _PRE, 9.0), ("Q7 Oct 3", "2026-10-03", 9.0)):
    check(f"{_lbl} — a pre-floor unmeasured day still pays its SCHEDULE",
          B.resolve_shift_row(shift(day=_d, sched=9.0), FLOORED)[:2], (9.0, B.SCHEDULE_OPT_IN))
check("Q8 …while the same shape ON the new cycle is unpaid and SAYS so",
      B.resolve_shift_row(shift(day=_POST, sched=9.0), FLOORED)[:2], (0.0, B.NOT_MEASURED_UNPAID))

# Q9  BYTE-IDENTICAL, asserted on the whole detail dict and not just the hours — including the
#     `count_auto_clockout_punches` forcing `resolve_config` applies ONLY in schedule-fallback mode.
_FLOOR_ONLY_KEYS = ("effective_from", "pre_effective_from", "floor_reason", "org_mode")
_d_pre = B.resolve_shift_row(shift(day=_PRE, sched=9.0), FLOORED)[2]
_d_fb = B.resolve_shift_row(shift(day=_PRE, sched=9.0), DEFAULT)[2]
check("Q9 a pre-floor day's whole DETAIL equals the house schedule-fallback answer",
      {k: v for k, v in _d_pre.items() if k not in _FLOOR_ONLY_KEYS},
      {k: v for k, v in _d_fb.items() if k not in _FLOOR_ONLY_KEYS})
check("Q10 …including the auto-clockout forcing, which only schedule-fallback mode applies",
      (FLOORED["count_auto_clockout_punches"],
       FLOORED[B.PRE_FLOOR_KEY]["count_auto_clockout_punches"]), (False, True))
check("Q11 …and the manual-hours forcing, the other mode-dependent one",
      (FLOORED["count_manual_hours"], FLOORED[B.PRE_FLOOR_KEY]["count_manual_hours"]), (True, False))

# Q12-Q13  THE WHOLE SWITCH IS RESTORED, not a third of it. This is the trap the first cut fell into:
#          forcing only `hours_basis_mode` back leaves `pay_from_schedule_default = false` — the live
#          house value — and schedule-fallback + no opt-in + nothing measured is STILL $0.00. So
#          Jun–Sep stayed at zero through the one door the floor had left open.
check("Q12 the pre-floor config restores pay_from_schedule_default to the house TRUE",
      (FLOORED["pay_from_schedule_default"],
       FLOORED[B.PRE_FLOOR_KEY]["pay_from_schedule_default"]), (False, True))
check("Q13 …and the per-employee tick box does not bite on a pre-floor day either",
      [B.resolve_shift_row(shift(day=d, sched=9.0), FLOORED,
                           {"employee_id": "E1", "pay_from_schedule": False})[:2]
       for d in (_PRE, _POST)],
      [(9.0, B.SCHEDULE_OPT_IN), (0.0, B.NOT_MEASURED_UNPAID)])

# Q14  A MEASURED pre-floor day is untouched: the floor restores the old BASIS, it does not discard
#      evidence. A punch that was being paid before the floor existed keeps being paid.
_q_pre_ev = B.build_evidence(shifts=[shift(day=_PRE, sched=9.0)], punches=[punch(day=_PRE, hours=8.5)])
check("Q14 a pre-floor day WITH a punch still pays the punch — the floor discards no evidence",
      B.resolve_day(_q_pre_ev[("E1", _PRE)], FLOORED)[:2], (8.5, B.CLOCKED))

# Q15-Q18  COVERAGE AND THE INTERLOCK COUNT ONLY DAYS ON OR AFTER THE FLOOR. Without this, June
#          2026 (no punches at all, by construction) would hold the measured share below the
#          threshold forever and refuse a period the floor had already excused.
_q_span = ([shift(day=f"2026-09-{d:02d}", sched=9.0) for d in range(1, 29)]
           + [shift(day=f"2026-10-{d:02d}", sched=9.0) for d in range(6, 26)])
_q_punches = [punch(day=f"2026-10-{d:02d}", hours=8.5) for d in range(6, 26)]
_q_cov = B.coverage(B.build_evidence(shifts=_q_span, punches=_q_punches), FLOORED)
check("Q15 the 28 pre-floor employee-days are EXCLUDED from the coverage denominator",
      (_q_cov["scheduled_days"], _q_cov["measured_days"]), (20, 20))
check("Q16 …and COUNTED as excluded, so they are never merely missing",
      _q_cov["pre_effective_from_days"], 28)
check("Q17 …so the interlock does not refuse a period the floor already excused",
      B.interlock(_q_cov, FLOORED)["refused"], False)
_q_cov_unfloored = B.coverage(B.build_evidence(shifts=_q_span, punches=_q_punches), CLOCKED)
check("Q18 …and WITHOUT the floor that same period IS refused — the exclusion is doing the work",
      (B.interlock(_q_cov_unfloored, CLOCKED)["refused"], _q_cov_unfloored["scheduled_days"]),
      (True, 48))
_q_all_pre = B.coverage(B.build_evidence(shifts=[shift(day="2026-06-15", sched=9.0)]), FLOORED)
_q_lock_pre = B.interlock(_q_all_pre, FLOORED)
check("Q19 a period ENTIRELY before the floor is not refused, and the reason names the date",
      (_q_lock_pre["refused"], "2026-10-04" in (_q_lock_pre["reason"] or "")), (False, True))

# Q20-Q22  THE DETAIL TELLS THE READER, so a figure is never shown on a basis the org's mode does
#          not name. And `punch_counts_for_pay` routes through the same floor.
check("Q20 a pre-floor day's detail names the floor and says which side it fell on",
      (_d_pre["effective_from"], _d_pre["pre_effective_from"], _d_pre["mode"], _d_pre["org_mode"]),
      ("2026-10-04", True, B.MODE_SCHEDULE_FALLBACK, B.MODE_CLOCKED_ONLY))
check("Q21 …and carries a sentence saying a recompute restates nothing",
      "restates nothing" in (_d_pre.get("floor_reason") or ""), True)
_q_auto = punch(day=_PRE, hours=9.0, auto=True)
check("Q22 an AUTO-CLOSED punch on a pre-floor day still counts for pay, as it does today",
      (B.punch_counts_for_pay(_q_auto, {}, FLOORED),
       B.punch_counts_for_pay(punch(day=_POST, hours=9.0, auto=True), {}, FLOORED)), (True, False))

# Q23  RULE TWO. No date is spelled in the module; the floor is a config value and nothing else.
check("Q23 the floor is CONFIG — it is one of the declared config fields, with a None default",
      ("clocked_basis_effective_from" in B.DEFAULT_CONFIG,
       B.DEFAULT_CONFIG["clocked_basis_effective_from"]), (True, None))
check("Q24 a malformed date reads as NO FLOOR rather than as some guessed day",
      [B.resolve_config({"clocked_basis_effective_from": v})["clocked_basis_effective_from"]
       for v in ("", "not-a-date", "2026-10", None)], [None, None, None, None])


section("Z. ARMED negative control")
_f0 = len(FAIL)
check("Z-armed unruled basis", B.basis_pays_hours("invented"), True)
check("Z-armed clocked-only pays the schedule", one(shifts=[shift()], cfg=CLOCKED)[0], 9.0)
check("Z-armed interlock passes at 70%", B.interlock(_cov, CLOCKED)["ok"], True)
check("Z-armed a pre-floor day is unpaid", B.resolve_shift_row(shift(day="2026-09-15"), FLOORED)[0], 0.0)
check("Z-armed the floor excludes nothing from coverage", _q_cov["scheduled_days"], 48)
_fired = len(FAIL) - _f0
if _fired == 5:
    FAIL[:] = FAIL[:_f0]
    PASS.append("Z1 negative control fired on all five wrong expectations (the checks are live)")
else:
    FAIL.append(f"Z1 NEGATIVE CONTROL DID NOT FIRE — {_fired}/5 wrong answers accepted.")

print(f"\n{'=' * 78}")
for f in FAIL:
    print(f"  ✗ {f}")
print(f"  PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
