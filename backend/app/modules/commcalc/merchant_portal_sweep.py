"""Merchant-portal RUNTIME — drives a browser with the merchant_portals adapters and ingests the result.

WHAT THIS IS. The moving half of the merchant-processor integration (owner directive 2026-09-04): pull
each configured portal's transaction/deposit reports daily and land them in commcalc.merchant_settlement_day
/ _batch (mig 955) so the closing recon can tally the processor's figures against what employees entered.

WHAT IT REUSES — no second login engine, no second browser stack, no second scheduler (CLAUDE.md
duplicate-check build gate):
  • live_login.py               ONE live browser from login through 2FA code entry, streamed to the
                                operator, then the authenticated storage_state persisted. Untouched:
                                this module supplies the `pull_fn` its start_session() already accepts,
                                so a merchant-portal pull runs on the very session that passed 2FA.
  • vidapay_sweep primitives    _launch / _new_context / _proxy_arg / capture_session_state / _classify /
                                _wait_settle / _page_text / egress_hint / VidaPayAuthError. The cold
                                (scheduled) restore is the same code path every other portal uses.
  • commcalc.data_source        the login registry — credentials, proxy, enabled, frequency/hour,
                                next_run_at, session_state, auth_status. A merchant portal IS a
                                data_source row; mig 955 only added the fields a card processor needs.
  • /data-sources/sweep/run-due the EXISTING generic scheduler. It dispatches on
                                router._SOURCE_SCRAPERS[processor], so registering the three portal
                                keys there is the whole of "daily scheduling" (mig 956 makes sure that
                                cron is actually registered — mig 241 only ever left instructions).
  • storeops.merchant_ids       (mig 902) the canonical (org, processor, merchant_id) → store_code map.
                                Store attribution resolves through it. NO new mapping table.
  • portal_backoff              the portal cooldown the whole platform already respects.

WHAT IS HONEST ABOUT THE NAVIGATION. These three portals sit behind a login we cannot reach from CI, so
this module does NOT ship invented CSS selectors dressed up as verified ones. It reaches a report by:
  (1) the per-source CALIBRATION (data_source.portal_calibration) an operator captures ONCE on the live
      screencast — the authoritative path once it exists; then
  (2) the adapter's TEXT hints via the same visible-text walker every other sweep here uses; and
  (3) a CSV/XLSX DOWNLOAD, which is what all three portals offer and is far more stable than scraping a
      rendered grid.
When none of those reach a report, the pull returns a NAMED, actionable failure ("could not reach the
'Deposits' report — calibrate this source on the live login") instead of silently delivering 0 rows.
That distinction matters: this platform's own standard is that a 0-row pull is never a green tick.

NO PROD WRITES FROM A DEV BOX: ingestion goes through the injected supabase client, and every parser is
importable and provable without a browser or a database (harness_merchant_portals.py).
"""
import csv
import io
import os
import tempfile
from datetime import date, datetime, timedelta, timezone

from app.modules.commcalc import merchant_portals as mp

DEFAULT_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 45          # a daily pull that re-fetches months is how a portal starts rate-limiting
_DOWNLOAD_TIMEOUT_MS = 120000


class MerchantPortalError(Exception):
    """A named, operator-actionable portal failure. Never carries a credential."""


def _vp():
    """Lazy import of the shared browser driver (keeps Playwright import cost off module load)."""
    from app.modules.commcalc import vidapay_sweep as vp
    return vp


# ── pure helpers (proven by harness_merchant_portals.py) ─────────────────────────────────────────
def window_days(src_row, portal_key):
    """How many days back one pull re-fetches. Per-source config first, then the portal's house default,
    clamped to MAX_WINDOW_DAYS. Portals restate recent days (a chargeback, a late batch), so a daily
    pull deliberately re-fetches a few days and the upsert restates them in place."""
    for v in (((src_row or {}).get("portal_window_days")),
              ((mp.portal(portal_key) or {}).get("date_window_days"))):
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return min(n, MAX_WINDOW_DAYS)
    return DEFAULT_WINDOW_DAYS


def date_range(src_row, portal_key, today=None):
    """(from_iso, to_iso) for a pull. Ends YESTERDAY by default: a processor's business day is not final
    until it closes, so pulling today's partial figures would hand the recon a number that changes."""
    t = today or date.today()
    if isinstance(t, datetime):
        t = t.date()
    end = t - timedelta(days=1)
    start = end - timedelta(days=max(0, window_days(src_row, portal_key) - 1))
    return start.isoformat(), end.isoformat()


def read_table(payload, filename=""):
    """Report payload → list-of-lists (header row first). PURE.

    Accepts CSV/TSV text or bytes (what all three portals export) and an already-parsed list of lists.
    XLSX is delegated to the platform's existing workbook reader at the call site — this function's job
    is to never let an encoding or delimiter quirk become a parsing bug that reaches money."""
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
    # Portals commonly print a title/date banner above the real header. The header is the first row
    # whose cells map to at least two known fields — anything above it is a banner, not data.
    for i, r in enumerate(rows[:12]):
        if len(mp.map_headers(r)) >= 2:
            return [list(x) for x in rows[i:]]
    return [list(x) for x in rows]


def parse_report(portal_key, spec, payload, *, src_row=None, filename=""):
    """One downloaded report → normalized rows, via the adapter's PURE normalizer. Batch-grain reports
    go to normalize_batches, everything else to normalize_settlement. No DB, no browser."""
    src_row = src_row or {}
    table = read_table(payload, filename)
    cal = (src_row.get("portal_calibration") or {})
    per_report_cal = dict(cal)
    per_report_cal.update((cal.get(spec["key"]) or {}) if isinstance(cal.get(spec["key"]), dict) else {})
    kw = dict(source_id=src_row.get("id"), org_id=src_row.get("org_id"),
              role_override=src_row.get("settlement_role"), calibration=per_report_cal,
              report_key=spec["key"], merchant_id_default=src_row.get("account_id"))
    if spec.get("grain") == "batch":
        return mp.normalize_batches(portal_key, table, **kw)
    return mp.normalize_settlement(portal_key, table, **kw)


# ── store attribution — through the canonical map, never guessed ─────────────────────────────────
def resolve_stores(org_id, portal_key, rows):
    """Stamp each row's store_code from storeops.store_merchant_id (mig 902), the SAME canonical
    (org, processor, merchant_id) → store_code map the ePay ingest uses. Returns the unresolved merchant
    ids so the operator can map them in the EXISTING store-setup panel — no new mapping UI, no guessing.

    A row whose merchant id is unmapped keeps store_code=None. It is still STORED (the money is real and
    the evidence must not be dropped) but it is excluded from per-store totals and reported, because
    silently attributing it to no store would make a store's card total read low in the recon."""
    from app.modules.storeops import merchant_ids as _mids
    try:
        tmap = {str(k).strip().upper(): v for k, v in (_mids.resolve_map(org_id, portal_key) or {}).items()}
    except Exception:
        tmap = {}
    unresolved = {}
    for r in rows or []:
        mid = str(r.get("merchant_id") or "").strip()
        tid = str(r.get("terminal_id") or "").strip()
        sc = tmap.get(mid.upper()) or (tmap.get(tid.upper()) if tid else None)
        r["store_code"] = sc
        if not sc:
            key = mid or tid
            if key and key not in unresolved:
                unresolved[key] = {"merchant_id": mid or None, "terminal_id": tid or None,
                                   "store_label": r.get("store_label"), "portal_key": portal_key}
    return sorted(unresolved.values(), key=lambda x: str(x.get("merchant_id") or ""))


# ── persistence ──────────────────────────────────────────────────────────────────────────────────
_SETTLEMENT_COLS = ("org_id", "source_id", "portal_key", "report_key", "settlement_role",
                    "business_date", "merchant_id", "terminal_id", "store_label", "store_code",
                    "card_brand", "gross_amount", "refund_amount", "net_amount", "fee_amount",
                    "txn_count", "currency", "batch_ref", "source_line", "raw")
_BATCH_COLS = ("org_id", "source_id", "portal_key", "report_key", "settlement_role", "deposit_date",
               "batch_date", "merchant_id", "terminal_id", "store_label", "store_code", "batch_ref",
               "deposit_amount", "fee_amount", "txn_count", "currency", "source_line", "raw")


def _project(rows, cols, org_id, source_id):
    out = []
    for r in rows or []:
        d = {c: r.get(c) for c in cols}
        d["org_id"] = org_id
        d["source_id"] = source_id
        out.append(d)
    return out


def store_settlement(client, org_id, source_id, rows, chunk=400):
    """Upsert settlement rows on the mig-955 natural key. Deduped FIRST: several export lines can share
    (merchant, day, brand) — one per terminal or per batch — and the table's grain is the day, so they
    must be SUMMED before the upsert. Upserting them one by one would let the last line overwrite the
    rest and a store's day would read as one terminal's total."""
    rows = mp.dedupe_settlement(rows or [])
    payload = _project(rows, _SETTLEMENT_COLS, org_id, source_id)
    saved = 0
    for i in range(0, len(payload), chunk):
        client.schema("commcalc").table("merchant_settlement_day").upsert(
            payload[i:i + chunk],
            on_conflict="org_id,source_id,merchant_id,business_date,card_brand").execute()
        saved += len(payload[i:i + chunk])
    return saved


def store_batches(client, org_id, source_id, rows, chunk=400):
    """Upsert funding/deposit batches on their own natural key (a different grain from settlement)."""
    payload = _project(rows or [], _BATCH_COLS, org_id, source_id)
    saved = 0
    for i in range(0, len(payload), chunk):
        client.schema("commcalc").table("merchant_settlement_batch").upsert(
            payload[i:i + chunk],
            on_conflict="org_id,source_id,merchant_id,deposit_date,batch_ref").execute()
        saved += len(payload[i:i + chunk])
    return saved


def ingest_report(client, org_id, src_row, portal_key, spec, payload, filename=""):
    """Parse → resolve stores → persist ONE report. Returns an honest per-report result: `delivered` is
    True only when rows actually landed, mirroring router._pull_delivered so the live session, the
    data_source stamp and the UI can never disagree about what success means."""
    parsed = parse_report(portal_key, spec, payload, src_row=src_row, filename=filename)
    rows = parsed["rows"]
    unresolved = resolve_stores(org_id, portal_key, rows)
    saved = 0
    if rows:
        if spec.get("grain") == "batch":
            saved = store_batches(client, org_id, src_row.get("id"), rows)
        else:
            saved = store_settlement(client, org_id, src_row.get("id"), rows)
    return {"report": spec["key"], "label": spec.get("label"), "rows_parsed": len(rows),
            "rows_ingested": saved, "delivered": saved > 0,
            "skipped": parsed["skipped"][:20], "warnings": parsed["warnings"],
            "unresolved_merchants": unresolved[:50],
            "unresolved_count": len(unresolved)}


# ── browser navigation (calibration-first, hints-second, honest failure third) ───────────────────
def _calibrated(src_row, report_key, field):
    cal = ((src_row or {}).get("portal_calibration") or {}).get(report_key) or {}
    v = cal.get(field)
    return v if isinstance(v, str) and v.strip() else None


def _click_text(page, words, timeout_ms=15000):
    """Click the first VISIBLE control whose text matches any of `words`. Mirrors epay_sweep._click_visible
    (the portal grids here render hidden duplicate controls too — a bare text= selector clicks the first
    node, often an invisible one, and the report silently never runs)."""
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


def _navigate_to_report(page, src_row, portal_key, spec):
    """Reach one report. Calibrated selector first (authoritative once an operator captured it on the
    live screencast), then the adapter's text hints. Returns True when we believe we arrived."""
    vp = _vp()
    sel = _calibrated(src_row, spec["key"], "nav_selector")
    if sel:
        try:
            page.click(sel, timeout=20000)
            vp._wait_settle(page)
            return True
        except Exception:
            pass                                  # a stale calibration falls back to the hints
    url = _calibrated(src_row, spec["key"], "report_url")
    if url:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            vp._wait_settle(page)
            return True
        except Exception:
            pass
    hit = False
    for word in (spec.get("nav") or ()):
        if _click_text(page, [word.title(), word.upper(), word], timeout_ms=8000):
            hit = True
            try:
                vp._wait_settle(page)
            except Exception:
                pass
    return hit


def _download_report(page, src_row, spec, dest_dir):
    """Trigger the report's export and return (path, filename), or (None, None). Prefers the calibrated
    export control; otherwise tries the usual export words. Uses Playwright's download interception —
    the file the portal itself generates, not a scrape of the rendered grid."""
    export_sel = _calibrated(src_row, spec["key"], "export_selector")
    words = ["Export", "Download", "CSV", "Export to CSV", "Excel", "Export Report"]
    try:
        with page.expect_download(timeout=_DOWNLOAD_TIMEOUT_MS) as dl:
            if export_sel:
                page.click(export_sel, timeout=20000)
            elif not _click_text(page, words, timeout_ms=20000):
                raise MerchantPortalError("no export control found")
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


def pull_reports_on_page(client, org_id, src_row, page, should_stop=None):
    """Pull EVERY enabled report for this source on an ALREADY-AUTHENTICATED page.

    This is the single pull implementation. Both entry points reach it:
      • the LIVE session (live_login's pull_fn) — runs on the very browser that passed 2FA, which is
        why the durable-session approach works at all on portals that re-challenge a cold restore;
      • the SCHEDULED pull — a cold context restored from the saved storage_state (below).
    One implementation, so the daily result can never differ from what the operator saw."""
    portal_key = (src_row.get("processor") or "").strip().lower()
    specs = mp.report_specs(portal_key, src_row.get("portal_reports"))
    if not specs:
        return {"ok": False, "delivered": False, "rows_ingested": 0,
                "error": "No reports are enabled for this portal source."}
    d_from, d_to = date_range(src_row, portal_key)
    results, total, unreachable = [], 0, []
    with tempfile.TemporaryDirectory() as tmp:
        for spec in specs:
            if should_stop is not None and should_stop():
                break
            if not _navigate_to_report(page, src_row, portal_key, spec):
                unreachable.append(spec["label"])
                results.append({"report": spec["key"], "label": spec["label"], "rows_ingested": 0,
                                "delivered": False,
                                "error": "Could not reach this report — calibrate it on the live login."})
                continue
            path, name = _download_report(page, src_row, spec, tmp)
            if not path:
                unreachable.append(spec["label"])
                results.append({"report": spec["key"], "label": spec["label"], "rows_ingested": 0,
                                "delivered": False,
                                "error": "Reached the report but found no export control — calibrate "
                                         "export_selector for this source on the live login."})
                continue
            try:
                res = ingest_report(client, org_id, src_row, portal_key, spec, _read_download(path), name)
            except Exception as e:
                res = {"report": spec["key"], "label": spec["label"], "rows_ingested": 0,
                       "delivered": False, "error": ("Ingest failed: %s" % e)[:300]}
            results.append(res)
            total += res.get("rows_ingested") or 0
    unresolved = []
    for r in results:
        for u in (r.get("unresolved_merchants") or []):
            if u not in unresolved:
                unresolved.append(u)
    # HONEST outcome: delivered only when rows actually landed. A 0-row pull is never a green tick —
    # that is exactly how a dead connector used to look healthy.
    status = ("Imported %d row(s) across %d report(s) for %s..%s."
              % (total, sum(1 for r in results if r.get("delivered")), d_from, d_to))
    if unreachable:
        status += " Not reached: %s." % ", ".join(unreachable[:4])
    if unresolved:
        status += (" %d merchant id(s) are not mapped to a store yet — map them in store setup or their "
                   "money stays out of the store totals." % len(unresolved))
    return {"ok": True, "delivered": total > 0, "rows_ingested": total, "status": status,
            "date_from": d_from, "date_to": d_to, "reports": results,
            "unresolved_merchants": unresolved[:50]}


def make_pull_fn(client, org_id, src_row):
    """The `pull_fn` live_login.start_session() already accepts — so a merchant-portal pull runs on the
    LIVE browser that just passed 2FA, and again on every '▶ Pull now'. Accepts the optional should_stop
    (live_login inspects arity), so an operator's Cancel stops between reports."""
    def _pull(page, should_stop=None):
        return pull_reports_on_page(client, org_id, src_row, page, should_stop)
    return _pull


def run_merchant_portal_sweep(client, org_id, src_row):
    """The SCHEDULED (cold) pull — restores the durable session saved by the live login and pulls.

    Raises vidapay_sweep.VidaPayAuthError when there is no usable session, which run_data_source already
    turns into an auth_status=needs_2fa prompt (a human is asked to re-link) rather than a hard error.
    That is the whole 2FA story for the daily run: no code is ever needed while the session holds."""
    vp = _vp()
    portal_key = (src_row.get("processor") or "").strip().lower()
    if not mp.is_portal(portal_key):
        raise MerchantPortalError("Unknown merchant portal: %s" % portal_key)
    state = src_row.get("session_state")
    if not state:
        raise vp.VidaPayAuthError(
            "This merchant portal has never been linked. Open the live login and complete the portal's "
            "second factor once; the saved session then drives the daily pull.")
    from app.core.service_role import assert_browser_allowed
    assert_browser_allowed()
    from playwright.sync_api import sync_playwright
    base = (mp.portal(portal_key) or {}).get("base_url")
    with sync_playwright() as p:
        browser = vp._launch(p)
        try:
            ctx = vp._new_context(browser, storage_state=state,
                                  proxy=vp._proxy_arg(src_row.get("proxy_url")))
            page = ctx.new_page()
            page.goto(vp._norm_url(src_row.get("portal_url"), base),
                      wait_until="domcontentloaded", timeout=60000)
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


def session_ttl_hint(portal_key):
    """Best-effort expiry to stamp when a portal publishes none. Deliberately conservative: assuming a
    long life is how a dead session hides until the recon has a hole in it."""
    return int((mp.portal(portal_key) or {}).get("session_ttl_hours") or 8)


def next_expiry(portal_key, now=None):
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(hours=session_ttl_hint(portal_key))).isoformat()
