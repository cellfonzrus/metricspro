-- 934_pl_rebate_presentation.sql
-- mod-account · follows 933. Additive + idempotent + safe to re-run.
--
-- WHAT IT IS FOR (owner report 2026-09-02, verbatim): "rebate is coming in negative, it should be
-- a positive number as it is coming in."
--
-- CONTEXT / EVIDENCE (measured live, luxelink August 2026): the device-purchase rebate books per
-- owner ruling K1 (2026-08-10) as CONTRA-COGS — `device_rebate` −251,946.31 inside the COGS
-- section (raw_ma_commission Aug sheet, 1,248 rows across 20 merchant accounts; the P&L figure
-- equals the raw sheet to the penny). Accounting-correct, but the owner reads incoming money as a
-- negative number. This migration makes the PRESENTATION per-org config (RULE TWO):
--
--   commcalc.commission_org_config.pl_rebate_presentation (text):
--   • 'contra_cogs' (house default) — ruling K1 unchanged: rebates net against Device cost on the
--     `device_rebate` line, booked negative. Every org is byte-identical to pre-934.
--   • 'income' — the SAME dollars book POSITIVE on the new `rebate_income` P&L revenue line
--     ("Rebates (device purchase)", auto_opt — materialises only where it carries value). The
--     contra-COGS line then carries $0 and is suppressed. BOTH rebate sources follow the one
--     resolved route (MA commission sheet component AND activation_rebate_ledger) so they can
--     never present differently — resolution in app/modules/account/ma_store_pnl.py
--     (rebate_route, load_config) + account/coa.py (PL_SPEC `rebate_income`, activation-ledger
--     booking) + account/engine.py (`suppress_zero` passthrough).
--
-- 💰 MONEY POSTURE — the DDL alone changes NOTHING for any org. The LUXELINK SEED below applies
-- the owner's explicit 2026-09-02 ask and is PRESENTATION-ONLY: on August 2026 (measured)
-- Revenue and COGS both rise by 251,946.31 (rebates move from −251,946.31 contra-COGS to
-- +251,946.31 revenue), so GROSS PROFIT and NET INCOME are unchanged to the penny. Store/company
-- grain is unchanged (rebates keep the mig-314 account→store attribution). Proof:
-- backend/harness_pl_rebate_presentation.py. Takes effect at the next /account/compute per period.
--
-- REVERT:
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS pl_rebate_presentation;
--   (The backend then resolves 'contra_cogs' for every org — ruling-K1 presentation, pre-934.)

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS pl_rebate_presentation TEXT NOT NULL DEFAULT 'contra_cogs'
  CHECK (pl_rebate_presentation IN ('contra_cogs', 'income'));

COMMENT ON COLUMN commcalc.commission_org_config.pl_rebate_presentation IS
  'Mig 934. Device-purchase rebate presentation on the P&L: contra_cogs = negative on '
  'device_rebate inside COGS (ruling K1, house default); income = positive on the rebate_income '
  'revenue line (owner report 2026-09-02). Same dollars, same store attribution, identical gross '
  'profit and net income either way. Resolved by ma_store_pnl.load_config/rebate_route.';

-- ── LUXELINK SEED (org 854f6d7b-6590-4e4d-88ab-646f560d4f4c) — the owner-directed opt-in this
-- migration exists for ("it should be a positive number as it is coming in"). Presentation-only;
-- see MONEY POSTURE above. Idempotent plain UPDATE.
UPDATE commcalc.commission_org_config SET
  pl_rebate_presentation = 'income'
WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';

-- ── OPTIONAL SEED (SEED DATA — NOT auto-applied; needs the owner's own GO, mig-224 pattern).
-- Merchant account 170405 is the ONLY luxelink processor account with no store: it never appears
-- on raw_ma_fulfillment, so the mig-314 automatic map cannot place it and its money books
-- company-wide (August 2026, measured: rebates −6,816.87 · merchant discount 467.97 · residual
-- 574.43 · month spiffs 1,323.00 — visible ONLY in the Consolidated scope today, which is part of
-- the owner's "rebates are not accurate" report). ROW-LEVEL EVIDENCE it is the 104-08 Lefferts
-- Blvd store (the one Nova Wave store with no account): 170405's daily-tx account_name is
-- 'Novawave Communications INC', and its clerk user_names are Salman (135 rows) and Henry (30
-- rows) — exactly the StoreOps employees homed at 'Lefferts' (Henry Costa, Salman) — plus Dixit
-- (350 rows, owner/manager). Uncomment to pin it once the owner confirms:
-- INSERT INTO commcalc.ma_account_store_map (org_id, account_id, store_address, note) VALUES
--   ('854f6d7b-6590-4e4d-88ab-646f560d4f4c', '170405', '104-08 Lefferts Blvd',
--    'owner-pinned: account absent from raw_ma_fulfillment; clerk evidence Salman/Henry = Lefferts')
-- ON CONFLICT (org_id, account_id) DO UPDATE SET store_address = EXCLUDED.store_address;

SELECT 'Migration 934 complete — P&L rebate presentation config installed '
       '(house default contra_cogs byte-identical; luxelink seeded income per owner report 2026-09-02)'
       AS status;
