"""A card for many stores costs a fixed number of reads, not one set per store (index §STOREOPS-02).

The 1,000-user load test established that this platform queues on ROUND TRIPS: every read is an
HTTPS call to Supabase holding one of the `workers x 40` worker threads. `_gr_store_card` answered
for ONE store in 3-4 reads and three endpoints looped over it, so the DM dashboard cost 112 round
trips on a 28-store tenant and grew with the store count.

These tests pin the COST, not only the answer — a correct result that takes one trip per store is
the defect this file exists to catch.
"""
import pytest

from tests.fakes.supabase import FakeSupabase
import app.modules.storeops.router as R

ORG = "00000000-0000-0000-0000-000000000001"
CFG = {"target_default": 4.5, "lookback_days": 30}


def _codes(n):
    return [f"B-{i:04d}" for i in range(n)]


def _sb(codes, with_plans=True):
    return FakeSupabase({
        "public.stores": [{"org_id": ORG, "store_code": c, "address": f"{i} Main St",
                           "market": "M", "is_active": True} for i, c in enumerate(codes)],
        "public.google_review_store": [{"org_id": ORG, "store_code": c, "place_id": f"p{i}"}
                                       for i, c in enumerate(codes)],
        "public.google_review_snapshot": [
            {"org_id": ORG, "store_code": c, "rating": 4.2, "review_count": 10 + i,
             "fetched_at": f"2026-10-2{i % 9}T00:00:00Z"} for i, c in enumerate(codes)],
        "public.google_review_item": [
            {"org_id": ORG, "store_code": c, "matched_employee_id": None,
             "first_seen_at": "2026-10-20T00:00:00Z"} for c in codes],
        "public.action_plan": ([{"org_id": ORG, "store_code": c, "employee_id": "E1",
                                 "status": "open", "created_at": "2026-10-20T00:00:00Z"}
                                for c in codes] if with_plans else []),
    })


def _by_code(sb):
    return {s["store_code"]: s
            for s in sb.table("stores").select("*").eq("org_id", ORG).execute().data}


@pytest.mark.parametrize("n", [1, 5, 28, 40])
def test_every_store_gets_a_card(n):
    codes = _codes(n)
    sb = _sb(codes)
    cards = R._gr_store_cards(sb, ORG, codes, _by_code(sb), CFG)
    assert sorted(cards) == sorted(codes)


def test_the_read_count_does_not_grow_with_the_store_count():
    counts = []
    for n in (1, 5, 28, 40):
        codes = _codes(n)
        sb = _sb(codes)
        by = _by_code(sb)
        sb.reset_reads()
        R._gr_store_cards(sb, ORG, codes, by, CFG)
        counts.append(sb.read_count)
    assert len(set(counts)) == 1, f"reads grew with the store count: {counts}"


def test_twenty_eight_stores_cost_a_handful_of_reads_not_a_hundred():
    codes = _codes(28)
    sb = _sb(codes)
    by = _by_code(sb)
    sb.reset_reads()
    R._gr_store_cards(sb, ORG, codes, by, CFG, employee_id="E1")
    # 112 before this change (4 per store). The ceiling is deliberately loose: what must never
    # come back is a count that TRACKS the store count.
    assert sb.read_count <= 6, f"{sb.read_count} reads for 28 stores"


def test_the_dm_dashboards_action_plans_are_one_set_read_too():
    codes = _codes(28)
    sb = _sb(codes)
    sb.reset_reads()
    plans = R._gr_action_plans_by_store(sb, ORG, codes)
    assert sb.read_count <= 2, f"{sb.read_count} reads for 28 stores of plans"
    assert len(plans) == 28


def test_one_stores_card_is_the_same_answer_either_way():
    codes = _codes(3)
    sb = _sb(codes)
    by = _by_code(sb)
    one = R._gr_store_card(sb, ORG, codes[1], by[codes[1]], CFG)
    many = R._gr_store_cards(sb, ORG, codes, by, CFG)
    assert one == many[codes[1]]


def test_a_store_with_no_review_data_still_gets_a_card():
    sb = _sb([])
    cards = R._gr_store_cards(sb, ORG, ["B-9999"], {}, CFG)
    assert list(cards) == ["B-9999"]
    assert cards["B-9999"]["rating"] is None
    assert cards["B-9999"]["store_code"] == "B-9999"


def test_an_empty_set_of_codes_reads_nothing_at_all():
    sb = _sb(_codes(3))
    sb.reset_reads()
    assert R._gr_store_cards(sb, ORG, [], {}, CFG) == {}
    assert sb.read_count == 0


def test_every_read_is_org_scoped():
    codes = _codes(4)
    sb = _sb(codes)
    by = _by_code(sb)
    sb.reset_reads()
    R._gr_store_cards(sb, ORG, codes, by, CFG, employee_id="E1")
    for key, filters in sb.reads:
        assert any(f[0] == "eq" and f[1] == "org_id" for f in filters), f"{key} is not org-scoped"
