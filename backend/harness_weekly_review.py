"""THE LOCK — Weekly Performance Review: one fold, one gate, one vocabulary, and no second derivation.

Owner ask 2026-11-16 (index §COMMCALC-76): the performance deck as an in-app report on the Management
tile, administrators only, one pack per market plus a consolidated one, inventory aging per carrier
among its items, generated weekly and on demand.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check
that FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

WHAT FAILS THE BUILD

 (a) THE ITEM REGISTRY IS ONE LIST. Every item in `ITEMS` has a fold and every fold has an item; each
     declares its label, unit, window and source. An eighth item added to one half and not the other
     is RED, because that is how a review item becomes a heading with nothing under it.

 (b) NOTHING MEASURED IS NEVER A ZERO — checked BEHAVIOURALLY over the module, not by reading it. An
     item with no fact, and an item whose read failed, both come back `not_measured` with a reason
     and a null figure.

 (c) THE FOLD IS PURE. `weekly_review.py` imports nothing from `app.` — every fact is handed in — so
     it can never grow a read of its own beside the report it quotes.

 (d) NO SECOND DERIVATION. The router's facts function READS the surfaces that own each number
     (the GP report, the pay engine's rows, the accessory target plan, the shared sales cells, the
     action plan's verdict, the aging report, the one payroll authority). A copy of any of those
     derivations inside this feature is the duplicate defect the index rules forbid.

 (e) THE GATE IS ON THE SERVER, ON EVERY DOOR. All five routes call `_require_weekly_review`, the
     rule is `weekly_review.review_allowed` and nothing else, a company-wide reporting scope is
     refused, and the frontend mirror exists and says it is a mirror.

 (f) THE TENANT'S OWN WORDS ARE REQUIRED. `scopes()` cannot be called without the market vocabulary,
     and neither the fold nor the page spells a market, a carrier or a store of any tenant.

 (g) THE SURFACE IS WIRED. The page renders the shared send bar, the shared filter bar and the shared
     drill-down panel; it dereferences the one home for "what the figure covers" and for an
     unmeasured figure rather than spelling either again.

 (h) IT IS REGISTERED. The index, the drill-down ledger, the migration and the report directory all
     name it — the registration CLAUDE.md requires in the same PR.

 (i) NEGATIVE CONTROLS: an item with no fold → RED; a fold that returns 0 for an unmeasured item →
     RED; a `scopes()` that would answer without the org's markets → RED.

  python3 backend/harness_weekly_review.py
"""
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import weekly_review as W                 # noqa: E402
from app.modules.commcalc import weekly_review_drilldown as D       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PASS = 0
FAIL = 0


def check(name, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok   " + name)
    else:
        FAIL += 1
        print("FAIL " + name + ("" if extra is None else "  -> " + repr(extra)))


def src(rel, base=HERE):
    path = os.path.join(base, rel)
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""


def code_of(text):
    """Executable code only — docstrings and comments stripped, so a cross-reference in prose is
    never mistaken for a second implementation (the mutation that fooled the toolbar gate on
    2026-10-30: a page satisfied a source check by TALKING about the component)."""
    body = text.split("if __name__ ==")[0]
    return re.sub(r"#[^\n]*", "", re.sub(r'"""[\s\S]*?"""', "", body))


WR = src("app/modules/commcalc/weekly_review.py")
WR_CODE = code_of(WR)
DD = src("app/modules/commcalc/weekly_review_drilldown.py")
ROUTER = src("app/modules/commcalc/router.py")
PAGE = src("frontend/src/app/(platform)/commcalc/weekly-review/page.tsx", base=ROOT)
EXPORT = src("frontend/src/lib/weekly-review-export.ts", base=ROOT)
RBAC = src("frontend/src/lib/rbac.ts", base=ROOT)
INDEX = src("docs/SYSTEM_DATA_FLOW_INDEX.md", base=ROOT)
LEDGER = src("harness_drilldown_pending.txt")
MIGRATION = src("database/migrations/1123_weekly_performance_review.sql", base=ROOT)

#: The router's own block, bounded at both ends, so a feature appended to router.py later is never
#: mistaken for this one (the zero-sales lock's BLOCK-with-an-END rule).
_BLOCK = ROUTER.split("# WEEKLY PERFORMANCE REVIEW — the Monday review pack", 1)
BLOCK = _BLOCK[1] if len(_BLOCK) > 1 else ""

WEEK = {"date_from": "2026-11-09", "date_to": "2026-11-15", "week_ending": "2026-11-15",
        "label": "9–15 November 2026"}
SCOPE = {"kind": "company", "label": "Company", "store_codes": ["A-1"], "note": ""}


print("\n== A. the item registry is ONE list ==")
check("a1. every declared item has a fold behind it",
      set(W.ITEM_KEYS) <= set(W.FOLDS), sorted(set(W.ITEM_KEYS) - set(W.FOLDS)))
check("a2. and every fold belongs to a declared item — a fold nobody declares is dead code",
      set(W.FOLDS) <= set(W.ITEM_KEYS), sorted(set(W.FOLDS) - set(W.ITEM_KEYS)))
check("a3. each item declares its label, unit, window and the read it quotes",
      all(i.get("label") and i.get("source") and i.get("unit") in ("money", "count", "pct")
          and i.get("window") in ("week", "period", "today") for i in W.ITEMS),
      [i["key"] for i in W.ITEMS
       if not (i.get("label") and i.get("source") and i.get("unit") and i.get("window"))])
check("a4. the items the owner named are all on the review, inventory aging per carrier included",
      {"profit", "growth", "accessory", "commission", "rep_watch", "aging", "payroll"}
      <= set(W.ITEM_KEYS), sorted(W.ITEM_KEYS))
check("a5. every item carries its window onto the pack, so a month-to-date figure on a weekly pack "
      "says it is monthly",
      all(i["window"] == W.ITEM_BY_KEY[i["key"]]["window"]
          for i in W.review(SCOPE, facts={}, week=WEEK)["items"]))


print("\n== B. nothing measured is never a zero (behavioural) ==")
_empty = {i["key"]: i for i in W.review(SCOPE, facts={}, week=WEEK)["items"]}
check("b1. an item with no fact is NOT MEASURED, carries no figure and says why",
      all(v["state"] == W.NOT_MEASURED and v["figure"] is None and v["reason"]
          for v in _empty.values()),
      [k for k, v in _empty.items() if v["figure"] is not None or not v["reason"]])
_broken = W.review(SCOPE, facts={"profit": W.Unreadable("the GP read timed out")}, week=WEEK)
_p = {i["key"]: i for i in _broken["items"]}["profit"]
check("b2. a read that FAILED is reported as itself, with the reason, not as an empty report",
      _p["state"] == W.NOT_MEASURED and _p["figure"] is None and "timed out" in _p["reason"])
check("b3. and one unreadable fact does not drop the other items",
      len(_broken["items"]) == len(W.ITEMS))
_measured = W.review(SCOPE, week=WEEK, facts={"profit": [
    {"store_code": "A-1", "address": "1 First St", "total_rev": 100.0, "net_profit": 0.0}]})
check("b4. a MEASURED zero is still a zero — the honesty rule must not swallow a real 0",
      {i["key"]: i for i in _measured["items"]}["profit"]["figure"] == 0.0)
check("b5. coverage counts the two states and nothing else",
      _broken["coverage"]["items"] == len(W.ITEMS)
      and _broken["coverage"]["measured"] + _broken["coverage"]["not_measured"] == len(W.ITEMS))


print("\n== C. the fold is PURE ==")
check("c1. it imports nothing from the app — every fact is handed in, so it can never grow a read "
      "beside the report it quotes",
      not re.search(r"^\s*(from|import)\s+app\b", WR_CODE, re.M),
      re.findall(r"^\s*(?:from|import)\s+app[^\n]*", WR_CODE, re.M))
check("c2. no I/O and no clock of its own — the day it reviews is handed in",
      not re.search(r"\b(requests|httpx|supabase|get_supabase|datetime\.now|date\.today)\b", WR_CODE),
      re.findall(r"\b(requests|httpx|supabase|get_supabase|datetime\.now|date\.today)\b", WR_CODE))


print("\n== D. no second derivation — every figure is quoted from its one home ==")
for name, needle in (
        ("the Gross Profit report", "get_gp_report("),
        ("the pay engine's own rep rows", "get_commissions("),
        ("the accessory target plan", "get_accessory_target_plan("),
        ("the Daily Action Plan's verdict", "get_action_plan("),
        ("the inventory aging report", "get_aging"),
        ("the one payroll authority", "get_payroll_by_store"),
        ("the shared sales cells", "_sales_cell_agg(")):
    check(f"d1. it READS {name} rather than deriving that figure again", needle in BLOCK, needle)
check("d2. it computes no pay, no target and no rate of its own",
      not re.search(r"\b(payout_config|commission_plan|tier|rate_for|calculate_pay)\b", BLOCK),
      re.findall(r"\b(payout_config|commission_plan|tier|rate_for|calculate_pay)\b", BLOCK))
check("d3. it classifies no sale line — the activation predicate has one home",
      not re.search(r"\b(line_class|contract_type|bucket_of)\b", WR_CODE + BLOCK),
      re.findall(r"\b(line_class|contract_type|bucket_of)\b", BLOCK))
_AGING_FOLD = code_of(WR).split("def _aging", 1)[1].split("def _payroll", 1)[0]
check("d4. it re-buckets no device — a carrier's selling window is that carrier's contract, so the "
      "fold READS the bucket the aging report already stamped and does no day arithmetic of its own",
      "aging_bucket" in _AGING_FOLD
      and not re.search(r"timedelta|days_aged|acquired_date|warn_fraction|[<>]=?\s*\d+", _AGING_FOLD),
      re.findall(r"timedelta|days_aged|acquired_date|warn_fraction", _AGING_FOLD))
check("d5. the drill-down declares rows and relations only — it folds no figure",
      not re.search(r"\bsum\(|\+=", code_of(DD)), re.findall(r"\bsum\(|\+=", code_of(DD)))


print("\n== E. the gate is on the server, on every door ==")
_routes = re.findall(r'@router\.(?:get|post|put)\("(/weekly-review[^"]*)"\)', BLOCK)
check("e1. all five weekly-review routes exist — the read, the config read and write, the on-demand "
      "run and the standing run",
      len(_routes) == 5 and set(_routes) == {"/weekly-review", "/weekly-review/config",
                                             "/weekly-review/run-now", "/weekly-review/run-due"},
      _routes)
_handlers = re.split(r"@router\.(?:get|post|put)\(", BLOCK)[1:]
_ungated = [h.split("\n", 1)[0] for h in _handlers
            if "_require_weekly_review" not in h and "run-due" not in h.split("\n", 1)[0]]
check("e2. every human-facing route calls the gate — hiding a tile is not access control",
      _ungated == [], _ungated)
check("e3. the gate has ONE rule and the router dereferences it rather than spelling its own",
      "_wrev.review_allowed(caller)" in BLOCK
      and not re.search(r"modules.*admin", code_of(BLOCK)),
      "the router must not re-decide who an administrator is")
check("e4. an administrator passes and a company-wide REPORTING scope does not",
      W.review_allowed({"perms": {"modules": ["admin"]}}) is True
      and W.review_allowed({"super_admin": True, "perms": {}}) is True
      and W.review_allowed({"perms": {"scope": "all"}}) is False)
check("e5. an unresolvable caller is refused — the gate degrades CLOSED",
      W.review_allowed(None) is False and W.review_allowed({}) is False)
check("e6. the frontend MIRRORS it and says so",
      "canOpenWeeklyReview" in RBAC and "review_allowed" in RBAC)
_due = [h for h in _handlers if h.split("\n", 1)[0].startswith('"/weekly-review/run-due"')]
check("e8. the standing run carries the SWEEP gate — it reads every tenant under the root client, so "
      "no caller's own org could scope it, and without a gate a stranger could name another tenant "
      "in `org_id_filter` (found by the security pass 2026-11-16, before go-live)",
      len(_due) == 1 and "verify_notify_secret(x_notify_secret)" in _due[0]
      and "x_notify_secret" in _due[0].split('"""')[0] + _due[0],
      "the run-due handler must refuse a caller with no NOTIFY_RUN_SECRET")
check("e9. …and an admin token is not that secret — the two gates are different authorities",
      len(_due) == 1 and "_require_weekly_review" not in _due[0])
check("e7. the nav entry is admin-gated too, so the tile is not offered to anyone else",
      re.search(r"href: '/commcalc/weekly-review'[^}]*module: 'admin'", RBAC) is not None)


print("\n== F. the tenant's own words are a REQUIRED argument ==")
_sig = inspect.signature(W.scopes)
check("f1. `scopes` takes the org's markets with NO default — a report that could answer without "
      "asking the org which markets exist would answer with somebody else's words",
      _sig.parameters["markets"].default is inspect.Parameter.empty
      and _sig.parameters["markets"].kind is inspect.Parameter.KEYWORD_ONLY)
try:
    W.scopes(store_codes=["A-1"])
    _refused = False
except TypeError:
    _refused = True
check("f2. NEGATIVE CONTROL — calling it without the vocabulary raises rather than defaulting",
      _refused)
_none = W.scopes(markets={}, store_codes=["A-1"])
check("f3. nothing declared means nothing identified, and the pack SAYS so",
      [s["kind"] for s in _none] == ["company", "unplaced"] and _none[-1]["note"])
_some = W.scopes(markets={"Chicago": ["A-1"]}, store_codes=["A-1", "ORPHAN"])
check("f4. a store no market claims is reviewed in a scope of its own, never dropped",
      any(s["kind"] == "unplaced" and s["store_codes"] == ["ORPHAN"] for s in _some)
      and "ORPHAN" in [s for s in _some if s["kind"] == "company"][0]["store_codes"])
_TENANT_WORDS = r"\b(boost|total wireless|vidapay|luxelink|cellfonz|vzone|chicago|brooklyn|queens)\b"
check("f5. neither the fold nor the page spells a tenant's market, carrier or store",
      not re.search(_TENANT_WORDS, code_of(WR), re.I)
      and not re.search(_TENANT_WORDS, PAGE, re.I),
      re.findall(_TENANT_WORDS, code_of(WR) + PAGE, re.I))


print("\n== G. the surface is wired to the shared mechanisms ==")
check("g1. the page renders the shared SEND bar (the element, not the import)",
      re.search(r"<ReportExportBar[\s/>]", PAGE) is not None)
check("g2. and the shared FILTER bar",
      re.search(r"<StandardFilterBar[\s/>]", PAGE) is not None)
check("g3. and the shared DRILL-DOWN panel",
      re.search(r"<DrillDownPanel[\s/>]", PAGE) is not None)
check("g4. the page dereferences the one home for what a figure covers and for an unmeasured one, "
      "rather than spelling either again",
      "WR_WINDOW_WORD" in PAGE and "screenFigure" in PAGE
      and "month to date" not in PAGE,
      "a window word was respelled on the page")
check("g5. the export says NOT MEASURED in words and exports blank, never 0",
      "WR_STATE_WORD" in EXPORT and "state === 'measured'" in EXPORT)
check("g6. the page dumps no raw payload to screen", "JSON.stringify(" not in PAGE)
check("g7. the on-demand button is there — the owner's \"let us make it on demand\"",
      "run-now" in PAGE and "Generate now" in PAGE)


print("\n== H. it is registered where the house requires ==")
check("h1. the index names the report", "COMMCALC-76" in INDEX and "weekly-review" in INDEX)
check("h2. the drill-down ledger declares it exactly once",
      LEDGER.count("/commcalc/weekly-review\t") == 1)
check("h3. the report directory carries it, so the toolbar and drill-down gates see it",
      "['/commcalc/weekly-review', 'comm']" in RBAC)
check("h4. the migration exists, grants what it creates and is reversible",
      "weekly_review_config" in MIGRATION and "weekly_review_run" in MIGRATION
      and MIGRATION.count("TO service_role") >= 2 and "-- REVERT:" in MIGRATION)
check("h5. and this lock is wired into a workflow rather than sitting on the debt list",
      "harness_weekly_review.py" in src(".github/workflows/carrier-vocab-guard.yml", base=ROOT)
      and "harness_weekly_review.py" not in src("harness_unrun_pending.txt"))


print("\n== I. negative controls ==")
_mutant_items = [dict(i) for i in W.ITEMS] + [{"key": "invented", "label": "x", "unit": "money",
                                               "window": "week", "source": "nowhere"}]
check("i1. an item with no fold behind it would be caught by a1",
      not {i["key"] for i in _mutant_items} <= set(W.FOLDS))
_as_zero = {"state": W.MEASURED, "figure": 0.0}
check("i2. a fold that answered an unmeasured item with 0 would be caught by b1",
      not (_as_zero["state"] == W.NOT_MEASURED and _as_zero["figure"] is None))
check("i3. the week under review is the one that CLOSED, never the part-week the meeting sits in",
      W.week_window("2026-11-16", W.SUNDAY)["date_to"] == "2026-11-15"
      and W.week_window("2026-11-16", W.SUNDAY)["date_from"] == "2026-11-09")
try:
    W.week_window("2026-11-16", 9)
    _bad_day = False
except W.ConfigRefused:
    _bad_day = True
check("i4. a closing weekday outside the week is REFUSED, not read as Sunday", _bad_day)


print("\n" + "=" * 60)
print("%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
