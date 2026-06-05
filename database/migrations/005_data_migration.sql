-- ============================================================
-- DATA MIGRATION SCRIPT
-- Migrates existing data from old public.commcalc_* tables
-- into new commcalc.* and storeops.* schemas
--
-- Run AFTER migrations 001, 002, 003
-- Safe to run multiple times (ON CONFLICT DO NOTHING)
-- ============================================================

-- Set the org_id for Cellular Services
DO $$ DECLARE org UUID := '00000000-0000-0000-0000-000000000001'; BEGIN

-- ── CommCalc tables ───────────────────────────────────────────

-- Raw sales
INSERT INTO commcalc.raw_sales
  SELECT gen_random_uuid(), org, period, period_month, period_year,
         store, salesperson, user_login, department, category,
         product_desc, product_id, gp, ext_price, trans_id,
         trans_date::date, contract_type, mdn, serial_1,
         register, tender_type, voided, trans_type, sku, NOW()
  FROM public.commcalc_raw_sales
ON CONFLICT DO NOTHING;

-- Raw payment detail
INSERT INTO commcalc.raw_payment_detail
  SELECT gen_random_uuid(), org, period, period_month, period_year,
         business_address, payment_type, amount,
         mdn, imei, payment_date::date, rep_username, NULL, NOW()
  FROM public.commcalc_raw_payment_detail
ON CONFLICT DO NOTHING;

-- Raw MI
INSERT INTO commcalc.raw_mi
  SELECT gen_random_uuid(), org, period, period_month, period_year,
         salesforce_id, actual_mi_payout, actual_atu_payout,
         phone_number, subscriber_status, NOW()
  FROM public.commcalc_raw_mi
ON CONFLICT DO NOTHING;

-- Raw DLAR rep
INSERT INTO commcalc.raw_dlar_rep
  SELECT gen_random_uuid(), org, period, period_month, period_year,
         rep_name, store, atu_pct, protect_pct, byod_pct,
         family_plan_pct, tmr3, aal_conversion,
         NULL, NULL, ga_prepaid, NOW()
  FROM public.commcalc_raw_dlar_rep
ON CONFLICT DO NOTHING;

-- Raw DLAR store
INSERT INTO commcalc.raw_dlar_store
  SELECT gen_random_uuid(), org, period, period_month, period_year,
         salesforce_id, address, NULL, NULL, NULL, NOW()
  FROM public.commcalc_raw_dlar_store
ON CONFLICT DO NOTHING;

-- Product catalog
INSERT INTO commcalc.raw_catalog
  SELECT gen_random_uuid(), org, product_id, product_desc, cost, sku, NOW()
  FROM public.commcalc_raw_catalog
ON CONFLICT DO NOTHING;

-- Payment categories
INSERT INTO commcalc.payment_categories (id, org_id, description, category, is_active)
  SELECT gen_random_uuid(), org, description, category, is_active
  FROM public.commcalc_payment_categories
ON CONFLICT (org_id, description) DO NOTHING;

-- Store mapping
INSERT INTO commcalc.store_mapping
  (id, org_id, store_code, store_address, market, salesforce_id, is_active, created_at)
  SELECT gen_random_uuid(), org, store_code, store_address,
         COALESCE(market, 'Boost'), salesforce_id, is_active, NOW()
  FROM public.commcalc_store_mapping
ON CONFLICT (org_id, store_code) DO NOTHING;

-- Payout config
INSERT INTO commcalc.payout_config
  (id, org_id, period, upgrade_flat, premium_flat, byod_flat, byod_extra_spiff,
   trade_in_spiff, acima_spiff, acc_rate, setup_fee_rate,
   kpi_atu_target, kpi_protect_target, kpi_boostapp_target, kpi_familyplan_target,
   kpi_byod_target, kpi_tmr3_target, kpi_aal_target,
   tier_100_min_kpis, tier_75_min_kpis, tier_75_pct, tier_50_pct,
   straight_line, acc_target_enabled, acc_target_pct, custom_spiffs, updated_at, created_at)
  SELECT gen_random_uuid(), org, period,
         COALESCE(upgrade_flat, 20), COALESCE(premium_flat, 5),
         COALESCE(byod_flat, 3), COALESCE(byod_extra_spiff, 0),
         COALESCE(trade_in_spiff, 20), COALESCE(acima_spiff, 25),
         COALESCE(acc_rate, 0.10), COALESCE(setup_fee_rate, 0.10),
         COALESCE(kpi_atu_target, 55), COALESCE(kpi_protect_target, 80),
         COALESCE(kpi_boostapp_target, 65), COALESCE(kpi_familyplan_target, 45),
         COALESCE(kpi_byod_target, 35), COALESCE(kpi_tmr3_target, 70),
         COALESCE(kpi_aal_target, 5),
         COALESCE(tier_100_min_kpis, 7), COALESCE(tier_75_min_kpis, 5),
         COALESCE(tier_75_pct, 0.75), COALESCE(tier_50_pct, 0.50),
         COALESCE(straight_line, false), COALESCE(acc_target_enabled, false),
         COALESCE(acc_target_pct, 0.10), COALESCE(custom_spiffs, '[]'::jsonb),
         NOW(), NOW()
  FROM public.commcalc_payout_config
ON CONFLICT (org_id, period) DO NOTHING;

-- Rep commissions
INSERT INTO commcalc.rep_commissions
  (id, org_id, period, period_month, period_year,
   epay_salesperson, storeops_name, store,
   tier, kpis_met, total_kpis,
   premium_acts, byod_acts, upgrade_acts,
   acc_comm, setup_fee_comm, trade_in_comm, acima_comm,
   subtotal, total_payout,
   boost_commission, boost_reimbursement, created_at)
  SELECT gen_random_uuid(), org, period, period_month, period_year,
         epay_salesperson, storeops_name, store,
         COALESCE(tier, 0.5), COALESCE(kpis_met, 0), COALESCE(total_kpis, 7),
         COALESCE(premium_acts, 0), COALESCE(byod_acts, 0), COALESCE(upgrade_acts, 0),
         COALESCE(acc_comm, 0), COALESCE(setup_fee_comm, 0), COALESCE(trade_in_comm, 0),
         COALESCE(acima_comm, 0),
         COALESCE(subtotal, 0), COALESCE(total_payout, 0),
         boost_commission, boost_reimbursement, NOW()
  FROM public.commcalc_rep_commissions
ON CONFLICT DO NOTHING;

-- Flags
INSERT INTO commcalc.flags
  (id, org_id, period, period_month, period_year,
   flag_type, source, severity,
   store_address, epay_salesperson, mdn, imei, amount,
   description, coaching_note, created_at)
  SELECT gen_random_uuid(), org, period, period_month, period_year,
         flag_type, source, severity,
         store_address, epay_salesperson, mdn, imei, amount,
         description, coaching_note, NOW()
  FROM public.commcalc_flags
ON CONFLICT DO NOTHING;

-- Store expenses
INSERT INTO commcalc.store_expenses
  (id, org_id, period, store_code, expense_name, expense_type, amount, created_at)
  SELECT gen_random_uuid(), org, period, store_code, expense_name, expense_type,
         COALESCE(amount, 0), NOW()
  FROM public.commcalc_store_expenses
ON CONFLICT DO NOTHING;

-- Name map
INSERT INTO commcalc.name_map
  (id, org_id, epay_login, epay_salesperson, storeops_name, confirmed)
  SELECT gen_random_uuid(), org, epay_login, epay_salesperson, storeops_name,
         COALESCE(confirmed, false)
  FROM public.commcalc_name_map
ON CONFLICT (org_id, epay_login) DO NOTHING;

-- ── StoreOps tables ───────────────────────────────────────────

-- Stores
INSERT INTO storeops.stores
  (id, org_id, store_code, address, market, monthly_target, is_active, created_at)
  SELECT id, org, store_code, address, market,
         COALESCE(monthly_target, 0), COALESCE(is_active, true), NOW()
  FROM public.stores
ON CONFLICT DO NOTHING;

-- Employees (preserving epay_login and epay_salesperson)
INSERT INTO storeops.employees
  (id, org_id, employee_id, name, home_store, role, pay_rate,
   is_active, epay_login, epay_salesperson, email, phone, created_at)
  SELECT id, org, employee_id, name, home_store, role,
         COALESCE(pay_rate, 0), COALESCE(is_active, true),
         epay_login, epay_salesperson, email, phone, NOW()
  FROM public.employees
ON CONFLICT DO NOTHING;

-- Shifts (active + soft-deleted, preserve history)
INSERT INTO storeops.shifts
  (id, org_id, employee_id, employee_name, store_code,
   shift_date, start_time, end_time, scheduled_hours, actual_hours,
   status, notes, is_deleted, deleted_at, created_at)
  SELECT id, org, employee_id::text, employee_name, store_code,
         shift_date, start_time, end_time,
         COALESCE(scheduled_hours, 0), COALESCE(actual_hours, 0),
         COALESCE(status, 'scheduled'), notes,
         COALESCE(is_deleted, false), deleted_at, NOW()
  FROM public.shifts
ON CONFLICT DO NOTHING;

-- Also archive any soft-deleted shifts
INSERT INTO storeops.shifts_archive
  SELECT id, org, employee_id::text, employee_name, store_code,
         shift_date, start_time, end_time,
         COALESCE(scheduled_hours, 0), COALESCE(actual_hours, 0),
         NULL, NULL, COALESCE(status, 'deleted'), notes,
         COALESCE(is_deleted, true), deleted_at, NULL,
         NOW(), 'migrated_from_public'
  FROM public.shifts
  WHERE is_deleted = true
ON CONFLICT DO NOTHING;

-- Time off requests
INSERT INTO storeops.time_off_requests
  (id, org_id, employee_id, start_date, end_date, type, status, notes, created_at)
  SELECT id, org, employee_id::text, start_date, end_date,
         COALESCE(type, 'PTO'), COALESCE(status, 'pending'), notes, NOW()
  FROM public.time_off_requests
ON CONFLICT DO NOTHING;

RAISE NOTICE 'Migration complete. All data moved to new schemas.';

END $$;

-- Verify counts
SELECT 'commcalc.raw_sales' as tbl, COUNT(*) FROM commcalc.raw_sales
UNION ALL SELECT 'commcalc.rep_commissions', COUNT(*) FROM commcalc.rep_commissions
UNION ALL SELECT 'commcalc.store_mapping', COUNT(*) FROM commcalc.store_mapping
UNION ALL SELECT 'commcalc.flags', COUNT(*) FROM commcalc.flags
UNION ALL SELECT 'storeops.stores', COUNT(*) FROM storeops.stores
UNION ALL SELECT 'storeops.employees', COUNT(*) FROM storeops.employees
UNION ALL SELECT 'storeops.shifts', COUNT(*) FROM storeops.shifts
ORDER BY tbl;
