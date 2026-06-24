-- 035_vip_asset_ledger_sweep.sql
-- Toggle for the VIP Asset-Lending auto-sweep. The VIP sweep now also downloads Asset_Lending.xlsx
-- (GET /paygodashboard/DownloadAssetLanding — the "Asset Lending" download icon on the dealer
-- /account/dashboard) and refreshes commcalc.asset_ledger via the asset module's upload processing.
-- Defaults ON so existing configs start refreshing the ledger on their next VIP sweep; set false to
-- skip. Reuses the existing VIP credentials + schedule (no new creds/cron needed).
ALTER TABLE commcalc.vip_sweep_config
  ADD COLUMN IF NOT EXISTS sweep_asset_ledger BOOLEAN DEFAULT true;
