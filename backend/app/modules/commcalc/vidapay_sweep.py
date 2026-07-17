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


class VidaPayAuthError(Exception):
    """The stored session is missing/expired — the operator must (re-)log in + pass 2FA."""


class VidaPayPortalError(Exception):
    """Logged in fine, but a later step (report navigation/download/parse) failed."""


def _norm_url(u, fallback):
    """Playwright rejects a scheme-less URL ("vidapaycrm.com" -> "Cannot navigate to invalid URL").
    Operators naturally type the bare host, so add the scheme they omitted."""
    u = (u or "").strip()
    if not u:
        return fallback
    if "://" not in u:
        u = "https://" + u.lstrip("/")
    return u


def _egress_ip(proxy_url=None, timeout=12):
    """The public IP a request actually leaves from (through `proxy_url`, or direct). None on failure."""
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
    """page.goto with a BOUNDED retry on CONNECTION-CLASS failures only. A rotating residential proxy can
    hand back a dead exit / severed TLS, so a single navigation dies with net::ERR_CONNECTION_CLOSED even
    though the very next attempt succeeds. SAFE only for a FRESH ENTRY navigation where nothing has been
    clicked/submitted (no 2FA-resend risk) — callers MUST NOT use it for a post-login / post-code (2FA)
    navigation. A NON-connection error (selector timeout, WAF) is re-raised on the FIRST failure (never
    retried). On the final connection-class failure raises a VidaPayLoginError with an operator-friendly
    message that NEVER carries the Playwright 'Call log:' block."""
    last = None
    for i in range(attempts):
        try:
            page.goto(url, timeout=timeout, wait_until=wait_until)
            return
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


def _goto_login(page, base_url):
    """Navigate to the portal and settle. If it lands on the bot-wall (or the egress squid page) with no
    form, retry straight at the id-server login endpoint (deep links are flagged more often than the login
    URL itself, AND the https id-server is the real pre-auth destination — going there skips the www
    http-redirect chain that produces the squid hop entirely).

    Both navigations here are PRE-LOGIN entry navigations (nothing has been clicked/submitted — no 2FA
    has been dispatched), so each goes through _goto_with_retry: a transient connection-class drop through
    the residential proxy is retried, and an exhausted failure surfaces as a clean VidaPayLoginError (never
    a raw Playwright 'Call log:'). Non-connection errors are NOT retried. After each goto we also run
    _recover_from_proxy_error (the http-302→squid hop; GET-only, no resubmit)."""
    _goto_with_retry(page, base_url, timeout=60000, wait_until="domcontentloaded")
    _wait_settle(page)
    page.wait_for_timeout(2500)
    # PRE-AUTH recovery destination is the https id-server LOGIN_URL (a clean https page with the login
    # form — it never triggers the www returnto→http hop that produces squid).
    _recover_from_proxy_error(page, dest_url=LOGIN_URL)
    fr, pw = _wait_for_password(page, timeout_s=20)
    if pw:
        return
    if _looks_like_bot_wall(page) or _looks_like_proxy_error(page):
        try:
            _goto_with_retry(page, LOGIN_URL, timeout=60000, wait_until="domcontentloaded")
            _wait_settle(page)
            _recover_from_proxy_error(page, dest_url=LOGIN_URL)
            _wait_for_password(page, timeout_s=20)
        except VidaPayLoginError:
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
def _open_reports_page(page):
    """Find the frame holding the Report <select>. If we're not on the Reports page yet, click a
    'Reports' / 'Billing Manager' nav link and settle. Returns the frame (or the main page)."""
    def _report_select_frame():
        for fr in _frames(page):
            try:
                for s in fr.query_selector_all("select"):
                    if not s.is_visible():
                        continue
                    opts = " ".join([(o.inner_text() or "") for o in s.query_selector_all("option")]).lower()
                    hay = ((s.get_attribute("name") or "") + (s.get_attribute("id") or "")).lower()
                    if "report" in hay or any(k in opts for k in
                                              ("commission details", "daily tx", "fulfillment",
                                               "sim assignment", "activation details")):
                        return fr
            except Exception:
                continue
        return None
    fr = _report_select_frame()
    if fr:
        return fr
    # navigate: click a Reports / Billing Manager link
    for want in ("reports", "billing manager", "report"):
        for f in _frames(page):
            try:
                if _click_submit(f, (want,)):
                    page.wait_for_timeout(2500)
                    _wait_settle(page)
                    break
            except Exception:
                continue
        fr = _report_select_frame()
        if fr:
            return fr
    return _report_select_frame() or page


def _select_report(frame, display_name):
    """Select the report by its display name in the Report <select> and wait for the ASP.NET postback
    that renders that report's parameter fields. Returns True if selected."""
    target = (display_name or "").strip().lower()
    for s in frame.query_selector_all("select"):
        try:
            if not s.is_visible():
                continue
            for o in s.query_selector_all("option"):
                t = (o.inner_text() or "").strip().lower()
                if t == target or (target and (target in t or t in target)) and t:
                    val = o.get_attribute("value")
                    if val is not None:
                        s.select_option(value=val)
                    else:
                        s.select_option(label=(o.inner_text() or "").strip())
                    try:
                        frame.page.wait_for_load_state("networkidle", timeout=20000)
                    except Exception:
                        pass
                    frame.page.wait_for_timeout(1500)
                    return True
        except Exception:
            continue
    return False


def _fill_param_fields(frame, fields, win_start, win_end, source_row):
    """Fill each param field per its spec: date fields get the month-window boundary formatted; static
    fields (Account_ID/SessionId) come from the data_source row; select fields pick the configured
    literal. Heuristic finders (by name/id/label/placeholder) so it survives small DOM drift."""
    from app.modules.commcalc import report_pull as rp
    filled = []
    for f in (fields or []):
        name = f.get("name") or ""
        kind = f.get("kind")
        toks = [w for w in name.lower().replace("_", " ").split() if len(w) > 1]
        try:
            if kind == "date":
                fmt = f.get("format") or "%m/%d/%Y"
                val = (win_start if f.get("role") == "start" else win_end).strftime(fmt)
                el = _find_input(frame, kinds=("text", "date", "datetime-local"), want=toks) \
                    or _find_input(frame, kinds=("text",), want=("date", "start", "end"))
                if el:
                    el.click(); el.fill(""); el.type(val, delay=8); filled.append(name)
            elif kind == "select":
                lit = (f.get("literal") or "").strip().lower()
                for s in frame.query_selector_all("select"):
                    if not s.is_visible():
                        continue
                    hay = ((s.get_attribute("name") or "") + (s.get_attribute("id") or "")).lower()
                    if not (any(t in hay for t in toks) or lit):
                        continue
                    for o in s.query_selector_all("option"):
                        if (o.inner_text() or "").strip().lower() == lit:
                            v = o.get_attribute("value")
                            s.select_option(value=v) if v is not None else s.select_option(label=o.inner_text())
                            filled.append(name)
                            break
            else:  # static
                val = rp.resolve_static(f.get("source"), source_row)
                if val == "" and f.get("optional"):
                    continue
                el = _find_input(frame, kinds=("text", "number", "hidden"), want=toks) \
                    or _find_input(frame, kinds=("text", "number"), want=("account", "session"))
                if el:
                    try:
                        el.fill(str(val))
                    except Exception:
                        el.evaluate("(e,v)=>{e.value=v;}", str(val))
                    filled.append(name)
        except Exception:
            continue
    return filled


def _submit_and_export(page, frame, export_pref, timeout_s=300):
    """Click Submit, wait (up to the report's timeout) for results, then click the preferred
    'Export to: CSV|Excel' link and capture the download. Returns (bytes, filename)."""
    _click_submit(frame, ("submit", "run", "view report", "generate", "search", "go"))
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_s, 300) * 1000)
    except Exception:
        pass
    page.wait_for_timeout(2000)
    pref = (export_pref or "csv").lower()
    order = ("csv", "excel") if pref == "csv" else ("excel", "csv")
    for want in order:
        for f in _frames(page):
            try:
                cands = f.query_selector_all("a, button, input[type=button], input[type=submit]")
            except Exception:
                continue
            for c in cands:
                try:
                    if not c.is_visible():
                        continue
                    label = ((c.get_attribute("value") or c.inner_text() or "") + " " +
                             (c.get_attribute("href") or "")).strip().lower()
                    if want in label and ("export" in label or want in label):
                        with page.expect_download(timeout=120000) as dl:
                            c.click()
                        d = dl.value
                        import tempfile, os
                        path = d.path()
                        with open(path, "rb") as fh:
                            content = fh.read()
                        try:
                            os.unlink(path)
                        except Exception:
                            pass
                        return content, (d.suggested_filename or ("export." + want))
                except Exception:
                    continue
    return None, None


def _pull_one_report(page, client, org_id, source_id, carrier_id, source_row, spec, start_dt, end_dt):
    """Drive one report end-to-end across its month windows. Returns a summary dict (never raises)."""
    from app.modules.commcalc import report_pull as rp
    rk = spec.get("report_key")
    ps = spec.get("param_spec") or {}
    target = spec.get("target_table")
    date_col = ps.get("date_col")
    frame = _open_reports_page(page)
    if not _select_report(frame, spec.get("display_name")):
        return {"report_key": rk, "ok": False,
                "error": "report not found in the Reports dropdown — calibrate the display_name",
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
    total, months, win_diag = 0, [], None
    for (ws, we) in wins:
        try:
            _select_report(frame, spec.get("display_name"))     # each postback resets the form
            _fill_param_fields(frame, ps.get("fields"), ws, we, source_row)
            content, fn = _submit_and_export(page, frame, spec.get("export_pref"),
                                             ps.get("submit_timeout_s", 300))
            if content is None:
                win_diag = win_diag or {"window": ws.strftime("%Y-%m"), "note": "no export produced",
                                        "diag": _snapshot(page)}
                continue
            rows = rp.parse_export_bytes(content, fn)
            mapped = rp.apply_column_map(rows, spec, org_id, source_id, carrier_id)
            n = rp.ingest_report_rows(client, org_id, target, mapped, source_id=source_id,
                                      date_col=date_col, win_start=ws, win_end=we)
            total += n
            months.append(ws.strftime("%Y-%m"))
        except Exception as e:
            win_diag = win_diag or {"window": ws.strftime("%Y-%m"), "error": str(e)[:200],
                                    "diag": _snapshot(page)}
            continue
    out = {"report_key": rk, "target_table": target, "ok": True,
           "rows_ingested": total, "months_covered": months}
    if ps.get("calibration"):
        out["calibration"] = True
        out["diag"] = _snapshot(page)   # pin the un-screenshotted SIM/PR params from this
    if win_diag:
        out["window_diag"] = win_diag
    return out


def _pull_all_reports_on_page(page, client, org_id, source_id=None, carrier_id=None,
                              months_back=2, source_row=None):
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
    reports = []
    for spec in specs:
        reports.append(_pull_one_report(page, client, org_id, source_id, carrier_id,
                                        src, spec, start_dt, end_dt))
    ok_rows = sum(r.get("rows_ingested", 0) for r in reports)
    ok_reports = [r["report_key"] for r in reports if r.get("ok") and r.get("rows_ingested")]
    calib = [r["report_key"] for r in reports if not r.get("ok") or r.get("calibration")]
    status = (f"pulled {ok_rows} rows across {len(ok_reports)} report(s): "
              f"{', '.join(ok_reports) or '—'}"
              + (f"; calibration/diagnostic needed: {', '.join(calib)}" if calib else ""))
    return {"status": status, "authenticated": True, "reports": reports,
            "rows_ingested": ok_rows, "months_back": months_back}


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
            _goto_with_retry(page, base_url, timeout=60000, wait_until="domcontentloaded")
            _wait_settle(page)
            page.wait_for_timeout(2500)
            state = _classify(page)
            if state == "proxy_error" and _recover_from_proxy_error(page, dest_url=base_url):
                state = _classify(page)            # http-302→squid hop recovered (GET-only, no re-submit)
            if state == "proxy_error":
                raise VidaPayPortalError(_proxy_error_message(page.url, proxy_url, _squid_reported_url(page)))
            if state in ("login", "twofa", "botwall"):
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
            _goto_with_retry(page, base_url, timeout=60000, wait_until="domcontentloaded")
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
                raise VidaPayAuthError(
                    "The b2bsoft session has expired — please re-authenticate (Log in + enter the 2FA code).")
            diag = _snapshot(page)
            # Rich probe of the reports UI (links + export buttons + date fields + dropdowns) so the
            # actual Sales-Transaction-Details download can be wired in ONE pass from a real logged-in
            # session, instead of guessing the portal's navigation blind.
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
                        probe = p
                        break
                    if p and not probe:
                        probe = p
                except Exception:
                    continue
            return {
                "status": "session verified — logged in to b2bsoft OK; Sales Transaction Details auto-"
                          "download pending one live calibration (the email feed keeps ingesting meanwhile)",
                "authenticated": True, "report_probe": probe, "diag": diag,
            }
        finally:
            browser.close()
