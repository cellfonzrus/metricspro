"""THE ONE SENDER DEREFERENCES THE ROUTE — so every scope is fixed at once, for every tenant.

`closing.router._send_alert` is the single door all five data-upload senders go through. These pin
that the door reads `storeops.alert_route` and, for a company+scope declared `inbox`:
  · files ONE inbox item and carries NO email or WhatsApp;
  · files the SAME item again tomorrow instead of a second one;
  · records the delivery in the existing `storeops.alert_log` as channel `inbox` — no second log;
  · and, with no stable key, REFUSES to go quiet: the message still goes out and says why.
"""
import asyncio

import pytest

from app.modules.core import notification_inbox as NI
from tests.fakes.supabase import FakeSupabase

HOUSE = NI.HOUSE_ORG
TENANT = "33333333-3333-3333-3333-333333333333"
INBOX = "core.notification_inbox"


def _db(route="inbox", scope="connector", org=HOUSE):
    return FakeSupabase({
        "storeops.alert_route": [{"org_id": HOUSE, "scope": scope, "route": route}],
        "storeops.alert_recipient": [{"org_id": org, "scope": scope, "email": "ops@example.test",
                                      "via_email": True}],
        "storeops.alert_log": [],
        INBOX: [],
    })


def _send(db, org, scope, subject, text, ref, inbox_key=None):
    """The REAL sender, run to completion — no second implementation of the decision under test."""
    from app.modules.closing.router import _send_alert
    return asyncio.run(_send_alert(db, org, scope, subject, text, ref, inbox_key=inbox_key))


@pytest.mark.unit
def test_a_declared_inbox_scope_files_an_item_and_mails_nobody(monkeypatch):
    from app.modules.notify import digest_delivery as delivery
    monkeypatch.setattr(delivery, "deliver_recipient",
                        lambda *a, **k: pytest.fail("an inbox-routed alert must not be delivered"))
    db = _db()
    out = _send(db, HOUSE, "connector", "Payment detail is 2 day(s) behind", "open imports",
                      "freshness:house:payment_detail:stuck-at-2026-11-22",
                      inbox_key="freshness:house:payment_detail")
    assert out["route"] == "inbox" and out["sent"] == 0
    items = db.rows(INBOX)
    assert len(items) == 1
    assert items[0]["org_id"] == HOUSE and items[0]["occurrences"] == 1
    assert items[0]["title"] == "Payment detail is 2 day(s) behind"
    logged = db.rows("storeops.alert_log")
    assert [r["channel"] for r in logged] == ["inbox"], "the inbox delivery is recorded like any other"


@pytest.mark.unit
def test_the_same_finding_tomorrow_overwrites_instead_of_adding():
    db = _db()
    for _ in range(3):
        _send(db, HOUSE, "connector", "DLAR sweep errored", "check the connector",
                    "connector:dlar_sweep_config:7:since-2026-11-20:errored",
                    inbox_key="connector:dlar_sweep_config:7")
    items = db.rows(INBOX)
    assert len(items) == 1, "three days of the same finding is one inbox item"
    assert items[0]["occurrences"] == 3
    assert len(db.rows("storeops.alert_log")) == 1, "an unchanged sighting is not a new delivery"


@pytest.mark.unit
def test_without_a_stable_key_the_message_still_goes_out_and_says_why():
    db = _db()
    out = _send(db, HOUSE, "connector", "something broke", "body", "ref-of-the-day")
    assert out.get("inbox_key_missing") is True
    assert db.rows(INBOX) == [], "nothing is filed under a key that would change tomorrow"


@pytest.mark.unit
def test_a_tenant_that_declared_nothing_keeps_the_behaviour_it_has_today():
    db = FakeSupabase({
        "storeops.alert_route": [],
        "storeops.alert_recipient": [{"org_id": TENANT, "scope": "connector",
                                      "email": "ops@tenant.test", "via_email": True}],
        "storeops.alert_log": [], INBOX: [],
    })
    out = _send(db, TENANT, "connector", "subject", "body", "ref", inbox_key="connector:x:1")
    assert out["route"] == "message"
    assert db.rows(INBOX) == []


@pytest.mark.unit
def test_one_tenants_item_is_never_filed_under_another(monkeypatch):
    db = _db()
    _send(db, HOUSE, "connector", "house finding", "b", "r1", inbox_key="connector:a:1")
    _send(db, TENANT, "connector", "tenant finding", "b", "r2", inbox_key="connector:a:1")
    items = db.rows(INBOX)
    assert len(items) == 2, "the same key for two companies is two items"
    assert {i["org_id"] for i in items} == {HOUSE, TENANT}
    for org in (HOUSE, TENANT):
        mine = NI.list_items(db, org)
        assert [i["org_id"] for i in mine] == [org]
