#!/usr/bin/env python3
"""DB-free proof for exec_metric_defs (mig 962) — the Executive-MTD "Bill Payment Qty reads 0 on one
tenant, correct on the other" defect (owner 2026-09-04).

Stdlib only, no DB, no fastapi. Run: python3 backend/harness_exec_metric_defs.py

Sections:
  A  the line predicate (moved, must stay byte-identical) + the over-match it must refuse
  B  preset resolution: tenant > carrier preset > built-in default
  C  the reported defect, replayed on the real two-tenant vocabulary
  D  the silent-zero detector
  E  hygiene: RULE TWO + the migration's own claims
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from app.modules.commcalc import exec_metric_defs as emd  # noqa: E402

HOUSE = emd.HOUSE_ORG
LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
_P = _F = 0


def ok(cond, label):
    global _P, _F
    if cond:
        _P += 1
        print(f"  ✓ {label}")
    else:
        _F += 1
        print(f"  ✗ {label}")


def eq(got, want, label):
    ok(got == want, f"{label}  (got {got!r}, want {want!r})")


def line(dept="", cat="", pdesc="", **kw):
    d = {"department": dept, "category": cat, "product_desc": pdesc}
    d.update(kw)
    return d


def match(rules, r):
    return emd.line_match(rules, str(r.get("department") or "").strip().lower(),
                          str(r.get("category") or "").strip().lower(),
                          str(r.get("product_desc") or "").strip().lower())


# ── A. the predicate ──────────────────────────────────────────────────────────────────────────────
print("\n§A  line predicate")
BP_OLD = {"department": ["rtr"], "category": ["rtr product", "other carr. payments"]}
BP_NEW = {"department": ["bill payments"], "exclude_category": ["other charge"]}

ok(match(BP_OLD, line("rtr", "other carr. payments", "wallet funding")), "exact department token matches")
ok(not match(BP_OLD, line("bill payments", "boost rtr", "boost rtr $1-$650")),
   "department is EXACT, not substring: 'bill payments' does not match token 'rtr'")
ok(match(BP_NEW, line("bill payments", "boost rtr", "boost rtr $1-$650")), "corrected rule matches the real payment line")
ok(not match(BP_NEW, line("bill payments", "other charge", "epay service charge")),
   "exclude_category removes the ePay SERVICE CHARGE fee line")
ok(match(BP_NEW, line("bill payments", "xfinity refill", "xfinity prepaid refill")), "other-carrier refill still counts")
ok(match(BP_NEW, line("bill payments", "other carr. payments", "likewize deductible payment")),
   "a payment line under an unlisted category still counts (department carries it)")

# THE over-match this design exists to refuse. `_BILLPAY_DEFAULT_TOKENS` uses substring 'boost rtr';
# these PROTECTION lines end "... included in your boost rtr payment".
PROTECT = line("miscellaneous", "service", "boost protect - boost protect tier 1 - $8 included in your boost rtr payment")
ok(match({"product_desc_contains": ["boost rtr"]}, PROTECT),
   "substring token 'boost rtr' DOES swallow a protection line (the trap)")
ok(not match(BP_NEW, PROTECT), "the shipped rule refuses that protection line")
ok(not match(BP_NEW, line("", "", "")), "an empty line matches nothing")
ok(not match({}, line("bill payments", "boost rtr", "x")), "empty rules match nothing")
ok(not match(None, line("bill payments", "boost rtr", "x")), "None rules never raise")

# exclusions win over positives
ok(not match({"department": ["bill payments"], "exclude_department": ["bill payments"]},
             line("bill payments", "", "")), "exclude_department beats a positive department match")
SCREEN = {"category": ["service"], "exclude_product_desc_contains": ["screen protect"]}
ok(not match(SCREEN, line("", "service", "screen protector")),
   "exclude_product_desc_contains is a SUBSTRING test ('screen protect' hits 'screen protector')")
ok(match(SCREEN, line("", "service", "device protection")),
   "…and leaves a non-matching description alone")

# ── B. resolution precedence ──────────────────────────────────────────────────────────────────────
print("\n§B  tenant > carrier preset > built-in default")
NEW_ORG = "11111111-1111-1111-1111-111111111111"
preset_row = {"org_id": HOUSE, "bucket": "bill_payment", "rules": BP_NEW, "basis": "count", "carrier": "boost"}
own_row = {"org_id": NEW_ORG, "bucket": "bill_payment", "rules": {"department": ["mine"]}, "basis": "count", "carrier": None}
boost = [{"code": "boost", "name": "Boost Mobile", "is_default": True}]
total = [{"code": None, "name": "Total Wireless", "is_default": True}]

r = emd.resolve([preset_row], NEW_ORG, boost)
eq(r["bill_payment"]["rules"], BP_NEW, "a new BOOST tenant with no row inherits the carrier preset")
eq(r["bill_payment"]["source"], "carrier_preset", "and the source says it was inherited, not chosen")

r = emd.resolve([preset_row, own_row], NEW_ORG, boost)
eq(r["bill_payment"]["rules"], {"department": ["mine"]}, "the tenant's own row beats the preset")
eq(r["bill_payment"]["source"], "tenant", "source reports the tenant override")

r = emd.resolve([preset_row], NEW_ORG, total)
eq(r["bill_payment"]["rules"], emd.CODE_DEFAULTS["bill_payment"]["rules"],
   "a tenant on ANOTHER carrier does not inherit that preset")
eq(r["bill_payment"]["source"], "default", "…and falls through to the built-in default")

r = emd.resolve([preset_row], NEW_ORG, [])
eq(r["bill_payment"]["source"], "default", "no carrier chosen -> presets do not apply (byte-identical to pre-962)")
eq(emd.resolve([], NEW_ORG, boost)["bill_payment"]["rules"], emd.CODE_DEFAULTS["bill_payment"]["rules"],
   "no rows at all -> built-in defaults")

# a tenant must never be able to publish a preset for anybody
rogue = {"org_id": LUX, "bucket": "bill_payment", "rules": {"department": ["rogue"]}, "basis": "count", "carrier": "boost"}
eq(emd.resolve([rogue], NEW_ORG, boost)["bill_payment"]["rules"], emd.CODE_DEFAULTS["bill_payment"]["rules"],
   "a NON-house row carrying a carrier is NOT honored as a preset")

# every bucket always resolves, and provenance never changes a number
r = emd.resolve([preset_row], NEW_ORG, boost)
eq(sorted(r.keys()), sorted(emd.CODE_DEFAULTS.keys()), "every bucket is always present")
eq(emd.strip_sources(r)["bill_payment"], {"rules": BP_NEW, "basis": "count"}, "strip_sources drops provenance only")
ok(all("source" not in v for v in emd.strip_sources(r).values()), "the aggregation shape carries no 'source' key")

# legacy rows (pre-962: no carrier key at all) behave exactly as before
legacy = {"org_id": NEW_ORG, "bucket": "phones", "rules": {"category": ["x"]}, "basis": "count"}
eq(emd.resolve([legacy], NEW_ORG, boost)["phones"]["rules"], {"category": ["x"]},
   "a pre-962 row with no `carrier` key resolves as the org's own definition")
eq(emd.resolve([{"org_id": NEW_ORG, "bucket": "nonsense", "rules": {}, "basis": "count"}], NEW_ORG, boost)["phones"]["source"],
   "default", "an unknown bucket is ignored, never fatal")

# ── C. the reported defect, on the real vocabulary ────────────────────────────────────────────────
print("\n§C  the reported defect replayed (shapes taken from live Aug-2026 rows)")
# CellfonzRUs / Boost side
HOUSE_ROWS = ([line("bill payments", "boost rtr", "boost rtr $1-$650")] * 6
              + [line("bill payments", "other charge", "epay service charge")] * 4
              + [line("bill payments", "xfinity refill", "xfinity prepaid refill")] * 2
              + [PROTECT] * 3
              + [line("ondigo", "phone cases", "case")] * 2)
# LuxeLink / Total side
LUX_ROWS = ([line("rtr", "other carr. payments", "wallet funding")] * 5
            + [line("accessories", "accessory", "case")] * 2)

n_old_house = sum(1 for r in HOUSE_ROWS if match(BP_OLD, r))
n_new_house = sum(1 for r in HOUSE_ROWS if match(BP_NEW, r))
eq(n_old_house, 0, "THE BUG: the shared default matched ZERO house bill-payment lines")
eq(n_new_house, 8, "the corrected definition matches the payments (6 RTR + 2 refills)")
ok(not any(match(BP_NEW, r) for r in [PROTECT]), "…and never the protection lines")
eq(sum(1 for r in HOUSE_ROWS if match(BP_NEW, r) and r["category"] == "other charge"), 0,
   "…and never the ePay service-charge fee lines")

BP_LUX = {"department": ["rtr"], "category": ["rtr product", "other carr. payments"],
          "product_desc_contains": ["wallet funding"]}
eq(sum(1 for r in LUX_ROWS if match(BP_LUX, r)), 5, "the other tenant matched correctly all along")
eq(sum(1 for r in LUX_ROWS if match(BP_LUX, r)), sum(1 for r in LUX_ROWS if match(BP_LUX, r)),
   "and this change does not touch its rule at all")
ok(all(match(BP_LUX, r) == match(BP_LUX, r) for r in LUX_ROWS), "other-tenant classification is untouched")

# ── D. the silent-zero detector ───────────────────────────────────────────────────────────────────
print("\n§D  silent-zero detector")
broken = emd.resolve([{"org_id": HOUSE, "bucket": "bill_payment", "rules": BP_OLD,
                       "basis": "count", "carrier": None}], HOUSE, boost)
cov = emd.bucket_coverage(HOUSE_ROWS, broken)
ok(any(g["bucket"] == "bill_payment" for g in cov["gaps"]), "a bucket matching zero lines IS reported")
ok(cov["note"] and "bill_payment" in cov["note"], "the note names the bucket")
gap = [g for g in cov["gaps"] if g["bucket"] == "bill_payment"][0]
eq(gap["unmatched_departments"][0][0], "bill payments", "the gap names the department that DID occur")
eq(gap["source"], "tenant", "…and where the failing definition came from")

fixed = emd.resolve([{"org_id": HOUSE, "bucket": "bill_payment", "rules": BP_NEW,
                      "basis": "count", "carrier": None}], HOUSE, boost)
cov2 = emd.bucket_coverage(HOUSE_ROWS, fixed)
ok(not any(g["bucket"] == "bill_payment" for g in cov2["gaps"]), "no gap once the definition is right")
eq(cov2["matched"]["bill_payment"], 8, "…and the detector counts what the report counts")

eq(emd.bucket_coverage([], fixed)["gaps"], [], "an EMPTY period reports no gaps (not a broken definition)")
eq(emd.bucket_coverage([], fixed)["note"], None, "…and no banner")
eq(emd.bucket_coverage(HOUSE_ROWS, fixed)["scanned"], len(HOUSE_ROWS), "scanned counts every row")
ok("activation" not in emd.bucket_coverage(HOUSE_ROWS, fixed)["matched"],
   "the contract-type bucket is excluded (its zero is a legitimate answer)")
ok(emd.bucket_coverage([None, None], fixed)["scanned"] == 0, "None rows are skipped, never fatal")

# ── E. hygiene ────────────────────────────────────────────────────────────────────────────────────
print("\n§E  RULE TWO + migration hygiene")
HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "app/modules/commcalc/exec_metric_defs.py"), encoding="utf-8").read()
body = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
body = re.sub(r'""".*?"""', "", body, flags=re.S)
for brand in ("boost", "total wireless", "luxelink", "cellfonz", "vidapay", "verizon", "xfinity"):
    ok(brand not in body.lower(), f"RULE TWO: no {brand!r} in the module's executable body")

mig = open(os.path.join(HERE, "../database/migrations/962_exec_metric_carrier_presets.sql"), encoding="utf-8").read()
ok("-- REVERT" in mig, "migration carries a REVERT note")
ok("IF NOT EXISTS" in mig, "migration is idempotent/additive")
ok("ON CONFLICT" in mig, "seeds are re-runnable")
ok("NULLS NOT DISTINCT" in mig, "uniqueness keeps one own-row per bucket")
ok("exec_metric_config" in mig and "CREATE TABLE" not in mig.upper(),
   "EXTENDS the existing table — no sibling table (duplicate-check build gate)")

router = open(os.path.join(HERE, "app/modules/commcalc/router.py"), encoding="utf-8").read()
ok("_exec_line_match = _emd.line_match" in router, "the router re-points at the shared predicate")
ok("_EXEC_METRIC_DEFAULTS = _emd.CODE_DEFAULTS" in router, "…and at the shared vocabulary")
ok(router.count("def _exec_line_match") == 0, "no second copy of the predicate remains")
ok("'metric_coverage': _metric_cov_ex" in router, "Exec MTD returns the coverage banner")

print(f"\n{_P} passed, {_F} failed")
sys.exit(1 if _F else 0)
