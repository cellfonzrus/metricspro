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
    def __init__(self, store, schema="storeops"):
        self.store = store
        self.schema_name = schema

    def table(self, t):
        return FakeQuery(self.store, (self.schema_name, t))


class FakeClient:
    def __init__(self):
        self.store = {}

    def schema(self, n):
        return FakeSchema(self.store, n)


fake = FakeClient()

from app.modules.approvals import engine  # noqa: E402
import app.modules.approvals.router as AR  # noqa: E402  (imports the adapters for their side effect)
import app.modules.storeops.router as S  # noqa: E402
assert AR  # keep the import (adapters register on import)

# Point BOTH the engine and storeops at the same fake store, and silence the async notifier.
engine._sb = lambda: FakeSchema(fake.store, "storeops")
engine._notify_approvers = lambda *a, **k: None
S.get_supabase = lambda: fake
S.sb = lambda: FakeSchema(fake.store, "storeops")

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


# ── closing_expense (MONEY-CRITICAL: approve books the line onto the P&L) ─────────────────────────
import app.modules.closing.router as C  # noqa: E402

C.sb = lambda: fake   # closing reads/writes commcalc.closing_expense on the same fake store
PL_PUSHES = []


def _fake_pl_push(client, org_id, period, cat_id, cat_name, *a, **k):
    PL_PUSHES.append((org_id, period, cat_id))
    return {"pushed": True, "stores": 1}


C._push_expense_category_pl = _fake_pl_push   # record P&L pushes instead of hitting the internal API


def seed_exp(status="pending"):
    fake.store[("commcalc", "closing_expense")] = [
        {"org_id": ORG, "id": "E1", "store_code": "S1", "close_date": "2026-08-15", "amount": 42.50,
         "category_kind": "expense", "category_id": "CAT1", "category_name": "Supplies",
         "description": "printer ink", "status": status}]


# A: decide via the UNIFIED INBOX → the closing_expense row flips to approved AND the P&L is pushed.
fake.store.clear(); PL_PUSHES.clear(); seed_exp()
er = engine.create_request(ORG, type="closing_expense", title="Store expense $42.50 — Supplies at S1",
                           source_table="closing_expense", source_id="E1", store_code="S1")
check("closing_expense: request registered against the source row", er.get("status") == "pending" and er.get("id"), er)
engine.decide(ORG, er["id"], decision="approve", actor="dm@x")
exp = fake.store[("commcalc", "closing_expense")][0]
check("closing_expense: an inbox APPROVE flips the line to 'approved' (module vocabulary)",
      exp["status"] == "approved" and exp["approved_by"] == "dm@x", exp)
check("closing_expense: ...and pushes the category P&L for the line's period exactly once",
      PL_PUSHES == [(ORG, "2026-08", "CAT1")], PL_PUSHES)
check("closing_expense: ...and stamps the approval_request approved",
      engine.get_request(ORG, er["id"])["status"] == "approved")

# A2: an inbox DENY maps to 'rejected' and does NOT touch the P&L.
fake.store.clear(); PL_PUSHES.clear(); seed_exp()
er2 = engine.create_request(ORG, type="closing_expense", title="reject me",
                            source_table="closing_expense", source_id="E1", store_code="S1")
engine.decide(ORG, er2["id"], decision="deny", actor="dm@x")
exp2 = fake.store[("commcalc", "closing_expense")][0]
check("closing_expense: an inbox DENY flips the line to 'rejected' (never 'denied')", exp2["status"] == "rejected", exp2)
check("closing_expense: a DENY pushes NO P&L", PL_PUSHES == [], PL_PUSHES)
check("closing_expense: ...and stamps the approval_request denied",
      engine.get_request(ORG, er2["id"])["status"] == "denied")

# B: decide via the LEGACY board (sync only) → inbox reflects it, effect not re-run by the engine.
fake.store.clear(); PL_PUSHES.clear(); seed_exp()
er3 = engine.create_request(ORG, type="closing_expense", title="legacy",
                            source_table="closing_expense", source_id="E1", store_code="S1")
# legacy management board already applied the effect itself; the engine just syncs the request:
fake.store[("commcalc", "closing_expense")][0].update({"status": "approved", "approved_by": "mgr@x"})
engine.sync_source_decision(ORG, type="closing_expense", source_table="closing_expense", source_id="E1",
                            decision="approve", actor="mgr@x")
check("closing_expense: a legacy-board decision syncs the inbox request to approved",
      engine.get_request(ORG, er3["id"])["status"] == "approved")
check("closing_expense: sync does NOT re-run the P&L push (module already did the effect)", PL_PUSHES == [], PL_PUSHES)

# C: idempotency — the line already decided → inbox decide is a no-op on the row + no P&L (still stamps request).
fake.store.clear(); PL_PUSHES.clear(); seed_exp(status="approved")
er4 = engine.create_request(ORG, type="closing_expense", title="already done",
                            source_table="closing_expense", source_id="E1", store_code="S1")
engine.decide(ORG, er4["id"], decision="deny", actor="dm@x")
exp4 = fake.store[("commcalc", "closing_expense")][0]
check("closing_expense: on_decide leaves an already-decided line untouched", exp4["status"] == "approved", exp4)
check("closing_expense: ...and pushes no P&L on the idempotent no-op", PL_PUSHES == [], PL_PUSHES)


# ── referral (MONEY-CRITICAL: gated commission; approve books amount + payout, SoD-gated) ─────────
import app.modules.referral.router as R  # noqa: E402
import app.modules.referral.referral_core as RC  # noqa: E402

R.get_supabase = lambda: fake          # referral.sb() -> fake.schema("core")
R._notify_referrer_approved = lambda *a, **k: None   # no email in the harness


def seed_ref(status="commission_pending"):
    fake.store[("core", "referral")] = [
        {"org_id": ORG, "id": "R1", "referral_no": "REF-1", "status": status, "store_code": "S1",
         "referrer_name": "Ann", "customer_name": "Bob", "created_by": "REP1"}]
    fake.store[("core", "referral_config")] = []   # -> resolve_config defaults


# A: decide via the UNIFIED INBOX → the referral flips to approved with a booked amount + payout date.
fake.store.clear(); seed_ref()
rr = engine.create_request(ORG, type="referral", title="Referral commission — Ann",
                           source_table="referral", source_id="R1", store_code="S1")
check("referral: request registered against the source row", rr.get("status") == "pending" and rr.get("id"), rr)
engine.decide(ORG, rr["id"], decision="approve", actor="mgr@x")
ref = fake.store[("core", "referral")][0]
default_amt = RC.compute_commission({"status": "commission_pending"}, RC.resolve_config({}))
check("referral: an inbox APPROVE moves commission_pending -> approved", ref["status"] == "approved", ref)
check("referral: ...and books the default commission_amount onto the referral",
      ref.get("commission_amount") == default_amt and ref.get("payout_date"), ref)
check("referral: ...and stamps the approval_request approved",
      engine.get_request(ORG, rr["id"])["status"] == "approved")

# A2: an inbox DENY maps to 'rejected'.
fake.store.clear(); seed_ref()
rr2 = engine.create_request(ORG, type="referral", title="reject", source_table="referral",
                            source_id="R1", store_code="S1")
engine.decide(ORG, rr2["id"], decision="deny", actor="mgr@x")
check("referral: an inbox DENY moves commission_pending -> rejected",
      fake.store[("core", "referral")][0]["status"] == "rejected")
check("referral: ...and stamps the approval_request denied",
      engine.get_request(ORG, rr2["id"])["status"] == "denied")

# B: decide via the LEGACY board (sync only) → inbox reflects it, effect not re-run by the engine.
fake.store.clear(); seed_ref()
rr3 = engine.create_request(ORG, type="referral", title="legacy", source_table="referral",
                            source_id="R1", store_code="S1")
fake.store[("core", "referral")][0].update({"status": "approved"})   # legacy endpoint already applied it
engine.sync_source_decision(ORG, type="referral", source_table="referral", source_id="R1",
                            decision="approve", actor="mgr@x")
check("referral: a legacy-board decision syncs the inbox request to approved",
      engine.get_request(ORG, rr3["id"])["status"] == "approved")

# C: idempotency — the referral already decided → inbox decide is a no-op on the row (still stamps request).
fake.store.clear(); seed_ref(status="approved")
fake.store[("core", "referral")][0]["commission_amount"] = 999.0
rr4 = engine.create_request(ORG, type="referral", title="already done", source_table="referral",
                            source_id="R1", store_code="S1")
engine.decide(ORG, rr4["id"], decision="deny", actor="mgr@x")
ref4 = fake.store[("core", "referral")][0]
check("referral: on_decide leaves an already-decided referral untouched",
      ref4["status"] == "approved" and ref4["commission_amount"] == 999.0, ref4)

# D: segregation-of-duties — the approver_predicate refuses the rep who created the referral, allows others.
fake.store.clear(); seed_ref()
_refadp = __import__("app.modules.approvals.adapters.referral", fromlist=["_approver_predicate"])
R._can_approve = lambda caller: True   # focus this test on the SoD conflict, not the RBAC tier
R._caller = lambda authz, org=None: {"employee_id": authz}   # 'authorization' carries the emp id here
predicate = engine._TYPES["referral"].approver_predicate
req_for_pred = {"org_id": ORG, "source_id": "R1"}
check("referral: SoD predicate BLOCKS the rep who created the referral",
      predicate({"authorization": "REP1", "org_id": ORG}, req_for_pred) is False)
check("referral: SoD predicate ALLOWS a different manager",
      predicate({"authorization": "MGR1", "org_id": ORG}, req_for_pred) is True)


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
