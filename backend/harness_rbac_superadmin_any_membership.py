"""Proof harness — `core._resolve_caller` resolves super_admin with the LOGIN-LEVEL (ANY-membership)
rule, matching `_require_super_admin` and `tenant_middleware._resolve_identity`.

Runs the ACTUAL shipped `app.modules.core.router._resolve_caller` / `_can_edit_setting` /
`_can_view_failures` against a fake Supabase client (same convention as
harness_privesc_rbac_gates.py / harness_core_bootstrap.py) — no DB, no network. Run from backend/:

    python3 harness_rbac_superadmin_any_membership.py

THE BUG (live 2026-08-06): two definitions of "super-admin" coexisted.
  • CANONICAL — `_require_super_admin` (core/router.py) and `tenant_middleware._resolve_identity`:
    `any(r.get("super_admin") for r in rows)` — a LOGIN-level key across all memberships.
  • DRIFTED — `_resolve_caller`: `bool(u.get("super_admin"))` off the ONE acting membership row.
Every `_can_edit_setting` consumer (all SETTING_AREAS write gates, in EVERY module) reads
`_resolve_caller`, so a genuine super-admin whose flag sits on a different membership row than the
acting one was denied. Live symptom: the owner, signed in as super-admin, saw the read-only "ask an
administrator" banner on the Google Reviews settings page.

Proves:
  A. NEGATIVE CONTROL — the pre-fix expression (`bool(acting_row.super_admin)`), evaluated on the
     same rows, REPRODUCES the denial; the shipped function does not. The fix is exactly what closes
     the hole (and the control fails loudly if someone reverts it).
  B. THE FIX — super_admin is true whenever the flag is on ANY membership row, for every
     (flag-position × x-active-org) combination: flag on the default/house row while acting
     elsewhere, flag on a non-default row, flag on the acting row, flag on several rows.
  C. NON-REGRESSION, exhaustive — for EVERY caller with NO super_admin flag anywhere, the resolved
     dict is byte-identical to the pre-fix implementation, across the full matrix of membership
     shapes × active_org values. No non-super-admin gains a single thing.
  D. ORG/ROLE/PERMS UNTOUCHED — org_id, role and perms still come from the ACTING row under the
     unchanged `pick_membership` rule (untrusted x-active-org honored only when it names one of the
     login's own memberships), for supers and non-supers alike. Only the `super_admin` key moves.
  E. THREE-WAY AGREEMENT — for the same membership rows, `_resolve_caller`, `_require_super_admin`
     and the middleware's identity rule now return the SAME super-admin verdict (they disagreed on
     the drifted shapes before).
  F. DOWNSTREAM — `_can_edit_setting` now returns True for the drifted super-admin on EVERY
     registered SETTING_AREA (incl. 'google_reviews', the live symptom) and on an UNREGISTERED key
     (the degrade path storeops relied on before mig-less registration); `_can_view_failures` too.
     A scope='self' sales_rep in the same tenant still gets False everywhere.
  G. SUPER-ADMIN PATH STILL WORKS — a real super-admin acting as a NON-HOUSE tenant keeps
     org_id = that tenant (no org leak from the flag row), and `put_tenant_settings`'s
     "super-admin may target another org" branch is reachable for them (it was silently dead).
"""
import copy
import os
import sys

sys.path.insert(0, ".")
os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")
os.environ.setdefault("SUPABASE_KEY", "harness-dummy-anon-key")

import app.modules.core.router as rt                                    # noqa: E402
from app.modules.core.membership import list_memberships, pick_membership  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
LUX = "00000000-0000-0000-0000-0000000000ff"
THIRD = "00000000-0000-0000-0000-0000000000aa"
UID = "auth-uid-1"


# ── fake supabase client (filters honored; only the two tables _resolve_caller touches) ───────────
class FakeExec:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, rows, table):
        self.rows, self.table = rows, table
        self.filters = {}
        self._limit = None

    def select(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def execute(self):
        out = [r for r in self.rows.get(self.table, [])
               if all(r.get(c) == v for c, v in self.filters.items())]
        if self._limit is not None:
            out = out[:self._limit]
        return FakeExec(copy.deepcopy(out))


class FakeSchema:
    def __init__(self, client):
        self.client = client

    def table(self, name):
        return FakeQuery(self.client.rows, name)


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def schema(self, s):
        return FakeSchema(self)


ROLE_PERMS = {
    ("admin", HOUSE):       {"scope": "all", "modules": {"admin": True}},
    ("admin", LUX):         {"scope": "all", "modules": {"admin": True}},
    ("admin", THIRD):       {"scope": "all", "modules": {"admin": True}},
    ("sales_rep", HOUSE):   {"scope": "self"},
    ("sales_rep", LUX):     {"scope": "self"},
    ("sales_rep", THIRD):   {"scope": "self"},
    # a NON-full-scope leader role: no settings grant, scope != 'all' → _can_edit_setting says NO
    # unless the caller is a super-admin. This is the acting-row shape that produced the live banner.
    ("market_manager", LUX): {"scope": "market"},
    # a role that is explicitly DENIED google_reviews — used to prove precedence is untouched
    ("store_manager", LUX): {"scope": "store", "settings": {"google_reviews": False}},
}


def client_for(rows):
    roles = [{"org_id": o, "name": n, "permissions": p} for (n, o), p in ROLE_PERMS.items()]
    return FakeClient({"app_users": rows, "roles": roles})


def member(org, role, super_admin=False, is_default=False, uid=UID):
    return {"id": f"row-{org[-2:]}-{role}", "auth_id": uid, "org_id": org, "role": role,
            "super_admin": super_admin, "is_default_org": is_default, "created_at": f"2026-01-0{1}"}


# ── the PRE-FIX implementation, kept verbatim as the negative control ─────────────────────────────
def resolve_caller_PREFIX(client, uid, active_org=None):
    """Byte-for-byte the shipped-on-origin/main body of _resolve_caller (super_admin off the ONE
    acting row). Only here, so every 'the fix changed something' claim has a live counter-example."""
    u = pick_membership(list_memberships(client, uid), (active_org or "").strip() or None)
    if not u:
        return None
    org_id = u.get("org_id") or rt.ORG_ID
    perms = {}
    if u.get("role"):
        rr = (client.schema("storeops").table("roles").select("permissions")
              .eq("org_id", org_id).eq("name", u["role"]).limit(1).execute().data) or []
        if rr:
            perms = rr[0].get("permissions") or {}
    return {"org_id": org_id, "role": u.get("role"), "super_admin": bool(u.get("super_admin")),
            "perms": perms}


def middleware_rule(rows):
    """tenant_middleware._resolve_identity's super-admin element, quoted."""
    return any(r.get("super_admin") for r in rows)


def require_super_admin_rule(rows):
    """_require_super_admin's super-admin element (its house-admin BOOTSTRAP fallback is separate and
    deliberately NOT part of this comparison — it is a different, additional door)."""
    return any(r.get("super_admin") for r in rows)


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# THE DRIFTED SHAPES — super_admin stamped on the HOUSE row while the request acts as another tenant.
#   DRIFTED       = acting row is NOT a full-scope admin  → the drift is VISIBLE (the live banner).
#   DRIFTED_MASK  = acting row IS a full-scope admin      → the drift is MASKED by the scope='all'
#                   fallback in _can_edit_setting. This is why the bug survived so long: it only
#                   surfaces for a super-admin acting as a tenant where their own row is not admin.
# ═════════════════════════════════════════════════════════════════════════════════════════════════
DRIFTED = [member(HOUSE, "admin", super_admin=True, is_default=True),
           member(LUX, "market_manager", super_admin=False)]
DRIFTED_MASK = [member(HOUSE, "admin", super_admin=True, is_default=True),
                member(LUX, "admin", super_admin=False)]

print("── (A) NEGATIVE CONTROL: the pre-fix expression reproduces the denial ──")
c = client_for(DRIFTED)
pre = resolve_caller_PREFIX(c, UID, LUX)
post = rt._resolve_caller(c, UID, LUX)
check("A1. pre-fix _resolve_caller returns super_admin=False for a REAL super-admin (the bug)",
      pre["super_admin"] is False, str(pre))
check("A2. pre-fix _can_edit_setting('google_reviews') DENIES them (the read-only banner)",
      rt._can_edit_setting(pre, "google_reviews") is False)
check("A3. shipped _resolve_caller returns super_admin=True on the same rows",
      post["super_admin"] is True, str(post))
check("A4. shipped _can_edit_setting('google_reviews') ALLOWS them",
      rt._can_edit_setting(post, "google_reviews") is True)
check("A5. the ONLY key that differs pre/post is 'super_admin'",
      {k for k in set(pre) | set(post) if pre.get(k) != post.get(k)} == {"super_admin"},
      str({k: (pre.get(k), post.get(k)) for k in set(pre) | set(post) if pre.get(k) != post.get(k)}))
mpre = resolve_caller_PREFIX(client_for(DRIFTED_MASK), UID, LUX)
check("A6. WHY IT HID: when the acting row IS a full-scope admin the pre-fix caller was allowed "
      "anyway (scope='all' fallback) even though the resolved super_admin was still WRONG",
      mpre["super_admin"] is False and rt._can_edit_setting(mpre, "google_reviews") is True,
      str(mpre))
check("A7. the masked shape resolves super_admin=True after the fix too",
      rt._resolve_caller(client_for(DRIFTED_MASK), UID, LUX)["super_admin"] is True)

print()
print("── (B) THE FIX: super_admin = flag on ANY membership row, every position × active_org ──")
B_CASES = [
    ("flag on default/house row, acting as Luxelink (the live case)",
     DRIFTED, LUX),
    ("flag on default/house row, NO x-active-org (falls back to default)",
     DRIFTED, ""),
    ("flag on default/house row, x-active-org the login does NOT belong to (ignored)",
     DRIFTED, "00000000-0000-0000-0000-00000000dead"),
    ("flag on a NON-default row, acting as the default org",
     [member(HOUSE, "admin", is_default=True), member(LUX, "admin", super_admin=True)], HOUSE),
    ("flag on a NON-default row, acting as that same row",
     [member(HOUSE, "admin", is_default=True), member(LUX, "admin", super_admin=True)], LUX),
    ("flag on the acting row only (never regressed, must stay true)",
     [member(HOUSE, "admin", super_admin=True, is_default=True)], HOUSE),
    ("flag on several rows",
     [member(HOUSE, "admin", super_admin=True, is_default=True),
      member(LUX, "admin", super_admin=True)], LUX),
    ("flag on a THIRD tenant's row while acting as Luxelink",
     [member(HOUSE, "admin", is_default=True), member(LUX, "sales_rep"),
      member(THIRD, "admin", super_admin=True)], LUX),
    ("flag row's role is a plain sales_rep (the flag, not the role, is the key)",
     [member(HOUSE, "sales_rep", super_admin=True, is_default=True),
      member(LUX, "sales_rep")], LUX),
]
for label, rows, active in B_CASES:
    cl = client_for(rows)
    got = rt._resolve_caller(cl, UID, active)
    check(f"B. {label}", got is not None and got["super_admin"] is True, str(got))

print()
print("── (C) NON-REGRESSION: no super_admin flag anywhere ⇒ byte-identical to pre-fix ──")
NO_FLAG_SHAPES = [
    [member(HOUSE, "admin", is_default=True)],
    [member(LUX, "admin", is_default=True)],
    [member(LUX, "sales_rep", is_default=True)],
    [member(LUX, "store_manager", is_default=True)],
    [member(HOUSE, "admin", is_default=True), member(LUX, "sales_rep")],
    [member(HOUSE, "sales_rep"), member(LUX, "admin", is_default=True), member(THIRD, "sales_rep")],
    [member(LUX, "admin")],                      # no is_default anywhere → earliest wins
    [],                                          # unprovisioned login
]
ACTIVES = ["", HOUSE, LUX, THIRD, "00000000-0000-0000-0000-00000000dead", None, "   "]
diffs = []
for rows in NO_FLAG_SHAPES:
    for active in ACTIVES:
        cl = client_for(rows)
        a = resolve_caller_PREFIX(cl, UID, active)
        b = rt._resolve_caller(cl, UID, active)
        if a != b:
            diffs.append((rows, active, a, b))
check(f"C1. {len(NO_FLAG_SHAPES)}×{len(ACTIVES)} = {len(NO_FLAG_SHAPES) * len(ACTIVES)} no-flag "
      f"combinations are byte-identical pre/post", not diffs, str(diffs[:2]))
check("C2. an unprovisioned login still resolves to None (both)",
      rt._resolve_caller(client_for([]), UID, LUX) is None
      and resolve_caller_PREFIX(client_for([]), UID, LUX) is None)
check("C3. a falsy uid still resolves to None",
      rt._resolve_caller(client_for(DRIFTED), None, LUX) is None)
# every SETTING_AREA, every no-flag shape: the edit verdict cannot have moved
moved = []
for rows in NO_FLAG_SHAPES:
    for active in ACTIVES:
        cl = client_for(rows)
        a, b = resolve_caller_PREFIX(cl, UID, active), rt._resolve_caller(cl, UID, active)
        for area in [x["key"] for x in rt.SETTING_AREAS] + ["unregistered_key"]:
            if rt._can_edit_setting(a, area) != rt._can_edit_setting(b, area):
                moved.append((rows, active, area))
check(f"C4. _can_edit_setting verdict unchanged for every no-flag caller × all "
      f"{len(rt.SETTING_AREAS)} registered areas + 1 unregistered", not moved, str(moved[:3]))
denied = client_for([member(LUX, "store_manager", is_default=True)])
check("C5. an explicit per-role DENY (settings.google_reviews=false) still denies — precedence intact",
      rt._can_edit_setting(rt._resolve_caller(denied, UID, LUX), "google_reviews") is False)

print()
print("── (D) ORG / ROLE / PERMS keep ACTING-ROW semantics (only super_admin moved) ──")
ALL_SHAPES = NO_FLAG_SHAPES + [DRIFTED, DRIFTED_MASK,
                               [member(HOUSE, "admin", is_default=True),
                                member(LUX, "admin", super_admin=True)]]
bad = []
for rows in ALL_SHAPES:
    for active in ACTIVES:
        cl = client_for(rows)
        a, b = resolve_caller_PREFIX(cl, UID, active), rt._resolve_caller(cl, UID, active)
        if (a is None) != (b is None):
            bad.append(("nullness", rows, active))
            continue
        if a is None:
            continue
        if (a["org_id"], a["role"], a["perms"]) != (b["org_id"], b["role"], b["perms"]):
            bad.append(("org/role/perms", rows, active, a, b))
check(f"D1. org_id/role/perms identical pre/post across {len(ALL_SHAPES) * len(ACTIVES)} combinations",
      not bad, str(bad[:2]))
d = rt._resolve_caller(client_for(DRIFTED), UID, LUX)
check("D2. the drifted super-admin acting as Luxelink resolves org_id=Luxelink (NOT the flag's org)",
      d["org_id"] == LUX, str(d))
check("D3. ...and role/perms come from the Luxelink row, not the house 'admin' row",
      d["role"] == "market_manager" and d["perms"] == ROLE_PERMS[("market_manager", LUX)], str(d))
d0 = rt._resolve_caller(client_for(DRIFTED), UID, "")
check("D4. with no x-active-org they resolve to their DEFAULT org (house), unchanged",
      d0["org_id"] == HOUSE, str(d0))
mixed = [member(HOUSE, "admin", super_admin=True, is_default=True), member(LUX, "sales_rep")]
m = rt._resolve_caller(client_for(mixed), UID, LUX)
check("D5. a super-admin whose ACTING row is a sales_rep keeps role='sales_rep' + self scope "
      "(the flag does not rewrite their role)",
      m["role"] == "sales_rep" and m["perms"].get("scope") == "self" and m["super_admin"] is True,
      str(m))

print()
print("── (E) THREE-WAY AGREEMENT: _resolve_caller ≡ _require_super_admin ≡ middleware ──")
AGREE_SHAPES = ALL_SHAPES + [[member(HOUSE, "sales_rep", super_admin=True, is_default=True),
                              member(LUX, "sales_rep")]]
disagree_post, disagree_pre = [], []
for rows in AGREE_SHAPES:
    for active in ACTIVES:
        cl = client_for(rows)
        canonical = middleware_rule(rows)
        assert canonical == require_super_admin_rule(rows)
        b = rt._resolve_caller(cl, UID, active)
        a = resolve_caller_PREFIX(cl, UID, active)
        if b is not None and b["super_admin"] != canonical:
            disagree_post.append((rows, active, b["super_admin"], canonical))
        if a is not None and a["super_admin"] != canonical:
            disagree_pre.append((rows, active))
check(f"E1. shipped _resolve_caller agrees with the canonical rule on all "
      f"{len(AGREE_SHAPES) * len(ACTIVES)} combinations", not disagree_post, str(disagree_post[:2]))
check("E2. NEGATIVE CONTROL — the pre-fix version DISAGREED on the drifted shapes "
      f"({len(disagree_pre)} disagreements)", len(disagree_pre) > 0)

print()
print("── (F) DOWNSTREAM consumers: the settings gates every module shares ──")
sup = rt._resolve_caller(client_for(DRIFTED), UID, LUX)
rep = rt._resolve_caller(client_for([member(LUX, "sales_rep", is_default=True)]), UID, LUX)
all_areas = [a["key"] for a in rt.SETTING_AREAS]
check(f"F1. drifted super-admin may now edit ALL {len(all_areas)} registered SETTING_AREAS",
      all(rt._can_edit_setting(sup, a) for a in all_areas),
      str([a for a in all_areas if not rt._can_edit_setting(sup, a)]))
check("F2. ...including 'google_reviews' (the live symptom)",
      rt._can_edit_setting(sup, "google_reviews") is True)
check("F3. ...and an UNREGISTERED key still degrades OPEN for a super-admin only",
      rt._can_edit_setting(sup, "not_a_registered_area") is True
      and rt._can_edit_setting(rep, "not_a_registered_area") is False)
check("F4. a scope='self' sales_rep is still denied EVERY area (no widening for non-supers)",
      not any(rt._can_edit_setting(rep, a) for a in all_areas),
      str([a for a in all_areas if rt._can_edit_setting(rep, a)]))
check("F5. _can_view_failures: drifted super-admin YES, sales_rep NO",
      rt._can_view_failures(sup) is True and rt._can_view_failures(rep) is False)
check("F6. _can_view_failures unchanged for a page-level DENY on a non-super",
      rt._can_view_failures({"org_id": LUX, "role": "sales_rep", "super_admin": False,
                             "perms": {"pages": {"/failures": False}}}) is False)

print()
print("── (G) the super-admin PATH still works (contract: never lock the operator out) ──")
check("G1. acting as a NON-HOUSE tenant, org_id is that tenant — the flag's org never leaks in",
      rt._resolve_caller(client_for(DRIFTED), UID, LUX)["org_id"] == LUX)
# put_tenant_settings: `org_id = (body.org_id if caller["super_admin"] else None) or caller["org_id"]`
def pts_target(caller, body_org):
    return (body_org if caller["super_admin"] else None) or caller["org_id"] or rt.ORG_ID
check("G2. put_tenant_settings' super-admin 'target another tenant' branch is reachable again "
      "(it was silently dead for a drifted super-admin)",
      pts_target(rt._resolve_caller(client_for(DRIFTED), UID, LUX), THIRD) == THIRD
      and pts_target(resolve_caller_PREFIX(client_for(DRIFTED), UID, LUX), THIRD) == LUX)
check("G3. a NON-super caller still cannot target another tenant via the body",
      pts_target(rep, THIRD) == LUX)
check("G4. _require_super_admin's own verdict is untouched by this change (source unmodified)",
      "any(r.get(\"super_admin\") for r in rows)" in
      open("app/modules/core/router.py", encoding="utf-8").read())
check("G5. the shipped _resolve_caller resolves the membership list EXACTLY ONCE per call "
      "(no extra round trip added)",
      rt._resolve_caller.__code__.co_names.count("_memberships") == 1
      if "_memberships" in rt._resolve_caller.__code__.co_names else False)

print()
print("=" * 60)
print(f"PASS {len(PASS)}   FAIL {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
