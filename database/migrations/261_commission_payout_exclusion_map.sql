-- 261_commission_payout_exclusion_map.sql   (mod-commission, band 200-299)
--
-- THE PAYOUT-EXCLUSION MAP — lines that never pay, whatever a rule says.
--
-- ⚠️ MONEY-TOUCHING. READ THIS HEADER BEFORE RUNNING.
--
-- OWNER DIRECTIVE 2026-08-01, verbatim:
--   "there shgould be no paymentfor any rtr trasactions , again nothing hardocded, but with mapping,
--    map it in teh back end but let the user define going forward"
-- Evidence row: luxelink 2026-07-12, transaction 3215, "Total Wireless Protect+ RTR. Phone#: (773)
-- 648-1456." — a bill-payment / real-time-refill line that collected a commission.
--
-- WHY CODE AND NOT CONFIG (the config-first check, done first)
-- ───────────────────────────────────────────────────────────
-- There is NO existing setting that can do this. `commission_rule.qualifies=false` looks like the
-- answer and is not: THE PLAN ENGINE HAS NO EXCLUSIVITY — every rule is tested against every line
-- independently, so a "non-qualifying" rule stops ITS OWN payment and does nothing about the other
-- rules that also match the line. Excluding a class of transaction from payout ACROSS ALL RULES has
-- no representation in the schema at all. Hence this table.
--
-- WHAT IT IS
--   commcalc.payout_exclusion_map — per-tenant rows: (match_field, match_op, match_value) -> never pay.
--   Checked BEFORE any rule pays, in commission_engine.preview() via plan_pay_gate.exclusion_hit().
--
-- MATCHING IS WORD-ANCHORED BY DEFAULT, ON PURPOSE
--   'RTR' is a three-letter token. A `contains` match would bill 'CARTRIDGE' and 'PARTRIDGE' as RTR
--   transactions — the exact model-name collision class that produced the "edge" bug (a `contains
--   'edge'` rule matching "Motorola Edge 2025"). The default operator is `word`, which matches the
--   TOKEN and never a substring, and the harness carries those negative fixtures.
--
-- THE ONE SEED, AND IT IS A VALUE NOT A BRANCH
--   plan_pay_gate.DEFAULT_EXCLUSIONS carries ONE row — product_desc word 'RTR', status confirmed —
--   because the owner explicitly ordered it mapped now. It lives in exactly one named constant, it is
--   applied through the same code path as any tenant row, and a tenant row with the same
--   (field, op, value) REPLACES it — including with enabled=false, which switches it off. Nothing is
--   seeded INTO this table by this migration: an empty table still yields the RTR default.
--
-- 💰 WHAT MOVES: any line whose product description contains the WORD 'RTR' stops paying, on the next
-- Calculate, in every tenant. Measure it first — GET /commcalc/commission-plans/exclusion-impact/{period}
-- runs the real engine twice and returns per-rep before/after/delta plus every excluded line.
--
-- UNTIL THIS RUNS: load_exclusions() swallows the missing table and returns the seed alone, so the
-- RTR rule is already in force and every page renders. Running the migration is what makes the map
-- EDITABLE (add your own, or switch RTR off) — RULE TWO.
--
-- ADDITIVE + IDEMPOTENT. New table -> RLS enabled with ZERO policies and no anon/authenticated grants
-- (AGENT_CONTRACT §5); all access is through the backend service role.

CREATE TABLE IF NOT EXISTS commcalc.payout_exclusion_map (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  code         TEXT,                                  -- short stable key ('rtr'), optional
  label        TEXT,                                  -- what a human calls this class
  match_field  TEXT NOT NULL,                         -- product_desc|department|category|sku|contract_type|trans_type|tender_type
  match_op     TEXT NOT NULL DEFAULT 'word',          -- word|equals|contains|prefix|suffix
  match_value  TEXT NOT NULL,
  reason       TEXT,                                  -- shown on the drill-down next to the $0
  enabled      BOOLEAN NOT NULL DEFAULT true,
  status       TEXT NOT NULL DEFAULT 'confirmed',     -- confirmed | proposed
  source       TEXT NOT NULL DEFAULT 'tenant',        -- tenant | seed
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS payout_exclusion_map_org
  ON commcalc.payout_exclusion_map (org_id, enabled);
CREATE UNIQUE INDEX IF NOT EXISTS payout_exclusion_map_uniq
  ON commcalc.payout_exclusion_map (org_id, match_field, match_op, lower(match_value));

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'payout_exclusion_map_field_chk') THEN
    ALTER TABLE commcalc.payout_exclusion_map
      ADD CONSTRAINT payout_exclusion_map_field_chk
      CHECK (match_field IN ('product_desc', 'department', 'category', 'sku',
                             'contract_type', 'trans_type', 'tender_type'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'payout_exclusion_map_op_chk') THEN
    ALTER TABLE commcalc.payout_exclusion_map
      ADD CONSTRAINT payout_exclusion_map_op_chk
      CHECK (match_op IN ('word', 'equals', 'contains', 'prefix', 'suffix'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'payout_exclusion_map_status_chk') THEN
    ALTER TABLE commcalc.payout_exclusion_map
      ADD CONSTRAINT payout_exclusion_map_status_chk
      CHECK (status IN ('confirmed', 'proposed'));
  END IF;
END $$;

COMMENT ON TABLE commcalc.payout_exclusion_map IS
  'Per-tenant classes of sale line that never pay a commission, whatever a commission_rule says '
  '(mig 261). Checked before every rule in commission_engine.preview(). Default operator is ''word'' '
  '(token-anchored) because a substring match on a short token bills unrelated products. An empty '
  'table still yields plan_pay_gate.DEFAULT_EXCLUSIONS (the owner-ordered RTR rule); a row here with '
  'the same field/op/value replaces that seed, including with enabled=false.';

ALTER TABLE commcalc.payout_exclusion_map ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON commcalc.payout_exclusion_map FROM anon, authenticated;
GRANT ALL ON commcalc.payout_exclusion_map TO service_role;

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 261 complete — commcalc.payout_exclusion_map (empty). The owner-ordered RTR rule '
       'is a CODE default and is already in force; this table is what makes it editable and lets a '
       'tenant add their own.' AS status;
