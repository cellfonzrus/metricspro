"""WHICH STORE-DLAR COLUMN IS WHICH METRIC — the one home, proved (index §COMMCALC-58).

WHY THESE TESTS EXIST. On 2026-11-10 the owner asked for the 21 columns Elevate Go sends at store
grain that nothing reads to become part of KPI reporting. The obstacle was not the columns: it was
that "which DLAR column means what" had THREE homes —

  1. `dlar_sweep.STORE_FIELDS`            (which portal field feeds which of our columns)
  2. `kpi_failing.STORE_KPI_COLUMNS`      (which of our columns carries a scored KPI)
  3. a hand-typed `KPIS` array inside `frontend/.../commcalc/kpi/page.tsx`, plus the same column
     list written out twice as a PostgREST `select(...)` literal in `commcalc/router.py`

— so adding a column meant editing four places and the first one anybody forgot would read as a
store that sold nothing. That is the patchwork CLAUDE.md's "one fact, one home, dereferenced" rule
exists to stop, and the tests below are what makes the single home un-droppable:

  * the catalog is DERIVED from (1) and (2) and never copies them, so a column declared in the
    sweep cannot be missing from the report;
  * every displayable column carries a label and a group, because a number nobody can name is not
    reporting;
  * a metric's SCORED-ness comes from `kpi_failing`, so the KPI page and the pay engine can never
    disagree about which seven are scored;
  * RULE TWO: no carrier, tenant or product name appears in the module. Only the SOURCE's own field
    spellings, which is what a feed mapping is made of.
"""
import re

from app.modules.commcalc import dlar_metric_catalog as cat
from app.modules.commcalc import dlar_sweep as ds
from app.modules.commcalc import kpi_failing as kf


# The 21 fields the live sweep reported as arriving-but-unread on 2026-11-10 (28 stores, run
# 12:00 UTC, `core.job_run.detail.evidence.fields.dlar.unconsumed`). This list is EVIDENCE, copied
# from that run — it is the reason this change exists, so it is pinned rather than paraphrased.
ARRIVING_UNREAD_2026_11_10 = (
    "activations_total", "auxiliary_device_acts", "avg_platinum_points", "byod_total",
    "byod_unlocked_activations", "mom_percent_change", "non_selling_days", "offer_trade_ins",
    "projection", "protection_dop", "protection_upgrades", "rpl_count", "starlink_activations",
    "starlink_sales", "swaps_total", "total_health_checks", "total_trade_ins",
    "trade_in_unlocked_activations", "upgrade_value_trade_ins", "value_trade_ins",
    "yoy_percent_change",
)


def _declared_source_spellings():
    """Every source field spelling the STORE mapping reads, flattened. Derived from the sweep."""
    return {k for row in ds.STORE_FIELDS for k in row[2:]}


# ── THE POINT OF THE CHANGE: all 21 are read now ────────────────────────────────────────────────

def test_every_arriving_field_is_now_declared():
    """Each of the 21 fields the portal sends is read by the store mapping. The whole ask."""
    declared = _declared_source_spellings()
    missing = [f for f in ARRIVING_UNREAD_2026_11_10 if f not in declared]
    assert not missing, f"still unread: {missing}"


def test_non_selling_days_is_the_new_spelling_of_the_column_we_already_had():
    """`zero_selling_days` stopped arriving under that name. It is the SAME column, so it gets the
    new spelling beside the old one — not a second column that would split one fact in two."""
    row = [r for r in ds.STORE_FIELDS if r[0] == "zero_selling_days"]
    assert len(row) == 1, "zero_selling_days must stay ONE column"
    spellings = row[0][2:]
    assert "non_selling_days" in spellings and "zero_selling_days" in spellings
    # The old spelling first would make a historical re-pull prefer a field that no longer arrives;
    # either order is CORRECT (`_pick` takes the first that CARRIES a value) but the new one leads.
    assert spellings[0] == "non_selling_days"
    assert not any(r[0] == "non_selling_days" for r in ds.STORE_FIELDS), \
        "a second column for the same fact is the divergence this rule forbids"


def test_activations_total_lands_under_its_own_name_not_as_a_guess_at_gross_adds():
    """OWNER DECISION PENDING 2026-11-10: whether the portal's `activations_total` means what we
    called gross adds. Until he answers, it lands as its OWN column and `gross_adds` keeps asking
    for the field it was declared with. Missing beats wrong on a figure reps are scored against
    (§19.26) — a guess here would restate every store's KPI row."""
    assert any(r[0] == "activations_total" for r in ds.STORE_FIELDS)
    gross = [r for r in ds.STORE_FIELDS if r[0] == "gross_adds"][0]
    assert "activations_total" not in gross[2:]


# ── THE CATALOG IS DERIVED, NOT COPIED ──────────────────────────────────────────────────────────

def test_catalog_covers_every_measurable_declared_column():
    """A column the sweep lands is a column the report can show. Derived from STORE_FIELDS, so a
    future column is in the catalog the moment it is declared — nobody has to remember a second
    list. TEXT columns (store identity) are not metrics and are excluded by KIND, not by name."""
    measurable = {r[0] for r in ds.STORE_FIELDS if r[1] in (ds.COUNT, ds.RATE)}
    keys = {m["key"] for m in cat.catalog()}
    assert measurable <= keys, f"declared but not in the catalog: {sorted(measurable - keys)}"


def test_catalog_includes_the_derived_total_and_says_it_is_derived():
    """`total_acts` is not a portal field — the sweep sums two of them. It is still a column the
    report shows, so it is in the catalog, marked derived so nothing hunts for its source field."""
    entry = [m for m in cat.catalog() if m["key"] == "total_acts"]
    assert entry, "total_acts is a stored column the KPI page already shows"
    assert entry[0]["derived"] is True
    assert entry[0]["source"] == ()


def test_every_entry_has_a_label_and_a_known_group():
    for m in cat.catalog():
        assert m["label"].strip(), f"{m['key']} has no label"
        assert m["group"] in cat.GROUPS, f"{m['key']} has unknown group {m['group']!r}"
        assert m["kind"] in (ds.COUNT, ds.RATE)


def test_labels_are_unique_so_two_columns_cannot_read_as_one_metric():
    labels = [m["label"] for m in cat.catalog()]
    assert len(labels) == len(set(labels)), "duplicate label: two columns would look like one"


def test_scored_comes_from_kpi_failing_and_is_not_a_second_copy():
    """Which metrics are SCORED is the pay engine's fact. The catalog dereferences it, so the KPI
    page's columns and the paid score can never name a different seven."""
    scored = {m["key"]: m["scored"] for m in cat.catalog() if m["scored"]}
    assert scored == {col: k for k, col in kf.STORE_KPI_COLUMNS.items()}
    src = open(cat.__file__).read()
    for literal in ("atu_pct", '"protect_pct"', '"family_plan_pct"', '"aal_conversion"'):
        assert f"STORE_KPI_COLUMNS" in src, "the scored set must be dereferenced"


def test_source_spellings_are_the_declaration_not_a_restatement():
    """Each entry's `source` is exactly what the sweep declares for that column."""
    declared = {r[0]: tuple(r[2:]) for r in ds.STORE_FIELDS}
    for m in cat.catalog():
        if not m["derived"]:
            assert m["source"] == declared[m["key"]]


# ── THE READ SIDE: one spelling of the column list, for every caller ────────────────────────────

def test_select_columns_is_one_list_with_identity_first_and_no_duplicates():
    cols = cat.select_columns()
    assert cols[:3] == ("location", "address", "store_code"), "store identity leads the read"
    assert len(cols) == len(set(cols)), "a duplicated column makes PostgREST 400"
    assert set(cat.REQUIRED_COLUMNS) <= set(cols)
    for m in cat.catalog():
        assert m["key"] in cols


def test_optional_columns_are_exactly_the_ones_a_migration_has_yet_to_add():
    """The new columns arrive by hand-applied migration (1116), so they are PROBED, never assumed.
    `REQUIRED_COLUMNS` are the ones migration 002 already created — a read of those can never 400."""
    assert set(cat.OPTIONAL_COLUMNS).isdisjoint(cat.REQUIRED_COLUMNS)
    assert set(cat.OPTIONAL_COLUMNS) | set(cat.REQUIRED_COLUMNS) == set(cat.select_columns())
    # every brand-new column is optional; nothing pre-existing became optional
    for key in ("activations_total", "total_trade_ins", "starlink_activations", "yoy_percent_change"):
        assert key in cat.OPTIONAL_COLUMNS
    for key in ("atu", "protect_pct", "total_acts", "zero_selling_days"):
        assert key in cat.REQUIRED_COLUMNS


def test_filter_keys_are_what_a_kpi_picker_may_offer():
    """"Filtered by the KPI" (owner 2026-11-10): the picker's options ARE the catalog, so it can
    never offer a metric the read does not return."""
    assert cat.filter_keys() == tuple(m["key"] for m in cat.catalog())


# ── RULE TWO ────────────────────────────────────────────────────────────────────────────────────

def test_no_carrier_tenant_or_product_name_in_the_module():
    src = open(cat.__file__).read()
    # the module names the SOURCE's field spellings (what a feed mapping is) and no carrier brand
    for word in ("boost", "elevate", "cellfonz", "luxelink", "vzone", "t-mobile", "verizon"):
        assert not re.search(word, src, re.I), f"RULE TWO: {word!r} must not appear in code"
