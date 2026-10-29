#!/usr/bin/env python3
"""A SWEEP SCHEDULES ITSELF FROM STATE — the lock for index §COMMCALC-45. DB-free, stdlib only.

Owner 2026-10-29: *"I should not be running the sweep it should be automatic like the rebuild or the
commission calculation - whenever anything changes it should automatically do the sweep as required."*

Two things are proved here, and the second is the one that matters:

  §A–§E  BEHAVIOUR — `sweep_due.verdict` on the REAL house DLAR config row as it stood on
         2026-10-29, on the stale-attempt trap that would have made the floor on contact vacuous,
         and on every reason in both directions.
  §F     THE UN-WIRE LOCK — the defect was never in a home, it was in EIGHT CALLERS each deciding
         due-ness from a stored clock. So the router is read as SOURCE: no `run-due` may filter on
         the clock in SQL or restate the rule inline, and the shipped rule is kept as an ARMED
         NEGATIVE CONTROL. Without this the one home could sit there, correct and unused, which has
         happened three times in this repo (§19.18).
"""
import datetime as _dt
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harnesslib as _hl   # the ONE comment/docstring stripper (§24) — a lock reads CODE, not prose
from app.modules.commcalc import sweep_due as sd      # noqa: E402

P = F = 0


def check(label, ok, got=None):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s%s" % (label, "" if got is None else "   got=%r" % (got,)))


NOW = _dt.datetime(2026, 10, 29, 1, 0, tzinfo=_dt.timezone.utc)

#: THE REAL ROW, read from production 2026-10-29 01:5x UTC (org 00000000-…-0001, dlar_sweep_config).
#: `last_attempt_at` really is NULL while `last_run_at` is fresh, because `_sweep_set_status` writes
#: exactly one of the two and a 'partial' run counts as an import.
LIVE = {"enabled": True, "frequency": "daily", "hour": 7, "timezone": "America/New_York",
        "next_run_at": "2026-10-29T11:00:00+00:00",
        "last_run_at": "2026-10-28T11:00:21.435029+00:00",
        "last_attempt_at": None,
        "last_status": "partial",
        "updated_at": "2026-06-17T02:52:08.813297+00:00"}

print("A. the measured defect: thirteen identical PARTIAL runs, each waiting a full day")
v = sd.verdict(LIVE, NOW)
check("A1 the real 2026-10-29 row is DUE", v["due"] is True, v)
check("A2 …and the reason is the incomplete last run, not the clock",
      v["reason"] == sd.REASON_RETRY_INCOMPLETE, v["reason"])
check("A3 the old rule would have said NOT due (the shipped behaviour, as a control)",
      not (not LIVE["next_run_at"] or LIVE["next_run_at"] <= NOW.isoformat()))
check("A4 the reason is said in words a person can read, naming the status",
      "partial" in v["detail"] and "retry gap" in v["detail"], v["detail"])
check("A5 a clean run on the same row is NOT due — only the clock moves it",
      sd.verdict(dict(LIVE, last_status="ok"), NOW)["reason"] == sd.REASON_WAITING)

print("\nB. the floor on contact, and the trap that would have made it vacuous")
# `last_run_at` fresh + `last_attempt_at` two months old is the REAL shape of a connector that failed
# in August and has run partially every day since. A first draft read the attempt column and fell
# back to the run column, so it took the August date as "when we last knocked": the floor existed and
# protected nothing. The latest contact is the LATER of the two.
TRAP = dict(LIVE, last_attempt_at="2026-08-01T00:00:00+00:00",
            last_run_at="2026-10-29T00:55:00+00:00")
check("B1 attempted 5 min ago → NOT due, whatever else changed", sd.verdict(TRAP, NOW)["reason"] == sd.REASON_TOO_SOON,
      sd.verdict(TRAP, NOW))
check("B2 the pre-fix reading (attempt column, else run column) would have called it due",
      sd.REASON_TOO_SOON != sd.verdict(
          {k: val for k, val in TRAP.items() if k != "last_run_at"} | {"last_run_at": None,
                                                                      "next_run_at": "2026-10-29T11:00:00+00:00"},
          NOW)["reason"])
check("B3 the floor beats a settings change too — nothing can force contact inside it",
      sd.verdict(dict(TRAP, updated_at="2026-10-29T00:59:00+00:00"), NOW)["reason"] == sd.REASON_TOO_SOON)
check("B4 the floor beats the CLOCK as well: two cron ticks inside the gap are one run",
      sd.verdict(dict(LIVE, last_run_at="2026-10-29T00:55:00+00:00", last_status="ok",
                      next_run_at="2026-10-29T00:00:00+00:00"), NOW)["reason"] == sd.REASON_TOO_SOON)
check("B5 and past the floor, the clock is honoured exactly as before",
      sd.verdict(dict(LIVE, last_run_at="2026-10-29T00:00:00+00:00", last_status="ok",
                      next_run_at="2026-10-29T00:30:00+00:00"), NOW)["reason"] == sd.REASON_CLOCK)

print("\nC. 'whenever anything changes' — the declaration is the change that matters")
check("C1 settings changed after the last attempt → due now",
      sd.verdict(dict(LIVE, last_status="ok", updated_at="2026-10-29T00:30:00+00:00"),
                 NOW)["reason"] == sd.REASON_CONFIG_CHANGED)
check("C2 settings changed BEFORE the last attempt → that run already used them",
      sd.verdict(dict(LIVE, last_status="ok", updated_at="2026-10-27T00:00:00+00:00"),
                 NOW)["reason"] == sd.REASON_WAITING)
check("C3 a row with no `updated_at` reports no change, never a false one",
      sd.verdict(dict(LIVE, last_status="ok", updated_at=None), NOW)["reason"] == sd.REASON_WAITING)
check("C4 never attempted → due at once, ahead of the floor (no previous contact to protect)",
      sd.verdict({"enabled": True, "frequency": "daily"}, NOW)["reason"] == sd.REASON_NEVER_RUN)
check("C5 switched off is switched off, whatever the clock or the status says",
      sd.verdict(dict(LIVE, enabled=False), NOW)["reason"] == sd.REASON_DISABLED
      and sd.verdict(dict(LIVE, enabled=False), NOW)["due"] is False)

print("\nD. an unruled status reads as INCOMPLETE, and the retry rides the tenant's own cadence")
for word in ("partial", "error", "running", "", None, "weird-new-word", "FAILED"):
    check("D·%-14r is incomplete" % (word,), not sd.status_is_complete(word))
for word in ("ok", "OK", " success ", "idle"):
    check("D·%-14r is complete" % (word,), sd.status_is_complete(word))
check("D1 hourly retries at the floor, not faster",
      sd.retry_gap_s({"frequency": "hourly"}) == sd.MIN_ATTEMPT_GAP_S,
      sd.retry_gap_s({"frequency": "hourly"}))
check("D2 daily retries four times a day (quarter cadence)",
      sd.retry_gap_s({"frequency": "daily"}) == 6 * 3600, sd.retry_gap_s({"frequency": "daily"}))
check("D3 monthly is still re-tried the same day, not next month",
      sd.retry_gap_s({"frequency": "monthly"}) == sd.RETRY_GAP_MAX_S)
check("D4 an unknown cadence falls to weekly — never to 'very often'",
      sd.cadence_s({"frequency": "fortnightly"}) == sd.CADENCE_DEFAULT_S
      and sd.cadence_s({}) == sd.CADENCE_DEFAULT_S)
check("D5 no cadence can produce a gap under the floor or over the cap",
      all(sd.MIN_ATTEMPT_GAP_S <= sd.retry_gap_s({"frequency": f}) <= sd.RETRY_GAP_MAX_S
          for f in list(sd.CADENCE_S) + ["", None, "nonsense"]))
check("D6 an incomplete run inside its retry gap waits for the gap, not for tomorrow",
      sd.verdict(dict(LIVE, last_run_at="2026-10-28T22:00:00+00:00"), NOW)["reason"] == sd.REASON_WAITING)

print("\nE. the verdict never raises, and every reason is declared")
for bad in (None, {}, "nonsense", {"enabled": True, "last_run_at": "not-a-date",
                                   "next_run_at": 7, "updated_at": {}, "frequency": 3}):
    try:
        got = sd.verdict(bad, NOW)
        check("E·%-26s → %s" % (str(bad)[:26], got["reason"]),
              got["reason"] in sd.DUE_REASONS + sd.NOT_DUE_REASONS
              and got["due"] is (got["reason"] in sd.DUE_REASONS))
    except Exception as e:
        check("E·%-26s does not raise" % str(bad)[:26], False, e)
check("E1 `is_due` agrees with `verdict` on every fixture",
      all(sd.is_due(r, NOW) == sd.verdict(r, NOW)["due"]
          for r in (LIVE, TRAP, {}, dict(LIVE, enabled=False))))
check("E2 the two reason lists do not overlap and cover every constant",
      not (set(sd.DUE_REASONS) & set(sd.NOT_DUE_REASONS))
      and len(set(sd.DUE_REASONS) | set(sd.NOT_DUE_REASONS)) == 7)
check("E3 `READS` names every column the verdict actually consults",
      set(sd.READS) == {"enabled", "next_run_at", "last_run_at", "last_attempt_at", "last_status",
                        "updated_at", "frequency"}, sd.READS)

print("\nF. THE UN-WIRE LOCK — the defect was in the callers, so the callers are read as source")
ROUTER = pathlib.Path(__file__).resolve().parent / "app" / "modules" / "commcalc" / "router.py"
SRC = ROUTER.read_text(encoding="utf-8") if ROUTER.exists() else ""
# A LOCK READS CODE, NOT PROSE (§24). The first draft of §F counted TWO surviving clock
# filters and one of them was this very fix's own docstring explaining the filter it removed —
# a comment naming the thing the fix is about, read as the fix's own violation.
CODE = _hl.py_code_only(SRC)
check("F1 the router dereferences the one home", "import sweep_due as _sweep_due" in CODE)
check("F2 no sweep `run-due` restates the rule inline (the shipped defect, armed)",
      "connector_due = not nra or nra <= now_iso" not in CODE)
# The SQL clock filter is the stronger half of the defect: a `.lte()` CANNOT express any reason but
# the clock, so leaving one is leaving a sweep that can never retry itself. One survives on purpose —
# the email sweep, excused in §COMMCALC-45.3 with the incident that hardened it — so the count is
# PINNED rather than required to be zero, and may only shrink.
EXCUSED_CLOCK_FILTERS = 1
check("F3 exactly %d SQL clock filter(s) remain, each excused in writing" % EXCUSED_CLOCK_FILTERS,
      CODE.count("lte('next_run_at'") == EXCUSED_CLOCK_FILTERS,
      CODE.count("lte('next_run_at'"))
check("F4 the one surviving filter is the excused email sweep, not a sweep that lost its retry",
      "email_sweep_config" in CODE.split("lte('next_run_at'")[0][-900:])
check("F5 the enabled rows are filtered through the shared helper, not re-derived per endpoint",
      CODE.count("_sweep_due_rows(") >= 5, CODE.count("_sweep_due_rows("))
check("F6 only a CLOCK-due run advances the scheduled slot — a retry leaves it owed",
      CODE.count("_sweep_due_by_clock(cfg)") >= 4
      and "_dv.get('reason') == _sweep_due.REASON_CLOCK" in CODE,
      CODE.count("_sweep_due_by_clock(cfg)"))
check("F7 the dispatcher selects every column the verdict reads, not just the schedule ones",
      "_sweep_due.READS" in CODE
      and "select('enabled,next_run_at,frequency,day_of_week,day_of_month,hour,timezone')" not in CODE)
check("F8 the run history carries the WHOLE finding beside the fitted sentence",
      "'evidence': evidence" in CODE and "evidence={'fields': res.get('fields')" in CODE)

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — a sweep is due from its own state, nothing can knock on a portal inside the floor, and "
      "no caller decides due-ness for itself.")
