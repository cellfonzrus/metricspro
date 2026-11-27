"""PROOF + LOCK — EVERY PAGE A BUSINESS TYPE LEAVES VISIBLE HAS A DOOR (index §CORE-06.5).

Owner report 2026-11-27, in the NY LOGISTICS tenant: *"i cannot see the targtet setting dashboard and the
incentives dashboard in ny logistcis - why is that and fix it"*.

THE CLASS. `NavItem.tileOnly` means, in rbac.ts's own words, *"the page is covered by a master tile on its
hub dashboard, so the sidebar's default rendering SKIPS it"*. That is a PRECONDITION, not a property. A
business type's hide-list (`core.tenant_vertical.nav_hidden`, mig 1020) names group HUBS — and when the hub
goes, nothing covers the group's pages, the sidebar drops the group whole
(`loose.length === 0 && shownSubs.length === 0` in `(platform)/layout.tsx`) and every page that hide-list
DELIBERATELY LEFT VISIBLE loses its only door while the route guard would still open it. Measured on
`ups_store` before the fix: 20 such pages, "Daily Incentive Rules" (the mig-1072 daily-incentive mechanism
built FOR that business type) and "Target Settings" among them. Nothing in the suite asked the question.

It is the §CORE-06 family seen from the other side: that section asked whether a door ASKS PERMISSION; this
one asks whether a door EXISTS. Same subject (a tile is the only door to a tileOnly page), new fact.

  §A  NAV + the hide-lists parse, and the Python mirror of `tileOnlySkipped` matches the TypeScript
  §B  THE INVARIANT: for every house business type, every page it leaves visible is reachable
  §C  the reported instance: Target Settings and Daily Incentive Rules have doors in `ups_store`
  §D  the SWEPT-CHILDREN ratchet: a bare hide-list entry's children are pinned, so a page added under one
      cannot be hidden by accident (what happened to the six pages under `/commcalc/targets`)
  §E  wiring: the sidebar dereferences the one home and keeps no private copy of the skip
  §F  negative controls — each lock goes RED on a planted defect

Stdlib only, DB-free: `python backend/harness_nav_reachability.py`.
"""
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

if "fastapi" not in sys.modules:
    try:
        import fastapi  # noqa: F401
    except Exception:
        sys.modules["fastapi"] = types.ModuleType("fastapi")

from app.modules.core import verticals as V  # noqa: E402

FAILS, N = [], [0]


def check(name, cond, detail=""):
    N[0] += 1
    if not cond:
        FAILS.append(name)
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  → {detail}"))


def read(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


RBAC = "frontend/src/lib/rbac.ts"
TILE_HUBS = "frontend/src/lib/tile-hubs.ts"
LAYOUT = "frontend/src/app/(platform)/layout.tsx"

# ── §A parse + mirror ───────────────────────────────────────────────────────────────────────────────────
print("§A parse + mirror")


def parse_nav(src):
    """[(group, [{'href','tileOnly','module'}])] from rbac.ts's NAV literal.

    Split on the group headers rather than matching each group's items array: a lazy `items: [...]` match
    runs past a one-item group into the next one (it merged 'Store Operations' into 'Point of Sale' while
    this was being written), and a trailing `\n` is appended so the LAST item of each group is seen.
    """
    block = src.split("export const NAV: NavGroup[] = [", 1)[1].split("\n]\n", 1)[0]
    parts = re.split(r"\n  \{ group: '", block)[1:]
    out = []
    for part in parts:
        group = part.split("'", 1)[0]
        items = part.split("items: [", 1)[1] + "\n"
        rows = []
        for href, tail in re.findall(r"\{ href: '([^']+)',(.*?)\},?\n", items):
            mod = re.search(r"module: '([a-z_]+)'", tail)
            rows.append({"href": href, "tileOnly": "tileOnly: true" in tail,
                         "module": mod.group(1) if mod else ""})
        out.append((group, rows))
    return out


def slug_group(name):
    """Mirror of tile-hubs.slugGroup (used only to CROSS-CHECK the hub read off each group)."""
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()))


def path_of(href):
    return re.sub(r"/+$", "", str(href or "").split("?")[0]) or "/"


def group_hub(items):
    """Mirror of tile-hubs.groupHubItem — the group's one '/hub/…' dashboard entry, or None."""
    return next((i for i in items or [] if path_of(i["href"]).startswith("/hub/")), None)


def tile_only_skipped(item, group, declared, visible, is_report_category):
    """Mirror of tile-hubs.tileOnlySkipped — §A5 pins that the TS keeps the same three gates."""
    if not item.get("tileOnly"):
        return False
    if is_report_category:
        return False
    hub = group_hub(declared)
    if not hub:
        return True
    return any(path_of(i["href"]) == path_of(hub["href"]) for i in visible or [])


NAV = parse_nav(read(RBAC))
FLAT = re.findall(r"href: '([^']+)'", read(RBAC).split("export const NAV: NavGroup[] = [", 1)[1].split("\n]\n", 1)[0])
check("A1 NAV parsed — every group, and every group's LAST item", len(NAV) > 25
      and sum(len(i) for _, i in NAV) == len(FLAT) and len(FLAT) > 350,
      (len(NAV), sum(len(i) for _, i in NAV), len(FLAT)))
check("A2 two house business types", len(V.HOUSE_VERTICALS) == 2, [r["key"] for r in V.HOUSE_VERTICALS])
_hubsper = {g: [i["href"] for i in items if i["href"].startswith("/hub/")] for g, items in NAV}
check("A3 a group declares at most ONE hub, and reading it agrees with '/hub/' + slugGroup(group)",
      all(len(h) <= 1 and (not h or h[0] == "/hub/" + slug_group(g)) for g, h in _hubsper.items()),
      {g: h for g, h in _hubsper.items() if len(h) > 1 or (h and h[0] != "/hub/" + slug_group(g))})
check("A3b a hand-woven group declares no hub and is therefore untouched",
      all(group_hub(dict(NAV)[g]) is None for g in ("Store Operations", "Workforce", "Payroll & HR",
                                                    "Flags & Compliance", "Configuration")),
      [g for g in ("Store Operations", "Workforce", "Payroll & HR") if group_hub(dict(NAV)[g])])
ts = read(TILE_HUBS)
check("A4 the one home exports the predicate + the hub reader",
      "export function tileOnlySkipped(" in ts and "export function groupHubItem" in ts
      and "export function groupDeclaresHub(" in ts)
check("A5 the TS skip has the same three gates this mirror has",
      all(x in ts for x in ("if (!item?.tileOnly) return false", "if (isReportCategory) return false",
                            "if (!hub) return true")), ts.count("return"))


def visible_items(items, vertical):
    hid_mods = set(V.hidden_modules(V.HOUSE_MODULE_VERTICALS, vertical["key"]))
    nh = vertical["nav_hidden"]
    return [i for i in items if not V.href_hidden(i["href"], nh) and i["module"] not in hid_mods]


def unreachable(vertical, nav=None, skip=None):
    """Pages this business type leaves visible that no door reaches."""
    skip = skip or tile_only_skipped
    out = []
    for group, items in (nav or NAV):
        vis = visible_items(items, vertical)
        if not vis:
            continue                                  # the whole group is hidden: nothing is stranded
        hub = group_hub(items)
        hub_visible = bool(hub) and any(path_of(i["href"]) == path_of(hub["href"]) for i in vis)
        rows = [i for i in vis if not skip(i, group, items, vis, False)]
        if rows:
            continue                                  # the group renders; every tiled page sits on a door
        if not hub_visible:
            out += [(group, i["href"]) for i in vis]
    return out


# ── §B the invariant ────────────────────────────────────────────────────────────────────────────────────
print("§B every visible page has a door")
for _v in V.HOUSE_VERTICALS:
    bad = unreachable(_v)
    check(f"B1 {_v['key']}: nothing stranded", not bad, bad[:6])

# ── §C the reported instance ────────────────────────────────────────────────────────────────────────────
print("§C the owner's report")
UPS = next(r for r in V.HOUSE_VERTICALS if r["key"] == "ups_store")
for _href, _label in (("/commcalc/targets/settings", "Target Settings"),
                      ("/commcalc/targets/my", "My Targets"),
                      ("/commcalc/incentive-rules", "Daily Incentive Rules"),
                      ("/commcalc/daily-incentive", "What a Day Pays")):
    check(f"C1 {_label} is not hidden from the UPS store", not V.href_hidden(_href, UPS["nav_hidden"]))
check("C2 the Daily Targets hide is EXACT, so it sweeps no child",
      "/commcalc/targets$" in UPS["nav_hidden"] and "/commcalc/targets" not in UPS["nav_hidden"])
check("C3 Daily Targets itself is still hidden", V.href_hidden("/commcalc/targets", UPS["nav_hidden"]))
for _hub in ("/hub/incentives", "/hub/incentive-payout-plans", "/hub/targets-coaching"):
    check(f"C4 {_hub} is a dashboard the UPS store may open", not V.href_hidden(_hub, UPS["nav_hidden"]))

# ── §D swept-children ratchet ───────────────────────────────────────────────────────────────────────────
print("§D swept children are pinned")
# A BARE hide-list entry hides its subtree, so a page shipped under one is hidden with nothing said — which
# is exactly what happened to the six pages under /commcalc/targets. Every child a bare entry sweeps today is
# pinned here. Adding a page under one of these prefixes FAILS THE BUILD until somebody decides whether that
# business type should see it; the answer is either a line here or an exact '$' entry on the parent.
SWEPT = {
    "ups_store": {
        "/commcalc/exec": ["/commcalc/exec/mtd"],
        "/commcalc/asset/charges": ["/commcalc/asset/charges/rma"],
        "/commcalc/vip": ["/commcalc/vip/paygo", "/commcalc/vip/sweep"],
        "/commcalc/epay": ["/commcalc/epay/sweep"],
        "/commcalc/dlar": ["/commcalc/dlar/sweep"],
    },
}
ALL_HREFS = sorted({i["href"] for _, items in NAV for i in items})
for _v in V.HOUSE_VERTICALS:
    pinned = SWEPT.get(_v["key"], {})
    live = {}
    for p in _v["nav_hidden"]:
        if p.endswith("$"):
            continue
        q = p.rstrip("/")
        kids = sorted({path_of(h) for h in ALL_HREFS if path_of(h).startswith(q + "/")})
        if kids:
            live[q] = kids
    check(f"D1 {_v['key']}: swept children match the pin", live == pinned,
          {k: v for k, v in live.items() if pinned.get(k) != v} or
          {k: v for k, v in pinned.items() if k not in live})

# ── §E wiring ───────────────────────────────────────────────────────────────────────────────────────────
print("§E the sidebar dereferences the one home")
lay = read(LAYOUT)
check("E1 the sidebar imports the predicate", "from '@/lib/tile-hubs'" in lay and "tileOnlySkipped" in lay)
check("E2 the sidebar's skip IS the call, not a private copy",
      "!tileOnlySkipped(it, group, declaredItems, items, REPORT_CAT_NAMES.has(group))" in lay)
check("E3 no second copy of the old skip survives",
      "!it.tileOnly || REPORT_CAT_NAMES.has(group)" not in lay)
check("E4 the sidebar keeps no hub list of its own",
      "'/hub/" not in lay.split("const shown =", 1)[1][:400])

# ── §F negative controls ────────────────────────────────────────────────────────────────────────────────
print("§F negative controls")


def old_skip(item, group, declared, visible, is_report_category):
    """The pre-fix rule: a tileOnly row is skipped unconditionally."""
    return bool(item.get("tileOnly")) and not is_report_category


check("F1 the pre-fix skip strands pages (the reported defect reproduces)",
      len(unreachable(UPS, skip=old_skip)) >= 10, len(unreachable(UPS, skip=old_skip)))
_bare = dict(UPS)
_bare["nav_hidden"] = ["/commcalc/targets" if h == "/commcalc/targets$" else h for h in UPS["nav_hidden"]]
check("F2 putting the bare targets entry back hides Target Settings again",
      V.href_hidden("/commcalc/targets/settings", _bare["nav_hidden"]))
_re_hidden = dict(UPS)
_re_hidden["nav_hidden"] = UPS["nav_hidden"] + ["/hub/incentive-payout-plans"]
check("F3 with that hub hidden, the pre-fix skip strands its pages and the fix does not",
      len(unreachable(_re_hidden, skip=old_skip)) > len(unreachable(_re_hidden)),
      (len(unreachable(_re_hidden, skip=old_skip)), len(unreachable(_re_hidden))))
_hub = [{"href": "/hub/incentives", "tileOnly": False}]
_row = {"href": "/x", "tileOnly": True}
check("F4 the mirror's own gates each matter",
      tile_only_skipped(_row, "Incentives", _hub, _hub, False) is True        # hub there  → skip
      and tile_only_skipped(_row, "Incentives", _hub, [], False) is False     # hub gone   → show
      and tile_only_skipped(_row, "Incentives", [], [], False) is True        # no hub ever→ unchanged
      and tile_only_skipped(_row, "Reports · X", _hub, _hub, True) is False   # directory  → always shown
      and tile_only_skipped({"href": "/x"}, "Incentives", [], [], False) is False)

print(f"\n{N[0] - len(FAILS)}/{N[0]} passed")
if FAILS:
    print("FAILED: " + ", ".join(FAILS))
sys.exit(1 if FAILS else 0)
