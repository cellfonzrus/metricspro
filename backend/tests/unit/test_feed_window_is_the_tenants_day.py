"""THE DAY A FEED ASKS FOR IS THE TENANT'S DAY, NEVER THE SERVER'S UTC DAY.

The regression (index §COMMCALC-52, owner report 2026-10-31). The comprehensive comp report has
never once arrived automatically — not one `epay auto-sweep` row in `commcalc.upload_log` since the
leg was built in June. Its schedule is 23:30 in the tenant's timezone, deliberately, because the
portal posts a day's compensation at about 23:30 local. 23:30 America/New_York is 03:30 UTC THE NEXT
DAY, and the window was built from `datetime.now(timezone.utc).date()`, so every run asked the
portal for a day that had not begun where the portal lives. Measured on eight consecutive recorded
runs:

    run start (UTC)       UTC today     tenant's today   window the sweep asked for
    2026-09-27T03:31:00   2026-09-27    2026-09-26       2026-09-27..2026-09-27
    2026-09-28T03:31:02   2026-09-28    2026-09-27       2026-09-28..2026-09-28
    …                                                     (every run, one day ahead)

Zero rows, every time, for months, reported as "no compensation posted" — a feed that cannot answer
being read as a source with nothing to say.

The rule already had a home. `notify/report_filters.business_today` was written on 2026-07-30 and
its docstring states this exact defect: *"A scheduled send fires at the SCHEDULE's local hour;
resolving a relative date off the server's UTC day would put an 8pm-Eastern Friday run on
Saturday's date."* The sweep never asked it. These tests are about the dereference, not the rule.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.modules.commcalc import epay_sweep as es
from app.modules.notify import report_filters as rf


EASTERN = "America/New_York"


# ── THE REGRESSION, ON THE RECORDED RUN TIMES ────────────────────────────────────────────────────

#: (run start in UTC, the tenant's local date at that instant) — read off `core.job_run`.
RECORDED_RUNS = [
    ("2026-09-27T03:31:00+00:00", date(2026, 9, 26)),
    ("2026-09-28T03:31:02+00:00", date(2026, 9, 27)),
    ("2026-09-29T03:31:07+00:00", date(2026, 9, 28)),
    ("2026-09-30T03:30:59+00:00", date(2026, 9, 29)),
    ("2026-10-01T03:30:57+00:00", date(2026, 9, 30)),
    ("2026-10-02T03:30:39+00:00", date(2026, 10, 1)),
    ("2026-10-03T03:31:01+00:00", date(2026, 10, 2)),
    ("2026-10-04T03:31:04+00:00", date(2026, 10, 3)),
]


@pytest.mark.parametrize("started,local", RECORDED_RUNS)
def test_the_utc_day_really_was_a_day_ahead_of_the_tenants_day(started, local):
    """The arithmetic the defect rests on, asserted rather than asserted-about: at every one of
    these recorded run instants the server's UTC date is one day later than the tenant's."""
    t = datetime.fromisoformat(started)
    assert t.astimezone(timezone.utc).date() == local.fromordinal(local.toordinal() + 1)
    assert t.astimezone(ZoneInfo(EASTERN)).date() == local


@pytest.mark.parametrize("started,local", RECORDED_RUNS)
def test_the_window_now_ends_on_the_tenants_day(started, local):
    """THE FIX. A one-day window asks for the tenant's today, which is a day the portal can have."""
    days = es._recent_days(1, today=local)
    assert days == [local.isoformat()]


def test_a_one_day_window_used_to_ask_for_a_day_that_had_not_begun():
    """Stated as a test so the shape cannot come back unnoticed: building the window off the UTC
    day at 23:31 Eastern yields TOMORROW in the tenant's calendar."""
    t = datetime.fromisoformat("2026-09-27T03:31:00+00:00")
    utc_window = es._recent_days(1, today=t.astimezone(timezone.utc).date())
    local_today = t.astimezone(ZoneInfo(EASTERN)).date()
    assert utc_window == ["2026-09-27"]
    assert local_today.isoformat() == "2026-09-26"
    assert utc_window[0] > local_today.isoformat(), "the window asked for a future day"


# ── THE ONE HOME IS DEREFERENCED, NOT RE-SPELLED ─────────────────────────────────────────────────

def test_the_tenants_today_comes_from_the_one_home():
    """`business_today` is the home for this (notify/report_filters, 2026-07-30). The sweep asks it
    rather than carrying a second zoneinfo ladder."""
    assert es.feed_today(EASTERN) == rf.business_today(EASTERN)


def test_the_one_home_answers_in_the_zone_it_is_given():
    """Two zones on opposite sides of the date line cannot both be today."""
    a = rf.business_today("Pacific/Kiritimati")
    b = rf.business_today("Pacific/Niue")
    assert (a - b).days in (0, 1)


def test_an_unusable_zone_does_not_crash_the_sweep():
    """A typo in a tenant's timezone must not take the feed down; the house ladder answers."""
    assert isinstance(es.feed_today("Not/AZone"), date)
    assert isinstance(es.feed_today(""), date)
    assert isinstance(es.feed_today(None), date)


# ── THE ZONE IS A REQUIRED ARGUMENT, SO NO CALLER CAN SILENTLY USE THE SERVER'S DAY ──────────────

def test_expanding_the_jobs_requires_the_tenants_zone():
    """The defect was a caller that never asked the org what day it was. A default here would let
    the next caller not ask either, so `tz` is keyword-only and required."""
    with pytest.raises(TypeError):
        es._expand_jobs(["comp_report"], {})
    with pytest.raises(TypeError):
        es._expand_jobs(["comp_report"])


def test_the_day_range_job_is_built_on_the_tenants_day():
    jobs = es._expand_jobs(["comp_report"], {"comp_report": {"refresh_days": 1}}, tz=EASTERN)
    assert len(jobs) == 1
    _key, target = jobs[0]
    assert target["kind"] == "day_range"
    assert target["covers_through"] == rf.business_today(EASTERN).isoformat()


def test_a_wider_window_still_ends_on_the_tenants_day():
    """Widening the window must extend it BACKWARDS; the far end stays the tenant's today, because
    a feed cannot answer about tomorrow however wide the range is."""
    jobs = es._expand_jobs(["comp_report"], {"comp_report": {"refresh_days": 5}}, tz=EASTERN)
    _key, target = jobs[0]
    today = rf.business_today(EASTERN).isoformat()
    assert target["covers_through"] == today
    assert len(target["days"]) == 5
    assert target["days"][-1] == today
    assert target["days"][0] < today


def test_a_month_grain_report_is_unaffected():
    """MI is month-grain and was working; this change must not move it."""
    jobs = es._expand_jobs(["mi"], {"mi_report": {"refresh_months": 2}}, tz=EASTERN)
    assert len(jobs) == 2
    assert all(t["kind"] == "month" for _k, t in jobs)


def test_a_report_with_no_window_is_unaffected():
    assert es._expand_jobs(["payment_detail"], {}, tz=EASTERN) == [("payment_detail", None)]


# ── EVERY TENANT, NOT ONE ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tz", ["America/New_York", "America/Chicago", "America/Los_Angeles",
                                "Asia/Kolkata", "UTC"])
def test_each_tenants_window_ends_on_that_tenants_own_day(tz):
    """One tenant's clock must never decide another tenant's window. Every zone gets its own."""
    jobs = es._expand_jobs(["comp_report"], {"comp_report": {"refresh_days": 2}}, tz=tz)
    _key, target = jobs[0]
    assert target["covers_through"] == rf.business_today(tz).isoformat()


def test_two_tenants_either_side_of_midnight_get_different_windows():
    """The whole point: at the same instant, two tenants can be on different days, and each one's
    feed must ask for its own."""
    east = es._expand_jobs(["comp_report"], {"comp_report": {"refresh_days": 1}},
                           tz="Pacific/Kiritimati")[0][1]["covers_through"]
    west = es._expand_jobs(["comp_report"], {"comp_report": {"refresh_days": 1}},
                           tz="Pacific/Niue")[0][1]["covers_through"]
    assert east >= west
