-- 241_commission_import_evidence_audit.sql — mod-commission (band 200–299)
-- SETTINGS + IMPORTS AUDIT (owner directive 2026-07-26): make the import EVIDENCE CHAIN honest and put
-- the two thresholds it needs into config instead of code.
--
-- WHY
--   core.import_evidence (mig 717) answers "when did this feed last DELIVER?" for the portal feeds from
--   ONE column: commcalc.<x>_sweep_config.last_run_at / commcalc.data_source.last_run_at. commcalc used
--   to bump that column on FAILURE too (a rejected ePay login, a VidaPay "needs login", even merely
--   clicking "Log in") — so a connector that has imported nothing for weeks looked FRESH and the admin
--   attention popup stayed silent. That is the exact opposite of the directive.
--
--   The code fix (router.py `_sweep_set_status` / `_source_stamp`) keeps last_run_at = LAST SUCCESSFUL
--   IMPORT and records every non-delivering attempt in `last_attempt_at` instead. This migration adds
--   that column. Until it runs, the code silently drops the field (the write is retried without it), so
--   nothing breaks — you just lose the "last attempt" timestamp in the UI while failures already stop
--   faking freshness.
--
-- ADDITIVE + IDEMPOTENT. Safe to re-run. No data is moved: last_run_at keeps whatever it holds today and
-- simply stops advancing on failures from the next deploy on.

-- ── (1) last_attempt_at on every commcalc import-status carrier ───────────────────────────────────
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'data_source', 'epay_sweep_config', 'dlar_sweep_config', 'vip_sweep_config',
    'b2b_sweep_config', 'ftp_sweep_config', 'email_sweep_config'
  ] LOOP
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'commcalc' AND table_name = t) THEN
      EXECUTE format('ALTER TABLE commcalc.%I ADD COLUMN IF NOT EXISTS last_attempt_at timestamptz', t);
      EXECUTE format('COMMENT ON COLUMN commcalc.%I.last_attempt_at IS %L', t,
                     'Last time a run was ATTEMPTED (success or failure). last_run_at = last run that '
                     'actually imported data — core.import_evidence reads last_run_at as the freshness '
                     'trail, so it must never advance on a failure.');
    END IF;
  END LOOP;
END $$;

-- ── (2) the two audit thresholds, per tenant, on the existing commission posture table ────────────
-- RULE TWO: a human-tunable threshold belongs in config with a sane default, not in code. Both columns
-- are read best-effort (missing table/column ⇒ the documented default), so this section is optional.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
              WHERE table_schema = 'commcalc' AND table_name = 'commission_org_config') THEN
    ALTER TABLE commcalc.commission_org_config
      ADD COLUMN IF NOT EXISTS connector_stale_hours numeric,
      ADD COLUMN IF NOT EXISTS audit_zero_price_pct  numeric;
    COMMENT ON COLUMN commcalc.commission_org_config.connector_stale_hours IS
      'Hours without a SUCCESSFUL connector run before the connector-health alert calls it stalled '
      '(default 30 when NULL). Was the hard-coded _CONNECTOR_STALE_HOURS.';
    COMMENT ON COLUMN commcalc.commission_org_config.audit_zero_price_pct IS
      'Share (0–1) of sampled sales rows that must have $0 Ext Price before the degraded-sales-export '
      'attention item fires (default 0.95 when NULL).';
  END IF;
END $$;

-- ── (3) OPERATOR: schedule the portal-pull sweep (this cron does not exist yet) ───────────────────
-- The owner's "the VidaPay commission is supposed to run on a schedule" has no scheduler today: nothing
-- calls POST /api/v1/commcalc/data-sources/sweep/run-due (grep: zero references outside its own def), so
-- every VidaPay / T-CETRA pull has only ever happened when a human clicked Pull. The endpoint now accepts
-- the same X-Notify-Secret the other five /run-due sweeps use (with the secret ⇒ every tenant's due
-- logins; without it ⇒ only the caller's own org). Run this ONCE in the Supabase SQL editor, replacing
-- <NOTIFY_RUN_SECRET> with the Railway env value:
--
--   SELECT cron.schedule('data-sources-run-due', '*/30 * * * *', $$
--     SELECT net.http_post(
--       url     := 'https://metricspro-production.up.railway.app/api/v1/commcalc/data-sources/sweep/run-due',
--       headers := '{"Content-Type":"application/json","X-Notify-Secret":"<NOTIFY_RUN_SECRET>"}'::jsonb,
--       body    := '{}'::jsonb)
--   $$);
--
-- Verify:  SELECT * FROM cron.job WHERE jobname = 'data-sources-run-due';
--          SELECT status, content FROM net._http_response ORDER BY created DESC LIMIT 5;   -- 403 = secret mismatch
