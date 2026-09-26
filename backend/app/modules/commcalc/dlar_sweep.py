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


def _bounded_session(_timeout=60):
    """requests.Session with a DEFAULT per-request timeout (2026-08-04 outage class fix).
    requests has NO default timeout — a blackholed portal (e.g. an IP-blocking host that
    drops packets after SYN-ACK) hangs the calling THREAD forever. These sweeps run hourly
    in the threadpool; each eternal thread is gone for good, and once enough accumulate the
    pool exhausts and EVERY sync endpoint starves (app-wide zero-byte hangs). All Session
    verbs funnel through Session.request, so wrapping it covers get/post/head/etc. An
    explicit timeout= at a call site still wins."""
    s = requests.Session()
    _orig = s.request
    def _req(method, url, **kw):
        kw.setdefault("timeout", _timeout)
        return _orig(method, url, **kw)
    s.request = _req
    return s
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
    """Coerce a DLAR cell ('71.43', '-100.00', '0', '', '0% / 71.43%', '55%') to float.

    Returns 0.0 for an ABSENT cell. Kept verbatim for the COUNT columns, where the portal omitting a
    count and reporting a count of zero are the same fact and every reader treats them alike. For a
    RATE column they are NOT the same fact — use `_rate`."""
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


def _rate(v):
    """A PERCENTAGE cell → float, or **None when the portal reported no rate at all**.

    NO DENOMINATOR IS NOT A SCORE OF ZERO (owner defect 2026-09-26, index §19.28). A KPI rate is a
    measurement; an absent measurement stored as `0.0` is indistinguishable from a rep who genuinely
    attached nothing, and it fails that rep against a target off a number nobody observed.
    `kpi_failing.score` reports a `None` as `no_data` and leaves it OUT of the met-count denominator.
    Same parsing as `_num` otherwise — a cell that SAYS '0' still means zero."""
    if v is None:
        return None
    s = str(v).strip().replace("%", "").replace(",", "")
    if not s or s.lower() in ("n/a", "na", "-", "--", "none"):
        return None
    if "/" in s:
        s = s.split("/")[-1].strip().replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def derived_rate(numerator, denominator):
    """A rate this platform COMPUTES itself → float, or **None when there is no basis to compute it**.

    THE DEFECT THIS CLOSES, verbatim from the code it replaces:
        "boost_app_pct": (bounty / ga_prepaid * 100) if ga_prepaid > 0 else 0
    `ga_prepaid = 0` is the portal not reporting a prepaid-activation count, not a rep with no
    activations: measured 2026-09-26, **189 of 516 `raw_dlar_rep` rows** carried `ga_prepaid = 0` and
    every one of them was written a measured `0`; **122 of those 189 had `boost_ready_bounty > 0`** —
    they had sold the very thing being scored. For July, August and September 2026 EVERY house rep row
    is in that set, so one of seven KPIs was a guaranteed fail for every rep in three months.
    PURE. Returns None for a zero, negative, absent or non-numeric denominator."""
    n, d = _rate(numerator), _rate(denominator)
    if n is None or d is None or d <= 0:
        return None
    return n / d * 100.0


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
        # RATE columns go through `_rate` — an absent rate stays absent (None), never a measured 0.
        "byod_pct": _rate(rec.get("byod_rate")),
        "atu": _num(rec.get("atu_activations")),
        "atu_pct": _rate(rec.get("atu_loading_rate")),
        "protect_pct": _rate(rec.get("insurance_take_rate")),
        "device_insurance_total": _num(rec.get("insurance_total")),
        "device_insurance_ga": _num(rec.get("insurance_activations")),
        "device_insurance_upgrades": _num(rec.get("insurance_upgrades")),
        "device_insurance_pct": _rate(rec.get("insurance_take_rate")),
        "platinum_pts": _num(rec.get("platinum_points")),
        "avg_platinum_pts": _num(rec.get("platinum_points_per_activation")),
        "platinum_pts_5plus": _num(rec.get("platinum_sales")),
        "boost_ready_bounty": bounty,
        "tablet_ga": _num(rec.get("tablet_activations")),
        # THE ONE derived rate on this feed. `None` when the portal reported no prepaid-activation
        # denominator — a KPI with no basis, not a KPI failed at 0% (see `derived_rate`).
        "boost_app_pct": derived_rate(bounty, ga_prepaid),
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
        # RATE columns — `_rate`, for the same reason as the rep report above. These seven are the
        # store-grain KPI actuals `kpi_failing.STORE_KPI_COLUMNS` names, and three of them
        # (familyplan / tmr3 / aal) are the values the Boost pay engine rolls DOWN to every rep.
        "family_plan_pct": _rate(rec.get("family_plan_percent")),
        "tmr3": _rate(rec.get("three_mr")),
        "aal_conversion": _rate(rec.get("aal_conversion")),
        "protect_pct": _rate(rec.get("protect_total_attach")),
        "atu": _rate(rec.get("atu_loading_percent")),
        "byod_pct": _rate(rec.get("byod_adds_percent") or rec.get("byod_total_percent")),
        "port_pct": _rate(rec.get("port_ins")),
        "conversion_rate": _rate(rec.get("conversion_rate")),
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


def as_of_date(import_date):
    """'08/24/2026' → '2026-08-24' (an ISO date string), or None when the portal sent no date. PURE."""
    s = str(import_date or "").strip()
    try:
        m, d, y = s.split("/")
        return "%04d-%02d-%02d" % (int(y), int(m), int(d))
    except Exception:
        return None


def vintage(period, as_of):
    """AS OF WHEN WAS THIS TRUE — the fact this feed used to throw away (owner defect 2026-09-26).

    → `{"as_of", "period_last_day", "complete", "days_short"}`; `complete` is None when the as-of date
    is unknown or unparseable (absent, never assumed). PURE, stdlib only.

    WHY IT EXISTS. Both DLAR reports are MONTH-TO-DATE for the portal's current period, and the sweep
    only ever files the period it is currently serving — so once a month rolls over, that month's slice
    is frozen at whatever day the last pull of it happened to be, forever. Measured live, house org:
    `raw_dlar_store` August 2026 was written 2026-09-02 (final) while `raw_dlar_rep` August 2026 was
    written **2026-08-24** — seven days short — and the pay engine scored one rep on the stale rep
    grain (ATU / Protect / BYOD / Boost App) and the fresh store grain (Family Plan / 3MR / AAL) in the
    SAME row, with nothing anywhere saying either number had an as-of date at all. Neither table had a
    column for it; the only copy was a sentence in `dlar_sweep_config.last_detail`, overwritten hourly."""
    # accept either spelling: the portal's MM/DD/YYYY, or an ISO date already stored on a row
    raw = str(as_of or "").strip()
    iso = raw if (len(raw) == 10 and raw[4] == "-" and raw[7] == "-") else as_of_date(raw)
    out = {"as_of": iso, "period_last_day": None, "complete": None, "days_short": None}
    try:
        name, _y = str(period or "").strip().split()
        month = list(_calendar.month_name).index(name.capitalize())
        year = int(_y)
        last = _calendar.monthrange(year, month)[1]
        out["period_last_day"] = "%04d-%02d-%02d" % (year, month, last)
    except Exception:
        return out
    if not iso:
        return out
    try:
        y2, m2, d2 = (int(x) for x in iso.split("-"))
    except Exception:
        return out
    if (y2, m2) != (year, month):
        # a slice filed under a period the report's own date does not describe — never "complete"
        out["complete"] = False
        out["days_short"] = None
        return out
    last = int(out["period_last_day"][8:])
    out["complete"] = d2 >= last
    out["days_short"] = max(0, last - d2)
    return out


def vintage_of_row(period, row):
    """AS OF WHEN was THIS stored row true → the `vintage()` dict plus `basis` and `written_at`.

    Prefers the row's own `as_of_date` (the report's own date, stamped by this sweep from mig 1026 on).
    For a row written BEFORE that column existed the report's date was never recorded, and this refuses
    to invent one: it reports `basis='write_date'` and treats `created_at` as an UPPER BOUND — the
    numbers cannot be newer than the day they were written, but they may well be older. Saying
    "as of ≤ 2026-08-24, and we did not record the report's own date" is the honest answer; back-filling
    `as_of_date = created_at` would have manufactured exactly the kind of fact this whole change removes.
    PURE."""
    r = row or {}
    stamp = r.get("as_of_date")
    if stamp:
        out = vintage(period, stamp)
        out["basis"] = "as_of_date"
        out["written_at"] = str(r.get("created_at") or "")[:10] or None
        out["upper_bound"] = False
        return out
    written = str(r.get("created_at") or "")[:10] or None
    out = vintage(period, written)
    out["basis"] = "write_date" if written else "unknown"
    out["written_at"] = written
    out["upper_bound"] = bool(written)
    # A write AFTER the period's last day means the month-to-date report was pulled once the month had
    # closed, so the slice does cover the whole month — INFERRED from the write date, not recorded, which
    # is what `upper_bound` says. `vintage()` alone can only call that "not within the period".
    if written and out.get("period_last_day") and written > out["period_last_day"]:
        out["complete"] = True
        out["days_short"] = 0
    return out


def slice_vintage(period, rep_rows=None, store_rows=None):
    """The per-GRAIN vintage of one period's stored DLAR slices, and whether the two DISAGREE.

    `disagree` is the fact nobody could see: the pay engine reads four KPIs from the rep grain and three
    from the store grain into ONE paid row, so two slices of different vintages are two different months
    scored as one. Measured live for August 2026, house org: store grain written 2026-09-02 (final),
    rep grain written 2026-08-24 — seven days short. PURE."""
    out = {"period": period, "grains": {}, "disagree": False, "incomplete": []}
    for key, rows in (("raw_dlar_rep", rep_rows), ("raw_dlar_store", store_rows)):
        rows = list(rows or [])
        if not rows:
            out["grains"][key] = {"rows": 0, "as_of": None, "complete": None, "basis": "no_rows"}
            continue
        v = vintage_of_row(period, rows[0])
        for r in rows[1:]:                        # the OLDEST as-of in the slice governs it
            w = vintage_of_row(period, r)
            if (w.get("as_of") or "9999") < (v.get("as_of") or "9999"):
                v = w
        v["rows"] = len(rows)
        out["grains"][key] = v
        if v.get("complete") is False:
            out["incomplete"].append(key)
    a = (out["grains"].get("raw_dlar_rep") or {}).get("as_of")
    b = (out["grains"].get("raw_dlar_store") or {}).get("as_of")
    out["disagree"] = bool(a and b and a != b)
    return out


GRAIN_LABEL = {"raw_dlar_store": "stores", "raw_dlar_rep": "reps"}


def status_sentence(res):
    """The sweep's own status line — WHAT WAS WRITTEN, per grain, as of when, and every refusal. PURE.

    Never says "OK" about a table nothing was written to: a run whose rep table the partial-collapse
    guard refused reads `⚠ raw_dlar_rep NOT REPLACED …`, because a green status over a frozen slice is
    how August 2026's rep KPIs went on being paid from a 2026-08-24 snapshot (index §19.28)."""
    res = res or {}
    period = res.get("period") or "?"
    written, pulled = res.get("written") or {}, res.get("pulled") or {}
    vint, as_of = res.get("vintage") or {}, res.get("as_of") or {}
    skipped = res.get("skipped_guard") or []
    parts = []
    for tbl in ("raw_dlar_store", "raw_dlar_rep"):
        lbl = GRAIN_LABEL.get(tbl, tbl)
        w, p = written.get(tbl), pulled.get(tbl)
        stamp = as_of.get(tbl)
        seg = f"{w if w is not None else '?'} {lbl} written"
        if p is not None and w is not None and p != w:
            seg += f" of {p} pulled"
        if stamp:
            seg += f" as of {stamp}"
        v = vint.get(tbl) or {}
        if v.get("complete") is False and v.get("days_short"):
            seg += f" — {v['days_short']} day(s) SHORT of {period}"
        elif v.get("complete") is False:
            seg += f" — NOT within {period}"
        parts.append(seg)
    head = "OK" if not skipped else "⚠ PARTIAL"
    line = f"{head} — {period}: " + " · ".join(parts)
    if skipped:
        line += " · NOT REPLACED (partial-collapse guard, existing->pulled): " + ", ".join(skipped)
    return line


def _as_of_column(client, table, org_id):
    """Is `as_of_date` present on this table yet? ONE reading rule for a hand-applied column —
    `core.column_tolerant.present_columns` (index §4b.1), org-scoped, never a block ladder."""
    try:
        from app.core import column_tolerant as _ct
        present = _ct.present_columns(lambda: client.schema("commcalc").table(table),
                                      lambda q: q.eq("org_id", org_id), ("as_of_date",))
        return "as_of_date" in present
    except Exception:
        return False


def run_dlar_sweep(client, org_id, user, pw):
    """Login, pull both reports, and replace the period's raw_dlar_rep / raw_dlar_store rows.

    A full snapshot (not incremental): the DLAR is month-to-date cumulative, so we wipe the
    period and re-insert. Returns a summary dict; raises DlarLoginError on auth failure."""
    session = _bounded_session()
    session.headers.update({"User-Agent": UA})
    login(session, user, pw)

    store_recs, import_date = fetch_report(session, "dlar")
    # EACH GRAIN'S OWN AS-OF DATE. The advocate report's `import_date` used to be thrown away (`rep_recs,
    # _ = …`) and the rep rows were stamped with the STORE report's period — so a rep slice could be
    # filed under a month its own report did not describe, and no column anywhere said when either
    # number was true. See `vintage()` for the live consequence.
    rep_recs, rep_import_date = fetch_report(session, "advocate")
    period, pm, py = _period_from_import(import_date)
    base = {"org_id": org_id, "period": period, "period_month": pm, "period_year": py}
    as_of = {"raw_dlar_store": as_of_date(import_date),
             "raw_dlar_rep": as_of_date(rep_import_date) or as_of_date(import_date)}

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
    skipped, written = [], {}
    for tbl, rows in (("raw_dlar_store", store_rows), ("raw_dlar_rep", rep_rows)):
        existing = _period_count(client, tbl, org_id, period)
        if existing >= REPLACE_MIN_ROWS and len(rows) < existing * REPLACE_MIN_RETAIN:
            skipped.append(f"{tbl} ({existing}->{len(rows)})")
            written[tbl] = 0
            continue
        # `as_of_date` arrives by a hand-applied migration — probe it, never ladder (core.column_tolerant
        # is THE reading rule for that, index §4b.1). A row keeps its stamp when the column is there and
        # lands exactly as before when it is not.
        rows_out = rows
        if as_of.get(tbl) and _as_of_column(client, tbl, org_id):
            rows_out = [{**r, "as_of_date": as_of[tbl]} for r in rows]
        client.schema("commcalc").table(tbl).delete() \
            .eq("org_id", org_id).eq("period", period).execute()
        for i in range(0, len(rows_out), 500):
            client.schema("commcalc").table(tbl).insert(rows_out[i:i + 500]).execute()
        written[tbl] = len(rows_out)

    # WHAT WAS WRITTEN, NOT WHAT WAS PULLED (the §19.21 class, on the one path it survived on). The old
    # summary reported `len(store_rows)` / `len(rep_rows)` — the PULL — so a run whose rep table was
    # refused by the guard still read "OK — 28 stores, 45 reps" while writing zero rep rows. That is how
    # August 2026's rep slice stayed frozen at 2026-08-24 with a green status above it.
    return {"period": period, "import_date": import_date, "rep_import_date": rep_import_date,
            "as_of": as_of,
            "vintage": {t: vintage(period, as_of.get(t)) for t in ("raw_dlar_store", "raw_dlar_rep")},
            "pulled": {"raw_dlar_store": len(store_rows), "raw_dlar_rep": len(rep_rows)},
            "written": written,
            # kept for compatibility with the status line: these are the PULL counts it has always shown
            "stores": len(store_rows), "reps": len(rep_rows),
            "skipped_guard": skipped or None}
