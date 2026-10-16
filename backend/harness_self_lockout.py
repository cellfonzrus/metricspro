"""PROOF + LOCK — an administrative write may not shut the door on its own author (index §CORE-12).

  python3 backend/harness_self_lockout.py

OWNER 2026-10-16: *"i made my self market manager under vzone and cannot get admin privilidges
anymore under ss, fix urgently"*.

THE DEFECT, REPRODUCED FROM THE REAL ROWS. The `ss@1313global.us` login holds four memberships, one
per tenant, three of them `admin`. Its Vzone membership was changed admin -> market_manager through
`POST /core/users/assign`, which gates on `_can_edit_setting(caller, 'security')` and then writes
whatever it was asked to write — including the row that answer was computed from. Vzone's Market
Manager role carries `modules.admin: false` and `scope: 'market'`, and the login carries no
`super_admin` flag, so after the write no screen inside Vzone could undo it and no other tenant could
reach in. It took a hand-written UPDATE in the database to restore.

THE FIX IS THE CLASS, NOT THE ROW. `core/self_lockout.py` answers "would this write take away the
authority that just made it?" once, for every writer, by asking the SAME `can_edit_setting` the gate
asks (moved there, not copied) before and after the proposed write. It refuses only when the answer
flips True -> False, so demoting somebody else, demoting yourself while a `super_admin` flag or an
explicit `settings.security` grant survives, and every write by a caller who never had the authority
are all untouched.

  §A the instance, reproduced on the real role permissions and the real membership shape
  §B the pure decision table of the one home
  §C every authority writer in core/router.py dereferences the guard (the anti-un-wiring lock)
  §D the excused writers, named, with the reason each is excused
  §E negative controls — each check above must actually be able to fail

PURE: stdlib only. The decision module imports nothing; the router is read as SOURCE (it cannot be
imported without FastAPI, and the failure mode here is an ABSENT call, which no import-level test
can see).
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.modules.core.module_graph import home_under_app          # noqa: E402
from app.modules.core.self_lockout import (                      # noqa: E402
    WRITE_AREA, acting_caller, can_edit_setting, lockout_reason, rows_with_change, rows_without)

ROUTER = os.path.join(HERE, "app", "modules", "core", "router.py")
# The graph (§50) is the one home for WHERE the ruling lives, so this lock cannot point at a file
# that has moved, and the fact cannot acquire a second copy unnoticed.
HOME = os.path.join(HERE, "app", home_under_app("self_lockout"))
P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, detail))


# ── The live rows, as read from production 2026-10-16 (ids shortened; roles and flags verbatim) ──
HOUSE = "00000000-0000-0000-0000-000000000001"
VZONE = "f4f1c16e-2acf-4221-854a-c29a605754a7"
LUXE = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
NYLOG = "ba084e25-adcf-4d4d-9cc5-d3728775513f"

SS_ROWS = [
    {"id": "r-house", "org_id": HOUSE, "email": "ss@1313global.us", "role": "admin",
     "is_active": True, "super_admin": False},
    {"id": "r-luxe", "org_id": LUXE, "email": "ss@1313global.us", "role": "admin",
     "is_active": True, "super_admin": False},
    {"id": "r-nylog", "org_id": NYLOG, "email": "ss@1313global.us", "role": "admin",
     "is_active": True, "super_admin": False},
    {"id": "r-vzone", "org_id": VZONE, "email": "ss@1313global.us", "role": "admin",
     "is_active": True, "super_admin": False},
]
# storeops.roles for Vzone, as stored (the module/scope keys this decision actually reads).
VZONE_PERMS = {
    "admin": {"scope": "all", "people_visibility": "all",
              "modules": {"admin": True, "storeops": True, "commissions": True}},
    "market_manager": {"scope": "market", "people_visibility": "span",
                       "modules": {"admin": False, "storeops": True, "commissions": True}},
    "store_manager": {"scope": "store", "modules": {"admin": False}},
    "sales_rep": {"scope": "self", "modules": {"admin": False}},
}


def vzone_assign(role, rows=SS_ROWS):
    """The write the owner actually made: the Vzone membership moved to `role`."""
    return lockout_reason(rows_before=rows,
                          rows_after=rows_with_change(rows, row_id="r-vzone", changes={"role": role}),
                          org_id=VZONE, perms_before=VZONE_PERMS, perms_after=VZONE_PERMS)


print("\n§A  THE INSTANCE — ss@1313global.us, Vzone, admin -> market_manager")
before = can_edit_setting(acting_caller(SS_ROWS, VZONE, VZONE_PERMS), WRITE_AREA)
after_rows = rows_with_change(SS_ROWS, row_id="r-vzone", changes={"role": "market_manager"})
after = can_edit_setting(acting_caller(after_rows, VZONE, VZONE_PERMS), WRITE_AREA)
check("before the write the login could edit Vzone security settings", before is True)
check("after it, it could not — the door the write used is shut", after is False)
check("so the write is REFUSED", bool(vzone_assign("market_manager")))
check("the refusal says what to do instead, in words a non-engineer can act on",
      "another administrator" in (vzone_assign("market_manager") or ""))
check("PRE-FIX behaviour reproduced: with no guard the write simply proceeded",
      before is True and after is False,
      "the gate was computed from the row the write then overwrote")
check("the other three tenants are untouched by the Vzone write",
      all(can_edit_setting(acting_caller(after_rows, o, {"admin": {"scope": "all"}}), WRITE_AREA)
          for o in (HOUSE, LUXE, NYLOG)))
check("store_manager and sales_rep shut the same door (not a market_manager quirk)",
      bool(vzone_assign("store_manager")) and bool(vzone_assign("sales_rep")))
check("admin -> admin (a no-op re-save) is NOT refused", vzone_assign("admin") is None)

print("\n§B  THE DECISION — what is refused and what is deliberately not")
sa = [dict(r, super_admin=(r["org_id"] == HOUSE)) for r in SS_ROWS]
check("a super-admin demoting their own tenant row keeps the flag, so it is allowed",
      vzone_assign("market_manager", sa) is None)
granted = dict(VZONE_PERMS, market_manager=dict(VZONE_PERMS["market_manager"],
                                                settings={"security": True}))
check("a role carrying an explicit settings.security grant is allowed",
      lockout_reason(rows_before=SS_ROWS, rows_after=after_rows, org_id=VZONE,
                     perms_before=VZONE_PERMS, perms_after=granted) is None)
check("demoting SOMEBODY ELSE'S row is never refused by this guard",
      lockout_reason(rows_before=SS_ROWS,
                     rows_after=rows_with_change(SS_ROWS, row_id="someone-else",
                                                 changes={"role": "sales_rep"}),
                     org_id=VZONE, perms_before=VZONE_PERMS, perms_after=VZONE_PERMS) is None)
reps = [dict(r, role="sales_rep") for r in SS_ROWS]
check("a caller who never had the authority is not refused here (the gate already refused them)",
      lockout_reason(rows_before=reps,
                     rows_after=rows_with_change(reps, row_id="r-vzone", changes={"role": "sales_rep"}),
                     org_id=VZONE, perms_before=VZONE_PERMS, perms_after=VZONE_PERMS) is None)
check("DEACTIVATING your own row is the same door, and is refused",
      bool(lockout_reason(rows_before=SS_ROWS,
                          rows_after=rows_with_change(SS_ROWS, row_id="r-vzone",
                                                      changes={"is_active": False}),
                          org_id=VZONE, perms_before=VZONE_PERMS, perms_after=VZONE_PERMS)))
check("DELETING your own login is the same door, and is refused",
      bool(lockout_reason(rows_before=SS_ROWS, rows_after=rows_without(SS_ROWS, row_id="r-vzone"),
                          org_id=VZONE, perms_before=VZONE_PERMS, perms_after=VZONE_PERMS)))
# A role NAMED 'admin' is itself a rung of `can_edit_setting`, so narrowing its permissions cannot
# remove the authority — that is the gate's own rule, and the guard must agree with it rather than
# invent a stricter one. The role that CAN lose it this way is a custom one, scope-'all':
exec_perms = dict(VZONE_PERMS, executive={"scope": "all", "modules": {"admin": True}})
exec_rows = rows_with_change(SS_ROWS, row_id="r-vzone", changes={"role": "executive"})
check("editing your OWN custom role's scope down from 'all' is refused",
      bool(lockout_reason(rows_before=exec_rows, rows_after=exec_rows, org_id=VZONE,
                          perms_before=exec_perms,
                          perms_after=dict(exec_perms, executive={"scope": "market"}))))
check("narrowing the role literally NAMED 'admin' is allowed — the name is a rung of the gate "
      "itself, and the guard agrees with the gate rather than out-guessing it",
      lockout_reason(rows_before=SS_ROWS, rows_after=SS_ROWS, org_id=VZONE,
                     perms_before=VZONE_PERMS,
                     perms_after=dict(VZONE_PERMS,
                                      admin={"scope": "market",
                                             "modules": {"admin": False}})) is None)
check("editing a role the caller does NOT hold is allowed",
      lockout_reason(rows_before=SS_ROWS, rows_after=SS_ROWS, org_id=VZONE,
                     perms_before=VZONE_PERMS,
                     perms_after=dict(VZONE_PERMS, sales_rep={"scope": "self"})) is None)
check("an explicit settings.security DENY on your own role is refused too",
      bool(lockout_reason(rows_before=SS_ROWS, rows_after=SS_ROWS, org_id=VZONE,
                          perms_before=VZONE_PERMS,
                          perms_after=dict(VZONE_PERMS,
                                           admin=dict(VZONE_PERMS["admin"],
                                                      settings={"security": False})))))
check("a tenant the caller holds no membership in yields no authority either way",
      lockout_reason(rows_before=SS_ROWS, rows_after=after_rows, org_id="no-such-org",
                     perms_before=VZONE_PERMS, perms_after=VZONE_PERMS) is None)
check("acting_caller reads super_admin across ALL memberships, like _resolve_caller",
      acting_caller(sa, VZONE, VZONE_PERMS)["super_admin"] is True)
check("an inactive acting row supplies no role at all",
      acting_caller([dict(SS_ROWS[3], is_active=False)], VZONE, VZONE_PERMS)["role"] is None)

print("\n§C  THE WIRING — every authority writer asks the one home")
SRC = open(ROUTER, encoding="utf-8").read()
TREE = ast.parse(SRC)
LINES = SRC.splitlines()


def body_of(name):
    for n in ast.walk(TREE):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return "\n".join(LINES[n.lineno - 1:n.end_lineno])
    return ""


def writers():
    """Every function in core/router.py that WRITES authority: an app_users row carrying a role /
    is_active / super_admin, a roles row's permissions, or a login removal. DERIVED from the source,
    so a new writer joins the subject set by existing."""
    out = []
    for n in ast.walk(TREE):
        if not isinstance(n, ast.FunctionDef):
            continue
        b = "\n".join(LINES[n.lineno - 1:n.end_lineno])
        au = re.search(r'table\("app_users"\)[\s\S]{0,400}?\.(update|insert|upsert|delete)\(', b)
        authority = au and re.search(r'"(role|is_active|super_admin)"\s*:', b)
        roles = re.search(r'table\("roles"\)[\s\S]{0,300}?\.(update|upsert)\(', b)
        if authority or roles:
            out.append((n.name, b))
    return out


# The excuses are part of the fact and are written down (§D proves each one is real).
EXCUSED = {
    "_provision_tenant": "mints a NEW tenant's first administrator — it can only add authority",
    "create_super_admin": "elevates a login — it can only add authority",
    "disable_and_switch": "the signed-in user's own deliberate hand-over, and it mints the "
                          "replacement login FIRST; refusing it would be the lockout",
    "reinstate_login": "restores an access that was removed — it can only add authority",
    "bulk_provision": "creates logins; it writes no role of an existing administrator",
}
# DECLARED, not bounded: a writer that joins or leaves core/router.py changes this line, which is
# the point — the subject set is derived from the source and then held against what a human wrote.
EXPECTED_WRITERS = {
    "_provision_tenant", "assign_role", "bulk_assign", "bulk_provision", "create_role",
    "create_super_admin", "deactivate_user", "disable_and_switch", "reinstate_login",
    "revoke_super_admin", "update_role",
}
found = {n for n, _ in writers()}
for name, b in writers():
    if name in EXCUSED:
        continue
    check("%s dereferences _refuse_self_lockout" % name, "_refuse_self_lockout(" in b,
          "an authority writer that cannot refuse its own author")
check("the five role/membership writers are all in the subject set",
      {"assign_role", "bulk_assign", "update_role", "create_role", "deactivate_user"} <= found,
      sorted(found))
check("purge_app_user carries the guard and takes a caller token",
      "_refuse_self_lockout(" in body_of("purge_app_user")
      and "authorization=None" in body_of("purge_app_user"))
check("revoke_super_admin keeps its own last-super-admin refusal as well",
      "cannot remove the last platform super-admin" in body_of("revoke_super_admin")
      and "_refuse_self_lockout(" in body_of("revoke_super_admin"))

print("\n§C2  ONE HOME — the authority answer is not copied")
check("core/router.py DEREFERENCES can_edit_setting, it does not redefine it",
      "_can_edit_setting = _self_lockout.can_edit_setting" in SRC
      and "def _can_edit_setting(" not in SRC)
defs = len(re.findall(r"^def can_edit_setting\(", open(HOME, encoding="utf-8").read(), re.M))
check("the answer is defined exactly once, in the one home", defs == 1, defs)
copies = [os.path.relpath(os.path.join(dp, f), HERE)
          for dp, _, fs in os.walk(os.path.join(HERE, "app")) for f in fs
          if f.endswith(".py")
          and re.search(r"def can_edit_setting\(|def _can_edit_setting\(",
                        open(os.path.join(dp, f), encoding="utf-8").read())
          and os.path.join(dp, f) != HOME]
check("no second copy of it anywhere under backend/app", not copies, copies)
check("the one home stays importable without FastAPI (it is pure)",
      not re.search(r"^\s*(import|from)\s+(fastapi|supabase|app\.)",
                    open(HOME, encoding="utf-8").read(), re.M))

print("\n§D  THE EXCUSED WRITERS, NAMED")
for name, why in EXCUSED.items():
    check("%s — %s" % (name, why), bool(body_of(name)),
          "excused writer no longer exists; drop it from the excuse list")

print("\n§E  NEGATIVE CONTROLS — each check above can actually fail")
check("E1 a guard that always returned None would fail §A",
      vzone_assign("market_manager") is not None)
check("E2 a guard that refused everything would fail §B",
      vzone_assign("admin") is None)
check("E3 the wiring scan sees a writer that does NOT call the guard",
      "_refuse_self_lockout(" not in body_of("create_super_admin"),
      "the scan would pass vacuously")
check("E4 the derived writer set is exactly the declared one", found == EXPECTED_WRITERS,
      "new: %s  gone: %s" % (sorted(found - EXPECTED_WRITERS), sorted(EXPECTED_WRITERS - found)))
check("E6 every excused name is really one of the writers",
      set(EXCUSED) <= found, sorted(set(EXCUSED) - found))
check("E5 the copy scan reads real files",
      os.path.exists(HOME) and "can_edit_setting" in open(HOME, encoding="utf-8").read())

print("\n%d passed / %d failed" % (P, F))
sys.exit(1 if F else 0)
