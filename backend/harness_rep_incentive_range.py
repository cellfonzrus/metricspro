"""PROOF — the Rep Incentive report over a MONTH RANGE, one row per rep per month on one page (owner 2026-09-25:
"al for rep incentive report the range for multiple months should be there to display the commission for teh
months selected in different rowqn on one page").

THE RULE: a month in the range is EXACTLY that month viewed alone — the range endpoint loops the single-month
handler (`router.get_commissions`) over the months THE one enumeration (`account/_period.month_range`) lists;
`rep_incentive_range.assemble` only lays them side by side and sums. DB-free: the REAL handlers over an
in-memory read-only client.

  A. month_range — both spellings in, canonical out, a year boundary, one month, reversed / unparseable / too
     long refused (never guessed); the discrepancy-appeals period filter now enumerates through it and is
     byte-identical to its retired inline loop (fuzz).
  B. THE EQUALITY — for every month of a 3-month range, the range's rows == get_commissions(month), JSON-
     identical (to the cent, every field, the deductions and the market stamp included); a month with no rows
     is present and empty; the month totals and the grand total are the sums of those rows.
  C. THE CAP — 12 months pass, 13 are refused 400 with the reason; a reversed range is refused 400.
  D. STRUCTURE — the endpoint calls `get_commissions(` inside its month loop and enumerates with
     `month_range(`; the pure assembler does no pay arithmetic (sums only); negative controls.

    python3 backend/harness_rep_incentive_range.py
"""
import asyncio
import copy
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.account import _period as PD                      # noqa: E402
from app.modules.commcalc import discrepancy_appeals as DA          # noqa: E402
from app.modules.commcalc import rep_incentive_range as RIR         # noqa: E402
from app.modules.commcalc import router as R                        # noqa: E402

ORG = "00000000-0000-0000-0000-00000000a4a1"
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
    def __init__(self, rows):
        self._rows = list(rows)
        self._order = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col)) == str(val)]
        return self

    def in_(self, col, vals):
        vs = {str(v) for v in vals}
        self._rows = [r for r in self._rows if str(r.get(col)) in vs]
        return self

    def order(self, col, desc=False):
        self._rows = sorted(self._rows, key=lambda r: (r.get(col) or 0), reverse=desc)
        return self

    def _noop(self, *a, **k):
        return self
    neq = not_ = is_ = gte = lte = gt = lt = ilike = like = limit = range = _noop

    def execute(self):
        return type("R", (), {"data": copy.deepcopy(self._rows), "count": len(self._rows)})()


class _Schema:
    def __init__(self, store, name):
        self._store, self._name = store, name

    def table(self, t):
        return _Q(self._store.get((self._name, t), []))


class FakeClient:
    def __init__(self, tables):
        self._t = tables

    def schema(self, name):
        return _Schema(self._t, name)

    def table(self, t):
        return _Q(self._t.get(("public", t), []))

    def rpc(self, *a, **k):
        return type("R", (), {"execute": lambda s=None: type("R2", (), {"data": []})()})()


def rc(period, rep, store, payout, pa=0, ba=0, ua=0):
    return {"org_id": ORG, "period": period, "epay_salesperson": rep, "storeops_name": rep, "store": store,
            "total_payout": payout, "subtotal": payout, "plan_comm": payout, "premium_acts": pa, "byod_acts": ba,
            "upgrade_acts": ua, "tier": 1.0, "kpis_met": 0, "total_kpis": 0}


TABLES = {
    ("commcalc", "rep_commissions"): [
        rc("July 2026", "Rep A", "Store 1", 715.00, 37, 6, 57), rc("July 2026", "Rep B", "Store 1", 320.00, 12, 10, 20),
        rc("2026-08", "Rep A", "Store 1", 365.00, 18, 4, 29),       # a numeric-spelled stored period, read as August
        rc("August 2026", "Rep C", "Store 2", 125.10, 7, 1, 9),
        rc("July 2026", "Rep X", "Store 9", 999.00, 1, 1, 1) | {"org_id": "another-org"},   # never read
    ],
    ("commcalc", "chargeback_items"): [
        {"org_id": ORG, "period": "July 2026", "epay_salesperson": "Rep B", "amount": "25.00", "deduct": True},
        {"org_id": ORG, "period": "August 2026", "epay_salesperson": "Rep C", "amount": 10.05, "deduct": True},
        {"org_id": ORG, "period": "August 2026", "epay_salesperson": "Rep A", "amount": 50, "deduct": False},
    ],
}
_FAKE = FakeClient(TABLES)
R.sb = lambda: _FAKE                       # the handlers' client, in memory (read-only surface)


def run(coro):
    return asyncio.run(coro)


# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE one month enumeration (account/_period.month_range)")
check("A1 either spelling in, canonical out", PD.month_range("2026-06", "August 2026") == ["June 2026", "July 2026", "August 2026"])
check("A2 across a year boundary", PD.month_range("Nov 2025", "2026-02") == ["November 2025", "December 2025", "January 2026", "February 2026"])
check("A3 a missing to-month = the one month", PD.month_range("2026-07") == ["July 2026"])
for bad, why in ((("2026-08", "2026-07"), "reversed"), (("garbage", "2026-07"), "unparseable"),
                 (("2025-01", "2026-01", 12), "13 months over a cap of 12")):
    try:
        PD.month_range(*bad)
        check("A4 %s is refused" % why, False)
    except ValueError:
        check("A4 %s is refused (ValueError, never guessed)" % why, True)


def retired_variants(pf, pt):
    y0, m0 = DA.parse_month(pf)
    y1, m1 = DA.parse_month(pt)
    n = (y1 - y0) * 12 + (m1 - m0) + 1
    out = []
    for i in range(n):
        yy, mm = y0 + (m0 - 1 + i) // 12, (m0 - 1 + i) % 12 + 1
        out.extend(DA.month_spellings(yy, mm))
    return out


rnd = random.Random(925)
bad = []
for _ in range(300):
    y0, m0 = rnd.randint(2020, 2030), rnd.randint(1, 12)
    k = rnd.randint(0, 35)
    y1, m1 = y0 + (m0 - 1 + k) // 12, (m0 - 1 + k) % 12 + 1
    a = rnd.choice([f"{y0}-{m0:02d}", f"{DA._calendar.month_name[m0]} {y0}"])
    b = rnd.choice([f"{y1}-{m1:02d}", f"{DA._calendar.month_name[m1]} {y1}"])
    if DA.period_range_variants(a, b) != retired_variants(a, b):
        bad.append((a, b))
check("A5 discrepancy appeals' period filter == its retired inline loop (300 ranges, both spellings)", not bad, bad[:3])

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("B. every month of the range == that month viewed alone")
rng = run(R.get_commissions_range("2026-06", "2026-08", authorization="", org_id=ORG))
check("B1 the months are June, July, August 2026", rng["months"] == ["June 2026", "July 2026", "August 2026"], rng["months"])
for m in rng["months"]:
    alone = run(R.get_commissions(m, authorization="", org_id=ORG))
    mine = [{k: v for k, v in r.items() if k != "range_month"} for r in rng["rows"] if r["range_month"] == m]
    check("B2 %s: range rows == get_commissions('%s') — JSON-identical (%d rows)" % (m, m, len(alone)),
          json.dumps(mine, sort_keys=True, default=str) == json.dumps(alone, sort_keys=True, default=str))
    mt = next(t for t in rng["month_totals"] if t["period"] == m)
    check("B3 %s subtotal payout = Σ that month's rows ($%.2f), final = Σ final ($%.2f)" % (m, mt["total_payout"], mt["final_payout"]),
          mt["total_payout"] == round(sum(r["total_payout"] for r in alone), 2)
          and mt["final_payout"] == round(sum(r["final_payout"] for r in alone), 2))
check("B4 June (no rows) is present and empty", next(t for t in rng["month_totals"] if t["period"] == "June 2026")["reps"] == 0)
check("B5 the August row stored as '2026-08' is read (the per-month read's own spelling rule)",
      any(r["range_month"] == "August 2026" and r["epay_salesperson"] == "Rep A" for r in rng["rows"]))
check("B6 deductions ride along exactly: Rep B July final = $295.00, Rep C August final = $115.05",
      [round(r["final_payout"], 2) for r in rng["rows"] if (r["epay_salesperson"], r["range_month"]) in
       (("Rep B", "July 2026"), ("Rep C", "August 2026"))] == [295.00, 115.05])
g = rng["grand_total"]
check("B7 grand total = Σ month totals ($%.2f payout, $%.2f final)" % (g["total_payout"], g["final_payout"]),
      g["total_payout"] == round(sum(t["total_payout"] for t in rng["month_totals"]), 2) == 1525.10
      and g["final_payout"] == 1490.05)
check("B8 per-rep totals across months (Rep A: 2 months, $1,080.00)",
      next(t for t in rng["rep_totals"] if t["rep"] == "Rep A")["total_payout"] == 1080.0
      and next(t for t in rng["rep_totals"] if t["rep"] == "Rep A")["months"] == 2)
check("B9 org-scoped: another org's row never appears", not any(r.get("epay_salesperson") == "Rep X" for r in rng["rows"]))
check("B10 counts sum too (PA 74 / BA 21 / UA 115)", (g["premium_acts"], g["byod_acts"], g["upgrade_acts"]) == (74, 21, 115))

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("C. the cap and the refusals")
ok12 = run(R.get_commissions_range("2025-09", "2026-08", authorization="", org_id=ORG))
check("C1 12 months pass (the cap, rep_incentive_range.MAX_MONTHS = 12)", len(ok12["months"]) == 12 == RIR.MAX_MONTHS)
for (a, b), why in ((("2025-08", "2026-08"), "13 months"), (("2026-08", "2026-06"), "a reversed range"), (("x", "2026-06"), "a junk month")):
    try:
        run(R.get_commissions_range(a, b, authorization="", org_id=ORG))
        check("C2 %s is refused" % why, False)
    except R.HTTPException as e:
        check("C2 %s is refused 400 — %s" % (why, e.detail), e.status_code == 400)

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("D. structure: one per-month path, one enumeration, the assembler only sums")
SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "modules", "commcalc", "router.py"), encoding="utf-8").read()


def fn_body(src, name):
    m = re.search(r"^(?:async\s+)?def\s+%s\s*\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    rest = src[m.start():]
    nxt = re.search(r"^(?:@router\.|def |class |async def )", rest[m.end() - m.start():], re.M)
    return rest[: (m.end() - m.start()) + nxt.start()] if nxt else rest


def structure_ok(src):
    body = fn_body(src, "get_commissions_range")
    loop = re.search(r"for m in months:\s*\n\s*per_month\[m\] = await get_commissions\(m,", body)
    return bool(body) and "_pd.month_range(" in body and bool(loop) and ".table(" not in body


check("D1 the endpoint enumerates with month_range and awaits get_commissions(month) per month, no table read of its own",
      structure_ok(SRC))
check("D2 NEGATIVE: an endpoint that reads rep_commissions itself → RED",
      not structure_ok(SRC.replace("per_month[m] = await get_commissions(m,", "per_month[m] = client.table('rep_commissions').select(m,")))
check("D3 NEGATIVE: an endpoint with its own month loop → RED",
      not structure_ok(SRC.replace("_pd.month_range(", "my_months(")))
asrc = open(RIR.__file__, encoding="utf-8").read()
_code = "\n".join(ln.split("#", 1)[0] for ln in re.sub(r'"""[\s\S]*?"""', "", asrc).split("\n"))
check("D4 the assembler multiplies / divides nothing (sums and rounding only)",
      not re.search(r"[^*]\*[^*=]|/(?!/)", _code.replace("**", "")), [ln for ln in _code.split("\n") if re.search(r"[^*]\*[^*=]|/", ln)][:3])
check("D5 the assembler is pure (no client / table / import of the router)",
      ".table(" not in _code and "import" not in _code.replace("__future__", ""))

print("\n" + "=" * 100)
print("%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
