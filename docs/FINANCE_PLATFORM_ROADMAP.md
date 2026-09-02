# Finance Platform Roadmap — senior-analyst buildout

> Owner directive 2026-09-02 (verbatim intent): "we really need a senior financial analyst to take
> over and fix the entire finance module and improve where it needs improvement and add other
> financial analysis options, bars charts, projections, etc whatever a top of the line system
> should have — act as we are building an earnest and young financial analyst system with a
> probable company valuation and create an on demand financial statement whenever required
> platform wide not as an added feature."
>
> This document is the gap analysis + the phased plan of record. Everything shipped from it
> registers in `docs/SYSTEM_DATA_FLOW_INDEX.md` in the same PR; every money-affecting change
> ships a stdlib proof harness; every knob is per-org config (RULE TWO); UI phases go to the
> owner as Option-B preview PRs.

## 1. Where the finance module stands today (gap analysis)

### What already exists (do not rebuild)
| Capability | Where | State |
|---|---|---|
| Deterministic P&L + Balance Sheet per org/period, scoped consolidated / company / store | `account/coa.py` (specs + build_inputs), `account/engine.py` (assembly + snapshots in `account_statements`) | solid; snapshot-based |
| **On-demand statement engine (P&L + BS + Cash Flow, any org/period/scope, fresh)** | `account/statement_engine.py`, `GET /account/statement/{period}`; notify key `financial_statement` | **shipped this phase** |
| Store/market/company filters over statements | `account/statement_filter.py` (RULE FIVE) | shipped (sibling, 2026-09-02) |
| MA→store attribution, per-org line labels, MDF line | `account/ma_store_pnl.py` (mig 314) | shipped (sibling) |
| Balance-sheet truths: unsold-phone inventory basis + recon, handset payables by vendor due dates, journal company designation | `account/balance_sheet.py` (mig 933) | **shipped this phase** (org seeds gated on owner GO) |
| Per-org accounting config | `commcalc.account_config` (migs 611/613/621/933) | growing config surface |
| Statement staleness + auto-recompute sweep | `account/autocompute.py`, `POST /account/run-due` | works; **scheduling gap** — see Phase 1 |
| Deterministic narrative + optional Claude narrative | `account/router.py` `_account_narrative`, `engine._narrate` | shipped |
| Scheduled/on-demand report sends (email/WhatsApp) | `notify/report_registry.py` (`account_pl`, `account_balance_sheet`, now `financial_statement`) | shipped |
| Trends hub, residual-per-sub, GP/expenses/commission trend endpoints | `/accounts/trends`, `/gp-trend`, `/expenses-trend`, `/commission-trend` | partial charting exists |

### The gaps a top-of-line young-company system closes
1. **Cash flow** — existed nowhere until this phase; now derived (indirect) and stored, but the
   Cash line is manual: cash tie-out automation (bank-deposit feed → cash roll-forward) is open.
2. **No scheduled recompute trigger** — `/account/run-due` has no pg_cron registration (unlike the
   email sweep, migs 921/922). Books go stale until someone clicks Recompute; this is exactly how
   the owner's journal entries "never showed up" (entered 03:05Z, snapshot from 02:30Z).
3. **Journal UX** — no company picker (owner typed company names into the free-text store field),
   no entry dates on BS items, silent row drops (fixed server-side; picker is UI Phase 2).
4. **Charts** — trends exist for a few series; no general financial-analysis charting (revenue /
   GP / opex / NI trends, per-company comparisons, expense composition bars, margin waterfalls).
5. **Projections / forecasting** — nothing. No run-rate, no seasonality, no budget-vs-actual.
6. **Valuation** — nothing. No trailing-metric view, no configurable multiples, no DCF-lite.
7. **Ratios & health** — no margin/liquidity/efficiency ratio panel, no working-capital view.
8. **Multi-period statements** — statements are one-period; no side-by-side months / quarter /
   YTD / trailing-twelve-month (TTM) assembly (the quarterly + royalty reporting seam).

## 2. Phased plan

### Phase 1 — statements as a platform service (backend; THIS PASS ships the core)
- ✅ `statement_engine.statement(org, period, scope, kinds)` — fresh P&L/BS/CF on demand;
  endpoint `GET /account/statement/{period}`; notify report `financial_statement` for scheduled /
  on-demand email + WhatsApp via the standard registry (never a bespoke exporter).
- ✅ `statement_engine.compute_and_store` supersedes `engine.compute_and_store` on `/compute` and
  the `run-due` sweep: same snapshots + Cash Flow + the balance-sheet truths.
- ▢ **Follow-up**: mig 934 — pg_cron registration for `POST /account/run-due` (the mig 921/922
  self-scheduling pattern), so books recompute themselves after every ingest/journal edit.
- ▢ **Follow-up**: multi-period assembly in statement_engine — `periods=[...]` returning
  month-by-month columns + QTD/YTD/TTM rollups from the same inputs (feeds quarterly + royalty
  reporting and every chart below). Pure aggregation over per-period inputs; harnessed.

### Phase 2 — finance UI truth fixes (Option-B preview PRs, small)
- Journal page: **company picker** (from `/account/companies`) + store picker (RULE THREE:
  pick-don't-type — the defect that stranded the owner's $560k of entries), show the server's
  `rejected`/`resolved` echo after save, "Recompute now" button on save.
- Balance-sheet page: Cash Flow tab (reads `GET /account/cash-flow/{period}`), handset-payable
  drill (detail per order-type family), inventory line source chip (report/devices/manual).
- Reconciliation tab: the inventory tie-out grid from `GET /account/inventory-recon`
  (report vs unsold-phone ledger vs manual vs effective, with unplaced/superseded ghost counts).

### Phase 3 — financial analysis charts (backend series + Option-B UI)
- One series endpoint: `GET /account/series?metrics=revenue,gross_profit,net_income,opex&months=N`
  computed from stored statements (never a second math path) with per-company/store filters.
- Charts (frontend, shared chart kit): revenue/GP/NI trend lines; expense-composition stacked
  bars; per-company comparison bars; margin % trend; working-capital trend from BS snapshots.
- Ratio panel on the dashboard: gross margin, opex ratio, net margin, current ratio,
  inventory days (device ledger ÷ device COGS run-rate), payable days.

### Phase 4 — projections & forecasting (config, never code)
- `account/projection_engine.py` (pure): run-rate + trailing-N-month weighted trend per P&L line,
  optional per-org seasonality factors and growth overrides in `account_config`
  (`projection_config JSONB`); output = the SAME statement payload shape, flagged
  `projected: true`, so every statement surface renders projections for free.
- Budget-vs-actual: per-org budget rows (per line/period) + variance columns; harnessed.
- Cash runway: projected NI + working-capital deltas → months-of-cash at current burn.

### Phase 5 — company valuation (defensible, assumption-driven)
- `account/valuation.py` (pure) + `GET /account/valuation`:
  - **Revenue/earnings multiples** on trailing metrics (TTM revenue, TTM adjusted EBITDA ≈ NI +
    interest/taxes from journal `other` lines): `value = metric × multiple`, multiples per org in
    `account_config.valuation_config` (house defaults published in the doc, e.g. wireless-retail
    0.3–0.6× revenue / 2.5–4× SDE — every number a CONFIG, cited on the page, never code).
  - **DCF-lite**: Phase-4 projected free cash flows, config discount rate + horizon + terminal
    multiple; sensitivity grid (rate × multiple) rendered alongside — a range, never one number.
  - Every output carries its assumptions inline (the E&Y-style "basis of preparation" block) and
    a stdlib harness proving the arithmetic.
- UI: valuation page with assumption editors (Option B).

### Phase 6 — close-the-loop accounting hygiene
- Cash roll-forward from the deposit-recon feed (mig 107 `bank_deposit`) as an optional cash
  basis: `cash_basis='deposits'` knob → the CF tie-out becomes automatic.
- Inter-store borrowings UI + repayment column (index §19 note), fixtures register,
  owner-contribution log (a first-class equity ledger instead of journal rows).
- Period locks: a closed month's snapshot freezes; recompute requires an explicit unlock.

## 3. Standing rules for every phase
- **Index-first**: each shipped item registers in `SYSTEM_DATA_FLOW_INDEX.md` (§20 finance
  statements + §16–18) in the same PR; report keys register in the reports category.
- **Config, never code**: all bases/multiples/tokens/families are per-org `account_config` /
  `commission_org_config` rows with house defaults; org seeds ship COMMENTED behind owner gates
  (mig 622/933 precedent).
- **Money proves itself**: every engine ships `backend/harness_*.py` (stdlib, no DB); live
  evidence is gathered READ-ONLY (the mig-933 header pattern).
- **Org-scoped fail-closed**: every query carries org_id; unknown scopes return computed:false,
  never another org's rows.
- **Option B**: backend/logic merges on green; anything visual waits for the owner's eyeball on a
  preview PR.
