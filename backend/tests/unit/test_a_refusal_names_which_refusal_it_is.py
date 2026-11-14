"""A REFUSAL NAMES WHICH REFUSAL IT IS (index §COMMCALC-74).

FOUND 2026-11-14, by it misleading the owner. `commcalc.data_source`
`31157161-2ac3-4a1e-8d32-b21c29de35d0` ("epay back office") carried, live:

    The portal REFUSED the request — this is not a sign-in page. Check the address saved on this
    source: asking for a page inside the portal without a session is answered this way. The
    screenshot shows exactly what it said.

That sentence covers TWO states with different remedies:

  1. the saved address points INSIDE the portal          → fix the address
  2. the portal's EDGE refused the client outright       → the address is already right and
                                                            nothing about it helps

Both arrived on that one row inside 25 minutes: first with `portal_url` = `…/reports` (state 1, the
sentence was right), then with the FRONT DOOR (state 2 — an F5/ASM "The requested URL was rejected"
page, which is that back office's permanent condition for any non-browser client). The second time
it sent the owner to check an address that was already correct.

THE RULE: the remedy an operator is given is derived from what the page IS, not from the kind alone.
An appliance rejection names itself — "request rejected" / "the requested URL was rejected", plus a
support id their security team can look up — and that is evidence read off the page, which is what
this module already requires of every other decision it makes.

THE REGRESSION USES THE FRONT DOOR deliberately: a test that assumed "rejected at a sub-path, fine
at the root" would pass while the real case failed.
"""
from app.modules.commcalc import login_outcome as lo
from app.modules.commcalc import portal_backoff as pb

# The F5/ASM page, as served at the FRONT DOOR of that back office (measured 2026-11-14).
EDGE_PAGE = ("Request Rejected  The requested URL was rejected. Please consult with your "
             "administrator.  Your support ID is: 7814562390118473055  [Go Back]")

# A portal application's own "you asked for a page you are not signed in for".
INSIDE_PAGE = ("Access to this page has been denied. Please sign in to continue to the reporting "
               "section.")


def test_an_appliance_rejection_names_itself_and_carries_its_support_id():
    said = pb.appliance_refusal(EDGE_PAGE)
    assert said is not None
    assert said["support_id"] == "7814562390118473055"
    assert said["marker"] in pb.DEFAULT_REFUSAL_MARKERS


def test_a_rejection_with_no_support_id_is_still_an_appliance_rejection():
    said = pb.appliance_refusal("Request Rejected")
    assert said is not None and said["support_id"] is None


def test_the_portal_s_own_denial_is_not_an_appliance_rejection():
    assert pb.appliance_refusal(INSIDE_PAGE) is None
    assert pb.appliance_refusal("") is None
    assert pb.appliance_refusal(None) is None


def test_the_edge_sentence_never_sends_an_operator_to_check_the_address():
    note = pb.verdict_note(pb.REFUSAL_KIND, text=EDGE_PAGE)
    low = note.lower()
    # It may RULE the address out — that is useful — but it must never ask for it to be checked,
    # which is the round trip this fix exists to stop.
    assert "check the address" not in low, "the address is already correct in this state"
    assert "7814562390118473055" in note, "the support id is the only thing their team can act on"
    assert "support id" in low


def test_the_inside_the_portal_sentence_still_asks_for_the_address():
    note = pb.verdict_note(pb.REFUSAL_KIND, text=INSIDE_PAGE)
    assert "check the address" in note.lower()
    assert "support id" not in note.lower()


def test_a_refusal_with_no_page_in_hand_keeps_the_address_wording():
    # A caller that cannot supply the page gets the state that is still actionable, never a
    # fabricated support id.
    assert "check the address" in pb.verdict_note(pb.REFUSAL_KIND).lower()


def test_the_other_two_kinds_are_untouched_by_the_page():
    for kind in (pb.CHALLENGE_KIND, pb.THROTTLE_KIND):
        assert pb.verdict_note(kind) == pb.verdict_note(kind, text=EDGE_PAGE), kind
    assert pb.verdict_note("not_a_kind", text=EDGE_PAGE) == ""


def test_the_verdict_carries_the_sentence_its_own_page_earned():
    v = pb.page_verdict(EDGE_PAGE)
    assert v and v["kind"] == pb.REFUSAL_KIND
    assert "support id" in v["note"].lower() and "check the address" not in v["note"].lower()
    assert v.get("support_id") == "7814562390118473055"
    w = pb.page_verdict(INSIDE_PAGE)
    assert w and w["kind"] == pb.REFUSAL_KIND and "check the address" in w["note"].lower()
    assert w.get("support_id") is None


def test_a_challenge_still_wins_over_a_refusal_when_both_match():
    v = pb.page_verdict("Request Rejected. Please verify you are human to continue.")
    assert v["kind"] == pb.CHALLENGE_KIND


def test_the_login_verdict_reports_the_sentence_the_page_earned_not_its_own_copy():
    # login_outcome kept a SECOND refusal sentence, which also named the address. When the evidence
    # arrives with a sentence of its own, that one is reported — one fact, one home, dereferenced.
    out = lo.verdict(visible_text=EDGE_PAGE, form_present=False,
                     page_verdict=pb.page_verdict(EDGE_PAGE))
    assert out["outcome"] == lo.REFUSED
    assert "support id" in out["note"].lower()
    assert "check the address" not in out["note"].lower()


def test_the_login_verdict_falls_back_to_its_own_sentence_with_no_evidence_in_hand():
    out = lo.verdict(visible_text="Access to this page has been denied.", form_present=False,
                     page_verdict={"kind": "refusal", "marker": "access denied"})
    assert out["outcome"] == lo.REFUSED and out["note"]


def test_a_refusal_is_never_a_credential_problem():
    assert not lo.is_credential_problem(lo.REFUSED)
