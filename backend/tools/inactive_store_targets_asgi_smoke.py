"""REAL-ASGI smoke — disabled stores must not reach Targets (owner defect 2026-08-06).

The helper proof (`tools/active_store_roster_proof.py`) proves the PREDICATE. This drives the whole
FastAPI app through Starlette's TestClient at the EXACT `/api/v1/...` URLs the Targets pages request,
because the mount + query-param binding is its own repeat-offender trap
(`[[curl-verified-not-ui-verified-apiv1]]`), and `include_inactive` is a NEW query param.

Fixture = the live house roster shape probed on 2026-08-06: 20 active B-stores, the 6 T-stores the
operator disabled (`T-531 T-7812 T-902 T-957 T21880 T3560`, all market TT, NULL address), and one
store whose `is_active` is NULL — the row the naive `.eq("is_active", True)` fix would have destroyed.

Asserts:
  • GET /targets/{period}                      -> the 6 disabled stores are GONE, the NULL-flag store STAYS
  • GET /targets/{period}?include_inactive=1   -> all 27 come back (the /storeops/employees convention)
  • a disabled store that HAS a saved target for the period -> STILL returned, `is_active: false`
    (history is not rewritten) and `_seeded: false` (it was never re-seeded)
  • POST /targets/{period}/roll-forward        -> writes ZERO rows for any disabled store, and reports
    `inactive_skipped: 6` instead of skipping silently
  • GET /targets/{period}/summary              -> disabled stores absent from `stores` AND from the
    `filters.stores` dropdown; a disabled store with a target row for the month still renders
  • the bare `/commcalc/targets/...` (no /api/v1) is 404

Run: `python3 tools/inactive_store_targets_asgi_smoke.py` from the backend dir.
"""
import os
import sys
from urllib.parse import quote

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))
os.environ.setdefault("COMMCALC_CFG_CACHE_TTL", "0")

from fastapi.testclient import TestClient           # noqa: E402
from app.main import app                            # noqa: E402
import app.core.database as DB                      # noqa: E402
from app.modules.commcalc import router as R        # noqa: E402
from app.modules.storeops import router as SO       # noqa: E402

ORG = "00000000-0000-0000-0000-000000000001"
PERIOD = "August 2026"
DISABLED = ["T-531", "T-7812", "T-902", "T-957", "T21880", "T3560"]
ACTIVE = [f"B-{i}" for i in range(1, 21)]

STORES = (
    [{"store_code": c, "address": f"{c} Main St", "market": "LI",
      "monthly_target": 8000, "is_active": True} for c in ACTIVE]
    + [{"store_code": c, "address": None, "market": "TT",
        "monthly_target": 6000, "is_active": False} for c in DISABLED]
    + [{"store_code": "NULLFLAG", "address": "9 Legacy Ave", "market": "NJ",
        "monthly_target": 5000, "is_active": None}]
)

WRITES = []          # every upsert the roll-forward performs
SAVED_TARGET_CODES = []   # mutated per scenario


def _target_row(code):
    return {"org_id": ORG, "store_code": code, "period": PERIOD, "period_month": 8,
            "period_year": 2026, "activations_monthly": 50, "upgrades_monthly": 25,
            "accessories_monthly": 6000.0, "byod_pct": None, "notes": None}


class _Q:
    def __init__(self, schema, table, kind="select"):
        self.schema, self.table, self.kind = schema, table, kind

    def __getattr__(self, _n):                     # eq / in_ / order / limit / range …
        return lambda *a, **k: self

    def execute(self):
        data = []
        if self.kind == "select":
            if (self.schema, self.table) == ("storeops", "stores"):
                data = [dict(s) for s in STORES]
            elif (self.schema, self.table) == ("commcalc", "targets"):
                data = [_target_row(c) for c in SAVED_TARGET_CODES]
            elif (self.schema, self.table) == ("commcalc", "payout_config"):
                data = [{"kpi_byod_target": 35.0}]
        return type("R", (), {"data": data})()


class _Tbl:
    def __init__(self, schema, table):
        self.schema, self.table = schema, table

    def select(self, *a, **k):
        return _Q(self.schema, self.table)

    def upsert(self, row, **k):
        WRITES.append(row)
        return _Q(self.schema, self.table, "upsert")

    def insert(self, row, **k):
        WRITES.append(row)
        return _Q(self.schema, self.table, "insert")


class _Schema:
    def __init__(self, name):
        self.name = name

    def table(self, t):
        return _Tbl(self.name, t)

    def rpc(self, *a, **k):
        return _Q(self.name, "rpc")


class _Client:
    def schema(self, n):
        return _Schema(n)

    def table(self, t):
        return _Tbl("storeops", t)

    def rpc(self, *a, **k):
        return _Q("public", "rpc")


DB.get_supabase = lambda: _Client()
R.sb = lambda: _Client()
SO.sb = lambda: _Client()
SO.scope_keyset = lambda *a, **k: None            # unrestricted caller (admin / RBAC off)
SO.in_keyset = lambda *a, **k: True
R._fetch_shifts = lambda *a, **k: []
R._fetch_actuals = lambda *a, **k: []
R._targets_trending_by_code = lambda *a, **k: ({}, {})
R._caller_rep_keys = lambda *a, **k: None
# The carry-forward MATH is proven elsewhere; here it must only ever be handed the roster this
# package filtered, so echo back a fixed suggestion per code it is given.
R._carry_forward_map = lambda client, org_id, period, stores: {
    "prior_period": "July 2026",
    "by_code": {str(s.get("store_code", "")).upper():
                {"activations_monthly": 40, "upgrades_monthly": 20, "accessories_monthly": 5000.0,
                 "byod_pct": None, "basis": {"activations": "carry"}} for s in stores},
}

client = TestClient(app)
_pass, _fail = 0, 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print(f"  FAIL  {name}  {extra}")


def get(path, **params):
    params.setdefault("org_id", ORG)
    r = client.get(path, params=params)
    assert r.status_code == 200, (path, r.status_code, r.text[:400])
    return r.json()


P = quote(PERIOD)

# ── Scenario A: nothing saved yet — every store would be SEEDED ────────────────────────────────
SAVED_TARGET_CODES = []
d = get(f"/api/v1/commcalc/targets/{P}")
codes = [t["store_code"] for t in d["targets"]]
_bad = [c for c in codes if c in DISABLED]
check("A: no disabled store is seeded a target", _bad == [], _bad)
check("A: all 20 active stores still seeded", sorted(c for c in codes if c.startswith("B-")) == sorted(ACTIVE))
check("A: the NULL-is_active store SURVIVES (the trap)", "NULLFLAG" in codes, codes)
check("A: 21 of 27 returned", len(codes) == 21, len(codes))
check("A: every returned row carries is_active", all("is_active" in t for t in d["targets"]))

d = get(f"/api/v1/commcalc/targets/{P}", include_inactive=1)
codes = [t["store_code"] for t in d["targets"]]
check("A: include_inactive=1 returns all 27", len(codes) == 27, len(codes))
check("A: include_inactive=1 flags them inactive",
      sorted(t["store_code"] for t in d["targets"] if t["is_active"] is False) == sorted(DISABLED))

# ── Scenario B: a disabled store already HAS a saved target — history must not be rewritten ────
SAVED_TARGET_CODES = ACTIVE + ["T-902", "T3560"]
d = get(f"/api/v1/commcalc/targets/{P}")
rows = {t["store_code"]: t for t in d["targets"]}
check("B: the two disabled stores WITH a saved target still render",
      "T-902" in rows and "T3560" in rows, sorted(rows))
check("B: they are labelled inactive",
      rows.get("T-902", {}).get("is_active") is False and rows.get("T3560", {}).get("is_active") is False)
check("B: they were NOT re-seeded (_seeded false)",
      rows.get("T-902", {}).get("_seeded") is False)
check("B: their saved numbers are untouched",
      rows.get("T-902", {}).get("activations_monthly") == 50)
_bad = [c for c in ("T-531", "T-7812", "T-957", "T21880") if c in rows]
check("B: the 4 disabled stores with NOTHING saved stay gone", _bad == [], _bad)

# ── Scenario C: roll-forward must never CREATE a target for a disabled store ───────────────────
SAVED_TARGET_CODES = []
WRITES.clear()
r = client.post(f"/api/v1/commcalc/targets/{P}/roll-forward", params={"org_id": ORG}, json={})
assert r.status_code == 200, r.text[:400]
j = r.json()
written_codes = sorted({w["store_code"] for w in WRITES})
_bad = [c for c in written_codes if c in DISABLED]
check("C: roll-forward wrote ZERO disabled stores", _bad == [], _bad)
check("C: roll-forward wrote all 20 active + the NULL-flag store",
      written_codes == sorted(ACTIVE + ["NULLFLAG"]), written_codes)
check("C: it reports the skip instead of hiding it", j.get("inactive_skipped") == 6, j.get("inactive_skipped"))
check("C: written count matches", j.get("written") == 21, j.get("written"))

# ── Scenario D: the all-stores summary + its filter dropdown ───────────────────────────────────
SAVED_TARGET_CODES = ACTIVE + ["T-902"]
d = get(f"/api/v1/commcalc/targets/{P}/summary", today="2026-08-06")
scodes = [s["store_code"] for s in d["stores"]]
fcodes = [o["value"] for o in d["filters"]["stores"]]
_bad = [c for c in scodes if c in DISABLED and c != "T-902"]
check("D: disabled stores absent from the summary", _bad == [], _bad)
_bad = [c for c in fcodes if c in DISABLED and c != "T-902"]
check("D: disabled stores absent from the filter dropdown", _bad == [], _bad)
check("D: T-902 (has a target this month) still renders", "T-902" in scodes, scodes)
check("D: T-902 renders flagged inactive",
      [s["is_active"] for s in d["stores"] if s["store_code"] == "T-902"] == [False])
check("D: the NULL-flag store is in the dropdown", "NULLFLAG" in fcodes, fcodes)

d = get(f"/api/v1/commcalc/targets/{P}/summary", today="2026-08-06", include_inactive=1)
check("D: include_inactive=1 brings every disabled store back",
      all(c in [o["value"] for o in d["filters"]["stores"]] for c in DISABLED))

# ── E: the /api/v1 prefix trap ─────────────────────────────────────────────────────────────────
check("E: bare /commcalc/targets/... is 404 (pages MUST use /api/v1)",
      client.get(f"/commcalc/targets/{P}", params={"org_id": ORG}).status_code == 404)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
