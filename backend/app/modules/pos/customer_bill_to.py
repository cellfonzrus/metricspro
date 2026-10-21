"""THE customer's own address, as a receipt prints it (§POS-07).

Owner 2026-10-21, verbatim: *"teh receipt shoudl show the complete address for the customer as in the
system"*.

WHAT WAS WRONG, STATED AS A CLASS. Not "the till receipt is missing the address" — that is the
instance. The class is that **"what does this customer's record say, written out for a human"** had
no home. The platform stores the address in five columns (`pos.customers.address_1 / address_2 /
city / state / zip`) and every caller that wanted it written out spelled those columns and joined
them itself. There was exactly one such caller — the receipt rebuilt from an imported sales report —
so the till slip, the reprint and the emailed copy carried the customer's NAME and nothing else,
while the imported copy of the same customer's receipt carried their address. Two paths answering
one question, one of them fixed and one not.

So the columns and the joining live HERE, once, and every caller dereferences it: the receipt
renderer (`sale_receipt.slip_model` / `document`), the imported-receipt builder
(`sales_from_reports._customer_lines_fn`) and the reader that selects the columns
(`pos.router._receipt_bundle` through `COLUMNS`). A second module joining these fields for display
fails the build — `harness_pos_receipt_render.py` §O.

NOT the comparison of two addresses. `customer_identity.norm_address` answers "are these two records
the same person", which is a different question with a different answer shape (one normalised string,
punctuation stripped), and it stays its own home. This module answers only "print it".

RULE TWO: no tenant, carrier or country is named here. A record that carries only a street prints
only a street; the shape is whatever the record holds, never a template a tenant has to match.
"""

#: The columns of `pos.customers` that make up an address. Named once so a caller SELECTS what this
#: module reads — a caller that selects four of the five silently drops a line of someone's address.
FIELDS = ("address_1", "address_2", "city", "state", "zip")

#: The same, as a PostgREST select list. `_receipt_bundle` appends this rather than spelling it.
COLUMNS = ",".join(FIELDS)


def _s(v) -> str:
    return str(v or "").strip()


def lines(row) -> list:
    """The address as the lines that print, in order: the street (with its apartment or suite), then
    the city, state and postal code. A field the record does not hold takes no line and leaves no
    blank one, so a half-filled record prints as much as it knows and nothing more.

    `row` is a customer record (or anything with those keys); `None` and an empty record give `[]`,
    which every caller treats as "name only" — the walk-in case, and never an error.
    """
    r = row or {}
    street = " ".join(x for x in (_s(r.get("address_1")), _s(r.get("address_2"))) if x)
    locality = " ".join(x for x in (_s(r.get("city")), _s(r.get("state")), _s(r.get("zip"))) if x)
    return [x for x in (street, locality) if x]


def block(name, row) -> dict:
    """The whole bill-to block: who they are, then where they are. `{"name", "lines"}`, where
    `lines` LEADS with the name — the shape both the structured Document's `bill_to` and the till
    slip's head want, so neither assembles it from the parts itself."""
    nm = _s(name)
    return {"name": nm, "lines": ([nm] if nm else []) + lines(row)}


def has_address(row) -> bool:
    """Whether this record holds an address at all. The screens that ask "why is the address not on
    the receipt" need the answer to be about the RECORD, not about the receipt."""
    return bool(lines(row))
