"""ONE DIALECT — turning a MetricsPro purchase order into a draft order on a vendor's own store.

Selected by VALUE, never by name: `order_transport.transport_for(vendor)` returns
`config.dialect == "shopify_draft_order"` because a config row says so. No vendor, store or tenant
name appears in this file, and nothing here decides WHETHER to send — that is
`order_transport.push_enabled`.

WHAT A DRAFT ORDER IS, and why it is the right shape. A draft order is a basket the merchant can
look at, edit and then invoice. Creating one places NO order, charges nothing and emails nobody —
this module never calls the invoice-send endpoint, which is the only call that would reach a
customer. That is what makes it safe for an unattended sweep to raise one; it is also why
`order_transport` refuses any route that declares `places_order`.

IDEMPOTENCY IS OURS, NOT THEIRS. This API has no idempotency key, so a retry would make a second
basket. The key is our own record: a purchase order that already carries `external_ref` is never
pushed again (migration 1053). The tag below is for the human looking at the vendor's admin, not
for machine matching — matching on a tag we would have to list-and-scan is a second source of
truth for a fact our own row already holds.

CREDENTIAL. The token is passed in by the caller, read from the vendor's `commcalc.data_source`
login row (a column of `commcalc/router._SOURCE_SECRETS`). It is never read from config here, never
logged, and never returned: `push_draft_order` returns the draft's id and URL and nothing else that
could carry it.
"""
from __future__ import annotations

import re

DIALECT = "shopify_draft_order"
TAG_PREFIX = "metricspro-po-"
# The shape of a permanent store domain. A pretty custom domain is NOT accepted: it can be moved or
# removed while the permanent one cannot, and a token is issued against the permanent one.
HOST_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,58}[a-z0-9]\.myshopify\.com$")
VERSION_RE = re.compile(r"^20[0-9]{2}-(01|04|07|10)$")


def _s(v) -> str:
    return str(v or "").strip()


def _money(v) -> str:
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def validate_target(cfg) -> list:
    """Errors in the `api` block for this dialect. [] means it can be addressed."""
    cfg = cfg or {}
    errors: list = []
    host = _s(cfg.get("host")).lower()
    if not host:
        errors.append("api.host is required")
    elif not HOST_RE.match(host):
        errors.append("api.host must be the permanent <store>.myshopify.com domain, not a custom "
                      "domain — a token is issued against the permanent one")
    ver = _s(cfg.get("version"))
    if not ver:
        errors.append("api.version is required")
    elif not VERSION_RE.match(ver):
        errors.append("api.version must look like 2026-07 (the quarterly Admin API version shown "
                      "on the app's API credentials page)")
    return errors


def endpoint(cfg) -> str:
    """The draft-order collection URL. Raises on a target that did not validate, deliberately —
    building a URL from a half-valid target is how a request reaches the wrong host."""
    errors = validate_target(cfg)
    if errors:
        raise ValueError("; ".join(errors))
    return (f"https://{_s(cfg.get('host')).lower()}"
            f"/admin/api/{_s(cfg.get('version'))}/draft_orders.json")


def products_endpoint(cfg, *, limit=250) -> str:
    """The product-list URL. The same validated target as the order endpoint, so a vendor whose
    orders go to one store can never have its prices read from another."""
    errors = validate_target(cfg)
    if errors:
        raise ValueError("; ".join(errors))
    n = max(1, min(int(limit or 250), 250))
    return (f"https://{_s(cfg.get('host')).lower()}"
            f"/admin/api/{_s(cfg.get('version'))}/products.json?limit={n}&status=active")


def _availability(variant) -> tuple:
    """(stock_text, stock_qty). The vendor's own two facts, left as facts: `ordering_logic`
    classifies them, and classifying here too would be a second home for that judgement."""
    qty = variant.get("inventory_quantity")
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        qty = None
    policy = _s(variant.get("inventory_policy")).lower()
    tracked = _s(variant.get("inventory_management")) != ""
    if not tracked:
        return "In stock", None                 # untracked means the vendor always fills it
    if qty is None:
        return "", None
    if qty > 0:
        return f"{qty} in stock", qty
    return ("Backorder" if policy == "continue" else "Out of stock"), qty


def catalog_rows(data, *, host="") -> list:
    """Products → the rows `supply.store.land_catalog` already lands. PURE, and deliberately thin:
    it REPORTS the vendor's name, sku, price and stock and nothing else. Pack size, minimum order,
    availability wording and the item key are all decided by `ordering_logic.normalize_catalog_row`,
    which every other catalog route already goes through — a second derivation of any of them here
    would drift from the portal route's prices on the same screen.

    One row per VARIANT, because a variant is what gets ordered and priced. A variant whose title
    is the vendor's placeholder for "no variants" is not named twice in the product title.
    """
    out = []
    for prod in ((data or {}).get("products") or []):
        if not isinstance(prod, dict):
            continue
        title = _s(prod.get("title"))
        handle = _s(prod.get("handle"))
        body = re.sub(r"<[^>]+>", " ", _s(prod.get("body_html")))
        body = re.sub(r"\s+", " ", body).strip()
        url = f"https://{_s(host).lower()}/products/{handle}" if (host and handle) else ""
        for var in (prod.get("variants") or []):
            if not isinstance(var, dict):
                continue
            vt = _s(var.get("title"))
            name = title if (not vt or vt.lower() == "default title") else f"{title} — {vt}"
            stock_text, qty = _availability(var)
            out.append({
                "name": name,
                "sku": _s(var.get("sku")),
                "price": var.get("price"),
                "list_price": var.get("compare_at_price"),
                "stock_qty": qty,
                "stock_text": stock_text,
                "url": url,
                "description": body,
            })
    return out


def next_page(link_header) -> str:
    """The vendor's cursor for the next page, or "". Paging is the vendor's own: a page count we
    kept would read a stale slice the moment they add a product."""
    for part in _s(link_header).split(","):
        bits = part.split(";")
        if len(bits) < 2 or 'rel="next"' not in part:
            continue
        u = bits[0].strip()
        if u.startswith("<") and u.endswith(">"):
            return u[1:-1]
    return ""


def tag_for(po_number) -> str:
    return TAG_PREFIX + re.sub(r"[^A-Za-z0-9_-]+", "-", _s(po_number)).strip("-").lower()


def customer_block(customer) -> dict:
    """This platform's customer identity (`vendor_customer.customer_for_store`) in the vendor's
    shape. The identity itself is NEVER derived here — one home decides who the customer is, and
    this maps it, so the list the tenant uploaded and the order that names a customer agree."""
    c = customer or {}
    email = _s(c.get("email"))
    if not email:
        return {}
    return {"email": email, "first_name": _s(c.get("company")) or "Store", "last_name": "Store",
            "company": _s(c.get("company")), "phone": _s(c.get("phone")),
            "address1": _s(c.get("address1")),
            "country_code": _s(c.get("country_code")).upper() or "US"}


def draft_order_payload(po, lines, *, note=None, customer=None) -> dict:
    """The request body. PURE — this is the part worth proving, and it is proved DB-free.

    An UNPRICED line is carried at 0.00 and said so in its title, never dropped: a basket missing
    the item nobody could price is a basket that silently under-orders (the same ruling the draft
    PO already follows, index §47.16).
    """
    po = po or {}
    items: list = []
    for ln in (lines or []):
        qty = int(ln.get("qty") or ln.get("quantity") or 0)
        if qty <= 0:
            continue
        title = _s(ln.get("name") or ln.get("title") or ln.get("key")) or "Item"
        priced = ln.get("unit_cost") is not None
        items.append({
            "title": title if priced else f"{title} (price to confirm)",
            "quantity": qty,
            "price": _money(ln.get("unit_cost")),
            "requires_shipping": True,
            "taxable": True,
        })
    body = {
        "line_items": items,
        "tags": tag_for(po.get("po_number")),
        "note": _s(note or po.get("notes")),
        "use_customer_default_address": False,
    }
    # A customer is named only when one home could name it. An order with no customer is still a
    # valid draft the merchant can assign by hand — a GUESSED customer is not recoverable.
    cb = customer_block(customer)
    if cb:
        body["email"] = cb["email"]
        body["shipping_address"] = {k: v for k, v in cb.items() if k != "email" and v}
    return {"draft_order": body}


def read_result(data) -> dict:
    """The vendor's response, reduced to what we record. Anything else is dropped on purpose: the
    response echoes the whole basket, and keeping it would put vendor data in our PO row."""
    d = ((data or {}).get("draft_order")) or {}
    ref = _s(d.get("id"))
    return {
        "external_ref": ref,
        "external_url": _s(d.get("invoice_url")),
        "external_name": _s(d.get("name")),
        "ok": bool(ref),
    }


async def push_draft_order(cfg, token, po, lines, *, note=None, customer=None, timeout=30) -> dict:
    """Create ONE draft order. The only place this dialect speaks to the network.

    Returns {ok, external_ref, external_url, external_name, error}. It never raises for a vendor-side
    refusal — the caller records the failure against the PO and the sweep carries on with the rest.
    """
    out = {"ok": False, "external_ref": "", "external_url": "", "external_name": "", "error": ""}
    if not _s(token):
        out["error"] = "no credential is stored for this vendor"
        return out
    try:
        url = endpoint(cfg)
    except ValueError as e:
        out["error"] = str(e)
        return out
    payload = draft_order_payload(po, lines, note=note, customer=customer)
    if not payload["draft_order"]["line_items"]:
        out["error"] = "nothing to order"
        return out
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as cx:
            res = await cx.post(url, json=payload, headers={
                "X-Shopify-Access-Token": _s(token),
                "Content-Type": "application/json",
            })
        if res.status_code >= 400:
            # The body can echo the request; keep it short and never log the headers.
            out["error"] = f"vendor refused ({res.status_code}): {res.text[:200]}"
            return out
        out.update(read_result(res.json()))
        if not out["ok"]:
            out["error"] = "the vendor accepted the request but named no draft order"
    except Exception as e:                       # noqa: BLE001 — a transport failure is a result
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    return out


async def read_products(cfg, token, *, timeout=30, max_pages=40) -> dict:
    """Read the vendor's whole product list. READ-ONLY: GET only, and the only other call this
    module makes is the draft-order POST — there is no path from here to placing an order.

    Returns {ok, rows, pages, error}. `rows` are the shape `store.land_catalog` lands, so the
    prices arrive in the SAME snapshot table, through the SAME lander, as the portal and upload
    routes. A page that fails stops the read with the reason: a half-read catalog landed as a run
    would read as "the vendor dropped those items".
    """
    out = {"ok": False, "rows": [], "pages": 0, "error": ""}
    if not _s(token):
        out["error"] = "no credential is stored for this vendor"
        return out
    try:
        url = products_endpoint(cfg)
    except ValueError as e:
        out["error"] = str(e)
        return out
    host = _s(cfg.get("host")).lower()
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as cx:
            while url and out["pages"] < max_pages:
                res = await cx.get(url, headers={"X-Shopify-Access-Token": _s(token)})
                if res.status_code >= 400:
                    out["error"] = f"vendor refused ({res.status_code}): {res.text[:200]}"
                    return out
                out["rows"] += catalog_rows(res.json(), host=host)
                out["pages"] += 1
                url = next_page(res.headers.get("Link") or res.headers.get("link"))
        out["ok"] = True
        if out["pages"] >= max_pages and url:
            out["error"] = (f"stopped after {max_pages} pages — the catalog is larger than this "
                            f"read allows and the landed run would be partial")
            out["ok"] = False
    except Exception as e:                       # noqa: BLE001 — a transport failure is a result
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    return out
