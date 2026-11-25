"""Unit: the arithmetic of "what became of this message" — pure, no app, no database.

WHAT THIS PINS (owner report 2026-11-25, index §NOTIFY-02). Onboarding invites were reported as not
reaching employees. Measured on production first: the invites existed, the audit said they had been
sent, and `notify.send_log` held no row for any of them — nor for any HR, CRM, helpdesk, receipt or
access-code mail ever sent. The record existed and only report senders dereferenced it.

Written test-first (project rule 2026-10-28). Each test below names the lie it stops the code
telling, because every one of them is a lie a surface told a human on 2026-11-25:

  · "invited" means the employee got something                -> delivery_verdict(None) is `unknown`
  · an accepted mail is a delivered mail                      -> `accepted` is its own state
  · a send that cannot say what it is may still be made       -> attempt_row refuses a blank purpose
  · a late "delivered" can overwrite a bounce                 -> merge_status: failure always wins
  · an opened mail is a delivered mail                        -> email.opened records nothing
  · an unsigned webhook may write a delivery state            -> fail CLOSED on an unset secret
"""
import base64
import hashlib
import hmac
import json

import pytest

from app.modules.notify import send_record as sr

HOUSE = "00000000-0000-0000-0000-000000000001"
#: Three tenants, because a feature that works for one is not a feature (project rule 2026-10-30).
TENANTS = (HOUSE, "00000000-0000-0000-0000-0000000000a2", "00000000-0000-0000-0000-0000000000a3")


# ── the attempt row ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("org", TENANTS)
def test_an_attempt_row_names_the_tenant_the_purpose_and_the_outcome(org):
    row = sr.attempt_row(org_id=org, purpose="hr_onboarding_invite", channel="email",
                         target="Rep@Example.com", status="sent", provider_message_id="re_1")
    assert row["org_id"] == org
    assert row["report_key"] == "hr_onboarding_invite"
    assert row["channel"] == "email"
    assert row["provider_message_id"] == "re_1"
    assert row["status"] == "sent"


def test_a_send_that_cannot_say_what_it_is_cannot_be_recorded():
    """The whole defect in one line: thirty-one senders recorded nothing. A purpose is REQUIRED."""
    with pytest.raises(ValueError):
        sr.attempt_row(org_id=HOUSE, purpose="  ", channel="email", target="a@b.c", status="sent")


def test_the_attempt_status_is_only_what_the_transport_itself_saw():
    """`delivered` is the PROVIDER's later word and belongs in the delivery columns. Accepting it
    here would let an acceptance be recorded as a delivery, which is the WhatsApp defect of
    2026-07-18 rebuilt for email."""
    for bad in ("delivered", "bounced", "pending", ""):
        with pytest.raises(ValueError):
            sr.attempt_row(org_id=HOUSE, purpose="p", channel="email", target="a@b.c", status=bad)


def test_an_error_is_kept_but_bounded():
    row = sr.attempt_row(org_id=HOUSE, purpose="p", channel="email", target="a@b.c",
                         status="failed", error="x" * 5000)
    assert row["error"] and len(row["error"]) <= 1000


# ── the allowlist of what a provider may say ─────────────────────────────────────────────────────
def test_each_channel_only_accepts_words_its_own_provider_uses():
    assert sr.valid_status("email", "bounced") and sr.valid_status("email", "delivered")
    assert sr.valid_status("whatsapp", "read") and sr.valid_status("whatsapp", "failed")
    # Meta never says "bounced"; accepting it would let a spoofed payload write a verdict that
    # channel cannot reach.
    assert not sr.valid_status("whatsapp", "bounced")
    assert not sr.valid_status("email", "read")
    # An unruled word, and an unruled channel, record NOTHING rather than a confident wrong verdict.
    assert not sr.valid_status("email", "probably_fine")
    assert not sr.valid_status("carrier_pigeon", "delivered")


# ── the monotonic merge ──────────────────────────────────────────────────────────────────────────
def test_a_failure_state_always_wins_however_late_it_arrives():
    for loser in ("sent", "delivered", "read", "delayed", None, ""):
        assert sr.merge_status(loser, "bounced") == "bounced"
        assert sr.merge_status(loser, "complained") == "complained"


def test_a_later_healthy_status_never_overwrites_a_failure():
    for inc in ("sent", "delivered", "read", "delayed"):
        assert sr.merge_status("bounced", inc) == "bounced"
        assert sr.merge_status("complained", inc) == "complained"


def test_the_status_never_regresses_and_an_unknown_word_changes_nothing():
    assert sr.merge_status("delivered", "sent") == "delivered"
    assert sr.merge_status("read", "delivered") == "read"
    assert sr.merge_status("delivered", "opened") == "delivered"
    assert sr.merge_status("delivered", "") == "delivered"


def test_the_whatsapp_fix_of_2026_07_18_still_behaves_exactly_as_it_did():
    """This module ABSORBED `remediation/router._merge_delivery_status`. The absorbed behaviour is
    pinned here so factoring it out cannot have changed the channel it came from."""
    assert sr.merge_status(None, "sent") == "sent"
    assert sr.merge_status("sent", "delivered") == "delivered"
    assert sr.merge_status("delivered", "read") == "read"
    assert sr.merge_status("read", "failed") == "failed"
    assert sr.merge_status("failed", "read") == "failed"


def test_meta_errors_flatten_to_one_human_string():
    out = sr.flatten_meta_errors([{"code": 131026, "title": "Message undeliverable",
                                   "error_data": {"details": "Receiver is incapable"}}])
    assert "131026" in out and "undeliverable" in out and "incapable" in out


# ── the provider's events ────────────────────────────────────────────────────────────────────────
def test_a_bounce_carries_the_providers_own_reason():
    mid, status, err = sr.delivery_from_resend({
        "type": "email.bounced",
        "data": {"email_id": "re_9", "bounce": {"type": "Permanent", "subType": "General",
                                                "message": "550 5.1.1 user unknown"}}})
    assert (mid, status) == ("re_9", "bounced")
    assert "Permanent" in err and "550" in err


def test_a_spam_complaint_is_recorded_as_its_own_state():
    assert sr.delivery_from_resend(
        {"type": "email.complained", "data": {"email_id": "re_9"}})[1] == "complained"


def test_a_suppressed_send_is_its_own_state_not_a_bounce():
    """CONFIRMED LIVE 2026-11-25. The reported invite showed `suppressed` in the provider's
    dashboard: the address was on the account's suppression list after an earlier bounce, so the
    send was dropped at the provider while our code got 200 OK and a real message id. A bounce is
    the receiving server saying no; this is OUR provider declining to send, and the remedy is ours,
    so it must not be reported as a bounce."""
    mid, status, err = sr.delivery_from_resend({
        "type": "email.failed",
        "data": {"email_id": "re_s", "failed": {"reason": "suppressed: previously bounced"}}})
    assert (mid, status) == ("re_s", "failed")
    assert "suppress" in err.lower()
    state, msg = sr.delivery_verdict({"status": "sent", "delivery_status": "failed",
                                      "delivery_error": err})
    assert state == "refused", "a provider refusal is not a bounce — the fix is a different one"
    assert "did not send" in msg


def test_a_refusal_with_no_reason_still_names_the_likely_cause():
    _m, _s2, err = sr.delivery_from_resend({"type": "email.failed",
                                            "data": {"email_id": "re_s"}})
    assert "suppression list" in err


def test_an_open_is_not_a_delivery():
    """Somebody reading a mail is not us delivering it, and recording an open as a delivery state
    would overwrite a bounce that arrived first."""
    for t in ("email.opened", "email.clicked", "email.whatever", "", None):
        assert sr.delivery_from_resend({"type": t, "data": {"email_id": "re_9"}}) is None


def test_an_event_with_no_message_id_cannot_be_attached_to_anything():
    assert sr.delivery_from_resend({"type": "email.delivered", "data": {}}) is None
    assert sr.delivery_from_resend("not a dict") is None
    assert sr.delivery_from_resend(None) is None


# ── the webhook's only authentication ────────────────────────────────────────────────────────────
SECRET = "whsec_" + base64.b64encode(b"a-test-signing-key-32-bytes-long").decode()


def _sign(secret, msg_id, ts, body):
    key = base64.b64decode(secret.split("_", 1)[1])
    mac = hmac.new(key, sr.svix_signed_payload(msg_id, ts, body), hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def test_a_correctly_signed_payload_verifies():
    body = json.dumps({"type": "email.delivered"}).encode()
    assert sr.svix_signature_ok(SECRET, "msg_1", "1700000000",
                                _sign(SECRET, "msg_1", "1700000000", body), body, now=1700000000)


def test_an_unset_secret_rejects_every_payload():
    """FAIL CLOSED. The endpoint is on the public allowlist, so a secret that is not set must mean
    'refuse', never 'accept anonymously' — the hole WHATSAPP_APP_SECRET was hardened against on
    2026-08-05."""
    body = b"{}"
    assert not sr.svix_signature_ok("", "msg_1", "1700000000",
                                    _sign(SECRET, "msg_1", "1700000000", body), body,
                                    now=1700000000)


def test_a_tampered_body_a_wrong_key_and_a_missing_header_all_fail():
    body = b'{"type":"email.delivered","data":{"email_id":"re_1"}}'
    good = _sign(SECRET, "msg_1", "1700000000", body)
    assert not sr.svix_signature_ok(SECRET, "msg_1", "1700000000", good, body + b" ", now=1700000000)
    assert not sr.svix_signature_ok(SECRET, "msg_OTHER", "1700000000", good, body, now=1700000000)
    assert not sr.svix_signature_ok(SECRET, "msg_1", "1700000000", "", body, now=1700000000)
    assert not sr.svix_signature_ok(SECRET, "msg_1", "1700000000", "v1,AAAA", body, now=1700000000)


def test_a_captured_payload_cannot_be_replayed_forever():
    body = b"{}"
    sig = _sign(SECRET, "msg_1", "1700000000", body)
    assert sr.svix_signature_ok(SECRET, "msg_1", "1700000000", sig, body, now=1700000000)
    assert not sr.svix_signature_ok(SECRET, "msg_1", "1700000000", sig, body,
                                    now=1700000000 + 4000)


def test_several_signatures_pass_while_a_secret_is_being_rotated():
    body = b"{}"
    header = "v1,AAAAAAAA " + _sign(SECRET, "msg_1", "1700000000", body)
    assert sr.svix_signature_ok(SECRET, "msg_1", "1700000000", header, body, now=1700000000)


# ── what one surface shows one human ─────────────────────────────────────────────────────────────
def test_no_record_is_never_reported_as_sent():
    """THE REPORTED DEFECT. `invited` meant "a link was minted" and the screen showed nothing else.
    An absent record must read as unknown, because "we have no idea" is the fact that was hidden."""
    state, msg = sr.delivery_verdict(None)
    assert state == "unknown"
    assert "no send recorded" in msg.lower()


def test_a_refused_send_says_so_with_the_providers_reason():
    state, msg = sr.delivery_verdict({"status": "failed", "error": "Resend 422: invalid address"})
    assert state == "failed" and "422" in msg


def test_an_accepted_mail_is_not_called_delivered_and_mentions_spam():
    """The honest middle state, and the one Sanjot needed on 2026-11-25: we handed it over, the
    provider took it, and nothing has confirmed it arrived."""
    state, msg = sr.delivery_verdict({"status": "sent", "delivery_status": None})
    assert state == "accepted"
    assert "spam" in msg.lower()


def test_a_bounce_a_complaint_a_delay_and_a_delivery_each_read_as_themselves():
    assert sr.delivery_verdict({"status": "sent", "delivery_status": "bounced",
                                "delivery_error": "550 user unknown"})[0] == "bounced"
    assert sr.delivery_verdict({"status": "sent", "delivery_status": "complained"})[0] == "complained"
    assert sr.delivery_verdict({"status": "sent", "delivery_status": "failed"})[0] == "refused"
    assert sr.delivery_verdict({"status": "sent", "delivery_status": "delayed"})[0] == "delayed"
    assert sr.delivery_verdict({"status": "sent", "delivery_status": "delivered"})[0] == "delivered"
