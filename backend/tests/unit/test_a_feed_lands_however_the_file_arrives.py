"""A FEED LANDS THE SAME WHETHER IT WAS PULLED OR DELIVERED (index §COMMCALC-75).

FOUND 2026-11-14. The processor back office refuses automated clients at its edge — a security
appliance in front of it answers "The requested URL was rejected" to the server, so the per-store
pull (§COMMCALC-58) cannot fetch the file and never will. The owner's instruction was to find a way
to GET the report, not a way through their firewall.

Everything downstream of the fetch was already built and is not the problem: the parse, the terminal
→ store resolution, the idempotent upsert into `commcalc.raw_epay_daily_tx`, the bill-pay and fee
recons that read it. ONLY THE TRANSPORT was broken.

THE CLASS: a feed's landing must not be a property of how its file arrived. Before this, these rows
could be reached one way only — the browser pull. A file of the very same report, emailed by the
processor on a schedule, forwarded by a person, or picked off the Upload page, had nowhere to land.
So `epay_ingest.from_upload` is the ONE arrival door for a delivered file of this feed, and it lands
through the SAME ingest the pull uses rather than a sibling path that would drift from it.
"""
import pytest

from app.modules.commcalc import epay_ingest as ei
from tests.fakes.supabase import FakeSupabase

RAW, LOG, TRACE = "commcalc.raw_epay_daily_tx", "commcalc.upload_log", "commcalc.upload_trace"

# Two lines of the report as the back office publishes it (columns per ei.COLUMNS).
FILE_ROWS = [
    {"TransactionID": "88001", "TransactionSourceID": "1", "SettlementDate": "2026-11-13",
     "TerminalID": "T-9001", "UserName": "libertyave", "ProductTitle": "BOOST RTR",
     "Type": "SALE", "Retail": "50.00", "Commission": "2.50"},
    {"TransactionID": "88002", "TransactionSourceID": "1", "SettlementDate": "2026-11-13",
     "TerminalID": "T-9001", "UserName": "libertyave", "ProductTitle": "EPAY SERVICE FEE",
     "Type": "FEE", "Retail": "1.00", "Commission": "0.00"},
]


@pytest.fixture
def db():
    return FakeSupabase({RAW: [], LOG: [], TRACE: []})


@pytest.fixture(autouse=True)
def _no_registry(monkeypatch):
    """The terminal → store registry is a DB read of another module; this suite is about ARRIVAL."""
    monkeypatch.setattr("app.modules.storeops.merchant_ids.resolve_map",
                        lambda org_id, processor: {"T-9001": "B-1115"})


def test_a_delivered_file_lands_exactly_where_the_pull_lands(db):
    out = ei.from_upload(db, "org-1", FILE_ROWS, filename="Transaction Detail 11-13.csv")
    assert out["success"] is True and out["saved"] == 2
    rows = db.rows(RAW)
    assert {r["transaction_id"] for r in rows} == {"88001", "88002"}
    # the SAME resolution the pull gets — the declared registry places the terminal
    assert {r["store_code"] for r in rows} == {"B-1115"}
    assert [r["is_fee"] for r in sorted(rows, key=lambda r: r["transaction_id"])] == [False, True]


def test_the_arrival_is_recorded_so_the_tile_can_see_it(db):
    ei.from_upload(db, "org-1", FILE_ROWS, filename="Transaction Detail 11-13.csv")
    lg = db.rows(LOG)[0]
    assert lg["file_type"] == "epay_daily_tx" and lg["rows_saved"] == 2
    assert lg["filename"] == "Transaction Detail 11-13.csv"


def test_a_file_whose_terminal_is_not_declared_names_the_terminal(db):
    out = ei.from_upload(db, "org-1", [{**FILE_ROWS[0], "TerminalID": "T-NEW"}])
    assert out["success"] is True
    assert [t["terminal_id"] for t in out["unresolved_terminals"]] == ["T-NEW"]


def test_a_store_may_be_declared_for_a_file_that_came_through_one_store_s_login(db):
    ei.from_upload(db, "org-1", [{**FILE_ROWS[0], "TerminalID": "T-NEW"}], store_code="B-2200")
    assert db.rows(RAW)[0]["store_code"] == "B-2200"


def test_a_file_that_is_not_this_report_is_a_skip_naming_what_it_looked_for(db):
    out = ei.from_upload(db, "org-1", [{"Store": "B-1115", "Total": "10"}])
    assert out["success"] is False and out["skipped"] == "epay_no_transactions"
    assert "TransactionID" in out["note"] and "Store" in out["note"]
    assert db.rows(RAW) == [] and db.rows(LOG) == []


def test_an_empty_file_is_never_a_green_tick(db):
    out = ei.from_upload(db, "org-1", [])
    assert out["success"] is False and out["saved"] == 0 and db.rows(LOG) == []


def test_the_journal_is_written_through_its_one_home(db):
    import inspect
    src = inspect.getsource(ei)
    for tbl in ('"upload_log"', "'upload_log'", '"upload_trace"', "'upload_trace'"):
        assert f".table({tbl})" not in src, "the journals are written through ingest_record only"
    assert "ingest_record" in src


def test_the_pull_and_the_delivery_share_one_ingest():
    import inspect
    body = inspect.getsource(ei.from_upload)
    assert "ingest(" in body, "a delivered file must land through the SAME ingest the pull uses"
