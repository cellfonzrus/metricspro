-- 096_phone_model_map.sql
-- PHONE MAPPING TABLE (the model-name alignment the forecast needs, now carrier-aware).
--
-- WHY: sold-side model strings (raw_sales.product_desc, e.g. "Apple iPhone 16e ... - $599.99") and
-- stocked-side strings (asset_ledger.device_model, e.g. "BAppleiPhone16e-128GB Black") are independent
-- free text, so velocity never lines up with on-hand and the forecast can't split by carrier. This turns
-- commcalc.device_model_alias (created empty in mig 095) INTO the phone mapping table: each raw model
-- string → a canonical model + the CARRIER it belongs to. Curating it is an onboarding to-do (the
-- forecast surfaces every unmapped raw string as a candidate).
--
-- Additive + idempotent. Run in the Supabase SQL editor (after 095).

ALTER TABLE commcalc.device_model_alias
  ADD COLUMN IF NOT EXISTS carrier_id UUID,                 -- commcalc.carrier.id this model belongs to
  ADD COLUMN IF NOT EXISTS side       TEXT,                 -- 'sales' | 'inventory' | 'both' (where the raw string appears)
  ADD COLUMN IF NOT EXISTS source     TEXT DEFAULT 'manual';-- 'auto' | 'manual'

CREATE INDEX IF NOT EXISTS dma_carrier ON commcalc.device_model_alias (org_id, carrier_id);

-- (RLS open_all + the base table already exist from 095.)
