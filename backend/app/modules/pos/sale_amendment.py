"""THE one home for "what would it take to ring this sale again" (§POS-12).

Owner 2026-10-29, verbatim: *"im not able to edit the receipt ot add more tiems to teh same
rec3ipt"*.

He was right, and nothing was broken: the capability did not exist. The register has exactly one
write path for a finished sale (`POST /pos/sales/{id}/void`), `pos.checkout` and `pos.void_sale` are
the only two sale functions in the database (mig 725), and the Sale Log's item table is display-only.

WHY THIS IS A RE-RING AND NOT AN EDIT. A tendered sale is a financial record. The customer is
holding a printed receipt for a total, and a tax figure was struck against that total. Rewriting the
row in place would leave the paper and the record disagreeing with nothing to say which one
happened — so the original is VOIDED and a NEW transaction replaces it, carrying every original line
plus whatever is added. That is what RQ itself does, and the void path already does the hard half
correctly: mig 725's `pos.void_sale` puts serialised units back to `in_stock` and non-serialised
quantities back on hand, so no stock is lost or duplicated by re-ringing.

THE CLASS, NOT THE INSTANCE. The missing fact is not "the Sale Log needs a button". It is **"may
this sale be rung again, and with what on it"**, and three callers ask it: the Sale Log that offers
the button, the endpoint that allows or refuses, and the till that says what it is about to do. One
answer, or a week-old sale is re-rungable on one screen and refused on another.

ONE CART VOCABULARY. A re-ring hands the register a cart, and §POS-01 already owns what a cart line
IS. This module spells no field of its own: it DEREFERENCES `held_sale.LINE_FIELDS`,
`held_sale.CONTEXT_FIELDS` and `held_sale.clean_cart`, so a cart restored from a sale and a cart
restored from a suspended hold are the same shape arriving at one checkout.

MONEY VERBATIM, TAX RE-DERIVED — §POS-01.4's bargain, for its reason. The agreed unit price, the
override it overrode, the discount and the serial are what the cashier agreed with the customer and
come back exactly. The tax RATE is a legal fact about the store, not a figure anyone negotiated, so
it is deliberately NOT carried: the register re-derives it on load, exactly as it does for a restored
hold and when the store is switched mid-sale. Carrying it would re-ring last month's rate today.

WHY THERE IS A WINDOW AT ALL. A void is stamped when it happens, not when the sale was rung. Voiding
a week-old sale therefore takes its money out of a POS day somebody has already read and puts the
replacement in today's. The window is how long that is allowed, and it is DECLARED per org
(`pos_settings.sale_reopen_window_days`, house default `HOUSE_WINDOW_DAYS`) rather than decided
here — RULE TWO: no tenant's answer lives in code.

PURE. No DB handle, no clock (the caller stamps `now`), no org id, no tenant, carrier or product
name. Proof: `backend/harness_pos_sale_amendment.py`.
"""
from datetime import datetime, timezone

from . import held_sale as _hs

#: The house answer to "how many days may a rung sale still be re-rung", when the org has declared
#: none. One day — the sale's own business day and no further, so a figure a manager has already
#: read does not move under them. A tenant raises it with a config row, never a code change.
HOUSE_WINDOW_DAYS = 1

#: The setting an org declares its own window in. Named here so the endpoint reads the key from the
#: one home rather than spelling it beside its own default.
WINDOW_SETTING = "sale_reopen_window_days"

#: Line fields `pos.sale_items` holds that a cart line deliberately does NOT carry over. `tax_rate`
#: and `tax_value` are re-derived from the store's live rate (see MONEY VERBATIM above); `is_taxable`
#: is a property of the product, which the register re-reads, and `sale_items` does not record it.
NOT_CARRIED = ("tax_rate", "tax_value", "is_taxable")


class AmendmentError(ValueError):
    """A refused re-ring. The message is written to be shown at the till."""


def _when(value):
    """A sale's own timestamp as an aware datetime, or `None` when it cannot be read. A date that
    will not parse is never treated as recent — `refusal` turns `None` into a refusal."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def declared_window(value):
    """The declared window, as a whole number of days. A blank or unreadable declaration falls back
    to the house default rather than to "forever" — an unreadable setting must not widen what may be
    rewritten."""
    if value in (None, ""):
        return HOUSE_WINDOW_DAYS
    try:
        n = int(value)
    except (TypeError, ValueError):
        return HOUSE_WINDOW_DAYS
    return max(n, 0)


def age_days(sale, now=None):
    """How many whole days ago this sale was rung, or `None` when its date cannot be read."""
    rung = _when((sale or {}).get("created_at"))
    at = _when(now) or datetime.now(timezone.utc)
    if rung is None:
        return None
    return max((at.date() - rung.date()).days, 0)


def refusal(sale, now=None, window_days=None):
    """Why this sale may NOT be rung again, or `None` when it may.

    A sentence rather than a code: every caller shows it to a cashier, and a second mapping from
    code to sentence is a second chance for two screens to word one refusal differently.
    """
    win = declared_window(window_days)
    s = sale or {}
    if not s.get("id"):
        return "That sale could not be found."
    if s.get("status") == "voided" or s.get("voided_at"):
        return (f"Transaction #{s.get('transaction_id') or '?'} is already voided — its items are "
                "back on hand, so it cannot be rung again. Start a new sale instead.")
    if s.get("status") not in (None, "", "completed"):
        return (f"Transaction #{s.get('transaction_id') or '?'} is {s.get('status')}, "
                "and only a completed sale can be rung again.")
    age = age_days(s, now)
    if age is None:
        return (f"Transaction #{s.get('transaction_id') or '?'} does not carry a readable date, so "
                "there is no way to tell whether it is still within the window for re-ringing.")
    if age > win:
        day = "day" if win == 1 else "days"
        return (f"Transaction #{s.get('transaction_id') or '?'} was rung {age} days ago, and a sale "
                f"can only be rung again within {win} {day}. Re-ringing it now would move its money "
                "out of a day that has already been reported. Ring the extra items as their own "
                "sale instead.")
    return None


def cart_from(items):
    """The sale's own lines, as the register's cart.

    Cleaned by `held_sale.clean_cart` — the §POS-01 vocabulary — so this is the same shape a
    suspended hold restores into. `NOT_CARRIED` fields are dropped before cleaning, which is what
    makes the register re-derive the tax rather than inheriting the original sale's.
    """
    rows = items or []
    if not rows:
        raise AmendmentError("That sale has no items, so there is nothing to ring again.")
    out = []
    for it in rows:
        line = {k: v for k, v in dict(it or {}).items()
                if k in _hs.LINE_FIELDS and k not in NOT_CARRIED}
        out.append(line)
    try:
        return _hs.clean_cart(out)
    except _hs.HeldSaleError as e:
        raise AmendmentError(f"That sale cannot be rung again: {e}") from None


def context_from(sale):
    """The cart-level context that belongs to the same sale — who it is for, and whether it is an
    activation. Whitelisted against `held_sale.CONTEXT_FIELDS`, so the context a re-ring produces is
    the context a hold produces. Losing the customer here would bill the replacement to a walk-in
    and drop the §POS-07 address off the new receipt.
    """
    s = sale or {}
    out = {}
    if s.get("customer_id"):
        out["customer_id"] = str(s["customer_id"])
    if "is_activation_sale" in s:
        out["is_activation_sale"] = bool(s.get("is_activation_sale"))
    return {k: v for k, v in out.items() if k in _hs.CONTEXT_FIELDS}


def sentence(sale, cart, reason=None):
    """One line the till shows before anything happens, so a re-ring is a decision and not a
    surprise. It states plainly that the number changes: the owner asked for "the same receipt" and
    cannot have it, because the old number's receipt is already in the customer's hand."""
    if reason:
        return reason
    s = sale or {}
    n = len(cart or [])
    return (f"Transaction #{s.get('transaction_id') or '?'} will be voided and its {n} "
            f"line{'' if n == 1 else 's'} put back in the cart. Add the extra items and tender once: "
            "the customer gets one receipt with everything on it, under a new transaction number. "
            "The prices are the ones that were agreed; tax is recalculated at the store's current rate.")


def plan(sale, items, now=None, window_days=None):
    """The whole answer, in one shape: may it be rung again, with what cart, what context, and what
    the cashier is told. `{"allowed", "reason", "cart", "context", "sentence"}`.

    A refused plan carries NO cart. A caller that received lines alongside a refusal could ring them
    anyway, and the refusal would be decoration.
    """
    reason = refusal(sale, now=now, window_days=window_days)
    if reason is None:
        try:
            cart = cart_from(items)
        except AmendmentError as e:
            reason = str(e)
            cart = []
    else:
        cart = []
    return {
        "allowed": reason is None,
        "reason": reason,
        "cart": cart,
        "context": context_from(sale) if reason is None else {},
        "sentence": sentence(sale, cart, reason),
    }
