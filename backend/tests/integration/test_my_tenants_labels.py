"""Integration: GET /core/my-tenants labels every company a human can read (index §POS-02).

Real routing, the real endpoint body, over the in-memory database double — conftest blocks every
outbound socket, so this cannot reach the live Supabase (docs/TESTING.md).

WHAT THIS COVERS THAT THE UNIT TEST CANNOT: that the picker's actual payload dereferences the one
home rather than re-deriving a label of its own. That is the defect — `_my_tenants_payload` had its
own `storeops.tenants` read and its own `"Tenant"` stand-in, and the app at the end of that chain
fell through to the org id. A unit test of the one home proves the fact; only this proves the wire.
"""
import pytest

from app.modules.core import tenant_identity as ti

HOUSE = "00000000-0000-0000-0000-000000000001"
NAMED = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
SLUGGED = "ba084e25-adcf-4d4d-9cc5-d3728775513f"
BARE = "f4f1c16e-2acf-4221-854a-c29a605754a7"

UID = "auth-multi-company"
PATH = "/api/v1/core/my-tenants"


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    """A login belonging to four companies: one named, one named-and-slugged, one slug-only, and
    one with NO tenants row at all — the four shapes the live picker has to render."""
    fake_supabase.seed("storeops.app_users", [
        {"auth_id": UID, "org_id": HOUSE, "role": "admin", "is_active": True, "is_default_org": True},
        {"auth_id": UID, "org_id": NAMED, "role": "admin", "is_active": True},
        {"auth_id": UID, "org_id": SLUGGED, "role": "admin", "is_active": True},
        {"auth_id": UID, "org_id": BARE, "role": "admin", "is_active": True},
    ])
    fake_supabase.seed("storeops.tenants", [
        {"org_id": HOUSE, "name": "House Retail Co", "slug": "house-retail-co"},
        {"org_id": NAMED, "name": "Second Retail LLC", "slug": "second-retail-llc"},
        {"org_id": SLUGGED, "name": "", "slug": "third-logistics"},
    ])
    fake_supabase.seed("storeops.roles", [])
    monkeypatch.setattr("app.modules.core.router.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("app.modules.core.router._uid_from_token", lambda _auth: UID)
    return fake_supabase


def _labels(api_client):
    res = api_client.get(PATH, headers={"Authorization": "Bearer t"})
    assert res.status_code == 200, res.text
    return {t["org_id"]: t["name"] for t in res.json()["tenants"]}


def test_the_route_is_registered_where_the_app_says_it_is(app_module):
    from app.core.path_redact import route_templates
    assert PATH in set(route_templates(app_module))


def test_every_company_comes_back_with_the_name_it_was_onboarded_under(wired, api_client):
    got = _labels(api_client)
    assert got[HOUSE] == "House Retail Co"
    assert got[NAMED] == "Second Retail LLC"


def test_a_company_with_no_name_reads_as_its_slug_not_as_a_stand_in(wired, api_client):
    assert _labels(api_client)[SLUGGED] == "Third Logistics"


def test_a_company_with_no_tenants_row_is_still_pickable(wired, api_client):
    assert _labels(api_client)[BARE] == ti.GENERIC_LABEL


def test_no_company_is_ever_labelled_with_its_id(wired, api_client):
    """THE REGRESSION. The screen the owner photographed on 2026-10-30 was four of these."""
    got = _labels(api_client)
    assert len(got) == 4
    for org_id, label in got.items():
        assert label, f"{org_id} has no label at all"
        assert not ti.looks_like_uuid(label), f"{org_id} is labelled with an id: {label!r}"
        assert org_id not in label


def test_the_payload_label_is_the_one_home_s_answer_not_a_second_derivation(wired, api_client):
    """Byte-for-byte the same string the one home returns for the same row — so the picker, the
    switcher and any future asker cannot drift apart again."""
    got = _labels(api_client)
    expected = ti.picker_labels(wired, [HOUSE, NAMED, SLUGGED, BARE])
    assert got == expected


def test_bootstrap_carries_the_same_labels_as_the_picker(wired, api_client):
    """`/core/bootstrap` serves the same list to save round trips; two payloads of one fact must
    agree or the login screen and the switcher disagree about what a company is called."""
    res = api_client.get("/api/v1/core/bootstrap", headers={"Authorization": "Bearer t"})
    assert res.status_code == 200, res.text
    boot = {t["org_id"]: t["name"] for t in res.json()["tenants"]["tenants"]}
    assert boot == _labels(api_client)
