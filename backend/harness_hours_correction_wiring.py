"""INTEGRATION half of `harness_hours_correction_reaches_figure.py` (§D).

Runs the REAL shipped handlers out of `app.modules.storeops.router` against an in-memory Supabase
client — the same fake-client pattern `harness_payroll_rpc_equivalence.py` established — and asserts
that a correction a human typed REACHES the figure on every reader, for every tenant, with no
leakage between them.

Exposes `run() -> [(name, cond, detail), ...]` so the parent harness reports one tally. Importable
and runnable on its own.
"""
import sys
from datetime import datetime

sys.path.insert(0, ".")

HOUSE = "00000000-0000-0000-0000-000000000001"
NY = "ba084e25-adcf-4d4d-9cc5-d3728775513f"
LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"


# ── in-memory Supabase ────────────────────────────────────────────────────────────────────────────
class FakeQuery:
    def __init__(self, store, table):
        self.store, self.table_name, self.filters, self._limit = store, table, [], None

    def select(self, cols=None):
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def neq(self, k, v):
        self.filters.append(("neq", k, v)); return self

    def in_(self, k, vals):
        self.filters.append(("in", k, {str(x) for x in vals})); return self

    def gte(self, k, v):
        self.filters.append(("gte", k, v)); return self

    def lte(self, k, v):
        self.filters.append(("lte", k, v)); return self

    def lt(self, k, v):
        self.filters.append(("lt", k, v)); return self

    def is_(self, k, v):
        self.filters.append(("is", k, v)); return self

    def order(self, k, desc=False):
        return self

    def limit(self, n):
        self._limit = n; return self

    def range(self, a, b):
        return self

    def _match(self, row):
        for op, k, v in self.filters:
            rv = row.get(k)
            if op == "eq" and rv != v:
                return False
            if op == "neq" and rv == v:
                return False
            if op == "in" and str(rv) not in v:
                return False
            if op == "gte" and not (rv is not None and str(rv) >= str(v)):
                return False
            if op == "lte" and not (rv is not None and str(rv) <= str(v)):
                return False
            if op == "lt" and not (rv is not None and str(rv) < str(v)):
                return False
            if op == "is" and v == "null" and rv is not None:
                return False
        return True

    def execute(self):
        rows = [dict(r) for r in self.store.get(self.table_name, []) if self._match(r)]
        if self._limit:
            rows = rows[: self._limit]
        return FakeResult(rows)


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeRpc:
    def __init__(self, fn, params):
        self.fn = fn

    def execute(self):
        # The mig-914 RPC is RETIRED from the pay path: it decided `correction > punch > schedule`
        # in SQL, where it can read neither `manual_hours` nor the org's declaration. A handler that
        # still reaches for it is the second derivation §E1 fails the build over, so make it loud
        # here too rather than letting it answer.
        raise AssertionError(
            f"the pay path called the {self.fn} RPC — a second, declaration-blind derivation")


class FakeClient:
    def __init__(self, store):
        self.store = store

    def schema(self, name):
        return self

    def table(self, name):
        return FakeQuery(self.store, name)

    def rpc(self, fn, params):
        return FakeRpc(fn, params)


STORE = {}
CLIENT = FakeClient(STORE)

import app.modules.storeops.router as R           # noqa: E402
from app.modules.storeops import payroll_hours_basis as HB   # noqa: E402

R.get_supabase = lambda: CLIENT


def _punch(org, eid, day, hours, store_code="7710", name=None):
    return {"org_id": org, "employee_id": eid, "employee_name": name or eid, "store_code": store_code,
            "work_date": day, "hours": hours, "auto_clocked_out": False,
            "clock_in": f"{day}T09:00:00+00:00", "clock_out": f"{day}T18:00:00+00:00",
            "created_at": f"{day}T09:00:01+00:00", "id": f"p-{org[:4]}-{eid}-{day}"}


def _emp(org, eid, rate, store_code="7710", name=None):
    return {"id": f"e-{org[:4]}-{eid}", "org_id": org, "employee_id": eid, "name": name or eid,
            "pay_rate": rate, "home_store": store_code, "is_active": True}


def _manual(org, eid, day, hours, reason="didnt clock out"):
    return {"id": f"m-{org[:4]}-{eid}-{day}", "org_id": org, "employee_id": eid,
            "work_date": day, "hours": hours, "reason": reason}


def _cfg(org, mode="schedule_fallback", manual=True, floor=None, corrections_floor=None):
    return {"org_id": org, "hours_basis_mode": mode, "pay_from_schedule_default": True,
            "count_manual_hours": manual, "clocked_basis_effective_from": floor,
            "corrections_effective_from": corrections_floor}


def _row(rows, eid):
    for r in rows or []:
        if r.get("employee_id") == eid:
            return r
    return None


def run():
    out = []

    def check(name, cond, detail=""):
        out.append((name, bool(cond), str(detail)))

    LO, HI = "2026-11-01", "2026-11-15"
    DAY, PRE = "2026-11-05", "2026-10-28"

    STORE.clear()
    STORE["employees"] = [
        _emp(NY, "E276", 17.0, name="Zohaib Ali"),
        _emp(NY, "E273", 17.0, name="Mohammad Ansari"),
        _emp(LUX, "E198", 20.0, "9001", name="Lux Rep"),
        _emp(HOUSE, "E012", 19.0, "B-1115", name="House Rep"),
    ]
    STORE["shifts"] = []
    STORE["timelog"] = [
        # NY's real shape: punches, and NOT ONE shift row in the period.
        _punch(NY, "E276", DAY, 9.00, name="Zohaib Ali"),
        _punch(NY, "E273", DAY, 8.00, name="Mohammad Ansari"),
        _punch(NY, "E276", PRE, 9.00, name="Zohaib Ali"),
        _punch(LUX, "E198", DAY, 9.00, "9001", name="Lux Rep"),
        _punch(HOUSE, "E012", DAY, 9.00, "B-1115", name="House Rep"),
    ]
    STORE["manual_hours"] = [
        _manual(NY, "E276", DAY, -2.50),                      # the reported act
        _manual(LUX, "E198", DAY, -3.00),
        _manual(HOUSE, "E012", DAY, -1.00),
        _manual(NY, "E276", PRE, -4.00),                      # outside [LO,HI): must never be read
    ]
    STORE["payroll_hours_basis_config"] = [
        _cfg(NY), _cfg(LUX), _cfg(HOUSE, mode="clocked_only", floor="2026-10-04"),
    ]
    HB.invalidate_config()

    # ── D1 — THE REPORTED DEFECT, on the reported tenant ────────────────────────────────────────
    ny = R.get_payroll(month=None, start=LO, end=HI, authorization="", org_id=NY, response=None)
    r = _row(ny, "E276")
    check("D1 GET /storeops/payroll reflects the correction (9.00 - 2.50 = 6.50)",
          r is not None and r.get("actual_hours") == 6.50,
          f"got {None if r is None else r.get('actual_hours')} — the figure still ignores the "
          f"correction, which is the reported defect")

    # An employee with no correction is UNTOUCHED — the fix moves only what was corrected.
    r2 = _row(ny, "E273")
    check("D1 an uncorrected employee's figure does not move",
          r2 is not None and r2.get("actual_hours") == 8.00,
          f"got {None if r2 is None else r2.get('actual_hours')}")

    # ── D2 — the pay that follows from those hours ───────────────────────────────────────────────
    check("D2 the pay follows the corrected hours (6.50 x 17.00 = 110.50)",
          r is not None and round(float(r.get("actual_pay") or 0), 2) == 110.50,
          f"got {None if r is None else r.get('actual_pay')}")

    # ── D3 — the row SAYS it was corrected (owner 2026-10-10: "mention why") ─────────────────────
    check("D3 the row names the correction as the basis",
          r is not None and HB.CLOCKED_CORRECTED in (r.get("hours_by_basis") or {}),
          f"got {None if r is None else r.get('hours_by_basis')}")

    # ── D4 — /payroll-by-store reconciles with /payroll ──────────────────────────────────────────
    by_store = R.get_payroll_by_store(month=None, start=LO, end=HI, authorization="", org_id=NY)
    stores = {s["store_code"]: s for s in (by_store or {}).get("stores", [])}
    total = round(sum(float(x.get("hours") or 0) for x in stores.values()), 2)
    rep_total = round(sum(float(x.get("actual_hours") or 0) for x in ny), 2)
    check("D4 /payroll-by-store totals the same corrected hours as /payroll",
          total == rep_total, f"by-store {total} vs report {rep_total}")

    # ── D5 — THE FLOOR, through the real handler ─────────────────────────────────────────────────
    STORE["payroll_hours_basis_config"] = [_cfg(NY, corrections_floor="2026-11-10"), _cfg(LUX),
                                           _cfg(HOUSE, mode="clocked_only", floor="2026-10-04")]
    HB.invalidate_config()
    ny_floored = R.get_payroll(month=None, start=LO, end=HI, authorization="", org_id=NY,
                              response=None)
    rf = _row(ny_floored, "E276")
    check("D5 a correction BEFORE the floor is not applied — the clock figure stands (9.00)",
          rf is not None and rf.get("actual_hours") == 9.00,
          f"got {None if rf is None else rf.get('actual_hours')}")

    # ── D6 — EVERY TENANT, measured against its own declaration ──────────────────────────────────
    STORE["payroll_hours_basis_config"] = [_cfg(NY), _cfg(LUX),
                                           _cfg(HOUSE, mode="clocked_only", floor="2026-10-04")]
    HB.invalidate_config()
    for org, eid, want in ((NY, "E276", 6.50), (LUX, "E198", 6.00), (HOUSE, "E012", 8.00)):
        rows = R.get_payroll(month=None, start=LO, end=HI, authorization="", org_id=org,
                             response=None)
        rr = _row(rows, eid)
        check(f"D6 {org[:8]}: the correction reaches its own figure ({want})",
              rr is not None and rr.get("actual_hours") == want,
              f"got {None if rr is None else rr.get('actual_hours')}")

    # ── D7 — NO LEAKAGE. One tenant's corrections must never reach another's figure. ─────────────
    lux = R.get_payroll(month=None, start=LO, end=HI, authorization="", org_id=LUX, response=None)
    check("D7 a tenant's report carries only its own employees",
          {x.get("employee_id") for x in lux} == {"E198"},
          f"got {sorted({x.get('employee_id') for x in lux})}")
    # Same employee id in two orgs, each with its OWN correction: neither may borrow the other's.
    STORE["employees"].append(_emp(LUX, "E276", 20.0, "9001", name="Lux Namesake"))
    STORE["timelog"].append(_punch(LUX, "E276", DAY, 9.00, "9001", name="Lux Namesake"))
    STORE["manual_hours"].append(_manual(LUX, "E276", DAY, -0.25))
    HB.invalidate_config()
    lux2 = R.get_payroll(month=None, start=LO, end=HI, authorization="", org_id=LUX, response=None)
    ny2 = R.get_payroll(month=None, start=LO, end=HI, authorization="", org_id=NY, response=None)
    check("D7 a shared employee id gets each org's OWN correction (lux 8.75)",
          _row(lux2, "E276") is not None and _row(lux2, "E276").get("actual_hours") == 8.75,
          f"got {None if _row(lux2, 'E276') is None else _row(lux2, 'E276').get('actual_hours')}")
    check("D7 and the other org's figure is unchanged by it (ny 6.50)",
          _row(ny2, "E276") is not None and _row(ny2, "E276").get("actual_hours") == 6.50,
          f"got {None if _row(ny2, 'E276') is None else _row(ny2, 'E276').get('actual_hours')}")

    # ── D8 — A MISSING manual_hours TABLE MUST NOT BREAK PAYROLL ─────────────────────────────────
    # A tenant whose mig-045 table is absent reads as "no corrections", never as a 500 and never as
    # zero hours. Failing closed here would turn a missing table into a missing wage line.
    saved = STORE.pop("manual_hours")
    HB.invalidate_config()
    try:
        degraded = R.get_payroll(month=None, start=LO, end=HI, authorization="", org_id=NY,
                                 response=None)
        rd = _row(degraded, "E276")
        check("D8 no manual_hours table -> the uncorrected figure, never an error or a zero",
              rd is not None and rd.get("actual_hours") == 9.00,
              f"got {None if rd is None else rd.get('actual_hours')}")
    except Exception as e:                                        # noqa: BLE001
        check("D8 no manual_hours table -> the uncorrected figure, never an error or a zero",
              False, f"raised {type(e).__name__}: {e}")
    STORE["manual_hours"] = saved
    HB.invalidate_config()

    # ── D9 — a correction on a day with a SHIFT as well as a punch ───────────────────────────────
    STORE["shifts"] = [{"id": 1, "org_id": NY, "employee_id": "E273", "employee_name": "Mohammad Ansari",
                        "store_code": "7710", "shift_date": DAY, "scheduled_hours": 8.0,
                        "actual_hours": 0, "is_deleted": False, "status": "scheduled"}]
    STORE["manual_hours"].append(_manual(NY, "E273", DAY, -1.00))
    HB.invalidate_config()
    with_shift = R.get_payroll(month=None, start=LO, end=HI, authorization="", org_id=NY,
                               response=None)
    rs = _row(with_shift, "E273")
    check("D9 a corrected day that also has a shift row counts the correction ONCE (8.00-1.00)",
          rs is not None and rs.get("actual_hours") == 7.00,
          f"got {None if rs is None else rs.get('actual_hours')}")

    return out


if __name__ == "__main__":
    rows = run()
    bad = [(n, d) for n, ok, d in rows if not ok]
    for n, ok, d in rows:
        print(("  ok  " if ok else "  FAIL") + f" {n}" + ("" if ok else f"\n        {d}"))
    print(f"\n{len(rows) - len(bad)} passed, {len(bad)} failed")
    sys.exit(1 if bad else 0)
