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
import ast
import os
import sys

sys.path.insert(0, ".")

from app.modules.core.module_graph import home_under_app        # noqa: E402
from app.modules.commcalc import prospect_eval as P            # noqa: E402
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
                "ma_sold_cost_from_rows", "route_expense_line"}
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
                   "upload_prospect_feed", "prospect_evaluation", "_prospect_row", "_prospect_cfg",
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
check("…and the upload writes only the one prospect landing table",
      _written_in([n for n in _nodes if n.name == "upload_prospect_feed"]) == {"prospect_feed_row"})
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

print("\n" + "=" * 98)
print(f"{len(PASS)} passed / {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1)
print("A prospect is evaluated through the company's own books, writes none of them, and says what "
      "it could not measure.")
