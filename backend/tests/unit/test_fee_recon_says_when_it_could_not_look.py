"""A RECONCILIATION THAT COULD NOT READ A LEG MUST SAY SO, NEVER FLAG THE WHOLE RANGE AGAINST ZERO.

Written RED. `epay_ingest.per_store_day` and `epay_fee_recon.system_fee_per_store_day` both answered
`{}` when their read FAILED, which is the same answer they give when the table is genuinely empty.
Fed into `build_recon` that produced a flagged variance for every store-day — a false alarm over
money, on a read that never happened. Same class as `portal_backfill._rows` (no memory ⇒ no backfill)
and §PAYABLES-02's `can_date` (row count is not measurement).
"""
import pytest

from app.modules.commcalc import epay_fee_recon as fr
from app.modules.commcalc import epay_ingest as ei

pytestmark = pytest.mark.unit


class _Boom:
    """A client whose every read raises — "I could not look", not "there is nothing there"."""

    def schema(self, _name):
        return self

    def table(self, _name):
        return self

    def select(self, *_a, **_k):
        raise RuntimeError("transport is down")


def test_an_unreadable_portal_leg_answers_None_not_an_empty_map():
    assert ei.per_store_day(_Boom(), "org", "2026-11-01", "2026-11-02") is None


def test_an_unreadable_system_leg_answers_None_not_an_empty_map():
    assert fr.system_fee_per_store_day(_Boom(), "org", "2026-11-01", "2026-11-02") is None


def test_an_empty_but_readable_feed_still_answers_a_map(fake_supabase):
    """The distinction only works if a real empty read keeps answering {}."""
    fake_supabase.seed("commcalc.raw_epay_daily_tx", [])
    assert ei.per_store_day(fake_supabase, "org", "2026-11-01", "2026-11-02") == {}


def test_the_recon_refuses_to_flag_a_range_whose_portal_leg_it_could_not_read(monkeypatch):
    monkeypatch.setattr(fr, "system_fee_per_store_day",
                        lambda *a, **k: {("B-1", "2026-11-01"): 10.0})
    monkeypatch.setattr(ei, "per_store_day", lambda *a, **k: None)
    out = fr.fee_recon(_Boom(), "org", "2026-11-01", "2026-11-01")
    assert out["measured"] is False
    assert out["rows"] == []
    assert out["totals"]["flagged"] == 0
    assert "portal" in out["note"].lower()


def test_the_recon_names_the_system_leg_when_that_is_the_one_it_could_not_read(monkeypatch):
    monkeypatch.setattr(fr, "system_fee_per_store_day", lambda *a, **k: None)
    monkeypatch.setattr(ei, "per_store_day", lambda *a, **k: {})
    out = fr.fee_recon(_Boom(), "org", "2026-11-01", "2026-11-01")
    assert out["measured"] is False and out["rows"] == []
    assert "system" in out["note"].lower()


def test_a_readable_recon_is_measured_and_still_reconciles(monkeypatch):
    monkeypatch.setattr(fr, "system_fee_per_store_day",
                        lambda *a, **k: {("B-1", "2026-11-01"): 10.0})
    monkeypatch.setattr(ei, "per_store_day",
                        lambda *a, **k: {("B-1", "2026-11-01"): {"payment": 0.0, "fee": 10.0,
                                                                 "lines": 2}})
    out = fr.fee_recon(_Boom(), "org", "2026-11-01", "2026-11-01")
    assert out["measured"] is True and out["note"] == ""
    assert len(out["rows"]) == 1 and out["totals"]["flagged"] == 0
