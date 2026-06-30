-- 072_boost_commission_template.sql — seed the 'boost' rule-set for the canonical commission ledger.
--
-- WHY: 071 shipped the SAP-style commission_ledger + commission_category_map and seeded the 'ma_daily_tx'
-- (Total/MA) rule-set. This seeds the SECOND built-in template — 'boost' — so a Boost ePay/DLAR Commission
-- Payment Detail file normalises into the SAME five canonical buckets (Commission / Spiff / Equipment rebate
-- / Residual-monthly / Auto Pay residual) instead of Boost's own columns. Derived directly from the curated
-- "Commission Categories Master File" (Description -> Category) the dealer maintains.
--
-- KEY DIFFERENCE FROM ma_daily_tx: Boost commission AMOUNTS ARE POSITIVE (the dealer is paid a positive
-- number), whereas the MA convention is negative=payout. So every Boost rule uses sign_rule='any' — a
-- positive Boost line still books into its payout bucket. The Boost line-type lives in the "Payment Type"
-- column (map it to product_name in the import wizard), e.g. "Boost Ready Bounty - Month 2", "Boost Auto
-- Top-Up". Each Month 1-6 installment keeps its payment_month (parsed from "… Month N").
--
-- MAPPING (faithful to the master's 3 categories, refined onto the 5 canonical buckets):
--   Re-imbursement                    -> equipment_rebate   (device discounts, promos, BOGO/GOGO, SIM reimb)
--   MDF                               -> commission
--   Commission, "… Auto Top-Up"       -> autopay_residual
--   Commission, "… SPIFF/Spiff …"     -> spiff
--   Commission (everything else)      -> commission         (bounties, activation, withholding, etc.)
-- Exact (equals) rules reproduce the curated master 1:1 (the labels don't carry consistent keywords, so
-- contains-only would misfile, e.g. "ePay RTR Invoice Reimbursement" is master-Commission). A few low-
-- priority (>=1000) contains fallbacks catch future quarter/month variants; genuinely unseen labels still
-- surface as 'other' (never silently dropped).
--
-- ADDITIVE + IDEMPOTENT + BOOST-SAFE: inserts rows into the EXISTING 071 commission_category_map only
-- (source_report='boost'). The live Boost/Total calc, rep_commissions, carrier_commission, and every legacy
-- upload branch are untouched. Re-running is a no-op (ON CONFLICT DO NOTHING on the unique key).

INSERT INTO commcalc.commission_category_map
  (org_id, source_report, match_field, match_op, pattern, category, sign_rule, priority, is_seeded)
VALUES
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','$10 Network Change SPIFF','spiff','any',10,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','$20 Network Change SPIFF','spiff','any',11,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2024 Q3 Exclusive Upgrade Offer','equipment_rebate','any',12,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2024 Q3 Promo New Act Boost 5G','equipment_rebate','any',13,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2024 Q3 Promo New Act Offer','equipment_rebate','any',14,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2024 Q3 Promo PIC Boost 5G','equipment_rebate','any',15,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2024 Q3 Promo PIC Offer','equipment_rebate','any',16,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2024 Q3 Promo Upgrade','equipment_rebate','any',17,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2024 Q3 Promo Upgrade Boost 5G','equipment_rebate','any',18,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2024 Q4 Exclusive Upgrade Offer','equipment_rebate','any',19,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2024 Q4 Promo New Act Boost 5G','equipment_rebate','any',20,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2024 Q4 Promo New Act Offer','equipment_rebate','any',21,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2024 Q4 Promo PIC Boost 5G','equipment_rebate','any',22,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2024 Q4 Promo PIC Offer','equipment_rebate','any',23,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2024 Q4 Promo Upgrade','equipment_rebate','any',24,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2024 Q4 Promo Upgrade Boost 5G','equipment_rebate','any',25,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2024 SIM Card Reimbursement','equipment_rebate','any',26,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q1 Exclusive Upgrade Offer','equipment_rebate','any',27,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q1 Promo New Act Boost 5G','equipment_rebate','any',28,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q1 Promo New Act Offer','equipment_rebate','any',29,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q1 Promo PIC Boost 5G','equipment_rebate','any',30,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q1 Promo PIC Offer','equipment_rebate','any',31,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q1 Promo Retail Postpaid Offer','equipment_rebate','any',32,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q1 Promo Upgrade','equipment_rebate','any',33,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q1 Promo Upgrade Boost 5G','equipment_rebate','any',34,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q2 Exclusive Upgrade Offer','equipment_rebate','any',35,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q2 Promo New Act Boost 5G','equipment_rebate','any',36,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q2 Promo New Act Offer','equipment_rebate','any',37,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q2 Promo PIC Boost 5G','equipment_rebate','any',38,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q2 Promo PIC Offer','equipment_rebate','any',39,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q2 Promo Retail Postpaid Offer','equipment_rebate','any',40,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q2 Promo Upgrade','equipment_rebate','any',41,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q3 Exclusive Upgrade Offer','equipment_rebate','any',42,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q3 Promo New Act Boost 5G','equipment_rebate','any',43,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q3 Promo New Act Offer','equipment_rebate','any',44,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q3 Promo PIC Boost 5G','equipment_rebate','any',45,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q3 Promo PIC Offer','equipment_rebate','any',46,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q3 Promo Retail Postpaid Offer','equipment_rebate','any',47,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q3 Promo Upgrade','equipment_rebate','any',48,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q4 Promo New Act Offer','equipment_rebate','any',49,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q4 Promo PIC Offer','equipment_rebate','any',50,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q4 Promo Retail Postpaid Offer','equipment_rebate','any',51,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 Q4 Promo Upgrade','equipment_rebate','any',52,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','2025 SIM Card Reimbursement','equipment_rebate','any',53,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','4.0 $1K Ramp Up Bonus','commission','any',54,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Boost 5G Certified Device Bounty - Month 3','commission','any',55,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Boost 5G Certified Device Bounty - Month 4','commission','any',56,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Boost 5G Certified Device Bounty - Month 5','commission','any',57,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Boost 5G Certified Device Bounty - Month 6','commission','any',58,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Boost 5G Network Bounty - Month 4','commission','any',59,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Boost 5G Network Bounty - Month 5','commission','any',60,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Boost 5G Network Migration Bounty - Month 1','commission','any',61,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Boost 5G Network Migration Bounty - Month 2','commission','any',62,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Boost 5G Network Migration Bounty - Month 3','commission','any',63,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Boost 5G Network Migration Bounty - Month 4','commission','any',64,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Boost 5G Network Migration Bounty - Month 5','commission','any',65,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Boost 5G Network Migration Bounty - Month 6','commission','any',66,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Boost Auto Top-Up','autopay_residual','any',67,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Boost Plus Remodel DevFi SPIFF','spiff','any',68,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Boost Ready Bounty - Month 1','commission','any',69,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Commission Withholding','commission','any',70,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Device Financing Bounty - Month 1','commission','any',71,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Device Financing Bounty - Month 2','commission','any',72,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Device Financing Bounty - Month 3','commission','any',73,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Device Financing Bounty - Month 4','commission','any',74,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Device Financing Bounty - Month 5','commission','any',75,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Device Financing Bounty - Month 6','commission','any',76,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Device Upgrade Bounty - Month 1','commission','any',77,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Device Upgrade Bounty - Month 2','commission','any',78,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Device Upgrade Bounty - Month 3','commission','any',79,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Device Upgrade Bounty - Month 4','commission','any',80,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Device Upgrade Bounty - Month 5','commission','any',81,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Device Upgrade Bounty - Month 6','commission','any',82,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','ePay RTR Invoice Reimbursement','commission','any',83,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','In-Store Device Financing Bounty - Month 1','commission','any',84,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','In-Store Device Financing Bounty - Month 2','commission','any',85,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','In-Store Device Financing Bounty - Month 3','commission','any',86,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','In-Store Device Financing Bounty - Month 4','commission','any',87,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','In-Store Device Financing Bounty - Month 5','commission','any',88,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','In-Store Device Financing Bounty - Month 6','commission','any',89,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','likewize. Coupon Redemption','commission','any',90,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','New Activation Bounty - Month 1','commission','any',91,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','New Activation Bounty - Month 2','commission','any',92,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','New Activation Bounty - Month 3','commission','any',93,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','New Activation Bounty - Month 4','commission','any',94,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','New Activation Bounty - Month 5','commission','any',95,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','New Activation Bounty - Month 6','commission','any',96,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','New iPhone Bounty - Month 4','commission','any',97,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','New iPhone Bounty - Month 5','commission','any',98,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','New iPhone Bounty - Month 6','commission','any',99,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','November Orange and Black Friday Bonus','commission','any',100,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','October Monster Sales Mania Bonus','commission','any',101,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Other Commission','commission','any',102,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Other Equipment Reimbursement','equipment_rebate','any',103,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Other Service Reimbursement','equipment_rebate','any',104,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Pay Later SPIFF - Month 1','spiff','any',105,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Pay Later SPIFF - Month 2','spiff','any',106,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Platinum Sale SPIFF','spiff','any',107,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Postpaid Dropship Launch SPIFF','spiff','any',108,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q1 2025 AAL Device Discount','equipment_rebate','any',109,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q2 2025 AAL Device Discount','equipment_rebate','any',110,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q2 2025 SAMSUNG GALAXY A16 BOGO 2 Line','equipment_rebate','any',111,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q2 2025 SAMSUNG GALAXY A16 BOGO 4 Line','equipment_rebate','any',112,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q3 2024 AAL B5G Device Discount','equipment_rebate','any',113,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q3 2024 AAL Device Discount','equipment_rebate','any',114,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q3 2024 Moto G 5G 2024 BOGO 2 Line','equipment_rebate','any',115,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q3 2024 Moto G 5G 2024 GOGO 2 Line','equipment_rebate','any',116,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q3 2024 Samsung Galaxy A15 GOGO 2 Line','equipment_rebate','any',117,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q3 2025 AAL Device Discount','equipment_rebate','any',118,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q3 2025 SAMSUNG Celero tablet bundle BOGO 2 Line','commission','any',119,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q3 2025 SAMSUNG Celero tablet bundle BOGO 3 Line','commission','any',120,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q3 2025 SAMSUNG GALAXY A16 BOGO 2 Line','commission','any',121,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q4 2024 AAL B5G Device Discount','equipment_rebate','any',122,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q4 2024 AAL Device Discount','equipment_rebate','any',123,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q4 2024 Celero 5G SC GOGO 2 Line','commission','any',124,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q4 2024 Moto G 5G 2024 GOGO 2 Line','commission','any',125,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q4 2024 moto g STYLUS 5G 2024 BOGO 2 Line','commission','any',126,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q4 2024 moto g STYLUS 5G 2024 BOGO 4 Line','commission','any',127,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q4 2025 AAL Device Discount','equipment_rebate','any',128,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Q4 2025 SAMSUNG Celero tablet bundle BOGO 2 Line','commission','any',129,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','S25 Device Spiff','spiff','any',130,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Simplified SIM Loading Bounty - Month 1','commission','any',131,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Simplified SIM Loading Bounty - Month 2','commission','any',132,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Simplified SIM Loading Bounty - Month 3','commission','any',133,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Simplified SIM Loading Bounty - Month 4','commission','any',134,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Simplified SIM Loading Bounty - Month 5','commission','any',135,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Simplified SIM Loading Bounty - Month 6','commission','any',136,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Sizzlin Summer Incentive Bonus','commission','any',137,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','equals','Tablet SPIFF','spiff','any',138,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','contains','Auto Top-Up','autopay_residual','any',1000,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','contains','SPIFF','spiff','any',1010,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','contains','Reimbursement','equipment_rebate','any',1020,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','contains','Discount','equipment_rebate','any',1030,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','contains','BOGO','equipment_rebate','any',1040,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','contains','GOGO','equipment_rebate','any',1050,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','contains','Promo','equipment_rebate','any',1060,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','contains','AAL','equipment_rebate','any',1070,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','contains','Bounty','commission','any',1080,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','contains','Bonus','commission','any',1090,true),
  ('00000000-0000-0000-0000-000000000001','boost','product_name','contains','Commission','commission','any',1100,true)
ON CONFLICT (org_id, source_report, match_field, match_op, pattern) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 072 complete — boost template seeded ('
       || (SELECT count(*) FROM commcalc.commission_category_map
           WHERE source_report='boost' AND is_seeded) || ' rules)' AS status;
