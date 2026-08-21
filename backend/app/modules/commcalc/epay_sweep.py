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
from datetime import datetime, timedelta, timezone

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
#
# COMP CADENCE — CORRECTED 2026-08-09 (owner), replacing the 2026-06-20 note that used to sit here.
# The old note said each pull was "the carrier's cumulative month-to-date snapshot", so the sweep
# REPLACED the whole open month on every pull. That was wrong, and it was never observed to be
# right: the comp leg has NEVER once succeeded (zero 'epay auto-sweep' rows for comp_report in
# upload_log, ever, since the leg was added on 2026-06-17). What the portal actually serves:
#
#   * Comp is a DAILY report. Every row carries Begin Date == End Date == one day, ~270-450 rows
#     and ~$11k-26k a day (verified live 2026-08-09 against seven pulls).
#   * Its filter panel has NO month control at all. It has "Summarize by" (Daily/Weekly/Monthly)
#     plus Start Date / End Date. _set_report_month could never move anything here and always
#     returned False, which is why all three "in-arrears" months pulled the same empty default.
#   * "Summarize by" is REQUIRED. Left on "Please Choose:" the report returns a header-only
#     workbook for EVERY date — that, not a portal change or a parser drift, is the entire cause of
#     'Comprehensive Comp report downloaded but contained no rows'. Set it to Daily and the same
#     date returns its rows: 2026-04-15 -> 381 rows / $20,698.61, matching raw_comp_report exactly.
#   * The day's compensation posts LATE (owner: ~11:30 PM). A 06:00 pull of today is legitimately
#     empty. So a zero-row day is NORMAL and must not be an error; comp gets its own late slot via
#     report_definitions.sweep_hour.
#
# Storage therefore keys on the DAY, not the month: each day in the pull replaces only its own
# begin_date. A single day's pull can never wipe a month, and a re-run of the same day is
# idempotent. The month label still comes from the rows' own dates (period mode "data"), so
# nothing can be mislabeled.


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
        # Normalised to ISO. The export spells these 'MM/DD/YYYY', and this used to store that
        # string verbatim and let Postgres coerce it — which only works while DateStyle stays MDY,
        # and left the value in a different spelling from the ISO day key the sweep now replaces
        # by. _iso_day falls back to the raw first-10 characters if it meets a shape it doesn't
        # know, so an unexpected format still reaches the column exactly as it did before.
        "begin_date": _iso_day(_comp_get(r, "Begin Date", "BeginDate"))
                      or (str(_comp_get(r, "Begin Date", "BeginDate"))[:10] or None),
        "end_date": _iso_day(_comp_get(r, "End Date", "EndDate"))
                    or (str(_comp_get(r, "End Date", "EndDate"))[:10] or None),
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

# HOW WIDE EACH REPORT REFETCHES is now read from the registry the Connectors page already edits
# (commcalc.report_definitions), not from a constant here:
#
#   refresh_months  month-grain reports (MI). Current month + N-1 prior closed months. MI is a
#                   month-to-date ACCRUAL, so a month that stops being re-pulled freezes wherever
#                   it was: June 2026 froze on 06-23 at $83,221 and July on 07-08 at $23,156,
#                   against a ~$128k/month run rate. The column existed and was set to 3, and
#                   nothing read it — the refresh was hard-coded and applied to comp only.
#   refresh_days    day-grain reports (comp). Trailing days including today; 1 = today only.
#                   The portal accepts a date RANGE and returns every day in it in ONE run
#                   (2026-07-01..07-31 -> 11,054 rows across 30 days in 12s), so widening this
#                   costs one report run, not N, and makes a missed night self-heal.
#
# These are the DEFAULTS used only when a report has no registry row.
DEFAULT_REFRESH_MONTHS = 1
DEFAULT_REFRESH_DAYS = 1

# Partial-collapse guard: a REPLACE never overwrites a period that already holds >= REPLACE_MIN_ROWS
# rows with a pull smaller than REPLACE_MIN_RETAIN of that count. A glitched/partial pull (e.g. the
# portal returning a single stray row) is non-empty, so the empty-download guard misses it — this is
# what silently reduced a populated month to one account. Below the floor (a month just starting, or
# a first-ever load) the guard does nothing, so normal growth is unaffected.
REPLACE_MIN_ROWS = 50
REPLACE_MIN_RETAIN = 0.5

# ── Daily Transaction Detail (#P1) → commcalc.raw_epay_daily_tx (via epay_ingest) ─────────────
# Unlike the comp/MI/payment reports above (which map+REPLACE via _process_report/_store_day_grain),
# the Daily Transaction Detail feeds the P1 ingest verbatim: epay_ingest owns the parse, the
# PAYMENT-vs-FEE split ("…FEE" title match) and the TerminalID→store resolution, and UPSERTS
# idempotently on (org_id, transaction_id, transaction_source_id) — so an hourly re-pull is safe and
# never double-counts. This hook is the sweep's bridge into that path; it re-uses epay_ingest whole
# and reimplements none of the DTD parse.
def ingest_daily_tx(client, org_id, xlsx_path, source_batch=None):
    """Route a downloaded Daily Transaction Detail workbook through epay_ingest (parse + payment/fee
    split + terminal→store resolution + idempotent upsert). Returns a sweep-shaped result dict.

    A zero-row DTD is a legitimately quiet window (no transactions), NOT a failure — it is reported
    as mode 'no_data' rather than raising, exactly like the comp report's empty_ok path."""
    from app.modules.commcalc import epay_ingest as _ei
    try:
        records, _cols = _read_report_records(xlsx_path, "Daily Transaction Detail")
    except EpayEmptyReport:
        return {"report": "epay_daily_tx", "label": "Daily Transaction Detail", "grain": "day",
                "rows": 0, "mode": "no_data",
                "note": "no transactions in the DTD window — nothing to store (not an error)"}
    res = _ei.ingest(org_id, records, source_batch=source_batch, client=client) or {}
    out = {"report": "epay_daily_tx", "label": "Daily Transaction Detail", "grain": "day",
           "rows": res.get("saved", 0), "parsed": res.get("rows", 0), "mode": "upsert",
           "unresolved_terminals": res.get("unresolved_terminals", []),
           "source_batch": source_batch}
    return out


# Report registry: key → how to download, filter, map and store it.
#   registry_key  the commcalc.report_definitions.report_key this report is configured by
#   report_id     the portal Commissions-menu id; None means RESOLVE it at run time (label_match /
#                 a report_definitions override) because it cannot be hardcoded from a dev box
#   label_match   when report_id is None: the case-insensitive substring of the menu row's visible
#                 text that identifies this report (resolved from the live Commissions menu)
#   grain         "month" (refetch N months, one run each) | "day" (refetch a date RANGE in ONE
#                 run and store per day) | "none" (single default pull)
#   filter        which portal control the sweep drives: "month" dropdown | "daily_range"
#                 (Summarize by = Daily + Start/End Date) | None
#   ingest        optional hook (client, org_id, xlsx_path, source_batch) -> result dict, used
#                 INSTEAD of _process_report/_store_day_grain to route the workbook through a
#                 dedicated ingest (DTD → epay_ingest). When present the sweep loop downloads the
#                 file and hands it to this hook.
#   period        how the stored period is derived: the file's "Report Month" column, the rows'
#                 own dates ("data"), or the current month
#   day_key       for grain "day": the mapped column that carries the row's day
#   empty_ok      a zero-row pull is a legitimate answer, not a failure
REPORTS = {
    "mi": {"report_id": MI_REPORT_ID, "table": "raw_mi", "file_type": "mi_report",
           "registry_key": "mi_report", "grain": "month", "filter": "month",
           "period": "report_month", "map": map_mi_records, "label": "MI/ATU"},
    "payment_detail": {"report_id": PAYMENT_DETAIL_REPORT_ID, "table": "raw_payment_detail",
                       "file_type": "payment_detail", "registry_key": "payment_detail",
                       "grain": "none", "filter": None,
                       "period": "current", "label": "Commission Payment Detail",
                       "map": lambda recs, base: _map_filtered(recs, base, map_payment_detail_row)},
    "comp_report": {"report_id": COMP_REPORT_ID, "table": "raw_comp_report",
                    "file_type": "comp_report", "registry_key": "comp_report",
                    "grain": "day", "filter": "daily_range", "day_key": "begin_date",
                    "empty_ok": True,
                    "period": "data", "label": "Comprehensive Comp",
                    "map": lambda recs, base: _map_filtered(recs, base, map_comp_report_row)},
    # Daily Transaction Detail (P1). report_id is RESOLVED at run time from the Commissions menu by
    # label (or a report_definitions override) — see run_epay_sweep. Same day_range Start/End flow as
    # comp, but stored via the `ingest` hook (epay_ingest) instead of the map/REPLACE path.
    "epay_daily_tx": {"report_id": None, "label_match": "daily transaction detail",
                      "table": "commcalc.raw_epay_daily_tx", "file_type": "epay_daily_tx",
                      "registry_key": "epay_daily_tx", "grain": "day", "filter": "daily_range",
                      "empty_ok": True, "label": "Daily Transaction Detail",
                      "ingest": ingest_daily_tx},
}


def _iso_day(v):
    """'04/15/2026' or '2026-04-15' -> '2026-04-15'. None when unparseable."""
    s = str(v or "").strip()
    if len(s) >= 10 and s[2] == "/" and s[5] == "/":
        try:
            return f"{int(s[6:10]):04d}-{int(s[0:2]):02d}-{int(s[3:5]):02d}"
        except ValueError:
            return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


def _period_of_day(iso):
    """'2026-04-15' -> ('April 2026', 4, 2026)."""
    import calendar as _cal
    y, m = int(iso[0:4]), int(iso[5:7])
    return f"{_cal.month_name[m]} {y}", m, y


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

    NO LONGER BLIND (2026-08-09). This used to be written against a portal nobody could reach from
    a dev box, so it guessed at widget patterns. Observed live, the MI report (#102817) carries a
    jqWidgets dropdown whose labels are spelled "August- 26" / "July- 26" — note the space AFTER the
    hyphen and the TWO-DIGIT year, which none of the guessed variants match. It only worked at all
    because the last-resort variant is the bare month name and Playwright's `text=June` is a
    SUBSTRING match. That is one "June- 25" away from silently selecting the wrong year, on a report
    that feeds residual income. Step 0 below now selects through the widget's own API, matching the
    year properly; the old heuristics stay as fallbacks.

    Still NON-FATAL by design: if the month cannot be set, the report runs on its default month and
    the caller stores the result under the period implied by the DOWNLOADED DATA — a failed
    month-set wastes a pull but can never mislabel one. (The comp report takes the opposite,
    fail-loud path — see _set_daily_range — because an unfiltered comp run returns nothing at all.)
    Returns True if it believes it changed the filter.

    Verified live 2026-08-09: on #102817 this drives the dropdown to 'June- 26'."""
    label = f"{month_name} {year}"
    yy = str(year)[-2:]
    variants = [label, f"{month_name}-{year}", f"{month_name} - {year}",
                f"{month_name}- {yy}", f"{month_name}-{yy}", f"{month_name} {yy}", month_name]
    try:
        # 0) The widget's own API, matching BOTH the month and the year — the portal's real
        #    spelling is "June- 26", so compare on normalised text rather than an exact literal.
        picked = page.evaluate(
            """([month, year, yy]) => {
                 const $ = window.jQuery || window.$;
                 if (!$) return null;
                 const norm = s => String(s||'').toLowerCase().replace(/[^a-z0-9]/g, '');
                 const want = [norm(month + year), norm(month + yy)];
                 let hit = null;
                 document.querySelectorAll('.jqx-dropdownlist-state-normal').forEach(host => {
                   if (hit) return;
                   try {
                     const items = ($(host).jqxDropDownList('getItems') || []);
                     const m = items.find(it => want.indexOf(norm(it.label)) >= 0);
                     if (m) {
                       $(host).jqxDropDownList('selectItem', m.label);
                       const s = $(host).jqxDropDownList('getSelectedItem');
                       hit = s ? s.label : m.label;
                     }
                   } catch (e) {}
                 });
                 return hit;
               }""",
            [month_name, str(year), yy])
        if picked:
            page.wait_for_timeout(800)
            return True
    except Exception:
        pass
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


# The comp report's real controls, reverse-engineered live 2026-08-09 from the Railway-equivalent
# egress. jQuery 2.1.3 + jqWidgets are on the page, so the widgets are driven through their own API
# rather than by typing into a masked input (typing '04/15/2026' into a widget whose formatString is
# 'M/d/yyyy' produced 12/15/2026 — the mask reinterpreted it).
_SET_INTERVAL_JS = """
(want) => {
  const $ = window.jQuery || window.$;
  if (!$) return {ok:false, why:'no jQuery'};
  let hit = null;
  document.querySelectorAll('.jqx-dropdownlist-state-normal').forEach(host => {
    if (hit) return;
    try {
      const items = ($(host).jqxDropDownList('getItems') || []).map(i => i.label);
      if (items.indexOf(want) >= 0) {
        $(host).jqxDropDownList('selectItem', want);
        const s = $(host).jqxDropDownList('getSelectedItem');
        hit = {id: host.id, selected: s ? s.label : null};
      }
    } catch (e) {}
  });
  const h = document.querySelector('input[name="DateIntervalOption"]');
  return {ok: !!hit, hit, hidden: h ? h.value : null};
}
"""

_SET_DATES_JS = """
([beginIso, endIso]) => {
  const $ = window.jQuery || window.$;
  if (!$) return {ok:false, why:'no jQuery'};
  const put = (name, iso) => {
    const inp = document.querySelector('input[name="' + name + '"]');
    const host = inp ? inp.closest('.jqx-datetimeinput') : null;
    if (!host) return null;
    const p = iso.split('-').map(Number);
    // NOON, not midnight: a midnight Date can roll into the previous day under the widget's
    // own timezone handling and silently fetch the wrong day.
    $(host).jqxDateTimeInput('setDate', new Date(p[0], p[1]-1, p[2], 12, 0, 0));
    const got = $(host).jqxDateTimeInput('getDate');
    return got ? (got.getFullYear() + '-' +
                  String(got.getMonth()+1).padStart(2,'0') + '-' +
                  String(got.getDate()).padStart(2,'0')) : null;
  };
  const b = put('BeginDate', beginIso), e = put('EndDate', endIso);
  return {ok: b === beginIso && e === endIso, begin: b, end: e};
}
"""


def _set_daily_range(page, begin_iso, end_iso):
    """Drive the comp report to Summarize by = Daily over [begin_iso, end_iso].

    Returns (ok, detail). Unlike _set_report_month this is VERIFIED and its caller treats failure
    as fatal, because an unfiltered comp run returns an empty workbook that is indistinguishable
    from a legitimately quiet day — silently reporting "no data posted" when in truth we never set
    the filter is exactly how this leg stayed broken for eight weeks.
    """
    try:
        iv = page.evaluate(_SET_INTERVAL_JS, "Daily")
    except Exception as e:
        return False, f"could not set 'Summarize by' ({type(e).__name__}: {e})"
    if not iv.get("ok") or (iv.get("hidden") or "") != "Daily":
        return False, (f"'Summarize by' could not be set to Daily (hidden field = "
                       f"{iv.get('hidden')!r}); the report returns an empty workbook without it")
    page.wait_for_timeout(600)
    try:
        dr = page.evaluate(_SET_DATES_JS, [begin_iso, end_iso])
    except Exception as e:
        return False, f"could not set Start/End Date ({type(e).__name__}: {e})"
    if not dr.get("ok"):
        return False, (f"Start/End Date did not take: wanted {begin_iso}..{end_iso}, "
                       f"widget reads {dr.get('begin')}..{dr.get('end')}")
    page.wait_for_timeout(400)
    return True, f"Daily {begin_iso}..{end_iso}"


def _open_and_download(page, report_id, dest_path, target=None):
    """Open a Commissions report by its menu id, run it, and save the .xlsx.

    `target` (optional) describes which slice to fetch:
      {'kind': 'month', 'month_name', 'year'}       -> drive the Month dropdown (best-effort)
      {'kind': 'day_range', 'begin', 'end'}          -> drive Summarize by = Daily + Start/End Date
                                                        (VERIFIED; a failure raises)
    When omitted the report runs on its own defaults."""
    page.hover("span.k-link:has-text('Commissions')", timeout=20000)
    page.wait_for_timeout(1500)
    page.wait_for_selector(f'[id="{report_id}"]', state="visible", timeout=20000)
    page.click(f'[id="{report_id}"]')
    page.wait_for_timeout(5000)
    if target and target.get("kind") == "day_range":
        # FATAL on failure, deliberately: without the filter this report returns a header-only
        # workbook, which the caller would otherwise record as "nothing posted that day".
        ok, detail = _set_daily_range(page, target["begin"], target["end"])
        if not ok:
            raise EpayPortalError(f"could not set the report's daily date filter — {detail}")
    elif target:
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


# The Commissions-menu enumeration JS — shared by discover_reports and the run-time label resolver
# (both need the exact same [{id,label}] view of the menu so a label match in the sweep behaves
# identically to what the operator sees on the discover-reports page).
_MENU_ENUM_JS = (
    "() => {"
    "  const out = [];"
    "  document.querySelectorAll('[id]').forEach(el => {"
    "    const id = (el.getAttribute('id')||'').trim();"
    "    const txt = (el.textContent||'').trim();"
    "    if (/^[0-9]{4,}$/.test(id) && txt && txt.length < 120) out.push({id, label: txt});"
    "  });"
    "  return out;"
    "}")


def _enumerate_commissions_menu(page):
    """Hover the Commissions menu on an already-logged-in page and return [{id, label}] (deduped by
    id). Shared by discover_reports and the run-time report-id resolver."""
    page.hover("span.k-link:has-text('Commissions')", timeout=20000)
    page.wait_for_timeout(2000)
    items = page.evaluate(_MENU_ENUM_JS) or []
    seen = {}
    for it in items:
        seen.setdefault(it["id"], it["label"])
    return [{"id": k, "label": v} for k, v in seen.items()]


def _resolve_report_id(label_match, menu_items):
    """First menu id whose visible label contains `label_match` (case-insensitive substring), or
    None. Pure — the sweep passes the live [{id,label}] set, the harness passes a fake one."""
    lm = (label_match or "").strip().lower()
    if not lm:
        return None
    for it in (menu_items or []):
        if lm in str(it.get("label") or "").lower():
            return str(it.get("id"))
    return None


def discover_reports(url, user, pw):
    """Log in and enumerate the Commissions report menu → [{id, label}]. MUST run server-side
    (the portal WAF only allows the Railway egress IP). Used to find the report ids of the
    Commission Payment Detail + Comprehensive Compensation + Daily Transaction Detail reports so they
    can be swept too."""
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
            items = _enumerate_commissions_menu(page)
        finally:
            browser.close()
    return items


def _period_row_count(client, table, org_id, period):
    """Existing row count for (org_id, period) — used by the partial-collapse guard. 0 on error."""
    try:
        resp = (client.schema("commcalc").table(table).select("org_id", count="exact")
                .eq("org_id", org_id).eq("period", period).limit(1).execute())
        return resp.count or 0
    except Exception:
        return 0


class EpayEmptyReport(EpayPortalError):
    """The portal ran the report and returned a VALID workbook with zero data rows.

    Split out from EpayPortalError on 2026-08-09 because the old single message —
    "<report> downloaded but contained no rows" — conflated three completely different situations:
      (a) the carrier has posted nothing for that slice yet  (normal; comp before ~11:30 PM)
      (b) we downloaded something we cannot parse as Excel   (broken; an error page, a truncated file)
      (c) we never actually set the report's required filter (broken; returns empty for every date)
    (b) now raises EpayPortalError with the file's real shape attached, (c) raises before the
    download even happens, and (a) is this class — which a report marked empty_ok records as a
    clean 'no data' rather than a failure.
    """


def _describe_workbook(path):
    """What we actually downloaded, in words — size, magic bytes, sheets and their dimensions.
    Attached to every parse failure so the next one is diagnosable without a live re-run."""
    import os
    bits = []
    try:
        size = os.path.getsize(path)
        bits.append(f"{size} bytes")
    except OSError:
        return "the download could not be read from disk"
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
        if magic[:2] == b"PK":
            bits.append("looks like a real .xlsx (PK zip header)")
        elif magic[:1] == b"<":
            bits.append("looks like HTML — the portal probably served an error/login page")
        else:
            bits.append(f"unrecognised leading bytes {magic!r}")
    except OSError:
        pass
    try:
        import pandas as pd
        book = pd.read_excel(path, sheet_name=None, header=None, dtype=str)
        bits.append("sheets: " + ", ".join(f"{n!r} {d.shape[0]}x{d.shape[1]}"
                                           for n, d in book.items()))
    except Exception as e:
        bits.append(f"pandas could not open it: {type(e).__name__}: {e}")
    return "; ".join(bits)


def _read_report_records(xlsx_path, label):
    """Parse a downloaded report into records, distinguishing 'unparseable' from 'legitimately
    empty'. Returns (records, columns). Raises EpayPortalError for (b), EpayEmptyReport for (a)."""
    import pandas as pd
    try:
        df = pd.read_excel(xlsx_path, dtype=str).fillna("")
    except Exception as e:
        raise EpayPortalError(
            f"{label}: downloaded a file we could NOT PARSE as Excel "
            f"({type(e).__name__}: {e}). What arrived: {_describe_workbook(xlsx_path)}") from e
    records = df.to_dict("records")
    if not records:
        raise EpayEmptyReport(
            f"{label}: the portal returned an EMPTY report — the workbook parsed fine and has the "
            f"expected header ({', '.join(str(c) for c in list(df.columns)[:8])}"
            f"{'…' if len(df.columns) > 8 else ''}) but zero data rows. "
            f"What arrived: {_describe_workbook(xlsx_path)}")
    return records, [str(c) for c in df.columns]


def _store_day_grain(client, org_id, spec, key, records, target):
    """Comp: store a pull DAY BY DAY, each day replacing only its own begin_date.

    Why not by month: the pull is now a date range (often a single day), so replacing the month
    would delete every other day in it. Each day is independent and idempotent, the month label
    comes from the day itself, and a day the portal did not return is LEFT ALONE rather than
    treated as deleted — we cannot tell "nothing posted" from "not included" and the safe reading
    of an absent day is that we simply do not have news about it."""
    day_key = spec["day_key"]
    by_day = {}
    for r in records:
        iso = _iso_day(_comp_get(r, "Begin Date", "BeginDate"))
        if iso:
            by_day.setdefault(iso, []).append(r)
    if not by_day:
        raise EpayPortalError(
            f"{spec['label']}: {len(records)} row(s) came back but not one carried a usable "
            f"Begin Date, so they cannot be filed against a day. Columns seen: "
            f"{', '.join(sorted(records[0].keys()))[:300]}")

    from app.modules.commcalc.safe_replace import safe_replace as _safe_replace
    days, saved_total, skipped = [], 0, []
    for iso in sorted(by_day):
        period, pm, py = _period_of_day(iso)
        base = {"org_id": org_id, "period": period, "period_month": pm, "period_year": py}
        rows = spec["map"](by_day[iso], base)
        if not rows:
            continue
        # Same partial-collapse guard as the month path, applied per DAY.
        existing = _day_row_count(client, spec["table"], org_id, day_key, iso)
        if existing >= REPLACE_MIN_ROWS and len(rows) < existing * REPLACE_MIN_RETAIN:
            skipped.append({"day": iso, "existing": existing, "pulled": len(rows)})
            continue
        res = _safe_replace(client, spec["table"], rows,
                            lambda q, _k=day_key, _v=iso: q.eq("org_id", org_id).eq(_k, _v),
                            label=f"{key} {iso}")
        saved_total += res["saved"]
        days.append({"day": iso, "rows": res["saved"], "prior": res["prior"],
                     "warning": res.get("warning")})
    out = {"report": key, "label": spec["label"], "grain": "day",
           "period": ", ".join(sorted({_period_of_day(d)[0] for d in by_day})),
           "days": days, "rows": saved_total, "mode": "replace_by_day"}
    if skipped:
        out["skipped_guard"] = skipped
    return out


def _day_row_count(client, table, org_id, day_key, iso):
    try:
        resp = (client.schema("commcalc").table(table).select("org_id", count="exact")
                .eq("org_id", org_id).eq(day_key, iso).limit(1).execute())
        return resp.count or 0
    except Exception:
        return 0


def _process_report(client, org_id, page, key, xlsx_path, target=None, report_id=None):
    """Download one report by key, parse, derive its period, and store it. Every report REPLACES its
    period (delete that period + insert the fresh pull). For comp the period comes from the rows'
    own Begin Date (mode 'data'), so the pull lands under the month it belongs to even if the portal
    Month filter wasn't moved; closed months stay frozen because each is a distinct `period`.

    `target` (optional) = {'month_name','month','year','period'} — the month to drive the report's
    filter to (comp multi-month refresh). Two guards keep a populated period safe: an EMPTY download
    raises before any write, and the PARTIAL-COLLAPSE guard refuses to overwrite a period that holds
    >= REPLACE_MIN_ROWS rows with a pull < REPLACE_MIN_RETAIN of that count."""
    spec = REPORTS[key]
    _open_and_download(page, report_id or spec["report_id"], xlsx_path, target=target)
    try:
        records, _cols = _read_report_records(xlsx_path, spec["label"])
    except EpayEmptyReport as empty:
        # A report that is ALLOWED to come back empty (comp: the carrier posts late in the evening,
        # and some days genuinely have nothing) records the attempt cleanly and touches no data.
        if spec.get("empty_ok"):
            win = (f"{target['begin']}..{target['end']}"
                   if target and target.get("kind") == "day_range" else "default window")
            return {"report": key, "label": spec["label"], "rows": 0, "mode": "no_data",
                    "window": win, "period": None,
                    "note": f"no compensation posted for {win} — nothing to store (not an error)"}
        raise
    if spec.get("grain") == "day":
        return _store_day_grain(client, org_id, spec, key, records, target)
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
        elif target and target.get("period"):
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


def _recent_days(n, today=None):
    """The last `n` days ending today (UTC), oldest first, as ISO strings."""
    from datetime import date as _date
    end = today or datetime.now(timezone.utc).date()
    n = max(1, int(n or 1))
    return [(end - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _expand_jobs(keys, report_cfg=None):
    """Expand report keys into (key, target) jobs, driven by commcalc.report_definitions.

    MONTH-grain (MI): one job per month, current + refresh_months-1 prior closed months. This is
    the setting that existed in the database and was never read — MI only ever pulled the current
    month, so every closed month froze mid-accrual.

    DAY-grain (comp): ONE job covering the trailing refresh_days window, because the portal returns
    every day in a date range in a single run. Storage then splits it per day.

    Anything else: a single default pull.
    """
    cfg = report_cfg or {}
    jobs = []
    for k in keys:
        spec = REPORTS.get(k) or {}
        rc = cfg.get(spec.get("registry_key") or k) or {}
        grain = spec.get("grain")
        if grain == "month":
            n = int(rc.get("refresh_months") or DEFAULT_REFRESH_MONTHS)
            for tm in _recent_months(n):
                jobs.append((k, {**tm, "kind": "month"}))
        elif grain == "day":
            days = _recent_days(rc.get("refresh_days") or DEFAULT_REFRESH_DAYS)
            jobs.append((k, {"kind": "day_range", "begin": days[0], "end": days[-1],
                             "days": days, "period": None}))
        else:
            jobs.append((k, None))
    return jobs


def run_epay_sweep(client, org_id, url, user, pw, reports=None, report_cfg=None):
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
            # Resolve any report whose menu id is NOT hardcoded (Daily Transaction Detail carries
            # report_id=None). Order: a commcalc.report_definitions override
            # (registry_key.portal_report_id, surfaced via report_cfg) so the operator can pin the
            # exact id without a code change, then a case-insensitive label match against the LIVE
            # Commissions menu (the same enumeration discover_reports uses). A report that cannot be
            # resolved errors CLEARLY and is skipped — it never crashes the rest of the sweep.
            resolved_ids, menu_items = {}, None
            for k in keys:
                spec = REPORTS[k]
                if spec.get("report_id"):
                    resolved_ids[k] = spec["report_id"]
                    continue
                pinned = ((report_cfg or {}).get(spec.get("registry_key") or k) or {}).get("portal_report_id")
                if pinned:
                    resolved_ids[k] = str(pinned)
                    continue
                if menu_items is None:
                    try:
                        menu_items = _enumerate_commissions_menu(page)
                    except Exception as e:
                        menu_items = []
                        errors.append(f"{spec['label']}: could not read the Commissions menu to "
                                      f"resolve its report id ({type(e).__name__}: {e})")
                rid = _resolve_report_id(spec.get("label_match"), menu_items)
                if rid:
                    resolved_ids[k] = rid
                else:
                    errors.append(f"{spec['label']}: could not resolve a report id — no live menu row "
                                  f"matched label {spec.get('label_match')!r} and no "
                                  f"report_definitions override (portal_report_id) was set")
            jobs = _expand_jobs([k for k in keys if k in resolved_ids], report_cfg)
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
                tgt = ""
                if target and target.get("kind") == "day_range":
                    tgt = (f" [{target['begin']}]" if target["begin"] == target["end"]
                           else f" [{target['begin']}..{target['end']}]")
                elif target and target.get("period"):
                    tgt = f" [{target['period']}]"

                spec = REPORTS[key]
                try:
                    if spec.get("ingest"):
                        # DTD path: download the workbook, then hand it to the report's own ingest
                        # hook (epay_ingest) — parse + payment/fee split + terminal→store resolution +
                        # idempotent upsert — instead of the map/REPLACE path in _process_report.
                        _open_and_download(page, resolved_ids[key], tmp.name, target=target)
                        source_batch = f"epay-sweep {key}{tgt}".strip()
                        results.append(spec["ingest"](client, org_id, tmp.name, source_batch))
                    else:
                        results.append(_process_report(client, org_id, page, key, tmp.name,
                                                       target=target, report_id=resolved_ids[key]))
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
    # A day-grain report that legitimately had nothing posted returns mode='no_data'; that is a
    # completed pull, not a partial run, and must not flag the connector as degraded.
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
