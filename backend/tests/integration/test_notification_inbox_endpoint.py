"""THE INBOX OVER THE REAL APP — the surface a company actually opens.

Pinned here: the routes exist and resolve, the gate is the platform's existing admin-ish one, a
caller only ever reads THEIR OWN company's items (the cross-tenant rule, owner 2026-10-30), reading
clears the badge, and a company with nothing filed is told that rather than shown a bare zero.
"""
import pytest

from app.modules.core import notification_inbox as NI
from tests.fakes.supabase import FakeSupabase

HOUSE = NI.HOUSE_ORG
TENANT = "44444444-4444-4444-4444-444444444444"
INBOX = "core.notification_inbox"
PATH = "/api/v1/core/notifications"


def _item(org, key, title, read_at=None, resolved_at=None):
    return {"id": f"id-{org[-1]}-{key}", "org_id": org, "scope": "connector", "inbox_key": key,
            "title": title, "body": "b", "severity": "warning", "deep_link": "/commcalc/email-imports",
            "affected": [], "occurrences": 4, "first_seen_at": "2026-11-17T09:00:00+00:00",
            "last_seen_at": "2026-11-24T09:00:00+00:00", "read_at": read_at,
            "resolved_at": resolved_at}


@pytest.fixture
def db(monkeypatch):
    fake = FakeSupabase({INBOX: [
        _item(HOUSE, "freshness:house:payment_detail", "Payment detail is 2 day(s) behind"),
        _item(HOUSE, "connector:dlar_sweep_config:7", "DLAR sweep errored", read_at="2026-11-20T09:00:00+00:00"),
        _item(HOUSE, "connector:old:1", "recovered", resolved_at="2026-11-21T09:00:00+00:00"),
        _item(TENANT, "freshness:tenant:sales", "Daily sales is 5 day(s) behind"),
    ]})
    from app.modules.core import notification_inbox_api as api
    monkeypatch.setattr(api, "sb", lambda: fake)
    return fake


def _as(caller_org, super_admin=False):
    return {"org_id": caller_org, "super_admin": super_admin, "role": "admin",
            "perms": {"scope": "all"}, "employee_id": "e1"}


@pytest.fixture
def caller(monkeypatch):
    """Stand in for the real gate's identity lookup only — the GATE ITSELF still runs."""
    box = {"caller": _as(HOUSE)}
    from app.modules.core import import_health as IH
    monkeypatch.setattr(IH, "_caller", lambda *a, **k: box["caller"])
    monkeypatch.setattr(IH, "sb", lambda: FakeSupabase({}))
    return box


@pytest.mark.integration
def test_a_company_reads_its_own_items_and_nobody_elses(api_client, db, caller):
    r = api_client.get(PATH, params={"org_id": HOUSE})
    assert r.status_code == 200, r.text
    body = r.json()
    titles = [i["title"] for i in body["items"]]
    assert "Payment detail is 2 day(s) behind" in titles
    assert "Daily sales is 5 day(s) behind" not in titles, "another company's item must never appear"
    assert "recovered" not in titles, "a resolved item is out of the inbox by default"
    assert body["unread"] == 1
    assert body["org_id"] == HOUSE


@pytest.mark.integration
def test_a_caller_cannot_read_another_companys_inbox_by_asking_for_it(api_client, db, caller):
    caller["caller"] = _as(TENANT)
    r = api_client.get(PATH, params={"org_id": HOUSE})
    assert r.status_code == 200, r.text
    assert [i["org_id"] for i in r.json()["items"]] == [TENANT], (
        "org_id is clamped to the caller's own membership, not taken from the query")


@pytest.mark.integration
def test_an_anonymous_caller_gets_nothing(api_client, db, monkeypatch):
    from app.modules.core import import_health as IH
    monkeypatch.setattr(IH, "_caller", lambda *a, **k: None)
    monkeypatch.setattr(IH, "sb", lambda: FakeSupabase({}))
    assert api_client.get(PATH, params={"org_id": HOUSE}).status_code == 401


@pytest.mark.integration
def test_reading_clears_the_badge_for_that_company_only(api_client, db, caller):
    r = api_client.post(f"{PATH}/read", json={"all": True}, params={"org_id": HOUSE})
    assert r.status_code == 200, r.text
    assert r.json()["read"] == 1
    assert api_client.get(PATH, params={"org_id": HOUSE}).json()["unread"] == 0
    still = [i for i in db.rows(INBOX) if i["org_id"] == TENANT]
    assert still[0]["read_at"] is None, "one company's read must not clear another's badge"


@pytest.mark.integration
def test_an_empty_inbox_says_so_rather_than_failing(api_client, db, caller, monkeypatch):
    from app.modules.core import notification_inbox_api as api
    monkeypatch.setattr(api, "sb", lambda: FakeSupabase({INBOX: []}))
    body = api_client.get(PATH, params={"org_id": HOUSE}).json()
    assert body["items"] == [] and body["unread"] == 0 and body["read_ok"] is True
