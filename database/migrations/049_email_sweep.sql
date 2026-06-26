-- 049_email_sweep.sql — generic, configurable EMAIL (IMAP) inbox sweep. Sibling of 046_ftp_sweep.
--
-- A vendor (B2B Soft etc.) can EMAIL report files as attachments instead of FTP-pushing them. This
-- lets the backend poll an IMAP mailbox on a schedule, pull attachments whose filename matches a
-- configured glob, and route each to the right /upload/{type} parser — all configured in the UI
-- (host/creds/mailbox/from-filter/patterns), nothing hard-coded. Same routing + dedup model as FTP.
--
--   email_sweep_config — one row per org. patterns is a JSONB array of
--       [{ "pattern": "*Sales*Transaction*Details*", "upload_type": "daily_sales" }, ...]
--     where upload_type is a supported /upload/{type} key (sales, daily_sales, payment_detail, …).
--   email_processed — every attachment already ingested (by message_id + filename) so a re-run skips it.
--
-- Idempotent. Re-running is safe.

CREATE TABLE IF NOT EXISTS commcalc.email_sweep_config (
  org_id        UUID PRIMARY KEY,
  imap_host     TEXT,
  imap_port     INT DEFAULT 993,
  username      TEXT,
  password      TEXT,                         -- set in the UI; never logged or returned by GET
  use_ssl       BOOLEAN DEFAULT true,         -- IMAP4_SSL (993) when true; STARTTLS (143) when false
  mailbox       TEXT DEFAULT 'INBOX',
  from_filter   TEXT,                         -- optional: only messages whose From contains this
  since_days    INT DEFAULT 14,               -- only scan messages newer than N days (bounds the search)
  patterns      JSONB DEFAULT '[]',           -- [{pattern, upload_type, note}] matched on attachment filename
  enabled       BOOLEAN DEFAULT false,
  frequency     TEXT DEFAULT 'daily',         -- hourly | daily | weekly
  hour          INT DEFAULT 7,
  next_run_at   TIMESTAMPTZ,
  last_run_at   TIMESTAMPTZ,
  last_status   TEXT,
  updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS commcalc.email_processed (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  message_id   TEXT,
  filename     TEXT NOT NULL,
  file_size    BIGINT,
  upload_type  TEXT,
  rows_saved   INT,
  status       TEXT,                          -- ok | error | skipped
  detail       TEXT,
  processed_at TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS email_processed_uq ON commcalc.email_processed (org_id, message_id, filename);

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['commcalc.email_sweep_config','commcalc.email_processed'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

INSERT INTO commcalc.email_sweep_config (org_id) VALUES ('00000000-0000-0000-0000-000000000001')
ON CONFLICT (org_id) DO NOTHING;
