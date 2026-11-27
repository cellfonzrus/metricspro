"""PROOF — an employee can SEE their own schedule, and who "mine" is is answered ONCE, on the server.

OWNER (2026-11-27, verbatim): *"also under employee login it does not show the employee thier own
schdule"* and, clarifying: *"it shows your next shift at the bottom on the main page it shoudl also
show in schedule whic his made for teh next 2 weeks or one week"*.

THE INSTANCE. `rep_surface.SURFACES` declares a tile labelled **"My schedule"**, and a tile's link is
`pages[0]` of its surface. The schedule surface's first page was `/storeops/timeoff`, so the tile
opened the TIME-OFF REQUEST FORM. The only schedule an employee could see at all was the four-row
"Your next shifts" list inlined on `/my`. The manager's builder (`/storeops/schedule` — whole-roster
grid, shift writes, hours-trend report) is correctly NOT granted and stays that way; what was missing
is an employee-facing READ of the published schedule.

THE CLASS (CLAUDE.md, "A fix is a DESIGN fix"): **a surface whose answer the server already has, and
that the browser re-derives anyway with a narrower rule.** `GET /storeops/shifts` already narrows to
the people the caller may read (`schedule_emp_ids`, §14w). `/my` then filtered those rows AGAIN in
the browser with `String(r.employee_id) === String(user.employee_id)` — one id form, string-equal. A
shift row created on the Schedule page stores the NUMERIC `employees.id` ("45") while a login carries
the BUSINESS id ("E45"); the server widens both forms on purpose and the browser did not, so for a
tenant whose rows land on the other form that filter answers ZERO — a plausible-looking answer, which
is why this class hides (the same shape as the device-line default, index §COMMCALC-49.10).

AND ITS SIBLING, FOUND WHILE FIXING IT. `storeops.router._widen_emp_id_forms` — the widening the
schedule gate itself dereferences — paired the two forms UNGUARDED, while
`payroll_identity.business_id_alias_map` (the one home for "these two ids are the same person") has
carried a collision guard since 2026-07-27: a numeric id can BE a different employee's own all-digit
business id, so widening "E42" to "42" can admit employee B's shifts into employee A's gate. Both now
dereference that one home, so the guard is not a thing one caller happens to have.

    python3 backend/harness_my_schedule.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
P = F = 0


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {name}")
    else:
        F += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def ctl(name, cond, detail=""):
    """A negative control: the rule above it must be able to go RED."""
    check("CONTROL " + name, cond, detail)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def code_only(src, lang="py"):
    if lang == "py":
        src = re.sub(r'"""(?:.|\n)*?"""', "", src)
        return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    src = re.sub(r"/\*(?:.|\n)*?\*/", "", src)
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))


from app.core import rep_surface as rs
from app.modules.storeops.payroll_identity import business_id_alias_map, own_id_forms

RS = read("backend", "app", "core", "rep_surface.py")
SO = read("backend", "app", "modules", "storeops", "router.py")
MY = read("frontend", "src", "app", "(platform)", "my", "page.tsx")
SCHED = read("frontend", "src", "app", "(platform)", "my", "schedule", "page.tsx")
RBAC = read("frontend", "src", "lib", "rbac.ts")

MY_SCHEDULE = "/my/schedule"

# ── 1. THE TILE OPENS A SCHEDULE ─────────────────────────────────────────────────────────────────
print("\n1. the tile labelled 'My schedule' opens a schedule")
tiles = {t["key"]: t for t in rs.tiles()}
check("1a the schedule surface is still tiled", "schedule" in tiles)
check("1b its tile opens the employee's own schedule page, NOT the time-off request form",
      tiles["schedule"]["page"] == MY_SCHEDULE,
      f"tile opens {tiles['schedule']['page']!r}")
ctl("1b can go red — the form is still a declared page of that surface, just not its FIRST",
    "/storeops/timeoff" in rs.pages())
check("1c a confined employee may OPEN it", rs.page_allowed(MY_SCHEDULE))
check("1d no carve-out shadows it", MY_SCHEDULE not in rs.page_exceptions())
check("1e the manager's builder stays refused — the fix is a READ for the employee, never a grant "
      "of the shift writes",
      not rs.page_allowed("/storeops/schedule")
      and not rs.api_allowed("/api/v1/storeops/shifts", "POST")
      and not rs.api_allowed("/api/v1/storeops/schedule/hours-trend", "GET") is False)
check("1f the read it needs is granted, and GET-only",
      rs.api_allowed("/api/v1/storeops/shifts", "GET")
      and not rs.api_allowed("/api/v1/storeops/shifts", "POST")
      and not rs.api_allowed("/api/v1/storeops/shifts", "DELETE"))
check("1g it is a real page on disk, registered in NAV so it carries a page title",
      os.path.isfile(os.path.join(ROOT, "frontend", "src", "app", "(platform)",
                                  "my", "schedule", "page.tsx"))
      and f"href: '{MY_SCHEDULE}'" in RBAC)

# ── 2. ONE OR TWO WEEKS, AND THE WINDOW IS THE PAGE'S OWN ────────────────────────────────────────
print("\n2. the window the owner asked for")
check("2a the page offers both windows the owner named — one week and two",
      "WINDOWS" in SCHED and re.search(r"\b7\b", SCHED) and re.search(r"\b14\b", SCHED))
check("2b it reads the SHARED shift read, never a sibling endpoint of its own",
      "/api/v1/storeops/shifts" in SCHED)
check("2c and it asks the SERVER whose shifts are mine", "mine=1" in SCHED)
check("2d it never writes a shift — an employee's schedule is a read",
      not re.search(r"method:\s*'(POST|PATCH|PUT|DELETE)'", SCHED))

# ── 3. "WHOSE SHIFT IS THIS" IS THE SERVER'S ANSWER, NOT THE BROWSER'S ───────────────────────────
print("\n3. the browser holds no private copy of 'mine'")
MY_CODE = code_only(MY, "ts")
SCHED_CODE = code_only(SCHED, "ts")
check("3a /my asks the server with `mine=1` instead of filtering rows itself", "mine=1" in MY_CODE)
check("3b neither page compares an employee_id to decide whose row this is",
      not re.search(r"employee_id\s*\|\|\s*''\)\s*===", MY_CODE + SCHED_CODE)
      and "user.employee_id)" not in MY_CODE.replace("mine=1", ""),
      "a browser-side id comparison is back")
check("3c the endpoint takes the question as a parameter", "mine: bool" in SO)
check("3d and answers it from the TOKEN, never from a client-supplied id",
      "def own_emp_ids" in SO
      and not re.search(r"def own_emp_ids\([^)]*employee_id", SO))
check("3e asking for `mine` with an unresolvable caller is DENY-ALL, never unrestricted — the "
      "opposite posture to schedule_emp_ids, because a caller who asked for THEIR OWN rows and "
      "cannot be named must get none rather than their manager's team",
      re.search(r"def own_emp_ids(?:.|\n)*?return set\(\)", SO))
check("3f the narrowing is applied ON TOP of the existing gate, so it can only ever narrow",
      re.search(r"gate_person_rows\((?:.|\n)*?\n(?:.|\n){0,400}?if mine:", SO))

# ── 4. ONE HOME FOR "THESE TWO IDS ARE THE SAME PERSON" ──────────────────────────────────────────
print("\n4. the id forms are one fact, dereferenced")
EMPS = [{"id": 45, "employee_id": "E45"}, {"id": 46, "employee_id": "E46"}]
check("4a a person's own rows are found under BOTH id forms",
      own_id_forms("E45", EMPS) == frozenset({"E45", "45"}),
      str(sorted(own_id_forms("E45", EMPS))))
check("4b and never under anybody else's", "46" not in own_id_forms("E45", EMPS)
      and "E46" not in own_id_forms("E45", EMPS))
check("4c a caller with no business id gets NOTHING, not everything",
      own_id_forms("", EMPS) == frozenset() and own_id_forms(None, EMPS) == frozenset())
# THE COLLISION. Employee B's own business id is the literal "42"; employee A is numeric 42 / "E42".
# Pairing the forms blindly would put B's shifts inside A's gate.
COLLIDE = [{"id": 42, "employee_id": "E42"}, {"id": 99, "employee_id": "42"}]
check("4d an AMBIGUOUS numeric id is not claimed — it is somebody else's real business id",
      own_id_forms("E42", COLLIDE) == frozenset({"E42"}),
      str(sorted(own_id_forms("E42", COLLIDE))))
# …and the person whose business id "42" REALLY is keeps it, together with their own numeric form
# ("99"). The guard costs A one form of their own rows; it costs B nothing. That asymmetry is the
# right way round: A seeing one form of A's shifts is a gap, A seeing B's shifts is a leak.
check("4e and the person whose business id it really is keeps it, plus their own numeric form",
      own_id_forms("42", COLLIDE) == frozenset({"42", "99"}),
      str(sorted(own_id_forms("42", COLLIDE))))
ctl("4d/4e can go red — the same two employees WITHOUT the collision do pair up",
    own_id_forms("E42", [{"id": 42, "employee_id": "E42"}]) == frozenset({"E42", "42"}))
check("4f it dereferences the one home rather than re-deriving the pairing",
      "business_id_alias_map(" in code_only(read("backend", "app", "modules", "storeops",
                                                 "payroll_identity.py")).split("def own_id_forms")[1])
check("4g the SIBLING is wired to the same home — the schedule gate's own widening no longer "
      "pairs the forms unguarded",
      "business_id_alias_map" in code_only(SO)
      and re.search(r"def _widen_emp_id_forms(?:.|\n)*?business_id_alias_map", SO))
ctl("4g can go red", "def _widen_emp_id_forms" in SO)

# ── 5. EVERY TENANT ──────────────────────────────────────────────────────────────────────────────
print("\n5. every tenant, and no tenant's words")
check("5a nothing here names a tenant, carrier or product",
      not any(w in (code_only(SCHED, "ts") + code_only(RS)).lower()
              for w in ("cellfonz", "luxelink", "vzone", "boost", "ny logistics")))
check("5b the declaration still takes no org — it is house POLICY, identical for every tenant",
      not re.search(r"def (page_allowed|api_allowed|tiles|pages)\([^)]*org", RS))
check("5c `own_id_forms` is PURE — it is handed the roster, it never reads one",
      "sb()" not in code_only(read("backend", "app", "modules", "storeops",
                                   "payroll_identity.py")).split("def own_id_forms")[1]
      and "get_supabase" not in code_only(read("backend", "app", "modules", "storeops",
                                               "payroll_identity.py")).split("def own_id_forms")[1])
# Two tenants, two id shapes: one whose shifts carry the numeric form, one whose carry the business
# form. BOTH must find their own person, and NEITHER may find the other's.
T1 = [{"id": 7, "employee_id": "E7"}]                     # auto-generated ids -> forms differ
T2 = [{"id": 1, "employee_id": "STAFF-1"}]                # imported ids -> no numeric overlap
check("5d a tenant on auto-generated ids finds both forms of its own person",
      own_id_forms("E7", T1) == frozenset({"E7", "7"}))
check("5e a tenant on imported ids finds its own person and invents no numeric claim",
      own_id_forms("STAFF-1", T2) == frozenset({"STAFF-1", "1"}))
check("5f one tenant's roster can never name another tenant's person — the function is given ONE "
      "org's roster and returns only forms of the id it was asked about",
      own_id_forms("E7", T1 + T2) == frozenset({"E7", "7"})
      and own_id_forms("STAFF-1", T1 + T2) == frozenset({"STAFF-1", "1"}))

print("\n" + "=" * 100)
print("%d passed, %d failed" % (P, F))
print("OK — the employee sees their own schedule, and 'mine' is the server's answer."
      if not F else "FAIL — the employee's own schedule is not proven.")
sys.exit(1 if F else 0)
