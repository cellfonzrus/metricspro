-- 1105_portal_block_marker_kind.sql
--
-- ONE VOCABULARY FOR "WHAT IS THIS PAGE TELLING US INSTEAD OF SHOWING A FORM" (index §COMMCALC-50).
--
-- WHY. `commcalc.portal_block_marker` (mig 244) already held the phrases that mean "this portal is
-- throttling us", per-org and overridable — config, not code. But a throttle is only ONE of the
-- things a portal can answer with, and the other two were decided in CODE: `vidapay_sweep`
-- carried a hardcoded `_BOT_PHRASES` tuple and a hardcoded Cloudflare check. Three vocabularies for
-- one question, two of them unreachable by a tenant.
--
-- The owner's UPS billing login on 2026-10-30 is what that costs. UPS answered "Something went
-- wrong, or you are trying to perform an action that is not allowed" — a plain refusal, carrying its
-- own error reference. No tuple held that wording, so the driver reported "Could not find the login
-- form. The login form may render differently than expected — check the screenshot", and the
-- screenshot showed no form at all.
--
-- So the one table carries all three kinds, and a tenant whose portal uses different words adds a
-- ROW rather than needing a code change (RULE TWO).
--
-- ADDITIVE AND IDEMPOTENT. The column defaults to 'throttle', so EVERY row seeded by mig 244 keeps
-- exactly the meaning it has today and every reader written before this migration keeps the rows it
-- always got. Nothing is deleted and nothing is re-pointed.
--
-- NOT MONEY-TOUCHING. No rate, tier, plan rule, payout or calculation input is read or written here;
-- these rows only decide how a portal's answer is READ and what the operator is told about it.
--
-- -- REVERT: alter table commcalc.portal_block_marker drop column if exists kind;
--            (the seeded rows below are then orphaned but harmless — delete them with
--             `delete from commcalc.portal_block_marker where kind is not null;` BEFORE dropping.)

alter table commcalc.portal_block_marker
  add column if not exists kind text not null default 'throttle';

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'portal_block_marker_kind_chk') then
    alter table commcalc.portal_block_marker
      add constraint portal_block_marker_kind_chk
      check (kind in ('throttle', 'refusal', 'challenge'));
  end if;
end $$;

comment on column commcalc.portal_block_marker.kind is
  'What a page carrying this phrase is doing: throttle (come back later — sets a cooldown), refusal '
  '(you may not do that — no cooldown, it will say the same next time), challenge (prove you are '
  'human — a wait, a proxy or a person may clear it). Defaults to throttle, which is what every row '
  'seeded by migration 244 means.';

-- The unique index from mig 244 is (org_id, coalesce(processor,''), lower(marker)) and stays correct:
-- one phrase cannot sensibly mean two different things for one tenant and processor.

-- Seed the house defaults for the two new kinds. ON CONFLICT DO NOTHING ⇒ a re-run never resurrects
-- a marker an operator disabled and never overwrites their notes. NARROW on purpose: these decide
-- that a page is not worth a login attempt, so a false positive reads as "refused" on a portal that
-- is perfectly healthy.
insert into commcalc.portal_block_marker (org_id, processor, marker, kind, enabled, notes) values
  ('00000000-0000-0000-0000-000000000001', null, 'action that is not allowed',          'refusal',   true,
   'The exact UPS billing wording in the owner''s 2026-10-30 report.'),
  ('00000000-0000-0000-0000-000000000001', null, 'access to this page has been denied', 'refusal',   true, null),
  ('00000000-0000-0000-0000-000000000001', null, 'access denied',                       'refusal',   true, null),
  ('00000000-0000-0000-0000-000000000001', null, 'request unsuccessful',                'refusal',   true, null),
  ('00000000-0000-0000-0000-000000000001', null, 'not authorized to view this page',    'refusal',   true, null),
  ('00000000-0000-0000-0000-000000000001', null, 'something doesn''t look right',       'refusal',   true,
   'Moved here from the hardcoded tuple in vidapay_sweep.'),
  ('00000000-0000-0000-0000-000000000001', null, 'doesn''t look right',                 'refusal',   true,
   'Moved here from the hardcoded tuple in vidapay_sweep.'),
  ('00000000-0000-0000-0000-000000000001', null, 'unusual activity',                    'refusal',   true,
   'Moved here from the hardcoded tuple in vidapay_sweep.'),
  ('00000000-0000-0000-0000-000000000001', null, 'verify you are human',                'challenge', true,
   'Moved here from the hardcoded tuple in vidapay_sweep.'),
  ('00000000-0000-0000-0000-000000000001', null, 'are you a robot',                     'challenge', true,
   'Moved here from the hardcoded tuple in vidapay_sweep.'),
  ('00000000-0000-0000-0000-000000000001', null, 'attention required',                  'challenge', true,
   'Moved here from the hardcoded tuple in vidapay_sweep.'),
  ('00000000-0000-0000-0000-000000000001', null, 'just a moment',                       'challenge', true,
   'Moved here from the hardcoded Cloudflare check in vidapay_sweep.'),
  ('00000000-0000-0000-0000-000000000001', null, 'cf-chl',                              'challenge', true,
   'Moved here from the hardcoded Cloudflare check in vidapay_sweep.'),
  ('00000000-0000-0000-0000-000000000001', null, 'challenge-platform',                  'challenge', true,
   'Moved here from the hardcoded Cloudflare check in vidapay_sweep.'),
  ('00000000-0000-0000-0000-000000000001', null, 'checking your browser',               'challenge', true, null),
  -- moved out of the inline WAF tuple in vidapay_sweep.begin_login_b2bsoft, which was OR'd with the
  -- one home and so honoured no tenant's configuration
  ('00000000-0000-0000-0000-000000000001', null, 'request is blocked',                  'refusal',   true,
   'Moved here from the inline WAF tuple in vidapay_sweep.'),
  ('00000000-0000-0000-0000-000000000001', null, 'forbidden',                           'refusal',   true,
   'Moved here from the inline WAF tuple in vidapay_sweep.'),
  ('00000000-0000-0000-0000-000000000001', null, 'unusual traffic',                     'challenge', true,
   'Moved here from the inline WAF tuple in vidapay_sweep.'),
  ('00000000-0000-0000-0000-000000000001', null, 'are you a human',                     'challenge', true,
   'Moved here from the inline WAF tuple in vidapay_sweep.'),
  ('00000000-0000-0000-0000-000000000001', null, 'captcha',                             'challenge', true,
   'Moved here from the inline WAF tuple in vidapay_sweep. NOT the same question as the captcha '
   'WIDGET detector (index COMMCALC-11), which asks whether a human must tick a box on a form that '
   'IS there; this asks whether a form is there at all.')
on conflict do nothing;

-- A migration grants what it creates. The column is new on an existing table, so the table's own
-- grant already covers it; restated here because the house rule is that a migration never assumes
-- another file granted what this one needs.
grant select, insert, update, delete on commcalc.portal_block_marker to service_role;
