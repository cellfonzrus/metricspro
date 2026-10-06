-- 1059_subscriber_installment_pay_period_key.sql
--
-- THE RESIDUAL INSTALMENT LEDGER KEEPS ONE ROW PER PAY PERIOD — index §19.51 (D3).
--
-- WHAT WAS WRONG. `commcalc.subscriber_installments` (mig 057) was created with
--     UNIQUE (org_id, subscriber_id, activation_type, month_index)
-- and `installment_engine._persist` upserts on exactly that key. `pay_period` is a STORED COLUMN but
-- is NOT part of the key, so one subscriber can only ever hold ONE row per month_index: the September
-- run OVERWRITES August's record of the same instalment. The ledger is described in mig 057's own
-- header as "the 'reflected in the statement' audit trail", and an audit trail that cannot hold two
-- pay periods is not one.
--
-- Its SIBLING ledger for the sale-triggered path has had the right key since mig 201:
--     commcalc.sale_installment_ledger UNIQUE (org_id, trans_id, mdn, month_index, pay_period)
-- Two ledgers recording the same kind of fact, two different keys, and only one of them can keep
-- history. This migration makes the residual ledger's key the same SHAPE as its sibling's.
--
-- MONEY-TOUCHING: this file is SURFACED FOR OWNER APPROVAL and is NOT applied by shipping the code.
-- The code that goes with it is ADAPTIVE (`installment_engine._persist` probes which unique key the
-- database actually has and upserts on that one, reporting which it used), so merging the code
-- without applying this file changes nothing and writes nothing differently.
--
-- NO ROW IS DELETED OR REWRITTEN HERE. Widening a unique key can only ever ADMIT rows that the
-- narrow key rejected; every row already stored stays, byte for byte. Step 1 proves up front whether
-- any stored row would collide under the new key (it cannot: the new key is strictly wider).
--
-- Idempotent, additive, re-runnable.

-- ── STEP 1 (read-only) — what is in the ledger now, and what the new key would do to it ──────────
-- Expected on every tenant today: both counts EQUAL, because the narrow key already forced one row
-- per (org, subscriber, activation_type, month_index). A wider key cannot reduce either number.
DO $$
DECLARE
  n_rows    BIGINT;
  n_narrow  BIGINT;
  n_wide    BIGINT;
BEGIN
  IF to_regclass('commcalc.subscriber_installments') IS NULL THEN
    RAISE NOTICE '1059: commcalc.subscriber_installments does not exist (mig 057 not applied) — nothing to do.';
    RETURN;
  END IF;
  SELECT count(*) INTO n_rows FROM commcalc.subscriber_installments;
  SELECT count(*) INTO n_narrow FROM (
    SELECT DISTINCT org_id, subscriber_id, activation_type, month_index
    FROM commcalc.subscriber_installments) x;
  SELECT count(*) INTO n_wide FROM (
    SELECT DISTINCT org_id, subscriber_id, activation_type, month_index, pay_period
    FROM commcalc.subscriber_installments) y;
  RAISE NOTICE '1059: ledger rows=% ; distinct under the OLD key=% ; distinct under the NEW key=%',
    n_rows, n_narrow, n_wide;
END $$;

-- ── STEP 2 — the new, wider unique key (pay_period included, like the sibling ledger) ────────────
-- A NULL pay_period does not collide in Postgres (NULLS DISTINCT by default), which is correct here:
-- a row whose pay period was never recorded is not evidence about any particular month.
CREATE UNIQUE INDEX IF NOT EXISTS subscriber_installments_pay_period_key
  ON commcalc.subscriber_installments (org_id, subscriber_id, activation_type, month_index, pay_period);

-- ── STEP 3 — retire the narrow key that was overwriting history ──────────────────────────────────
-- Dropped only AFTER step 2 exists, so the table is never without a uniqueness guarantee. mig 057
-- created it as a table-level UNIQUE(...), which Postgres implements as a named constraint; the
-- lookup below finds it by its COLUMN SET rather than by a name spelling, so this works whatever the
-- constraint ended up being called.
DO $$
DECLARE
  c_name TEXT;
BEGIN
  IF to_regclass('commcalc.subscriber_installments') IS NULL THEN
    RETURN;
  END IF;
  SELECT con.conname INTO c_name
  FROM pg_constraint con
  JOIN pg_class rel ON rel.oid = con.conrelid
  JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
  WHERE nsp.nspname = 'commcalc'
    AND rel.relname = 'subscriber_installments'
    AND con.contype = 'u'
    AND (
      SELECT array_agg(att.attname::text ORDER BY att.attname)
      FROM unnest(con.conkey) k
      JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k
    ) = ARRAY['activation_type', 'month_index', 'org_id', 'subscriber_id']
  LIMIT 1;
  IF c_name IS NULL THEN
    RAISE NOTICE '1059: no narrow unique constraint found (already retired) — nothing to drop.';
  ELSE
    EXECUTE format('ALTER TABLE commcalc.subscriber_installments DROP CONSTRAINT %I', c_name);
    RAISE NOTICE '1059: dropped narrow unique constraint %', c_name;
  END IF;
END $$;

-- ── STEP 4 — the ledger row records WHICH anchor placed its month (index §19.51) ─────────────────
-- Additive and nullable. `installment_engine` resolves every row's month through
-- `installment_month.resolve_month`, which returns the month index AND the basis it rests on
-- ('activation_date' = the subscriber's own date; 'window_edge' = no date on the row, so the month
-- was taken from the oldest month read and is NOT proven). Persisting it makes a stored payout
-- auditable for that difference instead of only the live run being able to say so. NULL on every
-- pre-1059 row, which is honest: those rows were written before the basis was recorded.
ALTER TABLE commcalc.subscriber_installments
  ADD COLUMN IF NOT EXISTS month_basis TEXT;

COMMENT ON COLUMN commcalc.subscriber_installments.month_basis IS
  'How this row''s month_index was derived: activation_date (the subscriber''s own anchor) | origin_period | window_edge (no anchor on the row — assumed, not proven). See installment_month.py, index 19.51.';

-- ── STEP 5 (read-only) — confirm the key the writer will now find ────────────────────────────────
DO $$
DECLARE
  has_wide BOOLEAN;
  has_basis BOOLEAN;
BEGIN
  IF to_regclass('commcalc.subscriber_installments') IS NULL THEN
    RETURN;
  END IF;
  SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'commcalc'
    AND tablename = 'subscriber_installments'
    AND indexname = 'subscriber_installments_pay_period_key') INTO has_wide;
  SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'commcalc'
    AND table_name = 'subscriber_installments' AND column_name = 'month_basis') INTO has_basis;
  RAISE NOTICE '1059: wide pay_period key present=% ; month_basis column present=%', has_wide, has_basis;
END $$;

-- REVERT:
--   -- Restores mig 057's narrow key exactly. Run the SELECT first: if it returns any row, two pay
--   -- periods are already stored for one instalment and the narrow key CANNOT be restored without
--   -- choosing which pay period to delete — that is a money decision, so it stops here rather than
--   -- deleting anything.
--   SELECT org_id, subscriber_id, activation_type, month_index, count(*) AS pay_periods
--     FROM commcalc.subscriber_installments
--    GROUP BY 1,2,3,4 HAVING count(*) > 1;
--
--   ALTER TABLE commcalc.subscriber_installments
--     ADD CONSTRAINT subscriber_installments_org_id_subscriber_id_activation_typ_key
--     UNIQUE (org_id, subscriber_id, activation_type, month_index);
--   DROP INDEX IF EXISTS commcalc.subscriber_installments_pay_period_key;
--   ALTER TABLE commcalc.subscriber_installments DROP COLUMN IF EXISTS month_basis;
