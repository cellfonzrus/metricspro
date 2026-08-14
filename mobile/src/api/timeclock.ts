import { api } from './client'
import { enqueue } from '@/offline/queue'
import { getOnline } from '@/offline/net'

// ── Time Clock API ───────────────────────────────────────────────────────────────────────────────
// Backend: storeops router /timeclock/*. Identity is always the signed-in employee (the token) — a
// body employee_id is ignored server-side, so a rep can only punch themselves.
const BASE = '/api/v1/storeops/timeclock'

export type TimeEntry = {
  id: string
  employee_id: string
  employee_name?: string | null
  store_code?: string | null
  clock_in: string
  clock_out?: string | null
  work_date?: string | null
  hours?: number | null
  selfie_url?: string | null
}

export type ClockStatus = { clockedIn: boolean; entry: TimeEntry | null }

export type AllowedStores = { home_store: string | null; work_date: string; stores: string[] }

export type ClockInBody = {
  store_code?: string
  selfie?: string // base64 (optional; audit only)
  gps_lat?: number
  gps_lng?: number
  gps_accuracy_m?: number
  face_match_pct?: number
  device?: string
  override?: boolean
  priority_ack?: boolean
  priority_ack_count?: number
}

// The clock-in response is a discriminated union: success, or a "needs X" prompt the UI must resolve.
export type ClockInResult =
  | { success: true; data: { time: string; entry_id: string; store_code: string }; missed_closing_notice?: string }
  | {
      success: false
      needs_override: true
      store_code: string
      allowed_stores: string[]
      home_store: string | null
      message: string
    }
  | { success: false; needs_priority_ack: true; store_code: string; priority: unknown; message: string }

export type ClockOutResult =
  | { success: true; data: { time: string; hours: number | null; clock_in: string }; missed_closing_notice?: string }
  | { success: false; needs_closing: true; message: string }

export function getStatus() {
  return api.get<ClockStatus>(`${BASE}/status`)
}

export function getAllowedStores() {
  return api.get<AllowedStores>(`${BASE}/allowed-stores`)
}

/** The full active store list for the picker (unscoped kiosk list). */
export function getStores() {
  return api.get<{ stores: { store_code: string; store_name?: string }[] }>(`${BASE}/stores`)
}

export function clockIn(body: ClockInBody) {
  return api.post<ClockInResult>(`${BASE}/clock-in`, body)
}

export function clockOut(body: { entry_id?: string; override?: boolean } = {}) {
  return api.post<ClockOutResult>(`${BASE}/clock-out`, body)
}

/**
 * Durable clock-in for the offline case: when there is no network we cannot resolve an override /
 * ack prompt, so we enqueue a straightforward punch for the already-selected store and let the server
 * apply its gates on replay. Online, callers should use clockIn() directly to handle the prompts.
 */
export async function clockInDurable(body: ClockInBody): Promise<{ queued: true } | ClockInResult> {
  if (getOnline()) return clockIn(body)
  await enqueue({
    kind: 'timeclock.clock-in',
    label: `Clock in${body.store_code ? ` @ ${body.store_code}` : ''}`,
    method: 'POST',
    path: `${BASE}/clock-in`,
    body,
  })
  return { queued: true }
}

export async function clockOutDurable(
  body: { entry_id?: string; override?: boolean } = {},
): Promise<{ queued: true } | ClockOutResult> {
  if (getOnline()) return clockOut(body)
  await enqueue({
    kind: 'timeclock.clock-out',
    label: 'Clock out',
    method: 'POST',
    path: `${BASE}/clock-out`,
    body,
  })
  return { queued: true }
}
