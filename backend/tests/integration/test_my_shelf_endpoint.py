"""Integration: GET /commcalc/my-shelf through the REAL app, for EVERY tenant.

THE SIBLING DEFECT. Nobody reported this one. It was found on 2026-10-31 by the sweep written for
the Productivity Insights crash (`tests/unit/test_internal_imports_resolve.py`), which reads every
`app.*` import in the backend statically. `my_shelf` opened with:

    from app.modules.commcalc import unsold_inventory as _unsold

and `unsold_inventory` is in `app.modules.payables` — as its eight other callers all say. The
statement sits inside the endpoint body, so the app booted clean and the defect waited for the first
rep to open their shelf, where it would have raised the same `ModuleNotFoundError` and printed the
same "A system error occurred" reference the owner saw on the other page.

Owner directive 2026-09-20, verbatim: *"if one thing is fixed for one tenant it should be a design
fix not a temporary fix"*. The class here is "a function-local import of our own code is checked by
nothing", the lock is the import sweep, and this file is the half of the rule that says the siblings
get fixed in the SAME PR rather than left for the next report.

MULTI-TENANT (project rule 2026-10-30). Three orgs, each with its own snapshot, its own store and
its own declared selling window. A device belonging to one tenant appearing on another's shelf is
the leak this asserts against directly, by IMEI.
"""
import pytest

PATH = "/api/v1/commcalc/my-shelf"
HOUSE = "00000000-0000-0000-0000-000000000001"
ORG_B = "00000000-0000-0000-0000-0000000000c2"
ORG_C = "00000000-0000-0000-0000-0000000000c3"
TENANTS = [HOUSE, ORG_B, ORG_C]

OWN_STORE = {HOUSE: "B-1598", ORG_B: "B-2400", ORG_C: "B-3700"}
OWN_IMEI = {HOUSE: "350000000000001", ORG_B: "350000000000002", ORG_C: "350000000000003"}
#: Each tenant declares its OWN selling window, so a tenant served the house number is visible as a
#: wrong `windows_declared` rather than only as a wrong deadline on one row.
OWN_WINDOW = {HOUSE: 60, ORG_B: 10, ORG_C: 30}
SNAPSHOT = "2026-10-30"

#: Every field the shelf card reads off the payload.
TOP_FIELDS = ("as_of", "snapshot", "devices", "stores", "summary", "windows_declared",
              "excluded_non_devices", "excluded_models", "span_restricted",
              "devices_out_of_span", "setup_hint", "received_basis", "source")


def _device(org):
    return {"org_id": org, "imei": OWN_IMEI[org], "serial": OWN_IMEI[org], "sku": "MOTO-G-PLAY",
            "item": "moto g play 2026", "store": OWN_STORE[org], "unit_cost": 59.0,
            "received_date": "2026-10-01", "days_in_stock": 29, "as_of_date": SNAPSHOT,
            "on_hand": 1, "status": "In Stock"}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    from app.modules.commcalc import router as commcalc_router
    monkeypatch.setattr("app.modules.commcalc.router.get_supabase", lambda: fake_supabase,
                        raising=False)
    monkeypatch.setattr("app.modules.storeops.router.get_supabase", lambda: fake_supabase,
                        raising=False)
    for name in ("_ACFG_CACHE", "_CFG_CACHE"):
        cache = getattr(commcalc_router, name, None)
        if isinstance(cache, dict):
            cache.clear()

    fake_supabase.seed("storeops.stores", [
        {"org_id": org, "store_code": OWN_STORE[org], "address": f"{OWN_STORE[org]} Main St",
         "market": "Queens", "is_active": True} for org in TENANTS])
    fake_supabase.seed("commcalc.store_mapping", [
        {"org_id": org, "store_code": OWN_STORE[org], "store_address": f"{OWN_STORE[org]} Main St",
         "market": "Queens"} for org in TENANTS])
    fake_supabase.seed("commcalc.inventory_aging_device", [_device(org) for org in TENANTS])
    # The declared selling window, through the payables source map the shared loader reads.
    fake_supabase.seed("commcalc.payable_source_map", [
        {"org_id": org, "carrier_id": None, "is_active": True,
         "sell_within_days": OWN_WINDOW[org]}
        for org in TENANTS])
    return fake_supabase


def test_the_route_is_registered_at_its_documented_path(app_module):
    from app.core.path_redact import route_templates
    assert PATH in set(route_templates(app_module))


@pytest.mark.parametrize("org", TENANTS, ids=[o[-2:] for o in TENANTS])
def test_opening_the_shelf_does_not_500_for_any_tenant(wired, api_client, org):
    """THE REGRESSION for the sibling import defect. Before the fix this raised
    ModuleNotFoundError at the first line of the endpoint body, for every tenant and every rep."""
    r = api_client.get(PATH, params={"org_id": org})
    assert r.status_code == 200, r.text
    body = r.json()
    for field in TOP_FIELDS:
        assert field in body, f"the shelf card reads `{field}` and the endpoint no longer serves it"


@pytest.mark.parametrize("org", TENANTS, ids=[o[-2:] for o in TENANTS])
def test_each_tenant_sees_its_own_device_and_no_other_tenants(wired, api_client, org):
    body = api_client.get(PATH, params={"org_id": org}).json()
    imeis = {str(d.get("imei") or d.get("serial") or "") for d in body["devices"]}
    assert OWN_IMEI[org] in imeis, f"{org}'s own on-hand device is missing from its shelf"
    for other in TENANTS:
        if other != org:
            assert OWN_IMEI[other] not in imeis, f"{other}'s device is on {org}'s shelf"


@pytest.mark.parametrize("org", TENANTS, ids=[o[-2:] for o in TENANTS])
def test_the_window_each_tenant_declared_is_the_one_served(wired, api_client, org):
    """RULE TWO. The deadline is a declared per-org number; if every tenant got the house 60 the
    card would quietly hold three tenants to one tenant's policy."""
    body = api_client.get(PATH, params={"org_id": org}).json()
    assert body["windows_declared"] == [OWN_WINDOW[org]]
