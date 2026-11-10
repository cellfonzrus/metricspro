"""A per-STORE portal login, on the pull loop that already exists. Written RED first, 2026-11-10.

WHY THIS FEED EXISTS. Owner, 2026-11-10: the payment processor's back office gives every store its
own login, and he asked for every available report pulled per store, from the first of the year and
then daily, for the processor reconciliation.

WHAT IS NEW, AND IT IS EXACTLY TWO FACTS. The pull loop, the login, the durable session, the cold
restore, the cron, the calibration ladder and the honest-failure wording are the EXISTING machinery
(`portal_report_pull`, index §COMMCALC-07) — this adapter supplies the loop's four contract functions
and nothing else. The two facts that had no home are:

  1. **a login can belong to a STORE** — every row pulled through a store's own login is that store's,
     which is a fact no connector could state before (`data_source.store_code`);
  2. **a pull can owe HISTORY** — `portal_backfill`, its own one home, tested next door.

RULE TWO, and the all-tenants rule. No vendor, portal, tenant or carrier name appears in this module:
the address, the report list, the selectors, the landing target and the store are all DECLARED on the
source row. A tenant that has declared nothing identifies nothing and says so — it never falls back to
another tenant's words, and it never guesses a store.
"""
import pytest

pytestmark = pytest.mark.unit

ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "22222222-2222-2222-2222-222222222222"

TX_CSV = (b"TransactionID,TransactionSourceID,SettlementDate,TerminalID,UserName,Product,"
          b"ProductTitle,Type,Retail,Discount,Cost,Commission\n"
          b"T-1,1,2026-01-03,TERM-9,storelogin,RTR,BOOST RTR,SALE,50.00,1.00,49.00,1.00\n"
          b"T-2,1,2026-01-03,TERM-9,storelogin,FEE,EPAY SERVICE FEE,FEE,3.00,0,3.00,0\n")


def _src(**kw):
    row = {"id": "src-1", "org_id": ORG_A, "processor": "processor_backoffice",
           "portal_url": "https://portal.example.com", "store_code": "B-1115"}
    row.update(kw)
    return row


# ── the loop's contract ──────────────────────────────────────────────────────────────────────────
def test_the_adapter_is_registered_on_the_one_pull_loop():
    from app.modules.commcalc import portal_report_pull as prp
    from app.modules.commcalc import processor_backoffice_sweep as pbs
    assert any("processor_backoffice_sweep" in m for m in prp.ADAPTER_MODULES), \
        "the adapter must be registered, not discovered by accident"
    assert prp.adapter_for(pbs.CONNECTOR) is not None, "the loop must resolve this connector"
    assert prp.is_pullable(pbs.CONNECTOR)
    for name in prp.REQUIRED:
        assert callable(getattr(pbs, name, None)), name


def test_the_connector_is_a_kind_not_a_vendor_and_the_module_names_no_portal():
    """RULE TWO. The address is config; a vendor's name, host or report wording may not be here."""
    import os
    from app.modules.commcalc import processor_backoffice_sweep as pbs
    text = open(os.path.join(os.path.dirname(pbs.__file__),
                             "processor_backoffice_sweep.py")).read().lower()
    for banned in ("epayworldwide", "backoffice.epay", "cellfonz", "luxelink", "vzone", "boost"):
        assert banned not in text, banned
    assert pbs.portal_descriptor(pbs.CONNECTOR)["base_url"] is None, \
        "the portal's address is the operator's declaration, never a constant here"


def test_an_unconfigured_source_declares_no_report_and_says_what_to_do():
    from app.modules.commcalc import processor_backoffice_sweep as pbs
    assert pbs.report_specs(pbs.CONNECTOR, None, src_row=_src()) == ()
    hint = pbs.setup_hint(_src())
    assert "report" in hint.lower() and len(hint) > 40


def test_declared_reports_become_specs_carrying_their_landing_target():
    from app.modules.commcalc import processor_backoffice_sweep as pbs
    src = _src(portal_reports=[{"key": "Daily Transaction Detail", "label": "Daily TX",
                                "target": "processor_daily_tx", "nav": ["Reports", "Daily"]},
                               {"key": "commission_summary", "label": "Commission"}])
    specs = pbs.report_specs(pbs.CONNECTOR, src.get("portal_reports"), src_row=src)
    assert [s["key"] for s in specs] == ["daily_transaction_detail", "commission_summary"]
    assert specs[0]["target"] == "processor_daily_tx"
    assert specs[1]["target"] == "", "an undeclared target is empty, never a guessed default"


def test_the_report_key_has_one_spelling():
    from app.modules.commcalc import processor_backoffice_sweep as pbs
    for raw in ("Daily Transaction Detail", "daily-transaction-detail",
                {"key": " Daily Transaction Detail "}):
        assert pbs.spec_key(raw) == "daily_transaction_detail"
    assert pbs.spec_key(None) == ""


# ── parsing ──────────────────────────────────────────────────────────────────────────────────────
def test_the_export_becomes_records_the_feeds_own_ingest_understands():
    """The columns are mapped through the ONE column vocabulary (§COMMCALC-38), so a portal that
    prints 'Trans ID' instead of 'TransactionID' still lands."""
    from app.modules.commcalc import processor_backoffice_sweep as pbs
    from app.modules.commcalc import epay_ingest
    recs = pbs.records_from_payload(TX_CSV, filename="tx.csv")
    assert len(recs) == 2
    assert recs[0]["TransactionID"] == "T-1"
    assert set(recs[0]) <= set(epay_ingest.COLUMNS), "no field this feed's ingest does not know"
    renamed = TX_CSV.replace(b"TransactionID", b"Trans ID").replace(b"SettlementDate", b"Settle Date")
    assert pbs.records_from_payload(renamed, filename="tx.csv")[0]["TransactionID"] == "T-1"


def test_a_payload_with_no_recognizable_header_yields_nothing_rather_than_garbage():
    from app.modules.commcalc import processor_backoffice_sweep as pbs
    assert pbs.records_from_payload(b"total,only\n5,6\n", filename="x.csv") == []
    assert pbs.records_from_payload(b"", filename="x.csv") == []
    assert pbs.records_from_payload(None) == []


def test_the_columns_a_report_published_are_reported_as_evidence():
    """A report nobody has mapped yet must still tell us what it contains — that is how the next
    report gets mapped instead of guessed at."""
    from app.modules.commcalc import processor_backoffice_sweep as pbs
    fp = pbs.shape_of(b"A,B,C\n1,2,3\n4,5,6\n", filename="r.csv")
    assert fp["columns"] == ["A", "B", "C"] and fp["rows"] == 2


# ── landing ──────────────────────────────────────────────────────────────────────────────────────
class _Fake:
    """The smallest client the landing uses: one upsert sink per table."""

    def __init__(self):
        self.written = {}

    def schema(self, _s):
        return self

    def table(self, name):
        self._t = name
        return self

    def upsert(self, payload, **_kw):
        self.written.setdefault(self._t, []).extend(
            payload if isinstance(payload, list) else [payload])
        return self

    def execute(self):
        class R:
            data = []
        return R()


def test_rows_land_in_the_feeds_existing_table_stamped_with_the_logins_store(monkeypatch):
    from app.modules.commcalc import processor_backoffice_sweep as pbs
    from app.modules.storeops import merchant_ids
    monkeypatch.setattr(merchant_ids, "resolve_map", lambda *a, **k: {})
    client = _Fake()
    spec = {"key": "daily_transaction_detail", "label": "Daily TX", "target": "processor_daily_tx"}
    out = pbs.ingest_report(client, ORG_A, _src(), pbs.CONNECTOR, spec, TX_CSV, "tx.csv")
    rows = client.written["raw_epay_daily_tx"]
    assert out["rows_ingested"] == 2 and out["delivered"] is True
    assert {r["store_code"] for r in rows} == {"B-1115"}, "the login's own store, not a guess"
    assert {r["org_id"] for r in rows} == {ORG_A}


def test_a_declared_terminal_mapping_still_wins_over_the_login(monkeypatch):
    """The registry is the DECLARED mapping; the login is what answers when it has nothing to say."""
    from app.modules.commcalc import processor_backoffice_sweep as pbs
    from app.modules.storeops import merchant_ids
    monkeypatch.setattr(merchant_ids, "resolve_map", lambda *a, **k: {"TERM-9": "B-2778"})
    client = _Fake()
    spec = {"key": "daily_transaction_detail", "target": "processor_daily_tx"}
    pbs.ingest_report(client, ORG_A, _src(), pbs.CONNECTOR, spec, TX_CSV, "tx.csv")
    assert {r["store_code"] for r in client.written["raw_epay_daily_tx"]} == {"B-2778"}


def test_a_login_with_no_store_and_no_mapping_refuses_rather_than_landing_homeless_money(monkeypatch):
    from app.modules.commcalc import processor_backoffice_sweep as pbs
    from app.modules.storeops import merchant_ids
    monkeypatch.setattr(merchant_ids, "resolve_map", lambda *a, **k: {})
    client = _Fake()
    spec = {"key": "daily_transaction_detail", "target": "processor_daily_tx"}
    out = pbs.ingest_report(client, ORG_A, _src(store_code=None), pbs.CONNECTOR, spec,
                            TX_CSV, "tx.csv")
    assert out["delivered"] is False and out["rows_ingested"] == 0
    assert "store" in (out.get("error") or "").lower()
    assert client.written == {}, "nothing may land under no store"


def test_a_report_with_no_declared_target_is_reported_never_silently_dropped(monkeypatch):
    from app.modules.commcalc import processor_backoffice_sweep as pbs
    client = _Fake()
    spec = {"key": "commission_summary", "label": "Commission summary", "target": ""}
    out = pbs.ingest_report(client, ORG_A, _src(), pbs.CONNECTOR, spec, b"A,B\n1,2\n", "c.csv")
    assert out["delivered"] is False and out["rows_ingested"] == 0
    assert out["shape"]["columns"] == ["A", "B"], "what it contains is kept as evidence"
    assert "target" in (out.get("error") or "").lower()
    assert out["unresolved"], "an operator has something to act on"
    assert client.written == {}


def test_a_target_this_version_does_not_know_refuses_by_name(monkeypatch):
    from app.modules.commcalc import processor_backoffice_sweep as pbs
    client = _Fake()
    spec = {"key": "x", "target": "somewhere_else"}
    out = pbs.ingest_report(client, ORG_A, _src(), pbs.CONNECTOR, spec, TX_CSV, "tx.csv")
    assert out["delivered"] is False and "somewhere_else" in (out.get("error") or "")


# ── every tenant, and no leakage between them ────────────────────────────────────────────────────
def test_two_tenants_each_get_their_own_declaration_and_never_each_others(monkeypatch):
    from app.modules.commcalc import processor_backoffice_sweep as pbs
    from app.modules.storeops import merchant_ids
    monkeypatch.setattr(merchant_ids, "resolve_map", lambda *a, **k: {})
    a = _src(org_id=ORG_A, store_code="B-1115",
             portal_reports=[{"key": "daily_transaction_detail", "target": "processor_daily_tx"}])
    b = _src(id="src-2", org_id=ORG_B, store_code="L-200", portal_reports=[])
    assert [s["key"] for s in pbs.report_specs(pbs.CONNECTOR, a["portal_reports"], src_row=a)] \
        == ["daily_transaction_detail"]
    assert pbs.report_specs(pbs.CONNECTOR, b["portal_reports"], src_row=b) == (), \
        "tenant B declared nothing, so tenant B identifies nothing"
    client = _Fake()
    spec = {"key": "daily_transaction_detail", "target": "processor_daily_tx"}
    pbs.ingest_report(client, ORG_B, b, pbs.CONNECTOR, spec, TX_CSV, "tx.csv")
    rows = client.written["raw_epay_daily_tx"]
    assert {r["org_id"] for r in rows} == {ORG_B} and {r["store_code"] for r in rows} == {"L-200"}
