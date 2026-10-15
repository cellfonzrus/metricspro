#!/usr/bin/env python3
"""PROOF: a stored pay slice nobody computed reads as UNKNOWN, never as $0.00 earned — and it cannot un-wire.

DB-free. Imports the REAL `commcalc.payout_slice_evidence` and reads its callers as text for the
wiring locks. No network, no database, stdlib only.

Owner report 2026-10-15: *"rep incentive report for luxelink shopws ACC GP as zero but calculates the
commission when you click on teh linke ... if the source for the data is teh same then why is the
index not updateiong all areas when one is claculated , check for all such things in the commission
module and the sales module so everywhere the same data is visible"*.

The two reads were each right about their own source: the table shows the STORED `rep_commissions`
row, the drill-down RE-COMPUTES. The stored row for Luxelink's earlier months was written on
2026-09-10, before the 2026-09-17 itemisation, so it carries a correct `total_payout` beside a
breakdown that was never computed. Measured live: 102 of 192 rows, $23,440.15 of pay.

Run:  python3.11 backend/harness_payout_slice_evidence.py
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from app.modules.commcalc import payout_slice_evidence as pse  # noqa: E402
from app.modules.core.module_graph import home_under_app       # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
# THE GRAPH OWNS THE RULING (§19.18 / §50): no path literal of its own, so moving the home cannot
# leave this lock guarding a file that is no longer there while printing OK.
HOME = os.path.join(APP, home_under_app("payout_slice_evidence"))
ROUTER = os.path.join(APP, "modules", "commcalc", "router.py")
RANGE = os.path.join(APP, "modules", "commcalc", "rep_incentive_range.py")
WHATIF = os.path.join(APP, "modules", "commcalc", "whatif.py")
AUDIENCE = os.path.join(APP, "modules", "commcalc", "payout_audience.py")
FRONT = os.path.join(os.path.dirname(ROOT), "frontend", "src", "app", "(platform)", "commcalc")
PAGE = os.path.join(FRONT, "reports", "page.tsx")
RANGE_TS = os.path.join(FRONT, "_lib", "repIncentiveRange.ts")

_P = []


def ok(name, cond, extra=""):
    _P.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + name + (("   " + str(extra)) if extra and not cond else ""))


def section(t):
    print("\n" + t)


def read(path):
    """A missing file fails the checks BY NAME rather than ending the run in a traceback."""
    try:
        return io.open(path, encoding="utf-8").read()
    except OSError:
        return ""


RSRC, RANGESRC, WSRC, ASRC = read(ROUTER), read(RANGE), read(WHATIF), read(AUDIENCE)
PAGESRC, RANGETS = read(PAGE), read(RANGE_TS)
HOMESRC = read(HOME)

# ── fixtures: the three row shapes that exist in the live table ──────────────────────────────────
# Taken from the measured Luxelink rows (figures real, names not).
AUG = {"tier_source": "plan", "plan_comm": 957.36, "subtotal": 0, "acc_comm": 0,
       "setup_fee_comm": 0, "total_payout": 957.36, "period": "August 2026"}
SEP = {"tier_source": "plan", "plan_comm": 1122.58, "subtotal": 1122.58, "acc_comm": 415.08,
       "setup_fee_comm": 37.5, "total_payout": 1122.58, "period": "September 2026"}
# a plan-mode rep paid ONLY by the installment ledger: no plan paid them, so subtotal 0 is TRUE.
INST = {"tier_source": "plan", "plan_comm": 0, "subtotal": 0.0, "acc_comm": 0,
        "residual_installment_comm": 120.0, "total_payout": 120.0, "period": "August 2026"}
# a Boost row: the calculator has always written every slice.
BOOST = {"tier_source": "kpi", "plan_comm": 0, "subtotal": 300.0, "acc_comm": 40.0,
         "total_payout": 300.0, "period": "August 2026"}

# ═════ A. THE PREDICATE: WHICH ROWS CONTRADICT THEMSELVES ════════════════════════════════════════
section("A. A row that contradicts itself was never itemised")
ok("A1 the measured August row reads NOT RECORDED", pse.basis(AUG) == pse.BASIS_NOT_RECORDED)
ok("A2 the measured September row reads RECORDED", pse.basis(SEP) == pse.BASIS_RECORDED)
ok("A3 an installment-only row is RECORDED — its zero subtotal is a true statement",
   pse.basis(INST) == pse.BASIS_RECORDED)
ok("A4 a Boost row is RECORDED, so the house org is byte-identical",
   pse.basis(BOOST) == pse.BASIS_RECORDED)
ok("A5 the predicate needs BOTH halves: a positive subtotal is never withheld",
   pse.basis({**AUG, "subtotal": 957.36}) == pse.BASIS_RECORDED)
ok("A6 ... and a zero plan_comm is never withheld",
   pse.basis({**AUG, "plan_comm": 0}) == pse.BASIS_RECORDED)
ok("A7 a row that is not a dict does not raise", pse.basis(None) == pse.BASIS_RECORDED)
ok("A8 the mode test reads the row's OWN tier_source, with no tenant or carrier name",
   pse.is_plan_mode({"tier_source": "PLAN"}) and not pse.is_plan_mode({"tier_source": ""}))

# ═════ B. UNKNOWN IS NOT ZERO ════════════════════════════════════════════════════════════════════
section("B. An unknown slice is None, and a known zero is still zero")
ok("B1 a withheld slice is None, not 0.0", pse.slice_value(AUG, "acc_comm") is None)
ok("B2 a recorded slice is its figure", pse.slice_value(SEP, "acc_comm") == 415.08)
ok("B3 a genuine zero on a recorded row stays 0.0 — it IS a measurement",
   pse.slice_value(BOOST, "setup_fee_comm") == 0.0)
ok("B4 a column this module does not own is returned as it stands",
   pse.slice_value(AUG, "total_payout") == 957.36)
ok("B5 every named slice is withheld together — a row is itemised or it is not",
   all(pse.slice_value(AUG, f) is None for f in pse.NAMED_SLICES))
ok("B6 the TOTAL is never withheld: it has always been written by every writer",
   "total_payout" not in pse.NAMED_SLICES and "plan_comm" not in pse.NAMED_SLICES
   and "final_payout" not in pse.NAMED_SLICES)
ok("B7 the installment columns are not named slices either",
   not {"residual_installment_comm", "installment_comm_sale"} & set(pse.NAMED_SLICES))

# ═════ C. THE ALLOWLIST POSTURE (§66) ════════════════════════════════════════════════════════════
section("C. Only a basis named as recorded is evidence (allowlist, §66)")
ok("C1 'recorded' is evidence", pse.is_evidence(pse.BASIS_RECORDED))
ok("C2 'not_recorded' is not", not pse.is_evidence(pse.BASIS_NOT_RECORDED))
ok("C3 a basis nobody has ruled on is NOT evidence", not pse.is_evidence("probably_fine"))
ok("C4 a missing basis is NOT evidence", not pse.is_evidence(None) and not pse.is_evidence(""))
ok("C5 case and padding do not change the verdict", pse.is_evidence("  Recorded "))

# ═════ D. A SUM CANNOT SILENTLY ADD AN UNKNOWN AS ZERO ═══════════════════════════════════════════
section("D. A sum reports what it excluded")
_s = pse.sum_slice([AUG, SEP], "acc_comm")
ok("D1 only the recorded row is summed", _s["total"] == 415.08)
ok("D2 the excluded count is returned, not hidden",
   _s["rows_not_recorded"] == 1 and _s["rows_counted"] == 1 and _s["complete"] is False)
ok("D3 an all-recorded sum is complete", pse.sum_slice([SEP, BOOST], "acc_comm")["complete"] is True)
ok("D4 an empty list is complete and zero",
   pse.sum_slice([], "acc_comm") == {"total": 0.0, "rows_counted": 0, "rows_not_recorded": 0,
                                     "complete": True})
ok("D5 the months to recalculate are named, in first-seen order",
   pse.periods_not_recorded([SEP, AUG, {**AUG, "period": "July 2026"}, AUG])
   == ["August 2026", "July 2026"])

# ═════ E. ANNOTATION CHANGES NO FIGURE ═══════════════════════════════════════════════════════════
section("E. Stamping a row never edits its money")
_row = dict(AUG)
_before = {k: v for k, v in _row.items()}
pse.annotate(_row)
ok("E1 every stored value is untouched", all(_row[k] == _before[k] for k in _before))
ok("E2 the basis is stamped", _row[pse.BASIS_FIELD] == pse.BASIS_NOT_RECORDED)
ok("E3 a reason is given for a withheld row", bool(_row[pse.REASON_FIELD]))
ok("E4 a recorded row gets no reason", pse.annotate(dict(SEP))[pse.REASON_FIELD] == "")
ok("E5 the reason tells the operator what to DO", "Run Calculation" in pse.reason(AUG))
ok("E6 the reason names no month, tenant or figure of its own",
   not re.search(r"\d", pse.reason(AUG)))

# ═════ F. RULE TWO ═══════════════════════════════════════════════════════════════════════════════
section("F. Nothing hard-coded (RULE TWO)")
_BANNED = ("boost", "luxelink", "total wireless", "cellfonz", "vzone", "854f6d7b")


def _code_only(src):
    """Source with the module docstring and every comment removed.

    The first draft of F1 failed on this module's OWN header, which names the tenant whose rows were
    measured and the engine whose rows were not — exactly the evidence the house rules ask a fix to
    state. RULE TWO is about what the CODE branches on, so the scan reads code. (It also flagged
    `acima_comm`, which is a column of `rep_commissions` this module can only name verbatim; a column
    name is not a branch, and `NAMED_SLICES` is checked field-by-field in B5 instead.)"""
    # EVERY docstring, not just the module's: a function's own docstring explaining which engine
    # wrote a row is documentation too. A docstring is matched as a triple-quoted string that STARTS a
    # line (after indentation), which is what one looks like here; a sentence this module actually
    # renders is written as an ordinary literal and is still scanned.
    src = re.sub(r'(?m)^[ \t]*(?:r|u)?"""[\s\S]*?"""', " ", src)
    return "\n".join(re.sub(r"#.*$", " ", l) for l in src.splitlines())


_HOMECODE = _code_only(HOMESRC)
_hit = [w for w in _BANNED if w in _HOMECODE.lower()]
ok("F1 no carrier, tenant or org id is named in the home's CODE", not _hit, _hit)
ok("F1b the home's only product-named string is an existing rep_commissions column",
   [w for w in _HOMECODE.split() if "acima" in w.lower()] == ['"acima_comm",'])
ok("F2 the home reads no table and opens no connection",
   not re.search(r"\.table\(|\.select\(|\.execute\(|requests\.|httpx", HOMESRC))
ok("F3 the home imports nothing from app", "from app" not in HOMESRC and "import app" not in HOMESRC)

# ═════ G. THE WIRING: EVERY READER DEREFERENCES THE HOME ═════════════════════════════════════════
section("G. Every surface that reads a stored slice asks this module")
ok("G1 the one rep-row read stamps the basis",
   "_pse.annotate(cr)" in RSRC or "payout_slice_evidence" in RSRC)
ok("G2 the audience allow-list lets the basis through to the client",
   '"payout_slice_basis"' in ASRC and '"payout_slice_reason"' in ASRC)
ok("G3 the month range sums named slices through this module",
   "_pse.NAMED_SLICES" in RANGESRC and "_pse.sum_slice" in RANGESRC)
ok("G4 the range names the months to recalculate", "_pse.periods_not_recorded" in RANGESRC)
ok("G5 the simulator's baseline excludes a never-written slice",
   "slices_are_evidence" in WSRC and "slices_not_recorded" in WSRC)
ok("G6 the coaching card and the action plan read the slice through this module",
   RSRC.count("_pse_mod().slice_value(cr, 'subtotal')") == 2)
ok("G7 neither of them multiplies an unknown into an at-risk figure",
   RSRC.count("_sub_known is not None") >= 3)
ok("G8 the router holds ONE import accessor, not a copy per surface",
   RSRC.count("def _pse_mod(") == 1)

# No second copy of the predicate: nobody else may spell the contradiction test.
_COPIES = []
for _name, _src in (("router.py", RSRC), ("rep_incentive_range.py", RANGESRC),
                    ("whatif.py", WSRC), ("page.tsx", PAGESRC), ("repIncentiveRange.ts", RANGETS)):
    for _l in _src.splitlines():
        if ("plan_comm" in _l and "subtotal" in _l
                and re.search(r"==\s*0|>\s*0", _l) and "payout_slice_evidence" not in _l):
            _COPIES.append(f"{_name}: {_l.strip()[:90]}")
ok("G9 no caller spells the contradiction test itself", not _COPIES, _COPIES[:2])

# ═════ H. THE WRITER CANNOT PRODUCE A ROW THIS CANNOT DETECT ═════════════════════════════════════
section("H. Both plan-mode writers itemise, so an undetectable row cannot be created")
# The reader recognises a never-itemised row by the subtotal/plan_comm contradiction. A writer that
# set a positive subtotal WITHOUT the slices would make a row no reader could ever flag — so the
# second branch (a rep the calculator never emitted a row for) must write them too.
_wr = re.search(r'newrow = \{"org_id": org_id, "period": period,[\s\S]{0,2600}?comms\.append\(newrow\)', RSRC)
ok("H1 the second plan-mode writer branch was found", _wr is not None)
_wsrc = _wr.group(0) if _wr else ""
ok("H2 it writes the accessory slice", '"acc_comm"' in _wsrc and 'pv.get("acc_comm")' in _wsrc)
ok("H3 it writes the set-up fee slice", 'newrow["setup_fee_comm"]' in _wsrc)
ok("H4 it is guarded, so a pre-migration column set still inserts",
   'cols["setup_fee_comm"]' in _wsrc)

# ═════ I. THE SCREEN ═════════════════════════════════════════════════════════════════════════════
section("I. The screen shows a dash and the server's reason, and decides nothing")
ok("I1 the page reads the stamped basis", "payout_slice_basis" in PAGESRC)
ok("I2 the page shows the server's reason, not wording of its own", "payout_slice_reason" in PAGESRC)
ok("I3 an unrecorded slice renders as a dash", "sliceRecorded(r) ? fmt" in PAGESRC
   or "slice(r," in PAGESRC)
ok("I4 every named slice column on the full breakdown goes through the helper",
   all(f"slice(r, r.{f}, fmt)" in PAGESRC
       for f in ("acc_comm", "setup_fee_comm", "subtotal", "premium_comm")))
ok("I5 the TOTAL column is never dashed — it is always a real figure",
   "slice(r, r.total_payout" not in PAGESRC and "slice(r, r.final_payout" not in PAGESRC)
ok("I6 the range table's client-side sums skip an unrecorded slice",
   "RANGE_SLICES" in RANGETS and "filter(sliceRecorded)" in RANGETS)
ok("I7 the client treats a payload with no basis as recorded, so an older server is unchanged",
   "?? 'recorded'" in RANGETS or "|| 'recorded'" in RANGETS)
ok("I8 the page names which months to recalculate", "notRecordedMonths" in PAGESRC)

# ═════ Z. ARMED CONTROLS ═════════════════════════════════════════════════════════════════════════
section("Z. The checks are armed (each would catch its own defect)")
ok("Z1 A1 would catch a predicate that only looked at subtotal: the installment row proves it",
   pse.basis(INST) == pse.BASIS_RECORDED and float(INST["subtotal"]) == 0.0)
ok("Z2 B1 would catch a module that returned 0.0 for unknown: the raw column IS 0",
   float(AUG["acc_comm"]) == 0.0 and pse.slice_value(AUG, "acc_comm") is None)
ok("Z3 C3's detector really rejects an unruled basis",
   not pse.is_evidence("recorded_ish") and pse.is_evidence("recorded"))
ok("Z4 D1 would catch a sum that added the unknown in: that sum would be the same number",
   round(sum(float(r["acc_comm"]) for r in (AUG, SEP)), 2) == 415.08
   and pse.sum_slice([AUG, SEP], "acc_comm")["rows_not_recorded"] == 1)
ok("Z5 G9's detector finds a planted second copy",
   bool(re.search(r"==\s*0|>\s*0", "if r['plan_comm'] > 0 and r['subtotal'] == 0:")))
ok("Z6 F1's detector finds a planted carrier name in CODE but not in a comment",
   [w for w in _BANNED if w in _code_only("if mode == 'boost':").lower()] == ["boost"]
   and [w for w in _BANNED if w in _code_only("x = 1  # the boost engine").lower()] == [])

print("\n%d passed, %d failed" % (sum(_P), len(_P) - sum(_P)))
sys.exit(0 if all(_P) else 1)
