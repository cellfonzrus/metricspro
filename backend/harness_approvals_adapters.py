"""Offline proof for the approvals ENGINE ADAPTERS — the per-module handlers that make a unified-inbox
decision perform the module's real effect (Tier-A migration onto the engine, migration 867). Runs the
REAL engine + adapters + module effect against ONE shared in-memory fake Supabase client, proving for
each migrated type that:

  • a decision made in the unified Approvals inbox (engine.decide → the type's on_decide) flips the
    module's own record exactly as its legacy board would; and
  • a decision made on the legacy board (engine.sync_source_decision) reflects into the inbox without
    re-running the effect.

Covered so far: shift_extension, budget_override. (Extended as more surfaces are migrated.)

Run: `python3 harness_approvals_adapters.py` from backend/.
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, key):
        self.store, self.key, self.filters = store, key, []
        self._mode, self._payload, self._limit, self._order = "select", None, None, None

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self.filters.append((k, v)); return self

    def in_(self, k, vals):
        self.filters.append((k, ("__in__", set(str(x) for x in vals)))); return self

    def order(self, k, desc=False):
        self._order = (k, desc); return self

    def limit(self, n):
        self._limit = n; return self

    def insert(self, payload):
        self._mode, self._payload = "insert", payload; return self

    def update(self, payload):
        self._mode, self._payload = "update", payload; return self

    def _matches(self, row):
        for k, v in self.filters:
            if isinstance(v, tuple) and v and v[0] == "__in__":
                if str(row.get(k)) not in v[1]:
                    return False
            elif str(row.get(k)) != str(v):
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        if self._mode == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for p in payload:
                r = dict(p); r.setdefault("id", f"{self.key[1]}-{len(rows) + len(out) + 1}"); out.append(r)
            rows.extend(out)
            return Result(out)
        matched = [r for r in rows if self._matches(r)]
        if self._mode == "update":
            for r in matched:
                r.update(self._payload)
            return Result(matched)
        if self._order:
            k, desc = self._order
            matched = sorted(matched, key=lambda r: (r.get(k) is None, r.get(k)), reverse=desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        return Result(matched)


class FakeSchema:
    def __init__(self, store):
        self.store = store

    def table(self, t):
        return FakeQuery(self.store, ("storeops", t))


class FakeClient:
    def __init__(self):
        self.store = {}

    def schema(self, _n):
        return FakeSchema(self.store)


fake = FakeClient()

from app.modules.approvals import engine  # noqa: E402
import app.modules.approvals.router as AR  # noqa: E402  (imports the adapters for their side effect)
import app.modules.storeops.router as S  # noqa: E402
assert AR  # keep the import (adapters register on import)

# Point BOTH the engine and storeops at the same fake store, and silence the async notifier.
engine._sb = lambda: FakeSchema(fake.store)
engine._notify_approvers = lambda *a, **k: None
S.get_supabase = lambda: fake
S.sb = lambda: FakeSchema(fake.store)

ORG = "org-adapters"


# ── shift_extension ─────────────────────────────────────────────────────────────────────────────
def seed_ext(status="pending"):
    fake.store[("storeops", "shift_extension")] = [
        {"org_id": ORG, "id": "X1", "employee_name": "Alice", "store_code": "S1", "status": status}]


# A: decide via the UNIFIED INBOX → the shift_extension row flips.
fake.store.clear(); seed_ext()
req = engine.create_request(ORG, type="shift_extension", title="Alice: shift extension",
                            source_table="shift_extension", source_id="X1", store_code="S1")
check("shift_extension: request registered against the source row", req.get("status") == "pending" and req.get("id"), req)
engine.decide(ORG, req["id"], decision="approve", actor="dm@x")
ext = fake.store[("storeops", "shift_extension")][0]
check("shift_extension: an inbox APPROVE flips the shift_extension row to approved",
      ext["status"] == "approved" and ext["decided_by"] == "dm@x", ext)
check("shift_extension: ...and stamps the approval_request approved",
      engine.get_request(ORG, req["id"])["status"] == "approved")

# B: decide via the LEGACY board (sync only) → inbox reflects it, effect not re-run by the engine.
fake.store.clear(); seed_ext()
req2 = engine.create_request(ORG, type="shift_extension", title="Alice again",
                             source_table="shift_extension", source_id="X1", store_code="S1")
# legacy board already flipped the row itself; the engine just syncs the request:
fake.store[("storeops", "shift_extension")][0].update({"status": "denied", "decided_by": "mgr@x"})
engine.sync_source_decision(ORG, type="shift_extension", source_table="shift_extension", source_id="X1",
                            decision="deny", actor="mgr@x")
check("shift_extension: a legacy-board decision syncs the inbox request to denied",
      engine.get_request(ORG, req2["id"])["status"] == "denied")

# C: idempotency — the source already decided → inbox decide is a no-op on the row (still stamps request).
fake.store.clear(); seed_ext(status="approved")
req3 = engine.create_request(ORG, type="shift_extension", title="already done",
                             source_table="shift_extension", source_id="X1", store_code="S1")
engine.decide(ORG, req3["id"], decision="deny", actor="dm@x")
ext3 = fake.store[("storeops", "shift_extension")][0]
check("shift_extension: on_decide leaves an already-decided source row untouched",
      ext3["status"] == "approved", ext3)


# ── budget_override (same clean status-flip shape) ───────────────────────────────────────────────
def seed_ov(status="pending"):
    fake.store[("storeops", "budget_override")] = [
        {"org_id": ORG, "id": "OV1", "store_code": "S1", "week_start": "2026-08-17", "status": status}]


fake.store.clear(); seed_ov()
ov = engine.create_request(ORG, type="budget_override", title="S1: budget override",
                           source_table="budget_override", source_id="OV1", store_code="S1")
engine.decide(ORG, ov["id"], decision="approve", actor="dm@x")
check("budget_override: an inbox APPROVE flips the budget_override row",
      fake.store[("storeops", "budget_override")][0]["status"] == "approved",
      fake.store[("storeops", "budget_override")][0])
check("budget_override: ...and stamps the approval_request", engine.get_request(ORG, ov["id"])["status"] == "approved")

fake.store.clear(); seed_ov()
ov2 = engine.create_request(ORG, type="budget_override", title="S1 again",
                            source_table="budget_override", source_id="OV1", store_code="S1")
fake.store[("storeops", "budget_override")][0].update({"status": "denied"})
engine.sync_source_decision(ORG, type="budget_override", source_table="budget_override", source_id="OV1",
                            decision="deny", actor="mgr@x")
check("budget_override: a legacy-board decision syncs the inbox request",
      engine.get_request(ORG, ov2["id"])["status"] == "denied")


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
