"""Integration: the cash calendar through the real endpoint, for every tenant (§PAYABLES-02).

Real routing, the real router body, the real one homes — over the in-memory database double, with
conftest blocking every outbound socket. There is no staging Supabase, so "integration" here means
the app, not a real database (docs/TESTING.md).

WHAT THIS COVERS THAT THE PROOF HARNESS CANNOT. `harness_cash_calendar.py` drives the three pure
homes and a fake PostgREST directly, so it cannot see the four things only the WIRING can get wrong:

  · that the endpoint really reaches the SHARED owed read rather than a query of its own — the
    duplicate defect the index rules gate on is a fact about the router, not about the homes;
  · which ORG's declaration answers, so one tenant's payout cycle cannot date another tenant's
    money even though both tenants' rows sit in the same physical tables (the multi-tenant rule,
    asserted rather than assumed);
  · that an UNMEASURED inbound reaches the response as `null` and withholds the net, instead of
    being coerced to 0.00 somewhere between the home and the JSON;
  · that the drill-downs are built over the response's OWN rows, so a panel cannot total something
    the screen refused to show.

THE FOUR TENANTS ARE THE REAL FOUR (production org ids, read 2026-11-09), because "works for every
tenant" is a claim about these orgs and not about two placeholders.
"""
import pytest

ORG_HOUSE = "00000000-0000-0000-0000-000000000001"     # Cellfonz R Us
ORG_LUXE = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"      # Luxelink Wireless LLC
ORG_VZONE = "f4f1c16e-2acf-4221-854a-c29a605754a7"     # Vzone
ORG_NYL = "ba084e25-adcf-4d4d-9cc5-d3728775513f"       # NY LOGISTICS

PATH = "/api/v1/payables/cash-calendar"
SOURCES_PATH = "/api/v1/payables/receipt-sources"
WIN = "start=2026-11-01&end=2026-11-30"

STORE_HOUSE = "B-1115"
STORE_LUXE = "L-204"


def _q(org, extra=""):
    return f"{PATH}?{WIN}&org_id={org}" + (f"&{extra}" if extra else "")


@pytest.fixture
def wired(monkeypatch, fake_supabase, app_module):
    """The real app, with the payables router's client swapped for the double."""
    monkeypatch.setattr("app.modules.payables.router.get_supabase", lambda: fake_supabase)

    # THE DECLARATIONS — two tenants declare, two have declared nothing. That is the production
    # shape, and the two that declared nothing are the half of the rule that hides: zero looks like
    # an answer.
    fake_supabase.seed("commcalc.receipt_source_map", [
        {"id": "r1", "org_id": ORG_HOUSE, "label": "carrier statement",
         "source_table": "raw_payment_detail", "amount_field": "amount",
         "payout_cadence": "semi_monthly", "payout_days": [6, 21],
         "payout_weekday": None, "payout_day_of_month": None, "payout_net_days": None,
         "is_active": True},
        {"id": "r2", "org_id": ORG_LUXE, "label": "processor statement",
         "source_table": "raw_ma_commission", "amount_field": "total_commission",
         "payout_cadence": "monthly", "payout_day_of_month": 15,
         "payout_days": None, "payout_weekday": None, "payout_net_days": None,
         "is_active": True},
    ])
    # THE FEEDS — each tenant's rows, and deliberately one tenant's rows inside the OTHER tenant's
    # declared table. An unscoped read would hand the house $9,999 of Luxelink's money.
    fake_supabase.seed("commcalc.raw_payment_detail", [
        {"org_id": ORG_HOUSE, "payment_date": "2026-11-02", "amount": 400.00,
         "business_address": STORE_HOUSE},
        {"org_id": ORG_HOUSE, "payment_date": "2026-11-03", "amount": 100.00,
         "business_address": STORE_HOUSE},
        {"org_id": ORG_LUXE, "payment_date": "2026-11-03", "amount": 9999.00,
         "business_address": STORE_LUXE},
    ])
    fake_supabase.seed("commcalc.raw_ma_commission", [
        {"org_id": ORG_LUXE, "tx_date": "2026-11-04", "total_commission": 250.00,
         "business_address": STORE_LUXE},
        {"org_id": ORG_HOUSE, "tx_date": "2026-11-04", "total_commission": 7777.00,
         "business_address": STORE_HOUSE},
    ])
    # THE OWED SIDE — the per-IMEI payable ledger the SHARED read answers off.
    fake_supabase.seed("commcalc.device_payable_ledger", [
        {"org_id": ORG_HOUSE, "due_date": "2026-11-06", "net_owed": 120.00, "owed": 120.00,
         "store": STORE_HOUSE, "imei": "111", "device_model": "A", "status": "on_inventory"},
        {"org_id": ORG_HOUSE, "due_date": "2026-11-10", "net_owed": 80.00, "owed": 80.00,
         "store": STORE_HOUSE, "imei": "222", "device_model": "A", "status": "on_inventory"},
        {"org_id": ORG_LUXE, "due_date": "2026-11-06", "net_owed": 55.00, "owed": 55.00,
         "store": STORE_LUXE, "imei": "333", "device_model": "B", "status": "on_inventory"},
        # Vzone owes money but has declared no payout cycle — the asymmetric case, which is the one
        # that proves an unknown half does not hide the known half.
        {"org_id": ORG_VZONE, "due_date": "2026-11-06", "net_owed": 40.00, "owed": 40.00,
         "store": "V-1", "imei": "444", "device_model": "C", "status": "on_inventory"},
    ])
    fake_supabase.seed("commcalc.store_mapping", [
        {"org_id": ORG_HOUSE, "store_address": STORE_HOUSE, "market": "LI"},
        {"org_id": ORG_LUXE, "store_address": STORE_LUXE, "market": "NJ"},
    ])
    return fake_supabase


# ── the tenant that declares a semi-monthly cycle ────────────────────────────────────────────────
def test_house_tenant_sees_its_own_money_on_its_own_declared_pay_day(wired, api_client):
    r = api_client.get(_q(ORG_HOUSE))
    assert r.status_code == 200
    out = r.json()
    # Both earning days are before the 6th, so both land on the 6th — the tenant's own declaration.
    assert out["total_in"] == 500.00
    assert out["expected_declared"] is True
    assert out["expected_source"] == "raw_payment_detail"
    by_day = {d["date"]: d for d in out["days"]}
    assert by_day["2026-11-06"]["expected_in"] == 500.00
    # The OWED side is the shared read's own figure, unchanged.
    assert out["total_out"] == 200.00
    assert out["owed_source"] == "device_ledger"
    # And the net is folded per day, then added up.
    assert by_day["2026-11-06"]["net"] == 380.00
    assert by_day["2026-11-10"]["net"] == -80.00
    assert out["total_net"] == 300.00


def test_the_running_position_carries_an_opening_balance_forward(wired, api_client):
    out = api_client.get(_q(ORG_HOUSE, "opening_balance=1000")).json()
    by_day = {d["date"]: d for d in out["days"]}
    assert by_day["2026-11-05"]["running"] == 1000.00
    assert by_day["2026-11-06"]["running"] == 1380.00
    assert by_day["2026-11-10"]["running"] == 1300.00
    assert out["closing_balance"] == 1300.00


# ── the tenant whose feed, column and cadence are all different ──────────────────────────────────
def test_a_second_tenant_answers_off_a_different_feed_column_and_cadence(wired, api_client):
    out = api_client.get(_q(ORG_LUXE)).json()
    assert out["expected_source"] == "raw_ma_commission"
    assert out["total_in"] == 250.00
    # Earned 4 Nov, a declared monthly 15th → the 15th.
    assert {d["date"]: d["expected_in"] for d in out["days"]}["2026-11-15"] == 250.00
    assert out["total_out"] == 55.00


def test_no_tenants_declaration_answers_another_tenants_read(wired, api_client):
    """THE NEGATIVE, and it is the half of the multi-tenant rule that hides. Both tenants' rows sit
    in both physical tables; only org-scoping on every read keeps them apart."""
    house = api_client.get(_q(ORG_HOUSE)).json()
    luxe = api_client.get(_q(ORG_LUXE)).json()
    assert house["total_in"] == 500.00          # never 500 + 9,999
    assert luxe["total_in"] == 250.00           # never 250 + 7,777
    assert house["total_out"] == 200.00         # never 200 + 55
    assert luxe["total_out"] == 55.00


# ── the two tenants that have declared nothing ───────────────────────────────────────────────────
def test_an_undeclared_tenant_is_not_measured_and_never_zero(wired, api_client, org=ORG_VZONE):
    """$0.00 would read as "no carrier owes us anything", which is a sentence about the carrier while
    the truth is a sentence about the setup. The two must not be the same answer."""
    out = api_client.get(_q(org)).json()
    assert out["expected_declared"] is False
    assert out["in_measured"] is False
    assert out["total_in"] is None
    assert all(d["expected_in"] is None for d in out["days"])
    # AND IT REFUSES TO NET: a cash position computed against an unknown is wrong by exactly the
    # amount nobody measured.
    assert out["total_net"] is None
    assert out["closing_balance"] is None
    assert all(d["net"] is None and d["running"] is None for d in out["days"])
    # The side it DID measure is still shown in full — one unknown half must not hide the known one.
    assert out["out_measured"] is True
    assert out["total_out"] == 40.00
    # And it says what is missing, in words.
    assert "declare" in (out["expected_note"] or "").lower()


def test_a_tenant_with_neither_side_measured_says_so_on_both(wired, api_client):
    """NY Logistics has no declaration and no payable rows. BOTH sides read not measured — and an
    empty owed read is not $0.00 owed either, which is `owed_read`'s own contract."""
    out = api_client.get(_q(ORG_NYL)).json()
    assert out["expected_declared"] is False and out["in_measured"] is False
    assert out["owed_source"] is None and out["out_measured"] is False
    assert out["total_in"] is None and out["total_out"] is None and out["total_net"] is None


# ── the filters are the window the money is READ through, not a client narrowing ─────────────────
def test_the_store_filter_narrows_both_sides_server_side(wired, api_client):
    out = api_client.get(_q(ORG_HOUSE, f"store={STORE_HOUSE}")).json()
    assert out["total_in"] == 500.00 and out["total_out"] == 200.00
    # A STORE THAT HAS NOTHING IS A MEASURED ZERO, not an unknown: the declaration answered and
    # nothing was there. Reading row count as measurement printed "not measured" here, which would
    # have made a quiet store look like a setup fault forever.
    none = api_client.get(_q(ORG_HOUSE, "store=B-9999")).json()
    assert none["in_measured"] is True and none["total_in"] == 0.0
    # THE OUT SIDE IS ASYMMETRIC HERE, and it is the SHARED home's contract rather than this
    # report's choice: `owed_read` returns `source: None` when neither of its sources produced a
    # row, deliberately, so that an empty read is never "$0.00 is owed". Under a filter that matches
    # nothing that reads as not measured — the same answer the Daily Owed screen gives — and
    # overriding it here would be a SECOND opinion about whether we owe anything, which is worse
    # than the asymmetry. Stated in the module docstring and index §PAYABLES-02.6, not papered over.
    assert none["out_measured"] is False and none["total_out"] is None
    assert none["total_net"] is None


def test_the_market_filter_narrows_both_sides_server_side(wired, api_client):
    assert api_client.get(_q(ORG_HOUSE, "market=LI")).json()["total_in"] == 500.00
    other = api_client.get(_q(ORG_HOUSE, "market=NJ")).json()
    assert other["in_measured"] is True and other["total_in"] == 0.0


def test_a_narrower_window_is_applied_at_READ_time_and_nothing_lands_on_the_edge_day(wired, api_client):
    """Both sides are read THROUGH the window, so money dated outside it never reaches the fold at
    all — `out_of_span` is a guard against a caller handing wider rows, and must stay zero here. The
    thing that must never happen is the 6th's 500.00 being clamped onto the 8th."""
    out = api_client.get(f"{PATH}?start=2026-11-08&end=2026-11-09&org_id={ORG_HOUSE}").json()
    assert out["in_measured"] is True and out["total_in"] == 0.0
    assert out["out_of_span_in"] == 0.0
    assert all(d["expected_in"] == 0.0 for d in out["days"])
    # Nothing from the 6th was clamped onto the edge day, which is the thing that must never happen.
    assert all(d["owed_out"] in (0.0, None) for d in out["days"])


# ── every figure names its rows, and a panel cannot total what the screen refused ────────────────
def test_the_three_figures_drill_and_tie_out(wired, api_client):
    out = api_client.get(_q(ORG_HOUSE)).json()
    drills = out["drilldowns"]
    assert sorted(drills) == ["total_in", "total_net", "total_out"]
    for key in drills:
        assert drills[key]["tie_out"]["ties"] is True
    # The panels name THIS TENANT'S OWN declared relation, not a literal.
    tables = {o["table"] for r in drills["total_in"]["rows"] for o in r["origins"]}
    assert tables == {"raw_payment_detail"}
    # The closing balance deliberately has NO panel: the opening balance is not a row, so no set of
    # records adds up to it (§CORE-17.3h).
    assert "closing_balance" not in drills


def test_an_unmeasured_side_gets_no_panel_at_all(wired, api_client):
    """A panel over an unmeasured figure would total something the cell above it refused to show."""
    out = api_client.get(_q(ORG_NYL)).json()
    assert out["drilldowns"] == {}
    half = api_client.get(_q(ORG_VZONE)).json()
    assert sorted(half["drilldowns"]) == ["total_out"]


# ── the declaration endpoints ────────────────────────────────────────────────────────────────────
def test_the_declaration_read_offers_only_cadences_and_feeds_that_can_answer(wired, api_client):
    from app.modules.payables import receipt_schedule as rs
    out = api_client.get(f"{SOURCES_PATH}?org_id={ORG_HOUSE}").json()
    assert [r["label"] for r in out["rows"]] == ["carrier statement"]
    assert out["cadences"] == list(rs.CADENCES)
    # A feed the lineage registry declares has NO day column cannot be placed on a calendar, so it
    # is not offered as a choice — the registry's None is a declaration and is honoured.
    assert "raw_payment_detail" in out["candidate_tables"]
    assert "mi_residual" not in out["candidate_tables"]


def test_a_declaration_naming_an_undatable_feed_is_refused_rather_than_stored(wired, api_client):
    r = api_client.put(f"{SOURCES_PATH}?org_id={ORG_VZONE}",
                       json={"source_table": "mi_residual", "amount_field": "amount",
                             "payout_cadence": "monthly", "payout_day_of_month": 15})
    assert r.status_code == 400
    assert "day" in r.json()["detail"].lower()


def test_a_cadence_the_home_has_never_ruled_on_cannot_be_saved(wired, api_client):
    r = api_client.put(f"{SOURCES_PATH}?org_id={ORG_VZONE}",
                       json={"source_table": "raw_payment_detail", "amount_field": "amount",
                             "payout_cadence": "whenever_they_feel_like_it"})
    assert r.status_code == 400


def test_declaring_a_cycle_fills_a_previously_unmeasured_calendar(wired, api_client):
    """The whole loop, end to end: the tenant that read NOT MEASURED declares its cycle and the
    calendar answers — with no code change, which is what config-never-code means here."""
    fake = wired
    fake.seed("commcalc.raw_payment_detail", fake._store["commcalc.raw_payment_detail"] + [
        {"org_id": ORG_VZONE, "payment_date": "2026-11-02", "amount": 300.00,
         "business_address": "V-1"}])
    before = api_client.get(_q(ORG_VZONE)).json()
    assert before["total_in"] is None

    r = api_client.put(f"{SOURCES_PATH}?org_id={ORG_VZONE}",
                       json={"label": "carrier statement", "source_table": "raw_payment_detail",
                             "amount_field": "amount", "payout_cadence": "weekly",
                             "payout_weekday": 4})
    assert r.status_code == 200

    after = api_client.get(_q(ORG_VZONE)).json()
    assert after["expected_declared"] is True
    # Earned Mon 2 Nov, declared pay weekday Friday → Fri 6 Nov 2026.
    assert {d["date"]: d["expected_in"] for d in after["days"]}["2026-11-06"] == 300.00
    assert after["total_in"] == 300.00
    # And it still sees only ITS OWN money, with the two other tenants' rows in the same table.
    assert after["total_in"] == 300.00
