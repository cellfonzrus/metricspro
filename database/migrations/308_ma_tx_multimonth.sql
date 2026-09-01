-- 308_ma_tx_multimonth.sql
-- mod-commission · band 200–299 spill → 308 (follows 307). Additive + idempotent + safe to re-run.
--
-- WHAT IT IS FOR (owner spec 2026-09-01, "MA TX → multi-month payout", Phase A). For Total (plan-mode)
-- tenants the VidaPay MA Daily Tx export (commcalc.raw_ma_daily_tx, mig 083) joins the multi-month
-- payout formula:
--   • The M1 activation is the row whose order_type matches the configured activation order type
--     (default 'Activation Order') — and THAT row's retail_cost IS the subscriber's MRC.
--   • Months 2..16 are dictated by the "MONTH n" wording in product_name (e.g. 'TBV MONTH 2 New
--     Activation Commission', 'TBV MONTH 5 New Activation SPF') — parsed by the SAME
--     commission_ledger.parse_payment_month the Commission Ledger already uses, never a second regex.
-- raw_ma_daily_tx carries NO imei/mdn, so a B2B sale reaches its MA TX rows through a TWO-HOP join:
-- raw_sales.serial_1 ↔ raw_ma_commission.imei|sim (digit-normalized) → raw_ma_commission
-- .activation_order ↔ raw_ma_daily_tx.order_number. Engine: sale_installment_engine (gate_source
-- 'ma_tx'; MRC basis 'ma_tx_activation').
--
-- CONFIG, NEVER CODE (RULE TWO): the activation order-type string, the month horizon and the gate
-- knobs all live in config tables — nothing tenant/carrier-specific is in a branch.
--
-- 💰 MONEY POSTURE: running this migration changes NO behaviour on its own. gate_source stays
-- 'ma_commission' for plan-mode tenants until an operator sets a config row to 'ma_tx', and
-- installment_mrc_basis stays 'plan_line' until set to 'ma_tx_activation'. The widened CHECKs only
-- ALLOW months 13..16 to be configured; no schedule gains months by itself. merchant_invoice (an
-- invoice NUMBER stored as NUMERIC — see residual_subs._MA_IDENTIFIER_COLUMNS) is never read as money
-- by any of this: the only MA TX money column the engine reads is retail_cost.
--
-- REVERT:
--   ALTER TABLE commcalc.plan_installment_schedule DROP CONSTRAINT IF EXISTS plan_installment_schedule_num_months_check;
--   ALTER TABLE commcalc.ma_commission_month_rate  DROP CONSTRAINT IF EXISTS ma_commission_month_rate_month_index_check;
--   ALTER TABLE commcalc.ma_commission_month_rate  ADD CONSTRAINT ma_commission_month_rate_month_index_check CHECK (month_index BETWEEN 1 AND 6);
--   ALTER TABLE commcalc.installment_gate_source_config DROP COLUMN IF EXISTS ma_tx_activation_order_type;
--   ALTER TABLE commcalc.sale_installment_ledger DROP COLUMN IF EXISTS order_number, DROP COLUMN IF EXISTS account_id;
--   (Rows written meanwhile with num_months/month_index 13..16 must be trimmed before re-adding the 1..12/1..6 checks.)

-- ── a. plan_installment_schedule.num_months → ceiling 16 ─────────────────────────────────────────
-- Mig 201 declared NO CHECK on num_months (the "1..12" lived only in a comment + the engine's
-- min(12, …) clamp, itself widened to 16 in sale_installment_engine.MAX_SCHEDULE_MONTHS this change).
-- Drop-by-name is still issued first so a re-run — or an environment where the auto-named constraint
-- was added by hand — stays idempotent, then the ceiling is made EXPLICIT at 16.
ALTER TABLE commcalc.plan_installment_schedule
  DROP CONSTRAINT IF EXISTS plan_installment_schedule_num_months_check;
ALTER TABLE commcalc.plan_installment_schedule
  ADD CONSTRAINT plan_installment_schedule_num_months_check
  CHECK (num_months BETWEEN 1 AND 16);

COMMENT ON CONSTRAINT plan_installment_schedule_num_months_check ON commcalc.plan_installment_schedule IS
  'Multi-month horizon ceiling: 16 (was a comment-only 1..12 in mig 201; widened by mig 308 for the '
  'MA TX MONTH 2..16 payout wording). The engine clamp is sale_installment_engine.MAX_SCHEDULE_MONTHS.';

-- ── b. ma_commission_month_rate.month_index → ceiling 16 ─────────────────────────────────────────
-- Mig 268/268b declared the CHECK inline (auto-named <table>_month_index_check by Postgres).
-- Widened 1..6 → 1..16 so per-month expected rates can be configured for the MA TX months 7..16.
-- Existing rows (1..6) all satisfy the new check; nothing is rewritten.
ALTER TABLE commcalc.ma_commission_month_rate
  DROP CONSTRAINT IF EXISTS ma_commission_month_rate_month_index_check;
ALTER TABLE commcalc.ma_commission_month_rate
  ADD CONSTRAINT ma_commission_month_rate_month_index_check
  CHECK (month_index BETWEEN 1 AND 16);

-- ── c. installment_gate_source_config — the 'ma_tx' gate source + its ONE new config string ──────
-- gate_source is a free TEXT column (mig 223 declared no CHECK on it — verified), so the new value
-- 'ma_tx' needs no constraint change; it is documented on the column instead. The M1 activation
-- order-type string is CONFIG (the whole point: 'Activation Order' is VidaPay's wording, another
-- processor's export may differ) — column, not code constant.
ALTER TABLE commcalc.installment_gate_source_config
  ADD COLUMN IF NOT EXISTS ma_tx_activation_order_type TEXT NOT NULL DEFAULT 'Activation Order';

COMMENT ON COLUMN commcalc.installment_gate_source_config.gate_source IS
  'Which statement table proves "dealer paid this month" for the sale-installment gate: ''boost_mi'' '
  '(raw_mi MI+ATU residual), ''ma_commission'' (raw_ma_commission per-month spiffs), or ''ma_tx'' '
  '(mig 308: the UNION of the ma_commission spiff evidence for months <= 6 AND raw_ma_daily_tx rows '
  'whose product_name carries the "MONTH n" wording, joined to the sale through raw_ma_commission '
  'serial->activation_order->order_number; month 1 also counts the linked activation-order row '
  'itself). ma_max_month caps the gated horizon in every MA mode — a Total org row can set 16.';

COMMENT ON COLUMN commcalc.installment_gate_source_config.ma_tx_activation_order_type IS
  'gate_source=''ma_tx'' only: the raw_ma_daily_tx.order_type value that marks the M1 ACTIVATION row '
  '(matched case-insensitively, trimmed). That row''s retail_cost IS the subscriber''s MRC when '
  'commission_org_config.installment_mrc_basis=''ma_tx_activation''. Config, not code: another '
  'processor''s export can spell it differently without a deploy. Default ''Activation Order'' '
  '(VidaPay). Trade-off: a single string per (org,carrier) row — a feed mixing several activation '
  'order-type spellings in one carrier needs per-carrier rows, not a list (deliberately kept a '
  'scalar so the resolver''s merge semantics stay byte-identical for existing rows).';

-- ── d. sale_installment_ledger — MA TX provenance columns ────────────────────────────────────────
-- Written ADAPTIVELY by sale_installment_engine._persist (exactly like the mig-258 expected_amount
-- tier): a backend deployed before this migration keeps writing the narrower column set, a backend
-- deployed after it degrades gracefully when the migration has not run. Only rows whose MA TX
-- linkage actually resolved carry values; every other row stays NULL (no backfill).
ALTER TABLE commcalc.sale_installment_ledger
  ADD COLUMN IF NOT EXISTS order_number TEXT,
  ADD COLUMN IF NOT EXISTS account_id   TEXT;

-- ── e. why these columns / the trade-offs ────────────────────────────────────────────────────────
COMMENT ON COLUMN commcalc.sale_installment_ledger.order_number IS
  'MA TX provenance (mig 308): the raw_ma_daily_tx.order_number this chain''s MA TX evidence/MRC came '
  'from, via the two-hop serial->raw_ma_commission.activation_order join. IDENTIFIER, never money '
  '(order_number is in residual_subs._MA_IDENTIFIER_COLUMNS). NOT unique in the feed — one order has '
  'an activation row plus its MONTH-n rows plus adjustments — so this is an audit pointer, not a join '
  'key for aggregation. NULL = the row''s evidence did not come from MA TX (Boost rows, ma_commission '
  'rows, unlinked chains): absence is honest, never guessed.';
COMMENT ON COLUMN commcalc.sale_installment_ledger.account_id IS
  'MA TX provenance (mig 308): the processor store account (raw_ma_daily_tx.account_id) of the linked '
  'activation row — which VidaPay account the payout chain is anchored to (store attribution recon). '
  'TEXT identifier; same NULL semantics as order_number.';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 308 complete — MA TX multi-month: checks widened to 16, ma_tx gate config + ledger provenance columns added' AS status;
