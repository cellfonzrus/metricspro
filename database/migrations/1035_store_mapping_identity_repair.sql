-- 1035_store_mapping_identity_repair.sql
--
-- STORE IDENTITY: one physical store, one canonical key.
--
-- Owner, 2026-10-02: B-1800's and B-1115's figures were each appearing under TWO keys.
--
-- ⚠️  WRITTEN, **NOT APPLIED**. This migration changes how sales / commission / P&L rows ATTRIBUTE
--     to a store, so it is surfaced for owner approval before it is run (CLAUDE.md: "money-touching
--     changes and migrations are still surfaced for owner approval before applying"). It moves no
--     money: no amount is recomputed here, no payout row is touched. It repairs three MAPPING rows
--     so that figures already in the database stop being counted under two store names.
--
-- ── THE CAUSE (the class, not the instance) ──────────────────────────────────────────────────────
-- `account.coa.store_resolver` (index §13) collapses every spelling of a store onto its canonical
-- ADDRESS. Its chain is: exact `store_mapping.store_address` → `store_aliases.alias` → the raw
-- string IS a `store_code` → unambiguous leading street number. Every step but the last can only
-- land on an address `commcalc.store_mapping` already carries. So a store whose mapping row has no
-- real address — or no mapping row at all — has NOTHING for its other spellings to collapse onto,
-- and its money reads as two stores. Nothing errors; the totals just split.
--
-- Three house rows were in that shape (proven, live, 2026-10-02 — see
-- backend/harness_store_mapping_identity.py and the audit module it exercises):
--
--   B-1800   store_address = 'B-1800'                  ← the CODE typed into the address box
--   B-1115   no store_mapping row at all               ← roster-only store
--   B-60TH   no store_mapping row at all               ← the SIBLING, found by the class sweep
--
-- `B-60TH` was NOT in the report. It is the identical class and is repaired in the same migration,
-- because fixing one instance and leaving its sibling is the patchwork the house rules forbid.
--
-- ── NOT REPAIRED HERE, AND WHY ───────────────────────────────────────────────────────────────────
-- Two more house rows carry a placeholder address whose REAL location is not anywhere in the
-- database: `B-2778` (PA) and `Cellular Services`. They are REPORTED by the audit and deliberately
-- left alone — inventing a street address to silence a finding would be the "write code that hides
-- it" the house rules forbid. They need the owner to say what those two addresses are.
--
-- ── THE LOCK ─────────────────────────────────────────────────────────────────────────────────────
-- `app/modules/account/store_identity_audit.py` is the ONE home for the invariant, and
-- `backend/harness_store_mapping_identity.py` (39 checks, DB-free, over the REAL resolver) fails the
-- build if a repair is undone or the resolver stops collapsing these spellings.
--
-- IDEMPOTENT: the UPDATE is a no-op once the address is right; both INSERTs are guarded by NOT
-- EXISTS on (org_id, upper(store_code)). ADDITIVE: no row is deleted, no column changes.
--
-- REVERT:
--   update commcalc.store_mapping set store_address = 'B-1800'
--    where org_id = '00000000-0000-0000-0000-000000000001' and store_code = 'B-1800';
--   delete from commcalc.store_mapping
--    where org_id = '00000000-0000-0000-0000-000000000001' and store_code in ('B-1115', 'B-60TH');

begin;

-- ══ 1) B-1800 — replace the placeholder with the real address ════════════════════════════════════
-- The address is not invented: `commcalc.store_aliases` already carries '1800 Great Neck rd' → B-1800,
-- and a second mapping row ('1800GreatNeckRd') already carries '1800 Great Neck Rd'. This row is
-- being brought into agreement with both. Afterwards '1800GreatNeckRd' is a harmless code-alias
-- pointing at the same canonical address rather than a rival key.
update commcalc.store_mapping
   set store_address = '1800 Great Neck Rd'
 where org_id = '00000000-0000-0000-0000-000000000001'
   and upper(btrim(store_code)) = 'B-1800'
   and btrim(coalesce(store_address, '')) <> '1800 Great Neck Rd';

-- ══ 2) B-1115 — give the roster-only store its mapping row ══════════════════════════════════════
-- Address taken verbatim from `storeops.stores` (B-1115, market LI), which is also the spelling the
-- confirmed alias in `commcalc.store_aliases` already uses. That alias is INERT today: the
-- alias→address step needs the code to be present in store_mapping. This row activates it.
insert into commcalc.store_mapping (org_id, store_code, store_address)
select '00000000-0000-0000-0000-000000000001', 'B-1115', '1115 Liberty Ave'
 where not exists (
   select 1 from commcalc.store_mapping
    where org_id = '00000000-0000-0000-0000-000000000001'
      and upper(btrim(store_code)) = 'B-1115');

-- ══ 3) B-60TH — the sibling, same class ═════════════════════════════════════════════════════════
-- `storeops.stores` has B-60TH at '1 S 60th St, Philadelphia'; `store_mapping` already holds that
-- same physical store under code `B-1` at '1 S 60th street'. The roster ADDRESS already collapses
-- onto B-1's spelling via the leading-street-number step, but the CODE 'B-60TH' resolves to itself.
-- Mapping the code to B-1's existing canonical address joins them; it introduces no new address.
insert into commcalc.store_mapping (org_id, store_code, store_address)
select '00000000-0000-0000-0000-000000000001', 'B-60TH', '1 S 60th street'
 where not exists (
   select 1 from commcalc.store_mapping
    where org_id = '00000000-0000-0000-0000-000000000001'
      and upper(btrim(store_code)) = 'B-60TH');

commit;

-- ══ VERIFY ══════════════════════════════════════════════════════════════════════════════════════
-- Expect exactly three rows, each with a real street address:
--
--   select store_code, store_address from commcalc.store_mapping
--    where org_id = '00000000-0000-0000-0000-000000000001'
--      and upper(btrim(store_code)) in ('B-1800', 'B-1115', 'B-60TH')
--    order by store_code;
--
-- Cause-class sweep — a mapping row whose address box holds nothing but its own code. Expect only
-- B-2778 and 'Cellular Services', the two awaiting their real addresses from the owner:
--
--   select org_id, store_code, store_address from commcalc.store_mapping
--    where btrim(coalesce(store_address, '')) = ''
--       or lower(btrim(regexp_replace(store_address, '[^a-zA-Z0-9]+', ' ', 'g')))
--        = lower(btrim(regexp_replace(store_code, '[^a-zA-Z0-9]+', ' ', 'g')))
--    order by org_id, store_code;
--
-- Cause-class sweep — a roster store with no mapping row. Expect ZERO rows after this migration:
--
--   select s.org_id, s.store_code, s.address from storeops.stores s
--    where nullif(btrim(s.store_code), '') is not null
--      and not exists (select 1 from commcalc.store_mapping m
--                       where m.org_id = s.org_id
--                         and upper(btrim(m.store_code)) = upper(btrim(s.store_code)))
--    order by s.org_id, s.store_code;
--
-- The full behavioural audit (all three classes, every org, through the REAL resolver) is
-- `app/modules/account/store_identity_audit.py::audit`.
