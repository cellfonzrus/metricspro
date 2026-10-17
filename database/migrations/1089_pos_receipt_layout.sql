-- MIGRATION 1089: WHICH LAYOUT THIS TENANT'S RECEIPTS PRINT IN — one column, per org, as config
-- (owner 2026-10-17; docs/SYSTEM_DATA_FLOW_INDEX.md §POS-02). Idempotent, additive. OWNER-RUN.
--
-- Owner, verbatim: "admin should be able to edit the receipt, teh template for the receipt for vzone
-- ahoul be same as uplaoded from rq, the email option should show which email to send to and it
-- should be editable, the from email address should show the company name Vzone Wireless or whatever
-- name is set up during onboarding"
--
-- WHAT WAS WRONG. "What does this org's receipt look like?" had TWO answers. The register built its
-- receipt as HTML in the BROWSER, applying the template's toggles, the brand block and the
-- disclaimers; a receipt IMPORTED from a POS was built as a structured Document and printed in that
-- POS's own layout by pos/receipt_formats/render.py. So an admin editing "the receipt template"
-- changed one of them, the other kept its own look, and a tenant whose uploaded receipts print in
-- its POS's layout could not ask for its till receipts to match. Nothing could render a receipt
-- where there is no browser at all, which is why emailing one had never been built — the config keys
-- for it (`email_receipt_subject` / `email_receipt_body`) have sat in the POS registry marked "not
-- yet enforced" since the module landed.
--
-- WHAT THIS COLUMN HOLDS. `pos.receipt_templates.layout` — 'thermal' (the 58/80 mm till slip, which
-- is what every tenant keeps unless it says otherwise) or 'pos_format' (print the sale in the layout
-- of the POS this tenant DECLARED, i.e. the layout its own uploaded receipts are parsed from and
-- reprinted in). It is the only new fact; everything else the admin edits was already here.
--
-- RULE TWO: no tenant and no POS is named. 'pos_format' means "the POS this org declared", resolved
-- through the one reader of that declaration (report_kinds.tenant_declaration →
-- receipt_formats.registry), so a tenant moving POS moves its receipts with it and no carrier,
-- tenant or product name appears in code. The DEFAULT is 'thermal', which is byte-identical to what
-- every tenant prints today — applying this file changes nothing until an admin chooses otherwise.
--
-- MONEY IMPACT: NONE. A receipt is a restatement of a sale that has already been rung: the figures
-- printed are the snapshot `pos.sales.receipt` already carries, and nothing here is read by any P&L,
-- GP, commission, payout or tax path. No existing row of any table is written by this file.
-- Proof (DB-free): backend/harness_pos_receipt_render.py.
--
-- ADAPTIVE: the backend works before this file is applied. The reader treats a missing column as
-- 'thermal' (`pos/sale_receipt.template_view`), GET /pos/receipt-template reports
-- `layout_column_ready: false`, and a PATCH that tries to set the layout answers with the name of
-- this file instead of a bare failure. So applying it turns the CHOICE on; printing and emailing
-- receipts work either way.
--
-- REVERT (paste and run to undo — drops only what this migration owns):
--   BEGIN;
--   ALTER TABLE pos.receipt_templates DROP COLUMN IF EXISTS layout;
--   COMMIT;
--   NOTIFY pgrst, 'reload schema';

BEGIN;

ALTER TABLE pos.receipt_templates
  ADD COLUMN IF NOT EXISTS layout TEXT NOT NULL DEFAULT 'thermal';

-- The two layouts the renderer declares (pos/sale_receipt.LAYOUTS). A constraint rather than an
-- enum: adding a third layout is then one migration and no type juggling.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pos_receipt_templates_layout_ck') THEN
    ALTER TABLE pos.receipt_templates
      ADD CONSTRAINT pos_receipt_templates_layout_ck
      CHECK (layout IN ('thermal', 'pos_format'));
  END IF;
END $$;

COMMENT ON COLUMN pos.receipt_templates.layout IS
  'Which layout this org''s receipts print in (mig 1089, index §POS-02): thermal = the 58/80 mm till '
  'slip; pos_format = the declared POS''s own receipt layout, the same one this tenant''s uploaded '
  'receipts are reprinted in. One home for the rendering: pos/sale_receipt.py.';

COMMIT;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1089 complete — pos.receipt_templates.layout (till slip or the declared POS''s layout).' AS status;
