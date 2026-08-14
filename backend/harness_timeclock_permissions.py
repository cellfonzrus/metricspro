"""Offline proof (no live DB/network) for the owner-approved auto-clock-out + DM-permission feature
(2026-08-14, migration 432). Runs the REAL clock_in / clock_out / _do_force_clockout / permission
handlers against an in-memory fake Supabase client (same pattern as harness_timeclock_multisession.py),
proving:

  1. GRACE BOUNDARY   — the force-clockout sweep does NOT fire before scheduled_end + 5 min, and DOES
                        fire at/after it.
  2. STAMP = END + 5  — the auto clock-out is stamped AT scheduled_end + 5 min (owner's choice, not the
                        bare scheduled end), and the row is flagged auto_clocked_out.
  3. RE-CLOCK-IN PENDING — a second session after an auto-clock-out is allowed but held pending the
                        DM's permission (needs_dm_permission), its hours are NOT counted (held NULL),
                        and a reclock_in permission row is raised.
  4. LATE-CLOCKOUT PENDING — a manual clock-out worked past end+5 is capped at end+5 (base hours count),
                        and the EXTRA time is raised as a pending late_clockout permission.
  5. DM APPROVE MAKES IT COUNT — approving a reclock_in stamps the held hours; approving a late_clockout
                        extends the clock-out to the approved departure and recomputes hours.
  6. PAYROLL EXCLUSION — a pending/denied second session carries hours=None, so every payroll reader
                        (all require hours IS NOT NULL) skips it until approval.

Run: `python3 harness_timeclock_permissions.py` from backend/.
"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── Fake Supabase client (identical shape to harness_timeclock_multisession.py) ──────────────────
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

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        if self._mode == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for p in payload:
                row = dict(p)
                row.setdefault("id", f"{self.key[1]}-{len(rows) + len(out) + 1}")
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


fake = FakeClient()

import app.modules.storeops.router as R  # noqa: E402

R.get_supabase = lambda: fake
R.sb = lambda: fake.schema("storeops")

ORG = "org-tcp-1"
EMP = "E1"
# Self-service identity is fixed to our one employee; a DM approval is our one manager.
R._caller_identity = lambda auth: (ORG, EMP)
R._require_manager = lambda auth, org_id=ORG: {"org_id": ORG, "email": "dm@x", "role": "district_manager"}


# ── Controllable fake clock (advance simulated time between punches). ─────────────────────────────
class _FakeDateTime:
    _now = datetime.now(timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._now if tz is None else cls._now.astimezone(tz)

    @staticmethod
    def fromisoformat(s):
        return datetime.fromisoformat(s)


def set_now(dt):
    _FakeDateTime._now = dt


R.datetime = _FakeDateTime

_biz_tz = R._biz_tz_for(ORG)
_today = datetime.now(timezone.utc).astimezone(_biz_tz).date()
TODAY = _today.isoformat()


def biz(h, m=0):
    """A UTC datetime for HH:MM business-local on TODAY."""
    return datetime(_today.year, _today.month, _today.day, h, m, 0, tzinfo=_biz_tz).astimezone(timezone.utc)


SCHED_END = R._biz_dt_utc(TODAY, "17:00", ORG)          # 17:00 business-local -> UTC
GRACE = timedelta(minutes=R.FORCE_CLOCKOUT_GRACE_MIN)   # owner: 5 minutes
STAMP = SCHED_END + GRACE                                # 17:05 business-local


def reset():
    fake.store.clear()
    fake.seed("storeops", "tenants", [{"org_id": ORG, "closing_gate_enabled": False, "priority_ack_enabled": False}])
    fake.seed("storeops", "employees", [
        {"org_id": ORG, "employee_id": EMP, "id": 201, "name": "Alice", "home_store": "S1", "pay_rate": 20.0, "is_active": True},
    ])
    fake.seed("storeops", "shifts", [
        {"org_id": ORG, "employee_id": EMP, "employee_name": "Alice", "store_code": "S1",
         "shift_date": TODAY, "start_time": "08:00", "end_time": "17:00", "is_deleted": False},
    ])
    fake.seed("storeops", "timelog", [])
    fake.seed("storeops", "timeclock_permission", [])


AUTH = "Bearer x"


def tl_rows():
    return sorted(fake.store.get(("storeops", "timelog"), []), key=lambda r: r.get("clock_in") or "")


def perms(kind=None, status=None):
    out = fake.store.get(("storeops", "timeclock_permission"), [])
    if kind:
        out = [p for p in out if p.get("kind") == kind]
    if status:
        out = [p for p in out if p.get("status") == status]
    return out


# FORCE_CLOCKOUT_GRACE_MIN must be exactly 5 for the owner-approved behavior.
check("0 grace constant is 5 minutes (owner directive)", R.FORCE_CLOCKOUT_GRACE_MIN == 5, R.FORCE_CLOCKOUT_GRACE_MIN)


# ══ 1 + 2: GRACE BOUNDARY and STAMP = scheduled_end + 5 ═══════════════════════════════════════════
reset()
set_now(biz(8, 0))
r1 = R.clock_in({"store_code": "S1"}, authorization=AUTH, org_id=ORG)
check("1a morning clock-in succeeds", r1.get("success") is True, r1)

set_now(SCHED_END + timedelta(minutes=4))      # 17:04 — inside the grace window
sweep_early = R._do_force_clockout(org_id=ORG)
check("1b sweep does NOT fire before scheduled_end + 5 (still open at +4 min)",
      sweep_early["closed"] == 0 and tl_rows()[0].get("clock_out") is None, (sweep_early, tl_rows()))

set_now(SCHED_END + timedelta(minutes=6))      # 17:06 — past the grace window
sweep_late = R._do_force_clockout(org_id=ORG)
row = tl_rows()[0]
check("2a sweep fires once now >= scheduled_end + 5 (one punch auto-closed)",
      sweep_late["closed"] == 1 and row.get("clock_out") is not None, (sweep_late, row))
check("2b clock-out is STAMPED at scheduled_end + 5 min (not the bare scheduled end)",
      row.get("clock_out") == STAMP.isoformat(), (row.get("clock_out"), STAMP.isoformat()))
check("2c row is flagged auto_clocked_out", row.get("auto_clocked_out") is True, row)
# 08:00 -> 17:05 = 9h05m = 9.08h
check("2d hours = clock_in .. scheduled_end+5 (9.08h)", row.get("hours") == 9.08, row.get("hours"))


# ══ 3 + 5a + 6: RE-CLOCK-IN after auto-clock-out is PENDING, held out of pay, then DM-approved ═════
# (continues from the auto-closed session above.)
set_now(SCHED_END + timedelta(minutes=10))     # 17:10 — a genuine second session
r2 = R.clock_in({"store_code": "S1"}, authorization=AUTH, org_id=ORG)
check("3a re-clock-in after an auto-clock-out is ALLOWED", r2.get("success") is True, r2)
check("3b ...but flagged needs_dm_permission / pending", r2.get("needs_dm_permission") is True
      and r2.get("permission_status") == "pending", r2)
reclock = perms(kind="reclock_in", status="pending")
check("3c a pending reclock_in permission row was raised", len(reclock) == 1, perms())
session2 = tl_rows()[-1]
check("3d the second punch itself is flagged permission_status=pending", session2.get("permission_status") == "pending", session2)

set_now(SCHED_END + timedelta(minutes=70))     # 18:10 — they clock out of the second session
r3 = R.clock_out({}, authorization=AUTH, org_id=ORG)
session2 = [r for r in tl_rows() if r["id"] == session2["id"]][0]
check("3e clocking out of a pending second session records clock_out but HOLDS hours NULL",
      r3.get("success") is True and session2.get("clock_out") is not None and session2.get("hours") is None, (r3, session2))
check("6a payroll exclusion: a pending second session has hours=None (every payroll reader requires "
      "hours IS NOT NULL, so it is not paid until approved)", session2.get("hours") is None, session2)

# DM approves the reclock_in -> the held hours are stamped and it now counts.
perm_id = reclock[0]["id"]
d1 = R.decide_timeclock_permission(perm_id, {"decision": "approve"}, authorization=AUTH, org_id=ORG)
session2 = [r for r in tl_rows() if r["id"] == session2["id"]][0]
check("5a DM approve flips the permission to approved", d1.get("status") == "approved", d1)
check("5b ...the punch permission_status becomes approved", session2.get("permission_status") == "approved", session2)
# 17:10 -> 18:10 = 1.00h
check("5c ...and the held hours are stamped so it now counts (1.00h)", session2.get("hours") == 1.0, session2.get("hours"))


# ══ 4 + 5b: LATE CLOCK-OUT worked past end+5 is capped; the extra is pending; DM approve adds it ═══
reset()
set_now(biz(8, 0))
R.clock_in({"store_code": "S1"}, authorization=AUTH, org_id=ORG)
set_now(SCHED_END + timedelta(minutes=20))     # 17:20 — kept working 15 min past the grace cap
r4 = R.clock_out({}, authorization=AUTH, org_id=ORG)
row = tl_rows()[0]
check("4a manual clock-out worked past end+5 succeeds", r4.get("success") is True, r4)
check("4b counted clock-out is CAPPED at scheduled_end + 5 (base scheduled time counts)",
      row.get("clock_out") == STAMP.isoformat() and row.get("hours") == 9.08, row)
check("4c row flagged auto_clocked_out and response signals extra_pending",
      row.get("auto_clocked_out") is True and r4.get("extra_pending") is True, (row, r4))
late = perms(kind="late_clockout", status="pending")
check("4d a pending late_clockout permission was raised for the EXTRA time (~15 min)",
      len(late) == 1 and late[0].get("extra_minutes") == 15, perms())

# DM approves the late_clockout -> the punch clock-out extends to the actual departure, hours recompute.
d2 = R.decide_timeclock_permission(late[0]["id"], {"decision": "approve"}, authorization=AUTH, org_id=ORG)
row = tl_rows()[0]
check("5d DM approve extends the clock-out to the approved departure (17:20)",
      d2.get("status") == "approved" and row.get("clock_out") == (SCHED_END + timedelta(minutes=20)).isoformat(), (d2, row))
# 08:00 -> 17:20 = 9h20m = 9.33h
check("5e ...and hours are recomputed to include the approved extra (9.33h), auto flag cleared",
      row.get("hours") == 9.33 and row.get("auto_clocked_out") is False, row)


# ══ 7: DENY leaves the held second-session time uncounted ═════════════════════════════════════════
reset()
set_now(biz(8, 0))
R.clock_in({"store_code": "S1"}, authorization=AUTH, org_id=ORG)
set_now(SCHED_END + timedelta(minutes=6))
R._do_force_clockout(org_id=ORG)               # auto-close session 1
set_now(SCHED_END + timedelta(minutes=10))
R.clock_in({"store_code": "S1"}, authorization=AUTH, org_id=ORG)   # pending second session
set_now(SCHED_END + timedelta(minutes=40))
R.clock_out({}, authorization=AUTH, org_id=ORG)
pend = perms(kind="reclock_in", status="pending")[0]
R.decide_timeclock_permission(pend["id"], {"decision": "deny"}, authorization=AUTH, org_id=ORG)
s2 = tl_rows()[-1]
check("7a a DENIED second session stays uncounted (hours None, permission_status denied)",
      s2.get("hours") is None and s2.get("permission_status") == "denied", s2)


# ══ 8: REGRESSION — a normal lunch-break second session is NOT gated (prior close was manual) ══════
# The gate keys off a PRIOR punch flagged auto_clocked_out, NOT merely "a second session today" — so
# the legal multi-session day (harness_timeclock_multisession.py's invariant) stays permission-free.
reset()
set_now(biz(8, 0))
R.clock_in({"store_code": "S1"}, authorization=AUTH, org_id=ORG)
set_now(biz(12, 0))
r_lunch = R.clock_out({}, authorization=AUTH, org_id=ORG)      # manual mid-day clock-out (before end)
check("8a mid-day manual clock-out is a normal punch (not auto_clocked_out, hours counted)",
      r_lunch.get("success") is True and not tl_rows()[0].get("auto_clocked_out")
      and tl_rows()[0].get("hours") == 4.0, (r_lunch, tl_rows()[0]))
set_now(biz(13, 0))
r_after = R.clock_in({"store_code": "S1"}, authorization=AUTH, org_id=ORG)  # afternoon session
check("8b re-clock-in after a MANUAL close is NOT gated (no DM permission needed)",
      r_after.get("success") is True and not r_after.get("needs_dm_permission"), r_after)
check("8c ...and no permission row is raised for a normal lunch-break day",
      len(perms()) == 0 and tl_rows()[-1].get("permission_status") is None, (perms(), tl_rows()[-1]))


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
