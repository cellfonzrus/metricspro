-- 743_pos_onboarding_tax_coverage.sql   (platform-core band 700-799; core schema)
--
-- THE POS SETUP WIZARD WAS REPORTING SALES TAX AS DONE WHILE 19 OF 20 STORES CHARGED $0.
--
-- Measured on Luxelink 2026-08-10: 20 active stores, ONE row in pos.tax_codes (Lefferts, 8.875%).
-- GET /pos/tax-codes/store-grid resolved `effective_scope: 'none'` for the other 19 -- meaning a
-- taxable sale at any of them charges no tax at all -- and yet /core/onboarding/pos/status returned
-- complete: true, required 8/8. The tax step's own `why` text has always said "a taxable sale with
-- no tax code charges zero tax, and that is a number you cannot fix after the customer has left";
-- its PREDICATE said `count(pos.tax_codes) >= 1`. The text and the test did not agree, and the test
-- is what the wizard acts on.
--
-- The registry is per-tenant DATA (core.module_onboarding_task, migration 733) and the rows are
-- already seeded for all three tenants, so changing the shipped default in code reaches new tenants
-- ONLY. This migration repoints the existing rows at the new coverage predicate, which asks the
-- register's own question through the register's own resolver (store > market > org-wide).
--
-- CONDITIONAL ON PURPOSE. It only rewrites a row still carrying the exact shipped count predicate.
-- A tenant that has edited its own tax step keeps its edit -- the same never-clobber rule the tour
-- seed and seed_tenant_defaults follow.
--
-- EXPECTED EFFECT, stated so nobody reads it as a regression: Luxelink's POS wizard moves from
-- 8/8 required to 7/8, with the reason "19 of 20 stores have NO rate". That is the tenant's real
-- state becoming visible, not a new problem. The wizard nag is dismissable ("Continue later") and
-- no register is blocked by it.
--
-- Additive + idempotent + reversible: re-running changes nothing once applied, and the revert is the
-- commented statement at the foot of this file. Touches no rate, no payout, no paid/earned column --
-- it changes what the wizard REPORTS, never what anything charges.

begin;

update core.module_onboarding_task
   set predicate = '{"type": "coverage", "check": "pos_tax_rate"}'::jsonb
 where module_key = 'pos'
   and task_key   = 'tax_codes'
   and predicate  = '{"min": 1, "type": "count", "table": "tax_codes", "schema": "pos"}'::jsonb;

update core.module_onboarding_task
   set why = 'A taxable sale with no tax code charges zero tax, and that is a number you cannot fix '
             'after the customer has left. Every store needs a rate that reaches it — its own, its '
             'market''s, or a company default.'
 where module_key = 'pos'
   and task_key   = 'tax_codes'
   and why like 'A taxable sale with no tax code charges zero tax%One rate per state/store is enough to start.';

commit;

-- REVERT (paste and run to undo):
-- update core.module_onboarding_task
--    set predicate = '{"min": 1, "type": "count", "table": "tax_codes", "schema": "pos"}'::jsonb
--  where module_key = 'pos' and task_key = 'tax_codes'
--    and predicate = '{"type": "coverage", "check": "pos_tax_rate"}'::jsonb;
