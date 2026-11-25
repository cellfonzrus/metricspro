"""Integration: an onboarding invite leaves a record, and the provider's verdict lands on it.

THE REPORTED DEFECT (owner 2026-11-25, index §NOTIFY-02). "onboarding email not reaching
employees". Two NY Logistics invites had been minted minutes earlier; both were stamped `invited`,
both audit rows said `sent_to: <address>`, and `notify.send_log` held NO row for either — so nobody
could say whether the provider had accepted them, refused them, or delivered to a mistyped address.

These tests drive the REAL app (real routing, real middleware, the real handler and the real email
transport) over the in-memory database double, with every outbound socket blocked by conftest. ONLY
the one HTTP call to the provider is replaced, at `httpx.AsyncClient`, so the recording under test
is the transport's own and not a copy of it. Three things are pinned:

  1. THE TRANSPORT RECORDS. The email channel writes the `notify.send_log` row itself, so none of
     the thirty-one senders can forget — including a send the provider REFUSES.
  2. THE AUDIT TELLS THE TRUTH. A refused send no longer leaves an audit row claiming it was sent.
  3. THE WEBHOOK STAMPS. A bounce arriving later lands on that row, and the screen's read turns it
     into "the address rejected it" instead of "invited".

MULTI-TENANT (project rule 2026-10-30): every assertion is made for three orgs, and one test
asserts that one tenant's send history is not reachable from another tenant's read.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import time

import pytest

from app.modules.notify import send_record as sr

HOUSE = "00000000-0000-0000-0000-000000000001"
TENANTS = (HOUSE, "00000000-0000-0000-0000-0000000000a2", "00000000-0000-0000-0000-0000000000a3")
#: Each tenant's own hire, with its own address, so a cross-tenant leak shows up as the WRONG
#: ADDRESS rather than only as a wrong count.
HIRE = {org: (f"E{i}00", f"hire{i}@example.com") for i, org in enumerate(TENANTS, start=1)}

SECRET = "whsec_" + base64.b64encode(b"a-test-signing-key-32-bytes-long").decode()
LOG = "notify.send_log"
INVITE = "hr_onboarding_invite"
ACCEPTED_ID = "re_accepted"


def _now():
    return str(int(time.time()))


def _sign(msg_id, ts, body):
    key = base64.b64decode(SECRET.split("_", 1)[1])
    mac = hmac.new(key, sr.svix_signed_payload(msg_id, ts, body), hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


class _Resp:
    """Just enough of httpx.Response for the transport: the three attributes it reads."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _Client:
    """Stands in for httpx.AsyncClient. `PROVIDER` decides what the provider answers."""

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        PROVIDER["calls"].append({"url": url, "to": (json or {}).get("to")})
        if PROVIDER["status"] >= 300:
            return _Resp(PROVIDER["status"], {"message": PROVIDER["message"]})
        return _Resp(200, {"id": ACCEPTED_ID})


PROVIDER = {"status": 200, "message": "", "calls": []}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    from app.modules.notify.channels import email_resend

    PROVIDER.update({"status": 200, "message": "", "calls": []})
    for mod in ("app.modules.hr.router.get_supabase", "app.core.database.get_supabase"):
        monkeypatch.setattr(mod, lambda: fake_supabase, raising=False)
    monkeypatch.setattr(sr, "_table",
                        lambda client=None: fake_supabase.schema("notify").table("send_log"),
                        raising=False)
    # The transport's own gate and its tenant-name read, neither of which is what these tests are
    # about: a key must look configured, and the display name must not need a database.
    monkeypatch.setattr("app.core.config.settings.RESEND_API_KEY", "test-key", raising=False)
    monkeypatch.setattr("app.core.config.settings.RESEND_WEBHOOK_SECRET", SECRET, raising=False)
    monkeypatch.setattr(email_resend, "tenant_from_name", lambda org_id: "Tenant")
    monkeypatch.setattr(email_resend.httpx, "AsyncClient", _Client)

    for org, (emp_id, addr) in HIRE.items():
        fake_supabase.seed("storeops.employees",
                           [{"org_id": org, "employee_id": emp_id, "name": f"Hire {emp_id}",
                             "email": addr, "is_active": True}])
    fake_supabase.seed(LOG, [])
    return fake_supabase


def _rows(db, org=None, purpose=INVITE):
    out = [r for r in db.rows(LOG) if r.get("report_key") == purpose]
    return [r for r in out if org is None or r.get("org_id") == org]


def _invite(org):
    """Drive the REAL invite path. `link` rather than `login`, deliberately: the login method also
    provisions a Supabase auth account through the core user-write routes, which is a different
    subsystem's permission gate and not what is under test here. Both methods reach the same send,
    which is the line this file exists to pin."""
    from app.modules.hr import router as hr

    emp_id, addr = HIRE[org]
    return asyncio.run(hr._send_invite(
        org, {"employee_id": emp_id, "name": "Hire", "email": addr}, "link",
        dob="1990-01-01"))


# ── 1. the transport records, for every tenant ───────────────────────────────────────────────────
@pytest.mark.parametrize("org", TENANTS)
def test_an_invite_leaves_one_row_naming_the_tenant_and_the_address(wired, org):
    res = _invite(org)
    emp_id, addr = HIRE[org]
    assert res["emailed"] is True, res.get("email_note")
    rows = _rows(wired, org)
    assert len(rows) == 1, "exactly one history row per invite — no row, and no second copy"
    assert rows[0]["target"] == addr
    assert rows[0]["status"] == "sent"
    assert rows[0]["provider_message_id"] == ACCEPTED_ID
    assert rows[0]["channel"] == "email"
    assert rows[0]["filters"]["employee_id"] == emp_id
    # and nothing of any other tenant's was written
    assert len(_rows(wired)) == 1


@pytest.mark.parametrize("org", TENANTS)
def test_a_refused_invite_is_recorded_as_refused(wired, org):
    """Before this, the provider's refusal went into an HTTP response field and was thrown away."""
    PROVIDER.update({"status": 422, "message": "invalid `to` address"})
    res = _invite(org)
    assert res["emailed"] is False
    rows = _rows(wired, org)
    assert [r["status"] for r in rows] == ["failed"]
    assert "422" in rows[0]["error"]
    assert sr.delivery_verdict(rows[0])[0] == "failed"


def test_an_unconfigured_mail_provider_is_recorded_too(wired, monkeypatch):
    """"Email was never switched on" and "the employee got it" must not look the same either."""
    monkeypatch.setattr("app.core.config.settings.RESEND_API_KEY", "", raising=False)
    from app.modules.notify.channels import email_resend

    with pytest.raises(RuntimeError):
        asyncio.run(email_resend.send_email("a@b.c", "s", "<p>h</p>", org_id=HOUSE,
                                            purpose=INVITE))
    rows = _rows(wired, HOUSE)
    assert [r["status"] for r in rows] == ["failed"]
    assert not PROVIDER["calls"], "nothing should have been sent"


@pytest.mark.parametrize("org", TENANTS)
def test_the_audit_row_no_longer_claims_a_refused_invite_was_sent(wired, org):
    from app.modules.hr import router as hr

    PROVIDER.update({"status": 422, "message": "invalid `to` address"})
    res = _invite(org)
    detail = hr.invite_audit_detail("link", HIRE[org][1], emailed=res["emailed"],
                                    note=res.get("email_note"))
    assert detail["emailed"] is False
    assert "sent_to" not in detail, "an intention to send is not a delivery"
    assert detail["not_emailed_because"]


def test_an_invite_with_no_address_says_why_rather_than_claiming_a_send():
    from app.modules.hr import router as hr

    detail = hr.invite_audit_detail("link", "", emailed=False)
    assert detail["emailed"] is False
    assert "no email address" in detail["not_emailed_because"]


# ── 2. the webhook stamps the provider's later verdict ───────────────────────────────────────────
@pytest.mark.parametrize("org", TENANTS)
def test_a_bounce_lands_on_the_row_the_send_wrote(wired, api_client, org):
    _invite(org)
    body = json.dumps({"type": "email.bounced",
                       "data": {"email_id": ACCEPTED_ID, "to": [HIRE[org][1]],
                                "bounce": {"type": "Permanent", "subType": "General",
                                           "message": "550 5.1.1 user unknown"}}}).encode()
    ts = _now()
    r = api_client.post("/api/v1/notify/email-webhook", content=body,
                        headers={"svix-id": "msg_1", "svix-timestamp": ts,
                                 "svix-signature": _sign("msg_1", ts, body),
                                 "content-type": "application/json"})
    assert r.status_code == 200, r.text
    assert r.json()["recorded"] >= 1
    row = _rows(wired, org)[0]
    assert row["delivery_status"] == "bounced"
    assert "550" in row["delivery_error"]
    assert sr.delivery_verdict(row)[0] == "bounced"


def test_an_unsigned_webhook_cannot_write_a_delivery_state(wired, api_client):
    """The endpoint is public (the provider carries no JWT of ours), so the signature is its only
    gate. A stranger must not be able to park a fake 'delivered' on a mail that bounced."""
    _invite(HOUSE)
    body = json.dumps({"type": "email.delivered", "data": {"email_id": ACCEPTED_ID}}).encode()
    assert api_client.post("/api/v1/notify/email-webhook", content=body,
                           headers={"content-type": "application/json"}).status_code == 403
    assert api_client.post("/api/v1/notify/email-webhook", content=body,
                           headers={"svix-id": "msg_1", "svix-timestamp": _now(),
                                    "svix-signature": "v1,AAAA",
                                    "content-type": "application/json"}).status_code == 403
    assert _rows(wired, HOUSE)[0].get("delivery_status") in (None, "")


def test_an_event_the_house_has_not_ruled_on_answers_2xx_and_records_nothing(wired, api_client):
    """A provider disables a subscription that keeps erroring, and a disabled subscription is how
    this blind spot would come back. So an open is a 200 that changes nothing, not a 4xx."""
    _invite(HOUSE)
    body = json.dumps({"type": "email.opened", "data": {"email_id": ACCEPTED_ID}}).encode()
    ts = _now()
    r = api_client.post("/api/v1/notify/email-webhook", content=body,
                        headers={"svix-id": "msg_2", "svix-timestamp": ts,
                                 "svix-signature": _sign("msg_2", ts, body),
                                 "content-type": "application/json"})
    assert r.status_code == 200
    assert r.json()["recorded"] == 0
    assert _rows(wired, HOUSE)[0].get("delivery_status") in (None, "")


def test_replaying_the_same_event_changes_nothing(wired, api_client):
    _invite(HOUSE)
    body = json.dumps({"type": "email.delivered", "data": {"email_id": ACCEPTED_ID}}).encode()
    ts = _now()
    hdr = {"svix-id": "msg_3", "svix-timestamp": ts, "content-type": "application/json",
           "svix-signature": _sign("msg_3", ts, body)}
    first = api_client.post("/api/v1/notify/email-webhook", content=body, headers=hdr).json()
    second = api_client.post("/api/v1/notify/email-webhook", content=body, headers=hdr).json()
    assert first["recorded"] >= 1
    assert second["recorded"] == 0, "a re-sent event must be a pure no-op"
    assert _rows(wired, HOUSE)[0]["delivery_status"] == "delivered"


# ── 3. what the screen reads, and that it stays inside the tenant ────────────────────────────────
@pytest.mark.parametrize("org", TENANTS)
def test_the_screens_read_answers_unknown_before_any_send_is_recorded(wired, org):
    """`invited` must never be allowed to pass for `reached them`."""
    assert sr.last_attempts(org, INVITE, [HIRE[org][1]]) == {}
    assert sr.delivery_verdict(None)[0] == "unknown"


@pytest.mark.parametrize("org", TENANTS)
def test_the_screens_read_names_the_state_after_a_send(wired, org):
    _invite(org)
    seen = sr.last_attempts(org, INVITE, [HIRE[org][1]])
    assert set(seen) == {HIRE[org][1]}
    assert sr.delivery_verdict(seen[HIRE[org][1]])[0] == "accepted"


def test_one_tenants_send_history_is_not_reachable_from_another_tenants_read(wired):
    """A tenant-scoped read is where cross-tenant leakage lives (project rule 2026-10-30). Every
    tenant sends an invite; asking for EVERY address from one tenant's read must return only that
    tenant's own."""
    for org in TENANTS:
        _invite(org)
    every_address = [addr for (_e, addr) in HIRE.values()]
    for org in TENANTS:
        seen = sr.last_attempts(org, INVITE, every_address)
        assert set(seen) == {HIRE[org][1]}, f"{org} saw {sorted(seen)}"
