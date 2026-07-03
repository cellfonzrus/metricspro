"""VidaPay / Total Access "Master Agent" portal login + report sweep (Total Wireless side).

Total Wireless dealers get their MI/ATU-equivalent commission data from the VidaPay CRM portal
(https://www.vidapaycrm.com/Main%20Panel.aspx), NOT Boost's ePay. Two things make this portal
different from every other sweep in the app and shape the whole design here:

  1. THREE-FIELD LOGIN. Auth needs Account ID + User ID + Password (ePay/VIP/DLAR use two).
  2. INTERACTIVE 2FA. After the password step the portal challenges with a code the operator
     receives out-of-band (email/SMS). That means login can't complete inside one request — a
     human has to fetch the code and hand it back. So login is a STATE MACHINE across two calls:
         begin_login(...)      -> reaches the 2FA challenge, returns the half-auth session
         complete_2fa(code)    -> submits the code, returns the durable authenticated session
     The browser session is carried between the two calls (and reused by later scheduled pulls)
     as Playwright **storage_state** (cookies/localStorage) persisted in commcalc.data_source —
     no long-lived browser process, so it survives Railway restarts and multiple workers.

  3. The portal sits behind **Cloudflare** (bot management) and is an ASP.NET SPA, so — like ePay
     — a plain `requests` login is rejected; this drives a headless Chromium via Playwright. The
     backend image already bundles Chromium (see backend/Dockerfile).

CALIBRATION NOTE: unlike ePay (reverse-engineered live 2026-06-15), the exact login/2FA/report
DOM of vidapaycrm.com has NOT been driven end-to-end yet — it is Cloudflare-gated and needs real
credentials to reach. The field finders below are therefore HEURISTIC (match by type + nearby
label/placeholder/name/id text, with fallbacks) and every failure path returns a DIAGNOSTIC
snapshot (page title, headings, the inputs it actually saw). The operator's FIRST real login is
the calibration pass: the diagnostic tells us precisely which selectors to pin. Nothing here is
hard-coded — credentials always come from the data_source row (UI config).
"""
from datetime import datetime, timezone, timedelta

DEFAULT_URL = "https://www.vidapaycrm.com/Main%20Panel.aspx"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# How long, best-effort, we assume an authenticated session stays valid before proactively
# marking it stale. The portal is the source of truth — expiry is ALSO detected on every use
# (a pull that lands back on the login/2FA form flips auth_status to needs_2fa regardless).
SESSION_TTL_HOURS = 8
# A freshly-captured pending (mid-2FA) session this old is treated as abandoned.
PENDING_TTL_MINUTES = 20


class VidaPayLoginError(Exception):
    """Login could not start — bad account/user/password, Cloudflare block, or Chromium missing.
    Surfaced to the admin UI; never echoes the password."""


class VidaPayAuthError(Exception):
    """The stored session is missing/expired — the operator must (re-)log in + pass 2FA."""


class VidaPayPortalError(Exception):
    """Logged in fine, but a later step (report navigation/download/parse) failed."""


# ── diagnostics ──────────────────────────────────────────────────────────────────────────────
def _snapshot(page):
    """A compact, credential-free description of the current page — returned on every ambiguous
    outcome so the first live login tells us exactly which selectors to pin (calibration)."""
    try:
        inputs = page.evaluate(
            """() => Array.from(document.querySelectorAll('input,button,select')).slice(0,40).map(e => ({
                 tag: e.tagName.toLowerCase(), type: (e.type||'').toLowerCase(),
                 name: e.name||'', id: e.id||'', ph: e.placeholder||'',
                 val: (e.tagName.toLowerCase()==='button'||e.type==='submit'||e.type==='button') ? (e.value||e.innerText||'').slice(0,30) : '',
                 vis: !!(e.offsetParent) }))""")
    except Exception:
        inputs = []
    try:
        heads = page.evaluate(
            """() => Array.from(document.querySelectorAll('h1,h2,h3,legend,label')).slice(0,20)
                     .map(e => (e.innerText||'').trim()).filter(Boolean)""")
    except Exception:
        heads = []
    return {"url": (page.url or "")[:200], "title": (page.title() or "")[:120],
            "headings": heads[:12], "controls": [c for c in inputs if c.get("vis")][:24]}


def _looks_like_cloudflare(page):
    try:
        t = (page.title() or "").lower()
        body = (page.content() or "").lower()
    except Exception:
        return False
    if any(k in t for k in ("just a moment", "attention required", "access denied")):
        return True
    return ("cf-chl" in body or "challenge-platform" in body or
            ("cloudflare" in body and "ray id" in body))


def _wait_out_cloudflare(page, timeout_s=25):
    """Cloudflare's __cf_bm managed challenge usually clears itself for a real Chromium within a
    few seconds. Give it a moment; a persistent interstitial (JS/interactive challenge) means the
    egress IP is being blocked — raise so the operator can point the sweep at an allow-listed IP."""
    import time
    waited = 0.0
    while _looks_like_cloudflare(page) and waited < timeout_s:
        page.wait_for_timeout(1500)
        waited += 1.5
    if _looks_like_cloudflare(page):
        raise VidaPayLoginError(
            "Cloudflare is blocking this egress IP (bot challenge did not clear). VidaPay must be "
            "reachable from an allow-listed / residential IP — the same WAF caveat as ePay.")


# ── heuristic field finders (credential-free; calibrated on first live login) ─────────────────
def _find_input(page, kinds=("text", "email", "tel", "number"), want=(), avoid=()):
    """Return a Playwright element handle for the first VISIBLE input whose type is in `kinds`
    and whose name/id/placeholder/aria/nearby-label text matches ANY `want` keyword and NO
    `avoid` keyword. Text-based so it survives ASP.NET's mangled control ids."""
    try:
        handles = page.query_selector_all("input, textarea")
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
                best = h  # first visible candidate of the right type as a fallback
        except Exception:
            continue
    return best


def _click_submit(page, texts):
    """Click the first visible button/submit whose text/value matches any of `texts`; falls back
    to pressing Enter in the focused field. `texts` are lowercase substrings."""
    try:
        cands = page.query_selector_all("button, input[type=submit], input[type=button], a[role=button]")
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
    try:
        page.keyboard.press("Enter")
        return True
    except Exception:
        return False


def _classify(page):
    """After a form submit, decide where we landed: 'twofa' | 'authenticated' | 'login' | 'unknown'."""
    try:
        body = (page.content() or "").lower()
    except Exception:
        body = ""
    has_pw = bool(page.query_selector("input[type=password]"))
    code_field = _find_input(page, kinds=("text", "tel", "number", "password"),
                             want=("code", "otp", "pin", "verif", "token", "authenticat", "2fa",
                                   "one-time", "onetime", "passcode", "security code"))
    twofa_words = ("verification code", "verify your", "two-factor", "two factor", "2-step",
                   "authentication code", "code sent", "enter the code", "one-time",
                   "we sent", "security code", "otp")
    if (code_field and not has_pw) or any(w in body for w in twofa_words):
        return "twofa"
    if has_pw:
        return "login"
    auth_words = ("logout", "log out", "sign out", "main panel", "dashboard", "commission",
                  "welcome", "my account")
    if any(w in body for w in auth_words):
        return "authenticated"
    return "unknown"


def _twofa_hint(page):
    """Pull a short 'code sent to j***@x' style hint from the challenge page if present."""
    try:
        txt = page.evaluate(
            """() => (document.body.innerText||'').split('\\n').map(s=>s.trim())
                     .filter(s => /code|sent|verif|otp|text|email|phone/i.test(s) && s.length<120)
                     .slice(0,3).join(' | ')""")
        return (txt or "")[:200] or None
    except Exception:
        return None


# ── phase 1: start login (fill 3 fields, submit, land on the 2FA challenge) ────────────────────
def begin_login(url, account_id, user, pw):
    """Launch headless Chromium, submit Account ID + User ID + Password, and report where we land.

    Returns one of:
      {"status": "needs_2fa",      "storage_state": {...}, "two_fa_hint": "...", "diag": {...}}
      {"status": "authenticated",  "storage_state": {...}, "diag": {...}}   # portal skipped 2FA
    Raises VidaPayLoginError on Cloudflare block, missing Chromium, or clearly-rejected creds
    (the exception message carries a diagnostic snapshot for calibration)."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise VidaPayLoginError(
            "Playwright/Chromium is not available in the backend image. Add "
            "`RUN playwright install --with-deps chromium` to backend/Dockerfile.")
    base_url = (url or DEFAULT_URL).strip() or DEFAULT_URL
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(user_agent=UA, accept_downloads=True, locale="en-US")
        page = ctx.new_page()
        try:
            page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
            _wait_out_cloudflare(page)
            page.wait_for_timeout(2500)
            # Some portals show User+Password first and only ask Account ID on a prior screen, or
            # vice-versa — fill whichever of the three fields are present on this screen.
            acct_el = _find_input(page, want=("account", "acct", "agent", "dealer", "merchant"),
                                  avoid=("user", "pass"))
            user_el = _find_input(page, want=("user", "login", "email", "userid", "username"),
                                  avoid=("account", "acct"))
            pw_el = page.query_selector("input[type=password]")
            if not pw_el:
                diag = _snapshot(page)
                raise VidaPayLoginError(
                    "Could not find the password field on the VidaPay login page — the login form "
                    f"may render differently than expected. Saw: {diag}")
            if acct_el and account_id:
                acct_el.fill(str(account_id))
            if user_el and user:
                user_el.fill(str(user))
            pw_el.fill(str(pw or ""))
            _click_submit(page, ("log in", "login", "sign in", "signin", "submit", "continue"))
            try:
                page.wait_for_load_state("networkidle", timeout=25000)
            except Exception:
                pass
            page.wait_for_timeout(3500)
            _wait_out_cloudflare(page)
            state = _classify(page)
            diag = _snapshot(page)
            if state == "twofa":
                return {"status": "needs_2fa", "storage_state": ctx.storage_state(),
                        "two_fa_hint": _twofa_hint(page), "diag": diag}
            if state == "authenticated":
                return {"status": "authenticated", "storage_state": ctx.storage_state(), "diag": diag}
            if state == "login":
                raise VidaPayLoginError(
                    "Login was rejected — Account ID / User ID / Password not accepted (still on the "
                    f"login form). Double-check the three credentials. Saw: {diag}")
            # unknown — return as needs_2fa-ish so the operator can try the code; carry the diag.
            return {"status": "needs_2fa", "storage_state": ctx.storage_state(),
                    "two_fa_hint": _twofa_hint(page),
                    "diag": {**diag, "_note": "post-login page not recognized as 2FA or app; "
                             "if no code field appears, send this diagnostic for calibration"}}
        finally:
            browser.close()


# ── phase 2: submit the 2FA code, promote the session to authenticated ─────────────────────────
def complete_2fa(url, pending_state, code):
    """Restore the mid-2FA session, submit the code, and return the durable authenticated session.

    Returns {"status": "authenticated", "storage_state": {...}, "diag": {...}}.
    Raises VidaPayAuthError if the code is rejected / the challenge expired."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise VidaPayLoginError("Playwright/Chromium is not available in the backend image.")
    if not pending_state:
        raise VidaPayAuthError("No pending login to verify — start the login again.")
    base_url = (url or DEFAULT_URL).strip() or DEFAULT_URL
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(user_agent=UA, accept_downloads=True, locale="en-US",
                                  storage_state=pending_state)
        page = ctx.new_page()
        try:
            page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
            _wait_out_cloudflare(page)
            page.wait_for_timeout(2500)
            if _classify(page) == "authenticated":
                # The session already promoted itself (e.g. "remember this device") — accept it.
                return {"status": "authenticated", "storage_state": ctx.storage_state(),
                        "diag": _snapshot(page)}
            code_el = _find_input(page, kinds=("text", "tel", "number", "password"),
                                  want=("code", "otp", "pin", "verif", "token", "authenticat",
                                        "2fa", "one-time", "onetime", "passcode"))
            if not code_el:
                code_el = _find_input(page, kinds=("text", "tel", "number"))  # lone field fallback
            if not code_el:
                raise VidaPayAuthError(
                    f"Could not find the 2FA code field to enter the code. Saw: {_snapshot(page)}")
            code_el.fill(str(code).strip())
            _click_submit(page, ("verify", "submit", "continue", "confirm", "log in", "sign in"))
            try:
                page.wait_for_load_state("networkidle", timeout=25000)
            except Exception:
                pass
            page.wait_for_timeout(3500)
            _wait_out_cloudflare(page)
            state = _classify(page)
            if state == "authenticated":
                return {"status": "authenticated", "storage_state": ctx.storage_state(),
                        "diag": _snapshot(page)}
            if state == "twofa":
                raise VidaPayAuthError(
                    "2FA code was not accepted (still on the verification screen) — check the code "
                    "and try again; it may have expired, request a new one.")
            # Unknown but no longer on login/2FA — save the session and let the health check decide.
            return {"status": "authenticated", "storage_state": ctx.storage_state(),
                    "diag": {**_snapshot(page), "_note": "post-2FA page not definitively recognized"}}
        finally:
            browser.close()


# ── session health check + report pull (report nav calibrated on first authenticated login) ────
def run_vidapay_sweep(client, org_id, url, session_state, source_id=None, carrier_id=None):
    """Restore the authenticated session and pull reports. Right now this VERIFIES the stored
    session is still alive (the hard, novel part — login + 2FA + persistence) and reports what the
    authenticated portal exposes, so report auto-download can be pinned in one calibration pass.

    Raises VidaPayAuthError if the session is missing/expired (caller flips status to needs_2fa)."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise VidaPayLoginError("Playwright/Chromium is not available in the backend image.")
    if not session_state:
        raise VidaPayAuthError("Not authenticated yet — click “Log in” and complete 2FA first.")
    base_url = (url or DEFAULT_URL).strip() or DEFAULT_URL
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(user_agent=UA, accept_downloads=True, locale="en-US",
                                  storage_state=session_state)
        page = ctx.new_page()
        try:
            page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
            _wait_out_cloudflare(page)
            page.wait_for_timeout(2500)
            state = _classify(page)
            if state in ("login", "twofa"):
                raise VidaPayAuthError(
                    "The VidaPay session has expired — please re-authenticate (Log in + 2FA).")
            # Session is alive. Surface what the authenticated portal exposes so report auto-pull
            # can be calibrated. (Report menu discovery + download/ingest is the next build step —
            # reports import via Data Imports / the email sweep in the meantime.)
            diag = _snapshot(page)
            try:
                links = page.evaluate(
                    """() => Array.from(document.querySelectorAll('a,button,span.k-link,li'))
                             .map(e => (e.innerText||'').trim())
                             .filter(t => /commission|report|daily|device|residual|activation|fulfil|download|export/i.test(t))
                             .filter((v,i,a)=>a.indexOf(v)===i).slice(0,25)""")
            except Exception:
                links = []
            return {
                "status": "session verified — logged in OK; report auto-download pending one live "
                          "calibration (import the MA reports via Data Imports / email sweep for now)",
                "authenticated": True, "report_links_seen": links, "diag": diag,
            }
        finally:
            browser.close()
