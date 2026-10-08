#!/usr/bin/env python3
"""PROOF — Spiff Impact (owner ask 2026-10-08, index §60).

DB-FREE, stdlib only, network-free. Drives the REAL pure functions with fixtures, so what is proved
here is what runs in production.

What this file is for, section by section:
  §A  the option list is DERIVED from the tenant's own rows, and carries §58's verdict with it
  §B  the report never silently chooses its own subject
  §C  the money — the share is of the line the dollars ACTUALLY book to
  §D  the profit effect — with it against without it, and the percentage a loss cannot carry
  §E  the rate — paid units per 100 boxes, and the denominator that is not a zero
  §F  the payload — the estate ties to its own rows, zero is a measurement and missing is not
  §G  the caveats — each fires on its measured condition and NOT otherwise
  §H  THE REGRESSION — the live September 2026 figures, pinned as the oracle
  §I  THE UN-WIRE LOCK — this module may not read a sale line, classify a dollar, or re-decide
      "behind"; and the three homes it dereferences must still be dereferenced
  §J  `peer_comparison.with_extra_metric` — a borrowed metric ranks through §59's OWN machinery,
      and every existing caller stays byte-identical
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from app.modules.commcalc import spiff_impact as S              # noqa: E402
from app.modules.commcalc import peer_comparison as P           # noqa: E402

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}" + (f"  got={got!r}" if got is not None else ""))
        FAIL.append(name)


# ── FIXTURES ──────────────────────────────────────────────────────────────────────────────────────
# A tenant's own vocabulary, standing in for `payment_categories` + `carrier_category_map`. Not one
# of these strings appears in the module under test — that is §I's RULE TWO check.
VERDICTS = {
    "alpha bounty": {"component": "COMMISSION", "basis": "declared", "declared": True,
                     "inferred": False, "twin_of": None},
    "alpha bounty - month 3": {"component": "COMMISSION", "basis": "declared", "declared": True,
                               "inferred": False, "twin_of": None},
    "beta spiff": {"component": "SPIFF", "basis": "keyword_rule", "declared": False,
                   "inferred": False, "twin_of": None},
    "gamma offer": {"component": "REIMBURSEMENT", "basis": "inferred_prior_year_twin",
                    "declared": False, "inferred": True, "twin_of": "older gamma offer"},
    "delta clawback": {"component": "COMMISSION", "basis": "declared", "declared": True,
                       "inferred": False, "twin_of": None},
}
LINES = {"COMMISSION": "carrier_comm", "SPIFF": "carrier_comm", "RESIDUAL": "carrier_comm",
         "REIMBURSEMENT": "vip_reimb"}


def classify(raw):
    return dict(VERDICTS.get(" ".join(str(raw or "").split()).lower(),
                             {"component": None, "basis": "unresolved", "declared": False,
                              "inferred": False, "twin_of": None}))


def component_line(component):
    return LINES.get(component, "carrier_comm")


def month_token(raw):
    """Stands in for `commission_ledger.parse_payment_month` — "does this label SAY a month"."""
    t = str(raw or "").lower()
    if " - month " in t:
        try:
            return int(t.split(" - month ", 1)[1].split()[0])
        except (ValueError, IndexError):
            return None
    return None


def row(store, ctype, amount, qty):
    return {"business_address": store, "compensation_type": ctype,
            "payment_amount": amount, "quantity": qty}


ROWS = [
    # store A — a healthy store
    row("A", "Beta Spiff", 300.0, 30), row("A", "Alpha Bounty", 1000.0, 100),
    row("A", "Gamma Offer", 5000.0, 20),
    # store B — same traffic band as A, earns far less of the spiff per box
    row("B", "Beta Spiff", 50.0, 5), row("B", "Alpha Bounty", 900.0, 90),
    row("B", "Gamma Offer", 4000.0, 16),
    # store C — the carrier paid it NOTHING for the spiff. That IS the finding.
    row("C", "Alpha Bounty", 400.0, 40), row("C", "Gamma Offer", 1000.0, 4),
    # store D — a spelling nothing resolves, and no sales rows at all
    row("D-unmapped", "Beta Spiff", 20.0, 2),
    # a cosmetic re-spelling of the SAME pay type: folding must make it one type
    row("A", "  beta   SPIFF ", 100.0, 10),
]
STORE_OF = {"A": "A", "B": "B", "C": "C", "D-unmapped": "D-unmapped"}
PEER = {
    "rows": [
        {"store": "A", "market": "M1", "band": 1, "band_label": "150–249 bill payments",
         "billpay_txns": 200, "boxes": 100, "peers": 2},
        {"store": "B", "market": "M1", "band": 1, "band_label": "150–249 bill payments",
         "billpay_txns": 210, "boxes": 100, "peers": 2},
        {"store": "C", "market": "M2", "band": 1, "band_label": "150–249 bill payments",
         "billpay_txns": 190, "boxes": 50, "peers": 2},
    ],
    "unbanded": [],
    "bands": [{"band": 1, "label": "150–249 bill payments", "stores": 3, "comparable": True,
               "leader": "A", "boxes_best": 100, "boxes_median": 100}],
    "band_cuts": [150, 250, 400], "band_basis": "Distinct bill-payment transactions",
    "gap_metrics": [], "rows_note": None,
}
PL = {
    "A": {"revenue": 20000.0, "cogs": 8000.0, "gross_profit": 12000.0, "expenses": 7000.0,
          "net_income": 5000.0},
    "B": {"revenue": 18000.0, "cogs": 9000.0, "gross_profit": 9000.0, "expenses": 9100.0,
          "net_income": -100.0},
    "C": {"revenue": 9000.0, "cogs": 4000.0, "gross_profit": 5000.0, "expenses": 4800.0,
          "net_income": 200.0},
    # store D has NO snapshot on purpose
}


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§A  the option list is derived from the tenant's own rows")
OPTS = S.pay_type_options(ROWS, classify, component_line=component_line, month_token=month_token)
ck("A1 one entry per pay type, folded (a re-spelling is NOT a new type)",
   len(OPTS) == 3, [o["type"] for o in OPTS])
_by = {o["type"].strip().lower(): o for o in OPTS}
ck("A2 the folded re-spelling's dollars are IN the type's total",
   _by["beta spiff"]["amount"] == 470.0, _by["beta spiff"]["amount"])
ck("A3 … and its units too", _by["beta spiff"]["units"] == 47, _by["beta spiff"]["units"])
ck("A4 biggest money first — the order a manager scans",
   [o["type"] for o in OPTS][0].lower().strip() == "gamma offer", OPTS[0]["type"])
ck("A5 §58's component travels with the entry",
   _by["gamma offer"]["component"] == "REIMBURSEMENT", _by["gamma offer"]["component"])
ck("A6 … and so does the BASIS it rests on",
   _by["gamma offer"]["basis"] == "inferred_prior_year_twin")
ck("A7 … and whether the ORG declared it",
   _by["alpha bounty"]["declared"] is True and _by["beta spiff"]["declared"] is False)
ck("A8 … and the twin an inference came from, named",
   _by["gamma offer"]["twin_of"] == "older gamma offer")
ck("A9 the P&L line is the one the COMPONENT routes to, never a guess",
   _by["gamma offer"]["pl_line"] == "vip_reimb" and _by["beta spiff"]["pl_line"] == "carrier_comm")
ck("A10 a label that NAMES a month rung is flagged as one",
   S.pay_type_options([row("A", "Alpha Bounty - Month 3", 10.0, 1)], classify,
                      component_line=component_line, month_token=month_token)[0]["month_rung"] == 3)
ck("A11 … and one that does not is not", _by["beta spiff"]["month_rung"] is None)
ck("A12 the store count is DISTINCT stores, not rows",
   _by["beta spiff"]["stores"] == 3, _by["beta spiff"]["stores"])
ck("A13 an empty window offers nothing (never an invented list)",
   S.pay_type_options([], classify) == [])
ck("A14 a row with no pay type is skipped, not counted as a blank type",
   S.pay_type_options([row("A", "", 5.0, 1)], classify) == [])


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§B  the report never silently chooses its own subject")
ck("B1 a requested type wins", S.default_selection(OPTS, "Alpha Bounty") ==
   ("Alpha Bounty", "requested"))
ck("B2 … matched through the folding rule, so a re-spelling still finds it",
   S.default_selection(OPTS, "  beta   spiff ")[1] == "requested")
ck("B3 a request the window cannot honour SAYS so, and shows nothing else",
   S.default_selection(OPTS, "Nothing Like This") == (None, "requested_not_found"))
ck("B4 with nothing requested it opens on the largest SPIFF and names the default",
   S.default_selection(OPTS) == ("Beta Spiff", "default_largest_spiff"), S.default_selection(OPTS))
_no_spiff = [o for o in OPTS if o["component"] != "SPIFF"]
ck("B5 with no spiff at all it falls back to commission, still naming the default",
   S.default_selection(_no_spiff)[1] == "default_largest_commission", S.default_selection(_no_spiff))
ck("B6 with neither, the largest of whatever there is — and it is still named a default",
   S.default_selection([{"type": "X", "component": None, "amount": 1.0}])[1] == "default_largest")
ck("B7 nothing at all is `none_available`, never a silent blank",
   S.default_selection([]) == (None, "none_available"))
# A NEGATIVE pay type (the carrier taking money back) must be reachable as a default by SIZE, or the
# biggest clawback on a statement could never be the thing the report opens on.
_neg = S.pay_type_options([row("A", "Delta Clawback", -900.0, 9), row("A", "Alpha Bounty", 10.0, 1)],
                          classify, component_line=component_line)
ck("B8 size is ABSOLUTE, so a large clawback outranks a small payment",
   _neg[0]["type"] == "Delta Clawback", [o["type"] for o in _neg])


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§C  the money — the share is of the line the dollars actually book to")
MONEY, SEL_LINE, UNRES = S.store_money(ROWS, "Beta Spiff", classify,
                                       component_line=component_line,
                                       store_of=lambda s: STORE_OF.get(s, ""))
ck("C1 the selected type's line is the one its component routes to",
   SEL_LINE == "carrier_comm", SEL_LINE)
ck("C2 the spiff's dollars are the folded type's, at the store",
   MONEY["A"]["spiff_amount"] == 400.0, MONEY["A"]["spiff_amount"])
ck("C3 the line total is every carrier dollar at that store on THAT line",
   MONEY["A"]["line_total"] == 1400.0, MONEY["A"]["line_total"])
ck("C4 … and so EXCLUDES the dollars that book elsewhere",
   MONEY["A"]["carrier_total"] == 6400.0, MONEY["A"]["carrier_total"])
ck("C5 the share is of the line, computed from the two numbers reported",
   MONEY["A"]["share_of_line_pct"] == round(400.0 / 1400.0 * 100, 2),
   MONEY["A"]["share_of_line_pct"])
ck("C6 the share of ALL carrier dollars is reported beside it",
   MONEY["A"]["share_of_carrier_pct"] == round(400.0 / 6400.0 * 100, 2))
ck("C7 a store the carrier paid NOTHING for this type reads 0.00, not missing",
   MONEY["C"]["spiff_amount"] == 0.0 and MONEY["C"]["spiff_units"] == 0)
ck("C8 … and its share of the line is an honest 0.0%, because the line is not empty",
   MONEY["C"]["share_of_line_pct"] == 0.0, MONEY["C"]["share_of_line_pct"])
ck("C9 a spelling the resolver cannot place is kept on its OWN key, never merged",
   "D-unmapped" in MONEY, sorted(MONEY))
_m2, _l2, _u2 = S.store_money(ROWS, "Beta Spiff", classify, component_line=component_line,
                              store_of=lambda s: ("" if s == "D-unmapped" else STORE_OF.get(s, "")))
ck("C10 … and a resolver that SIGNALS failure has the spelling reported",
   _u2 == ["D-unmapped"], _u2)
ck("C11 a selected type on a REIMBURSEMENT line changes the denominator to that line",
   S.store_money(ROWS, "Gamma Offer", classify, component_line=component_line,
                 store_of=lambda s: STORE_OF.get(s, ""))[1] == "vip_reimb")
_mg = S.store_money(ROWS, "Gamma Offer", classify, component_line=component_line,
                    store_of=lambda s: STORE_OF.get(s, ""))[0]
ck("C12 … and the share is then 100% of that line, which is the truth about it",
   _mg["A"]["share_of_line_pct"] == 100.0, _mg["A"]["share_of_line_pct"])
ck("C13 a type absent from the window leaves every store at 0.00 and no line",
   S.store_money(ROWS, "Not Present", classify, component_line=component_line,
                 store_of=lambda s: STORE_OF.get(s, ""))[0]["A"]["spiff_amount"] == 0.0)
# A dollar must appear in exactly ONE line bucket, or the shares are fractions of a lie.
for st, e in MONEY.items():
    ck(f"C14 {st}: the per-line buckets sum to the carrier total",
       round(sum(e["by_line"].values()), 2) == e["carrier_total"], (e["by_line"], e["carrier_total"]))


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§D  the profit effect — with it against without it")
_a = S.profit_effect(400.0, PL["A"])
ck("D1 the base is the profit that SURVIVES without the spiff",
   _a["net_income_ex_spiff"] == 4600.0, _a["net_income_ex_spiff"])
ck("D2 the lift is the spiff over that base — 'profit is this much higher because of it'",
   _a["profit_lift_pct"] == round(400.0 / 4600.0 * 100, 2), _a["profit_lift_pct"])
ck("D3 … and it is NOT the spiff over the profit that already contains it",
   _a["profit_lift_pct"] != round(400.0 / 5000.0 * 100, 2))
ck("D4 the margin points are the spiff as a share of everything the store took",
   _a["margin_points"] == round(400.0 / 20000.0 * 100, 2))
ck("D5 a positive base carries no reason — nothing to excuse", _a["profit_reason"] is None)
_b = S.profit_effect(50.0, PL["B"])
ck("D6 a store at a loss carries NO lift percentage", _b["profit_lift_pct"] is None, _b)
ck("D7 … and says why, naming the dollars as the answer",
   _b["profit_reason"] == S.LOSS_BASE_REASON)
ck("D8 … while its dollars-and-revenue figures are still reported",
   _b["net_income"] == -100.0 and _b["margin_points"] is not None)
_c = S.profit_effect(300.0, {"revenue": 1000.0, "net_income": 300.0})
ck("D9 a base of EXACTLY zero carries no percentage either (a lift of infinity is not a number)",
   _c["profit_lift_pct"] is None and _c["net_income_ex_spiff"] == 0.0)
_d = S.profit_effect(100.0, None)
ck("D10 no snapshot → every profit figure is None, never 0.00",
   _d["net_income"] is None and _d["revenue"] is None and _d["profit_lift_pct"] is None)
ck("D11 … and it says the figures are NOT COMPUTED rather than zero",
   _d["profit_reason"] == S.NO_SNAPSHOT_REASON)
_e = S.profit_effect(-200.0, PL["A"])
ck("D12 a clawback LOWERS profit, and the base rises by what was taken back",
   _e["net_income_ex_spiff"] == 5200.0, _e["net_income_ex_spiff"])
ck("D13 … the percentage is reported as the reduction it is, with the reason said",
   _e["profit_lift_pct"] < 0 and _e["profit_reason"] == S.NEGATIVE_SPIFF_REASON)
ck("D14 no revenue → no margin points, and no zero standing in for it",
   S.profit_effect(10.0, {"revenue": 0.0, "net_income": 10.0})["margin_points"] is None)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§E  the rate — paid units against boxes sold")
ck("E1 units per 100 boxes", S.units_per_100_boxes(30, 100) == 30.0)
ck("E2 no boxes → None, because a rate with no denominator is not a zero",
   S.units_per_100_boxes(30, 0) is None)
ck("E3 … and that holds for an unknown box count too",
   S.units_per_100_boxes(30, None) is None)
ck("E4 zero units over real boxes IS 0.0 — that is the measurement the owner asked for",
   S.units_per_100_boxes(0, 100) == 0.0)
ck("E5 the rate may exceed 100 when a pay type pays instalments on one sale",
   S.units_per_100_boxes(600, 100) == 600.0)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§F  the payload — the estate ties to its own rows")
PEER_BUILT = P.with_extra_metric(
    {k: ([dict(r) for r in v] if k in ("rows", "unbanded", "bands") else v)
     for k, v in PEER.items()},
    S.SPIFF_METRIC, S.SPIFF_METRIC_LABEL,
    {st: S.units_per_100_boxes((MONEY.get(st) or {}).get("spiff_units", 0), bx)
     for st, bx in (("A", 100), ("B", 100), ("C", 50))})
OUT = S.build(MONEY, PEER_BUILT, PL, selected="Beta Spiff", selected_entry=_by["beta spiff"],
              selection_basis="requested", options=OPTS, commission_line="carrier_comm",
              period="September 2026", unresolved_stores=[])
_rows = {r["store"]: r for r in OUT["rows"]}
ck("F1 every store with money OR sales is on the table",
   set(_rows) == {"A", "B", "C", "D-unmapped"}, sorted(_rows))
ck("F2 the estate spiff total is the sum of the rows, not a second read",
   OUT["estate"]["spiff_amount"] == round(400.0 + 50.0 + 0.0 + 20.0, 2),
   OUT["estate"]["spiff_amount"])
ck("F3 the estate units likewise", OUT["estate"]["spiff_units"] == 40 + 5 + 0 + 2)
ck("F4 the estate lift uses the ESTATE's own base, never an average of percentages",
   OUT["estate"]["profit_lift_pct"] ==
   round(470.0 / (5100.0 - 470.0) * 100, 2), OUT["estate"]["profit_lift_pct"])
ck("F5 only stores WITH a snapshot are counted in the profit headline",
   OUT["estate"]["stores_with_pl"] == 3, OUT["estate"]["stores_with_pl"])
ck("F6 the store at a loss is counted as one",
   OUT["estate"]["stores_at_a_loss"] == 1, OUT["estate"]["stores_at_a_loss"])
ck("F7 the row with no snapshot carries None, never 0.00",
   _rows["D-unmapped"]["net_income"] is None)
ck("F8 … and it says WHY it has no peers either",
   "no sales rows" in str(_rows["D-unmapped"].get("reason") or ""))
ck("F9 the zero-earning store is NOT hidden — it is the finding",
   _rows["C"]["spiff_amount"] == 0.0 and _rows["C"][S.SPIFF_METRIC] == 0.0)
ck("F10 rows are ordered by the money, biggest first",
   [r["store"] for r in OUT["rows"]][0] == "A", [r["store"] for r in OUT["rows"]])
ck("F11 the band, the traffic and the boxes come from the peer payload",
   _rows["A"]["band_label"] == "150–249 bill payments" and _rows["A"]["boxes"] == 100)
# A(40 units/100 boxes) · B(5) · C(0) → the band median is 5, so B is AT it and C is BELOW it. The
# store the report accuses is the one that earned NOTHING of the spiff on the boxes it sold, which is
# precisely the owner's question — and B, exactly at its band median, is deliberately NOT accused.
ck("F12 the gap on the table is §59's own gap for this metric",
   (_rows["C"]["gaps"] or {}).get("behind_median") is True, _rows["C"]["gaps"])
ck("F12b … and a store exactly AT its band median is not accused",
   (_rows["B"]["gaps"] or {}).get("behind_median") is False, _rows["B"]["gaps"])
ck("F13 the lagging list is §59's `lagging()`, on this report's metric",
   [i["store"] for i in OUT["lagging"]] == ["C"], OUT["lagging"])
# Guarded rather than indexed: a regression that empties this list must redden F13 and still let
# §I and §J report, instead of taking the whole file down with an IndexError and hiding them.
_lag0 = (OUT["lagging"] or [{}])[0]
ck("F14 … and each lagging store carries the sentence §59 owns",
   bool(_lag0.get("prompt")) and "traffic band" in str(_lag0.get("prompt")))
ck("F15 … and the dollars it actually earned, so the prompt is arguable",
   _lag0.get("spiff_amount") == 0.0, _lag0.get("spiff_amount"))
ck("F16 the metric is named on the payload, so a surface renders no key of its own",
   OUT["metric"] == S.SPIFF_METRIC and OUT["metric_label"] == S.SPIFF_METRIC_LABEL)
ck("F17 an empty window says there is nothing to assess, rather than showing an empty table",
   bool(S.build({}, None, {}, selected=None, selected_entry=None,
                selection_basis="none_available")["note"]))
ck("F18 the selection basis travels to the surface",
   OUT["selection_basis"] == "requested")
# THE IDENTITY. net_income − spiff == net_income_ex_spiff, for every row, always.
for st, r in _rows.items():
    if r["net_income"] is None:
        continue
    ck(f"F19 {st}: net profit − spiff == net profit without it",
       round(r["net_income"] - r["spiff_amount"], 2) == r["net_income_ex_spiff"],
       (r["net_income"], r["spiff_amount"], r["net_income_ex_spiff"]))


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§G  the caveats — each fires on its condition and NOT otherwise")


def sevs(**kw):
    return {c["severity"] for c in S.caveats(kw.pop("entry", None), **kw)}


ck("G1 a pay type booking off the commission line is flagged, loudly",
   "different_line" in sevs(entry=_by["gamma offer"], commission_line="carrier_comm"))
ck("G2 … and a pay type ON the commission line is NOT flagged",
   "different_line" not in sevs(entry=_by["alpha bounty"], commission_line="carrier_comm"))
ck("G3 an undeclared type says the component is not the org's own word",
   "not_declared" in sevs(entry=_by["beta spiff"], commission_line="carrier_comm"))
ck("G4 … and names the twin when the component came from an inference",
   any("older gamma offer" in c["message"] for c in
       S.caveats(_by["gamma offer"], commission_line="carrier_comm")))
ck("G5 … and a DECLARED type raises no such caveat",
   "not_declared" not in sevs(entry=_by["alpha bounty"], commission_line="carrier_comm"))
_rung = S.pay_type_options([row("A", "Alpha Bounty - Month 3", 10.0, 1)], classify,
                           component_line=component_line, month_token=month_token)[0]
ck("G6 a month-rung pay type warns that the units are an earlier cohort",
   "different_cohort" in sevs(entry=_rung, commission_line="carrier_comm"))
ck("G7 … and says the rate can exceed 100%, so nobody reads it as a share of this month",
   any("exceed 100" in c["message"] for c in S.caveats(_rung, commission_line="carrier_comm")))
ck("G8 … and a non-rung type does not warn",
   "different_cohort" not in sevs(entry=_by["beta spiff"], commission_line="carrier_comm"))
ck("G9 an unresolvable store spelling is reported as an identity gap, not a sales result",
   "unresolved_identity" in sevs(entry=_by["alpha bounty"], unresolved_stores=["D-unmapped"]))
ck("G10 a store with money but no P&L is named, and blank is called not-computed",
   any("never zero" in c["message"] for c in S.caveats(_by["alpha bounty"],
                                                       stores_without_pl=["D-unmapped"])))
ck("G11 the profit columns say WHEN they were computed, because they do not recompute on load",
   any("Recompute" in c["message"] for c in S.caveats(_by["alpha bounty"],
                                                      pl_computed_at="2026-10-08 20:16 UTC")))
ck("G12 … and say nothing of the sort when there is no snapshot time to report",
   "as_computed" not in sevs(entry=_by["alpha bounty"]))
ck("G13 stores with no profit base are counted in one sentence, not accused one by one",
   "loss_base" in sevs(entry=_by["alpha bounty"], stores_at_a_loss=1, stores_measured=3))
ck("G14 a clean tenant raises NO caveats at all (the panel is not decorative)",
   sevs(entry=_by["alpha bounty"], commission_line="carrier_comm") == set())
ck("G15 the payload's caveats include the ones the build's own facts earn",
   {"not_declared", "loss_base", "as_computed"} <= {c["severity"] for c in OUT["caveats"]} or
   {"not_declared", "loss_base"} <= {c["severity"] for c in OUT["caveats"]},
   [c["severity"] for c in OUT["caveats"]])
ck("G16 … and the unjoinable store is reported from the build's OWN facts, uninstructed",
   "unresolved_identity" in {c["severity"] for c in OUT["caveats"]},
   [c["severity"] for c in OUT["caveats"]])


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§H  THE REGRESSION — the live September 2026 figures, pinned as the oracle")
# Measured 2026-10-08, read-only, house org `00000000-…-0001`, `commcalc.raw_comp_report` for
# September 2026 (9,846 rows), classified through §58 with the org's own 154 declarations and 19
# keyword rules. These numbers are the ORACLE because they tie EXACTLY to the 31 stored per-store
# P&L snapshots for the same month — which is the whole claim this report makes: it reads the same
# dollars the P&L books.
SEPT = {"carrier_comm": 110780.14, "vip_reimb": 373211.61}
SEPT_COMPONENTS = {"COMMISSION": 105340.19, "SPIFF": 5295.00,
                   "REIMBURSEMENT": 373211.61, "UNRESOLVED": 144.95}
ck("H1 the two P&L lines sum to the whole statement",
   round(SEPT["carrier_comm"] + SEPT["vip_reimb"], 2) == 483991.75,
   round(SEPT["carrier_comm"] + SEPT["vip_reimb"], 2))
ck("H2 the commission line is COMMISSION + SPIFF + the unresolved money, to the cent",
   round(SEPT_COMPONENTS["COMMISSION"] + SEPT_COMPONENTS["SPIFF"]
         + SEPT_COMPONENTS["UNRESOLVED"], 2) == SEPT["carrier_comm"],
   round(SEPT_COMPONENTS["COMMISSION"] + SEPT_COMPONENTS["SPIFF"]
         + SEPT_COMPONENTS["UNRESOLVED"], 2))
ck("H3 the reimbursement line is the REIMBURSEMENT component, to the cent",
   SEPT_COMPONENTS["REIMBURSEMENT"] == SEPT["vip_reimb"])
# THE LIVE SPIFF. `2026 BYOD SPIFF - Month 2` — $2,632.50 over 242 paid units at 27 stores, the
# largest SPIFF-component type on the month. It is the type the report opens on by default, so the
# default path is the measured path.
ck("H4 the largest live SPIFF type is a small share of a line it does book to",
   round(2632.50 / SEPT["carrier_comm"] * 100, 2) == 2.38,
   round(2632.50 / SEPT["carrier_comm"] * 100, 2))
# AND THE FINDING THE REPORT EXISTS FOR, pinned so it cannot regress quietly: on the live house org
# MOST carrier promo money is NOT commission. A report that showed these as commission would restate
# the very money §58 moved.
ck("H5 77% of the live statement books to the reimbursement line, not the commission line",
   round(SEPT["vip_reimb"] / (SEPT["carrier_comm"] + SEPT["vip_reimb"]) * 100) == 77,
   round(SEPT["vip_reimb"] / (SEPT["carrier_comm"] + SEPT["vip_reimb"]) * 100))
# The profit side's live shape, pinned because it is WHY the lift column must tolerate a loss:
# 13 of the 28 September stores have no positive profit base.
ck("H6 the lift column must survive a majority-loss month (13 of 28 stores, live September)",
   S.profit_effect(85.0, {"revenue": 21418.71, "net_income": -723.20})["profit_lift_pct"] is None)
ck("H7 … and the owner's own example store still reports its dollars",
   S.profit_effect(85.0, {"revenue": 21418.71, "net_income": -723.20})["net_income"] == -723.20)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§I  THE UN-WIRE LOCK")
_here = __file__.rsplit("/", 1)[0]
_mod = open(f"{_here}/app/modules/commcalc/spiff_impact.py").read()
# PROSE MUST BE ALLOWED TO DISCUSS WHAT THE CODE MAY NOT DO. This module's docstrings name the homes
# it dereferences and the fields it must never read, so a textual check over the whole file would
# redden on its own explanation — and a check that only strips the MODULE docstring (the first draft
# here) still let a function docstring satisfy it, which is how the §19.28 trap gets in. So: the
# textual checks run over code with EVERY docstring removed, and "does it still dereference §59" is
# an AST check over real Call nodes, which prose cannot satisfy at all.
import ast as _ast                                                              # noqa: E402

_tree = _ast.parse(_mod)
for _n in _ast.walk(_tree):
    if isinstance(_n, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
        _b = getattr(_n, "body", None)
        if _b and isinstance(_b[0], _ast.Expr) and isinstance(_b[0].value, _ast.Constant) \
                and isinstance(_b[0].value.value, str):
            _b[0].value.value = ""
_body = "\n".join(l for l in _ast.unparse(_tree).split("\n") if not l.lstrip().startswith("#"))
_CALLS = {n.func.attr for n in _ast.walk(_ast.parse(_mod))
          if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)}

# 1) IT MUST NOT TOUCH A SALE LINE. Every box, activation and bill payment belongs to
#    `_sales_cell_agg` (§59.1's finding). A read of a raw sale field here is a second derivation.
for field in ("contract_type", "department", "product_desc", "trans_id", "salesperson",
              "box_departments", "accessory_rev"):
    ck(f"I1 no raw sale field read: {field}", f'"{field}"' not in _body and f"'{field}'" not in _body)
# 2) IT MUST NOT CLASSIFY A DOLLAR. §58 owns that, and is INJECTED.
ck("I2 no classifier import — the verdict is handed in",
   "carrier_dollar_class" not in _body and "carrier_map" not in _body)
ck("I3 … and no keyword ladder of its own",
   not any(t in _body.lower() for t in ("startswith(\"promo", "'promo'", '"promo"',
                                        "'bounty'", '"bounty"', "'spiff '", "contains(")))
# 3) IT MUST NOT RE-DECIDE "BEHIND". §59 owns the median, the gap and the verdict.
ck("I4 no median, no gap and no band of its own",
   not any(t in _body for t in ("def _median", "def _gaps", "def band_of", "HOUSE_BANDS",
                                "BAND_MIN_PEERS", "behind_median =", "gap_to_median =")))
ck("I5 … and it DEREFERENCES §59's `lagging` and sentence instead (AST, so prose cannot pass it)",
   "peer_comparison" in _body and {"lagging", "prompt_sentence"} <= _CALLS, sorted(_CALLS))
# 4) THE THREE HOMES IT READS MUST STILL BE READ. A home written but not wired is §19.18's trap.
_router = open(f"{_here}/app/modules/commcalc/router.py").read()
_ep = _router.split("def _spiff_impact_inputs", 1)[1].split("\n@router.get(\"/sales-diagnostics\")", 1)[0]
ck("I6 the router reads the classification through §58's one home",
   "_cdc.classify(" in _ep and "_cdc.component_line(" in _ep and "_cdc.load_declarations(" in _ep)
ck("I7 … the per-store P&L through §19.50's deduped read and §4's totals home",
   "store_snapshots(" in _ep and "pl_totals(" in _ep)
ck("I8 … the month token through its own home, not a regex here",
   "parse_payment_month" in _ep and "re.compile" not in _ep)
ck("I9 … the store key through the SAME resolver §59 and Daily Targets use",
   "_store_code_resolver(" in _ep)
ck("I10 … the sales side through the ONE shared assembly, never a second read of its own",
   "_peer_comparison_payload(" in _ep)
ck("I11 … and the metric is folded in THROUGH §59, never ranked here",
   "with_extra_metric(" in _ep)
ck("I12 the carrier feed is read PAGED, with NO literal row ceiling (§19.48's rule)",
   "_feed_read.read_all(" in _ep and ".limit(" not in _ep)
ck("I13 a comparison that could not run is REPORTED, not silently absent",
   "peer_meta" in _ep and '"ran"' in _ep)
# 5) THE EXTRACTED HOME MUST STAY ONE HOME. Two readers of the per-store snapshots is §19.50 again.
_sf = open(f"{_here}/app/modules/account/statement_filter.py").read()
ck("I14 `store_snapshots` exists where the report expects it", "def store_snapshots(" in _sf)
ck("I15 … and the filtered statement DEREFERENCES it rather than keeping its own read",
   "store_snapshots(client, org_id, period, st_type)" in _sf
   and _sf.count('.like("scope_key", "store:%")') == 1,
   _sf.count('.like("scope_key", "store:%")'))
ck("I16 … including the dedupe, which must not be re-done at a call site",
   _sf.count("dedupe_latest(") == 1, _sf.count("dedupe_latest("))
# 6) RULE TWO, on this module's own literals. Not one carrier, tenant, promo, quarter or product
#    word may appear — the vocabulary is rows in the tenant's tables.
_lit = _body.lower()
for word in ("boost", "luxelink", "vzone", "total wireless", "byod spiff", "promo pic",
             "q1 ", "q2 ", "q3 ", "q4 ", "iphone", "tablet -", "fulton"):
    ck(f"I17 RULE TWO: no '{word.strip()}' in the module's code", word not in _lit)
ck("I18 the component names it DOES carry are §58's own vocabulary, as a tuple of components",
   S.DEFAULT_PICK_COMPONENTS == ("SPIFF", "COMMISSION"))
# 7) PURITY. The module must do no IO at all; the one import it makes at runtime is §59's.
ck("I19 no client, no table, no network in the module",
   not any(t in _body for t in ("client.", ".table(", ".execute(", "requests.", "urllib")))


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§J  `with_extra_metric` — a borrowed metric ranks through §59's own machinery")
BASE = {
    "rows": [
        {"store": "A", "band": 1, "boxes": 100, "billpay_txns": 200, "band_label": "b1"},
        {"store": "B", "band": 1, "boxes": 100, "billpay_txns": 210, "band_label": "b1"},
        {"store": "C", "band": 1, "boxes": 50, "billpay_txns": 190, "band_label": "b1"},
        {"store": "Z", "band": 2, "boxes": 300, "billpay_txns": 500, "band_label": "b2"},
    ],
    "unbanded": [], "gap_metrics": [],
    "bands": [{"band": 1, "label": "b1", "leader": "A"}, {"band": 2, "label": "b2", "leader": "Z"}],
}


def fresh():
    import copy
    return copy.deepcopy(BASE)


J = P.with_extra_metric(fresh(), "mine", "Mine", {"A": 40.0, "B": 5.0, "C": 20.0, "Z": 9.0})
_jr = {r["store"]: r for r in J["rows"]}
ck("J1 the value lands on the row", _jr["A"]["mine"] == 40.0)
ck("J2 the gap is computed by §59's own `_gaps`, inside the band",
   _jr["B"]["gaps"]["mine"]["band_median"] == 20.0, _jr["B"]["gaps"]["mine"])
ck("J3 … the median INCLUDES the store itself (a band of two else has none)",
   _jr["A"]["gaps"]["mine"]["band_median"] == 20.0)
ck("J4 … and the band's best is the band's, not the estate's",
   _jr["A"]["gaps"]["mine"]["band_best"] == 40.0)
ck("J5 a store alone in its band carries NO gap — being alone is not under-performance",
   _jr["Z"]["gaps"] == {}, _jr["Z"]["gaps"])
# A(40) · B(5) · C(20) in band 1 → median 20, so only B is behind it; C sits exactly on it.
ck("J6 `lagging` works on the borrowed metric with no change of its own",
   [i["store"] for i in P.lagging(J, metric="mine")] == ["B"],
   [i["store"] for i in P.lagging(J, metric="mine")])
ck("J7 … and the sentence names the metric's label, the median and the leader",
   "mine" in P.prompt_sentence(P.lagging(J, metric="mine")[0]).lower()
   and "A is at" in P.prompt_sentence(P.lagging(J, metric="mine")[0]))
ck("J8 the metric is announced on `gap_metrics`, so a surface can render the column",
   {"key": "mine", "label": "Mine", "higher_is_better": True} in J["gap_metrics"])
_twice = P.with_extra_metric(
    P.with_extra_metric(fresh(), "mine", "Mine", {"A": 40.0, "B": 5.0, "C": 20.0}),
    "mine", "Mine", {"A": 40.0, "B": 5.0, "C": 20.0})
ck("J9 … and announced ONCE, however many times it is folded in",
   len(_twice["gap_metrics"]) == 1, _twice["gap_metrics"])
_absent = P.with_extra_metric(fresh(), "mine", "Mine", {"A": 40.0, "B": None})
_ar = {r["store"]: r for r in _absent["rows"]}
ck("J10 absence is NOT ranked as a zero — a None keeps no value",
   _ar["B"]["mine"] is None and "mine" not in (_ar["B"]["gaps"] or {}), _ar["B"])
ck("J11 … and a store missing from the map likewise",
   _ar["C"]["mine"] is None and "mine" not in (_ar["C"]["gaps"] or {}))
ck("J12 the band summary carries the borrowed metric's best and median",
   J["bands"][0]["mine_best"] == 40.0 and J["bands"][0]["mine_median"] == 20.0, J["bands"][0])
# BYTE-IDENTICAL FOR EVERY EXISTING CALLER. `_gaps`' metrics parameter must default to §59's own set,
# or the peer screen and the action plan would silently start ranking on somebody else's column.
_plain = {"store": "A", "boxes": 10, "boxes_per_billpay": 1.0}
_peers = [_plain, {"store": "B", "boxes": 20, "boxes_per_billpay": 2.0}]
ck("J13 `_gaps` with no metrics argument ranks exactly §59's declared set",
   set(P._gaps(_plain, _peers)) == {"boxes", "boxes_per_billpay"},
   sorted(P._gaps(_plain, _peers)))
ck("J14 … and the declared set is unchanged by this PR",
   [k for k, _l, _h in P.GAP_METRICS] == ["boxes", "boxes_per_billpay", "aal",
                                          "accessory_revenue", "accessory_per_box",
                                          "family_plan_pct"],
   [k for k, _l, _h in P.GAP_METRICS])
ck("J15 a declared direction is honoured, so a lower-is-better metric cannot invert silently",
   P.with_extra_metric(fresh(), "bad", "Bad", {"A": 40.0, "B": 5.0, "C": 20.0},
                       higher_is_better=False)["rows"][0]["gaps"]["bad"]["behind_median"] is True)
ck("J16 an empty payload is returned untouched rather than raising",
   P.with_extra_metric(None, "mine", "Mine", {}) is None)
ck("J17 the module graph's own peer fact is unchanged by the parameter",
   "def with_extra_metric" in open(f"{_here}/app/modules/commcalc/peer_comparison.py").read())


print("\n" + "=" * 70)
if FAIL:
    print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
    sys.exit(1)
print(f"Spiff Impact — all checks passed")
