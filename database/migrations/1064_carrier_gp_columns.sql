-- 1064 — WHICH GROSS-PROFIT COLUMN A CARRIER COMPONENT LANDS IN (index §58.7, owner report 2026-10-08)
--
-- Owner: "gross profit is still showing the old data m teh source of information should be the same".
--
-- WHAT THIS MIGRATION DOES: adds the per-org override for the GP side of the ONE classification home
-- (backend/app/modules/commcalc/carrier_dollar_class.py — `gp_column`), the exact twin of the
-- `carrier_component_lines` knob mig 1062 added for the P&L side. The code is ADAPTIVE and ships
-- with house defaults that reproduce what the GP columns have always MEANT, so WITHOUT this
-- migration every org's Gross Profit report is byte-identical to the merged code. Applying it only
-- lets an org ROUTE a component somewhere else.
--
-- NOT money-touching on its own: the DDL adds two nullable columns and UPDATEs nothing. It is
-- surfaced for owner approval anyway, because changing a value in these columns moves which column
-- a carrier dollar is displayed in. Nothing here recomputes a stored P&L or a stored GP snapshot.
--
-- Idempotent and additive. Columns only; no row is updated by the DDL.

BEGIN;

ALTER TABLE commcalc.commission_org_config
  -- {component (RESIDUAL|COMMISSION|SPIFF|REIMBURSEMENT) -> GP money column}
  -- GP money columns: comm | reimb | mdf | chb | unmapped (commcalc/carrier_dollar_class.GP_COLUMNS).
  -- A component with no row lands in the unclassified column and is REPORTED — never folded into
  -- commission, which is the defect §58.7 exists to end. Empty/NULL keeps the house default:
  --   {"COMMISSION": "comm", "SPIFF": "comm", "RESIDUAL": "comm", "REIMBURSEMENT": "reimb"}
  ADD COLUMN IF NOT EXISTS carrier_gp_component_columns jsonb,
  -- {payment_categories.category (lower, space-collapsed) -> GP money column} — consulted ONLY for
  -- the GP columns the four-component vocabulary has no component for, so an MDF or chargeback
  -- dollar keeps its own column. Empty/NULL keeps the house default: {"mdf": "mdf",
  -- "chargeback": "chb"}.
  ADD COLUMN IF NOT EXISTS carrier_gp_category_columns  jsonb;

COMMENT ON COLUMN commcalc.commission_org_config.carrier_gp_component_columns IS
  'index §58.7 — which Gross Profit money column each carrier component lands in. The GP twin of '
  'carrier_component_lines (the P&L line). Empty/NULL keeps the house default, which reproduces '
  'the columns as they have always been meant.';
COMMENT ON COLUMN commcalc.commission_org_config.carrier_gp_category_columns IS
  'index §58.7 — the GP column for a DECLARED CATEGORY the component vocabulary has no component '
  'for (MDF, chargeback). Empty/NULL keeps the house default.';

COMMIT;

-- REVERT:
--   ALTER TABLE commcalc.commission_org_config
--     DROP COLUMN IF EXISTS carrier_gp_component_columns,
--     DROP COLUMN IF EXISTS carrier_gp_category_columns;
--   (The code degrades to the house defaults when the columns are absent, so the revert is safe and
--    leaves every report byte-identical.)
