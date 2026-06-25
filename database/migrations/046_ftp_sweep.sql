-- 046_ftp_sweep.sql — generic, configurable FTP-pull sweep (Theme 6).
--
-- B2B Soft (and any vendor) can PUSH report files to an FTP server we control; this lets the backend
-- PULL them on a schedule and route each file to the right upload parser by filename pattern — all
-- configured in the UI (host/creds/folder/patterns), nothing hard-coded. Unblocks the daily B2B feed
-- for sales recon (Theme 5) + the closing recon.
--
--   ftp_sweep_config — one row per org. patterns is a JSONB array of
--       [{ "pattern": "*Sales-Transaction-Details*", "upload_type": "sales" }, ...]
--     where upload_type is a supported /upload/{type} key (sales, daily_sales, payment_detail, …).
--   ftp_processed — every file already ingested (by name+size) so a re-run skips it.
--
-- Idempotent. Re-running is safe.

CREATE TABLE IF NOT EXISTS commcalc.ftp_sweep_config (
  org_id        UUID PRIMARY KEY,
  host          TEXT,
  port          INT DEFAULT 21,
  username      TEXT,
  password      TEXT,                         -- set in the UI; never logged or returned by GET
  use_tls       BOOLEAN DEFAULT false,        -- FTP_TLS (explicit) when true
  passive       BOOLEAN DEFAULT true,
  remote_dir    TEXT DEFAULT '/',
  patterns      JSONB DEFAULT '[]',           -- [{pattern, upload_type, note}]
  enabled       BOOLEAN DEFAULT false,
  frequency     TEXT DEFAULT 'daily',
  hour          INT DEFAULT 7,
  next_run_at   TIMESTAMPTZ,
  last_run_at   TIMESTAMPTZ,
  last_status   TEXT,
  updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS commcalc.ftp_processed (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  filename     TEXT NOT NULL,
  file_size    BIGINT,
  upload_type  TEXT,
  rows_saved   INT,
  status       TEXT,                          -- ok | error | skipped
  detail       TEXT,
  processed_at TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ftp_processed_uq ON commcalc.ftp_processed (org_id, filename, file_size);

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['commcalc.ftp_sweep_config','commcalc.ftp_processed'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

INSERT INTO commcalc.ftp_sweep_config (org_id) VALUES ('00000000-0000-0000-0000-000000000001')
ON CONFLICT (org_id) DO NOTHING;
