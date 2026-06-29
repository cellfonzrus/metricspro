"""Boost Elevate GO DLAR auto-sweep — runs INSIDE the backend (Railway) so it can be
scheduled unattended, replacing the manual monthly DLAR upload.

Driven by Supabase pg_cron → POST /commcalc/dlar/sweep/run-due (same pattern as notify
and the VIP sweep). Credentials + schedule live in commcalc.dlar_sweep_config, a
BACKEND-ONLY table (the password is never returned to the browser).

The portal (boostelevatego.com) is a server-rendered Laravel app:
  - /login                     POST form: _token (CSRF), email, password   (no 2FA/CAPTCHA)
  - /reports/dlar/inline        DataTables JSON  {records:[...store...], recordsTotal, import_date}
  - /reports/advocate/inline    DataTables JSON  {records:[...rep...],  recordsTotal}
Both /inline endpoints page at 25 rows (length=-1 500s), so we page start/length=100.
Data is month-to-date for the current period; DLAR carries import_date (MM/DD/YYYY).

Login + fetch + normalize are ported from tools/boost_scraper/scrape.py (verified
2026-06-14). The sweep is a full snapshot: it wipes the period's raw_dlar_rep /
raw_dlar_store rows and re-inserts, mirroring the manual monthly upload's column shape
so the commission calculator (which joins by the leading number of the store address)
keeps working unchanged.
"""
import calendar as _calendar
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://boostelevatego.com"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")


class DlarLoginError(Exception):
    """Login failed — surfaced to the admin UI without ever echoing the password."""


class DlarPortalError(Exception):
    """Login worked but the pull was empty/degraded — surfaced as an error (NOT a silent
    'OK — 0 stores') so a bad pull can never quietly wipe the live commission period."""


# Load guards (mirror epay_sweep): never let an empty or drastically-smaller pull REPLACE a
# populated period. The DLAR drives commissions and is auto-recalc'd after each sweep, so a silent
# wipe here zeroes live payouts — this is the protection the epay sweep already had and DLAR lacked.
REPLACE_MIN_ROWS = 20
REPLACE_MIN_RETAIN = 0.5


def _period_count(client, table, org_id, period):
    """Existing row count for (org_id, period) — used by the partial-collapse guard. 0 on error."""
    try:
        resp = (client.schema("commcalc").table(table).select("org_id", count="exact")
                .eq("org_id", org_id).eq("period", period).limit(1).execute())
        return resp.count or 0
    except Exception:
        return 0


def _num(v):
    """Coerce a DLAR cell ('71.43', '-100.00', '0', '', '0% / 71.43%', '55%') to float."""
    if v is None:
        return 0.0
    s = str(v).strip().replace("%", "").replace(",", "")
    if not s or s.lower() in ("n/a", "na", "-", "--", "none"):
        return 0.0
    if "/" in s:                                   # '0% / 71.43%' → take the period value
        s = s.split("/")[-1].strip().replace("%", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def login(session, user, pw):
    """Laravel CSRF form login. Raises DlarLoginError if a protected page stays unreachable."""
    r = session.get(f"{BASE}/login", timeout=30)
    tok_el = BeautifulSoup(r.text, "html.parser").find("input", attrs={"name": "_token"})
    if not tok_el:
        raise DlarLoginError("Carrier login page changed — no CSRF token found.")
    session.post(
        f"{BASE}/login",
        data={"_token": tok_el.get("value", ""), "email": user, "password": pw},
        headers={"Referer": f"{BASE}/login"},
        allow_redirects=False,
        timeout=30,
    )
    chk = session.get(f"{BASE}/reports/dlar", allow_redirects=False, timeout=30)
    if chk.status_code != 200:
        raise DlarLoginError("Carrier login failed — credentials rejected (or account/2FA changed).")


def fetch_report(session, name, page=100):
    """GET /reports/{name}/inline (DataTables JSON), paging start/length to get every row."""
    records, start, import_date = [], 0, None
    while True:
        r = session.get(
            f"{BASE}/reports/{name}/inline",
            params={"draw": 1, "start": start, "length": page},
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/reports/{name}"},
            timeout=120,
        )
        r.raise_for_status()
        j = r.json()
        if import_date is None:
            import_date = j.get("import_date")
        batch = j.get("records", []) or []
        records.extend(batch)
        total = j.get("recordsTotal") or 0
        start += len(batch)
        if not batch or start >= total:
            break
    return records, import_date


def normalize_rep(rec):
    """Advocate (rep) record → raw_dlar_rep row fields (matches the manual-upload mapping)."""
    ga_prepaid = _num(rec.get("prepaid_activations"))
    bounty = _num(rec.get("boost_ready_bounty"))
    return {
        "salesforce_id": rec.get("sfid", ""),
        "door_name": rec.get("name", ""),
        "door_address": rec.get("address", ""),
        "door_city": rec.get("city", ""),
        "door_state": rec.get("state", ""),
        "door_zip": str(rec.get("zip", "")).strip(),
        "advocate_name": rec.get("sales_rep_name", ""),
        "rep_name": rec.get("sales_rep_name", ""),
        "store": rec.get("address", ""),               # calculator join key = leading number of address
        "gross_adds": _num(rec.get("activations")),
        "ga_prepaid": ga_prepaid,
        "ga_postpaid": _num(rec.get("postpaid_activations")),
        "upgrades": _num(rec.get("upgrades")),
        "byod_pct": _num(rec.get("byod_rate")),
        "atu": _num(rec.get("atu_activations")),
        "atu_pct": _num(rec.get("atu_loading_rate")),
        "protect_pct": _num(rec.get("insurance_take_rate")),
        "device_insurance_total": _num(rec.get("insurance_total")),
        "device_insurance_ga": _num(rec.get("insurance_activations")),
        "device_insurance_upgrades": _num(rec.get("insurance_upgrades")),
        "device_insurance_pct": _num(rec.get("insurance_take_rate")),
        "platinum_pts": _num(rec.get("platinum_points")),
        "avg_platinum_pts": _num(rec.get("platinum_points_per_activation")),
        "platinum_pts_5plus": _num(rec.get("platinum_sales")),
        "boost_ready_bounty": bounty,
        "tablet_ga": _num(rec.get("tablet_activations")),
        "boost_app_pct": (bounty / ga_prepaid * 100) if ga_prepaid > 0 else 0,
    }


def normalize_store(rec):
    """DLAR (store) record → raw_dlar_store row fields (matches the manual-upload mapping)."""
    return {
        "salesforce_id": str(rec.get("id", "")),
        "store_code": "",                              # boost has no metricspro code; joined via address
        "address": rec.get("address", ""),
        "location": rec.get("name", ""),
        "gross_adds": _num(rec.get("gross_activation_quantity")),
        "pay_now_acts": _num(rec.get("prepaid_activations")),
        "pay_later_acts": _num(rec.get("postpaid_activations")),
        "total_upgrades": _num(rec.get("upgrades")),
        # raw_dlar_store.total_acts is INT (migration 002); _num() returns float, and
        # Postgres rejects "0.0" for an integer column (22P02). Coerce to a whole number.
        "total_acts": int(round(_num(rec.get("gross_activation_quantity")) + _num(rec.get("upgrades")))),
        "psa_projected": _num(rec.get("projected_percent_to_target") or rec.get("projected_pct_to_sales_quota")),
        "family_plan_pct": _num(rec.get("family_plan_percent")),
        "tmr3": _num(rec.get("three_mr")),
        "aal_conversion": _num(rec.get("aal_conversion")),
        "protect_pct": _num(rec.get("protect_total_attach")),
        "atu": _num(rec.get("atu_loading_percent")),
        "byod_pct": _num(rec.get("byod_adds_percent") or rec.get("byod_total_percent")),
        "port_pct": _num(rec.get("port_ins")),
        "conversion_rate": _num(rec.get("conversion_rate")),
        "acc_attach_rate": _num(rec.get("accessory_attach_rate")),
        "avg_first_mrc": _num(rec.get("avg_first_mrc")),
        "sales_target": _num(rec.get("sales_target")),
        "zero_selling_days": _num(rec.get("zero_selling_days")),
        "shopper_trak_conversion": _num(rec.get("shopper_trak_conversion")),
    }


def _period_from_import(import_date):
    """'06/13/2026' → ('June 2026', 6, 2026). Falls back to the current UTC month."""
    s = str(import_date or "").strip()
    try:
        m, _d, y = s.split("/")
        m, y = int(m), int(y)
        return f"{_calendar.month_name[m]} {y}", m, y
    except Exception:
        now = datetime.now(timezone.utc)
        return f"{_calendar.month_name[now.month]} {now.year}", now.month, now.year


def run_dlar_sweep(client, org_id, user, pw):
    """Login, pull both reports, and replace the period's raw_dlar_rep / raw_dlar_store rows.

    A full snapshot (not incremental): the DLAR is month-to-date cumulative, so we wipe the
    period and re-insert. Returns a summary dict; raises DlarLoginError on auth failure."""
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    login(session, user, pw)

    store_recs, import_date = fetch_report(session, "dlar")
    rep_recs, _ = fetch_report(session, "advocate")
    period, pm, py = _period_from_import(import_date)
    base = {"org_id": org_id, "period": period, "period_month": pm, "period_year": py}

    store_rows = [{**base, **normalize_store(r)} for r in store_recs]
    rep_rows = [{**base, **normalize_rep(r)} for r in rep_recs]

    # GUARD: a DLAR pull that returns nothing is almost never a real empty month — it's an expired
    # session or a portal layout change. Aborting BEFORE the wipe (instead of "OK — 0 stores") keeps
    # an empty/auth-degraded pull from zeroing the live commission period that gets auto-recalc'd.
    if not store_rows and not rep_rows:
        raise DlarPortalError(
            "DLAR returned 0 store and 0 rep rows — aborting before wiping the period (likely an "
            "expired session or portal change, not a real empty month). Period left untouched.")

    # Wipe-and-insert the period (replaces the manual monthly upload), per table, but never let a
    # drastically-smaller pull REPLACE a populated table (partial-collapse guard).
    skipped = []
    for tbl, rows in (("raw_dlar_store", store_rows), ("raw_dlar_rep", rep_rows)):
        existing = _period_count(client, tbl, org_id, period)
        if existing >= REPLACE_MIN_ROWS and len(rows) < existing * REPLACE_MIN_RETAIN:
            skipped.append(f"{tbl} ({existing}->{len(rows)})")
            continue
        client.schema("commcalc").table(tbl).delete() \
            .eq("org_id", org_id).eq("period", period).execute()
        for i in range(0, len(rows), 500):
            client.schema("commcalc").table(tbl).insert(rows[i:i + 500]).execute()

    return {"period": period, "import_date": import_date,
            "stores": len(store_rows), "reps": len(rep_rows),
            "skipped_guard": skipped or None}
