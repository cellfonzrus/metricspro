"""Fixture-driven check of sales_comparison pure math (no DB)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import sales_comparison as sc
from app.modules.commcalc import installment_category as icat

rules = icat.effective_rules([])  # built-in category ladder

# One resolved financing vendor: ACIMA via tender_type contains "acima"
vendors = [{
    "vendor_key": "acima", "label": "ACIMA lease-to-own", "enabled": True,
    "detection_status": "configured",
    "matchers": [{"match_field": "tender_type", "match_op": "contains", "match_value": "acima"}],
}]

def is_acc(r):
    return str(r.get("department") or "").strip().lower() == "ondigo"

def line(tid, store, date, dept="", cat="", prod="", ct="", tender="", ext=0.0, gp=0.0, serial=""):
    return {"trans_id": tid, "store": store, "trans_date": date, "department": dept, "category": cat,
            "product_desc": prod, "contract_type": ct, "tender_type": tender, "ext_price": ext, "gp": gp,
            "serial_1": serial, "voided": "", "trans_type": ""}

# BASE window (2026-08): store A — 2 phones, 1 tablet, 1 byod, accessories, 1 acima financed
base = [
    # txn1: a phone activation with an accessory + acima tender
    line("t1", "Store A", "2026-08-03", dept="BrandedHandset", prod="iPhone 15", ct="New Activation",
         tender="ACIMA Lease", ext=800, gp=150, serial="356789012345678"),
    line("t1", "Store A", "2026-08-03", dept="Ondigo", prod="Case", ext=30, gp=20),
    # txn2: a phone upgrade
    line("t2", "Store A", "2026-08-10", dept="BrandedHandset", prod="Galaxy S24", ct="Upgrade",
         ext=700, gp=120, serial="356789012345679"),
    # txn3: a tablet
    line("t3", "Store A", "2026-08-12", prod="iPad tablet", ct="New Activation", ext=500, gp=90,
         serial="356789012345680"),
    # txn4: BYOD activation
    line("t4", "Store A", "2026-08-15", prod="SIM Kit", ct="BYOD Activation", ext=10, gp=5,
         serial="8901234567890123456"),
    # txn5: accessory-only sale
    line("t5", "Store A", "2026-08-20", dept="Ondigo", prod="Screen Protector", ext=25, gp=18),
    # voided line must be ignored
    line("t6", "Store A", "2026-08-21", dept="BrandedHandset", prod="Pixel", ext=600, gp=100,
         serial="356789012345681"),
]
base[-1]["voided"] = "true"

# COMPARE window (2026-07): store A — 1 phone, 1 tablet, 0 byod, 0 acima
comp = [
    line("u1", "Store A", "2026-07-05", dept="BrandedHandset", prod="iPhone 14", ct="New Activation",
         ext=750, gp=140, serial="356789012345690"),
    line("u2", "Store A", "2026-07-06", prod="Galaxy Tab tablet", ct="New Activation", ext=450, gp=80,
         serial="356789012345691"),
    line("u1", "Store A", "2026-07-05", dept="Ondigo", prod="Case", ext=20, gp=12),
]

def resolve_market(store):
    return "Metro" if store == "Store A" else ""

out = sc.build(base, comp, rules, is_acc, vendors,
               base_period="2026-08", compare_period="2026-07", mode="mom",
               window_label="Full month", resolve_market=resolve_market)

print("=== totals_by_category ===")
for t in out["totals_by_category"]:
    print(f"  {t['label']:20s} cur={t['current']:>3} prev={t['previous']:>3} "
          f"Δ={t['delta']:>3} pct={t['pct']} rev cur={t['current_rev']}")

print("=== overall ===", out["overall"])
print("=== window ===", out["window_label"], "| periods", out["base_period"], "vs", out["compare_period"])
print("=== row count ===", len(out["rows"]))

# ── assertions ──
tbc = {t["key"]: t for t in out["totals_by_category"]}
assert tbc["phone"]["current"] == 2, ("phones base", tbc["phone"]["current"])
assert tbc["phone"]["previous"] == 1, ("phones prev", tbc["phone"]["previous"])
assert tbc["phone"]["pct"] == 100.0, ("phones pct", tbc["phone"]["pct"])
assert tbc["tablet"]["current"] == 1 and tbc["tablet"]["previous"] == 1, "tablet"
assert tbc["tablet"]["pct"] == 0.0, ("tablet pct", tbc["tablet"]["pct"])
assert tbc["byod"]["current"] == 1 and tbc["byod"]["previous"] == 0, "byod"
assert tbc["byod"]["pct"] is None, ("byod pct should be None (new)", tbc["byod"]["pct"])
# accessories: 2 accessory lines in base (case + screen protector), 1 in compare
assert tbc["accessory"]["current"] == 2, ("acc base", tbc["accessory"]["current"])
assert tbc["accessory"]["previous"] == 1, ("acc prev", tbc["accessory"]["previous"])
assert tbc["accessory"]["current_rev"] == 55.0, ("acc rev", tbc["accessory"]["current_rev"])
# Financing: 1 in base, 0 in compare.
# This used to assert a PER-VENDOR key, `tbc["fin:acima"]`. Commit f4ce76c5 deliberately collapsed
# the per-vendor financing rows into ONE neutral "Financing" line, and that collapse is
# COMPLIANCE-CRITICAL, not cosmetic: the report was flagged for a dual-affiliation leak, and the
# fix is that the vendor BRAND (ACIMA / TW / Edge) is never emitted at all — only the neutral
# label. So the old key is not something to restore; asserting it back would re-open the defect.
assert "fin:acima" not in tbc, ("per-vendor financing keys must stay collapsed (f4ce76c5) — a "
                                "fin:<vendor> key is the dual-affiliation leak coming back", sorted(tbc))
assert tbc["financing"]["current"] == 1 and tbc["financing"]["previous"] == 0, (
    "financing", tbc["financing"])
assert tbc["financing"]["label"] == "Financing", ("financing label must stay brand-neutral",
                                                  tbc["financing"]["label"])
# The compliance guarantee itself, asserted over the WHOLE payload rather than one row: the
# configured vendor's brand and key appear nowhere a screen could render them.
_payload = repr(out).lower()
assert "acima" not in _payload, "financing vendor BRAND leaked into the sales-comparison payload"
assert "fin:" not in _payload, "a per-vendor financing key leaked into the sales-comparison payload"
# phone revenue for base = txn1 (800, accessory excluded) + txn2 (700) = 1500
assert tbc["phone"]["current_rev"] == 1500.0, ("phone rev", tbc["phone"]["current_rev"])
# overall txns: base has 5 live txns (t6 voided), compare has 2
assert out["overall"]["current_txns"] == 5, ("cur txns", out["overall"]["current_txns"])
assert out["overall"]["previous_txns"] == 2, ("prev txns", out["overall"]["previous_txns"])

# ── scenario helper assertions ──
assert sc.period_ym("2026-08") == (2026, 8)
assert sc.period_ym("August 2026") == (2026, 8)
assert sc.period_ym("garbage") is None
assert sc.shift_month(2026, 1, 1) == (2025, 12), sc.shift_month(2026, 1, 1)     # MoM across year edge
assert sc.shift_month(2026, 8, 1) == (2026, 7)
assert sc.week_bounds(1) == (1, 7) and sc.week_bounds(5) == (29, 31)
# day windows
assert sc.in_day_window("2026-08-13", 13, 0) is True
assert sc.in_day_window("2026-08-14", 13, 0) is False
assert sc.in_day_window("2026-08-05", 0, 1) is True      # week 1
assert sc.in_day_window("2026-08-10", 0, 1) is False     # day 10 not in week 1
assert sc.in_day_window("2026-08-10", 0, 2) is True      # week 2 (8-14)
assert sc.in_day_window("", 0, 0) is True                # undated line kept only in full-month window
assert sc.in_day_window("", 13, 0) is False              # undated line excluded from a MTD slice

# ── "as of day" alignment: cut both windows to day ≤ 12 → base loses the day-15 BYOD + day-20 accessory
b12 = [r for r in base if sc.in_day_window(r["trans_date"], 12, 0)]
c12 = [r for r in comp if sc.in_day_window(r["trans_date"], 12, 0)]
out12 = sc.build(b12, c12, rules, is_acc, vendors, base_period="2026-08", compare_period="2026-07",
                 mode="mom", window_label="Through day 12", resolve_market=resolve_market)
t12 = {t["key"]: t for t in out12["totals_by_category"]}
assert t12["byod"]["current"] == 0, ("byod should drop out of day<=12 window", t12["byod"]["current"])
assert t12["phone"]["current"] == 2, ("phones still 2 by day 12", t12["phone"]["current"])
assert t12["accessory"]["current"] == 1, ("only the txn1 case remains by day 12", t12["accessory"]["current"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §E  ACCESSORY SALES AND STORE EXPENSES, MONTH OVER MONTH (owner ask 2026-10-17, §COMMCALC-18)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The two facts proved here: (1) accessory DOLLARS carry their own month-over-month change, so the
# table can show an accessory line moving even though its unit change is not the headline; and (2) the
# expense line is joined on THE store-identity home, is reported ONCE per store, and never leaks into
# revenue, GP or the transaction count.
from app.modules.commcalc import expenses_effective as exf
from app.modules.account import store_identity as sid

# The real resolver over DECLARED spellings — the sale feed writes ADDRESSES, store_expenses writes
# CODES. This is the fixture of that live asymmetry (measured house org 2026-10-17: 28 of 28 stores).
MAPPING = [{"store_code": "B-1", "store_address": "Store A"},
           {"store_code": "B-2", "store_address": "Store B"}]
resolve = sid.build_store_resolver(MAPPING, [])
fold = lambda v: sid.store_key(resolve, v)                                             # noqa: E731
assert fold("B-1") == "Store A", fold("B-1")        # a CODE folds onto the address the sales feed uses
assert fold("Store A") == "Store A"
assert fold("b-1") == "Store A", fold("b-1")        # case drift is not a second store (the b-1115 class)

# ── §E1 the per-store expense fold is THE one home, and it is case/spelling-proof ─────────────────
exp_rows_base = [{"period": "2026-08", "store_code": "B-1", "amount": 4000, "source_key": None},
                 {"period": "2026-08", "store_code": "b-1", "amount": 1000, "source_key": None},
                 {"period": "2026-08", "store_code": "B-2", "amount": 2500, "source_key": None}]
tb = exf.totals_by_store(exp_rows_base, fold)
assert tb == {"Store A": 5000.0, "Store B": 2500.0}, tb
# WITHOUT the fold the two spellings of one store are two stores — the defect this join exists to stop.
assert set(exf.totals_by_store(exp_rows_base)) == {"B-1", "b-1", "B-2"}
assert exf.totals_by_store([{"store_code": "B-1", "amount": "1,200.50"}], fold) == {"Store A": 1200.5}
assert exf.totals_by_store([{"store_code": "", "amount": 7}], fold) == {"": 7.0}   # visible, not dropped
assert exf.totals_by_store([], fold) == {} and exf.totals_by_store(None) == {}

# ── §E2 THE CARRY-FORWARD RULE is the one the P&L books — not re-stated here ──────────────────────
# 2026-07 has no rows of its own, so it inherits the latest strictly-prior MANUAL month (2026-06) and
# says so; a SYSTEM row (source_key set) is period-specific and never carries.
all_exp = exp_rows_base + [
    {"period": "2026-06", "store_code": "B-1", "amount": 3000, "source_key": None},
    {"period": "2026-06", "store_code": "B-2", "amount": 2000, "source_key": None},
    {"period": "2026-06", "store_code": "B-2", "amount": 9999, "source_key": "payroll_gross"},
]
eff = exf.effective_series(all_exp, ["2026-08", "2026-07"])
assert eff["2026-08"]["carried_from"] is None
assert eff["2026-07"]["carried_from"] == "2026-06", eff["2026-07"]["carried_from"]
cmp_tot = exf.totals_by_store(eff["2026-07"]["rows"], fold)
assert cmp_tot == {"Store A": 3000.0, "Store B": 2000.0}, cmp_tot   # the payroll row did NOT carry

base_tot = exf.totals_by_store(eff["2026-08"]["rows"], fold)
expenses = {"base": base_tot, "compare": cmp_tot,
            "base_carried_from": None, "compare_carried_from": "2026-06"}

oute = sc.build(base, comp, rules, is_acc, vendors,
                base_period="2026-08", compare_period="2026-07", mode="mom",
                window_label="Full month", resolve_market=resolve_market,
                expenses=expenses, store_key_of=fold)

# ── §E3 the expense line exists, is flagged, and is LAST (it is not an item sold) ─────────────────
cats_e = [c for c in oute["categories"] if c.get("expense")]
assert len(cats_e) == 1 and cats_e[0]["key"] == sc.EXPENSE_KEY, cats_e
assert oute["categories"][-1]["key"] == sc.EXPENSE_KEY
assert cats_e[0]["metric"] == "dollars"
# ...and it is ABSENT when no expenses are handed in, so every existing caller is unchanged.
assert not [c for c in out["categories"] if c.get("expense")]
assert out["expenses_meta"] is None and out["overall"]["current_exp"] is None

# ── §E4 ONE expense row per store, on the right store, with its own Δ and Δ% ──────────────────────
erows = [r for r in oute["rows"] if r["category_key"] == sc.EXPENSE_KEY]
assert len(erows) == 2, erows
by_store = {r["store"]: r for r in erows}
assert set(by_store) == {"Store A", "Store B"}, by_store
a = by_store["Store A"]
assert a["current_rev"] == 5000.0 and a["previous_rev"] == 3000.0
assert a["rev_delta"] == 2000.0 and a["rev_pct"] == 66.7, (a["rev_delta"], a["rev_pct"])
assert a["market"] == "Metro", a["market"]        # the store's market, same resolver as its sale rows
assert a["current"] == 0 and a["previous"] == 0 and a["delta"] == 0   # an expense has NO unit
assert a["pct"] == a["rev_pct"]                   # sorting on 'Change %' sorts this row on its only change
assert a["expense"] is True

# ── §E5 THE LEAK TEST: an expense never enters revenue, GP or the transaction count ───────────────
assert oute["overall"]["current_rev"] == out["overall"]["current_rev"], (
    "expenses leaked into revenue", oute["overall"]["current_rev"], out["overall"]["current_rev"])
assert oute["overall"]["previous_rev"] == out["overall"]["previous_rev"]
assert oute["overall"]["current_txns"] == out["overall"]["current_txns"]
assert oute["overall"]["rev_pct"] == out["overall"]["rev_pct"]
# every per-item total is byte-identical to the run with no expenses at all
t_no = {t["key"]: t for t in out["totals_by_category"]}
t_yes = {t["key"]: t for t in oute["totals_by_category"]}
for k, v in t_no.items():
    assert t_yes[k] == v, ("per-item total moved when expenses were added", k, v, t_yes[k])
# the expense TOTAL is its own figure, reported on its own keys
assert t_yes[sc.EXPENSE_KEY]["current_rev"] == 7500.0
assert t_yes[sc.EXPENSE_KEY]["previous_rev"] == 5000.0
assert oute["overall"]["current_exp"] == 7500.0 and oute["overall"]["previous_exp"] == 5000.0
assert oute["overall"]["exp_pct"] == 50.0, oute["overall"]["exp_pct"]
assert oute["expenses_meta"]["compare_carried_from"] == "2026-06"
assert oute["expenses_meta"]["stores"] == 2 and oute["expenses_meta"]["measured"] is True

# ── §E6 ACCESSORY SALES COMPARE ON DOLLARS — the ask, pinned on the figure the table renders ──────
acc = t_yes["accessory"]
assert acc["metric"] == "dollars"
assert acc["current_rev"] == 55.0 and acc["previous_rev"] == 20.0, (acc["current_rev"], acc["previous_rev"])
assert acc["rev_delta"] == 35.0, acc["rev_delta"]
assert acc["rev_pct"] == 175.0, acc["rev_pct"]
# and per store, on the row the grid shows — rev_delta / rev_pct are what the $ Change columns read
acc_rows = [r for r in oute["rows"] if r["category_key"] == "accessory"]
assert acc_rows and all(r["rev_delta"] == round(r["current_rev"] - r["previous_rev"], 2) for r in acc_rows)
assert all(r["rev_pct"] is not None or r["previous_rev"] == 0 for r in acc_rows)

# ── §E7 SEVERAL SALE SPELLINGS OF ONE STORE: the month's expense is reported ONCE, and named ──────
# The regression for the defect a per-spelling expense row would be: one month of rent multiplied by
# however many ways the feed happened to spell the door.
base_two = base + [line("t9", "Store A Annex", "2026-08-18", dept="Ondigo", prod="Cable", ext=15, gp=9)]
resolve2 = sid.build_store_resolver(MAPPING, [{"alias": "Store A Annex", "store_code": "B-1"}])
fold2 = lambda v: sid.store_key(resolve2, v)                                           # noqa: E731
assert fold2("Store A Annex") == "Store A", fold2("Store A Annex")
out2 = sc.build(base_two, comp, rules, is_acc, vendors,
                base_period="2026-08", compare_period="2026-07", mode="mom",
                window_label="Full month", resolve_market=resolve_market,
                expenses=expenses, store_key_of=fold2)
e2 = [r for r in out2["rows"] if r["category_key"] == sc.EXPENSE_KEY]
assert len(e2) == 2, ("one expense row per STORE, not per spelling", [r["store"] for r in e2])
assert sum(r["current_rev"] for r in e2) == 7500.0, sum(r["current_rev"] for r in e2)
assert out2["expenses_meta"]["store_merges"] == {"Store A": ["Store A", "Store A Annex"]}, \
    out2["expenses_meta"]["store_merges"]

# ── §E8 A STORE THAT SPENT AND SOLD NOTHING IS STILL REPORTED ─────────────────────────────────────
exp_ghost = {"base": dict(base_tot, **{"Store C": 900.0}), "compare": cmp_tot,
             "base_carried_from": None, "compare_carried_from": "2026-06"}
out3 = sc.build(base, comp, rules, is_acc, vendors,
                base_period="2026-08", compare_period="2026-07", mode="mom",
                window_label="Full month", resolve_market=resolve_market,
                expenses=exp_ghost, store_key_of=fold)
ghost = [r for r in out3["rows"] if r["store"] == "Store C"]
assert len(ghost) == 1 and ghost[0]["current_rev"] == 900.0, ghost
assert ghost[0]["rev_pct"] is None            # no base month to divide by — 'new', never a fake 0%
gs = [r for r in out3["store_summary"] if r["store"] == "Store C"]
assert gs and gs[0]["current_txns"] == 0 and gs[0]["current_exp"] == 900.0, gs
# ...and the store roll-up carries the expense beside the revenue for the stores that DID sell
sa = [r for r in out3["store_summary"] if r["store"] == "Store A"][0]
assert sa["current_exp"] == 5000.0 and sa["exp_pct"] == 66.7, sa

print("§E accessory $ + store expenses month-over-month: PASSED ✅")

# ══ §F THE ±0% THAT WAS A LIE: a change between two CARRIED months is NOT MEASURED ════════════════
# THE DEFECT, from the live screen (owner, 2026-10-19): "Store expenses $276,162.69 ± 0% was
# $276,162.69". Both October and September had no entered expenses of their own, so both inherited
# the SAME sheet (August). The report then divided that sheet by itself and printed "± 0%", which a
# reader takes as "spending held steady" — when in truth nobody had entered either month.
# THE CLASS (§19.49): an ABSENCE is reported with its reason, never as a result. The figures stay —
# they are what each month books — but the PERCENT is the claim, so the percent is what is withheld.

# §F1 the predicate itself, on its own, before any report is built
assert sc.expense_change_measured("2026-08", "2026-08") is False      # one sheet against itself
assert sc.expense_change_measured("2026-08", "2026-07") is True       # two real months
assert sc.expense_change_measured(None, "2026-07") is True            # base is entered: a real pair
assert sc.expense_change_measured("2026-08", None) is True            # compare is entered
assert sc.expense_change_measured(None, None) is True                 # both entered — the normal case
assert sc.expense_change_measured("", "") is True                     # blank is "not carried", not equal
assert sc.expense_change_measured("2026-08", "August 2026") is False, \
    "the same month spelled two ways is still one sheet"              # period_sort_key, not string ==
assert sc.expense_change_measured("August 2026", "august 2026") is False  # ...nor does case
assert sc.expense_change_measured("opening", "opening") is False      # unparseable: fall back to text
assert sc.expense_change_measured("opening", "closing") is True

# §F2 THE REGRESSION — the exact shape of the reported screen, through the real `build`
exp_both_carried = {"base": base_tot, "compare": dict(base_tot),
                    "base_carried_from": "2026-06", "compare_carried_from": "2026-06"}
outf = sc.build(base, comp, rules, is_acc, vendors,
                base_period="2026-08", compare_period="2026-07", mode="mom",
                window_label="Full month", resolve_market=resolve_market,
                expenses=exp_both_carried, store_key_of=fold)
mf = outf["expenses_meta"]
assert mf["measured"] is True, "the FIGURES were read — that part was never in doubt"
assert mf["change_measured"] is False, "two carried copies of one sheet is not a measured change"
assert mf["pct"] is None, mf["pct"]                            # the ± 0% the screen used to print
assert outf["overall"]["exp_pct"] is None, outf["overall"]["exp_pct"]
# the FIGURES are untouched: withholding the percent must not blank the money
assert mf["current_total"] == mf["previous_total"] == 7500.0, (mf["current_total"], mf["previous_total"])
assert outf["overall"]["current_exp"] == 7500.0 and outf["overall"]["previous_exp"] == 7500.0
tf = {t["key"]: t for t in outf["totals_by_category"]}[sc.EXPENSE_KEY]
assert tf["rev_pct"] is None and tf["pct"] is None, (tf["rev_pct"], tf["pct"])
assert tf["change_measured"] is False
assert tf["current_rev"] == 7500.0 and tf["previous_rev"] == 7500.0
assert tf["rev_delta"] == 0.0, "nought IS the subtraction; it is the percent that is a claim"

# §F3 ...per store too — a grid row may not print a percent its total refuses to
ef = [r for r in outf["rows"] if r["category_key"] == sc.EXPENSE_KEY]
assert ef and all(r["change_measured"] is False for r in ef), ef
assert all(r["rev_pct"] is None and r["pct"] is None for r in ef)
assert all(r["current_rev"] == r["previous_rev"] for r in ef)      # same sheet on both sides

# §F4 ONE SIDE CARRIED IS STILL A REAL COMPARISON — the carry rule is the P&L's own, not an estimate.
# This is the case the existing §E fixture already is, and it must keep its percent.
assert oute["expenses_meta"]["change_measured"] is True
assert oute["expenses_meta"]["pct"] == 50.0 and oute["overall"]["exp_pct"] == 50.0
assert all(r.get("change_measured") is not False
           for r in oute["rows"] if r["category_key"] == sc.EXPENSE_KEY)

# §F5 TWO DIFFERENT CARRIED MONTHS ARE TWO MONTHS — carried on both sides is not the test; SAME is.
exp_two_carries = dict(exp_both_carried, base_carried_from="2026-06", compare_carried_from="2026-05")
outg = sc.build(base, comp, rules, is_acc, vendors,
                base_period="2026-08", compare_period="2026-07", mode="mom",
                window_label="Full month", resolve_market=resolve_market,
                expenses=exp_two_carries, store_key_of=fold)
assert outg["expenses_meta"]["change_measured"] is True
assert outg["expenses_meta"]["pct"] == 0.0, "0% between two DIFFERENT months is a real finding"

# §F6 NEGATIVE CONTROLS — each rule is red when broken
assert sc.expense_change_measured("2026-08", "2026-08") is not True, \
    "a predicate that compared only object identity would pass this and still be wrong"
# an implementation that string-compared would call these two the same month and must not
assert sc.expense_change_measured("2026-6", "2026-06") is False, \
    "period_sort_key normalises the spelling; a raw == would wrongly report a measured change"
# ...and the sales side is untouched by any of it (the §E5 leak test, repeated for this path)
t_f = {t["key"]: t for t in outf["totals_by_category"]}
for k, v in {t["key"]: t for t in out["totals_by_category"]}.items():
    assert t_f[k] == v, ("a withheld expense percent moved an item total", k)
assert outf["overall"]["current_rev"] == out["overall"]["current_rev"]
assert outf["overall"]["rev_pct"] == out["overall"]["rev_pct"]

print("§F a change between two carried sheets is NOT MEASURED: PASSED ✅")
print("\nALL ASSERTIONS PASSED ✅")
