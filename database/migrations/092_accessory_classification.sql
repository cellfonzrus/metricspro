-- 092_accessory_classification.sql
-- Make "what counts as an accessory sale" CONFIGURABLE instead of the hardcoded department='Ondigo'.
-- Two lists on the existing per-org flag_rules row: which POS DEPARTMENTS and/or CATEGORIES are
-- accessories. A sale line is an accessory if its department is in accessory_departments OR its
-- category is in accessory_categories (case-insensitive). Empty/unset → falls back to ['Ondigo'] in
-- code, so behavior is unchanged until the user configures it on the Sales Report → Accessory settings.
-- Idempotent. Run in the Supabase SQL editor.
ALTER TABLE commcalc.flag_rules
  ADD COLUMN IF NOT EXISTS accessory_departments text[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS accessory_categories  text[] NOT NULL DEFAULT '{}';
