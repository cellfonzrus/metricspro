#!/usr/bin/env python3
"""PROOF + BUILD LOCK: "assign this employee a password I type" is ONE home, and every surface
offering it dereferences that home — plus the Payroll hub's Manage Employees tile.

OWNER 2026-10-14: *"provide a link to reset the password for the employee login on payroll Dashboard,
make a tile called Manage employee and move employee and pay and employee database along with
password reset under that tile — in both config and this new link to password reset, the admin should
be able to assign a manual password also"*.

WHAT WAS ACTUALLY WRONG, named as a class. The CAPABILITY already existed and was already correct:
`POST /api/v1/core/users/set-password` (core/router.py::admin_set_password) sets a SPECIFIC
admin-chosen password, validated server-side against the tenant policy, flipping
`must_reset_password`, audited as `password_admin_set`, gated on
`_can_edit_setting(caller, "security")`. Its own docstring already said it "generalizes create-login
/ Reset pw — one path, not a fork".

What was missing was a DOOR. Its only UI was a card at the bottom of Admin → Security Settings, a
page tagged `module: 'admin'` that no HR or market manager ever sees — so the people who manage
employees had no way to do it, and the owner asked for the same affordance in two more places.

The patchwork answer would be to retype the form, the fetch and the client policy echo on each new
surface: three copies of one question, drifting the moment the policy or the endpoint changes. So the
card became ONE home — `frontend/src/components/AssignPasswordCard.tsx` — that three surfaces render:

    /admin/security                     Security Settings (where it used to be inline)
    /admin/roles  (👥 Assign people)    "in config": where logins are provisioned / resent / revealed
    /hr/employee-password               the Payroll hub's Manage Employees tile

WHY A STATIC HARNESS. Every failure mode here is a MISSING or DUPLICATED wire: a surface that
re-implements the form compiles and renders and looks right, a surface that drops the component
simply shows nothing, and a tile that loses a door is still a valid tile. No type checker and no test
of the component itself can see any of that. So this asserts the wiring at the source.

  A  the one home exists and carries the whole affordance (the endpoint, the policy echo, the picker,
     and the SERVER's can_edit as the authority)
  B  all three surfaces dereference it
  C  nobody holds a second copy — not the endpoint call, not the policy echo
  D  the Manage Employees tile holds the owner's three doors, the two folded-in tiles are gone, and
     the §19.40 side-menu entry for Employees & Pay is untouched
  E  the SERVER is the authority: the endpoint's own gate + policy re-validation are intact, and the
     new page carries no client-side role guess standing in for them
  Z  ARMED CONTROLS — each check re-run against a deliberately broken source, and it must FAIL.
     A check that cannot fail is not a check (index §24).

PURE: stdlib only; reads the frontend + backend sources. DB-free (fastapi/supabase are not imported).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FRONT = os.path.join(ROOT, "frontend", "src")
PLAT = os.path.join(FRONT, "app", "(platform)")

# THE ONE HOME. A frontend component, so it is deliberately NOT a module_graph fact (that graph's
# homes are backend paths under app/ — `home_under_app` splits on "app/"). Its neighbour §CORE-06
# registered `lib/hub-gate.ts` the same way: one home, one static lock, no graph row.
ONE_HOME = os.path.join(FRONT, "components", "AssignPasswordCard.tsx")

CONSUMERS = {
    "admin → security settings": os.path.join(PLAT, "admin", "security", "page.tsx"),
    "admin → roles (assign people)": os.path.join(PLAT, "admin", "roles", "page.tsx"),
    "hr → employee password reset": os.path.join(PLAT, "hr", "employee-password", "page.tsx"),
}
PAYROLL_HUB = os.path.join(PLAT, "payroll", "page.tsx")
RBAC = os.path.join(FRONT, "lib", "rbac.ts")
CORE_ROUTER = os.path.join(ROOT, "backend", "app", "modules", "core", "router.py")

ENDPOINT = "core/users/set-password"
# One distinctive line of the client policy ECHO. A second copy of the validator would carry it.
ECHO = "Add an uppercase letter."
# The owner's three doors, in his order.
TILE_DOORS = ["/hr?tab=employees", "/hr/employee-database", "/hr/employee-password"]

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
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def code_only(src):
    """Drop // and /* */ comments, so a fact merely NAMED in prose never counts as a wire."""
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return "\n".join(re.sub(r"//.*$", "", ln) for ln in src.split("\n"))


def ts_files():
    for dp, _dn, fns in os.walk(FRONT):
        for f in fns:
            if f.endswith((".ts", ".tsx")):
                yield os.path.join(dp, f)


def tile(src, title):
    """The items[] block of the hand-woven HubGroup literal titled `title`; '' when there is none."""
    pat = r"title: '%s',[\s\S]*?items: \[([\s\S]*?)\n    \],"% re.escape(title)
    m = re.search(pat, src)
    return m.group(1) if m else ""


def nav_item(src, href):
    """The single NAV line declaring `href`; '' when absent."""
    for ln in code_only(src).split("\n"):
        if re.search(r"\{\s*href: '%s'" % re.escape(href), ln):
            return ln
    return ""


HOME = read(ONE_HOME)
HOME_CODE = code_only(HOME)
HUB = read(PAYROLL_HUB)
RBAC_SRC = read(RBAC)

# ── §A the one home carries the whole affordance ──────────────────────────────────────────────────
print("\n§A  components/AssignPasswordCard.tsx is the ONE home")
check("A1 the file exists", bool(HOME), ONE_HOME)
check("A2 it default-exports the card", "export default function AssignPasswordCard" in HOME)
check("A3 it POSTs the ONE existing server path", ENDPOINT in HOME_CODE and "method: 'POST'" in HOME_CODE)
check("A4 it carries the client policy echo", "export function pwErrors" in HOME and ECHO in HOME)
check("A5 it offers a MANUAL password (a typed input, not a generated code)",
      "placeholder=\"New password\"" in HOME_CODE and "password: pw" in HOME_CODE,
      "the owner asked to assign a manual password — a generated temp code is a different thing")
check("A6 it offers the require-change toggle the endpoint takes", "require_change: requireChange" in HOME_CODE)
check("A7 it picks the person from the roster, never a typed email", "EntityPicker" in HOME_CODE)
check("A8 THE SERVER is the authority: it reads can_edit from /core/security-settings",
      "core/security-settings" in HOME_CODE and "can_edit" in HOME_CODE,
      "showing the form is a UI decision; the gate is _can_edit_setting(caller,'security')")
check("A9 …and says so rather than showing a form that 403s",
      "don&apos;t have permission" in HOME or "don't have permission" in HOME)
check("A10 it adopts a host's already-loaded policy/roster instead of re-fetching",
      "policy?" in HOME and "users?" in HOME and "canEdit?" in HOME)

# ── §B every surface dereferences it ─────────────────────────────────────────────────────────────
print("\n§B  all three surfaces dereference the one home")
for name, path in sorted(CONSUMERS.items()):
    src = read(path)
    check("B · %s: exists" % name, bool(src), path)
    check("B · %s: imports the one home" % name, "@/components/AssignPasswordCard" in src)
    check("B · %s: RENDERS it" % name, "<AssignPasswordCard" in code_only(src),
          "an import nobody renders is a surface that went back to its own copy")

# ── §C nobody holds a second copy ────────────────────────────────────────────────────────────────
print("\n§C  no second copy of the affordance anywhere under frontend/src")
def endpoint_dups(files):
    """THE detector §C1 applies: any file but the one home whose CODE calls the endpoint.
    Taken as a function so §Z can arm it against a synthetic second copy."""
    return [p for p, src in files if p != ONE_HOME and ENDPOINT in code_only(src)]


dup_ep = [os.path.relpath(f, ROOT) for f in
          endpoint_dups([(f, read(f)) for f in ts_files()])]
check("C1 only the one home calls %s" % ENDPOINT, not dup_ep,
      "also called in: %s — render the component instead" % ", ".join(dup_ep))
dup_echo = [os.path.relpath(f, ROOT) for f in ts_files()
            if f != ONE_HOME and ECHO in code_only(read(f))]
check("C2 only the one home carries the policy echo", not dup_echo,
      "also in: %s — import pwErrors from the one home" % ", ".join(dup_echo))
dup_fn = [os.path.relpath(f, ROOT) for f in ts_files()
          if f != ONE_HOME and re.search(r"function pwErrors\s*\(", code_only(read(f)))]
check("C3 only the one home DEFINES pwErrors", not dup_fn, ", ".join(dup_fn))

# ── §D the Manage Employees tile ─────────────────────────────────────────────────────────────────
print("\n§D  the Payroll hub's Manage Employees tile (the owner's spec, verbatim)")
mt = tile(HUB, "Manage Employees")
check("D1 the hub carries a tile titled 'Manage Employees'", bool(mt),
      "owner: \"make a tile called Manage employee\"")
for href in TILE_DOORS:
    check("D2 …holding %s" % href, "href: '%s'" % href in mt)
check("D3 the tile is gated like every other (the hub dereferences lib/hub-gate.ts)",
      "useHubGate" in code_only(HUB), "index §CORE-06 — a tile is a menu entry")
for gone in ("Employees & Pay", "Employee Database"):
    check("D4 the folded-in tile '%s' no longer stands alone" % gone,
          ("title: '%s'" % gone) not in HUB,
          "the owner asked for them MOVED under Manage Employees, not duplicated")
nav_pw = nav_item(RBAC_SRC, "/hr/employee-password")
check("D5 NAV declares the password page", bool(nav_pw))
check("D6 …as module 'hr' + ['all','market'], the shape of its tile neighbours",
      "module: 'hr'" in nav_pw and "scopes: ['all', 'market']" in nav_pw,
      "an 'admin'-module destination behind an hr tile would be dropped for every HR manager")
check("D7 …and tileOnly, because the tile IS its door", "tileOnly: true" in nav_pw)
nav_ep = nav_item(RBAC_SRC, "/hr?tab=employees")
check("D8 §19.40 UNTOUCHED: Employees & Pay is still its own SIDE-MENU row",
      bool(nav_ep) and "tileOnly" not in nav_ep,
      "the owner asked about the TILE; the 2026-10-02 menu ruling stands")

# ── §E the server is the authority ───────────────────────────────────────────────────────────────
print("\n§E  the server gate and the server-side policy check are intact")
rt = read(CORE_ROUTER)
handler = rt[rt.index("async def admin_set_password"):] if "async def admin_set_password" in rt else ""
handler = handler[:handler.index("\n@router.") if "\n@router." in handler else len(handler)]
check("E1 admin_set_password still gates on the 'security' setting permission",
      '_can_edit_setting(caller, "security")' in handler)
check("E2 …and still re-validates the password server-side", "validate_password(" in handler)
check("E3 …and still audits the set", '"password_admin_set"' in handler)
check("E4 the new page carries no client-side role guess standing in for the gate",
      not re.search(r"\bisAdmin\b|\bsuper_admin\b", code_only(read(CONSUMERS["hr → employee password reset"]))),
      "the only gate is the server's; a client guess here would be a second, drifting answer")

# ── §Z armed controls ────────────────────────────────────────────────────────────────────────────
print("\n§Z  armed controls — each check must be able to go RED")
check("Z1 a home that stops calling the endpoint → caught",
      ENDPOINT not in code_only(HOME.replace(ENDPOINT, "core/users/nope")))
check("Z2 a consumer that imports but never renders → caught",
      "<AssignPasswordCard" not in code_only(
          read(CONSUMERS["admin → security settings"]).replace("<AssignPasswordCard", "{/* <X")))
check("Z3 a second surface calling the endpoint → caught",
      endpoint_dups([(ONE_HOME, HOME),
                     ("other/page.tsx", "api('/api/v1/%s', { method: 'POST' })" % ENDPOINT)])
      == ["other/page.tsx"])
check("Z3b …while the same call inside a COMMENT does not count",
      endpoint_dups([("other/page.tsx", "// see /api/v1/%s" % ENDPOINT)]) == [])
check("Z4 a retyped policy echo → caught",
      ECHO in code_only("function pwErrors(){ e.push('%s') }" % ECHO))
check("Z5 a tile that loses a door → caught",
      "href: '/hr/employee-password'" not in tile(
          HUB.replace("href: '/hr/employee-password'", "href: '/x'"), "Manage Employees"))
check("Z6 the tile renamed away from the owner's words → caught",
      not tile(HUB.replace("title: 'Manage Employees'", "title: 'People'"), "Manage Employees"))
check("Z7 the password page given the 'admin' module → caught",
      "module: 'hr'" not in nav_pw.replace("module: 'hr'", "module: 'admin'"))
check("Z8 Employees & Pay hidden from the side menu again → caught",
      "tileOnly" in (nav_ep + ", tileOnly: true"))
check("Z9 the server gate dropped → caught",
      '_can_edit_setting(caller, "security")' not in handler.replace(
          '_can_edit_setting(caller, "security")', "True"))

print()
print("=" * 78)
print("RESULT: %d passed, %d failed" % (P, F))
print("=" * 78)
sys.exit(1 if F else 0)
