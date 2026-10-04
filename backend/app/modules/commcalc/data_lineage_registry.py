"""Canonical source-of-truth registry — the ONE place code names a data table.

WHY THIS EXISTS (owner 2026-08-30). The data-freshness banner read commcalc.raw_sales (the MONTHLY
reconciliation upload) when the LIVE feed is commcalc.daily_sales_feed, so it cried "stale since 8-09"
while the numbers were current. Nothing in code stopped it — the fact "which table is the live sales
feed" lived only in a human's head. This module makes that fact CODE, and the owner asked to extend the
same discipline to every module ("one by one … so we don't duplicate anything or miss anything"):

    • Read a data source, or add a metric/report? Reference the constants here — never hardcode a raw
      table name at the call site. If the source-of-truth table ever changes, it changes in ONE place.
    • The machine-readable dependency map is still commcalc.data_lineage (migrations 924/925) +
      docs/DATA_LINEAGE.md — query it for "what does X touch?". THIS module is the small runtime slice
      of that map that code dereferences, kept in sync with the SQL seed by
      harness_data_lineage_guard.py (which fails if they drift, an ingest table has no lineage edge, or
      freshness stops pointing at the live feed).

DON'T DUPLICATE, DON'T MISS. Before wiring a new feed or metric:
  1. Is its table already a value in INGEST_TABLES_BY_MODULE / an edge in 925_data_lineage_seed.sql?
     Then reuse it — do not stand up a second capture path for the same data (the "duplicate" risk).
  2. New EXTERNAL FEED (a file/API/scrape written to a table)? Add its table under the owning module in
     INGEST_TABLES_BY_MODULE AND add an `ingest` edge to the seed, then re-run the guard. The guard
     refuses a registered ingest table that has no lineage edge, so a new feed can't land undocumented
     (the "miss" risk).

SCOPE. This registry names EXTERNAL-FEED ingest tables (parsed from an uploaded/swept file, or pulled
from an external API/scrape) and the LIVE-vs-authoritative pairs behind freshness decisions — the two
places the 2026-08-30 class of bug lives. Purely in-app/derived/config tables (invoices, journal
entries, computed ledgers) are documented as lineage edges where they matter but are NOT feeds, so they
are not listed here.

Pure data + tiny pure helpers: no DB, no network, no heavy imports. Safe to import anywhere.
"""

# ── SALES (commcalc) ─────────────────────────────────────────────────────────────────────────────
# The hourly email/FTP sweep lands transactions here (clean ISO trans_date). THIS is the live feed:
# "is sales data flowing?" is answered by daily_sales_feed, not by the monthly upload.
LIVE_SALES_FEED = "daily_sales_feed"
# The MONTHLY 'sales' reconciliation upload. Moves only when a monthly file is loaded — so it is NOT a
# freshness signal on its own. Authoritative for month-close reconciliation; the daily feed is promoted
# into it. Displayed sales read the UNION of the two (see SALES_DISPLAY_SOURCES).
MONTHLY_SALES = "raw_sales"
# What the display aggregation (_sales_cell_agg) reads: the union, so a fresh daily feed keeps every
# report current between monthly uploads.
SALES_DISPLAY_SOURCES = (LIVE_SALES_FEED, MONTHLY_SALES)

# ── OTHER NAMED commcalc feeds referenced by name in code ─────────────────────────────────────────
CUSTOM_CAPTURE = "raw_custom_import"      # b2b custom sheets (Activation Details, Bill Payment, …) → JSONB
EPAY_DAILY = "raw_epay_daily_tx"          # Boost ePay settlement
MA_DAILY_TX = "raw_ma_daily_tx"           # VidaPay / Total MA daily transactions
DAILY_CLOSING = "daily_closing"           # employee cash + tender declaration (owned by the closing module)

# ── THE COMMISSION-PER-DEVICE FEED — which table says "the carrier paid (or clawed back) on this IMEI" ─
# Owner 2026-09-24 (index §11b): inventory is "crashed against the sales report by invoice and the
# commission received reports". ONE fact, ONE home: the per-line vendor rebate / commission history
# (mig 1005) — one row per rebate COMPONENT per line, the device IMEI on every row, `earned_amount`
# SIGNED (a reversal / chargeback line is negative), the customer and invoice as the carrier states them.
# Every reader (the inventory-integrity engine, its router, the Inventory-vs-Sold report) DEREFERENCES
# these names; `harness_inventory_integrity_lock.py` fails the build on a copied table literal.
COMMISSION_PER_DEVICE_FEED = "raw_vendor_rebate"
# The columns a per-device reader takes from it, and what each MEANS (the header lies on two of them —
# §27.6: `imei` is the file's 'Related Tracking Number', `mdn` its 'Tracking Number'). `earned` is the ONLY
# money column summed (never `unit_amount`, which is unsigned — §27.7); `reversal_flag` is the feed's own
# chargeback cell, read as evidence beside the sign.
COMMISSION_PER_DEVICE_COLUMNS = {
    "device": "imei", "mobile": "mdn", "customer": "customer_name", "customer_ref": "customer_ref",
    "invoice": "invoice_no", "sold_on": "sold_on", "earned": "earned_amount", "reversal_flag": "charge_back",
    "device_name": "device_name", "device_sku": "device_sku", "rate_plan": "rate_plan", "store": "store",
}


def commission_per_device_select() -> str:
    """The select list a per-device commission reader uses — derived from COMMISSION_PER_DEVICE_COLUMNS."""
    return ",".join(dict.fromkeys(COMMISSION_PER_DEVICE_COLUMNS.values()))

# ── LIVE-vs-MONTHLY PAIRS — the freshness trap, per feed ──────────────────────────────────────────
# A freshness / "is data flowing?" check must read the LIVE (first) table, NEVER the monthly (second).
# Reading the monthly table is the 2026-08-30 false alarm this registry guards against. Each new pair a
# module introduces (e.g. the POS builtin stream) belongs here so the guard can hold the invariant.
LIVE_VS_MONTHLY_PAIRS = {
    "sales": (LIVE_SALES_FEED, MONTHLY_SALES),
    # POS builtin stream (pos/commcalc_feed.py MODE_TABLES): the in-house POS writes its OWN daily and
    # monthly tables, then promotes into the sales feed/raw_sales. Same trap — a freshness read here must
    # take the daily stream, never the monthly one.
    "pos_builtin": ("pos_builtin_daily_sales", "pos_builtin_sales"),
}

# ── WHAT THE COMMISSION RUN CALCULATION READS — the tables whose landing re-runs it (owner 2026-09-28) ─
# Owner: "when sept is uploaded the system should calculate automatically without manual intervention".
# ONE fact, ONE home: the period-keyed feed tables `router._run_calculation` reads — `_calc_inputs` (the sales
# basis via `_fetch_sales_unified`, payment detail, MI, the two DLAR grains), `_apply_new_engines` (the
# carrier statement) and `sale_installment_engine`'s paid-gate (the two MA feeds). A landing into one of these
# for org X, period P is what `auto_calc.landed` turns into ONE standard Run Calculation of (X, P). Index §6l.
# `harness_auto_calc_lock.py` fails the build if `_calc_inputs` starts reading a period-keyed table that is
# not listed here (the calculation would then read data whose arrival re-runs nothing).
COMMISSION_CALC_FEEDS = (
    LIVE_SALES_FEED, MONTHLY_SALES,
    "raw_payment_detail", "raw_mi", "raw_dlar_rep", "raw_dlar_store",
    "carrier_commission",
    "raw_ma_commission", MA_DAILY_TX,
)
# Sales tables that land BESIDE the pay basis but that the calculation does not read (index §30.13 / §32).
# Their landers call the same hook, which answers `not_a_calc_input` — so the day the calculation starts
# reading one of them, moving it into COMMISSION_CALC_FEEDS is the whole change; no lander is touched.
SALES_SIBLING_TABLES = ("raw_sales_invoice", "raw_sales_invoice_tender", "raw_sales_product")

# ── EXTERNAL-FEED INGEST TABLES, BY OWNING MODULE ─────────────────────────────────────────────────
# Every table here must have an `ingest` edge in 925_data_lineage_seed.sql (the guard enforces it), so a
# new feed cannot land undocumented. Extended one module at a time (owner 2026-08-30).
INGEST_TABLES_BY_MODULE = {
    # commcalc — the b2b / POS / MA report importers. Union of upload_file's TABLE_MAP and the special
    # handlers (x_report → pos_tender_summary, inventory_aging → inventory_value, ma_overview,
    # custom reports → raw_custom_import, ePay → raw_epay_daily_tx). Mirrors _TRACE_TARGET_TABLE.
    "commcalc": (
        "raw_sales", "daily_sales_feed", "raw_payment_detail", "raw_mi",
        "raw_dlar_rep", "raw_dlar_store", "raw_catalog", "raw_categories",
        "raw_comp_report", "raw_ma_commission", "raw_ma_daily_tx", "raw_ma_fulfillment",
        "pos_tender_summary", "inventory_value", "ma_overview_upload",
        "raw_custom_import", "raw_epay_daily_tx",
        # Merchant-processor portal scrape (owner 2026-09-04, migs 955/956). The daily pull from the
        # three card-processor portals — PayAnywhere/Payments Hub (the EXTERNAL credit-card terminal
        # both current tenants run, the "white machine"), TransFirst TransLink and ClientLine/
        # BusinessTrack (the POS merchant providers). Settlement is the day-grain feed the closing
        # recon tallies against what employees declared; the batch table is the funding grain the
        # cash/deposit recon reads. Two tables because they are two GRAINS — summing them
        # double-counts, which is exactly the confusion a lineage edge exists to prevent.
        "merchant_settlement_day", "merchant_settlement_batch",
        # Per-line vendor rebate/commission history (mig 1005), landed by the mapped ingest
        # (/commcalc/upload-mapped). A LANDING ZONE: earned_amount is what the carrier owes,
        # collected_amount what it has paid, and no P&L / GP / payout / balance-sheet path reads it
        # while "is an earned rebate a receivable?" is an open owner decision. Distinct from the
        # `pos` module's activation_rebate_ledger below, which is the AGGREGATE that books — two
        # edges because they answer two different questions, not two paths to one answer.
        # (Dereferenced — it is THE commission-per-device feed named above, not a second spelling.)
        COMMISSION_PER_DEVICE_FEED,
        # The POS by-product sales aggregate's OWN table (mig 1011, landing identity 2026-09-20) —
        # layout pos_product_sales through the mapped ingest / the intake's `pos` kind. Product-level
        # rows (SKU, cost, selling price); never summed beside raw_sales (double count). Read by the
        # onboarding verify + report links only; no money path.
        "raw_sales_product",
        # Sales by invoice with the tender types (mig 1012, owner 2026-09-21): ONE ROW PER INVOICE
        # (raw_sales_invoice) + one row per invoice × declared tender / tax-component column
        # (raw_sales_invoice_tender), landed by the intake's `invoice` kind through the mapped ingest.
        # Read by the onboarding Stage-4 verify (invoice totals, the tender split beside the X-report
        # per store-day, Σ tax, the report links); no money path reads either yet — the tax aggregator
        # and the closing basis are PROPOSED in index §30.13.
        "raw_sales_invoice", "raw_sales_invoice_tender",
    ),
    # pos — the in-house POS. Its builtin stream (commcalc.pos_builtin_daily_sales /
    # commcalc.pos_builtin_sales) promotes into the sales feed; receipt OCR and the carrier vendor-rebate
    # xlsx are its other external ingests. pos.* live in the `pos` schema (qualified keys); the two
    # commcalc-schema tables use bare keys, matching the seed's affected_key convention.
    "pos": (
        "pos_builtin_daily_sales", "pos_builtin_sales",
        "pos.sales", "pos.receipt_imports", "pos.customers", "pos.activations",
        "activation_rebate_ledger",
    ),
    # closing — employee daily cash + tender declaration and the per-org tender-map config sheet.
    "closing": (
        "daily_closing", "closing_tender_def", "closing_tender_map",
    ),
    # storeops — roster/identity template uploads, the external merchant-ID mapping, and the Google
    # Reviews API sweep. storeops.* are in the `storeops` schema (qualified keys); store_mapping is the
    # commcalc-schema mirror the roster upload writes (bare key, per the seed convention).
    "storeops": (
        "storeops.employees", "storeops.stores", "storeops.store_alias",
        "storeops.store_merchant_id", "store_mapping",
        "storeops.google_review_store", "storeops.google_review_snapshot", "storeops.google_review_item",
    ),
    # billing — the ONLY external feed here is the platform-cost connector (it pulls each store's
    # platform bill from an external source). Plans/invoices/pricing are in-app config, not feeds.
    "billing": (
        "storeops.platform_billing_connector",
    ),
    # asset — the Asset Lending ledger, parsed from an uploaded Asset_Lending.xlsx (staging → atomic swap).
    "asset": (
        "asset_ledger",
    ),
    # supply (mig 1021, index §36) — the vendor catalog price snapshot: each supply vendor's products, prices,
    # pack sizes and stock, landed by ONE lander (supply/store.land_catalog) from two routes — the price-compare
    # kit's products.json/csv upload and the read-only portal read (live login or the data-sources scheduler).
    # Read by Price Compare and the cart optimizer only; no P&L / payout path reads it.
    "supply": (
        "vendor_catalog_price",
    ),
    # account — the franchise royalty report (mig 1022, index §37): the franchisor's monthly statement per center,
    # uploaded as a PDF / saved HTML / pasted text (or typed on the manual form) through /account/royalty/import |
    # /manual. Header + lines, one report per org × center × month (a re-import replaces it). The account module was
    # feed-less (a compute engine) until this; it now owns exactly these two tables and still derives everything else.
    "account": (
        "royalty_report", "royalty_report_line",
    ),
    # Other modules are added in subsequent PRs, one by one.
}


# ── FRESHNESS COLUMN per table — which timestamp reflects DATA ARRIVAL ────────────────────────────
# A "is data flowing?" probe must read the column that moves when new data lands. For most raw tables
# that is created_at (the DB stamps it on insert). daily_sales_feed is the exception: rows are re-inserted
# / promoted, so its true arrival stamp is `uploaded_at`, NOT created_at — probing created_at made a
# feed-only tenant (luxelink) read newest_ingest_at=None, so its P&L/Balance-Sheet never auto-computed
# (permanently empty) and the books-stale banner never fired. account/autocompute._PERIOD_SOURCES already
# lists daily_sales_feed with uploaded_at first (fix dcb0807); this registry makes that rule the ONE place
# it's written down, and the guard locks it so it can't silently regress.
#
# EVERY table whose arrival column is NOT created_at belongs here — that is the whole point of the map.
# `harness_ingest_freshness.py` §C fails the build if `account/autocompute._PERIOD_SOURCES` names a
# non-created_at arrival column for a table this dict does not declare, so the two copies of the fact
# cannot drift apart again (they already did once: the registry held daily_sales_feed and nothing
# dereferenced it, while autocompute carried its own copy).
FRESHNESS_COLUMN_BY_TABLE = {
    "daily_sales_feed": "uploaded_at",
    # VIP sweep landing tables: the sweep stamps `swept_at` when a row lands. `created_at` exists but
    # moves on re-write, so it is the same trap as daily_sales_feed — declared here rather than left
    # implicit in the one list that happened to know about it.
    "vip_paygo_payments": "swept_at",
    "vip_credit_memos": "swept_at",
    # THESE THREE TABLES HAVE NO `created_at` AT ALL (measured against the live schema 2026-10-03), so
    # the default left `last_ingest_at` None and the watchdog could only ever say "stopped arriving" —
    # the §19.18 discriminator dead again, on three more feeds. Found the moment the watchdog began
    # covering every registered feed instead of three: `asset_ledger` reported 16 days late with no
    # arrival time to explain whether the file had stopped or its content had frozen.
    "asset_ledger": "uploaded_at",
    "pos_tender_summary": "updated_at",
    "inventory_value": "updated_at",
}

# ── WHICH COLUMNS MUST CARRY A VALUE — the COMPLETENESS axis (owner defect 2026-09-26, index §19.28) ──
# §19.18 gave the platform two questions about a feed: is it still ARRIVING (`last_ingest_at`), and is its
# CONTENT moving (`latest_data_date`). Both can be green while a feed has quietly become USELESS, because
# there is a third question neither asks: **do the columns still carry values?**
#
# MEASURED, house org, `commcalc.raw_dlar_rep`, 2026-09-26 — rows kept arriving and the data date kept
# moving, and in JULY 2026 seven columns stopped carrying anything at all, together:
#
#   period      rows   store / door_*      ga_prepaid + ga_postpaid (sum)
#   March 2026   110   populated            1508 / 16
#   April 2026   103   populated            1071 /  2
#   May 2026      81   populated             403 /  3
#   June 2026     75   populated             621 /  2
#   July 2026     58   ALL BLANK               0 /  0     <-- the advocate report changed shape
#   August 2026   44   ALL BLANK               0 /  0
#   September     45   ALL BLANK               0 /  0
#
# One upstream change, seven columns: `normalize_rep` maps `store` and `door_address` from the record's
# `address`, `door_name/city/state/zip` from `name/city/state/zip`, and `ga_prepaid`/`ga_postpaid` from
# `prepaid_activations`/`postpaid_activations`. The portal stopped sending that whole block. Nothing
# failed: 147 rep rows since July divided a bounty by a denominator that was no longer being sent, and
# the `if ga_prepaid > 0 else 0` guard turned every one of them into a measured KPI failure at 0%.
#
# A column that STOPS ARRIVING must say so, and WHICH columns those are is declared here — once — so the
# sweep that sees the portal's response dereferences the fact instead of carrying its own list.
REQUIRED_CONTENT_COLUMNS = {
    # the advocate (rep) DLAR: the door identity the store roll-down joins on, and the prepaid split the
    # Boost App attach rate is measured against.
    "raw_dlar_rep": ("store", "ga_prepaid", "gross_adds", "atu_pct"),
    # the store DLAR: the join key and the three KPIs that are ONLY published at store grain.
    "raw_dlar_store": ("address", "family_plan_pct", "tmr3", "aal_conversion"),
}


def required_content_columns(table: str):
    """The columns a row of `table` must actually CARRY, or () when nothing is declared for it. The ONE
    home of that fact — a caller reads it, never repeats it (`harness_kpi_registry_lock.py` §(h))."""
    return tuple(REQUIRED_CONTENT_COLUMNS.get(table) or ())


def content_arrival(rows, columns):
    """PURE. Per declared column: how many of these rows carry a value, and which columns carry NONE.

    → `{"rows": n, "columns": {col: {"filled": n, "empty": n}}, "empty_columns": [col, ...]}`

    "Carries a value" means not None, not a blank/whitespace string, and — for a NUMERIC column — not a
    whole-pull zero: a column that is 0 in EVERY row of a pull that has activations is a column that
    stopped being sent, not a month in which nobody sold anything. That distinction is the whole point;
    it is what makes July's `ga_prepaid` visible while a genuine zero for one rep is not. With no rows,
    nothing is reported empty — an empty pull is a different failure and the load guards already own it.
    stdlib only, no DB."""
    rows = list(rows or [])
    cols = tuple(columns or ())
    out = {"rows": len(rows), "columns": {}, "empty_columns": []}
    if not rows or not cols:
        return out
    for col in cols:
        filled = 0
        for r in rows:
            v = (r or {}).get(col)
            if v is None:
                continue
            if isinstance(v, str):
                if v.strip():
                    filled += 1
                continue
            try:
                if float(v) != 0.0:
                    filled += 1
            except (TypeError, ValueError):
                filled += 1          # a non-numeric, non-string value is still a value
        out["columns"][col] = {"filled": filled, "empty": len(rows) - filled}
        if filled == 0:
            out["empty_columns"].append(col)
    return out


# ── WHICH UPLOAD TYPES REPLACE THEIR WHOLE PERIOD ─────────────────────────────────────────────────
# An upload type here lands by DELETING the (org, period) slice and inserting the file's rows. Two
# consequences follow, and the second is why this list exists:
#
#   1. Re-ingesting the same report is idempotent — it rewrites what it already wrote.
#   2. Given two files of the SAME report covering the SAME period, ingesting the newer one ALONE
#      leaves the database in exactly the state that ingesting both in order would. The older file's
#      entire contribution is erased by the newer one's delete. It is redundant work, not lost data.
#
# Property 2 is what lets a sweep collapse a BACKLOG. It is a consequence of the replace semantics,
# not an assumption about the report being cumulative — nothing here needs to know that.
#
# WHY IT IS A DECLARED LIST AND NOT A GUESS (Boost, 2026-09-20). While the house mailbox's login was
# rejected, ~336 hourly copies of the same b2bsoft sales report piled up. The sweep drained them in
# ARRIVAL ORDER, each one deleting September and re-inserting a slightly larger file: 336 full 1.1 MB
# imports to reach a state the newest file alone describes, ~30 per sweep, with the feed showing a
# part-month for hours while it crawled — and going BACKWARD whenever an older message landed after a
# newer one. §19.19.
#
# AN UPLOAD TYPE NOT LISTED HERE IS NEVER COLLAPSED. Anything incremental, append-only, or whose slice
# is narrower than the period (the INGEST_PARTITION tables in `ingest_slice.py`, where a file replaces
# only its own store/account slice) must process every message — dropping one there would lose rows.
# Silence means "process them all", which is the safe answer for a type nobody has reasoned about.
FULL_REPLACE_UPLOAD_TYPES = frozenset({
    # The b2b daily sales export -> daily_sales_feed. Not in INGEST_PARTITION, so its ingest takes the
    # legacy wide delete of (org, period) and re-inserts: the definition of a whole-period replace.
    "daily_sales",
    # POS X-report tender summary -> pos_tender_summary, and the sales-trend / key-stats summaries:
    # each is a per-period SNAPSHOT of the same numbers, rewritten whole on every ingest.
    "x_report",
    "sales_trend",
})


def replaces_whole_period(upload_type: str) -> bool:
    """True when ingesting the NEWEST file of this report makes older copies of the SAME report, for the
    same period, redundant — because the ingest replaces the whole (org, period) slice. Unknown or
    unlisted types return False: never collapse a report nobody has reasoned about."""
    return (upload_type or "").strip() in FULL_REPLACE_UPLOAD_TYPES


# ── MODULES AUDITED TO HAVE NO EXTERNAL FEED (owner 2026-08-30 census) ─────────────────────────────
# These modules were checked and own NO external-feed ingest: either pure in-app CRUD, or a compute/
# derive engine that READS the feeds above and writes computed tables (not feeds). Listed so "every
# module" is explicitly accounted for — nothing was skipped silently. The guard asserts none of these
# is also in INGEST_TABLES_BY_MODULE (a module can't be both feed-owning and feed-less).
MODULES_WITHOUT_EXTERNAL_FEEDS = (
    # compute / derive engines (read feeds, write computed tables — not feeds):
    "payables",
    # core owns the freshness/feed REGISTRY infrastructure (core.import_feed), not an external feed itself:
    "core",
    # pure in-app feature modules (user-created data, no external file/API feed):
    "approvals", "chat", "crm", "helpdesk", "hr", "notify",
    "recovery", "referral", "remediation", "storevisit", "vision",
    # marketing (migs 986/987) — outside-store event management. Feed-LESS on purpose: every row it
    # owns is typed by a human (the event, its staff, the checklist, the giveaway counts) or captured
    # from the device at check-in. Its one derived number, event planned-vs-actual, is READ from
    # commcalc's shared sales pass (_sales_cell_agg via _compute_feed_actuals_py, §3/§23) rather than
    # ingested, so it introduces no external feed and owns no ingest table. The later creative-gallery
    # / marketing-portal-pull phase WILL bring an external feed; when it does it moves to
    # INGEST_TABLES_BY_MODULE above and seeds database/migrations/925_data_lineage_seed.sql.
    "marketing",
)


# ── WHICH ROUTE MAY DELIVER A FEED — pointer, not a second registry ──────────────────────────────
# This module names WHICH TABLE a feed lands in. WHICH ROUTE is allowed to deliver it (a portal login
# pull, the email sweep, FTP, a manual upload) is per-org CONFIG, not a fact of the lineage map:
# `commcalc.connector_route_policy` (migration 998) + `commcalc/connector_route_policy.py`, keyed on
# the connector slug and the `core.import_feed.source_type` route vocabulary.
#
# It is recorded here because the two questions get confused: a feed whose portal login has been closed
# still lands in exactly the same table, so nothing in THIS registry changes when a route is switched
# off — and a reader who assumes "no pull ⇒ no data" would wrongly hunt for a broken table.
#
# LIVE as of the owner directive 2026-09-09: the POS sales/inventory connector's `pull` route is CLOSED
# by the house default (the vendor instructed us not to use their 2FA/browser login), so
# `daily_sales_feed`, `raw_custom_import` and `inventory_value` arrive for it by `email_sweep` and by
# manual upload ONLY. The connector is named in the migration's seed rows, never here — RULE TWO.
ROUTE_POLICY_TABLE = "connector_route_policy"      # commcalc schema; migration 998


def freshness_column(table: str) -> str:
    """The timestamp column a freshness probe should read for `table` to detect new data — the mapped
    override (e.g. daily_sales_feed → uploaded_at) or 'created_at' by default."""
    return FRESHNESS_COLUMN_BY_TABLE.get(table, "created_at")


def all_ingest_tables() -> tuple:
    """Flattened, de-duplicated set of every registered external-feed ingest table across all modules."""
    seen, out = set(), []
    for tables in INGEST_TABLES_BY_MODULE.values():
        for t in tables:
            if t not in seen:
                seen.add(t); out.append(t)
    return tuple(out)


# Backwards-compatible alias (the guard and any earlier caller can keep using RAW_INGEST_TABLES).
RAW_INGEST_TABLES = all_ingest_tables()


def freshness_source(item: str = "sales") -> str:
    """The table a freshness / "is data flowing?" check must measure for a logical feed — always the LIVE
    side of its LIVE_VS_MONTHLY pair. For sales this is daily_sales_feed, never the monthly upload — the
    2026-08-30 false-alarm this registry guards against."""
    pair = LIVE_VS_MONTHLY_PAIRS.get(item)
    return pair[0] if pair else LIVE_SALES_FEED


def display_sources(item: str = "sales") -> tuple:
    """The table(s) a DISPLAY aggregation reads for a logical item (the union of a live/monthly pair)."""
    pair = LIVE_VS_MONTHLY_PAIRS.get(item)
    return tuple(pair) if pair else (freshness_source(item),)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# WHICH FEEDS ARE WATCHED, AND HOW OFTEN EACH IS DUE — the watchdog's one home (owner 2026-10-03)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Owner, after two feeds died unnoticed: *"need a root cause analysis why this fails and a fool proof
# system to avoid such fails"*.
#
# THE CLASS, named. The registry above already knows every external feed. The freshness monitor watched
# THREE of them — activation details, bill payments and sales — named by hand at the call site. So
# registering a feed did not get it watched, and a feed nobody listed could stop for seven weeks with no
# alarm: `raw_comp_report` last carried data on 2026-08-06 and `asset_ledger` on 2026-09-23, and BOTH
# were found by a human noticing a wrong number weeks later. That is not a monitor that missed a feed;
# it is a monitor that could not see it. The same shape as §19.18 three times over — the fact is written
# down here and the caller kept its own copy.
#
# ONE FACT, ONE HOME, DEREFERENCED. A feed's cadence and its data-date column live HERE and callers READ
# them. `watched_feeds()` is DERIVED from INGEST_TABLES_BY_MODULE, so a feed added to this registry is
# watched the same day — the list cannot fall behind the registry because it IS the registry.
#
# LOCKED SO IT CANNOT UN-WIRE. `harness_feed_watchdog.py` fails the build when a registered ingest table
# is neither watched nor explicitly excused here, when a MONTHLY archive is watched (the 2026-08-30 false
# alarm), when a watched table has no declared data-date column, or when a caller re-implements either
# map. An omission is therefore impossible; only a DECLARED exclusion, with its reason, compiles.

# How many days without new data makes a feed late. A feed's cadence is a property OF THE FEED, so it is
# declared, never inferred from how long the table has been quiet.
CADENCE_DAILY = 1
CADENCE_WEEKLY = 7
CADENCE_MONTHLY = 31

# ── WHICH COLUMN MEANS "THE DATE OF THE THING" ────────────────────────────────────────────────────
# NOT the same question as FRESHNESS_COLUMN_BY_TABLE above, and the whole diagnosis needs both: that one
# says when a row ARRIVED, this one says what day the DATA is about. An old arrival means the file
# stopped coming; a recent arrival carrying an old data date means the file still comes and its content
# is frozen — a different phone call, as `_table_feed_freshness` already documents.
#
# `None` means the table carries no single column naming its own day (it is period-keyed, or it is not
# yet populated on any org here so no column can be declared honestly). Such a feed is watched on
# ARRIVAL only. `None` is a DECLARATION — the lock requires an entry for every watched table, so a
# missing column is a stated fact rather than a silent gap.
DATA_DATE_COLUMN_BY_TABLE = {
    # sales — the live hourly feed and its monthly archive (the archive is not watched; see below)
    LIVE_SALES_FEED: "trans_date",
    MONTHLY_SALES: "trans_date",
    "raw_sales_invoice": "trans_date",
    "raw_sales_invoice_tender": "trans_date",
    # carrier / processor money feeds
    "raw_payment_detail": "payment_date",
    "raw_comp_report": "begin_date",          # the statement's own coverage start
    "raw_ma_commission": "tx_date",
    MA_DAILY_TX: "tx_date",
    "raw_ma_fulfillment": "date_ordered",
    # period-keyed snapshots: one snapshot per month, replaced not appended, so no row names a day
    "raw_mi": None,
    COMMISSION_PER_DEVICE_FEED: None,
    # day-grain operational feeds
    "raw_dlar_rep": "as_of_date",
    "raw_dlar_store": "as_of_date",
    "pos_tender_summary": "close_date",
    "inventory_value": "as_of_date",
    "asset_ledger": "acquired_date",          # when the distributor booked the unit to us
    # Unpopulated on every org in this deployment, so no data-date column is declared from a real row.
    # Watched on arrival; add the column here the day one lands (the lock makes that a conscious change).
    EPAY_DAILY: None,
    "merchant_settlement_day": None,
    "merchant_settlement_batch": None,
    "pos_builtin_daily_sales": None,
    "raw_sales_product": None,
    "royalty_report": None,
    "ma_overview_upload": None,
}

# ── HOW OFTEN EACH WATCHED FEED IS DUE ────────────────────────────────────────────────────────────
# The default is daily, deliberately: a feed nobody thought about is better watched too keenly than not
# at all. A slower feed says so here.
FEED_CADENCE_BY_TABLE = {
    "raw_mi": CADENCE_MONTHLY,                # one monthly subscriber snapshot
    COMMISSION_PER_DEVICE_FEED: CADENCE_MONTHLY,
    "royalty_report": CADENCE_MONTHLY,        # the franchisor's monthly statement
    "ma_overview_upload": CADENCE_MONTHLY,
    "raw_ma_fulfillment": CADENCE_WEEKLY,
    "inventory_value": CADENCE_WEEKLY,        # an aging snapshot, not a transaction stream
}
DEFAULT_FEED_CADENCE = CADENCE_DAILY

# ── FEEDS DELIBERATELY NOT WATCHED, each with its reason ──────────────────────────────────────────
# A registered ingest table absent from this map AND absent from DATA_DATE_COLUMN_BY_TABLE fails the
# build. So this is the only way a feed goes unwatched, and it costs a sentence saying why.
NOT_WATCHED_REASONS = {
    # The monthly side of a LIVE_VS_MONTHLY pair. Watching it IS the 2026-08-30 false alarm: it moves
    # only at month close, so it reads "stale" while the live feed is current. Measured 2026-10-03 —
    # raw_sales' newest row was 2026-08-31 while daily_sales_feed carried that same day's trading.
    MONTHLY_SALES: "monthly archive of the sales pair — freshness reads the live feed",
    "pos_builtin_sales": "monthly archive of the pos_builtin pair — freshness reads the daily stream",
    # Already watched, per report_key, by the custom-import probe — watching the shared JSONB table
    # would answer for whichever report arrived last and hide the one that stopped.
    CUSTOM_CAPTURE: "watched per report_key by the custom-import probe, not as one table",
    # Reference data replaced wholesale when the catalog changes. It has no cadence to be late against.
    "raw_catalog": "reference catalog, replaced on change — no arrival cadence",
    "raw_categories": "reference catalog, replaced on change — no arrival cadence",
    # Org setup / template uploads: they arrive when a human changes the org, not on a schedule.
    "store_mapping": "org setup upload — arrives when the org changes, not on a cadence",
    "storeops.employees": "org setup upload — arrives when the org changes, not on a cadence",
    "storeops.stores": "org setup upload — arrives when the org changes, not on a cadence",
    "storeops.store_alias": "org setup upload — arrives when the org changes, not on a cadence",
    "storeops.store_merchant_id": "org setup upload — arrives when the org changes, not on a cadence",
    "closing_tender_def": "org config sheet — arrives when the org changes it",
    "closing_tender_map": "org config sheet — arrives when the org changes it",
    "storeops.platform_billing_connector": "connector config row, not a data feed",
    # Entered by people in the app, so an absence is a staffing fact, not a feed fault. The closing
    # module raises its own exceptions for a missing declaration.
    DAILY_CLOSING: "entered by employees in-app — the closing module reports a missing declaration",
    # On-demand pulls with no promised cadence; a stale price list is a price question, not an outage.
    "vendor_catalog_price": "on-demand vendor price pull — no promised cadence",
    "storeops.google_review_store": "review sweep — a quiet week is not an outage",
    "storeops.google_review_snapshot": "review sweep — a quiet week is not an outage",
    "storeops.google_review_item": "review sweep — a quiet week is not an outage",
    # Line children of a watched parent: the parent's arrival is the feed's arrival, and watching both
    # would report one outage twice.
    "royalty_report_line": "line child of royalty_report, which is watched",
    # The `pos` schema is outside the commcalc-scoped probe's reach. Stated rather than omitted, so
    # extending the probe to that schema is a visible change to this line.
    "pos.sales": "pos schema — outside the commcalc-scoped probe; watch when the probe spans schemas",
    "pos.receipt_imports": "pos schema — outside the commcalc-scoped probe",
    "pos.customers": "pos schema — outside the commcalc-scoped probe",
    "pos.activations": "pos schema — outside the commcalc-scoped probe",
    "activation_rebate_ledger": "aggregate written by the pos module from its own feeds, not an ingest",
}


def data_date_column(table: str):
    """PURE: the column naming the DAY THE DATA IS ABOUT for `table`, or None when the table carries no
    such column (period-keyed, or unpopulated so none is declared). Distinct from freshness_column(),
    which names when a row ARRIVED — see the header above."""
    return DATA_DATE_COLUMN_BY_TABLE.get(table)


def feed_cadence_days(table: str) -> int:
    """PURE: how many days without new data makes `table` late. Declared per feed; daily by default."""
    return int(FEED_CADENCE_BY_TABLE.get(table, DEFAULT_FEED_CADENCE))


def is_monthly_archive(table: str) -> bool:
    """PURE: True when `table` is the MONTHLY side of a live/monthly pair — the side a freshness check
    must never measure."""
    return any(table == pair[1] for pair in LIVE_VS_MONTHLY_PAIRS.values())


def watched_feeds() -> tuple:
    """PURE: every registered ingest feed a freshness check must cover, DERIVED from
    INGEST_TABLES_BY_MODULE — so a feed registered today is watched today.

    Each entry: {table, module, data_date_column, cadence_days}. `data_date_column` None means watch
    ARRIVAL only. Tables named in NOT_WATCHED_REASONS are excluded, by declaration; the harness fails
    the build on any registered table that is in neither place, so the set cannot quietly shrink."""
    out = []
    for module, tables in INGEST_TABLES_BY_MODULE.items():
        for t in tables:
            if t in NOT_WATCHED_REASONS:
                continue
            out.append({"table": t, "module": module,
                        "data_date_column": data_date_column(t),
                        "cadence_days": feed_cadence_days(t)})
    return tuple(out)


def unwatched_reason(table: str):
    """PURE: why `table` is deliberately not watched, or None when it IS watched."""
    return NOT_WATCHED_REASONS.get(table)


# ── HOW A FEED IS NAMED TO A HUMAN ────────────────────────────────────────────────────────────────
# A watchdog line a tenant cannot read is a line nobody acts on, and "raw_comp_report is 7 days late"
# is not a sentence an owner should have to decode. RULE TWO holds: these are the names of OUR tables
# and the reports they carry, never a carrier, tenant or product branch — the carrier-vocabulary guard
# reads this file. Anything not named here derives a readable label from the table itself, so a new
# feed is never unnamed; naming it well is a one-line change.
FEED_LABEL_BY_TABLE = {
    LIVE_SALES_FEED: "Sales transactions (live feed)",
    MONTHLY_SALES: "Sales transactions (monthly reconciliation)",
    "raw_payment_detail": "Payment detail",
    "raw_comp_report": "Compensation report (commission basis)",
    "raw_mi": "Subscriber base snapshot",
    "raw_dlar_rep": "Daily activity by rep",
    "raw_dlar_store": "Daily activity by store",
    "raw_ma_commission": "Master-agent commission",
    MA_DAILY_TX: "Master-agent daily transactions",
    "raw_ma_fulfillment": "Master-agent fulfilment orders",
    "pos_tender_summary": "Register tender summary",
    "inventory_value": "Inventory aging snapshot",
    "asset_ledger": "Asset lending ledger (device cost)",
    COMMISSION_PER_DEVICE_FEED: "Per-device rebate history",
    EPAY_DAILY: "Settlement transactions",
    "merchant_settlement_day": "Card settlement by day",
    "merchant_settlement_batch": "Card settlement by batch",
    "raw_sales_product": "Sales by product",
    "raw_sales_invoice": "Sales by invoice",
    "raw_sales_invoice_tender": "Sales by invoice tender",
    "pos_builtin_daily_sales": "In-house register sales (daily)",
    "royalty_report": "Royalty statement",
    "ma_overview_upload": "Master-agent overview",
}


def feed_label(table: str) -> str:
    """PURE: the human name for `table` — the declared label, else one derived from the table name, so
    every feed has a readable name and none is left as a bare identifier."""
    lbl = FEED_LABEL_BY_TABLE.get(table)
    if lbl:
        return lbl
    base = table.split(".")[-1]
    for prefix in ("raw_", "pos_builtin_"):
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    return base.replace("_", " ").strip().capitalize() or table
