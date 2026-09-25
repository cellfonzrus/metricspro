"""PROOF + LOCK: the Super Admin Toolbox — one tiled page holding every platform-super-admin screen.

OWNER 2026-09-25, verbatim: *"create a super admin toolbox and duplicate all the items which are
needed by the superadmin only into that toolbox under different tiles to make it easy"*.

DUPLICATE CHECK (index-first). Nothing new was invented for the page itself:
  • the tiled page IS the generic hub (`/hub/[group]`, index §14 D2) — the toolbox is one more NAV
    group, rendered by `HubTiles`, re-arrangeable in the Dashboard Designer (module key
    'super-admin-toolbox'), saved through the existing `PUT /commcalc/tile-layout`;
  • "duplicate, not move" is the existing Flags & Compliance precedent — a `tileOnly` COPY of a NAV
    item whose real home stays where it was.
The two genuinely new facts are both DATA on the NAV literal:
  • `NavGroup.platformOnly` — the whole group is for the platform super admin
    (`AppUser.super_admin`, what the backend's `_require_super_admin` reads), NOT `modules.admin`,
    which every tenant admin role holds;
  • `NavItem.tile` — the built-in master tile an item sits on (`tile-hubs.subsFromItemTiles`), so
    the toolbox arrives already tiled with no saved layout and no migration.

WHAT THIS PINS
  A. the toolbox group exists, is `platformOnly`, opens with its own `/hub/…` entry, and every other
     item is a `tileOnly` copy with a `tile` name (≥ 5 distinct tiles — "under different tiles");
  B. DUPLICATE, NOT MOVED — every copy keeps a home: another NAV group carries the same href with the
     same module + scopes (a copy can never widen access), or it is a page of the /operator console;
  C. every href is a real route (a tile can never be a dead link);
  D. THE ONE GATE — `platformOK` reads `super_admin` (never `modules.admin`), `TENANT_NAV` drops every
     platform-only group, and the hub precedence is saved layout > menu subs > item tiles;
  E. EVERY NAV CONSUMER DEREFERENCES IT — a file that walks the bare `NAV` must call `platformOK`
     (sidebar + search, hub, designer) or be on the justified href-lookup list; the tenant
     configuration screens (roles, menu, labels, business types, walk-throughs, help docs, ScreenLink)
     read `TENANT_NAV`. A new consumer that forgets fails HERE, not in a tenant admin's sidebar;
  F. the index registers it.

PURE / DB-FREE: parses the real .ts/.tsx sources as text; stdlib only.
"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FE = os.path.join(ROOT, "frontend", "src")
RBAC = os.path.join(FE, "lib", "rbac.ts")
HUBS = os.path.join(FE, "lib", "tile-hubs.ts")
APP = os.path.join(FE, "app")
INDEX = os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md")

GROUP = "Super Admin Toolbox"
SLUG = "super-admin-toolbox"

FAILS = []


def check(name, ok, why=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  -- " + why))
    if not ok:
        FAILS.append(name)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def code_only(src):
    """Strip // line comments and /* */ blocks so prose never satisfies (or trips) a check."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", r"\1", ln) for ln in src.split("\n"))


rbac = read(RBAC)


def nav_groups(src):
    """[(group, header_text, [item_text…])] from the NAV literal — each chunk runs from one top-level
    `  { group: '…'` line to the next (or to the literal's closing `]`)."""
    body = src[src.index("export const NAV: NavGroup[] = ["):]
    body = body[:body.index("\n]\n")]
    heads = list(re.finditer(r"^  \{ group: '([^']+)'([^\n]*)$", body, re.M))
    out = []
    for n, m in enumerate(heads):
        chunk = body[m.end():heads[n + 1].start() if n + 1 < len(heads) else len(body)]
        items = re.findall(r"^\s*(\{ href: '[^\n]*\}),?\s*$", chunk, re.M)
        out.append((m.group(1), m.group(2), items))
    return out


def field(item, key):
    m = re.search(key + r": '([^']*)'", item)
    return m.group(1) if m else None


def scopes(item):
    m = re.search(r"scopes: \[([^\]]*)\]", item)
    return tuple(sorted(re.findall(r"'([^']+)'", m.group(1)))) if m else None


groups = nav_groups(rbac)
by_name = {g: (hdr, items) for g, hdr, items in groups}
print("NAV: %d group(s) parsed" % len(groups))
check("A0 the parser sees every NAV group", len(groups) == len(re.findall(r"^  \{ group: '", rbac, re.M)) >= 20,
      "NAV literal shape changed — fix nav_groups before trusting §B")

# ── A. structure ─────────────────────────────────────────────────────────────────────────────────
print("A. the toolbox group")
check("A1 the '%s' NAV group exists" % GROUP, GROUP in by_name, "group missing from rbac.ts NAV")
hdr, items = by_name.get(GROUP, ("", []))
check("A2 the group is platformOnly", "platformOnly: true" in hdr, "missing platformOnly: true")
check("A3 its first item is the hub entry /hub/%s (not tileOnly)" % SLUG,
      bool(items) and field(items[0], "href") == "/hub/" + SLUG and "tileOnly" not in items[0],
      "first item must be the sidebar door to the tiled page")
copies = items[1:]
check("A4 the toolbox carries at least 20 super-admin screens", len(copies) >= 20, "only %d" % len(copies))
bad = [field(i, "href") for i in copies if "tileOnly: true" not in i or not field(i, "tile")]
check("A5 every copy is tileOnly and names its tile", not bad, "offenders: %s" % bad)
tiles = []
for i in copies:
    t = field(i, "tile")
    if t and t not in tiles:
        tiles.append(t)
check("A6 the copies spread over >= 5 distinct tiles (%s)" % ", ".join(tiles), len(tiles) >= 5,
      "owner asked for different tiles")
hrefs = [field(i, "href") for i in copies]
check("A7 no screen is listed twice inside the toolbox", len(hrefs) == len(set(hrefs)),
      "duplicates: %s" % sorted({h for h in hrefs if hrefs.count(h) > 1}))
for must in ("/admin/tenants", "/admin/business-types", "/operator", "/admin/control-box",
             "/admin/billing", "/admin/pricing"):
    check("A8 the toolbox holds %s" % must, must in hrefs, "a core super-admin screen is missing")

# ── B. duplicate, not moved ──────────────────────────────────────────────────────────────────────
print("B. every copy keeps its real home (duplicate, not moved)")
homes = {}
for g, _h, its in groups:
    if g == GROUP:
        continue
    for i in its:
        homes.setdefault(field(i, "href"), []).append((g, field(i, "module"), scopes(i)))
operator_dir = os.path.join(APP, "(operator)")
for i in copies:
    h = field(i, "href")
    if h in homes:
        same = [x for x in homes[h] if x[1] == field(i, "module") and x[2] == scopes(i)]
        check("B1 %-28s home %-22s same module+scopes" % (h, homes[h][0][0]), bool(same),
              "copy differs from its home %s — a copy must never widen or narrow access" % homes[h])
    else:
        is_op = h == "/operator" or h.startswith("/operator/")
        check("B2 %-28s is an /operator console page" % h, is_op,
              "no other NAV group carries it — that is a MOVE, not a duplicate")

# ── C. routes exist ──────────────────────────────────────────────────────────────────────────────
print("C. every tile link is a real route")
for h in hrefs:
    rel = h.strip("/")
    cands = [os.path.join(APP, grp, rel, "page.tsx") for grp in ("(platform)", "(operator)")]
    check("C1 %-28s has a page" % h, any(os.path.isfile(c) for c in cands), "no page.tsx for it")
check("C2 the generic hub route renders it", os.path.isfile(os.path.join(APP, "(platform)", "hub", "[group]", "page.tsx")),
      "hub route missing")
check("C3 the hub slug of '%s' is '%s'" % (GROUP, SLUG),
      re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", GROUP.lower())) == SLUG, "slug drift")

# ── D. the one gate ──────────────────────────────────────────────────────────────────────────────
print("D. the one gate")
rc = code_only(rbac)
m = re.search(r"export function platformOK\([^)]*\)[^{]*\{(.*?)\n\}", rc, re.S)
check("D1 platformOK is defined in rbac.ts", bool(m), "missing")
body = (m.group(1) if m else "")
m2 = re.search(r"export function isPlatformAdmin\([^)]*\)[^{]*\{(.*?)\n\}", rc, re.S)
check("D2 isPlatformAdmin reads user.super_admin", bool(m2) and "super_admin" in m2.group(1), "wrong flag")
check("D3 platformOK goes through isPlatformAdmin and the group's platformOnly",
      "isPlatformAdmin(" in body and "platformOnly" in body, "gate bypassed")
check("D4 neither reads modules.admin / isSuperAdmin (every tenant admin holds it)",
      "isSuperAdmin" not in body and "modules" not in body and (not m2 or "modules" not in m2.group(1)),
      "tenant admins would see the toolbox")
check("D5 TENANT_NAV = NAV without platform-only groups",
      bool(re.search(r"export const TENANT_NAV: NavGroup\[\] = NAV\.filter\(g => !g\.platformOnly\)", rc)), "missing")
hubs = code_only(read(HUBS))
check("D6 tile-hubs.subsFromItemTiles exists", "export function subsFromItemTiles(" in hubs, "missing")
hub_page = code_only(read(os.path.join(APP, "(platform)", "hub", "[group]", "page.tsx")))
check("D7 hub precedence: saved layout, then menu subs, then item tiles",
      bool(re.search(r"layoutToHubGroups\(.*?subsFromNavLayout\(.*?menuSubs\.length \? menuSubs : subsFromItemTiles\(",
                     hub_page, re.S)), "precedence changed")

# ── E. every NAV consumer dereferences the gate ──────────────────────────────────────────────────
print("E. every NAV consumer asks platformOK / reads TENANT_NAV")
# href-lookup consumers that walk bare NAV for a FIXED, non-platform group — justified one by one.
HREF_LOOKUP_OK = {
    os.path.join("app", "(platform)", "compliance", "page.tsx"):
        "finds its own fixed 'flags-compliance' group by slug; never lists other groups",
}
MUST_TENANT_NAV = [
    os.path.join("app", "(platform)", "admin", p, "page.tsx")
    for p in ("roles", "menu", "labels", "business-types", "training", os.path.join("support", "docs"))
] + [os.path.join("components", "ScreenLink.tsx")]
MUST_PLATFORM_OK = [
    os.path.join("app", "(platform)", "layout.tsx"),
    os.path.join("app", "(platform)", "hub", "[group]", "page.tsx"),
    os.path.join("app", "(platform)", "admin", "dashboards", "page.tsx"),
]
imp_re = re.compile(r"import\s*\{([^}]*)\}\s*from\s*'@/lib/rbac'", re.S)
consumers = []
for dp, _dn, fns in os.walk(FE):
    for fn in fns:
        if not fn.endswith((".ts", ".tsx")):
            continue
        p = os.path.join(dp, fn)
        rel = os.path.relpath(p, FE)
        if rel == os.path.join("lib", "rbac.ts"):
            continue
        src = read(p)
        names = [n.strip().split(" as ")[0] for m3 in imp_re.finditer(src) for n in m3.group(1).split(",")]
        if "NAV" in names:
            consumers.append((rel, code_only(src)))
print("   %d file(s) import bare NAV" % len(consumers))
for rel, src in consumers:
    ok = "platformOK(" in src or rel in HREF_LOOKUP_OK
    check("E1 %-44s gates platform-only groups" % rel, ok,
          "walks NAV without platformOK — use TENANT_NAV, or call platformOK(g, user)")
for rel in MUST_PLATFORM_OK:
    check("E2 %-44s calls platformOK" % rel, rel in dict(consumers) and "platformOK(" in dict(consumers)[rel],
          "the sidebar/hub/designer lost the gate")
for rel in MUST_TENANT_NAV:
    src = code_only(read(os.path.join(FE, rel)))
    names = [n.strip() for m3 in imp_re.finditer(src) for n in m3.group(1).split(",")]
    check("E3 %-44s reads TENANT_NAV" % rel, "TENANT_NAV" in names and "NAV" not in names,
          "a tenant configuration screen would list the platform copies")
lay = dict(consumers).get(os.path.join("app", "(platform)", "layout.tsx"), "")
check("E4 the sidebar filters groups with platformOK(g, user) before any rendering",
      ".filter(g => platformOK(g, user))" in lay, "sidebar + ⌘K search would show the toolbox to tenant admins")

# ── F. registered ────────────────────────────────────────────────────────────────────────────────
print("F. registered in the index")
idx = read(INDEX)
check("F1 index §38 documents the toolbox", "## 38. SUPER ADMIN TOOLBOX" in idx, "register it")
check("F2 index names this harness", "harness_super_admin_toolbox.py" in idx, "register the lock")

print()
print("%d FAIL(s)" % len(FAILS) if FAILS else "ALL PASS")
sys.exit(1 if FAILS else 0)
