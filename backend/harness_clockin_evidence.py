"""HARNESS — clockin_evidence.py (the failed-clock-in self check).

This module is asked to help decide whether someone is telling the truth about their pay. The way it
fails is not a crash — it is producing a confident verdict the evidence does not support. So the tests
are built around the ways it could accuse someone unfairly, or clear them unfairly.

  A. The classification, day by day.
  B. Kiosk health is the real discriminator — peers clocking in vs nobody clocking in.
  C. Absence of evidence is never scored as proof (the central rule).
  D. Janet Garibay's REAL rows, 2026-07-23 → 08-05 (measured 2026-08-11).
  E. Placeholder shift rows are not schedules.
  F. ARMED negative control.

Run: python3 harness_clockin_evidence.py     (pure — no DB)
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.storeops import clockin_evidence as ce   # noqa: E402

PASS, FAIL = [], []


def check(label, got, want):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def section(t):
    print(f"\n── {t}")


def one_day(**kw):
    """analyze() over a single day, returning that day's report."""
    d = kw.pop("d", date(2026, 7, 24))
    r = ce.analyze(kw.pop("name", "Test Person"), d, d,
                   kw.pop("punches", []), kw.pop("failures", []), kw.pop("shifts", []),
                   peer_punches=kw.pop("peer_punches", None), activity=kw.pop("activity", None),
                   strikes=kw.pop("strikes", None))
    return r["days"][0]


SHIFT = {"shift_date": "2026-07-24", "store_code": "CERMARK", "scheduled_hours": "7.75",
         "start_time": "11:30", "end_time": "19:15"}
PLACEHOLDER = {"shift_date": "2026-07-24", "store_code": "CERMARK", "scheduled_hours": "0",
               "start_time": None, "end_time": None}
PUNCH = {"work_date": "2026-07-24", "store_code": "CERMARK", "device": "kiosk",
         "face_match_pct": 100, "hours": "7.67"}
OVERRIDE_PUNCH = {"work_date": "2026-07-24", "store_code": "CERMARK", "device": "kiosk-override",
                  "notes": "manager override: someone@x.com", "hours": "7.67"}
REFUSAL = {"created_at": "2026-07-24 14:48:50+00", "category": "face_mismatch",
           "message": "Face didn't match at clock-in (best 0%)."}

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. day classification")

check("A1 a normal punch", one_day(punches=[PUNCH], shifts=[SHIFT])["verdict"], "clocked_in")
check("A2 an override-only punch is called out, not treated as normal",
      one_day(punches=[OVERRIDE_PUNCH], shifts=[SHIFT])["verdict"], "clocked_in_override")
check("A3 a refused attempt", one_day(failures=[REFUSAL], shifts=[SHIFT])["verdict"], "attempted_failed")
check("A4 no punch but they rang sales",
      one_day(shifts=[SHIFT], activity=[{"work_date": "2026-07-24", "kind": "sales rung"}])["verdict"],
      "worked_without_clocking")
check("A5 scheduled and nothing at all", one_day(shifts=[SHIFT])["verdict"], "no_record_scheduled")
check("A6 not scheduled and nothing at all", one_day()["verdict"], "no_record_unscheduled")

# A late-clock-in strike only exists because a punch landed — it is proof of presence.
check("A7 a late strike counts as presence",
      one_day(shifts=[SHIFT], strikes=[{"work_date": "2026-07-24"}])["verdict"],
      "worked_without_clocking")

# A mixed day (one override + one normal punch) is NOT an override day.
check("A8 a day with any self-service punch is a normal day",
      one_day(punches=[OVERRIDE_PUNCH, PUNCH], shifts=[SHIFT])["verdict"], "clocked_in")

section("B. kiosk health — the discriminating evidence")

peers_ok = [{"work_date": "2026-07-24", "store_code": "CERMARK", "device": "kiosk",
             "employee_name": f"Colleague {i}"} for i in range(3)]

d = one_day(shifts=[SHIFT], peer_punches=peers_ok)
check("B1 colleagues clocked in fine => a bare 'no record' day WEAKENS the claim",
      (d["verdict"], d["claim_effect"]), ("no_record_scheduled", "weakens"))
check("B2 ... and says so in words", any("kiosk was working" in n for n in d["notes"]), True)
check("B3 ... and counts them", d["peers_clocked_in"], 3)

d = one_day(shifts=[SHIFT], peer_punches=[])
check("B4 nobody clocked in at that store => SUPPORTS the claim", d["claim_effect"], "supports")
check("B5 ... and says so", any("Nobody at this store" in n for n in d["notes"]), True)

# Peers who ALSO needed an override are not evidence the kiosk worked.
peers_override = [{"work_date": "2026-07-24", "store_code": "CERMARK", "device": "kiosk-override",
                   "employee_name": "Colleague 9"}]
d = one_day(shifts=[SHIFT], peer_punches=peers_override)
check("B6 override-only peers do NOT count as a working kiosk", d["peers_clocked_in"], 0)
check("B7 ... so the day still supports the claim", d["claim_effect"], "supports")

# The employee's own punches must never be counted as their own corroborating peers.
own = [dict(PUNCH, employee_name="Test Person")]
check("B8 the employee is not their own peer",
      one_day(shifts=[SHIFT], peer_punches=own)["peers_clocked_in"], 0)

# A REFUSED attempt stays 'supports' even when colleagues were fine — the server refused THEM.
d = one_day(failures=[REFUSAL], shifts=[SHIFT], peer_punches=peers_ok)
check("B9 a logged refusal supports the claim regardless of peers",
      (d["verdict"], d["claim_effect"]), ("attempted_failed", "supports"))

section("C. absence of evidence is never proof (the central rule)")

r = ce.analyze("Nobody", date(2026, 7, 24), date(2026, 7, 24), [], [], [SHIFT])
check("C1 a blank scheduled day is INCONCLUSIVE, never 'lying'",
      r["days"][0]["claim_effect"], "inconclusive")
check("C2 no verdict in the whole vocabulary accuses anyone",
      any(w in " ".join(ce.VERDICTS.values()).lower() for w in ("lying", "lie", "dishonest", "fraud")),
      False)
check("C3 the limits are stated on every response", len(r["limits"]), 3)
check("C4 ... including the device-side blind spot",
      any("leaves no record" in x for x in r["limits"]), True)
check("C5 'supports'/'weakens'/'inconclusive' are the only claim effects used",
      set(ce.CLAIM_EFFECT.values()) <= {"supports", "weakens", "inconclusive", "neutral"}, True)

# C6-C8: the module must not commit its own error — UNCHECKED peer data is not "nobody clocked in".
# Omitting the argument (None) and loading it to find nothing ([]) are different facts.
d_unknown = one_day(shifts=[SHIFT])                      # peer_punches not supplied
d_known_empty = one_day(shifts=[SHIFT], peer_punches=[])  # loaded, genuinely nobody
check("C6 unchecked peer data => inconclusive, not corroboration",
      d_unknown["claim_effect"], "inconclusive")
check("C7 ... and it SAYS the check was not run",
      any("were not checked" in n for n in d_unknown["notes"]), True)
check("C8 a real empty peer set still supports the claim",
      d_known_empty["claim_effect"], "supports")

section("D. Janet Garibay — the real rows (measured 2026-08-11)")

JANET_PUNCH = {"work_date": "2026-07-23", "store_code": "3352 26TH", "device": "kiosk-override",
               "hours": "122.19", "face_match_pct": None,
               "notes": "manager override: janet.garibay@luxelinkwireless.com | stale punch "
                        "(opened 2026-07-23) closed from kiosk — review hours"}
JANET_SHIFT = {"shift_date": "2026-07-23", "store_code": "3352 26TH", "scheduled_hours": "0",
               "start_time": None, "end_time": None}

rep = ce.analyze("Janet Garibay", date(2026, 7, 23), date(2026, 8, 5),
                 [JANET_PUNCH], [], [JANET_SHIFT])
check("D1 fourteen days reviewed", len(rep["days"]), 14)
d0 = rep["days"][0]
check("D2 07/23 is an override punch", d0["verdict"], "clocked_in_override")
check("D3 ... flagged as a manager override in words",
      any("manager override" in n.lower() for n in d0["notes"]), True)
check("D4 ... and its 122.19 h is disclosed as not a measure of time worked",
      any("auto-closed" in n for n in d0["notes"]), True)
check("D5 an override punch SUPPORTS a report of clock-in trouble", d0["claim_effect"], "supports")
check("D6 the other 13 days hold nothing and are NOT scheduled",
      rep["counts"].get("no_record_unscheduled"), 13)
check("D7 no day is scored as weakening her claim", rep["claim_effects"].get("weakens"), None)
check("D8 zero refused attempts were logged for her", rep["counts"].get("attempted_failed"), None)

section("E. placeholder shift rows are not schedules")

check("E1 a 0-hour, no-times shift row does not make a day 'scheduled'",
      one_day(shifts=[PLACEHOLDER])["verdict"], "no_record_unscheduled")
check("E2 a real shift row does", one_day(shifts=[SHIFT])["verdict"], "no_record_scheduled")
check("E3 a placeholder contributes no scheduled hours",
      one_day(shifts=[PLACEHOLDER])["scheduled_hours"], 0)
check("E4 a real one does", one_day(shifts=[SHIFT])["scheduled_hours"], 7.75)

section("F. ARMED negative control")

_f0 = len(FAIL)
check("F-armed blank day", one_day(shifts=[SHIFT])["claim_effect"], "weakens")     # wrong: no peer data
check("F-armed override", one_day(punches=[OVERRIDE_PUNCH], shifts=[SHIFT])["verdict"], "clocked_in")
fired = len(FAIL) - _f0
if fired == 2:
    FAIL[:] = FAIL[:_f0]
    PASS.append("F1 negative control fired on both wrong expectations (checks are live)")
else:
    FAIL.append(f"F1 NEGATIVE CONTROL DID NOT FIRE — {fired}/2 wrong answers accepted.")

print(f"\n{'=' * 78}")
for f in FAIL:
    print(f"  ✗ {f}")
print(f"  PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
