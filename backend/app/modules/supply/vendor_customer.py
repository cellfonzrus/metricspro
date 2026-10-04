"""ONE HOME — "which customer on the vendor's side IS this store?"

Owner 2026-10-04: *"the store list from the metrics pro will be uploaded as a customer list in
shopify also to enable placing an order … they have to set up as wholesale customers or retail
customers to set up the payment method, once that is set up the order can be placed."*

THE CLASS, NOT THE INSTANCE. The instance is one vendor's Shopify store needing a customer per
store. The class is: **an order is placed FOR somebody, and who that somebody is on the vendor's
side is a fact about the store, declared once and read by every caller** — the export that seeds
the vendor's customer list, and the draft order that has to name the same customer later. Two
places deriving "the customer for store B-1115" differently is exactly the drift the house rules
forbid: the export would seed one address and the order would ask for another, and the vendor would
hold two customers for one store with the payment terms on the wrong one.

So the identity is derived HERE, by `customer_for_store`, and both callers dereference it:
  · `customer_rows` / `import_csv`  — the list the tenant uploads to the vendor once;
  · `shopify_draft_order.draft_order_payload(customer=...)` — the customer each order names.

RULE TWO — config, never code. No tenant, vendor or store name appears here. The rule lives on the
vendor row, inside the route it belongs to:
`po_vendor.portal_config["order_transport"]["customer"]`, so it needs no new column and no new
table. A vendor with nothing declared gets mode `none`: the export is refused with a reason and an
order names no customer, rather than a guessed address reaching a real vendor.

WHY AN EMAIL AT A DECLARED DOMAIN, and not the store's own address. A store row carries no email
(`storeops.stores` has store_code, address, market, phone). The vendor matches a customer by
email, so one has to exist and be STABLE: derived from the store code at a domain the tenant
declares, it is the same string every time, which is what lets the second upload update a customer
instead of creating a twin. It is a routing identity, not a mailbox we invent for a person.

NO PII IS INVENTED. The export carries what the store row already holds — code, address, phone —
plus the tags the tenant declared. No person's name, and never a rep's own email.

Pure: no I/O, no client, no network, stdlib only.
"""
from __future__ import annotations

import csv
import io
import re

CONFIG_KEY = "customer"                 # inside the order_transport block
MODES = ("none", "per_store")

# The columns Shopify's customer import reads. Fixed ORDER matters to a human eyeballing the file;
# the header names are the vendor's, which is why they live in one list and not in a format string.
CSV_COLUMNS = ("First Name", "Last Name", "Email", "Company", "Address1", "City", "Province",
               "Zip", "Country", "Phone", "Accepts Email Marketing", "Tags", "Note")

DOMAIN_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")
# A tag is a label on the vendor's side; a comma would silently split it into two.
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _/-]{0,38}$")


def _s(v) -> str:
    return str(v or "").strip()


def local_part(store_code) -> str:
    """The stable, vendor-safe local part of one store's address. Lower-cased deliberately: store
    codes are compared without case-folding elsewhere in this repo and 'b-1115' vs 'B-1115' already
    produced a phantom store once — an address that differed by case would produce a twin customer
    the same way."""
    s = re.sub(r"[^a-z0-9]+", "-", _s(store_code).lower()).strip("-")
    return s[:52]


def validate_customer(decl) -> list:
    """Errors in the `customer` block. [] means it may be stored. Absent is legal and means `none`."""
    errors: list = []
    if decl in (None, "", {}):
        return errors
    if not isinstance(decl, dict):
        return [f"{CONFIG_KEY} must be an object"]
    mode = _s(decl.get("mode")).lower()
    if mode and mode not in MODES:
        errors.append(f"{CONFIG_KEY}.mode must be one of {', '.join(MODES)}")
    if mode == "per_store":
        dom = _s(decl.get("email_domain")).lower().lstrip("@")
        if not dom:
            errors.append(f"{CONFIG_KEY}.email_domain is required when mode is 'per_store' — the "
                          f"vendor matches a customer by email, so each store needs a stable one")
        elif not DOMAIN_RE.match(dom):
            errors.append(f"{CONFIG_KEY}.email_domain {dom!r} is not a domain name")
    tags = decl.get("tags")
    if tags not in (None, "", []):
        if isinstance(tags, str):
            tags = [t for t in tags.split(",")]
        if not isinstance(tags, (list, tuple)):
            errors.append(f"{CONFIG_KEY}.tags must be a list of labels")
        else:
            for t in tags:
                if not TAG_RE.match(_s(t)):
                    errors.append(f"{CONFIG_KEY}.tags: {_s(t)!r} is not a usable label — letters, "
                                  f"digits, space, dash, underscore and slash, and never a comma")
    cty = _s(decl.get("country_code")).upper()
    if cty and not re.match(r"^[A-Z]{2}$", cty):
        errors.append(f"{CONFIG_KEY}.country_code must be a two-letter code such as US")
    return errors


def tags_for(decl) -> list:
    """The labels every exported customer carries. The tenant declares them because the vendor's
    price list and payment terms hang off them (wholesale vs retail) — this platform never decides
    which of its own customers is wholesale."""
    raw = (decl or {}).get("tags")
    if isinstance(raw, str):
        raw = raw.split(",")
    return [_s(t) for t in (raw or []) if _s(t)]


def customer_for_store(decl, store) -> dict:
    """THE identity, for one store row. {} when no customer can be named, with no guessing.

    Returned shape is this platform's own, not any vendor's: the dialect maps it on the way out.
    """
    decl = decl or {}
    store = store or {}
    if _s(decl.get("mode")).lower() != "per_store" or validate_customer(decl):
        return {}
    code = _s(store.get("store_code"))
    lp = local_part(code)
    if not lp:
        return {}
    dom = _s(decl.get("email_domain")).lower().lstrip("@")
    return {
        "store_code": code,
        "company": code,
        "email": f"{lp}@{dom}",
        "phone": _s(store.get("phone")),
        "address1": _s(store.get("address")),
        "country_code": _s(decl.get("country_code")).upper() or "US",
        "tags": tags_for(decl) + ([f"market-{local_part(store.get('market'))}"]
                                  if _s(store.get("market")) else []),
        "note": _s(decl.get("note")),
    }


def customer_rows(decl, stores) -> tuple:
    """(customers, skipped). One row per store that can be named; the rest are REPORTED with the
    reason rather than dropped, because a store missing from the vendor's customer list is a store
    nobody can order for and the tenant has to know which."""
    out, skipped = [], []
    for st in (stores or []):
        c = customer_for_store(decl, st)
        if c:
            out.append(c)
        else:
            skipped.append({"store_code": _s((st or {}).get("store_code")),
                            "reason": "; ".join(validate_customer(decl))
                                      or "no customer rule is declared for this vendor"})
    return out, skipped


def import_csv(decl, stores) -> str:
    """The file the tenant uploads to the vendor once. Shopify's own customer-import columns.

    `Accepts Email Marketing` is always `no`: a store's routing address never consents to marketing
    on a person's behalf.
    """
    customers, _ = customer_rows(decl, stores)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for c in customers:
        w.writerow([c["company"], "Store", c["email"], c["company"], c["address1"], "", "", "",
                    c["country_code"], c["phone"], "no", ",".join(c["tags"]), c["note"]])
    return buf.getvalue()
