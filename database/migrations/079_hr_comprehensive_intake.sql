-- 079_hr_comprehensive_intake.sql — expand the onboarding intake form into a comprehensive,
-- standard new-hire packet so a hire submits ALL their information in one place and it's stored
-- on their employee file (employee_onboarding_profile.intake_data + the tracker's "Captured
-- information") for later reference.
--
-- WHAT THIS DOES: rewrites storeops.seed_intake_fields(p_org) to seed the full field set — the
-- original 15 fields PLUS ~25 more grouped into standard HR sections:
--   • personal            — legal + preferred name, DOB, gender/marital (optional, EEO/tax), phone, personal email
--   • address             — street/apt/city/state/ZIP
--   • emergency           — primary + optional secondary emergency contact
--   • work_eligibility    — I-9 work-authorization status + A-Number + expiration + I-9 confirmation
--   • tax                 — W-4 federal filing status, multiple-jobs, dependents/other/deductions/extra $, state status
--   • direct_deposit      — bank / routing / account / type (repositioned after tax)
--   • policies            — handbook, code of conduct, anti-harassment, confidentiality/NDA, at-will, accuracy acks
--
-- NOTE ON PII: full SSN is intentionally NOT captured here (kept only in the uploaded W-4/I-9 PDFs,
-- matching the app's existing posture). Bank + A-Number fields are flagged sensitive (never echoed
-- back to the portal after submit). Every value lands in intake_data on the employee's profile.
--
-- New fields propagate_to NULL (kept in intake_data — the existing allow-list only covers the
-- operational columns already on storeops.employees; nothing new is written to that table).
--
-- IDEMPOTENT + ADDITIVE: every INSERT is ON CONFLICT (org_id,key) DO NOTHING, so a tenant that
-- already has the original 15 keeps them and only gains the new ones; a tenant that customized a
-- field is untouched. The direct-deposit reposition only moves rows still at their default
-- sort_order. Back-fills every existing tenant. Bump entitlements.SEED_VERSION so logins re-seed too.

CREATE OR REPLACE FUNCTION storeops.seed_intake_fields(p_org uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $fn$
BEGIN
  INSERT INTO storeops.onboarding_intake_field
    (org_id, key, label, section, field_type, options, required, propagate_to, sensitive, help_text, sort_order)
  VALUES
    -- ── personal ──────────────────────────────────────────────────────────────────────────────
    (p_org,'legal_name','Full legal name','personal','text',NULL,true,'legal_name',false,NULL,10),
    (p_org,'preferred_name','Preferred name (optional)','personal','text',NULL,false,NULL,false,'What you like to be called',12),
    (p_org,'date_of_birth','Date of birth','personal','date',NULL,true,'date_of_birth',false,NULL,20),
    (p_org,'gender','Gender (optional)','personal','select',ARRAY['Male','Female','Non-binary','Prefer not to say'],false,NULL,false,'Voluntary — used only for EEO reporting',22),
    (p_org,'marital_status','Marital status (optional)','personal','select',ARRAY['Single','Married','Divorced','Widowed','Domestic partnership','Prefer not to say'],false,NULL,false,NULL,24),
    (p_org,'phone','Mobile phone','personal','tel',NULL,true,'phone',false,NULL,30),
    (p_org,'personal_email','Personal email','personal','email',NULL,true,NULL,false,'Where we send pay + benefits info',32),
    -- ── address ───────────────────────────────────────────────────────────────────────────────
    (p_org,'address_line1','Street address','address','text',NULL,true,'address_line1',false,NULL,40),
    (p_org,'address_line2','Apt / unit (optional)','address','text',NULL,false,'address_line2',false,NULL,50),
    (p_org,'city','City','address','text',NULL,true,'city',false,NULL,60),
    (p_org,'state','State','address','text',NULL,true,'state',false,NULL,70),
    (p_org,'zip','ZIP code','address','text',NULL,true,'zip',false,NULL,80),
    -- ── emergency contact(s) ──────────────────────────────────────────────────────────────────
    (p_org,'emergency_name','Emergency contact name','emergency','text',NULL,true,'emergency_name',false,NULL,90),
    (p_org,'emergency_phone','Emergency contact phone','emergency','tel',NULL,true,'emergency_phone',false,NULL,100),
    (p_org,'emergency_relation','Relationship','emergency','text',NULL,false,'emergency_relation',false,NULL,110),
    (p_org,'emergency2_name','Second emergency contact (optional)','emergency','text',NULL,false,NULL,false,NULL,112),
    (p_org,'emergency2_phone','Second contact phone (optional)','emergency','tel',NULL,false,NULL,false,NULL,114),
    (p_org,'emergency2_relation','Second contact relationship (optional)','emergency','text',NULL,false,NULL,false,NULL,116),
    -- ── work eligibility (Form I-9) ───────────────────────────────────────────────────────────
    (p_org,'work_authorization','Work authorization status','work_eligibility','select',ARRAY['U.S. Citizen','U.S. National','Lawful Permanent Resident','Alien authorized to work'],true,NULL,false,'Confirmed on Form I-9 during onboarding',200),
    (p_org,'alien_registration_number','A-Number / USCIS number (if applicable)','work_eligibility','text',NULL,false,NULL,true,'Only if you selected Permanent Resident or Authorized alien',210),
    (p_org,'work_auth_expiration','Work authorization expiration (if any)','work_eligibility','date',NULL,false,NULL,false,'Leave blank if not applicable',220),
    (p_org,'i9_ack','I-9 confirmation','work_eligibility','select',ARRAY['I confirm I can provide I-9 documents'],true,NULL,false,'Bring acceptable ID on day one (e.g. passport, or license + Social Security card)',230),
    -- ── tax withholding (Form W-4) ────────────────────────────────────────────────────────────
    (p_org,'federal_filing_status','Federal filing status (W-4 Step 1c)','tax','select',ARRAY['Single or Married filing separately','Married filing jointly','Head of household'],true,NULL,false,NULL,300),
    (p_org,'multiple_jobs','Multiple jobs / spouse works? (W-4 Step 2)','tax','select',ARRAY['No','Yes'],false,NULL,false,'Yes if you hold more than one job or your spouse also works',310),
    (p_org,'dependents_amount','Dependents claim $ (W-4 Step 3)','tax','number',NULL,false,NULL,false,'Total $ for qualifying children/dependents, or 0',320),
    (p_org,'other_income','Other income $ (W-4 Step 4a)','tax','number',NULL,false,NULL,false,'Optional',330),
    (p_org,'extra_deductions','Deductions $ (W-4 Step 4b)','tax','number',NULL,false,NULL,false,'Optional',340),
    (p_org,'extra_withholding','Extra withholding per paycheck $ (W-4 Step 4c)','tax','number',NULL,false,NULL,false,'Optional',350),
    (p_org,'state_filing_status','State withholding (if different from federal)','tax','text',NULL,false,NULL,false,'State allowances/status — varies by state',360),
    -- ── direct deposit (repositioned after tax) ───────────────────────────────────────────────
    (p_org,'dd_bank_name','Bank name','direct_deposit','text',NULL,false,NULL,true,NULL,400),
    (p_org,'dd_routing','Routing number','direct_deposit','text',NULL,false,NULL,true,'9 digits',410),
    (p_org,'dd_account','Account number','direct_deposit','text',NULL,false,NULL,true,NULL,420),
    (p_org,'dd_account_type','Account type','direct_deposit','select',ARRAY['Checking','Savings'],false,NULL,true,NULL,430),
    -- ── policy acknowledgements ───────────────────────────────────────────────────────────────
    (p_org,'ack_handbook','Employee handbook','policies','select',ARRAY['I acknowledge and agree'],true,NULL,false,'I have received and read the employee handbook',500),
    (p_org,'ack_conduct','Code of conduct','policies','select',ARRAY['I acknowledge and agree'],true,NULL,false,'I agree to the code of conduct and professional standards',510),
    (p_org,'ack_harassment','Anti-harassment & non-discrimination','policies','select',ARRAY['I acknowledge and agree'],true,NULL,false,NULL,520),
    (p_org,'ack_confidentiality','Confidentiality / NDA','policies','select',ARRAY['I acknowledge and agree'],true,NULL,false,'I agree to protect confidential and customer information',530),
    (p_org,'ack_at_will','At-will employment','policies','select',ARRAY['I acknowledge and agree'],true,NULL,false,'I understand employment is at-will',540),
    (p_org,'ack_accuracy','Accuracy certification','policies','select',ARRAY['I certify this is accurate'],true,NULL,false,'I certify all information I provided is true and complete',560)
  ON CONFLICT (org_id, key) DO NOTHING;

  -- Move the original direct-deposit rows to sit after the new tax section — only when still at
  -- their default positions (a tenant that reordered them is left alone). Idempotent.
  UPDATE storeops.onboarding_intake_field
     SET sort_order = CASE key
                        WHEN 'dd_bank_name'     THEN 400
                        WHEN 'dd_routing'       THEN 410
                        WHEN 'dd_account'       THEN 420
                        WHEN 'dd_account_type'  THEN 430
                      END
   WHERE org_id = p_org
     AND key IN ('dd_bank_name','dd_routing','dd_account','dd_account_type')
     AND sort_order IN (120,130,140,150);
END;
$fn$;
-- 2026-08-09 (mig 724): the web roles were REMOVED from this grant. A function granted to
-- anon is callable by anyone holding the public anon key, and SECURITY DEFINER means it runs
-- as the owner with RLS bypassed. The backend uses the SERVICE ROLE, so service_role alone is
-- correct. See docs/PLAN_REVIEW_2026-08-09.md finding F1.
GRANT EXECUTE ON FUNCTION storeops.seed_intake_fields(uuid) TO service_role;

-- ── Back-fill the comprehensive fields for EVERY existing tenant now ──────────────────────────────
DO $backfill$
DECLARE t record;
BEGIN
  FOR t IN SELECT org_id FROM storeops.tenants LOOP
    PERFORM storeops.seed_intake_fields(t.org_id);
  END LOOP;
END $backfill$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 079 complete — comprehensive intake seeded for '
  || (SELECT count(*) FROM storeops.tenants)::text || ' tenant(s); '
  || (SELECT count(*) FROM storeops.onboarding_intake_field
        WHERE org_id = '00000000-0000-0000-0000-000000000001')::text || ' fields on the house org' AS status;
