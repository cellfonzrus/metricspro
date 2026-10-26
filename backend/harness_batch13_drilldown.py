"""PROOF — THE FIVE TARGETS REPORTS NAME THE ROWS BEHIND THEIR FIGURES.

DB-free, stdlib only, driving the REAL declaration (`commcalc/targets_drilldown.py`) and the REAL
producers (`commcalc/targets_engine.compute_scope` / `aggregate_stores`,
`commcalc/manager_report_card.build`) through the REAL shared fold
(`core/report_drilldown.tiles` / `tile_panel` / `panel`), over fixtures shaped like what each
endpoint actually hands them. Batch 13 of the §CORE-17 audit: /commcalc/targets,
/commcalc/targets/my, /commcalc/targets/accessories, /commcalc/targets/action-plan and
/commcalc/targets/report-cards.

WHAT THIS BATCH IS ABOUT.

1. THE GRAIN COUNT IS MEASURED, NOT ASSUMED (§CORE-17.3l's rule, applied). SIX grains over five
   routes — and three of them are the SAME store row in the SAME unit, separated only by where the
   number came from: what was sold, what somebody typed into a settings screen, and the gap that
   names both. §A.

2. THE EVIDENCE IS THE DAYS THE FIGURE WAS ACTUALLY SUMMED OVER, which is NOT the calendar: a scope's
   achieved counts days it was not scheduled on, and the calendar holds days it never sold on. The
   rows are the union and each says which side it came from. §B.

3. A TARGET IS A DECLARED NUMBER, NOT A MEASUREMENT, so its origin is the config row it was typed
   into — read PER ROW, because a store with no row of its own seeds its accessory target from the
   store setup instead. And an UNSET target is never a zero target. §D.

4. EVERY ATTAINMENT PERCENTAGE GETS NO PANEL (§CORE-17.3h), and neither does a figure composed from
   other figures, a leaf, or a count whose list is on screen. Not an empty panel: no key. §Y5.

5. ONE FOLD BECAME ONE: the action plan's team total was a `reduce` in TypeScript and is now folded
   by the one home. §F1 pins it against the arithmetic it replaced, with a control that fails.

6. FIVE LIVE DEFECTS, REPORTED NOT HIDDEN — including a crash the area roll-up has today for any
   tenant with a declared category, which this batch fixes at the mechanism and proves moves no
   built-in figure. §Z10–§Z14.

  python3.11 backend/harness_batch13_drilldown.py
"""
import os
import re
import sys
import tokenize
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import data_lineage_registry as lineage              # noqa: E402
from app.modules.commcalc import manager_report_card as mrc                    # noqa: E402
from app.modules.commcalc import targets_drilldown as dd                       # noqa: E402
from app.modules.commcalc import targets_engine as te                          # noqa: E402
from app.modules.core import report_drilldown as rd                            # noqa: E402

P, F = [], []


def check(label, ok, detail=""):
    (P if ok else F).append(label)
    print(("PASS" if ok else "FAIL") + "  " + label
          + (("   [%s]" % str(detail)[:400]) if detail and not ok else ""))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECL = os.path.join(ROOT, "backend", "app", "modules", "commcalc", "targets_drilldown.py")
ENGINE = os.path.join(ROOT, "backend", "app", "modules", "commcalc", "targets_engine.py")
CC_ROUTER = os.path.join(ROOT, "backend", "app", "modules", "commcalc", "router.py")
PAGES = {
    "/commcalc/targets": "frontend/src/app/(platform)/commcalc/targets/page.tsx",
    "/commcalc/targets/my": "frontend/src/app/(platform)/commcalc/targets/my/page.tsx",
    "/commcalc/targets/accessories": "frontend/src/app/(platform)/commcalc/targets/accessories/page.tsx",
    "/commcalc/targets/action-plan": "frontend/src/app/(platform)/commcalc/targets/action-plan/page.tsx",
    "/commcalc/targets/report-cards": "frontend/src/app/(platform)/commcalc/targets/report-cards/page.tsx",
}
AREA_COMPONENT = "frontend/src/app/(platform)/commcalc/targets/my/_components/AreaTargets.tsx"


def _read(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def _code_only(path):
    """The file's CODE, with comments and docstrings dropped — `harness_pl_drill_detail` §G5's
    narrowing, reused rather than re-derived: a build lock about what a module DOES must not fire on
    the prose describing it. A file that will not tokenise comes back WHOLE, so it fails CLOSED."""
    try:
        out = []
        with open(path, "rb") as fh:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type not in (tokenize.STRING, tokenize.COMMENT):
                    out.append(tok.string)
        return " ".join(out)
    except Exception:
        return open(path, encoding="utf-8").read()


def _func_src(path, name):
    import ast
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    lines = src.splitlines(True)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "".join(lines[node.lineno - 1:node.end_lineno])
    return ""


DECL_SRC = open(DECL, encoding="utf-8").read()
DECL_CODE = _code_only(DECL)
# `_code_only` joins TOKENS with spaces, so a call reads `_te . target_axis (` in it. A check
# about which function the declaration CALLS needs the spelling back, with the prose still gone.
DECL_TIGHT = DECL_CODE.replace(" ", "")


def _tie(p):
    return (p or {}).get("tie_out", {}).get("ties")


def _row(p, label):
    for r in (p or {}).get("rows") or []:
        if r.get("label") == label:
            return r
    return {}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FIXTURES — shaped exactly like what each producer hands the declaration.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
TODAY = date(2026, 10, 6)
MONTH_END = date(2026, 10, 31)
#: Three scheduled days; the third sold nothing. A fourth day SOLD with no shift entered at all —
#: which is the case a panel folded over the calendar would have been short of.
HOURS = {date(2026, 10, 1): 8.0, date(2026, 10, 2): 8.0, date(2026, 10, 3): 6.5}
ACTUALS = {
    date(2026, 10, 1): {"prem": 2.0, "byod": 1.0, "upg": 1.0, "acc": 100.0, "setup": 10.0,
                        "box": 3.0, "billpay": 10.0},
    date(2026, 10, 2): {"prem": 1.0, "byod": 0.0, "upg": 0.0, "acc": 50.5, "setup": 0.0,
                        "box": 1.0, "billpay": 4.0},
    date(2026, 10, 5): {"prem": 3.0, "byod": 0.0, "upg": 2.0, "acc": 25.25, "setup": 0.0,
                        "box": 2.0, "billpay": 5.0},
}
MONTHLY = {"activations": 100.0, "upgrades": 20.0, "byod": 10.0, "accessories": 1000.0}
SCOPE = te.compute_scope(MONTHLY, HOURS, ACTUALS, TODAY, round_counts=True, month_end=MONTH_END)
SCOPE.update({"period": "October 2026", "scope": "store", "store_code": "S-1", "rep": None})
SCOPE_P = dd.scope_drilldowns(SCOPE)

#: A DECLARED category (a tenant that sells services rather than activations), so the second
#: provenance family is exercised: its achievement comes out of the store's own daily closing.
CATALOG = [{"key": "dropoffs", "label": "Drop-offs", "value_kind": "count", "source": "count_field"}]
DECL_ACTUALS = te.merge_declared_actuals(ACTUALS, {"2026-10-01": {"dropoffs": 4.0},
                                                   "2026-10-02": {"dropoffs": 2.0}})
DSCOPE = te.compute_scope(dict(MONTHLY, dropoffs=40.0), HOURS, DECL_ACTUALS, TODAY,
                          round_counts=True, month_end=MONTH_END, catalog=CATALOG)
DSCOPE.update({"store_code": "S-1", "rep": "Dana Rep"})
DSCOPE_P = dd.scope_drilldowns(DSCOPE, CATALOG)


def cat(monthly, achieved, unit="count"):
    return {"unit": unit, "monthly": monthly, "achieved_mtd": achieved,
            "need": max(0.0, monthly - achieved), "today_target": 1.0, "pace": 2.0,
            "base_today": 1.0, "setup_fee_mtd": 0.0}


#: Two store rows as `GET /targets/{period}/summary` serves them. S-2 has NO `targets` row of its
#: own — its accessory target is seeded from the store setup, and it has no activations target at all.
STORES = [
    {"store_code": "S-1", "address": "1 Main St", "market": "North", "has_target_row": True,
     "categories": {"activations": cat(100.0, 47.0), "accessories": cat(1000.0, 400.0, "dollars")},
     "conversion": {"boxes": 10, "billpays": 40, "rate": 25.0, "target": 30.0},
     "trending_acc_target": 900.0, "trending_acc_sales": 880.0, "trending_box": 80, "reps": []},
    {"store_code": "S-2", "address": "2 Oak Ave", "market": "North", "has_target_row": False,
     "categories": {"activations": cat(0.0, 7.0), "accessories": cat(500.0, 600.0, "dollars")},
     "conversion": {"boxes": 5, "billpays": 10, "rate": 50.0, "target": 30.0},
     "trending_acc_target": 650.0, "trending_acc_sales": 640.0, "trending_box": 20, "reps": []},
]
COLL = te.aggregate_stores(STORES)
AREA_P = dd.area_drilldowns(STORES, COLL)

#: The action plan's own shape for the same store-month: a `metrics` LIST, and rep plans carrying the
#: stored commission row. "No Subtotal" is live defect 1 — the calculation never worked out their
#: subtotal, so `at_risk` came back 0.0 beside a 0.5 tier.
PLAN = [
    {"store_code": "S-1", "address": "1 Main St", "market": "North", "has_target_row": True,
     "conversion": {"boxes": 10, "billpays": 40, "rate": 25.0, "target": 30.0},
     "metrics": [{"cat": "activations", "label": "Activations", "unit": "count", "target": 100.0,
                  "achieved": 47.0, "need": 53.0, "pace": 3.0, "today_target": 2.0}],
     "reps": [{"rep": "A Rep", "commission": {"tier": 0.75, "kpis_met": 3, "total_kpis": 7,
                                              "subtotal": 400.0, "at_risk": 100.0,
                                              "payout_slice_basis": "stored"}},
              {"rep": "No Subtotal", "commission": {"tier": 0.5, "kpis_met": 1, "total_kpis": 7,
                                                    "subtotal": None, "at_risk": 0.0,
                                                    "payout_slice_basis": "not_computed"}}],
     "commission_at_risk": 100.0, "counts": {"critical": 1, "warning": 1}},
    {"store_code": "S-3", "address": "3 Elm", "market": "South", "has_target_row": True,
     "conversion": {"boxes": 2, "billpays": 9, "rate": 22.0, "target": 30.0},
     "metrics": [{"cat": "activations", "label": "Activations", "unit": "count", "target": 50.0,
                  "achieved": 30.0, "need": 20.0, "pace": 1.0, "today_target": 1.0}],
     "reps": [], "commission_at_risk": 0.0, "counts": {"critical": 0, "warning": 0}},
]
TEAM = dd.team_totals(PLAN)
TEAM_P = dd.team_drilldowns(PLAN, TEAM)
AT_RISK_P = dd.at_risk_drilldowns(PLAN, 100.0)

#: The org tree names a DM for S-1 and nobody for S-2 — the live proportion, scaled down.
CHAIN = {"S-1": {"district": {"name": "D1"}, "dm": [{"employee_id": "E1", "name": "Dana M"}],
                 "above": [{"employee_id": "M1", "name": "Marco Mgr", "level": "market"}]},
         "S-2": {}}
CARDS = mrc.build(STORES, CHAIN, aggregate=te.aggregate_stores, peer_compared=["S-1"],
                  axis=te.target_axis(None))
CARD_P, NOBODY_P, NOBODY_COLL = dd.report_card_drilldowns(CARDS, STORES)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A. THE GRAIN COUNT, MEASURED.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. six grains over five routes, measured rather than assumed ==")

_AXIS = te.target_axis(None)
check("A1 the axis is asked of its ONE home — the declaration keeps no list of categories",
      [a["key"] for a in dd.axis_of(None)] == [a["key"] for a in _AXIS]
      and "BUILTIN_CATEGORIES" not in DECL_CODE
      and not re.search(r"\[\s*['\"]activations['\"]", DECL_CODE),
      [a["key"] for a in dd.axis_of(None)])

_SCOPE_KEYS = sorted({tuple(sorted((r.get("row_key") or {}).keys()))
                      for p in SCOPE_P.values() for r in p["rows"]})
_AREA_KEYS = sorted({tuple(sorted((r.get("row_key") or {}).keys()))
                     for p in AREA_P.values() for r in p["rows"]})
_RISK_KEYS = sorted({tuple(sorted((r.get("row_key") or {}).keys()))
                     for p in AT_RISK_P.values() for r in p["rows"]})
check("A2 a SCOPE-DAY panel is keyed by the DAY, the store and (in a rep scope) the person — the "
      "date COLUMN differs per provenance family, so it is not one name asserted for both",
      _SCOPE_KEYS == [("shift_date", "store_code"), ("store_code", "trans_date")], _SCOPE_KEYS)
check("A2b and a DECLARED category's days are keyed by the closing date instead, with the rep "
      "carried too",
      any(sorted(r["row_key"]) == ["close_date", "rep_name", "store_code"]
          for r in DSCOPE_P["achieved_dropoffs"]["rows"] if r.get("row_key")),
      [r.get("row_key") for r in DSCOPE_P["achieved_dropoffs"]["rows"][:2]])
check("A3 a STORE-MONTH panel is keyed by the STORE — a different row from a scope-day, in the "
      "same report family",
      _AREA_KEYS == [("store_code",)], _AREA_KEYS)
check("A4 a REP'S COMMISSION row is keyed by the name the calculation stored it under, inside the "
      "store — neither of the other two keys would address it",
      _RISK_KEYS == [("store_code", "storeops_name")], _RISK_KEYS)

#: The three store-month grains differ ONLY in provenance, which is what makes them three.
_ACH_SRC = sorted(s["table"] for s in AREA_P["achieved_activations"]["sources"])
_TGT_SRC = sorted(s["table"] for s in AREA_P["target_activations"]["sources"])
_NEED_SRC = sorted(s["table"] for s in AREA_P["need_activations"]["sources"])
check("A5 THE MEASUREMENT: a store's achieved names the sales feeds and nothing else",
      _ACH_SRC == sorted(dd.SALES_FEEDS), _ACH_SRC)
check("A6 THE DECLARED NUMBER: the same store row's TARGET names the settings rows somebody typed "
      "it into — a target is not a measurement, so it cannot name the feed that measures sales",
      _TGT_SRC == sorted({dd.TARGETS, dd.STORE_ROSTER}) and not (set(_TGT_SRC) & set(dd.SALES_FEEDS)),
      _TGT_SRC)
check("A7 THE GAP: the same store row again, naming BOTH — which is why `need` drills at all "
      "(§CORE-17.3k: subtract at the ROW and it drills)",
      set(_NEED_SRC) == set(_ACH_SRC) | set(_TGT_SRC), _NEED_SRC)
check("A8 so three panels over ONE row shape are three grains, and the harness can tell them apart "
      "by what they name — one catalogue over them would have to claim the sales feed produced a "
      "target",
      len({tuple(_ACH_SRC), tuple(_TGT_SRC), tuple(sorted(_NEED_SRC))}) == 3)
check("A9 and the two SCOPE-DAY grains differ the same way: hours come out of the schedule, in "
      "HOURS, and achievement out of the feeds, in counts or dollars",
      (sorted(s["table"] for s in SCOPE_P["scheduled_hours"]["sources"]) == [dd.SHIFTS]
       and SCOPE_P["scheduled_hours"]["figure"]["unit"] == rd.UNIT_HOURS
       and SCOPE_P["achieved_accessories"]["figure"]["unit"] == rd.UNIT_MONEY
       and SCOPE_P["achieved_activations"]["figure"]["unit"] == rd.UNIT_COUNT),
      [p["figure"]["unit"] for p in SCOPE_P.values()])
check("A10 the unit vocabulary is the ONE home's, and the same two words the report card module "
      "already translates to — not a third spelling",
      (dd.unit_of("dollars") == rd.UNIT_MONEY == "money"
       and dd.unit_of("count") == rd.UNIT_COUNT == "count"
       and {i["unit"] for i in mrc.items_for(te.target_axis(None))}
       <= {rd.UNIT_MONEY, rd.UNIT_COUNT, "pct"}))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B. GRAIN 1 — a scope-day's achievement.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. the days an achieved figure was ACTUALLY summed over ==")

check("B1 every category the scope produced has a panel, and the figure is the scope's own",
      all(SCOPE_P["achieved_%s" % c]["figure"]["amount"]
          == SCOPE["categories"][c]["achieved_mtd"] for c in MONTHLY),
      {c: SCOPE_P["achieved_%s" % c]["figure"]["amount"] for c in MONTHLY})
check("B2 and each one TIES to the days behind it, to the cent",
      all(_tie(SCOPE_P["achieved_%s" % c]) is True for c in MONTHLY),
      {c: SCOPE_P["achieved_%s" % c]["tie_out"] for c in MONTHLY if
       _tie(SCOPE_P["achieved_%s" % c]) is not True})
check("B3 THE EVIDENCE IS NOT THE CALENDAR. The day this scope sold on with NO shift entered is in "
      "the panel and IS counted — a panel folded over the calendar alone would have been short of "
      "the figure it explains, and the tie-out would have said so",
      (_row(SCOPE_P["achieved_activations"], "2026-10-05 · not scheduled")["in_figure"] is True
       and _row(SCOPE_P["achieved_activations"], "2026-10-05 · not scheduled")["amount"] == 3.0
       and "2026-10-05" not in [d["date"] for d in SCOPE["calendar"]]),
      [d["date"] for d in SCOPE["calendar"]][:5])
check("B3b …and the reverse: an OPEN day that recorded nothing is shown and NOT counted",
      (_row(SCOPE_P["achieved_activations"], "2026-10-03")["present"] is False
       and _row(SCOPE_P["achieved_activations"], "2026-10-03")["in_figure"] is False
       and _row(SCOPE_P["achieved_activations"], "2026-10-03")["reason"] == "not_recorded"),
      _row(SCOPE_P["achieved_activations"], "2026-10-03"))
_future = [r for r in SCOPE_P["achieved_activations"]["rows"]
           if (r.get("fields") or {}).get("day") == "future"]
check("B4 A DAY THAT HAS NOT HAPPENED YET IS A THIRD THING, neither a zero nor a missing record — "
      "it says so in the reader's words and is counted in nothing",
      (_future and all(r["amount"] is None and r["in_figure"] is False
                       and r["reason"] == "day_not_yet_reached" and r["reason_label"]
                       for r in _future)),
      len(_future))
check("B5 a day that recorded NOTHING names NO table — a provenance claim about a row that does "
      "not exist is worse than none, so it is a stated, countable untraceable origin (§CORE-17.3i)",
      (_row(SCOPE_P["achieved_activations"], "2026-10-03")["sources"] == []
       and SCOPE_P["achieved_activations"]["untraceable_origins"] >= 1),
      SCOPE_P["achieved_activations"]["untraceable_origins"])
check("B6 A MEASURED ZERO IS A DIFFERENT ROW: the day that sold upgrades but no BYOD is counted at "
      "0, because the feed really did report that day",
      (_row(SCOPE_P["achieved_byod"], "2026-10-02")["amount"] == 0.0
       and _row(SCOPE_P["achieved_byod"], "2026-10-02")["in_figure"] is True
       and _row(SCOPE_P["achieved_byod"], "2026-10-02")["measured"] is True))
check("B7 A DECLARED CATEGORY'S DAYS NAME THE STORE'S OWN DAILY CLOSING, never the sales feed — "
      "the achievement arrives from a different place and the panel says which",
      (sorted(s["table"] for s in DSCOPE_P["achieved_dropoffs"]["sources"])
       == sorted(dd.DECLARED_SOURCES)
       and _tie(DSCOPE_P["achieved_dropoffs"]) is True
       and DSCOPE_P["achieved_dropoffs"]["figure"]["amount"] == 6.0),
      DSCOPE_P["achieved_dropoffs"]["sources"])
check("B8 the panel's SUBJECT is the scope the endpoint was asked about, so a panel can never hold "
      "a different population than the tile it opened from",
      (SCOPE_P["achieved_activations"]["subject"] == "S-1"
       and SCOPE_P["achieved_activations"]["subject_kind"] == "store_code"
       and DSCOPE_P["achieved_dropoffs"]["subject"] == "Dana Rep"
       and DSCOPE_P["achieved_dropoffs"]["subject_kind"] == "rep"))
check("B9 an empty report drills to an empty mapping, not to a panel asserting a tie",
      dd.scope_drilldowns({}) == {} and dd.scope_drilldowns(None) == {})
check("B10 the per-day evidence is recorded by the SAME loop that sums the figure, at full "
      "precision — so it cannot be a different set of days, and a panel of DISPLAYED figures "
      "cannot drift from the month",
      ("_evidence.setdefault(d, {})[cat] = _a" in _read("backend/app/modules/commcalc/targets_engine.py")
       and "'achieved_days'" in _read("backend/app/modules/commcalc/targets_engine.py")))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C. GRAIN 2 — a scope-day's scheduled hours.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. the schedule the whole weighting is spread over ==")

HRS = SCOPE_P["scheduled_hours"]
check("C1 the figure is the CONCRETE schedule and ties to the days with a shift entered",
      (HRS["figure"]["amount"] == SCOPE["scheduled_hours_total"] == 22.5
       and _tie(HRS) is True and HRS["tie_out"]["counted_rows"] == 3), HRS["tie_out"])
_proj = [r for r in HRS["rows"] if (r.get("fields") or {}).get("day") == "future"
         and r["amount"] is not None]
check("C2 A DAY FILLED IN FROM THE WEEKLY PATTERN CARRIES ITS ESTIMATE AND IS NOT COUNTED — it is "
      "in `effective_hours_total` beside the figure, not in it",
      (_proj and all(r["in_figure"] is False and r["measured"] is True for r in _proj)
       and SCOPE["effective_hours_total"] > SCOPE["scheduled_hours_total"]),
      (len(_proj), SCOPE["effective_hours_total"], SCOPE["scheduled_hours_total"]))
check("C3 …and it names NOTHING: nobody has scheduled it, so there is no row to trace (§CORE-17.3i)",
      all(r["sources"] == [] for r in _proj) and HRS["untraceable_origins"] >= len(_proj),
      HRS["untraceable_origins"])
check("C4 a day this scope SOLD on with no shift at all carries no hours and says why, rather than "
      "reading 0 hours",
      (_row(HRS, "2026-10-05 · not scheduled")["amount"] is None
       and _row(HRS, "2026-10-05 · not scheduled")["reason"] == "not_recorded"
       and _row(HRS, "2026-10-05 · not scheduled")["sources"] == []),
      _row(HRS, "2026-10-05 · not scheduled"))
check("C5 the hours sum at FULL precision against a total that is one round over the raw sum",
      "'hours_raw'" in _read("backend/app/modules/commcalc/targets_engine.py"))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §D. GRAINS 3–5 — a store-month: the area roll-up and the accessory tiles.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. the area roll-up opened to the stores it is the sum of ==")

check("D1 every area figure is `aggregate_stores`' OWN, so a panel cannot be a second opinion "
      "about the total above it",
      (AREA_P["achieved_activations"]["figure"]["amount"]
       == COLL["categories"]["activations"]["achieved_mtd"] == 54.0
       and AREA_P["target_accessories"]["figure"]["amount"]
       == COLL["categories"]["accessories"]["monthly"] == 1500.0),
      (AREA_P["achieved_activations"]["figure"], COLL["categories"]["activations"]["achieved_mtd"]))
check("D2 and every one of them ties to the store rows",
      all(_tie(p) is True for p in AREA_P.values()),
      {k: p["tie_out"]["words"] for k, p in AREA_P.items() if _tie(p) is not True})
check("D3 AN UNSET TARGET IS NOT A TARGET OF ZERO — the store nobody set an activations target for "
      "carries NO figure, is counted in nothing, and says so in the reader's words",
      (_row(AREA_P["target_activations"], "2 Oak Ave")["amount"] is None
       and _row(AREA_P["target_activations"], "2 Oak Ave")["in_figure"] is False
       and _row(AREA_P["target_activations"], "2 Oak Ave")["reason"] == "target_not_set"
       and _row(AREA_P["target_activations"], "2 Oak Ave")["reason_label"]),
      _row(AREA_P["target_activations"], "2 Oak Ave"))
check("D3b …and NEITHER IS ITS GAP: a store with no target has no measurable gap, rather than a "
      "gap of zero that would read as being on top of a target it does not have",
      (_row(AREA_P["need_activations"], "2 Oak Ave")["amount"] is None
       and _row(AREA_P["need_activations"], "2 Oak Ave")["reason"] == "target_not_set"),
      _row(AREA_P["need_activations"], "2 Oak Ave"))
check("D3c …and the figure does not move a cent for it, because the roll-up adds nothing for that "
      "store either — only the ROW changes, which is the whole point of putting it there",
      AREA_P["target_activations"]["figure"]["amount"] == 100.0
      and AREA_P["target_activations"]["tie_out"]["ties"] is True)
check("D4 A MEASURED ZERO IS STILL COUNTED: the store that passed its accessory target has a gap "
      "of $0.00 and is in the figure",
      (_row(AREA_P["need_accessories"], "2 Oak Ave")["amount"] == 0.0
       and _row(AREA_P["need_accessories"], "2 Oak Ave")["in_figure"] is True))
check("D5 WHICH SETTINGS ROW A TARGET WAS TYPED INTO IS READ PER ROW, off the producer's own stored "
      "fact: the store with a targets row of its own names it, and the one seeded from the store "
      "setup names THAT — claiming the targets row for both would name a row that does not exist",
      (_row(AREA_P["target_activations"], "1 Main St")["sources"] == [dd.TARGETS]
       and _row(AREA_P["target_accessories"], "2 Oak Ave")["sources"] == [dd.STORE_ROSTER]
       and dd.target_tables({"has_target_row": False}) == (dd.STORE_ROSTER,)
       and dd.target_tables({}) == (dd.TARGETS,)),
      [_row(AREA_P["target_activations"], "1 Main St")["sources"],
       _row(AREA_P["target_accessories"], "2 Oak Ave")["sources"]])
check("D6 a target names the COLUMN it was read out of, dereferenced from the engine's own map — "
      "BYOD names the percentage it is derived from, because it has no column of its own",
      (dd.target_origin("activations")[1] == te.BUILTIN_TARGET_COLUMN["activations"]
       and dd.target_origin("byod")[1] == dd.COL_BYOD_PCT
       and dd.target_origin("dropoffs") == ((dd.TARGET_LINE,), dd.COL_TARGET_VALUE, "commcalc")),
      [dd.target_origin(c) for c in ("activations", "byod", "dropoffs")])
check("D7 THE PROJECTION IS LABELLED AS ONE: the trending tiles drill to the stores, and the note "
      "says every row is a projection rather than a measurement",
      ("projection" in AREA_P["trending_accessory_dollars"]["note"].lower()
       and AREA_P["trending_accessory_dollars"]["figure"]["amount"] == 1550.0
       and _tie(AREA_P["trending_accessory_dollars"]) is True
       and AREA_P["trending_box_count"]["figure"]["unit"] == rd.UNIT_COUNT),
      AREA_P["trending_accessory_dollars"]["tie_out"])
check("D8 an empty area drills to nothing",
      dd.area_drilldowns([], COLL) == {} and dd.area_drilldowns(STORES, None) == {})
check("D9 every drill row carries the ROW KEY the screen narrows on, so the accessory page can show "
      "a panel over exactly the stores it is showing (§CORE-17.3d)",
      all(r.get("row_key", {}).get("store_code") for p in AREA_P.values() for r in p["rows"]))

#: THE ACCESSORY PAGE'S OWN CLIENT FILTER, ported — the stores it drops have no accessory target, no
#: accessory sales and no projection, so the four tiles are identical over both populations. The
#: narrowed panel is what the page shows; `prove_drill_narrow.mjs` pins the TS fold to the server's.
_shown = [s for s in STORES
          if (s["categories"]["accessories"]["monthly"] or 0) > 0
          or (s["categories"]["accessories"]["achieved_mtd"] or 0) > 0
          or (s.get("trending_acc_target") or 0) > 0]
_page_tot = {
    "target_accessories": round(sum(s["categories"]["accessories"]["monthly"] for s in _shown), 2),
    "achieved_accessories": round(sum(s["categories"]["accessories"]["achieved_mtd"] for s in _shown), 2),
    "need_accessories": round(sum(max(0.0, s["categories"]["accessories"]["need"]) for s in _shown), 2),
    "trending_accessory_dollars": round(sum(s["trending_acc_target"] for s in _shown), 2),
}
check("D10 NO FIGURE MOVED on the accessory page: each of its four drillable tiles equals the "
      "server panel it now reads, to the cent, over the stores it shows",
      all(AREA_P[k]["figure"]["amount"] == v for k, v in _page_tot.items()),
      {k: (AREA_P[k]["figure"]["amount"], v) for k, v in _page_tot.items()})
check("D10b and that comparison CAN fail — a dropped store gives a different answer, so D10 is a "
      "check and not a tautology",
      round(sum(s["categories"]["accessories"]["monthly"] for s in _shown[:1]), 2)
      != _page_tot["target_accessories"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §E. THE ACTION PLAN — the team total, folded once, and the reps behind the money at risk.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. the action plan's two figures ==")


def _page_reduce(stores):
    """The Daily Action Plan page's OWN reduce, as it stood before this batch — verbatim."""
    agg = {}
    for s in stores:
        for m in (s.get("metrics") or []):
            a = agg.setdefault(m["cat"], {"target": 0.0, "achieved": 0.0, "need": 0.0})
            a["target"] += m.get("target") or 0
            a["achieved"] += m.get("achieved") or 0
            a["need"] += m.get("need") or 0
    return agg


_old = _page_reduce(PLAN)
check("E1 NO FIGURE MOVED — the team total the page used to reduce in TypeScript and the one home's "
      "fold agree exactly, which is what makes serving it a presentation fix and not a number "
      "changing",
      (TEAM["categories"]["activations"]["monthly"] == _old["activations"]["target"] == 150.0
       and TEAM["categories"]["activations"]["achieved_mtd"] == _old["activations"]["achieved"] == 77.0
       and TEAM["categories"]["activations"]["need"] == _old["activations"]["need"] == 73.0),
      (TEAM["categories"]["activations"], _old["activations"]))
check("E1b and that comparison can fail too",
      _page_reduce(PLAN[:1])["activations"]["target"] != 150.0)
check("E2 the team panels tie to the plan's own store rows",
      all(_tie(p) is True for p in TEAM_P.values()) and len(TEAM_P) == 3,
      {k: p["tie_out"]["words"] for k, p in TEAM_P.items()})
check("E3 and the plan's row shape is read without a second definition of the same three figures — "
      "the catalogue is the SAME one the area roll-up uses",
      (dd.cat_block(PLAN[0], "activations")["monthly"] == 100.0
       and dd.cat_block(STORES[0], "activations")["monthly"] == 100.0
       and sorted(TEAM_P) == ["achieved_activations", "need_activations", "target_activations"]))

RISK = AT_RISK_P["commission_at_risk"]
check("E4 the at-risk figure opens to the REPS, out of the calculated-commission rows",
      (RISK["figure"]["amount"] == 100.0
       and [s["table"] for s in RISK["sources"]] == [dd.REP_COMMISSIONS]
       and _tie(RISK) is True), RISK["tie_out"])
check("E5 LIVE DEFECT 1, SURFACED: the rep whose subtotal the calculation never worked out is shown "
      "WITHOUT a figure and is not counted — `at_risk: 0.0` was the endpoint's answer for both "
      "'nothing at risk' and 'we never worked out what was at risk'",
      (_row(RISK, "No Subtotal · 1 Main St")["amount"] is None
       and _row(RISK, "No Subtotal · 1 Main St")["in_figure"] is False
       and _row(RISK, "No Subtotal · 1 Main St")["reason"] == "not_computed"),
      _row(RISK, "No Subtotal · 1 Main St"))
check("E6 LIVE DEFECT 2, the honest half: a total the endpoint DELIBERATELY suppressed for a "
      "self-scoped rep gets NO panel at all — not an empty one — because a figure that was not "
      "measured has no rows behind it",
      dd.at_risk_drilldowns(PLAN, 0.0, rolled_up=False) == {}
      and dd.at_risk_drilldowns(PLAN, None) == {})
check("E7 the rep's tier and KPI count travel on the row, so the reader can see WHY it is at risk "
      "without leaving the panel",
      (_row(RISK, "A Rep · 1 Main St")["fields"]["tie" "r"] == 0.75
       and _row(RISK, "A Rep · 1 Main St")["fields"]["kpis met"] == 3))
check("E8 the commission snapshot's name is DEREFERENCED from the commission batch's own "
      "declaration, not spelled a second time",
      dd.REP_COMMISSIONS == "rep_commissions"
      and DECL_CODE.count('"rep_commissions"') == 0
      and "_cpd.REP_COMMISSIONS" in DECL_SRC)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §G. THE MANAGER REPORT CARDS.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. what was assigned to a manager, and what answers to nobody ==")

_E1 = CARD_P["E1"]
check("G1 a DM card's roll-up opens to the stores ON that card, and ties",
      (_E1["achieved_activations"]["figure"]["amount"] == 47.0
       and _E1["achieved_activations"]["rows_total_count"] == 1
       and all(_tie(p) is True for p in _E1.values())),
      {k: p["tie_out"]["words"] for k, p in _E1.items()})
check("G1b and its subject is the manager, so one card's panel can never hold another's stores",
      _E1["achieved_activations"]["subject"] == "Dana M"
      and _E1["achieved_activations"]["subject_kind"] == "district_manager")
check("G2 the check-off's own three-state verdict decides whether a target exists — a `no_target` "
      "item carries no figure here either, so the card and its evidence agree about what was set",
      (dd._card_target("activations")({"items": [{"key": "activations", "state": "no_target",
                                                  "target": 0.0}]}) is None
       and dd._card_target("activations")({"items": [{"key": "activations", "state": "missed",
                                                      "target": 100.0}]}) == 100.0))
check("G3 THE CONVERSION RATE AND THE PEER VERDICT GET NO PANEL: a rate is a quotient "
      "(§CORE-17.3h) and the peer item has no number at all — 'the comparison IS the target'",
      (not any(k.endswith("conversion") or k.endswith("peer_gap") for k in _E1)
       and {i["key"] for i in CARDS["items"]} >= {"conversion", "peer_gap"}),
      sorted(_E1))
check("G4 THE ORG-CHART GAP HAS A NUMBER NOW: the stores the tree cannot place get a roll-up of "
      "their own, opened to the stores it is — the coverage note says how many, this says how much",
      (sorted(NOBODY_P) == ["achieved_accessories", "achieved_activations",
                            "target_accessories", "target_activations"]
       and NOBODY_P["achieved_accessories"]["figure"]["amount"] == 600.0
       and all(_tie(p) is True for p in NOBODY_P.values())
       and NOBODY_COLL["categories"]["accessories"]["achieved_mtd"] == 600.0),
      sorted(NOBODY_P))
check("G4b …and the stores in it are exactly the ones the card builder reported as unassigned, read "
      "back rather than re-decided",
      ([r["store_code"] for r in dd.unassigned_rows(STORES, CARDS["unassigned"])] == ["S-2"]
       and [u["store_code"] for u in CARDS["unassigned"]] == ["S-2"]))
_M1 = CARD_P["M1"]
check("G5 a MARKET MANAGER's card rolls up BY DM — one row per district manager, each carrying "
      "their own roll-up",
      (_M1["by_dm_achieved_activations"]["figure"]["amount"] == 47.0
       and _row(_M1["by_dm_achieved_activations"], "Dana M · 1 store(s)")["amount"] == 47.0
       and _tie(_M1["by_dm_achieved_activations"]) is True),
      _M1["by_dm_achieved_activations"]["tie_out"])
check("G6 AND IT CANNOT MEASURE WHAT THE ORG TREE NEVER PLACED. A store under no district manager "
      "is listed WITHOUT a figure and counted in nothing — unattributable is not zero — with the "
      "reason in the reader's words out of the ONE home's vocabulary",
      (_row(_M1["by_dm_achieved_activations"], "2 Oak Ave — on nobody's card")["amount"] is None
       and _row(_M1["by_dm_achieved_activations"],
                "2 Oak Ave — on nobody's card")["reason"] == "no_dm_in_org_tree"
       and _row(_M1["by_dm_achieved_activations"],
                "2 Oak Ave — on nobody's card")["reason_label"]
       == rd.UNMEASURED_REASONS["no_dm_in_org_tree"]
       and _row(_M1["by_dm_achieved_activations"],
                "2 Oak Ave — on nobody's card")["sources"] == []),
      _row(_M1["by_dm_achieved_activations"], "2 Oak Ave — on nobody's card"))
check("G6b …and the figure is untouched by those rows, so naming the hole costs nothing",
      _M1["by_dm_achieved_activations"]["figure"]["amount"]
      == CARDS["manager_cards"][0]["collective"]["categories"]["activations"]["achieved_mtd"])
check("G7 a tenant whose tree places NOBODY still gets the 'on nobody's card' roll-up and no card "
      "panels at all — which is today's live proportion at both tenants",
      (lambda c: (c[0] == {} and sorted(c[1]) and c[2]))(
          dd.report_card_drilldowns(
              mrc.build(STORES, {}, aggregate=te.aggregate_stores, axis=te.target_axis(None)),
              STORES)),
      "see report_card_drilldowns over an empty chain")
check("G8 an empty report drills to nothing at every level",
      dd.report_card_drilldowns({}, []) == ({}, {}, {})
      and dd.card_drilldowns({}, CARDS["items"]) == {}
      and dd.manager_card_drilldowns({}, [], [], CARDS["items"]) == {})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §Y. BUILD LOCKS — so it cannot un-wire.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== Y. build locks ==")

_ENDPOINT_FNS = {
    "get_target_calendar": "scope_drilldowns",
    "get_targets_summary": "area_drilldowns",
    "get_action_plan": "team_drilldowns",
    "get_manager_report_cards": "report_card_drilldowns",
}
for _fn, _producer in _ENDPOINT_FNS.items():
    _src = _func_src(CC_ROUTER, _fn)
    check("Y1(%s) the endpoint ATTACHES the declaration's output and folds no panel of its own" % _fn,
          ("_targets_dd.%s(" % _producer) in _src and "drilldown" in _src
          and "rd.panel(" not in _src and '"tie_out"' not in _src
          and '"rows_total_count"' not in _src,
          _src[-300:])
_AP = _func_src(CC_ROUTER, "get_action_plan")
check("Y1b the action plan also serves the at-risk panel, and only where the roll-up was really "
      "computed — `rolled_up` is the endpoint's own suppression rule, not a copy of it",
      "_targets_dd.at_risk_drilldowns(" in _AP and "rolled_up=rep_keys is None" in _AP)
check("Y1c and it serves the team total the page used to reduce, from the one home",
      "_targets_dd.team_totals(" in _AP and "'team_total'" in _AP)

_MIG = ""
import glob  # noqa: E402

for _f in glob.glob(os.path.join(ROOT, "database", "migrations", "*.sql")):
    _MIG += open(_f, encoding="utf-8").read()
DRILL_TABLES = sorted(set(dd.SALES_FEEDS) | set(dd.DECLARED_SOURCES)
                      | {dd.TARGETS, dd.TARGET_LINE, dd.STORE_ROSTER, dd.SHIFTS,
                         dd.REP_COMMISSIONS})
check("Y2 every table a drill on this batch can name is CREATED BY A MIGRATION — a provenance claim "
      "about a relation that does not exist is worse than none",
      [t for t in DRILL_TABLES if not re.search(r"\b%s\b" % t, _MIG)] == [],
      [t for t in DRILL_TABLES if not re.search(r"\b%s\b" % t, _MIG)])
_LABELS = _read("frontend/src/lib/sourceLabels.ts")
check("Y3 BUILD LOCK — and every one has a `sourceLabels.ts` entry, so the operator reads 'monthly "
      "target settings', never an identifier (§19.38)",
      [t for t in DRILL_TABLES if not re.search(r"^\s+%s:" % t, _LABELS, re.M)] == [],
      [t for t in DRILL_TABLES if not re.search(r"^\s+%s:" % t, _LABELS, re.M)])
check("Y4 the two FEED families are dereferenced from the lineage registry, never spelled here",
      (dd.SALES_FEEDS == tuple(lineage.SALES_DISPLAY_SOURCES)
       and dd.DECLARED_SOURCES[0] == lineage.DAILY_CLOSING
       and not re.search(r'"(daily_sales_feed|raw_sales|daily_closing)"', DECL_CODE)),
      DECL_CODE[:0] or "see the declaration")

#: WHAT GETS NO PANEL — not an empty one: NO KEY.
_ALL_KEYS = (set(SCOPE_P) | set(AREA_P) | set(TEAM_P) | set(AT_RISK_P) | set(NOBODY_P)
             | {k for p in CARD_P.values() for k in p})
check("Y5 NO PANEL FOR AN ATTAINMENT PERCENTAGE, A COMPOSED FIGURE, A LEAF OR A COUNT WHOSE LIST "
      "IS ON SCREEN — no key at all, so a screen offers no link rather than an empty panel",
      not any(re.search(r"(attainment|score|pct|percent|conversion|today_target|pace|base_today"
                        r"|stores_on_track|stores_with_target|critical|warning)", k)
              for k in _ALL_KEYS),
      sorted(_ALL_KEYS))
check("Y5b and the catalogues do not declare one either — the refusal is in the declaration, not "
      "only in what a fixture happened to produce",
      not re.search(r'"(pace|today_target|base_today|attainment_pct|score_pct|rate)"', DECL_CODE))
check("Y5c …and that scan is NOT vacuous: the producers really do serve those figures, so a "
      "catalogue that grew one would be caught",
      ("attainment_pct" in COLL["categories"]["activations"]
       and "pace" in SCOPE["categories"]["activations"]
       and CARDS["dm_cards"][0]["tally"]["score_pct"] is not None))

for _route, _page in PAGES.items():
    _src = _read(_page)
    check("Y6(%s) the page renders the SHARED component and hand-rolls no panel" % _route,
          ("DrillDownPanel" in _src and "useDrillTile(" in _src
           and "tie_out" not in _src and "rows_total_count" not in _src),
          _route)
_AREA_SRC = _read(AREA_COMPONENT)
check("Y6b the area component does too — the DM's roll-up is rendered by the one renderer",
      "DrillDownPanel" in _AREA_SRC and "useDrillTile(" in _AREA_SRC
      and "tie_out" not in _AREA_SRC)

#: CLIENT NARROWING — wired on exactly ONE of these screens, and the lock says which.
_NARROWERS = {r: ("@/lib/drill-narrow" in _read(p)) for r, p in PAGES.items()}
check("Y7 CLIENT NARROWING IS WIRED ON THE ONE SCREEN THAT NEEDS IT and refused on the other four. "
      "The accessory page drops stores with no accessory activity AFTER the server folded, so its "
      "panels are narrowed to the same selection its tiles are (§CORE-17.3d); every filter on the "
      "other four is a QUERY PARAMETER, and this FAILS THE BUILD if one is 'fixed' into a class it "
      "was never in",
      _NARROWERS == {"/commcalc/targets": False, "/commcalc/targets/my": False,
                     "/commcalc/targets/accessories": True,
                     "/commcalc/targets/action-plan": False,
                     "/commcalc/targets/report-cards": False},
      _NARROWERS)
check("Y7b and the narrowing screen reads its tile numbers OFF the narrowed panel, with the gate "
      "that refuses the drill unless the two agree to the cent — the one home's gate, not a "
      "condition the page invented",
      ("narrowPanels(" in _read(PAGES["/commcalc/targets/accessories"])
       and "agreesToTheCent(" in _read(PAGES["/commcalc/targets/accessories"])
       and "figure?.amount" in _read(PAGES["/commcalc/targets/accessories"])))
check("Y8 the action-plan page no longer reduces the team total itself — two folds of one figure "
      "are two numbers hoping to agree",
      ("data.team_total?.categories" in _read(PAGES["/commcalc/targets/action-plan"])
       and "a.target += m.target" not in _read(PAGES["/commcalc/targets/action-plan"])))
check("Y9 RULE TWO — no carrier, tenant, product, vendor or store name in the declaration",
      not re.search(r"(?i)\b(boost|total wireless|vidapay|luxelink|cellfonz|verizon|t-?mobile|"
                    r"at&t|tracfone|rq|b2bsoft|b2b ?soft)\b", DECL_SRC))
_FLOATS = [m for m in re.findall(r"(?<![\w.])\d+\.\d+(?![\w.])", DECL_CODE) if float(m) != 0.0]
check("Y10 BUILD LOCK — the declaration carries NO non-zero numeric literal: every amount it "
      "presents was handed to it, so it can never become a second opinion about money",
      _FLOATS == [], _FLOATS)
check("Y10b NEGATIVE CONTROL — the scan really does catch one, so Y10 is not a check that cannot "
      "fire",
      [m for m in re.findall(r"(?<![\w.])\d+\.\d+(?![\w.])", "x = 12.50 ; y = 0.0")
       if float(m) != 0.0] == ["12.50"])
check("Y11 BUILD LOCK — the declaration is PURE: no DB, no framework, no clock",
      not re.search(r"^(import|from)\s+(fastapi|supabase|datetime|time|requests|httpx|pandas)",
                    DECL_SRC, re.M))
check("Y11b …and it defines no panel shape of its own: the fold is the one home's",
      "report_drilldown" in DECL_SRC and "def panel(" not in DECL_SRC
      and "def tie_out(" not in DECL_SRC)
_REASONS = set(re.findall(r'^REASON_\w+ = "(\w+)"', DECL_SRC, re.M))
check("Y12 every reason key this declaration can emit has a SENTENCE in the one home's vocabulary, "
      "so a reader is never shown a bare field name",
      _REASONS and all(k in rd.UNMEASURED_REASONS for k in _REASONS), sorted(_REASONS))
check("Y12b and the three the batch added are there in the reader's words, not a fourth spelling "
      "of somebody else's",
      all(len(rd.UNMEASURED_REASONS[k]) > 40
          for k in ("target_not_set", "day_not_yet_reached", "no_dm_in_org_tree")))
check("Y13 the targets ENGINE still owns the arithmetic and the declaration owns none of it: the "
      "team total is `aggregate_stores`, the axis is `target_axis`, the target's home is "
      "`target_store`",
      all(s in DECL_TIGHT for s in ("_te.aggregate_stores(", "_te.target_axis(", "_te.target_store(",
                                    "_te.BUILTIN_TARGET_COLUMN")))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §Z. NEGATIVE CONTROLS — a tie-out that cannot fail is not a proof.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== Z. negative controls — each one BREAKS a tie-out, or pins a reported defect ==")

# Z1 — A ONE-CENT TOTAL.
_z1 = rd.tile_panel("Accessories achieved", 175.76, dd.day_rows(SCOPE),
                    value=dd._achieved_reader("accessories"), row_label=dd._day_label,
                    tables=dd.SALES_FEEDS, schema=dd.SCHEMA_COMMCALC, unit=rd.UNIT_MONEY)
check("Z1 a ONE-CENT total breaks the achieved tie-out, in words",
      _tie(_z1) is False and "disagree" in _z1["tie_out"]["words"], _z1["tie_out"])

# Z2 — A DROPPED ROW. The day this scope sold on with no shift entered vanishes from the evidence.
_z2rows = [r for r in dd.day_rows(SCOPE) if r["date"] != "2026-10-05"]
_z2 = rd.tile_panel("Activations achieved", SCOPE["categories"]["activations"]["achieved_mtd"],
                    _z2rows, value=dd._achieved_reader("activations"), row_label=dd._day_label,
                    tables=dd.SALES_FEEDS, schema=dd.SCHEMA_COMMCALC, unit=rd.UNIT_COUNT)
check("Z2 a DROPPED day breaks it too — which is exactly what folding the panel over the CALENDAR "
      "instead of over the days the figure was summed over would have done",
      _tie(_z2) is False and _z2["tie_out"]["difference"] == -3.0, _z2["tie_out"])

# Z3 — A FIGURE FOLDED IN AT $0.00 NOBODY RECORDED. The careless at-risk declaration coerces the
# rep whose subtotal was never computed; BOTH tie, and the difference is visible only on the ROW.
_z3 = rd.tile_panel("Commission at risk", 100.0, dd.at_risk_rows(PLAN),
                    value=lambda r: ((r or {}).get("commission") or {}).get("at_risk"),
                    row_label=dd._at_risk_label, tables=(dd.REP_COMMISSIONS,),
                    schema=dd.SCHEMA_COMMCALC, unit=rd.UNIT_MONEY)
check("Z3 the CARELESS declaration folds the rep nobody priced in at $0.00 and TIES, and the "
      "honest one shows them instead and ties too — the figure is identical, the row is not, and "
      "the row is the whole point",
      (_tie(_z3) is True and _z3["tie_out"]["counted_rows"] == 2
       and _row(_z3, "No Subtotal · 1 Main St")["amount"] == 0.0
       and _tie(RISK) is True and RISK["tie_out"]["counted_rows"] == 1
       and _z3["figure"]["amount"] == RISK["figure"]["amount"]),
      (_z3["tie_out"], RISK["tie_out"]))

# Z4 — AN UNSET TARGET FOLDED IN AS ZERO. Same shape, on the figure a manager acts on.
_z4 = rd.tile_panel("Activations assigned", COLL["categories"]["activations"]["monthly"], STORES,
                    value=lambda r: dd.cat_block(r, "activations").get("monthly"),
                    row_label=dd._store_label, keys=("store_code",), tables=dd.target_tables,
                    schema=None, unit=rd.UNIT_COUNT)
check("Z4 the CARELESS target declaration folds the store nobody set a target for in as a target "
      "of ZERO — it ties, exactly as the honest one does, and only the row tells the reader that "
      "the store has no target rather than a target it is failing",
      (_tie(_z4) is True and _z4["tie_out"]["counted_rows"] == 2
       and _row(_z4, "2 Oak Ave")["amount"] == 0.0
       and _row(_z4, "2 Oak Ave")["measured"] is True
       and AREA_P["target_activations"]["tie_out"]["counted_rows"] == 1),
      (_z4["tie_out"], AREA_P["target_activations"]["tie_out"]))

# Z5 — A TAMPERED BREAKDOWN CELL. One store's achieved is edited after the fold.
_z5rows = [dict(s, categories=dict(s["categories"])) for s in STORES]
_z5rows[0]["categories"]["activations"] = dict(_z5rows[0]["categories"]["activations"],
                                               achieved_mtd=48.0)
_z5 = rd.tile_panel("Activations achieved", COLL["categories"]["activations"]["achieved_mtd"],
                    _z5rows, value=dd._achieved_of("activations"), row_label=dd._store_label,
                    keys=("store_code",), tables=dd.SALES_FEEDS, schema=dd.SCHEMA_COMMCALC,
                    unit=rd.UNIT_COUNT)
check("Z5 a TAMPERED store cell breaks the area tie-out",
      _tie(_z5) is False and _z5["tie_out"]["difference"] == 1.0, _z5["tie_out"])

# Z6 — A COUNTED ROW WITH NO NUMBER. `ties` must be None, never True and never False.
_z6 = rd.tile_panel("Activations assigned", COLL["categories"]["activations"]["monthly"], STORES,
                    value=dd._target_of("activations"), row_label=dd._store_label,
                    keys=("store_code",), tables=dd.target_tables, schema=None,
                    unit=rd.UNIT_COUNT, in_figure=lambda _r: True)
check("Z6 a declaration that COUNTS the store with no target cannot claim a tie at all — `ties` is "
      "None, never False, because a sum missing a row was never a comparison",
      _tie(_z6) is None and "cannot be added up" in _z6["tie_out"]["words"], _z6["tie_out"])

# Z7 — A TAMPERED DM ROLL-UP. The manager's by-DM panel is a real comparison: the figure is folded
# over every store beneath them at once, the rows are each DM's own roll-up.
_z7cards = mrc.build(STORES, CHAIN, aggregate=te.aggregate_stores, axis=te.target_axis(None))
_z7dms = [dict(c) for c in _z7cards["dm_cards"]]
_z7dms[0]["collective"] = {"categories": {"activations": {"achieved_mtd": 40.0}}}
_z7 = dd.manager_card_drilldowns(_z7cards["manager_cards"][0], _z7dms, _z7cards["unassigned"],
                                 _z7cards["items"])
check("Z7 a TAMPERED district-manager roll-up breaks the market manager's tie-out — the two sides "
      "really are folded over different groupings, so the panel compares rather than restates",
      _tie(_z7["by_dm_achieved_activations"]) is False
      and _z7["by_dm_achieved_activations"]["tie_out"]["difference"] == -7.0,
      _z7["by_dm_achieved_activations"]["tie_out"])

# Z8 — A GUESSED PROVENANCE. Naming the targets row for every store would name one that does not
# exist for the store seeded from the store setup.
check("Z8 a careless target declaration naming the targets row on EVERY row would claim a settings "
      "row that does not exist for the seeded store — the real one reads the producer's own stored "
      "fact per row",
      (dd.target_tables(STORES[1]) == (dd.STORE_ROSTER,)
       and dd.target_tables(STORES[0]) == (dd.TARGETS,)
       and dd.target_tables(STORES[1]) != dd.target_tables(STORES[0])))
check("Z8b and a day with nothing recorded resolves to NO table at all, which the panel reports as "
      "a countable untraceable origin rather than a silent blank",
      (dd._achieved_tables(dd.SALES_FEEDS)({"has_actual": False}) == ()
       and dd._achieved_tables(dd.SALES_FEEDS)({"has_actual": True}) == tuple(dd.SALES_FEEDS)
       and dd._hours_tables({"projected": True, "scheduled": True}) == ()
       and dd._hours_tables({"scheduled": False}) == ()
       and dd._hours_tables({"scheduled": True}) == (dd.SHIFTS,)))

# Z9 — THE ROW KEYS REALLY ADDRESS THEIR OWN GRAIN. A panel whose keys addressed a different row
# would be proof about a different number.
check("Z9 a scope-day key names a DATE and a store-month key does not, so a screen cannot line up "
      "the wrong two",
      ("trans_date" in (SCOPE_P["achieved_activations"]["rows"][0]["row_key"] or {})
       and "trans_date" not in (AREA_P["achieved_activations"]["rows"][0]["row_key"] or {})))

# Z10 — LIVE DEFECT 3, PINNED. A table name reaches the report-card page's column tooltips.
_RC_PAGE = _read(PAGES["/commcalc/targets/report-cards"])
check("Z10 LIVE DEFECT 3, PINNED — the report card's item `source` prose names a TABLE and the page "
      "puts it in every item column's tooltip, which is a database name on a manager's screen "
      "(§19.38). NOT fixed here: that string is the card module's own declared vocabulary and "
      "rewording it is a copy change across three surfaces. If someone fixes it this check goes red "
      "and the index note must follow",
      (any("commcalc." in (i.get("source") or "") for i in mrc.ITEMS)
       and "title={i.source}" in _RC_PAGE),
      [i.get("source") for i in mrc.ITEMS][:2])
check("Z10b …and the panels this batch serves name the same relations through `sourceLabel` "
      "instead, which is what the operator should have been reading",
      "sourceLabel(" in _read("frontend/src/components/DrillDownPanel.tsx")
      and all(s["table"] in DRILL_TABLES for p in CARD_P["E1"].values() for s in p["sources"]))

# Z11 — LIVE DEFECT 4, PINNED AT THE SOURCE. An unparseable sale date counts toward conversion and
# toward nothing else.
_SC = _func_src(ENGINE, "scope_conversion")
_SA = _func_src(ENGINE, "scope_actuals_by_day")
check("Z11 LIVE DEFECT 4, PINNED — a sale row whose `trans_date` will not parse is KEPT by "
      "`scope_conversion` (`if d and d > today`) and DROPPED by `scope_actuals_by_day` (`if not d: "
      "continue`), so it moves the conversion rate and no per-day figure. Reported, not fixed: "
      "making the two agree moves a figure — and it is why the conversion numerator and "
      "denominator are not folded per day here",
      ("if d and d > today:" in _SC and "if not d:" in _SA and "continue" in _SA),
      (_SC[-200:], _SA[-200:]))
check("Z11b and the conversion RATE is refused a panel for the §CORE-17.3h reason, with its own "
      "numerator and denominator already printed beside it on every screen that shows it",
      all("boxes" in _read(p) and "billpays" in _read(p)
          for p in (PAGES["/commcalc/targets"], PAGES["/commcalc/targets/my"]))
      and not any("conversion" in k for k in _ALL_KEYS))

# Z12 — LIVE DEFECT 2 AT THE SOURCE: the suppressed roll-up really is zeroed by the endpoint.
check("Z12 LIVE DEFECT 2, PINNED — the endpoint zeroes the STORE at-risk roll-up for a rep "
      "(a store-wide figure is a cross-rep figure) and the tile still renders `$0`, which reads as "
      "'nothing is at risk'. NOT fixed: what that tile should say is a decision about a figure. The "
      "honest half is that no panel is served for it",
      "store_at_risk = 0.0" in _AP and "rep_keys is not None" in _AP)

# Z13 — THE DECLARED-CATEGORY CRASH, FIXED AT THE MECHANISM, AND NO BUILT-IN FIGURE MOVED.
_DECL_STORES = [
    {"store_code": "S-1", "address": "1 Main St",
     "categories": {"activations": cat(100.0, 47.0), "dropoffs": cat(40.0, 6.0)},
     "conversion": {"boxes": 1, "billpays": 2, "rate": 50.0, "target": 30.0}},
]


def _old_aggregate_dec(rows):
    """`aggregate_stores`' rounding as it stood BEFORE this batch — `UNITS[cat]`, the BUILT-IN map."""
    axis, _u = te._axis_of_rows(rows)
    return [te.UNITS[c] for c in axis]


_raised = False
try:
    _old_aggregate_dec(_DECL_STORES)
except KeyError:
    _raised = True
_new = te.aggregate_stores(_DECL_STORES)
check("Z13 LIVE DEFECT 5, FIXED AT THE MECHANISM — `aggregate_stores` asked the BUILT-IN unit map "
      "how many decimals to round to, so a tenant with a category declared on Count Fields raised "
      "`KeyError` and took the whole area roll-up with it (and every manager report card, which "
      "rolls up through the same function). It asks the unit THESE ROWS reported now",
      _raised and _new["categories"]["dropoffs"]["achieved_mtd"] == 6.0,
      (_raised, sorted(_new["categories"])))
check("Z13b NO FIGURE MOVED — for every BUILT-IN category the old map and the new reader give the "
      "same unit, because that is where `compute_scope` read it from in the first place",
      all(te.UNITS[c] == (te._axis_of_rows(STORES)[1].get(c))
          for c in te._axis_of_rows(STORES)[0]),
      te._axis_of_rows(STORES))
check("Z13c …and the built-in roll-up is byte-identical to the rounding the old line produced",
      (COLL["categories"]["activations"]["achieved_mtd"] == round(47.0 + 7.0, 1)
       and COLL["categories"]["accessories"]["monthly"] == round(1000.0 + 500.0, 2)))

# Z14 — A STORE UNDER TWO DISTRICT MANAGERS IS COUNTED TWICE ON BOTH SIDES, so the panel cannot
# surface it — stated, so nobody reads the tie-out as having checked for it.
_z14chain = {"S-1": {"district": {"name": "D1"},
                     "dm": [{"employee_id": "E1", "name": "Dana M"},
                            {"employee_id": "E2", "name": "Eli M"}],
                     "above": [{"employee_id": "M1", "name": "Marco Mgr"}]},
             "S-2": {}}
_z14 = mrc.build(STORES, _z14chain, aggregate=te.aggregate_stores, axis=te.target_axis(None))
_z14p = dd.manager_card_drilldowns(_z14["manager_cards"][0], _z14["dm_cards"],
                                   _z14["unassigned"], _z14["items"])
check("Z14 STATED, NOT CLAIMED — a store the tree places under TWO district managers is counted "
      "twice in the manager's own roll-up AND twice across the rows, so this panel ties and cannot "
      "surface it. The card module reports that case as `store_card_placements`; the panel is not a "
      "second answer to it",
      (_z14["coverage"]["store_card_placements"] == 2
       and _tie(_z14p["by_dm_achieved_activations"]) is True
       and _z14p["by_dm_achieved_activations"]["figure"]["amount"] == 94.0),
      (_z14["coverage"], _z14p["by_dm_achieved_activations"]["figure"]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §I. REGISTRATION — the index rules are a build gate, not advice (CLAUDE.md).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== I. registration ==")

_IDX = _read("docs/SYSTEM_DATA_FLOW_INDEX.md")
for _f in ("targets_drilldown.py", "harness_batch13_drilldown.py"):
    check("I1(%s) registered in the index by its path" % _f, _f in _IDX)
check("I2 and the section this batch adds is there, so the next reader does not re-derive why a "
      "target is a different grain from what was sold", "CORE-17.3m" in _IDX)
_MG = _read("backend/app/modules/core/module_graph.py")
check("I3 the declaration is a caller of the one home in the module graph, and the harness is one "
      "of its locks",
      "commcalc/targets_drilldown.py" in _MG and "harness_batch13_drilldown.py" in _MG)
_WF = _read(".github/workflows/carrier-vocab-guard.yml")
check("I4 the harness is locked into CI, with its own paths-filter block",
      "harness_batch13_drilldown.py" in _WF and _WF.count("harness_batch13_drilldown.py") >= 2)
_LEDGER = _read("backend/harness_drilldown_pending.txt")
for _r in PAGES:
    _ln = next((ln for ln in _LEDGER.splitlines() if ln.split("\t")[0] == _r), "")
    check("I5(%s) the ledger reads `shared` and says what it REFUSES to claim — an excuse nobody "
          "wrote down is indistinguishable from an oversight" % _r,
          _ln.split("\t")[1:2] == ["shared"] and "REFUSES to claim" in _ln, _ln[:160])


def _owes_max(src):
    """THE RATCHET'S PIN, or None when it cannot be read at all.

    SHRINK-ONLY, AND NOT RE-TIGHTENED TO `==` (batch 11's correction of batch 10): the rule the
    ratchet states is *"may only shrink"*, and `<=` IS that rule. A value assertion expires on the
    next correct change, and the next reader's reflex when it goes red is to edit the number — which
    is how a ratchet quietly becomes decoration. It still FAILS CLOSED: a pin above this batch's 59
    is red, and a pin that cannot be read at all is red rather than absent."""
    m = re.search(r"^OWES_MAX = (\d+)", src or "", re.M)
    return int(m.group(1)) if m else None


_pin = _owes_max(_read("backend/harness_drilldown_coverage.py"))
check("I6 the ratchet was re-pinned DOWNWARD in the same PR and has never been pushed back up past "
      "this batch's 59", _pin is not None and _pin <= 59, _pin)
check("I6b NEGATIVE CONTROL — a pin ABOVE 59 is caught, so the ratchet cannot be loosened by "
      "editing the number", _owes_max("OWES_MAX = 60\n") > 59)
check("I6c NEGATIVE CONTROL — a LATER batch's lower pin passes without touching this file, which "
      "is the whole point of asserting the direction instead of the value",
      _owes_max("OWES_MAX = 59\n") <= 59 and _owes_max("OWES_MAX = 50\n") <= 59)
check("I6d NEGATIVE CONTROL — FAIL CLOSED: a ratchet with no readable pin is not a pass",
      _owes_max("# the pin was deleted\n") is None and _owes_max("OWES_MAX = many\n") is None)


print("\n" + "=" * 96)
print("PASS %d   FAIL %d" % (len(P), len(F)))
if F:
    print("\nFAILED:")
    for f in F:
        print("  - " + f)
sys.exit(1 if F else 0)
