"""Proof harness for the carrier-aware (MA-fed) Device History money section — drives the REAL pure
logic in `app.modules.commcalc.device_history` (no DB, no network). Covers:
  • ma_paid sign normalization (VidaPay negative=payout → shown paid-to-dealer positive; charge negative)
  • build_ma_money_table over the REAL repro fixture (luxelink IMEI 355163568356973, June 2026, TWO rows)
  • aggregation of MULTIPLE rows per IMEI per period + per-row `detail` kept expandable
  • period dedupe across the 'June 2026' / '2026-06' spelling duality (canon_display_period)
  • line_status = NULL never gates the paid display (nonzero spiff/rebate = the payment evidence)
  • ma_tenure_from_periods (recurring months, MA wording — NOT the false 'No residual history on file')
  • empty-table fallback (explicit note, grand_total 0, no crash) + IMEI normalization (dashed query)
Run: `python3 backend/scratchpad/device_history_ma_proof.py` from the backend dir.
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.modules.commcalc import device_history as dh

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}")


def approx(a, b, eps=1e-6):
    return abs(float(a) - float(b)) < eps


# The REAL repro rows the owner pulled from live raw_ma_commission (luxelink org, June 2026,
# IMEI 355163568356973). Both period='June 2026', both line_status=NULL, all amounts NEGATIVE.
REPRO = [
    {"imei": "355163568356973", "period": "June 2026", "line_status": None, "status_change_date": None,
     "activation_type": "New", "activation_type2": "branded", "ban": "BAN1",
     "rebate": -529.0, "device_margin": -20.0, "consumer_margin": 0,
     "spiff_m1": -5.0, "spiff_m2": -48.75, "spiff_m3": 0, "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0,
     "mrc_net_discount": 0},
    {"imei": "355163568356973", "period": "June 2026", "line_status": None, "status_change_date": None,
     "activation_type": "New", "activation_type2": "branded", "ban": "BAN1",
     "rebate": 0, "device_margin": 0, "consumer_margin": 0,
     "spiff_m1": 0, "spiff_m2": -5.0, "spiff_m3": 0, "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0,
     "mrc_net_discount": 0},
]

print("── 1. ma_paid: negative=payout → paid-to-dealer positive; charge → negative; sign preserved ──")
check("rebate -529 → +529 paid", approx(dh.ma_paid(-529.0), 529.0))
check("spiff -48.75 → +48.75 paid", approx(dh.ma_paid(-48.75), 48.75))
check("device_margin -20 → +20 paid", approx(dh.ma_paid(-20.0), 20.0))
check("a POSITIVE charge -> negative (sign NOT dropped)", approx(dh.ma_paid(10.0), -10.0))
check("blank -> 0.0", approx(dh.ma_paid(""), 0.0) and approx(dh.ma_paid(None), 0.0))
check("'$1,234.50' string parsed then negated", approx(dh.ma_paid("$1,234.50"), -1234.5))

print("── 2. build_ma_money_table over the REAL two-row repro fixture ──")
mt = dh.build_ma_money_table(REPRO)
check("kind == 'ma'", mt["kind"] == "ma")
check("source == raw_ma_commission", mt["source"] == "raw_ma_commission")
check("TWO rows same period → ONE period bucket", len(mt["periods"]) == 1)
p0 = mt["periods"][0]
check("period is canonical 'June 2026'", p0["period"] == "June 2026")
check("per-row DETAIL kept expandable (2 lines)", len(p0["detail"]) == 2)
check("spiff_total aggregated = 5 + 48.75 + 5 = 58.75", approx(p0["spiff_total"], 58.75))
check("rebate aggregated = 529", approx(p0["rebate"], 529.0))
check("margin_total aggregated = 20 (device) + 0", approx(p0["margin_total"], 20.0))
check("mrc_net_discount = 0 (informational)", approx(p0["mrc_net_discount"], 0.0))
check("spiff subtotal = 58.75", approx(mt["spiff"]["subtotal"], 58.75))
check("rebate subtotal = 529", approx(mt["rebate"]["subtotal"], 529.0))
check("margin subtotal = 20", approx(mt["margin"]["subtotal"], 20.0))
check("grand_total = spiff+rebate+margin = 607.75 (mrc EXCLUDED)", approx(mt["grand_total"], 607.75))
check("grand_total does NOT include mrc", approx(mt["grand_total"],
      mt["spiff"]["subtotal"] + mt["rebate"]["subtotal"] + mt["margin"]["subtotal"]))
check("sign_convention documented", "payout" in (mt.get("sign_convention") or ""))
check("no false $0 — grand_total is the real 607.75", approx(mt["grand_total"], 607.75))

print("── 2b. MRC is a POSITIVE plan price, NOT a payout — un-negated, excluded from grand total ──")
mrc_fixture = [
    {"imei": "1", "period": "June 2026", "spiff_m1": -10.0, "mrc_net_discount": 45.0},   # $45 plan
    {"imei": "1", "period": "June 2026", "rebate": -100.0, "mrc_net_discount": 45.0},     # same plan, 2nd line
]
mm = dh.build_ma_money_table(mrc_fixture)
check("MRC stays POSITIVE (+45 per line, not −45)", approx(mm["periods"][0]["detail"][0]["mrc_net_discount"], 45.0))
check("MRC aggregates positive (45 + 45 = 90)", approx(mm["periods"][0]["mrc_net_discount"], 90.0))
check("MRC section subtotal = 90 (positive)", approx(mm["mrc"]["subtotal"], 90.0))
check("payouts still paid-to-dealer (spiff 10 + rebate 100)",
      approx(mm["spiff"]["subtotal"], 10.0) and approx(mm["rebate"]["subtotal"], 100.0))
check("grand_total = 110 — MRC ($90) EXCLUDED", approx(mm["grand_total"], 110.0))
check("_num0 does NOT negate a positive; blank → 0", approx(dh._num0(45.0), 45.0) and approx(dh._num0(""), 0.0))

print("── 3. line_status = NULL never gates the paid display (amounts are the evidence) ──")
check("period line_status is None (both rows NULL)", p0["line_status"] is None)
check("top-level line_status None but money still present", mt["line_status"] is None and mt["grand_total"] > 0)
check("detail rows carry line_status None (not crash)", all(d["line_status"] is None for d in p0["detail"]))

print("── 4. period dedupe across 'June 2026' / '2026-06' spelling duality ──")
dup = [
    {"imei": "1", "period": "June 2026", "spiff_m1": -10.0},
    {"imei": "1", "period": "2026-06", "spiff_m1": -5.0},   # SAME month, other spelling
    {"imei": "1", "period_year": 2026, "period_month": 6, "spiff_m2": -1.0},  # y/m fallback, same month
]
mtd = dh.build_ma_money_table(dup)
check("three spellings of June 2026 collapse to ONE period", len(mtd["periods"]) == 1)
check("collapsed period is 'June 2026'", mtd["periods"][0]["period"] == "June 2026")
check("spiff_total summed across all three = 16", approx(mtd["periods"][0]["spiff_total"], 16.0))
check("detail keeps all three contributing rows", len(mtd["periods"][0]["detail"]) == 3)

multi = [
    {"imei": "1", "period": "May 2026", "spiff_m1": -3.0},
    {"imei": "1", "period": "June 2026", "spiff_m1": -4.0},
]
mtm = dh.build_ma_money_table(multi)
check("distinct months stay distinct (2 periods)", len(mtm["periods"]) == 2)
check("periods sorted chronologically (May before June)",
      [p["period"] for p in mtm["periods"]] == ["May 2026", "June 2026"])

print("── 5. empty-table fallback: explicit note, grand_total 0, no crash ──")
mte = dh.build_ma_money_table([])
check("empty → kind still 'ma'", mte["kind"] == "ma")
check("empty → grand_total 0.0", approx(mte["grand_total"], 0.0))
check("empty → explicit note (NOT a silent $0)", bool(mte["note"]) and "ingested" in mte["note"])
check("empty → periods == []", mte["periods"] == [])
check("None input → no crash, empty note", dh.build_ma_money_table(None)["periods"] == [])

print("── 6. ma_tenure_from_periods: recurring months + MA wording (not the false Boost empty text) ──")
tt = dh.ma_tenure_from_periods(["June 2026", "2026-06", "July 2026", "May 2026"])
check("distinct recurring months = 3 (June dedup)", tt["months_active"] == 3)
check("activation = earliest (May 2026)", tt["activation_period"] == "May 2026")
check("last seen = latest (July 2026)", tt["last_seen_period"] == "July 2026")
check("basis mentions master-agent", "master-agent" in tt["basis"])
te = dh.ma_tenure_from_periods([])
check("empty MA tenure → 0 months", te["months_active"] == 0)
check("empty MA tenure note is MA-specific (NOT 'No residual history on file')",
      "master-agent" in te["note"] and "No residual history on file" not in te["note"])

print("── 7. IMEI normalization — the router matches a dashed/spaced query to the stored MA imei ──")
q = dh.query_candidates("35-516356-835697-3")
check("dashed IMEI query matches stored raw_ma_commission.imei", dh.keys_match(q, "355163568356973"))
check("dashed IMEI query does NOT match a different imei", not dh.keys_match(q, "355163568356900"))
check("digits-only form is a candidate", "355163568356973" in q)

print("── 8. carrier label passthrough (config-driven — no carrier name in the pure code) ──")
lbl = dh.build_ma_money_table(REPRO, carrier_label="Total by Verizon")
check("carrier_label carried through when provided", lbl["carrier_label"] == "Total by Verizon")
check("carrier_label None → None (UI applies neutral fallback)", dh.build_ma_money_table(REPRO)["carrier_label"] is None)
check("blank carrier_label → None", dh.build_ma_money_table(REPRO, carrier_label="   ")["carrier_label"] is None)

print("── 9. Boost payload byte-identity: carrier_mode omitted for boost (carrier_response_fields) ──")
# The pre-carrier-aware device-history response key-set (must stay byte-identical for Boost tenants).
BOOST_KEYS = {"query", "detected", "org_id", "found", "device", "sold_by_us", "prompt", "tenure",
              "residual_periods", "aging", "purchase_price", "commission_visible", "money", "money_locked"}
check("boost → carrier_response_fields is EMPTY", dh.carrier_response_fields("boost") == {})
check("boost key-set == pre-change shape (no carrier_mode added)",
      (BOOST_KEYS | set(dh.carrier_response_fields("boost").keys())) == BOOST_KEYS)
check("plan → adds ONLY carrier_mode", set(dh.carrier_response_fields("plan").keys()) == {"carrier_mode"})
check("plan carrier_mode value == 'plan'", dh.carrier_response_fields("plan")["carrier_mode"] == "plan")
check("None mode treated as boost → {}", dh.carrier_response_fields(None) == {})

print(f"\n==== device-history MA proof: {_pass} passed, {_fail} failed ====")
sys.exit(1 if _fail else 0)
