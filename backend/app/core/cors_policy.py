"""CORS policy — which browser origins may call this API cross-origin (owner 2026-09-28, index §40).

WHY IT CHANGED. The frontend now calls the API SAME-ORIGIN: the browser talks only to the site's own
host and the site proxies /api/v1/* here server-side (frontend/src/lib/apiBase.ts). A same-origin call
needs no CORS at all, and a server-side proxy call carries the browser's Origin only as a forwarded
header — Starlette still serves a non-preflight request whose Origin is not allowed; it just omits the
Access-Control-Allow-* headers, which a same-origin page does not need. So CORS now matters only for
the few callers that are genuinely cross-origin:

  · the app itself, for its DIRECT-class calls (multipart uploads and the long synchronous endpoints),
    until/unless those go through a customer-domain API origin — that caller's origin is the app's
    canonical site, i.e. `settings.APP_PUBLIC_URL`;
  · the marketing site (www / apex), which reads GET /billing/public-pricing;
  · local development (a Next dev server on :3000 calling a local API on :8000).

THE POLICY (pure: `cors_policy(env, app_public_url)`; `backend/harness_cors_policy.py` proves it):
  · `CORS_ORIGINS` (comma-separated) names the allowed origins; unset ⇒ `DEFAULT_ORIGINS`.
  · The app's own canonical origin (APP_PUBLIC_URL) is ALWAYS allowed, whatever CORS_ORIGINS says —
    a typo in that variable must not be able to cut the app off from its own API.
  · NO WILDCARD. A literal `*` entry is dropped (with `allow_credentials=True` Starlette would REFLECT
    any Origin — "every website on the internet"), and so is any origin that is not a bare
    scheme://host[:port].
  · `CORS_ORIGIN_REGEX` is OFF by default. The old default, `https://metricspro[a-z0-9\\-]*\\.vercel\\.app`,
    was a wildcard in practice: platform hostnames are first-come, so anyone can deploy a project
    whose URL matches it. Previews no longer need it (they call their own origin through their own
    proxy). An operator may still set one; it is refused if it would admit an origin we do not own
    (it is tested against canaries on the platform's shared domains and an unrelated domain).

Stdlib only.
"""
import re

# The origins allowed when CORS_ORIGINS is unset. The marketing site on its own host reads one public
# endpoint; localhost covers a Next dev server calling a local API directly. The app's canonical
# origin is added separately (always).
DEFAULT_ORIGINS = (
    # The production app's platform alias — an EXACT origin we own, not a pattern. Kept so the two
    # hosts can deploy in either order: until the frontend's canonical-host redirect is live, users on
    # this alias still make direct-class calls from it. Remove once that redirect has been live a while.
    "https://metricspro-five.vercel.app",
    # The app's customer-facing site (owner 2026-09-28: app.metricspro.tech; the apex stays the marketing site).
    # APP_PUBLIC_URL adds it anyway once set — listed so the direct-class calls work in either deploy order.
    "https://app.metricspro.tech",
    "https://metricspro.tech",
    "https://www.metricspro.tech",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)

_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.-]+(?::\d{1,5})?$")

# An allow-regex that admits any of these admits hosts anyone can register — refuse it.
REGEX_CANARIES = (
    "https://attacker-controlled.vercel.app",
    "https://metricspro-attacker.vercel.app",
    "https://metricspro-evil-project.vercel.app",
    "https://evil.example",
    "https://metricspro.tech.evil.example",
)


def _clean(origin):
    return str(origin or "").strip().rstrip("/")


def cors_policy(env, app_public_url):
    """(env mapping, APP_PUBLIC_URL) → (allowed origins list, allow-origin regex or None, notes list).

    `notes` names every entry that was dropped and why, so startup can log it."""
    notes = []
    raw = [o for o in (_clean(x) for x in str(env.get("CORS_ORIGINS", "") or "").split(",")) if o]
    listed = raw or list(DEFAULT_ORIGINS)
    origins = []
    for o in [_clean(app_public_url)] + listed:
        if not o:
            continue
        if o == "*" or not _ORIGIN.match(o):
            notes.append(f"CORS origin {o!r} dropped: not a bare scheme://host[:port] (wildcards are refused)")
            continue
        if o not in origins:
            origins.append(o)

    regex = str(env.get("CORS_ORIGIN_REGEX", "") or "").strip() or None
    if regex is not None:
        try:
            pat = re.compile(regex)
            hits = [c for c in REGEX_CANARIES if pat.fullmatch(c)]
        except re.error as e:
            hits, notes = ["<invalid>"], notes + [f"CORS_ORIGIN_REGEX refused: invalid regex ({e})"]
        if hits:
            if hits != ["<invalid>"]:
                notes.append(f"CORS_ORIGIN_REGEX refused: it admits {hits[0]} — a host anyone can register")
            regex = None
    return origins, regex, notes
