#!/usr/bin/env python3
"""PROOF, DB-FREE: the Hours Approval board saves a correction without signing it off, and the four
other defects that made a typed figure vanish.

OWNER REPORT 2026-10-26, verbatim: *"there is a lag in saving andf updating the hours when a change is
done"* and *"i havd to enter hours for simarjyot 3 times to get the correct horus and still i cannot
see the correnct hours"* and *"i selected mohammed sami but nothing showed up in filters"*.

THE REGRESSION THIS FILE REPRODUCES, measured on production before the fix (read-only, 2026-10-26):

  · `storeops.payroll_approval` for E043 / 2026-10-05..18 carries ONE adjustment (-10.97) and
    `storeops.payroll_change_log` carries exactly ONE matching row — against three typed attempts.
    Two of the three never reached the server at all, because the ONLY way to persist a typed figure
    was to approve in the same click: `decide` wrote `hours_approved` solely on the DM stage, and the
    board cleared every typed box on each reload, including the reload after a REFUSED save.
  · E043 also carries SIX `storeops.manual_hours` rows totalling -16.00 h, all reasoned "wrong clock
    out" — a correction `GET /storeops/payroll` does not read, so the board showed 102.84 h as the
    only truth and the reviewer rebuilt the correction by hand 5.03 h adrift of it.
  · E029 (Mohammed Sami): 125.18 h computed, adjusted **+18.49** toward a target of 88.20 — a sign
    error of 55.47 h / $943 at $17.00, already approved by DM AND HR. The confirmation this work adds
    states "125.18 -> 143.67" in words before such a save lands.
  · The person filter sent the picker's option id (a NAME — `GET /core/filter-options` keys `reps`
    by name) as `employee_id`, compared against 'E043'. It could not match anybody, ever.

WHAT IS PROVED HERE, all of it against the REAL module, imported, with no database:
  A  `save` is an action of the existing decide path and moves NEITHER approval stage
  B  the reason gates and the negative-payable guard are unchanged by it
  C  an explicit empty `hours_approved` CLEARS a correction, reason-gated and logged
  D  an HR approval survives a DM `save` (a correction must not re-sign a row)
  E  the person filter resolves a NAME through core.person_identity, and an id still matches
  F  `manual_hours` is REPORTED beside the figure and folded into NOTHING
  G  the lag: the 200k-row sales scan is skipped when every employee has a clock record

Run: python3 harness_payroll_approval_save.py        (from backend/)
No DB, no network, no FastAPI app: the Supabase client is a recording fake, so every assertion below
is about the module's own decisions.
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS: list[str] = []
FAIL: list[str] = []


def ck(label: str, cond: bool, extra: object = "") -> None:
    if cond:
        PASS.append(label)
    else:
        FAIL.append(f"{label}  {extra!r}" if extra != "" else label)


# ── the recording fake ────────────────────────────────────────────────────────────────────────────
# One table-ish object per relation, answering .select/.eq/.gte/.lte/.limit/.order/.execute and
# recording every upsert. It is deliberately dumb: the point is to watch what the module DECIDES,
# never to re-implement PostgREST.
class _Res:
    def __init__(self, data):
        self.data = data


class _Tbl:
    def __init__(self, store, name):
        self.store, self.name = store, name
        self._filters: dict = {}

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self._filters[k] = v
        return self

    def gte(self, *_a):
        return self

    def lte(self, *_a):
        return self

    def in_(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def insert(self, row, *_a, **_k):
        self.store.inserts.append((self.name, row))
        return self

    def upsert(self, row, *_a, **_k):
        self.store.upserts.append((self.name, dict(row)))
        self._pending = row
        return self

    def execute(self):
        if getattr(self, "_pending", None) is not None:
            row, self._pending = self._pending, None
            return _Res([{**row, "id": "row-1"}])
        rows = self.store.rows.get(self.name, [])
        for k, v in self._filters.items():
            rows = [r for r in rows if r.get(k) == v]
        self.store.reads.append(self.name)
        return _Res(list(rows))


class _Client:
    def __init__(self, rows):
        self.rows = rows
        self.upserts: list = []
        self.inserts: list = []
        self.reads: list = []

    def table(self, name):
        return _Tbl(self, name)

    def schema(self, _s):
        return self


import app.modules.storeops.payroll_approval as PA  # noqa: E402

PERIOD = (date(2026, 10, 5), date(2026, 10, 18))

#: The live case, with its real figures. E043 = a correction already filed on the Time Clock screen
#: that the board's own figure does not contain; E029 = the sign error the confirmation prevents.
HOURS_ROWS = [
    {"employee_id": "E043", "name": "Simarjyot Singh", "store": "B-1750",
     "actual_hours": 102.84, "lunch_deduction_hours": 5.50, "scheduled_hours": 87.0, "pay_rate": 17.0},
    {"employee_id": "E029", "name": "Mohammed Sami", "store": "B-2612",
     "actual_hours": 125.18, "lunch_deduction_hours": 4.0, "scheduled_hours": 87.0, "pay_rate": 17.0},
]
#: `org_id` is on every fixture row deliberately: the fake applies `.eq("org_id", ...)`, so a reader
#: that forgot the org scope would return NOTHING here rather than silently leaking another tenant.
MANUAL_ROWS = [
    {"org_id": PA.ORG_ID, "employee_id": "E043", "work_date": "2026-10-07", "hours": -3.0, "reason": "wrong clock out"},
    {"org_id": PA.ORG_ID, "employee_id": "E043", "work_date": "2026-10-10", "hours": -3.0, "reason": "wrong clock out"},
    {"org_id": PA.ORG_ID, "employee_id": "E043", "work_date": "2026-10-11", "hours": -3.0, "reason": "wrong clock out"},
    {"org_id": PA.ORG_ID, "employee_id": "E043", "work_date": "2026-10-12", "hours": -3.0, "reason": "wrong clock out"},
    {"org_id": PA.ORG_ID, "employee_id": "E043", "work_date": "2026-10-13", "hours": -3.0, "reason": "wrong clock out"},
    {"org_id": PA.ORG_ID, "employee_id": "E043", "work_date": "2026-10-14", "hours": -1.0, "reason": "wrong clock out"},
]

LOGGED: list[dict] = []


def _install(saved_rows, manual=None, client_box=None):
    """Point the module's I/O at the fake. Restored by the caller's try/finally."""
    cli = _Client({"payroll_approval": list(saved_rows), "manual_hours": list(manual or []),
                   "employees": [], "payroll_payer": [], "store_payer": []})
    if client_box is not None:
        client_box.append(cli)
    PA.sb = lambda: cli                                                      # type: ignore[assignment]
    PA._hours_for_period = lambda *_a, **_k: [dict(r) for r in HOURS_ROWS]   # type: ignore[assignment]
    PA._existing = lambda *_a, **_k: {r["employee_id"]: dict(r) for r in saved_rows}  # type: ignore[assignment]
    return cli


class _Body:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def run_decide(rows, stage="dm", saved=(), manual=()):
    """decide() against the fake, with the manager gate and the change log stubbed."""
    import app.modules.storeops.router as R
    LOGGED.clear()
    box: list = []
    _install(saved, manual, box)
    old_req, old_log = getattr(R, "_require_manager", None), getattr(R, "_log_payroll_change", None)
    R._require_manager = lambda *_a, **_k: {"email": "dm@example.test", "role": "district_manager"}  # type: ignore[assignment]
    R._log_payroll_change = lambda *a, **k: LOGGED.append(dict(k))                                   # type: ignore[assignment]
    PA._resolve_period = lambda *_a, **_k: PERIOD                                                    # type: ignore[assignment]
    try:
        out = PA.decide(_Body(stage=stage, period_start="2026-10-05", period_end="2026-10-18", rows=rows),
                        authorization="Bearer x", org_id=PA.ORG_ID)
    finally:
        if old_req is not None:
            R._require_manager = old_req      # type: ignore[assignment]
        if old_log is not None:
            R._log_payroll_change = old_log   # type: ignore[assignment]
    return out, box[0]


BASE_SAVED = [{"id": "a1", "employee_id": "E043", "hours_approved": None, "adjustment_hours": -10.97,
               "dm_status": "approved", "hr_status": "approved", "dm_by": "dm@example.test"}]

print(__doc__.split("Run:")[0].strip()[:0] or "", end="")
print("\n── A. `save` records and signs nothing ──")
out, cli = run_decide([{"employee_id": "E043", "action": "save",
                        "hours_approved": "86.84", "reason": "wrong clock out"}], saved=BASE_SAVED)
ck("save is an accepted action", out["applied"] == 1, out)
ck("…with no errors", out["errors"] == [], out["errors"])
wrote = dict(cli.upserts[-1][1]) if cli.upserts else {}
ck("…and it WROTE the corrected figure", wrote.get("hours_approved") == 86.84, wrote.get("hours_approved"))
ck("…touching dm_status not at all", "dm_status" not in wrote, sorted(wrote))
ck("…touching hr_status not at all", "hr_status" not in wrote, sorted(wrote))
ck("…and not stamping a signature", "dm_by" not in wrote and "dm_at" not in wrote, sorted(wrote))
ck("…and not snapshotting an approval it did not make",
   "worked_at_approval" not in wrote, sorted(wrote))
ck("…while the change log still records who moved the number",
   len(LOGGED) == 1 and LOGGED[0].get("field") == "approved_hours"
   and LOGGED[0].get("after") == 86.84 and LOGGED[0].get("reason") == "wrong clock out", LOGGED)
ck("…and the result says which action it was, not a status it did not set",
   out["results"][0].get("action") == "save", out["results"][0])
ck("…and reports the hours that will now be paid",
   out["results"][0].get("hours_effective") == 86.84, out["results"][0])

print("\n── B. the gates `save` inherits, unchanged ──")
out, _ = run_decide([{"employee_id": "E043", "action": "save", "hours_approved": "86.84"}], saved=BASE_SAVED)
ck("a correction with no reason is refused",
   out["applied"] == 0 and "reason" in out["errors"][0]["error"], out["errors"])
out, _ = run_decide([{"employee_id": "E043", "action": "save", "hours_approved": "-1", "reason": "r"}],
                    saved=BASE_SAVED)
ck("negative hours are refused", out["applied"] == 0 and ">= 0" in out["errors"][0]["error"], out["errors"])
out, _ = run_decide([{"employee_id": "E043", "action": "save", "adjustment_hours": "-900",
                      "adjustment_reason": "r"}], saved=BASE_SAVED)
ck("an adjustment below zero payable is refused",
   out["applied"] == 0 and "negative" in out["errors"][0]["error"], out["errors"])
out, _ = run_decide([{"employee_id": "E043", "action": "save", "adjustment_hours": "-20"}], saved=BASE_SAVED)
ck("an adjustment with no reason is refused",
   out["applied"] == 0 and "reason" in out["errors"][0]["error"], out["errors"])
out, _ = run_decide([{"employee_id": "E999", "action": "save", "hours_approved": "8", "reason": "r"}])
ck("an employee who did not work in the period is refused, not invented",
   out["applied"] == 0 and "did not work" in out["errors"][0]["error"], out["errors"])
out, _ = run_decide([{"employee_id": "E043", "action": "save", "hours_approved": "8", "reason": "r"}],
                    stage="hr", saved=BASE_SAVED)
ck("`save` is not an HR action — hours are corrected at the correcting stage",
   out["applied"] == 0 and "HR" in out["errors"][0]["error"], out["errors"])

print("\n── C. a correction can be taken back ──")
CORRECTED = [{"id": "a1", "employee_id": "E043", "hours_approved": 86.84, "adjustment_hours": 0,
              "dm_status": "approved", "hr_status": "pending"}]
out, cli = run_decide([{"employee_id": "E043", "action": "save", "hours_approved": "",
                        "reason": "typed in error"}], saved=CORRECTED)
wrote = dict(cli.upserts[-1][1]) if cli.upserts else {}
ck("an explicit empty value clears the correction",
   out["applied"] == 1 and wrote.get("hours_approved", "absent") is None, wrote.get("hours_approved", "absent"))
ck("…and the clear is logged with the figure it reverts to",
   len(LOGGED) == 1 and LOGGED[0].get("before") == 86.84 and LOGGED[0].get("after") == 102.84, LOGGED)
out, _ = run_decide([{"employee_id": "E043", "action": "save", "hours_approved": ""}], saved=CORRECTED)
ck("a clear with no reason is refused — a payroll number may not move anonymously either way",
   out["applied"] == 0 and "reason" in out["errors"][0]["error"], out["errors"])
out, cli = run_decide([{"employee_id": "E043", "action": "save", "hours_approved": "",
                        "reason": "r"}], saved=BASE_SAVED)
wrote = dict(cli.upserts[-1][1]) if cli.upserts else {}
ck("an empty box on a row with NO correction is 'not mentioned', never a clear and never a zero",
   "hours_approved" not in wrote and out["applied"] == 1, sorted(wrote))

print("\n── D. a correction never re-signs the row ──")
out, cli = run_decide([{"employee_id": "E043", "action": "save", "adjustment_hours": "-36.98",
                        "adjustment_reason": "clock-out never recorded"}], saved=BASE_SAVED)
wrote = dict(cli.upserts[-1][1]) if cli.upserts else {}
ck("a DM save leaves an existing HR approval standing",
   "hr_status" not in wrote, sorted(wrote))
out, cli = run_decide([{"employee_id": "E043", "action": "send_back", "note": "n"}], saved=BASE_SAVED)
wrote = dict(cli.upserts[-1][1]) if cli.upserts else {}
ck("…while a send_back still invalidates it, exactly as before",
   wrote.get("hr_status") == "pending", wrote.get("hr_status"))
out, cli = run_decide([{"employee_id": "E043", "action": "reset"}], saved=BASE_SAVED)
wrote = dict(cli.upserts[-1][1]) if cli.upserts else {}
ck("…and a reset still clears the snapshot and both stages",
   wrote.get("dm_status") == "pending" and wrote.get("worked_at_approval") is None, wrote)
out, cli = run_decide([{"employee_id": "E043", "action": "approve"}], saved=BASE_SAVED)
wrote = dict(cli.upserts[-1][1]) if cli.upserts else {}
ck("an approve still stamps, signs and snapshots (byte-identical to before)",
   wrote.get("dm_status") == "approved" and wrote.get("dm_by") == "dm@example.test"
   and wrote.get("worked_at_approval") == 108.34, wrote)

print("\n── E. the person filter takes a NAME or an id ──")


def run_list(employee_id="", roster=()):
    _install([], MANUAL_ROWS)
    _r = [dict(x) for x in roster]
    PA._resolve_period = lambda *_a, **_k: PERIOD                 # type: ignore[assignment]
    PA._active_in_span = lambda *_a, **_k: _r                     # type: ignore[assignment]
    PA._payers = lambda *_a, **_k: []                             # type: ignore[assignment]
    PA._store_payer_map = lambda *_a, **_k: {}                    # type: ignore[assignment]
    PA._can_see_pay_rates = lambda *_a, **_k: True                # type: ignore[assignment]
    PA._existing = lambda *_a, **_k: {}                           # type: ignore[assignment]
    return PA.list_approvals(start="2026-10-05", end="2026-10-18", employee_id=employee_id,
                             authorization="Bearer x", org_id=PA.ORG_ID)


ck("the picker's own value — a NAME — now matches",
   [r["employee_id"] for r in run_list("Mohammed Sami")["rows"]] == ["E029"],
   [r["employee_id"] for r in run_list("Mohammed Sami")["rows"]])
ck("…whatever the word ORDER the other system spells it in",
   [r["employee_id"] for r in run_list("Sami Mohammed")["rows"]] == ["E029"],
   [r["employee_id"] for r in run_list("Sami Mohammed")["rows"]])
ck("…and whatever the case",
   [r["employee_id"] for r in run_list("MOHAMMED SAMI")["rows"]] == ["E029"])
# NEGATIVE CONTROL, stated rather than left to be discovered: this parameter is the standard bar's
# COMMA-SEPARATED multi-select, so a comma inside one value is two values. 'Sami, Mohammed' is the
# POS's spelling, never the picker's (storeops.employees.name is 'First Last'), so it cannot arrive
# here from the UI — but a caller hand-building the query must not expect it to work.
ck("a 'Last, First' spelling is TWO values to a comma-separated multi-select, and matches neither",
   run_list("Sami, Mohammed")["rows"] == [],
   [r["employee_id"] for r in run_list("Sami, Mohammed")["rows"]])
ck("an employee id still matches as an id (an existing caller is byte-identical)",
   [r["employee_id"] for r in run_list("E043")["rows"]] == ["E043"])
ck("a multi-select of both forms matches both",
   sorted(r["employee_id"] for r in run_list("E043,Mohammed Sami")["rows"]) == ["E029", "E043"])
ck("an unknown person is an EMPTY board, never every row",
   run_list("Nobody At All")["rows"] == [])
ck("no filter still returns everyone", len(run_list("")["rows"]) == 2)

print("\n── F. the correction the figure does not contain is REPORTED, and folded into nothing ──")
board = run_list("")
by_id = {r["employee_id"]: r for r in board["rows"]}
ck("the -16.00 h of Time Clock corrections are stated on the row",
   by_id["E043"]["manual_hours_uncounted"] == -16.0, by_id["E043"]["manual_hours_uncounted"])
ck("…with the day and reason of each, so the correction is informed not invented",
   len(by_id["E043"]["manual_hours_days"]) == 6
   and by_id["E043"]["manual_hours_days"][0]["reason"] == "wrong clock out",
   by_id["E043"]["manual_hours_days"][:1])
ck("…labelled NOT counted, by name", by_id["E043"]["manual_hours_counted"] is False)
ck("…and the payable figure is byte-identical to before it was shown: 102.84 + (-10.97) with NO fold",
   by_id["E043"]["hours_payable"] == 102.84 and by_id["E043"]["hours_effective"] == 102.84,
   (by_id["E043"]["hours_payable"], by_id["E043"]["hours_effective"]))
ck("…and the period total reports it beside the hours, never inside them",
   board["totals"]["manual_hours_uncounted"] == -16.0 and board["totals"]["hours"] == 228.02,
   (board["totals"]["manual_hours_uncounted"], board["totals"]["hours"]))
ck("an employee with no correction says so with a zero, not a missing key",
   by_id["E029"]["manual_hours_uncounted"] == 0.0 and by_id["E029"]["manual_hours_days"] == [])

print("\n── G. the lag: the 200k-row sales scan only runs when something needs it ──")
SCANS: list[int] = []
PA._worked_days_from_sales = lambda *_a, **_k: (SCANS.append(1) or {})   # type: ignore[assignment]
run_list("")
ck("every employee has a clock record ⇒ the scan is SKIPPED", SCANS == [], SCANS)
SCANS.clear()
no_punch = [{"employee_id": "E900", "name": "No Punch Person", "home_store": "B-1", "pay_rate": 15.0}]
board2 = run_list("", roster=no_punch)
ck("…and it STILL runs for the row that needs the evidence", SCANS == [1], SCANS)
ck("…whose row is added at 0.00 hours and flagged, not dropped",
   [(r["employee_id"], r["hours_source"], r["no_clock_record"]) for r in board2["rows"]
    if r["employee_id"] == "E900"] == [("E900", 0, True)],
   [r["employee_id"] for r in board2["rows"]])

# ── verdict ──────────────────────────────────────────────────────────────────────────────────────
print(f"\nharness_payroll_approval_save: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("\n" + "\n".join("  XX  " + f for f in FAIL))
    sys.exit(1)
print("  ok  a typed figure saves on its own, signs nothing, and cannot be lost in silence")
