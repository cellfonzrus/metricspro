import { api } from './client'

// ── Earnings API (Commissions, Targets & Achievement) ──────────────────────────────────────────────
// The employee-facing view of the platform's commission engine. Backed by the SAME self-service
// bundle the web portal uses (core /employee-dashboard) plus the schedule-weighted target calendar
// (commcalc /targets/{period}/calendar). Identity is the signed-in rep: the caller passes their OWN
// employee_id (from /core/me), mirroring how the web portal pins it.

// A row from commcalc.rep_commissions for the selected period.
export type CommissionRow = {
  period?: string
  tier?: string | number | null
  total_payout?: number | null
  final_payout?: number | null
  acc_target?: number | null
  acc_comm?: number | null
  kpis_met?: number | null
  total_kpis?: number | null
  kpi_values?: Record<string, unknown> | null
  storeops_name?: string | null
  epay_salesperson?: string | null
  [k: string]: unknown
}

export type TrackingRow = {
  period: string
  period_year?: number | null
  period_month?: number | null
  total_payout?: number | null
  tier?: string | number | null
  kpis_met?: number | null
  total_kpis?: number | null
}

export type ReportCard = {
  tier?: string | number | null
  kpis_met?: number | null
  total_kpis?: number | null
  kpi_values?: Record<string, unknown>
  commission_earned?: number | null
  flags_count?: number
  chargebacks_count?: number
  chargebacks_total?: number
}

export type EmployeeDashboard = {
  employee: {
    employee_id: string
    name: string
    store?: string | null
    epay_salesperson?: string | null
    role?: string | null
    pay_rate?: number
    rep_name?: string | null
  }
  period: string
  widgets: Record<string, boolean>
  commission: CommissionRow | null
  commission_tracking: TrackingRow[]
  flags: unknown[]
  chargebacks: unknown[]
  hours?: {
    scheduled_hours?: number
    actual_hours?: number
    pay_rate?: number
    scheduled_pay?: number
    actual_pay?: number
    shifts?: number
  }
  report_card: ReportCard
  targets: { acc_target?: number | null; acc_comm?: number | null }
}

// One category's attainment from the target calendar (compute_scope categories).
export type TargetCategory = {
  unit: string
  monthly: number
  achieved_mtd: number
  need: number
  base_today?: number
  today_target: number
  pace: number
  open_days_left: number
  setup_fee_mtd?: number
}

export type TargetCalendar = {
  period: string
  scope: string
  store_code: string
  rep: string | null
  monthly_targets: Record<string, number>
  rep_share?: number
  reps?: string[]
  categories: Record<string, TargetCategory>
  conversion?: {
    store?: { rate?: number; target?: number; boxes?: number; billpays?: number }
    rep?: { rate?: number; target?: number; below_store?: boolean }
  }
  scheduled_hours_total?: number
  effective_hours_total?: number
  open_days_total?: number
  has_schedule?: boolean
  today?: string
}

export function getEmployeeDashboard(employeeId: string, period?: string) {
  const q = new URLSearchParams({ employee_id: employeeId })
  if (period) q.set('period', period)
  return api.get<EmployeeDashboard>(`/api/v1/core/employee-dashboard?${q.toString()}`)
}

export function getTargetCalendar(params: {
  period: string
  store_code: string
  rep: string
  today?: string
}) {
  const q = new URLSearchParams({ scope: 'rep', store_code: params.store_code, rep: params.rep })
  if (params.today) q.set('today', params.today)
  return api.get<TargetCalendar>(
    `/api/v1/commcalc/targets/${encodeURIComponent(params.period)}/calendar?${q.toString()}`,
  )
}

export type CoachingTip = { title?: string; body?: string; message?: string; [k: string]: unknown }

export function getCoaching(period: string, rep: string) {
  const q = new URLSearchParams({ rep })
  return api
    .get<{ tips?: CoachingTip[]; items?: CoachingTip[] }>(
      `/api/v1/commcalc/coaching/${encodeURIComponent(period)}?${q.toString()}`,
    )
    .then((r) => r.tips ?? r.items ?? [])
    .catch(() => [] as CoachingTip[]) // coaching is optional; never block the screen
}

// Local device date as YYYY-MM-DD (the target calendar's "today" should track the store's wall clock).
export function localToday(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
