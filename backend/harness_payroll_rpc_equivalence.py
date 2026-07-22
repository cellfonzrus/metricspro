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
"""
import json
import sys
from datetime import datetime

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
    """Python mirror of storeops.payroll_month_rows() (migration 407) — the SQL's exact
    filters/grouping/expressions, so the FAST path is fed what Postgres would return."""
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
            act_eff += sched if act == 0 else act      # /payroll basis
            hrs_eff += act if act > 0 else sched       # /payroll-by-store basis
        out.append({"kind": "shift", "employee_id": eid, "store_code": st,
                    "employee_name": by_id[0].get("employee_name"),
                    "first_ord": float(by_id[0]["id"]),
                    "scheduled_sum": sched_sum, "actual_eff_sum": act_eff,
                    "hours_eff_sum": hrs_eff, "shift_count": len(rows_),
                    "timelog_hours_sum": 0.0})
    # ── timelog branch ──────────────────────────────────────────────────────────────────────────
    live_shift_days = {(s.get("employee_id"), str(s.get("shift_date") or ""))
                       for s in store.get("shifts", [])
                       if s.get("org_id") == p_org_id and s.get("is_deleted") is False
                       and s.get("employee_id") is not None}
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
        if (eid, wd) in live_shift_days:
            continue                                   # NOT EXISTS live shift same emp/day
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
    # INACTIVE employee: excluded from /payroll's active-only emp_map (rate shows 0 there) but
    # present in /payroll-by-store's all-employees rate_map (terminated rep who worked still earns).
    {"id": 6, "employee_id": "E9", "org_id": ORG, "name": "Ivan Old", "home_store": "Store2", "pay_rate": 15.0, "is_active": False},
    {"id": 7, "employee_id": "E10", "org_id": ORG, "name": "Zed Same", "home_store": "Store1", "pay_rate": 10.0, "is_active": True},
    {"id": 8, "employee_id": "E11", "org_id": ORG, "name": "Zed Same", "home_store": "Store1", "pay_rate": 11.0, "is_active": True},
    {"id": 9, "employee_id": "E12", "org_id": ORG, "name": "Nia Deleted", "home_store": "Store1", "pay_rate": 12.0, "is_active": True},
    {"id": 10, "employee_id": "X1", "org_id": ORG2, "name": "Other Org", "home_store": "OStore", "pay_rate": 9.0, "is_active": True},
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
check("5: E2 dominant-store TIE (Store1 8 vs Store2 8) -> first-seen Store1; blocked 07-13 punch "
      "excluded; blank-store shift hours still count (sched 10 / act 10, 3 shifts)",
      pay["E2"]["store"] == "Store1" and pay["E2"]["scheduled_hours"] == 10
      and pay["E2"]["actual_hours"] == 10 and pay["E2"]["shifts"] == 3, pay.get("E2"))
check("6: E4 kiosk-only appears: act 9 / sched 0 / 0 shifts, store Store2, name from timelog",
      pay["E4"]["actual_hours"] == 9 and pay["E4"]["scheduled_hours"] == 0 and pay["E4"]["shifts"] == 0
      and pay["E4"]["store"] == "Store2" and pay["E4"]["name"] == "Dana Kiosk", pay.get("E4"))
check("7: E6 same-day punch NOT double-counted (act stays 7.5)",
      pay["E6"]["actual_hours"] == 7.5 and pay["E6"]["scheduled_hours"] == 8, pay.get("E6"))
check("8: NULL-employee_id shift survives: own bucket, name 'Ghost Temp', store Store3, rate 0",
      pay.get(None, {}).get("name") == "Ghost Temp" and pay.get(None, {}).get("store") == "Store3"
      and pay.get(None, {}).get("scheduled_hours") == 5 and pay.get(None, {}).get("actual_hours") == 5
      and pay.get(None, {}).get("pay_rate") == 0.0, pay.get(None))
check("9: INACTIVE E9 appears in /payroll with rate 0 (active-only emp_map) but by-store pays "
      "their real 15/hr (6h*15=90 inside Store2)",
      pay["E9"]["pay_rate"] == 0.0 and pay["E9"]["actual_pay"] == 0.0
      and bys["Store2"]["hours"] == 23 and bys["Store2"]["amount"] == 432.0,
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
check("13: keyset {STORE1}: byte-identical on both paths AND actually filters (5 emp rows, 1 store)",
      kouts["legacy"][0] == kouts["fast"][0] and kouts["legacy"][1] == kouts["fast"][1]
      and len(kpay) == 5 and [s["store_code"] for s in kbys] == ["Store1"],
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

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL GREEN")
