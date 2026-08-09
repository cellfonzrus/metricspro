-- 708_core_remediation_playbook_seed.sql — MULTI-TENANT: fold the Auto-Remediation starter catalog
-- into seed_tenant_defaults() so EVERY tenant gets it, not just the house org.
--
-- WHY: migration 097 seeded commcalc.remediation_playbook ONLY for the house org
-- (00000000-0000-0000-0000-000000000001). Its own comment said "A new tenant seeds its own via
-- seed_tenant_defaults / the catalog admin UI" — but that per-tenant seed path was never added. So a
-- second tenant (e.g. Luxelink) opens Auto-Remediation to an EMPTY playbook catalog. The 4 starter
-- playbooks are carrier-NEUTRAL, generic ops actions (nothing Boost/VIP/ePay-specific), so they belong
-- in the shared tenant-default seed exactly like the store-visit checklist / HR onboarding.
--
-- WHAT: rewrite commcalc.seed_tenant_defaults(p_org) = the AUTHORITATIVE mig-077 body (unchanged) +
-- one new idempotent remediation_playbook INSERT block, then back-fill every existing tenant. The
-- backend SEED_VERSION is bumped to 5 (entitlements.py) so any tenant created between deploy and this
-- migration also re-seeds on its next /core/me. Additive + idempotent (every INSERT is ON CONFLICT DO
-- NOTHING; the PERFORM seed_intake_fields(p_org) call picks up mig-079's version at runtime).

CREATE OR REPLACE FUNCTION commcalc.seed_tenant_defaults(p_org uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $fn$
BEGIN
  INSERT INTO commcalc.companies (org_id, name, legal_name)
  VALUES (p_org, 'Default Company', 'Default Company')
  ON CONFLICT (org_id, name) DO NOTHING;

  -- carrier-neutral: no default carrier seeded (product-owner decision, see mig 076).

  INSERT INTO storeops.checklist_items (org_id, item_key, label, category, input_type, sort_order) VALUES
    (p_org,'uniform','Uniform','appearance','check',10),
    (p_org,'lanyard','Lanyard / name card','appearance','check',20),
    (p_org,'broken_tiles','No broken tiles','facilities','check',30),
    (p_org,'hvac','HVAC working','facilities','check',40),
    (p_org,'counter_clean','Counter clean','facilities','check',50),
    (p_org,'floor_clean','Floor clean','facilities','check',60),
    (p_org,'windows_clean','Windows clean','facilities','check',70),
    (p_org,'alarm','Alarm working','security','check',80),
    (p_org,'cameras','Cameras working','security','check',90),
    (p_org,'safe','Safe','security','check',100),
    (p_org,'camera_on_safe','Camera pointed at safe','security','check',110),
    (p_org,'cc_machine','Credit card machine','facilities','check',120),
    (p_org,'water','Water in store','supplies','check',130),
    (p_org,'pens','Pens in store','supplies','check',140),
    (p_org,'currency_pen','Currency-checking pen in store','supplies','check',150),
    (p_org,'accessories_stocked','All accessories in store (list what is needed below)','accessories','check',160)
  ON CONFLICT (org_id, item_key) DO NOTHING;

  INSERT INTO storeops.onboarding_category (org_id, key, label, sort_order) VALUES
    (p_org,'personal','Personal Information',10),
    (p_org,'tax','Tax Forms',20),
    (p_org,'eligibility','Work Eligibility (I-9 & ID)',30),
    (p_org,'direct_deposit','Direct Deposit',40),
    (p_org,'agreements','Company Policies & Agreements',50),
    (p_org,'equipment','Equipment & System Access',60),
    (p_org,'training','Training & Orientation',70)
  ON CONFLICT (org_id, key) DO NOTHING;

  INSERT INTO storeops.onboarding_task
    (org_id, category_id, key, label, description, owner_role, doc_url, doc_label, is_fillable, requires_upload, applies_state, sort_order)
  SELECT p_org, c.id, v.key, v.label, v.description, v.owner_role,
         v.doc_url, v.doc_label, v.is_fillable, v.requires_upload, v.applies_state, v.sort_order
  FROM (VALUES
    ('personal','personal_info','Personal information & emergency contact','Full legal name, address, phone, and an emergency contact.','employee',NULL,NULL,false,true,NULL,10),
    ('tax','w4_federal','Federal Form W-4','Employee''s Withholding Certificate (federal income tax).','employee','https://www.irs.gov/pub/irs-pdf/fw4.pdf','IRS Form W-4',true,true,NULL,10),
    ('tax','w4_ny','NY State Form IT-2104','New York Employee''s Withholding Allowance Certificate.','employee','https://www.tax.ny.gov/pdf/current_forms/it/it2104_fill_in.pdf','NY IT-2104',true,true,'NY',20),
    ('tax','w4_nj','NJ State Form NJ-W4','New Jersey Employee''s Withholding Allowance Certificate.','employee','https://www.nj.gov/treasury/taxation/pdf/current/njw4.pdf','NJ-W4',true,true,'NJ',21),
    ('tax','w4_de','DE State Form W-4DE','Delaware Employee''s Withholding Allowance Certificate.','employee','https://revenuefiles.delaware.gov/docs/w4de.pdf','DE W-4DE',true,true,'DE',22),
    ('tax','w4_pa','PA Form REV-419','Pennsylvania Employee''s Nonwithholding Application Certificate (PA has a flat tax; file REV-419 only if claiming exemption/reciprocity).','employee','https://www.pa.gov/content/dam/copapwp-pagov/en/revenue/documents/formsandpublications/formsforbusinesses/employerwithholding/documents/rev-419.pdf','PA REV-419',true,false,'PA',23),
    ('tax','w4_il','IL State Form IL-W-4','Illinois Employee''s Withholding Allowance Certificate.','employee','https://tax.illinois.gov/content/dam/soi/en/web/tax/forms/withholding/documents/currentyear/il-w-4.pdf','IL-W-4',true,true,'IL',24),
    ('tax','w4_ct','CT State Form CT-W4','Connecticut Employee''s Withholding Certificate.','employee','https://portal.ct.gov/-/media/drs/forms/2024/wth/ct-w4_1223.pdf','CT-W4',true,true,'CT',25),
    ('tax','w4_ma','MA State Form M-4','Massachusetts Employee''s Withholding Exemption Certificate.','employee','https://www.mass.gov/doc/form-m-4-massachusetts-employees-withholding-exemption-certificate/download','MA M-4',true,true,'MA',26),
    ('tax','w4_in','IN State Form WH-4','Indiana Employee''s Withholding Exemption & County Status Certificate.','employee','https://forms.in.gov/download.aspx?id=2702','IN WH-4',true,true,'IN',27),
    ('eligibility','i9','Form I-9 — Employment Eligibility','USCIS Employment Eligibility Verification. Section 1 by the employee; HR completes Section 2 with original ID documents.','employee','https://www.uscis.gov/sites/default/files/document/forms/i-9.pdf','USCIS Form I-9',true,true,NULL,10),
    ('eligibility','id_docs','Identity & work-authorization documents','Upload a photo of the ID(s) used for I-9 (e.g. driver''s license + Social Security card, or passport).','employee',NULL,NULL,false,true,NULL,20),
    ('eligibility','i9_verify','HR verifies I-9 Section 2','HR inspects the original documents and completes/verifies Section 2.','hr',NULL,NULL,false,false,NULL,30),
    ('direct_deposit','dd_auth','Direct deposit authorization','Voided check or bank letter + a completed direct-deposit authorization.','employee',NULL,NULL,false,true,NULL,10),
    ('agreements','handbook','Employee handbook acknowledgment','Read and sign the employee handbook acknowledgment.','hr',NULL,NULL,false,true,NULL,10),
    ('agreements','conduct','Code of conduct / confidentiality','Sign the code of conduct and confidentiality agreement.','hr',NULL,NULL,false,true,NULL,20),
    ('equipment','pos_access','POS / system credentials provisioned','Create the POS login and store-system access for the new hire.','dm',NULL,NULL,false,false,NULL,10),
    ('equipment','uniform','Uniform & name badge issued','Issue uniform, name badge, and any store keys/access.','dm',NULL,NULL,false,false,NULL,20),
    ('training','orientation','Store orientation completed','Walk the new hire through store operations, safety, and opening/closing.','market_manager',NULL,NULL,false,false,NULL,10),
    ('training','sales_training','Sales & compliance training','Complete the sales process and compliance training modules.','dm',NULL,NULL,false,false,NULL,20)
  ) AS v(cat_key, key, label, description, owner_role, doc_url, doc_label, is_fillable, requires_upload, applies_state, sort_order)
  JOIN storeops.onboarding_category c
    ON c.org_id = p_org AND c.key = v.cat_key
  ON CONFLICT (org_id, key) DO NOTHING;

  INSERT INTO storeops.ticket_counters (org_id, last_value)
  VALUES (p_org, 1000)
  ON CONFLICT (org_id) DO NOTHING;

  -- mig 077: the configurable employee-intake capture form.
  PERFORM storeops.seed_intake_fields(p_org);

  -- NEW in mig 708: the Auto-Remediation starter playbook catalog (mig 097 seeded it house-only).
  -- Carrier-neutral, generic ops actions. dedupe_timeoff + add_store_alias are IMPLEMENTED (enabled);
  -- recalc_period + rerun_sweep are roadmap → seeded DISABLED (visible in the catalog, not runnable).
  INSERT INTO commcalc.remediation_playbook (org_id, key, name, description, risk_level, enabled, params_schema) VALUES
   (p_org,'dedupe_timeoff',
      'Fix voided time-off blocking scheduling',
      'Deny a duplicate approved/pending time-off row that survived a void and still blocks scheduling the rep.',
      'low', true,  '{"employee_id":"optional","date":"optional YYYY-MM-DD"}'::jsonb),
   (p_org,'add_store_alias',
      'Map a store spelling to a store code',
      'Add a store alias so a sales-file store spelling attaches to the right store (Daily Targets / P&L).',
      'low', true,  '{"alias":"required","store_code":"required"}'::jsonb),
   (p_org,'recalc_period',
      'Recalculate commissions for a period',
      'Re-run the commission calculation for a month (roadmap — not yet executable via the agent).',
      'medium', false, '{"period":"required e.g. June 2026"}'::jsonb),
   (p_org,'rerun_sweep',
      'Re-run a data sweep',
      'Re-run a connector / email sweep to re-ingest data (roadmap — not yet executable via the agent).',
      'medium', false, '{"kind":"required e.g. email"}'::jsonb)
  ON CONFLICT (org_id, key) DO NOTHING;
END;
$fn$;

-- 2026-08-09 (mig 724): the web roles were REMOVED from this grant. A function granted to
-- anon is callable by anyone holding the public anon key, and SECURITY DEFINER means it runs
-- as the owner with RLS bypassed. The backend uses the SERVICE ROLE, so service_role alone is
-- correct. See docs/PLAN_REVIEW_2026-08-09.md finding F1.
GRANT EXECUTE ON FUNCTION commcalc.seed_tenant_defaults(uuid) TO service_role;

-- ── Back-fill EVERY existing tenant now (idempotent) ─────────────────────────────────────────────
DO $backfill$
DECLARE t record;
BEGIN
  FOR t IN SELECT org_id FROM storeops.tenants LOOP
    PERFORM commcalc.seed_tenant_defaults(t.org_id);
  END LOOP;
END $backfill$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 708 complete — remediation playbook catalog folded into seed_tenant_defaults() + back-filled '
  || (SELECT count(*) FROM storeops.tenants)::text || ' tenant(s)' AS status;
