import { api } from './client'

// ── DM tools API — closing verification + store-visit checklist ────────────────────────────────────
// The native twin of the web DM Closing Verification (/closing/verify) and the Store Visit checklist
// (/storeops/visits). All endpoints are org-scoped by the server; the closing summary is further
// scoped to the caller's store span (a market DM sees only their market).

const enc = encodeURIComponent
type Dict = Record<string, unknown>

// ── DM Verify (daily closing sign-off) ─────────────────────────────────────────────────────────────
export type ClosingTotals = {
  store_cash?: number; store_cc?: number; epay_on_cash?: number; epay_on_cc?: number
  acc_sale?: number; other_account?: number; total_collected?: number; t_ext_cc?: number
  rep_count?: number; upgrade_count?: number; new_line_count?: number; postpaid_count?: number
  [k: string]: unknown
}
export type ClosingStore = {
  store_code?: string; store_name?: string; store_address?: string; market?: string | null
  close_date?: string; closer?: string; closing_mode?: string
  no_closing_submitted?: boolean
  gate_status?: 'ok' | 'flagged' | 'blocked' | 'recon_pending' | string
  dm_corrected?: boolean
  totals?: ClosingTotals
  recon?: Dict
  verification?: { verified?: boolean; verified_by?: string; verified_at?: string; note?: string; [k: string]: unknown }
  [k: string]: unknown
}
export type ClosingSummary = { date?: string; stores: ClosingStore[]; can_review?: boolean; [k: string]: unknown }

export function getClosingSummary(date: string) {
  return api.get<ClosingSummary>(`/api/v1/closing/summary?date=${enc(date)}`)
}
export function getClosingDates() {
  return api.get<{ dates?: { close_date?: string; rows?: number }[] } | Dict>('/api/v1/closing/dates')
}
export type VerifyStoreIn = {
  store_code: string; close_date: string; store_name?: string; verified?: boolean; verified_by?: string
  dm_store_cash?: number; dm_store_cc?: number; dm_epay_cash?: number; dm_epay_cc?: number
  dm_acc_sale?: number; dm_other?: number; dm_ext_cc?: number; note?: string
}
export function verifyStore(body: VerifyStoreIn) {
  return api.post<{ ok?: boolean; store_code?: string; close_date?: string }>('/api/v1/closing/verify', body)
}

// ── DM Checklist (store visit) ───────────────────────────────────────────────────────────────────
export type VisitStore = { store_code?: string; address?: string; market?: string | null }
export function getVisitStores(market = '') {
  const q = market ? `?market=${enc(market)}` : ''
  return api.get<VisitStore[] | { stores?: VisitStore[] }>(`/api/v1/storevisit/stores${q}`)
}

export type ChecklistItem = {
  id?: string; item_key: string; label: string; category?: string; input_type?: string
  sort_order?: number; is_active?: boolean
}
export function getChecklistItems() {
  return api.get<ChecklistItem[] | { items?: ChecklistItem[] }>('/api/v1/storevisit/checklist-items')
}

export type Visit = {
  id?: string; store_code?: string; store_address?: string; market?: string | null
  dm_name?: string; status?: string; check_in_at?: string; submitted_at?: string
  actual_rep?: string; scheduled_rep?: string
  [k: string]: unknown
}
export function getVisits(params: { market?: string; store_code?: string; status?: string } = {}) {
  const q: string[] = []
  if (params.market) q.push(`market=${enc(params.market)}`)
  if (params.store_code) q.push(`store_code=${enc(params.store_code)}`)
  if (params.status) q.push(`status=${enc(params.status)}`)
  return api.get<Visit[] | { visits?: Visit[] }>(`/api/v1/storevisit/visits${q.length ? `?${q.join('&')}` : ''}`)
}
export function getScheduledRep(store_code: string, date: string) {
  return api.get<{ reps?: string[]; shifts?: Dict[] }>(
    `/api/v1/storevisit/scheduled-rep?store_code=${enc(store_code)}&date=${enc(date)}`)
}

export type CreateVisitIn = {
  store_code: string; store_address?: string; market?: string | null
  dm_email?: string; dm_name?: string; check_in_at?: string
  check_in_lat?: number; check_in_lng?: number; check_in_accuracy?: number
  scheduled_rep?: string; actual_rep?: string; rep_discrepancy_reason?: string
}
export function createVisit(body: CreateVisitIn) {
  return api.post<Visit>('/api/v1/storevisit/visits', body)
}

export type VisitResponse = {
  item_key: string; label_snapshot?: string; category_snapshot?: string
  checked?: boolean; note?: string; photo_path?: string
}
export type VisitAccessory = { accessory_name: string; qty?: number; note?: string }
export type UpdateVisitIn = {
  responses?: VisitResponse[]; accessories?: VisitAccessory[]
  notes?: string; check_out_at?: string; actual_rep?: string; [k: string]: unknown
}
export function updateVisit(id: string, body: UpdateVisitIn) {
  return api.patch<Visit>(`/api/v1/storevisit/visits/${enc(id)}`, body)
}
export function submitVisit(id: string) {
  return api.post<{ ok?: boolean; status?: string }>(`/api/v1/storevisit/visits/${enc(id)}/submit`, {})
}
