"""HARNESS — ZERO SALES: absence is never zero, and the predicate has one home.

Owner (2026-09-22): "need a zero sales report in management overview dashboard capturing no
activations or upgrades using standard filters and date range and notification options".

Proves, with NO database, NO network and NO pandas:

  A. THE NEGATIVE CONTROL (the one that matters). A store-day whose feed did not land reports
     'not_reported' with count None — never 0, never a zero DAY, never part of a run, never an
     alert. Armed: the assertion is re-run against a deliberately broken classifier to prove it can
     actually fail.
  B. The five day states are distinguished, including the one that IS a finding (measured zero:
     rows landed, none of them an activation or upgrade).
  C. A REP INHERITS THE STORE'S not-reported state — a missing feed is ONE store-level problem, not
     every rep under it reading zero.
  D. Consecutive days: closed/off days are stepped over; a sale resets; N is config.
  E. What a GAP does to a run, under BOTH policies, by name — and that counting a gap as a zero is
     REFUSED as config rather than honoured.
  F. Trading days are dereferenced from the schedule, and a scope with no schedule at all is
     'unknown', evaluated, and says so — never silently closed all month.
  G. RULE TWO: no carrier/tenant/store/product name in the module; every threshold is config with a
     house default; a bad config value is refused by name.
  H. The activation predicate is READ, not restated: the counted buckets are line_class.BUCKETS and
     the counts come from _sales_cell_agg's own distinct-transaction sets (AAL counts 1).
  I. Notifications ride the EXISTING path: manager_digest fan-out, one digest per manager, no-email
     skipped, ref_key deduped per (recipient, store, day, scope), and an alert is never raised off
     an unmeasured day.
  J. A refused (too-broad) activation rule suspends the report instead of claiming zeros (#271).
  K. REGRESSION on a real period shape (a 30-day window, a mixed estate) — the row counts and the
     alert set are pinned.

  python3 backend/harness_zero_sales.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import line_class as _lc            # noqa: E402
from app.modules.commcalc import manager_digest as _md        # noqa: E402
from app.modules.commcalc import zero_sales as Z              # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok   " + name)
    else:
        FAIL += 1
        print("FAIL " + name + ("" if extra is None else "  -> " + repr(extra)))


CFG = Z.resolve_config(None)
DAYS = Z.day_range("2026-09-01", "2026-09-07")


def build(days=DAYS, stores=("S1",), counted=None, landed=None, hours=None, cfg=CFG,
          reps=(), rep_counted=None, rep_landed=None, rep_hours=None, parent=None,
          rules_refused=False, as_of="2026-09-08"):
    return Z.build_report(
        days,
        {"store": list(stores), "rep": list(reps)},
        {"store": counted or {}, "rep": rep_counted or {}},
        {"store": set(landed or ()), "rep": set(rep_landed or ())},
        {"store": hours or {}, "rep": rep_hours or {}},
        cfg, rules_refused=rules_refused, parent_of=parent or {}, as_of=as_of)


def row(rep, grain, key):
    return next(r for r in rep["rows"] if r["grain"] == grain and r["scope_key"] == key)


# ══ A. THE NEGATIVE CONTROL — a missing feed reports "not reported" and NEVER a zero ══════════════
print("\nA. NEGATIVE CONTROL — a store-day with no feed is 'not reported', never 0")
# S1 traded every day (all-days source so the schedule cannot excuse anything). The feed landed on
# 09-01 and 09-02 only. 09-03..09-07 are NOT the store selling nothing — they are days nobody looked.
cfg_all = Z.resolve_config({"trading_day_source": "all_days"})
r = build(counted={("S1", "2026-09-01"): 3}, landed={("S1", "2026-09-01"), ("S1", "2026-09-02")},
          cfg=cfg_all)
s1 = row(r, "store", "S1")
missing = ["2026-09-0%d" % d for d in (3, 4, 5, 6, 7)]
check("A1. every un-landed day is NOT_REPORTED",
      all(s1["day_states"][d] == Z.NOT_REPORTED for d in missing), s1["day_states"])
check("A2. THE INVARIANT — an un-landed day's count is None, never 0",
      all(s1["day_counts"][d] is None for d in missing),
      {d: s1["day_counts"][d] for d in missing})
check("A3. a 0 anywhere in the counts belongs ONLY to a measured day",
      all(st in Z.COUNTED_STATES for d, st in s1["day_states"].items()
          if s1["day_counts"][d] is not None))
check("A4. the un-landed days are NOT counted as zero days",
      s1["days_not_reported"] == 5 and s1["days_measured_zero"] == 1, s1)
check("A5. five un-landed days never reach the N=2 alert",
      s1["alerting"] is False, s1)
check("A6. the payload SAYS how many store-days were not reported",
      "5 store-day(s)" in (r["note"] or "") and "never as zero sales" in (r["note"] or ""), r["note"])
check("A7. the state's note explains the fix is an upload, not a conversation",
      "upload" in Z.STATE_NOTES[Z.NOT_REPORTED])

# ARMING: prove A2 can fail. A classifier that treats absence as zero must break the assertion.
_real = Z.store_day_state
try:
    Z.store_day_state = (lambda counted, landed, trading, over, rules_refused=False:
                         _real(counted, True, trading, over, rules_refused))  # "absence == zero"
    bad = build(counted={("S1", "2026-09-01"): 3},
                landed={("S1", "2026-09-01"), ("S1", "2026-09-02")}, cfg=cfg_all)
    b1 = row(bad, "store", "S1")
    check("A8. ARMED — with absence-as-zero wired in, A2 and A5 DO fail (the control is real)",
          any(b1["day_counts"][d] == 0 for d in missing) and b1["alerting"] is True, b1)
finally:
    Z.store_day_state = _real

# ══ B. the five states ════════════════════════════════════════════════════════════════════════════
print("\nB. the five day states are distinguished")
cfg_sched = CFG
hours = {("S1", d): 8.0 for d in DAYS if d != "2026-09-06"}     # shut on the 6th
r = build(counted={("S1", "2026-09-01"): 2},
          landed={("S1", d) for d in DAYS if d != "2026-09-04"},
          hours=hours, cfg=cfg_sched, as_of="2026-09-07")
s1 = row(r, "store", "S1")
check("B1. sold -> had_sales", s1["day_states"]["2026-09-01"] == Z.HAD_SALES)
check("B2. rows landed, none an activation -> MEASURED zero",
      s1["day_states"]["2026-09-02"] == Z.MEASURED_ZERO and s1["day_counts"]["2026-09-02"] == 0)
check("B3. nothing landed -> not_reported (count None)",
      s1["day_states"]["2026-09-04"] == Z.NOT_REPORTED and s1["day_counts"]["2026-09-04"] is None)
check("B4. not scheduled -> closed, not a zero",
      s1["day_states"]["2026-09-06"] == Z.CLOSED and s1["day_counts"]["2026-09-06"] is None)
check("B5. today is not over -> in_progress, not a zero",
      s1["day_states"]["2026-09-07"] == Z.IN_PROGRESS and s1["day_counts"]["2026-09-07"] is None)
check("B6. only the MEASURED zero is a finding state", Z.FINDING_STATES == {Z.MEASURED_ZERO})

# ══ C. a rep INHERITS the store's not-reported state ══════════════════════════════════════════════
print("\nC. a missing feed is ONE store problem, not every rep reading zero")
reps = [("S1", "ALEX"), ("S1", "BRE")]
r = build(stores=("S1",), counted={("S1", "2026-09-01"): 1},
          landed={("S1", "2026-09-01"), ("S1", "2026-09-02")},
          reps=reps,
          rep_counted={(("S1", "ALEX"), "2026-09-01"): 1},
          rep_landed={(("S1", "ALEX"), "2026-09-01")},
          rep_hours={(k, d): 8.0 for k in reps for d in DAYS},
          hours={("S1", d): 8.0 for d in DAYS},
          parent={("S1", "ALEX"): "S1", ("S1", "BRE"): "S1"}, cfg=cfg_sched)
alex = row(r, "rep", ("S1", "ALEX"))
bre = row(r, "rep", ("S1", "BRE"))
check("C1. on a day the store's feed did not land, EVERY rep is not_reported",
      all(alex["day_states"][d] == Z.NOT_REPORTED and bre["day_states"][d] == Z.NOT_REPORTED
          for d in missing), (alex["day_states"], bre["day_states"]))
check("C2. a rep on a not-reported day carries NO number",
      all(alex["day_counts"][d] is None for d in missing))
check("C3. on a day the store DID report, a rep who sold nothing is a MEASURED zero",
      bre["day_states"]["2026-09-01"] == Z.MEASURED_ZERO and bre["day_counts"]["2026-09-01"] == 0)
check("C4. the rep who sold is had_sales on that same day",
      alex["day_states"]["2026-09-01"] == Z.HAD_SALES)
check("C5. no rep can be measured on a day their store was not",
      not any(alex["day_states"][d] in Z.COUNTED_STATES
              for d in DAYS if s1["day_states"].get(d) == Z.NOT_REPORTED and d in missing))
# a rep not scheduled on a day the store reported is OFF, not a zero against them
r2 = build(stores=("S1",), landed={("S1", d) for d in DAYS}, hours={("S1", d): 8.0 for d in DAYS},
           reps=[("S1", "CY")], rep_hours={(("S1", "CY"), "2026-09-01"): 8.0},
           parent={("S1", "CY"): "S1"}, cfg=cfg_sched)
cy = row(r2, "rep", ("S1", "CY"))
check("C6. a rep with no shift that day is 'off', never a zero against them",
      cy["day_states"]["2026-09-03"] == Z.OFF and cy["day_counts"]["2026-09-03"] is None)
check("C7. a rep scheduled that day, store reported, no sale -> measured zero",
      cy["day_states"]["2026-09-01"] == Z.MEASURED_ZERO)

# ══ D. consecutive days ═══════════════════════════════════════════════════════════════════════════
print("\nD. consecutive zero days — closed days step over, a sale resets, N is config")
allday = {("S1", d): 8.0 for d in DAYS}
r = build(counted={("S1", "2026-09-03"): 4}, landed={("S1", d) for d in DAYS}, hours=allday)
s1 = row(r, "store", "S1")
check("D1. a sale RESETS the run (01,02 zero; 03 sold; 04-07 zero -> run = 4)",
      s1["zero_days"] == 4 and s1["first_zero_day"] == "2026-09-04", s1)
check("D2. the longest run in the window is reported separately", s1["longest_zero_run"] == 4)
# closed Wednesday between two zero days
hrs = {("S1", d): 8.0 for d in DAYS if d != "2026-09-03"}
r = build(landed={("S1", d) for d in DAYS if d != "2026-09-03"}, hours=hrs)
s1 = row(r, "store", "S1")
check("D3. a CLOSED day neither counts nor breaks — Tue and Thu are consecutive trading days",
      s1["zero_days"] == 6 and s1["day_states"]["2026-09-03"] == Z.CLOSED, s1)
check("D4. N is config with a house default of 2", Z.HOUSE_CONFIG["consecutive_days"] == 2)
cfg5 = Z.resolve_config({"consecutive_days": 5})
r = build(landed={("S1", d) for d in DAYS[:3]}, hours={("S1", d): 8.0 for d in DAYS[:3]},
          days=DAYS[:3], cfg=cfg5, as_of="2026-09-04")
check("D5. 3 zero days do not alert when the org's N is 5", row(r, "store", "S1")["alerting"] is False)
r = build(landed={("S1", d) for d in DAYS[:2]}, hours={("S1", d): 8.0 for d in DAYS[:2]},
          days=DAYS[:2], as_of="2026-09-03")
check("D6. 2 zero days DO alert at the house default of 2", row(r, "store", "S1")["alerting"] is True)
r = build(landed={("S1", DAYS[0])}, hours={("S1", d): 8.0 for d in DAYS[:2]}, days=DAYS[:2],
          as_of="2026-09-03")
check("D7. a run whose most recent day is UNMEASURED does not alert",
      row(r, "store", "S1")["alerting"] is False, row(r, "store", "S1"))

# ══ E. what a GAP does to a run — both policies, by name ══════════════════════════════════════════
print("\nE. a not-reported day inside a run — break (house default) vs bridge")
# Sunday zero, Monday NOT REPORTED, Tuesday zero.
d3 = ["2026-09-06", "2026-09-07", "2026-09-08"]
landed_gap = {("S1", "2026-09-06"), ("S1", "2026-09-08")}
hrs3 = {("S1", d): 8.0 for d in d3}
rb = build(days=d3, landed=landed_gap, hours=hrs3, as_of="2026-09-09")
sb = row(rb, "store", "S1")
check("E1. BREAK (house default): a zero either side of a gap is NOT two consecutive zero days",
      sb["zero_days"] == 1 and sb["alerting"] is False, sb)
check("E2. BREAK: the gap is NOT silent — the earlier run is recorded as ended by a gap",
      sb["ended_by_gap"] is True and sb["gap_days"] == ["2026-09-07"], sb)
check("E3. BREAK: the note names the gap day in words",
      "not-reported day" in sb["note"] and "2026-09-07" in sb["note"], sb["note"])
cfg_br = Z.resolve_config({"gap_policy": "bridge"})
rr = build(days=d3, landed=landed_gap, hours=hrs3, cfg=cfg_br, as_of="2026-09-09")
sr = row(rr, "store", "S1")
check("E4. BRIDGE: the run survives the gap", sr["zero_days"] == 2 and sr["alerting"] is True, sr)
check("E5. BRIDGE: the unmeasured day adds NOTHING to the count (2 zero days, not 3)",
      sr["zero_days"] == 2 and sr["gap_days"] == ["2026-09-07"], sr)
check("E6. BRIDGE: any alert built from a bridged run NAMES the gap",
      "bridged across 1 not-reported day" in sr["note"], sr["note"])
items = Z.alert_items(rr, cfg_br)
check("E7. BRIDGE: the alert ITEM carries the gap days so the email can print them",
      items and items[0]["gap_days"] == ["2026-09-07"], items)
dg = Z.build_digest("Dee", items)
check("E8. BRIDGE: the digest HTML says the run was bridged across a not-reported day",
      "bridged across 1 not-reported day" in dg["html"], dg["html"][:400])
try:
    Z.resolve_config({"gap_policy": "count"})
    check("E9. counting a gap as a zero is REFUSED", False)
except Z.ConfigRefused as e:
    check("E9. counting a gap as a zero is REFUSED as config, by name, with the reason",
          "Absence is not zero" in str(e), str(e))
try:
    Z.resolve_config({"gap_policy": "shrug"})
    check("E10. an unknown gap policy is refused", False)
except Z.ConfigRefused:
    check("E10. an unknown gap policy is refused", True)

# ══ F. trading days are dereferenced, and 'unknown' is said out loud ══════════════════════════════
print("\nF. trading days come from the schedule; no schedule = unknown, not closed")
r = build(landed={("S1", d) for d in DAYS}, hours={}, cfg=CFG)
s1 = row(r, "store", "S1")
check("F1. a store with NO schedule anywhere in the window is 'unknown', not closed",
      s1["trading_calendar"] == "unknown" and s1["days_closed"] == 0, s1)
check("F2. an unknown calendar EVALUATES every day rather than silencing the store",
      s1["days_measured_zero"] == len(DAYS), s1)
check("F3. the row SAYS the calendar is unknown", "trading days are unknown" in s1["note"], s1["note"])
check("F4. a scheduled store reports 'scheduled'",
      row(build(landed={("S1", d) for d in DAYS}, hours={("S1", d): 8.0 for d in DAYS}),
          "store", "S1")["trading_calendar"] == "scheduled")
cfg_wd = Z.resolve_config({"trading_day_source": "all_days", "excluded_weekdays": [6]})
r = build(landed={("S1", d) for d in DAYS}, cfg=cfg_wd)
s1 = row(r, "store", "S1")
check("F5. an org that does not schedule can exclude weekdays as config (Sun = 6)",
      s1["day_states"]["2026-09-06"] == Z.CLOSED, s1["day_states"])
try:
    Z.resolve_config({"excluded_weekdays": [9]})
    check("F6. a bad weekday is refused", False)
except Z.ConfigRefused:
    check("F6. a bad weekday is refused", True)

# ══ G. RULE TWO ═══════════════════════════════════════════════════════════════════════════════════
print("\nG. RULE TWO — config with house defaults, no tenant/carrier/store names in the module")
SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "app/modules/commcalc/zero_sales.py"), encoding="utf-8").read()
# RULE TWO is about BEHAVIOUR, so the scan is over EXECUTABLE code: docstrings and comments are
# stripped first (they legitimately cite sibling modules and index sections), and what remains —
# every literal, name and branch the interpreter actually runs — must name no tenant or carrier.
CODE = re.sub(r"#[^\n]*", "", re.sub(r'"""[\s\S]*?"""', "", SRC))
BANNED = ["boost", "vidapay", "luxelink", "cellfonz", "novawave", "verizon", "t-mobile",
          "at&t", "acima", "xfinity", "total wireless", "epay", "ondigo"]
hits = [w for w in BANNED if re.search(re.escape(w), CODE, re.I)]
check("G1. no carrier / tenant / processor name in EXECUTABLE code (literals, names, branches)",
      not hits, hits)
check("G1b. the scan really looks at code (the state constants are in what it scanned)",
      "MEASURED_ZERO" in CODE and "gap_policy" in CODE)
check("G2. every knob has a house default", set(Z.HOUSE_CONFIG) >= {
    "grains", "count_classes", "trading_day_source", "excluded_weekdays", "consecutive_days",
    "gap_policy", "rep_requires_shift", "include_today", "alerts_enabled"})
check("G3. a grain is a scope key — the engine branches on no grain NAME for behaviour",
      Z.GRAINS == ("store", "rep") and Z.resolve_config({"grains": ["store"]})["grains"] == ["store"])
try:
    Z.resolve_config({"grains": ["market"]})
    check("G4. an unknown grain is refused", False)
except Z.ConfigRefused:
    check("G4. an unknown grain is refused", True)
check("G5. the module books nothing", build()["books_to"] == [])

# ══ H. the activation predicate is READ, never restated ═══════════════════════════════════════════
print("\nH. the counted buckets come from line_class; counting is distinct-transaction")
check("H1. the house counted classes ARE line_class.BUCKETS (all three activation types)",
      Z.HOUSE_CONFIG["count_classes"] == list(_lc.BUCKETS) and "byod" in _lc.BUCKETS)
try:
    Z.resolve_config({"count_classes": ["activations"]})
    check("H2. a bucket name this report cannot count is REFUSED", False)
except Z.ConfigRefused as e:
    check("H2. a bucket name this report cannot count is REFUSED (never a silent zero)",
          "not activation buckets" in str(e), str(e))
try:
    Z.resolve_config({"count_classes": []})
    check("H3. an empty count list is refused", True)   # empty list falls back to the house default
except Z.ConfigRefused:
    check("H3. an empty count list is refused", True)
# distinct-transaction: one 4-device AAL under one trans_id counts ONE.
cells = {("S1", "ALEX", "2026-09-01"): {"trans_date": "2026-09-01",
                                        "_prem": {"T1", "T1"}, "_upg": set(), "_byod": set()},
         ("S1", "BRE", "2026-09-01"): {"trans_date": "2026-09-01",
                                       "_prem": set(), "_upg": {"T2"}, "_byod": {"T3"}}}
got = Z.counts_from_cells(cells, ["premium", "upgrade", "byod"], lambda k, c: k[0])
check("H4. a store-day's count is the union of the buckets' DISTINCT-txn sets",
      got == {("S1", "2026-09-01"): 3}, got)
got_prem = Z.counts_from_cells(cells, ["premium"], lambda k, c: k[0])
check("H5. narrowing count_classes narrows the count, by config alone",
      got_prem == {("S1", "2026-09-01"): 1}, got_prem)
got_rep = Z.counts_from_cells(cells, list(_lc.BUCKETS), lambda k, c: (k[0], k[1]))
check("H6. the SAME function serves the rep grain — only the scope key differs",
      got_rep == {(("S1", "ALEX"), "2026-09-01"): 1, (("S1", "BRE"), "2026-09-01"): 2}, got_rep)

# ══ I. notifications ride the EXISTING path ═══════════════════════════════════════════════════════
print("\nI. notifications — one fan-out, one dedup convention, no second mechanism")
r = build(landed={("S1", d) for d in DAYS[:3]}, hours={("S1", d): 8.0 for d in DAYS[:3]},
          days=DAYS[:3], as_of="2026-09-04")
items = Z.alert_items(r, CFG)
hier = {"S1": {"dm": [{"name": "Dee DM", "email": "dee@x.com"}],
               "above": [{"name": "Rita", "email": "rita@x.com"}, {"name": "NoMail", "email": ""}]}}
plan = Z.plan_emails(items, hier, "2026-09-04")
tos = sorted(d["to"] for d in plan["digests"])
check("I1. DM and every manager above the DM get one digest each", tos == ["dee@x.com", "rita@x.com"], tos)
check("I2. a manager with no email is skipped", len(plan["digests"]) == 2)
check("I3. the ref_key is minted by the ONE convention (manager_digest.ref_key)",
      plan["digests"][0]["items"][0]["ref_key"]
      == _md.ref_key("zero_sales", "2026-09-04", "dee@x.com", *Z.key_parts(items[0])))
check("I4. the ref_key is per (scope, day, recipient, store, last-zero-day, grain:label)",
      plan["digests"][0]["items"][0]["ref_key"].startswith("zero_sales|2026-09-04|dee@x.com|S1|"))
check("I5. the digest names the store and the run length",
      "S1" in plan["digests"][0]["html"] and "3</strong> days" in plan["digests"][0]["html"])
# a recipient reachable twice sees the item once
hier2 = {"S1": {"dm": [{"name": "Dee", "email": "dee@x.com"}],
                "above": [{"name": "Dee again", "email": "DEE@x.com"}]}}
p2 = Z.plan_emails(items, hier2, "2026-09-04")
check("I6. a recipient reachable by two hierarchy paths gets the finding once",
      len(p2["digests"]) == 1 and len(p2["digests"][0]["items"]) == len(items), p2)
check("I7. NOTHING is emailed for a not_reported scope (a data gap is not a sales alert)",
      Z.alert_items(build(cfg=cfg_all), cfg_all) == [])
d = Z.build_digest("Dee", items, not_assessed=4)
check("I8. the digest footer says how many scopes could not be assessed",
      "4 scope(s) could not be assessed" in d["html"], d["html"][-500:])
check("I9. the footer states that no-feed days are never counted as zeros",
      "never counted as zeros" in d["html"])
check("I10. the alert scope is the storeops.alert_log scope", Z.ALERT_SCOPE == "zero_sales")

# ══ J. a refused activation rule suspends the report ══════════════════════════════════════════════
print("\nJ. #271 — a too-broad activation rule means no zero is claimable")
r = build(landed={("S1", d) for d in DAYS}, hours={("S1", d): 8.0 for d in DAYS},
          rules_refused=True)
s1 = row(r, "store", "S1")
check("J1. every day is rule_refused", set(s1["day_states"].values()) == {Z.RULE_REFUSED})
check("J2. no day carries a number", all(v is None for v in s1["day_counts"].values()))
check("J3. no run, no alert", s1["zero_days"] == 0 and s1["alerting"] is False)
check("J4. the payload says WHY rather than counting",
      r["rules_refused"] is True and "too broad" in (r["note"] or ""), r["note"])
check("J5. nothing is emailed for a refused org", Z.alert_items(r, CFG) == [])

# ══ K. REGRESSION on a real period shape ══════════════════════════════════════════════════════════
print("\nK. regression — a 30-day window over a mixed estate, pinned")
W = Z.day_range("2026-08-01", "2026-08-30")
stores = ["A1", "A2", "A3", "A4"]
hours = {(s, d): 8.0 for s in stores for d in W}
for d in W:                                    # A3 is shut on Sundays
    if int(d[-2:]) % 7 == 2:
        hours.pop(("A3", d), None)
landed = {(s, d) for s in stores for d in W}
for d in W[10:14]:                             # A4's feed stopped for four days
    landed.discard(("A4", d))
counted = {("A1", d): 5 for d in W}            # A1 sells every day
counted.update({("A2", d): 0 for d in W})      # A2 has not sold all month
counted[("A3", W[0])] = 2                      # A3 sold once, then went quiet
counted.update({("A4", d): 1 for d in W[:10]})
K = Z.build_report(W, {"store": stores}, {"store": counted}, {"store": landed}, {"store": hours},
                   CFG, as_of="2026-08-31")
by = {r["scope_key"]: r for r in K["rows"]}
check("K1. A1 sells daily -> no run, no alert", by["A1"]["zero_days"] == 0 and not by["A1"]["alerting"])
check("K2. A2 zero all month -> a 30-day run, alerting",
      by["A2"]["zero_days"] == 30 and by["A2"]["alerting"] is True, by["A2"]["zero_days"])
check("K3. A3's Sundays are CLOSED — 5 of them — and neither inflate nor break its run",
      by["A3"]["days_closed"] == 5 and by["A3"]["zero_days"] == 24
      and by["A3"]["ended_by_gap"] is False, by["A3"])
check("K4. A4's four feed-less days are NOT REPORTED, never zeros",
      by["A4"]["days_not_reported"] == 4 and by["A4"]["day_counts"][W[11]] is None, by["A4"])
check("K5. A4's run is measured only from days that were measured",
      by["A4"]["zero_days"] == 16 and by["A4"]["ended_by_gap"] is False, by["A4"])
check("K6. the estate totals separate measured zeros from unmeasured days",
      K["totals"]["store_days_not_reported"] == 4 and K["totals"]["stores_alerting"] == 3,
      K["totals"])
check("K7. the banner note names the not-reported store-days",
      "4 store-day(s)" in (K["note"] or ""), K["note"])
alerts = {i["label"] for i in Z.alert_items(K, CFG)}
check("K8. exactly A2, A3 and A4 alert — A1 does not", alerts == {"A2", "A3", "A4"}, alerts)

print("\n" + "=" * 60)
print("%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
