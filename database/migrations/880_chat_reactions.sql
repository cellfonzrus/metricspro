-- 880_chat_reactions.sql — Internal Chat, Phase 2 (rich messaging). Owner directive 2026-08-19. See
-- docs/APPROVALS_AND_CHAT_PLAN.md.
--
-- Emoji reactions on messages. Threaded replies (reply_to_id), attachments (attachments jsonb) and
-- edit/delete (edited_at / deleted_at) already exist on storeops.chat_messages from migration 868, so
-- Phase 2 only adds the reactions table here. Storage in the storeops schema (PostgREST-exposed,
-- service-role-only behind FastAPI). Org-scoped per the mig-728 tenant-scoped-FK convention. Additive +
-- idempotent.
CREATE TABLE IF NOT EXISTS storeops.chat_reactions (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL,
  channel_id   uuid NOT NULL,                     -- denormalized so a reaction can be scoped/broadcast
  message_id   uuid NOT NULL,
  employee_id  text NOT NULL,
  emoji        text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  -- one person can add a given emoji to a message once (toggling removes it)
  UNIQUE (org_id, message_id, employee_id, emoji),
  CONSTRAINT chat_reactions_message_fk
    FOREIGN KEY (org_id, message_id) REFERENCES storeops.chat_messages (org_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_chat_reactions_message ON storeops.chat_reactions (org_id, message_id);

-- Thread lookups (replies under a parent) + the parent-preview join in list_messages.
CREATE INDEX IF NOT EXISTS ix_chat_messages_reply ON storeops.chat_messages (org_id, reply_to_id)
  WHERE reply_to_id IS NOT NULL;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 880 complete — storeops.chat_reactions (internal chat, Phase 2 rich messaging)' AS status;
