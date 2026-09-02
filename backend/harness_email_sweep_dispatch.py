"""Harness for the /email-sweep/run-due BACKGROUND-DISPATCH fix (incident 2026-08-26→09-01).

Drives the REAL `email_run_due` + `_email_sweep_due_worker` against an in-memory fake Supabase and
stubbed sweeps. No network, no DB. What it proves:

  THE TICK CAN NEVER OUTLIVE pg_net AGAIN (root cause of the dead scheduler)
  • the handler returns WITHOUT awaiting a single sweep — sweeps are handed to a DEDICATED daemon
    thread with its own event loop (pg_net's http_post hangs up at 5000 ms; the old inline handler
    was cancelled mid-sweep at that point, and because next_run_at was only stamped after a sweep
    finished, the schedule never advanced — the dead-scheduler spiral of 2026-08-26→09-01)
  • the thread, not Starlette BackgroundTasks: BackgroundTasks runs on the SERVING loop, and the
    sweep's sync stretches (pandas, supabase .execute()) blocked it long enough for gunicorn's
    heartbeat timeout to KILL the worker mid-sweep (observed 2026-09-01 14:0x/15:0x: first
    mailbox's ingests landed, then the process died — no completion stamps, second mailbox starved)
  • the due list is SHUFFLED per tick, so a sweep that still dies partway rotates which mailbox
    goes first on the next tick instead of starving the same scan-order victim forever
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

from fastapi import HTTPException                      # noqa: E402
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

    real = (R.sb, R._run_email_sweep, R._data_freshness_monitor, R.verify_notify_secret,
            R._dispatch_email_sweep_worker)
    real_dispatch = R._dispatch_email_sweep_worker
    dispatched = []   # batches handed to the dedicated sweep thread

    fake = _FakeSB(due_rows)
    R.sb = lambda: fake
    R._run_email_sweep = fake_sweep
    R._data_freshness_monitor = fake_monitor
    R.verify_notify_secret = lambda s: s == 'sekret'
    R._dispatch_email_sweep_worker = lambda due: dispatched.append(list(due))
    try:
        print("── 1. wrong secret: 403, no writes, no dispatched work ─────────────────────────")
        try:
            asyncio.run(R.email_run_due(x_notify_secret='wrong'))
            check("wrong secret raises", False)
        except HTTPException as e:
            check("wrong secret raises 403", e.status_code == 403)
        check("no schedule writes on 403", not [l for l in fake.log if l[0] == 'update'])
        check("no worker dispatched on 403", dispatched == [])

        print("── 2. the tick: advances ALL schedules up front, sweeps NOTHING inline ─────────")
        resp = asyncio.run(R.email_run_due(x_notify_secret='sekret'))
        upds = [l for l in fake.log if l[0] == 'update']
        check("returns triggered=2", resp.get("triggered") == 2)
        check("no sweep ran inline", swept == [])
        check("freshness monitor did not run inline", monitored == [])
        check("next_run_at advanced for every due mailbox up front",
              len(upds) == 2 and all('next_run_at' in u[2] for u in upds))
        check("advance did not wait for sweep status", all(set(u[2]) == {'next_run_at'} for u in upds))
        check("worker dispatched exactly once, as one batch", len(dispatched) == 1)
        check("dispatched batch carries every due mailbox (any order — shuffled on purpose)",
              sorted((c['org_id'], c['account']) for c in dispatched[0])
              == sorted((c['org_id'], c['account']) for c in due_rows))

        print("── 3. the background worker: per-mailbox isolation, monitor best-effort ────────")
        n_upd_before = len([l for l in fake.log if l[0] == 'update'])
        # run the real worker directly on the dispatched batch — this is what the thread executes
        asyncio.run(R._email_sweep_due_worker([_due(HOUSE, 'default'), _due(TEN, 'default')]))
        check("worker swept exactly the given batch, in order",
              swept == [(HOUSE, 'default'), (TEN, 'default')])
        crash_upds = [l for l in fake.log if l[0] == 'update'][n_upd_before:]
        check("crashed mailbox stamped last_status on its row",
              any('last_status' in u[2] and 'sweep crashed' in str(u[2].get('last_status')) for u in crash_upds))
        check("worker never re-touches next_run_at", all('next_run_at' not in u[2] for u in crash_upds))
        check("freshness monitor ran once per swept org", sorted(monitored) == sorted([HOUSE, TEN]))
        locks = [u[2].get('sweeping_since') for u in crash_upds if 'sweeping_since' in u[2]]
        check("lock stamped at start and cleared at end for BOTH mailboxes (crash included)",
              len(locks) == 4 and locks[0] and locks[1] is None and locks[2] and locks[3] is None)

        print("── 4. empty due set: fast no-op, nothing dispatched ────────────────────────────")
        fake.rows = []
        n_disp = len(dispatched)
        resp = asyncio.run(R.email_run_due(x_notify_secret='sekret'))
        check("triggered=0 on empty due", resp.get("triggered") == 0)
        check("no worker dispatched on empty due", len(dispatched) == n_disp)

        print("── 5. the real dispatcher runs the worker on a separate daemon thread ──────────")
        R._dispatch_email_sweep_worker = real_dispatch
        import threading as _th
        before = {t.name for t in _th.enumerate()}
        swept.clear()
        real_dispatch([_due(TEN, 'default')])
        deadline = __import__('time').time() + 5
        while __import__('time').time() < deadline and (TEN, 'default') not in swept:
            __import__('time').sleep(0.05)
        check("dedicated thread executed the sweep off the serving loop", (TEN, 'default') in swept)
    finally:
        (R.sb, R._run_email_sweep, R._data_freshness_monitor, R.verify_notify_secret,
         R._dispatch_email_sweep_worker) = real

    print("── 6. per-mailbox lock: a mailbox already mid-sweep is skipped, stale locks are not ─")
    import datetime as _dt
    fresh = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=5)).isoformat()
    stale = (_dt.datetime.now(_dt.timezone.utc)
             - _dt.timedelta(minutes=R.EMAIL_SWEEP_LOCK_STALE_MINUTES + 30)).isoformat()
    check("fresh sweeping_since reads as in progress", R._email_sweep_in_progress({'sweeping_since': fresh}))
    check("stale sweeping_since reads as NOT in progress (crash self-heals)",
          not R._email_sweep_in_progress({'sweeping_since': stale}))
    check("absent/garbage stamps read as NOT in progress (fail-open)",
          not R._email_sweep_in_progress({}) and not R._email_sweep_in_progress({'sweeping_since': 'nope'}))
    fake2 = _FakeSB([])
    swept2, R.sb, R._run_email_sweep = [], (lambda: fake2), None

    async def sweep2(org_id, account='default'):
        swept2.append((org_id, account))
        return {"ok": True}
    R._run_email_sweep = sweep2
    locked = dict(_due(HOUSE, 'default'), sweeping_since=fresh)
    free = dict(_due(TEN, 'default'))
    asyncio.run(R._email_sweep_due_worker([locked, free]))
    check("locked mailbox skipped, free mailbox still swept", swept2 == [(TEN, 'default')])
    lock_writes = [u for u in fake2.log if u[0] == 'update' and 'sweeping_since' in u[2]]
    lock_orgs = {v for u in fake2.log if u[0] == 'eq' and u[2] == 'org_id' for v in [u[3]]}
    check("only the swept mailbox's row is stamped (stamp + clear), never the locked one's",
          len(lock_writes) == 2 and lock_writes[0][2]['sweeping_since']
          and lock_writes[1][2]['sweeping_since'] is None and lock_orgs == {TEN})

    print("── 7. retry cap: exhausted files stop being re-fetched — visibly, never silently ──")
    K = R.SWEEP_MAX_NONTERMINAL_ATTEMPTS
    seen = ([{'message_id': 'm-ok', 'filename': 'a.xlsx', 'rows_saved': 5, 'status': 'ok'}]
            + [{'message_id': 'm-dup', 'filename': 'b.xlsx', 'rows_saved': 0, 'status': 'duplicate'}]
            + [{'message_id': 'm-retry', 'filename': 'c.xlsx', 'rows_saved': 0, 'status': 'skipped'}] * (K - 1)
            + [{'message_id': 'm-dead', 'filename': 'd.xlsx', 'rows_saved': 0, 'status': 'error'}] * K)
    already, retryable, exhausted = R._sweep_dedup_sets(seen, lambda r: (r['message_id'], r['filename']))
    check("really-ingested and terminal-zero files are done",
          {('m-ok', 'a.xlsx'), ('m-dup', 'b.xlsx')} <= already)
    check("a file below the cap keeps retrying", ('m-retry', 'c.xlsx') in retryable)
    check("a file AT the cap is exhausted and no longer fetched",
          ('m-dead', 'd.xlsx') in exhausted and ('m-dead', 'd.xlsx') in already
          and ('m-dead', 'd.xlsx') not in retryable)
    check("giving up is visible in the status line",
          f"given up after {K} failed attempts" in R._sweep_status_suffix([], exhausted=len(exhausted)))
    check("no exhausted files → no give-up line", 'given up' not in R._sweep_status_suffix([], exhausted=0))

    print(f"\n{_pass} passed, {_fail} failed")
    if FAILED:
        print("FAILED:", *FAILED, sep="\n  - ")
        sys.exit(1)


if __name__ == "__main__":
    run()
