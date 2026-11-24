"""WHICH SCOPES GO TO THE INBOX IS DECLARED, PER COMPANY, WITH A HOUSE DEFAULT.

The multi-tenant rule (CLAUDE.md, owner 2026-10-30): a feature works for EVERY tenant, measured
against each tenant's own declaration, and one tenant's declaration never leaks into another's read.
A delivery ROUTE is a policy, not a tenant's vocabulary, so a house default is correct here — and a
scope nobody declared keeps mailing exactly as it does today.
"""
import pytest

from app.modules.core import notification_inbox as NI

HOUSE = NI.HOUSE_ORG
T1 = "11111111-1111-1111-1111-111111111111"
T2 = "22222222-2222-2222-2222-222222222222"


@pytest.mark.unit
def test_an_undeclared_scope_still_sends_a_message():
    assert NI.route_for([], "lateness_am", T1) == "message"


@pytest.mark.unit
def test_the_house_default_reaches_every_tenant():
    rows = [{"org_id": HOUSE, "scope": "connector", "route": "inbox"}]
    for org in (HOUSE, T1, T2):
        assert NI.route_for(rows, "connector", org) == "inbox"


@pytest.mark.unit
def test_a_tenants_own_row_wins_over_the_house_default_and_reaches_nobody_else():
    rows = [{"org_id": HOUSE, "scope": "connector", "route": "inbox"},
            {"org_id": T1, "scope": "connector", "route": "both"}]
    assert NI.route_for(rows, "connector", T1) == "both"
    assert NI.route_for(rows, "connector", T2) == "inbox", "T1's choice must not leak to T2"
    assert NI.route_for(rows, "connector", HOUSE) == "inbox"


@pytest.mark.unit
def test_a_named_scope_wins_over_the_catch_all():
    rows = [{"org_id": T1, "scope": "all", "route": "inbox"},
            {"org_id": T1, "scope": "connector", "route": "message"}]
    assert NI.route_for(rows, "connector", T1) == "message"
    assert NI.route_for(rows, "upload_due", T1) == "inbox"


@pytest.mark.unit
def test_a_route_the_code_cannot_speak_is_refused_not_guessed():
    rows = [{"org_id": T1, "scope": "connector", "route": "carrier-pigeon"}]
    assert NI.route_for(rows, "connector", T1) == "message", "an unknown route never silences an alert"


@pytest.mark.unit
def test_routing_to_the_inbox_requires_a_stable_key():
    filed = {"state": "created"}
    assert NI.suppresses_message("inbox", "connector:x:1", filed) is True
    assert NI.suppresses_message("inbox", None, filed) is False, (
        "with no stable key there is nothing to overwrite, so the message must still go out")
    assert NI.suppresses_message("both", "connector:x:1", filed) is False
    assert NI.suppresses_message("message", "connector:x:1", filed) is False


@pytest.mark.unit
def test_an_item_that_could_not_be_filed_never_silences_the_message():
    """Shipping this code before migration 1129 is applied must not cost a single alert."""
    assert NI.suppresses_message("inbox", "connector:x:1", None) is False
    assert NI.suppresses_message("inbox", "connector:x:1", {"state": "error"}) is False
    for state in ("created", "changed", "unchanged"):
        assert NI.suppresses_message("inbox", "connector:x:1", {"state": state}) is True
