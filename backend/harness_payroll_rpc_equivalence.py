"""Equivalence proof for the mig-407 payroll RPC fast path (mod-people P0 perf package,
2026-07-22). MONEY-ADJACENT: GET /storeops/payroll and GET /storeops/payroll-by-store now try
storeops.payroll_month_rows() (Postgres-side per-(employee, store) aggregation, migration 407)
and fall back to the legacy full-row Python aggregation when the RPC is missing/failing. This
harness runs the ACTUAL shipped handlers from app.modules.storeops.router against an in-memory
fake Supabase client twice — once with the RPC unavailable (LEGACY path) and once with a fake
RPC that simulates migration 407's SQL aggregation row-for-row (FAST path) — and asserts the
endpoint outputs are BYTE-IDENTICAL (json.dumps string equality).
Run: `python3 harness_payroll_rpc_equivalence.py` from backend/.

Covered semantics (each present in the fixture):
  * per-row actual==0 -> scheduled fallback (incl. a sched=0/act>0 row summed into the same emp)
  * timelog fallback: only CLOSED punches; no-double-count vs shift days; a BLANK-store shift
    still blocks its day; a soft-DELETED shift does NOT block its day; open punch excluded
  * dominant-store attribution incl. an exact tie (first-seen store wins, legacy insertion order)
  * NULL employee_id shift row survives with its own bucket + store attribution
  * INACTIVE employee: rate 0 in /payroll (active-only emp_map) but real rate in /payroll-by-store
    (all-employees rate_map)
  * same-name employees keep stable (insertion-order) sort
  * scope_keyset filtering applied identically on both paths
  * empty month; month=None (fast path must NOT engage — legacy semantics preserved)
  * org isolation (second tenant)
  * 2026-07-25 (arbitrary pay-period ranges): start/end == an exact calendar month is
    byte-identical to the legacy month= path (both fast + legacy); malformed range rejected
    (400); a non-month-aligned multi-week range is hand-computed + fast==legacy; org isolation
    holds in range mode too
"""
import json
import sys
from datetime import datetime
from fastapi import HTTPException

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── Fake Supabase client (same pattern as harness_payroll_data_flow.py) ─────────────────────────
class FakeQuery:
    def __init__(self, store, table):
        self.store = store
        self.table_name = table
        self.filters = []
        self._limit = None

    def select(self, cols=None):
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def in_(self, k, vals):
        self.filters.append(("in", k, set(str(x) for x in vals))); return self

    def gte(self, k, v):
        self.filters.append(("gte", k, v)); return self

    def lt(self, k, v):
        self.filters.append(("lt", k, v)); return self

    def limit(self, n):
        self._limit = n; return self

    def _match(self, row):
        for op, k, v in self.filters:
            rv = row.get(k)
            if op == "eq" and rv != v:
                return False
            if op == "in" and str(rv) not in v:
                return False
            if op == "gte" and not (rv is not None and str(rv) >= str(v)):
                return False
            if op == "lt" and not (rv is not None and str(rv) < str(v)):
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        matched = [r for r in rows if self._match(r)]
        if self._limit:
            matched = matched[: self._limit]
        return FakeResult(matched)


class FakeResult:
    def __init__(self, data):
        self.data = data


def _btrim(v):
    # SQL btrim(x, ' ') default: trims SPACES (the handler re-strips anyway).
    return (v if v is not None else "").strip(" ")


def _epoch(iso):
    return datetime.fromisoformat(iso).timestamp()


def simulate_payroll_month_rows(store, p_org_id, p_lo, p_hi):
    """Python mirror of storeops.payroll_month_rows() (migrations 407 -> 913 -> 914) — the SQL's exact
    filters/grouping/expressions, so the FAST path is fed what Postgres would return.

    PUNCH-DRIVEN PAY (migration 914): a shift contributes actual_hours if >0 (manual wins), else 0 on a
    punch-driven day (a closed punch that (emp, day) with no manual correction), else scheduled_hours;
    and a punch is suppressed ONLY by a manual correction (actual_hours>0) that day, never by a
    merely-scheduled shift. Mirrors storeops._payroll_day_is_punch_driven + the anti-join."""
    # (emp, day) grain sets used by BOTH legs, matching the SQL EXISTS/NOT EXISTS predicates.
    manual_days = {(s.get("employee_id"), str(s.get("shift_date") or ""))
                   for s in store.get("shifts", [])
                   if s.get("org_id") == p_org_id and s.get("is_deleted") is False
                   and float(s.get("actual_hours") or 0) > 0}
    punch_days = {(t.get("employee_id"), str(t.get("work_date") or ""))
                  for t in store.get("timelog", [])
                  if t.get("org_id") == p_org_id
                  and t.get("clock_out") is not None and t.get("hours") is not None}

    def _is_punch_driven(eid, day):
        return (eid, day) in punch_days and (eid, day) not in manual_days

    out = []
    # ── shift branch ────────────────────────────────────────────────────────────────────────────
    groups = {}
    for s in store.get("shifts", []):
        if s.get("org_id") != p_org_id or s.get("is_deleted") is not False:
            continue                                   # org + is_deleted = false
        sd = str(s.get("shift_date") or "")
        if not sd or sd < p_lo or sd >= p_hi:
            continue                                   # shift_date in [p_lo, p_hi)
        groups.setdefault((s.get("employee_id"), _btrim(s.get("store_code"))), []).append(s)
    for (eid, st), rows_ in groups.items():
        by_id = sorted(rows_, key=lambda r: r["id"])   # shifts.id = bigserial
        sched_sum = act_eff = hrs_eff = 0.0
        for r in rows_:
            sched = float(r.get("scheduled_hours") or 0)
            act = float(r.get("actual_hours") or 0)
            sched_sum += sched
            # PUNCH-DRIVEN: manual(act>0) -> act ; punch-driven day -> 0 ; else scheduled fallback.
            if act > 0:
                contrib = act
            elif _is_punch_driven(r.get("employee_id"), str(r.get("shift_date") or "")):
                contrib = 0.0
            else:
                contrib = sched
            act_eff += contrib                         # /payroll basis
            hrs_eff += contrib                         # /payroll-by-store basis (identical since mig 914)
        out.append({"kind": "shift", "employee_id": eid, "store_code": st,
                    "employee_name": by_id[0].get("employee_name"),
                    "first_ord": float(by_id[0]["id"]),
                    "scheduled_sum": sched_sum, "actual_eff_sum": act_eff,
                    "hours_eff_sum": hrs_eff, "shift_count": len(rows_),
                    "timelog_hours_sum": 0.0})
    # ── timelog branch ──────────────────────────────────────────────────────────────────────────
    tgroups = {}
    for t in store.get("timelog", []):
        if t.get("org_id") != p_org_id:
            continue
        wd = str(t.get("work_date") or "")
        if not wd or wd < p_lo or wd >= p_hi:
            continue
        if t.get("clock_out") is None or t.get("hours") is None:
            continue                                   # CLOSED punches only
        eid = t.get("employee_id")
        if eid is None or eid == "":
            continue
        if (eid, wd) in manual_days:
            continue                                   # NOT EXISTS manual correction same emp/day (mig 914)
        tgroups.setdefault((eid, _btrim(t.get("store_code"))), []).append(t)
    for (eid, st), rows_ in tgroups.items():
        ordered = sorted(rows_, key=lambda r: (r.get("created_at") is None,
                                               str(r.get("created_at") or ""), str(r.get("id") or "")))
        cts = [r.get("created_at") for r in rows_ if r.get("created_at") is not None]
        out.append({"kind": "timelog", "employee_id": eid, "store_code": st,
                    "employee_name": ordered[0].get("employee_name"),
                    "first_ord": _epoch(min(cts)) if cts else None,
                    "scheduled_sum": 0.0, "actual_eff_sum": 0.0, "hours_eff_sum": 0.0,
                    "shift_count": 0,
                    "timelog_hours_sum": sum(float(r.get("hours") or 0) for r in rows_)})
    # Postgres gives NO ordering guarantee across the UNION ALL — return rows deliberately
    # scrambled so the handler's own first_ord sorting is what restores legacy order.
    out.sort(key=lambda g: (str(g["store_code"]), str(g["employee_id"])), reverse=True)
    return out


class FakeRpcQuery:
    def __init__(self, client, fn, params):
        self.client, self.fn, self.params = client, fn, params

    def execute(self):
        if not self.client.rpc_enabled:
            raise RuntimeError(f"function storeops.{self.fn} does not exist (migration 407 not run)")
        assert self.fn == "payroll_month_rows", self.fn
        self.client.rpc_calls += 1
        return FakeResult(simulate_payroll_month_rows(
            self.client.store, self.params["p_org_id"], self.params["p_lo"], self.params["p_hi"]))


class FakeSchemaClient:
    def __init__(self, store):
        self.store = store
        self.rpc_enabled = False   # False = mig 407 "not run yet" -> handlers must use LEGACY path
        self.rpc_calls = 0

    def schema(self, name):
        return self

    def table(self, name):
        return FakeQuery(self.store, name)

    def rpc(self, fn, params):
        return FakeRpcQuery(self, fn, params)


STORE = {}
FAKE_CLIENT = FakeSchemaClient(STORE)


def fake_get_supabase():
    return FAKE_CLIENT


import app.modules.storeops.router as router_mod   # noqa: E402

router_mod.get_supabase = fake_get_supabase

ORG = "ORG1"
ORG2 = "ORG2"

STORE["employees"] = [
    {"id": 1, "employee_id": "E1", "org_id": ORG, "name": "Alice Rep", "home_store": "Store1", "pay_rate": 20.0, "is_active": True},
    {"id": 2, "employee_id": "E2", "org_id": ORG, "name": "Bob Floater", "home_store": "Store1", "pay_rate": 25.0, "is_active": True},
    {"id": 3, "employee_id": "E4", "org_id": ORG, "name": "Dana Kiosk", "home_store": "Store2", "pay_rate": 22.0, "is_active": True},
    {"id": 4, "employee_id": "E6", "org_id": ORG, "name": "Fay BothSources", "home_store": "Store1", "pay_rate": 21.0, "is_active": True},
    {"id": 5, "employee_id": "E8", "org_id": ORG, "name": "Hana Idle", "home_store": "Store9", "pay_rate": 30.0, "is_active": True},
    # INACTIVE employee with REAL activity this period (act=6>0 on their one shift, id6 below):
    # 2026-07-25 fix — appears in /payroll AND /payroll-by-store paid at their REAL rate (15/hr),
    # not the old $0-in-/payroll (active-only emp_map) behavior — "must still appear and be paid".
    {"id": 6, "employee_id": "E9", "org_id": ORG, "name": "Ivan Old", "home_store": "Store2", "pay_rate": 15.0, "is_active": False},
    {"id": 7, "employee_id": "E10", "org_id": ORG, "name": "Zed Same", "home_store": "Store1", "pay_rate": 10.0, "is_active": True},
    {"id": 8, "employee_id": "E11", "org_id": ORG, "name": "Zed Same", "home_store": "Store1", "pay_rate": 11.0, "is_active": True},
    {"id": 9, "employee_id": "E12", "org_id": ORG, "name": "Nia Deleted", "home_store": "Store1", "pay_rate": 12.0, "is_active": True},
    {"id": 10, "employee_id": "X1", "org_id": ORG2, "name": "Other Org", "home_store": "OStore", "pay_rate": 9.0, "is_active": True},
    # INACTIVE employee with ONLY a schedule-only PHANTOM shift (id13 below, actual_hours=0, never
    # actually worked, left over from before they were deactivated) — 2026-07-25 fix: must NOT
    # appear anywhere, at all, in EITHER endpoint (the money-adjacent "phantom rows drop" half of
    # the rule) — see checks 27-29.
    {"id": 11, "employee_id": "E13", "org_id": ORG, "name": "Zara Ghost", "home_store": "Store1", "pay_rate": 18.0, "is_active": False},
    # Gate-1 MAJOR-B1 (2026-07-26): INACTIVE employee whose REAL shift (act>0) has a SAME-DAY closed
    # punch too (id16/t11 below) — must resolve EXACTLY like the active path's own no-double-count
    # rule (E6's identical sched8/act7.5/punch8h pattern, just a different store/date), not sum both.
    {"id": 12, "employee_id": "E16", "org_id": ORG, "name": "Uma BothInactive", "home_store": "Store1", "pay_rate": 20.0, "is_active": False},
    # Gate-1 LOW-B2 (2026-07-26): is_active is NULLABLE — a NULL/missing flag must be treated as
    # ACTIVE (never folded into the inactive/phantom-filtering path), matching every frontend
    # picker's `!== false` convention. This employee's ONLY shift is phantom (actual_hours=0); if
    # wrongly treated as inactive it would vanish entirely — instead it must go through the ordinary
    # ACTIVE act==0->scheduled fallback and show real scheduled-based pay.
    {"id": 13, "employee_id": "E17", "org_id": ORG, "name": "Vik NullFlag", "home_store": "Store1", "pay_rate": 25.0, "is_active": None},
]
STORE["shifts"] = [   # list order == id order (bigserial insert order, what PostgREST returns)
    {"id": 1, "org_id": ORG, "employee_id": "E1", "employee_name": "Alice Rep", "store_code": "Store1",
     "shift_date": "2026-07-05", "scheduled_hours": 8, "actual_hours": 0, "is_deleted": False},
    {"id": 2, "org_id": ORG, "employee_id": "E2", "employee_name": "Bob Floater", "store_code": "Store1",
     "shift_date": "2026-07-06", "scheduled_hours": 4, "actual_hours": 4, "is_deleted": False},
    {"id": 3, "org_id": ORG, "employee_id": "E2", "employee_name": "Bob Floater", "store_code": "Store2",
     "shift_date": "2026-07-07", "scheduled_hours": 4, "actual_hours": 4, "is_deleted": False},
    # NULL employee_id, real store: must survive with its own row (name from the shift row)
    {"id": 4, "org_id": ORG, "employee_id": None, "employee_name": "Ghost Temp", "store_code": "Store3",
     "shift_date": "2026-07-08", "scheduled_hours": 5, "actual_hours": 0, "is_deleted": False},
    {"id": 5, "org_id": ORG, "employee_id": "E6", "employee_name": "Fay BothSources", "store_code": "Store1",
     "shift_date": "2026-07-10", "scheduled_hours": 8, "actual_hours": 7.5, "is_deleted": False},
    {"id": 6, "org_id": ORG, "employee_id": "E9", "employee_name": "Ivan Old", "store_code": "Store2",
     "shift_date": "2026-07-11", "scheduled_hours": 6, "actual_hours": 6, "is_deleted": False},
    # sched=0 / act>0 row (no fallback fires; sums into E1)
    {"id": 7, "org_id": ORG, "employee_id": "E1", "employee_name": "Alice Rep", "store_code": "Store1",
     "shift_date": "2026-07-12", "scheduled_hours": 0, "actual_hours": 3, "is_deleted": False},
    # BLANK-store shift: excluded from by-store hours but still BLOCKS E2's 07-13 punch
    {"id": 8, "org_id": ORG, "employee_id": "E2", "employee_name": "Bob Floater", "store_code": "",
     "shift_date": "2026-07-13", "scheduled_hours": 2, "actual_hours": 0, "is_deleted": False},
    # soft-DELETED shift: ignored everywhere and does NOT block same-day punches
    {"id": 9, "org_id": ORG, "employee_id": "E12", "employee_name": "Nia Deleted", "store_code": "Store1",
     "shift_date": "2026-07-18", "scheduled_hours": 5, "actual_hours": 5, "is_deleted": True},
    {"id": 10, "org_id": ORG, "employee_id": "E10", "employee_name": "Zed Same", "store_code": "Store1",
     "shift_date": "2026-07-15", "scheduled_hours": 4, "actual_hours": 4, "is_deleted": False},
    {"id": 11, "org_id": ORG, "employee_id": "E11", "employee_name": "Zed Same", "store_code": "Store2",
     "shift_date": "2026-07-16", "scheduled_hours": 4, "actual_hours": 4, "is_deleted": False},
    {"id": 12, "org_id": ORG2, "employee_id": "X1", "employee_name": "Other Org", "store_code": "OStore",
     "shift_date": "2026-07-05", "scheduled_hours": 8, "actual_hours": 0, "is_deleted": False},
    # PHANTOM: INACTIVE E13's only shift this month, never worked (actual_hours=0) — 07-20 is
    # deliberately OUTSIDE the 07-10..07-16 multi-week sub-range so it never touches those checks.
    {"id": 13, "org_id": ORG, "employee_id": "E13", "employee_name": "Zara Ghost", "store_code": "Store1",
     "shift_date": "2026-07-20", "scheduled_hours": 5, "actual_hours": 0, "is_deleted": False},
    # MAJOR-B1 fixture: INACTIVE E16's REAL shift (same numbers as E6's proven sched8/act7.5, but a
    # fresh store/date — Store4/07-19 — so it never perturbs Store1/Store2/Store3's already-asserted
    # totals or the 07-10..07-16 multi-week window) — its same-day punch is below (t11).
    {"id": 16, "org_id": ORG, "employee_id": "E16", "employee_name": "Uma BothInactive", "store_code": "Store4",
     "shift_date": "2026-07-19", "scheduled_hours": 8, "actual_hours": 7.5, "is_deleted": False},
    # LOW-B2 fixture: E17 (NULL is_active) — a schedule-only PHANTOM shift (act=0). If wrongly
    # treated as inactive this would vanish entirely (like E13); correctly treated as ACTIVE it must
    # survive via the ordinary act==0->scheduled fallback. Store5/07-23 — fresh, no interference.
    {"id": 17, "org_id": ORG, "employee_id": "E17", "employee_name": "Vik NullFlag", "store_code": "Store5",
     "shift_date": "2026-07-23", "scheduled_hours": 6, "actual_hours": 0, "is_deleted": False},
]
STORE["timelog"] = [
    # E4 kiosk-only: two closed punches, zero shift rows this month
    {"id": "t1", "org_id": ORG, "employee_id": "E4", "employee_name": "Dana Kiosk", "store_code": "Store2",
     "clock_out": "2026-07-11T17:00:00", "hours": 4.0, "work_date": "2026-07-11", "created_at": "2026-07-11T17:00:01"},
    {"id": "t2", "org_id": ORG, "employee_id": "E4", "employee_name": "Dana Kiosk", "store_code": "Store2",
     "clock_out": "2026-07-12T18:00:00", "hours": 5.0, "work_date": "2026-07-12", "created_at": "2026-07-12T18:00:01"},
    # OPEN punch: never counts
    {"id": "t3", "org_id": ORG, "employee_id": "E4", "employee_name": "Dana Kiosk", "store_code": "Store2",
     "clock_out": None, "hours": None, "work_date": "2026-07-20", "created_at": "2026-07-20T13:00:01"},
    # same-day punch as E6's live shift: no-double-count
    {"id": "t4", "org_id": ORG, "employee_id": "E6", "employee_name": "Fay BothSources", "store_code": "Store1",
     "clock_out": "2026-07-10T21:00:00", "hours": 8.0, "work_date": "2026-07-10", "created_at": "2026-07-10T21:00:01"},
    # blocked by E2's BLANK-store shift on 07-13
    {"id": "t5", "org_id": ORG, "employee_id": "E2", "employee_name": "Bob Floater", "store_code": "Store2",
     "clock_out": "2026-07-13T20:00:00", "hours": 6.0, "work_date": "2026-07-13", "created_at": "2026-07-13T20:00:01"},
    # 0-hour closed punch (counts as a row, adds 0h -> keeps E2's store tie intact)
    {"id": "t6", "org_id": ORG, "employee_id": "E2", "employee_name": "Bob Floater", "store_code": "Store1",
     "clock_out": "2026-07-14T20:00:00", "hours": 0.0, "work_date": "2026-07-14", "created_at": "2026-07-14T20:00:01"},
    # punch on the same day as E12's soft-DELETED shift: deleted shift must NOT block it
    {"id": "t7", "org_id": ORG, "employee_id": "E12", "employee_name": "Nia Deleted", "store_code": "Store1",
     "clock_out": "2026-07-18T19:00:00", "hours": 4.0, "work_date": "2026-07-18", "created_at": "2026-07-18T19:00:01"},
    {"id": "t8", "org_id": ORG2, "employee_id": "X1", "employee_name": "Other Org", "store_code": "OStore",
     "clock_out": "2026-07-06T19:00:00", "hours": 3.0, "work_date": "2026-07-06", "created_at": "2026-07-06T19:00:01"},
    # MAJOR-B1: E16 (INACTIVE)'s SAME-DAY punch as their real shift (id16, 07-19) — must be BLOCKED
    # by that surviving real shift (mirrors E6/t4's active-path no-double-count), never summed on top.
    {"id": "t11", "org_id": ORG, "employee_id": "E16", "employee_name": "Uma BothInactive", "store_code": "Store4",
     "clock_out": "2026-07-19T21:00:00", "hours": 8.0, "work_date": "2026-07-19", "created_at": "2026-07-19T21:00:01"},
]


def run_both(month, org):
    """(payroll_json, by_store_json, rpc_calls) for LEGACY then FAST, resetting counters."""
    outs = {}
    for mode in ("legacy", "fast"):
        FAKE_CLIENT.rpc_enabled = (mode == "fast")
        FAKE_CLIENT.rpc_calls = 0
        p = router_mod.get_payroll(month=month, org_id=org)
        b = router_mod.get_payroll_by_store(month=month, org_id=org)
        outs[mode] = (json.dumps(p), json.dumps(b), FAKE_CLIENT.rpc_calls)
    FAKE_CLIENT.rpc_enabled = False
    return outs


def run_both_kw(org, **kwargs):
    """Same as run_both() but accepts arbitrary get_payroll/get_payroll_by_store kwargs (start=/end=
    instead of month=) — used by the 2026-07-25 range-vs-month differential + multi-week checks."""
    outs = {}
    for mode in ("legacy", "fast"):
        FAKE_CLIENT.rpc_enabled = (mode == "fast")
        FAKE_CLIENT.rpc_calls = 0
        p = router_mod.get_payroll(org_id=org, **kwargs)
        b = router_mod.get_payroll_by_store(org_id=org, **kwargs)
        outs[mode] = (json.dumps(p), json.dumps(b), FAKE_CLIENT.rpc_calls)
    FAKE_CLIENT.rpc_enabled = False
    return outs


# ══ 1-2: full-fixture byte-identical equivalence ═════════════════════════════════════════════════
outs = run_both("2026-07", ORG)
check("1: /payroll byte-identical legacy vs RPC fast path",
      outs["legacy"][0] == outs["fast"][0],
      f"\nLEGACY: {outs['legacy'][0]}\nFAST:   {outs['fast'][0]}")
check("2: /payroll-by-store byte-identical legacy vs RPC fast path",
      outs["legacy"][1] == outs["fast"][1],
      f"\nLEGACY: {outs['legacy'][1]}\nFAST:   {outs['fast'][1]}")
check("3: fast run actually used the RPC (2 calls) and legacy run used none",
      outs["fast"][2] == 2 and outs["legacy"][2] == 0, (outs["legacy"][2], outs["fast"][2]))

# ══ 4-12: semantic spot checks on the FAST output (guards against both paths being wrong) ════════
FAKE_CLIENT.rpc_enabled = True
pay = {r["employee_id"]: r for r in router_mod.get_payroll(month="2026-07", org_id=ORG)}
bys = {r["store_code"]: r for r in router_mod.get_payroll_by_store(month="2026-07", org_id=ORG)["stores"]}
FAKE_CLIENT.rpc_enabled = False

check("4: E1 row-level act==0->sched fallback (8) + sched0/act3 row => sched 8 / act 11",
      pay["E1"]["scheduled_hours"] == 8 and pay["E1"]["actual_hours"] == 11 and pay["E1"]["shifts"] == 2,
      pay.get("E1"))
check("5: E2 PUNCH-DRIVEN 07-13 — the blank-store scheduled shift (sched2/act0) no longer suppresses "
      "its 6h punch; the punch drives pay (shift contributes 0, punch counts 6h at Store2), so "
      "actual=4+4+0+6+0=14 (sched still 10, 3 shifts). Store2 now dominant (14>8) so store=Store2",
      pay["E2"]["store"] == "Store2" and pay["E2"]["scheduled_hours"] == 10
      and pay["E2"]["actual_hours"] == 14 and pay["E2"]["shifts"] == 3, pay.get("E2"))
check("6: E4 kiosk-only appears: act 9 / sched 0 / 0 shifts, store Store2, name from timelog",
      pay["E4"]["actual_hours"] == 9 and pay["E4"]["scheduled_hours"] == 0 and pay["E4"]["shifts"] == 0
      and pay["E4"]["store"] == "Store2" and pay["E4"]["name"] == "Dana Kiosk", pay.get("E4"))
check("7: E6 same-day punch NOT double-counted (act stays 7.5)",
      pay["E6"]["actual_hours"] == 7.5 and pay["E6"]["scheduled_hours"] == 8, pay.get("E6"))
check("8: NULL-employee_id shift survives: own bucket, name 'Ghost Temp', store Store3, rate 0",
      pay.get(None, {}).get("name") == "Ghost Temp" and pay.get(None, {}).get("store") == "Store3"
      and pay.get(None, {}).get("scheduled_hours") == 5 and pay.get(None, {}).get("actual_hours") == 5
      and pay.get(None, {}).get("pay_rate") == 0.0, pay.get(None))
check("9: INACTIVE E9 (REAL activity, act=6>0) appears in /payroll AND /payroll-by-store paid at "
      "their REAL 15/hr rate (2026-07-25 money-adjacent fix: 'must still appear and be paid', "
      "reversing the old $0-in-/payroll behavior for an inactive employee with genuine worked hours)",
      pay["E9"]["pay_rate"] == 15.0 and pay["E9"]["actual_pay"] == 90.0
      # Store2 now also carries E2's punch-driven 07-13 punch (6h*$25=$150): was 23h/$432, now 29h/$582.
      and bys["Store2"]["hours"] == 29 and bys["Store2"]["amount"] == 582.0,
      (pay.get("E9"), bys.get("Store2")))
check("10: E12's punch on the soft-DELETED shift's day COUNTS (deleted shift blocks nothing)",
      pay["E12"]["actual_hours"] == 4 and pay["E12"]["shifts"] == 0 and pay["E12"]["scheduled_hours"] == 0,
      pay.get("E12"))
check("11: by-store totals: Store1 30.5h/$565.50, Store3 (NULL-eid) 5h/$0, blank store absent",
      bys["Store1"]["hours"] == 30.5 and bys["Store1"]["amount"] == 565.5
      and bys["Store3"]["hours"] == 5 and bys["Store3"]["amount"] == 0.0 and "" not in bys,
      bys)
FAKE_CLIENT.rpc_enabled = True
zed_fast = [r["employee_id"] for r in router_mod.get_payroll(month="2026-07", org_id=ORG)
            if r["name"] == "Zed Same"]
FAKE_CLIENT.rpc_enabled = False
zed_legacy = [r["employee_id"] for r in router_mod.get_payroll(month="2026-07", org_id=ORG)
              if r["name"] == "Zed Same"]
check("12: same-name employees keep stable insertion order on BOTH paths (E10 id10 before E11 id11)",
      zed_fast == ["E10", "E11"] and zed_legacy == ["E10", "E11"], (zed_fast, zed_legacy))

# ══ 13: keyset filtering identical on both paths ═════════════════════════════════════════════════
_real_scope_keyset = router_mod.scope_keyset


def _fake_scope_keyset(authorization, org_id=ORG):
    return {"STORE1"}


router_mod.scope_keyset = _fake_scope_keyset
try:
    kouts = run_both("2026-07", ORG)
    kpay = json.loads(kouts["fast"][0])
    kbys = json.loads(kouts["fast"][1])["stores"]
finally:
    router_mod.scope_keyset = _real_scope_keyset
check("13: keyset {STORE1}: byte-identical on both paths AND actually filters (4 emp rows, 1 store). "
      "Was 5 pre-PUNCH-DRIVEN: E2's dominant store flipped to Store2 (it genuinely worked more hours "
      "there once its 07-13 punch counts), so E2's single row now labels Store2 and drops from {STORE1}",
      kouts["legacy"][0] == kouts["fast"][0] and kouts["legacy"][1] == kouts["fast"][1]
      and len(kpay) == 4 and [s["store_code"] for s in kbys] == ["Store1"],
      (kouts["legacy"][0] == kouts["fast"][0], kouts["legacy"][1] == kouts["fast"][1],
       len(kpay), [s["store_code"] for s in kbys]))

# ══ 14: empty month — fast path engaged, zero groups, byte-identical empties ═════════════════════
eouts = run_both("2026-01", ORG)
check("14: empty month byte-identical + RPC still exercised + returns empties",
      eouts["legacy"][0] == eouts["fast"][0] == "[]"
      and eouts["legacy"][1] == eouts["fast"][1]
      and json.loads(eouts["fast"][1])["stores"] == [] and eouts["fast"][2] == 2,
      eouts)

# ══ 15: month=None — RPC must NOT engage (open range), output identical to legacy ════════════════
nouts = run_both(None, ORG)
check("15: month=None never calls the RPC and stays byte-identical to legacy",
      nouts["fast"][2] == 0 and nouts["legacy"][0] == nouts["fast"][0]
      and nouts["legacy"][1] == nouts["fast"][1], nouts)

# ══ 16-17: org isolation ═════════════════════════════════════════════════════════════════════════
oouts = run_both("2026-07", ORG2)
opay = json.loads(oouts["fast"][0])
obys = json.loads(oouts["fast"][1])["stores"]
check("16: ORG2 byte-identical on both paths",
      oouts["legacy"][0] == oouts["fast"][0] and oouts["legacy"][1] == oouts["fast"][1],
      oouts)
check("17: ORG2 sees ONLY its own data (X1/OStore: 8+3=11h), no ORG1 leakage either way",
      [r["employee_id"] for r in opay] == ["X1"] and opay[0]["actual_hours"] == 11
      and [s["store_code"] for s in obys] == ["OStore"]
      and "X1" not in {r["employee_id"] for r in json.loads(outs["fast"][0])},
      (opay, obys))

# ══ 18-19: RANGE-VS-MONTH DIFFERENTIAL (2026-07-25, owner: arbitrary time-range payroll) ═══════════
# A start/end range set to EXACTLY a calendar month must be byte-identical to the legacy `month=`
# path, on BOTH the fast RPC path and the legacy Python path — proves _resolve_range()'s date math
# reproduces month math exactly, and that start/end reuse the identical aggregation as month= (no
# parallel/divergent code path was introduced).
month_outs = run_both("2026-07", ORG)
range_outs = run_both_kw(ORG, start="2026-07-01", end="2026-07-31")
# /payroll (index 0) is byte-identical outright. /payroll-by-store (index 1) echoes its OWN `month`
# input verbatim (None for a start/end call vs "2026-07" for a month= call — a legitimate, expected
# difference in what was actually passed in, not a data divergence), so compare the `stores` payload
# specifically — the numbers a range-mode caller and a month-mode caller actually see must match.
def _stores(js):
    return json.loads(js)["stores"]
check("18: start=07-01/end=07-31 byte-identical /payroll, same `stores` payload on /payroll-by-store, "
      "vs month=2026-07 (FAST path)",
      range_outs["fast"][0] == month_outs["fast"][0] and _stores(range_outs["fast"][1]) == _stores(month_outs["fast"][1]),
      (range_outs["fast"], month_outs["fast"]))
check("19: same range-vs-month equivalence on the LEGACY path too",
      range_outs["legacy"][0] == month_outs["legacy"][0] and _stores(range_outs["legacy"][1]) == _stores(month_outs["legacy"][1]),
      (range_outs["legacy"], month_outs["legacy"]))

# ══ 20-21: malformed range rejected with 400 (never silently misinterpreted) ═════════════════════
try:
    router_mod.get_payroll(start="2026-07-10", end="2026-07-01", org_id=ORG)
    check("20: start>end raises HTTPException(400)", False, "no exception raised")
except HTTPException as e:
    check("20: start>end raises HTTPException(400)", e.status_code == 400, e.detail)
try:
    router_mod.get_payroll(start="2026-07-10", org_id=ORG)   # end missing -> half a range, must reject
    check("21: start without end raises HTTPException(400)", False, "no exception raised")
except HTTPException as e:
    check("21: start without end raises HTTPException(400)", e.status_code == 400, e.detail)

# ══ 22-25: MULTI-WEEK RANGE SANITY (2026-07-10..2026-07-16 — NOT a month, NOT a week-aligned month
# boundary) — hand-computed against the exact fixture above. Narrower than the full month: excludes
# Ghost Temp's 07-08 NULL-employee shift and E12's 07-18 post-soft-delete punch, both outside the window.
wk_outs = run_both_kw(ORG, start="2026-07-10", end="2026-07-16")
check("22: multi-week range byte-identical FAST vs LEGACY (arbitrary non-month bound honored by BOTH paths)",
      wk_outs["fast"][0] == wk_outs["legacy"][0] and wk_outs["fast"][1] == wk_outs["legacy"][1], wk_outs)
wk_pay = {r["employee_id"]: r for r in router_mod.get_payroll(start="2026-07-10", end="2026-07-16", org_id=ORG)}
wk_bys = {r["store_code"]: r for r in router_mod.get_payroll_by_store(start="2026-07-10", end="2026-07-16", org_id=ORG)["stores"]}
check("23: multi-week /payroll narrows to exactly 7 employees (vs 9 for the full month) — Ghost Temp "
      "(07-08) and E12 (07-18) correctly fall outside the window",
      set(wk_pay.keys()) == {"E1", "E2", "E4", "E6", "E9", "E10", "E11"}, sorted([str(k) for k in wk_pay.keys()]))
check("24: multi-week per-employee hand-computed hours/store (E1 sched0/act3, E6 sched8/act7.5 Store1, "
      "E9 sched6/act6 Store2 REAL 15/hr rate (2026-07-25 fix — inactive but genuinely worked), "
      "E4 kiosk-only act9 Store2)",
      wk_pay["E1"]["scheduled_hours"] == 0 and wk_pay["E1"]["actual_hours"] == 3
      and wk_pay["E6"]["scheduled_hours"] == 8 and wk_pay["E6"]["actual_hours"] == 7.5 and wk_pay["E6"]["store"] == "Store1"
      and wk_pay["E9"]["actual_hours"] == 6 and wk_pay["E9"]["store"] == "Store2" and wk_pay["E9"]["pay_rate"] == 15.0
      and wk_pay["E4"]["actual_hours"] == 9 and wk_pay["E4"]["store"] == "Store2",
      wk_pay)
check("25: multi-week /payroll-by-store hand-computed (Store1 14.5h/$257.50 unchanged; Store2 now "
      "25h/$482.00 — E2's blank-store shift on 07-13 still contributes to NEITHER store, but under "
      "PUNCH-DRIVEN PAY its 6h punch is no longer suppressed and now lands at Store2: 19+6=25h)",
      wk_bys["Store1"]["hours"] == 14.5 and wk_bys["Store1"]["amount"] == 257.5
      and wk_bys["Store2"]["hours"] == 25 and wk_bys["Store2"]["amount"] == 482.0,
      wk_bys)

# ══ 26: org isolation holds in range mode too (the required multi-tenant proof for a new param) ═══
wk_outs2 = run_both_kw(ORG2, start="2026-07-01", end="2026-07-10")
wk_pay2 = json.loads(wk_outs2["fast"][0])
check("26: range-mode org isolation — ORG2's range call sees only its own X1/OStore row (8+3=11h), "
      "no ORG1 leak, byte-identical fast vs legacy",
      [r["employee_id"] for r in wk_pay2] == ["X1"] and wk_pay2[0]["actual_hours"] == 11
      and wk_outs2["fast"][0] == wk_outs2["legacy"][0], wk_pay2)

# ══ 27-29: PHANTOM-SCHEDULE MONEY DIFFERENTIAL (2026-07-25 owner fix) ═══════════════════════════
# E13 (inactive, id 11) has ONLY a schedule-only phantom shift (id 13, actual_hours=0, 07-20) —
# never actually worked, left over from before deactivation. Money-adjacent rule: this must drop
# EVERYWHERE, on both endpoints, on both fast and legacy paths — proven explicitly here (rather than
# only implicitly via checks 1/2/11's unchanged totals).
full_outs = run_both("2026-07", ORG)
full_pay_fast = json.loads(full_outs["fast"][0])
full_bys_fast = json.loads(full_outs["fast"][1])["stores"]
check("27: phantom-only INACTIVE E13 (schedule-only, never worked) does NOT appear in /payroll at "
      "all, on EITHER path", "E13" not in {r["employee_id"] for r in full_pay_fast}
      and "E13" not in {r["employee_id"] for r in json.loads(full_outs["legacy"][0])},
      [r["employee_id"] for r in full_pay_fast])
store1_fast = next(s for s in full_bys_fast if s["store_code"] == "Store1")
check("28: E13's phantom 5h never lands on Store1's /payroll-by-store total — UNCHANGED at "
      "30.5h/$565.50 (identical to check 11's pre-E13 numbers) despite E13 now being in the fixture, "
      "on EITHER path", store1_fast["hours"] == 30.5 and store1_fast["amount"] == 565.5
      and full_outs["fast"][1] == full_outs["legacy"][1], store1_fast)
check("29: the whole full-month output (incl. the phantom-employee fixture) is STILL byte-identical "
      "fast vs legacy — the money differential is proven, not just asserted",
      full_outs["fast"][0] == full_outs["legacy"][0], full_outs)

# ══ 30: a tenant with ZERO inactive employees is COMPLETELY untouched by this fix — byte-identical ══
# ORG2 has only X1 (is_active=True) — _inactive_ids_from(...) is empty, so _inactive_activity_rows
# short-circuits to ([], []) without even querying, and neither endpoint's aggregation loop skips
# anything. Locks in the exact expected JSON so a future regression here is caught immediately.
o2_outs = run_both("2026-07", ORG2)
check("30: ORG2 (no inactive employees at all) full-month /payroll output is the EXACT expected "
      "JSON — completely unaffected by the phantom-schedule fix",
      o2_outs["fast"][0] == o2_outs["legacy"][0]
      == '[{"employee_id": "X1", "name": "Other Org", "store": "OStore", "pay_rate": 9.0, '
         '"scheduled_hours": 8.0, "actual_hours": 11.0, "shifts": 1, "scheduled_pay": 72.0, "actual_pay": 99.0}]',
      o2_outs["fast"][0])

# ══ 31-32: Gate-1 MAJOR-B1 — INACTIVE employee, REAL shift + SAME-DAY closed punch must NOT
# double-count, and must match the ACTIVE path's own treatment of the IDENTICAL shift/punch shape
# (E6: sched8/act7.5/punch8h same day -> actual stays 7.5, shift wins, punch blocked) ══════════════
b1_outs = run_both("2026-07", ORG)
b1_pay = {r["employee_id"]: r for r in json.loads(b1_outs["fast"][0])}
b1_pay_legacy = {r["employee_id"]: r for r in json.loads(b1_outs["legacy"][0])}
b1_bys = {s["store_code"]: s for s in json.loads(b1_outs["fast"][1])["stores"]}
b1_bys_legacy = {s["store_code"]: s for s in json.loads(b1_outs["legacy"][1])["stores"]}
check("31: INACTIVE E16's real shift + same-day punch does NOT double-count — actual_hours matches "
      "the ACTIVE E6's IDENTICAL sched8/act7.5/punch8h pattern EXACTLY (7.5, not 15.5), on BOTH "
      "fast and legacy paths (byte-identical to each other too)",
      b1_pay["E16"]["actual_hours"] == b1_pay["E6"]["actual_hours"] == 7.5
      and b1_pay["E16"]["scheduled_hours"] == b1_pay["E6"]["scheduled_hours"] == 8
      and b1_pay["E16"]["shifts"] == 1 and b1_pay["E16"]["pay_rate"] == 20.0
      and b1_pay["E16"] == b1_pay_legacy["E16"],
      (b1_pay.get("E16"), b1_pay.get("E6"), b1_pay_legacy.get("E16")))
check("32: by-store totals reflect the NON-double-counted number too — Store4 (E16's only store) is "
      "7.5h/$150.00 (7.5*20), not 15.5h/$310.00, on BOTH paths",
      b1_bys["Store4"]["hours"] == 7.5 and b1_bys["Store4"]["amount"] == 150.0
      and b1_bys["Store4"] == b1_bys_legacy["Store4"],
      (b1_bys.get("Store4"), b1_bys_legacy.get("Store4")))

# ══ 33-34: Gate-1 LOW-B2 — is_active NULL/missing must be treated as ACTIVE, never folded into the
# inactive/phantom-filtering path (E17's phantom shift must survive via the ORDINARY active-path
# act==0->scheduled fallback, not vanish like a truly-inactive phantom would) ═══════════════════════
check("33: NULL-is_active E17 is treated as ACTIVE — survives via the ordinary act==0->scheduled "
      "fallback (sched6/act6, NOT vanished like a truly-inactive phantom), real rate 25/hr, on BOTH paths",
      b1_pay["E17"]["scheduled_hours"] == 6 and b1_pay["E17"]["actual_hours"] == 6
      and b1_pay["E17"]["pay_rate"] == 25.0 and b1_pay["E17"] == b1_pay_legacy["E17"],
      (b1_pay.get("E17"), b1_pay_legacy.get("E17")))
check("34: E17's by-store total (Store5) reflects the fallback-based hours too — 6h/$150.00, both paths",
      b1_bys["Store5"]["hours"] == 6 and b1_bys["Store5"]["amount"] == 150.0
      and b1_bys["Store5"] == b1_bys_legacy["Store5"],
      (b1_bys.get("Store5"), b1_bys_legacy.get("Store5")))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
