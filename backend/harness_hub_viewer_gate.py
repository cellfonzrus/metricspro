"""PROOF + BUILD LOCK: one home answers "may this viewer open this page from a tile", and every
tiled hub dereferences it.

OWNER 2026-10-14: *"I cannot see the schedule on the app, where are we at making that live to all
employees"*.

THE DEFECT, named as a class. Five surfaces render `HubTiles`, and each answered "which of these
pages may the viewer open" for itself:

    (platform)/hub/[group]/page.tsx   the predicate stack, inline
    (platform)/compliance/page.tsx    the same stack, RETYPED
    (platform)/admin/dashboards/..    a THIRD copy — a DIFFERENT question (what a DESIGNER may place)
    (platform)/storeops/page.tsx      hand-woven HubGroup[] literal, NO GATE AT ALL
    (platform)/payroll/page.tsx       hand-woven HubGroup[] literal, NO GATE AT ALL

The Workforce hub is the ONLY door to `/storeops/schedule`, which is `tileOnly` and therefore absent
from the sidebar. Measured against the live NAV before the fix, a store-scoped rep was shown **11 of
its 21 interior links** and **2 of its 6 KPI tiles** pointing at pages `canAccessPath` bounces:
/storeops/timeclock, /attendance, /accountability, /employees, /reports, /visits, /visits/settings,
/visits/plan, /reviews/config, /setup/stores, /setup/employees. That is the "the tab is there but
clicking it does nothing" failure rbac.ts already names twice (TKT-1009, TKT-1012), reached through a
tile rather than the menu — because the curated hubs were the two consumers that never asked.

THE FIX IS THE CLASS: `frontend/src/lib/hub-gate.ts::useHubGate()` is the ONE home, and all four
VIEWER surfaces dereference it. `/admin/dashboards` is deliberately excused — it answers the
designer's question, not the viewer's, and its renderer re-applies the viewer gate.

WHY A STATIC HARNESS. The failure mode is an ABSENT filter: the page compiles, renders, and simply
offers links that bounce. No type checker and no test of the gate itself can see a caller that never
calls it. So this asserts the WIRING at the source — §A that the one home exists and carries the
whole predicate stack, §B that every HubTiles consumer dereferences it, §C that no consumer retypes
the stack, §D that the curated hubs gate their KPI rows too, and §E negative controls proving each
check actually bites.

PURE: stdlib only; reads the frontend sources. DB-free.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FRONT = os.path.join(os.path.dirname(HERE), "frontend", "src")
GATE = os.path.join(FRONT, "lib", "hub-gate.ts")
PLAT = os.path.join(FRONT, "app", "(platform)")

# Every page that renders HubTiles for a VIEWER. The designer is excused (see the docstring).
VIEWER_HUBS = {
    "generic hub": os.path.join(PLAT, "hub", "[group]", "page.tsx"),
    "compliance": os.path.join(PLAT, "compliance", "page.tsx"),
    "workforce (curated)": os.path.join(PLAT, "storeops", "page.tsx"),
    "payroll (curated)": os.path.join(PLAT, "payroll", "page.tsx"),
}
DESIGNER = os.path.join(PLAT, "admin", "dashboards", "page.tsx")
# The curated hubs are the ones holding a hand-woven HubGroup[] literal AND a KPI/StatTile row.
CURATED = {"workforce (curated)", "payroll (curated)"}

# The predicate stack the gate must carry, and that no consumer may retype.
PREDICATES = ["platformOK", "canSeeItem", "carrierOKActive", "verticalOK"]

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
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def strip_comments(src):
    """Drop // and /* */ comments so a predicate NAMED in prose never counts as a call site."""
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return "\n".join(re.sub(r"//.*$", "", ln) for ln in src.split("\n"))


def calls(src, name):
    """True when `name` is INVOKED (not merely imported or mentioned) in comment-free source."""
    body = strip_comments(src)
    body = re.sub(r"^\s*import[\s\S]*?from\s+'[^']+'\s*$", "", body, flags=re.M)
    return re.search(r"\b%s\s*\(" % re.escape(name), body) is not None


# ── §A the one home exists and carries the whole stack ───────────────────────────────────────────
print("\n§A  lib/hub-gate.ts is the ONE home")
check("the file exists", os.path.exists(GATE), GATE)
gate = read(GATE) if os.path.exists(GATE) else ""
check("exports useHubGate", "export function useHubGate" in gate)
for fn in ("gateItems", "canOpen", "gateHubGroups"):
    check("returns %s" % fn, re.search(r"\b%s:" % fn, gate) is not None)
for pred in PREDICATES:
    check("dereferences %s" % pred, calls(gate, pred),
          "the gate must APPLY the predicate, not describe it")
check("honors the tenant nav-layout `hidden` override", "hidden" in gate)
check("RBAC applies only while login is enforced AND a session exists",
      "rbacEnabled !== false" in gate and "!!session" in gate,
      "the open app must show everything, exactly as the sidebar's `open` path does")
check("canOpen falls back to canAccessPath for an href NAV does not carry",
      calls(gate, "canAccessPath"),
      "fail-closed would hide a page the route guard admits — a tile and the guard must agree")
check("canOpen keys on navPath (query/anchor stripped)", calls(gate, "navPath"))
check("gateItems never tiles a link to the hub itself", "'/hub/'" in gate)
check("a tile left with no reachable page is dropped",
      re.search(r"items\.length\s*>\s*0", gate) is not None)

# ── §B every viewer hub dereferences it ──────────────────────────────────────────────────────────
print("\n§B  every HubTiles consumer dereferences the one home")
for name, path in sorted(VIEWER_HUBS.items()):
    src = read(path) if os.path.exists(path) else ""
    check("%s: exists" % name, bool(src), path)
    check("%s: imports the one home" % name, "@/lib/hub-gate" in src)
    check("%s: calls useHubGate()" % name, calls(src, "useHubGate"))
    # What it renders must be the GATED set, never the raw literal/nav items.
    rendered = re.search(r"<HubTiles\s+groups=\{([A-Za-z0-9_.]+)\}", src)
    check("%s: renders a gated groups value" % name, rendered is not None, "no <HubTiles groups={…}>")
    if rendered:
        check("%s: does not render the raw GROUPS literal" % name, rendered.group(1) != "GROUPS",
              "renders %s" % rendered.group(1))

# ── §C nobody retypes the stack ──────────────────────────────────────────────────────────────────
print("\n§C  the predicate stack is not retyped outside the one home")
for name, path in sorted(VIEWER_HUBS.items()):
    src = read(path) if os.path.exists(path) else ""
    retyped = [p for p in ("canSeeItem", "carrierOKActive", "verticalOK") if calls(src, p)]
    check("%s: applies no copy of the stack" % name, not retyped,
          "retypes %s — read it from lib/hub-gate.ts" % ", ".join(retyped))
check("the designer is excused ON PURPOSE and still asks its own question",
      os.path.exists(DESIGNER) and calls(read(DESIGNER), "canSeeItem"),
      "it answers what a DESIGNER may place; its renderer re-applies the viewer gate")

# ── §D the curated hubs gate their KPI rows too ──────────────────────────────────────────────────
print("\n§D  a curated hub's KPI row is gated as well as its tiles")
for name in sorted(CURATED):
    src = read(VIEWER_HUBS[name]) if os.path.exists(VIEWER_HUBS[name]) else ""
    body = strip_comments(src)
    tiles = re.findall(r"<StatTile\b[\s\S]*?/>", body)
    if not tiles:
        check("%s: no KPI row to gate" % name, True)
        continue
    ungated = [t for t in tiles if not re.search(
        r"canOpen\('%s'\)" % re.escape(re.search(r'href="([^"]+)"', t).group(1)), body)]
    check("%s: all %d KPI tiles gated on canOpen" % (name, len(tiles)), not ungated,
          "%d ungated" % len(ungated))
    check("%s: says so plainly when nothing is reachable" % name,
          "none of the" in body.lower() or "nothing to show" in body.lower(),
          "an empty hub must explain itself, not render blank")

# ── §E negative controls — each check above must actually bite ───────────────────────────────────
print("\n§E  negative controls (a broken source must FAIL, not pass)")
_WF = read(VIEWER_HUBS["workforce (curated)"])
ctrl_unwired = _WF.replace("@/lib/hub-gate", "@/lib/nothing").replace("useHubGate()", "noGate()")
check("C1 un-wiring the import is caught", "@/lib/hub-gate" not in ctrl_unwired)
check("C2 un-wiring the call is caught", not calls(ctrl_unwired, "useHubGate"))
ctrl_raw = _WF.replace("<HubTiles groups={groups} />", "<HubTiles groups={GROUPS} />")
m = re.search(r"<HubTiles\s+groups=\{([A-Za-z0-9_.]+)\}", ctrl_raw)
check("C3 rendering the raw literal is caught", m is not None and m.group(1) == "GROUPS")
ctrl_retyped = _WF.replace("const groups = gateHubGroups(GROUPS)",
                           "const groups = GROUPS.filter(g => canSeeItem(permissions, g as any))")
check("C4 a retyped predicate is caught", calls(ctrl_retyped, "canSeeItem"))
ctrl_kpi = re.sub(r"\{canOpen\('/storeops/employees'\) && \(", "(", _WF, count=1)
check("C5 an ungated KPI tile is caught", "canOpen('/storeops/employees')" not in ctrl_kpi)
ctrl_gate = gate.replace("canAccessPath(permissions, path)", "false")
check("C6 a fail-closed canOpen is caught", not calls(ctrl_gate, "canAccessPath"))
check("C7 a predicate only MENTIONED in a comment does not count",
      not calls("// we call canSeeItem(perms, it) here one day\nconst x = 1", "canSeeItem"))
check("C8 a predicate only IMPORTED does not count",
      not calls("import { canSeeItem } from '@/lib/rbac'\nconst x = 1", "canSeeItem"))

print("\n%d passed / %d failed" % (P, F))
sys.exit(1 if F else 0)
