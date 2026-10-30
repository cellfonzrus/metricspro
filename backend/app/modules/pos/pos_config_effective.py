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
    # WHAT THIS STORE PRINTS ON (owner 2026-10-19). A store override, so one counter's thermal roll
    # does not become every store's setting. RULE TWO: the printer is DECLARED, never branched on —
    # `receipt_printer_label` is the store's own words for its printer ("Epson TM-T20III",
    # "Citizen CT-S310II"), carried so the screen can say which printer it is sized for and so a
    # later direct-to-printer route has one place to read it from. No printer make appears in code.
    "receipt_printer_label": "",
    # Blank = inherit the org template's width. A store that prints on a narrower roll says so here.
    "receipt_paper_width_mm": "",
    # Blank = inherit the org template's layout, i.e. the store accepts the company default.
    "receipt_layout": "",
    # DIRECT PRINTING (owner 2026-10-20: the receipts print to the thermal printer "only if desired
    # by the user, there cannot be any harcoded rule"). OFF everywhere until a store turns it on, and
    # even then it is only this store's DEFAULT: the print screen offers both destinations every
    # time, and the printer dialog keeps working exactly as it does today.
    "receipt_direct_print": False,
    # Where the till helper listens on the counter PC. It is a setting, not a constant in code, so a
    # store that runs it on another port says so without a release.
    "receipt_helper_url": "http://127.0.0.1:9100/print",
    # PAY BY BANK TRANSFER (owner 2026-10-21, §POS-06). The account a customer is told to pay into,
    # declared in the register setup — per store, so a shop with its own bank account says so, and
    # the org row serves the rest. STORED ENCRYPTED: these two keys are the only POS settings that
    # are, and `pos/receipt_remit.py` is the one home for that (its `KEYS` names this pair). This
    # module stays PURE and never decrypts: it resolves which ROW applies, and the reader opens it.
    # THE TENDERS A REGISTER OFFERS (owner 2026-10-30, §POS-14). Read by `GET /pos/tender-types`
    # and by the checkout, so the server declares their defaults here like every other key it reads.
    # The VALUES are the house answer and `pos/tender_vocabulary.py` is their one home — pinned
    # key by key by `harness_pos_tender_vocabulary.py` §F, which fails the build if the two drift.
    # Per store as well as per org, so a counter that does not sell on account can say so.
    "tender_terms_enabled": True,
    "tender_terms_day_options": "15,30,60",
    # THE WHOLESALE CUSTOMER AND THEIR W-9 (owner 2026-10-30, §POS-15). Read by the W-9 panel's
    # endpoint and by the checkout's gate. The VALUES are the house answer and
    # `pos/wholesale_w9.py` is their one home — pinned key by key by `harness_pos_wholesale_w9.py`
    # §F. Per store as well as per org: a trade counter and a retail shop can differ.
    "wholesale_tax_exempt_default": True,
    "w9_valid_months": 36,
    "w9_block_sale": True,
    "w9_blank_form_url": "",
    "receipt_remit_routing_number": "",
    "receipt_remit_account_number": "",
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


def printer(rows, store_code: str = "") -> dict:
    """What this store prints receipts on, as the store declared it.

    `label` is free text the store typed, never a key behaviour switches on. `paper_width_mm` and
    `layout` are blank when the store accepts the company default — blank, not a guessed value, so
    `sale_receipt.print_choice` can tell "this store asked for 58 mm" apart from "this store never
    said", which is the difference between honouring a declaration and inventing one.
    """
    return {
        "label": text(rows, "receipt_printer_label", store_code),
        "paper_width_mm": text(rows, "receipt_paper_width_mm", store_code),
        "layout": text(rows, "receipt_layout", store_code),
        # Whether THIS store prints straight to the printer by default, and where its helper listens.
        "direct": flag(rows, "receipt_direct_print", store_code),
        "helper_url": text(rows, "receipt_helper_url", store_code),
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
