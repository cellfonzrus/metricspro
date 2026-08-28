"""Integration test for the morning lateness-alert orchestration (_run_lateness_alerts), with the DB
layer + email sender mocked. The pure planning (accountability_alerts.plan_emails) and aggregation
(accountability.aggregate) have their own self-tests; this proves the router glue: pay-period window →
attendance engine → hierarchy resolution → email plan → dedupe, end to end, in dry-run (sends nothing)."""
import asyncio
import datetime as _dt
import sys

import app.modules.storeops.router as R
from app.modules.notify.channels import email_resend

PASS, FAIL = [], []
def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" :: {detail}"))

TODAY = _dt.datetime.now(_dt.timezone.utc).date().isoformat()

# Fixture late incidents: Dana late TODAY + once earlier (2 this period); Evan late earlier only.
EXC = [
    {"exception_type": "late", "is_late": True, "employee_name": "Dana", "employee_id": "E1", "store_code": "S1",
     "work_date": TODAY, "actual_clock_in": TODAY + "T13:10:00+00:00", "minutes_late": 10},
    {"exception_type": "late", "is_late": True, "employee_name": "Dana", "employee_id": "E1", "store_code": "S1",
     "work_date": "2026-08-01", "actual_clock_in": "2026-08-01T13:05:00+00:00", "minutes_late": 5},
    {"exception_type": "late", "is_late": True, "employee_name": "Evan", "employee_id": "E2", "store_code": "S1",
     "work_date": "2026-08-02", "actual_clock_in": "2026-08-02T13:03:00+00:00", "minutes_late": 3},
]

# ── mock the DB-touching module globals ──────────────────────────────────────────────────────────
R._attendance_rows_for_range = lambda org_id, start, end, client=None: (EXC, {}, True, False)
R._managers_above_dm = lambda org_id, store: {
    "dm": [{"name": "Dee DM", "email": "dee@x.com"}],
    "above": [{"name": "Rita Regional", "email": "rita@x.com"}, {"name": "Vic VP", "email": "vic@x.com"}]}
R._biz_tz_for = lambda org_id: _dt.timezone.utc


class FakeQ:
    def __init__(self, data): self._data = data
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def insert(self, *a, **k): return self
    def update(self, *a, **k): return self
    def execute(self):
        class _R: pass
        r = _R(); r.data = self._data; return r


class FakeClient:
    def __init__(self, tenant, already_sent): self.tenant, self.already = tenant, already_sent
    def table(self, name):
        if name == "tenants": return FakeQ([self.tenant])
        if name == "alert_log": return FakeQ(self.already)   # [] = nothing sent yet
        return FakeQ([])


TENANT = {"org_id": "ORG", "lateness_alerts_enabled": True, "lateness_alert_time": "00:00",
          "work_week_start_dow": 0, "pay_period_type": "weekly", "timezone": "UTC"}

email_resend.is_configured = lambda: False   # dry run never sends anyway; belt & suspenders


async def main():
    R.sb = lambda: FakeClient(TENANT, [])
    res = await R._run_lateness_alerts(dry_run=True)     # respect_time True; send_time 00:00 → always due
    r0 = res["results"][0]
    planned = r0.get("planned") or []
    tos = {(p["kind"], p["to"]) for p in planned}
    ok("A1 morning summary goes to EACH manager above the DM (rita + vic)",
       ("manager_summary", "rita@x.com") in tos and ("manager_summary", "vic@x.com") in tos, tos)
    ok("A2 CAP email goes to the immediate DM (dee) for the employee late TODAY",
       ("cap", "dee@x.com") in tos, tos)
    caps = [p for p in planned if p["kind"] == "cap"]
    ok("A3 exactly ONE CAP (only Dana was late today, not Evan)", len(caps) == 1, [c["subject"] for c in caps])
    ok("A4 CAP names the pay-period count (Dana late 2×)", "2×" in caps[0]["subject"], caps[0]["subject"])
    ok("A5 dry run sends nothing", r0["sent"] == 0 and res["dry_run"] is True, r0)
    ok("A6 late_employees counted", r0["late_employees"] >= 1, r0)

    # respect_time gate: a send_time in the far future must NOT fire.
    R.sb = lambda: FakeClient({**TENANT, "lateness_alert_time": "23:59"}, [])
    res2 = await R._run_lateness_alerts(dry_run=True)
    ok("A7 not yet due (send_time 23:59) → tenant skipped", res2["ran"] == 0, res2)

    # disabled tenant is skipped when respect_enabled (the run-due path).
    R.sb = lambda: FakeClient({**TENANT, "lateness_alerts_enabled": False}, [])
    res3 = await R._run_lateness_alerts(respect_time=False, respect_enabled=True, dry_run=True)
    ok("A8 disabled tenant skipped on the run-due path", res3["ran"] == 0, res3)

    # run-now bypasses the enabled gate (respect_enabled=False) so an admin can preview.
    R.sb = lambda: FakeClient({**TENANT, "lateness_alerts_enabled": False}, [])
    res4 = await R._run_lateness_alerts(respect_time=False, respect_enabled=False, dry_run=True)
    ok("A9 run-now previews even when disabled", res4["ran"] == 1, res4)

    # dedupe: an alert_log row already present for a recipient marks it already_sent.
    dana_cap_key = None
    for p in planned:
        pass
    R.sb = lambda: FakeClient(TENANT, [{"id": "x"}])   # every lookup returns a row → all already sent
    res5 = await R._run_lateness_alerts(dry_run=True)
    p5 = res5["results"][0]["planned"] or []
    ok("A10 dedupe marks recipients already_sent when alert_log has a row",
       len(p5) > 0 and all(p["already_sent"] for p in p5), p5)


asyncio.run(main())
print(f"\n{'=' * 60}\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:", FAIL)
sys.exit(1 if FAIL else 0)
