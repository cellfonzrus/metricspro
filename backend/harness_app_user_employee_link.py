"""PROOF + LOCK — a login is linked to its person, whichever way it was created (index §CORE-07).

  python3 backend/harness_app_user_employee_link.py

OWNER 2026-10-14: *"all stores should be able to see their dashboard with commissions earned and
their schedules and submit their closing at night with their app, then send me the process to add app
access to all of them"*.

THE DEFECT. A login is tied to its person by ONE column, `app_users.employee_id`. With it NULL the
login is nobody — §A reproduces that against the REAL shipped `core/scope.py::visible_people_keyset`,
not a description of it. Three paths write an `app_users` row for a person and each decided the link
for itself: the Roles GRID sent `employee_id`, the add-a-person FORM did not, and the spreadsheet
UPLOAD did not. So whether a newly provisioned rep could see their own schedule depended on which
button the administrator pressed. Measured live the same day: the one Vzone sales_rep login carries no
`employee_id`, which is exactly this instance.

THE FIX IS THE CLASS. `core/app_user_identity.py::link_employee_id` is the one home for the
precedence, and `_ensure_employee` — already the one helper both write paths call to put the person on
the roster — now also answers which person they are. Tenant creation and super-admin elevation are
excused on purpose (an 'all'-scope administrator is not on any store's roster); §D pins the excuse so
it stays deliberate.

  §A the consequence, against the shipped scope resolver (the regression)
  §B the precedence table of the one home (pure)
  §C `_ensure_employee` resolves and returns the link, and both write paths dereference the one home
  §D the two excused write paths, named
  §E negative controls — each check above must actually bite

PURE: stdlib only. Imports `app.core.scope` (which has no web dependency) and reads the router source
statically, because the router itself cannot be imported without FastAPI installed — and because the
failure mode here is an ABSENT assignment, which no import-level test can see.
"""
import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.core.scope import visible_people_keyset                       # noqa: E402
from app.modules.core.app_user_identity import LINK_SOURCES, link_employee_id   # noqa: E402
from app.modules.core.module_graph import home_under_app               # noqa: E402

ROUTER = os.path.join(HERE, "app", "modules", "core", "router.py")
# The graph (§50) is the one home for WHERE the ruling lives, so this lock cannot point at a file
# that has moved, and the fact cannot acquire a second copy unnoticed.
HOME = os.path.join(HERE, "app", home_under_app("app_user_employee_link"))
P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, detail))


def read(path):
    return io.open(path, encoding="utf-8").read()


SRC = read(ROUTER)
HOME_SRC = read(HOME)


def func_src(src, name):
    """The source of one top-level def, by AST so a nested def or a string cannot be mistaken for it."""
    tree = ast.parse(src)
    lines = src.split("\n")
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    return ""


def endpoint_src(src, route):
    """The source of the handler decorated with @router.post("<route>")."""
    tree = ast.parse(src)
    lines = src.split("\n")
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            txt = ast.unparse(dec) if hasattr(ast, "unparse") else ""
            if route in txt:
                return "\n".join(lines[node.lineno - 1:node.end_lineno])
    return ""


# ── §A the consequence, against the SHIPPED resolver ─────────────────────────────────────────────
# A people_visibility-'self' role returns before any client read, so this needs no database. The
# client is passed as None on purpose: if the self branch ever started reading, this would raise
# rather than quietly pass.
print("\n§A  what a NULL link costs, proved against core/scope.py as shipped")
SELF_ROLE = {"scope": "self", "people_visibility": "self"}
linked, why_linked = visible_people_keyset(
    None, "org", role_perms=SELF_ROLE, app_user={"email": "rep@x.com", "employee_id": "E229"})
unlinked, why_unlinked = visible_people_keyset(
    None, "org", role_perms=SELF_ROLE, app_user={"email": "rep@x.com"})
check("A1 a LINKED rep resolves to exactly their own person", linked == {"E229"}, repr(linked))
check("A2 an UNLINKED rep resolves to the empty set — a blank Schedule, not an error",
      unlinked == set(), repr(unlinked))
check("A3 ...and it is not 'unrestricted': an empty set is deny-all, None would be everything",
      unlinked is not None)
check("A4 the resolver itself says why, so the blank week is diagnosable",
      "NO employee_id" in str(why_unlinked), repr(why_unlinked))
check("A5 an empty-string link counts as no link (never written as a real id)",
      visible_people_keyset(None, "org", role_perms=SELF_ROLE,
                            app_user={"employee_id": ""})[0] == set())

# ── §B the precedence table of the one home ──────────────────────────────────────────────────────
print("\n§B  core/app_user_identity.link_employee_id — the precedence, pure")
check("B1 an explicitly requested id wins over everything",
      link_employee_id(request_id="E1", stored_id="E2", roster_id="E3") == "E1")
check("B2 an existing stored link is never DROPPED by an upsert that names nobody",
      link_employee_id(request_id=None, stored_id="E2", roster_id="E3") == "E2")
check("B3 otherwise the roster's answer for this email",
      link_employee_id(roster_id="E3") == "E3")
check("B4 nobody nameable -> None, so the column stays NULL rather than falsy",
      link_employee_id() is None and link_employee_id(request_id="", stored_id="  ") is None)
check("B5 whitespace is not an id, and a real id is trimmed",
      link_employee_id(request_id="   ", roster_id="  E7 ") == "E7")
check("B6 a non-string id is tolerated, not raised on",
      link_employee_id(request_id=229) == "229")
check("B7 the three sources are DECLARED (so no caller spells the order itself)",
      LINK_SOURCES == ("request", "stored", "roster"), repr(LINK_SOURCES))

# ── §C the wiring ────────────────────────────────────────────────────────────────────────────────
print("\n§C  the roster helper answers the link, and both write paths dereference the one home")
ENSURE = func_src(SRC, "_ensure_employee")
FOR_EMAIL = func_src(SRC, "_employee_id_for_email")
ASSIGN = endpoint_src(SRC, "/users/assign")
BULK = endpoint_src(SRC, "/users/bulk-assign")
check("C0 all four sources were located", all([ENSURE, FOR_EMAIL, ASSIGN, BULK]))
check("C1 _ensure_employee returns (created, employee_id) on EVERY path — no bare `return False`",
      ENSURE and not re.search(r"\n\s+return (False|True)\s*$", ENSURE, re.M),
      "a bool-only return would silently unlink the caller")
check("C2 ...and an ALREADY-ROSTERED person is resolved too, not skipped",
      "_employee_id_for_email(" in ENSURE)
check("C3 the email resolver is ORG-SCOPED — a cross-tenant match would link a stranger",
      '.eq("org_id", org_id)' in FOR_EMAIL)
check("C4 ...and it mints through THE ONE MINT rather than inventing an id",
      "_ensure_employee_id(" in FOR_EMAIL and "E%" not in FOR_EMAIL and 'f"E' not in FOR_EMAIL)
check("C5 ...and it never guesses on a read failure", 'return ""' in FOR_EMAIL)
for name, body in (("/users/assign", ASSIGN), ("/users/bulk-assign", BULK)):
    check("C6 %s dereferences the one home for the link" % name,
          "link_employee_id(" in body)
    check("C7 %s spells no precedence of its own" % name,
          not re.search(r'employee_id"\)\s*or\s', body) and not re.search(r"or\s+linked_eid", body),
          "an `or` chain here IS the second copy")
    _writes = [body.index(w) for w in ('table("app_users").insert', 'table("app_users").update')
               if w in body]
    check("C8 %s resolves the roster BEFORE it WRITES the login row" % name,
          "_ensure_employee(" in body and _writes
          and body.index("_ensure_employee(") < min(_writes),
          "resolving after the write leaves the column NULL "
          "(the handler's own SELECT of the row does not count)")
check("C9 the one home is PURE — no client, no I/O, no imports",
      not re.search(r"^\s*(import|from)\s", HOME_SRC, re.M)
      and "client" not in HOME_SRC.split('"""')[-1])

# ── §D the excused write paths, named on purpose ─────────────────────────────────────────────────
print("\n§D  the app_users writers that are NOT people are excused, and the excuse is pinned")
writers = [m.start() for m in re.finditer(r'table\("app_users"\)\.insert', SRC)]
check("D1 there are exactly four app_users insert sites (a NEW one must be ruled on here)",
      len(writers) == 4, "%d found" % len(writers))
excused = 0
for fn in ("_provision_tenant", "create_super_admin"):
    body = func_src(SRC, fn)
    if body and 'table("app_users").insert' in body:
        excused += 1
        check("D2 %s mints an administrator, not a rostered person — no link expected" % fn,
              '"role": "admin"' in body, "if this stops being an admin path, it needs the link")
        check("D2b %s names no employee_id at all (it is not a roster person)" % fn,
              '"employee_id"' not in body)
check("D3 the excuse is written down where the next reader will look",
      "excused on purpose" in HOME_SRC and "super-admin" in HOME_SRC)
check("D4 the two person-paths and the excused paths together account for every insert",
      excused + 2 == len(writers), "%d excused + 2 person paths vs %d inserts" % (excused, len(writers)))

# ── §E negative controls ─────────────────────────────────────────────────────────────────────────
print("\n§E  negative controls (a regressed source must FAIL, not pass)")
check("E1 the PRE-FIX assign row (an `or` chain, no one home) -> RED",
      not ("link_employee_id(" in
           '"employee_id": body.get("employee_id") or (cur[0].get("employee_id") if cur else None),'))
check("E2 the PRE-FIX bulk row (no employee_id key at all) -> RED",
      "link_employee_id(" not in '''row = {"org_id": org_id, "email": email, "is_active": True}''')
_ctrl_after = ASSIGN.replace("_ensure_employee(", "pass  # removed(")
check("E3 un-wiring the roster call -> RED", "_ensure_employee(" not in _ctrl_after)
_ctrl_order = ("    res = client.schema('storeops').table(\"app_users\").insert(row).execute()\n"
               "    _ensure_employee(client, org_id, email)\n")
check("E3b resolving the roster AFTER the write -> RED",
      _ctrl_order.index("_ensure_employee(")
      > _ctrl_order.index('table("app_users").insert'))
_ctrl_bool = "def _ensure_employee(c, o, e):\n    if not e:\n        return False\n    return True, ''\n"
check("E4 a bool-only return path -> RED",
      re.search(r"\n\s+return (False|True)\s*$", _ctrl_bool, re.M) is not None)
_ctrl_unscoped = 'def _employee_id_for_email(c, o, e):\n    return c.table("employees").eq("email", e)\n'
check("E5 an un-scoped email resolver -> RED", '.eq("org_id", org_id)' not in _ctrl_unscoped)
_ctrl_invent = 'def _employee_id_for_email(c, o, e):\n    return f"E{rows[0][\'id\']}"\n'
check("E6 inventing an id instead of using the one mint -> RED",
      "_ensure_employee_id(" not in _ctrl_invent)
check("E7 a fifth app_users insert -> RED (D1 forces a ruling on it)",
      len(writers) + 1 != 4)
check("E8 the one home gaining an import -> RED",
      re.search(r"^\s*import\s", "import json\n\ndef link_employee_id():\n    pass\n", re.M) is not None)
check("E9 §A would notice if the resolver stopped narrowing at all",
      visible_people_keyset(None, "org", role_perms={"scope": "self", "people_visibility": "all"},
                            app_user={})[0] is None,
      "'all' must still mean unrestricted — otherwise A2 proves nothing about 'self'")

print("\n%d passed / %d failed" % (P, F))
sys.exit(1 if F else 0)
