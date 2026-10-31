"""Integration: saving a commission plan restates the months that plan paid (index §COMMCALC-41.9).

Owner, 2026-10-31: *"i upldated the commission for luxelink in ny plan but it did not get auto
updated as we confirmed yesterday that everytime anything is change it will auto update acorss the
board, i changed teh comission from june - oct"*.

WHAT WAS WRONG. The platform already holds the rule — *a build's DECLARATION is one of its inputs*
(§COMMCALC-41.8, shipped 2026-10-29 for the carrier's net payment terms). It had been applied to one
build. The `commission` build, the oldest and the one that moves rep pay, declared its feeds and no
declaration at all, so saving a plan queued nothing and the figures waited for an unrelated upload.
`_write_installment_schedule`'s docstring said so out loud: *"Does NOT recompute pay (that waits for
POST /calculate)"*.

TWO THINGS ARE PROVED HERE, through the real app and the real endpoint:
  · the save queues a recomputation of every month that HAS stored figures — not just this month;
  · it is BOUNDED, so a rate corrected today does not rewrite a month from two years ago. Cellfonz
    R Us really does hold a `July 2024` row beside its 2026 months (measured 2026-10-31).

EVERY TENANT, not the house org (project directive 2026-10-30): the parametrised org ids below are
the three live tenants, and each is measured against its own stored months.
"""
from datetime import datetime, timezone

import pytest

HOUSE = "00000000-0000-0000-0000-000000000001"
LUXELINK = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
VZONE = "f4f1c16e-2acf-4221-854a-c29a605754a7"
PATH = "/api/v1/commcalc/commission-plans"

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")


def _month_label(back):
    """`back` months before today, in the ONE stored spelling. Relative, so the suite does not rot."""
    now = datetime.now(timezone.utc)
    idx = now.year * 12 + now.month - 1 - back
    return f"{_MONTHS[idx % 12]} {idx // 12}"


RECENT = [_month_label(n) for n in (4, 3, 2, 1, 0)]          # five months up to this one
ANCIENT = _month_label(27)                                   # comfortably outside any horizon


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    from app.modules.commcalc import auto_calc, router as commcalc_router
    monkeypatch.setattr("app.modules.commcalc.router.sb", lambda: fake_supabase)
    for name in ("_ACFG_CACHE", "_CFG_CACHE"):
        cache = getattr(commcalc_router, name, None)
        if isinstance(cache, dict):
            cache.clear()
    # the hook's own in-process queue, so one test cannot see another's requests
    with auto_calc._MEM_LOCK:
        auto_calc._MEM.clear()
    auto_calc._STATE.update(ready=None, checked=0.0)
    return fake_supabase


PLAN_ID = "8f35ce16-cb1c-44a7-a672-be5ccce69494"      # LuxeLink's real "NY / Luxelink Comp" plan id


def _edit(name="NY / Luxelink Comp", plan_id=PLAN_ID):
    """The body the plan editor sends when somebody CHANGES an existing plan — the owner's own case."""
    return {"id": plan_id, "name": name, "rules": [], "tiers": [], "assignments": []}


def _seed_tenant(fake, org_id, months):
    fake.seed("commcalc.commission_org_config",
              (fake.rows("commcalc.commission_org_config") or [])
              + [{"org_id": org_id, "auto_calc_on_landing": True, "auto_calc_debounce_minutes": 5}])
    fake.seed("commcalc.commission_plan",
              (fake.rows("commcalc.commission_plan") or [])
              + [{"id": PLAN_ID if org_id == LUXELINK else f"plan-{org_id}", "org_id": org_id,
                  "name": "NY / Luxelink Comp", "is_active": True}])
    fake.seed("commcalc.rep_commissions",
              (fake.rows("commcalc.rep_commissions") or [])
              + [{"id": f"{org_id}-{i}", "org_id": org_id, "period": p, "rep_name": "A"}
                 for i, p in enumerate(months)])


@pytest.mark.parametrize("org_id", [HOUSE, LUXELINK, VZONE])
def test_saving_a_plan_queues_every_stored_month_for_every_tenant(wired, api_client, org_id):
    _seed_tenant(wired, org_id, RECENT)
    r = api_client.post(PATH, params={"org_id": org_id},
                        json=_edit(plan_id=PLAN_ID if org_id == LUXELINK else f"plan-{org_id}"))
    assert r.status_code == 200, r.text
    auto = r.json().get("auto_calc") or {}
    assert auto.get("queued") is True, auto
    assert sorted(auto.get("periods") or []) == sorted(RECENT), auto


def test_one_tenants_plan_edit_never_queues_another_tenants_months(wired, api_client):
    """A tenant-scoped write is where cross-tenant leakage lives (project directive 2026-10-30)."""
    _seed_tenant(wired, LUXELINK, RECENT[-2:])
    _seed_tenant(wired, HOUSE, RECENT)
    r = api_client.post(PATH, params={"org_id": LUXELINK},
                        json=_edit())
    auto = r.json().get("auto_calc") or {}
    assert sorted(auto.get("periods") or []) == sorted(RECENT[-2:]), auto


def test_the_restatement_is_bounded_and_leaves_an_ancient_month_alone(wired, api_client):
    _seed_tenant(wired, HOUSE, list(RECENT) + [ANCIENT])
    r = api_client.post(PATH, params={"org_id": HOUSE},
                        json=_edit("Total Comp DM", plan_id=f"plan-{HOUSE}"))
    auto = r.json().get("auto_calc") or {}
    assert ANCIENT not in (auto.get("periods") or []), auto
    assert sorted(auto.get("periods") or []) == sorted(RECENT), auto


def test_a_tenant_with_no_stored_month_yet_queues_nothing_and_says_so(wired, api_client):
    """Nothing stored means nothing to restate — stated, never a silent success."""
    _seed_tenant(wired, VZONE, [])
    r = api_client.post(PATH, params={"org_id": VZONE},
                        json=_edit("New plan", plan_id=f"plan-{VZONE}"))
    assert r.status_code == 200, r.text
    auto = r.json().get("auto_calc") or {}
    assert auto.get("queued") is False
    assert auto.get("reason") == "no_period", auto


def test_a_tenant_that_has_turned_auto_calc_off_is_respected(wired, api_client):
    """RULE TWO: the switch is the tenant's config row, and an edit must not override it."""
    _seed_tenant(wired, HOUSE, RECENT)
    wired.seed("commcalc.commission_org_config",
               [{"org_id": HOUSE, "auto_calc_on_landing": False, "auto_calc_debounce_minutes": 5}])
    r = api_client.post(PATH, params={"org_id": HOUSE},
                        json=_edit("Total Comp DM", plan_id=f"plan-{HOUSE}"))
    auto = r.json().get("auto_calc") or {}
    assert auto.get("queued") is False
    assert auto.get("reason") == "off", auto


def test_removing_a_plan_restates_too(wired, api_client):
    """Deleting a plan changes what the stored months were paid by, exactly as editing one does."""
    _seed_tenant(wired, LUXELINK, RECENT)
    r = api_client.delete(f"{PATH}/{PLAN_ID}", params={"org_id": LUXELINK})
    assert r.status_code == 200, r.text
    auto = r.json().get("auto_calc") or {}
    assert auto.get("queued") is True, auto
    assert sorted(auto.get("periods") or []) == sorted(RECENT), auto
