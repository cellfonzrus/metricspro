## A commission statement has ONE ledger identity, ONE period spelling, ONE lander — and N landings of one statement × period are REFUSED, never summed (index §30.15)

### The instance (measured on the live tenant, org `f4f1c16e…`, 2026-09-22)

`commcalc.commission_ledger` grouped by (period, source_report), Σ payout_total:

| period | source_report | rows | Σ |
|---|---|---|---|
| `'Aug 2026'` | `<carrier>` | 522 | 7,396.27 |
| `'aug 2026'` | `<carrier>__commission_statement` | 521 | 7,396.27 |
| `'August 2026'` | `<carrier>__commission_statement` | 521 | 86,970.34 |
| `'July 2026'` | `<carrier>` | 973 | 14,411.40 |
| `'July 2026'` | `<carrier>__commission_statement` | 973 | 165,997.59 |

`upload_trace`: the same July file landed at 04:00:37 through the onboarding intake (under `<carrier>__commission_statement`), at 04:00:57 through the older Commission Ledger wizard (under the bare `<carrier>`, source `ledger-import`), and again at 00:15 via the intake. `_period.period_keys('August 2026')` = `['August 2026', '2026-08']`, so the `'Aug'` / `'aug'` copies were ORPHANS no reader saw, while July's two copies were BOTH read: a ledger-sourced P&L (#273) would have booked July TWICE. The legacy-key copies carry the pre-#246 sign (Σ 14,411.40 vs 165,997.59): the intake copy is the one with the declared sign convention.

### The class (three facts, each fixed for every caller, one home each)

1. **Statement identity was route-dependent.** The intake wrote `<carrier>__<statement slug>`; the older `/commission-ledger/import` wrote the bare carrier code; the MA refresh wrote the bare template key. #258 unified the MAPPING key per statement type but not the LEDGER key, so the same statement under two routes was two statements and `_ledger_delete_scoped` never replaced the other copy.
2. **The period was stored as typed** (`'Aug 2026'`, `'aug 2026'`, `'August 2026'`); readers looked a fixed spelling set up, so copies under another spelling were orphans that still counted in any Σ not going through `period_keys`.
3. **No reader guarded against N copies** of one statement × period.

### The fix — one identity, one spelling, one lander, one guard

| | Home | What |
|---|---|---|
| **Identity** | `commission_ledger.ledger_identity` / `ledger_source_report` / `source_report_family` / `identity_key` / `template_key` | `(base, statement slug)` derived ONCE; the stored key is `<base>__<slug>` — the intake's form byte-for-byte (`onboarding_intake.source_report_key` / `slug` / `STATEMENT_TYPE_DEFAULT` now DEREFERENCE it); a bare pre-existing key READS as its base's default statement type (its family `[canonical, bare]`), so every existing tenant reads byte-identically; the mapping key (#258) is a projection of the same pair through `statement_type_of_source_report` |
| **Period** | `account/_period.canonical_period` (+ `is_canonical_period`); `period_keys` now canonical-first and deterministic (a strict superset of before); `parse_period` accepts `Aug 2026` / `Sept 2026` / `2026-8` (additive) | The month-NAME form is stored; every spelling is read. `commission_ledger.canonical_period` / `ledger_period_keys` / `is_orphan_period` dereference it; **`ma_recon.canonical_period`, a pre-existing sibling, now dereferences it too** |
| **Lander** | `router._ledger_land_rows` — the older wizard, the intake's 3.9 **and the MA refresh** (origin `ma_sync`; its own insert loop is gone) | Stamps the derived key + canonical period on every row; `_ledger_delete_scoped` MEASURES what the family × canonical period already holds (`_ledger_landings_present`: the family read across every stored spelling, so an orphan copy of the month being landed joins the wipe), wipes family × every spelling × origin, then inserts; the trace and the 3.9 result / import payload say **"replaced 973 rows landed on 2026-09-20 under the older key '<carrier>' (its net 14,411.40 / 973 rows differs from this landing's 165,997.59 / 973 rows — a different sign convention or line count)"** |
| **Query** | `router._ledger_query(client, org, source_report, period, origin, cols)` | org × family × every period spelling × origin — every ledger reader in the router builds on it; rule reads (`load_rules_meta`, `_intake_house_defaults`, `get_commission_category_map`) read the family too |
| **Guard** | `commission_ledger.landings_for` / `landing_conflicts` / `landing_sentence` | A LANDING = one (origin, stored key, stored period spelling) tuple, derived from the columns every row carries — **no landing-id column, no migration**. `summarize` carries `landings` / `landing_conflict` additively. Every summing reader refuses: `/commission-ledger/summary` (`router._ledger_guarded_summary` → every money key 0, `refused`, the evidence), `/by-rep`, `/observed-types`, `_statement_buckets`, and the P&L (`ledger_pnl.ledger_bookings` books NOTHING for the conflicted statement × period, reports it under `unbooked`, `divergence(conflicts=)` puts it on the line's words) — with **"July 2026 holds 2 landings of the commission statement (973 + 973 rows) — retire one under Onboarding → Intake"**. Two origins (file + MA sync) of one statement × period are two landings too: the earlier "the tiles add them together" warning is now a refusal (the origin filter still reads one at a time) |
| **Retire** | `GET /commission-ledger/landings`, `POST /commission-ledger/landings/retire`; `POST /onboarding/intake/retire` extended to commission instances | One landing by its STORED spelling: dry run counts, the exact count is confirmed, rows go by id, the trace carries the reason and the name (admin-gated; the #268 counted core `_intake_remove_landed` takes a ledger slice shape). A commission instance's retire slice is its statement × period FAMILY (both copies). The Commission Ledger page renders the refusal block with a landings table and "Retire this landing" per row, and lists the template's orphan periods with the same control; the intake's 3.9 card shows the replaced note and a "Retire this statement" button |

### Duplicate check (build gate)

Searched §15 / §16 (`commission_ledger`, `commission_category_map`, `column_mapping`), §17 (`/commission-ledger/*`, `/onboarding/intake/retire`, `/commission-category-map`), §18, §4b (`ledger_pnl`), §25.11–25.12, §30.6 (`_ledger_land_rows`), §30.7, §30.10 (the mapping-key derivation), §32 (`_ledger_delete_scoped` excused), `_period` and its siblings (`router._pvariants` × 9, `ma_recon.canonical_period`, `calculator.parse_period`). **Reused:** `_ledger_land_rows` (the one lander — the refresh now goes through it), `_ledger_delete_scoped`, `onboarding_intake.source_report_key` (kept by name, dereferencing), `column_mapping.split_report_key` / `variant_report_key`, `_period.period_keys`, `_intake_remove_landed`, `summarize` (additive keys), the mig-202 trace, the pages' existing retire pattern. **New:** the identity + landings functions in `commission_ledger.py`, `_period.canonical_period` / `is_canonical_period`, `router._ledger_query` / `_ledger_landings_present` / `_ledger_guarded_summary` / `_ledger_landing_slice`, two endpoints, one refusal block, one lock, one proof. **No migration. No new feed.**

### Siblings — fixed or excused by name

Fixed: `/commission-ledger/import`, `_intake_commit_commission`, `/ma-sync` (+ its preview's `delete_scope`), `/summary`, `/rows`, `/observed-types`, `/by-rep`, `/provenance` (per period: `canonical`, `orphan`, `landings`, `landing_conflict`), `_intake_reread`, `_ledger_existing_by_origin`, `_mcw_ledger_rows`, `_statement_buckets`, `ledger_pnl.load_ledger_rows` → `ledger_bookings`, `list_templates` (folds a family onto its `template_key`), the rule reads, `ma_recon.canonical_period` (folded). Excused: `whatif._ledger_income_rows` (whole-org read, groups by key for a trend — not a booking; the §30.7 open item covers it); the nine `_pvariants` copies across the platform (the ledger's chains no longer use any of them — the lock forbids it; folding the rest is platform-wide, outside this PR).

### Proof and locks

- **`backend/harness_ledger_statement_identity.py` — 92 checks**, DB-free: the shared intake fake driving the REAL router + the P&L proof's client driving the REAL `coa.build_inputs`. §A the derivation; §B the canonical period; §C the live shape as a fixture → THE SENTENCE verbatim, August's 3 landings with the orphans flagged; §D the regression through the endpoints — summary / by-rep / observed-types / `_statement_buckets` refuse July, `/landings` + `/provenance` show the evidence, August reads 86,970.34 through `period_keys` (the orphans count nowhere, as measured), the retire (dry run 973 → 409 on a wrong count → confirmed → traced) after which July books 165,997.59, the two August orphans retired, the intake's retire on the commission instance (1,946 rows, confirmed); §E the lander — bare key + lower-case period stored canonical, legacy rows REPLACED with the trace sentence, orphan spellings replaced, other statement types / months untouched, the origin scope kept, the older wizard's `/import` end to end; §F the P&L — one landing books, two hold the statement, a second statement still books, the REAL `coa.build_inputs` with a statement under two period spellings books 0 on `carrier_comm` and says why while every uncovered line is byte-identical; §G compatibility pins (house org bare `ma_daily_tx`, the master-agent tenant's two templates, an intake-only carrier); §H negative controls (a sibling keyed by the raw key → no conflict; the guard bypassed → the double 180,408.99; the lock scanner RED); §I wiring, RULE TWO, registration, CI, no migration.
- **`backend/harness_ledger_identity_lock.py` — 34 checks, in `carrier-vocab-guard.yml`**: one derivation, one period home (every other `canonical_period` dereferences it), no second `<base>__<type>` composition, no ledger chain filtered by a literal `period` / `source_report` / `_pvariants`, one lander (the refresh calls it, no insert of its own), the intake dereferences, every summing reader guarded, the pages wired, negative controls, a stale allow entry RED.
- **Unchanged and green:** sign 125, statement_type_mapping 62, mapping_key_lock 17, intake 164 / B 87 / C 92 / D 49, pl_commission_source 73 + lock 18, sales_by_invoice 95, ma_recon 44, landing_identity_lock 32, org-scope 25, carrier-vocab, report-kind lock, line-class lock, lineage 63, connector-scope, report-links 15, tender-vocab, pos-sales lock; `tsc --noEmit` clean.
- **Re-baselined deliberately, by name:** `harness_ledger_ma_sync` (113: the delete is `in_` over the family and every spelling; the refresh's rows carry the canonical key; the preview's `delete_scope` shape; a period populated by both origins REFUSES to sum instead of adding), `harness_commission_ledger_sign` (125: the refresh's guard-before-wipe order is the lander's), `harness_landing_identity_lock` (the excuse accepts `in_("source_report"`).

### Live data — REPORTED, not repaired. The owner-approved cleanup (org `f4f1c16e…`)

**As clicks:** Commission Ledger page → template `<carrier>` → period `July 2026` → the refusal block → **Retire this landing** on the `<carrier>` / `July 2026` row (dry run says 973 → confirm) → the "stored under a spelling no report reads" block → retire `'Aug 2026'` (522) and `'aug 2026'` (521). Or under Onboarding → Intake → the statement's 3.9 card → **Retire this statement** (removes the whole July family, 1,946 rows — then re-land the kept file once).

**As SQL (verify the counts first; nothing is applied by this PR):**

```sql
-- preflight: the five groups exactly as measured
SELECT period, source_report, count(*), round(sum(payout_total), 2)
  FROM commcalc.commission_ledger WHERE org_id = '<f4f1c16e…>' GROUP BY 1, 2 ORDER BY 1, 2;
-- 1) the legacy-key July copy (pre-#246 sign): expected 973 rows, Σ 14,411.40
DELETE FROM commcalc.commission_ledger WHERE org_id = '<f4f1c16e…>' AND source_report = '<carrier>' AND period = 'July 2026';
-- 2) the legacy-key August copy under the abbreviated spelling: expected 522 rows, Σ 7,396.27
DELETE FROM commcalc.commission_ledger WHERE org_id = '<f4f1c16e…>' AND source_report = '<carrier>' AND period = 'Aug 2026';
-- 3) the orphan lower-case August copy under the intake key: expected 521 rows, Σ 7,396.27
DELETE FROM commcalc.commission_ledger WHERE org_id = '<f4f1c16e…>' AND source_report = '<carrier>__commission_statement' AND period = 'aug 2026';
-- keep: 'July 2026' / '<carrier>__commission_statement' (973, 165,997.59) and 'August 2026' / '<carrier>__commission_statement' (521, 86,970.34)
```

**Money effect, stated plainly.** After the cleanup the ledger holds one landing per month: **July books 165,997.59 and August 86,970.34** from the intake copies (the declared sign convention). Nothing books from the ledger to the P&L until the org's `pl_commission_source` is switched (#273, mig 1013 not applied); until the cleanup, July's two landings make the ledger page and any ledger-sourced P&L REFUSE rather than show 180,408.99. No row anywhere is deleted, moved or re-keyed by this PR.

### Seams left (reported, not hidden)

1. A tenant carrier's picker key is now its bare code (the picker used to list both spellings); the Category Map link saves NEW rules under the bare key while the intake's sit under the long key — one family for every read, two stored spellings for the writer (the editor updates by id; a rule writer that canonicalises on save is a follow-up).
2. Two origins (file + MA sync) of one statement × period now refuse instead of summing with a warning.
3. `_pvariants` × 9 across the platform (see siblings).
4. A statement typed at 3.1 whose slug differs from a later registry token (#258 seam 3) keeps its own ledger key — the identity follows the slug the rows carry.

Registered: index §15 / §16 / §17 / §18 / §4b addendum / §30.7 addendum / §30.10 addendum / §32 / **§30.15**; `docs/ONBOARDING_FLOW_DESIGN.md` §14.

### Merged onto #279's head (238607c = main 43aabcb + `wip/config-reader-any-subset`)

Both designs hold. In the resolved files: `.github/workflows/carrier-vocab-guard.yml` keeps the plan-sources lock step AND the ledger statement-identity lock step (beside the any-columns lock); `coa.build_inputs` hands `divergence` BOTH `conflicts=` (the landings guard) and `config_columns_missing=` (the reader's report); `ledger_pnl.divergence` takes both, and `load_ledger_rows` is #279's any-subset read verbatim (`_LEDGER_REQUIRED` / `_LEDGER_OPTIONAL`, `origin` probed through `core.column_tolerant.present_columns` — the landing is derived from `source_report` / `period` / `origin`, so the P&L reader needs no date column); `router._ledger_existing_by_origin` and `commission_ledger_provenance` probe `origin` / `synced_at` through `present_columns` AND read through `_ledger_query` (org × family × every period spelling — no `_pvariants`, no literal filter); my `_ledger_landings_present` column-tier loop was a for-ladder under #279's lock and is rewritten as one probe + ONE select (`present_columns` / `select_list`); `docs/ONBOARDING_FLOW_DESIGN.md` keeps #278's §13 (plan sources) and this design as **§14**. Re-run on the merged tree: identity proof 92, identity lock 34, any-columns 57 + lock 16, P&L 73 + lock 18, ma_sync 113, sign 125, landing-identity lock, org-scope 25, carrier-vocab, statement-type mapping 62, intake 164 / B 87 / C 92, `tsc --noEmit` clean.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01Ybsehpbqn5E2oKoGGR4uhf
