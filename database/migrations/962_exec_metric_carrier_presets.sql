-- 962_exec_metric_carrier_presets.sql — Executive-MTD "Bill Payment Qty" reads 0 on one tenant and
-- correct on the other (owner 2026-09-04: "executive mtd in cellfonz r us does not have bill payment
-- qty, but luxelink has it, fix it")
--
-- ── ROOT CAUSE (live evidence, both orgs read side by side 2026-09-04) ─────────────────────────────
-- The `bill_payment` bucket matches sale lines by EXACT department/category membership against tokens
-- in commcalc.exec_metric_config (mig 204). Both orgs carry the SAME tokens:
--
--     department ['rtr']  ·  category ['rtr product', 'other carr. payments']
--
-- router.py's own comment says where those came from: "Defaults DERIVED from the real luxelink
-- Total-Wireless Sales-Transaction-Details export." They are ONE tenant's vocabulary.
--
--   • LuxeLink's export spells it that way            → 1,731 lines / $73,914.79 matched (Aug 2026) ✓
--   • CellfonzRUs's export spells the SAME concept as
--     department 'bill payments', category 'boost rtr' / 'xfinity refill'
--                                                     →     2 lines /     $74.77 matched (Aug 2026) ✗
--
-- against 6,869 real bill-payment lines worth $359,873.05 in that same month. The column was not
-- empty because data was missing; it was empty because the definition described another tenant's POS.
-- No error was ever raised — the report simply reported zero. This is the LI/1115-Liberty defect class
-- (owner: "fix as a design not a band aid as this could happen to a new store also").
--
-- ── WHY THE OBVIOUS TOKEN IS WRONG (and what this seeds instead) ───────────────────────────────────
-- The tempting fix is the substring token 'boost rtr' — the codebase already uses it as
-- _BILLPAY_DEFAULT_TOKENS for the Daily-Targets conversion display. It OVER-MATCHES: 1,339 August
-- lines are PROTECTION plans whose description ends "... $8 included in your boost rtr payment"
-- (department 'miscellaneous', category 'service', $0.00). Counting them would inflate Bill Payment
-- Qty by ~19% and corrupt the conv ratio (activations ÷ bill payments), which is why this seeds an
-- EXACT department match instead:
--
--     department ['bill payments']  ·  exclude_category ['other charge']
--
-- `other charge` inside that department is the ePay SERVICE CHARGE line (4,303 lines / $17,088.00 in
-- August) — the fee the store charges to take a payment, not a payment. Including it would roughly
-- double the count. Result on August 2026: 6,869 lines · $359,873.05 · 6,868 distinct transactions.
--
-- ── WHAT SHIPS (additive; the systemic half is in the same PR) ─────────────────────────────────────
--   1. commcalc.exec_metric_config.carrier (nullable) — the mig-945/953 preset pattern applied to
--      metric DEFINITIONS. carrier IS NULL = that org's own definition (every pre-962 row, unchanged);
--      carrier NOT NULL at the HOUSE org = that carrier's PRESET. Resolution is
--      `tenant row > house carrier preset > built-in default`, reusing report_labels' carrier identity
--      primitives (app/modules/commcalc/exec_metric_defs.py; proof harness_exec_metric_defs.py).
--      LAZY auto-assign, exactly like mig 945: a NEW tenant that picks a carrier at setup inherits the
--      preset the moment the resolver runs — no setup hook, and an org with no carrier / no preset
--      falls through to the built-in defaults, byte-identical to today.
--   2. The boost preset row for `bill_payment` (so the NEXT Boost tenant is correct on day one).
--   3. The house org's OWN bill_payment row corrected to its actual vocabulary.
--
-- Also shipping in the PR, not here: the silent-zero PRECAUTION. Exec MTD now returns
-- `metric_coverage` — any line bucket matching ZERO rows over a period that HAS rows is reported with
-- the department/category values that did occur. A definition that describes nobody's data becomes
-- visible instead of printing a quiet 0.
--
-- ── MONEY ─────────────────────────────────────────────────────────────────────────────────────────
-- No P&L line, payout, accrual or commission figure reads this bucket. It feeds Executive MTD's
-- display columns, /metric-recon's SECONDARY comparison basis, and Leg B of the mig-944 3-way
-- bill-pay recon — all reconciliation surfaces. The mig-939 P&L bill-pay carve-out books from the
-- PROCESSOR feed (_billpay_processor_by_store_day), not from this basis, and is untouched.
-- Those recon screens currently compare against ~0 for the house org, so every variance they show for
-- CellfonzRUs today is an artifact of this defect; after this seed they compare against the real
-- figure. That is a correction of a broken comparison, not a re-pricing — but it DOES change what
-- those screens display, so it is called out here rather than applied quietly.
--
-- LuxeLink is unaffected: verified by replaying both rule sets over its live August rows —
-- 1,731 lines / $73,914.79 / 1,234 distinct txns before and after.
--
-- Idempotent + additive. Run in the Supabase SQL editor.

-- 1. the preset layer -----------------------------------------------------------------------------
ALTER TABLE commcalc.exec_metric_config
  ADD COLUMN IF NOT EXISTS carrier TEXT;
COMMENT ON COLUMN commcalc.exec_metric_config.carrier IS
  'NULL = this org''s OWN metric definition (every pre-962 row). NOT NULL at the house org '
  '(00000000-0000-0000-0000-000000000001) = that carrier''s PRESET, inherited by any tenant whose '
  'commcalc.carrier resolves to the same code. Precedence: tenant row > carrier preset > built-in '
  'default (exec_metric_defs.resolve; the mig 945/953 label-preset pattern). A tenant can never '
  'publish a preset: rows with a carrier are only honored at the house org.';

-- Uniqueness must now be per (org, bucket, carrier) so a house PRESET can coexist with the house
-- org's OWN row for the same bucket. Pre-962 uniqueness was (org_id, bucket); NULLS NOT DISTINCT
-- keeps at most one own-row per bucket, exactly as before.
DO $$
DECLARE c record;
BEGIN
  FOR c IN
    SELECT con.conname
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_namespace ns ON ns.oid = rel.relnamespace
    WHERE ns.nspname = 'commcalc' AND rel.relname = 'exec_metric_config'
      AND con.contype IN ('u', 'p')
      -- att.attname is `name`, so it must be cast to text before comparing with a text[] literal:
      -- `name[] = text[]` has no operator and raises 42883 (owner hit this running the file, 2026-09-05).
      AND (SELECT array_agg(att.attname::text ORDER BY att.attname::text)
           FROM unnest(con.conkey) k
           JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k)
          = ARRAY['bucket', 'org_id']::text[]
  LOOP
    EXECUTE format('ALTER TABLE commcalc.exec_metric_config DROP CONSTRAINT %I', c.conname);
  END LOOP;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS exec_metric_config_org_bucket_carrier_uk
  ON commcalc.exec_metric_config (org_id, bucket, carrier) NULLS NOT DISTINCT;

-- 2. the boost preset — so the NEXT Boost tenant is right on day one, with no setup step ------------
INSERT INTO commcalc.exec_metric_config (org_id, bucket, rules, basis, carrier)
VALUES ('00000000-0000-0000-0000-000000000001', 'bill_payment',
        '{"department": ["bill payments"], "exclude_category": ["other charge"]}'::jsonb,
        'count', 'boost')
ON CONFLICT (org_id, bucket, carrier) DO UPDATE
  SET rules = EXCLUDED.rules, basis = EXCLUDED.basis, updated_at = now();

-- 3. the house org's OWN definition, corrected to its real vocabulary ------------------------------
--    (an own-row exists today carrying the other tenant's tokens; it wins over the preset, so it has
--     to be corrected rather than merely shadowed.)
INSERT INTO commcalc.exec_metric_config (org_id, bucket, rules, basis, carrier)
VALUES ('00000000-0000-0000-0000-000000000001', 'bill_payment',
        '{"department": ["bill payments"], "exclude_category": ["other charge"]}'::jsonb,
        'count', NULL)
ON CONFLICT (org_id, bucket, carrier) DO UPDATE
  SET rules = EXCLUDED.rules, basis = EXCLUDED.basis, updated_at = now();

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 962 — exec-metric carrier presets + CellfonzRUs bill-payment vocabulary corrected' AS status;

-- Verify (August 2026 house org should go from 2 lines / $74.77 to 6,869 lines / $359,873.05):
--   GET /api/v1/commcalc/exec-mtd/August%202026  → by_location.total.bill_payment_qty / .amount
--   and `metric_coverage.gaps` should be empty for that period.
--
-- REVERT:
--   UPDATE commcalc.exec_metric_config
--      SET rules = '{"department": ["rtr"], "category": ["rtr product", "other carr. payments"]}'::jsonb
--    WHERE org_id = '00000000-0000-0000-0000-000000000001' AND bucket = 'bill_payment' AND carrier IS NULL;
--   DELETE FROM commcalc.exec_metric_config
--    WHERE org_id = '00000000-0000-0000-0000-000000000001' AND bucket = 'bill_payment' AND carrier = 'boost';
--   DROP INDEX IF EXISTS commcalc.exec_metric_config_org_bucket_carrier_uk;
--   ALTER TABLE commcalc.exec_metric_config DROP COLUMN IF EXISTS carrier;
--   (Reads are adaptive: exec_metric_defs falls back to the legacy select when `carrier` is absent,
--    so a pre-962 database resolves org-own rows then code defaults — byte-identical to pre-962.)
