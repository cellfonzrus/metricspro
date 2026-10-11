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
  O. THE OWNER RULING: the clock report as a PAY SOURCE, pinned to the measured day of 2026-10-09.
  Z. ARMED negative control.

Run: cd backend && python3 harness_payroll_hours_basis.py   (DB-free, stdlib only)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.storeops import payroll_hours_basis as B  # noqa: E402

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

section("Z. ARMED negative control")
_f0 = len(FAIL)
check("Z-armed unruled basis", B.basis_pays_hours("invented"), True)
check("Z-armed clocked-only pays the schedule", one(shifts=[shift()], cfg=CLOCKED)[0], 9.0)
check("Z-armed interlock passes at 70%", B.interlock(_cov, CLOCKED)["ok"], True)
_fired = len(FAIL) - _f0
if _fired == 3:
    FAIL[:] = FAIL[:_f0]
    PASS.append("Z1 negative control fired on all three wrong expectations (the checks are live)")
else:
    FAIL.append(f"Z1 NEGATIVE CONTROL DID NOT FIRE — {_fired}/3 wrong answers accepted.")

print(f"\n{'=' * 78}")
for f in FAIL:
    print(f"  ✗ {f}")
print(f"  PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
