"""Unit: "was this person at the store this punch is for?" — the ONE presence decision.

OWNER ASK 2026-11-01 (verbatim): *"the sales reps when they log in should capture the gos location
so they are not clocking out at their homes and the geolocation when they clock out from somewhere
else than their usual location which should match the store address should be reported as flag to
the management"*.

WHAT THIS MODULE IS NOT ALLOWED TO BE. A second geofence. `core/geo.evaluate_checkin` has been THE
geofence decision in this platform since the marketing event check-in (index §23), and it already
takes the reported accuracy seriously. So this module resolves the INPUTS a punch has and the
marketing module did not — which store, whose pin, whose radius — and hands them to that function.
A haversine written here, or a radius compared here, would be the duplicate defect CLAUDE.md's
build gate forbids. `test_Z*` below fails the build if either appears.

THE CASE THAT MATTERS MOST IS THE ONE WITH NO ANSWER. A store nobody has pinned cannot produce a
verdict, and the honest answer is "could not verify" — never "they were not there". Zero stores are
pinned on the day this ships, so a module that read an unpinned store as a violation would flag
every punch in the company at once. §B.

MULTI-TENANT (project rule 2026-10-30). The radius and the accuracy limit are POLICY, so a house
default is right for them; the PIN is the tenant's own declaration and has no default at all.
Every case that touches config runs for three tenant shapes: one that declares a tight fence, one
that declares nothing, and one that has switched the check off. §T.
"""
import pytest

from app.modules.core import geo
from app.modules.core import store_geofence as sg

# A store's pin, and points a measured distance from it. 0.00100 degrees of latitude is 111.2 m.
PIN_LAT, PIN_LNG = 40.650000, -73.950000
AT_DOOR = (40.650000, -73.950000)            # 0 m
NEAR = (40.650450, -73.950000)               # ~50 m
JUST_OUTSIDE = (40.651800, -73.950000)       # ~200 m
AT_HOME = (40.700000, -73.950000)            # ~5.6 km — the case the owner described

HOUSE = "00000000-0000-0000-0000-000000000001"
SOLO = "11111111-2222-3333-4444-555555555555"
BARE = "99999999-8888-7777-6666-555555555555"

PINNED = {"store_code": "B-1115", "address": "2723 Example Ave",
          "geo_lat": PIN_LAT, "geo_lng": PIN_LNG, "geofence_radius_m": None}
UNPINNED = {"store_code": "B-1598", "address": "88 Utica Ave",
            "geo_lat": None, "geo_lng": None, "geofence_radius_m": None}
TIGHT = {"store_code": "L-01", "address": "1 Solo Way",
         "geo_lat": PIN_LAT, "geo_lng": PIN_LNG, "geofence_radius_m": 40}


def _ev(kind="clock_out", fix=AT_DOOR, store=None, accuracy=10, config=None):
    store = PINNED if store is None else store
    return sg.evaluate_presence(kind, fix_lat=(None if fix is None else fix[0]),
                                fix_lng=(None if fix is None else fix[1]),
                                fix_accuracy_m=accuracy, store_row=store,
                                config=(config or dict(sg.DEFAULT_CONFIG)))


# ── §A. the verdict a manager acts on ────────────────────────────────────────────────────────────
def test_A1_a_punch_at_the_store_is_inside_and_is_not_flagged():
    v = _ev(fix=AT_DOOR)
    assert v["decision"] == geo.INSIDE and v["within_geofence"] is True
    assert v["off_site"] is False and v["distance_m"] == 0


def test_A2_a_punch_fifty_metres_away_is_still_inside_the_house_fence():
    v = _ev(fix=NEAR)
    assert v["decision"] == geo.INSIDE and v["off_site"] is False
    assert 45 <= v["distance_m"] <= 55


def test_A3_THE_OWNERS_CASE_a_clock_out_from_home_is_outside_and_flagged():
    v = _ev(kind="clock_out", fix=AT_HOME)
    assert v["decision"] == geo.OUTSIDE and v["within_geofence"] is False
    assert v["off_site"] is True
    assert 5500 <= v["distance_m"] <= 5700
    assert v["kind"] == "clock_out"


def test_A4_a_punch_is_NEVER_refused_only_recorded_and_flagged():
    """The owner asked for a flag to management, not a lock on the door. A rep whose phone puts them
    across the street must still be able to end their shift; the row is what a manager reviews."""
    for fix in (AT_DOOR, JUST_OUTSIDE, AT_HOME, None):
        assert _ev(fix=fix)["accepted"] is True


def test_A5_the_clock_in_is_judged_by_the_same_rule_as_the_clock_out():
    assert _ev(kind="clock_in", fix=AT_HOME)["off_site"] is True
    assert _ev(kind="clock_in", fix=AT_DOOR)["off_site"] is False


def test_A6_a_store_visit_check_in_is_judged_by_the_same_rule():
    """THE SIBLING (CLAUDE.md: find them before you ship). storevisit captures a fix and has never
    judged one; it asks the identical question and must not grow a second answer to it."""
    assert sg.KIND_STORE_VISIT in sg.KINDS
    assert _ev(kind=sg.KIND_STORE_VISIT, fix=AT_HOME)["off_site"] is True


def test_A7_an_unruled_kind_is_refused_rather_than_silently_judged():
    with pytest.raises(ValueError):
        _ev(kind="whatever_the_next_caller_invents")


# ── §B. what cannot be answered is never answered against the employee ──────────────────────────
def test_B1_a_store_nobody_pinned_cannot_produce_a_violation():
    """Zero stores are pinned on the day this ships. If this read as `outside`, every punch in the
    company would flag at once and the board would be useless on its first morning."""
    v = _ev(fix=AT_HOME, store=UNPINNED)
    assert v["decision"] == geo.UNVERIFIED_NO_TARGET
    assert v["within_geofence"] is None and v["off_site"] is False
    assert v["needs_store_pin"] is True


def test_B2_a_device_that_reported_no_position_is_unverified_not_absent():
    v = _ev(fix=None)
    assert v["decision"] == geo.UNVERIFIED_NO_FIX
    assert v["within_geofence"] is None and v["off_site"] is False


def test_B3_a_fix_too_coarse_to_settle_a_borderline_is_unverified():
    """±900 m on a 150 m fence cannot tell the door from the next neighbourhood."""
    v = _ev(fix=JUST_OUTSIDE, accuracy=900)
    assert v["decision"] == geo.UNVERIFIED_ACCURACY
    assert v["within_geofence"] is None and v["off_site"] is False


def test_B4_a_coarse_fix_still_flags_when_even_its_best_case_is_off_site():
    """Honesty cuts both ways: ±200 m cannot rescue a punch 5.6 km away."""
    v = _ev(fix=AT_HOME, accuracy=200)
    assert v["decision"] == geo.OUTSIDE and v["off_site"] is True


def test_B5_every_unverified_decision_carries_a_sentence_a_manager_can_read():
    for v in (_ev(fix=None), _ev(fix=AT_HOME, store=UNPINNED), _ev(fix=JUST_OUTSIDE, accuracy=900)):
        assert v["note"] and len(v["note"]) > 20


# ── §C. the radius is declared, with a house POLICY default behind it ───────────────────────────
def test_C1_the_house_default_fence_applies_when_nothing_is_declared():
    assert _ev(fix=NEAR)["radius_m"] == sg.DEFAULT_CONFIG["punch_geofence_radius_m"] == 150


def test_C2_a_tenants_declared_fence_overrides_the_house_default():
    cfg = {**sg.DEFAULT_CONFIG, "punch_geofence_radius_m": 60}
    v = _ev(fix=NEAR, config=cfg)
    assert v["radius_m"] == 60 and v["off_site"] is False     # ~50 m, inside 60
    assert _ev(fix=(40.651000, -73.950000), config=cfg)["off_site"] is True   # ~111 m, outside 60


def test_C3_a_STORE_may_hold_a_tighter_fence_than_its_tenant():
    """A mall kiosk and a standalone shop are not the same fence. The store's own radius wins."""
    v = _ev(fix=NEAR, store=TIGHT)
    assert v["radius_m"] == 40 and v["off_site"] is True


def test_C4_an_absurd_declared_radius_is_clamped_not_obeyed():
    """A real but silly NUMBER is clamped to something physically sensible. Junk — zero, negative,
    a word — is not a declaration at all and falls back to the house default, because clamping it
    would turn a typo into a 10 m fence that flags the whole company."""
    house = sg.DEFAULT_CONFIG["punch_geofence_radius_m"]
    for bad, ok in ((5, geo.MIN_RADIUS_M), (10 ** 9, geo.MAX_RADIUS_M),
                    (0, house), (-5, house), ("nonsense", house), ("", house), (None, house)):
        got = _ev(config={**sg.DEFAULT_CONFIG, "punch_geofence_radius_m": bad})["radius_m"]
        assert got == ok, f"radius {bad!r} resolved to {got}, expected {ok}"


def test_C5_the_switch_being_off_means_no_verdict_at_all_not_a_passing_one():
    """A tenant that has switched the check off must store NOTHING, so turning it on later cannot
    find a column full of verdicts nobody measured."""
    assert _ev(fix=AT_HOME, config={**sg.DEFAULT_CONFIG, "punch_geo_enabled": False}) is None


# ── §D. the pin is a fact with one home ─────────────────────────────────────────────────────────
def test_D1_the_pin_is_read_from_the_store_row_and_nowhere_else():
    assert sg.store_pin(PINNED) == {"lat": PIN_LAT, "lng": PIN_LNG, "radius_m": None}
    assert sg.store_pin(TIGHT)["radius_m"] == 40


def test_D2_a_store_with_no_pin_or_a_junk_pin_has_no_pin():
    for row in (UNPINNED, {}, None, {"geo_lat": "", "geo_lng": ""},
                {"geo_lat": 91.0, "geo_lng": 0.0}, {"geo_lat": "abc", "geo_lng": "def"},
                {"geo_lat": 40.6, "geo_lng": None}):
        assert sg.store_pin(row) is None, row


def test_D3_a_pin_sent_as_strings_is_still_a_pin():
    """Every JSON body spells a number as a string sooner or later."""
    assert sg.store_pin({"geo_lat": "40.65", "geo_lng": "-73.95"}) == {
        "lat": 40.65, "lng": -73.95, "radius_m": None}


def test_D4_which_column_holds_which_fact_is_declared_once():
    """The writer (the punch endpoints) and the reader (the exceptions board) must agree on the
    column names or the board reads NULL forever. So the mapping is one table, dereferenced."""
    for kind in ("clock_in", "clock_out"):
        cols = sg.PUNCH_COLUMNS[kind]
        assert set(cols) == {"lat", "lng", "accuracy", "decision", "distance"}
        assert len(set(cols.values())) == 5
    ci, co = sg.PUNCH_COLUMNS["clock_in"], sg.PUNCH_COLUMNS["clock_out"]
    assert ci["lat"] == "gps_lat", "the clock-in keeps the columns migration 045 already created"
    assert not (set(ci.values()) & set(co.values())), \
        "a clock-out fix must never overwrite the clock-in's — they are two different facts"


def test_D5_a_verdict_is_turned_into_stored_columns_by_the_same_table():
    v = _ev(kind="clock_out", fix=AT_HOME)
    row = sg.stored_columns("clock_out", v, fix_lat=AT_HOME[0], fix_lng=AT_HOME[1],
                            fix_accuracy_m=10)
    assert row["clock_out_gps_lat"] == AT_HOME[0]
    assert row["clock_out_geo_decision"] == geo.OUTSIDE
    assert row["clock_out_geo_distance_m"] == v["distance_m"]


def test_D6_a_fix_is_stored_even_when_there_is_no_verdict_to_store():
    """The pin arrives later. The coordinates are what make a retrospective answer possible, so a
    punch at an unpinned store still records where it happened."""
    row = sg.stored_columns("clock_out", _ev(fix=AT_DOOR, store=UNPINNED),
                            fix_lat=AT_DOOR[0], fix_lng=AT_DOOR[1], fix_accuracy_m=10)
    assert row["clock_out_gps_lat"] == AT_DOOR[0]
    assert row["clock_out_geo_decision"] == geo.UNVERIFIED_NO_TARGET
    assert row["clock_out_geo_distance_m"] is None


# ── §E. reading a stored punch back ─────────────────────────────────────────────────────────────
def test_E1_a_stored_off_site_punch_is_recognised_from_its_row():
    punch = {"clock_out_geo_decision": geo.OUTSIDE, "clock_out_geo_distance_m": 5600}
    flags = sg.punch_off_site_flags(punch)
    assert [f["kind"] for f in flags] == ["clock_out"]
    assert flags[0]["distance_m"] == 5600


def test_E2_both_ends_of_one_punch_can_be_flagged_independently():
    punch = {"clock_in_geo_decision": geo.INSIDE, "clock_out_geo_decision": geo.OUTSIDE}
    assert [f["kind"] for f in sg.punch_off_site_flags(punch)] == ["clock_out"]
    punch = {"clock_in_geo_decision": geo.OUTSIDE, "clock_out_geo_decision": geo.OUTSIDE}
    assert [f["kind"] for f in sg.punch_off_site_flags(punch)] == ["clock_in", "clock_out"]


def test_E3_an_unverified_or_absent_verdict_is_never_a_flag():
    for d in (None, "", geo.INSIDE, geo.UNVERIFIED_NO_FIX, geo.UNVERIFIED_NO_TARGET,
              geo.UNVERIFIED_ACCURACY, "something_a_later_version_invented"):
        assert sg.punch_off_site_flags({"clock_in_geo_decision": d,
                                        "clock_out_geo_decision": d}) == []


def test_E4_the_flag_is_decided_by_an_ALLOWLIST_so_an_unruled_word_cannot_accuse_anyone():
    """Same posture as `carrier_dollar_class.component_is_evidence` (index §66): a decision this
    version has never heard of reads as "not a violation", never as one."""
    assert sg.is_off_site(geo.OUTSIDE) is True
    assert sg.is_off_site("outside_ish") is False


# ── §T. THE CROSS-TENANT PASS (project rule 2026-10-30) ─────────────────────────────────────────
TENANTS = [
    (HOUSE, {**sg.DEFAULT_CONFIG}),                                              # house policy
    (SOLO, {**sg.DEFAULT_CONFIG, "punch_geofence_radius_m": 40}),                 # tight fence
    (BARE, {**sg.DEFAULT_CONFIG, "punch_geo_enabled": False}),                    # switched off
]


@pytest.mark.parametrize("org,cfg", TENANTS)
def test_T1_every_tenant_gets_an_answer_under_its_OWN_policy(org, cfg):
    v = _ev(fix=AT_HOME, config=cfg)
    if not cfg["punch_geo_enabled"]:
        assert v is None, f"{org} has the check off and still got a verdict"
    else:
        assert v["off_site"] is True and v["radius_m"] == cfg["punch_geofence_radius_m"]


@pytest.mark.parametrize("org,cfg", TENANTS)
def test_T2_a_tenant_with_no_pinned_store_is_told_so_rather_than_handed_zero_flags_quietly(org, cfg):
    v = _ev(fix=AT_HOME, store=UNPINNED, config=cfg)
    if v is not None:
        assert v["needs_store_pin"] is True and v["off_site"] is False


@pytest.mark.parametrize("org,cfg", TENANTS)
def test_T3_one_tenants_fence_never_judges_anothers_punch(org, cfg):
    """~50 m from the door is inside the house fence and outside a 40 m one. The verdict must follow
    the config passed in, with no module-level state that could carry one tenant's policy over."""
    v = _ev(fix=NEAR, config=cfg)
    if v is not None:
        assert v["off_site"] is (cfg["punch_geofence_radius_m"] < 50)


def test_T4_the_default_config_is_POLICY_only_and_names_no_tenant_vocabulary():
    """The one place a house default is BANNED is a tenant's own words (owner 2026-10-30). A radius,
    an accuracy limit and a switch are policy; a store's pin is the tenant's and has no default."""
    assert set(sg.DEFAULT_CONFIG) == {"punch_geo_enabled", "punch_geofence_radius_m",
                                      "punch_geo_max_accuracy_m"}
    assert "geo_lat" not in sg.DEFAULT_CONFIG and "geo_lng" not in sg.DEFAULT_CONFIG


# ── §Z. the locks (a second geofence cannot appear) ─────────────────────────────────────────────
def _src(rel):
    import pathlib
    return (pathlib.Path(__file__).resolve().parents[2] / rel).read_text()


def test_Z1_this_module_dereferences_the_one_geofence_decision_and_writes_no_second_one():
    src = _src("app/modules/core/store_geofence.py")
    assert "evaluate_checkin" in src, "the shared decision is not dereferenced"
    for banned in ("haversine(", "math.asin", "math.radians", "6371", "EARTH_RADIUS"):
        assert banned not in src, f"a second distance calculation ({banned}) appeared here"


def test_Z2_no_tenant_carrier_or_store_name_is_spelled_in_either_home():
    banned = ("boost", "luxelink", "cellfonz", "vzone", "b-1115", "total wireless")
    for rel in ("app/modules/core/store_geofence.py", "app/modules/core/geo.py"):
        low = _src(rel).lower()
        assert not [b for b in banned if b in low], rel


def test_Z3_the_module_stays_a_leaf_so_it_is_provable_without_the_app():
    src = _src("app/modules/core/store_geofence.py")
    for banned in ("import fastapi", "from fastapi", "import supabase"):
        assert banned not in src, f"{banned} would stop this being provable in a bare container"
