"""Lunch-break auto-deduction (mod-people, owner directive 2026-07-27, Deliverable 3):

"there should be option to deduct the lunch break for all by 30 minutes by default but configurable
and assigned to each user as needed, it is a user based provision universally for all tenants."

RULE TWO (SAP-configurable): a TENANT-LEVEL default {enabled, minutes, min_shift_hours} +
a PER-EMPLOYEE override {enabled, minutes} (migration 418, storeops.tenants / storeops.employees).
OWNER'S STATED DEFAULT, built exactly as instructed: every tenant starts ON at 30 minutes, applied to
shifts of 6+ worked hours — adjustable per tenant and per employee (including fully disabling it for
one person). `min_shift_hours` is tenant-only; the owner only asked for a per-employee override on
enabled/minutes.

DOUBLE-DEDUCTION GUARD (mandatory, money-adjacent — read before changing):
A day's eligibility is decided from its CLOSED storeops.timelog punch-pairs for that (employee,
work_date) ONLY — an auto deduction is never based on a schedule-only fallback (there is no clock data
to show whether a break was actually taken on a day nobody clocked in).

  1. Any punch that day still OPEN (no clock_out yet) → skip, `open_punch_present`. We can't yet know
     whether a second, gapped pair is coming (which would make this a real-break/split-shift day) —
     deferring is safer than deducting now and being wrong once it closes.
  2. Otherwise, order the closed pairs by clock_in and look at the gap between each pair's clock_out
     and the next pair's clock_in:
       - a SINGLE pair, or multiple pairs whose gaps are all <= LUNCH_GAP_EPSILON_MINUTES (a tiny
         system artifact — e.g. a force-clockout immediately re-clocked-in — not a real break) are
         treated as ONE continuous worked block, hours summed.
       - ANY larger (or negative/unparseable) gap → skip, `real_break_present`. This is the SAME
         outcome for a genuine lunch re-clock-in (the kiosk has supported this since the 2026-07-26
         clock-fix) and for a true split shift (two independent work blocks, e.g. AM/PM) — punch data
         alone cannot tell the two apart, and BOTH already contain real unpaid time between the pairs,
         so subtracting an ADDITIONAL 30 minutes on top would double-count that gap. This is the
         documented, explicit split-shift rule: split shifts are NEVER auto-deducted, by construction
         of the guard, not by a special case. (Owner-confirmable: if a future need arises to deduct
         PER PAIR on a genuine multi-block day, that is a distinct, not-yet-requested design — flagged,
         not built.)
  3. The continuous block's total hours must meet `min_shift_hours` (tenant config, default 6h,
     OWNER-CONFIRMABLE threshold) or skip, `below_threshold`.
  4. `deduct_hours = min(minutes / 60, worked_hours)` — the NEGATIVE-HOURS GUARD: a deduction can never
     exceed the hours it's being subtracted from. Callers apply a SECOND clamp at the point they
     subtract from a report row's own total (defense in depth against any other rounding/edge case).

HONESTY: this module never mutates a punch's own `hours` field — every caller attaches the deduction as
its OWN explicit value (`deduct_hours`) alongside the untouched source hours, so every surface can show
it as a visible "− 0:30 lunch (auto)" line rather than a silent subtraction.

DEGRADE (AGENT_CONTRACT §5): every DB read here is wrapped in try/except. `available=False` (deduct
NOTHING, everywhere) whenever migration 418 hasn't run yet — merging this code changes nothing in prod
until the owner actually runs it. This is intentionally a HARD gate, not a soft fallback to the owner's
stated default: the tenant-config read failing (unknown column) must never be silently treated as
"use the default", or the feature would start deducting hours the moment this code ships, before the
owner has reviewed the blast radius.

SEAM for the salaried-pay branch (parallel, `agent/people/salary-pay-basis`): this module only ever
nets HOURS. `get_payroll()` (hourly path, owned by this package) additionally recomputes
`actual_pay = actual_hours * pay_rate` from the now-netted hours, which is correct for an hourly
employee. A salaried-pay aggregator must call `period_lunch_deduction()`/`compute_lunch_deduction_from_rows()`
itself to net DISPLAYED hours the same way, but must NOT derive salaried pay from `hours * rate` — it
should keep computing pay from its own salary formula, independent of the netted hours number.
"""
from datetime import datetime, timezone

# A gap this small between two closed punch-pairs the same day is treated as a system artifact (e.g. a
# force-clockout immediately re-clocked-in), never a real break — the pairs merge into ONE continuous
# block for the min-shift-hours test. Deliberately small and NOT tenant-configurable (an implementation
# constant, not a product setting the owner asked to expose).
LUNCH_GAP_EPSILON_MINUTES = 1.0

DEFAULT_TENANT_LUNCH_CONFIG = {"enabled": True, "minutes": 30, "min_shift_hours": 6.0}


def _parse_dt(s):
    """Parse an ISO timestamp, always returning a TZ-AWARE datetime (naive -> assumed UTC). Every real
    storeops.timelog clock_in/clock_out is a TIMESTAMPTZ (always arrives tz-aware over PostgREST), so
    this normalization is unreachable in production — but a naive input (e.g. a hand-built test fixture,
    or any future caller) would otherwise raise TypeError the moment it's compared/subtracted against an
    aware one (sort key fallback at `_day_deduction`'s `ordered_all`, or the gap subtraction below),
    which the caller's try/except turns into a fail-safe "skip this day" (pays MORE, never less) rather
    than a crash — normalizing here means that fallback path is never even exercised for this reason."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def get_tenant_lunch_config(org_id, sb_client):
    """(config, available). `available=False` whenever migration 418 hasn't run — `config` is still the
    owner's stated default in that case, for documentation/UI purposes ONLY; callers must never use it
    to actually compute a deduction when `available` is False (see module docstring, DEGRADE).

    Availability signal is the COLUMN'S PRESENCE on the fetched row (`"lunch_deduction_enabled" in t`),
    not just "the select didn't raise". Real PostgREST DOES raise for a genuinely unknown column (the
    pre-migration case, caught below) — but the harness suite's in-memory fake Supabase client is a
    schemaless dict store that never raises for an unrecognized key, so relying on exceptions ALONE
    would make every pre-existing (pre-migration-418) test fixture look "available" the instant this
    module is imported, silently changing dozens of already-proven money numbers. Checking for the
    key's presence models both correctly: a real migrated tenant row always HAS the key (NOT NULL
    DEFAULT backfills it at ALTER TIME — never a bare missing key), while an old fixture's plain
    `{"org_id": ...}` row (or no row at all) has never heard of it, exactly like a real pre-migration
    column. See harness_lunch_deduction.py for the explicit proof."""
    try:
        rows = (sb_client.table("tenants").select(
            "lunch_deduction_enabled,lunch_deduction_minutes,lunch_deduction_min_shift_hours")
            .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return dict(DEFAULT_TENANT_LUNCH_CONFIG), False
    if not rows or "lunch_deduction_enabled" not in rows[0]:
        return dict(DEFAULT_TENANT_LUNCH_CONFIG), False
    t = rows[0]
    cfg = {
        "enabled": bool(t["lunch_deduction_enabled"]) if t.get("lunch_deduction_enabled") is not None else True,
        "minutes": int(t["lunch_deduction_minutes"]) if t.get("lunch_deduction_minutes") is not None else 30,
        "min_shift_hours": float(t["lunch_deduction_min_shift_hours"]) if t.get("lunch_deduction_min_shift_hours") is not None else 6.0,
    }
    return cfg, True


def get_employee_lunch_overrides(org_id, sb_client):
    """({employee_id: {enabled, minutes}}, available). Isolated from the main employees roster query
    (never fetched inline with name/pay_rate/etc.) so a missing migration 418 can NEVER 500 an unrelated
    employee read — see router.py call sites, which all query this separately and independently."""
    try:
        rows = (sb_client.table("employees").select(
            "employee_id,lunch_deduction_enabled,lunch_deduction_minutes")
            .eq("org_id", org_id).execute().data) or []
        out = {}
        for e in rows:
            eid = e.get("employee_id")
            if not eid:
                continue
            out[eid] = {"enabled": e.get("lunch_deduction_enabled"), "minutes": e.get("lunch_deduction_minutes")}
        return out, True
    except Exception:
        return {}, False


def resolve_employee_lunch_settings(tenant_cfg, override):
    """Precedence: a non-NULL per-employee value wins per-field independently; NULL/missing inherits
    the tenant default. Returns (enabled: bool, minutes: int, min_shift_hours: float)."""
    override = override or {}
    enabled = override.get("enabled")
    if enabled is None:
        enabled = tenant_cfg.get("enabled", True)
    minutes = override.get("minutes")
    if minutes is None:
        minutes = tenant_cfg.get("minutes", 30)
    try:
        minutes = max(0, int(minutes))
    except (TypeError, ValueError):
        minutes = 0
    return bool(enabled), minutes, float(tenant_cfg.get("min_shift_hours", 6.0))


def _day_deduction(punches, enabled, minutes, min_shift_hours):
    """One (employee, work_date) group of RAW timelog rows -> the guard's verdict for that day."""
    ordered_all = sorted(punches, key=lambda p: _parse_dt(p.get("clock_in")) or datetime.min.replace(tzinfo=timezone.utc))
    store_code = next((p.get("store_code") for p in reversed(ordered_all) if p.get("store_code")), None)
    out = {"deduct_hours": 0.0, "applied": False, "skip_reason": None, "worked_hours": 0.0,
           "marked_punch_id": None, "store_code": store_code, "minutes_configured": minutes}
    if not enabled:
        out["skip_reason"] = "disabled"
        return out
    if any(not p.get("clock_out") or p.get("hours") is None for p in ordered_all):
        out["skip_reason"] = "open_punch_present"
        return out
    worked = round(sum(float(p.get("hours") or 0) for p in ordered_all), 4)
    out["worked_hours"] = round(worked, 2)
    for prev, nxt in zip(ordered_all, ordered_all[1:]):
        a, b = _parse_dt(prev.get("clock_out")), _parse_dt(nxt.get("clock_in"))
        gap_min = (b - a).total_seconds() / 60.0 if (a and b) else None
        if gap_min is None or gap_min < 0 or gap_min > LUNCH_GAP_EPSILON_MINUTES:
            out["skip_reason"] = "real_break_present"
            return out
    if worked < min_shift_hours:
        out["skip_reason"] = "below_threshold"
        return out
    # NEGATIVE-HOURS GUARD: never deduct more than was actually worked.
    deduct = max(0.0, min(minutes / 60.0, worked))
    out["deduct_hours"] = round(deduct, 2)
    out["applied"] = deduct > 0
    out["marked_punch_id"] = ordered_all[-1].get("id")
    if not out["applied"]:
        out["skip_reason"] = "zero_minutes_configured"
    return out


def compute_lunch_deduction_from_rows(timelog_rows, tenant_cfg, overrides):
    """PURE (no DB): group already-fetched timelog rows by (employee_id, work_date) and run the guard
    per day. Returns {available: True, tenant_config, by_employee: {eid: hours}, by_employee_store:
    {(eid,store): hours}, days: [{employee_id, work_date, store_code, worked_hours, deduct_hours,
    applied, skip_reason, marked_punch_id, minutes_configured}, ...]}."""
    by_day: dict = {}
    for r in timelog_rows or []:
        eid = r.get("employee_id")
        wd = str(r.get("work_date") or "")[:10]
        if not eid or not wd:
            continue
        by_day.setdefault((eid, wd), []).append(r)

    days_out, by_employee, by_employee_store = [], {}, {}
    for (eid, wd), punches in by_day.items():
        enabled, minutes, min_shift_hours = resolve_employee_lunch_settings(tenant_cfg, overrides.get(eid))
        result = _day_deduction(punches, enabled, minutes, min_shift_hours)
        result["employee_id"] = eid
        result["work_date"] = wd
        days_out.append(result)
        if result["applied"]:
            by_employee[eid] = round(by_employee.get(eid, 0.0) + result["deduct_hours"], 2)
            key = (eid, result["store_code"])
            by_employee_store[key] = round(by_employee_store.get(key, 0.0) + result["deduct_hours"], 2)
    return {"available": True, "tenant_config": tenant_cfg, "by_employee": by_employee,
            "by_employee_store": by_employee_store, "days": days_out, "limit_hit": False}


def get_lunch_config(org_id, sb_client):
    """(tenant_cfg, overrides, available) — the ONE call every router endpoint makes before touching
    lunch deduction, so tenant+employee config is fetched exactly once per request."""
    tenant_cfg, t_ok = get_tenant_lunch_config(org_id, sb_client)
    overrides, e_ok = get_employee_lunch_overrides(org_id, sb_client)
    return tenant_cfg, overrides, (t_ok and e_ok)


# Generous headroom over the legacy /payroll timelog fallback's own 20,000-row cap (that comment's own
# words: "No current tenant is near 20k punches/month") — NOT unbounded (a single unbounded org-wide
# query is its own risk), but sized so hitting it is a genuine anomaly worth flagging, not routine.
LUNCH_TIMELOG_FETCH_LIMIT = 50000


def period_lunch_deduction(org_id, lo, hi, sb_client):
    """Convenience one-shot: fetch config + closed timelog rows for [lo, hi) (SAME half-open bounds as
    `_resolve_range`/`_payroll_month_groups`) and compute. Prefer `compute_lunch_deduction_from_rows`
    directly when the caller already has a `timelog` fetch to reuse (e.g. the actual-hours-detail
    drill-down) — avoids a second identical query.

    HONESTY (no-silent-caps doctrine): the fetch is bounded (`LUNCH_TIMELOG_FETCH_LIMIT`) — a
    pathological org+range with MORE rows than that would silently under-deduct (fail-safe: pays MORE,
    never less) if this weren't flagged. The returned dict's `limit_hit` is True whenever the fetch
    returned exactly the cap (a strong signal, not proof, of truncation — PostgREST gives no total-count
    without a separate query) — router.py callers surface this as an explicit
    `lunch_deduction_data_capped` field wherever they already add lunch fields (never fabricated when
    `limit_hit` is False, same additive-only-when-relevant convention as `lunch_deduction_hours`
    itself)."""
    tenant_cfg, overrides, available = get_lunch_config(org_id, sb_client)
    empty = {"available": False, "tenant_config": tenant_cfg, "by_employee": {}, "by_employee_store": {},
             "days": [], "limit_hit": False}
    if not available or not (lo and hi):
        return empty
    try:
        rows = (sb_client.table("timelog").select("id,employee_id,work_date,store_code,clock_in,clock_out,hours")
                .eq("org_id", org_id).gte("work_date", lo).lt("work_date", hi)
                .limit(LUNCH_TIMELOG_FETCH_LIMIT).execute().data) or []
    except Exception:
        return empty
    result = compute_lunch_deduction_from_rows(rows, tenant_cfg, overrides)
    result["limit_hit"] = len(rows) >= LUNCH_TIMELOG_FETCH_LIMIT
    return result
