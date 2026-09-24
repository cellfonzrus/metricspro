"""WHO THE CUSTOMER IS — the one home for how a customer is recognised across uploads, receipts and the POS.
PURE (stdlib).

OWNER (2026-09-24): *"these customers which are uploaded should be available in the pos to make the sales in
future and if any data is later uploaded for the same customer the system to check for existing fields to
match the data and update their record rather than creating a new record"*.

MEASURED (live, the first rebuild of 23 invoices): every receipt CREATED a customer — the matcher selected a
`notes` column mig 725 never created, the select failed, the failure read as "not found" — and the POS's
placeholder bill-to names ('Walk In' ×4) became customers. This module holds the facts every matcher needs:

  · `norm_name` — one spelling of a person's name for comparison (case, spaces, punctuation);
  · `is_placeholder` — a bill-to that names nobody (the house words below, generic, plus the org's own list
    from config) never becomes, and never matches, a customer.

The words below are generic POS placeholders, not a carrier, tenant or product name (RULE TWO). An org adds
its own through `pos.pos_settings` key CONFIG_KEY (`{"placeholders": [...]}`), read by `resolve_config`.
"""
from __future__ import annotations

import re

CONFIG_KEY = "customer_identity"
HOUSE_PLACEHOLDERS = ("walk in", "walkin", "walk in customer", "no customer", "no name", "cash customer", "cash sale",
                      "guest", "customer", "unknown", "n a", "na", "none", "test")


def _s(v):
    return "" if v is None else str(v).strip()


def norm_name(v):
    """A name for comparison: lower case, punctuation → space, runs of spaces → one."""
    return " ".join(re.sub(r"[^\w]+", " ", _s(v).lower()).split())


def resolve_config(raw):
    """The org's row value → {'placeholders': [normalised words]} (house words + the org's own)."""
    v = raw if isinstance(raw, dict) else {}
    extra = [norm_name(x) for x in (v.get("placeholders") or []) if norm_name(x)]
    words = list(HOUSE_PLACEHOLDERS)
    for w in extra:
        if w not in words:
            words.append(w)
    return {"placeholders": words}


def is_placeholder(name, config=None):
    """True for a bill-to that names nobody: empty, or one of the placeholder words (house + org)."""
    n = norm_name(name)
    if not n:
        return True
    words = (config or {}).get("placeholders") or HOUSE_PLACEHOLDERS
    return n in words


def split_name(full):
    """'First Middle Last' → ('First', 'Middle Last') on single spaces (the stored first / last name)."""
    parts = _s(full).split()
    if not parts:
        return ("", "")
    if len(parts) == 1:
        return (parts[0], "")
    return (parts[0], " ".join(parts[1:]))


def same_name(row, first, last):
    """Does a stored customer row carry this first + last name (normalised both sides)?"""
    return norm_name(row.get("first_name")) == norm_name(first) and norm_name(row.get("last_name")) == norm_name(last)
