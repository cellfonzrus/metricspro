# MetricsPro — Architecture (how it works today)

Technical map for developers/agents. Pair with [SAAS_FRAMEWORK.md](SAAS_FRAMEWORK.md) (where it's
going) and [BUG_AUDIT.md](BUG_AUDIT.md) (known issues). Plain-English version: [USER_GUIDE.md](USER_GUIDE.md).

## Infrastructure

| Piece | Where | Notes |
|---|---|---|
| Frontend | **Next.js 14** → Vercel (`metricspro-five.vercel.app`) | auto-deploys on push to `main` |
| Backend | **FastAPI** → Railway (`metricspro-production.up.railway.app`) | auto-deploys on push to `main`; all routes under `/api/v1` |
| Database | **Supabase Postgres** (ref `etxdalernqqtwjcrtcuj`) | schema-per-module: `commcalc.*`, `storeops.*`, `core.*` |
| Repos | `cellfonzrus/metricspro` (the app) + `cellfonzrus/commcalc` (context/docs/tools) | dev in Codespace `/workspaces/{metricspro,commcalc}` |
| Deploy | `git push` to `metricspro` `main` via `METRICSPRO_TOKEN` (the Codespace token lacks write) | Railway green + Vercel Ready before testing |
| SQL/migrations | **User-only**, in the Supabase SQL editor | Claude provides the exact block; migrations are idempotent (`IF NOT EXISTS`) |
| Tenancy | single hardcoded `org_id = 00000000-…-0001` | the multi-tenant rework surface (see framework §5) |

## Backend modules (`backend/app/modules/`)

- **commcalc** — the core. Commissions (`calculator.py`), daily targets (`targets_engine`), Sales
  Analyzer (`sales_analyzer.py`), comp/total-comp trend (`comp_trend.py`), and the four portal
  **sweeps** (`dlar_sweep`, `vip_sweep`, `epay_sweep`, `b2b_sweep`). Router: `commcalc/router.py`.
- **account** — P&L / Balance Sheet. `coa.py` (chart-of-accounts + `store_resolver` canonicalization +
  `build_inputs`), `engine.py` (compute + snapshot to `account_statements`), `recon.py` (VIP
  credit-memo ↔ MI/ATU reconciliation). Multi-company, deterministic numbers + optional Claude narrative.
- **asset** — device ledger (`asset_ledger`), aging buckets, charges dashboard, hotsheet expected-vs-paid
  recon, VIP invoice join. Parser: `asset_parser.py`.
- **storeops** — employees, scheduling/shifts, time-off, payroll.
- **storevisit** — DM store-visit (checklist + GPS, action-item rollup + sign-off, daily closing).
- **closing** — daily closing sheet + reconciliation vs B2B actuals.
- **notify** — email (Resend) + WhatsApp (Meta) report delivery, on-demand + scheduled.

## Data flow

```
PORTALS                         INGEST                     RAW TABLES (commcalc.*)        ENGINES                      OUTPUT
ePay owner portal  ─ sweep ─►   epay_sweep   ─►  raw_mi, raw_payment_detail, raw_comp_report ─┐
Elevate Go (DLAR)  ─ sweep ─►   dlar_sweep   ─►  raw_dlar_rep, raw_dlar_store               ─┤
VIP Wireless       ─ sweep ─►   vip_sweep    ─►  vip_invoices/lines/devices, vip_paygo      ─┼─► calculator  ─► rep_commissions, flags, chargeback_items
B2B Soft           ─ upload ─►  upload_file  ─►  raw_sales (78-col), inventory_value         ─┤   targets_engine ─► targets dashboards
Yoobic             ─ (todo) ─►  hotsheet                                                     ─┤   coa+engine    ─► account_statements (P&L/BS)
manual uploads     ─ upload ─►  /commcalc/upload/{file_type}                                 ─┘   comp_trend    ─► Total Compensation Trend
                                                                                                 recon         ─► VIP↔MI/ATU flags
asset ledger       ─ upload ─►  asset_parser ─►  asset_ledger ─► aging / charges / hotsheet-recon / owed-to-VIP
```

## Conventions that bite (read before editing)

- **Period is stored as `"%B %Y"`** ("June 2026"), NOT `"2026-06"`. Filtering by the wrong spelling
  silently returns `[]`/`$0`. `account/coa.build_inputs` queries BOTH spellings; most other endpoints
  don't (see BUG_AUDIT Theme 2). When in doubt, normalize at the boundary.
- **Aggregate in Postgres**, not Python. A full `asset_ledger`/`raw_mi` scan into Python is slow and
  fragile (depends on PostgREST max-rows being raised); prefer RPCs (e.g. `daily_sales_actuals`).
- **Wipe-and-insert is the upload/sweep model** — always guard it (empty-abort + partial-collapse) so a
  bad pull can't zero a period. The epay + DLAR sweeps and the upload endpoint now do; others are
  flagged in BUG_AUDIT Theme 1.
- **Store canonicalization**: raw store strings (address/SFID/code) resolve to a canonical
  `store_mapping.store_address` via `store_aliases` then leading-number match (`coa.store_resolver`).
  Unmapped stores show up in the store-match UI.
- **Backend Python edits** (per CLAUDE.md): anchor-based, `assert anchor in src` + single-occurrence.
- **Folder paths with parentheses**: `frontend/src/app/(platform)/…` — always quote in shell.

## Sweep scheduling

Each portal has a backend-only `*_sweep_config` table (creds write-only) with `frequency/hour/timezone/
next_run_at/last_status`. `pg_cron` POSTs `/commcalc/{dlar,epay,vip,b2b}/sweep/run-due` (+ `notify/run-due`)
with header `X-Notify-Secret`; `run-due` runs every enabled config whose `next_run_at` has passed and
advances it. `run-now` triggers a single config immediately (background task). `epay` drives headless
Chromium (WAF-gated to Railway's egress IP); the others use `requests`/HTML.

## Key tables (commcalc.* unless noted)

`raw_sales` · `raw_mi` · `raw_payment_detail` · `raw_comp_report` · `raw_dlar_rep` · `raw_dlar_store` ·
`asset_ledger` · `store_mapping` · `store_aliases` · `rep_aliases` · `hotsheet` · `vip_invoices` /
`vip_invoice_lines` / `vip_invoice_devices` · `vip_paygo_payments` · `rep_commissions` · `flags` ·
`chargeback_items` · `targets` · `inventory_value` · `daily_closing` · `account.account_statements` ·
`storeops.{employees,shifts,time_off,store_visits,checklist_items}`.
