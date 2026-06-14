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
        raise VipLoginError("VIP login page layout changed (no anti-forgery token)")
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
        raise VipLoginError("VIP login failed — check the credentials in the admin area "
                            "(or the portal may have added 2FA).")
    return True


def _fetch(session, path):
    r = session.get(f"{BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.text


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

    inv_rows, line_rows, dev_rows, vip_ids = [], [], [], []
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
        except Exception:
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

    # Upsert invoices on the (org_id, vip_id) unique key; replace lines/devices for the
    # swept invoices only (delete-by-invoice then insert) so unchanged history is untouched.
    for i in range(0, len(inv_rows), 500):
        client.schema("commcalc").table("vip_invoices").upsert(
            inv_rows[i:i + 500], on_conflict="org_id,vip_id").execute()
    for i in range(0, len(vip_ids), 100):
        chunk = vip_ids[i:i + 100]
        for tbl in ("vip_invoice_lines", "vip_invoice_devices"):
            client.schema("commcalc").table(tbl).delete() \
                .eq("org_id", org_id).in_("vip_invoice_id", chunk).execute()
    for tbl, rows in (("vip_invoice_lines", line_rows), ("vip_invoice_devices", dev_rows)):
        for i in range(0, len(rows), 500):
            client.schema("commcalc").table(tbl).insert(rows[i:i + 500]).execute()

    return {"invoices": len(inv_rows), "lines": len(line_rows),
            "devices": len(dev_rows), "window": f"{sdate} → {edate}"}
