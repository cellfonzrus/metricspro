-- 926_data_lineage_retention.sql — record the Retention Analysis dependency (owner 2026-08-26)
--
-- The owner flagged that "Sales Analyzer — 3-Month Retention" (now RENAMED to Retention Analysis) showed
-- nothing for LuxeLink because it was never mapped to WHICH ingested report it calculates from. The basis of
-- 3-month retention is whether the subscriber is active in month 3 — proven by whether the RESIDUAL was paid.
-- This adds those edges to the data_lineage schematic so the dependency is documented and the onboarding
-- checklist can flag it as unwired for a tenant that hasn't ingested the residual (or MI) report.
--
-- Idempotent: clears the retention edges then reinserts. Additive; no money touched.
-- REVERT: delete from commcalc.data_lineage where affected_key = 'retention_analysis' or source_key = 'retention_analysis';

delete from commcalc.data_lineage where affected_key = 'retention_analysis' or source_key = 'retention_analysis';

insert into commcalc.data_lineage
  (source_key, source_label, entry_point, affected_key, affected_label, surface, kind, auto_updated, effect_code, effect_english, seq)
values
('residual_report','Residual / MA commission','POST /commcalc/upload/ma_commission · VidaPay sweep','retention_analysis','Retention Analysis (3-month)','Retention Analysis','display',true,'sales_analyzer.analyze (residual basis)','Whether the month-3 residual was PAID = the subscriber was active in the 3rd month → retained; not paid → churned. This is the basis of 3-month retention for residual/MA tenants.',70),
('mi_report','MI subscriber status','POST /commcalc/upload/mi_report','retention_analysis','Retention Analysis (3-month)','Retention Analysis','display',true,'sales_analyzer.analyze (MI basis)','A subscriber deactivation date within 90 days of activation = churned before the 3rd bill (the alternate basis used when the MI report is uploaded).',71),
('retention_analysis','Retention Analysis','—','coaching','Rep coaching / churn drill-down','Rep Coaching','display',true,'sales_analyzer + coaching','The churned line items (rep, device, MRC, dates) feed the coaching / churn drill-down.',72);

notify pgrst, 'reload schema';
select count(*) || ' data_lineage edges after retention seed' as status from commcalc.data_lineage;
