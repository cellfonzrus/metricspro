"""Integration: GET /commcalc/zero-sales through the REAL app, for EVERY tenant (index §CORE-26).

Real routing, real middleware, the real endpoint body — over the in-memory database double, with
conftest blocking every outbound socket. There is no staging Supabase, so "integration" here means
the app, not a real database (docs/TESTING.md).

WHY THIS TEST EXISTS FOR A TOOLBAR CHANGE. The owner's ask was the send bar, and what a send mails
is `frontend/src/lib/zero-sales-export.zeroSalesSheets(payload)` — a converter that reads this
endpoint's payload field by field. A unit test proves the converter over a payload a human typed;
only the app proves the payload is really shaped that way. If the endpoint renames `day_counts` or
starts returning 0 where it used to return null, the screen keeps working and the MAILED FILE starts
printing a counted zero for a day nobody measured. That is the exact lie the report was built to
avoid, so the contract is pinned here.

MULTI-TENANT (project rule 2026-10-30): every assertion is made for THREE orgs, each with its own
config row, and each org's figures are checked to be its own. A report that works for one tenant is
not a feature.
"""
import pytest

PATH = "/api/v1/commcalc/zero-sales"
HOUSE = "00000000-0000-0000-0000-000000000001"
# Three tenants, deliberately differing in the two settings that change what a run MEANS: the
# threshold, and whether a day nobody measured bridges a run or ends it.
TENANTS = [
    (HOUSE, {"consecutive_days": 3, "gap_policy": "break"}),
    ("00000000-0000-0000-0000-0000000000a2", {"consecutive_days": 2, "gap_policy": "bridge"}),
    ("00000000-0000-0000-0000-0000000000a3", {"consecutive_days": 5, "gap_policy": "break"}),
]
FROM, TO = "2026-10-01", "2026-10-03"
#: Each tenant owns one store and uploaded one day — a different store and a different day each, so
#: a cross-tenant leak shows up as the WRONG STORE CODE rather than only as a wrong total.
OWN_STORE = {HOUSE: "B-1115", TENANTS[1][0]: "B-2000", TENANTS[2][0]: "B-3000"}
OWN_DAY = {HOUSE: FROM, TENANTS[1][0]: "2026-10-02", TENANTS[2][0]: TO}

#: Every field `zeroSalesSheets` / `zeroSalesTiles` dereference. Named here so a rename on either
#: side of the language boundary fails a test instead of silently emptying a mailed column.
ROW_FIELDS = ("grain", "scope_key", "label", "parent", "state", "day_states", "day_counts",
              "zero_days", "days_measured_zero", "days_not_reported", "trading_calendar",
              "alerting", "note")
TOTAL_FIELDS = ("stores_alerting", "store_days_measured_zero", "store_days_not_reported",
                "stores_unknown_calendar")
TOP_FIELDS = ("days", "rows", "totals", "config", "rules_refused", "market_of",
              "date_from", "date_to")


def _line(org, trans_id, day, *, store="B-1115", rep="Arora, Tanish", contract="New"):
    return {"org_id": org, "period": "October 2026", "trans_id": trans_id, "id": org[-2:] + trans_id,
            "store": store, "salesperson": rep, "trans_date": day, "contract_type": contract,
            "department": "Handsets", "category": "Phones", "product_desc": "moto g play",
            "ext_price": 49.99, "voided": None, "trans_type": "Sale", "serial_1": "S" + trans_id,
            "mdn": "2155550143", "tender_type": "Cash"}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    from app.modules.commcalc import router as commcalc_router
    monkeypatch.setattr("app.modules.commcalc.router.get_supabase", lambda: fake_supabase,
                        raising=False)
    for name in ("_ACFG_CACHE", "_CFG_CACHE"):
        cache = getattr(commcalc_router, name, None)
        if isinstance(cache, dict):
            cache.clear()
    fake_supabase.seed("commcalc.zero_sales_config",
                       [{"org_id": org, **cfg} for org, cfg in TENANTS])
    fake_supabase.seed("commcalc.store_mapping",
                       [{"org_id": org, "store_code": OWN_STORE[org],
                         "store_address": f"{OWN_STORE[org]} Main St"} for org, _ in TENANTS])
    fake_supabase.seed("commcalc.daily_sales_feed",
                       [_line(org, "T-1", OWN_DAY[org], store=OWN_STORE[org])
                        for org, _ in TENANTS])
    return fake_supabase


def test_the_route_is_registered_at_its_documented_path(app_module):
    from app.core.path_redact import route_templates
    assert PATH in set(route_templates(app_module))


@pytest.mark.parametrize("org,cfg", TENANTS, ids=[t[0][-2:] for t in TENANTS])
def test_the_payload_carries_every_field_the_mailed_file_reads(wired, api_client, org, cfg):
    r = api_client.get(PATH, params={"date_from": FROM, "date_to": TO, "org_id": org})
    assert r.status_code == 200, r.text
    body = r.json()
    for f in TOP_FIELDS:
        assert f in body, f"the export converter reads `{f}` and the endpoint no longer serves it"
    for f in TOTAL_FIELDS:
        assert f in body["totals"], f"tile `{f}` is mailed in the snapshot sheet"
    assert body["date_from"] == FROM and body["date_to"] == TO
    assert body["days"] == [FROM, "2026-10-02", TO]
    for row in body["rows"]:
        for f in ROW_FIELDS:
            assert f in row, f"row field `{f}` is a column in the mailed file"
        assert row["grain"] in ("store", "rep")


@pytest.mark.parametrize("org,cfg", TENANTS, ids=[t[0][-2:] for t in TENANTS])
def test_the_threshold_each_tenant_declared_is_the_one_served(wired, api_client, org, cfg):
    # RULE TWO: the number the mailed snapshot's note prints comes from the tenant's own config row,
    # never from code. If every tenant got the house threshold, three tenants would mail one
    # tenant's standard.
    body = api_client.get(PATH, params={"date_from": FROM, "date_to": TO, "org_id": org}).json()
    assert body["config"]["consecutive_days"] == cfg["consecutive_days"]
    assert body["config"]["gap_policy"] == cfg["gap_policy"]


@pytest.mark.parametrize("org,cfg", TENANTS, ids=[t[0][-2:] for t in TENANTS])
def test_a_day_nobody_measured_is_null_and_not_zero(wired, api_client, org, cfg):
    """THE CONTRACT THE MAILED FILE DEPENDS ON. The converter prints a blank for an unmeasured day
    *because* this endpoint returns null for it. A 0 here would travel into a spreadsheet as a
    counted fact with no banner beside it to question — the one thing this report must never do."""
    body = api_client.get(PATH, params={"date_from": FROM, "date_to": TO, "org_id": org}).json()
    for row in body["rows"]:
        for day, state in row["day_states"].items():
            if state not in ("had_sales", "measured_zero"):
                assert row["day_counts"].get(day) is None, (
                    f"{org} {row['label']} {day} is `{state}` yet carries a number")
        # and the two day tallies are never folded into one another
        assert row["days_measured_zero"] + row["days_not_reported"] <= len(body["days"])


def test_one_tenants_rows_are_never_another_tenants_feed_evidence(wired, api_client):
    """Org scoping, read off the REPORT rather than asserted about the query.

    Each tenant uploaded ONE day for ONE store of its own. So a leak does not show up here as a
    wrong total — it shows up as another tenant's STORE CODE in this tenant's report, or as this
    tenant's store reading "the feed arrived" on a day only another tenant uploaded. On this report
    that second failure is the serious one: another tenant's upload standing as proof that THIS
    tenant's store was looked at turns a data gap into a measured zero, which is the single thing
    the report exists to refuse."""
    MEASURED = {"had_sales", "measured_zero"}
    for org, _ in TENANTS:
        body = api_client.get(PATH, params={"date_from": FROM, "date_to": TO, "org_id": org}).json()
        store_rows = [r for r in body["rows"] if r["grain"] == "store"]
        assert store_rows, f"{org} has no store rows at all"
        assert {str(r["scope_key"]) for r in store_rows} == {OWN_STORE[org]}, (
            f"{org} is showing another tenant's store")
        measured_days = {d for r in store_rows for d, st in r["day_states"].items()
                         if st in MEASURED}
        assert measured_days == {OWN_DAY[org]}, (
            f"{org} believes its store was looked at on {sorted(measured_days)}, but it only "
            f"uploaded {OWN_DAY[org]} — the rest is another tenant's upload")
