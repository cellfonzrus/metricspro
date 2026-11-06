-- 1114_portal_block_marker_signed_in.sql
--
-- A FOURTH KIND ON THE ONE PORTAL VOCABULARY: "THESE WORDS MEAN WE ARE INSIDE" (index §COMMCALC-55).
--
-- WHY. Migration 1105 gave `commcalc.portal_block_marker` a `kind`, so that the three answers a
-- portal can give INSTEAD of a sign-in form — throttle, refusal, challenge — share one table, one
-- inheritance rule and one reader, and a tenant whose portal uses different words adds a ROW rather
-- than needing code (RULE TWO).
--
-- The opposite question had no such home. "Which words on this page mean the login WORKED" was
-- decided in code: §COMMCALC-52 rightly deleted the old test (the vendor's name in the document
-- title — a carrier name in code) but replaced it with a tuple of six house words, which is the
-- platform guessing at a vocabulary it does not own.
--
-- The owner's own Boost owners-portal login on 2026-11-05 is what that costs. The sign-in SUCCEEDED
-- — his screenshot shows the signed-in shell, greeting him by his e-mail address, offering
-- "Multi-Door Owner Portal", "Commissions" and "Boost Activations" — and the page carries not one
-- of the six house words. So the driver reported "The sign-in page went away and we did not
-- recognise what replaced it", and three carrier feeds stayed frozen on a login that was working.
--
-- The general fix is evidence, not vocabulary, and ships in the same change: a page that greets you
-- by the account name you signed in WITH cannot be an anonymous page, which recognises that portal
-- and most others with nothing declared at all. This kind is for the portal that neither greets you
-- nor uses the house words — so that one is a row, and never another code change.
--
-- NOT A VERDICT. The other three kinds each carry an operator sentence ("the portal refused us",
-- "route this via a proxy", "it is rate-limiting us"). This one is not a failure and has no such
-- sentence; the code keeps it out of `portal_backoff.VERDICT_KINDS` for exactly that reason, and
-- lists it only in `MARKER_KINDS`.
--
-- ADDITIVE AND IDEMPOTENT. Only the check constraint widens; no existing row changes meaning, no
-- row is deleted and no row is re-pointed. A reader written before this migration asks for one of
-- the three older kinds and keeps getting exactly the rows it always got.
--
-- NOTHING IS SEEDED. The house signed-in words have ONE home already —
-- `login_outcome.DEFAULT_SIGNED_IN_MARKERS` — and `portal_backoff._signed_in_seeds()` dereferences
-- it. Seeding the same six phrases as rows here would be the second copy that diverges, which is
-- the defect the house rule names. This migration opens the door; a tenant walks through it only
-- when its portal needs words of its own.
--
-- NOT MONEY-TOUCHING. No rate, tier, plan rule, payout or calculation input is read or written here;
-- these rows only decide how a portal's answer is READ and what the operator is told about it.
--
-- -- REVERT: delete from commcalc.portal_block_marker where kind = 'signed_in';
--            alter table commcalc.portal_block_marker drop constraint if exists portal_block_marker_kind_chk;
--            alter table commcalc.portal_block_marker
--              add constraint portal_block_marker_kind_chk
--              check (kind in ('throttle', 'refusal', 'challenge'));
--            (the delete must run FIRST, or the narrower constraint cannot be re-added.)

alter table commcalc.portal_block_marker
  drop constraint if exists portal_block_marker_kind_chk;

alter table commcalc.portal_block_marker
  add constraint portal_block_marker_kind_chk
  check (kind in ('throttle', 'refusal', 'challenge', 'signed_in'));

comment on column commcalc.portal_block_marker.kind is
  'What a page carrying this phrase is doing: throttle (come back later — sets a cooldown), refusal '
  '(you may not do that — no cooldown, it will say the same next time), challenge (prove you are '
  'human — a wait, a proxy or a person may clear it), signed_in (NOT a verdict: these words mean the '
  'login worked and we are inside the application). Defaults to throttle, which is what every row '
  'seeded by migration 244 means.';

-- The unique index from mig 244 is (org_id, coalesce(processor,''), lower(marker)) and stays correct:
-- one phrase cannot sensibly mean two different things for one tenant and processor. That it also
-- forbids declaring the SAME phrase as both a refusal and a signed-in marker is the right answer —
-- a phrase that means both is a phrase that means neither.

-- A migration grants what it creates. Nothing is created here, so the table's own grant already
-- covers it; restated because the house rule is that a migration never assumes another file granted
-- what this one needs.
grant select, insert, update, delete on commcalc.portal_block_marker to service_role;
