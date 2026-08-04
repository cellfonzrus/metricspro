-- 268_commission_ma_overview_recon.sql — mod-commission · MA "Overview of Accounts" RECONCILIATION
--
-- WHY (owner directive, in chat 2026-08-04): the Total Wireless / VidaPay master-agent portal publishes an
-- "Overview of Accounts" report whose tiles state, for one period, the dealer's Activation Count, TWP Count,
-- Residual, Rebates Paid, Fees Margin Paid, Commissions Paid, Commissions Not Eligible, Edge (Device
-- Finance) count and Appeal Count. The owner wants those STATED numbers stored next to the SAME tiles
-- computed from our own ingested data (commcalc.raw_ma_commission / raw_ma_daily_tx, mig 083), with a delta
-- per tile and a per-merchant-account drill-down — "so all activations and commission paid can be
-- cross checked with this report to check the validity of the data in our system".
--
-- THIS MIGRATION MOVES $0, AND SO DOES THE CODE THAT READS IT. The recon surface is READ-ONLY over the
-- raw MA feed: it never writes rep_commissions, commission plans, payout schedules, the commission ledger
-- or any pay table, and it never triggers a recalculation. It COMPARES and REPORTS. The only rows it ever
-- writes are the uploaded overview report's own stated numbers (table 1 below) and the tenant's tile
-- mapping (table 2).
--
-- RULE TWO (SAP-configurable): the tile -> source mapping is DATA, not code. Every tile names its own
-- source table, aggregate, money columns, sign convention and row filter in commcalc.ma_overview_tile_config,
-- seeded here with the Total-Wireless defaults and editable per tenant on /commcalc/ma-overview-recon
-- (⚙ Tile mapping). NOTHING in the compute path branches on a carrier or tenant name.
--
-- RULE ONE (multi-tenant): both tables carry org_id NOT NULL with an index; every read and write in the
-- code is .eq("org_id", org_id) from the QUERY PARAM. The house seed below is a SEED, not the resolution
-- rule — a tenant with no rows of its own falls back to the CODE defaults in
-- backend/app/modules/commcalc/ma_overview.py (DEFAULT_TILES), which are identical to this seed. That is
-- why luxelink (or any future TW tenant) works before anyone runs a per-tenant seed.
--
-- TWO TILES ARE DELIBERATELY LEFT UNMAPPED (agg='none'): 'Commissions Not Eligible' and 'Appeal Count'.
-- The source report's exact definition of those two is NOT known (see the OPEN QUESTIONS block in
-- docs/handoffs/commission.md), and the directive is explicit: render an honest "no source mapped" state
-- rather than a fake 0. The page instead shows the candidate value distributions (raw_ma_commission
-- .line_status / .suspension_reason) so the owner can pick the right filter and save it as config.
--
-- ⚠️ IDEMPOTENCE NOTE (2026-08-04): a uniqueness rule that includes a NULLABLE column does NOT make an
-- INSERT idempotent — Postgres treats NULLs as DISTINCT inside a UNIQUE constraint, so ON CONFLICT never
-- fires. That defect reached production once here (the rate-plan seed ran twice); it is fixed below with
-- a COALESCE-based expression index plus a WHERE NOT EXISTS seed. Every other uniqueness rule in this
-- file is over NOT NULL columns only (ma_overview_upload: org_id/period/merchant_account_id;
-- ma_overview_tile_config: org_id/tile_key), which is why those ON CONFLICT clauses are sound.
--
-- ADDITIVE + IDEMPOTENT + RLS-ZERO-POLICY: safe to re-run; RLS on, NO policies, NO anon/authenticated
-- grants (contract §5 — all access is via the backend service role). Degrades gracefully: until this runs,
-- the recon page reads the code-default tiles, computes the system side from the raw tables via the
-- fallback paged scan, and shows "no uploaded report stored yet" instead of erroring.

-- ── 1) the UPLOADED report: one row per (tenant, period, merchant account) ──────────────────────────
-- merchant_account_id = '*' is the RESERVED sentinel for a report-level TOTAL row — used when the export
-- carries only the tiles and no per-account breakdown. The recon prefers per-account rows and falls back
-- to the '*' row for the tile totals; both can coexist for the same period.
CREATE TABLE IF NOT EXISTS commcalc.ma_overview_upload (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   UUID NOT NULL,
  period                   TEXT NOT NULL,            -- canonical 'Month YYYY' (code writes _canon_period)
  period_month             INT,
  period_year              INT,
  merchant_account_id      TEXT NOT NULL DEFAULT '*',
  account_name             TEXT,
  carrier_name             TEXT,
  -- the STATED tile metrics, exactly as the source report words them
  activation_count         NUMERIC,
  twp_count                NUMERIC,
  residual                 NUMERIC,
  rebates_paid             NUMERIC,
  fees_margin_paid         NUMERIC,
  commissions_paid         NUMERIC,
  commissions_not_eligible NUMERIC,
  edge_count               NUMERIC,
  appeal_count             NUMERIC,
  extra                    JSONB,                    -- every unmapped column of the source row, verbatim
  source_file              TEXT,
  uploaded_by              TEXT,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotent re-upload: REPLACE by (org, period, account). A re-upload of July can never touch June.
CREATE UNIQUE INDEX IF NOT EXISTS ma_overview_upload_key
  ON commcalc.ma_overview_upload (org_id, period, merchant_account_id);
CREATE INDEX IF NOT EXISTS ma_overview_upload_org_period
  ON commcalc.ma_overview_upload (org_id, period);

COMMENT ON TABLE commcalc.ma_overview_upload IS
  'The master-agent portal''s "Overview of Accounts" report AS STATED (VidaPay / Total Access), one row '
  'per (org, period, merchant account); merchant_account_id=''*'' is a report-level TOTAL row. Input to '
  'the /commcalc/ma-overview-recon cross-check ONLY — no payout path reads this table.';

-- ── 2) the tile -> source MAPPING (RULE TWO) ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.ma_overview_tile_config (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  tile_key       TEXT NOT NULL,
  label          TEXT NOT NULL,
  sort_order     INT  NOT NULL DEFAULT 500,
  value_format   TEXT NOT NULL DEFAULT 'count',        -- 'count' | 'money'
  -- SYSTEM side (what we compute from our ingested data)
  source_table   TEXT NOT NULL DEFAULT 'raw_ma_commission',  -- 'raw_ma_commission' | 'raw_ma_daily_tx' | ''
  agg            TEXT NOT NULL DEFAULT 'count',        -- 'count' | 'sum' | 'none' (none = not mapped)
  value_fields   TEXT,                                 -- comma list of numeric columns, for agg='sum'
  sign           TEXT NOT NULL DEFAULT 'as_is',        -- 'as_is' | 'negate' | 'abs'
  filter_field   TEXT,                                 -- a DIMENSION column (whitelisted in code)
  filter_op      TEXT,                                 -- eq|neq|in|not_in|contains|nonblank|blank|truthy
  filter_value   TEXT,                                 -- comma list (in/not_in/eq) or substring (contains)
  filters        JSONB,                                -- [{field,op,value}, ...] ANDed; wins over the
                                                       -- single triplet above (which stays for the
                                                       -- simple one-condition case + back-compat)
  -- UPLOADED side (where the stated number comes from)
  uploaded_field TEXT,                                 -- a metric column on ma_overview_upload
  uploaded_aliases TEXT,                               -- comma list of source-file header spellings
  -- delta severity: |delta| <= tolerance_abs OR |delta%| <= tolerance_pct => 'ok'
  tolerance_abs  NUMERIC NOT NULL DEFAULT 0,
  tolerance_pct  NUMERIC NOT NULL DEFAULT 0,
  is_active      BOOLEAN NOT NULL DEFAULT true,
  note           TEXT,
  updated_by     TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, tile_key)
);
CREATE INDEX IF NOT EXISTS ma_overview_tile_config_org
  ON commcalc.ma_overview_tile_config (org_id, sort_order);

-- Idempotent UPGRADE path: CREATE TABLE IF NOT EXISTS will not add a column to a table that already
-- exists, so every column added after the first release is also stated as an ALTER. Re-running this file
-- on an older 268 install therefore brings it fully up to date.
ALTER TABLE commcalc.ma_overview_tile_config ADD COLUMN IF NOT EXISTS filters JSONB;

COMMENT ON TABLE commcalc.ma_overview_tile_config IS
  'Per-tenant mapping of each MA "Overview of Accounts" tile to (a) the column of the uploaded report '
  'that STATES it and (b) the raw_ma_* aggregate that COMPUTES it. Absent rows => the code defaults in '
  'ma_overview.DEFAULT_TILES. Read-only with respect to money: nothing here decides a payout.';

-- ── 2b) the M1–M6 COMMISSION RATE PLAN (owner answer 2026-08-04) ───────────────────────────────────
-- Owner verbatim: "the current commission plan for total is M1 = 50%, M2-M6 = 75% each plus any
-- applicable spiff which change from time to time; M3-M6 is also a temporary spiff which can change over
-- a period of time." Rates that change over time are CONFIG, never constants (RULE TWO) — and because
-- they change, they are EFFECTIVE-DATED: the row that applies to a period is the newest one whose
-- effective_from is on or before that period's first day (NULL = always in force).
--
-- THIS TABLE PAYS NOBODY. It drives the EXPECTED column on the recon page (rate% x MRC + flat spiff),
-- which is a cross-check against the carrier's stated figure. No payout path reads it: rep pay is
-- POS sales x Commission Plans, and this is the CARRIER's plan, not a rep's.
CREATE TABLE IF NOT EXISTS commcalc.ma_commission_month_rate (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  month_index    INT  NOT NULL CHECK (month_index BETWEEN 1 AND 6),
  rate_pct       NUMERIC NOT NULL DEFAULT 0,     -- percent OF the line's MRC
  spiff_flat     NUMERIC NOT NULL DEFAULT 0,     -- flat $ per activation ON TOP of the percentage
  effective_from DATE,                           -- NULL = always in force
  note           TEXT,
  updated_by     TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
  -- NO table-level UNIQUE here on purpose — see the expression index below.
);
CREATE INDEX IF NOT EXISTS ma_commission_month_rate_org
  ON commcalc.ma_commission_month_rate (org_id, month_index);

-- ── UNIQUENESS FOR THE RATE PLAN — an EXPRESSION index, not a table constraint ─────────────────────
-- ⚠️ DEFECT FIXED HERE (found in PRODUCTION 2026-08-04 by running 268 and then 268b). The original
-- `UNIQUE (org_id, month_index, effective_from)` did NOT prevent a duplicate seed: the seed rows leave
-- effective_from NULL, Postgres treats NULLs as DISTINCT in a unique constraint, so no conflict ever
-- arose and `ON CONFLICT DO NOTHING` never fired — every month_index was inserted TWICE. The fix is a
-- unique index over COALESCE(effective_from, '0001-01-01'), which makes "no start date" a real, single
-- value. The seed below additionally uses WHERE NOT EXISTS rather than ON CONFLICT, so it is idempotent
-- even on a database where neither the constraint nor the index exists yet.
--
-- This block is correct on EVERY state: a fresh database, a 268-only database, a 268+268b database that
-- still holds the duplicate rows, and the owner's post-cleanup database (where the constraint is already
-- dropped and an index of this exact name already exists). Each step is individually conditional.

-- 1. drop the unenforceable constraint (auto-named by Postgres; also try the explicit name).
ALTER TABLE commcalc.ma_commission_month_rate
  DROP CONSTRAINT IF EXISTS ma_commission_month_rate_org_id_month_index_effective_from_key;
ALTER TABLE commcalc.ma_commission_month_rate
  DROP CONSTRAINT IF EXISTS ma_commission_month_rate_uq;

-- 2. DEDUPE before indexing — otherwise CREATE UNIQUE INDEX fails on a dirty table and the whole
--    migration aborts. Keeps the EARLIEST row of each (org, month, effective-date) group (ctid breaks
--    a tie when created_at is identical, which it is for rows inserted by the same statement).
DELETE FROM commcalc.ma_commission_month_rate a
 USING commcalc.ma_commission_month_rate b
 WHERE a.org_id = b.org_id
   AND a.month_index = b.month_index
   AND COALESCE(a.effective_from, '0001-01-01'::date) = COALESCE(b.effective_from, '0001-01-01'::date)
   AND (a.created_at, a.ctid) > (b.created_at, b.ctid);

-- 3. the real uniqueness rule.
CREATE UNIQUE INDEX IF NOT EXISTS ma_commission_month_rate_uq
  ON commcalc.ma_commission_month_rate (org_id, month_index, COALESCE(effective_from, '0001-01-01'::date));

COMMENT ON TABLE commcalc.ma_commission_month_rate IS
  'The CARRIER''s M1-M6 commission plan (percent of MRC + any flat spiff), per tenant and effective-'
  'dated. Drives the EXPECTED column of the MA Overview cross-check ONLY. Pays nobody: rep pay is POS '
  'sales x Commission Plans and never reads this table.';

-- ── 3) RLS: enabled, ZERO policies, ZERO anon/authenticated grants (contract §5) ────────────────────
ALTER TABLE commcalc.ma_overview_upload         ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.ma_overview_tile_config    ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.ma_commission_month_rate   ENABLE ROW LEVEL SECURITY;

-- ── 4) AGGREGATE IN POSTGRES — the two recon cubes ──────────────────────────────────────────────────
-- The tile mapping is config, so we cannot pre-compute one number per tile in SQL. Instead each RPC
-- returns a small CUBE: one row per (merchant account x every dimension a tile may filter on) carrying
-- the row count and every money column summed. Cardinality is a few hundred rows for a real month, so
-- the whole recon (all tiles + the per-account table + the delta explainers) is ONE round trip per source
-- table instead of paging tens of thousands of rows into Python.
--
-- p_periods is an ARRAY so the caller passes _pvariants(period) — 'June 2026' AND '2026-06' — which is the
-- recurring period-spelling bug class in this module.
DROP FUNCTION IF EXISTS commcalc.ma_overview_commission_cube(uuid, text[], text[]);
CREATE OR REPLACE FUNCTION commcalc.ma_overview_commission_cube(
  p_org uuid, p_periods text[], p_accounts text[] DEFAULT NULL)
RETURNS TABLE (
  merchant_account_id text,
  activation_type     text,
  activation_type2    text,
  sub_type            text,
  line_status         text,
  suspension_reason   text,
  is_financed         text,
  port_status         text,
  perfect_sale        text,
  rows_n              bigint,
  orders_n            bigint,
  imei_n              bigint,
  imei_blank_n        bigint,
  device_margin       numeric,
  consumer_margin     numeric,
  consumer_financing  numeric,
  rebate              numeric,
  wallet_funding      numeric,
  fees                numeric,
  fees_margin         numeric,
  mrc_net_discount    numeric,
  consumer_value      numeric,
  spiff_m1            numeric,
  spiff_m2            numeric,
  spiff_m3            numeric,
  spiff_m4            numeric,
  spiff_m5            numeric,
  spiff_m6            numeric,
  min_tx_date         date,
  max_tx_date         date
)
LANGUAGE sql
STABLE
AS $$
  SELECT
    coalesce(nullif(btrim(c.merchant_account_id), ''), '?')          AS merchant_account_id,
    coalesce(btrim(c.activation_type),   '')                        AS activation_type,
    coalesce(btrim(c.activation_type2),  '')                        AS activation_type2,
    coalesce(btrim(c.sub_type),          '')                        AS sub_type,
    coalesce(btrim(c.line_status),       '')                        AS line_status,
    coalesce(btrim(c.suspension_reason), '')                        AS suspension_reason,
    coalesce(btrim(c.is_financed),       '')                        AS is_financed,
    coalesce(btrim(c.port_status),       '')                        AS port_status,
    coalesce(btrim(c.perfect_sale),      '')                        AS perfect_sale,
    count(*)                                                        AS rows_n,
    count(DISTINCT nullif(btrim(c.activation_order), ''))           AS orders_n,
    count(DISTINCT nullif(btrim(c.imei), ''))                       AS imei_n,
    count(*) FILTER (WHERE coalesce(btrim(c.imei), '') = '')        AS imei_blank_n,
    coalesce(sum(c.device_margin),      0) AS device_margin,
    coalesce(sum(c.consumer_margin),    0) AS consumer_margin,
    coalesce(sum(c.consumer_financing), 0) AS consumer_financing,
    coalesce(sum(c.rebate),             0) AS rebate,
    coalesce(sum(c.wallet_funding),     0) AS wallet_funding,
    coalesce(sum(c.fees),               0) AS fees,
    coalesce(sum(c.fees_margin),        0) AS fees_margin,
    coalesce(sum(c.mrc_net_discount),   0) AS mrc_net_discount,
    coalesce(sum(c.consumer_value),     0) AS consumer_value,
    coalesce(sum(c.spiff_m1), 0) AS spiff_m1,
    coalesce(sum(c.spiff_m2), 0) AS spiff_m2,
    coalesce(sum(c.spiff_m3), 0) AS spiff_m3,
    coalesce(sum(c.spiff_m4), 0) AS spiff_m4,
    coalesce(sum(c.spiff_m5), 0) AS spiff_m5,
    coalesce(sum(c.spiff_m6), 0) AS spiff_m6,
    min(c.tx_date) AS min_tx_date,
    max(c.tx_date) AS max_tx_date
  FROM commcalc.raw_ma_commission c
  WHERE c.org_id = p_org
    AND (p_periods IS NULL OR array_length(p_periods, 1) IS NULL OR c.period = ANY (p_periods))
    AND (p_accounts IS NULL OR array_length(p_accounts, 1) IS NULL
         OR coalesce(nullif(btrim(c.merchant_account_id), ''), '?') = ANY (p_accounts))
  GROUP BY 1,2,3,4,5,6,7,8,9
$$;

DROP FUNCTION IF EXISTS commcalc.ma_overview_dailytx_cube(uuid, text[], text[]);
CREATE OR REPLACE FUNCTION commcalc.ma_overview_dailytx_cube(
  p_org uuid, p_periods text[], p_accounts text[] DEFAULT NULL)
RETURNS TABLE (
  account_id        text,
  account_name      text,
  order_type        text,
  rows_n            bigint,
  orders_n          bigint,
  retail_cost       numeric,
  merchant_discount numeric,
  merchant_invoice  numeric,
  min_tx_date       date,
  max_tx_date       date
)
LANGUAGE sql
STABLE
AS $$
  SELECT
    coalesce(nullif(btrim(t.account_id), ''), '?')      AS account_id,
    coalesce(max(btrim(t.account_name)), '')            AS account_name,
    coalesce(btrim(t.order_type), '')                   AS order_type,
    count(*)                                            AS rows_n,
    count(DISTINCT nullif(btrim(t.order_number), ''))   AS orders_n,
    coalesce(sum(t.retail_cost),       0)               AS retail_cost,
    coalesce(sum(t.merchant_discount), 0)               AS merchant_discount,
    coalesce(sum(t.merchant_invoice),  0)               AS merchant_invoice,
    min(t.tx_date) AS min_tx_date,
    max(t.tx_date) AS max_tx_date
  FROM commcalc.raw_ma_daily_tx t
  WHERE t.org_id = p_org
    AND (p_periods IS NULL OR array_length(p_periods, 1) IS NULL OR t.period = ANY (p_periods))
    AND (p_accounts IS NULL OR array_length(p_accounts, 1) IS NULL
         OR coalesce(nullif(btrim(t.account_id), ''), '?') = ANY (p_accounts))
  GROUP BY 1,3
$$;

-- The ACCOUNT PROFILE — one row per merchant account with the TRUE distinct counts. The cube above
-- cannot answer "how many distinct activation orders" because count(DISTINCT ...) inside a grouped cube
-- is distinct WITHIN each dimension combination: an order with a plain line AND a TWP line lands in two
-- groups and would be counted twice. That difference is exactly the signal the recon needs ("the portal
-- counts orders, we count rows"), so it gets its own account-level aggregate.
DROP FUNCTION IF EXISTS commcalc.ma_overview_commission_accounts(uuid, text[], text[]);
CREATE OR REPLACE FUNCTION commcalc.ma_overview_commission_accounts(
  p_org uuid, p_periods text[], p_accounts text[] DEFAULT NULL)
RETURNS TABLE (merchant_account_id text, rows_n bigint, orders_n bigint,
               imei_n bigint, imei_blank_n bigint, min_tx_date date, max_tx_date date)
LANGUAGE sql
STABLE
AS $$
  SELECT
    coalesce(nullif(btrim(c.merchant_account_id), ''), '?')   AS merchant_account_id,
    count(*)                                                  AS rows_n,
    count(DISTINCT nullif(btrim(c.activation_order), ''))     AS orders_n,
    count(DISTINCT nullif(btrim(c.imei), ''))                 AS imei_n,
    count(*) FILTER (WHERE coalesce(btrim(c.imei), '') = '')  AS imei_blank_n,
    min(c.tx_date) AS min_tx_date,
    max(c.tx_date) AS max_tx_date
  FROM commcalc.raw_ma_commission c
  WHERE c.org_id = p_org
    AND (p_periods IS NULL OR array_length(p_periods, 1) IS NULL OR c.period = ANY (p_periods))
    AND (p_accounts IS NULL OR array_length(p_accounts, 1) IS NULL
         OR coalesce(nullif(btrim(c.merchant_account_id), ''), '?') = ANY (p_accounts))
  GROUP BY 1
$$;

-- The DATE PROFILE — one row per (transaction date, stored period spelling) — powers the "which rows
-- plausibly explain the delta" panel: rows sitting on the first/last day of the month (a late-night
-- activation the portal counted in the neighbouring month), and rows whose tx_date month does NOT match
-- the period they are filed under (the period-spelling / month-boundary bug class this module keeps
-- hitting). At most a few dozen rows per period.
DROP FUNCTION IF EXISTS commcalc.ma_overview_commission_dates(uuid, text[], text[]);
CREATE OR REPLACE FUNCTION commcalc.ma_overview_commission_dates(
  p_org uuid, p_periods text[], p_accounts text[] DEFAULT NULL)
RETURNS TABLE (tx_date date, period text, rows_n bigint)
LANGUAGE sql
STABLE
AS $$
  SELECT c.tx_date, coalesce(btrim(c.period), '') AS period, count(*) AS rows_n
  FROM commcalc.raw_ma_commission c
  WHERE c.org_id = p_org
    AND (p_periods IS NULL OR array_length(p_periods, 1) IS NULL OR c.period = ANY (p_periods))
    AND (p_accounts IS NULL OR array_length(p_accounts, 1) IS NULL
         OR coalesce(nullif(btrim(c.merchant_account_id), ''), '?') = ANY (p_accounts))
  GROUP BY 1, 2
$$;

DROP FUNCTION IF EXISTS commcalc.ma_overview_dailytx_dates(uuid, text[], text[]);
CREATE OR REPLACE FUNCTION commcalc.ma_overview_dailytx_dates(
  p_org uuid, p_periods text[], p_accounts text[] DEFAULT NULL)
RETURNS TABLE (tx_date date, period text, rows_n bigint)
LANGUAGE sql
STABLE
AS $$
  SELECT t.tx_date, coalesce(btrim(t.period), '') AS period, count(*) AS rows_n
  FROM commcalc.raw_ma_daily_tx t
  WHERE t.org_id = p_org
    AND (p_periods IS NULL OR array_length(p_periods, 1) IS NULL OR t.period = ANY (p_periods))
    AND (p_accounts IS NULL OR array_length(p_accounts, 1) IS NULL
         OR coalesce(nullif(btrim(t.account_id), ''), '?') = ANY (p_accounts))
  GROUP BY 1, 2
$$;

-- The PERIOD PICKER (RULE THREE: pick, don't type) — every period this tenant actually has data for,
-- across the two raw MA tables and the stored overview report, with row counts. One aggregate instead of
-- paging three whole columns into Python.
DROP FUNCTION IF EXISTS commcalc.ma_overview_periods(uuid);
CREATE OR REPLACE FUNCTION commcalc.ma_overview_periods(p_org uuid)
RETURNS TABLE (period text, commission_rows bigint, dailytx_rows bigint, report_rows bigint)
LANGUAGE sql
STABLE
AS $$
  WITH u AS (
    SELECT coalesce(btrim(period), '') AS period, 1 AS src FROM commcalc.raw_ma_commission WHERE org_id = p_org
    UNION ALL
    SELECT coalesce(btrim(period), ''), 2 FROM commcalc.raw_ma_daily_tx     WHERE org_id = p_org
    UNION ALL
    SELECT coalesce(btrim(period), ''), 3 FROM commcalc.ma_overview_upload  WHERE org_id = p_org
  )
  SELECT period,
         count(*) FILTER (WHERE src = 1) AS commission_rows,
         count(*) FILTER (WHERE src = 2) AS dailytx_rows,
         count(*) FILTER (WHERE src = 3) AS report_rows
  FROM u
  WHERE period <> ''
  GROUP BY period
$$;

-- The FOLLOW-UP WORKLIST (owner answer 2026-08-04: the Appeal tile "would be the lines which don't get
-- paid and have to be followed up like we did in Boost"). Returns the ACTIVATION lines on which every
-- caller-supplied pay column is zero — i.e. nothing was paid in ANY leg. Which columns count as "paid"
-- is the tile's config, so they arrive as p_pay_cols and are read positionally out of the row's own
-- jsonb; a column name that does not exist contributes 0 rather than erroring. READ-ONLY: it creates no
-- appeal record and writes nothing.
DROP FUNCTION IF EXISTS commcalc.ma_overview_unpaid_lines(uuid, text[], text[], text[], int);
CREATE OR REPLACE FUNCTION commcalc.ma_overview_unpaid_lines(
  p_org uuid, p_periods text[], p_accounts text[], p_pay_cols text[], p_limit int DEFAULT 20000)
RETURNS TABLE (
  merchant_account_id text, activation_order text, imei text, sim text, sku text,
  tx_date date, period text, user_name text, line_status text, suspension_reason text,
  port_status text, activation_type text, activation_type2 text, sub_type text,
  perfect_sale text, is_financed text, mrc_net_discount numeric, paid_total numeric
)
LANGUAGE sql
STABLE
AS $$
  SELECT
    coalesce(nullif(btrim(c.merchant_account_id), ''), '?'),
    coalesce(btrim(c.activation_order), ''), coalesce(btrim(c.imei), ''),
    coalesce(btrim(c.sim), ''), coalesce(btrim(c.sku), ''),
    c.tx_date, coalesce(btrim(c.period), ''), coalesce(btrim(c.user_name), ''),
    coalesce(btrim(c.line_status), ''), coalesce(btrim(c.suspension_reason), ''),
    coalesce(btrim(c.port_status), ''), coalesce(btrim(c.activation_type), ''),
    coalesce(btrim(c.activation_type2), ''), coalesce(btrim(c.sub_type), ''),
    coalesce(btrim(c.perfect_sale), ''), coalesce(btrim(c.is_financed), ''),
    coalesce(c.mrc_net_discount, 0), 0::numeric
  FROM commcalc.raw_ma_commission c
  WHERE c.org_id = p_org
    AND (p_periods IS NULL OR array_length(p_periods, 1) IS NULL OR c.period = ANY (p_periods))
    AND (p_accounts IS NULL OR array_length(p_accounts, 1) IS NULL
         OR coalesce(nullif(btrim(c.merchant_account_id), ''), '?') = ANY (p_accounts))
    -- only ACTIVATION lines can be chased; a blank Activation Type is not a line anyone follows up
    AND coalesce(btrim(c.activation_type), '') <> ''
    -- nothing was paid, in any configured leg
    AND coalesce((SELECT sum(abs(coalesce((to_jsonb(c) ->> col)::numeric, 0)))
                  FROM unnest(coalesce(p_pay_cols, ARRAY[]::text[])) AS col), 0) = 0
  ORDER BY c.tx_date NULLS LAST, c.merchant_account_id
  LIMIT greatest(1, coalesce(p_limit, 20000))
$$;

GRANT EXECUTE ON FUNCTION commcalc.ma_overview_unpaid_lines(uuid, text[], text[], text[], int) TO service_role;
GRANT EXECUTE ON FUNCTION commcalc.ma_overview_periods(uuid)                          TO service_role;
GRANT EXECUTE ON FUNCTION commcalc.ma_overview_commission_cube(uuid, text[], text[])  TO service_role;
GRANT EXECUTE ON FUNCTION commcalc.ma_overview_dailytx_cube(uuid, text[], text[])     TO service_role;
GRANT EXECUTE ON FUNCTION commcalc.ma_overview_commission_accounts(uuid, text[], text[]) TO service_role;
GRANT EXECUTE ON FUNCTION commcalc.ma_overview_commission_dates(uuid, text[], text[]) TO service_role;
GRANT EXECUTE ON FUNCTION commcalc.ma_overview_dailytx_dates(uuid, text[], text[])    TO service_role;

-- ── 4b) seed the HOUSE org's carrier rate plan (owner: Total = M1 50%, M2–M6 75%) ──────────────────
INSERT INTO commcalc.ma_commission_month_rate (org_id, month_index, rate_pct, spiff_flat, note)
SELECT v.org_id, v.month_index, v.rate_pct, v.spiff_flat, v.note
  FROM (VALUES
    ('00000000-0000-0000-0000-000000000001'::uuid, 1, 50::numeric, 0::numeric, 'Total plan 2026-08-04 (owner): M1 = 50% of MRC.'),
    ('00000000-0000-0000-0000-000000000001'::uuid, 2, 75::numeric, 0::numeric, 'Total plan 2026-08-04 (owner): M2 = 75% of MRC.'),
    ('00000000-0000-0000-0000-000000000001'::uuid, 3, 75::numeric, 0::numeric, 'Total plan 2026-08-04 (owner): M3 = 75% of MRC — TEMPORARY spiff, expect changes.'),
    ('00000000-0000-0000-0000-000000000001'::uuid, 4, 75::numeric, 0::numeric, 'Total plan 2026-08-04 (owner): M4 = 75% of MRC — TEMPORARY spiff, expect changes.'),
    ('00000000-0000-0000-0000-000000000001'::uuid, 5, 75::numeric, 0::numeric, 'Total plan 2026-08-04 (owner): M5 = 75% of MRC — TEMPORARY spiff, expect changes.'),
    ('00000000-0000-0000-0000-000000000001'::uuid, 6, 75::numeric, 0::numeric, 'Total plan 2026-08-04 (owner): M6 = 75% of MRC — TEMPORARY spiff, expect changes.')
  ) AS v(org_id, month_index, rate_pct, spiff_flat, note)
 WHERE NOT EXISTS (
   SELECT 1 FROM commcalc.ma_commission_month_rate x
    WHERE x.org_id = v.org_id AND x.month_index = v.month_index
 );

-- ── 5) seed the HOUSE org with the Total-Wireless tile defaults ─────────────────────────────────────
-- Identical to ma_overview.DEFAULT_TILES. Every other tenant inherits those code defaults until it saves
-- its own rows, so this seed is convenience (something to edit in the UI), never the resolution rule.
-- The swap/upgrade exclusion list, shared by the three activation-qualified tiles. VERIFIED LIVE
-- 2026-08-04 against luxelink's 998 MA rows: the real Activation Type vocabulary is ONLY 'New' (393)
-- and 'Add' (605) — no swap/upgrade spelling occurs in this export, so today this list removes ZERO
-- rows. It is kept as a guard for tenants/exports that DO carry them, and the page prints the live
-- vocabulary with each value marked counted/excluded so the config can never quietly drift.
INSERT INTO commcalc.ma_overview_tile_config
  (org_id, tile_key, label, sort_order, value_format, source_table, agg, value_fields, sign,
   filter_field, filter_op, filter_value, filters, uploaded_field, uploaded_aliases,
   tolerance_abs, tolerance_pct, note)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'activation_count', 'Activation Count', 10, 'count',
   'raw_ma_commission', 'count', NULL, 'as_is',
   'activation_type', 'nonblank', NULL,
   '[{"field":"activation_type","op":"nonblank"},{"field":"activation_type","op":"not_in","value":"Upgrade,Upgrades,Swap,Swaps,SIM Swap,Sim Swap,Device Swap,Exchange,Handset Upgrade,Equipment Upgrade"}]'::jsonb,
   'activation_count',
   'Activation Count,Activations,ActivationCount,Total Activations', 0, 0,
   'OWNER DEFINITION 2026-08-04: new activations + ports + BYOD, EXCLUDING swaps and upgrades. Port and '
   'BYOD are ATTRIBUTES of a fresh line (port_status / activation_type2), not separate activation types, '
   'so the rule is an EXCLUSION list rather than an include-list that would double-count.'),

  ('00000000-0000-0000-0000-000000000001', 'twp_count', 'TWP Count', 20, 'count',
   'raw_ma_commission', 'count', NULL, 'as_is',
   'sub_type', 'eq', 'TWP', NULL, 'twp_count', 'TWP Count,TWP,TWPCount', 0, 0,
   'Sub Type = TWP.'),

  ('00000000-0000-0000-0000-000000000001', 'residual', 'Residual', 30, 'money',
   'raw_ma_daily_tx', 'sum', 'retail_cost', 'negate',
   'order_type', 'contains', 'Postpaid Residual Order', NULL, 'residual',
   'Residual,Residuals,Residual Paid', 0, 0,
   'OWNER-CONFIRMED 2026-08-04: the portal''s Residual means the SAME thing we compute — the What-If / '
   'finance residual-per-sub basis (raw_ma_daily_tx rows whose Order Type contains "Postpaid Residual '
   'Order", summing retail_cost, sign-negated to income; whatif._CFG_DEFAULTS[''plan''] + the mig 252 '
   'amount-field correction).'),

  ('00000000-0000-0000-0000-000000000001', 'rebates_paid', 'Rebates Paid', 40, 'money',
   'raw_ma_commission', 'sum', 'rebate', 'negate',
   NULL, NULL, NULL, NULL, 'rebates_paid', 'Rebates Paid,Rebate,Rebates', 0, 0,
   'Sum of the rebate column, sign-flipped (the export posts money paid TO the dealer as negative — the '
   'same convention /ma-commission/summary, account.residual_subs and coa.build_inputs use).'),

  ('00000000-0000-0000-0000-000000000001', 'fees_margin_paid', 'Fees Margin Paid', 50, 'money',
   'raw_ma_commission', 'sum', 'fees_margin', 'negate',
   NULL, NULL, NULL, NULL, 'fees_margin_paid', 'Fees Margin Paid,Fees Margin,Fee Margin', 0, 0,
   'Sum of fees_margin, sign-flipped to money received.'),

  ('00000000-0000-0000-0000-000000000001', 'commissions_paid', 'Commissions Paid (M1)', 60, 'money',
   'raw_ma_commission', 'sum', 'spiff_m1', 'negate',
   NULL, NULL, NULL,
   '[{"field":"activation_type","op":"nonblank"},{"field":"activation_type","op":"not_in","value":"Upgrade,Upgrades,Swap,Swaps,SIM Swap,Sim Swap,Device Swap,Exchange,Handset Upgrade,Equipment Upgrade"}]'::jsonb,
   'commissions_paid', 'Commissions Paid,Commission Paid,Commissions', 0, 0,
   'OWNER DEFINITION 2026-08-04: the CURRENT month''s commission on this month''s activations = the M1 '
   'leg (spiff_m1 — the 1st-month column), an MRC-BASED percentage. NOT consumer_margin + device_margin '
   '(the pre-answer assumption, proven wrong live: consumer_margin is EMPTY in this feed). The EXPECTED '
   'column beside it is the tenant''s configured M1 rate x the qualifying activations'' MRC.'),

  ('00000000-0000-0000-0000-000000000001', 'commissions_not_eligible', 'Commissions Not Eligible', 70,
   'count', 'raw_ma_commission', 'none', NULL, 'as_is',
   NULL, NULL, NULL, NULL, 'commissions_not_eligible',
   'Commissions Not Eligible,Not Eligible,Ineligible Commissions', 0, 0,
   'NO SOURCE MAPPED ON PURPOSE — the source report''s definition is not known. Candidates on '
   'raw_ma_commission are line_status and suspension_reason; the recon page lists their real value '
   'distributions so the owner can choose, then set agg=count + the filter here.'),

  ('00000000-0000-0000-0000-000000000001', 'edge_count', 'Edge (Device Finance)', 80, 'count',
   'raw_ma_commission', 'count', NULL, 'as_is',
   'is_financed', 'truthy', NULL, NULL, 'edge_count',
   'Edge,Edge Count,Device Finance,Device Financing', 0, 0,
   'Rows whose Is Financed flag is truthy (Y/Yes/True/1). "Edge" here is the TW FINANCING TENDER, not a '
   'Motorola Edge handset — never match this on a device model name.'),

  ('00000000-0000-0000-0000-000000000001', 'appeal_count', 'Appeal / follow-up lines', 90, 'count',
   'raw_ma_commission', 'unpaid_count',
   'spiff_m1,spiff_m2,spiff_m3,spiff_m4,spiff_m5,spiff_m6,rebate,consumer_margin,device_margin,consumer_financing,fees_margin',
   'as_is', NULL, NULL, NULL,
   '[{"field":"activation_type","op":"nonblank"},{"field":"activation_type","op":"not_in","value":"Upgrade,Upgrades,Swap,Swaps,SIM Swap,Sim Swap,Device Swap,Exchange,Handset Upgrade,Equipment Upgrade"}]'::jsonb,
   'appeal_count', 'Appeal Count,Appeals', 0, 0,
   'OWNER DEFINITION 2026-08-04: the source report has no appeal column, so this tile IS the follow-up '
   'worklist — "the lines which don''t get paid and have to be followed up like we did in Boost". It '
   'counts QUALIFYING ACTIVATION lines on which EVERY configured pay column is zero, and drills down to '
   'those exact lines (account, order, IMEI, MRC, months elapsed). Derived and READ-ONLY: it writes '
   'nothing and creates no appeal record.')
ON CONFLICT (org_id, tile_key) DO NOTHING;

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 268 complete — commcalc.ma_overview_upload + ma_overview_tile_config + the two recon '
       'cubes (ma_overview_commission_cube / ma_overview_dailytx_cube). READ-ONLY recon: no pay table is '
       'written, no recalculation is triggered.' AS status;
