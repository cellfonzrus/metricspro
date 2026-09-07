// Marketing & Events — shared types and the handful of helpers every marketing page needs.
// Keeping them here (rather than re-declaring per page) is what stops the event shape from drifting
// between the list, the workspace and the settings screen — the three places that render the same
// records. Mirrors the CRM module's `lib/crm.ts` so there is one house pattern for a module's lib.
//
// The option lists are CONFIG (migs 986/987): nothing in this file — or anywhere in the frontend —
// enumerates a theme, venue, party type, transport mode, giveaway type, role or channel. Pickers are
// rendered from whatever `/marketing/options` returns, which is exactly what makes the owner's "+"
// work without a deploy.
import type { CSSProperties } from 'react'

// ── shared visual language (same tokens as the CRM/POS modules) ─────────────────────────────────
export const panel: CSSProperties = { background: 'var(--surface2)', borderRadius: 8, padding: 14, border: '1px solid var(--border)' }
export const input: CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', width: '100%', outline: 'none' }
export const label: CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
export const btn: CSSProperties = { padding: '7px 14px', borderRadius: 7, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13, cursor: 'pointer' }
export const btnPrimary: CSSProperties = { ...btn, background: '#2563eb', borderColor: '#2563eb', color: '#fff', fontWeight: 600 }
export const btnDanger: CSSProperties = { ...btn, borderColor: '#dc2626', color: '#dc2626' }
export const cell: CSSProperties = { padding: '7px 12px', borderBottom: '1px solid var(--border)' }
export const th: CSSProperties = { ...cell, textAlign: 'left', fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text2)', fontWeight: 600, whiteSpace: 'nowrap' }

export const STATUS_COLOR: Record<string, string> = {
  draft: '#6b7280', approved: '#2563eb', live: '#16a34a', closed: '#334155', cancelled: '#dc2626',
}
export const SEVERITY_COLOR: Record<string, string> = {
  error: '#dc2626', warning: '#f39c12', info: '#2563eb',
}
export const CONFIRM_COLOR: Record<string, string> = {
  planned: '#6b7280', confirmed: '#16a34a', declined: '#dc2626', no_show: '#b91c1c',
}
/** Geofence verdicts. Every `unverified_*` is GREY, never red: "we could not tell" must not look
 *  like "they were not there" on a screen a manager glances at. */
export const DECISION_COLOR: Record<string, string> = {
  inside: '#16a34a', outside: '#dc2626',
  unverified_no_fix: '#6b7280', unverified_no_target: '#6b7280', unverified_accuracy: '#6b7280',
}
export const DECISION_LABEL: Record<string, string> = {
  inside: 'At the event', outside: 'Away from the event',
  unverified_no_fix: 'No location reported', unverified_no_target: 'No venue pin set',
  unverified_accuracy: 'Location too imprecise to verify',
}

// ── types ───────────────────────────────────────────────────────────────────────────────────────
export interface MarketingOption {
  key: string; label: string; sort_order: number; is_active: boolean
  extra: Record<string, any>; source?: 'house' | 'tenant'
}
export interface OptionList { list_key: string; label: string; options: MarketingOption[] }

export type EventStatus = 'draft' | 'approved' | 'live' | 'closed' | 'cancelled'
export type ApprovalState = 'not_required' | 'pending' | 'approved' | 'rejected'

export interface MarketingEvent {
  id: string
  title: string
  description: string | null
  theme_key: string | null
  theme_label?: string
  status: EventStatus
  market: string | null
  primary_store_code: string | null
  store_codes?: string[]
  venue_name: string | null
  venue_type_key: string | null
  venue_type_label?: string
  address: string | null
  city: string | null
  state: string | null
  postal_code: string | null
  geo_lat: number | null
  geo_lng: number | null
  checkin_radius_m: number | null
  setup_notes: string | null
  parking_notes: string | null
  event_start: string | null
  event_end: string | null
  /** What time the employees have to BE THERE — a separate field from event_start by owner directive. */
  staff_call_at: string | null
  setup_start_at: string | null
  teardown_end_at: string | null
  planned_spend: number | null
  approval_state: ApprovalState
  approval_reason: string | null
  approved_by: string | null
  approved_at: string | null
  approval_note: string | null
  debrief_what_worked: string | null
  debrief_what_didnt: string | null
  debrief_notes: string | null
  debrief_at: string | null
  is_active: boolean
  created_at: string
  issues?: ReadinessIssue[]
  staff_count?: number
}

export interface EventStaff {
  id: string; event_id: string
  employee_id: string | null; employee_name: string | null; role_key: string | null
  is_backup: boolean; backup_for_staff_id: string | null
  confirm_state: 'planned' | 'confirmed' | 'declined' | 'no_show'
  confirmed_at: string | null
  transport_mode_key: string | null
  pickup_by_staff_id: string | null; pickup_at: string | null; pickup_location: string | null
  call_time_override: string | null
  notes: string | null
  resolved_call_time?: string | null
  call_time_source?: 'personal' | 'event' | 'event_start_fallback' | 'unset'
}
export interface RosterEntry extends EventStaff {
  backup: EventStaff | null; backup_count: number; is_covered: boolean; arrived: boolean
  effective: EventStaff | null
}
export interface EventVendor {
  id: string; party_type_key: string | null; vendor_name: string | null
  contact_name: string | null; contact_phone: string | null; contact_email: string | null
  cost: number | null; confirm_state: string; arrival_at: string | null
  contract_document_id: string | null; notes: string | null
}
export interface ChecklistItem {
  id: string; label: string; category: string | null; qty: number | null
  owner_staff_id: string | null; owner_employee_id: string | null
  is_returnable: boolean; is_packed: boolean; packed_by: string | null
  is_returned: boolean; returned_by: string | null; sort_order: number; notes: string | null
}
export interface EventLink {
  id: string; channel_key: string | null; label: string | null; url: string | null
  planned_post_at: string | null; posted_at: string | null; status: string
  notes: string | null; sort_order: number
}
export interface EventGiveaway {
  id: string; giveaway_type_key: string | null; item_label: string
  qty_out: number | null; qty_returned: number | null; qty_given: number | null
  unit_cost: number | null; notes: string | null
}
export interface EventGoal {
  id: string; metric_key: string; target_value: number | null; note: string | null; sort_order: number
}
export interface EventCheckin {
  id: string; event_id: string; staff_id: string | null
  employee_id: string | null; employee_name: string | null
  checked_in_at: string; checked_out_at: string | null
  check_in_lat: number | null; check_in_lng: number | null; check_in_accuracy: number | null
  distance_m: number | null; radius_m: number | null; within_geofence: boolean | null
  decision: string | null; decision_note: string | null
  purge_after_date: string | null
  event_title?: string | null
}
export interface ReadinessIssue {
  severity: 'error' | 'warning' | 'info'; key: string; detail: string; count?: number
}
export interface MarketingConfig {
  approval_required: boolean
  approval_spend_threshold: number | null
  default_checkin_radius_m: number
  max_checkin_accuracy_m: number
  block_checkin_outside_fence: boolean
  checkin_geo_retention_days: number
  staffing_alert_lead_hours: number
}

/** One goal line as the actuals endpoint returns it. `actual_value === null` with `derivable:false`
 *  means "no automatic source" — it is NEVER the same as an actual of 0, and the UI must not render
 *  it as one. */
export interface GoalLine {
  metric_key: string; label: string; unit: 'count' | 'money' | string
  target_value: number | null; derivable: boolean
  actual_value: number | null; variance: number | null; pct_of_goal: number | null
  baseline_per_day?: number | null; diff_per_day?: number | null
  pct_change_vs_baseline?: number | null
  source_label?: string | null; reason?: string
}
export interface Attribution {
  headline: string; detail: string; grain_note: string
  event_days: string[]; baseline_days: string[]; stores: string[]
  baseline_method: string; source: string; derived: boolean; source_note?: string | null
}
export interface ActualsResponse {
  available: boolean; reason?: string
  goals: GoalLine[]
  attribution: Attribution
  comparison?: Record<string, {
    event_total: number; event_per_day: number
    baseline_total: number; baseline_per_day: number | null
    diff_per_day: number | null; pct_change: number | null; has_baseline: boolean
  }>
  field_labels?: Record<string, string>
}

export interface EventWorkspace {
  event: MarketingEvent
  options: Record<string, MarketingOption[]>
  config: MarketingConfig
  staff: EventStaff[]
  staffing: { counts: Record<string, number>; roster: RosterEntry[]; uncovered: RosterEntry[]; unassigned_backups: EventStaff[] }
  transport: { rides: Record<string, EventStaff[]>; needs_ride: EventStaff[]; problems: { kind: string; detail: string }[] }
  checkins: EventCheckin[]
  checklist: ChecklistItem[]
  checklist_readiness: { total: number; packed: number; unpacked: number; pct_packed: number; returnable: number; returned: number; outstanding_returns: number; complete: boolean; outstanding_items: ChecklistItem[] }
  vendors: EventVendor[]
  links: EventLink[]
  giveaways: EventGiveaway[]
  giveaway_reconciliation: { items: any[]; totals: Record<string, number>; uncounted_items: number; note: string }
  goals: EventGoal[]
  readiness: { issues: ReadinessIssue[]; imminent: boolean; hours_out: number | null }
  allowed_transitions: EventStatus[]
}

// ── helpers ─────────────────────────────────────────────────────────────────────────────────────
export function optionLabel(options: MarketingOption[] | undefined, key: string | null | undefined): string {
  if (!key) return '—'
  const hit = (options || []).find(o => o.key === key)
  // An unfamiliar word beats a blank: a value whose option was deactivated must still read back.
  return hit?.label || key
}

export function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })
}

export function fmtMetric(v: number | null | undefined, unit: string): string {
  if (v === null || v === undefined) return '—'
  return unit === 'money' ? fmtMoney(v) : String(Math.round(Number(v) * 100) / 100)
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

/** `<input type="datetime-local">` wants a local `YYYY-MM-DDTHH:mm` with no zone. */
export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function fromLocalInput(v: string): string | null {
  if (!v) return null
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? null : d.toISOString()
}

/** "in 3h" / "2h ago" — a call time is read relatively far faster than a timestamp. */
export function relTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return '—'
  const diff = t - Date.now()
  const abs = Math.abs(diff)
  const mins = Math.round(abs / 60000)
  const unit = mins < 60 ? `${mins}m` : mins < 1440 ? `${Math.round(mins / 60)}h` : `${Math.round(mins / 1440)}d`
  return diff >= 0 ? `in ${unit}` : `${unit} ago`
}

/**
 * Read ONE position from the browser, once, at the moment the person asked.
 *
 * Deliberately `getCurrentPosition`, never `watchPosition`: this platform does not track anybody
 * between check-ins, and using the watching API — even with the handler unsubscribed — would put
 * continuous-location code in the bundle. A denial or timeout RESOLVES with nulls rather than
 * rejecting, because a check-in with no fix is a legitimate, recordable outcome (the backend files
 * it as `unverified_no_fix`), not an error the person should be blocked by.
 */
export function readPositionOnce(timeoutMs = 12000): Promise<{ lat: number | null; lng: number | null; accuracy: number | null; error?: string }> {
  return new Promise(resolve => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) {
      resolve({ lat: null, lng: null, accuracy: null, error: 'This device cannot report a location.' })
      return
    }
    let done = false
    const finish = (r: { lat: number | null; lng: number | null; accuracy: number | null; error?: string }) => {
      if (!done) { done = true; resolve(r) }
    }
    navigator.geolocation.getCurrentPosition(
      p => finish({ lat: p.coords.latitude, lng: p.coords.longitude, accuracy: p.coords.accuracy }),
      e => finish({ lat: null, lng: null, accuracy: null, error: e?.message || 'Location unavailable.' }),
      { enableHighAccuracy: true, timeout: timeoutMs, maximumAge: 0 },
    )
    setTimeout(() => finish({ lat: null, lng: null, accuracy: null, error: 'Location timed out.' }), timeoutMs + 500)
  })
}
