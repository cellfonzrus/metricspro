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

# THE one home of "which columns must carry a value" (index §19.18 arrival/content, §19.28 completeness).
from app.modules.commcalc import data_lineage_registry as _lineage
# THE one home of "what does a source cell mean, and did the source send it at all" (index §COMMCALC-16).
# Both readings below delegate: a count and a rate obey ONE rule about absence, in one place, so the
# next shape change cannot land on a reading that was never taught it (owner report 2026-10-16).
from app.modules.commcalc import portal_cell as _cell
from app.modules.commcalc import empty_pull_verdict as _empty_verdict
from app.modules.commcalc import auto_calc as _auto_calc   # the ONE post-landing hook (index §6l)
# THE one home of "we submitted a login, what happened" (index §COMMCALC-50.9). This driver's
# DETECTION was already sound — it asks for a page that needs a session — but its REPORTING named
# every failure a rejected credential, so a portal refusing us at the edge and a changed route both
# sent the operator to re-type a password. The naming is dereferenced, not re-spelled.
from app.modules.commcalc import login_outcome as _lo

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
    """A COUNT or amount cell → float, or **None when the portal sent no cell at all**.

    DEREFERENCES `portal_cell.count` — this used to return `0.0` for an absent cell, and that is the
    defect of 2026-10-16: the store report stopped sending its unit counts and 28 stores landed
    October 2026 with 0 activations, 0 upgrades and 0 total activity beside populated attach rates,
    under a green status line. An unsent count is not a sold-nothing month. The parse is unchanged for
    a cell the portal DID send, including a literal '0', which still reads as a measured zero."""
    return _cell.count(v)


def _rate(v):
    """A PERCENTAGE cell → float, or **None when the portal reported no rate at all**.

    NO DENOMINATOR IS NOT A SCORE OF ZERO (owner defect 2026-09-26, index §19.28). A KPI rate is a
    measurement; an absent measurement stored as `0.0` is indistinguishable from a rep who genuinely
    attached nothing, and it fails that rep against a target off a number nobody observed.
    `kpi_failing.score` reports a `None` as `no_data` and leaves it OUT of the met-count denominator.
    DEREFERENCES `portal_cell.rate` — the rule it states is now true of counts too, in one home."""
    return _cell.rate(v)


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


# ── WHICH REPORTS THIS PORTAL SERVES — ONE DECLARATION, and the SET is per-org CONFIG ─────────────
# OWNER, verbatim: *"also need to add this https://boostelevatego.com/reports/boost-ready/by-advocate to
# poplate the kpi for boost app, which gives the data per rep … nothing is hard coded, option is platform
# wide"*. `run_dlar_sweep` had the two report names written into its body, so adding a third was a code
# change in the middle of the path that WIPES AND REWRITES a live commission period. The names, the table
# each lands in, and the normalizer that shapes it are declared here ONCE, and which of them a tenant
# pulls is `dlar_sweep_config.reports` (mig `1029`; NULL = the default pair = byte-identical).
#
# `boost_ready_by_advocate` IS DECLARED AND IS NOT ENABLED, deliberately: a report whose JSON nobody here
# has ever seen cannot be given a normalizer without guessing which key carries the rate, and a guessed
# mapping on a KPI that tiers pay is the §19.26 defect ("missing beats wrong on a money gate"). Its
# `normalize` is None, `resolve_report_set` refuses to enable a report that has none, and the refusal says
# what is needed: ONE sample pull. See index §19.31 for what it would supersede if it carries the rate.
PORTAL_REPORTS = {
    "dlar":     {"path": "dlar",     "table": "raw_dlar_store", "normalize": "normalize_store",
                 "label": "store (DLAR)", "default": True},
    "advocate": {"path": "advocate", "table": "raw_dlar_rep",   "normalize": "normalize_rep",
                 "label": "rep (Advocate)", "default": True},
    "boost_ready_by_advocate": {
        "path": "boost-ready/by-advocate", "table": "raw_dlar_rep", "normalize": None,
        "label": "Ready App per rep", "default": False,
        "needs": ("no normalizer: one sample of this report's /inline JSON is needed to map which key "
                  "carries the per-rep rate before it may write to a table that tiers pay")},
}
DEFAULT_REPORT_SET = tuple(k for k, v in PORTAL_REPORTS.items() if v["default"])


def resolve_report_set(raw=None):
    """The org's report set (`dlar_sweep_config.reports`, a JSON/text list) → (keys, refused). PURE.

    Missing / junk / empty → `DEFAULT_REPORT_SET`, so every tenant to date pulls exactly the two reports
    it always did. A declared report with no normalizer is REFUSED BY NAME with the reason, never silently
    dropped and never pulled — an unmapped report that writes nothing is a config the owner would
    otherwise believe was working. An unknown key is refused the same way."""
    keys, refused = [], []
    items = raw if isinstance(raw, (list, tuple)) else None
    if isinstance(raw, str):
        items = [x.strip() for x in raw.replace(",", " ").split() if x.strip()]
    for k in items or ():
        k = str(k or "").strip()
        if k in keys:
            continue
        spec = PORTAL_REPORTS.get(k)
        if spec is None:
            refused.append((k, "not a report this portal declares"))
        elif not spec.get("normalize"):
            refused.append((k, spec.get("needs") or "no normalizer"))
        else:
            keys.append(k)
    return (tuple(keys) if keys else DEFAULT_REPORT_SET), refused


def _login_outcome_of(session, refused):
    """(outcome, marker) for a protected page that did not come back. Never raises.

    `refused` is the response to the protected page. A redirect carries no body, so where one was
    served we follow it ONCE — read-only, GET, no form posted — to see what the portal is actually
    showing. The probe above is untouched: this runs only after it has already failed, so nothing
    about the signed-in path changes shape.
    """
    body = ""
    try:
        body = refused.text or ""
    except Exception:
        body = ""
    if not body.strip():
        try:
            loc = (refused.headers or {}).get("Location") or f"{BASE}/login"
            if loc.startswith("/"):
                loc = BASE + loc
            body = (session.get(loc, timeout=30).text or "")
        except Exception:
            body = ""
    vis = _lo.visible_from_html(body)
    pv = None
    try:
        from app.modules.commcalc import portal_backoff as _pb
        pv = _pb.page_verdict(vis)
    except Exception:
        pv = None
    v = _lo.verdict(form_present=_lo.form_in_html(body), visible_text=vis, page_verdict=pv)
    return v["outcome"], v.get("marker")


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
        # THE PROBE IS SOUND, THE NAME WAS NOT. A page that needs a session did not come back, which
        # proves we are not signed in and proves nothing about WHY. Ask the one home, giving it the
        # same two facts the browser drivers give it — is a sign-in form in front of us, and what
        # does the page SAY — recovered from the document because this driver has no browser.
        raise DlarLoginError(_lo.message(*_login_outcome_of(session, chk)))


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


# ── WHICH SOURCE FIELD FEEDS WHICH COLUMN — ONE DECLARATION PER REPORT ───────────────────────────
# Owner report 2026-10-16: the store report's unit counts stopped arriving and landed as measured
# zeros. The reason nothing could SAY so is that the only record of "this column comes from that
# portal field" was the `rec.get("…")` literal inside the normalizer. A fact that exists only as an
# argument cannot be checked, so a RENAME upstream reads as data: the old key returns nothing, the
# coercion returned 0.0, and 28 stores filed a month of zero activations under a green status.
#
# So the mapping is DECLARED here, the normalizers DEREFERENCE it, and `field_arrival` compares it
# against the records the portal actually sent — naming the fields that stopped arriving and, beside
# them, the fields the portal IS sending that nothing reads. That second list is the renamed field's
# new name, printed by the feed itself on the first run after a rename.
#
# `kind` decides the reading, and the readings differ only in what the caller MEANS (`portal_cell`):
#   · TEXT   — identity; an absent field is '' (what every consumer already sees for a blank door).
#   · COUNT  — units. Absent → None, NEVER 0 (the 2026-10-16 defect).
#   · RATE   — a percentage. Absent → None (the 2026-09-26 defect, unchanged).
# Several spellings may be declared for one column, most-preferred first; the first that CARRIES a
# value wins, so a measured '0' under the first spelling is kept rather than falling through to the
# second. RULE TWO: these are the SOURCE's own field names, not behaviour branched on a carrier.
TEXT, COUNT, RATE = "text", "count", "rate"

STORE_FIELDS = (
    # (our column,            kind,  source field spelling(s) …)
    ("salesforce_id",         TEXT,  "id"),
    ("address",               TEXT,  "address"),
    ("location",              TEXT,  "name"),
    ("gross_adds",            COUNT, "gross_activation_quantity"),
    ("pay_now_acts",          COUNT, "prepaid_activations"),
    ("pay_later_acts",        COUNT, "postpaid_activations"),
    ("total_upgrades",        COUNT, "upgrades"),
    ("psa_projected",         RATE,  "projected_percent_to_target", "projected_pct_to_sales_quota"),
    # the three KPIs published ONLY at store grain, which the pay engine rolls down to every rep
    ("family_plan_pct",       RATE,  "family_plan_percent"),
    ("tmr3",                  RATE,  "three_mr"),
    ("aal_conversion",        RATE,  "aal_conversion"),
    ("protect_pct",           RATE,  "protect_total_attach"),
    ("atu",                   RATE,  "atu_loading_percent"),
    ("byod_pct",              RATE,  "byod_adds_percent", "byod_total_percent"),
    ("port_pct",              RATE,  "port_ins"),
    ("conversion_rate",       RATE,  "conversion_rate"),
    ("acc_attach_rate",       RATE,  "accessory_attach_rate"),
    ("avg_first_mrc",         COUNT, "avg_first_mrc"),
    ("sales_target",          COUNT, "sales_target"),
    ("zero_selling_days",     COUNT, "zero_selling_days"),
    ("shopper_trak_conversion", RATE, "shopper_trak_conversion"),
)

REP_FIELDS = (
    ("salesforce_id",         TEXT,  "sfid"),
    ("door_name",             TEXT,  "name"),
    ("door_address",          TEXT,  "address"),
    # the calculator joins a rep to a store on the LEADING NUMBER of this address
    ("store",                 TEXT,  "address"),
    ("door_city",             TEXT,  "city"),
    ("door_state",            TEXT,  "state"),
    ("door_zip",              TEXT,  "zip"),
    ("advocate_name",         TEXT,  "sales_rep_name"),
    ("rep_name",              TEXT,  "sales_rep_name"),
    ("gross_adds",            COUNT, "activations"),
    ("ga_prepaid",            COUNT, "prepaid_activations"),
    ("ga_postpaid",           COUNT, "postpaid_activations"),
    ("upgrades",              COUNT, "upgrades"),
    ("byod_pct",              RATE,  "byod_rate"),
    ("atu",                   COUNT, "atu_activations"),
    ("atu_pct",               RATE,  "atu_loading_rate"),
    ("protect_pct",           RATE,  "insurance_take_rate"),
    ("device_insurance_pct",  RATE,  "insurance_take_rate"),
    ("device_insurance_total", COUNT, "insurance_total"),
    ("device_insurance_ga",   COUNT, "insurance_activations"),
    ("device_insurance_upgrades", COUNT, "insurance_upgrades"),
    ("platinum_pts",          COUNT, "platinum_points"),
    ("avg_platinum_pts",      COUNT, "platinum_points_per_activation"),
    ("platinum_pts_5plus",    COUNT, "platinum_sales"),
    ("boost_ready_bounty",    COUNT, "boost_ready_bounty"),
    ("tablet_ga",             COUNT, "tablet_activations"),
)

# A derived column is declared with the SOURCE fields it is derived FROM, so the arrival check covers
# it too: `total_acts` is the sum of two source fields and `boost_app_pct` is a quotient of two, and
# both used to go silently absent with the fields under them.
STORE_DERIVED_FROM = ("gross_activation_quantity", "upgrades")
REP_DERIVED_FROM = ("boost_ready_bounty", "prepaid_activations")

FIELD_MAPS = {"dlar": STORE_FIELDS, "advocate": REP_FIELDS}


def declared_source_fields(report_key):
    """Every source FIELD the named report's mapping reads, one entry per field — the ONE list
    `field_arrival` checks. A field with alternative spellings stays ONE entry (a tuple), because a
    reader that accepts two spellings is not missing the one it did not get. PURE."""
    out = []
    for row in FIELD_MAPS.get(report_key) or ():
        group = tuple(row[2:])
        if group and group not in out:
            out.append(group)
    extra = {"dlar": STORE_DERIVED_FROM, "advocate": REP_DERIVED_FROM}.get(report_key) or ()
    for key in extra:
        if (key,) not in out:
            out.append((key,))
    return tuple(out)


def _pick(rec, keys):
    """The first declared spelling of a field that CARRIES a value → its raw cell; else the raw cell of
    the first spelling the record has at all; else None (the source sent no such field). PURE."""
    rec = rec or {}
    present = None
    for k in keys:
        if k in rec:
            if present is None:
                present = rec.get(k)
            if _cell.carries(rec.get(k)):
                return rec.get(k)
    return present


def shape(rec, fields):
    """One source record → our column values, by the declared mapping. PURE, no portal knowledge."""
    out = {}
    for row in fields:
        col, kind, keys = row[0], row[1], row[2:]
        raw = _pick(rec, keys)
        if kind == TEXT:
            out[col] = "" if raw is None else str(raw).strip()
        elif kind == RATE:
            out[col] = _rate(raw)
        else:
            out[col] = _num(raw)
    return out


def normalize_rep(rec):
    """Advocate (rep) record → raw_dlar_rep row fields, by the REP_FIELDS declaration."""
    row = shape(rec, REP_FIELDS)
    # THE ONE derived rate on this feed. `None` when the portal reported no prepaid-activation
    # denominator — a KPI with no basis, not a KPI failed at 0% (see `derived_rate`).
    row["boost_app_pct"] = derived_rate(_pick(rec, ("boost_ready_bounty",)),
                                        _pick(rec, ("prepaid_activations",)))
    return row


def normalize_store(rec):
    """DLAR (store) record → raw_dlar_store row fields, by the STORE_FIELDS declaration."""
    row = shape(rec, STORE_FIELDS)
    row["store_code"] = ""          # the carrier publishes no platform store code; joined via address
    # raw_dlar_store.total_acts is an INT column (migration 002), and a total of fields the portal did
    # not send is ABSENT, not zero: `portal_cell.total` returns None when neither part arrived, which
    # is the difference between "this store sold nothing" and "nobody told us what it sold".
    row["total_acts"] = _cell.as_int(_cell.total(_pick(rec, ("gross_activation_quantity",)),
                                                 _pick(rec, ("upgrades",))))
    return row


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


def status_verdict(res):
    """'ok' or 'partial' — the ONE home for "did this run leave the feed whole". PURE.

    The sentence SAYS "⚠ PARTIAL" and the stored `last_status` said 'ok', because the caller passed
    the literal. So every surface that colours on the status word — the DLAR sweep page, the
    connectors row, the upload-duty feed at `_upload_duties` (which already treats 'partial' as a
    failed auto feed) — showed green over October 2026's missing unit counts for ten days, while the
    detail line beside it said a column had stopped arriving. One fact, two homes, one of them a
    literal: `status_sentence` and the caller now BOTH dereference this.

    'partial' is not a new word: `_sweep_set_status` already counts it as a run that imported
    (`success` defaults to status in ('ok', 'partial')), the ePay sweep already emits it for a mixed
    run, and `_upload_duties` already reads it. Nothing is invented here — the DLAR sweep was the one
    sibling computing the verdict and then throwing it away."""
    res = res or {}
    if res.get("skipped_guard"):
        return "partial"
    for tbl in ("raw_dlar_store", "raw_dlar_rep"):
        if ((res.get("content") or {}).get(tbl) or {}).get("empty_columns"):
            return "partial"
    for rkey in ("dlar", "advocate"):
        f = (res.get("fields") or {}).get(rkey) or {}
        if f.get("missing") or f.get("blank"):
            return "partial"
    return "ok"


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
    # A COLUMN THAT STOPPED ARRIVING IS NOT AN "OK" RUN. The pull succeeded, the rows landed and the
    # period moved — and a declared column carries nothing in any of them, which is how July 2026's
    # advocate-report shape change went unnoticed for three months (index §19.28).
    gone = []
    for tbl in ("raw_dlar_store", "raw_dlar_rep"):
        c = (res.get("content") or {}).get(tbl) or {}
        for col in c.get("empty_columns") or []:
            gone.append(f"{tbl}.{col} (0 of {c.get('rows', 0)} rows)")
    # A PORTAL FIELD THAT STOPPED ARRIVING IS NAMED, AND SO IS WHAT ARRIVED INSTEAD (owner report
    # 2026-10-16). `gone` above watches OUR columns; this watches the SOURCE's own field names, which
    # is the only thing that can tell a rename from a genuine zero. `unconsumed` is reported only
    # beside a field that went missing — see `portal_cell.field_arrival` — and it is the renamed
    # field's new name, so the mapping is corrected from evidence instead of guessed.
    def _cap(items, limit=8):
        """At most `limit` names, then a count — a status line a person reads, not a dump."""
        items = list(items)
        if len(items) <= limit:
            return items
        return items[:limit] + [f"+{len(items) - limit} more"]

    shape, unknown = [], []
    for rkey in ("dlar", "advocate"):
        f = (res.get("fields") or {}).get(rkey) or {}
        lbl = (PORTAL_REPORTS.get(rkey) or {}).get("label") or rkey
        for k in f.get("missing") or ():
            shape.append(f"{lbl}: '{k}' NOT SENT")
        for k in f.get("blank") or ():
            shape.append(f"{lbl}: '{k}' arrived EMPTY")
        for k in f.get("unconsumed") or ():
            unknown.append(f"{lbl}: '{k}'")
    head = "OK" if status_verdict(res) == "ok" else "⚠ PARTIAL"
    line = f"{head} — {period}: " + " · ".join(parts)
    if skipped:
        line += " · NOT REPLACED (partial-collapse guard, existing->pulled): " + ", ".join(skipped)
    if gone:
        line += " · ⚠ COLUMN STOPPED ARRIVING: " + ", ".join(gone)
    if shape:
        line += " · ⚠ PORTAL FIELD CHANGED: " + ", ".join(_cap(shape))
    if unknown:
        line += " · FIELDS THE PORTAL SENDS THAT NOTHING READS (the new spelling is in here): " \
                + ", ".join(_cap(unknown))
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


def run_dlar_sweep(client, org_id, user, pw, reports=None):
    """Login, pull both reports, and replace the period's raw_dlar_rep / raw_dlar_store rows.

    A full snapshot (not incremental): the DLAR is month-to-date cumulative, so we wipe the
    period and re-insert. Returns a summary dict; raises DlarLoginError on auth failure."""
    session = _bounded_session()
    session.headers.update({"User-Agent": UA})
    login(session, user, pw)

    report_keys, report_refused = resolve_report_set(reports)
    store_recs, import_date = fetch_report(session, PORTAL_REPORTS["dlar"]["path"])
    # EACH GRAIN'S OWN AS-OF DATE. The advocate report's `import_date` used to be thrown away (`rep_recs,
    # _ = …`) and the rep rows were stamped with the STORE report's period — so a rep slice could be
    # filed under a month its own report did not describe, and no column anywhere said when either
    # number was true. See `vintage()` for the live consequence.
    rep_recs, rep_import_date = fetch_report(session, PORTAL_REPORTS["advocate"]["path"])
    period, pm, py = _period_from_import(import_date)
    base = {"org_id": org_id, "period": period, "period_month": pm, "period_year": py}
    as_of = {"raw_dlar_store": as_of_date(import_date),
             "raw_dlar_rep": as_of_date(rep_import_date) or as_of_date(import_date)}

    store_rows = [{**base, **normalize_store(r)} for r in store_recs]
    rep_rows = [{**base, **normalize_rep(r)} for r in rep_recs]

    # DID THE PORTAL'S OWN FIELDS ARRIVE (owner report 2026-10-16, index §COMMCALC-16). `content`
    # below asks whether OUR columns carry values, which cannot tell a renamed field from a month in
    # which nobody sold anything — the store report's unit counts stopped arriving in October 2026 and
    # landed as measured zeros under a green status. This asks the question one step earlier, against
    # the records as the portal returned them, and names the fields it is sending that nothing reads.
    fields = {
        "dlar": _cell.field_arrival(store_recs, declared_source_fields("dlar")),
        "advocate": _cell.field_arrival(rep_recs, declared_source_fields("advocate")),
    }

    # DO THE DECLARED COLUMNS STILL CARRY VALUES? The third freshness question (index §19.28): rows can
    # keep arriving and the data date keep moving while a column has silently stopped being sent. The
    # advocate report lost its whole door-identity block and its prepaid/postpaid split in JULY 2026 and
    # nothing failed — 147 rep rows since then divided a bounty by a denominator that was no longer being
    # sent. WHICH columns must carry a value is declared ONCE in `data_lineage_registry`; this reads it.
    content = {}
    for _tbl, _rows in (("raw_dlar_store", store_rows), ("raw_dlar_rep", rep_rows)):
        content[_tbl] = _lineage.content_arrival(_rows, _lineage.required_content_columns(_tbl))

    # GUARD: a DLAR pull that returns nothing is almost never a real empty month — it's an expired
    # session or a portal layout change. Aborting BEFORE the wipe (instead of "OK — 0 stores") keeps
    # an empty/auth-degraded pull from zeroing the live commission period that gets auto-recalc'd.
    if not store_rows and not rep_rows:
        # The DECISION is not re-implemented here: "is this zero the source's own answer?" has ONE
        # home (`empty_pull_verdict`) and this sweep dereferences it, declaring only the fact that is
        # true of THIS feed — a zero is never legitimate for it. The sentence comes back from the
        # shared home so every surface words it the same way (index §19.41).
        _v = _empty_verdict.classify_empty_pull(
            empty_allowed=False, label="the advocate/store report pull (0 store + 0 rep rows)")
        raise DlarPortalError(_v["sentence"] + " Period left untouched.")

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

    # DATA LANDED (index §6l) — the ONE post-landing hook, per grain that was actually WRITTEN (a grain the
    # partial-collapse guard refused changed nothing, so it queues nothing). Replaces the inline
    # `_run_calculation` the router's `_do_dlar_sweep` used to make on every run; the two grains of one pull
    # coalesce into ONE queued calculation of the period.
    auto_calc = None
    for tbl in ("raw_dlar_store", "raw_dlar_rep"):
        if written.get(tbl):
            auto_calc = _auto_calc.landed(client, org_id, table=tbl, periods=[period], source="dlar_sweep",
                                          rows=written[tbl])

    # WHAT WAS WRITTEN, NOT WHAT WAS PULLED (the §19.21 class, on the one path it survived on). The old
    # summary reported `len(store_rows)` / `len(rep_rows)` — the PULL — so a run whose rep table was
    # refused by the guard still read "OK — 28 stores, 45 reps" while writing zero rep rows. That is how
    # August 2026's rep slice stayed frozen at 2026-08-24 with a green status above it.
    return {"period": period, "import_date": import_date, "rep_import_date": rep_import_date,
            "as_of": as_of,
            "vintage": {t: vintage(period, as_of.get(t)) for t in ("raw_dlar_store", "raw_dlar_rep")},
            "pulled": {"raw_dlar_store": len(store_rows), "raw_dlar_rep": len(rep_rows)},
            "written": written,
            "content": content,
            # the SOURCE side of the same question: which of the portal's own fields arrived
            "fields": fields,
            # WHICH REPORTS THIS RUN PULLED, and every report the config named that could not be —
            # by name, with the reason. A refused report is part of the run's record, not a silence.
            "reports": list(report_keys),
            "reports_refused": [{"report": k, "reason": r} for k, r in report_refused] or None,
            # kept for compatibility with the status line: these are the PULL counts it has always shown
            "stores": len(store_rows), "reps": len(rep_rows),
            "skipped_guard": skipped or None,
            "auto_calc": auto_calc or {"queued": False, "reason": "nothing_landed"}}
