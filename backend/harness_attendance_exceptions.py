"""Offline proof (no live DB/network) for the 2026-08-06 owner directive (mod-people, branch
agent/people/timeclock-attendance-exceptions): "time clock should show who were scheduled and didn't
clock in and also if somebody else clocked in instead of the scheduled".

SECTION A — resolve_config: defaults, clamping.
SECTION B — get_tenant_attendance_config: missing-table / pre-migration-row / migrated-row degrade,
            mirroring lunch_deduction's own availability-signal proof.
SECTION C — compute_attendance_exceptions (the pure classifier): no_show, don't-flag-the-future,
            excused-by-timeoff (label AND suppress modes), covered_by_other (same-store, cross-store,
            2-coverers), unscheduled (incl. the "same join, other side" pairing with a cover),
            late, left_early, late_and_left_early combined, multi-session-same-day (both the
            no-false-positive case and the union-of-sessions case), open-punch (still clocked in =
            not absent), self cross-store presence (not a no-show, `same_store=False` surfaced, not
            dropped), timezone correctness across a UTC-midnight-crossing evening ET shift, an
            overnight (crosses-midnight) shift, and unparseable/missing shift times never crashing.
SECTION D — router-level wiring (GET /storeops/timeclock/attendance-exceptions) against an in-memory
            fake Supabase client: org scoping, numeric-vs-business employee_id canonicalization
            (payroll_identity.business_id_alias_map, the SAME reconciliation every other write path in
            this file already applies), RBAC store-span narrowing, and the pre-migration-421 config
            degrade (still classifies correctly on code defaults, never a 500).

Run: `python3 harness_attendance_exceptions.py` from backend/.
"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = timezone.utc

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION A — resolve_config
# ══════════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.storeops.attendance_exceptions import (  # noqa: E402
    DEFAULT_CONFIG, resolve_config, get_tenant_attendance_config, compute_attendance_exceptions,
)

check("A1 no overrides -> byte-identical to DEFAULT_CONFIG", resolve_config(None) == DEFAULT_CONFIG)
check("A2 partial override merges onto defaults, doesn't drop other keys",
      resolve_config({"late_grace_min": 5}) == {**DEFAULT_CONFIG, "late_grace_min": 5})
check("A3 negative grace clamps to 0 (never a negative window)",
      resolve_config({"noshow_grace_min": -10})["noshow_grace_min"] == 0)
check("A4 garbage grace value ignored, default kept",
      resolve_config({"coverage_overlap_min": "not-a-number"})["coverage_overlap_min"] == DEFAULT_CONFIG["coverage_overlap_min"])
check("A5 unknown timeoff_mode ignored, default 'label' kept",
      resolve_config({"timeoff_mode": "delete-everything"})["timeoff_mode"] == "label")
check("A6 'suppress' timeoff_mode accepted", resolve_config({"timeoff_mode": "SUPPRESS"})["timeoff_mode"] == "suppress")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION B — get_tenant_attendance_config availability signal
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class _RaisingTable:
    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self
    def execute(self): raise Exception("relation storeops.tenants does not exist")


class _RaisingClient:
    def table(self, _t): return _RaisingTable()


cfg, avail = get_tenant_attendance_config("org-1", _RaisingClient())
check("B1 raising client -> unavailable, but config is still the full usable DEFAULT_CONFIG",
      avail is False and cfg == DEFAULT_CONFIG, cfg)


class _Result:
    def __init__(self, data): self.data = data


class _StubQuery:
    def __init__(self, rows): self._rows = rows
    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self
    def execute(self): return _Result(self._rows)


class _StubClient:
    def __init__(self, rows): self._rows = rows
    def table(self, _t): return _StubQuery(self._rows)


# pre-migration-421 fixture: a real tenants row that predates the new columns (shape of every
# pre-existing fixture in this repo, same convention as lunch_deduction's own B-section proof).
cfg2, avail2 = get_tenant_attendance_config("org-1", _StubClient([{"org_id": "org-1"}]))
check("B2 pre-migration row (no attendance_* keys) -> unavailable, defaults returned",
      avail2 is False and cfg2 == DEFAULT_CONFIG, cfg2)

# migrated row with a real tenant override.
migrated_row = {
    "attendance_late_grace_min": 5, "attendance_early_leave_grace_min": 5,
    "attendance_noshow_grace_min": 15, "attendance_coverage_overlap_min": 20,
    "attendance_timeoff_mode": "suppress",
}
cfg3, avail3 = get_tenant_attendance_config("org-1", _StubClient([migrated_row]))
check("B3 migrated row -> available=True, overrides applied",
      avail3 is True and cfg3 == {"late_grace_min": 5, "early_leave_grace_min": 5, "noshow_grace_min": 15,
                                   "coverage_overlap_min": 20, "timeoff_mode": "suppress"}, cfg3)

cfg4, avail4 = get_tenant_attendance_config("org-1", _StubClient([]))
check("B4 no tenant row at all -> unavailable, defaults", avail4 is False and cfg4 == DEFAULT_CONFIG, cfg4)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION C — compute_attendance_exceptions, the pure classifier
# ══════════════════════════════════════════════════════════════════════════════════════════════════
WD = "2026-08-10"   # a Monday, arbitrary
CFG = dict(DEFAULT_CONFIG)   # late=10, early=10, noshow_grace=30, overlap=15, timeoff_mode='label'


def shift(id, eid, name, store, start, end, wd=WD, deleted=False):
    return {"id": id, "employee_id": eid, "employee_name": name, "store_code": store,
            "shift_date": wd, "start_time": start, "end_time": end, "is_deleted": deleted}


def biz(wd, hhmm, tz=ET):
    h, m = [int(x) for x in hhmm.split(":")]
    return datetime.fromisoformat(f"{wd}T{h:02d}:{m:02d}:00").replace(tzinfo=tz).astimezone(timezone.utc)


def punch(id, eid, name, store, ci_hhmm, co_hhmm=None, wd=WD, tz=ET, ci_wd=None, co_wd=None):
    ci = biz(ci_wd or wd, ci_hhmm, tz).isoformat()
    co = biz(co_wd or wd, co_hhmm, tz).isoformat() if co_hhmm else None
    return {"id": id, "employee_id": eid, "employee_name": name, "store_code": store,
            "work_date": wd, "clock_in": ci, "clock_out": co}


NOW_LATE = biz(WD, "18:00")   # well after every 9-5 shift's grace window -> no-show-eligible
NOW_EARLY = biz(WD, "09:05")  # before a 9am shift's own grace window has elapsed


def run(shifts, punches, timeoff=None, now=NOW_LATE, cfg=None, tz=ET):
    return compute_attendance_exceptions(shifts, punches or [], timeoff or [], cfg or CFG, now, tz)


# C1 — the headline ask: plain no-show.
rows = run([shift("s1", "E1", "Jane Doe", "S1", "09:00", "17:00")], [])
check("C1 plain no-show emitted, not excused", len(rows) == 1 and rows[0]["exception_type"] == "no_show"
      and rows[0]["excused"] is False, rows)

# C2 — don't flag the future: same shift, but "now" is before shift_start+grace.
rows = run([shift("s1", "E1", "Jane Doe", "S1", "09:00", "17:00")], [], now=NOW_EARLY)
check("C2 shift not due yet (inside grace window) -> NO row at all", rows == [], rows)

rows = run([shift("s1", "E1", "Jane Doe", "S1", "20:00", "23:00")], [], now=biz(WD, "12:00"))
check("C2b a shift later TODAY (hasn't started) -> NO row at all", rows == [], rows)

# C3 — excused by approved time off, mode='label' (default): row still emitted, tagged excused.
tor = [{"employee_id": "E1", "start_date": WD, "end_date": WD, "status": "approved", "type": "PTO", "notes": ""}]
rows = run([shift("s1", "E1", "Jane Doe", "S1", "09:00", "17:00")], [], timeoff=tor)
check("C3 excused no-show (label mode) STILL emitted, exception_type stays no_show, excused=True + reason",
      len(rows) == 1 and rows[0]["exception_type"] == "no_show" and rows[0]["excused"] is True
      and rows[0]["excused_reason"] == "PTO", rows)

# C3b — a PENDING (not approved) time-off request must NOT excuse anything.
tor_pending = [{"employee_id": "E1", "start_date": WD, "end_date": WD, "status": "pending", "type": "PTO"}]
rows = run([shift("s1", "E1", "Jane Doe", "S1", "09:00", "17:00")], [], timeoff=tor_pending)
check("C3b a PENDING (not approved) time-off request does NOT excuse", rows[0]["excused"] is False, rows)

# C4 — excused by approved time off, mode='suppress': the row is dropped entirely.
suppress_cfg = {**CFG, "timeoff_mode": "suppress"}
rows = run([shift("s1", "E1", "Jane Doe", "S1", "09:00", "17:00")], [], timeoff=tor, cfg=suppress_cfg)
check("C4 excused no-show (suppress mode) -> dropped, no row at all", rows == [], rows)

# C5 — covered by other, SAME store.
shifts5 = [shift("s1", "E1", "Jane Doe", "S1", "09:00", "17:00")]
punches5 = [punch("p1", "E2", "John Smith", "S1", "07:58", "16:12")]
rows = run(shifts5, punches5)
cov5 = [r for r in rows if r["exception_type"] == "covered_by_other"]
check("C5 covered_by_other (same store), exactly 1 coverer, same_store=True",
      len(cov5) == 1 and len(cov5[0]["coverers"]) == 1
      and cov5[0]["coverers"][0]["employee_id"] == "E2" and cov5[0]["coverers"][0]["same_store"] is True, rows)

# C6 — covered by other, CROSS-store: reported, not silently dropped.
punches6 = [punch("p1", "E2", "John Smith", "S2", "08:00", "16:00")]
rows = run(shifts5, punches6)
cov6 = [r for r in rows if r["exception_type"] == "covered_by_other"]
check("C6 cross-store cover is REPORTED (not dropped), same_store=False, worked store visible",
      len(cov6) == 1
      and cov6[0]["coverers"][0]["same_store"] is False and cov6[0]["coverers"][0]["store_code"] == "S2", rows)

# C7 — TWO coverers, sorted by clock_in.
punches7 = [punch("p1", "E3", "Late Cover", "S1", "12:00", "17:00"),
            punch("p2", "E2", "John Smith", "S1", "07:58", "16:12")]
rows = run(shifts5, punches7)
cov7 = [r for r in rows if r["exception_type"] == "covered_by_other"]
c = cov7[0]["coverers"] if cov7 else []
check("C7 more than one coverer -> ALL listed", len(cov7) == 1 and len(c) == 2, rows)
check("C7b coverers sorted by clock_in (earliest first)", len(c) == 2 and c[0]["employee_id"] == "E2" and c[1]["employee_id"] == "E3", c)

# C8 — unscheduled: a punch with no matching shift for that employee AT THAT STORE that day. Also
# proves the "same join, other side" claim: John's OWN cover-punch from C5 is unscheduled from HIS
# point of view (he has no shift of his own that day) even though it covered Jane's shift.
rows = run(shifts5, punches5)   # John (E2) covers Jane's S1 shift, has no shift of his own
unsched = [r for r in rows if r["exception_type"] == "unscheduled"]
check("C8 the coverer's own punch is ALSO reported unscheduled (same join, other side)",
      len(unsched) == 1 and unsched[0]["employee_id"] == "E2" and unsched[0]["punch_id"] == "p1", rows)

# C8b — a punch with a matching SAME-store shift is never flagged unscheduled.
shifts8b = [shift("s1", "E1", "Jane Doe", "S1", "09:00", "17:00")]
punches8b = [punch("p1", "E1", "Jane Doe", "S1", "08:58", "17:03")]
rows = run(shifts8b, punches8b)
check("C8b a normally-scheduled, on-time, same-store punch is never flagged unscheduled (or anything else)", rows == [], rows)

# C9 — late (own shift, own store), grace-aware.
shifts9 = [shift("s1", "E1", "Jane Doe", "S1", "09:00", "17:00")]
punches9 = [punch("p1", "E1", "Jane Doe", "S1", "09:25", "17:00")]   # 25 min late, grace=10
rows = run(shifts9, punches9)
check("C9 late flagged with correct minutes_late (25 - 0 grace-anchor => 25 late from shift start)",
      len(rows) == 1 and rows[0]["exception_type"] == "late" and rows[0]["is_late"] is True
      and rows[0]["minutes_late"] == 25 and rows[0]["is_left_early"] is False, rows)

# C9b — within grace: NOT late.
rows = run(shifts9, [punch("p1", "E1", "Jane Doe", "S1", "09:07", "17:00")])
check("C9b 7 minutes late is WITHIN the 10-minute grace -> no exception row", rows == [], rows)

# C10 — left early.
rows = run(shifts9, [punch("p1", "E1", "Jane Doe", "S1", "09:00", "16:30")])   # 30 min early, grace=10
check("C10 left_early flagged with correct minutes_early",
      len(rows) == 1 and rows[0]["exception_type"] == "left_early" and rows[0]["minutes_early"] == 30
      and rows[0]["is_late"] is False, rows)

# C11 — both late AND left early on the same shift.
rows = run(shifts9, [punch("p1", "E1", "Jane Doe", "S1", "09:20", "16:40")])
check("C11 late_and_left_early combined, both flags true",
      len(rows) == 1 and rows[0]["exception_type"] == "late_and_left_early"
      and rows[0]["is_late"] and rows[0]["is_left_early"], rows)

# C12 — multi-session same day (a real lunch re-clock-in): on-time first-in, on-time last-out across
# TWO closed punch pairs -> no false positive.
shifts12 = [shift("s1", "E1", "Jane Doe", "S1", "09:00", "17:00")]
punches12 = [punch("p1", "E1", "Jane Doe", "S1", "08:58", "12:00"),
             punch("p2", "E1", "Jane Doe", "S1", "13:00", "17:02")]
rows = run(shifts12, punches12)
check("C12 multi-session same day, on time both ends -> NOT a false late/early/no-show", rows == [], rows)

# C12b — the UNION rule: first_in = earliest session's clock_in, last_out = LATEST closed session's
# clock_out (not just whichever punch happens to be last in the list) -> left_early judged off the
# actually-last session even when rows are given out of chronological order.
punches12b = [punch("p2", "E1", "Jane Doe", "S1", "13:00", "16:20"),   # afternoon session, leaves early
              punch("p1", "E1", "Jane Doe", "S1", "08:58", "12:00")]   # morning session, listed SECOND
rows = run(shifts12, punches12b)
check("C12b left_early correctly judged off the UNION's latest closed clock_out (16:20), not row order",
      len(rows) == 1 and rows[0]["exception_type"] == "left_early"
      and rows[0]["actual_clock_out"] == biz(WD, "16:20").isoformat(), rows)

# C13 — open punch: still clocked in as of "now" = present, not absent, and never judged left-early.
shifts13 = [shift("s1", "E1", "Jane Doe", "S1", "09:00", "17:00")]
punches13 = [punch("p1", "E1", "Jane Doe", "S1", "08:58")]   # no clock_out yet
rows = run(shifts13, punches13, now=biz(WD, "13:00"))
check("C13 still-open punch covering shift start -> NOT a no-show, NOT left-early (no row at all)", rows == [], rows)

# C14 — self cross-store presence: scheduled at S1, actually punched in at S2 (late enough to also
# produce a visible row so the same_store/worked_store_code fields are inspectable). Not a no-show —
# they worked, just not at the scheduled store — and the fact is surfaced, not dropped.
shifts14 = [shift("s1", "E1", "Jane Doe", "S1", "09:00", "17:00")]
punches14 = [punch("p1", "E1", "Jane Doe", "S2", "09:20", "17:00")]   # 20 min late, at a DIFFERENT store
rows = run(shifts14, punches14)
main_row = [r for r in rows if r["exception_type"] in ("late", "left_early", "late_and_left_early")]
check("C14 cross-store self-presence is NOT a no-show, but IS still evaluated late/early against the schedule",
      len(main_row) == 1 and main_row[0]["same_store"] is False and main_row[0]["worked_store_code"] == "S2", rows)
unsched14 = [r for r in rows if r["exception_type"] == "unscheduled"]
check("C14b the SAME punch is ALSO independently unscheduled at S2 (no shift of theirs there) — two true facts, not a contradiction",
      len(unsched14) == 1 and unsched14[0]["punch_id"] == "p1", rows)

# C15 — timezone correctness: an evening ET shift whose UTC instant crosses into the NEXT UTC calendar
# date must still classify correctly via aware-instant comparison, never a UTC-date-string mixup.
shifts15 = [shift("s1", "E1", "Jane Doe", "S1", "21:00", "23:30", wd=WD)]   # 9pm-11:30pm ET
# 21:00 ET on 2026-08-10 (EDT, UTC-4) = 2026-08-11 01:00 UTC — genuinely crosses the UTC date line.
check("C15 setup sanity: the shift's own UTC instant really does fall on the NEXT utc calendar date",
      biz(WD, "21:00").date().isoformat() != WD)
punches15 = [punch("p1", "E1", "Jane Doe", "S1", "20:58", "23:32", wd=WD)]   # on time, same business work_date
rows = run(shifts15, punches15, now=biz(WD, "23:59"))
check("C15b evening ET shift entirely spanning the UTC midnight boundary still resolves on-time, no false exception",
      rows == [], rows)
punches15b = [punch("p1", "E1", "Jane Doe", "S1", "21:25", "23:32", wd=WD)]   # 25 min late
rows = run(shifts15, punches15b, now=biz(WD, "23:59"))
check("C15c ...and STILL correctly detects a genuine 25-min-late punch across that same UTC boundary",
      len(rows) == 1 and rows[0]["exception_type"] == "late" and rows[0]["minutes_late"] == 25, rows)

# C16 — overnight shift (end_time earlier than start_time) rolls to the next calendar day rather than
# being treated as zero/negative length.
shifts16 = [shift("s1", "E1", "Jane Doe", "S1", "22:00", "06:00", wd=WD)]
punches16 = [punch("p1", "E1", "Jane Doe", "S1", "21:58", wd=WD, co_hhmm="06:05", co_wd="2026-08-11")]
rows = run(shifts16, punches16, now=biz("2026-08-11", "07:00"))
check("C16 overnight shift (22:00->06:00) is NOT a false no-show/negative-length exception when covered", rows == [], rows)

shifts16b = [shift("s1", "E1", "Jane Doe", "S1", "22:00", "06:00", wd=WD)]
rows = run(shifts16b, [], now=biz(WD, "22:45"))
check("C16b overnight shift correctly flags NO_SHOW once past start+grace even though end_time < start_time",
      len(rows) == 1 and rows[0]["exception_type"] == "no_show", rows)

# C17 — unparseable / missing shift start_time never crashes, just skips (no classification possible).
shifts17 = [shift("s1", "E1", "Jane Doe", "S1", None, "17:00"), shift("s2", "E1", "Jane Doe", "S1", "not-a-time", "17:00")]
try:
    rows = run(shifts17, [])
    check("C17 missing/unparseable start_time never crashes; simply produces no exception for that shift", rows == [], rows)
except Exception as e:
    check("C17 missing/unparseable start_time never crashes", False, e)

# C18 — a deleted shift is never classified even if unmatched.
shifts18 = [shift("s1", "E1", "Jane Doe", "S1", "09:00", "17:00", deleted=True)]
rows = run(shifts18, [])
check("C18 a soft-deleted shift is ignored entirely (no no-show for a cancelled shift)", rows == [], rows)


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("SECTION A-C FAILURES:")
    for f in FAIL:
        print(" -", f)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION D — router-level wiring: GET /storeops/timeclock/attendance-exceptions
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class Result:
    def __init__(self, data): self.data = data


class FakeQuery:
    def __init__(self, store, key):
        self.store, self.key, self.filters = store, key, []
        self._limit = None
        self._mode, self._payload = None, None

    def select(self, *_a, **_k): self._mode = self._mode or "select"; return self
    def eq(self, k, v): self.filters.append(("eq", k, v)); return self
    def in_(self, k, vals): self.filters.append(("in", k, set(str(x) for x in vals))); return self
    def gte(self, k, v): self.filters.append(("gte", k, v)); return self
    def lte(self, k, v): self.filters.append(("lte", k, v)); return self
    def order(self, *_a, **_k): return self
    def limit(self, n): self._limit = n; return self
    def update(self, payload): self._mode = "update"; self._payload = payload; return self

    def _matches(self, row):
        for kind, k, v in self.filters:
            rv = row.get(k)
            if kind == "eq" and str(rv) != str(v):
                return False
            if kind == "in" and str(rv) not in v:
                return False
            if kind == "gte" and str(rv) < str(v):
                return False
            if kind == "lte" and str(rv) > str(v):
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        matched = [r for r in rows if self._matches(r)]
        if self._mode == "update":
            for r in matched:
                r.update(self._payload)
            return Result(matched)
        if self._limit is not None:
            matched = matched[: self._limit]
        return Result(matched)


class FakeSchema:
    def __init__(self, client, name): self.client, self.name = client, name
    def table(self, t): return FakeQuery(self.client.store, (self.name, t))


class FakeClient:
    def __init__(self): self.store = {}
    def schema(self, name): return FakeSchema(self, name)
    def table(self, t): return FakeQuery(self.store, ("storeops", t))
    def seed(self, schema, table, rows): self.store[(schema, table)] = [dict(r) for r in rows]


fake = FakeClient()

import app.modules.storeops.router as R  # noqa: E402
import app.modules.core.router as core_router  # noqa: E402

R.get_supabase = lambda: fake
R.sb = lambda: fake.schema("storeops")
core_router._uid_from_token = lambda auth: "uid-1"

ORG = "org-att-1"
# Section D calls the REAL router handler, which reads the REAL wall clock (datetime.now(timezone.utc))
# internally (unlike Section C, which passes its own controlled `now`) — so its fixture date must be
# safely in the PAST relative to whenever this harness actually runs, or the "don't flag the future"
# rule would correctly (but inconveniently, for a fixed test date) suppress every row. 3 days back is
# comfortably past any grace window regardless of which business timezone is in play.
D_WD = (datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat()


def reset():
    fake.store.clear()
    fake.seed("storeops", "tenants", [{"org_id": ORG}])   # no attendance_* columns -> pre-migration
    fake.seed("storeops", "app_users", [{"org_id": ORG, "auth_id": "uid-1", "employee_id": "E1", "role": "admin"}])
    fake.seed("storeops", "employees", [
        {"org_id": ORG, "id": 101, "employee_id": "E1", "name": "Jane Doe", "is_active": True},
        {"org_id": ORG, "id": 102, "employee_id": "E2", "name": "John Smith", "is_active": True},
    ])
    fake.seed("storeops", "roles", [])


AUTH = "Bearer x"

reset()
# Schedule-page-shaped shift: employee_id is the NUMERIC row id (101), same real-world bug
# payroll_identity.py documents — the endpoint must canonicalize this to "E1" before joining against
# timelog's business-id-keyed punches, or Jane's own coverage would never match her own shift.
fake.seed("storeops", "shifts", [
    {"org_id": ORG, "id": "sh1", "employee_id": "101", "employee_name": "Jane Doe", "store_code": "S1",
     "shift_date": D_WD, "start_time": "09:00", "end_time": "17:00", "is_deleted": False},
])
fake.seed("storeops", "timelog", [])   # Jane never punched
fake.seed("storeops", "time_off_requests", [])

resp = R.attendance_exceptions(start=D_WD, end=D_WD, authorization=AUTH, org_id=ORG)
check("D1 endpoint reachable, returns a dict with rows/available/config",
      isinstance(resp, dict) and "rows" in resp and "available" in resp and "config" in resp, resp)
check("D2 numeric-id shift (Schedule-page shape) still resolves to a no-show via business-id canonicalization",
      len(resp["rows"]) == 1 and resp["rows"][0]["exception_type"] == "no_show"
      and resp["rows"][0]["employee_id"] == "E1", resp)
check("D3 pre-migration-421 tenant row -> available=False, config is still the full DEFAULT_CONFIG (degrade, not 500)",
      resp["available"] is False and resp["config"] == DEFAULT_CONFIG, resp["config"])

# D4 — org isolation: an identical shift in ANOTHER org must never leak into this org's response.
fake.store[("storeops", "shifts")].append(
    {"org_id": "org-OTHER", "id": "sh-other", "employee_id": "E9", "employee_name": "Ghost",
     "store_code": "S1", "shift_date": D_WD, "start_time": "09:00", "end_time": "17:00", "is_deleted": False})
resp2 = R.attendance_exceptions(start=D_WD, end=D_WD, authorization=AUTH, org_id=ORG)
check("D4 a different org's shift never leaks into this org's exceptions (multi-tenant RULE ONE)",
      all(r.get("employee_id") != "E9" for r in resp2["rows"]), resp2["rows"])

# D5 — covered-by-other end to end through the router (John's punch, business-id-keyed as timelog
# always is, covers Jane's numeric-id-keyed shift).
fake.seed("storeops", "timelog", [
    {"org_id": ORG, "id": "t1", "employee_id": "E2", "employee_name": "John Smith", "store_code": "S1",
     "work_date": D_WD, "clock_in": biz(D_WD, "07:58").isoformat(), "clock_out": biz(D_WD, "16:12").isoformat()},
])
resp3 = R.attendance_exceptions(start=D_WD, end=D_WD, authorization=AUTH, org_id=ORG)
covered = [r for r in resp3["rows"] if r["exception_type"] == "covered_by_other"]
check("D5 covered_by_other end-to-end through the real router handler",
      len(covered) == 1 and covered[0]["coverers"][0]["employee_id"] == "E2", resp3["rows"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SECTION E — the RULE TWO admin config endpoints (GET/PUT /timeclock/attendance-config)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
r = R.get_attendance_config(authorization=AUTH, org_id=ORG)
check("E1 GET config returns the full default config pre-migration, available=False",
      r["available"] is False and r["config"] == DEFAULT_CONFIG, r)

r2 = R.set_attendance_config({"late_grace_min": 15, "timeoff_mode": "suppress"}, authorization=AUTH, org_id=ORG)
check("E2 PUT config (as admin) persists the change and returns the resolved config",
      r2["ok"] is True and r2["config"]["late_grace_min"] == 15 and r2["config"]["timeoff_mode"] == "suppress", r2)
persisted = fake.store[("storeops", "tenants")][0]
check("E3 PUT actually wrote the attendance_* columns onto the tenants row (not just the response)",
      persisted.get("attendance_late_grace_min") == 15 and persisted.get("attendance_timeoff_mode") == "suppress", persisted)

# a non-manager ('rep' role) must be rejected.
fake.seed("storeops", "app_users", [{"org_id": ORG, "auth_id": "uid-1", "employee_id": "E1", "role": "rep"}])
try:
    R.set_attendance_config({"late_grace_min": 1}, authorization=AUTH, org_id=ORG)
    check("E4 a non-manager PUT is rejected", False, "no exception raised")
except Exception as e:
    check("E4 a non-manager PUT is rejected (403)", getattr(e, "status_code", None) == 403, e)
fake.seed("storeops", "app_users", [{"org_id": ORG, "auth_id": "uid-1", "employee_id": "E1", "role": "admin"}])   # restore

print(f"\n{len(PASS)} passed, {len(FAIL)} failed (cumulative, all sections)")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
