"""Offline proof harness — A MULTI-STEP FLOW SOMEBODY CAN LEAVE HALFWAY HAS ONE HOME, and Employee
Setup dereferences it (index §CORE-51 / §STOREOPS-14). Drives the PURE `core/guided_workflow` and
`storeops/employee_setup_flow`, and reads the endpoint surface and HR's router as source. No live
database, no network, no framework import.

Run: `cd backend && python3 harness_guided_workflow.py`

WHAT WAS ASKED FOR (owner 2026-11-25): *"The way we have set up teh closing workflow , it is very
easy for a new person to start and then pick up where tehy left off, need similar for the Employee
set up in logical steps leading up to onboaring and then simlar workflow for scheduling andn simlar
workflow for runnign the payroll with paying attention to details"*

THE CLASS: three flows were asked for in one sentence, and answering "how far through is this, and
where do I pick it up" once per flow is three homes for one question. The third copy would drift
from the first two, and a flow a new person abandons halfway is exactly what must not drift.

Proves:
  A. PURE — the vocabulary: five statuses, a label for each, `OPEN_STATUSES` / `SETTLED_STATUSES`,
     and no database, table or hosting name in copy that reaches a screen (index §19.38).
  B. PURE — `status_of` is the ONE rule: done beats everything, skipped is never done, a named gap
     beats "in progress", and nothing entered reads not_started.
  C. PURE — the rail, the resume and the progress count: a step whose dependency is unsettled is
     never offered, a skipped dependency stops blocking, and a MISSING verdict is a refusal rather
     than a silent not_started (absence is never zero).
  D. BEHAVIOURAL, THE REGRESSION — the shape the owner described: a person set up halfway comes
     back to the step they stopped at, with what is missing named. Before this package, employee
     setup had no such answer at all.
  E. THE TENANT RULE (owner 2026-10-30) — `declared['roles']` is a REQUIRED argument with no house
     default; a tenant that declared none is TOLD so; one tenant's role word is not valid in
     another; and no tenant's vocabulary appears in either module's source.
  F. ONE HOME, DEREFERENCED — the $0.00 payroll question is ASKED of `clock_variance`, and the six
     onboarding stage words are ASKED of `hr/onboarding_stages`.
  G. WIRING LOCKS — these FAIL THE BUILD if the design un-wires:
       G1  the shared home imports no framework, no database client and no HTTP library;
       G2  the flow module contains no writer at all, and neither does the endpoint surface;
       G3  no module outside the home declares the five status words (the fourth-copy lock);
       G4  the flow spells no status string of its own — it dereferences `guided_workflow`;
       G5  `hr/router` holds no literal copy of the six stage words;
       G6  every step the flow declares names a screen that EXISTS in the frontend;
       G7  the endpoint publishes every status word, so the screen can invent none.
"""
import ast
import os
import re
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOME_PATH = "app/modules/core/guided_workflow.py"
FLOW_PATH = "app/modules/storeops/employee_setup_flow.py"
API_PATH = "app/modules/storeops/employee_setup_api.py"
STAGES_PATH = "app/modules/hr/onboarding_stages.py"
HR_PATH = "app/modules/hr/router.py"

HOME_SRC = open(HOME_PATH).read()
FLOW_SRC = open(FLOW_PATH).read()
API_SRC = open(API_PATH).read()
HR_SRC = open(HR_PATH).read()

from app.modules.core import guided_workflow as gw          # noqa: E402
from app.modules.hr import onboarding_stages as stages      # noqa: E402
from app.modules.storeops import clock_variance as cv       # noqa: E402
from app.modules.storeops import employee_setup_flow as flow  # noqa: E402

print("\n── A. the vocabulary ───────────────────────────────────────────────────────────────────")
check("A1 five statuses, in the order a flow moves through them",
      gw.STATUSES == ("not_started", "in_progress", "needs_input", "skipped", "done"))
check("A2 every status is worded for a person", set(gw.LABELS) == set(gw.STATUSES)
      and all(gw.LABELS[s].strip() for s in gw.STATUSES))
check("A3 OPEN_STATUSES are the ones still needing somebody",
      gw.OPEN_STATUSES == ("not_started", "in_progress", "needs_input")
      and gw.DONE not in gw.OPEN_STATUSES and gw.SKIPPED not in gw.OPEN_STATUSES)
check("A4 SETTLED_STATUSES settle a step either way",
      gw.SETTLED_STATUSES == (gw.DONE, gw.SKIPPED))
_banned = ("storeops.", "commcalc.", "supabase", "postgres", "vercel", "select ", "table")
check("A5 no label names a table, a schema or a host (§19.38)",
      not any(b in gw.LABELS[s].lower() for s in gw.STATUSES for b in _banned))
check("A6 'skipped' is worded as not-needed, never as done",
      "done" not in gw.LABELS[gw.SKIPPED].lower())

print("\n── B. status_of is the one rule ────────────────────────────────────────────────────────")
check("B1 nothing entered reads not_started", gw.status_of(gw.verdict()) == gw.NOT_STARTED)
check("B2 something entered reads in_progress",
      gw.status_of(gw.verdict(started=True)) == gw.IN_PROGRESS)
check("B3 a named gap beats in_progress",
      gw.status_of(gw.verdict(started=True, needs_input=("x",))) == gw.NEEDS_INPUT)
check("B4 done beats everything",
      gw.status_of(gw.verdict(done=True, started=True, needs_input=("x",))) == gw.DONE)
check("B5 skipped is its own answer and never done",
      gw.status_of(gw.verdict(skipped=True)) == gw.SKIPPED)
try:
    gw.verdict(needs_input=("",))
    _empty_refused = False
except ValueError:
    _empty_refused = True
check("B6 'needs input' with nothing named is refused", _empty_refused)
try:
    gw.verdict(needs_input="a sentence")
    _str_refused = False
except ValueError:
    _str_refused = True
check("B7 a bare string is not a list of reasons", _str_refused)

print("\n── C. the rail, the resume, the count ──────────────────────────────────────────────────")
_steps = gw.declare([gw.step("a", "A"), gw.step("b", "B", depends_on=("a",)),
                     gw.step("c", "C", required=False, depends_on=("b",))])
_v = {"a": gw.verdict(), "b": gw.verdict(started=True), "c": gw.verdict()}
check("C1 a step whose dependency is unsettled says what blocks it",
      {r["key"]: r["blocked_by"] for r in gw.rail(_steps, _v)}["b"] == ("a",))
check("C2 resume never sends somebody to a blocked step", gw.resume_key(_steps, _v) == "a")
_v2 = {"a": gw.verdict(done=True), "b": gw.verdict(skipped=False, done=True), "c": gw.verdict()}
check("C3 resume skips what is settled", gw.resume_key(_steps, _v2) == "c")
_v3 = {"a": gw.verdict(done=True), "b": gw.verdict(done=True), "c": gw.verdict(skipped=True)}
check("C4 an optional step need not be done for the flow to finish",
      gw.is_finished(_steps, _v3) and gw.blocking(_steps, _v3) == ())
check("C5 a skipped dependency stops blocking what follows",
      {r["key"]: r["blocked_by"] for r in gw.rail(
          gw.declare([gw.step("x", "X", required=False), gw.step("y", "Y", depends_on=("x",))]),
          {"x": gw.verdict(skipped=True), "y": gw.verdict()})}["y"] == ())
try:
    gw.rail(_steps, {"a": gw.verdict(), "b": gw.verdict()})
    _missing_refused = False
except ValueError:
    _missing_refused = True
check("C6-lock a MISSING verdict is a refusal, never a silent not_started", _missing_refused)
try:
    gw.rail(_steps, dict(_v, ghost=gw.verdict()))
    _ghost_refused = False
except ValueError:
    _ghost_refused = True
check("C7 a verdict for an undeclared step is refused", _ghost_refused)
try:
    gw.declare([gw.step("a", "A", depends_on=("b",)), gw.step("b", "B")])
    _cycle_possible = True
except ValueError:
    _cycle_possible = False
check("C8 a step may only depend on an EARLIER step, so a cycle cannot be expressed",
      not _cycle_possible)
try:
    gw.rail(gw.declare([gw.step("r", "R")]), {"r": gw.verdict(skipped=True)})
    _req_skippable = True
except ValueError:
    _req_skippable = False
check("C9 a required step cannot be skipped", not _req_skippable)

print("\n── D. the regression: a person set up halfway ──────────────────────────────────────────")
ROLES = ("Shift Lead", "Store Manager")
DECL = {"roles": ROLES, "pays_incentive_plans": True, "hours_from_clock": True}
HALFWAY = {"employee_id": "E777", "name": "A New Hire", "home_store": "S-1",
           "role": "Shift Lead", "is_active": True, "pay_basis": "hourly", "pay_rate": None}
HALF_FACTS = {"login": None, "plan_assignments": [], "onboarding": {}}
_d = flow.describe(HALFWAY, DECL, HALF_FACTS)
check("D1 six steps, in the order the owner confirmed",
      [s["key"] for s in _d["steps"]] == list(flow.STEP_ORDER))
check("D2 they come back to the step they stopped at", _d["resume"] == flow.STEP_PAY,
      detail=str(_d["resume"]))
_pay = {s["key"]: s for s in _d["steps"]}[flow.STEP_PAY]
check("D3 and it says what is missing, in money a person understands",
      _pay["status"] == gw.NEEDS_INPUT and any("$0.00" in r for r in _pay["needs_input"]),
      detail=str(_pay["needs_input"]))
check("D4 the flow is not finished while a required step is open", _d["status"] != gw.DONE)
check("D5 each step names the door where that work is done",
      all(s["screen"].startswith("/") for s in _d["steps"]))
_blank = flow.describe({"employee_id": "E0", "name": "", "home_store": "", "role": "",
                        "is_active": True}, DECL, HALF_FACTS)
check("D6 a brand-new record resumes at step one and counts nothing done",
      _blank["resume"] == flow.STEP_IDENTITY and _blank["progress"]["done"] == 0)
_full_facts = {"login": {"employee_id": "E777", "is_active": True, "role": "Shift Lead",
                         "store_code": "S-1", "store_codes": None, "market": None,
                         "last_login": "2026-11-20T00:00:00Z", "must_reset_password": False},
               "plan_assignments": [{"plan_id": "p", "plan_name": "Lead Tier"}],
               "onboarding": {"workflow_status": "active", "mandatory_total": 2,
                              "mandatory_done": 2}}
_done = flow.describe(dict(HALFWAY, pay_rate=18.5), DECL, _full_facts)
check("D7 a fully set-up person reads done with no resume",
      _done["status"] == gw.DONE and _done["resume"] is None)
check("D8 the board says WHERE people are getting stuck",
      flow.summarize([_d, _blank, _done])["stuck_at"][0]["count"] >= 1)

print("\n── E. the tenant rule (owner 2026-10-30) ───────────────────────────────────────────────")
try:
    flow.steps({"pays_incentive_plans": True, "hours_from_clock": True})
    _roles_optional = True
except ValueError:
    _roles_optional = False
check("E1-lock declared['roles'] is REQUIRED — a caller that has not asked the org cannot answer",
      not _roles_optional)
_none = flow.describe(dict(HALFWAY, pay_rate=18.0),
                      {"roles": (), "pays_incentive_plans": True, "hours_from_clock": True},
                      _full_facts)
_ident = {s["key"]: s for s in _none["steps"]}[flow.STEP_IDENTITY]
check("E2 a tenant that declared NO roles is told so, never measured against other words",
      _ident["status"] == gw.NEEDS_INPUT
      and any("no roles" in r.lower() for r in _ident["needs_input"]))
check("E3 and no other tenant's role word appears in what it says",
      not any(w in " ".join(_ident["needs_input"]).lower()
              for w in ("sales rep", "district manager", "associate", "operator")))
_other = flow.describe(dict(HALFWAY, pay_rate=18.0),
                       {"roles": ("Associate",), "pays_incentive_plans": True,
                        "hours_from_clock": True}, _full_facts)
_oident = {s["key"]: s for s in _other["steps"]}[flow.STEP_IDENTITY]
check("E4 one tenant's role is NOT valid in another tenant",
      _oident["status"] == gw.NEEDS_INPUT
      and any("Shift Lead" in r for r in _oident["needs_input"]))
_TENANT_WORDS = ("cellfonz", "luxelink", "vzone", "ny logistics", "boost", "total wireless",
                 "sales rep", "district manager", "store manager", "shift lead", "associate")
for _label, _src in (("the shared home", HOME_SRC), ("the flow", FLOW_SRC),
                     ("the endpoints", API_SRC)):
    check(f"E5 no tenant's own word appears in {_label} (RULE TWO)",
          not any(w in _src.lower() for w in _TENANT_WORDS),
          detail=str([w for w in _TENANT_WORDS if w in _src.lower()]))
check("E6 every tenant shape answers completely, and refuses nothing silently",
      all(len(flow.describe(dict(HALFWAY, role=r[0], pay_rate=18.0),
                            {"roles": r, "pays_incentive_plans": p, "hours_from_clock": h},
                            _full_facts)["steps"]) == 6
          for r in (("Rep",), ("A", "B", "C")) for p in (True, False) for h in (True, False)))

print("\n── F. one home, dereferenced ───────────────────────────────────────────────────────────")
check("F1 the $0.00 payroll question is ASKED of clock_variance, and the two agree",
      bool(cv.employees_without_rate([dict(HALFWAY, pay_rate=None)]))
      and {s["key"]: s for s in flow.describe(dict(HALFWAY, pay_rate=None), DECL,
                                              HALF_FACTS)["steps"]}[
          flow.STEP_PAY]["status"] == gw.NEEDS_INPUT)
check("F1b and a person WITH a rate is clear in both",
      cv.employees_without_rate([dict(HALFWAY, pay_rate=18.0)]) == []
      and {s["key"]: s for s in _done["steps"]}[flow.STEP_PAY]["status"] == gw.DONE)
check("F2 the six onboarding stage words come from their own home",
      flow.ONBOARDING_STATUSES == tuple(stages.WORKFLOW_STATUSES)
      and flow.ONBOARDING_SETTLED == tuple(stages.SETTLED_STATUSES))
check("F3 an onboarding word this platform does not know is REPORTED, not coerced",
      {s["key"]: s for s in flow.describe(
          dict(HALFWAY, pay_rate=18.0), DECL,
          dict(_full_facts, onboarding={"workflow_status": "halfway", "mandatory_total": 0,
                                        "mandatory_done": 0}))["steps"]}[
          flow.STEP_ONBOARDING]["status"] == gw.NEEDS_INPUT)

print("\n── G. the wiring locks ─────────────────────────────────────────────────────────────────")
_home_tree = ast.parse(HOME_SRC)
_home_imports = {n.module or "" for n in ast.walk(_home_tree) if isinstance(n, ast.ImportFrom)}
_home_imports |= {a.name for n in ast.walk(_home_tree) if isinstance(n, ast.Import)
                  for a in n.names}
_BANNED_IMPORTS = ("fastapi", "starlette", "requests", "httpx", "psycopg", "supabase",
                   "app.core.database", "pandas")
check("G1-lock the shared home imports no framework, client or HTTP library",
      not any(any(b in m for b in _BANNED_IMPORTS) for m in _home_imports),
      detail=str(sorted(_home_imports)))
check("G1b and it reads no file and opens no socket",
      not re.search(r"\bopen\(|socket|urlopen", HOME_SRC))

_WRITERS = (".insert(", ".update(", ".upsert(", ".delete(", "get_supabase")
check("G2-lock the flow module carries no writer and no client",
      not any(w in FLOW_SRC for w in _WRITERS),
      detail=str([w for w in _WRITERS if w in FLOW_SRC]))
check("G2b-lock the endpoint surface carries no writer — it is a projection and a set of doors",
      not any(w in API_SRC for w in (".insert(", ".update(", ".upsert(", ".delete(")))
check("G2c and every route on it is a GET",
      API_SRC.count("@router.") == API_SRC.count("@router.get("),
      detail=f"{API_SRC.count('@router.')} routes, {API_SRC.count('@router.get(')} GETs")

# G3 — THE FOURTH-COPY LOCK. Nobody outside the home may declare this five-word vocabulary.
_FIVE = ("not_started", "in_progress", "needs_input", "skipped", "done")
_copies = []
for _root, _dirs, _files in os.walk("app"):
    _dirs[:] = [d for d in _dirs if d != "__pycache__"]
    for _f in _files:
        if not _f.endswith(".py"):
            continue
        _p = os.path.join(_root, _f)
        if _p.replace("\\", "/") == HOME_PATH:
            continue
        _src = open(_p, encoding="utf-8").read()
        # A DECLARATION of the vocabulary: all five words as string literals assigned in one file.
        if all(re.search(rf'=\s*"{w}"|"{w}"\s*[,)\]]', _src) for w in _FIVE):
            _copies.append(_p)
check("G3-lock no module outside the home declares the five status words (the fourth-copy lock)",
      not _copies, detail=str(_copies))

_flow_tree = ast.parse(FLOW_SRC)
_flow_strings = {n.value for n in ast.walk(_flow_tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)}
check("G4-lock the flow spells no status word of its own — it dereferences the home",
      not (_flow_strings & set(gw.STATUSES)),
      detail=str(sorted(_flow_strings & set(gw.STATUSES))))
check("G4b and it imports the home",
      "from app.modules.core import guided_workflow" in FLOW_SRC)

_hr_tree = ast.parse(HR_SRC)
_hr_lists = [n for n in ast.walk(_hr_tree) if isinstance(n, ast.List)
             and len(n.elts) == len(stages.WORKFLOW_STATUSES)
             and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in n.elts)
             and [e.value for e in n.elts] == list(stages.WORKFLOW_STATUSES)]
check("G5-lock hr/router holds no literal copy of the six stage words", not _hr_lists)
check("G5b and it dereferences the stages home",
      "from app.modules.hr import onboarding_stages" in HR_SRC)

_FRONTEND = "../frontend/src/app/(platform)"
_missing_screens = []
for _s in flow.steps(DECL):
    _path = os.path.join(_FRONTEND, _s["screen"].lstrip("/"), "page.tsx")
    if not os.path.exists(_path):
        _missing_screens.append(f"{_s['key']} -> {_s['screen']}")
check("G6-lock every step names a screen that EXISTS — a door that is not there is not a door",
      not _missing_screens, detail=" | ".join(_missing_screens))

check("G7 the endpoint publishes every status word, so the screen can invent none",
      'for s in gw.STATUSES' in API_SRC)
check("G7b and it dereferences both homes rather than re-deriving either",
      "from app.modules.core import guided_workflow" in API_SRC
      and "from app.modules.storeops import employee_setup_flow" in API_SRC)
check("G7c the roster-visibility default is the platform's own (active-only, one spelling)",
      "roster_visibility" in API_SRC and "include_inactive" in API_SRC)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
