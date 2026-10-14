-- 1083_prospect_store_declared_input.sql
-- ────────────────────────────────────────────────────────────
-- WHAT THE SELLER DECLARES, PER STORE: ACCESSORY SALES AND THIS SITE'S RENT
-- (owner ask 2026-10-14, index §COMMCALC-03.26)
--
-- Owner, verbatim: "add a line for accessories sales as declared by seller to include in gorss
-- profit and then calculate the net income, the cell sould be able to fill in manually and include
-- the rent cell also for each store, if the rent is declared then the suystem will automaticalluy
-- subtract our avg rent from the expenses if the rent is nto declared then it will keep our average
-- as a part of the expense or the bettwe rway is to create a cell with ouer rent and if it is diff
-- then it can vbe user overwritten"
--
-- The owner named two designs for the rent and chose the second himself, so the cell OPENS on our
-- own average and an entered figure replaces it for that store alone.
--
-- WHY A TABLE AND NOT COLUMNS. `store_prospect.actual_rent_monthly` already holds ONE rent for the
-- whole prospect, and the obvious cheap move is to add two more columns beside it. That cannot
-- carry the ask: both figures are PER STORE, and a prospect's stores are not known until its feeds
-- are uploaded (they are derived from the uploaded rows by commcalc/prospect_eval.store_index, which
-- is why there is no prospect_store table to hang a column on). A row per (prospect, store) is the
-- only shape that fits a roster nobody declared in advance.
--
-- WHY THESE TWO FACTS ARE DIFFERENT FROM EVERY OTHER FIGURE ON THE REPORT. §COMMCALC-03's whole
-- design is that a prospect is measured through the company's own booking code over the prospect's
-- own uploaded feeds — nothing is entered, so nothing can be wished into being profitable. These
-- two are the exceptions the feeds CANNOT answer, and the P&L vocabulary already said so: both
-- `accessory_rev` and `accessory_cost` are listed in `prospect_eval.NEEDS_POS_FEED`, the map of
-- lines that need a point-of-sale feed a store we do not operate will never give us. A seller's
-- declaration is the only measurement of that hole that exists, so it is STORED AS A DECLARATION
-- and rendered as one — never folded into the measured figures.
--
-- NO NEW RULE SHIPS WITH IT (duplicate check, house index rules):
--   · the accessory COST rate is coa's own `account_config.accessory_cogs_pct` (mig 611, the rate
--     the owner set to 20% on 2026-06-20), dereferenced and injected. Booking a declared sale with
--     no cost would show a gross profit the store has never earned; a second rate here would be the
--     divergence the index rules forbid.
--   · the rent substitution is `prospect_eval.apply_rent_override`, which has replaced an averaged
--     rent with an entered one since §COMMCALC-03.6. It is now applied ONCE PER COLUMN off the
--     averaged matrix instead of once for the whole prospect — never chained, because a second pass
--     would record the first override as "the average it replaced".
--   · the M1 / M2–M12 split added in the same PR introduces no table and no column: it reads
--     `ma_store_pnl.ma_earned_month_ladder` and `ma_received_month_ladder`.
--
-- NULL IS NOT ZERO, and the table is built so the two cannot be confused. A NULL cell means the
-- seller has declared nothing: the column then charges our average rent and books no accessory
-- revenue. A declared 0.00 is a CLAIM — that this site pays no rent, or sells no accessories — and
-- is charged as such. The endpoint clears a cell to NULL and never writes 0.00 to mean "blank".
--
-- Additive and idempotent. One new empty table; no existing table, column or row is touched, and
-- no live reader reads it. Applying this changes no figure on any live report.
--
-- MONEY: this migration moves no money. It creates an empty table.
--
-- REVERT:
--   DROP TABLE IF EXISTS commcalc.prospect_store_input;
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commcalc.prospect_store_input (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                    UUID NOT NULL,
  prospect_id               UUID NOT NULL REFERENCES commcalc.store_prospect (id) ON DELETE CASCADE,
  -- The store column this cell sits on: the key `prospect_eval.store_index` builds from the
  -- prospect's OWN uploaded rows (the processor account the feeds name). Deliberately not a
  -- store_code and not an address — a prospect has no identity any live reader could resolve, which
  -- is the safety property §COMMCALC-03 is built on, and a key naming no column is REPORTED by the
  -- evaluation rather than silently added to a total nobody can drill into.
  store_key                 TEXT NOT NULL,
  -- Monthly accessory sales AS THE SELLER STATES THEM. NULL = declared nothing. Gross sales, not
  -- margin: the cost is taken at the org's own accessory_cogs_pct and both halves are shown, so the
  -- declaration can be argued with rather than trusted.
  declared_accessory_sales  NUMERIC,
  -- This site's own monthly rent. NULL = declared nothing, and the column then carries the average
  -- monthly rent our own stores pay, labelled as an average.
  declared_rent_monthly     NUMERIC,
  updated_at                TIMESTAMPTZ DEFAULT NOW(),
  created_at                TIMESTAMPTZ DEFAULT NOW(),
  -- ONE row per store per prospect. Two rows would be two declarations for one counter, drifting
  -- apart exactly as two P&L paths would; the endpoint upserts on this constraint.
  UNIQUE (prospect_id, store_key)
);

CREATE INDEX IF NOT EXISTS prospect_store_input_org
  ON commcalc.prospect_store_input (org_id, prospect_id);

-- Same posture as the two tables mig 1077 created: RLS on, one permissive policy, because every
-- read and write goes through the org-scoped FastAPI endpoints (`require_org`) and nothing here is
-- reachable from a browser session directly.
ALTER TABLE commcalc.prospect_store_input ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'commcalc' AND tablename = 'prospect_store_input' AND policyname = 'open_all'
  ) THEN
    CREATE POLICY open_all ON commcalc.prospect_store_input FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;
