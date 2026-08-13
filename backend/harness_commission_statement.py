"""HARNESS — commission_statement.py (the individual per-employee Commission Statement PDF).

This document is the READ-ONLY companion to the Payout Structure doc: it re-states what ONE employee
earned for ONE period, itemized, from the drill-down (the single source of truth). The risk is the same
as the structure doc's — not a crash, but a WRONG NUMBER or a misattributed reason on a page an employee
reads as their pay. Every section targets a way that could happen; the fixtures mimic the real
`commission_drilldown.explain_rep` return shape so a passing harness means the real statement is right.

  A. The headline total is SOURCED (rep_commissions.total_payout), never silently re-summed.
  B. Earned items re-use the STRUCTURE doc's rate/condition/frequency English (matched set) and read the
     amount the engine actually paid — a $0 rule is not shown as earning.
  C. Multi-month residuals: a PAID installment is earned; a HELD one carries the drill-down's own reason.
  D. Held/suppressed plan lines are surfaced with their reason and would-have-paid $, never dropped.
  E. The five canonical ledger buckets roll up and label correctly; absent ledger => no bucket section.
  F. Honest degradation — empty rep, no last-calc row, totally empty input all produce a valid document.
  G. The PDF renders, escapes hostile markup, and the filename is slugged (guarded skip if no reportlab).
  H. ARMED negative control — a deliberately wrong expectation MUST fail, or this harness proves nothing.

Run: python3 harness_commission_statement.py     (no DB, no network — pure fixtures)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import commission_statement as cs   # noqa: E402

PASS, FAIL = [], []


def check(label, got, want):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def check_in(label, needle, hay):
    if needle in hay:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      {needle!r} not found in {hay!r}")


def check_not_in(label, needle, hay):
    if needle not in hay:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      {needle!r} WAS found in {hay!r} (must not be)")


def section(t):
    print(f"\n── {t}")


# ── FIXTURE — the shape commission_drilldown.explain_rep returns for a paid rep ─────────────────────
# Rules carry the detail=True fields (match_field/op/value + amount/pct) so the statement can describe
# them with payout_structure's helpers, exactly as the real preview(detail=True) supplies them.
PLAN_COMPONENT = {
    "plan_name": "Total Employee Comp Chicago", "plan_id": "p1", "total_payout": 232.50,
    "rules": [
        {"rule_id": "r1", "label": "Accessory", "payout_kind": "pct_price",
         "amount": 0.175, "pct": 0.175, "match_field": "accessory", "match_op": "equals",
         "match_value": "yes", "qualifies": True, "matched_lines": 10, "qualifying_units": 10,
         "payout": 175.0, "lines": [{"product": "Case", "amount": 17.5} for _ in range(10)]},
        {"rule_id": "r2", "label": "Edge", "payout_kind": "flat_per_unit",
         "amount": 25.0, "pct": 0.0, "match_field": "tender_type", "match_op": "contains",
         "match_value": "Credit Card; TW Financing Prepaid", "qualifies": True,
         "matched_lines": 3, "qualifying_units": 2, "payout": 50.0,
         "lines": [{"product": "iPhone 15", "amount": 25.0},
                   {"product": "iPhone 15", "amount": 25.0},
                   {"product": "RTR handset", "amount": 0.0, "suppressed": True,
                    "suppressed_reason": "Removed by exclusion 'RTR'", "would_have_paid": 25.0}]},
        {"rule_id": "r3", "label": "Upgrade", "payout_kind": "flat_per_unit",
         "amount": 0.0, "pct": 0.0, "match_field": "contract_type", "match_op": "equals",
         "match_value": "Upgrade", "qualifies": True, "matched_lines": 5, "qualifying_units": 5,
         "payout": 0.0, "lines": [{"product": "Upgrade line", "amount": 0.0}]},
    ],
}
MULTIMONTH_COMPONENT = {
    "devices": [
        {"product": "iPhone 15", "label": "iPhone 15 / Unlimited", "installments": [
            {"label": "iPhone 15 / Unlimited", "month_index": 1, "status": "paid", "amount": 10.0},
            {"label": "iPhone 15 / Unlimited", "month_index": 2, "status": "withheld", "amount": 0.0,
             "withheld_amount": 10.0, "expected_amount": 10.0,
             "hold_detail": "Held — dealer not shown paid on this line: no matching raw_mi residual row."},
        ]},
    ],
    "totals": {"paid": 1, "withheld": 1, "amount": 10.0},
}
RECON = {"plan_comm": 232.50, "installment_comm_sale": 10.0, "residual_installment_comm": 0.0,
         "total_payout": 242.50, "source": "rep_commissions (last Run Calculation)"}
EXPLAIN = {"period": "2026-07", "rep": "Jose Utrera", "plan_component": PLAN_COMPONENT,
           "multimonth_component": MULTIMONTH_COMPONENT, "reconciliation": RECON,
           "zero_explanation": [], "note": None}
BUCKETS = {"commission": 225.0, "spiff": 0.0, "equipment_rebate": 0.0,
           "residual_monthly": 10.0, "autopay_residual": 0.0}

DOC = cs.build_statement(EXPLAIN, buckets=BUCKETS, tenant_name="Luxelink Wireless LLC",
                         rep_name="Jose Utrera", period="2026-07", generated_at="August 13, 2026")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. The headline total is SOURCED, not re-summed")

check("A1 total is the reconciliation (rep_commissions.total_payout)",
      DOC["summary"]["total_payout"], "$242.50")
check("A2 total_raw carries the number", DOC["summary"]["total_raw"], 242.50)
check_in("A3 the source is named as the payout of record", "rep_commissions",
         DOC["summary"]["total_source"])
# An explicit caller value (the endpoint passing rep_commissions.total_payout) overrides.
d_ovr = cs.build_statement(EXPLAIN, total_payout=999.99)
check("A4 an explicit total wins", d_ovr["summary"]["total_payout"], "$999.99")
# With no last-calc row, the total falls back to the component sum and SAYS SO.
d_norec = cs.build_statement(dict(EXPLAIN, reconciliation={}))
check("A5 no recon => plan + installment component sum", d_norec["summary"]["total_payout"], "$242.50")
check_in("A6 ... and the source discloses it is a component sum", "component",
         d_norec["summary"]["total_source"])
check("A7 plan subtotal is the plan component total", DOC["summary"]["plan_subtotal"], "$232.50")
check("A8 installment subtotal", DOC["summary"]["installment_subtotal"], "$10.00")

section("B. Earned items — same English as the structure doc, engine's own amounts")

earned = {it["what"]: it for it in DOC["earned"]}
check("B1 the paying plan rules are itemized", set(earned) >= {"Accessory", "Edge"}, True)
check("B2 a $0 plan rule is NOT shown as earning", "Upgrade" in earned, False)
check("B3 accessory earned the engine's payout", earned["Accessory"]["amount"], "$175.00")
# The pct_price rate reads `pct` (10%/17.5%), never the decoy amount — the structure doc's Trap ①.
check("B4 accessory rate is the % the engine uses", earned["Accessory"]["rate"],
      "17.5% of the sale price")
check_not_in("B5 ... never the decoy $ amount", "$0.17", earned["Accessory"]["rate"])
check("B6 edge earned 2 units x $25", earned["Edge"]["amount"], "$50.00")
check("B7 edge rate reads the flat amount", earned["Edge"]["rate"], "$25.00")
# Frequency comes from the SAME pay-gate resolver the structure doc and the engine use (Trap ③).
check("B8 a tender-matched flat rule pays once per device", earned["Edge"]["frequency"],
      "Once per device")
check("B9 accessory qualifying-unit count surfaces", earned["Accessory"]["units"], 10)

section("C. Multi-month residuals — paid is earned, held carries the drill-down's reason")

check("C1 a PAID installment joins the earned table",
      "iPhone 15 / Unlimited" in earned, True)
check("C2 ... at the amount that paid", earned["iPhone 15 / Unlimited"]["amount"], "$10.00")
check("C3 ... marked as an installment source", earned["iPhone 15 / Unlimited"]["source"],
      "installment")

held_by_reason = [h for h in DOC["held"]]
inst_held = [h for h in held_by_reason if "dealer not shown paid" in h["reason"]]
check("C4 a HELD installment appears in the held table", len(inst_held), 1)
check("C5 ... with the withheld amount", inst_held[0]["amount"], "$10.00")
check_in("C6 ... and the drill-down's own hold reason", "dealer not shown paid", inst_held[0]["reason"])
check("C7 the held installment names its month", inst_held[0]["when"], "Month 2 — iPhone 15")

section("D. Suppressed plan lines are surfaced, never dropped")

excl = [h for h in DOC["held"] if "exclusion" in h["reason"]]
check("D1 a matched-but-excluded plan line is held, not silently gone", len(excl), 1)
check("D2 ... with its would-have-paid amount", excl[0]["amount"], "$25.00")
check_in("D3 ... and the reason the engine attached", "RTR", excl[0]["reason"])
# Edge pays $50 (2 units) AND holds $25 (1 excluded line): it must appear in BOTH tables.
check("D4 a partly-paid rule is in earned", "Edge" in earned, True)
check("D5 ... and its excluded line is in held", excl[0]["what"], "Edge")

section("E. The five canonical ledger buckets")

buckets = {b["label"]: b for b in DOC["summary"]["buckets"]}
check("E1 all five canonical categories are present", len(DOC["summary"]["buckets"]), 5)
check("E2 commission bucket rolls up", buckets["Commission"]["amount"], "$225.00")
check("E3 residual bucket rolls up", buckets["Residual / monthly incentives"]["amount"], "$10.00")
check("E4 bucket total sums", DOC["summary"]["bucket_total"], "$235.00")
check("E5 has_buckets true when the ledger has data", DOC["summary"]["has_buckets"], True)
# No ledger rollup passed => no bucket section (honest empty).
d_nob = cs.build_statement(EXPLAIN, buckets=None)
check("E6 no ledger => has_buckets false", d_nob["summary"]["has_buckets"], False)

section("F. Honest degradation")

# A rep with sales but no plan match / no earnings — the drill-down's zero_explanation carries the WHY.
EMPTY_EXPLAIN = {"period": "2026-07", "rep": "New Hire", "plan_component": {"rules": [], "total_payout": 0.0},
                 "multimonth_component": {"devices": [], "totals": {"paid": 0, "withheld": 0, "amount": 0.0}},
                 "reconciliation": None,
                 "zero_explanation": ["No sale lines found for 'New Hire' in 2026-07."], "note": None}
d_e = cs.build_statement(EMPTY_EXPLAIN, tenant_name="Luxelink Wireless LLC", rep_name="New Hire",
                         period="2026-07")
check("F1 an earnings-free statement is flagged empty", d_e["empty"], True)
check("F2 ... has no earned rows", d_e["earned"], [])
check("F3 ... total is a clean $0.00", d_e["summary"]["total_payout"], "$0.00")
check("F4 ... and the drill-down's reason is carried as a note",
      any("No sale lines found" in n for n in d_e["notes"]), True)

# The absolute worst case: nothing at all. Must still be a valid, JSON-safe document, not a crash.
d_none = cs.build_statement({})
check("F5 an entirely empty input still builds", d_none["empty"], True)
check("F6 ... with a $0 total", d_none["summary"]["total_payout"], "$0.00")
check("F7 ... and no buckets", d_none["summary"]["has_buckets"], False)

check("F8 filename is slugged from tenant + employee + period",
      cs.filename_for(DOC), "luxelink-wireless-llc-jose-utrera-2026-07-commission-statement.pdf")
check("F9 filename survives an empty document", cs.filename_for({}), "commission-statement.pdf")

# The model is served as JSON for the on-screen preview — it must be JSON-serialisable.
import json  # noqa: E402
check("F10 the document model is JSON-serialisable", isinstance(json.dumps(DOC), str), True)

# Batch: one model per employee (so a later zip/export is possible), single-employee proven above.
batch = cs.build_statements(
    [{"rep": "Jose Utrera", "explain": EXPLAIN, "buckets": BUCKETS, "total_payout": 242.50},
     {"rep": "New Hire", "explain": EMPTY_EXPLAIN}],
    tenant_name="Luxelink Wireless LLC", period="2026-07")
check("F11 batch returns one model per employee", len(batch), 2)
check("F12 ... the first is the paid rep", batch[0]["employee"], "Jose Utrera")
check("F13 ... the second degrades to empty", batch[1]["empty"], True)

section("G. The PDF renders, and hostile text cannot break it")

try:
    import reportlab  # noqa: F401
    _HAVE_RL = True
except Exception:
    _HAVE_RL = False

if not _HAVE_RL:
    PASS.append("G-skip reportlab not installed — PDF render checks skipped (model checks above stand)")
else:
    pdf = cs.render_pdf(DOC)
    check("G1 output is a real PDF", pdf[:5], b"%PDF-")
    check("G2 ... of a plausible size", len(pdf) > 3000, True)
    check("G3 ... and is terminated", pdf.rstrip()[-5:], b"%%EOF")

    hostile_rule = {"rule_id": "h", "label": "Tom & Jerry <b>bonus</b>", "payout_kind": "flat_per_unit",
                    "amount": 5.0, "pct": 0.0, "match_field": "product_desc", "match_op": "contains",
                    "match_value": "A&B <script>alert(1)</script> Ω", "qualifies": True,
                    "matched_lines": 1, "qualifying_units": 1, "payout": 5.0, "lines": []}
    h_explain = {"period": "2026-07", "rep": "Amp & <Co>",
                 "plan_component": {"plan_name": "Plan & <Co>", "total_payout": 5.0, "rules": [hostile_rule]},
                 "multimonth_component": {"devices": [], "totals": {}}, "reconciliation": None,
                 "zero_explanation": [], "note": "Note with <tags> & ampersands"}
    h_doc = cs.build_statement(h_explain, tenant_name="Ampersand & Co <Ltd>", rep_name="Amp & <Co>",
                               period="2026-07")
    check("G4 markup in tenant/product/label renders instead of crashing", cs.render_pdf(h_doc)[:5], b"%PDF-")
    check("G5 an empty document still renders", cs.render_pdf(cs.build_statement({}))[:5], b"%PDF-")

section("H. ARMED negative control — these MUST fail if the checks are real")

_p_before, _f_before = len(PASS), len(FAIL)
check("H-armed total", DOC["summary"]["total_payout"], "$0.00")               # the WRONG answer
check("H-armed edge freq", earned["Edge"]["frequency"], "Each qualifying item")  # WRONG (it's per device)
armed_failed = len(FAIL) - _f_before
if armed_failed == 2:
    FAIL[:] = FAIL[:_f_before]
    PASS.append("H1 negative control fired on both wrong expectations (checks are live)")
else:
    FAIL.append(f"H1 NEGATIVE CONTROL DID NOT FIRE — {armed_failed}/2 wrong answers were accepted. "
                f"The assertions above prove nothing.")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 78}")
for f in FAIL:
    print(f"  ✗ {f}")
print(f"  PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
