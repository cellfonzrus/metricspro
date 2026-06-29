-- 066_commission_field_catalog.sql — make commission CATEGORIES data, not hard-coded.
--
-- WHY: carrier_commission's categories (spiff_m1..m6, rebate, residual, margins…) are hard-coded in
-- Python (column_mapping.TARGET_FIELDS) AND as fixed columns. A new carrier whose sheet has a category
-- we don't have (7th-month spiff, NAB bounty, port-in incentive, tablet bonus) can't be mapped → data
-- lost. This catalog stores each commission category as a ROW: a physical column on carrier_commission,
-- its human label, what KIND it is, whether it's an amount (summed into total_commission), and (for
-- monthly comm/spiff) which payout month it is. The wizard reads this to offer categories, and writes a
-- new row + a new physical column (via fn add_commission_column, migration 067) when the user creates one.
--
-- ADDITIVE + IDEMPOTENT + BOOST-SAFE: only the NEW carrier_commission table is involved; the live Boost
-- calc, rep_commissions, and the legacy upload_file branches are untouched. The backend MERGES this
-- catalog on top of its hard-coded defaults, so everything still works before this runs (catalog empty →
-- pure fallback to the seeded Python defaults).

CREATE TABLE IF NOT EXISTS commcalc.commission_field_catalog (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  report_key    TEXT NOT NULL DEFAULT 'carrier_commission',
  target_field  TEXT NOT NULL,                 -- the physical column on carrier_commission (sanitised name)
  label         TEXT,                           -- human label shown in the wizard
  kind          TEXT DEFAULT 'other',           -- identity | comm_month | spiff | rebate | residual | margin | fee | bounty | other
  data_type     TEXT DEFAULT 'number',          -- maps to a column_mapping transform: number|text|int|date10|mdn|bool
  is_amount     BOOLEAN DEFAULT true,           -- true → summed into carrier_commission.total_commission
  month_index   INT,                            -- for comm_month/spiff: which payout month (1..N); else NULL
  sort_order    INT DEFAULT 100,
  is_seeded     BOOLEAN DEFAULT false,          -- true = shipped default; false = user-created in the wizard
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, report_key, target_field)
);
CREATE INDEX IF NOT EXISTS commission_field_catalog_lookup
  ON commcalc.commission_field_catalog (org_id, report_key, sort_order);

ALTER TABLE commcalc.commission_field_catalog ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='commission_field_catalog' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.commission_field_catalog FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

-- ── Seed the categories that already exist as fixed columns on carrier_commission (house org) ──────────
-- These mirror column_mapping.TARGET_FIELDS['carrier_commission']. Idempotent via UNIQUE + ON CONFLICT.
INSERT INTO commcalc.commission_field_catalog
  (org_id, report_key, target_field, label, kind, data_type, is_amount, month_index, sort_order, is_seeded)
VALUES
  ('00000000-0000-0000-0000-000000000001','carrier_commission','rep_name','Rep name','identity','text',false,NULL,1,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','rep_user_id','Rep user id','identity','text',false,NULL,2,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','store','Store / merchant account','identity','text',false,NULL,3,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','account_id','Account id','identity','text',false,NULL,4,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','carrier_name','Carrier name','identity','text',false,NULL,5,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','trans_date','Date','identity','date10',false,NULL,6,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','activation_type','Activation type','identity','text',false,NULL,7,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','sub_type','Sub type','identity','text',false,NULL,8,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','sku','SKU','identity','text',false,NULL,9,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','imei','IMEI','identity','mdn',false,NULL,10,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','mdn','MDN','identity','mdn',false,NULL,11,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','order_id','Order id','identity','text',false,NULL,12,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','device_margin','Device margin','margin','number',true,NULL,20,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','consumer_margin','Consumer margin','margin','number',true,NULL,21,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','rebate','Rebate','rebate','number',true,NULL,22,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','mrc_net_discount','MRC net discount','margin','number',true,NULL,23,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','fees_margin','Fees margin','fee','number',true,NULL,24,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','spiff_m1','1st month spiff','spiff','number',true,1,31,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','spiff_m2','2nd month spiff','spiff','number',true,2,32,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','spiff_m3','3rd month spiff','spiff','number',true,3,33,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','spiff_m4','4th month spiff','spiff','number',true,4,34,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','spiff_m5','5th month spiff','spiff','number',true,5,35,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','spiff_m6','6th month spiff','spiff','number',true,6,36,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','residual','Residual','residual','number',true,NULL,40,true),
  ('00000000-0000-0000-0000-000000000001','carrier_commission','other_amount','Other amount','other','number',true,NULL,50,true)
ON CONFLICT (org_id, report_key, target_field) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 066 complete — commcalc.commission_field_catalog (' ||
       (SELECT count(*) FROM commcalc.commission_field_catalog) || ' rows)' AS status;
