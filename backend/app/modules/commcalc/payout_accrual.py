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
      change `accrued` and it does not reduce what the rep is owed at month end. An over-advance is
      always FLAGGED (ledger Q14); a tenant may additionally switch `over_advance_mode='auto_net'`, in
      which case a PRIOR cycle's over-advance is deducted from the employee's NEXT cash due — as its
      own labelled line, never silently — and still nothing is written to rep_commissions and no
      earned pay changes. It nets the envelope-advance balance stream only.
    * Balances are PER CYCLE (ledger Q19): they reset each calendar month / payroll cycle / commission
      cycle as the tenant defines, and anything unsettled from an earlier cycle stays visible as a
      labelled carry-over line with a "settle employee balances" advisory. Advisory only: this module
      never moves cash.

════════════════════════════════════════════════════════════════════════════════════════════════════
HOW A DAY IS COMPUTED
════════════════════════════════════════════════════════════════════════════════════════════════════
The day's sale lines are read and run through the tenant's REAL pay logic — resolved by
`_resolve_carrier_mode`, exactly like the monthly calc:

  * plan mode  -> commission_engine.preview(sales_override=<that day's lines>). That is the SAME
                  function the live plan payout runs through: the same matcher, _line_payout, the
                  flat-once accumulation, plan_pay_gate (scope / exclusion / unit-basis / accessory
                  basis), the accessory classifier and the set-up-fee pay item. A second, drift-prone
                  copy of the pay math would be worse than useless here — a rep would be shown an
                  accrual their monthly pay then contradicts.
  * boost mode -> calculator.calc_rep_commissions over that day's lines, with the MONTHLY inputs
                  (ePay payment detail, MI, DLAR) deliberately empty, and we take `subtotal`.

THE TIER BASIS IS TENANT CONFIG. Owner 2026-08-04 (ledger Q18), verbatim: "it will be based on tier
meeting on that day, it keeps varying throughout the month as their commission changes in the
individual rep report."

  'mtd_attained' (DEFAULT) — see mtd_allocate(). The month's sale lines THROUGH that date are run
      through the same pay logic WITH the real tier attainment, and that month-to-date total is shared
      across the month's accrued days in proportion to each day's un-tiered commission. Consequence,
      by construction: SUM(accruals month-to-date) == the individual rep report's month-to-date
      commission, and the whole current month RESTATES when attainment moves.
  'none'         — accrue the day un-tiered; the entire tier effect arrives later as the monthly
      true-up. (This was the original default: a single day cannot know a monthly attainment, so a day
      multiplied by a guessed tier is wrong in both directions.) Kept as an option for tenants that
      want the conservative number.
  'as_computed'  — accrue the day's OWN multiplier, for plans that tier on something a day can attain.

Two Boost components are deferred under EVERY basis because they are not knowable from sale lines at
all: the KPI tier (DLAR, monthly) and the trade-in spiff (ePay payment detail, monthly).
`components.deferred_to_monthly` says so on every row, in words, and the monthly true-up below still
reconciles whatever residual remains at month close.

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
# The management roles that may RECORD a cash advance (owner 2026-08-04, ledger Q17: "dm or higher").
# A store manager is deliberately NOT in this set — handing envelope cash to a rep is a district-level
# decision. Tenant-overridable via accrual_config.record_roles, and a custom role whose RBAC scope is
# 'all' or 'market' (i.e. it already spans more than one store) also qualifies.
DEFAULT_RECORD_ROLES = ("admin", "district_manager", "market_manager", "regional_manager",
                        "director", "executive")

CODE_DEFAULT = {
    "enabled": True,
    # ── tier basis (owner 2026-08-04, ledger Q18: "it will be based on tier meeting on that day, it
    #    keeps varying throughout the month as their commission changes in the individual rep report")
    # 'mtd_attained' (DEFAULT) — the day is accrued at the tier the rep is MEETING as of that day. The
    #                  month-to-date total is computed from the month's OWN sale lines through that date
    #                  with the real tier attainment applied, then allocated across the month's accrued
    #                  days in proportion to each day's un-tiered commission. Two consequences, both
    #                  intended: SUM(accrual month-to-date) == the rep report's current month-to-date
    #                  commission, and when attainment moves mid-month the WHOLE current month restates.
    # 'none'        — accrue the day UN-TIERED; the whole tier effect arrives as the monthly true-up.
    # 'as_computed' — accrue the day's own tier multiplier (only sane when tiers are day-attainable).
    "tier_basis": "mtd_attained",
    "tier_recognition": {"mode": "on_run_available", "day_of_month": None, "lookback_months": 3},
    # min_interval_minutes throttles the SWEEP only (never a hand-pressed run): the accrual rides the
    # hourly promote sweep, and without this a burst of promote calls would re-drive preview() for
    # every tenant several times inside one hour for no new information.
    "auto_run": {"enabled": True, "days_back": 1, "min_interval_minutes": 50},
    # ── over-advance (owner 2026-08-04, ledger Q14: "flag it and keep an option to auto net")
    # 'flag'     (DEFAULT) — an over-advance is shown and flagged; the next cycle's payable is untouched.
    # 'auto_net' — a PRIOR cycle's over-advance reduces the employee's NEXT payable balance, and the
    #              reduction appears as its OWN labelled line ("Less: prior-cycle over-advance applied").
    #              It still writes nothing to rep_commissions and changes nobody's earned pay — it only
    #              nets the envelope-advance balance stream.
    "over_advance_mode": "flag",
    # ── balance cycle (owner 2026-08-04, ledger Q19: "reset each month and advise the user to clear the
    #    employee balance at the end of the month / payroll cycle / commission cycle as defined in the
    #    system"). Balances RESET per cycle; anything unsettled from a prior cycle stays VISIBLE as a
    #    labelled carry-over line (never hidden, never silently rolled in).
    "cycle": {
        "mode": "calendar_month",              # calendar_month | payroll | commission
        "payroll": {"kind": "semimonthly", "anchor_date": None, "semi_day": 16},
        "commission": {"end_day": None},       # None = the calendar month end
        "carry_cycles": 3,                     # how many prior cycles the settlement view lists
        "settlement_advice_days": 3,           # advise "settle balances" this many days before cycle end
    },
    # ── who may record a cash advance (ledger Q17)
    "record_roles": list(DEFAULT_RECORD_ROLES),
}
TIER_BASES = ("mtd_attained", "none", "as_computed")
RECOGNITION_MODES = ("on_run_available", "day_of_month")
OVER_ADVANCE_MODES = ("flag", "auto_net")
CYCLE_MODES = ("calendar_month", "payroll", "commission")
PAYROLL_KINDS = ("weekly", "biweekly", "semimonthly", "monthly")
# A Monday, used only when a biweekly/weekly payroll cycle has no anchor_date configured.
_ANCHOR_FALLBACK = _date(2026, 1, 5)
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
    cfg["cycle"] = dict(CODE_DEFAULT["cycle"])
    cfg["cycle"]["payroll"] = dict(CODE_DEFAULT["cycle"]["payroll"])
    cfg["cycle"]["commission"] = dict(CODE_DEFAULT["cycle"]["commission"])
    cfg["record_roles"] = list(CODE_DEFAULT["record_roles"])
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
        try:
            mi = int(ar.get("min_interval_minutes"))
        except Exception:
            mi = CODE_DEFAULT["auto_run"]["min_interval_minutes"]
        cfg["auto_run"]["min_interval_minutes"] = min(1440, max(0, mi))
    oam = str(raw.get("over_advance_mode") or "").strip().lower()
    if oam in OVER_ADVANCE_MODES:
        cfg["over_advance_mode"] = oam
    cy = raw.get("cycle")
    if isinstance(cy, dict):
        mode = str(cy.get("mode") or "").strip().lower()
        if mode in CYCLE_MODES:
            cfg["cycle"]["mode"] = mode
        pr = cy.get("payroll")
        if isinstance(pr, dict):
            kind = str(pr.get("kind") or "").strip().lower()
            if kind in PAYROLL_KINDS:
                cfg["cycle"]["payroll"]["kind"] = kind
            anchor = parse_day(pr.get("anchor_date"), None)
            cfg["cycle"]["payroll"]["anchor_date"] = anchor.isoformat() if anchor else None
            try:
                sd = int(pr.get("semi_day"))
            except Exception:
                sd = CODE_DEFAULT["cycle"]["payroll"]["semi_day"]
            # 2..28: a semi-monthly split on the 1st would make the first half empty, and a split past
            # the 28th would not exist in February.
            cfg["cycle"]["payroll"]["semi_day"] = min(28, max(2, sd))
        cm = cy.get("commission")
        if isinstance(cm, dict):
            ed = cm.get("end_day")
            try:
                ed = int(ed) if ed not in (None, "") else None
            except Exception:
                ed = None
            cfg["cycle"]["commission"]["end_day"] = min(31, max(1, ed)) if ed else None
        try:
            cc = int(cy.get("carry_cycles"))
        except Exception:
            cc = CODE_DEFAULT["cycle"]["carry_cycles"]
        cfg["cycle"]["carry_cycles"] = min(12, max(0, cc))
        try:
            sa = int(cy.get("settlement_advice_days"))
        except Exception:
            sa = CODE_DEFAULT["cycle"]["settlement_advice_days"]
        cfg["cycle"]["settlement_advice_days"] = min(28, max(0, sa))
    rr = raw.get("record_roles")
    if isinstance(rr, (list, tuple)):
        roles = sorted({str(x).strip().lower() for x in rr if str(x or "").strip()})
        # An EMPTY list would lock every non-super-admin out of recording cash, so it falls back to the
        # default set rather than bricking the envelope flow (one-directional-safe, like every clamp
        # above).
        cfg["record_roles"] = roles or list(DEFAULT_RECORD_ROLES)
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


# ── balance CYCLES (ledger Q19) — pure, deterministic, tenant-configured ──────────────────────────
def _cycle_label(start, end, mode):
    """A human label for a cycle. A calendar month reads as "August 2026"; anything else names its real
    span, because "August 2026" would be a lie for an Aug 16–31 payroll half. PURE."""
    if mode == "calendar_month" or (start == month_bounds(start)[0] and end == month_bounds(start)[1]):
        return period_label(start)
    if start.year == end.year and start.month == end.month:
        return f"{_calendar.month_abbr[start.month]} {start.day}–{end.day} {start.year}"
    if start.year == end.year:
        return (f"{_calendar.month_abbr[start.month]} {start.day} – "
                f"{_calendar.month_abbr[end.month]} {end.day} {start.year}")
    return f"{start.isoformat()} – {end.isoformat()}"


def cycle_bounds(day, cfg=None):
    """(start, end, label, mode) of the balance CYCLE that `day` falls in. PURE and deterministic.

    Owner 2026-08-04 (ledger Q19): balances "reset each month … or payroll cycle / commission cycle as
    defined in the system". The cycle is therefore tenant config, never a constant:

      calendar_month (default) — the 1st to the month end.
      payroll                  — semimonthly (1..semi_day-1 / semi_day..month end), biweekly or weekly
                                 from an anchor date, or monthly.
      commission               — the commission cycle: it CLOSES on `end_day` (e.g. the 25th), so the
                                 cycle runs (end_day+1 of the previous month) .. (end_day of this one).
                                 end_day is clamped to each month's real length, so a tenant who picks
                                 the 31st still closes in February.

    Nothing here moves money — a cycle only decides which advances and accruals are shown together and
    when the "settle balances" advisory appears."""
    cy = ((cfg or {}).get("cycle") or {})
    if not isinstance(cy, dict):
        cy = {}
    mode = str(cy.get("mode") or "calendar_month").strip().lower()
    if mode not in CYCLE_MODES:
        mode = "calendar_month"
    first, last = month_bounds(day)

    if mode == "payroll":
        p = cy.get("payroll") if isinstance(cy.get("payroll"), dict) else {}
        kind = str(p.get("kind") or "semimonthly").strip().lower()
        if kind not in PAYROLL_KINDS:
            kind = "semimonthly"
        if kind == "semimonthly":
            try:
                sd = int(p.get("semi_day") or CODE_DEFAULT["cycle"]["payroll"]["semi_day"])
            except Exception:
                sd = CODE_DEFAULT["cycle"]["payroll"]["semi_day"]
            sd = min(max(2, sd), last.day)
            if day.day < sd:
                s, e = first, _date(day.year, day.month, sd - 1)
            else:
                s, e = _date(day.year, day.month, sd), last
        elif kind in ("biweekly", "weekly"):
            n = 14 if kind == "biweekly" else 7
            anchor = parse_day(p.get("anchor_date"), None) or _ANCHOR_FALLBACK
            k = (day - anchor).days // n          # floor division: correct before the anchor too
            s = anchor + _timedelta(days=k * n)
            e = s + _timedelta(days=n - 1)
        else:
            s, e = first, last
    elif mode == "commission":
        cm = cy.get("commission") if isinstance(cy.get("commission"), dict) else {}
        try:
            ed = int(cm.get("end_day")) if cm.get("end_day") not in (None, "") else None
        except Exception:
            ed = None
        if not ed:
            s, e = first, last
        else:
            def _close(d):
                f, l = month_bounds(d)
                return _date(d.year, d.month, min(int(ed), l.day))
            this_close = _close(day)
            if day <= this_close:
                s = _close(add_months(first, -1)) + _timedelta(days=1)
                e = this_close
            else:
                s = this_close + _timedelta(days=1)
                e = _close(add_months(first, 1))
    else:
        s, e = first, last
    return s, e, _cycle_label(s, e, mode), mode


def previous_cycle(start, cfg=None):
    """The cycle immediately before the one starting at `start`. PURE."""
    return cycle_bounds(start - _timedelta(days=1), cfg)


def cycle_series(day, cfg=None, back=3):
    """[oldest … current] cycles ending with the one containing `day`. PURE, bounded by `back`."""
    cur = cycle_bounds(day, cfg)
    out = [cur]
    for _ in range(max(0, int(back or 0))):
        out.insert(0, previous_cycle(out[0][0], cfg))
    return out


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


def read_sales_range(client, org_id, start_day, end_day):
    """Sale lines for [start_day, end_day] inclusive, org-scoped: (rows, source_table, read_error).

    Reads by trans_date, NOT by period, so the period-spelling bug class ('June 2026' vs '2026-06')
    cannot reach it. `read_error` is True only when BOTH tables failed to read — the caller must then
    write nothing at all, because "I couldn't read the sales" and "there were no sales" have to have
    different consequences (the second legitimately clears a day, the first must never).

    ONE window read serves both the day and the month-to-date pass of the 'mtd_attained' basis, so the
    two can never disagree about which table the month came from."""
    s, e = start_day.isoformat(), end_day.isoformat()

    def _page(table):
        out, start, page = [], 0, 1000
        while True:
            rows = (client.schema("commcalc").table(table).select("*")
                    .eq("org_id", org_id).gte("trans_date", s).lte("trans_date", e)
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


def read_day_sales(client, org_id, day):
    """That ONE day's sale lines, org-scoped: (rows, source_table, read_error). See read_sales_range."""
    return read_sales_range(client, org_id, day, day)


def day_of(row):
    """A sale line's trans_date as a date (None when unparseable). PURE."""
    return parse_day(row.get("trans_date"), None)


# ── computing one day ────────────────────────────────────────────────────────────────────────────
def resolve_mode(client, org_id, carrier_mode=None):
    """This tenant's carrier mode ('boost' | 'plan') — the same resolver the monthly calc uses."""
    if carrier_mode is not None:
        return carrier_mode
    from app.modules.commcalc import router as _r          # lazy: router imports this module
    try:
        carriers = (client.schema("commcalc").table("carrier").select("*")
                    .eq("org_id", org_id).execute().data) or []
    except Exception:
        carriers = []
    return _r._resolve_carrier_mode(carriers)


def _engine_rows(client, org_id, period, lines, cfg, smap, source_table, carrier_mode):
    """The tenant's REAL pay logic over `lines`, as accrual rows. One switch, used by both the day pass
    and the month-to-date pass so the two can never diverge."""
    if carrier_mode != "boost":
        return _compute_day_plan(client, org_id, period, lines, cfg, smap, source_table)
    return _compute_day_boost(client, org_id, period, lines, cfg, smap, source_table)


def compute_day(client, org_id, day, cfg=None, carrier_mode=None):
    """Per-employee accrual rows for ONE date. READ-ONLY — writes nothing.

    Returns {ready, mode, work_date, source_table, sale_lines, rows:[...], restate:[...], note}. Each
    row: {employee_key, employee_name, store_code, store_raw, base_amount, components}.

    LIGHT BY CONSTRUCTION: under the 'none'/'as_computed' bases this reads ONE day of sale lines and
    makes ONE plan/calculator pass over them. Under the default 'mtd_attained' basis it reads the
    month-to-date WINDOW once and makes two passes over it (the window and the day inside it) — still
    a single bounded read and never `_run_calculation`, never the 300s-502-prone recompute path, and
    never a delete/rewrite of anything a payout reads.

    `restate` is non-empty only under 'mtd_attained': it is the OTHER days of the same month whose
    allocated amount moved because attainment changed (see mtd_allocate)."""
    cfg = cfg or normalize_config(None)
    carrier_mode = resolve_mode(client, org_id, carrier_mode)
    if (cfg.get("tier_basis") or "") == "mtd_attained":
        return mtd_allocate(client, org_id, day, cfg, carrier_mode)

    lines, source_table, read_error = read_day_sales(client, org_id, day)
    if read_error:
        return {"ready": True, "mode": carrier_mode, "work_date": day.isoformat(), "rows": [],
                "restate": [], "sale_lines": 0, "source_table": None, "read_error": True,
                "note": ("neither raw_sales nor daily_sales_feed could be read for this date — "
                         "nothing was computed and nothing will be written")}

    smap = store_code_map(client, org_id)
    period = period_label(day)

    if not lines:
        return {"ready": True, "mode": carrier_mode, "work_date": day.isoformat(), "rows": [],
                "restate": [], "sale_lines": 0, "source_table": source_table, "read_error": False,
                "note": "no sale lines for this date (nothing accrued — not an error)"}

    rows = _engine_rows(client, org_id, period, lines, cfg, smap, source_table, carrier_mode)
    return {"ready": True, "mode": carrier_mode, "work_date": day.isoformat(), "rows": rows,
            "restate": [], "sale_lines": len(lines), "source_table": source_table,
            "read_error": False, "note": None}


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
    basis = cfg.get("tier_basis") or "none"
    tier_as_computed = (basis == "as_computed")
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
                # the two numbers the MTD basis aims at: what these lines pay WITHOUT the tier
                # multiplier, and what they pay WITH the attainment these very lines produce (which,
                # over a month-to-date window, IS the rep report's month-to-date commission).
                "untiered_total": _round(base + tiered + setup),
                "tiered_total": _round(base + tiered * mult + setup),
                "qualifying_units": r.get("qualifying_units"),
                "day_tier_multiplier": mult,
                "tier_basis": cfg.get("tier_basis"),
                "rules": [{"label": rb.get("label"), "payout_kind": rb.get("payout_kind"),
                           "matched_lines": rb.get("matched_lines"),
                           "qualifying_units": rb.get("qualifying_units"),
                           "tiered": rb.get("tiered"), "payout": _round(rb.get("payout"))}
                          for rb in (r.get("rules") or [])],
                "deferred_to_monthly": ([] if basis in ("as_computed", "mtd_attained")
                                        else ["plan_tier_multiplier"]),
                "explain": _basis_explain(basis, "plan"),
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

    basis = cfg.get("tier_basis") or "none"
    tier_as_computed = (basis == "as_computed")
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
                # Boost: the KPI tier multiplier comes from the MONTHLY DLAR, which a sales-only window
                # cannot know, so the month-to-date target is the un-tiered subtotal for BOTH numbers.
                # The KPI tier stays deferred to the monthly true-up under every basis (see explain).
                "untiered_total": _round(subtotal),
                "tiered_total": _round(subtotal),
                "tier_basis": cfg.get("tier_basis"),
                "deferred_to_monthly": (["trade_in_spiff"] if tier_as_computed
                                        else ["kpi_tier", "trade_in_spiff"]),
                "explain": _basis_explain(basis, "boost"),
            },
        })
    return out


# ── the 'mtd_attained' basis (ledger Q18) ────────────────────────────────────────────────────────
def _basis_explain(basis, mode):
    """The plain-language sentence stored on every accrual row. It is SHOWN TO REPS, so it has to say
    what the number is, not merely assert it."""
    if basis == "mtd_attained":
        if mode == "boost":
            return ("This day's share of the month-to-date commission at the tier the rep is meeting "
                    "TODAY. The KPI tier comes from the MONTHLY DLAR and the trade-in spiff from the "
                    "MONTHLY ePay payment detail — neither is knowable from sales alone, so both still "
                    "arrive in the monthly true-up. The month restates as attainment moves.")
        return ("This day's share of the month-to-date commission at the tier the rep is meeting TODAY. "
                "The month-to-date total is computed from this month's own sale lines with the real tier "
                "attainment applied and shared across the month's accrued days, so the days add up to "
                "what the rep report shows today — and the whole month restates when attainment moves.")
    if basis == "as_computed":
        return ("Includes this day's own tier multiplier (tenant setting tier_basis='as_computed').")
    if mode == "boost":
        return ("Un-tiered day total. The KPI tier comes from the MONTHLY DLAR and the trade-in spiff "
                "from the MONTHLY ePay payment detail — neither is knowable from one day's sales, so "
                "both arrive in the monthly true-up.")
    return ("Un-tiered day total: the plan's tier multiplier is a MONTHLY attainment and is recognized "
            "once, later, as the monthly true-up.")


def _row_weight(row):
    """The ALLOCATION WEIGHT of a stored accrual row: the day's own UN-TIERED commission.

    Read from components.mtd.untiered_base when the row has already been allocated, else from
    base_amount (which is exactly the un-tiered figure for a row written under the 'none' basis — so a
    tenant switching basis mid-month re-weights correctly on the first run). Reading the weight rather
    than the already-scaled amount is what makes a re-run IDEMPOTENT: scaling never compounds."""
    comp = row.get("components") or {}
    if isinstance(comp, dict):
        m = comp.get("mtd")
        if isinstance(m, dict) and m.get("untiered_base") is not None:
            return safe_float(m.get("untiered_base"))
    return safe_float(row.get("base_amount"))


def _allocate(target, entries):
    """Split `target` across `entries` (each {weight}) in proportion to weight, EXACTLY. PURE.

    The last entry absorbs the rounding residue, so the allocated amounts always sum to `target` to the
    cent — the whole point of this basis is that the days add up to the rep report's month-to-date
    number, and a half-cent of float drift would break that claim on every export."""
    if not entries:
        return []
    total = sum(safe_float(e["weight"]) for e in entries)
    out = []
    if total <= 0:
        # No un-tiered weight anywhere (e.g. every rule paid $0 but a flat month-to-date figure exists).
        # Put it all on the LAST entry rather than inventing a split.
        for e in entries[:-1]:
            out.append(0.0)
        out.append(_round(target))
        return out
    run = 0.0
    for e in entries[:-1]:
        v = _round(safe_float(e["weight"]) / total * target)
        run = _round(run + v)
        out.append(v)
    out.append(_round(target - run))
    return out


def mtd_allocate(client, org_id, day, cfg, carrier_mode):
    """The DEFAULT basis (owner 2026-08-04, ledger Q18): accrue each day at the tier the rep is MEETING.

    Owner, verbatim: "it will be based on tier meeting on that day, it keeps varying throughout the
    month as their commission changes in the individual rep report."

    HOW, and why this shape:
      1. Read this month's sale lines ONCE, from the 1st through `edge` (= the later of `day` and the
         last day already accrued this month, capped at month end).
      2. Run the tenant's REAL pay logic over that whole window. For a plan-mode tenant that is
         commission_engine.preview() — the same function the monthly payout runs through — so the
         window total IS what the individual rep report shows for the month to date, tier and all.
      3. Run the same logic over `day`'s lines alone to get the day's UN-TIERED commission. That is the
         allocation WEIGHT, not the answer.
      4. Share the month-to-date total across every accrued day of the month in proportion to those
         weights, to the cent.

    Therefore SUM(accruals month-to-date) == the rep report's month-to-date commission, by construction,
    and when attainment moves mid-month EVERY day of the current month restates (that is the "keeps
    varying" the owner described — a rep who crosses into 2.0x sees the whole month lift, not just the
    day they crossed).

    IDEMPOTENT: the weights are read from `components.mtd.untiered_base`, never from the already-scaled
    amount, so re-running a date recomputes the identical split instead of compounding it. It is also
    ORDER-INDEPENDENT — the allocation is a pure function of the month's sale lines plus the stored
    weights.

    STILL NOT PAY. Every number here is expected/probable; nothing is written to rep_commissions, and
    the monthly true-up still reconciles whatever residual remains at month close (for Boost that is
    always the KPI tier + trade-in spiff, which sales alone cannot know)."""
    m_first, m_last = month_bounds(day)
    period = period_label(day)
    empty = {"ready": True, "mode": carrier_mode, "work_date": day.isoformat(), "rows": [],
             "restate": [], "sale_lines": 0, "source_table": None, "read_error": False,
             "tier_basis": "mtd_attained"}

    try:
        stored = _page_accruals(client, org_id, m_last, since=m_first)
    except Exception as e:
        if not _table_missing(e):
            raise
        stored = []                       # pre-migration: degrade to "this day only"

    edge = day
    for r in stored:
        wd = parse_day(r.get("work_date"), None)
        if wd and wd > edge:
            edge = wd
    edge = min(edge, m_last)

    window, source_table, read_error = read_sales_range(client, org_id, m_first, edge)
    if read_error:
        out = dict(empty)
        out.update({"read_error": True,
                    "note": ("neither raw_sales nor daily_sales_feed could be read for this month — "
                             "nothing was computed and nothing will be written")})
        return out

    smap = store_code_map(client, org_id)
    day_lines = [r for r in window if day_of(r) == day]
    day_rows = (_engine_rows(client, org_id, period, day_lines, cfg, smap, source_table, carrier_mode)
                if day_lines else [])
    mtd_rows = (_engine_rows(client, org_id, period, window, cfg, smap, source_table, carrier_mode)
                if window else [])

    target, untiered, meta = {}, {}, {}
    for r in mtd_rows:
        k = r["employee_key"]
        c = r.get("components") or {}
        target[k] = _round(target.get(k, 0.0) + safe_float(c.get("tiered_total", r.get("base_amount"))))
        untiered[k] = _round(untiered.get(k, 0.0) + safe_float(c.get("untiered_total", r.get("base_amount"))))
        meta.setdefault(k, {"name": r.get("employee_name"), "store_code": r.get("store_code"),
                            "store_raw": r.get("store_raw"), "multiplier": (c or {}).get("day_tier_multiplier")})

    # ── build the allocation entries: this day's FRESH rows + every OTHER stored day of the month ──
    entries = {}
    for r in day_rows:
        entries.setdefault(r["employee_key"], []).append(
            {"kind": "day", "work_date": day.isoformat(), "store_code": r.get("store_code") or "",
             "weight": safe_float(r.get("base_amount")), "row": r})
    iso_day = day.isoformat()
    for r in stored:
        wd = str(r.get("work_date") or "")[:10]
        if wd == iso_day:
            continue                       # this day is being recomputed from scratch above
        k = r.get("employee_key") or ""
        if not k:
            continue
        entries.setdefault(k, []).append(
            {"kind": "stored", "work_date": wd, "store_code": str(r.get("store_code") or ""),
             "weight": _row_weight(r), "stored": r})

    # an employee the month-to-date pass pays but with no accrued day at all (their days were never
    # accrued): give them a row on `day` rather than losing the money, and say so in words.
    for k, amt in target.items():
        if k in entries or abs(amt) < 0.005:
            continue
        info = meta.get(k) or {}
        code = info.get("store_code") or resolve_store_code(info.get("store_raw"), smap)
        row = {"employee_key": k, "employee_name": info.get("name") or k, "store_code": code or "",
               "store_raw": info.get("store_raw") or "", "base_amount": 0.0,
               "components": {"mode": "mtd_only", "source_table": source_table,
                              "explain": ("No accrued day carries this rep's weight yet, so the whole "
                                          "month-to-date figure is shown here.")}}
        day_rows.append(row)
        entries.setdefault(k, []).append(
            {"kind": "day", "work_date": iso_day, "store_code": code or "", "weight": 0.0,
             "row": row, "no_weights": True})

    restate = []
    for k, items in entries.items():
        items.sort(key=lambda x: (x["work_date"], x["store_code"]))
        tgt = safe_float(target.get(k, 0.0))
        unt = safe_float(untiered.get(k, 0.0))
        total_w = _round(sum(safe_float(i["weight"]) for i in items))
        factor = round(tgt / total_w, 6) if total_w else None
        amounts = _allocate(tgt, items)
        for it, amt in zip(items, amounts):
            mtd_block = {
                "basis": "mtd_attained", "period": period,
                "untiered_base": _round(it["weight"]),
                "factor": factor, "mtd_total": _round(tgt), "mtd_untiered": _round(unt),
                "mtd_through": edge.isoformat(), "allocated": _round(amt),
                "days_in_allocation": len(items),
                "no_daily_weights": bool(it.get("no_weights")) or (total_w == 0 and tgt != 0),
                "explain": (f"Month-to-date ({period}, through {edge.isoformat()}) this rep's commission "
                            f"is ${_round(tgt):,.2f} at the tier they are meeting now"
                            + (f" (x{factor:.4g} on ${_round(unt):,.2f} un-tiered)" if factor else "")
                            + f"; this day's ${_round(it['weight']):,.2f} of un-tiered commission is "
                              f"{'its share' if total_w else 'carrying the whole figure'} of it, "
                              f"${_round(amt):,.2f}. The month restates as attainment moves."),
            }
            if it["kind"] == "day":
                row = it["row"]
                row["base_amount"] = _round(amt)
                comp = row.setdefault("components", {})
                comp["mtd"] = mtd_block
                comp["tier_basis"] = "mtd_attained"
            else:
                s = it["stored"]
                if abs(safe_float(s.get("base_amount")) - _round(amt)) < 0.005 and \
                        isinstance((s.get("components") or {}).get("mtd"), dict) and \
                        (s["components"]["mtd"] or {}).get("mtd_total") == _round(tgt):
                    continue               # already correct — do not rewrite an unchanged row
                comp = dict(s.get("components") or {})
                comp["mtd"] = mtd_block
                comp["tier_basis"] = "mtd_attained"
                tier = _round(s.get("tier_amount"))
                restate.append({
                    "work_date": it["work_date"], "employee_key": k,
                    "store_code": it["store_code"], "employee_name": s.get("employee_name"),
                    "base_amount": _round(amt), "tier_amount": tier,
                    "total_amount": _round(_round(amt) + tier), "components": comp,
                })

    note = None
    if not day_lines:
        note = "no sale lines for this date (nothing accrued — not an error)"
    return {"ready": True, "mode": carrier_mode, "work_date": day.isoformat(),
            "rows": day_rows, "restate": restate, "sale_lines": len(day_lines),
            "source_table": source_table, "read_error": False, "tier_basis": "mtd_attained",
            "mtd_through": edge.isoformat(), "mtd_window_lines": len(window),
            "mtd_totals": {k: _round(v) for k, v in target.items()}, "note": note}


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

    # RESTATEMENT (tier_basis='mtd_attained', ledger Q18). When attainment moves, the OTHER days of the
    # current month are re-allocated so the month still adds up to the rep report's month-to-date
    # number. These rows carry their own (already recognized) tier_amount untouched — only the allocated
    # base moves, and only inside the month `day` belongs to.
    restated = []
    for r in (day_res.get("restate") or []):
        restated.append({
            "org_id": org_id,                       # RULE ONE write side, on the restatement too
            "work_date": r["work_date"], "employee_key": r["employee_key"],
            "store_code": r.get("store_code") or "", "employee_name": r.get("employee_name"),
            "base_amount": _round(r.get("base_amount")), "tier_amount": _round(r.get("tier_amount")),
            "total_amount": _round(r.get("total_amount")), "components": r.get("components") or {},
            "computed_at": now_iso,
        })

    try:
        if payload:
            for i in range(0, len(payload), 500):
                (client.schema("commcalc").table(ACCRUAL_TABLE)
                 .upsert(payload[i:i + 500],
                         on_conflict="org_id,work_date,employee_key,store_code").execute())
        for i in range(0, len(restated), 500):
            (client.schema("commcalc").table(ACCRUAL_TABLE)
             .upsert(restated[i:i + 500],
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
            "restated": len(restated), "tier_basis": cfg.get("tier_basis"),
            "mtd_through": day_res.get("mtd_through"),
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


def accrued(client, org_id, as_of, employee_key=None, store_code=None, keyset=None, since=None,
            cfg=None):
    """GET /commcalc/payout/accrued — per-employee accrued / advanced / balance for the CURRENT CYCLE.

    Shape is FIXED by docs/specs/envelope-expense-payout.md (retail-ops' payout-due endpoint consumes
    it): {employees:[{employee_key, name, store_codes[], accrued_total, paid_total, unpaid_balance,
    today_accrual, components:{base,tier}}], as_of}. Additional keys (cycle, carry-over, due_now, lines,
    flags, counts, note) are additive and safe for that consumer to ignore.

    ── PER-CYCLE BALANCES (owner 2026-08-04, ledger Q19) ──────────────────────────────────────────
    Owner, verbatim: "reset each month and advise the user to clear the employee balance at the end of
    the month / payroll cycle / commission cycle as defined in the system; also the cash can carry over
    to next month as it might or might not be picked up."

    So `accrued_total` / `paid_total` / `unpaid_balance` are THIS CYCLE's figures (cycle = calendar
    month by default, or the tenant's payroll / commission cycle — see cycle_bounds). Anything left
    unsettled from an earlier cycle is NOT swept under the rug and NOT silently rolled in: it is a
    labelled `carry_over` line, visible on every surface, and settling it is a human decision (the
    settlement checklist). Lifetime figures are still returned as `lifetime_*` for anyone who wants the
    running total. NOTE: envelope CASH physically carrying to the next month is retail-ops' cash
    position, not this stream — nothing here moves or hides it.

    ── OVER-ADVANCE (ledger Q14) ─────────────────────────────────────────────────────────────────
    Owner: "flag it and keep an option to auto net". Default `over_advance_mode='flag'` — the
    over-advance is flagged and NOTHING is netted. With `'auto_net'`, a PRIOR cycle's over-advance
    reduces this cycle's `due_now`, and the reduction is its OWN labelled line in `lines[]` ("Less:
    prior-cycle over-advance applied") so it can never happen silently. Under either mode this writes
    nothing, changes no accrual and never touches rep_commissions: it nets the ENVELOPE-ADVANCE balance
    stream only, and what the rep is actually owed at month end is unaffected."""
    cfg = cfg if cfg is not None else load_config(client, org_id)
    c_start, c_end, c_label, c_mode = cycle_bounds(as_of, cfg)
    mode = (cfg.get("over_advance_mode") or "flag")
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
                          "last_accrual_date": None,
                          "prior_accrued": 0.0, "prior_paid": 0.0,
                          "lifetime_accrued": 0.0, "lifetime_paid": 0.0}
        if name and (e["name"] == k or not e["name"]):
            e["name"] = name
        return e

    iso = as_of.isoformat()
    c_start_iso = c_start.isoformat()
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
        amt = safe_float(r.get("total_amount"))
        wd = str(r.get("work_date") or "")[:10]
        e["lifetime_accrued"] = _round(e["lifetime_accrued"] + amt)
        if wd >= c_start_iso:
            e["accrued_total"] = _round(e["accrued_total"] + amt)
            e["components"]["base"] = _round(e["components"]["base"] + safe_float(r.get("base_amount")))
            e["components"]["tier"] = _round(e["components"]["tier"] + safe_float(r.get("tier_amount")))
            e["accrual_days"] += 1
        else:
            e["prior_accrued"] = _round(e["prior_accrued"] + amt)
        if wd == iso:
            e["today_accrual"] = _round(e["today_accrual"] + amt)
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
        amt = safe_float(r.get("amount"))
        pd = str(r.get("paid_date") or "")[:10]
        e["lifetime_paid"] = _round(e["lifetime_paid"] + amt)
        if pd >= c_start_iso:
            e["paid_total"] = _round(e["paid_total"] + amt)
            e["payout_count"] += 1
        else:
            e["prior_paid"] = _round(e["prior_paid"] + amt)
        if pd and (e["last_paid_date"] is None or pd > e["last_paid_date"]):
            e["last_paid_date"] = pd
        if code and code not in e["store_codes"]:
            e["store_codes"].append(code)

    prior_label = previous_cycle(c_start, cfg)[2]
    out = []
    for e in emp.values():
        e["unpaid_balance"] = _round(e["accrued_total"] - e["paid_total"])
        e["lifetime_balance"] = _round(e["lifetime_accrued"] - e["lifetime_paid"])
        e["carry_over"] = _round(e["prior_accrued"] - e["prior_paid"])
        e["over_advanced"] = bool(e["paid_total"] - e["accrued_total"] > 0.005)
        e["over_advance_amount"] = _round(max(0.0, e["paid_total"] - e["accrued_total"]))
        e["over_advanced_lifetime"] = bool(-e["lifetime_balance"] > 0.005)
        e["over_advance_amount_lifetime"] = _round(max(0.0, -e["lifetime_balance"]))
        # ── due now, and the netting decision (ledger Q14) ────────────────────────────────────────
        due_before = _round(max(0.0, e["unpaid_balance"]))
        prior_over = _round(max(0.0, -e["carry_over"]))
        applied = _round(min(due_before, prior_over)) if mode == "auto_net" else 0.0
        e["prior_over_advance"] = prior_over
        e["net_applied"] = applied
        e["due_now"] = _round(due_before - applied)
        e["over_advance_mode"] = mode
        lines = [{"label": f"Accrued this cycle ({c_label}) — expected commission",
                  "kind": "accrued", "amount": e["accrued_total"], "affects_due": True},
                 {"label": "Cash advanced this cycle",
                  "kind": "advance", "amount": _round(-e["paid_total"]), "affects_due": True}]
        if abs(e["carry_over"]) >= 0.005:
            lines.append({
                "kind": "carry_over", "amount": e["carry_over"], "affects_due": False,
                "label": (f"Carry-over from {prior_label} and earlier — "
                          + ("unsettled balance still owed to the rep"
                             if e["carry_over"] > 0 else "cash advanced beyond what was accrued")
                          + " (not settled; shown so it is never hidden)")})
        if applied:
            lines.append({
                "kind": "net", "amount": _round(-applied), "affects_due": True,
                "label": (f"Less: prior-cycle over-advance applied (auto-net) — ${applied:,.2f} of the "
                          f"{prior_label} over-advance is recovered from this cycle's cash")})
        lines.append({"label": "Due now (cash this cycle)", "kind": "due", "amount": e["due_now"],
                      "affects_due": False})
        e["lines"] = lines
        e["store_codes"].sort()
        out.append(e)
    out.sort(key=lambda x: -(x.get("due_now") or 0))
    flagged = [e for e in out if e["over_advanced"] or e["over_advanced_lifetime"]]
    days_left = (c_end - as_of).days
    advise_in = int(((cfg.get("cycle") or {}).get("settlement_advice_days")
                     if isinstance(cfg.get("cycle"), dict) else None) or 0)
    unsettled = [e for e in out if abs(e["carry_over"]) >= 0.005]
    advisory_due = bool(unsettled) or (0 <= days_left <= advise_in)
    return {
        "ready": True, "as_of": as_of.isoformat(), "employees": out,
        "cycle": {"start": c_start.isoformat(), "end": c_end.isoformat(), "label": c_label,
                  "mode": c_mode, "days_left": days_left,
                  "previous_label": prior_label},
        "over_advance_mode": mode,
        "tier_basis": cfg.get("tier_basis"),
        "settlement_advisory": {
            "due": advisory_due,
            "employees_with_carry_over": len(unsettled),
            "message": (
                (f"{len(unsettled)} employee(s) still carry an unsettled balance from a previous cycle."
                 if unsettled else "")
                + ((" " if unsettled else "")
                   + f"This cycle ({c_label}) ends in {days_left} day(s) — settle employee balances "
                     f"before it closes." if 0 <= days_left <= advise_in else "")
            ).strip() or None},
        "totals": {"accrued": _round(sum(e["accrued_total"] for e in out)),
                   "paid": _round(sum(e["paid_total"] for e in out)),
                   "unpaid": _round(sum(e["unpaid_balance"] for e in out)),
                   "due_now": _round(sum(e["due_now"] for e in out)),
                   "carry_over": _round(sum(e["carry_over"] for e in out)),
                   "net_applied": _round(sum(e["net_applied"] for e in out)),
                   "lifetime_balance": _round(sum(e["lifetime_balance"] for e in out)),
                   "today": _round(sum(e["today_accrual"] for e in out)),
                   "employees": len(out)},
        "over_advanced": len(flagged),
        # CROSS-MODULE CONTRACT NOTE (retail-ops' /closing/payout-due consumes this shape): the cash
        # figure to advance is `due_now`, NOT `unpaid_balance`. `unpaid_balance` stays the honest
        # arithmetic (this cycle's accrued minus this cycle's advances, negative when over-advanced);
        # `due_now` is that floored at zero AND, under over_advance_mode='auto_net', reduced by a prior
        # cycle's over-advance. A consumer still reading `unpaid_balance` behaves exactly as before for
        # a 'flag' tenant (the default) and merely fails to recover the netting for an 'auto_net' one —
        # it can never advance more than the cycle balance.
        "payable_field": "due_now",
        "consumer_note": ("Use `due_now` for the cash to hand over: it is floored at zero and already "
                          "net of any prior-cycle over-advance the tenant asked to auto-net. "
                          "`unpaid_balance` is the raw cycle arithmetic and can be negative."),
        "note": ("Accrued figures are EXPECTED (probable) commission, not pay. Paid figures are cash "
                 "ADVANCES against them. Balances are per CYCLE and reset each cycle; an unsettled "
                 "prior-cycle balance is shown as a labelled carry-over line, never hidden. Nothing "
                 "here changes what anyone is owed."),
    }


def settlement(client, org_id, as_of, keyset=None, cfg=None):
    """GET /commcalc/payout/settlement — the END-OF-CYCLE "settle employee balances" CHECKLIST.

    Owner 2026-08-04 (ledger Q19): "advise the user to clear the employee balance at the end of the
    month / payroll cycle / commission cycle as defined in the system".

    ADVISORY ONLY. It moves no money, writes nothing and settles nothing — it lists, per employee and
    per cycle, what was accrued, what cash was advanced, and the remainder to pay or collect, so a human
    can clear it deliberately. Prior cycles that were never settled stay on the list as carry-over."""
    try:
        arows = _page_accruals(client, org_id, as_of)
        lrows = _page_ledger(client, org_id, as_of)
    except Exception as e:
        if _table_missing(e):
            return {"ready": False, "as_of": as_of.isoformat(), "employees": [], "note": _MISSING_NOTE}
        raise
    cfg = cfg if cfg is not None else load_config(client, org_id)
    back = int(((cfg.get("cycle") or {}).get("carry_cycles")
                if isinstance(cfg.get("cycle"), dict) else 3) or 0)
    series = cycle_series(as_of, cfg, back)
    cur = series[-1]
    # index cycles by start so a row is bucketed by comparing dates only (no repeated cycle math)
    def _bucket(d):
        for i, (s, e, _l, _m) in enumerate(series):
            if s <= d <= e:
                return i
        return -1 if d < series[0][0] else None

    emp = {}

    def _slot(k, name=None):
        e = emp.get(k)
        if not e:
            e = emp[k] = {"employee_key": k, "name": name or k, "store_codes": [],
                          "cycles": [{"label": l, "start": s.isoformat(), "end": en.isoformat(),
                                      "accrued": 0.0, "advanced": 0.0, "remainder": 0.0,
                                      "is_current": (i == len(series) - 1)}
                                     for i, (s, en, l, _m) in enumerate(series)],
                          "older": {"label": f"before {series[0][2]}", "accrued": 0.0,
                                    "advanced": 0.0, "remainder": 0.0}}
        if name and (e["name"] == k or not e["name"]):
            e["name"] = name
        return e

    for r in arows:
        k = r.get("employee_key") or ""
        code = str(r.get("store_code") or "").strip()
        if not k or (keyset is not None and code and not _in_keys(keyset, code)):
            continue
        d = parse_day(r.get("work_date"), None)
        if not d:
            continue
        e = _slot(k, r.get("employee_name"))
        b = _bucket(d)
        tgt = e["older"] if b == -1 else (e["cycles"][b] if isinstance(b, int) else None)
        if tgt is None:
            continue
        tgt["accrued"] = _round(tgt["accrued"] + safe_float(r.get("total_amount")))
        if code and code not in e["store_codes"]:
            e["store_codes"].append(code)

    for r in lrows:
        k = r.get("employee_key") or ""
        code = str(r.get("store_code") or "").strip()
        if not k or (keyset is not None and code and not _in_keys(keyset, code)):
            continue
        d = parse_day(r.get("paid_date"), None)
        if not d:
            continue
        e = _slot(k, r.get("employee_name"))
        b = _bucket(d)
        tgt = e["older"] if b == -1 else (e["cycles"][b] if isinstance(b, int) else None)
        if tgt is None:
            continue
        tgt["advanced"] = _round(tgt["advanced"] + safe_float(r.get("amount")))
        if code and code not in e["store_codes"]:
            e["store_codes"].append(code)

    out = []
    for e in emp.values():
        for c in e["cycles"] + [e["older"]]:
            c["remainder"] = _round(c["accrued"] - c["advanced"])
        cur_c = e["cycles"][-1]
        carry = _round(sum(c["remainder"] for c in e["cycles"][:-1]) + e["older"]["remainder"])
        e["store_codes"].sort()
        e.update({
            "cycle_label": cur_c["label"],
            "cycle_accrued": cur_c["accrued"], "cycle_advanced": cur_c["advanced"],
            "cycle_remainder": cur_c["remainder"],
            "carry_over": carry,
            "to_pay": _round(max(0.0, cur_c["remainder"] + max(0.0, carry))),
            "to_collect": _round(max(0.0, -(cur_c["remainder"] + min(0.0, carry)))),
            "status": ("settled" if abs(cur_c["remainder"]) < 0.005 and abs(carry) < 0.005
                       else ("owed to employee" if cur_c["remainder"] + carry > 0 else "over-advanced")),
            "unsettled_prior": bool(abs(carry) >= 0.005),
        })
        out.append(e)
    out.sort(key=lambda x: -(abs(x["cycle_remainder"]) + abs(x["carry_over"])))
    days_left = (cur[1] - as_of).days
    advise_in = int(((cfg.get("cycle") or {}).get("settlement_advice_days")
                     if isinstance(cfg.get("cycle"), dict) else 0) or 0)
    return {
        "ready": True, "as_of": as_of.isoformat(),
        "cycle": {"start": cur[0].isoformat(), "end": cur[1].isoformat(), "label": cur[2],
                  "mode": cur[3], "days_left": days_left},
        "cycles": [{"label": l, "start": s.isoformat(), "end": e2.isoformat()}
                   for (s, e2, l, _m) in series],
        "employees": out,
        "totals": {"cycle_accrued": _round(sum(e["cycle_accrued"] for e in out)),
                   "cycle_advanced": _round(sum(e["cycle_advanced"] for e in out)),
                   "cycle_remainder": _round(sum(e["cycle_remainder"] for e in out)),
                   "carry_over": _round(sum(e["carry_over"] for e in out)),
                   "employees": len(out),
                   "unsettled": len([e for e in out if e["status"] != "settled"])},
        "advisory": {
            "due": bool([e for e in out if e["status"] != "settled"]) and (
                days_left <= advise_in or any(e["unsettled_prior"] for e in out)),
            "message": (f"{cur[2]} ends {cur[1].isoformat()} ({days_left} day(s)). Settle each "
                        f"employee's balance: pay the remainder in cash, or record what you collected "
                        f"back. Nothing is settled automatically — this list is advice, not a payment."),
        },
        "note": ("Advisory only: no money moves from this view and nothing here changes anyone's pay. "
                 "Envelope cash left uncollected is retail-ops' cash position — it may legitimately "
                 "carry to the next month; an unsettled BALANCE is what this list tracks."),
    }


def _in_keys(keyset, *vals):
    if keyset is None:
        return True
    return any(str(v or "").strip().upper() in keyset for v in vals if v)


def over_advance_review(client, org_id, as_of, keyset=None, lookback_months=3, cfg=None):
    """The review list for advances that outran the accrual. ALWAYS FLAGGED — never a clawback.

    Owner 2026-08-04 (ledger Q14): "flag it and keep an option to auto net". Flagging is unconditional
    and is what this endpoint is; the tenant's `over_advance_mode` decides only whether a PRIOR cycle's
    over-advance is additionally netted off the NEXT cycle's cash due (shown as its own labelled line on
    /payout/accrued and the settlement view). Neither mode writes rep_commissions, reduces an accrual or
    changes earned pay.

    Three independent questions, all answered:
      running — LIFETIME advances exceed lifetime accrual as of `as_of`;
      cycle   — the CURRENT cycle's advances exceed the current cycle's accrual (the one a DM acts on);
      monthly — for a month whose commission run is FINISHED, cash advanced within that month exceeds
                what that month actually paid. This is the one an owner cares about: it means real
                cash left the envelope against commission that did not materialize."""
    cfg = cfg if cfg is not None else load_config(client, org_id)
    mode = cfg.get("over_advance_mode") or "flag"
    acc = accrued(client, org_id, as_of, keyset=keyset, cfg=cfg)
    if not acc.get("ready"):
        return {"ready": False, "as_of": as_of.isoformat(), "running": [], "cycle": [], "monthly": [],
                "note": _MISSING_NOTE}
    running = [{"employee_key": e["employee_key"], "name": e["name"], "store_codes": e["store_codes"],
                "accrued_total": e["lifetime_accrued"], "paid_total": e["lifetime_paid"],
                "over_by": e["over_advance_amount_lifetime"],
                "reason": "lifetime cash advances exceed lifetime accrued commission"}
               for e in acc["employees"] if e["over_advanced_lifetime"]]
    cyc = [{"employee_key": e["employee_key"], "name": e["name"], "store_codes": e["store_codes"],
            "period": acc["cycle"]["label"],
            "accrued_total": e["accrued_total"], "paid_total": e["paid_total"],
            "over_by": e["over_advance_amount"], "net_applied": e["net_applied"],
            "reason": (f"cash advanced in {acc['cycle']['label']} exceeds what has accrued in it"
                       + (f"; ${e['net_applied']:,.2f} is being auto-netted off this cycle's cash due"
                          if e["net_applied"] else ""))}
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
    return {"ready": True, "as_of": as_of.isoformat(), "running": running, "cycle": cyc,
            "monthly": monthly, "over_advance_mode": mode, "cycle_window": acc.get("cycle"),
            "counts": {"running": len(running), "cycle": len(cyc), "monthly": len(monthly)},
            "policy": (("Flag only — no clawback and no netting. Correcting an over-advance is a human "
                        "decision (ledger Q14 default).") if mode != "auto_net" else
                       ("Flagged AND auto-netted: a prior cycle's over-advance is deducted from the "
                        "employee's next cash due, shown as its own line. No clawback from payroll, no "
                        "change to the accrual and nothing written to what anyone is paid."))}


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


def may_record(caller, cfg=None):
    """(allowed, reason) — may this resolved caller RECORD a cash advance? PURE (no I/O).

    Owner 2026-08-04, ledger Q17: "dm or higher". `caller` is core.router._resolve_caller's shape
    ({role, super_admin, perms}) — the same resolution every other management gate in this module uses.
    A STORE manager is deliberately excluded: handing envelope cash to a rep is a district-level call.

    Allowed when the caller is a super-admin, OR their role is in the tenant's `record_roles`
    (default DEFAULT_RECORD_ROLES), OR their RBAC scope already spans more than one store
    ('all' / 'market') — that last clause is what lets a tenant's CUSTOM district-level role work
    without anyone hard-coding its name. `caller is None` (RBAC off / unresolvable token) degrades OPEN,
    the same posture as _require_commission_admin, so the house org can never be locked out of its own
    envelope."""
    if caller is None:
        return True, "caller could not be resolved (RBAC off) — same open posture as the rest of the module"
    if caller.get("super_admin"):
        return True, "super admin"
    roles = [str(r).strip().lower() for r in ((cfg or {}).get("record_roles") or DEFAULT_RECORD_ROLES)]
    role = str(caller.get("role") or "").strip().lower()
    if role in roles:
        return True, f"role '{role}' is permitted to record cash advances"
    scope = str(((caller.get("perms") or {}).get("scope") or "")).strip().lower()
    if scope in ("all", "market"):
        return True, f"role '{role}' spans {scope} stores (district level or above)"
    return False, ("Recording a cash advance against commission is a district-manager-or-above action. "
                   f"'{caller.get('role') or 'this role'}' covers a single store or less. Ask a DM "
                   "(or an admin) to record it.")


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


def _recently_computed(client, org_id, day, minutes):
    """True when this (org, date) was accrued less than `minutes` ago. Throttles the SWEEP only —
    `POST /payout/accrual/run` never consults it, because a human pressing the button is asking for a
    recompute NOW. Fails OPEN (returns False) so a read error can only cause an extra run, never a
    silently skipped one."""
    if not minutes:
        return False
    try:
        rows = (client.schema("commcalc").table(ACCRUAL_TABLE).select("computed_at")
                .eq("org_id", org_id).eq("work_date", day.isoformat())
                .order("computed_at", desc=True).limit(1).execute().data) or []
    except Exception:
        return False
    if not rows:
        return False
    try:
        ts = str(rows[0].get("computed_at") or "").replace("Z", "+00:00")
        last = _datetime.fromisoformat(ts)
        if last.tzinfo is None:
            last = last.replace(tzinfo=_timezone.utc)
    except Exception:
        return False
    return (_datetime.now(_timezone.utc) - last) < _timedelta(minutes=minutes)


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
            mins = int((cfg.get("auto_run") or {}).get("min_interval_minutes", 50))
            days = dates or [today - _timedelta(days=i) for i in range(back, -1, -1)]
            for d in days:
                if mins and _recently_computed(client, oid, d, mins):
                    out.append({"org_id": oid, "date": d.isoformat(),
                                "skipped": f"already accrued within {mins} min"})
                    continue
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
