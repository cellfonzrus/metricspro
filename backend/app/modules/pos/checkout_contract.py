"""THE wire contract of a checkout — which keys `pos.checkout` actually reads (index §POS-20).

THE DEFECT THIS EXISTS FOR. On 2026-11-12 the owner tried to ring a sale on the phone and the
register refused it: *"This register does not know a tender called “(blank)”."* The app was sending
`{method: "cash"}` and both `pos/router.checkout` and the `pos.checkout` RPC read
`payment_method`, so the tender arrived empty. Three more keys were wrong the same way — the app
sent `quantity` where the RPC reads `qty`, `short_name` where it reads `description`, and no
`subtotal` / `tax_total` / `total` / `extended_price` at all.

**The refusal was the only thing that stopped a silently wrong sale.** `pos.checkout` COALESCEs a
missing `qty` to 1 and every missing money key to 0, so had the tender been spelled right the sale
would have been stored with every line at quantity one, no description, and a value of $0.00. The
mobile register had never been able to record a correct sale, and nothing anywhere said so.

THE CLASS, not the instance. The class is not "the app misspelled payment_method". It is **"a till's
spelling of the checkout contract is checked by nothing"** — the keys live in SQL, the readers live
in Python, the writers live in two TypeScript packages that cannot import each other, and a key that
no reader recognises is silently dropped rather than refused. Renaming four fields fixes the
instance; this module plus `harness_pos_checkout_contract.py` fixes the class, by DECLARING the keys
once and failing the build when a client sends a key the RPC does not read, omits one it must, or
when this declaration drifts from the migration that defines it.

WHY A DECLARATION AND NOT A DERIVATION. The harness DERIVES the real key set out of the migration's
SQL and asserts it equals what is declared here, so the two cannot disagree. The declaration is
still written out, because a derived-only set would silently absorb a change to `pos.checkout` —
the point is that a change to the contract must be a deliberate edit to this file, the same
tripwire discipline as `harness_db_resilience.py`.

RULE TWO: no carrier, tenant or product name appears here. These are platform field names.
"""

from __future__ import annotations

# ── The relation each group of keys lands in ─────────────────────────────────────────────────────
# `pos.checkout` reads p_sale -> pos.sales, p_items -> pos.sale_items, p_payments -> pos.sale_payments.
SALE_KEYS = frozenset({
    "store_code", "customer_id", "employee_id", "receipt_type",
    "subtotal", "discount_total", "tax_total", "total", "balance",
    "is_activation_sale", "receipt", "notes", "tax_exempt_basis", "w9_override_by",
})

ITEM_KEYS = frozenset({
    "product_id", "product_type", "description", "serial_number", "qty",
    "unit_price", "list_price", "cost", "discount", "tax_rate", "tax_value", "extended_price",
})

PAYMENT_KEYS = frozenset({
    "payment_method", "amount", "card_last_four", "check_number", "terms_days", "due_date",
})

# ── What a till MUST send, as opposed to what it MAY ─────────────────────────────────────────────
# Everything above is COALESCEd by the RPC, so "optional" means "silently becomes zero, one or
# null". These are the keys whose absence makes the stored sale WRONG rather than merely sparse, so
# a client that omits one is refused by the harness rather than trusted.
#
# `qty` is here because its COALESCE is 1, not 0: a missing quantity does not look missing
# afterwards, it looks like a sale of one. That is the shape that hides.
REQUIRED_ITEM_KEYS = frozenset({"product_id", "qty", "unit_price", "extended_price"})
REQUIRED_SALE_KEYS = frozenset({"store_code", "subtotal", "total"})
REQUIRED_PAYMENT_KEYS = frozenset({"payment_method", "amount"})

# ── Keys a till has sent that the RPC does not read ──────────────────────────────────────────────
# Named so the refusal can say what was MEANT, instead of "unknown key". A till sending one of these
# believes it is sending the fact on the right, and the platform drops it.
MISSPELLINGS = {
    "method": "payment_method",
    "quantity": "qty",
    "short_name": "description",
    "note": "notes",
    "price": "unit_price",
    "line_total": "extended_price",
    "tax": "tax_value",
    "qty_sold": "qty",
}

# A till may carry fields of its own that are never meant to reach the wire (a display name, a
# taxable flag it used to compute tax). They are listed BY NAME so that "the RPC ignores it" is a
# decision somebody made, never an accident — an unlisted stray key fails the harness.
CLIENT_ONLY_KEYS = frozenset({
    "register_number",   # which till; pos.sales carries no column for it
    "is_taxable",        # an input to the till's own tax maths, not a stored fact
    "w9_override",       # a REQUEST, read by pos/router.checkout and never passed to the RPC
})


def group_of(key: str) -> str:
    """Which part of the body a key belongs to, or '' when the contract does not know it."""
    if key in SALE_KEYS:
        return "sale"
    if key in ITEM_KEYS:
        return "items"
    if key in PAYMENT_KEYS:
        return "payments"
    return ""


def unknown_keys(group: str, keys) -> list[str]:
    """The keys in `group` this contract does not recognise, sorted. A client-only key is not
    unknown — it is declared as never reaching the wire."""
    declared = {"sale": SALE_KEYS, "items": ITEM_KEYS, "payments": PAYMENT_KEYS}.get(group, frozenset())
    return sorted(k for k in keys if k not in declared and k not in CLIENT_ONLY_KEYS)


def missing_required(group: str, keys) -> list[str]:
    """The required keys of `group` that `keys` does not carry, sorted."""
    required = {"sale": REQUIRED_SALE_KEYS, "items": REQUIRED_ITEM_KEYS,
                "payments": REQUIRED_PAYMENT_KEYS}.get(group, frozenset())
    return sorted(required - set(keys))


def refusal(group: str, keys) -> str:
    """One sentence for a client sending the wrong shape, naming the fix where it is known.

    Returns "" when the shape is right, so every caller reads this one field rather than wording a
    refusal of its own.
    """
    unknown = unknown_keys(group, keys)
    missing = missing_required(group, keys)
    if not unknown and not missing:
        return ""
    parts = []
    for key in unknown:
        meant = MISSPELLINGS.get(key)
        parts.append(f"“{key}” is not read by pos.checkout"
                     + (f" — the field is called “{meant}”" if meant else ""))
    for key in missing:
        parts.append(f"“{key}” is required and was not sent")
    return (f"this checkout's {group} carry a shape pos.checkout does not read, so the sale would "
            f"be stored wrong rather than refused: " + "; ".join(parts) + ".")
