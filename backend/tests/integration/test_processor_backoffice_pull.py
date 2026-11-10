"""THE PULL LOOP, DRIVEN OVER THE PER-STORE BACK OFFICE. Written 2026-11-10.

Integration rather than unit because this exercises the REAL loop (`portal_report_pull`), the REAL
adapter and the REAL backlog against the house database double — no browser, no portal, no network
(`tests/conftest.py` blocks every outbound socket, so this cannot reach a portal even by accident).
What it proves is the wiring the unit tests next door cannot:

  • a login that declares where its history starts is pulled for the OLDEST window it still owes,
    not for the recent window;
  • the window it answered is RECORDED, so the next run asks for the next one;
  • a window that failed is asked again; one that was answered is not;
  • the rows land in the feed's existing table, stamped with the store whose login pulled them;
  • a login that declares no history is pulled for the recent window, exactly as before.

Index §COMMCALC-57.
"""
import pytest

from app.modules.commcalc import portal_backfill as bf
from app.modules.commcalc import portal_report_pull as prp
from app.modules.commcalc import processor_backoffice_sweep as pbs

ORG = "00000000-0000-0000-0000-000000000001"
BACKLOG = "commcalc.portal_pull_backlog"
LANDED = "commcalc.raw_epay_daily_tx"

TX_CSV = (b"TransactionID,TransactionSourceID,SettlementDate,TerminalID,UserName,Product,"
          b"ProductTitle,Type,Retail,Discount,Cost,Commission\n"
          b"T-1,1,2026-01-03,TERM-9,storelogin,RTR,BOOST RTR,SALE,50.00,1.00,49.00,1.00\n")


class FakePage:
    """The handful of calls the loop makes on a page. It records the dates it was asked for, which is
    how a test can see WHICH window the pull actually covered."""

    def __init__(self):
        self.filled = {}
        self.clicked = []

    def fill(self, selector, value, timeout=None):
        self.filled[selector] = value

    def click(self, selector, timeout=None):
        self.clicked.append(selector)

    def wait_for_timeout(self, _ms):
        return None


def _src(**kw):
    row = {"id": "src-1", "org_id": ORG, "processor": pbs.CONNECTOR,
           "portal_url": "https://portal.example.com", "store_code": "B-1115",
           "portal_reports": [{"key": "daily_transaction_detail", "label": "Daily TX",
                               "target": "processor_daily_tx", "nav": ["Reports"]}],
           "portal_calibration": {"daily_transaction_detail": {
               "params": [{"selector": "#from", "kind": "fill", "value": "{date_from}"},
                          {"selector": "#to", "kind": "fill", "value": "{date_to}"}],
               "export_selector": "#export"}}}
    row.update(kw)
    return row


@pytest.fixture
def driven(monkeypatch):
    """The loop with its two browser-only steps replaced by the export we already have."""
    monkeypatch.setattr(prp, "navigate_to_report", lambda page, src, spec: True)
    monkeypatch.setattr(prp, "download_report", lambda page, src, spec, tmp: ("/tmp/x.csv", "x.csv"))
    monkeypatch.setattr(prp, "_read_download", lambda path: TX_CSV)
    monkeypatch.setattr(prp, "run_report", lambda page, src, key: True)
    from app.modules.storeops import merchant_ids
    monkeypatch.setattr(merchant_ids, "resolve_map", lambda *a, **k: {})
    return FakePage()


def test_a_login_that_owes_history_is_pulled_for_its_oldest_owed_window(driven, fake_supabase,
                                                                       monkeypatch):
    monkeypatch.setattr(bf, "plan_for",
                        lambda src_row, today=None: bf.plan_windows("2026-01-01",
                                                                    today="2026-01-20", chunk_days=7))
    out = prp.pull_reports_on_page(fake_supabase, ORG, _src(history_from="2026-01-01"), driven)
    assert (out["date_from"], out["date_to"]) == ("2026-01-01", "2026-01-07")
    assert driven.filled == {"#from": "2026-01-01", "#to": "2026-01-07"}
    assert out["rows_ingested"] == 1 and out["delivered"] is True
    rows = fake_supabase._store[LANDED]
    assert rows[0]["store_code"] == "B-1115" and rows[0]["org_id"] == ORG
    # the window it answered is recorded, so tomorrow's run asks for the next one
    logged = fake_supabase._store[BACKLOG]
    assert len(logged) == 1
    assert (logged[0]["window_from"], logged[0]["window_to"]) == ("2026-01-01", "2026-01-07")
    assert logged[0]["state"] == "done" and logged[0]["attempts"] == 1
    assert out["backfill_progress"]["owed"] == 2


def test_the_next_run_moves_on_to_the_next_window(driven, fake_supabase, monkeypatch):
    monkeypatch.setattr(bf, "plan_for",
                        lambda src_row, today=None: bf.plan_windows("2026-01-01",
                                                                    today="2026-01-20", chunk_days=7))
    fake_supabase.seed(BACKLOG, [{"org_id": ORG, "source_id": "src-1", "report_key": bf.ALL_REPORTS,
                                  "window_from": "2026-01-01", "window_to": "2026-01-07",
                                  "state": "done", "attempts": 1}])
    out = prp.pull_reports_on_page(fake_supabase, ORG, _src(history_from="2026-01-01"), driven)
    assert (out["date_from"], out["date_to"]) == ("2026-01-08", "2026-01-14")


def test_a_window_that_failed_is_asked_again(driven, fake_supabase, monkeypatch):
    monkeypatch.setattr(bf, "plan_for",
                        lambda src_row, today=None: bf.plan_windows("2026-01-01",
                                                                    today="2026-01-20", chunk_days=7))
    fake_supabase.seed(BACKLOG, [{"org_id": ORG, "source_id": "src-1", "report_key": bf.ALL_REPORTS,
                                  "window_from": "2026-01-01", "window_to": "2026-01-07",
                                  "state": "failed", "attempts": 1}])
    out = prp.pull_reports_on_page(fake_supabase, ORG, _src(history_from="2026-01-01"), driven)
    assert (out["date_from"], out["date_to"]) == ("2026-01-01", "2026-01-07")
    row = [r for r in fake_supabase._store[BACKLOG]
           if r["window_from"] == "2026-01-01"][0]
    assert row["attempts"] == 2, "the retry counts, so an unreachable window cannot loop forever"


def test_a_login_that_declares_no_history_keeps_the_recent_window_and_writes_no_backlog(
        driven, fake_supabase):
    out = prp.pull_reports_on_page(fake_supabase, ORG, _src(), driven)
    assert out["rows_ingested"] == 1
    assert BACKLOG not in fake_supabase._store, "nothing is owed, so nothing is recorded"
    d_from, d_to = prp.date_range(_src(), pbs.CONNECTOR)
    assert (out["date_from"], out["date_to"]) == (d_from, d_to)


def test_a_backlog_table_that_does_not_exist_yet_changes_nothing(driven, monkeypatch):
    """Migration 1116 unapplied: every call is guarded, so the pull is today's pull."""
    class NoTable:
        def schema(self, _s):
            return self

        def table(self, _t):
            raise RuntimeError("relation does not exist")

    out = prp.pull_reports_on_page(NoTable(), ORG, _src(history_from="2026-01-01"), driven)
    d_from, d_to = prp.date_range(_src(), pbs.CONNECTOR)
    assert (out["date_from"], out["date_to"]) == (d_from, d_to), \
        "no memory of what was answered means no backfill — never January re-pulled every night"
    assert out["backfill_progress"]["blocked"] is True
    assert "1116" in out["status"], "and the operator is told why the history has not started"
