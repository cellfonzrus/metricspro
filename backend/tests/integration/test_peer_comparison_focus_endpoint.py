"""Integration: GET /commcalc/peer-comparison's SUBJECT tiles through the REAL app (index §59.10).

Real routing, real middleware, the real endpoint body — over the in-memory database double, with
conftest blocking every outbound socket. There is no staging Supabase, so "integration" here means
the app, not a real database (docs/TESTING.md).

WHY A UNIT TEST IS NOT ENOUGH HERE. `peer_comparison.focus` is pure and is proved over a typed
payload. What only the app can prove is that the payload REALLY has that shape by the time focus
reads it: that the roll-up stamped the market the picker offers, that the subject is applied AFTER
the RBAC keyset rather than before it, and that the tiles' two counts arrive with the panels the
screen offers beside them. A rename on either side of that seam leaves the screen rendering and the
tiles silently empty — which, on a report whose purpose is to hold a manager accountable, reads as
"this market has nothing to answer for".

MULTI-TENANT (project rule 2026-10-30): every assertion is made for THREE orgs, each declaring its
OWN box departments and its OWN market names, and each org's answer is checked to be its own. The
one thing this report must never do is answer one tenant's question with another tenant's words.
"""
import pytest

PATH = "/api/v1/commcalc/peer-comparison"
HOUSE = "00000000-0000-0000-0000-000000000001"
PERIOD = "2026-11"

#: Three tenants, each with its own DEPARTMENT vocabulary (what counts as a box) and its own MARKET
#: names. A default behind any of these would make one tenant's report answer with another's words.
TENANTS = (
    (HOUSE, "Handsets", ("NJ", "NY")),
    ("00000000-0000-0000-0000-0000000000a2", "Mobiles", ("Jersey North", "Jersey South")),
    ("00000000-0000-0000-0000-0000000000a3", "Devices", ("Region One", "Region Two")),
)
#: Two stores per market, built so the FIRST market is behind the second one inside the one traffic
#: band: subject median 0.15, peer median 0.35, peer best 0.40 at the second market's leader.
#:   store, bill payments, boxes
LAYOUT = (("S-1", 100, 10), ("S-2", 100, 20), ("S-3", 100, 40), ("S-4", 100, 30))
BILLPAY_DEPT = "RTR"          # the house `bill_payment` exec rule (mig 962 default)


def _stores(org):
    """{store_code: market} for one tenant — the first two stores in its first market."""
    _org, _dept, (m1, m2) = next(t for t in TENANTS if t[0] == org)
    return {f"{org[-2:]}-{code}": (m1 if i < 2 else m2)
            for i, (code, _bp, _bx) in enumerate(LAYOUT)}


def _line(org, store, trans, day, dept, product="moto g play", price=49.99):
    return {"org_id": org, "period": "November 2026", "trans_id": trans,
            "id": f"{org[-2:]}{store}{trans}", "store": store, "salesperson": "Arora, Tanish",
            "trans_date": day, "contract_type": "New", "department": dept,
            "category": "Phones" if dept != BILLPAY_DEPT else "RTR Product",
            "product_desc": product, "ext_price": price, "voided": None, "trans_type": "Sale",
            "serial_1": f"S{trans}", "mdn": "2155550143", "tender_type": "Cash"}


def _feed(org):
    _o, dept, _m = next(t for t in TENANTS if t[0] == org)
    rows, markets = [], _stores(org)
    for i, (code, billpay, boxes) in enumerate(LAYOUT):
        store = f"{org[-2:]}-{code}"
        for n in range(billpay):
            rows.append(_line(org, store, f"BP{i}-{n}", "2026-11-03", BILLPAY_DEPT,
                              product="Bill Payment", price=40.0))
        for n in range(boxes):
            rows.append(_line(org, store, f"BX{i}-{n}", "2026-11-03", dept))
    assert markets
    return rows


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    from app.modules.commcalc import router as commcalc_router
    monkeypatch.setattr("app.modules.commcalc.router.get_supabase", lambda: fake_supabase,
                        raising=False)
    for name in ("_ACFG_CACHE", "_CFG_CACHE"):
        cache = getattr(commcalc_router, name, None)
        if isinstance(cache, dict):
            cache.clear()
    try:
        from app.modules.commcalc import accessory_catalog as _accat
        _accat.cache_invalidate_all()
    except Exception:
        pass
    feed, mapping, acfg = [], [], []
    for org, dept, _markets in TENANTS:
        feed += _feed(org)
        acfg.append({"org_id": org, "box_departments": [dept], "activation_details_rules": {},
                     "contract_type_map": {}, "activation_rules": []})
        for store, market in _stores(org).items():
            mapping.append({"org_id": org, "store_code": store, "market": market,
                            "store_address": f"{store} Main St"})
    fake_supabase.seed("commcalc.daily_sales_feed", feed)
    fake_supabase.seed("commcalc.store_mapping", mapping)
    fake_supabase.seed("commcalc.accessory_config", acfg)
    return fake_supabase


def _get(api_client, org, **params):
    r = api_client.get(PATH, params={"period": PERIOD, "org_id": org, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _first_market(org):
    return next(t for t in TENANTS if t[0] == org)[2][0]


# ── the payload contract the tiles bind to ───────────────────────────────────────────────────────

FOCUS_FIELDS = ("subject", "metric", "metric_label", "higher_is_better", "measurable", "reason",
                "stores_in_view", "peer_stores", "not_banded", "not_banded_reason", "gap_median",
                "verdict", "behind", "behind_count", "bands", "peer_basis", "composite_basis",
                "sentence")
BAND_FIELDS = ("band", "label", "subject_stores", "peer_stores", "subject_median", "peer_median",
               "peer_best", "peer_leader", "gap_to_peer_median", "gap_to_peer_best", "comparable",
               "verdict", "note")


@pytest.mark.parametrize("org", [t[0] for t in TENANTS], ids=[t[0][-2:] for t in TENANTS])
def test_every_field_the_tiles_read_is_served(wired, api_client, org):
    body = _get(api_client, org, focus_markets=_first_market(org))
    assert "focus" in body, "the tile strip binds to `focus`"
    for f in FOCUS_FIELDS:
        assert f in body["focus"], f"tile field `{f}` is no longer served"
    for b in body["focus"]["bands"]:
        for f in BAND_FIELDS:
            assert f in b, f"per-band comparison field `{f}` is no longer served"


@pytest.mark.parametrize("org", [t[0] for t in TENANTS], ids=[t[0][-2:] for t in TENANTS])
def test_the_cascade_roster_carries_each_stores_own_market(wired, api_client, org):
    """The shared market/store picker cascades on `store_options[*].market`. Without the market on
    the roster the store dropdown cannot narrow, which is how a store filter silently offers another
    market's stores."""
    body = _get(api_client, org)
    roster = {r["store"]: r["market"] for r in body["store_options"]}
    assert roster == _stores(org)
    assert set(body["markets"]) >= set(next(t for t in TENANTS if t[0] == org)[2])


@pytest.mark.parametrize("org", [t[0] for t in TENANTS], ids=[t[0][-2:] for t in TENANTS])
def test_the_subject_is_measured_against_the_other_market_in_its_band(wired, api_client, org):
    """THE OWNER'S QUESTION, end to end: one market against every other store in its band."""
    body = _get(api_client, org, focus_markets=_first_market(org))
    foc = body["focus"]
    assert foc["subject"]["kind"] == "market"
    assert foc["stores_in_view"] == 2 and foc["peer_stores"] == 2
    assert foc["measurable"] is True
    band = foc["bands"][0]
    assert band["subject_median"] == pytest.approx(0.15)
    assert band["peer_median"] == pytest.approx(0.35), "the OTHER market only, never its own stores"
    assert band["peer_best"] == pytest.approx(0.40)
    assert band["peer_leader"] == f"{org[-2:]}-S-3"
    assert foc["verdict"] == "behind" and foc["gap_median"] == pytest.approx(0.20)
    assert foc["sentence"].startswith(_first_market(org))


@pytest.mark.parametrize("org", [t[0] for t in TENANTS], ids=[t[0][-2:] for t in TENANTS])
def test_the_comparison_set_is_not_narrowed_by_the_subject(wired, api_client, org):
    """A subject must not shrink the table. If it did, the band median would become the subject's own
    and the report would tell every manager he is average — the defect this design exists to avoid."""
    wide = _get(api_client, org)
    focused = _get(api_client, org, focus_markets=_first_market(org))
    assert [r["store"] for r in focused["rows"]] == [r["store"] for r in wide["rows"]]
    assert focused["bands"] == wide["bands"]


@pytest.mark.parametrize("org", [t[0] for t in TENANTS], ids=[t[0][-2:] for t in TENANTS])
def test_a_store_subject_names_the_leader_it_is_behind(wired, api_client, org):
    body = _get(api_client, org, focus_stores=f"{org[-2:]}-S-1")
    foc = body["focus"]
    assert foc["subject"]["kind"] == "store" and foc["stores_in_view"] == 1
    assert foc["bands"][0]["peer_stores"] == 3
    assert foc["bands"][0]["peer_leader"] == f"{org[-2:]}-S-3"
    assert foc["behind_count"] == 1 and foc["behind"][0]["prompt"]


@pytest.mark.parametrize("org", [t[0] for t in TENANTS], ids=[t[0][-2:] for t in TENANTS])
def test_nothing_focused_states_why_there_are_no_tiles(wired, api_client, org):
    foc = _get(api_client, org)["focus"]
    assert foc["measurable"] is False and foc["subject"]["kind"] == "estate"
    assert foc["reason"] and foc["gap_median"] is None


@pytest.mark.parametrize("org", [t[0] for t in TENANTS], ids=[t[0][-2:] for t in TENANTS])
def test_the_subjects_two_counts_carry_their_row_lists(wired, api_client, org):
    """§CORE-17.3u: a COUNT on this report opens to the store rows behind it. The two the tiles print
    are the selection's stores and how many of them are behind their own band."""
    body = _get(api_client, org, focus_markets=_first_market(org))
    panels = body["drilldown"]
    for key in ("focus.stores", "focus.behind"):
        assert key in panels, f"the tile prints a count with no row list behind it: {key}"
    stores = {r["label"] for r in panels["focus.stores"]["rows"] if r["in_figure"]}
    assert stores == {f"{org[-2:]}-S-1", f"{org[-2:]}-S-2"}
    # EVERY STORE IN VIEW IS SHOWN, and only the selection's are counted — the panel says which, and
    # the figure it opens is the producer's own, so it ties to the tile exactly.
    assert panels["focus.stores"]["figure"]["amount"] == body["focus"]["stores_in_view"]
    assert panels["focus.behind"]["figure"]["amount"] == body["focus"]["behind_count"]
    for key in ("focus.stores", "focus.behind"):
        assert panels[key]["tie_out"]["ties"] is True, panels[key]["tie_out"]["words"]
        assert panels[key]["tie_out"]["difference"] == 0


# ── scope: the subject cannot reach past what the caller may already read ────────────────────────

@pytest.mark.parametrize("org", [t[0] for t in TENANTS], ids=[t[0][-2:] for t in TENANTS])
def test_another_tenants_store_cannot_be_focused(wired, api_client, org):
    """THE CROSS-TENANT RULE (2026-10-30). The subject is resolved against the rows already served,
    so another tenant's store code reaches no row — and is REPORTED as unknown rather than quietly
    dropped, because a blank tile reads as a market with nothing to answer for."""
    other = next(t[0] for t in TENANTS if t[0] != org)
    foreign = f"{other[-2:]}-S-1"
    foc = _get(api_client, org, focus_stores=foreign)["focus"]
    assert foc["subject"]["stores"] == []
    assert foc["subject"]["unknown"] == [foreign]
    assert foc["measurable"] is False and foc["reason"]
    assert "accountable" not in foc, "no org-tree read happens for a subject that reached no store"


@pytest.mark.parametrize("org", [t[0] for t in TENANTS], ids=[t[0][-2:] for t in TENANTS])
def test_another_tenants_market_name_reaches_none_of_this_tenants_stores(wired, api_client, org):
    other_market = next(t[2][0] for t in TENANTS if t[0] != org)
    foc = _get(api_client, org, focus_markets=other_market)["focus"]
    assert foc["subject"]["stores"] == []
    assert foc["subject"]["unknown"] == [other_market]
    assert foc["measurable"] is False


@pytest.mark.parametrize("org", [t[0] for t in TENANTS], ids=[t[0][-2:] for t in TENANTS])
def test_the_tiles_never_name_a_store_outside_this_tenant(wired, api_client, org):
    """Belt and braces on the leak that would matter most: a peer leader, a coaching sentence or a
    drill-down row carrying another tenant's store code."""
    body = _get(api_client, org, focus_markets=_first_market(org))
    mine = set(_stores(org))
    named = {b["peer_leader"] for b in body["focus"]["bands"] if b["peer_leader"]}
    named |= {x["store"] for x in body["focus"]["behind"]}
    named |= {r["label"] for r in body["drilldown"]["focus.stores"]["rows"]}
    assert named <= mine, f"{named - mine} belongs to another tenant"


# ── accountability: who owns the subject, and the honest blank when nobody does ──────────────────

@pytest.mark.parametrize("org", [t[0] for t in TENANTS], ids=[t[0][-2:] for t in TENANTS])
def test_an_unwired_org_tree_says_so_rather_than_naming_nobody(wired, api_client, org):
    """No org chart is seeded here, so no store has a district manager. "Nobody is assigned" and
    "the tree was never set up" are the same fact in this fixture and it must be STATED — a silent
    empty manager list reads as a market nobody has to answer for."""
    acc = _get(api_client, org, focus_markets=_first_market(org))["focus"]["accountable"]
    assert acc["dms"] == []
    assert acc["no_dm"] == sorted(s for s, m in _stores(org).items() if m == _first_market(org)) \
        or acc["error"]
    assert acc["note"], "the tile must print why it can name nobody"
    assert "org chart" in acc["note"] or acc["error"]
