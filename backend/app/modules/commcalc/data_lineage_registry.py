"""Canonical source-of-truth registry — the ONE place code names a data table.

WHY THIS EXISTS (owner 2026-08-30). The data-freshness banner read commcalc.raw_sales (the MONTHLY
reconciliation upload) when the LIVE feed is commcalc.daily_sales_feed, so it cried "stale since 8-09"
while the numbers were current. Nothing in code stopped it — the fact "which table is the live sales
feed" lived only in a human's head and, after the fact, in a doc. This module makes that fact CODE:

    • Read a data source, or add a metric/report? Reference the constants here — never hardcode a raw
      table name at the call site. If the source-of-truth table ever changes, it changes in ONE place.
    • The machine-readable dependency map is still commcalc.data_lineage (migrations 924/925) +
      docs/DATA_LINEAGE.md — query it for "what does X touch?". THIS module is the small runtime slice
      of that map that code actually dereferences, kept in sync with the SQL seed by
      harness_data_lineage_guard.py (which fails if they drift, an ingest table has no lineage edge, or
      freshness stops pointing at the live feed).

DON'T DUPLICATE, DON'T MISS. Before wiring a new feed or metric:
  1. Is its table already a value here / an edge in 925_data_lineage_seed.sql? Then reuse it — do not
     stand up a second capture path for the same data (that was the "duplicate" risk).
  2. New source? Add its constant here AND an 'ingest' edge to the seed, then re-run the guard. The
     guard refuses a raw ingest table that has no lineage edge, so a new feed can't land undocumented
     (that was the "miss" risk).

Pure data + tiny pure helpers: no DB, no network, no heavy imports. Safe to import anywhere.
"""

# ── SALES ────────────────────────────────────────────────────────────────────────────────────────
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

# ── OTHER RAW INGEST TARGETS (kept so the guard can prove each has a lineage edge) ────────────────
# b2b custom sheets (Activation Details, Bill Payment, Sales by Product, Store Performance) → JSONB.
CUSTOM_CAPTURE = "raw_custom_import"
EPAY_DAILY = "raw_epay_daily_tx"          # Boost ePay settlement
MA_DAILY_TX = "raw_ma_daily_tx"           # VidaPay / Total MA daily transactions
DAILY_CLOSING = "daily_closing"           # employee cash + tender declaration

# Every raw ingest table this registry knows. The guard asserts each appears as an 'ingest' edge in
# 925_data_lineage_seed.sql — so a new feed cannot land without being documented in the lineage map.
RAW_INGEST_TABLES = (
    LIVE_SALES_FEED,
    MONTHLY_SALES,
    CUSTOM_CAPTURE,
    EPAY_DAILY,
    MA_DAILY_TX,
    DAILY_CLOSING,
)


def freshness_source(item: str = "sales") -> str:
    """The table a freshness/"is data flowing?" check must measure for a logical feed. For sales this
    is the LIVE feed (daily_sales_feed), never the monthly upload — measuring the monthly table is the
    2026-08-30 false-alarm this registry guards against."""
    return {"sales": LIVE_SALES_FEED}.get(item, LIVE_SALES_FEED)


def display_sources(item: str = "sales") -> tuple:
    """The table(s) a DISPLAY aggregation reads for a logical item (the union for sales)."""
    return {"sales": SALES_DISPLAY_SOURCES}.get(item, (freshness_source(item),))
