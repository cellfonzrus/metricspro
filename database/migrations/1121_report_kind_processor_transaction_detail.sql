-- 1121_report_kind_processor_transaction_detail.sql — THE PROCESSOR'S TRANSACTION DETAIL, as a report
-- a person can DELIVER rather than only one the platform can fetch (index §COMMCALC-75).
--
-- OWNER, verbatim 2026-11-14: "dont break their firewall but find a way to get the report". The
-- per-store back office (§COMMCALC-58) is fronted by a security appliance that refuses automated
-- clients outright — the browser pull is answered "The requested URL was rejected" at every address
-- tried, and the owner confirmed the back office offers no scheduled email or subscription of its own.
-- So the file must come to us: forwarded to the mailbox the email sweep already watches, uploaded by
-- hand, or delivered by the processor if they agree to it.
--
-- WHAT THIS ROW CHANGES. Nothing is fetched differently and no money moves. The five upload surfaces
-- read the report-kind registry (mig 1010) to decide what a tenant may be offered; this feed had no
-- row, so its `upload_types` route could not be PICKED — in the Email Auto-Import rule dropdown or on
-- the Upload page — even though the landing for it has existed since mig 903. The row is the
-- declaration that this report may arrive as a file. `epay_ingest.from_upload` is where it lands,
-- through the same ingest the pull uses.
--
-- RULE TWO: no vendor, carrier or portal is named. The key, label and what_in_it describe *a payment
-- processor's* transaction detail; `upload_types` carries the route key that feed has carried since
-- mig 903, which is an existing identifier in the schema, not a new vendor word.
--
-- DUPLICATE CHECK (build gate): searched the index for an existing mechanism for "this feed received
-- a file" — §COMMCALC-07 (portal_report_pull, the PULL transport), §COMMCALC-58 (the per-store back
-- office adapter), §16 (report_kind / report_signature mig 1010), §COMMCALC-73 (ingest_record, the one
-- journal writer), the email/FTP sweeps' pattern → upload_type routing, and raw_epay_daily_tx's own
-- ingest (mig 902/903). REUSED, all of it: the sweep's routing, the /upload choke point, epay_ingest's
-- parse + terminal→store resolution + idempotent upsert, and ingest_record for the journal. NEW: this
-- one registry row and one arrival function. No sibling table, no second parser, no second landing.
--
-- 💰 MONEY: none moved, none recomputed. One INSERT of one declaration row, ON CONFLICT DO NOTHING.
--
-- REVERT: DELETE FROM commcalc.report_kind
--           WHERE org_id = '00000000-0000-0000-0000-000000000001' AND key = 'processor_transaction_detail';

-- ── THE HOUSE REGISTRY ROW (mig 1010's table; the code mirror is report_kinds.HOUSE_KINDS — byte-equal,
--    parsed back by harness_report_kinds.py §A over report_kinds.SEED_MIGRATIONS). Same VALUES block
--    shape as 1010 so the one parser reads both. Sorted beside the carrier bill-payment card. ANY POS,
--    ANY carrier.
INSERT INTO commcalc.report_kind (org_id, key, label, what_in_it, recognisable_columns, source_hint, applies_to_pos,
  applies_to_carrier, defined_by, statement_type, landing, layout, signature_fields, requires_columns, excludes_columns,
  upload_types, sort_order, custom_sheet_label) VALUES
  ('00000000-0000-0000-0000-000000000001', 'processor_transaction_detail', 'Transaction detail from the payment processor back office', 'Every transaction the processor settled for a store — one row per line, with the terminal, the product, the retail amount and the fee lines — the feed the bill-payment and fee reconciliations read.', '{"TransactionID","TerminalID","SettlementDate","ProductTitle","Retail","Commission"}', 'from the processor back office', '{}', '{}', 'house', NULL, 'bill_payments', 'epay_daily_tx', '{}', '{"TerminalID"}', '{}', '{"epay_daily_tx"}', 85, NULL)
ON CONFLICT (org_id, key) DO NOTHING;

SELECT 'Migration 1121 complete — report_kind processor_transaction_detail seeded. No existing row touched, no money moved.' AS status;
