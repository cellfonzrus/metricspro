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
    return _map_filtered(records, base, map_mi_row)


def _map_filtered(records, base, row_fn):
    org_id = base.get("org_id")
    out = []
    for r in records:
        row = row_fn(r, base)
        if any(v for v in row.values() if v and v != org_id):
            out.append(row)
    return out


# ── Commission Payment Detail (#50273) → raw_payment_detail (shared by upload + sweep) ───────
def map_payment_detail_row(r, base):
    from app.modules.commcalc.calculator import safe_float
    return {
        **base,
        "business_address": r.get("Business Address", ""),
        "payment_type": r.get("Payment Type", ""),
        "amount": safe_float(r.get("Amount")),
        "mdn": str(r.get("Phone Number", r.get("MDN", ""))).replace(".0", "").strip(),
        "imei": str(r.get("IMEI", "")).replace(".0", "").strip(),
        "payment_date": str(r.get("Payment Date", ""))[:10] or None,
        "rep_username": r.get("Rep Username", ""),
    }


# ── Comprehensive Compensation Report (#100614) → raw_comp_report ────────────────────────────
# The manual upload previously had NO comp parser (it stored empty rows via the else-branch);
# this is the real mapping, now shared by the upload + the sweep. The full per-payment grain is
# captured (incl. external_reference_id) so the comp is stored VERBATIM and the Residual Trend
# report can track each payment across months.
# COMP CADENCE (user 2026-06-20): each daily pull is the carrier's cumulative month-to-date snapshot,
# so the sweep REPLACES the open month (delete current period + insert). A canceled account that drops
# out of the report correctly disappears — a merge/upsert would keep it stale and mask the residual
# dip. Closed months are a different `period`, so they're never re-pulled and stay frozen. An empty
# in-arrears pull raises before touching the table (guard in _process_report), so a not-yet-posted
# month is never wiped.


def _comp_get(r, *names):
    """First non-empty value among candidate column spellings. The export uses no-space
    'OwnerID' / 'ExternalReferenceID' headers; spaced variants are tolerated too."""
    for n in names:
        v = r.get(n)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _comp_ref(row):
    """Stable conflict key for a comp payment. Uses the per-payment ExternalReferenceID when the
    export provides one; otherwise a deterministic content hash so a re-pull overwrites the same
    logical row rather than appending a duplicate."""
    import hashlib
    ref = row.get("external_reference_id") or ""
    if ref:
        return ref
    parts = [str(row.get(k, "")) for k in (
        "begin_date", "end_date", "account_id", "terminal_id", "owner_id",
        "compensation_type", "brand", "business_address", "payment_amount", "quantity")]
    return "h:" + hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def map_comp_report_row(r, base):
    # quantity is an INTEGER column — safe_float would hand PostgREST '1.0' and Postgres would
    # reject the whole batch with 22P02 on row 0. See calculator.safe_int.
    from app.modules.commcalc.calculator import safe_float, safe_int
    row = {
        **base,
        "begin_date": str(_comp_get(r, "Begin Date", "BeginDate"))[:10] or None,
        "end_date": str(_comp_get(r, "End Date", "EndDate"))[:10] or None,
        "retailer_account": _comp_get(r, "Retailer Account", "RetailerAccount"),
        "owner_id": _comp_get(r, "OwnerID", "Owner ID"),
        "terminal_id": _comp_get(r, "TerminalID", "Terminal ID"),
        "account_id": _comp_get(r, "AccountID", "Account ID"),
        "business_name": _comp_get(r, "Business Name", "BusinessName"),
        "business_address": _comp_get(r, "Business Address", "BusinessAddress"),
        "compensation_type": _comp_get(r, "Compensation Type", "CompensationType"),
        "brand": _comp_get(r, "Brand"),
        "salesforce_id": _comp_get(r, "SalesForce ID", "Salesforce ID", "SalesForceID"),
        "quantity": safe_int(_comp_get(r, "Quantity")),
        "payment_amount": safe_float(_comp_get(r, "Payment Amount", "PaymentAmount")),
        "external_reference_id": _comp_get(r, "ExternalReferenceID", "External Reference ID"),
        "has_payment_detail": _comp_get(r, "HasPaymentDetail", "Has Payment Detail"),
        "internal_brand": _comp_get(r, "InternalBrand", "Internal Brand"),
    }
    row["external_reference_id"] = _comp_ref(row)
    return row


# Commission report ids discovered from the portal's Commissions menu (via discover_reports).
PAYMENT_DETAIL_REPORT_ID = "50273"
COMP_REPORT_ID = "100614"

# Comp posts in ARREARS (a closed month's compensation keeps accruing for weeks after month-end),
# so each sweep refreshes the current month PLUS the most recent closed months. The portal's report
# defaults its Month filter to the current month, which is why a just-closed month's comp was never
# auto-swept (the design gap). _set_report_month drives the filter to each target month, and — the
# key safety — every pull is stored under the period implied by its own rows' Begin Date
# (period mode "data"), so even if the filter doesn't move, a pull can never be MISLABELED.
COMP_REFRESH_MONTHS = 3  # current + 2 prior closed months

# Partial-collapse guard: a REPLACE never overwrites a period that already holds >= REPLACE_MIN_ROWS
# rows with a pull smaller than REPLACE_MIN_RETAIN of that count. A glitched/partial pull (e.g. the
# portal returning a single stray row) is non-empty, so the empty-download guard misses it — this is
# what silently reduced a populated month to one account. Below the floor (a month just starting, or
# a first-ever load) the guard does nothing, so normal growth is unaffected.
REPLACE_MIN_ROWS = 50
REPLACE_MIN_RETAIN = 0.5

# Report registry: key → how to download + map + store. MI derives its period from the report's
# "Report Month" column; comp derives it from the data's Begin Date ("data"); payment uses the
# current month ("current", the portal's default Month filter).
REPORTS = {
    "mi": {"report_id": MI_REPORT_ID, "table": "raw_mi", "file_type": "mi_report",
           "period": "report_month", "map": map_mi_records, "label": "MI/ATU"},
    "payment_detail": {"report_id": PAYMENT_DETAIL_REPORT_ID, "table": "raw_payment_detail",
                       "file_type": "payment_detail", "period": "current", "label": "Commission Payment Detail",
                       "map": lambda recs, base: _map_filtered(recs, base, map_payment_detail_row)},
    "comp_report": {"report_id": COMP_REPORT_ID, "table": "raw_comp_report",
                    "file_type": "comp_report", "period": "data", "label": "Comprehensive Comp",
                    # REPLACE the open month each pull (cumulative MTD snapshot); empty + partial pulls
                    # are guarded, and the period comes from the rows' Begin Date so it's never mislabeled.
                    "map": lambda recs, base: _map_filtered(recs, base, map_comp_report_row)},
}


def comp_month_spread(records):
    """[(period, row_count), ...] newest-first for EVERY month present in a comp file.

    The manual upload stamps ONE operator-selected period onto every row, so a file spanning more
    than one month silently files (say) June and August rows under July and replaces July with all
    three. The dominant-month guard cannot see that — it only compares the winner. This exposes the
    whole spread so the upload can refuse a multi-month file and say which months it found."""
    import calendar as _cal
    from collections import Counter
    c = Counter()
    for r in records:
        bd = _comp_get(r, "Begin Date", "BeginDate")
        mo = yr = None
        if len(bd) >= 10 and bd[2] == "/" and bd[5] == "/":
            try:
                mo, yr = int(bd[0:2]), int(bd[6:10])
            except ValueError:
                pass
        elif len(bd) >= 7 and bd[4] == "-":
            try:
                yr, mo = int(bd[0:4]), int(bd[5:7])
            except ValueError:
                pass
        if mo and yr and 1 <= mo <= 12:
            c[(mo, yr)] += 1
    return [(f"{_cal.month_name[m]} {y}", n)
            for (m, y), n in sorted(c.items(), key=lambda kv: (kv[0][1], kv[0][0]), reverse=True)]


def comp_period_from_records(records):
    """Dominant (period, month, year) implied by a comp report's rows via their Begin Date — e.g.
    rows dated '04/29/2026' -> ('April 2026', 4, 2026). Used so a comp pull is stored under the
    month its data actually belongs to, regardless of what the portal's Month filter was set to.
    Returns None when no Begin Date is parseable."""
    import calendar as _cal
    from collections import Counter
    c = Counter()
    for r in records:
        bd = _comp_get(r, "Begin Date", "BeginDate")
        mo = yr = None
        if len(bd) >= 10 and bd[2] == "/" and bd[5] == "/":      # MM/DD/YYYY
            try:
                mo, yr = int(bd[0:2]), int(bd[6:10])
            except ValueError:
                pass
        elif len(bd) >= 7 and bd[4] == "-":                       # YYYY-MM-DD
            try:
                yr, mo = int(bd[0:4]), int(bd[5:7])
            except ValueError:
                pass
        if mo and yr and 1 <= mo <= 12:
            c[(mo, yr)] += 1
    if not c:
        return None
    (mo, yr), _n = c.most_common(1)[0]
    return (f"{_cal.month_name[mo]} {yr}", mo, yr)


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
    # First wait for the run to actually START (spinner/Cancel appears, up to ~8s) so we don't
    # conclude "done" and download a stale/empty export before the report has begun running —
    # the cause of the "downloaded but contained no rows" failure when the Run click mis-fires.
    waited = 0
    while waited < 8000:
        try:
            if _report_running(page):
                break
        except Exception:
            pass
        page.wait_for_timeout(500)
        waited += 500
    # Then wait for it to FINISH.
    waited = 0
    while waited < timeout_s * 1000:
        try:
            if not _report_running(page):
                return
        except Exception:
            pass
        page.wait_for_timeout(3000)
        waited += 3000
    raise EpayPortalError(f"report did not finish running within {timeout_s}s.")


def _click_visible(page, text, timeout=20000):
    """Click the first VISIBLE element whose text matches `text`.

    The portal's jqx toolbar renders hidden duplicate controls (a toolbar separator reuses the
    "Run Report" label) and, when several reports are opened in one browser session, the toolbar
    buttons accumulate. A bare `text=` selector then resolves to multiple nodes and Playwright
    clicks the FIRST — often an invisible separator — which either times out (Comprehensive Comp)
    or silently no-ops so the report never runs and the export comes back empty (MI/ATU "no rows").
    Restricting the click to a visible node fixes both failure modes."""
    loc = page.locator(f"text={text}")
    waited = 0
    while waited <= timeout:
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    el.click()
                    return
            except Exception:
                continue
        page.wait_for_timeout(500)
        waited += 500
    raise EpayPortalError(f"Could not find a visible '{text}' control within {timeout}ms.")


def _set_report_month(page, month_name, year):
    """Best-effort: drive the report's Month filter to `month_name year` (e.g. 'May 2026') before
    the report is run, so an in-arrears closed month can be pulled instead of just the default
    current month.

    Written BLIND — the portal is only reachable from Railway's egress (WAF), so this cannot be
    exercised from a dev box. It therefore tries several generic widget patterns and is fully
    NON-FATAL: if it can't set the month, the report simply runs on its default month, and the
    caller stores the result under the period implied by the DOWNLOADED DATA (comp_period_from_
    records) — so a failed month-set wastes a pull but can never mislabel one. Returns True if it
    believes it changed the filter."""
    label = f"{month_name} {year}"
    variants = [label, f"{month_name}-{year}", f"{month_name} - {year}", month_name]
    try:
        # 1) Native <select> month picker — the cleanest case.
        for sel in page.query_selector_all("select"):
            try:
                opts = [(o.inner_text() or "").strip() for o in sel.query_selector_all("option")]
            except Exception:
                opts = []
            for v in variants:
                if any(v.lower() == o.lower() for o in opts):
                    try:
                        sel.select_option(label=next(o for o in opts if o.lower() == v.lower()))
                        page.wait_for_timeout(800)
                        return True
                    except Exception:
                        pass
        # 2) jqx / Kendo dropdown: a widget currently SHOWING a month name. Open it, then click the
        #    option that matches the target. Only touch elements whose own text is a month label, so
        #    we never accidentally click an unrelated control.
        months = [f"{__import__('calendar').month_name[m]}" for m in range(1, 13)]
        opener = None
        for el in page.query_selector_all(
                ".jqx-dropdownlist, .jqx-combobox, [role='combobox'], .k-dropdown, .k-dropdownlist"):
            try:
                if not el.is_visible():
                    continue
                txt = (el.inner_text() or "").strip()
                if any(mn in txt for mn in months):
                    opener = el
                    break
            except Exception:
                continue
        if opener is not None:
            opener.click()
            page.wait_for_timeout(900)
            for v in variants:
                opt = page.locator(f"text={v}")
                for i in range(min(opt.count(), 12)):
                    try:
                        node = opt.nth(i)
                        if node.is_visible():
                            node.click()
                            page.wait_for_timeout(800)
                            return True
                    except Exception:
                        continue
    except Exception:
        return False
    return False


def _open_and_download(page, report_id, dest_path, target=None):
    """Open a Commissions report by its menu id, run it, and save the .xlsx.

    `target` (optional) = a {'month_name','year'} dict; when given, the Month filter is driven to
    that month before running (best-effort — see _set_report_month). When omitted the report runs
    on its default (current) month."""
    page.hover("span.k-link:has-text('Commissions')", timeout=20000)
    page.wait_for_timeout(1500)
    page.wait_for_selector(f'[id="{report_id}"]', state="visible", timeout=20000)
    page.click(f'[id="{report_id}"]')
    page.wait_for_timeout(5000)
    if target:
        try:
            _set_report_month(page, target["month_name"], target["year"])
        except Exception:
            pass  # non-fatal — period is derived from the downloaded data regardless
    # run — click the VISIBLE Run Report button (Month defaults to current if not set above)
    _click_visible(page, "Run Report", timeout=20000)
    _wait_report_done(page)
    # download as Excel (same visible-only disambiguation)
    with page.expect_download(timeout=120000) as dl_info:
        _click_visible(page, "Download Report", timeout=30000)
        page.wait_for_timeout(1500)
        _click_visible(page, "as Excel spreadsheet", timeout=20000)
    dl_info.value.save_as(dest_path)


def _download_mi_report(page, dest_path):
    """Open the MI/ATU report (#102817), run it, and save the .xlsx to dest_path."""
    _open_and_download(page, MI_REPORT_ID, dest_path)


def _safe_base(url):
    """The epay portal base, VALIDATED at USE time (SSRF finding C4, 2026-08-06).

    `epay_sweep_config.portal_url` is a tenant-editable settings field that was passed to
    `page.goto()` unchecked — `(url or DEFAULT_URL).rstrip("/")` is not validation, so
    `file:///app/.env` reached a `--no-sandbox` Chromium here exactly as it did on the VidaPay path.
    Rows saved before this landed were never validated, which is why the check is here and not only
    on the settings save. A bad value becomes a NAMED EpayLoginError the sweep status shows."""
    raw = (url or "").strip()
    if not raw:
        return DEFAULT_URL.rstrip("/")
    try:
        return _url_guard.assert_safe_url(raw, what="portal address").rstrip("/")
    except _url_guard.UnsafeUrlError as e:
        raise EpayLoginError(e.message)


def discover_reports(url, user, pw):
    """Log in and enumerate the Commissions report menu → [{id, label}]. MUST run server-side
    (the portal WAF only allows the Railway egress IP). Used to find the report ids of the
    Commission Payment Detail + Comprehensive Compensation reports so they can be swept too."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise EpayLoginError(
            "Playwright is not installed in the backend image (add it to backend/Dockerfile).")
    base_url = _safe_base(url)
    items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(user_agent=UA)
        _url_guard.install_ssrf_route_guard(ctx)   # C4: re-validate every redirect hop, not just the base
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


def _period_row_count(client, table, org_id, period):
    """Existing row count for (org_id, period) — used by the partial-collapse guard. 0 on error."""
    try:
        resp = (client.schema("commcalc").table(table).select("org_id", count="exact")
                .eq("org_id", org_id).eq("period", period).limit(1).execute())
        return resp.count or 0
    except Exception:
        return 0


def _process_report(client, org_id, page, key, xlsx_path, target=None):
    """Download one report by key, parse, derive its period, and store it. Every report REPLACES its
    period (delete that period + insert the fresh pull). For comp the period comes from the rows'
    own Begin Date (mode 'data'), so the pull lands under the month it belongs to even if the portal
    Month filter wasn't moved; closed months stay frozen because each is a distinct `period`.

    `target` (optional) = {'month_name','month','year','period'} — the month to drive the report's
    filter to (comp multi-month refresh). Two guards keep a populated period safe: an EMPTY download
    raises before any write, and the PARTIAL-COLLAPSE guard refuses to overwrite a period that holds
    >= REPLACE_MIN_ROWS rows with a pull < REPLACE_MIN_RETAIN of that count."""
    import pandas as pd
    spec = REPORTS[key]
    _open_and_download(page, spec["report_id"], xlsx_path, target=target)
    df = pd.read_excel(xlsx_path, dtype=str).fillna("")
    records = df.to_dict("records")
    if not records:
        raise EpayPortalError(f"{spec['label']} report downloaded but contained no rows.")
    mode = spec["period"]
    if mode == "report_month":
        report_month = ""
        for r in records:
            if str(r.get("Report Month", "")).strip():
                report_month = str(r.get("Report Month")).strip()
                break
        period, pm, py = _period_from_report_month(report_month)
    elif mode == "data":
        # Store under the period the rows themselves belong to (their Begin Date). This is what makes
        # a mis-set / non-moving Month filter harmless: the data is never mislabeled. Fall back to the
        # requested target, then the current month, only if no Begin Date is parseable.
        derived = comp_period_from_records(records)
        if derived:
            period, pm, py = derived
        elif target:
            period, pm, py = target["period"], target["month"], target["year"]
        else:
            period, pm, py = _period_now()
    else:  # "current"
        period, pm, py = _period_now()
    base = {"org_id": org_id, "period": period, "period_month": pm, "period_year": py}
    rows = spec["map"](records, base)

    # PARTIAL-COLLAPSE guard: never replace a populated period with a drastically smaller pull. A
    # canceled account legitimately dropping out shrinks a period slightly; a pull collapsing it to a
    # fraction of its rows is a portal glitch (the failure that reduced a month to one account).
    existing = _period_row_count(client, spec["table"], org_id, period)
    if existing >= REPLACE_MIN_ROWS and len(rows) < existing * REPLACE_MIN_RETAIN:
        return {"report": key, "label": spec["label"], "period": period, "rows": 0,
                "mode": "skipped_guard", "existing": existing, "pulled": len(rows),
                "note": f"kept {existing} existing rows — pull had only {len(rows)} (suspect/partial)"}

    # REPLACE the period — insert the fresh pull FIRST, retire the previous load only once it has
    # all landed. This used to delete the period and then insert; a failure part-way through left
    # the period EMPTY (the failure mode that destroyed raw_comp_report April 2026 on the manual
    # upload path). safe_replace makes a failed pull a no-op instead. Other periods are untouched.
    from app.modules.commcalc.safe_replace import safe_replace as _safe_replace
    res = _safe_replace(client, spec["table"], rows,
                        lambda q: q.eq("org_id", org_id).eq("period", period),
                        label=f"{key} {period}")
    saved = res["saved"]
    if res.get("warning"):
        print(f"WARN epay sweep {key} {period}: {res['warning']}")

    # best-effort: record it on the Upload page's history (never fail the sweep on this)
    try:
        client.schema("commcalc").table("upload_log").insert({
            "org_id": org_id, "file_type": spec["file_type"], "period": period,
            "filename": "epay auto-sweep", "rows_saved": saved}).execute()
    except Exception:
        pass
    return {"report": key, "label": spec["label"], "period": period, "rows": saved,
            "mode": "replace"}


def _recent_months(n):
    """The current UTC month plus the (n-1) preceding months, newest first, each as
    {'period','month_name','month','year'} — the comp in-arrears refresh window."""
    import calendar as _cal
    now = datetime.now(timezone.utc)
    y, m = now.year, now.month
    out = []
    for _ in range(max(1, n)):
        out.append({"period": f"{_cal.month_name[m]} {y}", "month_name": _cal.month_name[m],
                    "month": m, "year": y})
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


def _expand_jobs(keys):
    """Expand report keys into (key, target) jobs. comp_report fans out across the current month +
    the most recent closed months (so in-arrears comp lands automatically); every other report is a
    single current-month pull (target=None)."""
    jobs = []
    for k in keys:
        if k == "comp_report":
            for tm in _recent_months(COMP_REFRESH_MONTHS):
                jobs.append((k, tm))
        else:
            jobs.append((k, None))
    return jobs


def run_epay_sweep(client, org_id, url, user, pw, reports=None):
    """Launch headless Chromium, log into the epay Owner Portal ONCE, and download + ingest each
    requested report. `reports` = list of REPORTS keys; defaults to ['mi'] for back-compat (MI/ATU
    only). MI/payment pull the current month; comp pulls the current month + COMP_REFRESH_MONTHS-1
    closed months (in-arrears) and stores each under the period its data belongs to. Each pull
    REPLACEs its own period/table (empty + partial-collapse guarded).

    Returns a summary dict. Raises EpayLoginError if Chromium/Playwright isn't installed or login
    fails; EpayPortalError only if EVERY requested report failed (partial failures are reported)."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise EpayLoginError(
            "Playwright is not installed in the backend image. Add "
            "`RUN pip install playwright && playwright install --with-deps chromium` "
            "to backend/Dockerfile to enable the epay headless sweep.")
    import tempfile
    import os

    keys = [k for k in (reports or ["mi"]) if k in REPORTS] or ["mi"]
    base_url = _safe_base(url)
    results, errors = [], []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(user_agent=UA, accept_downloads=True)
        _url_guard.install_ssrf_route_guard(ctx)   # C4: re-validate every redirect hop, not just the base
        page = ctx.new_page()
        try:
            page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
            _login(page, user, pw)
            jobs = _expand_jobs(keys)
            for idx, (key, target) in enumerate(jobs):
                if idx > 0:
                    # Reload to a clean app shell between every report run so each opens on a fresh
                    # toolbar (no accumulated/stale "Run Report" buttons + a reset Month filter);
                    # re-login if the reload bounced back to the sign-in form.
                    try:
                        page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
                        page.wait_for_timeout(2500)
                        if page.query_selector("#passwordInput"):
                            _login(page, user, pw)
                    except Exception:
                        pass
                tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
                tmp.close()
                tgt = f" [{target['period']}]" if target else ""
                try:
                    results.append(_process_report(client, org_id, page, key, tmp.name, target=target))
                except Exception as e:
                    errors.append(f"{REPORTS[key]['label']}{tgt}: {type(e).__name__}: {e}")
                finally:
                    try:
                        os.unlink(tmp.name)
                    except Exception:
                        pass
        finally:
            browser.close()

    if not results and errors:
        raise EpayPortalError("; ".join(errors))
    summary = {"reports": results}
    if errors:
        summary["errors"] = errors
    mi = next((r for r in results if r["report"] == "mi"), None)
    if mi:  # keep back-compat top-level fields for the admin status line
        summary.update({"period": mi["period"], "rows": mi["rows"]})
    return summary


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
