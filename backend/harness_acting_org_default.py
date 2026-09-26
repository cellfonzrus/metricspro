"""LOCK: a page's organisation starts on the company the user is ACTING AS — never the house org.

Owner report 2026-09-26 (KPI Definitions, standing in the UPS Store tenant): *"why does it show 0000 as the
org"*. The page initialised its organisation from `ORG_ID` (the house org, 00000000-…-0001). The tenant
middleware lets a super admin pass a foreign `?org_id=`, so the page read — and would have written — the
house org's KPIs while the user was in another company.

The CLASS: frontend state seeded from the house-org constant. Fixed on the one page that had it; this
harness fails the build if any page seeds React state from ORG_ID again, and pins the KPI page to the
acting company (index §29.8).

PURE / DB-FREE: reads sources as text.
"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FE = os.path.join(ROOT, "frontend", "src")
FAILS = []


def check(name, ok, why=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  -- " + why))
    if not ok:
        FAILS.append(name)


seeded = []
for dp, _dn, fns in os.walk(FE):
    for fn in fns:
        if fn.endswith((".ts", ".tsx")):
            p = os.path.join(dp, fn)
            src = open(p, encoding="utf-8").read()
            if re.search(r"useState(<[^>]*>)?\(\s*ORG_ID\s*\)", src):
                seeded.append(os.path.relpath(p, FE))
check("A1 no React state is seeded from the house-org ORG_ID", not seeded, "offenders: %s" % seeded)

kpi = open(os.path.join(FE, "app", "(platform)", "admin", "kpi-metrics", "page.tsx"), encoding="utf-8").read()
check("A2 KPI Definitions follows the acting company",
      "const actingOrg = activeOrg || tenant?.org_id || ORG_ID" in kpi and "const orgId = orgPick || actingOrg" in kpi,
      "page no longer defaults to the company the user is in")
check("A3 KPI Definitions offers companies by NAME, not a raw id box", "<select" in kpi and "t.name" in kpi)

idx = open(os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
check("A4 index §29.8 registers it", "### 29.8" in idx and "harness_acting_org_default.py" in idx)

print()
print("%d FAIL(s)" % len(FAILS) if FAILS else "ALL PASS")
sys.exit(1 if FAILS else 0)
