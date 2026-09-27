"""PROOF — THE CARRIER FEED vs THE STORE'S TRANSACTIONS: every difference visible and ATTRIBUTED.
stdlib only, DB-free, no framework. Run: python3 backend/harness_dlar_vs_platform.py

OWNER 2026-09-27, verbatim: *"the comparison of the dlar report for numbers and the ones we are
reporting in thr platform , they should be the same - the source of truth is the transaction done in thr
store so all reporting should have the same data , create a report for the. Difference of thr incoming
data from dlar and whatever you are using to assess the difference and show it it in management
dashboard"*.

WHAT THIS PROVES (the interesting cases, not the easy ones)
 A. ABSENCE IS NOT ZERO, ON BOTH SIDES — `reported` / `measured_zero` / `not_reported`, the §31
    vocabulary reused rather than a fourth spelling; a column that stopped arriving never produces a
    delta of 0 nor a 100% variance, and a metric the platform cannot speak to says so.
 B. A STALE SNAPSHOT MAKES A DIFFERENCE UNDECIDABLE — it is NOT attributed to staleness and NOT called
    a finding. Measured why: the reference rep's stored August slice says 4 activations where the
    carrier's finalised August says 13, so a partial snapshot does not grow in proportion to the days
    it is missing; any rule that scaled it by day coverage would invent a counting bug, and any rule
    that let staleness swallow the residual would hide one.
 C. A COUNTING-DEFINITION difference is attributed EXACTLY, by named unit kind, per METRIC.
 D. THE WALEED AUGUST RECONCILIATION END TO END, from the anonymised fixture: against a COMPLETE feed
    slice the activation count reconciles exactly through the ineligible port-in, and the +5 on
    UPGRADES is attributed exactly to the five BYOD-Swap invoices — which is index §19.28 (3)
    reproduced from the outside, by a report that shares the platform's one activation count.
 E. A DIFFERENCE NOTHING EXPLAINS is `unattributed` and reaches the headline. That is the output.
 F. GRAIN — the feed publishes per rep per DOOR; a rep with several feed rows is undecidable, never
    silently compared against one of them.
 G. The report books nothing, and its summary never nets the two sides.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import dlar_vs_platform as dvp   # noqa: E402  (pure)

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:300]))


def metric(row, key):
    return next(m for m in row["metrics"] if m["metric"] == key)


def causes(row, key):
    return [c["cause"] for c in metric(row, key)["causes"]]


V_SHORT = {"as_of": "2026-08-24", "period_last_day": "2026-08-31", "complete": False,
           "days_short": 7, "basis": "write_date", "upper_bound": True, "rows": 44}
V_DONE = {"as_of": "2026-08-31", "period_last_day": "2026-08-31", "complete": True,
          "days_short": 0, "basis": "as_of_date", "upper_bound": False, "rows": 44}

print("\nA. absence is not zero, on both sides")
for raw, want in ((None, dvp.STATE_NOT_REPORTED), ("", dvp.STATE_NOT_REPORTED),
                  ("   ", dvp.STATE_NOT_REPORTED), (0, dvp.STATE_MEASURED_ZERO),
                  ("0", dvp.STATE_MEASURED_ZERO), (0.0, dvp.STATE_MEASURED_ZERO),
                  (13, dvp.STATE_REPORTED), ("13.5", dvp.STATE_REPORTED),
                  ("junk", dvp.STATE_NOT_REPORTED)):
    check("A1 %-8r → %s" % (raw, want), dvp.side(raw)[1] == want, dvp.side(raw))
check("A2 the three states are §31's vocabulary verbatim, not a fourth spelling",
      dvp.STATES == ("reported", "measured_zero", "not_reported"))
# `ga_prepaid` stopped arriving in July 2026 (index §19.28) — the feed side must read not_reported
r = dvp.entity_row(key="R", label="R", feed_row={"gross_adds": 10, "ga_prepaid": None},
                   platform={"new_activations": 10}, vintage=V_DONE)
check("A3 a column that stopped arriving is not_reported — no delta, no 100% variance",
      metric(r, "ga_prepaid")["feed_state"] == dvp.STATE_NOT_REPORTED
      and metric(r, "ga_prepaid")["delta"] is None
      and not metric(r, "ga_prepaid")["comparable"])
check("A4 a metric the PLATFORM cannot speak to says so (ATU has no transaction counterpart)",
      metric(r, "atu")["platform_state"] == dvp.STATE_NOT_REPORTED
      and dict((m[0], m[3]) for m in dvp.METRIC_REP)["atu"] is None)
check("A5 a rep with no transactions is not a rep with zero (platform=None → not_reported)",
      all(m["platform_state"] == dvp.STATE_NOT_REPORTED
          for m in dvp.compare({"gross_adds": 9}, None))
      and dvp.entity_row(key="x", label="x", feed_row={"gross_adds": 9},
                         platform=None, vintage=V_DONE)["compared"] == 0)
check("A6 a feed row that is genuinely 0 is measured_zero and IS compared",
      metric(dvp.entity_row(key="z", label="z", feed_row={"gross_adds": 0},
                            platform={"new_activations": 0}, vintage=V_DONE),
             "activations")["agree"] is True)
check("A7 the fallback column ORDER is honoured and the column used is named",
      metric(dvp.entity_row(key="p", label="p", feed_row={"protect_pct": 71.4},
                            platform={}, vintage=V_DONE), "protect")["feed_column"] == "protect_pct"
      and metric(dvp.entity_row(key="p", label="p",
                                feed_row={"device_insurance_pct": 70.0, "protect_pct": 71.4},
                                platform={}, vintage=V_DONE),
                 "protect")["feed_column"] == "device_insurance_pct")

print("\nB. a stale snapshot makes a difference UNDECIDABLE — not explained, not a finding")
stale = dvp.entity_row(key="W", label="W", store="S", feed_row={"gross_adds": 4, "upgrades": 3},
                       platform={"new_activations": 14, "upgrades": 17},
                       excluded_units={"activations": {"ineligible": 1}, "upgrades": {"swap": 5}},
                       vintage=V_SHORT)
check("B1 the vintage cause is present and says it is NOT decidable",
      dvp.CAUSE_VINTAGE in causes(stale, "activations")
      and not metric(stale, "activations")["decidable"])
check("B2 the row is NOT counted as unattributed (it is not a counting dispute)",
      stale["unattributed"] == 0 and stale["undecidable"] == 2, stale)
check("B3 nor is it called attributed/explained",
      metric(stale, "activations")["attributed"] is False)
check("B4 the as-of date, the days short and the basis travel with the cause",
      [c for c in metric(stale, "activations")["causes"]
       if c["cause"] == dvp.CAUSE_VINTAGE][0]["days_short"] == 7)
check("B5 the exact definition part is STILL attributed first, even on a stale row",
      dvp.CAUSE_DEFINITION in causes(stale, "activations")
      and causes(stale, "activations")[0] == dvp.CAUSE_DEFINITION)
check("B6 a COMPLETE slice makes the same residual a finding instead",
      not metric(dvp.entity_row(key="W", label="W", feed_row={"gross_adds": 4},
                                platform={"new_activations": 14}, vintage=V_DONE),
                 "activations")["attributed"])
check("B7 the vintage cause never appears when the slice is complete",
      dvp.CAUSE_VINTAGE not in causes(dvp.entity_row(key="W", label="W", feed_row={"gross_adds": 4},
                                                     platform={"new_activations": 14},
                                                     vintage=V_DONE), "activations"))
check("B8 an unknown vintage (None) is treated as complete-unknown → the residual is a finding, "
      "never hidden behind a vintage nobody measured",
      dvp.CAUSE_UNATTRIBUTED in causes(dvp.entity_row(key="W", label="W",
                                                      feed_row={"gross_adds": 4},
                                                      platform={"new_activations": 14}), "activations"))

print("\nC/D. the counting definition, attributed exactly — and the Waleed August reconciliation")
final = dvp.entity_row(key="W", label="Waleed", store="11636", grain=dvp.GRAIN_REP,
                       feed_row={"gross_adds": 13, "upgrades": 12, "boost_ready_bounty": 8,
                                 "boost_app_pct": 61.54},
                       platform={"new_activations": 14, "upgrades": 17, "boostapp_rate": 61.54},
                       excluded_units={"activations": {"ineligible": 1}},
                       vintage=V_DONE)
a = metric(final, "activations")
check("D1 activations 13 vs 14 reconciles EXACTLY through the ineligible port-in",
      a["delta"] == 1.0 and a["attributed"] and a["residual"] == 0.0
      and causes(final, "activations") == [dvp.CAUSE_DEFINITION], a)
check("D2 the cause names the KIND and the unit count, and says it is exact",
      a["causes"][0]["kind"] == "ineligible" and a["causes"][0]["units"] == 1.0
      and a["causes"][0]["exact"] is True)
u = metric(final, "upgrades")
check("D3 UPGRADES +5 is unexplained when no upgrade-side exclusion is offered — "
      "index §19.28 (3) reproduced from the outside",
      u["delta"] == 5.0 and not u["attributed"] and dvp.CAUSE_UNATTRIBUTED in causes(final, "upgrades"))
final2 = dvp.entity_row(key="W", label="Waleed", store="11636",
                        feed_row={"gross_adds": 13, "upgrades": 12},
                        platform={"new_activations": 14, "upgrades": 17},
                        excluded_units={"activations": {"ineligible": 1}, "upgrades": {"swap": 5}},
                        vintage=V_DONE)
check("D4 …and it is attributed EXACTLY to the five BYOD-Swap invoices once the upgrade side's own "
      "exclusion is supplied",
      metric(final2, "upgrades")["attributed"]
      and metric(final2, "upgrades")["residual"] == 0.0
      and metric(final2, "upgrades")["causes"][0]["units"] == 5.0)
check("D5 the whole rep then reconciles: 2 differences, both attributed, nothing unattributed",
      (final2["differing"], final2["unattributed"], final2["undecidable"]) == (2, 0, 0), final2)
check("C1 the exclusion map is PER METRIC — a swap explaining upgrades never also explains "
      "activations",
      metric(dvp.entity_row(key="q", label="q", feed_row={"gross_adds": 4, "upgrades": 12},
                            platform={"new_activations": 14, "upgrades": 17},
                            excluded_units={"upgrades": {"swap": 5}}, vintage=V_DONE),
             "activations")["causes"][0]["cause"] == dvp.CAUSE_UNATTRIBUTED)
check("C2 an exclusion can never explain MORE than the gap itself",
      metric(dvp.entity_row(key="q", label="q", feed_row={"gross_adds": 13},
                            platform={"new_activations": 14},
                            excluded_units={"activations": {"ineligible": 99}}, vintage=V_DONE),
             "activations")["causes"][0]["units"] == 1.0)
check("C3 a RATE is never attributed to a unit exclusion (a ratio's gap is not a unit gap)",
      not any(c["cause"] == dvp.CAUSE_DEFINITION
              for c in metric(dvp.entity_row(key="q", label="q", feed_row={"byod_pct": 50.0},
                                             platform={"byod_rate": 78.6},
                                             excluded_units={"byod_rate": {"swap": 5}},
                                             vintage=V_DONE), "byod_rate")["causes"]))
check("C4 a rate agrees within the float tolerance and disagrees beyond it",
      metric(dvp.entity_row(key="q", label="q", feed_row={"byod_pct": 61.54},
                            platform={"boostapp_rate": 0}, vintage=V_DONE), "byod_rate")["feed"] == 61.54
      and dvp.compare({"byod_pct": 50.0}, {"byod_rate": 50.04})[9]["agree"] is True
      and dvp.compare({"byod_pct": 50.0}, {"byod_rate": 50.5})[9]["agree"] is False)
check("C5 the platform side NEVER loses to the feed — the delta is platform − feed, so a positive "
      "delta always means the transactions counted more",
      metric(dvp.entity_row(key="q", label="q", feed_row={"gross_adds": 4},
                            platform={"new_activations": 14}, vintage=V_DONE),
             "activations")["delta"] == 10.0)
check("C6 a negative delta (the feed counted MORE) is never attributed to an exclusion",
      not metric(dvp.entity_row(key="q", label="q", feed_row={"gross_adds": 20},
                                platform={"new_activations": 14},
                                excluded_units={"activations": {"swap": 5}}, vintage=V_DONE),
                 "activations")["attributed"])

print("\nE. a difference nothing explains reaches the headline")
rows = [final2, stale,
        dvp.entity_row(key="A", label="A", feed_row={"gross_adds": 9},
                       platform={"new_activations": 12}, vintage=V_DONE),
        dvp.entity_row(key="B", label="B", feed_row={"gross_adds": 9},
                       platform={"new_activations": 9}, vintage=V_DONE)]
s = dvp.summarize(rows, period="August 2026", vintage={"grains": {}},
                  stopped_columns={"raw_dlar_rep.ga_prepaid", "raw_dlar_rep.store"},
                  feed_rows_per_entity={"A": 1, "SPLIT": 3})
check("E1 the unattributed cell reaches the headline", s["cells_unattributed"] == 1, s)
check("E2 the undecidable cells are counted SEPARATELY and never inflate it",
      s["cells_undecidable"] == 2 and s["entities_undecidable"] == 1, s)
check("E3 an agreeing entity is neither", s["entities_differing"] == 3, s)
check("E4 the per-metric roll-up sorts the unattributed first",
      s["by_metric"][0]["metric"] == "activations" and s["by_metric"][0]["unattributed"] == 1,
      s["by_metric"][:2])
check("E5 a column the feed stopped sending for EVERYBODY is one fact, not N findings",
      s["stopped_columns"] == ["raw_dlar_rep.ga_prepaid", "raw_dlar_rep.store"])
check("E6 an entity with SEVERAL feed rows (per door) is reported as such",
      s["entities_with_several_feed_rows"] == {"SPLIT": 3}, s["entities_with_several_feed_rows"])
check("E7 the state and cause notes ship with the payload (the page explains itself)",
      set(s["state_notes"]) == set(dvp.STATES) and set(s["cause_notes"]) == set(dvp.CAUSES))

print("\nF. grain — the feed is per rep per DOOR")
split = dvp.entity_row(key="M", label="M", feed_row={"gross_adds": 5},
                       platform={"new_activations": 23}, vintage=V_DONE,
                       grain_note="the feed holds 6 rows for this rep (one per door)")
check("F1 a several-door rep is UNDECIDABLE, never compared against one of its rows",
      dvp.CAUSE_GRAIN in causes(split, "activations")
      and not metric(split, "activations")["decidable"]
      and split["unattributed"] == 0)
check("F2 and the vintage cause wins when BOTH apply (a stale slice cannot be judged at all)",
      causes(dvp.entity_row(key="M", label="M", feed_row={"gross_adds": 5},
                            platform={"new_activations": 23}, vintage=V_SHORT,
                            grain_note="several doors"), "activations") == [dvp.CAUSE_VINTAGE])

print("\nG. the report books nothing, and never nets the two sides")
check("G1 books_to == []", dvp.summarize([])["books_to"] == [])
check("G2 the summary names the owner's source of truth",
      "transaction done in the store" in dvp.summarize([])["source_of_truth"])
check("G3 there is no key that sums the feed and the platform together",
      not any("total" in k and "delta" not in k for k in dvp.summarize(rows)
              if k not in ("cells_compared",)),
      [k for k in dvp.summarize(rows)])
check("G4 the store grain has its own metric declaration and no boostapp (there is no store-grain "
      "Ready App column)",
      "boostapp" not in [m[0] for m in dvp.METRIC_STORE])
check("G5 empty input never raises",
      dvp.summarize(None)["entities"] == 0 and dvp.compare(None, None) != [] and
      dvp.entity_row(key="", label="")["compared"] == 0)

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — every difference is visible, attributed to a counting definition, held as undecidable "
      "behind a stale slice, or reported as the finding it is.")
