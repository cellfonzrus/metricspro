-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- 999 — EVENT ROI: the per-number commission basis (which per-line feed shapes an org reads)
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- OWNER DIRECTIVE 2026-09-09 (verbatim): "days share cannot be calcultaed as vag it needs to tbe
-- total of teh commisison paid on each number", then "find a connection between the ma comm, and ma
-- tx report whioch gives us the comm paid details", and on making the per-number figure the primary
-- basis: "yes".
--
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- WHAT WAS WRONG, MEASURED
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- 196 Martin Luther King Jr Dr, 2026-05-02, house org: 50 event-register rows carrying 10 distinct
-- numbers. The allocated basis reported $2,613.35 — 10/32 of the store's ENTIRE May commission of
-- $8,362.72, most of it bounty and residual on the store's pre-existing subscriber base. Traced row
-- by row, those ten numbers were paid $158.50 of payment-detail money and $45.00 of residual. The
-- allocation was ~13x too high, and wrong in the flattering direction.
--
-- The report now sums the commission ACTUALLY PAID against the numbers and IMEIs each day
-- activated. The allocation survives ONLY as a labelled fallback for lines that cannot be matched.
--
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- WHAT THIS DOES *NOT* CREATE (the CLAUDE.md duplicate-check build gate, applied and recorded)
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- Searched the index (§4, §15, §16–18, §19, §23s) and the schema for: commission received, per-line
-- commission, payment label classification, MDN/IMEI matching, chargeback, rebate exclusion.
--
--   • NO second commission-received read.  commcalc.commission_leg_label_rollup (mig 274) +
--                                          commission_received.build_breakout stay THE read. The
--                                          per-number basis routes each payment label through the
--                                          SAME per-org classifier those use —
--                                          commcalc.payment_categories, gated on category
--                                          'Commission' — so promos and reimbursements are excluded
--                                          because a CONFIG ROW says so, not a keyword list in code.
--   • NO second label→category map.        commcalc.payment_categories is untouched by this
--                                          migration. Nothing is seeded into it (see MONEY below).
--   • NO second line matcher.              The line key is commission_engine._norm_mdn, reached
--                                          through sale_installment_engine._mi_index/_match_mi —
--                                          the commission paid gate's own key, which the retention
--                                          report already reuses.
--   • NO second rebate rule.               ma_store_pnl.commission_received_lines() stays the rule;
--                                          the per-IMEI master-agent leg drops the `rebate`
--                                          component for exactly that reason (owner 2026-09-08).
--   • NO new table, NO new feed.           Every source read already exists and is already in the
--                                          data-lineage registry. Nothing new is ingested.
--
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- MONEY / SAFETY
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- Additive, idempotent, re-runnable. ONE nullable config column. NOTHING money-valued is seeded, no
-- payout is recomputed, no ledger row is written or mutated, and no existing figure changes as a
-- side effect of applying this: the column's NULL default means "probe the feeds this org actually
-- has", which is exactly what the code does today with the column absent.
--
-- The commission figure ON THE EVENT ROI SCREEN does change — that is the point of the directive —
-- but the ROI report is read-only: it books nothing to the P&L, writes no accrual and settles no
-- payout. No commission anybody is PAID moves because of this migration.
--
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- A LIVE-DATA DEFECT THIS WORK FOUND — REPORTED, NOT PAPERED OVER
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- Labels paid to the house org carry NO row in commcalc.payment_categories, so the platform's one
-- commission-received read cannot tell whether they are commission and silently drops them:
--
--     Momentum Incentive              113,052 rows, $56,526.00   (July + August 2026)
--     2026 Q3 Promo New Act Offer / 2026 Q3 Promo PIC Offer / 2026 BYOD SPIFF - Month 2
--     2026 Q1 Promo Upgrade - Quarterly True-Up   $3,379.00      (May 2026 alone)
--
-- This migration deliberately DOES NOT seed them. Deciding that "Momentum Incentive" is commission
-- is a money decision that belongs to the owner, and a migration that guessed it would move a
-- reported commission figure without anybody choosing to. The ROI report NAMES this money on every
-- affected day instead ("no category rule configured"), and one row per label on the existing
-- /commcalc/commission-legs admin surface closes it with no deploy.
--
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- REVERT
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- REVERT: ALTER TABLE core.marketing_config
-- REVERT:   DROP COLUMN IF EXISTS event_roi_commission_feed_shapes;
-- REVERT: (the report then probes every shape, which is the pre-999 behaviour — no data is lost.)
-- ═══════════════════════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ═══ 1. WHICH PER-LINE COMMISSION FEED SHAPES THIS ORG READS ══════════════════════════════════
-- RULE TWO. A feed SHAPE is a property of the report a feed arrives as — the same vocabulary
-- commcalc.processor_ledger.FEED_SHAPES already uses — never a carrier and never a tenant. The
-- house shapes are:
--
--   payment_detail_lines  commcalc.raw_payment_detail   keyed by mdn AND imei; the label is
--                                                       classified through payment_categories.
--                                                       This shape carries the CHARGEBACK leg, which
--                                                       is keyed by IMEI with no number on the row
--                                                       at all (349 rows house-scoped, 349 with an
--                                                       imei, 0 with an mdn) — a number-only join
--                                                       reports gross commission as if it were net.
--   subscriber_residual   commcalc.raw_mi               per subscriber; residual is commission
--                                                       received by construction, no label to map.
--   master_agent_lines    commcalc.raw_ma_commission    per IMEI (populated on 100% of rows for the
--                                                       tenant measured), joined to raw_sales.
--                                                       serial_1. The master-agent ORDER NUMBER is
--                                                       NOT the spine: activation_order ->
--                                                       raw_ma_daily_tx.order_number matches only
--                                                       one order family (1,735 of 4,995) and 0.0%
--                                                       of every other. The IMEI is the spine.
--
-- NULL (the default) means "read every shape that has rows", which is what the code does today.
-- An org that receives a shape it must NOT attribute from names the subset here — a config row,
-- not a deploy.
ALTER TABLE core.marketing_config
  ADD COLUMN IF NOT EXISTS event_roi_commission_feed_shapes TEXT[] DEFAULT NULL;

COMMENT ON COLUMN core.marketing_config.event_roi_commission_feed_shapes IS
  'Per-line commission feed shapes the Sales-from-Events ROI reads when summing the commission paid against the numbers/IMEIs a day activated. NULL = every shape that has rows (the adaptive default). Values: payment_detail_lines | subscriber_residual | master_agent_lines. A SHAPE, never a carrier or tenant (RULE TWO) — the same vocabulary as commcalc.processor_ledger.FEED_SHAPES.';

COMMIT;

SELECT 'mig 999 applied — event_roi_commission_feed_shapes on core.marketing_config; '
       'no money seeded, no payout recomputed, no payment_categories row written' AS status;
