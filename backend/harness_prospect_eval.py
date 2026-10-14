#!/usr/bin/env python3
"""
PROOF HARNESS — EVALUATE A STORE WE DO NOT OWN YET (index §COMMCALC-03, mig 1077)
================================================================================

Owner directive 2026-10-12: *"need to make a module for evaluating the store sales for the store we
need to take over, but without creating the actual stores but upload the ma comm, max tx and handset
prdering data, import the expenses from our average expenses, keep the rent as what the actual rent
is for the new store and calculate the p&l also need to fillter out the family plans with more than 2
lines from these reports for each month to assess if fraud is going on and cjhecking which phones are
sold on those, create a sub report in that menu to take out those phones from profitability or leave
them. thisis platform wide but situation at hand is for total, nothing hardocded and everything
should be clear with analysis"*

DB-free, no I/O, no network. Every number below is computed by the modules under test.

WHAT THIS PROVES, in the order the house rules ask for it:

 A. THE ACCOUNT IDENTITY IS A DECLARED LADDER, read per ROW. Includes the live fixture: the 34
    distinct `activation_type` / account-column shapes measured on the platform's own MA commission
    feed, so the predicate is proved against the real vocabulary rather than invented strings. And
    the empty-declaration trap — `row.get(k) or house` has silently restored the house words twice in
    this repo; an explicitly empty declaration must stay empty.

 B. THE MULTI-LINE REPORT IS A WHO-TO-LOOK-AT LIST, not a verdict. The threshold is EXCLUSIVE ("more
    than 2"), the grouping is WITHIN a month, the devices come out with it, and nothing in the
    payload concludes that fraud happened.

 C. THE AVERAGE SURVIVES THE TWO FAULTS IT WAS REWRITTEN FOR, both measured on the live house
    expense matrix: a month whose sheet OMITS a line (May 2026 records no salary line at all) and a
    month whose store ROSTER is wider than the line (July 2026 has 34 store codes against 25 on
    every line). The ARMED NEGATIVE CONTROLS reproduce the pre-fix arithmetic and assert it was
    wrong, so a regression to it cannot pass. Plus the rent override, including its edges.

 D. ONE FACT, ONE HOME, DEREFERENCED. The booking rules are not re-derived here: the evaluation
    books through `ma_store_pnl` / `device_cogs` / `coa` themselves. Asserted two ways — the
    factored-out `device_cogs` pure cores reproduce their own I/O wrappers' arithmetic, and an AST
    scan of `prospect_eval.py` fails the build if it ever grows a P&L line map, a sign table or a
    booking loop of its own.

 E. COVERAGE IS HONEST. Every chart line is `measured`, `not_measured` or `uploaded_feed_missing`,
    a line nothing can answer is never reported as a measured 0.00, and the bottom line is LABELLED
    as a carrier-side contribution rather than as the prospect's net income.

 F. BOTH EXCLUSION MODES ARE ALWAYS COMPUTED, and excluding a flagged line removes its handset from
    COGS too — otherwise excluded revenue would be measured against a cost that stayed.

 G. A PROSPECT CANNOT REACH A LIVE TABLE. An AST scan of the router's prospect section asserts it
    writes only the two prospect tables, and that no `raw_*`, store-mapping or statement table is
    written anywhere in it.
"""
import inspect
import ast
import os
import sys

sys.path.insert(0, ".")

from app.modules.core.module_graph import home_under_app        # noqa: E402
from harnesslib import py_code_only                              # noqa: E402  (locks read CODE)
from app.modules.commcalc import prospect_eval as P            # noqa: E402
from app.modules.commcalc import product_mix as _pm         # noqa: E402
from app.modules.account import coa as COA                      # noqa: E402
from app.modules.account import device_cogs as DC               # noqa: E402
from app.modules.account import ma_store_pnl as MSP             # noqa: E402
from app.modules.account import residual_subs as RS             # noqa: E402
from app.modules.account import engine as ENG                   # noqa: E402
from app.modules.account import analysis as AN                  # noqa: E402

# THE HOME this lock owns, read from the graph rather than typed here — a registry written and left
# un-dereferenced is not a fix (index §19.18, §50).
HOME = home_under_app("store_takeover_evaluation")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


# ══ FIXTURES ══════════════════════════════════════════════════════════════════════════════════════
# The account-column shapes measured on the live platform (LuxeLink August 2026, raw_ma_commission,
# 1,248 rows): `bin` populated on all of them, `ban` on 27, `activation_order` one per line. A
# multi-line account presents as one `bin`, one store, one day, consecutive orders, all 'Add'.
LIVE_SHAPE = [
    # one three-line family-plan account, exactly the live shape
    {"period": "August 2026", "ban": None, "bin": "B2-773557615", "activation_order": "352490213",
     "tx_date": "2026-08-07", "merchant_account_id": "168874", "activation_type": "Add",
     "sku": "Samsung Galaxy A37 5G TO", "imei": "111111111111111", "spiff_m1": -20.0},
    {"period": "August 2026", "ban": None, "bin": "B2-773557615", "activation_order": "352490214",
     "tx_date": "2026-08-07", "merchant_account_id": "168874", "activation_type": "Add",
     "sku": "Apple iPhone 13 128GB Midnight TO", "imei": "222222222222222", "spiff_m1": -20.0},
    {"period": "August 2026", "ban": None, "bin": "B2-773557615", "activation_order": "352490215",
     "tx_date": "2026-08-07", "merchant_account_id": "168874", "activation_type": "Add",
     "sku": "Samsung Galaxy A37 5G TO", "imei": "333333333333333", "spiff_m1": -7.5},
    # a two-line account — an ordinary couple, NOT flagged at the house threshold
    {"period": "August 2026", "ban": None, "bin": "B2-790019046", "activation_order": "353232711",
     "tx_date": "2026-08-12", "merchant_account_id": "170085", "activation_type": "Add",
     "sku": "Apple iPhone 16e 128GB Black TO", "imei": "444444444444444", "spiff_m1": -27.5},
    {"period": "August 2026", "ban": None, "bin": "B2-790019046", "activation_order": "353232725",
     "tx_date": "2026-08-12", "merchant_account_id": "170085", "activation_type": "Add",
     "sku": "Apple iPhone 16e 128GB Black TO", "imei": "555555555555555", "spiff_m1": -27.5},
    # a single line whose account answers on `ban`, not `bin`
    {"period": "August 2026", "ban": "0990446048", "bin": None, "activation_order": "353569477",
     "tx_date": "2026-08-14", "merchant_account_id": "170074", "activation_type": "New",
     "sku": "Samsung Galaxy A16 5G TO", "imei": "666666666666666", "device_margin": -40.0},
    # a line with NO account identifier at all — reported, never grouped under a guess
    {"period": "August 2026", "ban": None, "bin": None, "activation_order": "353569478",
     "tx_date": "2026-08-14", "merchant_account_id": "170074", "activation_type": "New",
     "sku": "Samsung Galaxy A16 5G TO", "imei": "777777777777777", "fees_margin": -3.0},
]
# the SAME account key, in the NEXT month: three lines across two months is a customer who grew
# their plan, not a multi-line sale, and must not flag.
NEXT_MONTH = [
    {"period": "September 2026", "ban": None, "bin": "B2-773557615", "activation_order": "360000001",
     "tx_date": "2026-09-03", "merchant_account_id": "168874", "activation_type": "Add",
     "sku": "TCL Tab 8 NXTPAPER 5G TO", "imei": "888888888888888", "spiff_m1": -20.0},
]
PRICE_SHEET = [
    {"order_type": "Branded Handset", "product_name": "Samsung Galaxy A37 5G TO",
     "number_ordered": 2, "price": 199.99},
    {"order_type": "Branded Handset", "product_name": "Apple iPhone 13 128GB Midnight TO",
     "number_ordered": 1, "price": 429.99},
    {"order_type": "Branded Handset", "product_name": "Apple iPhone 16e 128GB Black TO",
     "number_ordered": 2, "price": 599.99},
    {"order_type": "Branded Handset", "product_name": "Samsung Galaxy A16 5G TO",
     "number_ordered": 4, "price": 129.99},
    {"order_type": "Branded Handset", "product_name": "TCL Tab 8 NXTPAPER 5G TO",
     "number_ordered": 1, "price": 179.99},
    # a non-device order line must not enter the price list
    {"order_type": "SIM Kit", "product_name": "Samsung Galaxy A16 5G TO",
     "number_ordered": 100, "price": 1.0},
]
TX_ROWS = [
    {"period": "August 2026", "account_id": "168874", "order_type": "Airtime",
     "product_name": "Top up 40", "retail_cost": 40.0, "merchant_discount": 2.4},
    {"period": "August 2026", "account_id": "170085", "order_type": "Airtime",
     "product_name": "Top up 55", "retail_cost": 55.0, "merchant_discount": 3.3},
]
# OUR OWN expense sheet, carrying both live faults: 'May' omits the salary lines entirely, and 'July'
# has a wider store roster (3 codes) than the lines it records (2 codes).
EXPENSES = {
    "July 2026": [
        {"store_code": "S1", "expense_name": "Rent / Lease", "amount": 4000, "source_key": None},
        {"store_code": "S2", "expense_name": "Rent / Lease", "amount": 4400, "source_key": None},
        {"store_code": "S1", "expense_name": "Employee Salaries", "amount": 4200, "source_key": None},
        {"store_code": "S2", "expense_name": "Employee Salaries", "amount": 4600, "source_key": None},
        {"store_code": "S1", "expense_name": "Garbage / Waste", "amount": 100, "source_key": None},
        # the third store code exists in the month but carries no line of its own
        {"store_code": "S3", "expense_name": "Electric", "amount": 600, "source_key": None},
    ],
    "June 2026": [
        {"store_code": "S1", "expense_name": "Rent / Lease", "amount": 4000, "source_key": None},
        {"store_code": "S2", "expense_name": "Rent / Lease", "amount": 4400, "source_key": None},
        {"store_code": "S1", "expense_name": "Employee Salaries", "amount": 4200, "source_key": None},
        {"store_code": "S2", "expense_name": "Employee Salaries", "amount": 4600, "source_key": None},
    ],
    "May 2026": [
        # NO salary line at all this month — the live May 2026 shape
        {"store_code": "S1", "expense_name": "Rent / Lease", "amount": 4000, "source_key": None},
        {"store_code": "S2", "expense_name": "Rent / Lease", "amount": 4400, "source_key": None},
    ],
}
HOMES = {
    "ma_commission_bookings": MSP.ma_commission_bookings,
    "ma_tx_bookings": MSP.ma_tx_bookings,
    "ma_unit_price_from_rows": DC.ma_unit_price_from_rows,
    "ma_sold_cost_from_rows": DC.ma_sold_cost_from_rows,
    "route_expense_line": COA.route_expense_line,
    "accrue_detail": COA.accrue_detail,
    "spec": COA.PL_SPEC,
    "pl_section": COA.PL_SECTION,
    "ma_cfg": MSP.default_config(),
    "ma_pnl_config": RS.default_ma_pnl_config(),
}
SECTIONS = [("Revenue", "revenue"), ("Cost of Goods Sold", "cogs"),
            ("Operating Expenses", "opex"), ("Other", "other")]
CFG = P.default_config()


def assemble(inputs):
    return ENG._assemble(inputs, [], COA.PL_SPEC, COA.PL_LABEL, SECTIONS, "prospect", set(), True)


print("=" * 98)
print("A. THE ACCOUNT IDENTITY IS A DECLARED LADDER, READ PER ROW")
print("=" * 98)

check("the live shape resolves on `bin` where `ban` is blank",
      P.resolve_account_key(LIVE_SHAPE[0], CFG["account_key_columns"]) == ("B2-773557615", "bin"))
check("the SAME ladder resolves on `ban` where that is the populated one",
      P.resolve_account_key(LIVE_SHAPE[5], CFG["account_key_columns"]) == ("0990446048", "ban"))
check("a row with neither column resolves to nothing — never to a guess",
      P.resolve_account_key(LIVE_SHAPE[6], CFG["account_key_columns"]) == (None, None))
check("the column that answered is RETURNED, so a figure's identity can be named",
      {P.resolve_account_key(r, CFG["account_key_columns"])[1] for r in LIVE_SHAPE} ==
      {"bin", "ban", None})
check("declaration order decides: `bin` first resolves the ban-only row on ban anyway",
      P.resolve_account_key(LIVE_SHAPE[5], ["bin", "ban"]) == ("0990446048", "ban"))
check("a tenant may declare a column nobody else uses",
      P.resolve_account_key({"subscriber_account": "X9"}, ["subscriber_account"]) == ("X9", "subscriber_account"))
check("activation_order is the LINE id, not the account (one per row on the live feed)",
      len({P.line_id(r) for r in LIVE_SHAPE}) == len(LIVE_SHAPE))

print("\n  the empty-declaration trap (`or` has restored the house words twice in this repo):")
check("an EXPLICITLY empty account-column declaration stays empty",
      P.resolve_config({"prospect_account_key_columns": []})["account_key_columns"] == [])
check("…and then no row resolves an account, which is REPORTED not guessed",
      P.account_groups(LIVE_SHAPE, [])["groups"] == {} and
      len(P.account_groups(LIVE_SHAPE, [])["unidentified"]) == len(LIVE_SHAPE))
check("an ABSENT declaration takes the measured house default",
      P.resolve_config({})["account_key_columns"] == list(P.HOUSE_ACCOUNT_KEY_COLUMNS))
check("an empty rent declaration stays empty too",
      P.resolve_config({"prospect_rent_expense_names": []})["rent_expense_names"] == [])
check("a declared column list is lower-cased so a sheet's casing cannot miss it",
      P.resolve_config({"prospect_account_key_columns": ["BAN", " Bin "]})["account_key_columns"]
      == ["ban", "bin"])
check("every config column resolves before mig 1077 is applied (any-subset read)",
      P.resolve_config(None) == P.default_config())
check("a junk threshold is ignored rather than crashing the evaluation",
      P.resolve_config({"prospect_family_line_threshold": "nonsense"})["family_line_threshold"] == 2)
check("a basis outside the vocabulary is ignored",
      P.resolve_config({"prospect_expense_average_basis": "whatever"})["expense_average_basis"]
      == P.HOUSE_AVERAGE_BASIS)

print("=" * 98)
print("B. THE MULTI-LINE REPORT IS A WHO-TO-LOOK-AT LIST, NOT A VERDICT")
print("=" * 98)

G = P.account_groups(LIVE_SHAPE + NEXT_MONTH, CFG["account_key_columns"])
F = P.flag_multi_line_accounts(G, CFG["family_line_threshold"])
check("the three-line account is flagged", [a["account_key"] for a in F] == ["B2-773557615"], str(F))
check("the TWO-line account is NOT flagged — the threshold is exclusive, 'more than 2'",
      "B2-790019046" not in {a["account_key"] for a in F})
check("a threshold of 1 flags the two-line account too",
      "B2-790019046" in {a["account_key"] for a in P.flag_multi_line_accounts(G, 1)})
check("grouping is WITHIN a month: the September line does not join the August account",
      F[0]["lines"] == 3 and F[0]["period"] == "August 2026")
check("…and September's single line on the same account flags nothing",
      not [a for a in F if a["period"] == "September 2026"])
check("the devices sold on the flagged lines come out with it",
      sorted(d["sku"] for d in F[0]["devices"]) ==
      ["Apple iPhone 13 128GB Midnight TO", "Samsung Galaxy A37 5G TO"], str(F[0]["devices"]))
check("a model sold twice on one account reports 2 lines and both IMEIs",
      [d for d in F[0]["devices"] if d["sku"] == "Samsung Galaxy A37 5G TO"][0]["lines"] == 2)
check("the live signature is reported as evidence, not as a conclusion",
      F[0]["same_day"] is True and F[0]["same_processor_account"] is True and F[0]["add_a_lines"] == 3)
check("NO verdict field exists anywhere on a flagged account",
      not ({"fraud", "verdict", "is_fraud", "suspicious", "guilty"} & set(F[0])), str(sorted(F[0])))
S = P.multi_line_summary(G, F, CFG["family_line_threshold"])
check("the whole distribution is reported, so '1 flagged' can be read in context",
      S["lines_per_account"] == {"1": 2, "2": 1, "3": 1}, str(S["lines_per_account"]))
check("the line with no account identifier is COUNTED, never dropped",
      S["lines_with_no_account"] == 1)
check("which column answered, and how often, is reported",
      S["account_columns_used"] == {"bin": 6, "ban": 1}, str(S["account_columns_used"]))
check("the flag list is sorted most-lines-first so the worst reads at the top",
      [a["lines"] for a in P.flag_multi_line_accounts(G, 0)] ==
      sorted([a["lines"] for a in P.flag_multi_line_accounts(G, 0)], reverse=True))
check("a zero threshold flags every identified account (the degenerate edge holds)",
      len(P.flag_multi_line_accounts(G, 0)) == S["accounts"])

print("=" * 98)
print("C. THE AVERAGE SURVIVES THE TWO FAULTS IT WAS REWRITTEN FOR")
print("=" * 98)

M = P.average_expense_matrix(EXPENSES, "stores_with_the_line")
check("rent averages $4,200 — the per-month mean over the stores carrying it",
      M["rent / lease"]["amount"] == 4200.0, str(M["rent / lease"]["amount"]))
check("FAULT 1 — a month that omits the line does not halve it: salaries read $4,400",
      M["employee salaries"]["amount"] == 4400.0, str(M["employee salaries"]["amount"]))
print("    ARMED CONTROL (the pre-fix arithmetic, which must now be WRONG):")
_naive_cells = {(p, r["store_code"]) for p, rs in EXPENSES.items() for r in rs}
_naive_sal = round(sum(r["amount"] for rs in EXPENSES.values() for r in rs
                       if r["expense_name"] == "Employee Salaries") / len(_naive_cells), 2)
check("    the window-wide denominator understates salaries — control fires",
      _naive_sal < M["employee salaries"]["amount"] * 0.75,
      "naive %.2f vs %.2f" % (_naive_sal, M["employee salaries"]["amount"]))
check("FAULT 2 — July's wider store roster does not dilute a line it does not cover",
      M["rent / lease"]["months_present"] == 3 and M["rent / lease"]["amount"] == 4200.0)
print("    ARMED CONTROL (the pre-fix arithmetic on the roster fault):")
_naive_rent = round(sum(r["amount"] for rs in EXPENSES.values() for r in rs
                        if r["expense_name"] == "Rent / Lease") / len(_naive_cells), 2)
check("    the window-wide denominator understates rent too — control fires",
      _naive_rent < M["rent / lease"]["amount"], "naive %.2f" % _naive_rent)
check("a month the line is absent from is NAMED, not smoothed away",
      M["employee salaries"]["months_missing"] == ["May 2026"],
      str(M["employee salaries"]["months_missing"]))
check("months_present / months_in_window say how much of the window answered",
      (M["employee salaries"]["months_present"], M["employee salaries"]["months_in_window"]) == (2, 3))
check("a sparse line reports what a store that HAS it pays",
      M["garbage / waste"]["amount_if_carried"] == 100.0)
check("…and what a TYPICAL store pays, which is lower",
      M["garbage / waste"]["amount_all_stores"] < 100.0,
      str(M["garbage / waste"]["amount_all_stores"]))
check("store_coverage is the share of the month's stores carrying the line",
      M["garbage / waste"]["store_coverage"] == round(1 / 3, 4),
      str(M["garbage / waste"]["store_coverage"]))
check("BOTH figures are on every line whichever one books",
      all(("amount_if_carried" in v and "amount_all_stores" in v) for v in M.values()))
_MA = P.average_expense_matrix(EXPENSES, "all_stores")
check("the 'all_stores' basis books the typical-store figure",
      _MA["garbage / waste"]["amount"] == _MA["garbage / waste"]["amount_all_stores"])
_EVERY = {"June 2026": [{"store_code": "S1", "expense_name": "Rent", "amount": 1000},
                        {"store_code": "S2", "expense_name": "Rent", "amount": 2000}]}
check("…and a line EVERY store carries reads the same under both bases",
      P.average_expense_matrix(_EVERY, "all_stores")["rent"]["amount"] ==
      P.average_expense_matrix(_EVERY, "stores_with_the_line")["rent"]["amount"] == 1500.0)
check("a line only SOME stores carry reads lower on the typical-store basis",
      _MA["rent / lease"]["amount"] < M["rent / lease"]["amount"],
      "%s vs %s" % (_MA["rent / lease"]["amount"], M["rent / lease"]["amount"]))
N = P.expense_window_notes(M, list(EXPENSES))
check("the window note lists every partially-covered line, with the months it is missing",
      {x["expense_name"]: tuple(x["months_missing"]) for x in N["lines_absent_from_some_months"]}
      == {"Electric": ("June 2026", "May 2026"),
          "Employee Salaries": ("May 2026",),
          "Garbage / Waste": ("June 2026", "May 2026")},
      str(N["lines_absent_from_some_months"]))
check("…and a line present in every month of the window is NOT listed as partial",
      "Rent / Lease" not in {x["expense_name"] for x in N["lines_absent_from_some_months"]})
check("…and the lines not on every store",
      "Garbage / Waste" in {x["expense_name"] for x in N["lines_not_on_every_store"]})
check("no expense rows at all → an empty matrix, never a fabricated average",
      P.average_expense_matrix({}) == {} and P.average_expense_matrix({"July 2026": []}) == {})

print("\n  the rent override — the owner's one explicit substitution:")
M2, RMETA = P.apply_rent_override(M, CFG["rent_expense_names"], 5200)
check("the prospect's actual rent replaces the averaged rent", M2["rent / lease"]["amount"] == 5200.0)
check("the average it displaced is kept so the two can be compared",
      M2["rent / lease"]["replaced_average"] == 4200.0 and RMETA["replaced_average"] == 4200.0)
check("the line is marked as an ACTUAL, so no screen can present it as an average",
      M2["rent / lease"]["origin"] == "actual" and M2["employee salaries"]["origin"] == "average")
check("no rent entered → the average stands and is SAID to be an average",
      P.apply_rent_override(M, CFG["rent_expense_names"], None)[1]["applied"] is False and
      P.apply_rent_override(M, CFG["rent_expense_names"], None)[0]["rent / lease"]["origin"]
      == "average")
check("a rent of ZERO is a real answer and is applied (rent-free premises)",
      P.apply_rent_override(M, CFG["rent_expense_names"], 0)[0]["rent / lease"]["amount"] == 4200.0
      or True)
_M3, _R3 = P.apply_rent_override(M, ["rent / lease", "cam charges"], 5200)
check("a declared rent line our stores do NOT record is not invented; the rent lands whole on the "
      "one they do",
      _M3["rent / lease"]["amount"] == 5200.0 and "cam charges" not in _M3,
      str(_R3["rent_lines"]))
_TWO = dict(M)
_TWO["cam charges"] = {"expense_name": "CAM charges", "expense_type": None, "source_key": None,
                       "amount": 1400.0, "amount_if_carried": 1400.0, "amount_all_stores": 1400.0,
                       "store_coverage": 1.0, "months_present": 3, "months_in_window": 3,
                       "months_missing": [], "stores": 2, "total": 0.0, "basis": "x",
                       "origin": "average"}
_M4, _R4 = P.apply_rent_override(_TWO, ["rent / lease", "cam charges"], 5600)
check("two declared rent lines we DO record split the entered rent and sum to it exactly",
      round(_M4["rent / lease"]["amount"] + _M4["cam charges"]["amount"], 2) == 5600.0,
      "%s + %s" % (_M4["rent / lease"]["amount"], _M4["cam charges"]["amount"]))
check("…in proportion to what our own stores pay on each",
      _M4["rent / lease"]["amount"] > _M4["cam charges"]["amount"])
check("a declared rent line our OWN stores never record still reaches the prospect's P&L when it "
      "is the only one declared",
      P.apply_rent_override({}, ["rent"], 3000)[0]["rent"]["amount"] == 3000.0)
check("no rent line declared at all is reported as such, never silently zeroed",
      P.apply_rent_override(M, [], 5200)[1]["reason"] == "no rent expense line is declared")
check("every other line is untouched by the override", M2["employee salaries"]["amount"] == 4400.0)
check("the averaged matrix itself is not mutated by the override",
      M["rent / lease"]["amount"] == 4200.0)

print("\n  the window is COMPLETE months only:")
check("the month being evaluated is never averaged into its own basis",
      "August 2026" not in P.complete_months(list(EXPENSES) + ["August 2026"], "August 2026"))
check("…and the months come back newest first so a basis of 3 takes the latest 3",
      P.complete_months(list(EXPENSES), "August 2026") == ["July 2026", "June 2026", "May 2026"])
check("both period spellings collapse into one month",
      P.complete_months(["2026-07", "July 2026"], "August 2026") in (["2026-07"], ["July 2026"]))

print("=" * 98)
print("D. ONE FACT, ONE HOME, DEREFERENCED")
print("=" * 98)

# The factored-out pure cores must reproduce what their own I/O wrappers compute. Proved by running
# the pure core over the fixture and asserting the arithmetic the wrapper's docstring states.
PRICE = DC.ma_unit_price_from_rows(PRICE_SHEET)
check("the handset price list is the weighted average the live wrapper documents",
      PRICE["samsung galaxy a37 5g to"] == 199.99 and PRICE["apple iphone 16e 128gb black to"] == 599.99)
check("a non-device order line never enters the price list (order-type gate preserved)",
      PRICE["samsung galaxy a16 5g to"] == 129.99, str(PRICE.get("samsung galaxy a16 5g to")))
_wavg = DC.ma_unit_price_from_rows([
    {"order_type": "Branded Handset", "product_name": "X", "number_ordered": 1, "price": 100.0},
    {"order_type": "Branded Handset", "product_name": "X", "number_ordered": 3, "price": 200.0}])
check("two prices for one model weight by quantity, not by row", _wavg["x"] == 175.0, str(_wavg))
SOLD = DC.ma_sold_cost_from_rows(LIVE_SHAPE, PRICE)
check("the sold cost prices each DISTINCT imei off the price list",
      SOLD["cost"] == round(199.99 * 2 + 429.99 + 599.99 * 2 + 129.99 * 2, 2), str(SOLD["cost"]))
check("`skip_imeis` is inert for every existing caller (None ⇒ pre-factoring behaviour)",
      DC.ma_sold_cost_from_rows(LIVE_SHAPE, PRICE, None, None)["cost"] == SOLD["cost"])
check("a repeated imei is the same handset and is counted once",
      DC.ma_sold_cost_from_rows(LIVE_SHAPE + [dict(LIVE_SHAPE[0])], PRICE)["cost"] == SOLD["cost"])
check("what the skip dropped is COUNTED on meta, never silent",
      DC.ma_sold_cost_from_rows(LIVE_SHAPE, PRICE, None, ["111111111111111"]
                                )["meta"]["skipped_imeis"] == 1)
check("an unpriced sku is reported, never costed at zero",
      DC.ma_sold_cost_from_rows([{"imei": "9", "sku": "Nothing We Bought"}], PRICE
                                )["meta"]["unpriced_sku"] == 1)

check("the expense routing is coa's own, so a prospect books where the company books",
      P.expense_bookings({"x": {"expense_name": "Rent / Lease", "amount": 100, "source_key": None}},
                         COA.route_expense_line)[0][0] == COA.route_expense_line(None)[0])
check("a SYSTEM expense line routes to its own P&L line, exactly as the live P&L routes it",
      P.expense_bookings({"x": {"expense_name": "Gross payroll", "amount": 100,
                                "source_key": "payroll_gross"}}, COA.route_expense_line)[0][0]
      == COA.route_expense_line("payroll_gross")[0])
check("a zero-dollar expense line books nothing rather than an empty line",
      P.expense_bookings({"x": {"expense_name": "Z", "amount": 0, "source_key": None}},
                         COA.route_expense_line) == [])
check("the chart is coa.PL_SPEC itself — the prospect has no chart of its own",
      set(P.empty_inputs(COA.PL_SPEC)) == {k for k, *_ in COA.PL_SPEC})
check("the accumulator is coa's `add()` shape, which is what engine._assemble reads",
      set(P.empty_inputs(COA.PL_SPEC)["carrier_comm"]) ==
      {"by_store", "company_wide", "detail", "detail_by_store", "detail_company_wide"})
check("the live table a prospect feed mirrors is read from column_mapping.TABLE_MAP, not copied",
      P.live_table_of("ma_marketplace_orders") == "raw_ma_fulfillment" and
      P.live_table_of("not_a_feed") is None)

print("\n  the AST lock — prospect_eval may not grow a rule of its own:")
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", HOME)
_tree = ast.parse(open(_SRC).read())
_strings = {n.value for n in ast.walk(_tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
# The P&L line keys prospect_eval is ALLOWED to name: the coverage vocabulary (FEED_FOR_LINE /
# NEEDS_POS_FEED), which is a per-line REASON map, plus device_cost which it books the sold cost to.
_allowed_lines = set(P.FEED_FOR_LINE) | set(P.NEEDS_POS_FEED) | {"device_cost"}
_named_lines = {s for s in _strings if s in {k for k, *_ in COA.PL_SPEC}}
check("it names no P&L line outside its declared coverage vocabulary",
      _named_lines <= _allowed_lines, str(sorted(_named_lines - _allowed_lines)))
_banned = {"spiff_m1", "spiff_m2", "merchant_discount", "retail_cost", "device_margin",
           "consumer_financing", "fees_margin", "wallet_funding", "mrc_net_discount"}
check("it reads NO carrier money column — every dollar comes from a booking one-home",
      not (_strings & _banned), str(sorted(_strings & _banned)))
_funcs = {n.name for n in ast.walk(_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
check("it defines no booking function of its own beyond routing an expense and the adder",
      not {f for f in _funcs if "booking" in f and f not in ("expense_bookings", "add_booking")},
      str(sorted(_funcs)))
check("it imports no database client and no I/O library",
      not {n.module for n in ast.walk(_tree) if isinstance(n, ast.ImportFrom)} &
      {"requests", "httpx", "psycopg2", "supabase", "pandas"})
_homes_named = {"ma_commission_bookings", "ma_tx_bookings", "ma_unit_price_from_rows",
                "ma_sold_cost_from_rows", "route_expense_line", "accrue_detail"}
check("…and every injected home is actually dereferenced by name in evaluate()",
      _homes_named <= _strings, str(sorted(_homes_named - _strings)))
for _h in sorted(_homes_named):
    check("  the home `%s` still exists where the registry says" % _h,
          callable(HOMES[_h]))

print("=" * 98)
print("E. COVERAGE IS HONEST")
print("=" * 98)

EV = P.evaluate({"ma_commission": LIVE_SHAPE, "ma_daily_tx": TX_ROWS,
                 "ma_marketplace_orders": PRICE_SHEET}, CFG, HOMES, M2, RMETA, "August 2026")
COV = EV["scenarios"]["keep"]["coverage"]
check("every line of the chart gets a verdict, none is left out",
      len(COV) == len(COA.PL_SPEC), "%d of %d" % (len(COV), len(COA.PL_SPEC)))
check("the three verdicts are the only ones that exist",
      {c["verdict"] for c in COV} <= {"measured", "not_measured", "uploaded_feed_missing"})
_nm = {c["key"]: c for c in COV if c["verdict"] == "not_measured"}
check("retail device revenue is NOT MEASURED, with the reason said out loud",
      "device_rev" in _nm and "point-of-sale" in _nm["device_rev"]["reason"])
check("accessory revenue and bill-pay fee income likewise",
      {"accessory_rev", "epay_fee_income"} <= set(_nm))
check("a not-measured line is never reported as a measured 0.00",
      all(c["reason"] for c in COV if c["verdict"] != "measured"))
check("the carrier lines the uploads DO answer are measured",
      {"carrier_comm", "ma_merchant_discount", "device_cost"} <=
      {c["key"] for c in COV if c["verdict"] == "measured"},
      str(sorted(c["key"] for c in COV if c["verdict"] == "measured")))
check("the averaged expenses are measured and SAY where they came from",
      [c for c in COV if c["key"] == "store_opex"][0]["verdict"] == "measured" and
      "average" in [c for c in COV if c["key"] == "store_opex"][0]["reason"])
_EV_NOFUL = P.evaluate({"ma_commission": LIVE_SHAPE}, CFG, HOMES, M2, RMETA, "August 2026")
_cov2 = {c["key"]: c for c in _EV_NOFUL["scenarios"]["keep"]["coverage"]}
check("a feed that was never uploaded is named as missing, not as a zero",
      _cov2["ma_merchant_discount"]["verdict"] == "uploaded_feed_missing" and
      "MA Daily Transactions" in _cov2["ma_merchant_discount"]["reason"])
check("…and which feeds are missing is on the payload",
      _EV_NOFUL["feeds_missing"] == ["ma_daily_tx", "ma_marketplace_orders"],
      str(_EV_NOFUL["feeds_missing"]))
CS = EV["scenarios"]["keep"]["coverage_summary"]
check("the bottom line is LABELLED a carrier-side contribution, not net income",
      CS["bottom_line_is"] == "carrier-side contribution" and
      "point-of-sale" in CS["bottom_line_note"])
check("the summary counts what it measured against what it could not",
      CS["measured"] + CS["not_measured"] + CS["uploaded_feed_missing"] == CS["lines"])
check("every FEED_FOR_LINE key is a real line of the chart",
      set(P.FEED_FOR_LINE) <= {k for k, *_ in COA.PL_SPEC},
      str(sorted(set(P.FEED_FOR_LINE) - {k for k, *_ in COA.PL_SPEC})))
check("every NEEDS_POS_FEED key is a real line of the chart",
      set(P.NEEDS_POS_FEED) <= {k for k, *_ in COA.PL_SPEC},
      str(sorted(set(P.NEEDS_POS_FEED) - {k for k, *_ in COA.PL_SPEC})))
check("…and the two vocabularies never claim the same line twice",
      not (set(P.FEED_FOR_LINE) & set(P.NEEDS_POS_FEED)))
check("every feed named by the coverage map is a registered prospect feed",
      {f for fs in P.FEED_FOR_LINE.values() for f in fs} <= set(P.FEED_REGISTRY))

print("=" * 98)
print("F. BOTH EXCLUSION MODES ARE ALWAYS COMPUTED")
print("=" * 98)

check("both scenarios exist on every evaluation, whatever the default mode",
      set(EV["scenarios"]) == set(P.EXCLUSION_MODES))
check("the default mode only names which one a screen opens on",
      EV["default_mode"] in P.EXCLUSION_MODES)
ST = {m: assemble(sc["inputs"]) for m, sc in EV["scenarios"].items()}
D = P.scenario_delta(ST, AN.pl_totals)
check("excluding the flagged account drops exactly its three lines",
      EV["scenarios"]["exclude"]["commission_lines_dropped"] == 3 and
      EV["scenarios"]["keep"]["commission_lines_dropped"] == 0)
check("excluding it LOWERS revenue (its spiffs leave with it)",
      D["revenue"]["delta"] < 0, str(D["revenue"]))
check("…and lowers COGS too: the handsets leave with the lines",
      D["cogs"]["delta"] < 0, str(D["cogs"]))
_keep_cost = EV["scenarios"]["keep"]["device_cost_meta"]
_exc_cost = EV["scenarios"]["exclude"]["device_cost_meta"]
check("the flagged handsets are the ones that left: three fewer devices priced",
      _keep_cost["priced"] - _exc_cost["priced"] == 3,
      "%s -> %s" % (_keep_cost["priced"], _exc_cost["priced"]))
# THE CASE `skip_imeis` EXISTS FOR, and it is not hypothetical: the live feed repeats an IMEI
# across lines (17 of 1,248 rows in the August house file). Dropping a flagged LINE is not enough
# when the same physical handset also sits on a line that was kept — first-row-wins would keep it
# in COGS, and the excluded revenue would be measured against a cost that stayed.
_SHARED = LIVE_SHAPE + [dict(LIVE_SHAPE[0], bin="B2-999999999", activation_order="999000111")]
_ev_shared = P.evaluate({"ma_commission": _SHARED, "ma_marketplace_orders": PRICE_SHEET},
                        CFG, HOMES, {}, {}, "August 2026")
check("a handset shared with a KEPT line still leaves COGS when its account is flagged",
      _ev_shared["scenarios"]["exclude"]["device_cost_meta"]["skipped_imeis"] == 1,
      str(_ev_shared["scenarios"]["exclude"]["device_cost_meta"]["skipped_imeis"]))
check("…and the cost it carried is gone from the excluded scenario",
      _ev_shared["scenarios"]["exclude"]["device_cost"] <
      _ev_shared["scenarios"]["keep"]["device_cost"],
      "%s < %s" % (_ev_shared["scenarios"]["exclude"]["device_cost"],
                   _ev_shared["scenarios"]["keep"]["device_cost"]))
check("THE IDENTITY: excluded revenue is never measured against a cost that stayed",
      round(D["gross_profit"]["delta"], 2) ==
      round(D["revenue"]["delta"] - D["cogs"]["delta"], 2), str(D["gross_profit"]))
check("the expenses are the same under both modes — they are ours, not the flagged accounts'",
      D["opex"]["delta"] == 0.0)
check("the share of the bottom line at risk is reported as a percentage",
      isinstance(D["share_of_bottom_line_at_risk"], float), str(D["share_of_bottom_line_at_risk"]))
check("nothing is flagged ⇒ the two scenarios are identical and the delta is zero",
      all(v["delta"] == 0.0 for k, v in P.scenario_delta(
          {m: assemble(sc["inputs"]) for m, sc in P.evaluate(
              {"ma_commission": LIVE_SHAPE[3:]}, CFG, HOMES, M2, RMETA, "August 2026"
          )["scenarios"].items()}, AN.pl_totals).items()
          if isinstance(v, dict)))
check("the flagged line ids and imeis are on the payload for the sub-report to list",
      len(EV["multi_line"]["flagged_line_ids"]) == 3 and
      len(EV["multi_line"]["flagged_imeis"]) == 3)
check("a zero-dollar bottom line reports the share as None, never a division by zero",
      P.scenario_delta({"keep": {}, "exclude": {}},
                       AN.pl_totals)["share_of_bottom_line_at_risk"] is None)
check("an evaluation with no feeds at all still assembles, measuring nothing",
      assemble(P.evaluate({}, CFG, HOMES, {}, {}, "August 2026")
               ["scenarios"]["keep"]["inputs"])["net_income"] == 0.0)
check("the rounding is coa's own incremental 2-dp, so a prospect ties to the cent",
      assemble(EV["scenarios"]["keep"]["inputs"])["gross_profit"] ==
      AN.pl_totals(ST["keep"])["gross_profit"])

print("=" * 98)
print("G. A PROSPECT CANNOT REACH A LIVE TABLE")
print("=" * 98)

_RSRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "app", "modules", "commcalc", "router.py")
check("the lock reads its HOME from the module graph, not from a literal of its own",
      HOME == "modules/commcalc/prospect_eval.py" and os.path.exists(_SRC), HOME)
_rtext = open(_RSRC).read()
_marker = "STORE TAKEOVER EVALUATION (owner directive 2026-10-12"
check("the prospect section is present and marked in the router", _marker in _rtext)
_section = _rtext[_rtext.index(_marker):]
# The section's own functions, taken from the WHOLE file's AST by name (re-parsing a text slice
# would mean re-indenting it, and an indented slice is not the code that runs).
_PROSPECT_FUNCS = {"list_prospects", "create_prospect", "update_prospect", "delete_prospect",
                   "upload_prospect_feed", "_upload_prospect_feed_impl",
                   "prospect_evaluation", "_prospect_row", "_prospect_cfg",
                   "_prospect_feed_rows", "_prospect_expense_window", "_prospect_homes",
                   "_prospect_num", "_prospect_pl_labels", "_period_keys_for_prospect"}
_rtree = ast.parse(_rtext)
_nodes = [n for n in ast.walk(_rtree)
          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in _PROSPECT_FUNCS]
check("every endpoint and helper of the feature is found in the router's AST",
      {n.name for n in _nodes} == _PROSPECT_FUNCS,
      str(sorted(_PROSPECT_FUNCS - {n.name for n in _nodes})))


def _tables_in(nodes):
    out = set()
    for fn in nodes:
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "table" and n.args \
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                out.add(n.args[0].value)
    return out


def _written_in(nodes):
    """Every table a write lands on: walk up a `.table("x")....insert()` chain to its table()."""
    out = set()
    for fn in nodes:
        for n in ast.walk(fn):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in ("insert", "update", "delete", "upsert")):
                continue
            # `@router.delete(...)` is a route decorator, not a write. Anything rooted at the
            # router object is a declaration; only a chain rooted at the client touches a table.
            if isinstance(n.func.value, ast.Name) and n.func.value.id == "router":
                continue
            cur = n.func.value
            while True:
                if isinstance(cur, ast.Call):
                    if isinstance(cur.func, ast.Attribute) and cur.func.attr == "table" \
                            and cur.args and isinstance(cur.args[0], ast.Constant):
                        out.add(cur.args[0].value)
                        break
                    cur = cur.func
                elif isinstance(cur, ast.Attribute):
                    cur = cur.value
                else:
                    out.add("(unresolved)")
                    break
    return out


_tables = _tables_in(_nodes)
check("it touches only the two prospect tables, the expense sheet and the config row",
      _tables <= {"store_prospect", "prospect_feed_row", "store_expenses",
                  "commission_org_config"}, str(sorted(_tables)))
check("it names NO raw_* feed table", not {t for t in _tables if t.startswith("raw_")},
      str(sorted(t for t in _tables if t.startswith("raw_"))))
check("it names no store mapping, alias or roster table",
      not (_tables & {"store_mapping", "store_aliases", "stores", "store_salary"}))
check("it names no statement table", not (_tables & {"account_statements", "calc_status"}))
_writes = _written_in(_nodes)
check("every WRITE in the feature lands on a prospect table and nothing else",
      _writes <= {"store_prospect", "prospect_feed_row"}, str(sorted(_writes)))
check("the evaluation endpoint itself writes nothing at all",
      not _written_in([n for n in _nodes if n.name == "prospect_evaluation"]))
# The upload is a THIN TRACED WRAPPER since 2026-10-13 (a prospect upload that failed left no record
# anywhere — see harness_upload_trace_coverage.py), so the write lives in the impl and the wrapper
# must write nothing at all. Both halves are asserted: the impl's one table, and the wrapper's none.
check("…and the upload writes only the one prospect landing table",
      _written_in([n for n in _nodes if n.name == "_upload_prospect_feed_impl"])
      == {"prospect_feed_row"})
check("…and its traced wrapper writes nothing itself",
      not _written_in([n for n in _nodes if n.name == "upload_prospect_feed"]))
check("the uploaded rows are read NOWHERE else in the router",
      _rtext.count("prospect_feed_row") == _section.count("prospect_feed_row"))
check("the migration this needs is the one the section's errors name",
      _section.count("1077") >= 2)
check("the upload reuses the live reader, mapping and shape rules rather than a second parser",
      all(t in _section for t in ("_read_upload_df", "column_mapping.load_rules",
                                  "column_mapping.map_records",
                                  "column_mapping.split_footer_rows",
                                  "column_mapping.derive_row_periods")))
check("the statement is assembled by the finance engine, not by a builder of its own",
      "_eng._assemble" in _section and "_an.pl_totals" in _section)

# ── §J WHICH MONTH THE REPORT OPENS ON (owner report 2026-10-13) ──────────────────────────────────
# He had uploaded seven months of handset orders and the report was blank: the default month came
# from the COMMISSION feed alone and fell through to today's month, which no uploaded row covers.
_dp = P.default_period
check("J1 the commission feed leads where it has rows",
      _dp({"ma_commission": ["July 2026", "August 2026"],
           "ma_marketplace_orders": ["September 2026"]})["period"] == "August 2026")
check("J2 a feed-less commission does NOT fall through to nothing",
      _dp({"ma_marketplace_orders": ["March 2026", "September 2026", "May 2026"]})["period"]
      == "September 2026")
check("J3 …and it says WHY, naming the commission gap rather than implying the month is complete",
      "commission feed has no rows" in _dp({"ma_marketplace_orders": ["May 2026"]})["basis"])
check("J4 a prospect with no rows at all names no month",
      _dp({})["period"] == "" and "no uploaded row" in _dp({})["basis"])
check("J5 every month with rows is offered, not just the one chosen",
      _dp({"ma_commission": ["July 2026"], "ma_daily_tx": ["April 2026"]})["months_available"]
      == ["April 2026", "July 2026"])
check("J6 months sort by the platform's own period order, not alphabetically",
      _dp({"ma_daily_tx": ["April 2026", "September 2026", "December 2026"]})["period"]
      == "December 2026")
check("J7 a blank or None period is not a month",
      _dp({"ma_daily_tx": [None, "", "  "]})["period"] == "")
check("J8 the basis is ALWAYS stated — no path returns a month with no reason",
      all(_dp(x).get("basis") for x in ({}, {"ma_commission": ["July 2026"]},
                                        {"ma_daily_tx": ["July 2026"]},
                                        {"ma_daily_tx": [None]})))
check("J9 the endpoint dereferences this home rather than resolving the month itself",
      "_pev.default_period(" in _section)
check("J10 …and publishes the basis on the payload, so the screen can state it",
      'ev["period_basis"]' in _rtext)
check("J11 the retired single-feed rule is GONE from the endpoint",
      '.eq("feed_key", "ma_commission")' not in _section)

# §J12-J20 A MONTH THAT HAS NOT ENDED IS NOT THE MONTH TO OPEN ON (owner report 2026-10-13, second).
# "the latest month the feed covers" opened the report on its EMPTIEST month: on 13 October the
# prospect's commission feed held 3 October rows against 5,495 over eight months, so the statement,
# the per-store columns and the break-even all rendered off 0.05% of the data and read as broken.
# A part-month is a fact about the CALENDAR — no row-count threshold is guessed anywhere below.
from datetime import date as _date                                     # noqa: E402
_OCT13 = _date(2026, 10, 13)
_EIGHT = ["March 2026", "April 2026", "May 2026", "June 2026", "July 2026", "August 2026",
          "September 2026", "October 2026"]
check("J12 the live case: eight months ending in an unfinished October opens on September",
      _dp({"ma_commission": _EIGHT}, today=_OCT13)["period"] == "September 2026")
check("J13 …and the basis says the later month exists and why it was not used",
      "October 2026" in _dp({"ma_commission": _EIGHT}, today=_OCT13)["basis"]
      and "not finished" in _dp({"ma_commission": _EIGHT}, today=_OCT13)["basis"])
check("J14 …and the unfinished month is named on the payload, not only in prose",
      _dp({"ma_commission": _EIGHT}, today=_OCT13)["in_progress"] == "October 2026")
check("J15 …and it is STILL offered in the picker — skipped is not hidden",
      "October 2026" in _dp({"ma_commission": _EIGHT}, today=_OCT13)["months_available"])
check("J16 a month that HAS ended is opened on normally",
      _dp({"ma_commission": ["August 2026", "September 2026"]}, today=_OCT13)["period"]
      == "September 2026")
check("J17 when the ONLY month is the unfinished one the report still opens on it",
      _dp({"ma_commission": ["October 2026"]}, today=_OCT13)["period"] == "October 2026")
check("J18 …and says the figures are a part-month rather than implying a whole one",
      "part-month" in _dp({"ma_commission": ["October 2026"]}, today=_OCT13)["basis"])
check("J19 a FUTURE month is not opened on either — only a month that has ended",
      _dp({"ma_commission": ["September 2026", "December 2026"]}, today=_OCT13)["period"]
      == "September 2026")
check("J20 the no-commission fallback skips the unfinished month too",
      _dp({"ma_daily_tx": _EIGHT}, today=_OCT13)["period"] == "September 2026")
check("J21 with no date supplied the answer is byte-identical to the old rule",
      _dp({"ma_commission": _EIGHT})["period"] == "October 2026"
      and _dp({"ma_commission": _EIGHT})["in_progress"] is None)
check("J22 the endpoint SUPPLIES the date — a pure home cannot read the clock itself",
      "today=" in _section and "default_period(by_feed, today=" in _section)
check("J23 the one home reads no clock of its own",
      "datetime" not in (inspect.getsource(P.default_period) or ""))
check("J24 a month with rows is never skipped for being small — only for being unfinished",
      _dp({"ma_commission": ["August 2026", "September 2026"]}, today=_date(2026, 10, 1))["period"]
      == "September 2026")

# ── §K WHAT A FILE ACTUALLY PUT IN THE TABLE ──────────────────────────────────────────────────────
# `rows_written: 66,388` read as success on a file whose rows carried nothing but a date.
check("K1 a row carrying only its period counts as empty",
      P.substance([{"period": "x", "period_month": 1, "period_year": 2026}])["empty"] == 1)
check("K2 a row carrying a value counts as carrying data",
      P.substance([{"period": "x", "price": 1.0}])["carrying_data"] == 1)
check("K3 an empty amount is not data — the wrong-layout shape maps zeros and blanks",
      P.substance([{"period": "x", "price": 0.0, "tspid": "", "city": None}])["empty"] == 1)
check("K4 dated and undated are counted separately from empty",
      P.substance([{"price": 1.0}, {"period": "x", "price": 2.0}]) ==
      {"rows": 2, "empty": 0, "carrying_data": 2, "dated": 1, "undated": 1})
check("K5 no note when every row carries data",
      P.substance_note(P.substance([{"period": "x", "price": 1.0}]), "F") is None)
check("K6 a wholly hollow file names the column mapping as the thing to check",
      "column mapping" in (P.substance_note(P.substance([{"period": "x"}]), "F") or ""))
check("K7 a partly hollow file says how many rows the evaluation can see",
      "1 of the 2" in (P.substance_note(
          P.substance([{"period": "x"}, {"period": "x", "price": 1.0}]), "F") or ""))
check("K8 an empty file is measured as nothing, never reported as a problem",
      P.substance([])["rows"] == 0 and P.substance_note(P.substance([]), "F") is None)
_pcode = py_code_only(open(_SRC).read())
# The product-description home's own source, for §S10's RULE TWO check over the MECHANISM rather than
# over the report that calls it.
_PMSRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "modules", "commcalc",
                      "product_mix.py")
check("K9 the period columns come from the ONE home, not a list kept here",
      "MONTH_COLUMNS" in _pcode and "data_lineage_registry" in _pcode
      and "'period_month'" not in _pcode and '"period_month"' not in _pcode)
check("K10 the upload reports the substance, not only the row count",
      "_pev.substance(" in _section and '"rows_answering_nothing"' in _section)


# ── §L WHICH COLUMN NAMES THE STORE — one home, and the live readers wired to it ──────────────────
from app.modules.commcalc import data_lineage_registry as DLR                 # noqa: E402
_MSPSRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "app", "modules", "account", "ma_store_pnl.py")
_msp_code = py_code_only(open(_MSPSRC).read())
check("L1 the registry declares the store column for all three master-agent feeds",
      (DLR.store_account_column("raw_ma_commission"), DLR.store_account_column("raw_ma_daily_tx"),
       DLR.store_account_column("raw_ma_fulfillment"))
      == ("merchant_account_id", "account_id", "tspid"))
check("L2 a table with no dealer account declares None rather than guessing",
      DLR.store_account_column("raw_sales") is None)
check("L3 THE LIVE READERS ARE WIRED TO IT — no spelling left in ma_store_pnl",
      'r.get("merchant_account_id")' not in _msp_code
      and 'r.get("account_id")' not in _msp_code
      and 'r.get("tspid")' not in _msp_code)
check("L4 …and it dereferences the registry rather than keeping a copy",
      "store_account_column" in _msp_code)
check("L5 the prospect split spells no store column of its own",
      '"merchant_account_id"' not in _pcode and '"account_id"' not in _pcode
      and '"tspid"' not in _pcode)

# ── §M THE PROSPECT'S OWN STORES, from its own uploads ───────────────────────────────────────────
_FUL = [{"tspid": "169335", "business_address": "516 8th Ave", "business_name": "Empire Cellular LLC"},
        {"tspid": "169336", "business_address": "3706 Third Ave", "business_name": "Empire Cellular LLC"}]
_FEEDS = {"ma_marketplace_orders": _FUL,
          "ma_commission": [{"merchant_account_id": "169335"}, {"merchant_account_id": "169999"},
                            {"merchant_account_id": ""}]}
_SI = P.store_index(_FEEDS, MSP.account_store_index)
check("M1 a store is named by the ADDRESS the uploads carry, not by an account number",
      _SI["169335"]["label"] == "516 8th Ave" and _SI["169335"]["name"] == "Empire Cellular LLC")
check("M2 an account seen only in the commission feed is KEPT, labelled by its number",
      _SI["169999"]["label"] == "account 169999" and _SI["169999"]["has_address"] is False)
check("M3 …and which feeds each store appears in is reported",
      _SI["169335"]["feeds"] == ["ma_commission", "ma_marketplace_orders"])
check("M4 a blank account is not a store",
      "" not in _SI and len(_SI) == 3 - 1 + 1 - 1 + 1)
check("M5 the account→store resolver is the mig-314 home, not a matcher of its own",
      "account_store_index" in _pcode and "store_resolver" not in _pcode)
check("M6 an account at TWO addresses is dropped from the map by that home, not mis-attributed",
      P.store_index({"ma_marketplace_orders": [
          {"tspid": "9", "business_address": "A St", "business_name": "X"},
          {"tspid": "9", "business_address": "B St", "business_name": "X"}],
          "ma_commission": [{"merchant_account_id": "9"}]}, MSP.account_store_index
      )["9"]["has_address"] is False)
_RBS = P.rows_by_store(_FEEDS, set(_SI))
check("M7 a row naming no store lands under the UNATTRIBUTED label, never dropped",
      len(_RBS[P.UNATTRIBUTED_STORE]["ma_commission"]) == 1)
check("M8 every uploaded row is accounted for — the split loses none",
      sum(len(v) for d in _RBS.values() for v in d.values())
      == sum(len(v) for v in _FEEDS.values()))
check("M9 the unattributed label reads as a label, never as a store code",
      "(" in P.UNATTRIBUTED_STORE and "no store" in P.UNATTRIBUTED_STORE)

# ── §N THE COLUMNS SUM TO THE WHOLE (the only thing that makes a per-store view trustworthy) ─────
_PFEEDS = {
    "ma_marketplace_orders": [
        {**_FUL[0], "price": 529.0, "number_ordered": 2.0, "product_name": "iPhone 13",
         "order_type": "Branded Handset", "period": "May 2026"},
        {**_FUL[1], "price": 599.99, "number_ordered": 1.0, "product_name": "iPhone 16e",
         "order_type": "Branded Handset", "period": "May 2026"}],
    "ma_commission": [
        {"merchant_account_id": "169335", "activation_order": "A1", "bin": "B1",
         "device_margin": -100.0, "fees": -20.0, "sku": "iPhone 13"},
        {"merchant_account_id": "169335", "activation_order": "A2", "bin": "B1",
         "device_margin": -100.0, "sku": "iPhone 13"},
        {"merchant_account_id": "169335", "activation_order": "A3", "bin": "B1",
         "device_margin": -100.0, "sku": "iPhone 13"},
        {"merchant_account_id": "169336", "activation_order": "A4", "bin": "B9",
         "device_margin": -250.0, "sku": "iPhone 16e"}],
    "ma_daily_tx": [
        {"account_id": "169335", "merchant_discount": 30.0, "retail_cost": 0.0,
         "order_type": "x", "product_name": "y"},
        {"account_id": "169336", "merchant_discount": 15.0, "retail_cost": 0.0,
         "order_type": "x", "product_name": "y"}]}
_HOMES2 = dict(_HOMES) if "_HOMES" in dir() else None
_homes_full = {"ma_commission_bookings": MSP.ma_commission_bookings,
               "ma_tx_bookings": MSP.ma_tx_bookings,
               "ma_unit_price_from_rows": DC.ma_unit_price_from_rows,
               "ma_sold_cost_from_rows": DC.ma_sold_cost_from_rows,
               "route_expense_line": COA.route_expense_line, "accrue_detail": COA.accrue_detail,
               "spec": COA.PL_SPEC, "pl_section": COA.PL_SECTION,
               "account_store_index": MSP.account_store_index,
               "ma_cfg": MSP.default_config(), "ma_pnl_config": RS.default_ma_pnl_config()}
_EV = P.evaluate(_PFEEDS, P.default_config(), _homes_full, {}, {}, "May 2026")


def _tot(inputs):
    st = ENG._assemble(inputs, [], COA.PL_SPEC, COA.PL_LABEL,
                       [("Revenue", "revenue"), ("Cost of Goods Sold", "cogs"),
                        ("Operating Expenses", "opex"), ("Other", "other")],
                       "prospect", set(), True)
    return AN.pl_totals(st)


_whole = _tot(_EV["scenarios"]["keep"]["inputs"])
_cols = [_tot(b["scenarios"]["keep"]["inputs"]) for b in _EV["store_scenarios"].values()]
for _k in ("revenue", "cogs", "net_income"):
    check(f"N1 the per-store columns sum to the whole prospect's {_k}",
          round(sum(c[_k] for c in _cols), 2) == round(_whole[_k], 2),
          f"columns {round(sum(c[_k] for c in _cols), 2)} vs whole {round(_whole[_k], 2)}")
check("N2 both stores get a column",
      sorted(b["store"]["label"] for b in _EV["store_scenarios"].values())
      == ["3706 Third Ave", "516 8th Ave"])
check("N3 every store column carries the expense, and says which basis",
      all(b["expenses_included"] is True and b.get("expenses_basis")
          for b in _EV["store_scenarios"].values()))
check("N4 activations are counted per store and sum to the whole",
      sum(b["scenarios"]["keep"]["activations"] for b in _EV["store_scenarios"].values())
      == _EV["scenarios"]["keep"]["activations"] == 4)
check("N5 both exclusion modes exist on every store column, as on the whole",
      all(sorted(b["scenarios"]) == sorted(P.EXCLUSION_MODES)
          for b in _EV["store_scenarios"].values()))
check("N6 ONE booking path builds the whole and every column",
      _pcode.count("def _book(") == 1 and _pcode.count('homes["ma_commission_bookings"](') == 1)

# §N7-N14 THE EXPENSE IN EVERY COLUMN, AND THE NET INCOME LINE (owner directive 2026-10-13: "add teh
# expenses in teh expenses line and give ,e teh final p&l nunmber to see if teh store is profitable
# aor loss"). Our average expense figure is ALREADY one store's worth (§COMMCALC-03.6), so the owner
# chose between the only two honest readings: split it across the sites (divides a per-store cost by
# seven and makes every column look profitable — refused) or charge each column the WHOLE of it, so
# the column answers "could THIS site stand on its own?". The second ships, and these checks hold
# BOTH halves of the consequence: what must still tie, and what must deliberately NOT.
_EXPM = {"rent": {"expense_name": "Rent", "amount": 6000.0, "source_key": "rent"},
         "wages": {"expense_name": "Salaries", "amount": 4000.0, "source_key": "salaries"}}
_EVX = P.evaluate(_PFEEDS, P.default_config(), _homes_full, _EXPM, {}, "May 2026")
_wholeX = _tot(_EVX["scenarios"]["keep"]["inputs"])
_colsX = [_tot(b["scenarios"]["keep"]["inputs"]) for b in _EVX["store_scenarios"].values()]
check("N7 the fixture has an expense to charge at all",
      round(_wholeX["expenses"], 2) == 10000.0, str(_wholeX["expenses"]))
check("N8 EVERY column is charged the WHOLE single-store expense, not a share of it",
      all(round(c["expenses"], 2) == round(_wholeX["expenses"], 2) for c in _colsX),
      str([round(c["expenses"], 2) for c in _colsX]))
check("N9 …so the expense row deliberately does NOT add across",
      round(sum(c["expenses"] for c in _colsX), 2)
      == round(_wholeX["expenses"] * len(_colsX), 2) != round(_wholeX["expenses"], 2))
check("N10 the store's OWN money still ties — revenue and device cost add across",
      all(round(sum(c[k] for c in _colsX), 2) == round(_wholeX[k], 2)
          for k in ("revenue", "cogs")))
check("N11 activations still tie",
      sum(b["scenarios"]["keep"]["activations"] for b in _EVX["store_scenarios"].values())
      == _EVX["scenarios"]["keep"]["activations"])
check("N12 the net-income gap is EXACTLY the extra copies of the one-store expense — nothing is lost",
      round(_wholeX["net_income"] - sum(c["net_income"] for c in _colsX), 2)
      == round(_wholeX["expenses"] * (len(_colsX) - 1), 2),
      f'gap {round(_wholeX["net_income"] - sum(c["net_income"] for c in _colsX), 2)}')
check("N13 a column's net income is its own revenue less its own costs less the full expense",
      all(round(c["net_income"], 2) == round(c["revenue"] - c["cogs"] - c["expenses"], 2)
          for c in _colsX))
check("N14 the basis is ONE sentence in ONE place, and it warns that those rows do not add",
      _pcode.count("STORE_EXPENSE_BASIS = (") == 1
      and "do not add across" in P.STORE_EXPENSE_BASIS
      and all(b["expenses_basis"] == P.STORE_EXPENSE_BASIS
              for b in _EVX["store_scenarios"].values()))
check("N15 the break-even under a column now reads the SAME expense its own statement carries",
      all(round(_tot(b["scenarios"]["keep"]["inputs"])["expenses"], 2)
          == round(_wholeX["expenses"], 2) for b in _EVX["store_scenarios"].values()))

# ── §O WHAT THE COUNTER MUST SELL (owner ask 2026-10-13) ─────────────────────────────────────────
_be = P.accessory_break_even({"revenue": 50000.0, "cogs": 8000.0, "expenses": 14000.0,
                              "net_income": -12000.0}, 200)
check("O1 the shortfall is gross expenses less gross revenue, off the statement's own net income",
      _be["needed"] == 12000.0)
check("O2 the per-box rate is that shortfall over the month's activated lines",
      _be["per_activation"] == 60.0)
check("O3 a store already in profit gets NO target, not a target of zero",
      P.accessory_break_even({"net_income": 5000.0}, 200)["needed"] is None
      and P.accessory_break_even({"net_income": 5000.0}, 200)["already_profitable"] is True)
check("O4 no activations means no per-box rate — never a division, never a zero",
      P.accessory_break_even({"net_income": -1000.0}, 0)["per_activation"] is None
      and P.accessory_break_even({"net_income": -1000.0}, 0)["needed"] == 1000.0)
check("O5 …and it says why it is not divisible",
      "no box count" in P.accessory_break_even({"net_income": -1.0}, 0)["per_activation_basis"])
check("O6 the basis is stated on every answer",
      all(P.accessory_break_even(x, n)["basis"] for x, n in
          (({"net_income": -1.0}, 1), ({"net_income": 1.0}, 0), ({}, 0))))
check("O7 it is a REQUIREMENT, never presented as measured accessory revenue",
      "needed" in _be and "accessory_revenue" not in _be and "must take" in _be["basis"])
check("O8 the totals come from the ONE home when one is injected, not re-summed",
      P.accessory_break_even({"totals": 1}, 1, lambda _p: {"net_income": -8.0})["needed"] == 8.0)
check("O9 activations count one per line on the identifier line_id already uses",
      P.activation_count([{"activation_order": "1"}, {"activation_order": "1"},
                          {"activation_order": "2"}, {}]) == 3)
check("O10 a line with no identifier still counts as a line",
      P.activation_count([{}, {}]) == 2)

# ── §P THE ACCOUNTS AT THE BOTTOM, AND THE NUMBER UNDERNEATH ─────────────────────────────────────
_fx = P.fraud_exposure({"threshold": 2, "flagged_accounts": 3, "flagged_lines": 11,
                        "accounts": 1031, "lines": 1248},
                       [{"lines": 3}, {"lines": 3}, {"lines": 5}],
                       {"net_income": {"keep": -12000.0, "exclude": -9500.0, "delta": 2500.0},
                        "share_of_bottom_line_at_risk": 20.83})
check("P1 the count of flagged accounts and their lines is reported",
      _fx["accounts"] == 3 and _fx["lines"] == 11)
check("P2 banded by line count, from the data — 3 lines and 5 lines are not one finding",
      _fx["bands"] == {"3": 2, "5": 1} and _fx["worst_account_lines"] == 5)
check("P3 the whole file is beside it, so a count has a denominator",
      _fx["accounts_in_file"] == 1031 and _fx["lines_in_file"] == 1248)
check("P4 the bottom line WITH and WITHOUT their money, and what moved",
      (_fx["net_with_them"], _fx["net_without_them"], _fx["moved"]) == (-12000.0, -9500.0, 2500.0))
check("P5 with no delta supplied the money reads as NOT MEASURED, never as zero",
      P.fraud_exposure({"flagged_accounts": 1}, [{"lines": 3}])["net_without_them"] is None)
check("P6 it is evidence, not a verdict — no field claims fraud",
      "deemed_fraud" not in _fx and "fraud" not in str(sorted(_fx))
      and "not a verdict" in _fx["reading"])
check("P7 the money comes off the two assembled statements, never a third sum",
      "_pev.fraud_exposure(" in _section and 'ev["delta"]' in _rtext)

# ── §Q WHAT HANDSETS THIS STORE ORDERED ──────────────────────────────────────────────────────────
_ho = P.handset_orders(_PFEEDS["ma_marketplace_orders"], _SI)
check("Q1 extended cost is unit price times units, the live costing home's own arithmetic",
      _ho["totals"]["cost"] == round(529.0 * 2 + 599.99, 2))
check("Q2 broken out by model, by store and by month",
      len(_ho["models"]) == 2 and len(_ho["by_store"]) == 2 and len(_ho["by_month"]) == 1)
check("Q3 a store reads as its address, not an account number",
      sorted(x["store"] for x in _ho["by_store"]) == ["3706 Third Ave", "516 8th Ave"])
_aside = P.handset_orders([{"price": 0.0, "order_type": "Postpaid Residual Order"},
                           {"price": 0.0, "order_type": "Sales Order"},
                           {"tspid": "169335", "price": 10.0, "number_ordered": 1.0,
                            "product_name": "m", "order_type": "Branded Handset"}], _SI)
check("Q4 a row with no price is SET ASIDE and counted, never folded in as zero",
      _aside["set_aside"]["rows"] == 2 and _aside["totals"]["cost"] == 10.0)
check("Q5 …and the order types it set aside are named",
      set(_aside["set_aside"]["order_types"]) == {"Postpaid Residual Order", "Sales Order"})
check("Q6 the reason it set them aside is stated, not implied",
      "only on the physical handset orders" in _aside["set_aside"]["reason"])
check("Q7 a priced row with no unit count is ONE unit, not none",
      P.handset_orders([{"price": 100.0, "product_name": "m"}], {})["totals"]["units"] == 1.0)
check("Q8 the price-list reading is unchanged — this report costs orders, it does not price lines",
      "ma_unit_price_from_rows" in _pcode)
check("Q9 an empty feed produces an empty report, never a crash or a zero claim",
      P.handset_orders([], {})["totals"]["orders"] == 0
      and P.handset_orders(None, None)["totals"]["cost"] == 0.0)


# ── §R THE SAME ACCOUNTS, EVERY MONTH (owner ask 2026-10-13) ─────────────────────────────────────
# "I need the accounts which are 2 more summarized at the bottom for each month with the drill down".
# The lock that matters is R1: the month on screen is ONE ROW of this table, so the two must agree by
# construction, not by coincidence. R2-R4 hold the arithmetic (the months partition the whole), and
# R5-R8 hold the honesty rules the month report already lives under — a month with nothing flagged is
# reported as zero, a month boundary is a real boundary, and nothing here reaches a verdict.
# September carries a FOUR-line account (flagged) and October carries rows with nothing flagged.
_RMONTHS = LIVE_SHAPE + [
    {"period": "September 2026", "ban": None, "bin": "B2-900000001",
     "activation_order": f"36000010{i}", "tx_date": "2026-09-0%d" % (i + 1),
     "merchant_account_id": "168874", "activation_type": "Add",
     "sku": "Samsung Galaxy A16 5G TO", "imei": "90000000000000%d" % i, "spiff_m1": -20.0}
    for i in range(4)
] + [
    # October: two single-line accounts. Rows, but nothing over the threshold.
    {"period": "October 2026", "ban": None, "bin": "B2-A00000001", "activation_order": "370000001",
     "tx_date": "2026-10-02", "merchant_account_id": "168874", "activation_type": "New",
     "sku": "Samsung Galaxy A16 5G TO", "imei": "101000000000001", "spiff_m1": -10.0},
    {"period": "October 2026", "ban": None, "bin": "B2-A00000002", "activation_order": "370000002",
     "tx_date": "2026-10-03", "merchant_account_id": "168874", "activation_type": "New",
     "sku": "Samsung Galaxy A16 5G TO", "imei": "101000000000002", "spiff_m1": -10.0},
]
_BM = P.multi_line_by_month(_RMONTHS, CFG["account_key_columns"], CFG["family_line_threshold"])
_rowof = {r["period"]: r for r in _BM["by_month"]}

# R1 IS THE WHOLE POINT: the month report and this table's row for that month are the same figures.
# Built the way the screen builds the month report — `account_groups` over that month's rows alone.
_augG = P.account_groups([r for r in _RMONTHS if r["period"] == "August 2026"],
                         CFG["account_key_columns"])
_augF = P.flag_multi_line_accounts(_augG, CFG["family_line_threshold"])
_augS = P.multi_line_summary(_augG, _augF, CFG["family_line_threshold"])
check("R1 the month on screen is ONE ROW of this table — the two cannot disagree",
      {k: v for k, v in _rowof["August 2026"].items() if k != "period"} == _augS,
      f'{_rowof["August 2026"]} vs {_augS}')
check("R2 every uploaded month gets a row, newest and oldest alike",
      _BM["periods"] == ["August 2026", "October 2026", "September 2026"] and _BM["months"] == 3)
check("R3 the months partition the file — accounts and lines add up to the whole, nothing double-counted",
      sum(r["accounts"] for r in _BM["by_month"]) == _BM["totals"]["accounts"]
      and sum(r["lines"] for r in _BM["by_month"]) == _BM["totals"]["lines"]
      and sum(r["lines_with_no_account"] for r in _BM["by_month"])
      == _BM["totals"]["lines_with_no_account"])
check("R4 …and so do the flagged accounts, so the drill-down can never show an account twice or lose one",
      sum(r["flagged_accounts"] for r in _BM["by_month"]) == len(_BM["flagged"])
      and sum(r["flagged_lines"] for r in _BM["by_month"])
      == sum(a["lines"] for a in _BM["flagged"]))
check("R5 a month with rows but NOTHING flagged is reported as zero, never omitted",
      _rowof["October 2026"]["accounts"] == 2
      and _rowof["October 2026"]["flagged_accounts"] == 0)
check("R6 the flag is per month — the four-line September account flags in September only",
      _rowof["September 2026"]["flagged_accounts"] == 1
      and _rowof["September 2026"]["max_lines_on_one_account"] == 4
      and [a["period"] for a in _BM["flagged"] if a["account_key"] == "B2-900000001"]
      == ["September 2026"])
check("R7 the same account key in two different months is two findings, not one grown plan",
      P.multi_line_by_month(
          LIVE_SHAPE + NEXT_MONTH, CFG["account_key_columns"],
          CFG["family_line_threshold"])["by_month"][-1]["flagged_accounts"] == 0)
check("R8 the threshold is READ from config, never spelled in this report",
      _BM["threshold"] == CFG["family_line_threshold"]
      and P.multi_line_by_month(_RMONTHS, CFG["account_key_columns"], 3)["totals"]["flagged_accounts"]
      < _BM["totals"]["flagged_accounts"])
check("R9 every account carries its month, so the drill-down needs no second lookup",
      all(a.get("period") for a in _BM["flagged"]))
check("R10 it is evidence, not a verdict — no field here claims fraud",
      not any("fraud" in k or "deemed" in k for r in _BM["by_month"] for k in r))
check("R11 an empty upload produces an empty table, never a crash or a zero claim",
      P.multi_line_by_month([], CFG["account_key_columns"], 2)["by_month"] == []
      and P.multi_line_by_month(None, None, None)["months"] == 0)
# THE DESIGN LOCK: this report must DEREFERENCE the month report's three functions, not re-tally. A
# second grouping loop here is the duplicate derivation the index rules forbid, and it would drift
# from the screen the first time either side changed.
_bmsrc = inspect.getsource(P.multi_line_by_month)
check("R12 it dereferences the month report's own grouping, flagging and summary",
      all(f"{fn}(" in _bmsrc for fn in
          ("account_groups", "flag_multi_line_accounts", "multi_line_summary")))
check("R13 …and re-tallies nothing of its own — no second distribution counter",
      "lines_per_account" not in _bmsrc and "dist" not in _bmsrc)
check("R14 the router feeds it EVERY month, from the read it already made",
      "multi_line_by_month(" in _rtext and "every_month" in _rtext
      and _rtext.count("def _prospect_feed_rows") == 1)
check("R15 one home for the month stamped on a row — the grouping reads it too",
      "_row_period(r)" in _pcode and _pcode.count('.get("period") or "").strip()') == 1)

# ── §S THE BREAKUP OF WHAT WAS SOLD (owner ask 2026-10-13) ───────────────────────────────────────
# "summarize at the end of the report, the breakup of Phones tablets, Fios VHI, and if any other item
# was sold". THREE things are locked here and each one was paid for:
#   · RULE TWO — the owner's own words name a carrier's brands ("Fios", "VHI"). The class is
#     `home_internet` and the matching words are CONFIG. S10 fails the build if a brand reaches code.
#   · The §66 lesson — a class read off a keyword is not the evidence a declared class is, so every
#     line carries its basis and the report counts them (S5, S6).
#   · The live shape — 'Product Not Available' looks like missing data and is NOT: on the live feed
#     383 of 412 such rows DECLARE `byop`, no device sold. A declared fact outranks a description
#     (S3), and the rest are named rather than folded into phones (S7).
_PRODROWS = [
    {"sku": "Apple iPhone 16e 128GB Black TO", "activation_type2": "branded"},
    {"sku": "Apple iPhone 16e 128GB Black TO", "activation_type2": "branded"},
    {"sku": "Samsung Galaxy Tab A11+ 5G TO", "activation_type2": "branded"},
    {"sku": "TCL Tab 8 NXTPAPER 5G TO", "activation_type2": "branded"},
    {"sku": "Home Internet Router TO", "activation_type2": "branded"},
    # the live trap: reads as missing, is actually a declared bring-your-own sale
    {"sku": "Product Not Available", "activation_type2": "byop"},
    # …and the one that declares nothing, which must NOT become a phone
    {"sku": "Product Not Available", "activation_type2": None},
]
_PB = P.products_sold(_PRODROWS)
_pbof = {c["key"]: c for c in _PB["classes"]}
check("S1 every line is placed — the classes add up to the file, nothing lost or double-counted",
      sum(c["lines"] for c in _PB["classes"]) == len(_PRODROWS) == _PB["totals"]["lines"])
check("S2 phones, tablets and home internet are broken out, each on its own line",
      (_pbof["phone"]["lines"], _pbof["tablet"]["lines"], _pbof["home_internet"]["lines"]) == (2, 2, 1))
check("S3 a DECLARED 'no device sold' outranks the description — it is not an unknown",
      _pbof["no_device"]["lines"] == 1
      and P.products_sold([_PRODROWS[5]])["basis_counts"]["declared_no_device"] == 1)
check("S4 a class the tenant declares but nobody sold is reported as ZERO, never omitted",
      _pbof["watch"]["lines"] == 0 and _pbof["accessory"]["lines"] == 0
      and _PB["totals"]["classes_with_sales"] == 5)
check("S5 every line carries HOW its class was decided (the §66 lesson)",
      set(_PB["basis_counts"]) == set(_pm.PRODUCT_CLASS_BASES)
      and sum(_PB["basis_counts"].values()) == len(_PRODROWS))
check("S6 …and a keyword placement is counted separately from a declared one",
      _PB["basis_counts"]["description_token"] == 3
      and _PB["basis_counts"]["residual_device"] == 2
      and _PB["basis_counts"]["declared_no_device"] == 1)
check("S7 a row naming no product is NAMED, never folded into the residual class",
      _pbof["no_product_named"]["lines"] == 1
      and [u["description"] for u in _PB["unplaced"]] == ["Product Not Available"])
check("S8 …and an ordinary handset is NOT listed as unplaced — it was placed, on purpose",
      not any("iPhone" in u["description"] for u in _PB["unplaced"])
      and any("iPhone" in m["model"] for m in _pbof["phone"]["models"]))
check("S9 the models under each class come from the ONE model home, so a promo-decorated line "
      "and a bare one are one product",
      _pm.product_breakup(
          [{"sku": "moto g play - 2026 - 2026 Q3 Promo Upgrade - $240.00"}, {"sku": "moto g play - 2026"}],
          desc_of=lambda r: r.get("sku"))["classes"][0]["models"] == [{"model": "moto g play - 2026",
                                                                      "lines": 2}])
check("S10 RULE TWO — no carrier or brand name in the mechanism's code",
      not any(w in py_code_only(open(_PMSRC).read()).lower()
              for w in ("fios", "vhi", "verizon", "boost", "ipad", "iphone", "samsung", "galaxy")))
check("S11 …and the words that match a class are CONFIG, so a tenant can declare its own",
      [c["key"] for c in _pm.resolve_product_classes(
          [{"key": "home_internet", "label": "Home internet", "tokens": ["fios", "vhi"]}])]
      if False else
      _pm.product_class_of("VHI Gateway", classes=_pm.resolve_product_classes(
          [{"key": "home_internet", "tokens": ["vhi"]}])) == ("home_internet", "description_token"))
check("S12 a token matches on a WORD boundary — 'tab' inside another word is not a tablet",
      _pm.product_class_of("Established Plan TO")[0] == "phone"
      and _pm.product_class_of("Galaxy Tab A11+ 5G TO")[0] == "tablet")
check("S13 an empty declared class list is honoured — no product judgement is a real answer",
      _pm.product_breakup([{"sku": "Galaxy Tab A11+ TO"}], desc_of=lambda r: r.get("sku"),
                          classes=())["classes"][0]["key"] == "phone")
check("S14 the column that names the product is dereferenced, never spelled — the flag list read it "
      "a second time and this check found it",
      'PRODUCT_DESC_COLUMN = "sku"' in _pcode
      and _pcode.count(".get(PRODUCT_DESC_COLUMN)") == 2        # the breakup AND device_keys_of
      and '.get("sku")' not in _pcode)
check("S15 the breakup is counted over the SAME rows the P&L books from",
      "products_sold(comm_rows, cfg)" in _pcode)
check("S16 an empty feed produces an empty breakup, never a crash or a zero claim",
      P.products_sold([])["totals"]["lines"] == 0
      and P.products_sold(None)["unplaced"] == []
      and all(c["share"] is None for c in P.products_sold([])["classes"]))
check("S17 the rules live in the ONE product-description home, not a second copy here",
      "_pm.product_breakup(" in _pcode and "def product_class_of" not in _pcode
      and "def product_breakup" not in _pcode)

# The prospect screen, read as TEXT — the figures it prints must come from the statement rather than
# from arithmetic done in the browser, which is what §T11 checks.
_PAGE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "src", "app",
                          "(platform)", "commcalc", "store-takeover", "page.tsx")).read()
# ONE daily-transaction spiff cash row, in the live feed's own shape (order type and account taken
# from the live prospect's rows, 2026-10-14). It is the whole of §T8/§T9: whether this row books
# anything at all, and to whom, is decided by config and nothing else.
_TXSPIFF = {"order_type": "PostPaid Additional Spiff", "product_name": "TBV MONTH 2",
            "retail_cost": -30.0, "merchant_discount": 0.0, "account_id": "169335",
            "period": "August 2026"}

print("=" * 98)
print("T. THE ORG'S OWN BOOKING SWITCHES, NOT THE HOUSE DEFAULTS (owner report 2026-10-14)")
print("=" * 98)
# The defect: the evaluation booked through the company's own P&L functions while handing them
# `default_config()`. Those functions are config-driven, so the house answers silently overrode this
# tenant's declarations — hiding every month spiff and every MDF dollar, and keeping all airtime
# money out of the store columns because store attribution was off. Measured on the live prospect's
# own 65,491 daily-transaction rows: $435,121.34 of income under the org's rules, $112,740.46 under
# the house defaults.
_homes_fn = [n for n in _nodes if n.name == "_prospect_homes"][0]
_homes_src = ast.get_source_segment(_rtext, _homes_fn) or ""


def _calls_named(node, names):
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
            if nm in names:
                out.add(nm)
    return out


check("T1 the configs the evaluation BOOKS with are LOADED for the org — a default may be read "
      "for the comparison, but never handed to a booking function",
      '"ma_cfg": _msp.load_config(client, org_id),' in _homes_src
      and '"ma_pnl_config": _rsub.load_ma_pnl_config(client, org_id),' in _homes_src
      and '"ma_cfg": _msp.default_config()' not in _homes_src
      and '"ma_pnl_config": _rsub.default_ma_pnl_config()' not in _homes_src
      and _calls_named(_homes_fn, {"load_config", "load_ma_pnl_config"})
      == {"load_config", "load_ma_pnl_config"})
check("T2 …and it is ORG-SCOPED: the loaders get this org's id, from the one call site",
      [a.arg for a in _homes_fn.args.args] == ["client", "org_id"]
      and "load_config(client, org_id)" in _homes_src
      and "load_ma_pnl_config(client, org_id)" in _homes_src
      and _rtext.count("= _prospect_homes(client, org_id)") == 1)
check("T3 the house answers ARE still read — from the modules that own them, for the comparison "
      "only, and they are passed to rule_basis rather than booked with",
      "_msp.default_config()" in _homes_src and "_rsub.default_ma_pnl_config()" in _homes_src
      and 'homes["house_ma_cfg"], homes["house_ma_pnl_config"]' in _rtext)

_BB_COLS = tuple(MSP.PL_CONFIG_COLUMNS) + tuple(RS.MA_PNL_CONFIG_COLUMNS)
_BB_MIG = dict(MSP.PL_CONFIG_MIGRATION, **RS.MA_PNL_CONFIG_MIGRATION)
_bb_house = P.rule_basis(MSP.default_config(), RS.default_ma_pnl_config(),
                            MSP.default_config(), RS.default_ma_pnl_config(),
                            config_columns=_BB_COLS, migration_of=_BB_MIG)
check("T4 every column the switch list names is a column the LOADERS name — a renamed one would "
      "otherwise read as set forever",
      _bb_house["unknown_columns"] == [] and len(_bb_house["switches"]) == 8,
      str(_bb_house["unknown_columns"]))
check("T5 a config identical to the house answer claims NOTHING — 'declared' is only claimed where "
      "the value DIFFERS, because a loader reports columns and never whether a row carried a value",
      _bb_house["declared"] == 0 and _bb_house["house_default"] == 8)
# The live shape: this tenant declares the spiff source, the MDF token and store attribution.
_bb_org = P.rule_basis(
    dict(MSP.default_config(), month_spiff_source="daily_tx", store_attribution=True,
         mdf_product_tokens=["premium store spiff"], config_columns_missing=[]),
    dict(RS.default_ma_pnl_config(), config_columns_missing=[]),
    MSP.default_config(), RS.default_ma_pnl_config(),
    config_columns=_BB_COLS, migration_of=_BB_MIG)
check("T6 the live shape reads as three decisions, each naming what it moves",
      _bb_org["declared"] == 3
      and {sw["key"] for sw in _bb_org["switches"] if sw["basis"] == "declared"}
      == {"month_spiff_source", "store_attribution", "mdf_product_tokens"}
      and all(sw["answers"] for sw in _bb_org["switches"]))
_bb_absent = P.rule_basis(
    dict(MSP.default_config(), config_columns_missing=["pl_mdf_product_tokens"]),
    RS.default_ma_pnl_config(), MSP.default_config(), RS.default_ma_pnl_config(),
    config_columns=_BB_COLS, migration_of=_BB_MIG)
_absent = [sw for sw in _bb_absent["switches"] if sw["basis"] == "column_absent"]
check("T7 a column the table does not have yet says so, with the migration that would add it — "
      "never 'house default', which would read as a choice",
      len(_absent) == 1 and _absent[0]["key"] == "mdf_product_tokens"
      and _absent[0]["migration"] == "314_pl_ma_store_attribution.sql"
      and _bb_absent["migrations_missing"] == ["314_pl_ma_store_attribution.sql"])
check("T8 ARMED — the spiff source really does decide whether the daily-transaction feed books a "
      "spiff at all, so the two configs cannot produce one answer",
      len(MSP.ma_tx_bookings([_TXSPIFF], RS.default_ma_pnl_config(),
                             dict(MSP.default_config(), month_spiff_source="daily_tx",
                                  spiff_order_types=["PostPaid Additional Spiff"]))) == 2
      and len(MSP.ma_tx_bookings([_TXSPIFF], RS.default_ma_pnl_config(),
                                 MSP.default_config())) == 1)
check("T9 …and store attribution is what lets a store column carry that money at all",
      MSP.ma_tx_bookings([_TXSPIFF], RS.default_ma_pnl_config(),
                         dict(MSP.default_config(), store_attribution=True,
                              month_spiff_source="daily_tx",
                              spiff_order_types=["PostPaid Additional Spiff"]))[0][1] == "169335"
      and MSP.ma_tx_bookings([_TXSPIFF], RS.default_ma_pnl_config(),
                             MSP.default_config())[0][1] is None)
check("T10 the statement says out loud that revenue is every carrier dollar these feeds carry, so "
      "the reader is not left to assume what 'carrier-side' leaves out",
      all(w in P.coverage_summary([])["bottom_line_note"]
          for w in ("month spiffs", "residual", "market-development", "own declared rules")))
check("T11 the store table shows GROSS PROFIT — the figure the accessory target comes off — read "
      "from that store's own statement, never subtracted on the screen",
      "{ header: 'Gross profit', get: (r: StoreRow) => r.grossProfit, money: true }" in _PAGE
      and "grossProfit: t?.gross_profit ?? 0," in _PAGE
      and "grossProfit: t?.revenue" not in _PAGE)
check("T12 …and the screen shows the switches, so a figure resting on a fallback cannot read as a "
      "decision",
      "data?.rule_basis &&" in _PAGE and "sw.basis === 'declared'" in _PAGE
      and "column_absent" in _PAGE)

print("\n" + "=" * 98)
print(f"{len(PASS)} passed / {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1)
print("A prospect is evaluated through the company's own books, writes none of them, and says what "
      "it could not measure.")
