"""epay Owner Portal MI + ATU auto-sweep (#5b) — runs INSIDE the backend (Railway) on a
schedule, replacing the manual MI/comp_report upload.

Unlike the VIP (ASP.NET form) and DLAR (Laravel form) sweeps, the epay Owner Portal
(ownerportal.epayworldwide.com) is a **WAF-protected JavaScriptMVC SPA** ("CarrierPortal" /
steal.js) whose reports are exported as **Excel downloads**. A plain `requests` login is
rejected by the WAF and can't render the SPA, so this sweep drives a **headless Chromium via
Playwright**.

REVERSE-ENGINEERED & VERIFIED END-TO-END 2026-06-15 (live, from the dev Codespace):
  • Login form (main document): input#usernameInput, input#passwordInput, button#loginButton.
  • Report menu is a Kendo menu: top-level "Commissions" -> submenu items keyed by report id.
    The MI/ATU report is "Monthly Incentive & ATU Subscriber Details", id 102817 — and it
    carries BOTH the MI and the ATU columns, so a single download covers both.
  • Opening a report renders a filter panel (Month defaults to the CURRENT month) + a toolbar
    with "Run Report" and a "Download Report" split-button.
  • Flow: hover Commissions -> click #102817 -> "Run Report" -> wait for the run to finish
    (the spinner/Cancel button disappears) -> "Download Report" -> "as Excel spreadsheet"
    -> the browser downloads an .xlsx whose columns are IDENTICAL to the manual MI upload
    ('SalesForceID','Subscriber Status', ... ,'Actual MI Payout Amount','Actual ATU Payout
    Amount'). 38,508 rows for the current month in the live test.
  • The workbook is then mapped into commcalc.raw_mi via map_mi_records() — the SAME mapper
    the manual upload uses (single source of truth) — and the period is wiped + re-inserted,
    exactly like the DLAR sweep.

Credentials + schedule live in commcalc.epay_sweep_config (BACKEND-ONLY; password never
returned to the browser). Driven by pg_cron -> POST /commcalc/epay/sweep/run-due.

DEPLOY NOTE: the backend image must include Chromium — see backend/Dockerfile
(`RUN playwright install --with-deps chromium`). Without it run_epay_sweep() raises a clear
EpayLoginError telling the operator to add it (graceful degradation). The portal is also
WAF-protected, so Railway's datacenter IP must be allowed to reach it; if a run reports a WAF
"Request Rejected", the sweep can be pointed at a residential/allow-listed egress.
"""
from datetime import datetime, timezone

DEFAULT_URL = "https://ownerportal.epayworldwide.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# "Monthly Incentive & ATU Subscriber Details" — the one report that carries MI AND ATU.
MI_REPORT_ID = "102817"
# How long a single month's MI/ATU run may take before we give up (seconds).
REPORT_RUN_TIMEOUT_S = 360


class EpayLoginError(Exception):
    """Login failed / Chromium missing — surfaced to the admin UI, never echoing the password."""


class EpayPortalError(Exception):
    """Portal reached + logged in, but a later step (run/download/parse) failed."""


# ── MI/ATU column mapping (shared by the manual upload AND this sweep) ─────────────────────
def _mi_date(v):
    s = str(v or "").strip()
    return s[:10] if s and s.lower() not in ("nat", "nan", "none", "") else None


def map_mi_row(r, base):
    """Map one MI/ATU report row (dict keyed by the report's column headers) into a
    commcalc.raw_mi row dict. `base` carries org_id + period/period_month/period_year.
    Identical for the manual upload and the portal sweep so both produce the same raw_mi."""
    from app.modules.commcalc.calculator import safe_float
    return {
        **base,
        "salesforce_id": r.get("SalesForceID", ""),
        "subscriber_id": r.get("SubscriberID", ""),
        "subscriber_status": r.get("Subscriber Status", ""),
        "phone_number": str(r.get("Phone Number", "")).replace(".0", "").strip(),
        "device_serial": str(r.get("Device Serial", "")).replace(".0", "").strip(),
        "mi_activation_date": _mi_date(r.get("MI Activation Date")),
        "mi_deactivation_date": _mi_date(r.get("MI Deactivation Date")),
        "residual_transfer_in_date": _mi_date(r.get("Residual Transfer In Date")),
        "residual_transfer_out_date": _mi_date(r.get("Residual Transfer Out Date")),
        "customer_plan": r.get("Customer Plan", ""),
        "base_mrc": safe_float(r.get("Base MRC Amount")),
        "commissionable_mrc": safe_float(r.get("Commissionable MRC Amount")),
        "actual_mi_payout": safe_float(r.get("Actual MI Payout Amount")),
        "actual_atu_payout": safe_float(r.get("Actual ATU Payout Amount")),
        "rep_username": r.get("Rep Username", ""),
        "door_type": r.get("Door Type", ""),
        "report_month": r.get("Report Month", ""),
    }


def map_mi_records(records, base):
    """Map + filter a list of MI/ATU report rows into raw_mi rows (drops empty rows)."""
    org_id = base.get("org_id")
    out = []
    for r in records:
        row = map_mi_row(r, base)
        if any(v for v in row.values() if v and v != org_id):
            out.append(row)
    return out


# ── headless-browser login + report download ──────────────────────────────────────────────
def _login(page, user, pw):
    """Fill the CarrierPortal login window and submit. Raises EpayLoginError on failure."""
    page.wait_for_selector("#passwordInput", timeout=30000)
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
    page.wait_for_timeout(4000)
    title = page.title() or ""
    body = (page.content() or "").lower()
    if "owner portal" in title.lower():
        return  # the post-login app shell renders this title
    if any(w in body for w in ("invalid", "incorrect", "failed")) and "passwordinput" in body:
        raise EpayLoginError("epay login failed — credentials rejected (or the application/2FA changed).")
    if not any(w in body for w in ("logout", "sign out", "commissions", "report", "dashboard")):
        raise EpayLoginError("epay login did not reach a logged-in page — login form/flow may have changed.")


def _report_running(page):
    """True while the report run spinner / Cancel button is up."""
    return page.evaluate(
        "() => {"
        "  const el = document.querySelector('.reportloading');"
        "  if (el && el.offsetParent !== null) return true;"
        "  const cancels = [...document.querySelectorAll('button,input,div')].filter("
        "    e => (e.textContent||e.value||'').trim() === 'Cancel' && e.offsetParent);"
        "  return cancels.length > 0;"
        "}"
    )


def _wait_report_done(page, timeout_s=REPORT_RUN_TIMEOUT_S):
    page.wait_for_timeout(2000)  # let the spinner appear
    waited = 2000
    while waited < timeout_s * 1000:
        try:
            if not _report_running(page):
                return
        except Exception:
            pass
        page.wait_for_timeout(3000)
        waited += 3000
    raise EpayPortalError(f"MI/ATU report did not finish running within {timeout_s}s.")


def _open_and_download(page, report_id, dest_path):
    """Open a Commissions report by its menu id, run it for the current month, save the .xlsx."""
    page.hover("span.k-link:has-text('Commissions')", timeout=20000)
    page.wait_for_timeout(1500)
    page.wait_for_selector(f'[id="{report_id}"]', state="visible", timeout=20000)
    page.click(f'[id="{report_id}"]')
    page.wait_for_timeout(5000)
    # run (Month defaults to the current month)
    page.click("text=Run Report", timeout=20000)
    _wait_report_done(page)
    # download as Excel
    with page.expect_download(timeout=120000) as dl_info:
        page.click("text=Download Report", timeout=30000)
        page.wait_for_timeout(1500)
        page.click("text=as Excel spreadsheet", timeout=20000)
    dl_info.value.save_as(dest_path)


def _download_mi_report(page, dest_path):
    """Open the MI/ATU report (#102817), run it, and save the .xlsx to dest_path."""
    _open_and_download(page, MI_REPORT_ID, dest_path)


def discover_reports(url, user, pw):
    """Log in and enumerate the Commissions report menu → [{id, label}]. MUST run server-side
    (the portal WAF only allows the Railway egress IP). Used to find the report ids of the
    Commission Payment Detail + Comprehensive Compensation reports so they can be swept too."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise EpayLoginError(
            "Playwright is not installed in the backend image (add it to backend/Dockerfile).")
    base_url = (url or DEFAULT_URL).rstrip("/")
    items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        try:
            page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
            _login(page, user, pw)
            page.hover("span.k-link:has-text('Commissions')", timeout=20000)
            page.wait_for_timeout(2000)
            items = page.evaluate(
                "() => {"
                "  const out = [];"
                "  document.querySelectorAll('[id]').forEach(el => {"
                "    const id = (el.getAttribute('id')||'').trim();"
                "    const txt = (el.textContent||'').trim();"
                "    if (/^[0-9]{4,}$/.test(id) && txt && txt.length < 120) out.push({id, label: txt});"
                "  });"
                "  return out;"
                "}")
        finally:
            browser.close()
    seen = {}
    for it in (items or []):
        seen.setdefault(it["id"], it["label"])
    return [{"id": k, "label": v} for k, v in seen.items()]


def run_epay_sweep(client, org_id, url, user, pw):
    """Launch headless Chromium, log into the epay Owner Portal, download the Monthly
    Incentive & ATU Subscriber Details report for the current month, and wipe+insert the
    period into commcalc.raw_mi (replacing the manual MI upload).

    Returns a summary dict. Raises EpayLoginError if Chromium/Playwright isn't installed or
    login fails; EpayPortalError if a later step (run/download/parse) fails."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise EpayLoginError(
            "Playwright is not installed in the backend image. Add "
            "`RUN pip install playwright && playwright install --with-deps chromium` "
            "to backend/Dockerfile to enable the epay headless sweep.")
    import pandas as pd
    import tempfile
    import os

    base_url = (url or DEFAULT_URL).rstrip("/")
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    xlsx_path = tmp.name

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = browser.new_context(user_agent=UA, accept_downloads=True)
            page = ctx.new_page()
            try:
                page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
                _login(page, user, pw)
                _download_mi_report(page, xlsx_path)
            finally:
                browser.close()

        # parse + map (same mapper as the manual upload) + wipe/insert the period
        df = pd.read_excel(xlsx_path, dtype=str).fillna("")
        records = df.to_dict("records")
        if not records:
            raise EpayPortalError("MI/ATU report downloaded but contained no rows.")
        report_month = ""
        for r in records:
            if str(r.get("Report Month", "")).strip():
                report_month = str(r.get("Report Month")).strip()
                break
        period, pm, py = _period_from_report_month(report_month)
        base = {"org_id": org_id, "period": period, "period_month": pm, "period_year": py}
        rows = map_mi_records(records, base)

        client.schema("commcalc").table("raw_mi").delete() \
            .eq("org_id", org_id).eq("period", period).execute()
        saved = 0
        for i in range(0, len(rows), 500):
            batch = rows[i:i + 500]
            client.schema("commcalc").table("raw_mi").insert(batch).execute()
            saved += len(batch)

        # best-effort: record it on the Upload page's history (never fail the sweep on this)
        try:
            client.schema("commcalc").table("upload_log").insert({
                "org_id": org_id, "file_type": "mi_report", "period": period,
                "filename": "epay auto-sweep", "rows_saved": saved}).execute()
        except Exception:
            pass

        return {"period": period, "rows": saved, "report_month": report_month}
    finally:
        try:
            os.unlink(xlsx_path)
        except Exception:
            pass


def _period_from_report_month(rm):
    """'June - 2026' or 'June 2026' -> ('June 2026', 6, 2026). Falls back to the current
    UTC month if the report-month text is missing/unparseable."""
    import calendar as _cal
    import re as _re
    s = (rm or "").replace(" - ", " ").strip()
    m = _re.match(r"([A-Za-z]+)\s+(\d{4})", s)
    if m:
        name, yr = m.group(1).capitalize(), int(m.group(2))
        months = {mn: i for i, mn in enumerate(_cal.month_name) if mn}
        if name in months:
            return f"{name} {yr}", months[name], yr
    return _period_now()


def _period_now():
    """Current UTC month as ('June 2026', 6, 2026) for the wipe+insert key."""
    import calendar as _cal
    n = datetime.now(timezone.utc)
    return f"{_cal.month_name[n.month]} {n.year}", n.month, n.year
