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
_STEALTH_JS = (
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    "window.chrome=window.chrome||{runtime:{}};"
    "Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});"
    "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
)
_LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"]
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


def _new_context(browser, storage_state=None, proxy=None):
    """A desktop-Chrome context with the anti-automation shims applied before any page script runs.
    `proxy` is a Playwright proxy dict (see _proxy_arg) routing the session through a given egress."""
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


def _looks_like_cloudflare(page):
    body = _page_text(page)
    return ("cf-chl" in body or "challenge-platform" in body or
            ("cloudflare" in body and "ray id" in body) or "just a moment" in body)


def _looks_like_bot_wall(page):
    body = _page_text(page)
    return any(p in body for p in _BOT_PHRASES)


def _wait_settle(page, timeout_s=25):
    """Give Cloudflare's managed challenge time to clear for a real Chromium. A persistent
    interstitial means the egress IP is being blocked."""
    import time
    waited = 0.0
    while _looks_like_cloudflare(page) and waited < timeout_s:
        page.wait_for_timeout(1500)
        waited += 1.5
    if _looks_like_cloudflare(page):
        raise VidaPayLoginError(
            "Cloudflare is blocking this egress IP (bot challenge did not clear). VidaPay must be "
            "reachable from an allow-listed / residential IP — the same WAF caveat as ePay.")


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


def _classify(page):
    """Where did we land: 'twofa' | 'authenticated' | 'login' | 'botwall' | 'unknown'."""
    if _looks_like_bot_wall(page) and not _password_frame(page)[1]:
        return "botwall"
    body = _page_text(page)
    fr, pw = _password_frame(page)
    code_field = None
    for f in _frames(page):
        code_field = _find_input(f, kinds=("text", "tel", "number", "password"),
                                 want=("code", "otp", "pin", "verif", "token", "authenticat", "2fa",
                                       "one-time", "onetime", "passcode", "security code"))
        if code_field:
            break
    twofa_words = ("verification code", "verify your", "two-factor", "two factor", "2-step",
                   "authentication code", "code sent", "enter the code", "one-time",
                   "we sent", "security code", "otp")
    if (code_field and not pw) or any(w in body for w in twofa_words):
        return "twofa"
    if pw:
        return "login"
    auth_words = ("logout", "log out", "sign out", "main panel", "dashboard", "commission",
                  "welcome", "my account")
    if any(w in body for w in auth_words):
        return "authenticated"
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


def _goto_login(page, base_url):
    """Navigate to the portal and settle. If it lands on the bot-wall with no form, retry straight
    at the id-server login endpoint (deep links are flagged more often than the login URL itself)."""
    page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
    _wait_settle(page)
    page.wait_for_timeout(2500)
    fr, pw = _wait_for_password(page, timeout_s=20)
    if pw:
        return
    if _looks_like_bot_wall(page):
        try:
            page.goto(LOGIN_URL, timeout=60000, wait_until="domcontentloaded")
            _wait_settle(page)
            _wait_for_password(page, timeout_s=20)
        except VidaPayLoginError:
            raise
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
    base_url = (url or DEFAULT_URL).strip() or DEFAULT_URL
    proxy = _proxy_arg(proxy_url)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        ctx = _new_context(browser, proxy=proxy)
        page = ctx.new_page()
        try:
            _goto_login(page, base_url)
            login_fr, pw_el = _password_frame(page)
            if not pw_el:
                diag = _snapshot(page)
                if _looks_like_bot_wall(page):
                    raise VidaPayLoginError(
                        "VidaPay served an anti-automation page (\"Something doesn't look right\") instead "
                        "of the login form — the portal flagged this browser/IP. It must be reached from "
                        "an allow-listed / residential IP (same WAF caveat as ePay). Diagnostic: " + str(diag))
                raise VidaPayLoginError(
                    "Could not find the password field on the VidaPay login page — the form may render "
                    "differently than expected. Diagnostic: " + str(diag))
            # Fill whichever of the three fields exist in the login frame.
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
            try:
                page.wait_for_load_state("networkidle", timeout=25000)
            except Exception:
                pass
            page.wait_for_timeout(3500)
            _wait_settle(page)
            state = _classify(page)
            diag = _snapshot(page)
            if state == "twofa":
                return {"status": "needs_2fa", "storage_state": ctx.storage_state(),
                        "two_fa_hint": _twofa_hint(page), "diag": diag}
            if state == "authenticated":
                return {"status": "authenticated", "storage_state": ctx.storage_state(), "diag": diag}
            if state == "botwall":
                raise VidaPayLoginError(
                    "VidaPay served an anti-automation page after the login submit — reach it from an "
                    "allow-listed / residential IP. Diagnostic: " + str(diag))
            if state == "login":
                raise VidaPayLoginError(
                    "Login was rejected — Account ID / User ID / Password not accepted (still on the "
                    "login form). Double-check the three credentials. Diagnostic: " + str(diag))
            return {"status": "needs_2fa", "storage_state": ctx.storage_state(),
                    "two_fa_hint": _twofa_hint(page),
                    "diag": {**diag, "_note": "post-login page not recognized as 2FA or app; if no "
                             "code field appears, send this diagnostic for calibration"}}
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
    base_url = (url or B2BSOFT_URL).strip() or B2BSOFT_URL
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        ctx = _new_context(browser, proxy=_proxy_arg(proxy_url))
        page = ctx.new_page()
        try:
            _goto_login(page, base_url)
            page.wait_for_timeout(1800)

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
                return {"status": "needs_2fa", "storage_state": ctx.storage_state(),
                        "two_fa_hint": _twofa_hint(page), "diag": d2}
            state = _classify(page)
            diag = _snapshot(page)
            try:
                diag = {**diag, "filled": _filled, "portal_error": _b2b_error(page)}
            except Exception:
                pass
            if state == "twofa":
                return {"status": "needs_2fa", "storage_state": ctx.storage_state(),
                        "two_fa_hint": _twofa_hint(page), "diag": diag}
            if state == "authenticated":
                return {"status": "authenticated", "storage_state": ctx.storage_state(), "diag": diag}
            if state == "botwall":
                raise VidaPayLoginError(
                    "b2bsoft served an anti-automation page after login — set a residential proxy. Diagnostic: " + str(diag))
            if state == "login":
                raise VidaPayLoginError(
                    "Login rejected — still on the login form. What was typed + the portal's own error are in the "
                    "diagnostic (filled / portal_error) — if 'filled' shows your values, the creds/Access-Code are "
                    "being refused; if empty, the form didn't accept the fill. Diagnostic: " + str(diag))
            return {"status": "needs_2fa", "storage_state": ctx.storage_state(),
                    "two_fa_hint": _twofa_hint(page),
                    "diag": {**diag, "_note": "post-login page not recognized as 2FA/app; send this diagnostic"}}
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
    base_url = (url or DEFAULT_URL).strip() or DEFAULT_URL
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        ctx = _new_context(browser, storage_state=pending_state, proxy=_proxy_arg(proxy_url))
        page = ctx.new_page()
        try:
            page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
            _wait_settle(page)
            page.wait_for_timeout(2500)
            if _classify(page) == "authenticated":
                return {"status": "authenticated", "storage_state": ctx.storage_state(),
                        "diag": _snapshot(page)}
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
            _wait_settle(page)
            state = _classify(page)
            if state == "authenticated":
                return {"status": "authenticated", "storage_state": ctx.storage_state(),
                        "diag": _snapshot(page)}
            if state == "twofa":
                raise VidaPayAuthError(
                    "2FA code was not accepted (still on the verification screen) — check the code and "
                    "try again; it may have expired, request a new one.")
            return {"status": "authenticated", "storage_state": ctx.storage_state(),
                    "diag": {**_snapshot(page), "_note": "post-2FA page not definitively recognized"}}
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
    base_url = (url or B2BSOFT_URL).strip() or B2BSOFT_URL
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        ctx = _new_context(browser, storage_state=pending_state, proxy=_proxy_arg(proxy_url))
        page = ctx.new_page()
        try:
            # Restoring the mid-2FA session + hitting the portal resumes the pending 2FA challenge.
            page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
            _wait_settle(page)
            page.wait_for_timeout(2500)
            on_2fa = bool(page.query_selector("#TwoFactorCode")) or "twofactor" in (page.url or "").lower()
            if not on_2fa and _classify(page) == "authenticated":
                return {"status": "authenticated", "storage_state": ctx.storage_state(), "diag": _snapshot(page)}
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
            return {"status": "authenticated", "storage_state": ctx.storage_state(),
                    "diag": {**_snapshot(page), "_note": "post-2FA (b2bsoft)"}}
        finally:
            browser.close()


# ── session health check + report pull ─────────────────────────────────────────────────────────
def run_vidapay_sweep(client, org_id, url, session_state, source_id=None, carrier_id=None, proxy_url=None):
    """Restore the authenticated session and verify it's still alive (login + 2FA + persistence —
    the hard part), reporting what the authenticated portal exposes so report auto-download can be
    pinned in one calibration pass. Raises VidaPayAuthError if the session is missing/expired."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise VidaPayLoginError("Playwright/Chromium is not available in the backend image.")
    if not session_state:
        raise VidaPayAuthError("Not authenticated yet — click “Log in” and complete 2FA first.")
    base_url = (url or DEFAULT_URL).strip() or DEFAULT_URL
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        ctx = _new_context(browser, storage_state=session_state, proxy=_proxy_arg(proxy_url))
        page = ctx.new_page()
        try:
            page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
            _wait_settle(page)
            page.wait_for_timeout(2500)
            state = _classify(page)
            if state in ("login", "twofa", "botwall"):
                raise VidaPayAuthError(
                    "The VidaPay session has expired — please re-authenticate (Log in + 2FA).")
            diag = _snapshot(page)
            links = []
            for fr in _frames(page):
                try:
                    links += fr.evaluate(
                        """() => Array.from(document.querySelectorAll('a,button,span.k-link,li'))
                                 .map(e => (e.innerText||'').trim())
                                 .filter(t => /commission|report|daily|device|residual|activation|fulfil|download|export/i.test(t))
                                 .filter((v,i,a)=>a.indexOf(v)===i).slice(0,25)""")
                except Exception:
                    continue
            return {
                "status": "session verified — logged in OK; report auto-download pending one live "
                          "calibration (import the MA reports via Data Imports / email sweep for now)",
                "authenticated": True, "report_links_seen": links[:25], "diag": diag,
            }
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
    base_url = (url or B2BSOFT_URL).strip() or B2BSOFT_URL
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        ctx = _new_context(browser, storage_state=session_state, proxy=_proxy_arg(proxy_url))
        page = ctx.new_page()
        try:
            page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
            _wait_settle(page)
            page.wait_for_timeout(2500)
            state = _classify(page)
            if state in ("login", "twofa", "botwall"):
                raise VidaPayAuthError(
                    "The b2bsoft session has expired (or the datacenter IP was walled) — please re-"
                    "authenticate (Log in + 2FA). If it keeps walling, set a residential proxy on the source.")
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
