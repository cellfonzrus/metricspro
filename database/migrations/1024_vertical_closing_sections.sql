-- 1024_vertical_closing_sections.sql — which parts of the DAILY CLOSING FORM a tenant VERTICAL does not use.
-- Band 1000+ (platform). Additive + idempotent. Depends on 1020 (core.tenant_vertical). NOT money-touching: it
-- decides which INPUT boxes a rep is shown; no calculation, recon rule or stored value changes (a hidden box
-- submits empty, exactly as a rep leaving it blank does today).
--
-- OWNER (2026-09-25): "Cash and credit needs to reconciled by the reps declaring the actual cash at the end of
-- the day … most of this is already made but might need some tweaking."
--
-- THE CLASS: the closing form's tenders and transaction counts were already per-tenant CONFIG (tender_config
-- mig 111, count_config mig 501), but four wireless inputs were hard-wired into the form and its fallbacks:
-- the "Accessory Sale $" box, the "Bill Payments, already included above" section (processor on cash / credit
-- / financing), the three built-in activation counts (Upgrades / New Lines / Postpaid) used when a tenant has
-- configured none, and the financing (lease) tender in the built-in tender fallback. A franchise store's rep
-- was asked for all four. They become DATA on the vertical (one more column on the 1020 vocabulary) and the
-- form reads them from /core/me → tenant.vertical.closing_hidden. Section keys (the form's own vocabulary):
--   acc_sale · bill_payments · activation_counts · tender:<built-in tender key>
-- Mirrored byte-equal in core/verticals.HOUSE_VERTICALS[*].closing_hidden (harness_tenant_vertical §A).

ALTER TABLE core.tenant_vertical ADD COLUMN IF NOT EXISTS closing_hidden TEXT[] NOT NULL DEFAULT '{}';

UPDATE core.tenant_vertical SET closing_hidden = '{acc_sale,bill_payments,activation_counts,tender:acima}'
 WHERE key = 'ups_store' AND closing_hidden = '{}';

SELECT 'Migration 1024 complete — core.tenant_vertical.closing_hidden (franchise vertical hides 4 wireless closing inputs)' AS status;

-- REVERT:
--   ALTER TABLE core.tenant_vertical DROP COLUMN IF EXISTS closing_hidden;
