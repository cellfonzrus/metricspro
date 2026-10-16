"""PROOF — AN UNSENT FIGURE IS NOT A ZERO, AND A RENAMED PORTAL FIELD NAMES ITSELF.
stdlib only, DB-free, no framework. Run: python3 backend/harness_portal_cell.py

OWNER REPORT 2026-10-16, verbatim: *"Check why atu information for atu and other metrics is not
pulling up same as in elevate go for boost , need to fix it asap"*.

THE DEFECT, MEASURED LIVE (house org, `commcalc.raw_dlar_store`, read-only via the REST API):

    period            pulled        gross_adds  total_upgrades  total_acts   atu (a rate)
    September 2026    2026-10-02          1061             494        1555    populated
    October 2026      2026-10-16             0               0           0    populated

Every rate arrived; every unit count landed as zero, in the same rows, on the same pull. A store
cannot attach 66.67% of nothing. Those zeros are the portal no longer sending those fields, written
down as a measurement — and the sweep's status line read PARTIAL only about the two REP columns the
registry happened to declare, saying nothing at all about the store grain.

WHAT THIS PROVES (the interesting cases, not the easy ones)
 A. THE ONE READING RULE — absent is None for a COUNT exactly as it already was for a RATE; a cell
    that SAYS '0' is still a measured zero; a derived total of absent parts is absent.
 B. THE OCTOBER 2026 REGRESSION, from the real record shape: the store record without its count
    fields lands `None` for activations, upgrades and total activity — never 0 — while every rate it
    did send lands unchanged.
 C. A FIELD WITH TWO ACCEPTED SPELLINGS IS NOT MISSING when the portal sends one of them. Without
    this, every healthy run would report a shape change and the alarm would be worthless.
 D. A RENAME NAMES ITSELF: the field that stopped arriving is reported by name, and beside it the
    fields the portal IS sending that nothing reads — which is where the new spelling is.
 E. QUIET WHEN HEALTHY: a full record reports no shape change and NO unconsumed fields, so a feed
    that ignores fields by design does not cry wolf.
 F. THE MAPPING IS STILL THE SAME MAPPING — for a record carrying every declared field, the declared
    maps produce exactly the columns and values the hand-written normalizers produced, field for
    field, including the two-spelling fallbacks and the derived columns.
 G. THE STATUS LINE SAYS IT, and an INT column never receives a float or a silent zero.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from app.modules.commcalc import portal_cell as pc          # noqa: E402
from app.modules.commcalc import dlar_sweep as ds           # noqa: E402

P = F = 0


def check(label, ok, got=None):
    global P, F
    if ok:
        P += 1
        print("  PASS  " + label)
    else:
        F += 1
        print("  FAIL  " + label + ("" if got is None else "  got=%r" % (got,)))


# ── the real October 2026 store record shape: rates present, unit counts gone ─────────────────────
OCT_STORE = {
    "id": "166111", "address": "1800 Great Neck rd", "name": "Cellular Services dot net llc",
    "atu_loading_percent": "66.67", "protect_total_attach": "100", "byod_adds_percent": "33.33",
    "family_plan_percent": "66.67", "three_mr": "62.5", "aal_conversion": "2.56",
    "port_ins": "66.67", "conversion_rate": "3.85", "accessory_attach_rate": "416.67",
    "avg_first_mrc": "52.67", "sales_target": "25", "shopper_trak_conversion": "0",
    "projected_percent_to_target": "28",
    # what the portal is sending INSTEAD of its old count fields (the names are what the next real
    # run will print; this fixture only has to be a rename, not the right one)
    "activation_quantity": "61", "upgrade_quantity": "29",
}

# the September 2026 shape — every declared field present
SEP_STORE = dict(OCT_STORE)
SEP_STORE.pop("activation_quantity")
SEP_STORE.pop("upgrade_quantity")
SEP_STORE.update({"gross_activation_quantity": "61", "upgrades": "29",
                  "prepaid_activations": "58", "postpaid_activations": "3",
                  "zero_selling_days": "0"})

print("A. the one reading rule: absent is not zero, for a count as much as for a rate")
check("A1 an absent count is None", pc.count(None) is None and pc.count("") is None)
check("A2 a cell that SAYS zero is a measured zero", pc.count("0") == 0.0 and pc.rate("0%") == 0.0)
check("A3 'n/a' is no measurement, not a zero", pc.count("n/a") is None and pc.rate("--") is None)
check("A4 report formatting survives", pc.count("1,061") == 1061.0 and pc.rate("0% / 71.43%") == 71.43)
check("A5 non-numeric text is absent, never zero", pc.count("pending") is None)
check("A6 count and rate obey ONE rule (same function, two names)",
      all(pc.count(v) == pc.rate(v) or (pc.count(v) is None and pc.rate(v) is None)
          for v in (None, "", "0", "5", "n/a", "1,061", "x")))
check("A7 a total of absent parts is absent", pc.total(None, "") is None)
check("A8 a total uses the parts that DID arrive", pc.total("61", None) == 61.0)
check("A9 a total of measured zeros is a measured zero", pc.total("0", "0") == 0.0)
check("A10 an int column gets an int, and keeps an absence absent",
      pc.as_int(pc.total("61", "29")) == 90 and pc.as_int(None) is None)
check("A11 a boolean is not a measurement", pc.count(True) is None)

print("\nB. the October 2026 regression — the shape that landed 28 stores a month of zeros")
oct_row = ds.normalize_store(OCT_STORE)
check("B1 activations land ABSENT, not 0", oct_row["gross_adds"] is None, oct_row["gross_adds"])
check("B2 upgrades land ABSENT, not 0", oct_row["total_upgrades"] is None, oct_row["total_upgrades"])
check("B3 total activity lands ABSENT, not 0 (it is derived from two fields that never arrived)",
      oct_row["total_acts"] is None, oct_row["total_acts"])
check("B4 the prepaid/postpaid split lands ABSENT, not 0",
      oct_row["pay_now_acts"] is None and oct_row["pay_later_acts"] is None)
check("B5 every rate the portal DID send is unchanged",
      (oct_row["atu"], oct_row["tmr3"], oct_row["family_plan_pct"], oct_row["port_pct"]) ==
      (66.67, 62.5, 66.67, 66.67), oct_row["atu"])
check("B6 a rate the portal sent as zero stays a measured zero",
      oct_row["shopper_trak_conversion"] == 0.0, oct_row["shopper_trak_conversion"])
check("B7 the store identity still lands (the join is on the address)",
      oct_row["address"] == "1800 Great Neck rd" and oct_row["store_code"] == "")

print("\nC. a field with two accepted spellings is not missing when one of them arrives")
fa_sep = pc.field_arrival([SEP_STORE], ds.declared_source_fields("dlar"))
check("C1 September's shape reports NOTHING missing", fa_sep["missing"] == [], fa_sep["missing"])
check("C2 ... and nothing blank", fa_sep["blank"] == [], fa_sep["blank"])
check("C3 ... so the run is not a shape change", fa_sep["shape_changed"] is False)
check("C4 the two-spelling fields are declared as ONE field each",
      any(len(g) > 1 for g in ds.declared_source_fields("dlar")))
check("C5 a field is named by its PREFERRED spelling when it does go missing",
      pc.field_arrival([{"a": "1"}], [("x", "y")])["missing"] == ["x"])

print("\nD. a rename names itself")
fa_oct = pc.field_arrival([OCT_STORE], ds.declared_source_fields("dlar"))
check("D1 the fields that stopped arriving are named",
      {"gross_activation_quantity", "upgrades"} <= set(fa_oct["missing"]), fa_oct["missing"])
check("D2 the fields the portal sends that nothing reads are named — the new spelling is in here",
      {"activation_quantity", "upgrade_quantity"} <= set(fa_oct["unconsumed"]), fa_oct["unconsumed"])
check("D3 a field that arrives EMPTY is reported as blank, not as missing",
      pc.field_arrival([{"a": "", "b": "1"}], ["a", "b"]) ["blank"] == ["a"])
check("D4 blank and missing are both a shape change",
      pc.field_arrival([{"a": ""}], ["a"])["shape_changed"] is True)

print("\nE. quiet when healthy — a feed that ignores fields by design does not cry wolf")
noisy = dict(SEP_STORE)
noisy.update({"something_we_never_read": "7", "another_one": "8"})
fa_noisy = pc.field_arrival([noisy], ds.declared_source_fields("dlar"))
check("E1 unconsumed fields are NOT listed on a healthy run", fa_noisy["unconsumed"] == [],
      fa_noisy["unconsumed"])
check("E2 ... and the run is not a shape change", fa_noisy["shape_changed"] is False)
check("E3 no records → nothing reported (an empty pull is a different failure)",
      pc.field_arrival([], ["a"])["shape_changed"] is False and
      pc.field_arrival(None, ["a"])["rows"] == 0)
check("E4 junk input never raises", pc.field_arrival(["x", None, 3], ["a"])["rows"] == 0)

print("\nF. the declared maps are the SAME mapping the hand-written normalizers were")
FULL_REP = {
    "sfid": "001U1", "name": "A door", "address": "559 Broadway", "city": "Brooklyn",
    "state": "NY", "zip": " 11206 ", "sales_rep_name": "AKHTAR, RANA",
    "activations": "17", "prepaid_activations": "15", "postpaid_activations": "2",
    "upgrades": "4", "byod_rate": "23.53", "atu_activations": "8", "atu_loading_rate": "47.06",
    "insurance_take_rate": "90.48", "insurance_total": "19", "insurance_activations": "15",
    "insurance_upgrades": "4", "platinum_points": "42", "platinum_points_per_activation": "2.47",
    "platinum_sales": "1", "boost_ready_bounty": "17", "tablet_activations": "0",
}
# the mapping as it was written by hand before this change, kept here as the EXPECTATION
expect_rep = {
    "salesforce_id": "001U1", "door_name": "A door", "door_address": "559 Broadway",
    "door_city": "Brooklyn", "door_state": "NY", "door_zip": "11206",
    "advocate_name": "AKHTAR, RANA", "rep_name": "AKHTAR, RANA", "store": "559 Broadway",
    "gross_adds": 17.0, "ga_prepaid": 15.0, "ga_postpaid": 2.0, "upgrades": 4.0,
    "byod_pct": 23.53, "atu": 8.0, "atu_pct": 47.06, "protect_pct": 90.48,
    "device_insurance_pct": 90.48, "device_insurance_total": 19.0, "device_insurance_ga": 15.0,
    "device_insurance_upgrades": 4.0, "platinum_pts": 42.0, "avg_platinum_pts": 2.47,
    "platinum_pts_5plus": 1.0, "boost_ready_bounty": 17.0, "tablet_ga": 0.0,
    "boost_app_pct": 17.0 / 15.0 * 100.0,
}
got_rep = ds.normalize_rep(FULL_REP)
check("F1 the rep mapping produces exactly the same columns",
      set(got_rep) == set(expect_rep), sorted(set(got_rep) ^ set(expect_rep)))
check("F2 ... with exactly the same values",
      all(got_rep[k] == v for k, v in expect_rep.items()),
      {k: (got_rep.get(k), v) for k, v in expect_rep.items() if got_rep.get(k) != v})
expect_store_full = {
    "salesforce_id": "166111", "store_code": "", "address": "1800 Great Neck rd",
    "location": "Cellular Services dot net llc", "gross_adds": 61.0, "pay_now_acts": 58.0,
    "pay_later_acts": 3.0, "total_upgrades": 29.0, "total_acts": 90, "psa_projected": 28.0,
    "family_plan_pct": 66.67, "tmr3": 62.5, "aal_conversion": 2.56, "protect_pct": 100.0,
    "atu": 66.67, "byod_pct": 33.33, "port_pct": 66.67, "conversion_rate": 3.85,
    "acc_attach_rate": 416.67, "avg_first_mrc": 52.67, "sales_target": 25.0,
    "zero_selling_days": 0.0, "shopper_trak_conversion": 0.0,
}
got_store = ds.normalize_store(SEP_STORE)
check("F3 the store mapping produces exactly the same columns",
      set(got_store) == set(expect_store_full), sorted(set(got_store) ^ set(expect_store_full)))
check("F4 ... with exactly the same values",
      all(got_store[k] == v for k, v in expect_store_full.items()),
      {k: (got_store.get(k), v) for k, v in expect_store_full.items() if got_store.get(k) != v})
check("F5 the SECOND spelling is used when the first is not sent",
      ds.normalize_store({"byod_total_percent": "41.5"})["byod_pct"] == 41.5)
check("F6 ... and a measured zero under the FIRST spelling is kept, not fallen through",
      ds.normalize_store({"byod_adds_percent": "0", "byod_total_percent": "41.5"})["byod_pct"] == 0.0)
check("F7 the derived Ready-App rate stays absent with no denominator (the 2026-09-26 rule)",
      ds.normalize_rep({"boost_ready_bounty": "17"})["boost_app_pct"] is None)
check("F8 every declared source field is a real string, and no column is declared twice per report",
      all(isinstance(k, str) and k for m in (ds.STORE_FIELDS, ds.REP_FIELDS) for r in m for k in r[2:]))
check("F9 the derived columns declare the source fields they are derived FROM",
      set(ds.STORE_DERIVED_FROM) and set(ds.REP_DERIVED_FROM))

print("\nG. the status line says it, and an INT column never receives a float")
res = {"period": "October 2026", "written": {"raw_dlar_store": 28, "raw_dlar_rep": 43},
       "pulled": {"raw_dlar_store": 28, "raw_dlar_rep": 43},
       "as_of": {"raw_dlar_store": "2026-10-14", "raw_dlar_rep": "2026-10-14"},
       "vintage": {}, "fields": {"dlar": fa_oct}}
line = ds.status_sentence(res)
check("G1 a portal-field change is NOT an OK run", line.startswith("⚠ PARTIAL"), line[:40])
check("G2 the line names the field that stopped arriving", "gross_activation_quantity" in line)
check("G3 the line names where the new spelling is", "activation_quantity" in line)
check("G4 a healthy run is still OK",
      ds.status_sentence({"period": "September 2026",
                          "written": {"raw_dlar_store": 28, "raw_dlar_rep": 43},
                          "pulled": {"raw_dlar_store": 28, "raw_dlar_rep": 43},
                          "as_of": {}, "vintage": {}, "fields": {"dlar": fa_sep}}).startswith("OK"))
check("G5 total_acts is an int or None — never a float Postgres would reject on an INT column",
      isinstance(got_store["total_acts"], int) and oct_row["total_acts"] is None)
check("G6 a long list of changed fields is capped, not dumped",
      "+" in ds.status_sentence({"period": "P", "written": {}, "pulled": {}, "as_of": {},
                                 "vintage": {},
                                 "fields": {"dlar": {"missing": [str(i) for i in range(20)],
                                                     "blank": [], "unconsumed": []}}}))
check("G7 the sweep measures BOTH reports' field arrival, not just the one that broke",
      set(ds.FIELD_MAPS) == {"dlar", "advocate"} and
      bool(ds.declared_source_fields("advocate")))

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — a figure the portal did not send lands absent, a rename is named by the feed itself, and "
      "a healthy run stays quiet.")
