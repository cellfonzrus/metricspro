"""Proof harness — THE ONE CLAWBACK TEST (owner ask 2026-10-06).

Stdlib-only, DB-free. Proves backend/app/modules/commcalc/clawback.py:
  1. the sign rule is DEREFERENCED, not restated — a feed's own convention decides the direction,
     and both registered feeds behave per their recorded rule;
  2. a debit is a clawback only when the ORG declares its type in a pay category;
  3. the phantom `Chargeback` category is excluded from the pay set (the defect this module
     replaces: a category nobody can declare);
  4. an undeclared debit is NAMED, never guessed and never dropped;
  5. the REGRESSION that reproduces the reported defect: the live house shape — a
     'Commission Withholding' debit declared as 'Commission' — was invisible to the old
     `category == 'Chargeback'` test and IS a clawback under this one;
  6. declaration_findings separates undeclared / declared-as-earnings / outside-pay and says so.

Run:  python backend/harness_clawback.py   → all checks must print OK.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from app.modules.commcalc import clawback as cb                      # noqa: E402
from app.modules.commcalc.processor_ledger import FEED_SHAPES        # noqa: E402

FAILURES = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILURES.append(name)


# The org's OWN declaration map. No carrier/tenant name is needed to exercise any of this; these
# are stand-in type strings, and the live-shape regression below uses the real one deliberately.
ORG_MAP = {
    "TypeA": "Commission",
    "TypeB": "Re-imbursement",
    "TypeC": "MDF",
    "DeviceCharge": "Equipment",       # declared, but outside the pay categories
}
PAY = cb.pay_categories()


# ── 1. the sign rule is the FEED's, dereferenced ────────────────────────────────────────────────
check("both registered feeds are reachable through feed_shape",
      cb.feed_shape("epay") is FEED_SHAPES["epay"]
      and cb.feed_shape("vidapay") is FEED_SHAPES["vidapay"])
check("an unregistered feed debits nothing rather than guessing a convention",
      cb.debit_of({"amount": -50.0}, "no_such_feed") == 0.0
      and cb.feed_shape("no_such_feed") is None)

# epay: credit_positive True  → negative is the debit.
check("epay: a negative amount is the debit, reported as a positive magnitude",
      cb.debit_of({"amount": -11.0}, "epay") == 11.0)
check("epay: a positive amount is a credit, so no debit",
      cb.debit_of({"amount": 240.0}, "epay") == 0.0)
# vidapay: credit_positive False → positive is the debit. Opposite sign, same code path.
check("vidapay: a POSITIVE retail_cost is the debit (the feed's own opposite convention)",
      cb.debit_of({"retail_cost": 325.0}, "vidapay") == 325.0)
check("vidapay: a negative retail_cost is a credit, so no debit",
      cb.debit_of({"retail_cost": -325.0}, "vidapay") == 0.0)
check("a zero amount is neither",
      cb.debit_of({"amount": 0}, "epay") == 0.0 and cb.debit_of({"amount": None}, "epay") == 0.0)
check("the type comes from the feed's registered column, not a literal",
      cb.row_type({"payment_type": " TypeA "}, "epay") == "TypeA"
      and cb.row_type({"order_type": "Void"}, "vidapay") == "Void")

# ── 2. a debit is a clawback only when the org declares the type as PAY ─────────────────────────
c = cb.classify_row({"payment_type": "TypeA", "amount": -11.0}, ORG_MAP)
check("a pay-declared debit IS a clawback, amount positive",
      c["clawback"] is True and c["amount"] == 11.0 and c["category"] == "Commission")
check("a CREDIT on a pay-declared type is never a clawback",
      cb.is_clawback({"payment_type": "TypeA", "amount": 11.0}, ORG_MAP) is False)
c = cb.classify_row({"payment_type": "DeviceCharge", "amount": -900.0}, ORG_MAP)
check("a debit the org declares OUTSIDE pay is excluded, with its reason",
      c["clawback"] is False and c["skip_reason"] == cb.SKIP_NOT_PAY and c["amount"] == 900.0)

# ── 3. the phantom category is gone from the pay set ────────────────────────────────────────────
check("the pay set carries no 'Chargeback' — the category nobody can declare",
      not any(str(x).strip().lower() == "chargeback" for x in PAY))
check("the pay set is otherwise the pay engine's own list, dereferenced",
      set(PAY) == {c for c in __import__(
          "app.modules.commcalc.pay_data_quality", fromlist=["x"]
      ).PLACEABLE_CATEGORIES if str(c).strip().lower() != "chargeback"})
check("a debit declared as the phantom category is NOT counted as a clawback",
      cb.is_clawback({"payment_type": "Z", "amount": -5.0}, {"Z": "Chargeback"}) is False)

# ── 4. an undeclared debit is named, not guessed ────────────────────────────────────────────────
for undeclared in ({}, {"X": ""}, {"X": "Unknown"}, {"X": None}):
    c = cb.classify_row({"payment_type": "X", "amount": -7.5}, undeclared)
    check("an undeclared debit (%r) is reported, never guessed" % (undeclared.get("X"),),
          c["clawback"] is False and c["skip_reason"] == cb.SKIP_UNDECLARED
          and c["amount"] == 7.5 and c["category"] is None)
check("every skip reason carries a human note",
      set(cb.SKIP_REASONS) == {cb.SKIP_UNDECLARED, cb.SKIP_NOT_PAY}
      and all(len(v) > 40 for v in cb.SKIP_REASONS.values()))
check("a callable category map works as well as a dict (the caller's own resolver)",
      cb.is_clawback({"payment_type": "TypeA", "amount": -1.0},
                     lambda t: ORG_MAP.get(t)) is True)

# ── 5. THE REGRESSION — the live house shape the old test could not see ─────────────────────────
# Measured live 2026-10-06: commcalc.payment_categories declares 'Commission Withholding' as
# 'Commission' (NOT 'Chargeback', which no org has ever been able to declare), and
# raw_payment_detail carries 474 such rows, every one a negative amount keyed by imei with no mdn.
LIVE_ROW = {"payment_type": "Commission Withholding", "amount": -11.0,
            "imei": "352700326611885", "mdn": "", "payment_date": "2026-04-24"}
LIVE_DECLARATION = {"Commission Withholding": "Commission"}

check("REGRESSION: the OLD test (category == 'Chargeback') misses the live row entirely",
      LIVE_DECLARATION["Commission Withholding"] != "Chargeback")
check("REGRESSION: the live row IS a clawback under the one test",
      cb.is_clawback(LIVE_ROW, LIVE_DECLARATION) is True
      and cb.classify_row(LIVE_ROW, LIVE_DECLARATION)["amount"] == 11.0)
check("REGRESSION: a feed that RENAMES its withholding line next quarter is still caught",
      cb.is_clawback({"payment_type": "2026 Q4 Commission Recovery", "amount": -11.0},
                     {"2026 Q4 Commission Recovery": "Commission"}) is True)

# ── 6. declaration_findings — the evidence-first half ───────────────────────────────────────────
ROWS = [
    {"payment_type": "Commission Withholding", "amount": -11.0},
    {"payment_type": "Commission Withholding", "amount": -4.0},
    {"payment_type": "Commission Withholding", "amount": 99.0},    # a credit; not a finding
    {"payment_type": "Mystery Recovery", "amount": -60.0},          # undeclared
    {"payment_type": "DeviceCharge", "amount": -900.0},             # outside pay
    {"payment_type": "TypeA", "amount": 500.0},                     # a credit
]
DEC = dict(ORG_MAP)
DEC["Commission Withholding"] = "Commission"
d = cb.declaration_findings(ROWS, DEC)
check("declared-as-earnings clawbacks are folded per type with their total",
      d["as_earnings"] == [{"type": "Commission Withholding", "category": "Commission",
                            "rows": 2, "amount": 15.0}])
check("an undeclared debit lands in its own bucket",
      d["undeclared"] == [{"type": "Mystery Recovery", "category": None,
                           "rows": 1, "amount": 60.0}])
check("a debit declared outside pay lands in its own bucket",
      d["outside_pay"] == [{"type": "DeviceCharge", "category": "Equipment",
                            "rows": 1, "amount": 900.0}])
check("credits are in no finding bucket at all",
      sum(x["rows"] for x in d["as_earnings"] + d["undeclared"] + d["outside_pay"]) == 4)
check("the as-earnings note is stated when there is money in it, and withheld when there is not",
      d["as_earnings_note"] and "moves money" in d["as_earnings_note"]
      and cb.declaration_findings([], DEC)["as_earnings_note"] is None)
check("empty input yields empty buckets, not a fabricated zero finding",
      cb.declaration_findings([], DEC)["as_earnings"] == []
      and cb.declaration_findings(None, DEC)["undeclared"] == [])
check("buckets are ordered by money at stake, worst first",
      [x["type"] for x in cb.declaration_findings(
          [{"payment_type": "TypeA", "amount": -5.0},
           {"payment_type": "TypeB", "amount": -50.0}], ORG_MAP)["as_earnings"]]
      == ["TypeB", "TypeA"])

# RULE TWO: no payment-type, carrier or tenant literal may drive BEHAVIOR. The module docstring
# documents the live defect by name on purpose (that is evidence, not behavior), so the check runs
# over the module's CODE with every docstring and comment stripped by the parser, not by hand.
import ast as _ast                                                    # noqa: E402

_SRC = open(os.path.join(os.path.dirname(__file__),
                         "app/modules/commcalc/clawback.py")).read()
_TREE = _ast.parse(_SRC)
for _n in _ast.walk(_TREE):
    if isinstance(_n, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
        _b = getattr(_n, "body", None)
        if _b and isinstance(_b[0], _ast.Expr) and isinstance(_b[0].value, _ast.Constant) \
                and isinstance(_b[0].value.value, str):
            _b[0].value.value = ""
_CODE_STRINGS = [n.value for n in _ast.walk(_TREE)
                 if isinstance(n, _ast.Constant) and isinstance(n.value, str)]
check("RULE TWO: no payment-type or carrier literal is a string constant in the module's code",
      not any("withhold" in s.lower() or "boost" in s.lower() or "verizon" in s.lower()
              or "total by" in s.lower() for s in _CODE_STRINGS))
check("the only category literal in the code is the phantom one it exists to exclude",
      [s for s in _CODE_STRINGS if "chargeback" in s.lower()] == ["chargeback"])

print()
if FAILURES:
    print("%d FAILURE(S):" % len(FAILURES))
    for f in FAILURES:
        print("  - " + f)
    sys.exit(1)
print("ALL CHECKS PASSED")
