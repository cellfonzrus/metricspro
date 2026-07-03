-- 084_data_source_2fa.sql — interactive login + 2FA session state for commcalc.data_source
--
-- WHY: the VidaPay / Total Access "Master Agent" portal (vidapaycrm.com) authenticates with
-- THREE fields — Account ID + User ID + Password — and then challenges with a 2-factor code the
-- operator receives out-of-band (email/SMS). mig 083 only modelled username+password with no
-- interactive step, so the VidaPay scraper had nowhere to (a) store the account id, (b) hold the
-- half-authenticated browser session while the human fetches the 2FA code, or (c) persist the
-- authenticated session so scheduled pulls reuse it until it expires.
--
-- This adds those columns. The login flow is a small state machine driven from the UI:
--   unconfigured --login/start--> needs_2fa --login/verify(code)--> authenticated
--                                    ^                                    |
--                                    +----------- (session expired) ------+
-- session_state / pending_state hold Playwright storage_state (cookies) and are BACKEND-ONLY —
-- never returned to the browser (list_data_sources strips them, exposing only has_session +
-- auth_status). Same credential posture as the rest of the app (creds live in UI config).
--
-- SAFE: additive + idempotent. Nothing existing changes; other processors ignore these columns.

ALTER TABLE commcalc.data_source
  ADD COLUMN IF NOT EXISTS account_id         TEXT,          -- 3rd login credential (VidaPay Account ID)
  ADD COLUMN IF NOT EXISTS auth_status        TEXT DEFAULT 'unconfigured',
                                                             -- unconfigured | needs_2fa | authenticated | error
  ADD COLUMN IF NOT EXISTS auth_message       TEXT,          -- human-readable last auth result (UI)
  ADD COLUMN IF NOT EXISTS two_fa_hint        TEXT,          -- e.g. 'code sent to j***@x.com' (UI)
  ADD COLUMN IF NOT EXISTS session_state      JSONB,         -- durable authenticated storage_state (cookies)
  ADD COLUMN IF NOT EXISTS session_expires_at TIMESTAMPTZ,   -- best-effort; expiry is also detected on use
  ADD COLUMN IF NOT EXISTS pending_state      JSONB,         -- transient mid-2FA storage_state
  ADD COLUMN IF NOT EXISTS pending_started_at TIMESTAMPTZ,   -- when login/start captured pending_state
  ADD COLUMN IF NOT EXISTS proxy_url          TEXT;          -- optional egress proxy (residential/allow-listed IP)

COMMENT ON COLUMN commcalc.data_source.proxy_url IS
  'Optional HTTP(S)/SOCKS proxy the login + pull route through, e.g. http://user:pass@host:port. Portals like VidaPay (Cloudflare bot-management) block datacenter IPs with a "Something doesn''t look right" wall; a residential/allow-listed proxy is the reliable fix (same WAF caveat as ePay).';

COMMENT ON COLUMN commcalc.data_source.session_state IS
  'Playwright storage_state (cookies/localStorage) for the AUTHENTICATED portal session. Backend-only; stripped from API reads. Reused by scheduled pulls until the portal invalidates it, at which point auth_status flips back to needs_2fa.';
COMMENT ON COLUMN commcalc.data_source.pending_state IS
  'Transient storage_state captured right after the password step, while the 2FA challenge is open. login/verify restores it, submits the code, and promotes it to session_state on success.';
