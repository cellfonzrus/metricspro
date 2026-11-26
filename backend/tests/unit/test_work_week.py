"""§STOREOPS-17 — "which seven days is this store's week", and "which days is it open".

DB-FREE. Every test here calls the pure core (`resolve_start_dow` / `resolve_open_dows`) with rows
handed to it, which is the whole point of splitting the decision from the read: the ordering between
market, tenant and the house default is provable without a database, and so is the difference
between a declaration and its absence.

THE DEFECT BEING PINNED (owner 2026-11-26). `storeops.tenants.work_week_start_dow` held one week
start per tenant. Luxelink is one tenant whose two markets run different weeks — Chicago Thu–Wed, NY
Mon–Sun — so its 10 open NY stores were bucketed Thu–Wed with no row in which to say otherwise.
"""
from app.modules.storeops import work_week as ww

CHI = [{"market": "Chicago", "work_week_start_dow": 3}]
BOTH = [{"market": "Chicago", "work_week_start_dow": 3},
        {"market": "NY", "work_week_start_dow": 0}]


# ── the order of resolution ──────────────────────────────────────────────────────────────────────
def test_a_market_with_its_own_week_answers_before_its_tenant():
    got = ww.resolve_start_dow(market_rows=CHI, tenant_dow=0, market="Chicago")
    assert got == {"dow": 3, "level": ww.LEVEL_MARKET, "declared": True}


def test_a_market_with_no_row_falls_through_to_its_tenant():
    """THE LUXELINK NY CASE, the right way round: the tenant answers, and says it was the tenant."""
    got = ww.resolve_start_dow(market_rows=CHI, tenant_dow=0, market="NY")
    assert got == {"dow": 0, "level": ww.LEVEL_TENANT, "declared": True}


def test_both_markets_declared_each_gets_its_own_answer_from_one_tenant():
    chi = ww.resolve_start_dow(market_rows=BOTH, tenant_dow=3, market="Chicago")
    ny = ww.resolve_start_dow(market_rows=BOTH, tenant_dow=3, market="NY")
    assert (chi["dow"], ny["dow"]) == (3, 0)
    assert chi["level"] == ny["level"] == ww.LEVEL_MARKET


def test_no_market_asked_answers_for_the_tenant():
    """A tenant-wide caller (a board header, a whole-tenant rota send) must not be given one
    market's week just because a market happens to have declared one."""
    got = ww.resolve_start_dow(market_rows=BOTH, tenant_dow=3, market=None)
    assert got == {"dow": 3, "level": ww.LEVEL_TENANT, "declared": True}


def test_nothing_declared_anywhere_is_the_house_monday_and_says_it_is_undeclared():
    got = ww.resolve_start_dow(market_rows=(), tenant_dow=None, market="NY")
    assert got == {"dow": ww.HOUSE_START_DOW, "level": ww.LEVEL_HOUSE, "declared": False}
    assert got["dow"] == 0


def test_a_tenant_that_declared_monday_is_still_DECLARED():
    """0 is a declaration, not an absence. A screen that reads `declared` to decide whether to offer
    "this isn't set up" must not nag a tenant that has set its week to Monday on purpose."""
    assert ww.resolve_start_dow(market_rows=(), tenant_dow=0, market=None)["declared"] is True


# ── a typo is not Monday ─────────────────────────────────────────────────────────────────────────
def test_an_out_of_range_market_value_falls_through_rather_than_becoming_monday():
    got = ww.resolve_start_dow(market_rows=[{"market": "NY", "work_week_start_dow": 9}],
                               tenant_dow=3, market="NY")
    assert got == {"dow": 3, "level": ww.LEVEL_TENANT, "declared": True}


def test_an_unparseable_tenant_value_reaches_the_house_default_and_says_undeclared():
    got = ww.resolve_start_dow(market_rows=(), tenant_dow="thursday", market=None)
    assert got["level"] == ww.LEVEL_HOUSE and got["declared"] is False


def test_a_market_row_with_no_market_name_cannot_answer_for_everybody():
    got = ww.resolve_start_dow(market_rows=[{"market": "", "work_week_start_dow": 3}],
                               tenant_dow=0, market="NY")
    assert got["level"] == ww.LEVEL_TENANT


# ── the market name is compared the way the roster spells it ─────────────────────────────────────
def test_market_matching_ignores_case_and_surrounding_space():
    """A market typed 'chicago' and a market resolved 'Chicago' are the SAME market. Comparing them
    raw is how a second spelling becomes a phantom entity (§13e)."""
    for spelling in ("chicago", "CHICAGO", "  Chicago  "):
        assert ww.resolve_start_dow(market_rows=CHI, tenant_dow=0,
                                    market=spelling)["dow"] == 3, spelling


def test_a_market_nobody_declared_does_not_borrow_another_markets_week():
    assert ww.resolve_start_dow(market_rows=BOTH, tenant_dow=0, market="Detroit")["dow"] == 0


# ── open days: nothing stands behind a tenant's own statement ────────────────────────────────────
def test_a_stores_own_open_days_win_over_its_tenants():
    got = ww.resolve_open_dows(store_dows=[0, 1, 2, 3, 4], tenant_dows=[0, 1, 2, 3, 4, 5, 6])
    assert got == {"dows": (0, 1, 2, 3, 4), "level": ww.LEVEL_STORE, "declared": True}


def test_a_store_with_nothing_declared_takes_its_tenants_days():
    got = ww.resolve_open_dows(store_dows=None, tenant_dows=[0, 1, 2, 3, 4, 5, 6])
    assert got == {"dows": ww.WEEKDAYS, "level": ww.LEVEL_TENANT, "declared": True}


def test_nothing_declared_anywhere_is_UNDECLARED_and_never_seven_days():
    """THE RULE THIS PINS (owner 2026-10-30): nothing declared means nothing identified. A seven-day
    week assumed here would be the house answering for a tenant, and the scheduling flow would start
    calling a closed Sunday a gap."""
    got = ww.resolve_open_dows(store_dows=None, tenant_dows=None)
    assert got == {"dows": (), "level": None, "declared": False}


def test_an_empty_declaration_is_a_declaration_not_an_absence():
    """"We have not said" and "open no day" must not read the same."""
    got = ww.resolve_open_dows(store_dows=[], tenant_dows=[0, 1, 2])
    assert got["declared"] is True and got["dows"] == ()
    assert got["level"] == ww.LEVEL_STORE


def test_a_declared_set_is_sorted_and_deduplicated():
    assert ww.resolve_open_dows(store_dows=[5, 0, 5, 1], tenant_dows=None)["dows"] == (0, 1, 5)


def test_a_set_containing_a_non_weekday_is_refused_WHOLE_not_trimmed():
    """Trimming would turn 'Mon–Fri plus a typo' into a smaller week nobody declared, and the
    scheduler would then flag a covered day as uncovered."""
    got = ww.resolve_open_dows(store_dows=[0, 1, 7], tenant_dows=[0, 1, 2, 3, 4, 5, 6])
    assert got["level"] == ww.LEVEL_TENANT


def test_a_string_is_not_a_day_set():
    """A column read back as text must not be iterated into characters."""
    assert ww.resolve_open_dows(store_dows="0,1,2", tenant_dows=None)["declared"] is False
