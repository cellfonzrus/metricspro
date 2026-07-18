-- 223_rollback_commission_installment_gate_source.sql — REVERSAL LAYER L1 for mig 223.
--
-- WHY: owner-mandated reversal mechanism ("create a reversal mechanism if sql 223 does something to the
-- boost platform"). This drops the config-override table created by mig 223. It is one of FOUR reversal
-- layers; run this ONLY to undo the CONFIG-OVERRIDE surface. It is NOT a full behavior rollback.
--
-- WHAT THIS DOES vs DOES NOT DO:
--   • DROPS commcalc.installment_gate_source_config → removes every per-carrier override AND the two house
--     mode-default seed rows.
--   • The engine (sale_installment_engine._resolve_gate_cfg) then falls back to its per-mode CODE DEFAULTS:
--       boost mode → 'boost_mi'      (the raw_mi gate — EXACTLY today's Boost behavior, unchanged) ;
--       plan  mode → 'ma_commission' (the master-agent gate — STILL ACTIVE).
--     => Dropping this table does NOT restore pre-mig-223 pay for master-agent tenants; it only removes the
--        ability to OVERRIDE the source per carrier. Boost is unaffected either way.
--   • To instantly restore the EXACT pre-mig-223 behavior for ALL orgs without a redeploy, use the L2 KILL
--     SWITCH instead: set the Railway env var INSTALLMENT_GATE_LEGACY=1 (forces the legacy raw_mi gate for
--     every org/mode) and restart. Full code reversal = L4 (git revert of the Fix B commit).
--
-- SAFE: idempotent (IF EXISTS). Re-runnable. No other object depends on this table.

DROP TABLE IF EXISTS commcalc.installment_gate_source_config CASCADE;

NOTIFY pgrst, 'reload schema';
SELECT 'Rollback of mig 223 complete — installment_gate_source_config dropped. '
       'Engine now uses per-mode CODE DEFAULTS (boost→raw_mi unchanged; plan→raw_ma_commission still active). '
       'For a FULL pre-mig-223 restore use L2 kill switch INSTALLMENT_GATE_LEGACY=1 (no redeploy) or L4 git revert.'
       AS status;
