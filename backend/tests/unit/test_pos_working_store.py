"""Unit: which store is this sale for, and is that a CONFIRMED fact? (index §POS-14)

THE DEFECT THIS WAS WRITTEN RED FOR (owner, 2026-10-30):
*"since we have multiple stores under 1 tenant, the user if assigned to multiple stores should
confirm which store they are working in so the receipt and the sale are logged for that store."*

THE CLASS, not the instance. "Which store is this sale for?" was answered by a GUESS that was
indistinguishable from an answer. Four POS screens each spelled their own ladder —
`getActiveStore() || user.store_code || stores[0].store_code` — so a cashier granted three stores
rang up at whichever store sorted first, with no sign on the screen that nobody had chosen it. The
terminal's saved pick was not keyed by user either, so the next cashier on that till inherited the
last one's store. And `POST /pos/sales/checkout` never checked the store on the sale against the
caller's span at all: it would accept any store in the tenant, or none.

So the fix is not "add a dropdown". It is: a working store carries its BASIS, a guess can never be
one, and the money path refuses a store the caller cannot ring at.

TAX, the second half. A tax rate's precedence (store > market > org-wide) had a one home on the
server, `pos/tax_precedence.resolve`, and a SECOND, SHORTER copy in the register, which skipped the
market rung. A rate set on a market therefore showed as "effective" in the Settings grid and charged
the org-wide rate (or nothing) at the till. These tests pin the three rungs and the two negatives.
"""
import pytest

from app.modules.pos import working_store as WS
from app.modules.pos import tax_precedence as TP

ORG = "00000000-0000-0000-0000-000000000001"

#: A roster shaped like `storeops.stores` — the columns it actually has (see §POS-10).
ROSTER = [{"store_code": "B-1115", "address": "2723 Example Ave", "market": "Brooklyn"},
          {"store_code": "B-1598", "address": "88 Utica Ave", "market": "Brooklyn"},
          {"store_code": "B-2001", "address": "14 Main St", "market": "Long Island"}]


# ── §A. the span: which stores may this caller ring at ───────────────────────────────────────────

def test_A1_unrestricted_caller_may_ring_at_every_store_and_must_confirm():
    s = WS.span(ROSTER, None)
    assert s["stores"] == ["B-1115", "B-1598", "B-2001"]
    assert s["must_confirm"] is True


def test_A2_a_span_of_one_store_has_nothing_to_confirm():
    s = WS.span(ROSTER, {"B-1598"})
    assert s["stores"] == ["B-1598"]
    assert s["must_confirm"] is False


def test_A3_the_keyset_matches_on_address_too_because_scope_keyset_widens_it():
    # storeops.scope_keyset returns codes AND their addresses, upper-cased.
    s = WS.span(ROSTER, {"2723 EXAMPLE AVE"})
    assert s["stores"] == ["B-1115"]


def test_A4_a_login_store_outside_the_span_is_not_offered_as_the_login_store():
    s = WS.span(ROSTER, {"B-1598"}, login_store="B-2001")
    assert s["login_store"] is None


def test_A5_a_login_store_inside_the_span_is_carried():
    assert WS.span(ROSTER, None, login_store="B-1598")["login_store"] == "B-1598"


def test_A6_no_store_in_the_span_must_not_read_as_nothing_to_confirm():
    # A caller scoped to a store that is not on the roster can ring at nothing. That is a refusal,
    # not a free pass — must_confirm False here would mean "go ahead with no store".
    s = WS.span(ROSTER, {"B-9999"})
    assert s["stores"] == []
    assert s["must_confirm"] is True


# ── §B. the basis: a guess is never an answer ────────────────────────────────────────────────────

def test_B1_an_explicit_pick_inside_the_span_is_confirmed():
    r = WS.resolve(WS.span(ROSTER, None), "B-1598")
    assert (r["store_code"], r["basis"], r["refusal"]) == ("B-1598", WS.BASIS_CONFIRMED, None)


def test_B2_a_sole_store_needs_no_confirmation_and_says_so():
    r = WS.resolve(WS.span(ROSTER, {"B-1598"}), None)
    assert (r["store_code"], r["basis"]) == ("B-1598", WS.BASIS_SOLE)
    assert r["refusal"] is None


def test_B3_nothing_picked_with_several_stores_is_UNCONFIRMED_and_names_no_store():
    r = WS.resolve(WS.span(ROSTER, None), None)
    assert r["basis"] == WS.BASIS_UNCONFIRMED
    assert r["store_code"] is None, "a guess must not be handed back as the working store"
    assert r["refusal"] == WS.ASK_SENTENCE


def test_B4_the_first_store_in_the_list_is_NEVER_the_answer():
    # The exact defect: `stores[0].store_code`. Pinned as its own case so no later change can
    # reintroduce a positional fallback and still pass §B3 by accident.
    for roster in (ROSTER, list(reversed(ROSTER))):
        assert WS.resolve(WS.span(roster, None), None)["store_code"] is None


def test_B5_a_pick_outside_the_span_is_refused_not_quietly_replaced():
    r = WS.resolve(WS.span(ROSTER, {"B-1598"}), "B-2001")
    assert r["store_code"] is None
    assert r["basis"] == WS.BASIS_UNCONFIRMED
    assert "B-2001" in r["refusal"]


def test_B6_a_caller_with_no_ringable_store_is_told_so_in_its_own_sentence():
    r = WS.resolve(WS.span(ROSTER, {"B-9999"}), None)
    assert r["store_code"] is None
    assert r["refusal"] == WS.NO_STORE_SENTENCE


def test_B7_store_codes_are_matched_without_case_folding_surprises():
    # store-code-case-phantom-store: 'b-1115' and 'B-1115' are the same store to a human and were
    # NOT to this platform. A pick must resolve to the roster's own spelling, never the typed one.
    r = WS.resolve(WS.span(ROSTER, None), "b-1115")
    assert r["store_code"] == "B-1115"
    assert r["basis"] == WS.BASIS_CONFIRMED


# ── §C. what the money path enforces ─────────────────────────────────────────────────────────────

def test_C1_a_sale_at_a_store_in_the_span_is_allowed():
    assert WS.sale_refusal(WS.span(ROSTER, {"B-1598"}), "B-1598") is None


def test_C2_a_sale_at_a_store_outside_the_span_is_refused():
    assert WS.sale_refusal(WS.span(ROSTER, {"B-1598"}), "B-2001") is not None


def test_C3_a_sale_with_NO_store_is_refused_when_the_caller_could_ring_at_several():
    # pos.checkout writes NULL whenever the caller omits store_code, and `_span_filter` then shows
    # that sale to every store-scoped manager in the tenant. An unattributed sale is not a sale.
    assert WS.sale_refusal(WS.span(ROSTER, None), None) == WS.ASK_SENTENCE


def test_C4_a_sale_with_no_store_is_STILL_refused_when_only_one_store_is_ringable():
    # The server knows which store it must be, but the sale must carry it rather than be inferred
    # at read time — the receipt, the closing and the P&L all read the stored column.
    assert WS.sale_refusal(WS.span(ROSTER, {"B-1598"}), None) is not None


# ── §D. tax precedence: store > market > org-wide, one home ──────────────────────────────────────

CODES = [{"id": "t-store", "name": "Ave U", "rate": 8.875, "store_code": "B-1115",
          "market": None, "is_active": True},
         {"id": "t-market", "name": "Brooklyn", "rate": 8.5, "store_code": None,
          "market": "Brooklyn", "is_active": True},
         {"id": "t-org", "name": "Default", "rate": 4.0, "store_code": None,
          "market": None, "is_active": True}]


def test_D1_a_rate_on_the_store_wins():
    r = TP.resolve(CODES, "B-1115", "Brooklyn")
    assert (r["rate"], r["scope"]) == (8.875, "store")


def test_D2_a_rate_on_the_market_wins_when_the_store_has_none():
    r = TP.resolve(CODES, "B-1598", "Brooklyn")
    assert (r["rate"], r["scope"]) == (8.5, "market")


def test_D3_the_org_wide_rate_is_the_last_rung():
    r = TP.resolve(CODES, "B-2001", "Long Island")
    assert (r["rate"], r["scope"]) == (4.0, "org")


def test_D4_no_rate_at_all_is_explicit_never_a_silent_zero():
    r = TP.resolve([], "B-2001", "Long Island")
    assert r["rate"] is None
    assert r["scope"] == "none"
    assert r["warning"], "a taxable sale charging $0 is not recoverable after the customer leaves"


def test_D5_a_retired_rate_never_wins():
    codes = [dict(CODES[0], is_active=False), CODES[1]]
    assert TP.resolve(codes, "B-1115", "Brooklyn")["scope"] == "market"


def test_D6_market_is_matched_case_insensitively():
    assert TP.resolve(CODES, "B-1598", "brooklyn")["scope"] == "market"


def test_D7_the_rate_is_a_PERCENT_and_the_fraction_is_derived_here_not_at_the_till():
    # router docstring: "rate is a PERCENT (8.875); the register converts to a fraction". That
    # conversion was the register's own, which is how a second rule grew. It lives here now.
    assert TP.resolve(CODES, "B-1115", "Brooklyn")["fraction"] == pytest.approx(0.08875)
    assert TP.resolve([], "B-1115", "")["fraction"] == 0.0


def test_D8_the_market_rung_is_NOT_skippable_which_is_the_register_defect():
    # The register's own copy was store > org. Under it, B-1598 charged 4.0%. Pinned explicitly.
    assert TP.resolve(CODES, "B-1598", "Brooklyn")["rate"] != 4.0
