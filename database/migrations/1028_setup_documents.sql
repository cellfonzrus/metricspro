-- 1028_setup_documents.sql — the per-carrier REQUIRED DOCUMENTS a new tenant uploads at setup (index §39).
--
-- OWNER (2026-09-27): "Create a list of documents which need to be uploaded for each carrier to get all the required
-- reports and queries, create them as a part of super admin console and attach them by default when the tenant is set
-- up, then on the tenant side the first page which opens up is the set up wizard which requires the tenant to upload
-- these files, tell the tenant where to download those files from … tell them to add credentials if they want
-- automated data updating if we have a successful history of doing that … create reminders for the tenant to upload
-- the files on the period as chosen by the tenant."
--
-- DUPLICATE CHECK: the list IS the report-kind registry (mig 1010) — already per carrier / POS / vertical, house rows
-- are the platform default every tenant inherits at read time ("attached by default" needs no copy). This migration
-- only ADDS the setup facts that registry lacked. The tenant's chosen period lives on the EXISTING core.import_feed row
-- (cadence_hours, mig 717); reminders ride the EXISTING alert pipeline (storeops.alert_recipient / alert_log, mig 089);
-- automation history rides the EXISTING core.job_run + commcalc.upload_trace. No new table.
--
-- Additive, idempotent, moves no money. The seeded values below are the platform's starting list and are edited on the
-- Super Admin Toolbox → Carrier Documents page afterwards (the UPDATEs only fill a column that is still empty, so a
-- re-run never overwrites an edit). The download steps are taken from the report names and portal links already on
-- file for live tenants (commcalc.report_definitions) — nothing is invented.
--
-- REVERT:
--   ALTER TABLE commcalc.report_kind DROP COLUMN IF EXISTS required, DROP COLUMN IF EXISTS default_cadence,
--     DROP COLUMN IF EXISTS download_url, DROP COLUMN IF EXISTS download_steps, DROP COLUMN IF EXISTS evidence_table,
--     DROP COLUMN IF EXISTS upload_path,
--     DROP COLUMN IF EXISTS automation_min_runs, DROP COLUMN IF EXISTS automation_window_days;
--   ALTER TABLE storeops.tenants DROP COLUMN IF EXISTS documents_setup_done_at;
--   NOTIFY pgrst, 'reload schema';

ALTER TABLE commcalc.report_kind ADD COLUMN IF NOT EXISTS required BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE commcalc.report_kind ADD COLUMN IF NOT EXISTS default_cadence TEXT NOT NULL DEFAULT 'monthly';
ALTER TABLE commcalc.report_kind ADD COLUMN IF NOT EXISTS download_url TEXT;
ALTER TABLE commcalc.report_kind ADD COLUMN IF NOT EXISTS download_steps TEXT;
ALTER TABLE commcalc.report_kind ADD COLUMN IF NOT EXISTS evidence_table TEXT;
ALTER TABLE commcalc.report_kind ADD COLUMN IF NOT EXISTS upload_path TEXT;
ALTER TABLE commcalc.report_kind ADD COLUMN IF NOT EXISTS automation_min_runs INTEGER NOT NULL DEFAULT 3;
ALTER TABLE commcalc.report_kind ADD COLUMN IF NOT EXISTS automation_window_days INTEGER NOT NULL DEFAULT 60;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'report_kind_default_cadence_chk') THEN
    ALTER TABLE commcalc.report_kind ADD CONSTRAINT report_kind_default_cadence_chk
      CHECK (default_cadence IN ('daily', 'weekly', 'monthly'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'report_kind_automation_bounds_chk') THEN
    ALTER TABLE commcalc.report_kind ADD CONSTRAINT report_kind_automation_bounds_chk
      CHECK (automation_min_runs BETWEEN 1 AND 1000 AND automation_window_days BETWEEN 1 AND 365);
  END IF;
END $$;

-- A tenant is walked to the setup wizard until its required documents are in. Every tenant that exists TODAY is
-- stamped done, so no live company is suddenly redirected; a super admin can re-open the wizard for one tenant.
ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS documents_setup_done_at TIMESTAMPTZ;
UPDATE storeops.tenants SET documents_setup_done_at = now() WHERE documents_setup_done_at IS NULL;

-- ── THE PLATFORM'S STARTING LIST (house rows; mirrored byte-for-byte in commcalc/setup_documents.HOUSE_SETUP) ────
UPDATE commcalc.report_kind SET required = true, default_cadence = 'daily',
  download_url = COALESCE(download_url, 'https://wsreports.b2bsoft.com'),
  download_steps = COALESCE(download_steps, 'Open your POS reports. On B2B Soft: wsreports.b2bsoft.com → Reports → Sales Transaction Details (the 78-column report). Pick the day, export to Excel, then upload the file here.')
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'sales_imei_phone' AND download_steps IS NULL;

UPDATE commcalc.report_kind SET required = true, default_cadence = 'weekly',
  download_url = COALESCE(download_url, 'https://wsreports.b2bsoft.com'),
  download_steps = COALESCE(download_steps, 'Open your POS reports. On B2B Soft: wsreports.b2bsoft.com → Reports → Inventory Aging. Export to Excel, then upload the file here.')
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'inventory_aging' AND download_steps IS NULL;

UPDATE commcalc.report_kind SET required = true, default_cadence = 'monthly',
  download_url = COALESCE(download_url, 'https://ownerportal.epayworldwide.com'),
  download_steps = COALESCE(download_steps, 'Sign in to the ePay owner portal (ownerportal.epayworldwide.com) → Reports → Commission Payment Detail. Set the month, export to Excel, then upload the file here.')
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'payment_detail' AND download_steps IS NULL;

UPDATE commcalc.report_kind SET required = true, default_cadence = 'monthly',
  download_url = COALESCE(download_url, 'https://ownerportal.epayworldwide.com'),
  download_steps = COALESCE(download_steps, 'Sign in to the ePay owner portal (ownerportal.epayworldwide.com) → Reports → Monthly Incentive & ATU Subscriber Detail. Set the month, export to Excel, then upload the file here.')
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'mi_report' AND download_steps IS NULL;

UPDATE commcalc.report_kind SET required = true, default_cadence = 'monthly',
  download_url = COALESCE(download_url, 'https://ownerportal.epayworldwide.com'),
  download_steps = COALESCE(download_steps, 'Sign in to the ePay owner portal (ownerportal.epayworldwide.com) → Reports → Comprehensive Compensation Report. Set the month, export to Excel, then upload the file here.')
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'comp_report' AND download_steps IS NULL;

UPDATE commcalc.report_kind SET required = true, default_cadence = 'daily',
  download_url = COALESCE(download_url, 'https://boostelevatego.com'),
  download_steps = COALESCE(download_steps, 'Sign in to Elevate Go (boostelevatego.com) → DLAR - Rep report. Export the month-to-date view to Excel, then upload the file here.')
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'dlar_rep' AND download_steps IS NULL;

UPDATE commcalc.report_kind SET required = true, default_cadence = 'daily',
  download_url = COALESCE(download_url, 'https://boostelevatego.com'),
  download_steps = COALESCE(download_steps, 'Sign in to Elevate Go (boostelevatego.com) → DLAR - Store / Advocate report. Export the month-to-date view to Excel, then upload the file here.')
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'dlar_store' AND download_steps IS NULL;

UPDATE commcalc.report_kind SET required = true, default_cadence = 'monthly',
  download_url = COALESCE(download_url, 'https://www.vidapaycrm.com/Reports.aspx'),
  download_steps = COALESCE(download_steps, 'Sign in to the VidaPay portal (vidapaycrm.com) → Reports → MA Commission Details. Set the date range, export to Excel, then upload the file here. One file per range; do not merge months.')
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'ma_commission' AND download_steps IS NULL;

UPDATE commcalc.report_kind SET required = true, default_cadence = 'daily',
  download_url = COALESCE(download_url, 'https://www.vidapaycrm.com/Reports.aspx'),
  download_steps = COALESCE(download_steps, 'Sign in to the VidaPay portal (vidapaycrm.com) → Reports → MA Daily Tx (SubMA). Set the date range, export to Excel, then upload the file here. This is the only source for total residual.')
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'ma_daily_tx' AND download_steps IS NULL;

UPDATE commcalc.report_kind SET required = true, default_cadence = 'monthly',
  evidence_table = COALESCE(evidence_table, 'royalty_report'), upload_path = COALESCE(upload_path, '/accounts/royalty'),
  download_steps = COALESCE(download_steps, 'Sign in to the franchisor''s center-management portal and open the monthly royalty report for your center. Save the page (or print it to PDF), then upload it on Finance → Royalty Report.')
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'royalty_report' AND download_steps IS NULL;

-- The two DLAR reports come from Boost's Elevate Go portal and only a Boost tenant has ever landed one (live: every
-- raw_dlar_rep / raw_dlar_store row belongs to the one Boost tenant). They were seeded scoped to no carrier, so a
-- Total or Verizon tenant would be asked for them. Registry DATA fix, only while still unscoped (an edit is kept).
UPDATE commcalc.report_kind SET applies_to_carrier = '{boost}'
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key IN ('dlar_rep', 'dlar_store') AND applies_to_carrier = '{}';

-- Optional (not required) documents still get their download steps, so the wizard can say where they come from.
UPDATE commcalc.report_kind SET default_cadence = 'monthly',
  download_url = COALESCE(download_url, 'https://www.vidapaycrm.com/Reports.aspx'),
  download_steps = COALESCE(download_steps, 'Sign in to the VidaPay portal (vidapaycrm.com) → Reports → MA Marketplace Handset Fulfillment Orders. Set the date range, export to Excel, then upload the file here.')
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'ma_fulfillment' AND download_steps IS NULL;

UPDATE commcalc.report_kind SET default_cadence = 'weekly',
  download_url = COALESCE(download_url, 'https://www.vipwireless.com'),
  download_steps = COALESCE(download_steps, 'Sign in to the VIP Wireless dealer portal (vipwireless.com) → Invoices / PayGo workbook. Download the workbook, then upload it here.')
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'vip_workbook' AND download_steps IS NULL;

UPDATE commcalc.report_kind SET default_cadence = 'weekly',
  download_url = COALESCE(download_url, 'https://www.vipwireless.com'),
  download_steps = COALESCE(download_steps, 'Sign in to the VIP Wireless dealer portal (vipwireless.com) → Asset Lending. Download the asset ledger, then upload it here.')
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'asset_ledger' AND download_steps IS NULL;

UPDATE commcalc.report_kind SET default_cadence = 'weekly',
  download_url = COALESCE(download_url, 'https://app.yoobic.com'),
  download_steps = COALESCE(download_steps, 'Sign in to Yoobic (app.yoobic.com) and open the current Boost pricing hotsheet. Download it, then upload it here.')
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'hotsheet' AND download_steps IS NULL;

UPDATE commcalc.report_kind SET default_cadence = 'daily'
  WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'x_report' AND default_cadence = 'monthly';

COMMENT ON COLUMN commcalc.report_kind.required IS 'Setup wizard: the tenant must upload this document (index §39). Edited on Super Admin Toolbox → Carrier Documents.';
COMMENT ON COLUMN commcalc.report_kind.default_cadence IS 'How often the document is expected (daily/weekly/monthly) until the tenant picks its own period (core.import_feed.cadence_hours).';
COMMENT ON COLUMN commcalc.report_kind.download_steps IS 'Where / how the tenant downloads this document — shown in the setup wizard.';
COMMENT ON COLUMN commcalc.report_kind.evidence_table IS 'For a document with no upload route: the landing table (commcalc schema, created_at) that proves it arrived.';
COMMENT ON COLUMN commcalc.report_kind.upload_path IS 'For a document uploaded on its own page (no upload route): that page, e.g. /accounts/royalty.';
COMMENT ON COLUMN commcalc.report_kind.automation_min_runs IS 'Successful automated runs (any tenant) inside automation_window_days before the wizard offers credentials.';
COMMENT ON COLUMN storeops.tenants.documents_setup_done_at IS 'When the tenant finished uploading its required setup documents (NULL = walk admins to the setup wizard). Index §39.';

NOTIFY pgrst, 'reload schema';
