"""REQUEST PATHS CARRY CREDENTIALS — strip them before anything writes a log row. PURE (stdlib only).

WHY THIS EXISTS (owner question, 2026-09-20: *"why does our url have secrets — should all of those be
masked?"*). Two public flows authenticate with a CAPABILITY URL, because there is nobody to
authenticate yet: a walk-in customer scanning a referral QR on their own phone, and a pre-start
employee who has no account. The URL IS the credential. That is the correct pattern — the same one
password-reset and magic links use — and both are built carefully: the referral token is an
HMAC-SHA256 capability over one referral id + version with expiry and single-use enforced by the ROW,
and the onboarding token is 192 bits of `secrets.token_urlsafe(24)`, revocable, with a date-of-birth
gate on top.

What was NOT handled is where those URLs get WRITTEN DOWN. `core.access_log` records one row per
request with `path[:400]` and `query[:400]`, and none of the twelve token-bearing endpoints are in its
skip list. So every redemption and every onboarding action wrote a live credential into our own
database in plaintext — and on the onboarding document endpoints the date-of-birth gate travels as
`?value=…`, so a single row held BOTH factors. A deliberately short-lived, single-use capability
became a durable plaintext record that outlives the token by years.

`db_resilience` already had this instinct — it drops the query string because *"filters carry org_id /
emails"* — but nothing applied the same reasoning to path SEGMENTS.

ONE FACT, ONE HOME — AND THIS DEREFERENCES IT. "Which URL segments are secrets" is already declared,
precisely, by the route definitions themselves: a route written `/public/onboarding/{token}` names its
own sensitive parameter. So this module takes the app's route templates as INPUT and derives the
redaction from them. It keeps no second list of sensitive paths, which is exactly the copy that would
drift the next time a token route is added.

WHAT IS KEPT. Only the sensitive SEGMENTS are masked; every other path parameter survives, because an
opaque row id (`{task_id}`, `{file_id}`, `{rid}`) is what makes the log worth having. A redacted row
reads `/api/v1/hr/public/onboarding/{token}/task/9f3c…/document/aa12…` — you can still see who did
what to which file, just not the credential that let them.

TWO LAYERS, DELIBERATELY. The precise layer matches the concrete path against the route templates. The
BACKSTOP catches anything under a sensitive route's literal prefix that the precise layer missed — a
trailing slash, a `:path` converter, a 404 under that prefix that never matched a route at all. The
backstop is derived from the SAME templates, so it is not a second home either; it is the same fact,
applied more bluntly. A 404 probe at `/hr/public/onboarding/<stolen-token>/nope` is still a token in
a log row, so it must be caught even though no route matched it.

FAIL CLOSED. Every entry point is wrapped so a redaction fault can never raise into a logging path,
and the fallback on any internal error is MORE masking, never less. `_FALLBACK_RULES` keeps the most
recently compiled rules so a call site that cannot reach the app object still redacts — derived, never
hardcoded.

Proven by `backend/harness_path_redact.py` (DB-free, stdlib), which also FAILS THE BUILD if a logging
call site stops dereferencing this module.
"""
import re

# Path-parameter names that are ALWAYS a credential when they appear. Today only `{token}` exists in
# the app (13 routes); the rest are listed so a future route named `{secret}` is covered the day it is
# written rather than the day someone notices. Deliberately NOT included: `{key}`, `{code_id}`,
# `{report_key}`, `{store_code}` and friends — those are business identifiers, and masking them would
# cost real audit value for no security gain.
SENSITIVE_PARAMS = frozenset({
    "token", "access_token", "refresh_token", "secret", "signature", "nonce", "password", "api_key",
})

# What a masked segment reads as in the stored row: the parameter's own name, back in braces.
_PARAM = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# The whole query string is dropped on a capability route (see `redact_query`).
QUERY_MASK = "[redacted]"

# Set by `compile_rules`; used only when a call site cannot reach the app object. Derived, not written.
_FALLBACK_RULES = None


def params_in(path_format):
    """Every path-parameter name in a route template, in order. PURE."""
    return tuple(m.group(1) for m in _PARAM.finditer(str(path_format or "")))


def sensitive_params_in(path_format):
    """The subset of a template's parameters that name a credential. PURE."""
    return tuple(p for p in params_in(path_format) if p in SENSITIVE_PARAMS)


def is_sensitive(path_format):
    """True when a route template carries a credential in its PATH. PURE."""
    return bool(sensitive_params_in(path_format))


def _to_regex(path_format):
    """A route template → an anchored regex with one named group per parameter. PURE.

    `[^/]+` per segment on purpose: a template is matched segment-for-segment, so a longer path never
    matches a shorter template. The optional trailing slash is what stops `/redeem/<tok>/` slipping
    past the precise layer and landing on the backstop."""
    out, last = [], 0
    seen = set()
    for m in _PARAM.finditer(path_format):
        name = m.group(1)
        if name in seen:                     # a duplicate name cannot be a named group; skip the
            return None                      # template and let the backstop cover it
        seen.add(name)
        out.append(re.escape(path_format[last:m.start()]))
        out.append("(?P<%s>[^/]+)" % name)
        last = m.end()
    out.append(re.escape(path_format[last:]))
    try:
        return re.compile("^" + "".join(out) + "/?$")
    except re.error:
        return None


def _prefix_of(path_format):
    """(literal prefix before the FIRST sensitive parameter, that parameter's name), or None. PURE."""
    for m in _PARAM.finditer(path_format):
        if m.group(1) in SENSITIVE_PARAMS:
            return path_format[:m.start()], m.group(1)
    return None


def compile_rules(path_formats):
    """Route templates → the immutable rule set `redact` consumes. PURE (bar the fallback cache).

    Only templates that actually carry a credential are compiled: a route with no sensitive parameter
    needs no work at request time, and leaving it out keeps the matcher small and the common path
    fast."""
    global _FALLBACK_RULES
    matchers, prefixes = [], {}
    for fmt in path_formats or []:
        if not isinstance(fmt, str) or not is_sensitive(fmt):
            continue
        rx = _to_regex(fmt)
        if rx is not None:
            matchers.append((rx, frozenset(sensitive_params_in(fmt))))
        pre = _prefix_of(fmt)
        if pre:
            prefixes[pre[0]] = pre[1]
    # Longest prefix first: the most specific backstop wins.
    rules = (tuple(matchers), tuple(sorted(prefixes.items(), key=lambda kv: -len(kv[0]))))
    _FALLBACK_RULES = rules
    return rules


EMPTY_RULES = ((), ())


def _backstop(path, prefixes):
    """Mask the segment directly after a sensitive route's literal prefix. PURE.

    This is what catches the paths the precise layer cannot: a 404 under a token prefix, an extra
    segment, a converter this module does not model. Everything after that segment is kept."""
    for prefix, name in prefixes:
        if path.startswith(prefix):
            rest = path[len(prefix):]
            if not rest:
                return path
            seg, slash, tail = rest.partition("/")
            if not seg:
                return path
            return prefix + "{%s}" % name + slash + tail
    return path


def redact(path, rules):
    """A concrete request path with every credential segment masked. PURE. Never raises.

    Precise layer first (it preserves the non-sensitive parameters), backstop second. A path that
    matches no sensitive route is returned unchanged — this masks credentials, not logs."""
    try:
        p = str(path or "")
        if not p:
            return p
        matchers, prefixes = rules or EMPTY_RULES
        for rx, sensitive in matchers:
            m = rx.match(p)
            if not m:
                continue
            # Replace from the RIGHT so each span stays valid as the string is rewritten.
            spans = sorted((m.span(n) for n in sensitive if m.group(n) is not None), reverse=True)
            names = {m.span(n): n for n in sensitive if m.group(n) is not None}
            for span in spans:
                s, e = span
                p = p[:s] + "{%s}" % names[span] + p[e:]
            return p
        return _backstop(p, prefixes)
    except Exception:
        # A redaction fault must never leak the thing it was redacting, and must never break logging.
        try:
            return _backstop(str(path or ""), (rules or EMPTY_RULES)[1])
        except Exception:
            return "[redaction-failed]"


def is_sensitive_path(path, rules):
    """True when this concrete path reached a capability route. PURE. Never raises.

    Used to decide the QUERY, because on those public endpoints the query carries the SECOND factor
    (`?value=<date-of-birth>` on the onboarding document routes)."""
    try:
        return redact(path, rules) != str(path or "")
    except Exception:
        return True                          # unsure ⇒ treat as sensitive and drop the query


def redact_query(path, query, rules):
    """The query string to store for this request. PURE. Never raises.

    The WHOLE query is dropped on a capability route rather than named keys being filtered: those
    endpoints are the ones that carry the gate value, and an allow-list of safe key names would be a
    second copy of a fact that lives in the handler signatures. Every other route keeps its query
    exactly as before."""
    try:
        q = query or ""
        if not q:
            return ""
        if not isinstance(q, str):
            return QUERY_MASK        # not a query string at all ⇒ mask; never hand this to a log row
        return QUERY_MASK if is_sensitive_path(path, rules) else q
    except Exception:
        return QUERY_MASK


# ── Wiring helpers (the only impure part: a per-app cache of the compiled rules) ──────────────────
_CACHE = {}
_MAX_ROUTER_DEPTH = 8


def route_templates(node, prefix="", _depth=0):
    """Every FULL route template under an app or router, walking INCLUDED ROUTERS.

    `app.routes` is NOT a flat list of endpoints in this FastAPI: including a router leaves a single
    `_IncludedRouter` container in its place, holding the real `APIRouter` on `.original_router` and
    the mount prefix on `.include_context.prefix`. The first version of this module read only the top
    level and so found 31 entries instead of ~1500 — it compiled ZERO sensitive routes and redacted
    nothing, while the unit tests passed against fixtures. That is why `harness_path_redact` §F
    exercises this against real FastAPI objects rather than strings.

    Written to survive either shape: a flattened `app.routes` (other FastAPI versions) yields its
    templates directly, and a container is recursed into with its prefix prepended. Never raises."""
    out = []
    if _depth > _MAX_ROUTER_DEPTH:
        return out
    try:
        routes = getattr(node, "routes", None)
        if routes is None:
            routes = getattr(getattr(node, "router", None), "routes", None)
        for r in routes or []:
            f = getattr(r, "path_format", None) or getattr(r, "path", None)
            if isinstance(f, str) and f:
                out.append(prefix + f)
            nested = getattr(r, "original_router", None) or getattr(r, "router", None)
            if nested is not None and nested is not r and nested is not node:
                ctx = getattr(r, "include_context", None)
                sub = getattr(ctx, "prefix", None) or getattr(r, "prefix", None) or ""
                if not isinstance(sub, str):
                    sub = ""
                out.extend(route_templates(nested, prefix + sub, _depth + 1))
    except Exception:
        pass
    return out


def rules_for_app(app):
    """Compiled rules for a FastAPI/Starlette app, built once and cached.

    The route table IS the home of "which segments are secrets", so this reads it rather than
    restating it. Falls back to the most recently compiled rules when the app object cannot be
    reached — derived from a real route table, never a hardcoded list."""
    try:
        if app is None:
            return _FALLBACK_RULES or EMPTY_RULES
        key = id(app)
        hit = _CACHE.get(key)
        if hit is None:
            hit = compile_rules(route_templates(app))
            _CACHE[key] = hit
        return hit
    except Exception:
        return _FALLBACK_RULES or EMPTY_RULES


def redact_for_app(app, path):
    """`redact` against an app's own routes. Never raises — the logging-path entry point."""
    return redact(path, rules_for_app(app))


def redact_query_for_app(app, path, query):
    """`redact_query` against an app's own routes. Never raises."""
    return redact_query(path, query, rules_for_app(app))
