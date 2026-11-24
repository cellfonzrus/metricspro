-- 1130_epay_digest_to_inbox.sql
-- THE ePay DISCREPANCY DIGEST TAKES THE INBOX ROUTE.
--
-- Owner, 2026-11-24, on the card "Which daily alerts should move into the inbox?": chose
-- *"All 5 plus ePay discrepancy"*.
--
-- The five were company-wide alerts through `closing.router._send_alert`, which migration 1129
-- already routed (`connector`, `upload_due`). ePay discrepancy is NOT one of them: it is an
-- ADDRESSED per-manager digest through `notify/digest_delivery.deliver_digests`, which sent a
-- manager their own stores' gaps every hour the sweep found one — 873 sends over the 21 days to
-- 2026-11-24, about 40 a day.
--
-- So this is ONE row, and the mechanism it switches on is shared: `deliver_digests` is the one home
-- for every addressed digest (ePay, zero sales, bill-pay declarations, manager follow-ups, store
-- visits), and it now reads the SAME `storeops.alert_route` that 1129 created. Every one of those
-- scopes therefore becomes one row rather than one more code path — which is why no new table and
-- no new column appear here (CLAUDE.md: extend the mechanism, never add a sibling).
--
-- WHAT THE COMPANY SEES INSTEAD OF THE MAIL: one inbox item per company, titled with the number of
-- open findings and listing each as "<store> <store-day> <kind>". It is updated in place while they
-- stay open, and closes itself when the sweep reports none. A finding two managers were both emailed
-- about is one line, because the item answers for the company, not for a recipient.
--
-- Index §CORE-49. Money-touching: NO (no ledger, no rate, no amount is read or written).
-- Additive and idempotent: it inserts one declaration row and alters nothing.

INSERT INTO storeops.alert_route (org_id, scope, route)
VALUES ('00000000-0000-0000-0000-000000000001', 'epay_discrepancy', 'inbox')
ON CONFLICT (org_id, scope) DO UPDATE SET route = EXCLUDED.route;

NOTIFY pgrst, 'reload schema';

-- REVERT:
--   DELETE FROM storeops.alert_route
--    WHERE org_id = '00000000-0000-0000-0000-000000000001' AND scope = 'epay_discrepancy';
--   (With no row the scope reads `message`, which is the pre-1130 behaviour exactly: the
--    per-manager ePay emails resume on the next sweep. No other table is touched. A tenant that
--    declared its own `epay_discrepancy` row keeps it — this reverts the HOUSE default only.)
