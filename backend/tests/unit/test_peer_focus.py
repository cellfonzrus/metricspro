"""Unit: the SUBJECT-vs-PEERS comparison on the Peer Sales Comparison report (index §59.10).

OWNER ASK 2026-11-12, verbatim: *"under peer sales, add another filter of market and store along
with other tiles on top to compare that selected market or store to their compettive stores . the
isdea is to copare a nj market with all other store in thier band with other stores so the dm can be
held respsonbile"*.

THE TRAP THIS TEST EXISTS TO PIN, stated once. A market "filter" that NARROWS the rows destroys the
very comparison the owner asked for: filter the report to NJ and every band then holds NJ stores
only, so the band median becomes NJ's own median and the report tells the DM he is average. The
subject must narrow and the PEER SET MUST NOT. So the picked market / store is a SUBJECT (a
spotlight), the comparison set stays the whole in-scope estate, and the peer median a subject store
is measured against EXCLUDES every other subject store — otherwise NJ is partly compared with itself.

Every test here is DB-free and drives `peer_comparison.focus` over a payload `build()` produced, so
the band, the median rule and the verdict are the ones the screen serves rather than ones typed here.
"""
import pytest

from app.modules.commcalc import peer_comparison as pc

HOUSE_METRIC = "boxes_per_billpay"


def _cell(store, rep, day, *, boxes, billpay, acc=0.0):
    """ONE `_sales_cell_agg`-shaped cell. The distinct-transaction sets are the real shape: a set of
    transaction ids, which `roll_up` unions rather than sums."""
    return {
        "store": store, "rep": rep, "day": day,
        "box_count": boxes, "accessory_rev": acc,
        "_prem": {f"{store}-{day}-p{i}" for i in range(boxes)},
        "_port": set(), "_byod": set(), "_upg": set(), "_swap": set(), "_dev_tablet": set(),
        "_aal": set(),
        "_billpay_exec": {f"{store}-{day}-b{i}" for i in range(billpay)},
    }


#: A four-store estate across two markets, deliberately inside ONE traffic band (every store is
#: under 150 bill payments, the house band-0 cut) so the peer set is unambiguous.
#:   NJ:  N-1 100 billpay / 10 boxes (0.10)   N-2 100 / 20 (0.20)   → NJ median 0.15
#:   NY:  Y-1 100 / 40 (0.40)                 Y-2 100 / 30 (0.30)   → NY median 0.35
#: So NJ is BEHIND its band's non-NJ peers, and the peer median NJ is measured against (0.35) is NOT
#: the band median including NJ (0.25) — which is exactly the difference the owner asked for.
ESTATE = (
    ("N-1", 100, 10), ("N-2", 100, 20),
    ("Y-1", 100, 40), ("Y-2", 100, 30),
)
MARKET_OF = {"N-1": "NJ", "N-2": "NJ", "Y-1": "NY", "Y-2": "NY"}


def _payload(estate=ESTATE, market_of=None, **kw):
    market_of = MARKET_OF if market_of is None else market_of
    cells = {f"{s}|r|d": _cell(s, "r", "2026-11-01", boxes=bx, billpay=bp) for s, bp, bx in estate}
    out = pc.build(cells, resolve_market=lambda s: market_of.get(s, ""),
                   period="2026-11", **kw)
    out["lagging"] = pc.lagging(out, metric=HOUSE_METRIC)
    return out


# ── the subject is a spotlight, never a narrowing ────────────────────────────────────────────────

def test_the_peer_median_excludes_every_other_subject_store():
    """THE WHOLE POINT. NJ's peers are the NON-NJ stores in the band. If the subject's own stores
    were left in the peer set, a weak market would be measured partly against itself and the gap
    would shrink by exactly the thing being investigated."""
    f = pc.focus(_payload(), markets=["NJ"], metric=HOUSE_METRIC)
    band = f["bands"][0]
    assert band["peer_stores"] == 2, "the two NY stores are the peers"
    assert band["peer_median"] == pytest.approx(0.35), "median of NY only, never of NJ+NY"
    assert band["subject_median"] == pytest.approx(0.15)
    assert band["peer_leader"] == "Y-1" and band["peer_best"] == pytest.approx(0.40)
    assert band["gap_to_peer_median"] == pytest.approx(0.20)
    assert f["gap_median"] == pytest.approx(0.20)
    assert f["verdict"] == "behind"


def test_the_comparison_set_is_not_narrowed_by_the_subject():
    """A subject of one market must leave the payload's own rows and bands untouched — the peer
    stores are still there to be compared with. A `focus` that mutated the payload would be the
    narrowing filter this design refuses."""
    p = _payload()
    before = [r["store"] for r in p["rows"]]
    pc.focus(p, markets=["NJ"], metric=HOUSE_METRIC)
    assert [r["store"] for r in p["rows"]] == before
    assert len(p["rows"]) == 4


def test_a_focused_store_is_compared_with_its_band_and_names_the_leader():
    f = pc.focus(_payload(), stores=["N-1"], metric=HOUSE_METRIC)
    assert f["subject"]["kind"] == "store"
    assert f["subject"]["stores"] == ["N-1"]
    band = f["bands"][0]
    assert band["peer_stores"] == 3, "the other three stores, NJ sibling included"
    assert band["peer_median"] == pytest.approx(0.30)
    assert band["peer_leader"] == "Y-1" and band["peer_best"] == pytest.approx(0.40)
    assert band["verdict"] == "behind"
    assert f["gap_median"] == pytest.approx(0.20)


def test_a_focused_store_that_leads_its_band_is_not_called_behind():
    f = pc.focus(_payload(), stores=["Y-1"], metric=HOUSE_METRIC)
    assert f["verdict"] == "ahead"
    assert f["behind_count"] == 0
    assert (f["gap_median"] or 0) < 0


# ── honesty: a comparison that cannot run says so, and never with a zero ─────────────────────────

def test_no_subject_selected_is_not_the_estate_compared_with_itself():
    f = pc.focus(_payload(), metric=HOUSE_METRIC)
    assert f["measurable"] is False
    assert f["subject"]["kind"] == "estate"
    assert f["reason"], "the screen must be told why there are no tiles, not shown a 0"
    assert f["gap_median"] is None and f["verdict"] == "not_measured"


def test_a_subject_holding_every_store_in_a_band_has_no_peer_and_says_so():
    """A market that IS the whole band carries no gap — the same ruling `build()` already applies to
    a band of one store. Inventing a gap from a subject compared with itself would be the false
    accusation this report must never make."""
    f = pc.focus(_payload(), markets=["NJ", "NY"], metric=HOUSE_METRIC)
    assert f["measurable"] is False
    band = f["bands"][0]
    assert band["peer_stores"] == 0
    assert band["comparable"] is False
    assert band["peer_median"] is None, "never 0 — there is nobody to be behind"
    assert band["note"]


def test_a_store_outside_the_payload_is_reported_unknown_not_silently_dropped():
    """A store the caller asked for that the payload does not carry is either out of this user's RBAC
    scope or not trading this month. Both are facts the screen must state; a silent empty tile reads
    as "nothing to answer for"."""
    f = pc.focus(_payload(), stores=["N-1", "B-9999"], metric=HOUSE_METRIC)
    assert f["subject"]["unknown"] == ["B-9999"]
    assert f["subject"]["stores"] == ["N-1"], "the comparison runs on what is actually in view"
    assert f["measurable"] is True


def test_an_unbanded_subject_store_is_counted_apart_and_never_as_a_zero():
    estate = ESTATE + (("N-3", 0, 7),)
    market_of = dict(MARKET_OF, **{"N-3": "NJ"})
    f = pc.focus(_payload(estate, market_of), markets=["NJ"], metric=HOUSE_METRIC)
    assert f["not_banded"] == ["N-3"]
    assert f["stores_in_view"] == 2, "only the banded NJ stores are compared"
    assert f["not_banded_reason"]


def test_a_metric_the_subject_has_no_value_for_is_not_measured():
    """Family plan % comes from the carrier's own feed. With no feed the column is blank for every
    store — and a blank subject must come back `not_measured` with the reason, never a 0% gap."""
    f = pc.focus(_payload(), markets=["NJ"], metric="family_plan_pct")
    assert f["verdict"] == "not_measured"
    assert f["gap_median"] is None
    assert f["reason"]


# ── the verdict is the report's existing one, borrowed rather than re-defined ─────────────────────

def test_behind_is_read_from_lagging_and_not_re_decided():
    """`lagging()` is THE one definition of behind on this report (index §59.4). The subject tile
    REPORTS which of its own stores that call named; it must not apply a second test of its own, or
    the tiles and the coaching list below them could disagree about the same store."""
    p = _payload()
    named = {x["store"] for x in p["lagging"]}
    f = pc.focus(p, markets=["NJ"], metric=HOUSE_METRIC)
    assert {b["store"] for b in f["behind"]} == named & {"N-1", "N-2"}
    assert f["behind_count"] == len(f["behind"])
    assert f["behind"], "both NJ stores are behind the band median on this fixture"
    for item in f["behind"]:
        assert item["prompt"] == pc.prompt_sentence(item), (
            "the coaching sentence has ONE home; the tiles must not word it a second time")


def test_the_metric_is_declared_and_an_unknown_one_is_refused():
    f = pc.focus(_payload(), markets=["NJ"], metric="not_a_metric")
    assert f["metric"] == pc.DEFAULT_GAP_METRIC, "an unknown metric falls back to the declared default"
    assert f["metric_label"]


def test_every_gap_metric_the_report_declares_can_be_focused():
    """A metric the report offers in its picker but the tiles cannot answer would be a dead control.
    Derived from the declaration, so a new metric is covered the day it is declared."""
    p = _payload()
    for key, label, higher in pc.GAP_METRICS:
        f = pc.focus(p, markets=["NJ"], metric=key)
        assert f["metric"] == key and f["metric_label"] == label
        assert f["higher_is_better"] is bool(higher)
        assert f["verdict"] in ("behind", "ahead", "level", "not_measured")


def test_the_subject_label_reads_as_the_thing_picked():
    assert pc.focus(_payload(), markets=["NJ"], metric=HOUSE_METRIC)["subject"]["label"] == "NJ"
    assert pc.focus(_payload(), markets=["NJ", "NY"], metric=HOUSE_METRIC)["subject"]["label"] == "NJ + NY"
    assert pc.focus(_payload(), stores=["N-1"], metric=HOUSE_METRIC)["subject"]["label"] == "N-1"


def test_a_market_and_a_store_picked_together_intersect_to_the_store():
    """The cascade picker can emit both (pick NJ, then one NJ store). The subject is then that store,
    not the whole market — the narrower pick is what the person is looking at."""
    f = pc.focus(_payload(), markets=["NJ"], stores=["N-1"], metric=HOUSE_METRIC)
    assert f["subject"]["stores"] == ["N-1"]
    assert f["subject"]["kind"] == "store"


# ── RULE TWO / multi-tenant: nothing here knows a tenant's words ─────────────────────────────────

def test_a_tenants_own_band_cuts_and_market_names_drive_the_comparison():
    """Every feature is for EVERY tenant (owner directive 2026-10-30). This module holds no market
    name, no store code and no band cut of its own: a tenant whose markets are called something else
    and whose bands are declared elsewhere gets its own answer out of the same code."""
    estate = (("S1", 900, 90), ("S2", 800, 40), ("S3", 300, 30), ("S4", 310, 60))
    market_of = {"S1": "Region One", "S2": "Region One", "S3": "Region Two", "S4": "Region Two"}
    p = _payload(estate, market_of, bands=[500])
    f = pc.focus(p, markets=["Region One"], metric=HOUSE_METRIC)
    assert [b["band"] for b in f["bands"]] == [1], "both subject stores sit in the tenant's upper band"
    assert f["bands"][0]["peer_stores"] == 0, "the tenant's own cut leaves Region One alone up there"
    assert f["measurable"] is False and f["bands"][0]["note"]


def test_the_focus_is_pure_and_holds_no_vocabulary():
    """AST-free, cheap version of the §I un-wire rule: the function must not reach a sale line, a
    classifier or a tenant name. It is handed a payload and reads it."""
    src = pc.focus.__doc__ or ""
    assert src, "the one home for this answer documents itself"
    import inspect
    body = inspect.getsource(pc.focus)
    for banned in ("contract_type", "department", "daily_sales_feed", "Boost", "Cellfonz",
                   "LuxeLink", "Luxelink", "Vzone", "supabase", "client."):
        assert banned not in body, f"`{banned}` must not appear in a pure band comparison"
