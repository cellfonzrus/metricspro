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
sys.path.insert(0, HERE)
# This lock reads source TEXT for most of its checks. It also IMPORTS the declaration, for the one
# fact a text search must never re-derive: the address of a page-grammar pattern (`tile_page`). A
# private copy of that conversion in this very file is why the 2026-11-27 tile 404 shipped green.
from app.core import rep_surface as _rs                     # noqa: E402
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
DL = read("backend", "app", "modules", "storeops", "dropoff_label_api.py")

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
      "REP_SUBTREE = '/' + REP_STAR + REP_STAR" in RBAC and f'SUBTREE = "{SUB}"' in RS
      and "page_except" in RBAC and "page_except" in RS)
check("2g the frontend never spells the subtree marker as one literal, nor its two stars as one — a "
      "plain text scan reads the first as the start of a block comment and the second as a filename "
      "glob, and THREE source-text locks read this file one of those two ways",
      ("'" + SUB + "'") not in RBAC and ('"' + SUB + '"') not in RBAC
      and "'" + "**" + "'" not in RBAC and '"' + "**" + '"' not in RBAC)

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
# 4c THE ONE HOME for "this act is a manager's". Extracted 2026-11-20 from the `manager_only`
# branch, because the first act that needed it was not about a person at all (a tenant DECLARING a
# drop-off label type), and passing that through `require_person_write` would have refused managers
# too — so a copy of the role test would have appeared beside it. Both halves are pinned: the one
# home asks the self-scope answer, and `require_person_write` dereferences the one home.
def body_of(src, name):
    """One top-level function's text, from its `def` to the next one. A CHARACTER WINDOW was used
    here before and it is the wrong tool: it silently stops at an arbitrary offset, so a docstring
    that grows moves the thing being pinned out of view and the check goes red for no reason the
    reader can see (it did, 2026-11-20). The function's own extent is the honest boundary, and a
    stricter one — nothing from the NEXT function can satisfy a check about this one."""
    i = src.index("def %s(" % name)
    nxt = re.search(r"^(?:def |class |@)", src[i + 4:], re.M)
    return src[i: i + 4 + nxt.start()] if nxt else src[i:]


_RNIC = "def require_not_individual_contributor"
check("4c manager authority is THE self-scope answer, not a role-name list, asked in ONE place",
      SO.count(_RNIC) == 1
      and "role_is_self_scoped(org_id," in body_of(SO, "require_not_individual_contributor"))
check("4c2 require_person_write dereferences that one home rather than re-spelling the role test",
      "require_not_individual_contributor(authorization, org_id, what)" in
      body_of(SO, "require_person_write")
      and "role_is_self_scoped" not in body_of(SO, "require_person_write"))
check("4c3 the label-type writes ask it too, so the rep_surface carve-out is not the only gate",
      "require_not_individual_contributor" in DL and _RNIC not in DL
      and '_require_manager_act(authorization, org_id, "Declaring a label type")' in DL
      and '_require_manager_act(authorization, org_id, "Retiring a label type")' in DL)
check("4c4 …and the route that asks takes an authorization header to ask with",
      "def retire_profile(profile_id: str, authorization: str = Header(default=\"\")" in DL)
WIRED = {
    "add_manual_hours": (SO, 'require_person_write(authorization, org_id, employee_id, "Adjusting hours", manager_only=True)'),
    "delete_manual_hours": (SO, '"Removing an hours adjustment", manager_only=True'),
    "create_time_off": (SO, '"Filing time off"'),
    "create_shift_swap": (SO, '"Raising a shift swap"'),
    # The ANSWER to each ask, gated by the security pass on this change (2026-11-04). The grant was
    # on the collection; these live below it and had no authority of their own.
    "update_time_off": (SO, '"Answering a time-off request", manager_only=True'),
    "update_shift_swap": (SO, '"Answering a shift swap", manager_only=True'),
    "reconcile_timeoff_duplicates": (SO, "mgr = _require_manager(authorization, org_id)"),
    "onboarding_for_employee": (HR, '"Opening onboarding paperwork"'),
    "onboarding_set_profile": (HR, '"Changing onboarding paperwork"'),
}
for fn, (src, needle) in WIRED.items():
    check(f"4- {fn} still dereferences the gate", needle in src, "it stopped asking")
    check(f"4- {fn} takes an authorization header to ask with",
          bool(re.search(rf"def {fn}\(.*?authorization", src, re.S | re.M)))
# DECLARED, not counted: a route that stops being manager-only — or a new one that quietly becomes
# manager-only — names itself here. (A bare count went blind to exactly that; same lesson as
# harness_subject_set_lock.py.)
MANAGER_ONLY = {"Adjusting hours", "Removing an hours adjustment",
                "Answering a time-off request", "Answering a shift swap",
                # Added by the 2026-11-27 security pass (§STOREOPS-07.8). Both are acts ABOUT a
                # person that an individual contributor must not perform even on THEMSELVES:
                # setting withholding changes what comes out of a paycheck, and a rep writing their
                # own shift is the same shape as a rep adjusting their own hours — the thing the
                # owner stopped. Neither is an ASK, so §4f is untouched.
                "Changing payroll tax settings", "Creating a shift"}
_mo = set(re.findall(r'"([^"]+)", manager_only=True', SO))
check("4d the manager-only writes are exactly the declared set — the ask-for-yourself writes are "
      "NOT manager-only, or an employee could not ask",
      _mo == MANAGER_ONLY,
      "only in code: %s / only in this lock: %s" % (sorted(_mo - MANAGER_ONLY),
                                                    sorted(MANAGER_ONLY - _mo)))
ctl("4d can go red", "manager_only=True" in SO)
check("4f the ASK is never manager-only — a confined employee must be able to raise one",
      "'\"Filing time off\", manager_only" not in SO
      and '"Filing time off", manager_only' not in SO
      and '"Raising a shift swap", manager_only' not in SO)
check("4e the import reaches HR from the one home, not a copy",
      "require_person_write" in HR and "def require_person_write" not in HR)

# ── 4g. A GRANT ON A COLLECTION IS NOT A GRANT ON ITS MEMBERS ────────────────────────────────────
# The security pass on this very change found the first draft granting whole API prefixes for every
# method, so the POST that ASKS for time off also carried the PATCH that APPROVES it. The carve-out
# is the fix; these pin that it cannot quietly stop being asked, and that its grammar stays the one
# that makes sense for a member (strictly BELOW the collection, unlike a page subtree).
print("\n4g the API carve-out")
check("4g1 the declaration carves manager routes out of its granted prefixes",
      '"api_except"' in RS and "def api_exceptions" in RS)
check("4g2 the carve-out is asked FIRST in api_allowed, before any grant can answer",
      re.search(r"def api_allowed.*?api_except_matches.*?UNIVERSAL_API", RS, re.S) is not None)
check("4g3 …and in surface_for_api too, so a trace never names a surface the gate refuses",
      re.search(r"def surface_for_api.*?api_except_matches", RS, re.S) is not None)
check("4g4 a member carve-out is STRICTLY below its collection — carving the approve must not "
      "refuse the ask",
      "def _api_except_matches" in RS
      and 'return p.startswith(_norm(e[: -len(SUBTREE)]) + "/")' in RS)
API_EXCEPT = {
    "/api/v1/storeops/timeclock/attendance-config", "/api/v1/storeops/timeclock/geo-config",
    "/api/v1/storeops/timeclock/face-config", "/api/v1/storeops/timeclock/face-retention/**",
    "/api/v1/storeops/timeclock/lunch-config", "/api/v1/storeops/timeclock/permissions/**",
    "/api/v1/storeops/timeclock/force-clockout/**",
    "/api/v1/storeops/time-off/**", "/api/v1/storeops/shift-swaps/**",
    "/api/v1/storeops/shift-extensions/**",
    # The two helpdesk carve-outs are GONE as of 2026-11-27 (§CORE-33.6) and must not come back:
    # the grant they carved — the whole `/api/v1/helpdesk` module, every method — was replaced by the
    # ticket routes plus a read of the form's vocabulary. Carving what was never granted is "a
    # comment pretending to be a gate", and that carve-out was also REFUSING `/config/bootstrap`,
    # which all three employee screens fetch on mount. See 4g7.
    "/api/v1/closing/row/**", "/api/v1/chat/admin/**",
    # 2026-11-20: a tenant's drop-off label DECLARATIONS. The rescue was opened to a confined
    # employee; retiring one of the organisation's declarations was not.
    "/api/v1/storeops/dropoff-label/profiles/**",
}
_ae = {m for grp in re.findall(r'"api_except":\s*\(([^)]*)\)', RS)
       for m in re.findall(r'"([^"]+)"', grp)}
check("4g5 the carved routes are exactly the declared set",
      _ae == API_EXCEPT,
      "only in code: %s / only in this lock: %s" % (sorted(_ae - API_EXCEPT),
                                                    sorted(API_EXCEPT - _ae)))
check("4g6 the closing grant is NAMED routes, not the whole /closing prefix (which carried every "
      "tender, cash, deposit and sweep config write)",
      '"api": ("/api/v1/closing/row", "/api/v1/closing/envelope-photo",' in RS
      and '"api": ("/api/v1/closing",)' not in RS)
# 4g7 — THE HELPDESK IS GRANTED BY WHAT ITS SCREENS CALL (§CORE-33.6). The settings screen is still
# carved out on the PAGE side; on the API side the module prefix is no longer granted at all, so the
# settings writes, the agent console and the manager stats are refused by default deny rather than by
# a carve-out. The form's own vocabulary is a READ, which is what the employee screens were missing.
check("4g7 the helpdesk settings screen is refused on BOTH sides",
      "/helpdesk/settings" in RS
      and '"api": ("/api/v1/helpdesk",)' not in RS
      and '"api": ("/api/v1/helpdesk/tickets",)' in RS
      and not _rs.api_allowed("/api/v1/helpdesk/config/settings", "PUT")
      and not _rs.api_allowed("/api/v1/helpdesk/config/categories", "GET")
      and not _rs.api_allowed("/api/v1/helpdesk/support/queue", "GET")
      and not _rs.api_allowed("/api/v1/helpdesk/stats/dashboard", "GET"))
check("4g7b …and the ticket form's vocabulary is granted read-only",
      _rs.api_allowed("/api/v1/helpdesk/config/bootstrap", "GET")
      and not _rs.api_allowed("/api/v1/helpdesk/config/bootstrap", "POST"))


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
# rather than sliding under a size floor. Index §CORE-33; the same rule harness_subject_set_lock.py
# enforces across every harness.
DECLARED_PAGES = {
    "/account/password/**",
    "/chat/**",
    "/closing/submit",
    "/commcalc/targets/action-plan",
    "/commcalc/targets/my",
    # The rep's own phone shelf and the counter device lookup, each its own page with its own tile
    # (owner 2026-11-27, §CORE-33.7). The shelf was a CARD on My targets and went with it when a
    # tenant switched that page off; the lookup is a page the UPS business type hides and the
    # wireless one does not, which is the whole of the owner's "not on the portal" / "missing from
    # Ali's dashboard" pair.
    "/commcalc/asset/my-shelf",
    "/commcalc/device-history",
    "/employee/**",
    "/helpdesk/**",
    "/helpdesk/dashboard",
    "/helpdesk/settings",
    "/my/**",
    # The employee's own schedule (owner 2026-11-27, §CORE-33.2). Already inside `/my/**`, and
    # declared on its OWN surface anyway because a tile's link is its surface's FIRST page — the
    # tile labelled "My schedule" used to open `/storeops/timeoff`, the time-off request form.
    "/my/schedule",
    "/portal/**",
    "/storeops/dropoff-barcode",
    "/storeops/swaps",
    "/storeops/timeoff",
    "/training/**",
}
check("6a the declared pages are exactly the declared set",
      set(entries) == DECLARED_PAGES,
      "only in the code: %s / only in this lock: %s"
      % (sorted(set(entries) - DECLARED_PAGES), sorted(DECLARED_PAGES - set(entries))))
APP = os.path.join(ROOT, "frontend", "src", "app")


def _on_disk(route):
    rel = str(route or "").strip("/")
    return any(os.path.isfile(os.path.join(APP, pre, rel, "page.tsx"))
               for pre in ("(platform)", "", "(auth)"))


for e in sorted(set(entries)):
    # THE ADDRESS OF A PATTERN HAS ONE HOME, and this lock dereferences it rather than carrying its
    # own slice of the pattern. That private copy is why the 404 of 2026-11-27 shipped green:
    # this lock knew a
    # pattern is not an address and `tiles()` did not, so the pages all existed on disk while the
    # tiles linked to patterns of them.
    route = _rs.tile_page(e)
    check(f"6- {e} has a page on disk", _on_disk(route), f"no page.tsx for {route}")

# ── 6c. A TILE'S LINK IS AN ADDRESS, NOT A PATTERN ───────────────────────────────────────────────
# The owner's report, 2026-11-27: a confined employee tapped "My commissions" and then "Clock in /
# out" on the landing and got the app's 404 — on NY Logistics, and on every tenant, because the tile
# published the permission pattern (`/employee/**`, `/my/**`) as its href. Three facts are pinned so
# it cannot return: every tiled surface DECLARES where it goes, that destination is navigable, and
# the browser is handed nothing it cannot open.
print("\n6c a tile's link is an address")
TILES = _rs.tiles()
check("6c1 every tiled surface declares its own destination",
      all(s.get("tile_page") for s in _rs.SURFACES if s.get("tile")),
      [s["key"] for s in _rs.SURFACES if s.get("tile") and not s.get("tile_page")])
check("6c2 THE 404: no tile publishes a pattern as its link",
      all(t["page"] and "*" not in t["page"] for t in TILES), [t["page"] for t in TILES])
for t in TILES:
    check(f"6c- the {t['key']} tile opens a real page", _on_disk(t["page"]),
          f"no page.tsx for {t['page']}")
check("6c3 …and every destination is one the gate admits",
      all(_rs.page_allowed(t["page"]) for t in TILES))
# A GRANTED PAGE WITH NO TILE IS UNREACHABLE (owner 2026-11-27, three reports in ten minutes). A
# confined employee has no menu row, so the tiles are the only way in: a surface that declares a page
# and no tile can be opened only by typing its address.
check("6c7 every surface that declares a page has a tile",
      [s["key"] for s in _rs.SURFACES if s["pages"] and not s.get("tile")] == [],
      [s["key"] for s in _rs.SURFACES if s["pages"] and not s.get("tile")])
check("6c4 the destination is DECLARED, never guessed from the first grant — no pages[0] fallback "
      "survives in the tile builder",
      'pages"][0]' not in RS.split("def tiles(")[1].split("\ndef ")[0])
check("6c5 the pattern → address conversion is ONE function the builder dereferences",
      RS.count("def tile_page(") == 1 and "tile_page(s[\"tile_page\"])" in RS)
ctl("6c5 can go red", "def tiles(" in RS)
MINE = open(os.path.abspath(__file__), encoding="utf-8").read()
# The forbidden spelling is assembled, never written, so this check cannot trip on itself.
_SLICE_COPY = "[" + ":-3" + "]"
check("6c6 and this lock keeps no second copy of it",
      MINE.count("_rs.tile_page(") >= 1 and _SLICE_COPY not in MINE)
ctl("6c6 can go red", _SLICE_COPY in "route = e" + _SLICE_COPY)
nav_hrefs = set(re.findall(r"href: '([^']+)'", RBAC))
check("6b the landing is registered in NAV, so it carries a page title like every other page",
      LANDING in nav_hrefs)

# ── 6d. A GRANTED PAGE MAY NOT CALL AN API THE GATE REFUSES ──────────────────────────────────────
# The owner, 2026-11-27, opening the tile that had just been added for him: *"on employee dashboard
# it shows Inventory aging could not be loaded (This is not part of your access. GET
# /api/v1/commcalc/my-shelf belongs to a manager screen…)"*. Same shape as the device lookup the day
# before (§CORE-33.3): THE BROWSER OFFERED WHAT THE API REFUSES. Granting the page is half a grant;
# the cards on it ask the server too, and a refused card is what the employee actually sees.
#
# So this walks every page a confined employee may open, reads the API paths its own code calls —
# comments stripped, `const BASE = "/api/v1/…"` templates expanded — and asks the gate itself. A
# refused call is a FAILED BUILD unless it is excused here BY NAME, with the reason the screen never
# makes it for this viewer. Four screens were already broken when this check was written (the shelf
# card, the ticket form's vocabulary, the time-off policy read, and the personnel roster two pages
# used as a name picker); each is a grant or a rewrite in this same change, not an excuse.
print("\n6d every API a granted page calls is granted")
PLAT = os.path.join(ROOT, "frontend", "src", "app", "(platform)")
# EXCUSED — a call the screen makes only for a viewer this is not. The reason is the point: an excuse
# without one is how a real refusal gets filed as noise.
API_EXCUSED = {
    "/api/v1/core/users": "the helpdesk Assignee roster, fetched only when `isAgent` (the ticket "
                          "page returns early for a requester)",
    "/api/v1/chat/admin/retention": "the chat retention purge, inside the admin-only control block",
    "/api/v1/storeops/dropoff-label/scans": "the audit list of everyone's rescues — manager only, "
                                            "and the page renders no list for a confined viewer",
    "/api/v1/storeops/dropoff-label": "the collection itself is never called; every call is a "
                                      "member of it (`${BASE}/read`, `${BASE}/barcodes`)",
}
_CALL = re.compile(r"/api/v1/[A-Za-z0-9/_.-]+")


def _page_files(route, subtree):
    """The files that render `route`: its own page/layout/components, plus — for a SUBTREE grant —
    the child routes the gate still admits. A carved-out child (`/helpdesk/settings`) is not this
    viewer's page, so its calls are not this viewer's calls."""
    base = os.path.join(PLAT, route.lstrip("/"))
    out = []
    if not os.path.isdir(base):
        return out
    for root, dirs, files in os.walk(base):
        rel = os.path.relpath(root, base)
        if rel != ".":
            parts = [p for p in rel.split(os.sep)]
            # `_components` and friends are not routes; a route segment is anything else.
            if any(not p.startswith("_") for p in parts):
                child = route.rstrip("/") + "/" + "/".join(parts)
                if not subtree or not _rs.page_allowed(child):
                    dirs[:] = []
                    continue
        out.extend(os.path.join(root, f) for f in files if f.endswith((".tsx", ".ts")))
    return out


def _calls(src):
    """Every API path the code (not the prose) asks for, with `const BASE = "/api/v1/…"` templates
    resolved — a lock that cannot see through one literal indirection is a lock that passes while
    the screen is broken."""
    code = code_only(src, "ts")
    found = set()
    for name, lit in re.findall(r"const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[\'\"`](/api/v1/[^\'\"`]+)", code):
        for tail in re.findall(r"\$\{" + name + r"\}([A-Za-z0-9/_.-]*)", code):
            found.add((lit.rstrip("/") + tail).rstrip("/"))
    for m in _CALL.findall(code):
        found.add(m.split("${")[0].rstrip("/"))
    return {p for p in found if p.count("/") > 3 or p.startswith("/api/v1/")}


REFUSED_CALLS = []
for e in _rs.pages():
    route, subtree = _rs.tile_page(e), e.endswith("/**")
    for f in _page_files(route, subtree):
        for path in sorted(_calls(open(f, encoding="utf-8", errors="replace").read())):
            if _rs.api_allowed(path, "GET") or path in API_EXCUSED:
                continue
            REFUSED_CALLS.append((route, path, os.path.relpath(f, ROOT)))
check("6d1 no granted page calls an API the gate refuses",
      not REFUSED_CALLS,
      "; ".join(f"{r}: {p} ({f})" for r, p, f in REFUSED_CALLS[:6]))
ctl("6d1 can go red", not _rs.api_allowed("/api/v1/commcalc/report-cards", "GET"))
check("6d2 every excuse names its reason", all(len(v) > 25 for v in API_EXCUSED.values()))
# An excuse for a path the gate now GRANTS is stale: it reads as "the screen must not call this"
# while the screen may. Delete it rather than leave a contradiction in the file.
STALE = [p for p in API_EXCUSED if _rs.api_allowed(p, "GET")]
check("6d3 no excuse survives its own grant", not STALE, STALE)
# THE REPORTED DEFECT, as its own regression.
check("6d4 the rep's own shelf is granted to the screen that shows it",
      _rs.api_allowed("/api/v1/commcalc/my-shelf", "GET")
      and not _rs.api_allowed("/api/v1/commcalc/my-shelf", "POST"))

# ── 6e. THE ADMIN'S SWITCH CAN CLOSE WHAT THIS FILE OPENS ────────────────────────────────────────
# The owner, 2026-11-27: *"i disaables my targets from ny logistics sales reps but it still shows
# up"*. He had switched the page off for that role and the tile stayed, the page opened and the API
# answered — because for a confined viewer this declaration was consulted as the WHOLE truth, so a
# tenant's own administrator could not take away what it gave. These pin the subtraction in all
# three places at once, because a switch honoured in two of them is the browser-only gate again.
print("\n6e the role's own switch")
OFF = {"pages": {"/commcalc/targets/my": False}}
check("6e1 the switch closes the TILE",
      "targets" in [t["key"] for t in _rs.tiles()]
      and "targets" not in [t["key"] for t in _rs.tiles(OFF)])
check("6e2 …the PAGE", _rs.page_allowed("/commcalc/targets/my")
      and not _rs.page_allowed("/commcalc/targets/my", OFF))
# The shelf read is granted by TWO surfaces now (the targets page still carries the card, and the
# shelf has its own page), so closing one of them must not close the read — and closing BOTH must.
OFF_BOTH = {"pages": {"/commcalc/targets/my": False, "/commcalc/asset/my-shelf": False}}
check("6e3 …and the API the page calls, once every page that calls it is closed",
      _rs.api_allowed("/api/v1/commcalc/my-shelf", "GET")
      and _rs.api_allowed("/api/v1/commcalc/my-shelf", "GET", role_perms=OFF)
      and not _rs.api_allowed("/api/v1/commcalc/my-shelf", "GET", role_perms=OFF_BOTH))
check("6e3b …and the device lookup goes with its own page",
      _rs.api_allowed("/api/v1/commcalc/device-history", "GET")
      and not _rs.api_allowed("/api/v1/commcalc/device-history", "GET",
                              role_perms={"pages": {"/commcalc/device-history": False}}))
check("6e4 a switch can only SUBTRACT — turning a refused page ON does not grant it",
      not _rs.page_allowed("/commcalc/report-cards", {"pages": {"/commcalc/report-cards": True}})
      and not _rs.api_allowed("/api/v1/commcalc/report-cards", "GET",
                              role_perms={"pages": {"/commcalc/report-cards": True}}))
check("6e5 only an explicit False counts — absent, True and a non-dict leave the declaration whole",
      all(len(_rs.tiles(p)) == len(_rs.tiles())
          for p in ({}, {"pages": {}}, {"pages": {"/commcalc/targets/my": True}},
                    {"pages": "nonsense"}, None)))
check("6e6 ★ NO LOCKOUT ★ the password surface is never switchable",
      len(_rs.tiles({"pages": {"/account/password": False}})) == len(_rs.tiles())
      and _rs.page_allowed("/account/password", {"pages": {"/account/password": False}}))
check("6e7 closing one page of a surface leaves its siblings open — the time-off form is not the "
      "schedule beside it",
      _rs.page_allowed("/my/schedule", {"pages": {"/storeops/timeoff": False}})
      and not _rs.page_allowed("/storeops/timeoff", {"pages": {"/storeops/timeoff": False}})
      and _rs.api_allowed("/api/v1/storeops/shifts", "GET",
                          role_perms={"pages": {"/storeops/timeoff": False}}))
check("6e8 the UNIVERSAL shell is never subtracted",
      all(_rs.api_allowed(u, "GET", role_perms={"pages": {p: False for p in
                                                          [_rs.tile_page(x) for x in _rs.pages()]}})
          for u in _rs.UNIVERSAL_API))
check("6e9 /me hands the browser the role's own switches, with no second read",
      "role_perms=perms" in CORE)
check("6e10 the API middleware asks the switch too — and only when a surface call makes it matter",
      "api_switchable(" in TM and "_role_switches(" in TM
      and "def api_switchable" in RS
      and not _rs.api_switchable("/api/v1/core/me", "GET")
      and _rs.api_switchable("/api/v1/commcalc/my-shelf", "GET"))
check("6e11 the switch has ONE home — nothing else reads the permissions key",
      RS.count("ROLE_PAGE_SWITCH_KEY") == 2
      and "def switched_off" in RS and RBAC.count("rep_surface") >= 1
      and '"pages"]' not in TM)
ctl("6e can go red", _rs.page_allowed("/commcalc/targets/my"))

# ── 6f. AND THE TENANT'S BUSINESS TYPE ───────────────────────────────────────────────────────────
# The owner, 2026-11-27, on a rep of the NY LOGISTICS tenant — which is registered as The UPS Store:
# *"barcode resue does not belong to the wireless stors it is only for the ups store, also the device
# history is also missing from alis dashbpard"*. Both halves are one fact: what a tenant IS was
# already declared (`core.tenant_vertical.nav_hidden`, §35) and already obeyed by the sidebar; the
# tiles never asked. These pin that they ask, that they ask through the ONE matching rule, and that
# this file still spells no vertical (RULE TWO).
print("\n6f the tenant's business type")
VERT = read("backend", "app", "modules", "core", "verticals.py")
MIG = read("database", "migrations", "1136_business_type_rep_tiles.sql")
_HIDE = ["/commcalc/asset/my-shelf", "/commcalc/device-history", "/storeops/dropoff-barcode"]
check("6f1 a hidden page has no tile",
      {"shelf", "device_history", "dropoff"} <= {t["key"] for t in _rs.tiles()}
      and not ({"shelf", "device_history", "dropoff"} & {t["key"] for t in _rs.tiles(None, _HIDE)}))
check("6f2 …nor its page", _rs.page_allowed("/storeops/dropoff-barcode")
      and not _rs.page_allowed("/storeops/dropoff-barcode", nav_hidden=_HIDE))
check("6f3 …nor the API that page calls",
      _rs.api_allowed("/api/v1/storeops/dropoff-label/read", "POST")
      and not _rs.api_allowed("/api/v1/storeops/dropoff-label/read", "POST", nav_hidden=_HIDE))
check("6f4 the matching rule is verticals.href_hidden, dereferenced — not a second copy here",
      "href_hidden" in RS and "def href_hidden" in VERT and RS.count("def href_hidden") == 0)
check("6f5 both subtractions are ONE answer every caller asks",
      "def page_off" in RS and RS.count("def page_off") == 1
      and all(f"page_off(" in RS.split(f"def {fn}(")[1].split("\ndef ")[0]
              for fn in ("tiles", "pages", "surface_switched_off")))
check("6f6 the shell is never subtracted by a business type",
      all(_rs.api_allowed(u, "GET", nav_hidden=[_rs.tile_page(p) for p in _rs.pages()])
          for u in _rs.UNIVERSAL_API))
check("6f7 /me hands the tiles the SAME nav_hidden the nav gate reads, with no second read",
      "nav_hidden=_nav_hidden" in CORE and '("vertical") or {}).get("nav_hidden")' in CORE)
check("6f8 the API gate asks it too, cached beside the role's switches",
      "_tenant_nav_hidden(" in TM and "def _tenant_nav_hidden" in TM
      and "nav_hidden=hidden" in TM)
# THE SPLIT ITSELF IS CONFIG. The migration is the only place the two business types' page lists
# live (plus the byte-equal mirror the vertical harness parses back), and no vertical key appears in
# any declaration or gate.
check("6f9 the UPS/wireless split is a config row, not code",
      "/storeops/dropoff-barcode" in MIG and "wireless_retail" in MIG
      and "/commcalc/asset/my-shelf" in MIG and "ups_store" in MIG
      and "wireless_retail" not in RS and "ups_store" not in RS)
ctl("6f can go red", _rs.page_allowed("/storeops/dropoff-barcode"))

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
