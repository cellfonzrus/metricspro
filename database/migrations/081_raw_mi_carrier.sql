-- 081_raw_mi_carrier.sql — carrier attribution on raw_mi (Total Wireless engine wiring)
--
-- WHY: the payout schedules seeded by mig 078 are SCOPED to the Total Wireless carrier
-- (d9c215ea-62fb-4ff4-aed3-b78d430e5060), but raw_mi rows carry no carrier_id, so the installment
-- engine's _resolve_schedule skips every carrier-scoped schedule (anchor_row.carrier_id is always
-- None → `cr and cr != carrier_id` is true). This adds the nullable column; the config-driven
-- ingest endpoints (/upload-mapped and /commission-import/commit) stamp it when the caller selects
-- a carrier, and installment_engine reads it with no code change (select * + .get('carrier_id')).
--
-- SAFE: additive + idempotent (IF NOT EXISTS). Boost rows — ePay sweep and the legacy manual
-- /upload/mi_report path — are never stamped, stay NULL, and keep matching only NULL-carrier
-- schedules exactly as today. No existing pay behavior changes on recompute.

ALTER TABLE commcalc.raw_mi ADD COLUMN IF NOT EXISTS carrier_id UUID;

COMMENT ON COLUMN commcalc.raw_mi.carrier_id IS
  'Which carrier this statement row came from (NULL = legacy/Boost ePay). Stamped at ingestion by the config-driven uploads when a carrier is selected; read by installment_engine._resolve_schedule to match carrier-scoped payout schedules (e.g. Total Wireless, mig 078).';
