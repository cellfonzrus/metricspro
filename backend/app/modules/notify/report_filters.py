"""Report FILTER resolution — pure, leaf module (stdlib + settings only).

Deliberately imports nothing from another module: `report_registry` pulls in the asset / commcalc /
account routers, and the attention provider (which validates saved schedules) must not drag those in
just to check a date string. Everything here is pure and unit-proven in
`backend/harness_notify_failure_leads.py`.

WHY THIS EXISTS (2026-07-30, failure_log lead `run_for_tenant/notify.subscription|sweep_error` ×4):
a scheduled Weekly Owed-to-Distributor raised "owed_weekly requires a 'thursday' (billing Friday,
YYYY-MM-DD) filter" on every run. A RECURRING schedule cannot carry a correct fixed date — one was
never entered (the subscription form's filter boxes are free-text and optional), and had one been
entered it would have frozen the report on one stale week forever. So a blank / relative filter is
RESOLVED at run time, exactly the way the report's own page defaults it, and only a value that can't
mean anything is refused — as a CONFIG error, not a crash.
"""
from datetime import date, datetime, timedelta, timezone

from app.core.config import settings


class ReportConfigError(ValueError):
    """The report can't be built because its SAVED FILTERS are wrong — a configuration problem the
    admin fixes on the schedule / send form, not a crash to retry.

    Subclasses ValueError so callers that already map ValueError → HTTP 400 keep working; the
    scheduled runner catches this type FIRST so a mis-configured schedule is reported against that
    schedule instead of being recorded as a sweep crash."""


def business_today(tz: str = "") -> date:
    """Today in the tenant's business timezone (falls back to settings.BUSINESS_TZ, then UTC).

    A scheduled send fires at the SCHEDULE's local hour; resolving a relative date off the server's
    UTC day would put an 8pm-Eastern Friday run on Saturday's date."""
    name = (tz or "").strip() or (settings.BUSINESS_TZ or "").strip() or "America/New_York"
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(name)).date()
    except Exception:
        return datetime.now(timezone.utc).date()


# The distributor bills on FRIDAY, and the (historically named) `thursday` filter carries that
# Friday's date — see asset.get_owed_weekly. This mirrors the owed-weekly PAGE's own default
# (`upcomingFriday()`: `(5 - getDay() + 7) % 7` days ahead, so ON a Friday it is TODAY), so a
# schedule with no date sends the same week the page opens on. The Friday billing trigger itself is
# asset's and is NOT touched here — this only chooses WHICH Friday to ask for.
_TOKENS_CURRENT = ("", "current", "this", "now", "upcoming", "next", "this week")
_TOKENS_LAST = ("last", "previous", "prev", "last week")


def resolve_billing_friday(f: dict, tz: str = "") -> str:
    """The billing-Friday date (YYYY-MM-DD) for the owed-weekly report.

    Accepts an explicit date (passed through UNCHANGED), or the relative tokens
    ''/current/this/upcoming/next → this week's billing Friday, and last/previous/prev → the Friday
    before it, so a RECURRING schedule stays meaningful. Anything else raises ReportConfigError."""
    raw = str((f or {}).get("thursday") or "").strip()
    tok = raw.lower()
    if tok in _TOKENS_CURRENT or tok in _TOKENS_LAST:
        today = business_today(tz)
        friday = today + timedelta(days=(4 - today.weekday()) % 7)   # Mon=0 … Fri=4; on a Friday → today
        if tok in _TOKENS_LAST:
            friday -= timedelta(days=7)
        return friday.isoformat()
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except Exception:
        raise ReportConfigError(
            f"Weekly Owed-to-Distributor: the 'thursday' filter must be a billing Friday date "
            f"(YYYY-MM-DD) or one of current / last — got '{raw}'. Leave it blank to send the "
            f"current billing week every time.")


# report_key → a pure callable that raises ReportConfigError if the saved filters can't build.
# Reports absent from this map have no required filter (every builder defaults its own).
FILTER_VALIDATORS = {
    "owed_weekly": resolve_billing_friday,
}


def validate_filters(report_key: str, filters: dict, known_keys=None) -> None:
    """Raise ReportConfigError if this report's SAVED filters can't produce a report.

    Cheap + pure (no DB, no network, no report build): the scheduled runner calls it BEFORE the
    tenant job guard, and the notify attention provider calls it to surface the same problem in the
    admin popup. `known_keys` (when given) also rejects a schedule pointing at a report this build no
    longer has — it never sends, and a KeyError at build time would otherwise look like a crash."""
    if known_keys is not None and report_key not in known_keys:
        raise ReportConfigError(
            f"this schedule points at a report this system no longer has ('{report_key}') — pick a "
            f"current report or delete the schedule")
    v = FILTER_VALIDATORS.get(report_key)
    if v:
        v(filters or {})
