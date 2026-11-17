"""A tenant's own export answers its own declared categories — for EVERY tenant.

Owner, 2026-11-17 (NY Logistics / a UPS Store, whose POS is nothing like the wireless tenants'):
*"daily numbers shouold come from the daily sales report i just uploaded, make a way to upload this
report"*, *"each transaction is qty 1"*, *"the nda is nexday air, 2 day i 2 day air"*, *"let the feed
be the authority till all reps are fully trained to enter daily data"*.

THE CLASS UNDER TEST, stated so nobody re-derives it. `commcalc/daily_incentive.py` pays off what a
store DECLARED at closing. Three facts about a tenant's own export had no home, so no export could
answer a declared category:

  1. HOW MANY. A line-level export has no Quantity column; `pos_report_feed._units` reads an absent
     cell as 0, so every shipment counted as nothing and a "5 shipments in a day" gate could never
     pass. One row IS one unit — when the tenant SAYS so.
  2. WHICH COLUMN. `report_columns.merge_synonyms` promises that a tenant's declared words "outrank
     the house defaults", and `map_headers` then sorts every synonym by LENGTH, throwing that order
     away. An export carrying both `Category` ("UPS Domestic") and `Product name` ("NDA", "2DA") is
     read on `Category`, so overnight and 2nd-day parcels become one indistinguishable line — and
     declaring the synonym does not fix it. A promise kept in one function and lost in the next.
  3. WHICH SIGN. This export signs a SALE negative and a REFUND positive. Read as-is, every
     percent-of-amount rule pays a negative number.

And the authority itself: which of the two sources answers a store-day is a DECLARATION, never a
default — `commcalc/metric_authority.py`.

NO TENANT'S VOCABULARY IS IN THIS FILE'S ASSERTIONS AS A CODE EXPECTATION. "NDA", "UPS Domestic" and
"Mailbox" appear only as CONFIG a fixture declares, which is the whole point: the tests assert that
each tenant's own shape produces its own answer, and §F asserts that one tenant's declaration can
never be reached from another tenant's read.
"""
import pytest

from app.modules.closing import metric_catalog as mc
from app.modules.commcalc import commission_ledger as cl
from app.modules.commcalc import daily_incentive as di
from app.modules.commcalc import metric_authority as ma
from app.modules.commcalc import pos_report_feed as feed
from app.modules.commcalc import report_columns as rc


# ── fixtures: two tenants, two completely different export shapes ────────────────────────────────

def _cat(*entries):
    """A declared-metric catalog in `closing/metric_catalog`'s own shape."""
    return [
        {"key": k, "label": label, "value_kind": vk, "declared_by": db,
         "in_revenue": True, "unit_price": None}
        for (k, label, vk, db) in entries
    ]


#: Tenant A — a parcel/print shop. Its export is one row per sale LINE: no quantity column, a
#: category column AND a product-name column, and a sale written NEGATIVE.
#: NOTE the `both` value kind on the counted categories. A category declared `count` with no unit
#: price EARNS NOTHING (`metric_catalog._earned`), so a store-day's gross revenue would hold only the
#: `amount` categories and a "the store must take $X today" gate could never be measured. `both` is
#: what lets a parcel be BOTH one unit and its own money. That is a config row, not code — and it is
#: the configuration this feature requires, which is why the fixture models it.
CAT_A = _cat(("overnight", "Next Day Air", "both", "employee"),
             ("two_day", "2 Day Air", "both", "employee"),
             ("printing", "Printing", "amount", "employee"),
             ("mailbox_year", "Mailbox 1 Year", "both", "employee"))

#: Tenant A's declarations. Every tenant word lives HERE, in config, never in the module.
CAL_A = {
    "column_bindings": {"line_label": "Product name", "employee": "Operator ID",
                        "business_date": "Date", "amount": "Amount before tax"},
    "unit_basis": "row",
    "amount_sign": cl.SIGN_PAYOUT_NEGATIVE,
    "line_map": {"NDA": "overnight", "NDA Early AM": "overnight", "NDA Saver": "overnight",
                 "2DA": "two_day", "2DA AM": "two_day",
                 "Online Printing Paper": "printing",
                 "Large Mailbox (NR)": "mailbox_year"},
}

TABLE_A = [
    ["Category", "Operator ID", "Product name", "Date", "Amount before tax"],
    ["UPS Domestic", "295533", "NDA",                  "2026-10-21", "-38.40"],
    ["UPS Domestic", "295533", "NDA Early AM",         "2026-10-21", "-51.25"],
    ["UPS Domestic", "407152", "2DA",                  "2026-10-21", "-22.10"],
    ["Print",        "407152", "Online Printing Paper", "2026-10-21", "-160.00"],
    ["Print",        "407152", "Online Printing Paper", "2026-10-21", "-10.00"],
    ["Standard",     "295533", "Large Mailbox (NR)",   "2026-10-21", "-300.00"],
]

#: Tenant B — a wireless store on a portal whose export is already aggregated: it HAS a quantity
#: column, its category column is the one to read, and a sale is POSITIVE. It declares nothing.
CAT_B = _cat(("postpaid_count", "Activations", "count", "employee"),
             ("accessories", "Accessories", "amount", "employee"))

TABLE_B = [
    ["Date", "Salesperson", "Category", "Quantity", "Net Sales"],
    ["2026-10-21", "Ana", "Activations", "4", "0"],
    ["2026-10-21", "Ana", "Accessories", "7", "212.50"],
]


def _norm(table, catalog, calibration, **kw):
    return feed.normalize(table, catalog=catalog, calibration=calibration,
                          line_map=(calibration or {}).get("line_map"), **kw)


def _by_metric(rows):
    out = {}
    for r in rows:
        e = out.setdefault(r["metric_key"], {"units": 0, "amount": 0.0})
        e["units"] += r["units"]
        e["amount"] = round(e["amount"] + r["amount"], 2)
    return out


# ═════ §A — one row is one unit, when the tenant says so ═════════════════════════════════════════

def test_a1_absent_quantity_column_still_counts_when_the_row_is_the_unit():
    """THE DEFECT: no Quantity column meant zero units, so a units gate could never pass."""
    got = _by_metric(_norm(TABLE_A, CAT_A, CAL_A)["rows"])
    assert got["overnight"]["units"] == 2, "two overnight lines are two overnight parcels"
    assert got["two_day"]["units"] == 1
    assert got["mailbox_year"]["units"] == 1


def test_a2_the_unit_basis_is_declared_and_its_absence_changes_nothing():
    """Tenant B declares nothing, so it reads EXACTLY as it does today: its own Quantity column."""
    got = _by_metric(_norm(TABLE_B, CAT_B, {})["rows"])
    assert got["postpaid_count"]["units"] == 4, "the export's own quantity, not a row count"
    assert got["accessories"]["units"] == 7


def test_a3_a_row_basis_never_overrides_a_quantity_the_export_states():
    """Declaring `row` on an export that DOES carry quantities must not silently halve them."""
    got = _by_metric(_norm(TABLE_B, CAT_B, {"unit_basis": feed.UNIT_BASIS_ROW})["rows"])
    assert got["postpaid_count"]["units"] == 4


def test_a4_an_unknown_unit_basis_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        feed.unit_basis_named("per_carton")


# ═════ §B — a declared column binding actually wins ══════════════════════════════════════════════

def test_b1_the_house_default_claims_the_wrong_column_without_a_binding():
    """Documents the mechanism this fix exists for: `Category` would answer, and 'UPS Domestic'
    cannot tell an overnight parcel from a 2-day one."""
    idx = rc.map_headers(TABLE_A[0], feed.COLUMN_SYNONYMS)
    assert TABLE_A[0][idx["line_label"]] == "Category"


def test_b2_a_binding_outranks_every_synonym_however_long():
    idx = rc.map_headers(TABLE_A[0], feed.COLUMN_SYNONYMS,
                         bindings=CAL_A["column_bindings"])
    assert TABLE_A[0][idx["line_label"]] == "Product name"
    assert TABLE_A[0][idx["employee"]] == "Operator ID"


def test_b3_a_binding_that_names_a_column_the_export_does_not_have_identifies_nothing():
    """Nothing declared means nothing identified (owner 2026-10-30). A binding that misses must NOT
    fall back to the house default — that silently returns the very column the tenant rejected."""
    idx = rc.map_headers(TABLE_A[0], feed.COLUMN_SYNONYMS,
                         bindings={"line_label": "SKU Description"})
    assert "line_label" not in idx
    assert rc.binding_misses(TABLE_A[0], {"line_label": "SKU Description"}) == ["line_label"]


def test_b4_a_missed_binding_is_reported_in_words_not_as_an_empty_report():
    out = _norm(TABLE_A, CAT_A, dict(CAL_A, column_bindings={"line_label": "SKU Description"}))
    assert out["rows"] == []
    assert any("SKU Description" in w for w in out["warnings"]), out["warnings"]


def test_b5_binding_nothing_leaves_every_existing_tenant_byte_identical():
    assert (rc.map_headers(TABLE_B[0], feed.COLUMN_SYNONYMS)
            == rc.map_headers(TABLE_B[0], feed.COLUMN_SYNONYMS, bindings=None)
            == rc.map_headers(TABLE_B[0], feed.COLUMN_SYNONYMS, bindings={}))


# ═════ §C — which sign is a sale is declared, and dereferenced ═══════════════════════════════════

def test_c1_a_negative_signed_export_reads_as_positive_sales():
    got = _by_metric(_norm(TABLE_A, CAT_A, CAL_A)["rows"])
    assert got["printing"]["amount"] == 170.00, "160 + 10 of printing, as money earned"


def test_c2_the_opposite_sign_is_a_refund_that_nets_off():
    table = TABLE_A + [["Print", "407152", "Online Printing Paper", "2026-10-21", "42.50"]]
    got = _by_metric(_norm(table, CAT_A, CAL_A)["rows"])
    assert got["printing"]["amount"] == 127.50, "the credit nets off, it is not a second sale"


def test_c3_the_sign_rule_is_the_platforms_one_home_not_a_second_copy():
    """`commission_ledger` already owns 'which sign is money earned' (mig 1006). A second rule here
    would be the duplicate defect the house rules forbid."""
    assert set(feed.AMOUNT_SIGNS) == set(cl.SIGN_CONVENTIONS)
    assert feed.AMOUNT_SIGN_DEFAULT == cl.SIGN_PAYOUT_POSITIVE, (
        "today's behaviour is amount-as-written; the default may never move a live tenant")


def test_c4_declaring_nothing_leaves_an_existing_tenants_money_unmoved():
    got = _by_metric(_norm(TABLE_B, CAT_B, {})["rows"])
    assert got["accessories"]["amount"] == 212.50


# ═════ §D — which source answers a store-day is DECLARED ═════════════════════════════════════════

PORTAL_ROWS = [
    {"business_date": "2026-10-21", "metric_key": "overnight", "metric_basis": feed.BASIS_LINE_MAP,
     "employee_name": "295533", "units": 2, "amount": 0.0},
    {"business_date": "2026-10-21", "metric_key": "printing", "metric_basis": feed.BASIS_LABEL,
     "employee_name": "407152", "units": 2, "amount": 170.0},
    {"business_date": "2026-10-21", "metric_key": "two_day", "metric_basis": feed.BASIS_LABEL_PART,
     "employee_name": "407152", "units": 9, "amount": 0.0},
]


def test_d1_absent_declaration_is_todays_behaviour_for_every_existing_tenant():
    assert ma.resolve(None) == ma.SOURCE_DECLARATION
    assert ma.resolve("") == ma.SOURCE_DECLARATION


def test_d2_an_unknown_authority_is_refused_never_guessed():
    with pytest.raises(ValueError):
        ma.resolve("whatever_the_portal_said")


def test_d3_the_feed_answers_in_the_shape_the_catalog_reader_already_takes():
    lines = ma.lines_from_feed(PORTAL_ROWS)
    assert {l["metric_key"] for l in lines} == {"overnight", "printing"}
    folded = mc.declared(CAT_A, None, lines)
    assert folded["overnight"]["units"] == 2
    assert folded["printing"]["amount"] == 170.0
    assert folded["printing"]["declared"] is True


def test_d4_a_text_coincidence_never_pays():
    """§66's rule, in the place it decides money: only a basis the tenant DECLARED is evidence. A
    `label_part` containment match is a guess, and a guess may not become a payment."""
    lines = ma.lines_from_feed(PORTAL_ROWS)
    assert all(l["metric_key"] != "two_day" for l in lines), (
        "a one-and-only-one containment match is still a coincidence, not a declaration")


def test_d5_the_answer_always_says_which_source_gave_it():
    assert ma.SOURCE_FEED in ma.source_sentence(ma.SOURCE_FEED, rows=3)
    assert "nobody declared" not in ma.source_sentence(ma.SOURCE_FEED, rows=3).lower()
    assert ma.source_sentence(ma.SOURCE_FEED, rows=0)


def test_d6_every_source_is_named_in_the_vocabulary():
    assert ma.SOURCES == (ma.SOURCE_DECLARATION, ma.SOURCE_FEED)
    for s in ma.SOURCES:
        assert ma.SOURCE_LABELS[s]


# ═════ §E — the whole way through: a day's export pays the plan ══════════════════════════════════

#: Tenant A's rules, as CONFIG — the shapes `daily_incentive` already serves.
RULES_A = [
    {"id": "r1", "rule_key": "store_pool", "rule_kind": "threshold_pool",
     "qualifier": {"scope": "store", "threshold": 400},
     "payout": {"rate": 0.01, "basis": "revenue", "split": "individual_sales", "metric_keys": []}},
    {"id": "r2", "rule_key": "overnight", "rule_kind": "per_unit",
     "qualifier": {"scope": "store", "basis": "units", "threshold": 3,
                   "metric_keys": ["overnight", "two_day"]},
     "payout": {"rate": 5.0, "basis": "units", "metric_keys": ["overnight"]}},
]


def _day_from_feed(table, catalog, calibration, rules):
    rows = _norm(table, catalog, calibration)["rows"]
    lines = ma.lines_from_feed(rows)
    declared = mc.declared(catalog, None, lines)
    per_emp = mc.by_employee(catalog, lines)
    return di.compute_day(catalog, rules, declared_map=declared, per_employee=per_emp,
                          employees=sorted(per_emp))


def test_e1_a_days_export_pays_the_plans_rules():
    r = _day_from_feed(TABLE_A, CAT_A, CAL_A, RULES_A)
    assert r["total"] > 0, "a day the store worked may not pay zero merely because nobody typed it in"


def test_e2_only_people_who_contributed_share_the_pool():
    """Owner 2026-11-17: *"the pay will only go if the person contributes to the $1300"*."""
    r = _day_from_feed(TABLE_A, CAT_A, CAL_A, [RULES_A[0]])
    paid = {k for k, v in (r.get("by_employee") or {}).items() if v}
    assert paid and all(p in {"295533", "407152"} for p in paid)


def test_e3_a_gate_the_day_misses_pays_nothing_and_says_why():
    lean = [TABLE_A[0], TABLE_A[1]]
    r = _day_from_feed(lean, CAT_A, CAL_A, [RULES_A[1]])
    assert r["total"] == 0.0
    assert any(not (x.get("qualifier") or {}).get("passed") for x in (r.get("rules") or []))


# ═════ §F — EVERY tenant, and no leakage between them ════════════════════════════════════════════

def test_f1_each_tenants_own_shape_produces_its_own_answer():
    a = _by_metric(_norm(TABLE_A, CAT_A, CAL_A)["rows"])
    b = _by_metric(_norm(TABLE_B, CAT_B, {})["rows"])
    assert a["overnight"]["units"] == 2 and b["postpaid_count"]["units"] == 4


def test_f2_one_tenants_declaration_is_never_reachable_from_anothers_read():
    """The 2026-10-30 class: a default that is really one tenant's vocabulary. Reading tenant B with
    NO calibration must be unaffected by tenant A having been read first."""
    _norm(TABLE_A, CAT_A, CAL_A)
    got = _by_metric(_norm(TABLE_B, CAT_B, {})["rows"])
    assert got["postpaid_count"]["units"] == 4 and got["accessories"]["amount"] == 212.50
    assert "column_bindings" not in feed.COLUMN_SYNONYMS


def test_f3_the_house_synonym_table_is_never_mutated_by_a_tenants_declaration():
    before = {f: tuple(w) for f, w in feed.COLUMN_SYNONYMS.items()}
    _norm(TABLE_A, CAT_A, dict(CAL_A, column_synonyms={"line_label": ("widget",)}))
    assert {f: tuple(w) for f, w in feed.COLUMN_SYNONYMS.items()} == before


def test_f4_a_tenant_that_declares_a_category_the_catalog_lacks_is_told_not_guessed():
    out = _norm(TABLE_A, CAT_A, dict(CAL_A, line_map=dict(CAL_A["line_map"], NDA="no_such_key")))
    assert out["stale_map"], "a mapping to a category nobody declares is reported"


def _executable_text(mod):
    """A module's source with every docstring and comment removed. RULE TWO is about what the CODE
    says, not about prose quoting the owner — `harness_line_class` takes the same posture."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)
    return ast.unparse(tree).lower()


def test_f5_no_tenant_word_is_spelled_in_the_modules_under_test():
    """RULE TWO, asserted over the executable text — the same posture `harness_line_class` takes."""
    for mod in (feed, ma, rc, di):
        text = _executable_text(mod)
        for word in ("nda", "2da", "ups domestic", "mailbox", "luxelink", "cellfonz", "boost"):
            assert word not in text, f"{mod.__name__} spells {word!r}"


def test_f6_a_payout_that_measures_nothing_says_so_rather_than_paying_a_confident_zero():
    """A rule whose payout names UNITS but NO category measures 0.00 and reads as "we looked and the
    store did nothing". An absence needs the right reason, so the trail must name it.

    NARROWED 2026-11-17 (§COMMCALC-78), deliberately. This case used to be asserted with a MONEY
    basis and no category, and that shape no longer belongs here: dollars are dollars, so a payout
    naming no category now measures the whole day's revenue — which is what the rules editor has
    always told the admin it does, and what `payout_measure_basis` is the one home for. UNITS have
    no such fallback (there is no "day's units"), so that is the case this test keeps, and the
    companion below pins the half that changed so neither can drift back."""
    mute = [dict(RULES_A[0], payout=dict(RULES_A[0]["payout"], basis="units", metric_keys=[]))]
    r = _day_from_feed(TABLE_A, CAT_A, CAL_A, mute)
    trail = (r.get("rules") or [])
    assert trail and trail[0].get("payout_measurable") is False
    assert trail[0].get("payout_note")


def test_f6b_a_money_payout_naming_no_category_is_the_whole_day_not_a_silent_zero():
    """The other half of f6, and the defect found against this tenant's own live rule row.

    The rules editor promises, in words, that a payout with no categories ticked is a percentage of
    the whole day. It stored basis 'amount', which SUMS the named categories — so naming none summed
    nothing and the rule paid $0.00 for a store that had traded all day. Asserting it both ways here
    means neither the promise nor the engine can move without the other."""
    whole = [dict(RULES_A[0], payout=dict(RULES_A[0]["payout"], basis="amount", metric_keys=[]))]
    as_rev = [dict(RULES_A[0], payout=dict(RULES_A[0]["payout"], basis="revenue", metric_keys=[]))]
    r_whole = _day_from_feed(TABLE_A, CAT_A, CAL_A, whole)
    r_rev = _day_from_feed(TABLE_A, CAT_A, CAL_A, as_rev)
    assert r_whole["total"] == r_rev["total"], "the two spellings must be one answer"
    assert r_whole["total"] > 0, "the shape the editor actually writes has to pay"
    assert (r_whole.get("rules") or [])[0].get("payout_measurable") is True
    assert di.payout_measure_basis("amount", []) == "revenue"
    assert di.payout_measure_basis("units", []) == "units"
