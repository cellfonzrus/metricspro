-- 621_finance_owner_rulings_k1_k3.sql — config for owner rulings K2 (payroll authority) + K3 (Option-C
-- device COGS). Companion to docs/finance/STATEMENT_FORMULA_BOOK.md §K.
--
-- ⚠️⚠️ MONEY-TOUCHING — **DO NOT APPLY WITHOUT OWNER GO.** ⚠️⚠️
-- The DDL below is inert (three additive columns, all defaulting to "no change"). The **seed block at
-- the bottom is what moves reported numbers**, and it is deliberately commented out. Per
-- AGENT_CONTRACT §5, a migration that alters a reported figure is parked, not run, even though
-- mod-finance is authorised to run its own band.
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────────
-- K2 · PAYROLL AUTHORITY. `coa.py` books wages from `store_expenses.source_key='payroll_gross'` and,
-- when that token is absent, falls back to a StoreOps shifts×rate ESTIMATE. luxelink keys its payroll
-- BY HAND (all 304 July rows carry source_key NULL), so it got BOTH: a $145,358.27 estimate AND
-- $108,430.59 of manual salary rows inside store_opex. Measured proof they are the same dollars — the
-- shifts×rate figure is penny-identical to the manual "Employee Salaries" row for 8 of 20 stores.
-- The guard keyed on a PRODUCER TOKEN and could never notice a hand-entered salary. Owner ruling K2:
-- the manual rows are authoritative; suppress the estimate.
--
-- RULE TWO (SAP-configurable): the fix must not hard-code an expense name or a tenant. `coa` therefore
-- reads a per-org LIST of expense_names that ARE payroll. **Code default = EMPTY**, so every org with
-- no seeded value is byte-identical to today. The list is the tenant's own vocabulary, picked from the
-- expense names already present in its data (RULE THREE, pick-don't-type).
--
-- K3 · OPTION-C DEVICE COGS. Device/handset cost is on NO P&L line: luxelink July `device_cost` =
-- $234.00 against a MEASURED $142,033.93 of devices actually sold. `device_payable_ledger` carries
-- 1,192 rows with `owed` NULL on every one (`owed_source='unconfigured'`). Policy of record (owner
-- 2026-07-30, docs/designs/device-cost-ledger.md §9) specifies invoice-first COGS with a sale-time
-- fallback; the P&L flip was HELD as "Option C". Owner ruling K3 releases it.
--
-- The POS fallback must stay a FALLBACK: on luxelink's own handset lines POS `ext − gp` =
-- 23,289.18 − 25,625.51 = **NEGATIVE $2,336.33**, because B2B Soft records the post-subsidy cost.
-- Invoice-first is not a preference here, it is the only correct basis.
--
-- ── WHAT ───────────────────────────────────────────────────────────────────────────────────────────
--   account_config.payroll_expense_names   TEXT[]  — these expense_names ARE payroll ⇒ suppress the
--                                                    shifts×rate estimate. Empty = today's behaviour.
--   account_config.payroll_expense_routes  JSONB   — OPTIONAL per-name P&L line override
--                                                    ('wages' | 'payroll_expenses'). Absent for a name
--                                                    = leave it where it already books (store_opex),
--                                                    which is what ruling K2's target figures show.
--   account_config.device_cogs_mode        TEXT    — 'off' (default, byte-identical) | 'auto' (Option C:
--                                                    invoice-first, POS fallback) | 'invoice' (invoice
--                                                    only, no POS fallback) | 'pos' (legacy POS only).
--
-- ── BOOST / EVERY-TENANT SAFETY ────────────────────────────────────────────────────────────────────
-- All three defaults mean "behave exactly as today". `device_cogs_mode='off'` is the whole reason the
-- house org stays byte-identical: its VIP consignment ledger WOULD produce a COGS figure under 'auto',
-- and that is a separate money change the owner has not ruled on. Enabling Boost needs its own GO.
--
-- ⚠️ The house org carries the SAME manual-salary pattern (`Employee Salaries` $75,718 ·
-- `Owner / Mgmt Salaries` $72,500 · `Dm Salary` $42,500 — note the different spelling, itself a
-- RULE THREE argument). It is intentionally NOT seeded below: ruling K2 was made about luxelink, and
-- suppressing Boost's estimate would move Boost's reported P&L without a ruling.

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS payroll_expense_names  TEXT[] NOT NULL DEFAULT '{}';

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS payroll_expense_routes JSONB  NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS device_cogs_mode       TEXT   NOT NULL DEFAULT 'off'
    CHECK (device_cogs_mode IN ('off', 'auto', 'invoice', 'pos'));

COMMENT ON COLUMN commcalc.account_config.payroll_expense_names IS
  'store_expenses.expense_name values that ARE employer payroll for this tenant (e.g. "Employee '
  'Salaries"). Presence of any such row makes payroll AUTHORITATIVE and SUPPRESSES the StoreOps '
  'shifts x rate wages estimate, exactly as source_key=''payroll_gross'' does. Matched '
  'case-insensitively on the trimmed name. Empty = byte-identical to pre-621 behaviour. '
  'Owner ruling K2, 2026-08-10.';

COMMENT ON COLUMN commcalc.account_config.payroll_expense_routes IS
  'OPTIONAL {expense_name: line_key} override for names in payroll_expense_names; line_key is '
  '''wages'' or ''payroll_expenses''. A name absent from this map keeps its existing route '
  '(store_opex for a hand-entered row), which is the presentation ruling K2''s target figures use: '
  'wages 0.00, store_opex unchanged, net income unchanged either way. Display only - it moves a '
  'dollar between two OPEX lines and never changes net income.';

COMMENT ON COLUMN commcalc.account_config.device_cogs_mode IS
  'Device/handset COGS recognition (owner policy 2026-07-30 sec.9 + ruling K3 2026-08-10). '
  '''off'' = pre-621 behaviour, POS ext-gp only (DEFAULT, byte-identical). '
  '''auto'' = Option C: invoice-first from the distributor ledger, POS ext-gp only for devices no '
  'invoice covers. ''invoice'' = invoice only, never POS (POS cost is NEGATIVE on subsidised handsets). '
  '''pos'' = explicit legacy. Carrier-agnostic: the invoice source is chosen by which data EXISTS for '
  'the org (raw_ma_* for VidaPay, asset_ledger/vip_invoices for VIP), NEVER by tenant name.';

-- ═══════════════════════════════════════════════════════════════════════════════════════════════════
-- ⛔ SEED BLOCK — MONEY-TOUCHING. COMMENTED OUT. Owner Gate 2 uncomments and runs this.
--
-- Effect on luxelink (854f6d7b-6590-4e4d-88ab-646f560d4f4c) July 2026, MEASURED, after a recompute:
--   wages         145,358.27 -> 0.00           (estimate suppressed; K2)
--   store_opex    225,080.58 -> 225,080.58     (unchanged - keeps the authoritative $108,430.59)
--   device_cost      234.00 -> 137,185.10      (invoice-first, IMEI-DEDUPED; K3. The $234.00 of POS
--                                               SIM-kit cost is DISPLACED, not added to - when an
--                                               invoice source answers, the POS basis is not booked.)
--   device_rebate      0.00 -> (126,636.77)    (contra-COGS; K1, code-side, no seed needed)
--   vip_reimb    126,636.77 -> 0.00            (K1 moves it out of income)
--   NET INCOME  (172,411.98) -> (164,004.81)   delta +8,407.17    <- mig 621 ALONE (this file)
--                             -> (140,715.63)  delta +31,696.35   <- 621 AND 622 together
--
-- 📌 CORRECTED 2026-08-11 at Gate-1 review. This block previously read device_cost -> 142,267.93 and
-- NET INCOME -> (145,798.46) / delta +26,613.52. Those figures are SUPERSEDED and were wrong twice:
-- they counted all 787 activation rows instead of the 746 distinct IMEIs that policy S9 C1 requires
-- (-$4,848.83), and they added the displaced $234.00 POS cost on top of the invoice figure. They also
-- silently assumed mig 622 was applied. The numbers above are the ones in formula book SM, measured
-- read-only against prod. READ THE 621-ALONE LINE unless you are running 622 in the same session.
--
-- INSERT INTO commcalc.account_config (org_id, payroll_expense_names, payroll_expense_routes, device_cogs_mode)
-- VALUES ('854f6d7b-6590-4e4d-88ab-646f560d4f4c',
--         ARRAY['Employee Salaries','DM Salaries','Owner / Mgmt Salaries'],
--         '{}'::jsonb,
--         'invoice')
-- Names VERIFIED against luxelink's own July rows at Gate-1 (2026-08-11): 'Employee Salaries'
-- $72,930.58 + 'DM Salaries' $25,500.01 + 'Owner / Mgmt Salaries' $10,000.00 = $108,430.59, which is
-- ruling K2's figure to the cent. A mistyped name would match NOTHING and silently change nothing.
--
-- MODE = 'invoice', OWNER RULING 2026-08-11 (was 'auto' as first drafted). 'auto' falls back to the
-- POS when no distributor invoice covers a period, and POS device cost on a subsidised handset is
-- NEGATIVE. For July the two are identical (the MA source answers, so the POS is never reached), but
-- the owner then ruled ALL HISTORY is to be recomputed - which sweeps Feb-May, where there is no
-- `raw_ma_commission` at all. 'invoice' is the mode that honours ruling K3(b): those months report a
-- LABELLED honest zero instead of a negative cost the moment their sales are backloaded.
-- ON CONFLICT (org_id) DO UPDATE
--   SET payroll_expense_names  = EXCLUDED.payroll_expense_names,
--       payroll_expense_routes = EXCLUDED.payroll_expense_routes,
--       device_cogs_mode       = EXCLUDED.device_cogs_mode,
--       updated_at             = now();
-- ═══════════════════════════════════════════════════════════════════════════════════════════════════

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 621 complete — account_config gained payroll_expense_names / payroll_expense_routes / '
       'device_cogs_mode. ALL DEFAULTS = no change; the money seed is commented out pending owner GO.' AS status;
