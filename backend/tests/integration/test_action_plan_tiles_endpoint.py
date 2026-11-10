"""Integration: GET /commcalc/targets/{period}/action-plan through the REAL app, for EVERY tenant.

WHY THIS TEST EXISTS. The Daily Action Plan was redesigned into tiles on 2026-11-10 (owner ask:
*"Make it tiled with focused metrics … the tiles should be switched between kpi / stores / sales
reps"*). The tile fold is pure and client-side (`frontend/src/lib/action-plan-tiles.ts`, unit-proved
beside it) and it dereferences the fields THIS endpoint serves. So the contract between them needs a
test that actually presses the button: real routing, real middleware, the real endpoint body, over
the in-memory database double with conftest blocking every outbound socket (docs/TESTING.md — there
is no staging Supabase).

WHAT IT PINS, and why each one would otherwise blank a screen silently:

  · THE FIELD NAMES THE TILES READ. A rename on the Python side must fail a test rather than render
    an empty tile row. Both grains are pinned: a store's declared categories and a rep's KPI cells.
  · NOT MEASURED IS SERVED AS SUCH. A KPI with a target and no recorded value arrives in
    `commission.kpis_no_data` and NOT in `kpis` — `kpi_failing.evaluate`'s own honesty. The tile for
    it says "not measured"; if the endpoint ever folded it in as a 0 instead, the tile would read as
    a clean sheet. This is live: the DLAR gross adds / activations / upgrades columns stopped
    arriving with the Elevate Go renames.
  · MULTI-TENANT (project rule 2026-10-30). Every assertion runs for THREE orgs, each with its OWN
    declared KPI vocabulary, its own store, its own rep and its own target. A leak therefore surfaces
    as the WRONG TENANT'S WORDS in a tenant's payload, not as a total that is hard to argue about.
"""
import pytest

TEMPLATE = "/api/v1/commcalc/targets/{period}/action-plan"
PERIOD = "October 2026"
HOUSE = "00000000-0000-0000-0000-000000000001"
ORG_B = "00000000-0000-0000-0000-0000000000b2"
ORG_C = "00000000-0000-0000-0000-0000000000b3"
TENANTS = [HOUSE, ORG_B, ORG_C]
NIL_CARRIER = "00000000-0000-0000-0000-000000000000"
AS_OF = "2026-10-20"

#: One store per tenant, by code and street address.
OWN_STORE = {HOUSE: "B-1598", ORG_B: "B-2400", ORG_C: "B-3700"}
OWN_ADDRESS = {HOUSE: "104-08 Lefferts Blvd", ORG_B: "77 Market Ave", ORG_C: "9 Harbor Rd"}
OWN_MARKET = {HOUSE: "Queens", ORG_B: "Nassau", ORG_C: "Suffolk"}
#: One scheduled rep per tenant, spelled so a leak is unmistakable.
OWN_REP = {HOUSE: "Khan, Abid", ORG_B: "Perez, Dalia", ORG_C: "Novak, Imre"}

#: EACH TENANT'S OWN DECLARED KPI VOCABULARY (`commcalc.carrier_kpi_metric`, mig 060) — the words the
#: metric picker offers. The first of each pair is FED a value; the second is declared with a target
#: and nothing feeds it, which is the not-measured case.
OWN_KPIS = {
    HOUSE: [("familyplan", "Family Plan", 45), ("boostapp", "Carrier App", 65)],
    ORG_B: [("protect", "Shield Plan", 80), ("atu", "Units per Deal", 55)],
    ORG_C: [("tmr3", "Three Month Run", 70), ("aal", "Added Lines", 5)],
}
#: Which target columns each tenant set, and to what. Different per tenant, so the store-grain axis
#: in the response is that tenant's own.
OWN_TARGETS = {
    HOUSE: {"activations_monthly": 100, "accessories_monthly": 5000},
    ORG_B: {"accessories_monthly": 4000},
    ORG_C: {"activations_monthly": 60, "upgrades_monthly": 20},
}

#: Every top-level field the redesigned page dereferences on the response.
TOP_FIELDS = ("period", "today", "summary", "setup_hint", "peer", "team_total", "drilldown", "stores")
#: Every store-row field it reads.
STORE_FIELDS = ("store_code", "address", "market", "conversion", "metrics", "items", "reps",
                "commission_at_risk", "counts")
#: A declared category's cells — the store-grain half of a focus tile.
METRIC_FIELDS = ("cat", "label", "unit", "target", "achieved", "need", "pace", "today_target")
#: A rep's cells, and the commission cells the rep tiles read.
REP_FIELDS = ("rep", "conversion", "below_store", "items", "commission")
COMMISSION_FIELDS = ("tier", "kpis_met", "total_kpis", "at_risk", "short_kpis", "kpis", "kpis_no_data")


def _store_row(org):
    return {"org_id": org, "store_code": OWN_STORE[org], "address": OWN_ADDRESS[org],
            "market": OWN_MARKET[org], "is_active": True, "monthly_target": 0}


def _shift(org, day):
    return {"org_id": org, "store_code": OWN_STORE[org], "employee_name": OWN_REP[org],
            "shift_date": day, "start_time": "10:00", "end_time": "18:00", "is_deleted": False}


def _target_row(org):
    return {"org_id": org, "period": PERIOD, "store_code": OWN_STORE[org], **OWN_TARGETS[org]}


def _kpi_defs_rows(org):
    out = []
    for i, (key, label, target) in enumerate(OWN_KPIS[org]):
        out.append({"org_id": org, "carrier_id": NIL_CARRIER, "metric_key": key, "label": label,
                    "payout_config_col": f"kpi_{key}_target", "target_default": target,
                    "is_active": True, "sort": i})
    return out


def _commission_row(org):
    """The rep's calculated commission. ONLY the first declared KPI carries a value: the second is
    declared, has a target, and nothing fed it — so the endpoint must report it as no-data."""
    fed_key = OWN_KPIS[org][0][0]
    return {"org_id": org, "period": PERIOD, "storeops_name": OWN_REP[org],
            "epay_salesperson": OWN_REP[org], "store_code": OWN_STORE[org],
            "tier": 0.75, "kpis_met": 1, "total_kpis": 2,
            "kpi_values": {fed_key: 10.0},          # below every seeded target → a failing KPI
            "subtotal": 4000.0, "total_payout": 3000.0}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    """Three tenants, each with its own store, rep, shift, target row, KPI vocabulary and commission."""
    from app.modules.commcalc import router as commcalc_router  # noqa: F401 - imported for patching

    for mod in ("app.modules.commcalc.router.get_supabase",
                "app.modules.storeops.router.get_supabase",
                "app.modules.closing.router.get_supabase"):
        monkeypatch.setattr(mod, lambda: fake_supabase, raising=False)
    # `_kpi_defs` reaches the database through the module-level `sb()` helper rather than the
    # endpoint's client, so the KPI vocabulary is per-tenant only if THAT is pointed at the double.
    monkeypatch.setattr("app.modules.commcalc.router.sb", lambda: fake_supabase, raising=False)

    # ALL THREE TENANTS IN ONE TABLE, seeded once: `seed` replaces a relation, and a per-tenant call
    # in a loop would leave only the last org's rows — which would make a leak test pass by accident.
    fake_supabase.seed("storeops.stores", [_store_row(o) for o in TENANTS])
    fake_supabase.seed("storeops.shifts",
                       [_shift(o, f"2026-10-{d:02d}") for o in TENANTS for d in (6, 7, 8)])
    fake_supabase.seed("commcalc.targets", [_target_row(o) for o in TENANTS])
    fake_supabase.seed("commcalc.carrier_kpi_metric",
                       [r for o in TENANTS for r in _kpi_defs_rows(o)])
    fake_supabase.seed("commcalc.rep_commissions", [_commission_row(o) for o in TENANTS])
    return fake_supabase


def _get(api_client, org):
    r = api_client.get(TEMPLATE.format(period=PERIOD), params={"org_id": org, "today": AS_OF})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("org", TENANTS)
def test_the_plan_loads_and_carries_every_field_the_tiles_read(wired, api_client, org):
    body = _get(api_client, org)
    for f in TOP_FIELDS:
        assert f in body, f"{f} missing for {org}"
    assert body["today"] == AS_OF, "the as-of day is the engine's own parameter, not a client filter"
    stores = body["stores"]
    assert stores, f"{org} set a target and staffed a store, so its plan cannot be empty"
    for s in stores:
        for f in STORE_FIELDS:
            assert f in s, f"store field {f} missing for {org}"
        assert s["metrics"], "a store with a target carries its declared categories"
        for m in s["metrics"]:
            for f in METRIC_FIELDS:
                assert f in m, f"metric field {f} missing for {org}"


@pytest.mark.parametrize("org", TENANTS)
def test_each_tenant_gets_its_own_store_rep_and_target_axis(wired, api_client, org):
    body = _get(api_client, org)
    s = body["stores"][0]
    assert s["store_code"] == OWN_STORE[org]
    assert s["address"] == OWN_ADDRESS[org]
    assert s["market"] == OWN_MARKET[org]
    # THE STORE-GRAIN AXIS IS THIS TENANT'S: exactly the categories it set a target on.
    got = {m["cat"] for m in s["metrics"]}
    want = {c.replace("_monthly", "") for c in OWN_TARGETS[org]}
    assert got == want, f"{org} axis {got} != declared {want}"
    assert [r["rep"] for r in s["reps"]] == [OWN_REP[org]]


@pytest.mark.parametrize("org", TENANTS)
def test_no_other_tenants_words_reach_this_tenants_plan(wired, api_client, org):
    body = _get(api_client, org)
    blob = str(body)
    for other in TENANTS:
        if other == org:
            continue
        for leak in (OWN_STORE[other], OWN_ADDRESS[other], OWN_REP[other], OWN_MARKET[other],
                     OWN_KPIS[other][0][1], OWN_KPIS[other][1][1]):
            assert leak not in blob, f"{other}'s {leak!r} reached {org}'s plan"


@pytest.mark.parametrize("org", TENANTS)
def test_a_rep_kpi_carries_this_tenants_label_and_a_failing_one_is_named(wired, api_client, org):
    body = _get(api_client, org)
    rep = body["stores"][0]["reps"][0]
    comm = rep["commission"]
    assert comm, "the commission row was seeded for this period, so it must reach the plan"
    for f in COMMISSION_FIELDS:
        assert f in comm, f"commission field {f} missing for {org}"
    fed_key, fed_label, fed_target = OWN_KPIS[org][0]
    scored = {k["kpi"]: k for k in comm["kpis"]}
    assert fed_key in scored, f"{org}'s fed KPI is missing from the score"
    k = scored[fed_key]
    assert k["label"] == fed_label, "the KPI wears the label THIS tenant declared"
    assert k["target"] == fed_target
    assert k["met"] is False and k["actual"] == 10.0


@pytest.mark.parametrize("org", TENANTS)
def test_a_kpi_nothing_fed_is_reported_as_no_data_and_never_as_a_zero(wired, api_client, org):
    """THE HONESTY THE TILES DEPEND ON. A declared KPI with a target and no recorded value must
    arrive in `kpis_no_data`, out of `kpis`, and must not be counted as failing — otherwise a tile
    over an empty feed reads either as a clean sheet or as the rep's fault."""
    body = _get(api_client, org)
    comm = body["stores"][0]["reps"][0]["commission"]
    unfed_key, unfed_label, unfed_target = OWN_KPIS[org][1]
    assert unfed_key not in {k["kpi"] for k in comm["kpis"]}, "an unfed KPI is not a scored KPI"
    nd = {k["kpi"]: k for k in comm["kpis_no_data"]}
    assert unfed_key in nd, f"{org}'s unfed KPI must be reported, not omitted"
    assert nd[unfed_key]["label"] == unfed_label
    assert nd[unfed_key]["target"] == unfed_target
    assert "actual" not in nd[unfed_key], "there is no value to report — not even a 0"
    assert unfed_label not in comm["short_kpis"], "unfed is not short"


@pytest.mark.parametrize("org", TENANTS)
def test_the_team_roll_up_is_folded_on_the_server_for_the_page_to_read(wired, api_client, org):
    """The page must never re-sum this (two folds of one figure are two numbers hoping to agree), so
    the fold has to arrive keyed by the tenant's own categories."""
    body = _get(api_client, org)
    cats = (body.get("team_total") or {}).get("categories") or {}
    want = {c.replace("_monthly", "") for c in OWN_TARGETS[org]}
    assert set(cats) >= want, f"{org} roll-up {set(cats)} does not cover {want}"
    for cat in want:
        for f in ("monthly", "achieved_mtd", "need"):
            assert f in cats[cat], f"{cat}.{f} missing for {org}"
