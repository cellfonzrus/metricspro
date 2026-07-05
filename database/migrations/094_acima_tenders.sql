-- 094_acima_tenders.sql
-- Make the ACIMA-lease spiff tender CONFIGURABLE. The ACIMA lease shows up in Tender Type under
-- different labels per POS/period (e.g. "Financing", "Acima Leasing", "Dish SmartPay") — the calc
-- hardcoded the substring 'acima', so it counted 0 when the label was "Financing". This lets the user
-- pick which Tender Type value(s) = an ACIMA lease (substring, case-insensitive; matches inside a
-- combined tender like "Cash; Financing"). ACIMA commission = DISTINCT such transactions × acima_spiff.
-- Empty → falls back to the historical default (substring 'acima'). Idempotent. Run in Supabase.
ALTER TABLE commcalc.flag_rules
  ADD COLUMN IF NOT EXISTS acima_tenders text[] NOT NULL DEFAULT '{}';
