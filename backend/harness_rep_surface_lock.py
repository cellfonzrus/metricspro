"""LOCK — the individual contributor's reach stays ONE declaration, and the API stays the one that
enforces it. Stdlib only, static on purpose: an ABSENT gate compiles, renders and ships, so neither
`tsc` nor a test run can see a caller that simply stopped asking.

OWNER (2026-11-04): *"Manan was able to go past it and also see the adjustment hours … fix the
permissions accordingly — this is universal across the board."*

WHY A LOCK AT ALL (CLAUDE.md, "A fix is a DESIGN fix"): the registry-written-but-not-wired failure
has happened in this repo three times (index §19.18). The fix here is only a fix while
  · `backend/app/core/rep_surface.py` is the ONLY list of what an employee may reach,
  · the four person-writes keep dereferencing `require_person_write`,
  · `lib/rbac.ts` keeps reading the SERVER's list instead of holding its own, and
  · the API gate stays installed and stays unconditional.
Each of those is a line below, with a negative control that proves the line can actually go red.

    python3 backend/harness_rep_surface_lock.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
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
    """What actually executes: comments and docstrings removed. A rule must never be satisfied — or
    broken — by prose that merely EXPLAINS the defect."""
    if lang == "py":
        src = re.sub(r'"""(?:.|\n)*?"""', "", src)
        return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    src = re.sub(r"/\*(?:.|\n)*?\*/", "", src)
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))


RS = read("backend", "app", "core", "rep_surface.py")
RBAC = read("frontend", "src", "lib", "rbac.ts")
MAIN = read("backend", "app", "main.py")
TM = read("backend", "app", "core", "tenant_middleware.py")
SO = read("backend", "app", "modules", "storeops", "router.py")
HR = read("backend", "app", "modules", "hr", "router.py")
CORE = read("backend", "app", "modules", "core", "router.py")

# ── 1. ONE DECLARATION ───────────────────────────────────────────────────────────────────────────
print("\n1. the declaration is the only list")
check("1a the declaration exists and declares the surfaces", "SURFACES = (" in RS)
check("1b it declares the landing", bool(re.search(r'^LANDING_PAGE = "', RS, re.M)))
LANDING = re.search(r'^LANDING_PAGE = "([^"]+)"', RS, re.M).group(1)
fe_landing = re.search(r"export const REP_LANDING = '([^']+)'", RBAC)
check("1c the frontend names the same landing route", fe_landing and fe_landing.group(1) == LANDING,
      f"{fe_landing and fe_landing.group(1)} vs {LANDING}")
ctl("1c can go red", "REP_LANDING = '/not-the-landing'" not in RBAC)
check("1d the frontend holds NO page list of its own — it reads the server's",
      "repPages(perms)" in RBAC and "perms.rep_surface.pages" in RBAC)
# The one literal still in rbac.ts is the LEGACY self branch (an older backend sends no payload). It
# must stay inside that branch and nothing else may read it.
legacy = re.search(r"if \(scope === 'self'\) \{(.*?)\n  \}", RBAC, re.S)
RBAC_CODE = code_only(RBAC, "ts")
check("1e the only SELF_ALLOWED literal is inside the legacy branch",
      RBAC_CODE.count("SELF_ALLOWED") == 2 and legacy and "SELF_ALLOWED" in legacy.group(1)
      and RBAC_CODE.count("const SELF_ALLOWED = [") == 1,
      f"{RBAC_CODE.count('SELF_ALLOWED')} mentions in code")
ctl("1e can go red", RBAC.count("const SELF_ALLOWED = [") == 1)
check("1f no second employee-page list anywhere in the frontend lib",
      "REP_ALLOWED" not in RBAC and "EMPLOYEE_PAGES" not in RBAC)

# ── 2. THE PREFIX HOLE STAYS SHUT ────────────────────────────────────────────────────────────────
print("\n2. the prefix hole")
HOLE = "path.startsWith(it.href + '/')) && canSeeItem"
check("2a the parent-prefix widening appears ONCE and only in the legacy branch",
      RBAC.count(HOLE) == 1 and legacy and HOLE in legacy.group(1), f"{RBAC.count(HOLE)}")
ctl("2a can go red", RBAC.count(HOLE) == 1)
CAP = RBAC[RBAC.index("export function canAccessPath"):RBAC.index("export function homeFor")]
check("2b canAccessPath asks the confinement BEFORE the scope ladder",
      CAP.index("if (repConfined(perms)) return repPageAllowed(perms, path)")
      < CAP.index("const scope = perms.scope || 'all'"))
check("2c canSeeItem asks it too, so the menu and the guard cannot disagree",
      "if (repConfined(perms)) return repPageAllowed(perms, item.href)" in RBAC)
check("2d the confined answer is CLOSED — it returns the predicate, never `|| something`",
      not re.search(r"repConfined\(perms\)\)\s*return repPageAllowed\([^)]*\)\s*\|\|", RBAC))
check("2e a page's scope restriction binds one segment deeper (the governing nav item)",
      "const gov = governingNavItem(path)" in RBAC and "export function governingNavItem" in RBAC)
SUB = "/" + "**"
check("2f the page grammar is mirrored, not reinvented — both sides know the subtree marker and "
      "the carve-out",
      f"REP_SUBTREE = '/' + '**'" in RBAC and f'SUBTREE = "{SUB}"' in RS
      and "page_except" in RBAC and "page_except" in RS)
check("2g the frontend never spells the subtree marker as one literal — a plain text scan reads it "
      "as the start of a block comment, and two source-text locks read this file that way",
      ("'" + SUB + "'") not in RBAC and ('"' + SUB + '"') not in RBAC)

# ── 3. THE API GATE IS INSTALLED AND UNCONDITIONAL ───────────────────────────────────────────────
print("\n3. the API gate")
check("3a the middleware exists", "class RepSurfaceMiddleware" in TM)
check("3b it is registered in main.py", "app.add_middleware(RepSurfaceMiddleware)" in MAIN)
check("3c it is registered AFTER TenantScopeMiddleware ⇒ OUTER of it",
      MAIN.index("app.add_middleware(RepSurfaceMiddleware)")
      > MAIN.index("app.add_middleware(TenantScopeMiddleware)"))
mw = code_only(TM[TM.index("class RepSurfaceMiddleware"):])
check("3d it is NOT gated behind MULTI_TENANT_ENFORCE — a permission fix behind another flag is "
      "the browser-only gate with an env variable in front of it",
      "MULTI_TENANT_ENFORCE" not in mw and "_enabled()" not in mw)
ctl("3d can go red", "_enabled()" in code_only(TM))          # the flag IS read elsewhere in this file
check("3e it dereferences THE declaration and derives nothing itself",
      "rep_surface as _rs" in mw and "_rs.api_allowed(path, method)" in mw)
check("3f it asks the declaration BEFORE any identity work (a declared path costs no I/O)",
      mw.index("_rs.api_allowed") < mw.index("_resolve_identity"))
check("3g the acting tenant is the login's own membership, never the caller's org_id string",
      "_pick_active_org(member_orgs, default_org, requested)" in mw and "org_id" not in mw)
check("3h who is confined is THE one answer, not a second derivation",
      "role_is_self_scoped" in TM and "_rep_surface_refusal" in mw)
check("3i it fails OPEN on anything unexpected (★ NO LOCKOUT ★)",
      mw.count("return await self.app(scope, receive, send)") >= 6 and "except Exception" in mw)
check("3j the break-glass exists", "REP_CONFINE" in TM)
check("3k the refusal carries its own code so a client can tell it apart",
      '"code": "not_your_access"' in TM)

# ── 4. THE FOUR PERSON-WRITES STAY WIRED ─────────────────────────────────────────────────────────
print("\n4. the person-writes")
check("4a the gate is defined ONCE", SO.count("def require_person_write") == 1
      and SO.count("def person_write_allowed") == 1)
check("4b it dereferences the §14w one home and derives no span of its own",
      "schedule_emp_ids(authorization, org_id)" in
      SO[SO.index("def person_write_allowed"):SO.index("def require_person_write")])
check("4c manager authority is THE self-scope answer, not a role-name list",
      "role_is_self_scoped(org_id," in SO[SO.index("def require_person_write"):
                                          SO.index("def require_person_write") + 1200])
WIRED = {
    "add_manual_hours": (SO, 'require_person_write(authorization, org_id, employee_id, "Adjusting hours", manager_only=True)'),
    "delete_manual_hours": (SO, '"Removing an hours adjustment", manager_only=True'),
    "create_time_off": (SO, '"Filing time off"'),
    "create_shift_swap": (SO, '"Raising a shift swap"'),
    "onboarding_for_employee": (HR, '"Opening onboarding paperwork"'),
    "onboarding_set_profile": (HR, '"Changing onboarding paperwork"'),
}
for fn, (src, needle) in WIRED.items():
    check(f"4- {fn} still dereferences the gate", needle in src, "it stopped asking")
    check(f"4- {fn} takes an authorization header to ask with",
          bool(re.search(rf"def {fn}\(.*?authorization", src, re.S | re.M)))
check("4d the two hours writes are MANAGER-only; the ask-for-yourself writes are not",
      SO.count("manager_only=True") == 2)
ctl("4d can go red", "manager_only=True" in SO)
check("4e the import reaches HR from the one home, not a copy",
      "require_person_write" in HR and "def require_person_write" not in HR)

# ── 5. /me SERVES THE SAME DECLARATION ───────────────────────────────────────────────────────────
print("\n5. /me")
check("5a /me builds the payload from the one home", "rep_surface as _rs" in CORE
      and "_rs.viewer_payload(" in CORE)
check("5b it reuses the self-scope answer already computed, with no second read",
      "_rs.viewer_payload(_is_self," in CORE)
check("5c nothing else in the backend builds that payload",
      sum(1 for _ in re.finditer(r"viewer_payload\(", RS)) == 1
      and "def viewer_payload" in RS)

# ── 6. EVERY DECLARED PAGE IS A REAL PAGE ────────────────────────────────────────────────────────
print("\n6. the declared pages exist")
entries = [m for grp in re.findall(r'"pages":\s*\(([^)]*)\)', RS) for m in re.findall(r'"([^"]+)"', grp)]
entries += [m for grp in re.findall(r'"page_except":\s*\(([^)]*)\)', RS) for m in re.findall(r'"([^"]+)"', grp)]
# Declared, not counted: a page that leaves the declaration — or arrives in it — names itself here
# rather than sliding under a size floor. Index §CORE-32; the same rule harness_subject_set_lock.py
# enforces across every harness.
DECLARED_PAGES = {
    "/account/password/**",
    "/chat/**",
    "/commcalc/targets/action-plan",
    "/employee/**",
    "/helpdesk/**",
    "/helpdesk/dashboard",
    "/helpdesk/settings",
    "/my/**",
    "/portal/**",
    "/storeops/swaps",
    "/storeops/timeoff",
    "/training/**",
}
check("6a the declared pages are exactly the declared set",
      set(entries) == DECLARED_PAGES,
      "only in the code: %s / only in this lock: %s"
      % (sorted(set(entries) - DECLARED_PAGES), sorted(DECLARED_PAGES - set(entries))))
APP = os.path.join(ROOT, "frontend", "src", "app")
for e in sorted(set(entries)):
    route = e[:-3] if e.endswith("/**") else e
    rel = route.strip("/")
    found = any(os.path.isfile(os.path.join(APP, pre, rel, "page.tsx"))
                for pre in ("(platform)", "", "(auth)"))
    check(f"6- {e} has a page on disk", found, f"no page.tsx for {route}")
nav_hrefs = set(re.findall(r"href: '([^']+)'", RBAC))
check("6b the landing is registered in NAV, so it carries a page title like every other page",
      LANDING in nav_hrefs)

# ── 7. RULE TWO + EVERY TENANT ───────────────────────────────────────────────────────────────────
print("\n7. house policy, not a tenant's words")
check("7a the declaration's executing lines name no tenant, carrier or product",
      not any(w in code_only(RS).lower() for w in ("cellfonz", "luxelink", "vzone", "boost")))
check("7b who lands on the tiled page is derived from role CONFIG, never a role NAME",
      "LANDING_SCOPES" in RS and "LANDING_ROLES" not in RS and
      not re.search(r'"(sales_rep|store_manager)"', code_only(RS)))
ctl("7b can go red", 'LANDING_SCOPES = ("store",)' in RS)
check("7c the declaration's public functions take no org", not re.search(
    r"def (page_allowed|api_allowed|tiles|pages|refusal)\([^)]*org", RS))

print("\n" + "=" * 100)
print("%d passed, %d failed" % (P, F))
print("OK — the employee-reach lock holds." if not F else "FAIL — the employee-reach lock is open.")
sys.exit(1 if F else 0)
