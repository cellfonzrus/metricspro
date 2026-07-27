"""StoreOps contributions to the cross-module admin-attention feed
(app/modules/core/import_health.py's `register_provider`) — settings-audit package, 2026-07-26.

WHY THIS FILE EXISTS: `core/import_health.py` is a SHARED file (AGENT_CONTRACT §1) — mod-people never
edits it. Instead this module registers its own providers via the documented extension point:
    from app.modules.core.import_health import register_provider
    register_provider(key, label=..., group=..., cost=...)(fn)
`register(register_provider)` below is called ONCE from storeops/router.py's own import, guarded by a
try/except there so a missing/renamed core module can never break StoreOps itself (contract-mandated
degrade-gracefully posture — see AGENT_CONTRACT §5).

CONVENTIONS (matching import_health.py's own providers):
  - `client` passed to every provider is the UNSCOPED supabase client — schema-qualify every call
    (`client.schema("storeops").table(...)`), never assume a default schema.
  - Every read is filtered by the `org_id` argument the aggregator passes in (RULE ONE) — this file
    never uses a house-org constant as a data scope.
  - cost='cheap': bounded roster/config-shaped reads (employees, stores) — same shape as
    import_health's own `unmapped_stores` provider. cost='heavy': anything that scans
    storeops.timelog/shifts (potentially years of punches) — never runs on a login popup (deep=0).
  - Every provider is exception-isolated by the aggregator itself, but each one ALSO wraps its own
    reads in try/except so a missing/un-migrated table degrades to "no finding" instead of erroring
    the whole attention call for every other provider.
"""
from datetime import datetime, timedelta, timezone


def _item(group, key, severity, label, detail, count, deep_link, deep_link_label):
    return {"group": group, "key": key, "severity": severity, "label": label, "detail": detail,
            "count": int(count or 0), "deep_link": deep_link, "deep_link_label": deep_link_label}


def register(register_provider):
    """Called once, with the REAL decorator from core.import_health (import guarded by the caller).

    GROUP = 'other' for all 5 providers below, deliberately: the login popup
    (frontend/src/components/AdminAttention.tsx) hard-codes GROUP_ORDER = ['import','mapping',
    'duplicate','other'] and its modal filters items to exactly those four buckets — an item tagged
    with any other group string is counted in the header pill's total but never rendered in the
    modal body (Gate-1 finding, 2026-07-26; the same constraint hit mod-finance/mod-asset). Keep this
    'other' until platform-core teaches the popup to bucket unknown groups under 'Other' itself."""

    @register_provider("storeops_no_payscale", label="Employees with no pay rate set",
                       group="other", cost="cheap")
    def _p_no_payscale(client, org_id, ctx):
        """PENDING SETUP (cheap, roster-shaped table): an active employee with no hourly pay_rate pays
        $0 for every hour they work, with nothing else in the app ever flagging it (payroll/reports
        just show $0, indistinguishable from 'correctly paid nothing')."""
        try:
            rows = (client.schema("storeops").table("employees")
                    .select("id,employee_id,name,pay_rate,is_active")
                    .eq("org_id", org_id).limit(5000).execute().data) or []
        except Exception:
            return []
        missing = [e for e in rows if e.get("is_active") is not False
                   and (e.get("pay_rate") is None or float(e.get("pay_rate") or 0) <= 0)]
        if not missing:
            return []
        eg = ", ".join(sorted((e.get("name") or e.get("employee_id") or "?") for e in missing)[:3])
        return [_item("other", "no_payscale", "warning", "Employees with no pay rate set",
                      f"{len(missing)} active employee(s) have no hourly pay rate configured "
                      f"(e.g. {eg}) — payroll will pay them $0 for any hours they work until a rate "
                      f"is set. Set it at HR → People (per-employee) or upload a payscale sheet there.",
                      len(missing), "/hr", "Set pay rates (HR)")]

    @register_provider("storeops_stores_no_coverage",
                       label="Active stores with recent punches but no staff/schedule on record",
                       group="other", cost="heavy")
    def _p_stores_no_coverage(client, org_id, ctx):
        """PENDING SETUP (heavy — scans timelog/shifts): a store that people are ACTUALLY clocking in
        at (real recent punches, so it's clearly operating) but that currently has zero active
        employees calling it home AND nothing scheduled going forward — staffing/scheduling for it may
        have silently stopped (the same class of drift the 2026-07-25 'stores not going inactive'
        fix addressed for the is_active flag itself; this catches the coverage side)."""
        now = ctx.get("now") or datetime.now(timezone.utc)
        since = (now - timedelta(days=30)).date().isoformat()
        today = now.date().isoformat()
        upto = (now + timedelta(days=14)).date().isoformat()
        try:
            stores = (client.schema("storeops").table("stores")
                      .select("store_code,is_active").eq("org_id", org_id).limit(5000).execute().data) or []
        except Exception:
            return []
        active_codes = {(s.get("store_code") or "").strip().upper()
                         for s in stores if s.get("is_active") is not False and s.get("store_code")}
        if not active_codes:
            return []
        try:
            emps = (client.schema("storeops").table("employees").select("home_store,is_active")
                    .eq("org_id", org_id).limit(5000).execute().data) or []
        except Exception:
            emps = []
        staffed = {(e.get("home_store") or "").strip().upper()
                   for e in emps if e.get("is_active") is not False and e.get("home_store")}
        try:
            shifts = (client.schema("storeops").table("shifts").select("store_code")
                      .eq("org_id", org_id).eq("is_deleted", False)
                      .gte("shift_date", today).lte("shift_date", upto)
                      .limit(20000).execute().data) or []
        except Exception:
            shifts = []
        scheduled = {(s.get("store_code") or "").strip().upper() for s in shifts if s.get("store_code")}
        try:
            punches = (client.schema("storeops").table("timelog").select("store_code")
                       .eq("org_id", org_id).gte("work_date", since)
                       .limit(20000).execute().data) or []
        except Exception:
            return []
        recently_punched = {(p.get("store_code") or "").strip().upper() for p in punches if p.get("store_code")}
        gap = sorted(c for c in active_codes if c in recently_punched and c not in staffed and c not in scheduled)
        if not gap:
            return []
        eg = ", ".join(gap[:3])
        return [_item("other", "stores_no_coverage", "warning",
                      "Active stores with punches but no staff/schedule on record",
                      f"{len(gap)} active store(s) had a real clock-in punch in the last 30 days "
                      f"(e.g. {eg}) but no active employee's home store is set there and nothing is "
                      f"scheduled in the next 14 days — check whether staffing/scheduling silently "
                      f"stopped for {'this store' if len(gap) == 1 else 'these stores'}.",
                      len(gap), "/storeops/admin", "Review store assignments")]

    @register_provider("storeops_kiosk_no_face_template",
                       label="Kiosk clock-ins with no enrolled face template",
                       group="other", cost="heavy")
    def _p_kiosk_unenrolled(client, org_id, ctx):
        """PENDING SETUP (heavy — scans timelog): the kiosk auto-enrolls a face template on an
        employee's FIRST clock-in and verifies against it from the second onward (portal/page.tsx
        doEnroll/doVerify). An employee with 2+ kiosk punches but still zero storeops.face_descriptors
        row means enrollment never once succeeded for them (commonly a face-api model load failure on
        their device) — face-match protection is silently absent for every one of their punches."""
        now = ctx.get("now") or datetime.now(timezone.utc)
        since = (now - timedelta(days=60)).date().isoformat()
        try:
            punches = (client.schema("storeops").table("timelog")
                       .select("employee_id,employee_name,device")
                       .eq("org_id", org_id).gte("work_date", since)
                       .limit(20000).execute().data) or []
        except Exception:
            return []
        counts, names = {}, {}
        for p in punches:
            # EXACT match, not substring: a manager override's own punch is stamped
            # device='kiosk-override' (distinct sentinel, storeops/router.py's clock_in_override) —
            # it deliberately bypasses self-service face verification, so it must never count toward
            # "should have enrolled a face template but didn't."
            if str(p.get("device") or "").strip().lower() != "kiosk":
                continue
            eid = str(p.get("employee_id") or "").strip()
            if not eid:
                continue
            counts[eid] = counts.get(eid, 0) + 1
            names.setdefault(eid, p.get("employee_name"))
        frequent = {eid for eid, n in counts.items() if n >= 2}
        if not frequent:
            return []
        try:
            enrolled = {str(r.get("employee_id")) for r in
                        (client.schema("storeops").table("face_descriptors").select("employee_id")
                         .eq("org_id", org_id).in_("employee_id", list(frequent))
                         .limit(5000).execute().data or [])}
        except Exception:
            return []
        missing = sorted(frequent - enrolled)
        if not missing:
            return []
        eg = ", ".join((names.get(e) or e) for e in missing[:3])
        return [_item("other", "kiosk_no_face_template", "warning",
                      "Kiosk clock-ins with no enrolled face template",
                      f"{len(missing)} employee(s) have clocked in via the kiosk at least twice in the "
                      f"last 60 days (e.g. {eg}) but have never enrolled a face template — face-match "
                      f"verification is silently skipped for every one of their punches (often a "
                      f"face-api model load failure on their device). Have them reopen the kiosk on a "
                      f"stable connection so enrollment can complete, or check Timeclock for the raw "
                      f"punch history.",
                      len(missing), "/storeops/timeclock", "Review time clock")]

    @register_provider("storeops_weekly_hours_over_limit",
                       label="Stores over their configured weekly hours limit",
                       group="other", cost="heavy")
    def _p_weekly_hours_over_limit(client, org_id, ctx):
        """PENDING REVIEW (heavy — scans timelog): owner directive 2026-07-27 (payroll rework,
        Deliverable 3) — luxelink stores configured with a weekly hours budget
        (storeops.hours_budget, migration 087, the SAME setting the Schedule page's create-shift
        guard reads) were showing employees with ACTUAL clocked hours well past it. The budget is
        enforced ONLY at schedule-CREATE time (storeops/router.py `_enforce_hours_budget`) — nothing
        ever compared it to real clocked hours, so a store can silently run over budget an entire pay
        cycle with no signal anywhere. This is a cheap 7-day proxy (raw closed timelog punches, not
        the full /payroll merge-and-dedupe aggregation); the Payroll Report's own precise,
        drill-down-backed version is `GET /storeops/payroll/over-hours` — this item is just the
        login-popup nudge to go look there. No finding at all for a tenant with no budgets
        configured (default NULL = no limit, never flagged)."""
        try:
            budgets = {b["store_code"]: float(b.get("weekly_hours") or 0)
                       for b in (client.schema("storeops").table("hours_budget")
                                 .select("store_code,weekly_hours").eq("org_id", org_id)
                                 .execute().data or [])
                       if b.get("weekly_hours") is not None}
        except Exception:
            return []
        if not budgets:
            return []
        now = ctx.get("now") or datetime.now(timezone.utc)
        since = (now - timedelta(days=7)).date().isoformat()
        try:
            tl = (client.schema("storeops").table("timelog")
                  .select("store_code,hours,clock_out,work_date")
                  .eq("org_id", org_id).gte("work_date", since).limit(20000).execute().data) or []
        except Exception:
            return []
        totals = {}
        for t in tl:
            if not (t.get("clock_out") and t.get("hours") is not None):
                continue
            st = (t.get("store_code") or "").strip()
            if not st:
                continue
            totals[st] = totals.get(st, 0.0) + float(t.get("hours") or 0)
        over = sorted(((st, hrs, budgets[st]) for st, hrs in totals.items()
                       if st in budgets and hrs > budgets[st]), key=lambda x: -x[1])
        if not over:
            return []
        eg = ", ".join(f"{st} ({hrs:.0f}h vs {lim:.0f}h)" for st, hrs, lim in over[:3])
        return [_item("other", "weekly_hours_over_limit", "warning",
                      "Stores over their configured weekly hours limit",
                      f"{len(over)} store(s) had clocked hours over their configured weekly budget "
                      f"in the last 7 days (e.g. {eg}) — review the Payroll Report's over-limit "
                      f"highlighting and the Payroll Change Log for manual corrections.",
                      len(over), "/storeops/payroll", "Review Payroll Report")]
