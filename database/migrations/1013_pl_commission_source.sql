-- 1013_pl_commission_source.sql
-- mod-account/commission · follows 1012. Additive + idempotent + safe to re-run. WRITTEN, NOT APPLIED.
--
-- WHAT IT IS FOR (owner 2026-09-21, verbatim): "p&l is not showing the commission received, it shows
-- in the commission ledger but not populating the p&l - check platform wide not bandaid".
--
-- THE CLASS, NOT THE INSTANCE. The instance: org f4f1c16e… holds 973 commission-ledger lines for
-- July 2026 (net 86,970.34 after the sign convention, bucketed through the mig-1009 registry) and its
-- P&L "Carrier commissions & incentives" reads nothing from them. The class: the P&L's commission
-- lines were derived from PER-FEED tables (raw_ma_commission, raw_ma_daily_tx, raw_comp_report,
-- activation_rebate_ledger, raw_mi) and the canonical, bucketed, sign-conventioned commission ledger
-- — where EVERY carrier onboarded through the intake lands — was not a P&L source at all. A tenant
-- whose statements land only in the ledger showed $0 commission on the P&L, silently.
--
-- WHAT THIS ADDS (RULE TWO — per-org config with a house default; no tenant or carrier is named in
-- code). ONE column on THE per-org money-policy row, beside the other P&L source-of-truth switches
-- (pl_ma_month_spiff_source, pl_rebate_presentation, pl_device_margin_presentation — mig 314/934/996):
--
--   commcalc.commission_org_config.pl_commission_source (text)
--   • 'feeds'              (house default) — today's bookings, from the feed tables. Byte-identical for
--                          every existing tenant (backend/harness_pl_commission_source.py §A pins it).
--   • 'ledger'             — book commission from commcalc.commission_ledger: Σ per bucket per period
--                          (commission_ledger.summarize — the ledger's OWN summarizer, never a second
--                          sum) lands on the P&L line the org's bucket registry names
--                          (commission_bucket.pl_line_key, mig 1009), per store when the line carries
--                          one; deductions land signed on their expense line. The commission-FEED
--                          bookings for the SAME lines are suppressed so a line is never booked twice
--                          (the suppression set is DERIVED from the registry's pl_line_key set).
--                          Bookings from non-commission sources (rent on store_opex, chargeback_items
--                          on chargebacks, distributor fees on vip_fees) are untouched.
--   • 'ledger_else_feeds'  — per period: the ledger when it holds lines for that period, else the feeds.
--
-- Resolution: app/modules/account/ma_store_pnl.load_config (column set falls back 1013→996→934→314→
-- defaults, so a database without this column reads 'feeds' and books exactly as before) →
-- app/modules/account/ledger_pnl.resolve_source (THE one resolver) → account/coa.build_inputs.
-- Writer: PUT /commcalc/commission-settings {pl_commission_source}; reader for the settings panel:
-- GET /commcalc/pl-commission-source (with the suggestion and the evidence behind it).
--
-- 💰 MONEY POSTURE — the DDL alone changes NOTHING for any org (default 'feeds'). NO org is seeded to
-- 'ledger' here: switching is an owner decision made on the settings panel (suggested when the org has
-- ledger lines and NO feed table populated — confirmed by a person, never silently). Flipping an org
-- to 'ledger' changes the VALUE of its commission lines from Σ(feed tables) to Σ(ledger buckets); the
-- P&L line's drill-down shows both figures and the difference in words before and after the flip.
--
-- REVERT:
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS pl_commission_source;
--   (The backend then reads the default — 'feeds' — for every org.)

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS pl_commission_source TEXT NOT NULL DEFAULT 'feeds';

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname = 'commission_org_config_pl_commission_source_ck') THEN
    ALTER TABLE commcalc.commission_org_config
      ADD CONSTRAINT commission_org_config_pl_commission_source_ck
      CHECK (pl_commission_source IN ('feeds', 'ledger', 'ledger_else_feeds'));
  END IF;
END $$;

COMMENT ON COLUMN commcalc.commission_org_config.pl_commission_source IS
  'Mig 1013. Which source books the P&L commission lines: feeds (house default: the raw feed tables, '
  'byte-identical to pre-1013) | ledger (commcalc.commission_ledger by bucket -> commission_bucket.'
  'pl_line_key; the feed bookings for those lines are suppressed) | ledger_else_feeds (per period: the '
  'ledger when it holds lines, else the feeds). Read by ma_store_pnl.load_config -> ledger_pnl.resolve_source.';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1013 complete — commission_org_config.pl_commission_source (default feeds; no org switched)' AS status;
