-- 952_finance_entity_scope_cleanup.sql
-- Owner report 2026-09-04: "cash flow analysis in cellfonz r us has other companies like nova wave,
-- and luxelink in the drop down menu along with the T stores, need to fix this as a system not a
-- band aid."
--
-- EVIDENCE (live reads 2026-09-04, full row snapshots preserved in the incident evidence JSON):
--   • commcalc.companies carried two LuxeLink-tenant entities STORED UNDER THE HOUSE ORG
--     (00000000-0000-0000-0000-000000000001), created as a batch within 30 seconds on 2026-06-27 —
--     before the LuxeLink tenant (854f6d7b-6590-4e4d-88ab-646f560d4f4c) got its own proper rows
--     ("Nova Wave Communications" 2026-07-01, "Luxlink Wireless" 2026-06-28):
--       9b22c0d8-efc9-4bc7-aef6-94480b7f9ea0  'Novawave Communications LLC'  (created 2026-06-27)
--       b5993b9d-4edf-422c-b69c-fee7de484897  'Luxelink Wireless LLC'        (created 2026-06-27)
--     PROVEN PHANTOM: zero store_companies assignments, zero journal_entries references (any org),
--     and every derived snapshot for their scopes is all-zero (P&L rev/NI 0, BS 0/0/0, CF 0).
--     statement_engine._scopes builds one company:<id> scope per companies row, so these two rows
--     alone put "Novawave"/"Luxelink" into every cellfonz scope dropdown (Cash Flow, P&L, BS,
--     dashboard) for July/August/September 2026.
--   • The house July 2026 snapshot set still carried scope 'store:4640-A W Diversey Ave' — a
--     LuxeLink store — computed 2026-08-20 from the six mis-filed raw_sales rows of the 2026-07-14
--     cross-tenant ingest incident (§19.15). Those source rows were removed on 2026-09-03, but the
--     snapshot predates the cleanup: the stale scope still lists in the dropdown and its $29.99
--     leaked revenue is still inside the stored July consolidated P&L.
--
-- WHAT THIS DOES (deletes are pinned BY ID to the proven-leaked rows — nothing pattern-based):
--   1. removes the 2 phantom company rows from the house org;
--   2. removes their 16 all-zero account_statements scope rows (pl/balance_sheet/cash_flow,
--      July/August/September 2026) and the 2 stale Diversey scope rows;
--   3. NOTE, not done here: recompute 'July 2026' (POST /account/compute/July%202026, or the
--      mig-940 run-due sweep) to purge the $29.99 Diversey remnant from the stored July
--      consolidated P&L — statement_engine.compute_and_store purge-then-inserts the period, and
--      with the companies rows gone the phantom scopes cannot regenerate. August/September need no
--      recompute (phantom scopes were all-zero; consolidated is unaffected).
--
-- The SYSTEMIC half ships in the same PR: coa.org_companies is now the ONE canonical, fail-closed
-- entity enumeration (CI-pinned by harness_org_scope_guard.py), and /account/overview +
-- analysis.assemble drop any company:<id> scope not in the org's own inventory
-- (coa.filter_org_scopes, proof harness_finance_entity_enumeration.py).
--
-- Idempotent: re-running deletes nothing once the rows are gone.
-- -- REVERT: restore the deleted rows from the incident evidence JSON
--            (scratchpad leak_evidence_2026-09-04.json — full column snapshots of all 20 rows);
--            there is no schema change to revert.

BEGIN;

-- 2. derived snapshots first (children of the entity rows in spirit, no FK either way)
DELETE FROM commcalc.account_statements
WHERE org_id = '00000000-0000-0000-0000-000000000001'
  AND scope_key IN ('company:9b22c0d8-efc9-4bc7-aef6-94480b7f9ea0',
                    'company:b5993b9d-4edf-422c-b69c-fee7de484897');

DELETE FROM commcalc.account_statements
WHERE org_id = '00000000-0000-0000-0000-000000000001'
  AND scope_key = 'store:4640-A W Diversey Ave';

-- 1. the poisoned entity rows themselves (defensive belt: only if still unreferenced)
DELETE FROM commcalc.companies c
WHERE c.org_id = '00000000-0000-0000-0000-000000000001'
  AND c.id IN ('9b22c0d8-efc9-4bc7-aef6-94480b7f9ea0',
               'b5993b9d-4edf-422c-b69c-fee7de484897')
  AND NOT EXISTS (SELECT 1 FROM commcalc.store_companies sc WHERE sc.company_id = c.id)
  AND NOT EXISTS (SELECT 1 FROM commcalc.journal_entries je WHERE je.company_id = c.id);

COMMIT;
