"""Proof harness — ONE OVERHEAD LINE PER ORG LEVEL (owner ask 2026-11-13, index §STOREOPS-09).

OWNER (verbatim): "Anybody who is a dm their salary is divided among the stores they are responsible
for , similarly for the regional manager or market manager whatever stores they cover their salary is
divided amount their stores , add a line in the expenses with the name dm salary and rm salary or mm
salary , if we add another level the expense report should automatically add they level and then
divide the salary based on the people who report to them"

DB-free, pure stdlib. It proves the THREE things that ask adds to the mechanism that already existed
(`storeops/overhead_allocation.py`, §14t, proof `harness_overhead_allocation.py` — EXTENDED here,
not forked), and it LOCKS each of them so the next change cannot quietly un-wire it.

  §1  THE LEVEL VOCABULARY IS THE TENANT'S AND IS REQUIRED. `level_vocabulary` answers from
      `storeops.org_levels` rows and from nothing else; `level_label` RAISES on a blank name rather
      than substituting a word the tenant did not declare. This is the §COMMCALC-49.10 trap: a
      default that is really one tenant's vocabulary is the per-tenant code RULE TWO forbids, and
      zero (or a plausible wrong label) is the answer that hides.
  §2  A HIGHER LEVEL COVERS THE UNION OF EVERYTHING BENEATH IT, derived from the ONE downward
      org-tree walk (`org_chain.subtree_ids`) — so a regional node's store set IS the union of its
      district nodes' sets, with no per-level arithmetic and no second traversal.
  §3  A MARKET-SHAPED UNIT GETS ITS STORES FROM THE CANONICAL STORE→MARKET ANSWER, as a rung of the
      SAME ladder with its own COVER_* reason code — never a parallel resolver.
  §4  THE SUM IS PRESERVED EXACTLY: Σ(per-level lines) + combined == wages_by_store, cell for cell.
      The per-level view is the same allocation GROUPED, so no subtotal and no net income moves.
  §5  CROSS-TENANT (owner directive 2026-10-30, "every feature is for EVERY tenant"). The four live
      vocabularies, measured 2026-11-13, each produce THEIR OWN lines from THEIR OWN rows, and one
      tenant's level names can never be reached from another tenant's read.
  §6  THE LOCKS, which fail the build rather than the review:
        (a) no declared level NAME appears as a literal anywhere in the two modules;
        (b) `level_label` cannot answer without the org's word;
        (c) every P&L assembly dereferences `coa.pl_spec_for` — nobody passes `coa.PL_SPEC` to
            `engine._assemble` any more, so a tenant-declared line cannot be dropped by a caller
            that forgot it exists;
        (d) ONE coverage ladder: the `COVER_*` vocabulary and a `covered_stores` are defined in
            exactly one module;
        (e) ONE downward org-tree walk: no second subtree traversal in storeops.
  §7  MONEY DOES NOT MOVE. The house default is still `mode='off'`, so an org with no config row
      derives nothing, declares no dynamic line, and `pl_spec_for` hands back the shared chart
      object ITSELF — byte-identical, line for line.

MEASURED LIVE 2026-11-13 and reproduced below as regressions:

  R1  The only salaried, active, store-unattached person in the reporting tenant sits at its RANK-1
      level and is paid annual $78,000 ($6,500.00/month). Under the ask their salary leaves the
      single combined line and lands on that level's OWN line, labelled in that tenant's word.
  R2  A second tenant's only such person sits at its RANK-0 ROOT level on a weekly basis of $2,000
      ($8,666.67/month) and covers its one store. Two tenants, two different level names, two
      different line keys, from the same code.
  R3  A market node holding 6 placed stores and 4 more that carry only the market string resolved to
      6 stores before this change and resolves to 10 after it — `COVER_ORG_UNIT_MARKET`, named on
      the cell. The salary that divided by the wrong count now divides by the right one.
  R5  THE NOTE RIDES A LINE THAT RENDERS. Found by replaying this work end to end through the real
      statement engine against production: `overhead_wages` is `auto_opt`, so once the per-level
      lines carry every dollar the combined line is DROPPED — and the honesty note went with it,
      taking the hand-entered-overhead gap report and the unallocated-pay report with it exactly
      when they are hardest to notice missing. Pinned: the note attaches to the first per-level line
      when the combined line books nothing.
  R4  Two people in the house org are salaried and active with NO unit and NO managed unit. Their
      level cannot be identified, so they stay on the combined line and the note SAYS their level is
      unknown. Nothing is guessed onto a level they may not hold.

Run: python3 backend/harness_overhead_level_lines.py   (exit 0 = all proofs hold)
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.modules.account import coa                                        # noqa: E402
from app.modules.storeops import org_chain as oc                           # noqa: E402
from app.modules.storeops.overhead_allocation import (                     # noqa: E402
    COVER_ORG_SPAN, COVER_ORG_UNIT, COVER_MARKET, COVER_ORG_UNIT_MARKET,
    COVER_ORG_WIDE, COVER_NONE, LEVEL_UNIT, LEVEL_MANAGED, LEVEL_NONE,
    LEVEL_LINE_PREFIX, LEVEL_LINE_AFTER, DEFAULT_CONFIG, resolve_config,
    covered_stores, market_unit_stores, level_vocabulary, level_label, level_of_person,
    build_overhead, level_lines, level_spec_rows, line_note, company_total,
)

FAIL = 0
CHECKS = 0


def check(name, cond):
    global FAIL, CHECKS
    CHECKS += 1
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAIL += 1


def body(rel):
    with open(os.path.join(HERE, rel)) as fh:
        return fh.read()


CFG_ON = resolve_config({"mode": "derive"})

# ── THE FOUR LIVE VOCABULARIES, measured 2026-11-13 from storeops.org_levels ────────────────────
# Read from each org's OWN rows, exactly as `gather` reads them. They live in the harness (data)
# and deliberately NOT in the module (code) — §6(a) fails the build if any of them appears there.
VOCAB = {
    "house": [{"id": 3, "name": "Executive", "rank": 0}, {"id": 4, "name": "Director", "rank": 1},
              {"id": 5, "name": "Regional Manager", "rank": 2},
              {"id": 6, "name": "District Manager", "rank": 3},
              {"id": 7, "name": "Store Manager", "rank": 4},
              {"id": 8, "name": "Sales Consultant", "rank": 5}],
    "reporting": [{"id": 9, "name": "Company", "rank": 0}, {"id": 10, "name": "Market", "rank": 1}],
    "logistics": [{"id": 13, "name": "Management", "rank": 0},
                  {"id": 14, "name": "Market", "rank": 1}, {"id": 15, "name": "Store", "rank": 2}],
    "fourth": [{"id": 18, "name": "Executive", "rank": 0}, {"id": 19, "name": "Director", "rank": 1},
               {"id": 20, "name": "Regional Manager", "rank": 2},
               {"id": 21, "name": "District Manager", "rank": 3},
               {"id": 22, "name": "Store Manager", "rank": 4},
               {"id": 23, "name": "Sales Consultant", "rank": 5}],
}

print("1. THE LEVEL VOCABULARY IS THE TENANT'S OWN, AND IT IS REQUIRED (§COMMCALC-49.10)")
v = level_vocabulary(VOCAB["reporting"])
check("the org's rows become the line family, in rank order, keyed by RANK (unique per org) and not "
      "by the tenant's word",
      [(x["rank"], x["name"], x["key"]) for x in v]
      == [(0, "Company", "overhead_wages_l0"), (1, "Market", "overhead_wages_l1")])
check("NOTHING DECLARED MEANS NOTHING IDENTIFIED — no house list of level names exists to fall back "
      "to, for None, [], a string or malformed rows",
      level_vocabulary(None) == level_vocabulary([]) == level_vocabulary("x")
      == level_vocabulary([{"name": "", "rank": 0}, {"name": "x"}, {"rank": 1}]) == [])
check("a duplicate level id is taken once; a negative rank is refused",
      len(level_vocabulary(VOCAB["reporting"] + VOCAB["reporting"])) == 2
      and level_vocabulary([{"id": 1, "name": "X", "rank": -1}]) == [])
check("the LABEL is the tenant's own word rendered into the house POLICY template",
      level_label("Market", CFG_ON) == "Market salaries (allocated to stores)"
      and level_label("District Manager", CFG_ON) == "District Manager salaries (allocated to stores)")
check("the template is per-org CONFIG, so a tenant that words its expenses differently is not "
      "arguing with code",
      level_label("Market", resolve_config({"mode": "derive",
                                            "level_label_template": "{level} payroll — allocated"}))
      == "Market payroll — allocated")
check("a template with no {level} placeholder is refused back to the house template rather than "
      "producing an UNNAMED line",
      resolve_config({"level_label_template": "overhead"})["level_label_template"]
      == DEFAULT_CONFIG["level_label_template"])
_raised = False
try:
    level_label("", CFG_ON)
except ValueError:
    _raised = True
check("§6(b) LOCK: level_label RAISES on a blank name — a caller that has not asked the org CANNOT "
      "answer, which is the one place a house default is banned",
      _raised)
_raised2 = False
try:
    level_label(None, CFG_ON)
except ValueError:
    _raised2 = True
check("…and on None, for the same reason", _raised2)

print()
print("2. A HIGHER LEVEL COVERS THE UNION OF EVERYTHING BENEATH IT — from the ONE downward walk")
# A three-rung tree: company -> two regions -> two districts each -> stores placed at the districts.
UNITS = {"co": {"id": "co", "parent_id": None, "level_id": 5, "name": "Company", "code": "__ROOT__"},
         "r1": {"id": "r1", "parent_id": "co", "level_id": 20, "name": "R1", "code": "region:r1"},
         "r2": {"id": "r2", "parent_id": "co", "level_id": 20, "name": "R2", "code": "region:r2"},
         "d1": {"id": "d1", "parent_id": "r1", "level_id": 21, "name": "D1", "code": "district:d1"},
         "d2": {"id": "d2", "parent_id": "r1", "level_id": 21, "name": "D2", "code": "district:d2"},
         "d3": {"id": "d3", "parent_id": "r2", "level_id": 21, "name": "D3", "code": "district:d3"}}
KIDS = oc.children_index(UNITS)
STORES = {"d1": ["S1", "S2"], "d2": ["S3"], "d3": ["S4", "S5", "S6"]}


def codes_under(unit_id):
    sub = oc.subtree_ids(unit_id, UNITS, KIDS)
    return sorted(c for u, cs in STORES.items() if u in sub for c in cs)


check("a district's set is its own stores", codes_under("d1") == ["S1", "S2"])
check("THE UNION RULE: the region's set IS the union of its districts' sets, with no per-level "
      "arithmetic anywhere",
      codes_under("r1") == sorted(set(codes_under("d1")) | set(codes_under("d2"))) == ["S1", "S2", "S3"])
check("…and it composes to the top: the company's set is the union of both regions'",
      codes_under("co") == sorted(set(codes_under("r1")) | set(codes_under("r2")))
      == ["S1", "S2", "S3", "S4", "S5", "S6"])
check("ADDING A LEVEL NEEDS NO CODE: a new rung spliced under a district is picked up by the same "
      "walk and lands in every ancestor's union",
      (lambda U: (lambda K: sorted(c for u, cs in dict(STORES, sub1=["S7"]).items()
                                   if u in oc.subtree_ids("r1", U, K) for c in cs))(
          oc.children_index(U)))(
          dict(UNITS, sub1={"id": "sub1", "parent_id": "d1", "level_id": 22, "name": "Sub"}))
      == ["S1", "S2", "S3", "S7"])
check("an unknown unit resolves to NOTHING, never to the whole tree",
      oc.subtree_ids("nope", UNITS, KIDS) == set() and oc.subtree_ids(None, UNITS, KIDS) == set())
check("a cycle in parent_id terminates instead of hanging the request",
      oc.subtree_ids("a", {"a": {"id": "a", "parent_id": "b"}, "b": {"id": "b", "parent_id": "a"}})
      == {"a", "b"})

print()
print("3. A MARKET-SHAPED UNIT DEREFERENCES THE CANONICAL STORE->MARKET ANSWER (R3)")
# The live shape: a market node with SIX stores placed under it and FOUR more that carry only the
# market string. `codes_by_market` is built by the caller from core.scope.market_by_code (§13a).
MKT_UNITS = {"co": {"id": "co", "parent_id": None, "level_id": 9, "name": "Company", "code": "__ROOT__"},
             "m1": {"id": "m1", "parent_id": "co", "level_id": 10, "name": "North",
                    "code": "mkt:north"},
             "m2": {"id": "m2", "parent_id": "co", "level_id": 10, "name": "South",
                    "code": "mkt:south"}}
PLACED = ["P1", "P2", "P3", "P4", "P5", "P6"]
BY_MARKET = {"North": PLACED + ["U1", "U2", "U3", "U4"], "South": ["Z1", "Z2"]}
check("a unit is matched to a market by its NAME — a data-to-data match between two things the "
      "TENANT declared",
      market_unit_stores(["m1"], MKT_UNITS, BY_MARKET) == sorted(BY_MARKET["North"]))
check("…or by its code's tail after the last ':' (the shape mig 050's seeder writes), "
      "case-insensitively",
      market_unit_stores(["m2"], MKT_UNITS, {"south": ["Z1", "Z2"]}) == ["Z1", "Z2"])
check("a unit that is NOT a market matches nothing — no level name, no market name, no tenant word "
      "is consulted in code",
      market_unit_stores(["co"], MKT_UNITS, BY_MARKET) == []
      and market_unit_stores(["m1"], MKT_UNITS, {}) == [])
check("R3 BEFORE: the placement walk alone sees only the SIX placed stores",
      covered_stores([], PLACED, PLACED + ["U1", "U2", "U3", "U4", "Z1", "Z2"], CFG_ON)
      == (PLACED, COVER_ORG_UNIT))
check("R3 AFTER: the market rung UNIONS the four unplaced stores in and the cell SAYS it was both "
      "rungs — the salary now divides by ten, not six",
      covered_stores([], PLACED, PLACED + ["U1", "U2", "U3", "U4"], CFG_ON,
                     market_codes=market_unit_stores(["m1"], MKT_UNITS, BY_MARKET))
      == (sorted(BY_MARKET["North"]), COVER_ORG_UNIT_MARKET))
check("the market rung ALONE (no store placed under the unit at all) is named COVER_MARKET",
      covered_stores([], [], BY_MARKET["North"], CFG_ON, market_codes=["U1", "U2"])
      == (["U1", "U2"], COVER_MARKET))
check("THE SAME LADDER, unchanged above: a non-empty canonical span still WINS and is used as-is",
      covered_stores(["S9"], PLACED, PLACED, CFG_ON, market_codes=["U1"])
      == (["S9"], COVER_ORG_SPAN))
check("span_fallback='org_span' still refuses the market rung — it is exactly as strong a claim as "
      "the unit rung and no stronger",
      covered_stores([], [], PLACED, resolve_config({"mode": "derive", "span_fallback": "org_span"}),
                     market_codes=["U1", "U2"]) == ([], COVER_NONE))
check("nothing resolved is still REPORTED, never spread",
      covered_stores([], [], [], CFG_ON, market_codes=[]) == ([], COVER_NONE))
check("the market rung is a RUNG of the one ladder, not a resolver: every reason code lives in the "
      "same vocabulary",
      {COVER_ORG_SPAN, COVER_ORG_UNIT, COVER_MARKET, COVER_ORG_UNIT_MARKET, COVER_ORG_WIDE,
       COVER_NONE} == {"org_span", "org_unit", "market", "org_unit_market", "org_wide", "none"})

print()
print("4. ONE LINE PER LEVEL — the SAME cells, grouped; the sum is preserved exactly (R1, R4)")
RV = level_vocabulary(VOCAB["reporting"])
CHI = ["C%02d" % i for i in range(1, 14)]      # the 13 stores of the rank-1 market node
R1_PEOPLE = [{"employee_id": "E223", "label": "person-a", "role": "Manager",
              "why": "salaried_unattached", "salary_month": 6500.00, "commission": 0.0,
              "covered": CHI, "coverage": COVER_ORG_UNIT,
              "level_id": "10", "level_name": "Market", "level_rank": 1, "level_how": LEVEL_UNIT}]
res = build_overhead(R1_PEOPLE, CFG_ON)
lv = level_lines(res, CFG_ON, RV)
check("R1: the $6,500.00/month leaves the single combined line and lands on the RANK-1 level's own "
      "line, labelled in that tenant's word",
      [(l["key"], l["label"], l["total"]) for l in lv["lines"]]
      == [("overhead_wages_l1", "Market salaries (allocated to stores)", 6500.00)])
check("…divided equally across the stores that level covers, cents-exact ($500.00 x 13)",
      set(lv["lines"][0]["by_store"].values()) == {500.00}
      and len(lv["lines"][0]["by_store"]) == 13)
check("a DECLARED level nobody is paid at books NO line — the chart does not grow empty rows",
      not any(l["key"] == "overhead_wages_l0" for l in lv["lines"]))
check("§4 THE IDENTITY: sum of the per-level lines + the combined line == wages_by_store, cell for "
      "cell, so no subtotal and no net income moves",
      {k: round(sum(l["by_store"].get(k, 0.0) for l in lv["lines"])
                + lv["combined_by_store"].get(k, 0.0), 2) for k in res["wages_by_store"]}
      == res["wages_by_store"]
      and round(sum(l["total"] for l in lv["lines"]) + lv["combined_total"], 2)
      == company_total(res["wages_by_store"]) == 6500.00)
check("company is still the SUM OF STORES and there is still no company-wide overhead cell",
      "" not in res["wages_by_store"] and None not in res["wages_by_store"]
      and all("company" not in str(k).lower() for k in res["wages_by_store"]))

# R4 — two people whose level cannot be identified.
R4_PEOPLE = [dict(R1_PEOPLE[0], employee_id="E127", salary_month=0.08, covered=["H1", "H2"],
                  level_id="", level_name="", level_rank=None, level_how=LEVEL_NONE),
             dict(R1_PEOPLE[0], employee_id="E130", salary_month=0.08, covered=["H1", "H2"],
                  level_id="", level_name="", level_rank=None, level_how=LEVEL_NONE)]
res4 = build_overhead(R1_PEOPLE + R4_PEOPLE, CFG_ON)
lv4 = level_lines(res4, CFG_ON, RV)
check("R4: a person whose level cannot be identified STAYS on the combined line — never guessed "
      "onto a level they may not hold",
      lv4["combined_total"] == 0.16 and lv4["lines"][0]["total"] == 6500.00)
check("R4: and the surface SAYS SO, naming the reason rather than showing a silent figure",
      "could not be identified" in lv4["note"] and "combined line" in lv4["note"])
check("§4 the identity holds with both kinds of person present",
      round(sum(l["total"] for l in lv4["lines"]) + lv4["combined_total"], 2)
      == company_total(res4["wages_by_store"]) == 6500.16)
check("the per-person audit row carries the level and HOW it was resolved, so 'who got paid how "
      "much' survives the split",
      {r["employee_id"]: (r["level_name"], r["level_how"]) for r in res4["people"]}
      == {"E223": ("Market", LEVEL_UNIT), "E127": ("", LEVEL_NONE), "E130": ("", LEVEL_NONE)})

print()
print("5. CROSS-TENANT — every tenant's OWN words, and no leakage between them (2026-10-30)")
LIVE = {
    # tenant -> (vocabulary, the person's level_id, their monthly salary, their covered stores)
    "reporting": (VOCAB["reporting"], "10", 6500.00, CHI),                       # rank 1 'Market'
    "logistics": (VOCAB["logistics"], "13", 8666.67, ["N1"]),                    # rank 0 root level
    "house": (VOCAB["house"], "6", 4000.00, ["H1", "H2", "H3", "H4"]),           # rank 3
    "fourth": (VOCAB["fourth"], "20", 9000.00, ["V1", "V2", "V3"]),              # rank 2
}
OWN = {}
for tenant, (rows, lid, sal, codes) in LIVE.items():
    voc = level_vocabulary(rows)
    name = next(x["name"] for x in voc if x["level_id"] == lid)
    r = build_overhead([{"employee_id": "X", "label": "p", "role": None, "why": "salaried_unattached",
                         "salary_month": sal, "commission": 0.0, "covered": codes,
                         "coverage": COVER_ORG_UNIT, "level_id": lid, "level_name": name,
                         "level_rank": next(x["rank"] for x in voc if x["level_id"] == lid),
                         "level_how": LEVEL_UNIT}], CFG_ON)
    OWN[tenant] = (level_lines(r, CFG_ON, voc), voc, name, sal, codes)

check("R1/R2: all four tenants produce a line from the SAME code, each named in its OWN word",
      [(t, OWN[t][0]["lines"][0]["label"]) for t in ("reporting", "logistics", "house", "fourth")]
      == [("reporting", "Market salaries (allocated to stores)"),
          ("logistics", "Management salaries (allocated to stores)"),
          ("house", "District Manager salaries (allocated to stores)"),
          ("fourth", "Regional Manager salaries (allocated to stores)")])
check("R2: the second tenant's weekly $2,000 basis ($8,666.67/month) lands whole on its ONE store, "
      "on its RANK-0 line key",
      OWN["logistics"][0]["lines"][0]["key"] == "overhead_wages_l0"
      and OWN["logistics"][0]["lines"][0]["by_store"] == {"N1": 8666.67})
check("the line KEY carries no tenant vocabulary — two tenants that word rank 1 differently still "
      "share the key, and two tenants at different ranks do not",
      OWN["reporting"][0]["lines"][0]["key"] == "overhead_wages_l1"
      and OWN["house"][0]["lines"][0]["key"] == "overhead_wages_l3"
      and OWN["fourth"][0]["lines"][0]["key"] == "overhead_wages_l2")
def rendered_level(label):
    """The level word the label actually NAMES — the template's own substitution, read back."""
    tail = DEFAULT_CONFIG["level_label_template"].replace("{level}", "")
    return label.replace(tail, "").strip()


check("NO LEAKAGE: the word each tenant's line NAMES is declared by that tenant, and the four "
      "tenants name four different words from one code path",
      all(rendered_level(OWN[t][0]["lines"][0]["label"])
          in {x["name"] for x in OWN[t][1]} for t in LIVE)
      and [rendered_level(OWN[t][0]["lines"][0]["label"])
           for t in ("reporting", "logistics", "house", "fourth")]
      == ["Market", "Management", "District Manager", "Regional Manager"])
check("NO LEAKAGE: no tenant's line names a word that ONLY another tenant declares",
      all(rendered_level(OWN[t][0]["lines"][0]["label"]) not in
          ({x["name"] for o in LIVE if o != t for x in level_vocabulary(LIVE[o][0])}
           - {x["name"] for x in OWN[t][1]})
          for t in LIVE))
check("reading one tenant's result with ANOTHER tenant's vocabulary identifies NOTHING and books "
      "everything to the combined line — it never relabels with the foreign word",
      all(level_lines(build_overhead(
              [{"employee_id": "X", "label": "p", "role": None, "why": "salaried_unattached",
                "salary_month": 100.0, "commission": 0.0, "covered": ["A"],
                "coverage": COVER_ORG_UNIT, "level_id": LIVE[t][1]}], CFG_ON),
          CFG_ON, level_vocabulary(VOCAB["reporting"]))["combined_total"] == 100.0
          for t in ("logistics", "house") )
      )
check("A TENANT THAT DECLARES NO LEVELS identifies nothing, books the single combined line it books "
      "today, and the note NAMES the org chart as the fix",
      (lambda z: z["lines"] == [] and z["combined_total"] == 6500.00
       and "declared NO levels" in z["note"] and "Org Chart" in z["note"])(
          level_lines(res, CFG_ON, []))
      )
check("level_lines='off' is a config row back to the single line, with the note saying so",
      (lambda z: z["lines"] == [] and z["combined_total"] == 6500.00
       and "level_lines` is off" in z["note"])(
          level_lines(res, resolve_config({"mode": "derive", "level_lines": "off"}), RV)))
check("'on' is the house default (the ask), and an unknown value falls back to it rather than "
      "inventing a third behaviour",
      resolve_config(None)["level_lines"] == "on"
      and resolve_config({"level_lines": "vibes"})["level_lines"] == "on")

print()
print("6. THE LEVEL -> PERSON LADDER, mirroring the coverage ladder's order of authority")
HV = level_vocabulary(VOCAB["fourth"])
check("the level of the unit the person is PLACED in answers first",
      level_of_person("d1", [], UNITS, HV)[0]["name"] == "District Manager"
      and level_of_person("d1", [], UNITS, HV)[1] == LEVEL_UNIT)
check("failing that, the HIGHEST (lowest rank) level among the units they MANAGE — a person "
      "managing three districts is a district-level cost wherever they sit",
      level_of_person(None, ["d1", "d2", "r1"], UNITS, HV)[0]["name"] == "Regional Manager"
      and level_of_person(None, ["d1", "d2"], UNITS, HV)[1] == LEVEL_MANAGED)
check("nothing resolves ⇒ LEVEL_NONE, and an org with no declared levels can never resolve one",
      level_of_person(None, [], UNITS, HV) == (None, LEVEL_NONE)
      and level_of_person("d1", ["r1"], UNITS, []) == (None, LEVEL_NONE))

print()
print("7. THE P&L CONTRACT — per-org lines in ONE home, dereferenced by every assembler")
rows = level_spec_rows(lv["lines"])
check("the chart rows are opex / auto_opt / STORE grain, anchored after the combined overhead line",
      rows == [("overhead_wages_l1", "Market salaries (allocated to stores)", "opex", "auto_opt",
                "store", LEVEL_LINE_AFTER)]
      and LEVEL_LINE_AFTER == "overhead_wages")
spec = coa.pl_spec_for({coa.DYNAMIC_PL_SPEC: rows})
keys = [r[0] for r in spec]
check("pl_spec_for splices the declared line in immediately after its anchor",
      keys.index("overhead_wages_l1") == keys.index("overhead_wages") + 1)
check("…and preserves DECLARED (rank) order when several levels book",
      (lambda k: k.index("overhead_wages_l0") < k.index("overhead_wages_l3"))(
          [r[0] for r in coa.pl_spec_for({coa.DYNAMIC_PL_SPEC: [
              ("overhead_wages_l0", "A", "opex", "auto_opt", "store", "overhead_wages"),
              ("overhead_wages_l3", "B", "opex", "auto_opt", "store", "overhead_wages")]})]))
check("the label map carries the declared labels too, so a line can never render keyless",
      coa.pl_label_for({coa.DYNAMIC_PL_SPEC: rows})["overhead_wages_l1"]
      == "Market salaries (allocated to stores)")
check("§7 MONEY DOES NOT MOVE: no declarations ⇒ pl_spec_for returns the SHARED CHART OBJECT itself, "
      "so every org with no config row is byte-identical, line for line",
      coa.pl_spec_for(None) is coa.PL_SPEC and coa.pl_spec_for({}) is coa.PL_SPEC
      and coa.pl_spec_for({coa.DYNAMIC_PL_SPEC: []}) is coa.PL_SPEC
      and coa.pl_label_for(None) is coa.PL_LABEL)
check("the house default is still mode='off' — nothing is derived and no line is declared until an "
      "owner opts in",
      resolve_config(None)["mode"] == "off"
      and DEFAULT_CONFIG["mode"] == "off")
check("PL_SPEC itself is never mutated by a per-org read (a tenant's line cannot leak into the "
      "shared chart another tenant is read against)",
      "overhead_wages_l1" not in {r[0] for r in coa.PL_SPEC})
check("a malformed declared row is skipped rather than corrupting the chart",
      coa.pl_spec_for({coa.DYNAMIC_PL_SPEC: [("short",), None, 7]}) == list(coa.PL_SPEC))
check("the honesty note carries the level sentence to the P&L box through the EXISTING note "
      "passthrough, not a third mechanism",
      "Market" in line_note(dict(res, levels=lv)))

print()
print("8. THE LOCKS — these fail the BUILD, not the review")
OA = body("app/modules/storeops/overhead_allocation.py")
OC = body("app/modules/storeops/org_chain.py")
COA_SRC = body("app/modules/account/coa.py")
ALL_NAMES = sorted({x["name"] for rows in VOCAB.values() for x in rows})
# `company`, `store` and `market` are the STATEMENT's own grain and scope words — they are platform
# vocabulary that predates org_levels by a year and appears in every spec row and every scope key.
# They are excluded by name so this lock stays about a TENANT'S LEVEL WORD leaking into code, which
# is the failure §COMMCALC-49.10 is about, instead of failing on the chart's own grain column. The
# AST check below then catches them too, in the only place they could do harm.
PLATFORM_GRAIN = {"company", "store", "market"}
VOCAB_ONLY = [n for n in ALL_NAMES if n.lower() not in PLATFORM_GRAIN]
_leaks = sorted({"%s in %s" % (n, p) for n in VOCAB_ONLY
                 for p, src in (("overhead_allocation.py", OA), ("org_chain.py", OC),
                                ("coa.py", COA_SRC))
                 if re.search(r"""['"]\s*%s\s*['"]""" % re.escape(n), src, re.I)})
check("§6(a) LOCK: NOT ONE declared level name appears as a string literal in the allocation "
      "module, the org-tree walk or the chart of accounts — checked against all four live "
      "vocabularies (%s)" % ", ".join(VOCAB_ONLY),
      not _leaks)

# …and INSIDE the functions that name a line, not even the platform grain words may appear as a
# literal: those five functions must be able to answer ONLY from their arguments.
import ast                                                                      # noqa: E402

_tree = ast.parse(OA)
_NAMERS = ("level_vocabulary", "level_label", "level_of_person", "level_lines", "level_spec_rows")
_lits = set()
for _fn in ast.walk(_tree):
    if isinstance(_fn, ast.FunctionDef) and _fn.name in _NAMERS:
        for _n in ast.walk(_fn):
            if isinstance(_n, ast.Constant) and isinstance(_n.value, str):
                _lits.add(_n.value.strip().lower())
check("§6(a) LOCK: the five functions that NAME a level line hold no level-word literal at all "
      "(%s) — they can answer only from the org's own rows" % ", ".join(_NAMERS),
      not (_lits & {n.lower() for n in ALL_NAMES}))
check("R5 REGRESSION: the note RIDES A LINE THAT RENDERS. `overhead_wages` is auto_opt, so once the "
      "per-level lines carry every dollar it is dropped — and the manual-gap and unallocated-pay "
      "reports went with it. build_inputs now attaches the note to the FIRST per-level line when "
      "the combined line books nothing",
      re.search(r"_note_key\s*=\s*\(\"overhead_wages\" if \(L\[\"overhead_wages\"\]", COA_SRC)
      and "_lv_rows[0][0] if _lv_rows else" in COA_SRC
      and 'L[_note_key]["note"] = _n' in COA_SRC
      and 'L["overhead_wages"]["note"] = _n' not in COA_SRC)
check("§6(b) LOCK: level_label's refusal is in the SOURCE, so it cannot be softened to a default "
      "without this check moving too",
      "raise ValueError" in OA and "there is no house default for a tenant's vocabulary" in OA
      # …and the refusal is worded in BUSINESS words: the infra-names lock (§19.39) refuses a
      # user-reachable message that names a schema or a table, and this one can reach a tenant.
      and "storeops.org_levels" not in OA.split("def level_label")[1].split("def level_of_person")[0])

# §6(c) — every P&L assembly dereferences the one home.
ASSEMBLERS = ("app/modules/account/engine.py", "app/modules/account/statement_engine.py")
_direct = [p for p in ASSEMBLERS
           if re.search(r"_assemble\([^)]*coa\.PL_SPEC", body(p), re.S)]
check("§6(c) LOCK: no P&L assembly passes coa.PL_SPEC to engine._assemble any more — both "
       "dereference coa.pl_spec_for(inputs), so a tenant-declared line cannot be dropped by a "
       "caller that forgot it exists",
      not _direct and all("coa.pl_spec_for(inputs)" in body(p) for p in ASSEMBLERS))
check("§6(c) LOCK: the one home exists and is the only place the shared chart is extended",
      "def pl_spec_for" in COA_SRC and "def pl_label_for" in COA_SRC
      and COA_SRC.count("def pl_spec_for") == 1)

# §6(d) — ONE coverage ladder.
import glob                                                                     # noqa: E402
LADDERS = [p for p in glob.glob(os.path.join(HERE, "app/modules/**/*.py"), recursive=True)
           if re.search(r"^def covered_stores\(", open(p).read(), re.M)]
check("§6(d) LOCK: exactly ONE covered_stores ladder exists in the backend",
      [os.path.relpath(p, HERE) for p in LADDERS]
      == ["app/modules/storeops/overhead_allocation.py"])
COVER_DEFS = [p for p in glob.glob(os.path.join(HERE, "app/modules/**/*.py"), recursive=True)
              if re.search(r"^COVER_(ORG_SPAN|MARKET|NONE)\s*=", open(p).read(), re.M)]
check("§6(d) LOCK: the COVER_* reason vocabulary is DEFINED in exactly one module — a second ladder "
      "cannot appear without this check failing",
      [os.path.relpath(p, HERE) for p in COVER_DEFS]
      == ["app/modules/storeops/overhead_allocation.py"])

# §6(e) — ONE downward org-tree walk in storeops.
WALKERS = []
for p in glob.glob(os.path.join(HERE, "app/modules/storeops/*.py")):
    src = open(p).read()
    if os.path.basename(p) == "org_chain.py":
        continue
    # the shape of a hand-rolled subtree walk: a stack/queue seeded with a unit and fed by children
    if re.search(r"children\s*\.\s*get\([^)]*\)\s*\)", src) and "stack" in src:
        WALKERS.append(os.path.relpath(p, HERE))
check("§6(e) LOCK: no second DOWNWARD org-tree walk in storeops — the union rule has one home "
      "(org_chain.subtree_ids) and callers dereference it",
      WALKERS == [] and "def subtree_ids" in OC and OC.count("def subtree_ids") == 1)
check("§6(e) the allocation module USES that one home rather than keeping its own copy",
      "org_chain" in OA and "subtree_ids" in OA)
check("RULE TWO still holds in the extended module: no tenant, org id, store or carrier literal",
      not any(t in OA.lower() for t in ("luxelink", "854f6d7b", "cermark", "nostrand", "vzone",
                                        "ny logistics")))

# §6(f) — A SIDE ENTRY ON `inputs` IS NOT A LINE CELL, and the walk that reads them has ONE home.
# The regression this caught: `coa.DYNAMIC_PL_SPEC` is a LIST, and `engine.compute_and_store` had
# its OWN copy of the "every store any line mentions" walk doing `ln["by_store"]` with no guard —
# so the persisted Accounts compute would raise TypeError for the first tenant to declare an org
# level, while the on-demand statement, whose copy WAS guarded, answered fine. Two readers of one
# dict, one of them not knowing its shape. Now `coa.stores_in_inputs` is the only walk and both
# dereference it.
import ast as _ast

_SIDE_INPUTS = {
    "wages": {"by_store": {"1 A St": 1000.0}},
    "overhead_wages_l1": {"by_store": {"2 B Ave": 500.0}},
    "_royalty_coverage": {"by_store": {}, "reports": 0},          # an existing DICT side entry
    coa.DYNAMIC_PL_SPEC: [("overhead_wages_l1", "Market salaries", "opex", "auto_opt",
                           "store", "overhead_wages")],           # the new LIST side entry
}
check("§6(f) the one home walks the LINE CELLS and steps over every side entry, dict or list "
      "(the regression, replayed)",
      coa.stores_in_inputs(_SIDE_INPUTS) == {"1 A St", "2 B Ave"})
check("§6(f) …and answers for an empty or absent inputs dict rather than raising",
      coa.stores_in_inputs({}) == set() and coa.stores_in_inputs(None) == set())


def _own_inputs_walk(path):
    """A hand-rolled `for <x> in inputs.values()` — the copy this lock forbids."""
    tree = _ast.parse(open(path).read())
    return [n for n in _ast.walk(tree)
            if isinstance(n, _ast.For) and isinstance(n.iter, _ast.Call)
            and isinstance(n.iter.func, _ast.Attribute) and n.iter.func.attr == "values"
            and isinstance(n.iter.func.value, _ast.Name) and n.iter.func.value.id == "inputs"]


_EN = os.path.join(HERE, "app/modules/account/engine.py")
_SE = os.path.join(HERE, "app/modules/account/statement_engine.py")
check("§6(f) LOCK: NEITHER account engine keeps its own walk over inputs.values() any more — a "
      "second copy is how one reader came not to know the dict's shape",
      _own_inputs_walk(_EN) == [] and _own_inputs_walk(_SE) == [])
check("§6(f) LOCK: both engines dereference the one home, and it is defined exactly once",
      "coa.stores_in_inputs(" in open(_EN).read()
      and "coa.stores_in_inputs(" in open(_SE).read()
      and COA_SRC.count("def stores_in_inputs") == 1)

print()
print("checks run: %d   failures: %d" % (CHECKS, FAIL))
print("ALL PROOFS HOLD" if not FAIL else "PROOFS FAILED")
sys.exit(1 if FAIL else 0)
