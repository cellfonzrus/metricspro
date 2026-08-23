import { api, ApiError } from './client'
import { enqueue, newClientId } from '@/offline/queue'
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
  // Stable per-punch id the backend dedupes on (idempotent_replay). Set by clockInDurable.
  client_request_id?: string
}

export type ClockOutBody = { entry_id?: string; override?: boolean; client_request_id?: string }

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

export function clockOut(body: ClockOutBody = {}) {
  return api.post<ClockOutResult>(`${BASE}/clock-out`, body)
}

/**
 * Durable clock-in: mint a stable client_request_id, try it online first (to resolve any override /
 * ack prompt), and on a saturation TIMEOUT or network drop (ApiError status 0) — not only when we
 * were already offline — fall through to the durable queue carrying the SAME id, so the punch is
 * retried, never lost, and idempotent with the attempt that may have landed. Offline, we enqueue
 * straight away and let the server apply its gates on replay.
 */
export async function clockInDurable(body: ClockInBody): Promise<{ queued: true } | ClockInResult> {
  const clientId = newClientId()
  const withId: ClockInBody = { ...body, client_request_id: clientId }
  if (getOnline()) {
    try {
      return await clockIn(withId)
    } catch (e) {
      // A dead/saturated network surfaces as ApiError status 0 (timeout or fetch failure). Don't lose
      // the punch — queue it for retry. Any other error (a real 4xx/5xx business answer) propagates.
      if (!(e instanceof ApiError) || e.status !== 0) throw e
    }
  }
  await enqueue({
    kind: 'timeclock.clock-in',
    label: `Clock in${body.store_code ? ` @ ${body.store_code}` : ''}`,
    method: 'POST',
    path: `${BASE}/clock-in`,
    body: withId,
    clientId,
  })
  return { queued: true }
}

export async function clockOutDurable(body: ClockOutBody = {}): Promise<{ queued: true } | ClockOutResult> {
  const clientId = newClientId()
  const withId: ClockOutBody = { ...body, client_request_id: clientId }
  if (getOnline()) {
    try {
      return await clockOut(withId)
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 0) throw e
    }
  }
  await enqueue({
    kind: 'timeclock.clock-out',
    label: 'Clock out',
    method: 'POST',
    path: `${BASE}/clock-out`,
    body: withId,
    clientId,
  })
  return { queued: true }
}
