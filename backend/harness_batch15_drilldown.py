"""BATCH 15 OF THE §CORE-17 DRILL-DOWN PROGRAMME — the three ACCESSORY MONEY reports.

  /commcalc/accessory-target-plan   Accessory Target Allocation
  /commcalc/accessory-cost-audit    Accessory Cost Audit
  /commcalc/accessory-flags         Accessory Flags

WHAT THIS PROVES, AND WHY IT IS WORTH A FILE
────────────────────────────────────────────
Owner ask 2026-10-24: *every report built on platform data must let him see WHERE each figure came
from.* This batch's three reports answer one question in three parts — what accessory money did this
store make, at what cost, and against what target — and the ruling it settles (§CORE-17.3o) is
**A PANEL EXPLAINS THE FIGURE, NOT THE TABLE**: two of the three screens filter their row table in
the BROWSER and then print a tile the SERVER folded over the whole range, so narrowing those panels
would make each tile read a different number under a filter. That is a figure moved, and this batch
moves none — §F reproduces every screen's own pre-batch fold, unfiltered AND under a real filter that
drops rows, with a non-vacuity witness proving rows really were dropped.

The whole file is DB-FREE and stdlib-only. The producers are driven for real:
`accessory_target_plan.plan` is pure and takes its rows as arguments; `accessory_cost_audit.audit` is
driven through a scripted in-memory client, so the REAL commission-plan engine matches the REAL rule
over the REAL per-line arithmetic — including the owner's own reported instance (a $24.99 screen
protector that pays $0.00 beside a $14.99 pair of headphones that pays $0.90). The flag report's
rollup lives inside a FastAPI router that cannot be imported without the framework, so its fold is
reproduced here line-for-line AND pinned to the router's own source text (§D7): if that expression
changes, this file goes red rather than quietly measuring a fold nobody runs.

Run: `cd backend && python3 harness_batch15_drilldown.py`
"""
import ast
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import accessory_cost_audit as aca            # noqa: E402
from app.modules.commcalc import accessory_money_drilldown as dd        # noqa: E402
from app.modules.commcalc import accessory_target_plan as ap            # noqa: E402
from app.modules.commcalc import data_lineage_registry as lineage       # noqa: E402
from app.modules.commcalc import targets_drilldown as tdd               # noqa: E402
from app.modules.core import report_drilldown as rd                     # noqa: E402

PASS, FAIL = [], []
ROOT = ".."


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def ctl(name, red):
    """A NEGATIVE CONTROL. `red` must be True — the tampered case really did come back wrong. A
    control that passes is a check that cannot fail, printed as though it had."""
    check("NEG " + name, red)


def read(rel):
    p = os.path.join(ROOT, rel) if not rel.startswith("harness") else rel
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def cents(v):
    return None if v is None else int(round(float(v) * 100))


def agrees(panel, tile):
    """A PORT of `lib/drill-narrow.agreesToTheCent` — the ONE gate every screen in this batch passes
    to `useDrillTile`. Deliberately the same arithmetic, so the safety net the pages rely on is
    tested here rather than assumed."""
    return bool(panel) and cents((panel.get("figure") or {}).get("amount")) == cents(tile or 0)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  THE ONE HOME, AND A VOCABULARY THAT IS DEREFERENCED RATHER THAN RE-SPELLED.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. the declaration dereferences every shared fact ==")

DECL = read("backend/app/modules/commcalc/accessory_money_drilldown.py")

check("A1 the sales display union is the registry's own, through the targets family that already "
      "dereferenced it — not a third spelling",
      dd.SALES_FEEDS is tdd.SALES_FEEDS
      and tuple(dd.SALES_FEEDS) == tuple(lineage.SALES_DISPLAY_SOURCES))
check("A2 the PAY engine's own read is named separately, and in ITS order — the monthly upload "
      "leads and the live feed is the fallback, the reverse of the display union's lead",
      dd.PAY_SALES_LEAD == lineage.MONTHLY_SALES
      and dd.PAY_SALES_FALLBACK == lineage.LIVE_SALES_FEED
      and dd.PAY_SALES_FEEDS == (lineage.MONTHLY_SALES, lineage.LIVE_SALES_FEED)
      and tuple(lineage.SALES_DISPLAY_SOURCES) != dd.PAY_SALES_FEEDS)
check("A3 the product catalogue is the registry's constant, which this batch ADDED and re-keyed the "
      "registry's own four literals onto — naming a fact is only half the fix",
      dd.PRODUCT_CATALOG == lineage.PRODUCT_CATALOG == "raw_catalog")
REG = read("backend/app/modules/commcalc/data_lineage_registry.py")
_reg_def = 'PRODUCT_CATALOG = "raw_catalog"'
_reg_body = REG.split(_reg_def, 1)[-1]
check("A4 and no literal copy of it survives in the registry below the constant — the ingest list, "
      "the no-month reason, the snapshot set and the cadence note all dereference it",
      '"raw_catalog"' not in _reg_body,
      str([ln for ln in _reg_body.split("\n") if '"raw_catalog"' in ln][:3]))
check("A5 the TARGET settings relation is the targets family's own constant, dereferenced",
      dd.TARGETS is tdd.TARGETS and dd.TARGETS == "targets")
check("A6 the option LABELS are the producer's own, never re-worded here",
      all(dd.option_label(k) == aca.OPTION_LABELS[k] for k in aca.OPTION_KEYS)
      and not any(lbl in DECL for lbl in aca.OPTION_LABELS.values()))
check("A7 the engine-read options are DERIVED from the producer's own OPTION_KEYS minus the one "
      "option that reads a second relation — a new option there cannot be silently left out",
      dd.ENGINE_OPTIONS == tuple(k for k in aca.OPTION_KEYS if k != "option_a")
      and "option_a" in aca.OPTION_KEYS)
check("A8 every reason this declaration can emit resolves to a SENTENCE in the one home, so a "
      "reader never sees a bare field name",
      all(rd.UNMEASURED_REASONS.get(r)
          for r in (dd.REASON_NO_TARGET, dd.REASON_NOT_SELECTED, dd.REASON_NO_CAPACITY)))
check("A9 the two reasons this batch added are the one home's, not a local dict",
      "not_in_selection" in rd.UNMEASURED_REASONS
      and "no_capacity_measured" in rd.UNMEASURED_REASONS
      and "UNMEASURED_REASONS = {" not in DECL)
_TREE = ast.parse(DECL)
_floats = [n.value for n in ast.walk(_TREE)
           if isinstance(n, ast.Constant) and isinstance(n.value, float)]
_const_math = [n for n in ast.walk(_TREE) if isinstance(n, ast.BinOp)
               and any(isinstance(side, ast.Constant) and isinstance(side.value, (int, float))
                       and not isinstance(side.value, bool)
                       for side in (n.left, n.right))]
check("A10 THE DECLARATION DOES NO ARITHMETIC AND HOLDS NO MONEY. Checked on the PARSED module "
      "rather than on its prose: not one float literal anywhere — so no dollar figure, no rate, no "
      "margin and no threshold — and not one constant used as an operand, so it cannot have "
      "computed a figure it was handed. The only integers in it are string-slice positions",
      _floats == [] and _const_math == [],
      str(_floats[:5]) + str([ast.dump(n)[:60] for n in _const_math[:2]]))
check("A11 it imports no framework and does no I/O — PURE, so this file can drive it under env -i",
      not re.search(r"^\s*(import|from)\s+(fastapi|pydantic|supabase|requests)", DECL, re.M)
      and "open(" not in DECL and "datetime" not in DECL)
check("A12 every panel it can serve is built by the ONE shared fold — no hand-rolled panel dict",
      DECL.count("report_drilldown.tiles(") == 3
      and "\"schema\": rd.SCHEMA" not in DECL
      and '"tie_out"' not in DECL and '"rows_total_count"' not in DECL)
check("A13 and the relation names it does spell are the two CONFIG rows the feed registry "
      "deliberately does not hold, exactly as `targets_drilldown` names its own",
      dd.PLAN_RULES == "commission_rule" and dd.FLAG_RULES == "flag_rules"
      and dd.PLAN_RULES not in lineage.RAW_INGEST_TABLES
      and dd.FLAG_RULES not in lineage.RAW_INGEST_TABLES)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  THE ACCESSORY TARGET ALLOCATION — four grains on one store row, over the REAL producer.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. the target allocation, over the real `accessory_target_plan.plan` ==")

#: One store per shape the producer can produce, so every rung of its fallback ladder and every
#: absence this batch reports is exercised by a REAL plan() call rather than by a fixture of one.
STORES = [
    # Own history, a target row of its own.
    {"store_code": "S1", "address": "1115 Liberty Ave", "market": "LI", "is_active": True,
     "m2_acc": 4000.0, "m2_boxes": 100, "m1_acc": 5000.0, "m1_boxes": 120,
     "mtd_acc": 1200.0, "mtd_boxes": 30, "projected_acc": 4800.0, "projected_boxes": 118,
     "current_target": 5200.0, "has_target_row": True},
    # Sold boxes and attached NOTHING -> weighted at the company rate; and NOBODY set it a target.
    {"store_code": "S2", "address": "88 Market St", "market": "LI", "is_active": True,
     "m2_acc": 0.0, "m2_boxes": 60, "m1_acc": 0.0, "m1_boxes": 70,
     "mtd_acc": 0.0, "mtd_boxes": 10, "projected_acc": 0.0, "projected_boxes": 66,
     "current_target": 0.0, "has_target_row": False},
    # No history and no projection -> nothing measurable, so no share can be derived.
    {"store_code": "S3", "address": "7 New Rd", "market": "NJ", "is_active": True,
     "m2_acc": 0.0, "m2_boxes": 0, "m1_acc": 0.0, "m1_boxes": 0,
     "mtd_acc": 0.0, "mtd_boxes": 0, "projected_acc": 0.0, "projected_boxes": 0,
     "current_target": 900.0, "has_target_row": True},
    # Measurable, but OUTSIDE the selection -> its target is reserved, never re-planned.
    {"store_code": "S4", "address": "42 Hill Rd", "market": "NJ", "is_active": True,
     "m2_acc": 2000.0, "m2_boxes": 50, "m1_acc": 2500.0, "m1_boxes": 55,
     "mtd_acc": 600.0, "mtd_boxes": 14, "projected_acc": 2400.0, "projected_boxes": 54,
     "current_target": 2600.0, "has_target_row": True},
]
HIST = {"m1": "Sep 2026", "m2": "Aug 2026"}


def plan_result(stores=None, mode="fixed", value="20000", selected=("S1", "S2", "S3")):
    r = ap.plan(list(stores if stores is not None else STORES), mode=mode, value=value,
                selected=list(selected) if selected else None)
    r["history_periods"] = dict(HIST)
    return r


PLAN = plan_result()
PD = dd.plan_drilldowns(PLAN)
_row = {r["store_code"]: r for r in PLAN["rows"]}

check("B1 the producer really produced the four shapes this batch reports on — own history, a store "
      "weighted at the company rate with NO target row, a store nothing measurable was found for, "
      "and a measurable store outside the selection",
      _row["S1"]["acc_per_box_basis"] == "own_history"
      and _row["S2"]["acc_per_box_basis"] == "company_rate_zero_attach"
      and _row["S2"]["has_target_row"] is False
      and _row["S3"]["capacity"] is None and "S3" in PLAN["allocation"]["unweighted"]
      and _row["S4"]["selected"] is False and _row["S4"]["suggested_target"] is None)

check("B2 SEVEN panels are served once a goal is entered, and every one of them is a figure the "
      "report's own `totals` carries",
      sorted(PD) == ["assigned", "current_target", "extension", "m1_acc", "m2_acc", "mtd_acc",
                     "projected_acc"]
      and all(agrees(PD[k], PLAN["totals"][k]) for k in PD), str(sorted(PD)))

check("B3 GRAIN 1 — the four month figures are ONE grain: the same store row, the same unit and the "
      "same read, which is why they share a catalogue and differ only in which field holds the "
      "figure",
      all(tuple(s["table"] for s in PD[k]["sources"]) == tuple(sorted(dd.SALES_FEEDS))
          for k in ("m2_acc", "m1_acc", "mtd_acc", "projected_acc"))
      and all(PD[k]["tie_out"]["ties"] is True for k in ("m2_acc", "m1_acc", "mtd_acc",
                                                         "projected_acc")))
check("B4 and the PROJECTION says it is an estimate rather than a measurement, while the three "
      "measured months do not",
      "PROJECTION" in (PD["projected_acc"]["note"] or "")
      and all("PROJECTION" not in (PD[k]["note"] or "") for k in ("m2_acc", "m1_acc", "mtd_acc")))

_ct = PD["current_target"]
check("B5 GRAIN 2 — a TARGET is a declared number: the panel names the target settings row and the "
      "column, and nothing else",
      [s["table"] for s in _ct["sources"]] == ["targets"]
      and all(o.get("column") == dd.COL_CURRENT_TARGET
              for r in _ct["rows"] for o in r["origins"] if o.get("table")))
_ct_s2 = next(r for r in _ct["rows"] if r["row_key"]["store_code"] == "S2")
check("B6 AN UNSET TARGET IS NOT A TARGET OF ZERO (§CORE-17.3m). The store nobody set one for is "
      "SHOWN, carries no figure, is not counted, names NO table and says why in the reader's words",
      _ct_s2["amount"] is None and _ct_s2["present"] is False
      and _ct_s2["in_figure"] is False and _ct_s2["sources"] == []
      and _ct_s2["reason"] == dd.REASON_NO_TARGET
      and _ct_s2["reason_label"] == rd.UNMEASURED_REASONS[dd.REASON_NO_TARGET])
check("B7 AND THE FIGURE DID NOT MOVE A CENT: the producer's own total adds zero for that store "
      "too, so only the row changed",
      cents(_ct["figure"]["amount"]) == cents(PLAN["totals"]["current_target"])
      and _ct["tie_out"]["ties"] is True and _ct["untraceable_origins"] == 1)

_as = PD["assigned"]
check("B8 GRAIN 3 — THE FIGURE THE OWNER IS ABOUT TO WRITE ONTO EVERY STORE WAS READ OUT OF "
      "NOTHING. A suggested share is derived on this load and stored nowhere, so the panel names no "
      "table at all and reports one stated, countable untraceable origin per row (§CORE-17.3i)",
      _as["sources"] == [] and _as["untraceable_origins"] == len(PLAN["rows"])
      and all(r["sources"] == [] for r in _as["rows"]))
check("B9 and it still ties: the two stores with a share add up to the figure exactly, while the "
      "unselected store and the unmeasurable one are shown and counted in nothing",
      _as["tie_out"]["ties"] is True and _as["tie_out"]["counted_rows"] == 2
      and _as["tie_out"]["shown_not_counted"] == 2)
check("B10 THE TWO ABSENCES ARE TOLD APART, because they are two different facts about one blank: "
      "a store outside the selection is RESERVED out of the goal, a selected store with no number "
      "is one nothing measurable was found for",
      next(r for r in _as["rows"] if r["row_key"]["store_code"] == "S4")["reason"]
      == dd.REASON_NOT_SELECTED
      and next(r for r in _as["rows"] if r["row_key"]["store_code"] == "S3")["reason"]
      == dd.REASON_NO_CAPACITY)

_ex = PD["extension"]
_ex_s1 = next(r for r in _ex["rows"] if r["row_key"]["store_code"] == "S1")
_ex_s2 = next(r for r in _ex["rows"] if r["row_key"]["store_code"] == "S2")
check("B11 GRAIN 4 — AN EXTENSION DRILLS BECAUSE IT IS SUBTRACTED AT THE ROW (§CORE-17.3k): the "
      "figure is Σ of the per-store differences, and each row names the target side it was taken "
      "from",
      cents(_ex["figure"]["amount"]) == cents(PLAN["totals"]["extension"])
      and cents(_ex_s1["amount"]) == cents(_row["S1"]["suggested_target"]
                                           - _row["S1"]["current_target"])
      and _ex_s1["sources"] == ["targets"])
check("B12 COUNTED BUT NOT TRUSTED — a third state, and the honest one (defect 7). The store with "
      "no target row has its extension COUNTED, because the producer really did add it, and marked "
      "unmeasured with the reason, because the target it was subtracted from was never set",
      _ex_s2["present"] is True and _ex_s2["in_figure"] is True
      and _ex_s2["measured"] is False and _ex_s2["reason"] == dd.REASON_NO_TARGET
      and _ex_s2["sources"] == []
      and _ex["tie_out"]["ties"] is None)
check("B13 and the tie-out states the disagreement it cannot check rather than claiming one: "
      "`ties` is None, never False, when a counted row is not trusted",
      cents(sum(r["amount"] for r in _ex["rows"] if r["in_figure"]))
      == cents(PLAN["totals"]["extension"]))

_NOGOAL = plan_result(mode="pct_increase", value="")
_NG = dd.plan_drilldowns(_NOGOAL)
check("B14 A GOAL NOBODY ENTERED HAS NO ALLOCATION TO EXPLAIN (§CORE-17.3m). The two "
      "goal-dependent panels are not served AT ALL, so the tile offers no drill and the page keeps "
      "the em dash it already prints — rather than a $0.00 panel over stores that all carry no "
      "number, which would tie only by accident",
      _NOGOAL["goal"]["goal"] is None and _NOGOAL["goal"]["reason"] == "no_goal_entered"
      and "assigned" not in _NG and "extension" not in _NG
      and sorted(_NG) == ["current_target", "m1_acc", "m2_acc", "mtd_acc", "projected_acc"])
check("B15 a percentage of a basis that measures nothing is the same refusal, one step along",
      dd.plan_drilldowns(plan_result(stores=[], mode="pct_increase", value="10")) == {})
check("B16 AN EMPTY REPORT DRILLS TO NOTHING — no rows, no panels, never a panel asserting a figure "
      "behind an empty list",
      dd.plan_drilldowns({}) == {} and dd.plan_drilldowns({"rows": [], "totals": {"m1_acc": 0}}) == {})
check("B17 every row is addressed by the key a support query would find it with, and carries the "
      "basis words the page already shows beside the figure",
      all(r["row_key"] and "store_code" in r["row_key"]
          and r["fields"].get("rate basis") and r["fields"].get("target row") in ("yes", "no")
          for k in PD for r in PD[k]["rows"]))
check("B18 and the panel is titled with the MONTH the column is titled with, off the report's own "
      "`history_periods` rather than a second spelling of it",
      HIST["m1"] in PD["m1_acc"]["figure"]["label"]
      and HIST["m2"] in PD["m2_acc"]["figure"]["label"])
check("B19 NO RATE GETS A PANEL (§CORE-17.3h) — not accessories-per-box, not the required rate, not "
      "the share percentage, and not the company goal itself",
      not ({"acc_per_box", "required_acc_per_box", "share_pct", "goal", "company_acc_per_box"}
           & set(PD)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  THE ACCESSORY COST AUDIT — the REAL audit, over a scripted in-memory client.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. the cost audit, over the real `accessory_cost_audit.audit` ==")


class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    """Every PostgREST builder call this read path makes, answered from one in-memory table. A
    filter is a no-op on purpose: the fixture IS the org's slice, so a missing `.eq` can never make
    this harness pass something the real read would not have."""

    def __init__(self, rows):
        self._rows = rows

    def __getattr__(self, _name):
        def _chain(*_a, **_k):
            return self
        return _chain

    def select(self, *_a, **_k):
        return self

    def execute(self):
        return _Res([dict(r) for r in self._rows])


class _Schema:
    def __init__(self, tables):
        self._t = tables

    def table(self, name):
        return _Q(self._t.get(name, []))


class FakeClient:
    def __init__(self, tables):
        self._t = tables

    def schema(self, _s):
        return _Schema(self._t)

    def table(self, name):
        return _Q(self._t.get(name, []))


#: THE OWNER'S OWN REPORTED INSTANCE (luxelink, July 2026), as a fixture: a $24.99 screen protector
#: whose POS cost equals its price (GP $0.00 -> it pays $0.00, by arithmetic) beside a $14.99 pair of
#: headphones that pays $0.90. The catalogue carries a cost for the first item and NOT the second,
#: which is what makes Option A partly computable and partly today's pay (defect 3).
AUDIT_TABLES = {
    "commission_plan": [{"id": "P1", "org_id": "O", "name": "Plan A"}],
    "commission_rule": [{"id": "R1", "plan_id": "P1", "org_id": "O", "label": "Accessory % GP",
                         "payout_kind": "pct_gp", "pct": 0.10, "sort": 1,
                         "match_field": "department", "match_op": "equals",
                         "match_value": "ACC"}],
    "commission_tier": [],
    "commission_plan_assignment": [{"id": "A1", "plan_id": "P1", "org_id": "O",
                                    "scope": "default", "scope_value": None, "priority": 0}],
    "raw_sales": [
        {"id": 1, "org_id": "O", "trans_id": "T1", "trans_date": "2026-09-02",
         "period": "Sep 2026", "store": "1115 Liberty Ave", "salesperson": "ALICE",
         "department": "ACC", "category": "CASE", "product_desc": "SCREEN PROTECTOR",
         "sku": "SP1", "ext_price": 24.99, "gp": 0.0, "voided": ""},
        {"id": 2, "org_id": "O", "trans_id": "T2", "trans_date": "2026-09-03",
         "period": "Sep 2026", "store": "88 Market St", "salesperson": "BOB",
         "department": "ACC", "category": "AUDIO", "product_desc": "HEADPHONES",
         "sku": "HP1", "ext_price": 14.99, "gp": 9.00, "voided": ""},
    ],
    "raw_catalog": [{"org_id": "O", "product_id": 7, "product_desc": "SCREEN PROTECTOR",
                     "cost": 8.00, "sku": "SP1"}],
}

AUDIT = aca.audit(FakeClient(AUDIT_TABLES), "O", "Sep 2026")
AD = dd.audit_drilldowns(AUDIT)
_rep = {r["rep"]: r for r in AUDIT["by_rep"]}

check("C1 the REAL engine, the REAL rule and the REAL per-line arithmetic ran: the owner's reported "
      "instance reproduces — the $24.99 screen protector pays $0.00 because its implied cost equals "
      "its price, and the $14.99 headphones pay $0.90",
      AUDIT["ready"] and AUDIT["counts"]["matched_lines"] == 2
      and cents(_rep["ALICE"]["current"]) == 0 and cents(_rep["BOB"]["current"]) == 90,
      json.dumps(AUDIT["counts"]))
check("C2 EIGHT panels, and every one of them agrees with the figure the page prints — the five "
      "option totals off `totals`, the three per-rep differences off `deltas`",
      sorted(AD) == ["current", "delta_b", "delta_c", "delta_r", "option_a", "option_b",
                     "option_c", "option_r"]
      and all(agrees(AD[k], dd.audit_totals(AUDIT)[k]) for k in AD), str(sorted(AD)))
check("C3 GRAIN 5 — seven of the eight are ONE grain: the same rep row, the same unit and the same "
      "read, naming the two sale relations the pay engine's legacy read walks plus the plan rule "
      "that carries the rate",
      all([s["table"] for s in AD[k]["sources"]]
          == sorted(dd.PAY_SALES_FEEDS + (dd.PLAN_RULES,))
          for k in ("current", "option_b", "option_c", "option_r", "delta_b", "delta_c",
                    "delta_r")))
check("C4 GRAIN 6 — Option A is its own grain, and the ONE thing that separates it is a second "
      "read: the product catalogue, named beside the same two sale relations",
      [s["table"] for s in AD["option_a"]["sources"]]
      == sorted(dd.PAY_SALES_FEEDS + (dd.PLAN_RULES, dd.PRODUCT_CATALOG)))
check("C5 and its note states the live defect rather than implying a precision the payload does not "
      "have: the total is part corrected pay and part today's pay, and the per-rep payload records "
      "no split (defect 3)",
      "part corrected pay and part current pay" in (AD["option_a"]["note"] or "")
      and cents(_rep["BOB"]["option_a"]) == cents(_rep["BOB"]["current"])
      and cents(_rep["ALICE"]["option_a"]) != cents(_rep["ALICE"]["current"]))
check("C6 every panel TIES to the cent over the rep rows the report lists — a tile summing a field "
      "one way and declaring it another would come back `ties: False` on screen",
      all(AD[k]["tie_out"]["ties"] is True for k in AD))
check("C7 A MEASURED ZERO IS COUNTED. The rep the engine really paid $0.00 is in the figure with a "
      "figure of $0.00, unmeasured only where nothing was recorded — which never happens on this "
      "report, because a rep with no matched line is not in `by_rep` at all",
      next(r for r in AD["current"]["rows"] if r["row_key"]["rep"] == "ALICE")["present"] is True
      and next(r for r in AD["current"]["rows"]
               if r["row_key"]["rep"] == "ALICE")["in_figure"] is True
      and all(r["present"] for k in AD for r in AD[k]["rows"]))
check("C8 A DIFFERENCE DRILLS BECAUSE THE PRODUCER FOLDS IT PER REP (§CORE-17.3k) — Σ of the "
      "per-rep deltas is the page's own subtraction, to the cent",
      cents(AD["delta_b"]["figure"]["amount"])
      == cents(AUDIT["totals"]["option_b"] - AUDIT["totals"]["current"])
      and cents(sum(r["amount"] for r in AD["delta_b"]["rows"]))
      == cents(AD["delta_b"]["figure"]["amount"]))
check("C9 Δ A GETS NO PANEL, BY THE SAME TEST WITH THE OPPOSITE ANSWER: the page subtracts it at "
      "the TOTAL and `by_rep` carries no per-rep figure to open",
      "delta_a" not in AD and "delta_a" not in dd.audit_totals(AUDIT)
      and not any("delta_a" in r for r in AUDIT["by_rep"]))
check("C10 and a delta the producer expresses as WORDS rather than a number is never served as a "
      "figure — `deltas.option_a` is 'partial' / 'unknown', and it stays out of the totals the one "
      "home folds over",
      AUDIT["deltas"]["option_a"] in ("partial", "unknown")
      and not isinstance(dd.audit_totals(AUDIT).get("delta_a"), (int, float)))
check("C11 NO COUNT AND NO RE-PARTITION GETS A PANEL — the matched / suspect / shown counts, the "
      "per-rule table and the per-item worksheet are the lists the panels open to and print their "
      "own totals",
      not ({"matched_lines", "suspect_lines", "flagged_shown", "rules", "items", "lines",
            "catalog_rows", "pct"} & set(AD)))
check("C12 AN AUDIT WITH NO PLAN, AND AN AUDIT NO %-OF-BASIS RULE MATCHED, DRILL TO NOTHING",
      dd.audit_drilldowns({"ready": False, "by_rep": [], "totals": {"current": 0}}) == {}
      and dd.audit_drilldowns(
          aca.audit(FakeClient({k: ([] if k == "raw_sales" else v)
                                for k, v in AUDIT_TABLES.items()}), "O", "Sep 2026")) == {})
check("C13 the tile NAME is the owner's own option wording and the ROW name is the rep — the two "
      "are two keys on purpose, and a catalogue that mixed them titled every tile with its row "
      "accessor the first time it shipped",
      AD["current"]["figure"]["label"] == aca.OPTION_LABELS["current"]
      and all(r["label"] and r["label"] != AD["current"]["figure"]["label"]
              for r in AD["current"]["rows"]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §D  THE ACCESSORY FLAGS — two grains on one sale line, and the router's own fold pinned.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. the accessory flags ==")

#: One flagged line per shape the endpoint emits: over the max, under the min, one already pushed,
#: one with no salesperson at all.
FLAG_ROWS = [
    {"sale_id": 11, "trans_id": "T9", "trans_date": "2026-09-04", "period": "Sep 2026",
     "store": "1115 Liberty Ave", "rep": "ALICE", "department": "ACC", "category": "CASE",
     "item_desc": "PREMIUM CASE", "sku": "PC9", "ext_price": 59.99, "phone_model": "Model A",
     "flag_reason": "over", "chargeback_amount": 10.0, "dedupe_key": "acc:T9:PC9",
     "already_flagged": False},
    {"sale_id": 12, "trans_id": "T9", "trans_date": "2026-09-04", "period": "Sep 2026",
     "store": "1115 Liberty Ave", "rep": "ALICE", "department": "ACC", "category": "AUDIO",
     "item_desc": "EARBUDS", "sku": "EB2", "ext_price": 4.99, "phone_model": None,
     "flag_reason": "under", "chargeback_amount": 10.0, "dedupe_key": "acc:T9:EB2",
     "already_flagged": True},
    {"sale_id": 13, "trans_id": "T12", "trans_date": "2026-09-06", "period": "Sep 2026",
     "store": "88 Market St", "rep": "BOB", "department": "ACC", "category": "CASE",
     "item_desc": "RUGGED CASE", "sku": "RC3", "ext_price": 44.50, "phone_model": "Model B",
     "flag_reason": "over", "chargeback_amount": 10.0, "dedupe_key": "acc:T12:RC3",
     "already_flagged": False},
    {"sale_id": 14, "trans_id": "T15", "trans_date": "2026-09-07", "period": "Sep 2026",
     "store": "88 Market St", "rep": None, "department": "ACC", "category": "OTHER",
     "item_desc": "CAR MOUNT", "sku": "CM1", "ext_price": 39.99, "phone_model": None,
     "flag_reason": "over", "chargeback_amount": 10.0, "dedupe_key": "acc:T15:CM1",
     "already_flagged": False},
]

ROUTER = read("backend/app/modules/commcalc/router.py")

#: THE ENDPOINT'S OWN SUMMARY FOLD, reproduced line-for-line. It lives inside a FastAPI router that
#: cannot be imported without the framework, so the expression is PINNED to the router's source in
#: §D7 — if the producer's fold ever changes, this file goes red instead of measuring a fold nobody
#: runs.
_SUMMARY_PINS = (
    '"total": round(sum(x["ext_price"] or 0 for x in out), 2),',
    '"chargeback_total": round(sum(x["chargeback_amount"] or 0 for x in out), 2),',
    '"txns": len({x["trans_id"] for x in out}), "items": len(out), "flags": len(out),',
)


def flags_summary(rows):
    return {
        "txns": len({x["trans_id"] for x in rows}), "items": len(rows), "flags": len(rows),
        "total": round(sum(x["ext_price"] or 0 for x in rows), 2),
        "chargeback_total": round(sum(x["chargeback_amount"] or 0 for x in rows), 2),
        "over": sum(1 for x in rows if x.get("flag_reason") != "under"),
        "under": sum(1 for x in rows if x.get("flag_reason") == "under"),
    }


FLAGS = {"rows": FLAG_ROWS, "summary": flags_summary(FLAG_ROWS),
         "threshold": 35.0, "min_threshold": 5.0, "default_chargeback": 10.0}
FD = dd.flags_drilldowns(FLAGS)

check("D1 TWO panels — the two MONEY tiles, and nothing else",
      sorted(FD) == ["chargeback_total", "total"], str(sorted(FD)))
check("D2 GRAIN 7 — the rung-out total opens to the flagged lines and names the MONTHLY SALES "
      "UPLOAD alone, which is the only sales relation this report reads (defect 4)",
      [s["table"] for s in FD["total"]["sources"]] == [lineage.MONTHLY_SALES]
      and FD["total"]["tie_out"]["ties"] is True
      and agrees(FD["total"], FLAGS["summary"]["total"])
      and all(o.get("column") == dd.COL_EXT_PRICE
              for r in FD["total"]["rows"] for o in r["origins"]))
check("D3 GRAIN 8 — the chargeback exposure is the SAME line in the SAME unit and is NOT a "
      "measurement of the sale: it names the org's own flag-rules row and the column the default "
      "amount lives in",
      [s["table"] for s in FD["chargeback_total"]["sources"]] == [dd.FLAG_RULES]
      and all(o.get("column") == dd.COL_CHARGEBACK
              for r in FD["chargeback_total"]["rows"] for o in r["origins"])
      and agrees(FD["chargeback_total"], FLAGS["summary"]["chargeback_total"]))
check("D4 and the note says it, so a reader cannot take a number that moves when a setting moves "
      "for a number that moves when a sale moves",
      "NOT a measurement of that sale" in (FD["chargeback_total"]["note"] or "")
      and "default chargeback amount set once" in (FD["chargeback_total"]["note"] or ""))
check("D5 NO COUNT TILE AND NO ROLLUP CARD GETS A PANEL — the flags / over / under / transaction "
      "tiles are the cardinality of the table below, and the three cards re-partition those same "
      "rows and print their own totals",
      not ({"flags", "items", "txns", "over", "under", "by_rep", "by_store", "by_store_rep",
            "threshold", "min_threshold", "default_chargeback"} & set(FD)))
check("D6 each row is addressed by the transaction and the item, and carries why it was flagged and "
      "whether it has already been pushed — a line with no salesperson is still a line",
      all(set(r["row_key"]) <= {"trans_id", "sku"} and r["fields"].get("why flagged")
          for k in FD for r in FD[k]["rows"])
      and any(r["fields"].get("employee") is None for r in FD["total"]["rows"]))
check("D7 UN-WIRE LOCK — the endpoint's own summary fold is still the expression this file "
      "reproduces. A producer that changes its fold fails the build here rather than leaving this "
      "harness measuring a fold nobody runs",
      all(p in ROUTER for p in _SUMMARY_PINS),
      str([p for p in _SUMMARY_PINS if p not in ROUTER]))
check("D8 AN EMPTY RANGE DRILLS TO NOTHING",
      dd.flags_drilldowns({"rows": [], "summary": flags_summary([])}) == {}
      and dd.flags_drilldowns({}) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §E  THE WIRING — a registry nobody dereferences is not a fix (CLAUDE.md §19.18).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. the wiring, locked so it cannot un-wire ==")

PAGES = {
    "plan": read("frontend/src/app/(platform)/commcalc/accessory-target-plan/page.tsx"),
    "audit": read("frontend/src/app/(platform)/commcalc/accessory-cost-audit/page.tsx"),
    "flags": read("frontend/src/app/(platform)/commcalc/accessory-flags/page.tsx"),
}

check("E1 all three pages were read — a lock over a file nobody can find is not a lock",
      all(len(v) > 2000 for v in PAGES.values()))
check("E2 each of the three endpoints ATTACHES the declaration's output and folds no panel of its "
      "own",
      ROUTER.count("accessory_money_drilldown as _accdd") == 3
      and "_accdd.plan_drilldowns(result)" in ROUTER
      and "_accdd.audit_drilldowns(out)" in ROUTER
      and '_accdd.flags_drilldowns(payload)' in ROUTER
      and "report_drilldown.tile_panel(" not in ROUTER)
def _at(hay, needle):
    """Where a snippet is, or -1. A missing snippet must read as a FAILED ORDERING rather than taking
    this file down with a traceback — a lock that crashes reports nothing about the other checks."""
    return hay.find(needle)


check("E3 and the two scoped endpoints attach it AFTER their span filter, so a panel can never hold "
      "a store or a sale the caller may not see",
      _at(ROUTER, "_accdd.plan_drilldowns(result)")
      > _at(ROUTER, "in_keyset(ks, r.get('store_code')") > -1
      and _at(ROUTER, "_accdd.flags_drilldowns(payload)")
      > _at(ROUTER, 'sales = [r for r in sales if in_keyset(_ks, r.get("store"))]') > -1)
check("E4 every page renders the SHARED panel and reads its tile's own figure through the ONE "
      "agreement gate — not a local copy of it",
      all("DrillDownPanel" in v and "useDrillTile" in v and "agreesToTheCent" in v
          for v in PAGES.values()))
check("E5 §CORE-17.3o BUILD LOCK — `lib/drill-narrow` is NOT wired into any of the three pages. "
      "These tiles are the SERVER's fold while two of the tables are filtered in the browser, so "
      "narrowing a panel here would make the tile read a different number under a filter — a figure "
      "moved. A later reader cannot 'fix' them into a class they were never in",
      all("narrowPanel" not in v and "narrowPanels" not in v for v in PAGES.values()),
      str([k for k, v in PAGES.items() if "narrowPanel" in v]))
check("E5b and the scan is NOT VACUOUS — the same text really is found on a page that is in that "
      "class, so E5 is checking something",
      "narrowPanels" in read("frontend/src/app/(platform)/commcalc/epay-fee-recon/page.tsx"))
LABELS = read("frontend/src/lib/sourceLabels.ts")
check("E6 every relation this family can name has a PLAIN NAME in the one frontend home — the "
      "owner's ruling is to hide database names from the users",
      all(re.search(r"^\s+%s: '" % t, LABELS, re.M) for t in
          (lineage.MONTHLY_SALES, lineage.LIVE_SALES_FEED, dd.PRODUCT_CATALOG, dd.TARGETS,
           dd.PLAN_RULES, dd.FLAG_RULES)),
      str([t for t in (lineage.MONTHLY_SALES, lineage.LIVE_SALES_FEED, dd.PRODUCT_CATALOG,
                       dd.TARGETS, dd.PLAN_RULES, dd.FLAG_RULES)
           if not re.search(r"^\s+%s: '" % t, LABELS, re.M)]))
def _uncommented(src):
    """The page with its `//` and `/* */` comments removed. A relation name in a COMMENT is a note
    to the next engineer; the ruling is about what a manager READS, and that is the code."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(ln for ln in src.split("\n") if not ln.strip().startswith("//"))


# `targets` is deliberately NOT in this alternation: it is an ordinary English word and a ROUTE
# segment on two of these pages ("/commcalc/targets/accessories"), so matching it would fail on copy
# that names a SCREEN rather than a table. The infra-names axis of the carrier-vocab guard owns that
# question in general; widening someone else's lock on a guess is what the house rules forbid.
_RELATIONS = r"(raw_sales|raw_catalog|daily_sales_feed|flag_rules|commission_rule)"
check("E7 and no page prints a relation name of its own — every table reaching copy goes through "
      "`sourceLabel()` inside the shared panel, so a manager reads 'monthly sales upload' and never "
      "a table",
      all(not re.search(_RELATIONS, _uncommented(v)) for v in PAGES.values()),
      str([k for k, v in PAGES.items() if re.search(_RELATIONS, _uncommented(v))]))
check("E7b and that scan is NOT VACUOUS — it really does fire on the text it is looking for",
      bool(re.search(_RELATIONS, "const t = 'raw_sales'")))
LEDGER = read("backend/harness_drilldown_pending.txt")
check("E8 all three routes are flipped in the coverage ledger, each with a note saying what drills "
      "AND what it refuses to claim",
      all(re.search(r"^%s\tshared\t.{200,}REFUSES TO CLAIM" % re.escape(r), LEDGER, re.M)
          for r in ("/commcalc/accessory-cost-audit", "/commcalc/accessory-flags"))
      and re.search(r"^/commcalc/accessory-target-plan\tshared\t.{200,}REFUSES TO CLAIM",
                    LEDGER, re.M))
COV = read("backend/harness_drilldown_coverage.py")
check("E9 THE RATCHET SHRANK BY EXACTLY THE THREE ROUTES THIS BATCH SERVED, and never upward",
      "OWES_MAX = 53" in COV and len(re.findall(r"\towes\t", LEDGER)) == 53,
      str(len(re.findall(r"\towes\t", LEDGER))))
GRAPH = read("backend/app/modules/core/module_graph.py")
check("E10 the declaration and this lock are written into the module graph BY HAND, under both "
      "facts it dereferences",
      "'app/modules/commcalc/accessory_money_drilldown.py': ('report_drilldown',)" in GRAPH
      and "'app/modules/commcalc/accessory_money_drilldown.py': ('_lineage',)" in GRAPH
      and GRAPH.count("harness_batch15_drilldown.py") == 2)
WF = read(".github/workflows/carrier-vocab-guard.yml")
check("E11 and a lock nothing runs is not a lock — this file is run by the guard job and its paths "
      "filter names every file it is about",
      "python3 harness_batch15_drilldown.py" in WF
      and all(('"%s"' % f) in WF for f in
              ("backend/app/modules/commcalc/accessory_money_drilldown.py",
               "frontend/src/app/(platform)/commcalc/accessory-flags/page.tsx")))
IDX = read("docs/SYSTEM_DATA_FLOW_INDEX.md")
check("E12 the batch is registered in the index — the section, the declaration, this harness and "
      "all three routes",
      "§CORE-17.3o" in IDX and "accessory_money_drilldown.py" in IDX
      and "harness_batch15_drilldown.py" in IDX
      and all(r in IDX for r in ("/commcalc/accessory-target-plan",
                                 "/commcalc/accessory-cost-audit", "/commcalc/accessory-flags")))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §F  NO FIGURE MOVED — each screen's own pre-batch fold, unfiltered AND under a real filter.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. no figure moved, proved falsifiably ==")

# ── F1/F2  The cost audit. Its five option totals are PRINTED STRAIGHT off the payload, and the
#           page's filter bar narrows the per-rep and per-line TABLES only. Ported here so the
#           comparison is against the screen's own arithmetic rather than against a restatement.
def page_audit_tiles(payload):
    """The options table, exactly as the page renders it: `totals[k]`, and Δ computed on screen from
    two totals."""
    t = payload.get("totals") or {}
    return {k: t.get(k) for k in aca.OPTION_KEYS}


def page_audit_deltas(payload):
    t = payload.get("totals") or {}
    return {k: round(float(t.get(k) or 0) - float(t.get("current") or 0), 2)
            for k in ("option_b", "option_c", "option_r")}


def filter_rows(rows, stores):
    """A port of the page's `filterRows(repRows, filt, {store, market, rep})` for the STORE axis —
    the only axis these fixtures differ on."""
    return [r for r in rows if not stores or r.get("store") in stores]


_pre = page_audit_tiles(AUDIT)
check("F1 UNFILTERED — every option total the page prints is identical to the figure its panel now "
      "reads, to the cent",
      all(cents(_pre[k]) == cents(AD[k]["figure"]["amount"]) for k in _pre)
      and all(cents(page_audit_deltas(AUDIT)[k])
              == cents(AD["delta_%s" % k.split("_")[-1]]["figure"]["amount"])
              for k in page_audit_deltas(AUDIT)))

_shown = filter_rows(AUDIT["by_rep"], {"1115 Liberty Ave"})
check("F2a THE FILTER REALLY DROPS ROWS — otherwise F2 is the unfiltered case twice",
      0 < len(_shown) < len(AUDIT["by_rep"]), f"{len(_shown)} of {len(AUDIT['by_rep'])}")
check("F2b UNDER THAT FILTER the five option totals are UNCHANGED — the page prints the server's "
      "own figures and the filter never touched them, which is exactly why the panels are not "
      "narrowed (§CORE-17.3o)",
      page_audit_tiles(AUDIT) == _pre
      and all(agrees(AD[k], _pre[k]) for k in _pre))
check("F2c and narrowing WOULD have moved them, which is the whole argument: Σ over the rows the "
      "table is showing is a different number from the tile",
      cents(sum(r["current"] for r in _shown)) != cents(_pre["current"]))

# ── F3  The flag report. Its tiles are the server's `summary`; its table is filtered in the browser.
def page_flag_filter(rows, store="", rep="", reason=""):
    """A port of the page's own row predicate."""
    return [r for r in rows
            if (not store or r.get("store") == store)
            and (not rep or r.get("rep") == rep)
            and (not reason or r.get("flag_reason") == reason)]


check("F3a UNFILTERED — both money tiles are identical to their panels' figures",
      agrees(FD["total"], FLAGS["summary"]["total"])
      and agrees(FD["chargeback_total"], FLAGS["summary"]["chargeback_total"]))
_fshown = page_flag_filter(FLAG_ROWS, store="88 Market St")
check("F3b THE FILTER REALLY DROPS ROWS", 0 < len(_fshown) < len(FLAG_ROWS),
      f"{len(_fshown)} of {len(FLAG_ROWS)}")
check("F3c UNDER THAT FILTER the tiles are unchanged, because the page reads the server's summary — "
      "so the panels still agree with them to the cent and nothing moved",
      agrees(FD["total"], FLAGS["summary"]["total"])
      and cents(sum(r["ext_price"] for r in _fshown)) != cents(FLAGS["summary"]["total"]))

# ── F4  The plan. Every filter there is a QUERY PARAMETER, so the server re-folds and the figures
#        legitimately change — the panel must follow them.
_narrow = plan_result(stores=[s for s in STORES if s["market"] == "LI"], selected=("S1", "S2"))
_ND = dd.plan_drilldowns(_narrow)
check("F4a THE MARKET FILTER REALLY DROPS STORES — it is a query parameter, so the server folds "
      "again over fewer rows",
      0 < len(_narrow["rows"]) < len(PLAN["rows"])
      and cents(_narrow["totals"]["m1_acc"]) != cents(PLAN["totals"]["m1_acc"]))
check("F4b and every panel follows the re-folded figure exactly — one fold for the tile and its "
      "evidence, which is why there is nothing for `drill-narrow` to do here",
      all(agrees(_ND[k], _narrow["totals"][k]) for k in _ND)
      and all(len(_ND[k]["rows"]) == len(_narrow["rows"]) for k in _ND))
check("F4c the page's own footer totals are the SAME keys the panels read, so the two cannot drift",
      all(("plan!.totals.%s" % k) in PAGES["plan"]
          for k in ("m2_acc", "m1_acc", "mtd_acc", "projected_acc", "current_target", "assigned",
                    "extension")))

# ── F5/F6  The safety net.
check("F5 THE SAFETY NET — a panel that does not agree with its tile to the cent offers NO drill, "
      "so even a disagreement cannot move a figure. One cent is enough",
      agrees(FD["total"], FLAGS["summary"]["total"])
      and not agrees(FD["total"], FLAGS["summary"]["total"] + 0.01))
check("F6 and that gate is the SHARED one, passed to `useDrillTile` on every page in this batch "
      "rather than re-written three times",
      all("agreesToTheCent" in v[v.index("useDrillTile("):v.index("useDrillTile(") + 320]
          for v in PAGES.values() if "useDrillTile(" in v)
      and len([v for v in PAGES.values() if "useDrillTile(" in v]) == 3,
      str([k for k, v in PAGES.items()
           if "useDrillTile(" not in v
           or "agreesToTheCent" not in v[v.index("useDrillTile("):
                                         v.index("useDrillTile(") + 320]]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §Z  NEGATIVE CONTROLS — each one tampers with ONE side and must come back RED.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== Z. negative controls (each must come back RED) ==")


def _panel_over(rows, amount, **kw):
    """One panel through the REAL shared fold, so a control exercises the same arithmetic the
    production path does."""
    spec = {"key": "k", "total": "t", "label": "L", "row_label": "label",
            "keys": ("store_code",), "unit": rd.UNIT_MONEY, "schema": "commcalc"}
    spec.update(kw)
    return rd.tiles((spec,), rows, {"t": amount})["k"]


_ROWS = [{"store_code": "A", "label": "A", "v": 10.0}, {"store_code": "B", "label": "B", "v": 5.0}]

ctl("Z1 A ONE-CENT TOTAL — a tile a penny away from its own rows is reported as a disagreement, "
    "not rounded away, and the screen then offers no drill",
    _panel_over(_ROWS, 15.01, value="v", tables=("targets",))["tie_out"]["ties"] is False
    and not agrees(_panel_over(_ROWS, 15.01, value="v", tables=("targets",)), 15.00))

ctl("Z2 A DROPPED ROW — a declaration that leaves a row out of the panel makes the figure and its "
    "evidence disagree, in words",
    _panel_over(_ROWS[:1], 15.0, value="v", tables=("targets",))["tie_out"]["ties"] is False)

ctl("Z3 A FIGURE FOLDED IN AT $0.00 NOBODY RECORDED — the careless declaration reads "
    "`current_target` without asking `has_target_row`, so the store nobody set a target for is "
    "counted as a MEASURED zero. It ties, exactly as the honest one does, and the lie is visible "
    "only on the row",
    (lambda careless: careless["tie_out"]["ties"] is True
     and next(r for r in careless["rows"] if r["row_key"]["store_code"] == "S2")["present"] is True
     and next(r for r in careless["rows"]
              if r["row_key"]["store_code"] == "S2")["measured"] is True
     and next(r for r in careless["rows"]
              if r["row_key"]["store_code"] == "S2")["sources"] == ["targets"])(
        _panel_over(PLAN["rows"], PLAN["totals"]["current_target"],
                    value="current_target", tables=("targets",), row_label="address")))
ctl("Z3b and the HONEST declaration differs on exactly that row and nowhere else — same figure, "
    "same tie, one row shown instead of counted",
    cents(PD["current_target"]["figure"]["amount"])
    == cents(_panel_over(PLAN["rows"], PLAN["totals"]["current_target"], value="current_target",
                         tables=("targets",), row_label="address")["figure"]["amount"])
    and PD["current_target"]["tie_out"]["shown_not_counted"] == 1
    and _panel_over(PLAN["rows"], PLAN["totals"]["current_target"], value="current_target",
                    tables=("targets",), row_label="address")["tie_out"]["shown_not_counted"] == 0)

ctl("Z4 A COUNTED ROW WITH NO NUMBER — a declaration that counts a row it has no figure for cannot "
    "be checked at all, and `ties` comes back None rather than True",
    _panel_over(_ROWS + [{"store_code": "C", "label": "C", "v": None}], 15.0,
                value="v", tables=("targets",),
                in_figure=lambda _r: True)["tie_out"]["ties"] is None)

ctl("Z5 A SUGGESTION THAT CLAIMS A SOURCE — the careless declaration names the sales feeds on a "
    "figure that was read out of nothing, and the panel then reports a provenance nobody can check "
    "instead of the untraceable origin that is the truth",
    (lambda bad: bad["untraceable_origins"] == 0
     and [s["table"] for s in bad["sources"]] == sorted(dd.SALES_FEEDS)
     and PD["assigned"]["untraceable_origins"] == len(PLAN["rows"]))(
        _panel_over(PLAN["rows"], PLAN["totals"]["assigned"], value="suggested_target",
                    tables=dd.SALES_FEEDS, row_label="address")))

ctl("Z6 AN EXTENSION DROPPED INSTEAD OF MARKED — the declaration that refuses to count the store "
    "with no target row makes the panel DISAGREE with a tile it has no business disagreeing with, "
    "because the producer really did add that figure",
    _panel_over(PLAN["rows"], PLAN["totals"]["extension"],
                value=lambda r: (r.get("extension") if r.get("has_target_row") else None),
                tables=("targets",), row_label="address")["tie_out"]["ties"] is False)

ctl("Z7 A GOAL-DEPENDENT PANEL SERVED WITH NO GOAL — the careless declaration serves `assigned` "
    "anyway, and the reader gets a $0.00 figure 'tying' to a list of four stores that all carry no "
    "number at all",
    (lambda bad: cents(bad["figure"]["amount"]) == 0 and bad["tie_out"]["counted_rows"] == 0
     and bad["tie_out"]["ties"] is True and "assigned" not in _NG)(
        _panel_over(_NOGOAL["rows"], _NOGOAL["totals"]["assigned"], value="suggested_target",
                    tables=(), row_label="address")))

ctl("Z8 A NARROWED PANEL ON THESE SCREENS — Σ over the rows the cost audit's table is showing is a "
    "DIFFERENT figure from the tile, so a page that narrowed its panel here would be explaining a "
    "number nobody is reading",
    cents(_panel_over(_shown, _pre["current"], value="current",
                      keys=("rep",), row_label="rep",
                      tables=dd.PAY_SALES_FEEDS)["tie_out"]["rows_total"])
    != cents(_pre["current"]))

ctl("Z9 A DELTA TAKEN AT THE TOTAL — a panel for Δ A would have to assert rows behind a figure the "
    "producer never folded per rep, and `by_rep` has no field to read",
    all("delta_a" not in r for r in AUDIT["by_rep"])
    and rd.tiles(({"key": "delta_a", "total": "delta_a", "label": "L", "value": "delta_a",
                   "tables": ("raw_sales",), "row_label": "rep", "keys": ("rep",)},),
                 AUDIT["by_rep"], {"delta_a": 1.23})["delta_a"]["tie_out"]["ties"] is False)

ctl("Z10 A TAMPERED CHARGEBACK ROW — one line's default amount changed makes the exposure tile and "
    "its evidence disagree, which is the point: that figure is a setting applied to rows, so a "
    "change to either side must be visible",
    _panel_over([dict(r, chargeback_amount=(r["chargeback_amount"] + 1.0 if i == 0
                                            else r["chargeback_amount"]), label=r["item_desc"])
                 for i, r in enumerate(FLAG_ROWS)],
                FLAGS["summary"]["chargeback_total"], value="chargeback_amount",
                keys=("trans_id",), tables=(dd.FLAG_RULES,))["tie_out"]["ties"] is False)

ctl("Z11 THE UN-WIRE CONTROL — this file's own §E5 scan really does fire. A page with "
    "`narrowPanels` in it is detected, so E5 passing means the text is absent rather than the scan "
    "being blind",
    "narrowPanels" in "const x = narrowPanels(panels, keep)"
    and all("narrowPanels" not in v for v in PAGES.values()))

ctl("Z12 THE DEREFERENCE CONTROL — a declaration that re-spelled a relation name instead of "
    "dereferencing it would be a second copy, and §A4's scan catches exactly that pattern",
    '"raw_catalog"' in _reg_def and '"raw_catalog"' not in _reg_body
    and REG.count('"raw_catalog"') == 1)


print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("\nBatch 15 — the three accessory money reports drill, eight measured grains over three row "
      "shapes, five provenance answers, seven live defects reported and NO FIGURE MOVED.")
