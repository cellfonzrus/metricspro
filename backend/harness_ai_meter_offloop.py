"""PROOF: the AI usage meter never blocks the event loop, never loses spend silently, and never
under-reports to the mig-972 budget cap.

Owner 2026-09-06: *"fix the slowness for to ai billing"*. The slowness was structural, not a slow
query: `billing/ai_meter.record()` ended in a synchronous PostgREST insert and is called bare from
`async def` handlers in seven modules. On single-worker uvicorn a synchronous insert inside a
coroutine occupies the ONE event loop for its whole duration, so every other request on the process —
/health included — waits behind an invoice OCR's billing write. Worst case is the postgrest client
timeout (120s; db_resilience never retries a POST): a ~2-minute platform-wide freeze. Same defect
class as the SEV-1 of 2026-07-30, two orders of magnitude smaller.

`harness_agency_ocr_async.py` and `harness_closing_ocr_async.py` assert the property at their own OCR
call sites. THIS file is the authoritative proof of the shared contract they both depend on, because
the fix is one buffered sink owned by billing rather than nine hand-patched call sites:

  A. record() performs NO database I/O on the calling thread when a loop is running.
  B. The row still gets written — off the loop — and the row shape is unchanged from pre-fix.
  C. A write failure defers rather than loses; a sustained failure drops COUNTED, never silently.
  D. Mixed row shapes (the guard's audit row carries actor_email/created_at, the meter's does not)
     are grouped, because PostgREST rejects a batch whose objects do not share keys.
  E. Buffered rows still count against the mig-972 rate/budget cap — a burst inside one drain
     interval cannot slip past the ceiling just because its rows have not landed yet.
  F. core/ai_gate.audit() shares the same sink, so exactly one function writes core.ai_call_audit.

DB-FREE: the Supabase chokepoint is replaced with an in-memory fake via `_harness_dbfree`, which also
tripwires the real client constructor. Nothing here touches a live tenant.
"""
import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, detail))


# ── the fake database ────────────────────────────────────────────────────────────────────────────
class _Insert:
    def __init__(self, table, rows):
        self.table, self.rows = table, rows

    def execute(self):
        return self.table._do_insert(self.rows)


class _Table:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def insert(self, rows):
        return _Insert(self, rows)

    def _do_insert(self, rows):
        batch = rows if isinstance(rows, list) else [rows]
        self.db.calls.append({"table": self.name, "n": len(batch),
                              "thread": threading.current_thread().name,
                              "keysets": {tuple(sorted(r.keys())) for r in batch}})
        if self.db.fail:
            raise RuntimeError("simulated PostgREST failure")
        if self.db.delay:
            time.sleep(self.db.delay)
        self.db.rows.extend(batch)
        return type("R", (), {"data": batch})()


class _Schema:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return _Table(self.db, name)


class FakeDB:
    def __init__(self):
        self.rows, self.calls, self.fail, self.delay = [], [], False, 0.0

    def schema(self, _name):
        return _Schema(self)


DB = FakeDB()

import _harness_dbfree                                          # noqa: E402
_harness_dbfree.install(DB)

from app.modules.billing import ai_meter                        # noqa: E402


def reset():
    ai_meter.drain()
    DB.rows.clear()
    DB.calls.clear()
    DB.fail = False
    DB.delay = 0.0


class _Resp:
    class usage:
        input_tokens = 120
        output_tokens = 40


print("=" * 78)
print("A. record() does no database I/O on the caller's thread while a loop is running")
print("=" * 78)
reset()


async def _on_loop():
    """Exactly the shape of the real defect: a bare record() inside a coroutine."""
    loop_thread = threading.current_thread().name
    t0 = time.monotonic()
    DB.delay = 0.30            # stands in for a slow PostgREST; the old code paid this on the loop
    ai_meter.record("agency_ocr", "claude-x", _Resp(), org_id="org-a")
    elapsed = time.monotonic() - t0
    # The loop must be free immediately. Give the detached drain time to land, without blocking on it.
    for _ in range(60):
        if DB.rows:
            break
        await asyncio.sleep(0.02)
    return elapsed, loop_thread


_elapsed, _loop_thread = asyncio.run(_on_loop())
check("A1 record() returns immediately — the 300ms write is NOT paid on the event loop "
      "(pre-fix this was the whole insert, up to the 120s postgrest timeout)",
      _elapsed < 0.10, "record() took %.3fs on the loop" % _elapsed)
check("A2 the row was written anyway — off the loop, not skipped",
      len(DB.rows) == 1, "rows written: %d" % len(DB.rows))
check("A3 the write ran on a WORKER thread, never the loop thread",
      bool(DB.calls) and all(c["thread"] != _loop_thread for c in DB.calls),
      "insert threads: %s (loop was %s)" % ([c["thread"] for c in DB.calls], _loop_thread))

print()
print("=" * 78)
print("B. The row still says exactly what it said before the fix")
print("=" * 78)
row = DB.rows[0] if DB.rows else {}
check("B1 org / purpose / model / tokens are unchanged",
      row.get("org_id") == "org-a" and row.get("purpose") == "agency_ocr"
      and row.get("model") == "claude-x" and row.get("input_tokens") == 120
      and row.get("output_tokens") == 40, str(row))
check("B2 allowed defaults true and deny_code stays null (metering is not authorization)",
      row.get("allowed") is True and row.get("deny_code") is None, str(row))
check("B3 build_row() is pure — it returns the row and writes nothing",
      (lambda before: (ai_meter.build_row("x", org_id="org-a"), len(DB.rows) == before)[1])(len(DB.rows)),
      "build_row() wrote to the database")

print()
print("=" * 78)
print("C. Failure defers; only a sustained failure drops, and a drop is counted")
print("=" * 78)
reset()
DB.fail = True
ai_meter.enqueue(ai_meter.build_row("agency_ocr", org_id="org-a"))
ai_meter.flush_now()
check("C1 a failed write is restored to the buffer, not lost",
      ai_meter.size() == 1 and not DB.rows, "pending=%d written=%d" % (ai_meter.size(), len(DB.rows)))
DB.fail = False
ai_meter.flush_now()
check("C2 the next drain writes the deferred row (a database blip costs nothing)",
      ai_meter.size() == 0 and len(DB.rows) == 1,
      "pending=%d written=%d" % (ai_meter.size(), len(DB.rows)))

reset()
_before_dropped = ai_meter.dropped()
_cap = ai_meter.MAX_PENDING
for i in range(_cap + 5):
    ai_meter.enqueue(ai_meter.build_row("agency_ocr", org_id="org-a"))
check("C3 the buffer is bounded — memory cannot grow without limit while the database is down",
      ai_meter.size() == _cap, "pending=%d cap=%d" % (ai_meter.size(), _cap))
check("C4 rows lost past the cap are COUNTED, so an under-reported invoice is visible",
      ai_meter.dropped() - _before_dropped == 5,
      "dropped delta=%d" % (ai_meter.dropped() - _before_dropped))
reset()

print()
print("=" * 78)
print("D. Mixed row shapes are grouped (PostgREST rejects a batch with unequal keys)")
print("=" * 78)
reset()
ai_meter.enqueue(ai_meter.build_row("agency_ocr", org_id="org-a"))                       # meter shape
ai_meter.enqueue(ai_meter.build_row("agency_ocr", org_id="org-a"))                       # meter shape
ai_meter.enqueue({**ai_meter.build_row("remediation_diagnose", org_id="org-a"),
                  "actor_email": "a@b.c", "created_at": "2026-09-06T00:00:00+00:00"})    # guard shape
ai_meter.flush_now()
check("D1 all three rows are written",
      len(DB.rows) == 3, "written=%d" % len(DB.rows))
check("D2 one round trip per SHAPE, not per row (2 shapes -> 2 inserts)",
      len(DB.calls) == 2, "inserts=%d" % len(DB.calls))
check("D3 every batch is internally uniform, which is what PostgREST requires",
      all(len(c["keysets"]) == 1 for c in DB.calls),
      "keysets per insert: %s" % [len(c["keysets"]) for c in DB.calls])

print()
print("=" * 78)
print("E. Buffered rows still count against the mig-972 rate / budget cap")
print("=" * 78)
reset()
for _ in range(3):
    ai_meter.enqueue(ai_meter.build_row("remediation_diagnose", org_id="org-a",
                                        input_tokens=100, output_tokens=10))
ai_meter.enqueue(ai_meter.build_row("remediation_diagnose", org_id="org-b"))
ai_meter.enqueue(ai_meter.build_row("agency_ocr", org_id="org-a"))
check("E1 pending_rows() is org-scoped AND purpose-scoped — one tenant never sees another's spend",
      len(ai_meter.pending_rows(org_id="org-a", purpose="remediation_diagnose")) == 3,
      "got %d" % len(ai_meter.pending_rows(org_id="org-a", purpose="remediation_diagnose")))
check("E2 reading the buffer does not consume it (the rows must still be written)",
      ai_meter.size() == 5, "pending=%d" % ai_meter.size())

from app.modules.core import ai_gate as gate                     # noqa: E402
from app.modules.core import control_box as cbx                  # noqa: E402


class _NoRows:
    """A client whose ai_call_audit table is empty — i.e. the in-flight rows have not landed yet."""
    def schema(self, _n):
        return self

    def table(self, _n):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def gte(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return type("R", (), {"data": []})()


_seen = gate.recent_rows(_NoRows(), "org-a", "remediation_diagnose")
check("E3 ai_gate.recent_rows folds the in-flight rows in, so a burst inside one drain interval "
      "cannot slip past the per-hour cap",
      len(_seen) == 3, "guard saw %d row(s) for a tenant with 3 in flight" % len(_seen))
_usage = cbx.rollup_usage(_seen)
check("E4 those rows carry their tokens into the budget rollup (300 in + 30 out)",
      _usage.get("tokens_today") == 330, str(_usage))
reset()

print()
print("=" * 78)
print("F. The guard's audit write shares the same sink — ONE writer of core.ai_call_audit")
print("=" * 78)
reset()
async def _audit_on_loop():
    """The real shape: control-box `ai_triage`, storeops `post_document_extract` and remediation
    `_ai_diagnose` all call `_gate.audit(...)` from inside a coroutine."""
    me = threading.current_thread().name
    DB.delay = 0.30
    t0 = time.monotonic()
    ok = gate.audit(_NoRows(), ai_meter.build_row("control_box_triage", org_id="org-a"),
                    label="test")
    elapsed = time.monotonic() - t0
    for _ in range(60):
        if DB.rows:
            break
        await asyncio.sleep(0.02)
    return ok, elapsed, me


_ok, _el, _me = asyncio.run(_audit_on_loop())
check("F1 ai_gate.audit() does not insert on the event loop "
      "(pre-fix this was a bare PostgREST insert inside three async endpoints)",
      _ok is True and _el < 0.10 and DB.calls and all(c["thread"] != _me for c in DB.calls),
      "accepted=%s took=%.3fs on-loop-thread-inserts=%d"
      % (_ok, _el, sum(1 for c in DB.calls if c["thread"] == _me)))
DB.delay = 0.0
ai_meter.flush_now()
check("F2 the audit row lands in core.ai_call_audit through the meter's drain",
      len(DB.rows) == 1 and DB.calls[-1]["table"] == "ai_call_audit",
      "rows=%d table=%s" % (len(DB.rows), DB.calls[-1]["table"] if DB.calls else "-"))
check("F3 the audit is best-effort — an unusable row is refused and reported, never raised",
      gate.audit(_NoRows(), {"not_a_row": True}, label="test") is False,
      "a malformed row must be refused, not raised")

print()
print("=" * 78)
print(f"RESULT: {P} passed, {F} failed")
print("=" * 78)
sys.exit(1 if F else 0)
