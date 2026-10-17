"""THE effective value of a POS setting, on the server (owner ask 2026-10-17, index §POS-02).

**Why this module has to exist.** `pos.pos_settings` holds one jsonb value per key at either the org
level (`store_code IS NULL`) or a store level (an override). The RULE for turning those rows into the
value that applies — *store override, else org default, else the declared default* — existed in ONE
place, and that place was the browser (`frontend/src/lib/pos-config.ts::resolvePosConfig`). That was
fine while every reader of a POS setting was a screen. It stopped being fine the moment the server had
to render a receipt: an emailed receipt is built where there is no browser, so the server needs the
same answer, and a server that re-derives it by hand is the second copy this house forbids.

So the rule lives here for the backend, and the DEFAULTS of the keys the backend reads are declared
here ONCE and pinned, key by key, against the browser registry's own `default:` literals by
`backend/harness_pos_receipt_render.py` §B. If someone changes a default on either side without the
other, the build goes red naming the key — which is the only thing that keeps two languages' copies
of one fact honest.

PURE: no IO, no app imports. The caller passes the rows `GET /pos/settings` already returns.
"""
from __future__ import annotations

# The POS settings the SERVER reads, with the default that applies when no row exists. Every key
# here must exist in frontend/src/lib/pos-config.ts with the same default (pinned, see above). This
# is deliberately NOT the whole registry: a key the server never reads needs no copy of its default,
# and copying all of them would be a second registry.
SERVER_DEFAULTS: dict[str, object] = {
    # what the receipt prints
    "receipt_customer_short_name": False,
    "receipt_coupon_in_retail_price": False,
    # receipt branding — the company identity printed above the store line
    "brand_company_name": "",
    "brand_address_line1": "",
    "brand_address_line2": "",
    "brand_address_line3": "",
    "brand_address_line4": "",
    "brand_address_line5": "",
    "brand_license_tax_ids": "",
    # legal blocks, each with its target and optional signature line
    "disclaimer1_text": "",
    "disclaimer1_target": "none",
    "disclaimer1_signature": False,
    "disclaimer2_text": "",
    "disclaimer2_target": "none",
    "disclaimer2_signature": False,
    # the emailed receipt's own words
    "email_receipt_subject": "",
    "email_receipt_body": "",
}

#: Which disclaimer targets mean "print on a POS receipt" — the browser's own rule, stated once.
POS_DISCLAIMER_TARGETS = ("pos", "both")


def effective(rows, key: str, store_code: str = "", defaults: dict | None = None):
    """The value of `key` that applies at `store_code`.

    Resolution order, the same one the browser applies: the store's own override, then the org
    default (`store_code` null/blank), then the declared default. A key with no declared default
    answers None rather than inventing one — a reader asking for a key the server does not declare
    is a bug, not a blank.
    """
    table = SERVER_DEFAULTS if defaults is None else defaults
    store = (store_code or "").strip()
    org_value = _MISSING = object()
    store_value = _MISSING
    for row in rows or []:
        if (row or {}).get("key") != key:
            continue
        rs = ((row or {}).get("store_code") or "").strip()
        if rs and store and rs == store:
            store_value = row.get("value")
        elif not rs:
            org_value = row.get("value")
    if store_value is not _MISSING:
        return store_value
    if org_value is not _MISSING:
        return org_value
    return table.get(key)


def values(rows, store_code: str = "", keys=None) -> dict:
    """Every server-read setting as one dict — what a renderer takes instead of the raw rows."""
    wanted = tuple(keys) if keys else tuple(SERVER_DEFAULTS)
    return {k: effective(rows, k, store_code) for k in wanted}


def flag(rows, key: str, store_code: str = "") -> bool:
    """A boolean setting. A stored string ("true") counts as true, because the kv column is jsonb and
    an older row may hold either — the register has always read these as truthy/falsey, not typed."""
    v = effective(rows, key, store_code)
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    return bool(v)


def text(rows, key: str, store_code: str = "") -> str:
    """A text setting, trimmed. None and a stored null both read as empty."""
    v = effective(rows, key, store_code)
    return "" if v is None else str(v).strip()


def brand(rows, store_code: str = "") -> dict:
    """The receipt's company identity: the name that replaces the store line, the address lines in
    order (blanks dropped), and the licence / tax ids printed under them."""
    return {
        "company_name": text(rows, "brand_company_name", store_code),
        "address_lines": [ln for ln in
                          (text(rows, f"brand_address_line{n}", store_code) for n in range(1, 6))
                          if ln],
        "license_tax_ids": text(rows, "brand_license_tax_ids", store_code),
    }


def disclaimers(rows, store_code: str = "") -> list[dict]:
    """The legal blocks that print on a POS receipt, in order. A block with no text, or one targeted
    somewhere other than the POS receipt, is not returned at all."""
    out = []
    for n in (1, 2):
        body = text(rows, f"disclaimer{n}_text", store_code)
        target = (text(rows, f"disclaimer{n}_target", store_code) or "none").lower()
        if not body or target not in POS_DISCLAIMER_TARGETS:
            continue
        out.append({"text": body, "signature": flag(rows, f"disclaimer{n}_signature", store_code)})
    return out
