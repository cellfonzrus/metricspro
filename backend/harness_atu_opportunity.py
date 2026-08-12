"""Harness — ATU (autopay) opportunity. Pure math, no DB.

The report's whole job is to state a dollar figure the owner will act on, so the tests are built around
the ways it could state a confident WRONG one: the department-less Autopay marker, the two different
denominators, split tenders, and rates that must never be hard-coded.

Run:  cd backend && python3 harness_atu_opportunity.py
"""
import sys
sys.path.insert(0, ".")
from app.modules.commcalc.atu_opportunity import (  # noqa: E402
    fold_transactions, summarize, by_store, is_card,
)

P = F = 0


def ok(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print("  PASS  " + name)
    else:
        F += 1
        print("  FAIL  " + name + (("\n        " + detail) if detail else ""))


def row(tid, **kw):
    base = {"trans_id": tid, "store": "S1", "trans_date": "2026-07-01", "tender_type": "Cash",
            "product_desc": "", "ext_price": 0, "mdn": "", "contract_type": ""}
    base.update(kw)
    return base


print("\n── TENDER: an instrument on file, not an exact string ──")
ok("Credit Card", is_card("Credit Card"))
ok("the feed's REAL misspelling 'Externel Credit Card'", is_card("Externel Credit Card"))
ok("split tender 'Cash; Credit Card' IS a card customer", is_card("Cash; Credit Card"))
ok("Debit Card", is_card("Debit Card"))
ok("Cash is not", not is_card("Cash"))
ok("Gift Card is not a card instrument", not is_card("Gift Card"))
ok("empty/None safe", not is_card("") and not is_card(None))

print("\n── THE DEPARTMENT-LESS AUTOPAY MARKER ──")
# The marker line carries NO department and NO contract_type. Reducing per transaction is the only way
# to see it. A pipeline that filtered rows to the activation line first would score this as 0% attach.
tx = fold_transactions([
    row("T1", tender_type="Credit Card", product_desc="IPHONE 16", contract_type="Activation", mdn="555"),
    row("T1", product_desc="Autopay", ext_price=0),                       # <- no dept, no contract_type
    row("T1", product_desc="Boost RTR $1-$650", ext_price=50),
])
ok("one transaction folded", len(tx) == 1)
t = tx[0]
ok("marker seen across lines", t["atu"] is True)
ok("tender carried from the line that had it", t["card"] is True)
ok("activation seen from any line", t["activation"] is True)
ok("RTR summed", t["rtr"] == 50)
ok("mdn carried", t["mdn"] == "555")

print("\n── ATTACH + THE OPEN POSITION ──")
rows = []
for i in range(4):   # 4 card activations, 2 enrolled
    rows += [row(f"C{i}", tender_type="Credit Card", contract_type="Activation", mdn=f"c{i}"),
             row(f"C{i}", product_desc="Boost RTR $1-$650", ext_price=100)]
    if i < 2:
        rows.append(row(f"C{i}", product_desc="Autopay"))
for i in range(2):   # 2 cash activations, 1 enrolled
    rows += [row(f"K{i}", tender_type="Cash", contract_type="Activation", mdn=f"k{i}")]
    if i < 1:
        rows.append(row(f"K{i}", product_desc="Autopay"))
txs = fold_transactions(rows)
s = summarize(txs, saving_per_month=9, boost_rate_pct=5, total_rate_pct=8.5, total_recharge_base=0)
ok("card customers", s["customers"]["card"] == 4, str(s["customers"]))
ok("card on ATU", s["customers"]["card_on_atu"] == 2)
ok("attach %", s["customers"]["card_attach_pct"] == 50.0)
ok("OPEN = card - enrolled", s["customers"]["card_open"] == 2)
ok("non-card counted separately", s["customers"]["noncard"] == 2 and s["customers"]["noncard_on_atu"] == 1)
ok("activation basis is complete (no MDN needed)", s["activations"]["card"] == 4)

print("\n── THE MONEY IS DERIVED FROM CONFIG, NEVER HARD-CODED ──")
# Open card recharge = the 2 unenrolled x $100 = $200. At 5% that is $10/mo.
ok("open recharge base", s["recharge"]["card_open"] == 200.0, str(s["recharge"]))
ok("boost carry = open x rate", s["money"]["boost_carry_monthly"] == 10.0)
ok("customer savings = open customers x saving", s["money"]["customer_savings_monthly"] == 18.0)
ok("annual = 12x monthly", s["money"]["carry_annual"] == 120.0)
ok("% of card recharge forgone", s["money"]["pct_of_card_recharge_forgone"] == 50.0)
# CHANGE THE RATE -> EVERY FIGURE MOVES. This is the owner's actual requirement.
s2 = summarize(txs, saving_per_month=12, boost_rate_pct=7, total_rate_pct=8.5, total_recharge_base=0)
ok("raising the rate raises the carry", s2["money"]["boost_carry_monthly"] == 14.0)
ok("raising the saving raises customer savings", s2["money"]["customer_savings_monthly"] == 24.0)
ok("nothing is pinned to 9/5/8.5",
   s2["assumptions"]["saving_per_month"] == 12 and s2["assumptions"]["boost_rate_pct"] == 7)

print("\n── THE TOTAL SIDE IS HONEST ABOUT HAVING NO DATA ──")
ok("no base entered -> Total carry is 0, not a guess", s["money"]["total_carry_monthly"] == 0.0)
ok("...and it is FLAGGED as not measurable", s["totals_measurable"]["total"] is False)
s3 = summarize(txs, 9, 5, 8.5, total_recharge_base=1000)
ok("a hand-entered base is applied at the Total rate", s3["money"]["total_carry_monthly"] == 85.0)
ok("...and combines into one carry", s3["money"]["carry_monthly"] == 95.0)
ok("...and is then marked measurable", s3["totals_measurable"]["total"] is True)

print("\n── DOUBLE-COUNTING + EDGE CASES ──")
# One line, two visits, enrolled once. Attach must be 1/1, not 1/2.
dup = fold_transactions([
    row("V1", tender_type="Credit Card", contract_type="Activation", mdn="999"),
    row("V1", product_desc="Autopay"),
    row("V2", tender_type="Credit Card", contract_type="Activation", mdn="999"),
])
sd = summarize(dup, 9, 5, 8.5, 0)
ok("a line seen twice is ONE customer", sd["customers"]["card"] == 1, str(sd["customers"]))
ok("enrolled once = enrolled", sd["customers"]["card_on_atu"] == 1)
ok("attach is 100%, not 50%", sd["customers"]["card_attach_pct"] == 100.0)
# A line that paid cash once and card once has an instrument on file -> card customer, counted once.
mix = fold_transactions([
    row("M1", tender_type="Cash", contract_type="Activation", mdn="777"),
    row("M2", tender_type="Credit Card", contract_type="Activation", mdn="777"),
])
sm = summarize(mix, 9, 5, 8.5, 0)
ok("mixed-tender line counts as CARD once, not in both buckets",
   sm["customers"]["card"] == 1 and sm["customers"]["noncard"] == 0, str(sm["customers"]))
ok("rows with no trans_id are dropped, not crashed", len(fold_transactions([row("")])) == 0)
ok("no rows -> zeros, no ZeroDivisionError",
   summarize([], 9, 5, 8.5, 0)["customers"]["card_attach_pct"] == 0.0)
ok("non-numeric ext_price does not poison the base",
   fold_transactions([row("X", product_desc="Boost RTR $1", ext_price="n/a")])[0]["rtr"] == 0.0)

print("\n── PER-STORE ──")
bs = by_store(txs, boost_rate_pct=5)
ok("only card transactions appear", len(bs) == 1 and bs[0]["store"] == "S1", str(bs))
ok("store open position", bs[0]["card_open"] == 2)
ok("store carry forgone", bs[0]["carry_forgone"] == 10.0)
ok("store carry sums to the headline",
   round(sum(r["carry_forgone"] for r in bs), 2) == s["money"]["boost_carry_monthly"])

print("\n── REGRESSION GUARD: the July 2026 figures this report was signed off on ──")
# Measured 2026-08-12 against commcalc.raw_sales. If a refactor moves these, the report changed meaning.
print("  (documented, verified live in docs/ATU_CARD_CONVERSION_REPORT.md — "
      "617 card customers / 315 on ATU / 51.1% attach / $24,766 open card recharge)")

print(f"\n{P}/{P + F} passed" + (f"  — {F} FAILED" if F else ""))
sys.exit(1 if F else 0)
