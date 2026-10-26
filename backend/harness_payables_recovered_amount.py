"""PROOF: what the carrier already recovered against a device is READ, and an unmeasured recovery is
never billed to the owner as cash. Index §COMMCALC-33.

DB-free. Stdlib only, plus the pure home and a source scan for the ratchet.

THE DEFECT THIS REPRODUCES (owner, 2026-10-26, against the Luxelink payables banner): *"the payments
for all phones automatically are done from commission and rebates received and auto adjusted"* —
said to a screen claiming **$544,447.78** of devices "funded from your own cash". The netting was in
the feed the build already read (`raw_ma_commission.rebate`, negative on 3,442 of 4,495 rows,
-$834,234.25), and `engine.py` wrote `rebate_amount = 0.0` on every row because its `imei_match`
branch was a placeholder. A zero is a measurement claim; the honest value was unknown.

§A the sign convention · §B measured vs unmeasured (the allowlist) · §C the out-of-pocket arithmetic
§D the three-way split · §E the 2026-10-26 shapes, end to end · §F THE RATCHET over the real sources.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.modules.payables import recovered_amount as R        # noqa: E402
# THE GRAPH IS THE ONE HOME FOR THE RULING (index §19.18 / §50). This lock carries no literal of its
# own: `module_graph` says which file is allowed to answer this question and this reads it, so a home
# moved there moves here with no edit.
from app.modules.core.module_graph import home_under_app     # noqa: E402

ONE_HOME = home_under_app("device_recovery_amount")

P = F = 0


def ok(cond, label, extra=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {label}")
    else:
        F += 1
        print(f"  FAIL  {label}  {extra}")


def eq(a, b, label):
    ok(a == b, label, f"got {a!r} want {b!r}")


def read(*parts):
    try:
        return open(os.path.join(HERE, *parts), encoding="utf-8").read()
    except OSError:
        return ""


ENGINE = read("app", "modules", "payables", "engine.py")
UNSOLD = read("app", "modules", "payables", "unsold_inventory.py")
HOME_SRC = read("app", *ONE_HOME.split("/"))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§A  the SIGN is declared, never guessed")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
eq(R.from_value(-279.99, R.SIGN_NEGATIVE), 279.99,
   "A1  on a DEDUCTION feed a negative line is the recovery, at its magnitude")
eq(R.from_value(279.99, R.SIGN_NEGATIVE), 0.0,
   "A2  ...and a POSITIVE line on that same feed is not a recovery (it is something else)")
eq(R.from_value(279.99, R.SIGN_POSITIVE), 279.99,
   "A3  on a REIMBURSEMENT feed a positive line is the recovery")
eq(R.from_value(-279.99, R.SIGN_POSITIVE), 0.0,
   "A4  ...and a negative line there is not — a charge must never read as a credit")
eq(R.sign_of({}), R.SIGN_POSITIVE,
   "A5  an UNDECLARED sign keeps the historic meaning rather than inventing one")
eq(R.sign_of({"reimbursement_sign": "nonsense"}), R.SIGN_POSITIVE,
   "A6  ...and so does an unrecognised declaration")
eq(R.sign_of({"reimbursement_sign": R.SIGN_NEGATIVE}), R.SIGN_NEGATIVE,
   "A7  a declared deduction convention is honoured")
ok(R.from_value(None) is None and R.from_value("", R.SIGN_NEGATIVE) is None,
   "A8  THE LINE THAT MATTERS: no number at all is UNMEASURED (None), never a recovery of 0.00")
ok(R.from_value("abc") is None and R.from_value(True) is None,
   "A9  junk and a bool are unmeasured too, not coerced into an amount")
eq(R.from_value(0, R.SIGN_NEGATIVE), 0.0,
   "A10 an explicit zero IS a measurement of zero — the feed said so")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§B  MEASURED is an allowlist, so a new source cannot become evidence by appearing (§66)")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
for b in R.MEASURED_BASES:
    ok(R.is_measured(b), f"B1  {b} is a measurement")
ok(not R.is_measured(R.BASIS_UNMEASURED), "B2  'unmeasured' is not")
ok(not R.is_measured(R.BASIS_UNREADABLE),
   "B3  a DECLARED source that could not be read is not a measurement either (§COMMCALC-29)")
ok(not R.is_measured("some_feed_added_next_year"),
   "B4  THE RATCHET'S POINT: an unruled basis reads as NOT measured — fail-CLOSED, the opposite of "
   "ledger_status.is_payable, because a wrong 'measured' DELETES a real liability")
ok(not R.is_measured(None) and not R.is_measured(""),
   "B5  a missing basis is not a measurement")
eq(R.resolve(100.0, "invented_basis"), (None, R.BASIS_UNMEASURED),
   "B6  resolve refuses an amount whose basis is not ruled — the amount cannot smuggle it in")
eq(R.resolve(None, R.BASIS_DECLARED_FIELD), (None, R.BASIS_UNMEASURED),
   "B7  ...and an absent amount forces the basis back to unmeasured, so no stale basis survives")
eq(R.resolve(None, R.BASIS_UNREADABLE), (None, R.BASIS_UNREADABLE),
   "B8  an unreadable source KEEPS its own basis — 'broken' and 'absent' stay distinguishable")
eq(R.resolve(12.5, R.BASIS_EPAY), (12.5, R.BASIS_EPAY), "B9  a ruled basis passes through")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§C  the out-of-pocket arithmetic — unknown is NOT a bill")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
v = R.out_of_pocket(279.99, 279.99, R.BASIS_DECLARED_FIELD)
ok(v["amount"] == 0.0 and v["covered"] is True and v["recovered"] == 279.99,
   "C1  fully deducted: nothing out of pocket, and the recovery stays visible")
v = R.out_of_pocket(300.0, 250.0, R.BASIS_DECLARED_FIELD)
ok(v["amount"] == 50.0 and v["covered"] is False,
   "C2  partly deducted: out of pocket is the GAP, not the gross")
v = R.out_of_pocket(279.99, None, R.BASIS_UNMEASURED)
ok(v["amount"] == 279.99 and v["measured"] is False and v["netted"] is False
   and v["covered"] is None and v["recovered"] is None,
   "C3  THE OTHER HALF, and I got it wrong first: with no recovery source wired the liability stays "
   "at its GROSS and is FLAGGED not netted. Dropping it would be the same defect reversed — and on "
   "the Boost rows, which carry no basis at all, it would zero a real exposure")
v = R.out_of_pocket(279.99, 0.0, R.BASIS_DECLARED_FIELD)
ok(v["amount"] == 279.99 and v["measured"] is True and v["covered"] is False,
   "C4  a MEASURED zero recovery IS out of pocket in full — the distinction C3 exists for")
v = R.out_of_pocket(100.0, 400.0, R.BASIS_DECLARED_FIELD)
ok(v["amount"] == 0.0 and v["recovered"] == 400.0,
   "C5  an over-recovery clamps at zero and never becomes negative cash on this device")
v = R.out_of_pocket(None, 150.0, R.BASIS_DECLARED_FIELD)
ok(v["amount"] is None and v["recovered"] == 150.0 and v["measured"] is True,
   "C6  cost unknown but recovery real: report the recovery, never a $0.00 bill")
v = R.out_of_pocket(279.99, 279.99, "invented_basis")
ok(v["amount"] == 279.99 and v["measured"] is False and v["covered"] is None,
   "C7  an unruled basis cannot SETTLE a device, however good its number looks — the cost stands")
v = R.out_of_pocket("not a number", 10.0, R.BASIS_EPAY)
ok(v["amount"] is None, "C8  an unreadable cost is unknown, not zero")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§D  one total cannot tell this truth — the three-way split")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
ROWS = [
    {"owed": 280.0, "rebate_amount": 280.0, "rebate_basis": R.BASIS_DECLARED_FIELD},   # covered
    {"owed": 300.0, "rebate_amount": 250.0, "rebate_basis": R.BASIS_DECLARED_FIELD},   # gap 50
    {"owed": 200.0, "rebate_amount": 0.0, "rebate_basis": R.BASIS_DECLARED_FIELD},     # gap 200
    {"owed": 150.0, "rebate_amount": None, "rebate_basis": R.BASIS_UNMEASURED},        # unknown
]
sp = R.split(ROWS)
eq(sp["recovered_amount"], 530.0, "D1  the recovery is summed over the MEASURED rows only")
eq(sp["uncovered_amount"], 400.0,
   "D2  the exposure is the GAP where the recovery was measured (50 + 200) plus the GROSS where it "
   "was not (150) — one convention, nothing dropped and nothing settled")
eq(sp["unmeasured_devices"], 1, "D3  the unmeasured device is counted")
eq(sp["unmeasured_amount"], 150.0,
   "D4  ...and its gross is reported BESIDE the figures, never folded into either")
eq(sp["covered_devices"], 1, "D5  the fully netted devices are counted")
ok(sp["amount_incomplete"] is True,
   "D6  the one boolean a screen reads before presenting the sum as a total")
ok(sp["unmeasured_amount"] == 150.0 and sp["unmeasured_amount"] < sp["uncovered_amount"],
   "D7  the unmeasured money is INSIDE the figure and named, so a screen can say how much of its "
   "own total has never been netted")
sp2 = R.split([ROWS[0], ROWS[1], ROWS[2]])
ok(sp2["amount_incomplete"] is False and sp2["unmeasured_amount"] == 0.0,
   "D8  with every row measured the total is presentable as a total")
eq(R.split([])["devices"], 0, "D9  an empty set is empty, not an error")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§E  the 2026-10-26 shapes, end to end on the real config")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MA = {"reimbursement_source": "imei_match", "reimbursement_field": "rebate",
      "reimbursement_sign": R.SIGN_NEGATIVE}
row = {"imei": "352153372172297", "sku": "SAMSUNG A16", "rebate": -279.99}
amt, basis = R.resolve(R.from_row(row, MA["reimbursement_field"], R.sign_of(MA)),
                       R.BASIS_DECLARED_FIELD)
ok(amt == 279.99 and basis == R.BASIS_DECLARED_FIELD,
   "E1  THE LIVE SHAPE: the commission row's negative rebate reads as a $279.99 recovery")
ok(R.out_of_pocket(279.99, amt, basis)["covered"] is True,
   "E2  ...which settles a $279.99 device — it is not 'funded from your own cash'")
NOCFG = {"reimbursement_source": "imei_match", "reimbursement_field": None}
amt2, basis2 = R.resolve(R.from_row(row, NOCFG["reimbursement_field"], R.sign_of(NOCFG)),
                         R.BASIS_DECLARED_FIELD)
ok(amt2 is None and basis2 == R.BASIS_UNMEASURED,
   "E3  THE OLD STATE: with no column declared the answer is UNKNOWN — this is the 0.0 that was "
   "written into all 4,495 rows")
e4 = R.out_of_pocket(279.99, amt2, basis2)
ok(e4["amount"] == 279.99 and e4["netted"] is False,
   "E4  ...so the pre-config figure is the gross AND SAYS it is not netted — which is what the "
   "owner's banner should have said instead of 'funded from your own cash'")
ok(R.from_row({"sku": "X"}, "rebate", R.SIGN_NEGATIVE) is None,
   "E5  a declared column the row does not carry is unmeasured — the row was never asked")
ok(R.from_row({"rebate": None}, "rebate", R.SIGN_NEGATIVE) is None,
   "E6  a NULL in the declared column is unmeasured, not a measured zero")
# The banner's own arithmetic, in miniature: gross vs netted.
BANNER = ([{"owed": 213.81, "rebate_amount": 213.81, "rebate_basis": R.BASIS_DECLARED_FIELD}] * 3
          + [{"owed": 319.0, "rebate_amount": 290.0, "rebate_basis": R.BASIS_DECLARED_FIELD}] * 2
          + [{"owed": 278.0, "rebate_amount": None, "rebate_basis": R.BASIS_UNMEASURED}])
b = R.split(BANNER)
gross = round(sum(r["owed"] for r in BANNER), 2)
ok(b["uncovered_amount"] == 336.0 and b["unmeasured_amount"] == 278.0 and gross == 1557.43,
   "E7  the shape of the owner's complaint: of $1,557.43 gross, $58.00 is a MEASURED shortfall and "
   "$278.00 is one device nothing has netted — so the exposure reads $336.00 and says which part "
   "of it is unaccounted, instead of billing the whole $1,557.43 as his own cash",
   f"got uncovered {b['uncovered_amount']} / unmeasured {b['unmeasured_amount']} of {gross}")
ok(b["unmeasured_amount"] == 278.0 and b["uncovered_amount"] == 336.0,
   "E8  ...with the un-netted device inside the total and named, not hidden on either side",
   f"got unmeasured {b['unmeasured_amount']} of {b['uncovered_amount']}")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§F  THE RATCHET — the callers stay wired, and the placeholder cannot come back")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
ok("recovered_amount as _rec" in ENGINE, "F1  the engine dereferences the one home")
ok("recovered_amount as _rec" in UNSOLD, "F2  ...and so does the out-of-pocket read")
ok(not re.search(r"rebate_amount\s*=\s*0\.0", ENGINE),
   "F3  THE PLACEHOLDER IS GONE: no `rebate_amount = 0.0` survives in the engine — that assignment "
   "WAS the defect, a measurement claim nobody made")
ok(re.search(r"rebate_amount,\s*rebate_basis\s*=\s*_rec\.resolve\(", ENGINE),
   "F4  every source folds through resolve(), so amount and basis cannot disagree")
ok(ENGINE.count("_rec.resolve(") >= 3,
   "F5  all three recovery sources are wired, not just the one that surfaced the defect",
   f"found {ENGINE.count('_rec.resolve(')}")
ok("rebate_basis" in ENGINE and '"rebate_basis": rebate_basis' in ENGINE,
   "F6  the basis is WRITTEN to the ledger row — a reader must be able to ask 'do we know?'")
ok(re.search(r'"rebate_amount":\s*\(None if rebate_amount is None', ENGINE),
   "F7  an unmeasured recovery is stored as NULL, never as 0.00")
ok("rebate_measured" in ENGINE and re.search(
    r"sold_flag and rebate_measured and not rebate_got", ENGINE),
   "F8  `discrepancy` requires a MEASUREMENT: an unchecked rebate is no longer an accusation")
ok("_rec.out_of_pocket(" in UNSOLD,
   "F9  the exposure figure nets through the one home rather than re-deriving the subtraction")
ok(not re.search(r'float\(r\.get\("owed_to_vip"\) or r\.get\("owed"\) or 0\)[\s\S]{0,120}?amt \+= one',
                 UNSOLD),
   "F10 the gross cost is no longer added straight to the exposure total")
ok('"amount_not_netted": bool(n_unmeasured)' in UNSOLD
   and '"amount_incomplete": bool(n_unpriced)}' in UNSOLD,
   "F11b the two doubts are two flags: `amount_incomplete` keeps its original meaning (unpriced, "
   "§COMMCALC-29) and `amount_not_netted` is the new one — overloading one boolean broke two "
   "neighbouring proofs, which is how this was caught")
ok('"recovered_amount": round(recovered, 2)' in UNSOLD
   and '"recovery_unmeasured_amount"' in UNSOLD,
   "F11 the summary REPORTS the netting and the unmeasured money, instead of quietly subtracting")
ok("UNMEASURED_NOTE" in HOME_SRC and "recovery_note" in UNSOLD,
   "F12 the wording has one home, so 'not known' reads the same on every screen")
# abs() of a DIFFERENCE is fine (the ePay cross-check compares two amounts). What must never
# appear is abs() applied to a recovery VALUE itself, which is how a charge becomes a credit (§A4);
# the sign is the home's business and the home reads it from config.
ok(not re.search(r"abs\(\s*(rebate_amount|r\.get\(|epay_rebate\s*\))", ENGINE),
   "F13 nothing takes abs() of a recovery VALUE — only of a difference between two of them")
# RULE TWO over THIS change's surface: the recovery path names no carrier column. (A pre-existing
# violation outside it is REPORTED, not silently tolerated: engine.py reads the table
# `raw_ma_commission` literally in the store-account fallback — see §COMMCALC-33.7. It is not
# touched here because it is a different code path, and widening a money fix to reach it is how a
# PR stops being reviewable.)
REC_PATH = ENGINE[ENGINE.index("# WHAT THE CARRIER HAS ALREADY RECOVERED"):ENGINE.index("# offset math")]
for tok in ("raw_ma_commission", "activation_type2", "'rebate'", '"rebate"'):
    ok(tok not in REC_PATH, f"F14 RULE TWO: the recovery path names no carrier column ({tok})")
ok(re.search(r'cfg\.get\("reimbursement_field"\)', REC_PATH),
   "F14b ...it reads the column NAME from the carrier's config row instead")
ok(not re.search(r'^(?!\s*#).*reimbursement_sign\s*=\s*["\']', ENGINE),
   "F15 ...and the sign convention is read from config, never written into the engine")


print(f"\nharness_payables_recovered_amount — {P} passed, {F} failed")
sys.exit(1 if F else 0)
