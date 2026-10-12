"""THE ONE HOME for "reach a report on a portal we are logged into, export it, and land it".

THE CLASS THIS MODULE IS THE ONE HOME FOR (index §COMMCALC-05)
--------------------------------------------------------------
Pulling a report from a portal is six steps, and only the last two are about the FEED: resolve the
window of days, reach the report, press its export control, read the download, parse it, store it.
The first four are the same for every portal on earth. They were nevertheless written INSIDE
`merchant_portal_sweep.py`, bound to card settlement by three hard-coded calls — the descriptor
lookup, the normalizer and the lander. So a second feed on the same portal mechanism (the owner's
UPS Store POS report, 2026-10-12) could only be built by copying that file and changing those three
calls, which is a second copy of the navigation, the calibration ladder, the honest-failure wording
and the cold-session restore. Four facts, duplicated, drifting from the day they were written.

The INSTANCE was "the UPS Store report has no puller". The CLASS is that **the pull loop and the
feed were one lump**, so a new feed meant a new loop. This module is the loop, exactly once; a feed
is an ADAPTER it calls. Nothing here knows what a card brand, a settlement role or a declared
category is, and nothing here names a vendor.

THE ADAPTER CONTRACT — four functions, no classes, no inheritance
-----------------------------------------------------------------
A feed adapter is a MODULE exposing the names in `REQUIRED`:

    connector_keys()                              -> tuple of data_source.processor values it serves
    portal_descriptor(connector)                  -> dict | None (needs `base_url`; may carry
                                                     `date_window_days`, `session_ttl_hours`)
    report_specs(connector, enabled_keys,
                 src_row=None)                    -> tuple of {key, label, nav, export, grain, ...}
    ingest_report(client, org_id, src_row,
                  connector, spec, payload, name) -> the per-report result dict

An adapter MAY also expose `setup_hint(src_row) -> str`, which this loop appends when the source has
declared no report — so a connector nobody has configured yet reads as unconfigured rather than as a
pull that failed. `ADAPTER_MODULES` is the registry. A connector key is never spelled here: each adapter is ASKED
which ones it serves, so the merchant keys keep living in `merchant_portals.PORTAL_KEYS` and the
POS-report key in its own module. Adding a feed is one entry in that tuple plus a
`router._SOURCE_SCRAPERS` registration — no branch in this file, and no second pull loop.

WHAT IS PROVEN AND WHAT IS CALIBRATED. Unchanged from the posture
`merchant_portal_sweep` established and stated: the PARSER is provable without the portal and is
proven by each adapter's own harness; a portal's exact DOM is not, so a report is reached by the
per-source CALIBRATION an operator captures once on the live screencast, then the adapter's visible
TEXT hints, and when neither reaches it the pull returns a NAMED, actionable failure rather than
silently delivering zero rows. A zero-row pull is never a green tick.

Proof: `backend/harness_portal_report_pull.py`. It drives the whole loop against a fake page and a
fake adapter — no browser, no database, no network.
"""
import csv
import io
import os
import tempfile
from datetime import date, datetime, timedelta, timezone

DEFAULT_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 45          # a daily pull that re-fetches months is how a portal starts rate-limiting
_DOWNLOAD_TIMEOUT_MS = 120000

# The feed adapters, by module path. Each MUST expose every name in REQUIRED (the harness fails the
# build otherwise). Order is the resolution order; a connector key belongs to exactly one adapter.
ADAPTER_MODULES = (
    "app.modules.commcalc.merchant_portal_sweep",     # card settlement + funding (mig 955)
    "app.modules.commcalc.pos_report_sweep",          # the POS report of declared categories (mig 1078)
)

REQUIRED = ("connector_keys", "portal_descriptor", "report_specs", "ingest_report")


class PortalPullError(Exception):
    """A named, operator-actionable portal failure. Never carries a credential."""


def _vp():
    """Lazy import of the shared browser driver (keeps Playwright import cost off module load)."""
    from app.modules.commcalc import vidapay_sweep as vp
    return vp


# ── adapter resolution ───────────────────────────────────────────────────────────────────────────
def _load(path):
    import importlib
    try:
        return importlib.import_module(path)
    except Exception:
        return None


def adapters():
    """Every importable adapter module that satisfies the contract, in registry order."""
    out = []
    for path in ADAPTER_MODULES:
        mod = _load(path)
        if mod and all(callable(getattr(mod, n, None)) for n in REQUIRED):
            out.append(mod)
    return out


def connector(value):
    """Normalized `data_source.processor`. Case/space/dash tolerant, exactly as merchant_portals did."""
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def adapter_for(processor):
    """The adapter that serves this connector, or None. Asking each adapter which keys it serves is
    what keeps the keys in ONE place per feed — this module re-spells none of them."""
    key = connector(processor)
    if not key:
        return None
    for mod in adapters():
        try:
            if key in tuple(mod.connector_keys() or ()):
                return mod
        except Exception:
            continue
    return None


def is_pullable(processor):
    return adapter_for(processor) is not None


def descriptor(processor):
    mod = adapter_for(processor)
    if not mod:
        return None
    try:
        return mod.portal_descriptor(connector(processor))
    except Exception:
        return None


# ── pure helpers ─────────────────────────────────────────────────────────────────────────────────
def window_days(src_row, processor):
    """How many days back one pull re-fetches. Per-source config first, then the feed's house default,
    clamped to MAX_WINDOW_DAYS. Portals restate recent days (a correction, a late batch), so a daily
    pull deliberately re-fetches a few days and the upsert restates them in place."""
    for v in (((src_row or {}).get("portal_window_days")),
              ((descriptor(processor) or {}).get("date_window_days"))):
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return min(n, MAX_WINDOW_DAYS)
    return DEFAULT_WINDOW_DAYS


def date_range(src_row, processor, today=None):
    """(from_iso, to_iso) for a pull. Ends YESTERDAY by default: a business day is not final until it
    closes, so pulling today's partial figures would hand a recon a number that changes under it."""
    t = today or date.today()
    if isinstance(t, datetime):
        t = t.date()
    end = t - timedelta(days=1)
    start = end - timedelta(days=max(0, window_days(src_row, processor) - 1))
    return start.isoformat(), end.isoformat()


def read_table(payload, filename="", header_score=None):
    """Report payload → list-of-lists (header row first). PURE.

    Accepts CSV/TSV text or bytes (what every portal here exports) and an already-parsed list of
    lists. XLSX is delegated to the platform's existing workbook reader at the call site — this
    function's job is to never let an encoding or delimiter quirk become a parsing bug that reaches
    money.

    `header_score(row) -> int` is how a FEED says which row is the real header: portals commonly
    print a title/date banner above it, and "how many cells look like my columns" is the only
    reliable test. It is the feed's own synonym table, passed in rather than imported, so this module
    stays ignorant of any feed's vocabulary. Omitted ⇒ the first row is the header.
    """
    if payload is None:
        return []
    if isinstance(payload, (list, tuple)):
        return [list(r) for r in payload]
    if isinstance(payload, bytes):
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                payload = payload.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return []
    text = str(payload).lstrip("﻿")
    if not text.strip():
        return []
    sample = "\n".join(text.splitlines()[:5])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = "\t" if (filename or "").lower().endswith((".tsv", ".txt")) and "\t" in sample else ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim)]
    if header_score is not None:
        for i, r in enumerate(rows[:12]):
            try:
                if int(header_score(r) or 0) >= 2:
                    return [list(x) for x in rows[i:]]
            except Exception:
                continue
    return [list(x) for x in rows]


# ── browser navigation (calibration-first, hints-second, honest failure third) ───────────────────
def calibrated(src_row, report_key, field):
    cal = ((src_row or {}).get("portal_calibration") or {}).get(report_key) or {}
    v = cal.get(field) if isinstance(cal, dict) else None
    return v if isinstance(v, str) and v.strip() else None


def click_text(page, words, timeout_ms=15000):
    """Click the first VISIBLE control whose text matches any of `words`. Mirrors epay_sweep's
    _click_visible (portal grids render hidden duplicate controls too — a bare text= selector clicks
    the first node, often an invisible one, and the report silently never runs)."""
    waited = 0
    while waited <= timeout_ms:
        for w in words:
            try:
                loc = page.get_by_text(w, exact=False)
                for i in range(min(loc.count(), 12)):
                    el = loc.nth(i)
                    try:
                        if el.is_visible():
                            el.click()
                            return w
                    except Exception:
                        continue
            except Exception:
                continue
        try:
            page.wait_for_timeout(500)
        except Exception:
            return None
        waited += 500
    return None


def navigate_to_report(page, src_row, spec):
    """Reach one report. Calibrated selector first (authoritative once an operator captured it on the
    live screencast), then a calibrated URL, then the spec's text hints. True when we believe we
    arrived."""
    vp = _vp()
    sel = calibrated(src_row, spec["key"], "nav_selector")
    if sel:
        try:
            page.click(sel, timeout=20000)
            vp._wait_settle(page)
            return True
        except Exception:
            pass                                  # a stale calibration falls back to the hints
    url = calibrated(src_row, spec["key"], "report_url")
    if url:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            vp._wait_settle(page)
            return True
        except Exception:
            pass
    hit = False
    for word in (spec.get("nav") or ()):
        if click_text(page, [word.title(), word.upper(), word], timeout_ms=8000):
            hit = True
            try:
                vp._wait_settle(page)
            except Exception:
                pass
    return hit


def download_report(page, src_row, spec, dest_dir):
    """Trigger the report's export and return (path, filename), or (None, None). Prefers the
    calibrated export control; otherwise tries the usual export words. Uses Playwright's download
    interception — the file the portal itself generates, not a scrape of the rendered grid."""
    export_sel = calibrated(src_row, spec["key"], "export_selector")
    words = ["Export", "Download", "CSV", "Export to CSV", "Excel", "Export Report"]
    try:
        with page.expect_download(timeout=_DOWNLOAD_TIMEOUT_MS) as dl:
            if export_sel:
                page.click(export_sel, timeout=20000)
            elif not click_text(page, words, timeout_ms=20000):
                raise PortalPullError("no export control found")
        d = dl.value
        name = d.suggested_filename or "report.csv"
        path = os.path.join(dest_dir, name)
        d.save_as(path)
        return path, name
    except Exception:
        return None, None


def _read_download(path):
    with open(path, "rb") as fh:
        return fh.read()


# ── THE pull loop ────────────────────────────────────────────────────────────────────────────────
def pull_reports_on_page(client, org_id, src_row, page, should_stop=None):
    """Pull EVERY enabled report for this source on an ALREADY-AUTHENTICATED page.

    This is the single pull implementation. Both entry points reach it:
      • the LIVE session (live_login's pull_fn) — runs on the very browser that passed the portal's
        second factor, which is why the durable-session approach works at all on portals that
        re-challenge a cold restore;
      • the SCHEDULED pull — a cold context restored from the saved storage_state (below).
    One implementation, so the daily result can never differ from what the operator saw.
    """
    src_row = dict(src_row or {})
    proc = connector(src_row.get("processor"))
    mod = adapter_for(proc)
    if not mod:
        return {"ok": False, "delivered": False, "rows_ingested": 0,
                "error": "No report feed is registered for this connector."}
    specs = tuple(mod.report_specs(proc, src_row.get("portal_reports"), src_row=src_row) or ())
    if not specs:
        return {"ok": False, "delivered": False, "rows_ingested": 0,
                "error": ("No reports are enabled for this portal source. " + setup_hint(mod, src_row)).strip()}
    d_from, d_to = date_range(src_row, proc)
    results, total, unreachable = [], 0, []
    with tempfile.TemporaryDirectory() as tmp:
        for spec in specs:
            if should_stop is not None and should_stop():
                break
            if not navigate_to_report(page, src_row, spec):
                unreachable.append(spec["label"])
                results.append({"report": spec["key"], "label": spec["label"], "rows_ingested": 0,
                                "delivered": False,
                                "error": "Could not reach this report — calibrate it on the live login."})
                continue
            path, name = download_report(page, src_row, spec, tmp)
            if not path:
                unreachable.append(spec["label"])
                results.append({"report": spec["key"], "label": spec["label"], "rows_ingested": 0,
                                "delivered": False,
                                "error": "Reached the report but found no export control — calibrate "
                                         "export_selector for this source on the live login."})
                continue
            try:
                res = mod.ingest_report(client, org_id, src_row, proc, spec, _read_download(path), name)
            except Exception as e:
                res = {"report": spec["key"], "label": spec["label"], "rows_ingested": 0,
                       "delivered": False, "error": ("Ingest failed: %s" % e)[:300]}
            results.append(res)
            total += res.get("rows_ingested") or 0
    return summarize(results, total, d_from, d_to, unreachable)


def setup_hint(mod, src_row):
    """The adapter's own words for "what must an operator declare before this can pull", or "". A feed
    that needs configuration says what configuration, where the operator is already looking."""
    fn = getattr(mod, "setup_hint", None)
    if not callable(fn):
        return ""
    try:
        return str(fn(src_row) or "")
    except Exception:
        return ""


def summarize(results, total, d_from, d_to, unreachable):
    """The pull's answer. HONEST outcome: `delivered` only when rows actually landed — a zero-row pull
    is never a green tick, which is exactly how a dead connector used to look healthy. An adapter may
    attach `unresolved` items (anything a human must map before the rows count); they are carried up
    with the count, never swallowed."""
    unresolved = []
    for r in results:
        for u in (r.get("unresolved") or r.get("unresolved_merchants") or []):
            if u not in unresolved:
                unresolved.append(u)
    status = ("Imported %d row(s) across %d report(s) for %s..%s."
              % (total, sum(1 for r in results if r.get("delivered")), d_from, d_to))
    if unreachable:
        status += " Not reached: %s." % ", ".join(list(unreachable)[:4])
    if unresolved:
        status += (" %d item(s) are not mapped yet — map them in setup or their figures stay out of "
                   "the store totals." % len(unresolved))
    out = {"ok": True, "delivered": total > 0, "rows_ingested": total, "status": status,
           "date_from": d_from, "date_to": d_to, "reports": results,
           "unresolved": unresolved[:50]}
    # The merchant feed's own spelling, kept so its UI and harness keep reading the same key. One
    # value, two names on ONE dict is not a second home — it is a rename with a migration path.
    out["unresolved_merchants"] = out["unresolved"]
    return out


def make_pull_fn(client, org_id, src_row):
    """The `pull_fn` live_login.start_session() already accepts — so a portal pull runs on the LIVE
    browser that just passed the second factor, and again on every '▶ Pull now'. Accepts the optional
    should_stop (live_login inspects arity), so an operator's Cancel stops between reports."""
    def _pull(page, should_stop=None):
        return pull_reports_on_page(client, org_id, src_row, page, should_stop)
    return _pull


def run_cold_sweep(client, org_id, src_row):
    """The SCHEDULED (cold) pull — restores the durable session saved by the live login and pulls.

    Raises vidapay_sweep.VidaPayAuthError when there is no usable session, which run_data_source
    already turns into an auth_status=needs_2fa prompt (a human is asked to re-link) rather than a
    hard error. That is the whole second-factor story for the daily run: no code is ever needed while
    the session holds."""
    vp = _vp()
    src_row = dict(src_row or {})
    proc = connector(src_row.get("processor"))
    mod = adapter_for(proc)
    desc = descriptor(proc)
    if not mod or desc is None:
        raise PortalPullError("Unknown report portal: %s" % proc)
    state = src_row.get("session_state")
    if not state:
        raise vp.VidaPayAuthError(
            "This portal has never been linked. Open the live login and complete the portal's second "
            "factor once; the saved session then drives the daily pull.")
    url = vp._norm_url(src_row.get("portal_url"), desc.get("base_url"))
    if not str(url or "").strip():
        # A feed whose address is config (as it should be) cannot be pulled before an operator
        # declares it. Say so, with the feed's own setup words — never open a browser at nothing.
        raise PortalPullError(
            ("This source has no portal address declared. " + setup_hint(mod, src_row)).strip())
    from app.core.service_role import assert_browser_allowed
    assert_browser_allowed()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = vp._launch(p)
        try:
            ctx = vp._new_context(browser, storage_state=state,
                                  proxy=vp._proxy_arg(src_row.get("proxy_url")))
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            vp._wait_settle(page)
            if vp._classify(page) != "authenticated":
                # The portal invalidated the durable session. This is the ONE moment a human is needed
                # again — surfaced as needs_2fa so the session-health chip turns actionable and the
                # owner is notified, instead of the pull quietly returning nothing night after night.
                raise vp.VidaPayAuthError(
                    "The saved portal session is no longer accepted — re-link this source on the live "
                    "login (the portal will ask for its second factor once).")
            res = pull_reports_on_page(client, org_id, src_row, page)
            try:
                res["storage_state"] = vp.capture_session_state(page, ctx)
            except Exception:
                pass
            return res
        finally:
            try:
                browser.close()
            except Exception:
                pass


def session_ttl_hint(processor):
    """Best-effort expiry to stamp when a portal publishes none. Deliberately conservative: assuming a
    long life is how a dead session hides until a recon has a hole in it."""
    return int((descriptor(processor) or {}).get("session_ttl_hours") or 8)


def next_expiry(processor, now=None):
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(hours=session_ttl_hint(processor))).isoformat()
