"""Offline proof for the store merchant-ID registry (storeops/merchant_ids.py, migration 902).
Proves the ingest resolver (terminal/merchant id -> store_code) and the store-setup coverage audit.

Run: `python3 harness_merchant_ids.py` from backend/.
"""
import sys
sys.path.insert(0, ".")

import app.modules.storeops.merchant_ids as M  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


class _R:
    def __init__(self, data): self.data = data


class _Q:
    def __init__(self, rows): self._rows, self._f, self._payload, self._mode = rows, [], None, "select"
    def select(self, *_a, **_k): return self
    def eq(self, k, v): self._f.append((k, v)); return self
    def order(self, *_a, **_k): return self
    def upsert(self, payload, **_k): self._mode, self._payload = "upsert", payload; return self
    def execute(self):
        if self._mode == "upsert":
            return _R([self._payload])
        out = [r for r in self._rows if all(r.get(k) == v for k, v in self._f)]
        return _R(out)


class _Schema:
    def __init__(self, rows): self._rows = rows
    def table(self, _t): return _Q(self._rows)


class _Client:
    def __init__(self, rows): self._rows = rows
    def schema(self, _n): return _Schema(self._rows)


ORG = "org-1"
ROWS = [
    {"org_id": ORG, "store_code": "418", "processor": "epay", "merchant_id": "633423", "not_required": False},
    {"org_id": ORG, "store_code": "117", "processor": "epay", "merchant_id": "648757", "not_required": False},
    {"org_id": ORG, "store_code": "652", "processor": "epay", "merchant_id": None, "not_required": True},   # opted out
    {"org_id": ORG, "store_code": "418", "processor": "vidapay", "merchant_id": "V-418", "not_required": False},
    {"org_id": "other", "store_code": "999", "processor": "epay", "merchant_id": "633423", "not_required": False},  # other tenant
]
M.get_supabase = lambda: _Client(ROWS)

# resolve_map / resolve_store — the ingest's terminal -> store lookup, org- and processor-scoped.
m = M.resolve_map(ORG, "epay")
check("resolve_map returns configured epay terminals for the tenant", m == {"633423": "418", "648757": "117"}, m)
check("resolve_map excludes an opted-out store (no merchant_id)", "652" not in m.values(), m)
check("resolve_map is processor-scoped (vidapay id absent from epay map)", "V-418" not in m, m)
check("resolve_store maps a terminal id to its store", M.resolve_store(ORG, "epay", "633423") == "418")
check("resolve_store maps the vidapay id under the vidapay processor", M.resolve_store(ORG, "vidapay", "V-418") == "418")
check("resolve_store returns None for an unmapped terminal", M.resolve_store(ORG, "epay", "000000") is None)
check("resolve_store never crosses tenants", M.resolve_store("org-1", "epay", "633423") == "418")

# coverage — which stores still lack a decision (neither an id nor a not_required opt-out).
missing = M.coverage(ORG, ["418", "117", "652", "500", "600"], "epay")
check("coverage: a store with an id is configured", "418" not in missing and "117" not in missing, missing)
check("coverage: an opted-out store counts as configured (decision made)", "652" not in missing, missing)
check("coverage: stores with no row at all are unconfigured", missing == {"500", "600"}, missing)

# upsert returns the written row shape.
w = M.upsert(ORG, "500", "epay", merchant_id="661543")
check("upsert returns the written row", w and w[0]["store_code"] == "500" and w[0]["merchant_id"] == "661543", w)
check("upsert normalizes a blank merchant_id to None (opt-out path)",
      M.upsert(ORG, "600", "epay", merchant_id="  ", not_required=True)[0]["merchant_id"] is None)
try:
    M.upsert(ORG, "", "epay"); _raised = False
except ValueError:
    _raised = True
check("upsert rejects a missing store_code", _raised)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1)
