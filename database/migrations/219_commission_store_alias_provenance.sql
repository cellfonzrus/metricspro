-- 219_commission_store_alias_provenance.sql — audit trail for the Store-Matching explicit mappings.
--
-- The Store-Matching UI lets a tenant admin map a POS/sales-store string → a canonical store (the explicit
-- commcalc.store_aliases mapping — the resolver's source of truth). These two ADDITIVE columns record HOW
-- each mapping was created so the house/tenant can audit that its store map covers every POS string:
--   source     — 'manual' | 'suggested' | 'fallback-confirmed'  (how the row was written)
--   confidence — the smart-suggestion confidence at confirm time ('exact'|'high'|'medium'|'low'), or NULL
--
-- ADDITIVE + IDEMPOTENT + GRACEFUL: the UI works WITHOUT this migration (source/confidence just render
-- blank; the POST omits the columns until they exist — see router.add_store_alias). Nothing money-touching:
-- these columns are never read by calculator.py / commission_engine.py or any payout path.
--
-- Run in the Supabase SQL editor (Claude cannot run SQL). Safe to re-run.

ALTER TABLE commcalc.store_aliases ADD COLUMN IF NOT EXISTS source     TEXT;
ALTER TABLE commcalc.store_aliases ADD COLUMN IF NOT EXISTS confidence TEXT;

NOTIFY pgrst, 'reload schema';
SELECT '219 complete — commcalc.store_aliases.source/confidence available (Store-Matching audit trail)' AS status;
