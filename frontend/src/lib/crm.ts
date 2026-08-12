// CRM shared types + the handful of helpers every CRM page needs. Keeping them here (rather than
// re-declaring per page) is what stops the lead shape from drifting between the list, the board and
// the detail view — the three places that all render the same record.
import type { CSSProperties } from 'react'

export interface Lead {
  id: string
  lead_no: number
  first_name: string | null
  last_name: string | null
  company_name: string | null
  phone: string | null
  email: string | null
  store_code: string | null
  market: string | null
  status: 'open' | 'won' | 'lost' | 'disqualified'
  pipeline_id: string | null
  stage_id: string | null
  stage_name?: string | null
  stage_key?: string | null
  stage_sort?: number | null
  stage_probability?: number | null
  source_id: string | null
  source_name?: string | null
  source_key?: string | null
  interest_id: string | null
  interest_name?: string | null
  interest_key?: string | null
  disposition_id: string | null
  disposition_name?: string | null
  reason_code_id: string | null
  owner_employee_id: string | null
  queue_id: string | null
  agency_id: string | null
  agency_name?: string | null
  agency_assigned_at: string | null
  agency_accepted_at: string | null
  value_estimate: number
  lines_estimate: number
  expected_close_date: string | null
  score: number
  priority: 'hot' | 'warm' | 'cold'
  notes: string | null
  created_at: string
  last_activity_at: string | null
  next_action_at: string | null
  closed_at: string | null
  converted_customer_id: string | null
  display_name?: string
}

export interface Stage {
  id: string; pipeline_id: string; key: string; name: string; sort_order: number
  probability: number; is_won: boolean; is_lost: boolean; sla_hours: number | null
  requires_disposition: boolean; is_active: boolean
}
export interface RefRow { id: string; key?: string; name: string; is_active?: boolean; [k: string]: any }
export interface Disposition extends RefRow {
  outcome: 'connected' | 'no_contact' | 'won' | 'lost' | 'nurture'
  requires_followup: boolean; default_followup_hours: number | null
  requires_reason: boolean; closes_lead: boolean; sets_stage_id: string | null
}
export interface Task {
  id: string; lead_id: string; title: string; body: string | null; type: string
  due_at: string; remind_at: string | null; assigned_employee_id: string | null
  status: 'open' | 'done' | 'snoozed' | 'cancelled' | 'missed'
  priority: string; reminder_count: number
  lead_name?: string; lead_no?: number; lead_phone?: string | null; lead_store?: string | null
  is_overdue?: boolean; is_today?: boolean
}
export interface Activity {
  id: string; lead_id: string; kind: string; body: string | null; meta: any
  actor_employee_id: string | null; created_at: string; direction: string | null
}
export interface CrmConfig {
  default_pipeline_id: string | null; timezone: string; stale_lead_hours: number
  escalate_after_hours: number; miss_grace_hours: number; require_disposition_on_close: number
  duplicate_match: string; reminder_channels: string[]; auto_convert_on_won: boolean
  max_open_leads_per_rep: number | null; daily_logging_reminder_hour: number
  intake_key: string | null; lookup_requires_grant: boolean
  can_edit: boolean; can_lookup: boolean; can_lookup_money: boolean
  me: { employee_id: string | null; store_code: string | null; market: string | null; is_manager: boolean }
}

// ── shared styles (match the POS / StoreOps pages so CRM does not look like a bolted-on module) ──
export const input: CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', width: '100%', outline: 'none' }
export const label: CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
export const cell: CSSProperties = { padding: '7px 12px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
export const th: CSSProperties = { ...cell, textAlign: 'left', fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text2)', fontWeight: 600 }
export const panel: CSSProperties = { background: 'var(--surface2)', borderRadius: 8, padding: 14, border: '1px solid var(--border)' }
export const btn: CSSProperties = { padding: '7px 14px', borderRadius: 7, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13, cursor: 'pointer' }
export const btnPrimary: CSSProperties = { ...btn, background: '#2563eb', borderColor: '#2563eb', color: '#fff', fontWeight: 600 }

export const PRIORITY_COLOR: Record<string, string> = { hot: '#e74c3c', warm: '#f39c12', cold: '#6b7280' }
export const STATUS_COLOR: Record<string, string> = { open: '#2563eb', won: '#16a34a', lost: '#dc2626', disqualified: '#6b7280' }

export function leadName(l: Partial<Lead>): string {
  const n = [l.first_name, l.last_name].filter(Boolean).join(' ').trim()
  return n || l.company_name || l.phone || 'Unknown'
}

/** Format a US 10-digit number for display; anything else is shown as typed. */
export function fmtPhone(v: string | null | undefined): string {
  const d = String(v || '').replace(/[^0-9]/g, '')
  if (d.length === 10) return `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`
  if (d.length === 11 && d[0] === '1') return fmtPhone(d.slice(1))
  return String(v || '')
}

export function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })
}

/** "in 3h" / "2d overdue" — a rep reads relative time far faster than a timestamp. */
export function relTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return '—'
  const diff = t - Date.now()
  const abs = Math.abs(diff)
  const mins = Math.round(abs / 60000)
  const unit = mins < 60 ? `${mins}m` : mins < 1440 ? `${Math.round(mins / 60)}h` : `${Math.round(mins / 1440)}d`
  return diff >= 0 ? `in ${unit}` : `${unit} overdue`
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString()
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

/** `datetime-local` needs a local-clock string, never an ISO/UTC one. */
export function toLocalInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

export const ACTIVITY_ICON: Record<string, string> = {
  note: '📝', call: '📞', sms: '💬', email: '✉️', whatsapp: '🟢', visit: '🚶',
  stage_change: '➡️', assignment: '🤝', disposition: '✅', task: '🔔',
  conversion: '🎉', system: '⚙️',
}
