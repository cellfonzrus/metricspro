"""Offline proof (no live DB / network / email) for BLOCK-AND-HOLD unscheduled clock-in + AUTO
clock-in on manager schedule-save (owner-approved 2026-08-24; migration 915).

PAYROLL-CRITICAL. Owner decisions (locked):
  1. FULLY BLOCKED — a rep NOT scheduled at the store they tap at CANNOT accrue time. Their tap opens
     NO storeops.timelog punch and inserts NO zero-hour shift shell. It is captured as ONE pending
     request (storeops.pending_clockin) that notifies the store's manager. Zero time until scheduled.
  2. AUTO CLOCK-IN — when the manager SAVES a schedule covering the held (employee, store, work_date),
     the pending row activates: an OPEN timelog punch is created back-dated to the ORIGINAL tap time.
  3/4. professional pending message + manager notification with a pre-filled deep-link.

This drives the REAL clock_in / create_shift handlers end-to-end against an in-memory fake Supabase
that ENFORCES migration 912's timelog indexes AND migration 915's pending_clockin indexes exactly as
Postgres would (raising 23505 on a duplicate), so the handlers' dedupe / one-open paths are genuinely
exercised. The manager notification is stubbed to a COUNTER (no email / cross-module), so "exactly one
notification per held tap" is asserted directly.

Proves: (a) unscheduled tap -> pending_schedule_approval, NO punch, NO shift shell, ONE pending + ONE
notification with the correct store-local start; (b) a retap is idempotent (one pending, one notify);
(c) schedule-save activates -> exactly one open punch at the original tap time, request=activated, a
second save is a no-op (no double punch); (d) a genuinely SCHEDULED rep clocks in normally (unchanged,
no pending); (e) store-local tz for a Central store (a 7pm-Central tap buckets to the right day + start).

Run: `python3 harness_pending_clockin.py` from backend/.
"""
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


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
            if kind == "lte" and str(rv) > str(v):
                return False
        return True

    def _enforce_unique(self, rows, row):
        # migration 912 — storeops.timelog
        if self.key == ("storeops", "timelog"):
            org, emp, cri = row.get("org_id"), row.get("employee_id"), row.get("client_request_id")
            if cri is not None:
                for r in rows:
                    if r.get("org_id") == org and r.get("employee_id") == emp and r.get("client_request_id") == cri:
                        raise UniqueViolation("timelog_client_req_idx")
            if row.get("clock_out") is None:
                for r in rows:
                    if r.get("org_id") == org and r.get("employee_id") == emp and r.get("clock_out") is None:
                        raise UniqueViolation("timelog_one_open_idx")
        # migration 915 — storeops.pending_clockin
        if self.key == ("storeops", "pending_clockin"):
            org, emp, cri = row.get("org_id"), row.get("employee_id"), row.get("client_request_id")
            if cri is not None:
                for r in rows:
                    if r.get("org_id") == org and r.get("employee_id") == emp and r.get("client_request_id") == cri:
                        raise UniqueViolation("pending_clockin_client_req_idx")
            if row.get("status") == "pending":
                for r in rows:
                    if (r.get("org_id") == org and r.get("employee_id") == emp
                            and r.get("store_code") == row.get("store_code")
                            and str(r.get("work_date")) == str(row.get("work_date"))
                            and r.get("status") == "pending"):
                        raise UniqueViolation("pending_clockin_one_open_idx")

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        if self._mode == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for p in payload:
                row = dict(p)
                self._enforce_unique(rows, row)
                row.setdefault("id", f"{self.key[1]}-{len(rows) + len(out) + 1}")
                out.append(row)
            rows.extend(out)
            return Result([dict(r) for r in out])
        matched = [r for r in rows if self._matches(r)]
        if self._mode == "update":
            for r in matched:
                r.update(self._payload)
            return Result([dict(r) for r in matched])
        if self._mode == "delete":
            self.store[self.key] = [r for r in rows if not self._matches(r)]
            return Result(matched)
        if self._order:
            k, desc = self._order
            matched = sorted(matched, key=lambda r: (r.get(k) is None, r.get(k)), reverse=desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        return Result([dict(r) for r in matched])


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


fake = FakeClient()

import app.modules.storeops.router as R  # noqa: E402

R.get_supabase = lambda: fake
R.sb = lambda: fake.schema("storeops")

ORG = "org-pc-1"
AUTH = "Bearer x"

# Manager notification is stubbed to a COUNTER — no email, no cross-module approvals write. Records the
# (store, work_date, start_hhmm) it was called with so we can assert exactly-one-per-held-tap + the
# correct store-local start.
NOTIFY_CALLS = []
R._notify_pending_clockin_managers = lambda org_id, pending, start_hhmm, deeplink: (
    NOTIFY_CALLS.append({"store": pending.get("store_code"), "work_date": str(pending.get("work_date")),
                         "start": start_hhmm, "deeplink": deeplink}) or "approval-req-1")
# Resolution of the approvals row on activation is exercised elsewhere; stub it here.
R._resolve_pending_clockin_approval = lambda org_id, pending: None


class _FakeDateTime:
    _now = datetime.now(timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._now if tz is None else cls._now.astimezone(tz)

    @staticmethod
    def fromisoformat(s):
        return datetime.fromisoformat(s)


R.datetime = _FakeDateTime


def set_now(dt):
    _FakeDateTime._now = dt.astimezone(timezone.utc)


def reset(store_tz=None):
    fake.store.clear()
    NOTIFY_CALLS.clear()
    R._STORE_TZ_CACHE.clear()
    R._PENDING_CLOCKIN_PRESENT = None
    R._TC432_PRESENT = None
    R._caller_identity = lambda auth: (ORG, "E1")
    fake.seed("storeops", "tenants", [{"org_id": ORG, "closing_gate_enabled": False,
                                       "priority_ack_enabled": False}])
    fake.seed("storeops", "employees",
              [{"org_id": ORG, "employee_id": "E1", "id": 201, "name": "Alice", "home_store": "S1",
                "pay_rate": 20.0, "is_active": True}])
    fake.seed("storeops", "timelog", [])
    fake.seed("storeops", "pending_clockin", [])
    fake.seed("storeops", "shifts", [])
    fake.seed("storeops", "app_users", [])
    stores = [{"org_id": ORG, "store_code": "S1", "is_active": True},
              {"org_id": ORG, "store_code": "S2", "is_active": True, "timezone": store_tz}]
    fake.seed("storeops", "stores", stores)


def tl():
    return fake.store.get(("storeops", "timelog"), [])


def pc():
    return fake.store.get(("storeops", "pending_clockin"), [])


def sh():
    return fake.store.get(("storeops", "shifts"), [])


def open_punches():
    return [r for r in tl() if r.get("clock_out") is None]


def ci(store="S2", cid=None):
    body = R.ClockInIn(store_code=store, client_request_id=cid)
    return R.clock_in(body, authorization=AUTH, org_id=ORG)


# ══ (a) Unscheduled tap -> pending_schedule_approval, NO punch, NO shell, ONE pending + ONE notify ════
reset(store_tz="America/New_York")
# clock at 10:00 store-local (Eastern), tapping S2 (not home, not scheduled).
set_now(datetime(2026, 8, 24, 10, 0, tzinfo=ZoneInfo("America/New_York")))
r = ci(store="S2")
check("a1 unscheduled tap returns pending_schedule_approval",
      r.get("status") == "pending_schedule_approval", r)
check("a2 success is False (no time accrued)", r.get("success") is False, r)
check("a3 professional message, no 'override' wording",
      "override" not in (r.get("message") or "").lower() and "clocked in automatically" in (r.get("message") or "").lower(), r)
check("a4 NO timelog punch opened", len(tl()) == 0, tl())
check("a5 NO zero-hour shift shell inserted", len(sh()) == 0, sh())
check("a6 exactly ONE pending_clockin row", len(pc()) == 1, pc())
check("a7 the pending row is status=pending, store=S2", pc() and pc()[0].get("status") == "pending" and pc()[0].get("store_code") == "S2", pc())
check("a8 exactly ONE manager notification", len(NOTIFY_CALLS) == 1, NOTIFY_CALLS)
check("a9 notification start is store-local 10:00", NOTIFY_CALLS and NOTIFY_CALLS[0]["start"] == "10:00", NOTIFY_CALLS)
check("a10 work_date bucketed to 2026-08-24", pc() and str(pc()[0].get("work_date")) == "2026-08-24", pc())

# ══ (b) A RETAP is idempotent — still ONE pending, ONE notification (no stacking) ════════════════════
# b-i: same client_request_id.
reset(store_tz="America/New_York")
set_now(datetime(2026, 8, 24, 10, 0, tzinfo=ZoneInfo("America/New_York")))
ci(store="S2", cid="tap-1")
ci(store="S2", cid="tap-1")
check("b1 same client_request_id retap -> ONE pending row", len(pc()) == 1, pc())
check("b2 same client_request_id retap -> ONE notification", len(NOTIFY_CALLS) == 1, NOTIFY_CALLS)
# b-ii: no client_request_id (relies on the one-open-pending guard).
reset(store_tz="America/New_York")
set_now(datetime(2026, 8, 24, 10, 0, tzinfo=ZoneInfo("America/New_York")))
ci(store="S2")
ci(store="S2")
check("b3 no-id retap -> still ONE pending row (one-open guard)", len(pc()) == 1, pc())
check("b4 no-id retap -> still ONE notification", len(NOTIFY_CALLS) == 1, NOTIFY_CALLS)
check("b5 still NO punch after retaps", len(tl()) == 0, tl())


def save_schedule(store="S2", date="2026-08-24", start="09:00", end="17:00"):
    shift = {"org_id": ORG, "employee_id": "E1", "employee_name": "Alice", "store_code": store,
             "shift_date": date, "start_time": start, "end_time": end, "scheduled_hours": 8,
             "status": "scheduled"}
    return R.create_shift(shift, org_id=ORG)


# ══ (c) Manager schedule-save ACTIVATES -> one open punch at the ORIGINAL tap time; 2nd save no-op ════
reset(store_tz="America/New_York")
tap_at = datetime(2026, 8, 24, 10, 0, tzinfo=ZoneInfo("America/New_York"))
set_now(tap_at)
ci(store="S2")
check("c1 held: no punch before scheduling", len(tl()) == 0, tl())
set_now(datetime(2026, 8, 24, 11, 0, tzinfo=ZoneInfo("America/New_York")))   # manager saves an hour later
out = save_schedule()
check("c2 schedule-save reports activated_clockins", bool(out.get("activated_clockins")), out)
check("c3 exactly ONE open punch after activation", len(open_punches()) == 1, tl())
check("c4 the punch clock_in == the ORIGINAL tap time (not the save time)",
      open_punches() and open_punches()[0].get("clock_in") == tap_at.astimezone(timezone.utc).isoformat(), open_punches())
check("c5 pending row is now status=activated", pc() and pc()[0].get("status") == "activated", pc())
check("c6 activated pending links the timelog id",
      pc() and pc()[0].get("timelog_id") == open_punches()[0].get("id"), pc())
# second identical save = no-op (idempotent): no second punch, still one open.
out2 = save_schedule()
check("c7 a SECOND schedule-save does not double-punch", len(open_punches()) == 1, tl())
check("c8 the second save reports NO new activation", not out2.get("activated_clockins"), out2)

# ══ (d) A genuinely SCHEDULED rep clocks in NORMALLY (unchanged; no pending) ═════════════════════════
reset(store_tz="America/New_York")
# schedule Alice at S2 today, starting 09:00; tap at 10:00 (after start, within schedule).
fake.seed("storeops", "shifts", [{"org_id": ORG, "employee_id": "201", "employee_name": "Alice",
                                  "store_code": "S2", "shift_date": "2026-08-24", "start_time": "09:00",
                                  "end_time": "17:00", "is_deleted": False}])
set_now(datetime(2026, 8, 24, 10, 0, tzinfo=ZoneInfo("America/New_York")))
r = ci(store="S2")
check("d1 scheduled rep clocks in normally (success)", r.get("success") is True, r)
check("d2 scheduled rep is NOT pending", r.get("status") != "pending_schedule_approval", r)
check("d3 exactly ONE open punch for the scheduled rep", len(open_punches()) == 1, tl())
check("d4 NO pending_clockin row created", len(pc()) == 0, pc())
check("d5 NO manager notification for a scheduled rep", len(NOTIFY_CALLS) == 0, NOTIFY_CALLS)

# ══ (e) Store-local timezone — a 7pm-CENTRAL tap buckets to the right day + start (mig 851) ══════════
reset(store_tz="America/Chicago")
central = ZoneInfo("America/Chicago")
set_now(datetime(2026, 8, 24, 19, 0, tzinfo=central))   # 7:00 PM Central == 00:00 UTC next day
r = ci(store="S2")
check("e1 Central 7pm tap -> pending", r.get("status") == "pending_schedule_approval", r)
check("e2 work_date is the CENTRAL day 2026-08-24, not the UTC 2026-08-25",
      pc() and str(pc()[0].get("work_date")) == "2026-08-24", pc())
check("e3 notified start is 19:00 Central (not 20:00 Eastern)",
      NOTIFY_CALLS and NOTIFY_CALLS[0]["start"] == "19:00", NOTIFY_CALLS)
# and activation preserves the exact UTC instant of the tap.
tap_utc = datetime(2026, 8, 24, 19, 0, tzinfo=central).astimezone(timezone.utc).isoformat()
set_now(datetime(2026, 8, 24, 20, 0, tzinfo=central))
save_schedule(date="2026-08-24", start="18:00", end="22:00")
check("e4 activated punch clock_in preserves the tap's UTC instant",
      open_punches() and open_punches()[0].get("clock_in") == tap_utc, open_punches())

# ── report ──────────────────────────────────────────────────────────────────────────────────────
print("\n".join(f"  PASS  {p}" for p in PASS))
if FAIL:
    print("\n".join(f"  FAIL  {f}" for f in FAIL))
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
