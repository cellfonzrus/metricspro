-- MIGRATION 867: ACTIVATION-REPORT P&L FEED (vendor rebate/commission history import)
-- ─────────────────────────────────────────────────────────────────────────────────────────────
-- Owner request (2026-08): a tenant uploads the carrier's Vendor Rebate/Commission History export;
-- the commission feeds the P&L (carrier_comm revenue) and the device rebate nets against device
-- cost (device_rebate contra-COGS), matching the existing accounting model.
--
-- WHY A DEDICATED TABLE (not commcalc.raw_comp_report): raw_comp_report is REPLACED WHOLESALE per
-- period by the Comprehensive Compensation Report upload (see commcalc/safe_replace.py) — injecting
-- these rows there would let one upload wipe the other. This ledger is a separate, collision-free
-- source that app/modules/account/coa.py reads ALONGSIDE raw_comp_report. One aggregated row per
-- (store, period, source); a re-upload UPSERTs the same key, so re-running is idempotent.

CREATE TABLE IF NOT EXISTS commcalc.activation_rebate_ledger (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               UUID NOT NULL,
  business_address     TEXT,                                   -- store (same key coa uses: _norm_store)
  period               TEXT NOT NULL,                          -- 'YYYY-MM'
  source               TEXT NOT NULL DEFAULT 'vendor_rebate_report',
  commission_amount    NUMERIC(14,2) NOT NULL DEFAULT 0,       -- → carrier_comm (revenue)
  device_rebate_amount NUMERIC(14,2) NOT NULL DEFAULT 0,       -- → device_rebate (contra-COGS, booked negative)
  device_cost          NUMERIC(14,2) NOT NULL DEFAULT 0,       -- reference only; NOT posted (cost comes from sales)
  activations          INTEGER NOT NULL DEFAULT 0,
  updated_at           TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, business_address, period, source)
);

CREATE INDEX IF NOT EXISTS activation_rebate_ledger_org_period
  ON commcalc.activation_rebate_ledger(org_id, period);
