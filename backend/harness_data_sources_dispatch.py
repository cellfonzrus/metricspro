"""Harness for the /data-sources/sweep/run-due ADVANCE-THEN-BACKGROUND fix (2026-09-01).

Drives the REAL `data_sources_run_due` against an in-memory fake Supabase with the pull worker and
gates stubbed. No network, no DB, no Playwright. What it proves (the email-sweep incident pattern,
applied to the last inline cron entrypoint):

  THE CRON TICK ANSWERS pg_net IN MILLISECONDS
  • with a valid X-Notify-Secret the handler advances EVERY schedulable due source's next_run_at UP
    FRONT (via _source_reschedule), dispatches the pulls to the dedicated thread ONCE as one batch,
    and returns {"triggered": N} without awaiting a single pull — so a portal pull that outlives
    pg_net's 5000 ms hangup can no longer be cancelled mid-flight or stall its source's schedule
  • sources whose processor has NO wired scraper are neither advanced nor dispatched (same skip as
    the old inline loop — they were never pulled, so their schedule state stays untouched)
  • an empty/none-actionable due set dispatches nothing

  THE INTERACTIVE PATH IS UNCHANGED
  • without the secret the call requires an org, runs the pull worker INLINE and returns its full
    per-source result — a human asked and is waiting; no schedule is advanced up front

  THE REAL DISPATCHER USES A DEDICATED THREAD
  • _dispatch_data_sources_worker demonstrably executes the worker off the calling thread

Run: `python3 harness_data_sources_dispatch.py` from the backend dir.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

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


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        return self

    def or_(self, *a, **k):
        return self

    def execute(self):
        return type('X', (), {'data': [dict(r) for r in self.rows]})()


class _FakeSB:
    def __init__(self, rows):
        self.rows = rows

    def schema(self, s):
        return self

    def table(self, t):
        return _Q(self.rows)


def _src(sid, proc, org=HOUSE):
    return {'id': sid, 'org_id': org, 'enabled': True, 'processor': proc,
            'frequency': 'daily', 'hour': 7, 'next_run_at': '2026-08-26T03:00:00+00:00'}


def run():
    rows = [_src('s1', 'vidapay'), _src('s2', 'no_scraper_wired'), _src('s3', 'vidapay')]

    rescheduled = []   # (source_id, org_id) advanced up front
    dispatched = []    # batches handed to the dedicated thread
    pulled = []        # worker invocations (rows, org_id)

    async def fake_worker(batch, org_id):
        pulled.append(([s['id'] for s in batch], org_id))
        return {"ok": True, "ran": [{"id": s['id']} for s in batch], "count": len(batch)}

    real = (R.sb, R.verify_notify_secret, R.require_org, R.require_browser_service,
            R._SOURCE_SCRAPERS, R._source_reschedule, R._data_sources_pull_worker,
            R._dispatch_data_sources_worker)
    real_dispatch = R._dispatch_data_sources_worker
    R.sb = lambda: _FakeSB(rows)
    R.verify_notify_secret = lambda s: s == 'sekret'
    R.require_org = lambda o: None
    R.require_browser_service = lambda: None
    R._SOURCE_SCRAPERS = {'vidapay': object()}
    R._source_reschedule = lambda client, sid, oid, nxt: rescheduled.append((sid, oid, bool(nxt)))
    R._data_sources_pull_worker = fake_worker
    R._dispatch_data_sources_worker = lambda batch, org_id: dispatched.append([s['id'] for s in batch])
    try:
        print("── 1. cron tick: advances schedulable sources up front, pulls NOTHING inline ───")
        resp = asyncio.run(R.data_sources_run_due(org_id=HOUSE, x_notify_secret='sekret'))
        check("returns triggered=2 (only scraper-wired sources)", resp.get("triggered") == 2)
        check("no pull ran inline", pulled == [])
        check("next_run_at advanced up front for exactly the wired sources",
              sorted(r[0] for r in rescheduled) == ['s1', 's3'] and all(r[2] for r in rescheduled))
        check("unwired source neither advanced nor dispatched",
              's2' not in [r[0] for r in rescheduled] and all('s2' not in b for b in dispatched))
        check("worker dispatched once, as one batch of the wired sources",
              dispatched == [['s1', 's3']])

        print("── 2. cron tick with nothing actionable: no dispatch ───────────────────────────")
        R._SOURCE_SCRAPERS = {}
        n_disp = len(dispatched)
        resp = asyncio.run(R.data_sources_run_due(org_id=HOUSE, x_notify_secret='sekret'))
        check("triggered=0 when no source has a wired scraper", resp.get("triggered") == 0)
        check("nothing dispatched", len(dispatched) == n_disp)
        R._SOURCE_SCRAPERS = {'vidapay': object()}

        print("── 3. interactive path: inline worker, full results, no up-front advance ───────")
        rescheduled.clear()
        resp = asyncio.run(R.data_sources_run_due(org_id=HOUSE, x_notify_secret=''))
        check("interactive call ran the worker inline with ALL due rows",
              pulled == [(['s1', 's2', 's3'], HOUSE)])
        check("interactive call returns the worker's own result", resp.get("count") == 3)
        check("interactive call does not advance schedules up front", rescheduled == [])

        print("── 4. the real dispatcher runs the worker on a separate daemon thread ──────────")
        R._dispatch_data_sources_worker = real_dispatch
        pulled.clear()
        real_dispatch([_src('s9', 'vidapay')], HOUSE)
        deadline = __import__('time').time() + 5
        while __import__('time').time() < deadline and not pulled:
            __import__('time').sleep(0.05)
        check("dedicated thread executed the worker off the calling thread",
              pulled == [(['s9'], HOUSE)])
    finally:
        (R.sb, R.verify_notify_secret, R.require_org, R.require_browser_service,
         R._SOURCE_SCRAPERS, R._source_reschedule, R._data_sources_pull_worker,
         R._dispatch_data_sources_worker) = real

    print(f"\n{_pass} passed, {_fail} failed")
    if FAILED:
        print("FAILED:", *FAILED, sep="\n  - ")
        sys.exit(1)


if __name__ == "__main__":
    run()
