"""ONE HOME — "how does an order actually REACH this vendor?"

Owner 2026-10-03/04: the store-visit accessory list raises a draft purchase order, and the owner
asked for it to go to the vendor's own store, which is a Shopify store they own.

THE CLASS, NOT THE INSTANCE (CLAUDE.md, "A fix is a DESIGN fix"). The instance is one vendor with
an admin API. The class is: **a vendor's order route is a FACT about that vendor, and it has to be
declared once and read by every caller** — the store-visit sweep, the supply ordering screen, and
whatever asks next. Before this file there was exactly one route (a scripted portal walk, see
`ordering_logic.parse_recipe`) and it was implied rather than declared, so a second route could
only arrive as a sibling branch at every call site.

RULE TWO — config, never code. No vendor, store or brand name appears here. The route lives on the
vendor row (`commcalc.po_vendor.portal_config["order_transport"]`), and the API dialect is a VALUE
in that config, not a branch in this module. A tenant with nothing declared gets `none`, which is
the safe default: a draft is raised in MetricsPro and nothing leaves the building.

KINDS
  none    the house default — no route; the draft stays in MetricsPro. Also what an unreadable or
          invalid declaration resolves to, with the reason carried, because refusing to send is
          always safer than guessing how.
  portal  the existing scripted browser walk (`ordering_logic.parse_recipe`). DERIVED, never
          re-declared: a vendor that already carries an `ordering` recipe already has this route,
          and nobody has to restate it to keep it.
  api     an HTTP call to the vendor's own order API. `dialect` names which API shape to speak;
          `shopify_draft_order.py` is the only dialect today and is selected by that VALUE.

CREDENTIALS ARE NOT CONFIG. A token never lives in `portal_config` — `validate_transport` REFUSES a
declaration that carries one, the same posture `normalize_portal_config` already takes. The secret
lives in the vendor's `commcalc.data_source` login row, in a column of
`commcalc/router._SOURCE_SECRETS`, so it never leaves the backend and never appears in a read.

Pure: no I/O, no client, no network. The caller fetches rows and does the sending.
"""
from __future__ import annotations

KINDS = ("none", "portal", "api")

# An API dialect this platform can speak. A dialect the code does not implement resolves to `none`
# with a reason, rather than a half-send.
DIALECTS = ("shopify_draft_order",)

# Keys that must NEVER appear in a transport declaration — a declaration is config and is readable
# by the supply pages. Mirrors commcalc/router._SOURCE_SECRETS plus the obvious API spellings.
FORBIDDEN_KEYS = ("password", "token", "access_token", "api_token", "api_key", "apikey",
                  "secret", "client_secret", "totp_secret", "session_state", "pending_state",
                  "authorization", "bearer")

CONFIG_KEY = "order_transport"


def _s(v) -> str:
    return str(v or "").strip()


def _carries_secret(obj, path="order_transport"):
    """Every place a credential could hide in a declaration, named by where it is."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = _s(k).lower().replace("-", "_")
            if any(bad == kl or kl.endswith("_" + bad) for bad in FORBIDDEN_KEYS):
                found.append(f"{path}.{k}")
            found += _carries_secret(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            found += _carries_secret(v, f"{path}[{i}]")
    return found


def validate_transport(decl) -> list:
    """Errors in a transport declaration. [] means it may be stored. A credential is an ERROR, not
    a thing to strip quietly: stripping teaches whoever typed it that it was accepted."""
    errors: list = []
    if decl in (None, "", {}):
        return errors                                   # absent is legal; it means `none`
    if not isinstance(decl, dict):
        return [f"{CONFIG_KEY} must be an object"]
    for where in _carries_secret(decl):
        errors.append(f"{where} looks like a credential — a token belongs in the vendor's login "
                      f"row, never in config that the supply pages can read")
    kind = _s(decl.get("kind")).lower()
    if kind and kind not in KINDS:
        errors.append(f"{CONFIG_KEY}.kind must be one of {', '.join(KINDS)}")
    if kind == "api":
        api = decl.get("api")
        if not isinstance(api, dict):
            errors.append(f"{CONFIG_KEY}.api must be an object when kind is 'api'")
        else:
            dialect = _s(api.get("dialect"))
            if not dialect:
                errors.append(f"{CONFIG_KEY}.api.dialect is required when kind is 'api'")
            elif dialect not in DIALECTS:
                errors.append(f"{CONFIG_KEY}.api.dialect {dialect!r} is not a dialect this "
                              f"platform speaks ({', '.join(DIALECTS)})")
            if not _s(api.get("host")):
                errors.append(f"{CONFIG_KEY}.api.host is required when kind is 'api'")
            if not _s(api.get("version")):
                errors.append(f"{CONFIG_KEY}.api.version is required when kind is 'api' — an API "
                              f"version left to a default moves under you when the vendor retires it")
    return errors


def _portal_route(vendor) -> dict:
    """Does this vendor already carry a scripted portal walk? DERIVED from the recipe it already
    has, so the existing vendors keep their route without anybody restating it."""
    cfg = (vendor or {}).get("portal_config") or {}
    ordering = cfg.get("ordering") if isinstance(cfg, dict) else None
    if isinstance(ordering, dict) and (ordering.get("line_steps") or ordering.get("cart_url")):
        return {"kind": "portal", "config": ordering,
                "reason": "the vendor carries an ordering recipe",
                "can_send": bool(ordering.get("submit_steps")),
                "sends_on_its_own": False}
    return {}


def transport_for(vendor) -> dict:
    """THE ANSWER, for one vendor row. Always a dict; never raises.

    {kind, config, reason, can_send, sends_on_its_own}

    `can_send` is whether this route can reach the vendor at all. `sends_on_its_own` is whether
    using it PLACES an order — false for a draft route, which is what makes a draft route safe to
    run unattended. A caller that treats the two as the same thing is the bug this pair prevents.
    """
    vendor = vendor or {}
    cfg = vendor.get("portal_config") or {}
    decl = cfg.get(CONFIG_KEY) if isinstance(cfg, dict) else None

    if decl:
        errors = validate_transport(decl)
        if errors:
            return {"kind": "none", "config": {}, "can_send": False, "sends_on_its_own": False,
                    "reason": "the declared route is not valid: " + "; ".join(errors[:2])}
        kind = _s(decl.get("kind")).lower() or "none"
        if kind == "api":
            api = dict(decl.get("api") or {})
            # A draft route does not place the order. Declaring otherwise is possible but must be
            # EXPLICIT, because it turns an unattended sweep into a spending action.
            places = bool(api.get("places_order"))
            return {"kind": "api", "config": api, "can_send": True, "sends_on_its_own": places,
                    "reason": f"declared api route, dialect {api.get('dialect')}"}
        if kind == "portal":
            return _portal_route(vendor) or {
                "kind": "none", "config": {}, "can_send": False, "sends_on_its_own": False,
                "reason": "a portal route is declared but the vendor carries no ordering recipe"}
        if kind == "none":
            return {"kind": "none", "config": {}, "can_send": False, "sends_on_its_own": False,
                    "reason": "the vendor declares no route"}

    derived = _portal_route(vendor)
    if derived:
        return derived
    return {"kind": "none", "config": {}, "can_send": False, "sends_on_its_own": False,
            "reason": "the vendor declares no route and carries no ordering recipe"}


def push_enabled(route, cfg_push) -> tuple:
    """May the sweep use this route unattended? (bool, reason).

    THREE things must all be true, and they are deliberately separate facts:
      · the tenant switched the push ON (`cfg_push`) — off is the house default;
      · the route can reach the vendor at all;
      · the route does NOT place the order by itself.
    The third is what keeps an unattended sweep from spending money: a route that places an order
    is refused here however loudly the tenant switched it on, and a human drives it instead.
    """
    route = route or {}
    if not cfg_push:
        return False, "the vendor push is switched off for this tenant"
    if not route.get("can_send"):
        return False, route.get("reason") or "the vendor has no route"
    if route.get("sends_on_its_own"):
        return False, ("this route PLACES the order rather than drafting it, so it is never run "
                       "unattended — a person sends it")
    return True, f"{route.get('kind')} route, draft only"
