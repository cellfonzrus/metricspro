-- 882_chat_push_tokens.sql — Internal Chat, Phase 5 (voice/video + mobile push). Owner directive
-- 2026-08-19. See docs/APPROVALS_AND_CHAT_PLAN.md.
--
-- Device push-notification tokens per employee. The server send path (app/modules/chat/push.py) fans a
-- new-message notification to a member's registered devices; it is GATED on the operator supplying FCM
-- (and later APNs) credentials — without them the send path is a documented no-op, never a fake success.
-- WebRTC call SIGNALING rides the existing Realtime broadcast channel and needs no table. Additive +
-- idempotent. Storage in the storeops schema (PostgREST-exposed, service-role-only behind FastAPI).
CREATE TABLE IF NOT EXISTS storeops.chat_push_tokens (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL,
  employee_id  text NOT NULL,
  token        text NOT NULL,                    -- FCM registration token / Web Push endpoint token
  platform     text NOT NULL DEFAULT 'web' CHECK (platform IN ('web','ios','android')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  -- a device token is unique per org (re-registering just refreshes last_seen_at)
  UNIQUE (org_id, token)
);
CREATE INDEX IF NOT EXISTS ix_chat_push_tokens_emp ON storeops.chat_push_tokens (org_id, employee_id);

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 882 complete — storeops.chat_push_tokens (internal chat, Phase 5 mobile push)' AS status;
