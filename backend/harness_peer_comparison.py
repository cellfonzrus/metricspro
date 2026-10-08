#!/usr/bin/env python3
"""PROOF — Peer Sales Comparison (owner directive 2026-10-08, index §59).

DB-FREE, stdlib only. Drives the REAL pure functions with fixtures, so what is proved here is what
runs in production.

What this file is for, section by section:
  §A  the traffic band is a measurement — the boundaries, and the label a person reads
  §B  the band cuts are CONFIG, and an explicitly empty list is a real answer
  §C  THE ADD-A-LINE ONE HOME — the whole measured platform vocabulary, and the EQUIVALENCE PIN that
      says `asset/router._promo_type` cannot have moved a promo column by dereferencing it
  §D  the roll-up — the distinct-transaction defect this report would have had if it summed
  §E  the honesty rules: unbanded stores, a band of one, "cannot answer" is never 0
  §F  the gap — median includes self, direction is declared not assumed
  §G  "lagging" has ONE definition, and the sentence it produces is arguable
  §H  the caveats — the three configuration facts a reader would otherwise mistake for performance
  §I  THE UN-WIRE LOCK — every cell field this module reads must still exist in `_sales_cell_agg`
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from app.modules.commcalc import peer_comparison as P          # noqa: E402
from app.modules.commcalc import line_class as LC              # noqa: E402

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}" + (f"  got={got!r}" if got is not None else ""))
        FAIL.append(name)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§A  the traffic band is a measurement")
# The house cuts are (150, 250, 400) — the LOWER bound of each band.
for n, want_idx in ((0, 0), (1, 0), (149, 0), (150, 1), (249, 1), (250, 2), (399, 2),
                    (400, 3), (10_000, 3)):
    idx, _lab = P.band_of(n, P.HOUSE_BANDS)
    ck(f"A1 {n} bill payments → band {want_idx}", idx == want_idx, idx)
ck("A2 the bottom band reads as a bound, not a judgement",
   P.band_of(10, P.HOUSE_BANDS)[1] == "Under 150 bill payments", P.band_of(10, P.HOUSE_BANDS)[1])
ck("A3 a middle band names both ends",
   P.band_of(300, P.HOUSE_BANDS)[1] == "250–399 bill payments", P.band_of(300, P.HOUSE_BANDS)[1])
ck("A4 the top band is open-ended",
   P.band_of(900, P.HOUSE_BANDS)[1] == "400+ bill payments", P.band_of(900, P.HOUSE_BANDS)[1])
# No band label anywhere contains a word of judgement — a store cannot argue with its own count, and
# that is the whole basis of the report. It CAN argue with being called "low".
_labels = [P.band_of(n, P.HOUSE_BANDS)[1] for n in (0, 200, 300, 900)]
ck("A5 no label judges the store",
   not any(w in l.lower() for l in _labels for w in ("low", "high", "poor", "weak", "top", "bad")),
   _labels)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§B  the band cuts are config (RULE TWO)")
ck("B1 missing → house", P.resolve_bands(None) == tuple(P.HOUSE_BANDS))
ck("B2 junk → house", P.resolve_bands("nonsense") == tuple(P.HOUSE_BANDS))
ck("B3 a tenant's cuts are honoured, sorted and de-duplicated",
   P.resolve_bands([400, "150", 150, 0, -5, "oops", 250]) == (150, 250, 400),
   P.resolve_bands([400, "150", 150, 0, -5, "oops", 250]))
# An EMPTY list is a real answer: "compare the whole estate together".
ck("B4 empty list = ONE band holding everything", P.resolve_bands([]) == ())
ck("B5 … and that band's label still reads as a bound",
   P.band_of(999, ())[1] == "0+ bill payments", P.band_of(999, ())[1])
ck("B6 with no cuts every store lands in band 0",
   {P.band_of(n, ())[0] for n in (1, 50, 500, 5000)} == {0})


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§C  THE ADD-A-LINE ONE HOME")
# Every distinct non-blank `contract_type` value live on the platform, measured 2026-10-08 over
# commcalc.daily_sales_feed + commcalc.raw_sales (34 values). This list IS the fixture: the predicate
# is proved against the real vocabulary, not against invented strings.
AAL_VALUES = [
    "Activation AAL", "Activation Add A Line", "Activation With IDV AAL", "BYOD Activation AAL",
    "BYOD Add A Line", "BYOD Internal Port AAL", "BYOD Port AAL", "BYOD Port-In Add A Line",
    "Eligible Port-In Add A Line", "Ineligible Port-In Add A Line", "Internal Port with IDV AAL",
    "PML Activation Add a Line", "PML BYOD Add a Line", "PML BYOD Port-In Add a Line",
    "Port AAL", "Port with IDV AAL",
]
NOT_AAL_VALUES = [
    "Activation", "Activation With IDV", "BYOD", "BYOD Activation", "BYOD Internal Port",
    "BYOD Port", "BYOD Port-In", "BYOD Swap", "BYOD Upgrade", "Eligible Port-In Activation",
    "Ineligible Port-In Activation", "Internal Port", "Internal Port with IDV",
    "PML Ineligible Port In Activation", "Port", "Port with IDV", "Swap", "Upgrade",
]
ck("C0 the fixture is the whole measured vocabulary (16 + 18 = 34 values)",
   len(AAL_VALUES) + len(NOT_AAL_VALUES) == 34)
_miss = [v for v in AAL_VALUES if not LC.is_add_a_line({"contract_type": v})]
ck("C1 every add-a-line value is found", not _miss, _miss)
_false = [v for v in NOT_AAL_VALUES if LC.is_add_a_line({"contract_type": v})]
ck("C2 and NOTHING else is (no false positive in 34 values)", not _false, _false)
ck("C3 a blank value is not an add-a-line", not LC.is_add_a_line({"contract_type": ""}))
ck("C4 a row with no such column is not an add-a-line", not LC.is_add_a_line({}))

# THE EQUIVALENCE PIN. `asset/router._promo_type` used to answer this question itself, with
# `"add a line" in ct or ct == "aal" or ct.endswith(" aal")`. It now dereferences the one home. This
# check is what says no promo column — and so no expected reimbursement — moved when it did.
def _retired_asset_router_aal(ct):
    ct = (ct or "").strip().lower()
    return bool(ct) and ("add a line" in ct or ct == "aal" or ct.endswith(" aal"))


_drift = [v for v in AAL_VALUES + NOT_AAL_VALUES
          if _retired_asset_router_aal(v) != LC.is_add_a_line({"contract_type": v})]
ck("C5 EQUIVALENCE PIN — identical to the retired asset-router expression on all 34 live values",
   not _drift, _drift)
ck("C6 … including the bare 'AAL' value the retired expression special-cased",
   LC.is_add_a_line({"contract_type": "AAL"}) is True)

# AN AAL IS A MODIFIER, NEVER A CLASS — THE PIN THAT SAYS THIS PR MOVED NO MONEY.
# `add_a_line` is a SEPARATE key on the resolved rules and `activation_class` must never read it, so
# declaring (or emptying) the add-a-line vocabulary cannot move a single line's class — and therefore
# cannot move a box count, a premium / byod / upgrade tally, a tier or a payout.
#
# NOT TESTED HERE, because it is NOT TRUE and never was: that removing the AAL words from a VALUE
# leaves its class alone. 'Port AAL' classifies as `port` while a bare 'Port' classifies as None,
# because the house activation gate deliberately does not accept the bare word 'port' (see the
# DECISION note of 2026-09-21 in line_class) while it has always accepted ' aal'. That is pre-existing
# behaviour this PR does not touch, and asserting otherwise would be a false pin.
_plain = LC.resolve_rules(None)
_declared = LC.resolve_rules({"add_a_line": ["extra line", "aal"]})
_emptied = LC.resolve_rules({"add_a_line": []})
_cls_moved = [v for v in AAL_VALUES + NOT_AAL_VALUES
              if len({LC.activation_class({"contract_type": v}, r)
                      for r in (_plain, _declared, _emptied)}) != 1]
ck("C7 declaring or emptying the add-a-line vocabulary moves NO line's class", not _cls_moved,
   _cls_moved)
ck("C7b … because activation_class does not read the add_a_line key at all",
   "add_a_line" not in __import__("inspect").getsource(LC.activation_class))

# CONFIG, not code (RULE TWO) — a tenant whose POS says it differently, and one that says nothing.
_tenant = LC.resolve_rules({"fields": ["category"], "add_a_line": ["extra line"]})
ck("C8 a tenant's own word is read, over its own field",
   LC.is_add_a_line({"category": "Postpaid >> Extra Line"}, _tenant))
ck("C9 … and the house words do not leak into its field",
   not LC.is_add_a_line({"category": "Activation AAL"}, _tenant))
_none = LC.resolve_rules({"add_a_line": []})
ck("C10 an explicitly EMPTY vocabulary is honoured ('our POS does not say' is a real answer)",
   not LC.is_add_a_line({"contract_type": "Activation AAL"}, _none))
ck("C11 … and is reported as unconfigured so a report shows blank, not 0",
   LC.add_a_line_configured(_none) is False and LC.add_a_line_configured(None) is True)
ck("C12 the house default is unchanged by this feature existing",
   LC.resolve_rules(None)["add_a_line"] == LC.HOUSE_ADD_A_LINE)

# The transaction grain: a 3-line AAL receipt is ONE add-a-line sale.
_rows = [{"trans_id": "T1", "contract_type": "Activation AAL"},
         {"trans_id": "T1", "contract_type": "Activation AAL"},
         {"trans_id": "T1", "contract_type": "Accessory"},
         {"trans_id": "T2", "contract_type": "Upgrade"}]
_u = LC.add_a_line_units(_rows)
ck("C13 distinct-transaction grain (a 2-line AAL receipt is one sale)",
   _u["transactions"] == 1 and _u["lines"] == 2, _u)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§D  the roll-up, and the defect it avoids")
# THE DEFECT, reproduced. `_sales_cell_agg` keys cells by (store, REP, DAY) and each cell holds
# DISTINCT-TRANSACTION SETS. One receipt can appear in two cells — two reps on one invoice, or a
# store open across a day boundary. Summing the set LENGTHS double-counts it; unioning the sets does
# not. This is the whole reason `roll_up` exists rather than a sum.
CELLS = {
    ("S1", "ANA", "2026-09-01"): {"store": "S1", "box_count": 2, "accessory_rev": 100.0,
                                  "_prem": {"T1", "T2"}, "_port": set(), "_byod": set(),
                                  "_upg": set(), "_swap": set(), "_dev_tablet": set(),
                                  "_aal": {"T1"}, "_billpay_exec": {"B1", "B2"}},
    ("S1", "BOB", "2026-09-01"): {"store": "S1", "box_count": 1, "accessory_rev": 50.0,
                                  "_prem": {"T2"},            # <- THE SAME RECEIPT as Ana's
                                  "_port": set(), "_byod": set(), "_upg": set(), "_swap": set(),
                                  "_dev_tablet": set(), "_aal": set(),
                                  "_billpay_exec": {"B2"}},   # <- the same bill payment, too
}
R = P.roll_up(CELLS)
ck("D1 a receipt shared by two reps counts ONCE", R["S1"]["_prem"] == 2, R["S1"]["_prem"])
ck("D2 (summing the cell counts would have said 3 — the defect)",
   sum(len(c["_prem"]) for c in CELLS.values()) == 3)
ck("D3 a bill payment shared by two cells counts ONCE",
   R["S1"]["_billpay_exec"] == 2, R["S1"]["_billpay_exec"])
ck("D4 per-LINE tallies do sum (box_count is a line tally, not a set)",
   R["S1"]["box_count"] == 3, R["S1"]["box_count"])
ck("D5 money sums and is rounded once", R["S1"]["accessory_rev"] == 150.0, R["S1"]["accessory_rev"])
ck("D6 a list of cells works as well as a dict", P.roll_up(list(CELLS.values())) == R)
ck("D7 no cells → no stores", P.roll_up({}) == {} and P.roll_up(None) == {})
# the injected resolver: one store spelled two ways in the feed is ONE row here
_two = {1: {"store": "12 Main St", "box_count": 1, "_prem": {"A"}},
        2: {"store": "12 MAIN STREET", "box_count": 1, "_prem": {"B"}}}
_rr = P.roll_up(_two, store_of=lambda c: "B-12")
ck("D8 the store resolver folds two spellings into one row",
   list(_rr) == ["B-12"] and _rr["B-12"]["box_count"] == 2, _rr)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§E  the honesty rules")
def cell(store, *, bp=0, box=0, acc=0.0, aal=0, prem=0, port=0, byod=0, upg=0, swap=0, tab=0):
    def ids(p, n):
        return {f"{p}{i}" for i in range(n)}
    return {"store": store, "box_count": box, "accessory_rev": acc,
            "_prem": ids("p", prem), "_port": ids("o", port), "_byod": ids("y", byod),
            "_upg": ids("u", upg), "_swap": ids("w", swap), "_dev_tablet": ids("t", tab),
            "_aal": ids("a", aal), "_billpay_exec": ids("b", bp)}


# three stores in one band, one store alone in a band, one store with no bill payments at all
CS = [cell("A", bp=200, box=40, acc=4000.0, aal=10),
      cell("B", bp=210, box=20, acc=1000.0, aal=4),
      cell("C", bp=220, box=30, acc=3000.0, aal=6),
      cell("LONE", bp=500, box=90, acc=9000.0, aal=30),
      cell("QUIET", bp=0, box=5, acc=100.0, aal=1)]
OUT = P.build(CS, period="September 2026")
_by = {r["store"]: r for r in OUT["rows"]}
ck("E1 a store with no bill payments is NOT compared",
   [u["store"] for u in OUT["unbanded"]] == ["QUIET"], OUT["unbanded"])
ck("E2 … and it is told why, naming the thing to check",
   "Exec Metric Definitions" in OUT["unbanded"][0]["reason"])
ck("E3 … and it carries no band and no gap",
   "band" not in OUT["unbanded"][0] and "gaps" not in OUT["unbanded"][0])
_lone = _by["LONE"]
ck("E4 a band of ONE carries no gap (being alone is not under-performance)",
   _lone["gaps"] == {} and _lone["peers"] == 0, _lone["gaps"])
_lone_band = next(b for b in OUT["bands"] if b["band"] == _lone["band"])
ck("E5 … and the band says so rather than looking like a comparison",
   _lone_band["comparable"] is False and "no peer" in (_lone_band["note"] or ""))
ck("E6 a real band is comparable",
   next(b for b in OUT["bands"] if b["band"] == _by["A"]["band"])["comparable"] is True)
# "cannot answer" is never 0 — every one of these three.
ck("E7 no bill payments → the ratio is None, not 0",
   OUT["unbanded"][0]["boxes_per_billpay"] is None)
_nobox = P.build([cell("Z", bp=100, box=0, acc=500.0)], period="p")
ck("E8 no boxes → accessory-per-box is None, not 0",
   _nobox["rows"][0]["accessory_per_box"] is None)
ck("E9 no carrier KPI row → family plan is None, not 0%",
   _by["A"]["family_plan_pct"] is None)
_withkpi = P.build(CS, period="p", store_kpis={"A": {"family_plan_pct": 48.15}})
ck("E10 … and a carrier figure is passed through untouched",
   next(r for r in _withkpi["rows"] if r["store"] == "A")["family_plan_pct"] == 48.15)
_noaal = P.build(CS, period="p", aal_configured=False)
ck("E11 an org with no add-a-line word → the column is None, not 0",
   all(r["aal"] is None for r in _noaal["rows"]) and _noaal["aal_configured"] is False)
ck("E12 nothing to compare at all is said out loud",
   "nothing to compare" in (P.build([], period="p")["note"] or ""))
# the drill-down is NOT claimed to partition the total, and the payload says so
ck("E13 the payload states the parts are not a partition",
   "not expected to sum" in OUT["parts_are_not_a_partition"])
_p = P.build([cell("X", bp=100, box=5, prem=3, port=3, byod=3, upg=3)], period="p")["rows"][0]
ck("E14 … and indeed they do not (a receipt can name two types)",
   sum(_p["parts"].values()) > _p["boxes"], (_p["parts"], _p["boxes"]))
ck("E15 every part the owner named is present, in his order",
   [k for k, _l, _f in P.BOX_PARTS] == ["new", "port", "byod", "upgrade", "swap", "tablet"])
ck("E16 the peer basis is stated on the payload, in a person's words",
   "transaction grain" in OUT["band_basis"] and "Bill Payment" in OUT["band_basis"])


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§F  the gap")
A, B, C = _by["A"], _by["B"], _by["C"]
ck("F1 all three are in one band", A["band"] == B["band"] == C["band"])
ck("F2 the leader has no gap to the best", A["gaps"]["boxes"]["gap_to_best"] == 0)
ck("F3 … and is not reported as behind it", A["gaps"]["boxes"]["behind_best"] is False)
ck("F4 the laggard's gap to the best is the real difference",
   B["gaps"]["boxes"]["gap_to_best"] == 20, B["gaps"]["boxes"]["gap_to_best"])
# THE MEDIAN INCLUDES SELF — otherwise a band of two has no median at all, and a band of three
# would measure each store against a single other store and call it a median.
ck("F5 the median includes the store itself (20, 30, 40 → 30)",
   B["gaps"]["boxes"]["band_median"] == 30, B["gaps"]["boxes"]["band_median"])
ck("F6 the middle store is behind neither median nor best-by-median",
   C["gaps"]["boxes"]["behind_median"] is False)
ck("F7 the laggard is behind the median", B["gaps"]["boxes"]["behind_median"] is True)
ck("F8 … and behind the best", B["gaps"]["boxes"]["behind_best"] is True)
# direction is DECLARED, never assumed
ck("F9 every ranked metric declares its direction",
   all(isinstance(h, bool) for _k, _l, h in P.GAP_METRICS))
ck("F10 the metrics the owner named are all ranked",
   {"boxes", "aal", "accessory_revenue", "accessory_per_box", "family_plan_pct",
    "boxes_per_billpay"} <= {k for k, _l, _h in P.GAP_METRICS})
# a metric nobody in the band can answer produces no gap, rather than a gap against nothing
ck("F11 a metric no peer can answer yields no gap for it",
   "family_plan_pct" not in A["gaps"])


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§G  'lagging' has one definition")
LAG = P.lagging(OUT, metric="boxes")
ck("G1 only the store behind its band median is lagging",
   [l["store"] for l in LAG] == ["B"], [l["store"] for l in LAG])
ck("G2 the lone store is never lagging (it has no peer)",
   "LONE" not in [l["store"] for l in LAG])
ck("G3 an unbanded store is never lagging",
   "QUIET" not in [l["store"] for l in LAG])
ck("G4 the item names the band's leader so the prompt can cite them",
   LAG[0]["leader"] == "A", LAG[0]["leader"])
ck("G5 worst gap first",
   P.lagging(P.build([cell("A", bp=200, box=40), cell("B", bp=200, box=10),
                      cell("C", bp=200, box=30), cell("D", bp=200, box=20)], period="p"),
             metric="boxes")[0]["store"] == "B")
ck("G6 min_gap filters", P.lagging(OUT, metric="boxes", min_gap=100) == [])
ck("G7 the default metric is the footfall conversion, which is the owner's question",
   P.DEFAULT_GAP_METRIC == "boxes_per_billpay")
S = P.prompt_sentence(LAG[0])
ck("G8 the sentence carries the store's own number", "20" in S)
ck("G9 … and the median it is measured against", "30" in S)
ck("G10 … and names the peer who did it on the same traffic", "A is at 40" in S, S)
ck("G11 … and says why the comparison is fair", "sell-through, not footfall" in S)
ck("G12 … and names the band so it can be checked", "150–249 bill payments" in S, S)
ck("G13 an item with nothing to say produces no sentence", P.prompt_sentence({}) == "")
ck("G14 the prompt is not an accusation (no blame words)",
   not any(w in S.lower() for w in ("fail", "poor", "bad", "worst", "lazy")), S)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§H  the caveats — configuration a reader would mistake for performance")
CV = P.column_caveats(OUT, device_dimension=False, box_count_buckets=set(),
                      unresolved_stores={"12 Main St", "99 Side Rd"})
_by_col = {c["column"]: c for c in CV}
ck("H1 the tablet column says it CANNOT be answered, rather than reading 0",
   _by_col["tablet"]["severity"] == "cannot_answer")
ck("H2 … and says it is a setting, not a sales result",
   "setting, not a sales result" in _by_col["tablet"]["message"])
# A tablet sale carries a 'TABLET - XP' device-department line and `box_count` is counted from those
# lines, so a tablet has ALWAYS been a box (48 such lines live in September 2026). The device
# dimension only decides whether the sale can be NAMED a tablet, i.e. whether the SPLIT can be shown.
# Without this sentence a reader sees "Tablet: 0" beside a caveat about the device dimension and
# reasonably concludes tablets are missing from the box total too — the opposite of the owner's
# ruling of 2026-10-08 ("byod and tablets count towards the total boxes"). Pinned so the wording
# cannot drift back to implying a missing box.
ck("H2b … and says tablets are already IN the box total, so only the split is missing",
   "ARE already in the box total" in _by_col["tablet"]["message"]
   and "never a missing box" in _by_col["tablet"]["message"])
ck("H3 an empty box-count-buckets config is reported as UNDERSTATED boxes",
   _by_col["boxes"]["severity"] == "understated")
ck("H4 … naming the owner's own ruling that is not switched on",
   "2026-07-24" in _by_col["boxes"]["message"])
ck("H5 … and saying it affects every box surface, not just this screen",
   "not just this one" in _by_col["boxes"]["message"])
# A caveat that names a defect but not the control is a complaint. The control already ships: the
# tick in the Sales Report's Classification settings (mig 231, `box_count_buckets`), which the owner
# can press himself — no migration, no SQL, no engineer. Naming it is what makes the caveat a fix.
ck("H5b … and naming the control that applies it, not just the setting's name",
   "Classification settings" in _by_col["boxes"]["message"]
   and "toward total boxes sold" in _by_col["boxes"]["message"])
ck("H6 unmatched stores are named, and blank is explained as unmatched",
   _by_col["family_plan_pct"]["severity"] == "partial"
   and "never 0%" in _by_col["family_plan_pct"]["message"]
   and _by_col["family_plan_pct"]["stores"] == ["12 Main St", "99 Side Rd"])
ck("H7 a fully configured tenant gets NO caveats (this is not noise)",
   P.column_caveats(OUT, device_dimension=True, box_count_buckets={"byod"},
                    unresolved_stores=set()) == [])
ck("H8 an unknown device-dimension state invents no caveat",
   P.column_caveats(OUT, device_dimension=None, box_count_buckets=None,
                    unresolved_stores=None) == [])


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§I  THE UN-WIRE LOCK")
# A registry written but not wired is the trap this repo has fallen into three times (index §19.18).
# This module reads `_sales_cell_agg`'s cells BY FIELD NAME, so a renamed or deleted field would make
# every column silently read 0 — a report of zeros is far worse than a crash. These checks read the
# router's SOURCE (not the app: no DB, no fastapi) and fail the build if a field this module needs
# stops being initialised there.
import re  # noqa: E402

_src = open(__file__.rsplit("/", 1)[0] + "/app/modules/commcalc/router.py").read()
_agg = _src.split("def _sales_cell_agg", 1)[1].split("\ndef ", 1)[0]
for f in P.CELL_SETS:
    ck(f"I1 `_sales_cell_agg` still initialises {f}", f"'{f}'" in _agg, f)
for f in P.CELL_NUMBERS:
    ck(f"I2 `_sales_cell_agg` still initialises {f}", f"'{f}'" in _agg, f)
# the two facts this PR added to the one home must be POPULATED there, not merely declared
ck("I3 `_aal` is populated from THE one home, not a local substring",
   "_lc.is_add_a_line(r, line_rules)" in _agg)
ck("I4 `_billpay_exec` is populated under the DECLARED exec predicate",
   "_billpay_exec'].add(tid)" in _agg)
# and the module must not have grown its own line predicate behind our back
_mod = open(__file__.rsplit("/", 1)[0] + "/app/modules/commcalc/peer_comparison.py").read()
# I5 IS AN AST CHECK, AND THAT IS NOT A STYLE CHOICE — a textual one cannot work here.
# Prose must be allowed to DISCUSS a sale-line field: the caveat sentences name `box_departments` to
# explain the setting, and the module docstring lists what every column dereferences. Blanking the
# string literals to let the prose through ALSO blanks `r.get("contract_type")`, because a field read
# in Python IS a string literal — so the text check becomes vacuous and passes a real violation. That
# was this check's first draft, and arming it is what exposed it (the §19.28 trap, in the other
# direction). The AST sees a field READ (`.get(<name>)` or `[<name>]`) and never sees a comment, a
# docstring or a sentence, which is exactly the distinction this lock needs.
import ast as _ast  # noqa: E402

BANNED_FIELDS = ("contract_type", "product_desc", "department", "category", "ext_price",
                 "voided", "trans_type", "salesperson", "trans_date", "serial_1", "mdn")
_tree = _ast.parse(_mod)
_reads = set()
for _n in _ast.walk(_tree):
    # x.get("field") / x.get("field", default)
    if (isinstance(_n, _ast.Call) and isinstance(_n.func, _ast.Attribute)
            and _n.func.attr == "get" and _n.args
            and isinstance(_n.args[0], _ast.Constant) and isinstance(_n.args[0].value, str)):
        _reads.add(_n.args[0].value)
    # x["field"]
    if (isinstance(_n, _ast.Subscript) and isinstance(_n.slice, _ast.Constant)
            and isinstance(_n.slice.value, str)):
        _reads.add(_n.slice.value)
_violations = sorted(set(BANNED_FIELDS) & _reads)
ck("I5 peer_comparison reads NO raw sale-line field (AST) — it only rolls cells up",
   not _violations, _violations)
ck("I5b … and the check is non-vacuous: it does see the fields the module legitimately reads",
   {"box_count", "accessory_rev", "store"} <= _reads, sorted(_reads))
# Nor may it import a classifier: doing so is how a second derivation starts.
_imports = {a.name for _n in _ast.walk(_tree) if isinstance(_n, _ast.ImportFrom)
            for a in _n.names} | {a.name for _n in _ast.walk(_tree)
                                  if isinstance(_n, _ast.Import) for a in _n.names}
_bad_imp = sorted(i for i in _imports
                  if any(k in i for k in ("line_class", "exec_metric_defs", "installment_category",
                                          "sales_comparison", "router")))
ck("I6 … and imports no classifier or router of its own", not _bad_imp, _bad_imp)
# the asset-router sibling must stay dereferenced
_asset = open(__file__.rsplit("/", 1)[0] + "/app/modules/asset/router.py").read()
_apromo = _asset.split("def _promo_type", 1)[1].split("\ndef ", 1)[0]
ck("I7 `asset/router._promo_type` asks the one home for the AAL question",
   "_lc.is_add_a_line" in _apromo)
ck("I8 … and no longer spells it itself",
   '"add a line" in ct' not in _apromo and 'ct == "aal"' not in _apromo)
# AND THE RULES MUST BE THREADED. Dereferencing the home while handing it no rules is a NOMINAL fix:
# the home answers with the HOUSE words and a tenant whose POS carries the fact in its category path
# is no better served than by the substring this replaced. That is the §19.18 trap (a registry written
# but not wired), so the caller must resolve the org's rules and pass them.
_arecon = _asset.split("def _compute_hotsheet_recon", 1)[1].split("\ndef ", 1)[0]
ck("I9 the asset recon resolves the ORG's activation-type rules",
   "_line_rules_of(_accessory_config(client, org_id))" in _arecon)
ck("I10 … and threads them into every _promo_type call (no bare call left)",
   "_promo_type(ct, _line_rules)" in _arecon and "_promo_type(ct)" not in _arecon)


print("\n" + "=" * 70)
if FAIL:
    print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
    sys.exit(1)
print("Peer Sales Comparison — all checks passed")
