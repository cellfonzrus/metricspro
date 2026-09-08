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
  E. voids and returns stay excluded, and the split always ties back to the totals;
  F. a DATE RANGE spans every month it touches instead of being clipped to the period dropdown
     (owner 2026-09-07: From 06/01/2026 To 08/01/2026 returned "0 store(s)" because the range and the
     selected month intersected down to a single day), and with no range the single-period read is
     unchanged;
  G. TAXABLE and NON-TAXABLE sales are carried as separate numbers at every grain — store, day and
     tender bucket — and always sum back to total sales (owner 2026-09-07: "need to segregate the
     sales from taxable and non taxable sales");
  H. the note distinguishes "we read no sales at all in this window" from "we read sales and none
     carried tax" — the first used to accuse the operator of a bad upload.

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


READS = []          # every (org, period) the endpoint actually asked the union for


def run(rows, period="August 2026", by_period=None, **kw):
    """Drive the real endpoint. `rows` is the unified sales set for EVERY period; pass `by_period`
    ({period: [rows]}) instead when a test needs different months to hold different data — the range
    wrapper calls the union once per month it reads, and READS records which."""
    del READS[:]

    def _union(_client, _org, p, **_k):
        READS.append(p)
        if by_period is not None:
            return list(by_period.get(p, [])), {}
        return rows, {}

    R._sales_rows_union_txn = _union
    R._store_market_resolver = lambda *_a, **_k: ((lambda s: "M1"), ["M1"])
    R.require_org = lambda *_a, **_k: None
    R.sb = lambda: None
    return R.tax_collected(period, org_id=ORG, **kw)


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
print("F. A DATE RANGE SPANS MONTHS — it does not get clipped to the period dropdown")
print("=" * 78)
# OWNER 2026-09-07, with the screen: From 06/01/2026 To 08/01/2026 returned
#   "0 store(s)" + "No tax captured for this period yet — re-send a Sales Transaction Details file…"
# The read was `.in_('period', _pvariants(period))` for the ONE selected month, and start/end then
# filtered WITHIN it. With the selector on August, June..Aug-1 intersected down to a single day. The
# note then blamed the operator's upload for what was a windowing bug.
check("F1 no bounds -> no span, the single-period read is untouched",
      R._periods_spanning("", "", "August 2026") == [])
check("F2 both bounds -> every month the range touches, oldest first",
      R._periods_spanning("2026-06-01", "2026-08-01", "August 2026")
      == ["June 2026", "July 2026", "August 2026"])
check("F3 a range inside one month is that one month",
      R._periods_spanning("2026-08-03", "2026-08-09", "August 2026") == ["August 2026"])
check("F4 it crosses a year boundary",
      R._periods_spanning("2025-11-20", "2026-02-02", "January 2026")
      == ["November 2025", "December 2025", "January 2026", "February 2026"])
check("F5 reversed bounds are still read in order, never as an empty span",
      R._periods_spanning("2026-08-01", "2026-06-01", "August 2026")
      == ["June 2026", "July 2026", "August 2026"])
check("F6 ONE bound spans between it and the selected period",
      R._periods_spanning("2026-06-15", "", "August 2026") == ["June 2026", "July 2026", "August 2026"]
      and R._periods_spanning("", "2026-08-01", "June 2026") == ["June 2026", "July 2026", "August 2026"])
check("F7 an unparseable bound falls back to the period, never to a wrong month",
      R._periods_spanning("not-a-date", "", "August 2026") == [])
check("F8 a very wide range is capped at 24 months rather than reading forever",
      len(R._periods_spanning("2000-01-01", "2026-08-01", "August 2026")) == 24)
check("F8b the cap keeps the MOST RECENT months, and cap=None gives the true span",
      R._periods_spanning("2000-01-01", "2026-08-01", "August 2026")[-1] == "August 2026"
      and len(R._periods_spanning("2000-01-01", "2026-08-01", "August 2026", cap=None)) == 320)
check("F8c a span of exactly 24 months is NOT reported as truncated",
      len(R._periods_spanning("2024-09-01", "2026-08-01", "August 2026")) == 24)

JUNE = [line(1000.0, 80.0, day="2026-06-10")]
JULY = [line(2000.0, 160.0, day="2026-07-10")]
AUG1 = [line(500.0, 0.0, day="2026-08-01")]        # Aug 1 exists but carried no tax
BY = {"June 2026": JUNE, "July 2026": JULY, "August 2026": AUG1}

r = run(None, period="August 2026", by_period=BY, start="2026-06-01", end="2026-08-01")
check("F9 all three months are read", READS == ["June 2026", "July 2026", "August 2026"], READS)
check("F10 the owner's range now returns stores instead of '0 store(s)'", len(r["stores"]) == 1,
      r["stores"])
check("F11 and the money is the whole range, not one month",
      r["totals"]["tax"] == 240.0 and r["totals"]["revenue"] == 3500.0, r["totals"])
check("F12 the note is gone, because there IS tax", r["note"] is None, r["note"])
check("F13 the response names the window it actually read",
      r["window"] == "2026-06-01 to 2026-08-01"
      and r["periods_read"] == ["June 2026", "July 2026", "August 2026"], r.get("window"))

# REGRESSION: the pre-fix behaviour, reproduced by reading only the selected period.
r_old = run(None, period="August 2026", by_period={"August 2026": AUG1})
check("F14 PRE-FIX: reading only August gave 500.00 of sales and $0.00 of tax — the '0 store(s)' screen",
      r_old["totals"]["tax"] == 0.0 and r_old["totals"]["revenue"] == 500.0, r_old["totals"])

r_none = run(None, period="August 2026", by_period=BY)
check("F15 with NO range the endpoint still reads exactly one period (byte-identical path)",
      READS == ["August 2026"], READS)
r_wide = run([], period="August 2026", start="2000-01-01", end="2026-08-01")
check("F16 an over-wide range is truncated AND says so, never silently half-read",
      "more than 24 months" in (r_wide["note"] or "") and len(r_wide["periods_read"]) == 24,
      r_wide["note"])
r_24 = run([line(100.0, 8.0, day="2026-08-05")], period="August 2026",
           start="2024-09-01", end="2026-08-01")
check("F17 an exactly-24-month range is NOT flagged as truncated",
      "more than 24 months" not in (r_24["note"] or ""), r_24["note"])

print()
print("=" * 78)
print("G. TAXABLE AND NON-TAXABLE SALES ARE SEGREGATED AT EVERY GRAIN")
print("=" * 78)
# OWNER 2026-09-07: "also need to segregate the sales from taxable and non taxable sales".
MIX = [line(100.0, 8.0, tender="Cash", day="2026-08-05"),          # taxable, cash
       line(400.0, 0.0, tender="Cash", day="2026-08-05"),          # NOT taxable, cash
       line(200.0, 16.0, tender="Credit Card", day="2026-08-06"),  # taxable, card
       line(300.0, 0.0, tender="Acima", day="2026-08-06")]         # NOT taxable, financing
r = run(MIX)
st = r["stores"][0]
check("G1 the store row carries non-taxable as its own number",
      st["untaxed_revenue"] == 700.0, st.get("untaxed_revenue"))
check("G2 taxable + non-taxable == total sales, exactly",
      round(st["taxable_revenue"] + st["untaxed_revenue"], 2) == st["revenue"],
      (st["taxable_revenue"], st["untaxed_revenue"], st["revenue"]))
check("G3 the same holds on the totals",
      round(r["totals"]["taxable_revenue"] + r["totals"]["untaxed_revenue"], 2)
      == r["totals"]["revenue"], r["totals"])
days = {d["date"]: d for d in st["days"]}
check("G4 and per DAY, in the drill-down",
      days["2026-08-05"]["taxable_revenue"] == 100.0
      and days["2026-08-05"]["untaxed_revenue"] == 400.0
      and days["2026-08-06"]["taxable_revenue"] == 200.0
      and days["2026-08-06"]["untaxed_revenue"] == 300.0, days)
check("G5 every day still ties: taxable + non-taxable == that day's revenue",
      all(round(d["taxable_revenue"] + d["untaxed_revenue"], 2) == d["revenue"] for d in st["days"]))
tn = r["totals"]["tender"]
check("G6 and per TENDER BUCKET — cash was 100 taxable / 400 not",
      tn["cash"]["taxable_revenue"] == 100.0 and tn["cash"]["untaxed_revenue"] == 400.0, tn["cash"])
check("G7 financing was entirely non-taxable",
      tn["financing"]["taxable_revenue"] == 0.0 and tn["financing"]["untaxed_revenue"] == 300.0,
      tn["financing"])
check("G8 every bucket ties: taxable + non-taxable == that bucket's sales",
      all(round(v["taxable_revenue"] + v["untaxed_revenue"], 2) == v["sales"] for v in tn.values()), tn)
check("G9 the rate still divides by TAXABLE only, unchanged by the new column",
      r["totals"]["effective_rate"] == 8.0, r["totals"]["effective_rate"])

print()
print("=" * 78)
print("H. THE NOTE NAMES WHAT ACTUALLY HAPPENED")
print("=" * 78)
# It used to say "no tax captured for this period — re-send a file with the Tax column" for BOTH
# "we read sales and none carried tax" AND "we read no sales at all", so an emptied window accused
# the operator of a bad upload. Those are different problems and now read differently.
r_empty = run([], start="2026-06-01", end="2026-08-01")
check("H1 an empty window says so, and does NOT blame the upload",
      "No sales rows at all" in (r_empty["note"] or "")
      and "Tax column" not in (r_empty["note"] or ""), r_empty["note"])
check("H2 it names the window and the months it read",
      "2026-06-01 to 2026-08-01" in r_empty["note"] and "June 2026" in r_empty["note"], r_empty["note"])
check("H3 it says plainly that nothing was filtered out",
      "Nothing was filtered out" in r_empty["note"] and r_empty["rows_in_window"] == 0, r_empty["note"])
r_notax = run([line(500.0, 0.0)])
check("H4 rows present but no tax anywhere STILL points at the Tax column",
      "Tax column" in (r_notax["note"] or "") and "migration 105" in (r_notax["note"] or ""),
      r_notax["note"])
check("H5 and it says how many rows it did find, so the two cases are distinguishable",
      "1 sales row(s)" in r_notax["note"] and r_notax["rows_in_window"] == 1, r_notax["note"])
r_ok = run([line(100.0, 8.0)])
check("H6 a healthy report carries no note at all", r_ok["note"] is None, r_ok["note"])
check("H7 a void/return row does not count toward rows_in_window",
      run([line(100.0, 8.0), line(50.0, 4.0, voided="true"),
           line(50.0, 4.0, trans_type="Return")])["rows_in_window"] == 1)

print()
print("=" * 78)
print(f"RESULT: {P} passed, {F} failed")
print("=" * 78)
sys.exit(1 if F else 0)
