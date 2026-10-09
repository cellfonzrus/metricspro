#!/usr/bin/env python3
"""PROOF — Accessory target allocation (owner ask 2026-10-09, index §61).

DB-FREE, stdlib only, network-free. Drives the REAL pure functions with fixtures, so what is proved
here is what runs in production.

What this file is for, section by section:
  §A  the company goal — fixed, % up, % down, and every way it REFUSES to invent a number
  §B  the basis amounts — measured over the whole window, so narrowing the dropdown cannot move the
      company's own goal
  §C  the capacity ladder — own history, the company rate for zero attachment, the company rate for
      no history, and `no_basis` that is never a silent 0
  §D  the split — proportional to capacity, and it FOOTS to the goal to the dollar
  §E  the selection — an unselected store keeps its target, that target is RESERVED out of the goal,
      and a goal already committed is reported rather than forced
  §F  the report columns — the owner's sentence, in his order, each one derived and none invented
  §G  the write payload — a store with no suggestion is omitted, never written as 0
  §H  THE REGRESSION — the worked example, pinned, including the two-store case that exposed the
      rounding residue
  §I  THE UN-WIRE LOCK — this module may not read a sale line, may not re-derive a projection, may
      not name a carrier/tenant/product, and may not open a second home for a store's target; and the
      endpoint must DEREFERENCE the homes rather than re-deriving them
"""
import re
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from app.modules.commcalc import accessory_target_plan as A      # noqa: E402

_here = __file__.rsplit("/", 1)[0]
FAIL = []


def ck(name, cond, got=None):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}" + (f"  got={got!r}" if got is not None else ""))
        FAIL.append(name)


# ── FIXTURES ──────────────────────────────────────────────────────────────────────────────────────
# Four stores, each one standing for a rung of the capacity ladder. Nothing here names a carrier, a
# tenant or a product (RULE TWO); a store is a code and a pair of measured months.
#
#   S1  strong attachment, traffic flat        — own_history
#   S2  same traffic, HALF the attachment      — own_history (the finding the report exists for)
#   S3  sells boxes, attached NOTHING          — company_rate_zero_attach
#   S4  brand new, no history at all           — company_rate_no_history
def store(code, m2a=0.0, m2b=0, m1a=0.0, m1b=0, mtd=0.0, pacc=0.0, pbox=0, cur=0.0, **kw):
    d = {"store_code": code, "address": f"{code} address", "market": "M1",
         "m2_acc": m2a, "m2_boxes": m2b, "m1_acc": m1a, "m1_boxes": m1b,
         "mtd_acc": mtd, "projected_acc": pacc, "projected_boxes": pbox, "current_target": cur}
    d.update(kw)
    return d


def window():
    return [
        store("S1", 10000.0, 500, 12000.0, 500, mtd=6000.0, pacc=12500.0, pbox=520, cur=12000.0),
        store("S2",  5000.0, 500,  5500.0, 500, mtd=2600.0, pacc=5400.0,  pbox=500, cur=6000.0),
        store("S3",     0.0, 200,     0.0, 200, mtd=0.0,    pacc=0.0,     pbox=200, cur=1000.0),
        store("S4"),
    ]


# ══ §A  THE COMPANY GOAL ═════════════════════════════════════════════════════════════════════════
print("\n§A  the company goal — and every way it refuses to invent a number")
amt = A.basis_amounts(window())
g = A.company_goal("fixed", 40000, "last_month_actual", amt)
ck("A1 a fixed total is the number typed", g["goal"] == 40000.0 and g["mode"] == "fixed", g)
ck("A2 … and it still reports the basis it is being compared against",
   g["basis"] == "last_month_actual" and g["basis_amount"] == 17500.0, g)

g = A.company_goal("pct_increase", 10, "last_month_actual", amt)
ck("A3 +10% on last month's actual is computed off the MEASURED basis",
   g["goal"] == 19250.0, g)                      # 17,500 x 1.10
g = A.company_goal("pct_decrease", 20, "last_month_actual", amt)
ck("A4 -20% is a decrease, not a second increase", g["goal"] == 14000.0, g)
g = A.company_goal("pct_increase", -10, "last_month_actual", amt)
ck("A5 a sign typed into a % mode cannot invert the mode",
   g["goal"] == 19250.0 and g["value"] == 10.0, g)

# THE HONESTY RULES. Each of these used to be the obvious place for a silent 0.
g = A.company_goal("pct_increase", None, "last_month_actual", amt)
ck("A6 NOTHING ENTERED is not a goal of zero", g["goal"] is None and g["reason"] == "no_goal_entered", g)
g = A.company_goal("pct_increase", 10, "last_month_actual", {"last_month_actual": 0.0})
ck("A7 a % of nothing is `no_basis`, never 0", g["goal"] is None and g["reason"] == "no_basis", g)
g = A.company_goal("fixed", 0, "last_month_actual", amt)
ck("A8 … but a DELIBERATE zero IS a goal (0 is not blank)", g["goal"] == 0.0 and g["reason"] is None, g)
g = A.company_goal("pct_decrease", 150, "last_month_actual", amt)
ck("A9 a decrease over 100% plans no negative sales, and says it was clamped",
   g["goal"] == 0.0 and g["reason"] == "decrease_over_100_clamped_to_zero", g)
g = A.company_goal("fixed", -5000, "last_month_actual", amt)
ck("A10 a negative fixed total is clamped and SAID", g["goal"] == 0.0
   and g["reason"] == "negative_clamped_to_zero", g)
g = A.company_goal("make_it_up", 10, "last_month_actual", amt)
ck("A11 a mode nobody declared is refused, not guessed",
   g["goal"] is None and g["reason"] == "unknown_mode", g)
g = A.company_goal("pct_increase", 10, "a_basis_that_does_not_exist", amt)
ck("A12 an unknown basis falls back to the DECLARED default and names it",
   g["basis"] == A.DEFAULT_GOAL_BASIS and g["goal"] == 19250.0, g)
ck("A13 every declared mode and basis carries a label for the picker (pick, don't type)",
   all(k in A.GOAL_MODE_LABELS for k in A.GOAL_MODES)
   and all(k in A.GOAL_BASIS_LABELS for k in A.GOAL_BASES))


# ══ §B  THE BASIS AMOUNTS ════════════════════════════════════════════════════════════════════════
print("\n§B  the basis amounts — the company's goal does not move when the dropdown narrows")
ck("B1 last month's actual is the sum of the m1 column", amt["last_month_actual"] == 17500.0, amt)
ck("B2 the two-month average is the average, not the sum",
   amt["two_month_average"] == 16250.0, amt)     # (17,500 + 15,000) / 2
ck("B3 the projection basis sums the projections", amt["projected_this_month"] == 17900.0, amt)
ck("B4 the current-targets basis sums the targets in force", amt["current_targets"] == 19000.0, amt)
ck("B5 the basis is measured over EVERY store in the window — a selection cannot move it",
   A.basis_amounts(window()) == A.plan(window(), "fixed", 1, selected=["S1"])["goal_basis_amounts"])
ck("B6 an empty window yields zeros, and the % modes then refuse rather than divide",
   A.basis_amounts([])["last_month_actual"] == 0.0
   and A.company_goal("pct_increase", 10, "last_month_actual", A.basis_amounts([]))["goal"] is None)


# ══ §C  THE CAPACITY LADDER ══════════════════════════════════════════════════════════════════════
print("\n§C  capacity — accessories per box x the boxes being sold, every rung named")
rate = A.company_rate(window())
ck("C1 the company rate is the tenant's own, measured over the two history months",
   rate == round(32500.0 / 2400, 4), rate)       # 32,500 acc$ over 2,400 boxes
rows = {r["store_code"]: r for r in (A.capacity_row(s, house_rate=rate) for s in window())}

r1 = rows["S1"]
ck("C2 a store with history is weighted on ITS OWN rate",
   r1["acc_per_box"] == 22.0 and r1["acc_per_box_basis"] == "own_history", r1)
ck("C3 … against the PROJECTED boxes, not the historical ones",
   r1["expected_boxes"] == 520 and r1["expected_boxes_basis"] == "projection", r1)
ck("C4 capacity is the product", r1["capacity"] == 11440.0, r1)
ck("C5 the weaker attacher's rate is genuinely lower, which is the whole finding",
   rows["S2"]["acc_per_box"] == 10.5, rows["S2"])

r3 = rows["S3"]
ck("C6 ZERO ATTACHMENT IS NOT A WEIGHT OF ZERO — the company rate carries it",
   r3["acc_per_box"] == rate and r3["acc_per_box_basis"] == "company_rate_zero_attach", r3)
ck("C7 … so the store that most needs a target gets one instead of a $0 that looks deliberate",
   r3["capacity"] and r3["capacity"] > 0, r3)

r4 = rows["S4"]
ck("C8 no history is not no capacity — the company rate, and it says so",
   r4["acc_per_box"] == rate and r4["acc_per_box_basis"] == "company_rate_no_history", r4)
ck("C9 … but with no projected boxes either there is nothing to multiply, and the row admits it",
   r4["capacity"] is None and r4["expected_boxes_basis"] == "none", r4)
r4b = A.capacity_row(store("S4", 0, 0, 0, 0, pbox=300), house_rate=rate)
ck("C10 a new store WITH a projection is weighted at the company rate on that projection",
   r4b["capacity"] == round(rate * 300, 2), r4b)
r_hist = A.capacity_row(store("S5", 1000.0, 100, 1200.0, 100), house_rate=rate)
ck("C11 with no projection the history AVERAGE stands in, and the basis names it",
   r_hist["expected_boxes"] == 100.0 and r_hist["expected_boxes_basis"] == "history_average", r_hist)

empty = A.capacity_row(store("S9"), house_rate=None)
ck("C12 an empty window has no company rate either → `no_basis`, never a silent 0",
   empty["acc_per_box"] is None and empty["acc_per_box_basis"] == "no_basis"
   and empty["capacity"] is None, empty)
ck("C13 the company rate is None when nothing was sold anywhere, rather than 0",
   A.company_rate([store("Z")]) is None)
ck("C14 every basis a row can report is a DECLARED one (no string invented at the call site)",
   all(r["acc_per_box_basis"] in A.RATE_BASES and r["expected_boxes_basis"] in A.BOX_BASES
       for r in rows.values()))


# ══ §D  THE SPLIT ════════════════════════════════════════════════════════════════════════════════
print("\n§D  the split — proportional to capacity, and it foots to the dollar")
rws = [A.capacity_row(s, house_rate=rate) for s in window()]
al = A.allocate(rws, 40000.0)
ck("D1 the assignments SUM to the goal exactly — no rounding residue left on the floor",
   sum(al["assignments"].values()) == 40000.0, al)
ck("D2 the bigger capacity carries the bigger share",
   al["assignments"]["S1"] > al["assignments"]["S2"] > al["assignments"]["S3"], al["assignments"])
ck("D3 shares are proportional to capacity, not to last month's dollars",
   round(al["assignments"]["S1"] / al["assignments"]["S2"], 3)
   == round(rws[0]["capacity"] / rws[1]["capacity"], 3), al["assignments"])
ck("D4 a store with no measurable capacity is NAMED rather than silently given nothing",
   al["unweighted"] == ["S4"] and al["weight_basis"] == "capacity", al)
ck("D5 … and it is left OUT of the assignments, because a 0 there would be written to its target",
   "S4" not in al["assignments"], al["assignments"])

ck("D6 no goal → no assignments and the reason said",
   A.allocate(rws, None)["reason"] == "no_goal")
ck("D7 a window with nothing measurable anywhere splits EQUALLY, and the basis admits it",
   A.allocate([A.capacity_row(store("Z1"), None), A.capacity_row(store("Z2"), None)],
              1000.0)["weight_basis"] == "equal_no_measurable_capacity")
eq = A.allocate([A.capacity_row(store("Z1"), None), A.capacity_row(store("Z2"), None)], 1001.0)
ck("D8 … and the equal split still foots to the odd dollar",
   sum(eq["assignments"].values()) == 1001.0, eq)

# THE ROUNDING RULE, on its own. Independent rounding of 3 x 1000/3 gives 999.
odd = A._largest_remainder([333.333, 333.333, 333.334], 1000.0)
ck("D9 largest-remainder rounding foots where independent rounding loses a dollar",
   sum(odd) == 1000.0 and max(odd) == 334.0, odd)
ck("D10 the residue lands on the largest fraction, so the adjustment is proportionally smallest",
   A._largest_remainder([10.9, 10.1], 21.0) == [11.0, 10.0],
   A._largest_remainder([10.9, 10.1], 21.0))
ck("D11 an exact split needs no adjustment", A._largest_remainder([5.0, 5.0], 10.0) == [5.0, 5.0])
ck("D12 an empty share list is not a crash", A._largest_remainder([], 10.0) == [])


# ══ §E  THE SELECTION ════════════════════════════════════════════════════════════════════════════
print("\n§E  the selection — an unselected store keeps its target, and it is RESERVED")
sel = A.allocate(rws, 40000.0, selected=["S1", "S2"])
ck("E1 only the selected stores are assigned",
   sorted(sel["assignments"]) == ["S1", "S2"], sel["assignments"])
ck("E2 the UNSELECTED stores' current targets are reserved out of the goal",
   sel["reserved"] == 1000.0, sel)               # S3 $1,000 + S4 $0
ck("E3 … so only the remainder is split, and that is what foots",
   sel["to_split"] == 39000.0 and sum(sel["assignments"].values()) == 39000.0, sel)
ck("E4 assigning to a subset makes no silent promise about the rest",
   sel["reserved"] + sum(sel["assignments"].values()) == 40000.0, sel)
ck("E5 a selection is case- and whitespace-insensitive, so a dropdown value cannot miss",
   sorted(A.allocate(rws, 40000.0, selected=[" s1 ", "S2"])["assignments"]) == ["S1", "S2"])
_none = A.allocate(rws, 40000.0, selected=[])
ck("E6 an empty selection means EVERY store, not no store",
   sorted(_none["assignments"]) == ["S1", "S2", "S3"] and _none["unweighted"] == ["S4"],
   _none)

# A GOAL ALREADY COMMITTED. The unselected stores' existing targets exceed the whole company goal.
committed = A.allocate(rws, 5000.0, selected=["S3"])
ck("E7 when the unselected targets already exceed the goal, nothing is suggested",
   committed["assignments"] == {} and committed["reason"] == "goal_already_committed", committed)
ck("E8 … and the overage is NAMED rather than clamped to zero and made to look deliberate",
   committed["shortfall"] == 13000.0, committed)  # S1 12,000 + S2 6,000 - 5,000
ck("E9 a selection naming no store in the window is refused, not treated as 'all'",
   A.allocate(rws, 40000.0, selected=["NOT-A-STORE"])["reason"] == "no_stores_selected")


# ══ §F  THE REPORT COLUMNS ═══════════════════════════════════════════════════════════════════════
print("\n§F  the report — the owner's columns, in his order, every one derived")
p = A.plan(window(), "pct_increase", 10, "last_month_actual")
ck("F1 the goal rides on the payload with its mode, basis and measured basis amount",
   p["goal"]["goal"] == 19250.0 and p["goal"]["basis_amount"] == 17500.0, p["goal"])
by = {r["store_code"]: r for r in p["rows"]}
ck("F2 the last two months are in their OWN columns, not summed",
   (by["S1"]["m2_acc"], by["S1"]["m1_acc"]) == (10000.0, 12000.0), by["S1"])
ck("F3 month-to-date and the projection are both present", by["S1"]["mtd_acc"] == 6000.0
   and by["S1"]["projected_acc"] == 12500.0, by["S1"])
ck("F4 the current target is the one in force", by["S1"]["current_target"] == 12000.0, by["S1"])
ck("F5 the EXTENDED target is suggested minus current — the owner's own column",
   by["S1"]["extension"] == round(by["S1"]["suggested_target"] - 12000.0, 2), by["S1"])
ck("F6 an extension is NEGATIVE when the goal asks a store for less, never floored at zero",
   any(r["extension"] is not None and r["extension"] < 0 for r in p["rows"]),
   {r["store_code"]: r["extension"] for r in p["rows"]})
ck("F7 the implied attachment rate says whether the ask is 'more boxes' or 'more per box'",
   by["S1"]["required_acc_per_box"]
   == round(by["S1"]["suggested_target"] / 520, 4), by["S1"])
ck("F8 the gap against the projection says what changing nothing would leave on the table",
   by["S1"]["gap_vs_projection"] == round(by["S1"]["suggested_target"] - 12500.0, 2), by["S1"])
ck("F9 each store's share of the split is reported as a %",
   abs(sum(r["share_pct"] for r in p["rows"] if r["share_pct"]) - 100.0) < 0.05,
   [r["share_pct"] for r in p["rows"]])
ck("F10 the rows are ordered by capacity, so the stores carrying the goal lead the page",
   [r["store_code"] for r in p["rows"]][:2] == ["S1", "S2"],
   [r["store_code"] for r in p["rows"]])
ck("F11 the totals foot: assigned + reserved is the company goal",
   p["totals"]["assigned_plus_reserved"] == 19250.0, p["totals"])
ck("F12 every store is marked selected when nothing was selected",
   all(r["selected"] for r in p["rows"]))

# NOTHING ENTERED. The whole report still renders; not one suggestion is invented.
blank = A.plan(window(), "pct_increase", None, "last_month_actual")
ck("F13 with no goal entered the measured columns still render",
   {r["store_code"] for r in blank["rows"]} == {"S1", "S2", "S3", "S4"})
ck("F14 … and EVERY suggestion is null with the reason said — never a planned $0",
   all(r["suggested_target"] is None and r["extension"] is None for r in blank["rows"])
   and blank["goal"]["reason"] == "no_goal_entered")
ck("F15 a null suggestion carries no implied rate and no gap either",
   all(r["required_acc_per_box"] is None and r["gap_vs_projection"] is None
       for r in blank["rows"]))
sub = A.plan(window(), "fixed", 40000, selected=["S1"])
ck("F16 an unselected store is marked as such and keeps its own target untouched",
   by_sub := {r["store_code"]: r for r in sub["rows"]},
   None) or ck("F16 an unselected store is marked unselected and gets no suggestion",
               by_sub["S2"]["selected"] is False and by_sub["S2"]["suggested_target"] is None,
               by_sub["S2"])
ck("F17 the company's own accessories-per-box rate rides on the payload, so the fallback is auditable",
   p["company_acc_per_box"] == rate, p["company_acc_per_box"])
ck("F18 an empty window is a report, not a crash",
   A.plan([], "fixed", 1000)["totals"]["stores"] == 0)


# ══ §G  THE WRITE PAYLOAD ════════════════════════════════════════════════════════════════════════
print("\n§G  the write payload — omission, never a zero")
pay = A.assignment_payload(p)
ck("G1 every store with a suggestion is in the payload, keyed by its code",
   {x["store_code"] for x in pay} == {"S1", "S2", "S3"}, pay)
ck("G2 A STORE WITH NO SUGGESTION IS OMITTED — a planning page cannot wipe a target it could not compute",
   "S4" not in {x["store_code"] for x in pay}, pay)
ck("G3 the payload writes the accessory column and nothing else",
   all(set(x) == {"store_code", "accessories_monthly"} for x in pay), pay)
ck("G4 a confirm list narrows the write, so the browser cannot widen it",
   [x["store_code"] for x in A.assignment_payload(p, store_codes=["S2"])] == ["S2"])
ck("G5 a confirm list naming a store with no suggestion still writes nothing for it",
   A.assignment_payload(p, store_codes=["S4"]) == [])
ck("G6 no goal entered → an EMPTY payload, not a batch of zeros",
   A.assignment_payload(blank) == [])
ck("G7 a payload off nothing at all is empty rather than an exception",
   A.assignment_payload(None) == [])


# ══ §H  THE REGRESSION — the worked example, pinned ══════════════════════════════════════════════
print("\n§H  the worked example, pinned as the oracle")
# Two stores, same traffic, one attaching twice as well. Company goal +20% on last month's $18,000.
# Capacity: S1 20.0/box x 500 = 10,000 ; S2 10.0/box x 500 = 5,000. Goal 21,600 over 15,000 of
# capacity → S1 14,400 ; S2 7,200. The arithmetic a manager must be able to redo on paper.
two = [store("S1", 9000.0, 450, 12000.0, 600, pbox=500, cur=11000.0),
       store("S2", 4500.0, 450, 6000.0, 600, pbox=500, cur=7000.0)]
h = A.plan(two, "pct_increase", 20, "last_month_actual")
hb = {r["store_code"]: r for r in h["rows"]}
ck("H1 the goal is +20% on the measured last month", h["goal"]["goal"] == 21600.0, h["goal"])
ck("H2 the better attacher's rate is exactly twice the weaker one's",
   hb["S1"]["acc_per_box"] == 20.0 and hb["S2"]["acc_per_box"] == 10.0, hb)
ck("H3 the suggested targets are the proportional split, to the dollar",
   (hb["S1"]["suggested_target"], hb["S2"]["suggested_target"]) == (14400.0, 7200.0), hb)
ck("H4 the extension is what each store is being asked for ON TOP of its current target",
   (hb["S1"]["extension"], hb["S2"]["extension"]) == (3400.0, 200.0), hb)
ck("H5 the column foots to the company goal", h["totals"]["assigned"] == 21600.0, h["totals"])
ck("H6 the implied attachment rate is the ask, stated per box",
   (hb["S1"]["required_acc_per_box"], hb["S2"]["required_acc_per_box"]) == (28.8, 14.4), hb)
# THE RESIDUE CASE that made the rounding rule necessary: a goal that does not divide.
odd3 = A.plan([store("A", 100.0, 10, 100.0, 10, pbox=10),
               store("B", 100.0, 10, 100.0, 10, pbox=10),
               store("C", 100.0, 10, 100.0, 10, pbox=10)], "fixed", 1000)
ck("H7 three equal stores on a goal of 1,000 still foot to 1,000, not 999",
   odd3["totals"]["assigned"] == 1000.0,
   {r["store_code"]: r["suggested_target"] for r in odd3["rows"]})
ck("H8 … and no store is given a fractional dollar to chase",
   all(float(r["suggested_target"]).is_integer() for r in odd3["rows"]))


# ══ §I  THE UN-WIRE LOCK ═════════════════════════════════════════════════════════════════════════
print("\n§I  the un-wire lock — the homes stay dereferenced, and no second home opens")
SRC = open(f"{_here}/app/modules/commcalc/accessory_target_plan.py").read()
# The CODE, with the module docstring removed. The docstring is where the duplicate check NAMES the
# homes this module dereferences (`_exec_mtd`, `_sales_cell_agg`, the field names the pay engine
# reads), so a scan for "does this file re-derive X" must read the code and not the prose that
# deliberately cites X. Scanning the whole file would make documenting a dependency look like having
# one — and would punish the index-first rule the house requires.
BODY = SRC.split('"""', 2)[2] if SRC.count('"""') >= 2 else SRC
ck("I1 the module reads no table and opens no client — every input is handed in",
   not re.search(r"\.schema\(|\.table\(|def sb\(|import supabase|requests\.", SRC))
ck("I2 it re-derives no projection of its own — the trending column is INJECTED",
   not re.search(r"days_in_month|days_elapsed|_exec_mtd\(|date\.today\(|datetime", BODY))
ck("I3 it classifies no sale line — boxes and accessory $ arrive already aggregated",
   not re.search(r"contract_type|department|trans_id|ext_price|salesperson", BODY))
ck("I4 RULE TWO: no carrier, tenant or product name anywhere in the engine",
   not re.search(r"(?i)\b(boost|total\s*wireless|luxelink|cellfonz|vzone|ondigo|byod)\b", BODY))
ck("I5 no second home for a store's target — the engine names mig 006's column and nothing else",
   SRC.count("accessories_monthly") >= 1
   and not re.search(r"CREATE TABLE|accessory_target_row|acc_target_table", SRC))
ck("I5b the engine's own code touches no schema at all — the write is the caller's",
   "accessories_monthly" in BODY and "upsert" not in BODY)
ck("I6 the honesty rule is in the code and not only in the comment: blank is not zero",
   "def _blank" in SRC and 'str(v).strip() == ""' in SRC)
ck("I7 the goal vocabulary is declared once, as data the UI picks from",
   SRC.count("GOAL_MODES = ") == 1 and SRC.count("GOAL_BASES = ") == 1)

# THE ENDPOINT — it must DEREFERENCE the homes rather than re-deriving them, and it must write
# through the SAME gate the single-store save uses.
RT = open(f"{_here}/app/modules/commcalc/router.py").read()
seg = RT[RT.index("def _acc_plan_month_by_code"):RT.index('@router.get("/targets/{period}/report-cards")')]
ck("I8 the endpoint reads the month's accessory $ and boxes through §5's processed-sales home",
   "_fetch_actuals(" in seg and "acc_gp" in seg and "box_count" in seg)
ck("I9 … and the projection through Executive MTD's own home, not a formula of its own",
   "_targets_trending_by_code(" in seg and "trending_acc_plus_setup" in seg)
ck("I10 … and the target in force off mig 006's row",
   "table('targets')" in seg and "accessories_monthly" in seg)
ck("I11 … and the store resolution / roster through the shared roster, not a new matcher",
   "_storeops_roster(" in seg and not re.search(r"def _acc_plan_store_match|\.upper\(\)\s*==\s*str\(", seg))
ck("I12 the arithmetic is the pure module's — the endpoint computes no share of its own",
   "_accplan.plan(" in seg and "_accplan.assignment_payload(" in seg
   and "/ total_cap" not in seg and "largest_remainder" not in seg)
ck("I13 THE WRITE IS GATED by the same permission + span check the single-store save uses",
   seg.count("_require_target_edit(") >= 2)
ck("I14 the write recomputes server-side rather than trusting a client-supplied dollar figure",
   "get_accessory_target_plan(" in seg
   and not re.search(r"body\.(suggested|amount|targets)\b", seg))
ck("I15 the write moves ONLY the accessory column — the other categories are carried, not zeroed",
   "_carry_forward_map(" in seg and "prev.get('activations_monthly')" in seg)
ck("I16 the report endpoint is read-only — it upserts nothing",
   ".upsert(" not in seg[:seg.index("class AssignAccessoryTargetsIn")])
ck("I17 the module graph names this engine, so the next author is told what else answers this",
   "accessory_target_plan.py" in open(f"{_here}/app/modules/core/module_graph.py").read())
ck("I18 the index documents it (§61), so it cannot be re-derived by somebody who looked it up",
   "## 61." in open(f"{_here}/../docs/SYSTEM_DATA_FLOW_INDEX.md").read())


print("\n" + "=" * 70)
if FAIL:
    print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
    sys.exit(1)
print("Accessory target allocation — all checks passed")
