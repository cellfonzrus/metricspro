-- 1110_device_condition_tokens.sql — A PRE-OWNED DEVICE IS A DIFFERENT DEVICE (index §POS-18)
--
-- OWNER RULING 2026-10-31, verbatim: *"cpo is certified pre owned is adifferent device than new  ,
-- imnei is unique"* — answering whether a refurbished handset and a new one of the same model
-- should share a row on the accessory order grid. They must not.
--
-- WHAT THIS DOES
-- ─────────────────────────────────────────────────────────────────────────────────────────────────
--   1. ONE COLUMN on `commcalc.accessory_config`, the org's existing accessory-vocabulary row,
--      beside `device_colour_tokens` from mig 1109: the WORDS this org's own feed marks a device's
--      condition with. No new table, no sibling config row.
--   2. A SEED of those words for the one org whose feed actually marks a condition, measured from
--      that org's own live feed (below).
--
-- WHY A DECLARATION AND NOT A HOUSE LIST. The CONDITIONS themselves are the platform's policy and
-- have one home in code (`pos/device_condition.HOUSE_CONDITIONS`, the same four this schema has
-- allowed on `pos.inventory_serial` since mig 725). The WORDS a feed marks one with are the
-- tenant's own vocabulary — `CPO` in one feed, `CERTIFIED_PREOWNED` in another — and CLAUDE.md's
-- "every feature is for EVERY tenant" directive bans a house default that is really one tenant's
-- vocabulary. So an org that has declared none identifies NO condition and the screen says so,
-- rather than quietly reading a refurbished handset as new.
--
-- WHAT WAS MEASURED, BEFORE THE SEED WAS WRITTEN (live feed, 1 Aug – 31 Oct 2026, each org's own
-- declared device departments):
--
--   Luxelink Wireless LLC   191 device lines carry `CPO`; its CPO rows also carry the glued SKU
--                           form `CERTIFIED_PREOWNED_…` / `CERTIFIED_IPHONE_…`
--   Cellfonz R Us             0 device lines carry any condition word
--   Vzone                     0 device lines at all in the window
--
-- So only Luxelink is seeded. The other two are left NULL deliberately: seeding them with words
-- their feeds do not use would declare a vocabulary nobody measured, and the grid would read every
-- device as new with the same confidence either way. Their accessory grid is byte-identical to what
-- it is today until somebody declares a word for them.
--
-- NO MONEY MOVES. The column is NULL on arrival for every org but one, and the only reader plans
-- QUANTITIES TO BUY: no payout, no ledger, no P&L line, no commission and no tax figure is touched.
--
-- IDEMPOTENT. `add column if not exists`, and each seed is guarded on the column still being NULL,
-- so a second run changes nothing.
--
-- REVERT: alter table commcalc.accessory_config drop column if exists device_condition_tokens;
--   (dropping it returns every org to "no condition declared", which is the pre-1110 behaviour —
--    one row per spelling, no condition shown. Nothing else reads the column.)

alter table commcalc.accessory_config
  add column if not exists device_condition_tokens jsonb;

comment on column commcalc.accessory_config.device_condition_tokens is
  'The words THIS org''s product descriptions mark a device condition with (owner 2026-10-31). A '
  'flat list means "these mark the non-new condition"; an object keyed by condition key '
  '({"refurbished": ["cpo"]}) marks more than one. The condition KEYS are the platform''s '
  '(pos/device_condition.HOUSE_CONDITIONS, = mig 725''s CHECK on pos.inventory_serial); the WORDS '
  'are the tenant''s. NULL means no condition is identified for this org and the surface says so '
  '— never a silent fall back to another tenant''s words.';

-- ── THE SEED (measured above; guarded so a re-run and a later edit both survive) ─────────────────
update commcalc.accessory_config
   set device_condition_tokens = '{"refurbished": ["CPO", "CERTIFIED_PREOWNED", "CERTIFIED", "Certified Pre-Owned", "Pre-Owned", "Refurbished", "Refurb"]}'::jsonb
 where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
   and device_condition_tokens is null;

-- `accessory_config` is reached through PostgREST like every other relation here, and this file
-- creates no new relation — the grants on the table itself (mig 1104) already cover the column.
-- Stated rather than omitted, because "a migration grants what it creates" is a house rule and a
-- reader should see it was considered.

-- VERIFY (safe, read-only — expect one row per org, Luxelink's holding the seeded object):
--   select org_id, device_condition_tokens from commcalc.accessory_config order by org_id;
