-- MIGRATION 713: NOTIFY — no-login signed download of a sent report file
-- Band 700–799 (mod-platform-core). Additive + idempotent (safe to re-run).
--
-- WHY: OWNER DIRECTIVE 2026-07-17 — "the PDF should be sent as is without logging in". When Meta only
-- permits a link on WhatsApp (business-initiated, outside the 24h window, no doc-header template), the
-- link must serve the ACTUAL file with NO login. We persist each sent file here and hand out a signed,
-- expiring, single-file HMAC token (see backend notify.download_token) resolved by GET
-- /api/v1/notify/dl/{token}. The token reaches ONLY the one row — no other org data.
--
-- DEGRADES GRACEFULLY: until this runs, _store_artifact() catches the missing table and the WhatsApp
-- message falls back to the live-report link (today's behavior). The actual-document paths (doc-header
-- template / in-window free-form document) do NOT depend on this table, so they work regardless.

CREATE SCHEMA IF NOT EXISTS notify;   -- already exists from mig 010; harmless

CREATE TABLE IF NOT EXISTS notify.send_artifact (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,                 -- multi-tenant (RULE ONE): every artifact is org-owned
  filename           TEXT NOT NULL,
  mime               TEXT NOT NULL,
  content_b64        TEXT NOT NULL,                 -- the file bytes, base64 (small report files; see NOTE)
  size_bytes         INT,
  report_key         TEXT,
  created_by         TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at         TIMESTAMPTZ NOT NULL,          -- link stops working after this (tenant-configurable, default 7d)
  download_count     INT NOT NULL DEFAULT 0,
  last_downloaded_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS send_artifact_org_idx     ON notify.send_artifact(org_id);
CREATE INDEX IF NOT EXISTS send_artifact_expires_idx ON notify.send_artifact(expires_at);

-- NOTE (storage choice): file bytes are stored as base64 TEXT in-row rather than in a Supabase Storage
-- bucket. Justification: report files are small (Excel/PDF, typically well under a few MB), the app has
-- no bucket provisioned and the PostgREST client streams base64 reliably with zero extra creds/failure
-- surface, and artifacts are short-lived (expires_at + a purge sweep can reclaim them). Simplest reliable
-- option per the directive. A retention purge (optional, operator cron):
--   DELETE FROM notify.send_artifact WHERE expires_at < now() - interval '2 days';

-- RLS open_all (backend uses the service role; matches the mig-010 notify.* convention). Idempotent.
DO $$ BEGIN
  BEGIN
    EXECUTE 'ALTER TABLE notify.send_artifact ENABLE ROW LEVEL SECURITY';
    EXECUTE 'CREATE POLICY open_all ON notify.send_artifact FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)';
  EXCEPTION WHEN OTHERS THEN NULL; END;
END $$;

-- Tenant-configurable notify settings (RULE TWO). Additive JSONB column; NULL → owner defaults
-- (download_link_expiry_days = 7). Edited on /notify (Settings tab) via PUT /api/v1/notify/settings.
ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS notify_policy JSONB;

GRANT USAGE ON SCHEMA notify TO anon, authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA notify TO anon, authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA notify TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 713 complete — notify.send_artifact + storeops.tenants.notify_policy' AS status;
