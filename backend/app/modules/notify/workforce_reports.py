"""Payroll & Workforce report builders (W3) — the server twins of the six workforce pages' exports,
merged into the notify report registry so the surfaces get the platform's STANDARD scheduled
email/WhatsApp sends (charter rule 3: never a bespoke exporter).

SIX ENTRIES (`WORKFORCE_REPORTS`, spliced into `report_registry.REPORTS`):
    storeops_payroll          → GET /storeops/payroll            (hours + pay, pay RBAC-stripped)
    storeops_hours_approval   → GET /storeops/payroll/approvals  (HOURS-ONLY by design)
    storeops_payroll_tax      → GET /storeops/payroll-raw + payroll_tax_estimate (all-money, gated)
    storeops_payroll_expenses → GET /storeops/payroll-expenses/{YYYY-MM} (all-money, gated)
    storeops_attendance       → GET /storeops/timeclock/attendance-exceptions (hours-only)
    storeops_lateness         → GET /storeops/accountability     ("Lateness %", hours-only)

CHARTER RULES APPLIED HERE:
  • ONE data path — every builder calls the page's EXISTING storeops handler in-process (the same
    plain-function convention the rest of report_registry uses); nothing re-queries the tables.
    Every handler call binds `authorization=` and `org_id=` EXPLICITLY so no FastAPI parameter
    sentinel is ever left bound (the 2026-07-17 `Header`-sentinel 500, harness_notify_failure_leads
    §E). All handlers here are sync `def`s — called without await, deliberately.
  • ONE period resolver — `_pay_period_range` delegates to `core.router.pay_period_for` via
    `payroll_approval._pay_settings` (the same read-only dependency payroll_salary.py takes) and
    NEVER re-implements the arithmetic (charter rule 2: schedule / hours approval / payroll / tax /
    expenses share one resolver). Hours Approval passes blank dates through so the ENDPOINT's own
    default — the previous COMPLETE period, `payroll_approval._resolve_period` — stays authoritative.
  • PAY-VISIBILITY RBAC (mig 434, storeops/pay_visibility.py) — server-side, on the payload:
      - storeops_payroll strips money keys via the SAME can_see_pay + strip_pay calls its live
        route (`get_payroll_route`) makes, and drops the pay COLUMNS like the page does;
      - storeops_payroll_tax / storeops_payroll_expenses are ALL-money reports: a caller the gate
        denies gets a clear ValueError (HTTP 400 on-demand; a report_config-style failure on a
        schedule) — FAIL CLOSED, never a stripped-to-empty or leaking send. NOTE this is
        deliberately STRICTER than today's live `/payroll-raw` (currently ungated — flagged to the
        owner) and exactly the charter-4 posture for `/payroll-expenses`;
      - hours approval / attendance / lateness are HOURS-ONLY surfaces: pay keys are stripped
        unconditionally (belt) AND no pay column exists in their sheets (braces).
    A SCHEDULED run has no caller (authorization="") → the gate falls to its open-app-parity /
    fail-closed path: pay rides only when the tenant's config (`pay_visibility='all'`, or login
    enforcement off) says the org-wide view is open — never on a locked-down tenant.
  • Org-scoped — org_id threads into every handler; the handlers' own scope_keyset span narrowing
    applies unchanged. Config, never code — nothing tenant-specific lives here.

LAZY app imports only (report_filters is the one leaf dependency), so
backend/harness_workforce_report_registry.py can prove this module with the stdlib alone.
"""
from datetime import date, timedelta

from .report_filters import business_today as _business_today

# ── ONE shared pay-period resolver (charter rule 2) ───────────────────────────────────────────────
_TOKENS_LAST = ("last", "previous", "prev")


def _pay_period_range(org_id, f, tz=""):
    """(start_iso, end_iso), both INCLUSIVE — the tenant's CURRENT pay period by default, exactly
    what the payroll / payroll-tax pages open on (Phase W2 period coherence). An explicit
    `start`+`end` filter pair passes through untouched (the pages' From/To override); the `period`
    filter accepts the relative tokens current/this/now (default) and last/previous/prev (one full
    period back), so a recurring schedule stays meaningful forever.

    DELEGATES the arithmetic: settings via payroll_approval._pay_settings (normalized by core's
    _pp_settings), period math via core.router.pay_period_for — never a local copy. When the
    settings row is unreadable, `_pp_settings({})` supplies the resolver's OWN documented defaults
    (weekly, Monday) so even the degrade path runs the one shared implementation."""
    s = str((f or {}).get("start") or "").strip()[:10]
    e = str((f or {}).get("end") or "").strip()[:10]
    if s and e:
        return date.fromisoformat(s).isoformat(), date.fromisoformat(e).isoformat()
    from app.modules.core.router import pay_period_for, _pp_settings
    from app.modules.storeops.payroll_approval import _pay_settings
    cfg = _pay_settings(org_id) or _pp_settings({})
    cur = pay_period_for(cfg, _business_today(tz))
    if str((f or {}).get("period") or "").strip().lower() in _TOKENS_LAST:
        cur = pay_period_for(cfg, date.fromisoformat(cur["start"]) - timedelta(days=1))
    return cur["start"], cur["end"]


# ── the mig-434 gate for ALL-money reports ────────────────────────────────────────────────────────
def _require_pay_access(org_id, authorization):
    """Raise ValueError unless this caller may see pay figures (pay_visibility.can_see_pay — the
    exact gate the live payroll money routes apply). Used by the two reports that are NOTHING BUT
    pay (payroll tax / payroll expenses), where stripping would leave an empty lie of a report:
    deny loudly instead, and never send."""
    from app.modules.storeops import pay_visibility as _pv
    auth = authorization if isinstance(authorization, str) else ""
    if not _pv.can_see_pay(auth, org_id):
        raise ValueError(
            "This report is payroll money (market-manager-and-up per your org's pay-visibility "
            "config, migration 434). Your role may not view it, so nothing was sent. An admin can "
            "widen storeops.tenants.pay_visibility / pay_visible_roles, or grant the "
            "'employee_pay_rates' data permission.")


# ── column sets (module-level so the harness can prove the hours-only ones carry no pay) ──────────
_num = lambda hdr, key: {"header": hdr, "key": key, "align": "right"}
_mon = lambda hdr, key: {"header": hdr, "key": key, "money": True}

PAYROLL_HOURS_COLS = [
    {"header": "Employee", "key": "name"},
    {"header": "Store", "key": "store"},
    _num("Shifts", "shifts"),
    _num("Scheduled Hrs", "scheduled_hours"),
    _num("Actual Hrs", "actual_hours"),
    _num("Lunch (auto)", "lunch_deduction_hours"),
    {"header": "DM", "key": "dm_status"},
    {"header": "HR", "key": "hr_status"},
]
PAYROLL_PAY_COLS = [   # appended ONLY when the mig-434 gate allows — same column drop as the page
    {"header": "Pay Rate", "fn": (lambda r: r.get("pay_basis") if (r.get("pay_basis") or "hourly") != "hourly"
                                  else r.get("pay_rate"))},
    _mon("Scheduled Pay", "scheduled_pay"),
    _mon("Actual Pay", "actual_pay"),
]

HOURS_APPROVAL_COLS = [   # HOURS-ONLY — the harness asserts no pay field ever appears here
    {"header": "Employee", "key": "name"},
    {"header": "Store", "key": "store"},
    _num("Scheduled", "scheduled_hours"),
    _num("Worked Hours", "hours_worked"),
    _num("Lunch", "lunch_hours"),
    _num("Adjustment", "adjustment_hours"),
    {"header": "Adjustment reason", "key": "adjustment_reason"},
    _num("Payable Hours", "hours_payable"),
    {"header": "No clock record", "fn": (lambda r: "yes" if r.get("no_clock_record") else "")},
    _num("Computed Hours (net)", "hours_source"),
    _num("Approved Hours", "hours_effective"),
    {"header": "Corrected", "fn": (lambda r: "yes" if r.get("hours_corrected") else "")},
    {"header": "DM", "key": "dm_status"},
    {"header": "HR", "key": "hr_status"},
    {"header": "Paid by", "key": "payer_name"},
    {"header": "Held", "fn": (lambda r: "HELD — not approved" if r.get("held") else "")},
]

PAYROLL_TAX_COLS = [
    {"header": "Employee", "key": "name"},
    {"header": "Store", "key": "store"},
    _num("Regular Hrs", "regular_hours"),
    _num("OT Hrs", "ot_hours"),
    _mon("Rate", "pay_rate"),
    _mon("Gross", "gross"),
    _mon("Social Security", "fica_ss"),
    _mon("Medicare", "fica_medicare"),
    _mon("Federal", "federal"),
    _mon("State", "state_wh"),
    _mon("Net", "net"),
    {"header": "W-4 Filing/State", "key": "w4"},
    {"header": "Hours Basis", "fn": (lambda r: "Scheduled (est.)" if r.get("basis") == "scheduled" else "Clocked")},
]

PAYROLL_EXPENSES_COLS = [
    {"header": "Store", "key": "store"},
    _mon("Wages", "wages"),
    _mon("FICA SS", "fica_ss"),
    _mon("Medicare", "medicare"),
    _mon("FUTA", "futa"),
    _mon("SUTA", "suta"),
    _mon("Tax Total", "tax_total"),
    _mon("Items Total", "items_total"),
    _mon("Total", "total"),
]

ATTENDANCE_COLS = [   # hours-only
    {"header": "Type", "key": "exception_type"},
    {"header": "Excused", "fn": (lambda r: (r.get("excused_reason") or "Yes") if r.get("excused") else "")},
    {"header": "Employee", "key": "employee_name"},
    {"header": "Date", "key": "work_date"},
    {"header": "Store", "key": "store_code"},
    {"header": "Scheduled", "fn": (lambda r: f"{r.get('shift_start')}–{r.get('shift_end')}"
                                   if r.get("shift_start") else "")},
    {"header": "Clock In", "fn": (lambda r: r.get("actual_clock_in_local") or r.get("actual_clock_in") or "")},
    {"header": "Clock Out", "fn": (lambda r: r.get("actual_clock_out_local") or r.get("actual_clock_out") or "")},
    _num("Minutes Late", "minutes_late"),
    _num("Minutes Early", "minutes_early"),
]

LATENESS_EMPLOYEE_COLS = [   # hours-only — the renamed 'Lateness %' page (formerly Accountability)
    {"header": "Employee", "key": "employee"},
    _num("Scheduled Shifts", "total_shifts"),
    _num("Times Late", "late"),
    {"header": "Lateness %", "fn": (lambda r: f"{round(float(r.get('late_rate') or 0) * 100)}%"), "align": "right"},
    _num("No-shows", "no_show"),
    _num("Left Early", "left_early"),
    _num("Excused", "excused"),
    {"header": "Flags", "fn": (lambda r: ", ".join(r.get("flags") or []))},
]
LATENESS_INCIDENT_COLS = [
    {"header": "Employee", "key": "employee"},
    {"header": "Date", "key": "work_date"},
    {"header": "Store", "key": "store_code"},
    {"header": "Clock In", "fn": (lambda r: r.get("clock_in_local") or r.get("clock_in") or "")},
    _num("Min Late", "minutes_late"),
    {"header": "Clock Out", "fn": (lambda r: r.get("clock_out_local") or r.get("clock_out") or "")},
    _num("Min Early", "minutes_early"),
    _num("Times Late (period)", "times_late_period"),
]

# Belt for the hours-only surfaces: any pay key that could ride a row/totals dict is deleted even
# though no column reads one (pay_visibility.PAY_FIELDS is the platform's canonical list — this is
# a REFERENCE to it at call time via strip_pay's own defaults, not a second list).


# ── builders ──────────────────────────────────────────────────────────────────────────────────────
async def _payroll(org_id, f, authorization="", tz=""):
    """Server twin of /storeops/payroll's export. Pay gate = the EXACT live-route pair
    (can_see_pay → strip_pay) `get_payroll_route` applies; pay COLUMNS drop with it, like the page."""
    from app.modules.storeops import router as SO
    from app.modules.storeops import pay_visibility as _pv
    lo, hi = _pay_period_range(org_id, f, tz)
    auth = authorization if isinstance(authorization, str) else ""
    rows = SO.get_payroll(start=lo, end=hi, authorization=auth, org_id=org_id, response=None) or []
    allowed = _pv.can_see_pay(auth, org_id)
    if not allowed:
        rows, _ = _pv.strip_pay(rows)
    cols = PAYROLL_HOURS_COLS + (PAYROLL_PAY_COLS if allowed else [])
    total_hours = round(sum(float(r.get("actual_hours") or 0) for r in rows), 2)
    sub = f"Pay period {lo} – {hi} · {len(rows)} employees · {total_hours} actual hrs"
    if not allowed:
        sub += " · hours only (pay hidden by org policy)"
    return {"title": "Payroll", "subtitle": sub, "filename": f"payroll-{lo}",
            "sheets": [{"name": "By Employee", "rows": rows, "columns": cols}]}


async def _hours_approval(org_id, f, authorization=""):
    """Server twin of the Hours Approval board's export — HOURS ONLY (charter: this surface never
    shows pay, stricter than the money reports). Blank start/end defers to the endpoint's own
    default, the previous COMPLETE pay period (payroll_approval._resolve_period — the one shared
    resolver again, from its other end)."""
    from app.modules.storeops import payroll_approval as PA
    from app.modules.storeops import pay_visibility as _pv
    auth = authorization if isinstance(authorization, str) else ""
    f = f or {}
    data = PA.list_approvals(start=str(f.get("start") or "")[:10], end=str(f.get("end") or "")[:10],
                             store_code=str(f.get("store_code") or ""), market=str(f.get("market") or ""),
                             employee_id="", status=str(f.get("status") or ""),
                             authorization=auth, org_id=org_id) or {}
    rows, totals = data.get("rows") or [], data.get("totals") or {}
    # Hours-only surface: pay comes OFF unconditionally, whoever asked (the endpoint's own deny-list
    # gate may already have stripped it — stripping twice is a documented no-op).
    rows, totals = _pv.strip_pay(rows, totals)
    lo, hi = data.get("period_start") or "", data.get("period_end") or ""
    sub = (f"Pay period {lo} – {hi} · {totals.get('employees', len(rows))} employees · "
           f"{totals.get('hours', 0)} hrs · pending DM {totals.get('pending_dm', 0)} · "
           f"pending HR {totals.get('pending_hr', 0)} · held {totals.get('held', 0)}")
    if not data.get("ready", True):
        sub = data.get("note") or "Hours approval is not activated (run migration 431)."
    return {"title": "Hours Approval", "subtitle": sub, "filename": f"hours-approval-{lo or 'pending'}",
            "sheets": [{"name": "Approvals", "rows": rows, "columns": HOURS_APPROVAL_COLS}]}


def payroll_tax_lines(raw_rows, compute):
    """PURE: /payroll-raw rows + the tax-estimate twin → export lines (page parity)."""
    lines = []
    for r in raw_rows or []:
        w4 = r.get("settings") or {}
        p = compute(r.get("total_hours"), r.get("pay_rate"), w4)
        lines.append({"name": r.get("name"), "store": r.get("store"), "pay_rate": r.get("pay_rate"),
                      "basis": r.get("basis"),
                      "regular_hours": p["regular_hours"], "ot_hours": p["ot_hours"],
                      "gross": p["gross"], "fica_ss": p["fica_ss"], "fica_medicare": p["fica_medicare"],
                      "federal": p["federal"], "state_wh": p["state"], "net": p["net"],
                      "deductions": p["deductions"], "employer_fica": p["employer_fica"],
                      "w4": f"{w4.get('filing_status') or 'Single'} · {w4.get('state') or 'NY'}"
                           + (" · flat" if w4.get("skipped") else "")})
    return lines


async def _payroll_tax(org_id, f, authorization="", tz=""):
    """Server twin of /storeops/payroll-tax's export: raw inputs from GET /storeops/payroll-raw (the
    page's one data path), withholding via the payroll_tax_estimate twin. ALL-money → the mig-434
    gate must pass or the send fails closed."""
    _require_pay_access(org_id, authorization)
    from app.modules.storeops import router as SO
    from app.modules.storeops.payroll_tax_estimate import compute_pay
    lo, hi = _pay_period_range(org_id, f, tz)
    auth = authorization if isinstance(authorization, str) else ""
    data = SO.payroll_raw(start=lo, end=hi, authorization=auth, org_id=org_id) or {}
    lines = payroll_tax_lines(data.get("rows") or [], compute_pay)
    gross = round(sum(l["gross"] for l in lines), 2)
    net = round(sum(l["net"] for l in lines), 2)
    return {"title": "Payroll with Tax (Estimate)",
            "subtitle": f"Pay period {lo} – {hi} · gross ${gross:,.2f} · net ${net:,.2f} · "
                        "flat-rate estimate, not a payroll provider substitute",
            "filename": f"payroll-tax-{lo}",
            "sheets": [{"name": "Withholding", "rows": lines, "columns": PAYROLL_TAX_COLS}]}


def payroll_expenses_month(org_id, f, tz=""):
    """The `{period}` month key ('YYYY-MM') for /payroll-expenses: explicit `month` filter wins,
    else the calendar month of the CURRENT pay period's START — the Phase-W2 documented seam
    between the period-coherent default and the endpoint's month-granular contract."""
    m = str((f or {}).get("month") or "").strip()[:7]
    if len(m) == 7:
        return m
    lo, _hi = _pay_period_range(org_id, f, tz)
    return lo[:7]


async def _payroll_expenses(org_id, f, authorization="", tz=""):
    """Server twin of /hr/payroll-expenses' view: employer burden + gross payroll per store off
    GET /storeops/payroll-expenses/{YYYY-MM}. ALL-money → mig-434 gate, fail closed."""
    _require_pay_access(org_id, authorization)
    from app.modules.storeops import router as SO
    month = payroll_expenses_month(org_id, f, tz)
    auth = authorization if isinstance(authorization, str) else ""
    data = SO.get_payroll_expenses(period=month, authorization=auth, org_id=org_id) or {}
    stores = data.get("stores") or []
    total = round(sum(float(s.get("total") or 0) for s in stores), 2)
    return {"title": "Payroll Expenses", "subtitle": f"{month} · employer burden ${total:,.2f}",
            "filename": f"payroll-expenses-{month}",
            "sheets": [
                {"name": "Burden by Store", "rows": stores, "columns": PAYROLL_EXPENSES_COLS},
                {"name": "Gross Payroll", "rows": data.get("gross_cells") or [], "columns": [
                    {"header": "Store", "key": "store"},
                    _mon("Gross Payroll", "amount"),
                ]},
            ]}


async def _attendance(org_id, f, authorization="", tz=""):
    """Server twin of /storeops/attendance's export (hours-only)."""
    from app.modules.storeops import router as SO
    lo, hi = _pay_period_range(org_id, f, tz)
    auth = authorization if isinstance(authorization, str) else ""
    data = SO.attendance_exceptions(start=lo, end=hi, authorization=auth, org_id=org_id) or {}
    rows = data.get("rows") or []
    counts = [{"exception_type": k, "count": v} for k, v in sorted((data.get("counts") or {}).items())]
    sub = f"Pay period {lo} – {hi} · {len(rows)} exceptions"
    if data.get("limit_hit"):
        sub += " · large range, results may be capped"
    return {"title": "Attendance Exceptions", "subtitle": sub, "filename": f"attendance-{lo}",
            "sheets": [
                {"name": "Exceptions", "rows": rows, "columns": ATTENDANCE_COLS},
                {"name": "Counts", "rows": counts, "columns": [
                    {"header": "Type", "key": "exception_type"},
                    _num("Count", "count"),
                ]},
            ]}


def lateness_incident_rows(employees):
    """PURE: flatten per-employee incidents into the page's own export rows (one per late/left-early
    incident, dates + local clock times — what the manager actually mails)."""
    out = []
    for e in employees or []:
        for i in (e.get("incidents") or []):
            row = {"employee": e.get("employee"), "work_date": i.get("work_date"),
                   "store_code": i.get("store_code"),
                   "clock_in": i.get("actual_clock_in"), "clock_in_local": i.get("actual_clock_in_local"),
                   "clock_out": i.get("actual_clock_out"), "clock_out_local": i.get("actual_clock_out_local"),
                   "minutes_late": i.get("minutes_late") if i.get("late") else "",
                   "minutes_early": i.get("minutes_early") if i.get("left_early") else "",
                   "times_late_period": e.get("late")}
            if row["minutes_late"] != "" or row["minutes_early"] != "":
                out.append(row)
    return out


async def _lateness(org_id, f, authorization="", tz=""):
    """Server twin of /storeops/accountability — the page renamed 'Lateness %' (hours-only):
    per-employee attendance patterns + the flattened incident rows + coaching recommendations."""
    from app.modules.storeops import router as SO
    lo, hi = _pay_period_range(org_id, f, tz)
    auth = authorization if isinstance(authorization, str) else ""
    data = SO.accountability(start=lo, end=hi, authorization=auth, org_id=org_id) or {}
    emps = data.get("employees") or []
    flagged = sum(1 for e in emps if e.get("flags"))
    return {"title": "Lateness %",
            "subtitle": f"Pay period {lo} – {hi} · {len(emps)} employees with incidents · {flagged} flagged",
            "filename": f"lateness-{lo}",
            "sheets": [
                {"name": "By Employee", "rows": emps, "columns": LATENESS_EMPLOYEE_COLS},
                {"name": "Incidents", "rows": lateness_incident_rows(emps), "columns": LATENESS_INCIDENT_COLS},
                {"name": "Coaching", "rows": data.get("recommendations") or [], "columns": [
                    {"header": "Employee", "key": "employee"},
                    {"header": "Recommendation", "key": "text"},
                ]},
            ]}


# ── registry fragment (spliced into report_registry.REPORTS — keys proven unique by the harness) ──
# wants_auth on ALL SIX: every handler span-scopes and/or pay-gates off the caller's header
# (AGENT_CONTRACT §3c — an export respects the caller's gates; "" on a schedule = the org-wide,
# fail-closed path). wants_tz on the five that resolve a relative pay period off the tenant's
# business day; hours-approval defers its default to the endpoint (previous complete period).
WORKFORCE_REPORTS = {
    "storeops_payroll": {
        "label": "Payroll (Hours & Pay)", "filters": ["period", "start", "end"],
        "live_path": lambda f: "/storeops/payroll",
        "build": _payroll, "wants_auth": True, "wants_tz": True},
    "storeops_hours_approval": {
        "label": "Hours Approval", "filters": ["start", "end", "store_code", "market", "status"],
        "live_path": lambda f: "/storeops/payroll/approvals",
        "build": _hours_approval, "wants_auth": True},
    "storeops_payroll_tax": {
        "label": "Payroll with Tax (Estimate)", "filters": ["period", "start", "end"],
        "live_path": lambda f: "/storeops/payroll-tax",
        "build": _payroll_tax, "wants_auth": True, "wants_tz": True},
    "storeops_payroll_expenses": {
        "label": "Payroll Expenses", "filters": ["month"],
        "live_path": lambda f: "/hr/payroll-expenses",
        "build": _payroll_expenses, "wants_auth": True, "wants_tz": True},
    "storeops_attendance": {
        "label": "Attendance Exceptions", "filters": ["period", "start", "end"],
        "live_path": lambda f: "/storeops/attendance",
        "build": _attendance, "wants_auth": True, "wants_tz": True},
    "storeops_lateness": {
        "label": "Lateness %", "filters": ["period", "start", "end"],
        "live_path": lambda f: "/storeops/accountability",
        "build": _lateness, "wants_auth": True, "wants_tz": True},
}
