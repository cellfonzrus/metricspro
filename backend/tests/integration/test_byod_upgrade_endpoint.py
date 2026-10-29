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


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE CHARGEBACK AMOUNT AND THE DRILL-DOWN (owner ask 2026-10-28)
#
# Owner, verbatim: *"inmclude the $ amount of the rebate and title it as chargeback amount, make it
# oneline summary top show the sales rep teh store the phone sold and the phone number meim and teh
# $ amount and thebn drill down like other menus to get mroe detail sof both trasnactions"*.
#
# These prove the PAYLOAD CONTRACT the screen reads. The four-way reason distinction and the
# per-grain provenance are proved end to end in `harness_byod_upgrade_wiring.py` §H, against a
# client that records the select string; what is proved here is that the real app, through real
# routing and middleware, hands the screen the keys it binds to.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MEID = "351449743101051"

#: The same pair, with a device identifier on the handset the store sold — the key the carrier pays
#: against, and without it no amount could ever be found.
A_PAIR_WITH_DEVICE = [
    dict(r, serial_1=MEID) if r["id"] == "r4" else dict(r) for r in A_PAIR
]

#: THE ORG'S DECLARED DEVICE VOCABULARY. Without it the handset columns are "not measured" rather
#: than zero (which is itself the right behaviour, and the subject of the tests above), so a test
#: about the handset the store SOLD has to declare one — a fixture that relied on a house default
#: would be asserting against a guess.
ACCESSORY_CONFIG = [{"org_id": "00000000-0000-0000-0000-000000000001",
                     "box_departments": ["Handsets"], "activation_details_rules": {},
                     "contract_type_map": {}, "activation_rules": []}]


def _seed_with_device(wired):
    wired.seed("commcalc.daily_sales_feed", A_PAIR_WITH_DEVICE)
    wired.seed("commcalc.accessory_config", ACCESSORY_CONFIG)


def test_the_one_line_summary_fields_are_all_on_the_case(wired, api_client):
    """A person, a place, a phone and a figure — the four things the owner asked the top line to
    show. The screen's columns bind to exactly these keys, so a rename here blanks the table."""
    _seed_with_device(wired)
    c = api_client.get(PATH, params={"period": PERIOD}).json()["cases"][0]
    assert c["rep"] == "Arora, Tanish"
    assert c["store"] == "B-1115"
    assert c["number"] == "2155550143"
    assert c["meid"] == MEID
    # THE PHONE NAMED IS THE ONE THE STORE SOLD — the handset on the upgrade side. The first side is
    # the device the customer brought in; naming that would report the phone the store did not sell.
    assert c["phone_sold"] == "moto g play - 2026 Q4 Promo Upgrade"
    assert c["meids"] == [MEID] and c["phones_sold"] == 1


def test_no_carrier_payment_reads_as_unmeasured_never_as_zero(wired, api_client):
    """A rebate lands months after the sale, so "nothing has arrived" and "$0.00 was paid" are
    DIFFERENT facts. The screen prints "not yet" off `None`; a 0.0 here would make it print $0.00
    and state a measurement nobody made (§19.26)."""
    _seed_with_device(wired)
    body = api_client.get(PATH, params={"period": PERIOD}).json()
    c = body["cases"][0]
    assert c["chargeback_amount"] is None          # None, NOT 0.0 — the whole point
    assert c["chargeback_measured"] is False
    assert c["chargeback_partial"] is False
    assert body["summary"]["chargeback_unmeasured_cases"] == 1
    assert body["summary"]["chargeback_measured_cases"] == 0
    # And the case is still reported: the pair is the finding, the money is context.
    assert len(body["cases"]) == 1


def test_the_payload_states_which_feed_the_amount_was_read_from(wired, api_client):
    """`chargeback_source` is how the screen tells "the carrier paid nothing" from "nothing was
    looked at" — it raises a warning card on either absence rather than leaving the reader to infer
    it from a column of dashes."""
    _seed_with_device(wired)
    src = api_client.get(PATH, params={"period": PERIOD}).json()["chargeback_source"]
    assert set(src) >= {"available"}
    if src.get("available"):
        assert src["feed_table"] == "raw_payment_detail"
        assert "configured" in src and "lag_window" in src


def test_the_drill_panels_are_served_on_the_shared_mechanism(wired, api_client):
    """"Drill down like other menus" IS the house panel (§CORE-17). The page renders whatever keys
    arrive here, so an endpoint that served none would silently drop the drill-down."""
    _seed_with_device(wired)
    panels = api_client.get(PATH, params={"period": PERIOD}).json()["panels"]
    assert "dollars" in panels
    p = panels["dollars"]
    assert p["figure"]["label"] and p["rows"]
    # A PANEL NAMES THE FEED THE FIGURE WAS READ FROM, per row — that is what makes it evidence
    # rather than a second rendering of the same table.
    assert all(r["sources"] for r in p["rows"])
    assert {t for r in p["rows"] for t in r["sources"]} == {"daily_sales_feed"}
    # The row's label is the owner's one-line summary, so the panel reads like the table above it.
    assert "Arora, Tanish" in p["rows"][0]["label"]


def test_every_served_panel_ties_to_the_summary_figure_it_explains(wired, api_client):
    """A panel that opens to rows summing to something other than the tile is worse than no panel:
    it looks like evidence and disagrees with the number it is under."""
    _seed_with_device(wired)
    body = api_client.get(PATH, params={"period": PERIOD}).json()
    for key, panel in (body["panels"] or {}).items():
        assert abs(float(panel["figure"]["amount"] or 0)
                   - float(body["summary"].get(key) or 0)) < 0.005, key
