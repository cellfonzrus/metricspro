"""ONE home for "how much has the carrier ALREADY recovered against this device, and do we know?"

Index §COMMCALC-33. Lock: `backend/harness_payables_recovered_amount.py`.

OWNER REPORT 2026-10-26, verbatim: *"the payments for all phones automatically are done from
commission and rebates received and auto adjusted"* — said against a banner reading **"2656 unsold
devices must be paid OUT OF POCKET — $544,447.78 ... funded from your own cash rather than from the
sale"**.

He is right, and the netting was never missing. It is on the row the build already reads:
`commcalc.raw_ma_commission.rebate` is NEGATIVE on 3,442 of 4,495 Luxelink lines, totalling
**-$834,234.25** — the device cost deducted from his commission, line by line. MEASURED 2026-10-26
against the live ledger, on the same 2,656 devices the banner named: 1,070 of them ($228,779.70) are
FULLY deducted, 957 are partly deducted (owed $305,380.43 against $277,294.00 deducted) and 37
($10,287.65) carry no deduction at all. The unrecovered figure is about $38,374, not $544,447.78 — an
overstatement of roughly fourteen times, in the direction that tells an owner he is short half a
million dollars of cash.

THE CLASS, and it is not "we forgot to read a column".

**A placeholder that returns a NUMBER is a claim.** `payables/engine.py` held this:

    elif rebate_source == "imei_match":
        mt = cfg.get("reimbursement_match_table")
        if mt:   # pending until a Total reimbursement report exists -> stays 0 when unset
            rebate_amount = 0.0

`0.0` is not "not wired yet". It is the sentence *nothing was recovered against this device*, written
into every row of the ledger, and every surface downstream then read it as a measurement: 1,400 lines
went to `discrepancy` (the status whose legend says the dealer is carrying the cost) and the
out-of-pocket banner presented the gross as cash. The honest value for an unwired source is
**unknown**, and a screen must say so rather than bill it to the owner.

So this module separates three states that the single float could not express:

  * **recovered** — measured, and the amount is known.
  * **uncovered** — measured, and the recovery falls short of the cost. THIS is out of pocket.
  * **unmeasured** — no recovery source is wired, or the declared one could not be read. Not zero.

`is_measured(basis)` is an ALLOWLIST, the same shape as §66's `component_is_evidence`: a basis this
module has never ruled on reads as NOT measured, so a future source cannot quietly become evidence by
appearing.

THE SIGN IS DECLARED, NEVER GUESSED. On the MA statement a recovery is a DEDUCTION and arrives
negative; on the Boost/VIP ledger and the ePay feed a reimbursement arrives positive. A reader that
took `abs()` would turn a genuine charge into a credit, and a reader that kept `> 0` reads a
deduction as nothing — which is exactly how $834,234.25 of netting went unseen. So the convention is
a config value per carrier (`payable_source_map.reimbursement_sign`), and an UNDECLARED sign keeps
the historic meaning (positive is the recovery) rather than inventing one.
"""

#: A recovery arrives as a NEGATIVE amount — it is a deduction taken on the carrier's own statement
#: (the MA commission feed's `rebate`). The magnitude is the amount recovered.
SIGN_NEGATIVE = "negative_is_recovery"
#: A recovery arrives as a POSITIVE amount — a reimbursement paid to the dealer (Boost/VIP, ePay).
#: This is the historic meaning and stays the default when nothing is declared.
SIGN_POSITIVE = "positive_is_recovery"

SIGNS = (SIGN_NEGATIVE, SIGN_POSITIVE)

#: What answered. Only the bases in MEASURED_BASES are a measurement; see `is_measured`.
BASIS_DECLARED_FIELD = "declared_field"      # the device's own source row, per `reimbursement_field`
BASIS_ASSET_LEDGER = "asset_ledger"          # Boost/VIP: classified off the consignment row
BASIS_EPAY = "epay"                          # the ePay reimbursement feed
BASIS_UNMEASURED = "unmeasured"              # nothing is wired — NOT a recovery of $0.00
BASIS_UNREADABLE = "source_unreadable"       # a source IS declared and the read failed (§COMMCALC-29)

#: THE ALLOWLIST (§66). A basis absent from this tuple is not a measurement, so a source added later
#: cannot become evidence merely by existing. `harness_payables_recovered_amount.py` §D locks it.
MEASURED_BASES = (BASIS_DECLARED_FIELD, BASIS_ASSET_LEDGER, BASIS_EPAY)

#: The sentence a surface shows where no recovery source is wired. One wording, one home: an
#: unmeasured recovery must never be presented as money the owner has funded.
UNMEASURED_NOTE = ("No rebate or deduction source is configured for this carrier, so this figure "
                   "has NOT been reduced by what the carrier may already have recovered against "
                   "these devices. It is the full cost, not a measured shortfall.")

#: The sentence for the netting itself, so "deducted from commission" reads the same everywhere.
RECOVERED_NOTE = ("Already recovered by the carrier — deducted from commission or paid back as a "
                  "rebate. This is not funded from your own cash.")


def is_measured(basis) -> bool:
    """True only for a basis this module has RULED is a measurement (§66 basis-is-evidence).

    Fail-CLOSED on purpose, which is the opposite of `ledger_status.is_payable`: there, an unknown
    status must stay payable so money cannot vanish from a total; here, an unknown basis must not
    count as proof of a recovery, because that would delete a real liability.
    """
    return str(basis or "") in MEASURED_BASES


def sign_of(cfg) -> str:
    """The declared sign convention for this carrier's recovery amounts.

    An unrecognised or absent declaration keeps the historic meaning (positive is the recovery)
    instead of guessing — a guessed sign is how a charge becomes a credit.
    """
    want = str((cfg or {}).get("reimbursement_sign") or "").strip()
    return want if want in SIGNS else SIGN_POSITIVE


def _num(v):
    """A float, or None when the value is not a number at all (NOT 0.0 — see the class above)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def from_value(value, sign=SIGN_POSITIVE):
    """PURE: the recovery this raw statement value represents, under a DECLARED sign convention.

    Returns the amount as a non-negative float, 0.0 when the value exists but points the other way
    (a positive line on a deduction feed is something else, not a recovery), or None when there is
    no number to read at all — which is unmeasured, not zero.
    """
    f = _num(value)
    if f is None:
        return None
    if sign == SIGN_NEGATIVE:
        return round(-f, 2) if f < 0 else 0.0
    return round(f, 2) if f > 0 else 0.0


def from_row(row, field, sign=SIGN_POSITIVE):
    """The recovery carried by the device's OWN source row, per the declared column.

    `field` unset means no column was declared, which is unmeasured — the whole point of this module.
    A declared column that the row does not carry is also unmeasured rather than zero: the row cannot
    answer a question nobody asked it.
    """
    col = str(field or "").strip()
    if not col or not isinstance(row, dict) or col not in row:
        return None
    return from_value(row.get(col), sign)


def resolve(amount, basis):
    """Fold one source's answer into the (amount, basis) a ledger row carries.

    An amount of None forces the basis to UNMEASURED whatever the caller believed, so a source that
    returned nothing cannot leave a measured basis stamped on the row behind it.
    """
    if amount is None:
        # An UNREADABLE source keeps its own basis: "the declared feed broke" and "nothing was
        # declared" are different facts and §COMMCALC-29 exists because collapsing them hid a
        # $595,125.59 exposure behind the word `unconfigured`.
        return None, (BASIS_UNREADABLE if basis == BASIS_UNREADABLE else BASIS_UNMEASURED)
    if not is_measured(basis):
        return None, (basis if basis in (BASIS_UNMEASURED, BASIS_UNREADABLE) else BASIS_UNMEASURED)
    return round(float(amount), 2), basis


def out_of_pocket(owed, recovered, basis=BASIS_UNMEASURED):
    """What this device costs the owner once the carrier's recovery is taken off, and whether it was.

    Returns {amount, recovered, basis, measured, netted, covered}:
      * `measured`/`netted` False -> the recovery is UNKNOWN, so `amount` is the GROSS cost. The
        liability is real and known; it is the credit against it that nobody measured. A screen must
        label such a figure as not netted (see `UNMEASURED_NOTE`) rather than present it as final.
      * `covered` True -> the recovery meets or exceeds the cost; nothing is out of pocket.

    WHY UNMEASURED KEEPS THE GROSS, and this is the half I got wrong first. Dropping the device from
    the total instead would be the SAME defect in the other direction: a second claim nobody
    measured, and on the Boost/VIP aging report — whose rows carry no recovery basis at all — it
    would have zeroed a real exposure. An unnetted liability is reported in full and flagged; it is
    never silently settled, and never silently dropped.

    A recovery larger than the cost does NOT become a negative out-of-pocket: the surplus is a
    question about the statement, not cash back on this device, so it is clamped at zero and the
    overage stays visible as `recovered`.
    """
    o = _num(owed)
    if not is_measured(basis) or recovered is None:
        return {"amount": o, "recovered": None, "basis": basis or BASIS_UNMEASURED,
                "measured": False, "netted": False, "covered": None}
    rec = max(0.0, round(float(recovered), 2))
    if o is None:
        # The cost is unknown but the recovery is real: report the recovery, never a $0.00 bill.
        return {"amount": None, "recovered": rec, "basis": basis, "measured": True,
                "netted": True, "covered": None}
    gap = round(max(0.0, o - rec), 2)
    return {"amount": gap, "recovered": rec, "basis": basis, "measured": True,
            "netted": True, "covered": gap <= 0.0}


def split(rows, owed_of=None, recovered_of=None, basis_of=None):
    """The exposure over a set of devices, and how much of it was actually netted.

    Returns {devices, recovered_amount, uncovered_amount, unmeasured_devices, unmeasured_amount,
    covered_devices, amount_incomplete}. ONE convention, shared with `out_of_pocket`:
    `uncovered_amount` is the whole liability — the GAP on the devices whose recovery was measured
    plus the GROSS on the devices whose recovery was not — and `unmeasured_amount` says how much of
    that figure has never been netted against a rebate. Nothing is dropped from the total and
    nothing is quietly settled; the two numbers together let a screen say exactly what it knows.
    """
    owed_of = owed_of or (lambda r: r.get("owed"))
    recovered_of = recovered_of or (lambda r: r.get("rebate_amount"))
    basis_of = basis_of or (lambda r: r.get("rebate_basis"))
    rec = unc = unm_amt = 0.0
    n_unm = n_cov = 0
    rows = rows or []
    for r in rows:
        v = out_of_pocket(owed_of(r), recovered_of(r), basis_of(r))
        unc += v["amount"] or 0.0
        if not v["measured"]:
            n_unm += 1
            unm_amt += v["amount"] or 0.0
            continue
        rec += v["recovered"] or 0.0
        if v["covered"]:
            n_cov += 1
    return {"devices": len(rows), "recovered_amount": round(rec, 2),
            "uncovered_amount": round(unc, 2), "unmeasured_devices": n_unm,
            "unmeasured_amount": round(unm_amt, 2), "covered_devices": n_cov,
            "amount_incomplete": bool(n_unm)}
