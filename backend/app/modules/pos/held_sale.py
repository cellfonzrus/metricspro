"""THE one home for "what is still in this register's cart" — the suspended sale.

WHY THIS FILE EXISTS (owner 2026-10-16: *"if a person leaves the cash register it gets blank, if you
come back it should automatically store for the next session in suspended state"*). The register's
cart lived only in React state, so a reload, a navigation, a browser crash or the end of a shift
erased a part-rung sale with no trace. The customer is then re-rung from memory, and the lines that
are remembered wrong are the discounts and the serials.

THE FACT, AND WHERE IT LIVES. A suspended cart is a property of the **terminal**, not of the person
and not of the browser:

* Its identity is `(org, store_code, register_number)` — the SAME triple
  `pos.register_sessions` is keyed on (mig 725). That is what makes "the next session" literal: the
  next cashier to open that register is handed back exactly what the last one left on it.
* One cart per terminal. A second suspended cart on one register would mean the register has two
  "current sales", and nothing could say which one the next scan belongs to.
* It is stored and restored VERBATIM. A restore never re-prices a line and never re-reads the
  catalog: the agreed unit price, the override it overrode, the discount and the serial are what
  the cashier agreed with the customer, and quietly moving them is how a $99 line becomes $199
  between two halves of one sale. (The register still re-derives each line's TAX from the store's
  live rate on restore, exactly as it already does when the store is switched mid-sale — a tax rate
  is a legal fact about the store, not a figure the cashier negotiated.) The age of the hold travels
  with it (`held_at`) so the screen can say how old it is and the cashier can decide.
* It is NOT a sale. Nothing is written to `pos.sales`, no transaction number is burned, no stock
  moves and no money is recorded. A suspended cart that is never resumed has cost nothing — which
  is why it can be saved automatically without asking.

Why not browser storage: `localStorage` is per browser profile, so it answers "what did THIS
browser have" and not "what is on this register". A tablet swapped between staff, a second browser,
or a cleared profile all lose the cart, and no other screen (a manager looking at the terminal) can
ever see that a sale is part-rung.

PURE. No DB handle, no clock (the caller stamps the time), no org id, no carrier / tenant / product
name (RULE TWO). Proof: `backend/harness_pos_held_sale.py`.
"""

# A held line carries exactly what the register's own cart line carries — the fields
# `pos/sales/page.tsx` builds and `POST /pos/sales/checkout` consumes. Anything else a client sends
# is DROPPED rather than stored: a held cart must be restorable into a checkout body without the
# restore being a second, looser definition of what a cart line is.
LINE_FIELDS = ("product_id", "description", "product_type", "serial_number", "qty",
               "unit_price", "list_price", "cost", "discount", "tax_rate", "tax_value",
               "extended_price", "is_taxable")
MONEY_FIELDS = ("unit_price", "list_price", "cost", "discount", "tax_rate", "tax_value",
                "extended_price")
# Cart-level context that is part of the same unfinished sale.
CONTEXT_FIELDS = ("customer_id", "is_activation_sale", "note")

DEFAULT_REGISTER = 1
MAX_LINES = 200          # a till receipt, not a bulk import; a longer body is refused, not truncated


class HeldSaleError(ValueError):
    """A refused hold or restore. The message is written to be shown at the till."""


def clean_register(value):
    """The register number this cart belongs to. Blank means register 1, matching
    `pos.register_sessions.register_number`'s own default — the two must agree or a cart held on
    "the register" and a session opened on "register 1" would be different terminals."""
    if value in (None, ""):
        return DEFAULT_REGISTER
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise HeldSaleError("register_number must be a whole number") from None
    if n < 1:
        raise HeldSaleError("register_number must be 1 or more")
    return n


def terminal(store_code, register_number=None):
    """The identity of the terminal a cart is held on. A hold with no store is refused: a cart with
    no store cannot be handed to the next session, because no session owns it."""
    sc = (store_code or "").strip()
    if not sc:
        raise HeldSaleError("store_code required — a suspended cart belongs to a register at a store")
    return {"store_code": sc, "register_number": clean_register(register_number)}


def _number(v, field):
    if v in (None, ""):
        return 0
    if isinstance(v, bool):
        raise HeldSaleError(f"{field} must be a number")
    try:
        return float(v)
    except (TypeError, ValueError):
        raise HeldSaleError(f"{field} must be a number") from None


def clean_line(item):
    """ONE held cart line, whitelisted and typed.

    `product_id` is required — a held line must still name a catalogue product, or resuming it
    would check out a line the stock and commission paths cannot follow. The money fields are
    coerced to numbers HERE rather than on the way out, so a cart that was stored at all is a cart
    that can be restored; `qty` keeps its whole-number meaning.
    """
    if not isinstance(item, dict):
        raise HeldSaleError("each cart line must be an object")
    pid = (item.get("product_id") or "")
    pid = pid.strip() if isinstance(pid, str) else str(pid)
    if not pid:
        raise HeldSaleError("every held cart line must name a product (product_id)")
    out = {"product_id": pid}
    for k in LINE_FIELDS:
        if k == "product_id" or k not in item:
            continue
        v = item[k]
        if k in MONEY_FIELDS:
            out[k] = _number(v, k)
        elif k == "qty":
            out[k] = int(_number(v, "qty"))
        elif k == "is_taxable":
            out[k] = bool(v)
        else:
            out[k] = ("" if v is None else str(v))
    out.setdefault("qty", 1)
    return out


def clean_cart(items):
    """The whole held cart. An over-long body is refused rather than silently cut — a cart that
    came back three lines short is worse than one that would not save."""
    rows = items or []
    if not isinstance(rows, (list, tuple)):
        raise HeldSaleError("cart must be a list of lines")
    if len(rows) > MAX_LINES:
        raise HeldSaleError(f"a suspended cart holds at most {MAX_LINES} lines")
    return [clean_line(i) for i in rows]


def is_empty(cart, context=None):
    """Is there anything worth holding? An empty cart with no customer attached is nothing, and
    holding it would mean every visit to the register leaves a row behind. A customer selected with
    no lines yet IS worth holding — that is a sale in progress."""
    if cart:
        return False
    ctx = context or {}
    return not (ctx.get("customer_id") or ctx.get("note"))


def clean_context(body):
    """The cart-level context stored beside the lines, whitelisted the same way."""
    out = {}
    for k in CONTEXT_FIELDS:
        if k not in (body or {}):
            continue
        v = body[k]
        if k == "is_activation_sale":
            out[k] = bool(v)
        else:
            out[k] = (None if v in (None, "") else str(v))
    return out


def hold_row(store_code, register_number, cart, body=None, held_by=None, now=None):
    """The row that records this terminal's suspended cart.

    The caller supplies `now` — this module has no clock, so a harness can pin the stamp and the
    endpoint stamps the server's time. `held_by` is who left it, which is what lets the next
    session say whose sale it is picking up rather than presenting an anonymous cart.
    """
    t = terminal(store_code, register_number)
    lines = clean_cart(cart)
    ctx = clean_context(body)
    return {**t, "cart": lines, "line_count": len(lines),
            "cart_total": cart_total(lines),
            "held_by": (held_by or None), "held_at": now, "updated_at": now, **ctx}


def cart_total(cart):
    """What the held cart came to, for the "a sale is waiting on this register" line. Derived from
    the stored lines, never carried as a separate figure a client could contradict."""
    return round(sum(float(l.get("extended_price") or 0) for l in (cart or [])), 2)


def restore(row):
    """The suspended cart as it was left, ready to put straight back into the register.

    `None` for no hold. Returns the lines VERBATIM (re-cleaned, so a row written by an older shape
    still restores), the context, and the hold's own age metadata — never a recomputed price.
    """
    if not row:
        return None
    lines = clean_cart(row.get("cart"))
    return {
        "store_code": (row.get("store_code") or "").strip(),
        "register_number": clean_register(row.get("register_number")),
        "cart": lines,
        "line_count": len(lines),
        "cart_total": cart_total(lines),
        "customer_id": row.get("customer_id") or None,
        "is_activation_sale": bool(row.get("is_activation_sale")),
        "note": row.get("note") or None,
        "held_by": row.get("held_by") or None,
        "held_at": row.get("held_at"),
        "updated_at": row.get("updated_at") or row.get("held_at"),
    }


def sentence(held):
    """One line the screen shows when a cart comes back, so resuming is a decision and not a
    surprise. It never claims the prices are current — only what was left and when."""
    if not held:
        return "No suspended sale on this register."
    n = held.get("line_count") or 0
    who = held.get("held_by")
    at = held.get("held_at")
    lines = f"{n} line{'' if n == 1 else 's'}"
    total = f"${held.get('cart_total', 0):.2f}"
    tail = f" left by {who}" if who else ""
    when = f" at {at}" if at else ""
    return (f"Resumed a suspended sale from this register: {lines}, {total}{tail}{when}. "
            "The prices are the ones that were on the lines when it was suspended.")
