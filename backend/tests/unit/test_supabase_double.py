"""Unit tests for the in-memory Supabase double (tests/fakes/supabase.py).

WHY THIS FILE IS A UNIT TEST AND NOT AN AFTERTHOUGHT. Every integration test in this repo runs the
REAL FastAPI app against this double, because there is no staging database and a test must never
reach production Supabase. So the double is load-bearing: if its chain semantics drift from
postgrest's, every integration test built on it is asserting against a fiction. These tests pin the
semantics the double promises.
"""
import pytest

from tests.fakes.supabase import FakeSupabase, OutboundWriteRefused


ORG = "00000000-0000-0000-0000-000000000001"


def _sb():
    return FakeSupabase({
        "commcalc.store_mapping": [
            {"id": 1, "org_id": ORG, "store_code": "B-1115", "address": "1 S 60th"},
            {"id": 2, "org_id": ORG, "store_code": "B-1598", "address": "1598 Mount Ephraim"},
            {"id": 3, "org_id": "other-org", "store_code": "X-1", "address": "elsewhere"},
        ],
    })


def test_select_eq_filters_rows():
    rows = _sb().schema("commcalc").table("store_mapping").select("*").eq("org_id", ORG).execute().data
    assert [r["store_code"] for r in rows] == ["B-1115", "B-1598"]


def test_select_returns_a_copy_so_a_caller_cannot_mutate_the_store():
    sb = _sb()
    rows = sb.schema("commcalc").table("store_mapping").select("*").execute().data
    rows[0]["store_code"] = "MUTATED"
    again = sb.schema("commcalc").table("store_mapping").select("*").execute().data
    assert again[0]["store_code"] == "B-1115"


def test_schema_is_part_of_the_table_identity():
    # commcalc.store_mapping and storeops.store_mapping are DIFFERENT relations, exactly as in
    # postgrest. A double that ignored the schema would let a test pass against the wrong table.
    sb = FakeSupabase({"commcalc.t": [{"a": 1}], "storeops.t": [{"a": 2}]})
    assert sb.schema("commcalc").table("t").select("*").execute().data == [{"a": 1}]
    assert sb.schema("storeops").table("t").select("*").execute().data == [{"a": 2}]


def test_default_schema_is_public():
    sb = FakeSupabase({"public.t": [{"a": 1}]})
    assert sb.table("t").select("*").execute().data == [{"a": 1}]


def test_insert_then_read_back():
    sb = _sb()
    sb.schema("commcalc").table("store_mapping").insert({"id": 9, "org_id": ORG, "store_code": "B-9"}).execute()
    codes = [r["store_code"] for r in
             sb.schema("commcalc").table("store_mapping").select("*").eq("org_id", ORG).execute().data]
    assert "B-9" in codes


def test_update_only_touches_matching_rows():
    sb = _sb()
    sb.schema("commcalc").table("store_mapping").update({"address": "new"}).eq("store_code", "B-1115").execute()
    rows = {r["store_code"]: r["address"]
            for r in sb.schema("commcalc").table("store_mapping").select("*").execute().data}
    assert rows["B-1115"] == "new"
    assert rows["B-1598"] == "1598 Mount Ephraim"


def test_delete_without_a_filter_is_refused():
    # A delete+insert rebuild that loses its filter emptied 11,000 live payable rows once
    # (index PAYABLES-01). The double refuses the unfiltered shape so a test can PROVE a caller
    # never issues it.
    sb = _sb()
    with pytest.raises(OutboundWriteRefused):
        sb.schema("commcalc").table("store_mapping").delete().execute()


def test_delete_with_a_filter_removes_only_those_rows():
    sb = _sb()
    sb.schema("commcalc").table("store_mapping").delete().eq("store_code", "B-1115").execute()
    assert [r["store_code"] for r in
            sb.schema("commcalc").table("store_mapping").select("*").eq("org_id", ORG).execute().data] == ["B-1598"]


def test_in_gte_lte_order_and_limit():
    sb = FakeSupabase({"public.n": [{"v": i} for i in range(1, 6)]})
    q = sb.table("n").select("*").gte("v", 2).lte("v", 4).order("v", desc=True).execute().data
    assert [r["v"] for r in q] == [4, 3, 2]
    assert [r["v"] for r in sb.table("n").select("*").in_("v", [1, 5]).execute().data] == [1, 5]
    assert len(sb.table("n").select("*").limit(2).execute().data) == 2


def test_single_returns_the_row_itself_not_a_list():
    sb = _sb()
    row = sb.schema("commcalc").table("store_mapping").select("*").eq("store_code", "B-1115").single().execute().data
    assert row["address"] == "1 S 60th"


def test_an_unknown_relation_reads_empty_rather_than_raising():
    # postgrest 404s, but a test that reads a table it never seeded is almost always asserting
    # "nothing is there" — raising would make the double harder to use than the thing it doubles.
    assert FakeSupabase({}).schema("commcalc").table("nope").select("*").execute().data == []


# ── or_ : the shape a per-store setting is read with ─────────────────────────────────────────────
# `pos/router._pos_settings_rows` asks for "this store's rows OR the org-wide ones" as PostgREST's
# own `or=(store_code.is.null,store_code.eq.S1)` string. Until the double understood it the call
# raised, the router's `except` swallowed it, and a receipt integration test saw an org with no
# settings at all — the suite agreeing with the defect instead of catching it.
def _settings():
    return FakeSupabase({"pos.pos_settings": [
        {"org_id": ORG, "store_code": None, "key": "brand_company_name", "value": "Org Co"},
        {"org_id": ORG, "store_code": "S1", "key": "brand_company_name", "value": "Store Co"},
        {"org_id": ORG, "store_code": "S2", "key": "brand_company_name", "value": "Other Co"},
    ]})


def test_or_reads_this_store_s_rows_and_the_org_wide_ones():
    rows = (_settings().schema("pos").table("pos_settings").select("*").eq("org_id", ORG)
            .or_("store_code.is.null,store_code.eq.S1").execute().data)
    assert sorted(r["value"] for r in rows) == ["Org Co", "Store Co"]


def test_or_is_anded_with_the_filters_around_it():
    rows = (_settings().schema("pos").table("pos_settings").select("*").eq("org_id", "other-org")
            .or_("store_code.is.null,store_code.eq.S1").execute().data)
    assert rows == []


def test_an_or_term_the_double_cannot_read_raises_rather_than_matching_nothing():
    with pytest.raises(NotImplementedError):
        _settings().table("pos_settings").select("*").or_("store_code.like.S%").execute()
