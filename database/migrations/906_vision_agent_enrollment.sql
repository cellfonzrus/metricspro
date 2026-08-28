-- 906 · Vision edge analyzer ENROLLMENT
--
-- Why this exists.
--
-- Registering an analyzer minted a long-lived HMAC signing secret and showed it on screen, with
-- "copy this now, it cannot be shown again". That put a permanent credential into a human's hands,
-- their clipboard, and whatever they pasted it into next. It went exactly where you would expect:
-- into a chat window, in the first week.
--
-- The secret was never the thing the operator needed. What they needed was to AUTHORISE ONE MACHINE,
-- once. So they now carry an ENROLLMENT CODE instead:
--
--   * it is useless after one use,
--   * it expires in 30 minutes,
--   * it authorises nothing by itself — it can only be traded for a secret, once,
--   * and the secret it mints is generated ON the analyzer's first call and never rendered in any
--     UI, never returned to a browser, and never present in an admin page at all.
--
-- A leaked enrollment code half an hour later is a non-event. A leaked signing secret is an attacker
-- posting fabricated customer counts into a company's analytics until someone notices.
--
-- Safe to re-run.

ALTER TABLE core.vision_edge_agent
  ADD COLUMN IF NOT EXISTS enroll_code_hash  TEXT,          -- sha256 of the code; the code is never stored
  ADD COLUMN IF NOT EXISTS enroll_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS enrolled_at       TIMESTAMPTZ;   -- when the machine claimed its secret

-- An agent now exists BEFORE it has a secret: the row is created at registration, the secret arrives
-- when the machine enrolls. A NULL secret must therefore be representable — and _authenticate_agent
-- denies on one, so a registered-but-unenrolled agent can authenticate nothing.
ALTER TABLE core.vision_edge_agent ALTER COLUMN secret_enc DROP NOT NULL;

-- Enrollment looks an agent up BY the hash of the presented code, so this is the lookup path for an
-- unauthenticated public endpoint. Partial: only pending rows are ever searched.
CREATE INDEX IF NOT EXISTS idx_vision_edge_agent_enroll
  ON core.vision_edge_agent (enroll_code_hash)
  WHERE enroll_code_hash IS NOT NULL;

COMMENT ON COLUMN core.vision_edge_agent.enroll_code_hash IS
  'sha256 of a single-use enrollment code. Cleared the moment it is redeemed.';
COMMENT ON COLUMN core.vision_edge_agent.secret_enc IS
  'enc:v1: HMAC signing secret. NULL until the analyzer enrolls. Never returned to a browser.';
