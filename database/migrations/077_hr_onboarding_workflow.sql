-- 077_hr_onboarding_workflow.sql — connect HR onboarding to the employee portal.
--
-- Adds, on top of mig 073's checklist:
--   1. A WORKFLOW state per hire (invited → in_progress → docs_submitted → docs_verified →
--      provisioned → active) + an audit trail of every transition/override (onboarding_event).
--   2. Two invite paths: a no-login token link (existing) OR a temp portal login (invite_method).
--   3. STRUCTURED intake capture (personal / address / emergency / direct-deposit) stored on the
--      profile as intake_data JSONB, PLUS the operational subset propagated onto storeops.employees.
--   4. A CONFIGURABLE capture-form definition (onboarding_intake_field) so a tenant can tailor the
--      fields to their own HR info form (seeded with a sensible default set).
-- Idempotent (ADD COLUMN / CREATE TABLE IF NOT EXISTS, every INSERT ON CONFLICT DO NOTHING).

-- ── 1. employees: operational fields the intake form propagates into ─────────────────────────────
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS legal_name          TEXT;
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS address_line1       TEXT;
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS address_line2       TEXT;
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS city                TEXT;
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS state               TEXT;
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS zip                 TEXT;
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS date_of_birth       DATE;
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS hire_date           DATE;
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS emergency_name      TEXT;
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS emergency_phone     TEXT;
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS emergency_relation  TEXT;

-- ── 2. employee_onboarding_profile: workflow + invite method + structured intake ────────────────
ALTER TABLE storeops.employee_onboarding_profile
  ADD COLUMN IF NOT EXISTS workflow_status     TEXT NOT NULL DEFAULT 'invited';
ALTER TABLE storeops.employee_onboarding_profile
  ADD COLUMN IF NOT EXISTS invite_method       TEXT;                       -- 'link' | 'login'
ALTER TABLE storeops.employee_onboarding_profile
  ADD COLUMN IF NOT EXISTS invited_at          TIMESTAMPTZ;
ALTER TABLE storeops.employee_onboarding_profile
  ADD COLUMN IF NOT EXISTS provisioned_at      TIMESTAMPTZ;
ALTER TABLE storeops.employee_onboarding_profile
  ADD COLUMN IF NOT EXISTS intake_data         JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE storeops.employee_onboarding_profile
  ADD COLUMN IF NOT EXISTS intake_submitted_at TIMESTAMPTZ;

-- ── 3. onboarding_event: append-only audit of workflow transitions + overrides ───────────────────
CREATE TABLE IF NOT EXISTS storeops.onboarding_event (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  employee_id  TEXT NOT NULL,
  event_type   TEXT NOT NULL,                 -- invited | intake_submitted | doc_submitted | verified | status_change | provisioned | override
  from_status  TEXT,
  to_status    TEXT,
  actor        TEXT,                          -- who did it ('employee', an HR name, 'system')
  reason       TEXT,                          -- override justification / note
  is_override  BOOLEAN NOT NULL DEFAULT false,
  detail       JSONB,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS onboarding_event_emp ON storeops.onboarding_event (org_id, employee_id, created_at);

-- ── 4. onboarding_intake_field: the CONFIGURABLE capture-form definition ─────────────────────────
-- section groups fields in the portal UI; propagate_to = a storeops.employees column the value is
-- copied into on submit (NULL = kept only in intake_data, e.g. sensitive bank details); sensitive
-- fields are never echoed back to the portal after submit.
CREATE TABLE IF NOT EXISTS storeops.onboarding_intake_field (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  key           TEXT NOT NULL,
  label         TEXT NOT NULL,
  section       TEXT NOT NULL DEFAULT 'personal',   -- personal | address | emergency | direct_deposit | custom
  field_type    TEXT NOT NULL DEFAULT 'text',       -- text | date | tel | email | number | select
  options       TEXT[],                             -- for field_type='select'
  required      BOOLEAN NOT NULL DEFAULT false,
  propagate_to  TEXT,                               -- storeops.employees column name, or NULL
  sensitive     BOOLEAN NOT NULL DEFAULT false,
  help_text     TEXT,
  sort_order    INT NOT NULL DEFAULT 100,
  is_active     BOOLEAN NOT NULL DEFAULT true,
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, key)
);

-- ── 5. Seed the default intake fields for ONE tenant (idempotent) ────────────────────────────────
CREATE OR REPLACE FUNCTION storeops.seed_intake_fields(p_org uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $fn$
BEGIN
  INSERT INTO storeops.onboarding_intake_field
    (org_id, key, label, section, field_type, options, required, propagate_to, sensitive, sort_order)
  VALUES
    (p_org,'legal_name','Full legal name','personal','text',NULL,true,'legal_name',false,10),
    (p_org,'date_of_birth','Date of birth','personal','date',NULL,true,'date_of_birth',false,20),
    (p_org,'phone','Phone number','personal','tel',NULL,true,'phone',false,30),
    (p_org,'address_line1','Street address','address','text',NULL,true,'address_line1',false,40),
    (p_org,'address_line2','Apt / unit (optional)','address','text',NULL,false,'address_line2',false,50),
    (p_org,'city','City','address','text',NULL,true,'city',false,60),
    (p_org,'state','State','address','text',NULL,true,'state',false,70),
    (p_org,'zip','ZIP code','address','text',NULL,true,'zip',false,80),
    (p_org,'emergency_name','Emergency contact name','emergency','text',NULL,true,'emergency_name',false,90),
    (p_org,'emergency_phone','Emergency contact phone','emergency','tel',NULL,true,'emergency_phone',false,100),
    (p_org,'emergency_relation','Relationship','emergency','text',NULL,false,'emergency_relation',false,110),
    (p_org,'dd_bank_name','Bank name','direct_deposit','text',NULL,false,NULL,true,120),
    (p_org,'dd_routing','Routing number','direct_deposit','text',NULL,false,NULL,true,130),
    (p_org,'dd_account','Account number','direct_deposit','text',NULL,false,NULL,true,140),
    (p_org,'dd_account_type','Account type','direct_deposit','select',ARRAY['Checking','Savings'],false,NULL,true,150)
  ON CONFLICT (org_id, key) DO NOTHING;
END;
$fn$;
-- 2026-08-09 (mig 724): the web roles were REMOVED from this grant. A function granted to
-- anon is callable by anyone holding the public anon key, and SECURITY DEFINER means it runs
-- as the owner with RLS bypassed. The backend uses the SERVICE ROLE, so service_role alone is
-- correct. See docs/PLAN_REVIEW_2026-08-09.md finding F1.
GRANT EXECUTE ON FUNCTION storeops.seed_intake_fields(uuid) TO service_role;

-- ── 6. Fold intake-field seeding into the tenant provisioning engine (mig 076) ───────────────────
-- Append to seed_tenant_defaults() so NEW tenants + re-syncs get the capture form too. The backend
-- SEED_VERSION is bumped to 2 (entitlements.py) so every existing tenant re-seeds on next login.
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

  -- NEW in mig 077: the configurable employee-intake capture form.
  PERFORM storeops.seed_intake_fields(p_org);
END;
$fn$;

-- ── 7. Back-fill the intake fields for EVERY existing tenant now ──────────────────────────────────
DO $backfill$
DECLARE t record;
BEGIN
  FOR t IN SELECT org_id FROM storeops.tenants LOOP
    PERFORM storeops.seed_intake_fields(t.org_id);
  END LOOP;
END $backfill$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 077 complete — onboarding workflow + configurable intake seeded for '
  || (SELECT count(*) FROM storeops.tenants)::text || ' tenant(s)' AS status;
