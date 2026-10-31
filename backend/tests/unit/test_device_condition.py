"""Unit: a pre-owned device is a different device (index §POS-18).

THE OWNER'S RULING, 2026-10-31, verbatim: *"cpo is certified pre owned is adifferent device than
new  , imnei is unique"* — answering whether a refurbished handset and a new one of the same model
share a row on the accessory order grid.

WHAT THESE TESTS ARE REALLY HOLDING DOWN. The grid kept them apart already, but only because the
word `CPO` happened to survive the model-spelling parse. Nothing asserted it, so a curated
`device_model_alias` row, a promo token that swallowed the suffix, or a feed that wrote
`CERTIFIED_PREOWNED_…` instead would have merged two different devices into one order line with
nothing to notice. The fix makes the condition a declared FIELD; these tests pin the three
behaviours that make it safe, and the one that makes it fair to every tenant.

EVERY FIXTURE IS A LIVE SPELLING, measured on the feed for 1 Aug – 31 Oct 2026: Luxelink's 191
condition-marked device lines carry `CPO`, and their SKU tail carries the glued
`CERTIFIED_PREOWNED_` / `CERTIFIED_IPHONE_` forms. Cellfonz's feed marks no condition at all, which
is why "nothing declared" has to be a safe answer rather than an edge case.
"""
import pytest

from app.modules.pos import device_condition as dc

LIVE_MARKED = ("Apple iPhone 13 128GB Blue CPO TO - Promo $349.99",
               "Apple iPhone 14 128GB Red CPO TO - Promo $399.99",
               "CERTIFIED_PREOWNED_IPHONE_13_PORTIN_VERIFF_MULTI_MIN50_I",
               "CERTIFIED_IPHONE_13_128GB_PORTIN_VERIFF_MIN50M2_L")
LIVE_UNMARKED = ("IPHONE 16E BLK 128GB PPD-USA", "SAM GALAXY S25 FE JETBLACK 128 GB",
                 "Apple iPhone 13 128GB Midnight TO")
DECLARED = {"refurbished": ["CPO", "CERTIFIED_PREOWNED", "CERTIFIED", "Certified Pre-Owned"]}


def _declared():
    return dc.resolve_feed_tokens(DECLARED)


def test_the_conditions_are_the_platforms_and_the_words_are_the_tenants():
    """The split CLAUDE.md draws. Which conditions EXIST is policy, the same four for everyone; what
    a feed CALLS one is a tenant's vocabulary and may only be declared."""
    assert dc.condition_keys() == ("new", "refurbished", "used", "damaged")
    assert dc.unmarked_condition() == "new"
    tokens, basis, markers = _declared()
    assert basis == "declared"
    assert markers["refurbished"] == "CPO"          # the org's own first word, verbatim
    assert set(tokens.values()) == {"refurbished"}


def test_nothing_declared_identifies_nothing_and_says_so():
    """The directive: nothing declared means nothing identified, never a fall back to another
    tenant's words. Reading a marked handset as `new` with a confident basis is the failure mode —
    it looks exactly like a correct answer."""
    for desc in LIVE_MARKED:
        out = dc.condition_of(desc, feed_tokens={})
        assert out["key"] is None
        assert out["basis"] == "no_vocabulary"


def test_a_declared_marker_is_read_in_every_shape_the_feed_writes_it():
    """Worded, hyphenated and glued into a SKU are one declaration — otherwise the same device
    splits across three rows and each one under-orders."""
    tokens, _basis, markers = _declared()
    for desc in LIVE_MARKED:
        out = dc.condition_of(desc, feed_tokens=tokens, markers=markers)
        assert (out["key"], out["basis"]) == ("refurbished", "declared"), desc
        assert out["marker"] == "CPO"


def test_an_unmarked_line_is_the_house_default_but_the_basis_says_it_was_an_inference():
    """A feed marks the exception, not the rule, so unmarked IS new — and the schema said so first
    (`condition TEXT DEFAULT 'new'`, mig 725). The basis keeps a reading and an inference apart, so
    a surface never shows an assumption as if it had been read off the line."""
    tokens, _b, markers = _declared()
    for desc in LIVE_UNMARKED:
        out = dc.condition_of(desc, feed_tokens=tokens, markers=markers)
        assert (out["key"], out["basis"], out["marker"]) == ("new", "unmarked", None), desc


def test_one_tenants_words_never_mark_another_tenants_spelling():
    """The cross-tenant half of the 2026-10-30 directive, asserted in both directions."""
    theirs, _b, tm = dc.resolve_feed_tokens(["refurb"])
    assert dc.condition_of("Apple iPhone 13 CPO TO", feed_tokens=theirs,
                           markers=tm)["basis"] == "unmarked"
    ours, _b2, om = _declared()
    assert dc.condition_of("Apple iPhone 13 Refurb", feed_tokens=ours,
                           markers=om)["basis"] == "unmarked"


def test_a_word_naming_no_known_condition_is_dropped_not_invented():
    """A fifth condition cannot arrive through a tenant's declaration: the keys are the ones the
    schema allows, and a write of anything else would be refused at the till."""
    tokens, basis, _m = dc.resolve_feed_tokens({"sparkly": ["glittery"], "used": ["second hand"]})
    assert basis == "declared"
    assert set(tokens.values()) == {"used"}
    assert "glittery" not in tokens


def test_stripping_a_marker_folds_two_spellings_of_one_condition():
    """`strip_markers` is what makes the condition a FIELD rather than whichever words that line
    used. Both live shapes lose the marker and keep the model."""
    tokens, _b, _m = _declared()
    assert dc.strip_markers("Apple iPhone 13 128GB Blue CPO TO",
                            feed_tokens=tokens) == "Apple iPhone 13 128GB Blue TO"
    assert "CERTIFIED" not in dc.strip_markers(
        "CERTIFIED_PREOWNED_IPHONE_13_PORTIN", feed_tokens=tokens)


def test_a_spelling_with_no_marker_is_returned_byte_for_byte():
    """The safety property for every tenant the ruling does not apply to: nothing matched means
    nothing rebuilt, so separators and capacities survive and no family key on the platform moves."""
    tokens, _b, _m = _declared()
    for desc in LIVE_UNMARKED + ("IPHONE 17 PRO MAX CORANGE 256GB-USA",):
        assert dc.strip_markers(desc, feed_tokens=tokens) == desc


def test_the_vocabulary_is_a_required_argument_on_every_entry_point():
    """A caller that has not asked the org must not be able to answer."""
    with pytest.raises(TypeError):
        dc.condition_of("Apple iPhone 13 CPO")        # type: ignore[call-arg]
    with pytest.raises(TypeError):
        dc.strip_markers("Apple iPhone 13 CPO")       # type: ignore[call-arg]


def test_offered_is_what_every_surface_draws_from():
    """One list, three readers: this endpoint's payload, the POS inventory dropdown and mig 725's
    CHECK. Exactly one condition is the unmarked one, or a caller could not default a form."""
    offered = dc.offered()
    assert [c["key"] for c in offered] == list(dc.condition_keys())
    assert sum(1 for c in offered if c["unmarked"]) == 1
    assert all(c["label"] and c["label"] != c["key"] or c["key"] == c["label"] for c in offered)
