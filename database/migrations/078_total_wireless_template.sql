-- 078_total_wireless_template.sql — seed Total Wireless's commission/payout TEMPLATES.
--
-- SOURCE: the dealer's Total Wireless "Commission Summary — Standard & Residual Compensation
-- Structures" exhibit (VidaPay Commission Corner; file TW_Commission_Summary[157549].pdf).
--
-- WHAT THIS SEEDS (all in the HOUSE org 00000000-…-0001, scoped to the "Total Wireless" carrier —
-- Total Wireless is a CARRIER in the house org, NOT a separate tenant; see HANDOFF part c/d):
--   1. Ensures the commcalc.carrier row  name='Total Wireless' (code 'Total') exists.
--   2. commcalc.payout_schedule (+ _line) — the multi-month "Standard Compensation" curves: each
--      activation pays a % of that month's MRC (or a flat $) spread over N months, with months 2..N
--      gated on the bill being paid that month (gate_signal='paid_residual' = subscriber Active AND
--      non-zero residual on that month's statement). This is exactly what mig 057 was built for and
--      its docstring names Total Wireless.
--
-- WHAT THIS DELIBERATELY DOES NOT SEED (documented so nothing looks "missing"):
--   • Canonical-ledger line classification — ALREADY shipped as source_report='ma_daily_tx'
--     (label "Total Wireless (MA Daily Tx)", 7 rules, mig 071). A 'total' map would duplicate it.
--     The exhibit's RESIDUAL table (Standard 3% M2-7+, plan-variant ramps, TWP/TWP+ 3%) and the
--     AUTO PAY 8.5% residual are read from Total's STATEMENT (raw_mi MI+ATU) and bucketed by that
--     map into residual_monthly / autopay_residual — the carrier already computes those dollars, so
--     we do not re-derive them from a rate here (that would double-count).
--   • product_mrc VALUES — Total's statement carries NO per-subscriber MRC and the exhibit gives only
--     percentages, no plan prices. The pct_mrc lines below therefore compute $0 until each Total plan's
--     MRC is entered in the Per-product MRC catalog (mig 074) via /commcalc/payout-schedules → "Check
--     plans" coverage helper. That is the one remaining DATA input the dealer must supply.
--   • Pure event SPIFFs (In-Store Top-Up 8.5%, TWP Migration $2, BYO Spiff, NY Protect SPF $2) — these
--     are not per-subscriber multi-month installments; they surface via the 'spiff' bucket on the
--     statement, or can be added later as commission_plan rules (mig 059).
--
-- ⚠️ ENGINE LIMITS (as of this migration — flagged, not fixed here):
--   • installment_engine caps installments at month_index ≤ min(3, num_months). Schedules below declare
--     their true num_months (up to 6) so the template is faithful, but only months 1-3 pay until that
--     cap is lifted (a 1-line backend change; see the companion note in HANDOFF).
--   • The engine resolves schedules with activation_type='*' only (per-type derivation is not wired).
--     So the '*' Standard Commission schedule is the one that computes today; the named variant curves
--     ('edge','2_month','fios_500',…) are stored, faithful, and ready for when derivation lands.
--
-- IDEMPOTENT: re-running replaces exactly the activation_types this migration manages for the Total
-- Wireless carrier (lines cascade); any hand-added Total schedule of another type is left untouched.
-- ADDITIVE + BOOST-SAFE: touches only the Total Wireless carrier's rows. Boost, rep_commissions, and
-- every other carrier are unaffected (Boost has no payout_schedule → num_months defaults to 1 = no-op).

DO $$
DECLARE
  v_org     uuid := '00000000-0000-0000-0000-000000000001';
  v_carrier uuid;
  v_sched   uuid;
BEGIN
  -- 1) ensure the Total Wireless carrier (constraint-name-agnostic: guard on existence)
  IF NOT EXISTS (SELECT 1 FROM commcalc.carrier WHERE org_id = v_org AND name = 'Total Wireless') THEN
    INSERT INTO commcalc.carrier (org_id, name, code, is_default)
      VALUES (v_org, 'Total Wireless', 'Total', false);
  END IF;
  SELECT id INTO v_carrier FROM commcalc.carrier WHERE org_id = v_org AND name = 'Total Wireless';

  -- 2) idempotency: clear the activation_types this migration owns for Total Wireless (lines cascade)
  DELETE FROM commcalc.payout_schedule
   WHERE org_id = v_org AND carrier_id = v_carrier
     AND activation_type IN ('*','edge','2_month','3_month','6_month',
                             'fios_300','fios_500','fios_1g','fios_2g',
                             'upgrade_edge','lifeline_ca','access_fee',
                             'twp_protect','twp_protect_plus');

  -- 3) STANDARD COMMISSION — New ACT (Voice, FWA, Tablet). activation_type='*' = engine-live default.
  --    M1 50% of MRC, M2-6 75% of MRC. Months 2..6 gated on the bill being paid.
  INSERT INTO commcalc.payout_schedule
      (org_id, carrier_id, company_id, activation_type, num_months, gate_signal, bypass_tier, is_active)
    VALUES (v_org, v_carrier, NULL, '*', 6, 'paid_residual', true, true) RETURNING id INTO v_sched;
  INSERT INTO commcalc.payout_schedule_line
      (org_id, schedule_id, month_index, payout_kind, mrc_pct, mrc_basis, requires_paid) VALUES
    (v_org, v_sched, 1, 'pct_mrc', 0.5000, 'commissionable_mrc', false),
    (v_org, v_sched, 2, 'pct_mrc', 0.7500, 'commissionable_mrc', true),
    (v_org, v_sched, 3, 'pct_mrc', 0.7500, 'commissionable_mrc', true),
    (v_org, v_sched, 4, 'pct_mrc', 0.7500, 'commissionable_mrc', true),
    (v_org, v_sched, 5, 'pct_mrc', 0.7500, 'commissionable_mrc', true),
    (v_org, v_sched, 6, 'pct_mrc', 0.7500, 'commissionable_mrc', true);

  -- 4) EDGE COMMISSION — New ACT (from 2026-05-01). 100/100/100/75/75.
  INSERT INTO commcalc.payout_schedule
      (org_id, carrier_id, company_id, activation_type, num_months, gate_signal, bypass_tier, is_active)
    VALUES (v_org, v_carrier, NULL, 'edge', 5, 'paid_residual', true, true) RETURNING id INTO v_sched;
  INSERT INTO commcalc.payout_schedule_line
      (org_id, schedule_id, month_index, payout_kind, mrc_pct, mrc_basis, requires_paid) VALUES
    (v_org, v_sched, 1, 'pct_mrc', 1.0000, 'commissionable_mrc', false),
    (v_org, v_sched, 2, 'pct_mrc', 1.0000, 'commissionable_mrc', true),
    (v_org, v_sched, 3, 'pct_mrc', 1.0000, 'commissionable_mrc', true),
    (v_org, v_sched, 4, 'pct_mrc', 0.7500, 'commissionable_mrc', true),
    (v_org, v_sched, 5, 'pct_mrc', 0.7500, 'commissionable_mrc', true);

  -- 5) 2 MONTHS PLAN — New ACT. 62.5/0/75/75/75/75.
  INSERT INTO commcalc.payout_schedule
      (org_id, carrier_id, company_id, activation_type, num_months, gate_signal, bypass_tier, is_active)
    VALUES (v_org, v_carrier, NULL, '2_month', 6, 'paid_residual', true, true) RETURNING id INTO v_sched;
  INSERT INTO commcalc.payout_schedule_line
      (org_id, schedule_id, month_index, payout_kind, mrc_pct, mrc_basis, requires_paid) VALUES
    (v_org, v_sched, 1, 'pct_mrc', 0.6250, 'commissionable_mrc', false),
    (v_org, v_sched, 2, 'pct_mrc', 0.0000, 'commissionable_mrc', true),
    (v_org, v_sched, 3, 'pct_mrc', 0.7500, 'commissionable_mrc', true),
    (v_org, v_sched, 4, 'pct_mrc', 0.7500, 'commissionable_mrc', true),
    (v_org, v_sched, 5, 'pct_mrc', 0.7500, 'commissionable_mrc', true),
    (v_org, v_sched, 6, 'pct_mrc', 0.7500, 'commissionable_mrc', true);

  -- 6) 3 MONTHS PLAN — New ACT. 66.67/0/0/75/75/75.
  INSERT INTO commcalc.payout_schedule
      (org_id, carrier_id, company_id, activation_type, num_months, gate_signal, bypass_tier, is_active)
    VALUES (v_org, v_carrier, NULL, '3_month', 6, 'paid_residual', true, true) RETURNING id INTO v_sched;
  INSERT INTO commcalc.payout_schedule_line
      (org_id, schedule_id, month_index, payout_kind, mrc_pct, mrc_basis, requires_paid) VALUES
    (v_org, v_sched, 1, 'pct_mrc', 0.6667, 'commissionable_mrc', false),
    (v_org, v_sched, 2, 'pct_mrc', 0.0000, 'commissionable_mrc', true),
    (v_org, v_sched, 3, 'pct_mrc', 0.0000, 'commissionable_mrc', true),
    (v_org, v_sched, 4, 'pct_mrc', 0.7500, 'commissionable_mrc', true),
    (v_org, v_sched, 5, 'pct_mrc', 0.7500, 'commissionable_mrc', true),
    (v_org, v_sched, 6, 'pct_mrc', 0.7500, 'commissionable_mrc', true);

  -- 7) 6 MONTHS PLAN — New ACT. 33.34/0/0/37.5/0.
  INSERT INTO commcalc.payout_schedule
      (org_id, carrier_id, company_id, activation_type, num_months, gate_signal, bypass_tier, is_active)
    VALUES (v_org, v_carrier, NULL, '6_month', 5, 'paid_residual', true, true) RETURNING id INTO v_sched;
  INSERT INTO commcalc.payout_schedule_line
      (org_id, schedule_id, month_index, payout_kind, mrc_pct, mrc_basis, requires_paid) VALUES
    (v_org, v_sched, 1, 'pct_mrc', 0.3334, 'commissionable_mrc', false),
    (v_org, v_sched, 2, 'pct_mrc', 0.0000, 'commissionable_mrc', true),
    (v_org, v_sched, 3, 'pct_mrc', 0.0000, 'commissionable_mrc', true),
    (v_org, v_sched, 4, 'pct_mrc', 0.3750, 'commissionable_mrc', true),
    (v_org, v_sched, 5, 'pct_mrc', 0.0000, 'commissionable_mrc', true);

  -- 8) FIOS 300 Mbps — New ACT. 80 x6.
  INSERT INTO commcalc.payout_schedule
      (org_id, carrier_id, company_id, activation_type, num_months, gate_signal, bypass_tier, is_active)
    VALUES (v_org, v_carrier, NULL, 'fios_300', 6, 'paid_residual', true, true) RETURNING id INTO v_sched;
  INSERT INTO commcalc.payout_schedule_line
      (org_id, schedule_id, month_index, payout_kind, mrc_pct, mrc_basis, requires_paid) VALUES
    (v_org, v_sched, 1, 'pct_mrc', 0.8000, 'commissionable_mrc', false),
    (v_org, v_sched, 2, 'pct_mrc', 0.8000, 'commissionable_mrc', true),
    (v_org, v_sched, 3, 'pct_mrc', 0.8000, 'commissionable_mrc', true),
    (v_org, v_sched, 4, 'pct_mrc', 0.8000, 'commissionable_mrc', true),
    (v_org, v_sched, 5, 'pct_mrc', 0.8000, 'commissionable_mrc', true),
    (v_org, v_sched, 6, 'pct_mrc', 0.8000, 'commissionable_mrc', true);

  -- 9) FIOS 500 Mbps — New ACT. 80/80/80/35/35/35.
  INSERT INTO commcalc.payout_schedule
      (org_id, carrier_id, company_id, activation_type, num_months, gate_signal, bypass_tier, is_active)
    VALUES (v_org, v_carrier, NULL, 'fios_500', 6, 'paid_residual', true, true) RETURNING id INTO v_sched;
  INSERT INTO commcalc.payout_schedule_line
      (org_id, schedule_id, month_index, payout_kind, mrc_pct, mrc_basis, requires_paid) VALUES
    (v_org, v_sched, 1, 'pct_mrc', 0.8000, 'commissionable_mrc', false),
    (v_org, v_sched, 2, 'pct_mrc', 0.8000, 'commissionable_mrc', true),
    (v_org, v_sched, 3, 'pct_mrc', 0.8000, 'commissionable_mrc', true),
    (v_org, v_sched, 4, 'pct_mrc', 0.3500, 'commissionable_mrc', true),
    (v_org, v_sched, 5, 'pct_mrc', 0.3500, 'commissionable_mrc', true),
    (v_org, v_sched, 6, 'pct_mrc', 0.3500, 'commissionable_mrc', true);

  -- 10) FIOS 1 Gig — New ACT. 80/80/80/40/40/40.
  INSERT INTO commcalc.payout_schedule
      (org_id, carrier_id, company_id, activation_type, num_months, gate_signal, bypass_tier, is_active)
    VALUES (v_org, v_carrier, NULL, 'fios_1g', 6, 'paid_residual', true, true) RETURNING id INTO v_sched;
  INSERT INTO commcalc.payout_schedule_line
      (org_id, schedule_id, month_index, payout_kind, mrc_pct, mrc_basis, requires_paid) VALUES
    (v_org, v_sched, 1, 'pct_mrc', 0.8000, 'commissionable_mrc', false),
    (v_org, v_sched, 2, 'pct_mrc', 0.8000, 'commissionable_mrc', true),
    (v_org, v_sched, 3, 'pct_mrc', 0.8000, 'commissionable_mrc', true),
    (v_org, v_sched, 4, 'pct_mrc', 0.4000, 'commissionable_mrc', true),
    (v_org, v_sched, 5, 'pct_mrc', 0.4000, 'commissionable_mrc', true),
    (v_org, v_sched, 6, 'pct_mrc', 0.4000, 'commissionable_mrc', true);

  -- 11) FIOS 2 Gig — New ACT. 80/80/80/40.
  INSERT INTO commcalc.payout_schedule
      (org_id, carrier_id, company_id, activation_type, num_months, gate_signal, bypass_tier, is_active)
    VALUES (v_org, v_carrier, NULL, 'fios_2g', 4, 'paid_residual', true, true) RETURNING id INTO v_sched;
  INSERT INTO commcalc.payout_schedule_line
      (org_id, schedule_id, month_index, payout_kind, mrc_pct, mrc_basis, requires_paid) VALUES
    (v_org, v_sched, 1, 'pct_mrc', 0.8000, 'commissionable_mrc', false),
    (v_org, v_sched, 2, 'pct_mrc', 0.8000, 'commissionable_mrc', true),
    (v_org, v_sched, 3, 'pct_mrc', 0.8000, 'commissionable_mrc', true),
    (v_org, v_sched, 4, 'pct_mrc', 0.4000, 'commissionable_mrc', true);

  -- 12) UPGRADE (Edge Finance) — flat $25 at activation.
  INSERT INTO commcalc.payout_schedule
      (org_id, carrier_id, company_id, activation_type, num_months, gate_signal, bypass_tier, is_active)
    VALUES (v_org, v_carrier, NULL, 'upgrade_edge', 1, 'paid_residual', true, true) RETURNING id INTO v_sched;
  INSERT INTO commcalc.payout_schedule_line
      (org_id, schedule_id, month_index, payout_kind, flat_amount, requires_paid) VALUES
    (v_org, v_sched, 1, 'flat', 25.00, false);

  -- 13) LIFELINE+ (California only) — flat $35 M1, $40 M2.
  INSERT INTO commcalc.payout_schedule
      (org_id, carrier_id, company_id, activation_type, num_months, gate_signal, bypass_tier, is_active)
    VALUES (v_org, v_carrier, NULL, 'lifeline_ca', 2, 'paid_residual', true, true) RETURNING id INTO v_sched;
  INSERT INTO commcalc.payout_schedule_line
      (org_id, schedule_id, month_index, payout_kind, flat_amount, requires_paid) VALUES
    (v_org, v_sched, 1, 'flat', 35.00, false),
    (v_org, v_sched, 2, 'flat', 40.00, true);

  -- 14) ACCESS FEE — 50% of MRC at activation.
  INSERT INTO commcalc.payout_schedule
      (org_id, carrier_id, company_id, activation_type, num_months, gate_signal, bypass_tier, is_active)
    VALUES (v_org, v_carrier, NULL, 'access_fee', 1, 'paid_residual', true, true) RETURNING id INTO v_sched;
  INSERT INTO commcalc.payout_schedule_line
      (org_id, schedule_id, month_index, payout_kind, mrc_pct, mrc_basis, requires_paid) VALUES
    (v_org, v_sched, 1, 'pct_mrc', 0.5000, 'commissionable_mrc', false);

  -- 15) TOTAL WIRELESS PROTECT (TWP) — flat $3 M1, $3 M2.
  INSERT INTO commcalc.payout_schedule
      (org_id, carrier_id, company_id, activation_type, num_months, gate_signal, bypass_tier, is_active)
    VALUES (v_org, v_carrier, NULL, 'twp_protect', 2, 'paid_residual', true, true) RETURNING id INTO v_sched;
  INSERT INTO commcalc.payout_schedule_line
      (org_id, schedule_id, month_index, payout_kind, flat_amount, requires_paid) VALUES
    (v_org, v_sched, 1, 'flat', 3.00, false),
    (v_org, v_sched, 2, 'flat', 3.00, true);

  -- 16) TOTAL WIRELESS PROTECT+ (TWP+) — flat $7 M1, $7 M2, $10 M3.
  INSERT INTO commcalc.payout_schedule
      (org_id, carrier_id, company_id, activation_type, num_months, gate_signal, bypass_tier, is_active)
    VALUES (v_org, v_carrier, NULL, 'twp_protect_plus', 3, 'paid_residual', true, true) RETURNING id INTO v_sched;
  INSERT INTO commcalc.payout_schedule_line
      (org_id, schedule_id, month_index, payout_kind, flat_amount, requires_paid) VALUES
    (v_org, v_sched, 1, 'flat', 7.00, false),
    (v_org, v_sched, 2, 'flat', 7.00, true),
    (v_org, v_sched, 3, 'flat', 10.00, true);

  RAISE NOTICE 'Migration 078: Total Wireless carrier % — seeded % payout schedules',
    v_carrier,
    (SELECT count(*) FROM commcalc.payout_schedule WHERE org_id = v_org AND carrier_id = v_carrier);
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 078 complete — Total Wireless payout templates seeded ('
       || (SELECT count(*) FROM commcalc.payout_schedule ps
             JOIN commcalc.carrier c ON c.id = ps.carrier_id
            WHERE c.name = 'Total Wireless') || ' schedules, '
       || (SELECT count(*) FROM commcalc.payout_schedule_line l
             JOIN commcalc.payout_schedule ps ON ps.id = l.schedule_id
             JOIN commcalc.carrier c ON c.id = ps.carrier_id
            WHERE c.name = 'Total Wireless') || ' month-lines)' AS status;
