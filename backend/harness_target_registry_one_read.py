"""Proof harness: the per-tenant TARGET FIELD REGISTRY is read ONCE per org, never once per report.

WHY THIS EXISTS (load test 2026-10-28, /mnt/project-files/load-test-2026-10-28/). The 1,000-user
run named `commcalc.target_field_registry` the single busiest table in the platform — 19,658 reads,
more than every other table combined — because `column_mapping._registry_overlay` asked the DB for
ONE report_key at a time and `_readiness_payload` walks every known report key, twice (once for
`target_fields`, once for `period_source_field`). GET /commcalc/column-mapping/readiness cost 88
PostgREST round trips, and the count GREW with the number of report types a tenant declares. Each
round trip is a separate HTTPS call held on one of the app's (workers x 40) request threads, so the
fan-out is spent directly out of the platform's concurrency budget.

THE CLASS, NOT THE INSTANCE (house rule 2026-09-20): the defect is not "the readiness page is slow",
it is "a per-tenant config table is read per key instead of per tenant". So the fix lives in the ONE
module every caller already dereferences (`target_registry`), not in the page that surfaced it, and
this harness is the lock: section A FAILS if any caller can make the query count grow with the number
of report keys again.

DB-free: a fake client records every query. Run: `python3 harness_target_registry_one_read.py`
from backend/.
"""
import os
import sys

sys.path.insert(0, ".")
os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")

from app.modules.commcalc import target_registry as tr  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


# ── a fake PostgREST client that counts reads of the registry table ───────────────────────────────
ORG = "00000000-0000-0000-0000-000000000001"
KEYS = [f"report_{i}" for i in range(20)]
ROWS = [{"org_id": ORG, "report_key": k, "target_field": f"f_{k}", "label": k.upper(),
         "transform": "text", "required": True, "default_source": "", "aliases": [],
         "sort_order": 100, "is_seeded": False}
        for k in KEYS]


class _Q:
    def __init__(self, owner, table):
        self.owner, self.table, self.filters = owner, table, {}
        self.op, self.row = "select", None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def limit(self, _n):
        return self

    def upsert(self, row, **_k):
        self.op, self.row = "upsert", row
        return self

    def delete(self):
        self.op = "delete"
        return self

    def execute(self):
        self.owner.queries.append((self.table, dict(self.filters), self.op))
        if self.owner.raises:
            raise RuntimeError("relation does not exist")
        if self.op == "upsert":
            self.owner.rows.append(dict(self.row))
            return type("R", (), {"data": [self.row]})()
        rows = [r for r in self.owner.rows
                if all(r.get(c) == v for c, v in self.filters.items())]
        if self.op == "delete":
            for r in rows:
                self.owner.rows.remove(r)
        return type("R", (), {"data": rows})()


class _Schema:
    def __init__(self, owner):
        self.owner = owner

    def table(self, name):
        return _Q(self.owner, name)


class FakeClient:
    def __init__(self, rows=ROWS, raises=False):
        self.rows, self.raises, self.queries = list(rows), raises, []

    def schema(self, _name):
        return _Schema(self)

    def registry_reads(self):
        return [q for q in self.queries if q[0] == tr.REGISTRY_TABLE and q[2] == "select"]


# ── A. ONE read per org, however many report keys are asked for (THE LOCK) ────────────────────────
tr.invalidate()
c = FakeClient()
for k in KEYS:
    tr.registry_tuples(c, ORG, k)
n_a = len(c.registry_reads())
check("A1. 20 report keys cost ONE registry read, not 20", n_a == 1, f"{n_a} reads")

tr.invalidate()
c2 = FakeClient()
for k in KEYS[:5]:
    tr.registry_tuples(c2, ORG, k)
n_b = len(c2.registry_reads())
check("A2. the read count does NOT grow with the number of keys",
      n_b == n_a, f"5 keys -> {n_b} reads, 20 keys -> {n_a}")

tr.invalidate()
c3 = FakeClient()
tr.registry_report_keys(c3, ORG)
for k in KEYS:
    tr.registry_tuples(c3, ORG, k)
    tr.load_registry(c3, ORG, k)
n_c = len(c3.registry_reads())
check("A3. report-key listing + 40 per-key reads still cost ONE query", n_c == 1, f"{n_c} reads")

# the one query must be scoped to the org and NOT narrowed to a single report_key
tr.invalidate()
c4 = FakeClient()
tr.load_registry(c4, ORG, KEYS[0])
only = c4.registry_reads()[0][1]
check("A4. the single read is org-scoped", only.get("org_id") == ORG, str(only))
check("A5. the single read is not narrowed to one report_key", "report_key" not in only, str(only))

# ── B. the answers are UNCHANGED — filtering moved into memory, nothing else ──────────────────────
tr.invalidate()
c5 = FakeClient()
rows_one = tr.load_registry(c5, ORG, KEYS[3])
check("B1. load_registry(report_key) returns only that report's rows",
      [r["report_key"] for r in rows_one] == [KEYS[3]], str(rows_one))
rows_all = tr.load_registry(c5, ORG)
check("B2. load_registry(no report_key) returns the whole org registry",
      len(rows_all) == len(KEYS), f"{len(rows_all)} rows")
check("B3. registry_report_keys lists every declared key",
      tr.registry_report_keys(c5, ORG) == sorted(KEYS), "")
tup = tr.registry_tuples(c5, ORG, KEYS[2])
check("B4. registry_tuples keeps the column_mapping field-tuple shape",
      tup == [(f"f_{KEYS[2]}", KEYS[2].upper(), "text", True, "", [])], str(tup))
check("B5. a tenant with no registry row gets []", tr.load_registry(c5, "other-org") == [], "")

# ── C. a write makes the next read current (no stale config after add/remove) ─────────────────────
tr.invalidate()
c6 = FakeClient()
tr.load_registry(c6, ORG)                       # warm
tr.add_field(c6, ORG, "report_new", "Brand New")
after = tr.registry_report_keys(c6, ORG)
check("C1. add_field invalidates, so the new key is visible at once",
      "report_new" in after, str(after[-3:]))
tr.remove_field(c6, ORG, "report_new", "brand_new")
check("C2. remove_field invalidates too",
      "report_new" not in tr.registry_report_keys(c6, ORG), "")

# ── D. failure is never cached (an un-run migration must self-heal, per identity.py's posture) ────
tr.invalidate()
c7 = FakeClient(raises=True)
check("D1. an unreadable table still degrades to []", tr.load_registry(c7, ORG) == [], "")
c7.raises = False
check("D2. the failure is NOT cached — the next read succeeds",
      len(tr.load_registry(c7, ORG)) == len(KEYS), "")

# ── E. tenants never see each other's registry out of one cache ───────────────────────────────────
tr.invalidate()
OTHER = "00000000-0000-0000-0000-0000000000ff"
c8 = FakeClient(rows=ROWS + [{"org_id": OTHER, "report_key": "secret_report",
                              "target_field": "x", "label": "X", "transform": "text",
                              "required": False, "default_source": "", "aliases": [],
                              "sort_order": 1, "is_seeded": False}])
tr.load_registry(c8, ORG)
check("E1. org A's cache never leaks org B's rows",
      "secret_report" not in tr.registry_report_keys(c8, ORG), "")
check("E2. org B still sees its own row",
      tr.registry_report_keys(c8, OTHER) == ["secret_report"], "")


# ── F. THE REAL ENDPOINT, through the real app stack (integration) ───────────────────────────────
# Section A proves the module. This section proves the PAGE that surfaced the defect: the shipped
# GET /commcalc/column-mapping/readiness handler, driven against the in-memory client through the
# same chokepoint production resolves (`_harness_dbfree.install`), so nothing between the HTTP
# handler and the data layer is stubbed out. It is the regression for the measured 88 round trips.
import _harness_dbfree  # noqa: E402

ENDPOINT_KEYS = [f"endpoint_report_{i}" for i in range(12)]
ENDPOINT_ROWS = [dict(r, report_key=k) for k, r in
                 zip(ENDPOINT_KEYS, (dict(ROWS[0]) for _ in ENDPOINT_KEYS))]


class _AnyQ(_Q):
    """Tolerant of the filter verbs the real handler chains (order/in_/neq/not_/gte/…)."""

    def __getattr__(self, _name):
        return lambda *a, **k: self


class _AnySchema(_Schema):
    def table(self, name):
        return _AnyQ(self.owner, name)


class EndpointClient(FakeClient):
    def schema(self, _name):
        return _AnySchema(self)

    def table(self, name):
        return _AnyQ(self, name)


endpoint_client = EndpointClient(rows=ENDPOINT_ROWS)
_harness_dbfree.install(endpoint_client)
tr.invalidate()

from app.modules.commcalc import router as commcalc_router  # noqa: E402

payload = commcalc_router.column_mapping_readiness(org_id=ORG)
reads = len(endpoint_client.registry_reads())
check("F1. the real readiness endpoint reads the registry ONCE", reads == 1, f"{reads} reads")
check("F2. it still answers with a reports map", isinstance(payload.get("reports"), dict),
      str(type(payload.get("reports"))))
check("F3. every declared report key is present in the answer",
      all(k in payload["reports"] for k in ENDPOINT_KEYS),
      str(sorted(payload["reports"])[:4]))
total_db = len(endpoint_client.queries)
check("F4. the whole page costs a handful of queries, not dozens", total_db <= 10, f"{total_db} queries")

tr.invalidate()
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
