-- 073_hr_onboarding.sql — configurable employee ONBOARDING CHECKLIST for the HR module.
--
-- WHY: HR onboards every new employee against the same checklist, but the items, the collapsible
-- CATEGORIES they group under, WHO is responsible for each (HR vs the DM vs the Market Manager vs the
-- employee), and the live STATE/FEDERAL document links all differ per tenant and per state. This makes
-- the whole checklist data-driven (configurable in /hr/onboarding), attaches each employee's progress +
-- uploaded documents to their record (verified by HR), and gives a pre-start employee a credential-less
-- QR portal (a per-employee token + a DOB/last-4 identity gate) to read the forms and upload them BEFORE
-- they have a login.
--
-- FOUR tables (all storeops, keyed on storeops.employees.employee_id which already propagates everywhere):
--   onboarding_category          — the collapsible groups (template)
--   onboarding_task              — the checklist items (template): owner role, live doc link, flags, state
--   employee_onboarding          — per (employee × task): status + uploaded document + verification
--   employee_onboarding_profile  — per employee: work state (filters state forms) + the QR access token
--
-- Additive + idempotent + SAFE: four NEW tables only; nothing else is touched. Re-running is a no-op
-- (ON CONFLICT DO NOTHING). Seeds a sensible default checklist with VERIFIED official federal + 8-state
-- withholding-form links (all editable in the admin UI afterward). The backend degrades gracefully if
-- this migration hasn't been run yet (endpoints return empty / a clear 400, never a 500).

-- ── 1. CATEGORIES (collapsible groups) ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.onboarding_category (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  key         TEXT NOT NULL,                 -- stable slug
  label       TEXT NOT NULL,
  sort_order  INT  NOT NULL DEFAULT 100,
  is_active   BOOLEAN NOT NULL DEFAULT true,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, key)
);

-- ── 2. TASKS (checklist items) ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.onboarding_task (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  category_id     UUID REFERENCES storeops.onboarding_category(id) ON DELETE CASCADE,
  key             TEXT NOT NULL,
  label           TEXT NOT NULL,
  description     TEXT,
  owner_role      TEXT NOT NULL DEFAULT 'employee',  -- 'employee' | 'hr' | 'dm' | 'market_manager'
  doc_url         TEXT,                              -- live form link (state/federal); employee clicks to fill
  doc_label       TEXT,                              -- e.g. 'IRS Form W-4'
  is_fillable     BOOLEAN NOT NULL DEFAULT false,    -- the linked form can be completed online before upload
  requires_upload BOOLEAN NOT NULL DEFAULT true,     -- employee must upload the completed/signed document
  applies_state   TEXT,                              -- NULL = everyone; 'NY'/'NJ'/… = only that work state
  sort_order      INT  NOT NULL DEFAULT 100,
  is_active       BOOLEAN NOT NULL DEFAULT true,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, key)
);
CREATE INDEX IF NOT EXISTS onboarding_task_cat ON storeops.onboarding_task (org_id, category_id);

-- ── 3. PER-EMPLOYEE TASK STATUS + uploaded document + verification ─────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.employee_onboarding (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  employee_id    TEXT NOT NULL,
  task_id        UUID NOT NULL REFERENCES storeops.onboarding_task(id) ON DELETE CASCADE,
  status         TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'submitted' | 'verified' | 'na'
  document_path  TEXT,                             -- storage path in the private onboarding-docs bucket
  document_name  TEXT,
  note           TEXT,
  submitted_at   TIMESTAMPTZ,
  verified_by    TEXT,
  verified_at    TIMESTAMPTZ,
  updated_at     TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, employee_id, task_id)
);
CREATE INDEX IF NOT EXISTS employee_onboarding_emp ON storeops.employee_onboarding (org_id, employee_id);

-- ── 4. PER-EMPLOYEE PROFILE — work state (filters state forms) + credential-less QR access ─────────
CREATE TABLE IF NOT EXISTS storeops.employee_onboarding_profile (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  employee_id       TEXT NOT NULL,
  work_state        TEXT,                            -- 'NY'/'NJ'/… — which state withholding form applies
  access_token      TEXT UNIQUE,                     -- opaque token embedded in the QR (revocable)
  verify_kind       TEXT,                            -- 'dob' | 'ssn4' — which identity gate the portal shows
  verify_dob        DATE,                            -- gate value when verify_kind='dob'
  verify_ssn4       TEXT,                            -- gate value (last 4) when verify_kind='ssn4'
  token_active      BOOLEAN NOT NULL DEFAULT true,
  token_expires_at  TIMESTAMPTZ,                     -- NULL = no expiry
  started_at        TIMESTAMPTZ DEFAULT NOW(),
  completed_at      TIMESTAMPTZ,
  UNIQUE (org_id, employee_id)
);
CREATE INDEX IF NOT EXISTS onboarding_profile_token ON storeops.employee_onboarding_profile (access_token);

-- ── SEED: default categories ───────────────────────────────────────────────────────────────────────
INSERT INTO storeops.onboarding_category (org_id, key, label, sort_order) VALUES
  ('00000000-0000-0000-0000-000000000001','personal','Personal Information',10),
  ('00000000-0000-0000-0000-000000000001','tax','Tax Forms',20),
  ('00000000-0000-0000-0000-000000000001','eligibility','Work Eligibility (I-9 & ID)',30),
  ('00000000-0000-0000-0000-000000000001','direct_deposit','Direct Deposit',40),
  ('00000000-0000-0000-0000-000000000001','agreements','Company Policies & Agreements',50),
  ('00000000-0000-0000-0000-000000000001','equipment','Equipment & System Access',60),
  ('00000000-0000-0000-0000-000000000001','training','Training & Orientation',70)
ON CONFLICT (org_id, key) DO NOTHING;

-- ── SEED: default tasks (category_id resolved by key) ──────────────────────────────────────────────
-- helper note: cat() inlined as a subselect per row keeps this a single idempotent statement.
INSERT INTO storeops.onboarding_task
  (org_id, category_id, key, label, description, owner_role, doc_url, doc_label, is_fillable, requires_upload, applies_state, sort_order)
SELECT '00000000-0000-0000-0000-000000000001', c.id, v.key, v.label, v.description, v.owner_role,
       v.doc_url, v.doc_label, v.is_fillable, v.requires_upload, v.applies_state, v.sort_order
FROM (VALUES
  -- Personal Information
  ('personal','personal_info','Personal information & emergency contact','Full legal name, address, phone, and an emergency contact.','employee',NULL,NULL,false,true,NULL,10),
  -- Tax Forms — federal + per-state withholding certificate (only the employee's work state shows)
  ('tax','w4_federal','Federal Form W-4','Employee''s Withholding Certificate (federal income tax).','employee','https://www.irs.gov/pub/irs-pdf/fw4.pdf','IRS Form W-4',true,true,NULL,10),
  ('tax','w4_ny','NY State Form IT-2104','New York Employee''s Withholding Allowance Certificate.','employee','https://www.tax.ny.gov/pdf/current_forms/it/it2104_fill_in.pdf','NY IT-2104',true,true,'NY',20),
  ('tax','w4_nj','NJ State Form NJ-W4','New Jersey Employee''s Withholding Allowance Certificate.','employee','https://www.nj.gov/treasury/taxation/pdf/current/njw4.pdf','NJ-W4',true,true,'NJ',21),
  ('tax','w4_de','DE State Form W-4DE','Delaware Employee''s Withholding Allowance Certificate.','employee','https://revenuefiles.delaware.gov/docs/w4de.pdf','DE W-4DE',true,true,'DE',22),
  ('tax','w4_pa','PA Form REV-419','Pennsylvania Employee''s Nonwithholding Application Certificate (PA has a flat tax; file REV-419 only if claiming exemption/reciprocity).','employee','https://www.pa.gov/content/dam/copapwp-pagov/en/revenue/documents/formsandpublications/formsforbusinesses/employerwithholding/documents/rev-419.pdf','PA REV-419',true,false,'PA',23),
  ('tax','w4_il','IL State Form IL-W-4','Illinois Employee''s Withholding Allowance Certificate.','employee','https://tax.illinois.gov/content/dam/soi/en/web/tax/forms/withholding/documents/currentyear/il-w-4.pdf','IL-W-4',true,true,'IL',24),
  ('tax','w4_ct','CT State Form CT-W4','Connecticut Employee''s Withholding Certificate.','employee','https://portal.ct.gov/-/media/drs/forms/2024/wth/ct-w4_1223.pdf','CT-W4',true,true,'CT',25),
  ('tax','w4_ma','MA State Form M-4','Massachusetts Employee''s Withholding Exemption Certificate.','employee','https://www.mass.gov/doc/form-m-4-massachusetts-employees-withholding-exemption-certificate/download','MA M-4',true,true,'MA',26),
  ('tax','w4_in','IN State Form WH-4','Indiana Employee''s Withholding Exemption & County Status Certificate.','employee','https://forms.in.gov/download.aspx?id=2702','IN WH-4',true,true,'IN',27),
  -- Work Eligibility
  ('eligibility','i9','Form I-9 — Employment Eligibility','USCIS Employment Eligibility Verification. Section 1 by the employee; HR completes Section 2 with original ID documents.','employee','https://www.uscis.gov/sites/default/files/document/forms/i-9.pdf','USCIS Form I-9',true,true,NULL,10),
  ('eligibility','id_docs','Identity & work-authorization documents','Upload a photo of the ID(s) used for I-9 (e.g. driver''s license + Social Security card, or passport).','employee',NULL,NULL,false,true,NULL,20),
  ('eligibility','i9_verify','HR verifies I-9 Section 2','HR inspects the original documents and completes/verifies Section 2.','hr',NULL,NULL,false,false,NULL,30),
  -- Direct Deposit
  ('direct_deposit','dd_auth','Direct deposit authorization','Voided check or bank letter + a completed direct-deposit authorization.','employee',NULL,NULL,false,true,NULL,10),
  -- Company Policies & Agreements
  ('agreements','handbook','Employee handbook acknowledgment','Read and sign the employee handbook acknowledgment.','hr',NULL,NULL,false,true,NULL,10),
  ('agreements','conduct','Code of conduct / confidentiality','Sign the code of conduct and confidentiality agreement.','hr',NULL,NULL,false,true,NULL,20),
  -- Equipment & System Access
  ('equipment','pos_access','POS / system credentials provisioned','Create the POS login and store-system access for the new hire.','dm',NULL,NULL,false,false,NULL,10),
  ('equipment','uniform','Uniform & name badge issued','Issue uniform, name badge, and any store keys/access.','dm',NULL,NULL,false,false,NULL,20),
  -- Training & Orientation
  ('training','orientation','Store orientation completed','Walk the new hire through store operations, safety, and opening/closing.','market_manager',NULL,NULL,false,false,NULL,10),
  ('training','sales_training','Sales & compliance training','Complete the sales process and compliance training modules.','dm',NULL,NULL,false,false,NULL,20)
) AS v(cat_key, key, label, description, owner_role, doc_url, doc_label, is_fillable, requires_upload, applies_state, sort_order)
JOIN storeops.onboarding_category c
  ON c.org_id = '00000000-0000-0000-0000-000000000001' AND c.key = v.cat_key
ON CONFLICT (org_id, key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 073 complete — '
  || (SELECT count(*) FROM storeops.onboarding_category WHERE org_id='00000000-0000-0000-0000-000000000001')::text || ' categories, '
  || (SELECT count(*) FROM storeops.onboarding_task     WHERE org_id='00000000-0000-0000-0000-000000000001')::text || ' tasks seeded' AS status;
