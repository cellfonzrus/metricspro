"""Offline proof harness for the ops_chargebacks.py N+1 performance fix (OWNER BUG REPORT 2026-07-29:
"DM verify after doing one verification goes into a loop and locks out for over 3-4 minutes").

Root cause: `GET /closing/ops-chargebacks/dm-verify` (the "Missed verifications & chargebacks" panel
at the top of the DM Verify page) re-runs `detect_missed_dm_verifies` on EVERY call, with NO caching.
That function's `_run_missed_dm_verify_detection` used to call `storeops.router._dm_for_store` (itself
3-5 SEQUENTIAL queries: stores, org_levels, org_units, org_managers, employees) AND `_cb_exists` (1
more query) once per still-unverified (day, store) pair — a real backlog of unverified store-days
turned into hundreds of sequential synchronous Supabase round trips on literally every page load,
long enough (given FastAPI runs a sync `def` handler in the shared worker threadpool) to starve OTHER
requests behind it — which is what reads to the user as "the page locks up after I verify one store."
`_run_missed_closing_detection` (used by the time-clock punch notice, a separate hot path) had the
same `_cb_exists`-per-pair pattern.

Fix (`_dm_for_stores_batch`/`_existing_chargeback_keys` in ops_chargebacks.py): resolve every
candidate store's DM in ONE batched pass (5 queries total, regardless of pair count) and check
existing chargebacks in ONE query, both BEFORE the per-pair loop — same chargebacks created, same
idempotency semantics, just O(1) queries instead of O(pairs).

Run: `cd backend && python3 harness_ops_chargebacks_perf.py`

No live DB/network — a fake Supabase client (same convention as the other harnesses in this tree) that
ALSO counts per-table READS separately from WRITES, so the query-count claim is proven quantitatively,
not just asserted. Drives the REAL `detect_missed_dm_verifies`/`detect_missed_closings` functions end
to end.
"""
import sys
from types import SimpleNamespace
from datetime import date, timedelta

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"


class Q:
    def __init__(self, store, table):
        self.s, self.t = store, table
        self.op, self.payload, self.on_conflict = "select", None, None
        self.filters = []
        self._is_null = []
        self._limit = None

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
    def update(self, patch, **k): self.op = "update"; self.payload = patch; return self
    def delete(self, **k): self.op = "delete"; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def is_(self, c, v):
        self._is_null.append((c, v)); return self
    def order(self, *a, **k): return self
    def limit(self, n, *a, **k): self._limit = n; return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
        for c, v in self._is_null:
            if v == "null" and row.get(c) is not None: return False
        return True

    def execute(self):
        rows = self.s.setdefault(self.t, [])
        if self.op == "select":
            matched = [dict(r) for r in rows if self._match(r)]
            if self._limit is not None:
                matched = matched[: self._limit]
            return SimpleNamespace(data=matched)
        if self.op == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in payload:
                r = dict(r); r.setdefault("id", f"id-{len(rows) + 1}")
                rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload); out.append(dict(r))
            return SimpleNamespace(data=out)
        return SimpleNamespace(data=[])


class CountingQ(Q):
    """Same as Q, but reports its executed op back to the parent client's per-table READ counter —
    separate from writes (INSERT), since a real chargeback insert is unavoidable, necessary work (one
    per NEW row) and isn't part of the N+1 READ pattern this fix eliminates."""
    def __init__(self, parent, table):
        super().__init__(parent.store, table)
        self.parent = parent

    def execute(self):
        bucket = self.parent.reads if self.op == "select" else self.parent.writes
        bucket[self.t] = bucket.get(self.t, 0) + 1
        return super().execute()


class CountingClient:
    """schema()/table() chain identical to the app's, but tallies how many times each TABLE is READ
    (select) vs WRITTEN (insert/update) — the actual, quantitative proof that resolving N unverified
    pairs no longer costs N times the org-tree/roster READS. Writes (one INSERT per genuinely NEW
    chargeback) are real, unavoidable work and tracked separately so they don't muddy the READ-count
    claim."""
    def __init__(self, store):
        self.store = store
        self.reads: dict[str, int] = {}
        self.writes: dict[str, int] = {}

    def schema(self, _n): return self

    def table(self, name):
        return CountingQ(self, name)


def fresh_store():
    return {"daily_closing": [], "daily_closing_verification": [], "ops_chargeback": [],
            "ops_chargeback_policy": [], "stores": [], "org_levels": [], "org_units": [],
            "org_managers": [], "employees": [], "timelog": [], "store_closer": []}


import app.modules.closing.ops_chargebacks as oc   # noqa: E402


def wire(store):
    fake = CountingClient(store)
    oc.sb = lambda: fake
    oc.get_supabase = lambda: fake
    return fake


TODAY = date(2026, 7, 30)
oc._biz_today = lambda org_id=None: TODAY

STORE_CODES = [f"S{i}" for i in range(1, 6)]   # 5 stores
DAYS = [(TODAY - timedelta(days=d)).isoformat() for d in (1, 2, 3)]   # 3 unverified days each -> 15 pairs

st = fresh_store()
fake = wire(st)
st["ops_chargeback_policy"] = [{"org_id": HOUSE, "reason": "missed_dm_verify", "amount": 25.0, "enabled": True}]
st["stores"] = [{"org_id": HOUSE, "store_code": c, "org_unit_id": "unit-store", "market": "Texas"} for c in STORE_CODES]
st["org_levels"] = [{"org_id": HOUSE, "id": "lvl-store", "name": "Store"},
                    {"org_id": HOUSE, "id": "lvl-district", "name": "District"}]
st["org_units"] = [
    {"org_id": HOUSE, "id": "unit-store", "name": "Store Level", "level_id": "lvl-store",
     "parent_id": "unit-district", "code": None},
    {"org_id": HOUSE, "id": "unit-district", "name": "District One", "level_id": "lvl-district",
     "parent_id": None, "code": "district:texas"},
]
st["org_managers"] = [{"org_id": HOUSE, "unit_id": "unit-district", "employee_id": "emp-dm1"}]
st["employees"] = [{"org_id": HOUSE, "employee_id": "emp-dm1", "id": "emp-dm1", "name": "Dana DM", "email": "dana@x.com"}]
st["daily_closing"] = [
    {"org_id": HOUSE, "store_code": code, "close_date": d}
    for code in STORE_CODES for d in DAYS
]
st["daily_closing_verification"] = []   # nothing verified -> all 15 pairs are "missed"

rows_before = oc.detect_missed_dm_verifies(HOUSE, lookback_days=14)
inserted = [r for r in st["ops_chargeback"] if r.get("reason") == "missed_dm_verify"]
check("1. all 15 (store, day) pairs got a chargeback charged to the shared district's DM",
      len(inserted) == 15 and all(r["employee_id"] == "emp-dm1" for r in inserted), str(len(inserted)))
check("2. every chargeback's employee_name is the CANONICAL roster name ('Dana DM'), not raw/blank",
      all(r["employee_name"] == "Dana DM" for r in inserted), str({r["employee_name"] for r in inserted}))

# The actual, quantitative perf claim: org-tree/roster tables queried O(1), NOT O(pairs=15).
check("3. storeops.stores queried a SMALL bounded number of times (<=2), not once per pair (15)",
      fake.reads.get("stores", 0) <= 2, str(fake.reads.get("stores")))
check("4. storeops.org_levels queried EXACTLY once (was previously re-fetched per pair via "
      "_dm_for_store's per-store district walk)",
      fake.reads.get("org_levels", 0) == 1, str(fake.reads.get("org_levels")))
check("5. storeops.org_units queried EXACTLY once (same as org_levels)",
      fake.reads.get("org_units", 0) == 1, str(fake.reads.get("org_units")))
check("6. storeops.org_managers queried EXACTLY once (batched — one .in_(unit_id, [...]) call)",
      fake.reads.get("org_managers", 0) == 1, str(fake.reads.get("org_managers")))
check("7. storeops.employees queried a SMALL bounded number of times (<=2: the roster fetch + the "
      "batched DM-employee lookup), not once per pair",
      fake.reads.get("employees", 0) <= 2, str(fake.reads.get("employees")))
check("8. commcalc.ops_chargeback queried a SMALL bounded number of times (<=2: the batched "
      "existence check + the final list_chargebacks call), not once per pair (15 old _cb_exists calls)",
      fake.reads.get("ops_chargeback", 0) <= 2, str(fake.reads.get("ops_chargeback")))

# ═══ Idempotency preserved: a SECOND sweep (e.g. the very next page load) must not re-insert ═══════
rows_after_first = len(st["ops_chargeback"])
fake.reads.clear()
rows_second_call = oc.detect_missed_dm_verifies(HOUSE, lookback_days=14)
check("9. idempotent — a second sweep inserts NOTHING new (same 15 rows, not 30)",
      len(st["ops_chargeback"]) == rows_after_first, str(len(st["ops_chargeback"])))
check("10. the second sweep's own query counts stay bounded too (batched existence check, not "
      "15 more per-pair _cb_exists calls)",
      fake.reads.get("ops_chargeback", 0) <= 2, str(fake.reads.get("ops_chargeback")))

# ═══ A store with NO resolvable DM (no district match) is skipped, never guessed — unchanged ═══════
st2 = fresh_store()
fake2 = wire(st2)
st2["ops_chargeback_policy"] = [{"org_id": HOUSE, "reason": "missed_dm_verify", "amount": 25.0, "enabled": True}]
st2["stores"] = [{"org_id": HOUSE, "store_code": "S9", "org_unit_id": None, "market": None}]
st2["daily_closing"] = [{"org_id": HOUSE, "store_code": "S9", "close_date": DAYS[0]}]
oc.detect_missed_dm_verifies(HOUSE, lookback_days=14)
check("11. a store with no resolvable district/DM gets NO chargeback (never guesses)",
      len(st2["ops_chargeback"]) == 0, str(st2["ops_chargeback"]))

# ═══ _run_missed_closing_detection (the time-clock punch-notice hot path) — same batched existence
#     check, same idempotency, correctness unchanged ═════════════════════════════════════════════
st3 = fresh_store()
fake3 = wire(st3)
st3["ops_chargeback_policy"] = [{"org_id": HOUSE, "reason": "missed_closing", "amount": 15.0, "enabled": True}]
st3["timelog"] = [
    {"org_id": HOUSE, "employee_id": "e1", "employee_name": "Rep One", "store_code": "S1",
     "work_date": DAYS[0], "clock_in": "2026-07-29T09:00:00Z", "clock_out": "2026-07-29T17:00:00Z"},
    {"org_id": HOUSE, "employee_id": "e2", "employee_name": "Rep Two", "store_code": "S2",
     "work_date": DAYS[0], "clock_in": "2026-07-29T09:00:00Z", "clock_out": "2026-07-29T17:00:00Z"},
]
st3["daily_closing"] = []   # neither store closed -> both are "missed"
items = oc.detect_missed_closings(HOUSE, lookback_days=7)
missed_rows = [r for r in st3["ops_chargeback"] if r.get("reason") == "missed_closing"]
check("12. missed_closing: both unclosed stores charged their effective closer",
      len(missed_rows) == 2, str(missed_rows))
check("13. missed_closing: commcalc.ops_chargeback queried a SMALL bounded number of times (batched "
      "existence check, not once per pair)",
      fake3.reads.get("ops_chargeback", 0) <= 2, str(fake3.reads.get("ops_chargeback")))
rows_before_2nd = len(st3["ops_chargeback"])
oc.detect_missed_closings(HOUSE, lookback_days=7)
check("14. missed_closing: idempotent on a second sweep (no duplicate rows)",
      len(st3["ops_chargeback"]) == rows_before_2nd, str(len(st3["ops_chargeback"])))

# ── Summary ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
