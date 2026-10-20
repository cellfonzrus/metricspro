#!/usr/bin/env python3
"""
PROOF HARNESS — THE MONTH-OVER-MONTH P&L, AND THE EXPENSE THAT WAS NOT THERE
(index §COMMCALC-03.28; owner asks 2026-10-16)
============================================================================

Owner, verbatim: *"i also need to see the month over omont h p&l for each store in the same report
with a filter which gives the p&l on the righ tside for a particuklar monmth selected or a data range
selected"* and *"also expenses only appear int eh sept statementr i was check ing apr thre are not
expenses showing up fir any store"*.

The PURE rules are proved in `harness_prospect_eval.py` (§Y, §Z — 380 checks). THIS harness runs the
ENDPOINT, over a fake supabase client, because the defect the owner reported was not in a rule: it
was in which months the endpoint could SEE. The fixture is the live shape that produced it —

    our own `store_expenses`      : two months, JULY and AUGUST 2026 (nothing earlier, ever)
    the prospect's own uploads    : APRIL and MAY 2026

— so every month on screen sits BEFORE our first expense sheet, which is precisely the case that
used to charge no expense to any store and say nothing about it.

No network, no database: a fake client serves rows out of a dict and raises on every write verb.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


# ══ FAKE CLIENT ═══════════════════════════════════════════════════════════════════════════════════
QUERY_LOG = []


class WriteAttempted(AssertionError):
    pass


class _Q:
    def __init__(self, store, schema, table):
        self._store, self._schema, self._table = store, schema, table
        self._eq, self._in = {}, {}
        self._range, self._limit = None, None

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self._eq[k] = v
        return self

    def in_(self, k, v):
        self._in[k] = list(v)
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def insert(self, *a, **k):
        raise WriteAttempted(f"insert attempted on {self._schema}.{self._table}")

    def upsert(self, *a, **k):
        raise WriteAttempted(f"upsert attempted on {self._schema}.{self._table}")

    def update(self, *a, **k):
        raise WriteAttempted(f"update attempted on {self._schema}.{self._table}")

    def delete(self, *a, **k):
        raise WriteAttempted(f"delete attempted on {self._schema}.{self._table}")

    def execute(self):
        QUERY_LOG.append({"table": self._table, "schema": self._schema, "eq": dict(self._eq)})
        key = f"{self._schema}.{self._table}"
        if key not in self._store:
            raise RuntimeError(f"relation {key} does not exist")
        rows = []
        for r in self._store[key]:
            if all(str(r.get(k)) == str(v) for k, v in self._eq.items()) and \
               all(str(r.get(k)) in {str(x) for x in v} for k, v in self._in.items()):
                rows.append(dict(r))
        if self._range:
            a, b = self._range
            rows = rows[a:b + 1]
        if self._limit is not None:
            rows = rows[:self._limit]
        return type("Res", (), {"data": rows})()


class _Schema:
    def __init__(self, store, schema):
        self._store, self._schema = store, schema

    def table(self, t):
        return _Q(self._store, self._schema, t)

    def rpc(self, *a, **k):
        raise RuntimeError("rpc not available in this fixture")


class FakeClient:
    def __init__(self, store):
        self._store = store

    def schema(self, s):
        return _Schema(self._store, s)

    def table(self, t):
        return _Q(self._store, "public", t)

    def rpc(self, *a, **k):
        raise RuntimeError("rpc not available in this fixture")


# ══ THE FIXTURE — THE LIVE SHAPE THAT PRODUCED THE DEFECT ═════════════════════════════════════════
ORG = "00000000-0000-0000-0000-000000000001"
PID = "11111111-1111-1111-1111-111111111111"


def _comm(period, account, order, bin_, margin, store):
    return {"org_id": ORG, "prospect_id": PID, "feed_key": "ma_commission", "period": period,
            "row": {"merchant_account_id": account, "activation_order": order, "bin": bin_,
                    "device_margin": margin, "sku": "iPhone 13", "store_address": store}}


def _tx(period, account, discount):
    return {"org_id": ORG, "prospect_id": PID, "feed_key": "ma_daily_tx", "period": period,
            "row": {"account_id": account, "merchant_discount": discount, "retail_cost": 0.0,
                    "order_type": "x", "product_name": "y"}}


FEED_ROWS = [
    _comm("April 2026", "169335", "A1", "B1", -100.0, "1 First St"),
    _comm("April 2026", "169335", "A2", "B1", -100.0, "1 First St"),
    _comm("April 2026", "169336", "A3", "B9", -250.0, "2 Second St"),
    _comm("May 2026", "169335", "A4", "B2", -120.0, "1 First St"),
    _comm("May 2026", "169336", "A5", "B3", -130.0, "2 Second St"),
    _tx("April 2026", "169335", 30.0),
    _tx("May 2026", "169336", 15.0),
    # the handset sheet is a PRICE LIST: no month of its own, and read whole by every column
    {"org_id": ORG, "prospect_id": PID, "feed_key": "ma_marketplace_orders", "period": None,
     "row": {"product_name": "iPhone 13", "price": 529.0, "number_ordered": 2.0,
             "order_type": "Branded Handset"}},
]
# OUR OWN expense sheet: two months, BOTH AFTER every month the prospect carries.
EXPENSE_ROWS = [
    {"org_id": ORG, "store_code": "S1", "expense_name": "Rent / Lease", "amount": 4000,
     "period": "July 2026", "source_key": None, "expense_type": None},
    {"org_id": ORG, "store_code": "S2", "expense_name": "Rent / Lease", "amount": 4400,
     "period": "July 2026", "source_key": None, "expense_type": None},
    {"org_id": ORG, "store_code": "S1", "expense_name": "Employee Salaries", "amount": 4200,
     "period": "July 2026", "source_key": None, "expense_type": None},
    {"org_id": ORG, "store_code": "S1", "expense_name": "Rent / Lease", "amount": 4100,
     "period": "August 2026", "source_key": None, "expense_type": None},
    {"org_id": ORG, "store_code": "S2", "expense_name": "Rent / Lease", "amount": 4500,
     "period": "August 2026", "source_key": None, "expense_type": None},
    {"org_id": ORG, "store_code": "S1", "expense_name": "Employee Salaries", "amount": 4300,
     "period": "August 2026", "source_key": None, "expense_type": None},
    # ANOTHER ORG'S expenses, same months — they must never reach this evaluation
    {"org_id": "99999999-9999-9999-9999-999999999999", "store_code": "X1",
     "expense_name": "Rent / Lease", "amount": 99999, "period": "July 2026",
     "source_key": None, "expense_type": None},
]
STORE = {
    "commcalc.store_prospect": [{"org_id": ORG, "id": PID, "name": "Empire",
                                 "actual_rent_monthly": None, "expense_basis_months": None,
                                 "family_line_threshold": None, "exclusion_mode": None}],
    "commcalc.prospect_feed_row": FEED_ROWS,
    "commcalc.store_expenses": EXPENSE_ROWS,
    "commcalc.prospect_store_input": [],
    "commcalc.commission_org_config": [],
    "account.account_config": [],
    "commcalc.account_config": [],
    # The expense window's labour replacement (index §COMMCALC-23) reads the store-identity home and
    # the commission table. Present and EMPTY on purpose: both months here have their own expense
    # rows, so nothing is excluded from a carry, and an empty commission set adds nothing back — the
    # matrix below is byte-identical, while the real keying path is exercised rather than skipped.
    "commcalc.store_mapping": [],
    "commcalc.rep_commissions": [],
}

import app.modules.commcalc.router as R                                          # noqa: E402

R.sb = lambda: FakeClient(STORE)                                                 # noqa: E731
R.require_org = lambda *a, **k: None                                             # noqa: E731

print("=" * 98)
print("A. THE DEFECT, AT THE ENDPOINT — APRIL HAS AN EXPENSE AGAIN (owner report 2026-10-16)")
print("=" * 98)
OUT = R.prospect_pl_trend(PID, org_id=ORG)
_months = OUT["months"]
check("A1 every uploaded month is a column, oldest first",
      _months == ["April 2026", "May 2026"], str(_months))
_by = {b["period"]: b for b in OUT["by_month"]}
check("A2 EVERY month's expense side is MEASURED, although our own sheet starts three months later "
      "— the owner's report, reproduced and fixed at the endpoint",
      all(_by[m]["expenses"].get("measured") is True for m in _months),
      str({m: _by[m]["expenses"].get("measured") for m in _months}))
check("A3 …and every month charges a real expense, not a zero",
      all(round(_by[m]["totals"]["expenses"], 2) > 0 for m in _months),
      str({m: _by[m]["totals"]["expenses"] for m in _months}))
check("A4 the window it was averaged over is NAMED, and says the months fall AFTER the month shown",
      all(_by[m]["expenses"].get("direction") == "after" for m in _months)
      and all("AFTER" in _by[m]["expenses"].get("basis", "") for m in _months))
check("A5 EVERY STORE in every month carries it too — 'there are no expenses showing up for any "
      "store' was the words he used",
      all(round(st["totals"]["expenses"], 2) > 0
          for m in _months for st in _by[m]["by_store"].values()),
      str({m: {k: v["totals"]["expenses"] for k, v in _by[m]["by_store"].items()}
           for m in _months}))
check("A6 and there IS more than one store column to carry it",
      all(len(_by[m]["by_store"]) >= 2 for m in _months),
      str({m: sorted(_by[m]["by_store"]) for m in _months}))

print("    ARMED CONTROL — the PRE-FIX window rule, reinstated, must reproduce the defect:")
_real_window = R._pev.expense_window


def _old_rule(periods, period, want, today=None):
    """What the window was before §COMMCALC-03.28: the complete months STRICTLY BEFORE this one."""
    months = R._pev.complete_months(periods, period)[:max(1, int(want or 1))]
    return {"months": months, "before": months, "after": [], "requested": want,
            "direction": "before" if months else "none", "measured": bool(months),
            "basis": "the complete months before this one"}


R._pev.expense_window = _old_rule
try:
    _PRE = R.prospect_pl_trend(PID, org_id=ORG)
finally:
    R._pev.expense_window = _real_window
_pre = {b["period"]: b for b in _PRE["by_month"]}
check("    A7 the old rule finds NO month to average from either April or May — the control fires",
      all(_pre[m]["expenses"].get("measured") is False for m in _months))
check("    A8 …and every store in every month then reads a flat 0.00 expense, which is the screen "
      "the owner was looking at",
      all(round(_pre[m]["totals"]["expenses"], 2) == 0.0 for m in _months)
      and all(round(st["totals"]["expenses"], 2) == 0.0
              for m in _months for st in _pre[m]["by_store"].values()))
check("    A9 …and the bottom line was therefore OVERSTATED by a whole store's running cost in "
      "every column — which is why a silent zero is the dangerous half of this defect",
      all(_pre[m]["totals"]["net_income"] > _by[m]["totals"]["net_income"] for m in _months))

print("=" * 98)
print("B. A COLUMN PER MONTH IS THAT MONTH'S OWN REPORT, NOT A SECOND DERIVATION")
print("=" * 98)
_EVAL = R.prospect_evaluation(PID, period="April 2026", org_id=ORG)
_mode = OUT["mode"]
check("B1 the trend's April column and the April report agree on every P&L line, to the cent",
      R._pev.statement_line_amounts(_EVAL["statements"][_mode]) == _by["April 2026"]["lines"],
      str({k: (v, _by["April 2026"]["lines"].get(k))
           for k, v in R._pev.statement_line_amounts(_EVAL["statements"][_mode]).items()
           if v != _by["April 2026"]["lines"].get(k)}))
check("B2 …and on the totals, including the bottom line",
      _EVAL["statements"][_mode]["totals"] == _by["April 2026"]["totals"])
check("B3 …and on the activated-line count",
      _EVAL["scenarios"][_mode]["activations"] == _by["April 2026"]["activations"] == 3)
check("B4 …and on every STORE column's bottom line",
      {k: v["statements"][_mode]["totals"]["net_income"]
       for k, v in _EVAL["store_scenarios"].items()}
      == {k: v["totals"]["net_income"] for k, v in _by["April 2026"]["by_store"].items()})

print("=" * 98)
print("C. THE PANEL ON THE RIGHT — A MONTH, OR A DATE RANGE (owner ask 2026-10-16)")
print("=" * 98)
_ONE = R.prospect_pl_trend(PID, period="2026-04", org_id=ORG)
check("C1 a MONTH selects that month alone, and the panel is that month's statement",
      _ONE["selected_months"] == ["April 2026"]
      and _ONE["window"]["lines"] == _by["April 2026"]["lines"]
      and _ONE["window"]["expense_months"] == 1)
_RNG = R.prospect_pl_trend(PID, period="2026-04-14", period_to="2026-05-02", org_id=ORG)
check("C2 a DATE RANGE selects the whole months its two ends fall in",
      _RNG["selected_months"] == ["April 2026", "May 2026"]
      and _RNG["window"]["expense_months"] == 2)
check("C3 the range's revenue and device cost are the two columns added up, to the cent — it is "
      "the same rows booked once, never a sum of statements",
      round(_RNG["window"]["totals"]["revenue"], 2)
      == round(sum(_by[m]["totals"]["revenue"] for m in _months), 2)
      and round(_RNG["window"]["totals"]["cogs"], 2)
      == round(sum(_by[m]["totals"]["cogs"] for m in _months), 2),
      f'window {_RNG["window"]["totals"]["revenue"]} vs columns '
      f'{sum(_by[m]["totals"]["revenue"] for m in _months)}')
check("C4 …and it carries TWO months of running cost, because it carries two months of revenue",
      round(_RNG["window"]["totals"]["expenses"], 2)
      == round(sum(_by[m]["totals"]["expenses"] for m in _months), 2))
check("C5 the sentence saying so rides on the payload, so the screen never has to work it out",
      "2 months" in _RNG["window"]["expense_basis"]
      and "never the monthly columns added together" in _RNG["window_basis"])
check("C6 NO filter is not an empty filter: with nothing selected the panel is every month",
      OUT["selected_months"] == _months and OUT["window"]["expense_months"] == 2)
check("C7 the month-over-month columns are EVERY month whatever the filter says — a trend you can "
      "filter down to one column is not a trend",
      [b["period"] for b in _ONE["by_month"]] == _months
      and [b["period"] for b in _RNG["by_month"]] == _months)

print("=" * 98)
print("D. IT IS READ-ONLY, ORG-SCOPED, AND CANNOT SEE ANOTHER ORG'S MONEY")
print("=" * 98)
check("D1 not one write verb was reached (the fake client raises on all four)", True)
_qs = [q for q in QUERY_LOG if q["table"] in
       ("store_prospect", "prospect_feed_row", "store_expenses", "prospect_store_input")]
check("D2 every read of a prospect or expense table carries this org's id",
      _qs and all(str(q["eq"].get("org_id")) == ORG for q in _qs),
      str([q for q in _qs if str(q["eq"].get("org_id")) != ORG]))
check("D3 the other org's $99,999 rent never reaches a figure on this payload",
      all(round(st["totals"]["expenses"], 2) < 50000
          for m in _months for st in _by[m]["by_store"].values()))
def _refused(**kw):
    """True when the endpoint refuses the call rather than guessing."""
    try:
        R.prospect_pl_trend(PID, org_id=ORG, **kw)
        return False
    except Exception as e:
        return getattr(e, "status_code", None) == 400


check("D4 a flagged-account mode nobody declared is refused rather than guessed at",
      _refused(mode="whatever") and not _refused(mode="exclude"))
def _other():
    try:
        R.prospect_pl_trend(PID, org_id="99999999-9999-9999-9999-999999999999")
        return False
    except Exception as e:
        return getattr(e, "status_code", None) == 404


check("D5 the same prospect id read by ANOTHER org is a 404, never a payload", _other())

print("\n" + "=" * 98)
print(f"{len(PASS)} passed / {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1)
print("The month-over-month P&L is the month report, once per month — and a month before our own "
      "expense sheet begins is charged a measured benchmark, not a silent zero.")
