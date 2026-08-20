"""Offline proof (no live DB/network) for PER-STORE time zones (migration 851, owner report 2026-08-15).

Runs the REAL storeops resolvers/handlers against an in-memory fake Supabase client, proving the bug is
fixed: a tenant with Chicago (Central) and NY (Eastern) stores now has each store's "19:00" shift read
in the store's OWN zone, so the auto-clock-out sweep closes a Chicago punch at 7 PM Central and an NY
punch at 7 PM Eastern — instead of clocking Chicago out an hour early on one tenant-wide zone.

  1. RESOLUTION   — _biz_tz_for_store: store.timezone → tenant default → house default.
  2. TWO ZONES    — a Chicago store and an NY store resolve to DIFFERENT zones, 1 hour apart.
  3. SHIFT END    — _biz_dt_utc reads "19:00" as 7 PM Central for Chicago and 7 PM Eastern for NY
                    (different UTC instants).
  4. DEGRADE      — a missing store / unset column / un-run migration falls back to the tenant zone,
                    byte-identical to pre-851 behavior; never raises.
  5. SWEEP        — at one shared 'now', _do_force_clockout closes the NY punch (past 7 PM ET) but
                    LEAVES the Chicago punch open (still before 7 PM CT). Pre-fix, both read Eastern and
                    Chicago was swept an hour early — this is the regression guard.

Run: `python3 harness_store_timezone.py` from backend/.
"""
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  ({detail})"))


# ── Minimal in-memory fake Supabase client (same shape as the other timeclock harnesses) ──────────
class Result:
    def __init__(self, data): self.data = data


class FakeQuery:
    def __init__(self, store, key, absent):
        self.store, self.key, self.absent = store, key, absent
        self.filters, self._mode, self._payload, self._limit = [], "select", None, None

    def select(self, *_a, **_k): return self
    def eq(self, k, v): self.filters.append(("eq", k, v)); return self
    def in_(self, k, vals): self.filters.append(("in", k, set(str(x) for x in vals))); return self
    def is_(self, k, v): self.filters.append(("is", k, v)); return self
    def limit(self, n): self._limit = n; return self
    def order(self, *_a, **_k): return self
    def insert(self, p): self._mode = "insert"; self._payload = p; return self
    def update(self, p): self._mode = "update"; self._payload = p; return self

    def _matches(self, row):
        for kind, k, v in self.filters:
            rv = row.get(k)
            if kind == "eq" and str(rv) != str(v): return False
            if kind == "in" and str(rv) not in v: return False
            if kind == "is" and v == "null" and rv is not None: return False
        return True

    def execute(self):
        if self.key in self.absent:
            raise Exception(f'relation "{self.key[1]}" does not exist')
        rows = self.store.setdefault(self.key, [])
        if self._mode == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            out = [dict(p) for p in payload]
            for i, r in enumerate(out):
                r.setdefault("id", f"{self.key[1]}-{len(rows)+i+1}")
            rows.extend(out); return Result(out)
        matched = [r for r in rows if self._matches(r)]
        if self._mode == "update":
            for r in matched: r.update(self._payload)
            return Result(matched)
        if self._limit is not None: matched = matched[: self._limit]
        return Result(matched)


class FakeSchema:
    def __init__(self, c, name): self.c, self.name = c, name
    def table(self, t): return FakeQuery(self.c.store, (self.name, t), self.c.absent)


class FakeClient:
    def __init__(self): self.store, self.absent = {}, set()
    def schema(self, name): return FakeSchema(self, name)
    def table(self, t): return FakeQuery(self.store, ("storeops", t), self.absent)
    def seed(self, schema, table, rows): self.store[(schema, table)] = [dict(r) for r in rows]


fake = FakeClient()
import app.modules.storeops.router as R  # noqa: E402
R.get_supabase = lambda: fake
R.sb = lambda: fake.schema("storeops")

ORG = "org-tz-1"
CENTRAL = ZoneInfo("America/Chicago")
EASTERN = ZoneInfo("America/New_York")
DAY = "2026-08-15"   # summer → CDT (UTC-5) / EDT (UTC-4); 7 PM local differ by exactly 1 hour


def reset(stores, tenant_tz=None, absent=None):
    fake.store.clear()
    R._STORE_TZ_CACHE.clear()
    fake.absent = set(absent or [])
    fake.seed("storeops", "tenants", [{"org_id": ORG, "timezone": tenant_tz}])
    fake.seed("storeops", "stores", stores)


STORES = [
    {"org_id": ORG, "store_code": "CHI1", "market": "Chicago", "timezone": "America/Chicago"},
    {"org_id": ORG, "store_code": "NY1", "market": "NY", "timezone": "America/New_York"},
    {"org_id": ORG, "store_code": "NOZONE", "market": "NJ", "timezone": None},
]

print("\n(1) resolution: store zone → tenant default → house default")
reset(STORES, tenant_tz=None)
check("Chicago store resolves to America/Chicago", R._biz_tz_for_store(ORG, "CHI1") == CENTRAL)
check("NY store resolves to America/New_York", R._biz_tz_for_store(ORG, "NY1") == EASTERN)
check("a store with no own zone falls back to the tenant default",
      R._biz_tz_for_store(ORG, "NOZONE") == R._biz_tz_for(ORG))
check("an unknown store falls back to the tenant default",
      R._biz_tz_for_store(ORG, "GHOST") == R._biz_tz_for(ORG))
check("no store code at all → tenant default", R._biz_tz_for_store(ORG, "") == R._biz_tz_for(ORG))

print("\n(2) tenant default is honored when a store has no own zone")
reset(STORES, tenant_tz="America/Chicago")
check("NOZONE store inherits the tenant's Central default", R._biz_tz_for_store(ORG, "NOZONE") == CENTRAL)
check("but a store with its OWN zone still wins over the tenant default",
      R._biz_tz_for_store(ORG, "NY1") == EASTERN)

print("\n(3) a 19:00 shift end is read in each store's own zone")
reset(STORES)
chi_end = R._biz_dt_utc(DAY, "19:00", ORG, store_code="CHI1")
ny_end = R._biz_dt_utc(DAY, "19:00", ORG, store_code="NY1")
check("Chicago 19:00 == 7 PM CDT (00:00 UTC next day)",
      chi_end == datetime(2026, 8, 15, 19, 0, tzinfo=CENTRAL).astimezone(timezone.utc), str(chi_end))
check("NY 19:00 == 7 PM EDT (23:00 UTC same day)",
      ny_end == datetime(2026, 8, 15, 19, 0, tzinfo=EASTERN).astimezone(timezone.utc), str(ny_end))
check("the two ends are exactly 1 hour apart (Chicago later)", (chi_end - ny_end) == timedelta(hours=1))

print("\n(4) degrade: no stores table (un-run migration 851) → tenant default, never raises")
reset(STORES, tenant_tz="America/New_York", absent=[("storeops", "stores")])
check("missing stores relation degrades to the tenant zone (no crash)",
      R._biz_tz_for_store(ORG, "CHI1") == EASTERN)

print("\n(5) sweep: at one shared 'now', NY is closed but Chicago is NOT swept early")
reset(STORES)
R._emp_id_variants = lambda org, eid: ({str(eid)}, None)   # avoid an employees lookup
# One open punch per store, each with a 19:00 shift that day.
fake.seed("storeops", "shifts", [
    {"org_id": ORG, "employee_id": "E_CHI", "store_code": "CHI1", "shift_date": DAY, "end_time": "19:00", "is_deleted": False},
    {"org_id": ORG, "employee_id": "E_NY", "store_code": "NY1", "shift_date": DAY, "end_time": "19:00", "is_deleted": False},
])
fake.seed("storeops", "timelog", [
    {"id": "P_CHI", "org_id": ORG, "employee_id": "E_CHI", "store_code": "CHI1", "work_date": DAY,
     "clock_in": datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc).isoformat(), "clock_out": None},
    {"id": "P_NY", "org_id": ORG, "employee_id": "E_NY", "store_code": "NY1", "work_date": DAY,
     "clock_in": datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc).isoformat(), "clock_out": None},
])
# A shared 'now' PAST NY's (7 PM EDT + grace) but BEFORE Chicago's (7 PM CDT + grace, exactly 1h later).
# Derived from the live grace constant so this timezone regression guard holds at any grace value.
GRACE = timedelta(minutes=R.FORCE_CLOCKOUT_GRACE_MIN)
NOW = datetime(2026, 8, 15, 23, 0, tzinfo=timezone.utc) + GRACE + timedelta(minutes=5)


class _Clock:
    @staticmethod
    def now(tz=None): return NOW if tz is None else NOW.astimezone(tz)
    @staticmethod
    def fromisoformat(s): return datetime.fromisoformat(s)


_real_dt = R.datetime
R.datetime = _Clock
try:
    ny_end = R._scheduled_end_for_punch(ORG, {"employee_id": "E_NY", "store_code": "NY1", "work_date": DAY})
    chi_end = R._scheduled_end_for_punch(ORG, {"employee_id": "E_CHI", "store_code": "CHI1", "work_date": DAY})
    check("NY scheduled end computed at 7 PM EDT", ny_end == datetime(2026, 8, 15, 23, 0, tzinfo=timezone.utc), str(ny_end))
    check("Chicago scheduled end computed at 7 PM CDT (an hour later in UTC)",
          chi_end == datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc), str(chi_end))
    res = R._do_force_clockout(org_id=ORG)
    swept = {c["employee_id"] for c in res.get("detail", [])}
    check("NY punch IS swept (past its own 7 PM ET + grace)", "E_NY" in swept, str(res))
    check("Chicago punch is NOT swept early (still before its own 7 PM CT + grace)",
          "E_CHI" not in swept, str(res))
    tl = {r["id"]: r for r in fake.store[("storeops", "timelog")]}
    check("Chicago punch left open (clock_out still null)", tl["P_CHI"].get("clock_out") is None)
    check("NY punch closed at 7 PM EDT + grace",
          tl["P_NY"].get("clock_out") == (datetime(2026, 8, 15, 23, 0, tzinfo=timezone.utc) + GRACE).isoformat())
finally:
    R.datetime = _real_dt

print("\n(6) overnight shift: a scheduled end past midnight rolls to the NEXT day (not ~a day early)")
reset(STORES)
R._emp_id_variants = lambda org, eid: ({str(eid)}, None)
fake.seed("storeops", "shifts", [
    # wraps: starts 18:00, ends 00:30 the following morning
    {"org_id": ORG, "employee_id": "E_OVN", "store_code": "NY1", "shift_date": DAY,
     "start_time": "18:00", "end_time": "00:30", "is_deleted": False},
    # normal daytime shift — must NEVER be rolled
    {"org_id": ORG, "employee_id": "E_DAY", "store_code": "NY1", "shift_date": DAY,
     "start_time": "09:00", "end_time": "17:00", "is_deleted": False},
])
ovn_end = R._scheduled_end_for_punch(ORG, {"employee_id": "E_OVN", "store_code": "NY1", "work_date": DAY})
check("overnight end (00:30 ≤ 18:00 start) rolls to the next day in the store zone",
      ovn_end == datetime(2026, 8, 16, 0, 30, tzinfo=EASTERN).astimezone(timezone.utc), str(ovn_end))
day_end = R._scheduled_end_for_punch(ORG, {"employee_id": "E_DAY", "store_code": "NY1", "work_date": DAY})
check("a normal daytime shift end (17:00 > 09:00) is NOT rolled",
      day_end == datetime(2026, 8, 15, 17, 0, tzinfo=EASTERN).astimezone(timezone.utc), str(day_end))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1)
