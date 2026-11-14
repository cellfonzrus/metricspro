"""Integration: an inactive store or person is not offered or listed anywhere (index §CORE-44).

OWNER DIRECTIVE 2026-11-14: *"once the store or employee is made inactive their data should [not] be
showing on any module unless the checkox is clicked for show inactive stores"*.

THE THREE LIVE LEAKS this proves shut, measured on the real routes over the in-memory double:

1. **`GET /core/filter-options` never filtered.** It is the option source behind the shared
   `StandardFilterBar`, so it is the Stores / People dropdown on most reports. It SELECTED
   `is_active` for employees and then ignored it, and never filtered stores at all. Every adopting
   report offered closed stores and terminated people as things to filter by.
2. **`GET /storeops/employees` filtered `.eq("is_active", True)`, which DROPS NULL.** The column is
   `BOOLEAN DEFAULT true` and nullable (migration 003), so a row written before the column existed
   carries NULL and is a LIVE employee — and vanished from every roster, picker and schedule.
   The store side of the same module read NULL as active. Two answers, one question.
3. **`GET /hr/employee-database` had the same NULL drop**, and its own `include_inactive` defaulted
   TRUE, i.e. the opposite of the directive.

CROSS-TENANT (project rule 2026-10-30): every case is asserted for a SECOND org with its own rows,
and the negative is asserted too — one tenant's roster never appears in the other's answer. The
inactive rule is a POLICY, not a tenant's vocabulary, so a house default is correct here; what must
never cross is the DATA.
"""
import pytest

pytestmark = pytest.mark.integration

HOUSE = "00000000-0000-0000-0000-000000000001"
OTHER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def rostered(monkeypatch, fake_supabase):
    """Two tenants, each with a live store, a closed store, a store whose flag was never set, and the
    same three shapes of employee."""
    monkeypatch.setattr("app.modules.core.router.sb", lambda: fake_supabase)
    monkeypatch.setattr("app.modules.storeops.router.sb",
                        lambda: fake_supabase.schema("storeops"))
    monkeypatch.setattr("app.modules.storeops.router.get_supabase", lambda: fake_supabase)
    fake_supabase.seed("storeops.app_config", [{"id": 1, "rbac_enabled": False}])
    fake_supabase.seed("storeops.stores", [
        {"id": "s1", "org_id": HOUSE, "store_code": "B-1115", "address": "1115 Liberty Ave",
         "market": "LI", "is_active": True},
        {"id": "s2", "org_id": HOUSE, "store_code": "B-2778", "address": "2778 Nostrand Ave",
         "market": "LI", "is_active": False},
        {"id": "s3", "org_id": HOUSE, "store_code": "B-1598", "address": "1598 Utica Ave",
         "market": "LI"},                                        # flag never set -> LIVE
        {"id": "s9", "org_id": OTHER, "store_code": "L-7001", "address": "7001 Luxe Row",
         "market": "NY", "is_active": True},
        {"id": "s8", "org_id": OTHER, "store_code": "L-7002", "address": "7002 Luxe Row",
         "market": "NY", "is_active": False},
    ])
    fake_supabase.seed("storeops.employees", [
        {"id": 1, "org_id": HOUSE, "employee_id": "E001", "name": "Active Ann",
         "home_store": "B-1115", "is_active": True},
        {"id": 2, "org_id": HOUSE, "employee_id": "E002", "name": "Closed Carl",
         "home_store": "B-1115", "is_active": False},
        {"id": 3, "org_id": HOUSE, "employee_id": "E003", "name": "Nullflag Nina",
         "home_store": "B-1598"},                                # flag never set -> LIVE
        {"id": 9, "org_id": OTHER, "employee_id": "L901", "name": "Luxe Lee",
         "home_store": "L-7001", "is_active": True},
        {"id": 8, "org_id": OTHER, "employee_id": "L902", "name": "Luxe Gone",
         "home_store": "L-7001", "is_active": False},
    ])
    fake_supabase.seed("storeops.app_users", [])
    return fake_supabase


# ── 1. the shared option source behind every adopting report's filter bar ────────────────────────

def _opts(org, include_inactive=False):
    """The endpoint's own answer, reduced to the store spellings it offers."""
    from app.modules.core.router import filter_options
    out = filter_options(org_id=org, include_inactive=include_inactive)
    return {"stores": [s["store"] for s in out["stores"]],
            "reps": [r["label"] for r in out["reps"]],
            "inactive_hidden": out["inactive_hidden"]}


def test_the_shared_filter_options_no_longer_offer_a_closed_store(rostered):
    out = _opts(HOUSE)
    assert "2778 Nostrand Ave" not in out["stores"], (
        "the Stores dropdown on most reports offered a closed store")
    assert "1115 Liberty Ave" in out["stores"]


def test_a_store_whose_flag_was_never_set_is_still_offered(rostered):
    assert "1598 Utica Ave" in _opts(HOUSE)["stores"], (
        "NULL is ACTIVE — the column defaults true; hiding it deletes a live store")


def test_the_shared_filter_options_no_longer_offer_a_terminated_person(rostered):
    reps = _opts(HOUSE)["reps"]
    assert "Closed Carl" not in reps
    assert "Active Ann" in reps
    assert "Nullflag Nina" in reps


def test_the_optin_brings_the_inactive_ones_back_for_the_show_inactive_tick(rostered):
    out = _opts(HOUSE, include_inactive=True)
    assert "2778 Nostrand Ave" in out["stores"]
    assert "Closed Carl" in out["reps"]


def test_the_option_source_says_how_many_it_left_out(rostered):
    """Hiding is never silent: the bar needs a number to put beside its checkbox."""
    assert _opts(HOUSE)["inactive_hidden"] == 1
    assert _opts(HOUSE, include_inactive=True)["inactive_hidden"] == 0


def test_one_tenant_never_sees_the_other_tenants_roster(rostered):
    house, other = _opts(HOUSE), _opts(OTHER)
    assert "7001 Luxe Row" not in house["stores"]
    assert "Luxe Lee" not in house["reps"]
    assert "1115 Liberty Ave" not in other["stores"]
    assert "Active Ann" not in other["reps"]


def test_the_rule_holds_for_the_second_tenant_too(rostered):
    """The feature is for EVERY tenant (project rule 2026-10-30), measured on the other org's rows."""
    other = _opts(OTHER)
    assert other["stores"] == ["7001 Luxe Row"]
    assert other["reps"] == ["Luxe Lee"]


# ── 2. the employee roster, and the NULL it used to drop ─────────────────────────────────────────

def test_the_employee_roster_keeps_a_person_whose_flag_was_never_set(rostered):
    from app.modules.storeops.router import get_employees
    names = [e["name"] for e in get_employees(org_id=HOUSE)]
    assert "Nullflag Nina" in names, (
        "`.eq(is_active, True)` dropped NULL — a live employee vanished from every roster")
    assert "Closed Carl" not in names
    assert "Active Ann" in names


def test_the_employee_roster_still_opens_up_on_the_optin(rostered):
    from app.modules.storeops.router import get_employees
    names = [e["name"] for e in get_employees(include_inactive=True, org_id=HOUSE)]
    assert {"Active Ann", "Closed Carl", "Nullflag Nina"} <= set(names)


def test_the_store_roster_and_the_employee_roster_now_answer_the_same_way(rostered):
    """The defect class: two readers of the same kind of column disagreeing about NULL."""
    from app.modules.storeops.router import get_employees, get_stores
    codes = [s["store_code"] for s in get_stores(org_id=HOUSE)]
    names = [e["name"] for e in get_employees(org_id=HOUSE)]
    assert "B-1598" in codes and "Nullflag Nina" in names      # NULL is active on both sides
    assert "B-2778" not in codes and "Closed Carl" not in names  # explicit false is out on both


def test_the_employee_roster_is_org_scoped_as_well(rostered):
    from app.modules.storeops.router import get_employees
    assert [e["name"] for e in get_employees(org_id=OTHER)] == ["Luxe Lee"]


# ── 3. the one home is the only thing deciding this ──────────────────────────────────────────────

def test_both_rosters_give_the_answer_the_one_home_gives(rostered):
    """Not just "the right rows" — the SAME rule. A reader that happened to agree today, with its own
    copy of the test, is the patchwork the house forbids."""
    from app.modules.core import roster_visibility as rv
    from app.modules.storeops.router import get_employees, get_stores

    shapes = [{"k": "B-1115", "is_active": True}, {"k": "B-2778", "is_active": False},
              {"k": "B-1598"}]
    expected = [r["k"] for r in rv.visible(shapes)]
    assert [s["store_code"] for s in get_stores(org_id=HOUSE)] == expected
    assert [e["employee_id"] for e in get_employees(org_id=HOUSE)] == ["E001", "E003"]
