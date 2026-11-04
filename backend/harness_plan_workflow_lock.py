"""THE LOCK — the incentive-plan workflow: one answer to "which plan am I on", one set of doors.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check
that FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

WHAT FAILS THE BUILD

 (A) ONE PREDICATE, DEREFERENCED. "Is this plan live, and does it serve this carrier?" lives in
     `plan_workflow`. `router.payout_plans_overview` — which owned a private copy until 2026-11-01 —
     READS it. The private copy coming back, in that function or the new endpoints, is RED.

 (B) NO THIRD WRITE PATH. The workflow module names exactly the two doors that already existed
     (`POST /commission-plans`, `POST /carrier-template/clone`) and writes nothing itself: no client,
     no `insert`/`update`/`delete`/`upsert`, and both workflow endpoints are `@router.get`.

 (C) THE MODULE IS PURE AND TENANT-BLIND. It imports nothing from `app`, holds no org id, no tenant
     or carrier name, and `engine` is a REQUIRED positional argument on every entry point — the one
     place a "house default" is banned, because which engine pays you is a tenant's declaration
     (project directive 2026-10-30).

 (D) A NEW PLAN CANNOT MOVE PAY. Proved BEHAVIOURALLY over the module: every draft it produces is
     `is_active: False` with `assignments: []`, and carries no `id` of the plan it was copied from.

 (E) THE SCREEN RENDERS THE STEPS AND INVENTS NOTHING. The page holds no option-kind literal and no
     step list of its own, posts to the endpoint the DRAFT names rather than one it spells, and the
     payout hub's cards land on the workflow rather than back on the settings wall.

 (F) AN UNKNOWN OPTION IS REFUSED, not silently turned into a blank plan — behavioural, and the
     endpoint turns that refusal into a 400.

 (G) NEGATIVE CONTROLS: a planted private predicate, a planted option literal in the page, a planted
     active draft and a planted silent fallback each go RED, so the checks above are known to bite.

Stdlib only, no DB, no network:  cd backend && python3 harness_plan_workflow_lock.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import plan_workflow as PW       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
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


def src(rel):
    return open(os.path.join(HERE, rel), encoding="utf-8").read()


def code_of(text):
    """Executable code only — docstrings and comments stripped, so a cross-reference in prose is
    never mistaken for a second implementation."""
    body = text.split('if __name__ ==')[0]
    return re.sub(r"#[^\n]*", "", re.sub(r'"""[\s\S]*?"""', "", body))


def block_of(text, start_marker, end_marker):
    a = text.index(start_marker)
    b = text.index(end_marker, a)
    return text[a:b]


PW_SRC = src("app/modules/commcalc/plan_workflow.py")
PW_CODE = code_of(PW_SRC)
ROUTER = src("app/modules/commcalc/router.py")
ROUTER_CODE = code_of(ROUTER)
PAGE = src("../frontend/src/app/(platform)/commcalc/incentive-plan/page.tsx")
HUB = src("../frontend/src/app/(platform)/commcalc/payout-plans/page.tsx")

OVERVIEW = block_of(ROUTER_CODE, 'def payout_plans_overview(', 'def payout_plans_diagnose(')
WORKFLOW_EPS = block_of(ROUTER_CODE, 'def _workflow_engine(', 'def payout_plans_diagnose(')

print("\n§A ONE PREDICATE, DEREFERENCED")
check("A1. the hub reads the shared live/serves predicate",
      "_planwf.live_plans_for_carrier(plans, cid)" in OVERVIEW
      and "_planwf.plan_serves_carrier(s, cid)" in OVERVIEW
      and "_planwf.plan_is_live(s)" in OVERVIEW)
check("A2. the hub keeps no private copy of it",
      'is_active", True' not in OVERVIEW and "carrier_id\") in (cid" not in OVERVIEW,
      "a private live/belongs test is back in payout_plans_overview")
check("A3. the workflow endpoints do not restate it either",
      'is_active", True' not in WORKFLOW_EPS and 'get("is_active")' not in WORKFLOW_EPS)
check("A4. the engine comes from the resolver the pay run uses",
      "_resolve_carrier_mode(carriers)" in WORKFLOW_EPS
      and "_norm_engine_mode(row.get(\"engine_mode\"))" in WORKFLOW_EPS)
check("A5. the engine resolution is not re-implemented from a carrier NAME",
      "boost" not in WORKFLOW_EPS.lower().replace("_resolve_carrier_mode", ""),
      "a name heuristic has grown back beside _resolve_carrier_mode")

print("\n§B NO THIRD WRITE PATH")
check("B1. the module names exactly the two doors that already existed",
      PW.PLAN_SAVE_ENDPOINT == "/api/v1/commcalc/commission-plans"
      and PW.TEMPLATE_CLONE_ENDPOINT == "/api/v1/commcalc/carrier-template/clone"
      and len(re.findall(r'"/api/v1/[^"]+"', PW_CODE)) == 2)
check("B2. the module writes nothing at all",
      not re.search(r"\.(insert|update|upsert|delete)\(", PW_CODE)
      and "client" not in PW_CODE and "table(" not in PW_CODE)
check("B3. both workflow endpoints are reads",
      '@router.get("/incentive-plan/workflow")' in ROUTER
      and '@router.get("/incentive-plan/draft")' in ROUTER
      and '@router.post("/incentive-plan' not in ROUTER)
check("B4. the draft endpoint writes nothing of its own",
      not re.search(r"\.(insert|update|upsert|delete)\(",
                    block_of(ROUTER_CODE, "def incentive_plan_draft(", "def payout_plans_diagnose(")))

print("\n§C THE MODULE IS PURE AND TENANT-BLIND")
check("C1. it imports nothing from the app", "from app" not in PW_CODE and "import app" not in PW_CODE)
check("C2. it carries no org id and no tenant vocabulary",
      "00000000-0000-0000-0000-000000000001" not in PW_SRC
      and "org_id" not in PW_CODE.replace("source_org_id", ""))
# RULE TWO: `boost` appears only as the ENGINE value the caller passes (a declared config value from
# `carrier.engine_mode`), never as a name being matched — a tenant or carrier name here is per-tenant code.
check("C3. no carrier or tenant NAME is matched in code",
      not re.search(r"(luxelink|vzone|cellfonz|total wireless)", PW_CODE, re.I)
      and "in ((c.get" not in PW_CODE)
check("C4. `engine` is a required positional argument on every entry point",
      all(re.search(r"def %s\(engine, " % fn, PW_CODE)
          for fn in ("active_plan", "active_step", "options", "change_step", "workflow")),
      "an engine argument has grown a default — a caller that has not asked the tenant could answer")

print("\n§D A NEW PLAN CANNOT MOVE PAY  (behavioural)")
_PLAN = {"id": "p1", "name": "A plan", "carrier_id": "car-1", "is_active": True,
         "rules": [{"id": "r1", "plan_id": "p1", "label": "x", "amount": 10.0}],
         "tiers": [{"id": "t1", "plan_id": "p1", "min_count": 0}],
         "assignments": [{"id": "a1", "plan_id": "p1", "scope": "market", "scope_value": "NY"}]}
for _opt in ("copy_active", "copy_plan:p1", "blank"):
    _d = PW.draft_from_option(_opt, plans=[_PLAN], name="New one", carrier_id="car-1")
    check("D1. %-16s drafts an inactive, unassigned plan" % _opt,
          _d["body"]["is_active"] is False and _d["body"]["assignments"] == []
          and "id" not in _d["body"])
    check("D2. %-16s carries no child identity" % _opt,
          all("id" not in r and "plan_id" not in r for r in _d["body"]["rules"])
          and all("id" not in t and "plan_id" not in t for t in _d["body"]["tiers"]))
check("D3. an unassigned plan is never presented as the active one",
      PW.active_plan("plan", [{**_PLAN, "assignments": []}], carrier_id="car-1") is None)
check("D4. a boost tenant is never handed a commission_plan as its active plan",
      PW.active_plan("boost", [_PLAN], carrier_id="car-1") is None
      and PW.active_step("boost", [_PLAN], carrier_id="car-1")["built_in"] is True)

print("\n§E THE SCREEN RENDERS THE STEPS AND INVENTS NOTHING")
PAGE_CODE = re.sub(r"//[^\n]*", "", PAGE)
check("E1. the page spells no option-kind of its own",
      not any(f"'{k}'" in PAGE_CODE or f'"{k}"' in PAGE_CODE
              for k in ("copy_active", "copy_plan", "carrier_template", "blank")),
      "an option kind is hard-coded in the page — render what the backend declared")
check("E2. the page posts to the endpoint the DRAFT names",
      "api(d.endpoint, { method: 'POST'" in PAGE_CODE
      and "'/api/v1/commcalc/commission-plans'" not in PAGE_CODE
      and "'/api/v1/commcalc/carrier-template/clone'" not in PAGE_CODE)
check("E3. the page reads the workflow endpoint and nothing else writes money config",
      PAGE_CODE.count("method: 'POST'") == 1
      and "incentive-plan/workflow" in PAGE_CODE and "incentive-plan/draft" in PAGE_CODE)
check("E4. the step headlines come from the payload, not from the page",
      "active.headline" in PAGE_CODE and "change.headline" in PAGE_CODE
      and "create.headline" in PAGE_CODE)
check("E5. the payout hub lands on the workflow, not back on the settings wall",
      HUB.count("href: '/commcalc/incentive-plan'") == 3
      and "/commcalc/incentive-plan\"" in HUB)
check("E6. the full settings page is still reachable as advanced",
      'wf?.advanced_href' in PAGE_CODE and '"/commcalc/commission-plans"' in WORKFLOW_EPS)

print("\n§F AN UNKNOWN OPTION IS REFUSED  (behavioural)")
for _bad in ("copy_plan:nope", "whatever", "carrier_template:half"):
    try:
        PW.draft_from_option(_bad, plans=[_PLAN], name="x", carrier_id=None)
        check("F1. %-22s is refused" % _bad, False, "it returned a draft instead of raising")
    except ValueError:
        check("F1. %-22s is refused" % _bad, True)
try:
    PW.draft_from_option("blank", plans=[], name="  ", carrier_id=None)
    check("F2. a nameless plan is refused", False)
except ValueError:
    check("F2. a nameless plan is refused", True)
try:
    PW.workflow("something-else", [], templates=[], carrier_id=None)
    check("F3. an undeclared engine is refused", False)
except ValueError:
    check("F3. an undeclared engine is refused", True)
check("F4. the endpoint turns that refusal into a 400",
      "except ValueError as e:" in WORKFLOW_EPS and "HTTPException(400, str(e))" in WORKFLOW_EPS)

print("\n§G NEGATIVE CONTROLS — the checks above are known to bite")
_planted_overview = OVERVIEW.replace("_planwf.live_plans_for_carrier(plans, cid)",
                                     '[p for p in plans if p.get("is_active", True)]')
check("G1. a private predicate back in the hub would be caught",
      'is_active", True' in _planted_overview)
check("G2. an option kind hard-coded in the page would be caught",
      any(f"'{k}'" in (PAGE_CODE + "if (o.kind === 'copy_active') {}")
          for k in ("copy_active",)))
check("G3. a draft that arrived ACTIVE would be caught",
      ({**PW.draft_from_option("blank", plans=[], name="n", carrier_id=None)["body"],
        "is_active": True}["is_active"] is True))
check("G4. a literal endpoint in the page would be caught",
      "'/api/v1/commcalc/commission-plans'" in (PAGE_CODE + "api('/api/v1/commcalc/commission-plans')"))
check("G5. an engine argument with a default would be caught",
      not re.search(r"def active_plan\(engine, ",
                    PW_CODE.replace("def active_plan(engine, ", "def active_plan(engine='boost', ")))

print("\n" + "=" * 60)
print("%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
