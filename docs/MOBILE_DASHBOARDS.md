# Mobile dashboards + chat (native)

The phone app rebuilds the web reporting dashboards as native screens and pins the ask/chat bar on
top of every tab. Nothing new landed on the backend — the app reads the **same live endpoints** the
web dashboards use, so the numbers reconcile.

## Chat (ask bar) — `src/components/TopChatBar.tsx`

The native twin of `frontend/src/components/AskBar.tsx`. It is **deterministic and backend-free**:
it parses a metric + a period out of the typed question, calls the matching report endpoint, and
answers inline with a "View →" deep-link into the native dashboard. Rendered as the tabs' custom
`header`, so it sits above Home / POS / CRM / Dashboards / … ("keep chat on top").

Intents → endpoint:

| Ask about | Endpoint | Field read |
| --- | --- | --- |
| net income / net profit | `GET /account/overview/{period}` | `scopes[consolidated].net_income` |
| gross profit / margin | `GET /account/overview/{period}` | `scopes[consolidated].gross_profit` |
| revenue / sales | `GET /commcalc/sales-report/narrative?period=` | `facts.revenue` |
| activations | `GET /commcalc/exec-mtd/{period}/narrative` | `facts.total_activation` |
| commission / payout | `GET /commcalc/commissions/{period}` | `Σ total_payout` |

## Dashboards — `app/(app)/dashboards/*` + hub `app/(app)/(tabs)/dashboards.tsx`

The catalog is data-driven (`src/modules/dashboards.ts`); the hub, the tab and the answer deep-links
all read that one list.

| Dashboard | Endpoint | Headline KPIs |
| --- | --- | --- |
| My performance | `GET /core/employee-dashboard?employee_id=` | commission, tier, KPIs met (own record only, #13) |
| Store KPIs | `GET /pos/reports/kpis` | today / week / month sales, stock on hand (server-scoped) |
| Executive MTD | `GET /commcalc/exec-mtd/{period}` | activations, phones, sales, accessories; top stores & reps |
| Sales report | `GET /commcalc/sales-report?period=` | revenue, GP, txns, activations + mix |
| P&L / Accounts | `GET /account/overview/{period}` (+ `/narrative`) | revenue, GP, net income, margin; by company |
| Store P&L | `GET /commcalc/gp/{period}` | total revenue, net profit, commission; net profit + attainment by store |
| Commission payouts | `GET /commcalc/commissions/{period}` | total payout, reps paid; top reps |

## Access / scope gate — `src/lib/scope.ts`

These summary endpoints are authorised by tenant **membership** alone — they do not additionally
gate on role — so the app must gate, or a financial dashboard would leak company numbers to any
signed-in user. `minScope` in the catalog enforces it: org-wide financials require `market` or
`all`; the store-scoped POS KPIs and the personal dashboard stay open (`self`). This mirrors the
web's per-report `canSeeItem`.
