"""Offline proof (no live DB/network) for the clock-in/out reliability fix (PART A of the SPEC).

PAYROLL-CRITICAL: punches are labor hours. Under saturation the kiosk/mobile retried punches with no
idempotency key, so a slow response could open a SECOND concurrent punch (double clock-in) and a
clock-out retry after a successful close returned a confusing 404. Migration 912 adds:
  * timelog.client_request_id (a client UUID, STABLE across retries of the SAME punch),
  * a unique idempotency index (org_id, employee_id, client_request_id), and
  * a unique ONE-OPEN index (org_id, employee_id) WHERE clock_out IS NULL.
The clock_in / clock_out handlers now honour THE CONTRACT (see SPEC.md):
  (a) same client_request_id twice  -> ONE row; the 2nd call returns idempotent_replay:true with the
      identical outcome (never a 2nd row).
  (b) a 2nd clock-in while one is already open (the one-open index conflict, i.e. a concurrent double
      clock-in) -> returns the EXISTING open row as success, NO 2nd open row.
  (c) clock-out retry after a successful close -> SUCCESS (the closed row), NOT 404.
  (d) NO client_request_id -> today's behavior, byte-for-byte (a 2nd concurrent clock-in still 409s).

This drives the REAL clock_in / clock_out handlers end-to-end against an in-memory fake Supabase client
that ENFORCES both unique indexes exactly as Postgres would (raising 23505 on a duplicate), so the
handlers' 23505 re-select paths are genuinely exercised.

Run: `python3 harness_timeclock_idempotency.py` from backend/.
"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


# ── A 23505 unique-violation, shaped like the PostgREST/Postgres one the handlers detect ─────────────
class UniqueViolation(Exception):
    def __init__(self, msg):
        super().__init__({"code": "23505", "message": msg})
        self.code = "23505"
        self.message = msg


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, key):
        self.store, self.key, self.filters = store, key, []
        self._mode, self._payload, self._limit, self._order = None, None, None, None

    def select(self, *_a, **_k):
        self._mode = self._mode or "select"; return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def in_(self, k, vals):
        self.filters.append(("in", k, set(str(x) for x in vals))); return self

    def is_(self, k, v):
        self.filters.append(("is", k, v)); return self

    def gte(self, k, v):
        self.filters.append(("gte", k, v)); return self

    def lt(self, k, v):
        self.filters.append(("lt", k, v)); return self

    def lte(self, k, v):
        self.filters.append(("lte", k, v)); return self

    def order(self, k, desc=False):
        self._order = (k, desc); return self

    def limit(self, n):
        self._limit = n; return self

    def insert(self, payload):
        self._mode = "insert"; self._payload = payload; return self

    def update(self, payload):
        self._mode = "update"; self._payload = payload; return self

    def delete(self):
        self._mode = "delete"; return self

    def _matches(self, row):
        for kind, k, v in self.filters:
            rv = row.get(k)
            if kind == "eq" and str(rv) != str(v):
                return False
            if kind == "in" and str(rv) not in v:
                return False
            if kind == "is" and v == "null" and rv is not None:
                return False
            if kind == "gte" and str(rv) < str(v):
                return False
            if kind == "lt" and str(rv) >= str(v):
                return False
            if kind == "lte" and str(rv) > str(v):
                return False
        return True

    def _enforce_unique(self, rows, row):
        """Reproduce migration 912's two partial-unique indexes on storeops.timelog."""
        if self.key != ("storeops", "timelog"):
            return
        org, emp = row.get("org_id"), row.get("employee_id")
        cri = row.get("client_request_id")
        # timelog_client_req_idx: (org_id, employee_id, client_request_id) WHERE client_request_id NOT NULL
        if cri is not None:
            for r in rows:
                if r.get("org_id") == org and r.get("employee_id") == emp and r.get("client_request_id") == cri:
                    raise UniqueViolation("duplicate key value violates unique constraint \"timelog_client_req_idx\"")
        # timelog_one_open_idx: (org_id, employee_id) WHERE clock_out IS NULL
        if row.get("clock_out") is None:
            for r in rows:
                if r.get("org_id") == org and r.get("employee_id") == emp and r.get("clock_out") is None:
                    raise UniqueViolation("duplicate key value violates unique constraint \"timelog_one_open_idx\"")

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        if self._mode == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for p in payload:
                row = dict(p)
                self._enforce_unique(rows, row)
                row.setdefault("id", f"row{len(rows) + len(out) + 1}")
                out.append(row)
            rows.extend(out)
            return Result(out)
        matched = [r for r in rows if self._matches(r)]
        if self._mode == "update":
            for r in matched:
                r.update(self._payload)
            return Result(matched)
        if self._mode == "delete":
            self.store[self.key] = [r for r in rows if not self._matches(r)]
            return Result(matched)
        if self._order:
            k, desc = self._order
            matched = sorted(matched, key=lambda r: (r.get(k) is None, r.get(k)), reverse=desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        return Result(matched)


class FakeSchema:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def table(self, t):
        return FakeQuery(self.client.store, (self.name, t))


class FakeClient:
    def __init__(self):
        self.store = {}

    def schema(self, name):
        return FakeSchema(self, name)

    def table(self, t):
        return FakeQuery(self.store, ("storeops", t))

    def seed(self, schema, table, rows):
        self.store[(schema, table)] = [dict(r) for r in rows]


# A no-op BackgroundTasks so clock_out's fire-and-forget missed-closing detection doesn't run a real
# cross-module import on this fake DB (mirrors FastAPI's interface — add_task collects, never blocks).
class FakeBG:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *a, **k):
        self.tasks.append((fn, a, k))


fake = FakeClient()

import app.modules.storeops.router as R  # noqa: E402

R.get_supabase = lambda: fake
R.sb = lambda: fake.schema("storeops")

ORG = "org-tc-1"
# Identity is resolved straight to (ORG, E1) — this harness is about idempotency, not auth.
R._caller_identity = lambda auth: (ORG, "E1")


# ── Controllable fake clock so sessions have real positive hours (a genuine gap between in and out) ──
class _FakeDateTime:
    _now = datetime.now(timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._now if tz is None else cls._now.astimezone(tz)

    @staticmethod
    def fromisoformat(s):
        return datetime.fromisoformat(s)


def advance_clock(hours):
    _FakeDateTime._now = _FakeDateTime._now + timedelta(hours=hours)


R.datetime = _FakeDateTime

_biz_tz = R._biz_tz_for(ORG)
_today = datetime.now(timezone.utc).astimezone(_biz_tz).date()
_FakeDateTime._now = datetime(_today.year, _today.month, _today.day, 8, 0, 0, tzinfo=_biz_tz).astimezone(timezone.utc)
TODAY = _today.isoformat()
AUTH = "Bearer x"


def reset(home_store="S1"):
    fake.store.clear()
    # closing gate OFF so clock-out is never gated — keeps these tests purely about idempotency/dedupe.
    fake.seed("storeops", "tenants", [{"org_id": ORG, "closing_gate_enabled": False}])
    fake.seed("storeops", "employees",
              [{"org_id": ORG, "employee_id": "E1", "id": 201, "name": "Alice", "home_store": home_store,
                "pay_rate": 20.0, "is_active": True}])
    fake.seed("storeops", "timelog", [])
    _FakeDateTime._now = datetime(_today.year, _today.month, _today.day, 8, 0, 0, tzinfo=_biz_tz).astimezone(timezone.utc)


def open_count():
    return sum(1 for r in fake.store.get(("storeops", "timelog"), []) if r.get("clock_out") is None)


def row_count():
    return len(fake.store.get(("storeops", "timelog"), []))


def ci(cid=None, store="S1"):
    body = R.ClockInIn(store_code=store, client_request_id=cid)
    return R.clock_in(body, authorization=AUTH, org_id=ORG)


def co(cid=None):
    body = R.ClockOutIn(client_request_id=cid)
    return R.clock_out(body, FakeBG(), authorization=AUTH, org_id=ORG)


# ══ (a) SAME client_request_id twice -> ONE row, 2nd is idempotent_replay with identical outcome ═════
reset()
a1 = ci("punch-A")
a2 = ci("punch-A")   # a retry of the very same punch
check("a1 first clock-in succeeds", a1.get("success") is True, a1)
check("a2 replay succeeds", a2.get("success") is True, a2)
check("a3 replay is flagged idempotent_replay:true", a2.get("idempotent_replay") is True, a2)
check("a4 first call is NOT flagged idempotent_replay (fresh insert)", "idempotent_replay" not in a1, a1)
check("a5 exactly ONE timelog row (no double punch)", row_count() == 1, fake.store[("storeops", "timelog")])
check("a6 replay returns the SAME entry_id + identical data payload", a2["data"] == a1["data"], (a1, a2))

# (a') the same-id 23505 path (a true TOCTOU race where the up-front check misses): force the pre-insert
# idempotency SELECT to see nothing so the insert itself collides, and prove the handler re-selects.
reset()
ci("punch-A2")
_orig_sb = R.sb
_hide = {"on": True}
_real_schema = fake.schema
def _blind_schema(name):
    sch = _real_schema(name)
    _orig_table = sch.table
    def _t(t):
        q = _orig_table(t)
        if _hide["on"] and t == "timelog":
            _orig_exec = q.execute
            def _ex():
                res = _orig_exec()
                # blind ONLY the up-front idempotency lookup (a select filtering on client_request_id)
                if q._mode == "select" and any(f[1] == "client_request_id" for f in q.filters):
                    return Result([])
                return res
            q.execute = _ex
        return q
    sch.table = _t
    return sch
fake.schema = _blind_schema
R.sb = lambda: fake.schema("storeops")
aX = ci("punch-A2")   # up-front check is blinded -> reaches insert -> 23505 -> re-select recovers
fake.schema = _real_schema
R.sb = _orig_sb
check("a7 same-id 23505 race recovers to a replay (no 2nd row)",
      aX.get("success") is True and aX.get("idempotent_replay") is True and row_count() == 1, (aX, row_count()))

# ══ (b) 2nd clock-in while one is already open (one-open index) -> existing open row, NO 2nd open ════
reset()
b1 = ci("punch-B1")
b2 = ci("punch-B2")   # DIFFERENT id, but an open punch already exists -> one-open 23505 -> collapse
check("b1 first clock-in opens a punch", b1.get("success") is True and open_count() == 1, (b1, open_count()))
check("b2 second clock-in (distinct id, already open) returns success", b2.get("success") is True, b2)
check("b3 it is flagged idempotent_replay (collapsed onto the open row, not a new punch)",
      b2.get("idempotent_replay") is True, b2)
check("b4 it returns the EXISTING open row's entry_id", b2["data"]["entry_id"] == b1["data"]["entry_id"], (b1, b2))
check("b5 still exactly ONE open row (no double open)", open_count() == 1 and row_count() == 1,
      fake.store[("storeops", "timelog")])

# ══ (c) clock-out retry after a successful close -> SUCCESS (the closed row), NOT 404 ════════════════
reset()
ci("punch-C-in")
advance_clock(5.0)
c1 = co("punch-C-out")
c2 = co("punch-C-out")   # a retry of the same clock-out punch, after it already closed the row
check("c1 clock-out succeeds and stamps real hours (5h)", c1.get("success") is True and c1["data"]["hours"] == 5.0, c1)
check("c2 retry after a successful close returns SUCCESS (not 404)", c2.get("success") is True, c2)
check("c3 retry is flagged idempotent_replay:true", c2.get("idempotent_replay") is True, c2)
check("c4 retry returns the identical closed-row outcome (same hours/time)", c2["data"] == c1["data"], (c1, c2))
check("c5 no re-close / no extra row — one closed row, zero open", row_count() == 1 and open_count() == 0,
      fake.store[("storeops", "timelog")])

# clock-out with a NEVER-SEEN id and no open row still 404s (a genuine miss is not silently swallowed)
reset()
try:
    co("punch-never")
    check("c6 clock-out with unknown id and no open punch still 404s", False, "no exception raised")
except Exception as e:
    check("c6 clock-out with unknown id and no open punch still 404s", getattr(e, "status_code", None) == 404, e)

# ══ (d) NO client_request_id -> today's behavior unchanged (back-compat) ═════════════════════════════
reset()
d1 = ci(None)
check("d1 clock-in with no id succeeds and carries NO idempotent_replay field", d1.get("success") is True
      and "idempotent_replay" not in d1, d1)
try:
    ci(None)   # a 2nd concurrent clock-in with no id must STILL 409 (unchanged pre-check guard)
    check("d2 second no-id clock-in still 409s (back-compat guard preserved)", False, "no exception raised")
except Exception as e:
    check("d2 second no-id clock-in still 409s (back-compat guard preserved)",
          getattr(e, "status_code", None) == 409, e)
advance_clock(2.0)
d3 = co(None)
check("d3 clock-out with no id succeeds and carries NO idempotent_replay field", d3.get("success") is True
      and "idempotent_replay" not in d3, d3)
try:
    co(None)   # no open row, no id -> the original 404 is unchanged
    check("d4 second no-id clock-out with no open punch still 404s", False, "no exception raised")
except Exception as e:
    check("d4 second no-id clock-out with no open punch still 404s", getattr(e, "status_code", None) == 404, e)
check("d5 exactly one row, closed, positive hours (a clean single session)",
      row_count() == 1 and open_count() == 0 and (fake.store[("storeops", "timelog")][0].get("hours") or 0) > 0,
      fake.store[("storeops", "timelog")])

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
