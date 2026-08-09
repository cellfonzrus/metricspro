-- 732_core_import_batches.sql — platform-core band 700-799.
--
-- RENUMBERED from 725 before it was ever applied: the POS module took 724-727 out of this band when
-- it merged (PR #4), so `725_pos_sales.sql` already exists on main — as does a second `724_*`. Two
-- files sharing a number is how a migration silently gets skipped by whoever applies them in order.
--
-- DDIA implementation plan, PHASE 1 (Import batches and idempotency) — the one phase in that document
-- ranked "Critical" that was genuinely not built. Verified absent 2026-08-09: no core.import_batches,
-- no import_batch_id on any raw table, no file hashing anywhere in the upload path. Today, uploading
-- the same ePay/B2B file twice inserts its rows twice, and the money is double-counted until somebody
-- notices. `commcalc.upload_log` (7,926 rows) and `commcalc.upload_trace` (10,630) RECORD every upload
-- richly but PREVENT nothing.
--
-- CROSS-BAND NOTE: this migration adds a `core.*` table AND columns on `commcalc.raw_*`. That crosses
-- the mod-commission band (200-299) on purpose — it is one platform-wide initiative from the DDIA
-- plan, applied by the operator session, not a module feature. Splitting the table from the columns
-- that reference it would leave a half-migrated state, which is exactly what the plan's ground rules
-- forbid. Recorded in docs/PLAN_REVIEW_2026-08-09.md.
--
-- ── THE IDEMPOTENCY GUARD ─────────────────────────────────────────────────────────────────────────
-- A partial UNIQUE index on (org_id, file_sha256) WHERE status <> 'failed'. Claim the batch row BEFORE
-- parsing: if the same bytes already loaded for this org, the insert is rejected and the caller skips
-- the import entirely. `failed` batches are excluded so a genuine retry after a parse error still
-- works — that exclusion is the whole reason the index is partial.
--
-- Scoped per ORG deliberately: two tenants legitimately upload byte-identical files (the same carrier
-- template with no data yet, an empty export), and one tenant's load must never block another's.
--
-- ── WHY THIS DEGRADES OPEN, NOT CLOSED ────────────────────────────────────────────────────────────
-- import_batches.py wraps every call in try/except and, when this table is missing, returns
-- "unavailable" and lets the upload proceed EXACTLY as it does today. That is the opposite of
-- migration 420's fail-closed rule, and the difference is deliberate: there, off was both the safe
-- state and the requested one; here, failing closed would mean an unapplied migration silently blocks
-- every data import in the platform at month end. A guard that can take the business offline is worse
-- than the duplicate it prevents.
--
-- Additive + idempotent.

CREATE TABLE IF NOT EXISTS core.import_batches (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL,
  source         text NOT NULL,          -- the upload file_type: 'sales' | 'mi_report' | 'comp_report' | …
  format_version text NOT NULL,          -- from commcalc/formats.py, e.g. 'b2bsoft_sales_v1'
  file_name      text,
  file_sha256    text NOT NULL,
  file_bytes     bigint NOT NULL,
  row_count      integer,
  status         text NOT NULL DEFAULT 'parsing',   -- parsing | loaded | failed | superseded
  error_detail   text,
  period         text,                   -- the period label the operator selected, when there is one
  uploaded_by    text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  completed_at   timestamptz
);

-- THE guard. Partial so a failed batch can be retried with the same bytes.
CREATE UNIQUE INDEX IF NOT EXISTS import_batches_org_hash_uidx
  ON core.import_batches (org_id, file_sha256)
  WHERE status <> 'failed';

CREATE INDEX IF NOT EXISTS import_batches_org_source_idx
  ON core.import_batches (org_id, source, created_at DESC);
CREATE INDEX IF NOT EXISTS import_batches_stale_idx
  ON core.import_batches (status, created_at)
  WHERE status = 'parsing';

ALTER TABLE core.import_batches ENABLE ROW LEVEL SECURITY;
-- No policies and no anon/authenticated grants, per AGENT_CONTRACT §5: the backend is service_role,
-- which bypasses RLS. (Migration 722/724 posture.)

-- Link every raw row to the batch it arrived in. NULLABLE and NOT backfilled — existing rows are
-- pre-batch data and the DDIA plan explicitly says not to invent a batch for them. ON DELETE SET NULL
-- so removing a batch record can never block on, or destroy, the rows it loaded.
DO $$
DECLARE
  t TEXT;
  tables TEXT[] := ARRAY[
    'commcalc.raw_sales', 'commcalc.raw_payment_detail', 'commcalc.raw_mi',
    'commcalc.raw_comp_report', 'commcalc.raw_dlar_rep', 'commcalc.raw_dlar_store',
    'commcalc.raw_catalog', 'commcalc.raw_categories', 'commcalc.daily_sales_feed',
    'commcalc.raw_ma_commission', 'commcalc.raw_ma_daily_tx', 'commcalc.raw_ma_fulfillment'
  ];
  idx TEXT;
BEGIN
  FOREACH t IN ARRAY tables LOOP
    IF to_regclass(t) IS NULL THEN
      RAISE NOTICE 'skipping %, table not present', t;
      CONTINUE;
    END IF;
    EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS import_batch_id uuid', t);
    -- Add the FK separately and only once: ADD COLUMN IF NOT EXISTS cannot carry REFERENCES
    -- idempotently, and a second run must not raise "constraint already exists".
    idx := replace(split_part(t, '.', 2), '.', '_');
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = idx || '_import_batch_fk') THEN
      EXECUTE format(
        'ALTER TABLE %s ADD CONSTRAINT %I FOREIGN KEY (import_batch_id) '
        'REFERENCES core.import_batches(id) ON DELETE SET NULL',
        t, idx || '_import_batch_fk');
    END IF;
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %s (import_batch_id)',
                   idx || '_import_batch_idx', t);
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 732 complete — core.import_batches + import_batch_id on the raw tables (DDIA Phase 1)' AS status;
