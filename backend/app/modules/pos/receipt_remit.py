"""THE pay-by-bank details a receipt may carry (owner ask 2026-10-21, index §POS-06).

Owner, verbatim: *"need to add the option of adding the payment information to the customer when
creating the receipt for the customer as a check mark, two boxes to fill out routing number and
account number to be sent to the customer, tehy will be saved in the register set up"*.

WHAT THIS MODULE IS THE ONE HOME FOR — "which bank account does this store tell a customer to pay
into, and is it on THIS receipt". Four facts live here and nowhere else:

  * WHICH SETTINGS HOLD IT — `KEYS`. The sealer, the sweep, the masker, the resolver and the build
    lock all dereference this tuple, so "these two settings are bank numbers" is stated once.
  * WHAT A VALID NUMBER IS — `clean_routing` / `clean_account`. A routing number is nine digits AND
    passes the ABA checksum; an account number is 4–17 digits. A receipt is the one place a wrong
    number is expensive: the customer pays, the money goes nowhere, and nobody finds out until they
    chase it. So a number that cannot be a number is REFUSED at the save, never printed.
  * HOW IT IS STORED — `seal_setting_value` / `open_setting_value`. `pos.pos_settings` is read by
    `GET /pos/settings`, which any signed-in POS user may call, and on 2026-10-12 this house found
    two bank routing numbers sitting in plain text in production (index §CORE-04). So the value is
    encrypted at rest through `core.crypto`, this is the ONE sealer, and
    `core.encryption_backfill.SWEEPS` carries the family — the build fails otherwise.
  * WHAT PRINTS — `block`. One paragraph, rendered by `sale_receipt.slip_model` as an ordinary
    footer block, so the paper copy, the emailed copy and the printer's own byte stream carry the
    same words with no renderer change (§POS-04 put the content in one home for exactly this).

NOT sensitive the way an employee's own account is: these are the BUSINESS's numbers and the whole
point is that the customer reads them. What encryption buys here is that they are not casually
readable by every POS login, and that a number printed on a receipt was put there by somebody who
holds the settings right — a cashier who could type one could redirect a customer's payment.

RULE TWO: no bank, carrier or tenant is named anywhere in this module. A number is a number.
"""
from __future__ import annotations

from app.core import crypto
from app.modules.pos import pos_config_effective as _poscfg

#: The POS setting that holds the routing number of the account a customer pays into.
ROUTING_KEY = "receipt_remit_routing_number"
#: The POS setting that holds that account's number.
ACCOUNT_KEY = "receipt_remit_account_number"
#: THE declaration of which POS settings hold bank numbers. Everything dereferences this.
KEYS: tuple[str, ...] = (ROUTING_KEY, ACCOUNT_KEY)

#: A routing number is exactly this many digits (the ABA/RTN format).
ROUTING_DIGITS = 9
#: The range an account number may fall in. Shorter cannot be an account; longer is not issued.
ACCOUNT_MIN_DIGITS = 4
ACCOUNT_MAX_DIGITS = 17

#: What the block says above the two numbers. House default, overridable per org/store as a setting
#: would be if a tenant ever asks; today one sentence serves every tenant and names none of them.
HEADING = "Pay by bank transfer"

#: The character a masked number is written with. A screen only ever receives the mask, so a value
#: carrying this character is a mask that has been handed back — see `_refuse_mask`.
MASK_CHAR = "\u2022"


class RemitError(ValueError):
    """A number that cannot be a number. Carries the sentence the saver shows the person."""


def digits(value) -> str:
    """Only the digits of what was typed. People type routing numbers with spaces and dashes and
    paste them out of letters; the stored fact is the digits."""
    return "".join(ch for ch in str("" if value is None else value) if ch.isdigit())


def routing_checksum_ok(value) -> bool:
    """The ABA checksum: 3·(d1+d4+d7) + 7·(d2+d5+d8) + 1·(d3+d6+d9) is a multiple of ten.

    This is arithmetic over a published format, not a rule about any one bank — every real routing
    number satisfies it and a transposed pair of digits almost never does, which is the typo that
    would otherwise reach a customer's receipt.
    """
    d = digits(value)
    if len(d) != ROUTING_DIGITS:
        return False
    n = [int(c) for c in d]
    weighted = 3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + (n[2] + n[5] + n[8])
    return weighted % 10 == 0


def _refuse_mask(value) -> None:
    """Refuse a value that is the MASK we showed on a screen, handed back to us.

    This is not hypothetical and it is not cosmetic. Every screen reads these numbers masked, so the
    one thing a save is most likely to receive by accident is '••••7890' — and `digits()` would
    quietly reduce that to '7890', which is a perfectly valid four-digit account number. The real
    account would be replaced by its own last four, every receipt printed after that would carry a
    number that cannot be paid into, and nothing would have looked wrong at any point. So a value
    carrying a mask character is refused outright, before length or checksum.
    """
    if MASK_CHAR in str("" if value is None else value):
        raise RemitError("That is the hidden version we show on screen, not the number itself — "
                         "type the whole number.")


def clean_routing(value) -> str:
    """The routing number to store, or RemitError with a sentence a person can act on. Blank clears
    it — a store that stops taking bank payments empties the box."""
    _refuse_mask(value)
    d = digits(value)
    if not d:
        return ""
    if len(d) != ROUTING_DIGITS:
        raise RemitError(f"A routing number is {ROUTING_DIGITS} digits — that one has {len(d)}.")
    if not routing_checksum_ok(d):
        raise RemitError("That routing number fails its own check digit, so it is not a real one — "
                         "check it against the bank's own paperwork.")
    return d


def clean_account(value) -> str:
    """The account number to store, or RemitError with a sentence a person can act on. Blank clears
    it. There is no checksum to lean on here, so length is the only honest check."""
    _refuse_mask(value)
    d = digits(value)
    if not d:
        return ""
    if not (ACCOUNT_MIN_DIGITS <= len(d) <= ACCOUNT_MAX_DIGITS):
        raise RemitError(f"An account number is between {ACCOUNT_MIN_DIGITS} and "
                         f"{ACCOUNT_MAX_DIGITS} digits — that one has {len(d)}.")
    return d


def mask(value) -> str:
    """Last four real, the rest hidden — what a screen shows. A value of four digits or fewer is
    hidden in full: there is nothing safe to reveal as "the rest" of it."""
    d = digits(value)
    if not d:
        return ""
    if len(d) <= 4:
        return "•" * len(d)
    return "•" * (len(d) - 4) + d[-4:]


def seal_setting_value(value):
    """THE sealer for a stored bank number: cleaned to digits, then encrypted at rest.

    Every writer and the backfill sweep call THIS, never `crypto.encrypt` of their own — the §CORE-04
    lesson is that a sweep holding its own primitive is a second home for what sealing means.
    """
    d = digits(value)
    return "" if not d else crypto.encrypt(d)


def open_setting_value(value) -> str:
    """A stored bank number as digits again. A value sealed with a key this server no longer has
    reads as blank rather than raising: a receipt still prints, just without the block."""
    if value is None or value == "":
        return ""
    out = crypto.decrypt(value if isinstance(value, str) else str(value))
    return digits(out)


def declared(rows, store_code: str = "") -> dict:
    """What this store tells a customer to pay into: `{routing, account, available}`.

    Resolved through `pos_config_effective.effective` — the server's one home for the store-override
    → org-default → declared-default rule — so a chain of shops with one bank account declares it
    once and a store with its own says so on its own row. `available` is false unless BOTH numbers
    are there: half of a bank account is not a payment instruction, and a receipt that printed one
    line would read as if the other had been forgotten.
    """
    routing = open_setting_value(_poscfg.effective(rows, ROUTING_KEY, store_code))
    account = open_setting_value(_poscfg.effective(rows, ACCOUNT_KEY, store_code))
    return {"routing": routing, "account": account,
            "available": bool(routing and account)}


def masked_view(rows, store_code: str = "") -> dict:
    """The same two numbers as a screen may show them, plus whether a receipt could carry them."""
    d = declared(rows, store_code)
    return {"routing_masked": mask(d["routing"]), "account_masked": mask(d["account"]),
            "available": d["available"]}


def block(declared_v, *, include: bool) -> str:
    """The paragraph a receipt prints, or "" when it does not.

    Returns "" unless the store asked for it on THIS receipt AND both numbers are there, so a tick
    can never produce a block with a number missing from it.
    """
    if not include or not (declared_v or {}).get("available"):
        return ""
    return (f"{HEADING}\n"
            f"Routing number: {declared_v['routing']}\n"
            f"Account number: {declared_v['account']}")
