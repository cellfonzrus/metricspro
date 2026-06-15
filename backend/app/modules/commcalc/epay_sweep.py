"""epay Owner Portal MI + ATU auto-sweep (#5b) — runs INSIDE the backend (Railway) on a
schedule, replacing the manual MI/comp_report upload.

Unlike the VIP (ASP.NET form) and DLAR (Laravel form) sweeps, the epay Owner Portal
(ownerportal.epayworldwide.com) is a **WAF-protected JavaScriptMVC SPA** ("CarrierPortal" /
steal.js) whose reports are **SSRS downloads**. A plain `requests` login is rejected by the
WAF and can't render the SPA, so this sweep drives a **headless Chromium via Playwright**.

Discovered so far (2026-06-15, live probe from the dev Codespace — Chromium passes the WAF):
  • Login form (main document, rendered by loginWindow.ejs):
        input#usernameInput, input#passwordInput, select#application, button#loginButton
    (the `#application` <select> is populated dynamically; may be optional / defaulted.)
  • Reports are SSRS: the SPA loads report templates report.ejs / downloadreport.ejs /
    downloadssrsreport.ejs — i.e. each report is exported as a file (Excel) via an SSRS
    endpoint after selecting it + its parameters.

REMAINING TO FINISH THE CONNECTOR (needs a few more authenticated live iterations):
  1. Confirm the post-login success signal + whether #application must be set.
  2. Navigate to the MI report and the ATU report, set the date params, trigger the SSRS
     export, and capture the downloaded workbook.
  3. Map the MI workbook columns into commcalc.raw_mi (manual-upload signature:
     'SalesForceID','Subscriber Status'; recon needs col Z = MI payout, col AH = ATU payout)
     and the ATU figures, then wipe+insert the period like the DLAR sweep does.
Until (2)+(3) are mapped, run_epay_sweep() logs in (proving the path) and then raises
EpayPortalError so the admin UI shows a clear "report download not yet wired" status — the
infrastructure (config, schedule, creds, endpoints, admin page) is fully functional now and
the sweep degrades gracefully, exactly like the VIP/DLAR sweeps did before their parsers were
finalized.

Credentials + schedule live in commcalc.epay_sweep_config (BACKEND-ONLY; password never
returned to the browser). Driven by pg_cron -> POST /commcalc/epay/sweep/run-due.
"""
from datetime import datetime, timezone

DEFAULT_URL = "https://ownerportal.epayworldwide.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class EpayLoginError(Exception):
    """Login failed — surfaced to the admin UI without ever echoing the password."""


class EpayPortalError(Exception):
    """Portal reached but a later step (report download/parse) isn't available yet."""


def _login(page, user, pw):
    """Fill the CarrierPortal login window and submit. Raises EpayLoginError on failure."""
    page.wait_for_selector("#passwordInput", timeout=30000)
    # username may be a dynamic id; fall back to the discovered one then a generic text input.
    if page.query_selector("#usernameInput"):
        page.fill("#usernameInput", user)
    else:
        page.fill("input[type=text]", user)
    page.fill("#passwordInput", pw)
    btn = page.query_selector("#loginButton") or page.query_selector("button[type=submit]")
    if btn:
        btn.click()
    else:
        page.press("#passwordInput", "Enter")
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(3000)
    body = (page.content() or "").lower()
    if any(w in body for w in ("invalid", "incorrect", "failed")) and "passwordinput" in body:
        raise EpayLoginError("epay login failed — credentials rejected (or the application/2FA changed).")
    if not any(w in body for w in ("logout", "sign out", "report", "dashboard")):
        raise EpayLoginError("epay login did not reach a logged-in page — login form/flow may have changed.")


def run_epay_sweep(client, org_id, url, user, pw):
    """Launch headless Chromium, log into the epay Owner Portal, pull MI + ATU.

    Currently performs the login (proving the headless path works) and then raises
    EpayPortalError, because the SSRS MI/ATU report download + column mapping are not yet
    wired (see module docstring). Raises EpayLoginError if Playwright/Chromium isn't
    installed in the image, or if the login itself fails."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise EpayLoginError(
            "Playwright is not installed in the backend image. Add "
            "`RUN pip install playwright && playwright install --with-deps chromium` "
            "to backend/Dockerfile to enable the epay headless sweep.")

    base = (url or DEFAULT_URL).rstrip("/")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        try:
            page.goto(base, timeout=45000, wait_until="domcontentloaded")
            _login(page, user, pw)
            # --- report download + parse (TODO: map the SSRS MI/ATU export) ---
            raise EpayPortalError(
                "Logged in OK — MI/ATU SSRS report download is not yet wired. "
                "Finish _fetch_mi_report / _fetch_atu_report (see module docstring).")
        finally:
            browser.close()


def _period_now():
    """Current UTC month as ('June 2026', 6, 2026) for the wipe+insert key."""
    import calendar as _cal
    n = datetime.now(timezone.utc)
    return f"{_cal.month_name[n.month]} {n.year}", n.month, n.year
