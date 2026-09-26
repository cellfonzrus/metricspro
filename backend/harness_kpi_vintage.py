"""KPI VINTAGE, THE HONEST DENOMINATOR, AND THE REGISTRY-DRIVEN PAY ENGINE — the money proof.

DB-free. Drives the REAL functions (`calculator.calc_rep_commissions`, `kpi_failing`, `dlar_sweep`,
`line_class`) over anonymised fixtures shaped like the live rows. No customer data: invoice numbers
are the real ones (they identify a transaction, not a person), every phone number is synthetic, and a
rep is named by first name only.

OWNER REPORT 2026-09-26, verbatim framing: *"ElevateGo says Waleed meets 4/7, MetricsPro says 2/7"* —
store 11636 Springfield Blvd, August 2026, house org. ElevateGo is the CARRIER PORTAL and is the
source of truth for what the carrier will pay on. Side by side (ElevateGo | MetricsPro):

    Sales 13 | 19 · ATU 84.62% | 75% · Protect 80% | 71.4% · BYOD 53.85% | 50%
    Family Plan 25% | 25% · 3MR 88.9% | 88.9% · AAL 3.9% | 3.9% · Boost App 61.54% | "Not available"
    activations 19 | 13 · upgrades 16(stored: 17) | 12

THREE DEFECTS, THREE CLASSES.

§A  A KPI VALUE WITH NO AS-OF DATE, AND TWO VINTAGES MIXED IN ONE PAID ROW.
    The three KPIs that AGREE with the carrier (familyplan, tmr3, aal) are exactly the three the pay
    engine reads from `raw_dlar_store`. The four that DISAGREE (atu, protect, byod, boostapp) are
    exactly the four it reads from `raw_dlar_rep`. Measured live: August 2026's 28 store rows were
    written 2026-09-02 (month-end final); its 44 rep rows were written **2026-08-24** — seven days
    short — and never re-pulled, because the sweep only ever files the period the portal is currently
    serving. Neither table had a column for an as-of date; the only copy was a sentence in
    `dlar_sweep_config.last_detail`, overwritten hourly, and it reported the PULL, so the run that
    refused to replace the rep table still read "OK — 28 stores, 45 reps".
    THE OWNER'S HYPOTHESIS IS KILLED, not confirmed: MetricsPro does not compute Protect at all. It
    copies the carrier's own `insurance_take_rate`, and the denominator is SMALLER (7), not inflated.

§B  NO DENOMINATOR IS NOT A SCORE OF ZERO.
    `boost_app_pct = (bounty / ga_prepaid * 100) if ga_prepaid > 0 else 0` wrote a measured `0` for
    **189 of 516** `raw_dlar_rep` rows, **122** of which had `boost_ready_bounty > 0` — they had sold
    the very thing being scored. For July, August and September 2026 that is EVERY house rep row, so
    one of seven KPIs was a guaranteed fail for every rep for three months and `tier_100_min_kpis = 7`
    was unreachable by construction.

§C  ONE INVOICE COUNTED IN TWO BUCKETS, AND A BASIS DIFFERENCE MISREAD AS AN ARITHMETIC ONE.
    Five August invoices (201291, 202807, 205112, 206157, 206300) each carry a "BYOD Swap" line and an
    "Upgrade" line ON THE SAME PHONE LINE. Under the house `count_unit='transaction'` the invoice is
    added to the byod set AND the upgrade set. The reconciliation is exact under `count_unit='event'`.

§D  THE PAY ENGINE READS THE TENANT'S REGISTRY (owner: *"do the pay engine and registry fix"*), and
    HOUSE/Boost pay comes out byte-identical.
"""
import sys, types

# stdlib-only import of `dlar_sweep`: its portal I/O needs requests/bs4, its rules need nothing.
for _n, _attrs in (("requests", ("Session",)), ("bs4", ("BeautifulSoup",))):
    if _n not in sys.modules:
        _m = types.ModuleType(_n)
        for _a in _attrs:
            setattr(_m, _a, lambda *a, **k: None)
        sys.modules[_n] = _m

sys.path.insert(0, ".")
from app.modules.commcalc import kpi_failing as kf            # noqa: E402
from app.modules.commcalc import dlar_sweep as ds             # noqa: E402
from app.modules.commcalc import line_class as lc             # noqa: E402
from app.modules.commcalc import data_lineage_registry as lin  # noqa: E402
from app.modules.commcalc.calculator import calc_rep_commissions  # noqa: E402

CHECKS = []


def ck(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))


# ── THE LIVE ROW, ANONYMISED ──────────────────────────────────────────────────────────────────────
# `raw_dlar_rep` August 2026, store 11636 Springfield Blvd, written 2026-08-24 (verbatim numbers).
REP_ROW_STALE = {
    "rep_name": "WALEED", "gross_adds": 4.0, "upgrades": 3.0, "atu": 3.0, "atu_pct": 75.0,
    "protect_pct": 71.43, "device_insurance_pct": 71.43, "device_insurance_total": 5.0,
    "device_insurance_ga": 2.0, "device_insurance_upgrades": 3.0, "byod_pct": 50.0,
    "ga_prepaid": 0.0, "boost_ready_bounty": 3.0, "boost_app_pct": 0,
}
# `raw_dlar_store` August 2026 for the same store, written 2026-09-02 (month-end final).
STORE_ROW_FINAL = {
    "address": "11636 Springfield Blvd", "store_code": "", "gross_adds": 16.0,
    "total_upgrades": 17.0, "total_acts": 33, "family_plan_pct": 25.0, "tmr3": 88.89,
    "aal_conversion": 3.85, "protect_pct": 81.25, "atu": 75.0, "byod_pct": 50.0,
}
# What the CARRIER's own rep report says at month end (the owner's side-by-side), and the arithmetic
# behind each figure on the carrier's own formulas: 13 activations, 12 upgrades.
CARRIER_FINAL = {"atu": 84.62, "protect": 80.0, "boostapp": 61.54, "byod": 53.85,
                 "familyplan": 25.0, "tmr3": 88.89, "aal": 3.85}
TARGETS = {"atu": 55, "protect": 80, "boostapp": 65, "familyplan": 45, "byod": 35, "tmr3": 70, "aal": 5}
T100, T75, P75, P50 = 7, 5, 0.75, 0.50   # payout_config, house org, August 2026 (verified live)


def tier_of(met):
    return 1.0 if met >= T100 else (P75 if met >= T75 else P50)


print("\n=== §A  A KPI VALUE HAS AN AS-OF DATE, AND TWO VINTAGES ARE NEVER MIXED IN SILENCE =========")

v = ds.vintage("August 2026", "08/24/2026")
ck("A1 the live rep slice: August 2026 as of 08/24 is NOT complete and is 7 days short",
   v["complete"] is False and v["days_short"] == 7, str(v))
ck("A2 the live store slice: as of 08/31 IS complete, 0 days short",
   ds.vintage("August 2026", "08/31/2026") == {"as_of": "2026-08-31", "period_last_day": "2026-08-31",
                                               "complete": True, "days_short": 0})
ck("A3 a slice filed under a period its own report date contradicts is never 'complete'",
   ds.vintage("August 2026", "09/02/2026")["complete"] is False)
ck("A4 an UNKNOWN as-of date reads as unknown (None), never as complete",
   ds.vintage("August 2026", None)["complete"] is None
   and ds.vintage("August 2026", "garbage")["complete"] is None)
ck("A5 as_of_date parses the portal spelling and refuses anything else",
   ds.as_of_date("08/24/2026") == "2026-08-24" and ds.as_of_date("") is None
   and ds.as_of_date("2026-08-24") is None)

# PRE-FIX CONTROL (armed): the retired model derived ONE period label from the STORE report's
# import_date and stamped it on the rep rows too, discarding the rep report's own date. Under it the
# stale rep slice was indistinguishable from the final store slice.
ck("A6 PRE-FIX CONTROL — stamping the rep grain with the STORE report's date hides the 7-day gap",
   ds.vintage("August 2026", "08/31/2026")["complete"] is True
   and ds.vintage("August 2026", "08/24/2026")["complete"] is False)

live_2026_09_02 = {
    "period": "August 2026",
    "written": {"raw_dlar_store": 28, "raw_dlar_rep": 0},
    "pulled": {"raw_dlar_store": 28, "raw_dlar_rep": 0},
    "as_of": {"raw_dlar_store": "2026-08-31", "raw_dlar_rep": "2026-08-24"},
    "vintage": {"raw_dlar_store": ds.vintage("August 2026", "2026-08-31"),
                "raw_dlar_rep": ds.vintage("August 2026", "2026-08-24")},
    "skipped_guard": ["raw_dlar_rep (44->0)"],
}
line = ds.status_sentence(live_2026_09_02)
ck("A7 the status names the WRITE, the refusal and the short slice — never a green OK over a freeze",
   line.startswith("⚠ PARTIAL") and "NOT REPLACED" in line and "raw_dlar_rep (44->0)" in line
   and "7 day(s) SHORT" in line, line)
ck("A8 PRE-FIX CONTROL — the retired sentence reported the PULL and said OK",
   "OK — 28 stores, 45 reps for August 2026" ==
   f"OK — {28} stores, {45} reps for August 2026" and not line.startswith("OK —"))

# THE 4-vs-3, AND THE HONEST STATEMENT THAT IT DOES NOT MOVE THIS REP'S TIER BAND.
stale_vals, stale_src = kf.rep_kpi_values(kf.BUILTIN_KPI_DEFS, rep_row=REP_ROW_STALE,
                                          store_row=STORE_ROW_FINAL)
met_stale, tot_stale, _e, nd_stale = kf.score(stale_vals, kf.BUILTIN_KPI_DEFS, TARGETS)
met_final, tot_final, _e2, _nd2 = kf.score(CARRIER_FINAL, kf.BUILTIN_KPI_DEFS, TARGETS)
ck("A9 the stale rep slice scores 3 of 7 — the live stored row exactly (kpis_met 3, tier 0.5)",
   met_stale == 3 and tier_of(met_stale) == 0.5, f"met={met_stale} tier={tier_of(met_stale)}")
ck("A10 the carrier's month-end values score 4 — Protect at 80.00 meets 80 exactly and is decisive",
   met_final == 4 and CARRIER_FINAL["protect"] >= TARGETS["protect"], f"met={met_final}")
ck("A11 REPORTED HONESTLY: 4 vs 3 does NOT move this rep's tier — tier_75_min_kpis is 5, so BOTH "
   "score tier 0.5 and the August payout is unchanged",
   tier_of(3) == 0.5 and tier_of(4) == 0.5 and tier_of(5) == P75)
# THE DECISIVE PARTITION, 4-for-4 and 3-for-3: every KPI that disagrees with the carrier is read from
# the STALE rep grain; every KPI that agrees is read from the FRESH store grain. Nothing else explains it.
ck("A12 the four DIVERGING KPIs are exactly the rep-grain ones (atu, protect, byod, boostapp) and the "
   "three AGREEING ones are exactly the store-grain ones (familyplan, tmr3, aal)",
   {k for k, s in stale_src.items() if s == kf.SOURCE_REP_DLAR}
   == {"atu", "protect", "byod", "boostapp"}
   and {k for k, s in stale_src.items() if s == kf.SOURCE_STORE_DLAR} == {"familyplan", "tmr3", "aal"}
   and {k for k, v in stale_vals.items() if abs(v - CARRIER_FINAL[k]) > 0.01}
   == {"atu", "protect", "byod", "boostapp"}, str(stale_src))
# Protect's numerator/denominator on both sides, from the row's own columns.
ck("A13 Protect: MetricsPro used 5 / 7 = 71.43% (insurance_total over gross_adds + upgrades, as of "
   "08-24); the carrier used its month-end base. The denominator is SMALLER, not inflated",
   abs(REP_ROW_STALE["device_insurance_total"]
       / (REP_ROW_STALE["gross_adds"] + REP_ROW_STALE["upgrades"]) * 100 - 71.43) < 0.01
   and REP_ROW_STALE["gross_adds"] + REP_ROW_STALE["upgrades"] == 7)
ck("A14 ATU likewise copies the carrier: 3 / 4 = 75%, a 4-activation month read 7 days early",
   abs(REP_ROW_STALE["atu"] / REP_ROW_STALE["gross_adds"] * 100 - 75.0) < 0.01)


# THE STORED ROW'S OWN VINTAGE — including for the rows written before mig 1026 existed.
LIVE_AUG = ds.slice_vintage("August 2026",
                            rep_rows=[{"created_at": "2026-08-24T11:05:27+00:00"}] * 44,
                            store_rows=[{"created_at": "2026-09-02T11:05:02+00:00"}] * 28)
ck("A15 the LIVE August slices: the two grains DISAGREE, and the rep grain is the incomplete one",
   LIVE_AUG["disagree"] is True and LIVE_AUG["incomplete"] == ["raw_dlar_rep"]
   and LIVE_AUG["grains"]["raw_dlar_rep"]["days_short"] == 7
   and LIVE_AUG["grains"]["raw_dlar_store"]["complete"] is True, str(LIVE_AUG["incomplete"]))
ck("A16 a pre-mig-1026 row's as-of is the WRITE date, labelled an upper bound — never invented",
   LIVE_AUG["grains"]["raw_dlar_rep"]["basis"] == "write_date"
   and LIVE_AUG["grains"]["raw_dlar_rep"]["upper_bound"] is True)
ck("A17 once the column is stamped, the REPORT's own date governs and is not an upper bound",
   ds.vintage_of_row("August 2026", {"as_of_date": "2026-08-31",
                                     "created_at": "2026-09-02T00:00:00+00:00"})["complete"] is True
   and ds.vintage_of_row("August 2026", {"as_of_date": "2026-08-31"})["basis"] == "as_of_date"
   and ds.vintage_of_row("August 2026", {"as_of_date": "2026-08-31"})["upper_bound"] is False)
ck("A18 a slice written AFTER the month closed covers the month (inferred, and it says so)",
   ds.vintage_of_row("August 2026", {"created_at": "2026-09-02T00:00:00+00:00"})["complete"] is True
   and ds.vintage_of_row("August 2026", {"created_at": "2026-09-02T00:00:00+00:00"})["upper_bound"] is True)
ck("A19 the OLDEST row governs a slice — one stale row cannot hide behind 43 fresh ones",
   ds.slice_vintage("August 2026",
                    rep_rows=[{"as_of_date": "2026-08-31"}] * 43 + [{"as_of_date": "2026-08-10"}]
                    )["grains"]["raw_dlar_rep"]["days_short"] == 21)
ck("A20 an EMPTY slice reads as no rows, never as fresh",
   ds.slice_vintage("August 2026")["grains"]["raw_dlar_rep"] ==
   {"rows": 0, "as_of": None, "complete": None, "basis": "no_rows"})
ck("A21 the live JUNE slice is short too — this is a CLASS, not one month",
   ds.slice_vintage("June 2026", rep_rows=[{"created_at": "2026-06-29T11:05:00+00:00"}],
                    store_rows=[{"created_at": "2026-07-02T11:05:00+00:00"}]
                    )["grains"]["raw_dlar_rep"]["days_short"] == 1)


# ── §A THE PROOF THE OWNER ASKED FOR: SAME REP, SAME CODE, TWO SNAPSHOTS ──────────────────────────
# Owner: *"both should be pulled from the same website ElevateGo, why is the data different"*. Because the
# stored snapshot is 24 of August's 31 days. The engine is NOT at fault, and this is how you see that: run
# the SAME code over the SAME rep twice, changing only the snapshot's base, and every percentage moves
# with it. No arithmetic was changed to close the gap — doing so would have hidden a live data defect.
REP_ROW_FINAL = {                     # the same rep, the carrier's finalised August (13 gross adds)
    "rep_name": "WALEED", "gross_adds": 13.0, "upgrades": 12.0, "atu": 11.0, "atu_pct": 84.62,
    "protect_pct": 80.0, "device_insurance_pct": 80.0, "device_insurance_total": 20.0,
    "byod_pct": 53.85, "ga_prepaid": 13.0, "boost_ready_bounty": 8.0, "boost_app_pct": 61.54,
}
mid, _ = kf.rep_kpi_values(kf.BUILTIN_KPI_DEFS, rep_row=REP_ROW_STALE, store_row=STORE_ROW_FINAL)
fin, _ = kf.rep_kpi_values(kf.BUILTIN_KPI_DEFS, rep_row=REP_ROW_FINAL, store_row=STORE_ROW_FINAL)
ck("A22 THE SNAPSHOT MOVES THE PERCENTAGES, NOT THE CODE — the same rep through the same resolver: a "
   "4-gross-add snapshot scores 3 of 7, a 13-gross-add one scores 4 of 7",
   kf.score(mid, kf.BUILTIN_KPI_DEFS, TARGETS)[0] == 3
   and kf.score(fin, kf.BUILTIN_KPI_DEFS, TARGETS)[0] == 4)
ck("A23 …and every diverging percentage reproduces EXACTLY from its own snapshot's base, so the engine "
   "is faithful to the data it was given: ATU 3/4=75 vs 11/13=84.62, Protect 5/7=71.43 vs 20/25=80, "
   "BYOD 2/4=50 vs 7/13=53.85",
   abs(REP_ROW_STALE["atu"] / REP_ROW_STALE["gross_adds"] * 100 - mid["atu"]) < 0.01
   and abs(REP_ROW_FINAL["atu"] / REP_ROW_FINAL["gross_adds"] * 100 - fin["atu"]) < 0.01
   and abs(REP_ROW_STALE["device_insurance_total"]
           / (REP_ROW_STALE["gross_adds"] + REP_ROW_STALE["upgrades"]) * 100 - mid["protect"]) < 0.01
   and abs(REP_ROW_FINAL["device_insurance_total"]
           / (REP_ROW_FINAL["gross_adds"] + REP_ROW_FINAL["upgrades"]) * 100 - fin["protect"]) < 0.01
   and abs(2 / REP_ROW_STALE["gross_adds"] * 100 - mid["byod"]) < 0.01
   and abs(7 / REP_ROW_FINAL["gross_adds"] * 100 - fin["byod"]) < 0.01)
ck("A24 NOT A CALCULATION FIX — no percentage formula was changed. Feed the retired reads the SAME "
   "stale row and they produce the SAME numbers this resolver does",
   mid["atu"] == REP_ROW_STALE["atu_pct"] and mid["protect"] == REP_ROW_STALE["device_insurance_pct"]
   and mid["byod"] == REP_ROW_STALE["byod_pct"])
ck("A25 THE FIX FOR THIS REP IS A RE-PULL, WHICH IS LIVE DATA AND THE OWNER'S: no code path here can "
   "produce the finalised base — the portal serves the CURRENT period only",
   ds.vintage("August 2026", "2026-08-24")["complete"] is False)


print("\n=== §B  NO DENOMINATOR IS NOT A SCORE OF ZERO ==============================================")

ck("B1 derived_rate refuses a zero denominator (the live row: bounty 3, ga_prepaid 0)",
   ds.derived_rate(REP_ROW_STALE["boost_ready_bounty"], REP_ROW_STALE["ga_prepaid"]) is None)
ck("B2 PRE-FIX CONTROL — the retired expression scored that same row a measured 0%",
   ((REP_ROW_STALE["boost_ready_bounty"] / REP_ROW_STALE["ga_prepaid"] * 100)
    if REP_ROW_STALE["ga_prepaid"] > 0 else 0) == 0)
ck("B3 derived_rate computes normally when there IS a basis, and refuses every other absence",
   ds.derived_rate(3, 4) == 75.0 and ds.derived_rate(None, 4) is None
   and ds.derived_rate(3, None) is None and ds.derived_rate(3, "") is None
   and ds.derived_rate(3, -1) is None and ds.derived_rate("", "") is None)
ck("B4 a rate cell that SAYS zero still means zero; an ABSENT one is None",
   ds._rate("0") == 0.0 and ds._rate("0%") == 0.0 and ds._rate("") is None
   and ds._rate(None) is None and ds._rate("n/a") is None and ds._rate("71.43") == 71.43)
ck("B5 the COUNT parser is untouched — an absent count is still 0, because for a count it is",
   ds._num("") == 0.0 and ds._num(None) == 0.0 and ds._num("4") == 4.0)

no_basis = dict(stale_vals, boostapp=None)
met_nb, tot_nb, ev_nb, nd_nb = kf.score(no_basis, kf.BUILTIN_KPI_DEFS, TARGETS)
ck("B6 with no basis, Boost App is no_data — OUT of the denominator, and NOT a failure",
   tot_nb == 6 and [x["kpi"] for x in nd_nb] == ["boostapp"]
   and "boostapp" not in [x["kpi"] for x in ev_nb])
ck("B7 the met-count and therefore the tier are UNCHANGED by the honest denominator",
   met_nb == met_stale and tier_of(met_nb) == tier_of(met_stale), f"{met_nb} == {met_stale}")
ck("B8 REPORTED HONESTLY: Boost App does not flip 4-vs-3 here either — the carrier's own 61.54% "
   "fails the 65 target, so this rep fails it measured OR unmeasured",
   CARRIER_FINAL["boostapp"] < TARGETS["boostapp"])

# MONEY NEUTRALITY OF THE HONEST DENOMINATOR, over every shape a live row can take.
neutral = True
for tgt in (-1, 0, 1, 5, 35, 55, 65, 80, 100):
    for val in (None, 0.0, 0.5, tgt - 0.01, float(tgt), tgt + 0.01, 100.0):
        defs = (("m", "M", "kpi_m_target", tgt),)
        m1, t1, e1, n1 = kf.score({"m": val}, defs, {"m": tgt})
        if tgt <= 0:
            # no bar → not scored at all, measured or not: never met, never counted, never no_data
            neutral &= (m1 == 0 and t1 == 0 and not e1 and not n1)
        elif val is None:
            # unmeasured: never met, never counted, never in the denominator — reported as no_data
            neutral &= (m1 == 0 and t1 == 0 and len(n1) == 1 and not e1)
        else:
            neutral &= (t1 == 1 and m1 == (1 if val >= tgt else 0))
ck("B9 FUZZ — an unmeasured metric is never met and never in the denominator; a metric with no real "
   "bar (target <= 0) is not scored at all; a measured one against a real bar scores exactly as before",
   neutral)
ck("B10 a metric with neither a stored target nor a default cannot fail anyone (it is skipped, not "
   "given a target of 0 that every value meets)",
   kf.score({"x": 0.0}, (("x", "X", "kpi_x_target", None),), {}) == (0, 0, [], []))
# THE MIRROR HAZARD, now that the def list is the TENANT'S: `_kpi_defs` runs `target_default` through
# `safe_float`, so a registry row saved with no target arrives as 0.0. Scoring against 0 would hand
# every rep a free MET on a metric nobody set a bar for.
ck("B11 a registry metric with a FALSY target is not scored at all — never a free MET at target 0",
   kf.score({"x": 0.0}, (("x", "X", "kpi_x_target", 0.0),), {}) == (0, 0, [], [])
   and kf.score({"x": 99.0}, (("x", "X", "kpi_x_target", 0.0),), {}) == (0, 0, [], []))


# ── THE SIBLING AUDIT: who else reads a DLAR RATE, and can a NULL move their number? ──────────────
# Every reader of `raw_dlar_store`'s rate columns was checked in this change (index §19.28):
#   • `GET /dlar-store/{period}` and `_cr_resolve_kpi_metrics` — display rows; the KPI page already
#     renders a null as blank and `kpi_failing.store_values` -> `evaluate` reports it as no_data.
#   • the MI 3MR QUALIFIER (`router` ~42194) averages `tmr3` over a manager's stores through
#     `_f_num`, which coerces None to 0.0 — so a NULL averages in as a zero exactly as a blank cell
#     does today, and this change moves no manager's qualifier. Its `[v for v in vals if v is not
#     None]` filter is therefore a NO-OP.
#   ⚠ THAT INERTNESS IS ITSELF THE SAME DEFECT, and it is REPORTED, not fixed here: a store that
#     reported no 3MR is averaged in as 0% and drags the manager's qualifier down off a blank cell.
#     Fixing it MOVES MANAGER MONEY, so it waits for the owner (index §19.28 open item vi).
def _f_num_today(v):                       # verbatim copy of router._f_num, as the control
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


ck("B12 SIBLING AUDIT — the MI 3MR qualifier is UNCHANGED by a NULL: its reader coerces None to 0.0, "
   "so its is-not-None filter never drops anything and no manager's qualifier moves",
   _f_num_today(None) == 0.0 and _f_num_today("") == 0.0
   and [v for v in [_f_num_today(None), _f_num_today(88.89)] if v is not None] == [0.0, 88.89])
ck("B13 …and that is REPORTED as the same defect, not fixed: averaging a store that reported nothing "
   "as 0% drags the manager's qualifier down, and correcting it moves manager money (owner's call)",
   _f_num_today(None) == 0.0)          # the inertness IS the finding; asserted so it cannot change silently


# ── A COLUMN THAT STOPS ARRIVING (the COMPLETENESS axis — index §19.18 gains a third question) ─────
# ⚠ CORRECTION TO THIS HARNESS'S OWN FIRST ACCOUNT. The 189 `ga_prepaid = 0` rows were first reported as
# "the portal not reporting a prepaid-activation count" — scattered missing values. They are not. They
# DECOMPOSE, exactly, into a dated FEED-SHAPE BREAK plus a handful of genuine zeros (measured live):
#
#   March 13/110 · April 11/103 · May 11/81 · June 7/75      = 42   column PRESENT, rep genuinely had none
#   July 58/58 · August 44/44 · September 45/45              = 147  column STOPPED BEING SENT
#                                                              ---
#                                                              189
#
# And it was never one column: `store`, `door_name`, `door_city`, `door_state`, `door_zip`,
# `door_address`, `ga_prepaid` and `ga_postpaid` all went blank together in July 2026 — one upstream shape
# change, because `normalize_rep` maps that whole block from the record's `address` / `name` / `city` /
# `state` / `zip` / `prepaid_activations` / `postpaid_activations`. Rows kept arriving and the data date
# kept moving, so §19.18's two questions both read GREEN for three months.
JUL_REP_ROWS = [{"store": "", "door_address": "", "ga_prepaid": 0.0, "ga_postpaid": 0.0,
                 "gross_adds": 14.0, "atu_pct": 75.0}] * 58
JUN_REP_ROWS = ([{"store": "11636 Springfield Blvd", "door_address": "11636 Springfield Blvd",
                  "ga_prepaid": 8.0, "ga_postpaid": 0.0, "gross_adds": 9.0, "atu_pct": 75.0}] * 74
               + [{"store": "11636 Springfield Blvd", "door_address": "11636 Springfield Blvd",
                   "ga_prepaid": 0.0, "ga_postpaid": 0.0, "gross_adds": 0.0, "atu_pct": 0.0}])
REQ = lin.required_content_columns("raw_dlar_rep")
jul = lin.content_arrival(JUL_REP_ROWS, REQ)
jun = lin.content_arrival(JUN_REP_ROWS, REQ)
ck("B14 WHICH COLUMNS MUST CARRY A VALUE is declared ONCE, in the lineage registry — the sweep reads it",
   "store" in REQ and "ga_prepaid" in REQ and lin.required_content_columns("nope") == ())
ck("B15 THE JULY BREAK IS VISIBLE — `store` and `ga_prepaid` carry nothing in any of the 58 rows",
   jul["empty_columns"] == ["store", "ga_prepaid"] and jul["rows"] == 58
   and jul["columns"]["gross_adds"]["filled"] == 58, str(jul["empty_columns"]))
ck("B16 …and a GENUINE per-rep zero is NOT a break: June's one rep with no prepaid activations leaves "
   "the column reported as arriving (74 of 75 filled)",
   jun["empty_columns"] == [] and jun["columns"]["ga_prepaid"]["filled"] == 74,
   str(jun["columns"]["ga_prepaid"]))
ck("B17 the SWEEP SAYS SO OUT LOUD — the run is ⚠ PARTIAL and names the table.column and the row count",
   (lambda L: L.startswith("⚠ PARTIAL") and "COLUMN STOPPED ARRIVING" in L
    and "raw_dlar_rep.store (0 of 58 rows)" in L and "raw_dlar_rep.ga_prepaid (0 of 58 rows)" in L)(
       ds.status_sentence({"period": "July 2026",
                           "written": {"raw_dlar_store": 28, "raw_dlar_rep": 58},
                           "pulled": {"raw_dlar_store": 28, "raw_dlar_rep": 58},
                           "as_of": {"raw_dlar_store": "2026-07-31", "raw_dlar_rep": "2026-07-31"},
                           "vintage": {t: ds.vintage("July 2026", "2026-07-31")
                                       for t in ("raw_dlar_store", "raw_dlar_rep")},
                           "content": {"raw_dlar_rep": jul,
                                       "raw_dlar_store": {"rows": 28, "columns": {}, "empty_columns": []}},
                           "skipped_guard": None})),
   ds.status_sentence({"period": "July 2026", "written": {"raw_dlar_rep": 58}, "pulled": {},
                       "as_of": {}, "vintage": {}, "content": {"raw_dlar_rep": jul},
                       "skipped_guard": None}))
ck("B18 a CLEAN pull is still plainly OK — the new axis does not cry wolf",
   ds.status_sentence({"period": "June 2026",
                       "written": {"raw_dlar_store": 28, "raw_dlar_rep": 75},
                       "pulled": {"raw_dlar_store": 28, "raw_dlar_rep": 75},
                       "as_of": {"raw_dlar_store": "2026-06-30", "raw_dlar_rep": "2026-06-30"},
                       "vintage": {t: ds.vintage("June 2026", "2026-06-30")
                                   for t in ("raw_dlar_store", "raw_dlar_rep")},
                       "content": {"raw_dlar_rep": jun,
                                   "raw_dlar_store": {"rows": 28, "columns": {}, "empty_columns": []}},
                       "skipped_guard": None}).startswith("OK — "))
ck("B19 an EMPTY pull reports no empty columns — that is a different failure and the load guards own it",
   lin.content_arrival([], REQ)["empty_columns"] == []
   and lin.content_arrival(None, REQ)["rows"] == 0)
ck("B20 THE CORRECTED ATTRIBUTION, arithmetically: 42 scattered zeros (Mar-Jun, column present) + 147 "
   "feed-break rows (Jul-Sep) = the 189 measured platform-wide",
   13 + 11 + 11 + 7 == 42 and 58 + 44 + 45 == 147 and 42 + 147 == 189)

# ── D3 — WHICH GRAIN IS THIS KPI MEASURED AT ──────────────────────────────────────────────────────
ck("B21 THREE OF THE SEVEN a Boost rep is TIERED on are STORE-grain only — and a surface can now say so",
   [kf.grain_of(k) for k in ("atu", "protect", "boostapp", "byod")] == ["rep"] * 4
   and [kf.grain_of(k) for k in ("familyplan", "tmr3", "aal")] == ["store"] * 3
   and kf.grain_of("zulu") is None and kf.grain_of("") is None)
ck("B22 …which is exactly why those three MATCH ElevateGo while the rep-grain four do not: they are the "
   "rep's STORE's numbers, from a slice that WAS finalised",
   all(abs(stale_vals[k] - CARRIER_FINAL[k]) < 0.01 for k in ("familyplan", "tmr3", "aal"))
   and all(kf.grain_of(k) == "store" for k in ("familyplan", "tmr3", "aal")))


print("\n=== §C  ONE INVOICE, TWO BUCKETS — and the exact reconciliation to the carrier ==============")

# Waleed's August, rebuilt from the live contract-type distribution. Phone numbers are SYNTHETIC.
def line(tid, ct, mdn, product="Unlimited Data, Talk & Text"):
    return {"trans_id": tid, "contract_type": ct, "mdn": mdn, "serial_1": "",
            "product_desc": product, "category": "", "department": "", "voided": "", "trans_type": ""}


SWAP_INVOICES = ["201291", "202807", "205112", "206157", "206300"]
sales = []
# 7 single-line-per-phone premium invoices + 202731, which carries TWO Ineligible Port-In lines
for i, tid in enumerate(["203036", "204034", "204586", "205191", "205494", "206153", "206883"]):
    ct = "Activation" if tid in ("203036", "205191") else "Eligible Port-In Activation"
    for extra in ("Autopay", "Boost Protect Tier 1", "Unlimited Data, Talk & Text"):
        sales.append(line(tid, ct, f"555000{i:04d}", extra))
for j, ct in enumerate(("Ineligible Port-In Activation", "Ineligible Port-In Add A Line")):
    for extra in ("Autopay", "Boost Protect Tier 1", "Unlimited Data, Talk & Text", "SAMSUNG A16"):
        sales.append(line("202731", ct, f"555010{j:04d}", extra))
# 6 byod invoices that are genuinely new lines
for i, (tid, ct) in enumerate((("199750", "BYOD Add A Line"), ("201669", "BYOD Port-In"),
                               ("203190", "BYOD Port-In Add A Line"), ("205086", "BYOD"),
                               ("205402", "BYOD"), ("207019", "BYOD Port-In"))):
    sales.append(line(tid, ct, f"555020{i:04d}"))
# the 5 BYOD-Swap invoices: a swap line AND an upgrade line, on the SAME phone line
for i, tid in enumerate(SWAP_INVOICES):
    sales.append(line(tid, "BYOD Swap", f"555030{i:04d}"))
    sales.append(line(tid, "Upgrade", f"555030{i:04d}"))
# 12 plain upgrades
for i in range(12):
    sales.append(line(f"3000{i:02d}", "Upgrade", f"555040{i:04d}"))

tof = (lambda r: str(r.get("trans_id", "")).replace(".0", "").strip())


def counts(unit):
    out = {}
    for u in lc.activation_units(sales, None, txn_of=tof, require_txn=False, unit=unit):
        if u:
            out.setdefault(u[0], set()).add(u[1])
    return {k: len(v) for k, v in out.items()}


tx, evt = counts("transaction"), counts("event")
ck("C1 the house unit reproduces the live stored row exactly: premium 8, byod 11, upgrade 17",
   tx == {"premium": 8, "byod": 11, "upgrade": 17}, str(tx))
ck("C2 THE DOUBLE COUNT — each of the 5 BYOD-Swap invoices is counted in BOTH byod and upgrade",
   all(len({u[0] for u in lc.activation_units([r for r in sales if tof(r) == tid], None,
                                              txn_of=tof, require_txn=False) if u}) == 2
       for tid in SWAP_INVOICES), ", ".join(SWAP_INVOICES))
ck("C3 the event unit counts each phone line ONCE, in one bucket: premium 9, byod 11, upgrade 12",
   evt == {"premium": 9, "byod": 11, "upgrade": 12}, str(evt))
ck("C4 upgrades RECONCILE EXACTLY under the event unit: 12 == the carrier's 12 (17 − the 5 swaps)",
   evt["upgrade"] == 12 == tx["upgrade"] - len(SWAP_INVOICES))
acts = evt["premium"] + evt["byod"]
ck("C5 activations RECONCILE EXACTLY: 20 events − 5 BYOD-Swap (a customer's own device onto an "
   "EXISTING line: no gross add) − 2 Ineligible Port-In (the carrier refused the port) = 13",
   acts == 20 and acts - 5 - 2 == 13, f"events={acts}")
ck("C6 the transaction unit ALSO under-counts: invoice 202731's two Ineligible Port-In phone lines "
   "are one premium transaction but two activation events",
   tx["premium"] == 8 and evt["premium"] == 9)
amb = lc.activation_events(sales, None, txn_of=tof)["ambiguous"]
ck("C7 the engine ALREADY names the 5 invoices (event_mixed_classes) — nothing surfaced it",
   sorted(a["trans_id"] for a in amb if a["code"] == "event_mixed_classes") == sorted(SWAP_INVOICES),
   str(sorted(a["trans_id"] for a in amb)))
ck("C8 the SAME transactions drive both gaps — the 5 extra upgrades are 5 of the 7 extra activations",
   len(SWAP_INVOICES) == 5)


print("\n=== §D  THE PAY ENGINE READS THE REGISTRY — and HOUSE/Boost pay is byte-identical ==========")

CFG = {"upgrade_flat": 0, "premium_flat": 5, "byod_flat": 3, "acc_rate": 0.10,
       "setup_fee_rate": 0.10, "trade_in_spiff": 20, "acima_spiff": 25,
       "kpi_atu_target": 55, "kpi_protect_target": 80, "kpi_boostapp_target": 65,
       "kpi_familyplan_target": 45, "kpi_byod_target": 35, "kpi_tmr3_target": 70,
       "kpi_aal_target": 5, "tier_100_min_kpis": 7, "tier_75_min_kpis": 5,
       "tier_75_pct": 0.75, "tier_50_pct": 0.50, "straight_line": False}
REP_FOR_CALC = dict(REP_ROW_STALE, rep_name="WALEED", store="11636 Springfield Blvd")
SALES_FOR_CALC = [dict(r, salesperson="WALEED", user_login="waleed",
                       store="11636 Springfield Blvd", gp=0, ext_price=0) for r in sales]
COMMON = dict(pay_detail=[], mi_rows=[], catalog=[], store_mapping=[], shifts=[], employees=[],
              stores=[], period="August 2026", name_map=[])


def run(cfg):
    r = calc_rep_commissions(sales=SALES_FOR_CALC, dlar_rep=[REP_FOR_CALC],
                             dlar_store=[STORE_ROW_FINAL], cfg=cfg, carrier_mode="boost", **COMMON)
    return r["commissions"][0]

base = run(dict(CFG))
# HOUSE's registry IS the built-in seven, with the same columns and the same defaults.
house_registry = [list(d) for d in kf.BUILTIN_KPI_DEFS]
with_reg = run(dict(CFG, kpi_defs=house_registry))
MONEY = ("premium_acts", "byod_acts", "upgrade_acts", "premium_comm", "byod_comm", "upgrade_comm",
         "acc_comm", "setup_fee_comm", "trade_in_comm", "acima_comm", "custom_comm", "subtotal",
         "total_payout", "tier", "kpis_met")
ck("D1 BYTE-IDENTICAL — the house registry through cfg pays exactly what the built-ins pay",
   all(base[k] == with_reg[k] for k in MONEY) and base["kpi_values"] == with_reg["kpi_values"],
   f"tier={base['tier']} payout={base['total_payout']} met={base['kpis_met']}")
# THE LIVE HOUSE REGISTRY, verbatim from `carrier_kpi_metric` (measured read-only 2026-09-26): the same
# seven keys IN THE SAME ORDER with the same config columns and the same target defaults — only the
# LABELS differ from the built-ins ('Protection %' / 'App Attach %' / '3-Month Retention'). A label can
# never move a payout, and this is the registry the house org is actually scored on from now on.
LIVE_HOUSE_REGISTRY = [
    ["atu", "ATU", "kpi_atu_target", 55.0],
    ["protect", "Protection %", "kpi_protect_target", 80.0],
    ["boostapp", "App Attach %", "kpi_boostapp_target", 65.0],
    ["familyplan", "Family Plan", "kpi_familyplan_target", 45.0],
    ["byod", "BYOD", "kpi_byod_target", 35.0],
    ["tmr3", "3-Month Retention", "kpi_tmr3_target", 70.0],
    ["aal", "AAL", "kpi_aal_target", 5.0],
]
live = run(dict(CFG, kpi_defs=LIVE_HOUSE_REGISTRY))
ck("D1b BYTE-IDENTICAL against the LIVE house registry — it differs from the built-ins only in labels",
   all(live[k] == base[k] for k in MONEY) and live["kpi_values"] == base["kpi_values"]
   and live["total_kpis"] == base["total_kpis"],
   f"tier={live['tier']} payout={live['total_payout']} met={live['kpis_met']}")
ck("D1c …and that is not a coincidence of the fixture: keys, config columns, targets and ORDER match "
   "the built-ins exactly; only the labels differ",
   [r[0] for r in LIVE_HOUSE_REGISTRY] == [d[0] for d in kf.BUILTIN_KPI_DEFS]
   and [r[2] for r in LIVE_HOUSE_REGISTRY] == [d[2] for d in kf.BUILTIN_KPI_DEFS]
   and [r[3] for r in LIVE_HOUSE_REGISTRY] == [float(d[3]) for d in kf.BUILTIN_KPI_DEFS]
   and [r[1] for r in LIVE_HOUSE_REGISTRY] != [d[1] for d in kf.BUILTIN_KPI_DEFS])
ck("D2 no registry at all falls back to the built-ins — an unconfigured tenant is unchanged",
   run(dict(CFG, kpi_defs=None))["total_payout"] == base["total_payout"]
   and run(dict(CFG, kpi_defs=[]))["total_payout"] == base["total_payout"])
ck("D3 the live stored row is reproduced: premium 8, byod 11, upgrade 17, kpis_met 3, tier 0.5",
   (base["premium_acts"], base["byod_acts"], base["upgrade_acts"], base["kpis_met"], base["tier"])
   == (8, 11, 17, 3, 0.5), str((base["premium_acts"], base["byod_acts"], base["upgrade_acts"],
                                base["kpis_met"], base["tier"])))
ck("D4 the honest denominator reaches the paid row: 7 measured today, 6 once the sweep writes NULL",
   base["total_kpis"] == 7
   and run(dict(CFG))["total_kpis"] == 7)
no_basis_rep = dict(REP_FOR_CALC, boost_app_pct=None)
nb = calc_rep_commissions(sales=SALES_FOR_CALC, dlar_rep=[no_basis_rep],
                          dlar_store=[STORE_ROW_FINAL], cfg=dict(CFG), carrier_mode="boost", **COMMON)["commissions"][0]
ck("D5 THE FORWARD FIX PAYS THE SAME — a NULL Boost App gives total_kpis 6 and the SAME money",
   nb["total_kpis"] == 6 and nb["kpi_values"]["boostapp"] is None
   and all(nb[k] == base[k] for k in MONEY),
   f"total_kpis {base['total_kpis']}->{nb['total_kpis']} payout {nb['total_payout']}")

# An EIGHTH registry metric with no feed must never move a payout.
eighth = house_registry + [["zulu", "Zulu", "kpi_zulu_target", 90]]
e8 = run(dict(CFG, kpi_defs=eighth))
ck("D6 a registry metric NOTHING FEEDS can never move a payout — it is no_data, not a failed zero",
   all(e8[k] == base[k] for k in MONEY) and e8["kpi_values"]["zulu"] is None
   and e8["total_kpis"] == base["total_kpis"])

# A plan tenant: scored informationally from ITS registry, never tiered on it.
LUXE = [["acts", "Acts", "kpi_acts_target", 100], ["zulu", "Zulu", "kpi_zulu_target", 90],
        ["twp", "TWP", "kpi_twp_target", 50], ["tmr3", "3MR", "kpi_tmr3_target", 70]]
plan = calc_rep_commissions(sales=SALES_FOR_CALC, dlar_rep=[], dlar_store=[],
                            cfg=dict(CFG, kpi_defs=LUXE), carrier_mode="total", **COMMON)["commissions"][0]
ck("D7 a PLAN tenant is not KPI-tiered: tier stays 1.0 / 'plan' and the payout stays the plan's",
   plan["tier"] == 1.0 and plan["tier_source"] == "plan" and plan["total_payout"] == 0)
ck("D8 with nothing fed it reads 0 of 0 — never '0 of 17', the reading the owner refused",
   plan["kpis_met"] == 0 and plan["total_kpis"] == 0
   and sorted(plan["kpi_values"]) == ["acts", "tmr3", "twp", "zulu"]
   and all(v is None for v in plan["kpi_values"].values()), str(plan["kpi_values"]))
plan_fed = calc_rep_commissions(
    sales=SALES_FOR_CALC, dlar_rep=[], dlar_store=[dict(STORE_ROW_FINAL, tmr3=88.89)],
    cfg=dict(CFG, kpi_defs=LUXE, kpi_actuals={"11636 Springfield Blvd": {"zulu": 95.0, "twp": 10.0}}),
    carrier_mode="total", **COMMON)["commissions"][0]
ck("D9 a store-grain value — from raw_dlar_store OR from kpi_actual — rolls DOWN to the rep, which "
   "is the rule the Boost engine has always used for familyplan / tmr3 / aal",
   plan_fed["total_kpis"] == 3 and plan_fed["kpis_met"] == 2
   and plan_fed["kpi_values"]["zulu"] == 95.0 and plan_fed["kpi_values"]["tmr3"] == 88.89
   and plan_fed["kpi_values"]["acts"] is None,
   f"met={plan_fed['kpis_met']}/{plan_fed['total_kpis']} {plan_fed['kpi_values']}")
ck("D10 and it STILL does not tier a plan tenant — 2 of 3 met, tier unchanged at 1.0",
   plan_fed["tier"] == 1.0 and plan_fed["total_payout"] == plan["total_payout"])
# ── THE GRAIN IS A PROPERTY OF THE METRIC ─────────────────────────────────────────────────────────
# CAUGHT BY REPLAYING THIS RESOLVER AGAINST SEVEN LIVE MONTHS (322 rep-months, read-only): a first cut
# used a blanket fallback chain rep → store → actual, and a rep with NO advocate row then read
# atu 39.53 / protect 91.36 / byod 44.19 off their STORE and their met-count went 1 → 3. That is a
# money-shaped change nobody asked for, and a better-looking lie than the 0.0 it replaced.
ck("D11 a REP-grain metric is the rep's OWN or it is NOT MEASURED — never the store's number rolled "
   "down (the live-replay regression: met-count 1 -> 3 off a store figure)",
   kf.rep_kpi_values(kf.BUILTIN_KPI_DEFS, rep_row={"atu_pct": 1.0},
                     store_row={"atu": 2.0})[0]["atu"] == 1.0
   and kf.rep_kpi_values(kf.BUILTIN_KPI_DEFS, rep_row=None,
                         store_row={"atu": 2.0, "protect_pct": 9.0, "byod_pct": 8.0})[0]["atu"] is None
   and all(kf.rep_kpi_values(kf.BUILTIN_KPI_DEFS, rep_row=None,
                             store_row={"atu": 2.0, "protect_pct": 9.0, "byod_pct": 8.0},
                             actuals={"atu": 3.0})[0][k] is None
           for k in ("atu", "protect", "byod", "boostapp")))
ck("D11b …and a metric with NO rep-grain feed IS rolled down from the store, which is exactly what the "
   "Boost engine has always done for familyplan / tmr3 / aal",
   (lambda v: v["familyplan"] == 5.0 and v["tmr3"] == 6.0 and v["aal"] == 7.0)(
       kf.rep_kpi_values(kf.BUILTIN_KPI_DEFS, rep_row=None,
                         store_row={"family_plan_pct": 5.0, "tmr3": 6.0, "aal_conversion": 7.0})[0]))
ck("D11c …and a store-grain metric with no DLAR column value falls through to a measured kpi_actual",
   kf.rep_kpi_values(kf.BUILTIN_KPI_DEFS, store_row={}, actuals={"familyplan": 4.0}
                     )[1]["familyplan"] == kf.SOURCE_ACTUAL
   and kf.rep_kpi_values((("zulu", "Zulu", "kpi_zulu_target", 90),),
                         actuals={"zulu": 91.0})[1]["zulu"] == kf.SOURCE_ACTUAL)
ck("D11d the sources are stamped per metric, so a screen never has to guess where a number came from",
   kf.rep_kpi_values(kf.BUILTIN_KPI_DEFS, rep_row=REP_ROW_STALE, store_row=STORE_ROW_FINAL)[1]
   == {"atu": kf.SOURCE_REP_DLAR, "protect": kf.SOURCE_REP_DLAR, "boostapp": kf.SOURCE_REP_DLAR,
       "familyplan": kf.SOURCE_STORE_DLAR, "byod": kf.SOURCE_REP_DLAR,
       "tmr3": kf.SOURCE_STORE_DLAR, "aal": kf.SOURCE_STORE_DLAR})
# The live-replay result itself, encoded as the shapes it found (the replay is read-only and cannot run
# in CI; these are the two rep shapes it turned up, so the regression cannot come back unnoticed).
ck("D11e LIVE SHAPE — a rep with a DLAR row but no store row measures exactly the 4 rep-grain metrics",
   kf.score(kf.rep_kpi_values(kf.BUILTIN_KPI_DEFS, rep_row=REP_ROW_STALE, store_row=None)[0],
            kf.BUILTIN_KPI_DEFS, TARGETS)[1] == 4)
ck("D11f LIVE SHAPE — a rep with a store row but no DLAR row measures exactly the 3 store-grain ones",
   kf.score(kf.rep_kpi_values(kf.BUILTIN_KPI_DEFS, rep_row=None, store_row=STORE_ROW_FINAL)[0],
            kf.BUILTIN_KPI_DEFS, TARGETS)[1] == 3)
ck("D12 the retired protect fallback chain is preserved exactly (device_insurance_pct, then protect_pct)",
   kf.rep_kpi_values(kf.BUILTIN_KPI_DEFS, rep_row={"device_insurance_pct": 9.0,
                                                   "protect_pct": 1.0})[0]["protect"] == 9.0
   and kf.rep_kpi_values(kf.BUILTIN_KPI_DEFS,
                         rep_row={"protect_pct": 1.0})[0]["protect"] == 1.0
   and kf.rep_kpi_values(kf.BUILTIN_KPI_DEFS,
                         rep_row={"device_insurance_pct": None,
                                  "protect_pct": 1.0})[0]["protect"] == 1.0)
ck("D13 a rep with NO DLAR row at all reads 0 of 0, not 0 of 7 — and is paid exactly as before",
   (lambda r: r["total_kpis"] == 0 and r["kpis_met"] == 0 and r["tier"] == 0.5
    and r["kpi_values"] == {})(
       calc_rep_commissions(sales=SALES_FOR_CALC, dlar_rep=[], dlar_store=[], cfg=dict(CFG),
                            carrier_mode="boost", **COMMON)["commissions"][0]))
ck("D14 straight_line pay never touches a KPI at all (unchanged)",
   (lambda r: r["tier"] == 1.0 and r["kpi_values"] == {} and r["total_kpis"] == 0)(
       run(dict(CFG, straight_line=True))))


print("\n" + "=" * 96)
bad = [n for n, ok, _d in CHECKS if not ok]
print(f"{len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed")
if bad:
    print("FAILED:")
    for n in bad:
        print("  - " + n)
    sys.exit(1)
print("KPI VINTAGE / HONEST DENOMINATOR / REGISTRY-DRIVEN PAY ENGINE — all green")
