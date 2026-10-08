-- 1062 — CARRIER-DOLLAR CLASSIFICATION AND ROUTING (index §58, owner report 2026-10-08)
--
-- Owner: "i just checked the commission details for boost, the commission is over stated as the
-- device reimbursement is being added in the commision and also in device reimbursement … need to
-- move that amount from commssiomn to teh device reimbursement", and "distributors payments are not
-- in additon to the reimbursement they are the same payments but the discrepancy nbetween them
-- shows that the distributor claims it was paid bunt epay never paid it".
--
-- WHAT THIS MIGRATION DOES: adds the per-org knobs the ONE classification home
-- (backend/app/modules/commcalc/carrier_dollar_class.py) reads. The code is ADAPTIVE and already
-- merged: without these columns every org keeps the house defaults and every statement is
-- byte-identical, so applying this migration is what MOVES money. Nothing here recomputes a stored
-- P&L — that is a separate, owner-run step (see the restatement table in index §58.4).
--
-- MONEY-TOUCHING. Surfaced for owner approval; DO NOT apply unasked.
--
-- Idempotent and additive. Columns only; no row is updated by the DDL.

BEGIN;

ALTER TABLE commcalc.commission_org_config
  -- does the org's OWN payment_categories declaration beat carrier_category_map's keyword ladder?
  ADD COLUMN IF NOT EXISTS carrier_class_declaration_wins   boolean,
  -- {payment_categories.category (lower, space-collapsed) -> RESIDUAL|COMMISSION|SPIFF|REIMBURSEMENT}
  ADD COLUMN IF NOT EXISTS carrier_class_category_components jsonb,
  -- carry an undeclared, period-renamed payment type's category forward from its earlier-period twin?
  -- (the result is always LABELLED `inferred_prior_year_twin`, never as the org's own declaration)
  ADD COLUMN IF NOT EXISTS carrier_class_rename_inference    boolean,
  -- the regex shape of a period-renamed type; must name the groups `period` and `stem`
  ADD COLUMN IF NOT EXISTS carrier_class_rename_pattern      text,
  -- how many distinct earlier periods an inference may reach back through
  ADD COLUMN IF NOT EXISTS carrier_class_rename_lookback     integer,
  -- {component -> P&L line key}; a component with no row books to the P&L's default line
  ADD COLUMN IF NOT EXISTS carrier_component_lines           jsonb,
  -- 'carrier_paid' (the line carries what the carrier PAID; the distributor's asset-ledger figure is
  -- held as a CLAIM and books no revenue) | 'distributor_claim' (the pre-2026-10-08 posture)
  ADD COLUMN IF NOT EXISTS pl_device_reimb_source            text;

COMMENT ON COLUMN commcalc.commission_org_config.carrier_component_lines IS
  'index §58 — which P&L line each carrier component books to. Empty/NULL keeps every component on '
  'the P&L default line (carrier_comm), which is the pre-2026-10-08 posture.';
COMMENT ON COLUMN commcalc.commission_org_config.pl_device_reimb_source IS
  'index §58.3 — one payment, two sides. carrier_paid: the device-financing reimbursement line '
  'carries what the carrier actually paid and the distributor ledger is a CLAIM (no revenue). '
  'distributor_claim: the legacy posture.';

-- ── THE HOUSE ORG'S OWN POSTURE (the two switches the owner asked for) ──────────────────────────
-- Applying this block is what restates the books. Index §58.4 quantifies it per period:
--   carrier_comm  Mar–Oct 2026  4,025,856.73 -> 903,915.85   (3,121,940.88 reclassified)
--   vip_reimb     Mar–Oct 2026  2,928,645.35 -> 3,121,940.88 (the distributor claim de-recognised)
--   net revenue   Mar–Oct 2026  -2,928,645.35; all-time claim de-recognised 9,635,761.76
-- Of the reclassification, 1,078,862.83 rests on a period-rename INFERENCE, not on the org's own
-- declaration. Declaring those payment types in commcalc.payment_categories turns each inference
-- into a fact; until then the statement's `_carrier_class_coverage` side entry says which is which.
UPDATE commcalc.commission_org_config
   SET carrier_component_lines = '{"REIMBURSEMENT": "vip_reimb"}'::jsonb,
       pl_device_reimb_source  = 'carrier_paid'
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND carrier_component_lines IS NULL
   AND pl_device_reimb_source IS NULL;

COMMIT;

-- REVERT:
--   UPDATE commcalc.commission_org_config
--      SET carrier_component_lines = NULL, pl_device_reimb_source = NULL
--    WHERE org_id = '00000000-0000-0000-0000-000000000001';
--   ALTER TABLE commcalc.commission_org_config
--     DROP COLUMN IF EXISTS carrier_class_declaration_wins,
--     DROP COLUMN IF EXISTS carrier_class_category_components,
--     DROP COLUMN IF EXISTS carrier_class_rename_inference,
--     DROP COLUMN IF EXISTS carrier_class_rename_pattern,
--     DROP COLUMN IF EXISTS carrier_class_rename_lookback,
--     DROP COLUMN IF EXISTS carrier_component_lines,
--     DROP COLUMN IF EXISTS pl_device_reimb_source;
--   A stored P&L computed while the switches were on is NOT reverted by this; re-run the recompute.
