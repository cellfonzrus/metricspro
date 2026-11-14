import inspect

import pytest

from app.modules.commcalc import epay_sweep as es
from app.modules.commcalc import ingest_record as ir
from tests.fakes.supabase import FakeSupabase

TRACE, LOG = "commcalc.upload_trace", "commcalc.upload_log"


def db():
    """The ONE database double, seeded with the two ingest journals empty."""
    return FakeSupabase({TRACE: [], LOG: []})


class JournalsNotMigrated:
    """A client for which neither journal exists — mig 202 / 007 unrun. Not a database double: it
    answers every relation the way PostgREST answers a missing one, which is the only behaviour
    under test here."""

    def schema(self, _s):
        return self

    def table(self, name):
        raise RuntimeError(f"relation commcalc.{name} does not exist")


MONTH_SPEC = {"table": "raw_mi", "file_type": "mi_report", "label": "MI/ATU"}
DTD_SPEC = {"table": "commcalc.raw_epay_daily_tx", "file_type": "epay_daily_tx",
            "label": "Daily Transaction Detail"}


def test_the_journals_have_one_writer_and_the_router_dereferences_it():
    from app.modules.commcalc import router
    assert router._TRACE_TARGET_TABLE is ir.TARGET_TABLE, \
        "a second copy of which table an upload_type lands in"
    src = inspect.getsource(router)
    for tbl in ('"upload_trace"', "'upload_trace'"):
        assert f".table({tbl}).insert" not in src, \
            "router must insert into upload_trace only through ingest_record"


def test_a_schema_qualified_table_is_recorded_under_one_spelling():
    assert ir.table_name("commcalc.raw_epay_daily_tx") == "raw_epay_daily_tx"
    assert ir.table_name("raw_mi") == "raw_mi"
    assert ir.table_name("") is None


def test_an_unattended_landing_is_recorded_in_both_journals():
    c = db()
    out = ir.sweep_landed(c, "org-1", upload_type="comp_report", target_table="raw_comp_report",
                          label="Comprehensive Comp", rows=4390, mode="replace_by_day",
                          periods={"October 2026": 1200, "November 2026": 3190},
                          date_counts={"2026-11-11": 300}, period_label="November 2026")
    assert out["recorded"] is True
    tr = c.rows(TRACE)[0]
    assert tr["upload_type"] == "comp_report" and tr["target_table"] == "raw_comp_report"
    assert tr["rows_saved"] == 4390 and tr["status"] == "ok"
    # 'portal pull' is how the tile names it — never 'manual upload', which is what it said for a month.
    assert tr["source"] == "portal"
    assert tr["periods"]["November 2026"] == 3190
    lg = c.rows(LOG)[0]
    assert lg["file_type"] == "comp_report" and lg["rows_saved"] == 4390
    assert lg["period"] == "November 2026"


def test_a_zero_row_pull_is_not_recorded_as_a_landing():
    c = db()
    out = ir.sweep_landed(c, "org-1", upload_type="comp_report", target_table="raw_comp_report",
                          label="Comprehensive Comp", rows=0, mode="replace_by_day")
    assert out["recorded"] is False and c.rows(TRACE) == [] and c.rows(LOG) == []


def test_a_journal_that_is_not_there_never_breaks_the_pull():
    c = JournalsNotMigrated()
    out = ir.sweep_landed(c, "org-1", upload_type="mi_report", target_table="raw_mi",
                          label="MI/ATU", rows=10, mode="replace")
    assert out == {"trace": False, "log": False, "recorded": False}


def test_landing_records_and_recalculates_in_one_call(monkeypatch):
    seen = {}

    def fake_landed(client, org_id, **kw):
        seen["kw"] = kw
        return {"queued": True}

    monkeypatch.setattr("app.modules.commcalc.auto_calc.landed", fake_landed)
    c = db()
    got = es.landed(c, "org-1", MONTH_SPEC, "mi", periods=["October 2026"], rows=45720,
                    mode="replace", period_label="October 2026")
    assert got == {"queued": True}
    assert seen["kw"]["table"] == "raw_mi" and seen["kw"]["rows"] == 45720
    assert c.rows(TRACE) and c.rows(LOG)


def test_the_dtd_hook_lands_in_the_same_one_call(monkeypatch):
    monkeypatch.setattr("app.modules.commcalc.auto_calc.landed",
                        lambda client, org_id, **kw: {"queued": False, "reason": "not_a_calc_input"})
    c = db()
    es.landed(c, "org-1", DTD_SPEC, "epay_daily_tx", periods=[], rows=417, mode="upsert")
    assert c.rows(TRACE)[0]["target_table"] == "raw_epay_daily_tx"


def test_no_storage_path_can_recalculate_without_recording(monkeypatch):
    src = inspect.getsource(es)
    assert src.count("_auto_calc.landed(") == 1, \
        "the post-landing hook has ONE call site — a second one is a path that can land silently"
    body = inspect.getsource(es.landed)
    assert "_auto_calc.landed(" in body and "_ingest_record.sweep_landed(" in body, \
        "the record and the recalculation must be the same call"
    # and every storage path reaches it
    for fn in (es._store_rows_by_day, es._process_report, es.ingest_daily_tx):
        assert "landed(" in inspect.getsource(fn), fn.__name__


def test_a_recorded_landing_never_claims_rows_the_pull_did_not_save():
    c = db()
    ir.sweep_landed(c, "org-1", upload_type="payment_detail", target_table="raw_payment_detail",
                    label="Commission Payment Detail", rows=21602, mode="replace_by_day")
    tr = c.rows(TRACE)[0]
    assert tr["rows_in"] == tr["rows_saved"] == 21602


def test_the_sweep_does_not_keep_its_own_journal_insert():
    src = inspect.getsource(es)
    for tbl in ('"upload_log"', "'upload_log'", '"upload_trace"', "'upload_trace'"):
        assert f".table({tbl})" not in src, "the sweep writes the journals through ingest_record only"


@pytest.mark.parametrize("bad", ["", None])
def test_an_undeclared_target_table_falls_back_to_the_declared_map(bad):
    c = db()
    ir.sweep_landed(c, "org-1", upload_type="mi_report", target_table=bad, label="MI/ATU",
                    rows=5, mode="replace")
    assert c.rows(TRACE)[0]["target_table"] == "raw_mi"
