"""SSRF guard — ONE validator for every tenant-supplied URL that becomes a fetch/render target.

WHY THIS FILE EXISTS (security finding C4, 2026-08-05)
-----------------------------------------------------
`data_source.portal_url` / `epay_sweep_config.portal_url` / `data_source.proxy_url` are edited by a
TENANT ADMIN through the Data Imports settings UI, and they are then handed to a headless Chromium
launched with `--no-sandbox`. The only validation was a substring test::

    row["portal_url"] = pu if "://" in pu else "https://" + pu.lstrip("/")      # router.py
    if "://" not in u: u = "https://" + u.lstrip("/")                           # vidapay_sweep._norm_url

`file:///app/.env`, `http://169.254.169.254/latest/meta-data/iam/...` (cloud IMDS) and
`http://localhost:8000/...` all contain "://", so all three passed straight through to `page.goto()`.
The rendered result comes BACK to the caller through four surfaces (login screenshot, the
`auth_message` snapshot, the pull diagnostic, and the live-login JPEG screencast), so this was a
full-read SSRF that discloses SUPABASE_SERVICE_KEY (RLS bypass), FIELD_ENCRYPTION_KEY (all employee
PII), ANTHROPIC_API_KEY and cloud IAM credentials.

WHAT IT ENFORCES
----------------
1. **Scheme allow-list** — only `http` / `https`. `file:`, `gopher:`, `ftp:`, `data:`, `about:`,
   `chrome:`, `view-source:`, `javascript:` … are rejected, INCLUDING the schemeless-coercion trick
   (`"//169.254.169.254/x"`, `"localhost:8000"`, `"javascript:alert(1)"`).
2. **No credentials in the URL** (`https://user:pass@host/` — an exfil + confusion vector).
3. **No internal destinations** — the hostname is RESOLVED and every returned address must be a
   global unicast address. Loopback, link-local (169.254/16 incl. IMDS, fe80::/10), RFC1918,
   CGNAT 100.64/10, multicast, reserved, unspecified, and the IPv4-mapped / 6to4 / Teredo
   equivalents (`[::ffff:169.254.169.254]`) are all rejected. Resolving also defeats the
   decimal/octal (`http://2852039166/`) and `127.0.0.1.nip.io` encodings, which a literal-string
   deny-list never catches.
4. **Re-validation AFTER redirects** — a pre-flight-only check is defeated by a 302 to IMDS, so
   `install_ssrf_route_guard()` re-runs the check on EVERY request Chromium makes (each redirect hop
   is its own route event, and sub-frames are covered too) and aborts the unsafe ones.
5. **At USE time, not only at save time** — stored rows written before this landed are already
   poisoned-capable, so `_norm_url()` (every vidapay/b2bsoft entry point) and epay's `base_url`
   validate what they are about to navigate to, not what the settings form once accepted.

FAILURE MODE is explicit and NON-500: `UnsafeUrlError.message` is a plain-English sentence the
settings UI can show next to the field, and the router turns it into a 400.

CONFIG (RULE TWO — no hard-coded tenant behaviour, and never strand the operator)
    COMMCALC_URL_GUARD=0                   break-glass: drop the ADDRESS and CREDENTIAL checks (the
                                           parts that could conceivably block an exotic-but-legitimate
                                           portal). The SCHEME allow-list is NOT relaxed — no
                                           environment variable turns `file://` back on.
    COMMCALC_URL_GUARD_ALLOW_HOSTS=a,b     specific hostnames exempt from the ADDRESS check only
                                           (scheme + credential checks still apply). For a
                                           self-hosted / on-prem portal on a private address, or
                                           local dev. Preferred over the blanket break-glass.

DEPENDENCIES: stdlib only, no app imports, no DB — so it is trivially unit-testable and can be
promoted to `app/core/` verbatim (see the NEEDS CORE note in docs/handoffs/commission.md; the
storevisit / notify / helpdesk modules take tenant URLs too).
"""

import ipaddress
import os
import re
import socket
import time
from urllib.parse import urlsplit

__all__ = ["UnsafeUrlError", "normalize_url", "assert_safe_url", "is_safe_url",
           "assert_safe_proxy_url", "is_proxy_safe", "browser_url_blocked_reason", "install_ssrf_route_guard",
           "guard_enabled"]

# Schemes we will actually fetch/render.
SAFE_SCHEMES = ("http", "https")

# Schemes an operator may plausibly type for a PROXY (Playwright/requests both accept these).
PROXY_SCHEMES = ("http", "https", "socks4", "socks5", "socks5h")

# Known scheme tokens. Used ONLY to decide "did the operator type a scheme, or a host:port?" —
# `vidapaycrm.com:8443/x` must stay a schemeless host:port, while `file:` / `javascript:` must be
# recognised as schemes so they are REJECTED instead of being coerced into `https://file:...`.
_KNOWN_SCHEMES = {
    "http", "https", "file", "ftp", "ftps", "sftp", "gopher", "data", "javascript", "vbscript",
    "about", "chrome", "chrome-extension", "chrome-error", "view-source", "blob", "ws", "wss",
    "mailto", "tel", "sms", "jar", "netdoc", "dict", "ldap", "ldaps", "tftp", "telnet", "ssh",
    "resource", "filesystem", "intent", "content", "res", "smb", "nfs", "afp", "expect", "local",
}

_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")

# Explicit nets. `ipaddress.is_private` already covers most of these on modern CPython, but naming
# them makes the intent auditable and keeps the guard stable across interpreter versions.
_BLOCKED_V4 = [
    ipaddress.ip_network("0.0.0.0/8"),        # "this network" / 0 → 0.0.0.0
    ipaddress.ip_network("10.0.0.0/8"),       # RFC1918
    ipaddress.ip_network("100.64.0.0/10"),    # CGNAT
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("169.254.0.0/16"),   # link-local — cloud IMDS lives at 169.254.169.254
    ipaddress.ip_network("172.16.0.0/12"),    # RFC1918
    ipaddress.ip_network("192.0.0.0/24"),     # IETF protocol assignments
    ipaddress.ip_network("192.168.0.0/16"),   # RFC1918
    ipaddress.ip_network("198.18.0.0/15"),    # benchmarking
    ipaddress.ip_network("224.0.0.0/4"),      # multicast
    ipaddress.ip_network("240.0.0.0/4"),      # reserved
]
_BLOCKED_V6 = [
    ipaddress.ip_network("::/128"),           # unspecified
    ipaddress.ip_network("::1/128"),          # loopback
    ipaddress.ip_network("fc00::/7"),         # unique-local
    ipaddress.ip_network("fe80::/10"),        # link-local
    ipaddress.ip_network("ff00::/8"),         # multicast
]


class UnsafeUrlError(ValueError):
    """A tenant-supplied URL that must not be fetched/rendered.

    `message` is written for the person editing the settings form — the router turns it into a 400 so
    the field can show it inline. `reason` is the stable machine code (harness + logs)."""

    def __init__(self, reason, message, url=""):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.url = url


# ── configuration ────────────────────────────────────────────────────────────────────────────────

def guard_enabled():
    """Break-glass. Default ON; COMMCALC_URL_GUARD=0 restores the pre-2026-08-06 behaviour."""
    return os.environ.get("COMMCALC_URL_GUARD", "1").strip().lower() not in ("0", "false", "no", "off")


def _allow_hosts():
    raw = os.environ.get("COMMCALC_URL_GUARD_ALLOW_HOSTS", "") or ""
    return {h.strip().lower().rstrip(".") for h in raw.replace(";", ",").split(",") if h.strip()}


# ── normalization ────────────────────────────────────────────────────────────────────────────────

def _clean(raw):
    """Strip whitespace and the ASCII control characters browsers silently drop from URLs.

    Chromium removes TAB/CR/LF from a URL before parsing, so `"ja\\tvascript:alert(1)"` and
    `"http:/\\n/169.254.169.254"` are live bypasses of any naive string check. Strip them FIRST so we
    validate the same string the browser will act on."""
    s = "" if raw is None else str(raw)
    s = "".join(ch for ch in s if ord(ch) > 0x20 and ord(ch) != 0x7F)
    return s.strip()


def _split_scheme(s):
    """(scheme_or_None, rest). None ⇒ the operator typed a bare host (`vidapaycrm.com[:port][/path]`)."""
    m = _SCHEME_RE.match(s)
    if not m:
        return None, s
    scheme = m.group(1).lower()
    rest = s[m.end():]
    if scheme in _KNOWN_SCHEMES:
        return scheme, rest
    if rest.startswith("//"):
        # An explicit but unknown scheme (`weird://host`) — still a scheme, and not one we allow.
        return scheme, rest
    # `host.com:8443/x` — the "scheme" is really a hostname and the rest is a port. Schemeless.
    return None, s


def normalize_url(raw, default_scheme="https"):
    """Add the scheme an operator omitted, WITHOUT letting the coercion invent a dangerous one.

    Operators naturally type the bare host (`vidapaycrm.com`) and Playwright rejects it with
    "Cannot navigate to invalid URL" — that is the ONLY reason the original `"://" in u` coercion
    existed, and this preserves it exactly for legitimate input. Everything else raises."""
    s = _clean(raw)
    if not s:
        raise UnsafeUrlError("empty", "Enter a URL.", "")
    scheme, rest = _split_scheme(s)
    if scheme is None:
        return default_scheme + "://" + s.lstrip("/")
    if scheme not in SAFE_SCHEMES:
        raise UnsafeUrlError(
            "scheme",
            f"“{scheme}:” addresses are not allowed here — use a normal https:// web address. "
            f"(Only http:// and https:// portal addresses can be opened.)", s)
    if not rest.startswith("//"):
        # `http:/host` / `https:host` — malformed; rebuild it as the operator clearly meant.
        return scheme + "://" + rest.lstrip("/")
    return scheme + ":" + rest


# ── address checks ───────────────────────────────────────────────────────────────────────────────

def _unwrap(ip):
    """Follow IPv6 wrappers to the v4 address they really name.

    `[::ffff:169.254.169.254]` is IMDS wearing a hat; so are 6to4 (`2002::/16`) and Teredo. Without
    this, an IPv6-shaped literal walks past every v4 range check."""
    for attr in ("ipv4_mapped", "sixtofour"):
        try:
            inner = getattr(ip, attr, None)
        except Exception:
            inner = None
        if inner:
            return inner
    try:
        tered = getattr(ip, "teredo", None)
        if tered:
            return tered[1]
    except Exception:
        pass
    return ip


def _ip_block_reason(ip):
    """None if this address is a safe, globally routable destination; else the stable reason code."""
    ip = _unwrap(ip)
    nets = _BLOCKED_V4 if ip.version == 4 else _BLOCKED_V6
    for net in nets:
        if ip in net:
            return _reason_for_net(str(net))
    for flag, reason in (("is_loopback", "loopback"), ("is_link_local", "link_local"),
                         ("is_multicast", "multicast"), ("is_reserved", "reserved"),
                         ("is_unspecified", "unspecified"), ("is_private", "private")):
        try:
            if getattr(ip, flag, False):
                return reason
        except Exception:
            pass
    try:
        if not ip.is_global:
            return "not_global"
    except Exception:
        pass
    return None


def _reason_for_net(net):
    if net.startswith("127.") or net == "::1/128":
        return "loopback"
    if net.startswith("169.254") or net.startswith("fe80"):
        return "link_local"
    if net.startswith("224.") or net.startswith("ff00"):
        return "multicast"
    if net.startswith("100.64"):
        return "cgnat"
    if net.startswith("0.0.0.0") or net == "::/128":
        return "unspecified"
    if net.startswith("240.") or net.startswith("198.18") or net.startswith("192.0.0"):
        return "reserved"
    return "private"


_HUMAN = {
    "loopback": "the server itself (localhost)",
    "link_local": "a link-local address — this is the cloud metadata service, which holds the "
                  "server's own credentials",
    "private": "a private internal network address",
    "cgnat": "a carrier-internal (CGNAT) address",
    "multicast": "a multicast address",
    "reserved": "a reserved address",
    "unspecified": "an unspecified address",
    "not_global": "an address that is not on the public internet",
}


def _resolve(host, port=None):
    """Every address `host` resolves to. Empty list = could not resolve."""
    try:
        infos = socket.getaddrinfo(host, port or None, proto=socket.IPPROTO_TCP)
    except Exception:
        try:
            infos = socket.getaddrinfo(host, None)
        except Exception:
            return []
    out = []
    for info in infos:
        try:
            addr = info[4][0]
            out.append(ipaddress.ip_address(addr.split("%", 1)[0]))
        except Exception:
            continue
    return out


def _check_host(host, port, url, allow_hosts):
    """Raise UnsafeUrlError if `host` names an internal destination.

    UNRESOLVABLE ⇒ ALLOWED, deliberately. This guard exists to stop us REACHING an internal address;
    a name our own resolver cannot turn into an address cannot be turned into one by us either, and
    the fetch will simply fail. Rejecting on a DNS miss would instead break a legitimate portal
    whenever the container's egress/DNS is momentarily unhappy — a settings page that refuses to save
    a correct URL is a worse outcome than a fetch that fails honestly."""
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        raise UnsafeUrlError("no_host", "That address has no website name in it — "
                                        "enter something like https://portal.example.com.", url)
    if h in allow_hosts:
        return
    literal = None
    try:
        literal = ipaddress.ip_address(h.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        reason = _ip_block_reason(literal)
        if reason:
            raise UnsafeUrlError(reason, _msg(h, reason), url)
        return
    for ip in _resolve(h, port):
        reason = _ip_block_reason(ip)
        if reason:
            raise UnsafeUrlError(reason, _msg(f"{h} ({ip})", reason), url)


def _msg(what, reason):
    return (f"“{what}” points at {_HUMAN.get(reason, 'an internal address')}. Portal addresses must "
            f"be public websites — internal addresses are blocked because opening one would expose "
            f"this server's own secrets.")


# ── the public API ───────────────────────────────────────────────────────────────────────────────

def assert_safe_url(raw, what="URL", default_scheme="https"):
    """Validate a tenant-supplied URL and return the NORMALIZED, safe form.

    Raises UnsafeUrlError (never a 500) — callers turn it into a 400 for a settings save, or into a
    named failure for a sweep. Call it at USE time, not only at save time: rows stored before this
    landed were never validated."""
    normalized = normalize_url(raw, default_scheme=default_scheme)
    if not guard_enabled():
        return normalized
    parts = urlsplit(normalized)
    if (parts.scheme or "").lower() not in SAFE_SCHEMES:
        raise UnsafeUrlError("scheme", f"“{parts.scheme}:” addresses are not allowed for a {what} — "
                                       f"use an https:// web address.", normalized)
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        raise UnsafeUrlError(
            "credentials",
            "Remove the “user:password@” part from the address — enter the sign-in details in the "
            "User ID and Password fields instead.", normalized)
    try:
        port = parts.port
    except ValueError:
        raise UnsafeUrlError("bad_port", "That address has an invalid port number.", normalized)
    host = parts.hostname or ""
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        try:
            host = host.encode("idna").decode("ascii")
        except Exception:
            raise UnsafeUrlError("bad_host", "That website name isn't valid.", normalized)
    _check_host(host, port, normalized, _allow_hosts())
    return normalized


def is_safe_url(raw, **kw):
    try:
        assert_safe_url(raw, **kw)
        return True
    except UnsafeUrlError:
        return False


def is_proxy_safe(raw):
    """Boolean form of `assert_safe_proxy_url`, for the diagnostic paths that must never raise."""
    try:
        assert_safe_proxy_url(raw)
        return True
    except UnsafeUrlError:
        return False


def assert_safe_proxy_url(raw, what="proxy"):
    """Same guarantees for a PROXY endpoint, which is a fetch target too.

    A proxy pointing at `http://127.0.0.1:6379` turns the proxy-test button into an internal
    port-scanner / banner-grabber (the response text is echoed back to the caller). Socks schemes are
    allowed here because Playwright and requests both accept them for a proxy; they are NOT allowed
    as a portal address."""
    s = _clean(raw)
    if not s:
        raise UnsafeUrlError("empty", "Enter a proxy address (http://user:pass@host:port).", "")
    scheme, rest = _split_scheme(s)
    if scheme is None:
        s, scheme = "http://" + s.lstrip("/"), "http"
    elif scheme not in PROXY_SCHEMES:
        raise UnsafeUrlError("scheme", f"“{scheme}:” is not a proxy address — use http://, https:// "
                                       f"or socks5://.", s)
    if not guard_enabled():
        return s
    parts = urlsplit(s if "://" in s else "http://" + s)
    try:
        port = parts.port
    except ValueError:
        raise UnsafeUrlError("bad_port", "That proxy address has an invalid port number.", s)
    # Credentials ARE legitimate in a proxy URL (that is how proxy auth is expressed), so unlike a
    # portal URL we keep them — only the DESTINATION is checked.
    _check_host(parts.hostname or "", port, s, _allow_hosts())
    return s


# ── post-redirect enforcement (the classic pre-flight bypass) ────────────────────────────────────
# A pre-flight check on the CONFIGURED url is defeated by an attacker-controlled host that answers
# 302 → http://169.254.169.254/…. Chromium reports every redirect hop (and every sub-frame) as its
# own route event, so re-running the check there is what actually closes the hole.

_BROWSER_CACHE = {}          # url -> (reason_or_None, expires_at)
_BROWSER_TTL_S = 30.0
_BROWSER_CACHE_MAX = 512


def browser_url_blocked_reason(url):
    """Reason code if Chromium must NOT be allowed to load `url`, else None. Never raises.

    Deliberately narrower than `assert_safe_url`: it judges only http/https/file/ftp/gopher-class
    requests. Chromium's own internal schemes (`about:`, `blob:`, `data:`, `chrome-error:`) are
    passed through untouched — aborting those breaks the browser, and none of them can read a
    server-side secret. A host that fails to resolve is passed through for the same reason as in
    `_check_host`."""
    try:
        u = _clean(url)
        if not u:
            return None
        now = time.time()
        hit = _BROWSER_CACHE.get(u)
        if hit and hit[1] > now:
            return hit[0]
        reason = _browser_reason(u)
        if len(_BROWSER_CACHE) >= _BROWSER_CACHE_MAX:
            _BROWSER_CACHE.clear()
        _BROWSER_CACHE[u] = (reason, now + _BROWSER_TTL_S)
        return reason
    except Exception:
        return None      # a guard that crashes must not break a portal login


def _browser_reason(u):
    if not guard_enabled():
        return None
    scheme, rest = _split_scheme(u)
    if scheme is None:
        return None                       # relative/opaque — the browser resolves it against a base
    if scheme in ("about", "blob", "data", "chrome", "chrome-extension", "chrome-error",
                  "filesystem", "ws", "wss", "resource"):
        return None
    if scheme not in SAFE_SCHEMES:
        return "scheme"                   # file:, ftp:, gopher:, view-source:, javascript: …
    parts = urlsplit(u)
    if parts.username or parts.password:
        return "credentials"
    try:
        port = parts.port
    except ValueError:
        return "bad_port"
    try:
        _check_host(parts.hostname or "", port, u, _allow_hosts())
    except UnsafeUrlError as e:
        return e.reason
    return None


def install_ssrf_route_guard(ctx, on_block=None):
    """Register the post-redirect guard on a Playwright BrowserContext. Never raises.

    ORDERING MATTERS: Playwright runs matching routes in the REVERSE order of registration, so this
    must be installed AFTER the https-upgrade route in `vidapay_sweep._new_context` — then this one
    is consulted first and, for a safe URL, `route.fallback()` hands off to the https-upgrade route
    exactly as before. Installing it first would let an unsafe redirect be `continue_()`d by the
    upgrade route before this ever saw it."""
    def _handler(route):
        url = ""
        try:
            url = route.request.url
        except Exception:
            url = ""
        reason = browser_url_blocked_reason(url)
        if reason:
            if on_block:
                try:
                    on_block(url, reason)
                except Exception:
                    pass
            try:
                route.abort("blockedbyclient")
                return
            except Exception:
                pass
        try:
            route.fallback()
            return
        except Exception:
            pass
        try:
            route.continue_()
        except Exception:
            pass

    try:
        ctx.route("**/*", _handler)
    except Exception:
        pass
    return _handler
