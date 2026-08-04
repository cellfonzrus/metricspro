"""NEGATIVE CONTROL for harness_eep_pl_wiring.py — proves the harness actually BITES.

Re-runs the whole EEP P&L-wiring harness twice against DELIBERATELY WRONG routing, and asserts the
harness FAILS both times (a harness that passes on broken code proves nothing):

  A. the pre-change behaviour — 'additional_payroll' falling through to `store_opex`
     (what would happen if this package had never been written)
  B. the money trap — 'additional_payroll' treated as the AUTHORITATIVE gross, i.e. routed to
     `wages` AND allowed to suppress the shifts×rate fallback. That would silently DELETE a
     tenant's wages line and book a cash advance as clock-in payroll.

Run: `python3 harness_eep_pl_wiring_negcontrol.py` from backend/.  Exit 0 = the harness is armed.
"""
import io
import os
import runpy
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.modules.account import coa  # noqa: E402

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harness_eep_pl_wiring.py")


def run_harness():
    """Run the harness in-process; return (exit_code, stdout)."""
    buf = io.StringIO()
    code = 0
    try:
        with redirect_stdout(buf):
            runpy.run_path(HARNESS, run_name="__harness__")
    except SystemExit as e:
        code = e.code or 0
    return code, buf.getvalue()


ORIG_ROUTES = dict(coa._EXPENSE_ROUTES)
ORIG_AUTH = set(coa._WAGES_AUTHORITATIVE_KEYS)
results = []

# ── control A: additional_payroll → store_opex (the pre-package behaviour) ─────────────────────
coa._EXPENSE_ROUTES.pop("additional_payroll", None)
codeA, outA = run_harness()
failsA = [l.strip() for l in outA.splitlines() if l.strip().startswith("FAIL")]
coa._EXPENSE_ROUTES.clear()
coa._EXPENSE_ROUTES.update(ORIG_ROUTES)
results.append(("A  additional_payroll → store_opex (pre-change)", codeA, failsA))

# ── control B: additional_payroll treated as the authoritative gross (the money trap) ──────────
coa._EXPENSE_ROUTES["additional_payroll"] = ("wages", None)
coa._WAGES_AUTHORITATIVE_KEYS.add("additional_payroll")
codeB, outB = run_harness()
failsB = [l.strip() for l in outB.splitlines() if l.strip().startswith("FAIL")]
coa._EXPENSE_ROUTES.clear()
coa._EXPENSE_ROUTES.update(ORIG_ROUTES)
coa._WAGES_AUTHORITATIVE_KEYS.clear()
coa._WAGES_AUTHORITATIVE_KEYS.update(ORIG_AUTH)
results.append(("B  additional_payroll → wages + authoritative (money trap)", codeB, failsB))

# ── restored: the harness must pass again ──────────────────────────────────────────────────────
codeC, outC = run_harness()

print("═" * 96)
ok = True
for name, code, fails in results:
    armed = code != 0 and fails
    ok = ok and armed
    print(f"  {'ARMED ' if armed else 'BLIND '} control {name}: exit={code}, {len(fails)} checks flipped to FAIL")
    for f in fails[:6]:
        print(f"            ↳ {f}")
    if len(fails) > 6:
        print(f"            ↳ ... and {len(fails) - 6} more")
print(f"  {'OK    ' if codeC == 0 else 'BROKEN'} restored routing: harness exit={codeC} (expected 0)")
print("═" * 96)
ok = ok and codeC == 0
print("NEGATIVE CONTROL PASSED — the harness detects both regressions." if ok
      else "NEGATIVE CONTROL FAILED — the harness does NOT detect a regression.")
sys.exit(0 if ok else 1)
