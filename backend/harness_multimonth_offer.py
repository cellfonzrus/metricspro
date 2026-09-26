"""PROOF — multi-month is OFFERED only when the org has it configured, and multi-month MONEY is never hidden
(owner 2026-09-25: "right now the rep comisison is shown with plan incentive and multi month, if multi month is
not confgured then it should not be shown as an available option").

THE PREDICATE: `commcalc/multimonth_config` — configured = an ACTIVE row in `payout_schedule` (the residual
engine, §7) or `plan_installment_schedule` (the sale-triggered engine, §8) for the org; state 'configured' |
'off' | 'off_with_money' (money on the rep rows while no schedule is active: shown and SAID). DB-free.

  A. decide() — the truth table, incl. an unreadable table (never hides a possibly-live option) and cents.
  B. the reads — org-scoped, active-only, both tables; money summed over every stored spelling of a period.
  C. the R1 pay-source guard (`router._has_any_pay_source`) now counts schedules through the predicate's
     reader and answers exactly as its retired inline counts did (a matrix of fake orgs).
  D. the endpoint `GET /commcalc/multimonth/status` over three fake orgs shaped like the live ones
     (none / configured with money / configured without money) + one off-with-money org.

    python3 backend/harness_multimonth_offer.py
"""
import copy
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import multimonth_config as MM            # noqa: E402
from app.modules.commcalc import router as R                        # noqa: E402

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:300]))


def section(t):
    print("\n── %s %s" % (t, "─" * max(0, 96 - len(t))))


class _Q:
    def __init__(self, rows, fail=False):
        self._rows, self._fail = list(rows), fail

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col)) == str(val)]
        return self

    def in_(self, col, vals):
        vs = {str(v) for v in vals}
        self._rows = [r for r in self._rows if str(r.get(col)) in vs]
        return self

    def _noop(self, *a, **k):
        return self
    limit = order = neq = gte = lte = range = _noop

    def execute(self):
        if self._fail:
            raise RuntimeError("42P01 relation does not exist")
        return type("R", (), {"data": copy.deepcopy(self._rows), "count": len(self._rows)})()


class FakeClient:
    def __init__(self, tables, broken=()):
        self._t, self._broken = tables, set(broken)

    def schema(self, name):
        c = self

        class S:
            def table(self, t):
                return _Q(c._t.get((name, t), []), fail=t in c._broken)
        return S()


# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("A. decide(): configured / off / off_with_money")
d = MM.decide({"payout_schedule": 0, "plan_installment_schedule": 0}, {"July 2026": 0.0})
check("A1 no active schedule, no money → 'off', not offered, no note", (d["state"], d["offered"], d["note"]) == ("off", False, None))
d = MM.decide({"payout_schedule": 2, "plan_installment_schedule": 0}, {})
check("A2 an active residual schedule → configured, offered", (d["state"], d["offered"]) == ("configured", True))
d = MM.decide({"payout_schedule": 0, "plan_installment_schedule": 1}, {})
check("A3 an active sale-triggered schedule → configured, offered", (d["state"], d["offered"]) == ("configured", True))
d = MM.decide({"payout_schedule": 0, "plan_installment_schedule": 0}, {"July 2026": 1370.25, "August 2026": 0})
check("A4 no schedule but $1,370.25 on the rows → 'off_with_money': OFFERED (money is never hidden) and SAID",
      d["state"] == "off_with_money" and d["offered"] and "$1,370.25" in (d["note"] or "") and "July 2026" in d["note"])
d = MM.decide({"payout_schedule": None, "plan_installment_schedule": 0}, {})
check("A5 an UNREADABLE table counts as configured (a failed read never hides a live option)", d["offered"] and d["configured"])
d = MM.decide({"payout_schedule": 0, "plan_installment_schedule": 0}, {"July 2026": 0.004})
check("A6 sub-cent noise is not money", d["state"] == "off")
check("A7 the sources say which table is which", [s["table"] for s in d["sources"]] == ["payout_schedule", "plan_installment_schedule"])

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("B. the reads: org-scoped, active-only, every period spelling")
T = {("commcalc", "payout_schedule"): [{"org_id": "A", "is_active": True}, {"org_id": "A", "is_active": False},
                                       {"org_id": "B", "is_active": True}],
     ("commcalc", "plan_installment_schedule"): [{"org_id": "A", "is_active": False}],
     ("commcalc", "rep_commissions"): [
         {"org_id": "A", "period": "July 2026", "residual_installment_comm": 100.0, "installment_comm_sale": 20.5},
         {"org_id": "A", "period": "2026-07", "residual_installment_comm": 0, "installment_comm_sale": 5},
         {"org_id": "B", "period": "July 2026", "residual_installment_comm": 999, "installment_comm_sale": 0}]}
c = FakeClient(T)
check("B1 active only, org A: 1 residual, 0 sale", MM.schedule_counts(c, "A") == {"payout_schedule": 1, "plan_installment_schedule": 0})
check("B2 another org's schedule never counts (org C: none)", MM.schedule_counts(c, "C") == {"payout_schedule": 0, "plan_installment_schedule": 0})
check("B3 money over both spellings of July for org A = $125.50 (org B's $999 never read)",
      MM.money_by_period(c, "A", ["July 2026"], R._pvariants) == {"July 2026": 125.5})
check("B4 a broken table reads as None (unknown), not 0",
      MM.schedule_counts(FakeClient(T, broken={"plan_installment_schedule"}), "A")["plan_installment_schedule"] is None)

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("C. the R1 pay-source guard answers exactly as before, through the predicate's reader")


def retired_has_any_pay_source(client, org_id, period):
    def _count(table, extra=None):
        try:
            q = client.schema('commcalc').table(table).select('org_id', count='exact').eq('org_id', org_id)
            if extra:
                q = extra(q)
            return (q.limit(1).execute().count) or 0
        except Exception:
            return 0
    if _count('commission_plan_assignment') > 0:
        return True
    if _count('payout_schedule', lambda q: q.eq('is_active', True)) > 0:
        return True
    if _count('plan_installment_schedule', lambda q: q.eq('is_active', True)) > 0:
        return True
    if _count('carrier_commission', lambda q: q.in_('period', R._pvariants(period))) > 0:
        return True
    return False


mism = []
for a, ps, pis, cc, broken in itertools.product((0, 1), (0, 1, 2), (0, 1, 2), (0, 1),
                                                 ((), ("payout_schedule",), ("plan_installment_schedule",))):
    t = {("commcalc", "commission_plan_assignment"): [{"org_id": "O"}] * a,
         ("commcalc", "payout_schedule"): [{"org_id": "O", "is_active": i % 2 == 0} for i in range(ps)],
         ("commcalc", "plan_installment_schedule"): [{"org_id": "O", "is_active": True}] * pis,
         ("commcalc", "carrier_commission"): [{"org_id": "O", "period": "July 2026"}] * cc}
    fc = FakeClient(t, broken=broken)
    if R._has_any_pay_source(fc, "O", "July 2026") != retired_has_any_pay_source(fc, "O", "July 2026"):
        mism.append((a, ps, pis, cc, broken))
check("C1 _has_any_pay_source == its retired inline counts over 108 fake orgs (incl. broken tables)", not mism, mism[:3])

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("D. GET /commcalc/multimonth/status — shaped like the live orgs")
LIVE = {("commcalc", "payout_schedule"): [{"org_id": "LUX", "is_active": True}] * 2 + [{"org_id": "HOUSE", "is_active": True}] * 14,
        ("commcalc", "plan_installment_schedule"): [{"org_id": "LUX", "is_active": True}],
        ("commcalc", "rep_commissions"): [
            {"org_id": "LUX", "period": "July 2026", "residual_installment_comm": 0, "installment_comm_sale": 1370.25},
            {"org_id": "LUX", "period": "August 2026", "residual_installment_comm": 0, "installment_comm_sale": 183.66},
            {"org_id": "OLD", "period": "July 2026", "residual_installment_comm": 42.0, "installment_comm_sale": 0}]}
_live = FakeClient(LIVE)
R.sb = lambda: _live
R.require_org = lambda org_id: None
q = "June 2026,July 2026,August 2026"
s = R.get_multimonth_status(q, org_id="PLAIN")
check("D1 an org with no schedule and no money (the f4f1c16e shape) → off: the option is not offered",
      (s["state"], s["offered"]) == ("off", False))
s = R.get_multimonth_status(q, org_id="LUX")
check("D2 LuxeLink shape (2 residual + 1 sale schedule, $1,553.91 on the rows) → configured, offered",
      (s["state"], s["offered"]) == ("configured", True) and round(sum(s["money"].values()), 2) == 1553.91)
s = R.get_multimonth_status(q, org_id="HOUSE")
check("D3 house shape (14 residual schedules, no money) → configured, offered", s["offered"] and s["state"] == "configured")
s = R.get_multimonth_status(q, org_id="OLD")
check("D4 money on the rows but no schedule → off_with_money: offered and noted", s["state"] == "off_with_money" and s["note"])

print("\n" + "=" * 100)
print("%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
