"""PROOF: envelope cash that nobody closed the loop on now RAISES ITS HAND.

OWNER 2026-09-07, verbatim: *"We need to spend more time to fix the envelope checking issues. This
the forth month I have no control on envelopes."*

The live evidence when this was written, house org, since 2026-05-01: 1,666 envelopes declared worth
$595,470.29; **1,474 of them ($540,344.38) had no pickup record at all**, and 183 of the 192 that
were collected carried no disposition. Nine envelopes reached "deposited" in four months.

None of that was a missing report — `/closing/envelope-report`, `/closing/pickup` and the
deposit-accountability board all show it on demand. What did not exist, in any of the 49 registered
attention providers, was anything that says it WITHOUT BEING ASKED. Every other blind spot on this
platform pushes itself into the login popup; outstanding cash did not.

THE LESSON THIS CHECK IS BUILT AROUND. `closing_stale_stores` next door has been correct, enabled and
naming the right nine stores the whole time — and nobody ever saw it, because it is registered
cost="heavy", heavy providers run only under deep=True, and the only automatic deep run is a daily
control-box check that had never run. A check that is right and never runs is worth nothing. So §A
below asserts the REGISTRATION, not just the logic.

WHAT THIS PINS
  A. It is registered, and registered CHEAP — it runs on the ordinary login popup.
  B. Declared-but-never-collected is reported, with a grace period, per store, with dollars.
  C. Collected-but-never-dispositioned is reported separately — the money moved and nobody said where.
  D. It nets EEP through `closing/envelope.py`, so an envelope emptied by an APPROVED expense is not
     reported as outstanding. A second derivation of "what is left in the envelope" would drift from
     the Cash Pickup screen; this uses the same one.
  E. Config is per-org with a house default; 0 disables a half; a config read FAILURE falls back to
     the defaults and never silently switches a money alarm off.
  F. Org scoping — one tenant's envelopes never appear in another's alert.

DB-FREE: in-memory fake client, stdlib only.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, detail))


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
ORG, OTHER = "org-1", "org-2"


def day(n):
    """ISO date n days before NOW."""
    return (NOW - timedelta(days=n)).date().isoformat()


class _Q:
    def __init__(self, rows, fail=False):
        self.rows, self.fail = list(rows), fail

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.rows = [r for r in self.rows if r.get(col) == val]
        return self

    def gte(self, col, val):
        self.rows = [r for r in self.rows if str(r.get(col) or "") >= val]
        return self

    def lte(self, col, val):
        self.rows = [r for r in self.rows if str(r.get(col) or "") <= val]
        return self

    def in_(self, col, vals):
        self.rows = [r for r in self.rows if r.get(col) in set(vals)]
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("simulated read failure")
        return type("R", (), {"data": list(self.rows)})()


class Fake:
    def __init__(self, tables, fail_tables=()):
        self.tables, self.fail_tables, self._s = tables, set(fail_tables), None

    def schema(self, name):
        self._s = name
        return self

    def table(self, name):
        return _Q(self.tables.get((self._s, name), []), fail=(name in self.fail_tables))


def build(closings=(), pickups=(), expenses=(), withdrawals=(), tenant=None, fail_tables=()):
    t = {
        ("commcalc", "daily_closing"): list(closings),
        ("commcalc", "cash_pickup"): list(pickups),
        ("commcalc", "closing_expense"): list(expenses),
        ("commcalc", "envelope_withdrawal"): list(withdrawals),
        ("storeops", "tenants"): ([{"org_id": ORG, **tenant}] if tenant else
                                  [{"org_id": ORG}]),
    }
    return Fake(t, fail_tables)


def env(rid, days_ago, store="S1", rep="Rep A", cash=100.0, org=ORG):
    return {"id": rid, "org_id": org, "close_date": day(days_ago), "store_code": store,
            "employee_name": rep, "store_cash": cash, "epay_cash": 0.0}


def pick(days_ago, store="S1", rep="Rep A", picked=True, disposition=None, org=ORG):
    return {"org_id": org, "close_date": day(days_ago), "store_code": store,
            "employee_name": rep, "picked_up": picked, "disposition": disposition}


from app.modules.closing import attention_providers as AP     # noqa: E402


def run(client, org=ORG):
    return AP._p_closing_envelope_outstanding(client, org, {"now": NOW})


def item(items, key):
    return next((i for i in items if i.get("key") == key), None)


UNC, UND = "closing_envelope_uncollected", "closing_envelope_undisposed"

print("=" * 78)
print("A. It is REGISTERED, and registered CHEAP so it actually runs")
print("=" * 78)
from app.modules.core.import_health import PROVIDERS             # noqa: E402
spec = next((p for p in PROVIDERS if p["key"] == "closing_envelope_outstanding"), None)
check("A1 the provider is registered with the universal attention system",
      spec is not None, str(sorted(p["key"] for p in PROVIDERS))[:200])
check("A2 it is cost='cheap' — heavy providers run ONLY under deep=True, and the only automatic "
      "deep run is a daily check that had never run. That is exactly why closing_stale_stores went "
      "four months unseen while being correct.",
      bool(spec) and spec["cost"] == "cheap", str(spec and spec["cost"]))

print()
print("=" * 78)
print("B. Declared but never collected")
print("=" * 78)
it = item(run(build(closings=[env("a", 5), env("b", 4, store="S2", cash=50.0)])), UNC)
check("B1 uncollected envelopes past the grace period are reported",
      bool(it) and it["count"] == 2, str(it))
check("B2 the dollar total is carried, not just a count "
      "(a count alone does not tell you whether to care)",
      bool(it) and "$150.00" in it["detail"], it and it["detail"])
check("B3 the stores are named and the oldest date is given",
      bool(it) and "S1" in it["detail"] and "S2" in it["detail"] and day(5) in it["detail"],
      it and it["detail"])
check("B4 severity is a warning and it deep-links to the screen that fixes it",
      bool(it) and it["severity"] == "warning" and it["deep_link"] == "/closing/pickup", str(it))

it = item(run(build(closings=[env("a", 1)])), UNC)
check("B5 GRACE PERIOD: yesterday's envelope is NOT an alarm — a DM has not had time to collect it",
      it is None, str(it))
it = item(run(build(closings=[env("a", 5)], pickups=[pick(5, disposition="deposited")])), UNC)
check("B6 a collected envelope is not reported as uncollected",
      it is None, str(it))
it = item(run(build(closings=[env("a", 5, cash=0.0)])), UNC)
check("B7 an envelope with no cash is not outstanding (nothing to collect)",
      it is None, str(it))

print()
print("=" * 78)
print("C. Collected, then nobody said where the money went")
print("=" * 78)
items = run(build(closings=[env("a", 5)], pickups=[pick(5, disposition=None)]))
it = item(items, UND)
check("C1 picked up with NO disposition past the grace period is its own finding",
      bool(it) and it["count"] == 1, str(items))
check("C2 …and it is NOT also counted as uncollected — the two findings are disjoint",
      item(items, UNC) is None, str(items))
check("C3 the detail says what is actually unknown",
      bool(it) and "where the money went" in it["detail"], it and it["detail"])
it = item(run(build(closings=[env("a", 5)], pickups=[pick(5, disposition="handed_to_dm")])), UND)
check("C4 a recorded hand-off closes it",
      it is None, str(it))
it = item(run(build(closings=[env("a", 5)], pickups=[pick(5, disposition="   ")])), UND)
check("C5 a whitespace-only disposition does not count as an answer",
      bool(it) and it["count"] == 1, str(it))

print()
print("=" * 78)
print("D. EEP netting comes from closing/envelope.py — never a second derivation")
print("=" * 78)
spent = [{"org_id": ORG, "closing_row_id": "a", "store_code": "S1", "close_date": day(5),
          "amount": 100.0, "status": "approved", "paid": False}]
check("D1 an envelope emptied by an APPROVED expense is not reported as outstanding cash",
      item(run(build(closings=[env("a", 5)], expenses=spent)), UNC) is None)
pending = [dict(spent[0], status="pending", paid=False)]
check("D2 a PENDING expense does NOT net — that cash is still in the envelope",
      bool(item(run(build(closings=[env("a", 5)], expenses=pending)), UNC)))
partial = [dict(spent[0], amount=60.0)]
it = item(run(build(closings=[env("a", 5)], expenses=partial)), UNC)
check("D3 a partial spend reports the REMAINDER ($40), not the gross ($100)",
      bool(it) and "$40.00" in it["detail"], it and it["detail"])
wd = [{"org_id": ORG, "closing_row_id": "a", "store_code": "S1", "close_date": day(5),
       "amount": 100.0, "expense_id": None, "purpose": "commission_payout"}]
check("D4 a recorded withdrawal nets too (same helper the pickup screen uses)",
      item(run(build(closings=[env("a", 5)], withdrawals=wd)), UNC) is None)

print()
print("=" * 78)
print("E. Config: per-org with a house default, and it never fails silent")
print("=" * 78)
it = item(run(build(closings=[env("a", 5)], tenant={"envelope_uncollected_alert_days": 10})), UNC)
check("E1 a longer per-org grace period is honoured (5 days old, threshold 10 -> quiet)",
      it is None, str(it))
items = run(build(closings=[env("a", 5)], pickups=[pick(5, disposition=None)],
                  tenant={"envelope_uncollected_alert_days": 0,
                          "envelope_undisposed_alert_days": 0}))
check("E2 0 disables a half deliberately (an explicit tenant choice, not a bug)",
      items == [], str(items))
it = item(run(build(closings=[env("a", 5)], fail_tables=("tenants",))), UNC)
check("E3 a config READ FAILURE falls back to the house default and still alarms — "
      "an unreadable config table must never silently switch off a money alarm",
      bool(it) and it["count"] == 1, str(it))
it = item(run(build(closings=[env("a", 5)], fail_tables=("closing_expense", "envelope_withdrawal"))), UNC)
check("E4 if EEP cannot be read it reports GROSS rather than nothing — over-reporting outstanding "
      "money is tolerable, under-reporting it is not",
      bool(it) and "$100.00" in it["detail"], it and it["detail"])
check("E5 a daily_closing read failure returns no items rather than raising inside the popup",
      run(build(closings=[env("a", 5)], fail_tables=("daily_closing",))) == [])

print()
print("=" * 78)
print("F. Org scoping")
print("=" * 78)
c = build(closings=[env("a", 5, org=OTHER)])
check("F1 another tenant's outstanding envelope never appears in this org's alert",
      run(c, ORG) == [], str(run(c, ORG)))
c = build(closings=[env("a", 5, org=ORG), env("b", 5, org=OTHER)])
it = item(run(c, ORG), UNC)
check("F2 …and this org's own envelope is still reported, unaffected by the neighbour",
      bool(it) and it["count"] == 1, str(it))

print()
print("=" * 78)
print(f"RESULT: {P} passed, {F} failed")
print("=" * 78)
sys.exit(1 if F else 0)
