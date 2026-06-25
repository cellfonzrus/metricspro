-- 044_report_recipients.sql
-- Unified "report → designated recipient" routing (Theme 4).
--
-- Today recipients are scattered: notify.subscriptions (per recurring schedule), commcalc
-- cash_pickup_config (one org-level recipient), the SendReportButton picker (ad-hoc), etc. This adds
-- ONE place to say "report X is always sent to these people on these channels" — independent of any
-- schedule. Different reports route to different people (a Boost report → person A, Total → person B;
-- StoreOps → person C ≠ the sales manager).
--
-- notify.report_config: one row per (org, report_key). recipient_ids reference notify.recipients;
-- ad_hoc_* allow one-off email/phone targets too. Any module's "send to the designated person" reads
-- this via POST /api/v1/notify/send-to-designated.
--
-- Idempotent. Re-running is safe.

CREATE TABLE IF NOT EXISTS notify.report_config (
  org_id         UUID NOT NULL,
  report_key     TEXT NOT NULL,
  recipient_ids  UUID[]  DEFAULT '{}',     -- → notify.recipients.id
  ad_hoc_emails  TEXT[]  DEFAULT '{}',
  ad_hoc_phones  TEXT[]  DEFAULT '{}',
  channels       TEXT[]  DEFAULT '{email}',-- {'email','whatsapp'}
  formats        TEXT[]  DEFAULT '{xlsx,pdf}',
  is_active      BOOLEAN DEFAULT true,
  updated_at     TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (org_id, report_key)
);

-- RLS + grants (mirror migration 010 for the notify schema; service_role added explicitly).
ALTER TABLE notify.report_config ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS open_all ON notify.report_config;
CREATE POLICY open_all ON notify.report_config FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
GRANT ALL ON notify.report_config TO anon, authenticated, service_role;
