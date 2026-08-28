-- 881_chat_search.sql — Internal Chat, Phase 4 (search + org management). Owner directive 2026-08-19.
-- See docs/APPROVALS_AND_CHAT_PLAN.md.
--
-- Full-text search over message bodies. A STORED generated tsvector column + GIN index gives Postgres
-- to_tsvector search; the API also runs a portable ILIKE substring match (accelerated for operators who
-- add the pg_trgm index below). Additive + idempotent. Storage stays in the storeops schema
-- (PostgREST-exposed, service-role-only behind FastAPI).
ALTER TABLE storeops.chat_messages
  ADD COLUMN IF NOT EXISTS search_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('english', coalesce(body, ''))) STORED;

CREATE INDEX IF NOT EXISTS ix_chat_messages_search ON storeops.chat_messages USING gin (search_tsv);

-- Optional substring acceleration for the ILIKE path — pg_trgm ships with Supabase. Guarded so a role
-- without CREATE EXTENSION still applies the rest of the migration cleanly.
DO $$
BEGIN
  CREATE EXTENSION IF NOT EXISTS pg_trgm;
  CREATE INDEX IF NOT EXISTS ix_chat_messages_body_trgm
    ON storeops.chat_messages USING gin (body gin_trgm_ops);
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pg_trgm unavailable — ILIKE search still works, just unindexed (%).', SQLERRM;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 881 complete — chat message full-text search (internal chat, Phase 4)' AS status;
