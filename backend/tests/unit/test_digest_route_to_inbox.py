"""THE PER-MANAGER DIGESTS DEREFERENCE THE SAME ROUTE — so ePay was one config row, not a sibling.

Owner, 2026-11-24, chose "all five plus ePay discrepancy". ePay is NOT one of the five: those are
company-wide alerts through `closing.router._send_alert`, while ePay (and zero-sales, bill-pay, the
manager follow-ups and the store-visit digests) are ADDRESSED per-manager digests through
`notify/digest_delivery.deliver_digests`. Fixing ePay alone would have been the patchwork CLAUDE.md
forbids, so the route is read in the digest ONE HOME and every digest scope gains the choice at once.

These pin the mechanism, over the REAL `deliver_digests` and the REAL inbox:
  · a company whose route says `inbox` gets ONE item and no addressed mail;
  · the same findings tomorrow OVERWRITE it — the body is sorted and carries no date;
  · `both` keeps the mail AND files the item;
  · a scope that cannot NAME its findings never goes quiet (`item_label` absent → mail as before);
  · an un-run migration never goes quiet either;
  · the item CLOSES itself when the findings clear;
  · and one company's item is never filed under another's.
"""
import asyncio

import pytest

from app.modules.core import notification_inbox as NI
from app.modules.notify import digest_delivery as DD
from tests.fakes.supabase import FakeSupabase

HOUSE = NI.HOUSE_ORG
TENANT = "44444444-4444-4444-4444-444444444444"
INBOX = "core.notification_inbox"
SCOPE = "epay_discrepancy"


def _db(route="inbox", scope=SCOPE, org=HOUSE):
    rows = [{"org_id": org, "scope": scope, "route": route}] if route else []
    return FakeSupabase({"storeops.alert_route": rows, "storeops.alert_log": [], INBOX: []})


def _digests(*findings, to="dm@example.test"):
    """What `manager_digest.plan_digests` hands the delivery home, in its real shape."""
    return [{"kind": SCOPE, "to": to, "to_name": "A Manager", "subject": "s", "html": "<p/>",
             "items": [{"store_code": s, "close_date": d, "kind": k,
                        "ref_key": f"{SCOPE}|2026-11-24|{to}|{s}|{d}|{k}"}
                       for s, d, k in findings]}]


def _label(i):
    return f"{i['store_code']} {i['close_date']} {i['kind']}"


@pytest.fixture(autouse=True)
def _transport(monkeypatch):
    """The addressed channels, stubbed to DELIVER. What is under test is which route the findings
    take, not whether this container has an email provider configured — without this, every
    `sent` would be 0 because the real transport refuses, and a test asserting "the mail still
    went out" could not tell that apart from the silence it exists to forbid."""
    calls = []

    async def _send(ch, address, built, wa_filename, from_name="", org_id=None, scope=""):
        # `org_id`/`scope` mirror the real `_send` (§NOTIFY-02): a digest's send is recorded against
        # the tenant it belongs to, and the record names which digest it was.
        calls.append((ch, address))
        return True
    monkeypatch.setattr(DD, "_send", _send)
    return calls


def _deliver(db, digests, *, org=HOUSE, label=_label, dry_run=False):
    """`deliver_digests` is handed a STOREOPS-bound client in production (the ePay sweep's `so`),
    so it is handed one here too — the inbox write has to reach `core` through it."""
    return asyncio.run(DD.deliver_digests(
        db.schema("storeops"), org, SCOPE, digests,
        build=lambda name, items: {"subject": "s", "html": "<p/>", "text": "t"},
        wa_filename="x.txt", channels_ok={"email": True, "whatsapp": False},
        dry_run=dry_run, preview_item=_label, item_label=label,
        deep_link="/closing/epay-recon"))


# ── the pure derivation ────────────────────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_a_finding_two_managers_were_both_mailed_is_one_line_for_the_company():
    two = _digests(("S1", "2026-11-20", "fee"), to="dm@x.test") + \
        _digests(("S1", "2026-11-20", "fee"), ("S2", "2026-11-21", "payment"), to="rd@x.test")
    item = DD.company_item(SCOPE, two, _label)
    assert item["affected"] == ["S1 2026-11-20 fee", "S2 2026-11-21 payment"]
    assert item["title"] == "2 open epay discrepancy findings"


@pytest.mark.unit
def test_the_body_does_not_depend_on_recipient_order():
    a = DD.company_item(SCOPE, _digests(("S2", "2026-11-21", "fee"), ("S1", "2026-11-20", "fee")),
                        _label)
    b = DD.company_item(SCOPE, _digests(("S1", "2026-11-20", "fee"), ("S2", "2026-11-21", "fee")),
                        _label)
    assert a == b, "an item whose text moves with recipient order would re-nag at random"


@pytest.mark.unit
def test_the_item_carries_no_date_of_its_own():
    item = DD.company_item(SCOPE, _digests(("S1", "2026-11-20", "fee")), _label)
    assert "2026-11-24" not in item["body"] and "2026-11-24" not in item["title"], \
        "today's date in the body is exactly what made every recurrence a new message"
    assert item["inbox_key"] == f"digest:{SCOPE}", "one open item per scope, per company"


@pytest.mark.unit
def test_no_findings_is_no_item():
    assert DD.company_item(SCOPE, [], _label) is None
    assert DD.company_item(SCOPE, _digests(), _label) is None


# ── the route, over the real delivery home ─────────────────────────────────────────────────────────
@pytest.mark.unit
def test_a_declared_inbox_scope_files_one_item_and_mails_nobody(monkeypatch):
    monkeypatch.setattr(DD, "deliver_recipient",
                        lambda *a, **k: pytest.fail("an inbox-routed digest must not be addressed"))
    db = _db()
    sent, skipped, planned = _deliver(db, _digests(("S1", "2026-11-20", "fee")))
    assert (sent, skipped) == (0, 0)
    assert planned[0]["route"] == "inbox"
    items = db.rows(INBOX)
    assert len(items) == 1 and items[0]["org_id"] == HOUSE
    assert items[0]["deep_link"] == "/closing/epay-recon"
    assert [r["channel"] for r in db.rows("storeops.alert_log")] == ["inbox"]


@pytest.mark.unit
def test_the_same_findings_tomorrow_overwrite_instead_of_nagging():
    db = _db()
    for _ in range(4):
        _deliver(db, _digests(("S1", "2026-11-20", "fee")))
    items = db.rows(INBOX)
    assert len(items) == 1, "four days of the same open gap is one item"
    assert items[0]["occurrences"] == 4
    assert items[0]["read_at"] is None
    assert len(db.rows("storeops.alert_log")) == 1, "an unchanged sighting is not a new delivery"


@pytest.mark.unit
def test_a_new_finding_is_news_again():
    db = _db()
    _deliver(db, _digests(("S1", "2026-11-20", "fee")))
    NI.mark_read(db, HOUSE, all_items=True)
    _deliver(db, _digests(("S1", "2026-11-20", "fee"), ("S9", "2026-11-23", "payment")))
    items = db.rows(INBOX)
    assert len(items) == 1
    assert items[0]["read_at"] is None, "a finding they have not seen must be unread"
    assert "S9 2026-11-23 payment" in items[0]["body"]


@pytest.mark.unit
def test_the_item_closes_itself_when_the_findings_clear():
    db = _db()
    _deliver(db, _digests(("S1", "2026-11-20", "fee")))
    assert db.rows(INBOX)[0]["resolved_at"] is None
    _deliver(db, [])                      # the sweep now reports nothing
    assert db.rows(INBOX)[0]["resolved_at"] is not None
    assert NI.list_items(db, HOUSE) == [], "a closed item is off the company's open list"


@pytest.mark.unit
def test_both_keeps_the_mail_and_files_the_item():
    db = _db(route="both")
    sent, _skipped, planned = _deliver(db, _digests(("S1", "2026-11-20", "fee")))
    assert sent == 1, "`both` must still carry the addressed digest"
    assert len(db.rows(INBOX)) == 1
    assert planned[-1]["route"] == "both"


# ── the safe direction: nothing is ever silenced ───────────────────────────────────────────────────
@pytest.mark.unit
def test_a_scope_that_cannot_name_its_findings_mails_as_before():
    db = _db()
    sent, _skipped, planned = _deliver(db, _digests(("S1", "2026-11-20", "fee")), label=None)
    assert sent == 1, "a caller with no labels keeps the behaviour it has today"
    assert db.rows(INBOX) == []
    assert planned[-1]["inbox_unnameable"] is True


@pytest.mark.unit
def test_an_un_run_migration_mails_as_before(monkeypatch):
    db = _db()
    # Exactly what the one home returns when the write fails — mig 1129 un-run, or the table
    # unreachable. The I/O guard is its own; what is under test is what the DELIVERY does next.
    monkeypatch.setattr(NI, "file_item",
                        lambda *a, **k: {"state": "error", "detail": "relation does not exist"})
    sent, _skipped, planned = _deliver(db, _digests(("S1", "2026-11-20", "fee")))
    assert sent == 1, "shipping this before the route row exists must not cost one alert"
    assert planned[-1].get("inbox_unavailable") is True


@pytest.mark.unit
def test_an_undeclared_scope_is_untouched():
    db = _db(route=None)
    sent, _skipped, planned = _deliver(db, _digests(("S1", "2026-11-20", "fee")))
    assert sent == 1
    assert db.rows(INBOX) == []
    assert all("route" not in r for r in planned), \
        "a scope still on `message` gets exactly the planned rows its caller always had"


@pytest.mark.unit
def test_a_dry_run_writes_nothing():
    db = _db()
    _deliver(db, _digests(("S1", "2026-11-20", "fee")), dry_run=True)
    assert db.rows(INBOX) == [] and db.rows("storeops.alert_log") == []


# ── cross-tenant (owner 2026-10-30) ───────────────────────────────────────────────────────────────
@pytest.mark.unit
def test_one_companys_item_is_never_filed_under_another():
    db = FakeSupabase({
        "storeops.alert_route": [{"org_id": HOUSE, "scope": SCOPE, "route": "inbox"}],
        "storeops.alert_log": [], INBOX: [],
    })
    _deliver(db, _digests(("S1", "2026-11-20", "fee")), org=HOUSE)
    _deliver(db, _digests(("S1", "2026-11-20", "fee")), org=TENANT)
    items = db.rows(INBOX)
    assert len(items) == 2, "the same scope for two companies is two items"
    assert {i["org_id"] for i in items} == {HOUSE, TENANT}
    for org in (HOUSE, TENANT):
        assert [i["org_id"] for i in NI.list_items(db, org)] == [org]


@pytest.mark.unit
def test_a_tenant_can_opt_out_without_touching_the_other():
    db = FakeSupabase({
        "storeops.alert_route": [{"org_id": HOUSE, "scope": SCOPE, "route": "inbox"},
                                 {"org_id": TENANT, "scope": SCOPE, "route": "message"}],
        "storeops.alert_log": [], INBOX: [],
    })
    sent_house, _s, _p = _deliver(db, _digests(("S1", "2026-11-20", "fee")), org=HOUSE)
    sent_tenant, _s, _p = _deliver(db, _digests(("S1", "2026-11-20", "fee")), org=TENANT)
    assert sent_house == 0 and sent_tenant == 1
    assert [i["org_id"] for i in db.rows(INBOX)] == [HOUSE]
