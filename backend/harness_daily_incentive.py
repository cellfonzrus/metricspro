#!/usr/bin/env python3
"""PROOF — the declared-metric catalog and the daily incentive engine (mig 1070). DB-free, stdlib.

The oracle is the owner's own screenshot (NY Logistics / UPS Store, 2026-10-10): six live rules with
their types, qualifiers and payouts. Every one of them is reconstructed here as CONFIG — no rule name,
no category name and no rate appears in `metric_catalog.py` or `daily_incentive.py`, which is the
whole point of the change. If any of them has to be spelled in code to make this file pass, the design
has regressed to the patchwork the owner rejected.

  §A  catalog vocabulary + the mig-501 fallback is byte-identical
  §B  the two physical stores fold into one reader
  §C  per-employee attribution, and what it refuses to invent
  §D  gross revenue has ONE definition
  §E  the six screenshot rules, each paying what the screenshot says
  §F  gates fail closed
  §G  a pool that cannot be split is reported, never guessed
  §H  negative controls
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from app.modules.closing import metric_catalog as mc
from app.modules.closing import count_config
from app.modules.commcalc import daily_incentive as di

P = F = 0
def ok(cond, label, extra=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {label}")
    else:
        F += 1
        print(f"  FAIL  {label}  {extra}")
def eq(a, b, label):
    ok(a == b, label, f"got {a!r} want {b!r}")


# ── The tenant's declared categories. CONFIG ROWS — exactly what an admin types on Count Fields. ──
NY_DEFS = [
    {"field_key": "dropoff",        "label": "Package Drop-offs", "value_kind": "count",
     "declared_by": "employee", "unit_price": 0,    "in_revenue": False, "sort_order": 10},
    {"field_key": "overnight",      "label": "Overnight Shipments", "value_kind": "both",
     "declared_by": "employee", "unit_price": 0,    "sort_order": 20},
    {"field_key": "express_23day",  "label": "2/3 Day Express", "value_kind": "both",
     "declared_by": "employee", "unit_price": 0,    "sort_order": 30},
    {"field_key": "mailbox_1yr",    "label": "Mailbox 1-Year Rental", "value_kind": "count",
     "declared_by": "employee", "unit_price": 180,  "sort_order": 40},
    {"field_key": "mailbox_short",  "label": "Mailbox Short-Term Rental", "value_kind": "count",
     "declared_by": "employee", "unit_price": 60,   "sort_order": 50},
    {"field_key": "printing",       "label": "Printing Services", "value_kind": "amount",
     "declared_by": "employee", "sort_order": 60},
]
CAT = mc.build(NY_DEFS)

# ── The six rules from the screenshot. CONFIG ROWS — the editor's own shape. ──────────────────────
RULES = [
    {"key": "daily_cash_pool", "label": "Daily Cash Pool", "rule_kind": "threshold_pool",
     "qualifier": {"scope": "store", "basis": "revenue", "metric_keys": [], "op": "gte",
                   "threshold": 1300.00},
     "payout": {"basis": "revenue", "metric_keys": [], "rate": 0.02, "split": "hours"}},
    {"key": "overnight", "label": "Overnight Shipments", "rule_kind": "per_unit",
     "qualifier": {"scope": "store", "basis": "units",
                   "metric_keys": ["overnight", "express_23day"], "op": "gte", "threshold": 5},
     "payout": {"basis": "units", "metric_keys": ["overnight"], "rate": 5.00}},
    {"key": "express", "label": "2/3 Day Express Shipments", "rule_kind": "per_unit",
     "qualifier": {"scope": "store", "basis": "units",
                   "metric_keys": ["overnight", "express_23day"], "op": "gte", "threshold": 5},
     "payout": {"basis": "units", "metric_keys": ["express_23day"], "rate": 2.50}},
    {"key": "mailbox_1yr", "label": "Mailbox 1-Year Rental", "rule_kind": "per_unit",
     "qualifier": {"scope": "none"},
     "payout": {"basis": "units", "metric_keys": ["mailbox_1yr"], "rate": 20.00}},
    {"key": "mailbox_short", "label": "Mailbox Short-Term Rental", "rule_kind": "per_unit",
     "qualifier": {"scope": "none"},
     "payout": {"basis": "units", "metric_keys": ["mailbox_short"], "rate": 5.00}},
    {"key": "printing", "label": "Printing Services", "rule_kind": "percent_of_amount",
     "qualifier": {"scope": "employee", "basis": "amount", "metric_keys": ["printing"],
                   "op": "gte", "threshold": 150.00},
     "payout": {"basis": "amount", "metric_keys": ["printing"], "rate": 0.10}},
]

# ── A day. Two employees. ─────────────────────────────────────────────────────────────────────────
LINES = [
    # Ana: 3 overnight ($180), 2 express ($90), 1 one-year mailbox, $200 printing
    {"metric_key": "overnight",     "employee_name": "Ana",  "units": 3, "amount": 180.00},
    {"metric_key": "express_23day", "employee_name": "Ana",  "units": 2, "amount": 90.00},
    {"metric_key": "mailbox_1yr",   "employee_name": "Ana",  "units": 1},
    {"metric_key": "printing",      "employee_name": "Ana",  "units": 0, "amount": 200.00},
    {"metric_key": "dropoff",       "employee_name": "Ana",  "units": 14},
    # Ben: 1 overnight ($60), 2 express ($95), 2 short mailboxes, $100 printing (under the $150 gate)
    {"metric_key": "overnight",     "employee_name": "Ben",  "units": 1, "amount": 60.00},
    {"metric_key": "express_23day", "employee_name": "Ben",  "units": 2, "amount": 95.00},
    {"metric_key": "mailbox_short", "employee_name": "Ben",  "units": 2},
    {"metric_key": "printing",      "employee_name": "Ben",  "units": 0, "amount": 100.00},
    {"metric_key": "dropoff",       "employee_name": "Ben",  "units": 9},
]
DECLARED = mc.declared(CAT, {}, LINES)
PER_EMP = mc.by_employee(CAT, LINES)
HOURS = {"Ana": 8.0, "Ben": 4.0}


print("\n§A  catalog vocabulary + the mig-501 fallback")
eq(tuple(mc.STANDARD_KEYS), tuple(k for (k, _l, _r, _s) in count_config.STANDARD_DEFS),
   "A1 metric_catalog.STANDARD_KEYS mirrors count_config.STANDARD_DEFS (no drift)")
eq(mc.keys(mc.build([])), [], "A2 no declared categories -> an empty catalog, not a wireless one")
_std = mc.build([{"field_key": k, "label": l, "recon_class": r, "sort_order": s, "is_standard": True}
                 for (k, l, r, s) in count_config.STANDARD_DEFS])
eq([m["key"] for m in _std], list(mc.STANDARD_KEYS), "A3 the built-in three keep their order")
ok(all(m["value_kind"] == "count" and m["declared_by"] == "store" and not m["in_revenue"]
       for m in _std), "A4 the built-in three stay counts, store-level, out of revenue (mig 501 unchanged)")
ok(all(mc.storage_for(m) == "counts_jsonb" for m in _std),
   "A5 the built-in three still write daily_closing.counts — no existing tenant moves")
ok(all(m["storage"] == mc.storage_for(m) for m in CAT),
   "A5b every catalog entry carries WHERE it is stored, so no caller decides")
eq(mc.storage_for({"declared_by": "employee", "value_kind": "count"}), "metric_line",
   "A6 a per-employee category writes the new table")
eq(mc.storage_for({"declared_by": "store", "value_kind": "amount"}), "metric_line",
   "A7 an amount-valued category writes the new table")
eq(len(mc.VALUE_KINDS) + len(mc.DECLARED_BY) + len(di.RULE_KINDS) + len(di.SPLIT_BASES), 3 + 2 + 3 + 3,
   "A8 the vocabulary is the declared size (a silent addition trips this)")


print("\n§B  two physical stores, one reader")
_row = {"upgrade_count": 4, "counts": {"byod": 7}}
_cat2 = mc.build([{"field_key": "upgrade_count", "label": "Upgrades", "is_standard": True, "sort_order": 1},
                  {"field_key": "byod", "label": "BYOD", "sort_order": 2},
                  {"field_key": "printing", "label": "Printing", "value_kind": "amount", "sort_order": 3}])
_d = mc.declared(_cat2, _row, [{"metric_key": "printing", "amount": 42.50}])
eq(mc.units_of(_d, "upgrade_count"), 4, "B1 a physical column is read")
eq(mc.units_of(_d, "byod"), 7, "B2 a counts-jsonb custom field is read")
eq(mc.amount_of(_d, "printing"), 42.50, "B3 a metric_line amount is read")
eq(sorted(_d), ["byod", "printing", "upgrade_count"], "B4 one answer covers every catalog metric")
_d0 = mc.declared(_cat2, {}, [])
ok(all(not v["declared"] for v in _d0.values()),
   "B5 'declared nothing' is distinguishable from 'has no such category'")
eq(mc.declared(_cat2, _row, [{"metric_key": "retired_thing", "amount": 99}])["byod"]["units"], 7,
   "B6 a line for a retired category is ignored, not an error")
eq(mc.units_of(mc.declared(CAT, {}, LINES), "overnight"), 4,
   "B7 per-employee lines sum into the store-day figure (3 + 1)")


print("\n§C  per-employee attribution, and what it refuses to invent")
eq(sorted(PER_EMP), ["Ana", "Ben"], "C1 both people appear")
eq(PER_EMP["Ana"]["overnight"]["units"], 3, "C2 Ana's own units")
eq(PER_EMP["Ben"]["express_23day"]["amount"], 95.00, "C3 Ben's own dollars")
_store_only = mc.by_employee(_cat2, [{"metric_key": "byod", "employee_name": "Ana", "units": 3}])
eq(_store_only, {}, "C4 a STORE-level metric is never attributed to a person")
eq(mc.by_employee(CAT, [{"metric_key": "overnight", "units": 3}]), {},
   "C5 a line with no name is not credited to anybody")
eq(mc.by_employee(CAT, LINES, closing_row={"employee_name": "Submitter"}).get("Submitter"), None,
   "C6 the submitter is NOT credited with the store's day (the attribution bug this prevents)")


print("\n§D  gross revenue has ONE definition")
eq(sorted(mc.revenue_keys(CAT)),
   ["express_23day", "mailbox_1yr", "mailbox_short", "overnight", "printing"],
   "D1 revenue = the categories declared to be revenue")
ok("dropoff" not in mc.revenue_keys(CAT),
   "D2 a tracked volume with no price is not revenue")
# 180 + 90 + 180 + 200  +  60 + 95 + 120 + 100  = 1025.00
eq(mc.gross_revenue(CAT, DECLARED), 1025.00, "D3 the store-day's declared gross revenue")
_er = mc.employee_revenue(CAT, PER_EMP)
eq(_er, {"Ana": 650.00, "Ben": 375.00}, "D4 each person's declared revenue")
eq(round(sum(_er.values()), 2), mc.gross_revenue(CAT, DECLARED),
   "D5 the people's revenue sums to the store's — one definition, two scopes")
_m, _miss = di.measure(CAT, DECLARED, "revenue", [])
eq(_m, mc.gross_revenue(CAT, DECLARED), "D6 the engine measures revenue through the catalog, not locally")


print("\n§E  the six screenshot rules")
R = di.compute_day(CAT, RULES, declared_map=DECLARED, per_employee=PER_EMP,
                   hours_by_employee=HOURS, employees=["Ana", "Ben"])
def paid(who, rule):
    return round(sum(l["amount"] for l in R["lines"]
                     if l["employee"] == who and l["rule"] == rule), 2)

eq(R["store_revenue"], 1025.00, "E1 store revenue")
# Daily Cash Pool: $1,025 < $1,300 -> does NOT trigger.
eq(paid("Ana", "Daily Cash Pool"), 0.0, "E2 the pool does not pay below its $1,300 threshold")
_pool_note = [n for n in R["rules"] if n["rule"] == "Daily Cash Pool"][0]
eq(_pool_note["qualifier"]["value"], 1025.00, "E3 ...and says what the day actually was")
# Overnight + express combined = 4 + 4 = 8 >= 5 -> the store gate passes.
eq(paid("Ana", "Overnight Shipments"), 15.00, "E4 Ana 3 overnight x $5.00")
eq(paid("Ben", "Overnight Shipments"), 5.00, "E5 Ben 1 overnight x $5.00")
eq(paid("Ana", "2/3 Day Express Shipments"), 5.00, "E6 Ana 2 express x $2.50")
eq(paid("Ben", "2/3 Day Express Shipments"), 5.00, "E7 Ben 2 express x $2.50")
eq(paid("Ana", "Mailbox 1-Year Rental"), 20.00, "E8 Ana 1 one-year mailbox x $20.00")
eq(paid("Ben", "Mailbox Short-Term Rental"), 10.00, "E9 Ben 2 short mailboxes x $5.00")
eq(paid("Ana", "Printing Services"), 20.00, "E10 Ana $200 printing x 10% (over the $150 gate)")
eq(paid("Ben", "Printing Services"), 0.0, "E11 Ben $100 printing is under the $150 gate")
eq(R["by_employee"], {"Ana": 60.00, "Ben": 20.00}, "E12 each person's day")
eq(R["total"], 80.00, "E13 the store's incentive for the day")
eq(R["top_earner"], "Ana", "E14 who generated the most revenue today")
ok(all(l.get("basis") for l in R["lines"]), "E15 every line carries its arithmetic")
# The pool, on a day that DOES clear $1,300: 2% of 1,500 = $30, split 8h / 4h.
BIG = dict(DECLARED)
BIG["printing"] = dict(DECLARED["printing"], amount=775.00)   # 1025 - 300 + 775 = 1500
R2 = di.compute_day(CAT, [RULES[0]], declared_map=BIG, per_employee=PER_EMP,
                    hours_by_employee=HOURS, employees=["Ana", "Ben"])
eq(R2["store_revenue"], 1500.00, "E16 the bigger day")
eq(R2["by_employee"], {"Ana": 20.00, "Ben": 10.00}, "E17 2% of $1,500 = $30, split 8h/4h")
eq(R2["total"], 30.00, "E18 the pool is paid out in full")
R3 = di.compute_day(CAT, [dict(RULES[0], payout=dict(RULES[0]["payout"], split="individual_sales"))],
                    declared_map=BIG, per_employee=PER_EMP, employees=["Ana", "Ben"])
eq(R3["total"], 30.00, "E19 the same pool split by individual sales still pays $30")
ok(R3["by_employee"]["Ana"] > R3["by_employee"]["Ben"],
   "E20 ...weighted to the bigger seller (the screenshot's other wording, as config)")
eq(sorted(set(l["rule_kind"] for l in R["lines"])), ["per_unit", "percent_of_amount"],
   "E21 only the shapes the engine knows reach a line")

# ── "Who ran the sale" (owner 2026-10-10): a pool split by individual sales weights by each
#    person's own figure in the POOL'S OWN categories, not by their whole day. ──────────────────
PRINT_POOL = [{"key": "print_pool", "label": "Printing Pool", "rule_kind": "threshold_pool",
               "qualifier": {"scope": "none"},
               "payout": {"basis": "amount", "metric_keys": ["printing"], "rate": 0.10,
                          "split": "individual_sales"}}]
RP = di.compute_day(CAT, PRINT_POOL, declared_map=DECLARED, per_employee=PER_EMP,
                    hours_by_employee=HOURS, employees=["Ana", "Ben"])
# printing: Ana $200, Ben $100 -> pool 10% of $300 = $30.00, split 200:100
eq(RP["total"], 30.00, "E22 a pool on one category is 10% of that category")
eq(RP["by_employee"], {"Ana": 20.00, "Ben": 10.00},
   "E23 ...split by who ran THOSE sales (200:100), not by the whole day (650:375)")
# The whole-day pool still weights by the whole day — one setting, no second knob.
R3b = di.compute_day(CAT, [dict(RULES[0], payout=dict(RULES[0]["payout"], split="individual_sales"))],
                     declared_map=BIG, per_employee=PER_EMP, employees=["Ana", "Ben"])
eq(round(sum(R3b["by_employee"].values()), 2), 30.00,
   "E24 a pool on the whole day still divides the whole day")
# A category nobody is recorded as having sold cannot be divided by who sold it.
NOBODY = [{"key": "mb_pool", "label": "Mailbox Pool", "rule_kind": "threshold_pool",
           "qualifier": {"scope": "none"},
           "payout": {"basis": "amount", "metric_keys": ["mailbox_short"], "rate": 0.50,
                      "split": "individual_sales"}}]
RNB = di.compute_day(CAT, NOBODY, declared_map=DECLARED, per_employee={"Ana": {}},
                     employees=["Ana"])
eq(RNB["by_employee"], {}, "E25 nobody recorded as running those sales -> nobody is paid")
ok(RNB["unallocated"] > 0 and "ran" in RNB["rules"][0]["note"],
   "E26 ...the pool is reported unallocated, with a reason in the owner's own words")


print("\n§F  gates fail closed")
_ghost = [dict(RULES[1], qualifier={"scope": "store", "basis": "units",
                                    "metric_keys": ["not_a_category"], "op": "gte", "threshold": 1})]
RF = di.compute_day(CAT, _ghost, declared_map=DECLARED, per_employee=PER_EMP, employees=["Ana"])
eq(RF["total"], 0.0, "F1 a qualifier naming an undeclared category pays nothing")
ok("missing" in RF["rules"][0]["qualifier"], "F2 ...and says which category is missing")
_ghostpay = [dict(RULES[3], payout={"basis": "units", "metric_keys": ["nope"], "rate": 20.0})]
eq(di.compute_day(CAT, _ghostpay, declared_map=DECLARED, per_employee=PER_EMP,
                  employees=["Ana"])["total"], 0.0,
   "F3 a PAYOUT naming an undeclared category pays nothing")
_unknown = [{"label": "X", "rule_kind": "magic", "payout": {"rate": 1}}]
RU = di.compute_day(CAT, _unknown, declared_map=DECLARED, per_employee=PER_EMP, employees=["Ana"])
eq(RU["total"], 0.0, "F4 an unknown rule type pays nothing")
ok(RU["rules"][0].get("skipped"), "F5 ...and is reported, not silently dropped")
eq(di.compute_day(CAT, RULES, declared_map=DECLARED, per_employee={}, employees=[])["total"], 0.0,
   "F6 a day nobody declared anything pays nothing")
_inactive = di.compute_day(CAT, [dict(r, is_active=False) for r in RULES],
                           declared_map=DECLARED, per_employee=PER_EMP, employees=["Ana", "Ben"])
eq(_inactive["total"], 0.0, "F7 a switched-off rule pays nothing")
_badop = [dict(RULES[5], qualifier=dict(RULES[5]["qualifier"], op="roughly"))]
eq(di.compute_day(CAT, _badop, declared_map=DECLARED, per_employee=PER_EMP,
                  employees=["Ana"])["total"], 0.0, "F8 an unknown comparison fails closed")


print("\n§G  a pool that cannot be split is reported, never guessed")
RN = di.compute_day(CAT, [RULES[0]], declared_map=BIG, per_employee=PER_EMP,
                    hours_by_employee={}, employees=["Ana", "Ben"])
eq(RN["by_employee"], {}, "G1 a by-hours pool with no hours pays nobody")
eq(RN["unallocated"], 30.00, "G2 ...the whole pool comes back unallocated")
ok("hours" in RN["rules"][0]["note"], "G3 ...with a reason a person can read")
_sh, _un, _nt = di.split_pool(100.0, ["A", "B", "C"], basis="equal")
eq(round(sum(_sh.values()), 2), 100.00, "G4 an equal split of $100 over 3 sums to exactly $100")
eq(_un, 0.0, "G5 ...with nothing unallocated")
_sh2, _, _ = di.split_pool(10.0, ["A", "B", "C"], basis="hours",
                           hours_by_employee={"A": 1, "B": 1, "C": 1})
eq(round(sum(_sh2.values()), 2), 10.00, "G6 the rounding remainder is never lost")
eq(di.split_pool(0.0, ["A"], basis="equal"), ({}, 0.0, ""), "G7 no pool, no payment, no noise")
_sh3, _un3, _ = di.split_pool(50.0, [], basis="equal")
eq(_un3, 50.00, "G8 a pool nobody qualified for is unallocated, not dropped")


print("\n§H  negative controls")
import subprocess
_SRC = ["app/modules/closing/metric_catalog.py", "app/modules/commcalc/daily_incentive.py"]
_here = os.path.dirname(os.path.abspath(__file__))
def _code_only(path):
    """The module's CODE, with every docstring and comment stripped. The doctrine in a docstring
    names the owner's examples on purpose — explaining WHY is not hard-coding. What must contain no
    tenant vocabulary is the executable text."""
    import io, tokenize
    src = open(path).read()
    out, prev_end, prev_tok = [], (1, 0), tokenize.INDENT
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and prev_tok in (tokenize.INDENT, tokenize.NEWLINE,
                                                        tokenize.NL, tokenize.DEDENT):
            continue   # a bare string statement = a docstring
        out.append(tok.string)
        if tok.type not in (tokenize.NL, tokenize.NEWLINE):
            prev_tok = tok.type
    return " ".join(out).lower()

_blob = "\n".join(_code_only(os.path.join(_here, p)) for p in _SRC)
for _word in ("mailbox", "overnight", "printing", "drop-off", "dropoff", "ups store",
              "ny logistics", "express"):
    ok(_word not in _blob, f"H-{_word}: no tenant category is spelled in the engine")
ok("1300" not in _blob and "0.02" not in _blob, "H-rates: no tenant rate is spelled in the engine")
ok(" import " not in _code_only(os.path.join(_here, _SRC[0])),
   "H-pure: the catalog imports nothing (pure, DB-free)")
ok(mc.gross_revenue(CAT, {}) == 0.0, "H-empty: no declarations, no revenue")
ok(di.compute_day(CAT, [], declared_map=DECLARED, per_employee=PER_EMP,
                  employees=["Ana"])["total"] == 0.0, "H-norules: no rules, no pay")
ok(mc.build(None) == [], "H-none: a missing registry is an empty catalog, never a crash")

print(f"\n{'='*70}\n  {P} passed / {F} failed\n{'='*70}")
sys.exit(1 if F else 0)
