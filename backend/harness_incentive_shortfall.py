#!/usr/bin/env python3
"""PROOF — how close a store-day came to its payout, and what missing it cost. DB-free, stdlib.

The oracle is the owner's own sentence, 2026-11-18: *"the reps have done shipments but not the
overnight or second day — if they did one or two more shipments for overnight they would have gotten
paid for all the overnights they have done, we need to drive that behavior with constant coaching."*

THE THREE THINGS THIS MUST HOLD APART, because the whole design is the boundary between them:

    a NEAR MISS          one or two more sales was the whole difference    -> coach the rep
    a DISTANT miss       a real miss, nowhere near one more sale           -> plan, never promise
    a BLOCKED gate       the config could not be checked at all            -> an admin's item

A check that only proved the first would pass a build that tells a rep "two more and you'd have been
paid" on a day they were forty short, and that blames a rep for an admin's unfinished rule. §D and
§E are that negative half, and they are the half that matters.

  §A  what was short, in the gate's own unit — and the questions that have no answer
  §B  within reach: both policy tests, and the house default
  §C  the engine answers "what did it cost" — asked, never re-derived
  §D  NOT a near miss: ceiling gates, unverifiable gates, out-of-reach misses
  §E  the owner's own shape end to end: 3 parcels against a 5-parcel gate
  §F  TWO TENANTS, opposite vocabularies, one engine — no leakage either way
  §G  the period fold the coaching card reads
  §J  the two shapes arming this harness exposed
  §J  the two shapes arming this harness exposed — a partial gate, and a mixed per-person day
  §H  ONE home — the payout arithmetic and the words are dereferenced, not copied
  §I  negative controls + RULE TWO
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

import harnesslib as _hl
from app.modules.commcalc import incentive_shortfall as sf
from app.modules.commcalc import daily_incentive as di
from app.modules.closing import metric_catalog as mc

P = F = 0
def ok(cond, label, extra=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {label}")
    else:
        F += 1
        print(f"  FAIL  {label}" + (f"\n          {extra}" if extra else ""))

def eq(a, b, label):
    ok(a == b, label, f"got {a!r}, want {b!r}")

_HERE = os.path.dirname(os.path.abspath(__file__))
_SF = os.path.join(_HERE, "app/modules/commcalc/incentive_shortfall.py")
_DI = os.path.join(_HERE, "app/modules/commcalc/daily_incentive.py")


def _code_only(path):
    """The file's CODE, prose thrown away — dereferenced from `harnesslib`, never hand-tokenised
    (PEP 701 made a hand-rolled f-string reader version-dependent, and a duplicate of it once passed
    here on 3.13 and failed CI on 3.11).

    A guard that greps RAW source can be both satisfied and tripped by prose: a dereference check is
    satisfied by a name in a docstring, and a forbidden-word check is tripped by the owner's own
    quote. Every check about what the code DOES reads this."""
    return _hl.py_code_only(open(path).read()).lower()


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FIXTURES — config, not code. Nothing here is spelled in any module under test; §I proves it.
#
# TENANT ONE is the owner's own shape: a per-parcel payout gated on the store reaching five parcels
# across two categories. TENANT TWO is deliberately nothing like it — a money gate, a percentage
# payout, different words — so §F can prove one tenant's vocabulary never answers the other's day.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
CAT_A = mc.build([
    {"field_key": "overnight", "label": "Overnight", "value_kind": "count",
     "declared_by": "employee", "unit_price": 2.5, "in_revenue": True},
    {"field_key": "two_day", "label": "Two Day", "value_kind": "count",
     "declared_by": "employee", "unit_price": 2.5, "in_revenue": True},
    {"field_key": "copies", "label": "Copies", "value_kind": "amount",
     "declared_by": "employee", "in_revenue": True},
])
RULE_A = {"id": "r-a", "label": "Overnight And Two Day", "rule_kind": "per_unit", "is_active": True,
          "payout": {"rate": 2.5, "basis": "units", "metric_keys": ["overnight", "two_day"]},
          "qualifier": {"scope": "store", "basis": "units", "op": "gte", "threshold": 5,
                        "metric_keys": ["overnight", "two_day"]}}

CAT_B = mc.build([
    {"field_key": "widgets", "label": "Widgets", "value_kind": "amount",
     "declared_by": "employee", "in_revenue": True},
])
RULE_B = {"id": "r-b", "label": "Widget Bonus", "rule_kind": "percent_of_amount", "is_active": True,
          "payout": {"rate": 0.1, "basis": "amount", "metric_keys": ["widgets"]},
          "qualifier": {"scope": "store", "basis": "amount", "op": "gte", "threshold": 1000.0,
                        "metric_keys": ["widgets"]}}


def _lines(rows):
    return [{"employee_name": w, "metric_key": k, "units": u, "amount": a} for w, k, u, a in rows]


def _day(catalog, rows, **kw):
    """One store-day in the keyword bag `compute_day` takes — built once, so every check below is
    measured on the identical day under the identical revenue scope as a real run."""
    lines = _lines(rows)
    d = {"declared_map": mc.declared(catalog, None, lines),
         "per_employee": mc.by_employee(catalog, lines)}
    d.update(kw)
    return d


# THE OWNER'S DAY: two people, three parcels between them, against a gate of five.
DAY_SHORT = _day(CAT_A, [("ANA", "overnight", 2, 0.0), ("BEN", "two_day", 1, 0.0),
                         ("ANA", "copies", 0, 40.0)])
# The same day with the two extra parcels he described.
DAY_CLEARED = _day(CAT_A, [("ANA", "overnight", 4, 0.0), ("BEN", "two_day", 1, 0.0),
                           ("ANA", "copies", 0, 40.0)])
# A day nowhere near it.
DAY_DISTANT = _day(CAT_A, [("ANA", "copies", 0, 40.0)])


print("\n§A  WHAT WAS SHORT, IN THE GATE'S OWN UNIT")
_s = sf.shortfall_of({"passed": False, "scope": "store", "op": "gte", "basis": "units",
                      "threshold": 5, "value": 3, "metric_keys": ["overnight", "two_day"]})
eq(_s["short_by"], 2, "A1: 3 parcels against a gate of 5 is 2 short")
eq(_s["unit"], "units", "A2: and the shortfall is in units, not dollars")
eq((_s["value"], _s["threshold"]), (3, 5), "A3: both sides are whole parcels, not 3.0 and 5.0")
eq(type(_s["short_by"]), int, "A4: a unit shortfall is an int — '1.0 parcels short' is not a sentence")
_m = sf.shortfall_of({"passed": False, "scope": "store", "op": "gte", "basis": "amount",
                      "threshold": 1000.0, "value": 912.5, "metric_keys": ["widgets"]})
eq(_m["short_by"], 87.5, "A5: a money gate is short by dollars and cents")
eq(_m["unit"], "money", "A6: and says so")
# A STRICT floor is not cleared by matching it. Reporting 0 short on a day that was NOT paid is the
# shape that makes a surface call a passing day a failure, so it is pinned.
eq(sf.shortfall_of({"passed": False, "op": "gt", "basis": "units", "threshold": 5, "value": 5,
                    "metric_keys": []})["short_by"], 1,
   "A7: matching a strict floor exactly is still one unit short")
eq(sf.shortfall_of({"passed": False, "op": "gt", "basis": "amount", "threshold": 100.0,
                    "value": 100.0, "metric_keys": []})["short_by"], 0.01,
   "A8: on money, the smallest amount that clears a strict floor")
eq(sf.shortfall_of({"passed": False, "op": "gte", "basis": "units", "threshold": 5, "value": 2.4,
                    "metric_keys": []})["short_by"], 3,
   "A9: part of a parcel rounds UP — two and a half more sales is three sales")
ok(sf.shortfall_of({"passed": True, "op": "gte", "basis": "units", "threshold": 5, "value": 9}) is None,
   "A10: a gate that PASSED has no shortfall")
ok(sf.shortfall_of({}) is None, "A11: and neither does nothing at all")
ok(sf.shortfall_of({"passed": False, "op": "gte", "basis": "units", "threshold": 5,
                    "value": 5}) is None,
   "A12: a non-strict floor the day reached is not short (a verdict that disagrees with itself "
   "reports no shortfall rather than a negative one)")


print("\n§B  WITHIN REACH — both policy tests, and the house default")
eq(sf.REACH_UNITS_DEFAULT, 2.0, "B1: the house default is the owner's own 'one or two more'")
ok(sf.within_reach({"basis": "units", "short_by": 2, "threshold": 5}),
   "B2: two parcels short of five is within reach")
ok(not sf.within_reach({"basis": "units", "short_by": 3, "threshold": 5}),
   "B3: three is not — on the default")
ok(sf.within_reach({"basis": "units", "short_by": 3, "threshold": 5}, reach_units=3),
   "B4: unless the business declares a wider window (a POLICY, so a per-org row overrides it)")
# The PROPORTIONAL test exists because 'two more' is meaningless on a large gate.
ok(sf.within_reach({"basis": "units", "short_by": 20, "threshold": 400}),
   "B5: 20 short of 400 is within reach proportionally, though far outside the unit allowance")
ok(sf.within_reach({"basis": "amount", "short_by": 87.5, "threshold": 1000.0}),
   "B6: $87.50 short of $1,000 is within reach")
ok(not sf.within_reach({"basis": "amount", "short_by": 600.0, "threshold": 1000.0}),
   "B7: $600 short of $1,000 is not")
ok(not sf.within_reach({"basis": "units", "short_by": 0, "threshold": 5}),
   "B8: nothing short is not 'within reach' — it is not a miss at all")
ok(not sf.within_reach({"basis": "amount", "short_by": 5.0, "threshold": 0.0}),
   "B9: a threshold of zero cannot be proportionally near — and does not divide by zero")


print("\n§C  THE ENGINE ANSWERS WHAT IT COST — asked, never re-derived")
_it = sf.rule_shortfall(di.compute_day, CAT_A, RULE_A, DAY_SHORT)
ok(_it is not None, "C1: the owner's day produces a shortfall item")
eq(_it["kind"], sf.KIND_NEAR, "C2: and it is a NEAR miss")
eq(_it["shortfall"]["short_by"], 2, "C3: two parcels short")
# THE LOAD-BEARING FIGURE. 3 parcels already sold x $2.50 = $7.50, withheld on the work ALREADY
# DONE. If this ever reads 2 x $2.50 the design has been inverted into 'you lose the pay on the
# units you were short of', which is the misunderstanding the whole module exists to correct.
eq(_it["forgone"], 7.5, "C4: the gate cost the pay on all THREE parcels already sold, not on the two "
                        "that were missing")
_paid = di.compute_day(CAT_A, [RULE_A], **DAY_CLEARED)
eq(round(sum(l["amount"] for l in _paid["lines"]), 2), 12.5,
   "C5: and the engine agrees — with the two extra parcels the day pays 5 x $2.50")
eq(_it["would_have_paid"], {"ANA": 5.0, "BEN": 2.5},
   "C6: whose pay it was, out of the engine's own split")
ok(sf.rule_shortfall(di.compute_day, CAT_A, RULE_A, DAY_CLEARED) is None,
   "C7: a day that WAS paid has no shortfall item at all")
eq(sf.opened_rule(RULE_A)["payout"], RULE_A["payout"],
   "C8: opening the gate changes the qualifier and NOTHING else — the counterfactual is about the "
   "rule the tenant declared")
eq(sf.opened_rule(RULE_A)["qualifier"], {"scope": "none"}, "C9: and the qualifier is what it opens")
eq(RULE_A["qualifier"]["threshold"], 5, "C10: the tenant's own rule row is not mutated")


print("\n§D  NOT A NEAR MISS — the half that matters")
# (1) A CEILING gate is not missed by doing too little, so 'one or two more' is the wrong advice.
ok(sf.shortfall_of({"passed": False, "op": "lte", "basis": "units", "threshold": 5,
                    "value": 9, "metric_keys": []}) is None,
   "D1: a gate that CAPS is not missed by selling too little, and gets no shortfall")
ok(sf.shortfall_of({"passed": False, "op": "eq", "basis": "units", "threshold": 5,
                    "value": 3, "metric_keys": []}) is None,
   "D2: nor an exact-match gate")
# (2) A gate that could not be VERIFIED is an admin's item, never a rep's.
_bad = {**RULE_A, "qualifier": {**RULE_A["qualifier"], "metric_keys": ["not_declared_here"]}}
_bi = sf.rule_shortfall(di.compute_day, CAT_A, _bad, DAY_SHORT)
eq(_bi["kind"], sf.KIND_BLOCKED, "D3: a qualifier naming an undeclared category is BLOCKED")
eq(_bi["severity"], sf.SEVERITY_BLOCKED, "D4: with its own severity, so no surface can style it as "
                                         "a rep's shortfall")
ok(_bi["shortfall"] is None, "D5: and it carries NO shortfall figure — there is nothing to be short of")
ok("admin" in sf.coach_sentence(_bi).lower(),
   "D6: its sentence addresses whoever can fix the rule, not the rep")
eq(sf.steps(_bi), [], "D7: and it gets NO walkthrough — a rep cannot act on an unfinished rule")
ok(sf.blocked_reason({"passed": False, "op": "gte", "basis": "units", "threshold": 5,
                      "value": 3}) == "",
   "D8: a gate that CAN be read is not blocked")
# (3) An out-of-reach miss is reported, never dressed up.
_di_item = sf.rule_shortfall(di.compute_day, CAT_A, RULE_A, DAY_DISTANT)
eq(_di_item["kind"], sf.KIND_DISTANT, "D9: a day that sold no parcels at all is a DISTANT miss")
ok(not _di_item["within_reach"], "D10: and is not within reach")
ok("further than one or two more sales" in sf.coach_sentence(_di_item),
   "D11: its sentence says so in words rather than promising it was nearly there")
ok("tomorrow" not in " ".join(s["step"] for s in sf.steps(_di_item)),
   "D12: and it gets no 'do this tomorrow' step")
eq(_di_item["forgone"], 0.0,
   "D13: it cost nothing, because the rule pays per parcel and there were none — a gate is only "
   "blamed for money the day would actually have earned")


print("\n§E  THE OWNER'S OWN SHAPE, END TO END")
_s5 = sf.day_shortfalls(di.compute_day, CAT_A, [RULE_A], DAY_SHORT)
eq(len(_s5), 1, "E1: one unpaid rule on the day")
_sent = sf.coach_sentence(_s5[0])
ok("2 more sales" in _sent, "E2: the sentence leads with the two sales that were the difference")
ok("Overnight" in _sent and "Two Day" in _sent or "Overnight" in _sent,
   "E3: and names what was already sold in the TENANT's own words")
ok("withholds it on everything already sold" in _sent,
   "E4: it states the mechanism the owner asked to be taught — the gate withholds the pay on the "
   "work already done")
ok("$7.50" in _sent, "E5: with what that cost")
_st = sf.steps(_s5[0])
eq([s["step"] for s in _st], ["did", "gate", "gap", "cost", "next"],
   "E6: the guided flow is five ordered steps, ending in what to do tomorrow")
eq((_st[0]["figure"], _st[1]["figure"], _st[2]["figure"]), (3, 5, 2),
   "E7: did 3, needed 5, short 2 — as FIGURES, so a surface draws the bar rather than parsing prose")
eq(_st[3]["figure"], 7.5, "E8: and the cost is the engine's own number")
eq([d["label"] for d in _st[0]["detail"]], ["Overnight", "Two Day"],
   "E9: the first step itemises what was sold, labelled from the tenant's catalog")
_ai = sf.action_item(_s5[0])
eq(_ai["metric"], sf.METRIC_LABEL, "E10: it renders as an action-plan item")
eq(_ai["severity"], "critical", "E11: a near miss is critical — the money was already earned")
ok(_ai["incentive"]["steps"], "E12: carrying the walkthrough, so the plan and the card cannot "
                              "disagree about what the rep is shown")
eq(_ai["shortfall_pct"], 40.0, "E13: 2 of 5 is 40% short")


print("\n§F  TWO TENANTS, OPPOSITE VOCABULARIES, ONE ENGINE")
DAY_B = _day(CAT_B, [("CHRIS", "widgets", 0, 912.5)])
_b = sf.rule_shortfall(di.compute_day, CAT_B, RULE_B, DAY_B)
eq(_b["shortfall"]["short_by"], 87.5, "F1: tenant two is $87.50 short of its own $1,000 gate")
eq(_b["kind"], sf.KIND_NEAR, "F2: and that is within reach on the proportional test")
eq(_b["forgone"], 91.25, "F3: costing 10% of the $912.50 already sold")
ok("Widget Bonus" in sf.coach_sentence(_b), "F4: in tenant two's own words")
ok("Overnight" not in sf.coach_sentence(_b) and "parcel" not in sf.coach_sentence(_b).lower(),
   "F5: and NOT a word of tenant one's — which is the leak this check exists to catch")
ok(sf.rule_shortfall(di.compute_day, CAT_B, RULE_A, DAY_B)["kind"] == sf.KIND_BLOCKED,
   "F6: tenant one's RULE against tenant two's catalog is BLOCKED, not answered — nothing declared "
   "means nothing identified")
ok("Widget" not in sf.coach_sentence(_s5[0]),
   "F7: and tenant one's sentence carries none of tenant two's words either")
eq(sf.rule_shortfall(di.compute_day, CAT_A, RULE_A, DAY_SHORT)["forgone"], 7.5,
   "F8: tenant one's answer is unchanged by tenant two having been measured")


print("\n§G  THE PERIOD FOLD THE COACHING CARD READS")
_folded = sf.fold_days([
    {"date": "2026-10-20", "store_code": "S1", "items": _s5},
    {"date": "2026-10-21", "store_code": "S1",
     "items": sf.day_shortfalls(di.compute_day, CAT_A, [RULE_A], DAY_DISTANT)},
    {"date": "2026-10-22", "store_code": "S1", "items": []},
])
eq(_folded["days_missed"], 2, "G1: two of the three days earned nothing from a gated rule")
eq(_folded["forgone"], 7.5, "G2: and those gates cost $7.50 between them")
eq(_folded["forgone_within_reach"], 7.5,
   "G3: all of which was on the day that was within reach — the part coaching can move")
eq([r["rule"] for r in _folded["by_rule"]], ["Overnight And Two Day"], "G4: folded per rule")
eq(_folded["by_rule"][0]["days"], 2, "G5: both days against it")
eq(_folded["by_rule"][0]["days_within_reach"], 1, "G6: one of them close")
eq([e["employee"] for e in _folded["by_employee"]], [], "G7: a STORE-scope gate names nobody — the "
                                                        "store missed it, not a person")
eq(_folded["closest"]["date"], "2026-10-20",
   "G8: and the day to open the walkthrough on is the closest one")
ok("$7.50" in sf.period_sentence(_folded) and "100%" in sf.period_sentence(_folded),
   "G9: the card's lead line carries the cost and how much of it was within reach")
eq(sf.period_sentence(sf.fold_days([])), "", "G10: a period with nothing missed says nothing")
eq(sf.fold_days([{"date": "d", "items": [{"kind": sf.KIND_BLOCKED, "rule": "R",
                                          "forgone": 99.0}]}])["forgone"], 0.0,
   "G11: a BLOCKED gate contributes NO money to the total — its cost is unknown, and guessing it "
   "would put an admin's unfinished rule into a rep's coaching figure")
eq(len(sf.fold_days([{"date": "d", "items": [{"kind": sf.KIND_BLOCKED, "rule": "R"}]}])["blocked"]),
   1, "G12: it is carried separately instead, so it is still visible")


print("\n§J  THE TWO SHAPES ARMING THE HARNESS EXPOSED")
# Both of these passed for the WRONG REASON until the module was deliberately broken to check that
# they could fail at all (2026-11-18). Each pins a real defect the arming found.
#
# (1) A verdict carrying BOTH a measured basis AND an undeclared category. `_gate` does not emit
#     that shape today, but the two facts live on separate keys and nothing stops it: the figure
#     measured would be a PARTIAL one, so "two more and you would have been paid" is not knowable
#     from it, and coaching anybody on it would be inventing a shortfall out of an unfinished rule.
_half = {"passed": False, "scope": "store", "op": "gte", "basis": "units", "threshold": 5,
         "value": 3, "missing": ["not_declared_here"], "metric_keys": ["two_day"]}
ok(sf.shortfall_of(_half) is None,
   "J1: a gate that measured something but names an undeclared category yields NO shortfall — the "
   "figure it measured is partial")
ok(sf.blocked_reason(_half) != "",
   "J2: it is BLOCKED instead, so the day is still reported rather than silently dropped")

# (2) AN EMPLOYEE-SCOPE GATE ONE PERSON CLEARED AND ANOTHER MISSED. The module used to return None
#     for any rule that paid anything at all, which swallowed exactly this: the individual near miss
#     is the whole point, and it lives on a day the rule DID pay somebody else.
RULE_EMP = {"id": "r-emp", "label": "Per Person Gate", "rule_kind": "per_unit", "is_active": True,
            "payout": {"rate": 2.5, "basis": "units", "metric_keys": ["overnight", "two_day"]},
            "qualifier": {"scope": "employee", "basis": "units", "op": "gte", "threshold": 2,
                          "metric_keys": ["overnight", "two_day"]}}
_mixed = sf.rule_shortfall(di.compute_day, CAT_A, RULE_EMP, DAY_SHORT)
_as_is = di.compute_day(CAT_A, [RULE_EMP], **DAY_SHORT)
eq(round(sum(l["amount"] for l in _as_is["lines"]), 2), 5.0,
   "J3: the day DID pay — one person cleared their own gate")
ok(_mixed is not None,
   "J4: and the person who missed theirs is STILL coached, on a day the rule paid somebody else")
eq(_mixed["employee"], "BEN", "J5: named — it is their shortfall, not the store's")
eq(_mixed["shortfall"]["short_by"], 1, "J6: one sale short of their own gate")
eq(_mixed["forgone"], 2.5,
   "J7: and it cost THEM $2.50 — the difference the engine reports, never the whole day's payout")
eq(sf.fold_days([{"date": "2026-10-20", "store_code": "S1", "items": [_mixed]}])["by_employee"],
   [{"employee": "BEN", "days": 1, "days_within_reach": 1, "forgone": 2.5}],
   "J8: so the period fold can address a person, which a store-scope gate never can")
_both = _day(CAT_A, [("ANA", "overnight", 2, 0.0), ("BEN", "two_day", 2, 0.0)])
ok(sf.rule_shortfall(di.compute_day, CAT_A, RULE_EMP, _both) is None,
   "J9: and a day on which everybody cleared their own gate coaches nobody")
# (3) TWO PEOPLE SHORT BY DIFFERENT AMOUNTS. The one to coach is the one a single extra sale could
#     have rescued; opening the walkthrough on the person who was miles off is the false
#     encouragement §D exists to prevent, said to the wrong person. Nothing caught this until a plant
#     swapped the tightest miss for the widest (2026-11-18).
RULE_EMP4 = {**RULE_EMP, "qualifier": {**RULE_EMP["qualifier"], "threshold": 4}}
_two_short = _day(CAT_A, [("ANA", "overnight", 3, 0.0), ("BEN", "two_day", 1, 0.0)])
_tight = sf.rule_shortfall(di.compute_day, CAT_A, RULE_EMP4, _two_short)
eq(_tight["employee"], "ANA",
   "J11: with two people short, the TIGHTEST miss is the one coached — one sale, not three")
eq(_tight["shortfall"]["short_by"], 1, "J12: and the figure shown is that person's, not the worst one")
eq(_tight["forgone"], 10.0,
   "J13: while the cost is the whole gate's — all four sales the two of them made went unpaid")

eq(sf.rule_shortfall(di.compute_day, CAT_A, RULE_EMP, DAY_CLEARED)["employee"], "BEN",
   "J10: while a day one person ran away with still names the person who was left one short — the "
   "store total clearing the gate is not the question an employee-scope gate asks")


print("\n§H  ONE HOME — dereferenced, not copied")
_sfc = _code_only(_SF)
for name in ("mc.by_key", "mc.units_of", "mc.amount_of"):
    ok(name in _sfc, f"H1: the tenant's words and figures come from metric_catalog ({name})")
ok("engine(" in _sfc, "H2: the payout is the ENGINE's answer — this module calls it")
for banned in ("rate", "cap", "split_pool", "per_unit", "percent_of_amount", "threshold_pool"):
    ok(f'"{banned}"' not in _sfc and f"'{banned}'" not in _sfc,
       f"H3: and carries no payout arithmetic of its own — no '{banned}' literal")
ok("import" in _sfc and "daily_incentive" not in _sfc,
   "H4: it does NOT import the engine — the engine is passed in, so the dependency runs one way and "
   "the engine can never come to depend on this file")
ok("incentive_shortfall" not in _code_only(_DI),
   "H5: and the engine does not reference this module either")
ok("_gate(" not in _sfc, "H6: no second gate evaluation — the verdict the money was decided by "
                         "is the verdict this reads, so a shortfall can never disagree with the "
                         "payout it is explaining")


print("\n§I  NEGATIVE CONTROLS + RULE TWO")
# RULE TWO: no tenant, carrier or product name in code. The fixture words above are CONFIG and must
# not appear in the module; this is the check that keeps it that way.
#
# TWO SCOPES, deliberately. The CODE may carry none of these words at all — a product word in a
# branch or a rendered string is the per-tenant code RULE TWO forbids. The PROSE may carry them in
# exactly one place: inside the owner's own quoted sentence, which is the evidence this module exists
# for, and which is stripped below. Anywhere else in the prose is a product word creeping toward the
# code, so it is caught too.
_raw_full = open(_SF).read()
_quoted = _raw_full.split('*"')
_unquoted = (_quoted[0] + "".join(part.split('"*', 1)[-1] for part in _quoted[1:])).lower()
ok(len(_quoted) == 2, "I0: the module quotes the owner exactly once, so the exemption below is "
                      "bounded and cannot be widened by adding another quotation")
for word in ("overnight", "two day", "two_day", "parcel", "widget", "logistics", "cellfonz",
             "luxelink", "boost", "ups"):
    ok(word not in _sfc, f"I1: no tenant or product word in the CODE — '{word}'")
    ok(word not in _unquoted,
       f"I1b: nor anywhere in the prose outside the owner's own quoted sentence — '{word}'")
_raw = _raw_full.lower()
ok("$1,300" not in _raw and "22,653" not in _raw and "1300" not in _raw,
   "I2: and no tenant's live threshold or figure, which an admin can edit and which would make a "
   "docstring a lie the moment they did")
eq(sf.day_shortfalls(di.compute_day, CAT_A, [{**RULE_A, "is_active": False}], DAY_SHORT), [],
   "I3: an inactive rule cannot be missed")
eq(sf.day_shortfalls(di.compute_day, CAT_A, [], DAY_SHORT), [], "I4: no rules, no shortfalls")
eq(sf.day_shortfalls(di.compute_day, CAT_A, None, DAY_SHORT), [], "I5: and None is not a crash")
ok(sf.action_item({}) is None, "I6: an item that cannot support a sentence renders no plan item")
ok(sf.steps({}) == [], "I7: nor a walkthrough")
_ungated = {**RULE_A, "qualifier": {"scope": "none"}}
ok(sf.rule_shortfall(di.compute_day, CAT_A, _ungated, DAY_DISTANT) is None,
   "I8: a rule with NO gate has no shortfall even on a day it paid nothing — there was no gate to "
   "miss, and reporting one would invent a coaching moment out of a quiet day")

print(f"\n{'='*94}\n  {P} passed, {F} failed\n{'='*94}")
sys.exit(1 if F else 0)
