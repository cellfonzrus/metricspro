"""DAILY COMMISSION ACCRUAL + ENVELOPE PAYOUT LEDGER — the commission side of the EEP package.

Spec: docs/specs/envelope-expense-payout.md (Feature 2). Migration: 267.

════════════════════════════════════════════════════════════════════════════════════════════════════
WHAT THIS IS, AND — MORE IMPORTANTLY — WHAT IT IS NOT
════════════════════════════════════════════════════════════════════════════════════════════════════
A store owner hands a rep cash out of the day's envelope against commission the rep has "already
earned". Until now there was nothing to measure that cash against between monthly runs: commission is
computed once a month, so for 29 days of every month the honest answer to "what has Ali earned so far?"
was a shrug. This module answers that question, and records the cash.

It is built on ONE rule, and every design decision below follows from it:

    THE ACCRUAL IS A PROBABLE (EXPECTED) NUMBER. IT IS NEVER PAY.

    * Nothing here writes commcalc.rep_commissions, commission plans, payout schedules, tiers, rules,
      or ANY number a human is paid. Not once, not indirectly, not on a background task.
    * No payout engine reads this table. `_run_calculation`, `_apply_new_engines`, calculator.py and
      commission_engine.py do not know it exists, and adding a read from them would be the bug.
    * It is the same doctrine as the M2-M6 "expected" column (owner 2026-08-01): calculated, displayed,
      never summed into pay.
    * A recorded payout is an ADVANCE — a cash movement. It changes `paid` / `unpaid`. It does not
      change `accrued`, it does not reduce what the rep is owed at month end, and it never nets or
      claws anything back (ledger Q14 default: over-advance is FLAGGED for a human, full stop).

════════════════════════════════════════════════════════════════════════════════════════════════════
HOW A DAY IS COMPUTED (and why it is deliberately UN-TIERED)
════════════════════════════════════════════════════════════════════════════════════════════════════
The day's OWN sale lines are read (one day, one table, never a month) and run through the tenant's
REAL pay logic — resolved by `_resolve_carrier_mode`, exactly like the monthly calc:

  * plan mode  -> commission_engine.preview(sales_override=<that day's lines>). That is the SAME
                  function the live plan payout runs through: the same matcher, _line_payout, the
                  flat-once accumulation, plan_pay_gate (scope / exclusion / unit-basis / accessory
                  basis), the accessory classifier and the set-up-fee pay item. A second, drift-prone
                  copy of the pay math would be worse than useless here — a rep would be shown an
                  accrual their monthly pay then contradicts.
  * boost mode -> calculator.calc_rep_commissions over that day's lines, with the MONTHLY inputs
                  (ePay payment detail, MI, DLAR) deliberately empty, and we take `subtotal`.

A single DAY cannot know a MONTHLY tier attainment. Multiplying a day by a guessed tier is wrong in
both directions — it over-advances a rep who ends the month at 50%, and under-states one who ends at
100%. So `base_amount` is the day's un-tiered commission, and the ENTIRE tier effect is recognized
once, later, as `tier_amount` (below). Two Boost components are likewise deferred because they are
not knowable from a day's sale lines: the KPI tier (DLAR, monthly) and the trade-in spiff (ePay
payment detail, monthly). `components.deferred_to_monthly` says so on every row, in words.

`tier_basis='as_computed'` (opt-in, per tenant) accrues the day's own tier multiplier instead. It
exists for tenants whose plans tier on something a day CAN attain; it is not the default.

════════════════════════════════════════════════════════════════════════════════════════════════════
MONTHLY TIER / TRUE-UP RECOGNITION — once, replayably
════════════════════════════════════════════════════════════════════════════════════════════════════
Once a prior month's commission run EXISTS (rep_commissions rows for that period), that month's
difference between what was actually computed and what was accrued daily is recognized into the
accrual stream:

    tier_amount = rep_commissions.total_payout(month P)  -  SUM(base_amount accrued in month P)

so the accrual converges on the real number. It can be NEGATIVE (a month that finished below the
un-tiered accrual, e.g. a 50% KPI tier) — that is a true-up, not a clawback: nothing is deducted from
anybody, the balance simply reads honestly and, if cash was already advanced past it, the
over-advance FLAG lights up for a human.

RECOGNIZED ONCE, AND REPLAYABLE. The recognition lands on exactly one work_date per (employee,
source month). A re-run of that date recomputes the same value and upserts it. A run of any OTHER
date sees the existing recognition and does not add a second one. There is no watermark table to get
out of sync — the accrual rows themselves are the record (components.tier.source_period).

WHEN it may be recognized is tenant config (RULE TWO), never hard-coded:
    mode 'on_run_available' (default) — as soon as the run exists (earliest: the 1st of month P+1)
    mode 'day_of_month'              — on that day of month P+1 (clamped to month end), still gated
                                       on the run existing.

════════════════════════════════════════════════════════════════════════════════════════════════════
MULTI-TENANT (contract §2) + GRACEFUL DEGRADE (contract §5)
════════════════════════════════════════════════════════════════════════════════════════════════════
Every read is `.eq("org_id", org_id)`; every insert stamps org_id; org_id reaches this module only as
an explicit argument from a query param. Before migration 267 runs, every function returns
`ready: False` with a plain-language note and writes nothing — a missing migration can never break an
unrelated page, and the daily sweep no-ops.
"""
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta, timezone as _timezone
import calendar as _calendar
import re

from app.modules.commcalc.calculator import safe_float
from app.modules.commcalc.commission_engine import _canon_person, _pvariants

# ── config (RULE TWO: every knob below is per-tenant, this is only the fallback) ──────────────────
CODE_DEFAULT = {
    "enabled": True,
    # 'none'        — accrue the day UN-TIERED; the whole tier effect arrives as the monthly true-up.
    # 'as_computed' — accrue the day's own tier multiplier (only sane when tiers are day-attainable).
    "tier_basis": "none",
    "tier_recognition": {"mode": "on_run_available", "day_of_month": None, "lookback_months": 3},
    "auto_run": {"enabled": True, "days_back": 1},
}
TIER_BASES = ("none", "as_computed")
RECOGNITION_MODES = ("on_run_available", "day_of_month")
ACCRUAL_TABLE = "daily_commission_accrual"
LEDGER_TABLE = "commission_payout_ledger"
_MISSING_NOTE = ("Migration 267_commission_daily_accrual_payout_ledger.sql has not been run yet — "
                 "daily commission accrual is inactive for every tenant until it is. Nothing else is "
                 "affected.")


def normalize_config(raw):
    """Coerce a stored accrual_config blob into a complete, in-range dict. PURE. Never raises.

    Every clamp is one-directional-safe: a typo can make the feature do LESS, never more (days_back
    can't turn the daily sweep into a month rewrite; lookback_months can't turn tier recognition into
    a full-history replay)."""
    cfg = dict(CODE_DEFAULT)
    cfg["tier_recognition"] = dict(CODE_DEFAULT["tier_recognition"])
    cfg["auto_run"] = dict(CODE_DEFAULT["auto_run"])
    if not isinstance(raw, dict):
        return cfg
    if "enabled" in raw:
        cfg["enabled"] = raw.get("enabled") is not False
    tb = str(raw.get("tier_basis") or "").strip().lower()
    if tb in TIER_BASES:
        cfg["tier_basis"] = tb
    tr = raw.get("tier_recognition")
    if isinstance(tr, dict):
        mode = str(tr.get("mode") or "").strip().lower()
        if mode in RECOGNITION_MODES:
            cfg["tier_recognition"]["mode"] = mode
        dom = tr.get("day_of_month")
        try:
            dom = int(dom) if dom not in (None, "") else None
        except Exception:
            dom = None
        cfg["tier_recognition"]["day_of_month"] = min(31, max(1, dom)) if dom else None
        try:
            lb = int(tr.get("lookback_months"))
        except Exception:
            lb = CODE_DEFAULT["tier_recognition"]["lookback_months"]
        cfg["tier_recognition"]["lookback_months"] = min(12, max(1, lb))
    ar = raw.get("auto_run")
    if isinstance(ar, dict):
        if "enabled" in ar:
            cfg["auto_run"]["enabled"] = ar.get("enabled") is not False
        try:
            db = int(ar.get("days_back"))
        except Exception:
            db = CODE_DEFAULT["auto_run"]["days_back"]
        cfg["auto_run"]["days_back"] = min(7, max(0, db))
    return cfg


def load_config(client, org_id):
    """This tenant's accrual config, degrading to CODE_DEFAULT when the column/table/row is absent."""
    try:
        rows = (client.schema("commcalc").table("commission_org_config").select("accrual_config")
                .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return normalize_config(None)
    return normalize_config((rows[0] if rows else {}).get("accrual_config"))


def save_config(client, org_id, raw):
    """Upsert this tenant's accrual config (admin surface). Returns the normalized result."""
    cfg = normalize_config(raw)
    client.schema("commcalc").table("commission_org_config").upsert(
        {"org_id": org_id, "accrual_config": cfg,
         "updated_at": _datetime.now(_timezone.utc).isoformat()}, on_conflict="org_id").execute()
    return cfg


# ── small pure helpers ───────────────────────────────────────────────────────────────────────────
def canon_key(name):
    """The employee_key. `_canon_person` is the module's existing name-ORDER-insensitive canonical form
    (casefold + whitespace collapse + "Last, First" reorder) — deliberately reused so an accrual keyed
    off the POS `salesperson` string lines up with the plan-assignment / rep_commissions matching that
    already exists, rather than inventing a ninth notion of "who is this person"."""
    return _canon_person(name)


def parse_day(v, default=None):
    """'YYYY-MM-DD' (or a date/datetime) -> date. Returns `default` on anything unparseable. PURE."""
    if isinstance(v, _datetime):
        return v.date()
    if isinstance(v, _date):
        return v
    s = str(v or "").strip()[:10]
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if not m:
        return default
    try:
        return _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return default


def period_label(d):
    """date -> the sweep-canonical period spelling ('August 2026'). Readers use _pvariants anyway."""
    return f"{_calendar.month_name[d.month]} {d.year}"


def month_bounds(d):
    """(first_day, last_day) of d's month. PURE."""
    return _date(d.year, d.month, 1), _date(d.year, d.month, _calendar.monthrange(d.year, d.month)[1])


def add_months(d, n):
    """First day of the month n months from d's month. PURE."""
    y, m = d.year, d.month + n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return _date(y, m, 1)


def recognition_earliest(source_month_first_day, cfg):
    """The EARLIEST date a source month's true-up may be recognized. PURE, deterministic.

    'on_run_available' -> the 1st of the following month (recognize as soon as the run exists).
    'day_of_month'     -> that day of the following month, CLAMPED to that month's real length (so a
                          tenant who picks the 31st does not silently never recognize in February)."""
    nxt = add_months(source_month_first_day, 1)
    tr = (cfg or {}).get("tier_recognition") or {}
    if tr.get("mode") == "day_of_month" and tr.get("day_of_month"):
        dom = min(int(tr["day_of_month"]), _calendar.monthrange(nxt.year, nxt.month)[1])
        return _date(nxt.year, nxt.month, dom)
    return nxt


def _table_missing(exc):
    """True when the exception looks like 'migration 267 has not been run'. Anything else is a real
    error and is re-raised by the caller, so a genuine outage is never silently reported as 'not set
    up yet'."""
    s = str(exc).lower()
    return ("does not exist" in s or "could not find the table" in s or "not find the table" in s
            or "pgrst205" in s or "42p01" in s or "schema cache" in s)


def _round(v):
    return round(safe_float(v) + 0.0, 2)


# ── store resolution ─────────────────────────────────────────────────────────────────────────────
def store_code_map(client, org_id):
    """{lower store_address -> store_code, lower store_code -> store_code} for this org.

    The POS `store` string on a sale line is an ADDRESS-ish label; the envelope/closing side of EEP
    speaks store_code. Resolving here (not at read time) means the accrual row is already joinable by
    the retail-ops payout-due endpoint. Unresolvable strings keep their raw value — never dropped,
    never guessed."""
    out = {}
    try:
        rows = (client.schema("commcalc").table("store_mapping").select("store_address,store_code")
                .eq("org_id", org_id).limit(5000).execute().data) or []
    except Exception:
        return out
    for r in rows:
        code = str(r.get("store_code") or "").strip()
        if not code:
            continue
        addr = str(r.get("store_address") or "").strip().lower()
        if addr:
            out[addr] = code
        out.setdefault(code.lower(), code)
    return out


def resolve_store_code(raw_store, smap):
    """POS store string -> store_code (or the trimmed raw string when unmapped). PURE."""
    s = str(raw_store or "").strip()
    if not s:
        return ""
    hit = smap.get(s.lower())
    if hit:
        return hit
    # POS strings are frequently "1234 Main St ..." where the leading token is the store number.
    first = s.split(" ")[0].strip().lower()
    return smap.get(first) or s


# ── the day's sale lines ─────────────────────────────────────────────────────────────────────────
def _pick_day_rows(raw, feed):
    """(rows, source_table) given ONE day's rows from both sales tables. PURE.

    Sales live in two tables (daily_sales_feed = the hourly B2B email feed; raw_sales = the
    authoritative monthly basis) and a day can exist in either or both. Take the RICHER one — NEVER a
    union, because a trans_id present in both would then be counted twice and the rep's accrual would
    read double. Deterministic tie-break: raw_sales (the authoritative basis) wins an exact tie."""
    if len(feed) > len(raw):
        return feed, "daily_sales_feed"
    return raw, "raw_sales"


def read_day_sales(client, org_id, day):
    """That ONE day's sale lines, org-scoped: (rows, source_table, read_error).

    Reads by trans_date, NOT by period, so the period-spelling bug class ('June 2026' vs '2026-06')
    cannot reach it. `read_error` is True only when BOTH tables failed to read — the caller must then
    write nothing at all, because "I couldn't read the sales" and "there were no sales" have to have
    different consequences (the second legitimately clears a day, the first must never)."""
    iso = day.isoformat()

    def _page(table):
        out, start, page = [], 0, 1000
        while True:
            rows = (client.schema("commcalc").table(table).select("*")
                    .eq("org_id", org_id).eq("trans_date", iso)
                    .range(start, start + page - 1).execute().data) or []
            out.extend(rows)
            if len(rows) < page:
                return out
            start += page

    raw, feed, ok = [], [], 0
    try:
        raw = _page("raw_sales")
        ok += 1
    except Exception:
        raw = []
    try:
        feed = _page("daily_sales_feed")
        ok += 1
    except Exception:
        feed = []
    rows, table = _pick_day_rows(raw, feed)
    return rows, table, (ok == 0)


# ── computing one day ────────────────────────────────────────────────────────────────────────────
def compute_day(client, org_id, day, cfg=None, carrier_mode=None):
    """Per-employee accrual rows for ONE date. READ-ONLY — writes nothing.

    Returns {ready, mode, work_date, source_table, sale_lines, rows:[...], note}. Each row:
    {employee_key, employee_name, store_code, store_raw, base_amount, components}.

    LIGHT BY CONSTRUCTION: one day of sale lines, one plan/calculator pass over them. It never calls
    _run_calculation, never touches the 300s-502-prone recompute path, and never deletes or rewrites
    anything a payout reads."""
    cfg = cfg or normalize_config(None)
    from app.modules.commcalc import router as _r          # lazy: router imports this module

    if carrier_mode is None:
        try:
            carriers = (client.schema("commcalc").table("carrier").select("*")
                        .eq("org_id", org_id).execute().data) or []
        except Exception:
            carriers = []
        carrier_mode = _r._resolve_carrier_mode(carriers)

    lines, source_table, read_error = read_day_sales(client, org_id, day)
    if read_error:
        return {"ready": True, "mode": carrier_mode, "work_date": day.isoformat(), "rows": [],
                "sale_lines": 0, "source_table": None, "read_error": True,
                "note": ("neither raw_sales nor daily_sales_feed could be read for this date — "
                         "nothing was computed and nothing will be written")}

    smap = store_code_map(client, org_id)
    period = period_label(day)

    if not lines:
        return {"ready": True, "mode": carrier_mode, "work_date": day.isoformat(), "rows": [],
                "sale_lines": 0, "source_table": source_table, "read_error": False,
                "note": "no sale lines for this date (nothing accrued — not an error)"}

    rows = (_compute_day_plan(client, org_id, period, lines, cfg, smap, source_table)
            if carrier_mode != "boost"
            else _compute_day_boost(client, org_id, period, lines, cfg, smap, source_table))
    return {"ready": True, "mode": carrier_mode, "work_date": day.isoformat(), "rows": rows,
            "sale_lines": len(lines), "source_table": source_table, "read_error": False, "note": None}


def _compute_day_plan(client, org_id, period, lines, cfg, smap, source_table):
    """PLAN-MODE day. Drives commission_engine.preview() with the day's lines as sales_override — the
    SAME function the live plan payout runs through, so an accrual can never be computed by a formula
    the monthly pay does not use.

    carrier-mode gate: a rep with no Commission Plan assignment accrues $0 here, exactly as they are
    paid $0 monthly. That is CORRECT (a missing plan/assignment is a config gap, not a bug) and the
    coverage note below makes it visible instead of silent."""
    from app.modules.commcalc import commission_engine as _ce
    res = _ce.preview(client, org_id, period, sales_override=lines)
    if not res.get("ready"):
        return []
    tier_as_computed = (cfg.get("tier_basis") == "as_computed")
    out = []
    for r in res.get("by_rep") or []:
        base = safe_float(r.get("base_payout"))
        tiered = safe_float(r.get("tiered_payout"))
        setup = safe_float(r.get("setup_fee_comm"))
        mult = safe_float(r.get("tier_multiplier")) or 1.0
        amount = base + (tiered * mult if tier_as_computed else tiered) + setup
        name = str(r.get("rep") or "").strip()
        if not name:
            continue
        raw_store = str(r.get("store") or "").strip()
        out.append({
            "employee_key": canon_key(name),
            "employee_name": name,
            "store_code": resolve_store_code(raw_store, smap),
            "store_raw": raw_store,
            "base_amount": _round(amount),
            "components": {
                "mode": "plan",
                "source_table": source_table,
                "plan_id": r.get("plan_id"), "plan_name": r.get("plan_name"),
                "market": r.get("market"),
                "rule_payout": _round(base + tiered),
                "setup_fee_comm": _round(setup),
                "qualifying_units": r.get("qualifying_units"),
                "day_tier_multiplier": mult,
                "tier_basis": cfg.get("tier_basis"),
                "rules": [{"label": rb.get("label"), "payout_kind": rb.get("payout_kind"),
                           "matched_lines": rb.get("matched_lines"),
                           "qualifying_units": rb.get("qualifying_units"),
                           "tiered": rb.get("tiered"), "payout": _round(rb.get("payout"))}
                          for rb in (r.get("rules") or [])],
                "deferred_to_monthly": ([] if tier_as_computed else ["plan_tier_multiplier"]),
                "explain": ("Un-tiered day total: the plan's tier multiplier is a MONTHLY attainment "
                            "and is recognized once, later, as the monthly true-up."
                            if not tier_as_computed else
                            "Includes this day's own tier multiplier (tenant setting "
                            "tier_basis='as_computed')."),
            },
        })
    return out


def _compute_day_boost(client, org_id, period, lines, cfg, smap, source_table):
    """BOOST-MODE day. Drives calculator.calc_rep_commissions over the day's lines with the MONTHLY
    inputs empty (ePay payment detail, MI, DLAR) and takes `subtotal` — the 8 sale-derived components
    BEFORE the KPI tier multiplier.

    Why empty and not "the month's": a monthly residual/trade-in/tier figure attributed to a single day
    would be paid-looking nonsense (the same residual accrued 30 times). Those two components are named
    in components.deferred_to_monthly and arrive in the true-up."""
    from app.modules.commcalc.calculator import calc_rep_commissions
    from app.modules.commcalc import router as _r

    def _fetch(table, period_filter=False):
        try:
            q = (client.schema("commcalc").table(table).select("*").eq("org_id", org_id))
            if period_filter:
                q = q.in_("period", _pvariants(period))
            return (q.limit(50000).execute().data) or []
        except Exception:
            return []

    cfg_rows = _fetch("payout_config", period_filter=True)
    pcfg = cfg_rows[0] if cfg_rows else {}
    try:
        _acfg = _r._accessory_config(client, org_id)
        pcfg = {**pcfg,
                "accessory_departments": _acfg["departments_list"],
                "accessory_categories": _acfg["categories_list"],
                "accessory_product_keywords": _acfg["products_list"],
                "acima_tenders": _acfg["acima_tenders_list"],
                "setup_fee_keywords": _acfg["setup_fee_keywords_list"],
                "setup_fee_match_mode": _r._sfp_cfg_mode(client, org_id)}
    except Exception:
        pass

    res = calc_rep_commissions(
        sales=lines, pay_detail=[], dlar_rep=[], dlar_store=[], mi_rows=[],
        catalog=_fetch("raw_catalog"), cfg=pcfg, store_mapping=_fetch("store_mapping"),
        shifts=[], employees=_fetch("employees"), stores=_fetch("stores"),
        period=period, name_map=_fetch("name_map"), carrier_mode="boost")

    tier_as_computed = (cfg.get("tier_basis") == "as_computed")
    out = []
    for r in res.get("commissions") or []:
        name = str(r.get("epay_salesperson") or "").strip()
        if not name:
            continue
        subtotal = safe_float(r.get("subtotal"))
        amount = safe_float(r.get("total_payout")) if tier_as_computed else subtotal
        raw_store = str(r.get("store") or "").strip()
        out.append({
            "employee_key": canon_key(name),
            "employee_name": name,
            "store_code": resolve_store_code(raw_store, smap),
            "store_raw": raw_store,
            "base_amount": _round(amount),
            "components": {
                "mode": "boost",
                "source_table": source_table,
                "premium_acts": r.get("premium_acts"), "byod_acts": r.get("byod_acts"),
                "upgrade_acts": r.get("upgrade_acts"),
                "premium_comm": _round(r.get("premium_comm")),
                "byod_comm": _round(r.get("byod_comm")),
                "upgrade_comm": _round(r.get("upgrade_comm")),
                "acc_comm": _round(r.get("acc_comm")),
                "setup_fee_comm": _round(r.get("setup_fee_comm")),
                "acima_comm": _round(r.get("acima_comm")),
                "custom_comm": _round(r.get("custom_comm")),
                "subtotal": _round(subtotal),
                "tier_basis": cfg.get("tier_basis"),
                "deferred_to_monthly": (["kpi_tier", "trade_in_spiff"] if not tier_as_computed
                                        else ["trade_in_spiff"]),
                "explain": ("Un-tiered day total. The KPI tier comes from the MONTHLY DLAR and the "
                            "trade-in spiff from the MONTHLY ePay payment detail — neither is knowable "
                            "from one day's sales, so both arrive in the monthly true-up."),
            },
        })
    return out


# ── monthly tier / true-up recognition ───────────────────────────────────────────────────────────
def _final_month_totals(client, org_id, period):
    """{employee_key -> final total_payout} from that period's FINISHED commission run. READ-ONLY.

    This is the ONLY place this module reads rep_commissions, and it is a SELECT. Nothing in this file
    ever writes, updates or deletes a rep_commissions row."""
    try:
        rows = (client.schema("commcalc").table("rep_commissions")
                .select("epay_salesperson,storeops_name,store,total_payout")
                .eq("org_id", org_id).in_("period", _pvariants(period))
                .limit(20000).execute().data) or []
    except Exception:
        return {}, {}
    totals, stores = {}, {}
    for r in rows:
        name = str(r.get("epay_salesperson") or r.get("storeops_name") or "").strip()
        if not name:
            continue
        k = canon_key(name)
        totals[k] = _round(totals.get(k, 0.0) + safe_float(r.get("total_payout")))
        stores.setdefault(k, {"name": name, "store": str(r.get("store") or "").strip()})
    return totals, stores


def _accrued_base_by_employee(client, org_id, start, end):
    """{employee_key -> SUM(base_amount)} over [start, end] inclusive. Base only: a true-up must be
    measured against what the DAYS accrued, never against a previously recognized true-up."""
    out = {}
    start_i, page = 0, 1000
    while True:
        rows = (client.schema("commcalc").table(ACCRUAL_TABLE)
                .select("employee_key,base_amount")
                .eq("org_id", org_id).gte("work_date", start.isoformat())
                .lte("work_date", end.isoformat())
                .range(start_i, start_i + page - 1).execute().data) or []
        for r in rows:
            k = r.get("employee_key") or ""
            out[k] = _round(out.get(k, 0.0) + safe_float(r.get("base_amount")))
        if len(rows) < page:
            return out
        start_i += page


def _existing_recognitions(client, org_id, since):
    """{(employee_key, source_period) -> work_date} for every true-up ALREADY recognized on/after
    `since`. This is the recognized-once memory — the accrual rows themselves, no watermark table to
    drift out of sync. Cheap: only rows with a non-zero tier_amount are scanned."""
    out = {}
    start_i, page = 0, 1000
    while True:
        rows = (client.schema("commcalc").table(ACCRUAL_TABLE)
                .select("work_date,employee_key,tier_amount,components")
                .eq("org_id", org_id).neq("tier_amount", 0)
                .gte("work_date", since.isoformat())
                .range(start_i, start_i + page - 1).execute().data) or []
        for r in rows:
            comp = r.get("components") or {}
            src = ((comp.get("tier") or {}) if isinstance(comp, dict) else {}).get("source_period")
            if src:
                out[(r.get("employee_key") or "", src)] = str(r.get("work_date") or "")[:10]
        if len(rows) < page:
            return out
        start_i += page


def pending_tier_recognitions(client, org_id, day, cfg):
    """True-ups that should be recognized ON `day`. READ-ONLY.

    For each of the last `lookback_months` months before `day`'s month, in oldest-first order:
      1. the month's commission run must EXIST (rep_commissions rows) — no run, no recognition;
      2. `day` must be on/after the tenant's configured recognition date for that month;
      3. the (employee, month) pair must not already be recognized on a DIFFERENT date — recognized on
         `day` itself is fine and is simply restated, which is what makes a re-run of `day` idempotent.

    Returns [{employee_key, employee_name, store_raw, source_period, amount, final_total,
              accrued_base, days_accrued}]."""
    tr = cfg.get("tier_recognition") or {}
    lookback = int(tr.get("lookback_months") or 3)
    this_month_first = _date(day.year, day.month, 1)
    earliest_scan = add_months(this_month_first, -lookback)
    try:
        already = _existing_recognitions(client, org_id, earliest_scan)
    except Exception as e:
        if not _table_missing(e):
            raise
        already = {}

    out = []
    for back in range(lookback, 0, -1):
        m_first = add_months(this_month_first, -back)
        m_last = month_bounds(m_first)[1]
        if day < recognition_earliest(m_first, cfg):
            continue
        period = period_label(m_first)
        totals, meta = _final_month_totals(client, org_id, period)
        if not totals:
            continue                      # run not finished (or nothing paid) — nothing to recognize
        try:
            accrued_base = _accrued_base_by_employee(client, org_id, m_first, m_last)
        except Exception as e:
            if not _table_missing(e):
                raise
            accrued_base = {}
        try:
            day_counts = _accrual_day_counts(client, org_id, m_first, m_last)
        except Exception:
            day_counts = {}
        for k, final in totals.items():
            prior = already.get((k, period))
            if prior and prior != day.isoformat():
                continue                  # already recognized on another date — never twice
            base = safe_float(accrued_base.get(k, 0.0))
            amount = _round(final - base)
            if amount == 0 and not prior:
                continue                  # nothing to true up
            info = meta.get(k) or {}
            out.append({
                "employee_key": k,
                "employee_name": info.get("name") or k,
                "store_raw": info.get("store") or "",
                "source_period": period,
                "amount": amount,
                "final_total": _round(final),
                "accrued_base": _round(base),
                "days_accrued": int(day_counts.get(k, 0)),
            })
    return out


def _accrual_day_counts(client, org_id, start, end):
    """{employee_key -> number of accrued DAYS in the window} — used only to warn, in words, when a
    true-up is large because the daily accrual wasn't running for most of the month."""
    out, seen, start_i, page = {}, set(), 0, 1000
    while True:
        rows = (client.schema("commcalc").table(ACCRUAL_TABLE).select("employee_key,work_date")
                .eq("org_id", org_id).gte("work_date", start.isoformat())
                .lte("work_date", end.isoformat())
                .range(start_i, start_i + page - 1).execute().data) or []
        for r in rows:
            key = (r.get("employee_key") or "", str(r.get("work_date") or "")[:10])
            if key in seen:
                continue
            seen.add(key)
            out[key[0]] = out.get(key[0], 0) + 1
        if len(rows) < page:
            return out
        start_i += page


# ── the run (the ONLY writer) ────────────────────────────────────────────────────────────────────
def run_day(client, org_id, day, cfg=None, carrier_mode=None):
    """Compute + persist ONE date's accrual for one tenant. IDEMPOTENT.

    A re-run REPLACES that date: rows are upserted on (org_id, work_date, employee_key, store_code)
    and any row for the date that the recomputation no longer produces is deleted, so a voided sale
    can't leave a phantom accrual behind. Every insert stamps org_id (contract §2 write side).

    Writes EXACTLY ONE table: commcalc.daily_commission_accrual. It does not write rep_commissions,
    plans, schedules, payout_config or anything else — see the module docstring."""
    cfg = cfg or load_config(client, org_id)
    if not cfg.get("enabled"):
        return {"ready": True, "org_id": org_id, "date": day.isoformat(), "skipped": "accrual disabled for this tenant",
                "employees": 0, "written": 0}

    day_res = compute_day(client, org_id, day, cfg=cfg, carrier_mode=carrier_mode)
    if day_res.get("read_error"):
        # "couldn't read the sales" must NOT look like "there were no sales" — the second legitimately
        # clears the day's rows below, the first would erase a good accrual over a transient blip.
        return {"ready": True, "org_id": org_id, "date": day.isoformat(), "employees": 0, "written": 0,
                "removed": 0, "skipped": "sales unreadable — nothing written", "note": day_res.get("note")}
    by_key = {}
    for r in day_res.get("rows") or []:
        k = (r["employee_key"], r["store_code"])
        prev = by_key.get(k)
        if prev:            # defensive: two engine rows for one (rep, store) can only ever sum
            prev["base_amount"] = _round(prev["base_amount"] + r["base_amount"])
        else:
            by_key[k] = dict(r)

    # monthly true-up, folded into the SAME row for the day (one row per rep/store/day, per the spec)
    smap = store_code_map(client, org_id)
    try:
        pend = pending_tier_recognitions(client, org_id, day, cfg)
    except Exception as e:
        if not _table_missing(e):
            raise
        return {"ready": False, "note": _MISSING_NOTE, "org_id": org_id, "date": day.isoformat()}
    tier_total = 0.0
    for p in pend:
        # attach to the employee's row for this day if they sold today, else their run's store
        target = next((k for k in by_key if k[0] == p["employee_key"]), None)
        if target is None:
            code = resolve_store_code(p.get("store_raw"), smap)
            target = (p["employee_key"], code)
            by_key[target] = {"employee_key": p["employee_key"], "employee_name": p["employee_name"],
                              "store_code": code, "store_raw": p.get("store_raw") or "",
                              "base_amount": 0.0,
                              "components": {"mode": "tier_only", "source_table": None,
                                             "explain": "No sales this day — this row carries only the "
                                                        "recognized monthly true-up."}}
        row = by_key[target]
        row["tier_amount"] = _round(safe_float(row.get("tier_amount")) + p["amount"])
        comp = row.setdefault("components", {})
        comp["tier"] = {
            "source_period": p["source_period"], "amount": p["amount"],
            "final_month_total": p["final_total"], "daily_base_accrued": p["accrued_base"],
            "days_accrued": p["days_accrued"],
            "explain": (f"Monthly true-up for {p['source_period']}: that month's finished commission run "
                        f"totalled ${p['final_total']:,.2f} and ${p['accrued_base']:,.2f} had been "
                        f"accrued daily across {p['days_accrued']} day(s), so ${p['amount']:,.2f} is "
                        f"recognized once, here. This is an expected-value correction, not a payment."),
            "partial_month_warning": (p["days_accrued"] < 5),
        }
        tier_total = _round(tier_total + p["amount"])

    now_iso = _datetime.now(_timezone.utc).isoformat()
    payload = []
    for (ekey, code), r in by_key.items():
        base = _round(r.get("base_amount"))
        tier = _round(r.get("tier_amount"))
        payload.append({
            "org_id": org_id,                       # RULE ONE write side: stamped on every insert
            "work_date": day.isoformat(),
            "employee_key": ekey,
            "store_code": code or "",
            "employee_name": r.get("employee_name"),
            "base_amount": base, "tier_amount": tier, "total_amount": _round(base + tier),
            "components": r.get("components") or {},
            "computed_at": now_iso,
        })

    try:
        if payload:
            for i in range(0, len(payload), 500):
                (client.schema("commcalc").table(ACCRUAL_TABLE)
                 .upsert(payload[i:i + 500],
                         on_conflict="org_id,work_date,employee_key,store_code").execute())
        # drop rows for this date that the recomputation no longer produces (idempotent replace)
        existing = (client.schema("commcalc").table(ACCRUAL_TABLE).select("id,employee_key,store_code")
                    .eq("org_id", org_id).eq("work_date", day.isoformat())
                    .limit(20000).execute().data) or []
        keep = {(p["employee_key"], p["store_code"]) for p in payload}
        stale = [e["id"] for e in existing
                 if (e.get("employee_key") or "", e.get("store_code") or "") not in keep]
        for i in range(0, len(stale), 200):
            (client.schema("commcalc").table(ACCRUAL_TABLE).delete()
             .eq("org_id", org_id).in_("id", stale[i:i + 200]).execute())
    except Exception as e:
        if _table_missing(e):
            return {"ready": False, "note": _MISSING_NOTE, "org_id": org_id, "date": day.isoformat()}
        raise

    return {"ready": True, "org_id": org_id, "date": day.isoformat(), "mode": day_res.get("mode"),
            "source_table": day_res.get("source_table"), "sale_lines": day_res.get("sale_lines", 0),
            "employees": len(payload), "written": len(payload), "removed": len(stale),
            "base_total": _round(sum(p["base_amount"] for p in payload)),
            "tier_recognized": tier_total, "tier_recognitions": len(pend),
            "note": day_res.get("note")}


# ── reads ────────────────────────────────────────────────────────────────────────────────────────
def _page_accruals(client, org_id, until, employee_key=None, store_code=None, since=None):
    out, start_i, page = [], 0, 1000
    while True:
        q = (client.schema("commcalc").table(ACCRUAL_TABLE).select("*")
             .eq("org_id", org_id).lte("work_date", until.isoformat()))
        if since:
            q = q.gte("work_date", since.isoformat())
        if employee_key:
            q = q.eq("employee_key", employee_key)
        if store_code:
            q = q.eq("store_code", store_code)
        rows = (q.range(start_i, start_i + page - 1).execute().data) or []
        out.extend(rows)
        if len(rows) < page:
            return out
        start_i += page


def _page_ledger(client, org_id, until, employee_key=None, store_code=None, since=None):
    out, start_i, page = [], 0, 1000
    while True:
        q = (client.schema("commcalc").table(LEDGER_TABLE).select("*")
             .eq("org_id", org_id).lte("paid_date", until.isoformat()))
        if since:
            q = q.gte("paid_date", since.isoformat())
        if employee_key:
            q = q.eq("employee_key", employee_key)
        if store_code:
            q = q.eq("store_code", store_code)
        rows = (q.range(start_i, start_i + page - 1).execute().data) or []
        out.extend(rows)
        if len(rows) < page:
            return out
        start_i += page


def accrued(client, org_id, as_of, employee_key=None, store_code=None, keyset=None, since=None):
    """GET /commcalc/payout/accrued — per-employee accrued / paid / unpaid as of a date.

    Shape is FIXED by docs/specs/envelope-expense-payout.md (retail-ops' payout-due endpoint consumes
    it): {employees:[{employee_key, name, store_codes[], accrued_total, paid_total, unpaid_balance,
    today_accrual, components:{base,tier}}], as_of}. Additional keys (flags, counts, note) are additive
    and safe for that consumer to ignore.

    accrued_total is the LIFETIME accrual up to as_of, and paid_total the lifetime advances, because
    the useful figure for the envelope is a running unpaid BALANCE, not a month-slice."""
    try:
        arows = _page_accruals(client, org_id, as_of, employee_key, store_code, since)
        lrows = _page_ledger(client, org_id, as_of, employee_key, store_code, since)
    except Exception as e:
        if _table_missing(e):
            return {"ready": False, "as_of": as_of.isoformat(), "employees": [], "note": _MISSING_NOTE}
        raise

    emp = {}

    def _slot(k, name=None):
        e = emp.get(k)
        if not e:
            e = emp[k] = {"employee_key": k, "name": name or k, "store_codes": [],
                          "accrued_total": 0.0, "paid_total": 0.0, "unpaid_balance": 0.0,
                          "today_accrual": 0.0, "components": {"base": 0.0, "tier": 0.0},
                          "accrual_days": 0, "payout_count": 0, "last_paid_date": None,
                          "last_accrual_date": None}
        if name and (e["name"] == k or not e["name"]):
            e["name"] = name
        return e

    iso = as_of.isoformat()
    for r in arows:
        k = r.get("employee_key") or ""
        if not k:
            continue
        code = str(r.get("store_code") or "").strip()
        # Span scope: a row whose store IS resolved and is outside the caller's span is hidden. A row
        # with NO resolved store stays visible rather than vanishing — an unmapped POS store string is
        # a mapping gap (fix at Store Matching), and silently dropping the rep's money would be worse.
        if keyset is not None and code and not _in_keys(keyset, code):
            continue
        e = _slot(k, r.get("employee_name"))
        e["accrued_total"] = _round(e["accrued_total"] + safe_float(r.get("total_amount")))
        e["components"]["base"] = _round(e["components"]["base"] + safe_float(r.get("base_amount")))
        e["components"]["tier"] = _round(e["components"]["tier"] + safe_float(r.get("tier_amount")))
        e["accrual_days"] += 1
        wd = str(r.get("work_date") or "")[:10]
        if wd == iso:
            e["today_accrual"] = _round(e["today_accrual"] + safe_float(r.get("total_amount")))
        if code and code not in e["store_codes"]:
            e["store_codes"].append(code)
        if wd and (e["last_accrual_date"] is None or wd > e["last_accrual_date"]):
            e["last_accrual_date"] = wd

    for r in lrows:
        k = r.get("employee_key") or ""
        if not k:
            continue
        code = str(r.get("store_code") or "").strip()
        if keyset is not None and code and not _in_keys(keyset, code):
            continue
        # A payout for someone with no accrual row still belongs in the answer — that IS the
        # over-advance case, and dropping it would hide exactly what the flag exists to show.
        e = _slot(k, r.get("employee_name"))
        e["paid_total"] = _round(e["paid_total"] + safe_float(r.get("amount")))
        e["payout_count"] += 1
        pd = str(r.get("paid_date") or "")[:10]
        if pd and (e["last_paid_date"] is None or pd > e["last_paid_date"]):
            e["last_paid_date"] = pd
        if code and code not in e["store_codes"]:
            e["store_codes"].append(code)

    out = []
    for e in emp.values():
        e["unpaid_balance"] = _round(e["accrued_total"] - e["paid_total"])
        e["over_advanced"] = bool(e["paid_total"] - e["accrued_total"] > 0.005)
        e["over_advance_amount"] = _round(max(0.0, e["paid_total"] - e["accrued_total"]))
        e["store_codes"].sort()
        out.append(e)
    out.sort(key=lambda x: -(x.get("unpaid_balance") or 0))
    flagged = [e for e in out if e["over_advanced"]]
    return {
        "ready": True, "as_of": as_of.isoformat(), "employees": out,
        "totals": {"accrued": _round(sum(e["accrued_total"] for e in out)),
                   "paid": _round(sum(e["paid_total"] for e in out)),
                   "unpaid": _round(sum(e["unpaid_balance"] for e in out)),
                   "today": _round(sum(e["today_accrual"] for e in out)),
                   "employees": len(out)},
        "over_advanced": len(flagged),
        "note": ("Accrued figures are EXPECTED (probable) commission, not pay. Paid figures are cash "
                 "ADVANCES against them. Nothing here changes what anyone is owed."),
    }


def _in_keys(keyset, *vals):
    if keyset is None:
        return True
    return any(str(v or "").strip().upper() in keyset for v in vals if v)


def over_advance_review(client, org_id, as_of, keyset=None, lookback_months=3):
    """The review list for advances that outran the accrual. NO clawback, NO netting (ledger Q14).

    Two independent questions, both answered:
      running — lifetime advances exceed lifetime accrual as of `as_of`;
      monthly — for a month whose commission run is FINISHED, cash advanced within that month exceeds
                what that month actually paid. This is the one an owner cares about: it means real
                cash left the envelope against commission that did not materialize."""
    acc = accrued(client, org_id, as_of, keyset=keyset)
    if not acc.get("ready"):
        return {"ready": False, "as_of": as_of.isoformat(), "running": [], "monthly": [],
                "note": _MISSING_NOTE}
    running = [{"employee_key": e["employee_key"], "name": e["name"], "store_codes": e["store_codes"],
                "accrued_total": e["accrued_total"], "paid_total": e["paid_total"],
                "over_by": e["over_advance_amount"],
                "reason": "lifetime cash advances exceed lifetime accrued commission"}
               for e in acc["employees"] if e["over_advanced"]]

    monthly = []
    this_first = _date(as_of.year, as_of.month, 1)
    for back in range(int(lookback_months or 3), 0, -1):
        m_first = add_months(this_first, -back)
        m_last = month_bounds(m_first)[1]
        period = period_label(m_first)
        totals, meta = _final_month_totals(client, org_id, period)
        if not totals:
            continue
        try:
            paid = {}
            for r in _page_ledger(client, org_id, m_last, since=m_first):
                code = str(r.get("store_code") or "").strip()
                if keyset is not None and code and not _in_keys(keyset, code):
                    continue
                k = r.get("employee_key") or ""
                paid[k] = _round(paid.get(k, 0.0) + safe_float(r.get("amount")))
        except Exception as e:
            if _table_missing(e):
                continue
            raise
        for k, amt in paid.items():
            final = safe_float(totals.get(k, 0.0))
            if amt - final > 0.005:
                monthly.append({
                    "employee_key": k, "name": (meta.get(k) or {}).get("name") or k,
                    "period": period, "final_month_total": _round(final), "paid_in_month": _round(amt),
                    "over_by": _round(amt - final),
                    "reason": (f"cash advanced during {period} exceeds what {period}'s finished "
                               f"commission run actually paid")})
    monthly.sort(key=lambda x: -x["over_by"])
    return {"ready": True, "as_of": as_of.isoformat(), "running": running, "monthly": monthly,
            "counts": {"running": len(running), "monthly": len(monthly)},
            "policy": ("Flag only — no clawback and no netting. Correcting an over-advance is a human "
                       "decision (ledger Q14 default).")}


def ledger_rows(client, org_id, start=None, end=None, employee_key=None, store_code=None, keyset=None):
    """GET /commcalc/payout/ledger — org-scoped advance list for the report surface."""
    end = end or _date.today()
    try:
        rows = _page_ledger(client, org_id, end, employee_key, store_code, start)
    except Exception as e:
        if _table_missing(e):
            return {"ready": False, "rows": [], "note": _MISSING_NOTE}
        raise
    out = []
    for r in rows:
        code = str(r.get("store_code") or "").strip()
        if keyset is not None and code and not _in_keys(keyset, code):
            continue
        out.append({"id": r.get("id"), "employee_key": r.get("employee_key"),
                    "name": r.get("employee_name") or r.get("employee_key"),
                    "amount": _round(r.get("amount")), "paid_date": str(r.get("paid_date") or "")[:10],
                    "method": r.get("method"), "store_code": code,
                    "withdrawal_ref": r.get("withdrawal_ref"), "note": r.get("note"),
                    "recorded_by": r.get("recorded_by"),
                    "created_at": r.get("created_at")})
    out.sort(key=lambda x: (x["paid_date"], x["id"] or 0), reverse=True)
    return {"ready": True, "rows": out, "total": _round(sum(r["amount"] for r in out)),
            "count": len(out)}


def record_payout(client, org_id, body, recorded_by=None):
    """POST /commcalc/payout/record — write ONE cash-advance row. org_id is stamped, never taken from
    the body (contract §2: a body-sourced org lands the row in the wrong tenant).

    This RECORDS a cash movement. It does not pay anybody, does not change the accrual, and does not
    touch rep_commissions."""
    body = body or {}
    name = str(body.get("employee_name") or body.get("name") or "").strip()
    key = str(body.get("employee_key") or "").strip() or canon_key(name)
    if not key:
        raise ValueError("employee_key (or employee_name) is required")
    amount = safe_float(body.get("amount"))
    if amount <= 0:
        raise ValueError("amount must be greater than zero (this ledger records advances PAID, and "
                         "never nets or claws back — a correction is its own decision)")
    pd = parse_day(body.get("paid_date"), None) or _date.today()
    row = {
        "org_id": org_id,
        "employee_key": key,
        "employee_name": name or None,
        "amount": _round(amount),
        "paid_date": pd.isoformat(),
        "method": (str(body.get("method") or "").strip() or "envelope_cash"),
        "store_code": (str(body.get("store_code") or "").strip() or None),
        "withdrawal_ref": (str(body.get("withdrawal_ref") or "").strip() or None),
        "note": (str(body.get("note") or "").strip() or None),
        "recorded_by": (str(body.get("recorded_by") or "").strip() or recorded_by or None),
    }
    try:
        res = client.schema("commcalc").table(LEDGER_TABLE).insert(row).execute()
    except Exception as e:
        if _table_missing(e):
            return {"ready": False, "note": _MISSING_NOTE}
        # the partial unique index on (org_id, withdrawal_ref) makes a double-submit a no-op rather
        # than a second advance on paper
        if "duplicate key" in str(e).lower() and row.get("withdrawal_ref"):
            existing = (client.schema("commcalc").table(LEDGER_TABLE).select("*")
                        .eq("org_id", org_id).eq("withdrawal_ref", row["withdrawal_ref"])
                        .limit(1).execute().data) or []
            return {"ready": True, "duplicate": True, "row": (existing[0] if existing else None),
                    "note": "this envelope withdrawal was already recorded — nothing was added"}
        raise
    return {"ready": True, "row": ((res.data or [{}])[0] if getattr(res, "data", None) else row)}


# ── the daily sweep ──────────────────────────────────────────────────────────────────────────────
def active_orgs(client, days):
    """Tenants with sale lines on any of `days` — the set the accrual sweep must cover. Bounded scan
    over both sales tables; never raises (an enumeration failure returns what it has)."""
    seen = set()
    for table in ("daily_sales_feed", "raw_sales"):
        try:
            start_i, page = 0, 5000
            while True:
                rows = (client.schema("commcalc").table(table).select("org_id")
                        .in_("trans_date", [d.isoformat() for d in days])
                        .range(start_i, start_i + page - 1).execute().data) or []
                for r in rows:
                    if r.get("org_id"):
                        seen.add(r["org_id"])
                if len(rows) < page:
                    break
                start_i += page
        except Exception:
            continue
    return sorted(seen)


def run_all_due(client, dates=None, org_ids=None, today=None):
    """The DAILY AUTO-RUN. Re-accrues yesterday + today (per-tenant `auto_run.days_back`) for every
    tenant with sales on those dates.

    Wired into the module's EXISTING scheduled sweep path — it is called at the tail of
    `_promote_all_due` (so it runs right AFTER the feed→raw_sales derive lands, which is the moment
    the day's sales are actually complete) and is also exposed as its own NOTIFY_RUN_SECRET
    `/payout/accrual/run-due` entry point, exactly like the DLAR / email / promote sweeps. No new cron
    infrastructure.

    NEVER raises into its caller: a failure here must not be able to break the sales promotion it
    rides on."""
    today = today or _date.today()
    default_days = [today - _timedelta(days=1), today]
    scan = dates or default_days
    orgs = org_ids if org_ids is not None else active_orgs(client, scan)
    out, ran = [], 0
    for oid in orgs:
        try:
            cfg = load_config(client, oid)
            if not cfg.get("enabled") or not (cfg.get("auto_run") or {}).get("enabled", True):
                out.append({"org_id": oid, "skipped": "accrual auto-run disabled"})
                continue
            back = int((cfg.get("auto_run") or {}).get("days_back", 1))
            days = dates or [today - _timedelta(days=i) for i in range(back, -1, -1)]
            for d in days:
                res = run_day(client, oid, d, cfg=cfg)
                ran += 1
                out.append({"org_id": oid, "date": d.isoformat(),
                            "employees": res.get("employees"), "written": res.get("written"),
                            "base_total": res.get("base_total"),
                            "tier_recognized": res.get("tier_recognized"),
                            "skipped": res.get("skipped"),
                            "ready": res.get("ready", True), "note": res.get("note")})
        except Exception as e:
            out.append({"org_id": oid, "error": str(e)[:200]})
    return {"ok": True, "orgs": len(orgs), "runs": ran, "detail": out}
