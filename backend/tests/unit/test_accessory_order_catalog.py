"""Unit: the accessory ordering vocabulary (index §COMMCALC-49.12).

THE OWNER'S OWN TAXONOMY, 2026-10-30: *"scren protectors are clear and privacy, cases are silicon ,
kicstand, drop proof heavy duty, flowers, diamonds etc and option to add more with pictures ,
chargers could be single usb or dual usb for both type a and type c or dual with type a and type c in
one, the chargers could be 5 w , 18 w , 20 w, 45 w , 60 w, 100w , the earphones could be iphone
compatible , 3.5mm or type c compatible"*.

The load-bearing idea under test is the SPLIT / ASSIGNED distinction: a case is bought for a phone
and divides across styles, a cable is bought by connector and consolidates across phones. Get that
wrong and the sheet either hands a buyer thirty identical cable lines or one case line for five
different phones.
"""
import ast

from app.modules.commcalc import accessory_order_catalog as cat


def _house(key):
    cats, _ = cat.resolve_catalog(None)
    return next(c for c in cats if c["key"] == key)


# ── THE TAXONOMY THE OWNER NAMED ────────────────────────────────────────────────────────────────
def test_every_kind_the_owner_named_is_in_the_house_catalog():
    cats, basis = cat.resolve_catalog(None)
    assert basis == "house_default"
    assert {c["key"] for c in cats} == {"case", "screen_protector", "cable", "charger", "earphones"}


def test_the_protector_types_and_case_styles_are_his_list():
    assert [o["key"] for o in _house("screen_protector")["dimensions"][0]["options"]] \
        == ["clear", "privacy"]
    styles = [o["key"] for o in _house("case")["dimensions"][0]["options"]]
    assert styles == ["silicone", "kickstand", "heavy_duty", "floral", "diamond"]


def test_a_charger_is_chosen_on_ports_and_wattage_and_both_lists_are_his():
    ch = _house("charger")
    assert [d["key"] for d in ch["dimensions"]] == ["ports", "wattage"]
    assert [o["key"] for o in ch["dimensions"][0]["options"]] == [
        "single_usb_a", "single_usb_c", "dual_usb_a", "dual_usb_c", "dual_usb_a_and_c"]
    assert [o["key"] for o in ch["dimensions"][1]["options"]] == [
        "5w", "18w", "20w", "45w", "60w", "100w"]


def test_the_earphone_fits_are_the_three_he_named():
    assert [o["key"] for o in _house("earphones")["dimensions"][0]["options"]] \
        == ["usb_c", "lightning", "jack_3_5mm"]


def test_the_current_standard_is_the_default_connector():
    """*"current standard is type c which should be assigned ot the phone model in the beinging"*."""
    assert cat.default_spec(_house("cable")) == {"connector": "usb_c_to_usb_c"}
    assert cat.default_spec(_house("charger")) == {"ports": "single_usb_c", "wattage": "20w"}


# ── SPLIT vs ASSIGNED ───────────────────────────────────────────────────────────────────────────
def test_cases_and_protectors_split_per_phone_while_cables_and_cubes_consolidate():
    cats, _ = cat.resolve_catalog(None)
    grains = {c["key"]: c["grain"] for c in cats}
    assert grains == {"case": "split", "screen_protector": "split",
                      "cable": "assigned", "charger": "assigned", "earphones": "assigned"}


def test_only_the_case_is_ordered_in_colours():
    cats, _ = cat.resolve_catalog(None)
    assert {c["key"] for c in cats if c["colour_split"]} == {"case"}


def test_earphones_carry_no_rate_so_they_are_not_ordered_until_one_is_declared():
    """The owner named earphones as a kind to carry but gave no attach rate. Inventing one per
    device would order a pair with every phone sold; a zero would read as a decision to stock none.
    `None` is neither, and the screen says so."""
    assert _house("earphones")["per_device"] is None
    assert _house("case")["per_device"] == 1.0


# ── THE SHARES ──────────────────────────────────────────────────────────────────────────────────
def test_an_undeclared_mix_is_an_even_split_that_says_it_is_one():
    shares, basis = cat.resolve_option_mix(_house("case"), None)
    assert basis == "even_split"
    assert abs(sum(s["share"] for s in shares) - 1.0) < 1e-9
    assert len(shares) == 5


def test_percentages_and_fractions_cannot_be_read_differently():
    pct, b1 = cat.resolve_option_mix(_house("screen_protector"),
                                     [{"key": "clear", "share": 80}, {"key": "privacy", "share": 20}])
    frac, b2 = cat.resolve_option_mix(_house("screen_protector"),
                                      [{"key": "clear", "share": 0.8}, {"key": "privacy", "share": 0.2}])
    assert (b1, b2) == ("declared", "declared")
    assert [s["share"] for s in pct] == [s["share"] for s in frac] == [0.8, 0.2]


def test_a_mix_that_does_not_add_up_is_rescaled_and_said():
    shares, basis = cat.resolve_option_mix(
        _house("screen_protector"), [{"key": "clear", "share": 60}, {"key": "privacy", "share": 30}])
    assert basis == "declared_normalised"
    assert abs(sum(s["share"] for s in shares) - 1.0) < 1e-9


def test_a_declared_mix_naming_an_option_the_catalog_does_not_have_is_ignored():
    """Otherwise a column appears with no product behind it."""
    shares, _ = cat.resolve_option_mix(
        _house("screen_protector"), [{"key": "clear", "share": 50}, {"key": "hologram", "share": 50}])
    assert [s["key"] for s in shares] == ["clear"]
    assert abs(sum(s["share"] for s in shares) - 1.0) < 1e-9


# ── EXTENDING IT ────────────────────────────────────────────────────────────────────────────────
def test_adding_a_style_keeps_every_other_kind():
    cats, basis = cat.resolve_catalog([{"key": "case", "dimensions": [
        {"key": "style", "options": [{"key": "silicone", "label": "Silicone"},
                                     {"key": "wallet", "label": "Wallet",
                                      "image_url": "https://example.test/w.png"}]}]}])
    assert basis == "declared"
    assert {c["key"] for c in cats} == {"case", "screen_protector", "cable", "charger", "earphones"}
    opts = next(c for c in cats if c["key"] == "case")["dimensions"][0]["options"]
    assert [o["key"] for o in opts] == ["silicone", "wallet"]
    assert opts[1]["image_url"] == "https://example.test/w.png"


def test_a_kind_the_house_never_heard_of_can_be_added_whole():
    cats, _ = cat.resolve_catalog([{"key": "power_bank", "label": "Power bank",
                                    "grain": "assigned", "per_device": 0.2,
                                    "dimensions": [{"key": "capacity", "options": [
                                        {"key": "10000mah", "label": "10,000 mAh"}]}]}])
    pb = next(c for c in cats if c["key"] == "power_bank")
    assert (pb["label"], pb["grain"], pb["per_device"]) == ("Power bank", "assigned", 0.2)


def test_junk_falls_back_to_the_house_catalog_rather_than_emptying_it():
    for junk in (None, "", 7, [], [{"label": "no key"}]):
        cats, basis = cat.resolve_catalog(junk)
        assert basis == "house_default" and len(cats) == 5


# ── THE PER-MODEL ASSIGNMENT ────────────────────────────────────────────────────────────────────
def test_the_house_standard_holds_until_somebody_chooses_otherwise():
    spec, basis = cat.spec_for("Any Phone", _house("cable"), None)
    assert (spec, basis) == ({"connector": "usb_c_to_usb_c"}, "house_default")


def test_one_phone_can_be_moved_to_another_connector_without_moving_the_rest():
    specs = cat.resolve_model_specs([{"model": "Old Phone", "category": "cable",
                                      "dimension": "connector", "option": "usb_a_to_lightning"}])
    moved, b1 = cat.spec_for("Old Phone", _house("cable"), specs)
    other, b2 = cat.spec_for("New Phone", _house("cable"), specs)
    assert (moved["connector"], b1) == ("usb_a_to_lightning", "declared")
    assert (other["connector"], b2) == ("usb_c_to_usb_c", "house_default")


def test_a_model_name_is_matched_whatever_its_spacing_and_case():
    specs = cat.resolve_model_specs([{"model": "  Old   Phone ", "category": "cable",
                                      "dimension": "connector", "option": "usb_a_to_usb_c"}])
    assert cat.spec_for("OLD PHONE", _house("cable"), specs)[0]["connector"] == "usb_a_to_usb_c"


def test_an_option_the_catalog_does_not_offer_is_ignored_not_honoured():
    specs = cat.resolve_model_specs([{"model": "P", "category": "charger",
                                      "dimension": "ports", "option": "triple_usb_c"}])
    assert cat.spec_for("P", _house("charger"), specs)[0]["ports"] == "single_usb_c"


def test_the_spec_reads_as_one_human_line():
    assert cat.spec_label(_house("charger"),
                          {"ports": "dual_usb_a_and_c", "wattage": "45w"}) \
        == "Dual, one USB-A and one USB-C · 45 W"


# ── THE BOUNDARIES THIS FILE OWNS ───────────────────────────────────────────────────────────────
def test_which_product_classes_take_an_accessory_is_a_policy_with_a_house_answer():
    """A router and a SIM kit sit inside a tenant's own device departments and take no case. This is
    a POLICY (house: handsets and tablets), unlike a tenant's own vocabulary, so a house default is
    the right shape for it."""
    assert cat.HOUSE_ACCESSORY_DEVICE_CLASSES == ("phone", "tablet")


def test_rule_two_no_carrier_tenant_or_brand_word_is_a_value_in_the_module():
    src = open(cat.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef))
            and getattr(n, "body", None) and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant) and isinstance(n.body[0].value.value, str)}
    values = [n.value.lower() for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]
    for word in ("boost", "verizon", "iphone", "samsung", "apple", "luxelink", "cellfonz"):
        assert not any(word in v for v in values), word


def test_it_does_not_import_the_sold_line_accessory_classifier():
    """`accessory_catalog` (migs 230/231) classifies what was SOLD; this module is what can be
    BOUGHT. The names are one word apart, which is how a sibling derivation gets born — writing this
    feature overwrote that module once before the name was fixed."""
    tree = ast.parse(open(cat.__file__, encoding="utf-8").read())
    imported = {f"{n.module or ''}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m.endswith("accessory_catalog") for m in imported)
