"""Harness for the /email-sweep/run-due BACKGROUND-DISPATCH fix (incident 2026-08-26→09-01).

Drives the REAL `email_run_due` + `_email_sweep_due_worker` against an in-memory fake Supabase and
stubbed sweeps. No network, no DB. What it proves:

  THE TICK CAN NEVER OUTLIVE pg_net AGAIN (root cause of the dead scheduler)
  • the handler returns WITHOUT awaiting a single sweep — sweeps are queued on BackgroundTasks and
    run only after the response (pg_net's http_post hangs up at 5000 ms; the old inline handler was
    cancelled mid-sweep at that point, and because next_run_at was only stamped after a sweep
    finished, the schedule never advanced and every 15-min tick restarted the same ever-larger
    sweep and died again — silently, since CancelledError bypasses `except Exception`)
  • next_run_at is advanced UP FRONT for EVERY due mailbox — before any sweep work — so even a
    crashed/killed sweep retries at the mailbox's own cadence instead of re-entering the spiral

  SEMANTICS PRESERVED FROM THE INLINE VERSION
  • the worker sweeps exactly the due set, in order
  • per-mailbox isolation: a sweep that RAISES stamps `last_status` on ITS row and the loop moves
    on; the crash does not re-touch next_run_at (already advanced) and later mailboxes still sweep
  • the post-sweep data-freshness monitor still runs once per swept org, and its own failure is
    swallowed (best-effort, never affects the sweep result)

  GATES
  • a wrong X-Notify-Secret still 403s with NO schedule writes and NO queued work
  • an empty due set queues NO background task

Run: `python3 harness_email_sweep_dispatch.py` from the backend dir.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from fastapi import BackgroundTasks, HTTPException     # noqa: E402
from app.modules.commcalc import router as R           # noqa: E402

_pass = 0
_fail = 0
FAILED = []


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        FAILED.append(name)
        print(f"  FAIL  {name}")


HOUSE = "00000000-0000-0000-0000-000000000001"
TEN = "00000000-0000-0000-0000-0000000000aa"


# ── Fake Supabase: only the verbs email_run_due/_email_status_update use ─────────────────────────
class _Q:
    def __init__(self, rows, log, table):
        self.rows, self.log, self.table_ = rows, log, table
        self._upd = None

    def select(self, *a, **k):
        return self

    def update(self, upd):
        self._upd = upd
        return self

    def eq(self, k, v):
        self.log.append(('eq', self.table_, k, v)) if self._upd is not None else None
        self._last_eq = (k, v)
        return self

    def lte(self, k, v):
        return self

    def execute(self):
        if self._upd is not None:
            self.log.append(('update', self.table_, dict(self._upd)))
            return type('X', (), {'data': []})()
        return type('X', (), {'data': [dict(r) for r in self.rows]})()


class _FakeSB:
    def __init__(self, rows):
        self.rows, self.log = rows, []

    def schema(self, s):
        return self

    def table(self, t):
        return _Q(self.rows, self.log, t)


def _due(org, acct):
    return {'org_id': org, 'account': acct, 'enabled': True, 'frequency': 'hourly', 'hour': 7,
            'next_run_at': '2026-08-26T03:00:00+00:00'}


def run():
    due_rows = [_due(HOUSE, 'default'), _due(TEN, 'default')]

    swept = []          # (org, acct) in sweep order
    monitored = []      # org ids the freshness monitor saw

    async def fake_sweep(org_id, account='default'):
        swept.append((org_id, account))
        if org_id == HOUSE:
            raise RuntimeError("IMAP exploded")   # first mailbox crashes; second must still sweep
        return {"ok": True}

    async def fake_monitor(client, org_id):
        monitored.append(org_id)
        raise RuntimeError("monitor exploded")    # its failure must be swallowed

    real = (R.sb, R._run_email_sweep, R._data_freshness_monitor, R.verify_notify_secret)
    fake = _FakeSB(due_rows)
    R.sb = lambda: fake
    R._run_email_sweep = fake_sweep
    R._data_freshness_monitor = fake_monitor
    R.verify_notify_secret = lambda s: s == 'sekret'
    try:
        print("── 1. wrong secret: 403, no writes, no queued work ─────────────────────────────")
        bt = BackgroundTasks()
        try:
            asyncio.run(R.email_run_due(bt, x_notify_secret='wrong'))
            check("wrong secret raises", False)
        except HTTPException as e:
            check("wrong secret raises 403", e.status_code == 403)
        check("no schedule writes on 403", not [l for l in fake.log if l[0] == 'update'])
        check("no background task on 403", len(bt.tasks) == 0)

        print("── 2. the tick: advances ALL schedules up front, sweeps NOTHING inline ─────────")
        bt = BackgroundTasks()
        resp = asyncio.run(R.email_run_due(bt, x_notify_secret='sekret'))
        upds = [l for l in fake.log if l[0] == 'update']
        check("returns triggered=2", resp.get("triggered") == 2)
        check("no sweep ran inline", swept == [])
        check("freshness monitor did not run inline", monitored == [])
        check("next_run_at advanced for every due mailbox up front",
              len(upds) == 2 and all('next_run_at' in u[2] for u in upds))
        check("advance did not wait for sweep status", all(set(u[2]) == {'next_run_at'} for u in upds))
        check("exactly one background task queued", len(bt.tasks) == 1)

        print("── 3. the background worker: per-mailbox isolation, monitor best-effort ────────")
        n_upd_before = len([l for l in fake.log if l[0] == 'update'])
        asyncio.run(bt())     # run what the response left behind — this is what pg_net never waits for
        check("worker swept exactly the due set, in order",
              swept == [(HOUSE, 'default'), (TEN, 'default')])
        crash_upds = [l for l in fake.log if l[0] == 'update'][n_upd_before:]
        check("crashed mailbox stamped last_status on its row",
              any('last_status' in u[2] and 'sweep crashed' in str(u[2].get('last_status')) for u in crash_upds))
        check("worker never re-touches next_run_at", all('next_run_at' not in u[2] for u in crash_upds))
        check("freshness monitor ran once per swept org", sorted(monitored) == sorted([HOUSE, TEN]))

        print("── 4. empty due set: fast no-op, nothing queued ────────────────────────────────")
        fake.rows = []
        bt = BackgroundTasks()
        resp = asyncio.run(R.email_run_due(bt, x_notify_secret='sekret'))
        check("triggered=0 on empty due", resp.get("triggered") == 0)
        check("no background task on empty due", len(bt.tasks) == 0)
    finally:
        R.sb, R._run_email_sweep, R._data_freshness_monitor, R.verify_notify_secret = real

    print(f"\n{_pass} passed, {_fail} failed")
    if FAILED:
        print("FAILED:", *FAILED, sep="\n  - ")
        sys.exit(1)


if __name__ == "__main__":
    run()
