"""THE ONE TEST: is this processor-feed row money the carrier TOOK BACK?

OWNER ASK 2026-10-06, verbatim: *"create a report for commisison withodimng which is a payment type
in commimssion details reports uploaded everymonth — ti should be under flags which shows the
acrrier has not paid the commimssion"*.

THE DEFECT THIS NAMES, AND THE CLASS IT IS AN INSTANCE OF
─────────────────────────────────────────────────────────
A `CHARGEBACK` flag type has been registered since migration 002 and its detector has existed in
`commcalc/flags.py` just as long. It has NEVER FIRED. Measured live 2026-10-06, house org:

    commcalc.flags       flag_type = 'CHARGEBACK'                      0 rows, ever
    raw_payment_detail   payment_type = 'Commission Withholding'      474 rows, $7,123.39 taken back

Both numbers are correct, and that is the defect. The detector asks
`row['category'] == 'Chargeback'`, and `Chargeback` is a category **no org has ever been able to
declare**: `commcalc.payment_categories` is a free-text map the tenant fills in, and
`pay_data_quality.PLACEABLE_CATEGORIES` already records the consequence out loud — *"the calculator
tests for it and `payment_categories` has never contained it, so that bucket has always been $0"*.
The house org HAS mapped the payment type; it mapped it to `Commission`, which is the only honest
thing a person could pick from a list with no clawback on it.

So the class is NOT "Boost's withholding rows are missed". The class is:

    **A CLAWBACK WAS RECOGNISED BY A CATEGORY NAME NOBODY CAN DECLARE, SO IT WAS RECOGNISED
    NOWHERE — AND, BEING DECLARED AS EARNINGS, IT NETTED SILENTLY INTO COMMISSION.**

Fix the class: recognise a clawback by **the direction the processor moved the money**. Every
processor feed carries that, it is already a registered fact, it needs no tenant to declare
anything, and a feed that renames its withholding line next quarter is still caught — which is the
failure mode `pay_data_quality` measured at $288,813 for quarter-named promos.

ONE FACT, ONE HOME, DEREFERENCED
────────────────────────────────
The sign rule is NOT restated here. `processor_ledger.FEED_SHAPES` is its home — per feed, verified
against live rows, with the reasoning written down — and `processor_ledger.classify_amount` is the
one function that applies it. This module imports both. If a third feed is added there, clawback
detection follows it with no edit here, and `harness_clawback_lock.py` FAILS THE BUILD if any
module under `backend/app` goes back to testing a literal clawback category string, or if a second
sign table appears.

WHAT A CLAWBACK IS, STATED ONCE
───────────────────────────────
A feed row is a commission clawback when BOTH hold:

  1. the processor DEBITED it — `classify_amount` under that feed's own convention returns a debit;
  2. the org's own category map places it in a **pay** category — money that would otherwise have
     reached a rep or the commission line.

Condition 2 is what keeps a device purchase, a SIM charge or a terminal fee out of the report: those
are debits too, and they are not commission coming back. The caller hands in the org's own map
(RULE TWO — no carrier, tenant or payment-type name appears in this file), and a row whose type the
org has NEVER mapped is reported as `undeclared`, never guessed and never dropped: §19.26's
"missing beats wrong" applied to a debit nobody classified.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
─────────────────────────────────────────
  · **It books nothing and pays nobody.** It reclassifies no stored row and changes no total any
    payout or P&L path reads. The live finding that `Commission Withholding` is DECLARED as
    `Commission` and therefore nets into commission earnings is REPORTED (see
    `declaration_findings`) — never "fixed" by code that hides it. Re-declaring it moves money and
    is the owner's call.
  · **It does not re-spell the statement path.** `commcalc.commission_bucket`'s `chargebacks` bucket
    classifies an uploaded STATEMENT's line label and books it to the P&L. It answers the same
    question for a different source, and wiring it to the sign rule would move money, so it is
    excused here and named in the PR rather than silently left behind.

PURE: stdlib plus `processor_ledger`'s two registered facts. No I/O, no framework import. Proven
DB-free by `backend/harness_clawback.py`.

Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §55.
"""
from __future__ import annotations

from app.modules.commcalc.processor_ledger import FEED_SHAPES, classify_amount

#: The pay categories a clawback can come back OUT of. This is not a new vocabulary: it is
#: `pay_data_quality.PLACEABLE_CATEGORIES` minus the phantom `Chargeback` entry that this module
#: exists to replace. Dereferenced, not copied — see `pay_categories()`.
_PHANTOM = "chargeback"

#: Why a debit is not counted. The reason is part of the report, never a silent default.
SKIP_UNDECLARED = "type_not_declared_by_org"
SKIP_NOT_PAY = "declared_outside_the_pay_categories"

SKIP_REASONS = {
    SKIP_UNDECLARED: (
        "The processor took this money back under a payment type this org has never mapped to a "
        "pay category. It may or may not be commission coming back — the feed does not say, and "
        "this report will not guess. Mapping the type settles it with a config row."),
    SKIP_NOT_PAY: (
        "The processor took this money back, but the org declares the payment type as something "
        "other than pay — a device, a fee, a terminal charge. Correctly outside a commission "
        "clawback report."),
}


def pay_categories():
    """The pay categories, DEREFERENCED from the pay engine's own list rather than restated.

    `pay_data_quality.PLACEABLE_CATEGORIES` is the one statement of which buckets the calculator
    reads. The phantom `Chargeback` entry is dropped: it is the category nobody can declare, and
    counting a clawback as coming back out of it would make this module circular.

    Imported lazily so this file stays importable by a lock that proves it DB-free.
    """
    from app.modules.commcalc.pay_data_quality import PLACEABLE_CATEGORIES
    return tuple(c for c in PLACEABLE_CATEGORIES if str(c).strip().lower() != _PHANTOM)


def feed_shape(feed):
    """The registered shape for a feed key, or None. One home: `processor_ledger.FEED_SHAPES`."""
    return FEED_SHAPES.get(str(feed or "").strip().lower())


def debit_of(row, feed):
    """PURE. How much this row was DEBITED under its own feed's sign convention; 0.0 for a credit.

    An unregistered feed returns 0.0 rather than assuming a convention: guessing the sign is how a
    clawback report would turn earnings into losses.
    """
    shape = feed_shape(feed)
    if not shape:
        return 0.0
    debit, _credit = classify_amount(row.get(shape["amount_col"]), shape["credit_positive"])
    return round(debit, 2)


def row_type(row, feed):
    """PURE. The payment/order type string this feed names, via the registered column."""
    shape = feed_shape(feed)
    if not shape:
        return ""
    return str(row.get(shape["type_col"]) or "").strip()


def classify_row(row, category_of, feed="epay", pay_cats=None):
    """PURE. One feed row → what it is, for a clawback report.

    `category_of` is the org's OWN map (a dict or a callable) from the feed's type string to that
    org's pay category — never a copy of anybody's map in here. Returns:

        {"clawback": bool, "amount": float, "type": str, "category": str|None,
         "skip_reason": str|None}

    `amount` is the DEBITED magnitude, always non-negative, so a caller never has to remember which
    way the feed's sign points. `category` is None when the org has never declared the type.
    """
    amount = debit_of(row, feed)
    ptype = row_type(row, feed)
    if amount <= 0:
        return {"clawback": False, "amount": 0.0, "type": ptype, "category": None,
                "skip_reason": None}

    raw = category_of(ptype) if callable(category_of) else (category_of or {}).get(ptype)
    cat = str(raw or "").strip()
    if not cat or cat.lower() in ("unknown", "none", "null"):
        return {"clawback": False, "amount": amount, "type": ptype, "category": None,
                "skip_reason": SKIP_UNDECLARED}

    allowed = {str(c).strip().lower() for c in (pay_cats if pay_cats is not None else pay_categories())}
    if cat.lower() not in allowed:
        return {"clawback": False, "amount": amount, "type": ptype, "category": cat,
                "skip_reason": SKIP_NOT_PAY}

    return {"clawback": True, "amount": amount, "type": ptype, "category": cat,
            "skip_reason": None}


def is_clawback(row, category_of, feed="epay", pay_cats=None):
    """PURE. The one-line test every caller uses. See `classify_row` for the reasoning."""
    return classify_row(row, category_of, feed=feed, pay_cats=pay_cats)["clawback"]


def declaration_findings(rows, category_of, feed="epay", pay_cats=None):
    """PURE. What the org's declarations get WRONG about the money the processor took back.

    This is the evidence-first half, and the reason this module reports rather than repairs. Three
    findings, each a list of `{type, rows, amount, note}` ordered by money at stake:

      `undeclared`   debits under a type the org has never mapped. Unknowable, not zero.
      `as_earnings`  debits under a type the org declares as a PAY category. These ARE the
                     clawbacks this report counts — and because they are declared as earnings, the
                     same rows net into the pay engine's commission bucket with no sign of having
                     been taken back. That is a MONEY statement, so it is surfaced for a ruling
                     rather than silently re-declared here.
      `outside_pay`  debits the org declares as something other than pay. Correctly excluded; listed
                     so "nothing here" can be told apart from "nothing looked".
    """
    cats = tuple(pay_cats if pay_cats is not None else pay_categories())
    buckets = {"undeclared": {}, "as_earnings": {}, "outside_pay": {}}
    for row in (rows or []):
        c = classify_row(row, category_of, feed=feed, pay_cats=cats)
        if c["amount"] <= 0:
            continue
        key = ("as_earnings" if c["clawback"]
               else "undeclared" if c["skip_reason"] == SKIP_UNDECLARED else "outside_pay")
        d = buckets[key].setdefault(c["type"], {"type": c["type"], "category": c["category"],
                                                "rows": 0, "amount": 0.0})
        d["rows"] += 1
        d["amount"] = round(d["amount"] + c["amount"], 2)

    def _out(key):
        return sorted(buckets[key].values(), key=lambda d: (-d["amount"], d["type"]))

    as_earnings = _out("as_earnings")
    return {
        "undeclared": _out("undeclared"),
        "as_earnings": as_earnings,
        "outside_pay": _out("outside_pay"),
        "undeclared_note": SKIP_REASONS[SKIP_UNDECLARED],
        "outside_pay_note": SKIP_REASONS[SKIP_NOT_PAY],
        "as_earnings_note": (
            "Every dollar below was taken back by the processor while declared under a category "
            "the pay engine reads as EARNINGS, so the same rows also net into commission with "
            "nothing on screen saying they were clawed back. Changing that declaration moves "
            "money and is not done by this report."
            if as_earnings else None),
    }
