"""WHAT IS THIS UNPAID ACTIVATION WORTH, AND IS IT A CLAIM AT ALL?

OWNER REPORT 2026-10-20 (verbatim): *"trace teh 4 devices which were aid twice , 200 sales which
matched no commision , it could be a part of the discrepancies to be filed with the carrier and as a
matter of fact they shoudl be a part of the discrepancies if we did not get paid"*.

WHAT HE ASKED FOR ALREADY EXISTS, AND CANNOT BE FILED. The B2B-sold / carrier-paid reconciliation
(§15) resolves every sold activation against the statement and writes the unpaid ones to
`commcalc.discrepancy_results`. Measured read-only on 2026-10-20, every one of those rows carries
`expected_amount` $0.00, `commissionable_mrc` $0.00 and `gap` $0.00 — hardcoded at the write site —
so the Commission Discrepancy hub's open-$ card sums to $0.00 for the whole tenant. **A claim the
platform says is worth nothing is a claim nobody can file.**

THE CLASS, not the instance (owner directive 2026-09-20). The instance is "the carrier-statement
recon writes zero". The class is **two writers answering one question, one of which was wired and the
other left hardcoded** — the sibling defect the index rules already forbid. One table,
`discrepancy_results`, has two authors: the rate-card engine SIZES each claim; the
carrier-statement recon writes the literal `0.0`. A hardcoded `0.0` is also the shape §19.28, §66,
§COMMCALC-09 and §COMMCALC-21 each already outlawed on a different subject: a figure nobody measured,
printed as a measurement. So this module answers the money question for BOTH writers, and neither
composes a figure or a sentence of its own.

NOT EVERY NON-PAYMENT IS A CLAIM, and the writers could not tell the difference. Of the 822 Luxelink
activations the carrier has not paid for (June–October 2026):

  285   sold AFTER the last day that period's statement covers  → not yet due
   33   all on one day, 28 July, for which the statement holds ZERO rows  → a missing upload
  206   explained by the tenant's own payment rules (no bounty on this contract family)
  298   nothing explains them  → the only real claims, ~$55,946 at comparable value

Today all four shapes land as a claim. The first two are facts about the FEED, not about the carrier:
filing them would invoice the carrier for money that is not yet owed and for a statement day nobody
uploaded. A feed gap is reported, never absorbed (house rule) — so this module names it as its own
state and `is_claimable` says no.

ALLOWLIST POSTURE, TWICE (§66). `is_claimable` is an allowlist of states, and `value_is_measured` an
allowlist of bases. A state or basis this module has never heard of — including one missing entirely
— therefore reads as NOT claimable and NOT measured. A caller that forgets to resolve one files
nothing and shows a blank, never a wrong dollar figure in a letter to a carrier.

HOW A CLAIM IS VALUED, and the limit stated in the payload. There is no rate card for this feed, so
the value is a COMPARABLE: the median the carrier ITSELF paid, in the same period, for activations of
the same contract family. It needs `MIN_COMPARABLE_SAMPLE` observations before it will speak — a
median off one or two rows is an anecdote — and below that it returns None with the
`not_measured` basis rather than a guess. It is an order of magnitude to chase, never an invoice, and
`CLAIM_VALUE_CAVEAT` is the one place that sentence lives so every surface says it the same way.

PURE. No I/O, no framework, and no carrier, tenant, market or product name (RULE TWO) — the contract
family is whatever string the caller's own rows carry. Lock: `harness_carrier_claim_value.py`.
"""
from __future__ import annotations

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FOUR SHAPES OF A NON-PAYMENT.  Exactly one of them is a claim.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: Sold, inside a period the statement covers, no payment, and nothing explains it. THE claim.
STATE_OPEN_CLAIM = "open_claim"

#: Sold after the last day that period's statement reaches. The carrier has not reported on it yet,
#: so it is not owed — it is pending. An absence of a statement line, not a refusal to pay.
STATE_NOT_YET_STATEMENTED = "not_yet_statemented"

#: Sold on a day for which the statement holds no rows AT ALL. Other activations that day are unpaid
#: too, for the same reason: nobody uploaded that day. A feed gap, reported never absorbed.
STATE_STATEMENT_DAY_MISSING = "statement_day_missing"

#: A payment rule the tenant declared says this contract family is not paid. A decision, not a finding
#: (§COMMCALC-21) — the money is still shown, but it is not chased.
STATE_EXPLAINED_BY_RULE = "explained_by_rule"

#: ALLOWLIST (§66). Only a state named here may be filed. A state this module has not ruled on — a
#: later migration's invention, or a blank — is NOT claimable, so nothing unexamined reaches a carrier.
_CLAIMABLE_STATES = (STATE_OPEN_CLAIM,)

#: Every state this module speaks for, in the order a reader should see them.
NON_PAYMENT_STATES = (
    STATE_OPEN_CLAIM,
    STATE_STATEMENT_DAY_MISSING,
    STATE_NOT_YET_STATEMENTED,
    STATE_EXPLAINED_BY_RULE,
)

#: The ONE place each state's sentence lives. No caller composes one; a state with no entry here
#: yields the "could not be established" line rather than an invented reason.
STATE_REASON = {
    STATE_OPEN_CLAIM:
        "the carrier's statement covers this day and pays nothing against this activation, and no "
        "payment rule explains it",
    STATE_NOT_YET_STATEMENTED:
        "this activation was sold after the last day the period's statement reaches, so the carrier "
        "has not reported on it yet",
    STATE_STATEMENT_DAY_MISSING:
        "the statement holds no rows at all for the day this activation was sold, so its absence is "
        "a gap in the uploaded feed rather than a refusal to pay",
    STATE_EXPLAINED_BY_RULE:
        "a payment rule you declared says this contract family carries no payout",
}

#: Said when the state is one this module does not rule on. Never a guess.
STATE_REASON_UNKNOWN = "the reason the carrier has not paid could not be established"

#: Short labels for a screen. Same keys, same omission behaviour.
STATE_LABELS = {
    STATE_OPEN_CLAIM: "Open claim",
    STATE_NOT_YET_STATEMENTED: "Not yet statemented",
    STATE_STATEMENT_DAY_MISSING: "Statement day missing",
    STATE_EXPLAINED_BY_RULE: "Explained by your rules",
}


def is_claimable(state) -> bool:
    """True only for a state this module names as filable. ALLOWLIST: an unruled state is not."""
    return str(state or "") in _CLAIMABLE_STATES


def non_payment_reason(state, rule=None) -> str:
    """The one sentence for why the carrier has not paid. A rule's own description wins for the
    rule-explained state, because the tenant wrote it; otherwise the registry's line, and the
    "could not be established" line for a state this module does not rule on."""
    key = str(state or "")
    if key == STATE_EXPLAINED_BY_RULE:
        described = str((rule or {}).get("description") or "").strip()
        if described:
            return described
        keyed = str((rule or {}).get("rule_key") or "").strip()
        if keyed:
            return keyed
    return STATE_REASON.get(key, STATE_REASON_UNKNOWN)


def classify_non_payment(sale_date, statement_last_day, statement_days, rule=None) -> str:
    """Which of the four shapes this unpaid activation is. Called ONLY for an activation already
    established as unpaid — this module never decides whether the carrier paid.

    sale_date            the activation's own date, 'YYYY-MM-DD' (anything shorter is unusable).
    statement_last_day   the latest day that period's statement reaches, 'YYYY-MM-DD', or blank
                         when the statement carries no usable dates.
    statement_days       {'YYYY-MM-DD': rows} for the whole statement. EMPTY means the statement's
                         day-level coverage is UNKNOWN, which is not the same as a day being
                         missing — see below.
    rule                 the tenant's matched payment rule, when one matched.

    ORDER MATTERS, and it is the order of evidence strength. A sale beyond the statement's reach is
    pending whatever else is true of it; a day the statement demonstrably skipped explains its own
    silence before any rule does; a rule speaks only about an activation the statement really did
    cover and really did skip. Dates are compared as ISO strings, which sort chronologically, so no
    parsing can fail.

    ABSENCE OF COVERAGE DATA IS NOT A GAP. `statement_day_missing` may only be asserted when this
    module can SEE which days the statement covers — i.e. `statement_days` is non-empty. A statement
    whose rows carry no usable dates tells us nothing about any particular day, and claiming a gap
    from that would be the very "absence read as a finding" defect the house already outlawed. With
    coverage unknown the activation falls through to the rule, or to an open claim.
    """
    sold = str(sale_date or "")[:10]
    last = str(statement_last_day or "")[:10]
    days = statement_days or {}
    if len(sold) != 10:
        # No usable sale date: neither feed test can be run, so neither may be asserted.
        return STATE_EXPLAINED_BY_RULE if rule else STATE_OPEN_CLAIM
    if len(last) == 10 and sold > last:
        return STATE_NOT_YET_STATEMENTED
    if days:
        try:
            rows = int(days.get(sold) or 0)
        except (TypeError, ValueError):
            rows = 0
        if rows <= 0:
            return STATE_STATEMENT_DAY_MISSING
    if rule:
        return STATE_EXPLAINED_BY_RULE
    return STATE_OPEN_CLAIM


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# WHAT THE CLAIM IS WORTH.  A comparable, with its sample size, or nothing.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

#: The median of what the carrier itself paid for the same contract family in the same period.
BASIS_COMPARABLE_MEDIAN = "comparable_median"

#: No comparable reached `MIN_COMPARABLE_SAMPLE`. The value is None — never 0.00.
BASIS_NOT_MEASURED = "not_measured"

#: ALLOWLIST (§66). Only a basis named here carries a figure a reader may act on.
_MEASURED_BASES = (BASIS_COMPARABLE_MEDIAN,)

#: A median below this many observations is an anecdote, not a comparable. Three is the smallest
#: sample for which a median is not simply one of the two endpoints.
MIN_COMPARABLE_SAMPLE = 3

#: The ONE place the limit of this figure is worded, so no surface overstates it.
CLAIM_VALUE_CAVEAT = (
    "Each figure is the median the carrier itself paid for the same contract family in the same "
    "period — a comparable, not an invoice. Use it to size what is worth chasing; the amount owed is "
    "whatever the carrier's own rate card says."
)


def value_is_measured(basis) -> bool:
    """True only for a basis that carries a real figure. ALLOWLIST: an unruled basis does not."""
    return str(basis or "") in _MEASURED_BASES


def _median(values):
    """The median of a list of floats. Pure; the caller guarantees it is non-empty."""
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def comparable_key(period, contract_type) -> str:
    """The key a comparable is held under. Case- and space-insensitive on the contract family, so
    'BYOD Activation' and 'byod activation ' are one family, and scoped to the period because a
    carrier's rates move between statements."""
    return "{}\x1f{}".format(
        str(period or "").strip().lower(),
        " ".join(str(contract_type or "").split()).lower(),
    )


def build_comparable_index(paid_rows, period_of, type_of, amount_of):
    """{comparable_key: (median, sample_n)} over the activations the carrier DID pay.

    paid_rows   the rows with a real payment — the caller decides what "paid" means (it already owns
                that test) and hands only those in.
    period_of / type_of / amount_of   accessors, so this module needs to know nothing about the
                shape of a statement row or which of its columns are earnings. A zero or negative
                magnitude contributes nothing: the comparable is built from money that moved.
    """
    gathered = {}
    for row in (paid_rows or []):
        try:
            amount = abs(float(amount_of(row) or 0.0))
        except (TypeError, ValueError):
            continue
        if amount <= 0.0:
            continue
        gathered.setdefault(comparable_key(period_of(row), type_of(row)), []).append(amount)
    return {key: (round(_median(vals), 2), len(vals)) for key, vals in gathered.items()}


def claim_value(index, period, contract_type, min_sample=MIN_COMPARABLE_SAMPLE):
    """(amount, basis, sample_n) for one unpaid activation.

    Returns (None, BASIS_NOT_MEASURED, n) whenever the comparable is absent or thinner than
    `min_sample` — the figure is withheld, never rounded down to 0.00, which is the whole point of
    this module. `sample_n` is reported either way so a reader can see how thin the evidence was.
    """
    found = (index or {}).get(comparable_key(period, contract_type))
    if not found:
        return None, BASIS_NOT_MEASURED, 0
    median, sample = found
    try:
        sample = int(sample or 0)
    except (TypeError, ValueError):
        sample = 0
    if sample < max(1, int(min_sample or 1)):
        return None, BASIS_NOT_MEASURED, sample
    return round(float(median), 2), BASIS_COMPARABLE_MEDIAN, sample


def claim_gap(expected, received):
    """What is still owed, or None when the expected side was never measured.

    A gap is a SUBTRACTION, so it cannot exist where one side does not. Returning 0.00 for an
    unmeasured claim is exactly the defect this module was written for, and a received figure alone
    never produces one.
    """
    if expected is None:
        return None
    try:
        return round(float(expected) - float(received or 0.0), 2)
    except (TypeError, ValueError):
        return None


def summarize(entries):
    """Totals over classified non-payments, split by state and honest about what is unpriced.

    entries   dicts carrying at least 'state', 'expected_amount' and 'claim_basis' — the very keys
              the writers persist, so nothing is recomputed to summarise.

    `claimable_amount` sums ONLY measured claim values. `claimable_not_measured` counts the claims
    whose value is withheld, so a reader is never told a total is complete when it is not.
    """
    by_state = {state: 0 for state in NON_PAYMENT_STATES}
    unknown_state = 0
    claimable = 0
    claimable_amount = 0.0
    not_measured = 0
    for entry in (entries or []):
        state = str((entry or {}).get("state") or "")
        if state in by_state:
            by_state[state] += 1
        else:
            unknown_state += 1
        if not is_claimable(state):
            continue
        claimable += 1
        if value_is_measured((entry or {}).get("claim_basis")):
            try:
                claimable_amount += float((entry or {}).get("expected_amount") or 0.0)
            except (TypeError, ValueError):
                not_measured += 1
        else:
            not_measured += 1
    return {
        "by_state": by_state,
        "states_not_ruled_on": unknown_state,
        "claimable": claimable,
        "claimable_amount": round(claimable_amount, 2),
        "claimable_not_measured": not_measured,
        "value_caveat": CLAIM_VALUE_CAVEAT,
    }
