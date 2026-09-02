"""Proof harness — closing entry-quality coaching detection (owner 2026-09-02, item 3).

Proves, stdlib-only and DB-free:
  A. resolve_config: house defaults, per-org overrides, garbage degradation.
  B. incorrect_days: the two signals (dm_corrected via a verified correction on the submitted
     store-day; sent_to_review via auto_accepted/mgmt_flag), signal selection, no dollars leak.
  C. streaks: consecutive calendar-day runs, gaps split, bad dates skipped.
  D. needs_walkthrough: "a second day in a row" (threshold 2), recency window (an old resolved
     streak never re-notifies), longest/most-recent streak wins.
  E. guidance_message: template placeholders + a tenant template missing a placeholder.

Run: python3 backend/harness_closing_entry_quality.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))
sys.path.insert(0, os.path.dirname(__file__))

from app.modules.closing.entry_quality import (  # noqa: E402
    resolve_config, incorrect_days, streaks, needs_walkthrough, guidance_message,
    DEFAULT_MESSAGE, DEFAULT_TOUR_SLUG)

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


print("A. resolve_config")
c = resolve_config(None)
check("house defaults (pre-937 / no row)", c["enabled"] is True and c["threshold_days"] == 2
      and c["signals"] == ["dm_corrected", "sent_to_review"] and c["notify_channel"] == "none"
      and c["tour_slug"] == DEFAULT_TOUR_SLUG)
c = resolve_config({"threshold_days": 3, "notify_channel": "email", "tour_slug": "my-tour",
                    "signals": ["dm_corrected"], "message_template": "Yo {name}"})
check("per-org overrides", c["threshold_days"] == 3 and c["notify_channel"] == "email"
      and c["tour_slug"] == "my-tour" and c["signals"] == ["dm_corrected"]
      and c["message_template"] == "Yo {name}")
c = resolve_config({"threshold_days": "abc", "notify_channel": "carrier-pigeon", "signals": ["bogus"]})
check("garbage degrades to defaults", c["threshold_days"] == 2 and c["notify_channel"] == "none"
      and c["signals"] == ["dm_corrected", "sent_to_review"])
check("enabled=false honoured", resolve_config({"enabled": False})["enabled"] is False)

print("B. incorrect_days")
ver = {("S1", "2026-09-01"): {"verified": True, "dm_store_cash": 100.0},
       ("S1", "2026-09-02"): {"verified": True},                       # verified, NO correction
       ("S2", "2026-09-01"): {"verified": False, "dm_store_cash": 50}}  # correction but unverified
rows = [
    {"employee_name": "Ana", "store_code": "S1", "close_date": "2026-09-01"},   # dm_corrected
    {"employee_name": "Ana", "store_code": "S1", "close_date": "2026-09-02",
     "auto_accepted": True},                                                    # sent_to_review
    {"employee_name": "Bob", "store_code": "S2", "close_date": "2026-09-01"},   # unverified corr → no
    {"employee_name": "Bob", "store_code": "S1", "close_date": "2026-09-02"},   # verified no corr → no
    {"employee_name": "Cyn", "store_code": "S3", "close_date": "2026-09-02", "mgmt_flag": True},
]
d = incorrect_days(rows, ver)
check("dm_corrected fires only on verified WITH correction",
      d.get("Ana", {}).get("2026-09-01") == ["dm_corrected"] and "Bob" not in d)
check("sent_to_review via auto_accepted / mgmt_flag",
      d["Ana"]["2026-09-02"] == ["sent_to_review"] and d["Cyn"]["2026-09-02"] == ["sent_to_review"])
d1 = incorrect_days(rows, ver, signals=["dm_corrected"])
check("signal selection narrows detection", "2026-09-02" not in d1.get("Ana", {}) and "Cyn" not in d1)
check("no dollar amounts anywhere in reasons",
      all(isinstance(r, str) and "$" not in r for days in d.values() for rs in days.values() for r in rs))

print("C. streaks")
check("consecutive run detected", streaks(["2026-09-01", "2026-09-02", "2026-09-03"]) ==
      [("2026-09-01", "2026-09-03", 3)])
check("gap splits runs", streaks(["2026-09-01", "2026-09-03", "2026-09-04"]) ==
      [("2026-09-01", "2026-09-01", 1), ("2026-09-03", "2026-09-04", 2)])
check("month boundary is consecutive", streaks(["2026-08-31", "2026-09-01"]) ==
      [("2026-08-31", "2026-09-01", 2)])
check("bad dates skipped", streaks(["garbage", "2026-09-01"]) == [("2026-09-01", "2026-09-01", 1)])

print("D. needs_walkthrough")
de = {"Ana": {"2026-09-01": ["dm_corrected"], "2026-09-02": ["sent_to_review"]},
      "Bob": {"2026-09-01": ["dm_corrected"]},
      "Cyn": {"2026-08-20": ["dm_corrected"], "2026-08-21": ["dm_corrected"]}}
w = needs_walkthrough(de, 2)
check("second day in a row triggers; single day doesn't",
      {x["employee_name"] for x in w} == {"Ana", "Cyn"})
ana = next(x for x in w if x["employee_name"] == "Ana")
check("streak fields + the run's day-reasons", ana["streak"] == 2
      and ana["streak_start"] == "2026-09-01" and ana["streak_end"] == "2026-09-02"
      and set(ana["days"]) == {"2026-09-01", "2026-09-02"})
w2 = needs_walkthrough(de, 2, recent_within=1, as_of="2026-09-02")
check("recency window: an old resolved streak never re-notifies",
      {x["employee_name"] for x in w2} == {"Ana"})
check("threshold 3 filters both", needs_walkthrough(de, 3) == [])
de2 = {"Ana": {"2026-09-01": ["dm_corrected"], "2026-09-02": ["dm_corrected"],
               "2026-09-05": ["dm_corrected"], "2026-09-06": ["dm_corrected"],
               "2026-09-07": ["dm_corrected"]}}
w3 = needs_walkthrough(de2, 2)
check("longest streak wins and carries only its own days",
      w3[0]["streak"] == 3 and w3[0]["streak_end"] == "2026-09-07"
      and set(w3[0]["days"]) == {"2026-09-05", "2026-09-06", "2026-09-07"})

print("E. guidance_message")
m = guidance_message(DEFAULT_MESSAGE, "Ana", {"2026-09-01": ["dm_corrected"],
                                              "2026-09-02": ["sent_to_review"]})
check("placeholders rendered", "Ana" in m and "2026-09-01, 2026-09-02" in m
      and "manager had to correct" in m and "went to review" in m)
check("tenant template missing placeholders never crashes",
      guidance_message("Fix your closings, {name}.", "Bob", {}) == "Fix your closings, Bob."
      and "{unknown}" in guidance_message("Hi {unknown}", "x", {}))

print()
if FAILS:
    print(f"❌ {len(FAILS)} failure(s): {FAILS}")
    sys.exit(1)
print("✅ harness_closing_entry_quality: ALL PASS")
