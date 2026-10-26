"""BATCH 20 OF THE §CORE-17 DRILL-DOWN PROGRAMME — the four PERFORMANCE-AND-COACHING reports.

  /commcalc/productivity     Productivity — output per hour worked vs the store's own baseline
  /commcalc/coaching         Rep Coaching — the money a missed tier costs each person
  /commcalc/atu-opportunity  Autopay Opportunity — card customers who were never asked
  /commcalc/kpi-failing      Failing KPIs — every KPI below target, at store and at rep grain

WHAT THIS PROVES, AND WHY IT IS WORTH A FILE
────────────────────────────────────────────
Owner ask 2026-10-24: *every report built on platform data must let him see WHERE each figure came
from.* This family is what a manager reads before a coaching conversation, so a figure here that
cannot name its rows is an accusation against a person with nothing behind it. That is the sharpest
version of the ask in the programme so far, and it is why the ABSENCES matter more here than
anywhere: a rep nobody clocked, a KPI the carrier never published and a pay breakdown the run never
itemised must each read as a blank with a reason, never as a zero somebody could coach on.

It also settles §CORE-17.3o in BOTH directions inside ONE family — two of the four pages fold their
own tiles in the browser and are narrowed, two put every filter in the query string and are not —
and it is the first batch in which a HOURS unit is folded, so `report_drilldown.UNIT_HOURS` finally
has a caller.

THE PRODUCERS ARE DRIVEN FOR REAL. `productivity.compute_productivity`,
`atu_opportunity.fold_transactions` / `summarize` / `by_store`, `kpi_failing.store_rows` /
`rep_rows` / `summarize` and `payout_slice_evidence.basis` / `slice_value` are all called directly.
The GRAIN COUNT in §H is therefore MEASURED off the panels, as a SET, not asserted.

THE ONE FOLD THAT LIVES INSIDE THE FRAMEWORK ROUTER IS REPRODUCED **AND PINNED**. The coaching
report's per-rep fold is written inline in `commcalc/router.py`, which cannot be imported without
FastAPI, so §C reproduces it line-for-line AND pins it to the router's OWN SOURCE TEXT: if any of
those expressions changes, this file goes red rather than quietly measuring a fold nobody runs. That
is batch 17's and batch 19's posture for exactly this problem.

The whole file is DB-FREE and stdlib-only.
Run: `cd backend && python3 harness_batch20_drilldown.py`
"""
import ast
import copy
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.account import liabilities_drilldown as liab               # noqa: E402
from app.modules.commcalc import atu_opportunity as atu                     # noqa: E402
from app.modules.commcalc import carrier_pay_drilldown as cpd               # noqa: E402
from app.modules.commcalc import data_lineage_registry as lineage           # noqa: E402
from app.modules.commcalc import kpi_failing as kpif                        # noqa: E402
from app.modules.commcalc import payout_slice_evidence as pse               # noqa: E402
from app.modules.commcalc import performance_coaching_drilldown as dd       # noqa: E402
from app.modules.commcalc import processor_money_drilldown as pmd           # noqa: E402
from app.modules.commcalc import productivity as prod                       # noqa: E402
from app.modules.core import report_drilldown as rd                         # noqa: E402

PASS, FAIL = [], []
ROOT = ".."


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def ctl(name, red):
    """A NEGATIVE CONTROL / MUTATION. `red` must be True — the tampered case really did come back
    wrong. A control that passes is a check that cannot fail, printed as though it had."""
    check("MUT " + name, bool(red))


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


DECL = read("backend/app/modules/commcalc/performance_coaching_drilldown.py")
ROUTER = read("backend/app/modules/commcalc/router.py")
REGISTRY = read("backend/app/modules/commcalc/data_lineage_registry.py")
ATU_SRC = read("backend/app/modules/commcalc/atu_opportunity.py")
LABELS = read("frontend/src/lib/sourceLabels.ts")
IDX = read("docs/SYSTEM_DATA_FLOW_INDEX.md")

PAGES = {
    "productivity": read("frontend/src/app/(platform)/commcalc/productivity/page.tsx"),
    "coaching": read("frontend/src/app/(platform)/commcalc/coaching/page.tsx"),
    "atu-opportunity": read("frontend/src/app/(platform)/commcalc/atu-opportunity/page.tsx"),
    "kpi-failing": read("frontend/src/app/(platform)/commcalc/kpi-failing/page.tsx"),
}
#: The two pages that fold their own tiles in the browser, and the two that do not. DECIDED BY
#: READING EACH PAGE (§CORE-17.3o), and §F locks each decision in BOTH directions.
NARROWED_PAGES = ("coaching", "kpi-failing")
SERVER_FOLDED_PAGES = ("productivity", "atu-opportunity")


def _literals(src):
    """Every STRING literal the module's CODE contains, and every number.

    A table name or a dollar figure may appear in a DOCSTRING or a comment — that is a note to the
    next engineer and the whole point of writing the defects down. The rulings are about the CODE, so
    the two are told apart by PARSING rather than by grepping.
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


def _code_only(src):
    """The module's CODE as text, with every docstring and comment gone, by UNPARSING the tree.

    The rulings are about code; the defects are written down in prose. A grep cannot tell the two
    apart — `tier < 1.0` appears in this declaration's docstring precisely BECAUSE the threshold is
    the producer's and the next reader needs to know where it lives.
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


DECL_CODE = _code_only(DECL)


def strip_comments(src):
    """A TS/JS source with its comments removed, so a rule WRITTEN DOWN in a page's header cannot be
    mistaken for the rule being broken in its code. Batch 18's §F posture, reused verbatim."""
    out = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", out)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. ONE FACT, ONE HOME — the design fix, with BOTH halves ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("A1 the declaration dereferences the SALES UNION PROVENANCE resolver by OBJECT IDENTITY — the "
      "one home for 'which tables did this period's sales actually come from', not a copy of its "
      "logic",
      dd.sales_union_tables is pmd.sales_union_tables)
check("A2 THE DESIGN FIX IN THE ONE HOME — it now recognises the TRANSACTION-grain union's own way "
      "of saying the second relation contributed, so one function answers for BOTH unions instead "
      "of a second copy appearing for the transaction grain",
      pmd.sales_union_tables({"primary": lineage.LIVE_SALES_FEED, "primary_rows": 9,
                              "other": lineage.MONTHLY_SALES, "other_rows": 4,
                              "other_only_rows": 4})
      == (lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES))
check("A2b and EVERY EXISTING CALLER IS BYTE-IDENTICAL: a per-DAY meta carries no `other_only_rows` "
      "at all, so a day read that contributed nothing still names the leading relation alone",
      pmd.sales_union_tables({"primary": lineage.LIVE_SALES_FEED, "primary_rows": 9,
                              "other": lineage.MONTHLY_SALES, "other_rows": 4})
      == (lineage.LIVE_SALES_FEED,))
ctl("A2c control: the new key is not a free pass — a transaction read the other side contributed "
    "NOTHING to still names one relation",
    pmd.sales_union_tables({"primary": lineage.LIVE_SALES_FEED, "primary_rows": 9,
                            "other": lineage.MONTHLY_SALES, "other_rows": 4,
                            "other_only_rows": 0}) == (lineage.LIVE_SALES_FEED,))
check("A3 THE TIME CLOCK IS A REGISTRY CONSTANT AS OF THIS BATCH, dereferenced by identity rather "
      "than spelled — 'which relation records an hour somebody actually worked' has one home",
      dd.TIME_CLOCK is lineage.TIME_CLOCK and lineage.TIME_CLOCK == "timelog")
check("A3b AND THE OTHER HALF OF THE FIX — the one pre-existing literal copy is re-keyed in the "
      "SAME commit, bound by OBJECT IDENTITY and not by equal text. Writing the registry without "
      "wiring the callers to it is not a fix at all (CLAUDE.md §19.18)",
      liab.TIMELOG is lineage.TIME_CLOCK)


#: WHERE A PROVENANCE NAME IS A PROVENANCE NAME. The registry's scope is stated in the constant's
#: own comment and is the scope `STORE_EXPENSES` has carried since batch 18: this is the one home for
#: WHAT A DRILL-DOWN TELLS A READER A FIGURE CAME OUT OF. A `.table("timelog")` read, a
#: `source_table="timelog"` audit argument and `core/scope.WORKED_AT_SOURCES` (its own
#: table/column/soft-delete registry, with its own lock) are I/O, not second statements about a
#: figure's origin — `shifts` has coexisted with `WORKED_AT_SOURCES` on exactly these terms for two
#: batches. So the lock scans the files that MAKE provenance claims: every drill-down declaration and
#: every lineage registry. A second copy there is the divergence, and it fails the build.
def _declares_provenance(rel):
    return rel.endswith("_drilldown.py") or "lineage" in os.path.basename(rel) \
        or rel.endswith("report_drilldown.py")


def _sole_home(name):
    """Every file whose CODE makes a PROVENANCE CLAIM spelling `name` as a string literal, with how
    many lines do so. One home means ONE copy, not zero."""
    out = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "backend", "app")):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(base, f)
            rel = os.path.relpath(path, os.path.join(ROOT, "backend"))
            if not _declares_provenance(rel):
                continue
            src = open(path, encoding="utf-8", errors="replace").read()
            try:
                strings, _n = _literals(src)
            except SyntaxError:
                continue
            if name not in strings:
                continue
            spelled = [ln for ln in src.splitlines()
                       if '"%s"' % name in ln and ".table(" not in ln
                       and "#" not in ln.split('"')[0]]
            if spelled:
                out.append((rel, len(spelled)))
    return sorted(out)


_homes = _sole_home("timelog")
check("A3c BUILD LOCK — across every drill-down declaration and lineage registry in the backend, "
      "the time clock's name is spelled in exactly ONE file, the registry, on exactly ONE line. A "
      "second copy in any declaration fails the build",
      _homes == [("app/modules/commcalc/data_lineage_registry.py", 1)], str(_homes))
ctl("A3d control: the sole-home scan is NOT vacuous — pointed at this batch's own declaration it "
    "finds the name it really does spell, so a reintroduced copy would be seen",
    _sole_home("storeops") != [] and len(_sole_home("rep_commissions")) >= 1)
check("A4 THE REP-PAY SNAPSHOT IS *NOT* A SECOND REGISTRY CONSTANT. It already has a home — "
      "`carrier_pay_drilldown.REP_COMMISSIONS`, named there by batch 11 — and this declaration "
      "dereferences THAT by identity. A constant for it in the feed registry would be the very "
      "copy the registry exists to prevent, and that reasoning is written INTO the registry",
      dd.REP_COMMISSIONS is cpd.REP_COMMISSIONS
      and "rep_commissions" not in _literals(REGISTRY)[0]
      and "REP-PAY SNAPSHOT IS DELIBERATELY" in REGISTRY)
check("A5 the two carrier-side relation names are the registry's own, by identity",
      dd.DLAR_STORE is lineage.DLAR_STORE and dd.OPS_CHARGEBACK is lineage.OPS_CHARGEBACK)
check("A6 'IS THIS STORED PAY BREAKDOWN A FIGURE AT ALL' IS ASKED OF ITS ONE HOME, never inferred "
      "from the stored column — the §CORE-17.3j ruling one fact along. Both the predicate and the "
      "field it reads are bound by OBJECT IDENTITY",
      dd.slices_are_evidence is pse.is_evidence and dd.SLICE_BASIS_FIELD is pse.BASIS_FIELD
      and dd.REASON_SLICES_NOT_RECORDED is pse.BASIS_NOT_RECORDED)
check("A7 and the failing-KPI report's two GRAIN WORDS are `kpi_failing`'s own, by identity",
      dd.GRAIN_STORE is kpif.GRAIN_STORE and dd.GRAIN_REP is kpif.GRAIN_REP)
check("A8 every reason this declaration can emit has an entry in the ONE vocabulary, so a reader "
      "gets a sentence and never a bare field name",
      all(k in rd.UNMEASURED_REASONS for k in
          (dd.REASON_NO_PUNCHES, dd.REASON_KPI_NOT_REPORTED, dd.REASON_SLICES_NOT_RECORDED,
           "not_recorded")))
check("A9 PURE — the declaration imports no framework, does no I/O, reads no clock and holds no "
      "org, carrier, tenant, product or store name in its CODE (RULE TWO)",
      not re.search(r"(?m)^\s*(import|from)\s+(fastapi|pydantic|requests|httpx|datetime|time)\b", DECL)
      and "sb()" not in DECL and ".execute()" not in DECL)
_money_lits = [n for n in DECL_NUMBERS if isinstance(n, float)]
check("A9b and it holds NO dollar literal, rate or threshold of its own: a count comparison asks "
      "the one home's own rounding rather than writing `0.0`",
      _money_lits == [], str(_money_lits))
check("A9c the TIER THRESHOLD is the producer's, not this file's — the declaration reads the "
      "verdict the handler now states on each row instead of re-testing a number, so its CODE "
      "compares nothing against a tier and spells no threshold (the docstring says where the "
      "threshold lives, which is the opposite of holding one)",
      "below_tier" in DECL_CODE and "1.0" not in DECL_CODE
      and not re.search(r"tier['\"]?\s*\]?\s*[<>]", DECL_CODE))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. PRODUCTIVITY — the real producer, three grains on one employee-at-a-store row ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
#: (store_code, rep_key) → the cell `_prod_gather` builds, in its own shape.
PROD_CELLS = {
    ("S1", "ANA"): {"store_label": "Store One", "market": "North", "rep_label": "Ana",
                    "boxes": 12.0, "acc_sales": 640.25},
    ("S1", "BEN"): {"store_label": "Store One", "market": "North", "rep_label": "Ben",
                    "boxes": 3.0, "acc_sales": 90.75},
    # ON THE CLOCK, SOLD NOTHING — the shape `_prod_gather` creates from a time-clock row at a
    # selling store the sales union never named this person at (defect 2).
    ("S1", "CAI"): {"store_label": "Store One", "market": "North", "rep_label": "Cai",
                    "boxes": 0.0, "acc_sales": 0.0},
    ("S2", "DOV"): {"store_label": "Store Two", "market": "South", "rep_label": "Dov",
                    "boxes": 7.0, "acc_sales": 310.10},
}
#: (store_code, rep_key) → closed punch hours. Ben has SALES AND NO PUNCH, which is the absence this
#: batch exists to stop printing as a zero.
PROD_HOURS = {("S1", "ANA"): 40.0, ("S1", "CAI"): 16.0, ("S2", "DOV"): 22.5}
PROD_META = {"primary": lineage.LIVE_SALES_FEED, "primary_rows": 880,
             "other": lineage.MONTHLY_SALES, "other_rows": 120, "filled_rows": 14}

_pres = prod.compute_productivity(PROD_CELLS, PROD_HOURS)
PP = {"period": "2026-09", "org_id": "org", **_pres, "source_meta": PROD_META,
      "filters": {}, "applied": {}}
PD = dd.productivity_drilldowns(PP)

check("B1 the real producer was driven and gave two stores and four employee rows",
      len(_pres["stores"]) == 2
      and sum(len(s["reps"]) for s in _pres["stores"]) == 4, str(_pres["totals"]))
check("B2 THREE panels per store and three for the company — boxes, accessory dollars and hours",
      sorted(PD) == sorted(["boxes", "acc_sales", "hours",
                            "store.S1.boxes", "store.S1.acc_sales", "store.S1.hours",
                            "store.S2.boxes", "store.S2.acc_sales", "store.S2.hours"]), str(sorted(PD)))
for _k in PD:
    check(f"B3[{_k}] the panel's figure IS the producer's own, to the cent, and its rows add up to it",
          PD[_k]["tie_out"]["ties"] is True, str(PD[_k]["tie_out"]["words"]))
check("B4 each store panel carries only ITS OWN employees — a panel can never hold a different set "
      "of people than the header it opened from",
      PD["store.S1.boxes"]["rows_total_count"] == 3
      and PD["store.S2.boxes"]["rows_total_count"] == 1)
check("B5 and the company panels hold all four",
      PD["boxes"]["rows_total_count"] == 4 and PD["hours"]["rows_total_count"] == 4)
check("B6 the three figures agree with the producer's own numbers through the SHARED offer gate",
      agrees(PD["boxes"], _pres["totals"]["boxes"])
      and agrees(PD["acc_sales"], _pres["totals"]["acc_sales"])
      and agrees(PD["hours"], _pres["totals"]["hours"])
      and agrees(PD["store.S1.hours"], _pres["stores"][0]["store_hours"]))
check("B7 THREE UNITS, NOT ONE — boxes is a count, accessory dollars are money and hours are HOURS, "
      "which is this programme's first caller for that unit",
      PD["boxes"]["figure"]["unit"] == rd.UNIT_COUNT
      and PD["acc_sales"]["figure"]["unit"] == rd.UNIT_MONEY
      and PD["hours"]["figure"]["unit"] == rd.UNIT_HOURS)
check("B8 TWO PROVENANCES ON ONE ROW — what was SOLD names whichever sales relations the period's "
      "union actually read; what was WORKED names the time clock and nothing else. The schedule is "
      "never named, because it records an intention rather than an hour",
      [s["table"] for s in PD["boxes"]["sources"]] == sorted([lineage.LIVE_SALES_FEED,
                                                              lineage.MONTHLY_SALES])
      and [s["table"] for s in PD["hours"]["sources"]] == [lineage.TIME_CLOCK]
      and lineage.SCHEDULE not in [s["table"] for s in PD["hours"]["sources"]])
check("B9 the hours panel carries the STOREOPS schema explicitly, because that relation does not "
      "live where the rest of this family's do",
      all(o["schema"] == dd.SCHEMA_STOREOPS
          for r in PD["hours"]["rows"] for o in r["origins"] if o["table"]))
_ben = [r for r in PD["hours"]["rows"] if r["label"] == "Ben"]
check("B10 ABSENCE IS NEVER ZERO — the employee with sales and NO PUNCH is shown WITHOUT a figure, "
      "names NO relation, is NOT counted and says why in the one vocabulary's words",
      len(_ben) == 1 and _ben[0]["amount"] is None and _ben[0]["measured"] is False
      and _ben[0]["in_figure"] is False and _ben[0]["reason"] == dd.REASON_NO_PUNCHES
      and _ben[0]["sources"] == []
      and _ben[0]["reason_label"] == rd.UNMEASURED_REASONS[dd.REASON_NO_PUNCHES], str(_ben))
check("B10b AND NO FIGURE MOVED BY A CENT — that employee contributed 0.00 hours to the producer's "
      "own store total and contributes 0.00 here. Only the ROW changed, from a measured zero into a "
      "stated blank, which is the entire argument for putting it there",
      cents(_pres["stores"][0]["store_hours"]) == cents(40.0 + 16.0)
      and PD["store.S1.hours"]["tie_out"]["ties"] is True
      and PD["store.S1.hours"]["tie_out"]["shown_not_counted"] == 1)
check("B10c and that same employee IS counted in the two SALES panels, because the sales union did "
      "answer for them — the absence is of an hour, not of a sale",
      all(r["measured"] for r in PD["store.S1.boxes"]["rows"] if r["label"] == "Ben"))
_cai = [r for r in PD["store.S1.boxes"]["rows"] if r["label"] == "Cai"]
check("B11 DEFECT 2, REPORTED NOT HIDDEN — the employee the sales union never named carries a "
      "measured 0 exactly as the producer counted them, because the row holds no transaction count "
      "to tell that from somebody who sold nothing; the panel says so in its note",
      len(_cai) == 1 and _cai[0]["amount"] == 0.0 and _cai[0]["measured"] is True
      and "sold nothing" in (PD["store.S1.boxes"]["note"] or ""))
ctl("B12 control: a DROPPED employee breaks the tie-out — the fold is a real comparison, not a "
    "tautology",
    dd.productivity_drilldowns(
        {**PP, "stores": [{**_pres["stores"][0],
                           "reps": _pres["stores"][0]["reps"][1:]}]}
    )["store.S1.boxes"]["tie_out"]["ties"] is False)
ctl("B12b control: a MOVED cent breaks it too",
    dd.productivity_drilldowns(
        {**PP, "totals": {**_pres["totals"], "acc_sales": _pres["totals"]["acc_sales"] + 0.01}}
    )["acc_sales"]["tie_out"]["ties"] is False)
check("B13 A REPORT WITH NO EMPLOYEE ROWS DRILLS TO NOTHING — an empty panel asserting a figure "
      "behind an empty list ties only by accident and reads like a bug",
      dd.productivity_drilldowns({"stores": [], "totals": {"boxes": 0}}) == {}
      and dd.productivity_drilldowns(None) == {})
_nohours = dd.productivity_drilldowns({
    **PP, "stores": [{"store_code": "S9", "store_label": "Nine", "store_hours": 0.0,
                      "store_boxes": 2.0, "store_acc": 5.0,
                      "reps": [{"rep": "Eve", "rep_key": "EVE", "boxes": 2.0, "acc_sales": 5.0,
                                "hours": 0.0, "no_hours": True}]}],
    "totals": {"boxes": 2.0, "acc_sales": 5.0, "hours": 0.0}})
check("B14 A ZERO OVER ZERO COUNTED ROWS IS SERVED NO PANEL — a store where nobody was clocked at "
      "all would tie 0 against 0 (True) and read as a measurement, so the HOURS panel is not served "
      "while the two sales panels still are",
      "store.S9.hours" not in _nohours and "hours" not in _nohours
      and "store.S9.boxes" in _nohours, str(sorted(_nohours)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. REP COACHING — the router's own fold, reproduced AND pinned to its source text ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The per-rep fold is written inline in `commcalc/router.py`, which cannot be imported without
# FastAPI. So it is reproduced here line-for-line and PINNED to the router's own text below: if any
# of those expressions changes, this file goes red rather than measuring a fold nobody runs.
COACH_PINS = (
    "at_risk = (round(subtotal * (1.0 - tier), 2)",
    "if (tier < 1.0 and _sub_known is not None) else 0.0)",
    "'money_on_table': round(at_risk + cbd['deducted'] + ops_amt, 2)",
    "'below_tier': tier < 1.0,",
    "_pse_mod().BASIS_FIELD: _pse_mod().basis(cr),",
    "'total_at_risk': round(sum(r['at_risk'] for r in reps), 2)",
    "'total_chargebacks': round(sum(r['chargeback_deducted'] for r in reps), 2)",
    "'below_tier': sum(1 for r in reps if r['tier'] < 1.0)",
    "_sub_known = _pse_mod().slice_value(cr, 'subtotal')",
)
for _p in COACH_PINS:
    check(f"C0 the router's coaching fold still reads as this file reproduces it — pinned: {_p[:48]}",
          _p in ROUTER, _p)

#: Stored `rep_commissions` rows, in the shapes `payout_slice_evidence` tells apart. DANA is the one
#: this batch exists for: plan mode, the plan paid, the subtotal of that pay is zero — so her
#: breakdown was never itemised and her "at risk" of $0.00 is a blank, not a measurement.
COMM_ROWS = [
    {"storeops_name": "Ana", "epay_salesperson": "ANA", "store": "S1", "tier": 0.6,
     "kpis_met": 3, "total_kpis": 7, "subtotal": 1000.0, "total_payout": 600.0,
     "final_payout": 600.0, "kpi_values": {"atu": 40, "protect": 90}},
    {"storeops_name": "Ben", "epay_salesperson": "BEN", "store": "S1", "tier": 1.0,
     "kpis_met": 7, "total_kpis": 7, "subtotal": 500.0, "total_payout": 500.0,
     "final_payout": 500.0, "kpi_values": {"atu": 70, "protect": 95}},
    {"storeops_name": "Dana", "epay_salesperson": "DANA", "store": "S2", "tier": 0.5,
     "kpis_met": 2, "total_kpis": 7, "subtotal": 0.0, "plan_comm": 800.0,
     "tier_source": pse.PLAN_MODE_TIER_SOURCE, "total_payout": 400.0, "final_payout": 400.0,
     "kpi_values": {"atu": 20}},
]
CB_ROWS = [{"epay_salesperson": "ANA", "amount": 75.0, "deduct": True},
           {"epay_salesperson": "ANA", "amount": 25.0, "deduct": False}]
OPS_LINES = {"BEN": [{"label": "short deposit", "amount": 40.0, "status": "posted"}]}


def coach_fold():
    """THE ROUTER'S OWN PER-REP FOLD, line-for-line (pinned by §C0), plus its summary."""
    cb_by = {}
    for it in CB_ROWS:
        d = cb_by.setdefault(it["epay_salesperson"], {"total": 0.0, "count": 0, "deducted": 0.0})
        d["count"] += 1
        d["total"] += it["amount"]
        if it["deduct"]:
            d["deducted"] += it["amount"]
    reps = []
    for cr in COMM_ROWS:
        name = cr["storeops_name"]
        tier = float(cr.get("tier") or 0)
        _sub_known = pse.slice_value(cr, "subtotal")
        subtotal = float(_sub_known or 0)
        at_risk = (round(subtotal * (1.0 - tier), 2)
                   if (tier < 1.0 and _sub_known is not None) else 0.0)
        keys = {name.upper(), (cr.get("epay_salesperson") or "").upper()} - {""}
        cbd = {"total": 0.0, "count": 0, "deducted": 0.0}
        for kk in keys:
            x = cb_by.get(kk)
            if x:
                cbd["total"] += x["total"]
                cbd["count"] += x["count"]
                cbd["deducted"] += x["deducted"]
        total_payout = round(float(cr.get("total_payout") or 0), 2)
        ops_lines = [ln for kk in keys for ln in OPS_LINES.get(kk, [])]
        ops_raw = round(sum(ln["amount"] for ln in ops_lines), 2)
        ops_amt = min(ops_raw, max(0.0, total_payout - cbd["deducted"])) if ops_raw > 0 else ops_raw
        reps.append({
            "rep": name, "store": cr["store"], "market": "North", "tier": tier,
            "kpis_met": cr["kpis_met"], "total_kpis": cr["total_kpis"],
            "subtotal": round(subtotal, 2), "total_payout": total_payout,
            "final_payout": round(float(cr.get("final_payout") or 0) - ops_amt, 2),
            "at_risk": at_risk, "chargeback_total": round(cbd["total"], 2),
            "chargeback_deducted": round(cbd["deducted"], 2), "chargeback_count": cbd["count"],
            "ops_chargeback_deduction": round(ops_amt, 2), "ops_chargeback_lines": ops_lines,
            "money_on_table": round(at_risk + cbd["deducted"] + ops_amt, 2),
            "below_tier": tier < 1.0,
            pse.BASIS_FIELD: pse.basis(cr),
        })
    reps.sort(key=lambda r: -r["money_on_table"])
    summary = {"reps": len(reps),
               "total_at_risk": round(sum(r["at_risk"] for r in reps), 2),
               "total_chargebacks": round(sum(r["chargeback_deducted"] for r in reps), 2),
               "total_ops_chargebacks": round(sum(r["ops_chargeback_deduction"] for r in reps), 2),
               "total_money_on_table": round(sum(r["money_on_table"] for r in reps), 2),
               "below_tier": sum(1 for r in reps if r["tier"] < 1.0)}
    return {"period": "2026-09", "reps": reps, "summary": summary}


CP = coach_fold()
CD = dd.coaching_drilldowns(CP)
_by_rep = {r["rep"]: r for r in CP["reps"]}

check("C1 the real evidence predicate was driven and really does tell the two shapes apart — the "
      "plan-mode row whose plan paid and whose subtotal is zero is NOT evidence, and the two "
      "ordinary rows are",
      pse.basis(COMM_ROWS[2]) == pse.BASIS_NOT_RECORDED
      and pse.basis(COMM_ROWS[0]) == pse.BASIS_RECORDED
      and _by_rep["Dana"][pse.BASIS_FIELD] == pse.BASIS_NOT_RECORDED)
check("C2 FIVE panels — four money figures and one count",
      sorted(CD) == sorted(["total_at_risk", "total_chargebacks", "total_ops_chargebacks",
                            "total_money_on_table", "below_tier"]), str(sorted(CD)))
for _k in CD:
    check(f"C3[{_k}] the panel's figure IS the endpoint's own summary entry and its rows add to it",
          CD[_k]["tie_out"]["ties"] is True and agrees(CD[_k], CP["summary"][_k]),
          str(CD[_k]["tie_out"]["words"]))
check("C4 THREE PROVENANCES ON ONE EMPLOYEE ROW (§CORE-17.3n) — the shortfall out of the calculated "
      "pay snapshot, the posted penalty out of the recorded penalty ledger, and the chargeback file "
      "out of NOTHING NAMEABLE, each opening separately",
      [s["table"] for s in CD["total_at_risk"]["sources"]] == [dd.REP_COMMISSIONS]
      and [s["table"] for s in CD["total_ops_chargebacks"]["sources"]] == [dd.OPS_CHARGEBACK]
      and CD["total_chargebacks"]["sources"] == [])
check("C4b DEFECT 4, REPORTED AND COUNTED — every row of the chargeback panel reports an origin "
      "that CANNOT BE TRACED rather than a guessed feed, because no migration in this repository "
      "creates that relation (§CORE-17.3i)",
      CD["total_chargebacks"]["untraceable_origins"] == len(CP["reps"])
      and not re.search(r"\bchargeback_items\b", DECL.split('"""', 2)[-1]))
_dana_risk = [r for r in CD["total_at_risk"]["rows"] if r["label"] == "Dana"]
check("C5 ABSENCE IS NEVER ZERO — the employee whose pay breakdown the run never itemised is shown "
      "WITHOUT an at-risk figure and is not counted, in the evidence module's OWN basis word",
      len(_dana_risk) == 1 and _dana_risk[0]["amount"] is None
      and _dana_risk[0]["in_figure"] is False
      and _dana_risk[0]["reason"] == pse.BASIS_NOT_RECORDED, str(_dana_risk))
check("C5b AND NO FIGURE MOVED — the producer's own at-risk for that row is 0.00, so it contributed "
      "0.00 to the total and contributes 0.00 here",
      cents(_by_rep["Dana"]["at_risk"]) == 0
      and CD["total_at_risk"]["tie_out"]["ties"] is True)
check("C5c and the employee at FULL TIER is a measured zero rather than a blank — a full tier with "
      "nothing at risk is a measurement, and the two cases must not read alike",
      all(r["measured"] and r["amount"] == 0.0
          for r in CD["total_at_risk"]["rows"] if r["label"] == "Ben"))
_dana_tbl = [r for r in CD["total_money_on_table"]["rows"] if r["label"] == "Dana"]
check("C6 THE COUNTED-BUT-NOT-TRUSTED POSTURE, STATED — the money-on-the-table row for that same "
      "employee is counted EXACTLY as the report counted it, with the never-itemised breakdown named "
      "on the row, because the figure is light by an unknown amount rather than wrong by a known one",
      len(_dana_tbl) == 1 and _dana_tbl[0]["in_figure"] is True
      and _dana_tbl[0]["fields"]["pay breakdown"] == "never itemised"
      and "never itemised" in (CD["total_money_on_table"]["note"] or ""))
_ben_cb = [r for r in CD["total_chargebacks"]["rows"] if r["label"] == "Ben"]
check("C7 an employee the chargeback file carries NO LINE for is shown without a figure rather than "
      "as an employee with $0.00 of chargebacks",
      len(_ben_cb) == 1 and _ben_cb[0]["amount"] is None and _ben_cb[0]["in_figure"] is False)
check("C7b while an employee the file DOES carry a line for, all of it non-deducting, is a measured "
      "zero — the file named them, and that is a different fact",
      all(r["measured"] and r["amount"] == 75.0
          for r in CD["total_chargebacks"]["rows"] if r["label"] == "Ana"))
check("C8 THE TIER COUNT IS A COUNT, and the employees at full tier are SHOWN beside it and "
      "deliberately not counted — which people are not in the figure is most of what makes it "
      "readable",
      CD["below_tier"]["figure"]["unit"] == rd.UNIT_COUNT
      and CD["below_tier"]["tie_out"]["counted_rows"] == 2
      and CD["below_tier"]["tie_out"]["shown_not_counted"] == 1)
check("C8b and the verdict is the PRODUCER's, read off the row — this declaration holds no tier "
      "threshold of its own (defect 10)",
      all(r["fields"]["full tier"] == ("no" if _by_rep[r["label"]]["below_tier"] else "yes")
          for r in CD["below_tier"]["rows"]))
check("C9 DEFECT 5, REPORTED — a capped penalty row's own lines can add up to MORE than the "
      "deduction the total is made of, and the panel follows the figure the total is Σ of while "
      "saying so in its note",
      "CAPPED" in (CD["total_ops_chargebacks"]["note"] or "")
      and CD["total_ops_chargebacks"]["tie_out"]["ties"] is True)
ctl("C10 control: a DROPPED employee breaks the tie-out",
    dd.coaching_drilldowns({**CP, "reps": CP["reps"][1:]})["total_money_on_table"]["tie_out"]["ties"]
    is False)
ctl("C10b control: a MOVED cent breaks it",
    dd.coaching_drilldowns(
        {**CP, "summary": {**CP["summary"],
                           "total_at_risk": CP["summary"]["total_at_risk"] + 0.01}}
    )["total_at_risk"]["tie_out"]["ties"] is False)
ctl("C10c control: STRIPPING THE EVIDENCE VERDICT off the rows does not quietly turn the blank into "
    "a measured zero — an unrecognised basis is NOT evidence by that module's allowlist posture, so "
    "the figure stays withheld",
    [r for r in dd.coaching_drilldowns(
        {**CP, "reps": [{k: v for k, v in r.items() if k != pse.BASIS_FIELD}
                        for r in CP["reps"]]})["total_at_risk"]["rows"]
     if r["label"] == "Dana"][0]["amount"] is None)
check("C11 A PERIOD THE COMMISSION RUN PRODUCED NOTHING FOR DRILLS TO NOTHING",
      dd.coaching_drilldowns({"reps": [], "summary": CP["summary"]}) == {}
      and dd.coaching_drilldowns({"reps": CP["reps"], "summary": {}}) == {})
_nocb = dd.coaching_drilldowns({
    "reps": [{**r, "chargeback_count": 0, "chargeback_deducted": 0.0} for r in CP["reps"]],
    "summary": {**CP["summary"], "total_chargebacks": 0.0}})
check("C12 A $0.00 OVER ZERO COUNTED ROWS IS SERVED NO PANEL — a period the chargeback file names "
      "nobody in would tie 0 against 0 (True) and read as a measurement",
      "total_chargebacks" not in _nocb and "total_at_risk" in _nocb, str(sorted(_nocb)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. AUTOPAY OPPORTUNITY — the real fold, and the six tiles it refuses ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
#: Raw POS line rows. The enrolment marker is a zero-dollar line with NO department, which is why the
#: producer has to reduce across every line of a transaction — the trap its own docstring records.
ATU_ROWS = [
    {"trans_id": "T1", "trans_date": "2026-09-02", "store": "S1", "tender_type": "Credit Card",
     "product_desc": "Handset", "ext_price": 299.0, "mdn": "5551110001", "contract_type": "NEW"},
    {"trans_id": "T1", "trans_date": "2026-09-02", "store": "S1", "tender_type": "",
     "product_desc": "Autopay", "ext_price": 0.0, "mdn": "", "contract_type": ""},
    {"trans_id": "T1", "trans_date": "2026-09-02", "store": "S1", "tender_type": "",
     "product_desc": "Boost RTR 25", "ext_price": 25.0, "mdn": "", "contract_type": ""},
    {"trans_id": "T2", "trans_date": "2026-09-03", "store": "S1", "tender_type": "Cash; Credit Card",
     "product_desc": "Handset", "ext_price": 149.0, "mdn": "5551110002", "contract_type": "NEW"},
    {"trans_id": "T2", "trans_date": "2026-09-03", "store": "S1", "tender_type": "",
     "product_desc": "Boost RTR 40", "ext_price": 40.0, "mdn": "", "contract_type": ""},
    {"trans_id": "T3", "trans_date": "2026-09-04", "store": "S2", "tender_type": "Externel Credit Card",
     "product_desc": "Handset", "ext_price": 99.0, "mdn": "5551110003", "contract_type": "NEW"},
    {"trans_id": "T3", "trans_date": "2026-09-04", "store": "S2", "tender_type": "",
     "product_desc": "Boost RTR 30", "ext_price": 30.0, "mdn": "", "contract_type": ""},
    {"trans_id": "T4", "trans_date": "2026-09-05", "store": "S2", "tender_type": "Cash",
     "product_desc": "Handset", "ext_price": 59.0, "mdn": "5551110004", "contract_type": "NEW"},
]
ATU_TXS = atu.fold_transactions(ATU_ROWS)
ATU_SUMMARY = atu.summarize(ATU_TXS, 9.0, 5.0, 8.5, 0.0)
ATU_STORES = atu.by_store(ATU_TXS, 5.0)
for _s in ATU_STORES:
    _s["market"] = "North"
ATU_META = {"primary": lineage.LIVE_SALES_FEED, "primary_rows": 300,
            "other": lineage.MONTHLY_SALES, "other_rows": 12, "other_only_rows": 3}
AP = {"org_id": "org", "period": "2026-09", "summary": ATU_SUMMARY, "stores": ATU_STORES,
      "transactions": len(ATU_TXS), "source_meta": ATU_META, "config": {}, "caveats": []}
AD = dd.atu_drilldowns(AP)

check("D1 the real producer was driven — four transactions, the department-less enrolment marker "
      "seen by reducing across the lines of one, and the mis-spelled card tender still counted",
      len(ATU_TXS) == 4 and sum(1 for t in ATU_TXS if t["atu"]) == 1
      and sum(1 for t in ATU_TXS if t["card"]) == 3, str(ATU_SUMMARY["activations"]))
check("D2 THREE panels — two counts and one money figure, on one store row",
      sorted(AD) == sorted(["card_activations", "card_on_atu", "recharge_open"]), str(sorted(AD)))
for _k in AD:
    check(f"D3[{_k}] the panel's figure IS the producer's own summary entry and its rows add to it",
          AD[_k]["tie_out"]["ties"] is True, str(AD[_k]["tie_out"]["words"]))
check("D3b and each agrees with the figure the page prints, through the shared gate",
      agrees(AD["card_activations"], ATU_SUMMARY["activations"]["card"])
      and agrees(AD["card_on_atu"], ATU_SUMMARY["activations"]["card_on_atu"])
      and agrees(AD["recharge_open"], ATU_SUMMARY["recharge"]["card_open"]))
check("D4 TWO UNITS OVER ONE PROVENANCE (§CORE-17.3l) — the counts and the money come out of the "
      "same transaction-grain read, and the SECOND relation is named because it contributed",
      AD["card_activations"]["figure"]["unit"] == rd.UNIT_COUNT
      and AD["recharge_open"]["figure"]["unit"] == rd.UNIT_MONEY
      and [s["table"] for s in AD["recharge_open"]["sources"]]
      == sorted([lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES]))
check("D5 THE CUSTOMER-BASIS FIGURES GET NO PANEL (defect 6) — they are per mobile number and the "
      "per-store table carries none, so they have no row list on this payload at all",
      not any(k in AD for k in ("card", "card_open", "customers"))
      and ATU_SUMMARY["customers"]["card"] > 0)
check("D6 NEITHER COMMISSION-CARRY FIGURE AND NO CUSTOMER SAVING GETS A PANEL — each is this fold "
      "multiplied by a rate somebody typed into the assumptions, and one of them has no rows at all "
      "because its base is typed in by hand, which the producer says itself",
      not any("carry" in k or "saving" in k for k in AD)
      and ATU_SUMMARY["totals_measurable"]["total"] is False)
#: The producer's own names for the five figures §D6 refuses. Spelling any of them in a declaration
#: would be the start of a panel for one, and the first of them carries a CARRIER NAME (RULE TWO).
ATU_REFUSED_KEYS = ("boost_carry_monthly", "total_carry_monthly", "carry_monthly", "carry_annual",
                    "customer_savings_monthly", "carry_forgone")
check("D6b and the declaration spells NONE of the producer's five names for those figures, so no "
      "carrier name reaches its code either (RULE TWO)",
      not any(k in DECL_CODE for k in ATU_REFUSED_KEYS)
      and all(k in ATU_SRC for k in ATU_REFUSED_KEYS[:5]),
      str([k for k in ATU_REFUSED_KEYS if k in DECL_CODE]))
check("D7 EVERY ATTACH PERCENTAGE IS REFUSED (§CORE-17.3h) and the two counts each divides are the "
      "ones served",
      not any("attach" in k or "pct" in k for k in AD)
      and "card_activations" in AD and "card_on_atu" in AD)
ctl("D8 control: a DROPPED store breaks the tie-out",
    dd.atu_drilldowns({**AP, "stores": ATU_STORES[1:]})["card_activations"]["tie_out"]["ties"]
    is False)
ctl("D8b control: a MOVED cent on the money figure breaks it",
    dd.atu_drilldowns({**AP, "summary": {**ATU_SUMMARY,
                                         "recharge": {**ATU_SUMMARY["recharge"],
                                                      "card_open": ATU_SUMMARY["recharge"]["card_open"] + 0.01}}}
                      )["recharge_open"]["tie_out"]["ties"] is False)
check("D9 A SELECTION WITH NO STORE ROWS DRILLS TO NOTHING",
      dd.atu_drilldowns({"stores": [], "summary": ATU_SUMMARY}) == {}
      and dd.atu_drilldowns({}) == {})
_noopen = dd.atu_drilldowns({
    **AP, "stores": [{**s, "rtr_open": 0.0} for s in ATU_STORES],
    "summary": {**ATU_SUMMARY, "recharge": {**ATU_SUMMARY["recharge"], "card_open": 0.0}}})
check("D10 a $0.00 of unconverted recharge over rows that all carry one is still a MEASUREMENT and "
      "keeps its panel — the refusal is about rows nobody measured, not about a figure that happens "
      "to be zero",
      "recharge_open" in _noopen and _noopen["recharge_open"]["tie_out"]["ties"] is True)
check("D11 DEFECT 7 — the endpoint now KEEPS the provenance its own sales read returns instead of "
      "binding it to a name nobody reads",
      "rows, sales_meta = _sales_rows_union_txn(" in ROUTER
      and '"source_meta": sales_meta' in ROUTER)
check("D11b and with that provenance discarded the panels report an UNTRACEABLE origin rather than "
      "guessing a feed — which is what the defect cost before it was carried through",
      dd.atu_drilldowns({**AP, "source_meta": None})["card_activations"]["untraceable_origins"]
      == len(ATU_STORES))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. FAILING KPIs — the real classifier, two row shapes, two relations ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
KPI_DEFS = kpif.resolve_defs(None)
KPI_TARGETS = {k: float(d) for (k, _l, _c, d) in KPI_DEFS if d}
DLAR_ROWS = [
    # failing ATU, meeting protect, and the carrier published NOTHING for BYOD.
    {"store_code": "S1", "location": "Store One", "address": "1 Main", "atu": 40, "protect_pct": 90,
     "family_plan_pct": 50, "tmr3": 80, "aal_conversion": 9},
    # a door passing everything it was measured on.
    {"store_code": "S2", "location": "Store Two", "address": "2 Main", "atu": 70, "protect_pct": 95,
     "byod_pct": 40, "family_plan_pct": 50, "tmr3": 80, "aal_conversion": 9},
    # a door the carrier published NOTHING at all for — not a door that is passing.
    {"store_code": "S3", "location": "Store Three", "address": "3 Main"},
]
KPI_COMMS = [
    {"storeops_name": "Ana", "epay_salesperson": "ANA", "store": "S1", "tier": 0.6, "kpis_met": 1,
     "total_kpis": 2, "kpi_values": {"atu": 30, "protect": 95}},
    {"storeops_name": "Ben", "epay_salesperson": "BEN", "store": "S2", "tier": 1.0, "kpis_met": 2,
     "total_kpis": 2, "kpi_values": {"atu": 75, "protect": 95}},
]
KPI_STORES = kpif.store_rows(DLAR_ROWS, KPI_DEFS, KPI_TARGETS)
KPI_REPS = kpif.rep_rows(KPI_COMMS, KPI_DEFS, KPI_TARGETS)
KPI_SUMMARY = kpif.summarize(KPI_STORES, KPI_REPS)
KP = {"period": "2026-09", "targets": KPI_TARGETS, "summary": KPI_SUMMARY,
      "stores": KPI_STORES, "reps": KPI_REPS, "defs": [], "note": None}
KD = dd.kpi_failing_drilldowns(KP)

check("E1 the real classifier was driven, and a metric with NO recorded value came back `no_data` "
      "rather than a failure — the report must never accuse a door off a blank cell",
      any(d["kpi"] == "byod" for d in KPI_STORES[0]["no_data"])
      and KPI_STORES[0]["failing_count"] >= 1
      and KPI_SUMMARY["stores_failing"] == 1, str(KPI_SUMMARY))
check("E2 the two row counts and one slice per failing metric per grain",
      "stores_failing" in KD and "reps_failing" in KD
      and dd.metric_key("atu", kpif.GRAIN_STORE) in KD
      and dd.metric_key("atu", kpif.GRAIN_REP) in KD, str(sorted(KD)))
for _k in KD:
    check(f"E3[{_k}] the panel's figure IS the producer's own and its rows add up to it",
          KD[_k]["tie_out"]["ties"] is True, str(KD[_k]["tie_out"]["words"]))
check("E3b and each agrees with the count the page prints, through the shared gate",
      agrees(KD["stores_failing"], KPI_SUMMARY["stores_failing"])
      and agrees(KD["reps_failing"], KPI_SUMMARY["reps_failing"])
      and agrees(KD[dd.metric_key("atu", kpif.GRAIN_STORE)],
                 [m for m in KPI_SUMMARY["by_metric"] if m["kpi"] == "atu"][0]["stores_failing"]))
check("E4 TWO GRAINS, ONE UNIT, NEVER POOLED (§CORE-17.3n) — a door's actual comes out of the "
      "carrier's own per-door report and an employee's out of what the last calculation wrote down",
      [s["table"] for s in KD["stores_failing"]["sources"]] == [lineage.DLAR_STORE]
      and [s["table"] for s in KD["reps_failing"]["sources"]] == [dd.REP_COMMISSIONS]
      and KD["stores_failing"]["subject_kind"] == kpif.GRAIN_STORE
      and KD["reps_failing"]["subject_kind"] == kpif.GRAIN_REP)
_s3 = [r for r in KD["stores_failing"]["rows"] if r["label"] == "Store Three"]
check("E5 THREE STATES, TOLD APART — the door the carrier published nothing for is shown WITHOUT a "
      "figure with the one vocabulary's reason, the door that PASSED is a measured zero marked not "
      "counted, and the failing door is a counted 1",
      len(_s3) == 1 and _s3[0]["amount"] is None
      and _s3[0]["reason"] == dd.REASON_KPI_NOT_REPORTED
      and all(r["amount"] == 0.0 and r["measured"] and not r["in_figure"]
              for r in KD["stores_failing"]["rows"] if r["label"] == "Store Two")
      and all(r["amount"] == 1.0 and r["in_figure"]
              for r in KD["stores_failing"]["rows"] if r["label"] == "Store One"))
_byod_key = dd.metric_key("byod", kpif.GRAIN_STORE)
check("E5b and on a PER-METRIC panel the same three states hold for that one metric — the door whose "
      "BYOD the carrier never published is a blank there, not a pass",
      _byod_key not in KD
      or [r for r in KD[_byod_key]["rows"] if r["label"] == "Store One"][0]["amount"] is None)
check("E6 THE MIXED-GRAIN FIGURE GETS NO PANEL (defect 8) — `failing_cells` adds store failing "
      "counts to employee failing counts, so there is no one row list it was folded from, and both "
      "halves of it are the two panels above",
      "failing_cells" not in KD and KPI_SUMMARY["failing_cells"] > 0)
check("E6b nor do the two ROW-COUNT denominators, which the panels already state in their headers",
      "stores_total" not in KD and "reps_total" not in KD)
#: The ONE failing door in this fixture, dropped. The figure still says 1 and NO surviving row is
#: counted — which is a DISAGREEMENT, not a trivial tie, so the panel is served and says so. §CORE-
#: 17.3o's refusal is about a ZERO over no counted rows (E8b); refusing this one too would hide the
#: very mismatch the tie-out exists to publish.
_dropped = dd.kpi_failing_drilldowns({**KP, "stores": KPI_STORES[1:]})
ctl("E7 control: a DROPPED failing door breaks the tie-out, and the panel is STILL SERVED so the "
    "reader is told the figure does not match its own rows rather than shown nothing",
    "stores_failing" in _dropped
    and _dropped["stores_failing"]["tie_out"]["ties"] is not True)
ctl("E7b control: a MIS-SCORED store breaks it — a door whose failing list is emptied stops being "
    "counted and the figure no longer matches",
    dd.kpi_failing_drilldowns(
        {**KP, "stores": [dict(s, failing=[], failing_count=0) for s in KPI_STORES]}
    ).get("stores_failing", {"tie_out": {"ties": None}})["tie_out"]["ties"] is not True)
check("E8 A REPORT WITH NO ROWS AT ALL DRILLS TO NOTHING",
      dd.kpi_failing_drilldowns({"stores": [], "reps": [], "summary": KPI_SUMMARY}) == {}
      and dd.kpi_failing_drilldowns({"stores": KPI_STORES, "reps": [], "summary": {}}) == {})
_nofail = dd.kpi_failing_drilldowns({
    **KP, "stores": [dict(s, failing=[], failing_count=0) for s in KPI_STORES],
    "summary": {**KPI_SUMMARY, "stores_failing": 0}})
check("E8b and a count of ZERO over rows none of which the figure counts is served NO PANEL — a 0 "
      "that ties against nothing would read as a measurement",
      "stores_failing" not in _nofail, str(sorted(_nofail)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. §CORE-17.3o — a panel explains the FIGURE, not the table. All four, both ways ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
for _name, _src in PAGES.items():
    check(f"F0[{_name}] the page was read", bool(_src), _name)
#: THE CALL, NEVER THE IDENTIFIER. `"narrowPanel" in src` is satisfied by the IMPORT ALONE —
#: measured here on 2026-10-26 by renaming the call site on a narrowed page and leaving its import,
#: which this lock passed in its first draft. That is the same hole batch 19 found in the programme's
#: `"DrillDownPanel" in src` checks, in the command you reach for to check the other half of it, so
#: every scan in this section matches a CALL (`narrowPanel(`) or an ELEMENT (`<DrillDownPanel`).
_NARROW_CALL = "narrowPanel("


def _calls_narrow(name):
    return _NARROW_CALL in strip_comments(PAGES[name])


check("F1 THE TWO BROWSER-FOLDED PAGES ARE NARROWED — each CALLS `narrowPanel(` in its CODE, "
      "matched on the call and never on the bare identifier, which an import satisfies on a page "
      "whose narrowing was renamed away, so its tiles and its panels are one fold over the "
      "selection on screen",
      all(_calls_narrow(n) for n in NARROWED_PAGES),
      str([n for n in NARROWED_PAGES if not _calls_narrow(n)]))
check("F1b AND THE TWO SERVER-FOLDED PAGES ARE NOT — no `narrowPanel` at all in the CODE of either, "
      "because narrowing a panel there would make it explain a figure the page is not showing. The "
      "rule is written down in each page's own HEADER, where somebody would break it, which is "
      "exactly why this scan strips comments first",
      all("narrowPanel" not in strip_comments(PAGES[n]) for n in SERVER_FOLDED_PAGES)
      and all("CORE-17.3t" in PAGES[n] for n in PAGES),
      str([n for n in SERVER_FOLDED_PAGES if "narrowPanel" in strip_comments(PAGES[n])]))
ctl("F1c control: the comment-stripping scan is NOT blind — it finds the real CALL in code",
    _NARROW_CALL in strip_comments("const x = narrowPanel(p, k)\n"))
ctl("F1d control: and it really does strip a comment that mentions it",
    _NARROW_CALL not in strip_comments("// never call narrowPanel(p) here\nconst x = 1\n"))
ctl("F1e control: AND THE CALL SCAN IS STRICTER THAN THE IDENTIFIER SCAN — an import-only page has "
    "the identifier and NOT the call, which is the hole this lock shipped with for one draft",
    "narrowPanel" in strip_comments("import { narrowPanel } from '@/lib/drill-narrow'\n")
    and _NARROW_CALL not in strip_comments("import { narrowPanel } from '@/lib/drill-narrow'\n"))
check("F2 THE TWO NARROWED PAGES REALLY DO FOLD THEIR OWN TILES — each contains a `reduce(` in its "
      "code, which is the fold the panels have to follow",
      all("reduce(" in strip_comments(PAGES[n]) for n in NARROWED_PAGES),
      str([n for n in NARROWED_PAGES if "reduce(" not in strip_comments(PAGES[n])]))
check("F2b AND NEITHER SERVER-FOLDED PAGE FOLDS A TILE IN THE BROWSER — no `reduce(` at all in "
      "either, so there is no client fold for a panel to have to follow",
      all("reduce(" not in strip_comments(PAGES[n]) for n in SERVER_FOLDED_PAGES),
      str([n for n in SERVER_FOLDED_PAGES if "reduce(" in strip_comments(PAGES[n])]))
ctl("F2c control: the `reduce(` scan is NOT vacuous — it finds one where there is one",
    "reduce(" in strip_comments("const t = rows.reduce((a, r) => a + r.x, 0)\n"))
check("F3 each narrowed page reads its tile OFF its narrowed panel through the SHARED gate, rather "
      "than keeping two numbers and hoping",
      all("agreesToTheCent" in strip_comments(PAGES[n])
          and "figure?.amount" in strip_comments(PAGES[n]) for n in NARROWED_PAGES))
check("F4 and the two server-folded pages say in their own text WHY they are not narrowed — their "
      "filters are query parameters, which this asserts by finding the parameter being sent",
      "qs.append('stores'" in PAGES["productivity"]
      and "new URLSearchParams({ period, start, end, stores" in PAGES["atu-opportunity"])
#: THE ELEMENT, NOT THE IDENTIFIER. `"DrillDownPanel" in src` is satisfied by the IMPORT ALONE, which
#: batch 19 measured by deleting a render and watching every scan in this programme stay green. Both
#: this file and the shared gate match the JSX ELEMENT now.
_RENDERS = {n: bool(re.search(r"<DrillDownPanel[\s/>]", strip_comments(s)))
            for n, s in PAGES.items()}
check("F5 all four pages RENDER the shared panel — matched on the JSX element `<DrillDownPanel`, "
      "never on the bare identifier, which an unused import satisfies on a page whose render was "
      "deleted — and hand-roll none of their own",
      all(_RENDERS.values())
      and not any(("rows_total_count" in strip_comments(s)) or ("tie_out" in strip_comments(s))
                  for s in PAGES.values()),
      str([n for n, ok in _RENDERS.items() if not ok]))
ctl("F5b control: the element scan is NOT vacuous, and it really is stricter than the identifier "
    "scan — an import-only page has the identifier and NOT the element",
    re.search(r"<DrillDownPanel[\s/>]", "  <DrillDownPanel panel={p} />\n")
    and not re.search(r"<DrillDownPanel[\s/>]",
                      "import DrillDownPanel from '@/components/DrillDownPanel'\n"))
check("F6 and each reads what the SERVER served, through the shared offer gate, rather than deriving "
      "a drill of its own",
      all("drilldown" in s and "useDrillTile" in s for s in PAGES.values()),
      str([n for n, s in PAGES.items() if "useDrillTile" not in s]))
check("F7 no page spells a relation name of its own: a feed's plain name has one home and it is not "
      "a page",
      not any(re.search(r"'(raw_[a-z_]+|rep_commissions|timelog|ops_chargeback|daily_sales_feed)'",
                        strip_comments(s)) for s in PAGES.values()),
      str([n for n, s in PAGES.items()
           if re.search(r"'raw_[a-z_]+'", strip_comments(s))]))
check("F8 THE NARROWER IS THE IDENTITY WHEN NOTHING IS DROPPED, which is what makes a narrowed tile "
      "trustworthy at all — the one home's own rule, asserted here because these two pages rely on "
      "it, and a capped panel is REFUSED rather than narrowed to a smaller number",
      "if (rows.length === panel.rows.length)" in read("frontend/src/lib/drill-narrow.ts")
      and "if (panel.capped) return null" in read("frontend/src/lib/drill-narrow.ts"))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. NO FIGURE MOVED — the declaration is read-only over the producer's own payload ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
for _label, _payload, _fn in (("productivity", PP, dd.productivity_drilldowns),
                              ("coaching", CP, dd.coaching_drilldowns),
                              ("atu-opportunity", AP, dd.atu_drilldowns),
                              ("kpi-failing", KP, dd.kpi_failing_drilldowns)):
    _before = copy.deepcopy(_payload)
    _fn(_payload)
    check(f"G1[{_label}] the payload is BYTE-IDENTICAL after the declaration has run over it — "
          f"deep-compared before and after, so no producer's arithmetic was moved",
          _before == _payload)
ctl("G1b control: the deep compare is NOT vacuous — it notices a single moved cent",
    copy.deepcopy(CP) != {**CP, "summary": {**CP["summary"],
                                            "total_at_risk": CP["summary"]["total_at_risk"] + 0.01}})
check("G1c and the productivity declaration builds NEW rows rather than stamping the store code "
      "onto the producer's own, which is what makes §G1 above a real check",
      all("store_code" not in r for s in PP["stores"] for r in s["reps"]))
check("G2 the declaration's entry points take a PAYLOAD and nothing else — no client, no query, no "
      "period — so a panel is folded over rows the caller is already being served and a drill-down "
      "cannot become a disclosure",
      all(re.search(r"def %s\(payload, \*, cap=None\)" % fn, DECL)
          for fn in ("productivity_drilldowns", "coaching_drilldowns", "atu_drilldowns",
                     "kpi_failing_drilldowns")))
check("G3 and every endpoint attaches the key LAST, after everything it filters — the failing-KPI "
      "report after its `in_keyset` scope filter and the coaching card after its self-scope "
      "narrowing",
      ROUTER.index("comms = [c for c in comms if in_keyset(ks, c.get('store'))]")
      < ROUTER.index('out["drilldown"] = _pcd.kpi_failing_drilldowns(out)')
      and ROUTER.index("reps = _pa.mine_only(reps, rep_keys")
      < ROUTER.index('out["drilldown"] = _pcd.coaching_drilldowns(out)')
      and 'out["drilldown"] = _pcd.productivity_drilldowns(out)' in ROUTER
      and 'out["drilldown"] = _pcd.atu_drilldowns(out)' in ROUTER)
check("G4 BUILD LOCK — no endpoint builds a panel of its own: there is no second shape for the same "
      "question",
      "rd.panel(" not in ROUTER and "report_drilldown.panel(" not in ROUTER)
_ALL = dict(PD, **CD)
_ALL.update(AD)
_ALL.update(KD)
check("G5 THE ROW CAP IS THE ONE HOME'S, and the true count is always served",
      all(p["rows_total_count"] >= p["rows_shown"] and p["capped"] is False
          for p in _ALL.values()))
_wide = {**CP, "reps": [dict(CP["reps"][0], rep="R%d" % i) for i in range(rd.ROW_CAP + 25)]}
_wide["summary"] = {**CP["summary"],
                    "total_money_on_table": round(CP["reps"][0]["money_on_table"]
                                                  * (rd.ROW_CAP + 25), 2)}
_wp = dd.coaching_drilldowns(_wide)["total_money_on_table"]
check("G5b and a REAL cap behaves — the panel serves ROW_CAP rows, states the TRUE count, and its "
      "tie-out is over ALL of them rather than over the slice anybody can see",
      _wp["capped"] is True and _wp["rows_shown"] == rd.ROW_CAP
      and _wp["rows_total_count"] == rd.ROW_CAP + 25
      and _wp["tie_out"]["counted_rows"] == rd.ROW_CAP + 25
      and _wp["tie_out"]["ties"] is True)
check("G6 every panel carries its SCHEMA, so a stale renderer fails loudly rather than drawing half "
      "a panel",
      all(p["schema"] == rd.SCHEMA for p in _ALL.values()))
check("G7 and every panel's figure carries a unit from the ONE vocabulary",
      all(p["figure"]["unit"] in rd.UNITS for p in _ALL.values()))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== H. THE GRAIN COUNT, MEASURED OFF THE PANELS AS A SET ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A grain fixes the ROW, the UNIT and the PROVENANCE (§CORE-17.3l / §CORE-17.3n). The row shape is
# named per report because the payload cannot say it; the unit and the provenance are READ OFF THE
# PANEL, so the number below is a measurement and not a claim.
ROW_SHAPE = {}
for _k in PD:
    ROW_SHAPE[("productivity", _k)] = "employee_at_a_store"
for _k in CD:
    ROW_SHAPE[("coaching", _k)] = "employee_pay_row"
for _k in AD:
    ROW_SHAPE[("atu-opportunity", _k)] = "store_row"
for _k in KD:
    ROW_SHAPE[("kpi-failing", _k)] = ("store_row_kpi" if _k.endswith(kpif.GRAIN_STORE)
                                      or _k == "stores_failing" else "employee_pay_row")


#: How this file writes "this panel's rows report an origin that cannot be traced" when it collects
#: provenances. Not a relation name: the whole point is that there is none (§CORE-17.3i).
UNTRACEABLE = "(nothing nameable)"


def _provenance(panel):
    """The set of relations this panel's rows were actually read out of, with an untraceable origin
    named as such — read off the panel, never declared."""
    names = {s["table"] for s in panel.get("sources") or ()}
    if panel.get("untraceable_origins"):
        names.add(UNTRACEABLE)
    return frozenset(names)


MEASURED = set()
for (report, key), shape in ROW_SHAPE.items():
    panel = {"productivity": PD, "coaching": CD, "atu-opportunity": AD,
             "kpi-failing": KD}[report][key]
    MEASURED.add((shape, panel["figure"]["unit"], _provenance(panel)))
print(f"       MEASURED {len(MEASURED)} distinct grains over {len(ROW_SHAPE)} panels in "
      f"{len({s for s in ROW_SHAPE.values()})} row shapes")
for _g in sorted(MEASURED, key=lambda g: (g[0], g[1], sorted(g[2]))):
    print(f"         · {_g[0]} / {_g[1]} / {sorted(_g[2])}")
#: THE GRAINS THIS FAMILY HAS, DECLARED AS A SET AND COMPARED FOR EQUALITY — batch 19's §H shape.
#: A BOUND ON THE SIZE IS NOT AN ASSERTION ABOUT THE SET: `len(MEASURED) >= 12` stays green while a
#: grain silently vanishes and another takes its place, which is the "guard that goes blind" shape
#: `harness_subject_set_lock.py` §B1 exists to refuse — and it caught this file's first draft doing
#: exactly that, one line under a docstring promising a SET. The relations are the registry's and the
#: units are the one home's, so this declaration spells no table name of its own either.
_SALES_UNION = frozenset({lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES})
_EXPECTED = {
    # PRODUCTIVITY — one employee under one store, three units, and the hours in two provenances
    # because an employee nobody clocked names nothing at all.
    ("employee_at_a_store", rd.UNIT_COUNT, _SALES_UNION),
    ("employee_at_a_store", rd.UNIT_MONEY, _SALES_UNION),
    ("employee_at_a_store", rd.UNIT_HOURS, frozenset({dd.TIME_CLOCK})),
    ("employee_at_a_store", rd.UNIT_HOURS, frozenset({dd.TIME_CLOCK, UNTRACEABLE})),
    # COACHING — one employee pay row, ONE unit, and provenance alone separating four figures.
    ("employee_pay_row", rd.UNIT_MONEY, frozenset({dd.REP_COMMISSIONS})),
    ("employee_pay_row", rd.UNIT_MONEY, frozenset({UNTRACEABLE})),
    ("employee_pay_row", rd.UNIT_MONEY, frozenset({dd.OPS_CHARGEBACK, UNTRACEABLE})),
    ("employee_pay_row", rd.UNIT_MONEY, frozenset({dd.REP_COMMISSIONS, dd.OPS_CHARGEBACK,
                                                   UNTRACEABLE})),
    # The COUNT over that same row — shared outright by the below-tier and failing-employee figures.
    ("employee_pay_row", rd.UNIT_COUNT, frozenset({dd.REP_COMMISSIONS})),
    # AUTOPAY — one store row out of the transaction-grain read, two units.
    ("store_row", rd.UNIT_COUNT, _SALES_UNION),
    ("store_row", rd.UNIT_MONEY, _SALES_UNION),
    # FAILING KPIs at the DOOR grain — the carrier's own per-door report, never pooled with the rep.
    ("store_row_kpi", rd.UNIT_COUNT, frozenset({dd.DLAR_STORE})),
}
_extra, _missing = sorted(MEASURED - _EXPECTED), sorted(_EXPECTED - MEASURED)
check("H1 THE GRAIN COUNT IS MEASURED AND THE SET IS COMPARED FOR EQUALITY — every panel's unit and "
      "provenance are read off the panel, collected as a SET so two panels that really are one "
      "grain collapse, and checked against the declared twelve. A bound on the SIZE would stay "
      "green while one grain vanished and another took its place, so a lost grain NAMES ITSELF here",
      MEASURED == _EXPECTED and len(MEASURED) == 12 and len(MEASURED) < len(ROW_SHAPE),
      "extra=%s missing=%s" % (_extra, _missing))
ctl("H1a control: the equality is not vacuous — dropping one declared grain is reported BY NAME, "
    "and the comparison really is a set difference rather than a count",
    (MEASURED - (_EXPECTED - {("store_row_kpi", rd.UNIT_COUNT, frozenset({dd.DLAR_STORE}))}))
    == {("store_row_kpi", rd.UNIT_COUNT, frozenset({dd.DLAR_STORE}))})
check("H1b TWO REPORTS SHARE ONE GRAIN OUTRIGHT, and that is the point rather than a defect — a "
      "failing-employee count and a below-tier count are both a COUNT over an employee pay row out "
      "of the same calculated snapshot, so the set holds one triple for the two",
      (ROW_SHAPE[("coaching", "below_tier")], CD["below_tier"]["figure"]["unit"],
       _provenance(CD["below_tier"]))
      == (ROW_SHAPE[("kpi-failing", "reps_failing")], KD["reps_failing"]["figure"]["unit"],
          _provenance(KD["reps_failing"])))
check("H1c THREE UNITS ON ONE EMPLOYEE-AT-A-STORE ROW is a real triple split and not an artefact of "
      "the fixture — the two sales figures share a provenance and differ only in UNIT, and the hours "
      "figure differs in BOTH",
      len({(PD[k]["figure"]["unit"], _provenance(PD[k]))
           for k in ("boxes", "acc_sales", "hours")}) == 3
      and _provenance(PD["boxes"]) == _provenance(PD["acc_sales"])
      and _provenance(PD["hours"]) != _provenance(PD["boxes"]))
check("H1d AND THREE PROVENANCES ON ONE EMPLOYEE PAY ROW, in ONE unit — the calculated snapshot, "
      "the penalty ledger, and nothing nameable at all. Provenance ALONE separates them "
      "(§CORE-17.3n)",
      len({_provenance(CD[k]) for k in ("total_at_risk", "total_ops_chargebacks",
                                        "total_chargebacks", "total_money_on_table")}) == 4
      and all(CD[k]["figure"]["unit"] == rd.UNIT_MONEY
              for k in ("total_at_risk", "total_ops_chargebacks", "total_chargebacks",
                        "total_money_on_table")))
ctl("H1e control: the provenance reader is NOT blind to an untraceable origin — the chargeback "
    "panel's provenance is the untraceable one and nothing else",
    _provenance(CD["total_chargebacks"]) == frozenset({UNTRACEABLE}))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== I. THE RATCHET — shrink-only, and mutation-tested in all three directions ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
GATE = read("harness_drilldown_coverage.py")
LEDGER_TXT = read("harness_drilldown_pending.txt")
MY_ROUTES = ("/commcalc/productivity", "/commcalc/coaching", "/commcalc/atu-opportunity",
             "/commcalc/kpi-failing")


def _pin(src):
    """THE PINNED CEILING, READ OUT OF THE GATE. Returns None when it cannot be read, so this lock
    FAILS CLOSED rather than passing on an unreadable pin."""
    m = re.search(r"(?m)^OWES_MAX\s*=\s*(\d+)\s*$", src or "")
    return int(m.group(1)) if m else None


def _owes(src):
    out = []
    for ln in (src or "").splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        parts = ln.split("\t")
        if len(parts) > 1 and parts[1].strip() == "owes":
            out.append(parts[0].strip())
    return out


def _state(src, route):
    for ln in (src or "").splitlines():
        parts = ln.split("\t")
        if parts and parts[0].strip() == route:
            return (parts[1].strip() if len(parts) > 1 else ""), \
                   (parts[2].strip() if len(parts) > 2 else "")
    return None, None


_p, _o = _pin(GATE), len(_owes(LEDGER_TXT))
check("I1 THE PIN AND THE LEDGER AGREE, AND BOTH ARE AT OR BELOW THIS BATCH'S NUMBER. The assertion "
      "is deliberately `<=` on the VALUE and `==` between the two: equality on the value has broken "
      "three times, because a later batch lowering the pin further is CORRECT and must stay green",
      _p is not None and _p <= 33 and _o <= 33 and _o == _p, str((_p, _o)))
ctl("I1b control: a HIGHER pin than this batch left is RED — the ratchet really is shrink-only",
    not (_pin("OWES_MAX = 37\n") is not None and _pin("OWES_MAX = 37\n") <= 33))
ctl("I1c control: a LATER, LOWER pin is GREEN — a future batch serving more routes must not have to "
    "come back and edit this file",
    (_pin("OWES_MAX = 26\n") or 99) <= 33)
ctl("I1d control: an UNREADABLE pin FAILS CLOSED rather than passing",
    _pin("OWES_MAX = nope\n") is None and _pin("") is None and _pin(None) is None)
check("I2 exactly the FOUR routes this batch serves flipped from `owes` to `shared`, and the state "
      "is true of the code — each page really renders the shared panel",
      all(_state(LEDGER_TXT, r)[0] == "shared" for r in MY_ROUTES),
      str({r: _state(LEDGER_TXT, r)[0] for r in MY_ROUTES}))
check("I3 and none of the four is still counted as owing one",
      not (set(MY_ROUTES) & set(_owes(LEDGER_TXT))))
check("I4 EVERY ONE OF THE FOUR NOTES CARRIES A `REFUSES TO CLAIM` CLAUSE — a drill-down that does "
      "not say what it declined to explain is an excuse nobody wrote down",
      all("REFUSES TO CLAIM" in (_state(LEDGER_TXT, r)[1] or "") for r in MY_ROUTES),
      str([r for r in MY_ROUTES if "REFUSES TO CLAIM" not in (_state(LEDGER_TXT, r)[1] or "")]))
check("I5 and each note names THIS batch's index section and the declaration that serves it, so a "
      "reader of the ledger can reach the reasoning",
      all("CORE-17.3t" in (_state(LEDGER_TXT, r)[1] or "")
          and "performance_coaching_drilldown" in (_state(LEDGER_TXT, r)[1] or "")
          for r in MY_ROUTES))
ctl("I4b control: the clause scan is NOT vacuous — a note without the clause is caught",
    "REFUSES TO CLAIM" not in "a note that declines to say what it declined")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== J. REGISTRATION — the index, the graph, the labels and the guard job ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MG = read("backend/app/modules/core/module_graph.py")
WF = read(".github/workflows/carrier-vocab-guard.yml")
MIG = ""
_migdir = os.path.join(ROOT, "database", "migrations")
if os.path.isdir(_migdir):
    for _f in sorted(os.listdir(_migdir)):
        if _f.endswith(".sql"):
            MIG += open(os.path.join(_migdir, _f), encoding="utf-8", errors="replace").read()

DRILL_TABLES = sorted({t for t in (
    dd.TIME_CLOCK, dd.OPS_CHARGEBACK, dd.DLAR_STORE, dd.REP_COMMISSIONS,
    lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES) if t})
_no_mig = [t for t in DRILL_TABLES if not re.search(r"create table[^;]*\b%s\b" % re.escape(t),
                                                    MIG, re.I | re.S)]
check("J1 every table a panel in this batch can name is CREATED BY A MIGRATION — a provenance claim "
      "about a relation that does not exist is worse than none",
      bool(MIG) and _no_mig == [], str(_no_mig))
_no_label = [t for t in DRILL_TABLES if not re.search(r"(?m)^\s+%s:" % re.escape(t), LABELS)]
check("J2 BUILD LOCK — and every one has a `sourceLabels.ts` entry, so the operator reads a feed's "
      "plain name and never an identifier (§19.38)",
      _no_label == [], str(_no_label))
check("J2b AND THE ONE RELATION THIS FAMILY CANNOT NAME IS GENUINELY UN-NAMEABLE — no migration "
      "here creates the employee chargeback file, which is why those rows report an untraceable "
      "origin instead of a feed nobody could label (defect 4)",
      not re.search(r"create table[^;]*\bchargeback_items\b", MIG, re.I | re.S)
      and not re.search(r"(?m)^\s+chargeback_items:", LABELS))
check("J3 the declaration is a registered CALLER of the one drill-down home, by hand",
      "'app/modules/commcalc/performance_coaching_drilldown.py': ('report_drilldown',)" in MG)
check("J4 and of the feed-lineage home, so 'who else answers which table this is' is one call away",
      "'app/modules/commcalc/performance_coaching_drilldown.py': ('_lineage',)" in MG)
check("J4b and of the two OTHER facts it dereferences — the stored-pay-breakdown evidence predicate "
      "and the KPI column/grain map. A hand-written caller edge is only half the registration",
      MG.count("'app/modules/commcalc/performance_coaching_drilldown.py': ('_pse',)") == 1
      and MG.count("'app/modules/commcalc/performance_coaching_drilldown.py': ('_kpif',)") == 1)
check("J5 this batch's harness is registered in the one home's lock list",
      "'harness_batch20_drilldown.py'" in MG)
check("J6 the index carries THIS batch's section, and the next free letter was taken rather than "
      "guessed", "§CORE-17.3t" in IDX and "performance_coaching_drilldown.py" in IDX)
#: THE RUN STEP, NEVER THE MENTION. The harness's own name appears in the job's PATHS FILTER too,
#: so `"harness_batch20_drilldown.py" in WF` stayed green when the run line was pointed at another
#: batch — measured here by doing exactly that. A lock that cannot tell a step that RUNS this file
#: from a filter that merely names it is the same un-wiring hole in a different file.
_RUN_STEP = [ln for ln in WF.splitlines()
             if re.search(r"run:\s*python3\s+harness_batch20_drilldown\.py\b", ln)]
check("J7 the guard job RUNS this harness — matched on the `run:` line itself, never on the file's "
      "name, which the paths filter below also carries — with a paths filter naming every file it "
      "is about",
      len(_RUN_STEP) == 1
      and "backend/app/modules/commcalc/performance_coaching_drilldown.py" in WF
      and "backend/app/modules/commcalc/productivity.py" in WF
      and "backend/app/modules/commcalc/atu_opportunity.py" in WF
      and "backend/app/modules/commcalc/kpi_failing.py" in WF)
_steps = [ln for ln in WF.splitlines() if "batch20" in ln]
check("J8 and its step's `name:` / `run:` scalars carry NO colon-space — a plain scalar that does "
      "makes the WHOLE workflow fail to parse, so every job in it silently never runs",
      bool(_steps) and not any(re.search(r"(?m)^\s*(?:-\s*)?(name|run):\s+[^'\"].*:\s", ln)
                               for ln in _steps), str(_steps))
check("J9 the four pages are registered in the index's endpoint rows with this batch's section",
      IDX.count("DRILL-DOWN §CORE-17.3t") >= 4, str(IDX.count("DRILL-DOWN §CORE-17.3t")))
check("J10 the ratchet gate itself still passes its own `shared` lock for these routes, which is "
      "what makes the ledger flip an assertion rather than a claim",
      "D1 BUILD LOCK" in GATE and "<DrillDownPanel" in GATE)


print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
_mut = len([p for p in PASS if p.startswith("MUT")]) + len([f for f in FAIL if f.startswith("MUT")])
print(f"{_mut} mutations / negative controls")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
