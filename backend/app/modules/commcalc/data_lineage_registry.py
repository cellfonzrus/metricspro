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

# ── LIVE-vs-MONTHLY PAIRS — the freshness trap, per feed ──────────────────────────────────────────
# A freshness / "is data flowing?" check must read the LIVE (first) table, NEVER the monthly (second).
# Reading the monthly table is the 2026-08-30 false alarm this registry guards against. Each new pair a
# module introduces (e.g. the POS builtin stream) belongs here so the guard can hold the invariant.
LIVE_VS_MONTHLY_PAIRS = {
    "sales": (LIVE_SALES_FEED, MONTHLY_SALES),
}

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
    ),
    # Other modules are added in subsequent PRs, one by one.
}


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
