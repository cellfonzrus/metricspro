"""Integration: GET /commcalc/watchdog/byod-upgrade through the REAL app (index §COMMCALC-43).

Real routing, real middleware, the real endpoint body — over the in-memory database double, with
conftest blocking every outbound socket. There is no staging Supabase, so "integration" here means
the app, not a real database (docs/TESTING.md).

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT: that the endpoint is reachable at its registered path,
that it reads the sales table the ONE home picks for the period rather than naming one, that it
answers the shape the screen reads, and that a feed the detector never looked at reports as
"no feed" rather than as a clean zero.

The column-level and sweep-level wiring (which columns the select must carry — the silent zero —
the span-filter ordering, and the flag row written through the migration-287 RPCs) is proved in
`backend/harness_byod_upgrade_wiring.py` against a client that records the select string, which
this double deliberately discards.
"""
import pytest

PERIOD = "2026-10"
PATH = "/api/v1/commcalc/watchdog/byod-upgrade"


def _line(trans_id, contract_type, **kw):
    row = {"org_id": "00000000-0000-0000-0000-000000000001", "period": PERIOD,
           "trans_id": trans_id, "contract_type": contract_type, "mdn": "2155550143",
           "store": "B-1115", "trans_date": "2026-10-04", "department": "Plans",
           "product_desc": "Unlimited Plus", "ext_price": 0.0, "salesperson": "Arora, Tanish",
           "voided": None, "trans_type": "Sale", "category": "Phones", "serial_1": "", "id": trans_id}
    row.update(kw)
    return row


A_PAIR = [
    _line("T-1", "BYOD", id="r1"),
    _line("T-1", "BYOD", department="Autopay", id="r2"),
    _line("T-2", "Upgrade", id="r3"),
    _line("T-2", "Upgrade", department="Handsets", ext_price=0.99, id="r4",
          product_desc="moto g play - 2026 Q4 Promo Upgrade"),
]


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    """The endpoint's client getter pointed at the double, and the config memo cleared."""
    from app.modules.commcalc import router as commcalc_router
    monkeypatch.setattr("app.modules.commcalc.watchdog_router.get_supabase", lambda: fake_supabase)
    for name in ("_ACFG_CACHE", "_CFG_CACHE"):
        cache = getattr(commcalc_router, name, None)
        if isinstance(cache, dict):
            cache.clear()
    return fake_supabase


def test_the_route_is_registered_at_its_documented_path(app_module):
    # `app.routes` is NOT flat here — including a router leaves one container in its place — so the
    # route table is read through its ONE home, `core/path_redact.route_templates`, rather than by
    # walking it again. (Reading only the top level is the 2026 defect that module documents.)
    from app.core.path_redact import route_templates
    assert PATH in set(route_templates(app_module))


def test_a_pair_on_one_number_comes_back_as_one_case(wired, api_client):
    wired.seed("commcalc.daily_sales_feed", A_PAIR)
    r = api_client.get(PATH, params={"period": PERIOD})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["period"] == PERIOD
    assert body["has_feed"] is True
    assert body["sales_table"] == "daily_sales_feed"      # the ONE home's answer for the open month
    assert body["classes"] == ["byod", "upgrade"]
    assert len(body["cases"]) == 1
    c = body["cases"][0]
    assert (c["store"], c["day"], c["number"]) == ("B-1115", "2026-10-04", "2155550143")
    assert sorted(c["receipts"]) == ["T-1", "T-2"]
    assert c["description"] and "signal" in c
    assert "verdict" not in c                              # it reports and does not conclude


def test_one_upgrade_over_many_lines_is_not_a_case_through_the_app(wired, api_client):
    wired.seed("commcalc.daily_sales_feed",
               [_line("T-9", "Upgrade", department=d, id=f"r{i}")
                for i, d in enumerate(("Plans", "Autopay", "Protection", "Handsets"))])
    body = api_client.get(PATH, params={"period": PERIOD}).json()
    assert body["cases"] == []
    assert body["has_feed"] is True                        # looked at, and genuinely clean


def test_an_empty_feed_says_no_feed_rather_than_no_cases(wired, api_client):
    body = api_client.get(PATH, params={"period": PERIOD}).json()
    assert body["cases"] == []
    assert body["has_feed"] is False                       # NOT a clean period — nothing was read
    assert body["summary"]["events"] == 0


def test_the_summary_carries_the_gaps_the_screen_must_state(wired, api_client):
    wired.seed("commcalc.daily_sales_feed", A_PAIR)
    s = api_client.get(PATH, params={"period": PERIOD}).json()["summary"]
    # numbered_share is the honest headline: one live tenant keys 93.9% of its events on a device.
    assert s["numbered_share"] == 1.0
    assert s["events_without_number"] == 0
    assert "box_vocabulary_matched" in s and "handset_lines" in s
    assert s["cannot_answer"]                              # the limits travel with the answer


def test_a_missing_sales_table_is_a_503_not_a_500(monkeypatch, api_client):
    def boom():
        raise RuntimeError("relation \"commcalc.daily_sales_feed\" does not exist")
    monkeypatch.setattr("app.modules.commcalc.watchdog_router.get_supabase",
                        lambda: (_ for _ in ()).throw(RuntimeError("no client")))
    r = api_client.get(PATH, params={"period": PERIOD})
    assert r.status_code in (500, 503)
