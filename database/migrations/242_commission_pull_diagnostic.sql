-- 242_commission_pull_diagnostic.sql — mod-commission (band 200–299)
-- "VIDAPAY LOGS IN BUT NOTHING IMPORTS" (owner report 2026-07-27).
--
-- WHY
--   The portal-pull driver has ALWAYS built a diagnostic on every ambiguous outcome (vidapay_sweep
--   `_snapshot`, and now `reports_probe`) — and it was returned over HTTP and then dropped on the floor
--   by the page. So the operator's whole evidence for a failed import was one truncated sentence
--   ("pulled 0 rows across 0 report(s): —; calibration/diagnostic needed: …") rendered next to a green
--   ✅ Connected chip, with no way to find out WHY. The module's own stated strategy — "the operator's
--   first real login is the calibration pass" — had no last mile.
--
--   These two columns give that diagnostic somewhere to live, so /commcalc/email-imports can show
--   "🔧 What the pull saw": every report the pull tried, why each one failed, and the report names the
--   portal's own dropdown actually offers (the vocabulary to fix Report mapping with).
--
--   `auto_pull_after_login` is the per-source switch for the other half of the fix: a successful live
--   login now pulls the due reports IMMEDIATELY instead of leaving a trusted session idling until it
--   expires. Default ON; a tenant that wants to sign in without pulling turns it off here.
--
-- ADDITIVE + IDEMPOTENT. Safe to re-run. NOTHING breaks before it runs: the diagnostic write is a
-- self-contained best-effort UPDATE (a missing column just logs a WARN and stores nothing), the pull
-- itself is unchanged by this file, and `auto_pull_after_login` is read as "not false" so a row without
-- the column behaves as ON. No pay number, rate, tier or calculation input is touched by this file.

-- ── (1) what the last pull saw, per portal login ──────────────────────────────────────────────────
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
              WHERE table_schema = 'commcalc' AND table_name = 'data_source') THEN

    ALTER TABLE commcalc.data_source
      ADD COLUMN IF NOT EXISTS last_pull_diag jsonb,
      ADD COLUMN IF NOT EXISTS last_pull_at   timestamptz,
      ADD COLUMN IF NOT EXISTS auto_pull_after_login boolean NOT NULL DEFAULT true;

    COMMENT ON COLUMN commcalc.data_source.last_pull_diag IS
      'What the last report pull saw: per-report outcome (ok / rows / reason / error), whether the '
      'portal''s Reports page was reachable, and a credential-free probe of the portal''s own nav '
      'links, dropdown options, buttons and date fields. Read by GET /commcalc/data-sources/{id}/'
      'pull-diagnostic to power "🔧 What the pull saw". No input VALUE is ever captured — only '
      'names/ids/labels — so no credential can land here.';
    COMMENT ON COLUMN commcalc.data_source.last_pull_at IS
      'When last_pull_diag was written (any pull attempt, delivering or not). Distinct from '
      'last_run_at = last pull that actually IMPORTED data, and last_attempt_at (mig 241).';
    COMMENT ON COLUMN commcalc.data_source.auto_pull_after_login IS
      'ON (default): the moment a live login authenticates, this login''s due reports are pulled on '
      'that same trusted browser. OFF: the session is saved and the operator pulls manually. Before '
      '2026-07-27 the behaviour was permanently OFF and undocumented — a session could (and did) '
      'expire without a single report ever being fetched.';
  END IF;
END $$;

-- ── (2) find the logins that sign in but never deliver (the admin-attention case) ─────────────────
-- Partial index: the attention provider scans for authenticated logins with no successful run.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'commcalc' AND table_name = 'data_source'
                AND column_name = 'last_run_at') THEN
    CREATE INDEX IF NOT EXISTS data_source_never_delivered_idx
      ON commcalc.data_source (org_id, processor)
      WHERE last_run_at IS NULL;
  END IF;
END $$;
