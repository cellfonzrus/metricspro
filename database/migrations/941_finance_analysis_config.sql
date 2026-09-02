-- 941_finance_analysis_config.sql — per-org projection + valuation assumptions (finance roadmap
-- Phases 4–5; owner directive 2026-09-02: "projections … with a probable company valuation").
--
-- Two JSONB knobs on commcalc.account_config (mig 611 — the per-org finance config table; the
-- mig 933/938 column pattern). RULE TWO: every projection/valuation assumption is per-org CONFIG
-- with house defaults resolved in code (account/projection_engine.resolve_projection_config,
-- account/valuation.resolve_valuation_config) — no tenant, carrier or industry branch in code.
--
--   projection_config  JSONB — {"method": "auto"|"linear"|"seasonal_naive",
--                               "trailing_months": 6, "horizon_months": 3,
--                               "growth_rate_override": null | monthly fraction (e.g. 0.02),
--                               "expense_inflation": 0.0 (monthly fraction)}
--                       NULL/absent ⇒ house defaults (auto method, 6-month window, 3-month
--                       horizon, no overrides). DISPLAY-ONLY: projections are a labelled
--                       forward-looking VIEW (`projected: true` rows); no booked number, snapshot
--                       or payout ever reads them.
--
--   valuation_config   JSONB — {"revenue_multiple_range": [lo, hi] (× TTM revenue),
--                               "sde_multiple_range": [lo, hi] (× TTM SDE),
--                               "ebitda_multiple_range": [lo, hi] (× TTM EBITDA),
--                               "owner_addbacks_annual": 0.0 ($ added back to NI for SDE — e.g.
--                                                            owner salary already in opex),
--                               "discount_rate_range": [lo, hi] (annual, DCF),
--                               "terminal_multiple_range": [lo, hi] (× terminal-year FCF),
--                               "dcf_horizon_months": 36}
--                       NULL/absent ⇒ house defaults (published in code + surfaced verbatim in
--                       every valuation payload's assumptions block). DISPLAY-ONLY estimate —
--                       explicitly NOT an appraisal; nothing books from it.
--
-- NOT money-moving: neither knob changes a booked P&L/BS number for any org — they only shape the
-- forward-looking analysis views. Additive + idempotent. Run in the Supabase SQL editor.

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS projection_config JSONB;

ALTER TABLE commcalc.account_config
  ADD COLUMN IF NOT EXISTS valuation_config JSONB;

COMMENT ON COLUMN commcalc.account_config.projection_config IS
  'Per-org projection assumptions (method/trailing_months/horizon_months/growth_rate_override/'
  'expense_inflation) — resolved with house defaults by account/projection_engine.'
  'resolve_projection_config. Display-only: projections never feed a booked number.';
COMMENT ON COLUMN commcalc.account_config.valuation_config IS
  'Per-org valuation assumptions (multiple ranges, owner addbacks, DCF discount/terminal/horizon) '
  '— resolved with house defaults by account/valuation.resolve_valuation_config. Display-only '
  'estimate, not an appraisal; nothing books from it.';

-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- ⛔ ORG SEEDS — COMMENTED OUT (mig-622/933 precedent: per-org rows only land on an explicit
-- owner GO). Example — a wireless-retail tenant adopting the house ranges verbatim but pinning a
-- known owner-salary addback:
--
-- INSERT INTO commcalc.account_config (org_id, valuation_config)
-- VALUES ('<ORG_UUID>',
--         '{"sde_multiple_range": [2.5, 4.0], "revenue_multiple_range": [0.3, 0.6],
--           "owner_addbacks_annual": 0}'::jsonb)
-- ON CONFLICT (org_id) DO UPDATE
--   SET valuation_config = EXCLUDED.valuation_config, updated_at = now();
-- ═══════════════════════════════════════════════════════════════════════════════════════════════

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 941 — projection_config + valuation_config columns added (house defaults in code; org seeds gated)' AS status;

-- REVERT:
--   ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS projection_config;
--   ALTER TABLE commcalc.account_config DROP COLUMN IF EXISTS valuation_config;
