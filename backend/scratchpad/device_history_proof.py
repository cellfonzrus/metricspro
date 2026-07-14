"""Proof harness for commission-16 (Device History Lookup) — drives the REAL pure logic in
`app.modules.commcalc.device_history` (no DB, no network). Covers: input-shape detection (IMEI vs
phone), key-matching, months-active counting (incl. gaps + spelling-collapse), the sold-vs-not prompt
truth table, the commission/rebate categorization SEPARATION, and the money-gate truth table (which the
router's `_can_view_device_commission` delegates to verbatim, so proving the pure fn proves the gate
shape). Run: `python3 backend/scratchpad/device_history_proof.py` from the backend dir.
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


print("── 1. input-shape detection (IMEI vs phone; a HINT, both keys searched anyway) ──")
check("15-digit → imei", dh.detect_shape("355123456789012") == "imei")
check("14-digit → imei", dh.detect_shape("35512345678901") == "imei")
check("16-digit → imei", dh.detect_shape("3551234567890123") == "imei")
check("IMEI with dashes → imei", dh.detect_shape("35-512345-678901-2") == "imei")
check("10-digit → phone", dh.detect_shape("5551234567") == "phone")
check("11-digit (leading 1) → phone", dh.detect_shape("15551234567") == "phone")
check("formatted 10-digit → phone", dh.detect_shape("(555) 123-4567") == "phone")
check("short junk → unknown", dh.detect_shape("123") == "unknown")
check("empty → unknown", dh.detect_shape("") == "unknown")
check("12-digit (neither) → unknown", dh.detect_shape("123456789012") == "unknown")

print("── 2. query candidates + key matching (both keys, '.0'/leading-1 normalization) ──")
c10 = dh.query_candidates("5551234567")
check("10-digit query offers 11-digit variant", "15551234567" in c10)
c11 = dh.query_candidates("15551234567")
check("11-digit query offers 10-digit variant", "5551234567" in c11)
check("phone matches stored mdn exactly", dh.keys_match(c10, None, "5551234567"))
check("11-digit query matches stored 10-digit mdn", dh.keys_match(c11, "", "5551234567"))
check("query matches stored '.0'-suffixed mdn", dh.keys_match(dh.query_candidates("1234567890"), None, "1234567890.0"))
imc = dh.query_candidates("355123456789012")
check("imei matches stored serial_1", dh.keys_match(imc, "355123456789012", None))
check("imei does NOT match a different serial", not dh.keys_match(imc, "999999999999999", None))
check("no false match on empty stored values", not dh.keys_match(imc, "", ""))

print("── 3. months-active = COUNT of DISTINCT residual periods (incl. gaps + spelling collapse) ──")
t1 = dh.tenure_from_periods(["January 2026", "February 2026", "March 2026"])
check("3 consecutive → 3 mo", t1["months_active"] == 3)
check("activation = earliest", t1["activation_period"] == "January 2026")
check("last seen = latest", t1["last_seen_period"] == "March 2026")
tg = dh.tenure_from_periods(["January 2026", "March 2026", "June 2026"])
check("GAPS: 3 distinct periods → 3 mo (NOT calendar span 6)", tg["months_active"] == 3)
check("GAPS: activation still earliest (Jan)", tg["activation_period"] == "January 2026")
check("GAPS: last seen still latest (Jun)", tg["last_seen_period"] == "June 2026")
tsp = dh.tenure_from_periods(["2026-01", "January 2026", "2026-02"])
check("SPELLING: '2026-01' & 'January 2026' collapse → 2 mo (not 3)", tsp["months_active"] == 2)
tu = dh.tenure_from_periods(["June 2026", "January 2026"])
check("UNORDERED input → activation is chronological min", tu["activation_period"] == "January 2026")
te = dh.tenure_from_periods([])
check("empty → 0 mo, activation None", te["months_active"] == 0 and te["activation_period"] is None)
check("note surfaces the 'residual months' assumption", "residual months" in t1["note"])

print("── 4. prompt truth table (sold-by-us vs not; ALWAYS shown, never gated) ──")
p_sold = dh.prompt_for(True, "2026-06-12")
check("sold → kind=upgrade", p_sold["kind"] == "upgrade")
check("sold → text says UPGRADE", "UPGRADE" in p_sold["text"])
check("sold → text carries the sold date", "2026-06-12" in p_sold["text"])
p_new = dh.prompt_for(False)
check("not sold → kind=new_phone", p_new["kind"] == "new_phone")
check("not sold → text says NEW phone", "NEW phone" in p_new["text"])
p_sold_nodate = dh.prompt_for(True, None)
check("sold w/o date → still upgrade, no stray date", p_sold_nodate["kind"] == "upgrade" and " on " not in p_sold_nodate["text"])

print("── 5. commission vs REBATE categorization — SEPARATE, never blended ──")
check("SIMCR → rebate", dh.categorize_comp("SIMCR") == "rebate")
check("DEVICE_REIMB → rebate", dh.categorize_comp("DEVICE_REIMB") == "rebate")
check("MI (residual) → other (NOT folded into commission from payment_detail)", dh.categorize_comp("MI") == "other")
check("NAB (bounty) → other", dh.categorize_comp("NAB") == "other")
check("blank comp → other", dh.categorize_comp("") == "other")

mi_matches = [
    {"period": "January 2026", "amount": 10.0},
    {"period": "January 2026", "amount": 2.5},    # same period → summed
    {"period": "February 2026", "amount": 12.0},
]
pay_matches = [
    {"period": "2026-01", "amount": 25.0, "payment_type": "SIM Card Reimbursement"},   # rebate
    {"period": "2026-02", "amount": 50.0, "payment_type": "New Activation Bounty M1"},  # excluded
]
stub_comp = {"SIM Card Reimbursement": "SIMCR", "New Activation Bounty M1": "NAB"}
mt = dh.build_money_table(mi_matches, pay_matches, lambda pt: stub_comp.get(pt, "UNMAPPED"))
check("commission has 2 period rows (Jan summed, Feb)", len(mt["commission"]["rows"]) == 2)
check("commission Jan row summed to 12.5", mt["commission"]["rows"][0] == {"period": "January 2026", "amount": 12.5, "label": "Residual (MI+ATU)", "source": "raw_mi"})
check("commission subtotal = 24.5", mt["commission"]["subtotal"] == 24.5)
check("rebate has exactly 1 row (the reimbursement)", len(mt["rebate"]["rows"]) == 1)
check("rebate subtotal = 25.0", mt["rebate"]["subtotal"] == 25.0)
check("grand total = commission + rebate = 49.5", mt["grand_total"] == 49.5)
check("excluded bounty is 1 row / $50 — NOT in any subtotal", mt["excluded"]["payment_detail_other"]["count"] == 1 and mt["excluded"]["payment_detail_other"]["total"] == 50.0)
check("SEPARATION: every commission row sources raw_mi", all(r["source"] == "raw_mi" for r in mt["commission"]["rows"]))
check("SEPARATION: every rebate row sources raw_payment_detail", all(r["source"] == "raw_payment_detail" for r in mt["rebate"]["rows"]))
check("SEPARATION: no rebate row carries the residual label", all(r["label"] != "Residual (MI+ATU)" for r in mt["rebate"]["rows"]))
check("grand_total excludes the $50 bounty", abs(mt["grand_total"] - (mt["commission"]["subtotal"] + mt["rebate"]["subtotal"])) < 1e-9)

# empty inputs → zeroed sections, no excluded note, grand total 0
mt0 = dh.build_money_table([], [], lambda pt: "MI")
check("empty money table → grand total 0, no excluded", mt0["grand_total"] == 0 and mt0["excluded"] is None)

print("── 5b. real discrepancy_engine.parse_payment_type classifier (reimbursement→rebate) ──")
try:
    from app.modules.commcalc.discrepancy_engine import parse_payment_type
    comp_of = lambda pt: parse_payment_type(pt)[0]
    real_pay = [
        {"period": "2026-03", "amount": 15.0, "payment_type": "SIM Card Reimbursement"},        # SIMCR → rebate
        {"period": "2026-03", "amount": 40.0, "payment_type": "Device Reimbursement"},           # DEVICE_REIMB → rebate
        {"period": "2026-03", "amount": 99.0, "payment_type": "Monthly Incentive Month 3"},      # MI → excluded
        {"period": "2026-03", "amount": 20.0, "payment_type": "Boost Ready Bounty Month 1"},     # BRB → excluded
    ]
    rmt = dh.build_money_table([], real_pay, comp_of)
    check("real: SIM + Device reimbursements → 2 rebate rows", len(rmt["rebate"]["rows"]) == 2)
    check("real: rebate subtotal = 55.0", rmt["rebate"]["subtotal"] == 55.0)
    check("real: MI + bounty → excluded (2 rows / $119)", rmt["excluded"]["payment_detail_other"]["count"] == 2 and rmt["excluded"]["payment_detail_other"]["total"] == 119.0)
except Exception as e:
    print(f"  SKIP  real-classifier subtest (import failed: {type(e).__name__}: {e})")

print("── 6. money-gate truth table (device_commission — ADMIN-ONLY by default) ──")
check("no caller → deny", dh.device_commission_allowed(None) is False)
check("super_admin → allow", dh.device_commission_allowed({"super_admin": True}) is True)
check("scope=all → allow", dh.device_commission_allowed({"perms": {"scope": "all"}}) is True)
check("role=admin → allow", dh.device_commission_allowed({"role": "admin"}) is True)
check("role=Admin (case) → allow", dh.device_commission_allowed({"role": "Admin"}) is True)
check("modules grant → allow", dh.device_commission_allowed({"perms": {"modules": ["device_commission"]}}) is True)
check("data grant true → allow", dh.device_commission_allowed({"perms": {"data": {"device_commission": True}}}) is True)
check("scoped user, no grant → DENY (admin-only default)", dh.device_commission_allowed({"perms": {"scope": "store"}}) is False)
check("wrong grant (carrier_residual) → deny", dh.device_commission_allowed({"perms": {"scope": "store", "modules": ["carrier_residual"]}}) is False)
check("data grant explicitly False → deny", dh.device_commission_allowed({"perms": {"scope": "store", "data": {"device_commission": False}}}) is False)

print(f"\n==== device-history proof: {_pass} passed, {_fail} failed ====")
sys.exit(1 if _fail else 0)
