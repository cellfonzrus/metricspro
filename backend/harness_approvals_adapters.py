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

# D: approver_predicate — an inbox expense decision books the P&L, so ONLY a manager who may run closing
# MANAGEMENT REVIEW may decide it (the SAME _can_mgmt_review gate the legacy board applies). Without this
# the engine's default store-scope check would let a store/market-scoped DM approve an expense in their
# span — a money privilege gap. The predicate must fail closed on a non-mgmt-review caller AND on error.
exp_predicate = engine._TYPES["closing_expense"].approver_predicate
check("closing_expense: predicate is registered (no silent store-only fallback)", callable(exp_predicate))
_saved_perms, _saved_gate = C._caller_perms, C._can_mgmt_review
C._caller_perms = lambda client, authz: {"who": authz}   # carry the caller through to the gate stub
C._can_mgmt_review = lambda perms: perms.get("who") == "MGMT"
_exp_req = {"org_id": ORG, "source_id": "E1", "store_code": "S1"}
check("closing_expense: predicate BLOCKS a store/market-scoped caller who cannot run management review",
      exp_predicate({"authorization": "DM", "org_id": ORG}, _exp_req) is False)
check("closing_expense: predicate ALLOWS a caller who may run management review",
      exp_predicate({"authorization": "MGMT", "org_id": ORG}, _exp_req) is True)
C._can_mgmt_review = lambda perms: (_ for _ in ()).throw(RuntimeError("perm lookup blew up"))
check("closing_expense: predicate FAILS CLOSED when the permission check errors",
      exp_predicate({"authorization": "MGMT", "org_id": ORG}, _exp_req) is False)
C._caller_perms, C._can_mgmt_review = _saved_perms, _saved_gate


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


# ── remediation (approve RUNS the bounded playbook → executed; reject → rejected) ─────────────────
import app.modules.remediation.router as RM  # noqa: E402

RM.get_supabase = lambda: fake
RUN_EXECUTE = []
RM.pb.run_execute = lambda key, client, org_id, params: (RUN_EXECUTE.append(key), {"ok": True})[1]


def seed_rem(status="awaiting_approval"):
    fake.store[("commcalc", "remediation_request")] = [
        {"org_id": ORG, "id": "RM1", "title": "recompute GP", "playbook_key": "recompute_gp",
         "params": {}, "status": status}]


# A: decide via the UNIFIED INBOX → approve runs the playbook and flips to executed.
fake.store.clear(); RUN_EXECUTE.clear(); seed_rem()
mr = engine.create_request(ORG, type="remediation", title="Automated fix: recompute GP",
                           source_table="remediation_request", source_id="RM1")
engine.decide(ORG, mr["id"], decision="approve", actor="ops@x")
rem = fake.store[("commcalc", "remediation_request")][0]
check("remediation: an inbox APPROVE runs the playbook and flips to executed",
      rem["status"] == "executed" and RUN_EXECUTE == ["recompute_gp"], (rem, RUN_EXECUTE))
check("remediation: ...and stamps the approval_request approved",
      engine.get_request(ORG, mr["id"])["status"] == "approved")

# A2: an inbox DENY maps to reject (no playbook run).
fake.store.clear(); RUN_EXECUTE.clear(); seed_rem()
mr2 = engine.create_request(ORG, type="remediation", title="reject",
                            source_table="remediation_request", source_id="RM1")
engine.decide(ORG, mr2["id"], decision="deny", actor="ops@x")
check("remediation: an inbox DENY flips to rejected and runs NO playbook",
      fake.store[("commcalc", "remediation_request")][0]["status"] == "rejected" and RUN_EXECUTE == [])
check("remediation: ...and stamps the approval_request denied",
      engine.get_request(ORG, mr2["id"])["status"] == "denied")

# B: decide via the LEGACY board (sync only) → inbox reflects it.
fake.store.clear(); RUN_EXECUTE.clear(); seed_rem()
mr3 = engine.create_request(ORG, type="remediation", title="legacy",
                            source_table="remediation_request", source_id="RM1")
fake.store[("commcalc", "remediation_request")][0].update({"status": "executed"})
engine.sync_source_decision(ORG, type="remediation", source_table="remediation_request", source_id="RM1",
                            decision="approve", actor="ops@x")
check("remediation: a legacy-board decision syncs the inbox request to approved",
      engine.get_request(ORG, mr3["id"])["status"] == "approved")

# C: idempotency — already executed → inbox decide is a no-op on the row + no playbook run.
fake.store.clear(); RUN_EXECUTE.clear(); seed_rem(status="executed")
mr4 = engine.create_request(ORG, type="remediation", title="already done",
                            source_table="remediation_request", source_id="RM1")
engine.decide(ORG, mr4["id"], decision="approve", actor="ops@x")
check("remediation: on_decide leaves an already-executed request untouched (no re-run)",
      fake.store[("commcalc", "remediation_request")][0]["status"] == "executed" and RUN_EXECUTE == [])


# ── payroll_hours (INTIMATION-ONLY: two-stage board owns the decision; inbox is read-only) ────────
import app.modules.storeops.payroll_approval as PA  # noqa: E402
from datetime import date as _date  # noqa: E402

_s, _e = _date(2026, 7, 23), _date(2026, 8, 5)

# DM approval opens the HR-release request (pending) in the unified inbox.
fake.store.clear()
PA._intimate_payroll_decision(ORG, _s, _e, "E9", "Zed", "S1", "PA1", "dm", "approve")
prs = [r for r in fake.store.get(("storeops", "approval_requests"), []) if r.get("type") == "payroll_hours"]
check("payroll_hours: a DM approval opens a pending HR-release request in the inbox",
      len(prs) == 1 and prs[0]["status"] == "pending", prs)
pr_id = prs[0]["id"]

# HR approval syncs the request to approved (the board applied the real effect itself).
PA._intimate_payroll_decision(ORG, _s, _e, "E9", "Zed", "S1", "PA1", "hr", "approve")
check("payroll_hours: an HR approval syncs the inbox request to approved",
      engine.get_request(ORG, pr_id)["status"] == "approved")

# HR send-back syncs a fresh request to denied.
fake.store.clear()
PA._intimate_payroll_decision(ORG, _s, _e, "E8", "Yan", "S1", "PA2", "dm", "approve")
pr2 = [r for r in fake.store[("storeops", "approval_requests")] if r.get("source_id") == "PA2"][0]
PA._intimate_payroll_decision(ORG, _s, _e, "E8", "Yan", "S1", "PA2", "hr", "send_back")
check("payroll_hours: an HR send-back syncs the inbox request to denied",
      engine.get_request(ORG, pr2["id"])["status"] == "denied")

# The inbox can NEVER decide a payroll_hours request: predicate blocks it AND on_decide would raise.
check("payroll_hours: approver_predicate blocks any inbox decision",
      engine._TYPES["payroll_hours"].approver_predicate({"authorization": "", "org_id": ORG}, pr2) is False)
fake.store.clear()
PA._intimate_payroll_decision(ORG, _s, _e, "E7", "Xio", "S1", "PA3", "dm", "approve")
pr3 = [r for r in fake.store[("storeops", "approval_requests")] if r.get("source_id") == "PA3"][0]
_raised = False
try:
    engine.decide(ORG, pr3["id"], decision="approve", actor="hr@x")
except Exception:
    _raised = True
check("payroll_hours: forcing engine.decide raises and leaves the request pending (no silent apply)",
      _raised and engine.get_request(ORG, pr3["id"])["status"] == "pending")


# ── management_incentive (INTIMATION-ONLY: multi-state ledger owns the decision) ──────────────────
import app.modules.commcalc.router as CC  # noqa: E402

# A draft payout intimates a pending request in the inbox.
fake.store.clear()
CC._intimate_mi_payout(ORG, {"id": "MI1", "status": "draft", "employee_name": "Meg",
                             "employee_id": "E1", "period": "2026-07", "total": 1200.0}, ["S1"])
mis = fake.store.get(("storeops", "approval_requests"), [])
check("management_incentive: a saved draft payout intimates a pending request",
      len(mis) == 1 and mis[0]["status"] == "pending" and mis[0]["type"] == "management_incentive", mis)
mi_id = mis[0]["id"]

# Approve on the board syncs the inbox request to approved.
engine.sync_source_decision(ORG, type="management_incentive",
                            source_table="management_incentive_payout", source_id="MI1",
                            decision="approve", actor="hr@x")
check("management_incentive: a board approve syncs the inbox request to approved",
      engine.get_request(ORG, mi_id)["status"] == "approved")

# The inbox can never decide it: predicate blocks + a forced decide raises.
check("management_incentive: approver_predicate blocks any inbox decision",
      engine._TYPES["management_incentive"].approver_predicate({"authorization": "", "org_id": ORG}, mis[0]) is False)
fake.store.clear()
CC._intimate_mi_payout(ORG, {"id": "MI2", "status": "draft", "employee_name": "Meg",
                             "employee_id": "E1", "period": "2026-08", "total": 900.0}, ["S1"])
mi2 = fake.store[("storeops", "approval_requests")][0]
_raised = False
try:
    engine.decide(ORG, mi2["id"], decision="approve", actor="hr@x")
except Exception:
    _raised = True
check("management_incentive: forcing engine.decide raises and leaves the request pending",
      _raised and engine.get_request(ORG, mi2["id"])["status"] == "pending")


# ── ingest_guard (INTIMATION-ONLY: guard board owns allow/reject incl. the store-alias pick) ──────
import app.modules.commcalc.ingest_store_guard as ISG  # noqa: E402

# A recorded flag intimates a pending request in the inbox.
fake.store.clear()
ISG._intimate_quarantine(ORG, [{"id": "Q1", "status": "pending", "store_raw": "FOREIGN-STORE",
                                "target_table": "raw_sales", "rows_withheld": 12, "amount_seen": 3400.0}])
igs = fake.store.get(("storeops", "approval_requests"), [])
check("ingest_guard: a recorded flag intimates a pending request",
      len(igs) == 1 and igs[0]["status"] == "pending" and igs[0]["type"] == "ingest_guard", igs)
ig_id = igs[0]["id"]

# A board 'allow' syncs the inbox request to approved; 'reject' would sync to denied.
engine.sync_source_decision(ORG, type="ingest_guard", source_table="ingest_store_quarantine",
                            source_id="Q1", decision="approve", actor="ops@x")
check("ingest_guard: a board allow syncs the inbox request to approved",
      engine.get_request(ORG, ig_id)["status"] == "approved")

# Inbox can never decide it: predicate blocks + a forced decide raises.
check("ingest_guard: approver_predicate blocks any inbox decision",
      engine._TYPES["ingest_guard"].approver_predicate({"authorization": "", "org_id": ORG}, igs[0]) is False)
fake.store.clear()
ISG._intimate_quarantine(ORG, [{"id": "Q2", "status": "pending", "store_raw": "FOREIGN2",
                                "target_table": "raw_sales", "rows_withheld": 3, "amount_seen": 100.0}])
ig2 = fake.store[("storeops", "approval_requests")][0]
_raised = False
try:
    engine.decide(ORG, ig2["id"], decision="approve", actor="ops@x")
except Exception:
    _raised = True
check("ingest_guard: forcing engine.decide raises and leaves the request pending",
      _raised and engine.get_request(ORG, ig2["id"])["status"] == "pending")


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
