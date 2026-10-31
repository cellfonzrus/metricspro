"""HARNESS — A MISSING TRADING DAY IS REPORTED, NOT INFERRED, AND NEVER CALLED ZERO SALES.

Owner (2026-10-31, verbatim): *"this is a big problem for management iff we wil miss trading days,
we need to get this fix right away"* — said of 957 Pennsylvania Ave, whose 22 and 23 September
sales never arrived, which made a rep's commission claim read $224 and two boxes high against our
own data. Measured the same day across the three live tenants: **324 store-days** of sales we never
received between August and October 2026, several stores missing whole months.

THE CLASS, not the instance. The fact "this store's feed did not land on this day" ALREADY had one
home — `zero_sales.store_day_state` returns `NOT_REPORTED` for it, correctly, and has since
2026-09-22 (§15z). Nothing ever told anybody: `zero_sales.alert_items` deliberately refuses to email
a not-reported scope (a missing feed is not a sales problem and must not send a manager after a rep
who sold fine), handing it to the import-health surface — which had no check for it. So the fact fell
between two surfaces. The fix DEREFERENCES the existing state; it does not derive absence a second
time, and `harness_zero_sales_lock.py` already fails the build on a second absence derivation.

Proves, with NO database, NO network and NO pandas:

  A. THE NEGATIVE CONTROL. A `measured_zero` store-day — rows LANDED and none was an activation —
     is NEVER reported as a missing upload. Confusing the two is the whole defect: one needs a
     manager, the other needs a file. Armed: re-run against a broken predicate to prove it can fail.
  B. A store with no missing days produces no finding at all (no false alarm on a healthy estate).
  C. The finding NAMES the dates, so the fix is an upload somebody can actually perform.
  D. A store whose every TRADING day is missing is its own kind (`whole_window`) and the harsher
     severity: that is a feed that never arrives, not a thin month.
  E. `closed` / `in_progress` / `off` days are NOT missing days — a store shut on Sunday has not
     lost Sunday's upload, and an unfinished day is not a gap.
  F. The threshold is config, the default is documented, and a value that would silence every
     finding is refused rather than honoured.
  G. RULE TWO + ONE HOME: no carrier/tenant/store/product name in the new code, and the state
     string is DEREFERENCED from `zero_sales.NOT_REPORTED` rather than spelled again.
  H. REP grain is never a feed-gap finding — a missing feed is ONE store problem (the §15z rule).
  I. REGRESSION — the shape that surfaced this: a 30-day window where one store lost two midweek
     days, one lost the whole window, one sold every day and one sold nothing at all.

  python3 backend/harness_feed_gap_days.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import zero_sales as Z              # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, got=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % name)
    else:
        FAIL += 1
        print("  FAIL  %s%s" % (name, "" if got is None else "  -> got %r" % (got,)))


def _row(key, label, day_states, *, grain=None, calendar="scheduled", parent=None):
    """A `build_report` row, only the keys a feed-gap finding may read."""
    tally = {}
    for st in day_states.values():
        tally[st] = tally.get(st, 0) + 1
    return {
        "grain": grain or Z.GRAIN_STORE,
        "scope_key": key,
        "label": label,
        "parent": parent,
        "day_states": dict(day_states),
        "trading_calendar": calendar,
        "days_not_reported": tally.get(Z.NOT_REPORTED, 0),
        "days_measured_zero": tally.get(Z.MEASURED_ZERO, 0),
        "days_had_sales": tally.get(Z.HAD_SALES, 0),
        "days_closed": tally.get(Z.CLOSED, 0) + tally.get(Z.OFF, 0),
    }


def _report(rows, days):
    return {"days": list(days), "rows": list(rows), "config": dict(Z.HOUSE_CONFIG)}


W = ["2026-09-%02d" % d for d in range(1, 31)]

print("=" * 60)
print("A. NEGATIVE CONTROL — a measured zero is not a missing upload")
print("=" * 60)

sold_nothing = _row("S-ZERO", "S-ZERO", {d: Z.MEASURED_ZERO for d in W})
rep = _report([sold_nothing], W)
items = Z.feed_gap_items(rep)
check("A1. a store that reported every day and sold nothing raises NO feed gap",
      items == [], items)
check("A2. ... and its measured zeros are still counted as measured",
      sold_nothing["days_measured_zero"] == 30 and sold_nothing["days_not_reported"] == 0)

# ARMED: a predicate that counted any non-selling day as missing WOULD fail A1.
broken = _report([_row("S-ZERO", "S-ZERO", {d: Z.NOT_REPORTED for d in W})], W)
check("A3. the assertion can fail — the same shape marked not-reported DOES raise",
      len(Z.feed_gap_items(broken)) == 1, Z.feed_gap_items(broken))

print()
print("=" * 60)
print("B/C. a healthy store is silent; a real gap names its dates")
print("=" * 60)

healthy = _row("S-OK", "S-OK", {d: Z.HAD_SALES for d in W})
check("B1. a store that reported every day raises nothing",
      Z.feed_gap_items(_report([healthy], W)) == [])

lost_two = dict({d: Z.HAD_SALES for d in W})
lost_two["2026-09-22"] = Z.NOT_REPORTED
lost_two["2026-09-23"] = Z.NOT_REPORTED
got = Z.feed_gap_items(_report([_row("S-957", "957", lost_two)], W))
check("C1. exactly one finding for the one store that lost days", len(got) == 1, got)
check("C2. it names the missing dates, in order",
      got and got[0]["dates"] == ["2026-09-22", "2026-09-23"], got and got[0].get("dates"))
check("C3. it counts them", got and got[0]["missing_days"] == 2, got and got[0].get("missing_days"))
check("C4. it carries the store so the upload can be targeted",
      got and got[0]["store_code"] == "S-957" and got[0]["label"] == "957", got)
check("C5. scattered days are the PARTIAL kind, not a dead feed",
      got and got[0]["kind"] == Z.FEED_GAP_PARTIAL, got and got[0].get("kind"))

print()
print("=" * 60)
print("D. a feed that never arrived is its own kind")
print("=" * 60)

dead = Z.feed_gap_items(_report([_row("S-DEAD", "S-DEAD", {d: Z.NOT_REPORTED for d in W})], W))
check("D1. every trading day missing -> the whole-window kind",
      dead and dead[0]["kind"] == Z.FEED_GAP_WHOLE_WINDOW, dead and dead[0].get("kind"))
check("D2. ... and all 30 days are named", dead and dead[0]["missing_days"] == 30, dead)
check("D3. the two kinds are distinct strings",
      Z.FEED_GAP_WHOLE_WINDOW != Z.FEED_GAP_PARTIAL)

print()
print("=" * 60)
print("E. closed / unfinished days are not missing days")
print("=" * 60)

sundays = {d: (Z.CLOSED if d in ("2026-09-06", "2026-09-13", "2026-09-20", "2026-09-27")
               else Z.HAD_SALES) for d in W}
check("E1. a store shut on Sundays has lost no upload",
      Z.feed_gap_items(_report([_row("S-SUN", "S-SUN", sundays)], W)) == [])

part_closed = dict(sundays)
part_closed["2026-09-10"] = Z.NOT_REPORTED
e2 = Z.feed_gap_items(_report([_row("S-SUN2", "S-SUN2", part_closed)], W))
check("E2. one real gap beside four closed days is ONE missing day",
      e2 and e2[0]["missing_days"] == 1 and e2[0]["dates"] == ["2026-09-10"], e2)
check("E3. ... and a store whose only unreported days sit beside closed ones is PARTIAL, "
      "not a dead feed", e2 and e2[0]["kind"] == Z.FEED_GAP_PARTIAL, e2)

alldead = {d: (Z.CLOSED if d.endswith(("06", "13", "20", "27")) else Z.NOT_REPORTED) for d in W}
e4 = Z.feed_gap_items(_report([_row("S-DEAD2", "S-DEAD2", alldead)], W))
check("E4. every TRADING day missing is whole-window even when closed days exist",
      e4 and e4[0]["kind"] == Z.FEED_GAP_WHOLE_WINDOW, e4)

today = {d: Z.HAD_SALES for d in W}
today["2026-09-30"] = Z.IN_PROGRESS
check("E5. an unfinished day is not a gap",
      Z.feed_gap_items(_report([_row("S-NOW", "S-NOW", today)], W)) == [])

print()
print("=" * 60)
print("F. the threshold is config and cannot be used to silence the report")
print("=" * 60)

one_day = dict({d: Z.HAD_SALES for d in W})
one_day["2026-09-11"] = Z.NOT_REPORTED
r1 = _report([_row("S-ONE", "S-ONE", one_day)], W)
check("F1. the default reports a single missing day — one lost day is a real gap",
      len(Z.feed_gap_items(r1)) == 1 and Z.FEED_GAP_MIN_DAYS == 1, Z.FEED_GAP_MIN_DAYS)
check("F2. a tenant may raise the threshold", Z.feed_gap_items(r1, min_days=2) == [])
check("F3. a threshold below 1 is REFUSED, not honoured — it would silence every finding",
      len(Z.feed_gap_items(r1, min_days=0)) == 1
      and len(Z.feed_gap_items(r1, min_days=-5)) == 1)

print()
print("=" * 60)
print("G. RULE TWO and ONE HOME")
print("=" * 60)

src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "app/modules/commcalc/zero_sales.py")).read()
fn = src[src.index("def feed_gap_items"):]
fn = fn[:fn.index("\ndef ", 10)] if "\ndef " in fn[10:] else fn
code = re.sub(r'""".*?"""', "", fn, flags=re.S)
code = re.sub(r"#.*", "", code)
check("G1. the state string is dereferenced, never respelled",
      '"not_reported"' not in code and "'not_reported'" not in code, code)
check("G2. no tenant / carrier / store / product name in the new code",
      not re.search(r"(?i)boost|luxelink|cellfonz|vzone|total wireless|pennsylvania", code))
check("G3. it reads the report's OWN rows and derives no absence of its own",
      "day_states" in code and "landed" not in code)

print()
print("=" * 60)
print("H. a missing feed is ONE store problem")
print("=" * 60)

store_gap = _row("S-R", "S-R", lost_two)
rep_gap = _row(("S-R", "Rep A"), "Rep A", lost_two, grain=Z.GRAIN_REP, parent="S-R")
h = Z.feed_gap_items(_report([store_gap, rep_gap], W))
check("H1. the rep row under a gapped store adds no second finding",
      len(h) == 1 and h[0]["store_code"] == "S-R", h)

print()
print("=" * 60)
print("I. REGRESSION — the estate shape that surfaced this")
print("=" * 60)

I = Z.feed_gap_items(_report([
    _row("S-957", "957", lost_two),
    _row("S-DEAD", "S-DEAD", {d: Z.NOT_REPORTED for d in W}),
    _row("S-OK", "S-OK", {d: Z.HAD_SALES for d in W}),
    _row("S-ZERO", "S-ZERO", {d: Z.MEASURED_ZERO for d in W}),
], W))
check("I1. two findings out of four stores", len(I) == 2, [i["store_code"] for i in I])
check("I2. the dead feed is reported first — worst first",
      I[0]["store_code"] == "S-DEAD" and I[1]["store_code"] == "S-957", [i["store_code"] for i in I])
check("I3. 32 store-days of sales are named as never received",
      sum(i["missing_days"] for i in I) == 32, sum(i["missing_days"] for i in I))
check("I4. the store that sold nothing is NOT among them — it needs a manager, not a file",
      "S-ZERO" not in {i["store_code"] for i in I})
check("I5. the order is deterministic",
      [i["store_code"] for i in I] == [i["store_code"] for i in
                                       Z.feed_gap_items(_report([
                                           _row("S-ZERO", "S-ZERO", {d: Z.MEASURED_ZERO for d in W}),
                                           _row("S-OK", "S-OK", {d: Z.HAD_SALES for d in W}),
                                           _row("S-957", "957", lost_two),
                                           _row("S-DEAD", "S-DEAD", {d: Z.NOT_REPORTED for d in W}),
                                       ], W))])

print("\n" + "=" * 60)
print("%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
