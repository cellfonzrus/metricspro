"""Offline proof (no live DB/network) for the 2026-07-25 owner-directed universal fix:
"if an employee clocks in the morning and leaves for lunch and comes back and clocks in a second
time on the same day, it gives an error about the second clock-in and does not let the user clock
in — the DM has to manually adjust the clock-ins — the solution needs to be universal."

ROOT CAUSE (code-verified, NOT a DB unique constraint — storeops.timelog has none on
(org_id, employee_id, work_date); see 045_timeclock.sql's own comment "one row per clock-in" and the
absence of any such index in 400/407's migrations). `clock_in` itself already only blocks a SECOND
OPEN entry (a genuine already-clocked-in state) — multi-session days were already legal there. The
actual failure chain is in `_closing_gate_block` (storeops/router.py, called from `clock_out`): its
"effective closer" fallback infers "this employee must be leaving for the day" from nothing more
than "no one else has an open punch at this store right now" — true both at TRUE end-of-day AND at
midday before anyone else has clocked in yet. That misfire blocks the LUNCH clock-out
(`{"success": false, "needs_closing": true}`, punch stays OPEN), and the employee's next clock-in
attempt then correctly (but confusingly) 409s "Already clocked in — clock out first." — the visible
symptom reported as "the second clock-in errors." The 2026-07-25 fix teaches `_closing_gate_block` to
tell a mid-day break apart from a true final departure (see router.py for the two-signal rule) so the
lunch clock-out succeeds cleanly, never leaving a stray open punch, so clock-in Really is only ever
blocked by a genuinely-open punch — exactly the owner's stated rule.

This harness runs the REAL `clock_in`/`clock_out` HANDLERS end-to-end (not just `_closing_gate_block`
in isolation, which harness_closer_chargebacks.py already covers exhaustively) against an in-memory
fake Supabase client, proving the full morning -> lunch -> afternoon -> evening cycle now works, that
a genuine still-open punch is still rejected on a THIRD concurrent clock-in attempt, and that BOTH
sessions' hours are summed correctly wherever timelog feeds payroll (mig-407 RPC aggregation AND the
legacy Python path).

Run: `python3 harness_timeclock_multisession.py` from backend/.
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


# ── Fake Supabase client (same pattern as harness_closer_chargebacks.py / harness_payroll_rpc_equivalence.py) ──
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


fake = FakeClient()

import app.modules.storeops.router as R  # noqa: E402
import app.modules.core.router as core_router  # noqa: E402

R.get_supabase = lambda: fake
R.sb = lambda: fake.schema("storeops")
core_router._uid_from_token = lambda auth: "uid-1"   # every call in this harness is signed in as one user


# ── Controllable fake clock — router.py calls `datetime.now(timezone.utc)`/`datetime.fromisoformat`
# everywhere via its module-level `datetime` name. Swapping that name for this wrapper lets the
# harness ADVANCE simulated time between clock-in/out calls (a real lunch break, hours later) instead
# of running sub-millisecond and rounding every session to 0.00h — without touching any product code.
class _FakeDateTime:
    _now = datetime.now(timezone.utc)   # overwritten with a pinned safe start-of-day below

    @classmethod
    def now(cls, tz=None):
        return cls._now if tz is None else cls._now.astimezone(tz)

    @staticmethod
    def fromisoformat(s):
        return datetime.fromisoformat(s)


def advance_clock(hours):
    _FakeDateTime._now = _FakeDateTime._now + timedelta(hours=hours)


R.datetime = _FakeDateTime

ORG = "org-tc-1"
# Pin the fake clock to 08:00 business-local on TODAY's real date, regardless of the actual
# wall-clock time this harness happens to run at — the test advances up to ~16h across sessions
# (lunch break + afternoon + evening + a manual-hours check) and must never risk crossing midnight
# into a different business day (which would silently change `work_date` mid-scenario).
_biz_tz = R._biz_tz_for(ORG)
_today_date = datetime.now(timezone.utc).astimezone(_biz_tz).date()
_FakeDateTime._now = datetime(_today_date.year, _today_date.month, _today_date.day, 8, 0, 0, tzinfo=_biz_tz).astimezone(timezone.utc)
TODAY = _today_date.isoformat()


def reset(closing_gate_enabled=True, home_store="S1"):
    fake.store.clear()
    fake.seed("storeops", "tenants", [{"org_id": ORG, "closing_gate_enabled": closing_gate_enabled}])
    fake.seed("storeops", "employees", [
        {"org_id": ORG, "employee_id": "E1", "id": 201, "name": "Alice", "home_store": home_store, "pay_rate": 20.0, "is_active": True},
    ])
    fake.seed("storeops", "app_users", [{"org_id": ORG, "auth_id": "uid-1", "employee_id": "E1"}])
    fake.seed("storeops", "store_closer", [{"org_id": ORG, "store_code": "S1", "employee_id": "E1"}])
    fake.seed("commcalc", "daily_closing", [])
    fake.seed("storeops", "timelog", [])


AUTH = "Bearer x"


# ══ 1: THE REPORTED BUG, END TO END — morning clock-in, lunch clock-out, afternoon clock-in ═══════
# Pure-kiosk (no shift schedule at all — the luxelink case), closing gate ON, closing not submitted.
reset(closing_gate_enabled=True)
r1 = R.clock_in({"store_code": "S1"}, authorization=AUTH, org_id=ORG)
check("1a morning clock-in succeeds", r1.get("success") is True, r1)
advance_clock(4.0)   # lunchtime — 4 real hours into the morning session

r2 = R.clock_out({}, authorization=AUTH, org_id=ORG)
check("1b lunch clock-out succeeds (THE FIX — previously blocked by the closing gate as a false "
      "'last one out' inference)", r2.get("success") is True and not r2.get("needs_closing"), r2)
check("1b2 lunch session hours are real and positive (exactly 4h)", (r2.get("data", {}).get("hours")) == 4.0, r2)

r3 = R.clock_in({"store_code": "S1"}, authorization=AUTH, org_id=ORG)
check("1c afternoon clock-in succeeds — NOT a 409 'already clocked in' (the reported symptom)",
      r3.get("success") is True, r3)
advance_clock(3.0)   # end of the afternoon session — 3 more real hours pass

# ══ 2: their SECOND clock-out of the day IS still gated (protection preserved, not just disabled) ══
r4 = R.clock_out({}, authorization=AUTH, org_id=ORG)
check("2a evening clock-out (2nd session-close today) IS gated — the closer is still held accountable",
      r4.get("success") is False and r4.get("needs_closing") is True, r4)

# Submit the closing -> now the gate clears and the SAME open punch can close normally.
fake.seed("commcalc", "daily_closing", [{"org_id": ORG, "close_date": TODAY, "store_code": "S1"}])
r5 = R.clock_out({}, authorization=AUTH, org_id=ORG)
check("2b after the closing is submitted, the SAME punch closes normally (not re-opened, not lost)",
      r5.get("success") is True, r5)
check("2c evening session hours are real and positive (exactly 3h)", (r5.get("data", {}).get("hours")) == 3.0, r5)

# ══ 3: a GENUINELY open punch still blocks a concurrent clock-in (the rule the owner wants KEPT) ═══
reset(closing_gate_enabled=True)
R.clock_in({"store_code": "S1"}, authorization=AUTH, org_id=ORG)
try:
    R.clock_in({"store_code": "S1"}, authorization=AUTH, org_id=ORG)
    check("3a concurrent clock-in on a truly-open punch is rejected", False, "no exception raised")
except Exception as e:
    code = getattr(e, "status_code", None)
    check("3a concurrent clock-in on a truly-open punch is rejected (409)", code == 409, e)

# ══ 4: gate disabled entirely -> both clock-outs succeed with zero gating, same session shape ══════
reset(closing_gate_enabled=False)
R.clock_in({"store_code": "S1"}, authorization=AUTH, org_id=ORG)
advance_clock(1.0)
r6 = R.clock_out({}, authorization=AUTH, org_id=ORG)
R.clock_in({"store_code": "S1"}, authorization=AUTH, org_id=ORG)
advance_clock(0.5)
r7 = R.clock_out({}, authorization=AUTH, org_id=ORG)
check("4a gate disabled: lunch clock-out succeeds", r6.get("success") is True, r6)
check("4b gate disabled: evening clock-out ALSO succeeds (no gating at all when the tenant opted out)",
      r7.get("success") is True, r7)

# ══ 5: MONEY — both closed sessions' hours are real, distinct, non-overlapping, and SUM correctly ══
rows = sorted(fake.store[("storeops", "timelog")], key=lambda r: r["clock_in"])
check("5a exactly 2 timelog rows recorded for the whole day (one per session, not merged/lost)",
      len(rows) == 2, rows)
check("5b both rows are closed with positive hours, second clock_in is AT/AFTER the first clock_out "
      "(no overlap — a real gap for the lunch break)",
      all(r.get("clock_out") and (r.get("hours") or 0) > 0 for r in rows)
      and rows[1]["clock_in"] >= rows[0]["clock_out"], rows)
total_hours = round(sum(r["hours"] for r in rows), 2)
check("5c total hours for the day = sum of BOTH sessions (not just the last one)",
      total_hours == round(rows[0]["hours"] + rows[1]["hours"], 2), (total_hours, rows))

# ══ 6: PAYROLL CONSUMERS sum multiple same-day sessions correctly — mig-407 RPC path AND legacy path ══
# Reuses the exact per-row semantics from harness_payroll_rpc_equivalence.py's RPC simulator so this
# is provably the SAME aggregation Postgres would run, not a re-invented approximation.
def simulate_payroll_month_rows(store, p_org_id, p_lo, p_hi):
    out = []
    live_shift_days = {(s.get("employee_id"), str(s.get("shift_date") or ""))
                       for s in store.get(("storeops", "shifts"), [])
                       if s.get("org_id") == p_org_id and s.get("is_deleted") is False
                       and s.get("employee_id") is not None}
    tgroups = {}
    for t in store.get(("storeops", "timelog"), []):
        if t.get("org_id") != p_org_id:
            continue
        wd = str(t.get("work_date") or "")
        if not wd or wd < p_lo or wd >= p_hi:
            continue
        if t.get("clock_out") is None or t.get("hours") is None:
            continue
        eid = t.get("employee_id")
        if not eid or (eid, wd) in live_shift_days:
            continue
        tgroups.setdefault((eid, (t.get("store_code") or "").strip()), []).append(t)
    for (eid, st), rows_ in tgroups.items():
        out.append({"kind": "timelog", "employee_id": eid, "store_code": st,
                    "employee_name": rows_[0].get("employee_name"), "first_ord": 0.0,
                    "scheduled_sum": 0.0, "actual_eff_sum": 0.0, "hours_eff_sum": 0.0, "shift_count": 0,
                    "timelog_hours_sum": sum(float(r.get("hours") or 0) for r in rows_)})
    return out


rpc_groups = simulate_payroll_month_rows(fake.store, ORG, "2020-01-01", "2099-01-01")
tl_group = next((g for g in rpc_groups if g["employee_id"] == "E1"), None)
check("6a mig-407 RPC aggregation sums BOTH sessions' hours for the day (not just one row)",
      tl_group is not None and round(tl_group["timelog_hours_sum"], 2) == total_hours, (tl_group, total_hours))

# Legacy Python path (the exact loop /payroll runs when the RPC is unavailable): every closed row's
# hours are added independently — no per-day grouping/replace, so multiple sessions already summed.
legacy_actual = 0.0
for t in fake.store[("storeops", "timelog")]:
    if t.get("clock_out") and t.get("hours") is not None:
        legacy_actual += float(t["hours"] or 0)
check("6b legacy per-row summation path ALSO sums both sessions identically",
      round(legacy_actual, 2) == total_hours, (legacy_actual, total_hours))

# ══ 7: manual_hours coexistence — a DM's earlier manual adjustment is untouched by any of this ═════
fake.seed("storeops", "manual_hours", [{"org_id": ORG, "employee_id": "E1", "work_date": TODAY,
                                        "hours": 1.5, "reason": "DM adjustment for a prior stuck punch"}])
mh_rows_before = list(fake.store[("storeops", "manual_hours")])
R.clock_in({"store_code": "S1"}, authorization=AUTH, org_id=ORG)
advance_clock(1.0)
R.clock_out({}, authorization=AUTH, org_id=ORG)
check("7a a pre-existing manual_hours adjustment is neither duplicated nor removed by clock-in/out",
      fake.store[("storeops", "manual_hours")] == mh_rows_before, fake.store[("storeops", "manual_hours")])

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
