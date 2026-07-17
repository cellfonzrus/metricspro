-- 709_core_auth_password_policy.sql — per-tenant configurable password policy (RULE TWO: config table,
-- never hard-coded). Adds ONE additive JSONB column on storeops.tenants; NULL = use the owner-directed
-- code defaults (min_length 8, max_length 12, require upper/lower/digit/special all true). The backend
-- normalize_policy() clamps every value (min>=4, max<=128 HARD CAP) so a hostile/partial value is safe.
--
-- SAFE: additive + idempotent, no existing column altered, no data migration. Until this runs, every
-- password path uses the in-code DEFAULT_PASSWORD_POLICY (the read is try/except → default), so nothing
-- breaks and enforcement is already the owner-directed default everywhere.

alter table storeops.tenants add column if not exists password_policy jsonb;

comment on column storeops.tenants.password_policy is
  'Per-tenant password policy override (auth-hardening 2026-07-17). Shape: '
  '{min_length,max_length,require_upper,require_lower,require_digit,require_special}. '
  'NULL = code defaults (8..12, all classes). Backend clamps max_length to <=128 (hard cap).';

notify pgrst, 'reload schema';
select '709 complete — storeops.tenants.password_policy (per-tenant password policy)' as status;
