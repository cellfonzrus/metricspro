-- 099_custom_import.sql — Self-serve custom import sheets (generic JSONB capture)
-- Lets a user add a NEW auto-import sheet (e.g. B2B "Sales Trend") entirely from the UI — no code, no
-- per-report table. They register a report_key in commcalc.report_definitions (target_table =
-- 'raw_custom_import', upload_endpoint = 'custom') and add a filename pattern on Email/FTP Imports; the
-- sweep then routes matching attachments here and captures every row verbatim as JSONB, keyed by
-- report_key. SAP doctrine: the import type is DATA, not hard-coded. Idempotent — safe to re-run.

create table if not exists commcalc.raw_custom_import (
  id            bigserial primary key,
  org_id        uuid not null,
  report_key    text not null,                       -- which custom sheet this row belongs to
  period        text,                                -- optional period label (e.g. 'July 2026'); NULL = periodless
  period_month  int,
  period_year   int,
  source_filename text,
  row_index     int,                                 -- position within the imported file (stable ordering)
  data          jsonb not null default '{}'::jsonb,  -- the whole source row, header -> value
  carrier_id    uuid,
  created_at    timestamptz not null default now()
);

-- Look up / replace a sheet's rows fast (the ingester deletes by org+report_key+period before re-insert).
create index if not exists raw_custom_import_lookup_idx
  on commcalc.raw_custom_import (org_id, report_key, period);
-- Periodless idempotency: replace by org+report_key+source_filename when a sheet has no period.
create index if not exists raw_custom_import_file_idx
  on commcalc.raw_custom_import (org_id, report_key, source_filename);
-- Ad-hoc filtering/reporting over the captured columns.
create index if not exists raw_custom_import_data_gin_idx
  on commcalc.raw_custom_import using gin (data);
