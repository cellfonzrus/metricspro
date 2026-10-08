-- 1061_device_reimb_recon_config.sql
-- ─────────────────────────────────────────────────────────────────────────────────────────────────
-- ePAY-PAID vs DISTRIBUTOR-CLAIMED DEVICE REIMBURSEMENT — the per-org CONFIG for the reconciliation
-- and its management flag (index §19.52, owner report 2026-10-08).
--
-- OWNER, verbatim:
--   "i just checked the commission details for boost, the commission is over stated as the device
--    reimbursement is being added in the commision and also in device reimbursement ... the report
--    which pays us is same as what is reported in commision 7583.96"
--   "distributors payments are not in additon to the reimbursement they are the same payments but
--    the discrepancy nbetween them shows that the distributor claims it was paid bunt epay never
--    paid it"
--
-- WHAT THIS IS: ONE additive jsonb column on the per-org config table that already holds 40+ such
-- blobs. RULE TWO — the carrier classification keys that are the device-financing side, the
-- distributor category/status spellings that count as a claim, the tolerance, the severity
-- thresholds and the column names all live HERE, as data, per org, with the code's house defaults
-- behind them (`commcalc/device_reimb_recon.py` CODE_DEFAULT). No carrier, tenant, product or
-- quarter name appears in any branch.
--
-- 💰 MOVES NO MONEY. It adds a column that drives a READ-ONLY reconciliation and a visibility flag.
-- No amount, rate, plan, schedule or paid/earned column is touched, and no P&L line is re-booked.
--
-- WHY `carrier_sources` IS SEEDED EMPTY, AND WHAT THAT MEANS
--   Declaring WHICH carrier dollars are device-financing reimbursement is a money decision and the
--   owner's call (§6d precedent), so this migration does NOT make it. With `carrier_sources` empty
--   every store-month reports `no_device_financing_rule_configured` and NOT a $0.00 paid side — the
--   absence is the finding (§19.48/§19.49 house shape). Nothing is reported as reconciled until the
--   declaration is made, and nothing false is reported in the meantime.
--
--   MEASURED LIVE 2026-10-08 (read-only, house org) so the choice can be made with numbers. The
--   $7,583.96 the owner identified at 103 Fulton Ave in September 2026 is three compensation types
--   ("…Promo PIC Offer" $3,964.96, "…Promo Upgrade" $3,204.00, "…Promo New Act Offer" $415.00) which
--   this org's OWN `carrier_category_map` classifies COMMISSION / subtype `promo`. Declaring that
--   key reproduces his figure to the cent against the distributor's $7,999.93 claim. The same
--   store-month ALSO carries $5,082.50 the map classifies REIMBURSEMENT / `subsidy` (ramp-up,
--   trade-in, SIM) which is NOT device financing — which is exactly why a component alone is not a
--   safe guess and this is not seeded blind.
--
--   The declaration, once the owner confirms it (and after the finance module's classification PR
--   lands, which may move those types to a different component — the reconciliation reports
--   `carrier_side_not_classified` rather than a false shortfall if the key stops matching):
--
--     UPDATE commcalc.commission_org_config
--        SET device_reimb_recon_config =
--              COALESCE(device_reimb_recon_config, '{}'::jsonb)
--              || '{"carrier_sources":[{"component":"COMMISSION","subtype":"promo"}]}'::jsonb,
--            updated_at = now()
--      WHERE org_id = '00000000-0000-0000-0000-000000000001';
--
-- ADDITIVE AND IDEMPOTENT: `ADD COLUMN IF NOT EXISTS`, and the house seed only fills a NULL. No
-- existing column, constraint, row or value is changed. A database without this migration behaves
-- exactly as it does today — the reader degrades to the code defaults (contract §5).
--
-- REVERT:
--   ALTER TABLE commcalc.commission_org_config DROP COLUMN IF EXISTS device_reimb_recon_config;
-- ─────────────────────────────────────────────────────────────────────────────────────────────────

BEGIN;

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS device_reimb_recon_config JSONB;

COMMENT ON COLUMN commcalc.commission_org_config.device_reimb_recon_config IS
  'Per-org config for the ePay-paid vs distributor-claimed device-reimbursement reconciliation '
  '(index §19.52). Keys: enabled, tolerance, carrier_sources [{component, subtype}] = WHICH '
  'classified carrier dollars are the device-financing side (empty = nothing declared, every '
  'store-month reports not-measured rather than a $0.00 paid side), distributor_categories, '
  'distributor_statuses, columns, severity_high_at, severity_critical_at, '
  'max_devices_in_evidence. House defaults live in commcalc/device_reimb_recon.py CODE_DEFAULT; '
  'a tenant row overrides the house org row key by key.';

-- House row: everything EXCEPT the money decision. `carrier_sources` stays absent on purpose.
UPDATE commcalc.commission_org_config
   SET device_reimb_recon_config = '{
         "enabled": true,
         "tolerance": 0.01,
         "carrier_sources": [],
         "distributor_categories": [],
         "distributor_statuses": [],
         "severity_high_at": 500.0,
         "severity_critical_at": 2500.0,
         "max_devices_in_evidence": 20
       }'::jsonb,
       updated_at = now()
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND device_reimb_recon_config IS NULL;

-- Post-flight: the column must exist and the house blob must parse as an object with an explicit
-- (possibly empty) carrier_sources array. A half-applied config row would make the reconciliation
-- read a shape it cannot normalise, so this rolls back rather than leaving one.
DO $$
DECLARE n INT;
BEGIN
  SELECT count(*) INTO n
    FROM information_schema.columns
   WHERE table_schema = 'commcalc' AND table_name = 'commission_org_config'
     AND column_name = 'device_reimb_recon_config';
  IF n <> 1 THEN
    RAISE EXCEPTION 'device_reimb_recon_config column was not created';
  END IF;
  SELECT count(*) INTO n
    FROM commcalc.commission_org_config
   WHERE org_id = '00000000-0000-0000-0000-000000000001'
     AND device_reimb_recon_config IS NOT NULL
     AND jsonb_typeof(device_reimb_recon_config) = 'object'
     AND jsonb_typeof(device_reimb_recon_config -> 'carrier_sources') = 'array';
  IF n <> 1 THEN
    RAISE EXCEPTION 'house device_reimb_recon_config row is missing or malformed (found %)', n;
  END IF;
END $$;

COMMIT;
