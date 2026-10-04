"""ONE HOME — "what bearer token do I use for this vendor's API right now?"

Owner 2026-10-04, after Shopify removed custom apps from the store admin on 1 January 2026: a
vendor API credential is no longer a permanent token somebody pastes in once. It is a client id and
a client secret, exchanged for a SHORT-LIVED token (about 24 hours) whenever one is needed.

THE CLASS, NOT THE INSTANCE. The instance is one vendor whose tokens now expire. The class is:
**"is the credential I hold still usable, and if not how do I get one" is a question with one
answer per vendor, and every caller must ask rather than decide.** A caller that reads a stored
string and sends it is correct for exactly as long as the vendor keeps permanent tokens — which is
precisely the assumption that just broke.

WHERE THINGS LIVE — no new table, no migration. The vendor's `commcalc.data_source` login row,
which is already THE credential store for this platform (`commcalc/router._SOURCE_SECRETS`):

  username       the client id       (an app identifier; not a secret, but kept beside its secret)
  password       the client secret   (a secret column — never leaves the backend, never in a read)
  session_state  the minted token and its expiry, as JSON — a secret column already used for
                 exactly this: short-lived material the backend holds on a tenant's behalf.

POLICY IS PURE, SENDING IS NOT. Everything that decides — is this cached token still good, what
does the exchange ask for, what do we write back — is a pure function proved DB-free. One async
function performs the exchange, and it is the only place this module touches the network.

A MINTED TOKEN IS NEVER RETURNED TO A CALLER THAT ONLY WANTED TO KNOW. `describe()` is what a
screen may see: whether a credential exists and when the current token expires. Never the token.
"""
from __future__ import annotations

import json
import time

GRANT = "client_credentials"
# Mint early rather than at the edge: a token that expires mid-request is a failure a human has to
# read about, and the exchange is cheap.
REFRESH_MARGIN_SECONDS = 600
# Refuse to believe a lifetime longer than this; a vendor that returns nonsense must not pin a dead
# token in place for a year.
MAX_LIFETIME_SECONDS = 7 * 24 * 3600


def _s(v) -> str:
    return str(v or "").strip()


def _now(now=None) -> float:
    return float(now if now is not None else time.time())


def cached(login_row) -> dict:
    """The token material held for this login. {} when there is none or it is unreadable.

    Unreadable is deliberately the same as absent: a corrupt cache must cause a fresh mint, never
    an exception on a path whose job is to send an order.
    """
    raw = (login_row or {}).get("session_state")
    if isinstance(raw, dict):
        blob = raw
    else:
        try:
            blob = json.loads(_s(raw) or "{}")
        except (ValueError, TypeError):
            return {}
    if not isinstance(blob, dict):
        return {}
    tok = _s(blob.get("access_token"))
    if not tok:
        return {}
    try:
        exp = float(blob.get("expires_at") or 0)
    except (TypeError, ValueError):
        exp = 0.0
    return {"access_token": tok, "expires_at": exp, "scope": _s(blob.get("scope"))}


def usable(login_row, *, now=None) -> str:
    """The cached token IF it will still be valid for the whole of the next request. Else ""."""
    c = cached(login_row)
    if not c:
        return ""
    if c["expires_at"] <= _now(now) + REFRESH_MARGIN_SECONDS:
        return ""
    return c["access_token"]


def has_credential(login_row) -> bool:
    """Is there anything to mint WITH? A cached token alone does not count — it expires."""
    return bool(_s((login_row or {}).get("username"))
                and _s((login_row or {}).get("password")))


def token_endpoint(host) -> str:
    h = _s(host).lower()
    if not h:
        raise ValueError("no host")
    return f"https://{h}/admin/oauth/access_token"


def mint_body(login_row) -> dict:
    """What the exchange asks for. The secret appears here and NOWHERE else in this module."""
    return {"client_id": _s((login_row or {}).get("username")),
            "client_secret": _s((login_row or {}).get("password")),
            "grant_type": GRANT}


def store_patch(data, *, now=None) -> dict:
    """What to write back to the login row after a successful exchange.

    Only `session_state` — the client id and secret are untouched, because an exchange never
    changes them, and rewriting a credential on a read path is how credentials get lost.
    """
    data = data or {}
    tok = _s(data.get("access_token"))
    if not tok:
        return {}
    try:
        life = float(data.get("expires_in") or 0)
    except (TypeError, ValueError):
        life = 0.0
    life = min(max(life, 0.0), MAX_LIFETIME_SECONDS)
    return {"session_state": json.dumps({
        "access_token": tok,
        "expires_at": _now(now) + life,
        "scope": _s(data.get("scope")),
        "minted_at": _now(now),
    })}


def describe(login_row, *, now=None) -> dict:
    """What a SCREEN may know. Never the token itself — presence and expiry only."""
    c = cached(login_row)
    return {
        "has_credential": has_credential(login_row),
        "has_token": bool(c),
        "expires_in": max(0, int(c["expires_at"] - _now(now))) if c else 0,
        "usable": bool(usable(login_row, now=now)),
    }


async def access_token(client, org_id, login_row, host, *, timeout=30, now=None) -> dict:
    """THE ANSWER: {token, error, minted}. The only place this module speaks to the network.

    A cached token that is still good is returned without a call. Otherwise one exchange happens
    and the result is written back to the login row — written back even though the caller holds the
    token in hand, because the NEXT caller must not have to mint again.
    """
    out = {"token": "", "error": "", "minted": False}
    good = usable(login_row, now=now)
    if good:
        out["token"] = good
        return out
    if not has_credential(login_row):
        out["error"] = ("no API credential is stored for this vendor — a client id and client "
                        "secret go in its login row, never in config")
        return out
    try:
        url = token_endpoint(host)
    except ValueError as e:
        out["error"] = str(e)
        return out
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as cx:
            res = await cx.post(url, json=mint_body(login_row),
                                headers={"Content-Type": "application/json"})
        if res.status_code >= 400:
            # The body can echo what was sent, which includes the secret. Status only.
            out["error"] = (f"the vendor refused the credential exchange ({res.status_code}) — "
                            f"check the client id and secret, and that the app is installed on "
                            f"this store")
            return out
        data = res.json()
    except Exception as e:                       # noqa: BLE001 — a transport failure is a result
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        return out

    patch = store_patch(data, now=now)
    if not patch:
        out["error"] = "the vendor accepted the exchange but returned no token"
        return out
    out["token"] = json.loads(patch["session_state"])["access_token"]
    out["minted"] = True
    try:
        (client.schema("commcalc").table("data_source").update(patch)
         .eq("org_id", org_id).eq("id", (login_row or {}).get("id")).execute())
    except Exception as e:
        # A token we could not cache is still a token we can USE for this request. Say so rather
        # than throwing away a good credential over a write failure.
        out["error"] = f"minted but not cached ({type(e).__name__}); the next run will mint again"
    return out
