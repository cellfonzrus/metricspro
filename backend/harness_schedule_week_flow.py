"""Offline proof harness — SCHEDULING A STORE-WEEK IS A FLOW SOMEBODY CAN LEAVE HALFWAY, and it
dereferences the shared home rather than copying it (index §STOREOPS-15 / §CORE-51). Drives the PURE
`storeops/schedule_week_flow` and reads the endpoint surface and the notify report builder as
source. No live database, no network, no framework import.

Run: `cd backend && python3 harness_schedule_week_flow.py`

WHAT WAS ASKED FOR (owner 2026-11-25): *"...need similar for the Employee set up in logical steps
leading up to onboaring and then simlar workflow for scheduling andn simlar workflow for runnign the
payroll with paying attention to details"*, said of the daily-closing workflow a new person can
start and come back to. The five steps were confirmed the same evening.

THE CLASS, stated once: a flow a new person abandons halfway must be able to say how far through it
is, and three flows asked for in one sentence means that answer lives in ONE home or the third copy
drifts. This harness is the second caller's half of that lock.

Proves:
  A. THE SHAPE — five steps in the order the work happens, only "tell the team" optional, each
     dependency pointing backwards, each step naming a screen that already does that work.
  B. ★ THE TENANT'S OWN WEEK (owner 2026-10-30) — `week_start_dow` is a REQUIRED argument with no
     house Monday behind it, a bad value is refused, and the SAME date lands in different weeks for
     two tenants that start their week on different days.
  C. THE STEPS, one at a time — a missing budget, an undecided request, an empty rota, over budget
     without an approval, somebody double-booked, somebody on approved leave, an unsent rota.
  D. BEHAVIOURAL, THE REGRESSION — the shape the owner described: a week left half-laid-out comes
     back to the step it stopped at, with what is missing named.
  E. ★ AN UNCOVERED DAY IS REPORTED, NOT JUDGED — nothing declares a store's opening days, so the
     step names the empty days and SAYS it is not calling them gaps; declare `open_dows` and the
     same step refuses, with no code change.
  F. ONE HOME, DEREFERENCED — a week's hours are ASKED of `schedule_hours`, and the week start is
     ASKED of the tenant's declaration through the reader that already answers it.
  G. WIRING LOCKS — these FAIL THE BUILD if the design un-wires:
       G1  the flow imports no framework, no database client and no HTTP library;
       G2  the flow contains no writer at all, and neither does the endpoint surface — every route
           on it is a GET;
       G3  the flow spells no status string of its own — it dereferences `guided_workflow`;
       G4  no second module declares the five step keys (the second-copy lock);
       G5  ★ NOTHING SNAPS TO A HOUSE MONDAY — no module that buckets a schedule week may compute
           it with `weekday()` arithmetic of its own instead of the tenant's declared week start;
       G6  every step the flow declares names a screen that EXISTS in the frontend;
       G7  the endpoint publishes every status word, so the screen can invent none;
       G8  the flow invents no "published" shift state — the last step reads a recorded SEND.
"""
import ast
import os
import re
import sys
from datetime import date

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


FLOW_PATH = "app/modules/storeops/schedule_week_flow.py"
API_PATH = "app/modules/storeops/schedule_week_api.py"
REPORT_PATH = "app/modules/notify/report_registry.py"

FLOW_SRC = open(FLOW_PATH).read()
API_SRC = open(API_PATH).read()
REPORT_SRC = open(REPORT_PATH).read()

from app.modules.core import guided_workflow as gw             # noqa: E402
from app.modules.storeops import schedule_hours as sh          # noqa: E402
from app.modules.storeops import schedule_week_flow as flow    # noqa: E402

MON = {"week_start_dow": 0}
THU = {"week_start_dow": 3}
OPEN6 = {"week_start_dow": 0, "open_dows": (0, 1, 2, 3, 4, 5)}
WEEK = "2026-11-23"


def _shift(eid, day, start="09:00", end="17:00", store="B-117"):
    return {"employee_id": eid, "employee_name": eid, "shift_date": day, "store_code": store,
            "start_time": start, "end_time": end}


def _facts(**over):
    f = {"store_code": "B-117", "week_start": WEEK, "budget": {"weekly_hours": 120},
         "time_off": [], "swaps": [], "other_shifts": [], "override": None,
         "shifts": [_shift("E1", "2026-11-23"), _shift("E2", "2026-11-24")],
         "sends": [{"status": "sent", "target": "team@x.test",
                    "created_at": "2026-11-22T18:00:00Z"}]}
    f.update(over)
    return f


print("\n── A. the shape of the flow ────────────────────────────────────────────────────────────")
STEPS = flow.steps(MON)
check("A1 five steps, in the order the work happens",
      [s["key"] for s in STEPS] == ["budget", "availability", "shifts", "coverage", "tell"])
check("A2 telling the team is the only optional step",
      [s["key"] for s in STEPS if not s["required"]] == ["tell"])
check("A3 every dependency points at a step declared BEFORE it, so a cycle is inexpressible",
      all(set(s["depends_on"]) <= set([x["key"] for x in STEPS[:i]])
          for i, s in enumerate(STEPS)))
check("A4 every step is worded with WHY it is there, for a person who has never done this",
      all(s["why"].strip() for s in STEPS))
check("A5 the careful checks wait on the shifts, and the shifts wait on who is available",
      {s["key"]: s["depends_on"] for s in STEPS}["coverage"] == ("shifts",)
      and {s["key"]: s["depends_on"] for s in STEPS}["shifts"] == ("availability",))

print("\n── B. the tenant's own week (owner 2026-10-30) ─────────────────────────────────────────")
_refused = []
for bad in ({}, None, {"week_start_dow": 7}, {"week_start_dow": -1}, {"week_start_dow": "mon"},
            {"week_start_dow": None}):
    try:
        flow.steps(bad)
        _refused.append(repr(bad))
    except ValueError:
        pass
check("B1-lock a caller that has not asked the org its week start CANNOT answer", not _refused,
      detail=" | ".join(_refused))
check("B2 the same date is a different week for two tenants",
      flow.week_days("2026-11-25", MON)[0] == date(2026, 11, 23)
      and flow.week_days("2026-11-25", THU)[0] == date(2026, 11, 19))
check("B3 a date anywhere inside the week names the same week",
      {flow.week_days(d, MON)[0] for d in ("2026-11-23", "2026-11-26", "2026-11-29")}
      == {date(2026, 11, 23)})
check("B4 the week is seven consecutive days",
      len(flow.week_days(WEEK, THU)) == 7
      and (flow.week_days(WEEK, THU)[-1] - flow.week_days(WEEK, THU)[0]).days == 6)
check("B5 no house vocabulary of any tenant appears in the flow",
      not any(w in FLOW_SRC.lower() for w in ("boost", "luxelink", "cellfonz", "verizon")))

print("\n── C. the five steps, one at a time ───────────────────────────────────────────────────")
check("C1 no hours budget asks for one by name",
      gw.status_of(flow._budget_verdict(None)) == gw.NEEDS_INPUT
      and "No weekly hours budget" in flow._budget_verdict(None)["needs_input"][0])
check("C2 a zero budget is named, not read as 'no budget'",
      "zero" in flow._budget_verdict({"weekly_hours": 0})["needs_input"][0])
_av = flow._availability_verdict([{"employee_id": "E1", "status": "pending"}], [])
check("C3 an undecided time-off request holds the week up",
      gw.status_of(_av) == gw.NEEDS_INPUT and "awaiting a decision" in _av["needs_input"][0])
check("C4 an undecided cover swap holds it up too",
      "cover swap" in flow._availability_verdict([], [{"status": "pending"}])["needs_input"][0])
check("C5 decided requests settle the step, and approved leave is recorded",
      gw.status_of(flow._availability_verdict([{"employee_id": "E9", "status": "approved"}],
                                              [])) == gw.DONE)
check("C6 an empty rota asks for somebody on it",
      gw.status_of(flow._shifts_verdict([])) == gw.NEEDS_INPUT)


def _cov(declared=MON, **over):
    f = _facts(**over)
    return flow._coverage_verdict(f["shifts"], f["other_shifts"], f["time_off"], f["budget"],
                                  f["override"], declared, flow.week_days(f["week_start"],
                                                                         declared))


check("C7 a clean week passes the careful checks", gw.status_of(_cov()) == gw.DONE)
_over = _cov(budget={"weekly_hours": 8})
check("C8 over budget is named with the hours and the ceiling",
      gw.status_of(_over) == gw.NEEDS_INPUT and "8h over" in _over["needs_input"][0])
check("C9 an APPROVED overage stops the refusal without hiding the number",
      gw.status_of(_cov(budget={"weekly_hours": 8},
                        override={"status": "approved", "approved_hours": 20})) == gw.DONE)
check("C10 an overage approval still awaiting a decision does not count as one",
      gw.status_of(_cov(budget={"weekly_hours": 8},
                        override={"status": "pending", "approved_hours": 20})) == gw.NEEDS_INPUT)
_leave = _cov(time_off=[{"employee_id": "E1", "status": "approved",
                         "start_date": "2026-11-23", "end_date": "2026-11-23"}])
check("C11 somebody scheduled while on APPROVED leave is named with the day",
      _leave["detail"]["on_approved_leave"] == ["E1 on 2026-11-23"])
check("C12 a PENDING request is not leave, so it is not that clash",
      _cov(time_off=[{"employee_id": "E1", "status": "pending",
                      "start_date": "2026-11-23", "end_date": "2026-11-23"}]
           )["detail"]["on_approved_leave"] == [])
_clash = _cov(other_shifts=[_shift("E1", "2026-11-23", store="B-200")])
check("C13 somebody booked at two stores the same day is named",
      _clash["detail"]["double_booked"] == ["E1 is also at B-200 on 2026-11-23"])
check("C14 the same person elsewhere on ANOTHER day is not a clash",
      _cov(other_shifts=[_shift("E1", "2026-11-26", store="B-200")]
           )["detail"]["double_booked"] == [])
check("C15 an unsent rota asks to be sent",
      gw.status_of(flow._tell_verdict([], [_shift("E1", "2026-11-23")])) == gw.NEEDS_INPUT)
check("C16 nothing to send is not something to chase",
      gw.status_of(flow._tell_verdict([], [])) == gw.NOT_STARTED)
check("C17 a send that did not arrive is named rather than counted as sent",
      gw.status_of(flow._tell_verdict([{"status": "failed", "target": "a@b.test"}],
                                      [])) == gw.NEEDS_INPUT)

print("\n── D. the regression: a week somebody left halfway ────────────────────────────────────")
_half = flow.describe(MON, _facts(shifts=[], sends=[]))
check("D1 a week with no rota picks up at laying out the shifts",
      _half["resume"] == "shifts", detail=str(_half["resume"]))
check("D2 the careful checks are not offered before there are shifts to check",
      {s["key"]: tuple(s["blocked_by"]) for s in _half["steps"]}["coverage"] == ("shifts",))
_blank = flow.describe(MON, {"store_code": "B-1", "week_start": WEEK})
check("D3 a week nobody has touched picks up at the budget", _blank["resume"] == "budget")
_held = flow.describe(MON, _facts(time_off=[{"status": "pending"}]))
check("D4 a week held up by an undecided request picks up where the decision is",
      _held["resume"] == "availability")
_done = flow.describe(MON, _facts(shifts=[_shift("E1", f"2026-11-{d}")
                                          for d in (23, 24, 25, 26, 27)]))
check("D5 a finished week has nothing left to pick up",
      _done["status"] == gw.DONE and _done["resume"] is None)
check("D6 the progress count is of the REQUIRED steps, so an optional one cannot block 'done'",
      _done["progress"]["required_done"] == _done["progress"]["required_total"] == 4)
check("D7 the board counts the store-weeks and names the step they stop at",
      flow.summarize([_half, _blank, _done])["open"] == 2)

print("\n── E. an uncovered day is reported, not judged ─────────────────────────────────────────")
_rep = _cov()
check("E1 the days with nobody on are NAMED", _rep["detail"]["days_with_nobody"]
      == ["Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
check("E2 and the step SAYS it is not calling them gaps, because nothing declares the open days",
      _rep["detail"]["open_days_declared"] is False
      and "No opening days are declared" in _rep["detail"]["days_with_nobody_note"])
check("E3 nothing declared means nothing identified — an empty day does not fail the step",
      gw.status_of(_rep) == gw.DONE)
_judged = _cov(declared=OPEN6)
check("E4 declare the opening days and the SAME step refuses, with no code change",
      gw.status_of(_judged) == gw.NEEDS_INPUT
      and "declared opening days have nobody on" in _judged["needs_input"][0])
check("E5 …and then it stops explaining itself, because there is nothing left to excuse",
      "days_with_nobody_note" not in _judged["detail"])

print("\n── F. one home, dereferenced ──────────────────────────────────────────────────────────")
_week_shifts = [_shift("E1", "2026-11-23"), _shift("E1", "2026-11-24", "09:00", "13:30")]
check("F1 a week's hours are ASKED of schedule_hours, never derived here",
      flow._shifts_verdict(_week_shifts)["detail"]["scheduled_hours"]
      == sh.total_hours(_week_shifts))
check("F1b and an overnight shift agrees with that home too",
      flow._shifts_verdict([_shift("E1", "2026-11-23", "22:00", "06:00")])["detail"][
          "scheduled_hours"] == sh.total_hours([_shift("E1", "2026-11-23", "22:00", "06:00")]))
check("F2 the week start is derived through that same home for every weekday",
      all(flow.week_days("2026-11-25", {"week_start_dow": d})[0]
          == sh.week_start_of(date(2026, 11, 25), d) for d in range(7)))
check("F3 the endpoint asks the tenant's DECLARED week start rather than choosing one",
      "_week_start_dow" in API_SRC and "work_week_start_dow" in API_SRC)
check("F4 'approved' and 'pending' have one spelling, read by both steps that need them",
      FLOW_SRC.count('PENDING = "pending"') == 1 and FLOW_SRC.count('APPROVED = "approved"') == 1)

print("\n── G. the wiring locks ────────────────────────────────────────────────────────────────")
_banned_imports = [w for w in ("fastapi", "supabase", "httpx", "requests", "psycopg")
                   if re.search(rf"^\s*(from|import)\s+{w}", FLOW_SRC, re.M)]
check("G1-lock the flow imports no framework, no client and no HTTP library", not _banned_imports,
      detail=" | ".join(_banned_imports))
check("G1b and it opens no file and no socket",
      "open(" not in FLOW_SRC and "socket" not in FLOW_SRC)

_writers = [w for w in (".insert(", ".update(", ".upsert(", ".delete(") if w in FLOW_SRC]
check("G2-lock the flow contains no writer at all — it is a projection", not _writers,
      detail=" | ".join(_writers))
_api_writers = [w for w in (".insert(", ".update(", ".upsert(", ".delete(") if w in API_SRC]
check("G2b-lock the endpoint surface contains no writer either", not _api_writers,
      detail=" | ".join(_api_writers))
_verbs = re.findall(r"@router\.(get|post|put|patch|delete)\(", API_SRC)
check("G2c-lock every route on the surface is a GET", set(_verbs) == {"get"},
      detail=" | ".join(sorted(set(_verbs))))

_literals = set()
for _node in ast.walk(ast.parse(FLOW_SRC)):
    if isinstance(_node, ast.Constant) and isinstance(_node.value, str):
        if _node.value in gw.STATUSES:
            _literals.add(_node.value)
check("G3-lock the flow spells no status word of its own", not _literals,
      detail=" | ".join(sorted(_literals)))
check("G3b and it dereferences the shared home",
      "from app.modules.core import guided_workflow" in FLOW_SRC)

_STEP_KEYS = set(flow.STEP_ORDER)
_copies = []
for _root, _dirs, _files in os.walk("app"):
    for _f in _files:
        if not _f.endswith(".py"):
            continue
        _p = os.path.join(_root, _f)
        if _p.replace("\\", "/") in (FLOW_PATH, API_PATH):
            continue
        try:
            _src = open(_p).read()
        except Exception:
            continue
        _found = {c.value for c in ast.walk(ast.parse(_src))
                  if isinstance(c, ast.Constant) and isinstance(c.value, str)}
        if _STEP_KEYS <= _found:
            _copies.append(_p)
check("G4-lock no second module declares the five step keys (the second-copy lock)", not _copies,
      detail=" | ".join(_copies))

# ★ G5 — the house-Monday lock. A module that buckets a SCHEDULE week must take the tenant's
# declared week start; `start.weekday()` arithmetic of its own is the defect this package fixed in
# the notify report builder (a Thursday-week tenant was emailed a Mon–Sun grid).
check("G5-lock the schedule report builder no longer snaps to a house Monday",
      "start - timedelta(days=start.weekday())" not in REPORT_SRC)
check("G5b and it asks the tenant's declared week start through the readers that answer it",
      "_week_start_dow" in REPORT_SRC and "week_start_of" in REPORT_SRC)

_FRONTEND = "../frontend/src/app/(platform)"
_missing = [f"{s['key']} -> {s['screen']}" for s in STEPS
            if not os.path.exists(os.path.join(_FRONTEND, s["screen"].lstrip("/"), "page.tsx"))]
check("G6-lock every step names a screen that EXISTS — a door that is not there is not a door",
      not _missing, detail=" | ".join(_missing))

check("G7 the endpoint publishes every status word, so the screen can invent none",
      "for s in gw.STATUSES" in API_SRC)
check("G7b and it dereferences both homes rather than re-deriving either",
      "from app.modules.core import guided_workflow" in API_SRC
      and "from app.modules.storeops import schedule_week_flow" in API_SRC)
check("G7c the roster-visibility default is the platform's own (open-stores-only, one spelling)",
      "roster_visibility" in API_SRC and "include_inactive" in API_SRC)

check("G8-lock the flow invents no 'published' shift state — the last step reads a recorded SEND",
      flow.SCHEDULE_REPORT_KEY == "storeops_schedule"
      and '"publish' not in FLOW_SRC and "status == \"published\"" not in FLOW_SRC)
check("G8b and the report key it reads is the one the registry actually records under",
      f'"{flow.SCHEDULE_REPORT_KEY}"' in REPORT_SRC)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
