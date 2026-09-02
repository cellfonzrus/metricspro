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
2. ~~No scheduled recompute trigger~~ **closed (mig 940)** — `/account/run-due` now self-schedules
   (`account-recompute-run-due` pg_cron job, every 2h, re-registered on every backend boot). The
   owner's "never showed up" staleness (entered 03:05Z, snapshot from 02:30Z) self-heals per tick.
3. **Journal UX** — no company picker (owner typed company names into the free-text store field),
   no entry dates on BS items, silent row drops (fixed server-side; picker is UI Phase 2).
4. ~~Charts~~ **closed (Phase 3, 2026-09-02)** — `/account/analysis` + the `/accounts/analysis`
   page (trends, margins, expense composition, comparisons; UI awaiting owner preview).
5. ~~Projections / forecasting~~ **core closed (Phase 4, mig 941)** — linear + seasonal-naive +
   overrides + cash runway; budget-vs-actual still open.
6. ~~Valuation~~ **core closed (Phase 5, mig 941)** — TTM multiples + asset floor + DCF w/
   sensitivity, own `company_valuation` grant; UI assumption editors still open.
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
- ✅ **Shipped (mig 940** — 934 was renumbered away to the rebate-presentation config**)**:
  pg_cron registration for `POST /account/run-due` via `commcalc.ensure_account_recompute_cron`
  (the mig 921/922 self-scheduling pattern; job `account-recompute-run-due`, every 2h,
  re-registered on every backend boot from `main.py`), so books recompute themselves after every
  ingest/journal edit lands — within a tick, with the staleness banner covering "right now".
- ▢ **Follow-up**: multi-period assembly in statement_engine — `periods=[...]` returning
  month-by-month columns + QTD/YTD/TTM rollups from the same inputs (feeds quarterly + royalty
  reporting and every chart below). Pure aggregation over per-period inputs; harnessed.

### Phase 2 — finance UI truth fixes (Option-B — SHIPPED 2026-09-02, awaiting owner preview)
- ✅ Journal page: **company picker** (from `/account/companies`) + store picker (RULE THREE:
  pick-don't-type — the defect that stranded the owner's $560k of entries), and the server's
  `rejected`/`resolved` echo surfaced after save (red rejected panel with reasons + company
  attributions). ▢ deferred: entry-date column, inline "Recompute now" button (the staleness
  banner + mig-940 cron cover it).
- ✅ Cash Flow: shipped as its own `/accounts/cash-flow` page (stored snapshot, scope select,
  staleness banner, honest tie-out banner, export) + a link from the Balance-sheet page.
  Handset-payable drill already renders via the BS line's `detail` map. ▢ deferred: per-line
  source chip on the BS inventory line (the inventory page shows it per store).
- ✅ Reconciliation: the inventory tie-out grid from `GET /account/inventory-recon` now renders
  on `/accounts/inventory` (report vs unsold-phone ledger vs manual vs effective, with
  unplaced/superseded ghost chips).

### Phase 3 — financial analysis charts (SHIPPED 2026-09-02; UI awaiting owner preview)
- ✅ ONE endpoint (supersets the planned `/account/series`): `GET /account/analysis?months=N` —
  monthly P&L/BS trend + margins + OPEX composition + per-company/per-store comparison series,
  all from stored statements (never a second math path; pure `account/analysis.py`, proof
  `harness_financial_analysis.py`; `account_trends` grant).
- ✅ Charts (`/accounts/analysis`, shared `TrendChart` kit + new additive `stack` prop):
  revenue/GP/NI trend with projection overlay; expense-composition stacked bars; per-company +
  top-store comparison bars; margin % trend; cash/assets/liabilities trend; headline tiles
  (margins ride the payload).
- ▢ deferred: current ratio / inventory days / payable days panel (needs BS line-level day-rate
  math — a follow-up on the same payload), per-store filters on the analysis endpoint (the
  per-scope series ship; the Trends hub keeps store filtering meanwhile).

### Phase 4 — projections & forecasting (CORE SHIPPED 2026-09-02, mig 941)
- ✅ `account/projection_engine.py` (pure, DETERMINISTIC — no LLM in the math): least-squares
  linear trend over a trailing window + seasonal-naive (same-month-last-year × recent YoY level,
  noted fallback), per-org `account_config.projection_config` (mig 941: method/window/horizon/
  `growth_rate_override`/`expense_inflation` — config wins over fit); GP/NI DERIVED per projected
  month; rows flagged `projected: true`; served by `GET /account/projection` and overlaid on the
  analysis page. Proof: `harness_projection_engine.py`.
- ✅ Cash runway: latest cash & equivalents ÷ avg projected burn (profitable trend ⇒ honest null).
- ▢ deferred: budget-vs-actual (per-org budget rows + variance columns — needs a budget table
  migration), per-line projection output in the full statement payload shape (today: the
  headline P&L blocks; per-line follows the same engine).

### Phase 5 — company valuation (CORE SHIPPED 2026-09-02, mig 941; UI awaiting owner preview)
- ✅ `account/valuation.py` (pure) + `GET /account/valuation` (own default-closed
  `company_valuation` grant): TTM revenue/SDE/EBITDA multiples (annualized <12 months, flagged;
  zero/negative basis marked not-meaningful), asset-based floor, projection-fed DCF with a 3×3
  rate × terminal-multiple sensitivity grid; summary = min/median/max across meaningful earnings
  methods with the asset floor lifting the low end; every assumption + source cited; disclaimer
  in every payload. Config: `account_config.valuation_config` (mig 941), house defaults 0.3–0.6×
  revenue / 2.5–4× SDE / 3–5× EBITDA / 20–30% discount / 2–3× terminal / 36-month horizon.
  Proof: `harness_valuation.py`. Valuation section renders on `/accounts/analysis`.
- ▢ deferred: assumption EDITORS in the UI (today: config via `account_config.valuation_config`
  rows; a `PUT /account/config` extension + editor panel is the follow-up).
- Original spec (kept for reference):
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
