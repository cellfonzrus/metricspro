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


def tag_for(po_number) -> str:
    return TAG_PREFIX + re.sub(r"[^A-Za-z0-9_-]+", "-", _s(po_number)).strip("-").lower()


def draft_order_payload(po, lines, *, note=None) -> dict:
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


async def push_draft_order(cfg, token, po, lines, *, note=None, timeout=30) -> dict:
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
    payload = draft_order_payload(po, lines, note=note)
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
