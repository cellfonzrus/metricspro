"""WHICH STORE-DLAR COLUMN IS WHICH METRIC — ONE HOME (index §COMMCALC-60).

WHY THIS MODULE EXISTS. On 2026-11-10 the owner asked for the 21 columns the carrier portal sends at
store grain that nothing read to become part of KPI reporting, filterable by KPI alongside the
standard filters, with the Send bar. The columns were the easy half. The hard half was that "which
store-DLAR column means what" had FOUR homes:

  1. `dlar_sweep.STORE_FIELDS`         — which SOURCE field feeds which of our columns (§COMMCALC-16)
  2. `kpi_failing.STORE_KPI_COLUMNS`   — which of our columns carries a SCORED KPI (§19.28)
  3. `router.get_dlar_store_kpis`      — a PostgREST `select("location,address,…")` literal
  4. `router._cr_resolve_kpi_metrics`  — the SAME literal again, written out a second time
     (plus a hand-typed `KPIS` array in `frontend/src/app/(platform)/commcalc/kpi/page.tsx`)

So landing a column meant editing four lists, and the first one anybody forgot would render as a
store that sold nothing — a plausible-looking zero, which is the class of defect that hides. The same
shape has already cost this platform a month of activations (§COMMCALC-16) and a tenant's whole
device-line report (§COMMCALC-49.10).

WHAT THIS MODULE IS. The single place that answers "what can the store KPI report show, what is each
figure called, and which of them are scored" — DERIVED from (1) and (2) and copying neither:

    dlar_sweep.STORE_FIELDS ─┐
                             ├─ catalog() ─→ display rows, select_columns(), filter_keys()
    kpi_failing.STORE_KPI_COLUMNS ─┘

A column declared in the sweep is therefore IN the report the moment it is declared; nobody has to
remember a second list, and the lock (`harness_dlar_metric_catalog.py`) fails the build if a caller
stops dereferencing this one or a second copy of the column list appears.

WHAT IT DOES NOT DECIDE. Not targets (`payout_config` / `carrier_kpi_metric`, resolved by
`_kpi_defs`), not grain (`kpi_failing.grain_of`), not whether a figure ARRIVED (`portal_cell`
/ `field_arrival`). Those are other modules' facts and this one reads or ignores them.

RULE TWO. The only proper nouns here are the SOURCE's own field spellings, which is what a feed
mapping is made of. No carrier, tenant or product name appears, and the labels below are English for
the portal's own words — not any tenant's vocabulary, which the multi-tenant rule requires be a
declared argument rather than a house default.

EVERY TENANT. The catalog is a statement about the SHAPE of this feed, identical for every org; what
differs per tenant is whether that org has rows at all. A tenant whose carrier publishes no store
DLAR gets an empty read and a surface that SAYS the feed published nothing — never another tenant's
figures and never a zero that reads as "sold none" (§COMMCALC-49.10).

PURE: stdlib only, no DB, no network, no app config. Proof: `backend/harness_dlar_metric_catalog.py`
and `backend/tests/unit/test_dlar_metric_catalog.py`.
"""
from app.modules.commcalc import dlar_sweep as _ds
from app.modules.commcalc import kpi_failing as _kf

# ── HOW A FIGURE IS GROUPED ON A SURFACE ────────────────────────────────────────────────────────
# Display grouping, in display order. A group is presentation, so it lives beside the labels and not
# in the feed mapping — but it is DECLARED (not guessed from the column name) so a surface cannot
# invent its own grouping and drift from the next one.
GROUPS = ("scored", "activations", "trade_ins", "attach", "loyalty", "target", "trend")

# our column → (human label, display group). The ONE place a store-DLAR figure is named.
#
# A column declared in `dlar_sweep.STORE_FIELDS` and absent here is a BUILD FAILURE, not a silently
# unnamed metric: `harness_dlar_metric_catalog.py` checks the two sides against each other. That is
# the lock that stops the next column from landing in the table and never reaching a surface.
LABELS = {
    # scored KPIs (which ones are scored is NOT decided here — see `_scored_by_column`)
    "atu":                          ("ATU %", "scored"),
    "protect_pct":                  ("Protect %", "scored"),
    "byod_pct":                     ("BYOD %", "scored"),
    "family_plan_pct":              ("Family Plan %", "scored"),
    "tmr3":                         ("3MR %", "scored"),
    "aal_conversion":               ("AAL %", "scored"),
    # activations
    "total_acts":                   ("Total Activations", "activations"),
    "gross_adds":                   ("Gross Adds", "activations"),
    "activations_total":            ("Activations Total (source)", "activations"),
    "pay_now_acts":                 ("Pay-Now Activations", "activations"),
    "pay_later_acts":               ("Pay-Later Activations", "activations"),
    "total_upgrades":               ("Upgrades", "activations"),
    "swaps_total":                  ("Swaps", "activations"),
    "auxiliary_device_acts":        ("Auxiliary Device Acts", "activations"),
    "starlink_activations":         ("Satellite Activations", "activations"),
    "starlink_sales":               ("Satellite Sales", "activations"),
    "rpl_count":                    ("RPL Count", "activations"),
    # trade-ins
    "total_trade_ins":              ("Trade-Ins", "trade_ins"),
    "value_trade_ins":              ("Value Trade-Ins", "trade_ins"),
    "upgrade_value_trade_ins":      ("Upgrade Value Trade-Ins", "trade_ins"),
    "offer_trade_ins":              ("Offer Trade-Ins", "trade_ins"),
    "trade_in_unlocked_activations": ("Trade-In Unlocked Acts", "trade_ins"),
    # attach / quality
    "acc_attach_rate":              ("Accessory Attach %", "attach"),
    "protection_dop":               ("Protection DOP", "attach"),
    "protection_upgrades":          ("Protection Upgrades", "attach"),
    "byod_total":                   ("BYOD Total", "attach"),
    "byod_unlocked_activations":    ("BYOD Unlocked Acts", "attach"),
    "total_health_checks":          ("Health Checks", "attach"),
    "conversion_rate":              ("Conversion %", "attach"),
    "shopper_trak_conversion":      ("Foot-Traffic Conversion %", "attach"),
    "port_pct":                     ("Port-In %", "attach"),
    "avg_first_mrc":                ("Avg First MRC", "attach"),
    # loyalty
    "avg_platinum_points":          ("Avg Loyalty Points", "loyalty"),
    # target
    "sales_target":                 ("Sales Target", "target"),
    "psa_projected":                ("Projected % to Target", "target"),
    "projection":                   ("Projected Units", "target"),
    "zero_selling_days":            ("Non-Selling Days", "target"),
    # trend
    "mom_percent_change":           ("Month-over-Month %", "trend"),
    "yoy_percent_change":           ("Year-over-Year %", "trend"),
}

# The store-identity columns every read of this table needs before any figure. Not metrics — they
# answer WHICH store, which is why they are excluded from the catalog by KIND (TEXT) and named here.
IDENTITY_COLUMNS = ("location", "address", "store_code")

# A column the sweep DERIVES rather than reads: `normalize_store` sums two source fields into it
# (`dlar_sweep.STORE_DERIVED_FROM`). It is a stored column a surface shows, so it belongs in the
# catalog; it has no source spelling of its own, and saying so stops a reader hunting for one.
DERIVED_COLUMNS = ("total_acts",)

# WHICH COLUMNS MIGRATION 002 ALREADY CREATED. Everything else arrives by hand-applied migration
# 1118 and is therefore PROBED per column before the one real select (`core.column_tolerant`,
# index §4b.1) — never assumed, because an unapplied migration would otherwise 400 the whole read
# and the page would show nothing rather than the columns that do exist.
PREEXISTING_COLUMNS = (
    "total_acts", "port_pct", "psa_projected", "family_plan_pct", "tmr3", "aal_conversion",
    "protect_pct", "atu", "byod_pct", "gross_adds", "pay_now_acts", "pay_later_acts",
    "total_upgrades", "conversion_rate", "acc_attach_rate", "avg_first_mrc", "sales_target",
    "zero_selling_days", "shopper_trak_conversion",
)


def _scored_by_column():
    """{our column → metric_key} for the SCORED KPIs. DEREFERENCES `kpi_failing.STORE_KPI_COLUMNS`
    — the pay engine's own map — so the report's columns and the paid score cannot name a different
    set. A second copy here is the §19.28 defect wearing a reporting hat. PURE."""
    return {col: key for key, col in _kf.STORE_KPI_COLUMNS.items()}


def catalog():
    """Every store-DLAR figure a surface may show, in display order. PURE, DERIVED.

    One entry per measurable column:
      key      — the `commcalc.raw_dlar_store` column
      label    — what a person calls it
      group    — one of `GROUPS`
      kind     — `dlar_sweep.COUNT` or `.RATE` (how to format, and what absence means)
      scored   — the KPI metric_key when this column carries a scored KPI, else None
      source   — the source field spelling(s) the sweep reads for it; () when derived
      derived  — True when the sweep computes it from other fields
      optional — True when the column arrives by a hand-applied migration and must be probed
    """
    scored, out, seen = _scored_by_column(), [], set()
    rows = [(r[0], r[1], tuple(r[2:])) for r in _ds.STORE_FIELDS if r[1] in (_ds.COUNT, _ds.RATE)]
    rows += [(c, _ds.COUNT, ()) for c in DERIVED_COLUMNS]
    for col, kind, source in rows:
        if col in seen:
            continue
        seen.add(col)
        label, group = LABELS.get(col, ("", ""))
        out.append({"key": col, "label": label, "group": group, "kind": kind,
                    "scored": scored.get(col), "source": source,
                    "derived": col in DERIVED_COLUMNS,
                    "optional": col not in PREEXISTING_COLUMNS})
    out.sort(key=lambda m: (GROUPS.index(m["group"]) if m["group"] in GROUPS else len(GROUPS),
                            m["label"]))
    return tuple(out)


# The column list EVERY reader of this table dereferences, so there is one spelling of it.
REQUIRED_COLUMNS = IDENTITY_COLUMNS + PREEXISTING_COLUMNS
OPTIONAL_COLUMNS = tuple(m["key"] for m in catalog() if m["optional"])


def select_columns():
    """Identity first, then every catalogued figure — the whole column list, no duplicates. PURE."""
    return REQUIRED_COLUMNS + OPTIONAL_COLUMNS


def filter_keys():
    """The metric keys a "filter by the KPI" picker may offer (owner 2026-11-10). They ARE the
    catalog, in catalog order, so a picker can never offer a figure the read does not return."""
    return tuple(m["key"] for m in catalog())


def by_key():
    """{column → catalog entry} for a caller holding a column name. PURE."""
    return {m["key"]: m for m in catalog()}


# WHY THE SENTENCE GOES UNDER `reason` AND NOT `unavailable_reason` (§19.36, caught by CI
# 2026-11-10 on the first push of this change). It names a migration, which is a SETUP INTERNAL: a
# tenant must see the one neutral house sentence and the platform super admin the detail. That
# swap is `SetupNoticeMiddleware`'s job and the boundary reads ONLY the message-shaped keys in
# `core.setup_notice.MESSAGE_KEYS` — `reason` is one of them, `unavailable_reason` is not. Under the
# wrong key the internals would have gone straight to the customer, so the key IS the fix, not the
# wording. `available` carries the machine-readable half, so a surface can still say "not stored yet"
# without reading the sentence at all.
UNAVAILABLE_REASON_KEY = "reason"


def present_catalog(present_columns):
    """The catalog as it is TRUE OF THIS DATABASE: each entry stamped with whether the column exists
    yet. A figure whose migration has not been applied is reported as unavailable WITH its reason —
    never omitted (which reads as "this carrier does not publish it") and never shown as 0 (which
    reads as "the store sold none"). `present_columns` is what `core.column_tolerant.present_columns`
    probed. PURE."""
    present = set(present_columns or ())
    out = []
    for m in catalog():
        avail = (not m["optional"]) or m["key"] in present
        out.append(dict(m, available=avail,
                        **{UNAVAILABLE_REASON_KEY:
                           None if avail else "column not yet applied (migration 1118)"}))
    return tuple(out)
