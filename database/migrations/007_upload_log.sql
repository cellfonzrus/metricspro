-- 007_upload_log.sql
-- Tracks every data-file upload so the Upload page can show, persistently,
-- what has already been uploaded for a given month (no more guessing after a
-- page reload) and render a newest-first history menu.
--
-- Run this in the Supabase SQL editor (Claude cannot run SQL).

CREATE TABLE IF NOT EXISTS commcalc.upload_log (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL,
  file_type   text NOT NULL,             -- sales, daily_sales, payment_detail, ...
  period      text,                       -- "June 2026" (NULL for catalog/master_cats)
  filename    text,                       -- original uploaded filename
  rows_saved  integer NOT NULL DEFAULT 0,
  uploaded_at timestamptz NOT NULL DEFAULT now()
);

-- Per-period lookup (badges) and global newest-first history (menu).
CREATE INDEX IF NOT EXISTS upload_log_org_period_idx
  ON commcalc.upload_log (org_id, period, uploaded_at DESC);
CREATE INDEX IF NOT EXISTS upload_log_org_time_idx
  ON commcalc.upload_log (org_id, uploaded_at DESC);
