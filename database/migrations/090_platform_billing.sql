-- 090_platform_billing.sql
-- Platform COST connectors: the OPERATOR's own spend to run MetricsPro (vendor/infra bills).
-- Powers the super-admin "Platform Costs" panel + the derived break-even cost-per-tenant.
-- One global registry (super-admin only); credentials are stored server-side, masked in the API,
-- and never logged. Additive + idempotent.

CREATE TABLE IF NOT EXISTS storeops.platform_billing_connector (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID DEFAULT '00000000-0000-0000-0000-000000000001',
  provider          TEXT NOT NULL,               -- anthropic | railway | supabase | vercel | resend | meta | bluehost | proxy | other
  display_name      TEXT,
  credential        TEXT,                         -- vendor API token (masked in API responses; never logged)
  config            JSONB DEFAULT '{}'::jsonb,    -- provider-specific extras (org id, project ref, workspace…)
  flat_monthly_cost NUMERIC,                       -- manual figure for providers with no cost API (or override)
  is_enabled        BOOLEAN DEFAULT true,
  last_cost         NUMERIC,                       -- last synced month-to-date cost
  last_currency     TEXT DEFAULT 'USD',
  last_synced_at    TIMESTAMPTZ,
  last_status       TEXT,                          -- ok | manual | error | unconfigured
  last_detail       TEXT,
  sort_order        INT DEFAULT 0,
  notes             TEXT,
  created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- RLS open_all (backend service role is the real guard, like the rest of storeops.*)
ALTER TABLE storeops.platform_billing_connector ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS open_all ON storeops.platform_billing_connector;
CREATE POLICY open_all ON storeops.platform_billing_connector FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
GRANT ALL ON storeops.platform_billing_connector TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 090 complete — storeops.platform_billing_connector' AS status;
