"""Offline proof (no live DB/network) for the Unified Approvals Engine (migration 867). Runs the REAL
engine against an in-memory fake Supabase client, proving:

  1. CREATE + IDEMPOTENCY — create_request inserts a pending request + a 'created' event; a second call
     for the same (type, source) returns the SAME row (no duplicate, no second notification).
  2. DECIDE runs the registered handler + stamps status + writes an audit event; approve vs deny.
  3. GUARDS — deciding an unknown or already-decided request raises ValueError; a bad decision too.
  4. HANDLER FAILURE aborts — if the type's on_decide raises, the request stays pending (nothing
     half-applied).
  5. SYNC — sync_source_decision reflects a legacy per-module decision onto the request WITHOUT
     re-running the handler.
  6. SCOPE — list_inbox hides out-of-span rows from a store-scoped caller; an admin (unrestricted) sees
     all; summary counts pending in-scope.

Run: `python3 harness_approvals_engine.py` from backend/.
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
                r = dict(p)
                r.setdefault("id", f"{self.key[1]}-{len(rows) + len(out) + 1}")
                out.append(r)
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

    def schema(self, _name):
        return FakeSchema(self.store)


fake = FakeClient()

from app.modules.approvals import engine  # noqa: E402

engine._sb = lambda: FakeSchema(fake.store)
engine._notify_approvers = lambda *a, **k: None   # deterministic + network-free

ORG = "org-a"

# A test approval type whose handler records the calls it receives (proves dispatch + no double-run).
HANDLER_CALLS = []


@engine.register_type("unit_test", label="Unit test")
def _on_decide(request, decision, actor, note):
    HANDLER_CALLS.append((request.get("id"), decision, actor))


BOOM_CALLS = []


@engine.register_type("boom", label="Boom")
def _boom(request, decision, actor, note):
    BOOM_CALLS.append(request.get("id"))
    raise RuntimeError("handler blew up")


def reqs():
    return fake.store.get(("storeops", "approval_requests"), [])


def events(rid=None):
    ev = fake.store.get(("storeops", "approval_events"), [])
    return [e for e in ev if rid is None or e.get("request_id") == rid]


# ── 1: CREATE + IDEMPOTENCY ───────────────────────────────────────────────────────────────────
fake.store.clear(); HANDLER_CALLS.clear()
r1 = engine.create_request(ORG, type="unit_test", title="Please approve X",
                           source_table="widget", source_id="W1", store_code="S1")
check("1a create returns a pending request with an id", r1.get("status") == "pending" and r1.get("id"), r1)
check("1b a 'created' audit event was written", len(events(r1["id"])) == 1
      and events(r1["id"])[0]["event_type"] == "created", events(r1["id"]))
r1b = engine.create_request(ORG, type="unit_test", title="dup", source_table="widget", source_id="W1",
                            store_code="S1")
check("1c a second create for the same source returns the SAME row (idempotent, no dup)",
      r1b.get("id") == r1["id"] and len(reqs()) == 1, (r1b, len(reqs())))


# ── 2: DECIDE (approve) runs the handler + stamps status + audits ──────────────────────────────
out = engine.decide(ORG, r1["id"], decision="approve", actor="dm@x", note="ok")
check("2a decide runs the registered handler exactly once", HANDLER_CALLS == [(r1["id"], "approve", "dm@x")], HANDLER_CALLS)
check("2b request is stamped approved with decider + note",
      out["status"] == "approved" and out["decided_by"] == "dm@x" and out["decision_note"] == "ok", out)
check("2c an 'approved' audit event was written", any(e["event_type"] == "approved" for e in events(r1["id"])), events(r1["id"]))


# ── 3: GUARDS ─────────────────────────────────────────────────────────────────────────────────
def raises(fn):
    try:
        fn(); return False
    except ValueError:
        return True
    except Exception:
        return False


check("3a deciding an already-decided request raises", raises(lambda: engine.decide(ORG, r1["id"], decision="approve")))
check("3b deciding an unknown request raises", raises(lambda: engine.decide(ORG, "nope", decision="approve")))
check("3c a bad decision verb raises", raises(lambda: engine.decide(ORG, r1["id"], decision="maybe")))


# ── 4: HANDLER FAILURE aborts (request stays pending) ──────────────────────────────────────────
fake.store.clear(); BOOM_CALLS.clear()
rb = engine.create_request(ORG, type="boom", title="will fail", source_table="b", source_id="B1", store_code="S1")
threw = False
try:
    engine.decide(ORG, rb["id"], decision="approve", actor="dm@x")
except RuntimeError:
    threw = True
still = engine.get_request(ORG, rb["id"])
check("4a a handler that raises propagates (endpoint maps to 400)", threw, threw)
check("4b ...and the request STAYS pending (nothing half-applied)", still.get("status") == "pending", still)


# ── 5: SYNC (legacy decision reflected without re-running the handler) ──────────────────────────
fake.store.clear(); HANDLER_CALLS.clear()
rs = engine.create_request(ORG, type="unit_test", title="legacy", source_table="widget", source_id="W9", store_code="S1")
engine.sync_source_decision(ORG, type="unit_test", source_table="widget", source_id="W9",
                            decision="deny", actor="mgr@x", note="legacy board")
after = engine.get_request(ORG, rs["id"])
check("5a sync flips the linked request to the module's decision", after["status"] == "denied", after)
check("5b ...WITHOUT re-running the type handler", HANDLER_CALLS == [], HANDLER_CALLS)
check("5c ...and records an audit event tagged via the module endpoint",
      any(e.get("detail", {}).get("via") == "module_endpoint" for e in events(rs["id"])), events(rs["id"]))


# ── 6: SCOPE (list_inbox + summary) ─────────────────────────────────────────────────────────────
fake.store.clear()
engine.create_request(ORG, type="unit_test", title="at S1", source_table="w", source_id="a", store_code="S1")
engine.create_request(ORG, type="unit_test", title="at S2", source_table="w", source_id="b", store_code="S2")
engine.create_request(ORG, type="unit_test", title="org-level", source_table="w", source_id="c", store_code=None)

# Admin (unrestricted): sees everything, org-level included.
engine._scope_keyset = lambda auth, org: None
admin_inbox = engine.list_inbox(ORG, authorization="admin")
check("6a admin (unrestricted) sees all pending incl. org-level", len(admin_inbox) == 3, len(admin_inbox))
check("6b admin summary counts all pending", engine.summary(ORG, authorization="admin")["pending"] == 3)

# Store-scoped to S1: sees only S1 (org-level store-less rows are admin-only).
engine._scope_keyset = lambda auth, org: {"S1"}
s1_inbox = engine.list_inbox(ORG, authorization="s1mgr")
check("6c a store-scoped caller sees only their store's requests",
      [r["title"] for r in s1_inbox] == ["at S1"], [r["title"] for r in s1_inbox])
check("6d ...and never the org-level (store-less) request (that's admin-only)",
      all(r["title"] != "org-level" for r in s1_inbox), s1_inbox)


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
