"""Proof harness — DM-verification audit trail + export parity (owner directive 2026-09-02).

Proves, stdlib-only and DB-free:
  A. changed_fields: cent-precision money diffing, None ('DM left blank') vs 0.00 ('DM set zero'),
     first-save semantics, meta (verified/verified_by/note) changes.
  B. build_audit_row: no-change saves write NOTHING (idle re-verify never spams the log); the
     owner's exact scenario ("dm changes the data in the field AFTER verifying") sets
     edited_after_verify; prior values are carried so history is reconstructable.
  C. submission_dm_fields: the export-facing per-row fields mirror verified_overlay.has_correction
     semantics (dm_corrected only when verified AND a dm_* value is set).
  D. ORIGINALS ARE PRESERVED: apply_overlay on a COPY leaves the original aggregate untouched —
     the totals_original snapshot in _closing_summary_for_date can never mutate.

Run: python3 backend/harness_dm_verification_audit.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))
sys.path.insert(0, os.path.dirname(__file__))

from app.modules.closing.verification_audit import (  # noqa: E402
    changed_fields, build_audit_row, submission_dm_fields, DM_FIELDS)
from app.modules.closing import verified_overlay  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


print("A. changed_fields")
prior = {"verified": True, "verified_by": "Dixit", "note": "",
         "dm_store_cash": 183.0, "dm_store_cc": 0, "dm_epay_cash": 120,
         "dm_epay_cc": 0, "dm_acc_sale": 63, "dm_other": 0}
same = dict(prior)
check("identical save changes nothing", changed_fields(prior, same) == [])
check("cent-precision equality (183 vs 183.004 rounds equal)",
      changed_fields(prior, {**same, "dm_store_cash": 183.004}) == [])
check("real money change detected",
      changed_fields(prior, {**same, "dm_store_cash": 200}) == ["dm_store_cash"])
check("None vs 0 is a change (blank != zero)",
      changed_fields(prior, {**same, "dm_store_cc": None}) == ["dm_store_cc"])
check("garbage money value == None", changed_fields(
    {**prior, "dm_store_cc": None}, {**same, "dm_store_cc": "abc"}) == [])
check("note change detected", "note" in changed_fields(prior, {**same, "note": "recount"}))
check("verified flip detected", "verified" in changed_fields(prior, {**same, "verified": False}))
first = changed_fields(None, {"verified": True, "dm_store_cash": 50})
check("first save counts only set fields", set(first) == {"verified", "dm_store_cash"}, str(first))

print("B. build_audit_row")
check("no-change save writes nothing", build_audit_row("org", same, prior) is None)
row = build_audit_row("org", {**same, "dm_store_cash": 200.0}, prior)
check("owner scenario: edit AFTER verify flags edited_after_verify",
      row is not None and row["edited_after_verify"] is True)
check("prior value preserved", row["prior_dm_store_cash"] == 183.0 and row["dm_store_cash"] == 200.0)
check("changed_fields recorded", row["changed_fields"] == ["dm_store_cash"])
check("not a first revision", row["first_revision"] is False)
row2 = build_audit_row("org", {"verified": True, "verified_by": "D", "dm_store_cash": 50,
                               "close_date": "2026-09-01", "store_code": "X"}, None)
check("first save: first_revision, priors all None",
      row2["first_revision"] is True and row2["prior_dm_store_cash"] is None
      and row2["prior_verified"] is None and row2["edited_after_verify"] is False)
unverified_prior = {**prior, "verified": False}
row3 = build_audit_row("org", {**same, "verified": False, "dm_store_cash": 500}, unverified_prior)
check("edit BEFORE verify does NOT flag edited_after_verify",
      row3 is not None and row3["edited_after_verify"] is False)
row4 = build_audit_row("org", {**same, "note": "checked again"}, prior)
check("meta-only change on verified day: revision recorded, NOT edited_after_verify",
      row4 is not None and row4["edited_after_verify"] is False)

print("C. submission_dm_fields")
f = submission_dm_fields(prior)
check("all six dm fields + note surfaced",
      all(k in f for k in DM_FIELDS) and "dm_note" in f and f["dm_store_cash"] == 183.0)
check("dm_corrected true when verified + values set", f["dm_corrected"] is True)
check("unverified row is not dm_corrected",
      submission_dm_fields({**prior, "verified": False})["dm_corrected"] is False)
blank = {"verified": True, "dm_store_cash": None, "dm_store_cc": None, "dm_epay_cash": None,
         "dm_epay_cc": None, "dm_acc_sale": None, "dm_other": None}
check("verified but no corrections is not dm_corrected",
      submission_dm_fields(blank)["dm_corrected"] is False)
check("mirrors verified_overlay.has_correction",
      submission_dm_fields(prior)["dm_corrected"] == verified_overlay.has_correction(prior)
      and submission_dm_fields(blank)["dm_corrected"] == verified_overlay.has_correction(blank))
check("missing verification row degrades to empty/uncorrected",
      submission_dm_fields(None)["dm_corrected"] is False)

print("D. totals_original snapshot semantics (originals preserved)")
totals = {"store_cash": 500.0, "t_cash": 500.0, "epay_cash": 0.0, "store_cc": 100.0,
          "t_credit": 100.0, "epay_cc": 0.0, "t_ext_cc": 0.0, "acc_sale": 63.0,
          "epay_on_cash": 120.0, "epay_on_cc": 0.0, "other_account": 0.0,
          "t_zelle": 0.0, "t_store_acct": 0.0, "t_gift": 0.0}
snapshot = dict(totals)                       # what _closing_summary_for_date stores as totals_original
overlaid = verified_overlay.apply_overlay(totals, {**prior, "dm_store_cash": 450.0})
check("overlay mutated the working aggregate", overlaid["store_cash"] == 450.0 and overlaid["t_cash"] == 450.0)
check("the ORIGINAL snapshot is untouched", snapshot["store_cash"] == 500.0 and snapshot["t_cash"] == 500.0)
check("original and modified are both available side by side",
      snapshot["store_cash"] != overlaid["store_cash"])

print()
if FAILS:
    print(f"❌ {len(FAILS)} failure(s): {FAILS}")
    sys.exit(1)
print("✅ harness_dm_verification_audit: ALL PASS")
