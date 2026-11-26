"""Offline proof harness — RUNNING A PAY PERIOD IS A FLOW SOMEBODY CAN LEAVE HALFWAY, and it ASKS
the payroll gate rather than forming a second opinion (index §STOREOPS-16 / §CORE-51). Drives the
PURE `storeops/payroll_run_flow` with `clock_variance` and `payroll_hours_basis`, and reads the
endpoint surface as source. No live database, no network, no framework import.

Run: `cd backend && python3 harness_payroll_run_flow.py`

WHAT WAS ASKED FOR (owner 2026-11-25): *"...and simlar workflow for runnign the payroll with paying
attention to details"*. The eight steps were confirmed the same evening; the first of them — "pick
the pay period and the stores" — is the page's filter, so the seven below are the checks for one
store in one period.

THE CLASS: three flows asked for in one sentence. This is the THIRD caller of the shared home, which
is the point at which a copied rail would have become permanent. §G3/§G4 are what keep it from
appearing.

Proves:
  A. THE SHAPE — seven steps in the order the work happens, none of them optional, the gate placed
     after what it judges and the run placed last.
  B. ★ THE TENANT'S OWN PAY PERIOD — `period_start`/`period_end` are REQUIRED, a weekly and a
     fortnightly tenant are each measured over their own period, and a period straddling a month end
     names both months.
  C. THE STEPS, one at a time — open punches, somebody who never clocked in, schedule-heavy hours,
     uncorrected variances, hours waiting on the DM vs sent back by the DM, a missing pay rate, a
     salaried person with no figure.
  D. BEHAVIOURAL, THE REGRESSION — a period left half-done comes back to the check it stopped at.
  E. ★ THE GATE IS ASKED, NOT RE-DECIDED — the preflight step's words are `payroll_run_gate`'s own,
     character for character, so this surface can never say "ready" while the run refuses.
  F. ★ THE RUN RECORD IS MONTH-GRAINED AND SAYS SO — nothing writes a per-period run, so the step
     answers the question it can and names the grain in words.
  G. WIRING LOCKS — these FAIL THE BUILD if the design un-wires:
       G1  the flow imports no framework, no database client and no HTTP library;
       G2  the flow carries no writer, the endpoint surface carries none either, and every route on
           it is a GET — a surface whose job is to say whether a run MAY happen must not run one;
       G3  the flow spells no status word and no BASIS word of its own;
       G4  no second module declares the seven step keys (the third-copy lock);
       G5  the $0.00 question is asked of `clock_variance.employees_without_rate`, and "stands on a
           punch" of `payroll_hours_basis`, with no second rule here;
       G6  every step names a screen that EXISTS in the frontend;
       G7  the endpoint publishes every status word and every basis word, so the screen invents none;
       G8  ★ THE MONEY GATE — step 7's amounts ride `pay_visibility.can_see_pay`, the withhold says
           so in words, and the gate fails CLOSED.
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


FLOW_PATH = "app/modules/storeops/payroll_run_flow.py"
API_PATH = "app/modules/storeops/payroll_run_api.py"
FLOW_SRC = open(FLOW_PATH).read()
API_SRC = open(API_PATH).read()

from app.modules.core import guided_workflow as gw              # noqa: E402
from app.modules.storeops import clock_variance as cv           # noqa: E402
from app.modules.storeops import payroll_hours_basis as hb      # noqa: E402
from app.modules.storeops import payroll_run_flow as flow       # noqa: E402

WEEKLY = {"period_start": "2026-11-16", "period_end": "2026-11-22"}
FORTNIGHT = {"period_start": "2026-11-09", "period_end": "2026-11-22"}
STRADDLES = {"period_start": "2026-10-26", "period_end": "2026-11-08"}

HOURLY = {"employee_id": "E1", "name": "Ada", "home_store": "B-117", "is_active": True,
          "pay_basis": "hourly", "pay_rate": 17.0}
NO_RATE = {"employee_id": "E2", "name": "Ben", "home_store": "B-117", "is_active": True,
           "pay_basis": "hourly", "pay_rate": None}
SALARIED = {"employee_id": "E3", "name": "Cleo", "home_store": "B-117", "is_active": True,
            "pay_basis": "salary", "pay_amount": 5000, "pay_rate": None}


def _facts(**over):
    f = {"store_code": "B-117",
         "punches": [{"employee_id": "E1", "clock_in": "x", "clock_out": "y"}],
         "worked_without_punch": [],
         "paid_rows": [{"employee_id": "E1", "hours_by_basis": {hb.CLOCKED: 40}}],
         "blocking": [], "manual_uncounted": [],
         "approvals": [{"employee_id": "E1", "dm_status": "approved", "hr_status": "approved"}],
         "employees": [HOURLY], "paid_employee_ids": ["E1"], "salary_rows": [],
         "run_rows": [{"period": "2026-11", "store": "B-117", "wages": 4000,
                       "run_at": "2026-11-23T02:00:00Z"}],
         "tax_rows": [{"employee_id": "E1", "employer_total": 320}],
         "expense_rows": [{"amount": 100}]}
    f.update(over)
    return f


print("\n── A. the shape of the flow ────────────────────────────────────────────────────────────")
STEPS = flow.steps(WEEKLY)
check("A1 seven checks, in the order the work happens",
      [s["key"] for s in STEPS] == ["punches", "basis", "exceptions", "approval", "rates",
                                    "preflight", "run"])
check("A2 none of them is optional — a payroll has no optional half",
      all(s["required"] for s in STEPS))
check("A3 every dependency points at a step declared BEFORE it, so a cycle is inexpressible",
      all(set(s["depends_on"]) <= {x["key"] for x in STEPS[:i]} for i, s in enumerate(STEPS)))
_dep = {s["key"]: set(s["depends_on"]) for s in STEPS}
check("A4 the gate comes after what it judges (the exceptions and the rates)",
      _dep["preflight"] == {"exceptions", "rates"})
check("A5 the run comes after the approvals and the gate", _dep["run"] == {"approval", "preflight"})
check("A6 every step is worded with WHY it is there", all(s["why"].strip() for s in STEPS))

print("\n── B. the tenant's own pay period ─────────────────────────────────────────────────────")
_refused = []
for bad in ({}, None, {"period_start": "2026-11-01"}, {"period_end": "2026-11-15"},
            {"period_start": "nope", "period_end": "2026-11-15"}):
    try:
        flow.steps(bad)
        _refused.append(repr(bad))
    except ValueError:
        pass
check("B1-lock a caller that has not asked the org its pay period CANNOT answer", not _refused,
      detail=" | ".join(_refused))
check("B2 a weekly and a fortnightly tenant are each measured over their own period",
      flow.describe(WEEKLY, _facts())["period_start"] == "2026-11-16"
      and flow.describe(FORTNIGHT, _facts())["period_start"] == "2026-11-09")
check("B3 a period inside one month names one month", flow.months_of(WEEKLY) == ("2026-11",))
check("B4 a period straddling a month end names both", flow.months_of(STRADDLES)
      == ("2026-10", "2026-11"))
check("B5 no tenant's vocabulary appears in the flow",
      not any(w in FLOW_SRC.lower() for w in ("boost", "luxelink", "cellfonz", "verizon")))

print("\n── C. the seven checks, one at a time ─────────────────────────────────────────────────")
check("C1 no punches at all is an OPEN step, not a clean one (absence is never zero)",
      gw.status_of(flow._punches_verdict([], [])) == gw.NEEDS_INPUT)
check("C2 somebody still clocked in is named",
      "still open" in flow._punches_verdict([{"employee_id": "E1", "clock_out": None}],
                                            [])["needs_input"][0])
check("C3 somebody on the rota who never clocked in is named",
      "never clocked in" in " ".join(flow._punches_verdict([{"employee_id": "E1",
                                                             "clock_out": "x"}],
                                                           ["E9"])["needs_input"]))
check("C4 hours standing on a punch settle the basis step",
      gw.status_of(flow._basis_verdict([{"hours_by_basis": {hb.CLOCKED: 40}}], WEEKLY)) == gw.DONE)
_sched = flow._basis_verdict([{"hours_by_basis": {hb.CLOCKED: 10}},
                              {"hours_by_basis": {hb.SCHEDULE_OPT_IN: 90}}], WEEKLY)
check("C5 mostly-schedule hours are named with the percentage",
      gw.status_of(_sched) == gw.NEEDS_INPUT and "Only 10%" in _sched["needs_input"][0])
check("C6 hours on a basis that does not pay are reported as paying nobody",
      "would pay nobody here" in flow._basis_verdict(
          [{"hours_by_basis": {hb.NOT_MEASURED_UNPAID: 8}}], WEEKLY)["needs_input"][0])
check("C7 an uncorrected variance is named with the person and the day",
      "B-117/E1 2026-11-17" in flow._exceptions_verdict(
          [{"store_code": "B-117", "employee_id": "E1", "date": "2026-11-17"}],
          [])["needs_input"][0])
check("C8 hand-typed hours nothing has counted are named",
      "not counted by anything yet" in flow._exceptions_verdict([], [{"x": 1}])["needs_input"][0])
check("C9 nothing raised for approval is SAID, not read as approved",
      "No hours have been raised" in flow._approval_verdict([])["needs_input"][0])
_wait = " ".join(flow._approval_verdict([{"dm_status": "pending",
                                          "hr_status": "pending"}])["needs_input"])
_back = " ".join(flow._approval_verdict([{"dm_status": "sent_back",
                                          "hr_status": "pending"}])["needs_input"])
check("C10 waiting on the manager and sent back by the manager are different sentences",
      "waiting on the manager" in _wait and "sent back by the manager" in _back and _wait != _back)
check("C11 HR is only waiting on what the manager already approved",
      flow._approval_verdict([{"dm_status": "pending",
                               "hr_status": "pending"}])["detail"]["hr_pending"] == 0)
check("C12 an admin override is recorded rather than hidden",
      "override" in flow._approval_verdict([{"dm_status": "approved", "hr_status": "approved",
                                             "override_by": "x",
                                             "override_reason": "y"}])["detail"]["override_note"])
check("C13 somebody this run would pay with no rate is named with the $0.00 consequence",
      "$0.00" in flow._rates_verdict([HOURLY, NO_RATE], ["E1", "E2"], [])["needs_input"][0])
check("C14 a dormant record this run would NOT pay does not block it",
      gw.status_of(flow._rates_verdict([HOURLY, NO_RATE], ["E1"], [])) == gw.DONE)
check("C15 a salaried person with no hourly rate is not a defect",
      gw.status_of(flow._rates_verdict([SALARIED], ["E3"],
                                       [{"employee_id": "E3", "amount": 2500}])) == gw.DONE)
check("C16 a salaried person with no figure for the period IS named",
      "no salary figure" in flow._rates_verdict([SALARIED], ["E3"], [])["needs_input"][0])

print("\n── D. the regression: a period somebody left halfway ──────────────────────────────────")
check("D1 a period nobody has started picks up at the punches",
      flow.describe(WEEKLY, {"store_code": "B-1"})["resume"] == "punches")
check("D2 a period held up by a variance picks up at the exceptions",
      flow.describe(WEEKLY, _facts(blocking=[{"store_code": "B-117",
                                              "employee_id": "E1"}]))["resume"] == "exceptions")
check("D3 a period waiting on a sign-off picks up at the approval",
      flow.describe(WEEKLY, _facts(approvals=[{"dm_status": "pending",
                                               "hr_status": "pending"}]))["resume"] == "approval")
_done = flow.describe(WEEKLY, _facts())
check("D4 a finished period has nothing left to pick up",
      _done["status"] == gw.DONE and _done["resume"] is None)
check("D5 the progress count is of all seven checks",
      _done["progress"]["required_done"] == _done["progress"]["required_total"] == 7)
check("D6 the gate is not offered before the exceptions it judges",
      "exceptions" in {k: tuple(s["blocked_by"]) for k, s in
                       {x["key"]: x for x in flow.describe(
                           WEEKLY, _facts(blocking=[{"store_code": "B-117",
                                                     "employee_id": "E1"}]))["steps"]}.items()
                       }["preflight"])
check("D7 the board counts the store-periods and names the check they stop at",
      flow.summarize([_done, flow.describe(WEEKLY, _facts(store_code="B-2",
                                                          run_rows=[]))])["open"] == 1)

print("\n── E. the gate is ASKED, never re-decided ─────────────────────────────────────────────")
_blocking = [{"store_code": "B-117", "employee_id": "E1", "date": "2026-11-17"}]
_gate = cv.payroll_run_gate(blocking=_blocking, cfg={"payroll_run_gate_enabled": True},
                            employee_rows=[HOURLY], paid_employee_ids=["E1"])
_pre = flow._preflight_verdict(_blocking, [HOURLY], ["E1"], WEEKLY)
check("E1 the refusal the person reads is the RUN'S OWN, character for character",
      _pre["needs_input"][0] == _gate["reason"])
check("E2 and the step is open exactly when the gate refuses",
      (gw.status_of(_pre) == gw.NEEDS_INPUT) is _gate["refused"])
_ok_gate = cv.payroll_run_gate(blocking=(), cfg={"payroll_run_gate_enabled": True},
                               employee_rows=[HOURLY], paid_employee_ids=["E1"])
_ok_pre = flow._preflight_verdict([], [HOURLY], ["E1"], WEEKLY)
check("E3 a passing gate keeps its own wording too",
      gw.status_of(_ok_pre) == gw.DONE and _ok_pre["detail"]["gate_reason"] == _ok_gate["reason"])
check("E4 an org with the variance gate off still hears about a missing rate",
      gw.status_of(flow._preflight_verdict(_blocking, [NO_RATE], ["E2"],
                                           dict(WEEKLY, gate_enabled=False))) == gw.NEEDS_INPUT)
check("E5-lock the flow calls the gate rather than restating either refusal",
      "_cv.payroll_run_gate(" in FLOW_SRC
      and "clock variance" not in FLOW_SRC.replace("clock variances are", "")
      .replace("clock variance is", "").replace("uncorrected clock variances", ""))

print("\n── F. the run record is month-grained, and says so ────────────────────────────────────")
check("F1 a month that has not been run is named",
      "has not been run for the month 2026-11" in flow._run_verdict([], [], [],
                                                                    WEEKLY)["needs_input"][0])
check("F2 a straddling period needs BOTH months and names the missing one",
      "2026-11" in flow._run_verdict([{"period": "2026-10", "wages": 1}],
                                     [{"employer_total": 1}], [], STRADDLES)["needs_input"][0])
check("F3 the step says IN WORDS that the record is by month and not by pay period",
      "recorded by MONTH, not by pay period"
      in flow._run_verdict([{"period": "2026-11", "wages": 1}], [{"employer_total": 1}], [],
                           FORTNIGHT)["detail"]["grain_note"])
check("F4 a run that pushed nothing for this store is named, not counted as done",
      "$0.00 of wages" in flow._run_verdict([{"period": "2026-11", "wages": 0}],
                                            [{"employer_total": 1}], [],
                                            WEEKLY)["needs_input"][0])
check("F5 employer tax not recorded is named",
      "Employer tax" in flow._run_verdict([{"period": "2026-11", "wages": 1}], [], [],
                                          WEEKLY)["needs_input"][0])
check("F6 no expenses is a NOTE, because a tenant may legitimately book none",
      gw.status_of(flow._run_verdict([{"period": "2026-11", "wages": 1}],
                                     [{"employer_total": 1}], [], WEEKLY)) == gw.DONE)
check("F7-lock nothing here reads a per-period run, because nothing writes one",
      "period_start" not in FLOW_SRC.split("def _run_verdict")[1].split("def ")[0])

print("\n── G. the wiring locks ────────────────────────────────────────────────────────────────")
_banned = [w for w in ("fastapi", "supabase", "httpx", "requests", "psycopg")
           if re.search(rf"^\s*(from|import)\s+{w}", FLOW_SRC, re.M)]
check("G1-lock the flow imports no framework, no client and no HTTP library", not _banned,
      detail=" | ".join(_banned))
check("G1b and it opens no file and no socket",
      "open(" not in FLOW_SRC and "socket" not in FLOW_SRC)

_w = [x for x in (".insert(", ".update(", ".upsert(", ".delete(") if x in FLOW_SRC]
check("G2-lock the flow carries no writer at all", not _w, detail=" | ".join(_w))
_aw = [x for x in (".insert(", ".update(", ".upsert(", ".delete(") if x in API_SRC]
check("G2b-lock the endpoint surface carries no writer either", not _aw, detail=" | ".join(_aw))
_verbs = re.findall(r"@router\.(get|post|put|patch|delete)\(", API_SRC)
check("G2c-lock every route is a GET — this surface says whether a run MAY happen, it never runs one",
      set(_verbs) == {"get"}, detail=" | ".join(sorted(set(_verbs))))

_status_lits = {c.value for c in ast.walk(ast.parse(FLOW_SRC))
                if isinstance(c, ast.Constant) and isinstance(c.value, str)
                and c.value in gw.STATUSES}
check("G3-lock the flow spells no status word of its own", not _status_lits,
      detail=" | ".join(sorted(_status_lits)))
_basis_lits = {c.value for c in ast.walk(ast.parse(FLOW_SRC))
               if isinstance(c, ast.Constant) and isinstance(c.value, str)
               and c.value in hb.ALL_BASES}
check("G3b-lock and no BASIS word of its own — that vocabulary has one home (§14y)",
      not _basis_lits, detail=" | ".join(sorted(_basis_lits)))
check("G3c and it dereferences both homes",
      "from app.modules.core import guided_workflow" in FLOW_SRC
      and "payroll_hours_basis as _hb" in FLOW_SRC)

_KEYS = set(flow.STEP_ORDER)
_copies = []
for _root, _dirs, _files in os.walk("app"):
    for _f in _files:
        if not _f.endswith(".py"):
            continue
        _p = os.path.join(_root, _f).replace("\\", "/")
        if _p in (FLOW_PATH, API_PATH):
            continue
        try:
            _src = open(_p).read()
            _found = {c.value for c in ast.walk(ast.parse(_src))
                      if isinstance(c, ast.Constant) and isinstance(c.value, str)}
        except Exception:
            continue
        if _KEYS <= _found:
            _copies.append(_p)
check("G4-lock no second module declares the seven step keys (the third-copy lock)", not _copies,
      detail=" | ".join(_copies))

check("G5 the $0.00 question is ASKED of the one home that answers it",
      "_cv.employees_without_rate(" in FLOW_SRC)
check("G5b and 'stands on a punch' is asked of the basis home",
      "_hb.basis_is_measurement(" in FLOW_SRC and "_hb.row_is_paid(" in FLOW_SRC)
check("G5c and the words each basis is explained with are that home's own",
      "_hb.basis_reason(" in FLOW_SRC)

_FRONTEND = "../frontend/src/app/(platform)"
_missing = [f"{s['key']} -> {s['screen']}" for s in STEPS
            if not os.path.exists(os.path.join(_FRONTEND, s["screen"].lstrip("/"), "page.tsx"))]
check("G6-lock every step names a screen that EXISTS — a door that is not there is not a door",
      not _missing, detail=" | ".join(_missing))

check("G7 the endpoint publishes every status word, so the screen can invent none",
      "for s in gw.STATUSES" in API_SRC)
check("G7b and every basis word with the words it means, from the one home",
      "_hb.basis_reason(b) for b in _hb.ALL_BASES" in API_SRC)
check("G7c and it dereferences both homes rather than re-deriving either",
      "from app.modules.core import guided_workflow" in API_SRC
      and "payroll_run_flow as _flow" in API_SRC)

check("G8-lock step 7's money rides the platform's own pay-visibility gate",
      "pay_visibility" in API_SRC and "can_see_pay" in API_SRC)
check("G8b and the withhold SAYS so, because a blank is indistinguishable from $0.00",
      "money_withheld" in API_SRC)
check("G8c and the pay gate FAILS CLOSED",
      "return False" in API_SRC.split("def _can_see_pay")[1].split("@router")[0])
check("G8d and the store gate fails closed too",
      "return []" in API_SRC.split("def _gate(")[1].split("def ")[0])
check("G8e the period is ASKED of the home that resolves the tenant's cycle",
      "_resolve_period" in API_SRC)
check("G8f the hours are the ONE pay derivation, called in-process",
      "from app.modules.storeops.router import get_payroll" in API_SRC)
check("G8g the punches and variances come from the clock-variance board's OWN assembler",
      "clock_variance_api" in API_SRC and "_assemble(" in API_SRC)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
