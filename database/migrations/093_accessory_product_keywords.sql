-- 093_accessory_product_keywords.sql
-- Third way to classify an accessory, for POS feeds that carry NO Department/Category (e.g. the B2B
-- daily "Sales Transaction Details" feed — every row's Department is blank). Match on the PRODUCT
-- description: a non-phone line is an accessory if its product_desc contains any of these keywords
-- (case-insensitive substring), e.g. {case, screen, protector, charger, cable, mount, holder, earbud}.
-- Combined with 092's department/category lists (a line is an accessory if ANY of the three match).
-- Empty → no effect. Idempotent. Run in the Supabase SQL editor.
ALTER TABLE commcalc.flag_rules
  ADD COLUMN IF NOT EXISTS accessory_product_keywords text[] NOT NULL DEFAULT '{}';
