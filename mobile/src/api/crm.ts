import { api } from './client'

// ── CRM API ──────────────────────────────────────────────────────────────────────────────────────
// Backend: crm router /crm/*. Types mirror frontend/src/lib/crm.ts so a record has the same shape on
// mobile as it does on the web (the property that stops the list, board and detail views drifting).
const BASE = '/api/v1/crm'

export type LeadPriority = 'hot' | 'warm' | 'cold'
export type LeadStatus = 'open' | 'won' | 'lost' | 'disqualified'

export type Lead = {
  id: string
  lead_no: number
  first_name: string | null
  last_name: string | null
  company_name: string | null
  phone: string | null
  email: string | null
  store_code: string | null
  status: LeadStatus
  stage_id: string | null
  stage_name?: string | null
  stage_key?: string | null
  source_name?: string | null
  interest_name?: string | null
  owner_employee_id: string | null
  value_estimate: number
  score: number
  priority: LeadPriority
  notes: string | null
  created_at: string
  last_activity_at: string | null
  next_action_at: string | null
  display_name?: string
}

export type Stage = {
  id: string
  key: string
  name: string
  sort_order: number
  probability: number
  is_won: boolean
  is_lost: boolean
}

export type CrmTask = {
  id: string
  lead_id: string
  title: string
  body: string | null
  type: string
  due_at: string
  status: 'open' | 'done' | 'snoozed' | 'cancelled' | 'missed'
  priority: string
  lead_name?: string
  lead_no?: number
  lead_phone?: string | null
  is_overdue?: boolean
  is_today?: boolean
}

export type CrmSummary = {
  open_leads?: number
  hot_leads?: number
  tasks_today?: number
  tasks_overdue?: number
  [k: string]: unknown
}

export function getSummary() {
  return api.get<CrmSummary>(`${BASE}/summary`)
}

export function listLeads(params: { status?: LeadStatus; owner?: string; search?: string; limit?: number } = {}) {
  const q = new URLSearchParams()
  if (params.status) q.set('status', params.status)
  if (params.owner) q.set('owner_employee_id', params.owner)
  if (params.search) q.set('search', params.search)
  q.set('limit', String(params.limit ?? 50))
  return api.get<{ leads: Lead[]; stages?: Stage[] }>(`${BASE}/leads?${q.toString()}`)
}

export function getLead(leadId: string) {
  return api.get<{ lead: Lead; stages?: Stage[]; activities?: any[]; tasks?: CrmTask[] }>(
    `${BASE}/leads/${leadId}`,
  )
}

export function moveStage(leadId: string, stage_id: string) {
  return api.post<{ lead: Lead }>(`${BASE}/leads/${leadId}/stage`, { stage_id })
}

export function logActivity(leadId: string, body: { kind: string; body?: string; direction?: string }) {
  return api.post<{ activity: unknown }>(`${BASE}/leads/${leadId}/activity`, body)
}

export function listTasks(params: { scope?: 'today' | 'overdue' | 'open'; limit?: number } = {}) {
  const q = new URLSearchParams()
  if (params.scope) q.set('scope', params.scope)
  q.set('limit', String(params.limit ?? 50))
  return api.get<{ tasks: CrmTask[] }>(`${BASE}/tasks?${q.toString()}`)
}

export function completeTask(taskId: string) {
  return api.post<{ task: CrmTask }>(`${BASE}/tasks/${taskId}/complete`, {})
}

export function snoozeTask(taskId: string, hours: number) {
  return api.post<{ task: CrmTask }>(`${BASE}/tasks/${taskId}/snooze`, { hours })
}
