"""UNIT: the Weekly Performance Review fold — pure, DB-free (docs/TESTING.md §1, TDD).

Owner ask 2026-11-16, verbatim: *"Add the performance deck to the management tile but allow access
only for the admins, make this report every week at the end of the week so the same can be reviewed
every monday with the dm, it should have invenotry aging for rteh repsective carrier also as one of
the items to review., make a separate report for each market and one consolidated for the entire
company, is there any way we can make this on demand"* — and, in the follow-up, *"let us make it on
demand"*.

These tests are written BEFORE the module. They pin the three things this report can get silently
wrong:

  · A FACT NOBODY MEASURED IS NEVER A ZERO. Every item is three-state (measured / not measured), the
    reason is carried in words, and the figure is None — the Zero Sales doctrine (§CORE-27) applied
    to a review pack a district manager is going to be judged on.
  · A STORE IS NEVER DROPPED. A store the market index cannot place lands in a named scope of its
    own; a market scope that loses a store would quietly shrink the company total.
  · THE TENANT'S OWN VOCABULARY IS A REQUIRED ARGUMENT (CLAUDE.md "Every feature is for EVERY
    tenant"). Markets and carrier labels are handed in. With nothing declared the review says
    nothing is identified, and it can never reach for another tenant's words.
"""
import pytest

from app.modules.commcalc import weekly_review as wr


# ── the item registry ─────────────────────────────────────────────────────────────────────────────
def test_every_review_item_is_declared_once_with_the_words_a_manager_reads():
    keys = [i["key"] for i in wr.ITEMS]
    assert len(keys) == len(set(keys)), "an item key is declared twice"
    for i in wr.ITEMS:
        assert i["label"] and i["source"], f"{i['key']} must say what it is and where it comes from"
        assert i["unit"] in ("money", "count", "pct"), i["key"]
        # WHAT THE FIGURE COVERS is declared, because a month-to-date figure on a weekly pack is
        # honest only when the pack says it is monthly.
        assert i["window"] in ("week", "period", "today"), i["key"]


def test_every_item_carries_its_window_onto_the_pack():
    out = wr.review(_scope(), facts={}, week=_WEEK)
    for item in out["items"]:
        assert item["window"] == wr.ITEM_BY_KEY[item["key"]]["window"]


def test_the_items_the_owner_named_are_on_the_review():
    keys = set(i["key"] for i in wr.ITEMS)
    for named in ("profit", "commission", "accessory", "growth", "rep_watch", "aging", "payroll"):
        assert named in keys, f"the owner's review item {named!r} is not declared"


# ── the week ──────────────────────────────────────────────────────────────────────────────────────
def test_the_review_week_is_the_last_completed_week_before_the_monday_meeting():
    # Monday 16 November 2026, weeks declared to end on Sunday: the week under review is the one
    # that just closed, 9–15 November — never the part-week the meeting sits in.
    w = wr.week_window("2026-11-16", week_ends_on=wr.SUNDAY)
    assert w["date_from"] == "2026-11-09"
    assert w["date_to"] == "2026-11-15"
    assert w["week_ending"] == "2026-11-15"
    assert "9" in w["label"] and "15" in w["label"]


def test_a_review_generated_on_the_closing_day_itself_covers_that_week():
    # Run on Saturday with Saturday declared as the end of the week: the week ending TODAY is
    # complete, so it is the one reviewed.
    w = wr.week_window("2026-11-14", week_ends_on=wr.SATURDAY)
    assert w["date_from"] == "2026-11-08" and w["date_to"] == "2026-11-14"


def test_the_closing_weekday_is_declared_not_assumed():
    assert wr.HOUSE_CONFIG["week_ends_on"] in range(7)
    # A value outside the week is refused rather than silently read as Sunday.
    with pytest.raises(wr.ConfigRefused):
        wr.week_window("2026-11-16", week_ends_on=9)


# ── nothing measured is never zero ────────────────────────────────────────────────────────────────
def test_an_item_with_no_facts_says_so_and_carries_no_figure():
    out = wr.review(_scope(), facts={}, week=_WEEK)
    by_key = {i["key"]: i for i in out["items"]}
    for key in ("profit", "commission", "accessory", "growth", "rep_watch", "aging", "payroll"):
        item = by_key[key]
        assert item["state"] == wr.NOT_MEASURED, key
        assert item["figure"] is None, f"{key} invented a figure out of nothing"
        assert item["reason"], f"{key} must say in words why it has no number"


def test_a_failed_read_is_reported_as_itself_and_does_not_fail_the_whole_review():
    out = wr.review(_scope(), facts={"profit": wr.Unreadable("the gross profit read timed out")},
                    week=_WEEK)
    item = {i["key"]: i for i in out["items"]}["profit"]
    assert item["state"] == wr.NOT_MEASURED and item["figure"] is None
    assert "timed out" in item["reason"]
    assert len(out["items"]) == len(wr.ITEMS), "one unreadable fact must not drop the other items"


# ── the money items ───────────────────────────────────────────────────────────────────────────────
def test_profit_is_folded_from_the_store_rows_the_gp_report_served():
    rows = [{"store_code": "A-1", "address": "1 First St", "total_rev": 1000.0, "net_profit": 250.0},
            {"store_code": "A-2", "address": "2 Second St", "total_rev": 500.0, "net_profit": -75.0}]
    out = wr.review(_scope(["A-1", "A-2"]), facts={"profit": rows}, week=_WEEK)
    item = {i["key"]: i for i in out["items"]}["profit"]
    assert item["state"] == wr.MEASURED
    assert item["figure"] == 175.0
    assert item["detail"]["revenue"] == 1500.0
    assert [r["store_code"] for r in item["rows"]] == ["A-1", "A-2"]
    assert [r["store_code"] for r in item["detail"]["losing"]] == ["A-2"], "a losing store is named"


def test_growth_needs_both_periods_and_states_the_direction():
    out = wr.review(_scope(), week=_WEEK,
                    facts={"growth": {"current": {"boxes": 110, "revenue": 2200.0},
                                      "prior": {"boxes": 100, "revenue": 2000.0}}})
    item = {i["key"]: i for i in out["items"]}["growth"]
    assert item["state"] == wr.MEASURED
    assert item["figure"] == 10.0, "boxes grew 10%"
    assert item["detail"]["revenue_change_pct"] == 10.0


def test_growth_with_no_prior_period_refuses_to_claim_a_direction():
    out = wr.review(_scope(), week=_WEEK,
                    facts={"growth": {"current": {"boxes": 110, "revenue": 2200.0}, "prior": None}})
    item = {i["key"]: i for i in out["items"]}["growth"]
    assert item["state"] == wr.NOT_MEASURED and item["figure"] is None
    assert "prior" in item["reason"].lower()


def test_the_accessory_gap_is_measured_against_the_targets_the_tenant_saved():
    rows = [{"store_code": "A-1", "target": 12000.0, "achieved": 9000.0},
            {"store_code": "A-2", "target": None, "achieved": 4000.0}]
    out = wr.review(_scope(["A-1", "A-2"]), facts={"accessory": rows}, week=_WEEK)
    item = {i["key"]: i for i in out["items"]}["accessory"]
    assert item["figure"] == -3000.0, "the gap is achieved minus target, over targeted stores only"
    # A store with no saved target is SHOWN and not counted — never scored against another store's
    # goal and never read as a store that hit its number.
    shown = {r["store_code"]: r for r in item["rows"]}
    assert shown["A-2"]["gap"] is None and shown["A-2"]["state"] == wr.NOT_MEASURED
    assert item["detail"]["stores_without_a_target"] == 1


# ── inventory aging, per carrier ──────────────────────────────────────────────────────────────────
#
# THE FOLD IS NOT HERE ANY MORE, and that is the fix (owner report 2026-11-17: *"the inventory aging
# is not correct it shows all devices out of the [carrier]'s day window"*). The review used to
# compose the aging report's `buckets` — the owed > 0 BILLING subset, which is almost entirely old
# stock — and report every device as past its window. The per-carrier fold over the WHOLE shelf has
# one home (`payables/unsold_inventory.aging_by_carrier`, proved by `harness_device_aging_policy.py`
# §I) and the review QUOTES it. What is pinned here is that the quote is honest.
def _aging_fact(**over):
    fact = {"by_carrier": [{"carrier_id": "c1", "label": "Total", "sell_within_days": 12,
                            "declared": True, "attribution": "declared", "devices": 2,
                            "under45": 0, "warn": 1, "missed": 1, "unknown_age": 0, "owed": 400.0},
                           {"carrier_id": "c2", "label": "Boost", "sell_within_days": 60,
                            "declared": False, "attribution": "source", "devices": 1,
                            "under45": 1, "warn": 0, "missed": 0, "unknown_age": 0, "owed": 50.0}],
            "devices": 3, "missed": 1, "owed": 450.0, "carriers": 2,
            "carriers_with_no_declared_window": 1, "devices_with_no_carrier": 0,
            "basis": "every unsold device handed in"}
    fact.update(over)
    return fact


def test_inventory_aging_quotes_the_whole_shelf_per_carrier_with_each_declared_window():
    out = wr.review(_scope(), week=_WEEK, facts={"aging": _aging_fact()})
    item = {i["key"]: i for i in out["items"]}["aging"]
    assert item["state"] == wr.MEASURED
    assert item["figure"] == 1, "one device is past its carrier's own window"
    # THE REGRESSION: the devices still INSIDE their window are reported, which is precisely what a
    # pack built from the owed>0 subset could never say.
    assert item["detail"]["devices"] == 3 and item["detail"]["within_window"] == 2
    by_carrier = {c["label"]: c for c in item["rows"]}
    assert by_carrier["Total"]["sell_within_days"] == 12
    assert by_carrier["Total"]["missed"] == 1 and by_carrier["Total"]["owed"] == 400.0
    # A carrier that declared no window is still reported, saying the house default answered.
    assert by_carrier["Boost"]["declared"] is False
    assert item["detail"]["carriers_with_no_declared_window"] == 1


def test_a_device_whose_carrier_is_not_declared_is_reported_not_silently_bucketed():
    out = wr.review(_scope(), week=_WEEK,
                    facts={"aging": _aging_fact(devices_with_no_carrier=1)})
    item = {i["key"]: i for i in out["items"]}["aging"]
    assert item["detail"]["devices_with_no_carrier"] == 1
    assert "carrier" in " ".join(item["narrative"]).lower()


def test_an_aging_answer_without_its_per_carrier_fold_is_not_measured_never_an_empty_shelf():
    item = {i["key"]: i for i in
            wr.review(_scope(), week=_WEEK, facts={"aging": {"devices": 9}})["items"]}["aging"]
    assert item["state"] == wr.NOT_MEASURED and item["figure"] is None


# ── scopes: one review per market, one consolidated, nothing dropped ──────────────────────────────
def _facts_for(codes):
    return {"profit": [{"store_code": c, "address": c, "total_rev": 100.0, "net_profit": 10.0}
                       for c in codes]}


def test_one_review_per_declared_market_plus_a_consolidated_company_review():
    scopes = wr.scopes(markets={"Chicago": ["A-1", "A-2"], "New York": ["B-1"]},
                       store_codes=["A-1", "A-2", "B-1"])
    kinds = [s["kind"] for s in scopes]
    assert kinds.count("company") == 1
    assert sorted(s["label"] for s in scopes if s["kind"] == "market") == ["Chicago", "New York"]
    company = [s for s in scopes if s["kind"] == "company"][0]
    assert sorted(company["store_codes"]) == ["A-1", "A-2", "B-1"]


def test_a_store_no_market_claims_is_reviewed_in_a_scope_of_its_own():
    scopes = wr.scopes(markets={"Chicago": ["A-1"]}, store_codes=["A-1", "ORPHAN"])
    unplaced = [s for s in scopes if s["kind"] == "unplaced"]
    assert unplaced and unplaced[0]["store_codes"] == ["ORPHAN"]
    assert "market" in unplaced[0]["note"].lower()
    company = [s for s in scopes if s["kind"] == "company"][0]
    assert "ORPHAN" in company["store_codes"], "the company total still counts every store"


def test_nothing_declared_means_nothing_identified_and_the_review_says_so():
    # No markets declared for this tenant: there is no market report to give, and the company
    # review still stands. A house market vocabulary must never appear here.
    scopes = wr.scopes(markets={}, store_codes=["A-1"])
    assert [s["kind"] for s in scopes] == ["company", "unplaced"]
    assert "no market" in scopes[-1]["note"].lower()


def test_the_market_vocabulary_is_a_required_argument():
    with pytest.raises(TypeError):
        wr.scopes(store_codes=["A-1"])          # type: ignore[call-arg]


def test_the_company_review_totals_what_the_market_reviews_total():
    scopes = wr.scopes(markets={"Chicago": ["A-1", "A-2"], "New York": ["B-1"]},
                       store_codes=["A-1", "A-2", "B-1"])
    pack = wr.build(scopes, facts=_facts_for(["A-1", "A-2", "B-1"]), week=_WEEK,
                    as_of="2026-11-16")
    def profit(review):
        return {i["key"]: i for i in review["items"]}["profit"]["figure"]
    assert profit(pack["company"]) == 30.0
    assert sum(profit(m) for m in pack["markets"]) == profit(pack["company"])
    assert pack["week"] == _WEEK and pack["as_of"] == "2026-11-16"


def test_every_review_carries_the_week_and_its_scope_so_a_pack_cannot_be_read_out_of_context():
    scopes = wr.scopes(markets={"Chicago": ["A-1"]}, store_codes=["A-1"])
    pack = wr.build(scopes, facts=_facts_for(["A-1"]), week=_WEEK, as_of="2026-11-16")
    for review in [pack["company"]] + list(pack["markets"]):
        assert review["scope"]["kind"] in ("company", "market", "unplaced")
        assert review["week"] == _WEEK
        assert len(review["items"]) == len(wr.ITEMS)


# ── the admin gate ────────────────────────────────────────────────────────────────────────────────
def test_only_an_admin_may_open_the_review():
    assert wr.review_allowed({"perms": {"modules": ["admin", "commissions"]}}) is True
    assert wr.review_allowed({"super_admin": True, "perms": {}}) is True
    assert wr.review_allowed({"perms": {"modules": ["commissions"]}}) is False
    assert wr.review_allowed({"perms": {"scope": "all"}}) is False, \
        "company-wide reporting scope is not the same as being an administrator"
    assert wr.review_allowed(None) is False, "an unresolvable caller is refused, never admitted"


_WEEK = {"date_from": "2026-11-09", "date_to": "2026-11-15", "week_ending": "2026-11-15",
         "label": "9–15 November 2026"}


def _scope(codes=("A-1",)):
    return {"kind": "company", "label": "Company", "store_codes": list(codes), "note": ""}
