"""PROOF: the Tax Collected report's rate, its taxable base, and its tender split.

OWNER 2026-09-07: *"for tax collected the sales tax rate is not correct, the tax report should also
have the total sales from which the sales tax was collected, also it should show how much is on cash
sale and how much is on credit card and financing."*

THE DEFECT, with the live numbers that found it (house org, August 2026, 24,241 non-void non-return
sales lines):

    tax                                        $13,733.70
    ext_price of EVERY line (the old base)    $615,344.62   ->  "2.23%"   <- what the report showed
    ext_price of the lines that CARRIED tax   $164,751.76   ->   8.34%    <- the real rate

No jurisdiction charges 2.23%. **73.2% of the old base ($450,592.86) is not taxable merchandise at
all** — $377,746.92 of bill payments and $55,704.92 of device set-up fees, both taxed $0.00 by
construction. The rate was not miscalculated; it was divided by the wrong thing.

TAXABILITY IS READ FROM THE DATA (tax > 0 on the line), never from a list of department names in code.
That is RULE TWO, and it is the same lesson as mig 962: a hardcoded vocabulary describes ONE tenant's
POS and silently mis-reports for the next one.

WHAT THIS PINS
  A. the rate divides by taxable sales, and all three figures are reported so the reader can see the
     gap rather than infer it;
  B. taxability comes from the line's own tax, not a department list;
  C. the tender split buckets through the SHARED closing/_canon_tender (so this report and the 3-way
     tender recon cannot disagree about what "cash" means), incl. the traps that mapper exists for:
     'gift card' contains 'card', 'cash app' contains 'cash', 'external credit card' contains 'credit';
  D. a MULTI-TENDER line is reported as `mixed`, never assigned whole to one tender;
  E. voids and returns stay excluded, and the split always ties back to the totals.

PURE-ish: drives the real `tax_collected` over an in-memory fake. stdlib only.
"""
import os
import sys

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


import app.modules.commcalc.router as R                      # noqa: E402

ORG = "org-1"


def run(rows, **kw):
    """Drive the real endpoint with `rows` as the unified sales set."""
    R._sales_rows_union_txn = lambda *_a, **_k: (rows, {})
    R._store_market_resolver = lambda *_a, **_k: ((lambda s: "M1"), ["M1"])
    R.require_org = lambda *_a, **_k: None
    R.sb = lambda: None
    return R.tax_collected("August 2026", org_id=ORG, **kw)


def line(ext, tax, tender="Cash", store="S1", day="2026-08-05", **kw):
    r = {"trans_id": "t", "trans_date": day, "store": store, "ext_price": ext, "tax": tax,
         "tender_type": tender, "voided": None, "trans_type": "Sale"}
    r.update(kw)
    return r


print("=" * 78)
print("A. The rate divides by TAXABLE sales, and the gap is shown, not hidden")
print("=" * 78)
# The live shape in miniature: a big untaxed bill-payment line beside a taxed merchandise line.
out = run([line(1000.0, 0.0, "Cash"), line(100.0, 8.34, "Cash")])
t = out["totals"]
check("A1 total sales counts everything ($1,100)", t["revenue"] == 1100.0, str(t))
check("A2 taxable sales counts only the taxed line ($100)", t["taxable_revenue"] == 100.0, str(t))
check("A3 untaxed sales is reported explicitly ($1,000) — the reader sees WHY the two differ",
      t["untaxed_revenue"] == 1000.0, str(t))
check("A4 the rate is 8.34%, not 0.76% — tax over TAXABLE, not over everything "
      "(the live defect: $13,733.70 / $615,344.62 = 2.23%, a rate no jurisdiction charges)",
      t["effective_rate"] == 8.34, str(t["effective_rate"]))
check("A5 a store with NO taxable sales reports 0%, never a divide-by-zero",
      run([line(500.0, 0.0)])["totals"]["effective_rate"] == 0.0)
day = out["stores"][0]["days"][0]
check("A6 the per-DAY drill-down uses the same rule",
      day["revenue"] == 1100.0 and day["taxable_revenue"] == 100.0 and day["effective_rate"] == 8.34,
      str(day))

print()
print("=" * 78)
print("B. Taxability comes from the line's own tax — never a department name in code")
print("=" * 78)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "app", "modules", "commcalc", "router.py"), encoding="utf-8").read()
fn = src[src.index("def tax_collected("):]
fn = fn[:fn.index("\n@router.")]
# Scan the CODE, not the docstring: the docstring cites "bill payments" and "device set-up fees" as
# the live EVIDENCE that found this defect, which is exactly the sort of note that should stay. What
# must never appear is a department literal the logic BRANCHES on.
_body = fn
if '"""' in _body:
    _body = _body.split('"""', 2)[2] if _body.count('"""') >= 2 else _body
check("B1 the endpoint's CODE names no department/product vocabulary to decide taxability (RULE TWO — "
      "mig 962 exists because one tenant's vocabulary was baked into a metric)",
      not any(w in _body.lower() for w in ("bill payment", "dev. charges", "'rtr'", "set-up fee",
                                           "department ==", "department in")),
      "a department literal leaked into the taxability decision")
# A line in ANY department is taxable if it carried tax — proven by giving the same department both.
out = run([line(100.0, 8.0, department="anything"), line(900.0, 0.0, department="anything")])
check("B2 two lines in the SAME department split correctly by whether they carried tax",
      out["totals"]["taxable_revenue"] == 100.0, str(out["totals"]))

print()
print("=" * 78)
print("C. Tender split rides the SHARED canonical mapper")
print("=" * 78)
out = run([line(100.0, 8.0, "Cash"), line(200.0, 16.0, "Credit Card"),
           line(300.0, 24.0, "Externel Credit Card"), line(400.0, 32.0, "Financing"),
           line(50.0, 4.0, "Gift Card"), line(60.0, 5.0, "Zelle")])
tn = out["totals"]["tender"]
check("C1 cash -> cash", tn["cash"]["sales"] == 100.0, str(tn["cash"]))
check("C2 credit card AND external credit card both -> card ($500)",
      tn["card"]["sales"] == 500.0, str(tn["card"]))
check("C3 financing -> financing ($400)", tn["financing"]["sales"] == 400.0, str(tn["financing"]))
check("C4 gift card -> other, NOT card ('gift card' contains 'card' — the trap the shared mapper "
      "exists for)", tn["other"]["sales"] == 110.0, str(tn["other"]))
check("C5 'Cash App' -> other, NOT cash (contains 'cash')",
      run([line(70.0, 5.0, "Cash App (PA)")])["totals"]["tender"]["cash"]["sales"] == 0.0)
check("C6 tax is split too, not just sales", tn["financing"]["tax"] == 32.0, str(tn["financing"]))

print()
print("=" * 78)
print("D. A multi-tender line is MIXED — never assigned whole to one tender")
print("=" * 78)
out = run([line(500.0, 40.0, "Cash; Externel Credit Card")])
tn = out["totals"]["tender"]
check("D1 it lands in `mixed`", tn["mixed"]["sales"] == 500.0, str(tn))
check("D2 …and NOT in cash or card — assigning the whole amount to one would overstate it by real "
      "money (233 such lines in the house org's August alone)",
      tn["cash"]["sales"] == 0.0 and tn["card"]["sales"] == 0.0, str(tn))
check("D3 a blank tender is `other`, not silently dropped",
      run([line(80.0, 6.0, "")])["totals"]["tender"]["other"]["sales"] == 80.0)

print()
print("=" * 78)
print("E. Voids and returns stay out, and the split always ties to the totals")
print("=" * 78)
out = run([line(100.0, 8.0, "Cash"), line(999.0, 99.0, "Cash", voided="true"),
           line(50.0, 4.0, "Cash", trans_type="Return")])
check("E1 a voided line is excluded", out["totals"]["revenue"] == 100.0, str(out["totals"]))
check("E2 a return is excluded", out["totals"]["tax"] == 8.0, str(out["totals"]))
out = run([line(100.0, 8.0, "Cash"), line(200.0, 0.0, "Credit Card"),
           line(300.0, 24.0, "Financing"), line(40.0, 3.0, "Cash; Credit Card")])
tn = out["totals"]["tender"]
check("E3 tender SALES sum to total sales — nothing is lost or double-counted",
      round(sum(v["sales"] for v in tn.values()), 2) == out["totals"]["revenue"], str(tn))
check("E4 tender TAX sums to total tax",
      round(sum(v["tax"] for v in tn.values()), 2) == out["totals"]["tax"], str(tn))
check("E5 tender TAXABLE sums to total taxable",
      round(sum(v["taxable_revenue"] for v in tn.values()), 2) == out["totals"]["taxable_revenue"],
      str(tn))
st = out["stores"][0]
check("E6 every store row carries the same four figures the totals do",
      all(k in st for k in ("revenue", "taxable_revenue", "effective_rate", "tender")), str(st.keys()))

print()
print("=" * 78)
print(f"RESULT: {P} passed, {F} failed")
print("=" * 78)
sys.exit(1 if F else 0)
