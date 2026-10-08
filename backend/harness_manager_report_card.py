#!/usr/bin/env python3
"""PROOF — MANAGER REPORT CARDS (index §59.9, owner directive 2026-10-08).

DB-free. Every input is a literal, every number is checked, and §D is an AST lock that fails the build
if a card starts deriving what a one-home already owns.

Owner, verbatim: *"create a report card for the Dm based on all the items assigned to them per store
and a check off by the system if those targets were met or not, the same report card will be made for
the market manager for all the goals assigned to the Dm but a higher level reporting so they are also
accoutable."*

Run:  python3 harness_manager_report_card.py
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import manager_report_card as M  # noqa: E402

_pass = _fail = 0
_failed = []


def ck(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  ok   {name}")
    else:
        _fail += 1
        _failed.append(name)
        print(f"  FAIL {name}" + (f" — {extra}" if extra != "" else ""))


def section(t):
    print(f"\n{t}\n" + "─" * min(len(t), 98))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FIXTURE — three stores under two DMs under one market manager, plus one store the tree cannot
# place. Shapes copied from the real payloads: `summary_rows` is `/targets/{period}/summary`'s
# `stores`, `chain` is `org_chain.dm_by_store`'s output.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _cats(act=(10, 12), upg=(8, 3), byod=(5, 5), acc=(1000.0, 400.0)):
    """categories{} as the summary builds it: monthly target, achieved MTD."""
    return {"activations": {"unit": "count", "monthly": act[0], "achieved_mtd": act[1]},
            "upgrades": {"unit": "count", "monthly": upg[0], "achieved_mtd": upg[1]},
            "byod": {"unit": "count", "monthly": byod[0], "achieved_mtd": byod[1]},
            "accessories": {"unit": "money", "monthly": acc[0], "achieved_mtd": acc[1]}}


def _conv(boxes, billpays, rate, target=30.0, meets=None):
    """The conversion block EXACTLY as the live summary row carries it — FLAT, with the engine's own
    `meets_target` verdict on it. Read off a real payload, not assumed: the first draft of the module
    looked for a `conversion.store` nesting that does not exist, and every store's conversion silently
    read as untargeted. A fixture that encodes a shape nobody sends is a test of a fiction."""
    return {"boxes": boxes, "billpays": billpays, "rate": rate, "target": target,
            "meets_target": (rate >= target if meets is None and target else meets)}


ROWS = [
    {"store_code": "B-1", "store": "Store One", "market": "North",
     "categories": _cats(), "conversion": _conv(40, 100, 40.0)},
    {"store_code": "B-2", "store": "Store Two", "market": "North",
     "categories": _cats(act=(10, 4)), "conversion": _conv(20, 200, 10.0)},
    {"store_code": "B-3", "store": "Store Three", "market": "South",
     # NO targets at all — the store nobody set anything for. It must never read as a store failing.
     "categories": _cats(act=(0, 7), upg=(0, 2), byod=(0, 1), acc=(0.0, 50.0)),
     "conversion": _conv(10, 50, 20.0, target=0)},
    {"store_code": "B-9", "store": "Orphan Store", "market": "North",
     "categories": _cats(), "conversion": _conv(30, 100, 30.0)},
]

DM_A = {"employee_id": "E-DMA", "name": "Dana M", "email": "dana@x", "level": "District"}
DM_B = {"employee_id": "E-DMB", "name": "Bo D", "email": "bo@x", "level": "District"}
MM = {"employee_id": "E-MM", "name": "Mel M", "email": "mel@x", "level": "Market"}
CHAIN = {
    "B-1": {"district": {"id": "u1", "name": "District 1"}, "dm": [DM_A], "above": [MM]},
    "B-2": {"district": {"id": "u1", "name": "District 1"}, "dm": [DM_A], "above": [MM]},
    "B-3": {"district": {"id": "u2", "name": "District 2"}, "dm": [DM_B], "above": [MM]},
    # B-9: a district with NO manager recorded — a different fact from "not in the tree at all".
    "B-9": {"district": {"id": "u3", "name": "District 3"}, "dm": [], "above": [MM]},
}
# `peer_action_item`'s shape, for the one store that is behind its band.
PEER = {"B-2": {"severity": "critical", "metric": "peer_gap",
                "title": "Behind B-1 on the same traffic — boxes per bill payment",
                "detail": "B-2 took 200 bill-payment visits and sold 20 boxes.",
                "shortfall_pct": 50.0,
                "peer": {"band": 2, "band_label": "150-249 bill payments", "billpay_txns": 200,
                         "mine": 0.1, "band_median": 0.2, "band_best": 0.4,
                         "gap_to_median": 0.1, "gap_to_best": 0.3, "leader": "B-1"}}}
COMPARED = {"B-1", "B-2", "B-3", "B-9"}


def _agg(rows):
    """Stand-in for `targets_engine.aggregate_stores`, so §B can prove the roll-up is INJECTED and
    this harness stays DB-free. §D proves the ROUTER injects the real one."""
    return {"stores": len(rows), "_injected": True}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§A the check-off is three-state — absence is never performance")
# THE DOCTRINE, same as `targets_engine.attainment_pct`: a store nobody set a target for is not a
# store failing its target. Two states would make every unconfigured store look like a miss, and that
# is how a report starts accusing people over configuration.
ck("A1 target met → met; target missed → missed; no target → no_target (never a miss)",
   M._state(10, 12) == M.MET and M._state(10, 4) == M.MISSED and M._state(0, 7) == M.NO_TARGET
   and M._state(None, 7) == M.NO_TARGET)
ck("A2 exactly on target is MET, not missed",
   M._state(10, 10) == M.MET)
ck("A3 a target with nothing achieved is a MISS, not an absence",
   M._state(10, 0) == M.MISSED and M._state(10, None) == M.MISSED)
ck("A4 a negative or unparseable target is no_target, never a divide or a crash",
   M._state(-5, 3) == M.NO_TARGET and M._state("abc", 3) == M.NO_TARGET)

_items1 = M.store_items(ROWS[0], None, True)
ck("A5 one store yields one row per DECLARED item, in the registry's order",
   [r["key"] for r in _items1] == list(M.ITEM_KEYS))
ck("A6 store B-1: activations met, upgrades missed, byod met, accessories missed, conversion met",
   [r["state"] for r in _items1[:5]] == [M.MET, M.MISSED, M.MET, M.MISSED, M.MET],
   [r["state"] for r in _items1])
# THE STORE NOBODY SET A TARGET FOR. Every target-bearing item reads `no_target` and NOTHING reads
# `missed` — the whole point. Its only checked item is the peer comparison, which needs no target
# because the comparison IS the target (it was compared, and it is not behind).
_items3 = M.store_items(ROWS[2], None, True)
ck("A7 the untargeted store misses NOTHING, and every target-bearing item reads no_target",
   M.tally(_items3)["missed"] == 0 and M.tally(_items3)["no_target"] == 5
   and all(r["state"] == M.NO_TARGET for r in _items3 if r["key"] != "peer_gap"),
   M.tally(_items3))
ck("A7b with nothing measurable at all it scores None — never 0%, never 100%",
   M.tally(M.store_items(ROWS[2], None, False))["checked"] == 0
   and M.tally(M.store_items(ROWS[2], None, False))["score_pct"] is None)
ck("A8 the tally counts the three states separately and scores met ÷ CHECKED, ignoring untargeted items",
   M.tally(_items1) == {"met": 4, "missed": 2, "no_target": 0, "checked": 6, "score_pct": 66.7},
   M.tally(_items1))

section("§A2 the peer item is the ONE verdict, carried not re-decided")
_items2 = M.store_items(ROWS[1], PEER["B-2"], True)
_peer2 = [r for r in _items2 if r["key"] == "peer_gap"][0]
ck("A9 a store the comparison named is MISSED on the peer item",
   _peer2["state"] == M.MISSED)
ck("A10 … and its wording IS peer_comparison's own sentence, never a second phrasing",
   _peer2["detail"] == PEER["B-2"]["detail"])
ck("A11 … and it carries the severity through, so the card can sort the way the action plan does",
   _peer2.get("severity") == "critical")
ck("A12 a store the comparison RAN on and did not name is met",
   [r for r in M.store_items(ROWS[0], None, True) if r["key"] == "peer_gap"][0]["state"] == M.MET)
# THE DISTINCTION THAT MATTERS: "compared and keeping up" vs "could not be compared". Collapsing them
# would tick a pass mark the store never earned.
_nc = [r for r in M.store_items(ROWS[0], None, False) if r["key"] == "peer_gap"][0]
ck("A13 a store that could NOT be compared is no_target with the reason, never a pass mark",
   _nc["state"] == M.NO_TARGET and "not compared" in (_nc["detail"] or ""))

# THE SHAPE PIN. The conversion block is FLAT on the summary row and carries the engine's own
# `meets_target`. The first draft of the module read a `conversion.store` nesting that does not
# exist, so every store's conversion silently read as untargeted and the fixture agreed with it.
# These two checks are what stop that coming back.
_cv = [r for r in M.store_items(ROWS[0], None, True) if r["key"] == "conversion"][0]
ck("A14 conversion is read FLAT off the row, with its numbers carried (not swallowed as untargeted)",
   _cv["state"] != M.NO_TARGET and _cv["target"] == 30.0 and _cv["achieved"] == 40.0
   and "100 bill payments" in (_cv["detail"] or ""), _cv)
ck("A15 … and the state DEREFERENCES the engine's own `meets_target`, never a second comparison",
   [r for r in M.store_items({**ROWS[0], "conversion": _conv(40, 100, 40.0, meets=False)},
                             None, True) if r["key"] == "conversion"][0]["state"] == M.MISSED)
ck("A16 a nested `conversion.store` is NOT what the engine sends, and is not quietly accepted either",
   [r for r in M.store_items({**ROWS[0], "conversion": {"store": {"rate": 40.0, "target": 30.0}}},
                             None, True) if r["key"] == "conversion"][0]["state"] == M.NO_TARGET)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§B the cards — one per DM, one per manager above, and nobody invented")
OUT = M.build(ROWS, CHAIN, peer_items=PEER, aggregate=_agg, peer_compared=COMPARED)
_dm = {c["employee_id"]: c for c in OUT["dm_cards"]}
ck("B1 a card per DM the tree names, and no card for a DM it does not",
   set(_dm) == {"E-DMA", "E-DMB"}, sorted(_dm))
ck("B2 each DM's card holds exactly their own stores",
   _dm["E-DMA"]["store_codes"] == ["B-1", "B-2"] and _dm["E-DMB"]["store_codes"] == ["B-3"])
ck("B3 the DM card carries the per-store check-off, not just a total",
   len(_dm["E-DMA"]["store_rows"]) == 2
   and all(r["items"] and r["tally"] for r in _dm["E-DMA"]["store_rows"]))
ck("B4 the DM's own total is the sum of their stores' items",
   _dm["E-DMA"]["tally"]["met"] + _dm["E-DMA"]["tally"]["missed"]
   == sum(r["tally"]["checked"] for r in _dm["E-DMA"]["store_rows"]))
ck("B5 the roll-up is the INJECTED aggregate (the area view's own function), not a local sum",
   _dm["E-DMA"]["collective"] == {"stores": 2, "_injected": True})
ck("B6 no aggregate injected → no collective block, rather than a second roll-up invented here",
   "collective" not in M.build(ROWS, CHAIN, aggregate=None)["dm_cards"][0])
ck("B7 the DM whose store has NO targets set is never shown as failing — zero missed, five untargeted",
   _dm["E-DMB"]["tally"]["missed"] == 0 and _dm["E-DMB"]["tally"]["no_target"] == 5)
ck("B7b … and a DM with nothing measurable at all scores None, not 0%",
   M.build([ROWS[2]], CHAIN, aggregate=_agg)["dm_cards"][0]["tally"]["score_pct"] is None)
ck("B8 cards sort worst-first, so the card with something to answer for is the one on top",
   [c["employee_id"] for c in OUT["dm_cards"]][0] == "E-DMA")

section("§B2 the level above — the DMs beneath them, not the stores again")
_mm = OUT["manager_cards"]
ck("B9 one card for the manager above the districts",
   [c["employee_id"] for c in _mm] == ["E-MM"])
ck("B10 its rows are the DMs, each with that DM's own totals — the higher-level view the owner asked for",
   [r["employee_id"] for r in _mm[0]["dm_rows"]] == ["E-DMA", "E-DMB"]
   and _mm[0]["dms"] == 2
   and _mm[0]["dm_rows"][0]["tally"] == _dm["E-DMA"]["tally"])
ck("B11 the manager's own total covers every store under their DMs",
   _mm[0]["stores"] == 3 and _mm[0]["tally"]["checked"]
   == _dm["E-DMA"]["tally"]["checked"] + _dm["E-DMB"]["tally"]["checked"])
ck("B12 … and the card does NOT repeat the store list one level up (that is the DM's card's job)",
   "store_rows" not in _mm[0])

section("§B3 a store the tree cannot place is reported, never dropped and never given an owner")
# A store missing from a card reads as a store with nothing to answer for. Measured 2026-10-08, the
# org tree names a DM for only 6 of 29 house stores and 0 of 20 Luxelink stores, so this is most of
# the org today — which is exactly why it is surfaced with the fix named.
_un = {u["store_code"]: u for u in OUT["unassigned"]}
ck("B13 the orphan store is in `unassigned`, with its own check-off kept",
   set(_un) == {"B-9"} and _un["B-9"]["tally"]["checked"] > 0)
ck("B14 … and the reason distinguishes 'district has no manager' from 'not in the tree'",
   "no manager is recorded" in _un["B-9"]["reason"]
   and "not under a district" in M.build(
       [ROWS[3]], {"B-9": {"district": None, "dm": [], "above": []}})["unassigned"][0]["reason"])
ck("B15 it appears on NOBODY's card — never attributed to the manager above by default",
   all("B-9" not in c["store_codes"] for c in OUT["dm_cards"] + OUT["manager_cards"]))
ck("B16 coverage states the shortfall in numbers a person can act on",
   OUT["coverage"]["stores"] == 4 and OUT["coverage"]["stores_with_a_dm"] == 3
   and OUT["coverage"]["stores_without_a_dm"] == 1 and OUT["coverage"]["dms"] == 2
   and OUT["coverage"]["managers_above"] == 1, OUT["coverage"])
ck("B17 the note names the FIX (the org chart), and stays silent when coverage is complete",
   "org chart" in (M.coverage_note(OUT["coverage"]) or "")
   and M.coverage_note({"stores": 3, "stores_without_a_dm": 0}) is None)
ck("B18 a store under TWO DMs is on both cards and the placement count says so, so the cards still add up",
   M.build([ROWS[0]], {"B-1": {"district": {"id": "u1", "name": "D1"},
                               "dm": [DM_A, DM_B], "above": [MM]}},
           aggregate=_agg)["coverage"]["store_card_placements"] == 2)
ck("B19 no rows at all → empty cards and an honest coverage block, never a crash",
   M.build([], {})["coverage"]["stores"] == 0 and M.build(None, None)["dm_cards"] == [])
ck("B20 a manager is keyed on EMPLOYEE ID, not a name — two people share a name, and a card is an accusation",
   M.build(ROWS, {**CHAIN, "B-3": {"district": {"id": "u2", "name": "D2"},
                                   "dm": [{"employee_id": "E-DMX", "name": "Dana M"}],
                                   "above": [MM]}},
           aggregate=_agg)["coverage"]["dms"] == 2)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§C the item registry is ONE list — a card cannot grow a second idea of what is assigned")
ck("C1 every ITEMS entry is fully declared (key, label, unit, source)",
   all({"key", "label", "unit", "source"} <= set(i) for i in M.ITEMS))
ck("C2 the keys are unique and the registry is what `store_items` iterates",
   len(set(M.ITEM_KEYS)) == len(M.ITEM_KEYS)
   and [r["key"] for r in M.store_items(ROWS[0], None, True)] == list(M.ITEM_KEYS))
ck("C3 the four pay categories are the engine's own, not a second list of category names",
   set(M.TARGET_CATEGORIES) <= set(M.ITEM_KEYS))
ck("C4 the payload hands the registry out, so a surface renders the declared items rather than its own",
   OUT["items"] == M.ITEMS)
# Adding an item must flow through: a registry nothing iterates is the §19.18 trap (written, not wired).
_extra = dict(M.ITEMS[0], key="made_up", label="Made up")
M.ITEMS.append(_extra)
try:
    _grown = [r["key"] for r in M.store_items(ROWS[0], None, True)]
finally:
    M.ITEMS.remove(_extra)
ck("C5 a NEW item in the registry appears on every card with no other edit (the registry is wired, not decorative)",
   "made_up" in _grown)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§D the un-wire lock — the cards dereference, they never re-derive")
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "app", "modules", "commcalc", "manager_report_card.py")
_src = open(_SRC).read()
_tree = ast.parse(_src)
ck("D1 the pure module imports NOTHING — no DB, no router, no engine (every fact is handed in)",
   not [n for n in ast.walk(_tree) if isinstance(n, (ast.Import, ast.ImportFrom))])
# THE REAL RISK: a card computing its own attainment percentage, which would let it disagree with the
# DM's own Targets screen about the same store on the same day. `attainment_pct` is the one formula.
ck("D2 … and it never spells an attainment formula of its own",
   "attainment" not in _src.replace("attainment_pct", "").replace("attainment_fraction", ""))
ck("D3 it never decides who is BEHIND their peers — no band, median or gap is computed here",
   not any(t in _src for t in ("band_of(", "resolve_bands(", "_median(", "gap_to_median =",
                               "HOUSE_BANDS")))
ck("D4 it never walks the org tree — the chain arrives as an argument",
   not any(t in _src for t in ("district_for(", "managers_at(", "chain_for(", "index_tree(",
                               "org_units", "org_managers")))

_RSRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "app", "modules", "commcalc", "router.py")
_rsrc = open(_RSRC).read()
_ep = _rsrc.split('@router.get("/targets/{period}/report-cards")', 1)
ck("D5 the endpoint exists", len(_ep) == 2)
_ep = _ep[1].split("\n@router.", 1)[0] if len(_ep) == 2 else ""
ck("D6 it CALLS the targets summary rather than reassembling it (one code path, one answer)",
   "await get_targets_summary(" in _ep)
ck("D7 … and reads no targets table, actuals or shift of its own",
   not any(t in _ep for t in ("table('targets')", '_fetch_actuals(', '_fetch_shifts(',
                              "derive_monthly_by_cat", "compute_scope(")))
ck("D8 it injects THE one attainment roll-up, so a card's totals are the area view's own function",
   "aggregate=targets_engine.aggregate_stores" in _ep)
ck("D9 it asks the ONE org-tree walk, in bulk",
   "_org_chain.dm_by_store(**org_chain_inputs(org_id))" in _ep)
ck("D10 it takes the peer verdict from the ONE shared assembly, never its own band",
   "_peer_comparison_payload(" in _ep and "_peercmp.peer_items_by_store(" in _ep)
ck("D11 a failure in the tree or the comparison is REPORTED, not swallowed into a pass mark",
   "'errors'" in _ep and "chain_error" in _ep and "peer_error" in _ep)
ck("D12 it inherits the summary's own scope filtering rather than re-implementing a keyset",
   "scope_keyset(" not in _ep and "'scope': summary.get('scope')" in _ep)
# D2 IS NON-VACUOUS ONLY IF THE STRING IT LOOKS FOR CAN APPEAR. Prove the search sees a planted one,
# so this cannot pass merely because the file is short — the §J6 / §K8 trap.
ck("D13 … and D2's search is not vacuous: it does see a planted attainment formula",
   "attainment" in (_src + "\n_pct = 100.0 * achieved / monthly  # attainment\n")
   .replace("attainment_pct", "").replace("attainment_fraction", ""))

print(f"\n{'=' * 70}")
if _fail:
    print(f"FAILED {_fail}: " + ", ".join(_failed))
else:
    print(f"Manager report cards — all {_pass} checks passed")
sys.exit(1 if _fail else 0)
