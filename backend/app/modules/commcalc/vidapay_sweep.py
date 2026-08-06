"""VidaPay / Total Access "Master Agent" portal login + report sweep (Total Wireless side).

Total Wireless dealers get their MI/ATU-equivalent commission data from the VidaPay CRM portal
(https://www.vidapaycrm.com/Main%20Panel.aspx), NOT Boost's ePay. Two things make this portal
different from every other sweep in the app and shape the whole design here:

  1. THREE-FIELD LOGIN. Auth needs Account ID + User ID + Password (ePay/VIP/DLAR use two).
  2. INTERACTIVE 2FA. After the password step the portal challenges with a code the operator
     receives out-of-band (email/SMS). Login therefore can't complete in one request — a human
     fetches the code and hands it back. Login is a STATE MACHINE across two calls:
         begin_login(...)      -> reaches the 2FA challenge, returns the half-auth session
         complete_2fa(code)    -> submits the code, returns the durable authenticated session
     The browser session is carried between the two calls (and reused by later scheduled pulls)
     as Playwright **storage_state** (cookies/localStorage) persisted in commcalc.data_source —
     no long-lived browser process, so it survives Railway restarts and multiple workers.

  3. ANTI-BOT. The real login lives on a separate ASP.NET Identity server, id.vidapaycrm.com
     /Account/Login (Main%20Panel.aspx redirects there), fronted by Cloudflare + an automation
     check that serves a "Something doesn't look right..." interstitial (no form fields) to
     browsers it flags. So this drives a headless Chromium presenting a realistic desktop-Chrome
     fingerprint (viewport/locale/timezone/Accept-Language + navigator.webdriver masked) and the
     form finders search inside IFRAMES. A hard datacenter-IP block still needs an allow-listed /
     residential egress — the same WAF caveat as ePay. The backend image bundles Chromium.

CALIBRATION: the exact login/2FA/report DOM has NOT been driven end-to-end (Cloudflare-gated,
needs real credentials). The field finders are HEURISTIC (match by type + nearby
label/placeholder/name/id text, across frames) and every failure returns a DIAGNOSTIC snapshot
(url/title/headings/inputs). The operator's first real login is the calibration pass. Nothing is
hard-coded — credentials always come from the data_source row (UI config).
"""
from datetime import datetime, timezone, timedelta

# Portal rate-limit / temporary-block detection + the shared cooldown state (migration 244). A pure
# leaf module (it imports nothing from commcalc), so a top-level import here cannot create a cycle.
#
# The FALLBACK is load-bearing, not defensive noise: this driver is deliberately importable as a
# STANDALONE FILE (several proof harnesses load it with importlib.spec_from_file_location so they can
# exercise the Playwright surface without booting the app), and in that mode the `app` package does not
# exist on sys.path. A bare absolute import here turns every one of those into a ModuleNotFoundError.
try:
    from app.modules.commcalc import portal_backoff as _pb
    from app.modules.commcalc.portal_backoff import PortalRateLimited
except ImportError:                                     # loaded by path, not as app.modules.commcalc.*
    import importlib.util as _ilu
    import os as _osmod
    _pb_spec = _ilu.spec_from_file_location(
        "commcalc_portal_backoff",
        _osmod.path.join(_osmod.path.dirname(_osmod.path.abspath(__file__)), "portal_backoff.py"))
    _pb = _ilu.module_from_spec(_pb_spec)
    _pb_spec.loader.exec_module(_pb)
    PortalRateLimited = _pb.PortalRateLimited
try:
    from app.modules.commcalc import url_guard as _url_guard      # SSRF guard (finding C4)
except ImportError:                                     # loaded by path, not as app.modules.commcalc.*
    import importlib.util as _ilu2
    import os as _osmod2
    _ug_spec = _ilu2.spec_from_file_location(
        "commcalc_url_guard",
        _osmod2.path.join(_osmod2.path.dirname(_osmod2.path.abspath(__file__)), "url_guard.py"))
    _url_guard = _ilu2.module_from_spec(_ug_spec)
    _ug_spec.loader.exec_module(_url_guard)

DEFAULT_URL = "https://www.vidapaycrm.com/Main%20Panel.aspx"
# The observed login host the portal redirects to. Used as a fallback if the configured URL lands
# on a bot-wall — going straight to the login endpoint sometimes renders the form when the deep
# link does not.
LOGIN_URL = "https://id.vidapaycrm.com/Account/Login"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

SESSION_TTL_HOURS = 8
PENDING_TTL_MINUTES = 20

# Realistic desktop-Chrome context + a light anti-automation init script. VidaPay's id server
# serves a "Something doesn't look right..." interstitial (no form fields) to flagged browsers, so
# we present a normal viewport/locale/timezone, send a real Accept-Language header, and mask the
# automation signals detectors check first.
# The signals Cloudflare's managed-challenge JS fingerprints FIRST. Kept internally consistent — an
# inconsistent spoof (e.g. a numeric plugins array, or a WebGL renderer that contradicts the UA) is
# itself a bot tell — so these mirror a real desktop-Chrome-on-Windows profile matching UA above.
_STEALTH_JS = (
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    "window.chrome=window.chrome||{};window.chrome.runtime=window.chrome.runtime||{};"
    "Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});"
    "Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>8});"
    "Object.defineProperty(navigator,'deviceMemory',{get:()=>8});"
    "Object.defineProperty(navigator,'platform',{get:()=>'Win32'});"
    # WebGL vendor/renderer → a common Intel desktop GPU (headless reports 'Google SwiftShader', a tell).
    "try{const gp=WebGLRenderingContext.prototype.getParameter;"
    "WebGLRenderingContext.prototype.getParameter=function(p){"
    "if(p===37445)return 'Intel Inc.';if(p===37446)return 'Intel Iris OpenGL Engine';"
    "return gp.call(this,p);};}catch(e){}"
    # Notification permission: headless returns 'denied' while Notification.permission='default' — a mismatch bots trip on.
    "try{const q=navigator.permissions.query.bind(navigator.permissions);"
    "navigator.permissions.query=(p)=>p&&p.name==='notifications'"
    "?Promise.resolve({state:Notification.permission}):q(p);}catch(e){}"
)
_LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process"]


def _launch(p):
    """Launch the browser for a portal login. Prefers REAL Google Chrome (channel='chrome') — Cloudflare's
    bot-management passes genuine Chrome far more often than bundled Chromium, whose build fingerprints as
    automation — and falls back to bundled Chromium when Chrome isn't in the image (so this is zero-regression
    if `playwright install chrome` hasn't run). Same args either way."""
    try:
        return p.chromium.launch(headless=True, channel="chrome", args=_LAUNCH_ARGS)
    except Exception:
        return p.chromium.launch(headless=True, args=_LAUNCH_ARGS)


_BOT_PHRASES = ("something doesn't look right", "doesn't look right", "unusual activity",
                "verify you are human", "are you a robot", "request unsuccessful",
                "access to this page has been denied", "attention required")


class VidaPayLoginError(Exception):
    """Login could not start — bad account/user/password, an anti-bot block, or Chromium missing.
    Surfaced to the admin UI; never echoes the password."""


class UnsafePortalUrlError(VidaPayLoginError):
    """The CONFIGURED portal/proxy URL is refused by the SSRF guard (finding C4).

    A SUBCLASS of VidaPayLoginError so every existing `except VidaPayLoginError` keeps working
    unchanged, but distinguishable — this is a CONFIGURATION error the operator must fix on the
    settings page, NOT the portal refusing us. Callers use that distinction to avoid arming the
    portal-block cooldown (mig 244) for what is our own bad config."""


class VidaPayAuthError(Exception):
    """The stored session is missing/expired — the operator must (re-)log in + pass 2FA."""


class VidaPayPortalError(Exception):
    """Logged in fine, but a later step (report navigation/download/parse) failed."""


def _norm_url(u, fallback):
    """Playwright rejects a scheme-less URL ("vidapaycrm.com" -> "Cannot navigate to invalid URL").
    Operators naturally type the bare host, so add the scheme they omitted.

    SSRF GUARD (finding C4, 2026-08-06). The old body was `if "://" not in u: u = "https://" + u`,
    which is not validation at all: `file:///app/.env`, `http://169.254.169.254/…` (cloud IMDS) and
    `http://localhost:8000/…` all contain "://" and sailed through to `page.goto()` in a Chromium
    launched `--no-sandbox`, whose rendered screen comes straight back to the caller (login
    screenshot / auth_message / pull diagnostic / live screencast). This is EVERY portal entry
    point's single choke point — begin_login, begin_login_b2bsoft, complete_2fa,
    complete_2fa_b2bsoft, run_vidapay_sweep, run_b2bsoft_sweep, plus live_login's base — so the
    check lives here and runs at USE time, not just when the settings form saved the row (rows
    stored before this landed were never validated).

    Raises UnsafePortalUrlError (a VidaPayLoginError subclass) so the operator sees the named,
    plain-English reason on the source row instead of a Playwright trace or a 500."""
    u = (u or "").strip()
    if not u:
        return fallback
    try:
        return _url_guard.assert_safe_url(u, what="portal address")
    except _url_guard.UnsafeUrlError as e:
        raise UnsafePortalUrlError(e.message)


def _egress_ip(proxy_url=None, timeout=12):
    """The public IP a request actually leaves from (through `proxy_url`, or direct). None on failure.

    SSRF guard (C4): this is the one remaining place a STORED proxy_url is used WITHOUT going through
    _proxy_arg, so an already-poisoned row could still make a request through an internal endpoint
    here. A refused proxy degrades to the DIRECT probe (this function is a diagnostic hint, never a
    credential path — the login itself is already refused by _proxy_arg), and never raises."""
    try:
        if proxy_url and not _url_guard.is_proxy_safe(proxy_url):
            proxy_url = None
    except Exception:
        proxy_url = None
    try:
        import requests
        px = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        r = requests.get("https://ipinfo.io/json", proxies=px, timeout=timeout,
                         headers={"User-Agent": "MetricsPro-egress-check/1.0"})
        return (r.json() or {}).get("ip") if r.status_code == 200 else None
    except Exception:
        return None


def egress_hint(proxy_url):
    """Explains WHICH egress a walled attempt actually used. A configured proxy is NOT proof that it took
    effect — a wrong scheme or missing credentials silently leaves traffic on the datacenter IP — so this
    RESOLVES the real egress IP and compares it against the server's own instead of assuming."""
    arg = _proxy_arg(proxy_url)
    fmt = ("Decodo ISP wants http://USER:PASS@isp.decodo.com:10001 — the http scheme, WITH credentials "
           "(an https:// scheme or a credential-less URL both fail).")
    if not arg:
        return (" No egress proxy is set on this source, so the login went out from the server's datacenter "
                "IP — exactly what the WAF blocks. Set a residential/ISP proxy on the source, click Test "
                "proxy (expect green: routed + US), then Log in again. " + fmt)

    server = arg.get("server")
    via, direct = _egress_ip(proxy_url), _egress_ip(None)
    if not via:
        return (" The configured proxy (%s) did NOT answer, so this attempt egressed from the server's own "
                "datacenter IP — which is what the WAF blocked. Fix the proxy URL first: %s Then click "
                "Test proxy until it reports routed + US." % (server, fmt))
    if direct and via == direct:
        return (" The configured proxy (%s) is NOT taking effect — the egress IP (%s) is still the server's "
                "own datacenter IP. Fix the proxy URL first: %s" % (server, via, fmt))
    return (" Routed through %s — the real egress IP was %s, and the WAF is blocking THAT IP too. The proxy "
            "IS working, so the IP itself is burned or untrusted: try a different dedicated ISP IP. If a clean "
            "US ISP IP is still challenged, the block is on the headless-browser fingerprint, not the IP."
            % (server, via))


def _proxy_arg(proxy_url):
    """Parse a proxy URL (e.g. http://user:pass@host:port or socks5://host:port) into Playwright's
    proxy dict {server, username?, password?}. Datacenter IPs get walled by VidaPay's bot-management,
    so an operator can route the login + pull through a residential/allow-listed proxy."""
    u = (proxy_url or "").strip()
    if not u:
        return None
    # SSRF guard (C4): a "proxy" of http://127.0.0.1:6379 or http://169.254.169.254:80 makes every
    # portal request an internal probe whose result is rendered back to the operator. Same validator,
    # socks schemes allowed (Playwright accepts them for a proxy; they are NOT allowed as a portal
    # address). A rejected proxy is a NAMED login failure, never a silent direct-egress fallback —
    # silently ignoring it would send credentials out of the wrong IP.
    try:
        u = _url_guard.assert_safe_proxy_url(u)
    except _url_guard.UnsafeUrlError as e:
        raise UnsafePortalUrlError(e.message)
    try:
        from urllib.parse import urlparse
        if "://" not in u:
            u = "http://" + u
        p = urlparse(u)
        host = p.hostname or ""
        if not host:
            return None
        server = f"{p.scheme}://{host}" + (f":{p.port}" if p.port else "")
        arg = {"server": server}
        if p.username:
            arg["username"] = p.username
        if p.password:
            arg["password"] = p.password
        return arg
    except Exception:
        return None


def _https_upgrade_url(url):
    """Return the https:// form of a plain-http URL, or None when it must NOT be upgraded (already
    https / a non-http scheme, or a localhost/loopback host — local test servers and Playwright's own
    internals must stay on http). Only the SCHEME is swapped: the host, port, userinfo, path, query
    string and every percent-encoded character are preserved byte-for-byte (the VidaPay bounce URL
    carries a percent-encoded ?returnto=http%3a%2f%2f… that must survive intact). Backs the
    https-upgrade route in _new_context — see the squid/Decodo plain-http failure documented there."""
    u = url or ""
    if not u.lower().startswith("http://"):
        return None
    rest = u[len("http://"):]                    # everything after the scheme, kept verbatim
    authority = rest
    for sep in ("/", "?", "#"):
        i = authority.find(sep)
        if i != -1:
            authority = authority[:i]
    hostport = authority.split("@")[-1]          # strip any user:pass@ before reading the host
    if hostport.startswith("["):                 # IPv6 literal, e.g. [::1]:8080
        host = hostport[1:hostport.find("]")] if "]" in hostport else hostport[1:]
    else:
        host = hostport.split(":")[0]
    host = host.lower()
    # GUARD (incident 4): a hostless / path-only value ("http:///path" or a malformed request URL) must
    # NEVER become a hostless Location — squid rejects it ("Missing hostname" / "Invalid URL"). Refuse it.
    if not host:
        return None
    if (host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
            or host.startswith("127.") or host.endswith(".localhost")):
        return None
    # Encode a RAW space (illegal in a request-URI — "Main Panel.aspx" → "Main%20Panel.aspx"; squid also
    # rejects the raw space). Only the literal space is touched, so an already-%xx-encoded returnto
    # (…returnto=http%3a%2f%2f…) survives byte-for-byte and is NOT double-escaped.
    rest = rest.replace(" ", "%20")
    return "https://" + rest


def _new_context(browser, storage_state=None, proxy=None):
    """A desktop-Chrome context with the anti-automation shims applied before any page script runs.
    `proxy` is a Playwright proxy dict (see _proxy_arg) routing the session through a given egress.
    Also RE-INJECTS sessionStorage stashed under storage_state['_sessionStorage'] (Playwright's
    storage_state doesn't persist sessionStorage — many OIDC SPAs keep their token there, so without
    this the session 'expires' the moment it's restored in a fresh context)."""
    ss_stash = None
    if isinstance(storage_state, dict):
        ss_stash = storage_state.get("_sessionStorage")
        # Strip our private keys — Playwright's storage_state parser only knows cookies/origins, and
        # `_2fa_url` (the captured code-entry page, see complete_2fa) is navigation state, not browser state.
        storage_state = {k: v for k, v in storage_state.items()
                         if k not in ("_sessionStorage", "_2fa_url")}
    kw = dict(user_agent=UA, accept_downloads=True, locale="en-US",
              timezone_id="America/New_York", viewport={"width": 1366, "height": 900},
              extra_http_headers={"Accept-Language": "en-US,en;q=0.9"})
    if storage_state:
        kw["storage_state"] = storage_state
    if proxy:
        kw["proxy"] = proxy
    ctx = browser.new_context(**kw)
    try:
        ctx.add_init_script(_STEALTH_JS)
    except Exception:
        pass
    if ss_stash and ss_stash.get("items"):
        try:
            import json as _json
            items_js = _json.dumps(ss_stash.get("items") or {})
            ctx.add_init_script(
                "(() => { try { const it = " + items_js +
                "; for (const k in it) sessionStorage.setItem(k, it[k]); } catch(e){} })();")
        except Exception:
            pass
    # HTTPS-UPGRADE ROUTE — applies to EVERY flow that builds a context (begin_login, complete_2fa,
    # the live session, run_vidapay_sweep, b2bsoft). VidaPay/T-CETRA is a legacy ASP.NET app behind
    # Cloudflare that believes its own scheme is plain http, so after login it bounces the browser to
    # http:// absolute URLs (e.g. /Default.aspx?returnto=http%3a%2f%2fwww.vidapaycrm.com%2fMain+Panel.aspx).
    # When the session egresses through the Decodo residential proxy (an HTTPS CONNECT tunnel), a
    # plain-http absolute-form request is parsed by Decodo's squid and REJECTED — the browser renders
    # squid's own "Invalid URL … (squid)" error page and every http hop dies AT THE PROXY. The portal
    # serves everything over https, so we transparently rewrite any http:// request to https:// with a
    # 307 (preserves method + body → ASP.NET postbacks survive) and let the browser re-issue it over the
    # CONNECT tunnel the proxy handles. Unconditional (not proxy-only): these portals are https-only and
    # it also hardens the no-proxy path. Loopback is excluded (local test servers must stay on http).
    try:
        def _https_upgrade_route(route):
            try:
                https = _https_upgrade_url(route.request.url)
            except Exception:
                https = None
            # Only emit a 307 when we have a GUARANTEED-ABSOLUTE https Location (scheme + host). A hostless
            # or relative Location would make the browser/proxy build a malformed request ("Missing
            # hostname", incident 4) — in that case pass the original request through untouched instead.
            if https and not https.lower().startswith("https://"):
                https = None
            try:
                if https:
                    route.fulfill(status=307, headers={"Location": https})
                else:
                    route.continue_()
            except Exception:
                try:
                    route.continue_()
                except Exception:
                    pass
        ctx.route(lambda u: bool(u) and u.lower().startswith("http://"), _https_upgrade_route)
    except Exception:
        pass
    # SSRF ROUTE GUARD (finding C4, 2026-08-06) — the POST-REDIRECT half of the fix. Validating the
    # configured portal_url up front is defeated by an attacker-controlled host that answers
    # `302 Location: http://169.254.169.254/latest/meta-data/iam/security-credentials/` — the classic
    # pre-flight bypass. Chromium reports every redirect hop, sub-frame and sub-resource as its OWN
    # route event, so re-running the check here is what actually closes it. Registered AFTER the
    # https-upgrade route ON PURPOSE: Playwright runs matching routes in REVERSE registration order,
    # so this is consulted first and hands a safe URL on with route.fallback(), leaving the
    # https-upgrade behaviour byte-identical.
    try:
        _url_guard.install_ssrf_route_guard(ctx)
    except Exception:
        pass
    return ctx


# ── diagnostics ──────────────────────────────────────────────────────────────────────────────
def _snapshot(page):
    """A compact, credential-free description of the current page (across frames) — returned on
    every ambiguous outcome so the first live login tells us which selectors to pin."""
    controls = []
    try:
        for fr in _frames(page):
            try:
                controls += fr.evaluate(
                    """() => Array.from(document.querySelectorAll('input,button,select')).slice(0,40).map(e => ({
                         tag: e.tagName.toLowerCase(), type: (e.type||'').toLowerCase(),
                         name: e.name||'', id: e.id||'', ph: e.placeholder||'',
                         val: (e.tagName.toLowerCase()==='button'||e.type==='submit'||e.type==='button') ? (e.value||e.innerText||'').slice(0,30) : '',
                         vis: !!(e.offsetParent) }))""")
            except Exception:
                continue
    except Exception:
        pass
    heads = []
    try:
        for fr in _frames(page):
            try:
                heads += fr.evaluate(
                    """() => Array.from(document.querySelectorAll('h1,h2,h3,legend,label')).slice(0,20)
                             .map(e => (e.innerText||'').trim()).filter(Boolean)""")
            except Exception:
                continue
    except Exception:
        pass
    return {"url": (page.url or "")[:200], "title": (page.title() or "")[:120],
            "frames": len(_frames(page)), "headings": heads[:12],
            "controls": [c for c in controls if c.get("vis")][:24] or controls[:24]}


def capture_session_state(page, ctx):
    """The FULL authenticated state to persist/restore = Playwright storage_state (cookies + localStorage)
    PLUS a sessionStorage stash under st['_sessionStorage']. VidaPay/T-CETRA is an OIDC SPA that keeps its
    auth token in sessionStorage, which ctx.storage_state() DROPS — so a session saved without this
    'expires' the instant it's restored in a fresh browser, and the report Pull bounces to the login
    screen (which then looks like a re-auth / new code). _new_context re-injects '_sessionStorage' on
    restore. Use this everywhere an authenticated (or pending) session is captured."""
    try:
        st = ctx.storage_state()
    except Exception:
        return None
    try:
        ss = page.evaluate(
            "() => { const o={}; for (let i=0;i<sessionStorage.length;i++){const k=sessionStorage.key(i);"
            " o[k]=sessionStorage.getItem(k);} return o; }") or {}
        if ss:
            st["_sessionStorage"] = {"origin": (page.url or ""), "items": ss}
    except Exception:
        pass
    return st


def _shot_b64(page):
    """Small viewport JPEG of the current page, base64 — persisted on the data_source row so the
    operator can SEE exactly what the headless browser saw (the 2FA challenge, a bot-wall, a portal
    error) instead of reverse-engineering it from text diagnostics. Password inputs render as dots,
    so no credential value can appear in the capture."""
    try:
        import base64
        return base64.b64encode(page.screenshot(type="jpeg", quality=45)).decode("ascii")
    except Exception:
        return None


def _frames(page):
    """All frames incl. the main one — the login/2FA form may be inside an iframe."""
    try:
        fr = list(page.frames)
        return fr or [page]
    except Exception:
        return [page]


def _page_text(page):
    try:
        return ((page.title() or "") + " " + (page.content() or "")).lower()
    except Exception:
        return ""


def _main_frame_text(page):
    """VISIBLE innerText of the TOP frame only (title + document.body.innerText). Excludes <script>/<style>,
    display:none elements, HTML comments, and ALL child frames — i.e. what the operator actually SEES on the
    main page. _classify's auth-vs-2FA word decision prefers THIS over the raw HTML soup (_page_text uses
    page.content(), which serializes hidden + script + comment text), so a STALE or HIDDEN 2FA phrase — or a
    stray token like 'otp' in a script/data-attr — can't pin the state at 'twofa' when the real Main Panel
    is on screen (incident 3, 2026-07-17). Never raises → '' on failure."""
    try:
        t = page.evaluate(
            "() => ((document.title || '') + ' ' + ((document.body && document.body.innerText) || ''))")
        return (t or "").lower()
    except Exception:
        return ""


def _all_frames_text(page):
    """VISIBLE innerText concatenated across EVERY frame (main + children) — the fallback signal when the
    top frame's text carries no auth/2FA marker (e.g. an app that renders its Main Panel inside a child
    frame). Still VISIBLE text only (per-frame document.body.innerText), so hidden/script 2FA noise stays
    excluded. Never raises → '' on failure."""
    parts = []
    for fr in _frames(page):
        try:
            t = fr.evaluate("() => ((document.body && document.body.innerText) || '')")
            if t:
                parts.append(t)
        except Exception:
            continue
    try:
        parts.append(page.title() or "")
    except Exception:
        pass
    return " ".join(parts).lower()


def _looks_like_cloudflare(page):
    body = _page_text(page)
    return ("cf-chl" in body or "challenge-platform" in body or
            ("cloudflare" in body and "ray id" in body) or "just a moment" in body)


def _looks_like_bot_wall(page):
    body = _page_text(page)
    return any(p in body for p in _BOT_PHRASES)


# ── RATE LIMIT / TEMPORARY BLOCK (owner report 2026-07-27: "you have too many requests, and have been
# temporarily blocked, try again later"). Until now NOTHING in this driver recognised that page: the
# login reported "could not find the password field" and the pull reported "report not listed", so the
# next scheduled poll / auto-pull / operator click went straight back at the portal and made it worse.
# `markers` comes from commcalc.portal_block_marker wherever the caller has a DB client (RULE TWO); the
# seeded portal_backoff.DEFAULT_MARKERS are the fallback deep in the driver, where there is none.
def _rate_limit_hit(page, markers=None):
    """The block dict (reason/marker/retry_after_s) if THIS page is a rate-limit/temporary-block page,
    else None. Reads the HTML soup AND the visible top-frame text (same belt-and-braces as
    _looks_like_proxy_error — content() can be flaky on a block commit). Never raises."""
    for reader in (_page_text, _main_frame_text):
        try:
            hit = _pb.detect_block(reader(page), markers=markers)
        except Exception:
            hit = None
        if hit:
            return hit
    return None


def _response_block(resp, markers=None):
    """The block dict if a Playwright navigation RESPONSE is a throttle (HTTP 429, or 503 carrying a
    Retry-After), else None. This is the only place the wire-level signal exists — the body of a 429 is
    often empty, so page text alone would miss it. Never raises."""
    if resp is None:
        return None
    try:
        status = resp.status
    except Exception:
        return None
    headers = None
    try:
        headers = resp.all_headers()
    except Exception:
        try:
            headers = resp.headers
        except Exception:
            headers = None
    try:
        return _pb.detect_block(status=status, headers=headers, markers=markers)
    except Exception:
        return None


def _raise_if_rate_limited(page, resp=None, markers=None, where="The portal"):
    """Raise PortalRateLimited when the portal is throttling us — checked at BOTH the wire level (the
    navigation response) and the page level (the block page's own words). The CALLER records the
    cooldown; nothing here writes. A no-op when the portal is fine."""
    hit = _response_block(resp, markers=markers) or _rate_limit_hit(page, markers=markers)
    if not hit:
        return None
    raise PortalRateLimited(
        ("%s is rate-limiting us: %s Waiting is the only fix — retrying now extends the block."
         % (where, hit.get("reason") or "")).strip()[:400],
        retry_after_s=hit.get("retry_after_s"), marker=hit.get("marker"), status=hit.get("status"))


# The egress PROXY's OWN rejection page. When a plain-http absolute-form request reaches the Decodo
# residential proxy (an HTTPS CONNECT tunnel), its squid refuses to tunnel it and serves its own page —
# "ERROR: The requested URL could not be retrieved … Your cache administrator is … Generated … by
# localhost (squid)". The https-upgrade route in _new_context stops VidaPay's http-redirect from
# producing this, but if a proxy still mangles a request we must NAME the proxy as the failure point —
# NOT misread it as an expired session and blunder into report pulls with confusing calibration errors.
_PROXY_ERR_PHRASES = ("requested url could not be retrieved", "your cache administrator", "(squid)",
                      "invalid url", "missing or incorrect access protocol", "missing hostname",
                      "illegal double-escape in the url-path")


def _looks_like_proxy_error(page):
    # Check the HTML soup (title + page.content()) AND the VISIBLE top-frame text — page.content() can be
    # flaky on a proxy-error commit (the request failed at the network layer), so the visible squid body is
    # a second, more reliable read. Either hit → it's the egress proxy's own page.
    try:
        if any(pph in _page_text(page) for pph in _PROXY_ERR_PHRASES):
            return True
    except Exception:
        pass
    try:
        if any(pph in _main_frame_text(page) for pph in _PROXY_ERR_PHRASES):
            return True
    except Exception:
        pass
    return False


# Squid's error page states the URL it FAILED to retrieve, e.g.
#   "... while trying to retrieve the URL: <a href="...">/Default.aspx?returnto=http%3a%2f%2f…</a>"
# Extracting it (from the RAW html, case preserved) tells the NEXT incident the EXACT (possibly malformed,
# host-less, double-escaped) form squid saw — without needing a screenshot. Best-effort.
import re as _re
_SQUID_URL_RE = _re.compile(r"retriev(?:e|ing)\s+the\s+url:?\s*(?:<a[^>]*>)?\s*([^<>\s\"']{3,400})", _re.I)


def _squid_reported_url(page):
    """The URL squid says it could not retrieve (raw, case-preserved), or None. Never raises."""
    try:
        raw = page.content() or ""
    except Exception:
        raw = ""
    if not raw:
        return None
    try:
        m = _SQUID_URL_RE.search(raw)
    except Exception:
        m = None
    if not m:
        return None
    u = (m.group(1) or "").strip().replace("&amp;", "&")
    return u[:400] or None


def _proxy_error_message(failing_url, proxy_url, reported_url=None):
    """A clear, actionable error for the egress-proxy's own rejection page — names the failing URL and
    states the request died AT THE EGRESS PROXY (NOT an expired session, NOT the portal). Mirrors the
    per-egress WAF error style (egress_hint / commit f60f1e1). `reported_url` is squid's OWN reported URL
    (see _squid_reported_url) — appended so a NEXT incident carries the exact malformed form."""
    arg = _proxy_arg(proxy_url)
    server = arg.get("server") if arg else None
    via = (" (%s)" % server) if server else ""
    rep = ""
    if reported_url:
        rep = " Squid reported the URL as: %s." % str(reported_url)[:200]
    return ("The request died AT THE EGRESS PROXY%s, which returned its OWN error page (squid: \"the "
            "requested URL could not be retrieved\") for %s.%s This is NOT a session/2FA problem and the "
            "portal is fine — the proxy rejected the request (typically a plain-http absolute-form URL "
            "its squid won't tunnel). The portal's http→https redirects are now auto-upgraded, so if you "
            "still see this, verify the egress proxy is a working HTTPS CONNECT proxy (Decodo ISP wants "
            "http://USER:PASS@isp.decodo.com:10001), click Test proxy (expect routed + US), then retry."
            % (via, (failing_url or "?")[:200], rep))


# A visible "I'm not a robot" human-check widget on the login/2FA form. We must NOT auto-submit past one
# (that trips the portal into rejecting the login as if the creds were bad — the owner's 2026-07-16 repro),
# so the live session detects it BEFORE clicking Sign in / Verify and hands control to the human. Detects
# Google reCAPTCHA v2, hCaptcha, and Cloudflare Turnstile by their rendered widget (element) AND by the
# human-facing text. We deliberately key off the VISIBLE checkbox widget / human phrasing — NOT a bare
# "recaptcha" string in a <script> include (which is present on reCAPTCHA v3 pages that need no human
# interaction and would false-positive).
_CAPTCHA_SELECTORS = ("iframe[src*='recaptcha']", "iframe[title*='recaptcha']",
                      "iframe[src*='hcaptcha']", "iframe[title*='hcaptcha']",
                      "iframe[src*='turnstile']", ".g-recaptcha", "#g-recaptcha",
                      ".h-captcha", ".cf-turnstile", "#cf-turnstile")
_CAPTCHA_TEXT = ("i'm not a robot", "i am not a robot", "i`m not a robot",
                 "verify you are human", "verify you're human", "confirm you are human",
                 "please complete the captcha", "complete the security check",
                 "complete the captcha to continue")


def _looks_like_captcha(page):
    """True if a captcha / 'I'm not a robot' human-check widget is present on the current page (across
    frames) — by rendered element (visible where determinable) OR by human-facing text. Best-effort;
    never raises. Used by the live session to enter human_action instead of auto-submitting past a check."""
    try:
        frames = _frames(page)
    except Exception:
        frames = [page]
    for fr in frames:
        for sel in _CAPTCHA_SELECTORS:
            try:
                el = fr.query_selector(sel)
            except Exception:
                el = None
            if not el:
                continue
            try:
                if el.is_visible():
                    return True
            except Exception:
                return True   # present but visibility can't be resolved → treat as present
    try:
        if any(t in _page_text(page) for t in _CAPTCHA_TEXT):
            return True
    except Exception:
        pass
    return False


def _wait_settle(page, timeout_s=30):
    """Give Cloudflare's managed challenge time to clear, then — if it hasn't — RELOAD once and wait
    again. The JS challenge often solves and sets the cf_clearance cookie without auto-redirecting the
    headless page; a single reload picks up the clearance and lands on the form. If it STILL hasn't
    cleared after the reload, the egress IP is genuinely blocked (or the browser fingerprint is flagged)."""
    def _spin(budget):
        waited = 0.0
        while _looks_like_cloudflare(page) and waited < budget:
            page.wait_for_timeout(1500)
            waited += 1.5
    _spin(timeout_s)
    if _looks_like_cloudflare(page):
        try:
            page.reload(timeout=45000, wait_until="domcontentloaded")
        except Exception:
            pass
        _spin(timeout_s)
    if _looks_like_cloudflare(page):
        raise VidaPayLoginError(
            "The portal's WAF is blocking this egress IP (bot challenge did not clear, even after a "
            "reload). Rotate to a fresh residential IP and retry; if a clean US residential IP is STILL "
            "challenged, the block is on the headless-browser fingerprint — the same WAF caveat as ePay.")


# ── heuristic field finders (frame-aware; credential-free; calibrated on first live login) ─────
def _find_input(scope, kinds=("text", "email", "tel", "number"), want=(), avoid=()):
    """First VISIBLE input in `scope` (a Page or Frame) whose type ∈ kinds and whose
    name/id/placeholder/aria/label text matches ANY `want` and NO `avoid` keyword."""
    try:
        handles = scope.query_selector_all("input, textarea")
    except Exception:
        return None
    best = None
    for h in handles:
        try:
            if not h.is_visible():
                continue
            typ = (h.get_attribute("type") or "text").lower()
            if kinds and typ not in kinds:
                continue
            hay = " ".join(filter(None, [
                h.get_attribute("name"), h.get_attribute("id"), h.get_attribute("placeholder"),
                h.get_attribute("aria-label"), h.get_attribute("autocomplete"),
                h.get_attribute("title")])).lower()
            if avoid and any(a in hay for a in avoid):
                continue
            if not want:
                return h
            if any(w in hay for w in want):
                return h
            if best is None:
                best = h
        except Exception:
            continue
    return best


def _password_frame(page):
    """Return (frame, password_element) for the first frame that has a password field, else (None,None)."""
    for fr in _frames(page):
        try:
            el = fr.query_selector("input[type=password]")
            if el:
                return fr, el
        except Exception:
            continue
    return None, None


def _wait_for_password(page, timeout_s=30):
    """Poll (across frames, clearing Cloudflare) until the login form's password field appears."""
    waited = 0.0
    while waited < timeout_s:
        if _looks_like_cloudflare(page):
            page.wait_for_timeout(1500); waited += 1.5; continue
        fr, el = _password_frame(page)
        if el:
            return fr, el
        page.wait_for_timeout(1500); waited += 1.5
    return _password_frame(page)


def _click_submit(scope, texts):
    """Click the first visible button/submit in `scope` whose text/value matches any of `texts`."""
    try:
        cands = scope.query_selector_all("button, input[type=submit], input[type=button], a[role=button]")
    except Exception:
        cands = []
    for c in cands:
        try:
            if not c.is_visible():
                continue
            label = (c.get_attribute("value") or c.inner_text() or "").strip().lower()
            if any(t in label for t in texts):
                c.click()
                return True
        except Exception:
            continue
    return False


# Controls a navigation click must NEVER hit, whatever the caller asked for. Clicking one of these
# would end the very session the pull is running on.
_NAV_NEVER = ("log out", "logout", "sign out", "signout", "log off", "logoff")


def _click_nav(scope, texts):
    """Click the first visible NAVIGATION control in `scope` matching any of `texts` — buttons FIRST
    (identical to _click_submit), then ordinary `<a href>` LINKS.

    ROOT CAUSE OF "logged in but nothing imports" (owner report 2026-07-27). _open_reports_page's
    comment says it clicks "a Reports / Billing Manager link", but the only helper it had was
    _click_submit, whose selector is `button, input[type=submit], input[type=button], a[role=button]`
    — an ordinary ASP.NET menu anchor (`<a href="Reports.aspx">Reports</a>`, or the
    `href="javascript:__doPostBack(...)"` variant) matches NONE of those. So on any portal whose
    post-login landing page is not already the Reports page, the navigation silently did nothing,
    _select_report then found no report <select>, and EVERY report came back
    "report not found in the Reports dropdown" → "pulled 0 rows across 0 report(s)" while the login
    itself showed a green ✅ Connected. Anchors are now clickable; sign-out controls never are."""
    try:
        btns = scope.query_selector_all("button, input[type=submit], input[type=button], a[role=button]")
    except Exception:
        btns = []
    try:
        links = scope.query_selector_all("a[href], a")
    except Exception:
        links = []
    for c in list(btns) + list(links):
        try:
            if not c.is_visible():
                continue
            label = ((c.get_attribute("value") or "") + " " + (c.inner_text() or "")).strip().lower()
            if not label:
                label = (c.get_attribute("title") or c.get_attribute("aria-label") or "").strip().lower()
            if not label or any(bad in label for bad in _NAV_NEVER):
                continue
            if any(t in label for t in texts):
                c.click()
                return True
        except Exception:
            continue
    return False


_CODE_KWS = ("code", "otp", "pin", "verif", "token", "authenticat", "2fa", "twofactor",
             "one-time", "onetime", "passcode", "security code")


def _code_field(page):
    """The visible 2FA-code input — STRICT keyword match on name/id/placeholder/aria/autocomplete, with
    NO 'first text input' fallback. (The fallback wrongly flagged the 'Trust This Device' nickname box as
    a code field.) Callers that must actually FILL a code keep their own lone-field fallback."""
    for f in _frames(page):
        try:
            handles = f.query_selector_all("input")
        except Exception:
            continue
        for h in handles:
            try:
                if not h.is_visible():
                    continue
                if (h.get_attribute("type") or "text").lower() not in ("text", "tel", "number", "password"):
                    continue
                hay = " ".join(filter(None, [
                    h.get_attribute("name"), h.get_attribute("id"), h.get_attribute("placeholder"),
                    h.get_attribute("aria-label"), h.get_attribute("autocomplete")])).lower()
                if any(k in hay for k in _CODE_KWS):
                    return h
            except Exception:
                continue
    return None


def _classify(page):
    """Where did we land: 'proxy_error' | 'twofa' | 'authenticated' | 'login' | 'botwall' | 'unknown'."""
    # The egress proxy's own rejection page is checked FIRST — it superficially resembles a generic
    # error/login page, and misreading it as 'unknown'/expired would blunder the sweep into report pulls.
    if _looks_like_proxy_error(page):
        return "proxy_error"
    if _looks_like_bot_wall(page) and not _password_frame(page)[1]:
        return "botwall"
    fr, pw = _password_frame(page)
    code_field = _code_field(page)
    twofa_words = ("verification code", "verify your", "two-factor", "two factor", "2-step",
                   "authentication code", "code sent", "enter the code", "one-time",
                   "we sent", "security code", "otp")
    auth_words = ("logout", "log out", "sign out", "main panel", "dashboard", "commission",
                  "welcome", "my account")
    # STRUCTURAL signals first — a VISIBLE strict 2FA-code input or a password field is unambiguous and
    # cannot be faked by leftover page text.
    if code_field and not pw:
        return "twofa"                                   # row 1 — real 2FA code-entry page
    if pw:
        return "login"                                   # row 2
    # No structural login/2FA control on the page. Decide by the VISIBLE text (what the operator SEES),
    # preferring the TOP frame and falling back to all frames — NOT _page_text's raw HTML/script soup, in
    # which a stale/hidden 2FA phrase (or a script/data-attr 'otp') survives and used to permanently pin
    # 'twofa' over the authenticated Main Panel (incident 3). Per the matrix an AUTHENTICATED marker BEATS
    # a 2FA marker (row 3 — Main Panel wins even if a stale 'we sent…' also appears); a 2FA marker with NO
    # auth marker (the 'New Sign In → Next' interstitial, which has no code box) stays 'twofa' (row 4).
    main = _main_frame_text(page)
    text = main if (any(w in main for w in auth_words) or any(w in main for w in twofa_words)) \
        else _all_frames_text(page)
    if any(w in text for w in auth_words):
        return "authenticated"
    if any(w in text for w in twofa_words):
        return "twofa"
    return "unknown"


def _twofa_hint(page):
    for fr in _frames(page):
        try:
            txt = fr.evaluate(
                """() => (document.body.innerText||'').split('\\n').map(s=>s.trim())
                         .filter(s => /code|sent|verif|otp|text|email|phone/i.test(s) && s.length<120)
                         .slice(0,3).join(' | ')""")
            if txt:
                return txt[:200]
        except Exception:
            continue
    return None


# Buttons/links that DISPATCH a 2FA code (a human clicks these; a headless login that only DETECTS the
# 2FA screen never receives a code). Ordered SMS-first (the common working channel), then generic send.
_SEND_CODE_TEXTS = (
    "send text", "text me", "send me a text", "send sms", "send code via text", "via text message",
    "send code", "send a code", "send me a code", "send verification", "send security code",
    "send passcode", "request code", "request a code", "get code", "get a code", "email me",
    "send email", "send me the code", "resend code", "send the code",
)


def _trigger_2fa_send(page):
    """Click the best 'Send code' / method (SMS/Email) control so the portal actually DISPATCHES the code.
    Returns the clicked label, or None if none found. SAFE: never clicks a verify/submit/login/continue/
    cancel control (those would submit an empty code or leave the page)."""
    AVOID = ("verify", "confirm", "submit", "continue", "log in", "login", "sign in",
             "cancel", "back", "next", "logout", "log out")
    try:
        frames = _frames(page)
    except Exception:
        frames = [page]
    for want in _SEND_CODE_TEXTS:
        for fr in frames:
            try:
                cands = fr.query_selector_all("button, input[type=submit], input[type=button], a, a[role=button]")
            except Exception:
                continue
            for c in cands:
                try:
                    if not c.is_visible():
                        continue
                    label = (c.get_attribute("value") or c.inner_text() or "").strip().lower()
                    if not label or len(label) > 40:
                        continue
                    if want in label and not any(a in label for a in AVOID):
                        c.click()
                        return label[:40]
                except Exception:
                    continue
    return None


# The AFFIRMATIVE "trust/remember this device" wording, and the NEGATIVE option to avoid — VidaPay's
# new-device screen offers a REQUIRED radio pair ("Trust this device" vs "Don't trust / public
# computer"), and picking the wrong one (or none) blocks the code submit.
_REMEMBER_POS = ("remember", "trust", "recognize this", "this device", "this browser",
                 "this computer", "90 day", "save this device", "don't ask", "dont ask")
_REMEMBER_NEG = ("don't trust", "do not trust", "dont trust", "not now", "public",
                 "no,", "don't remember", "do not remember", "someone else", "shared")


def _remember_text(el, scope):
    hay = " ".join(filter(None, [el.get_attribute("name"), el.get_attribute("id"),
                                 el.get_attribute("value"), el.get_attribute("aria-label")])).lower()
    lbl = ""
    try:
        cid = el.get_attribute("id")
        if cid:
            le = scope.query_selector('label[for="%s"]' % cid)
            if le:
                lbl = (le.inner_text() or "").strip().lower()
    except Exception:
        pass
    return hay + " " + lbl


def _tick_remember(scope):
    """Select the 'trust / remember this device' control (CHECKBOX **or RADIO**) on the 2FA screen so
    the portal trusts this profile (~90 days) AND — on portals like VidaPay that REQUIRE the choice —
    the code submit is unblocked. Picks the AFFIRMATIVE option, never a 'don't trust / public computer'
    one, and never an unrelated control. Returns True if it selected something."""
    if scope is None:
        return False
    try:
        controls = scope.query_selector_all("input[type=checkbox], input[type=radio]")
    except Exception:
        return False
    for c in controls:
        try:
            if not c.is_visible():
                continue
            text = _remember_text(c, scope)
            if any(n in text for n in _REMEMBER_NEG):
                continue
            if any(pkw in text for pkw in _REMEMBER_POS):
                if not c.is_checked():
                    c.check()
                return True
        except Exception:
            continue
    return False


# Provider keywords for the 2FA METHOD-CHOOSER step, SMS/text first (the channel the operator
# actually receives). Deliberately excludes "call" (voice) and authenticator options.
_METHOD_PICKS = ("sms", "text", "phone", "mobile", "cell")


def _choose_2fa_method(page):
    """Handle the METHOD-CHOOSER step (ASP.NET Identity's SendCode page and kin): a provider <select>
    or radio group plus a plain Submit/Next/Continue button — and NO code input yet. _trigger_2fa_send
    intentionally refuses generic submit/continue/next labels (on a code-ENTRY screen those would
    submit an empty code), so on a chooser page it clicks nothing and the portal never dispatches a
    code — the "code never arrives" symptom. Here there IS no code field, so selecting SMS/phone and
    clicking the dispatch button is safe. Returns a 'picked → clicked' label, or None. Only clicks
    the submit when the page is chooser-shaped (a visible select/radio exists) — never blind."""
    picked, saw_chooser = None, False
    for fr in _frames(page):
        # provider <select> (ASP.NET SendCode renders SelectedProvider as a dropdown)
        try:
            for s in fr.query_selector_all("select"):
                if not s.is_visible():
                    continue
                saw_chooser = True
                for o in s.query_selector_all("option"):
                    t = ((o.inner_text() or "") + " " + (o.get_attribute("value") or "")).lower()
                    if any(k in t for k in _METHOD_PICKS):
                        val = o.get_attribute("value")
                        if val is not None:
                            s.select_option(value=val)
                        else:
                            s.select_option(label=(o.inner_text() or "").strip())
                        picked = ((o.inner_text() or "").strip() or val or "sms")[:30]
                        break
                if picked:
                    break
        except Exception:
            pass
        if picked:
            break
        # provider radio group ("Text me (***) ***-1234" / "Email me j***@…" choices)
        try:
            for r in fr.query_selector_all("input[type=radio]"):
                if not r.is_visible():
                    continue
                saw_chooser = True
                hay = " ".join(filter(None, [r.get_attribute("name"), r.get_attribute("id"),
                                             r.get_attribute("value"), r.get_attribute("aria-label")])).lower()
                lbl = ""
                try:
                    rid = r.get_attribute("id")
                    if rid:
                        le = fr.query_selector('label[for="%s"]' % rid)
                        lbl = (le.inner_text() or "").strip().lower() if le else ""
                except Exception:
                    pass
                if any(k in hay or k in lbl for k in _METHOD_PICKS):
                    r.check()
                    picked = (lbl or hay)[:30] or "sms"
                    break
        except Exception:
            pass
        if picked:
            break
    if not (picked or saw_chooser):
        return None
    # Dispatch: on a chooser page the send action is usually a bare Send/Submit/Next/Continue.
    for fr in _frames(page):
        if _click_submit(fr, ("send", "submit", "next", "continue")):
            return ((picked + " → ") if picked else "") + "submitted method chooser"
    return None


# Wording of the post-login "confirm this computer" interstitial (T-CETRA/VidaPay: "New Sign In —
# We don't recognize this device… Cancel | Next"). Keywords deliberately start AFTER the word
# "don't", so the don't / don’t / do not variants all hit "recognize this device".
_INTERSTITIAL_WORDS = ("recognize this device", "recognize this computer", "new sign in",
                      "unrecognized device", "confirm this device", "confirm this computer",
                      "verify your identity")


def _click_2fa_interstitial(page):
    """The post-login device-confirmation interstitial: no code field, no chooser — just Cancel + Next,
    and clicking NEXT is what advances to the 2FA code dispatch (owner screenshot 2026-07-15). Safe
    ONLY because there is no code input to empty-submit; never clicks Cancel/Sign-Out, and only fires
    when the page actually carries the interstitial wording."""
    if _code_field(page):
        return None
    body = _page_text(page)
    if not any(w in body for w in _INTERSTITIAL_WORDS):
        return None
    AVOID = ("cancel", "sign out", "logout", "log out", "back", "forgot")
    for fr in _frames(page):
        try:
            cands = fr.query_selector_all("button, input[type=submit], input[type=button], a[role=button], a")
        except Exception:
            continue
        for c in cands:
            try:
                if not c.is_visible():
                    continue
                label = (c.get_attribute("value") or c.inner_text() or "").strip().lower()
                if not label or len(label) > 30 or any(a in label for a in AVOID):
                    continue
                if label in ("next", "continue", "verify", "proceed", "confirm", "ok") or label.startswith("next"):
                    c.click()
                    return label[:40]
            except Exception:
                continue
    return None


def _advance_2fa(page):
    """Drive the pre-code 2FA steps a human clicks through — an explicit send-code button, the
    'we don't recognize this device → Next' interstitial, or the SMS/email method chooser — LOOPING
    (max 3 transitions, e.g. interstitial → chooser → code screen) until a code input appears or
    nothing more can be safely clicked. Returns the ' → '-joined labels clicked, or None."""
    steps = []
    for _ in range(3):
        try:
            if _code_field(page):
                break
        except Exception:
            break
        sent = None
        for attempt in (_trigger_2fa_send, _choose_2fa_method, _click_2fa_interstitial):
            try:
                sent = attempt(page)
            except Exception:
                sent = None
            if sent:
                break
        if not sent:
            break
        steps.append(sent)
        try:
            page.wait_for_timeout(3000)
            _wait_settle(page)
        except Exception:
            pass
    return " → ".join(steps) if steps else None


# The POST-code interstitial pages T-CETRA/VidaPay shows AFTER the code is accepted but BEFORE the
# dashboard: "Trust This Device" (nickname + Next) AND "Ready to Go — you have completed 2-Factor
# Authentication" (Continue). BOTH carry a "Sign Out" header (so they LOOK authenticated to _classify),
# so both MUST be clicked THROUGH before concluding auth — else the live session freezes on them and the
# operator can't proceed. The affirmative button ("Continue" is in _TRUST_NEXT_WANT) is clicked.
_TRUST_PAGE_WORDS = ("trust this device", "trust this computer", "remember this as a secure device",
                     "secure device", "won't be needed when you sign in",
                     "wont be needed when you sign in", "nickname for reference",
                     "give this device a nickname", "recognize this device going forward",
                     "ready to go", "you have completed 2-factor", "you have completed 2 factor",
                     "completed 2-factor authentication", "completed 2 factor authentication")


_TRUST_NEXT_AVOID = ("cancel", "sign out", "logout", "log out", "back", "don't", "do not",
                     "skip", "not now", "forgot", "assistance")
_TRUST_NEXT_WANT = ("next", "continue", "confirm", "trust", "save", "finish", "done", "proceed",
                    "submit", "ok")


def _confirm_trust_device(page):
    """If we're on the post-code 'Trust This Device' page (nickname + Next, no code field), give the
    device a nickname when blank and click Next to finalize the trusted session. Returns True if clicked.
    Never clicks Sign Out / Cancel / a 'Don't trust' control. Robust to the button being a plain <a>
    styled as a button, and to a normal .click() being intercepted (falls back to a DOM click)."""
    try:
        if _code_field(page):
            return False
    except Exception:
        return False
    if not any(w in _page_text(page) for w in _TRUST_PAGE_WORDS):
        return False
    # Fill/normalize the nickname field. Use keystrokes (delay) so any input-event validation that
    # gates the Next button fires — same trait the login button had. If prefilled, nudge it once.
    for fr in _frames(page):
        try:
            el = _find_input(fr, kinds=("text",),
                             want=("nickname", "name", "device", "reference", "label"),
                             avoid=("code", "otp", "user", "pass"))
            if not el:
                continue
            cur = (el.input_value() or "").strip()
            if not cur:
                el.click(); el.type("MetricsPro", delay=15)
            else:
                try:                       # nudge validation without changing the value
                    el.click(); el.type(" "); el.press("Backspace")
                except Exception:
                    pass
        except Exception:
            continue
    # Click the affirmative control. Include PLAIN <a> and [onclick] (T-CETRA's Next is an anchor
    # styled as a button), and fall back to a DOM click if Playwright's actionable click is blocked.
    for fr in _frames(page):
        try:
            cands = fr.query_selector_all(
                "button, input[type=submit], input[type=button], a, [role=button], [onclick]")
        except Exception:
            continue
        for c in cands:
            try:
                if not c.is_visible():
                    continue
                label = (c.get_attribute("value") or c.inner_text()
                         or c.get_attribute("aria-label") or "").strip().lower()
                if not label or len(label) > 30 or any(a in label for a in _TRUST_NEXT_AVOID):
                    continue
                if not any(label == w or label.startswith(w) for w in _TRUST_NEXT_WANT):
                    continue
                try:
                    c.click(timeout=6000)
                except Exception:
                    try:
                        c.evaluate("el => el.click()")   # DOM click — bypasses overlay/actionability
                    except Exception:
                        continue
                return True
            except Exception:
                continue
    # LAST RESORT — find the "Next"/"Trust" control by TEXT across ANY tag (incl. a <div>/<span> styled
    # as a button whose handler is bound in JS, which the tag-based query above can't see) and DOM-click
    # the smallest/most-clickable match. Returns True if it clicked something.
    js = r"""() => {
        const WANT = ['next','continue','confirm','trust','proceed','submit','done','finish'];
        const AVOID = ['cancel','sign out','log out','logout','back','assistance','don','skip','not now','forgot'];
        const els = Array.from(document.querySelectorAll(
            'button,input[type=submit],input[type=button],a,[role=button],[onclick],div,span'));
        const cands = [];
        for (const e of els) {
            const t = (e.value || e.innerText || e.textContent || '').trim().toLowerCase();
            if (!t || t.length > 25) continue;
            if (AVOID.some(a => t.includes(a))) continue;
            if (!WANT.some(w => t === w || t.startsWith(w))) continue;
            const r = e.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) continue;
            const tag = e.tagName.toLowerCase();
            const clickable = ['button','a','input'].includes(tag) || e.hasAttribute('role') || e.hasAttribute('onclick');
            cands.push({e, score: (clickable ? 0 : 1000) + e.querySelectorAll('*').length});
        }
        if (!cands.length) return null;
        cands.sort((a, b) => a.score - b.score);
        cands[0].e.click();
        return (cands[0].e.value || cands[0].e.innerText || '').trim().slice(0, 30);
    }"""
    for fr in _frames(page):
        try:
            hit = fr.evaluate(js)
            if hit:
                return True
        except Exception:
            continue
    return False


def finalize_after_code(page, on_step=None):
    return _finalize_after_code_impl(page, on_step)


def _finalize_after_code_impl(page, on_step=None):
    """After the code is submitted, click through any post-code 'Trust This Device' page(s) until the
    portal lands on the app. Returns the final _classify state ('authenticated' | 'twofa' | ...). The
    trust page can LOOK authenticated (its header has 'Sign Out'), so it's handled BEFORE concluding
    auth — otherwise the session saves without the trust actually registering (no 90-day skip).
    `on_step` (optional) is called each pass so a live viewer's screenshot keeps refreshing."""
    for _ in range(6):
        try:
            _wait_settle(page)
        except Exception:
            pass
        if callable(on_step):
            try:
                on_step()
            except Exception:
                pass
        if _confirm_trust_device(page):
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(2000)
            continue
        # While the 'Trust This Device' wording is still on screen, do NOT conclude authenticated — its
        # header carries a 'Sign Out' link that _classify would misread as logged-in even though the Next
        # click hasn't landed. Keep looping (retrying the click) until the trust page is actually gone.
        if any(w in _page_text(page) for w in _TRUST_PAGE_WORDS):
            page.wait_for_timeout(1500)
            continue
        state = _classify(page)
        if state in ("authenticated", "twofa"):
            return state
        page.wait_for_timeout(1500)
    # Fell out of the loop still on the trust page → report a clear, actionable error (not a false auth).
    if any(w in _page_text(page) for w in _TRUST_PAGE_WORDS):
        return "twofa"
    return _classify(page)


def _twofa_result(page, ctx, extra=None):
    """Build a needs_2fa result, first CLICKING THROUGH whatever pre-code steps the portal requires
    (send-code button / device interstitial / method chooser — else no code is ever dispatched).
    Captures sent_via + the page's clickable buttons (in diag) for calibration."""
    sent = None
    try:
        sent = _advance_2fa(page)
    except Exception:
        sent = None
    diag = _snapshot(page)
    if extra:
        try:
            diag = {**diag, **extra}
        except Exception:
            pass
    # If we advanced all the way to the CODE-ENTRY page, remember its URL. complete_2fa navigates
    # straight there — avoiding a second "New Sign In → Next" click, which would DISPATCH ANOTHER code
    # and invalidate the one the operator just typed (owner: "it sent another code twice").
    st = capture_session_state(page, ctx)
    try:
        if _code_field(page):
            st["_2fa_url"] = page.url
    except Exception:
        pass
    return {"status": "needs_2fa", "storage_state": st,
            "two_fa_hint": _twofa_hint(page), "sent_via": sent, "diag": diag,
            "screenshot_b64": _shot_b64(page)}


# CONNECTION-CLASS Chromium/net errors — a transport failure BETWEEN the browser and the portal (a
# rotating residential-proxy exit went bad, a severed TLS tunnel, a DNS blip). Through Decodo's rotating
# residential proxy a single bad exit / severed TLS gives net::ERR_CONNECTION_CLOSED on ONE navigation,
# so a FRESH ENTRY navigation (nothing clicked/submitted yet → no 2FA-resend risk) is SAFE to retry.
# NOT in this set on purpose: selector timeouts (Timeout ...), WAF/bot walls, auth rejections — those are
# NOT transient transport blips and must surface on the first try.
_CONNECTION_ERR_MARKERS = (
    "ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET", "ERR_CONNECTION_REFUSED",
    "ERR_EMPTY_RESPONSE", "ERR_TIMED_OUT", "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_CONNECTION_FAILED", "ERR_SOCKET_NOT_CONNECTED", "ERR_NAME_NOT_RESOLVED",
)


def _is_connection_error(exc):
    """True if `exc` is a transport/connection-class navigation failure (vs a selector timeout, a WAF/bot
    wall, or an auth rejection). Matched on the net::ERR_ marker text so it's processor-generic (VidaPay +
    b2bsoft) and independent of the concrete Playwright error type."""
    s = str(exc or "")
    return any(m in s for m in _CONNECTION_ERR_MARKERS)


def _first_conn_marker(exc):
    """The specific net::ERR_ marker in `exc` (for the operator message); a generic fallback if none."""
    s = str(exc or "")
    for m in _CONNECTION_ERR_MARKERS:
        if m in s:
            return m
    return "ERR_CONNECTION"


def _strip_call_log(msg):
    """Drop Playwright's raw 'Call log:' block (and everything after it) so an operator never sees the
    internal trace ("Call log: - navigating to ... waiting until domcontentloaded"). Keeps the text UP TO
    the marker; a no-op when there is no call log."""
    s = str(msg or "")
    i = s.find("Call log:")
    if i != -1:
        s = s[:i]
    return s.strip()


def _goto_with_retry(page, url, *, timeout=60000, wait_until="domcontentloaded",
                     attempts=3, backoffs=(1.5, 3.0)):
    """page.goto with a BOUNDED retry on CONNECTION-CLASS failures only, RETURNING the navigation
    Response (or None). A rotating residential proxy can
    hand back a dead exit / severed TLS, so a single navigation dies with net::ERR_CONNECTION_CLOSED even
    though the very next attempt succeeds. SAFE only for a FRESH ENTRY navigation where nothing has been
    clicked/submitted (no 2FA-resend risk) — callers MUST NOT use it for a post-login / post-code (2FA)
    navigation. A NON-connection error (selector timeout, WAF) is re-raised on the FIRST failure (never
    retried). On the final connection-class failure raises a VidaPayLoginError with an operator-friendly
    message that NEVER carries the Playwright 'Call log:' block."""
    last = None
    for i in range(attempts):
        try:
            # RETURNED (was: discarded) so the caller can read the WIRE status — an HTTP 429 block
            # response frequently has no readable body at all, so this is the only honest signal.
            return page.goto(url, timeout=timeout, wait_until=wait_until)
        except Exception as e:
            last = e
            if not _is_connection_error(e):
                raise                                   # not a transient proxy drop → surface immediately
            if i < attempts - 1:
                try:
                    delay = backoffs[i] if i < len(backoffs) else backoffs[-1]
                    page.wait_for_timeout(int(delay * 1000))
                except Exception:
                    pass
                continue
    marker = _first_conn_marker(last)
    raise VidaPayLoginError(
        "The portal connection dropped at/behind the egress proxy (net::%s) — it kept dropping across %d "
        "attempts before the portal page could load. This is a transport failure between the server and "
        "the portal (a rotating residential-proxy exit went bad or the TLS tunnel was severed), NOT your "
        "credentials and NOT a 2FA problem. Retry in a moment; if it persists, click Test proxy and rotate "
        "to a fresh residential IP." % (marker, attempts))


def _https_force(url):
    """Coerce a destination URL to https (our known-good destinations are https-only). None for empty."""
    u = (url or "").strip()
    if not u:
        return None
    if u.lower().startswith("http://"):
        return "https://" + u[len("http://"):]
    if "://" not in u:
        return "https://" + u.lstrip("/")
    return u


def _recover_from_proxy_error(page, attempts=2, dest_url=None):
    """Recover from the egress proxy's OWN squid rejection page (the T-CETRA http-302 hop Playwright's
    http→https route can't intercept — a SERVER-SIDE 302 to plain-http …/Default.aspx?returnto=http%3a… is
    followed at network level, so Chromium reaches squid raw and renders "requested URL could not be
    retrieved"). Pure GET re-navigation, bounded — it CANNOT re-submit a form or resend a 2FA code, so it is
    safe pre-auth AND post-login/post-code. Returns True if the squid page cleared, False otherwise.

    v2 (incident 4). Two phases:
      1. HTTPS-TWIN re-goto keyed off page.url — works when squid reports a FULL http:// absolute URL.
      2. DESTINATION FALLBACK — when page.url is NOT a usable http absolute (about:blank / chrome-error://
         / a host-less path-only form / already https) OR the twin re-goto keeps squid'ing, do a DIRECT
         goto of the KNOWN-good https `dest_url` (pre-auth → the id-server LOGIN_URL; post-submit/post-auth
         → the https base/Main-Panel). Immune to URL mangling: context cookies persist, so if the login
         completed server-side the direct goto lands authenticated (auth-detect _classify concludes it);
         if not, it lands on the login/2FA screen and the normal flow resumes.
    Never raises."""
    def _is_squid():
        try:
            return _looks_like_proxy_error(page)
        except Exception:
            return False

    def _try(u):
        try:
            page.goto(u, timeout=60000, wait_until="domcontentloaded")
            _wait_settle(page)
            page.wait_for_timeout(1500)
            return True
        except Exception:
            return False

    # Phase 1 — HTTPS-TWIN re-goto off the current URL (v1 path; the incident-2 full-absolute-http case).
    for _ in range(max(1, attempts)):
        if not _is_squid():
            return True
        try:
            cur = page.url or ""
        except Exception:
            cur = ""
        https = _https_upgrade_url(cur)
        if not https:
            break                                 # url unusable → go to the destination fallback
        if not _try(https):
            break
    # Phase 2 — DESTINATION FALLBACK: one direct goto of the known-good https destination (retrying the
    # SAME url would just re-squid, so a single attempt is enough and clearly bounded).
    if _is_squid() and dest_url:
        d = _https_force(dest_url)
        if d:
            _try(d)
    return not _is_squid()


def _goto_login(page, base_url, markers=None):
    """Navigate to the portal and settle. If it lands on the bot-wall (or the egress squid page) with no
    form, retry straight at the id-server login endpoint (deep links are flagged more often than the login
    URL itself, AND the https id-server is the real pre-auth destination — going there skips the www
    http-redirect chain that produces the squid hop entirely).

    Both navigations here are PRE-LOGIN entry navigations (nothing has been clicked/submitted — no 2FA
    has been dispatched), so each goes through _goto_with_retry: a transient connection-class drop through
    the residential proxy is retried, and an exhausted failure surfaces as a clean VidaPayLoginError (never
    a raw Playwright 'Call log:'). Non-connection errors are NOT retried. After each goto we also run
    _recover_from_proxy_error (the http-302→squid hop; GET-only, no resubmit)."""
    resp = _goto_with_retry(page, base_url, timeout=60000, wait_until="domcontentloaded")
    # RATE-LIMIT GATE #1 — before Cloudflare settling, before the proxy-recovery ladder, before a single
    # keystroke. A throttled portal must cost exactly ONE navigation, not the up-to-10 page loads the
    # recovery + bot-wall fallback below can spend (every one of which the portal counts against us).
    _raise_if_rate_limited(page, resp, markers=markers, where="The portal")
    _wait_settle(page)
    page.wait_for_timeout(2500)
    # PRE-AUTH recovery destination is the https id-server LOGIN_URL (a clean https page with the login
    # form — it never triggers the www returnto→http hop that produces squid).
    _recover_from_proxy_error(page, dest_url=LOGIN_URL)
    _raise_if_rate_limited(page, markers=markers, where="The portal")
    fr, pw = _wait_for_password(page, timeout_s=20)
    if pw:
        return
    if _looks_like_bot_wall(page) or _looks_like_proxy_error(page):
        try:
            resp = _goto_with_retry(page, LOGIN_URL, timeout=60000, wait_until="domcontentloaded")
            _raise_if_rate_limited(page, resp, markers=markers, where="The portal's login server")
            _wait_settle(page)
            _recover_from_proxy_error(page, dest_url=LOGIN_URL)
            _wait_for_password(page, timeout_s=20)
        except (VidaPayLoginError, PortalRateLimited):
            raise
        except Exception:
            pass


def drive_typed_login(page, login_fr, pw_el, account_id, user, pw):
    """Fill + submit the login form on the ALREADY-OPEN page/frame, then leave the page on whatever
    screen the submit produced (the caller classifies). Shared by begin_login AND the live-session
    manager (live_login.py) so the pinned VidaPay/T-CETRA selectors live in ONE place.

    PINNED to the live id.vidapaycrm.com "SIGN IN" DOM (calibrated 2026-07-15): #AccountId
    (type=number) · #Username · #Password · submit #btnClick — with REAL keystroke typing + an
    enable-wait, because #btnClick stays disabled until the form's JS sees key events (fill() skips
    them). Falls back to heuristic field finders for other portal variants. The Sign-In POST is what
    makes the portal DISPATCH the 2FA code, so this must never depend on text heuristics for VidaPay."""
    if login_fr.query_selector("#AccountId") and login_fr.query_selector("#Password"):
        # TYPE (real keystrokes), don't fill: #btnClick is DISABLED until the form's JS validation
        # sees key events — fill() skips them, so the click timed out on "element is not enabled"
        # (same trait as b2bsoft's progressive form).
        def _type_id(sel, val):
            if val in (None, ""):
                return
            el = login_fr.query_selector(sel)
            if not el:
                return
            try:
                el.click()
                el.fill("")
                el.type(str(val), delay=25)
            except Exception:
                try:
                    el.fill(str(val))
                except Exception:
                    pass
        _type_id("#AccountId", account_id)
        _type_id("#Username", user)
        _type_id("#Password", pw or "")
        _ENABLED = "() => { const b = document.getElementById('btnClick'); return !!b && !b.disabled; }"
        try:
            login_fr.wait_for_function(_ENABLED, timeout=8000)
        except Exception:
            # Nudge the validation listeners, then wait once more.
            try:
                login_fr.evaluate(
                    """() => ['AccountId','Username','Password'].forEach(id => {
                           const el = document.getElementById(id); if (!el) return;
                           ['input','change','keyup','blur'].forEach(t =>
                               el.dispatchEvent(new Event(t, {bubbles: true})));
                       })""")
                login_fr.wait_for_function(_ENABLED, timeout=5000)
            except Exception:
                pass
        clicked = False
        btn = login_fr.query_selector("#btnClick")
        if btn:
            try:
                btn.click(timeout=8000)
                clicked = True
            except Exception:
                pass
        if not clicked:
            # Native form-submit fallback — but Enter "succeeds" as an API call even when it
            # submits nothing, so only count it if the login form actually went away.
            try:
                pw_el.press("Enter")
                page.wait_for_timeout(1200)
                try:
                    clicked = not login_fr.query_selector("#Password")
                except Exception:
                    clicked = True   # frame detached = navigation happened = submitted
            except Exception:
                pass
        if not clicked and btn:
            try:
                # Last resort: the fields ARE filled; the disable is stale UI state.
                login_fr.evaluate(
                    "() => { const b = document.getElementById('btnClick'); if (b) { b.disabled = false; b.click(); } }")
            except Exception:
                pass
    else:
        # Unknown portal variant — fill whichever of the three fields exist, heuristically.
        acct_el = _find_input(login_fr, want=("account", "acct", "agent", "dealer", "merchant"),
                              avoid=("user", "pass"))
        user_el = _find_input(login_fr, want=("user", "login", "email", "userid", "username"),
                              avoid=("account", "acct"))
        if acct_el and account_id:
            acct_el.fill(str(account_id))
        if user_el and user:
            user_el.fill(str(user))
        pw_el.fill(str(pw or ""))
        if not _click_submit(login_fr, ("log in", "login", "sign in", "signin", "submit", "continue")):
            try:
                pw_el.press("Enter")
            except Exception:
                pass


def prefill_login(page, login_fr, pw_el, account_id, user, pw):
    """Fill the login fields WITHOUT submitting. A convenience for the human-driven path: when a captcha /
    challenge is present, the live session pre-fills the credentials (so the operator only has to solve the
    'I'm not a robot' box and click Sign in), but MUST NOT auto-submit past the check. Mirrors
    drive_typed_login's field logic (pinned #AccountId/#Username/#Password with real keystrokes so the
    form's enable-validation fires, else heuristic finders) but stops before the submit click.
    Best-effort; never raises."""
    try:
        if login_fr.query_selector("#AccountId") and login_fr.query_selector("#Password"):
            def _type_id(sel, val):
                if val in (None, ""):
                    return
                el = login_fr.query_selector(sel)
                if not el:
                    return
                try:
                    el.click()
                    el.fill("")
                    el.type(str(val), delay=25)
                except Exception:
                    try:
                        el.fill(str(val))
                    except Exception:
                        pass
            _type_id("#AccountId", account_id)
            _type_id("#Username", user)
            _type_id("#Password", pw or "")
        else:
            acct_el = _find_input(login_fr, want=("account", "acct", "agent", "dealer", "merchant"),
                                  avoid=("user", "pass"))
            user_el = _find_input(login_fr, want=("user", "login", "email", "userid", "username"),
                                  avoid=("account", "acct"))
            if acct_el and account_id:
                try:
                    acct_el.fill(str(account_id))
                except Exception:
                    pass
            if user_el and user:
                try:
                    user_el.fill(str(user))
                except Exception:
                    pass
            try:
                pw_el.fill(str(pw or ""))
            except Exception:
                pass
    except Exception:
        pass


# ── phase 1: start login ───────────────────────────────────────────────────────────────────────
def begin_login(url, account_id, user, pw, proxy_url=None):
    """Launch headless Chromium, submit Account ID + User ID + Password, report where we land.
    Returns {"status": "needs_2fa"|"authenticated", "storage_state": {...}, ...}.
    Raises VidaPayLoginError on bot-wall/Cloudflare block, missing Chromium, or rejected creds."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise VidaPayLoginError(
            "Playwright/Chromium is not available in the backend image. Add "
            "`RUN playwright install --with-deps chromium` to backend/Dockerfile.")
    base_url = _norm_url(url, DEFAULT_URL)
    proxy = _proxy_arg(proxy_url)
    with sync_playwright() as p:
        browser = _launch(p)
        ctx = _new_context(browser, proxy=proxy)
        page = ctx.new_page()
        try:
            _goto_login(page, base_url)
            login_fr, pw_el = _password_frame(page)
            if not pw_el:
                diag = _snapshot(page)
                # A rate-limit page has no password field either. Naming it correctly is what stops an
                # operator from "fixing" a block by logging in again — which extends it.
                _raise_if_rate_limited(page, markers=None, where="The portal")
                if _looks_like_proxy_error(page):
                    raise VidaPayLoginError(
                        _proxy_error_message(page.url, proxy_url, _squid_reported_url(page))
                        + " Diagnostic: " + str(diag))
                if _looks_like_bot_wall(page):
                    raise VidaPayLoginError(
                        "VidaPay served an anti-automation page (\"Something doesn't look right\") instead "
                        "of the login form — the portal flagged this browser/IP. It must be reached from "
                        "an allow-listed / residential IP (same WAF caveat as ePay). Diagnostic: " + str(diag))
                raise VidaPayLoginError(
                    "Could not find the password field on the VidaPay login page — the form may render "
                    "differently than expected. Diagnostic: " + str(diag))
            # Fill + submit the (pinned VidaPay/T-CETRA or heuristic) login form. Extracted so the
            # live-session manager (live_login.py) drives the identical login against its live page.
            drive_typed_login(page, login_fr, pw_el, account_id, user, pw)
            try:
                page.wait_for_load_state("networkidle", timeout=25000)
            except Exception:
                pass
            page.wait_for_timeout(3500)
            _wait_settle(page)
            # RATE-LIMIT GATE #2 — the POST-SUBMIT page. A portal that serves the form and then throttles
            # the sign-in POST lands here; without this it fell through to "Login was rejected —
            # credentials not accepted", sending the operator to re-type perfectly good credentials.
            _raise_if_rate_limited(page, markers=None, where="The portal")
            state = _classify(page)
            if state == "proxy_error" and _recover_from_proxy_error(page, dest_url=base_url):
                state = _classify(page)            # http-302→squid hop recovered (GET-only, no re-submit)
            diag = _snapshot(page)
            if state == "proxy_error":
                raise VidaPayLoginError(
                    _proxy_error_message(page.url, proxy_url, _squid_reported_url(page))
                    + " Diagnostic: " + str(diag))
            if state == "twofa":
                return _twofa_result(page, ctx)
            if state == "authenticated":
                return {"status": "authenticated", "storage_state": capture_session_state(page, ctx), "diag": diag,
                        "screenshot_b64": _shot_b64(page)}
            if state == "botwall":
                raise VidaPayLoginError(
                    "VidaPay served an anti-automation page after the login submit — reach it from an "
                    "allow-listed / residential IP. Diagnostic: " + str(diag))
            if state == "login":
                raise VidaPayLoginError(
                    "Login was rejected — Account ID / User ID / Password not accepted (still on the "
                    "login form). Double-check the three credentials. Diagnostic: " + str(diag))
            return _twofa_result(page, ctx, {"_note": "post-login page not recognized as 2FA or app; if no code field appears, send this diagnostic for calibration"})
        except Exception as e:   # attach the evidence to EVERY failure, incl. Playwright timeouts
            if getattr(e, "screenshot_b64", None) is None:
                try:
                    e.screenshot_b64 = _shot_b64(page)
                except Exception:
                    pass
            raise
        finally:
            browser.close()


def begin_login_b2bsoft(url, access_code, user, pw, proxy_url=None):
    """b2bsoft SSO (sso.b2bsoft.com, IdentityServer) is a MULTI-STEP login: page 1 asks for the ACCESS
    CODE (Company ID — the same value as VidaPay's Account ID, DB field account_id), THEN page 2 asks for
    User ID + Password, then the 2FA challenge. The generic single-page begin_login can't find the
    password on page 1 (that page only has #companyId), so this does the Access-Code step first. Same
    {status, storage_state, two_fa_hint, diag} return shape as begin_login."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise VidaPayLoginError("Playwright/Chromium is not available in the backend image.")
    base_url = _norm_url(url, B2BSOFT_URL)
    with sync_playwright() as p:
        browser = _launch(p)
        ctx = _new_context(browser, proxy=_proxy_arg(proxy_url))
        page = ctx.new_page()
        try:
            _goto_login(page, base_url)
            page.wait_for_timeout(1800)

            def _blocked(pg):
                try:
                    d = _snapshot(pg)
                    txt = ((pg.title() or "") + " " + " ".join(d.get("headings", []))).lower()
                except Exception:
                    d, txt = {}, ""
                hit = any(k in txt for k in ("request is blocked", "service unavailable", "access denied",
                                             "forbidden", "captcha", "unusual traffic", "are you a human",
                                             "doesn't look right", "temporarily unavailable"))
                return (hit or _looks_like_bot_wall(pg)), d
            _b, _d0 = _blocked(page)
            if _b:
                raise VidaPayLoginError(
                    "b2bsoft's WAF is blocking our server's requests (\"" +
                    ((_d0.get("headings") or ["blocked"])[0]) +
                    "\") — this is the datacenter-IP wall (it lets a few logins through, then flags the IP). "
                    "The login itself works; the portal is refusing our egress IP. Put a RESIDENTIAL / allow-"
                    "listed proxy in the Egress proxy field and retry. Diagnostic: " + str(_d0))

            def _fill_id(sel, val):
                if not val:
                    return False
                el = page.query_selector(sel)
                if not el:
                    return False
                try:
                    el.click()
                    el.fill("")
                    el.type(str(val), delay=15)   # type() fires the keystroke events the progressive form listens for
                except Exception:
                    try:
                        el.fill(str(val))
                    except Exception:
                        return False
                return True

            # ── STEP 1: Access Code (Company ID). If User ID / Password aren't revealed yet, Continue. ──
            _fill_id("#companyId", access_code)
            uname = page.query_selector("#username")
            if not (uname and uname.is_visible()):
                b = page.query_selector("#btnSubmit")
                if b:
                    try:
                        b.click()
                    except Exception:
                        pass
                try:
                    page.wait_for_selector("#password", state="visible", timeout=15000)
                except Exception:
                    pass
                page.wait_for_timeout(1500)
                _wait_settle(page)

            # ── STEP 2: re-assert ALL THREE by id (the Company ID can clear on the re-render — that was the
            #   "rejected / all fields empty" symptom), then submit with #btnSubmit. ──
            _filled = {}
            if not page.query_selector("#password"):
                # Fall back to the generic heuristics if the ids aren't there (portal variant).
                login_fr, pw_el = _password_frame(page)
                if not pw_el:
                    diag = _snapshot(page)
                    if _looks_like_proxy_error(page):
                        raise VidaPayLoginError(
                            _proxy_error_message(page.url, proxy_url, _squid_reported_url(page))
                            + " Diagnostic: " + str(diag))
                    if _looks_like_bot_wall(page):
                        raise VidaPayLoginError(
                            "b2bsoft served an anti-automation page instead of the password form — reach it from a "
                            "residential / allow-listed IP (set the Egress proxy). Diagnostic: " + str(diag))
                    raise VidaPayLoginError(
                        "Reached b2bsoft but could not find the password field — send this diagnostic. Diagnostic: " + str(diag))
                ue = _find_input(login_fr, want=("user", "login", "email", "userid", "username"),
                                 avoid=("company", "access", "code", "pass"))
                if ue and user:
                    ue.fill(str(user))
                pw_el.fill(str(pw or ""))
                _click_submit(login_fr, ("log in", "login", "sign in", "signin", "submit")) or pw_el.press("Enter")
            else:
                _fill_id("#companyId", access_code)
                _fill_id("#username", user)
                _fill_id("#password", pw)
                page.wait_for_timeout(400)
                # Read back what's ACTUALLY in the fields at submit-time (the post-reject snapshot always
                # shows them empty, which hides whether the fill worked vs the creds were refused).
                def _val(sel):
                    try:
                        el = page.query_selector(sel)
                        return el.input_value() if el else None
                    except Exception:
                        return None
                _filled = {"companyId": _val("#companyId"), "username": _val("#username"),
                           "password_len": len(_val("#password") or "")}
                b = page.query_selector("#btnSubmit")
                if b:
                    try:
                        b.click()
                    except Exception:
                        pass
                else:
                    _click_submit(page, ("login", "log in", "sign in", "submit"))
            try:
                page.wait_for_load_state("networkidle", timeout=25000)
            except Exception:
                pass
            page.wait_for_timeout(3500)
            _wait_settle(page)

            def _b2b_error(pg):
                try:
                    return pg.evaluate(
                        """() => {
                            const sels=['.validation-summary-errors','.text-danger','.field-validation-error',
                                        '.alert','[role=alert]','.error','.errorMessage','#error','.login-error','.text-error'];
                            let m=[]; for (const s of sels) document.querySelectorAll(s).forEach(e=>{const t=(e.innerText||'').trim(); if(t) m.push(t)});
                            return m.filter((v,i,a)=>a.indexOf(v)===i).slice(0,8);
                        }""")
                except Exception:
                    return []
            # b2bsoft's SSO 2FA page (#TwoFactorCode / URL .../TwoFactor/...) trips the GENERIC bot-wall
            # heuristic — detect it EXPLICITLY and return needs_2fa (this was the false "anti-automation" error).
            if page.query_selector("#TwoFactorCode") or "twofactor" in (page.url or "").lower():
                d2 = _snapshot(page)
                try:
                    d2 = {**d2, "filled": _filled}
                except Exception:
                    pass
                return _twofa_result(page, ctx, {"filled": _filled})
            if _looks_like_proxy_error(page) and not _recover_from_proxy_error(page, dest_url=base_url):
                raise VidaPayLoginError(
                    _proxy_error_message(page.url, proxy_url, _squid_reported_url(page))
                    + " Diagnostic: " + str(_snapshot(page)))
            state = _classify(page)
            diag = _snapshot(page)
            try:
                diag = {**diag, "filled": _filled, "portal_error": _b2b_error(page)}
            except Exception:
                pass
            if state == "twofa":
                return _twofa_result(page, ctx, {"filled": _filled, "portal_error": _b2b_error(page)})
            if state == "authenticated":
                return {"status": "authenticated", "storage_state": capture_session_state(page, ctx), "diag": diag,
                        "screenshot_b64": _shot_b64(page)}
            if state == "botwall":
                raise VidaPayLoginError(
                    "b2bsoft served an anti-automation page after login — set a residential proxy. Diagnostic: " + str(diag))
            if state == "login":
                raise VidaPayLoginError(
                    "Login rejected — still on the login form. What was typed + the portal's own error are in the "
                    "diagnostic (filled / portal_error) — if 'filled' shows your values, the creds/Access-Code are "
                    "being refused; if empty, the form didn't accept the fill. Diagnostic: " + str(diag))
            return _twofa_result(page, ctx, {"_note": "post-login page not recognized as 2FA/app; send this diagnostic"})
        except Exception as e:   # attach the evidence to EVERY failure, incl. Playwright timeouts
            if getattr(e, "screenshot_b64", None) is None:
                try:
                    e.screenshot_b64 = _shot_b64(page)
                except Exception:
                    pass
            raise
        finally:
            browser.close()


# ── phase 2: submit the 2FA code ─────────────────────────────────────────────────────────────
def complete_2fa(url, pending_state, code, proxy_url=None):
    """Restore the mid-2FA session, submit the code, return the durable authenticated session."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise VidaPayLoginError("Playwright/Chromium is not available in the backend image.")
    if not pending_state:
        raise VidaPayAuthError("No pending login to verify — start the login again.")
    base_url = _norm_url(url, DEFAULT_URL)
    # Prefer the CODE-ENTRY url captured at begin_login (_twofa_result) — going straight there skips
    # the "New Sign In → Next" resend. Fall back to base_url if it wasn't captured.
    nav_url = pending_state.get("_2fa_url") if isinstance(pending_state, dict) else None
    with sync_playwright() as p:
        browser = _launch(p)
        ctx = _new_context(browser, storage_state=pending_state, proxy=_proxy_arg(proxy_url))
        page = ctx.new_page()
        try:
            page.goto(nav_url or base_url, timeout=60000, wait_until="domcontentloaded")
            _wait_settle(page)
            page.wait_for_timeout(2500)
            # The http-302→squid hop can appear here too; recover by re-goto (v2: https twin, then a direct
            # goto of the https base — GET-only, NEVER re-submits the form / re-sends a code). Cookies are
            # already set, so a completed login lands authenticated. Only error out if squid persists.
            if _looks_like_proxy_error(page) and not _recover_from_proxy_error(page, dest_url=base_url):
                raise VidaPayLoginError(
                    _proxy_error_message(page.url, proxy_url, _squid_reported_url(page))
                    + " Diagnostic: " + str(_snapshot(page)))
            if _classify(page) == "authenticated":
                return {"status": "authenticated", "storage_state": capture_session_state(page, ctx),
                        "diag": _snapshot(page), "screenshot_b64": _shot_b64(page)}
            # NEW-DEVICE FLOW: restoring the pending session re-lands on the "New Sign In → Next"
            # interstitial (id.vidapaycrm.com/TwoFactor/TwoFactorNewDeviceSignIn — only a Next button,
            # no code box), so click THROUGH it (Next / method chooser) to reach the code field before
            # searching. Same stateId → the already-sent code stays valid. (Owner diag 2026-07-15.)
            if not _code_field(page):
                try:
                    _advance_2fa(page)
                except Exception:
                    pass
            code_el, code_fr = None, None
            for fr in _frames(page):
                code_el = _find_input(fr, kinds=("text", "tel", "number", "password"),
                                      want=("code", "otp", "pin", "verif", "token", "authenticat",
                                            "2fa", "one-time", "onetime", "passcode"))
                if code_el:
                    code_fr = fr; break
            if not code_el:  # lone-field fallback
                for fr in _frames(page):
                    code_el = _find_input(fr, kinds=("text", "tel", "number"))
                    if code_el:
                        code_fr = fr; break
            if not code_el:
                raise VidaPayAuthError(
                    "Could not find the 2FA code field to enter the code. Diagnostic: " + str(_snapshot(page)))
            code_el.fill(str(code).strip())
            _tick_remember(code_fr)   # "Remember this device" → portal trusts this profile (typically 90 days)
            if not _click_submit(code_fr, ("verify", "submit", "continue", "confirm", "log in", "sign in")):
                try:
                    code_el.press("Enter")
                except Exception:
                    pass
            try:
                page.wait_for_load_state("networkidle", timeout=25000)
            except Exception:
                pass
            page.wait_for_timeout(3500)
            # Click through the post-code "Trust This Device" page (nickname + Next) before deciding.
            state = finalize_after_code(page)
            if state == "authenticated":
                return {"status": "authenticated", "storage_state": capture_session_state(page, ctx),
                        "diag": _snapshot(page), "screenshot_b64": _shot_b64(page)}
            if state == "twofa":
                raise VidaPayAuthError(
                    "2FA code was not accepted (still on the verification screen) — check the code and "
                    "try again; it may have expired, request a new one.")
            return {"status": "authenticated", "storage_state": capture_session_state(page, ctx),
                    "diag": {**_snapshot(page), "_note": "post-2FA page not definitively recognized"},
                    "screenshot_b64": _shot_b64(page)}
        except Exception as e:   # attach the evidence to EVERY failure, incl. Playwright timeouts
            if getattr(e, "screenshot_b64", None) is None:
                try:
                    e.screenshot_b64 = _shot_b64(page)
                except Exception:
                    pass
            raise
        finally:
            browser.close()


def complete_2fa_b2bsoft(url, pending_state, code, proxy_url=None):
    """b2bsoft SSO 2FA: fill #TwoFactorCode, TICK 'Remember this device for 90 days' (#IsTrustedDevice —
    so the session persists and future logins skip 2FA), click #verifyButton, then the OIDC flow
    redirects back to the portal (authenticated). Returns the durable session."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise VidaPayLoginError("Playwright/Chromium is not available in the backend image.")
    if not pending_state:
        raise VidaPayAuthError("No pending login to verify — start the login again.")
    base_url = _norm_url(url, B2BSOFT_URL)
    nav_url = pending_state.get("_2fa_url") if isinstance(pending_state, dict) else None
    with sync_playwright() as p:
        browser = _launch(p)
        ctx = _new_context(browser, storage_state=pending_state, proxy=_proxy_arg(proxy_url))
        page = ctx.new_page()
        try:
            # Restoring the mid-2FA session + hitting the portal resumes the pending 2FA challenge.
            # Prefer the captured code-entry url (no resend); else the portal home.
            page.goto(nav_url or base_url, timeout=60000, wait_until="domcontentloaded")
            _wait_settle(page)
            page.wait_for_timeout(2500)
            # The http-302→squid hop can appear here too; recover by re-goto (v2: https twin, then a direct
            # goto of the https base — GET-only, NEVER re-submits the form / re-sends a code). Cookies are
            # already set, so a completed login lands authenticated. Only error out if squid persists.
            if _looks_like_proxy_error(page) and not _recover_from_proxy_error(page, dest_url=base_url):
                raise VidaPayLoginError(
                    _proxy_error_message(page.url, proxy_url, _squid_reported_url(page))
                    + " Diagnostic: " + str(_snapshot(page)))
            on_2fa = bool(page.query_selector("#TwoFactorCode")) or "twofactor" in (page.url or "").lower()
            if not on_2fa and _classify(page) == "authenticated":
                return {"status": "authenticated", "storage_state": capture_session_state(page, ctx), "diag": _snapshot(page),
                        "screenshot_b64": _shot_b64(page)}
            # New-device flow can re-land on a "New Sign In → Next" interstitial before the code box.
            if not (page.query_selector("#TwoFactorCode") or _code_field(page)):
                try:
                    _advance_2fa(page)
                except Exception:
                    pass
            code_el = page.query_selector("#TwoFactorCode")
            if not code_el:
                for fr in _frames(page):
                    code_el = _find_input(fr, kinds=("text", "tel", "number"),
                                          want=("code", "otp", "pin", "verif", "token", "2fa", "twofactor"))
                    if code_el:
                        break
            if not code_el:
                raise VidaPayAuthError(
                    "Could not find the 2FA code field to enter the code. Diagnostic: " + str(_snapshot(page)))
            code_el.fill(str(code).strip())
            # Remember this device for 90 days → the session stays valid + skips 2FA next time.
            trust = page.query_selector("#IsTrustedDevice")
            if trust:
                try:
                    if not trust.is_checked():
                        trust.check()
                except Exception:
                    try:
                        trust.click()
                    except Exception:
                        pass
            vb = page.query_selector("#verifyButton")
            if vb:
                try:
                    vb.click()
                except Exception:
                    pass
            elif not _click_submit(page, ("verify", "submit", "confirm", "continue")):
                try:
                    code_el.press("Enter")
                except Exception:
                    pass
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass
            page.wait_for_timeout(4000)
            _wait_settle(page)
            if page.query_selector("#TwoFactorCode") or "twofactor" in (page.url or "").lower():
                raise VidaPayAuthError(
                    "2FA code was not accepted (still on the verification screen) — check the code and try "
                    "again; it may have expired, click Resend for a new one.")
            # After Verify the OIDC hybrid flow chains: SSO 2FA → /connect/authorize callback → (form_post)
            # → wsreports/signin-oidc → app. WAIT for it to leave the SSO domain and land on the app (don't
            # navigate away mid-chain — that restarts auth and loses the token).
            try:
                page.wait_for_url(lambda uu: "sso.b2bsoft.com" not in (uu or "").lower(), timeout=30000)
            except Exception:
                pass
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            page.wait_for_timeout(3500)
            finalize_after_code(page)   # click through a post-code "Trust This Device" page if shown

            def _storage_diag(pg):
                try:
                    return pg.evaluate(
                        """() => ({url: location.href, host: location.host,
                                   ls: Object.keys(localStorage||{}).slice(0,30),
                                   ss: Object.keys(sessionStorage||{}).slice(0,30)})""")
                except Exception:
                    return {}
            sd = _storage_diag(page)
            try:
                ck = ctx.cookies()
                cookie_hosts = sorted({(c.get("domain") or "") for c in ck})
            except Exception:
                cookie_hosts = []
            u = (page.url or "").lower()
            if "sso.b2bsoft.com" in u or page.query_selector("#TwoFactorCode") or page.query_selector("#companyId"):
                raise VidaPayAuthError(
                    "The code was accepted but sign-in didn't complete — still on the SSO screen. "
                    "Diagnostic: " + str({"final_url": page.url, "storage": sd, "cookie_hosts": cookie_hosts}))
            # IMPORTANT: capture BOTH the storage_state AND a snapshot of sessionStorage (Playwright's
            # storage_state does NOT save sessionStorage — many OIDC SPAs keep the token there, which is the
            # classic "session won't persist" cause). We stash sessionStorage so the sweep can re-inject it.
            ss_dump = {}
            try:
                ss_dump = page.evaluate("() => { const o={}; for (let i=0;i<sessionStorage.length;i++){const k=sessionStorage.key(i); o[k]=sessionStorage.getItem(k);} return o; }") or {}
            except Exception:
                pass
            st = capture_session_state(page, ctx)
            if ss_dump:
                st["_sessionStorage"] = {"origin": sd.get("url") or base_url, "items": ss_dump}
            return {"status": "authenticated", "storage_state": st,
                    "diag": {**_snapshot(page), "final_url": page.url, "storage": sd,
                             "cookie_hosts": cookie_hosts, "_note": "post-2FA landed on portal (b2bsoft)"},
                    "screenshot_b64": _shot_b64(page)}
        except Exception as e:   # attach the evidence to EVERY failure, incl. Playwright timeouts
            if getattr(e, "screenshot_b64", None) is None:
                try:
                    e.screenshot_b64 = _shot_b64(page)
                except Exception:
                    pass
            raise
        finally:
            browser.close()


# ── report-pull engine (config-driven; select report → fill → Submit → Export → parse → ingest) ──
# The report→table→column MAPPING is NOT hard-coded here: it comes from commcalc.report_pull_map via
# report_pull.resolve_report_specs (org override > house default > Python default). This driver only
# knows how to DRIVE the ASP.NET Reports page for whatever specs it's handed, and returns a per-report
# summary + a DOM diagnostic on any report whose page didn't match (so the un-screenshotted SIM/PR
# reports self-calibrate on the first live run).
# Option text that identifies THE report <select> (vs a store/period dropdown on the same page).
_REPORT_OPTION_HINTS = ("commission details", "daily tx", "fulfillment", "sim assignment",
                        "activation details", "activation sim", "marketplace", "pr activation")
# Nav labels tried, in order, to reach the Reports page. Config-free heuristics: a tenant that needs a
# different word calibrates the report display_name at /commcalc/report-mappings, and the probe below
# tells them exactly which links the portal actually offers.
_REPORT_NAV_LABELS = ("reports", "my reports", "reporting", "billing manager", "billing",
                      "commission", "report")
# REQUEST-VOLUME BUDGET for the Reports navigation (2026-07-27 "too many requests" incident). Each
# CLICK below is a full page load the portal counts against us; seven labels once meant seven of them
# fired with nothing in between. Three honest guesses, spaced, is the whole budget — a fourth has never
# found the menu when the first three did not.
_MAX_NAV_CLICKS = 3
_NAV_CLICK_DELAY_MS = 1200


# ── report-name matching, invisible-character-proof (owner report 2026-07-28) ────────────────────
# THE BUG THIS KILLS. The pull failed with a SELF-CONTRADICTING message:
#   "Activation SIM Assignment Report is not one of the reports this portal login offers
#    - it offers: -- SELECT --, Activation SIM Assignment Report, ..."
# The wanted name is printed INSIDE the list of offered names. Exactly two mechanisms produce that,
# and the old comparison (`(o.inner_text() or "").strip().lower() == target`, plus a loose substring
# fallback) could neither tell them apart nor SHOW either of them:
#   (1) an INVISIBLE character difference - a NO-BREAK SPACE (U+00A0), a zero-width space (U+200B),
#       a soft hyphen, or an en/em dash inside the portal's own option text. Every one of those
#       renders identically to a plain space/hyphen, so the printed list looks byte-for-byte like the
#       configured name while the bytes differ. `str.strip()` removes a LEADING/TRAILING nbsp but
#       nothing removed an INTERNAL one.
#   (2) the printed list was captured at a DIFFERENT PAGE STATE than the state the selection was
#       attempted in (once, before any report ran) - so it described a dropdown that was no longer on
#       screen. _select_report_detail now re-reads the LIVE options at attempt time and reports a
#       missing dropdown as its own outcome instead of blaming the report name.
# Both sides are normalised before comparison, and every failure carries repr() + codepoint
# forensics, so an invisible-character mismatch can never again read as a contradiction.
_CHAR_FIXES = {
    # exotic spaces -> plain space (NFKC already folds most of these; belt AND braces)
    "\u00a0": " ", "\u1680": " ", "\u2000": " ", "\u2001": " ", "\u2002": " ", "\u2003": " ",
    "\u2004": " ", "\u2005": " ", "\u2006": " ", "\u2007": " ", "\u2008": " ", "\u2009": " ",
    "\u200a": " ", "\u202f": " ", "\u205f": " ", "\u3000": " ",
    # zero-width / invisible formatting -> gone (NFKC does NOT remove these)
    "\u200b": "", "\u200c": "", "\u200d": "", "\u2060": "", "\ufeff": "", "\u00ad": "",
    # every dash variant -> ASCII hyphen ("MA \u2013 Commission Details" vs "MA - Commission Details")
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-",
    "\u2212": "-", "\uff0d": "-",
    # smart quotes -> straight
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201c": '"', "\u201d": '"',
}


def _norm_label(s):
    """NFKC-normalise, fold every invisible/dash/quote variant, collapse whitespace runs, casefold.
    PURE. This is the ONLY comparison basis for report names - both sides always go through it."""
    try:
        import unicodedata
        t = unicodedata.normalize("NFKC", str(s if s is not None else ""))
    except Exception:
        t = str(s if s is not None else "")
    for ch, rep in _CHAR_FIXES.items():
        if ch in t:
            t = t.replace(ch, rep)
    t = " ".join(t.split())          # collapses doubled/tab/newline whitespace too
    return t.strip().casefold()


def _squash_label(s):
    """_norm_label with every non-alphanumeric character dropped - the punctuation-insensitive tier
    ("MA - Commission Details" == "MA Commission Details" == "MA_Commission_Details"). PURE."""
    return "".join(ch for ch in _norm_label(s) if ch.isalnum())


def _is_placeholder_option(s):
    """True for the dropdown's own '-- SELECT --' / 'Choose a report' row, which must never be
    selected NOR offered as the nearest candidate. PURE."""
    q = _squash_label(s)
    return (not q) or q.startswith("select") or q.startswith("choose") or q.startswith("pleaseselect")


def label_forensics(s):
    """repr() + a codepoint listing of every non-ASCII/invisible character in `s`. This is what makes
    an invisible mismatch VISIBLE in the operator's diagnostic. PURE, credential-free (report names
    only)."""
    t = str(s if s is not None else "")
    odd = []
    try:
        import unicodedata
        for ch in t:
            o = ord(ch)
            if o < 32 or o > 126:
                odd.append("U+%04X %s" % (o, unicodedata.name(ch, "?")))
    except Exception:
        pass
    return {"text": t[:120], "repr": repr(t)[:200], "odd_chars": odd[:10],
            "normalized": _norm_label(t)[:120]}


def nearest_label(wanted, options):
    """(closest option, 0..1 similarity) over NORMALISED text - for the failure message. PURE."""
    try:
        import difflib
    except Exception:
        return (None, 0.0)
    w = _norm_label(wanted)
    best, score = None, 0.0
    for o in (options or []):
        if _is_placeholder_option(o):
            continue
        try:
            r = difflib.SequenceMatcher(None, w, _norm_label(o)).ratio()
        except Exception:
            continue
        if r > score:
            best, score = o, r
    return (best, round(score, 3))


def match_report_option(wanted, options, aliases=()):
    """PURE. Match a configured report display name against the portal's own option list.

    Tiered, exact-first, so a deliberate name always wins over a fuzzy one:
      1. `exact`       - byte equality after a plain .strip() (the pre-existing behaviour)
      2. `normalized`  - equality after _norm_label (kills nbsp / zero-width / dash / case / spacing)
      3. `punctuation` - equality after _squash_label (kills every separator difference)
      4. `contains`    - normalised containment, ONLY when exactly one option qualifies and the name
                         is long enough to be meaningful (an ambiguous containment is a FAILURE, not
                         a coin flip - pulling the WRONG report into a money table is worse than
                         pulling none).
    `aliases` (config: param_spec.name_aliases) are tried after the primary name, same ladder.
    Returns (index, tier) on success, or (-1, reason) with reason in
    no_options|no_name|ambiguous|not_listed."""
    opts = list(options or [])
    if not opts:
        return -1, "no_options"
    wants = [w for w in ([wanted] + list(aliases or [])) if str(w if w is not None else "").strip()]
    if not wants:
        return -1, "no_name"
    cand = [i for i, o in enumerate(opts) if not _is_placeholder_option(o)]
    if not cand:
        return -1, "no_options"
    for w in wants:                                                   # 1. exact
        ws = str(w).strip()
        for i in cand:
            if str(opts[i] if opts[i] is not None else "").strip() == ws:
                return i, "exact"
    for w in wants:                                                   # 2. normalised
        nw = _norm_label(w)
        if not nw:
            continue
        for i in cand:
            if _norm_label(opts[i]) == nw:
                return i, "normalized"
    for w in wants:                                                   # 3. punctuation-insensitive
        sw = _squash_label(w)
        if not sw:
            continue
        for i in cand:
            if _squash_label(opts[i]) == sw:
                return i, "punctuation"
    ambiguous = False
    for w in wants:                                                   # 4. guarded containment
        nw = _norm_label(w)
        if len(nw) < 6:            # a short name must not swallow a whole menu
            continue
        hits = [i for i in cand
                if nw in _norm_label(opts[i]) or (_norm_label(opts[i]) and _norm_label(opts[i]) in nw)]
        if len(hits) == 1:
            return hits[0], "contains"
        if len(hits) > 1:
            ambiguous = True
    return -1, ("ambiguous" if ambiguous else "not_listed")


def _spec_aliases(spec):
    """Alternate display names for one report, from CONFIG (param_spec.name_aliases, or spec.aliases).
    RULE TWO: a tenant whose portal spells a report differently adds an alias on Report mapping - no
    code change, no per-carrier branch. Absent key => (). PURE."""
    try:
        ps = (spec or {}).get("param_spec") or {}
        al = ps.get("name_aliases") or (spec or {}).get("aliases") or []
        if isinstance(al, str):
            al = [al]
        return [str(a) for a in al if str(a or "").strip()][:8]
    except Exception:
        return []


def _report_select(frame):
    """The visible <select> in `frame` that holds the report list, or None."""
    try:
        cands = frame.query_selector_all("select")
    except Exception:
        return None
    for s in cands:
        try:
            if not s.is_visible():
                continue
            opts = " ".join([(o.inner_text() or "") for o in s.query_selector_all("option")]).lower()
            hay = ((s.get_attribute("name") or "") + (s.get_attribute("id") or "")).lower()
            if "report" in hay or any(k in opts for k in _REPORT_OPTION_HINTS):
                return s
        except Exception:
            continue
    return None


def _report_select_frame(page):
    """The frame holding the report <select>, or None when this page isn't the Reports page."""
    for fr in _frames(page):
        try:
            if _report_select(fr) is not None:
                return fr
        except Exception:
            continue
    return None


def report_options(page):
    """The report names the portal's OWN dropdown offers, in portal order. [] when not on the Reports
    page. This is the calibration vocabulary: a configured display_name that is not in this list can
    never be selected, and until 2026-07-27 nothing ever showed it to the operator."""
    fr = _report_select_frame(page)
    if fr is None:
        return []
    s = _report_select(fr)
    if s is None:
        return []
    out = []
    try:
        for o in s.query_selector_all("option"):
            t = (o.inner_text() or "").strip()
            if t:
                out.append(t[:120])
    except Exception:
        return out
    return out[:60]


def reports_probe(page):
    """A credential-free description of what the AUTHENTICATED portal actually offers — nav links,
    every <select> with its option texts, the report-ish buttons and the date fields — so the operator
    can calibrate report names/params from the UI instead of the module guessing blind. The VidaPay twin
    of run_b2bsoft_sweep's `report_probe`. Never raises; no value of any input is read (only
    name/id/placeholder), so no credential can appear in it."""
    out = {"url": "", "title": "", "frames": 0, "nav_links": [], "selects": [],
           "buttons": [], "date_fields": []}
    try:
        out["url"] = (page.url or "")[:200]
    except Exception:
        pass
    try:
        out["title"] = (page.title() or "")[:120]
    except Exception:
        pass
    frames = _frames(page)
    out["frames"] = len(frames)
    for fr in frames:
        try:
            p = fr.evaluate(
                """() => ({
                    links: Array.from(document.querySelectorAll('a')).map(a=>({t:(a.innerText||'').trim().slice(0,60), href:(a.getAttribute('href')||'').slice(0,120)}))
                            .filter(x=>x.t).slice(0,60),
                    selects: Array.from(document.querySelectorAll('select')).map(s=>({
                            name:s.name||'', id:s.id||'',
                            opts:Array.from(s.options).slice(0,60).map(o=>(o.text||'').trim()).filter(Boolean)})).slice(0,12),
                    buttons: Array.from(document.querySelectorAll('button,input[type=button],input[type=submit]'))
                            .map(b=>({t:((b.innerText||b.value||'').trim()).slice(0,40), id:b.id||'', name:b.name||''}))
                            .filter(x=>x.t).slice(0,40),
                    dates: Array.from(document.querySelectorAll('input')).map(i=>({id:i.id||'',name:i.name||'',type:i.type||'',ph:i.placeholder||''}))
                            .filter(x=>/date|from|to|start|end|period/i.test(x.id+x.name+x.ph)).slice(0,20),
                })""") or {}
        except Exception:
            continue
        for l in (p.get("links") or []):
            if len(out["nav_links"]) < 60:
                out["nav_links"].append(l)
        for s in (p.get("selects") or []):
            if len(out["selects"]) < 12:
                out["selects"].append(s)
        for b in (p.get("buttons") or []):
            if len(out["buttons"]) < 40:
                out["buttons"].append(b)
        for d in (p.get("dates") or []):
            if len(out["date_fields"]) < 20:
                out["date_fields"].append(d)
    out["report_options"] = report_options(page)
    return out


def _open_reports_page(page, markers=None):
    """Find the frame holding the Report <select>, navigating there if we aren't on it yet.

    BOUNDED (2026-07-27 rate-limit incident): at most `_MAX_NAV_CLICKS` navigation CLICKS, spaced by
    `_NAV_CLICK_DELAY_MS`, with the rate-limit gate re-checked between them. Labels that match nothing
    cost no request and are not counted.

    Returns the frame, or **None** when the Reports page could not be reached at all. Returning None
    (instead of the old `or page` fallback) is deliberate: with the fallback, an unreachable Reports
    page was indistinguishable from a mis-named report, and every report reported
    "calibrate the display_name" — pointing the operator at the wrong fix.
    Navigation now goes through _click_nav, which can click a plain `<a href>` menu link (the old
    _click_submit could not — that was the 2026-07-27 "nothing imports" root cause)."""
    fr = _report_select_frame(page)
    if fr:
        return fr
    clicks = 0
    for want in _REPORT_NAV_LABELS:
        if clicks >= _MAX_NAV_CLICKS:
            # REQUEST-VOLUME CAP (2026-07-27 incident). Seven labels used to mean up to SEVEN full page
            # navigations fired back-to-back at a portal that had already declined to show us the
            # Reports menu. If the first few honest guesses miss, more guesses are not going to find it
            # — they only add load. Stop and let the caller report an honest "Reports page unreachable"
            # with the DOM snapshot, which is the actionable outcome anyway.
            break
        clicked = False
        for f in _frames(page):
            try:
                if _click_nav(f, (want,)):
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            continue                      # nothing clicked ⇒ no request was made ⇒ costs nothing
        clicks += 1
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        try:
            page.wait_for_timeout(2000)
        except Exception:
            pass
        _wait_settle(page)
        fr = _report_select_frame(page)
        if fr:
            return fr
        # A portal that started throttling MID-NAVIGATION must not eat the remaining clicks: raise now
        # so the caller stamps the cooldown instead of reporting "Reports page unreachable".
        _raise_if_rate_limited(page, markers=markers, where="The portal")
        # PACE the next attempt. Nothing above this line waits between two navigation CLICKS, and an
        # unspaced burst is what a rate limiter counts hardest.
        if clicks < _MAX_NAV_CLICKS:
            try:
                page.wait_for_timeout(_NAV_CLICK_DELAY_MS)
            except Exception:
                pass
    return _report_select_frame(page)


def _visible_safe(el):
    try:
        return bool(el.is_visible())
    except Exception:
        return False


def _select_report_detail(frame, display_name, aliases=()):
    """Select `display_name` in the report <select> and wait for the ASP.NET postback that renders
    that report's parameter fields. Returns an HONEST dict - never a bare False:

        {ok, tier, matched, options, reason, match_debug}

    `options` are read from the LIVE DOM **at the moment of the attempt** (not the list captured once
    before the pull started). That distinction is the whole point: printing a stale capture next to a
    live failure is what produced the self-contradicting 2026-07-28 message.

    reason (when ok is False):
      report_select_missing - there is no report dropdown on the page RIGHT NOW. A page-state failure
                              (e.g. the previous report's results replaced the form), NOT a wrong
                              name. Reporting it as "wrong name" sent the operator to Report mapping
                              to fix something that was already correct.
      report_not_listed     - the dropdown is there and the name genuinely is not among its options
                              (compared normalised, so invisible characters are ruled out first).
      ambiguous             - the name matched several options by containment; refusing is correct."""
    out = {"ok": False, "wanted": display_name, "options": [], "tier": None,
           "matched": None, "reason": None, "match_debug": None}
    try:
        selects = [s for s in frame.query_selector_all("select") if _visible_safe(s)]
    except Exception:
        selects = []
    primary = None
    try:
        primary = _report_select(frame)
    except Exception:
        primary = None
    ordered = ([primary] if primary is not None else []) + [s for s in selects if s is not primary]
    if not ordered:
        out["reason"] = "report_select_missing"
        return out
    seen, first_reason = [], None
    for s in ordered:
        try:
            opt_els = list(s.query_selector_all("option"))
            texts = [(o.inner_text() or "") for o in opt_els]
        except Exception:
            continue
        if not texts:
            continue
        if not seen:
            seen = [t.strip()[:120] for t in texts if t.strip()]
        idx, tier = match_report_option(display_name, texts, aliases)
        if idx < 0:
            first_reason = first_reason or tier
            continue
        o = opt_els[idx]
        try:
            val = o.get_attribute("value")
            if val is not None:
                s.select_option(value=val)
            else:
                s.select_option(label=(texts[idx] or "").strip())
        except Exception:
            first_reason = first_reason or "select_failed"
            continue
        try:
            frame.page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        try:
            frame.page.wait_for_timeout(1500)
        except Exception:
            pass
        out.update(ok=True, tier=tier, matched=(texts[idx] or "").strip()[:120],
                   options=(seen or [t.strip()[:120] for t in texts if t.strip()])[:60], reason=None)
        if tier != "exact":
            # The name only matched AFTER normalisation => the portal's text carries characters the
            # operator cannot see. Record it, so "why is my name different?" is never a mystery.
            out["match_debug"] = {"tier": tier, "wanted": label_forensics(display_name),
                                  "matched": label_forensics(texts[idx])}
        return out
    near, score = nearest_label(display_name, seen)
    out["options"] = seen[:60]
    out["reason"] = first_reason if first_reason in ("ambiguous", "no_name") else "report_not_listed"
    if first_reason == "no_options" or not seen:
        out["reason"] = "report_select_missing"
    out["match_debug"] = {"compared": "NFKC + invisible-char fold + whitespace collapse + casefold",
                          "wanted": label_forensics(display_name),
                          "nearest_offered": label_forensics(near) if near else None,
                          "similarity": score,
                          "aliases_tried": list(aliases or [])}
    return out


def _select_report(frame, display_name):
    """Back-compatible boolean wrapper over _select_report_detail (callers/proofs that only need
    'did it select?')."""
    try:
        return bool(_select_report_detail(frame, display_name).get("ok"))
    except Exception:
        return False


# ── param-field driving ─────────────────────────────────────────────────────────────────────────
# Three defects fixed here (owner report 2026-07-28, "the reports ran but returned nothing"):
#  (i)   NO CHANGE EVENT. The old code did `el.click(); el.fill(""); el.type(val, delay=8)`.
#        Playwright's `type()` dispatches key + `input` events but NEVER `change`, and `change` on a
#        text input otherwise fires only on blur - which never happened, because the next action was
#        a click on Submit somewhere else in the DOM... on an ASP.NET page whose date box is a
#        jQuery/AJAX datepicker that COMMITS its value on `change`. The server then ran the report
#        with its DEFAULT range (typically today) instead of the requested month => "ran, no rows".
#        Values are now committed with input+change+jQuery change+blur, and READ BACK to confirm.
#  (ii)  START AND END COLLIDING. `_find_input(want=toks)` matches ANY token, so a field named
#        "End Date" (toks: end, date) matched the *StartDate* input - "date" is in its name and it
#        comes first in the DOM. Both boundaries were written into the SAME box, collapsing the
#        window to a single day. Fields are now scored, the opposite role is disqualified, and an
#        element already used by another field is never reused.
#  (iii) HIDDEN FIELDS UNREACHABLE. Static params (SessionId) are usually `input[type=hidden]`, but
#        the finder required `is_visible()` - so `kinds=(... ,"hidden")` could never match anything
#        and SessionId was never sent. Hidden elements are now allowed and set via evaluate().
_ROLE_WORDS = {"start": ("start", "from", "begin", "beginning", "startdate", "fromdate"),
               "end": ("end", "to", "thru", "through", "till", "until", "enddate", "todate")}


def _hay_words(hay):
    """Split a name/id/placeholder soup into lowercase words, breaking camelCase too
    ('txtStartDate_ctl00' -> ['txt','start','date','ctl','00']). PURE."""
    import re
    return [w.lower() for w in re.findall(r"[A-Za-z][a-z]*|[A-Z]+(?![a-z])|\d+", str(hay or ""))]


def _el_key(el):
    try:
        return ((el.get_attribute("name") or ""), (el.get_attribute("id") or ""))
    except Exception:
        return ("", "")


def _find_param_input(scope, kinds, name_toks, role=None, used=(), allow_hidden=False):
    """The best input in `scope` for ONE param field. Scored, not first-match: an all-token hit beats
    a single-token hit, the field's ROLE (start vs end) beats both, and a candidate carrying the
    OPPOSITE role's word is disqualified outright so the end date can never land in the start box.
    `used` holds the (name,id) keys already consumed by earlier fields. PURE-ish (DOM reads only)."""
    try:
        handles = scope.query_selector_all("input, textarea")
    except Exception:
        return None
    opp = _ROLE_WORDS["end"] if role == "start" else (_ROLE_WORDS["start"] if role == "end" else ())
    mine = _ROLE_WORDS.get(role or "", ())
    best, best_score = None, 0
    for h in handles:
        try:
            vis = _visible_safe(h)
            if not vis and not allow_hidden:
                continue
            typ = (h.get_attribute("type") or "text").lower()
            if kinds and typ not in kinds:
                continue
            if _el_key(h) in used:
                continue
            hay = " ".join(filter(None, [
                h.get_attribute("name"), h.get_attribute("id"), h.get_attribute("placeholder"),
                h.get_attribute("aria-label"), h.get_attribute("autocomplete"),
                h.get_attribute("title")])).lower()
            words = _hay_words(hay)
            has_mine = any(w in words for w in mine)
            if opp and any(w in words for w in opp) and not has_mine:
                continue                        # this is the OTHER end of the range - never take it
            score = 0
            if name_toks and all(t in hay for t in name_toks):
                score += 5
            elif name_toks and any(t in hay for t in name_toks):
                score += 1
            if has_mine:
                score += 4
            if "date" in words:
                score += 1
            if vis:
                score += 1
            if score > best_score:
                best, best_score = h, score
        except Exception:
            continue
    return best if best_score > 0 else None


def _commit_value(el, page=None):
    """Fire the events an ASP.NET / jQuery-datepicker field needs to actually COMMIT a typed value.
    This is the difference between the portal running the month we asked for and the month it
    defaults to. No request is made here - dispatching events is local to the page."""
    try:
        el.evaluate("""e => {
            const fire = n => { try { e.dispatchEvent(new Event(n, {bubbles:true})); } catch(_) {} };
            fire('input'); fire('change'); fire('keyup');
            try { if (window.jQuery) window.jQuery(e).trigger('change').trigger('blur'); } catch(_) {}
            try { if (e.blur) e.blur(); } catch(_) {}
            fire('blur');
        }""")
    except Exception:
        pass
    if page is not None:
        try:
            page.wait_for_timeout(250)      # let an AutoPostBack settle; TIME, not a new request
        except Exception:
            pass


def _read_back(el):
    """The element's CURRENT value, so we can prove what was actually submitted. None when unknown
    (e.g. the element was detached by a postback)."""
    for get in (lambda: el.input_value(),
                lambda: el.evaluate("e => e.value"),
                lambda: el.get_attribute("value")):
        try:
            v = get()
            if v is not None:
                return str(v)
        except Exception:
            continue
    return None


def _fill_param_fields(frame, fields, win_start, win_end, source_row):
    """Fill each param field per its spec and CONFIRM it took. Returns a per-field report:
        [{name, kind, role, found, visible, value|value_len, committed, readback_ok}, ...]
    Date/select values are echoed (a date is not a secret); static values NEVER are - only their
    length - because they carry the account/session id."""
    from app.modules.commcalc import report_pull as rp
    out, used = [], []
    page = getattr(frame, "page", None)
    for f in (fields or []):
        name = f.get("name") or ""
        kind = f.get("kind")
        role = f.get("role")
        toks = [w for w in name.lower().replace("_", " ").split() if len(w) > 1]
        rec = {"name": name, "kind": kind, "role": role, "found": False}
        try:
            if kind == "date":
                fmt = f.get("format") or "%m/%d/%Y"
                val = (win_start if role == "start" else win_end).strftime(fmt)
                rec["value"] = val
                el = (_find_param_input(frame, ("text", "date", "datetime-local"), toks, role, used)
                      or _find_param_input(frame, ("text",), ("date",), role, used))
                if el is None:
                    out.append(rec)
                    continue
                if any(_el_key(el)):        # anonymous inputs share the ("","") key - never dedupe on it
                    used.append(_el_key(el))
                rec.update(found=True, visible=_visible_safe(el))
                try:
                    el.click()
                except Exception:
                    pass
                try:
                    el.fill(val)                 # fill() replaces + fires input/change
                except Exception:
                    try:
                        el.evaluate("(e,v)=>{e.value=v;}", val)
                    except Exception:
                        pass
                _commit_value(el, page)
                back = _read_back(el)
                rec["readback_ok"] = (back is not None and back.strip() == val.strip())
                rec["committed"] = True
            elif kind == "select":
                lit = (f.get("literal") or "").strip()
                rec["value"] = lit
                for s in (frame.query_selector_all("select") or []):
                    if not _visible_safe(s):
                        continue
                    hay = ((s.get_attribute("name") or "") + (s.get_attribute("id") or "")).lower()
                    if not (any(t in hay for t in toks) or lit):
                        continue
                    picked = False
                    for o in s.query_selector_all("option"):
                        if _norm_label(o.inner_text()) == _norm_label(lit):
                            v = o.get_attribute("value")
                            s.select_option(value=v) if v is not None else s.select_option(label=o.inner_text())
                            picked = True
                            break
                    if picked:
                        rec.update(found=True, visible=True, committed=True)
                        _commit_value(s, page)
                        break
            else:  # static (Account_ID / SessionId - value never echoed)
                val = rp.resolve_static(f.get("source"), source_row)
                rec["value_len"] = len(val or "")
                if val == "" and f.get("optional"):
                    rec["skipped"] = "optional and blank"
                    out.append(rec)
                    continue
                el = (_find_param_input(frame, ("text", "number", "hidden"), toks, None, used,
                                        allow_hidden=True)
                      or _find_param_input(frame, ("text", "number", "hidden"),
                                           ("account", "session"), None, used, allow_hidden=True))
                if el is None:
                    out.append(rec)
                    continue
                if any(_el_key(el)):
                    used.append(_el_key(el))
                vis = _visible_safe(el)
                rec.update(found=True, visible=vis)
                try:
                    if vis:
                        el.fill(str(val))
                    else:
                        el.evaluate("(e,v)=>{e.value=v;}", str(val))
                except Exception:
                    try:
                        el.evaluate("(e,v)=>{e.value=v;}", str(val))
                    except Exception:
                        pass
                _commit_value(el, page)
                back = _read_back(el)
                rec["readback_ok"] = (back is not None and back.strip() == str(val).strip())
                rec["committed"] = True
        except Exception as e:
            rec["error"] = str(e)[:120]
        out.append(rec)
    return out


# ── running a report + waiting for its results ──────────────────────────────────────────────────
# Run-control labels. Matched WORD-wise for short tokens: "go" as a substring lives inside "logout",
# and _click_submit has no sign-out guard - clicking that would end the session mid-pull.
_RUN_LABELS = ("submit", "run report", "run", "view report", "view", "generate report", "generate",
               "search", "display", "get report", "show", "go")
_RUN_LABELS_LINK = ("submit", "run report", "run", "view report", "generate report", "generate",
                    "search", "get report")     # anchors: stricter (never a bare "go"/"view")
_RUN_NEVER = _NAV_NEVER + ("export", "reset", "clear", "cancel", "back", "print", "help", "close",
                           "home", "new search")
# An explicit "the portal says there is nothing" marker. Seeing one of these is a REAL empty result;
# seeing NOTHING is a scrape failure. Before 2026-07-28 both were reported as "returned no rows".
_EMPTY_MARKERS = ("no records found", "no record found", "no records", "no data found", "no data",
                  "no rows", "0 records", "no results", "no result found", "nothing to display",
                  "there are no records", "no matching records", "no transactions",
                  "no report data", "returned no data", "no items to display")
# Bounded wait for the results region. TIME, never extra requests: we poll the DOM we already have.
_RESULTS_WAIT_S = 90          # default budget per window; param_spec.results_wait_s overrides
_RESULTS_POLL_MS = 1500
_EXPORT_GRACE_S = 20          # once data rows are visible, how long to keep looking for the export
_EXPORT_SETTLE_S = 30         # an export control that pre-dates the run: wait this long for the
                              # results to actually move before clicking it (stale-download guard)


def _label_words(el):
    try:
        raw = ((el.get_attribute("value") or "") + " " + (el.inner_text() or "")).strip()
    except Exception:
        raw = ""
    if not raw:
        try:
            raw = (el.get_attribute("title") or el.get_attribute("aria-label") or "")
        except Exception:
            raw = ""
    return _norm_label(raw)


def _label_matches(label, token):
    """Match a run-control label against one token. WORD-wise for short single words ("go", "run",
    "view", "show"), substring for long or multi-word ones ("submit", "view report"). Substring
    matching on a short word is how "go" finds "logout" and "view" finds "overview" - i.e. how a
    scraper clicks the control that ends its own session. PURE."""
    if not label or not token:
        return False
    if " " in token or len(token) >= 5:
        return token in label
    return token in label.split()


def _click_run(frame, page=None):
    """Click the report's Run/Submit control. Returns the label clicked, or None.

    Buttons/inputs FIRST, then plain `<a>` LINKS - the SAME gap that broke the Reports navigation on
    2026-07-27: this portal builds controls as ASP.NET LinkButtons (`<a href="javascript:__doPostBack
    (...)">Submit</a>`), and `_click_submit`'s selector (button, input[type=submit|button],
    a[role=button]) matches none of those. A report that is never actually submitted looks exactly
    like a report that returned no rows - which is what the operator was told."""
    try:
        btns = frame.query_selector_all("button, input[type=submit], input[type=button], a[role=button]")
    except Exception:
        btns = []
    try:
        links = frame.query_selector_all("a[href], a")
    except Exception:
        links = []
    for cands, toks in ((btns, _RUN_LABELS), (links, _RUN_LABELS_LINK)):
        for c in cands:
            try:
                if not _visible_safe(c):
                    continue
                label = _label_words(c)
                if not label or any(bad in label for bad in _RUN_NEVER):
                    continue
                if any(_label_matches(label, t) for t in toks):
                    c.click()
                    return label[:60]
            except Exception:
                continue
    return None


def _grid_row_count(page):
    """How many table/grid rows are currently rendered, across frames. A DOM read - no request.

    RAW count on purpose: a classic ASP.NET page is FULL of layout tables, so an absolute count says
    nothing. The caller takes a BASELINE before submitting and treats only the DELTA as results -
    otherwise a page whose chrome contains 30 <tr>s would look like a populated grid instantly and
    every report would be mis-reported as "results rendered but nothing downloadable"."""
    n = 0
    for f in _frames(page):
        try:
            c = f.evaluate(
                "() => document.querySelectorAll('table tr').length"
                " + document.querySelectorAll('div.jqx-grid-cell').length"
                " + document.querySelectorAll('[role=row]').length")
            n += int(c or 0)
        except Exception:
            continue
    return n


def _empty_marker(page):
    """The portal's OWN 'no records' phrase if it is on screen, else None. A DOM read - no request."""
    txt = _all_frames_text(page)
    if not txt:
        return None
    for m in _EMPTY_MARKERS:
        if m in txt:
            return m
    return None


def _find_export_control(page, order):
    """(element, want, label) for the best export control on screen, else (None, None, None).
    Format-specific controls win; a generic Export/Download control is the fallback (a portal whose
    link is just 'Export' used to be invisible to us)."""
    generic = None
    for f in _frames(page):
        try:
            cands = f.query_selector_all("a, button, input[type=button], input[type=submit]")
        except Exception:
            continue
        for c in cands:
            try:
                if not _visible_safe(c):
                    continue
                label = ((c.get_attribute("value") or c.inner_text() or "") + " " +
                         (c.get_attribute("href") or "")).strip().lower()
                if not label:
                    continue
                for want in order:
                    if want in label:
                        return c, want, label[:60]
                if generic is None and ("export" in label or "download" in label):
                    generic = (c, order[0], label[:60])
            except Exception:
                continue
    return generic if generic else (None, None, None)


def _wait_for_results(page, order, budget_s, poll_ms=_RESULTS_POLL_MS, base_rows=0,
                      export_was_present=False):
    """BOUNDED wait for the report results to render, then report WHICH of the four things happened.

    THE PRIME SUSPECT this fixes: an ASP.NET/jqx grid populates ASYNCHRONOUSLY. The old code waited
    for `networkidle` (which resolves immediately when the page is already idle at call time) plus a
    flat 2s, then looked for the export link ONCE. Reading an empty grid too early is
    INDISTINGUISHABLE from a genuinely empty result - and it was reported as the latter.

    Waits are TIME (page.wait_for_timeout between DOM reads), never extra requests: nothing here
    navigates, reloads or re-submits. Returns
        {state, waited_s, rows_seen, marker, export_label}
    with state in export_ready | empty | no_export_link | timeout."""
    import time as _t
    budget = max(5, int(budget_s or _RESULTS_WAIT_S))
    polls = max(2, int(budget * 1000 / max(250, poll_ms)))
    started = _t.time()
    # A portal that shows "Export to: CSV" on the EMPTY form as well would otherwise be exported
    # instantly - i.e. downloaded before the grid finished populating, which is the very race this
    # function exists to close. When the control pre-existed the run we first wait for the results to
    # change (or for the portal's own empty-state), and only then click it.
    settle = min(budget, _EXPORT_SETTLE_S) if export_was_present else 0
    rows_at, rows_seen = None, 0
    marker = None
    for _ in range(polls):
        el, want, label = _find_export_control(page, order)
        ready = el is not None
        if ready and settle and not (rows_seen or marker) and (_t.time() - started) < settle:
            ready = False                        # pre-existing export: hold until results move
        if ready:
            return {"state": "export_ready", "element": el, "want": want, "export_label": label,
                    "waited_s": round(_t.time() - started, 1), "rows_seen": rows_seen,
                    "marker": marker,
                    "stale_export_risk": bool(export_was_present and not (rows_seen or marker))}
        rows = max(0, _grid_row_count(page) - int(base_rows or 0))
        if rows > rows_seen:
            rows_seen = rows
        marker = marker or _empty_marker(page)
        if marker and not rows_seen:
            # The portal EXPLICITLY said there is nothing. That is a real answer, not a failure.
            return {"state": "empty", "marker": marker, "rows_seen": 0,
                    "waited_s": round(_t.time() - started, 1)}
        if rows_seen:
            # Results ARE on screen but no export control (yet). Give it a short grace, then say so -
            # "the grid rendered but nothing was downloadable" is a scrape gap, not an empty month.
            rows_at = rows_at if rows_at is not None else _t.time()
            if _t.time() - rows_at > _EXPORT_GRACE_S:
                return {"state": "no_export_link", "rows_seen": rows_seen, "marker": marker,
                        "waited_s": round(_t.time() - started, 1)}
        try:
            page.wait_for_timeout(poll_ms)
        except Exception:
            pass
        if _t.time() - started > budget:
            break
    if rows_seen:
        return {"state": "no_export_link", "rows_seen": rows_seen, "marker": marker,
                "waited_s": round(_t.time() - started, 1)}
    return {"state": "timeout", "rows_seen": 0, "marker": marker,
            "waited_s": round(_t.time() - started, 1)}


class ExportOutcome(tuple):
    """A plain `(content, filename)` 2-tuple - every existing caller and test double keeps working -
    carrying the honest per-window detail on `.detail`."""

    def __new__(cls, content, filename, detail=None):
        t = super().__new__(cls, (content, filename))
        t.detail = dict(detail or {})
        return t


def _submit_and_export(page, frame, export_pref, timeout_s=_RESULTS_WAIT_S):
    """Run the report and download its export. Returns ExportOutcome(content, filename, detail) -
    a 2-tuple whose `.detail` says WHAT HAPPENED:

        not_submitted  - no Run/Submit control could be found or clicked (nothing was ever run)
        empty          - the portal displayed an explicit "no records" state (a REAL empty result)
        no_export_link - results rendered, but nothing downloadable was offered (scrape gap)
        timeout        - nothing rendered within the budget (SCRAPE FAILURE, not "no data")
        export_failed  - the export control was clicked but the download never arrived
        exported       - bytes in hand

    `timeout_s` is the RESULTS-WAIT budget (the caller derives it from param_spec.results_wait_s,
    capped by submit_timeout_s)."""
    pref = (export_pref or "csv").lower()
    order = ("csv", "excel") if pref == "csv" else ("excel", "csv")
    # BEFORE the run: how much table chrome this page already has, and whether an export control is
    # already on screen. Both are needed to tell "the results arrived" from "the page always looked
    # like this" - the two states the old single read could not distinguish.
    base_rows = _grid_row_count(page)
    export_was_present = _find_export_control(page, order)[0] is not None
    label = _click_run(frame, page)
    if not label:
        return ExportOutcome(None, None, {
            "state": "not_submitted",
            "note": ("no Run/Submit control was found on the report form, so the report was never "
                     "actually run")})
    try:
        # Capped deliberately: the bounded poll below is the real wait now, and a page that keeps a
        # long-poll/XHR open would otherwise burn the whole budget here doing nothing.
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    res = _wait_for_results(page, order, timeout_s, base_rows=base_rows,
                            export_was_present=export_was_present)
    res["submit_label"] = label
    st = res.get("state")
    if st != "export_ready":
        res.pop("element", None)
        return ExportOutcome(None, None, res)
    c = res.pop("element", None)
    want = res.get("want") or pref
    try:
        with page.expect_download(timeout=120000) as dl:
            c.click()
        d = dl.value
        import os
        path = d.path()
        with open(path, "rb") as fh:
            content = fh.read()
        try:
            os.unlink(path)
        except Exception:
            pass
        res["state"] = "exported"
        res["bytes"] = len(content or b"")
        return ExportOutcome(content, (d.suggested_filename or ("export." + want)), res)
    except Exception as e:
        res["state"] = "export_failed"
        res["note"] = ("the export control (%s) was clicked but no download arrived: %s"
                       % (res.get("export_label") or want, str(e)[:120]))
        return ExportOutcome(None, None, res)


# The caller's "the Reports navigation was already tried and it failed" sentinel, threaded through the
# `options` argument (which is otherwise the portal's report-name vocabulary). Keeps the whole pull to a
# SINGLE navigation ladder instead of one per report — see _pull_all_reports_on_page.
NAV_EXHAUSTED = "__nav_exhausted__"


def _nav_exhausted(options):
    """True when the caller already spent this pull's navigation budget. PURE."""
    try:
        return NAV_EXHAUSTED in (options or [])
    except Exception:
        return False


# Window states that mean "this report did not produce data for a reason that will repeat" - the
# report stops iterating months (fewer heavy requests at a paced portal) and reports the failure.
_HARD_WINDOW_STATES = ("not_submitted", "timeout", "no_export_link", "export_failed")
# state -> (report reason, operator-facing sentence). The SPLIT that did not exist before
# 2026-07-28: an explicitly empty portal answer is NOT the same event as a grid that never rendered,
# and only one of the two means "there is no data".
_STATE_REASONS = {
    "not_submitted": ("run_control_missing",
                      "the report's Run/Submit control could not be found, so the report was never "
                      "actually run - nothing was submitted to the portal"),
    "timeout": ("results_never_rendered",
                "the report WAS submitted but its results never rendered within %(wait)ss - a scrape "
                "timeout, NOT an empty result: the portal may well have data for this window"),
    "no_export_link": ("export_link_missing",
                       "the results rendered but no CSV/Excel export control was offered, so nothing "
                       "could be downloaded"),
    "export_failed": ("export_download_failed",
                      "the export control was clicked but the download never arrived"),
    "reselect_failed": ("report_select_missing",
                        "the report dropdown disappeared before this month could be run"),
    "window_error": ("window_error", "the month window failed while being run"),
}


def _report_verdict(windows, rows_ingested, wait_s):
    """PURE. Turn the per-window states into ONE honest report-level verdict.

    The lie this removes: every report used to return ok=True regardless, so a report that was never
    submitted, or whose grid never rendered, was aggregated into "the reports ran but returned
    nothing for the last 2 month(s)" - indistinguishable from a portal that genuinely has no data."""
    ws = list(windows or [])
    exported = [w for w in ws if w.get("state") == "exported"]
    empties = [w for w in ws if w.get("state") == "empty"]
    hard = [w for w in ws if w.get("state") in _HARD_WINDOW_STATES
            or w.get("state") in ("reselect_failed", "window_error")]
    if exported:
        if rows_ingested:
            return {"ok": True, "outcome": ("imported %d row(s) from %d month(s)"
                                            % (rows_ingested, len(exported)))}
        return {"ok": True, "empty_confirmed": True,
                "outcome": ("the portal produced an export for %d month(s) and it contained no data "
                            "rows - a real empty result" % len(exported))}
    if empties and not hard:
        return {"ok": True, "empty_confirmed": True,
                "outcome": ("the portal ran this report and displayed its own \u201cno records\u201d "
                            "message for %d month(s) (%s) - a real empty result, not a scraping "
                            "problem" % (len(empties), (empties[0].get("marker") or "no records")))}
    if hard:
        reason, sentence = _STATE_REASONS.get(hard[0].get("state"), ("pull_failed", "the report failed"))
        return {"ok": False, "reason": reason,
                "outcome": sentence % {"wait": wait_s},
                "error": sentence % {"wait": wait_s}}
    if not ws:
        return {"ok": False, "reason": "no_windows",
                "outcome": "no month window was in range for this report"}
    return {"ok": True, "outcome": "the report ran and returned no rows"}


def _pull_one_report(page, client, org_id, source_id, carrier_id, source_row, spec, start_dt, end_dt,
                     frame=None, options=None):
    """Drive one report end-to-end across its month windows. Returns a summary dict (never raises).

    `frame` is the already-resolved Reports frame (resolved ONCE per pull by the caller instead of
    re-navigating per report); `options` is what the portal's dropdown actually offers, echoed into the
    failure so the operator is told the real report names rather than "calibrate the display_name"."""
    from app.modules.commcalc import report_pull as rp
    rk = spec.get("report_key")
    ps = spec.get("param_spec") or {}
    target = spec.get("target_table")
    date_col = ps.get("date_col")
    if frame is None and not _nav_exhausted(options):
        # Only ever ONE re-navigation ladder per pull. The caller resolves the Reports frame once and
        # passes it down; before 2026-07-27 a None frame made EVERY report re-run the whole ladder
        # (5 reports x up to 7 navigations = ~35 page loads at a portal that had already refused us).
        # `options` carries the caller's "I already tried and failed" sentinel.
        frame = _open_reports_page(page)
    if frame is None:
        return {"report_key": rk, "target_table": target, "ok": False, "reason": "no_reports_page",
                "error": ("the portal's Reports page could not be opened from the page this login "
                          "landed on, so no report could be selected"),
                "diag": _snapshot(page)}
    aliases = _spec_aliases(spec)
    sel = _select_report_detail(frame, spec.get("display_name"), aliases)
    if not sel.get("ok"):
        captured = [o for o in list(options or []) if o != NAV_EXHAUSTED]
        live = list(sel.get("options") or [])
        opts = live or captured or [o for o in report_options(page) if o != NAV_EXHAUSTED]
        dbg = sel.get("match_debug") or {}
        wrep = ((dbg.get("wanted") or {}).get("repr") or repr(spec.get("display_name") or ""))
        near = dbg.get("nearest_offered") or {}
        if sel.get("reason") == "report_select_missing":
            # NOT a naming problem: the dropdown was not on the page at this report's turn.
            err = ("the portal's report dropdown was not on the page when this report's turn came, so "
                   "%s was never offered for selection. That is a page-state problem (the previous "
                   "report's results replaced the form), not a wrong report name."
                   % (spec.get("display_name") or rk))
        elif sel.get("reason") == "ambiguous":
            err = ("\u201c%s\u201d matches SEVERAL of this portal's report names, so nothing was "
                   "selected (guessing could pull the wrong report into %s). Use the exact name from "
                   "the list: %s" % (spec.get("display_name") or rk, target, ", ".join(opts[:12])))
        else:
            err = ("\u201c%s\u201d is not one of the reports this portal login offers%s"
                   % (spec.get("display_name") or rk,
                      (" \u2014 it offers: " + ", ".join(opts[:12])) if opts else ""))
            # THE ANTI-CONTRADICTION CLAUSE. If the wanted name LOOKS present in that list, the
            # difference is invisible - so print the bytes of both sides. Compared after NFKC +
            # nbsp/zero-width/dash folding, so this can only ever fire on a genuine difference.
            err += (" \u2014 compared after normalising invisible characters: wanted %s, closest "
                    "offered %s%s." % (wrep, (near.get("repr") or "\u2014"),
                                       ((" " + ", ".join(near.get("odd_chars") or []))
                                        if near.get("odd_chars") else "")))
        return {"report_key": rk, "target_table": target, "ok": False,
                "reason": sel.get("reason") or "report_not_listed",
                "display_name": spec.get("display_name"),
                "outcome": err, "error": err,
                "portal_options": opts[:20],
                "portal_options_captured": captured[:20],
                # True ⇒ the dropdown CHANGED between the pre-pull capture and this attempt: the
                # second mechanism behind the self-contradicting message.
                "options_changed": bool(live and captured and live != captured),
                "match_debug": dbg,
                "diag": _snapshot(page)}
    # month iteration, capped at the report's max_months_back and VidaPay's ≤1-year hard limit
    max_back = min(int(ps.get("max_months_back") or 12), 12)
    floor = end_dt.replace(hour=0, minute=0)
    from datetime import datetime as _dt
    y, m = floor.year, floor.month - max_back
    while m <= 0:
        m += 12; y -= 1
    range_start = max(start_dt, _dt(y, m, 1))
    wins = rp.month_windows(range_start, end_dt, ps.get("interval_months", 1)) if ps.get("iterate_months") \
        else [(range_start, end_dt)]
    # The results-wait budget: config first (param_spec.results_wait_s), capped by the report's own
    # submit timeout. A wait is TIME, not requests - nothing here re-navigates or re-submits.
    wait_s = min(int(ps.get("results_wait_s") or _RESULTS_WAIT_S),
                 int(ps.get("submit_timeout_s") or 300))
    total, months, win_diag, windows = 0, [], None, []
    for (ws, we) in wins:
        wrec = {"window": ws.strftime("%Y-%m"),
                "requested": [ws.strftime("%Y-%m-%d"), we.strftime("%Y-%m-%d")]}
        try:
            resel = _select_report_detail(frame, spec.get("display_name"), aliases)
            if not resel.get("ok"):
                wrec.update(state="reselect_failed", note=(resel.get("reason") or "not selectable"))
                windows.append(wrec)
                break            # the form is gone; the remaining windows would fail identically
            fields = _fill_param_fields(frame, ps.get("fields"), ws, we, source_row)
            wrec["fields"] = fields
            res = _submit_and_export(page, frame, spec.get("export_pref"), wait_s)
            content, fn = (res[0], res[1])
            det = dict(getattr(res, "detail", None) or {})
            state = det.get("state") or ("exported" if content is not None else "unknown")
            wrec.update({k: v for k, v in det.items() if k != "element"})
            wrec["state"] = state
            if content is None:
                windows.append(wrec)
                win_diag = win_diag or {"window": wrec["window"], "state": state,
                                        "note": det.get("note"), "diag": _snapshot(page)}
                if state in _HARD_WINDOW_STATES:
                    # The same failure will repeat for every remaining window, and each retry is
                    # another heavy report-GENERATION request at a portal we are pacing. Stop here.
                    break
                continue
            rows = rp.parse_export_bytes(content, fn)
            mapped = rp.apply_column_map(rows, spec, org_id, source_id, carrier_id)
            n = rp.ingest_report_rows(client, org_id, target, mapped, source_id=source_id,
                                      date_col=date_col, win_start=ws, win_end=we)
            total += n
            months.append(ws.strftime("%Y-%m"))
            wrec.update(state="exported", rows_in_export=len(rows), rows_ingested=n)
            windows.append(wrec)
        except Exception as e:
            wrec.update(state="window_error", error=str(e)[:200])
            windows.append(wrec)
            win_diag = win_diag or {"window": wrec["window"], "error": str(e)[:200],
                                    "diag": _snapshot(page)}
            continue
    out = {"report_key": rk, "target_table": target,
           "rows_ingested": total, "months_covered": months, "windows": windows[:12]}
    if sel.get("tier") and sel.get("tier") != "exact":
        # Selected only AFTER normalising invisible characters - surfaced so the operator learns the
        # portal's real spelling instead of wondering why it "sometimes" works.
        out["name_match"] = {"tier": sel.get("tier"), "matched": sel.get("matched"),
                             "debug": sel.get("match_debug")}
    verdict = _report_verdict(windows, total, wait_s)
    out.update(verdict)
    if ps.get("calibration"):
        out["calibration"] = True
        out["diag"] = _snapshot(page)   # pin the un-screenshotted SIM/PR params from this
    if win_diag:
        out["window_diag"] = win_diag
    return out


def _pull_all_reports_on_page(page, client, org_id, source_id=None, carrier_id=None,
                              months_back=2, source_row=None, should_stop=None):
    """Pull EVERY enabled report (config-driven report_pull_map, degrading to DEFAULT_REPORT_SPECS) on
    an ALREADY-AUTHENTICATED `page`, month-by-month across the last `months_back` months (each report's
    ≤1-month window + ≤1-year-back caps still apply), mapping + ingesting each export idempotently.
    Shared by run_vidapay_sweep (cold storage_state restore) AND the live-login session, which calls
    this on the SAME trusted browser that just passed 2FA — the cold restore is re-challenged by
    T-CETRA (a fresh browser / egress IP / server session isn't the trusted device), so reusing the
    live page is what makes '▶ Pull now' work right after a live login. This NEVER builds a context or
    navigates to the login URL; the caller has already placed `page` on an authenticated portal page.
    Returns a per-report summary dict."""
    from app.modules.commcalc import report_pull as rp
    from datetime import datetime as _dt, timedelta as _td
    specs = rp.resolve_report_specs(client, org_id, processor="vidapay", only_enabled=True)
    end_dt = _dt.now()
    start_dt = end_dt - _td(days=31 * max(1, int(months_back or 1)))
    src = dict(source_row or {})
    markers = _pb.load_markers(client, org_id, "vidapay")
    # RATE-LIMIT GATE — checked BEFORE the Reports navigation, because this is the expensive path:
    # 5 configured reports x up to 3 month-windows x (select postback + submit postback + export) is
    # ~45–60 heavy report-GENERATION requests, the kind a throttle counts hardest.
    _raise_if_rate_limited(page, markers=markers, where="The portal")
    # Reach the Reports page ONCE (was: re-navigated inside every report), then capture what the portal
    # really offers so a failure can name the fix instead of saying "calibration needed".
    frame = _open_reports_page(page, markers=markers)
    if frame is None:
        # "Reports page unreachable" and "the portal is blocking us" look identical from here. Ask.
        _raise_if_rate_limited(page, markers=markers, where="The portal")
    # SENTINEL, not a retry: the navigation budget for this pull is spent, so the reports below report
    # "Reports page unreachable" from the ONE ladder already run instead of each re-running their own.
    options = report_options(page) if frame is not None else [NAV_EXHAUSTED]
    probe = reports_probe(page)
    reports = []
    if not specs:
        return {"status": ("\u26a0\ufe0f imported 0 rows \u2014 no reports are switched on for this "
                           "tenant, so the pull had nothing to fetch. Turn them on at Report mapping."),
                "authenticated": True, "delivered": False, "reports": [], "rows_ingested": 0,
                "months_back": months_back, "reason": "no_reports_configured",
                "reports_page_reachable": frame is not None, "probe": probe,
                "calibration": {"portal_report_options": [o for o in options if o != NAV_EXHAUSTED],
                                "configured": [], "unmatched": []}}
    stopped = False
    for spec in specs:
        # INTERRUPTIBLE between reports. Five reports × several month-windows × a 300s submit timeout is
        # a long time to hold the live session's only worker thread; since a successful login now starts
        # this pull on its own, an operator who presses Close must not have to wait it out.
        if should_stop is not None:
            try:
                if should_stop():
                    stopped = True
                    break
            except Exception:
                pass
        # A portal that starts throttling MID-PULL must not be hit by the remaining reports: the gate is
        # re-checked between reports and aborts the whole pull (the caller then stamps the cooldown).
        _raise_if_rate_limited(page, markers=markers, where="The portal")
        reports.append(_pull_one_report(page, client, org_id, source_id, carrier_id,
                                        src, spec, start_dt, end_dt, frame=frame, options=options))
    ok_rows = sum(r.get("rows_ingested", 0) for r in reports)
    ok_reports = [r["report_key"] for r in reports if r.get("ok") and r.get("rows_ingested")]
    calib = [r["report_key"] for r in reports
             if r.get("calibration") or r.get("reason") in ("report_not_listed", "ambiguous")]
    # SCRAPE FAILURES vs a portal that genuinely has nothing. Before 2026-07-28 both landed in the
    # same sentence ("the reports ran but returned nothing"), which is why a pull that never
    # submitted a single report read as "there is no data".
    failed = [r for r in reports if not r.get("ok")
              and r.get("reason") not in ("report_not_listed", "ambiguous")]
    empty_ok = [r for r in reports if r.get("ok") and r.get("empty_confirmed")]
    configured = [(s.get("display_name") or s.get("report_key") or "") for s in specs]
    unmatched = [r.get("display_name") or r.get("report_key")
                 for r in reports if r.get("reason") in ("report_not_listed", "ambiguous")]
    delivered = ok_rows > 0
    if delivered:
        status = (f"imported {ok_rows} rows across {len(ok_reports)} report(s): "
                  f"{', '.join(ok_reports)}"
                  + (f"; still uncalibrated: {', '.join(calib)}" if calib else "")
                  + (("; %d report(s) could not be scraped: " % len(failed)
                      + ", ".join("%s (%s)" % (r.get("report_key"), r.get("reason")) for r in failed))
                     if failed else ""))
        reason = None
    elif frame is None:
        reason = "no_reports_page"
        status = ("\u26a0\ufe0f imported 0 rows \u2014 signed in fine, but the portal's Reports page "
                  "could not be opened, so no report could be run. Open \U0001f527 What the pull saw "
                  "to see the menu this login actually has.")
    elif unmatched and len(unmatched) == len(reports):
        reason = "report_not_listed"
        status = ("\u26a0\ufe0f imported 0 rows \u2014 none of the %d configured report names exist in "
                  "this portal's Reports list%s. Fix the names at Report mapping."
                  % (len(reports),
                     (" (it offers: " + ", ".join(options[:8]) + ")") if options else ""))
    elif failed:
        # HONEST: a report that was never submitted, or whose grid never rendered, is a SCRAPE
        # FAILURE. Saying "returned nothing" about it invents a fact about the portal's data.
        reason = (failed[0].get("reason") or "pull_failed")
        status = ("\u26a0\ufe0f imported 0 rows \u2014 %d report(s) could not be scraped: %s. "
                  "This is a scraping failure, NOT a statement that the portal has no data. "
                  "Open \U0001f527 What the pull saw for the per-report detail."
                  % (len(failed),
                     "; ".join("%s \u2014 %s" % (r.get("report_key"), r.get("outcome") or r.get("reason"))
                               for r in failed[:4])))
    elif empty_ok and len(empty_ok) == len([r for r in reports if r.get("ok")]):
        # Every report that ran said, in the portal's own words, that it has no records.
        reason = "portal_reported_empty"
        status = ("imported 0 rows \u2014 the portal ran %d report(s) and reported no records for the "
                  "last %s month(s). That is the portal's own answer, not a scraping problem."
                  % (len(empty_ok), months_back))
    else:
        reason = "no_rows"
        status = ("\u26a0\ufe0f imported 0 rows \u2014 the reports ran but returned nothing for the "
                  "last %s month(s)%s." % (months_back,
                                           ("; not yet calibrated: " + ", ".join(calib)) if calib else ""))
    if stopped:
        status = ("stopped early at the operator's request — " + status)[:600]
    return {"status": status, "authenticated": True, "delivered": delivered, "reports": reports,
            "rows_ingested": ok_rows, "months_back": months_back, "reason": reason,
            "stopped": stopped,
            "reports_page_reachable": frame is not None, "probe": probe,
            "calibration": {"portal_report_options": [o for o in options if o != NAV_EXHAUSTED],
                            "configured": configured, "unmatched": unmatched}}


def run_vidapay_sweep(client, org_id, url, session_state, source_id=None, carrier_id=None,
                      proxy_url=None, account_id=None, months_back=2, source_row=None):
    """Restore the authenticated session and pull EVERY enabled report in report_pull_map for this org
    (config-driven), month-by-month across the last `months_back` months (respecting each report's
    ≤1-month window + ≤1-year-back caps), mapping + ingesting each export idempotently into its target
    table. Returns a per-report summary; raises VidaPayAuthError if the session is missing/expired.

    NOTE: this is the COLD-RESTORE path (fresh browser + persisted storage_state), used for scheduled
    pulls and when no live session is available. T-CETRA does not always trust a cold restore (new
    browser/egress/server session → it re-challenges 2FA and this raises VidaPayAuthError); an operator
    '▶ Pull now' right after a 🔴 Live login is routed to the live browser instead (router.run_data_source
    → LiveLoginSession.run_pull_blocking → _pull_all_reports_on_page on the SAME trusted page)."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise VidaPayLoginError("Playwright/Chromium is not available in the backend image.")
    if not session_state:
        raise VidaPayAuthError("Not authenticated yet — click “Log in” and complete 2FA first.")
    base_url = _norm_url(url, DEFAULT_URL)
    src = dict(source_row or {})
    if account_id and not src.get("account_id"):
        src["account_id"] = account_id
    with sync_playwright() as p:
        browser = _launch(p)
        ctx = _new_context(browser, storage_state=session_state, proxy=_proxy_arg(proxy_url))
        page = ctx.new_page()
        try:
            # FRESH ENTRY navigation of a cold-restore browser — nothing is clicked/submitted here (no 2FA
            # resend risk), so a transient connection-class drop through the residential proxy is retried
            # (same guard as _goto_login). A squid proxy-error PAGE (goto succeeds, renders squid) is still
            # caught below by _classify → proxy_error; the retry only covers goto that RAISES a net::ERR_.
            markers = _pb.load_markers(client, org_id, "vidapay")
            resp = _goto_with_retry(page, base_url, timeout=60000, wait_until="domcontentloaded")
            # RATE-LIMIT GATE — a throttled portal serves a block page carrying no session markers, so
            # the old code concluded "the session has expired" and prompted a re-login: the single worst
            # response to an active block (a fresh headless login is the most expensive request there is).
            _raise_if_rate_limited(page, resp, markers=markers, where="The portal")
            _wait_settle(page)
            page.wait_for_timeout(2500)
            state = _classify(page)
            if state == "proxy_error" and _recover_from_proxy_error(page, dest_url=base_url):
                state = _classify(page)            # http-302→squid hop recovered (GET-only, no re-submit)
            if state == "proxy_error":
                raise VidaPayPortalError(_proxy_error_message(page.url, proxy_url, _squid_reported_url(page)))
            if state in ("login", "twofa", "botwall"):
                _raise_if_rate_limited(page, markers=markers, where="The portal")
                raise VidaPayAuthError(
                    "The VidaPay session has expired — please re-authenticate (Log in + 2FA).")
            return _pull_all_reports_on_page(page, client, org_id, source_id, carrier_id,
                                             months_back, src)
        finally:
            browser.close()


# ── b2bsoft (wsreports.b2bsoft.com) — SAME interactive-2FA + session + proxy machinery as VidaPay ──
# The login/2FA endpoints already use the generic begin_login/complete_2fa above, so b2bsoft's
# interactive 2FA works for a data_source with processor='b2bsoft'; this is the report-pull handler.
B2BSOFT_URL = "https://wsreports.b2bsoft.com"


def b2b_reports_probe(page):
    """Rich probe of the b2bsoft reports UI (links + export buttons + date fields + dropdowns) so the
    actual Sales-Transaction-Details download can be wired in ONE pass from a real logged-in session,
    instead of guessing the portal's navigation blind. Never raises; reads names/ids/labels only."""
    probe = {}
    for fr in _frames(page):
        try:
            p = fr.evaluate(
                """() => ({
                    url: location.href, title: document.title,
                    links: Array.from(document.querySelectorAll('a')).map(a=>({t:(a.innerText||'').trim(), href:a.href, id:a.id}))
                            .filter(x=>x.t && /sales|transaction|report|export|download|daily|detail/i.test(x.t)).slice(0,40),
                    buttons: Array.from(document.querySelectorAll('button,input[type=button],input[type=submit]'))
                            .map(b=>({t:(b.innerText||b.value||'').trim(), id:b.id, name:b.name}))
                            .filter(x=>x.t && /report|export|download|run|view|generate|search|submit|go/i.test(x.t)).slice(0,40),
                    dates: Array.from(document.querySelectorAll('input')).map(i=>({id:i.id,name:i.name,type:i.type,ph:i.placeholder}))
                            .filter(x=>/date|from|to|start|end/i.test((x.id||'')+(x.name||'')+(x.ph||''))).slice(0,20),
                    selects: Array.from(document.querySelectorAll('select')).map(s=>({id:s.id,name:s.name,opts:Array.from(s.options).slice(0,12).map(o=>(o.text||'').trim())})).slice(0,20),
                })""")
            if p and (p.get("links") or p.get("buttons") or p.get("selects")):
                return p
            if p and not probe:
                probe = p
        except Exception:
            continue
    return probe


def pull_b2bsoft_on_page(page):
    """The b2bsoft 'pull' on an ALREADY-AUTHENTICATED page: there is no report download wired for this
    portal yet, so it probes what the session offers and says so HONESTLY.

    Split out of run_b2bsoft_sweep so the LIVE session can call it too. Before this, a b2bsoft live
    login's pull ran `_pull_all_reports_on_page`, which resolves specs for processor='vidapay' — i.e.
    it drove the VidaPay MA report list against the b2bsoft portal. Harmless-looking while it was only
    reachable by clicking ▶ Pull now; wrong for every reason once login auto-pulls.

    rows_ingested/delivered are EXPLICIT zeros: without them `_pull_delivered` fell through to its
    "unknown shape ⇒ True" branch and a b2bsoft pull that imported NOTHING advanced
    data_source.last_run_at — re-creating, for this processor, exactly the fake freshness that
    migration 241 was written to kill."""
    return {
        "status": "⚠️ imported 0 rows — signed in to b2bsoft OK, but its Sales Transaction Details "
                  "auto-download is not wired yet, so this pull imported nothing (the daily email "
                  "feed keeps ingesting meanwhile)",
        "authenticated": True, "delivered": False, "rows_ingested": 0, "reason": "not_wired",
        "report_probe": b2b_reports_probe(page), "diag": _snapshot(page),
    }


def run_b2bsoft_sweep(client, org_id, url, session_state, source_id=None, carrier_id=None, proxy_url=None):
    """Restore the authenticated b2bsoft session (established via the interactive Log in + 2FA flow and
    persisted as storage_state — optionally routed through a residential proxy to clear b2bsoft's
    datacenter-IP wall) and verify it's alive, reporting the sales-report links it exposes so the Sales
    Transaction Details auto-download can be pinned in one live calibration pass. Raises VidaPayAuthError
    (reused) if the session is missing/expired so the router prompts a re-login."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise VidaPayLoginError("Playwright/Chromium is not available in the backend image.")
    if not session_state:
        raise VidaPayAuthError("Not authenticated yet — click “Log in” and complete 2FA first.")
    base_url = _norm_url(url, B2BSOFT_URL)
    with sync_playwright() as p:
        browser = _launch(p)
        ctx = _new_context(browser, storage_state=session_state, proxy=_proxy_arg(proxy_url))
        page = ctx.new_page()
        try:
            # FRESH ENTRY navigation of a cold-restore browser — nothing clicked/submitted here (no 2FA
            # resend risk), so a transient connection-class drop through the residential proxy is retried
            # (same guard as _goto_login). A squid proxy-error PAGE is still caught below by
            # _looks_like_proxy_error; the retry only covers goto that RAISES a net::ERR_.
            markers = _pb.load_markers(client, org_id, "b2bsoft")
            resp = _goto_with_retry(page, base_url, timeout=60000, wait_until="domcontentloaded")
            _raise_if_rate_limited(page, resp, markers=markers, where="The POS portal")
            _wait_settle(page)
            page.wait_for_timeout(2500)
            # http-302→squid hop recovery (v2: https twin, then a direct goto of the https base; GET-only,
            # never re-submits). Cookies persist → a live session lands on the app if it's still valid.
            if _looks_like_proxy_error(page) and not _recover_from_proxy_error(page, dest_url=base_url):
                raise VidaPayPortalError(_proxy_error_message(page.url, proxy_url, _squid_reported_url(page)))
            # b2bsoft-specific validity check (the generic _classify can misread the wsreports app as a
            # login page): the session is invalid ONLY if we're on the SSO login (#companyId) or 2FA screen.
            u = (page.url or "").lower()
            if page.query_selector("#companyId") or page.query_selector("#TwoFactorCode") \
                    or "/account/login" in u or "twofactor" in u:
                _raise_if_rate_limited(page, markers=markers, where="The POS portal")
                raise VidaPayAuthError(
                    "The b2bsoft session has expired — please re-authenticate (Log in + enter the 2FA code).")
            return pull_b2bsoft_on_page(page)
        finally:
            browser.close()
