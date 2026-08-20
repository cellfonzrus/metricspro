-- 868_chat_core.sql — Internal Chat, Phase 1 (core messaging). Owner directive 2026-08-19. See
-- docs/APPROVALS_AND_CHAT_PLAN.md.
--
-- A Slack/WhatsApp-class messenger, phased to full parity. Phase 1 = channels + 1:1/group DMs +
-- messages + membership + unread/read receipts. Later phases add reactions, threads, attachments,
-- presence, search, and voice/video (columns for threads + attachments are added now, cheaply).
--
-- Storage in the storeops schema (already PostgREST-exposed, service-role-only behind FastAPI — same
-- placement as helpdesk/approvals). Realtime is driven from the backend (Supabase Realtime broadcast),
-- so no client table exposure / RLS is needed here. Org-scoped per the mig-728 tenant-scoped-FK
-- convention. Additive + idempotent.
CREATE TABLE IF NOT EXISTS storeops.chat_channels (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL,
  kind        text NOT NULL DEFAULT 'channel' CHECK (kind IN ('channel','dm','group')),
  name        text,                              -- null for DMs (derived from members client-side)
  topic       text,
  is_private  boolean NOT NULL DEFAULT false,
  dm_key      text,                              -- sorted member ids for a dm/group → dedup key
  created_by  text,                              -- employee_id
  archived    boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),   -- bumped on each new message (recency sort)
  UNIQUE (org_id, id)
);
-- A DM/group between the same set of people is opened once, not re-created.
CREATE UNIQUE INDEX IF NOT EXISTS ux_chat_channels_dmkey
  ON storeops.chat_channels (org_id, dm_key) WHERE dm_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS storeops.chat_members (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL,
  channel_id   uuid NOT NULL,
  employee_id  text NOT NULL,
  role         text NOT NULL DEFAULT 'member' CHECK (role IN ('owner','member')),
  last_read_at timestamptz,                       -- unread = messages created after this
  muted        boolean NOT NULL DEFAULT false,
  joined_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, channel_id, employee_id),
  CONSTRAINT chat_members_channel_fk
    FOREIGN KEY (org_id, channel_id) REFERENCES storeops.chat_channels (org_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_chat_members_emp ON storeops.chat_members (org_id, employee_id);

CREATE TABLE IF NOT EXISTS storeops.chat_messages (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              uuid NOT NULL,
  channel_id          uuid NOT NULL,
  sender_employee_id  text,                        -- null for system messages
  sender_name         text,
  body                text,
  kind                text NOT NULL DEFAULT 'text' CHECK (kind IN ('text','system','approval')),
  reply_to_id         uuid,                        -- threads (Phase 2)
  attachments         jsonb NOT NULL DEFAULT '[]'::jsonb,   -- (Phase 2)
  approval_request_id uuid,                        -- link to storeops.approval_requests (Phase 3)
  edited_at           timestamptz,
  deleted_at          timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, id),
  CONSTRAINT chat_messages_channel_fk
    FOREIGN KEY (org_id, channel_id) REFERENCES storeops.chat_channels (org_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_chat_messages_channel ON storeops.chat_messages (org_id, channel_id, created_at);

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 868 complete — storeops.chat_channels + chat_members + chat_messages (internal chat, Phase 1)' AS status;
