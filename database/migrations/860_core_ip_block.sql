-- 860 — IP blocklist for incident containment (External Threat Defense Plan §1.1 / §4.2 Phase 2).
--
-- Lets a super-admin instantly block a malicious source IP during an incident WITHOUT a redeploy —
-- the request middleware refuses blocked IPs with 403. Backs the IRP "block malicious source IPs
-- instantly" containment step and the access-log "block this scraper" action. Optional expiry so a
-- temporary block auto-lifts; NULL = permanent until removed.
--
-- NOTE: this is application-layer defense-in-depth. The authoritative volumetric/edge blocking still
-- belongs at a managed WAF/CDN (Cloudflare/AWS WAF) per the plan — see docs/INCIDENT_RESPONSE_PLAN.md.

CREATE TABLE IF NOT EXISTS core.ip_block (
  ip          TEXT PRIMARY KEY,                      -- exact client IP (first x-forwarded-for hop)
  reason      TEXT,
  created_by  TEXT,                                  -- super-admin auth_id / email who added it
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ                            -- NULL = permanent; else auto-expires
);
CREATE INDEX IF NOT EXISTS ip_block_active_idx ON core.ip_block(expires_at);

ALTER TABLE core.ip_block ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON core.ip_block FROM anon, authenticated;
GRANT ALL ON core.ip_block TO service_role;
