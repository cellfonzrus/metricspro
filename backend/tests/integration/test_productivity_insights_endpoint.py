"""Integration: GET /commcalc/productivity-insights/{period} through the REAL app, for EVERY tenant.

THE DEFECT THIS REPRODUCES. 2026-10-31, owner, verbatim: *"Productivity Insights / Lefferts ·
104-08 Lefferts Blvd / Accessory $/hr / Boxes/hr / Refresh / A system error occurred. Reference:
15fd7054"*. The reference resolved in `core.failure_log` to a `ModuleNotFoundError` raised INSIDE the
endpoint body — a function-local import naming `app.modules.commcalc.staffing_heatmap`, a package
that has never held that module (it is in `app.modules.storeops`). Every store, every tenant, every
metric: the screen could not load at all.

WHY A ROUTE TEST AND NOT ONLY THE IMPORT LOCK. `tests/unit/test_internal_imports_resolve.py` is the
fix for the CLASS and it would have caught this one statically. It would not have caught an endpoint
that resolves its imports and still 500s, and this endpoint has four cross-module reads and a hand-
rolled period parser. So the class gets the lock, and the reported surface gets a test that actually
presses Refresh: real routing, real middleware, the real endpoint body, over the in-memory database
double with conftest blocking every outbound socket (docs/TESTING.md — there is no staging Supabase).

MULTI-TENANT (project rule 2026-10-30). Every assertion runs for THREE orgs. Each owns a different
store, staffs a different employee and trades in a different part of the day, so a cross-tenant leak
surfaces as the WRONG NAME in a tenant's rep list or the WRONG peak hour in its busy-hours row —
not merely as a total that is hard to argue about. A report that works for one tenant is not a
feature.
"""
import pytest

PATH = "/api/v1/commcalc/productivity-insights/{period}"
TEMPLATE = "/api/v1/commcalc/productivity-insights/{period}"
PERIOD = "October 2026"
HOUSE = "00000000-0000-0000-0000-000000000001"
ORG_B = "00000000-0000-0000-0000-0000000000b2"
ORG_C = "00000000-0000-0000-0000-0000000000b3"
TENANTS = [HOUSE, ORG_B, ORG_C]

#: One store per tenant. The house store is the owner's reported one, by code and street address.
OWN_STORE = {HOUSE: "B-1598", ORG_B: "B-2400", ORG_C: "B-3700"}
OWN_ADDRESS = {HOUSE: "104-08 Lefferts Blvd", ORG_B: "77 Market Ave", ORG_C: "9 Harbor Rd"}
#: One scheduled employee per tenant, spelled so a leak is unmistakable.
OWN_REP = {HOUSE: "Khan, Abid", ORG_B: "Perez, Dalia", ORG_C: "Novak, Imre"}
#: Each tenant's shift, and the hour its sales land in — deliberately disjoint across tenants.
OWN_SHIFT = {HOUSE: ("10:00", "14:00"), ORG_B: ("15:00", "19:00"), ORG_C: ("20:00", "23:00")}
OWN_TRADE_HOUR = {HOUSE: 11, ORG_B: 16, ORG_C: 21}

#: Every field `frontend/src/app/(platform)/commcalc/productivity-insights/page.tsx` dereferences on
#: the response. Pinned here so a rename on the Python side fails a test instead of silently
#: blanking the screen the owner was looking at.
TOP_FIELDS = ("store_code", "period", "metric", "has_demand", "peak_basis", "timezone",
              "peak_hours", "reps", "recommendations")
#: `productivity_fit.analyze` reshapes each rep row, so these are the fields that SURVIVE it — and
#: they are exactly the six columns the table renders. `acc_per_hr` / `boxes_per_hr` are gathered
#: inside the endpoint and deliberately not served: the page plots the one metric it asked for.
REP_FIELDS = ("name", "output_per_hour", "hours", "peak_hours", "peak_share", "output_rank")


def _shift(org, day):
    start, end = OWN_SHIFT[org]
    return {"org_id": org, "store_code": OWN_STORE[org], "employee_name": OWN_REP[org],
            "shift_date": day, "start_time": start, "end_time": end, "is_deleted": False}


def _sale(org, trans_id, day):
    """A sales line with a store-local timestamp, the shape `_tx` reads for demand-by-hour."""
    return {"org_id": org, "period": PERIOD, "trans_id": trans_id, "id": org[-2:] + trans_id,
            "store": OWN_ADDRESS[org], "salesperson": OWN_REP[org], "trans_date": day,
            "trans_ts": f"{day}T{OWN_TRADE_HOUR[org]:02d}:30:00-04:00",
            "contract_type": "New", "department": "Accessories", "category": "Cases",
            "product_desc": "Clear case", "ext_price": 39.99, "voided": None, "trans_type": "Sale",
            "serial_1": "S" + trans_id, "mdn": "2155550143", "tender_type": "Cash"}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    """The three tenants, each with its own store, roster, shift and timestamped sale."""
    from app.modules.commcalc import router as commcalc_router
    from app.modules.storeops import router as storeops_router

    # Both routers reach the database through their own module-level `get_supabase`.
    monkeypatch.setattr("app.modules.commcalc.router.get_supabase", lambda: fake_supabase,
                        raising=False)
    monkeypatch.setattr("app.modules.storeops.router.get_supabase", lambda: fake_supabase,
                        raising=False)
    # Process-wide caches that would otherwise serve the FIRST tenant's answer to the next test.
    for module in (commcalc_router, storeops_router):
        for name in ("_ACFG_CACHE", "_CFG_CACHE", "_STORE_TZ_CACHE", "_BIZ_TZ_CACHE"):
            cache = getattr(module, name, None)
            if isinstance(cache, dict):
                cache.clear()

    fake_supabase.seed("storeops.stores", [
        {"org_id": org, "store_code": OWN_STORE[org], "address": OWN_ADDRESS[org],
         "market": "Queens", "is_active": True, "timezone": "America/New_York"}
        for org in TENANTS])
    fake_supabase.seed("commcalc.store_mapping", [
        {"org_id": org, "store_code": OWN_STORE[org], "store_address": OWN_ADDRESS[org],
         "market": "Queens"} for org in TENANTS])
    fake_supabase.seed("storeops.shifts",
                       [_shift(org, day) for org in TENANTS
                        for day in ("2026-10-05", "2026-10-06", "2026-10-07")])
    fake_supabase.seed("commcalc.daily_sales_feed",
                       [_sale(org, f"T-{i}", "2026-10-05") for org in TENANTS
                        for i in (1, 2, 3)])
    return fake_supabase


def test_the_route_is_registered_at_its_documented_path(app_module):
    from app.core.path_redact import route_templates
    assert TEMPLATE in set(route_templates(app_module))


@pytest.mark.parametrize("org", TENANTS, ids=[o[-2:] for o in TENANTS])
@pytest.mark.parametrize("metric", ["acc", "boxes"])
def test_refresh_does_not_500_for_any_tenant_or_metric(wired, api_client, org, metric):
    """THE REGRESSION. This is the owner's Refresh, for both toggles, for all three tenants.

    Before the fix every one of these six requests raised ModuleNotFoundError and the page printed
    a system-error reference. A 500 here means the endpoint body cannot run at all — never a data
    condition, because this endpoint is reached with rows seeded for the store it is asked about.
    """
    r = api_client.get(TEMPLATE.format(period=PERIOD),
                       params={"store_code": OWN_STORE[org], "metric": metric, "org_id": org})
    assert r.status_code == 200, r.text
    body = r.json()
    for field in TOP_FIELDS:
        assert field in body, f"the page reads `{field}` and the endpoint no longer serves it"
    assert body["store_code"] == OWN_STORE[org]
    assert body["metric"] == ("boxes/hr" if metric == "boxes" else "accessory $/hr")
    for rep in body["reps"]:
        for field in REP_FIELDS:
            assert field in rep, f"rep column `{field}` is a column on the screen"


@pytest.mark.parametrize("org", TENANTS, ids=[o[-2:] for o in TENANTS])
def test_the_period_parser_accepts_both_spellings_the_app_sends(wired, api_client, org):
    """`usePeriod()` sends "October 2026"; other surfaces send "2026-10". Both must resolve to the
    same month, because the shift window is built from them and an unparsed period is a 400."""
    long_form = api_client.get(TEMPLATE.format(period=PERIOD),
                               params={"store_code": OWN_STORE[org], "org_id": org})
    iso_form = api_client.get(TEMPLATE.format(period="2026-10"),
                              params={"store_code": OWN_STORE[org], "org_id": org})
    assert long_form.status_code == 200, long_form.text
    assert iso_form.status_code == 200, iso_form.text
    assert long_form.json()["reps"] == iso_form.json()["reps"]


def test_a_period_it_cannot_read_is_refused_and_not_guessed(wired, api_client):
    r = api_client.get(TEMPLATE.format(period="last-month"),
                       params={"store_code": OWN_STORE[HOUSE], "org_id": HOUSE})
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("org", TENANTS, ids=[o[-2:] for o in TENANTS])
def test_each_tenants_scheduled_rep_appears_and_nobody_elses_does(wired, api_client, org):
    """ORG SCOPING, read off the report rather than asserted about the query.

    Each tenant staffs one person nobody else staffs, so a leak shows up as another tenant's NAME
    in this tenant's rep list — the failure a wrong total would have let an author argue with.
    """
    body = api_client.get(TEMPLATE.format(period=PERIOD),
                          params={"store_code": OWN_STORE[org], "org_id": org}).json()
    names = {str(r["name"]).strip().upper() for r in body["reps"]}
    assert OWN_REP[org].upper() in names, f"{org} staffs {OWN_REP[org]} and the report omits them"
    for other in TENANTS:
        if other != org:
            assert OWN_REP[other].upper() not in names, (
                f"{other}'s employee {OWN_REP[other]} is visible in {org}'s report")


@pytest.mark.parametrize("org", TENANTS, ids=[o[-2:] for o in TENANTS])
def test_busy_hours_come_from_this_tenants_own_timestamped_sales(wired, api_client, org):
    """Each tenant trades in a different hour. The peak must be that tenant's hour and no other's —
    and `has_demand` must say the basis was transactions, not the scheduled-heads proxy."""
    body = api_client.get(TEMPLATE.format(period=PERIOD),
                          params={"store_code": OWN_STORE[org], "org_id": org}).json()
    assert body["has_demand"] is True, (
        "three timestamped sales were seeded for this store; the report fell back to the proxy")
    assert body["peak_basis"] == "transactions"
    assert OWN_TRADE_HOUR[org] in body["peak_hours"]
    for other in TENANTS:
        if other != org:
            assert OWN_TRADE_HOUR[other] not in body["peak_hours"], (
                f"{other}'s trading hour shows as {org}'s peak")
