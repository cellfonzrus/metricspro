"""POS special-order vendor connectors — the plug-and-play adapter layer.

Amazon is only the first dropship vendor. A tenant can register ANY vendor as a `pos.vendor_connector`
row (migration 866) in one of three integration modes; this module turns a connector row into a
`VendorAdapter` that `create_special_order` calls to place an order, so adding a vendor is a data
change, not a code change (except a bespoke outbound API — see below).

Two directions, per the owner directive ("their API to connect to our system, or our API to connect
to them"):

  • manual        — nothing to call: the order sits in the HQ fulfillment queue for a person to place
                    with the vendor. The default, and Amazon at launch.
  • inbound_api   — THE VENDOR pulls (their platform → our API). We expose neutral vendor-facing
                    endpoints (`vendor_api.py`) that the vendor polls, authenticating with a per-vendor
                    token whose SHA-256 hash is stored on the connector. `place_order` just leaves the
                    order queued for that pull.
  • outbound_api  — WE call the vendor's dropship API (our system → their API). The connector names an
                    `api_base_url` and a `credential_ref` — the NAME of the env var / secret holding the
                    API key, NEVER the key itself. A generic JSON adapter covers vendors that take a
                    simple order payload; a vendor with a bespoke API registers its own adapter via
                    `register_adapter("vendor:<key>", cls)`.

Source-hiding is preserved: adapters run server-side only (never in a store/customer response), and the
neutral order record carries no vendor identity beyond the internal `vendor_key`.
"""
from __future__ import annotations

import hashlib
import os


def token_hash(token: str) -> str:
    """SHA-256 hex of an inbound vendor token. The raw token is shown to the vendor once, at
    registration; only this hash is ever stored (pos.vendor_connector.inbound_token_hash)."""
    return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()


class VendorAdapter:
    """Base adapter. `connector` is a pos.vendor_connector row (dict)."""

    mode = "manual"
    # Adapter-supplied config defaults, overridden field-by-field by the connector's own `config` (so a
    # bespoke adapter can ship sane wire defaults while the operator's config still wins).
    default_config: dict = {}

    def __init__(self, connector: dict | None = None):
        self.connector = connector or {}
        self.config = {**self.default_config, **(self.connector.get("config") or {})}

    def place_order(self, order: dict) -> dict:
        """Attempt to place `order` with the vendor. Returns a PARTIAL update for the
        pos.special_orders row — any of {status, vendor_order_ref, tracking, notes}; {} changes
        nothing. MUST NOT raise for a vendor/transport failure: the POS sale is already booked, so a
        failure here simply leaves the order in the queue for manual placement."""
        return {}

    def refresh(self, order: dict) -> dict:
        """Optional: re-poll the vendor for status/tracking. Same return contract as place_order."""
        return {}


class ManualAdapter(VendorAdapter):
    """No integration — the order waits in the HQ fulfillment queue (status 'requested')."""

    mode = "manual"

    def place_order(self, order: dict) -> dict:
        return {"status": "requested"}


class InboundApiAdapter(VendorAdapter):
    """The vendor pulls orders from our API (vendor_api.py). We just leave it queued for that pull."""

    mode = "inbound_api"

    def place_order(self, order: dict) -> dict:
        return {"status": "requested"}


class OutboundApiAdapter(VendorAdapter):
    """Generic outbound JSON adapter: WE POST the order to the vendor's dropship API.

    Config-driven so a plain REST vendor needs no code (all keys optional, with sane defaults):
      place_path     path appended to api_base_url            (default "/orders")
      auth_header    header carrying the credential           (default "Authorization")
      auth_scheme    scheme prefix, "" for a bare token       (default "Bearer")
      timeout        request timeout in seconds               (default 20)
      order_ref_key  response key holding the vendor order id (default "order_id")
      tracking_key   response key holding tracking            (default "tracking")
    A vendor whose API doesn't fit this shape registers its own adapter subclass instead."""

    mode = "outbound_api"

    def _credential(self) -> str:
        ref = (self.connector.get("credential_ref") or "").strip()
        # credential_ref is the NAME of an env/secret; the raw key never lives in the DB.
        return (os.environ.get(ref, "") if ref else "").strip()

    def _payload(self, order: dict) -> dict:
        return {"sku": order.get("vendor_sku"), "quantity": order.get("qty"),
                "ship_to": order.get("ship_to"), "reference": order.get("reference")}

    def place_order(self, order: dict) -> dict:
        import requests  # local import: keeps module import cheap and matches google_reviews style
        base = (self.connector.get("api_base_url") or "").rstrip("/")
        if not base:
            return {"status": "requested",
                    "notes": "outbound connector has no api_base_url — queued for manual placement"}
        cfg = self.config
        url = base + (cfg.get("place_path") or "/orders")
        headers = {"Content-Type": "application/json"}
        key = self._credential()
        if key:
            scheme = cfg.get("auth_scheme", "Bearer")
            headers[cfg.get("auth_header") or "Authorization"] = f"{scheme} {key}".strip()
        try:
            r = requests.post(url, json=self._payload(order), headers=headers,
                              timeout=cfg.get("timeout", 20))
            r.raise_for_status()
            data = r.json() if r.content else {}
        except Exception as e:
            # Never blocks the sale; the order falls back to the manual queue with a breadcrumb.
            return {"status": "requested",
                    "notes": f"vendor API not reached — queued for manual placement ({e})"}
        ref = data.get(cfg.get("order_ref_key", "order_id"))
        return {"status": "ordered",
                "vendor_order_ref": (str(ref) if ref not in (None, "") else None),
                "tracking": data.get(cfg.get("tracking_key", "tracking"))}

    def refresh(self, order: dict) -> dict:
        """Re-poll the vendor for status/tracking. Config-driven and honest: a no-op unless the connector
        supplies a `status_path` (e.g. "/orders/{ref}") and we hold a vendor order ref. Never raises."""
        import requests
        base = (self.connector.get("api_base_url") or "").rstrip("/")
        cfg = self.config
        status_path = cfg.get("status_path")
        ref = order.get("vendor_order_ref")
        if not base or not status_path or not ref:
            return {}
        url = base + str(status_path).replace("{ref}", str(ref))
        headers = {"Accept": "application/json"}
        key = self._credential()
        if key:
            scheme = cfg.get("auth_scheme", "Bearer")
            headers[cfg.get("auth_header") or "Authorization"] = f"{scheme} {key}".strip()
        try:
            r = requests.get(url, headers=headers, timeout=cfg.get("timeout", 20))
            r.raise_for_status()
            data = r.json() if r.content else {}
        except Exception as e:
            return {"notes": f"vendor status not reached ({e})"}
        out = {}
        st = data.get(cfg.get("status_key", "status"))
        # Map the vendor's status vocabulary onto ours only when it is one of ours; otherwise leave it.
        if st in ("ordered", "shipped", "received", "delivered", "cancelled"):
            out["status"] = st
        tr = data.get(cfg.get("tracking_key", "tracking"))
        if tr:
            out["tracking"] = tr
        return out


class AmazonBusinessAdapter(OutboundApiAdapter):
    """Amazon Business dropship ordering (the Amazon Business API / SP-API ordering program).

    Amazon's ordering APIs are enrollment- and approval-gated (the Amazon Business API program + a
    Login-with-Amazon access token), so this adapter is REAL where it can be and an HONEST no-op
    otherwise — it never fakes a success:

      • the connector is `manual` (the launch default)  → the order waits in the HQ fulfillment queue;
      • it is `outbound_api` but missing an api_base_url or its credential env var → queued for manual
        placement WITH a breadcrumb note;
      • it is `outbound_api` and fully configured → we POST an Amazon-shaped order (ASIN + quantity +
        ship-to) to the operator-approved endpoint, using the LWA/API token from the env var named by
        `credential_ref`, and read back the order id + tracking.

    Every wire detail is `config`-overridable so the exact endpoint/payload the operator is approved for
    is DATA, not code. Defaults target the documented Amazon Business ordering shape; an Amazon connector
    carries the ASIN in `special_order_vendor.vendor_sku` (surfaced to the adapter as `order['vendor_sku']`)."""

    mode = "outbound_api"
    default_config = {
        "place_path": "/orders",
        # SP-API carries the Login-with-Amazon token in x-amz-access-token with no scheme prefix.
        "auth_header": "x-amz-access-token", "auth_scheme": "",
        # request payload field names
        "items_key": "lineItems", "asin_key": "asin", "qty_key": "quantity",
        "ship_to_key": "shipToStoreCode", "ship_to_address_key": "shipToAddress",
        "reference_key": "clientReferenceId",
        # response field names
        "order_ref_key": "orderId", "tracking_key": "trackingId", "status_key": "status",
    }

    def place_order(self, order: dict) -> dict:
        # Amazon stays MANUAL until the operator EXPLICITLY switches the connector to outbound_api AND
        # supplies credentials — this is what keeps the Phase-0 ToS gate intact (auto-order is opt-in).
        if (self.connector.get("integration_mode") or "manual").strip() != "outbound_api":
            return {"status": "requested"}
        if not (self.connector.get("api_base_url") or "").strip() or not self._credential():
            return {"status": "requested",
                    "notes": "Amazon auto-order not configured (needs api_base_url + the credential_ref "
                             "secret) — queued for manual placement"}
        return super().place_order(order)

    def _payload(self, order: dict) -> dict:
        cfg = self.config
        item = {cfg["asin_key"]: order.get("vendor_sku"), cfg["qty_key"]: order.get("qty")}
        body = {cfg["items_key"]: [item],
                cfg["ship_to_key"]: order.get("ship_to"),
                cfg["reference_key"]: order.get("reference")}
        # A resolved street address (operator-configured store→address map) rides along when provided;
        # otherwise the ship-to store CODE is sent for the operator's endpoint/proxy to resolve.
        if order.get("ship_to_address"):
            body[cfg["ship_to_address_key"]] = order["ship_to_address"]
        return body


_ADAPTERS: dict[str, type[VendorAdapter]] = {}


def register_adapter(key: str, cls: type[VendorAdapter]) -> None:
    """Register an adapter class under a mode ('outbound_api') or a vendor override ('vendor:acme').
    A 'vendor:<vendor_key>' registration wins over the generic mode adapter for that vendor."""
    _ADAPTERS[key] = cls


def get_adapter(connector: dict | None) -> VendorAdapter:
    """Resolve a connector row to its adapter. A vendor-specific override
    ('vendor:<vendor_key>') takes precedence over the integration_mode default; unknown modes fall
    back to manual (fail-safe: an order never silently escapes to an unconfigured integration)."""
    c = connector or {}
    mode = (c.get("integration_mode") or "manual").strip()
    vkey = (c.get("vendor_key") or "").strip()
    cls = _ADAPTERS.get(f"vendor:{vkey}") or _ADAPTERS.get(mode) or ManualAdapter
    return cls(c)


register_adapter("manual", ManualAdapter)
register_adapter("inbound_api", InboundApiAdapter)
register_adapter("outbound_api", OutboundApiAdapter)
# Amazon override: selected for any connector whose vendor_key == 'amazon', regardless of mode. It
# stays manual unless the connector is explicitly outbound_api + credentialed, so the launch-default
# manual Amazon connector is unaffected (see AmazonBusinessAdapter.place_order).
register_adapter("vendor:amazon", AmazonBusinessAdapter)
