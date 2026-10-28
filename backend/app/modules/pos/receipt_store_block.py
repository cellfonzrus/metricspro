"""WHAT THE SHOP IS, as a receipt prints it — one home for every layout and every medium (§POS-10).

Owner, 2026-10-28, on a real emailed receipt: *"see the rq receipt email to the customer, it does
not show the address and looks fake … the till thermal printer receipt came out ok"*.

THE CLASS THIS FIXES. "What does a receipt say the shop IS?" had TWO answers:

  1. the till slip (`sale_receipt.slip_model`) read the RECEIPT-BRANDING rows through
     `pos_config_effective.brand` — company name, address lines, licence/tax ids — and printed them;
  2. the tenant's own POS layout (`sale_receipt.document`) read something else entirely: a
     `store_lines` list the router assembled for that path alone out of the `storeops.stores` row.

Two paths answering one question drift, and these had: the second one selected a `name` column that
`storeops.stores` does not have, PostgREST answered 42703, the router's `except Exception: pass`
swallowed it, and every receipt printed in the tenant's own POS layout went out carrying the company
name and nothing else — no street, no city, no licence. The till slip was untouched because it never
read that row, which is exactly why the owner saw one medium right and the other wrong.

So the shop's identity is resolved HERE, once, and both layouts dereference it. The columns that
read it live here too (`STORE_COLUMNS`), because "which columns does that table actually have" is
the fact that was wrong — a caller spelling its own select is how it goes wrong again.

PRECEDENCE, stated once. The receipt-branding rows win: they are what an admin typed on the receipt
screen for this to print, they are per-org with a per-store override already, and they are the
address a customer should be able to walk back into. The store's own row is the FALLBACK, for a shop
that never filled the branding in. Nothing is invented: a shop that declared neither prints neither.

RULE TWO: no tenant, carrier or store is named here, and no behaviour branches on one.
"""
from __future__ import annotations

#: The columns `storeops.stores` ACTUALLY has that a receipt needs. Declared once and dereferenced
#: by the reader, because a hand-spelled select is what produced a silent 42703 and a receipt with
#: no address on it. There is no `name` column on that table — the shop's printed NAME comes from
#: the branding rows, else the tenant, else the store code.
STORE_COLUMNS = "store_code,address,phone"


def _txt(v) -> str:
    return " ".join(str(v or "").split())


def block(brand=None, store_row=None, tenant_name: str = "", store_code: str = "") -> dict:
    """The shop's identity as a receipt prints it:

        {"name", "address_lines", "phone", "license", "lines"}

    `lines` is the whole block in printing order (name, address, phone, licence) with blanks
    dropped — the one thing a renderer needs — and the named fields are there for a layout that
    places them itself (the POS formats put the phone on its own row).
    """
    # Lazy, and deliberately: `sale_receipt` imports THIS module, and the company-name rule is its
    # one home (§POS-02 E-checks). Dereferenced, never re-derived here.
    from . import sale_receipt as _sr

    brand = brand or {}
    row = store_row or {}
    name = _txt(_sr.company_name(brand, tenant_name)) or _txt(store_code)

    address_lines = [_txt(ln) for ln in (brand.get("address_lines") or []) if _txt(ln)]
    if not address_lines:
        # The shop's own row: the street exactly as that row holds it, one line, because one line is
        # all it holds. The `market` column is NOT folded in — it is a market name, not reliably a
        # state, and an early draft of this appended it onto an address that already ended in one
        # ("1 Test St Testville NY 10000 NY"). A receipt prints what is known, never a guess.
        fallback = _txt(row.get("address"))
        if fallback:
            address_lines = [fallback]

    phone = _txt(row.get("phone"))
    license_ids = _txt(brand.get("license_tax_ids"))

    lines = [ln for ln in ([name] + address_lines + [phone, license_ids]) if ln]
    return {"name": name, "address_lines": address_lines, "phone": phone,
            "license": license_ids, "lines": lines}
