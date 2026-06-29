"""VIP Wireless dealer-portal auto-sweep — runs INSIDE the backend (Railway), so it can
be scheduled unattended (the Codespace scraper in tools/vip_scraper only runs manually).

Driven by Supabase pg_cron → POST /commcalc/vip/sweep/run-due (same pattern as notify).
Credentials + schedule live in commcalc.vip_sweep_config, a BACKEND-ONLY table (the
password is never returned to the browser). Incremental: each sweep pulls invoices in a
lookback window and UPSERTs them, so it does not re-scrape the full history every run.

Login + InvoiceList JSON + invoicedetails HTML parsing are ported verbatim from
tools/vip_scraper/scrape.py (parser finalized 2026-06-13).
"""
import re
from datetime import datetime, timedelta, timezone
import requests
from bs4 import BeautifulSoup

BASE = "https://www.vipwireless.com"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")
_MONEY_RE = re.compile(r"[^0-9.\-]")


class VipLoginError(Exception):
    """Login failed — surfaced to the admin UI without ever echoing the password."""


def _soup(html):
    return BeautifulSoup(html, "html.parser")


def _money(s):
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    neg = "(" in s and ")" in s
    v = _MONEY_RE.sub("", s.replace(",", ""))
    if v in ("", "-", "."):
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    return -f if neg else f


def login(session, user, pw):
    """ASP.NET anti-forgery login. Raises VipLoginError on failure."""
    r = session.get(f"{BASE}/login?ReturnUrl=%2Finvoice%2Fhistory", timeout=30)
    r.raise_for_status()
    s = _soup(r.text)
    form = s.find("form", attrs={"action": re.compile(r"/login", re.I)}) or s.find("form")
    token_el = (form or s).find("input", attrs={"name": "__RequestVerificationToken"})
    if not token_el:
        raise VipLoginError("Distributor login page layout changed (no anti-forgery token)")
    payload = {
        "Email": user,
        "Password": pw,
        "RememberMe": "true",
        "__RequestVerificationToken": token_el.get("value", ""),
    }
    ru = (form or s).find("input", attrs={"name": "ReturnUrl"})
    if ru:
        payload["ReturnUrl"] = ru.get("value", "/invoice/history")
    session.post(f"{BASE}/login?ReturnUrl=%2Finvoice%2Fhistory", data=payload,
                 headers={"Referer": f"{BASE}/login"}, allow_redirects=True, timeout=30)
    chk = session.get(f"{BASE}/invoice/history", allow_redirects=False, timeout=30)
    ok = chk.status_code == 200 and "/login" not in chk.headers.get("Location", "")
    if not ok:
        raise VipLoginError("Distributor login failed — check the credentials in the admin area "
                            "(or the portal may have added 2FA).")
    return True


def _fetch(session, path):
    r = session.get(f"{BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.text


def run_asset_ledger_sweep(client, org_id, user, pw):
    """Download Asset_Lending.xlsx and refresh commcalc.asset_ledger.

    Source = the 'Asset Lending' download icon on /account/dashboard, whose link is a direct
    GET /paygodashboard/DownloadAssetLanding (file 'Asset_Lending.xlsx', sheet 'DataSheet' — the
    per-device PayGo ledger: Category/ESN/Owed to VIP/Reimbursement/Status/dates/SFID/…). Reuses
    the asset module's upload processing (parse + market/selling-price backfill + flag syncs).
    Never wipes the ledger on an empty/non-Excel download."""
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    login(session, user, pw)
    r = session.get(f"{BASE}/paygodashboard/DownloadAssetLanding",
                    headers={"Referer": f"{BASE}/account/dashboard"}, timeout=240)
    r.raise_for_status()
    ct = r.headers.get("Content-Type", "").lower()
    data = r.content
    if not any(k in ct for k in ("sheet", "excel", "octet")) or len(data) < 2000:
        raise RuntimeError(f"Asset Lending download was not a valid Excel file (ct={ct[:40]}, {len(data)} bytes)")
    from app.modules.asset.router import process_asset_ledger_bytes
    res = process_asset_ledger_bytes(data, org_id)
    return {"rows": res.get("rows_imported", 0), "bytes": len(data)}


def run_chargeback_sweep(client, org_id, user, pw):
    """Download the VIP chargebacks export (GET /paygodashboard/DownloadFile →
    Dealer-NNNNN-Chargebacks.xlsx) and stage each per-ESN incentive-credit clawback into
    commcalc.chargeback_review (source='vip_file', status OPEN). They are then ASSIGNED to the rep
    who did the sale, which pushes them into the employee chargeback file. Re-sweep PRESERVES any
    existing assignment (upsert omits the status/assignment columns)."""
    import io
    import pandas as pd
    from dateutil import parser as _dp
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    login(session, user, pw)
    r = session.get(f"{BASE}/paygodashboard/DownloadFile",
                    headers={"Referer": f"{BASE}/account/paygo/dashboard"}, timeout=240)
    r.raise_for_status()
    data = r.content
    ct = r.headers.get("Content-Type", "").lower()
    if not any(k in ct for k in ("sheet", "excel", "octet")) or len(data) < 1000:
        raise RuntimeError(f"Chargebacks download was not a valid Excel file (ct={ct[:40]})")
    df = pd.read_excel(io.BytesIO(data), dtype=str).fillna("")
    norm = {re.sub(r"[^a-z0-9]", "", c.lower()): c for c in df.columns}

    def col(*names):
        for n in names:
            if n in norm:
                return norm[n]
        return None

    cmap = {"customer_no": col("customerid"), "salesforceid": col("salesforceid"),
            "subscriber": col("subscriberid"), "esn": col("esn"), "imei": col("imei"),
            "phone": col("phonenumber"), "brand": col("brand"), "plan": col("plan"),
            "orig": col("originalincentivecredit"), "corr": col("correctedincentivecredit"),
            "amount": col("chargebackamount"), "pdate": col("chargebackprocessingdate")}
    sm = (client.schema("commcalc").table("store_mapping")
          .select("salesforce_id,store_code,store_address").eq("org_id", org_id).execute().data) or []
    by_sfid = {(s.get("salesforce_id") or "").strip(): s for s in sm if s.get("salesforce_id")}

    def _per(d):
        try:
            return _dp.parse(str(d)).strftime("%B %Y")
        except Exception:
            return ""

    rows = []
    for _, x in df.iterrows():
        def g(k):
            c = cmap.get(k)
            return str(x.get(c) or "").strip() if c else ""
        esn, imei = g("esn"), g("imei")
        amt = _money(g("amount")) or 0.0
        if not (esn or imei) and not amt:
            continue
        smr = by_sfid.get(g("salesforceid"), {})
        pdate = g("pdate")
        orig, corr = _money(g("orig")), _money(g("corr"))
        detail = "Distributor incentive chargeback" + (f" (incentive {orig}→{corr})" if orig is not None and corr is not None else "")
        rows.append({
            "org_id": org_id, "source": "vip_file", "severity": "warning",
            "store_code": smr.get("store_code"), "store_address": smr.get("store_address"),
            "period": _per(pdate), "occurred_date": pdate,
            "customer_name": g("subscriber") or None, "customer_no": g("customer_no") or None,
            "phone_number": g("phone") or None, "esn": esn or None, "imei": imei or None,
            "brand": g("brand") or None, "plan": g("plan") or None,
            "amount": abs(amt), "detail": detail,
            "dedupe_key": "vip:" + "|".join(str(g(k) or "") for k in
                                            ("esn", "imei", "pdate", "amount", "phone", "customer_no")),
            "raw": {k: g(k) for k in cmap},
        })
    if not rows:
        raise RuntimeError("Chargebacks file parsed to 0 rows")
    # De-dupe within the batch: the export can list the same (esn/date/amount/phone) row twice;
    # identical rows collapse to one, else Postgres rejects the upsert with "ON CONFLICT DO UPDATE
    # command cannot affect row a second time".
    rows = list({r["dedupe_key"]: r for r in rows}.values())
    for i in range(0, len(rows), 500):
        client.schema("commcalc").table("chargeback_review").upsert(
            rows[i:i + 500], on_conflict="org_id,dedupe_key").execute()
    return {"rows": len(rows)}


def list_invoices(session, sdate, edate, page_size=100):
    """Page through POST /Invoice/InvoiceList (MM/dd/yyyy dates). Returns the JSON rows."""
    rows, skip, page, total = [], 0, 1, None
    while True:
        data = {"startingDate": sdate, "endingDate": edate,
                "take": page_size, "skip": skip, "page": page, "pageSize": page_size}
        r = session.post(f"{BASE}/Invoice/InvoiceList", data=data,
                         headers={"X-Requested-With": "XMLHttpRequest",
                                  "Referer": f"{BASE}/invoice/history"}, timeout=30)
        r.raise_for_status()
        j = r.json()
        if j.get("Errors"):
            raise RuntimeError(f"InvoiceList returned errors: {j['Errors']}")
        batch = j.get("Data") or []
        total = j.get("Total")
        rows.extend(batch)
        if not batch or (total is not None and len(rows) >= total):
            break
        page += 1
        skip += page_size
    return rows


def _header_set(t):
    return {th.get_text(" ", strip=True) for th in t.find_all("th")}


def parse_detail(session, inv_id):
    """(line_items, devices) for one invoice. Tables located by header signature."""
    html = _fetch(session, f"/invoicedetails/{inv_id}")
    s = _soup(html)
    line_tbl = dev_tbl = None
    for t in s.find_all("table"):
        hs = _header_set(t)
        if {"Name", "Price", "Total"} <= hs:
            line_tbl = t
        elif {"Serial Number", "IMEI"} <= hs:
            dev_tbl = t

    lines = []
    if line_tbl:
        for row in line_tbl.find_all("tr")[1:]:
            name_cell = row.select_one('[data-label="Name"]')
            note_el = row.select_one(".invoice-note")
            name_el = name_cell.select_one("em a") or name_cell.select_one("em") if name_cell else None
            if name_el:
                name = name_el.get_text(" ", strip=True)
            elif name_cell:
                note_txt = note_el.get_text(" ", strip=True) if note_el else ""
                full = name_cell.get_text(" ", strip=True)
                name = full.replace(note_txt, "").strip() if note_txt else full
            else:
                name = None
            sku_el = row.select_one(".sku-number")
            price_el = row.select_one(".product-unit-price")
            qty_el = row.select_one(".product-quantity")
            tot_el = row.select_one(".product-subtotal")
            if not (name or price_el or tot_el):
                continue
            lines.append({
                "Name": name,
                "Note": note_el.get_text(" ", strip=True) if note_el else None,
                "SKU": (sku_el.get_text(" ", strip=True) if sku_el else None) or None,
                "Price": price_el.get_text(" ", strip=True) if price_el else None,
                "Quantity": qty_el.get_text(" ", strip=True) if qty_el else None,
                "Total": tot_el.get_text(" ", strip=True) if tot_el else None,
            })

    devices = []
    if dev_tbl:
        for row in dev_tbl.find_all("tr")[1:]:
            cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
            if len(cells) >= 4 and any(cells):
                devices.append({"Serial": cells[0], "ProductName": cells[1],
                                "IMEI": cells[2], "SIM": cells[3]})
    return lines, devices


def run_invoice_sweep(client, org_id, user, pw, lookback_days, helpers):
    """Login, pull invoices in [today-lookback, today], UPSERT invoices and replace the
    line/device rows for the swept invoices. `helpers` = the router's
    (_vip_money, _vip_int, _vip_ts, _vip_period) so storage exactly matches manual upload.
    Returns a small summary dict (counts + window). Raises on login / network errors."""
    _vip_money, _vip_int, _vip_ts, _vip_period = helpers
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    login(session, user, pw)

    today = datetime.now(timezone.utc).date()
    sdate = (today - timedelta(days=max(1, lookback_days))).strftime("%m/%d/%Y")
    edate = today.strftime("%m/%d/%Y")
    raw = list_invoices(session, sdate, edate)

    inv_rows, line_rows, dev_rows, vip_ids, parsed_ids = [], [], [], [], []
    for inv in raw:
        vid = _vip_int(inv.get("Id"))
        if vid is None:
            continue
        vip_ids.append(vid)
        period, pm, py = _vip_period(inv.get("CreatedOn", ""))
        inv_no = str(inv.get("InvoiceNumber", "")).strip() or None
        inv_rows.append({
            "org_id": org_id, "vip_id": vid,
            "invoice_number": inv_no,
            "order_number": str(inv.get("OrderNumber", "")).strip() or None,
            "location": inv.get("Location") or None,
            "company_id": _vip_int(inv.get("CompanyId")),
            "email": inv.get("Email") or None,
            "status": inv.get("Status") or None,
            "sub_total": _vip_money(inv.get("SubTotal")),
            "shipping": _vip_money(inv.get("Shipping")),
            "discount": _vip_money(inv.get("Discount")),
            "other_cost": _vip_money(inv.get("OtherCost")),
            "other_deductions": _vip_money(inv.get("OtherDeductions")),
            "tax": _vip_money(inv.get("Tax")),
            "grand_total": _vip_money(inv.get("GrandTotal")),
            "note": inv.get("Note") or None,
            "created_on": _vip_ts(inv.get("CreatedOn")),
            "transaction_date": _vip_ts(inv.get("TransactionDate")),
            "due_date": _vip_ts(inv.get("DueDate")),
            "period": period, "period_month": pm, "period_year": py,
        })
        try:
            lines, devices = parse_detail(session, inv.get("Id"))
            parsed_ids.append(vid)   # detail fetched OK — safe to replace this invoice's lines/devices
        except Exception:
            # Detail fetch/parse FAILED — leave this invoice's existing lines/devices alone (a flaky
            # detail page would otherwise silently erase good history). Not added to parsed_ids.
            lines, devices = [], []
        for ln in lines:
            line_rows.append({
                "org_id": org_id, "vip_invoice_id": vid, "invoice_number": inv_no,
                "location": inv.get("Location") or None, "status": inv.get("Status") or None,
                "created_on": _vip_ts(inv.get("CreatedOn")),
                "name": ln.get("Name") or None, "note": ln.get("Note") or None,
                "sku": ln.get("SKU") or None,
                "price": _vip_money(ln.get("Price")),
                "quantity": _vip_money(ln.get("Quantity")),
                "total": _vip_money(ln.get("Total")),
                "period": period, "period_month": pm, "period_year": py,
            })
        for dv in devices:
            dev_rows.append({
                "org_id": org_id, "vip_invoice_id": vid, "invoice_number": inv_no,
                "location": inv.get("Location") or None,
                "created_on": _vip_ts(inv.get("CreatedOn")),
                "serial": str(dv.get("Serial", "")).strip() or None,
                "product_name": dv.get("ProductName") or None,
                "imei": str(dv.get("IMEI", "")).strip() or None,
                "sim": str(dv.get("SIM", "")).strip() or None,
                "period": period, "period_month": pm, "period_year": py,
            })

    # Upsert invoices on the (org_id, vip_id) unique key; replace lines/devices ONLY for invoices
    # whose detail parse succeeded this run (delete-by-invoice then insert) so a flaky detail fetch
    # can't erase previously-good line/device history, and unchanged invoices are untouched.
    for i in range(0, len(inv_rows), 500):
        client.schema("commcalc").table("vip_invoices").upsert(
            inv_rows[i:i + 500], on_conflict="org_id,vip_id").execute()
    for i in range(0, len(parsed_ids), 100):
        chunk = parsed_ids[i:i + 100]
        for tbl in ("vip_invoice_lines", "vip_invoice_devices"):
            client.schema("commcalc").table(tbl).delete() \
                .eq("org_id", org_id).in_("vip_invoice_id", chunk).execute()
    for tbl, rows in (("vip_invoice_lines", line_rows), ("vip_invoice_devices", dev_rows)):
        for i in range(0, len(rows), 500):
            client.schema("commcalc").table(tbl).insert(rows[i:i + 500]).execute()

    return {"invoices": len(inv_rows), "lines": len(line_rows),
            "devices": len(dev_rows), "window": f"{sdate} → {edate}"}


# ── PayGo / asset-lending (weekly lent-device billing) ──────────────────────────────
# The portal bills "asset-lending" (Pay-As-You-Go) devices on a weekly cycle. Each weekly
# batch is one payment with N invoices + a grand total:
#   POST /PaygoPayment/PendingPaymentList   -> current week owed (1 batch)
#   POST /PaygoPayment/ApprovedPaymentList  -> weekly history
#   GET  /account/paygo/payments/details/{Id} -> invoice numbers in that batch (HTML table)
# Discovered + parser validated 2026-06-14 (see tools/vip_scraper/discover_asset.py).

def _paygo_date(s):
    """'06/12/2026' or '6/11/2026' -> ('2026-06-12', 'June 2026', 6, 2026). '' -> (None,...)."""
    import calendar as _cal
    s = str(s or "").strip()
    if not s:
        return None, None, None, None
    try:
        m, d, y = [int(x) for x in s.split("/")[:3]]
        return f"{y:04d}-{m:02d}-{d:02d}", f"{_cal.month_name[m]} {y}", m, y
    except Exception:
        return None, None, None, None


def list_paygo_payments(session, endpoint, page_size=100):
    """Page through a PaygoPayment list endpoint (Kendo grid JSON). Returns the rows."""
    rows, skip, page, total = [], 0, 1, None
    while True:
        r = session.post(f"{BASE}{endpoint}",
                         data={"take": page_size, "skip": skip, "page": page, "pageSize": page_size},
                         headers={"X-Requested-With": "XMLHttpRequest",
                                  "Referer": f"{BASE}/account/paygo/payments/dashboard"}, timeout=120)
        r.raise_for_status()
        j = r.json()
        if isinstance(j, dict) and j.get("Errors"):
            raise RuntimeError(f"{endpoint} returned errors: {j['Errors']}")
        batch = (j.get("Data") if isinstance(j, dict) else j) or []
        total = j.get("Total") if isinstance(j, dict) else None
        rows.extend(batch)
        if not batch or (total is not None and len(rows) >= total):
            break
        page += 1
        skip += page_size
    return rows


def parse_payment_invoices(session, payment_id):
    """Invoice numbers inside one weekly PayGo batch, from its details page.
    table[0] headers: 'Dealer Name/Address', 'Invoice Number'. Returns [{dealer, invoice_number}]."""
    html = _fetch(session, f"/account/paygo/payments/details/{payment_id}")
    s = _soup(html)
    out = []
    tables = s.find_all("table")
    if not tables:
        return out
    for tr in tables[0].find_all("tr")[1:]:
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) >= 2 and cells[1].isdigit():
            out.append({"dealer": cells[0] or None, "invoice_number": cells[1]})
    return out


def run_paygo_sweep(client, org_id, user, pw, lookback_days, session=None):
    """Login (or reuse `session`), pull the PayGo pending + approved weekly batches, upsert
    them, and refresh the invoice-number links for the pending batch + approved batches whose
    created_on falls within `lookback_days`. Full history of batches is upserted (cheap: 2 list
    calls); only recent batches' invoice details are (re)fetched so the run stays bounded.
    Returns a summary dict. Raises VipLoginError / network errors to the caller."""
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": UA})
        login(session, user, pw)

    pending = list_paygo_payments(session, "/PaygoPayment/PendingPaymentList")
    approved = list_paygo_payments(session, "/PaygoPayment/ApprovedPaymentList")

    pay_rows, detail_targets = [], []
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=max(1, lookback_days))
    for batch_type, recs in (("pending", pending), ("approved", approved)):
        for rec in recs:
            pid = rec.get("Id")
            if pid is None:
                continue
            try:
                pid = int(pid)
            except (TypeError, ValueError):
                continue
            iso, period, pm, py = _paygo_date(rec.get("CreatedOn"))
            pay_rows.append({
                "org_id": org_id, "vip_payment_id": pid, "batch_type": batch_type,
                "dealer": rec.get("Dealer") or None,
                "created_on": iso,
                "invoice_count": _to_int(rec.get("InvoiceCount")),
                "amount": _money(rec.get("Amount")),
                "amount_overdue": _money(rec.get("AmountOverdue")),
                "status": rec.get("Status") or None,
                "period": period, "period_month": pm, "period_year": py,
            })
            # refresh invoice links for the pending batch + recent approved batches
            recent = batch_type == "pending" or (iso and iso >= cutoff.isoformat())
            if recent:
                detail_targets.append((pid, iso))

    for i in range(0, len(pay_rows), 500):
        client.schema("commcalc").table("vip_paygo_payments").upsert(
            pay_rows[i:i + 500], on_conflict="org_id,vip_payment_id").execute()

    # Replace invoice links for the targeted batches only (delete-by-payment then insert).
    inv_link_rows, links = [], 0
    for pid, iso in detail_targets:
        try:
            invs = parse_payment_invoices(session, pid)
        except Exception:
            invs = []
        client.schema("commcalc").table("vip_paygo_payment_invoices").delete() \
            .eq("org_id", org_id).eq("vip_payment_id", pid).execute()
        for v in invs:
            inv_link_rows.append({
                "org_id": org_id, "vip_payment_id": pid,
                "invoice_number": v.get("invoice_number"),
                "dealer": v.get("dealer"), "created_on": iso,
            })
        links += len(invs)
    for i in range(0, len(inv_link_rows), 500):
        client.schema("commcalc").table("vip_paygo_payment_invoices").insert(
            inv_link_rows[i:i + 500]).execute()

    owed = next((p["amount"] for p in pay_rows if p["batch_type"] == "pending"), None)
    return {"payments": len(pay_rows), "pending": len(pending), "approved": len(approved),
            "invoice_links": links, "batches_detailed": len(detail_targets),
            "current_owed": owed}


def _to_int(v):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


# ── Credit memos (Weekly Incentive Credit) for the #10 MI/ATU reconciliation ────────
# VIP pays the MI + ATU residual COMBINED as one "Weekly Incentive Credit" memo per store.
#   POST /CreditMemo/CreditMemoListList -> Kendo grid JSON (same shape as InvoiceList)
#     fields: Id, CreditMemoNumber, Memo, Status, CompanyName, GrandTotal, AmountLinked,
#             Balance, CreatedOn, OrderStatus
# Endpoint + field names per the discovery notes (memory: vip-creditmemo-recon-notify).
# The list fields are enough for the GrandTotal-vs-MI+ATU recon — the line-item Details
# page is not needed, so we don't scrape it here.
_CM_MONTHS = {m.lower(): i for i, m in enumerate(
    ["", "January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"]) if m}
_CM_MONTHS.update({m[:3].lower(): i for m, i in list(_CM_MONTHS.items())})


def _cm_date_range(memo, created_on=None):
    """Best-effort parse of the date range embedded in a credit-memo's Memo text, e.g.
    'Weekly Incentive Credit - 01/01/2026 - 01/07/2026' or '... Jan 1 - Jan 7, 2026'.
    Returns (start_iso, end_iso) or (None, None)."""
    s = str(memo or "")
    # 1) MM/DD/YYYY - MM/DD/YYYY (or M/D/YY)
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4}).{0,5}?(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        def iso(mo, d, y):
            y = int(y); y = y + 2000 if y < 100 else y
            return f"{y:04d}-{int(mo):02d}-{int(d):02d}"
        return iso(m.group(1), m.group(2), m.group(3)), iso(m.group(4), m.group(5), m.group(6))
    # 2) "Mon D - Mon D, YYYY" / "Mon D-D YYYY"
    yr = None
    ym = re.search(r"(20\d{2})", s)
    if ym:
        yr = int(ym.group(1))
    m2 = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})\s*[-–]\s*(?:([A-Za-z]{3,9})\.?\s+)?(\d{1,2})", s)
    if m2 and yr:
        mo1 = _CM_MONTHS.get(m2.group(1).lower())
        mo2 = _CM_MONTHS.get((m2.group(3) or m2.group(1)).lower())
        if mo1 and mo2:
            return f"{yr:04d}-{mo1:02d}-{int(m2.group(2)):02d}", f"{yr:04d}-{mo2:02d}-{int(m2.group(4)):02d}"
    return None, None


def _cm_store_address(company_name):
    """The portal CompanyName is multi-line; line 2 is the store address."""
    parts = [p.strip() for p in str(company_name or "").replace("\r", "").split("\n") if p.strip()]
    if len(parts) >= 2:
        return parts[1]
    return parts[0] if parts else None


def list_credit_memos(session, page_size=200):
    """Page through POST /CreditMemo/CreditMemoListList (Kendo grid JSON)."""
    rows, skip, page, total = [], 0, 1, None
    while True:
        r = session.post(f"{BASE}/CreditMemo/CreditMemoListList",
                         data={"take": page_size, "skip": skip, "page": page, "pageSize": page_size},
                         headers={"X-Requested-With": "XMLHttpRequest",
                                  "Referer": f"{BASE}/CreditMemo"}, timeout=120)
        r.raise_for_status()
        j = r.json()
        if isinstance(j, dict) and j.get("Errors"):
            raise RuntimeError(f"CreditMemoListList returned errors: {j['Errors']}")
        batch = (j.get("Data") if isinstance(j, dict) else j) or []
        total = j.get("Total") if isinstance(j, dict) else None
        rows.extend(batch)
        if not batch or (total is not None and len(rows) >= total):
            break
        page += 1
        skip += page_size
    return rows


def run_creditmemo_sweep(client, org_id, user, pw, helpers, session=None):
    """Login (or reuse `session`), pull ALL credit memos, map the documented list fields,
    flag Xfinity memos (excluded from the recon), resolve the store from CompanyName line 2,
    parse the memo date range, and UPSERT commcalc.vip_credit_memos on (org_id, credit_memo_id).
    `helpers` = (_vip_money, _vip_int, _vip_ts, _vip_period). Returns a summary dict."""
    _vip_money, _vip_int, _vip_ts, _vip_period = helpers
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": UA})
        login(session, user, pw)

    raw = list_credit_memos(session)
    rows, xfin = [], 0
    for cm in raw:
        cid = _vip_int(cm.get("Id"))
        if cid is None:
            continue
        company_name = cm.get("CompanyName") or ""
        memo = cm.get("Memo") or ""
        is_xf = "xfinity" in (company_name + " " + memo).lower()
        if is_xf:
            xfin += 1
        period, pm, py = _vip_period(cm.get("CreatedOn", ""))
        start, end = _cm_date_range(memo, cm.get("CreatedOn"))
        rows.append({
            "org_id": org_id, "credit_memo_id": cid,
            "credit_memo_number": str(cm.get("CreditMemoNumber", "")).strip() or None,
            "memo": memo or None,
            "company_name": company_name or None,
            "store_address": _cm_store_address(company_name),
            "grand_total": _vip_money(cm.get("GrandTotal")),
            "amount_linked": _vip_money(cm.get("AmountLinked")),
            "balance": _vip_money(cm.get("Balance")),
            "status": cm.get("Status") or None,
            "order_status": cm.get("OrderStatus") or None,
            "is_xfinity": is_xf,
            "created_on": _vip_ts(cm.get("CreatedOn")),
            "memo_start": start, "memo_end": end,
            "period": period, "period_month": pm, "period_year": py,
        })
    for i in range(0, len(rows), 500):
        client.schema("commcalc").table("vip_credit_memos").upsert(
            rows[i:i + 500], on_conflict="org_id,credit_memo_id").execute()
    return {"credit_memos": len(rows), "xfinity_excluded": xfin}
