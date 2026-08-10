"""HARNESS — POS router-level membership gate vs. a super-admin administering a foreign tenant.

WHY THIS EXISTS. The 2026-08-09 fix (f4c39b2) taught `_caller_ctx` to recognise super-admin standing
from ANY membership. But `_require_pos_access` — the APIRouter-level dependency added by the
pos-schema-hardening merge (74e42b6, 21 minutes EARLIER) — calls `_require_member`, which was not
touched and still resolves membership in the ACTING org only. A router dependency runs BEFORE the
endpoint body, so for the exact caller the fix was written for (super-admin, no app_users row in the
acting tenant) `_caller_ctx` is never reached.

THE FAKE CLIENT IS NOT A STUB THAT SAYS YES. `[[fake-client-eq-noop-trap]]`: a fake `.eq()` that
no-ops tests the wrong thing. This one really filters, and the very first assertion is a NEGATIVE
CONTROL that FAILS if the filter is a no-op.

Run:  python3 harness_pos_superadmin_gate.py <path-to-worktree>
"""
import sys, os, types

WT = sys.argv[1] if len(sys.argv) > 1 else "/workspaces/wt-pos-training"
sys.path.insert(0, os.path.join(WT, "backend"))

HOUSE = "00000000-0000-0000-0000-000000000001"
LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
VZ = "f4f1c16e-2acf-4221-854a-c29a605754a7"

# Mirrors the REAL storeops.app_users rows measured live 2026-08-10 (see sbsql output in the report).
APP_USERS = [
    {"id": "u1", "org_id": HOUSE, "auth_id": "sanjot", "email": "sanjot@cellfonzrus.com",
     "role": "admin", "super_admin": True, "employee_id": None},
    {"id": "u2", "org_id": HOUSE, "auth_id": "ss", "email": "ss@1313global.us",
     "role": "admin", "super_admin": False, "employee_id": "E039"},
    {"id": "u3", "org_id": LUX, "auth_id": "ss", "email": "ss@1313global.us",
     "role": "admin", "super_admin": False, "employee_id": None},
    {"id": "u4", "org_id": VZ, "auth_id": "ss", "email": "ss@1313global.us",
     "role": "admin", "super_admin": False, "employee_id": None},
    {"id": "u5", "org_id": LUX, "auth_id": "rep", "email": "rep@lux",
     "role": "sales_rep", "super_admin": False, "employee_id": "L001"},
]
ROLES = [
    {"org_id": HOUSE, "name": "admin", "permissions": {"scope": "all"}},
    {"org_id": LUX, "name": "admin", "permissions": {"scope": "all"}},
    {"org_id": VZ, "name": "admin", "permissions": {"scope": "all"}},
    {"org_id": LUX, "name": "sales_rep", "permissions": {"scope": "self"}},
]
TABLES = {"app_users": APP_USERS, "roles": ROLES}

QUERIES = []            # every (table, filters) the code under test issued — proves what it asked


class _Q:
    def __init__(self, table):
        self.table = table
        self.filters = []

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def limit(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        rows = TABLES.get(self.table, [])
        for col, val in self.filters:          # REAL filtering — see the negative control below
            rows = [r for r in rows if r.get(col) == val]
        QUERIES.append((self.table, list(self.filters), len(rows)))
        return types.SimpleNamespace(data=[dict(r) for r in rows])


class _Schema:
    def table(self, name):
        return _Q(name)


class FakeClient:
    def schema(self, _name):
        return _Schema()


def install(monkey_uid):
    import app.modules.pos.router as R
    import app.modules.core.router as CR
    R.sb = lambda: FakeClient()
    CR._uid_from_token = lambda auth: monkey_uid
    return R


FAILS = []


def check(name, cond, detail=""):
    (print if cond else FAILS.append)(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        print(f"FAIL  {name}  — {detail}")


def call(fn, *a, **k):
    """Return ('ok', value) or ('http', status, detail)."""
    from fastapi import HTTPException
    try:
        return ("ok", fn(*a, **k))
    except HTTPException as e:
        return ("http", e.status_code, str(e.detail))


def main():
    # ── 0. NEGATIVE CONTROL on the fake client itself ────────────────────────────────────────────
    q = _Q("app_users").select("*").eq("org_id", LUX).eq("auth_id", "sanjot").execute()
    check("negative control: fake .eq() actually filters (sanjot has NO lux row)",
          q.data == [], f"got {q.data}")
    q2 = _Q("app_users").select("*").eq("org_id", HOUSE).eq("auth_id", "sanjot").execute()
    check("negative control: fake .eq() finds the row that DOES exist", len(q2.data) == 1)

    # ── 1. SUPER-ADMIN acting on a tenant they are not a member of ───────────────────────────────
    R = install("sanjot")
    r = call(R._require_pos_access, "Bearer x", LUX)
    check("super-admin on a FOREIGN tenant passes the router-level POS gate",
          r[0] == "ok", f"got {r}")

    ctx = R._caller_ctx("Bearer x", LUX)
    check("super-admin on a FOREIGN tenant resolves a POS context",
          bool(ctx) and ctx.get("super_admin") is True, f"got {ctx}")
    r = call(R._require_pos_perm, "Bearer x", LUX, "pos_settings")
    check("super-admin on a FOREIGN tenant may write POS settings (the sales-tax save)",
          r[0] == "ok", f"got {r}")

    # ...and on their OWN tenant, unchanged
    r = call(R._require_pos_access, "Bearer x", HOUSE)
    check("super-admin on their OWN tenant still passes", r[0] == "ok", f"got {r}")

    # ── 2. THE LEAK CONTROLS — nothing above may open a door for a non-super-admin ───────────────
    R = install("ss")
    r = call(R._require_pos_access, "Bearer x", LUX)
    check("member admin on a tenant they DO belong to passes", r[0] == "ok", f"got {r}")
    r = call(R._require_pos_perm, "Bearer x", LUX, "pos_settings")
    check("member admin may write POS settings on their own tenant", r[0] == "ok", f"got {r}")

    R = install("stranger")           # verified login, ZERO memberships anywhere
    r = call(R._require_pos_access, "Bearer x", LUX)
    check("LEAK CONTROL: a login with no membership anywhere is REFUSED (403)",
          r[0] == "http" and r[1] == 403, f"got {r}")
    check("LEAK CONTROL: _caller_ctx denies that same login",
          R._caller_ctx("Bearer x", LUX) is None)
    r = call(R._require_pos_perm, "Bearer x", LUX, "pos_settings")
    check("LEAK CONTROL: that login cannot write POS settings",
          r[0] == "http" and r[1] in (401, 403), f"got {r}")

    R = install("rep")                # member of LUX only, NOT a super-admin
    r = call(R._require_pos_access, "Bearer x", HOUSE)
    check("LEAK CONTROL: a lux rep is REFUSED on the house tenant",
          r[0] == "http" and r[1] == 403, f"got {r}")
    r = call(R._require_pos_perm, "Bearer x", LUX, "pos_settings")
    check("LEAK CONTROL: a scope='self' rep cannot write POS settings on their OWN tenant",
          r[0] == "http" and r[1] == 403, f"got {r}")

    R = install(None)                 # no/invalid token
    r = call(R._require_pos_access, "", LUX)
    check("LEAK CONTROL: no token is REFUSED (401)",
          r[0] == "http" and r[1] == 401, f"got {r}")

    # ── 3. The super-admin lookup must be by auth_id, never unfiltered ───────────────────────────
    sa_q = [q for q in QUERIES if q[0] == "app_users"
            and ("super_admin", True) in q[1]]
    check("the super-admin probe always filters on auth_id (never a bare table scan)",
          bool(sa_q) and all(any(c == "auth_id" for c, _ in q[1]) for q in sa_q),
          f"queries={sa_q}")

    print()
    print(f"{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILURES'} — {len(QUERIES)} DB reads issued")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
