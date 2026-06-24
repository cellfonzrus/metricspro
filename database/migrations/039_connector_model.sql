-- 039_connector_model.sql
-- SaaS framework Phase 2: the unified connector model. One registry of every vendor portal
-- (connector_instances) + every report it provides (report_definitions), generalizing the scattered
-- *_sweep_config tables + the hardcoded Upload-Wizard list + epay_sweep.REPORTS. Creds/schedule still
-- live in each connector's existing *_sweep_config (referenced by config_table); this layer is the
-- single source of truth for "what reports exist, where they come from, auto vs manual, last status",
-- and run-now dispatches to the existing sweep by sweep_kind. See docs/SAAS_FRAMEWORK.md §4.

CREATE TABLE IF NOT EXISTS commcalc.connector_instances (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  carrier_id   UUID,
  vendor_name  TEXT NOT NULL,             -- ePay | VIP Wireless | Elevate Go | B2B Soft | Yoobic | Google
  label        TEXT,
  sweep_kind   TEXT,                       -- epay | vip | dlar | b2b | google_closing | manual (dispatch)
  portal_url   TEXT,
  auth_type    TEXT DEFAULT 'form',        -- form | oauth | api_key | service_account | manual
  twofa_method TEXT DEFAULT 'none',        -- none | sms | totp | email | biometric
  twofa_status TEXT DEFAULT 'ok',          -- ok | needs_setup | blocked
  automatable  BOOLEAN DEFAULT true,       -- false ⇒ manual-upload fallback
  enabled      BOOLEAN DEFAULT true,
  config_table TEXT,                        -- the *_sweep_config holding creds + schedule + last_* status
  sort_order   INT DEFAULT 100,
  notes        TEXT,
  created_at   TIMESTAMPTZ DEFAULT now(),
  updated_at   TIMESTAMPTZ DEFAULT now(),
  UNIQUE (org_id, vendor_name)
);

CREATE TABLE IF NOT EXISTS commcalc.report_definitions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  connector_id    UUID,
  report_key      TEXT NOT NULL,           -- mi_report | comp_report | dlar_rep | vip_workbook | ...
  label           TEXT,
  source_name     TEXT,                     -- exact report name in the portal
  report_id       TEXT,                     -- portal internal id
  period_mode     TEXT DEFAULT 'current',   -- data | report_month | current | snapshot
  target_table    TEXT,
  upload_endpoint TEXT,                     -- manual upload route
  source_url      TEXT,
  auto            BOOLEAN DEFAULT false,    -- auto-swept vs manual upload
  refresh_months  INT DEFAULT 1,
  sort_order      INT DEFAULT 100,
  note            TEXT,
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now(),
  UNIQUE (org_id, report_key)
);
CREATE INDEX IF NOT EXISTS report_defs_connector ON commcalc.report_definitions (org_id, connector_id);

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['commcalc.connector_instances', 'commcalc.report_definitions'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

-- ── Seed the known connectors ────────────────────────────────────────────────────────────────
INSERT INTO commcalc.connector_instances
  (org_id, vendor_name, label, sweep_kind, portal_url, auth_type, twofa_method, twofa_status, automatable, config_table, sort_order)
VALUES
  ('00000000-0000-0000-0000-000000000001','ePay','ePay Owner Portal','epay','https://ownerportal.epayworldwide.com','form','none','ok',true,'epay_sweep_config',10),
  ('00000000-0000-0000-0000-000000000001','VIP Wireless','VIP dealer portal','vip','https://www.vipwireless.com','form','none','ok',true,'vip_sweep_config',20),
  ('00000000-0000-0000-0000-000000000001','Elevate Go','Boost Elevate Go (DLAR)','dlar','https://boostelevatego.com','form','none','ok',true,'dlar_sweep_config',30),
  ('00000000-0000-0000-0000-000000000001','B2B Soft','B2B Soft wsreports','b2b','https://wsreports.b2bsoft.com','form','sms','blocked',false,'b2b_sweep_config',40),
  ('00000000-0000-0000-0000-000000000001','Google','Daily-closing responses sheet','google_closing','https://docs.google.com','service_account','none','needs_setup',true,'closing_sweep_config',50),
  ('00000000-0000-0000-0000-000000000001','Yoobic','Pricing hotsheet (Knowledge Library)','manual','https://app.yoobic.com','form','biometric','blocked',false,NULL,60)
ON CONFLICT (org_id, vendor_name) DO NOTHING;

-- ── Seed the reports each connector provides ─────────────────────────────────────────────────
INSERT INTO commcalc.report_definitions
  (org_id, connector_id, report_key, label, source_name, report_id, period_mode, target_table, upload_endpoint, source_url, auto, refresh_months, sort_order)
SELECT '00000000-0000-0000-0000-000000000001', ci.id, v.rk, v.lbl, v.src, v.rid, v.pm, v.tt, v.ep, ci.portal_url, v.auto, v.rm, v.so
FROM commcalc.connector_instances ci
JOIN (VALUES
  ('ePay','mi_report','MI & ATU','Monthly Incentive & ATU Subscriber Details','102817','data','raw_mi','commcalc/upload/mi_report',true,3,10),
  ('ePay','comp_report','Comprehensive Comp','Comprehensive Compensation Report','100614','data','raw_comp_report','commcalc/upload/comp_report',true,3,20),
  ('ePay','payment_detail','Payment Detail','Commission Payment Detail','50273','current','raw_payment_detail','commcalc/upload/payment_detail',true,1,30),
  ('Elevate Go','dlar_rep','DLAR Rep KPI','DLAR — Rep report',NULL,'current','raw_dlar_rep','commcalc/upload/dlar_rep',true,1,10),
  ('Elevate Go','dlar_store','DLAR Store KPI','DLAR — Store / Advocate report',NULL,'current','raw_dlar_store','commcalc/upload/dlar_store',true,1,20),
  ('VIP Wireless','vip_workbook','VIP Workbook','Invoices / PayGo workbook',NULL,'current','vip_invoices','commcalc/vip/upload',true,1,10),
  ('VIP Wireless','asset_ledger','Asset Ledger','Asset Lending (DownloadAssetLanding)',NULL,'snapshot','asset_ledger','asset/upload',true,1,20),
  ('VIP Wireless','vip_chargebacks','Chargebacks','Dealer chargebacks (DownloadFile)',NULL,'snapshot','chargeback_review',NULL,true,1,30),
  ('B2B Soft','sales','Sales Transactions','Sales Transaction Details (78-col)',NULL,'current','raw_sales','commcalc/upload/sales',false,1,10),
  ('B2B Soft','inventory','Inventory Aging','Inventory Aging',NULL,'snapshot','inventory_value',NULL,false,1,20),
  ('Google','daily_closing','Daily Closing Sheet','Envelopes Data (Responses)',NULL,'snapshot','daily_closing','closing/upload',true,1,10),
  ('Yoobic','hotsheet','Pricing Hotsheet','Boost pricing hotsheet',NULL,'snapshot','hotsheet','commcalc/hotsheet/upload',false,1,10)
) AS v(vendor, rk, lbl, src, rid, pm, tt, ep, auto, rm, so) ON ci.vendor_name = v.vendor
WHERE ci.org_id = '00000000-0000-0000-0000-000000000001'
ON CONFLICT (org_id, report_key) DO NOTHING;
