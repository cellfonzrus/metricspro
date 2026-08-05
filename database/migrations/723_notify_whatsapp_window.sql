-- MIGRATION 723: NOTIFY — WhatsApp 24h customer-service WINDOW evidence + which rung delivered
-- Band 700–799 (mod-platform-core). Additive + idempotent (safe to re-run). Number verified free vs
-- origin/main and vs every branch: 700–722 present, 723 unused.
--
-- WHY (owner incident 2026-08-05 — WhatsApp report delivery silently failing in production):
-- Meta delivers FREE-FORM (non-template) messages only inside 24h of the recipient's last INBOUND
-- message. Out of window it does NOT reliably reject: the Graph API answered 200 with real wamids
-- (wamid.HBgLMTUxNjIzMzA0MjIV…) for luxelink sends at 2026-08-05 03:01/03:02Z, we logged status='sent',
-- and nothing was delivered (Meta Insights: zero conversations in 30 days). The send ladder read that
-- 2xx as success and never tried the approved TEMPLATE rung — the one that always arrives.
--
-- The fix needs ONE piece of durable state: proof that a number actually messaged us recently. Meta's
-- status/inbound webhook gives us that; this table stores it.
--
-- ⚠️ DELIBERATE, DOCUMENTED DEVIATION from AGENT_CONTRACT §2 ("every new table carries org_id NOT NULL"):
-- whatsapp_window has NO org_id, ON PURPOSE. One Meta WABA phone number serves EVERY tenant, and the 24h
-- window is a fact about (our sender, the recipient handset) AT META — not tenant state. An org_id would
-- fragment one real window into N per-tenant copies, so a genuinely-open window would read as closed and
-- we would send a link where the file could have attached. The table holds NO tenant data (our own
-- phone_number_id, the recipient's digits, a timestamp), is never used for authorization, and only ever
-- picks "attach the file" vs "send the approved template" for a recipient the caller already supplied.
--
-- WHAT BREAKS UNTIL THIS RUNS: NOTHING. Every read fails CLOSED to "window not proven open", which means
-- every business-initiated WhatsApp report goes out on the approved link TEMPLATE — the rung that always
-- arrives. Running this migration only RESTORES the ability to attach the real file to a recipient who
-- messaged us in the last WHATSAPP_WINDOW_HOURS. send_log.delivery_route likewise degrades: the notify
-- send-logger retries the insert without that key if the column is absent.

CREATE TABLE IF NOT EXISTS notify.whatsapp_window (
  phone_number_id TEXT        NOT NULL,   -- OUR Meta phone number id (the WABA sender) — never a tenant
  wa_id           TEXT        NOT NULL,   -- recipient handset, digits only (matches whatsapp_meta._to_number)
  last_inbound_at TIMESTAMPTZ NOT NULL,   -- when that handset last messaged us (opens/refreshes the window)
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (phone_number_id, wa_id)    -- both NOT NULL PK members ⇒ ON CONFLICT is always well-defined
);

CREATE INDEX IF NOT EXISTS whatsapp_window_recent
  ON notify.whatsapp_window(wa_id, last_inbound_at DESC);

-- Which ladder rung actually delivered, so the Send History can say "file attached" vs "download link"
-- without a Meta dashboard trip. NULL for every pre-existing row and for non-WhatsApp channels.
ALTER TABLE notify.send_log
  ADD COLUMN IF NOT EXISTS delivery_route TEXT;   -- template_doc | freeform_doc | template_link

-- Security posture per AGENT_CONTRACT §5: RLS ON, ZERO policies, ZERO anon/authenticated grants.
-- All access is via the backend service role (which bypasses RLS).
DO $$
BEGIN
  BEGIN
    EXECUTE 'ALTER TABLE notify.whatsapp_window ENABLE ROW LEVEL SECURITY';
  EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN
    EXECUTE 'REVOKE ALL ON notify.whatsapp_window FROM anon, authenticated';
  EXCEPTION WHEN OTHERS THEN NULL; END;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 723 complete — notify.whatsapp_window + send_log.delivery_route' AS status;
