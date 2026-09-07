"""PROOF: a dashboard tile may name a page from ANOTHER module, and nothing is dropped in silence.

OWNER 2026-09-07: *"I just added inventory aging and inventory values from finance to management
overview as additional view from the menu designer but they are not showing up, among some others I
did last week."*

THE DEFECT, both halves. `/hub/<group>` renders a designed tile layout through
`tile-hubs.ts::layoutToHubGroups(layout, visibleItems)`, which resolves every designed href against
`visibleItems` and DROPS what it cannot find:

    const nav = byHref.get(raw.href)
    if (!nav && !opts?.keepUnknown) continue        // <- silent
    ...
    if (!items.length && !opts?.keepUnknown) continue   // <- a whole tile disappears

`visibleItems` was built from `navGroup.items` — the ONE group being rendered. So a cross-module pick
could never resolve. Verified against the live nav:

    'Inventory Values'  /accounts/inventory        -> group **Finance**
    'Inventory Aging'   /commcalc/asset/aging      -> group **Assets**
    the dashboard being designed                   -> group **Management Overview**

Both would have been discarded at render with nothing said, and a tile containing only such picks
would have vanished whole. The DESIGNER had the matching half: its left panel offered only the
selected group's pages, so there was no supported way to place them in the first place.

WHY A STATIC HARNESS. The failure mode is a silent `continue`, which no type checker and no build
step can see — the page compiles, renders, and is simply missing rows. So this asserts the WIRING at
the source: that the hub resolves designed hrefs against every group, that the same viewer gate is
applied to that wider set, and that the auto-derived default tiles stay scoped to the group.

THE RULE THIS MUST NOT BREAK: widening what a DESIGNER may place must never widen what a VIEWER may
see. §C pins that the cross-module set passes through the identical RBAC / capability / carrier /
hidden filters as the group's own items.

PURE: stdlib only; reads the two frontend sources.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FRONT = os.path.join(os.path.dirname(HERE), "frontend", "src")
HUB = os.path.join(FRONT, "app", "(platform)", "hub", "[group]", "page.tsx")
DESIGNER = os.path.join(FRONT, "app", "(platform)", "admin", "dashboards", "page.tsx")
RBAC = os.path.join(FRONT, "lib", "rbac.ts")

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, detail))


def read(p):
    try:
        return open(p, encoding="utf-8").read()
    except Exception as e:
        return ""


hub, designer, rbac = read(HUB), read(DESIGNER), read(RBAC)

print("=" * 78)
print("A. The nav really does put these pages in other groups (the premise)")
print("=" * 78)
check("A0 all three sources are readable", bool(hub and designer and rbac))


def group_of(href):
    """Which NAV group declares `href`."""
    cur = None
    for line in rbac.split("\n"):
        m = re.search(r"group:\s*'([^']+)'", line)
        if m:
            cur = m.group(1)
        if f"href: '{href}'" in line:
            return cur
    return None


gv, ga = group_of("/accounts/inventory"), group_of("/commcalc/asset/aging")
check("A1 'Inventory Values' (/accounts/inventory) is NOT in Management Overview",
      gv is not None and gv != "Management Overview", f"group={gv}")
check("A2 'Inventory Aging' (/commcalc/asset/aging) is NOT in Management Overview",
      ga is not None and ga != "Management Overview", f"group={ga}")
check("A3 …so placing either on that dashboard is a CROSS-MODULE pick by construction",
      gv != ga or gv != "Management Overview", f"{gv} / {ga}")

print()
print("=" * 78)
print("B. The hub resolves DESIGNED hrefs against every group")
print("=" * 78)
m = re.search(r"layoutToHubGroups\(\s*tileResp\?\.layout\s*,\s*(\w+)\s*\)", hub)
check("B1 the hub calls layoutToHubGroups with an explicit item source", bool(m), "call not found")
src_var = m.group(1) if m else ""
check("B2 that source is the ALL-GROUPS set, not the single group's "
      "(this is the whole defect: a designed href absent from the list is dropped by a bare "
      "`continue`, so a cross-module pick simply vanished)",
      src_var == "allVisibleItems", f"resolved against `{src_var}`")
check("B3 the all-groups set is built by flattening NAV, not one navGroup",
      re.search(r"allVisibleItems\s*=\s*useMemo[^=]*=>\s*gateItems\(NAV\.flatMap", hub) is not None,
      "allVisibleItems is not derived from NAV.flatMap")

print()
print("=" * 78)
print("C. Widening the DESIGNER must not widen the VIEWER")
print("=" * 78)
check("C1 there is ONE gate function, so the wider set cannot drift from the group's own",
      hub.count("const gateItems = useCallback(") == 1, "gateItems is not a single shared helper")
gate = hub[hub.index("const gateItems"):]
gate = gate[:gate.index("}, [")]
for pred, why in (("canSeeItem(permissions, it)", "RBAC"),
                  ("caps[it.cap] !== false", "tenant capability"),
                  ("carrierOKActive(", "active-carrier lens"),
                  ("hidden", "tenant nav-layout hidden override")):
    check(f"C2 the gate still applies the {why} filter", pred in gate, gate[:200])
check("C3 BOTH sets go through that one gate — the cross-module list is gateItems(...), never raw NAV",
      "gateItems(NAV.flatMap" in hub and "gateItems(navGroup.items)" in hub)
check("C4 the '/hub/…' self-link is still excluded (a dashboard must not tile a link to itself)",
      "startsWith('/hub/')" in gate)

print()
print("=" * 78)
print("D. Auto-derived tiles stay scoped to the group")
print("=" * 78)
check("D1 the 'not yet placed' tile is fed the GROUP's items, not every module — an undesigned "
      "dashboard must not suddenly list the whole app",
      "mergeUnplacedItems(designed, visibleItems)" in hub, "mergeUnplacedItems got the wrong set")
check("D2 the default (no design saved) layout is still built from the group's items",
      "defaultHubGroups(navGroup.group, visibleItems, subs)" in hub)

print()
print("=" * 78)
print("E. The designer can actually offer those pages (the other half)")
print("=" * 78)
check("E1 the designer has a cross-module toggle — without it there is no supported way to place a "
      "Finance page on the Management Overview dashboard, and the render fix is unusable",
      "crossModule" in designer, "no cross-module affordance in the designer")
check("E2 it draws from NAV (all groups), not just the selected one",
      re.search(r"crossModule\s*\n?\s*\?\s*NAV\.filter", designer) is not None
      or "NAV.filter(g => g.group !== groupName)" in designer, "designer never widens past navGroup")
check("E3 the designer still filters by canSeeItem, so a menu_layout-granted manager cannot design "
      "with pages their own role cannot see",
      "canSeeItem(permissions, it)" in designer)
check("E4 a cross-module page is chipped with the module it came from, so the designer can tell "
      "where it lives", "_from" in designer)

print()
print("=" * 78)
print(f"RESULT: {P} passed, {F} failed")
print("=" * 78)
sys.exit(1 if F else 0)
