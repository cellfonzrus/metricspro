-- 854 — capture the transaction clock time (for the staffing heat map)
-- The POS 'Sales Transaction Details' export carries a 'Trans Date Time' column, but the ingest
-- truncated it to a DATE ([:10]) — so hour-of-day was thrown away at import. These nullable columns let
-- new uploads keep the full timestamp; the ingest stamps them only when the cell has a real clock time
-- (date-only rows stay NULL). Historical rows cannot be backfilled — the time was never stored.
ALTER TABLE commcalc.raw_sales        ADD COLUMN IF NOT EXISTS trans_ts TIMESTAMPTZ;
ALTER TABLE commcalc.daily_sales_feed ADD COLUMN IF NOT EXISTS trans_ts TIMESTAMPTZ;

-- Hour-of-day / weekday bucketing per store reads these; index the common filter.
CREATE INDEX IF NOT EXISTS raw_sales_trans_ts        ON commcalc.raw_sales        (org_id, store, trans_ts) WHERE trans_ts IS NOT NULL;
CREATE INDEX IF NOT EXISTS daily_sales_feed_trans_ts ON commcalc.daily_sales_feed (org_id, store, trans_ts) WHERE trans_ts IS NOT NULL;
