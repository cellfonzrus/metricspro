-- 1094_payable_recovery_sign_and_basis.sql
-- WHAT: two declaration columns for "what has the carrier already recovered against this device" —
--         commcalc.payable_source_map.reimbursement_sign : whether a recovery arrives NEGATIVE (a
--           deduction on the carrier's own statement) or POSITIVE (a reimbursement paid to the
--           dealer). Nullable; NULL keeps the historic meaning, positive.
--         commcalc.device_payable_ledger.rebate_basis    : what MEASURED the recovery on this row,
--           so a reader can ask "do we know?" instead of reading 0.00 as a measurement.
-- WHY : owner report 2026-10-26, verbatim: "the payments for all phones automatically are done from
--        commission and rebates received and auto adjusted" — said against a banner reading
--        "2656 unsold devices must be paid OUT OF POCKET - $544,447.78 ... funded from your own
--        cash". The netting was never missing: commcalc.raw_ma_commission.rebate is NEGATIVE on
--        3,442 of 4,495 Luxelink lines, -$834,234.25 in total. The engine's imei_match branch wrote
--        `rebate_amount = 0.0` as a placeholder, which is the sentence "nothing was recovered" —
--        so 1,400 lines became `discrepancy` (the dealer is carrying the cost) and the exposure
--        headlined the gross. Measured 2026-10-26 on the same 2,656 devices: 1,070 ($228,779.70)
--        fully deducted, 957 partly (owed $305,380.43 vs $277,294.00 deducted), 37 ($10,287.65)
--        with no deduction — roughly $38,374 genuinely unrecovered, not $544,447.78.
--        RULE TWO: the sign is a per-org declaration, so no carrier's convention enters code.
--        Index §COMMCALC-33.
-- SAFE : additive and nullable. Applying it moves no money and recomputes nothing — the ledger
--        re-statuses only on the next Rebuild ledger press, which the owner makes. With
--        reimbursement_sign left NULL and no reimbursement_field declared, every carrier behaves
--        exactly as before except that an unmeasured recovery is now stored as NULL and FLAGGED
--        rather than written as 0.00.
-- REVERT: alter table commcalc.payable_source_map  drop column if exists reimbursement_sign;
--         alter table commcalc.device_payable_ledger drop column if exists rebate_basis;

alter table commcalc.payable_source_map
  add column if not exists reimbursement_sign text;

alter table commcalc.device_payable_ledger
  add column if not exists rebate_basis text;

COMMENT ON COLUMN commcalc.payable_source_map.reimbursement_sign IS
  'How a recovery against a device appears on this carrier''s feed. '
  '''negative_is_recovery'': it is a DEDUCTION and arrives negative (the MA commission feed''s '
  '''rebate'' column) - the magnitude is the amount recovered. '
  '''positive_is_recovery'' (the default, and what NULL means): it is a reimbursement paid to the '
  'dealer and arrives positive (Boost/VIP, ePay). The sign is DECLARED and never inferred: taking '
  'abs() would turn a charge into a credit, and keeping only positives reads a deduction as '
  'nothing, which is how $834,234.25 of netting went unseen until 2026-10-26.';

COMMENT ON COLUMN commcalc.device_payable_ledger.rebate_basis IS
  'What measured the recovery on this row - one of declared_field / asset_ledger / epay (a real '
  'measurement), or unmeasured / source_unreadable (not one). A reader must consult this before '
  'treating rebate_amount as a fact: the allowlist lives in '
  'backend/app/modules/payables/recovered_amount.py (MEASURED_BASES, is_measured) and a basis it '
  'has not ruled on reads as NOT measured. NULL rebate_amount with an unmeasured basis means '
  '"nobody checked", which is why the exposure figure is flagged as not netted rather than '
  'presented as a shortfall.';

-- A migration grants what it creates. No new relation here, but both grants are restated so a
-- fresh environment applying files out of order cannot end up with a table the backend cannot reach.
grant select, insert, update, delete on commcalc.payable_source_map to service_role;
grant select, insert, update, delete on commcalc.device_payable_ledger to service_role;
