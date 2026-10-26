"""BATCH 18 OF THE §CORE-17 DRILL-DOWN PROGRAMME — the four SALES-AND-MARGIN reports.

  /commcalc/gp                Gross Profit Report
  /commcalc/sales-analyzer    Retention Analysis (3MR)
  /commcalc/sales-comparison  Sales Comparison
  /commcalc/zero-sales        Zero Sales

WHAT THIS PROVES, AND WHY IT IS WORTH A FILE
────────────────────────────────────────────
Owner ask 2026-10-24: *every report built on platform data must let him see WHERE each figure came
from.* This family is the one that answers *what did we sell, and at what margin*, which is where a
figure gets acted on with money — so a tile that cannot name its rows here is expensive in both
directions: a store chased for a shortfall that is really a missing upload, and a shortfall nobody
chases because it reads as a measured zero.

It also settles §CORE-17.3k on this family's hardest case and §CORE-17.3o on four pages at once:

  · NET PROFIT DRILLS, because `calc_gp_report` does the subtraction INSIDE its per-store loop and
    the header is Σ of those rows. Every `Δ` on the Sales Comparison does NOT, because that one is
    taken at the TOTAL from two independent window folds — and both its sides drill, which is the
    whole of the answer. §B and §D measure both off the real producers.
  · ONE of the four pages folds its tiles in the BROWSER and is therefore narrowed; the other THREE
    print the server's own fold and must not be. §F locks all four, in both directions, with a
    non-vacuity witness for every scan — and it asserts the narrowed page's `reduce` by its ACTUAL
    TEXT, because one of the three pages that must NOT be narrowed *does* contain `reduce(` (twice,
    inside its dry-run digest preview) and a lazier scan would have got it backwards.

THE PRODUCERS ARE DRIVEN FOR REAL, AND ALL FOUR OF THEM CAN BE. `gp_report.calc_gp_report`,
`sales_comparison.build` and `zero_sales.build_report` are pure and are called directly;
`sales_analyzer.analyze` takes its client as an argument, so it is driven through a stdlib stub
client that answers the three tables it reads. Nothing in this file re-implements a producer's
arithmetic: every figure a panel is compared against came out of the producer itself.

The whole file is DB-FREE and stdlib-only.
Run: `cd backend && python3 harness_batch18_drilldown.py`
"""
import ast
import copy
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.account import liabilities_drilldown as liabdd            # noqa: E402
from app.modules.commcalc import carrier_dollar_class as cdc               # noqa: E402
from app.modules.commcalc import carrier_pay_drilldown as cpd              # noqa: E402
from app.modules.commcalc import data_lineage_registry as lineage          # noqa: E402
from app.modules.commcalc import processor_money_drilldown as pmd          # noqa: E402
from app.modules.commcalc import sales_analyzer as sa                      # noqa: E402
from app.modules.commcalc import sales_comparison as sc                    # noqa: E402
from app.modules.commcalc import sales_margin_drilldown as dd              # noqa: E402
from app.modules.commcalc import targets_drilldown as tdd                  # noqa: E402
from app.modules.commcalc import zero_sales as zs                          # noqa: E402
from app.modules.commcalc.gp_report import calc_gp_report                  # noqa: E402
from app.modules.core import report_drilldown as rd                        # noqa: E402

PASS, FAIL = [], []
ROOT = ".."


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def ctl(name, red):
    """A NEGATIVE CONTROL / MUTATION. `red` must be True — the tampered case really did come back
    wrong. A control that passes is a check that cannot fail, printed as though it had."""
    check("MUT " + name, red)


def read(rel):
    p = os.path.join(ROOT, rel) if not rel.startswith("harness") else rel
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def cents(v):
    return None if v is None else int(round(float(v) * 100))


def agrees(panel, tile):
    """A PORT of `lib/drill-narrow.agreesToTheCent` — the ONE gate every screen in this batch hands
    to `useDrillTile`. Deliberately the same arithmetic, so the safety net the pages rely on is
    tested here rather than assumed."""
    return bool(panel) and cents((panel.get("figure") or {}).get("amount")) == cents(tile or 0)


DECL = read("backend/app/modules/commcalc/sales_margin_drilldown.py")
ROUTER = read("backend/app/modules/commcalc/router.py")
REGISTRY = read("backend/app/modules/commcalc/data_lineage_registry.py")
LABELS = read("frontend/src/lib/sourceLabels.ts")


def _literals(src):
    """Every STRING literal the module's CODE contains, and every number.

    A table name or a dollar figure may appear in a DOCSTRING or a comment — that is a note to the
    next engineer and the whole point of writing the defects down. The rulings are about the CODE, so
    the two are told apart by PARSING rather than by grepping, and a docstring (the first statement
    of a module, class or function) is excluded while every other string literal is kept.
    """
    tree = ast.parse(src)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    strings, numbers = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) not in docs:
            if isinstance(node.value, str):
                strings.append(node.value)
            elif isinstance(node.value, bool):
                continue
            elif isinstance(node.value, (int, float)):
                numbers.append(node.value)
    return strings, numbers


DECL_STRINGS, DECL_NUMBERS = _literals(DECL)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. ONE FACT, ONE HOME — the design fix, with BOTH halves ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("A1 the declaration dereferences the SALES DISPLAY UNION resolver by OBJECT IDENTITY — the "
      "one home for 'which tables did this period's sales come from' (§CORE-17.3r), never a second "
      "copy of the rule",
      dd.sales_union_tables is pmd.sales_union_tables)
check("A2 and the CALCULATED rep-commission snapshot by object identity, from the declaration that "
      "named it once (batch 11, §CORE-17.3j)",
      dd.REP_COMMISSIONS is cpd.REP_COMMISSIONS and dd.REP_COMMISSIONS == "rep_commissions")
check("A3 and every feed name from the lineage registry, by object identity",
      dd.MONTHLY_SALES is lineage.MONTHLY_SALES
      and dd.PAYMENT_DETAIL is lineage.PAYMENT_DETAIL
      and dd.MI_RESIDUAL is lineage.MI_RESIDUAL
      and dd.MA_COMMISSION is lineage.MA_COMMISSION
      and dd.MA_DAILY_TX is lineage.MA_DAILY_TX
      and dd.STORE_ROSTER is lineage.STORE_ROSTER)
check("A4 and the §66 question 'is this component evidence about the dollar, or a placement of LAST "
      "RESORT' by object identity — the allowlist, never a second opinion about a basis",
      dd.placement_is_evidence is cdc.component_is_evidence)
check("A5 THE DESIGN FIX — `shifts` and `store_expenses` are NAME constants in the registry now, "
      "which is the one home for a provenance name",
      lineage.SCHEDULE == "shifts" and lineage.STORE_EXPENSES == "store_expenses"
      and "SCHEDULE = \"shifts\"" in REGISTRY
      and "STORE_EXPENSES = \"store_expenses\"" in REGISTRY)
check("A6 AND ITS OTHER HALF — `shifts` was spelled as a LITERAL in TWO drill declarations and BOTH "
      "are re-keyed in this same commit, by OBJECT IDENTITY and not merely equal text. Writing the "
      "registry without wiring the callers to it is not a fix at all (CLAUDE.md §19.18)",
      tdd.SHIFTS is lineage.SCHEDULE and liabdd.SHIFTS is lineage.SCHEDULE)
_TDD = read("backend/app/modules/commcalc/targets_drilldown.py")
_LIAB = read("backend/app/modules/account/liabilities_drilldown.py")
check("A7 BUILD LOCK — and neither literal may come back. A second copy of a provenance name is the "
      "divergence the registry exists to prevent, and this is what stops the next change quietly "
      "restoring it",
      'SHIFTS = "shifts"' not in _TDD and 'SHIFTS = "shifts"' not in _LIAB
      and "SHIFTS = _lineage.SCHEDULE" in _TDD and "SHIFTS = _lineage.SCHEDULE" in _LIAB)
check("A7b NEGATIVE CONTROL — that scan really does fire on the literal it is looking for",
      'SHIFTS = "shifts"' in 'SHIFTS = "shifts"   # storeops.shifts')
check("A8 BUILD LOCK — this declaration spells NO relation name of its own. Every table it can name "
      "arrives through a constant, so a feed cannot be renamed in one place and left in another",
      not [s for s in DECL_STRINGS
           if s in ("raw_sales", "daily_sales_feed", "raw_payment_detail", "raw_mi",
                    "raw_comp_report", "rep_commissions", "store_expenses", "shifts", "stores",
                    "raw_ma_commission", "raw_ma_daily_tx")],
      str([s for s in DECL_STRINGS if "_" in s and s.startswith(("raw_", "rep_", "store_"))]))
check("A9 and it holds NO dollar literal, rate or threshold of its own — it presents figures other "
      "modules computed and computes none",
      all(n in (0, 1) for n in DECL_NUMBERS), str(sorted(set(DECL_NUMBERS))))
check("A10 and no carrier / tenant / product / store name (RULE TWO)",
      not re.search(r"(?i)\b(boost|vidapay|luxelink|acima|b2bsoft|t-?mobile|verizon)\b", DECL))
check("A11 every reason key this declaration can emit is in the SHARED vocabulary, so a screen "
      "prints a sentence rather than a field name",
      dd.REASON_NO_SALE_MATCH in rd.UNMEASURED_REASONS)
check("A12 and the relation it CANNOT name is None, never a label — an absence is reported, never "
      "filled in with a guess (§CORE-17.3i)",
      dd.UNNAMEABLE is None)
check("A13 every table this declaration can name has a PLAIN NAME in the one frontend home, so a "
      "database identifier can never reach a manager's screen",
      all(re.search(r"^\s+%s:" % t, LABELS, re.M) for t in
          (dd.MONTHLY_SALES, dd.PAYMENT_DETAIL, dd.MI_RESIDUAL, dd.REP_COMMISSIONS,
           dd.STORE_EXPENSES, dd.SCHEDULE, dd.STORE_ROSTER, dd.MA_COMMISSION, dd.MA_DAILY_TX,
           lineage.LIVE_SALES_FEED)),
      str([t for t in (dd.STORE_EXPENSES, dd.SCHEDULE) if not re.search(r"^\s+%s:" % t, LABELS, re.M)]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. THE GROSS PROFIT REPORT, driven through the REAL engine ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
GP_SALES = []
for _st in ("1 Main St", "2 Oak Ave"):
    GP_SALES += [
        {"store": _st, "salesperson": "Abid", "department": "Ondigo", "category": "Accessories",
         "product_desc": "Case", "ext_price": 50.0, "gp": 20.0, "trans_id": _st + "T1",
         "trans_date": "2026-09-02"},
        {"store": _st, "salesperson": "Dee", "department": "", "category": "Plan",
         "product_desc": "Plan X", "ext_price": 60.0, "gp": 60.0, "trans_id": _st + "T2",
         "trans_date": "2026-09-05"},
        {"store": _st, "salesperson": "Dee", "department": "Misc", "category": "Other",
         "product_desc": "Device Setup Charge", "ext_price": 35.0, "gp": 35.0,
         "trans_id": _st + "T3", "trans_date": "2026-09-06"},
    ]
GP_PAY = [{"business_address": "1 Main St", "amount": 120.0, "payment_type": "Commission - Month 1"},
          {"business_address": "1 Main St", "amount": 70.0, "payment_type": "Commission - Month 3"},
          {"business_address": "2 Oak Ave", "amount": 90.0, "payment_type": "Commission - Month 1"},
          # BOTH STORES CARRY BOTH LEGS AND A REIMBURSEMENT, deliberately: §G's non-vacuity witness
          # asserts that filtering to ONE market moves EVERY ONE of the seven tiles, and a leg only
          # one store is paid on would sit still through the filter and make that witness vacuous for
          # it. `net_phone_cost` is `phone_sales + reimb` in the producer, which is why the second
          # reimbursement is what gives that column two stores to lose one of.
          {"business_address": "2 Oak Ave", "amount": 30.0, "payment_type": "Commission - Month 3"},
          {"business_address": "2 Oak Ave", "amount": 25.0, "payment_type": "Re-imbursement"},
          {"business_address": "1 Main St", "amount": 40.0, "payment_type": "Re-imbursement"}]
GP_DECL = {"commission - month 1": "Commission", "commission - month 3": "Commission",
           "re-imbursement": "Reimbursement"}
GP_MAPPING = [{"store_address": "1 Main St", "salesforce_id": "SF1", "market": "LI",
               "store_code": "B-1", "is_active": True},
              {"store_address": "2 Oak Ave", "salesforce_id": "SF2", "market": "NJ",
               "store_code": "B-2", "is_active": True}]
#: The sales read's OWN meta, in the shape `router._sales_rows_union_txn` records it.
GP_META = {"primary": lineage.LIVE_SALES_FEED, "primary_rows": 6,
           "other": lineage.MONTHLY_SALES, "other_rows": 2, "filled_rows": 1}


def gp_result(**over):
    """THE REAL ENGINE. Nothing here folds a GP figure — `calc_gp_report` does, exactly as the
    endpoint calls it, and the endpoint's own read-only additions are stamped on afterwards."""
    kw = dict(sales=GP_SALES, pay_detail=GP_PAY,
              mi_rows=[{"salesforce_id": "SF1", "actual_mi_payout": 15.0,
                        "actual_atu_payout": 5.0}],
              rep_commissions=[{"store": "1 Main St", "total_payout": 80.0,
                                "epay_salesperson": "Abid", "storeops_name": "Abid K"},
                               {"store": "2 Oak Ave", "total_payout": 45.0,
                                "epay_salesperson": "Dee", "storeops_name": "Dee R"}],
              expenses=[{"store_code": "B-1", "amount": 200.0, "expense_name": "Rent"},
                        {"store_code": "B-2", "amount": 150.0, "expense_name": "Rent"}],
              catalog=[], store_mapping=GP_MAPPING, period="September 2026",
              pay_category_map=GP_DECL, carrier_declarations=GP_DECL,
              box_departments={"box"})
    kw.update(over)
    out = calc_gp_report(**kw)
    out["sales_source"] = dict(GP_META)
    # The labour-coverage report the endpoint attaches. B-1's salary was derived from its own hours
    # this month; B-2's was not — which is the whole of defect 6's per-row signal.
    out["labour_coverage"] = {"detail": {"B-1": {"hours_basis": "actual", "measured": True},
                                         "B-2": {"hours_basis": None, "measured": False}}}
    return out


GP = gp_result()
GPD = dd.gp_drilldowns(GP)
check("B1 the real engine produced a usable report — two store rows and a figure in every one of "
      "the seven tile columns, so nothing below is measured over an empty fold",
      len(GP["store_rows"]) == 2
      and all(rd.money(GP["totals"].get(k)) != 0.0 for k in dd.GP_TILE_COLUMNS),
      str({k: GP["totals"].get(k) for k in dd.GP_TILE_COLUMNS}))
check("B2 all seven tiles get a panel and nothing else does — the figure, not the table",
      sorted(GPD) == sorted(dd.GP_TILE_COLUMNS), str(sorted(GPD)))
check("B3 every panel's figure IS the producer's own total, to the cent — a panel can never be a "
      "second opinion about the tile above it",
      all(cents(GPD[k]["figure"]["amount"]) == cents(GP["totals"][k]) for k in GPD))
check("B4 and every one of them TIES: the store rows the panel names add up to the figure exactly. "
      "That is a real comparison, because the figure is the engine's own Σ and the rows are its "
      "per-store arithmetic",
      all(GPD[k]["tie_out"]["ties"] is True for k in GPD),
      str({k: GPD[k]["tie_out"]["words"] for k in GPD if GPD[k]["tie_out"]["ties"] is not True}))
check("B5 §CORE-17.3k — NET PROFIT DRILLS, and this is why: the engine subtracts at the ROW. Each "
      "store row's own net profit equals its revenue less rep pay, expenses and phone cost",
      all(cents(r["net_profit"]) == cents(r["total_rev"] - r["rep_pay"] - r["exp_total"]
                                          - r["net_phone_cost"]) for r in GP["store_rows"]))
check("B6 the revenue, phone-cost and net-profit panels name the SALES READ, the CARRIER FEED and "
      "the RESIDUAL REPORT — resolved off the read's own meta, never guessed",
      {s["table"] for s in GPD["total_rev"]["sources"]}
      == {lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES, dd.PAYMENT_DETAIL, dd.MI_RESIDUAL},
      str(GPD["total_rev"]["sources"]))
check("B7 §CORE-17.3n — the two COMMISSION panels are the same store row in the same unit and name "
      "the CARRIER FEED ALONE, which is the only reason they open separately",
      all({s["table"] for s in GPD[k]["sources"]} == {dd.PAYMENT_DETAIL}
          for k in ("comm_m1", "comm_m2_12")))
check("B8 §CORE-17.3j — REP PAY names the CALCULATED SNAPSHOT and nothing else, and says in words "
      "that the trail stops at the stored run",
      {s["table"] for s in GPD["rep_pay"]["sources"]} == {dd.REP_COMMISSIONS}
      and "WROTE DOWN" in GPD["rep_pay"]["note"])
check("B9 §CORE-17.3i — defect 6 is REPORTED on the rows it is true of: the expenses panel names "
      "the entered sheet on every row and carries a stated, countable UNTRACEABLE ORIGIN for the "
      "ONE store whose salary this report derived itself",
      {s["table"] for s in GPD["exp_total"]["sources"]} == {dd.STORE_EXPENSES}
      and GPD["exp_total"]["untraceable_origins"] == 1,
      str(GPD["exp_total"]["untraceable_origins"]))
check("B10 §CORE-17.3m — the NP TARGET column gets no panel at all, because this report substitutes "
      "a platform default for a store nobody set one for and keeps no flag to tell the two apart",
      "net_profit_target" not in GPD and "net_profit_target" not in dd.GP_TILE_COLUMNS
      and "net_profit_attainment" not in GPD)
check("B11 and the refusal survives the figure being SERVED: a `net_profit_target` total on the "
      "payload still gets no panel, so the refusal is a rule and not an accident of the fixture",
      "net_profit_target" not in dd.gp_drilldowns(
          dict(GP, totals=dict(GP["totals"], net_profit_target=30000.0))))
check("B12 the `comp_*` columns are DISPLAY-ONLY and get no panel — Total Revenue does not contain "
      "them, and the carrier panels say so in words so a reader who cannot reconcile knows why",
      not [k for k in GPD if k.startswith("comp_")]
      and "net profit does not move with them" in GPD["total_rev"]["note"]
      and cents(GP["totals"]["total_rev"]) == cents(
          sum(GP["totals"][c] for c in ("acc_gp", "setup_gp", "phone_sales", "plan_gp", "other_gp",
                                        "comm", "reimb", "mdf", "chargeback", "unmapped",
                                        "mi", "atu"))))
check("B13 the month-of-life LADDER columns get no panel — a re-partition of the very commission "
      "the two leg tiles already open",
      not [k for k in GPD if k.startswith("comm_month")]
      and bool(GP["commission_legs"]["ladder_months"]))
check("B14 a store the identity chain could NOT place keeps its own row, and the row carries the "
      "producer's OWN sentence for why rather than a rewording of it",
      True)
_GP_UNPLACED = gp_result(pay_detail=GP_PAY + [{"business_address": "99 Nowhere Rd", "amount": 25.0,
                                               "payment_type": "Commission - Month 1"}])
_GPDU = dd.gp_drilldowns(_GP_UNPLACED)
_un = [r for r in _GPDU["total_rev"]["rows"] if r["fields"] and "no store matched" in r["fields"]]
check("B14b and it really happens — an unplaceable spelling is a ROW in the panel, with the "
      "producer's own words on it, and the panel still ties",
      len(_un) == 1 and _un[0]["fields"]["no store matched"]
      and _GPDU["total_rev"]["tie_out"]["ties"] is True,
      str(len(_un)))
check("B15 the row key a panel serves is the STORE as the page spells it — which is the key the "
      "page's own market/store filter acts on, so panel and tile are one selection",
      all(set((r["row_key"] or {})) == {"store"} for r in GPD["net_profit"]["rows"]))
check("B16 an empty report drills to NOTHING rather than to a figure behind an empty list",
      dd.gp_drilldowns({}) == {} and dd.gp_drilldowns(dict(GP, store_rows=[])) == {})
check("B17 the declared tile list is the PAGE's own tile list — read out of the page source, so the "
      "two cannot drift apart",
      True)
GP_PAGE = read("frontend/src/app/(platform)/commcalc/gp/page.tsx")
_page_keys = re.findall(r"\{ label: '[^']+', key: '([a-z_0-9]+)'", GP_PAGE)
check("B17b and they agree exactly, in order",
      tuple(_page_keys) == tuple(dd.GP_TILE_COLUMNS), str(_page_keys))
#: The sentence the carrier panels add when the §66 allowlist says part of this period's money
#: was PLACED by a keyword rule rather than by a declaration. Asserted by the words the reader
#: actually sees, so a reworded note cannot leave this lock passing over copy that no longer says it.
_GUESS_SENTENCE = "PART OF THE CARRIER MONEY ON THIS REPORT WAS PLACED BY A PLATFORM GUESS"
check("B18 §66 — the carrier panels say so when part of this period's carrier money was placed by a "
      "KEYWORD GUESS rather than by the company's declaration. A fallback basis is not evidence, "
      "and the question is asked of the ONE home for it",
      _GUESS_SENTENCE not in GPD["comm_m1"]["note"]
      and dd.carrier_placement_all_evidence(GP) is True)
_GP_GUESS = dict(GP, carrier_class_coverage={"by_basis": [
    {"basis": cdc.BASIS_DECLARED, "amount": 400.0},
    {"basis": cdc.BASIS_UNRESOLVED, "amount": 60.0}]})
check("B18b and with an undeclared type carrying real money it DOES say so — on the two carrier "
      "panels and nowhere else",
      dd.carrier_placement_all_evidence(_GP_GUESS) is False
      and _GUESS_SENTENCE in dd.gp_drilldowns(_GP_GUESS)["comm_m1"]["note"]
      and _GUESS_SENTENCE in dd.gp_drilldowns(_GP_GUESS)["comm_m2_12"]["note"]
      and _GUESS_SENTENCE not in dd.gp_drilldowns(_GP_GUESS)["rep_pay"]["note"]
      and _GUESS_SENTENCE not in dd.gp_drilldowns(_GP_GUESS)["total_rev"]["note"])
check("B19 which CARRIER FEED filled the Commission column is the PRODUCER's own gate, read off the "
      "payload — never a second gate here, and never a tenant branch (RULE TWO)",
      dd.carrier_commission_tables(GP) == (dd.PAYMENT_DETAIL,)
      and dd.carrier_commission_tables(
          {"commission_legs": {"sources": [{"key": "comm", "basis": "received"}]}})
      == (dd.MA_COMMISSION, dd.MA_DAILY_TX))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. THE RETENTION ANALYSIS, driven through the REAL producer ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class _Q:
    """A stdlib stand-in for one PostgREST query. `sales_analyzer` pages with `.range()`, so that is
    the one method that has to mean anything."""

    def __init__(self, rows):
        self._rows, self._lo, self._hi = rows, 0, 10 ** 9

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def range(self, lo, hi):
        self._lo, self._hi = lo, hi
        return self

    def execute(self):
        return type("R", (), {"data": self._rows[self._lo:self._hi + 1]})()


class _Schema:
    def __init__(self, data):
        self._d = data

    def table(self, name):
        return _Q(list(self._d.get(name) or []))


class StubClient:
    """Answers the three tables `sales_analyzer.analyze` reads, and nothing else."""

    def __init__(self, data):
        self._d = data

    def schema(self, _name):
        return _Schema(self._d)


AN_MI = [
    {"subscriber_id": "S1", "subscriber_status": "Cancelled", "phone_number": "5550000001",
     "device_serial": "IMEI1", "mi_activation_date": "2026-06-03",
     "mi_deactivation_date": "2026-06-25", "base_mrc": 35.0, "commissionable_mrc": 35.0,
     "customer_plan": "Plan A", "rep_username": "abid", "period": "June 2026",
     "salesforce_id": "SF1"},
    {"subscriber_id": "S2", "subscriber_status": "Ported Out", "phone_number": "5550000002",
     "device_serial": "IMEI2", "mi_activation_date": "2026-06-07",
     "mi_deactivation_date": "2026-06-30", "base_mrc": 25.0, "commissionable_mrc": 25.0,
     "customer_plan": "Plan B", "rep_username": "dee", "period": "June 2026",
     "salesforce_id": "SF2"},
    {"subscriber_id": "S3", "subscriber_status": "Active", "phone_number": "5550000003",
     "device_serial": "IMEI3", "mi_activation_date": "2026-06-11",
     "mi_deactivation_date": None, "base_mrc": 40.0, "commissionable_mrc": 40.0,
     "customer_plan": "Plan A", "rep_username": "abid", "period": "June 2026",
     "salesforce_id": "SF1"},
    {"subscriber_id": "S4", "subscriber_status": "Active", "phone_number": "5550000004",
     "device_serial": "IMEI4", "mi_activation_date": "2026-06-14",
     "mi_deactivation_date": None, "base_mrc": 30.0, "commissionable_mrc": 30.0,
     "customer_plan": "Plan C", "rep_username": "dee", "period": "June 2026",
     "salesforce_id": "SF2"},
]
#: S1's sale IS in the archive. S2's is NOT — which is defect 1 and defect 2 in one row.
AN_SALES = [
    {"store": "1 Main St", "salesperson": "abid", "product_desc": "Phone A", "ext_price": 400.0,
     "gp": 60.0, "trans_id": "T1", "trans_date": "2026-06-03", "mdn": "5550000001",
     "serial_1": "IMEI1", "department": "BOX", "category": "Phones", "contract_type": "premium"},
    {"store": "1 Main St", "salesperson": "abid", "product_desc": "Case", "ext_price": 25.0,
     "gp": 10.0, "trans_id": "T1", "trans_date": "2026-06-03", "mdn": "", "serial_1": "",
     "department": "Ondigo", "category": "Accessories"},
]


def an_result(mi=None, sales=None):
    """THE REAL PRODUCER, through the stub client."""
    client = StubClient({"raw_mi": mi if mi is not None else AN_MI,
                         "raw_sales": sales if sales is not None else AN_SALES,
                         "name_map": [{"epay_login": "abid", "storeops_name": "Abid K"},
                                      {"epay_login": "dee", "storeops_name": "Dee R"}],
                         "store_mapping": GP_MAPPING})
    return sa.analyze(client, "org", "September 2026", window_days=90)


AN = an_result()
AND_ = dd.analyzer_drilldowns(AN)
check("C1 the real producer ran: a two-rep cohort of four with two early churns, one of which the "
      "sales archive carries a sale for and one of which it does NOT",
      AN["totals"]["cohort"] == 4 and AN["totals"]["churned"] == 2
      and len(AN["reps"]) == 2 and len(AN["churned"]) == 2
      and len([c for c in AN["churned"] if dd.sale_matched(c)]) == 1,
      str(AN["totals"]))
check("C2 every drillable figure gets a panel and the RATE does not (§CORE-17.3h — a retention "
      "percentage is a rate, and both figures it divides do drill)",
      sorted(AND_) == sorted(["cohort", "churned", "retained", "lost_value_sold", "lost_accessory",
                              "lost_mrc", "employee_driven", "customer_driven", "mixed"]),
      str(sorted(AND_)))
check("C3 every panel's figure IS the producer's own total, to the cent",
      all(cents(AND_[k]["figure"]["amount"]) == cents(AN["totals"][k]) for k in AND_))
check("C4 and every one of them TIES",
      all(AND_[k]["tie_out"]["ties"] is True for k in AND_),
      str({k: AND_[k]["tie_out"]["words"] for k in AND_ if AND_[k]["tie_out"]["ties"] is not True}))
check("C5 §CORE-17.3l — the REP panels fold the rep rows and the CHURN panels fold the churn rows, "
      "which are two row lists and therefore two sets of panels",
      {AND_[k]["rows_total_count"] for k in ("cohort", "churned", "retained")} == {2}
      and {AND_[k]["rows_total_count"] for k in ("lost_mrc", "employee_driven")} == {2}
      and AND_["cohort"]["rows"][0]["row_key"].keys() != AND_["lost_mrc"]["rows"][0]["row_key"].keys())
check("C6 DEFECT 1, REPORTED ON THE PANEL — the two sale-side figures name the MONTH-END SALES "
      "UPLOAD and NOT the daily feed union, because that is the read this report actually makes",
      all({s["table"] for s in AND_[k]["sources"]} == {dd.MONTHLY_SALES}
          for k in ("lost_value_sold", "lost_accessory"))
      and lineage.LIVE_SALES_FEED not in str(AND_["lost_value_sold"]["sources"]))
check("C7 §CORE-17.3n — LOST MRC is the SAME churn row in the SAME unit and names the SUBSCRIBER "
      "REPORT alone, which is the only reason it opens separately",
      {s["table"] for s in AND_["lost_mrc"]["sources"]} == {dd.MI_RESIDUAL})
check("C8 DEFECT 2 — a churn whose sale the archive does not carry is SHOWN WITHOUT A FIGURE and "
      "not counted, with the producer vocabulary's own reason, instead of a fabricated $0.00",
      AND_["lost_value_sold"]["tie_out"]["shown_not_counted"] == 1
      and AND_["lost_value_sold"]["untraceable_origins"] == 1
      and any(r["reason"] == dd.REASON_NO_SALE_MATCH and r["amount"] is None
              for r in AND_["lost_value_sold"]["rows"]))
check("C9 AND NO FIGURE MOVED BY A CENT — the producer still counts that row as $0.00 and the panel "
      "still equals the producer's total, because a blank contributes exactly what a zero did",
      cents(AND_["lost_value_sold"]["figure"]["amount"]) == cents(AN["totals"]["lost_value_sold"]))
check("C10 §CORE-17.3l — the three VERDICT panels are the same churn row in a different UNIT and "
      "over BOTH relations at once, and each counts only its own verdict while showing the rest",
      all(AND_[t]["figure"]["unit"] == rd.UNIT_COUNT for t in dd.LOSS_TOTALS.values())
      and {s["table"] for s in AND_["mixed"]["sources"]} == {dd.MI_RESIDUAL, dd.MONTHLY_SALES}
      and AND_["mixed"]["tie_out"]["shown_not_counted"] >= 1)
#: THE PRODUCER'S OWN SOURCE. The verdict vocabulary is read out of `sales_analyzer` rather than
#: restated here, so a FOURTH verdict added there fails this file instead of shipping as a tile with
#: no panel. Two reads, because the producer spells the vocabulary twice itself: once as the words a
#: churn row can carry, and once as the three totals it folds them into.
_SA_SRC = read("backend/app/modules/commcalc/sales_analyzer.py")
_SA_VERDICTS = set(re.findall(r'"(\w+)": "(?:emp|cust|mixed)_loss"', _SA_SRC))
_SA_TOTALS = set(re.findall(r'"(\w+)": sum\(1 for c in churned_rows if c\["loss_type"\]', _SA_SRC))
check("C11 the declared verdict vocabulary IS the producer's, read out of its SOURCE — a fourth "
      "verdict is a build failure here rather than a tile with no panel",
      _SA_VERDICTS == set(dd.LOSS_TOTALS)
      and _SA_TOTALS == set(dd.LOSS_TOTALS.values())
      and len(_SA_VERDICTS) == 3
      and {str(c["loss_type"]) for c in AN["churned"]} <= set(dd.LOSS_TOTALS)
      and all(v in AN["totals"] for v in dd.LOSS_TOTALS.values()),
      str(sorted(_SA_VERDICTS)) + " / " + str(sorted(_SA_TOTALS)))
check("C12 THE VACUOUS TIE IS REFUSED — on a cohort whose sale archive carries nothing, the two "
      "sale-side panels are NOT SERVED. A $0.00 over zero counted rows ties trivially (0 == 0, "
      "True) and would read as a measurement",
      True)
AN_NOSALE = an_result(sales=[])
ANDN = dd.analyzer_drilldowns(AN_NOSALE)
check("C12b and it really happens, non-vacuously: that run has churn rows and a $0.00 total for "
      "both figures, and neither gets a panel while the MRC panel beside them still does",
      len(AN_NOSALE["churned"]) == 2
      and cents(AN_NOSALE["totals"]["lost_value_sold"]) == 0
      and "lost_value_sold" not in ANDN and "lost_accessory" not in ANDN
      and "lost_mrc" in ANDN and ANDN["lost_mrc"]["tie_out"]["ties"] is True,
      str(sorted(ANDN)))
check("C13 an empty report drills to nothing",
      dd.analyzer_drilldowns({}) == {}
      and dd.analyzer_drilldowns({"totals": AN["totals"], "reps": [], "churned": []}) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. THE SALES COMPARISON, driven through the REAL producer ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
CMP_B = [
    {"store": "1 Main St", "trans_id": "B1", "trans_date": "2026-09-02", "ext_price": 300.0,
     "gp": 40.0, "product_desc": "Phone A", "department": "BOX", "category": "Phones"},
    {"store": "1 Main St", "trans_id": "B1", "trans_date": "2026-09-02", "ext_price": 50.0,
     "gp": 20.0, "product_desc": "Case", "department": "Ondigo", "category": "Accessories"},
    {"store": "2 Oak Ave", "trans_id": "B2", "trans_date": "2026-09-04", "ext_price": 30.0,
     "gp": 11.0, "product_desc": "Cable", "department": "Ondigo", "category": "Accessories"},
]
CMP_C = [
    {"store": "1 Main St", "trans_id": "C1", "trans_date": "2026-08-02", "ext_price": 60.0,
     "gp": 18.0, "product_desc": "Case", "department": "Ondigo", "category": "Accessories"},
    {"store": "2 Oak Ave", "trans_id": "C2", "trans_date": "2026-08-04", "ext_price": 40.0,
     "gp": 12.0, "product_desc": "Cable", "department": "Ondigo", "category": "Accessories"},
]
CMP_META = {"base": {"primary": lineage.LIVE_SALES_FEED, "primary_rows": 3},
            "compare": {"primary": lineage.MONTHLY_SALES, "primary_rows": 2}}
_is_acc = (lambda r: str(r.get("department") or "").lower() == "ondigo")


def cmp_result(base=None, comp=None, expenses=True):
    out = sc.build(CMP_B if base is None else base, CMP_C if comp is None else comp,
                   {}, _is_acc, [], base_period="2026-09", compare_period="2026-08",
                   mode="mom", window_label="Full month",
                   resolve_market=(lambda s: "LI" if "Main" in str(s) else "NJ"),
                   expenses=({"base": {"1 Main St": 700.0, "2 Oak Ave": 500.0},
                              "compare": {"1 Main St": 650.0, "2 Oak Ave": 450.0},
                              "base_carried_from": None, "compare_carried_from": None}
                             if expenses else None),
                   store_key_of=(lambda v: str(v or "").strip()))
    out["source_meta"] = copy.deepcopy(CMP_META)
    return out


CMP = cmp_result()
CMPD = dd.comparison_drilldowns(CMP)
_acc = next(c for c in CMP["totals_by_category"] if c["key"] == "accessory")
check("D1 the real producer ran, with an item moving in BOTH units and in BOTH windows, so nothing "
      "below is measured over a flat fold",
      _acc["current"] == 2 and _acc["previous"] == 2
      and rd.money(_acc["current_rev"]) != rd.money(_acc["previous_rev"]),
      str(_acc))
check("D2 every panel's figure IS the producer's own total, to the cent",
      all(cents(CMPD["item.accessory." + f]["figure"]["amount"]) == cents(_acc[f])
          for f in ("current", "previous", "current_rev", "previous_rev"))
      and all(cents(CMPD["overall." + f]["figure"]["amount"]) == cents(CMP["overall"][f])
              for f in ("current_txns", "previous_txns", "current_rev", "previous_rev")))
check("D3 and every one of them TIES",
      all(CMPD[k]["tie_out"]["ties"] is True for k in CMPD),
      str({k: CMPD[k]["tie_out"]["words"] for k in CMPD if CMPD[k]["tie_out"]["ties"] is not True}))
check("D4 §CORE-17.3n — THE TWO WINDOWS NAME DIFFERENT TABLES, because a comparison window is a "
      "different period and the union read it separately. That is the whole reason they open apart",
      {s["table"] for s in CMPD["item.accessory.current"]["sources"]} == {lineage.LIVE_SALES_FEED}
      and {s["table"] for s in CMPD["item.accessory.previous"]["sources"]} == {lineage.MONTHLY_SALES})
check("D5 §CORE-17.3k / §CORE-17.3h — NO Δ and NO Δ% gets a panel: the subtraction is taken at the "
      "TOTAL from two independent window folds, and both of its sides drill",
      not [k for k in CMPD if k.rsplit(".", 1)[-1] in ("delta", "rev_delta", "pct", "rev_pct")]
      and "rev_delta" in _acc and _acc["rev_pct"] is not None)
check("D6 §CORE-17.3l — the ITEM panels fold the store-and-item rows and the OVERALL panels fold "
      "the PER-STORE summary, which is a different row list and a different catalogue",
      CMPD["item.accessory.current"]["rows_total_count"] == 2
      and CMPD["overall.current_txns"]["rows_total_count"] == len(CMP["store_summary"]))
check("D7 an item's panel contains ONLY that item's rows — a panel over every item's rows would be "
      "a panel about rows its figure is not made of",
      all(set((r["row_key"] or {})) == {"store"} for r in CMPD["item.accessory.current"]["rows"])
      and len(dd._cmp_item_rows(CMP["rows"], "accessory")) == 2
      and len(dd._cmp_item_rows(CMP["rows"], "expenses")) == 2)
check("D8 the EXPENSE line opens on money only, and names the expense sheet rather than any sales "
      "read — an expense is not an item sold",
      {s["table"] for s in CMPD["expenses.current_rev"]["sources"]} == {dd.STORE_EXPENSES}
      and {s["table"] for s in CMPD["expenses.previous_rev"]["sources"]} == {dd.STORE_EXPENSES})
check("D9 DEFECT 9 — and its UNIT columns get NO panel, although the producer serves 0 for them. A "
      "panel over a field pinned at zero ties against nothing and reads as measured",
      "expenses.current" not in CMPD and "expenses.previous" not in CMPD
      and next(c for c in CMP["totals_by_category"] if c["key"] == "expenses")["current"] == 0)
check("D10 a window the union read NOTHING for reports an origin it could not trace, rather than "
      "naming a feed on a guess",
      True)
_CMP_NOMETA = cmp_result()
_CMP_NOMETA["source_meta"] = {"base": CMP_META["base"], "compare": {}}
_CMPDN = dd.comparison_drilldowns(_CMP_NOMETA)
check("D10b and it really happens: with the comparison read's meta gone, that window's panels name "
      "NO table and say so countably, while the base window's are untouched",
      _CMPDN["item.accessory.previous"]["sources"] == []
      and _CMPDN["item.accessory.previous"]["untraceable_origins"] == 2
      and {s["table"] for s in _CMPDN["item.accessory.current"]["sources"]}
      == {lineage.LIVE_SALES_FEED})
check("D11 with no expense read at all the producer serves no expense category and no expense "
      "panel is invented for it",
      not [k for k in dd.comparison_drilldowns(cmp_result(expenses=False))
           if k.startswith("expenses.")])
check("D12 an empty report drills to nothing",
      dd.comparison_drilldowns({}) == {}
      and dd.comparison_drilldowns(dict(CMP, rows=[])) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. ZERO SALES, driven through the REAL producer ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
ZS_DAYS = zs.day_range("2026-09-01", "2026-09-05")
ZS_CFG = zs.resolve_config()
ZS_META = [{"primary": lineage.LIVE_SALES_FEED, "primary_rows": 40}]


def zs_result(alerting=True, unknown=True):
    """THE REAL PRODUCER. `alerting` gives B-1 a run past the threshold; `unknown` leaves B-2 with
    no schedule in the window, which is the only way a store's trading calendar is unknown."""
    counted = {"store": {}, "rep": {}}
    landed = {"store": set(), "rep": set()}
    hours = {"store": {}, "rep": {}}
    for d in ZS_DAYS:
        hours["store"][("B-1", d)] = 8.0
        hours["rep"][(("B-1", "abid"), d)] = 8.0
        if unknown is False:
            hours["store"][("B-2", d)] = 8.0
    for d in (ZS_DAYS if alerting else ZS_DAYS[:1]):
        counted["store"][("B-1", d)] = 0
        landed["store"].add(("B-1", d))
        counted["rep"][(("B-1", "abid"), d)] = 0
        landed["rep"].add((("B-1", "abid"), d))
    if not alerting:
        for d in ZS_DAYS[1:]:
            counted["store"][("B-1", d)] = 4
            landed["store"].add(("B-1", d))
    counted["store"][("B-2", ZS_DAYS[0])] = 3
    landed["store"].add(("B-2", ZS_DAYS[0]))
    out = zs.build_report(
        ZS_DAYS, {"store": ["B-1", "B-2"], "rep": [("B-1", "abid")]}, counted, landed, hours,
        ZS_CFG, as_of="2026-09-20", parent_of={("B-1", "abid"): "B-1"},
        labels={"store": {"B-1": "B-1", "B-2": "B-2"}, "rep": {("B-1", "abid"): "Abid"}})
    out["sales_source"] = copy.deepcopy(ZS_META)
    return out


ZSR = zs_result()
ZSD = dd.zero_sales_drilldowns(ZSR)
_ZS_TILES = ("stores_alerting", "store_days_measured_zero", "store_days_not_reported",
             "stores_unknown_calendar")
check("E1 the real producer ran: a store past the alert threshold, a store with no schedule at all, "
      "days nothing landed for, and a rep row underneath — so every figure below is non-zero",
      all(ZSR["totals"][k] for k in _ZS_TILES)
      and len(ZSR["rows"]) == 3
      and len([r for r in ZSR["rows"] if r["grain"] == "rep"]) == 1,
      str(ZSR["totals"]))
check("E2 all four tiles get a panel, and the `scopes` figure does not — it counts store rows AND "
      "rep rows at once, so it has no row list of one grain behind it (defect 12)",
      sorted(ZSD) == sorted(_ZS_TILES) and "scopes" not in ZSD, str(sorted(ZSD)))
check("E3 every panel's figure IS the producer's own total, and every one TIES",
      all(cents(ZSD[k]["figure"]["amount"]) == cents(ZSR["totals"][k]) for k in ZSD)
      and all(ZSD[k]["tie_out"]["ties"] is True for k in ZSD),
      str({k: ZSD[k]["tie_out"]["words"] for k in ZSD if ZSD[k]["tie_out"]["ties"] is not True}))
check("E4 THE GRAIN IS THE PRODUCER'S OWN WORD, and the rep rows are SHOWN and deliberately not "
      "counted — the same days one grain finer, and counting both would count every day twice",
      dd.GRAIN_STORE == zs.GRAIN_STORE
      and all(ZSD[k]["tie_out"]["shown_not_counted"] >= 1 for k in _ZS_TILES)
      and all(not r["in_figure"] for k in _ZS_TILES for r in ZSD[k]["rows"]
              if r["label"] == "Abid"))
check("E5 DEFECT 4 — the sales provenance this report had been DISCARDING is collected now, and the "
      "two feed-backed panels name it. One meta per month, merged by the one home",
      {s["table"] for s in ZSD["store_days_measured_zero"]["sources"]}
      == {lineage.LIVE_SALES_FEED, dd.SCHEDULE}
      and dd.union_tables_over(ZS_META) == (lineage.LIVE_SALES_FEED,)
      and dd.union_tables_over([ZS_META[0], {"primary": lineage.MONTHLY_SALES, "primary_rows": 2}])
      == (lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES))
check("E5b and the router really does collect it now, instead of binding the meta and dropping it",
      "sales_metas.append(_meta)" in ROUTER
      and 'report["sales_source"] = sales_metas' in ROUTER)
check("E6 §CORE-17.3i — THE TWO FIGURES ABOUT AN ABSENCE NAME NO SOURCE AT ALL, countably. "
      "'Nothing landed' and 'there is no schedule' were not read out of any relation, and claiming "
      "one would be a claim about a row nobody wrote",
      ZSD["store_days_not_reported"]["sources"] == []
      and ZSD["store_days_not_reported"]["untraceable_origins"] == len(ZSR["rows"])
      and ZSD["stores_unknown_calendar"]["sources"] == []
      and ZSD["stores_unknown_calendar"]["untraceable_origins"] == len(ZSR["rows"]))
check("E7 THE VACUOUS TIE IS REFUSED — a count no row carries a number for is served NO PANEL. A 0 "
      "over zero counted rows ties against nothing and reads as measured",
      True)
_ZS_NOALERT = zs_result(alerting=False)
_ZSDN = dd.zero_sales_drilldowns(_ZS_NOALERT)
check("E7b and it really happens, non-vacuously: with no store alerting the producer still serves "
      "`stores_alerting: 0` and that panel is withheld, while the other three are served",
      _ZS_NOALERT["totals"]["stores_alerting"] == 0
      and "stores_alerting" not in _ZSDN
      and sorted(_ZSDN) == sorted(k for k in _ZS_TILES if k != "stores_alerting"),
      str(sorted(_ZSDN)))
_ZS_SCHED = zs_result(unknown=False)
_ZSDS = dd.zero_sales_drilldowns(_ZS_SCHED)
check("E7c and the same for the unknown-calendar count on an estate whose schedule covers every "
      "store",
      _ZS_SCHED["totals"]["stores_unknown_calendar"] == 0
      and "stores_unknown_calendar" not in _ZSDS)
check("E8 an empty report drills to nothing",
      dd.zero_sales_drilldowns({}) == {}
      and dd.zero_sales_drilldowns(dict(ZSR, rows=[])) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. §CORE-17.3o DECIDED PER PAGE, LOCKED BOTH WAYS — and the registration ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
PAGES = {
    "/commcalc/gp": GP_PAGE,
    "/commcalc/sales-analyzer":
        read("frontend/src/app/(platform)/commcalc/sales-analyzer/page.tsx"),
    "/commcalc/sales-comparison":
        read("frontend/src/app/(platform)/commcalc/sales-comparison/page.tsx"),
    "/commcalc/zero-sales": read("frontend/src/app/(platform)/commcalc/zero-sales/page.tsx"),
}
check("F1 all four pages were read", all(len(v) > 2000 for v in PAGES.values()),
      str({k: len(v) for k, v in PAGES.items()}))
check("F2 all four render the ONE shared panel and reach the figures through the ONE shared gate — "
      "no page re-implements either",
      all("DrillDownPanel" in v and "agreesToTheCent" in v for v in PAGES.values()))
_NARROWED = "/commcalc/gp"
check("F3 THE NARROWED PAGE — `/commcalc/gp` folds its tiles in the BROWSER, so its panels are "
      "narrowed through the one client home and each tile is READ OFF its narrowed panel",
      "narrowPanel(" in PAGES[_NARROWED] and "screenKey(" in PAGES[_NARROWED]
      and "gpNarrowed[k]?.figure?.amount" in PAGES[_NARROWED])
check("F4 AND THAT CALL IS JUSTIFIED BY THE PAGE'S OWN TEXT — the tell is a client `reduce` over "
      "the rows the browser filtered, feeding the tiles. This asserts the actual expression, not "
      "the mere presence of the word",
      re.search(r"COLS_BASE\.forEach\(c2 => \{ totals\[c2\.key\] = rows\.reduce\(", PAGES[_NARROWED])
      and re.search(r"const rows: StoreRow\[\] = allRows\.filter\(", PAGES[_NARROWED]))
def _uncommented(src):
    """The page with its `//` and `/* */` comments removed. A relation name in a COMMENT is a note
    to the next engineer; the ruling is about what a manager READS, and that is the code."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(ln for ln in src.split("\n") if not ln.strip().startswith("//"))


_NOT_NARROWED = [k for k in PAGES if k != _NARROWED]
check("F5 THE THREE PAGES THAT MUST NOT BE NARROWED — `narrowPanel` appears in none of them. Their "
      "filters are QUERY PARAMETERS, so the server folds again and each panel follows its tile by "
      "construction; narrowing would make a panel explain a figure the tile is not showing",
      all("narrowPanel" not in _uncommented(PAGES[k]) for k in _NOT_NARROWED),
      str([k for k in _NOT_NARROWED if "narrowPanel" in _uncommented(PAGES[k])]))
check("F5b and that scan is NOT VACUOUS — it fires on the CODE and not on the comment that "
      "documents the lock, which all three pages carry so the next reader finds the rule where "
      "they would break it",
      "narrowPanel" in _uncommented("const p = narrowPanel(panel, keep)")
      and "narrowPanel" not in _uncommented("// `narrowPanel` must not appear here")
      and "narrowPanel" in _uncommented(PAGES[_NARROWED])
      and all("THE LOCK FAILS THE BUILD IF `narrowPanel` APPEARS HERE" in PAGES[k]
              for k in _NOT_NARROWED))
check("F6 THE TRAP THIS BATCH HAD TO AVOID, LOCKED — one of the three pages that must NOT be "
      "narrowed DOES contain `reduce(`, twice, and neither is a tile fold: both are inside its "
      "dry-run digest preview. A scan treating 'this file contains reduce(' as 'this page folds its "
      "tiles' would have got the call backwards",
      PAGES["/commcalc/zero-sales"].count("reduce(") == 2
      and "res.reduce(" in PAGES["/commcalc/zero-sales"]
      and "function DigestPreview" in PAGES["/commcalc/zero-sales"])
check("F7 the three server-fold pages read each tile's number straight off the server and fold "
      "nothing of their own into it",
      "data?.totals?.[k]" in PAGES["/commcalc/sales-analyzer"]
      and "(data?.totals as Record<string, number> | undefined)?.[k]"
      in PAGES["/commcalc/zero-sales"]
      and "cmpFig(k)" in PAGES["/commcalc/sales-comparison"])


_REL_RE = r"(raw_sales|daily_sales_feed|raw_payment_detail|raw_mi|store_expenses|rep_commissions)"
check("F8 no page prints a relation name of its own — every table reaching copy goes through "
      "`sourceLabel()` inside the shared panel",
      all(not re.search(_REL_RE, _uncommented(v)) for v in PAGES.values()),
      str([k for k, v in PAGES.items() if re.search(_REL_RE, _uncommented(v))]))
check("F8b and that scan is NOT VACUOUS",
      bool(re.search(_REL_RE, _uncommented("const t = 'raw_payment_detail'")))
      and not re.search(_REL_RE, _uncommented("// raw_payment_detail is the carrier feed")))
check("F9 all four endpoints attach the panels read-only over their OWN output, so a panel cannot "
      "be a second opinion about the figure above it",
      "result['drilldown'] = _smdd.gp_drilldowns(result)" in ROUTER
      and 'out["drilldown"] = _smdd.analyzer_drilldowns(out)' in ROUTER
      and 'out["drilldown"] = _smdd.comparison_drilldowns(out)' in ROUTER
      and 'report["drilldown"] = _smdd.zero_sales_drilldowns(report)' in ROUTER)
check("F9b and the GP panels are attached AFTER the scope filters, so a store the caller may not "
      "see is in no panel row either — a drill-down must not become a disclosure",
      ROUTER.index("result['drilldown'] = _smdd.gp_drilldowns(result)")
      > ROUTER.index("if in_keyset(ks, r.get('store'), r.get('store_code'), r.get('address'))"))
LEDGER = read("backend/harness_drilldown_pending.txt")
_ROUTES = ("/commcalc/gp", "/commcalc/sales-analyzer", "/commcalc/sales-comparison",
           "/commcalc/zero-sales")
check("F10 all four routes are flipped in the coverage ledger, each with a note saying what drills "
      "AND what it refuses to claim",
      all(re.search(r"^%s\tshared\t.{200,}REFUSES TO CLAIM" % re.escape(r), LEDGER, re.M)
          for r in _ROUTES),
      str([r for r in _ROUTES
           if not re.search(r"^%s\tshared\t.{200,}REFUSES TO CLAIM" % re.escape(r), LEDGER, re.M)]))
COV = read("backend/harness_drilldown_coverage.py")


def _owes_max(src):
    """THE RATCHET'S PIN, or None when it cannot be read at all.

    **SHRINK-ONLY, AND NEVER RE-TIGHTENED TO `==`.** The rule the ratchet states is *"may only
    shrink"*, and `<=` IS that rule. A lock that asserts the VALUE goes red the moment a later batch
    correctly lowers the pin, and the next reader's reflex when it goes red is to edit the number —
    which is how a ratchet quietly becomes decoration. It has broken that way twice (batch 10 pinned
    `== 74`, batch 15 re-tightened to `== 53`). It still FAILS CLOSED: a pin ABOVE this batch's own
    number is red, and a pin that cannot be read at all is red rather than absent."""
    m = re.search(r"(?m)^OWES_MAX\s*=\s*(\d+)\s*$", src or "")
    return int(m.group(1)) if m else None


_pin = _owes_max(COV)
_owes = len(re.findall(r"\towes\t", LEDGER))
check("F11 THE RATCHET SHRANK BY THE FOUR ROUTES THIS BATCH SERVED, and has never been pushed back "
      "up past this batch's 41 — the DIRECTION, not the value, so a later batch's correct shrink "
      "does not turn this file red. The pin and the ledger's own owed count must agree",
      _pin is not None and _pin <= 41 and _owes <= 41 and _owes == _pin,
      "pin=%s owes=%s" % (_pin, _owes))
check("F11b NEGATIVE CONTROL — a pin ABOVE 41 is caught, so the ratchet cannot be loosened by "
      "editing the number", _owes_max("OWES_MAX = 42\n") > 41)
check("F11c NEGATIVE CONTROL — a LATER batch's lower pin passes without touching this file",
      _owes_max("OWES_MAX = 41\n") <= 41 and _owes_max("OWES_MAX = 27\n") <= 41)
check("F11d NEGATIVE CONTROL — FAIL CLOSED: a ratchet with no readable pin is not a pass",
      _owes_max("# the pin was deleted\n") is None and _owes_max("OWES_MAX = many\n") is None)
GRAPH = read("backend/app/modules/core/module_graph.py")
check("F12 the declaration and this lock are written into the module graph BY HAND, under both "
      "facts it dereferences — AND the two declarations this batch re-keyed",
      "'app/modules/commcalc/sales_margin_drilldown.py': ('report_drilldown',)" in GRAPH
      and "'app/modules/commcalc/sales_margin_drilldown.py': ('_lineage',)" in GRAPH
      and "'app/modules/account/liabilities_drilldown.py': ('_lineage',)" in GRAPH
      and "'app/modules/commcalc/targets_drilldown.py': ('_lineage',)" in GRAPH
      and GRAPH.count("harness_batch18_drilldown.py") >= 1)
WF = read(".github/workflows/carrier-vocab-guard.yml")
check("F13 and a lock nothing runs is not a lock — this file is run by the guard job and its paths "
      "filter names every file it is about",
      "python3 harness_batch18_drilldown.py" in WF
      and all(('"%s"' % f) in WF for f in
              ("backend/harness_batch18_drilldown.py",
               "backend/app/modules/commcalc/sales_margin_drilldown.py",
               "backend/app/modules/commcalc/gp_report.py",
               "backend/app/modules/commcalc/sales_analyzer.py",
               "backend/app/modules/commcalc/sales_comparison.py",
               "backend/app/modules/commcalc/zero_sales.py",
               "frontend/src/app/(platform)/commcalc/gp/page.tsx")))
check("F13b AND THE WORKFLOW STILL PARSES — an unquoted scalar containing ': ' makes the whole file "
      "unparseable, so every job in it silently never runs while the PR still looks green (38 jobs "
      "lost on batch 16). Every step name added here is colon-free",
      not re.search(r"^\s+- name: [^\"'\n]*: ", WF, re.M),
      str(re.findall(r"^\s+- name: [^\"'\n]*: .*$", WF, re.M)[:2]))
IDX = read("docs/SYSTEM_DATA_FLOW_INDEX.md")
check("F14 the family is registered in the index under its OWN letter, with its grain table and its "
      "cross-references",
      "§CORE-17.3r" in IDX and "sales_margin_drilldown.py" in IDX
      and "harness_batch18_drilldown.py" in IDX
      and IDX.count("§CORE-17.3r") >= 4, str(IDX.count("§CORE-17.3r")))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. EACH SCREEN'S OWN PRE-BATCH FOLD, unfiltered AND under a filter that drops rows ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def gp_page_fold(rows):
    """THE GP PAGE'S OWN FOLD, ported: `totals[k] = rows.reduce((s, r) => s + (r[k] || 0), 0)` over
    the rows the browser filtered. This is what the tiles were BEFORE this batch, and it is what
    each tile must still be after it."""
    return {k: round(sum((r.get(k) or 0) for r in rows), 2) for k in dd.GP_TILE_COLUMNS}


def gp_page_filter(rows, markets):
    """The page's own row predicate, ported: `allRows.filter(r => selMarkets.includes(r.market))`."""
    return [r for r in rows if not markets or r.get("market") in markets]


def _key(k):
    """A PORT of `lib/drill-narrow.screenKey` / `rowKeyOf` — one comparable string for a row key."""
    return "\u0001".join("%s=%s" % (n, k[n]) for n in sorted(k) if k[n] not in (None, ""))


def narrow(panel, keep_keys):
    """A PORT of `lib/drill-narrow.narrowPanel`, deliberately the same arithmetic — including its
    refusal on a CAPPED panel and its identity when nothing was dropped."""
    if not panel or not panel.get("rows"):
        return None
    if panel.get("capped"):
        return None
    rows = [r for r in panel["rows"]
            if (not r.get("row_key")) or _key(r["row_key"]) in keep_keys]
    if len(rows) == len(panel["rows"]):
        return panel
    amount = round(sum((r.get("amount") or 0) for r in rows
                       if r.get("in_figure") and r.get("present")) * 100) / 100
    return {**panel, "figure": {**panel["figure"], "amount": amount}, "rows": rows,
            "rows_total_count": len(rows), "capped": False}


_all_rows = GP["store_rows"]
_pre_all = gp_page_fold(_all_rows)
_keys_all = {_key({"store": r["store"]}) for r in _all_rows}
check("G1 UNFILTERED — the GP page's own pre-batch fold equals the producer's totals, so the tiles "
      "this batch wired were already the same number the server states",
      all(cents(_pre_all[k]) == cents(GP["totals"][k]) for k in dd.GP_TILE_COLUMNS))
_n_all = {k: narrow(GPD[k], _keys_all) for k in GPD}
check("G2 and with nothing dropped the narrowed panel is the panel ITSELF, so the tile reads the "
      "server's figure untouched and the shared gate passes for all seven",
      all(_n_all[k] is GPD[k] for k in GPD)
      and all(agrees(_n_all[k], _pre_all[k]) for k in dd.GP_TILE_COLUMNS))
_rows_li = gp_page_filter(_all_rows, ["LI"])
_pre_li = gp_page_fold(_rows_li)
_keys_li = {_key({"store": r["store"]}) for r in _rows_li}
check("G3 NON-VACUITY WITNESS — the filter really drops rows, and the figures really move. Without "
      "this the two checks below would be comparing a selection against itself",
      len(_rows_li) == 1 and len(_rows_li) < len(_all_rows)
      and all(cents(_pre_li[k]) != cents(_pre_all[k]) for k in dd.GP_TILE_COLUMNS),
      "%d of %d rows; %s" % (len(_rows_li), len(_all_rows),
                             [k for k in dd.GP_TILE_COLUMNS
                              if cents(_pre_li[k]) == cents(_pre_all[k])]))
_n_li = {k: narrow(GPD[k], _keys_li) for k in GPD}
check("G4 UNDER THE FILTER — every narrowed panel equals the page's own fold over the surviving "
      "rows, to the cent. The figure and its evidence are ONE fold, by construction",
      all(cents(_n_li[k]["figure"]["amount"]) == cents(_pre_li[k]) for k in dd.GP_TILE_COLUMNS)
      and all(agrees(_n_li[k], _pre_li[k]) for k in dd.GP_TILE_COLUMNS))
check("G5 and the narrowed panel shows only the rows the screen is showing, with its provenance "
      "roll-up recounted for them",
      all(_n_li[k]["rows_total_count"] == len(_rows_li) for k in dd.GP_TILE_COLUMNS)
      and _n_li["exp_total"]["rows_total_count"] == 1)
check("G6 THE SAFETY NET — a panel that disagrees with the tile offers NO DRILL and the screen "
      "keeps its own number. That is the shared gate, not a per-page condition",
      not agrees(_n_all["net_profit"], _pre_all["net_profit"] + 0.01)
      and not agrees(None, _pre_all["net_profit"]))
_CAPPED = dict(GPD["net_profit"], capped=True)
check("G7 AND THE CAPPED-PANEL REFUSAL — a panel whose rows the server truncated cannot be narrowed "
      "honestly, so the one home returns nothing and the drill is simply not offered",
      narrow(_CAPPED, _keys_li) is None and not agrees(narrow(_CAPPED, _keys_li), _pre_li["net_profit"]))
_SMALL = dd.gp_drilldowns(GP, cap=1)
check("G7b and a real cap really sets that flag, so G7 is not testing a hand-made dict",
      _SMALL["net_profit"]["capped"] is True
      and _SMALL["net_profit"]["rows_shown"] == 1
      and _SMALL["net_profit"]["rows_total_count"] == len(_all_rows)
      and _SMALL["net_profit"]["tie_out"]["ties"] is True)
check("G8 THE THREE SERVER-FOLD SCREENS — each tile's pre-batch figure is the payload total it "
      "already read, so the panel each one now opens is about exactly that figure and nothing was "
      "re-folded underneath them",
      all(agrees(AND_[k], AN["totals"][k]) for k in AND_)
      and all(agrees(ZSD[k], ZSR["totals"][k]) for k in ZSD)
      and all(agrees(CMPD["overall." + f], CMP["overall"][f])
              for f in ("current_txns", "previous_txns", "current_rev", "previous_rev")))
check("G9 and a SERVER-side filter on one of them really does move both the figure and the panel "
      "together — the non-vacuity witness for 'the server folds again'",
      True)
_CMP_ONE = cmp_result(base=[r for r in CMP_B if r["store"] == "1 Main St"],
                      comp=[r for r in CMP_C if r["store"] == "1 Main St"])
_CMPD1 = dd.comparison_drilldowns(_CMP_ONE)
_acc1 = next(c for c in _CMP_ONE["totals_by_category"] if c["key"] == "accessory")
check("G9b and it really happens: with one store's rows removed from the READ, the category total, "
      "the panel's figure and the panel's row count all move together, and it still ties",
      cents(_acc1["current_rev"]) != cents(_acc["current_rev"])
      and agrees(_CMPD1["item.accessory.current_rev"], _acc1["current_rev"])
      and _CMPD1["item.accessory.current_rev"]["rows_total_count"] == 1
      and _CMPD1["item.accessory.current_rev"]["tie_out"]["ties"] is True)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== H. MUTATIONS — one side at a time, each built from a FRESH payload ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Every mutation below starts from a newly built payload, so nothing mutates in place across
# sections and a control cannot be passing because an earlier one left the fixture broken.


def gp_mut(**over):
    p = gp_result()
    totals = dict(p["totals"])
    totals.update(over.pop("totals", {}))
    p["totals"] = totals
    p.update(over)
    return p


_m = dd.gp_drilldowns(gp_mut(totals={"net_profit": GP["totals"]["net_profit"] + 0.01}))
ctl("1 A ONE-CENT total — the rows no longer add up to the figure and the panel SAYS SO, in words",
    _m["net_profit"]["tie_out"]["ties"] is False
    and "0.01" in _m["net_profit"]["tie_out"]["words"]
    and all(dd.gp_drilldowns(gp_mut())[k]["tie_out"]["ties"] is True for k in GPD))
_p = gp_result()
_p["store_rows"] = _p["store_rows"][:1]
_m = dd.gp_drilldowns(_p)
ctl("2 A DROPPED STORE ROW — the panel refuses to tie rather than quietly explaining a smaller "
    "population", _m["net_profit"]["tie_out"]["ties"] is False
    and _m["net_profit"]["tie_out"]["counted_rows"] == 1)
_p = gp_result()
_p["store_rows"][0]["net_profit"] = None
_m = dd.gp_drilldowns(_p)
# THE SHARED FOLD'S DEFAULT PREDICATE IS "the row has a number", so a GP store row that lost its
# net profit LEAVES the figure and is shown beside it instead of silently contributing $0.00. The
# panel then refuses to tie, in words, and names the row it is no longer counting. The complementary
# case — a row the figure counts WHATEVER its number, which is what yields `ties is None` — is a
# grain predicate rather than a money reader, and MUT 3b mutates one of those.
ctl("3 A BLANKED FIGURE ON A MONEY TILE — the row LEAVES the figure rather than contributing a "
    "fabricated $0.00, the panel refuses to tie and says so, and the tiles beside it stay green",
    _m["net_profit"]["tie_out"]["ties"] is False
    and _m["net_profit"]["tie_out"]["shown_not_counted"] == 1
    and _m["net_profit"]["tie_out"]["counted_rows"] == 1
    and _m["net_profit"]["rows"][0]["amount"] is None
    and "not counted" in _m["net_profit"]["tie_out"]["words"]
    and _m["total_rev"]["tie_out"]["ties"] is True)
_p = zs_result()
_p["rows"][0]["days_measured_zero"] = None
_m = dd.zero_sales_drilldowns(_p)
ctl("3b A BLANKED FIGURE ON A ROW THE FIGURE COUNTS ANYWAY — on a tile whose predicate is the "
    "GRAIN and not the number, `ties` is None: never False and never True, because a sum missing "
    "one of its own rows was never a comparison at all",
    _m["store_days_measured_zero"]["tie_out"]["ties"] is None
    and _m["store_days_measured_zero"]["tie_out"]["unmeasured_rows"] == 1
    and _m["store_days_measured_zero"]["tie_out"]["counted_rows"] >= 2
    and ZSD["store_days_measured_zero"]["tie_out"]["ties"] is True)
_p = gp_result()
_p["labour_coverage"] = {"detail": {}}
_m = dd.gp_drilldowns(_p)
ctl("4 THE SALARY-DERIVED FLAG REMOVED — defect 6's untraceable origin disappears, which is what "
    "proves the panel is reading the coverage report rather than printing a constant",
    _m["exp_total"]["untraceable_origins"] == 0
    and GPD["exp_total"]["untraceable_origins"] == 1)
_m = dd.gp_drilldowns(dict(gp_result(), carrier_class_coverage={"by_basis": [
    {"basis": cdc.BASIS_DECLARED_UNMAPPED, "amount": 10.0}]}))
ctl("5 A FALLBACK BASIS READ AS EVIDENCE — a declared category the component map could not honour "
    "is a keyword guess standing where a declaration exists, and the carrier panels must say so",
    _GUESS_SENTENCE in _m["comm_m1"]["note"]
    and _GUESS_SENTENCE in _m["comm_m2_12"]["note"]
    and _GUESS_SENTENCE not in GPD["comm_m1"]["note"])
ctl("5b and a basis NOBODY HAS RULED ON reads as NOT evidence, because the one home is an "
    "ALLOWLIST — a new last-resort basis cannot silently start presenting itself as a fact",
    dd.carrier_placement_all_evidence(
        {"carrier_class_coverage": {"by_basis": [{"basis": "a_basis_added_later",
                                                  "amount": 1.0}]}}) is False)
_m = dd.gp_drilldowns(gp_mut(totals={"net_profit_target": 30000.0,
                                     "net_profit_attainment": 0.4}))
ctl("6 A TARGET AND ITS ATTAINMENT SERVED AS TOTALS — still no panel for either. A declared number "
    "this report cannot attribute gets none, and a rate never does",
    "net_profit_target" not in _m and "net_profit_attainment" not in _m and len(_m) == 7)
_p = an_result()
_p["churned"][0]["sold_for"] = 0.0
_m = dd.analyzer_drilldowns(_p)
ctl("7 A MATCHED SALE BLANKED — that row leaves the figure's evidence and is SHOWN with its reason "
    "instead, and the panel is then withheld entirely because no row carries a number at all",
    "lost_value_sold" not in _m and "lost_mrc" in _m
    and AND_["lost_value_sold"]["tie_out"]["shown_not_counted"] == 1)
_p = an_result()
_p["totals"] = dict(_p["totals"], employee_driven=_p["totals"]["employee_driven"] + 1)
_m = dd.analyzer_drilldowns(_p)
ctl("8 A MISCOUNTED VERDICT — that one panel refuses to tie and the MONEY panels beside it stay "
    "green, which is what makes the check per figure rather than per report",
    _m["employee_driven"]["tie_out"]["ties"] is False
    and _m["lost_value_sold"]["tie_out"]["ties"] is True
    and _m["lost_mrc"]["tie_out"]["ties"] is True
    and _m["mixed"]["tie_out"]["ties"] is True)
_p = an_result()
_p["totals"] = dict(_p["totals"], retained=_p["totals"]["retained"] - 1)
_m = dd.analyzer_drilldowns(_p)
ctl("9 A RETAINED COUNT OFF BY ONE — the rep rows refuse to tie to it",
    _m["retained"]["tie_out"]["ties"] is False and _m["cohort"]["tie_out"]["ties"] is True)
_p = an_result()
for _c in _p["churned"]:
    _c["charged_mrc"] = None
_m = dd.analyzer_drilldowns(_p)
ctl("10 EVERY MRC BLANKED — the panel counts NOTHING and refuses to tie, rather than presenting a "
    "$0.00 that looks measured against a figure the producer still shows",
    _m["lost_mrc"]["tie_out"]["ties"] is False
    and _m["lost_mrc"]["tie_out"]["counted_rows"] == 0
    and _m["lost_mrc"]["tie_out"]["shown_not_counted"] == 2
    and _m["lost_mrc"]["tie_out"]["rows_total"] == 0
    and all(r["amount"] is None for r in _m["lost_mrc"]["rows"])
    and AND_["lost_mrc"]["tie_out"]["ties"] is True)
_p = cmp_result()
_cat = next(c for c in _p["totals_by_category"] if c["key"] == "accessory")
_cat["current"] = _cat["current"] + 1
_m = dd.comparison_drilldowns(_p)
ctl("11 A UNIT TOTAL OFF BY ONE — that window's unit panel refuses to tie while its DOLLAR panel "
    "and the comparison window's stay green",
    _m["item.accessory.current"]["tie_out"]["ties"] is False
    and _m["item.accessory.current_rev"]["tie_out"]["ties"] is True
    and _m["item.accessory.previous"]["tie_out"]["ties"] is True)
_p = cmp_result()
_exp = next(c for c in _p["totals_by_category"] if c["key"] == "expenses")
_exp["current"] = 7
_m = dd.comparison_drilldowns(_p)
ctl("12 THE EXPENSE LINE'S UNIT COLUMN FED A REAL NUMBER — still no panel. An expense has no unit, "
    "and the refusal is a rule rather than an accident of the fixture",
    "expenses.current" not in _m and "expenses.current_rev" in _m)
_p = cmp_result()
_p["overall"] = dict(_p["overall"], current_txns=_p["overall"]["current_txns"] + 2)
_m = dd.comparison_drilldowns(_p)
ctl("13 AN OVERALL TRANSACTION COUNT INFLATED — the per-store summary refuses to tie to it while "
    "the item panels, a different row list, stay green",
    _m["overall.current_txns"]["tie_out"]["ties"] is False
    and _m["overall.current_rev"]["tie_out"]["ties"] is True
    and _m["item.accessory.current"]["tie_out"]["ties"] is True)
_p = zs_result()
_p["totals"] = dict(_p["totals"], store_days_not_reported=_p["totals"]["store_days_not_reported"] + 1)
_m = dd.zero_sales_drilldowns(_p)
ctl("14 A NOT-REPORTED COUNT OFF BY ONE — the panel refuses to tie even though it names no source "
    "at all. An untraceable origin is still a countable row",
    _m["store_days_not_reported"]["tie_out"]["ties"] is False
    and _m["store_days_measured_zero"]["tie_out"]["ties"] is True)
_p = zs_result()
_p["rows"][-1]["grain"] = zs.GRAIN_STORE
_m = dd.zero_sales_drilldowns(_p)
ctl("15 A REP ROW PROMOTED TO THE STORE GRAIN — it is counted and the store-grain totals no longer "
    "tie, which is what proves the grain predicate is the producer's own word and not decoration",
    _m["store_days_measured_zero"]["tie_out"]["ties"] is False
    and _m["store_days_measured_zero"]["tie_out"]["counted_rows"] == 3)
_p = zs_result()
_p["sales_source"] = []
_m = dd.zero_sales_drilldowns(_p)
ctl("16 THE SALES META REMOVED — the two feed-backed panels fall back to naming the SCHEDULE alone "
    "rather than guessing a feed, and still tie",
    {s["table"] for s in _m["store_days_measured_zero"]["sources"]} == {dd.SCHEDULE}
    and _m["store_days_measured_zero"]["tie_out"]["ties"] is True
    and {s["table"] for s in ZSD["store_days_measured_zero"]["sources"]}
    == {lineage.LIVE_SALES_FEED, dd.SCHEDULE})
_p = gp_result()
_nk = {_key({"store": _p["store_rows"][0]["store"]})}
_m = narrow(dd.gp_drilldowns(_p)["net_profit"], _nk)
ctl("17 A TILE ONE CENT OFF ITS NARROWED PANEL — the shared gate withholds the drill and the screen "
    "keeps its own number. A disagreement costs the link, never the figure",
    agrees(_m, _m["figure"]["amount"]) and not agrees(_m, _m["figure"]["amount"] + 0.01))
_m = narrow(dd.gp_drilldowns(gp_result(), cap=1)["net_profit"], _nk)
ctl("18 A CAPPED PANEL UNDER A FILTER — narrowing refuses outright rather than presenting a "
    "smaller number as the whole of one", _m is None)


print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed  "
      f"({len([p for p in PASS if p.startswith('MUT ')])} of them mutations / negative controls)")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
