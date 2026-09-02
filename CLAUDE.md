# MetricsPro — agent working rules

Commission/ops platform for multi-tenant wireless retail. Backend FastAPI
(`backend/app/modules/commcalc` and friends), frontend Next.js (`frontend/`), schema of truth in
`database/migrations/` (numbered, idempotent, additive).

## The index is mandatory (owner directive 2026-09-01)

- **Look up before building.** `docs/SYSTEM_DATA_FLOW_INDEX.md` is the durable map of every report,
  query, data flow, table and key function. Consult it FIRST for any investigation or feature —
  never re-derive what it answers, never duplicate a data path it already documents.
- **Duplicate check is a build gate (owner directive 2026-09-02).** Before building ANYTHING —
  a resolution, reconciliation, ingest path, report, endpoint or table — search the index for an
  existing mechanism serving the same data or purpose and EXTEND it (factor it into a shared
  helper if needed) rather than creating a sibling derivation. Two paths answering the same
  question is a defect: they will drift. State in the PR what was checked and what was reused.
- **Register what you create.** Every NEW report, function, endpoint, query or table must be added
  to `docs/SYSTEM_DATA_FLOW_INDEX.md` in the same PR — in its subsystem section and the §16–18
  cross-references, and in the reports category when it is a report. A new external feed also
  registers in `backend/app/modules/commcalc/data_lineage_registry.py` +
  `database/migrations/925_data_lineage_seed.sql` and must pass `harness_data_lineage_guard.py`.

## Payroll & Workforce work routes to the Payroll & Workforce agent (owner directive 2026-09-01)

Any job touching payroll or workforce — payroll setup/onboarding/compliance, employee database,
hours approval, payroll runs, payroll tax/expenses, HR total comp, scheduling, time off, shift
swaps/extensions, hours budget, shift approvals, time-clock permissions, attendance/lateness,
workforce reports, store/employee setup — is assigned to the **payroll-workforce-agent**
(`.claude/agents/payroll-workforce-agent.md`). The two domains are interrelated and owned together.

## Finance work routes to the Finance agent (owner directive 2026-09-02)

Any job touching the finance module — the P&L report and its filters/drill-downs (market, region,
store, company), company/entity structure and rollups, chart of accounts, P&L line bookings and
display, financial statements/exports, quarterly P&L, royalty reporting — is assigned to the
**finance-agent** (`.claude/agents/finance-agent.md`). Commission P&L line AMOUNTS stay with the
commission-agent; their display/filtering/rollup in the P&L is finance.

## Commission work routes to the Commission agent (owner directive 2026-09-01)

Any job touching commission — MA commission, MA TX, multi-month/installment payouts, spiffs,
residuals, rep/manager pay, payout accrual, commission reconciliation/discrepancy, commission P&L
lines — is assigned to the **commission-agent** (`.claude/agents/commission-agent.md`). Spawn it for
such work rather than handling inline, and follow its working rules (index-first, config-never-code,
proof harnesses for money changes, org-scoped queries, evidence-first reconciliation).

## House conventions (apply everywhere)

- **RULE TWO — config, never code**: no carrier/tenant/product branch names in code; behavior is
  per-org config rows with house defaults (org `00000000-0000-0000-0000-000000000001`).
- Migrations are numbered, idempotent, additive, with `-- REVERT:` notes; money-touching changes are
  surfaced for approval before applying.
- Every sensitive query is org-scoped; CI enforces it.
- Pure logic ships with a DB-free proof harness (`backend/harness_*.py`).
