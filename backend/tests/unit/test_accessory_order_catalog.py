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
#: The five kinds the owner named on 2026-10-30. The catalog has grown since (the distributor's own
#: shelf was read on 2026-11-04 and most of what they sell fits none of the first two grains), so
#: these are asserted as a SUBSET that may never shrink — losing one would drop a kind he asked for.
OWNER_NAMED = {"case", "screen_protector", "cable", "charger", "earphones"}


def test_every_kind_the_owner_named_is_in_the_house_catalog():
    cats, basis = cat.resolve_catalog(None)
    assert basis == "house_default"
    keys = {c["key"] for c in cats}
    assert OWNER_NAMED <= keys, f"a kind he named went missing: {OWNER_NAMED - keys}"


def test_the_standalone_kinds_arrive_un_rated_rather_than_invented():
    """The third grain: a power bank has no phone model, so it is ordered against the total. He has
    declared no attach rate for any of them, and this module's rule is that an un-rated kind says so
    rather than being invented at one per phone — the treatment earphones already got."""
    cats, _basis = cat.resolve_catalog(None)
    standalone = [c for c in cats if c["grain"] == cat.GRAIN_STANDALONE]
    assert {c["key"] for c in standalone} >= {"power_bank", "wireless_charger", "mount_stand"}
    assert all(c["per_device"] is None for c in standalone), \
        "a standalone rate nobody declared would put an invented number on a buyer's sheet"
    assert all(c["dimensions"] for c in standalone)


def test_the_protector_types_and_case_styles_are_his_list():
    """His case words are all still offered — but across the two axes they were always describing.

    Read off the distributor on 2026-11-04: their top split on a case is MagSafe against
    non-MagSafe (how it is BUILT) and the theme is a separate axis of 24 families (how it LOOKS).
    `silicone / kickstand / heavy duty` is construction and `floral / diamond` is decoration, so one
    column holding both could not express "20 heavy-duty, half of them floral"."""
    assert [o["key"] for o in _house("screen_protector")["dimensions"][0]["options"]] \
        == ["clear", "privacy"]
    case = _house("case")
    offered = {o["key"] for d in case["dimensions"] for o in d["options"]}
    assert {"silicone", "kickstand", "heavy_duty", "floral", "diamond"} <= offered
    styles = [o["key"] for o in case["dimensions"][0]["options"]]
    assert styles[:3] == ["silicone", "kickstand", "heavy_duty"]
    assert "floral" not in styles and "diamond" not in styles, "a design is not a construction"
    designs = [o["key"] for o in cat.secondary_dimensions(case)[-1]["options"]]
    assert {"floral", "diamond", "plain"} <= set(designs)


def test_a_charger_is_chosen_on_ports_and_wattage_and_both_lists_are_his():
    ch = _house("charger")
    assert [d["key"] for d in ch["dimensions"]] == ["ports", "wattage"]
    assert [o["key"] for o in ch["dimensions"][0]["options"]] == [
        "single_usb_a", "single_usb_c", "dual_usb_a", "dual_usb_c", "dual_usb_a_and_c"]
    assert [o["key"] for o in ch["dimensions"][1]["options"]] == [
        "5w", "18w", "20w", "45w", "60w", "100w"]


def test_the_earphone_fits_are_the_three_he_named():
    """His three are the first three, in his order. Bluetooth sits after them because the
    distributor's shelf carries it (read 2026-11-04) and a wireless pair has no port at all — but it
    may not displace one of his, which is what the slice pins."""
    fits = [o["key"] for o in _house("earphones")["dimensions"][0]["options"]]
    assert fits[:3] == ["usb_c", "lightning", "jack_3_5mm"]


def test_the_current_standard_is_the_default_connector():
    """*"current standard is type c which should be assigned ot the phone model in the beinging"*."""
    assert cat.default_spec(_house("cable")) == {"connector": "usb_c_to_usb_c"}
    assert cat.default_spec(_house("charger")) == {"ports": "single_usb_c", "wattage": "20w"}


# ── SPLIT vs ASSIGNED ───────────────────────────────────────────────────────────────────────────
def test_cases_and_protectors_split_per_phone_while_cables_and_cubes_consolidate():
    """The five he named keep the grains he described. A kind added since may not move one of them:
    a case is bought per phone and a cable is assigned then summed, whatever else joins the list."""
    cats, _ = cat.resolve_catalog(None)
    grains = {c["key"]: c["grain"] for c in cats}
    assert {k: grains[k] for k in OWNER_NAMED} == {
        "case": "split", "screen_protector": "split",
        "cable": "assigned", "charger": "assigned", "earphones": "assigned"}
    assert set(grains.values()) <= set(cat.GRAINS)


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
    assert OWNER_NAMED <= {c["key"] for c in cats}
    opts = next(c for c in cats if c["key"] == "case")["dimensions"][0]["options"]
    assert [o["key"] for o in opts] == ["silicone", "wallet"]
    assert opts[1]["image_url"] == "https://example.test/w.png"


def test_a_kind_the_house_never_heard_of_can_be_added_whole():
    cats, _ = cat.resolve_catalog([{"key": "tablet_sleeve", "label": "Tablet sleeve",
                                    "grain": "standalone", "per_device": 0.2,
                                    "dimensions": [{"key": "size", "options": [
                                        {"key": "ten_inch", "label": "10 inch"}]}]}])
    sl = next(c for c in cats if c["key"] == "tablet_sleeve")
    assert (sl["label"], sl["grain"], sl["per_device"]) == ("Tablet sleeve", "standalone", 0.2)


def test_a_declared_rate_on_a_standalone_kind_is_honoured():
    """The house ships every standalone kind un-rated; the org declaring one is what turns it on."""
    cats, _ = cat.resolve_catalog([{"key": "power_bank", "per_device": 0.15}])
    pb = next(c for c in cats if c["key"] == "power_bank")
    assert pb["per_device"] == 0.15 and pb["grain"] == cat.GRAIN_STANDALONE
    assert [d["key"] for d in pb["dimensions"]] == ["capacity", "attach"], \
        "declaring a rate may not wipe the axes the house already knew"


def test_junk_falls_back_to_the_house_catalog_rather_than_emptying_it():
    for junk in (None, "", 7, [], [{"label": "no key"}]):
        cats, basis = cat.resolve_catalog(junk)
        assert basis == "house_default" and OWNER_NAMED <= {c["key"] for c in cats}


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
    for word in ("boost", "verizon", "iphone", "samsung", "apple", "luxelink", "cellfonz",
                 "magsafe", "airpods"):
        assert not any(word in v for v in values), word


def test_it_does_not_import_the_sold_line_accessory_classifier():
    """`accessory_catalog` (migs 230/231) classifies what was SOLD; this module is what can be
    BOUGHT. The names are one word apart, which is how a sibling derivation gets born — writing this
    feature overwrote that module once before the name was fixed."""
    tree = ast.parse(open(cat.__file__, encoding="utf-8").read())
    imported = {f"{n.module or ''}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m.endswith("accessory_catalog") for m in imported)
