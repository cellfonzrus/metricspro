-- 712_core_invite_delivery.sql — invite/access-code DELIVERY tracking + RESEND accounting.
--
-- WHY: the account-link invite flow (mig 707) only ever RETURNED the access code to the inviting admin's
-- UI — it never emailed the invitee (root cause: rajiv.jaggi@… never received their code). Auth-hardening
-- now emails the code via Resend on create + resend. These columns record the delivery attempt + outcome
-- so the admin sees a visible "delivery failed" state, and cap resends (rate-limit accounting).
--
-- SAFE: additive + idempotent, no existing column altered. Until this runs, the email is still SENT
-- (best-effort), the outcome just can't be persisted on the invite row (the create/resend response still
-- carries it, and _audit_auth_event records the attempt) — nothing breaks.

alter table core.account_link_invite add column if not exists delivery_channel text;    -- 'email' | 'whatsapp'
alter table core.account_link_invite add column if not exists delivery_status  text;    -- 'sent' | 'failed' | NULL(not attempted)
alter table core.account_link_invite add column if not exists delivery_error   text;    -- provider error (truncated), when failed
alter table core.account_link_invite add column if not exists delivered_at     timestamptz;
alter table core.account_link_invite add column if not exists resent_count     int not null default 0;
alter table core.account_link_invite add column if not exists last_sent_at     timestamptz;

notify pgrst, 'reload schema';
select '712 complete — core.account_link_invite delivery + resend accounting columns' as status;
