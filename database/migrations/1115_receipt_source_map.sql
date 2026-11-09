-- 1115_receipt_source_map.sql
--
-- §COMMCALC-56 — WHEN DOES THE CARRIER PAY US, AND OUT OF WHICH FEED. The inbound mirror of
-- commcalc.payable_source_map (mig 095 + 1095), which already declares the OUTBOUND billing rhythm.
--
-- OWNER ASK, verbatim: "i need a report for the expected payments for teh next payout cycle by the
-- day as acalender display for all tenants, also the foloowing report will be how much is owed,
-- which we already ahave and the thent eh irs will be after offseting the payments what isteh cash
-- flow".
--
-- WHY A TABLE AND NOT A CONSTANT. "The carrier pays on the 15th" is a commercial arrangement, per
-- carrier and per tenant, and RULE TWO forbids it living in code. Nothing in this platform has ever
-- recorded it, so the calendar cannot be built without it. The three readers are
-- backend/app/modules/payables/receipt_schedule.py (the rhythm), expected_receipts.py (the read) and
-- cash_calendar.py (the netting) — proved DB-free by backend/harness_cash_calendar.py.
--
-- NOTHING IS SEEDED, AND THAT IS DELIBERATE. A house default here would be one tenant's payout
-- arrangement answering for every tenant — exactly the defect CLAUDE.md's "Every feature is for
-- EVERY tenant" rule was written against (index §COMMCALC-49.10). A tenant that has declared nothing
-- reads NOT MEASURED and the report says so in words; it never reads $0.00 and never borrows another
-- tenant's rhythm. House defaults stay right for a POLICY and wrong for a tenant's own arrangement.
--
-- MONEY: this migration MOVES NO MONEY and writes no amount. It creates an empty declaration table
-- that a new read-only report dereferences. No existing surface changes behaviour.
--
-- ADDITIVE AND IDEMPOTENT. Safe to run more than once.
--
-- REVERT: DROP TABLE IF EXISTS commcalc.receipt_source_map;

CREATE TABLE IF NOT EXISTS commcalc.receipt_source_map (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          uuid NOT NULL,
  -- What a human calls this inbound money stream on the screen. Not a carrier branch in code: the
  -- platform never compares against this value, it only prints it.
  label           text NOT NULL,
  -- WHICH RELATION states money the carrier/processor will pay us, and WHICH COLUMN is the money.
  -- The column that DATES a row is NOT declared here: it is dereferenced from
  -- commcalc data_lineage_registry.DATA_DATE_COLUMN_BY_TABLE, which has been that fact's one home
  -- since the feed watchdog shipped. A second copy would be the §19.18 divergence.
  source_table    text NOT NULL,
  amount_field    text NOT NULL,
  -- THE PAYOUT RHYTHM. Constrained to the vocabulary receipt_schedule.CADENCES rules on, so a typo
  -- cannot be stored and then read as a rhythm. NULL = nothing declared, which the reader reports as
  -- NOT MEASURED rather than defaulting.
  payout_cadence       text,
  payout_weekday       integer,   -- cadence 'weekly': 0 = Monday … 6 = Sunday
  payout_days          integer[], -- cadence 'semi_monthly': the days of the month it pays on
  payout_day_of_month  integer,   -- cadence 'monthly': the one day of the month it pays on
  payout_net_days      integer,   -- cadence 'net_days_after_period_end': the declared lag
  is_active       boolean NOT NULL DEFAULT true,
  updated_by      text,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS rsm_org ON commcalc.receipt_source_map (org_id, is_active);
CREATE UNIQUE INDEX IF NOT EXISTS rsm_org_source
  ON commcalc.receipt_source_map (org_id, source_table, amount_field);

DO $$
BEGIN
  -- The cadence vocabulary, mirrored from receipt_schedule.CADENCES. A value the home has never
  -- ruled on falls to NOT MEASURED in the reader; refusing it at the column keeps the two from
  -- disagreeing about what is storable.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'receipt_source_map_cadence_ck') THEN
    ALTER TABLE commcalc.receipt_source_map
      ADD CONSTRAINT receipt_source_map_cadence_ck
      CHECK (payout_cadence IS NULL OR payout_cadence IN
             ('statement_date', 'weekly', 'semi_monthly', 'monthly', 'net_days_after_period_end'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'receipt_source_map_weekday_ck') THEN
    ALTER TABLE commcalc.receipt_source_map
      ADD CONSTRAINT receipt_source_map_weekday_ck
      CHECK (payout_weekday IS NULL OR (payout_weekday >= 0 AND payout_weekday <= 6));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'receipt_source_map_dom_ck') THEN
    ALTER TABLE commcalc.receipt_source_map
      ADD CONSTRAINT receipt_source_map_dom_ck
      CHECK (payout_day_of_month IS NULL
             OR (payout_day_of_month >= 1 AND payout_day_of_month <= 31));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'receipt_source_map_net_ck') THEN
    ALTER TABLE commcalc.receipt_source_map
      ADD CONSTRAINT receipt_source_map_net_ck
      CHECK (payout_net_days IS NULL OR (payout_net_days >= 0 AND payout_net_days <= 365));
  END IF;
END $$;

COMMENT ON TABLE commcalc.receipt_source_map IS
  'Per-tenant declaration of inbound money: which feed states what a carrier/processor will pay us, '
  'which column is the money, and on what rhythm it pays. Read by payables/expected_receipts.py '
  'through payables/receipt_schedule.py (§COMMCALC-56). Deliberately unseeded: a house default here '
  'would be one tenant''s payout arrangement answering for every tenant.';
COMMENT ON COLUMN commcalc.receipt_source_map.payout_cadence IS
  'How this carrier pays: statement_date (its own feed stamps the day), weekly (payout_weekday), '
  'semi_monthly (payout_days), monthly (payout_day_of_month) or net_days_after_period_end '
  '(payout_net_days). NULL means nothing was declared, which reads as NOT MEASURED — never as a '
  'default day, because a defaulted payment date is a promise of cash no carrier ever made.';

ALTER TABLE commcalc.receipt_source_map ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'commcalc'
                 AND tablename = 'receipt_source_map' AND policyname = 'open_all') THEN
    CREATE POLICY open_all ON commcalc.receipt_source_map FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

-- A MIGRATION GRANTS WHAT IT CREATES (house convention, enforced by harness_data_api_grant_guard.py).
-- service_role ONLY: the backend reaches every relation through PostgREST and the browser never reads
-- a table here, so an anon grant would widen exposure rather than fix anything.
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.receipt_source_map TO service_role;
