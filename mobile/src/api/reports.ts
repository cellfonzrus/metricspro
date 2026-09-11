import { api } from './client'
import { todayISO } from '@/lib/period'

// ── Reporting / dashboard API ────────────────────────────────────────────────────────────────────
// The native twin of the web reporting pages. Every function hits the SAME endpoint the corresponding
// web dashboard uses (see docs/MOBILE_DASHBOARDS.md), so the phone shows the same reconciled numbers.
// All are GET; auth + org scoping are handled by the shared client. Types are intentionally loose —
// we type only the keys the KPI screens render, and keep an index signature so an added field on the
// server never breaks the app.

const enc = encodeURIComponent

type Dict = Record<string, unknown>

// account/overview — P&L headline per scope (consolidated / company / store).
export type OverviewScope = {
  scope_key: string
  scope_label: string
  revenue?: number
  gross_profit?: number
  net_income?: number
  assets?: number
  balanced?: boolean
  model?: string | null
  computed_at?: string | null
}
export type Overview = {
  period: string
  computed: boolean
  companies: { id: string; name: string }[]
  scopes: OverviewScope[]
}
export function getOverview(period: string) {
  return api.get<Overview>(`/api/v1/account/overview/${enc(period)}`)
}

export type Narrative = { headline?: string; facts?: Dict; [k: string]: unknown }
export function getOverviewNarrative(period: string) {
  return api.get<Narrative>(`/api/v1/account/overview/${enc(period)}/narrative`)
}

// commcalc/exec-mtd — the sales/activation MTD engine. by_location & by_employee each carry a
// {rows, total}; `total` holds total_activation, amount, acc_sales, and the mix breakdown.
export type ExecTotal = {
  total_activation?: number
  activation?: number
  port?: number
  byod?: number
  tablet?: number
  home_internet?: number
  upgrade?: number
  total_phones?: number
  amount?: number
  acc_sales?: number
  [k: string]: unknown
}
export type ExecRow = { label?: string; name?: string; store?: string; employee?: string } & ExecTotal
export type ExecMtd = {
  period: string
  by_location?: { rows: ExecRow[]; total: ExecTotal }
  by_employee?: { rows: ExecRow[]; total: ExecTotal }
  activation_source?: string
  [k: string]: unknown
}
export function getExecMtd(period: string) {
  return api.get<ExecMtd>(`/api/v1/commcalc/exec-mtd/${enc(period)}?today=${enc(todayISO())}`)
}
export function getExecMtdNarrative(period: string) {
  return api.get<Narrative>(`/api/v1/commcalc/exec-mtd/${enc(period)}/narrative?today=${enc(todayISO())}`)
}

// commcalc/sales-report — txns / activations / revenue / gp, plus per store/rep/day rows.
export type SalesTotals = {
  txns?: number
  lines?: number
  activations?: number
  byod?: number
  upgrades?: number
  swaps?: number
  accessory_rev?: number
  revenue?: number
  gp?: number
  [k: string]: unknown
}
export type SalesReport = {
  period: string
  source?: string
  totals: SalesTotals
  rows: Dict[]
  [k: string]: unknown
}
export function getSalesReport(period: string) {
  return api.get<SalesReport>(`/api/v1/commcalc/sales-report?period=${enc(period)}`)
}
export function getSalesNarrative(period: string) {
  return api.get<Narrative>(`/api/v1/commcalc/sales-report/narrative?period=${enc(period)}`)
}

// commcalc/gp — store & company P&L; per-store net_profit + attainment.
export type GpTotals = {
  total_rev?: number
  comm?: number
  reimb?: number
  phone_sales?: number
  acc_gp?: number
  rep_pay?: number
  exp_total?: number
  net_profit?: number
  net_excl_mdf?: number
  [k: string]: unknown
}
export type GpStoreRow = {
  store?: string
  store_code?: string
  store_name?: string
  net_profit?: number
  total_rev?: number
  net_profit_target?: number
  net_profit_attainment?: number
  [k: string]: unknown
}
export type GpReport = {
  period: string
  totals: GpTotals
  store_rows: GpStoreRow[]
  rep_rows: Dict[]
  [k: string]: unknown
}
export function getGpReport(period: string) {
  return api.get<GpReport>(`/api/v1/commcalc/gp/${enc(period)}?view=store`)
}

// commcalc/commissions — a bare LIST of per-rep rows (no totals object); we sum total_payout.
export type CommissionRow = {
  rep?: string
  name?: string
  employee_id?: string
  store?: string
  total_payout?: number
  tier?: string | number
  kpis_met?: number
  total_kpis?: number
  [k: string]: unknown
}
export function getCommissions(period: string) {
  return api.get<CommissionRow[]>(`/api/v1/commcalc/commissions/${enc(period)}`)
}

// pos/reports/kpis — live store rollups (today / week / month) + inventory counts. Server scopes to
// the caller's stores, so this is safe for any store-level user. No period param — it's "now".
export type PosKpis = {
  today: { count: number; total: number }
  week: { count: number; total: number }
  month: { count: number; total: number }
  customers: number
  products: number
  in_stock_units: number
}
export function getPosKpis() {
  return api.get<PosKpis>('/api/v1/pos/reports/kpis')
}

// core/employee-dashboard — the caller's personal performance. Access is enforced server-side (#13):
// a rep only ever sees their own. employee_id comes from /core/me.
export type EmployeeDashboard = {
  employee?: { employee_id?: string; name?: string; store?: string; role?: string; pay_rate?: number }
  period?: string
  commission?: Dict | null
  commission_tracking?: Dict[] | null
  widgets?: Dict
  [k: string]: unknown
}
export function getEmployeeDashboard(employeeId: string, period: string) {
  return api.get<EmployeeDashboard>(
    `/api/v1/core/employee-dashboard?employee_id=${enc(employeeId)}&period=${enc(period)}`,
  )
}
