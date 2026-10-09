-- 1066 — THE COMMISSION-LEG ROLLUP STOPS DECIDING STORE IDENTITY (index §64, owner directive
-- 2026-10-09: "chase trhew street number matching")
--
-- WHAT WAS WRONG. `commcalc.commission_leg_label_rollup` (mig 274) returned, as its store column,
-- `split_part(btrim(business_address), ' ', 1)` — the FIRST SPACE-SEPARATED TOKEN of the carrier's
-- address. A leading address token is not a store identity: the carrier writes
-- "116-36 Springfield Blvd …" where the roster writes "11636 Springfield Blvd", so that store's
-- money matched no store row at all; and where two store rows lead with one token the join picked
-- an arbitrary winner. The report that consumed this (the GP commission-leg trend / breakout) could
-- not select those rows, and the Gross Profit table lost the money outright.
--
-- WHAT THIS CHANGES. The function now returns the BTRIMMED RAW ADDRESS the feed carried, and the
-- backend resolves it through the ONE home for store identity
-- (`backend/app/modules/account/store_identity.py`, via `account.coa.store_resolver`: exact address
-- → alias → store_code → squashed spelling → UNAMBIGUOUS leading street number of an address or an
-- alias → nothing). SQL stops stating a resolution rule; the rule stays in one place, per org, as
-- config rows (RULE TWO).
--
-- SAFE IN EITHER ORDER, and this is deliberate. The backend already passes whatever this column
-- carries through that resolver, and the resolver places a bare token too (its number step), so:
--   • code merged, migration NOT yet applied  → tokens still arrive and still resolve;
--   • migration applied, code not yet deployed → never happens (this migration ships with it);
--   • both                                    → the exact address resolves exactly.
-- Nothing about this is a recompute: NO stored payout, snapshot or ledger row is read or written
-- here, and no money is moved by applying it. It changes only the GRAIN of a read-only aggregate.
--
-- Idempotent (CREATE OR REPLACE) and additive — the signature and the column names are unchanged,
-- so no caller breaks.
--
-- REVERT: re-run mig 274's definition of commcalc.commission_leg_label_rollup (the body there is
-- identical to this one apart from the two `split_part(…, ' ', 1)` wrappers).

BEGIN;

CREATE OR REPLACE FUNCTION commcalc.commission_leg_label_rollup(p_org_id uuid, p_periods text[])
RETURNS TABLE (source text, period text, store_num text, label text, category text,
               amount numeric, n bigint)
LANGUAGE sql STABLE AS $$
  SELECT 'payment_detail'::text,
         pd.period,
         btrim(coalesce(pd.business_address, '')),
         btrim(coalesce(pd.payment_type, '')),
         coalesce(pc.category, 'Unknown'),
         sum(coalesce(pd.amount, 0)),
         count(*)
    FROM commcalc.raw_payment_detail pd
    LEFT JOIN commcalc.payment_categories pc
           ON pc.org_id = pd.org_id
          AND btrim(pc.description) = btrim(pd.payment_type)
   WHERE pd.org_id = p_org_id
     AND pd.period = ANY (p_periods)
   GROUP BY 2, 3, 4, 5
  UNION ALL
  SELECT 'comp_report'::text,
         cr.period,
         btrim(coalesce(cr.business_address, '')),
         btrim(coalesce(cr.compensation_type, '')),
         ''::text,
         sum(coalesce(cr.payment_amount, 0)),
         count(*)
    FROM commcalc.raw_comp_report cr
   WHERE cr.org_id = p_org_id
     AND cr.period = ANY (p_periods)
   GROUP BY 2, 3, 4;
$$;

COMMENT ON FUNCTION commcalc.commission_leg_label_rollup(uuid, text[]) IS
  'Received-commission rollup for the GP commission-leg split/trend: (source, period, RAW store address as the feed wrote it, raw label, mapped payment category) -> summed amount. Store IDENTITY is NOT decided here: the backend resolves that address through account.store_identity (the one home). The leg (Month N) lives in the label and is classified from commission_leg_config, so neither rule is SQL.';

COMMIT;
