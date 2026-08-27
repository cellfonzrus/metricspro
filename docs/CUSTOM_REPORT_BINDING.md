# Custom Report → Standard Report binding (header-at-setup wiring)

Owner design (2026-08-27): *"at the time the user is creating the email ingestion they should be prompted to
enter the header … so it makes it easier for the system to run the cron job and set up the backend."*

The goal: a new custom report the vendor emails should be wired into a **standard report** **without a developer
writing a per-report resolver**. The header is what makes that possible in the UI at setup time instead of in
code later — so we **capture the header** and let the user **map its columns to a canonical dataset**.

## The flow

1. **Register** the sheet (Data Imports → Custom Reports). It auto-imports on the next sweep (#99 auto-match,
   #101 on-registration sweep) — the rows land verbatim in `raw_custom_import`.
2. **Detect the header** — three sources, in order of what's available:
   - **Upload a sample** (owner: *"I would ask for the sample report"*) — `POST /custom-import-types/{key}/sample`
     reads the header **and sample values** from a file the user uploads at setup (NOT ingested). Most
     reliable: no mailbox dependency, and the user can eyeball each field's real value in the mapper.
   - **captured-first**: `GET /custom-import-types/{key}/columns` returns the union of column keys already
     in `raw_custom_import`; else
   - **auto-read**: the backend opens the tenant's mailbox and reads *just this report's* first matching
     attachment header (no ingest), scoped to the report's own auto-derived filename pattern. Works even
     though no developer can see the inbox — the backend holds the IMAP creds.
3. **Map** — `🔗 Map to report` opens a modal: pick the target dataset, then confirm each canonical field's
   incoming column. The dropdowns are **pre-filled** by `column_mapping.suggest()` (exact > alias > fuzzy).
4. **Save** — `PUT /custom-import-types/{key}/binding` stores the binding, and a background re-ingest applies
   the mapping to already-captured history at once.
5. **From then on**, every sweep maps that sheet's rows into the standard report automatically.

## Design (reuse, not a parallel system)

- **Mapping engine**: the existing, proven `column_mapping` module + `_ingest_mapped_df` (extracted from
  `/upload-mapped` so both the manual-upload path and this binding path apply **byte-identical** safety
  guards — column pre-validation, partition-scoped + source-aware replace, snapshot/restore, upload_log,
  upload_trace). Single-source, migs 208/923 — not a second copy that can drift.
- **Storage**: the binding lives **in** `commcalc.column_mapping` under the *custom* report_key — one sentinel
  row (`target_field='__dataset__'`, `source_header=<dataset>`) names the target, the rest are ordinary
  `source_header → canonical field` rules. **No new table, no migration.**
- **Opt-in + additive**: no binding ⇒ capture-only, today's behaviour byte-for-byte. A binding failure is
  logged and can never fail the capture.
- **Mappable datasets**: only canonical datasets with a real target table (`column_mapping.TABLE_MAP`) are
  offered, so a saved mapping always flows somewhere.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/custom-import-types/{key}/columns?auto=true` | Detected header (captured-first, else auto-read) + samples |
| GET | `/custom-import-types/{key}/binding?target_dataset=` | Current binding + pre-filled suggestions + mappable datasets |
| POST | `/custom-import-types/{key}/sample` | Read header + sample rows from an UPLOADED file (no ingest); same payload as GET binding |
| PUT | `/custom-import-types/{key}/binding` | Save `{target_dataset, rules[]}` (empty dataset unbinds); kicks a re-ingest |

## Notes / limits

- Periodless custom sheets map as **append** into the canonical table (matches `/upload-mapped`); period-scoped
  sheets replace-by-period. Choose the target dataset accordingly.
- Mapping a pre-aggregated report into a transaction table is the user's choice; required fields are marked
  `*` in the UI. Unmapped fields stay empty — the ingest never crashes on a missing optional field.
