"""Unit: one phone is one row, whatever colour it was sold in (index §COMMCALC-49.11).

THE DEFECT THESE TESTS HOLD DOWN, reported by the owner on 2026-10-30: *"accessory order plan should
combine the accessories for all line items for iphone 13 oin different colors ... iph 13 is different
from 13 pro"*. The report was ranking five colours of one phone as five phones, so the best seller
looked like five mid-table rows and the case order was split five ways.

EVERY FIXTURE IS A SPELLING READ OFF THE LIVE FEED (September 2026, both tenants' own device
departments), not an invented one. That matters twice over: the two tenants spell a colour
differently (`BLK` against `Midnight`), which is the whole reason the vocabulary is a declaration,
and one of them decorates the model with a certified-pre-owned marker that must NOT be mistaken for
a colour.
"""
import ast
import inspect

import pytest

from app.modules.commcalc import device_model_family as dmf

# ── THE REAL SPELLINGS, AND THE REAL DECLARATIONS ───────────────────────────────────────────────
HOUSE_COLOURS = ("blk", "black", "blue", "jetblack", "navy", "gray", "silver", "cloud silver")
OTHER_COLOURS = ("black", "white", "midnight", "pink", "red", "purple", "blue")

LIVE_HOUSE = ("IPHONE 16E BLK 128GB PPD-USA", "SAM GALAXY S25 FE JETBLACK 128 GB",
              "SAM GALAXY S25 FE NAVY 128 GB", "CELERO5G TAB CLOUD SILVER 128 GB")
LIVE_OTHER = ("Apple iPhone 13 128GB Pink CPO TO", "Apple iPhone 13 128GB Black CPO TO",
              "Apple iPhone 13 128GB Red CPO TO", "Apple iPhone 13 128GB White CPO TO",
              "Apple iPhone 13 128GB Blue CPO TO", "Apple iPhone 13 128GB Midnight TO")


def test_the_reported_defect_five_colours_of_one_phone_become_one_row():
    keys = {dmf.family_of(m, colour_tokens=OTHER_COLOURS, condition_tokens={})["key"] for m in LIVE_OTHER[:5]}
    assert len(keys) == 1, "every certified-pre-owned iPhone 13 is one phone for ordering purposes"


def test_the_owners_own_distinction_a_pro_is_not_a_colour():
    plain = dmf.family_of("Apple iPhone 13 128GB Black TO", colour_tokens=OTHER_COLOURS, condition_tokens={})
    pro = dmf.family_of("Apple iPhone 13 Pro 128GB Black TO", colour_tokens=OTHER_COLOURS, condition_tokens={})
    maxi = dmf.family_of("Apple iPhone 13 Pro Max 128GB Black TO", colour_tokens=OTHER_COLOURS, condition_tokens={})
    assert len({plain["key"], pro["key"], maxi["key"]}) == 3


def test_a_phone_word_that_is_not_a_colour_survives():
    """`CPO` marks a refurbished unit and `5G`, `PLUS`, `FE` and `TO` are model or carrier words. A
    careless 'strip the noise' implementation eats these and merges phones that take different
    stock, so each is pinned."""
    for word in ("CPO", "5G", "PLUS", "FE", "TO", "XL", "E"):
        label = dmf.family_of(f"A PHONE {word} 128GB BLACK", colour_tokens=OTHER_COLOURS, condition_tokens={})["label"]
        assert word in label.split(), word


def test_the_capacity_is_a_format_and_goes_whether_glued_or_split():
    assert dmf.family_of("SAM GALAXY S25 FE 128 GB", colour_tokens=(), condition_tokens={})["label"] \
        == "SAM GALAXY S25 FE"
    assert dmf.family_of("SAM GALAXY S25 FE 256GB", colour_tokens=(), condition_tokens={})["label"] \
        == "SAM GALAXY S25 FE"
    assert dmf.family_of("A PHONE 1TB", colour_tokens=(), condition_tokens={})["label"] == "A PHONE"


def test_a_bare_number_that_is_not_a_capacity_stays():
    """`moto g play - 2026` carries its model YEAR as a number, and `A17` a model number. Dropping
    either would merge two generations of the same handset into one order."""
    assert dmf.family_of("moto g play - 2026", colour_tokens=HOUSE_COLOURS, condition_tokens={})["label"] \
        == "moto g play - 2026"
    assert dmf.family_of("SAMSUNG GALAXY A17 5G BLACK 128GB", colour_tokens=HOUSE_COLOURS, condition_tokens={})["label"] \
        == "SAMSUNG GALAXY A17 5G"


def test_nothing_declared_means_nothing_collapsed_and_the_basis_says_so():
    """CLAUDE.md, *"every feature is for EVERY tenant"*: a colour word is a tenant's own spelling, so
    there is no house list to fall back to. An org that has declared none keeps its raw spellings and
    the surface can say why — never another tenant's colour words."""
    out = dmf.family_of("Apple iPhone 13 128GB Pink CPO TO", colour_tokens=(), condition_tokens={})
    assert out["basis"] == "no_vocabulary"
    assert out["label"] == "Apple iPhone 13 Pink CPO TO"


def test_the_vocabulary_is_required_so_a_caller_that_has_not_asked_cannot_answer():
    with pytest.raises(TypeError):
        dmf.family_of("Apple iPhone 13 128GB Pink CPO TO")        # type: ignore[call-arg]
    p = inspect.signature(dmf.family_of).parameters["colour_tokens"]
    assert p.default is inspect.Parameter.empty
    assert p.kind is inspect.Parameter.KEYWORD_ONLY


def test_one_tenants_colour_words_never_reach_another_tenants_read():
    """The negative assertion is the one that pins the mechanism rather than the symptom: the same
    row, under the other org's declaration, must NOT collapse."""
    assert dmf.family_of("Apple iPhone 13 128GB Midnight TO",
                         colour_tokens=HOUSE_COLOURS, condition_tokens={})["label"] == "Apple iPhone 13 Midnight TO"
    assert dmf.family_of("IPHONE 16E BLK 128GB PPD-USA",
                         colour_tokens=OTHER_COLOURS, condition_tokens={})["label"] == "IPHONE 16E BLK PPD-USA"


def test_a_multi_word_colour_goes_as_one_thing():
    """One live spelling is `CLOUD SILVER`. Declared as one phrase it is removed whole; a single-word
    `silver` would leave a stray `CLOUD` behind and still split the phone in two."""
    assert dmf.family_of("CELERO5G TAB CLOUD SILVER 128 GB",
                         colour_tokens=("cloud silver",),
                         condition_tokens={})["label"] == "CELERO5G TAB"


def test_a_curated_alias_outranks_the_derivation():
    """`device_model_alias.canonical_model` is a human saying what a model is called, and a human
    outranks a rule. That is also how the owner can merge two families the derivation keeps apart."""
    out = dmf.family_of("Apple iPhone 13 128GB Pink CPO TO", colour_tokens=OTHER_COLOURS,
                        condition_tokens={},
                        alias={"apple iphone 13 128gb pink cpo to": "iPhone 13 (all)"})
    assert (out["label"], out["basis"]) == ("iPhone 13 (all)", "declared_alias")


def test_the_family_is_named_by_its_best_seller_and_carries_what_was_sold():
    units, variants, _fams = dmf.group_units(
        {"Apple iPhone 13 128GB Pink CPO TO": {"Sep": 23, "Oct": 2},
         "Apple iPhone 13 128GB Black CPO TO": {"Sep": 6}},
        colour_tokens=OTHER_COLOURS, condition_tokens={})
    assert units == {"Apple iPhone 13 CPO TO": {"Sep": 29, "Oct": 2}}
    rows = variants["Apple iPhone 13 CPO TO"]
    assert [r["model"] for r in rows] == ["Apple iPhone 13 128GB Pink CPO TO",
                                          "Apple iPhone 13 128GB Black CPO TO"]
    assert [r["units_total"] for r in rows] == [25, 6]


def test_the_grouping_never_loses_a_unit():
    """Whatever the folding does, the total has to survive it — a lost unit is an under-order."""
    raw = {m: {"Sep": i + 1} for i, m in enumerate(LIVE_HOUSE + LIVE_OTHER)}
    units, _, _f = dmf.group_units(raw, colour_tokens=HOUSE_COLOURS + OTHER_COLOURS, condition_tokens={})
    assert sum(sum(p.values()) for p in units.values()) \
        == sum(sum(p.values()) for p in raw.values())


def test_rule_two_no_colour_or_brand_word_is_a_value_in_the_module():
    """An AST scan, not a text search, so the module header may still quote the live spellings that
    paid for the fix without failing its own rule."""
    src = open(dmf.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef))
            and getattr(n, "body", None) and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant) and isinstance(n.body[0].value.value, str)}
    values = [n.value.lower() for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]
    for word in ("blk", "black", "midnight", "pink", "iphone", "samsung", "apple", "cpo"):
        assert not any(word == v or word in v.split() for v in values), word
