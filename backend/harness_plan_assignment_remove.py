"""PROOF — THE single-assignment remover: `DELETE /commcalc/commission-plans/{plan_id}/assignments/{assignment_id}`
removes exactly ONE commission_plan_assignment row, of that plan, of that org, and nothing else (index §6n / §17).

Why it exists (owner 2026-09-28): the stray employee-scope assignment with scope_value NULL on the "Flat
Comission" plan had to go, and the only writer was the plan save, which DELETES AND RE-INSERTS every rule / tier
/ assignment of the plan. This harness runs the REAL handler (`router.delete_commission_plan_assignment` →
`_remove_plan_assignment`) over an in-memory client that records every write.

  A. it deletes the one row (the NULL-scope employee row of the fixture) and returns it;
  B. every other row of every table is byte-identical afterwards — the plan's other assignments, its rules
     (same ids), its tiers, the other plan, the other org;
  C. exactly ONE delete call, filtered on org_id AND plan_id AND id; no insert, no update;
  D. it refuses — 404, zero writes — a row of ANOTHER ORG, a row of ANOTHER PLAN, an unknown id, a malformed id,
     and the same row twice;
  E. the gate: the real `_require_commission_plans_edit` → core `_can_edit_setting(caller, 'commission_plans')`
     — a rep without the grant is refused 403 BEFORE any read or write; an admin / a grant passes; an
     unresolvable caller degrades open (the house posture); the acting org is the one resolved;
  F. the config memo is dropped for THIS org on success, not on a refusal.

    python3 backend/harness_plan_assignment_remove.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for _k in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "DATABASE_URL"):
    os.environ.pop(_k, None)

from fastapi import HTTPException                                      # noqa: E402
from app.modules.commcalc import router as R                           # noqa: E402
from app.modules.core import router as core                            # noqa: E402

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:400]))


def section(t):
    print("\n── %s %s" % (t, "─" * max(0, 96 - len(t))))


# ── an in-memory client that APPLIES deletes and RECORDS every write ─────────────────────────────
class Q:
    def __init__(self, db, key, log):
        self.db, self.key, self.log = db, key, log
        self.filters, self.op, self.lim = [], "select", None

    def select(self, *a, **k):
        return self

    def delete(self):
        self.op = "delete"
        return self

    def insert(self, rows, **k):
        self.op, self.payload = "insert", rows
        return self

    def update(self, row, **k):
        self.op, self.payload = "update", row
        return self

    def upsert(self, rows, **k):
        self.op, self.payload = "upsert", rows
        return self

    def eq(self, c, v):
        self.filters.append((c, str(v)))
        return self

    def limit(self, n):
        self.lim = n
        return self

    def _match(self, r):
        return all(str(r.get(c)) == v for c, v in self.filters)

    def execute(self):
        rows = self.db.setdefault(self.key, [])
        hit = [r for r in rows if self._match(r)]
        if self.op == "select":
            self.log.append(("select", self.key, tuple(self.filters)))
            return type("Res", (), {"data": copy.deepcopy(hit[: self.lim] if self.lim else hit)})()
        self.log.append((self.op, self.key, tuple(self.filters)))
        if self.op == "delete":
            self.db[self.key] = [r for r in rows if not self._match(r)]
            return type("Res", (), {"data": copy.deepcopy(hit)})()
        raise AssertionError("the remover must never %s" % self.op)


class Client:
    def __init__(self, db):
        self.db, self.log = db, []

    def schema(self, n):
        c = self

        class S:
            def table(self_, name):
                return Q(c.db, n + "." + name, c.log)
        return S()


ORG_A = "00000000-0000-0000-0000-00000000aaaa"
ORG_B = "00000000-0000-0000-0000-00000000bbbb"
PLAN_FLAT = "11111111-1111-1111-1111-111111111111"     # "Flat Comission" of org A
PLAN_OTHER = "22222222-2222-2222-2222-222222222222"    # another plan of org A
PLAN_B = "33333333-3333-3333-3333-333333333333"        # org B's plan
A_STRAY = "aaaaaaaa-0000-0000-0000-000000000001"       # employee scope, scope_value NULL — the row to remove
A_STORE = "aaaaaaaa-0000-0000-0000-000000000002"
A_EMP = "aaaaaaaa-0000-0000-0000-000000000003"
A_OTHER = "aaaaaaaa-0000-0000-0000-000000000004"       # on PLAN_OTHER
B_ROW = "bbbbbbbb-0000-0000-0000-000000000001"         # org B


def fixture():
    return {
        "commcalc.commission_plan": [
            {"id": PLAN_FLAT, "org_id": ORG_A, "name": "Flat Comission"},
            {"id": PLAN_OTHER, "org_id": ORG_A, "name": "Other"},
            {"id": PLAN_B, "org_id": ORG_B, "name": "Flat Comission"}],
        "commcalc.commission_rule": [
            {"id": "r1", "org_id": ORG_A, "plan_id": PLAN_FLAT, "label": "New", "amount": 10, "unit_basis": "per_event"},
            {"id": "r2", "org_id": ORG_A, "plan_id": PLAN_FLAT, "label": "Upg", "amount": 5, "applies_scope_kind": "store"},
            {"id": "r3", "org_id": ORG_B, "plan_id": PLAN_B, "label": "New", "amount": 10}],
        "commcalc.commission_tier": [
            {"id": "t1", "org_id": ORG_A, "plan_id": PLAN_FLAT, "min_count": 10, "multiplier": 1.5}],
        "commcalc.commission_plan_assignment": [
            {"id": A_STRAY, "org_id": ORG_A, "plan_id": PLAN_FLAT, "scope": "employee", "scope_value": None, "priority": 0},
            {"id": A_STORE, "org_id": ORG_A, "plan_id": PLAN_FLAT, "scope": "store", "scope_value": "S1", "priority": 0},
            {"id": A_EMP, "org_id": ORG_A, "plan_id": PLAN_FLAT, "scope": "employee", "scope_value": "Rep A", "priority": 1},
            {"id": A_OTHER, "org_id": ORG_A, "plan_id": PLAN_OTHER, "scope": "default", "scope_value": None, "priority": 0},
            {"id": B_ROW, "org_id": ORG_B, "plan_id": PLAN_B, "scope": "employee", "scope_value": None, "priority": 0}],
    }


invalidated = []
R._invalidate_accessory_config = lambda org_id: invalidated.append(org_id)
core._uid_from_token = lambda auth: None               # default: unresolvable caller (degrade open, house posture)


def run(db, plan_id, assignment_id, org_id=ORG_A, auth=""):
    """call the REAL route handler over `db`; returns (status, body, client)"""
    c = Client(db)
    R.sb = lambda: c
    try:
        return 200, R.delete_commission_plan_assignment(plan_id, assignment_id, authorization=auth, org_id=org_id), c
    except HTTPException as e:
        return e.status_code, e.detail, c


def writes(c):
    return [x for x in c.log if x[0] != "select"]


# ── A / B / C — the one row goes, nothing else moves ────────────────────────────────────────────────
section("A. it deletes the one row and returns it")
db = fixture()
before = copy.deepcopy(db)
st, body, c = run(db, PLAN_FLAT, A_STRAY)
check("200 and the removed row is returned", st == 200 and body["deleted"]["id"] == A_STRAY, (st, body))
check("the returned row is the stray NULL-scope employee row",
      body["deleted"]["scope"] == "employee" and body["deleted"]["scope_value"] is None, body)
check("the row is gone", not [r for r in db["commcalc.commission_plan_assignment"] if r["id"] == A_STRAY])

section("B. every other row of every table is unchanged")
exp = copy.deepcopy(before)
exp["commcalc.commission_plan_assignment"] = [r for r in exp["commcalc.commission_plan_assignment"] if r["id"] != A_STRAY]
for k in sorted(before):
    check("%s identical apart from the one row" % k, db[k] == exp[k], (db[k], exp[k]))
check("the plan's rules keep their ids (no delete-then-insert)", [r["id"] for r in db["commcalc.commission_rule"]] == ["r1", "r2", "r3"])
check("4 of 5 assignments remain", len(db["commcalc.commission_plan_assignment"]) == 4)

section("C. exactly one write: a delete filtered on org + plan + id")
w = writes(c)
check("one write in total", len(w) == 1, w)
check("it is a delete of commission_plan_assignment",
      w and w[0][0] == "delete" and w[0][1] == "commcalc.commission_plan_assignment", w)
check("filtered on org_id AND plan_id AND id",
      w and dict(w[0][2]) == {"org_id": ORG_A, "plan_id": PLAN_FLAT, "id": A_STRAY}, w)
check("every read was org-scoped", all(dict(x[2]).get("org_id") == ORG_A for x in c.log), c.log)

# ── D — refusals: 404 and zero writes ────────────────────────────────────────────────────────────
section("D. it refuses cross-org, cross-plan, unknown, malformed and repeated ids — 404, zero writes")
for label, args in (("a row of ANOTHER ORG (org B's plan + row, asked as org A)", (PLAN_B, B_ROW, ORG_A)),
                    ("org B's row under org A's plan id", (PLAN_FLAT, B_ROW, ORG_A)),
                    ("a row of ANOTHER PLAN of the same org", (PLAN_FLAT, A_OTHER, ORG_A)),
                    ("an unknown id", (PLAN_FLAT, "aaaaaaaa-0000-0000-0000-00000000ffff", ORG_A)),
                    ("a malformed id", (PLAN_FLAT, "1 or 1=1", ORG_A)),
                    ("a malformed plan id", ("*", A_STORE, ORG_A)),
                    ("the same row twice (already removed)", (PLAN_FLAT, A_STRAY, ORG_A))):
    snap = copy.deepcopy(db)
    n_inv = len(invalidated)
    st, body, c = run(db, args[0], args[1], org_id=args[2])
    check("%s → 404" % label, st == 404, (st, body))
    check("%s → zero writes, every table unchanged" % label, not writes(c) and db == snap, writes(c))
    check("%s → the config memo is not dropped" % label, len(invalidated) == n_inv)
st, body, c = run(fixture(), PLAN_B, B_ROW, org_id=ORG_B)
check("the SAME org-B row asked as org B → removed (the refusal was the org, not the row)",
      st == 200 and body["deleted"]["id"] == B_ROW, (st, body))

# ── E — the gate ─────────────────────────────────────────────────────────────────────────────────
section("E. the gate: 'Commission Plans & Payout Schedules' (core._can_edit_setting), acting org")
core._uid_from_token = lambda auth: ("uid" if auth else None)
seen = {}


def as_caller(caller):
    def _res(client, uid, active_org=None):
        seen["org"] = active_org
        return caller
    core._resolve_caller = _res
    db = fixture()
    snap = copy.deepcopy(db)
    st, body, c = run(db, PLAN_FLAT, A_STRAY, auth="Bearer x")
    return st, c, db == snap


st, c, same = as_caller({"perms": {}, "role": "rep"})
check("a rep without the grant → 403", st == 403, st)
check("… refused BEFORE any read or write", not c.log and same, c.log)
check("the caller was resolved for the ACTING org", seen.get("org") == ORG_A, seen)
st, c, same = as_caller({"perms": {"settings": {"commission_plans": False}}, "role": "admin"})
check("an explicit DENY beats the admin role → 403", st == 403, st)
st, c, same = as_caller({"perms": {"settings": {"commission_plans": True}}, "role": "manager"})
check("an explicit grant → 200", st == 200, st)
st, c, same = as_caller({"perms": {}, "role": "admin"})
check("an admin → 200", st == 200, st)
core._uid_from_token = lambda auth: None
st, body, c = run(fixture(), PLAN_FLAT, A_STRAY)
check("no token (RBAC off / automation) → degrade open, per the house posture", st == 200, st)

# ── F — the memo ─────────────────────────────────────────────────────────────────────────────────
section("F. the config memo is dropped for THIS org on success")
invalidated.clear()
st, body, c = run(fixture(), PLAN_FLAT, A_STORE)
check("one invalidate, for org A", st == 200 and invalidated == [ORG_A], invalidated)

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — one row of one plan of one org, nothing else; refusals write nothing.")
