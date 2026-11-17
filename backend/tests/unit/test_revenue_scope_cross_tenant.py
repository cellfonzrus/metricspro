"""Does one tenant's revenue-scope declaration ever reach another tenant's day?

The 2026-10-30 owner directive requires the cross-tenant tests to SHIP WITH the feature: *"all
features should be for all tenanats and the testing agent shoudl test it after it is made"*. The
defect that directive was written about was a report that read one tenant's answer with another
tenant's vocabulary behind it, and this is the same shape one layer up — a POLICY this time rather
than a vocabulary, so the two things to prove are:

  1. EVERY tenant gets the feature, measured against its OWN declaration and its OWN feed shape.
  2. NO tenant's declaration changes another's figure, and the house default changes nobody's.

`backend/harness_revenue_scope.py` proves the mechanism. This file proves the ISOLATION, which a
single-tenant fixture cannot: it runs two tenants with different categories, different unit prices
and opposite declarations through the same engine in the same process.
"""
from app.modules.commcalc import revenue_scope as rs
from app.modules.commcalc import metric_authority as ma
from app.modules.commcalc import pos_report_feed as feed
from app.modules.commcalc import daily_incentive as di
from app.modules.closing import metric_catalog as mc

# Two tenants that share no category, no unit price and no operator. Neither vocabulary is spelled
# in any module under test.
ORG_A_DEFS = [{"field_key": "printing", "label": "Printing", "value_kind": "amount",
               "declared_by": "employee", "in_revenue": True}]
ORG_B_DEFS = [{"field_key": "activations", "label": "Activations", "value_kind": "count",
               "declared_by": "employee", "unit_price": 25.0, "in_revenue": True}]
CAT_A, CAT_B = mc.build(ORG_A_DEFS), mc.build(ORG_B_DEFS)

LINES_A = [{"metric_key": "printing", "units": 0, "amount": 100.0, "employee_name": "ANA"}]
LINES_B = [{"metric_key": "activations", "units": 4, "amount": 0.0, "employee_name": "BEN"}]

def _rows(store, metric, basis, amount, who):
    return {"business_date": "2026-10-20", "store_code": store, "employee_name": who,
            "metric_key": metric, "metric_basis": basis, "units": 1, "amount": amount}

FEED_A = [_rows("A1", "printing", feed.BASIS_LINE_MAP, 100.0, "ANA"),
          _rows("A1", feed.UNCLASSIFIED_KEY, feed.BASIS_UNMATCHED, 900.0, "ANA")]
FEED_B = [_rows("B1", "activations", feed.BASIS_LINE_MAP, 0.0, "BEN"),
          _rows("B1", feed.UNCLASSIFIED_KEY, feed.BASIS_UNMATCHED, 700.0, "BEN")]

POOL = [{"label": "Day pool", "rule_kind": "threshold_pool", "is_active": True,
         "qualifier": {"scope": "store", "basis": "revenue", "op": "gte", "threshold": 500.0},
         "payout": {"rate": 0.01, "basis": "amount", "metric_keys": [],
                    "split": "individual_sales"}}]


def _day(catalog, lines, feed_rows, scope):
    """One store-day for one tenant, exactly as the endpoint assembles it."""
    u = ma.unclassified_from_feed(feed_rows, business_date="2026-10-20")
    add = rs.unclassified_addend(scope, u["amount"])
    by_emp = {k: rs.unclassified_addend(scope, v) for k, v in u["by_employee"].items()} if add else {}
    declared = mc.declared(catalog, None, lines)
    per_emp = mc.by_employee(catalog, lines)
    return di.compute_day(catalog, POOL, declared_map=declared, per_employee=per_emp,
                          employees=sorted(set(list(per_emp) + list(by_emp))),
                          unclassified=add, unclassified_by_employee=by_emp)


# ── 1. every tenant gets the feature, on its own terms ───────────────────────────────────────────

def test_each_tenant_gets_the_feature_measured_on_its_own_declarations():
    """Opposite shapes — one tenant declares in dollars, the other in counts at its own unit price —
    and each one's whole day is its own placed revenue plus its own unclassified money."""
    a = _day(CAT_A, LINES_A, FEED_A, rs.SCOPE_WHOLE_DAY)
    b = _day(CAT_B, LINES_B, FEED_B, rs.SCOPE_WHOLE_DAY)
    assert a["store_revenue"] == 1000.0      # $100 printing + $900 unclassified
    assert b["store_revenue"] == 800.0       # 4 activations x $25 + $700 unclassified
    assert a["unclassified_revenue"] == 900.0
    assert b["unclassified_revenue"] == 700.0


def test_the_gate_opens_for_each_tenant_on_its_own_day():
    """Neither tenant's placed revenue clears $500; each one's whole day does. The feature is not
    one tenant's — it is the mechanism's."""
    for cat, lines, rows in ((CAT_A, LINES_A, FEED_A), (CAT_B, LINES_B, FEED_B)):
        assert _day(cat, lines, rows, rs.SCOPE_CLASSIFIED_ONLY)["total"] == 0.0
        assert _day(cat, lines, rows, rs.SCOPE_WHOLE_DAY)["total"] > 0.0


# ── 2. nothing leaks, and the default moves nobody ───────────────────────────────────────────────

def test_one_tenants_declaration_never_reaches_the_other():
    """A declares whole_day, B declares nothing. B's figures must be byte-identical to B's figures
    computed with no feature at all — the isolation a single-tenant fixture cannot prove."""
    a_whole = _day(CAT_A, LINES_A, FEED_A, rs.SCOPE_WHOLE_DAY)
    b_default = _day(CAT_B, LINES_B, FEED_B, rs.resolve(""))
    b_untouched = di.compute_day(CAT_B, POOL, declared_map=mc.declared(CAT_B, None, LINES_B),
                                 per_employee=mc.by_employee(CAT_B, LINES_B))
    assert a_whole["store_revenue"] == 1000.0
    assert b_default["store_revenue"] == b_untouched["store_revenue"] == 100.0
    assert b_default["total"] == b_untouched["total"] == 0.0
    assert b_default["by_employee"] == b_untouched["by_employee"]


def test_the_house_default_is_todays_behaviour_for_every_tenant():
    """An org that has declared nothing — which is every org the day this lands — reads exactly the
    number it read before. This is what makes the migration not money-touching as applied."""
    for cat, lines, rows in ((CAT_A, LINES_A, FEED_A), (CAT_B, LINES_B, FEED_B)):
        before = di.compute_day(cat, POOL, declared_map=mc.declared(cat, None, lines),
                                per_employee=mc.by_employee(cat, lines))
        after = _day(cat, lines, rows, rs.resolve(None))
        assert after["store_revenue"] == before["store_revenue"]
        assert after["total"] == before["total"]
        assert after["by_employee"] == before["by_employee"]


def test_one_tenants_unclassified_money_is_never_read_into_the_others_day():
    """The rows are read per org by the caller, but prove the reader itself is keyed on the store-day
    rather than pooling whatever it is handed: A's and B's rows together must still answer separately."""
    both = FEED_A + FEED_B
    by_day = feed.unclassified_by_store_day(both)
    assert by_day[("A1", "2026-10-20")]["amount"] == 900.0
    assert by_day[("B1", "2026-10-20")]["amount"] == 700.0
    assert by_day[("A1", "2026-10-20")]["by_employee"] == {"ANA": 900.0}
    assert by_day[("B1", "2026-10-20")]["by_employee"] == {"BEN": 700.0}


def test_a_tenants_unclassified_money_can_never_pay_another_tenants_category():
    """The load-bearing exclusion, per tenant: no unclassified row becomes a category line for anybody."""
    for rows in (FEED_A, FEED_B, FEED_A + FEED_B):
        assert all(str(l["metric_key"]).strip() for l in ma.lines_from_feed(rows))


def test_a_declared_scope_is_refused_rather_than_leaking_a_default():
    """A tenant whose stored scope is mistyped must not silently inherit the house answer — that is
    how one tenant's policy becomes another's."""
    import pytest
    for bad in ("whole day", "WHOLEDAY", "placed", "all"):
        with pytest.raises(ValueError):
            rs.resolve(bad)
