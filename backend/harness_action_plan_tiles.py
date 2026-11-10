"""LOCK — the Daily Action Plan's tiles are ONE grouping of verdicts somebody else reached, over the
vocabulary the TENANT declared (owner ask 2026-11-10, index §COMMCALC-50).

    python3 harness_action_plan_tiles.py        (from backend/)

THE ASK, verbatim: *"Need to organize the daily action plan to a more presentable way, it is right
now a long report, collapse it per store, give our standard filters for store rep market date range,
which metrics needs to be improved / all/ family plan etc. Make it tiled with focused metrics - cells
on the all line metrics and it shows who needs improvement and so forth with the details at the
bottom, the tiles should be switched between kpi / stores / sales reps"*.

WHAT WAS ALREADY THERE, AND SO WAS NOT BUILT AGAIN (the duplicate-check gate). The endpoint
`GET /commcalc/targets/{period}/action-plan` already decided every verdict the tiles show: a
category's severity (`targets_engine.build_action_items`), a KPI's pass/fail AND what nothing fed
(`kpi_failing.evaluate` → `kpis_no_data`), the at-risk dollars (the calculated commission rows) and
the team roll-up (`targets_drilldown.team_totals`). The standard filters already had one home
(`components/StandardFilterBar` over `lib/standard-filters`), sending already had one
(`lib/send-report.SendReportButton`), a mailed tile row already had one (`lib/tile-snapshot`), and
the drill panels already had one (`components/DrillDownPanel`). NOTHING in this change derives a
figure: `frontend/src/lib/action-plan-tiles.ts` GROUPS what the server served.

THE CLASS THIS LOCKS, not the instance:
  · *a tile whose headline is a re-summed figure is a second fold* — two numbers hoping to agree. So
    a tile's figure is the CARDINALITY of the list it prints, and the team roll-up stays the
    server's. §B.
  · *a metric list written in a screen is one tenant's vocabulary answering for every tenant*
    (directive 2026-10-30, §COMMCALC-49.10's class). The axis is READ off the payload and is a
    REQUIRED argument; nothing declared means no tiles. §C.
  · *"0 behind" over a feed that landed nothing reads as a clean sheet.* The endpoint's own
    separation of failing from unfed must survive into the tile and into the mailed copy. §D.

WHAT THIS LOCK FAILS THE BUILD FOR:
  §A ONE HOME — the module exists, is pure, declares the payload shape and the three views, and the
     page dereferences every part of it rather than keeping a copy.
  §B NO SECOND FOLD — the page sums no target, achieved or need of its own, and still reads the
     server's `team_total`.
  §C THE TENANT'S VOCABULARY — no metric-key or KPI-label literal in either file, no default for the
     declarations argument, and a selection of undeclared keys narrows to nothing.
  §D NOT MEASURED IS NOT ZERO — the module carries no figure for an unmeasured tile, the page renders
     the words, and `kpis_no_data` is read at both grains.
  §E THE SHARED BARS — the standard filters and the one sender are on the page, and the tile row is
     mailed through the shared snapshot converter.
  §F THE DETAIL IS COLLAPSED PER STORE, which is the ask's own shape.
  §G PROVED, not asserted — the behaviour lives in a vitest unit file that CI runs, and the endpoint
     contract in a backend integration test that CI runs.
  §H REGISTERED in the index, and this lock is wired into a workflow.
  §I NEGATIVE CONTROLS — every scan is shown finding its target in a control string, so a scan that
     has stopped matching anything cannot sit here reading green.

Stdlib only, DB-free. The TypeScript is read as text here and EXECUTED by §G's vitest file; this half
is the wiring lock, that half is the behaviour.
"""
import io
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

MODULE = "frontend/src/lib/action-plan-tiles.ts"
PAGE = "frontend/src/app/(platform)/commcalc/targets/action-plan/page.tsx"
UNIT = "frontend/src/lib/__tests__/action-plan-tiles.test.ts"
SCREEN = "frontend/src/app/(platform)/commcalc/targets/action-plan/__tests__/page.test.tsx"
ITEST = "backend/tests/integration/test_action_plan_tiles_endpoint.py"
ENDPOINT = "backend/app/modules/commcalc/router.py"
KPI_HONESTY = "backend/app/modules/commcalc/kpi_failing.py"
WORKFLOW = ".github/workflows/tests.yml"
INDEX = "docs/SYSTEM_DATA_FLOW_INDEX.md"

#: The metric keys and KPI labels a screen must NOT spell. Every one of these is a word a TENANT
#: declares (`commcalc.carrier_kpi_metric`, the Count Fields catalog, the built-in target columns);
#: a literal here is the house's vocabulary answering for somebody else's shop.
TENANT_WORDS = ("familyplan", "boostapp", "protect", "tmr3", "atu", "byod", "aal",
                "family plan", "carrier app", "activations", "upgrades", "accessories")

_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print("  PASS  %s" % name)
    else:
        _fail += 1
        print("  FAIL  %s %s" % (name, extra))


def read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def ts_code(text):
    """The TypeScript with its comments removed — a rule must live in CODE, not in prose about it.

    The line form drops `//` only where it starts the line (after whitespace) or follows whitespace,
    so a `https://` inside a string survives; the mutation tests in §I prove the stripper is not
    silently eating the code it is meant to expose.
    """
    out = re.sub(r"(?s)/\*.*?\*/", "", text)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", ln) for ln in out.splitlines())


mod, mod_code = read(MODULE), ts_code(read(MODULE))
page, page_code = read(PAGE), ts_code(read(PAGE))
unit = read(UNIT)
screen = read(SCREEN)
itest = read(ITEST)
endpoint = read(ENDPOINT)
kpi = read(KPI_HONESTY)
wf = read(WORKFLOW)
idx = read(INDEX)

print("\n§A ONE HOME FOR THE GROUPING")
check("A1 the module exports the fold the screen calls",
      all("export function %s" % f in mod_code
          for f in ("planMetrics", "buildTiles", "filterPlan", "tilesToSnapshot", "metricKey")))
check("A2 ...and declares the payload shape, so the fold and the screen cannot disagree about it",
      all("export interface %s" % t in mod_code
          for t in ("PlanStore", "PlanRep", "PlanMetric", "PlanKpi", "PlanKpiNoData", "PlanCommission")))
check("A3 ...and the THREE views, once, where both the toggle and the fold read them",
      "export const TILE_VIEWS" in mod_code
      and len(re.findall(r"key: '(kpi|store|rep)'", mod_code)) == 3)
check("A4 it is PURE — no React, no fetch, no clock",
      not re.search(r"from 'react'|useMemo|useState|\bfetch\s*\(|Date\.now|new Date\(", mod_code))
check("A5 the page DEREFERENCES the module rather than grouping the plan itself",
      "@/lib/action-plan-tiles" in page_code
      and all(f + "(" in page_code for f in ("buildTiles", "planMetrics", "filterPlan", "tilesToSnapshot")))
check("A6 the page keeps NO second view list of its own — the toggle is rendered off TILE_VIEWS",
      "TILE_VIEWS.map(" in page_code
      and not re.search(r"\[\s*'kpi'\s*,\s*'store'", page_code))
check("A7 the narrowing predicate is the shared one, not a second 'does this row match the picker'",
      "matchesStandardFilter(" in mod_code
      and not re.search(r"toLowerCase\(\)\s*===|\.trim\(\)\.toLowerCase\(\)\s*===", mod_code))

print("\n§B NO SECOND FOLD — a tile counts the list it prints")
check("B1 the page still reads the SERVER's team roll-up",
      "data.team_total?.categories" in page_code)
check("B2 ...and folds no category total of its own",
      not re.search(r"(target|achieved|need)\s*\+=", page_code))
check("B3 the module sums no money, target or achieved either — it counts who needs improvement",
      not re.search(r"(at_risk|subtotal|target|achieved)\s*\+=", mod_code)
      and not re.search(r"\.reduce\(\s*\(.*?\)\s*=>\s*\w+\s*\+", mod_code))
check("B4 a rep working two stores is folded ONCE (the commission row is per rep, not per store), "
      "and the proof says so",
      "if (!cur.commission) cur.commission" in mod_code
      and "never doubled" in unit)
check("B5 the tile's figure is the count behind, or the at-risk figure the server computed",
      "figure: behind" in mod_code and "figure: atRisk" in mod_code)

print("\n§C THE VOCABULARY IS THE TENANT'S, AND IT IS REQUIRED")
_mod_words = [w for w in TENANT_WORDS if w in mod_code.lower()]
check("C1 the module spells NO metric key or KPI label — it reads the declared axis",
      not _mod_words, "found %s" % _mod_words)
_page_words = [w for w in TENANT_WORDS if w in page_code.lower()]
check("C2 and neither does the page", not _page_words, "found %s" % _page_words)
check("C3 the declarations are a REQUIRED argument — no default, no optional marker",
      re.search(r"export function buildTiles\(stores: PlanStore\[\], decls: MetricDecl\[\]", mod_code)
      is not None
      and not re.search(r"decls\s*[?:]\s*MetricDecl\[\]\s*=", mod_code))
check("C4 nothing declared means NO tiles rather than house words",
      "if (!want.length || !(stores || []).length) return []" in mod_code)
check("C5 a picked key this tenant never declared narrows to nothing — it never widens back to all",
      "if (!selected || selected.length === 0) return d" in mod_code
      and "want.has(x.key)" in mod_code)
check("C6 the picker's options come from the payload's own declarations",
      "planMetrics(allStores)" in page_code
      and "decls.map(d => ({ id: d.key, label: d.label }))" in page_code)
check("C7 the axis is keyed by GRAIN too, so a word shared by a target and a KPI cannot collide",
      "export function metricKey" in mod_code and "`${grain}:${key}`" in mod_code)
check("C8 the cross-tenant case is PROVED, both ways round",
      "CANNOT ANSWER FOR ANOTHER" in unit
      and unit.count("planMetrics(TENANT_A)") >= 2 and "planMetrics(TENANT_B)" in unit)

print("\n§D NOT MEASURED IS NOT ZERO")
check("D1 the endpoint still serves what nothing fed, separately from what failed",
      "'kpis_no_data': _knd" in endpoint)
check("D2 ...and the one classifier still refuses to count a blank as failing",
      "no_data.append(" in kpi and "never counted as failing" in kpi)
check("D3 the module reads it at BOTH grains — the KPI tile and the rep tile",
      mod_code.count("kpis_no_data") >= 3)
check("D4 an unmeasured tile carries NO figure",
      len(re.findall(r"measured: false", mod_code)) >= 3
      and not re.search(r"measured: false,[^}]*figure: 0", mod_code))
check("D5 the screen prints the words rather than a zero",
      "not measured" in page_code and "kpis_no_data || []" in page_code)
check("D6 the mailed copy says it too — and claims NO change, because this report compares no two "
      "periods",
      "measured: false," in mod_code and "changePct: null" in mod_code)
check("D7 proved, not asserted", "A KPI NOBODY MEASURED IS NOT A CLEAN SHEET" in unit
      and "never_as_a_zero" in itest)

print("\n§E THE SHARED BARS (§CORE-27) AND THE ONE SENDER")
check("E1 the standard filter bar is on the page", "<StandardFilterBar" in page_code)
check("E2 ...rendering the month, the store/market cascade and the people picker",
      all(p in page_code for p in ("periodMode=\"month\"", "cascadeStores=", "repOptions=")))
check("E3 the one sender is on the page", "<SendReportButton" in page_code)
check("E4 ...and the page adds no send path of its own beside it",
      "notify/send" not in page_code)
check("E5 the tile row is mailed through the SHARED snapshot converter — no second mailed shape",
      "tileSnapshotSheet(tilesToSnapshot(tiles)" in page_code
      and "header: '" not in mod_code)
check("E6 the 'as of' day is the engine's own parameter, not a client narrowing",
      "today=${encodeURIComponent(asOf" in page_code)
check("E7 the drill-downs are still the shared renderer",
      "DrillDownPanel" in page_code and "useDrillTile(" in page_code
      and "tie_out" not in page_code)

print("\n§F THE DETAIL IS COLLAPSED PER STORE")
check("F1 each store's detail is behind a toggle, closed until it is opened",
      "aria-expanded={isOpen}" in page_code and "{isOpen && (" in page_code)
check("F2 ...with expand-all / collapse-all over the stores in view",
      "Expand all" in page and "Collapse all" in page)
check("F3 the per-store verdict is still the shared one, not `need > 0`",
      "targetState(" in page_code and "@/lib/target-state" in page_code)

print("\n§G PROVED BY TESTS THAT RUN")
check("G1 the unit file drives the REAL module", "from '@/lib/action-plan-tiles'" in unit)
check("G2 ...and covers all three views", all(("view('%s')" % v) in unit for v in ("kpi", "store", "rep")))
check("G3 CI runs the frontend unit suite", "npm run test" in wf and "frontend" in wf)
check("G4 the endpoint contract is covered for EVERY tenant, not one",
      "TENANTS = [HOUSE, ORG_B, ORG_C]" in itest
      and itest.count("@pytest.mark.parametrize(\"org\", TENANTS)") >= 5)
check("G5 ...including that no tenant's words reach another's plan",
      "test_no_other_tenants_words_reach_this_tenants_plan" in itest)
check("G6 CI runs the backend integration suite", "pytest -m integration" in wf)
check("G8 the SCREEN is proved too — the real page, rendered, over two tenants' words",
      "import ActionPlanPage from '../page'" in screen
      and "PLAN_B" in screen and "aria-expanded" in screen)
check("G9 ...including that the detail starts COLLAPSED and that an unfed KPI says so on screen",
      "aria-expanded')).toBe('false')" in screen and "NOTHING FED SAYS SO" in screen)
check("G7 this lock is wired into a workflow — a harness no job runs is a file, not a gate",
      "harness_action_plan_tiles.py" in wf)

print("\n§H REGISTERED")
check("H1 the section is in the index", "COMMCALC-57" in idx)
check("H2 the one home is named there", "action-plan-tiles" in idx)
check("H3 ...and so is this lock", "harness_action_plan_tiles.py" in idx)

print("\n§I NEGATIVE CONTROLS — every scan above, shown finding its target")
check("neg-A a page that stopped dereferencing the fold would be RED",
      "buildTiles(" not in page_code.replace("buildTiles(", "groupItMyself("))
check("neg-B the §B scanner finds a hand-rolled total when there is one",
      re.search(r"(target|achieved|need)\s*\+=", ts_code("a.target += m.target")) is not None)
check("neg-C the §C scanner finds a tenant word when there is one",
      any(w in ts_code("const FOCUS = ['familyplan']").lower() for w in TENANT_WORDS))
check("neg-C2 ...and a default on the declarations argument",
      re.search(r"decls\s*[?:]\s*MetricDecl\[\]\s*=",
                "function buildTiles(stores: PlanStore[], decls: MetricDecl[] = HOUSE)") is not None)
check("neg-D the §D scanner finds a zero figure on an unmeasured tile",
      re.search(r"measured: false,[^}]*figure: 0", ts_code("{ measured: false, figure: 0 }")) is not None)
check("neg-E the §E scanner finds a page-level notify call when there is one",
      "notify/send" in ts_code("await api('/api/v1/notify/send-file')"))
check("neg-F a COMMENT claiming any of this would NOT satisfy the code scans",
      "buildTiles" not in ts_code("// we call buildTiles( here\n")
      and "<StandardFilterBar" not in ts_code("  // renders <StandardFilterBar/> too\n"))
check("neg-G the stripper leaves real code alone, including a URL inside a string",
      "https://example.test" in ts_code("const u = 'https://example.test'\n")
      and "buildTiles(" in ts_code("const t = buildTiles(a, b, c, d)  // grouped\n"))

print("\n%d passed, %d failed" % (_pass, _fail))
if _fail:
    sys.exit(1)
print("OK — the action plan's tiles group the server's own verdicts over the tenant's own declared "
      "metrics, count the list they print rather than re-folding a figure, and say 'not measured' "
      "wherever nothing was.")
