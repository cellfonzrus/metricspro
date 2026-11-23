"""★ ONE HOME: may a router-level auth gate refuse THIS request?

WHY THIS FILE EXISTS (2026-11-23, adversarial access probe run 327, module `pos`, index §CORE-48).

`GET /api/v1/pos/public/w9/{token}` is the link a wholesale customer is emailed to send back their
completed Form W-9. It is DECLARED public in `tenant_middleware._PUBLIC_PREFIXES` — the one-use
token is the auth, it reaches one customer's upload slot, it expires. The deployed platform
answered every anonymous caller:

    GET https://api.metricspro.tech/api/v1/pos/public/w9/<token>
    -> 401 {"detail":"sign in to perform this action"}

The token was never read. The `pos` APIRouter carries `dependencies=[Depends(_require_pos_access)]`
and a ROUTER-LEVEL dependency runs before the endpoint body, for every route the router mounts —
the declared-public ones included. So 100% of W-9 return links were dead, and nothing noticed,
because every control here proves the gate REFUSES and none proved it STANDS DOWN.

THE CLASS, not the instance: "is this path public" had one home and a router-level gate was a
SECOND, independent gate that could not dereference it. Every router carrying a router-level auth
gate had the same hole, for every public path mounted under it today and every one added tomorrow.

THE FIX: a router-level gate ASKS this module and returns without refusing when the answer is yes.
It is a single dereference of `tenant_middleware.public_surface_allows` — never a copy of the
allowlist, never a path literal — so the declaration and the gates cannot drift.
`harness_router_gate_dereference.py` fails the build if a router-level gate stops asking, or if a
second copy of the answer appears.

NOT A HOLE. The stand-down is exactly as wide as the public declaration and no wider: method
scoping is honoured (`/core/auth-config` is public for GET, never for the PUT that flips the
global enforce-login flag), prefixes match at a segment boundary, and everything else still meets
the gate. The paths that stand down authenticate by their own mechanism inside the handler — a
one-use link token, an HMAC capability, a cron secret, a verified JWT in `/core/me`.
"""

# IMPORT-LIGHT ON PURPOSE. This module is imported by `modules/core/entitlements`, which four
# stdlib-only proof harnesses import with `fastapi` replaced by a stub carrying `HTTPException` and
# nothing else. A `from fastapi import Request` here made every one of them die on
# `ImportError: cannot import name 'Request'` — a gate that cannot be imported is worse than one
# that does not stand down. So: no fastapi import, and `tenant_middleware` is imported INSIDE the
# function, where a caller that never gates a request never pays for it. `request` is duck-typed
# because only two attributes are ever read.


def stand_down(request) -> bool:
    """True when a router-level auth gate must NOT refuse: the platform declared this path public
    for this method, and the handler carries its own auth.

    Fails CLOSED. No request (a gate called outside a request, a unit test, a stub) means no
    declaration to stand down on, so the gate refuses exactly as it did before.
    """
    if request is None:
        return False
    try:
        from app.core.tenant_middleware import public_surface_allows

        path = request.url.path
        method = request.method
    except Exception:
        return False
    return public_surface_allows(path, method)
