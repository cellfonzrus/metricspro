"""HARNESS — Flags & Compliance summary assembly (commcalc/compliance_summary.py, owner
directive 2026-09-03: "Flags and Compliance should be a separate Dashboard and every flag and
compliance issue should be under that").

  A. CATEGORIES registry shape — unique keys, internal hrefs, every entry labeled + described.
  B. assemble — known counts sum into total_open; a failed probe (None / missing key) reports
     count=null AND lands in `unavailable` (NEVER a fake 0); dict form carries the note;
     negative/garbage counts sanitized; category order = registry order (stable for the page).
  Z. ARMED negative control.

Run: python3 harness_compliance_summary.py   (stdlib-only, pure module)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import compliance_summary as cs    # noqa: E402

PASS, FAIL = [], []


def check(label, got, want=True):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}: got {got!r}, want {want!r}")


# ── A. registry shape ─────────────────────────────────────────────────────────────────────────────
keys = [c[0] for c in cs.CATEGORIES]
check("A1 keys unique", len(keys) == len(set(keys)))
check("A2 every href internal", all(c[2].startswith("/") for c in cs.CATEGORIES))
check("A3 every entry labeled+described", all(c[1] and c[3] for c in cs.CATEGORIES))
check("A4 the owner's named surfaces present",
      {"commission_flags", "pay_discrepancy", "ingest_quarantine", "attendance_exceptions",
       "hours_approval", "approvals_pending", "billpay_coverage", "deposit_accountability",
       "statement_staleness"} <= set(keys))

# ── B. assemble ───────────────────────────────────────────────────────────────────────────────────
counts = {
    "commission_flags": 3,
    "pay_discrepancy": 0,
    "ingest_quarantine": None,                       # probe failed
    "ops_chargebacks": {"count": 2, "note": "incl. envelope shorts"},
    "attendance_exceptions": "7",                    # stringy int tolerated
    "hours_approval": -4,                            # garbage → clamped to 0
    "approvals_pending": {"count": None, "note": "approvals table unavailable"},
    "billpay_coverage": 1,
    "statement_staleness": 1,
    # deposit_accountability deliberately MISSING → unavailable
}
out = cs.assemble(counts, period="September 2026", as_of="2026-09-03")
bykey = {c["key"]: c for c in out["categories"]}
check("B1 order = registry order", [c["key"] for c in out["categories"]], list(keys))
check("B2 known counts sum", out["total_open"], 3 + 0 + 2 + 7 + 0 + 1 + 1)
check("B3 failed probe is null, never 0", bykey["ingest_quarantine"]["count"], None)
check("B4 missing key is null", bykey["deposit_accountability"]["count"], None)
check("B5 unavailable lists exactly the nulls", set(out["unavailable"]),
      {"ingest_quarantine", "deposit_accountability", "approvals_pending"})
check("B6 note passthrough", bykey["ops_chargebacks"]["note"], "incl. envelope shorts")
check("B7 stringy int", bykey["attendance_exceptions"]["count"], 7)
check("B8 negative clamped", bykey["hours_approval"]["count"], 0)
check("B9 zero is a real count (not unavailable)", bykey["pay_discrepancy"]["count"], 0)
check("B10 period/as_of passthrough", (out["period"], out["as_of"]),
      ("September 2026", "2026-09-03"))
empty = cs.assemble({})
check("B11 nothing probed: total 0, all unavailable",
      (empty["total_open"], len(empty["unavailable"])), (0, len(keys)))

# ── Z. armed negative control ─────────────────────────────────────────────────────────────────────
before = len(FAIL)
check("Z1 armed control (failed probe must NOT count as 0)", bykey["ingest_quarantine"]["count"], 0)
if len(FAIL) == before + 1 and "Z1" in FAIL[-1]:
    FAIL.pop()
    PASS.append("Z1 armed negative control fired")
else:
    FAIL.append("Z1 armed negative control DID NOT fire — harness cannot detect failures")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  FAIL: {f}")
sys.exit(1 if FAIL else 0)
