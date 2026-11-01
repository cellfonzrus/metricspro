"""Integration: the Run Calculation's input gathering reads each relation from ITS OWN schema.

THE MEASURED DEFECT (production, 2026-10-31). `router._calc_inputs` gathers every read the full
calculation makes, through one local `fetch(table)` that hard-codes `client.schema('commcalc')`.
Two of the names passed to it — `employees` and `stores` — are `storeops` relations. Since
`core.feed_read.read_all` was made to RAISE rather than mistake a failed read for a small feed,
those two reads have killed the whole calculation on its first line: LuxeLink `error` for
June-October, Cellfonz R Us for July-October, every one of them *"Could not find the table
'commcalc.employees' in the schema cache"*. So no plan edit, no upload and no button could have
recalculated anything for about eight weeks.

MONEY-NEUTRAL, AND THAT IS ASSERTED HERE. The calculator uses `employees` and `stores` only inside
`if G['acc_target_on'] and shifts and employees`, and `_calc_inputs` passes `shifts=[]`
unconditionally, so `rep_acc_targets` is `{}` either way. Reading the two relations correctly
therefore restores the run without moving a payout — the last test in this file is that proof.
"""
import pytest

ORG = "00000000-0000-0000-0000-000000000001"
PERIOD = "October 2026"


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    from app.modules.commcalc import router as commcalc_router
    monkeypatch.setattr("app.modules.commcalc.router.sb", lambda: fake_supabase)
    for name in ("_ACFG_CACHE", "_CFG_CACHE"):
        cache = getattr(commcalc_router, name, None)
        if isinstance(cache, dict):
            cache.clear()
    fake_supabase.seed("storeops.employees", [
        {"id": "e1", "org_id": ORG, "name": "ARORA, TANISH", "home_store": "B-1115",
         "epay_salesperson": "ARORA, TANISH", "is_active": True}])
    fake_supabase.seed("storeops.stores", [
        {"id": "s1", "org_id": ORG, "store_code": "B-1115", "monthly_target": 4000}])
    return fake_supabase


def test_the_gathering_completes_instead_of_raising_on_a_wrong_schema(wired):
    from app.modules.commcalc.router import _calc_inputs
    got = _calc_inputs(wired, ORG, PERIOD)          # raised IncompleteRead before the fix
    assert set(("employees", "stores")) <= set(got)


def test_the_roster_and_the_stores_come_back_from_storeops(wired):
    from app.modules.commcalc.router import _calc_inputs
    got = _calc_inputs(wired, ORG, PERIOD)
    assert [e["name"] for e in got["employees"]] == ["ARORA, TANISH"]
    assert [s["store_code"] for s in got["stores"]] == ["B-1115"]


def test_it_never_asks_commcalc_for_a_storeops_relation(wired):
    """The assertion that pins the bug itself: the read must not be addressed to commcalc."""
    from app.modules.commcalc.router import _calc_inputs
    _calc_inputs(wired, ORG, PERIOD)
    asked = {key for key, _filters in wired.reads}
    assert "commcalc.employees" not in asked
    assert "commcalc.stores" not in asked
    assert "storeops.employees" in asked
    assert "storeops.stores" in asked


def test_every_read_is_org_scoped(wired):
    """A calc that folded another tenant's roster in would be the leak the org guard exists for."""
    from app.modules.commcalc.router import _calc_inputs
    _calc_inputs(wired, ORG, PERIOD)
    for key, filters in wired.reads:
        if key in ("storeops.employees", "storeops.stores"):
            assert ("eq", "org_id", ORG) in filters, key


def test_reading_the_roster_correctly_moves_no_payout(wired):
    """MONEY-NEUTRALITY, proved rather than asserted in prose: the calculator's only use of these
    two relations is gated on `shifts`, and the gathering passes `shifts=[]`."""
    from app.modules.commcalc.router import _calc_inputs, _calc_rep_rows
    got = _calc_inputs(wired, ORG, PERIOD)
    assert got["shifts"] == []
    with_roster = _calc_rep_rows(got, PERIOD)
    without = _calc_rep_rows({**got, "employees": [], "stores": []}, PERIOD)
    assert with_roster["commissions"] == without["commissions"]
